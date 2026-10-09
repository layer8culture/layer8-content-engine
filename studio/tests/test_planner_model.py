"""Regression test for the 2026-10-08 outage: claude-opus-5.5 was retired from the Copilot
CLI's model list, so the 7 PM planning run silently burned its full timeout on two pointless
repair attempts before failing with a misleading 'draft file was not written' error."""
import pathlib

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
