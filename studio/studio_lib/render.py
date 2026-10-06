"""Render plan posts to media: Playwright HTML templates (stills) + Remotion (reels)."""
from __future__ import annotations

import html
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

from .config import FONTS, INBOX, REMOTION, ROOT, TEMPLATES, media_dir

SIZES = {"carousel": (1080, 1350), "single": (1080, 1350), "story": (1080, 1920), "reel": (1080, 1920)}

FIT_JS = """
async (minK) => {
  await document.fonts.ready;
  const s = document.querySelector('.s'), body = s.querySelector('.body');
  const tag = s.querySelector('.tag'), foot = s.querySelector('.foot');
  const bad = () => {
    const first = body.firstElementChild;
    const top = first ? first.getBoundingClientRect().top < body.getBoundingClientRect().top - 1 : false;
    const wide = [...s.querySelectorAll('.h,.sub,.card,.items li,.num')].some(e => e.scrollWidth > e.clientWidth + 1);
    return top || wide;
  };
  let k = 1;
  while (bad() && k > minK) { k = Math.round((k - 0.03) * 100) / 100; s.style.setProperty('--k', k); }
  const b = body.getBoundingClientRect();
  const first = body.firstElementChild ? body.firstElementChild.getBoundingClientRect() : b;
  const overlap = (tag && first.top < tag.getBoundingClientRect().bottom) || (foot && b.bottom > foot.getBoundingClientRect().top + 1);
  return {k, overflow: bad(), overlap: !!overlap};
}
"""


def esc(t) -> str:
    return html.escape(str(t or ""))


def hero_file(post: dict, d: date) -> Path | None:
    """Cropped hero (from ingest) if present."""
    h = post.get("hero") or {}
    if not h.get("filename"):
        return None
    p = media_dir(d) / "heroes" / h["filename"]
    return p if p.exists() else None


def _slide_html(post: dict, slide: dict, idx: int, total: int, hero: Path | None) -> str:
    radio = post["brand"] == "radio"
    fmt = post["format"]
    w, h = SIZES[fmt]
    layout = slide.get("layout") or ("story" if fmt == "story" else "cover" if idx == 0 else "point")
    css = (TEMPLATES / ("radio.css" if radio else "layer8.css")).read_text(encoding="utf-8").replace("{{FONTS}}", FONTS.as_uri())
    cls = ["s"]
    if fmt == "story":
        cls.append("story")
    if hero is not None and idx == 0:
        cls.append("hero")
    elif not radio and idx % 2:
        cls.append("alt")
    q = ("“", "”") if layout == "quote" else ("", "")
    head = q[0] + esc(slide.get("headline"))
    if slide.get("accent"):
        head += f"<span>{esc(slide['accent'])}{q[1]}</span>"
    else:
        head += q[1]
    hs = {"cover": 168, "story": 176, "quote": 120, "list": 140, "cta": 150, "point": 140}[layout] if not radio else \
        {"cover": 118, "story": 124, "quote": 92, "list": 100, "cta": 104, "point": 104}[layout]
    if fmt == "carousel" and idx > 0 and not radio:
        hs = min(hs, 130)
    parts = []
    if layout == "point" and not radio and fmt == "carousel":
        parts.append(f'<div class="num">{idx:02d}</div>')
    parts.append(f'<div class="h" style="--hs:{hs}px">{head}</div>')
    if slide.get("body"):
        parts.append(f'<div class="sub">{esc(slide["body"])}</div>')
    if slide.get("items"):
        lis = "".join(f'<li data-n="{i + 1:02d}">{esc(t)}</li>' for i, t in enumerate(slide["items"]))
        parts.append(f'<ul class="items">{lis}</ul>')
    if slide.get("code"):
        if radio:
            parts.append(f'<div class="sub">{esc(slide["code"])}</div>')
        else:
            parts.append(f'<div class="card">Try this prompt:<code>{esc(slide["code"])}</code></div>')
    if layout == "cta" and not radio:
        parts.append('<div class="cue">SAVE THIS · FOLLOW FOR MORE</div>')
    if radio and layout in ("cover", "story", "cta"):
        parts.append('<div class="wave">' + "".join(f'<i style="height:{14 + (i * 37) % 46}px"></i>' for i in range(14)) + "</div>")
    if radio:
        right = f"{idx + 1}/{total}" if total > 1 else "LOFI · FOCUS"
        foot = f'<div class="foot"><span><b>●</b> LAYER8CULTURE RADIO</span><span>{right}</span></div>'
        deco = '<div class="glow"></div>'
        tag = f'<div class="tag">{esc(post.get("kicker"))}</div>'
    else:
        right = "SWIPE →" if total > 1 and idx == 0 else (f"{idx + 1}/{total}" if total > 1 else "WE'RE THE EIGHTH")
        foot = f'<div class="foot"><span class="sw">LAYER8CULTURE</span><span>{right}</span></div>'
        deco = '<div class="light"></div>'
        tag = f'<div class="tag">{esc(post.get("kicker"))} <b>//</b> LAYER8</div>'
    bg = ""
    if hero is not None and idx == 0:
        bg = f'<div class="bgimg" style="background-image:url(\'{hero.as_uri()}\')"></div><div class="scrim"></div>'
        deco = ""
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>"
            f"<div class='{' '.join(cls)}' style='--H:{h}px'>{bg}{deco}{tag}<div class='spacer'></div>"
            f"<div class='body'>{''.join(parts)}</div>{foot}</div></body></html>")


