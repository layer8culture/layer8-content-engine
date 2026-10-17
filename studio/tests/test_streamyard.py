from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from studio_lib import context, streamyard


VTT = """WEBVTT

00:00:00.000 --> 00:00:01.000
One

00:00:01.000 --> 00:00:02.000
Two

00:00:02.000 --> 00:00:03.000
Three
"""


def _paths(monkeypatch, tmp_path):
    transcripts = tmp_path / "transcripts"
    index = tmp_path / "data" / "transcripts" / "index.json"
    profile = tmp_path / "data" / "browser" / "streamyard-profile"
    monkeypatch.setattr(streamyard, "TRANSCRIPTS", transcripts)
    monkeypatch.setattr(streamyard, "INDEX", index)
    monkeypatch.setattr(streamyard, "PROFILE", profile)
    return transcripts, index, profile


def test_validate_vtt_requires_header_and_multiple_cues():
    assert streamyard.validate_vtt(VTT) == 3
    with pytest.raises(streamyard.StreamYardError, match="not WEBVTT"):
        streamyard.validate_vtt("not a transcript")
    with pytest.raises(streamyard.StreamYardError, match="only 1"):
        streamyard.validate_vtt("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nOne")


def test_air_date_and_show_classification_are_source_grounded():
    parsed = streamyard._parse_air_date("Oct 15, 2026, 9:06 PM 1:06:13")
    assert parsed == datetime(2026, 10, 15, 21, 6)
    card_text = (
        "42:14\nGenerate clips\n"
        "Tech Thursday LIVE: Claude Opus 5 and the New AI Builder Stack\n\n"
        "Aug 6, 2026, 09:11 PM\n\nNo AI clips yet"
    )
    title = streamyard._parse_title(card_text, "Generate clips")
    assert title == "Tech Thursday LIVE: Claude Opus 5 and the New AI Builder Stack"
    assert streamyard._classify_show(title) == "tech-thursday"
    assert streamyard._classify_show("AI FOR BUSINESS WORKSHOP LIVE") == "workshop"
    assert streamyard._classify_show("An unrelated folder recording") == "other"


def test_import_is_atomic_idempotent_and_preserves_workshop(monkeypatch, tmp_path):
    transcripts, index, _profile = _paths(monkeypatch, tmp_path)
    source = tmp_path / "download.vtt"
    source.write_text("\ufeff" + VTT, encoding="utf-8")
    recording = streamyard.Recording(
        "xegc8q86kgsk",
        "AI FOR BUSINESS WORKSHOP LIVE",
        datetime(2026, 8, 21, 16, 5),
        "https://streamyard.com/teams/t/videos/xegc8q86kgsk",
        "workshop",
    )

    destination, changed, cues = streamyard.import_vtt(recording, source)
    assert changed is True
    assert cues == 3
    assert destination.parent == transcripts
    destination2, changed2, _ = streamyard.import_vtt(recording, source)
    assert destination2 == destination
    assert changed2 is False
    row = json.loads(index.read_text(encoding="utf-8"))["files"][0]
    assert row["recording_id"] == "xegc8q86kgsk"
    assert row["show"] == "workshop"
    assert not list(index.parent.glob("*.tmp"))


