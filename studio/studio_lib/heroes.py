"""ChatGPT hero batch: write the prompt pack, then ingest images the user saves into studio/inbox/.

We never drive chatgpt.com. The user pastes PROMPTS-<date>.md into ChatGPT by hand and saves
the images with the exact filenames listed. Missing heroes fall back to the template version.
"""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps

from .config import INBOX, media_dir

ASPECTS = {"4:5": (1080, 1350), "9:16": (1080, 1920)}
EXTS = (".png", ".jpg", ".jpeg", ".webp")

LAYER8_STYLE = """LOCKED STYLE (apply to every shot, never drift):
- Format: the aspect named in each shot (4:5 portrait 1080x1350 unless the shot says 9:16 vertical 1080x1920), photoreal, cinematic editorial still.
- Palette: deep black and midnight navy (#050A1A) dominate; Electric Blue (#0047FF)
  is the ONLY light accent (holograms, rim light, glow); small warm touches allowed
  (walnut wood, brass desk lamp, warm city bokeh). No purple, no teal, no rainbow.
- Subject: Black creatives and professionals, shown from behind, in silhouette, or in
  3/4 profile. Calm, focused, confident. Never cheesy smiling, never a stock-photo look.
- Lighting: low-key, volumetric blue rim light, soft haze, subtle film grain, 35mm,
  shallow depth of field.
- Vibe: premium, quiet power, Afrofuturist undertone, builder-first. Nothing like a
  "hacker in a hoodie" or generic robot cliche.
- COMPOSITION RULE: The subject and focal point sit in the TOP 55% of the frame.
  The BOTTOM 45% must fade into clean, dark, nearly empty space (floor, desk shadow,
  or darkness) because headline text will be placed there.
- ABSOLUTELY NO text, letters, numbers, logos, UI words, captions, or watermarks
  anywhere in the image. Holographic panels show only abstract shapes, icons, or
  light patterns."""

RADIO_OVERRIDE = ("RADIO STYLE OVERRIDE for this shot only: cozy Afrofuturist lofi mood, warm amber and gold "
                  "(#F5A524) mixed with soft cyan (#00BFFF) on midnight navy, calm and late-night, no hype. "
                  "Same no-text and bottom-45%-dark rules apply.")


def shots_for(plan: dict) -> list[dict]:
    out = []
    for p in plan.get("posts", []):
        h = p.get("hero") or {}
        if h.get("filename") and h.get("shot"):
            out.append({"post": p["id"], "brand": p["brand"], "filename": h["filename"],
                        "aspect": h.get("aspect") or ("9:16" if p["format"] in ("story", "reel") else "4:5"),
                        "shot": " ".join(h["shot"].split())})
    return out


def write_prompt_pack(plan: dict, d: date, deadline: str | None = None) -> Path | None:
    shots = shots_for(plan)
    if not shots:
        return None
    INBOX.mkdir(parents=True, exist_ok=True)
    lines = [f"# ChatGPT hero batch — {d.isoformat()}", "",
             f"Save each image into `studio/inbox/` with the exact filename shown. "
             f"Anything not saved by post time{f' ({deadline})' if deadline else ''} falls back to the template design automatically.",
             "",
             "ChatGPT may or may not return multiple separate image files from one prompt; a Pro subscription does not guarantee it. "
             "If it returns one, ask it to continue the remaining shots. Never use a collage.",
             "", "## ONE-PASTE PROMPT", "",
             "You are my image studio for the Layer8Culture brand.", "", LAYER8_STYLE, "",
             "BATCH REQUEST:",
             f"Generate ALL {len(shots)} numbered shots from this single request when your image interface supports it. "
             "Each shot must be a SEPARATE downloadable image with the exact filename in brackets.",
             "Never combine shots into a collage, contact sheet, grid, diptych, or multi-panel image. "
             "Preserve each requested aspect ratio and complete the full list without waiting for me to type “next”.",
             "If your current interface can only generate one image per response, generate the first shot now. "
             "When I say “continue”, generate the rest in order without making me paste the style or list again.",
             "", "SHOT LIST:"]
    for i, s in enumerate(shots, 1):
        fmt = "VERTICAL 9:16 (1080x1920). " if s["aspect"] == "9:16" else ""
        extra = RADIO_OVERRIDE + " " if s["brand"] == "radio" else ""
        lines.append(f"{i}. [{s['filename']}] {fmt}{extra}{s['shot']}")
    lines += ["", "Begin now. Return images only, each as its own downloadable file.", ""]
    path = INBOX / f"PROMPTS-{d.isoformat()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _find(filename: str) -> Path | None:
    stem = Path(filename).stem
    for ext in EXTS:
        for cand in (INBOX / f"{stem}{ext}", INBOX / f"{stem}{ext.upper()}"):
            if cand.exists():
                return cand
    return None


def crop_to(src: Path, aspect: str, dest: Path) -> Path:
    size = ASPECTS.get(aspect, ASPECTS["4:5"])
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        # Subject lives in the top 55%: bias the vertical crop slightly upward.
        out = ImageOps.fit(im, size, method=Image.LANCZOS, centering=(0.5, 0.4))
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, "PNG", optimize=True)
    return dest


def ingest(plan: dict, d: date, log=print) -> list[str]:
    """Crop any newly arrived heroes for this plan. Returns post ids that need re-rendering."""
    changed = []
    used = INBOX / "_used"
    for s in shots_for(plan):
        src = _find(s["filename"])
        if not src:
            continue
        dest = media_dir(d) / "heroes" / s["filename"]
        crop_to(src, s["aspect"], dest)
        used.mkdir(exist_ok=True)
        shutil.move(str(src), str(used / src.name))
        changed.append(s["post"])
        log(f"  hero ingested: {s['filename']} -> {s['post']} ({s['aspect']})")
    return changed


def status(plan: dict, d: date) -> list[dict]:
    rows = []
    for s in shots_for(plan):
        ready = (media_dir(d) / "heroes" / s["filename"]).exists()
        rows.append({**s, "ready": ready, "waiting": not ready and _find(s["filename"]) is not None})
    return rows
