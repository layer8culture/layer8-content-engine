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


def _limited_text(path: str | None, max_chars: int) -> str:
    if not path:
        return "(none available)"
    text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n\n[Studio truncated this source after {max_chars} characters.]"


def write_context_bundle(d: date, cfg: dict, news_path: str, history_path: str,
                         transcript_path: str | None) -> str:
    """Write the smallest source bundle needed for today's assigned slots."""
    context_dir = ROOT / "data" / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    rel = f"data/context/planning-{d.isoformat()}.md"
    brand_files = [
        ("brand/voice-layer8culture.md", 8_000),
        ("brand/voice-lofi.md", 6_000),
        ("brand/topics-layer8culture.md", 4_000),
        ("brand/topics-radio.md", 3_000),
    ]
    sections = ["# Layer8 Studio planning source bundle", "",
                "Read this file once, then write the draft. Do not reread sections.", ""]
    for path, limit in brand_files:
        sections += [f"## {path}", _limited_text(path, limit), ""]
    news_limit = int((cfg.get("copilot") or {}).get("news_context_chars", 8_000))
    transcript_limit = int((cfg.get("copilot") or {}).get(
        "transcript_context_chars_recap" if d.weekday() == 4 else "transcript_context_chars", 0))
    sections += [f"## {news_path}", _limited_text(news_path, news_limit), "",
                 f"## {history_path}", _limited_text(history_path, 6_000), ""]
    if transcript_limit > 0:
        sections += [
            f"## {transcript_path or 'latest transcript'}",
            _limited_text(transcript_path, transcript_limit),
            "",
        ]
    (ROOT / rel).write_text("\n".join(sections), encoding="utf-8")
    return rel


def build_prompt(d: date, cfg: dict, slots: list[dict], news_path: str, history_path: str,
                 transcript_path: str | None, draft_path: str, bundle_path: str | None = None) -> str:
    wd = WEEKDAYS[d.weekday()]
    req = "\n".join(
        f"{i + 1}. id {s['id']} — brand {s['brand']}, format {s['format']}, platforms {s['platforms']} — {s['note']}"
        for i, s in enumerate(slots)
    )
    tline = f"- {transcript_path}  — latest Tech Thursday transcript (primary source for recaps and lessons)" if transcript_path else "- (no Tech Thursday transcript available)"
    facts = "\n".join(f"- {k}: {v}" for k, v in (cfg.get("facts") or {}).items() if v not in ("", None))
    tmpl = (ROOT / "templates" / "plan-prompt.md").read_text(encoding="utf-8")
    prompt = tmpl.format(
        date=d.isoformat(), weekday=wd.title(), compact=d.strftime("%Y%m%d"), requirements=req,
        news_path=news_path, history_path=history_path, transcript_line=tline,
        banned="; ".join(cfg["gates"]["banned_phrases"]), facts=facts or "- (none)",
        radio_banned=", ".join(cfg["gates"].get("radio_banned_topics", [])),
        draft_path=draft_path, schema=schema.SCHEMA_DOC,
    )
    if bundle_path:
        prompt = re.sub(
            r"## READ FIRST.*?(?=## TODAY'S SLOTS)",
            "## READ FIRST\n"
            f"- {bundle_path} — bounded brand, news, history, and transcript context\n\n"
            "Read that single bundle once. Do not glob for other files, reread the bundle, or consult source code.\n\n",
            prompt,
            flags=re.S,
        )
    return prompt


def _self_contained_request(prompt_file: str, bundle_file: str) -> str:
    """Inline every input so Copilot only has to return JSON, never operate tools."""
    prompt = (ROOT / prompt_file).read_text(encoding="utf-8")
    bundle = (ROOT / bundle_file).read_text(encoding="utf-8")
    prompt = prompt.replace("and save it as JSON.", "and return it as JSON.")
    prompt = re.sub(
        r"## READ FIRST.*?(?=## TODAY'S SLOTS)",
        "## SOURCE MATERIAL\n"
        "The complete bounded source material follows. Use it as evidence; do not request or read files.\n\n"
        f"{bundle}\n\n",
        prompt,
        flags=re.S,
    )
    prompt = re.sub(
        r"## OUTPUT\s+Write ONLY valid JSON.*?using exactly this shape:",
        "## OUTPUT\nReturn ONLY valid JSON (UTF-8, no comments, no markdown fences) using exactly this shape:",
        prompt,
        flags=re.S,
    )
    prompt = re.sub(
        r"Do not write any other files\. When the file is written, reply with the single word DONE\.",
        "Do not use tools. Return the JSON object only.",
        prompt,
    )
    return prompt


