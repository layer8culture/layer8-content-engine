#!/usr/bin/env python3
"""Generate visuals for queued posts via the OpenAI Images API (gpt-image-2).

Reads today's queue/<date>.json and renders a branded image for every post whose
visual.source == "openai", dispatching on the post's "format" field:

  * single  -> one image at assets/generated/<id>.png (visual.file)
  * story   -> one 9:16 frame (defaults to vertical) at the same path
  * reel    -> one 9:16 base still; scripts/reel_gen.py later animates it to mp4
  * carousel-> one image per visual.slides[] entry at assets/generated/<id>-<n>.png
               (visual.files is the ordered list; visual.file is the cover)

gpt-image-2 was chosen over the previous Higgsfield backend for its consistency
across this brand's cinematic, Afrofuturistic visual system. Prompts are still
composed per brand/visual-style.md (base prompt + add-on + scene + negative
prompt) by the generation step before this script runs.

After each image renders, the script composites branded typography onto it
(infographic / title-card style): a Space Grotesk headline + optional Inter
supporting line from the post's visual.headline / visual.subtext fields. The
image models are prompted NOT to render text
(theirs is garbled), so all on-image type is laid down here with bundled fonts
under assets/fonts/.

Requires: pip install openai requests pillow
Env vars:
  * Direct OpenAI (default):  OPENAI_API_KEY
  * Azure OpenAI (preferred when set): AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_IMAGE_DEPLOYMENT, and optionally
    AZURE_OPENAI_API_VERSION. When all three required Azure vars are present the
    script uses Azure OpenAI; otherwise it falls back to direct OpenAI.
"""
import base64
import json
import os
import string
import sys
import pathlib
from PIL import Image, ImageDraw, ImageFont, ImageOps
from openai import OpenAI, AzureOpenAI, OpenAIError

# ---------------------------------------------------------------------------
# OpenAI image model.
#
# gpt-image-2 returns base64-encoded PNG bytes (result.data[0].b64_json), NOT a
# URL. It supports a fixed set of canvas sizes; we map the brand aspect ratios
# to the closest supported portrait/landscape/square size below.
# ---------------------------------------------------------------------------
IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
# Quality tier: "low" (drafts), "medium", "high" (hero assets). The module
# default is "high" (the Azure benefit is uncharged, so favour quality); a post
# can still set visual.quality per item to "low"/"medium" for drafts. An empty
# OPENAI_IMAGE_QUALITY (e.g. an unset Actions variable) falls back to "high".
IMAGE_QUALITY = (os.environ.get("OPENAI_IMAGE_QUALITY") or "high").strip().lower()
VALID_QUALITIES = {"low", "medium", "high"}
if IMAGE_QUALITY not in VALID_QUALITIES:
    IMAGE_QUALITY = "high"
# Brand aspect ratio -> gpt-image-2 supported canvas size.
ASPECT_SIZE = {
    "1:1": "1024x1024",
    "9:16": "1024x1536",   # portrait
    "16:9": "1536x1024",   # landscape
}
DEFAULT_SIZE = "1024x1024"

# 2K master: gpt-image-2 caps natively at a 1536px long edge, so we LANCZOS-
# upscale each rendered still to IMAGE_LONG_EDGE (default 2048) *before*
# compositing brand type, keeping typography crisp at full resolution.
# Aspect ratio is preserved. Disable with OPENAI_IMAGE_2K=0 (empty/unset = on).
IMAGE_2K = os.environ.get("OPENAI_IMAGE_2K", "1").strip().lower() not in (
    "0", "false", "no", "off")
try:
    IMAGE_LONG_EDGE = int(os.environ.get("OPENAI_IMAGE_LONG_EDGE") or "2048")
except ValueError:
    IMAGE_LONG_EDGE = 2048

# ---------------------------------------------------------------------------
# Image backend selection: Azure OpenAI (preferred when configured) or direct
# OpenAI (fallback). The openai SDK ships both clients, so this adds no new
# dependency. On Azure the model passed to the Images API must be the
# *deployment name*, not "gpt-image-2"; everything else (generate/edit, the
# b64_json response, supported sizes, and quality tiers) is identical across the
# two backends.
# ---------------------------------------------------------------------------
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
AZURE_OPENAI_IMAGE_DEPLOYMENT = os.environ.get(
    "AZURE_OPENAI_IMAGE_DEPLOYMENT", "").strip()
# api-version that supports gpt-image generation/edits; override per deployment.
AZURE_OPENAI_API_VERSION = os.environ.get(
    "AZURE_OPENAI_API_VERSION", "2025-04-01-preview").strip()


