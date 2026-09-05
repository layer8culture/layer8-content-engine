#!/usr/bin/env python3
"""Finish hand-generated images and wire them back into a queue file.

This is the second half of the engine's manual (no-API) image mode:

    python scripts/openai_gen.py queue/2026-08-18.json --manual   # prompt pack
    #  ... generate the images in ChatGPT / Copilot, save them into
    #      assets/manual-inbox/<post-id>.png (carousels: <post-id>-2.png, ...)
    python scripts/manual_media_ingest.py queue/2026-08-18.json   # this script

For every image the queue expects it looks for the dropped file, then applies the
exact same finishing the API path applies in scripts/openai_gen.py:

  * center-crop to the post's aspect ratio (chat assistants rarely return an
    exact 9:16), normalizing to PNG
  * resample to the 2K master long edge
  * composite the brand headline / subtext (brand_title_card or editorial_drop)
  * (wordmark step, currently a no-op per brand direction)

Finished files land in assets/generated/<image-id>.png and the queue's
visual.file / visual.files are updated. Images that haven't been dropped yet are
reported and left alone, so the command is safe to run repeatedly as you work
through a batch.

Requires: pip install pillow (no image API, no openai package needed).
"""
import argparse
import contextlib
import json
import pathlib
import sys
import threading

from PIL import Image, ImageOps, __version__ as PILLOW_VERSION

import manual_media
import openai_gen


def _aspect_ratio(aspect: str) -> tuple[int, int]:
    """"9:16" -> (9, 16). Falls back to square on anything unparseable."""
    try:
        w, h = (int(part) for part in str(aspect).split(":", 1))
        if w > 0 and h > 0:
            return w, h
    except (TypeError, ValueError):
        pass
    return 1, 1


def fit_to_aspect(im: Image.Image, aspect: str) -> Image.Image:
    """Center-crop ``im`` to the brand aspect ratio without ever upscaling here.

    Hand-generated images come back at whatever canvas the assistant felt like
    using; the publisher and the typography layout both assume the post's aspect.
    """
    tw, th = _aspect_ratio(aspect)
    w, h = im.size
    target = tw / th
    current = w / h
    if abs(current - target) < 0.005:
        return im
    if current > target:  # too wide -> trim the sides
        new_w = max(1, round(h * target))
        left = (w - new_w) // 2
        return im.crop((left, 0, left + new_w, h))
    new_h = max(1, round(w / target))  # too tall -> trim top/bottom
    top = (h - new_h) // 2
    return im.crop((0, top, w, top + new_h))


def normalize_resolution(im: Image.Image) -> Image.Image:
    """Resample so the long edge matches the engine's 2K master size.

    The API path upscales small renders via openai_gen._upscale_to_2k; hand-made
    images can also come back oversized, so this scales in both directions to keep
    committed assets and composited type consistent.
    """
    if not openai_gen.IMAGE_2K:
        return im
    target = openai_gen.IMAGE_LONG_EDGE
    w, h = im.size
    long_edge = max(w, h)
    if long_edge == target:
        return im
    scale = target / long_edge
    return im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                     Image.LANCZOS)


# A drop this dark has almost no tonal range left for typography to sit on. These
# are warning thresholds, never rejection thresholds.
DARK_MEAN_LUM = 26.0      # mean luminance below this reads as near-black
DARK_SHADOW_SHARE = 0.80  # ...and this share of pixels already crushed to shadow
DARK_SHADOW_LEVEL = 32    # what counts as "shadow"


def darkness_report(im: Image.Image) -> tuple[float, float]:
    """Return ``(mean_luminance, share_of_near_black_pixels)`` for a drop."""
    grey = im.convert("L")
    hist = grey.histogram()
    total = sum(hist) or 1
    mean = sum(value * count for value, count in enumerate(hist)) / total
    shadow = sum(hist[:DARK_SHADOW_LEVEL]) / total
    return mean, shadow


def warn_if_too_dark(image_id: str, im: Image.Image) -> bool:
    """Flag a drop that is too dark to survive branding. Warns; never blocks.

    Catching this at intake is the whole point: the alternative is discovering it
    in the approval PR, after the batch has already been composited.
    """
    mean, shadow = darkness_report(im)
    if mean >= DARK_MEAN_LUM and shadow <= DARK_SHADOW_SHARE:
        return False
    print(f"  ! {image_id}: very dark source (mean luminance {mean:.1f}, "
          f"{shadow:.0%} of pixels near-black). Headline typography will still "
          f"be legible, but the scene may read as an empty frame -- consider "
          f"regenerating with more light in the prompt.")
    return True


