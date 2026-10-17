"""Local, durable workflow state. Queue JSON remains the content source of truth."""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import threading
import time
import uuid


def process_identity(pid: int) -> str | None:
    """Identify a live process without confusing a reused Windows PID with it."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        handle = kernel.OpenProcess(0x101000, False, pid)
        if not handle:
            return None
        try:
            if kernel.WaitForSingleObject(handle, 0) != 258:
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            return f"{times[0].dwHighDateTime}:{times[0].dwLowDateTime}"
        finally:
            kernel.CloseHandle(handle)
    proc = pathlib.Path(f"/proc/{pid}/stat")
    try:
        # Everything after the final ')' starts at field 3; starttime is field 22.
        return proc.read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class StateStore:
    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root).resolve()
        self.directory = self.root / ".local"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.jobs_dir = self.directory / "jobs"
        self.jobs_dir.mkdir(exist_ok=True)
        self.path = self.directory / "workflow.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, queue TEXT NOT NULL,
                    label TEXT NOT NULL, command TEXT NOT NULL, argv TEXT NOT NULL,
                    status TEXT NOT NULL, started REAL NOT NULL, finished REAL,
                    returncode INTEGER, result TEXT, pid INTEGER, identity TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS batches (
                    name TEXT PRIMARY KEY, approval TEXT
                );
                CREATE TABLE IF NOT EXISTS observations (
                    name TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def mutation(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover(db)
            if db.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                raise RuntimeError("A job is running. Wait for it to finish or cancel it.")
            yield db

    def _recover(self, db):
        for job in db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
            # Allow the reservation to reach the worker before declaring interruption.
            if not job["pid"] and time.time() - job["started"] < 30:
                continue
            identity = process_identity(job["pid"]) if job["pid"] else None
            if identity is not None and (not job["identity"] or identity == job["identity"]):
                continue
            db.execute(
                "UPDATE jobs SET status='interrupted', finished=?, result=? WHERE id=?",
                (time.time(), json.dumps({"error": "Worker interrupted. Review outputs before retrying."}),
                 job["id"]),
            )

    def active(self) -> str | None:
        with self.connection() as db:
            self._recover(db)
            row = db.execute("SELECT id FROM jobs WHERE status='running'").fetchone()
            return row["id"] if row else None

    def start(self, kind: str, argv: list[str], *, queue: str = "", label: str = "",
              command: str = "") -> str:
        job_id = uuid.uuid4().hex
        with self.mutation() as db:
            db.execute(
                "INSERT INTO jobs (id,kind,queue,label,command,argv,status,started) "
                "VALUES (?,?,?,?,?,?,'running',?)",
                (job_id, kind, queue, label, command or label, json.dumps(argv), time.time()),
            )
        worker = pathlib.Path(__file__).with_name("app_job.py")
        options = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                   if os.name == "nt" else {"start_new_session": True})
        try:
            with (self.jobs_dir / f"{job_id}.worker.log").open("ab") as log:
                proc = subprocess.Popen(
                    [sys.executable, str(worker), str(self.root), job_id],
                    cwd=self.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    env=dict(os.environ, LAYER8_DATA_ROOT=str(self.root)), **options,
                )
            with self.connection() as db:
                db.execute("UPDATE jobs SET pid=?,identity=? WHERE id=?",
                           (proc.pid, process_identity(proc.pid), job_id))
            threading.Thread(target=proc.wait, daemon=True).start()
        except OSError as exc:
            self.finish(job_id, "failed", None, {"error": f"Cannot start worker: {exc}"})
            raise
        return job_id

    def get(self, job_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown job.")
        return dict(row)

    def payload(self, job_id: str, since: int = 0) -> dict:
        self.active()
        job = self.get(job_id)
        path = self.jobs_dir / f"{job_id}.log"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
        return {key: job[key] for key in
                ("id", "kind", "queue", "label", "command", "status", "returncode")} | {
            "lines": lines[max(0, since):], "next": len(lines),
            "result": json.loads(job["result"]) if job["result"] else None,
            "cancellable": job["status"] == "running" and job["kind"] in {
                "generate", "prepare", "ingest", "reels"},
        }

    def finish(self, job_id: str, status: str, returncode: int | None, result=None):
        with self.connection() as db:
            db.execute("UPDATE jobs SET status=?,returncode=?,result=?,finished=? WHERE id=?",
                       (status, returncode, json.dumps(result), time.time(), job_id))

    def cancel(self, job_id: str):
        with self.connection() as db:
            row = db.execute("SELECT status,kind FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown job.")
            if row["kind"] not in ("generate", "prepare", "ingest", "reels"):
                raise ValueError("Only local generation or media preparation can be cancelled.")
            if row["status"] != "running":
                raise ValueError("That job is no longer running.")
            db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))

    def approval(self, name: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT approval FROM batches WHERE name=?", (name,)).fetchone()
        return json.loads(row["approval"]) if row and row["approval"] else None

    def save_approval(self, name: str, approval: dict | None):
        with self.connection() as db:
            db.execute("INSERT INTO batches(name,approval) VALUES(?,?) "
                       "ON CONFLICT(name) DO UPDATE SET approval=excluded.approval",
                       (name, json.dumps(approval) if approval is not None else None))

    def invalidate_approval(self, name: str, db=None):
        if db is not None:
            db.execute("UPDATE batches SET approval=NULL WHERE name=?", (name,))
        else:
            self.save_approval(name, None)

    def observation(self, name: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT value FROM observations WHERE name=?", (name,)).fetchone()
        return json.loads(row["value"]) if row else {}

    def save_observation(self, name: str, value: dict):
        with self.connection() as db:
            db.execute("INSERT INTO observations(name,value) VALUES(?,?) "
                       "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                       (name, json.dumps(value)))
