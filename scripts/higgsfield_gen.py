#!/usr/bin/env python3
"""Generate visuals for queued posts via the Higgsfield API.

Reads today's queue/<date>.json, finds posts with visual.source == "higgsfield",
submits each prompt, polls until complete, downloads the result into
assets/generated/<post_id>.png (or .mp4), and writes the local path back into
the post object as visual.file.

Requires: pip install higgsfield-client requests pillow
Env vars: HF_API_KEY, HF_API_SECRET
"""
import json
import os
import sys
import pathlib
import requests
from PIL import Image
import higgsfield_client as hf

# ---------------------------------------------------------------------------
# Higgsfield model slugs — DO NOT GUESS.
#
# There is NO programmatic catalog: the SDK exposes no list/models method
# (dir(higgsfield_client) has only submit/subscribe/cancel/status/result/
# upload*), and the REST API has no list endpoint — `GET /models` (and every
# variant) returns 405/404 because `https://platform.higgsfield.ai/{model_id}`
# is POST-only. The only browseable catalog is the Models Gallery web UI at
# https://cloud.higgsfield.ai (requires a logged-in dashboard session, not the
# API key/secret). Confirmed against docs.higgsfield.ai on 2026-06-12.
#
# VERIFIED-REAL slugs (named in official docs.higgsfield.ai, 2026-06-12):
#   text-to-image:   higgsfield-ai/soul/standard   (flagship; all doc examples)
#                    reve/text-to-image
#   image-to-video:  higgsfield-ai/dop/standard
#                    higgsfield-ai/dop/preview
#                    bytedance/seedance/v1/pro/image-to-video
#                    kling-video/v2.1/pro/image-to-video
#
# CONFIRMED-INVALID slugs (returned "Model not found" from the live API — never
# reuse these): bytedance/seedream/v4/text-to-image, nano-banana/text-to-image
#
# To verify NEW slugs / reference (input_images) support: browse the gallery UI
# and/or run scripts/verify_models.py with valid HF credentials. The docs do
# not publish which models accept input_images, so reference support must be
# confirmed against the live account before hardcoding a different slug here.
# ---------------------------------------------------------------------------
ASPECT_RES = {"1:1": "1080x1080", "9:16": "1080x1920", "16:9": "1920x1080"}
# higgsfield-ai/soul/standard is the documented standard-tier text-to-image
# model (cost-effective; supports 720p/1080p).
IMAGE_MODEL = "higgsfield-ai/soul/standard"
# soul/standard accepts "720p" | "1080p" (not "2K"). 720p = lowest credit cost.
IMAGE_RESOLUTION = "720p"
# Model used when a post supplies visual.style_reference (passed as input_images).
# Defaults to the verified-real flagship slug so we never submit a nonexistent
# model. Override with HF_REFERENCE_MODEL once a reference/edit-capable slug is
# confirmed in the gallery UI for this account (e.g. a Soul/Flux-Kontext edit
# model). If the chosen model rejects input_images, generate() retries without
# the reference before falling back to the library.
REFERENCE_IMAGE_MODEL = os.environ.get("HF_REFERENCE_MODEL", IMAGE_MODEL)

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


def generate(post: dict, out_dir: pathlib.Path) -> str | None:
    visual = post["visual"]
    prompt = visual.get("higgsfield_prompt")
    if not prompt:
        return None
    aspect = visual.get("aspect", "1:1")
    model = IMAGE_MODEL
    arguments = {"prompt": prompt, "resolution": IMAGE_RESOLUTION, "aspect_ratio": aspect}
    used_reference = False
    try:
        # Optional reference image: upload the local file and pass it as a
        # reference, switching to the reference-capable model.
        style_reference = visual.get("style_reference")
        if style_reference:
            ref_path = pathlib.Path(style_reference)
            if ref_path.exists():
                ref_url = hf.upload_file(ref_path)
                model = REFERENCE_IMAGE_MODEL
                arguments["input_images"] = [ref_url]
                used_reference = True
                print(f"  > {post['id']}: using reference {ref_path} via {model}")
            else:
                print(f"  ! {post['id']}: style_reference {ref_path} not found, "
                      f"generating without reference")

        try:
            controller = hf.submit(model, arguments=arguments)
        except hf.HiggsfieldClientError as e:
            if used_reference:
                # The chosen model likely doesn't accept input_images for this
                # account. Retry as a plain prompt-driven generation on the
                # verified base model so the branded image still renders.
                print(f"  ! {post['id']}: reference submit failed ({e}); "
                      f"retrying without reference on {IMAGE_MODEL}")
                arguments.pop("input_images", None)
                model = IMAGE_MODEL
                controller = hf.submit(model, arguments=arguments)
            else:
                raise
        for status in controller.poll_request_status():
            if isinstance(status, (hf.Failed, hf.NSFW, hf.Cancelled)):
                print(f"  x {post['id']}: generation failed ({type(status).__name__})")
                return None
            if isinstance(status, hf.Completed):
                break
        result = controller.get()
        url = result["images"][0]["url"]
        out_path = out_dir / f"{post['id']}.png"
        out_path.write_bytes(requests.get(url, timeout=120).content)
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
    except hf.HiggsfieldClientError as e:
        # e.g. not_enough_credits or other API errors — don't crash the whole
        # queue; let main() fall back to the library for this post.
        print(f"  x {post['id']}: Higgsfield error ({e}), falling back to library")
        return None

def main(queue_file: str) -> None:
    qpath = pathlib.Path(queue_file)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    out_dir = pathlib.Path("assets/generated")
    out_dir.mkdir(parents=True, exist_ok=True)

    for post in posts:
        if post.get("visual", {}).get("source") == "higgsfield":
            path = generate(post, out_dir)
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
