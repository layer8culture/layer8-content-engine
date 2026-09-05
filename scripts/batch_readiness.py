#!/usr/bin/env python3
"""Read-only, exact-payload readiness shared by the app and approval checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import subprocess
from datetime import datetime

from PIL import Image, UnidentifiedImageError

import manual_media
import publish_helpers
import queue_json_guard

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
POST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def normalized_path(value: object, repo_root: pathlib.Path) -> str:
    """Reject escapes on either OS, including links and Windows drive paths."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing repository-relative path")
    raw = value.replace("\\", "/")
    parts = raw.split("/")
    if (pathlib.PureWindowsPath(value).drive or raw.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or any(part.endswith((" ", ".")) or pathlib.PureWindowsPath(part).is_reserved()
                   for part in parts)
            or any(char in raw for char in (":", "\x00", "\n", "\r"))):
        raise ValueError(f"unsafe repository-relative path: {value!r}")
    root = repo_root.resolve()
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"path escapes repository: {value!r}")
    if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root
           and parent.is_relative_to(root)):
        raise ValueError(f"symbolic links are not approved payloads: {value!r}")
    return "/".join(parts)


def file_hash(path: pathlib.Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _image_info(path: pathlib.Path) -> dict:
    try:
        with Image.open(path) as image:
            expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
            if image.format != expected[path.suffix.lower()]:
                raise ValueError(f"image content does not match its extension: {path.name}")
            image.verify()
        with Image.open(path) as image:
            image.load()
            return {"kind": "image", "width": image.width, "height": image.height}
    except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError(f"unreadable image: {path.name}: {exc}") from exc
    except OSError as exc:
        if exc.errno is not None:
            raise
        raise ValueError(f"unreadable image: {path.name}: {exc}") from exc


def _video_info(path: pathlib.Path) -> dict:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if probe.returncode:
        raise ValueError(f"unreadable video: {path.name}: {probe.stderr.strip()}")
    try:
        data = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc
    streams = data.get("streams", [])
    containers = set(str(data.get("format", {}).get("format_name", "")).split(","))
    if not containers.intersection({"mov", "mp4"}):
        raise ValueError(f"video content is not an MP4/MOV container: {path.name}")
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    try:
        duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid video metadata: {path.name}") from exc
    if width <= 0 or height <= 0 or duration <= 0 or not math.isfinite(duration):
        raise ValueError(f"video has no playable frames/duration: {path.name}")
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
         "-map", "0:v:0", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if decoded.returncode:
        raise ValueError(f"unreadable video frames: {path.name}: {decoded.stderr.strip()}")
    return {"kind": "video", "width": width, "height": height, "duration": duration,
            "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def declared_media(post: dict) -> list[str]:
    visual = post.get("visual") or {}
    files = visual.get("files") if post.get("format") == "carousel" else [visual.get("file")]
    return list(files) if isinstance(files, list) else []


def media_report(posts: list[dict], repo_root: pathlib.Path) -> dict:
    """Resolve only declared delivery media, ordered planned slides and covers."""
    blockers: list[str] = []
    warnings: list[str] = []
    manifest: dict[str, dict] = {}
    states: list[dict] = []
    by_id = {post["id"]: post for post in posts}
    for post in posts:
        post_id, visual, fmt = post["id"], post["visual"], post["format"]
        problems: list[str] = []
        paths = declared_media(post)
        slides = manual_media.carousel_slide_visuals(visual)
        if not paths:
            problems.append("declares no final image or video")
        if fmt == "carousel":
            if visual.get("source") == "openai" and not slides:
                problems.append("carousel has no ordered slide plan")
            if slides and len(paths) != len(slides):
                problems.append(f"carousel needs {len(slides)} ordered slides, got {len(paths)}")
            if len(paths) < 2 or len(paths) > 20:
                problems.append("carousel requires 2-20 final slides")
            if visual.get("file") and paths and visual["file"] != paths[0]:
                problems.append("carousel visual.file differs from its first slide")
        final_paths: list[str] = []
        cover_paths: list[str] = []
        for index, value in enumerate(paths, 1):
            try:
                rel = normalized_path(value, repo_root)
                if not rel.startswith(("assets/generated/", "assets/library/")):
                    raise ValueError(f"delivery media must be under assets/generated or assets/library: {rel}")
                suffix = pathlib.PurePosixPath(rel).suffix.lower()
                needs_video = (fmt == "reel" or post["platform"] in {"tiktok", "youtube"}
                               or (fmt == "carousel" and index <= len(slides)
                                   and slides[index - 1].get("media_type") == "video"))
                if needs_video and suffix not in publish_helpers.VIDEO_EXTS:
                    raise ValueError(f"needs a video, got {rel}")
                if suffix not in IMAGE_EXTS | set(publish_helpers.VIDEO_EXTS):
                    raise ValueError(f"unsupported media type: {rel}")
                if visual.get("source") != "library":
                    expected_stem = f"{post_id}-{index}" if fmt == "carousel" else post_id
                    if rel != f"assets/generated/{expected_stem}{suffix}":
                        raise ValueError(f"media must belong to {expected_stem}, got {rel}")
                if fmt == "carousel" and slides and not needs_video and suffix in publish_helpers.VIDEO_EXTS:
                    raise ValueError(f"slide {index} is planned as an image, not a video")
                if rel in final_paths:
                    raise ValueError(f"duplicate delivery media: {rel}")
                final_paths.append(rel)
                if suffix in publish_helpers.VIDEO_EXTS:
                    cover = (visual.get("cover") if fmt != "carousel" else None)
                    cover_paths.append(cover or str(pathlib.PurePosixPath(rel).with_suffix("")) + "-cover.png")
            except ValueError as exc:
                problems.append(str(exc))
        if visual.get("cover") and not cover_paths:
            cover_paths.append(visual["cover"])
        resolved_covers: list[str] = []
        for value in cover_paths:
            try:
                rel = normalized_path(value, repo_root)
                if (not rel.startswith(("assets/generated/", "assets/library/"))
                        or pathlib.PurePosixPath(rel).suffix.lower() not in IMAGE_EXTS):
                    raise ValueError(f"invalid image cover: {rel}")
                if visual.get("source") != "library" and rel not in {
                        str(pathlib.PurePosixPath(p).with_suffix("")) + "-cover.png"
                        for p in final_paths}:
                    raise ValueError(f"cover does not belong to this post's final media: {rel}")
                resolved_covers.append(rel)
            except ValueError as exc:
                problems.append(str(exc))
        for rel in [*final_paths, *resolved_covers]:
            if rel in manifest:
                continue
            path = repo_root / publish_helpers.local_media_path(rel)
            try:
                digest = file_hash(path)
            except (FileNotFoundError, IsADirectoryError):
                problems.append(f"missing final media: {rel}")
                continue
            try:
                info = (_video_info(path) if path.suffix.lower() in publish_helpers.VIDEO_EXTS
                        else _image_info(path))
                if digest != file_hash(path):
                    raise ValueError(f"media changed while being inspected: {rel}")
                manifest[rel] = {"path": rel, "sha256": digest, **info}
                if info.get("has_audio") is False:
                    warnings.append(f"{post_id}: {rel} has no audio")
            except ValueError as exc:
                problems.append(str(exc))
        states.append({"id": post_id, "media": final_paths, "covers": resolved_covers,
                       "blockers": problems, "media_ready": not problems})
        blockers.extend(f"{post_id}: {problem}" for problem in problems)

    state_by_id = {state["id"]: state for state in states}
    for post in posts:
        if post["visual"].get("source") != "reuse":
            continue
        source_id = post["visual"].get("of")
        source = by_id.get(source_id) if isinstance(source_id, str) else None
        problem = None
        if (not source or source_id == post["id"]
                or source["visual"].get("source") == "reuse"):
            problem = "reuse must reference an original post in this batch"
        elif source["account"] != post["account"] or source["format"] != "reel" or post["format"] != "reel":
            problem = "reuse source must be a reel in the same account"
        else:
            target_state, source_state = state_by_id[post["id"]], state_by_id[source_id]
            target_paths = target_state["media"] + target_state["covers"]
            source_paths = source_state["media"] + source_state["covers"]
            if (len(target_paths) != len(source_paths) or not target_paths
                    or any(manifest.get(a, {}).get("sha256") != manifest.get(b, {}).get("sha256")
                           for a, b in zip(target_paths, source_paths))):
                problem = "reuse output is stale or differs from its source"
        if problem:
            blockers.append(f"{post['id']}: {problem}")
            state_by_id[post["id"]]["blockers"].append(problem)
            state_by_id[post["id"]]["media_ready"] = False
    return {"blockers": blockers, "warnings": warnings, "posts": states,
            "manifest": list(manifest.values())}


def validate_posts(posts: list[dict], qpath: pathlib.Path) -> list[str]:
    problems: list[str] = []
    lane = ("lofi" if qpath.stem.startswith("lofi-") else
            "deallab" if qpath.stem.startswith("deallab-") else "layer8culture")
    ids: set[str] = set()
    for index, post in enumerate(posts, 1):
        post_id = post["id"]
        if not isinstance(post_id, str) or not POST_ID.fullmatch(post_id):
            problems.append(f"post {index}: invalid post id")
        elif post_id in ids:
            problems.append(f"{post_id}: duplicate post id")
        else:
            ids.add(post_id)
        if post["account"] != lane:
            problems.append(f"post {index}: account must match {lane} queue lane")
        if post["platform"] not in ("instagram", "tiktok", "youtube"):
            problems.append(f"post {index}: unsupported platform")
        if lane == "deallab" and post["platform"] != "instagram":
            problems.append(f"post {index}: Deal Lab only supports Instagram")
        if lane == "lofi" and post["platform"] == "tiktok":
            problems.append(f"post {index}: Radio TikTok is paused")
        if post["format"] not in ("single", "story", "carousel", "reel"):
            problems.append(f"post {index}: unsupported format")
        if not isinstance(post["text"], str) or not post["text"].strip():
            problems.append(f"post {index}: caption must be nonempty text")
        for key in ("schedule_time", "first_comment", "youtube_title"):
            if key in post and not isinstance(post[key], str):
                problems.append(f"post {index}: {key} must be text")
        for key in ("hashtags", "collaborators"):
            values = post.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                problems.append(f"post {index}: {key} must be a list of strings")
        visual = post["visual"]
        if visual.get("source") not in ("openai", "library", "reuse"):
            problems.append(f"post {index}: invalid visual.source")
        slides = visual.get("slides", [])
        if not isinstance(slides, list) or any(not isinstance(s, dict) for s in slides):
            problems.append(f"post {index}: slides must be an ordered list of objects")
        if "files" in visual and not isinstance(visual["files"], list):
            problems.append(f"post {index}: visual.files must be a list")
        if "reel" in visual and not isinstance(visual["reel"], dict):
            problems.append(f"post {index}: visual.reel must be an object")
        if post["platform"] == "youtube" and not str(post.get("youtube_title") or "").strip():
            problems.append(f"post {index}: YouTube requires youtube_title")
    problems.extend(f"{p}: contradictory visual source"
                    for p in queue_json_guard.contradictory_visual_sources(posts))
    return problems


def report(qpath: pathlib.Path, repo_root: pathlib.Path, *,
           now: datetime | None = None, require_future: bool = True) -> dict:
    result = {"ready": False, "media_ready": False, "schedule_ready": False,
              "blockers": [], "warnings": [], "revision": "", "manifest": [], "posts": []}
    repo_root, qpath = pathlib.Path(repo_root).resolve(), pathlib.Path(qpath)
    if not qpath.is_absolute():
        qpath = repo_root / qpath
    try:
        rel = normalized_path(qpath.relative_to(repo_root).as_posix(), repo_root)
        if qpath.parent != repo_root / "queue" or qpath.suffix != ".json":
            raise ValueError("queue must be a JSON file directly inside queue/")
        raw_bytes = qpath.read_bytes()
        raw = raw_bytes.decode("utf-8")
        posts = queue_json_guard.load_json(raw, qpath)
        queue_json_guard.validate_queue_shape(posts, qpath)
        canonical = json.dumps(posts, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, UnicodeError) as exc:
        result["blockers"].append(str(exc))
        return result
    except (FileNotFoundError, IsADirectoryError):
        result["blockers"].append(f"missing queue: {qpath.name}")
        return result
    shape = validate_posts(posts, qpath)
    if shape:
        result["blockers"] = shape
        return result

    media = media_report(posts, repo_root)
    from prepare_media import preparation_status
    preparation = preparation_status(posts, repo_root)
    media["blockers"].extend(preparation["blockers"])
    media["warnings"].extend(preparation["warnings"])
    from schedule_planner import validate_for_publish
    schedule = validate_for_publish(posts, qpath, now=now,
                                    min_lead_minutes=15 if require_future else None)
    queue_hash = hashlib.sha256(raw_bytes).hexdigest()
    if file_hash(qpath) != queue_hash:
        media["blockers"].append("Queue changed during inspection; refresh readiness")
    manifest = [{"path": rel, "sha256": queue_hash, "kind": "queue"}, *media["manifest"]]
    summary = qpath.with_suffix(".summary.md")
    try:
        summary_rel = normalized_path(summary.relative_to(repo_root).as_posix(), repo_root)
        summary.read_text(encoding="utf-8")
        manifest.append({"path": summary_rel, "sha256": file_hash(summary), "kind": "summary"})
    except FileNotFoundError:
        pass
    except (ValueError, IsADirectoryError) as exc:
        media["blockers"].append(f"invalid summary: {exc}")
    revision_input = {"queue": json.loads(canonical),
                      "files": [{"path": item["path"], "sha256": item["sha256"]}
                                for item in sorted(manifest, key=lambda m: m["path"])
                                if item["kind"] != "queue"]}
    result.update(
        media_ready=not media["blockers"], schedule_ready=not schedule,
        blockers=media["blockers"] + schedule, warnings=media["warnings"],
        manifest=manifest, posts=media["posts"],
        revision=hashlib.sha256(json.dumps(revision_input, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest(),
    )
    result["ready"] = result["media_ready"] and result["schedule_ready"]
    return result


def affected_queues(repo_root: pathlib.Path, changed: list[str]) -> list[pathlib.Path]:
    """Asset/summary-only edits select their actual queues, not all old batches."""
    selected = {pathlib.Path(name) for name in changed
                if name.startswith("queue/") and name.endswith(".json") and name.count("/") == 1}
    changed_assets = {name for name in changed if name.startswith("assets/")}
    for name in changed:
        if name.startswith("queue/") and name.endswith(".summary.md"):
            selected.add(pathlib.Path(name.removesuffix(".summary.md") + ".json"))
    if changed_assets:
        for qpath in (repo_root / "queue").glob("*.json"):
            if qpath.relative_to(repo_root) in selected:
                continue
            try:
                posts = queue_json_guard.load_json(qpath.read_text(encoding="utf-8"), qpath)
                queue_json_guard.validate_queue_shape(posts, qpath)
            except (ValueError, UnicodeError):
                continue
            references: set[str] = set()
            for post in posts:
                values = declared_media(post)
                if post["visual"].get("cover"):
                    values.append(post["visual"]["cover"])
                for value in values:
                    if not isinstance(value, str):
                        continue
                    value = value.replace("\\", "/")
                    references.add(value)
                    if pathlib.PurePosixPath(value).suffix.lower() in publish_helpers.VIDEO_EXTS:
                        references.add(str(pathlib.PurePosixPath(value).with_suffix("")) + "-cover.png")
            if references & changed_assets:
                selected.add(qpath.relative_to(repo_root))
    return sorted(path for path in selected if (repo_root / path).is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queues", nargs="*", type=pathlib.Path)
    parser.add_argument("--repo-root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--static", action="store_true", help="Check schedule structure, not current lead time.")
    parser.add_argument("--changed-from", help="Select affected queues relative to a Git base commit.")
    args = parser.parse_args()
    queues = args.queues
    if args.changed_from:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", "--diff-filter=ACDMRT",
             f"{args.changed_from}...HEAD"], cwd=args.repo_root, check=True, capture_output=True,
        ).stdout.decode("utf-8").split("\0")
        queues = affected_queues(args.repo_root, changed)
    results = {str(queue): report(queue, args.repo_root, require_future=not args.static)
               for queue in queues}
    print(json.dumps(results, indent=2))
    raise SystemExit(0 if all(value["ready"] for value in results.values()) else 1)


if __name__ == "__main__":
    main()
