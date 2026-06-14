#!/usr/bin/env python3
"""Generate visuals for queued posts via the OpenAI Images API (gpt-image-2).

Reads today's queue/<date>.json, finds posts with visual.source == "openai",
submits each prompt, decodes the returned image, writes it into
assets/generated/<post_id>.png, and writes the local path back into the post
object as visual.file.

gpt-image-2 was chosen over the previous Higgsfield backend for its consistency
across this brand's cinematic, Afrofuturistic visual system. Prompts are still
composed per brand/visual-style.md (base prompt + add-on + scene + negative
prompt) by the generation step before this script runs.

Requires: pip install openai requests pillow
Env vars: OPENAI_API_KEY
"""
import base64
import json
import os
import sys
import pathlib
from PIL import Image
from openai import OpenAI, OpenAIError

# ---------------------------------------------------------------------------
# OpenAI image model.
#
# gpt-image-2 returns base64-encoded PNG bytes (result.data[0].b64_json), NOT a
# URL. It supports a fixed set of canvas sizes; we map the brand aspect ratios
# to the closest supported portrait/landscape/square size below.
# ---------------------------------------------------------------------------
IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
# Quality tier: "low" (drafts), "medium", "high" (hero assets). The module
# default is "medium" (cost-safe); a post sets visual.quality per item, and the
# generator marks exactly one daily hero as "high".
IMAGE_QUALITY = os.environ.get("OPENAI_IMAGE_QUALITY", "medium")
VALID_QUALITIES = {"low", "medium", "high"}
# Brand aspect ratio -> gpt-image-2 supported canvas size.
ASPECT_SIZE = {
    "1:1": "1024x1024",
    "9:16": "1024x1536",   # portrait
    "16:9": "1536x1024",   # landscape
}
DEFAULT_SIZE = "1024x1024"

# --- Branding composite -----------------------------------------------------
# After each image is generated, overlay the official Layer8Culture wordmark so
# branding is consistent and crisp (the models are prompted NOT to render text;
# see brand/visual-style.md). The overlay is skipped silently if the wordmark
# asset is missing, so generation never depends on it.
WORDMARK_PATH = pathlib.Path("assets/library/layer8-wordmark.png")
# Wordmark width as a fraction of the base image width, tuned per aspect ratio
# (taller/narrower formats get a slightly larger mark for legibility).
LOGO_WIDTH_FRAC = {"1:1": 0.26, "9:16": 0.40, "16:9": 0.20}
LOGO_MARGIN_FRAC = 0.04  # margin from the edges, as a fraction of base width
DEFAULT_LOGO_POSITION = "top-right"
# Per-account default wordmark placement. A post can still override with
# visual.logo_position; this only sets the fallback when none is given.
ACCOUNT_LOGO_POSITION = {
    "layer8culture": "top-right",
    "lofi": "top-right",
}
# Wordmark opacity (0.0 transparent .. 1.0 opaque). Posts may override with
# visual.logo_opacity, or set the boolean visual.logo_subtle flag to apply the
# subtle preset for an understated, watermark-style mark.
DEFAULT_LOGO_OPACITY = 1.0
SUBTLE_LOGO_OPACITY = 0.55


