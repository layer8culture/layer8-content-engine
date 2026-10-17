"""Small content mutations for the guided UI; callers hold the workflow lock."""
from __future__ import annotations

import io
import json
import pathlib
import re
import shutil
import tempfile
import uuid
import zipfile

from PIL import Image, ImageOps

import manual_media

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def atomic_json(path: pathlib.Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".write-", suffix=".tmp", delete=False) as handle:
        temp = pathlib.Path(handle.name)
        json.dump(value, handle, indent=2)
        handle.write("\n")
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def checked_id(value: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError("Invalid image identifier.")
    return value


def original(spec, root: pathlib.Path) -> pathlib.Path | None:
    inbox = root / manual_media.DEFAULT_INBOX
    fresh = spec.find_source(inbox)
    if fresh is not None:
        return fresh
    record = manual_media.read_record(root, f"image:{spec.image_id}") or {}
    if record.get("source_path"):
        candidate = root / record["source_path"]
        allowed = inbox / manual_media.INGESTED_DIRNAME
        if not candidate.resolve().is_relative_to(allowed.resolve()):
            raise ValueError("Original image record points outside the source archive.")
        return candidate if candidate.is_file() else None
    return spec.find_source(inbox / manual_media.INGESTED_DIRNAME)


def versions(root: pathlib.Path, image_id: str) -> list[pathlib.Path]:
    directory = root / ".local" / "versions" / checked_id(image_id)
    if not directory.exists():
        return []
    return sorted((p for p in directory.glob("*/manifest.json")
                   if not p.with_name("restored").exists()),
                  key=lambda p: p.stat().st_mtime_ns, reverse=True)


def snapshot(root: pathlib.Path, queue: str, spec) -> None:
    source = original(spec, root)
    if source is None:
        raise ValueError("The original source is unavailable. Import a source before editing its typography.")
    folder = root / ".local" / "versions" / checked_id(spec.image_id) / uuid.uuid4().hex
    folder.mkdir(parents=True)
    shutil.copy2(source, folder / "source.png")
    atomic_json(folder / "manifest.json", {
        "queue": queue, "image_id": spec.image_id, "visual": spec.visual,
        "source": "source.png",
    })


def decode_image(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Image exceeds the 40-megapixel import limit.")
            image.load()
            return ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f"Unreadable image: {exc}") from exc


def invalidate(root: pathlib.Path, post_ids: list[str]) -> None:
    import prepare_media
    prepare_media.invalidate(post_ids, root)


def replace_image(root: pathlib.Path, qpath: pathlib.Path, spec, data: bytes) -> None:
    image = decode_image(data)
    if original(spec, root) is not None:
        snapshot(root, qpath.name, spec)
    inbox = root / manual_media.DEFAULT_INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    # Normalize before assignment so extension precedence cannot select an old PNG.
    target = inbox / f"{checked_id(spec.image_id)}.png"
    with tempfile.NamedTemporaryFile(dir=inbox, suffix=".png", delete=False) as handle:
        temp = pathlib.Path(handle.name)
    try:
        image.save(temp, format="PNG")
        temp.replace(target)
    finally:
        image.close()
        temp.unlink(missing_ok=True)
    invalidate(root, [spec.post_id])


def visual_for(posts: list[dict], spec) -> dict:
    post = next(p for p in posts if p["id"] == spec.post_id)
    visual = post.setdefault("visual", {})
    if spec.slide_index is not None:
        return visual["slides"][spec.slide_index - 1]
    return visual


def edit_image(root: pathlib.Path, qpath: pathlib.Path, posts: list[dict],
               spec, headline: str, subtext: str) -> None:
    if not isinstance(headline, str) or not isinstance(subtext, str):
        raise ValueError("Headline and supporting text must be text.")
    if not headline.strip():
        raise ValueError("A headline is required for branded images.")
    snapshot(root, qpath.name, spec)
    visual_for(posts, spec).update(headline=headline.strip(), subtext=subtext.strip())
    atomic_json(qpath, posts)
    invalidate(root, [spec.post_id])


def undo_image(root: pathlib.Path, qpath: pathlib.Path, posts: list[dict], spec) -> None:
    history = versions(root, spec.image_id)
    if not history:
        raise ValueError("No earlier source is available.")
    # Rescheduling renames the queue, but the image's identity and history remain.
    manifest_path = history[0]
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("image_id") != spec.image_id:
        raise ValueError("The saved version belongs to a different image.")
    source = manifest_path.with_name("source.png")
    inbox = root / manual_media.DEFAULT_INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, inbox / f"{checked_id(spec.image_id)}.png")
    visual = visual_for(posts, spec)
    for key in ("headline", "subtext"):
        if key in data["visual"]:
            visual[key] = data["visual"][key]
        else:
            visual.pop(key, None)
    atomic_json(qpath, posts)
    invalidate(root, [spec.post_id])
    manifest_path.with_name("restored").touch()


def edit_post(qpath: pathlib.Path, posts: list[dict], post_id: str, changes: dict) -> None:
    allowed = {"text", "hashtags", "first_comment", "youtube_title", "schedule_time"}
    if not isinstance(changes, dict) or not changes or set(changes) - allowed:
        raise ValueError("Only caption, hashtags, first comment, title and schedule may be edited here.")
    post = next((p for p in posts if p.get("id") == post_id), None)
    if post is None:
        raise ValueError("Unknown post.")
    for key, value in changes.items():
        if key == "hashtags":
            if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
                raise ValueError("Hashtags must be a list of strings.")
        elif not isinstance(value, str):
            raise ValueError(f"{key} must be text.")
    if "schedule_time" in changes:
        import datetime as dt
        try:
            when = dt.datetime.fromisoformat(changes["schedule_time"])
        except ValueError as exc:
            raise ValueError("Use an ISO schedule with an explicit timezone offset.") from exc
        if when.utcoffset() is None:
            raise ValueError("The schedule must include a timezone offset.")
    post.update(changes)
    atomic_json(qpath, posts)


def download_bundle(posts: list[dict], manifest: list[dict], root: pathlib.Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for item in manifest:
            path = root / item["path"]
            archive.write(path, item["path"])
        archive.writestr("posts.json", json.dumps(posts, indent=2))
        archive.writestr("README.txt", "Prepared assets and captions. Downloading does not schedule posts.\n")
    return output.getvalue()
