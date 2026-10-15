"""Atomic, owner-checked file locks with stale-process recovery."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class LockBusy(RuntimeError):
    pass


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class FileLock:
    def __init__(self, path: Path, purpose: str, token: str):
        self.path = path
        self.purpose = purpose
        self.token = token
        self.released = False

    @classmethod
    def acquire(cls, path: Path, purpose: str, *, stale_seconds: int = 90 * 60) -> "FileLock":
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        payload = {
            "pid": os.getpid(), "token": token, "purpose": purpose,
            "created_at": time.time(),
        }
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    current = {}
                created_at = current.get("created_at")
                age = time.time() - float(path.stat().st_mtime if created_at is None else created_at)
                if process_exists(int(current.get("pid") or -1)) or age < stale_seconds:
                    raise LockBusy(f"{purpose} lock is held by pid {current.get('pid', '?')}")
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            return cls(path, purpose, token)
        raise LockBusy(f"could not acquire {purpose} lock")

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        if current.get("token") == self.token and int(current.get("pid") or -1) == os.getpid():
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> "FileLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
