from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

import studio
from studio_lib import context, gates, heroes, herodesk, publish, render
from studio_lib import locks
from studio_lib.locks import FileLock, LockBusy


D = date(2026, 12, 30)


class FakePostiz:
    def __init__(self, *, delete_mode="ok", create_mode="ok", old_state="QUEUE"):
        self.delete_mode = delete_mode
        self.create_mode = create_mode
        self.calls = []
        self.posts = {}
        self.old_state = old_state

    def __call__(self, _url, _key):
        return self

    def seed(self, post_id, integration, when):
        self.posts[post_id] = {
            "id": post_id, "state": self.old_state, "publishDate": when,
            "integration": {"id": integration},
        }

    def upload(self, path):
        self.calls.append(("upload", path.name))
        return {"id": "upload-1", "path": "media"}

    def delete(self, post_id):
        self.calls.append(("delete", post_id))
        if self.delete_mode == "removed-timeout":
            self.posts.pop(post_id, None)
            raise publish.PostizError("timeout")
        if self.delete_mode == "kept-timeout":
            raise publish.PostizError("timeout")
        self.posts.pop(post_id, None)

    def schedule(self, integration, when, _content, _media, _settings):
        self.calls.append(("schedule", when))
        self.posts["new-1"] = {
            "id": "new-1", "state": "QUEUE", "publishDate": when,
            "integration": {"id": integration},
        }
        if self.create_mode == "created-timeout":
            raise publish.PostizError("timeout")
        if self.create_mode == "missing-timeout":
            self.posts.pop("new-1", None)
            raise publish.PostizError("timeout")
        return [{"postId": "new-1"}]

    def list_posts(self, _start, _end):
        return list(self.posts.values())


def _route_fixture(plan, cfg, monkeypatch, tmp_path, fake, *, media_revision="rev-b"):
    from studio_lib.schedule import assign_schedule

    assign_schedule(plan, cfg["schedule_windows"], cfg["timezone"])
    post = next(item for item in plan["posts"] if item["format"] != "reel")
    plan = {**plan, "posts": [post]}
    when = datetime.fromisoformat(post["schedule"]["instagram"]).astimezone(timezone.utc)
    when_api = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    integration = publish.channel_id(cfg, "layer8culture", "instagram")
    fake.seed("old-1", integration, when_api)
    rows = [{
        "id": post["id"], "platform": "instagram", "status": "scheduled",
        "hero": False, "media_revision": "rev-a", "postiz": [{"postId": "old-1"}],
    }]
    monkeypatch.setattr(context, "read_jsonl", lambda _path: rows)
    logged = []
    monkeypatch.setattr(publish, "_log", lambda row: (rows.append(row), logged.append(row)))
    monkeypatch.setattr(publish, "media_dir", lambda _d: tmp_path)
    (tmp_path / f"{post['id']}.png").write_bytes(b"new")
    manifest = {
        post["id"]: {
            "files": [f"{post['id']}.png"], "hero": True,
            "hero_revision": "hero-b", "media_revision": media_revision,
        }
    }
    gates = {post["id"]: {"pass": True, "errors": []}}
    now = datetime.fromisoformat(post["schedule"]["instagram"]) - timedelta(hours=3)
    return plan, post, manifest, gates, now, rows, logged


def test_delete_timeout_with_old_route_live_stops_for_attention(plan, cfg, monkeypatch, tmp_path):
    fake = FakePostiz(delete_mode="kept-timeout")
    plan, post, manifest, gates, now, _rows, logged = _route_fixture(
        plan, cfg, monkeypatch, tmp_path, fake
    )
    actions = publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake,
    )
    assert actions == []
    assert logged == []
    assert [call[0] for call in fake.calls] == ["upload", "delete"]
    journal = json.loads(next((tmp_path / ".operations").glob("*.json")).read_text())
    assert journal["phase"] == "needs_attention"


def test_delete_timeout_reconciles_removed_route_without_duplicate(plan, cfg, monkeypatch, tmp_path):
    fake = FakePostiz(delete_mode="removed-timeout")
    plan, post, manifest, gates, now, _rows, logged = _route_fixture(
        plan, cfg, monkeypatch, tmp_path, fake
    )
    actions = publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake,
    )
    assert actions[0]["status"] == "scheduled"
    assert [call[0] for call in fake.calls].count("schedule") == 1
    assert logged[-1]["media_revision"] == "rev-b"


def test_create_timeout_reconciles_live_new_route(plan, cfg, monkeypatch, tmp_path):
    fake = FakePostiz(create_mode="created-timeout")
    plan, _post, manifest, gates, now, _rows, logged = _route_fixture(
        plan, cfg, monkeypatch, tmp_path, fake
    )
    actions = publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake,
    )
    assert actions[0]["status"] == "scheduled"
    assert logged[-1]["postiz"] == [{"postId": "new-1"}]
    assert [call[0] for call in fake.calls].count("schedule") == 1


