#!/usr/bin/env python3
"""Print queue JSON files changed by the current GitHub push event."""

from __future__ import annotations

import argparse
import json
import os
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
    files = changed_queue_files(event) if event is not None else []
    if event is None and args.fallback_all:
        files = fallback_queue_files()

    for path in files:
        print(path)


if __name__ == "__main__":
    main()