def composite_wordmark(
    image_path: pathlib.Path,
    aspect: str,
    position: str,
    opacity: float = DEFAULT_LOGO_OPACITY,
) -> bool:
    """Overlay the Layer8Culture wordmark onto a generated image in place.

    No-op (returns False) if the wordmark asset is missing. ``position`` is one
    of top-left, top-right, bottom-left, bottom-right, center. ``opacity`` scales
    the wordmark's alpha channel (1.0 = opaque, lower = subtle). Returns True
    when the wordmark was composited.
    """
    if not WORDMARK_PATH.exists():
        return False
    opacity = max(0.0, min(1.0, opacity))
    base = Image.open(image_path).convert("RGBA")
    logo = Image.open(WORDMARK_PATH).convert("RGBA")
    bw, bh = base.size
    target_w = max(1, int(bw * LOGO_WIDTH_FRAC.get(aspect, 0.26)))
    scale = target_w / logo.width
    logo = logo.resize((target_w, max(1, int(logo.height * scale))), Image.LANCZOS)
    if opacity < 1.0:
        alpha = logo.getchannel("A").point(lambda a: int(a * opacity))
        logo.putalpha(alpha)
    lw, lh = logo.size
    margin = int(bw * LOGO_MARGIN_FRAC)
    positions = {
        "top-left": (margin, margin),
        "top-right": (bw - lw - margin, margin),
        "bottom-left": (margin, bh - lh - margin),
        "bottom-right": (bw - lw - margin, bh - lh - margin),
        "center": ((bw - lw) // 2, (bh - lh) // 2),
    }
    pos = positions.get(position, positions[DEFAULT_LOGO_POSITION])
    base.alpha_composite(logo, pos)
    base.convert("RGB").save(image_path)
    return True


def _decode_b64_image(result) -> bytes:
    """Extract and decode the base64 PNG bytes from an OpenAI images response."""
    b64 = result.data[0].b64_json
    return base64.b64decode(b64)


def generate(client: OpenAI, post: dict, out_dir: pathlib.Path) -> str | None:
    visual = post["visual"]
    prompt = visual.get("openai_prompt")
    if not prompt:
        return None
    aspect = visual.get("aspect", "1:1")
    size = ASPECT_SIZE.get(aspect, DEFAULT_SIZE)
    quality = visual.get("quality", IMAGE_QUALITY)
    if quality not in VALID_QUALITIES:
        print(f"  ! {post['id']}: invalid quality {quality!r}, "
              f"using {IMAGE_QUALITY!r}")
        quality = IMAGE_QUALITY
    out_path = out_dir / f"{post['id']}.png"
    try:
        image_bytes: bytes | None = None

        # Optional reference image: steer the look via the image edit endpoint.
        # NOTE: images.edit anchors the output to this exact reference, so reusing
        # one shared reference across posts makes every image come out nearly
        # identical. Style consistency should come from the BASE PROMPT in
        # brand/visual-style.md instead; only set style_reference when a post
        # genuinely needs to transform that specific source image. When it's
        # absent we use images.generate (below) for varied, topic-specific output.
        style_reference = visual.get("style_reference")
        if style_reference:
            ref_path = pathlib.Path(style_reference)
            if ref_path.exists():
                try:
                    with ref_path.open("rb") as ref_file:
                        result = client.images.edit(
                            model=IMAGE_MODEL,
                            image=ref_file,
                            prompt=prompt,
                            size=size,
                            quality=quality,
                        )
                    image_bytes = _decode_b64_image(result)
                    print(f"  > {post['id']}: used reference {ref_path} via images.edit "
                          f"(quality={quality})")
                except OpenAIError as e:
                    # The edit call failed (e.g. unsupported reference). Retry as
                    # a plain prompt-driven generation so the branded image still
                    # renders.
                    print(f"  ! {post['id']}: reference edit failed ({e}); "
                          f"retrying without reference")
                    image_bytes = None
            else:
                print(f"  ! {post['id']}: style_reference {ref_path} not found, "
                      f"generating without reference")

        if image_bytes is None:
            result = client.images.generate(
                model=IMAGE_MODEL,
                prompt=prompt,
                size=size,
                quality=quality,
            )
            image_bytes = _decode_b64_image(result)

        out_path.write_bytes(image_bytes)

        # Overlay the official wordmark (models are prompted not to render text).
        try:
            account = post.get("account")
            position = visual.get(
                "logo_position",
                ACCOUNT_LOGO_POSITION.get(account, DEFAULT_LOGO_POSITION),
            )
            # Opacity: explicit logo_opacity wins; else logo_subtle picks the
            # subtle preset; else fully opaque.
            if "logo_opacity" in visual:
                opacity = float(visual["logo_opacity"])
            elif visual.get("logo_subtle"):
                opacity = SUBTLE_LOGO_OPACITY
            else:
                opacity = DEFAULT_LOGO_OPACITY
            applied = composite_wordmark(out_path, aspect, position, opacity)
            if applied:
                print(f"  > {post['id']}: wordmark composited "
                      f"(position={position}, opacity={opacity:.2f})")
            else:
                print(f"  > {post['id']}: wordmark asset missing, "
                      f"no overlay (would use position={position}, "
                      f"opacity={opacity:.2f})")
        except Exception as e:  # noqa: BLE001 - never lose a good image over branding
            print(f"  ! {post['id']}: wordmark overlay skipped ({e})")
        print(f"  + {post['id']} -> {out_path}")
        return str(out_path)
    except OpenAIError as e:
        # e.g. rate limit, billing, or other API errors — don't crash the whole
        # queue; let main() fall back to the library for this post.
        print(f"  x {post['id']}: OpenAI error ({e}), falling back to library")
        return None


def main(queue_file: str) -> None:
    qpath = pathlib.Path(queue_file)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    out_dir = pathlib.Path("assets/generated")
    out_dir.mkdir(parents=True, exist_ok=True)

    client = OpenAI()

    for post in posts:
        if post.get("visual", {}).get("source") == "openai":
            path = generate(client, post, out_dir)
            if path:
                post["visual"]["file"] = path
            else:
                # Fall back to the library so the post isn't blocked.
                post["visual"]["source"] = "library"
                post["visual"].setdefault("library_hint", "default branded graphic")

    qpath.write_text(json.dumps(posts, indent=2), encoding="utf-8")
    print("Queue updated with generated visual paths.")


if __name__ == "__main__":
    main(sys.argv[1])
