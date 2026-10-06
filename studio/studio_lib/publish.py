"""Just-in-time publishing to Postiz. Posts go out only if the kill switch is off and every gate passed."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import requests

from . import context
from .config import POSTED_LOG, media_dir
from .schedule import due, parse


class PostizError(RuntimeError):
    pass


def channel_id(cfg: dict, brand: str, platform: str) -> str:
    ch = ((cfg["publish"].get("channels") or {}).get(brand) or {}).get(platform) or {}
    return str(os.environ.get(ch.get("env", ""), "") or ch.get("default") or "").strip()


def posted_keys() -> dict[tuple[str, str], str]:
    """(post_id, platform) -> last recorded status."""
    out = {}
    for r in context.read_jsonl(POSTED_LOG):
        if r.get("id") and r.get("platform"):
            out[(r["id"], r["platform"])] = r.get("status", "")
    return out


def media_for(post: dict, platform: str, d: date, manifest: dict) -> list:
    files = (manifest.get(post["id"]) or {}).get("files") or []
    if post["format"] == "reel":
        files = [f for f in files if f.endswith(".mp4")]
    return [media_dir(d) / f for f in files]


def caption_for(post: dict, platform: str, cfg: dict) -> str:
    tags = post.get("hashtags") or []
    if platform == "youtube":
        tags = tags[: cfg["gates"].get("max_hashtags_youtube", 5)]
    return f"{post['caption'].strip()}\n\n{' '.join(tags)}".strip()


def settings_for(post: dict, platform: str, cfg: dict) -> dict:
    if platform == "youtube":
        s = {"title": post["youtube_title"].strip()[:100], "type": cfg["publish"].get("youtube_privacy", "public"),
             "selfDeclaredMadeForKids": "no"}
        tags, total = [], 0
        for t in post.get("hashtags") or []:
            label = t.lstrip("#")
            if total + len(label) <= 480:
                tags.append({"value": label, "label": label})
                total += len(label)
        if tags:
            s["tags"] = tags
        return s
    return {"post_type": "story" if post["format"] == "story" else "post"}


class Postiz:
    def __init__(self, url: str, key: str):
        if not url or not key:
            raise PostizError("POSTIZ_URL and POSTIZ_API_KEY must be set in studio/.env")
        self.url = url.rstrip("/")
        self.h = {"Authorization": key}

    def upload(self, path) -> dict:
        with open(path, "rb") as fh:
            r = requests.post(f"{self.url}/api/public/v1/upload", headers=self.h, files={"file": (path.name, fh)}, timeout=300)
        if r.status_code >= 300:
            raise PostizError(f"upload {path.name}: {r.status_code} {r.text[:300]}")
        j = r.json()
        return {"id": j["id"], "path": j["path"]}

    def schedule(self, integration: str, when_utc: str, content: str, media: list[dict], settings: dict) -> dict:
        body = {"type": "schedule", "date": when_utc, "shortLink": False, "tags": [],
                "posts": [{"integration": {"id": integration}, "value": [{"content": content, "image": media}], "settings": settings}]}
        r = requests.post(f"{self.url}/api/public/v1/posts", headers={**self.h, "Content-Type": "application/json"}, json=body, timeout=120)
        if r.status_code >= 300:
            raise PostizError(f"create post: {r.status_code} {r.text[:300]}")
        return r.json() if r.text else {}

    def delete(self, post_id: str) -> None:
        """Deletes the post's whole Postiz group (we create one group per post/platform)."""
        r = requests.delete(f"{self.url}/api/public/v1/posts/{post_id}", headers=self.h, timeout=60)
        if r.status_code >= 300:
            raise PostizError(f"delete {post_id}: {r.status_code} {r.text[:300]}")


def _postiz_ids(resp) -> list[str]:
    items = resp if isinstance(resp, list) else [resp]
    return [str(i["postId"]) for i in items if isinstance(i, dict) and i.get("postId")]


def last_rows() -> dict[tuple[str, str], dict]:
    out = {}
    for r in context.read_jsonl(POSTED_LOG):
        if r.get("id") and r.get("platform"):
            out[(r["id"], r["platform"])] = r
    return out


def needs_replace(post_id: str, manifest: dict, row: dict) -> bool:
    """A late ChatGPT hero arrived after the template version was already handed to Postiz."""
    return bool((manifest.get(post_id) or {}).get("hero")) and not row.get("hero")


def _log(row: dict) -> None:
    context.append_jsonl(POSTED_LOG, {"at": context.now_iso(), **row})


