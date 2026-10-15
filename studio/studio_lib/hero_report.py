"""Truthful end-of-Heroes status: local readiness, gates, and live Postiz state."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone

import requests

from . import config, gates, heroes, publish, render
from .schedule import parse


def next_publish_check(d: date, now: datetime, cfg: dict) -> datetime:
    """Next scheduled 30-minute publisher run that will process this content date."""
    tz = config.tz(cfg)
    local = now.astimezone(tz)
    if d > local.date():
        return datetime.combine(d, time(6, 0), tzinfo=tz)
    if local.hour < 6:
        return datetime.combine(local.date(), time(6, 0), tzinfo=tz)
    if local.hour >= 23:
        return datetime.combine(local.date() + timedelta(days=1), time(6, 0), tzinfo=tz)
    minute = 30 if local.minute < 30 else 0
    hour = local.hour if minute == 30 else local.hour + 1
    if hour >= 23 and minute == 0:
        return datetime.combine(local.date(), time(23, 0), tzinfo=tz)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _postiz_ids(row: dict) -> list[str]:
    raw = row.get("postiz")
    items = raw if isinstance(raw, list) else [raw]
    return [str(item["postId"]) for item in items if isinstance(item, dict) and item.get("postId")]


def live_postiz_states(d: date, cfg: dict) -> tuple[dict[str, str] | None, str | None]:
    url = os.environ.get("POSTIZ_URL", "").rstrip("/")
    key = os.environ.get("POSTIZ_API_KEY", "")
    if not url or not key:
        return None, "Postiz credentials are not configured"
    tz = config.tz(cfg)
    start = datetime.combine(d, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = (datetime.combine(d, time.min, tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
    try:
        response = requests.get(
            f"{url}/api/public/v1/posts",
            headers={"Authorization": key},
            params={
                "startDate": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "endDate": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
            timeout=60,
        )
        response.raise_for_status()
        return {
            str(item["id"]): str(item.get("state") or "UNKNOWN")
            for item in response.json().get("posts", [])
            if item.get("id")
        }, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def build_report(plan: dict, d: date, cfg: dict, *, pack_total: int, matched: int, ingested: int,
                 skipped: int, now: datetime, live_states: dict[str, str] | None,
                 live_error: str | None = None) -> tuple[list[str], bool]:
    manifest = render.load_manifest(d)
    gate_results = gates.load(d)
    ready = {row["post"]: row["ready"] for row in heroes.status(plan, d)}
    rows = publish.last_rows()
    paused = config.is_paused(cfg)
    cutoff = timedelta(minutes=int(cfg["publish"].get("replace_min_lead_minutes", 10)))
    route_rows = []
    verified = waiting = blocked = 0

    for post in plan.get("posts", []):
        for platform, when_raw in (post.get("schedule") or {}).items():
            when = parse(when_raw).astimezone(config.tz(cfg))
            row = rows.get((post["id"], platform)) or {}
            ids = _postiz_ids(row)
            states = [live_states.get(pid) for pid in ids if live_states and pid in live_states]
            hero_ready = bool(ready.get(post["id"])) and bool((manifest.get(post["id"]) or {}).get("hero"))
            gate_pass = bool((gate_results.get(post["id"]) or {}).get("pass"))
            label = ""
            if paused:
                label = "PAUSED"
                blocked += 1
            elif not hero_ready:
                label = "MISSING HERO"
                blocked += 1
            elif not gate_pass:
                label = "BLOCKED BY GATES"
                blocked += 1
            elif row.get("status") == "scheduled" and row.get("hero") is True:
                if any(state in {"QUEUE", "PUBLISHED"} for state in states):
                    label = "POSTIZ VERIFIED UPDATED" if "QUEUE" in states else "POSTIZ HERO PUBLISHED"
                    verified += 1
                elif live_states is None:
                    label = "POSTIZ VERIFICATION UNAVAILABLE"
                    waiting += 1
                else:
                    label = "POSTIZ UPDATE NOT FOUND"
                    blocked += 1
            elif row.get("status") == "scheduled":
                if when <= now.astimezone(config.tz(cfg)):
                    label = "TOO LATE: TEMPLATE ROUTE ALREADY DUE"
                    blocked += 1
                elif when - now.astimezone(config.tz(cfg)) <= cutoff:
                    label = "TOO CLOSE TO REPLACE"
                    blocked += 1
                else:
                    label = "WAITING FOR PUBLISH"
                    waiting += 1
            elif row.get("status") in {"blocked", "missed", "failed", "failed-final"}:
                label = f"POSTIZ {str(row['status']).upper()}"
                blocked += 1
            elif when <= now.astimezone(config.tz(cfg)):
                label = "POST TIME PASSED; NOT VERIFIED"
                blocked += 1
            else:
                label = "WAITING FOR PUBLISH"
                waiting += 1
            route_rows.append((when, post["id"], platform, label))

    local_ready = sum(1 for value in ready.values() if value)
    lines = [
        "",
        f"=== HEROES / POSTIZ STATUS — {d} ===",
        (
            f"Images: {local_ready}/{pack_total} LOCAL MEDIA READY "
            f"(matched this run: {matched}; ingested this run: {ingested}; "
            f"missing: {max(0, pack_total - local_ready)}; skipped: {skipped})"
        ),
        (
            f"Render/gates: {sum(1 for p in plan.get('posts', []) if p['id'] in manifest)}/{len(plan.get('posts', []))} "
            f"rendered; {sum(1 for r in gate_results.values() if r.get('pass'))}/{len(plan.get('posts', []))} pass"
        ),
        "Routes:",
    ]
    lines.extend(
        f"  {when.strftime('%a %I:%M %p ET')}  {post_id} → {platform}: {label}"
        for when, post_id, platform, label in sorted(route_rows)
    )
    total_routes = len(route_rows)
    if verified == total_routes and total_routes:
        lines.append(f"Result: POSTIZ VERIFIED UPDATED — {verified}/{total_routes} routes use hero media.")
    elif paused:
        lines.append("Result: PAUSED — local media is kept, but Studio will not update Postiz until resumed.")
    elif blocked:
        lines.append(f"Result: ACTION REQUIRED — {blocked}/{total_routes} routes are blocked, too late, or unverified.")
    else:
        check = next_publish_check(d, now, cfg)
        lines.append(
            f"Result: LOCAL MEDIA READY; WAITING FOR PUBLISH — next eligible scheduled check is "
            f"{check.strftime('%a %b %d at %I:%M %p ET')}."
        )
        lines.append(
            "This is conditional on the laptop being on, LayerStudio-Publish succeeding, and the route "
            f"remaining more than {int(cutoff.total_seconds() // 60)} minutes from post time."
        )
    if live_error:
        lines.append(f"Postiz verification detail: {live_error}")
    lines.append("The exact network upload time cannot be promised; rerun Heroes to refresh this status.")
    lines.append("========================================")
    return lines, verified == total_routes and total_routes > 0


def report(plan: dict, d: date, cfg: dict, *, pack_total: int, matched: int, ingested: int,
           skipped: int, now: datetime | None = None, log=print) -> bool:
    now = now or datetime.now(config.tz(cfg))
    states, error = live_postiz_states(d, cfg)
    lines, verified = build_report(
        plan, d, cfg, pack_total=pack_total, matched=matched, ingested=ingested,
        skipped=skipped, now=now, live_states=states, live_error=error,
    )
    for line in lines:
        log(line)
    return verified
