from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from studio_lib import planner
from studio_lib.schedule import assign_schedule, due

NY = ZoneInfo("America/New_York")


def test_every_platform_gets_a_time_inside_the_day(plan, cfg):
    assign_schedule(plan, cfg["schedule_windows"], cfg["timezone"])
    for p in plan["posts"]:
        assert set(p["schedule"]) == set(p["platforms"])
        for iso in p["schedule"].values():
            t = datetime.fromisoformat(iso)
            assert t.date() == date(2026, 12, 30)
            assert 6 <= t.hour <= 23


def test_schedule_is_deterministic(plan, cfg):
    import copy

    a = assign_schedule(copy.deepcopy(plan), cfg["schedule_windows"], cfg["timezone"])
    b = assign_schedule(copy.deepcopy(plan), cfg["schedule_windows"], cfg["timezone"])
    assert [p["schedule"] for p in a["posts"]] == [p["schedule"] for p in b["posts"]]


def test_due_states():
    t = datetime(2026, 12, 30, 12, 0, tzinfo=NY)
    iso = t.isoformat()
    assert due(iso, t - timedelta(minutes=120), 50, 180) == "future"
    assert due(iso, t - timedelta(minutes=30), 50, 180) == "due"
    assert due(iso, t + timedelta(minutes=60), 50, 180) == "due"
    assert due(iso, t + timedelta(minutes=200), 50, 180) == "missed"


def test_slots_follow_cadence(cfg, monkeypatch):
    monkeypatch.setattr(planner, "available_loop_clips", lambda: [])
    wed = planner.build_slots(date(2026, 10, 7), cfg)
    thu = planner.build_slots(date(2026, 10, 8), cfg)
    fri = planner.build_slots(date(2026, 10, 9), cfg)
    assert any("Tech Thursday Promos" in s["note"] for s in wed)
    assert any(s["format"] == "story" and "live today" in s["note"] for s in thu)
    assert any("Livestream Clips" in s["note"] for s in fri)
    for slots in (wed, thu, fri):
        assert sum(1 for s in slots if "youtube" in s["platforms"]) == cfg["cadence"]["layer8culture"]["youtube_shorts"]
        assert sum(1 for s in slots if s["brand"] == "radio") == cfg["cadence"]["radio"]["instagram_posts"]
        assert len({s["id"] for s in slots}) == len(slots)


def test_radio_loop_reel_when_clip_available(cfg, monkeypatch):
    monkeypatch.setattr(planner, "available_loop_clips", lambda: ["radio-rain.mp4"])
    slots = planner.build_slots(date(2026, 10, 7), cfg)
    reel = [s for s in slots if s["brand"] == "radio" and s["format"] == "reel"]
    assert reel and "radio-rain.mp4" in reel[0]["note"]
