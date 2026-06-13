#!/usr/bin/env python3
"""Publish approved posts to Postiz.

Runs on merge to main. Reads queue/<date>.json, uploads media, schedules each
post via the Postiz public API, then moves the file to posted/ and appends to
posted/log.json.

Env vars: POSTIZ_URL, POSTIZ_API_KEY
Note: integration IDs map your accounts/platforms to Postiz channels.
Fill INTEGRATIONS after connecting your accounts in the Postiz UI
(Settings -> API shows channel IDs).
"""
import json
import os
import sys
import pathlib
import requests

POSTIZ_URL = os.environ["POSTIZ_URL"].rstrip("/")
HEADERS = {"Authorization": os.environ["POSTIZ_API_KEY"]}

# account+platform -> Postiz integration (channel) ID. FILL THESE IN.
# Note: ("lofi", "tiktok") is provisioning-only — the lofi cadence currently
# targets Instagram and X (see calendar/topics.md); nothing generates for lofi
# TikTok unless topics.md explicitly asks for it.
INTEGRATIONS = {
    ("layer8culture", "tiktok"): "REPLACE_ME",
    ("layer8culture", "instagram"): "REPLACE_ME",
    ("layer8culture", "x"): "REPLACE_ME",
    ("lofi", "instagram"): "REPLACE_ME",
    ("lofi", "x"): "REPLACE_ME",
    ("lofi", "tiktok"): "REPLACE_ME",
}

VIDEO_EXTS = (".mp4", ".mov")

def upload_media(filepath: str) -> dict:
    with open(filepath, "rb") as f:
        r = requests.post(
            f"{POSTIZ_URL}/public/v1/upload",
            headers=HEADERS,
            files={"file": f},
            timeout=120,
        )
    r.raise_for_status()
    return r.json()  # contains id + path

def resolve_local_path(post: dict) -> str | None:
    visual = post.get("visual", {})
    path = visual.get("file")
    if not path and visual.get("source") == "library":
        # naive library match: first file whose name contains a hint word
        hint = (visual.get("library_hint") or "").lower().split()
        lib = pathlib.Path("assets/library")
        for f in lib.glob("*"):
            if any(w in f.name.lower() for w in hint) or not hint:
                path = str(f)
                break
    if not path or not pathlib.Path(path).exists():
        return None
    return path

def resolve_media(local_path: str) -> list[dict]:
    media = upload_media(local_path)
    return [{"id": media["id"], "path": media["path"]}]

def schedule(post: dict) -> bool:
    key = (post["account"], post["platform"])
    integration_id = INTEGRATIONS.get(key)
    if not integration_id or integration_id == "REPLACE_ME":
        print(f"  ! no integration mapped for {key}, skipping {post['id']}")
        return False
    text = post["text"]
    if post.get("hashtags"):
        text += "\n\n" + " ".join(post["hashtags"])
    local_path = resolve_local_path(post)
    if post["platform"] == "tiktok":
        if not local_path:
            print(f"  ! tiktok post {post['id']} has no resolved media, skipping")
            return False
        if not local_path.lower().endswith(VIDEO_EXTS):
            print(f"  ! tiktok post {post['id']} media is not a video "
                  f"({', '.join(VIDEO_EXTS)}), skipping")
            return False
    media = resolve_media(local_path) if local_path else []
    payload = {
        "type": "schedule",
        "date": post["schedule_time"],
        "posts": [{
            "integration": {"id": integration_id},
            "value": [{"content": text, "image": media}],
        }],
    }
    r = requests.post(f"{POSTIZ_URL}/public/v1/posts",
                      headers={**HEADERS, "Content-Type": "application/json"},
                      json=payload, timeout=60)
    if r.ok:
        print(f"  ✓ scheduled {post['id']} for {post['schedule_time']}")
        return True
    print(f"  ✗ {post['id']}: {r.status_code} {r.text[:200]}")
    return False

def main(queue_file: str) -> None:
    qpath = pathlib.Path(queue_file)
    posts = json.loads(qpath.read_text())
    results = [{**p, "scheduled": schedule(p)} for p in posts]

    posted_dir = pathlib.Path("posted")
    posted_dir.mkdir(exist_ok=True)
    log_path = posted_dir / "log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    log.extend(results)
    log_path.write_text(json.dumps(log, indent=2))
    qpath.rename(posted_dir / qpath.name)
    print(f"Done: {sum(p['scheduled'] for p in results)}/{len(results)} scheduled.")

if __name__ == "__main__":
    main(sys.argv[1])