def _make_image_client():
    """Build the Images API client and resolve the model identifier to use.

    Returns ``(client, model_name)``. When the Azure OpenAI env vars (endpoint +
    key + deployment) are all set we use ``AzureOpenAI`` and the *deployment
    name* as the model; otherwise we fall back to direct ``OpenAI()`` (reading
    ``OPENAI_API_KEY``) with the configured ``IMAGE_MODEL``. Behaviour is
    identical to before when Azure isn't configured.
    """
    if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY and AZURE_OPENAI_IMAGE_DEPLOYMENT:
        client = AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
        )
        print(f"Using Azure OpenAI image backend "
              f"(deployment={AZURE_OPENAI_IMAGE_DEPLOYMENT}, "
              f"api_version={AZURE_OPENAI_API_VERSION})")
        return client, AZURE_OPENAI_IMAGE_DEPLOYMENT
    client = OpenAI()
    print(f"Using direct OpenAI image backend (model={IMAGE_MODEL})")
    return client, IMAGE_MODEL


# --- Branding composite -----------------------------------------------------
# After each image is generated, the engine composites brand typography. The
# image models are prompted NOT to render text; see brand/visual-style.md.
# Wordmark compositing is disabled by default per current brand direction.
WORDMARK_PATH = pathlib.Path("assets/library/layer8-wordmark.png")
# Wordmark width as a fraction of the base image width, tuned per aspect ratio
# (taller/narrower formats get a slightly larger mark for legibility).
LOGO_WIDTH_FRAC = {"1:1": 0.26, "9:16": 0.40, "16:9": 0.20}
LOGO_MARGIN_FRAC = 0.04  # margin from the edges, as a fraction of base width
DEFAULT_LOGO_POSITION = "top-right"
# Per-account default wordmark placement. Kept for backwards-compatible queue
# parsing, but no wordmark asset is applied while ACCOUNT_WORDMARK maps to None.
ACCOUNT_LOGO_POSITION = {
    "layer8culture": "top-right",
    "lofi": "top-right",
}
# Per-account wordmark asset. None means no wordmark is composited for that
# account. Accounts not listed also skip the wordmark.
ACCOUNT_WORDMARK = {
    "layer8culture": None,
    "lofi": None,
}
# Wordmark opacity support is retained only for backwards-compatible queue parsing.
DEFAULT_LOGO_OPACITY = 1.0
SUBTLE_LOGO_OPACITY = 0.55


def composite_wordmark(
    image_path: pathlib.Path,
    aspect: str,
    position: str,
    opacity: float = DEFAULT_LOGO_OPACITY,
    wordmark_path: pathlib.Path | None = WORDMARK_PATH,
) -> bool:
    """Overlay a wordmark onto a generated image in place.

    No-op (returns False) if ``wordmark_path`` is None (the account has no
    wordmark) or the asset is missing. ``position`` is one of top-left,
    top-right, bottom-left, bottom-right, center. ``opacity`` scales the
    wordmark's alpha channel (1.0 = opaque, lower = subtle). Returns True
    when the wordmark was composited.
    """
    if wordmark_path is None or not wordmark_path.exists():
        return False
    opacity = max(0.0, min(1.0, opacity))
    base = Image.open(image_path).convert("RGBA")
    logo = Image.open(wordmark_path).convert("RGBA")
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


# --- Infographic typography overlay -----------------------------------------
# The image models are prompted NOT to render text (their typography is garbled);
# instead we composite clean, on-brand headline typography onto each image so the
# post reads like a branded infographic / title card. Headlines use Space Grotesk
# (uppercase, wide tracking per brand/visual-style.md), supporting copy uses
# Inter. Colors follow the brand system (Soft White text, Electric Blue accent on
# a deep-black scrim). Both fonts are OFL-licensed and bundled under assets/fonts.
FONTS_DIR = pathlib.Path("assets/fonts")
SPACE_GROTESK_PATH = FONTS_DIR / "SpaceGrotesk-Variable.ttf"
INTER_PATH = FONTS_DIR / "Inter-Variable.ttf"
SOFT_WHITE = (245, 245, 245, 255)
ELECTRIC_BLUE = (0, 71, 255, 255)
# Where the headline block sits. "lower-left" reads like a title card; the prompt
# is told to keep that region as clean negative space.
DEFAULT_OVERLAY_POSITION = "lower-left"


def _load_font(path: pathlib.Path, size: int, instance: str) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(path), size)
    try:
        font.set_variation_by_name(instance)
    except Exception:  # noqa: BLE001 - static fallback if no named instance
        pass
    return font


