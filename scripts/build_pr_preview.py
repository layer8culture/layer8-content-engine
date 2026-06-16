#!/usr/bin/env python3
"""Build a visual approval-PR body from a day's queue JSON.

The nightly pipeline opens a pull request the reviewer merges to approve. A plain
text summary makes it unclear what is actually being scheduled, so this script
renders a Postiz-style preview directly in the PR body: each post's image(s), the
exact caption that will post, hashtags, format and schedule time.

Images are embedded by the **pushed commit SHA** via raw.githubusercontent.com, so
the preview is immutable and survives the approval branch being deleted on merge.
Inline rendering relies on the repo being public (GitHub's image proxy cannot fetch
private raw URLs).

Output is a markdown file consumed by ``gh pr create --body-file``.

Stdlib only (no third-party deps); the render helpers are importable so a future
email-preview step can share one source of truth.

Usage:
  python scripts/build_pr_preview.py <queue.json> --repo owner/name --sha <sha> \
      --out pr-body.md [--assets-dir assets/generated]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

# Per-format display metadata. Keys match a post's "format" field.
FORMAT_META = {
    "reel": ("🎬", "Reel"),
    "carousel": ("🖼", "Carousel"),
    "story": ("📲", "Story"),
    "single": ("🪧", "Single"),
}
DEFAULT_FORMAT = "single"
# Display widths (px) for inline <img> embeds.
WIDTH_HERO = 300       # single / story / reel cover
WIDTH_SLIDE = 180      # one carousel slide in the side-by-side row


def raw_url(repo: str, sha: str, filename: str) -> str:
    """raw.githubusercontent.com URL for a file under assets/generated/."""
    return f"https://raw.githubusercontent.com/{repo}/{sha}/assets/generated/{filename}"


def _basename(path: str) -> str:
    return pathlib.PurePosixPath(str(path).replace("\\", "/")).name


def carousel_slide_files(post: dict, post_id: str, assets_dir: pathlib.Path) -> list[str]:
    """Ordered carousel slide filenames (``<id>-1.png`` …).

    Prefers the paths the generator wrote back to ``visual.files``; otherwise
    discovers ``<id>-N.png`` on disk; otherwise falls back to the declared slide
    count so a preview can still be built offline (missing files are flagged by
    the caller).
    """
    visual = post.get("visual") or {}
    files = visual.get("files")
    if files:
        return [_basename(f) for f in files]
    discovered = sorted(
        (p.name for p in assets_dir.glob(f"{post_id}-*.png")),
        key=lambda n: _slide_index(n, post_id),
    )
    if discovered:
        return discovered
    slides = visual.get("slides") or []
    return [f"{post_id}-{i}.png" for i in range(1, len(slides) + 1)]


def _slide_index(name: str, post_id: str) -> int:
    m = re.match(rf"^{re.escape(post_id)}-(\d+)\.png$", name)
    return int(m.group(1)) if m else 1 << 30


def _img(url: str, width: int, alt: str = "") -> str:
    return f'<img src="{url}" width="{width}" alt="{alt}">'


def render_media(post: dict, repo: str, sha: str, assets_dir: pathlib.Path) -> str:
    """Markdown/HTML for a post's image(s), embedded by commit SHA.

    Resolution is driven by ``post["format"]`` and the post id, robust to missing
    assets (renders a ``⚠️`` note rather than a broken embed).
    """
    fmt = post.get("format", DEFAULT_FORMAT)
    post_id = str(post.get("id", "")).strip()
    if not post_id:
        return "⚠️ _post has no id — cannot resolve media_"

    def exists(filename: str) -> bool:
        # On disk in CI (assets committed before the push); skip the check when
        # the assets dir isn't present (e.g. dry runs) and assume it will render.
        return (assets_dir / filename).exists() if assets_dir.exists() else True

    if fmt == "carousel":
        slides = carousel_slide_files(post, post_id, assets_dir)
        if not slides:
            return "⚠️ _no carousel slides found_"
        cells = []
        for i, fname in enumerate(slides, 1):
            if exists(fname):
                cells.append(
                    f"{_img(raw_url(repo, sha, fname), WIDTH_SLIDE, f'Slide {i}')}"
                    f"<br><sub>Slide {i}</sub>"
                )
            else:
                cells.append(f"⚠️ <sub>Slide {i} image missing</sub>")
        return " ".join(cells)

    if fmt == "reel":
        cover = f"{post_id}-cover.png"
        if not exists(cover):
            cover = f"{post_id}.png"
        mp4 = f"{post_id}.mp4"
        parts = []
        if exists(cover):
            parts.append(_img(raw_url(repo, sha, cover), WIDTH_HERO, "Reel cover"))
        else:
            parts.append("⚠️ _reel cover image missing_")
        parts.append(f"\n\n[▶ Watch reel]({raw_url(repo, sha, mp4)})")
        return "".join(parts)

    # single / story
    still = f"{post_id}.png"
    if exists(still):
        return _img(raw_url(repo, sha, still), WIDTH_HERO, fmt)
    return "⚠️ _image missing_"


def render_caption(text: str) -> str:
    """The exact caption that will post, as a markdown blockquote."""
    text = (text or "").rstrip("\n")
    if not text:
        return "> _(no caption)_"
    return "\n".join("> " + line if line else ">" for line in text.split("\n"))


def render_hashtags(post: dict) -> str | None:
    tags = post.get("hashtags") or []
    if not tags:
        return None
    line = " ".join(t if t.startswith("#") else f"#{t}" for t in tags)
    if post.get("hashtags_in_first_comment"):
        return f"**Hashtags** _(posted in first comment)_:\n{line}"
    return f"**Hashtags:** {line}"


def render_post(post: dict, n: int, repo: str, sha: str, assets_dir: pathlib.Path) -> str:
    fmt = post.get("format", DEFAULT_FORMAT)
    emoji, label = FORMAT_META.get(fmt, FORMAT_META[DEFAULT_FORMAT])
    category = post.get("category", "—")
    visual = post.get("visual") or {}
    quality = visual.get("quality", "medium")
    schedule = post.get("schedule_time", "unscheduled")
    platform = post.get("platform", "instagram")

    lines = [
        f"## {n} · {emoji} {label} — {category}",
        f"🕒 `{schedule}` · {platform} · quality: `{quality}`",
        "",
        render_media(post, repo, sha, assets_dir),
        "",
        render_caption(post.get("text", "")),
    ]
    hashtags = render_hashtags(post)
    if hashtags:
        lines += ["", hashtags]
    first_comment = post.get("first_comment")
    if first_comment:
        lines += ["", f"**First comment:** {first_comment}"]
    if post.get("trial_reel"):
        lines += ["", "_Trial reel: shown to non-followers first._"]
    collaborators = post.get("collaborators") or []
    if collaborators:
        lines += ["", "**Collaborators:** " + ", ".join(collaborators)]
    return "\n".join(lines)


def at_a_glance(posts: list[dict]) -> str:
    counts: dict[str, int] = {}
    for post in posts:
        fmt = post.get("format", DEFAULT_FORMAT)
        counts[fmt] = counts.get(fmt, 0) + 1
    order = ["reel", "carousel", "story", "single"]
    parts = []
    for fmt in order:
        if counts.get(fmt):
            emoji, label = FORMAT_META[fmt]
            parts.append(f"{emoji} {counts[fmt]} {label.lower()}{'s' if counts[fmt] > 1 else ''}")
    mix = " · ".join(parts) if parts else "—"

    hero = next(
        (p for p in posts if (p.get("visual") or {}).get("quality") == "high"),
        None,
    )
    hero_line = ""
    if hero:
        emoji, label = FORMAT_META.get(hero.get("format", DEFAULT_FORMAT),
                                       FORMAT_META[DEFAULT_FORMAT])
        hero_line = f"\n**Hero of the day:** {emoji} {hero.get('category', label)}"
    return f"**At a glance:** {mix}{hero_line}"


def date_label(queue_path: pathlib.Path, posts: list[dict]) -> str:
    stem = queue_path.stem
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem):
        return stem
    if posts:
        sched = posts[0].get("schedule_time", "")
        if len(sched) >= 10:
            return sched[:10]
    return stem


def find_summary(queue_path: pathlib.Path) -> str | None:
    """The Copilot-written ``<date>.summary.md`` narrative, if present."""
    summary_path = queue_path.with_name(queue_path.stem + ".summary.md")
    if summary_path.exists():
        return summary_path.read_text(encoding="utf-8").strip()
    return None


def build_body(
    posts: list[dict],
    repo: str,
    sha: str,
    assets_dir: pathlib.Path,
    date: str,
    summary: str | None = None,
) -> str:
    header = [
        f"# Posts for {date} — {len(posts)} to review",
        "",
        at_a_glance(posts),
    ]
    if summary:
        header += ["", summary]
    header += ["", "---", ""]

    sections = [
        render_post(post, i, repo, sha, assets_dir)
        for i, post in enumerate(posts, 1)
    ]
    body = ("\n\n---\n\n").join(sections)

    footer = [
        "---",
        "",
        "Review above. Edit the JSON under **Files changed** if needed, then "
        "**merge to approve & schedule.** Closing this PR without merging skips the day.",
    ]
    return "\n".join(header) + body + "\n\n" + "\n".join(footer) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a visual approval-PR body.")
    parser.add_argument("queue_file", help="Path to the day's queue JSON.")
    parser.add_argument("--repo", required=True, help="owner/name of the repo.")
    parser.add_argument("--sha", required=True, help="Pushed commit SHA for raw URLs.")
    parser.add_argument("--out", required=True, help="Markdown file to write.")
    parser.add_argument("--assets-dir", default="assets/generated",
                        help="Directory holding rendered media (default: assets/generated).")
    args = parser.parse_args()

    queue_path = pathlib.Path(args.queue_file)
    posts = json.loads(queue_path.read_text(encoding="utf-8"))
    if not isinstance(posts, list):
        raise SystemExit(f"{queue_path}: expected a JSON array of posts.")

    assets_dir = pathlib.Path(args.assets_dir)
    body = build_body(
        posts,
        repo=args.repo,
        sha=args.sha,
        assets_dir=assets_dir,
        date=date_label(queue_path, posts),
        summary=find_summary(queue_path),
    )
    pathlib.Path(args.out).write_text(body, encoding="utf-8")
    print(f"Wrote PR preview ({len(posts)} posts) -> {args.out}")


if __name__ == "__main__":
    main()
