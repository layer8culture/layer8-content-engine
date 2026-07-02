#!/usr/bin/env python3
"""Shared helpers for approved social queue publishers."""
from __future__ import annotations

import json
import os
import pathlib
import time


VIDEO_EXTS = (".mp4", ".mov")


def resolve_local_paths(post: dict) -> list[str]:
    """Ordered list of existing local media paths for the post."""
    visual = post.get("visual", {})
    if post.get("format") == "carousel":
        files = [f for f in (visual.get("files") or []) if f and pathlib.Path(f).exists()]
        if files:
            return files

    path = visual.get("file")
    if not path and visual.get("source") == "library":
        hint = (visual.get("library_hint") or "").lower().split()
        lib = pathlib.Path("assets/library")
        for f in lib.glob("*"):
            if any(w in f.name.lower() for w in hint) or not hint:
                path = str(f)
                break
    if path and pathlib.Path(path).exists():
        return [path]
    return []


def build_caption(post: dict) -> tuple[str, str | None]:
    """Return the caption and optional first comment for a queue post."""
    caption = post["text"]
    hashtags = post.get("hashtags") or []
    first_comment = (post.get("first_comment") or "").strip()
    tag_line = " ".join(hashtags)
    if post.get("hashtags_in_first_comment") and hashtags:
        first_comment = (
            f"{first_comment}\n\n{tag_line}".strip() if first_comment else tag_line
        )
    elif hashtags:
        caption += "\n\n" + tag_line
    return caption, (first_comment or None)


def load_log(log_path: pathlib.Path) -> list[dict]:
    if not log_path.exists():
        return []
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def scheduled_post_ids(log: list[dict]) -> set[str]:
    return {
        str(record.get("id"))
        for record in log
        if isinstance(record, dict) and record.get("id") and record.get("scheduled") is True
    }


def append_new_log_records(log: list[dict], results: list[dict]) -> list[dict]:
    """Append results without duplicating post IDs already present in the log."""
    seen = {str(record.get("id")) for record in log if isinstance(record, dict) and record.get("id")}
    for result in results:
        post_id = str(result.get("id") or "")
        if not post_id:
            log.append(result)
            continue
        if post_id in seen:
            print(f"  ! {post_id}: already present in posted/log.json, not appending duplicate")
            continue
        log.append(result)
        seen.add(post_id)
    return log


def archive_queue_file(qpath: pathlib.Path, posted_dir: pathlib.Path) -> pathlib.Path:
    """Move queue file to posted/, avoiding same-name archive collisions."""
    target = posted_dir / qpath.name
    if not target.exists():
        qpath.rename(target)
        return target

    run_id = os.environ.get("GITHUB_RUN_ID") or str(int(time.time()))
    target = posted_dir / f"{qpath.stem}-run-{run_id}{qpath.suffix}"
    qpath.rename(target)
    print(f"  ! archive {posted_dir / qpath.name} already exists; moved queue to {target}")
    return target