def render_stills(posts: list[dict], d: date, cfg: dict, log=print) -> dict:
    from playwright.sync_api import sync_playwright

    out = media_dir(d)
    tmp = out / "_html"
    tmp.mkdir(parents=True, exist_ok=True)
    min_k = float(cfg["gates"].get("min_font_scale", 0.62))
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for post in posts:
            fmt = post["format"]
            w, h = SIZES[fmt]
            page = browser.new_page(viewport={"width": w, "height": h})
            slides = post.get("slides") or []
            hero = hero_file(post, d)
            files, fits = [], []
            for i, slide in enumerate(slides):
                name = f"{post['id']}.png" if len(slides) == 1 else f"{post['id']}-{i + 1}.png"
                src = tmp / name.replace(".png", ".html")
                src.write_text(_slide_html(post, slide, i, len(slides), hero), encoding="utf-8")
                page.goto(src.as_uri())
                fit = page.evaluate(FIT_JS, min_k)
                page.locator(".s").screenshot(path=str(out / name))
                files.append(name)
                fits.append({"slide": i + 1, **fit})
            page.close()
            results[post["id"]] = {"files": files, "fit": fits, "hero": hero is not None}
            log(f"  rendered {post['id']} ({fmt}, {len(files)} image{'s' if len(files) != 1 else ''}{', hero' if hero else ''})")
        browser.close()
    return results


def _npx() -> str:
    return shutil.which("npx") or "npx"


def ensure_remotion(log=print) -> None:
    pub = REMOTION / "public"
    pub.mkdir(exist_ok=True)
    for f in FONTS.glob("*.ttf"):
        if not (pub / f.name).exists():
            shutil.copy2(f, pub / f.name)
    if not (REMOTION / "node_modules" / "remotion").exists():
        log("  installing Remotion (first run) …")
        subprocess.run([shutil.which("npm") or "npm", "install", "--no-audit", "--no-fund"], cwd=REMOTION, check=True)


def render_reel(post: dict, d: date, cfg: dict, log=print) -> dict:
    ensure_remotion(log)
    out = media_dir(d)
    pub = REMOTION / "public"
    tmp = pub / "_job"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    reel = post.get("reel") or {}
    props = {
        "brand": post["brand"], "kicker": post.get("kicker", ""), "beats": reel.get("beats", []),
        "prompt_box": reel.get("prompt_box", "") if post["brand"] == "layer8culture" else "",
        "cta": reel.get("cta", ""), "hero": None, "bgVideo": None, "audio": None,
    }
    hero = hero_file(post, d)
    clip = reel.get("loop_clip")
    if not clip and (INBOX / "video" / f"{post['id']}.mp4").exists():
        clip = f"{post['id']}.mp4"  # manual / Higgsfield premium clip
    if clip and (INBOX / "video" / clip).exists():
        shutil.copy2(INBOX / "video" / clip, tmp / clip)
        props["bgVideo"] = f"_job/{clip}"
    elif hero and cfg["visual"].get("ken_burns_reels_with_hero", True):
        shutil.copy2(hero, tmp / hero.name)
        props["hero"] = f"_job/{hero.name}"
    bed = cfg["visual"].get("layer8_audio_bed")
    if bed and post["brand"] == "layer8culture" and (ROOT / bed).exists():
        shutil.copy2(ROOT / bed, tmp / Path(bed).name)
        props["audio"] = f"_job/{Path(bed).name}"
    props_file = tmp / "props.json"
    props_file.write_text(json.dumps(props), encoding="utf-8")
    mp4 = out / f"{post['id']}.mp4"
    cmd = [_npx(), "remotion", "render", "src/index.jsx", "Reel", str(mp4), f"--props={props_file}",
           "--log=error", "--overwrite"]
    proc = subprocess.run(cmd, cwd=REMOTION, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if proc.returncode != 0 or not mp4.exists():
        raise RuntimeError(f"remotion render failed for {post['id']}: {proc.stderr[-1500:] or proc.stdout[-1500:]}")
    cover = out / f"{post['id']}-cover.png"
    # Cover frame: end of the first beat, when its words have fully landed.
    first = float(((reel.get("beats") or [{}])[0]).get("seconds") or 3)
    t = max(0.8, min(first - 0.4, 2.5))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(mp4), "-frames:v", "1", str(cover)],
                   check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    log(f"  rendered {post['id']} (reel{', loop clip' if props['bgVideo'] else ', hero' if props['hero'] else ''})")
    return {"files": [mp4.name, cover.name], "fit": [], "hero": bool(props["hero"]), "props": props}


def manifest_path(d: date) -> Path:
    return media_dir(d) / "manifest.json"


def load_manifest(d: date) -> dict:
    p = manifest_path(d)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def render_plan(plan: dict, d: date, cfg: dict, *, only: set[str] | None = None, log=print) -> dict:
    out = media_dir(d)
    out.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(d)
    posts = [p for p in plan["posts"] if not only or p["id"] in only]
    stills = [p for p in posts if p["format"] != "reel"]
    if stills:
        manifest.update(render_stills(stills, d, cfg, log=log))
    for p in posts:
        if p["format"] == "reel":
            try:
                manifest[p["id"]] = render_reel(p, d, cfg, log=log)
            except Exception as exc:  # noqa: BLE001 - one failed reel must not sink the day
                log(f"  ! {exc}")
                manifest[p["id"]] = {"files": [], "fit": [], "hero": False, "error": str(exc)[:500]}
    manifest_path(d).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