def _line_width(draw: ImageDraw.ImageDraw, text: str, font, tracking: float) -> float:
    if not text:
        return 0.0
    return sum(draw.textlength(ch, font=font) for ch in text) + tracking * (len(text) - 1)


def _wrap(draw, text: str, font, max_w: float, tracking: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = word if not current else f"{current} {word}"
        if not current or _line_width(draw, trial, font, tracking) <= max_w:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_headline(draw, text, max_w, start_size, min_size, max_lines):
    """Shrink the headline font until it wraps to <= max_lines within max_w."""
    size = start_size
    while size >= min_size:
        font = _load_font(SPACE_GROTESK_PATH, size, "Bold")
        tracking = size * 0.03
        lines = _wrap(draw, text, font, max_w, tracking)
        if len(lines) <= max_lines:
            return font, size, tracking, lines
        size -= 2
    font = _load_font(SPACE_GROTESK_PATH, min_size, "Bold")
    tracking = min_size * 0.03
    return font, min_size, tracking, _wrap(draw, text, font, max_w, tracking)


def _bottom_scrim(base: Image.Image, frac: float = 0.6, max_alpha: int = 205) -> Image.Image:
    """Composite a transparent-to-dark vertical gradient over the bottom of the
    image so overlaid text stays legible on any background."""
    w, h = base.size
    start = int(h * (1 - frac))
    column = Image.new("L", (1, h), 0)
    for y in range(start, h):
        column.putpixel((0, y), int(max_alpha * (y - start) / max(1, h - start)))
    alpha = column.resize((w, h))
    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    scrim.putalpha(alpha)
    return Image.alpha_composite(base, scrim)


def _draw_tracked_line(draw, x, y, line, font, tracking, default_fill,
                       accent_tokens, accent_fill):
    """Draw a single line with letter tracking; words whose normalized form is in
    accent_tokens are rendered in the accent color."""
    cx = x
    space_w = draw.textlength(" ", font=font)
    words = line.split(" ")
    for wi, word in enumerate(words):
        norm = word.strip(string.punctuation).lower()
        fill = accent_fill if norm and norm in accent_tokens else default_fill
        for ch in word:
            draw.text((cx, y), ch, font=font, fill=fill)
            cx += draw.textlength(ch, font=font) + tracking
        if wi != len(words) - 1:
            cx += space_w + tracking


def render_infographic(image_path: pathlib.Path, headline: str,
                       subtext: str | None = None,
                       position: str = DEFAULT_OVERLAY_POSITION,
                       accent: str | None = None) -> bool:
    """Composite a branded headline (and optional supporting line) onto an image,
    infographic / title-card style. Returns False if the fonts are missing so
    generation never depends on the overlay."""
    if not (SPACE_GROTESK_PATH.exists() and INTER_PATH.exists()):
        return False
    base = Image.open(image_path).convert("RGBA")
    w, h = base.size
    base = _bottom_scrim(base)
    draw = ImageDraw.Draw(base)

    margin = int(w * 0.07)
    max_w = w - 2 * margin
    centered = position == "lower-center"

    accent_tokens = {t.strip(string.punctuation).lower()
                     for t in (accent or "").split() if t.strip(string.punctuation)}

    headline_text = headline.strip().upper()
    font_h, size_h, track_h, lines_h = _fit_headline(
        draw, headline_text, max_w,
        start_size=int(w * 0.085), min_size=int(w * 0.042), max_lines=3)
    hlh = int(size_h * 1.16)

    sub_lines: list[str] = []
    font_s = None
    size_s = 0
    track_s = 0.0
    if subtext:
        size_s = max(int(w * 0.030), 18)
        font_s = _load_font(INTER_PATH, size_s, "Medium")
        track_s = size_s * 0.01
        sub_lines = _wrap(draw, subtext.strip(), font_s, max_w, track_s)
    slh = int(size_s * 1.35)

    accent_h = max(3, int(w * 0.012))
    accent_w = int(w * 0.10)
    accent_gap = int(size_h * 0.35)
    sub_gap = int(size_h * 0.28) if sub_lines else 0

    block_h = accent_h + accent_gap + len(lines_h) * hlh + sub_gap + len(sub_lines) * slh
    bottom_margin = int(h * 0.09)
    top = h - bottom_margin - block_h

    def line_x(line, font, tracking):
        if not centered:
            return margin
        return int((w - _line_width(draw, line, font, tracking)) / 2)

    # Electric-blue accent rule.
    bar_x = margin if not centered else int((w - accent_w) / 2)
    draw.rectangle([bar_x, top, bar_x + accent_w, top + accent_h], fill=ELECTRIC_BLUE)

    y = top + accent_h + accent_gap
    for line in lines_h:
        _draw_tracked_line(draw, line_x(line, font_h, track_h), y, line, font_h,
                           track_h, SOFT_WHITE, accent_tokens, ELECTRIC_BLUE)
        y += hlh

    if sub_lines:
        y += sub_gap - (hlh - size_h)  # tighten gap after headline
        for line in sub_lines:
            _draw_tracked_line(draw, line_x(line, font_s, track_s), y, line, font_s,
                               track_s, SOFT_WHITE, set(), SOFT_WHITE)
            y += slh

    base.convert("RGB").save(image_path)
    return True


def _decode_b64_image(result) -> bytes:
    """Extract and decode the base64 PNG bytes from an OpenAI images response."""
    b64 = result.data[0].b64_json
    return base64.b64decode(b64)


def _upscale_to_2k(path: pathlib.Path) -> None:
    """Upscale a rendered still so its long edge is ``IMAGE_LONG_EDGE`` px, keeping
    the native aspect ratio (LANCZOS). No-op when disabled or already at/above the
    target — gpt-image-2 maxes at a 1536px long edge, so this lifts stills to a 2K
    master before brand type is composited on top."""
    if not IMAGE_2K:
        return
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        long_edge = max(w, h)
        if long_edge >= IMAGE_LONG_EDGE:
            return
        scale = IMAGE_LONG_EDGE / long_edge
        im.resize((round(w * scale), round(h * scale)), Image.LANCZOS).save(path)


def _render_image(client, model: str, image_id: str, visual: dict,
                  account: str | None, out_dir: pathlib.Path) -> str | None:
    """Render one OpenAI image for ``visual``, composite branded typography,
    and return the written path (or None on API failure).

    Shared by every format: single posts, story frames, reel base stills, and each
    individual carousel slide. ``image_id`` becomes the output filename stem, so
    carousels pass ``<post_id>-<n>`` to write one file per slide.
    """
    prompt = visual.get("openai_prompt")
    if not prompt:
        return None
    aspect = visual.get("aspect", "1:1")
    size = ASPECT_SIZE.get(aspect, DEFAULT_SIZE)
    quality = visual.get("quality", IMAGE_QUALITY)
    if quality not in VALID_QUALITIES:
        print(f"  ! {image_id}: invalid quality {quality!r}, "
              f"using {IMAGE_QUALITY!r}")
        quality = IMAGE_QUALITY
    out_path = out_dir / f"{image_id}.png"
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
                            model=model,
                            image=ref_file,
                            prompt=prompt,
                            size=size,
                            quality=quality,
                        )
                    image_bytes = _decode_b64_image(result)
                    print(f"  > {image_id}: used reference {ref_path} via images.edit "
                          f"(quality={quality})")
                except OpenAIError as e:
                    # The edit call failed (e.g. unsupported reference). Retry as
                    # a plain prompt-driven generation so the branded image still
                    # renders.
                    print(f"  ! {image_id}: reference edit failed ({e}); "
                          f"retrying without reference")
                    image_bytes = None
            else:
                print(f"  ! {image_id}: style_reference {ref_path} not found, "
                      f"generating without reference")

        if image_bytes is None:
            result = client.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                quality=quality,
            )
            image_bytes = _decode_b64_image(result)

        out_path.write_bytes(image_bytes)

        # Upscale to a 2K master before laying brand type so typography is
        # composited at full resolution (gpt-image-2 caps at 1536px natively).
        try:
            _upscale_to_2k(out_path)
        except Exception as e:  # noqa: BLE001 - never lose a good image over upscale
            print(f"  ! {image_id}: 2K upscale skipped ({e})")

        # Composite branded headline typography (infographic / title-card style).
        # The image models render garbled text, so they're prompted to leave clean
        # negative space and we lay crisp brand type on top here.
        headline = visual.get("headline")
        if headline:
            try:
                applied = render_infographic(
                    out_path,
                    headline,
                    visual.get("subtext"),
                    visual.get("overlay_position", DEFAULT_OVERLAY_POSITION),
                    visual.get("accent"),
                )
                if applied:
                    print(f"  > {image_id}: headline composited "
                          f"({headline[:48]!r})")
                else:
                    print(f"  ! {image_id}: fonts missing under assets/fonts, "
                          f"headline overlay skipped")
            except Exception as e:  # noqa: BLE001 - never lose a good image over text
                print(f"  ! {image_id}: headline overlay skipped ({e})")

        # Wordmark overlay is disabled by default. Backwards-compatible logo fields
        # are parsed but ignored while ACCOUNT_WORDMARK maps accounts to None.
        try:
            wordmark_path = ACCOUNT_WORDMARK.get(account)
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
            applied = composite_wordmark(
                out_path, aspect, position, opacity, wordmark_path
            )
            if applied:
                print(f"  > {image_id}: wordmark composited "
                      f"(position={position}, opacity={opacity:.2f})")
            elif wordmark_path is None:
                print(f"  > {image_id}: no wordmark configured, skipped overlay")
            else:
                print(f"  > {image_id}: wordmark asset missing, "
                      f"no overlay (would use position={position}, "
                      f"opacity={opacity:.2f})")
        except Exception as e:  # noqa: BLE001 - never lose a good image over branding
            print(f"  ! {image_id}: wordmark overlay skipped ({e})")
        print(f"  + {image_id} -> {out_path}")
        return str(out_path)
    except OpenAIError as e:
        # e.g. rate limit, billing, or other API errors — don't crash the whole
        # queue; let the caller fall back to the library for this post.
        print(f"  x {image_id}: OpenAI error ({e}), falling back to library")
        return None


