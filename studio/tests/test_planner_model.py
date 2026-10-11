"""Regression test for the 2026-10-08 outage: claude-opus-5.5 was retired from the Copilot
CLI's model list, so the 7 PM planning run silently burned its full timeout on two pointless
repair attempts before failing with a misleading 'draft file was not written' error."""
import pathlib
from datetime import date

import pytest

from studio_lib import planner

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_config_model_is_not_the_retired_opus(cfg):
    assert cfg["model"] != "claude-opus-5.5"


def test_run_copilot_fails_fast_with_a_clear_error_when_the_model_is_unavailable(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)

    def fake_run(args, **kw):
        kw["stdout"].write('Error: Model "claude-opus-5.5" from --model flag is not available.\n')

        class R:
            returncode = 1

        return R()

    monkeypatch.setattr(planner.subprocess, "run", fake_run)
    cfg = dict(cfg, model="claude-opus-5.5")
    with pytest.raises(RuntimeError, match="no longer available"):
        planner.run_copilot("prompt.md", cfg, "plan-test.log")


def test_run_copilot_timeout_preserves_a_draft_for_normal_validation(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)

    def fake_run(args, **kw):
        (tmp_path / "draft.json").write_text('{"posts": []}', encoding="utf-8")
        raise planner.subprocess.TimeoutExpired(args, 1)

    monkeypatch.setattr(planner.subprocess, "run", fake_run)
    assert planner.run_copilot("prompt.md", cfg, "plan-timeout.log") == 124
    assert (tmp_path / "draft.json").exists()
    log = (tmp_path / "data" / "logs" / "plan-timeout.log").read_text(encoding="utf-8")
    assert "timed out; validating any draft" in log


def test_context_bundle_is_single_bounded_source(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)
    files = {
        "data/news/day.md": "N" * 100,
        "data/context/history.md": "recent topics",
        "data/context/transcript.md": "T" * 100,
    }
    for path in (
        "brand/voice-layer8culture.md", "brand/voice-lofi.md", "brand/visual-style.md",
        "brand/hashtags.md", "brand/topics-layer8culture.md", "brand/topics-radio.md",
        "brand/viral-formats.md",
    ):
        files[path] = path
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    cfg = {**cfg, "copilot": {**cfg["copilot"], "news_context_chars": 20,
                              "transcript_context_chars": 30}}
    rel = planner.write_context_bundle(
        date(2026, 10, 12), cfg, "data/news/day.md", "data/context/history.md",
        "data/context/transcript.md")
    bundle = (tmp_path / rel).read_text(encoding="utf-8")
    assert "Read this file once" in bundle
    assert "N" * 20 in bundle and "N" * 21 not in bundle
    assert "T" * 30 in bundle and "T" * 31 not in bundle


def test_build_prompt_replaces_multi_file_reads_with_bundle(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)
    template = ROOT / "templates" / "plan-prompt.md"
    target = tmp_path / "templates" / "plan-prompt.md"
    target.parent.mkdir(parents=True)
    target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    prompt = planner.build_prompt(
        date(2026, 10, 12), cfg, planner.build_slots(date(2026, 10, 12), cfg),
        "data/news/day.md", "data/context/history.md", "data/context/transcript.md",
        "data/plans/day.draft.json", "data/context/planning-day.md")
    assert "data/context/planning-day.md" in prompt
    assert "Read that single bundle once" in prompt
    assert "brand/voice-layer8culture.md  — voice" not in prompt
