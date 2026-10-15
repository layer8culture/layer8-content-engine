from datetime import date, datetime

from studio_lib import config, hero_report

D = date(2026, 10, 15)


def _plan(when="2026-10-15T08:31:00-04:00"):
    return {
        "posts": [{
            "id": "p1", "brand": "layer8culture", "format": "single",
            "schedule": {"instagram": when},
        }]
    }


def _setup(monkeypatch, *, ready=True, gate=True, hero=True, row=None, paused=False):
    monkeypatch.setattr(
        hero_report.render, "load_manifest",
        lambda d: {"p1": {"hero": hero, "files": ["p1.png"], "media_revision": "rev-b"}},
    )
    monkeypatch.setattr(hero_report.gates, "load", lambda d: {"p1": {"pass": gate, "errors": [] if gate else ["bad"]}})
    monkeypatch.setattr(hero_report.heroes, "status", lambda p, d: [{"post": "p1", "ready": ready}])
    monkeypatch.setattr(hero_report.publish, "last_rows", lambda: {("p1", "instagram"): row} if row else {})
    monkeypatch.setattr(config, "is_paused", lambda cfg: paused)


def _report(monkeypatch, cfg, *, now, live=None, **state):
    _setup(monkeypatch, **state)
    lines, verified = hero_report.build_report(
        _plan(), D, cfg, pack_total=1, matched=1, ingested=1, skipped=0,
        now=now.replace(tzinfo=config.tz(cfg)), live_states=live,
    )
    return "\n".join(lines), verified


def test_tomorrow_evening_waits_for_next_day_6am_publish_check(cfg, monkeypatch):
    text, verified = _report(monkeypatch, cfg, now=datetime(2026, 10, 14, 21, 10))
    assert not verified
    assert "LOCAL MEDIA READY; WAITING FOR PUBLISH" in text
    assert "Thu Oct 15 at 06:00 AM ET" in text
    assert "conditional on the laptop being on" in text


def test_today_uses_next_half_hour_publish_check(cfg, monkeypatch):
    text, _ = _report(monkeypatch, cfg, now=datetime(2026, 10, 15, 8, 10))
    assert "Thu Oct 15 at 08:30 AM ET" in text


def test_already_hero_queued_is_verified_only_with_live_postiz_id(cfg, monkeypatch):
    row = {"status": "scheduled", "hero": True, "media_revision": "rev-b", "postiz": [{"postId": "new"}]}
    text, verified = _report(
        monkeypatch, cfg, now=datetime(2026, 10, 14, 21, 10),
        row=row, live={"new": "QUEUE"},
    )
    assert verified
    assert "POSTIZ VERIFIED UPDATED" in text
    assert "1/1 routes use hero media" in text


def test_local_manifest_alone_never_claims_postiz_updated(cfg, monkeypatch):
    row = {"status": "scheduled", "hero": True, "media_revision": "rev-b", "postiz": [{"postId": "new"}]}
    text, verified = _report(
        monkeypatch, cfg, now=datetime(2026, 10, 14, 21, 10),
        row=row, live=None,
    )
    assert not verified
    assert "POSTIZ VERIFICATION UNAVAILABLE" in text


def test_missing_gate_failure_and_pause_are_explicit(cfg, monkeypatch):
    missing, _ = _report(monkeypatch, cfg, now=datetime(2026, 10, 14, 21, 10), ready=False, hero=False)
    assert "MISSING HERO" in missing and "ACTION REQUIRED" in missing
    gate, _ = _report(monkeypatch, cfg, now=datetime(2026, 10, 14, 21, 10), gate=False)
    assert "BLOCKED BY GATES" in gate
    paused, _ = _report(monkeypatch, cfg, now=datetime(2026, 10, 14, 21, 10), paused=True)
    assert "PAUSED" in paused and "Existing Postiz queue entries remain scheduled" in paused


def test_hero_revision_a_is_not_verified_after_revision_b_render(cfg, monkeypatch):
    row = {"status": "scheduled", "hero": True, "media_revision": "rev-a", "postiz": [{"postId": "old"}]}
    text, verified = _report(
        monkeypatch, cfg, now=datetime(2026, 10, 14, 21, 10),
        row=row, live={"old": "QUEUE"},
    )
    assert not verified
    assert "WAITING FOR PUBLISH" in text


def test_expired_and_near_cutoff_template_routes_are_not_promised(cfg, monkeypatch):
    row = {"status": "scheduled", "hero": False, "postiz": [{"postId": "old"}]}
    expired, _ = _report(
        monkeypatch, cfg, now=datetime(2026, 10, 15, 9, 0), row=row, live={"old": "PUBLISHED"},
    )
    assert "TOO LATE: TEMPLATE ROUTE ALREADY DUE" in expired
    near, _ = _report(
        monkeypatch, cfg, now=datetime(2026, 10, 15, 8, 25), row=row, live={"old": "QUEUE"},
    )
    assert "TOO CLOSE TO REPLACE" in near
