#!/usr/bin/env python3
"""Print queue JSON files changed by the current GitHub push event."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable


def normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def is_queue_json(path: str) -> bool:
    normalized = normalize_repo_path(path)
    return normalized.startswith("queue/") and normalized.endswith(".json")


def iter_changed_paths(event: dict[str, Any]) -> Iterable[str]:
    for commit in event.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        for key in ("added", "modified", "renamed"):
            for path in commit.get(key) or []:
                if isinstance(path, str):
                    yield path

    head_commit = event.get("head_commit")
    if isinstance(head_commit, dict):
        for key in ("added", "modified", "renamed"):
            for path in head_commit.get(key) or []:
                if isinstance(path, str):
                    yield path


def changed_queue_files(
    event: dict[str, Any],
    exists: Callable[[str], bool] | None = None,
) -> list[str]:
    """Return unique changed queue JSON files that still exist."""

    exists = exists or (lambda path: Path(path).exists())
    seen: set[str] = set()
    files: list[str] = []
    for raw_path in iter_changed_paths(event):
        path = normalize_repo_path(raw_path)
        if not is_queue_json(path) or path in seen:
            continue
        if not exists(path):
            continue
        seen.add(path)
        files.append(path)
    return files


def load_event(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    event_path = Path(path)
    if not event_path.exists():
        return None
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def fallback_queue_files() -> list[str]:
    return sorted(str(path).replace("\\", "/") for path in Path("queue").glob("*.json"))


def git_changed_paths() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD^", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def git_changed_queue_files(
    exists: Callable[[str], bool] | None = None,
    changed_paths: Iterable[str] | None = None,
) -> list[str]:
    exists = exists or (lambda path: Path(path).exists())
    files: list[str] = []
    seen: set[str] = set()
    for raw_path in changed_paths if changed_paths is not None else git_changed_paths():
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
    git_fallback: Callable[[], list[str]] | None = None,
) -> list[str]:
    if event is not None:
        files = changed_queue_files(event, exists=exists)
        if files:
            return files
        return git_fallback() if git_fallback else git_changed_queue_files(exists=exists)
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

    event = load_event(args.event)
    files = queue_files_for_publish(event, args.fallback_all)

    for path in files:
        print(path)


if __name__ == "__main__":
    main()