def apply_branding(out_path: pathlib.Path, spec: manual_media.ImageSpec) -> None:
    """Composite brand typography (and the wordmark step) exactly like the API path."""
    visual = spec.visual
    headline = visual.get("headline")
    if headline:
        if openai_gen.typography_preset_for(spec.account, visual) == "editorial_drop":
            applied = openai_gen.render_editorial_drop(out_path, visual)
        else:
            applied = openai_gen.render_infographic(
                out_path, headline, visual.get("subtext"),
                visual.get("overlay_position", openai_gen.DEFAULT_OVERLAY_POSITION),
                visual.get("accent"),
            )
        if not applied:
            raise RuntimeError("required headline typography failed; check assets/fonts")
        print(f"  > {spec.image_id}: headline composited ({str(headline)[:48]!r})")

    wordmark_path = openai_gen.ACCOUNT_WORDMARK.get(spec.account)
    position = visual.get(
        "logo_position",
        openai_gen.ACCOUNT_LOGO_POSITION.get(spec.account, openai_gen.DEFAULT_LOGO_POSITION),
    )
    if "logo_opacity" in visual:
        opacity = float(visual["logo_opacity"])
    elif visual.get("logo_subtle"):
        opacity = openai_gen.SUBTLE_LOGO_OPACITY
    else:
        opacity = openai_gen.DEFAULT_LOGO_OPACITY
    if openai_gen.composite_wordmark(out_path, spec.aspect, position, opacity, wordmark_path):
        print(f"  > {spec.image_id}: wordmark composited")


_FONT_LOCK = threading.RLock()
_FONT_KEYS = ("BEBAS_NEUE_PATH", "INTER_PATH", "SPACE_GROTESK_PATH")


@contextlib.contextmanager
def font_paths(repo_root: pathlib.Path | None):
    """Use the target repository's fonts without changing the process cwd."""
    with _FONT_LOCK:
        original = {key: getattr(openai_gen, key) for key in _FONT_KEYS}
        try:
            if repo_root is not None:
                for key, value in original.items():
                    setattr(openai_gen, key, repo_root / value)
            yield
        finally:
            for key, value in original.items():
                setattr(openai_gen, key, value)


def image_inputs(spec: manual_media.ImageSpec, source: pathlib.Path,
                 repo_root: pathlib.Path) -> dict:
    settings = {k: v for k, v in spec.visual.items()
                if k not in ("file", "files", "cover", "reel", "slides",
                             "media_type", "duration_sec")}
    settings.update(account=spec.account, format=spec.fmt, aspect=spec.aspect,
                    image_2k=openai_gen.IMAGE_2K, long_edge=openai_gen.IMAGE_LONG_EDGE)
    renderer = {
        "pillow": PILLOW_VERSION,
        "code": {name: manual_media.file_fingerprint(pathlib.Path(path))
                 for name, path in (("ingest", __file__),
                                    ("typography", openai_gen.__file__),
                                    ("plan", manual_media.__file__))},
        "fonts": {key: manual_media.file_fingerprint(repo_root / getattr(openai_gen, key))
                  for key in _FONT_KEYS} if spec.headline else {},
    }
    return {"source": manual_media.file_fingerprint(source),
            "settings": manual_media.fingerprint(settings),
            "renderer": manual_media.fingerprint(renderer)}


