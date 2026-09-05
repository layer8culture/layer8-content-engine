#!/usr/bin/env python3
"""Prove that an exact committed batch came through a merged approval PR."""
from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import re
import subprocess
import uuid

import batch_readiness

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
READINESS_CHECK = "Batch readiness"
SHA = re.compile(r"[0-9a-f]{40}")


class ApprovalError(RuntimeError):
    """An explicit approval/provenance blocker, not a provider delivery result."""


def run_git(args: list[str], cwd: pathlib.Path, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-c", "core.longpaths=true", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode:
        raise ApprovalError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def run_gh(args: list[str], cwd: pathlib.Path, *, check: bool = True,
           input_text: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(["gh", *args], cwd=cwd, input=input_text, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode:
        raise ApprovalError(f"gh {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def gh_json(args: list[str], root: pathlib.Path) -> object:
    proc = run_gh(args, root)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ApprovalError("GitHub returned invalid JSON") from exc


def api_pages(endpoint: str, root: pathlib.Path) -> list:
    pages = gh_json(["api", endpoint, "--paginate", "--slurp"], root)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ApprovalError(f"Unexpected paginated GitHub response: {endpoint}")
    return [item for page in pages for item in page]


def repository_name(root: pathlib.Path) -> str:
    data = gh_json(["repo", "view", "--json", "nameWithOwner"], root)
    name = data.get("nameWithOwner", "") if isinstance(data, dict) else ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise ApprovalError("Could not establish the authenticated GitHub repository")
    return name


def pr_details(number: int, repo: str, root: pathlib.Path) -> dict:
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ApprovalError("Invalid approval PR number")
    data = gh_json(["api", f"repos/{repo}/pulls/{number}"], root)
    if not isinstance(data, dict) or data.get("number") != number:
        raise ApprovalError("GitHub returned the wrong approval PR")
    return data


def validate_pr(pr: dict, repo: str, *, state: str, base: str = "main") -> None:
    if (pr.get("state") != state or pr.get("draft")
            or pr.get("base", {}).get("ref") != base
            or pr.get("base", {}).get("repo", {}).get("full_name", "").lower() != repo.lower()
            or pr.get("head", {}).get("repo", {}).get("full_name", "").lower() != repo.lower()):
        raise ApprovalError("Approval PR must be a non-draft PR in this repository targeting main")
    if not SHA.fullmatch(pr.get("head", {}).get("sha", "")):
        raise ApprovalError("Approval PR has no valid exact head SHA")


def pr_files(number: int, repo: str, root: pathlib.Path) -> set[str]:
    files = api_pages(f"repos/{repo}/pulls/{number}/files?per_page=100", root)
    return {item["filename"] for item in files}


def ensure_commit(root: pathlib.Path, sha: str, *, pr_number: int | None = None,
                  remote: str = "origin") -> None:
    if not SHA.fullmatch(sha):
        raise ApprovalError("An exact 40-character commit SHA is required")
    if run_git(["cat-file", "-e", f"{sha}^{{commit}}"], root, check=False).returncode == 0:
        return
    ref = f"refs/pull/{pr_number}/head" if pr_number else sha
    run_git(["fetch", remote, ref], root)
    run_git(["cat-file", "-e", f"{sha}^{{commit}}"], root)


@contextlib.contextmanager
def commit_worktree(root: pathlib.Path, ref: str):
    """Materialize immutable payloads inside ignored repo-local storage."""
    parent = root / ".local" / "pr"
    parent.mkdir(parents=True, exist_ok=True)
    worktree = parent / uuid.uuid4().hex[:16]
    try:
        run_git(["worktree", "add", "--detach", str(worktree), ref], root)
        yield worktree
    finally:
        if worktree.exists():
            run_git(["worktree", "remove", "--force", str(worktree)], root)


def committed_report(root: pathlib.Path, queue_rel: str, sha: str, *,
                     require_future: bool = False) -> dict:
    with commit_worktree(root, sha) as worktree:
        return batch_readiness.report(worktree / queue_rel, worktree,
                                      require_future=require_future)


def require_ready(result: dict) -> None:
    if not result["ready"]:
        raise ApprovalError("Batch is not ready:\n  " + "\n  ".join(result["blockers"]))


def payload_identity(result: dict) -> tuple:
    return (result["revision"], sorted((item["path"], item["sha256"])
                                      for item in result["manifest"]))


def verify(queue_file: str | pathlib.Path, commit: str, *,
           repo_root: pathlib.Path = REPO_ROOT) -> dict:
    """Accept human nightly merges and exact-head app merges; reject main pushes."""
    root = repo_root.resolve()
    path = pathlib.Path(queue_file)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError as exc:
            raise ApprovalError("Queue must be inside the repository") from exc
    queue_rel = batch_readiness.normalized_path(path.as_posix(), root)
    if path.parent != pathlib.Path("queue") or path.suffix != ".json":
        raise ApprovalError("Queue must be a JSON file directly inside queue/")
    ensure_commit(root, commit)
    local = batch_readiness.report(root / queue_rel, root, require_future=False)
    require_ready(local)
    committed = committed_report(root, queue_rel, commit)
    require_ready(committed)
    if payload_identity(local) != payload_identity(committed):
        raise ApprovalError("Local batch differs from the workflow commit payload")
    repo = repository_name(root)
    last_change = run_git(["log", "-1", "--format=%H", commit, "--", queue_rel], root).stdout.strip()
    if not last_change:
        raise ApprovalError("Queue has no committed provenance")
    candidates: dict[int, dict] = {}
    for ref in dict.fromkeys([last_change, commit]):
        for pr in api_pages(f"repos/{repo}/commits/{ref}/pulls?per_page=100", root):
            candidates[pr["number"]] = pr
    failures: list[str] = []
    for number in sorted(candidates, reverse=True):
        pr = pr_details(number, repo, root)
        try:
            validate_pr(pr, repo, state="closed")
            if not pr.get("merged") or not pr.get("merged_at"):
                raise ApprovalError("PR was closed without merging")
            merge_sha = pr.get("merge_commit_sha", "")
            ensure_commit(root, merge_sha)
            ancestor = run_git(["merge-base", "--is-ancestor", merge_sha, commit], root, check=False)
            if ancestor.returncode == 1:
                raise ApprovalError("Approved merge is not an ancestor of the workflow commit")
            if ancestor.returncode:
                raise ApprovalError(f"Cannot establish merge ancestry: {ancestor.stderr.strip()}")
            if queue_rel not in pr_files(number, repo, root):
                raise ApprovalError("PR did not review this queue")
            head = pr["head"]["sha"]
            ensure_commit(root, head, pr_number=number)
            reviewed = committed_report(root, queue_rel, head)
            require_ready(reviewed)
            if payload_identity(reviewed) != payload_identity(committed):
                raise ApprovalError("Workflow payload differs from the merged PR's reviewed head")
        except ApprovalError as exc:
            failures.append(f"PR #{number}: {exc}")
            continue
        return {"queue": path.name, "pr_number": number, "pr_url": pr["html_url"],
                "head_sha": head, "merge_sha": merge_sha, "commit": commit,
                "revision": reviewed["revision"], "state": "approved"}
    details = "; ".join(failures) if failures else "no associated merged approval PR"
    raise ApprovalError(f"Batch has no approved provenance: {details}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo-root", type=pathlib.Path, default=REPO_ROOT)
    args = parser.parse_args()
    try:
        result = verify(args.queue_file, args.commit, repo_root=args.repo_root)
    except (ApprovalError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
