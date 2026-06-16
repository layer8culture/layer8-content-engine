#!/usr/bin/env python3
"""Refresh the "Now Live" video-promo list in calendar/topics-lofi.md from YouTube.

Pulls the latest uploads from the Layer8Culture Radio YouTube channel via its public
RSS feed (no API key, no extra dependencies) and rewrites ONLY the
"## Now Live — video promos" section of calendar/topics-lofi.md with the most recent
not-yet-promoted videos. The lofi generation prompt turns each entry into a
"Now Live on YouTube" Video Promo Instagram post (the engine still renders its own
on-brand imagery — no YouTube footage is reposted).

Dependency-light: standard library + requests only. Idempotent — running twice
produces the same file.

Usage:
    python scripts/fetch_youtube.py [--channel-id UC...] [--max N] [--topics PATH]

Env overrides:
    YT_CHANNEL_ID   YouTube channel id (UC...) to read; overrides the default.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

import requests

# Layer8Culture Radio — https://www.youtube.com/@layer8cultureradio
DEFAULT_CHANNEL_ID = "UC0AQjSCaU9ByaU90XabBbHQ"
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

TOPICS_PATH = pathlib.Path("calendar/topics-lofi.md")
POSTED_LOG = pathlib.Path("posted/log.json")
QUEUE_DIR = pathlib.Path("queue")

SECTION_HEADER = "## Now Live — video promos"
# The next "## " heading after Now Live (keeps the rest of the file intact).
NEXT_SECTION = "## Session focus"

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"

# Title keyword -> mood steer for the generator (first match wins).
MOOD_RULES = [
    ("rainy", "rainy night"),
    ("rain", "rainy night"),
    ("night", "night"),
    ("midnight", "night"),
    ("sunrise", "sunrise"),
    ("morning", "sunrise"),
    ("dawn", "sunrise"),
    ("sunset", "sunset"),
    ("dusk", "sunset"),
    ("evening", "sunset"),
    ("jazz", "jazz"),
    ("reggae", "reggae"),
    ("latin", "latin"),
]
DEFAULT_MOOD = "night"


def infer_mood(title: str) -> str:
    low = title.lower()
    for keyword, mood in MOOD_RULES:
        if keyword in low:
            return mood
    return DEFAULT_MOOD


def fetch_feed(channel_id: str, timeout: int = 25) -> str:
    url = RSS_URL.format(channel_id=channel_id)
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "layer8-content-engine"})
    resp.raise_for_status()
    return resp.text


def parse_videos(feed_xml: str) -> list[dict]:
    """Return videos newest-first: {id, title, url, published}."""
    root = ET.fromstring(feed_xml)
    videos: list[dict] = []
    for entry in root.findall(f"{ATOM}entry"):
        vid_el = entry.find(f"{YT}videoId")
        title_el = entry.find(f"{ATOM}title")
        published_el = entry.find(f"{ATOM}published")
        if vid_el is None or not (vid_el.text or "").strip():
            continue
        vid = vid_el.text.strip()
        title = (title_el.text or "").strip() if title_el is not None else ""
        published = (published_el.text or "").strip() if published_el is not None else ""
        videos.append(
            {
                "id": vid,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "published": published,
            }
        )
    # The feed is already newest-first, but sort defensively by published desc.
    videos.sort(key=lambda v: v["published"], reverse=True)
    return videos


def consumed_blob() -> str:
    """Raw text of posted/log.json + queue/*.json for substring dedup.

    Robust to schema: we scan the raw JSON text for each video id/url, so a promo is
    surfaced once regardless of which field holds the link.
    """
    blobs: list[str] = []
    if POSTED_LOG.exists():
        try:
            blobs.append(POSTED_LOG.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    if QUEUE_DIR.exists():
        for qf in QUEUE_DIR.glob("*.json"):
            try:
                blobs.append(qf.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(blobs)


def filter_unpromoted(videos: list[dict]) -> list[dict]:
    blob = consumed_blob()
    return [v for v in videos if v["id"] not in blob and v["url"] not in blob]


def render_section(videos: list[dict]) -> str:
    """Render the Now Live section body (header + comment + entries)."""
    lines = [
        SECTION_HEADER,
        "# Auto-refreshed from YouTube by scripts/fetch_youtube.py before each lofi",
        "# run. Each entry below becomes one \"Now Live on YouTube\" Video Promo post",
        "# (the engine renders its own on-brand imagery; no YouTube footage is reposted).",
        "# Edit MOOD_RULES in the script or override entries by hand if needed.",
        "#",
    ]
    if not videos:
        lines.append("# (no live videos queued)")
    else:
        for v in videos:
            note = "drive viewers to the full session; link in bio."
            lines.append(f"  - title: {v['title']}")
            lines.append(f"    url: {v['url']}")
            lines.append(f"    mood: {infer_mood(v['title'])}")
            lines.append(f"    note: {note}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def replace_section(text: str, new_section: str) -> str:
    """Swap the Now Live section, preserving everything before/after it."""
    start = text.find(SECTION_HEADER)
    if start == -1:
        # Section missing: insert before the next section, or append.
        nxt = text.find(f"\n{NEXT_SECTION}")
        if nxt != -1:
            return text[:nxt] + "\n" + new_section + "\n" + text[nxt + 1 :]
        sep = "" if text.endswith("\n") else "\n"
        return text + sep + "\n" + new_section + "\n"

    end = text.find(f"\n{NEXT_SECTION}", start)
    if end == -1:
        # No following section — replace to end of file.
        prefix = text[:start]
        sep = "" if prefix.endswith("\n") or not prefix else ""
        return prefix + new_section + ("\n" if not new_section.endswith("\n") else "")
    return text[:start] + new_section + text[end + 1 :]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--channel-id",
        default=os.environ.get("YT_CHANNEL_ID", DEFAULT_CHANNEL_ID),
        help="YouTube channel id (UC...).",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=1,
        help="Max number of newest un-promoted videos to surface (default 1).",
    )
    parser.add_argument(
        "--topics",
        type=pathlib.Path,
        default=TOPICS_PATH,
        help="Path to topics-lofi.md (default calendar/topics-lofi.md).",
    )
    args = parser.parse_args()

    if not args.topics.exists():
        print(f"! topics file not found: {args.topics}; nothing to do")
        return 0

    try:
        feed_xml = fetch_feed(args.channel_id)
    except requests.RequestException as e:
        # Non-fatal: the generator falls back to evergreen content if Now Live is empty.
        print(f"! could not fetch YouTube feed ({e}); leaving topics-lofi.md unchanged")
        return 0
    except Exception as e:  # noqa: BLE001 - keep the lofi run resilient
        print(f"! unexpected error fetching feed ({e}); leaving topics-lofi.md unchanged")
        return 0

    try:
        videos = parse_videos(feed_xml)
    except ET.ParseError as e:
        print(f"! could not parse YouTube feed ({e}); leaving topics-lofi.md unchanged")
        return 0

    candidates = filter_unpromoted(videos)
    selected = candidates[: max(0, args.max)]

    text = args.topics.read_text(encoding="utf-8")
    new_text = replace_section(text, render_section(selected))

    if new_text == text:
        print(f"= no change: {len(selected)} live promo(s) already current in {args.topics}")
        return 0

    args.topics.write_text(new_text, encoding="utf-8")
    if selected:
        for v in selected:
            print(f"+ now live: {v['title']} ({v['url']})")
    else:
        print("= no new un-promoted videos; Now Live set to empty")
    print(f"  updated {args.topics}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
