#!/usr/bin/env python3
"""Prepare one exact-manifest approval PR; never ship content directly to main."""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import shutil
from urllib.parse import quote

import approval_guard
import batch_readiness
import build_pr_preview
import manual_media
import queue_json_guard

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ShipError = approval_guard.ApprovalError
run_git = approval_guard.run_git
run_gh = approval_guard.run_gh
declared_media = batch_readiness.declared_media


def resolve_queue_path(raw: str, repo_root: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    try:
        batch_readiness.normalized_path(path.relative_to(repo_root).as_posix(), repo_root)
    except ValueError as exc:
        raise ShipError(str(exc)) from exc
    if path.parent != repo_root / "queue" or path.suffix != ".json":
        raise ShipError(f"{raw}: must be a JSON file directly inside queue/")
    if not path.is_file():
        raise ShipError(f"{raw}: no such queue file")
    return path


def load_posts(qpath: pathlib.Path) -> list[dict]:
    payload = queue_json_guard.load_json(qpath.read_text(encoding="utf-8"), qpath)
    queue_json_guard.validate_queue_shape(payload, qpath)
    return payload


def media_problems(posts: list[dict], repo_root: pathlib.Path) -> list[str]:
    return batch_readiness.media_report(posts, repo_root)["blockers"]


def asset_files(posts: list[dict], repo_root: pathlib.Path) -> list[pathlib.Path]:
    result = batch_readiness.media_report(posts, repo_root)
    if result["blockers"]:
        raise ShipError("\n".join(result["blockers"]))
    return [repo_root / item["path"] for item in result["manifest"]]


def copy_into_worktree(paths: list[pathlib.Path], repo_root: pathlib.Path,
                       worktree: pathlib.Path) -> list[str]:
    copied: list[str] = []
    for src in paths:
        rel = batch_readiness.normalized_path(src.relative_to(repo_root).as_posix(), repo_root)
        # Reject destination links inherited from the base as well as source links.
        batch_readiness.normalized_path(rel, worktree)
        dest = worktree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(rel)
    return copied


def _batch_branch(qpath: pathlib.Path) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", qpath.stem):
        raise ShipError("Queue filename cannot form a safe approval branch")
    return f"posts/{qpath.stem}"


def _existing_pr(queue_rel: str, stable_branch: str, repo: str, root: pathlib.Path) -> dict | None:
    candidates = []
    for pr in approval_guard.api_pages(
            f"repos/{repo}/pulls?state=open&base=main&per_page=100", root):
        if (pr.get("head", {}).get("ref") == stable_branch
                or queue_rel in approval_guard.pr_files(pr["number"], repo, root)):
            candidates.append(pr)
    if len(candidates) > 1:
        raise ShipError("Multiple open PRs contain this batch; reconcile them before preparing approval")
    if not candidates:
        return None
    pr = candidates[0]
    approval_guard.validate_pr(pr, repo, state="open")
    return pr


def _preview(posts: list[dict], qpath: pathlib.Path, root: pathlib.Path,
             repo: str, sha: str, revision: str) -> str:
    sections = []
    assets_dir = root / "assets" / "generated"
    for index, post in enumerate(posts, 1):
        media = []
        for path in declared_media(post):
            rel = batch_readiness.normalized_path(path, root)
            url = f"https://raw.githubusercontent.com/{repo}/{sha}/{quote(rel, safe='/')}"
            label = html.escape(rel, quote=True)
            if pathlib.PurePosixPath(rel).suffix.lower() in batch_readiness.IMAGE_EXTS:
                media.append(f'<img src="{url}" width="300" alt="{label}">')
            else:
                cover = (post["visual"].get("cover") if post["format"] != "carousel" else None)
                cover = cover or str(pathlib.PurePosixPath(rel).with_suffix("")) + "-cover.png"
                cover = batch_readiness.normalized_path(cover, root)
                cover_url = f"https://raw.githubusercontent.com/{repo}/{sha}/{quote(cover, safe='/')}"
                media.append(f'<img src="{cover_url}" width="300" alt="Video cover">\n\n'
                             f'[Watch video]({url})')
        # Keep the shared caption/settings renderer, replacing only its
        # generated-filename media resolver with the approved declared paths.
        section = build_pr_preview.render_post(post, index, repo, sha, assets_dir)
        old_media = build_pr_preview.render_media(post, repo, sha, assets_dir)
        sections.append(section.replace(old_media, "\n\n".join(media), 1))
    body = (f"# Posts for {qpath.stem}\n\n"
            + (build_pr_preview.find_summary(qpath) or "") + "\n\n"
            + "\n\n---\n\n".join(sections)
            + "\n\nMerge approves this exact batch. Provider submission and delivery are separate states.\n")
    return (f"<!-- batch: queue/{qpath.name}; revision: {revision}; head: {sha} -->\n"
            f"**Reviewed revision:** `{revision}`\n\n" + body)


def ship(queue_file: str, remote: str = "origin", branch: str = "main",
         dry_run: bool = False, repo_root: pathlib.Path | None = None, log=print) -> dict:
    root = (repo_root or REPO_ROOT).resolve()
    if branch != "main":
        raise ShipError("Approval PRs must target main; direct branch publishing is not supported")
    qpath = resolve_queue_path(queue_file, root)
    ready = batch_readiness.report(qpath, root)
    approval_guard.require_ready(ready)
    queue_rel = qpath.relative_to(root).as_posix()
    if dry_run:
        log("Dry run: batch is ready; no PR, fetch, commit or push was performed.")
        return {"queue": qpath.name, "revision": ready["revision"], "state": "ready",
                "dry_run": True, "pushed": False,
                "assets": sum(item["kind"] in ("image", "video") for item in ready["manifest"]),
                "manifest": ready["manifest"]}
    repo = approval_guard.repository_name(root)
    stable_branch = _batch_branch(qpath)
    pr = _existing_pr(queue_rel, stable_branch, repo, root)
    head_branch = pr["head"]["ref"] if pr else stable_branch
    if head_branch == "main":
        raise ShipError("Refusing to push the publish branch")
    run_git(["check-ref-format", f"refs/heads/{head_branch}"], root)
    run_git(["fetch", remote, "main"], root)
    base = run_git(["rev-parse", "FETCH_HEAD"], root).stdout.strip()
    previous_head = pr["head"]["sha"] if pr else ""
    if not pr:
        existing = run_git(["ls-remote", "--heads", remote, f"refs/heads/{head_branch}"], root).stdout.strip()
        if existing:
            # A prior push can succeed while PR creation fails. Recover that
            # same branch instead of creating a second batch approval.
            previous_head = existing.split()[0]
            run_git(["fetch", remote, f"refs/heads/{head_branch}"], root)
    if previous_head:
        approval_guard.ensure_commit(root, previous_head,
                                     pr_number=pr["number"] if pr else None, remote=remote)
    manifest_paths = {item["path"] for item in ready["manifest"]}
    with approval_guard.commit_worktree(root, base) as worktree:
        if previous_head:
            # Preserve an existing nightly PR's complete history, but refuse to
            # merge code or another batch as an incidental side effect.
            changed = run_git(["diff", "--name-only", f"{base}...{previous_head}"], root).stdout.splitlines()
            previous = approval_guard.committed_report(root, queue_rel, previous_head)
            old_paths = {item["path"] for item in previous["manifest"]}
            for state in previous["posts"]:
                old_paths.update(state["media"] + state["covers"])
            with approval_guard.commit_worktree(root, previous_head) as old_tree:
                old_posts = load_posts(old_tree / queue_rel)
                old_shape = batch_readiness.validate_posts(old_posts, old_tree / queue_rel)
                if old_shape:
                    raise ShipError("Existing PR queue is invalid: " + "; ".join(old_shape))
            planned_stills = {spec.output_path().as_posix()
                              for spec in manual_media.plan_images(old_posts)}
            old_paths.update(planned_stills)
            allowed = manifest_paths | old_paths
            unexpected = set(changed) - allowed
            if unexpected:
                raise ShipError("Existing PR includes unrelated files: " + ", ".join(sorted(unexpected)))
            run_git(["-c", "user.name=layer8-engine", "-c", "user.email=engine@layer8culture.io",
                     "merge", "--no-edit", previous_head], worktree)
            for rel in old_paths - manifest_paths:
                if run_git(["cat-file", "-e", f"{base}:{rel}"], root, check=False).returncode == 0:
                    run_git(["restore", f"--source={base}", "--staged", "--worktree", "--", rel], worktree)
                else:
                    run_git(["rm", "--ignore-unmatch", "--", rel], worktree)
        copied = copy_into_worktree([root / item["path"] for item in ready["manifest"]], root, worktree)
        assembled = batch_readiness.report(worktree / queue_rel, worktree)
        approval_guard.require_ready(assembled)
        if approval_guard.payload_identity(assembled) != approval_guard.payload_identity(ready):
            raise ShipError("Batch changed during PR preparation; refresh and prepare again")
        run_git(["add", "-f", "--", *copied], worktree)
        if run_git(["diff", "--cached", "--quiet"], worktree, check=False).returncode:
            message = (f"Prepare approval for {qpath.stem}\n\n"
                       "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>")
            run_git(["-c", "user.name=layer8-engine", "-c", "user.email=engine@layer8culture.io",
                     "commit", "-m", message], worktree)
        head = run_git(["rev-parse", "HEAD"], worktree).stdout.strip()
        if head == base and not pr:
            raise ShipError("This exact batch is already on main; use its delivery status, not a new approval")
        committed = approval_guard.committed_report(root, queue_rel, head, require_future=True)
        approval_guard.require_ready(committed)
        if approval_guard.payload_identity(committed) != approval_guard.payload_identity(ready):
            raise ShipError("Committed PR payload differs from the reviewed batch")
        current = batch_readiness.report(qpath, root)
        approval_guard.require_ready(current)
        if approval_guard.payload_identity(current) != approval_guard.payload_identity(ready):
            raise ShipError("Local batch changed while preparing approval")
        # The expected old SHA also prevents overwriting a concurrently updated
        # nightly branch. No retry can silently accept somebody else's revision.
        run_git(["push", f"--force-with-lease=refs/heads/{head_branch}:{previous_head}",
                 remote, f"HEAD:refs/heads/{head_branch}"], worktree)
        body = _preview(load_posts(worktree / queue_rel), worktree / queue_rel, worktree,
                        repo, head, ready["revision"])
        if pr:
            run_gh(["pr", "edit", str(pr["number"]), "--repo", repo, "--body-file", "-"],
                   root, input_text=body)
            number = pr["number"]
        else:
            run_gh(["pr", "create", "--repo", repo, "--base", "main", "--head", head_branch,
                    "--title", f"Posts for {qpath.stem}", "--body-file", "-"], root, input_text=body)
            created = _existing_pr(queue_rel, stable_branch, repo, root)
            if created is None:
                raise ShipError("PR creation returned without an inspectable approval PR")
            number = created["number"]
        actual = approval_guard.pr_details(number, repo, root)
        approval_guard.validate_pr(actual, repo, state="open")
        if actual["head"]["sha"] != head:
            raise ShipError("Approval PR head changed during preparation; refresh before approving")
    log(f"Approval PR #{number} prepared; no provider submission has occurred.")
    return {"queue": qpath.name, "pr_number": number, "pr_url": actual["html_url"],
            "head_sha": head, "revision": ready["revision"], "state": "awaiting_approval"}


def _checks(number: int, repo: str, head: str, root: pathlib.Path) -> None:
    pages = approval_guard.gh_json(
        ["api", f"repos/{repo}/commits/{head}/check-runs?per_page=100", "--paginate", "--slurp"], root)
    readiness = [check for page in pages for check in page["check_runs"]
                 if check.get("name") == approval_guard.READINESS_CHECK
                 and check.get("app", {}).get("slug") == "github-actions"
                 and check.get("head_sha") == head]
    latest = max(readiness, key=lambda check: check["id"]) if readiness else None
    if not latest or latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ShipError(f"Required {approval_guard.READINESS_CHECK} check has not completed successfully")
    required = run_gh(["pr", "checks", str(number), "--repo", repo, "--required",
                       "--json", "name,state,bucket"], root, check=False)
    if required.returncode and "no required checks reported" in required.stderr.lower():
        return
    if required.returncode not in (0, 1, 8):
        raise ShipError(f"Cannot inspect required checks: {required.stderr.strip()}")
    try:
        checks = json.loads(required.stdout)
    except json.JSONDecodeError as exc:
        raise ShipError(f"Cannot inspect required checks: {required.stderr.strip()}") from exc
    if not isinstance(checks, list):
        raise ShipError("GitHub did not return a required-check list")
    failed = [check["name"] for check in checks
              if check.get("bucket") != "pass" or check.get("state") not in ("SUCCESS", "success")]
    if failed or required.returncode:
        raise ShipError("Required checks are failed or pending: " + ", ".join(failed))


def approve(queue_file: str, expected_revision: str, expected_head: str, pr_number: int,
            repo_root: pathlib.Path | None = None) -> dict:
    root = (repo_root or REPO_ROOT).resolve()
    qpath = resolve_queue_path(queue_file, root)
    ready = batch_readiness.report(qpath, root)
    approval_guard.require_ready(ready)
    if not expected_revision or ready["revision"] != expected_revision:
        raise ShipError("Batch revision changed; review and approve the current revision")
    if not approval_guard.SHA.fullmatch(expected_head or ""):
        raise ShipError("Approval requires the reviewed exact PR head SHA")
    repo = approval_guard.repository_name(root)
    pr = approval_guard.pr_details(pr_number, repo, root)
    approval_guard.validate_pr(pr, repo, state="open")
    if pr["head"]["sha"] != expected_head:
        raise ShipError("PR head changed; review and approve the new head")
    queue_rel = qpath.relative_to(root).as_posix()
    changed = approval_guard.pr_files(pr_number, repo, root)
    if queue_rel not in changed:
        raise ShipError("Approval PR does not contain this queue")
    if changed - {item["path"] for item in ready["manifest"]}:
        raise ShipError("Approval PR contains files outside this batch's exact manifest")
    approval_guard.ensure_commit(root, expected_head, pr_number=pr_number)
    committed = approval_guard.committed_report(root, queue_rel, expected_head, require_future=True)
    approval_guard.require_ready(committed)
    if approval_guard.payload_identity(committed) != approval_guard.payload_identity(ready):
        raise ShipError("PR payload differs from the locally reviewed revision")
    _checks(pr_number, repo, expected_head, root)
    policy = approval_guard.gh_json(
        ["pr", "view", str(pr_number), "--repo", repo,
         "--json", "mergeStateStatus,reviewDecision,headRefOid"], root)
    if (policy.get("headRefOid") != expected_head or policy.get("mergeStateStatus") != "CLEAN"
            or policy.get("reviewDecision") not in ("", "APPROVED", None)):
        raise ShipError(f"PR review/branch protection blocks merging: {policy}")
    fresh = batch_readiness.report(qpath, root)
    approval_guard.require_ready(fresh)
    if approval_guard.payload_identity(fresh) != approval_guard.payload_identity(ready):
        raise ShipError("Batch changed while approving; approval has been invalidated")
    run_gh(["pr", "merge", str(pr_number), "--repo", repo, "--merge",
            "--match-head-commit", expected_head], root)
    merged = approval_guard.pr_details(pr_number, repo, root)
    if (not merged.get("merged") or merged["head"]["sha"] != expected_head
            or not merged.get("merge_commit_sha")):
        raise ShipError("GitHub did not confirm an exact-head merge; inspect the PR before retrying")
    return {"queue": qpath.name, "pr_number": pr_number, "pr_url": merged["html_url"],
            "head_sha": expected_head, "merge_sha": merged["merge_commit_sha"],
            "revision": expected_revision, "state": "merged"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = ship(args.queue_file, remote=args.remote, branch=args.branch, dry_run=args.dry_run)
    except ShipError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