def generate(client, model: str, post: dict, out_dir: pathlib.Path) -> str | None:
    """Render the single base still for a non-carousel post (single / story / reel).

    Stories and reels are vertical by nature, so default them to 9:16 when the
    generator didn't pin an aspect. For reels this still becomes the frame that
    scripts/reel_gen.py later animates into an mp4. Returns the written path.
    """
    visual = post["visual"]
    fmt = post.get("format", "single")
    if fmt in ("story", "reel") and "aspect" not in visual:
        visual = {**visual, "aspect": "9:16"}
    return _render_image(client, model, post["id"], visual, post.get("account"), out_dir)


def render_carousel(client, model: str, post: dict, out_dir: pathlib.Path) -> list[str] | None:
    """Render each slide of a carousel post as its own branded image.

    Returns the ordered list of written paths (``<post_id>-1.png`` …) or None if
    no slide produced an image. Each slide inherits the post-level visual defaults
    it doesn't override (aspect, quality, overlay/logo placement).
    """
    visual = post["visual"]
    slides = visual.get("slides") or []
    if not slides:
        print(f"  ! {post['id']}: carousel format but no visual.slides — skipping")
        return None
    base = {
        "aspect": visual.get("aspect", "1:1"),
        "quality": visual.get("quality", IMAGE_QUALITY),
        "overlay_position": visual.get("overlay_position", DEFAULT_OVERLAY_POSITION),
    }
    for k in ("logo_position", "logo_subtle", "logo_opacity"):
        if visual.get(k) is not None:
            base[k] = visual[k]
    paths: list[str] = []
    for i, slide in enumerate(slides, 1):
        merged = {**base, **{k: v for k, v in slide.items() if v is not None}}
        path = _render_image(client, model, f"{post['id']}-{i}", merged,
                             post.get("account"), out_dir)
        if path:
            paths.append(path)
        else:
            print(f"  ! {post['id']}: slide {i} failed to render")
    return paths or None


