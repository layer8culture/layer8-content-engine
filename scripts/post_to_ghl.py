#!/usr/bin/env python3
"""Submit approved Deal Lab queues to GoHighLevel with immutable delivery receipts.

Direct invocation requires --commit <approved full SHA>, including dry-run.
GitHub Actions may use GITHUB_SHA. Merged-PR provenance is always verified.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from publish_helpers import (
    ACCEPTED_STATUSES, UNCERTAIN_STATUSES, VIDEO_EXTS, append_receipt,
    archive_queue_file, build_caption, latest_records, load_delivery_records,
    missing_local_paths, post_fingerprint, record_status, require_approved_payload, require_publish_ready,
    resolve_local_paths, response_post_id,
)


def env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def missing_config(commit: str | None = None) -> list[str]:
    missing = [name for name in (
        "GHL_ACCESS_TOKEN", "GHL_LOCATION_ID", "GHL_DEALLAB_INSTAGRAM_ACCOUNT_ID", "GHL_USER_ID",
    ) if not env(name)]
    if not env("GHL_MEDIA_BASE_URL") and not (
        env("GITHUB_REPOSITORY") and (commit or env("DELIVERY_APPROVAL_COMMIT") or env("GITHUB_SHA"))
    ):
        missing.append("GHL_MEDIA_BASE_URL or GITHUB_REPOSITORY plus approved commit")
    return missing


def ghl_headers() -> dict:
    token = env("GHL_ACCESS_TOKEN")
    if not token:
        raise ValueError("Missing GHL_ACCESS_TOKEN")
    return {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Version": env("GHL_API_VERSION") or "2021-07-28",
    }


def endpoint() -> str:
    location = env("GHL_LOCATION_ID")
    if not location:
        raise ValueError("Missing GHL_LOCATION_ID")
    base = (env("GHL_BASE_URL") or "https://services.leadconnectorhq.com").rstrip("/")
    return f"{base}/social-media-posting/{quote(location, safe='')}/posts"


def public_media_url(path: str, commit: str | None = None) -> str:
    normalized = str(path).replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
        raise ValueError("GoHighLevel media must have repository-relative paths")
    base = env("GHL_MEDIA_BASE_URL")
    if not base:
        repo = env("GITHUB_REPOSITORY")
        commit = commit or env("DELIVERY_APPROVAL_COMMIT") or env("GITHUB_SHA")
        if not repo or not commit:
            raise ValueError("Public media base URL or approved GitHub commit is required")
        base = f"https://raw.githubusercontent.com/{repo}/{commit}"
    return f"{base.rstrip('/')}/{quote(normalized, safe='/')}"


def schedule_date(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Schedule must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Schedule requires a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def caption_for_ghl(post: dict) -> str:
    caption, _ = build_caption(post)
    if len(caption) > 2200:
        raise ValueError("GoHighLevel Instagram caption exceeds 2200 characters")
    return caption


def ghl_payload(post: dict, paths: list[str], repo_root: Path | None = None, commit: str | None = None) -> dict:
    root = Path(repo_root or os.environ.get("LAYER8_DATA_ROOT") or Path.cwd()).resolve()
    relative_paths = [
        Path(path).resolve().relative_to(root).as_posix() if Path(path).is_absolute() else path
        for path in paths
    ]
    payload = {
        "accountIds": [env("GHL_DEALLAB_INSTAGRAM_ACCOUNT_ID")],
        "summary": caption_for_ghl(post),
        "media": [{"url": public_media_url(path, commit), "type": "video" if path.lower().endswith(VIDEO_EXTS) else "image"} for path in relative_paths],
        "status": "scheduled",
        "type": post["format"] if post["format"] in {"reel", "story"} else "post",
        "scheduleDate": schedule_date(post["schedule_time"]),
        "userId": env("GHL_USER_ID"),
    }
    _, first_comment = build_caption(post)
    if first_comment:
        payload["followUpComment"] = first_comment
    return payload


def matching_existing(post: dict) -> dict | None:
    """Read all relevant pages; an ambiguous or malformed result never permits a retry."""
    when = datetime.fromisoformat(schedule_date(post["schedule_time"]).replace("Z", "+00:00"))
    matches = []
    for offset in range(0, 10000, 100):
        response = requests.post(
            endpoint() + "/list", headers=ghl_headers(),
            json={
                "accounts": env("GHL_DEALLAB_INSTAGRAM_ACCOUNT_ID"),
                "fromDate": (when - timedelta(hours=12)).isoformat(),
                "toDate": (when + timedelta(hours=12)).isoformat(),
                "type": "all", "includeUsers": "false", "limit": "100", "skip": str(offset),
            }, timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        container = payload.get("results", payload.get("data", payload)) if isinstance(payload, dict) else payload
        posts = container.get("posts") if isinstance(container, dict) else container
        if not isinstance(posts, list) or any(not isinstance(item, dict) for item in posts):
            raise ValueError("GoHighLevel returned an invalid post list")
        for candidate in posts:
            accounts = candidate.get("accountIds") or [candidate.get("accountId")]
            if env("GHL_DEALLAB_INSTAGRAM_ACCOUNT_ID") not in accounts:
                continue
            if candidate.get("status") not in {"scheduled", "published", "in_progress"}:
                continue
            if candidate.get("summary") != caption_for_ghl(post):
                continue
            if schedule_date(candidate.get("scheduleDate", "")) == schedule_date(post["schedule_time"]):
                expected_comment = build_caption(post)[1] or ""
                if (candidate.get("followUpComment") or "") != expected_comment:
                    raise ValueError("Matching GoHighLevel post has a different or unverifiable approved first comment")
                matches.append(candidate)
        if len(posts) < 100:
            break
    else:
        raise ValueError("GoHighLevel reconciliation exceeded its page limit")
    if len(matches) > 1:
        raise ValueError("Multiple matching GoHighLevel posts require explicit reconciliation")
    return matches[0] if matches else None


def outcome(status: str, reason: str | None = None, **extra) -> dict:
    result = {
        "publisher": "gohighlevel", "delivery_status": status,
        "scheduled": status in ACCEPTED_STATUSES, "delivery_mode": "direct", "visibility": "unknown",
        **extra,
    }
    if reason:
        result["skip_reason"] = reason
    return result


def accepted(payload: object, reconciled=False) -> dict:
    post_id = response_post_id(payload)
    if not post_id:
        return outcome("unknown", "unknown_submission", skip_detail="Provider response has no verifiable post identifier")
    state = str(payload.get("status") or "") if isinstance(payload, dict) else ""
    if state in {"failed", "deleted", "draft"}:
        return outcome("unknown", "unknown_submission", ghl_post_id=post_id, provider_status=state)
    status = "published" if state == "published" else "queued" if state == "scheduled" else "accepted"
    return outcome(status, ghl_post_id=post_id, provider_status=state, reconciled=reconciled)


def schedule(post: dict, before_submit=None, reconcile_only=False, repo_root: Path | None = None,
             commit: str | None = None) -> dict:
    if post.get("account") != "deallab" or post.get("platform") != "instagram":
        raise ValueError("GoHighLevel publishing is restricted to Deal Lab Instagram")
    missing = missing_config(commit)
    if missing:
        if reconcile_only:
            return outcome("unknown", "unknown_submission", skip_detail="Configuration unavailable; earlier submission remains unresolved")
        return outcome("skipped", "missing_ghl_config", skip_detail=", ".join(missing))
    paths = resolve_local_paths(post, repo_root)
    if not paths or missing_local_paths(post, repo_root):
        return outcome("failed", "missing_media")
    if post["format"] == "reel" and not paths[0].lower().endswith(VIDEO_EXTS):
        return outcome("failed", "non_video_media")
    payload = ghl_payload(post, paths, repo_root, commit)
    existing = matching_existing(post)
    if existing:
        return accepted(existing, reconciled=True)
    if reconcile_only:
        return outcome("unknown", "unknown_submission", skip_detail="No unique provider match; no new post was submitted")
    if before_submit:
        before_submit()
    response = requests.post(endpoint(), headers=ghl_headers(), json=payload, timeout=60)
    if not response.ok:
        return outcome(
            "unknown" if response.status_code >= 500 else "failed",
            "unknown_submission" if response.status_code >= 500 else "ghl_error",
            ghl_status_code=response.status_code,
        )
    try:
        result = response.json()
    except ValueError:
        result = {}
    if not response_post_id(result):
        existing = matching_existing(post)
        return accepted(existing, reconciled=True) if existing else outcome("unknown", "unknown_submission")
    return accepted(result)


def process_queue(queue_file: str, dry_run=False, repo_root: Path | None = None, *,
                  commit: str | None = None) -> list[dict]:
    root = Path(repo_root or os.environ.get("LAYER8_DATA_ROOT") or Path.cwd()).resolve()
    qpath = Path(queue_file)
    qpath = (qpath if qpath.is_absolute() else root / qpath).resolve()
    approval = require_approved_payload(qpath, root, commit)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    if any(post.get("account") != "deallab" or post.get("platform") != "instagram" for post in posts):
        raise ValueError("GoHighLevel publishing is restricted to Deal Lab Instagram")
    require_publish_ready(qpath, root, approved_revision=approval["revision"])
    if dry_run:
        results = [{"id": post["id"], "delivery_status": "not_submitted", "payload": ghl_payload(post, resolve_local_paths(post, root), root, approval["commit"])} for post in posts]
        print(json.dumps({"dry_run": True, "posts": results}, indent=2))
        return results
    records = load_delivery_records(root)
    results = []
    for post in posts:
        fingerprint = post_fingerprint(post, root)
        previous = latest_records(records).get(str(post["id"]), {})
        previous_status = record_status(previous)
        submitted = False
        context = {
            "attempt_id": uuid.uuid4().hex,
            "integration_id": env("GHL_DEALLAB_INSTAGRAM_ACCOUNT_ID"),
            "commit": approval["commit"],
        }

        def before_submit():
            nonlocal submitted
            require_publish_ready(qpath, root, approved_revision=approval["revision"])
            records.append(append_receipt(root, post, outcome(
                "submitting", **context,
            ), fingerprint))
            submitted = True

        if previous_status in ACCEPTED_STATUSES | UNCERTAIN_STATUSES and previous.get("fingerprint") != fingerprint:
            result = outcome("failed", "revision_conflict", skip_detail="Reconcile the existing delivery before replacing its revision")
        elif previous_status in ACCEPTED_STATUSES:
            results.append(previous)
            continue
        else:
            try:
                result = schedule(
                    post, before_submit=before_submit,
                    reconcile_only=previous_status in UNCERTAIN_STATUSES, repo_root=root,
                    commit=approval["commit"],
                )
            except (requests.RequestException, ValueError, OSError) as exc:
                uncertain = submitted or previous_status in UNCERTAIN_STATUSES
                result = outcome(
                    "unknown" if uncertain else "failed",
                    "unknown_submission" if uncertain else "provider_error",
                    skip_detail=f"{type(exc).__name__}: provider operation did not complete",
                )
        record = append_receipt(root, post, {**context, **result}, fingerprint)
        records.append(record)
        results.append(record)
        print(json.dumps(record, sort_keys=True))
    if any(record_status(record) not in ACCEPTED_STATUSES | {"skipped"} for record in results):
        raise SystemExit(1)
    archive_queue_file(qpath, root / "posted")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--commit", help="Full immutable commit SHA of the reviewed, merged payload")
    args = parser.parse_args()
    process_queue(args.queue_file, dry_run=args.dry_run, repo_root=args.repo_root, commit=args.commit)