def test_import_rejects_same_recording_id_with_changed_content(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    source = tmp_path / "download.vtt"
    source.write_text(VTT, encoding="utf-8")
    recording = streamyard.Recording(
        "sameid", "Tech Thursday LIVE", datetime(2026, 10, 1),
        "https://streamyard.com/videos/sameid", "tech-thursday",
    )
    streamyard.import_vtt(recording, source)
    source.write_text(VTT.replace("Three", "Changed"), encoding="utf-8")
    with pytest.raises(streamyard.StreamYardError, match="different content"):
        streamyard.import_vtt(recording, source)


def test_import_deduplicates_same_transcript_hash_across_source_ids(monkeypatch, tmp_path):
    _transcripts, index, _profile = _paths(monkeypatch, tmp_path)
    source = tmp_path / "download.vtt"
    source.write_text(VTT, encoding="utf-8")
    first = streamyard.Recording(
        "first", "Tech Thursday LIVE: One", datetime(2026, 10, 1),
        "https://streamyard.com/videos/first", "tech-thursday",
    )
    second = streamyard.Recording(
        "second", "Tech Thursday LIVE: Duplicate", datetime(2026, 10, 2),
        "https://streamyard.com/videos/second", "tech-thursday",
    )
    first_path, first_changed, _ = streamyard.import_vtt(first, source)
    second_path, second_changed, _ = streamyard.import_vtt(second, source)
    assert first_changed is True
    assert second_changed is False
    assert second_path == first_path
    rows = json.loads(index.read_text(encoding="utf-8"))["files"]
    assert {row["recording_id"] for row in rows} == {"first", "second"}
    assert len({row["file"] for row in rows}) == 1


def test_latest_transcript_uses_indexed_air_date_not_mtime(monkeypatch, tmp_path):
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    runtime_index = tmp_path / "data" / "transcripts" / "index.json"
    runtime_index.parent.mkdir(parents=True)
    indexed = transcripts / "2026-10-15-id-show.vtt"
    indexed.write_text(VTT.replace("One", "Indexed"), encoding="utf-8")
    undated = transcripts / "downloaded-later.vtt"
    undated.write_text(VTT.replace("One", "Stale"), encoding="utf-8")
    dated_unindexed = transcripts / "2026-10-15-manual.vtt"
    dated_unindexed.write_text(VTT.replace("One", "Manual"), encoding="utf-8")
    future = datetime(2030, 1, 1).timestamp()
    os.utime(undated, (future, future))
    runtime_index.write_text(json.dumps({"files": [{
        "file": indexed.name, "date": "2026-10-15", "show": "tech-thursday",
        "recording_id": "id",
    }]}), encoding="utf-8")
    monkeypatch.setattr(context, "TRANSCRIPTS", transcripts)
    monkeypatch.setattr(context, "RUNTIME_TRANSCRIPT_INDEX", runtime_index)

    latest = context.latest_transcript()
    assert latest["file"] == indexed.name
    assert latest["date"] == "2026-10-15"
    assert "Indexed" in latest["text"]
    assert "Stale" not in latest["text"]


def test_missing_profile_blocks_before_browser_start(monkeypatch, tmp_path):
    _transcripts, _index, _profile = _paths(monkeypatch, tmp_path)
    health = []
    monkeypatch.setattr(streamyard, "update_job", lambda job, status, **details: health.append((job, status, details)))
    with pytest.raises(streamyard.StreamYardLoginRequired, match="not configured"):
        streamyard.import_latest({"streamyard": {"folder_url": "https://streamyard.com/folder"}})
    assert health[0][0:2] == ("streamyard-import", "running")
    assert health[-1][0:2] == ("streamyard-import", "failed")


def test_pending_transcript_is_waiting_not_failed(monkeypatch):
    health = []
    monkeypatch.setattr(streamyard, "update_job", lambda job, status, **details: health.append((job, status, details)))

    def pending(*_args, **_kwargs):
        streamyard.update_job("streamyard-import", "waiting", error="still generating")
        raise streamyard.StreamYardTranscriptPending("still generating")

    monkeypatch.setattr(streamyard, "_import_latest", pending)
    with pytest.raises(streamyard.StreamYardTranscriptPending):
        streamyard.import_latest({"streamyard": {"folder_url": "https://streamyard.com/folder"}})
    assert [status for _job, status, _details in health] == ["running", "waiting"]


def test_launch_context_uses_configured_installed_browser(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    calls = []

    class Chromium:
        def launch_persistent_context(self, **kwargs):
            calls.append(kwargs)
            return object()

    playwright = type("Playwright", (), {"chromium": Chromium()})()
    streamyard._launch_context(playwright, headless=False, channel="chrome")
    assert calls == [{
        "user_data_dir": str(streamyard.PROFILE),
        "headless": False,
        "accept_downloads": True,
        "channel": "chrome",
    }]


def test_login_opens_normal_chrome_with_dedicated_profile(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"")
    calls = []
    monkeypatch.setattr(streamyard, "_chrome_executable", lambda: chrome)
    monkeypatch.setattr(
        streamyard.subprocess, "Popen",
        lambda args: calls.append(args) or object(),
    )
    streamyard._open_login_browser("https://streamyard.com/folder")
    assert calls == [[
        str(chrome),
        f"--user-data-dir={streamyard.PROFILE}",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "https://streamyard.com/folder",
    ]]