def main(queue_file: str) -> None:
    qpath = pathlib.Path(queue_file)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    out_dir = pathlib.Path("assets/generated")
    out_dir.mkdir(parents=True, exist_ok=True)

    client, model = _make_image_client()

    for post in posts:
        if post.get("visual", {}).get("source") != "openai":
            # Non-"openai" sources are intentionally skipped here: "library" posts
            # bring their own asset, and "reuse" cross-posts (e.g. TikTok reusing
            # the day's Instagram reel) get their media in reel_gen.py's pass 2.
            continue
        if post.get("format") == "carousel":
            paths = render_carousel(client, model, post, out_dir)
            if paths:
                post["visual"]["files"] = paths
                post["visual"]["file"] = paths[0]  # primary/cover for the publisher
                continue
        else:
            path = generate(client, model, post, out_dir)
            if path:
                post["visual"]["file"] = path
                continue
        # Either format failed to render — fall back to the library so the post
        # isn't blocked. (Reels overwrite visual.file with their mp4 later.)
        post["visual"]["source"] = "library"
        post["visual"].setdefault("library_hint", "default branded graphic")

    qpath.write_text(json.dumps(posts, indent=2), encoding="utf-8")
    print("Queue updated with generated visual paths.")


if __name__ == "__main__":
    main(sys.argv[1])
