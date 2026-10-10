"""Daily plan: gather context, ask Claude (via Copilot CLI) for the plan, validate, schedule."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date

from . import context, news, schema
from .config import INBOX, PLANS, ROOT, plan_path
from .schedule import assign_schedule

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
RADIO_ROTATION = ["quote", "playlist", "video-promo", "community", "quote", "brand-intro", "behind-the-scenes"]


def available_loop_clips() -> list[str]:
    used = set()
    for p in PLANS.glob("????-??-??.json"):
        try:
            for post in json.loads(p.read_text(encoding="utf-8")).get("posts", []):
                clip = (post.get("reel") or {}).get("loop_clip")
                if clip:
                    used.add(clip)
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(f.name for f in (INBOX / "video").glob("radio-*.mp4") if f.name not in used)


def build_slots(d: date, cfg: dict) -> list[dict]:
    compact = d.strftime("%Y%m%d")
    wd = WEEKDAYS[d.weekday()]
    cad = cfg["cadence"]
    tt = cad.get("tech_thursday", {})
    yt_on = cfg["platforms"].get("youtube", False)
    l8 = cad["layer8culture"]
    fmts = list(l8.get("formats") or ["carousel", "reel", "single"])
    n = int(l8.get("instagram_posts", 3))
    slots = []
    shorts_left = int(l8.get("youtube_shorts", 1)) if yt_on else 0
    for i in range(n):
        fmt = fmts[i % len(fmts)]
        s = {"brand": "layer8culture", "format": fmt, "platforms": ["instagram"], "note": "your choice of category; practical AI lesson or a fresh AI news angle"}
        if fmt == "reel" and shorts_left > 0:
            s["platforms"] = ["instagram", "youtube"]
            s["note"] = "the day's strongest hook; cross-posted as a YouTube Short (needs youtube_title)"
            shorts_left -= 1
        slots.append(s)
    if wd in tt.get("promo_days", []) and wd != tt.get("weekday", "thursday"):
        target = next((s for s in slots if s["format"] == "single"), slots[-1])
        target.update(note="category 'Tech Thursday Promos': promote tomorrow's Tech Thursday live show (do not invent the topic or guests; frame it around the show's purpose)")
    if wd == tt.get("weekday", "thursday"):
        slots.append({"brand": "layer8culture", "format": "story", "platforms": ["instagram"],
                      "note": "category 'Tech Thursday Promos': day-of story — 'Tech Thursday is live today'. Do not state a time unless FACTS give one"})
    if wd == tt.get("recap_day", "friday"):
        target = next((s for s in slots if s["format"] == "carousel"), slots[0])
        target.update(note="category 'Livestream Clips': recap carousel of the latest Tech Thursday transcript — 3-5 concrete lessons actually said on the show (paraphrase faithfully, no invented quotes)")
    radio = cad.get("radio", {})
    rtype = RADIO_ROTATION[d.toordinal() % len(RADIO_ROTATION)]
    for _ in range(int(radio.get("instagram_posts", 1))):
        fmt = "carousel" if rtype == "playlist" else "single"
        slots.append({"brand": "radio", "format": fmt, "platforms": ["instagram"], "note": f"category '{rtype}'"})
    clips = available_loop_clips()
    if clips and radio.get("youtube_short_when_loop_clip", True):
        plats = ["instagram", "youtube"] if yt_on else ["instagram"]
        slots.append({"brand": "radio", "format": "reel", "platforms": plats,
                      "note": f"category 'loop-reel' using reel.loop_clip = '{clips[0]}' (calm 2-beat labels like 'NIGHT CODING' / 'PRESS PLAY')"})
    counters = {"layer8culture": 0, "radio": 0}
    for s in slots:
        counters[s["brand"]] += 1
        s["id"] = f"{compact}-{'l8' if s['brand'] == 'layer8culture' else 'radio'}-{counters[s['brand']]:02d}"
    return slots


def build_prompt(d: date, cfg: dict, slots: list[dict], news_path: str, history_path: str, transcript_path: str | None, draft_path: str) -> str:
    wd = WEEKDAYS[d.weekday()]
    req = "\n".join(
        f"{i + 1}. id {s['id']} — brand {s['brand']}, format {s['format']}, platforms {s['platforms']} — {s['note']}"
        for i, s in enumerate(slots)
    )
    tline = f"- {transcript_path}  — latest Tech Thursday transcript (primary source for recaps and lessons)" if transcript_path else "- (no Tech Thursday transcript available)"
    facts = "\n".join(f"- {k}: {v}" for k, v in (cfg.get("facts") or {}).items() if v not in ("", None))
    tmpl = (ROOT / "templates" / "plan-prompt.md").read_text(encoding="utf-8")
    return tmpl.format(
        date=d.isoformat(), weekday=wd.title(), compact=d.strftime("%Y%m%d"), requirements=req,
        news_path=news_path, history_path=history_path, transcript_line=tline,
        banned="; ".join(cfg["gates"]["banned_phrases"]), facts=facts or "- (none)",
        radio_banned=", ".join(cfg["gates"].get("radio_banned_topics", [])),
        draft_path=draft_path, schema=schema.SCHEMA_DOC,
    )


def run_copilot(prompt_file: str, cfg: dict, log_name: str) -> int:
    exe = shutil.which(cfg["copilot"].get("command", "copilot")) or "copilot"
    args = [exe, "-p", f"Read the file {prompt_file} and follow its instructions exactly.",
            "--model", cfg["model"], "-s", "--no-ask-user",
            "--allow-tool", "write", "--allow-tool", "web_fetch", "--deny-tool", "shell"]
    for dom in cfg["copilot"].get("allow_urls", []):
        args += ["--allow-url", dom]
    log = ROOT / "data" / "logs" / log_name
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as out:
        try:
            proc = subprocess.run(args, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=int(cfg["copilot"].get("timeout_minutes", 25)) * 60)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            # Copilot frequently finishes the instructed write before spending too long
            # validating/explaining it. make_plan validates the draft independently, so
            # preserve that completed work rather than failing the whole scheduled run.
            out.write("\nStudio: Copilot timed out; validating any draft it already wrote.\n")
            returncode = 124
    if returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace")
        if re.search(r'Model ".*" from --model flag is not available', tail):
            raise RuntimeError(
                f"configured model {cfg['model']!r} is no longer available from the Copilot CLI "
                f"(see {log.relative_to(ROOT)}) — update `model:` in config.yaml to a current model name"
            )
    return returncode


def _load_draft(path) -> tuple[dict | None, list[str]]:
    if not path.exists():
        return None, ["draft file was not written"]
    raw = path.read_text(encoding="utf-8-sig").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    try:
        return json.loads(raw), []
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: {exc}"]


def make_plan(d: date, cfg: dict, *, force: bool = False, log=print) -> dict:
    out = plan_path(d)
    if out.exists() and not force:
        log(f"plan exists: {out.relative_to(ROOT)} (use --force to regenerate)")
        return json.loads(out.read_text(encoding="utf-8"))
    PLANS.mkdir(parents=True, exist_ok=True)
    log("fetching news …")
    news.fetch_news(cfg, d)
    news_rel = f"data/news/{d.isoformat()}.md"
    hist_rel = context.write_history_context(d)
    tr_rel = context.write_transcript_context()
    slots = build_slots(d, cfg)
    draft_rel = f"data/plans/{d.isoformat()}.draft.json"
    draft = ROOT / draft_rel
    if draft.exists():
        draft.unlink()
    prompt = build_prompt(d, cfg, slots, news_rel, hist_rel, tr_rel, draft_rel)
    prompt_rel = f"data/plans/{d.isoformat()}.prompt.md"
    (ROOT / prompt_rel).write_text(prompt, encoding="utf-8")
    log(f"asking {cfg['model']} via Copilot CLI for {len(slots)} posts …")
    rc = run_copilot(prompt_rel, cfg, f"plan-{d.isoformat()}.log")
    plan, errs = _load_draft(draft)
    if plan is not None:
        errs = schema.validate_plan(plan, d.isoformat()) + _slot_errors(plan, slots)
    attempt = 1
    while errs and attempt <= 2:
        log(f"plan invalid (rc={rc}); repair attempt {attempt}: {errs[:6]}")
        fix_rel = f"data/plans/{d.isoformat()}.fix{attempt}.md"
        (ROOT / fix_rel).write_text(
            f"The plan at {draft_rel} (written for the instructions in {prompt_rel}) failed validation:\n\n"
            + "\n".join(f"- {e}" for e in errs)
            + f"\n\nRead {prompt_rel} for the full rules and schema. Fix every error and overwrite {draft_rel} "
            "with the corrected JSON only. If the file is missing, write it from scratch. Reply DONE.",
            encoding="utf-8")
        rc = run_copilot(fix_rel, cfg, f"plan-{d.isoformat()}-fix{attempt}.log")
        plan, errs = _load_draft(draft)
        if plan is not None:
            errs = schema.validate_plan(plan, d.isoformat()) + _slot_errors(plan, slots)
        attempt += 1
    if errs:
        raise RuntimeError(f"planner failed validation: {errs}")
    plan["model"] = cfg["model"]
    plan["generated_at"] = context.now_iso()
    assign_schedule(plan, cfg["schedule_windows"], cfg.get("timezone", "America/New_York"))
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"plan written: {out.relative_to(ROOT)} ({len(plan['posts'])} posts)")
    return plan


def _slot_errors(plan: dict, slots: list[dict]) -> list[str]:
    errs = []
    by_id = {p.get("id"): p for p in plan.get("posts", []) if isinstance(p, dict)}
    for s in slots:
        p = by_id.get(s["id"])
        if not p:
            errs.append(f"missing post {s['id']}")
            continue
        for k in ("brand", "format"):
            if p.get(k) != s[k]:
                errs.append(f"{s['id']}: {k} must be {s[k]}")
        if sorted(p.get("platforms") or []) != sorted(s["platforms"]):
            errs.append(f"{s['id']}: platforms must be {s['platforms']}")
    extra = set(by_id) - {s["id"] for s in slots}
    if extra:
        errs.append(f"unexpected post ids: {sorted(extra)}")
    return errs
