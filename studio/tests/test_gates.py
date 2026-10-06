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
