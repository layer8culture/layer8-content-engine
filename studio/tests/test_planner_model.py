"""Regression test for the 2026-10-08 outage: claude-opus-5.5 was retired from the Copilot
CLI's model list, so the 7 PM planning run silently burned its full timeout on two pointless
repair attempts before failing with a misleading 'draft file was not written' error."""
import json
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
        class R:
            returncode = 1
            stdout = 'Error: Model "claude-opus-5.5" from --model flag is not available.\n'

        return R()

    monkeypatch.setattr(planner.subprocess, "run", fake_run)
    cfg = dict(cfg, model="claude-opus-5.5")
    with pytest.raises(RuntimeError, match="no longer available"):
        planner.run_copilot("return json", cfg, "plan-test.log")


def test_run_copilot_timeout_is_bounded_and_logged(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)

    def fake_run(args, **kw):
        raise planner.subprocess.TimeoutExpired(args, 1)

    monkeypatch.setattr(planner.subprocess, "run", fake_run)
    rc, response = planner.run_copilot("return json", cfg, "plan-timeout.log")
    assert rc == 124
    assert "timed out" in response
    log = (tmp_path / "data" / "logs" / "plan-timeout.log").read_text(encoding="utf-8")
    assert "tool-free Copilot response timed out" in log


def test_run_copilot_auth_outage_fails_without_a_repair_loop(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)

        class R:
            returncode = 1
            stdout = "Authentication required. Run copilot login."

        return R()

    monkeypatch.setattr(planner.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Authentication required"):
        planner.run_copilot("return json", cfg, "plan-auth.log")
    assert len(calls) == 1


def test_run_copilot_has_no_autonomous_tools_and_uses_stdin(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)
    seen = {}

    def fake_run(args, **kw):
        seen["args"], seen["input"] = args, kw["input"]

        class R:
            returncode = 0
            stdout = json.dumps({
                "type": "assistant.message",
                "data": {"content": '{"ok": true}'},
            })

        return R()

    monkeypatch.setattr(planner.subprocess, "run", fake_run)
    rc, response = planner.run_copilot("SELF CONTAINED", cfg, "plan-tool-free.log")
    assert rc == 0 and response == '{"ok": true}'
    assert seen["input"] == "SELF CONTAINED"
    assert "--no-custom-instructions" in seen["args"]
    for tool in ("read", "write", "shell", "web_fetch"):
        assert tool in seen["args"]


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
    assert "youtube_description" in prompt
    assert "Do not lead with the brand/show name" in prompt
    assert "`#Shorts` is optional" in prompt


def test_self_contained_request_inlines_bundle_and_requires_json_response(tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)
    (tmp_path / "prompt.md").write_text(
        "save it as JSON.\n## READ FIRST\n- bundle.md\n## TODAY'S SLOTS\nslots\n"
        "## OUTPUT\nWrite ONLY valid JSON (UTF-8, no comments, no markdown fences) to the file:\n"
        "  draft.json\nusing exactly this shape:\n{\"posts\": []}\n"
        "Do not write any other files. When the file is written, reply with the single word DONE.",
        encoding="utf-8",
    )
    (tmp_path / "bundle.md").write_text("VERIFIED SOURCE", encoding="utf-8")
    request = planner._self_contained_request("prompt.md", "bundle.md")
    assert "VERIFIED SOURCE" in request
    assert "do not request or read files" in request
    assert "Return ONLY valid JSON" in request
    assert "draft.json" not in request


def test_decode_response_repairs_model_line_wrapping_inside_strings():
    plan, errors = planner._decode_response('{"summary":"first line\nsecond line","posts":[]}')
    assert errors == []
    assert plan["summary"] == "first line\nsecond line"


def test_decode_response_strips_terminal_hyperlinks():
    raw = '{"url":"\x1b]8;id=x;https://example.com\x07https://example.com\x1b]8;;\x07"}'
    plan, errors = planner._decode_response(raw)
    assert errors == []
    assert plan["url"] == "https://example.com"


def test_assistant_content_extracts_json_from_event_stream():
    stream = (
        '{"type":"session.mcp_server_status_changed","data":{}}\n'
        + json.dumps({"type": "assistant.message", "data": {"content": '{"posts":[]}'}})
        + "\n"
    )
    assert planner._assistant_content(stream) == '{"posts":[]}'


def test_make_plan_uses_one_bounded_repair_for_malformed_output(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)
    monkeypatch.setattr(planner, "PLANS", tmp_path / "data" / "plans")
    monkeypatch.setattr(planner, "plan_path", lambda d: tmp_path / "data" / "plans" / f"{d}.json")
    monkeypatch.setattr(planner.news, "fetch_news", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner.context, "write_history_context", lambda _d: "history.md")
    monkeypatch.setattr(planner.context, "write_transcript_context", lambda: "transcript.md")
    monkeypatch.setattr(planner, "write_context_bundle", lambda *_args, **_kwargs: "bundle.md")
    monkeypatch.setattr(planner, "build_prompt", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(planner, "_self_contained_request", lambda *_args, **_kwargs: "request")
    monkeypatch.setattr(planner.schema, "validate_plan", lambda *_args: [])
    monkeypatch.setattr(planner, "_slot_errors", lambda *_args: [])
    monkeypatch.setattr(planner, "_content_gate_errors", lambda *_args: [])
    responses = iter([(0, "not json"), (0, '{"summary":"fixed","posts":[]}')])
    monkeypatch.setattr(planner, "run_copilot", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(planner, "assign_schedule", lambda *_args, **_kwargs: None)
    plan = planner.make_plan(date(2026, 12, 30), cfg, log=lambda *_: None)
    assert plan["summary"] == "fixed"
    assert json.loads((tmp_path / "data" / "plans" / "2026-12-30.json").read_text())["summary"] == "fixed"
