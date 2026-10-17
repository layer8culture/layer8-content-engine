#!/usr/bin/env python3
"""Resolve a generation run's target once, using original run creation metadata.

Daily cron uses its most recent nominal UTC occurrence, not the runner's actual
start time. Retries reuse the original run's created_at. Manual overrides are
explicit; lane defaults remain main=Eastern tomorrow, lofi=UTC today,
Deal Lab=Eastern today. All resulting posting dates are Eastern calendar dates.
"""
from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from schedule_planner import EASTERN, ScheduleError, local_datetime, parse_schedule_time

PREFIXES = {"layer8culture": "", "lofi": "lofi-", "deallab": "deallab-"}


def resolve_target_date(lane: str, *, event: str, run_created_at: datetime,
                        cron: str = "0 2 * * *", override: date | None = None) -> date:
    if lane not in PREFIXES:
        raise ScheduleError(f"unknown generation lane {lane!r}")
    if run_created_at.utcoffset() is None:
        raise ScheduleError("run_created_at must be timezone-aware")
    if override is not None:
        return override
    nominal = run_created_at.astimezone(timezone.utc)
    if event == "schedule":
        match = re.fullmatch(r"(\d{1,2}) (\d{1,2}) \* \* \*", cron)
        if not match or int(match[1]) > 59 or int(match[2]) > 23:
            raise ScheduleError("only explicit daily UTC cron schedules are supported")
        nominal = nominal.replace(hour=int(match[2]), minute=int(match[1]),
                                  second=0, microsecond=0)
        if nominal > run_created_at:
            nominal -= timedelta(days=1)
    elif event != "workflow_dispatch":
        raise ScheduleError(f"unsupported generation event {event!r}")
    if lane == "lofi":
        return nominal.date()
    eastern = local_datetime(nominal, EASTERN).date()
    return eastern + timedelta(days=1) if lane == "layer8culture" else eastern


def run_values(lane: str, target: date) -> dict[str, str]:
    stem = PREFIXES[lane] + target.isoformat()
    return {
        "BATCH_DATE": target.isoformat(),
        "QUEUE_FILE": f"queue/{stem}.json",
        "SUMMARY_FILE": f"queue/{stem}.summary.md",
        "GENERATION_TARGET_INSTRUCTION": (
            f"AUTHORITATIVE RUN TARGET: {target.isoformat()} in {EASTERN}. "
            "This overrides every relative today/tomorrow/UTC-date instruction above. "
            f"Write exactly queue/{stem}.json and queue/{stem}.summary.md. "
            "Use this date for post IDs and all schedule_time values, with the correct "
            "Eastern seasonal offset. The scheduler assigns final times after generation."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=PREFIXES, required=True)
    parser.add_argument("--event", choices=("schedule", "workflow_dispatch"), required=True)
    parser.add_argument("--run-created-at", required=True)
    parser.add_argument("--cron", default="0 2 * * *")
    parser.add_argument("--date", default="")
    parser.add_argument("--github-env", type=Path)
    args = parser.parse_args()
    try:
        created = parse_schedule_time(args.run_created_at)
        if created is None:
            raise ScheduleError("missing or invalid original workflow run created_at")
        target = resolve_target_date(
            args.lane, event=args.event, run_created_at=created, cron=args.cron,
            override=date.fromisoformat(args.date) if args.date else None,
        )
        values = run_values(args.lane, target)
        if args.github_env:
            with args.github_env.open("a", encoding="utf-8") as output:
                for key, value in values.items():
                    output.write(f"{key}={value}\n")
        print(f"Target: {target.isoformat()} ({EASTERN}); queue: {values['QUEUE_FILE']}")
    except (ValueError, OSError) as exc:
        raise SystemExit(f"::error::{exc}") from exc


if __name__ == "__main__":
    main()