def test_create_timeout_without_live_route_requires_attention(plan, cfg, monkeypatch, tmp_path):
    fake = FakePostiz(create_mode="missing-timeout")
    plan, _post, manifest, gates, now, _rows, logged = _route_fixture(
        plan, cfg, monkeypatch, tmp_path, fake
    )
    assert publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake,
    ) == []
    assert logged == []
    journal = json.loads(next((tmp_path / ".operations").glob("*.json")).read_text())
    assert journal["phase"] == "needs_attention"
    assert [call[0] for call in fake.calls].count("schedule") == 1


def test_crash_before_ledger_recovers_without_second_create(plan, cfg, monkeypatch, tmp_path):
    fake = FakePostiz()
    plan, _post, manifest, gates, now, rows, logged = _route_fixture(
        plan, cfg, monkeypatch, tmp_path, fake
    )

    def crash(phase, _operation):
        if phase == "before_ledger":
            raise RuntimeError("injected crash")

    first = publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake, fault=crash,
    )
    assert first[0]["status"] == "failed"
    second = publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake,
    )
    assert second[0]["status"] == "scheduled"
    assert [call[0] for call in fake.calls].count("schedule") == 1
    assert logged[-1]["media_revision"] == "rev-b"


@pytest.mark.parametrize("crash_phase", ["after_upload", "after_delete", "after_create"])
def test_route_journal_resumes_each_interrupted_phase_without_duplicate_post(
        crash_phase, plan, cfg, monkeypatch, tmp_path):
    fake = FakePostiz()
    plan, _post, manifest, gates, now, _rows, _logged = _route_fixture(
        plan, cfg, monkeypatch, tmp_path, fake
    )

    def crash(phase, _operation):
        if phase == crash_phase:
            raise RuntimeError(f"injected {phase} crash")

    first = publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake, fault=crash,
    )
    assert first[0]["status"] == "failed"
    second = publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake,
    )
    assert second[0]["status"] == "scheduled"
    assert [call[0] for call in fake.calls].count("schedule") == 1


def test_actual_cutoff_is_rechecked_after_upload(plan, cfg, monkeypatch, tmp_path):
    fake = FakePostiz()
    plan, post, manifest, gates, now, _rows, _logged = _route_fixture(
        plan, cfg, monkeypatch, tmp_path, fake
    )
    route_time = datetime.fromisoformat(post["schedule"]["instagram"])
    actions = publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake,
        clock=lambda: route_time.astimezone(timezone.utc) - timedelta(minutes=5),
    )
    assert actions == []
    assert [call[0] for call in fake.calls] == ["upload"]
    assert "old-1" in fake.posts


def test_published_route_is_never_deleted(plan, cfg, monkeypatch, tmp_path):
    fake = FakePostiz(old_state="PUBLISHED")
    plan, _post, manifest, gates, now, _rows, _logged = _route_fixture(
        plan, cfg, monkeypatch, tmp_path, fake
    )
    assert publish.publish_due(
        plan, D, cfg, manifest, gates, now=now, ahead=True, client_factory=fake,
    ) == []
    assert [call[0] for call in fake.calls] == ["upload"]
    assert "old-1" in fake.posts


def test_owner_checked_lock_does_not_remove_replacement_owner(tmp_path, monkeypatch):
    path = tmp_path / "job.lock"
    first = FileLock.acquire(path, "first")
    path.write_text('{"pid":99999999,"token":"replacement","created_at":0}', encoding="utf-8")
    monkeypatch.setattr(locks, "process_exists", lambda _pid: False)
    second = FileLock.acquire(path, "second")
    first.release()
    assert path.exists()
    second.release()
    assert not path.exists()


