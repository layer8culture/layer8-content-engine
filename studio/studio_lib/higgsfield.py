"""Premium video via Higgsfield (Veo 3 / Kling / Seedance), behind HIGGSFIELD_API_KEY + a monthly credit cap.

API (docs.higgsfield.ai): async. POST https://api.higgsfield.ai/<model_path> with
`Authorization: Key <id>:<secret>`; response {status, request_id, status_url}. Poll status_url until
completed|failed|nsfw|canceled; completed jobs expose video.url (kept >= 7 days).
The body shape depends on the model, so model_path + body come from config (see console.higgsfield.ai).

Manual path (always available): drop a finished clip into studio/inbox/video/<post-id>.mp4 and
`studio.py render` uses it as the reel background instead of rendering a premium job.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import requests

from .config import DATA, INBOX

API = "https://api.higgsfield.ai"
LEDGER = DATA / "higgsfield-ledger.jsonl"


def enabled(cfg: dict) -> bool:
    h = cfg.get("higgsfield") or {}
    return bool(h.get("enabled") and os.environ.get("HIGGSFIELD_API_KEY") and h.get("model_path") and h.get("monthly_credit_cap", 0) > 0)


def spent_this_month(today: date) -> int:
    if not LEDGER.exists():
        return 0
    month = today.strftime("%Y-%m")
    total = 0
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(r.get("date", "")).startswith(month):
            total += int(r.get("credits", 0))
    return total


def can_spend(cfg: dict, today: date) -> bool:
    h = cfg["higgsfield"]
    return spent_this_month(today) + int(h.get("credits_per_job_estimate", 40)) <= int(h.get("monthly_credit_cap", 0))


def manual_clip(post_id: str) -> Path | None:
    p = INBOX / "video" / f"{post_id}.mp4"
    return p if p.exists() else None


def generate(cfg: dict, today: date, post_id: str, prompt: str, image_url: str | None = None,
             poll_seconds: int = 10, timeout_minutes: int = 20, log=print) -> Path | None:
    """Submit one job, wait, download to inbox/video/<post_id>.mp4. Returns None if disabled or over cap."""
    if not enabled(cfg):
        log("  higgsfield disabled (needs HIGGSFIELD_API_KEY, higgsfield.model_path, monthly_credit_cap > 0)")
        return None
    if not can_spend(cfg, today):
        log("  higgsfield monthly credit cap reached; skipping")
        return None
    h = cfg["higgsfield"]
    headers = {"Authorization": f"Key {os.environ['HIGGSFIELD_API_KEY']}", "Content-Type": "application/json",
               "Idempotency-Key": f"layer8-{post_id}"}
    # TODO: confirm the body for the chosen model in console.higgsfield.ai; prompt/image_url/aspect_ratio
    # covers the common image-to-video routes. Extra static fields can be set in higgsfield.body_extra.
    body = {"prompt": prompt, "aspect_ratio": "9:16", **(h.get("body_extra") or {})}
    if image_url:
        body["image_url"] = image_url
    r = requests.post(f"{API}/{h['model_path'].lstrip('/')}", headers=headers, json=body, timeout=60)
    r.raise_for_status()
    job = r.json()
    status_url = job.get("status_url") or f"{API}/requests/{job['request_id']}/status"
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": today.isoformat(), "post": post_id, "request_id": job.get("request_id"),
                            "credits": int(h.get("credits_per_job_estimate", 40))}) + "\n")
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        time.sleep(poll_seconds)
        s = requests.get(status_url, headers=headers, timeout=60).json()
        st = s.get("status")
        if st == "completed":
            url = (s.get("video") or {}).get("url")
            if not url:
                raise RuntimeError(f"higgsfield completed without video url: {s}")
            out = INBOX / "video" / f"{post_id}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(requests.get(url, timeout=300).content)
            log(f"  higgsfield video saved: {out.name}")
            return out
        if st in ("failed", "nsfw", "canceled"):
            raise RuntimeError(f"higgsfield job {st}: {s}")
    raise TimeoutError("higgsfield job timed out")
