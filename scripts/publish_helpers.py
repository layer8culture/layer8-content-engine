#!/usr/bin/env python3
"""Shared helpers for approved social queue publishers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import time
import uuid
from datetime import datetime, timezone


VIDEO_EXTS = (".mp4", ".mov")
ACCEPTED_STATUSES = {"accepted", "queued", "published", "inbox", "private"}
UNCERTAIN_STATUSES = {"submitting", "unknown"}


def local_media_path(path: str | os.PathLike) -> pathlib.Path:
    """Return a local Path for repo media paths written on any OS."""
    return pathlib.Path(str(path).replace("\\", "/"))


def resolve_media_path(path: str | os.PathLike | None, repo_root: pathlib.Path | None = None) -> str | None:
    if not path:
        return None
    candidate = local_media_path(path)
    if repo_root is not None and not candidate.is_absolute():
        candidate = pathlib.Path(repo_root).resolve() / candidate
    return str(candidate) if candidate.is_file() else None


def missing_local_paths(post: dict, repo_root: pathlib.Path | None = None) -> list[str]:
    """Declared media paths that do not resolve on this runner."""
    visual = post.get("visual", {})
    if post.get("format") == "carousel":
        return [
            str(path)
            for path in (visual.get("files") or [])
            if path and resolve_media_path(path, repo_root) is None
        ]

    path = visual.get("file")
    if path and resolve_media_path(path, repo_root) is None:
        return [str(path)]
    return []


def resolve_local_paths(post: dict, repo_root: pathlib.Path | None = None) -> list[str]:
    """Ordered list of existing local media paths for the post."""
    visual = post.get("visual", {})
    if post.get("format") == "carousel":
        files = [
            resolved
            for path in (visual.get("files") or [])
            if (resolved := resolve_media_path(path, repo_root))
        ]
        if files:
            return files

    path = visual.get("file")
    if not path and visual.get("source") == "library":
        hint = (visual.get("library_hint") or "").lower().split()
        lib = (pathlib.Path(repo_root).resolve() if repo_root is not None else pathlib.Path()) / "assets" / "library"
        for f in lib.glob("*"):
            if any(w in f.name.lower() for w in hint) or not hint:
                path = str(f)
                break
    resolved = resolve_media_path(path, repo_root)
    if resolved:
        return [resolved]
    return []


def build_caption(post: dict) -> tuple[str, str | None]:
    """Return the caption and optional first comment for a queue post."""
    caption = post["text"]
    hashtags = post.get("hashtags") or []
    first_comment = (post.get("first_comment") or "").strip()
    tag_line = " ".join(hashtags)
    if post.get("hashtags_in_first_comment") and hashtags:
        first_comment = (
            f"{first_comment}\n\n{tag_line}".strip() if first_comment else tag_line
        )
    elif hashtags:
        caption += "\n\n" + tag_line
    return caption, (first_comment or None)


def load_log(log_path: pathlib.Path) -> list[dict]:
    if not log_path.exists():
        return []
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise ValueError(f"{log_path}: delivery history must be a list of objects")
    return payload


def scheduled_post_ids(log: list[dict]) -> set[str]:
    return {
        str(record.get("id"))
        for record in log
        if isinstance(record, dict) and record.get("id")
        and (record.get("scheduled") is True or record_status(record) in ACCEPTED_STATUSES)
    }


def append_new_log_records(log: list[dict], results: list[dict]) -> list[dict]:
    """Preserve history while appending changed outcomes, including successful retries."""
    latest = {str(record.get("id")): record for record in log if record.get("id")}
    for result in results:
        post_id = str(result.get("id") or "")
        if post_id and latest.get(post_id) == result:
            continue
        log.append(result)
        latest[post_id] = result
    return log


def record_status(record: dict) -> str:
    if record.get("delivery_status"):
        return str(record["delivery_status"])
    if record.get("scheduled") is True:
        return "accepted"
    if record.get("skip_reason") == "postiz_duplicate":
        return "accepted" if record.get("postiz_post_id") else "unknown"
    if record.get("skip_reason") in {
        "missing_media", "non_video_media", "postiz_error", "ghl_error",
        "network_error", "provider_error", "payload_error", "revision_conflict",
    }:
        return "failed"
    return "skipped" if record.get("skip_reason") else "not_submitted"


def post_fingerprint(post: dict, repo_root: pathlib.Path) -> str:
    """Bind an attempt to content, schedule, and the exact declared media bytes."""
    visual = post.get("visual") or {}
    paths = visual.get("files") if post.get("format") == "carousel" else [visual.get("file")]
    media = []
    for raw in paths or []:
        if not raw:
            continue
        path = local_media_path(raw)
        if not path.is_absolute():
            path = repo_root / path
        digest = hashlib.sha256()
        if path.is_file():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            value = digest.hexdigest()
        else:
            value = "missing"
        media.append((str(raw).replace("\\", "/"), value))
    payload = json.dumps({"post": post, "media": media}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_delivery_records(repo_root: pathlib.Path, log_path: pathlib.Path | None = None) -> list[dict]:
    log_path = log_path if log_path is not None else repo_root / "posted" / "log.json"
    records = load_log(log_path)
    events = []
    for path in sorted((log_path.parent / "receipts").rglob("*.json")):
        event = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(event, dict) or not event.get("id") or not event.get("event_id"):
            raise ValueError(f"{path}: invalid delivery receipt; recovery is required")
        events.append(event)
    events.sort(key=lambda row: (row.get("recorded_at", ""), row["event_id"]))
    return records + events


def append_receipt(repo_root: pathlib.Path, post: dict, result: dict, fingerprint: str) -> dict:
    """Atomically add an immutable event; never rewrite posted/log.json."""
    event_id = uuid.uuid4().hex
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not run_id.isdecimal():
        run_id = "local"
    if not attempt.isdecimal():
        attempt = "1"
    directory = repo_root / "posted" / "receipts" / f"{run_id}-{attempt}"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        **{key: post[key] for key in ("id", "account", "platform", "format", "schedule_time") if key in post},
        **result,
        "fingerprint": fingerprint,
        "event_id": event_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    commit = os.environ.get("DELIVERY_APPROVAL_COMMIT") or os.environ.get("GITHUB_SHA")
    if commit and not record.get("commit"):
        record["commit"] = commit
    if os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID"):
        record["workflow_url"] = (
            f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        )
    target = directory / f"{time.time_ns()}-{event_id}.json"
    pending = target.with_suffix(".pending")
    with pending.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, target)
    return record


def latest_records(records: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for record in records:
        if record.get("id") and record.get("skip_reason") not in {"revision_conflict", "duplicate_post_id"}:
            latest[str(record["id"])] = record
    return latest


def merge_delivery_records(local: list[dict], remote: list[dict]) -> list[dict]:
    """Merge immutable events by identity/time without altering either history."""
    legacy = []
    events: dict[str, dict] = {}
    for record in [*local, *remote]:
        event_id = record.get("event_id")
        if not event_id:
            legacy.append(record)
            continue
        if event_id in events and events[event_id] != record:
            raise ValueError("Conflicting immutable delivery receipt; explicit reconciliation is required")
        events[event_id] = record

    def order(record):
        moment = datetime.fromisoformat(record["recorded_at"].replace("Z", "+00:00"))
        if moment.utcoffset() is None:
            raise ValueError("Delivery receipt has no timestamp timezone")
        return moment.astimezone(timezone.utc), record["event_id"]

    return legacy + sorted(events.values(), key=order)


def delivery_status(posts: list[dict], log_path: pathlib.Path) -> list[dict]:
    """Read local history and explicitly refreshed evidence; never use the network.

    Pass ``repo_root / "posted" / "log.json"``. A repository directory is also
    accepted for callers of the earlier helper contract.
    """
    log_path = pathlib.Path(log_path)
    if log_path.suffix != ".json":
        repo_root = log_path
        log_path = repo_root / "posted" / "log.json"
    else:
        repo_root = log_path.parent.parent
    records = load_delivery_records(repo_root, log_path=log_path)
    remote = {"records": [], "contexts": {}, "fingerprints": {}, "remote_event_ids": set()}
    if (repo_root / ".local" / "remote-delivery").is_dir():
        from delivery_refresh import cached_evidence

        remote = cached_evidence(repo_root, posts)
        records = merge_delivery_records(records, remote["records"])
    latest = latest_records(records)
    descriptions = {
        "not_submitted": "No local submission receipt. Await approval or refresh remote receipts after merge; scheduling is not confirmed.",
        "accepted": "The latest receipt confirms provider acceptance, not publication.",
        "queued": "The latest local receipt reports queued; live provider status has not been refreshed.",
        "published": "The latest receipt reports published; this is not a live provider lookup.",
        "inbox": "The latest receipt reports delivery to the TikTok inbox; manual publication is still required.",
        "private": "The latest receipt reports private delivery, not a public post.",
        "skipped": "The post was explicitly skipped and was not submitted.",
        "failed": "The latest attempt failed; inspect its receipt before retrying.",
        "unknown": "Submission is unconfirmed. Reconcile remote receipts/provider identity before resubmitting.",
        "submitting": "A submission intent was recorded without a final receipt; reconcile before retrying.",
        "revision_changed": "Receipts belong to different content, media, or timing. This draft is not confirmed submitted; renew approval and reconcile the previous delivery.",
    }
    statuses = []
    for post in posts:
        record = latest.get(str(post["id"]), {})
        status = record_status(record)
        recorded_status = status
        fingerprint = record.get("fingerprint")
        current_fingerprint = remote["fingerprints"].get(post["id"])
        if fingerprint and current_fingerprint is None:
            current_fingerprint = post_fingerprint(post, repo_root)
        identity_matches = bool(fingerprint and fingerprint == current_fingerprint)
        if fingerprint and not identity_matches:
            status = "revision_changed"
        elif record and not fingerprint and status in ACCEPTED_STATUSES | UNCERTAIN_STATUSES:
            status = "unknown"
        detail = descriptions.get(status, "Only a local receipt is available; provider status is unconfirmed.")
        if record and not fingerprint and recorded_status in ACCEPTED_STATUSES:
            detail = f"The latest legacy receipt reports {recorded_status}, but this draft's media/content identity cannot be verified. Reconcile before resubmitting."
        if record.get("skip_detail"):
            detail += " " + str(record["skip_detail"])
        elif record.get("skip_reason"):
            detail += " Reason: " + str(record["skip_reason"]) + "."
        result = {
            "id": post["id"],
            "state": "submission_pending" if status == "not_submitted" else "unknown" if status == "submitting" else status,
            "detail": detail,
            "delivery_status": status,
            "last_recorded_state": recorded_status,
            "source": "receipt" if record else "none",
            "live_status": False,
            "remote_refresh_required": status in {"not_submitted", "submitting", "unknown", "revision_changed"},
            **{key: record[key] for key in (
                "publisher", "provider_status",
                "delivery_mode", "visibility", "skip_reason", "skip_detail",
                "requested_visibility", "recorded_at", "workflow_url", "commit",
            ) if key in record},
        }
        provider_id = record.get("postiz_post_id") or record.get("ghl_post_id")
        if identity_matches and provider_id:
            result["provider_id"] = provider_id
            result.update({key: record[key] for key in ("postiz_post_id", "ghl_post_id") if key in record})
        elif provider_id:
            result["previous_provider_id"] = provider_id
        url = record.get("url") or record.get("post_url") or record.get("workflow_url")
        if url:
            result["url"] = url
        context = remote["contexts"].get(post["id"])
        if context:
            workflow = context.get("workflow")
            result.update(
                observed_at=context["observed_at"],
                remote_source_commit=context["source_commit"],
                receipt_source="remote" if record.get("event_id") in remote["remote_event_ids"] else "local" if record else "none",
            )
            if workflow:
                result["workflow"] = workflow
                if not result.get("url") and workflow.get("url"):
                    result["url"] = workflow["url"]
                failure = workflow.get("conclusion") in {
                    "failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale",
                }
                result["workflow_failed"] = failure
                if not identity_matches and status != "revision_changed":
                    result["source"] = "workflow"
                    result["remote_refresh_required"] = True
                    if failure:
                        result.update(
                            state="unknown", delivery_status="unknown",
                            detail=f"Publishing workflow ended with {workflow['conclusion']} and no verified receipt for this draft. Provider outcome is unknown; reconcile before retrying.",
                        )
                    elif workflow.get("status") != "completed":
                        result.update(
                            state="submission_pending", delivery_status="not_submitted",
                            detail=f"Publishing workflow is {workflow['status']}; no receipt confirms this draft was submitted.",
                        )
                    else:
                        result.update(
                            state="unknown", delivery_status="unknown",
                            detail="The workflow completed, but no matching receipt confirms this draft's provider submission. A green workflow or merge alone is not delivery evidence.",
                        )
            elif not record:
                result["detail"] = "No matching submission receipt or publish workflow was found during the explicit remote refresh. Await approval/submission evidence."
        statuses.append(result)
    return statuses


def require_approved_payload(qpath: pathlib.Path, repo_root: pathlib.Path, commit: str | None = None) -> dict:
    """Verify an immutable merged-PR payload inside every executable publisher."""
    if commit is None and os.environ.get("GITHUB_ACTIONS") == "true":
        commit = os.environ.get("GITHUB_SHA")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("Publishing requires --commit <approved full SHA>, or GITHUB_SHA inside GitHub Actions")
    from approval_guard import verify

    commit = commit.lower()
    proof = verify(qpath, commit, repo_root=repo_root)
    if (
        not isinstance(proof, dict) or proof.get("state") != "approved"
        or proof.get("commit") != commit or not isinstance(proof.get("revision"), str)
        or not proof["revision"]
    ):
        raise ValueError("Merged-PR payload provenance could not be verified")
    return proof


def require_publish_ready(
    qpath: pathlib.Path, repo_root: pathlib.Path, now=None, *, approved_revision: str | None = None,
) -> dict:
    from batch_readiness import report

    result = report(qpath, repo_root, now=now, require_future=True)
    if result.get("ready") is not True:
        raise ValueError(f"{qpath}: publish readiness failed: {json.dumps(result, sort_keys=True)}")
    if approved_revision is not None and result.get("revision") != approved_revision:
        raise ValueError("Queue or media changed after merged-PR verification; renew approval before publishing")
    return result


def response_post_id(payload: object) -> str | None:
    """Extract one post ID without mistaking media or integration IDs for posts."""
    def ids(value):
        if isinstance(value, list):
            return [post_id for item in value for post_id in ids(item)]
        if not isinstance(value, dict):
            return []
        for key in ("postId", "post_id"):
            found = value.get(key)
            if isinstance(found, (str, int)) and not isinstance(found, bool) and str(found).strip():
                return [str(found)]
        nested = [post_id for key in ("posts", "post", "data", "results") for post_id in ids(value.get(key))]
        if nested:
            return nested
        for key in ("id", "_id"):
            found = value.get(key)
            if isinstance(found, (str, int)) and not isinstance(found, bool) and str(found).strip():
                return [str(found)]
        return []

    found = set(ids(payload))
    return found.pop() if len(found) == 1 else None


def archive_queue_file(qpath: pathlib.Path, posted_dir: pathlib.Path) -> pathlib.Path:
    """Move queue file to posted/, avoiding same-name archive collisions."""
    target = posted_dir / qpath.name
    if not target.exists():
        qpath.rename(target)
        return target


    run_id = os.environ.get("GITHUB_RUN_ID") or str(int(time.time()))
    target = posted_dir / f"{qpath.stem}-run-{run_id}{qpath.suffix}"
    qpath.rename(target)
    print(f"  ! archive {posted_dir / qpath.name} already exists; moved queue to {target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read delivery receipts without contacting providers.")
    parser.add_argument("queue_file", type=pathlib.Path)
    parser.add_argument("--repo-root", type=pathlib.Path, default=pathlib.Path(os.environ.get("LAYER8_DATA_ROOT") or pathlib.Path.cwd()))
    args = parser.parse_args()
    queue_path = args.queue_file if args.queue_file.is_absolute() else args.repo_root / args.queue_file
    posts = json.loads(queue_path.read_text(encoding="utf-8"))
    print(json.dumps({"posts": delivery_status(posts, args.repo_root)}, indent=2))