def test_media_lock_rejects_overlapping_renderer(plan, cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(render, "media_dir", lambda _d: tmp_path)
    entered = threading.Event()
    release = threading.Event()
    one_post = next(post for post in plan["posts"] if post["format"] != "reel")
    one_plan = {**plan, "posts": [one_post]}

    def slow(posts, _d, _cfg, log=print):
        entered.set()
        release.wait(2)
        return {
            post["id"]: {"files": [], "fit": [], "hero": False}
            for post in posts
        }

    monkeypatch.setattr(render, "render_stills", slow)
    errors = []
    thread = threading.Thread(
        target=lambda: render.render_plan(one_plan, D, cfg, log=lambda *_: None),
        daemon=True,
    )
    thread.start()
    assert entered.wait(1)
    with pytest.raises(LockBusy):
        render.render_plan(one_plan, D, cfg, log=lambda *_: None)
    release.set()
    thread.join(2)
    assert not errors


def test_recent_download_import_is_opt_in(tmp_path):
    old = tmp_path / "old.png"
    recent = tmp_path / "recent.png"
    old.write_bytes(b"old")
    recent.write_bytes(b"recent")
    old_time = time.time() - 3600
    recent_time = time.time() - 60
    import os

    os.utime(old, (old_time, old_time))
    os.utime(recent, (recent_time, recent_time))
    default = herodesk.DownloadWatcher(tmp_path, settle_seconds=0)
    opted = herodesk.DownloadWatcher(tmp_path, settle_seconds=0, include_recent_minutes=5)
    assert {name for name, _ in default.preexisting} == {"old.png", "recent.png"}
    assert {name for name, _ in opted.preexisting} == {"old.png"}


def test_queue_recovery_accepts_existing_reconciled_routes(plan, cfg, monkeypatch):
    manifest = {post["id"]: {"media_revision": "r"} for post in plan["posts"]}
    results = {post["id"]: {"pass": True, "errors": []} for post in plan["posts"]}
    monkeypatch.setattr(studio, "acquire_lock", lambda: True)
    monkeypatch.setattr(studio, "release_lock", lambda: None)
    monkeypatch.setattr(publish, "publish_due", lambda *args, **kwargs: [])
    monkeypatch.setattr(publish, "reconcile_expected", lambda *args, **kwargs: [])
    states = []
    monkeypatch.setattr(studio, "_run_state", lambda *args, **kwargs: states.append((args, kwargs)))
    assert studio._queue_ready_day(plan, D, manifest, results, cfg) == []
    assert states[-1][0][2] == "complete"


def test_failed_reel_is_dirty_and_renders_again_after_restart(plan, cfg, monkeypatch, tmp_path):
    reel = next(post for post in plan["posts"] if post["format"] == "reel")
    one_plan = {**plan, "posts": [reel]}
    monkeypatch.setattr(render, "media_dir", lambda _d: tmp_path)
    calls = []

    def fake_reel(_post, _d, _cfg, log=print):
        calls.append("render")
        if len(calls) == 1:
            raise RuntimeError("injected render failure")
        (tmp_path / f"{reel['id']}.mp4").write_bytes(b"video")
        (tmp_path / f"{reel['id']}-cover.png").write_bytes(b"cover")
        return {
            "files": [f"{reel['id']}.mp4", f"{reel['id']}-cover.png"],
            "fit": [], "hero": False, "props": {},
        }

    monkeypatch.setattr(render, "render_reel", fake_reel)
    first = render.render_plan(one_plan, D, cfg, log=lambda *_: None)
    assert "error" in first[reel["id"]]
    second = render.render_plan(one_plan, D, cfg, log=lambda *_: None)
    assert "error" not in second[reel["id"]]
    assert len(calls) == 2
    (tmp_path / f"{reel['id']}.mp4").write_bytes(b"corrupt")
    render.render_plan(one_plan, D, cfg, log=lambda *_: None)
    assert len(calls) == 3


def test_ingested_hero_source_survives_until_current_revision_passes(tmp_path, monkeypatch):
    from PIL import Image

    inbox = tmp_path / "inbox"
    media = tmp_path / "media"
    inbox.mkdir()
    monkeypatch.setattr(heroes, "INBOX", inbox)
    monkeypatch.setattr(heroes, "media_dir", lambda _d: media)
    source = inbox / "hero.png"
    Image.new("RGB", (800, 1000), "navy").save(source)
    plan = {
        "posts": [{
            "id": "p1", "brand": "layer8culture", "format": "single",
            "hero": {"filename": "hero.png", "shot": "A calm professional", "aspect": "4:5"},
        }]
    }
    assert heroes.ingest(plan, D, log=lambda *_: None) == ["p1"]
    assert source.exists()
    state = json.loads((media / "hero-state.json").read_text())
    (media / "manifest.json").write_text(json.dumps({
        "p1": {"hero_revision": state["p1"]["cropped_revision"], "media_revision": "media-b"}
    }))
    heroes.finalize(D, {"p1"}, log=lambda *_: None)
    assert not source.exists()
    assert (inbox / "_used" / "hero.png").exists()


def test_preflight_failure_prevents_expensive_render(plan, cfg, monkeypatch):
    monkeypatch.setattr(studio, "load_plan", lambda _d: plan)
    monkeypatch.setattr(
        gates, "run_gates",
        lambda *_args, **_kwargs: {post["id"]: {"pass": False, "errors": ["bad copy"]} for post in plan["posts"]},
    )
    rendered = []
    monkeypatch.setattr(render, "render_plan", lambda *_args, **_kwargs: rendered.append(True))
    monkeypatch.setattr(studio, "_run_state", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="preflight gates failed before render"):
        studio.produce(D, cfg)
    assert rendered == []
