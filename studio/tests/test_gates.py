from datetime import date

from studio_lib import context, gates
from studio_lib import publish

D = date(2026, 12, 30)


def errs(post, cfg):
    return gates.text_checks(post, cfg)[0]


def test_fixture_passes_text_gates(plan, cfg):
    res = gates.run_gates(plan, D, cfg, manifest=None)
    assert all(r["pass"] for r in res.values()), res


def test_banned_hype_phrase(post, cfg):
    post["caption"] += " This is a total game changer."
    assert any("game changer" in e for e in errs(post, cfg))


def test_banned_phrase_in_slide(post, cfg):
    post["slides"][1]["body"] = "Unleash your workflow."
    assert any("unleash" in e for e in errs(post, cfg))


def test_banned_word_boundary(post, cfg):
    post["caption"] += " Insanely calm insight."  # 'insane' must not match 'insanely'
    assert not any("insane" in e for e in errs(post, cfg))


def test_exclamations_and_emojis(post, cfg):
    post["caption"] += " Wow! Yes!"
    post["caption"] += " 🚀🔥💯🎉"
    e = errs(post, cfg)
    assert any("exclamation" in x for x in e)
    assert any("emoji" in x for x in e)


def test_hashtag_limits(post, cfg):
    post["hashtags"] = [f"#Tag{i}" for i in range(12)]
    assert any("hashtags >" in e for e in errs(post, cfg))
    post["hashtags"] = ["#A1", "#a1"]
    assert any("duplicate hashtags" in e for e in errs(post, cfg))


def test_hashtags_not_in_caption(post, cfg):
    post["caption"] += " #AI"
    assert any("inside the caption" in e for e in errs(post, cfg))


def test_placeholder_text(post, cfg):
    post["slides"][0]["body"] = "Join us at [TIME] for the show."
    assert any("placeholder" in e for e in errs(post, cfg))


def test_link_allowlist(post, cfg):
    post["caption"] += " Read more at https://sketchy.example.net/x"
    assert any("non-allowed domain" in e for e in errs(post, cfg))
    post["caption"] = post["hook"] + " Details at layer8culture.io/guide. Save this."
    assert not any("domain" in e for e in errs(post, cfg))


def test_unconfigured_show_time(post, cfg):
    cfg["facts"]["tech_thursday_time"] = ""
    post["caption"] += " Tune in at 8 PM."
    assert any("states a time" in e for e in errs(post, cfg))
    cfg["facts"]["tech_thursday_time"] = "Thursdays 8 PM ET"
    assert not any("states a time" in e for e in errs(post, cfg))


def test_radio_stays_off_ai_topics(plan, cfg):
    radio = plan["posts"][3]
    radio["caption"] += " Bonus: my favorite ChatGPT prompt."
    assert any("radio must stay" in e for e in errs(radio, cfg))


def test_radio_live_claim_needs_fact(plan, cfg):
    radio = plan["posts"][3]
    cfg["facts"]["radio_stream_is_live_24_7"] = False
    radio["caption"] += " Live now, 24/7."
    assert any("24/7" in e for e in errs(radio, cfg))


def test_reel_beat_limits(plan, cfg):
    reel = plan["posts"][1]
    reel["reel"]["beats"][0]["text"] = "this beat is far too long to read"
    assert any("beat 1" in e for e in errs(reel, cfg))


def test_duplicate_vs_history(plan, cfg, monkeypatch):
    rows = [{"date": "2026-12-20", "brand": "layer8culture", "topic": "redact customer data before you prompt", "hook": ""}]
    monkeypatch.setattr(context, "history", lambda days, before: rows)
    dups = gates.duplicate_checks(plan, D, cfg)
    assert "20261230-l8-01" in dups


def test_duplicate_within_plan(plan, cfg, monkeypatch):
    monkeypatch.setattr(context, "history", lambda days, before: [])
    plan["posts"][2]["topic"] = plan["posts"][0]["topic"]
    plan["posts"][2]["hook"] = plan["posts"][0]["hook"]
    assert "20261230-l8-03" in gates.duplicate_checks(plan, D, cfg)


