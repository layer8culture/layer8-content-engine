"""Planner context: transcripts, posted history, dedupe helpers."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from .config import DATA, LEGACY_HISTORY, POSTED_LOG, ROOT

TRANSCRIPTS = ROOT / "transcripts"
STOP = set("a an the and or of to for in on with your you we our is are be it this that how what why when from at by as do don't not into vs".split())


def vtt_to_text(raw: str) -> str:
    out, last = [], None
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit() or line.startswith(("NOTE", "Kind:", "Language:")):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and line != last:
            out.append(line)
            last = line
    return " ".join(out)


def latest_transcript(show: str = "tech-thursday") -> dict | None:
    idx_path = TRANSCRIPTS / "index.json"
    rows = []
    if idx_path.exists():
        rows = [r for r in json.loads(idx_path.read_text(encoding="utf-8-sig")).get("files", []) if r.get("show") == show]
    rows = [r for r in rows if (TRANSCRIPTS / r["file"]).exists()]
    # Unindexed drops: date from a YYYY-MM-DD in the name, else file mtime.
    known = {r["file"] for r in rows}
    if idx_path.exists():
        known |= {r["file"] for r in json.loads(idx_path.read_text(encoding="utf-8-sig")).get("files", [])}
    for f in TRANSCRIPTS.glob("*.vtt"):
        if f.name in known:
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        d = m.group(1) if m else datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
        rows.append({"file": f.name, "date": d, "show": show})
    if not rows:
        return None
    row = sorted(rows, key=lambda r: (r["date"], r["file"]))[-1]
    text = vtt_to_text((TRANSCRIPTS / row["file"]).read_text(encoding="utf-8", errors="replace"))
    return {**row, "text": text}


def write_transcript_context(max_chars: int = 45000) -> str | None:
    t = latest_transcript()
    if not t:
        return None
    body = t["text"]
    if len(body) > max_chars:
        body = body[:max_chars] + " …[truncated]"
    path = DATA / "context" / "transcript-latest.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Latest Tech Thursday transcript\nFile: {t['file']}\nAired: {t['date']}\n\n{body}\n", encoding="utf-8")
    return str(path.relative_to(ROOT)).replace("\\", "/")


def read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def append_jsonl(path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def history(lookback_days: int, before: date) -> list[dict]:
    """Posted + legacy rows within the window: {date, brand, topic, hook}."""
    cutoff = before - timedelta(days=lookback_days)
    rows = []
    for r in read_jsonl(LEGACY_HISTORY) + read_jsonl(POSTED_LOG):
        try:
            d = date.fromisoformat(str(r.get("date"))[:10])
        except ValueError:
            continue
        if cutoff <= d < before:
            rows.append(r)
    return rows


def write_history_context(before: date, days: int = 30) -> str:
    rows = history(days, before)
    seen, lines = set(), [f"# Recently posted (last {days} days) — do NOT repeat these lessons/topics", ""]
    for r in sorted(rows, key=lambda r: str(r.get("date")), reverse=True):
        key = (r.get("brand"), r.get("topic") or r.get("hook"))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {r.get('date')} [{r.get('brand')}] {r.get('topic') or ''} — {r.get('hook') or ''}")
    if len(lines) == 2:
        lines.append("(nothing posted recently)")
    path = DATA / "context" / "history.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path.relative_to(ROOT)).replace("\\", "/")


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words if w not in STOP and len(w) > 1}


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def seed_legacy_history(log_path, out_path=LEGACY_HISTORY) -> int:
    """One-time import of the old engine's posted/log.json into a compact dedupe history."""
    data = json.loads(open(log_path, encoding="utf-8").read())
    rows = []
    for p in data:
        when = str(p.get("schedule_time") or "")[:10]
        if not when:
            continue
        text = (p.get("text") or "").strip().splitlines()
        vis = p.get("visual") or {}
        rows.append({
            "date": when, "brand": "radio" if p.get("account") == "lofi" else p.get("account"),
            "topic": vis.get("headline") or p.get("category"), "hook": text[0][:160] if text else "",
            "platform": p.get("platform"), "legacy_id": p.get("id"),
        })
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
