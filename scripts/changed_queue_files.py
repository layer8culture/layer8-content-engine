#!/usr/bin/env python3
"""Print existing queues affected by the current push's queue or media changes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable


def normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def is_queue_json(path: str) -> bool:
    normalized = normalize_repo_path(path)
    parts = normalized.split("/")
    return (
        len(parts) == 2 and parts[0] == "queue" and parts[1].endswith(".json")
        and parts[1] not in {".json", "..json"} and not any(ord(c) < 32 for c in normalized)
    )


def iter_changed_paths(event: dict[str, Any]) -> Iterable[str]:
    for commit in event.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        for key in ("added", "modified", "renamed", "removed"):
            for path in commit.get(key) or []:
                if isinstance(path, str):
                    yield path

    head_commit = event.get("head_commit")
    if isinstance(head_commit, dict):
        for key in ("added", "modified", "renamed", "removed"):
            for path in head_commit.get(key) or []:
                if isinstance(path, str):
                    yield path


def changed_queue_files(
    event: dict[str, Any],
    exists: Callable[[str], bool] | None = None,
) -> list[str]:
    """Return unique changed queue JSON files that still exist."""

    exists = exists or (lambda path: Path(path).exists())
    return git_changed_queue_files(exists=exists, changed_paths=list(iter_changed_paths(event)))


def load_event(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    event_path = Path(path)
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{event_path}: event payload must be an object")
    return payload


def fallback_queue_files() -> list[str]:
    return sorted(str(path).replace("\\", "/") for path in Path("queue").glob("*.json"))


def event_commit_range(event: dict[str, Any]) -> tuple[str, str] | None:
    before = str(event.get("before") or "").strip()
    after = str(event.get("after") or "").strip()
    if not before and not after:
        return None
    if any(not re.fullmatch(r"[0-9a-fA-F]{40}", value) or value == "0" * 40 for value in (before, after)):
        raise ValueError("Publishing requires the complete, nonzero triggering commit range")
    return before, after


def git_changed_paths(before: str, after: str) -> list[str]:
    result = subprocess.run(
        ["git", "--no-pager", "diff", "--name-only", "-z", "--diff-filter=ACDMR", before, after,
         "--", "queue/", "assets/generated/", "assets/library/"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in result.stdout.split("\0") if path]


def git_changed_queue_files(
    exists: Callable[[str], bool] | None = None,
    changed_paths: Iterable[str] | None = None,
    repo_root: Path | None = None,
) -> list[str]:
    root = Path(repo_root or Path.cwd()).resolve()
    exists = exists or (lambda path: (root / path).is_file())
    files: list[str] = []
    seen: set[str] = set()
    if changed_paths is None:
        raise ValueError("An explicit triggering commit diff is required")
    paths = [normalize_repo_path(path) for path in changed_paths]
    if any(path.startswith("assets/") for path in paths):
        from batch_readiness import affected_queues

        paths.extend(path.as_posix() for path in affected_queues(root, paths))
    for raw_path in paths:
        path = normalize_repo_path(raw_path)
        if not is_queue_json(path) or path in seen:
            continue
        if not exists(path):
            continue
        seen.add(path)
        files.append(path)
    return files


def queue_files_for_publish(
    event: dict[str, Any] | None,
    fallback_all: bool,
    exists: Callable[[str], bool] | None = None,
    git_fallback: Callable[[str, str], list[str]] | None = None,
) -> list[str]:
    if event is not None:
        commit_range = event_commit_range(event)
        if commit_range is not None:
            before, after = commit_range
            changed_paths = (
                git_fallback(before, after)
                if git_fallback
                else git_changed_paths(before, after)
            )
            return git_changed_queue_files(exists=exists, changed_paths=changed_paths)
        files = changed_queue_files(event, exists=exists)
        if not files:
            raise ValueError("Event has neither a triggering commit range nor explicit queue paths")
        return files
    return fallback_queue_files() if fallback_all else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event",
        default=os.environ.get("GITHUB_EVENT_PATH"),
        help="Path to the GitHub event payload. Defaults to GITHUB_EVENT_PATH.",
    )
    parser.add_argument(
        "--fallback-all",
        action="store_true",
        help="List all queue JSON files only when no event payload is available.",
    )
    args = parser.parse_args()

    if args.fallback_all and os.environ.get("GITHUB_ACTIONS") == "true":
        parser.error("--fallback-all is forbidden in publishing CI")
    event = load_event(args.event)
    if event is None and not args.fallback_all:
        parser.error("GitHub event payload is required unless --fallback-all is used.")
    files = queue_files_for_publish(event, args.fallback_all)

    for path in files:
        print(path)


if __name__ == "__main__":
    main()