def ingest_image(spec: manual_media.ImageSpec, source: pathlib.Path,
                 out_dir: pathlib.Path, *, repo_root: pathlib.Path | None = None,
                 warnings: list[str] | None = None,
                 history_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Crop, resample, brand, and write one dropped image. Returns the output path."""
    out_path = spec.output_path(out_dir)
    stage = manual_media.staging_path(out_path)
    try:
        with Image.open(source) as raw:
            im = ImageOps.exif_transpose(raw).convert("RGB")
            im = normalize_resolution(fit_to_aspect(im, spec.aspect))
            out_dir.mkdir(parents=True, exist_ok=True)
            im.save(stage, format="PNG")
            if warn_if_too_dark(spec.image_id, im) and warnings is not None:
                warnings.append(f"{spec.image_id}: very dark source; review the scene")
        with font_paths(repo_root):
            apply_branding(stage, spec)
        with Image.open(stage) as finished:
            finished.load()
        if out_path.exists():
            root = repo_root or out_dir.parent
            manual_media.snapshot_file(
                out_path, (history_dir or root / ".local" / "media" / "outputs") / spec.image_id)
        stage.replace(out_path)
        print(f"  + {spec.image_id}: {source.name} -> {out_path} ({spec.aspect})")
    finally:
        stage.unlink(missing_ok=True)
    return out_path


def retire_source(source: pathlib.Path, inbox: pathlib.Path) -> pathlib.Path:
    """Move a consumed drop into <inbox>/_ingested/.

    Ingest always derives from the source file, so keeping it in the inbox would
    let a re-run composite typography onto an already-branded image.
    """
    done_dir = pathlib.Path(inbox) / manual_media.INGESTED_DIRNAME
    done_dir.mkdir(parents=True, exist_ok=True)
    previous = done_dir / source.name
    versions = done_dir / "_versions" / source.stem
    if previous.is_file():
        manual_media.snapshot_file(previous, versions)
    version = manual_media.snapshot_file(source, versions)
    if source.resolve() != previous.resolve():
        source.replace(previous)
    return version


def update_queue(posts: list[dict], specs: list[manual_media.ImageSpec],
                 out_dir: pathlib.Path, *, valid_ids: set[str] | None = None,
                 changed_ids: set[str] | None = None,
                 repo_root: pathlib.Path | None = None) -> int:
    """Point each post's visual.file / visual.files at finished images.

    A carousel is only wired up once *every* slide exists, so a half-finished
    batch can never publish a short carousel.
    """
    by_post: dict[str, list[manual_media.ImageSpec]] = {}
    for spec in specs:
        by_post.setdefault(spec.post_id, []).append(spec)
    updated = 0
    for post in posts:
        post_specs = by_post.get(str(post.get("id", "")).strip())
        if not post_specs:
            continue
        visual = post.setdefault("visual", {})
        paths = [spec.output_path(out_dir) for spec in post_specs]
        if post.get("format") == "reel":
            continue  # Delivery is the MP4; a still is never a replacement for it.
        if valid_ids is not None and any(s.image_id not in valid_ids for s in post_specs):
            visual.pop("file", None)
            visual.pop("files", None)
            continue
        refs = [manual_media.relative_path(p, repo_root) if repo_root else p.as_posix()
                for p in paths]
        if post.get("format") == "carousel":
            missing = [p for p in paths if not p.exists()]
            if missing:
                print(f"  ~ {post['id']}: carousel {len(paths) - len(missing)}/"
                      f"{len(paths)} slides ready — queue not updated yet")
                continue
            previous = visual.get("files") or []
            for index, spec in enumerate(post_specs):
                if (spec.visual.get("media_type") == "video"
                        and changed_ids is not None and spec.image_id not in changed_ids
                        and len(previous) > index and str(previous[index]).endswith(".mp4")):
                    refs[index] = previous[index]
            visual["files"] = refs
            visual["file"] = refs[0]
            updated += 1
            continue
        if paths[0].exists():
            visual["file"] = refs[0]
            updated += 1
    return updated


def ingest(queue_file: pathlib.Path, inbox: pathlib.Path = manual_media.DEFAULT_INBOX,
           out_dir: pathlib.Path = manual_media.DEFAULT_OUT_DIR,
           keep: bool = False, dry_run: bool = False,
           repo_root: pathlib.Path | None = None) -> dict:
    """Finish stale images and persist each artifact's outcome for safe retries."""
    qpath = pathlib.Path(queue_file)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    root = (repo_root or (qpath.resolve().parent.parent if qpath.parent.name == "queue"
                        else qpath.resolve().parent)).resolve()
    inbox = pathlib.Path(inbox)
    out_dir = pathlib.Path(out_dir)
    report = {"failed": [], "missing": [], "warnings": [], "prepared": 0, "unchanged": 0}
    specs = manual_media.plan_images(
        posts, openai_gen.IMAGE_QUALITY, openai_gen.DEFAULT_OVERLAY_POSITION)
    if not specs:
        return report
    print(f"Manual image ingest: {len(specs)} expected, looking in {inbox}/")
    valid_ids, changed_ids, invalid_posts = set(), set(), set()
    for spec in specs:
        key = f"image:{spec.image_id}"
        record = manual_media.read_record(root, key)
        source = spec.find_source(inbox)
        fresh_drop = source is not None
        if source is None and record.get("source_path"):
            candidate = manual_media.filesystem_path(root / record["source_path"])
            source = candidate if candidate.is_file() else None
        if source is None:
            source = spec.find_source(inbox / manual_media.INGESTED_DIRNAME)
        if source is None:
            report["missing"].append(spec.image_id)
            continue
        inputs = image_inputs(spec, source, root)
        if (record.get("status") == "ready" and record.get("inputs") == inputs
                and manual_media.outputs_match(record, root)):
            valid_ids.add(spec.image_id)
            report["unchanged"] += 1
            report["warnings"].extend(record.get("warnings", []))
            print(f"  = {spec.image_id}: unchanged")
            continue
        if dry_run:
            report["prepared"] += 1
            print(f"  . {spec.image_id}: would ingest {source.name}")
            continue
        warnings = []
        invalid_posts.add(spec.post_id)
        attempt = dict(record)
        try:
            version = manual_media.snapshot_file(
                source, inbox / manual_media.INGESTED_DIRNAME / "_versions" / spec.image_id)
            attempt.update(post_id=spec.post_id, status="preparing", inputs=inputs,
                           source_path=manual_media.relative_path(version, root))
            attempt.pop("error", None)
            manual_media.write_record(root, key, attempt)
            output = ingest_image(
                spec, source, out_dir, repo_root=repo_root, warnings=warnings,
                history_dir=root / ".local" / "media" / "outputs")
            if fresh_drop and not keep:
                retire_source(source, inbox)
        except (OSError, ValueError, RuntimeError, Image.DecompressionBombError) as exc:
            message = f"{spec.image_id}: could not ingest {source.name} ({exc})"
            report["failed"].append(message)
            print(f"  x {message}")
            manual_media.write_record(root, key, {
                **attempt, "post_id": spec.post_id, "status": "failed",
                "error": message, "warnings": warnings,
            })
            continue
        manual_media.write_record(root, key, {
            "post_id": spec.post_id, "status": "ready", "inputs": inputs,
            "source_path": manual_media.relative_path(version, root),
            "outputs": {manual_media.relative_path(output, root):
                        manual_media.file_fingerprint(output)}, "warnings": warnings,
        })
        valid_ids.add(spec.image_id)
        changed_ids.add(spec.image_id)
        report["warnings"].extend(warnings)
        report["prepared"] += 1
    if not dry_run:
        invalidate_delivery(posts, invalid_posts)
        manual_media.invalidate_records(invalid_posts, root, images=False)
        update_queue(posts, specs, out_dir, valid_ids=valid_ids,
                     changed_ids=changed_ids, repo_root=repo_root)
        manual_media.atomic_json(qpath, posts)
    for image_id in report["missing"]:
        print(f"  ! {image_id}: original source missing; import the image to prepare it")
    report["changed"] = sorted(changed_ids)
    return report


def invalidate_delivery(posts: list[dict], changed_post_ids: set[str]) -> None:
    affected = set(changed_post_ids)
    while True:
        dependents = {str(p.get("id")) for p in posts
                      if (p.get("visual") or {}).get("of") in affected}
        if dependents.issubset(affected):
            break
        affected.update(dependents)
    for post in posts:
        if str(post.get("id")) in affected and post.get("format") == "reel":
            visual = post.get("visual") or {}
            visual.pop("file", None)
            visual.pop("cover", None)


def main(queue_file: str, inbox: pathlib.Path = manual_media.DEFAULT_INBOX,
         out_dir: pathlib.Path = manual_media.DEFAULT_OUT_DIR,
         keep: bool = False, strict: bool = False, dry_run: bool = False) -> int:
    report = ingest(pathlib.Path(queue_file), inbox, out_dir, keep, dry_run)
    print(f"Prepared {report['prepared']} image(s); {report['unchanged']} unchanged; "
          f"{len(report['failed'])} failed; {len(report['missing'])} missing.")
    return int(bool(report["failed"] or (strict and report["missing"])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Finish hand-generated images for a queue file (manual "
                    "no-API image mode) and write their paths back into it.")
    parser.add_argument("queue_file", help="e.g. queue/2026-08-18.json")
    parser.add_argument(
        "--inbox", default=str(manual_media.DEFAULT_INBOX),
        help=f"folder holding the dropped images (default: {manual_media.DEFAULT_INBOX})")
    parser.add_argument(
        "--out-dir", default=str(manual_media.DEFAULT_OUT_DIR),
        help=f"where finished images land (default: {manual_media.DEFAULT_OUT_DIR})")
    parser.add_argument(
        "--keep", action="store_true",
        help="leave consumed files in the inbox instead of moving them to "
             f"{manual_media.INGESTED_DIRNAME}/")
    parser.add_argument(
        "--strict", action="store_true",
        help="exit non-zero if any expected image is still missing")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would be ingested without writing anything")
    args = parser.parse_args()
    sys.exit(main(args.queue_file, pathlib.Path(args.inbox),
                  pathlib.Path(args.out_dir), args.keep, args.strict, args.dry_run))
