"""Config, paths and .env loading."""
from __future__ import annotations

import json
import os
import pathlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PLANS = DATA / "plans"
MEDIA = DATA / "media"
INBOX = ROOT / "inbox"
POSTED_LOG = DATA / "posted.jsonl"
LEGACY_HISTORY = DATA / "history-legacy.jsonl"
TEMPLATES = ROOT / "templates"
REMOTION = TEMPLATES / "remotion"
FONTS = ROOT / "assets" / "fonts"
BRAND = ROOT / "brand"


def load_env(path: pathlib.Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: pathlib.Path | None = None) -> dict:
    cfg = yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))
    windows = ROOT / cfg.get("schedule_windows_file", "config/schedule-windows.json")
    cfg["schedule_windows"] = json.loads(windows.read_text(encoding="utf-8"))
    return cfg


def is_paused(cfg: dict) -> bool:
    return bool(cfg.get("paused")) or (ROOT / "PAUSE").exists()


def tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg.get("timezone", "America/New_York"))


def today(cfg: dict) -> date:
    return datetime.now(tz(cfg)).date()


def parse_date(value: str | None, cfg: dict, default_offset: int = 1) -> date:
    if not value:
        return today(cfg) + timedelta(days=default_offset)
    if value == "today":
        return today(cfg)
    if value == "tomorrow":
        return today(cfg) + timedelta(days=1)
    return date.fromisoformat(value)


def plan_path(d: date) -> pathlib.Path:
    return PLANS / f"{d.isoformat()}.json"


def media_dir(d: date) -> pathlib.Path:
    return MEDIA / d.isoformat()


def ensure_dirs() -> None:
    for p in (DATA, PLANS, MEDIA, INBOX, INBOX / "video", DATA / "news", DATA / "context", DATA / "logs"):
        p.mkdir(parents=True, exist_ok=True)
