#!/usr/bin/env python3
"""Recover a Copilot-written queue JSON from Copilot CLI session artifacts."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


REQUIRED_POST_KEYS = ("id", "account", "platform", "format", "schedule_time", "text", "visual")


@dataclass(frozen=True)
class ValidCandidate:
    path: Path
    post_count: int
    mtime_ns: int


def escape_annotation(value: object) -> str:
    text = str(value)
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit(level: str, message: object) -> None:
    print(f"::{level}::{escape_annotation(message)}")


def validate_queue_json(path: Path) -> int:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc

    if not isinstance(payload, list):
        raise ValueError("queue JSON must be a list of posts")
    if not payload:
        raise ValueError("queue JSON must contain at least one post")

    for index, post in enumerate(payload, start=1):
        if not isinstance(post, dict):
            raise ValueError(f"post {index} must be an object")
        missing = [key for key in REQUIRED_POST_KEYS if key not in post]
        if missing:
            raise ValueError(f"post {index} missing required keys: {', '.join(missing)}")
        if not isinstance(post.get("visual"), dict):
            raise ValueError(f"post {index} visual must be an object")

    return len(payload)


def normalize_date_stem(value: str | None) -> str | None:
    if not value:
        return None
    stem = value.strip()
    if stem.endswith(".summary.md"):
        stem = stem[: -len(".summary.md")]
    elif stem.endswith(".json"):
        stem = stem[: -len(".json")]
    return stem or None


def json_artifact_names(target: Path, date: str | None) -> set[str]:
    names = {target.name}
    date_stem = normalize_date_stem(date)
    if date_stem:
        names.add(f"{date_stem}.json")
    return names


def summary_artifact_names(summary_file: Path, target: Path, date: str | None) -> set[str]:
    names = {summary_file.name, f"{target.stem}.summary.md"}
    date_stem = normalize_date_stem(date)
    if date_stem:
        names.add(f"{date_stem}.summary.md")
    return names


def iter_session_files_dirs(session_root: Path) -> list[Path]:
    if session_root.name == "files" and session_root.is_dir():
        return [session_root]
    if not session_root.is_dir():
        return []

    files_dirs: list[Path] = []
    for child in session_root.iterdir():
        files_dir = child / "files"
        if files_dir.is_dir():
            files_dirs.append(files_dir)
    return sorted(files_dirs, key=lambda path: str(path).lower())


def matching_artifacts(session_root: Path, names: set[str]) -> list[Path]:
    matches: list[Path] = []
    seen: set[Path] = set()
    for files_dir in iter_session_files_dirs(session_root):
        for name in sorted(names):
            path = files_dir / name
            if path.is_file() and path not in seen:
                matches.append(path)
                seen.add(path)
    return matches


def candidate_sort_key(path: Path) -> tuple[int, str]:
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return (-mtime_ns, str(path).lower())


def choose_valid_candidate(candidates: list[Path]) -> ValidCandidate | None:
    for path in sorted(candidates, key=candidate_sort_key):
        try:
            post_count = validate_queue_json(path)
            stat = path.stat()
        except (OSError, ValueError) as exc:
            emit("warning", f"Skipping invalid Copilot queue artifact {path}: {exc}")
            continue
        return ValidCandidate(path=path, post_count=post_count, mtime_ns=stat.st_mtime_ns)
    return None


def copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.recovering")
    try:
        if temp.exists():
            temp.unlink()
        shutil.copyfile(source, temp)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


def recover_summary(
    *,
    selected_json: Path,
    summary_file: Path,
    session_root: Path,
    summary_names: set[str],
) -> Path | None:
    if summary_file.exists():
        emit("notice", f"Summary already exists at {summary_file}; leaving it unchanged")
        return summary_file

    same_session_matches = [selected_json.with_name(name) for name in sorted(summary_names)]
    for candidate in same_session_matches:
        if candidate.is_file() and candidate.read_text(encoding="utf-8").strip():
            copy_atomic(candidate, summary_file)
            emit("notice", f"Recovered matching summary from {candidate} to {summary_file}")
            return candidate

    candidates = matching_artifacts(session_root, summary_names)
    for candidate in sorted(candidates, key=candidate_sort_key):
        if not candidate.is_file():
            continue
        try:
            if not candidate.read_text(encoding="utf-8").strip():
                emit("warning", f"Skipping empty Copilot summary artifact {candidate}")
                continue
        except OSError as exc:
            emit("warning", f"Skipping unreadable Copilot summary artifact {candidate}: {exc}")
            continue
        copy_atomic(candidate, summary_file)
        emit("notice", f"Recovered matching summary from {candidate} to {summary_file}")
        return candidate

    emit("notice", f"No matching Copilot summary artifact found for {summary_file.name}; continuing without one")
    return None


def recover_queue(
    target: Path,
    *,
    summary_file: Path | None = None,
    date: str | None = None,
    session_root: Path | None = None,
) -> ValidCandidate | None:
    target = target.expanduser()
    summary_file = (summary_file or target.with_name(f"{target.stem}.summary.md")).expanduser()
    session_root = (session_root or (Path.home() / ".copilot" / "session-state")).expanduser()

    if target.exists():
        post_count = validate_queue_json(target)
        emit("notice", f"{target} already exists and is valid ({post_count} posts); no recovery needed")
        return ValidCandidate(path=target, post_count=post_count, mtime_ns=target.stat().st_mtime_ns)

    names = json_artifact_names(target, date or target.stem)
    if not session_root.is_dir():
        emit("warning", f"Copilot session artifact root does not exist: {session_root}")
        return None

    candidates = matching_artifacts(session_root, names)
    emit(
        "notice",
        f"Searching {session_root} for Copilot queue artifacts named {', '.join(sorted(names))}; found {len(candidates)} candidate(s)",
    )
    selected = choose_valid_candidate(candidates)
    if selected is None:
        emit("error", f"No valid Copilot queue artifact found for {target.name}")
        return None

    copy_atomic(selected.path, target)
    recovered_count = validate_queue_json(target)
    emit("notice", f"Recovered {target} from {selected.path} ({recovered_count} posts)")

    summary_names = summary_artifact_names(summary_file, target, date or target.stem)
    recover_summary(
        selected_json=selected.path,
        summary_file=summary_file,
        session_root=session_root,
        summary_names=summary_names,
    )
    return selected


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file", type=Path, help="Target queue JSON path to recover, e.g. queue/2026-07-08.json.")
    parser.add_argument(
        "--summary-file",
        type=Path,
        help="Target summary markdown path. Defaults to <queue stem>.summary.md beside the queue file.",
    )
    parser.add_argument(
        "--date",
        help="Expected queue artifact date/stem. Defaults to the target queue file stem.",
    )
    parser.add_argument(
        "--session-root",
        type=Path,
        default=Path.home() / ".copilot" / "session-state",
        help="Copilot session-state root to search. Defaults to ~/.copilot/session-state.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        selected = recover_queue(
            args.queue_file,
            summary_file=args.summary_file,
            date=args.date,
            session_root=args.session_root,
        )
    except (OSError, ValueError) as exc:
        emit("error", f"{args.queue_file}: {exc}")
        return 2
    return 0 if selected is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
