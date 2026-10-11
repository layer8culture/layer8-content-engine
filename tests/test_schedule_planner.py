import copy
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generation_date
import schedule_planner as planner


def lane_cfg(**overrides):
    cfg = {
        "lane": "testlane", "timezone": planner.EASTERN, "min_gap": 30,
        "platform_gap": 45, "step": 5, "day_end": 22 * 60,
        "reuse_mode": "independent", "reuse_offset": 5,
        "reuse_pairs": [["instagram", "youtube"]],
        "windows": [(hour * 60, hour * 60 + 35) for hour in range(7, 22)],
    }
    cfg.update(overrides)
    return cfg


def post(post_id, platform="instagram", account="layer8culture", visual=None, when="09:00"):
    return {
        "id": post_id, "account": account, "platform": platform, "format": "single",
        "schedule_time": f"2026-08-21T{when}:00-04:00", "text": "Caption",
        "visual": visual if visual is not None else {"source": "openai"},
    }


class PlanningTests(unittest.TestCase):
    def test_seeded_rotation_and_chronological_platform_spacing(self):
        posts = [post(f"p{i}", "instagram" if i % 2 else "tiktok") for i in range(8)]
        cfg = lane_cfg(min_gap=20, platform_gap=60)
        days = []
        for offset in range(30):
            day = date(2026, 8, 1) + timedelta(days=offset)
            times = planner.plan_times(posts, cfg, day, [], [])
            self.assertEqual(times, planner.plan_times(posts, cfg, day, [], []))
            self.assertEqual(planner.constraint_problems(posts, times, cfg), [])
            days.append(tuple(times))
        self.assertGreater(len(set(days)), 25)

    def test_history_anti_repeat(self):
        posts = [post(f"p{i}") for i in range(6)]
        cfg = lane_cfg()
        first = planner.plan_times(posts, cfg, date(2026, 8, 21), [], [])
        second = planner.plan_times(posts, cfg, date(2026, 8, 21), [], [first])
        self.assertLess(planner.similarity(first, second, 10), planner.REPEAT_THRESHOLD)

    def test_impossible_capacity_hard_fails(self):
        cfg = lane_cfg(windows=[(21 * 60, 21 * 60 + 15)])
        with self.assertRaisesRegex(planner.ScheduleError, "impossible"):
            planner.plan_times([post(str(i)) for i in range(4)], cfg, date(2026, 8, 21), [], [])

    def test_jitter_overflow_uses_feasible_earliest_plan(self):
        cfg = lane_cfg(windows=[(21 * 60, 22 * 60)], min_gap=60, platform_gap=60)
        times = planner.plan_times([post("a"), post("b")], cfg, date(2026, 8, 21), [], [])
        self.assertEqual(times, [21 * 60, 22 * 60])

    def test_more_posts_than_windows_remains_possible(self):
        cfg = lane_cfg(windows=[(9 * 60, 9 * 60 + 30)])
        posts = [post(str(i)) for i in range(3)]
        times = planner.plan_times(posts, cfg, date(2026, 8, 21), [], [])
        self.assertEqual(planner.constraint_problems(posts, times, cfg), [])

    def test_day_end_is_not_a_penalty_that_can_be_accepted(self):
        cfg = lane_cfg(windows=[(22 * 60, 22 * 60)])
        with self.assertRaises(planner.ScheduleError):
            planner.plan_times([post("a"), post("b")], cfg, date(2026, 8, 21),
                               [], [[22 * 60]], max_attempts=1)
        with self.assertRaises(planner.ScheduleError):
            planner.iso_time(date(2026, 8, 21), 1440, planner.EASTERN)

    def test_exact_cross_platform_offset_exception(self):
        cfg = lane_cfg(reuse_mode="offset", min_gap=120, platform_gap=120)
        posts = [post("a"), post("b", "youtube", visual={"source": "reuse", "of": "a"}),
                 post("c")]
        times = planner.plan_times(posts, cfg, date(2026, 8, 21), [], [])
        self.assertEqual(times[1] - times[0], 5)
        self.assertEqual(planner.constraint_problems(posts, times, cfg), [])
        self.assertTrue(planner.constraint_problems(posts, [540, 550, 800], cfg))

    def test_cross_post_may_not_bypass_same_platform_gap(self):
        cfg = lane_cfg(reuse_mode="offset")
        posts = [post("a"), post("b", visual={"source": "reuse", "of": "a"})]
        with self.assertRaisesRegex(planner.ScheduleError, "exception"):
            planner.plan_times(posts, cfg, date(2026, 8, 21), [], [])

    def test_cross_post_exception_does_not_cover_unrelated_neighbors(self):
        cfg = lane_cfg(reuse_mode="offset", min_gap=120)
        posts = [post("a"), post("other", "tiktok"),
                 post("b", "youtube", visual={"source": "reuse", "of": "a"})]
        with self.assertRaises(planner.ScheduleError):
            planner.plan_times(posts, cfg, date(2026, 8, 21), [], [])

    def test_cross_post_exception_does_not_cover_missing_master(self):
        cfg = lane_cfg(reuse_mode="offset")
        with self.assertRaisesRegex(planner.ScheduleError, "precede"):
            planner.plan_times([post("b", "youtube", visual={"source": "reuse", "of": "missing"})],
                               cfg, date(2026, 8, 21), [], [])

    def test_cross_post_platform_floor_can_shift_its_master(self):
        cfg = lane_cfg(reuse_mode="offset", min_gap=30, platform_gap=180,
                       windows=[(540, 540)], day_end=800)
        posts = [post("earlier", "youtube"), post("master"),
                 post("copy", "youtube", visual={"source": "reuse", "of": "master"})]
        times = planner.plan_times(posts, cfg, date(2026, 8, 21), [], [])
        self.assertEqual(times, [540, 715, 720])
        self.assertEqual(planner.constraint_problems(posts, times, cfg), [])

    def test_cross_post_at_day_end_overflows_and_fails(self):
        cfg = lane_cfg(reuse_mode="offset", windows=[(1320, 1320)])
        posts = [post("a"), post("b", "youtube", visual={"source": "reuse", "of": "a"})]
        with self.assertRaises(planner.ScheduleError):
            planner.plan_times(posts, cfg, date(2026, 8, 21), [], [])

    def test_every_shipped_lane_handles_realistic_capacity(self):
        config = planner.load_config()
        for lane, count in {"layer8culture": 12, "lofi": 5, "deallab": 5}.items():
            cfg = planner.lane_config(config, lane)
            posts = [post(f"p{i}", account=lane) for i in range(count)]
            for offset in range(14):
                times = planner.plan_times(posts, cfg, date(2026, 8, 1) + timedelta(days=offset), [], [])
                self.assertEqual(planner.constraint_problems(posts, times, cfg), [], lane)

    def test_invalid_config_fails_closed(self):
        config = planner.load_config()
        for changes in ({"min_gap_minutes": 0}, {"jitter_step_minutes": -1},
                        {"day_end": "06:00"}, {"reuse": {"mode": "offset"}},
                        {"windows": [["25:00", "26:00"]]}):
            candidate = copy.deepcopy(config)
            candidate["layer8culture"].update(changes)
            with self.assertRaises(planner.ScheduleError):
                planner.lane_config(candidate, "layer8culture")

    def test_analytics_weighting_retained(self):
        self.assertEqual(planner.parse_best_hours(
            "**Best posting hours:** 19:00 (4 top posts), 09:00 (3 top posts)"), [1140, 540])
        self.assertEqual(planner.parse_best_hours(
            "**Best posting hours:** Not enough posting-hour data yet."), [])
        cfg = lane_cfg()
        posts = [post("a"), post("b")]
        hits = []
        for best in ([], [1140]):
            hits.append(sum(any(abs(t - 1140) <= 35 for t in planner.plan_times(
                posts, cfg, date(2026, 8, 1) + timedelta(days=i), best, [])) for i in range(60)))
        self.assertGreater(hits[1], hits[0] * 2)