def run_copilot(prompt_text: str, cfg: dict, log_name: str) -> tuple[int, str]:
    """Run one bounded, tool-free model call and return its machine-readable response."""
    exe = shutil.which(cfg["copilot"].get("command", "copilot")) or "copilot"
    args = [
        exe, "--model", cfg["model"], "-s", "--no-ask-user", "--no-custom-instructions",
        "--disable-builtin-mcps", "--max-autopilot-continues", "0", "--stream", "off",
        "--no-color", "--output-format", "json",
        "--reasoning-effort", cfg["copilot"].get("reasoning_effort", "low"),
        "--deny-tool", "read", "--deny-tool", "write", "--deny-tool", "shell",
        "--deny-tool", "web_fetch",
    ]
    log = ROOT / "data" / "logs" / log_name
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            args, cwd=ROOT, input=prompt_text, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=int(cfg["copilot"].get("response_timeout_minutes", 10)) * 60,
        )
        returncode, raw_output = proc.returncode, proc.stdout or ""
        response = _assistant_content(raw_output) or raw_output
    except subprocess.TimeoutExpired as exc:
        response = exc.stdout or ""
        if isinstance(response, bytes):
            response = response.decode("utf-8", errors="replace")
        response += "\nStudio: tool-free Copilot response timed out.\n"
        returncode = 124
    log.write_text(response, encoding="utf-8")
    if returncode != 0:
        if re.search(r'Model ".*" from --model flag is not available', response):
            raise RuntimeError(
                f"configured model {cfg['model']!r} is no longer available from the Copilot CLI "
                f"(see {log.relative_to(ROOT)}) — update `model:` in config.yaml to a current model name"
            )
    return returncode, response


def _assistant_content(raw_output: str) -> str:
    """Extract the safely escaped final assistant payload from Copilot's JSONL events."""
    messages = []
    for line in raw_output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant.message":
            content = (event.get("data") or {}).get("content")
            if isinstance(content, str):
                messages.append(content)
    return messages[-1] if messages else ""


def _decode_response(raw: str) -> tuple[dict | None, list[str]]:
    raw = _strip_terminal_sequences(raw).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    try:
        return json.loads(raw), []
    except json.JSONDecodeError as exc:
        repaired = _escape_string_control_chars(raw)
        try:
            return json.loads(repaired), []
        except json.JSONDecodeError:
            return None, [f"invalid JSON: {exc}"]


def _escape_string_control_chars(raw: str) -> str:
    """Repair model line-wrapping inside JSON strings without changing JSON structure."""
    out = []
    quoted = escaped = False
    for char in raw:
        if quoted and char in "\r\n\t":
            out.append({"\r": "\\r", "\n": "\\n", "\t": "\\t"}[char])
            escaped = False
            continue
        out.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
    return "".join(out)


def _strip_terminal_sequences(raw: str) -> str:
    raw = re.sub(r"\x1b\].*?(?:\x07|\x1b\\)", "", raw, flags=re.S)
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)


def _load_draft(path) -> tuple[dict | None, list[str]]:
    if not path.exists():
        return None, ["draft file was not written"]
    return _decode_response(path.read_text(encoding="utf-8-sig"))


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
    bundle_rel = write_context_bundle(d, cfg, news_rel, hist_rel, tr_rel)
    prompt = build_prompt(d, cfg, slots, news_rel, hist_rel, tr_rel, draft_rel, bundle_rel)
    prompt_rel = f"data/plans/{d.isoformat()}.prompt.md"
    (ROOT / prompt_rel).write_text(prompt, encoding="utf-8")
    log(f"asking {cfg['model']} via Copilot CLI for {len(slots)} posts …")
    request = _self_contained_request(prompt_rel, bundle_rel)
    rc, response = run_copilot(request, cfg, f"plan-{d.isoformat()}.log")
    plan, errs = _decode_response(response)
    if plan is not None:
        draft.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    if plan is not None:
        errs = schema.validate_plan(plan, d.isoformat()) + _slot_errors(plan, slots)
    if rc == 124:
        raise RuntimeError(
            "tool-free planner timed out before returning valid JSON "
            f"(see data/logs/plan-{d.isoformat()}.log)"
        )
    attempt = 1
    while errs and attempt <= 1:
        log(f"plan invalid (rc={rc}); repair attempt {attempt}: {errs[:6]}")
        repair = (
            request
            + "\n\n## VALIDATION REPAIR\nYour previous JSON failed these deterministic checks:\n"
            + "\n".join(f"- {e}" for e in errs)
            + "\n\nPrevious response:\n"
            + response
            + "\n\nReturn one corrected JSON object only. Do not use tools."
        )
        rc, response = run_copilot(repair, cfg, f"plan-{d.isoformat()}-fix{attempt}.log")
        plan, errs = _decode_response(response)
        if plan is not None:
            draft.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
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
