"""Daily plan schema + validator (no external jsonschema dependency)."""
from __future__ import annotations

import re

BRANDS = {"layer8culture", "radio"}
FORMATS = {"carousel", "single", "story", "reel"}
PLATFORMS = {"instagram", "youtube"}
LAYER8_CATEGORIES = [
    "Build in Public", "AI Tool Experiments", "Tech Thursday Promos", "Livestream Clips",
    "Creator Systems", "AI Fluency Education", "Behind the Scenes", "Sponsor Thank-Yous",
    "LoFi Stream Promos", "Community Questions",
]
RADIO_TYPES = ["brand-intro", "video-promo", "quote", "loop-reel", "behind-the-scenes", "playlist", "community"]
LAYOUTS = {"cover", "point", "cta", "quote", "list", "story"}
ID_RE = re.compile(r"^\d{8}-(l8|radio)-\d{2}$")
HERO_RE = re.compile(r"^\d{8}-(hero|radio)-[a-z0-9-]{2,40}\.png$")

SCHEMA_DOC = """
{
  "date": "YYYY-MM-DD",
  "summary": "2-3 sentence narrative of the day's plan",
  "news_used": [{"title": "...", "url": "https://...", "source": "..."}],
  "posts": [
    {
      "id": "YYYYMMDD-l8-01 | YYYYMMDD-radio-01",
      "brand": "layer8culture | radio",
      "format": "carousel | single | story | reel",
      "platforms": ["instagram"] or ["instagram", "youtube"] (youtube only for reels),
      "category": "one of the 10 layer8 categories | one radio post type",
      "topic": "short dedupe key, <= 80 chars, e.g. 'redact customer data before prompting'",
      "slot": "morning | midday | afternoon | evening",
      "hook": "the first line of the caption, verbatim",
      "caption": "full Instagram caption starting with the hook; ONE CTA; no hashtags inside",
      "hashtags": ["#Layer8Culture", "..."],
      "youtube_title": "required when youtube is a platform; <= 100 chars; ends with #Shorts",
      "kicker": "small label, <= 34 chars, e.g. 'AI FIELD GUIDE' (layer8) or 'NIGHT CODING' (radio)",
      "slides": [
        {"layout": "cover|point|cta|quote|list|story", "headline": "<= 32 chars", "accent": "optional blue/gold second line <= 32 chars",
         "body": "optional <= 180 chars", "code": "optional example prompt <= 160 chars", "items": ["optional list items, <= 5, <= 40 chars each"]}
      ],
      "reel": {
        "beats": [{"text": "<= 4 words", "accent": "optional 1-2 words shown in accent color", "seconds": 3}],
        "prompt_box": "optional typed example prompt <= 120 chars (layer8 only)",
        "cta": "closing line <= 60 chars",
        "loop_clip": "radio only: filename in inbox/video/ e.g. radio-rainy-night.mp4"
      },
      "hero": {"filename": "YYYYMMDD-hero-slug.png | YYYYMMDD-radio-slug.png", "aspect": "4:5 | 9:16",
               "shot": "one-paragraph scene description for ChatGPT image generation"},
      "sources": ["https://... (required for news-driven posts)"]
    }
  ]
}
""".strip()