def test_media_gate_flags_overflow_and_missing(plan, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(gates, "media_dir", lambda d: tmp_path)
    post = plan["posts"][3]
    manifest = {post["id"]: {"files": ["20261230-radio-01.png"], "fit": [{"slide": 1, "k": 0.62, "overflow": True, "overlap": False}]}}
    e, _ = gates.media_checks(post, D, manifest, cfg)
    assert any("missing media" in x for x in e)
    assert any("does not fit" in x for x in e)
    assert gates.media_checks(post, D, {}, cfg)[0] == ["not rendered"]


def test_media_gate_checks_dimensions(plan, cfg, tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(gates, "media_dir", lambda d: tmp_path)
    post = plan["posts"][3]
    Image.new("RGB", (1080, 1080)).save(tmp_path / "20261230-radio-01.png")
    manifest = {post["id"]: {"files": ["20261230-radio-01.png"], "fit": [{"slide": 1, "k": 1, "overflow": False, "overlap": False}]}}
    assert any("expected 1080x1350" in x for x in gates.media_checks(post, D, manifest, cfg)[0])


def test_publish_respects_gates_and_channels(plan, cfg, monkeypatch, tmp_path):
    from datetime import datetime

    from studio_lib.schedule import assign_schedule

    monkeypatch.setattr(publish, "POSTED_LOG", tmp_path / "posted.jsonl")
    monkeypatch.setattr(context, "read_jsonl", lambda p: [])
    logged = []
    monkeypatch.setattr(publish, "_log", logged.append)
    assign_schedule(plan, cfg["schedule_windows"], cfg["timezone"])
    post = plan["posts"][0]
    when = datetime.fromisoformat(post["schedule"]["instagram"])
    results = {p["id"]: {"pass": p["id"] != post["id"], "errors": ["banned phrase"]} for p in plan["posts"]}
    acts = publish.publish_due(plan, D, cfg, {}, results, now=when, dry=True, log=lambda *_: None)
    blocked = [a for a in acts if a["id"] == post["id"]]
    assert blocked and blocked[0]["status"] == "blocked"


def test_dry_publish_never_writes_log(plan, cfg, monkeypatch):
    from datetime import datetime, timedelta

    from studio_lib.schedule import assign_schedule

    monkeypatch.setattr(context, "read_jsonl", lambda p: [])
    logged = []
    monkeypatch.setattr(publish, "_log", logged.append)
    assign_schedule(plan, cfg["schedule_windows"], cfg["timezone"])
    late = datetime.fromisoformat(plan["posts"][0]["schedule"]["instagram"]) + timedelta(hours=8)
    acts = publish.publish_due(plan, D, cfg, {}, {}, now=late, dry=True, log=lambda *_: None)
    assert acts and not logged


def test_only_and_ahead(plan, cfg, monkeypatch):
    from datetime import datetime, timedelta

    from studio_lib.schedule import assign_schedule

    monkeypatch.setattr(context, "read_jsonl", lambda p: [])
    monkeypatch.setattr(publish, "_log", lambda r: None)
    assign_schedule(plan, cfg["schedule_windows"], cfg["timezone"])
    early = min(datetime.fromisoformat(t) for p in plan["posts"] for t in p["schedule"].values()) - timedelta(hours=5)
    q = lambda **kw: publish.publish_due(plan, D, cfg, {}, {}, now=early, dry=True, log=lambda *_: None, **kw)
    assert q() == []
    assert {a["id"] for a in q(ahead=True)} == {p["id"] for p in plan["posts"]}
    assert {a["id"] for a in q(ahead=True, only={"20261230-l8-03"})} == {"20261230-l8-03"}


def test_pause_switch(cfg, tmp_path, monkeypatch):
    from studio_lib import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    cfg["paused"] = False
    assert not config.is_paused(cfg)
    (tmp_path / "PAUSE").write_text("")
    assert config.is_paused(cfg)
    cfg["paused"] = True
    (tmp_path / "PAUSE").unlink()
    assert config.is_paused(cfg)

class _FakePostiz:
    def __init__(self, fail_delete=False):
        self.calls, self.fail_delete = [], fail_delete

    def __call__(self, url, key):
        return self

    def upload(self, path):
        self.calls.append(("upload", path.name))
        return {"id": "u", "path": "p"}

    def delete(self, pid):
        self.calls.append(("delete", pid))
        if self.fail_delete:
            raise publish.PostizError("nope")

    def schedule(self, integration, when, content, media, settings):
        self.calls.append(("schedule", when))
        return [{"postId": "new1", "integration": integration}]


def _replace_setup(plan, cfg, monkeypatch, tmp_path, fake, prev_hero=False):
    from datetime import datetime, timedelta

    from studio_lib.schedule import assign_schedule

    assign_schedule(plan, cfg["schedule_windows"], cfg["timezone"])
    post = next(p for p in plan["posts"] if p["brand"] == "layer8culture" and p["format"] in ("single", "carousel"))
    when = post["schedule"]["instagram"]
    rows = [{"at": "x", "id": p["id"], "platform": pl, "status": "scheduled", "hero": prev_hero if p is post else False,
             "postiz": [{"postId": f"old-{p['id']}-{pl}"}]} for p in plan["posts"] for pl in p["schedule"]]
    monkeypatch.setattr(context, "read_jsonl", lambda p: rows)
    logged = []
    monkeypatch.setattr(publish, "_log", logged.append)
    monkeypatch.setattr(publish, "Postiz", fake)
    monkeypatch.setattr(publish, "media_dir", lambda d: tmp_path)
    manifest = {}
    for p in plan["posts"]:
        (tmp_path / f"{p['id']}.png").write_bytes(b"x")
        manifest[p["id"]] = {"files": [f"{p['id']}.png"], "hero": p is post}
    results = {p["id"]: {"pass": True, "errors": []} for p in plan["posts"]}
    now = datetime.fromisoformat(when) - timedelta(hours=3)
    return post, manifest, results, now, logged


def test_replace_swaps_only_hero_upgraded_posts(plan, cfg, monkeypatch, tmp_path):
    fake = _FakePostiz()
    post, manifest, results, now, logged = _replace_setup(plan, cfg, monkeypatch, tmp_path, fake)
    acts = publish.publish_due(plan, D, cfg, manifest, results, now=now, log=lambda *_: None, ahead=True)
    assert [a["id"] for a in acts] == [post["id"]]
    assert [c[0] for c in fake.calls] == ["upload", "delete", "schedule"]
    assert fake.calls[1][1] == f"old-{post['id']}-instagram"
    assert logged[0]["status"] == "scheduled" and logged[0]["replaces"] == [f"old-{post['id']}-instagram"]
    assert logged[0]["hero"] is True


def test_replace_noop_when_already_hero_or_too_close(plan, cfg, monkeypatch, tmp_path):
    from datetime import datetime, timedelta

    fake = _FakePostiz()
    post, manifest, results, now, logged = _replace_setup(plan, cfg, monkeypatch, tmp_path, fake, prev_hero=True)
    assert publish.publish_due(plan, D, cfg, manifest, results, now=now, log=lambda *_: None, ahead=True) == []
    fake2 = _FakePostiz()
    post, manifest, results, now, logged = _replace_setup(plan, cfg, monkeypatch, tmp_path, fake2)
    close = datetime.fromisoformat(post["schedule"]["instagram"]) - timedelta(minutes=5)
    assert publish.publish_due(plan, D, cfg, manifest, results, now=close, log=lambda *_: None, ahead=True,
                               only={post["id"]}) == []
    assert fake.calls == [] and fake2.calls == [] and logged == []


def test_replace_keeps_old_post_if_delete_fails(plan, cfg, monkeypatch, tmp_path):
    fake = _FakePostiz(fail_delete=True)
    post, manifest, results, now, logged = _replace_setup(plan, cfg, monkeypatch, tmp_path, fake)
    acts = publish.publish_due(plan, D, cfg, manifest, results, now=now, log=lambda *_: None, ahead=True)
    assert acts == [] and logged == []
    assert [c[0] for c in fake.calls] == ["upload", "delete"]


def test_force_replace_resends_selected(plan, cfg, monkeypatch, tmp_path):
    fake = _FakePostiz()
    post, manifest, results, now, logged = _replace_setup(plan, cfg, monkeypatch, tmp_path, fake, prev_hero=True)
    other = next(p for p in plan["posts"] if p is not post and p["format"] != "reel")
    acts = publish.publish_due(plan, D, cfg, manifest, results, now=now,
                               log=lambda *_: None, force_replace=True, only={other["id"]})
    assert {a["id"] for a in acts} == {other["id"]} and all(a["status"] == "scheduled" for a in acts)
