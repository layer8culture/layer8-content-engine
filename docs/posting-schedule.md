# Posting schedule

The scheduler, not the content model, assigns final `schedule_time` values.
All lanes publish on explicit **America/New_York** calendar dates, with the
correct Eastern offset. The shared configuration is
[`config/schedule-windows.json`](../config/schedule-windows.json).

## Generation target

Generation workflows resolve `BATCH_DATE`, `QUEUE_FILE`, `SUMMARY_FILE`, and a
target-date instruction **once** using `scripts/generation_date.py`. That same
instruction overrides relative dates in the initial generation prompt and every
retry. Recovery, scheduling, asset generation and the approval PR all use the
same resolved filenames; there is no "newest queue" fallback.

Scheduled runs use the most recent nominal daily UTC cron occurrence relative
to the **original workflow run's `created_at`**, fetched from GitHub's workflow
run API. They do not calculate tomorrow from the worker's actual start time.
For the 02:00 UTC cron, a run delayed beyond Eastern midnight still targets that
UTC date's Eastern posting day. A rerun uses the original run metadata, not a new
date. Missing metadata fails the workflow rather than guessing.

Manual workflow dispatch accepts an optional `date` in `YYYY-MM-DD` format.
Blank preserves the existing lane defaults, evaluated at original run creation:

| Lane | Manual default |
| --- | --- |
| layer8culture | Tomorrow in America/New_York |
| lofi | Today's UTC date, interpreted as the Eastern posting date |
| deallab | Today in America/New_York |

Deal Lab remains paused: **manual dispatch only**, with no scheduled trigger.
An explicit manual date is also the recovery path when the nominal target has
expired. Generation does not automatically roll an old batch forward.

## Planning and stability

```powershell
python scripts\schedule_planner.py queue\2026-09-06.json --date 2026-09-06
python scripts\schedule_planner.py queue\2026-09-06.json --dry-run
```

The planner runs after generation JSON recovery/repair and before asset
preparation. It preserves post order and content, updating `schedule_time` and
the summary table's Time column. The first post additionally stores
`schedule_plan`: version, source/target dates, timezone, explicit-reschedule flag,
ordered post IDs, and the accepted configuration/analytics/history/seed inputs.
This is scheduling provenance, **not proof of human approval**.

Initial plans use a seeded daily window selection and jitter. The optional
`analytics/insights-digest.md` **Best posting hours** line weights proven windows.
The previous seven days in `queue/` and `posted/` provide an anti-repeat signal.
Missing analytics simply means unweighted selection; anti-repeat is a preference,
not permission to violate constraints.

Once accepted, a normal rerun preserves existing times byte-for-byte, even when
analytics or history change. Changed lane constraints may block an accepted plan,
but never silently move it. Changing batch membership also requires explicit
rescheduling. Valid same-date manual time edits are retained; review/approval
fingerprints must be renewed by the caller.

## Hard constraints and cross-posts

| Configuration key | Meaning |
| --- | --- |
| `timezone` | America/New_York; computed at the actual instant, including DST transitions. |
| `windows` | Ordered start/end preference pools; first start is the earliest permitted time. |
| `min_gap_minutes` | Required gap between posts, except an explicitly configured master/cross-post pair. |
| `same_platform_min_gap_minutes` | Additional same-platform minimum; never waived for reuse. |
| `jitter_step_minutes` | Jitter granularity within each window. |
| `day_end` | Hard latest time; it cannot be relaxed to avoid a repeat or fit more posts. |
| `reuse.mode` | `independent` gives cross-posts their own slots; `offset` pins them to their master. |
| `reuse.offset_minutes` | Exact delay from the earlier master. |
| `reuse.allowed_platform_pairs` | Explicit distinct-platform pairs allowed to use the offset exception. |

Layer8Culture's TikTok, Instagram and YouTube posts have independent slots.
Lofi allows exactly an **Instagram master -> YouTube reuse** at +5 minutes.
That exception does not waive a gap to another post, allow same-platform
collisions, excuse a missing/later master, or permit exceeding `day_end`.

If jittered selections overflow, the planner first tries other selections and
then the earliest feasible allocation. Impossible capacity, conflicting exact
offsets/order, and end-window overflow hard-fail. Every candidate is validated
before the queue is atomically replaced with a same-directory staged file.
Ambiguous/nonexistent DST clock times cannot be generated; offset-aware
historical times are checked against the actual transition instant.

