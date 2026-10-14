"""Layer8 Studio — plan, render, gate and publish Layer8Culture + Layer8Culture Radio content from this laptop.

  python studio.py plan       [--date YYYY-MM-DD] [--force]
  python studio.py render     [--date ...] [--only ID ...]
  python studio.py heroes     [--date ...]        # manual ChatGPT desk: copies prompts, files your downloads
  python studio.py ingest     [--date ...]        # pick up ChatGPT heroes from inbox/, re-render those posts
  python studio.py publish    [--now ISO]         # just-in-time push of due posts to Postiz (every 30 min)
  python studio.py run-daily  [--date ...]        # nightly: plan tomorrow, render, prompt pack, gates, preview
  python studio.py status     [--date ...]
  python studio.py dry-run    [--date ...] [--reuse-plan]   # everything except posting
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from studio_lib import config, gates, heroes, notify, planner, preview, render

# Windows consoles / Task Scheduler redirects default to cp1252; never crash on a "→".
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
from studio_lib.config import DATA, POSTED_LOG, ROOT, plan_path
from studio_lib.context import read_jsonl
from studio_lib.schedule import due, parse

LOCK = DATA / ".lock"
RUNS = DATA / "runs"
DATE_LOCKS = DATA / "locks"


def log(msg: str) -> None:
    print(msg, flush=True)


def acquire_lock() -> bool:
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 90 * 60:
        return False
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    return True


def release_lock() -> None:
    LOCK.unlink(missing_ok=True)


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def acquire_date_lock(d) -> Path | None:
    """Atomically prevent two planners for one date without blocking publishing."""
    DATE_LOCKS.mkdir(parents=True, exist_ok=True)
    path = DATE_LOCKS / f"plan-{d}.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            pid = -1
        if _process_exists(pid):
            return None
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(str(os.getpid()))
    return path


def _run_state(d, stage: str, status: str, **details) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{d}.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"date": str(d), "stages": {}}
    except (OSError, json.JSONDecodeError):
        state = {"date": str(d), "stages": {}}
    state["updated_at"] = datetime.now().astimezone().isoformat()
    state["stages"][stage] = {"status": status, "at": state["updated_at"], **details}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_plan(d):
    p = plan_path(d)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def produce(d, cfg, *, force_plan=False, reuse=False):
    """Plan (unless present) → ingest heroes → render → prompt pack → gates → preview."""
    plan = load_plan(d) if (reuse or not force_plan) else None
    if plan is None:
        _run_state(d, "plan", "running")
        plan = planner.make_plan(d, cfg, force=force_plan, log=log)
    _run_state(d, "plan", "complete", posts=len(plan["posts"]))
    heroes.ingest(plan, d, log=log)
    _run_state(d, "render", "running")
    log("rendering …")
    manifest = render.render_plan(plan, d, cfg, log=log)
    _run_state(d, "render", "complete", media_posts=len(manifest))
    pack = heroes.write_prompt_pack(plan, d)
    if pack:
        log(f"ChatGPT hero batch: {pack.relative_to(ROOT)}")
    results = gates.run_gates(plan, d, cfg, manifest)
    gates.save(results, d)
    for pid, r in results.items():
        log(f"  gates {'PASS' if r['pass'] else 'FAIL'} {pid}" + (f": {'; '.join(r['errors'])}" if r["errors"] else ""))
    _run_state(d, "gates", "complete" if all(r["pass"] for r in results.values()) else "failed",
               passed=sum(r["pass"] for r in results.values()), total=len(results))
    page = preview.build(plan, d, manifest, results, heroes.status(plan, d), cfg)
    _run_state(d, "preview", "complete", path=str(page))
    log(f"preview: {page}")
    return plan, manifest, results, page


def cmd_plan(a, cfg):
    planner.make_plan(config.parse_date(a.date, cfg), cfg, force=a.force, log=log)


def cmd_render(a, cfg):
    d = config.parse_date(a.date, cfg)
    plan = load_plan(d) or sys.exit(f"no plan for {d}; run plan first")
    manifest = render.render_plan(plan, d, cfg, only=set(a.only or []) or None, log=log)
    heroes.write_prompt_pack(plan, d)
    res = gates.run_gates(plan, d, cfg, manifest)
    gates.save(res, d)
    log(f"preview: {preview.build(plan, d, manifest, res, heroes.status(plan, d), cfg)}")


def cmd_ingest(a, cfg):
    d = config.parse_date(a.date, cfg, default_offset=0)
    total = 0
    for dd in sorted({d, config.parse_date(None, cfg)}):  # today and tomorrow
        plan = load_plan(dd)
        if not plan:
            continue
        changed = heroes.ingest(plan, dd, log=log)
        if changed:
            manifest = render.render_plan(plan, dd, cfg, only=set(changed), log=log)
            res = gates.run_gates(plan, dd, cfg, manifest)
            gates.save(res, dd)
            preview.build(plan, dd, manifest, res, heroes.status(plan, dd), cfg)
            total += len(changed)
    log(f"ingested {total} hero image(s)")


def cmd_heroes(a, cfg):
    from studio_lib import herodesk

    today = datetime.now(config.tz(cfg)).date()
    d = config.parse_date(a.date, cfg) if a.date else herodesk.default_date(today)
    if d is None:
        log("No prompt pack with missing heroes. (The 5 PM run writes inbox/PROMPTS-<tomorrow>.md.)")
        return
    if a.downloads:
        os.environ["STUDIO_DOWNLOADS_DIR"] = a.downloads
    pack = herodesk.load_pack(d)
    desk = herodesk.run(pack, cfg, log=log, open_browser=not a.no_browser)
    if desk.saved:
        log("filing heroes into the day's posts …")
        a.date = d.isoformat()
        cmd_ingest(a, cfg)
    if desk.skipped:
        log("skipped (template version will post): " + ", ".join(s.filename for s in desk.skipped))


def cmd_publish(a, cfg):
    from studio_lib import publish

    if config.is_paused(cfg):
        log("PAUSED (studio/PAUSE exists or config paused: true) — nothing will be posted.")
        return
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(config.tz(cfg))
    if now.tzinfo is None:
        now = now.replace(tzinfo=config.tz(cfg))
    d = config.parse_date(a.date, cfg) if a.date else now.astimezone(config.tz(cfg)).date()
    if not acquire_lock():
        log("another publish run is in progress; skipping")
        return
    try:
        plan = load_plan(d)
        cutoff = int(cfg["publish"].get("same_day_plan_cutoff_hour", 14))
        if plan is None and now.astimezone(config.tz(cfg)).hour >= cutoff:
            log(f"no plan for today ({d}) and it's past {cutoff}:00 — not planning same-day; run-daily covers tomorrow")
        else:
            _publish_day(a, cfg, d, plan, now)
    finally:
        release_lock()
    miss = None if (a.dry or a.date or a.only) else missed_daily(now, cfg)
    if miss:
        log(f"no plan for tomorrow ({miss}) yet — the 17:00 run-daily was missed; producing it now")
        _run_daily_date(miss, cfg)


def _publish_day(a, cfg, d, plan, now):
    from studio_lib import publish

    if plan is None:
        log(f"no plan for today ({d}); generating one now")
        plan, manifest, results, _ = produce(d, cfg)
    else:
        changed = heroes.ingest(plan, d, log=log)
        manifest = render.load_manifest(d)
        missing = [p["id"] for p in plan["posts"] if p["id"] not in manifest]
        if changed or missing:
            manifest = render.render_plan(plan, d, cfg, only=set(changed + missing), log=log)
        results = gates.run_gates(plan, d, cfg, manifest)
        gates.save(results, d)
        if changed:
            preview.build(plan, d, manifest, results, heroes.status(plan, d), cfg)
    actions = publish.publish_due(plan, d, cfg, manifest, results, now=now, dry=a.dry, log=log,
                                  only=set(a.only) if a.only else None, ahead=a.ahead,
                                  force_replace=a.replace)
    if actions and cfg["notify"].get("on_publish") and not a.dry:
        notify.send("Layer8 Studio publish:\n" + "\n".join(
            f"{r['status']} {r['id']} → {r['platform']}" + (f" ({r.get('reason', '')[:120]})" if r.get("reason") else "")
            for r in actions), log=log)
    if not actions:
        log("nothing due")


def missed_daily(now, cfg):
    """Tomorrow's date if run-daily should have produced its plan by now but hasn't, else None."""
    hour = (cfg.get("publish") or {}).get("catch_up_daily_after_hour")
    if hour is None:
        return None
    local = now.astimezone(config.tz(cfg))
    if local.hour < int(hour):
        return None
    tomorrow = local.date() + timedelta(days=1)
    return None if plan_path(tomorrow).exists() else tomorrow


def cmd_run_daily(a, cfg):
    d = config.parse_date(a.date, cfg)
    _run_daily_date(d, cfg, force=a.force)


def _queue_ready_day(plan, d, manifest, results, cfg):
    if config.is_paused(cfg):
        return []
    failed = [pid for pid, result in results.items() if not result["pass"]]
    if failed:
        raise RuntimeError(f"not queuing {d}; quality gates failed for {', '.join(failed)}")
    if not acquire_lock():
        raise RuntimeError("content is ready but the Postiz queue lock is busy; the next recovery run will retry")
    try:
        from studio_lib import publish

        actions = publish.publish_due(
            plan, d, cfg, manifest, results, now=datetime.now(config.tz(cfg)),
            ahead=True, log=log,
        )
    finally:
        release_lock()
    bad = [r for r in actions if r["status"] != "scheduled"]
    if bad:
        raise RuntimeError("Postiz queue incomplete: " + "; ".join(
            f"{r['id']}/{r['platform']}={r['status']} {r.get('reason', '')}".strip() for r in bad
        ))
    _run_state(d, "queue", "complete", routes=sum(len(p.get("schedule") or {}) for p in plan["posts"]))
    return actions


def _run_daily_date(d, cfg, *, force=False):
    date_lock = acquire_date_lock(d)
    if date_lock is None:
        log(f"a planner for {d} is already running; skipping duplicate")
        return
    try:
        try:
            plan, manifest, results, page = produce(d, cfg, force_plan=force)
            actions = _queue_ready_day(plan, d, manifest, results, cfg)
            _daily_ready(plan, d, results, page, cfg, actions)
            _run_state(d, "daily", "complete")
        except Exception as exc:
            _run_state(d, "daily", "failed", error=f"{type(exc).__name__}: {exc}")
            notify.send_daily_failure(d, exc, log=log)
            raise
    finally:
        date_lock.unlink(missing_ok=True)


def _daily_ready(plan, d, results, page, cfg, queue_actions=None):
    ok = sum(r["pass"] for r in results.values())
    msg = (f"Layer8 Studio planned {d} — {len(plan['posts'])} posts, {ok} pass gates"
           f"{' (PAUSED: nothing will post)' if config.is_paused(cfg) else ''}.\n{plan.get('summary', '')}\n"
           f"Hero batch: studio/inbox/PROMPTS-{d}.md\nPreview: {page}")
    blocked = [f"- {pid}: {'; '.join(r['errors'])[:200]}" for pid, r in results.items() if not r["pass"]]
    if blocked:
        msg += "\nBlocked:\n" + "\n".join(blocked)
    if cfg["notify"].get("on_run_daily"):
        notify.send(msg, log=log)
    if not notify.send_daily_ready(
        plan, d, results, page, heroes.status(plan, d), queue_actions=queue_actions, log=log
    ):
        raise RuntimeError("content was prepared, but the completion email failed; retry is safe")
    if (cfg.get("heroes") or {}).get("toast", True) and heroes.shots_for(plan):
        from studio_lib import herodesk

        herodesk.toast("Hero prompts ready — run heroes",
                       f"{len(heroes.shots_for(plan))} ChatGPT shots for {d}. Double-click 'Layer8 Heroes' on your desktop.")


def cmd_status(a, cfg):
    d = config.parse_date(a.date, cfg, default_offset=0)
    log(f"Layer8 Studio — {'PAUSED' if config.is_paused(cfg) else 'active'} — model {cfg['model']}")
    posted = {(r["id"], r["platform"]): r for r in read_jsonl(POSTED_LOG)}
    now = datetime.now(config.tz(cfg))
    for dd in sorted({d, config.parse_date(None, cfg)}):
        plan = load_plan(dd)
        if not plan:
            log(f"\n{dd}: no plan")
            continue
        res = gates.load(dd)
        hs = {r["post"]: r for r in heroes.status(plan, dd)}
        log(f"\n{dd}: {len(plan['posts'])} posts")
        for p in plan["posts"]:
            for plat, when in (p.get("schedule") or {}).items():
                st = (posted.get((p["id"], plat)) or {}).get("status") or due(when, now, cfg["publish"]["lookahead_minutes"], cfg["publish"]["catch_up_grace_minutes"])
                g = res.get(p["id"], {})
                gate = "pass" if g.get("pass") else ("FAIL" if g else "?")
                hero = ("hero✓" if hs[p["id"]]["ready"] else "hero…") if p["id"] in hs else ""
                log(f"  {parse(when).astimezone(config.tz(cfg)).strftime('%a %I:%M %p')}  {p['id']:<18} {p['format']:<8} {plat:<9} gates:{gate:<4} {st:<9} {hero}")


def cmd_dry_run(a, cfg):
    from studio_lib import publish

    d = config.parse_date(a.date, cfg)
    log(f"DRY RUN for {d} — nothing will be sent to Postiz")
    plan, manifest, results, page = produce(d, cfg, force_plan=not a.reuse_plan, reuse=a.reuse_plan)
    log("\nwould publish:")
    for p in plan["posts"]:
        for plat, when in (p.get("schedule") or {}).items():
            if not cfg["platforms"].get(plat):
                verdict = "skip (platform off)"
            elif not results[p["id"]]["pass"]:
                verdict = "BLOCKED by gates"
            elif not publish.channel_id(cfg, p["brand"], plat):
                verdict = "skip (no Postiz channel id)"
            else:
                verdict = "schedule"
            log(f"  {parse(when).astimezone(config.tz(cfg)).strftime('%a %I:%M %p')}  {p['id']:<18} {plat:<9} {verdict}")
    log(f"\npreview: {page}")


def cmd_email_test(a, cfg):
    recipient = os.environ.get("NOTIFY_EMAIL_TO", "").strip()
    if not recipient:
        sys.exit("email test failed: NOTIFY_EMAIL_TO is not configured")
    ok = notify.send_email(
        "Layer8 Studio email test",
        "Layer8 Studio's daily email is configured correctly.\n\n"
        "Future 5 PM preparation emails will include planned times, gate status, missing heroes, "
        "and the one-paste prompt pack.",
        log=log,
    )
    if not ok:
        sys.exit("email test failed; see the message above")
    log(f"test email accepted for {recipient}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="studio.py", description="Layer8 Studio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "render", "ingest", "run-daily", "status", "dry-run"):
        sp = sub.add_parser(name)
        sp.add_argument("--date", help="YYYY-MM-DD (default: tomorrow for plan/run-daily/dry-run, today for status/ingest)")
        if name in ("plan", "run-daily"):
            sp.add_argument("--force", action="store_true", help="regenerate even if a plan exists")
        if name == "render":
            sp.add_argument("--only", nargs="*", help="post ids")
        if name == "dry-run":
            sp.add_argument("--reuse-plan", action="store_true", help="reuse the existing plan instead of asking the model again")
    sp = sub.add_parser("publish")
    sp.add_argument("--now", help="override current time (ISO) for testing")
    sp.add_argument("--dry", action="store_true", help="evaluate due posts but do not call Postiz")
    sp.add_argument("--date", help="plan date to publish from (default: today)")
    sp.add_argument("--only", nargs="*", help="post ids")
    sp.add_argument("--ahead", action="store_true", help="schedule the whole day in Postiz now, at planned times")
    sp.add_argument("--replace", action="store_true",
                    help="delete + re-schedule already-scheduled posts (same time, current media); pair with --only")
    sp = sub.add_parser("heroes", help="manual ChatGPT hero desk: clipboard + Downloads watcher")
    sp.add_argument("--date", help="YYYY-MM-DD (default: next prompt pack with missing heroes)")
    sp.add_argument("--downloads", help="folder to watch instead of your Downloads folder")
    sp.add_argument("--no-browser", action="store_true", help="don't open chatgpt.com")
    sub.add_parser("email-test", help="send one recipient-only SMTP configuration test")
    a = ap.parse_args(argv)
    config.load_env()
    config.ensure_dirs()
    cfg = config.load_config()
    {"plan": cmd_plan, "render": cmd_render, "ingest": cmd_ingest, "publish": cmd_publish, "run-daily": cmd_run_daily,
     "status": cmd_status, "dry-run": cmd_dry_run, "heroes": cmd_heroes, "email-test": cmd_email_test}[a.cmd](a, cfg)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