def publish_due(plan: dict, d: date, cfg: dict, manifest: dict, gate_results: dict, *, now: datetime,
                dry: bool = False, log=print, only: set | None = None, ahead: bool = False,
                replace: bool | None = None, force_replace: bool = False) -> list[dict]:
    """ahead=True hands every not-yet-missed post of the day to Postiz now, at its planned time.

    replace (default publish.replace_on_media_change): a post already scheduled in Postiz with the
    template version is deleted there and re-scheduled at the same time once its ChatGPT hero arrives.
    force_replace re-sends already-scheduled posts regardless (use with only=...).
    Posts within replace_min_lead_minutes of going out are left alone.
    """
    pub = cfg["publish"]
    look, grace = int(pub.get("lookahead_minutes", 50)), int(pub.get("catch_up_grace_minutes", 180))
    if ahead:
        look = 7 * 24 * 60
    if replace is None:
        replace = bool(pub.get("replace_on_media_change", True))
    replace = replace or force_replace
    min_lead = timedelta(minutes=int(pub.get("replace_min_lead_minutes", 10)))
    done = posted_keys()
    prev_rows = last_rows() if replace else {}
    client = None
    actions = []
    for post in plan["posts"]:
        if only and post["id"] not in only:
            continue
        for platform, when in (post.get("schedule") or {}).items():
            key = (post["id"], platform)
            old_ids: list[str] = []
            if done.get(key) == "scheduled" and replace:
                prev = prev_rows.get(key) or {}
                if parse(when) - now < min_lead:
                    continue
                if not (force_replace or needs_replace(post["id"], manifest, prev)):
                    continue
                old_ids = _postiz_ids(prev.get("postiz"))
                if not old_ids:
                    log(f"  skip      {post['id']} → {platform}: media changed but no Postiz post id recorded to replace")
                    continue
            elif done.get(key) in ("scheduled", "missed", "blocked", "skipped", "failed-final"):
                continue
            state = "due" if old_ids else due(when, now, look, grace)
            if state == "future":
                continue
            base = {"id": post["id"], "date": d.isoformat(), "brand": post["brand"], "platform": platform,
                    "format": post["format"], "topic": post.get("topic"), "hook": post.get("hook"), "when": when}
            if old_ids:
                base["replaces"] = old_ids
            if not cfg["platforms"].get(platform, False):
                row = {**base, "status": "skipped", "reason": f"platform {platform} disabled"}
            elif state == "missed":
                row = {**base, "status": "missed", "reason": f"laptop was off past the {grace} min grace window"}
            elif not (gate_results.get(post["id"]) or {}).get("pass"):
                row = {**base, "status": "blocked", "reason": "; ".join((gate_results.get(post["id"]) or {}).get("errors", ["gates not run"]))[:800]}
            elif not channel_id(cfg, post["brand"], platform):
                row = {**base, "status": "skipped", "reason": f"no Postiz channel id for {post['brand']}/{platform}"}
            else:
                files = media_for(post, platform, d, manifest)
                if not files or not all(f.exists() for f in files):
                    row = {**base, "status": "blocked", "reason": "media missing"}
                elif dry:
                    row = {**base, "status": "dry-run", "media": [f.name for f in files]}
                else:
                    try:
                        client = client or Postiz(os.environ.get("POSTIZ_URL", ""), os.environ.get("POSTIZ_API_KEY", ""))
                        at = max(parse(when).astimezone(timezone.utc), now.astimezone(timezone.utc) + timedelta(minutes=3))
                        uploaded = [client.upload(f) for f in files]
                    except Exception as exc:  # noqa: BLE001
                        uploaded, upload_err = None, exc
                    if uploaded is not None and old_ids:
                        try:
                            for pid in old_ids:
                                client.delete(pid)
                        except Exception as exc:  # noqa: BLE001
                            # Old post may still be live in Postiz: record nothing, so we never double-post.
                            log(f"  kept      {post['id']} → {platform}: could not delete old Postiz post ({str(exc)[:200]})")
                            continue
                    try:
                        if uploaded is None:
                            raise upload_err
                        resp = client.schedule(channel_id(cfg, post["brand"], platform), at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                               caption_for(post, platform, cfg), uploaded, settings_for(post, platform, cfg))
                        row = {**base, "status": "scheduled", "postiz": resp if isinstance(resp, (dict, list)) else str(resp),
                               "media": [f.name for f in files], "hero": bool((manifest.get(post["id"]) or {}).get("hero"))}
                    except Exception as exc:  # noqa: BLE001
                        if old_ids and uploaded is None:
                            log(f"  kept      {post['id']} → {platform}: upload failed, existing Postiz post left in place ({str(exc)[:200]})")
                            continue
                        fails = sum(1 for r in context.read_jsonl(POSTED_LOG) if r.get("id") == post["id"] and r.get("platform") == platform and r.get("status") == "failed")
                        row = {**base, "status": "failed-final" if fails >= 2 else "failed", "reason": str(exc)[:500]}
            if old_ids and row["status"] not in ("scheduled", "failed", "failed-final", "dry-run"):
                log(f"  kept      {post['id']} → {platform}: not replacing ({row.get('reason') or row['status']})")
                continue
            if not dry:
                _log(row)
            actions.append(row)
            verb = "replaced" if old_ids and row["status"] == "scheduled" else row["status"]
            log(f"  {verb:<9} {post['id']} → {platform} @ {when}" + (f"  ({row.get('reason')})" if row.get("reason") else "")
                + (f"  [replaces {', '.join(old_ids)}]" if old_ids else ""))
    return actions