## Separate code and data roots

The scheduler CLI runs current code from its own script location. Setting
`LAYER8_DATA_ROOT` (or passing `--data-root`) changes relative queue paths,
configuration, analytics and history to the specified data checkout; it never
executes that checkout's possibly stale `scripts/` files. Explicit `--config` and
`--digest` paths override those defaults.

`reschedule_queue(..., repo_root=...)` likewise uses that root for queue/summary
updates, configuration, analytics/history and archives. Shared publish validation
uses the configuration beside an absolute `queue/` path when present, otherwise
the code checkout's default configuration. Configurations are validated strictly:
older lofi configuration must explicitly include
`reuse.allowed_platform_pairs: [["instagram", "youtube"]]`.

## Validation and explicit rescheduling

```powershell
# Structural date/offset/window/gap validation, including archived batches:
python scripts\schedule_planner.py queue\2026-09-06.json --check

# Read-only review/merge/submission gate with the current clock:
python scripts\schedule_planner.py queue\2026-09-06.json --for-publish --min-lead-minutes 15

# Deliberately replace a plan, moving to the target filename but keeping post IDs:
python scripts\schedule_planner.py queue\2026-09-06.json --reschedule --date 2026-09-07
```

The filename is the initial authoritative date. A generation timestamp on a
different date is rejected, even if all posts agree with each other. Only an
explicit reschedule moves the queue and its summary to the chosen target date:
the example becomes `queue\2026-09-07.json`. Lane prefixes and any filename suffix
are preserved. Post IDs and media paths do not change. The new `schedule_plan`
records `previous_queue`, while its target date must match the new filename.
An existing destination queue or summary is a blocker, never overwritten.
In-app rescheduling must pass the chosen date, select the returned queue path,
invalidate the existing approval, and request renewed review.

Python interfaces:

```python
# Stable app/worker interface (date object or YYYY-MM-DD string):
result = reschedule_queue(queue_path, "2026-09-07", repo_root=ROOT)
new_filename = result["queue"]  # e.g. "lofi-2026-09-07.json"
archive = result["archive"]    # repository-relative immutable original snapshot

# Lower-level planner, also used by the CLI:
result = plan_file(queue_path, target_date=date(2026, 9, 7), reschedule=True)
new_queue_path = result["queue_path"]  # pathlib.Path; the old path no longer exists
previous_queue_path = result["previous_queue_path"]
validate_for_publish(posts, queue_path, now=None, min_lead_minutes=15)  # list[str]
```

The app interface checks feasibility and freshness before mutation, archives the
exact old queue and summary plus a hashed manifest under `.local/reschedules/`,
then applies the already validated plan without recalculating it. It accepts
only JSON queues directly inside the supplied repository's `queue/` directory.
No queue moves if the plan is infeasible, the destination exists, or the archive
cannot be safely written. Callers hold the shared mutation lock, save the new
filename and invalidate approval. Historical snapshots are not provider receipts.

`validate_for_publish` accepts an aware `datetime` as `now`, defaulting to aware
UTC. It returns date/offset/target, configuration, window/gap, and freshness
blockers without mutating content. Naive clocks and invalid lead-time settings
are blockers. A timestamp exactly 15 minutes away meets the default minimum;
expired times and timestamps equal to now are always blocked. No immediate-post
fallback is allowed.

For the shared readiness report's historical/static mode only,
`min_lead_minutes=None` omits freshness while retaining all structural checks.
Review, merge and provider submission must use a numeric lead-time minimum.

All generation lanes check schema and schedule again before the approval PR.
The shared readiness gate additionally checks final media; a successful renderer
exit alone is not readiness. The same freshness check must run at review,
approval/merge and provider submission, since waiting for checks can consume the
remaining lead time. Human approval through a merged PR remains mandatory.

## Focused tests

```powershell
python -m unittest tests.test_schedule_planner
```

Coverage includes deterministic rotation, analytics/history changes, accepted
plan stability, impossible capacity, offset exceptions, end-window overflow,
filename/target mismatches, DST transitions with/without tzdata, expired and
too-close times, atomic-write failure, explicit rescheduling, and delayed
workflow starts across midnight. Tests use isolated fixtures under `.local/`.
