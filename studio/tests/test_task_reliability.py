"""Regression tests for the 2026-10-07 outage: scheduled tasks hung at launch, so nothing was planned or logged."""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime

import pytest

import studio
from studio_lib import config

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "install-schedule.ps1").read_text(encoding="utf-8")
TASK = ROOT / "task.ps1"


def at(cfg, s):
    return datetime.fromisoformat(s).replace(tzinfo=config.tz(cfg))


def test_missed_daily_only_after_hour_and_only_if_tomorrow_unplanned(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "plan_path", lambda d: tmp_path / f"{d}.json")
    cfg["publish"]["catch_up_daily_after_hour"] = 18
    assert studio.missed_daily(at(cfg, "2026-10-07T17:30:00"), cfg) is None
    miss = studio.missed_daily(at(cfg, "2026-10-07T18:00:00"), cfg)
    assert str(miss) == "2026-10-08"
    (tmp_path / "2026-10-08.json").write_text("{}")
    assert studio.missed_daily(at(cfg, "2026-10-07T23:00:00"), cfg) is None
    cfg["publish"]["catch_up_daily_after_hour"] = None
    (tmp_path / "2026-10-08.json").unlink()
    assert studio.missed_daily(at(cfg, "2026-10-07T23:00:00"), cfg) is None


def _args(**kw):
    base = dict(date=None, now=None, dry=False, only=None, ahead=False, replace=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def publish_env(cfg, monkeypatch, tmp_path):
    produced = []
    monkeypatch.setattr(studio, "plan_path", lambda d: tmp_path / f"{d}.json")
    monkeypatch.setattr(studio, "load_plan", lambda d: None)
    monkeypatch.setattr(studio, "acquire_lock", lambda: True)
    monkeypatch.setattr(studio, "release_lock", lambda: None)
    monkeypatch.setattr(config, "is_paused", lambda c: False)
    monkeypatch.setattr(studio, "produce",
                        lambda d, c, **k: (produced.append(str(d)) or ({}, {}, {}, tmp_path / "preview.html")))
    monkeypatch.setattr(studio, "_daily_ready", lambda *a, **k: None)
    monkeypatch.setattr(studio, "_run_daily_date", lambda d, c, **k: produced.append(str(d)))
    monkeypatch.setattr(studio, "log", lambda m: None)
    cfg["publish"]["catch_up_daily_after_hour"] = 18
    cfg["publish"]["same_day_plan_cutoff_hour"] = 14
    return cfg, produced


def test_publish_catches_up_missed_run_daily_even_with_no_plan_today(publish_env):
    cfg, produced = publish_env
    studio.cmd_publish(_args(now="2026-10-07T18:00:00"), cfg)
    assert produced == ["2026-10-08"]  # tomorrow only — never same-day after the cutoff


def test_publish_does_not_catch_up_before_hour_or_on_dry_runs(publish_env):
    cfg, produced = publish_env
    studio.cmd_publish(_args(now="2026-10-07T16:00:00"), cfg)
    studio.cmd_publish(_args(now="2026-10-07T18:00:00", dry=True), cfg)
    assert produced == []


def test_date_lock_is_atomic_and_stale_lock_is_recovered(monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "DATE_LOCKS", tmp_path)
    d = "2026-10-15"
    first = studio.acquire_date_lock(d)
    assert first is not None
    assert studio.acquire_date_lock(d) is None
    first.write_text("99999999", encoding="ascii")
    monkeypatch.setattr(studio, "_process_exists", lambda pid: False)
    recovered = studio.acquire_date_lock(d)
    assert recovered is not None
    recovered.unlink()


def test_tasks_launch_through_headless_conhost_not_the_default_terminal():
    assert "System32\\conhost.exe" in INSTALL and "--headless powershell.exe" in INSTALL
    assert "task.ps1" in INSTALL and "-WindowStyle Hidden -Command" not in INSTALL


def test_daily_task_defaults_to_5pm():
    assert '[string]$DailyTime = "17:00"' in INSTALL


def test_publish_repetition_does_not_kill_the_last_run():
    assert "StopAtDurationEnd = $false" in INSTALL


def test_shortcut_keeps_custom_hero_icon():
    assert "Layer8Studio\\heroes.ico" in INSTALL and "IconLocation" in INSTALL


def test_heroes_launcher_reports_completion_or_error():
    launcher = (ROOT / "heroes.cmd").read_text(encoding="utf-8")
    assert 'set "STUDIO_EXIT=%ERRORLEVEL%"' in launcher
    assert "HEROES COMPLETE" in launcher
    assert "HEROES STOPPED WITH AN ERROR" in launcher
    assert "Press any key to close this window." in launcher
    assert "pause >nul" in launcher
    assert "exit /b %STUDIO_EXIT%" in launcher


@pytest.mark.skipif(os.name != "nt" or not shutil.which("conhost.exe"), reason="Windows only")
def test_task_wrapper_logs_start_output_and_real_exit_code(tmp_path):
    shutil.copy(TASK, tmp_path / "task.ps1")
    stub = tmp_path / "fakepy.cmd"
    stub.write_text("@echo ran %*\r\n@echo caf\xc3\xa9\r\n@exit /b 3\r\n", encoding="latin-1")
    subprocess.run(["conhost.exe", "--headless", "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "task.ps1"), "-Job", "publish",
                    "-Python", str(stub)], timeout=120, check=False)
    log = (tmp_path / "data" / "logs" / "task-publish.log").read_text(encoding="utf-8")
    lines = log.splitlines()
    assert "start publish" in lines[0]
    assert any(l.startswith("ran -u") and "studio.py" in l and l.rstrip().endswith("publish") for l in lines)
    assert "café" in log  # UTF-8 bytes pass through unmangled
    assert "end publish exit 3" in lines[-1]
