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
import json
import pathlib
import sys

from PIL import Image, ImageOps

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
# are warning thresholds, never rejection thresholds -- a batch must always ship.
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
        try:
            if openai_gen.typography_preset_for(spec.account, visual) == "editorial_drop":
                applied = openai_gen.render_editorial_drop(out_path, visual)
            else:
                applied = openai_gen.render_infographic(
                    out_path,
                    headline,
                    visual.get("subtext"),
                    visual.get("overlay_position", openai_gen.DEFAULT_OVERLAY_POSITION),
                    visual.get("accent"),
                )
            if applied:
                print(f"  > {spec.image_id}: headline composited ({str(headline)[:48]!r})")
            else:
                print(f"  ! {spec.image_id}: fonts missing under assets/fonts, "
                      f"headline overlay skipped")
        except Exception as e:  # noqa: BLE001 - never lose a good image over text
            print(f"  ! {spec.image_id}: headline overlay skipped ({e})")

    try:
        wordmark_path = openai_gen.ACCOUNT_WORDMARK.get(spec.account)
        position = visual.get(
            "logo_position",
            openai_gen.ACCOUNT_LOGO_POSITION.get(spec.account,
                                                 openai_gen.DEFAULT_LOGO_POSITION),
        )
        if "logo_opacity" in visual:
            opacity = float(visual["logo_opacity"])
        elif visual.get("logo_subtle"):
            opacity = openai_gen.SUBTLE_LOGO_OPACITY
        else:
            opacity = openai_gen.DEFAULT_LOGO_OPACITY
        if openai_gen.composite_wordmark(out_path, spec.aspect, position, opacity,
                                         wordmark_path):
            print(f"  > {spec.image_id}: wordmark composited "
                  f"(position={position}, opacity={opacity:.2f})")
    except Exception as e:  # noqa: BLE001 - never lose a good image over branding
        print(f"  ! {spec.image_id}: wordmark overlay skipped ({e})")


def ingest_image(spec: manual_media.ImageSpec, source: pathlib.Path,
                 out_dir: pathlib.Path) -> pathlib.Path:
    """Crop, resample, brand, and write one dropped image. Returns the output path."""
    out_path = spec.output_path(out_dir)
    with Image.open(source) as raw:
        im = ImageOps.exif_transpose(raw)
        im = im.convert("RGB")
        original = im.size
        im = fit_to_aspect(im, spec.aspect)
        im = normalize_resolution(im)
        out_dir.mkdir(parents=True, exist_ok=True)
        im.save(out_path)
        print(f"  + {spec.image_id}: {source.name} {original[0]}x{original[1]} "
              f"-> {out_path} {im.size[0]}x{im.size[1]} ({spec.aspect})")
        warn_if_too_dark(spec.image_id, im)
    apply_branding(out_path, spec)
    return out_path


def retire_source(source: pathlib.Path, inbox: pathlib.Path) -> None:
    """Move a consumed drop into <inbox>/_ingested/.

    Ingest always derives from the source file, so keeping it in the inbox would
    let a re-run composite typography onto an already-branded image.
    """
    done_dir = pathlib.Path(inbox) / manual_media.INGESTED_DIRNAME
    done_dir.mkdir(parents=True, exist_ok=True)
    source.replace(done_dir / source.name)


def update_queue(posts: list[dict], specs: list[manual_media.ImageSpec],
                 out_dir: pathlib.Path) -> int:
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
        if post.get("format") == "carousel":
            missing = [p for p in paths if not p.exists()]
            if missing:
                print(f"  ~ {post['id']}: carousel {len(paths) - len(missing)}/"
                      f"{len(paths)} slides ready — queue not updated yet")
                continue
            visual["files"] = [p.as_posix() for p in paths]
            visual["file"] = paths[0].as_posix()
            updated += 1
            continue
        if paths[0].exists():
            visual["file"] = paths[0].as_posix()
            updated += 1
    return updated


def main(queue_file: str, inbox: pathlib.Path = manual_media.DEFAULT_INBOX,
         out_dir: pathlib.Path = manual_media.DEFAULT_OUT_DIR,
         keep: bool = False, strict: bool = False, dry_run: bool = False) -> int:
    """Ingest every dropped image for ``queue_file``. Returns a process exit code."""
    qpath = pathlib.Path(queue_file)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    inbox = pathlib.Path(inbox)
    out_dir = pathlib.Path(out_dir)

    specs = manual_media.plan_images(
        posts, openai_gen.IMAGE_QUALITY, openai_gen.DEFAULT_OVERLAY_POSITION)
    if not specs:
        print(f"No manual images expected for {qpath} "
              f"(no post has visual.source \"openai\" with an openai_prompt).")
        return 0

    print(f"Manual image ingest: {len(specs)} expected, looking in {inbox}/")
    ingested: list[manual_media.ImageSpec] = []
    missing: list[manual_media.ImageSpec] = []
    for spec in specs:
        source = spec.find_source(inbox)
        if source is None:
            if spec.output_path(out_dir).exists():
                print(f"  = {spec.image_id}: already finished, no new drop")
            else:
                missing.append(spec)
            continue
        if dry_run:
            print(f"  . {spec.image_id}: would ingest {source.name} "
                  f"-> {spec.output_path(out_dir)}")
            ingested.append(spec)
            continue
        try:
            ingest_image(spec, source, out_dir)
        except Exception as e:  # noqa: BLE001 - one bad drop must not abort the batch
            print(f"  x {spec.image_id}: could not ingest {source.name} ({e})")
            continue
        ingested.append(spec)
        if not keep:
            try:
                retire_source(source, inbox)
            except OSError as e:
                print(f"  ! {spec.image_id}: could not move {source.name} "
                      f"into {manual_media.INGESTED_DIRNAME}/ ({e})")

    if dry_run:
        print(f"Dry run: {len(ingested)} would be ingested, {len(missing)} missing. "
              f"Queue not modified.")
    else:
        updated = update_queue(posts, specs, out_dir)
        qpath.write_text(json.dumps(posts, indent=2), encoding="utf-8")
        print(f"Ingested {len(ingested)} image(s); {updated} post(s) updated in {qpath}.")

    if missing:
        print(f"Still needed ({len(missing)}) — generate these and drop them in {inbox}/:")
        for spec in missing:
            print(f"  - {spec.inbox_path(inbox)} "
                  f"({spec.fmt}, {spec.aspect}, request {spec.size})")
        print(f"Prompts for them are in {manual_media.prompt_pack_path(qpath)}.")
        if strict:
            return 1
    elif not dry_run:
        print("All expected images are in place. Next: "
              f"python scripts/reel_gen.py {qpath.as_posix()} (reels only), "
              "then open the approval PR.")
    return 0


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
