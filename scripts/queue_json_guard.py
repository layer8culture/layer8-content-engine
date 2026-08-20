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


def visual_slide_prompts(visual: dict) -> bool:
    """True when a carousel's slides carry generation prompts."""
    slides = visual.get("slides")
    if not isinstance(slides, list):
        return False
    return any(
        isinstance(slide, dict) and str(slide.get("openai_prompt") or "").strip()
        for slide in slides
    )


def carries_generation_prompt(visual: dict) -> bool:
    return bool(str(visual.get("openai_prompt") or "").strip()) or visual_slide_prompts(visual)


def contradictory_visual_sources(payload: list) -> list[str]:
    """Post IDs whose visual.source can't produce media but ships a prompt anyway.

    A "library" post is supposed to bring its own asset from assets/library/ and a
    "reuse" cross-post gets its media from reel_gen.py; neither carries an
    openai_prompt. So a non-"openai" source that *does* carry one is a generator
    slip, and it fails silently: plan_images skips anything that isn't "openai",
    so the manual image run produces nothing, and publishing then rejects every
    post with missing_media.
    """
    flagged: list[str] = []
    for post in payload:
        if not isinstance(post, dict):
            continue
        visual = post.get("visual")
        if not isinstance(visual, dict):
            continue
        source = str(visual.get("source", "")).strip().lower()
        if source in ("openai", "reuse"):
            continue
        if carries_generation_prompt(visual):
            flagged.append(str(post.get("id") or "<no id>"))
    return flagged


def repair_visual_sources(payload: list) -> list[str]:
    """Point contradictory visuals back at "openai". Returns the repaired IDs."""
    repaired: list[str] = []
    for post in payload:
        if not isinstance(post, dict):
            continue
        visual = post.get("visual")
        if not isinstance(visual, dict):
            continue
        source = str(visual.get("source", "")).strip().lower()
        if source in ("openai", "reuse") or not carries_generation_prompt(visual):
            continue
        visual["source"] = "openai"
        repaired.append(str(post.get("id") or "<no id>"))
    return repaired


def backup_invalid_file(path: Path, raw: str) -> Path:
    backup = path.with_suffix(path.suffix + ".invalid")
    backup.write_text(raw, encoding="utf-8")
    return backup


def validate_or_repair(path: Path, repair: bool) -> bool:
    raw = path.read_text(encoding="utf-8")
    first_error: ValueError | None = None
    syntax_repaired = False

    try:
        payload = load_json(raw, path)
        validate_queue_shape(payload, path)
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
        syntax_repaired = True
        print(
            f"{path}: repaired queue JSON ({len(payload)} posts); "
            f"original saved to {backup}"
        )

    flagged = contradictory_visual_sources(payload)
    if flagged and not repair:
        raise ValueError(
            f"{path}: {len(flagged)} post(s) carry an openai_prompt but visual.source "
            f'is not "openai", so no image would ever be generated: '
            f"{', '.join(flagged)}"
        )

    source_repaired = repair_visual_sources(payload) if flagged else []
    if source_repaired:
        print(
            f"{path}: reset visual.source to \"openai\" on {len(source_repaired)} "
            f"post(s) that carry a generation prompt: {', '.join(source_repaired)}"
        )

    if syntax_repaired or source_repaired:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return True

    print(f"{path}: valid queue JSON ({len(payload)} posts)")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file", type=Path)
    parser.add_argument(
        "--repair",
        action="store_true",
        help=(
            "Repair common JSON control-character issues, and reset visual.source "
            'to "openai" where a post carries a generation prompt.'
        ),
    )
    args = parser.parse_args()

    try:
        validate_or_repair(args.queue_file, args.repair)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
