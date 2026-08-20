#!/usr/bin/env python3
"""Publish an approved queue straight to the publish branch.

The web UI reviews a batch locally (step 6), so this ships it without an
approval PR: the queue file, its summary and every rendered asset are committed
and pushed to ``main``, and publish.yml takes it from there.

Why this exists as a script rather than a handful of git commands: the working
clone is routinely shallow and behind ``origin/main``, so committing from it and
pushing would revert whatever landed in between. Everything here happens in a
throwaway worktree created from a freshly fetched ``origin/main``, which is the
only safe way to push from a stale checkout.

Nothing is pushed unless every image the queue declares is actually on disk;
a half-rendered batch fails here instead of failing in publish.yml with
missing_media.

    python scripts/ship_queue.py queue/2026-08-20.json
    python scripts/ship_queue.py queue/lofi-2026-08-20.json --dry-run
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import publish_helpers  # noqa: E402
import queue_json_guard  # noqa: E402

ASSETS_SUBDIR = "assets/generated"


class ShipError(RuntimeError):
    """Anything that should stop the publish with a readable message."""


def run_git(args: list[str], cwd: pathlib.Path, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ShipError(f"git {' '.join(args)} failed: {detail}")
    return proc


def resolve_queue_path(raw: str, repo_root: pathlib.Path) -> pathlib.Path:
    """Accept a repo-relative or absolute path, but keep it inside queue/."""
    path = pathlib.Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve()
    queue_dir = (repo_root / "queue").resolve()
    if path.parent != queue_dir:
        raise ShipError(f"{raw}: must be a file directly inside queue/")
    if path.suffix != ".json":
        raise ShipError(f"{raw}: must be a .json queue file")
    if not path.is_file():
        raise ShipError(f"{raw}: no such queue file")
    return path


def load_posts(qpath: pathlib.Path) -> list[dict]:
    raw = qpath.read_text(encoding="utf-8")
    payload = queue_json_guard.load_json(raw, qpath)
    queue_json_guard.validate_queue_shape(payload, qpath)
    return payload


def declared_media(post: dict) -> list[str]:
    """The media paths a post claims, in the order the publisher reads them."""
    visual = post.get("visual") or {}
    if post.get("format") == "carousel":
        return [str(p) for p in (visual.get("files") or []) if p]
    path = visual.get("file")
    return [str(path)] if path else []


def media_problems(posts: list[dict], repo_root: pathlib.Path) -> list[str]:
    """Reasons publish.yml would reject this batch, checked before pushing.

    publish_helpers resolves media against the process working directory, so
    this re-roots every relative path at the repo instead: a run started from
    anywhere else would otherwise report a complete batch as entirely missing.
    """
    problems: list[str] = []
    for post in posts:
        post_id = str(post.get("id") or "<no id>")
        declared = declared_media(post)
        if not declared:
            problems.append(f"{post_id}: declares no image or video")
            continue

        for rel in declared:
            candidate = publish_helpers.local_media_path(rel)
            if not candidate.is_absolute():
                candidate = repo_root / candidate
            if not candidate.is_file():
                problems.append(f"{post_id}: missing {rel}")

        platform = str(post.get("platform") or "").lower()
        if platform in {"tiktok", "youtube"}:
            if not declared[0].lower().endswith(publish_helpers.VIDEO_EXTS):
                problems.append(f"{post_id}: {platform} needs a video, got {declared[0]}")
    return problems


def asset_files(posts: list[dict], repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Every rendered file belonging to this batch, matched by post ID prefix.

    Covers stills, carousel slides, mp4s and reel covers without listing them,
    and without sweeping in another day's assets.
    """
    assets_dir = repo_root / "assets" / "generated"
    if not assets_dir.is_dir():
        return []
    found: dict[str, pathlib.Path] = {}
    for post in posts:
        post_id = str(post.get("id") or "").strip()
        if not post_id:
            continue
        for path in assets_dir.glob(f"{post_id}*"):
            if path.is_file():
                found[path.name] = path
    return [found[name] for name in sorted(found)]


def copy_into_worktree(paths: list[pathlib.Path], repo_root: pathlib.Path,
                       worktree: pathlib.Path) -> list[str]:
    copied: list[str] = []
    for src in paths:
        rel = src.resolve().relative_to(repo_root).as_posix()
        dest = worktree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(rel)
    return copied


