"""Journaled Postiz handoff. Studio changes stop while paused; existing cloud routes are untouched."""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
        return post["youtube_description"].strip()
    return f"{post['caption'].strip()}\n\n{' '.join(tags)}".strip()


def settings_for(post: dict, platform: str, cfg: dict) -> dict:
    if platform == "youtube":
        s = {"__type": "youtube", "title": post["youtube_title"].strip()[:100],
             "type": cfg["publish"].get("youtube_privacy", "public"),
             "selfDeclaredMadeForKids": "no"}
        tags, total = [], 0
        for t in (post.get("hashtags") or [])[: cfg["gates"].get("max_hashtags_youtube", 5)]:
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

    def list_posts(self, start_utc: datetime, end_utc: datetime) -> list[dict]:
        r = requests.get(
            f"{self.url}/api/public/v1/posts",
            headers=self.h,
            params={
                "startDate": start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "endDate": end_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
            timeout=60,
        )
        if r.status_code >= 300:
            raise PostizError(f"list posts: {r.status_code} {r.text[:300]}")
        return r.json().get("posts", [])


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
    """The rendered media revision differs from the version recorded as handed to Postiz."""
    current = (manifest.get(post_id) or {}).get("media_revision")
    previous = row.get("media_revision")
    if current and previous:
        return current != previous
    return bool((manifest.get(post_id) or {}).get("hero")) and not row.get("hero")


def _log(row: dict) -> None:
    context.append_jsonl(POSTED_LOG, {"at": context.now_iso(), **row})


def _operation_path(d: date, post_id: str, platform: str) -> Path:
    safe = f"{post_id}--{platform}".replace("/", "_")
    return media_dir(d) / ".operations" / f"{safe}.json"


def _save_operation(path: Path, operation: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    operation["updated_at"] = context.now_iso()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(operation, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_operation(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _route_posts(client: Postiz, integration: str, when_utc: datetime, assumed_ids=()) -> list[dict]:
    if not hasattr(client, "list_posts"):
        return [{"id": post_id, "state": "QUEUE"} for post_id in assumed_ids]
    posts = client.list_posts(when_utc - timedelta(minutes=2), when_utc + timedelta(minutes=2))
    target = when_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return [
        post for post in posts
        if str((post.get("integration") or {}).get("id")) == integration
        and str(post.get("publishDate")) == target
        and str(post.get("state")) in {"QUEUE", "PUBLISHED"}
    ]


def _scheduled_row(base: dict, post: dict, manifest: dict, resp, files: list[Path], old_ids: list[str]) -> dict:
    row = {
        **base, "status": "scheduled",
        "postiz": resp if isinstance(resp, (dict, list)) else str(resp),
        "media": [f.name for f in files],
        "hero": bool((manifest.get(post["id"]) or {}).get("hero")),
        "hero_revision": (manifest.get(post["id"]) or {}).get("hero_revision"),
        "media_revision": (manifest.get(post["id"]) or {}).get("media_revision"),
    }
    if old_ids:
        row["replaces"] = old_ids
    return row


def _adopt_live_response(posts: list[dict]) -> list[dict]:
    return [{"postId": str(post["id"])} for post in posts if post.get("id")]


def _needs_attention(base: dict, reason: str, operation: dict, path: Path) -> dict:
    operation.update(phase="needs_attention", reason=reason)
    _save_operation(path, operation)
    return {**base, "status": "needs_attention", "reason": reason, "operation": str(path)}


def reconcile_expected(plan: dict, d: date, cfg: dict, manifest: dict) -> list[str]:
    """Verify every enabled route has the current media revision and a live Postiz ID."""
    client = Postiz(os.environ.get("POSTIZ_URL", ""), os.environ.get("POSTIZ_API_KEY", ""))
    latest = last_rows()
    issues = []
    for post in plan.get("posts", []):
        for platform, when in (post.get("schedule") or {}).items():
            if not cfg["platforms"].get(platform, False) or not channel_id(cfg, post["brand"], platform):
                issues.append(f"{post['id']}/{platform}: route disabled or channel missing")
                continue
            row = latest.get((post["id"], platform)) or {}
            current_revision = (manifest.get(post["id"]) or {}).get("media_revision")
            if row.get("status") != "scheduled":
                issues.append(f"{post['id']}/{platform}: latest status is {row.get('status') or 'missing'}")
                continue
            if current_revision and row.get("media_revision") != current_revision:
                issues.append(f"{post['id']}/{platform}: queued media revision is stale")
                continue
            ids = _postiz_ids(row.get("postiz"))
            when_utc = parse(row.get("scheduled_for") or when).astimezone(timezone.utc)
            live = {str(item.get("id")) for item in _route_posts(client, channel_id(cfg, post["brand"], platform), when_utc)}
            if not ids or not any(post_id in live for post_id in ids):
                issues.append(f"{post['id']}/{platform}: recorded Postiz ID is not live")
    return issues


def _cloud_route(*, client, post: dict, platform: str, d: date, cfg: dict, manifest: dict,
                 files: list[Path], base: dict, old_ids: list[str], planned_utc: datetime,
                 at: datetime, min_lead: timedelta, clock, fault) -> tuple[dict, dict]:
    op_path = _operation_path(d, post["id"], platform)
    integration = channel_id(cfg, post["brand"], platform)
    media_revision = (manifest.get(post["id"]) or {}).get("media_revision")
    operation = _load_operation(op_path)
    target_utc = at

    if operation and operation.get("media_revision") == media_revision:
        phase = operation.get("phase")
        if phase == "needs_attention":
            return {
                **base, "status": "needs_attention", "reason": operation.get("reason", "operation needs attention"),
                "operation": str(op_path),
            }, operation
        if phase in {"complete", "create_confirmed"}:
            live = _route_posts(client, integration, target_utc, operation.get("new_ids") or ())
            confirmed = [item for item in live if str(item.get("id")) in set(operation.get("new_ids") or ())]
            if confirmed:
                return _scheduled_row(base, post, manifest, _adopt_live_response(confirmed), files, old_ids), operation
            return _needs_attention(base, "created Postiz ID is no longer live", operation, op_path), operation
        if phase == "create_requested":
            live = _route_posts(client, integration, target_utc)
            old_set = set(operation.get("old_ids") or ())
            old_live = [item for item in live if str(item.get("id")) in old_set]
            new_live = [item for item in live if str(item.get("id")) not in old_set]
            if len(new_live) == 1 and not old_live:
                operation.update(phase="create_confirmed", new_ids=[str(new_live[0]["id"])])
                _save_operation(op_path, operation)
                return _scheduled_row(base, post, manifest, _adopt_live_response(new_live), files, old_ids), operation
            return _needs_attention(
                base, f"ambiguous prior create operation ({len(old_live)} old, {len(new_live)} new live routes)",
                operation, op_path,
            ), operation
        if phase == "delete_requested":
            live = _route_posts(client, integration, planned_utc, operation.get("old_ids") or ())
            old_live = [item for item in live if str(item.get("id")) in set(operation.get("old_ids") or ())]
            if old_live:
                return _needs_attention(
                    base, "prior delete outcome is ambiguous and the old route remains live", operation, op_path,
                ), operation
            operation["phase"] = "delete_confirmed"
            _save_operation(op_path, operation)
        if phase == "upload_complete":
            uploaded = operation.get("uploaded") or []
        elif operation.get("phase") == "delete_confirmed":
            uploaded = operation.get("uploaded") or []
        else:
            operation = None
            uploaded = None
    else:
        operation = None
        uploaded = None

    if operation is None:
        operation = {
            "operation_id": uuid.uuid4().hex, "date": d.isoformat(), "post_id": post["id"],
            "platform": platform, "integration": integration,
            "publish_date": target_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "media_revision": media_revision, "old_ids": old_ids, "phase": "uploading",
        }
        _save_operation(op_path, operation)
        try:
            uploaded = [client.upload(f) for f in files]
        except Exception as exc:  # noqa: BLE001
            operation.update(phase="upload_failed", reason=str(exc)[:500])
            _save_operation(op_path, operation)
            return {**base, "status": "failed", "reason": str(exc)[:500]}, operation
        operation.update(phase="upload_complete", uploaded=uploaded)
        _save_operation(op_path, operation)
        fault("after_upload", operation)

    if old_ids and operation.get("phase") == "upload_complete":
        actual_now = clock().astimezone(timezone.utc)
        live = _route_posts(client, integration, planned_utc, old_ids)
        old_live = [item for item in live if str(item.get("id")) in set(old_ids)]
        if planned_utc - actual_now < min_lead:
            reason = "replacement cutoff reached before delete"
            operation.update(phase="preserved_cutoff", reason=reason)
            _save_operation(op_path, operation)
            return {**base, "status": "preserved", "reason": reason}, operation
        if any(str(item.get("state")) == "PUBLISHED" for item in old_live):
            reason = "existing route is already published"
            operation.update(phase="preserved_published", reason=reason)
            _save_operation(op_path, operation)
            return {**base, "status": "preserved", "reason": reason}, operation
        if not old_live:
            return _needs_attention(base, "recorded old Postiz route was not live before delete", operation, op_path), operation
        operation["phase"] = "delete_requested"
        _save_operation(op_path, operation)
        try:
            for post_id in old_ids:
                client.delete(post_id)
        except Exception as exc:  # noqa: BLE001
            try:
                live = _route_posts(client, integration, planned_utc, old_ids)
                if any(str(item.get("id")) in set(old_ids) for item in live):
                    return _needs_attention(
                        base, f"delete outcome uncertain; old route remains live: {exc}", operation, op_path,
                    ), operation
                operation["phase"] = "delete_confirmed"
                _save_operation(op_path, operation)
            except Exception as check_exc:  # noqa: BLE001
                return _needs_attention(
                    base, f"delete outcome could not be reconciled: {exc}; live check: {check_exc}",
                    operation, op_path,
                ), operation
        else:
            operation["phase"] = "delete_confirmed"
            _save_operation(op_path, operation)
        fault("after_delete", operation)

    operation["phase"] = "create_requested"
    _save_operation(op_path, operation)
    try:
        response = client.schedule(
            integration, at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            caption_for(post, platform, cfg), uploaded, settings_for(post, platform, cfg),
        )
    except Exception as exc:  # noqa: BLE001
        try:
            live = _route_posts(client, integration, target_utc)
            old_set = set(old_ids)
            old_live = [item for item in live if str(item.get("id")) in old_set]
            new_live = [item for item in live if str(item.get("id")) not in old_set]
            if len(new_live) == 1 and not old_live:
                response = _adopt_live_response(new_live)
            else:
                return _needs_attention(
                    base, f"create outcome uncertain ({len(new_live)} candidate live routes): {exc}",
                    operation, op_path,
                ), operation
        except Exception as check_exc:  # noqa: BLE001
            return _needs_attention(
                base, f"create outcome could not be reconciled: {exc}; live check: {check_exc}",
                operation, op_path,
            ), operation
    operation.update(phase="create_confirmed", new_ids=_postiz_ids(response))
    _save_operation(op_path, operation)
    fault("after_create", operation)
    row = _scheduled_row(base, post, manifest, response, files, old_ids)
    fault("before_ledger", operation)
    return row, operation


def publish_due(plan: dict, d: date, cfg: dict, manifest: dict, gate_results: dict, *, now: datetime,
                dry: bool = False, log=print, only: set | None = None, ahead: bool = False,
                replace: bool | None = None, force_replace: bool = False, client_factory=None,
                clock=None, fault=lambda phase, operation: None) -> list[dict]:
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
    client_factory = client_factory or Postiz
    clock = clock or (lambda: now)
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
            operation = None
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
            elif done.get(key) == "scheduled":
                continue
            state = "due" if old_ids else due(when, now, look, grace)
            if state == "future":
                continue
            base = {"id": post["id"], "date": d.isoformat(), "brand": post["brand"], "platform": platform,
                    "format": post["format"], "topic": post.get("topic"), "hook": post.get("hook"), "when": when}
            if platform == "youtube":
                base["youtube_title"] = post.get("youtube_title")
                base["youtube_description"] = post.get("youtube_description")
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
                        client = client or client_factory(os.environ.get("POSTIZ_URL", ""), os.environ.get("POSTIZ_API_KEY", ""))
                        planned_utc = parse(when).astimezone(timezone.utc)
                        at = max(planned_utc, now.astimezone(timezone.utc) + timedelta(minutes=3))
                        base["scheduled_for"] = at.isoformat()
                        row, operation = _cloud_route(
                            client=client, post=post, platform=platform, d=d, cfg=cfg, manifest=manifest,
                            files=files, base=base, old_ids=old_ids, planned_utc=planned_utc, at=at,
                            min_lead=min_lead, clock=clock, fault=fault,
                        )
                    except Exception as exc:  # noqa: BLE001
                        row = {**base, "status": "failed", "reason": str(exc)[:500]}
            if old_ids and row["status"] not in ("scheduled", "failed", "failed-final", "dry-run"):
                log(f"  kept      {post['id']} → {platform}: not replacing ({row.get('reason') or row['status']})")
                continue
            if not dry:
                _log(row)
                if row["status"] == "scheduled" and "operation" in locals() and operation:
                    operation["phase"] = "complete"
                    _save_operation(_operation_path(d, post["id"], platform), operation)
            actions.append(row)
            verb = "replaced" if old_ids and row["status"] == "scheduled" else row["status"]
            log(f"  {verb:<9} {post['id']} → {platform} @ {when}" + (f"  ({row.get('reason')})" if row.get("reason") else "")
                + (f"  [replaces {', '.join(old_ids)}]" if old_ids else ""))
    return actions
