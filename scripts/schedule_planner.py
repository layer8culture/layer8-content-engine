#!/usr/bin/env python3
"""Plan rotating, jittered schedules with hard date/window/gap constraints.

Ported from the local scheduler: seeded window selection, optional analytics
weighting, recent-history anti-repeat and summary updates. Accepted inputs live
in the first post's schedule_plan field; reruns validate without moving times.
Only --reschedule may replace that plan or deliberately change its target date.
Stdlib only, including an Eastern DST fallback for Windows without tzdata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import uuid
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "schedule-windows.json"
DEFAULT_DIGEST = ROOT / "analytics" / "insights-digest.md"
QUEUE_DIR = ROOT / "queue"
POSTED_DIR = ROOT / "posted"
EASTERN = "America/New_York"
LANE_PREFIXES = {"lofi-": "lofi", "deallab-": "deallab"}
DEFAULT_LANE = "layer8culture"
DATE_IN_NAME = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
SUMMARY_ROW = re.compile(r"^(\|\s*(\d+)\s*\|\s*)(\d{1,2}:\d{2})(\s*\|)")
BEST_HOURS_LINE = re.compile(r"\*\*Best posting hours:\*\*\s*(.+)")
HOUR_TOKEN = re.compile(r"\b(\d{1,2}):(\d{2})\b")
HISTORY_DAYS = 7
REPEAT_TOLERANCE_MINUTES = 10
REPEAT_THRESHOLD = 0.4
MAX_ATTEMPTS = 24
ANALYTICS_BOOST = 3.0


class ScheduleError(ValueError):
    """A queue cannot be scheduled or fails validation."""


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date_cls:
    day = date_cls(year, month, 1)
    return day + timedelta(days=(weekday - day.weekday()) % 7 + 7 * (nth - 1))


def _us_eastern_offset(day: date_cls) -> timedelta:
    """Eastern offset at noon under the US rules in effect since 2007."""
    if day.year < 2007:
        raise ScheduleError("Eastern fallback supports dates from 2007 onward")
    start = _nth_weekday(day.year, 3, 6, 2)
    end = _nth_weekday(day.year, 11, 6, 1)
    return timedelta(hours=-4 if start <= day < end else -5)


def local_datetime(moment: datetime, tz_name: str = EASTERN) -> datetime:
    """Convert an aware instant, including the actual DST transition hour."""
    if moment.utcoffset() is None:
        raise ScheduleError("now must be timezone-aware")
    try:
        return moment.astimezone(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError as exc:
        if tz_name not in (EASTERN, "US/Eastern"):
            raise ScheduleError(f"cannot resolve timezone {tz_name!r}") from exc
    utc = moment.astimezone(timezone.utc)
    _us_eastern_offset(utc.date())
    start = datetime.combine(_nth_weekday(utc.year, 3, 6, 2), datetime.min.time(),
                             timezone.utc) + timedelta(hours=7)
    end = datetime.combine(_nth_weekday(utc.year, 11, 6, 1), datetime.min.time(),
                           timezone.utc) + timedelta(hours=6)
    return utc.astimezone(timezone(timedelta(hours=-4 if start <= utc < end else -5)))


def utc_offset(day: date_cls, tz_name: str) -> timedelta:
    return local_datetime(datetime(day.year, day.month, day.day, 17,
                                   tzinfo=timezone.utc), tz_name).utcoffset()


def iso_time(day: date_cls, minute_of_day: int, tz_name: str) -> str:
    if not 0 <= minute_of_day < 24 * 60:
        raise ScheduleError(f"minute {minute_of_day} is outside target date {day}")
    naive = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute_of_day)
    # Round-trip offsets rather than assuming noon's offset is valid at 01:30.
    try:
        zone = ZoneInfo(tz_name)
        offsets = {naive.replace(tzinfo=zone, fold=fold).utcoffset() for fold in (0, 1)}
    except ZoneInfoNotFoundError:
        if tz_name not in (EASTERN, "US/Eastern"):
            raise ScheduleError(f"cannot resolve timezone {tz_name!r}") from None
        offsets = {timedelta(hours=-4), timedelta(hours=-5)}
    candidates = []
    for offset in offsets:
        candidate = naive.replace(tzinfo=timezone(offset))
        if local_datetime(candidate, tz_name).replace(tzinfo=None) == naive:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise ScheduleError(f"{naive.isoformat()} is ambiguous or nonexistent in {tz_name}")
    return candidates[0].isoformat(timespec="seconds")


def format_offset(offset: timedelta | None) -> str:
    if offset is None:
        return "none"
    total = int(offset.total_seconds()) // 60
    return f"{'-' if total < 0 else '+'}{abs(total) // 60:02d}:{abs(total) % 60:02d}"


def parse_clock(value: str) -> int:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value))
    if not match:
        raise ScheduleError(f"invalid clock time {value!r}, expected HH:MM")
    hour, minute = map(int, match.groups())
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"clock time out of range: {value!r}")
    return hour * 60 + minute


def format_clock(minute_of_day: int) -> str:
    if not 0 <= minute_of_day < 1440:
        raise ScheduleError(f"minute {minute_of_day} is outside the day")
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScheduleError(f"cannot load schedule config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScheduleError(f"{path}: schedule config must be an object")
    return payload


def lane_config(config: dict, lane: str) -> dict:
    raw = config.get(lane)
    if not isinstance(raw, dict):
        raise ScheduleError(f"no schedule config for lane {lane!r}")
    windows = []
    for entry in raw.get("windows") or []:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ScheduleError(f"lane {lane!r}: each window must be a [start, end] pair")
        start, end = map(parse_clock, entry)
        if end < start:
            raise ScheduleError(f"lane {lane!r}: window {entry} ends before it starts")
        windows.append((start, end))
    if not windows:
        raise ScheduleError(f"lane {lane!r}: no posting windows configured")
    windows.sort()
    reuse = raw.get("reuse", {})
    if not isinstance(reuse, dict):
        raise ScheduleError(f"lane {lane!r}: reuse must be an object")
    mode = reuse.get("mode", "independent")
    if mode not in ("independent", "offset"):
        raise ScheduleError(f"lane {lane!r}: invalid reuse mode {mode!r}")
    pairs = reuse.get("allowed_platform_pairs", [])
    if not isinstance(pairs, list) or any(
        not isinstance(pair, list) or len(pair) != 2
        or not all(isinstance(p, str) and p for p in pair) or pair[0] == pair[1]
        for pair in pairs
    ):
        raise ScheduleError("reuse allowed_platform_pairs must name distinct platforms")
    if mode == "offset" and not pairs:
        raise ScheduleError("offset mode requires explicit allowed_platform_pairs")
    try:
        cfg = {
            "lane": lane,
            "timezone": raw.get("timezone", EASTERN),
            "min_gap": int(raw.get("min_gap_minutes", 30)),
            "platform_gap": int(raw.get("same_platform_min_gap_minutes", 45)),
            "step": int(raw.get("jitter_step_minutes", 5)),
            "day_end": parse_clock(raw.get("day_end", "22:30")),
            "reuse_mode": mode,
            "reuse_offset": int(reuse.get("offset_minutes", 5)),
            "reuse_pairs": pairs,
            "windows": windows,
        }
    except (ValueError, TypeError) as exc:
        raise ScheduleError(f"lane {lane!r}: invalid numeric schedule setting: {exc}") from exc
    if min(cfg["min_gap"], cfg["platform_gap"], cfg["step"], cfg["reuse_offset"]) <= 0:
        raise ScheduleError("gap, step and reuse offset values must be positive")
    if any(end > cfg["day_end"] for _, end in windows):
        raise ScheduleError("posting window extends beyond day_end")
    if cfg["timezone"] != EASTERN:
        raise ScheduleError(f"lane {lane!r}: target timezone must be {EASTERN}")
    return cfg


def load_queue(path: Path) -> list[dict]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScheduleError(f"cannot load queue {path}: {exc}") from exc
    if not isinstance(payload, list) or not payload or any(
        not isinstance(post, dict) for post in payload
    ):
        raise ScheduleError(f"{path}: queue JSON must be a non-empty list of post objects")
    return payload


def infer_lane(posts: list[dict], path: Path) -> str:
    accounts = {str(post.get("account") or "").strip().lower()
                for post in posts if post.get("account")}
    if len(accounts) > 1:
        raise ScheduleError(f"{path}: mixed account lanes cannot share a schedule")
    filename_lane = next((lane for prefix, lane in LANE_PREFIXES.items()
                          if Path(path).name.startswith(prefix)), DEFAULT_LANE)
    lane = next(iter(accounts), filename_lane)
    if date_from_name(path) is not None and lane != filename_lane:
        raise ScheduleError(f"{path}: account lane {lane!r} disagrees with filename lane")
    return lane


def parse_schedule_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def date_from_name(path: Path) -> date_cls | None:
    match = DATE_IN_NAME.search(Path(path).stem)
    if match is None:
        return None
    try:
        return date_cls.fromisoformat(match[1])
    except ValueError as exc:
        raise ScheduleError(f"{path}: invalid filename date {match[1]}") from exc


def accepted_plan(posts: list[dict], path: Path, *, check_membership=True) -> dict | None:
    records = [(i, p["schedule_plan"]) for i, p in enumerate(posts) if "schedule_plan" in p]
    if not records:
        return None
    if len(records) != 1 or records[0][0] != 0:
        raise ScheduleError(f"{path}: schedule_plan belongs on the first post only")
    plan = records[0][1]
    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise ScheduleError(f"{path}: invalid accepted schedule_plan")
    try:
        target = date_cls.fromisoformat(plan["target_date"])
        source = date_cls.fromisoformat(plan["source_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ScheduleError(f"{path}: invalid accepted target/source date") from exc
    if plan.get("timezone") != EASTERN or not isinstance(plan.get("inputs"), dict):
        raise ScheduleError(f"{path}: accepted plan is missing timezone/inputs")
    if source != date_from_name(path):
        raise ScheduleError(f"{path}: accepted plan source date disagrees with filename")
    if target != source:
        raise ScheduleError(f"{path}: accepted target date disagrees with filename")
    if check_membership and plan.get("post_ids") != [p.get("id") for p in posts]:
        raise ScheduleError(f"{path}: batch membership changed; explicitly reschedule")
    return plan


def infer_target_date(posts: list[dict], path: Path) -> date_cls:
    """The filename and accepted plan must identify the same target date."""
    plan = accepted_plan(posts, path)
    target = date_cls.fromisoformat(plan["target_date"]) if plan else date_from_name(path)
    if target is None:
        raise ScheduleError(f"{path}: target date requires a YYYY-MM-DD queue filename")
    return target


def platform_of(post: dict) -> str:
    return str(post.get("platform") or "unknown").strip().lower()


def reuse_master_id(post: dict) -> str | None:
    visual = post.get("visual")
    if not isinstance(visual, dict) or visual.get("source") != "reuse":
        return None
    return str(visual.get("of") or "").strip() or None


def parse_best_hours(text: str) -> list[int]:
    match = BEST_HOURS_LINE.search(text or "")
    if not match:
        return []
    hours = []
    for hour, minute in HOUR_TOKEN.findall(match[1]):
        if int(hour) < 24 and int(minute) < 60:
            value = int(hour) * 60 + int(minute)
            if value not in hours:
                hours.append(value)
    return hours


def read_best_hours(digest_path: Path | None) -> list[int]:
    if digest_path is None or not Path(digest_path).is_file():
        return []
    return parse_best_hours(Path(digest_path).read_text(encoding="utf-8"))


def window_weights(windows: list[tuple[int, int]], best_hours: list[int]) -> list[float]:
    weights = [1.0] * len(windows)
    for rank, minute in enumerate(best_hours):
        for index, (start, end) in enumerate(windows):
            if start - 30 <= minute <= end + 30:
                weights[index] *= 1.0 + ANALYTICS_BOOST / (rank + 1)
    return weights


def select_windows(windows, count, weights, rng, spacing):
    if count <= 0:
        return []
    order = sorted(range(len(windows)),
                   key=lambda i: rng.random() ** (1.0 / max(weights[i], 1e-9)), reverse=True)
    if count < len(windows):
        for relax in (1.0, 0.75, 0.5, 0.25, 0.0):
            chosen = []
            for index in order:
                if all(abs(windows[index][0] - windows[other][0]) >= spacing * relax
                       for other in chosen):
                    chosen.append(index)
                    if len(chosen) == count:
                        return sorted(windows[i] for i in chosen)
    result = sorted(windows[i] for i in order[:count])
    while len(result) < count:
        result.append(windows[-1])
    return result


def _jitter(window: tuple[int, int], step: int, rng: random.Random) -> int:
    start, end = window
    raw = start + rng.randrange(end - start + 1)
    return min(start + round((raw - start) / step) * step, end)


def pinned_masters(posts: list[dict], cfg: dict) -> dict[int, int]:
    ids = [str(post.get("id") or "") for post in posts]
    if any(not p for p in ids) or len(set(ids)) != len(ids):
        raise ScheduleError("schedule requires unique non-empty post IDs")
    pins = {}
    if cfg["reuse_mode"] != "offset":
        return pins
    for index, post in enumerate(posts):
        master = reuse_master_id(post)
        if master is None:
            continue
        if master not in ids[:index]:
            raise ScheduleError(f"{ids[index]}: reuse master must precede its cross-post")
        master_index = ids.index(master)
        pair = [platform_of(posts[master_index]), platform_of(post)]
        if pair not in cfg.get("reuse_pairs", []) or pair[0] == pair[1]:
            raise ScheduleError(f"{ids[index]}: no explicit cross-platform offset exception for {pair}")
        pins[index] = master_index
    return pins


def constraint_problems(posts: list[dict], times: list[float], cfg: dict) -> list[str]:
    if len(times) != len(posts):
        return ["schedule must assign every post"]
    try:
        pins = pinned_masters(posts, cfg)
    except ScheduleError as exc:
        return [str(exc)]
    problems = []
    for index, (post, moment) in enumerate(zip(posts, times)):
        label = post["id"]
        if moment < cfg["windows"][0][0] or moment > cfg["day_end"]:
            problems.append(f"{label}: outside posting window/day_end")
        if index in pins and abs(moment - times[pins[index]] - cfg["reuse_offset"]) > 1e-6:
            problems.append(f"{label}: cross-post must use its configured master offset")
        for earlier in range(index):
            gap = moment - times[earlier]
            same_platform = platform_of(post) == platform_of(posts[earlier])
            exception = (pins.get(index) == earlier and not same_platform
                         and abs(gap - cfg["reuse_offset"]) < 1e-6)
            required = max(cfg["min_gap"], cfg["platform_gap"]) if same_platform else cfg["min_gap"]
            if gap < 0 or (gap < required and not exception):
                problems.append(f"{label}: gap from {posts[earlier]['id']} is {gap:g} minutes; "
                                f"requires {required}")
    return problems


def assign_times(posts, windows, cfg, rng) -> list[int]:
    pins = pinned_masters(posts, cfg)
    times = [cfg["windows"][0][0] if index in pins
             else _jitter(windows[index], cfg["step"], rng) for index in range(len(posts))]
    edges = []
    for index, post in enumerate(posts):
        if index in pins:
            # Equality needs both bounds: a child's platform floor must move its
            # master too, never break the exact offset or waive another gap.
            edges.append((index, pins[index], -cfg["reuse_offset"]))
        for previous, earlier in enumerate(posts[:index]):
            required = cfg["reuse_offset"] if pins.get(index) == previous else cfg["min_gap"]
            if platform_of(earlier) == platform_of(post):
                required = max(required, cfg["platform_gap"])
            edges.append((previous, index, required))
    for _ in range(len(posts)):
        changed = False
        for start, end, gap in edges:
            floor = times[start] + gap
            if times[end] < floor:
                times[end] = floor
                changed = True
        if not changed:
            return times
    raise ScheduleError("impossible schedule constraints: offset/order/gap cycle")


def _minutes_of(post: dict) -> int | None:
    parsed = parse_schedule_time(post.get("schedule_time"))
    return parsed.hour * 60 + parsed.minute if parsed else None


def recent_schedules(lane, target_date, exclude, days=HISTORY_DAYS, directories=None):
    directories = directories if directories is not None else [QUEUE_DIR, POSTED_DIR]
    history = []
    for directory in directories:
        for path in sorted(Path(directory).glob("*.json")):
            if path.name == "log.json" or path.resolve() == Path(exclude).resolve():
                continue
            try:
                day = date_from_name(path)
                if day is None or not target_date - timedelta(days=days) <= day < target_date:
                    continue
                posts = load_queue(path)
                if infer_lane(posts, path) != lane:
                    continue
            except ScheduleError as exc:
                print(f"::warning::Skipping unusable schedule history: {exc}")
                continue
            minutes = [m for m in (_minutes_of(p) for p in posts) if m is not None]
            if minutes:
                history.append(sorted(minutes))
    return history


def similarity(candidate, previous, tolerance) -> float:
    used = set()
    for moment in candidate:
        for index, other in enumerate(previous):
            if index not in used and abs(moment - other) <= tolerance:
                used.add(index)
                break
    return len(used) / len(candidate) if candidate else 0.0


def plan_times(posts, cfg, target_date, best_hours, history,
               tolerance=REPEAT_TOLERANCE_MINUTES, threshold=REPEAT_THRESHOLD,
               max_attempts=MAX_ATTEMPTS) -> list[int]:
    if not posts or max_attempts < 1:
        raise ScheduleError("planning requires posts and at least one attempt")
    windows = cfg["windows"]
    weights = window_weights(windows, best_hours)
    best_times = None
    best_score = float("inf")
    for attempt in range(max_attempts):
        rng = random.Random(f"{cfg['lane']}:{target_date.isoformat()}:{attempt}")
        chosen = select_windows(windows, len(posts), weights, rng, cfg["min_gap"])
        times = assign_times(posts, chosen, cfg, rng)
        if constraint_problems(posts, times, cfg):
            continue
        score = max((similarity(times, day, tolerance) for day in history), default=0.0)
        if score < best_score:
            best_times, best_score = times, score
        if score < threshold:
            return times
    if best_times is not None:
        return best_times
    # Window preferences may use too much capacity. Prove feasibility with the
    # earliest possible allocation, never by accepting a violated hard bound.
    earliest = [(windows[0][0], windows[0][0])] * len(posts)
    times = assign_times(posts, earliest, cfg, random.Random(0))
    problems = constraint_problems(posts, times, cfg)
    if problems:
        raise ScheduleError("impossible schedule constraints: " + "; ".join(problems))
    return times


def apply_times(posts, times, target_date, tz_name) -> None:
    if len(times) != len(posts):
        raise ScheduleError("schedule must assign every post")
    stamps = [iso_time(target_date, minute, tz_name) for minute in times]
    for post, stamp in zip(posts, stamps):
        post["schedule_time"] = stamp


def atomic_text(path: Path, content: str, *, replace=True) -> None:
    """Stage alongside the destination so replace stays on the same filesystem."""
    staged = path.with_name(f".{uuid.uuid4().hex}.pending")
    try:
        with staged.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(staged, path)
        else:
            # Creating a hard link is atomic and fails if a destination appeared
            # after the collision check; unlike replace it cannot clobber a batch.
            os.link(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def summary_path_for(queue_path: Path) -> Path:
    return Path(queue_path).with_suffix(".summary.md")


def summary_content(original: str, times: list[int]) -> str:
    lines = []
    for line in original.splitlines(keepends=True):
        match = SUMMARY_ROW.match(line)
        if match and 0 <= int(match[2]) - 1 < len(times):
            replacement = format_clock(times[int(match[2]) - 1])
            line = SUMMARY_ROW.sub(lambda m: f"{m[1]}{replacement}{m[4]}", line, count=1)
        lines.append(line)
    return "".join(lines)


def patch_summary(path: Path, times: list[int]) -> bool:
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8")
    content = summary_content(original, times)
    if content == original:
        return False
    atomic_text(path, content)
    return True


def rescheduled_path(queue_path: Path, target_date: date_cls) -> Path:
    queue_path = Path(queue_path)
    if date_from_name(queue_path) is None:
        raise ScheduleError("rescheduling requires a dated queue filename")
    return queue_path.with_name(DATE_IN_NAME.sub(target_date.isoformat(), queue_path.name, count=1))


def relocate_plan(source: Path, destination: Path, content: str, times: list[int]) -> bool:
    """Promote a validated reschedule without overwriting another batch.

    Callers serialize batch mutations. Queue/summary writes are individually
    atomic; handled filesystem failures restore the originals and remove only
    the outputs created by this operation.
    """
    old_summary, new_summary = summary_path_for(source), summary_path_for(destination)
    if destination.exists() or new_summary.exists():
        raise ScheduleError(f"reschedule destination already exists: {destination}")
    original_summary = old_summary.read_bytes().decode("utf-8") if old_summary.is_file() else None
    created = []
    summary_removed = False
    try:
        if original_summary is not None:
            atomic_text(new_summary, summary_content(original_summary, times), replace=False)
            created.append(new_summary)
        atomic_text(destination, content, replace=False)
        created.append(destination)
        if original_summary is not None:
            old_summary.unlink()
            summary_removed = True
        source.unlink()
    except OSError:
        if summary_removed:
            atomic_text(old_summary, original_summary, replace=False)
        for path in reversed(created):
            path.unlink()
        raise
    return original_summary is not None


def validate_schedule(posts: list[dict], source: object, tz_name: str = EASTERN) -> list[str]:
    """Date/offset checks, without freshness; safe for historical queue inspection."""
    problems = []
    path = Path(str(source))
    if not isinstance(posts, list) or not posts or any(not isinstance(p, dict) for p in posts):
        return [f"{source}: queue must be a non-empty list of post objects"]
    try:
        target = infer_target_date(posts, path)
    except ScheduleError as exc:
        problems.append(str(exc))
        target = None
    seen = {}
    for index, post in enumerate(posts, 1):
        label = str(post.get("id") or f"post {index}")
        raw = post.get("schedule_time")
        moment = parse_schedule_time(raw)
        if moment is None:
            problems.append(f"{source}: {label} has an unparseable schedule_time: {raw!r}")
            continue
        if moment.utcoffset() is None:
            problems.append(f"{source}: {label} schedule_time has no UTC offset")
            continue
        try:
            local = local_datetime(moment, tz_name or EASTERN)
        except ScheduleError as exc:
            problems.append(f"{source}: {label}: {exc}")
            continue
        if local.utcoffset() != moment.utcoffset():
            problems.append(f"{source}: {label} offset {format_offset(moment.utcoffset())} "
                            f"is not {tz_name or EASTERN} at that instant; check EDT/EST")
        if target is not None and (local.date() != target or moment.date() != target):
            problems.append(f"{source}: {label} is scheduled for {moment.date()} "
                            f"(Eastern {local.date()}) but the batch targets {target}")
        key = (platform_of(post), moment)
        if key in seen:
            problems.append(f"{source}: {label} and {seen[key]} are both scheduled "
                            f"on {key[0]} at {moment.isoformat()}")
        seen[key] = label
    return problems


def _validate_with_config(posts, queue_path, cfg) -> list[str]:
    problems = validate_schedule(posts, queue_path, cfg["timezone"])
    if problems:
        return problems
    times = []
    for post in posts:
        moment = parse_schedule_time(post["schedule_time"])
        times.append(moment.hour * 60 + moment.minute + moment.second / 60
                     + moment.microsecond / 60000000)
    return [f"{queue_path}: {p}" for p in constraint_problems(posts, times, cfg)]


def validate_for_publish(posts, queue_path, *, now=None, min_lead_minutes=15) -> list[str]:
    """Shared gate; None lead time explicitly requests historical/static checks."""
    path = Path(queue_path)
    config_path = DEFAULT_CONFIG
    if path.parent.name == "queue":
        data_config = path.resolve().parent.parent / "config" / "schedule-windows.json"
        if data_config.is_file():
            config_path = data_config
    return _validate_for_publish(posts, queue_path, now=now,
                                 min_lead_minutes=min_lead_minutes, config_path=config_path)


def _validate_for_publish(posts, queue_path, *, now=None, min_lead_minutes=15,
                          config_path=DEFAULT_CONFIG) -> list[str]:
    if not isinstance(posts, list) or not posts or any(not isinstance(p, dict) for p in posts):
        return [f"{queue_path}: queue must be a non-empty list of post objects"]
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.utcoffset() is None:
        return [f"{queue_path}: now must be timezone-aware"]
    if min_lead_minutes is not None and (
        not isinstance(min_lead_minutes, (int, float))
        or not 0 <= min_lead_minutes < float("inf")
    ):
        return [f"{queue_path}: min_lead_minutes must be finite and non-negative"]
    try:
        cfg = lane_config(load_config(config_path), infer_lane(posts, Path(queue_path)))
        problems = _validate_with_config(posts, queue_path, cfg)
    except ScheduleError as exc:
        problems = [str(exc)]
    if min_lead_minutes is None:
        return problems
    cutoff = now.astimezone(timezone.utc) + timedelta(minutes=min_lead_minutes)
    for index, post in enumerate(posts, 1):
        moment = parse_schedule_time(post.get("schedule_time"))
        if moment is not None and moment.utcoffset() is not None and (
            moment <= now or moment < cutoff
        ):
            problems.append(f"{queue_path}: {post.get('id', index)} schedule_time is expired "
                            f"or too close; requires {min_lead_minutes:g} minutes lead time. "
                            "Explicitly reschedule and renew approval.")
    return problems


def plan_file(queue_path, config_path=DEFAULT_CONFIG, digest_path=DEFAULT_DIGEST,
              history_days=HISTORY_DAYS, dry_run=False, lane_override=None, *,
              target_date: date_cls | None = None, reschedule=False,
              history_directories: list[Path] | None = None) -> dict:
    queue_path = Path(queue_path)
    posts = load_queue(queue_path)
    lane = infer_lane(posts, queue_path)
    if lane_override and lane_override != lane:
        raise ScheduleError("lane override disagrees with queue account/filename")
    config = load_config(config_path)
    cfg = lane_config(config, lane)
    filename_date = date_from_name(queue_path)
    if filename_date is None:
        raise ScheduleError("queue filename must contain the source YYYY-MM-DD date")
    existing = accepted_plan(posts, queue_path, check_membership=not reschedule)
    inferred = date_cls.fromisoformat(existing["target_date"]) if existing else filename_date
    target_date = target_date or inferred
    if target_date != inferred and not reschedule:
        raise ScheduleError("changing the target date requires --reschedule --date")
    destination = rescheduled_path(queue_path, target_date) if reschedule else queue_path
    if destination != queue_path and (
        destination.exists() or summary_path_for(destination).exists()
    ):
        raise ScheduleError(f"reschedule destination already exists: {destination}")
    before = [post.get("schedule_time") for post in posts]
    best_hours, history = [], []
    if existing is not None and not reschedule:
        problems = _validate_with_config(posts, queue_path, cfg)
        if problems:
            raise ScheduleError("; ".join(problems))
        times = [_minutes_of(post) for post in posts]
    else:
        if not reschedule:
            # Generation may need an offset correction, but never repair a wrong
            # target date by silently accepting the model's date or rolling it.
            for post in posts:
                moment = parse_schedule_time(post.get("schedule_time"))
                if moment is not None and moment.date() != target_date:
                    raise ScheduleError(f"{post.get('id')}: schedule date {moment.date()} "
                                        f"disagrees with target/filename {target_date}")
        best_hours = read_best_hours(digest_path)
        history = recent_schedules(lane, target_date, queue_path, days=history_days,
                                   directories=history_directories)
        times = plan_times(posts, cfg, target_date, best_hours, history)
        apply_times(posts, times, target_date, cfg["timezone"])
        posts[0]["schedule_plan"] = {
            "version": 1, "target_date": target_date.isoformat(),
            "source_date": target_date.isoformat(), "timezone": cfg["timezone"],
            "explicit_reschedule": bool(reschedule),
            "post_ids": [post.get("id") for post in posts],
            "inputs": {"config": config[lane], "best_hours": best_hours, "history": history,
                       "seed": f"{lane}:{target_date.isoformat()}"},
        }
        if destination != queue_path:
            posts[0]["schedule_plan"]["previous_queue"] = queue_path.name
        problems = _validate_with_config(posts, destination, cfg)
        if problems:
            raise ScheduleError("; ".join(problems))
    after = [post["schedule_time"] for post in posts]
    result = {
        "lane": lane, "date": target_date, "timezone": cfg["timezone"],
        "times": times, "before": before, "after": after, "posts": posts,
        "history_days_compared": len(history), "best_hours": best_hours,
        "summary_patched": False, "preserved": existing is not None and not reschedule,
        "queue_path": destination, "previous_queue_path": queue_path,
    }
    if not dry_run:
        result["summary_patched"] = _persist_plan(result)
    return result


def _persist_plan(result: dict) -> bool:
    source, destination = result["previous_queue_path"], result["queue_path"]
    content = json.dumps(result["posts"], indent=2, ensure_ascii=False) + "\n"
    if destination != source:
        return relocate_plan(source, destination, content, result["times"])
    if not result["preserved"]:
        atomic_text(source, content)
    return patch_summary(summary_path_for(source), result["times"])


def reschedule_queue(qpath: Path | str, date: date_cls | str, *, repo_root: Path = ROOT) -> dict:
    """App reschedule transaction; returns JSON-safe names and an immutable archive.

    The caller holds its batch mutation lock and invalidates approval on success.
    Feasibility/freshness are checked before archiving or changing any queue.
    """
    repo_root = Path(repo_root).resolve()
    qpath = Path(qpath)
    if not qpath.is_absolute():
        qpath = repo_root / ("queue" if qpath.parent == Path(".") else "") / qpath
    qpath = qpath.resolve()
    if qpath.parent != repo_root / "queue" or qpath.suffix != ".json":
        raise ScheduleError("reschedule queue must be a JSON file directly inside repo_root/queue")
    if isinstance(date, str):
        try:
            target = date_cls.fromisoformat(date)
        except ValueError as exc:
            raise ScheduleError("reschedule date must be YYYY-MM-DD") from exc
        if target.isoformat() != date:
            raise ScheduleError("reschedule date must be YYYY-MM-DD")
    elif isinstance(date, date_cls) and not isinstance(date, datetime):
        target = date
    else:
        raise ScheduleError("reschedule date must be a date or YYYY-MM-DD string")

    original = qpath.read_bytes()
    summary = summary_path_for(qpath)
    original_summary = summary.read_bytes() if summary.is_file() else None
    result = plan_file(
        qpath, config_path=repo_root / "config" / "schedule-windows.json",
        digest_path=repo_root / "analytics" / "insights-digest.md",
        target_date=target, reschedule=True, dry_run=True,
        history_directories=[repo_root / "queue", repo_root / "posted"],
    )
    blockers = _validate_for_publish(
        result["posts"], result["queue_path"],
        config_path=repo_root / "config" / "schedule-windows.json",
    )
    if blockers:
        raise ScheduleError("; ".join(blockers))

    archive_root = (repo_root / ".local" / "reschedules").resolve()
    if not archive_root.is_relative_to(repo_root):
        raise ScheduleError("reschedule archive must remain inside repo_root")
    archive = archive_root / uuid.uuid4().hex
    archive.mkdir(parents=True)
    atomic_text(archive / qpath.name, original.decode("utf-8"), replace=False)
    if original_summary is not None:
        atomic_text(archive / summary.name, original_summary.decode("utf-8"), replace=False)
    manifest = {
        "version": 1, "operation": "reschedule", "previous_queue": qpath.name,
        "queue": result["queue_path"].name, "target_date": target.isoformat(),
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "previous_queue_sha256": hashlib.sha256(original).hexdigest(),
        "previous_summary_sha256": (hashlib.sha256(original_summary).hexdigest()
                                    if original_summary is not None else None),
    }
    atomic_text(archive / "manifest.json", json.dumps(manifest, indent=2) + "\n", replace=False)
    if qpath.read_bytes() != original or (
        summary.read_bytes() if summary.is_file() else None
    ) != original_summary:
        raise ScheduleError("queue or summary changed during reschedule; original snapshot retained")
    _persist_plan(result)
    return {
        "queue": result["queue_path"].name, "previous_queue": qpath.name,
        "target_date": target.isoformat(), "archive": archive.relative_to(repo_root).as_posix(),
    }


def check_file(queue_path: Path, config_path: Path = DEFAULT_CONFIG) -> list[str]:
    posts = load_queue(queue_path)
    try:
        cfg = lane_config(load_config(config_path), infer_lane(posts, queue_path))
        return _validate_with_config(posts, queue_path, cfg)
    except ScheduleError as exc:
        return [str(exc)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file", type=Path, nargs="+")
    parser.add_argument("--data-root", type=Path,
                        default=Path(os.environ.get("LAYER8_DATA_ROOT") or ROOT),
                        help="Queue/config/analytics/history root; code stays beside this script.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--digest", type=Path)
    parser.add_argument("--history-days", type=int, default=HISTORY_DAYS)
    parser.add_argument("--lane")
    parser.add_argument("--date", type=date_cls.fromisoformat, help="Explicit Eastern target date.")
    parser.add_argument("--reschedule", action="store_true",
                        help="Deliberately replace an accepted plan; invalidates approval.")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--for-publish", action="store_true",
                        help="Check only, including current submission lead time.")
    parser.add_argument("--min-lead-minutes", type=float, default=15)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if (args.check or args.for_publish) and (args.reschedule or args.dry_run):
        parser.error("check modes cannot reschedule")
    data_root = args.data_root.resolve()
    config_path = args.config or data_root / "config" / "schedule-windows.json"
    digest_path = args.digest or data_root / "analytics" / "insights-digest.md"
    failures = []
    for queue_file in args.queue_file:
        if not queue_file.is_absolute():
            queue_file = data_root / queue_file
        try:
            if args.check or args.for_publish:
                posts = load_queue(queue_file)
                problems = (_validate_for_publish(posts, queue_file,
                            min_lead_minutes=args.min_lead_minutes, config_path=config_path)
                            if args.for_publish else check_file(queue_file, config_path))
                if args.date and infer_target_date(posts, queue_file) != args.date:
                    problems.append(f"{queue_file}: accepted target disagrees with --date {args.date}")
                if problems:
                    raise ScheduleError("; ".join(problems))
                print(f"{queue_file}: schedule OK")
                continue
            result = plan_file(queue_file, config_path, digest_path, args.history_days,
                               args.dry_run, args.lane, target_date=args.date,
                               reschedule=args.reschedule,
                               history_directories=[data_root / "queue", data_root / "posted"])
            slots = " ".join(format_clock(t) for t in result["times"])
            action = "Preserved" if result["preserved"] else "Would schedule" if args.dry_run else "Scheduled"
            print(f"{action} {len(result['times'])} {result['lane']} post(s) for "
                  f"{result['date']} ({result['timezone']}): {slots}")
            if result["queue_path"] != result["previous_queue_path"]:
                print(f"{'Would move' if args.dry_run else 'Moved'} "
                      f"{result['previous_queue_path']} -> {result['queue_path']}")
        except (ScheduleError, OSError) as exc:
            failures.append(str(exc))
            print(f"::error::{exc}")
    if failures:
        raise SystemExit(f"{len(failures)} schedule problem(s)")


if __name__ == "__main__":
    main()