def push_with_retry(worktree: pathlib.Path, remote: str, branch: str,
                    log, attempts: int = 3) -> None:
    """Push, rebasing onto the branch when someone else landed first."""
    for attempt in range(1, attempts + 1):
        proc = run_git(["push", remote, f"HEAD:{branch}"], worktree, check=False)
        if proc.returncode == 0:
            log(f"pushed to {remote}/{branch}")
            return
        detail = (proc.stderr or proc.stdout or "").strip()
        log(f"push attempt {attempt} failed: {detail}")
        if attempt == attempts:
            raise ShipError(f"push failed after {attempts} attempts: {detail}")
        log(f"rebasing onto {remote}/{branch} before retrying")
        run_git(["fetch", remote, branch], worktree)
        run_git(["rebase", f"{remote}/{branch}"], worktree)


def ship(queue_file: str, remote: str = "origin", branch: str = "main",
         dry_run: bool = False, repo_root: pathlib.Path | None = None,
         log=print) -> dict:
    repo_root = (repo_root or REPO_ROOT).resolve()
    qpath = resolve_queue_path(queue_file, repo_root)
    posts = load_posts(qpath)
    log(f"{qpath.name}: {len(posts)} post(s)")

    absent = media_problems(posts, repo_root)
    if absent:
        listed = "\n  ".join(absent[:8]) + ("\n  ..." if len(absent) > 8 else "")
        raise ShipError(
            f"{qpath.name}: {len(absent)} post(s) are not ready, so publishing "
            f"would fail with missing_media:\n  {listed}"
        )

    assets = asset_files(posts, repo_root)
    log(f"{len(assets)} rendered asset(s) belong to this batch")
    if not assets:
        raise ShipError(
            f"{qpath.name}: no rendered assets found in {ASSETS_SUBDIR}/ for these "
            f"post IDs; finish the images before publishing"
        )

    payload = [qpath]
    summary = qpath.with_suffix(".summary.md")
    if summary.is_file():
        payload.append(summary)

    if dry_run:
        log("dry run: nothing fetched, committed or pushed")
        return {
            "queue": qpath.name,
            "posts": len(posts),
            "assets": len(assets),
            "pushed": False,
            "dry_run": True,
        }

    log(f"fetching {remote}/{branch}")
    run_git(["fetch", remote, branch], repo_root)

    tmp_parent = pathlib.Path(tempfile.mkdtemp(prefix="ship-queue-"))
    worktree = tmp_parent / "worktree"
    try:
        log(f"building a clean worktree from {remote}/{branch}")
        run_git(["worktree", "add", "--detach", str(worktree),
                 f"{remote}/{branch}"], repo_root)

        copied = copy_into_worktree(payload + assets, repo_root, worktree)
        log(f"staged {len(copied)} file(s)")

        run_git(["add", "--", *[p for p in copied if p.startswith("queue/")]], worktree)
        asset_paths = [p for p in copied if p.startswith(ASSETS_SUBDIR)]
        if asset_paths:
            # assets/generated/* is gitignored but tracked on main.
            run_git(["add", "-f", "--", *asset_paths], worktree)

        staged = run_git(["diff", "--cached", "--name-only"], worktree).stdout.strip()
        if not staged:
            log("nothing changed; this batch is already on the branch")
            return {
                "queue": qpath.name,
                "posts": len(posts),
                "assets": len(assets),
                "pushed": False,
                "already_published": True,
            }

        message = f"Publish {qpath.stem} ({len(posts)} posts, reviewed in the web UI)"
        run_git(["-c", "user.name=layer8-engine",
                 "-c", "user.email=engine@layer8culture.io",
                 "commit", "-m", message], worktree)
        sha = run_git(["rev-parse", "HEAD"], worktree).stdout.strip()
        log(f"committed {sha[:9]}")

        push_with_retry(worktree, remote, branch, log)
        return {
            "queue": qpath.name,
            "posts": len(posts),
            "assets": len(assets),
            "commit": sha,
            "pushed": True,
        }
    finally:
        run_git(["worktree", "remove", "--force", str(worktree)], repo_root, check=False)
        shutil.rmtree(tmp_parent, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("queue_file", help="queue/<name>.json to publish")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check the batch is complete without pushing.")
    args = parser.parse_args()

    try:
        result = ship(args.queue_file, remote=args.remote, branch=args.branch,
                      dry_run=args.dry_run)
    except ShipError as exc:
        raise SystemExit(str(exc)) from exc

    if result.get("pushed"):
        print(f"Published {result['queue']}; publish.yml will schedule it.")
    elif result.get("already_published"):
        print(f"{result['queue']} was already on the branch; nothing to do.")
    else:
        print(f"{result['queue']} looks ready ({result['assets']} assets).")


if __name__ == "__main__":
    main()