class ValidationTests(unittest.TestCase):
    def check(self, posts, **kwargs):
        return planner.validate_for_publish(
            posts, Path("queue") / "2026-08-21.json",
            now=kwargs.pop("now", datetime(2026, 8, 21, 12, tzinfo=timezone.utc)), **kwargs)

    def test_clean_future_times(self):
        self.assertEqual(self.check([post("a"), post("b", when="14:00")]), [])

    def test_filename_wins_over_all_wrong_post_dates(self):
        posts = [post("a"), post("b", when="14:00")]
        for p in posts:
            p["schedule_time"] = p["schedule_time"].replace("2026-08-21", "2026-08-22")
        self.assertTrue(any("targets 2026-08-21" in p for p in self.check(posts)))

    def test_invalid_naive_and_wrong_offset(self):
        for value, expected in (("tomorrow", "unparseable"),
                                ("2026-08-21T09:00:00", "no UTC offset"),
                                ("2026-08-21T09:00:00-05:00", "EDT/EST")):
            p = post("a")
            p["schedule_time"] = value
            self.assertTrue(any(expected in problem for problem in self.check([p])))

    def test_expired_and_too_close_times(self):
        for hour, minute in ((13, 0), (12, 59), (12, 46)):
            self.assertTrue(any("lead time" in p for p in self.check(
                [post("a")], now=datetime(2026, 8, 21, hour, minute, tzinfo=timezone.utc))))
        self.assertEqual(self.check(
            [post("a")], now=datetime(2026, 8, 21, 12, 45, tzinfo=timezone.utc)), [])

    def test_zero_lead_does_not_accept_now(self):
        self.assertTrue(self.check([post("a")], min_lead_minutes=0,
                                   now=datetime(2026, 8, 21, 13, tzinfo=timezone.utc)))

    def test_explicit_static_mode_keeps_structure_but_omits_freshness(self):
        later = datetime(2026, 8, 30, tzinfo=timezone.utc)
        self.assertEqual(self.check([post("a")], min_lead_minutes=None, now=later), [])
        self.assertTrue(self.check([post("a"), post("b", when="09:01")],
                                   min_lead_minutes=None, now=later))

    def test_naive_now_and_invalid_lead_are_blockers(self):
        self.assertTrue(self.check([post("a")], now=datetime(2026, 8, 21)))
        for lead in (-1, "15", float("inf"), float("nan")):
            self.assertTrue(self.check([post("a")], min_lead_minutes=lead))

    def test_platform_and_global_gaps_and_day_end(self):
        for batch in ([post("a"), post("b", "tiktok", when="09:05")],
                      [post("a"), post("b", when="09:35")],
                      [post("a", when="22:16")]):
            self.assertTrue(self.check(batch))

    def test_mixed_lane_blocked(self):
        self.assertTrue(self.check([post("a"), post("b", account="lofi", when="14:00")]))

    def test_lofi_offset_is_accepted_only_for_master_child(self):
        posts = [post("a", account="lofi"), post("b", "youtube", account="lofi", when="09:05",
                  visual={"source": "reuse", "of": "a"})]
        self.assertEqual(planner.validate_for_publish(
            posts, Path("queue") / "lofi-2026-08-21.json",
            now=datetime(2026, 8, 21, 12, tzinfo=timezone.utc)), [])
        posts[1]["visual"] = {"source": "openai"}
        self.assertTrue(planner.validate_for_publish(
            posts, Path("queue") / "lofi-2026-08-21.json",
            now=datetime(2026, 8, 21, 12, tzinfo=timezone.utc)))


