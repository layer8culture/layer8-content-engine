"""Automated quality gates. With no human approval step, a post only publishes if every gate passes."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from urllib.parse import urlparse

from PIL import Image

from . import context, schema
from .config import media_dir

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF]")
URL_RE = re.compile(r"https?://[^\s)>\]]+|\b(?:www\.)?[a-z0-9-]+\.(?:io|com|ai|net|org|co|be)(?:/[^\s)]*)?", re.I)
PLACEHOLDER_RE = re.compile(r"\[[^\]]{0,40}\]|\{\{|\}\}|\bTODO\b|\bTBD\b|lorem ipsum|<insert|\bXX+\b|\bINSERT\b", re.I)
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:a\.?m\.?|p\.?m\.?)\b|\b\d{1,2}(?::\d{2})?\s?(?:ET|EST|EDT|PT|CT)\b", re.I)
CTA_RE = re.compile(r"\b(save|share|follow|comment|reply|tell|drop|join|tune in|listen|subscribe|send|tag|try|link in bio|press play|bookmark|dm)\b", re.I)
EXPECTED = {"carousel": (1080, 1350), "single": (1080, 1350), "story": (1080, 1920), "reel": (1080, 1920)}


def all_text(p: dict) -> str:
    bits = [p.get("hook"), p.get("caption"), p.get("youtube_title"), p.get("kicker")]
    for s in p.get("slides") or []:
        if isinstance(s, dict):
            bits += [s.get("headline"), s.get("accent"), s.get("body"), s.get("code"), *(s.get("items") or [])]
    reel = p.get("reel") or {}
    for b in reel.get("beats") or []:
        if isinstance(b, dict):
            bits += [b.get("text"), b.get("accent")]
    bits += [reel.get("prompt_box"), reel.get("cta")]
    return "\n".join(str(b) for b in bits if b)


def phrase_hits(text: str, phrases: list[str]) -> list[str]:
    low = text.lower().replace("’", "'")
    hits = []
    for ph in phrases:
        ph_l = str(ph).lower()
        pat = re.escape(ph_l)
        if ph_l[:1].isalnum():
            pat = r"\b" + pat
        if ph_l[-1:].isalnum():
            pat += r"\b"
        if re.search(pat, low):
            hits.append(str(ph))
    return hits


def text_checks(p: dict, cfg: dict) -> tuple[list[str], list[str]]:
    g = cfg["gates"]
    errs, warns = [], []
    text = all_text(p)
    caption = p.get("caption") or ""
    for h in phrase_hits(text, g.get("banned_phrases", [])):
        errs.append(f"banned/hype phrase: '{h}'")
    if p.get("brand") == "radio":
        for h in phrase_hits(text, g.get("radio_banned_topics", [])):
            errs.append(f"radio must stay music/focus only, found '{h}'")
    if caption.count("!") > g.get("max_exclamations", 1):
        errs.append(f"too many exclamation marks ({caption.count('!')})")
    n_emoji = len(EMOJI_RE.findall(text))
    if n_emoji > g.get("max_emojis", 3):
        errs.append(f"too many emojis ({n_emoji})")
    if len(p.get("hook") or "") > g.get("hook_max_chars", 140):
        errs.append("hook too long")
    tags = p.get("hashtags") or []
    full = caption + "\n\n" + " ".join(tags)
    if len(full) > g.get("caption_max_chars", 2200):
        errs.append(f"caption+hashtags {len(full)} > {g.get('caption_max_chars', 2200)} chars")
    if len(tags) > g.get("max_hashtags_instagram", 8):
        errs.append(f"{len(tags)} hashtags > {g.get('max_hashtags_instagram', 8)}")
    if len({t.lower() for t in tags}) != len(tags):
        errs.append("duplicate hashtags")
    if "#" in caption:
        errs.append("hashtags must not be inside the caption body")
    m = PLACEHOLDER_RE.search(text)
    if m:
        errs.append(f"placeholder text: '{m.group(0)}'")
    allowed = [d.lower() for d in g.get("allowed_link_domains", [])]
    for url in URL_RE.findall(caption):
        host = urlparse(url if "://" in url else "https://" + url).hostname or ""
        host = host.lower().removeprefix("www.")
        if not any(host == d or host.endswith("." + d) for d in allowed):
            errs.append(f"link to non-allowed domain: {url}")
    facts = cfg.get("facts") or {}
    if not facts.get("tech_thursday_time") and TIME_RE.search(text):
        errs.append(f"states a time ('{TIME_RE.search(text).group(0)}') but no show time is configured in facts")
    if p.get("brand") == "radio" and not facts.get("radio_stream_is_live_24_7") and re.search(r"\b(live now|24/7|live 24)", text, re.I):
        errs.append("claims the radio stream is live 24/7 but facts.radio_stream_is_live_24_7 is false")
    words = re.findall(r"[A-Za-z]{4,}", caption)
    if words and sum(w.isupper() for w in words) / len(words) > 0.3:
        warns.append("caption is mostly ALL CAPS")
    if not CTA_RE.search(caption):
        warns.append("no clear call to action in caption")
    reel = p.get("reel") or {}
    for i, b in enumerate(reel.get("beats") or []):
        t = (b or {}).get("text", "") if isinstance(b, dict) else ""
        if len(t.split()) > g.get("beat_max_words", 5):
            errs.append(f"beat {i + 1} has {len(t.split())} words > {g.get('beat_max_words', 5)}")
        if len(t) > g.get("beat_max_chars", 26):
            errs.append(f"beat {i + 1} is {len(t)} chars > {g.get('beat_max_chars', 26)}")
    if "youtube" in (p.get("platforms") or []):
        yt = sum(len(t) for t in tags[: g.get("max_hashtags_youtube", 5)])
        if yt > 450:
            errs.append("youtube tags too long")
    return errs, warns


def duplicate_checks(plan: dict, d: date, cfg: dict) -> dict[str, list[str]]:
    g = cfg["gates"]
    thr = float(g.get("duplicate_similarity", 0.55))
    hist = context.history(int(g.get("duplicate_lookback_days", 21)), d)
    out: dict[str, list[str]] = {}
    posts = plan.get("posts", [])
    for i, p in enumerate(posts):
        key = f"{p.get('topic', '')} {p.get('hook', '')}"
        for r in hist:
            if r.get("brand") not in (p.get("brand"), "lofi" if p.get("brand") == "radio" else None):
                continue
            s = context.similarity(key, f"{r.get('topic') or ''} {r.get('hook') or ''}")
            if s >= thr:
                out.setdefault(p["id"], []).append(f"repeats a recent post ({r.get('date')}: '{(r.get('topic') or r.get('hook') or '')[:60]}', sim {s:.2f})")
                break
        for q in posts[:i]:
            if q.get("brand") == p.get("brand") and context.similarity(key, f"{q.get('topic', '')} {q.get('hook', '')}") >= thr:
                out.setdefault(p["id"], []).append(f"duplicates {q['id']} in today's plan")
    return out


def _probe(path) -> dict | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
        j = json.loads(r.stdout)
        s = j["streams"][0]
        return {"width": s["width"], "height": s["height"], "duration": float(j["format"]["duration"])}
    except Exception:  # noqa: BLE001
        return None


def media_checks(p: dict, d: date, manifest: dict, cfg: dict) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    m = manifest.get(p["id"])
    if not m:
        return ["not rendered"], warns
    if m.get("error"):
        return [f"render error: {m['error'][:200]}"], warns
    exp = EXPECTED[p["format"]]
    n_slides = len(p.get("slides") or [])
    if p["format"] != "reel" and len(m.get("files", [])) != n_slides:
        errs.append(f"rendered {len(m.get('files', []))} images for {n_slides} slides")
    for f in m.get("files", []):
        path = media_dir(d) / f
        if not path.exists():
            errs.append(f"missing media {f}")
            continue
        if f.endswith(".png"):
            with Image.open(path) as im:
                if im.size != exp:
                    errs.append(f"{f} is {im.size[0]}x{im.size[1]}, expected {exp[0]}x{exp[1]}")
        elif f.endswith(".mp4"):
            info = _probe(path)
            if not info:
                errs.append(f"{f} unreadable")
            else:
                if (info["width"], info["height"]) != exp:
                    errs.append(f"{f} is {info['width']}x{info['height']}")
                if not 3 <= info["duration"] <= 60:
                    errs.append(f"{f} duration {info['duration']:.1f}s outside 3-60s")
    min_k = float(cfg["gates"].get("min_font_scale", 0.62))
    for fit in m.get("fit", []):
        if fit.get("overflow"):
            errs.append(f"slide {fit['slide']}: text does not fit even at {min_k:.0%} size")
        if fit.get("overlap"):
            errs.append(f"slide {fit['slide']}: text overlaps header/footer")
        elif fit.get("k", 1) < 0.75:
            warns.append(f"slide {fit['slide']}: copy shrunk to {fit['k']:.0%}")
    return errs, warns


def run_gates(plan: dict, d: date, cfg: dict, manifest: dict | None = None) -> dict:
    """Returns {post_id: {"pass": bool, "errors": [...], "warnings": [...]}}. manifest=None skips media checks."""
    compact = d.strftime("%Y%m%d")
    dups = duplicate_checks(plan, d, cfg)
    res = {}
    for p in plan.get("posts", []):
        errs = schema.validate_post(p, compact)
        e2, warns = text_checks(p, cfg)
        errs += e2 + dups.get(p.get("id"), [])
        if manifest is not None and not schema.validate_post(p, compact):
            e3, w3 = media_checks(p, d, manifest, cfg)
            errs += e3
            warns += w3
        res[p.get("id", "?")] = {"pass": not errs, "errors": errs, "warnings": warns}
    return res


def save(results: dict, d: date) -> None:
    media_dir(d).mkdir(parents=True, exist_ok=True)
    (media_dir(d) / "gates.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


def load(d: date) -> dict:
    p = media_dir(d) / "gates.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
