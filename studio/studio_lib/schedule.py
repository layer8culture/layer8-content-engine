"""Assign posting times from the legacy schedule-windows pools (lean rewrite of schedule_planner.py)."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SLOTS = {"morning": (6 * 60, 11 * 60), "midday": (11 * 60, 14 * 60), "afternoon": (14 * 60, 17 * 60 + 30), "evening": (17 * 60 + 30, 23 * 60)}


def _m(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _ok(t: int, platform: str, taken: list[tuple[int, str]], lane: dict) -> bool:
    for other, plat in taken:
        gap = lane.get("same_platform_min_gap_minutes", 45) if plat == platform else lane.get("min_gap_minutes", 30)
        if abs(t - other) < gap:
            return False
    return True


def _pick(rng: random.Random, lane: dict, slot: str, platform: str, taken: list[tuple[int, str]]) -> int:
    windows = [(_m(a), _m(b)) for a, b in lane["windows"]]
    lo, hi = SLOTS.get(slot, (0, 24 * 60))
    preferred = [w for w in windows if lo <= w[0] < hi]
    others = [w for w in windows if w not in preferred]
    rng.shuffle(preferred)
    rng.shuffle(others)
    step = lane.get("jitter_step_minutes", 5)
    end = _m(lane.get("day_end", "22:30"))
    for a, b in preferred + others:
        candidates = list(range(a, b + 1, step))
        rng.shuffle(candidates)
        for t in candidates:
            if t <= end and _ok(t, platform, taken, lane):
                return t
    # Gaps are guarantees: push later until one fits.
    t = max([x for x, _ in taken] + [windows[0][0]])
    while not _ok(t, platform, taken, lane) and t < end:
        t += step
    return t


def assign_schedule(plan: dict, windows_cfg: dict, tzname: str) -> dict:
    d = date.fromisoformat(plan["date"])
    zone = ZoneInfo(tzname)
    taken: dict[str, list[tuple[int, str]]] = {"layer8culture": [], "radio": []}
    order = {"morning": 0, "midday": 1, "afternoon": 2, "evening": 3}
    posts = sorted(plan["posts"], key=lambda p: (order.get(p.get("slot"), 1), p["id"]))
    for p in posts:
        brand = p["brand"]
        lane = windows_cfg["layer8culture" if brand == "layer8culture" else "radio"]
        rng = random.Random(f"{plan['date']}:{p['id']}")
        sched = {}
        ig = _pick(rng, lane, p.get("slot", "midday"), "instagram", taken[brand])
        taken[brand].append((ig, "instagram"))
        if "instagram" in p["platforms"]:
            sched["instagram"] = ig
        if "youtube" in p["platforms"]:
            reuse = lane.get("reuse", {})
            if reuse.get("mode") == "offset":
                yt = ig + int(reuse.get("offset_minutes", 5))
            else:
                later = {"morning": "midday", "midday": "afternoon", "afternoon": "evening", "evening": "evening"}
                yt = _pick(rng, lane, later.get(p.get("slot", "midday"), "evening"), "youtube", taken[brand])
            taken[brand].append((yt, "youtube"))
            sched["youtube"] = yt
        p["schedule"] = {
            k: datetime(d.year, d.month, d.day, v // 60, v % 60, tzinfo=zone).isoformat() for k, v in sched.items()
        }
    return plan


def parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def due(iso: str, now: datetime, lookahead_min: int, grace_min: int) -> str:
    """'due' (push now), 'future', or 'missed'."""
    t = parse(iso)
    if t - now > timedelta(minutes=lookahead_min):
        return "future"
    if now - t > timedelta(minutes=grace_min):
        return "missed"
    return "due"