class DstTests(unittest.TestCase):
    def test_seasonal_offsets(self):
        for day, offset in ((date(2026, 7, 15), -4), (date(2026, 1, 15), -5),
                            (date(2026, 3, 8), -4), (date(2026, 11, 1), -5)):
            self.assertEqual(planner.utc_offset(day, planner.EASTERN), timedelta(hours=offset))
            self.assertEqual(planner._us_eastern_offset(day), timedelta(hours=offset))

    def assert_transitions(self):
        for stamp in ("2026-03-08T01:30:00-05:00", "2026-03-08T03:30:00-04:00",
                      "2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00"):
            moment = datetime.fromisoformat(stamp)
            p = dict(post("a"), schedule_time=stamp)
            self.assertEqual(planner.validate_schedule(
                [p], Path("queue") / f"{moment.date()}.json"), [])
        p = dict(post("a"), schedule_time="2026-03-08T02:30:00-05:00")
        self.assertTrue(planner.validate_schedule([p], Path("queue") / "2026-03-08.json"))
        with self.assertRaises(planner.ScheduleError):
            planner.iso_time(date(2026, 3, 8), 150, planner.EASTERN)
        with self.assertRaises(planner.ScheduleError):
            planner.iso_time(date(2026, 11, 1), 90, planner.EASTERN)
        self.assertEqual(planner.iso_time(date(2026, 8, 21), 570, planner.EASTERN),
                         "2026-08-21T09:30:00-04:00")

    def test_transition_instants(self):
        self.assert_transitions()

    def test_windows_fallback_transition_instants(self):
        with patch.object(planner, "ZoneInfo", side_effect=ZoneInfoNotFoundError):
            self.assert_transitions()
            with self.assertRaises(planner.ScheduleError):
                planner.utc_offset(date(2026, 8, 21), "Europe/Berlin")


class PlanFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / ".local" / f"schedule-tests-{uuid.uuid4().hex}"
        self.folder.mkdir(parents=True)
        self.path = self.folder / "2026-08-21.json"
        self.posts = [post("a"), post("b", when="14:00")]
        self.path.write_text(json.dumps(self.posts), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.folder)

    def test_accepted_plan_does_not_move_with_changed_analytics_history(self):
        result = planner.plan_file(self.path, history_days=0)
        before = self.path.read_bytes()
        with patch.object(planner, "read_best_hours", side_effect=AssertionError("must not reread")), \
             patch.object(planner, "recent_schedules", side_effect=AssertionError("must not reread")):
            again = planner.plan_file(self.path)
        self.assertEqual(before, self.path.read_bytes())
        self.assertTrue(again["preserved"])
        self.assertIn("inputs", result["posts"][0]["schedule_plan"])
        self.assertEqual(planner.check_file(self.path), [])

    def test_explicit_reschedule_records_new_target_without_renaming_ids(self):
        planner.plan_file(self.path, history_days=0)
        summary = self.path.with_suffix(".summary.md")
        summary.write_text("| 1 | 09:00 | Instagram |\n| 2 | 14:00 | Instagram |\n",
                           encoding="utf-8")
        with self.assertRaisesRegex(planner.ScheduleError, "reschedule"):
            planner.plan_file(self.path, target_date=date(2026, 8, 22))
        result = planner.plan_file(self.path, history_days=0, target_date=date(2026, 8, 22),
                                   reschedule=True)
        new_path = self.folder / "2026-08-22.json"
        self.assertEqual(result["queue_path"], new_path)
        self.assertEqual(result["previous_queue_path"], self.path)
        self.assertFalse(self.path.exists())
        self.assertFalse(summary.exists())
        self.assertTrue(new_path.with_suffix(".summary.md").is_file())
        self.assertEqual([p["id"] for p in result["posts"]], ["a", "b"])
        self.assertEqual(planner.check_file(new_path), [])
        self.assertEqual(planner.validate_for_publish(result["posts"], new_path,
                         now=datetime(2026, 8, 21, tzinfo=timezone.utc)), [])
        plan = result["posts"][0]["schedule_plan"]
        self.assertEqual(plan["target_date"], "2026-08-22")
        self.assertEqual(plan["source_date"], "2026-08-22")
        self.assertEqual(plan["previous_queue"], "2026-08-21.json")
        self.assertTrue(plan["explicit_reschedule"])
        original = new_path.read_bytes()
        planner.plan_file(new_path)
        self.assertEqual(new_path.read_bytes(), original)

    def test_reschedule_collision_does_not_replace_another_batch(self):
        destination = self.folder / "2026-08-22.json"
        destination.write_text("another batch", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(planner.ScheduleError, "already exists"):
            planner.plan_file(self.path, target_date=date(2026, 8, 22), reschedule=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(destination.read_text(encoding="utf-8"), "another batch")

    def test_reschedule_publication_failure_rolls_back_new_summary(self):
        summary = self.path.with_suffix(".summary.md")
        summary.write_text("Original summary", encoding="utf-8")
        destination = self.folder / "2026-08-22.json"
        before = self.path.read_bytes()
        real_link = planner.os.link

        def fail_queue_link(source, target):
            if target == destination:
                raise OSError("cannot publish new queue")
            return real_link(source, target)

        with patch.object(planner.os, "link", side_effect=fail_queue_link):
            with self.assertRaises(OSError):
                planner.plan_file(self.path, target_date=date(2026, 8, 22),
                                  reschedule=True, history_days=0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(summary.read_text(encoding="utf-8"), "Original summary")
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_suffix(".summary.md").exists())

    def test_reschedule_source_cleanup_failure_restores_originals(self):
        summary = self.path.with_suffix(".summary.md")
        summary.write_bytes(b"Original summary\r\n")
        before = self.path.read_bytes()
        real_unlink = Path.unlink

        def fail_source_unlink(path, *args, **kwargs):
            if path == self.path:
                raise OSError("source locked")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_source_unlink):
            with self.assertRaisesRegex(OSError, "source locked"):
                planner.plan_file(self.path, target_date=date(2026, 8, 22),
                                  reschedule=True, history_days=0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(summary.read_bytes(), b"Original summary\r\n")
        self.assertEqual(set(self.folder.iterdir()), {self.path, summary})

    def test_reschedule_dry_run_and_lane_suffix(self):
        for name in ("lofi-2026-08-21.json", "deallab-2026-08-21-custom.json"):
            self.assertEqual(planner.rescheduled_path(Path(name), date(2026, 8, 22)),
                             Path(name.replace("2026-08-21", "2026-08-22")))
        before = self.path.read_bytes()
        result = planner.plan_file(self.path, target_date=date(2026, 8, 22),
                                   reschedule=True, dry_run=True, history_days=0)
        self.assertEqual(result["queue_path"], self.folder / "2026-08-22.json")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(result["queue_path"].exists())

    def test_metadata_cannot_silently_excuse_date_filename_mismatch(self):
        result = planner.plan_file(self.path, history_days=0)
        result["posts"][0]["schedule_plan"]["target_date"] = "2026-08-22"
        self.assertTrue(planner.validate_schedule(result["posts"], self.path))
        result["posts"][0]["schedule_plan"]["source_date"] = "2026-08-22"
        self.assertTrue(planner.validate_schedule(result["posts"], self.path))

    def test_initial_date_mismatch_does_not_write(self):
        self.posts[0]["schedule_time"] = "2026-08-22T09:00:00-04:00"
        self.path.write_text(json.dumps(self.posts), encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(planner.ScheduleError, "target/filename"):
            planner.plan_file(self.path, history_days=0)
        self.assertEqual(before, self.path.read_bytes())

    def test_new_membership_requires_deliberate_reschedule(self):
        result = planner.plan_file(self.path, history_days=0)
        result["posts"].append(post("c", when="21:00"))
        self.path.write_text(json.dumps(result["posts"]), encoding="utf-8")
        with self.assertRaisesRegex(planner.ScheduleError, "membership"):
            planner.plan_file(self.path)
        result = planner.plan_file(self.path, reschedule=True, history_days=0)
        self.assertEqual(result["posts"][0]["schedule_plan"]["post_ids"], ["a", "b", "c"])

    def test_tightened_config_blocks_without_moving_accepted_plan(self):
        planner.plan_file(self.path, history_days=0)
        before = self.path.read_bytes()
        config = planner.load_config()
        config["layer8culture"]["same_platform_min_gap_minutes"] = 1440
        with patch.object(planner, "load_config", return_value=config):
            with self.assertRaisesRegex(planner.ScheduleError, "gap"):
                planner.plan_file(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_output_never_replaces_queue(self):
        before = self.path.read_bytes()
        with patch.object(planner, "plan_times", return_value=[540, 545]):
            with self.assertRaisesRegex(planner.ScheduleError, "gap"):
                planner.plan_file(self.path, history_days=0)
        self.assertEqual(before, self.path.read_bytes())

    def test_atomic_replace_failure_keeps_original_and_cleans_stage(self):
        before = self.path.read_bytes()
        with patch.object(planner.os, "replace", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                planner.plan_file(self.path, history_days=0)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_cli_explicit_date_reschedule_and_read_only_check(self):
        planned = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "schedule_planner.py"), str(self.path),
             "--reschedule", "--date", "2026-08-22", "--history-days", "0"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(planned.returncode, 0, planned.stderr + planned.stdout)
        new_path = self.folder / "2026-08-22.json"
        self.assertFalse(self.path.exists())
        self.assertIn("Moved", planned.stdout)
        before = new_path.read_bytes()
        checked = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "schedule_planner.py"), str(new_path),
             "--check", "--date", "2026-08-22"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)
        self.assertEqual(before, new_path.read_bytes())
        self.assertEqual(planner.infer_target_date(planner.load_queue(new_path), new_path),
                         date(2026, 8, 22))

    def test_cli_impossible_constraint_failure_is_nonzero_without_write(self):
        config = planner.load_config()
        config["layer8culture"]["same_platform_min_gap_minutes"] = 1440
        config_path = self.folder / "schedule-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        before = self.path.read_bytes()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "schedule_planner.py"), str(self.path),
             "--config", str(config_path), "--history-days", "0"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("impossible", result.stdout)
        self.assertEqual(before, self.path.read_bytes())

    def test_generation_cli_exports_one_target(self):
        output_path = self.folder / "github.env"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generation_date.py"), "--lane", "layer8culture",
             "--event", "schedule", "--run-created-at", "2026-09-05T05:45:00Z",
             "--github-env", str(output_path)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        text = output_path.read_text(encoding="utf-8")
        self.assertIn("BATCH_DATE=2026-09-05\n", text)
        self.assertIn("QUEUE_FILE=queue/2026-09-05.json\n", text)
        self.assertIn("SUMMARY_FILE=queue/2026-09-05.summary.md\n", text)

    def test_dry_run_does_not_write_and_payload_is_preserved(self):
        before = self.path.read_bytes()
        result = planner.plan_file(self.path, history_days=0, dry_run=True)
        self.assertEqual(before, self.path.read_bytes())
        for old, new in zip(self.posts, result["posts"]):
            self.assertEqual({k: v for k, v in new.items() if k not in ("schedule_time", "schedule_plan")},
                             {k: v for k, v in old.items() if k != "schedule_time"})

    def test_summary_updated_with_preserved_content(self):
        summary = self.path.with_suffix(".summary.md")
        summary.write_text("| 1 | 09:00 | Instagram |\n| 2 | 14:00 | Instagram |\n",
                           encoding="utf-8")
        result = planner.plan_file(self.path, history_days=0)
        for index, minute in enumerate(result["times"], 1):
            self.assertIn(f"| {index} | {planner.format_clock(minute)} | Instagram |",
                          summary.read_text(encoding="utf-8"))

    def test_history_ignores_target_other_lanes_and_later_days(self):
        for name, account, stamp in (
            ("2026-08-20.json", "layer8culture", "2026-08-20T09:00:00-04:00"),
            ("2026-08-22.json", "layer8culture", "2026-08-22T09:00:00-04:00"),
            ("lofi-2026-08-20.json", "lofi", "2026-08-20T09:00:00-04:00"),
        ):
            (self.folder / name).write_text(json.dumps(
                [dict(post("x", account=account), schedule_time=stamp)]), encoding="utf-8")
        self.assertEqual(planner.recent_schedules(
            "layer8culture", date(2026, 8, 21), self.path, directories=[self.folder]), [[540]])


class AppRescheduleTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".local" / f"rs-{uuid.uuid4().hex[:12]}"
        (self.root / "queue").mkdir(parents=True)
        (self.root / "config").mkdir()
        (self.root / "config" / "schedule-windows.json").write_text(
            json.dumps(planner.load_config()), encoding="utf-8")
        self.path = self.root / "queue" / "2026-08-21.json"
        self.posts = [post("original-id-a"), post("original-id-b", when="14:00")]
        self.posts[0]["visual"]["image"] = "assets/generated/original-id-a.png"
        self.path.write_text(json.dumps(self.posts, indent=2), encoding="utf-8")
        self.summary = self.path.with_suffix(".summary.md")
        self.summary.write_bytes(b"| 1 | 09:00 | Instagram |\r\n| 2 | 14:00 | Instagram |\r\n")
        self.target = planner.local_datetime(datetime.now(timezone.utc)).date() + timedelta(days=2)
        self.destination = self.path.with_name(f"{self.target}.json")

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_stable_backend_interface_returns_filename_and_archives_exact_original(self):
        original, summary = self.path.read_bytes(), self.summary.read_bytes()
        result = planner.reschedule_queue(self.path, self.target.isoformat(), repo_root=self.root)
        self.assertEqual(result["queue"], self.destination.name)
        self.assertEqual(result["previous_queue"], self.path.name)
        json.dumps(result)
        archived = self.root / result["archive"]
        self.assertEqual((archived / self.path.name).read_bytes(), original)
        self.assertEqual((archived / self.summary.name).read_bytes(), summary)
        manifest = json.loads((archived / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["previous_queue_sha256"], planner.hashlib.sha256(original).hexdigest())
        self.assertEqual(manifest["queue"], self.destination.name)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.summary.exists())
        updated = planner.load_queue(self.destination)
        self.assertEqual([p["id"] for p in updated], [p["id"] for p in self.posts])
        self.assertEqual([p["visual"] for p in updated], [p["visual"] for p in self.posts])
        self.assertTrue(all(p["schedule_time"].startswith(f"{self.target}T") for p in updated))
        self.assertEqual(planner.validate_for_publish(updated, self.destination), [])

    def test_backend_accepts_filename_and_date_object(self):
        result = planner.reschedule_queue(self.path.name, self.target, repo_root=self.root)
        self.assertEqual(result["queue"], self.destination.name)

    def test_explicit_data_config_is_used_for_planning_and_publish_validation(self):
        config = planner.load_config()
        config["layer8culture"].update(
            min_gap_minutes=5, same_platform_min_gap_minutes=5,
            windows=[["09:00", "09:00"]], day_end="09:10",
        )
        (self.root / "config" / "schedule-windows.json").write_text(json.dumps(config), encoding="utf-8")
        result = planner.reschedule_queue(self.path, self.target, repo_root=self.root)
        posts = planner.load_queue(self.destination)
        moments = [datetime.fromisoformat(p["schedule_time"]) for p in posts]
        self.assertEqual(moments[1] - moments[0], timedelta(minutes=5))
        self.assertEqual(planner.validate_for_publish(posts, self.destination), [])
        self.assertEqual(result["queue"], self.destination.name)

    def test_current_code_cli_resolves_data_config_digest_and_history_from_environment(self):
        config = planner.load_config()
        config["layer8culture"].update(
            min_gap_minutes=5, same_platform_min_gap_minutes=5,
            windows=[["09:00", "09:00"]], day_end="09:10",
        )
        (self.root / "config" / "schedule-windows.json").write_text(json.dumps(config), encoding="utf-8")
        (self.root / "analytics").mkdir()
        (self.root / "analytics" / "insights-digest.md").write_text(
            "**Best posting hours:** 09:00 (4 top posts)", encoding="utf-8")
        earlier = dict(post("previous"), schedule_time="2026-08-20T09:00:00-04:00")
        (self.root / "queue" / "2026-08-20.json").write_text(json.dumps([earlier]), encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "schedule_planner.py").write_text(
            "raise RuntimeError('stale data-root script must not execute')", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "schedule_planner.py"),
             str(Path("queue") / self.path.name)],
            cwd=ROOT, env={**os.environ, "LAYER8_DATA_ROOT": str(self.root)},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        posts = planner.load_queue(self.path)
        inputs = posts[0]["schedule_plan"]["inputs"]
        self.assertEqual(inputs["config"], config["layer8culture"])
        self.assertEqual(inputs["best_hours"], [540])
        self.assertEqual(inputs["history"], [[540]])
        self.assertEqual([p["schedule_time"][11:16] for p in posts], ["09:00", "09:05"])

    def test_backend_preserves_original_and_has_no_archive_when_infeasible(self):
        config = planner.load_config()
        config["layer8culture"]["same_platform_min_gap_minutes"] = 1440
        (self.root / "config" / "schedule-windows.json").write_text(json.dumps(config), encoding="utf-8")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(planner.ScheduleError, "impossible"):
            planner.reschedule_queue(self.path, self.target, repo_root=self.root)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.root / ".local" / "reschedules").exists())

    def test_backend_does_not_overwrite_another_batch(self):
        self.destination.write_text("other batch", encoding="utf-8")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(planner.ScheduleError, "already exists"):
            planner.reschedule_queue(self.path, self.target, repo_root=self.root)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.destination.read_text(encoding="utf-8"), "other batch")

    def test_backend_archive_failure_prevents_queue_move(self):
        original = self.path.read_bytes()
        real_atomic = planner.atomic_text

        def fail_archive(path, *args, **kwargs):
            if path.name == "manifest.json":
                raise OSError("archive failed")
            return real_atomic(path, *args, **kwargs)

        with patch.object(planner, "atomic_text", side_effect=fail_archive):
            with self.assertRaisesRegex(OSError, "archive failed"):
                planner.reschedule_queue(self.path, self.target, repo_root=self.root)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.destination.exists())

    def test_backend_rejects_stale_dates_and_invalid_paths_without_mutation(self):
        original = self.path.read_bytes()
        with self.assertRaisesRegex(planner.ScheduleError, "lead time"):
            planner.reschedule_queue(self.path, self.target - timedelta(days=3), repo_root=self.root)
        with self.assertRaisesRegex(planner.ScheduleError, "YYYY-MM-DD"):
            planner.reschedule_queue(self.path, "20260907", repo_root=self.root)
        with self.assertRaisesRegex(planner.ScheduleError, "inside"):
            planner.reschedule_queue(self.root / self.path.name, self.target, repo_root=self.root)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.root / ".local" / "reschedules").exists())


