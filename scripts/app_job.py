"""Detached job supervisor; completion and logs survive closing the web app."""
from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys

from app_state import StateStore, process_identity


def run(root: pathlib.Path, job_id: str) -> None:
    store = StateStore(root)
    job = store.get(job_id)
    if job["cancel_requested"]:
        store.finish(job_id, "cancelled", None)
        return
    if job["status"] != "running":
        return
    result_path = store.jobs_dir / f"{job_id}.result.json"
    env = dict(os.environ, LAYER8_JOB_RESULT=str(result_path))
    options = {} if os.name == "nt" else {"start_new_session": True}
    with (store.jobs_dir / f"{job_id}.log").open("ab", buffering=0) as log:
        try:
            proc = subprocess.Popen(json.loads(job["argv"]), cwd=root, env=env,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        except OSError as exc:
            log.write(f"Could not start job: {exc}\n".encode("utf-8"))
            store.finish(job_id, "failed", None, {"error": str(exc)})
            return
        cancelled = False
        child_identity = process_identity(proc.pid)
        while proc.poll() is None:
            try:
                proc.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                if store.get(job_id)["cancel_requested"]:
                    cancelled = True
                    if proc.poll() is not None:
                        break
                    if process_identity(proc.pid) != child_identity:
                        store.finish(job_id, "interrupted", None,
                                     {"error": "Child identity changed; refusing to signal another process."})
                        return
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       stdout=log, stderr=log, check=False)
                    else:
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
        result = None
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                store.finish(job_id, "failed", proc.returncode,
                             {"error": f"Unreadable job result: {exc}"})
                return
        status = "cancelled" if cancelled else ("done" if proc.returncode == 0 else "failed")
        store.finish(job_id, status, proc.returncode, result)


if __name__ == "__main__":
    run(pathlib.Path(sys.argv[1]), sys.argv[2])