def _s(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_post(p: dict, date_compact: str) -> list[str]:
    errs: list[str] = []
    pid = p.get("id", "?")
    e = lambda m: errs.append(f"{pid}: {m}")  # noqa: E731
    if not isinstance(p, dict):
        return [f"post is not an object: {p!r}"]
    if not (_s(p.get("id")) and ID_RE.match(p["id"])):
        e("id must match YYYYMMDD-(l8|radio)-NN")
    elif not p["id"].startswith(date_compact):
        e("id date prefix does not match plan date")
    brand = p.get("brand")
    if brand not in BRANDS:
        e(f"brand must be one of {sorted(BRANDS)}")
    elif _s(p.get("id")) and ("-radio-" in p["id"]) != (brand == "radio"):
        e("id brand segment does not match brand")
    fmt = p.get("format")
    if fmt not in FORMATS:
        e(f"format must be one of {sorted(FORMATS)}")
    plats = p.get("platforms")
    if not isinstance(plats, list) or not plats or not set(plats) <= PLATFORMS or len(set(plats)) != len(plats):
        e("platforms must be a non-empty unique subset of instagram/youtube")
        plats = []
    if "youtube" in plats and fmt != "reel":
        e("youtube is only allowed for reels (Shorts)")
    cats = LAYER8_CATEGORIES if brand == "layer8culture" else RADIO_TYPES
    if p.get("category") not in cats:
        e(f"category must be one of {cats}")
    for field in ("topic", "hook", "caption", "kicker"):
        if not _s(p.get(field)):
            e(f"{field} is required")
    if _s(p.get("topic")) and len(p["topic"]) > 80:
        e("topic > 80 chars")
    if _s(p.get("kicker")) and len(p["kicker"]) > 34:
        e("kicker > 34 chars")
    if _s(p.get("hook")) and _s(p.get("caption")) and not p["caption"].strip().startswith(p["hook"].strip()):
        e("caption must start with the hook verbatim")
    tags = p.get("hashtags")
    if not isinstance(tags, list) or not tags or not all(isinstance(t, str) and re.fullmatch(r"#[A-Za-z0-9_]{2,40}", t) for t in tags):
        e("hashtags must be a non-empty list of #Tags")
    if "youtube" in plats:
        t = p.get("youtube_title")
        if not _s(t) or not 2 <= len(t) <= 100 or "#shorts" not in t.lower():
            e("youtube_title required (2-100 chars, include #Shorts)")
    slides = p.get("slides")
    if fmt in {"carousel", "single", "story"}:
        if not isinstance(slides, list):
            e("slides list required")
        else:
            if fmt == "carousel" and not 3 <= len(slides) <= 8:
                e("carousel needs 3-8 slides")
            if fmt in {"single", "story"} and len(slides) != 1:
                e(f"{fmt} needs exactly 1 slide")
            for i, s in enumerate(slides):
                if not isinstance(s, dict) or not _s(s.get("headline")):
                    e(f"slide {i + 1} needs a headline")
                    continue
                if s.get("layout", "cover") not in LAYOUTS:
                    e(f"slide {i + 1} layout invalid")
                if len(s["headline"]) > 40:
                    e(f"slide {i + 1} headline > 40 chars")
                if s.get("items") is not None and (not isinstance(s["items"], list) or len(s["items"]) > 6):
                    e(f"slide {i + 1} items must be a list of <= 6")
    if fmt == "reel":
        reel = p.get("reel")
        if not isinstance(reel, dict):
            e("reel block required")
        else:
            beats = reel.get("beats")
            if not isinstance(beats, list) or not 2 <= len(beats) <= 5:
                e("reel needs 2-5 beats")
            else:
                for i, b in enumerate(beats):
                    if not isinstance(b, dict) or not _s(b.get("text")):
                        e(f"beat {i + 1} needs text")
            if not _s(reel.get("cta")):
                e("reel.cta required")
            if brand == "radio" and not _s(reel.get("loop_clip")):
                e("radio reels need reel.loop_clip")
    hero = p.get("hero")
    if hero is not None:
        if not isinstance(hero, dict) or not _s(hero.get("filename")) or not HERO_RE.match(hero["filename"]):
            e("hero.filename must look like YYYYMMDD-hero-slug.png")
        elif not hero["filename"].startswith(date_compact):
            e("hero filename date prefix must match plan date")
        if isinstance(hero, dict) and hero.get("aspect") not in {"4:5", "9:16"}:
            e("hero.aspect must be 4:5 or 9:16")
        if isinstance(hero, dict) and not _s(hero.get("shot")):
            e("hero.shot required")
    srcs = p.get("sources", [])
    if not isinstance(srcs, list) or not all(isinstance(u, str) and u.startswith("https://") for u in srcs):
        e("sources must be a list of https URLs")
    return errs


def validate_plan(plan: dict, expected_date: str | None = None) -> list[str]:
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    errs = []
    d = plan.get("date")
    if not _s(d) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return ["date must be YYYY-MM-DD"]
    if expected_date and d != expected_date:
        errs.append(f"date {d} != expected {expected_date}")
    posts = plan.get("posts")
    if not isinstance(posts, list) or not posts:
        return errs + ["posts must be a non-empty list"]
    compact = d.replace("-", "")
    ids = [p.get("id") for p in posts if isinstance(p, dict)]
    if len(ids) != len(set(ids)):
        errs.append("post ids must be unique")
    heroes = [p["hero"]["filename"] for p in posts if isinstance(p, dict) and isinstance(p.get("hero"), dict) and p["hero"].get("filename")]
    if len(heroes) != len(set(heroes)):
        errs.append("hero filenames must be unique")
    for p in posts:
        errs.extend(validate_post(p, compact))
    return errs