class GenerationDateTests(unittest.TestCase):
    def resolve(self, lane, created, event="schedule", override=None):
        return generation_date.resolve_target_date(
            lane, event=event, run_created_at=datetime.fromisoformat(created), override=override)

    def test_delayed_run_after_eastern_midnight_keeps_nominal_target(self):
        for lane in ("layer8culture", "lofi"):
            for instant in ("2026-09-05T02:00:00+00:00", "2026-09-05T04:55:00+00:00",
                            "2026-09-05T22:00:00+00:00"):
                self.assertEqual(self.resolve(lane, instant), date(2026, 9, 5))

    def test_run_queued_before_midnight_uses_original_created_timestamp(self):
        self.assertEqual(self.resolve("layer8culture", "2026-09-06T01:30:00+00:00"),
                         date(2026, 9, 5))

    def test_manual_lane_defaults_preserved(self):
        created = "2026-09-05T02:00:00+00:00"
        self.assertEqual(self.resolve("layer8culture", created, "workflow_dispatch"), date(2026, 9, 5))
        self.assertEqual(self.resolve("lofi", created, "workflow_dispatch"), date(2026, 9, 5))
        self.assertEqual(self.resolve("deallab", created, "workflow_dispatch"), date(2026, 9, 4))
        self.assertEqual(self.resolve("layer8culture", "2026-09-05T05:00:00+00:00",
                                     "workflow_dispatch"), date(2026, 9, 6))

    def test_manual_date_override(self):
        for lane in generation_date.PREFIXES:
            self.assertEqual(self.resolve(lane, "2026-09-05T02:00:00+00:00",
                                         "workflow_dispatch", date(2026, 9, 10)), date(2026, 9, 10))

    def test_dst_and_year_boundaries(self):
        for stamp, expected in (("2026-03-08T05:55:00+00:00", date(2026, 3, 8)),
                                ("2026-11-01T06:55:00+00:00", date(2026, 11, 1)),
                                ("2027-01-01T06:55:00+00:00", date(2027, 1, 1))):
            self.assertEqual(self.resolve("layer8culture", stamp), expected)

    def test_missing_metadata_or_unsupported_cron_fails_closed(self):
        with self.assertRaises(planner.ScheduleError):
            self.resolve("lofi", "2026-09-05T02:00:00")
        with self.assertRaises(planner.ScheduleError):
            generation_date.resolve_target_date("lofi", event="schedule",
                run_created_at=datetime(2026, 9, 5, tzinfo=timezone.utc), cron="*/5 * * * *")

    def test_run_values_share_one_date_for_all_outputs(self):
        values = generation_date.run_values("lofi", date(2026, 9, 5))
        self.assertEqual(values["BATCH_DATE"], "2026-09-05")
        self.assertEqual(values["QUEUE_FILE"], "queue/lofi-2026-09-05.json")
        self.assertIn("overrides every relative", values["GENERATION_TARGET_INSTRUCTION"])

    def test_workflows_use_one_target_and_gate_before_pr(self):
        for name in ("generate-content.yml", "generate-lofi.yml", "generate-deallab.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertEqual(text.count("python scripts/generation_date.py"), 1)
            self.assertNotIn("$(date", text)
            self.assertNotIn("QUEUE_FILE=$(ls", text)
            self.assertIn("inputs:\n      date:", text)
            self.assertLess(text.index("Assign and validate posting schedule"),
                            text.index("Generate visuals"))
            self.assertLess(text.index("Gate approval PR"), text.index("- name: Open approval PR"))
            self.assertIn('python scripts/batch_readiness.py "$QUEUE_FILE"', text)
            self.assertEqual(text.count('copilot -p "'), text.count('"$GENERATION_TARGET_INSTRUCTION"'))
        main = (ROOT / ".github" / "workflows" / "generate-content.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/recover_copilot_queue.py", main)
        self.assertIn("FIRST_COPILOT_OUTCOME", main)
        self.assertIn('python scripts/queue_json_guard.py "$QUEUE_FILE" --repair', main)
        deallab = (ROOT / ".github" / "workflows" / "generate-deallab.yml").read_text(encoding="utf-8")
        self.assertNotIn("  schedule:", deallab)


if __name__ == "__main__":
    unittest.main()
