"""Durable job health and deduplicated failure-notification markers."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime
from pathlib import Path

from .config import DATA


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_job(job: str, status: str, **details) -> Path:
    path = DATA / "health" / f"{job}.json"
    _atomic_json(path, {
        "job": job, "status": status, "updated_at": datetime.now().astimezone().isoformat(),
        **details,
    })
    return path


def failure_marker(scope: str, d: date | str, error: Exception | str) -> Path:
    text = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return DATA / "notifications" / f"failure-{scope}-{d}-{digest}.json"


def mark_failure_notified(path: Path, **details) -> None:
    _atomic_json(path, {"notified_at": datetime.now().astimezone().isoformat(), **details})
