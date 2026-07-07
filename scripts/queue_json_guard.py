#!/usr/bin/env python3
"""Validate and optionally repair generated queue JSON files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def escape_control_chars_in_strings(raw: str) -> str:
    """Escape raw control characters that appear inside JSON strings."""
    out: list[str] = []
    in_string = False
    escaped = False
    changed = False

    for char in raw:
        if escaped:
            out.append(char)
            escaped = False
            continue

        if char == "\\" and in_string:
            out.append(char)
            escaped = True
            continue

        if char == '"':
            out.append(char)
            in_string = not in_string
            continue

        if in_string and ord(char) < 0x20:
            changed = True
            if char == "\n":
                out.append("\\n")
            elif char == "\r":
                out.append("\\r")
            elif char == "\t":
                out.append("\\t")
            else:
                out.append(f"\\u{ord(char):04x}")
            continue

        out.append(char)

    return "".join(out) if changed else raw


def load_json(raw: str, source: Path) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{source}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def validate_queue_shape(payload: object, source: Path) -> None:
    if not isinstance(payload, list):
        raise ValueError(f"{source}: queue JSON must be a list of posts")
    if not payload:
        raise ValueError(f"{source}: queue JSON must contain at least one post")

    required = ("id", "account", "platform", "format", "schedule_time", "text", "visual")
    for index, post in enumerate(payload, start=1):
        if not isinstance(post, dict):
            raise ValueError(f"{source}: post {index} must be an object")
        missing = [key for key in required if key not in post]
        if missing:
            raise ValueError(f"{source}: post {index} missing required keys: {', '.join(missing)}")
        if not isinstance(post.get("visual"), dict):
            raise ValueError(f"{source}: post {index} visual must be an object")


def backup_invalid_file(path: Path, raw: str) -> Path:
    backup = path.with_suffix(path.suffix + ".invalid")
    backup.write_text(raw, encoding="utf-8")
    return backup


def validate_or_repair(path: Path, repair: bool) -> bool:
    raw = path.read_text(encoding="utf-8")
    first_error: ValueError | None = None
    try:
        payload = load_json(raw, path)
        validate_queue_shape(payload, path)
        print(f"{path}: valid queue JSON ({len(payload)} posts)")
        return False
    except ValueError as exc:
        if not repair:
            raise
        first_error = exc

    repaired = escape_control_chars_in_strings(raw)
    if repaired == raw:
        raise first_error

    payload = load_json(repaired, path)
    validate_queue_shape(payload, path)
    backup = backup_invalid_file(path, raw)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"{path}: repaired queue JSON ({len(payload)} posts); original saved to {backup}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file", type=Path)
    parser.add_argument("--repair", action="store_true", help="Repair common JSON control-character issues.")
    args = parser.parse_args()

    try:
        validate_or_repair(args.queue_file, args.repair)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
