"""Pull recent AI news from RSS/Atom feeds into a markdown digest for the planner."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from .config import DATA

UA = {"User-Agent": "Layer8Studio/1.0 (+https://layer8culture.io)"}


def _text(el, *names) -> str:
    for n in names:
        found = el.find(n)
        if found is not None:
            if found.text and found.text.strip():
                return found.text.strip()
            if found.get("href"):
                return found.get("href")
    return ""


def _when(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _clean(s: str, n: int = 280) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + ("…" if len(s) > n else "")


def parse_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    for el in root.iter():  # strip namespaces
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    items = []
    for it in list(root.iter("item")) + list(root.iter("entry")):
        link = _text(it, "link")
        if not link:
            le = it.find("link")
            link = le.get("href") if le is not None else ""
        items.append({
            "title": _clean(_text(it, "title"), 160),
            "url": link,
            "published": _when(_text(it, "pubDate", "published", "updated", "date")),
            "summary": _clean(_text(it, "description", "summary", "content")),
        })
    return items


def fetch_news(cfg: dict, for_date: date) -> tuple[str, list[dict]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(cfg.get("news_days", 5)))
    out, errors = [], []
    for feed in cfg.get("news_feeds", []):
        try:
            r = requests.get(feed["url"], headers=UA, timeout=20)
            r.raise_for_status()
            for item in parse_feed(r.text)[:25]:
                if item["published"] and item["published"] >= cutoff and item["url"].startswith("https://"):
                    out.append({**item, "source": feed["name"]})
        except Exception as exc:  # noqa: BLE001 - one bad feed must not stop planning
            errors.append(f"{feed['name']}: {exc.__class__.__name__}")
    out.sort(key=lambda x: x["published"], reverse=True)
    lines = [f"# AI news digest for planning {for_date.isoformat()}",
             f"Fetched {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from {len(cfg.get('news_feeds', []))} feeds; {len(out)} items in the last {cfg.get('news_days', 5)} days.", ""]
    if errors:
        lines.append(f"Feeds that failed: {', '.join(errors)}\n")
    for it in out[:60]:
        lines.append(f"- [{it['source']}] {it['published']:%Y-%m-%d} — **{it['title']}** — {it['url']}\n  {it['summary']}")
    if not out:
        lines.append("NO FRESH NEWS AVAILABLE. Use evergreen practical lessons only; do not invent news.")
    text = "\n".join(lines)
    path = DATA / "news" / f"{for_date.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text, out
