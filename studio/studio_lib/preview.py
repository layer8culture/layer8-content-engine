"""Local HTML preview of a day's posts: media, exact caption, hashtags, schedule, gate verdicts."""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from .config import media_dir
from .schedule import parse

CSS = """
body{margin:0;background:#0b0d12;color:#e8eaf0;font:15px/1.5 Inter,Segoe UI,sans-serif}
header{padding:28px 36px;border-bottom:1px solid #1d2230}
h1{margin:0 0 6px;font-size:24px}
.sum{color:#9aa3b5;max-width:1100px}
.stats{margin-top:10px;display:flex;gap:18px;color:#c7cdd9}
.post{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:28px;padding:28px 36px;border-bottom:1px solid #1d2230}
.media{display:flex;gap:10px;overflow-x:auto;padding-bottom:8px}
.media img,.media video{height:440px;border-radius:10px;flex:none;background:#000}
.meta h2{margin:0 0 4px;font-size:18px}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;margin:0 6px 6px 0;background:#1d2230;color:#c7cdd9}
.pass{background:#0f3d24;color:#7ee2a8}.fail{background:#4a1418;color:#ff9a9a}.warn{color:#f5c56b}
.cap{white-space:pre-wrap;background:#11141c;border:1px solid #1d2230;border-radius:10px;padding:14px;margin:10px 0}
.tags{color:#7fa3ff}
ul{margin:6px 0;padding-left:18px}
.hero{font-size:13px;color:#9aa3b5}
"""


def esc(t) -> str:
    return html.escape(str(t or ""))


def build(plan: dict, d: date, manifest: dict, gate_results: dict, hero_rows: list[dict], cfg: dict) -> Path:
    out = media_dir(d) / "preview.html"
    tz = cfg.get("timezone", "America/New_York")
    heroes = {r["post"]: r for r in hero_rows}
    n_pass = sum(1 for r in gate_results.values() if r.get("pass"))
    cards = []
    for p in sorted(plan["posts"], key=lambda p: min((p.get("schedule") or {"x": "9999"}).values())):
        m = manifest.get(p["id"]) or {}
        media = []
        for f in m.get("files", []):
            if f.endswith(".mp4"):
                media.append(f'<video src="{esc(f)}" controls loop muted playsinline poster="{esc(f.replace(".mp4", "-cover.png"))}"></video>')
            elif not f.endswith("-cover.png"):
                media.append(f'<img src="{esc(f)}" loading="lazy">')
        if m.get("error"):
            media.append(f'<div class="fail pill">render error: {esc(m["error"][:300])}</div>')
        g = gate_results.get(p["id"]) or {}
        verdict = '<span class="pill pass">GATES PASS</span>' if g.get("pass") else '<span class="pill fail">BLOCKED</span>'
        errs = "".join(f"<li>{esc(e)}</li>" for e in g.get("errors", []))
        warns = "".join(f'<li class="warn">{esc(w)}</li>' for w in g.get("warnings", []))
        sched = " · ".join(f"{esc(k)} {parse(v).astimezone(__import__('zoneinfo').ZoneInfo(tz)).strftime('%a %I:%M %p')}"
                           for k, v in (p.get("schedule") or {}).items())
        h = heroes.get(p["id"])
        hero = ""
        if h:
            hero = (f'<div class="hero">Hero: <code>{esc(h["filename"])}</code> — '
                    f'{"composited ✓" if h["ready"] else "waiting for ChatGPT image (template version posts if it never arrives)"}</div>')
        yt = f'<div><b>YouTube title:</b> {esc(p["youtube_title"])}</div>' if p.get("youtube_title") else ""
        src = "".join(f'<li><a href="{esc(u)}" style="color:#7fa3ff">{esc(u)}</a></li>' for u in p.get("sources") or [])
        cards.append(f"""
<section class="post"><div class="media">{''.join(media) or '<div class="pill fail">no media</div>'}</div>
<div class="meta"><h2>{esc(p['id'])}</h2>
<span class="pill">{esc(p['brand'])}</span><span class="pill">{esc(p['format'])}</span><span class="pill">{esc(p.get('category'))}</span>{verdict}
<div><b>When:</b> {sched}</div><div><b>Topic:</b> {esc(p.get('topic'))}</div>{yt}{hero}
<div class="cap">{esc(p.get('caption'))}</div><div class="tags">{esc(' '.join(p.get('hashtags') or []))}</div>
{f'<ul>{errs}</ul>' if errs else ''}{f'<ul>{warns}</ul>' if warns else ''}{f'<div><b>Sources</b><ul>{src}</ul></div>' if src else ''}
</div></section>""")
    news = "".join(f'<li><a style="color:#7fa3ff" href="{esc(n.get("url"))}">{esc(n.get("title"))}</a> — {esc(n.get("source"))}</li>'
                   for n in plan.get("news_used") or [])
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Layer8 Studio — {d.isoformat()}</title><style>{CSS}</style></head>
<body><header><h1>Layer8 Studio · {d.strftime('%A %B %d, %Y')}</h1><div class="sum">{esc(plan.get('summary'))}</div>
<div class="stats"><span>{len(plan['posts'])} posts</span><span>{n_pass} pass gates</span><span>model: {esc(plan.get('model'))}</span>
<span>{sum(1 for r in hero_rows if r['ready'])}/{len(hero_rows)} heroes in</span></div>
{f'<details style="margin-top:10px"><summary>News used</summary><ul>{news}</ul></details>' if news else ''}</header>
{''.join(cards)}</body></html>"""
    out.write_text(doc, encoding="utf-8")
    return out
