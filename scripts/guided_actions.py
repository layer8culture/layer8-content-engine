"""Validated background actions for the local guided content workflow."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

from app_state import StateStore
from guided_workflow import atomic_json

CODE_ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("LAYER8_DATA_ROOT", str(CODE_ROOT))).resolve()


def generate(payload: dict) -> dict:
    import adhoc_server
    import queue_json_guard
    import recover_copilot_queue
    import schedule_planner

    lane, date = payload["lane"], payload["date"]
    config = adhoc_server.lane_config(lane)
    adhoc_server.validate_date(date)
    qpath = ROOT / "queue" / adhoc_server.queue_name_for(lane, date)
    if qpath.exists():
        raise ValueError("This batch already exists. Resume it instead of generating over it.")
    prompt = (ROOT / config["prompt"]).read_text(encoding="utf-8")
    command = adhoc_server.generation_command(lane, date, adhoc_server.copilot_command(), prompt)
    proc = subprocess.run(command, cwd=ROOT, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Generation failed (exit {proc.returncode}); existing outputs were preserved.")
    if not qpath.exists():
        recovered = recover_copilot_queue.recover_queue(qpath, date=date)
        if recovered is None:
            raise ValueError("Generation produced no valid queue artifact. See the generation log.")
    posts = queue_json_guard.load_json(qpath.read_text(encoding="utf-8"), qpath)
    queue_json_guard.validate_queue_shape(posts, qpath)
    subprocess.run([sys.executable, str(CODE_ROOT / "scripts" / "schedule_planner.py"),
                    str(qpath), "--date", date], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(CODE_ROOT / "scripts" / "schedule_planner.py"),
                    str(qpath), "--for-publish"], cwd=ROOT, check=True)
    print("Draft generated and scheduled for review. Import images to continue.")
    return {"queue": qpath.name, "posts": len(posts), "state": "needs_images"}


def run(kind: str, payload: dict) -> dict:
    if kind == "generate":
        return generate(payload)
    import adhoc_server
    import batch_readiness

    qpath = adhoc_server.safe_queue_path(payload["queue"])
    state = StateStore(ROOT)
    report = batch_readiness.report(qpath, ROOT)
    if payload.get("revision") != report["revision"]:
        raise ValueError("This batch changed before the job started. Refresh and review it again.")
    if kind == "refresh-delivery":
        import delivery_refresh
        result = delivery_refresh.refresh(qpath, ROOT, state.approval(qpath.name))
        state.save_observation(qpath.name, dict(result, revision=report["revision"]))
        if result.get("approval"):
            state.save_approval(qpath.name, result["approval"])
        return result
    if kind == "prepare":
        import prepare_media
        state.save_approval(qpath.name, None)
        result = prepare_media.prepare(qpath, ROOT)
        if result.get("failed"):
            raise ValueError("Preparation incomplete: " + "; ".join(result["failed"]))
        return dict(result, queue=qpath.name)
    if kind == "stage-approval":
        import ship_queue
        result = ship_queue.ship(str(qpath), repo_root=ROOT)
        state.save_approval(qpath.name, result)
        return result
    if kind == "approve":
        import ship_queue
        previous = state.approval(qpath.name)
        if not previous or any(previous.get(key) != payload.get(key)
                               for key in ("revision", "head_sha", "pr_number")):
            raise ValueError("Prepare and review the current approval PR before approving it.")
        result = ship_queue.approve(
            str(qpath), expected_revision=payload["revision"],
            expected_head=payload["head_sha"], pr_number=payload["pr_number"], repo_root=ROOT)
        result = dict(previous, **result)
        state.save_approval(qpath.name, result)
        return result
    if kind == "reschedule":
        import schedule_planner
        result = schedule_planner.reschedule_queue(qpath, payload["date"], repo_root=ROOT)
        state.save_approval(qpath.name, None)
        return result
    raise ValueError(f"Unknown guided action: {kind}")


def main() -> int:
    result_path = os.environ.get("LAYER8_JOB_RESULT")
    try:
        result = run(sys.argv[1], json.loads(sys.argv[2]))
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        if result_path:
            atomic_json(pathlib.Path(result_path), {"error": str(exc)})
        print(f"Action failed: {exc}", file=sys.stderr)
        return 1
    if result_path:
        atomic_json(pathlib.Path(result_path), result)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
