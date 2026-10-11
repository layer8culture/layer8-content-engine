#!/usr/bin/env python3
"""Explicitly refresh selected delivery evidence; never submit or modify provider posts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import publish_helpers


CACHE_VERSION = 1
RECEIPT_STATES = publish_helpers.ACCEPTED_STATUSES | publish_helpers.UNCERTAIN_STATUSES | {"failed", "skipped"}
LEGACY_FIELDS = {
    "id", "account", "platform", "format", "schedule_time", "scheduled", "delivery_status",
    "publisher", "postiz_post_id", "ghl_post_id", "provider_status", "postiz_status",
    "delivery_mode", "visibility", "requested_visibility", "skip_reason", "skip_detail",
    "recorded_at", "workflow_url", "commit",
}


class RefreshError(RuntimeError):
    """A refresh failed; existing local evidence must remain intact."""


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise RefreshError("Expected an exact Git commit SHA")
    return value.lower()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RefreshError("Evidence timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RefreshError("Evidence timestamp is invalid") from None
    if parsed.utcoffset() is None:
        raise RefreshError("Evidence timestamp must include its UTC offset")
    return parsed.astimezone(timezone.utc)


def _command(args: list[str], root: Path, *, allowed=(0,), timeout=90) -> str:
    try:
        result = subprocess.run(
            args, cwd=str(root), capture_output=True, text=True, encoding="utf-8",
            errors="strict", timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise RefreshError(f"{args[0]} read timed out; previous delivery cache retained") from None
    except OSError:
        raise RefreshError(f"Unable to execute {args[0]}; check the local installation") from None
    except UnicodeError:
        raise RefreshError(f"{args[0]} returned unreadable output; previous delivery cache retained") from None
    if result.returncode not in allowed:
        # Git/GH stderr can contain authenticated remote URLs. Do not expose it.
        operation = "fetch" if "fetch" in args else "read"
        raise RefreshError(f"{args[0]} {operation} failed (exit {result.returncode}); check repository access")
    return result.stdout


def _gh(root: Path, args: list[str]) -> object:
    if args and args[0] == "api":
        args = [*args, "--hostname", "github.com"]
    raw = _command(["gh", *args], root)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RefreshError("GitHub returned invalid JSON; previous delivery cache retained") from None


def _repository(root: Path) -> str:
    origin = _command(["git", "--no-pager", "remote", "get-url", "origin"], root).strip()
    parsed = urlsplit(origin)
    scp = re.fullmatch(r"(?:[^@/]+@)?github\.com:([^/]+/[^/]+)", origin)
    candidate = parsed.path.strip("/") if parsed.hostname == "github.com" else scp.group(1) if scp else None
    if candidate:
        candidate = candidate.removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", candidate):
            raise RefreshError("Origin does not identify a valid GitHub repository")
    args = ["repo", "view", *([f"https://github.com/{candidate}"] if candidate else []), "--json", "nameWithOwner"]
    value = _gh(root, args)
    name = value.get("nameWithOwner") if isinstance(value, dict) else None
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise RefreshError("GitHub repository identity is unavailable")
    if candidate and name.casefold() != candidate.casefold():
        raise RefreshError("GitHub repository identity does not match origin")
    return name


def _receipt(record: object, selected: set[str], *, legacy=False) -> dict:
    if not isinstance(record, dict) or not isinstance(record.get("id"), str):
        raise RefreshError("Remote delivery evidence must identify one post")
    if record["id"] not in selected:
        raise RefreshError("Remote evidence does not belong to the selected posts")
    if "scheduled" in record and not isinstance(record["scheduled"], bool):
        raise RefreshError("Remote delivery evidence has an invalid scheduled flag")
    if legacy:
        return {key: value for key, value in record.items() if key in LEGACY_FIELDS}
    if not isinstance(record.get("event_id"), str) or not record["event_id"]:
        raise RefreshError("Remote immutable receipt has no event identifier")
    if not isinstance(record.get("fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["fingerprint"]):
        raise RefreshError("Remote immutable receipt has no valid content fingerprint")
    _timestamp(record.get("recorded_at"))
    state = publish_helpers.record_status(record)
    if state not in RECEIPT_STATES:
        raise RefreshError("Remote immutable receipt has an unsupported delivery state")
    if state in publish_helpers.ACCEPTED_STATUSES:
        provider_id = record.get("postiz_post_id") or record.get("ghl_post_id")
        if not isinstance(provider_id, str) or not provider_id.strip() or record.get("scheduled") is False:
            raise RefreshError("Remote acceptance receipt has no verifiable provider identifier")
    return dict(record)


def _remote_evidence(root: Path, commit: str, selected: set[str]) -> tuple[list[dict], list[dict]]:
    paths: set[str] = set()
    ids = sorted(selected)
    for offset in range(0, len(ids), 40):
        args = ["git", "--no-pager", "grep", "-l", "-z", "-F"]
        for post_id in ids[offset:offset + 40]:
            for encoded in {json.dumps(post_id), json.dumps(post_id, ensure_ascii=False)}:
                args.extend(["-e", f'"id": {encoded}', "-e", f'"id":{encoded}'])
        raw = _command([*args, commit, "--", "posted/receipts/"], root, allowed=(0, 1))
        for entry in raw.split("\0"):
            if not entry:
                continue
            prefix = f"{commit}:"
            if not entry.startswith(prefix):
                raise RefreshError("Unexpected receipt search result")
            path = entry[len(prefix):]
            parts = PurePosixPath(path).parts
            if parts[:2] != ("posted", "receipts") or ".." in parts or not path.endswith(".json"):
                raise RefreshError("Receipt search returned a path outside immutable receipts")
            paths.add(path)
    receipts = []
    for path in sorted(paths):
        raw = _command(["git", "--no-pager", "show", f"{commit}:{path}"], root)
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            raise RefreshError("Selected remote receipt is malformed JSON") from None
        if isinstance(record, dict) and record.get("id") not in selected:
            continue
        receipts.append(_receipt(record, selected))

    legacy = []
    exists = _command(
        ["git", "--no-pager", "ls-tree", "--name-only", commit, "--", "posted/log.json"], root,
    ).strip()
    if exists:
        raw = _command(["git", "--no-pager", "show", f"{commit}:posted/log.json"], root)
        try:
            log = json.loads(raw)
        except json.JSONDecodeError:
            raise RefreshError("Remote legacy log is malformed JSON") from None
        if not isinstance(log, list) or any(not isinstance(row, dict) for row in log):
            raise RefreshError("Remote legacy log must contain a list of records")
        legacy = [_receipt(row, selected, legacy=True) for row in log if row.get("id") in selected]
    return receipts, legacy


def _approval(root: Path, repository: str, queue: str, posts: list[dict], current: dict | None) -> dict | None:
    if current is None:
        return None
    if not isinstance(current, dict):
        raise RefreshError("Approval metadata must be an object")
    fields = {"state", "pr_number", "pr_url", "head_sha", "merge_sha", "revision", "approved_at"}
    updated = {key: current[key] for key in fields if key in current}
    number = current.get("pr_number")
    known_sha = current.get("merge_sha") or current.get("head_sha")
    if number is None and known_sha:
        associations = _gh(root, ["api", f"repos/{repository}/commits/{_sha(known_sha)}/pulls"])
        if not isinstance(associations, list):
            raise RefreshError("GitHub returned invalid PR associations")
        numbers = {
            row.get("number") for row in associations if isinstance(row, dict)
            and isinstance(row.get("base"), dict) and row["base"].get("ref") == "main"
        }
        if len(numbers) > 1:
            raise RefreshError("Multiple PRs match the supplied revision; select the approval PR explicitly")
        number = next(iter(numbers), None)
    if number is None:
        if known_sha:
            updated.update(state="unknown", detail="No approval PR could be confirmed for the supplied revision.")
        return updated
    if isinstance(number, bool) or not str(number).isdigit() or int(number) < 1:
        raise RefreshError("Approval PR number is invalid")
    pr = _gh(root, [
        "pr", "view", str(number), "--repo", f"github.com/{repository}", "--json",
        "number,url,state,mergedAt,mergeCommit,headRefOid,baseRefName,isCrossRepository,files",
    ])
    if not isinstance(pr, dict) or pr.get("baseRefName") != "main" or pr.get("isCrossRepository") is not False:
        raise RefreshError("Approval PR is not a same-repository main-target PR")
    if pr.get("number") != int(number):
        raise RefreshError("GitHub returned a different approval PR")
    files = pr.get("files")
    if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
        raise RefreshError("Approval PR changed-file evidence is unavailable")
    references = {queue}
    for post in posts:
        visual = post.get("visual") or {}
        references.update(
            str(path).replace("\\", "/") for path in (
                *(visual.get("files") or []), visual.get("file"), visual.get("cover"),
            ) if path
        )
    if not references.intersection(str(item.get("path")) for item in files):
        raise RefreshError("Approval PR does not reference the selected queue or its media")
    head = _sha(pr.get("headRefOid"))
    state = str(pr.get("state") or "").upper()
    if state not in {"OPEN", "CLOSED", "MERGED"}:
        raise RefreshError("Approval PR state is unavailable")
    updated.update(pr_number=int(number), pr_url=f"https://github.com/{repository}/pull/{number}")
    if current.get("head_sha") and _sha(current["head_sha"]) != head:
        updated.update(state="stale", remote_head_sha=head, detail="The PR head changed; the saved approval revision is stale.")
        updated.pop("merge_sha", None)
        return updated
    updated["head_sha"] = head
    if state == "MERGED":
        merge = pr.get("mergeCommit")
        merge_sha = _sha(merge.get("oid") if isinstance(merge, dict) else None)
        if current.get("merge_sha") and _sha(current["merge_sha"]) != merge_sha:
            raise RefreshError("Approval merge identity changed unexpectedly")
        _timestamp(pr.get("mergedAt"))
        updated.update(state="merged", merge_sha=merge_sha, merged_at=pr["mergedAt"])
    else:
        updated.update(state="awaiting_approval" if state == "OPEN" else "closed")
        updated.pop("merge_sha", None)
    return updated


def _workflow_value(run: object, repository: str) -> dict:
    if not isinstance(run, dict) or not isinstance(run.get("id"), int) or isinstance(run["id"], bool):
        raise RefreshError("Publish workflow response is invalid")
    path = str(run.get("path") or "").split("@", 1)[0]
    if path != ".github/workflows/publish.yml":
        raise RefreshError("Receipt points to a different workflow")
    if run.get("status") not in {"queued", "in_progress", "completed", "requested", "waiting", "pending"}:
        raise RefreshError("Publish workflow status is invalid")
    return {
        "id": run["id"], "url": f"https://github.com/{repository}/actions/runs/{run['id']}",
        "status": run["status"], "conclusion": run.get("conclusion"), "head_sha": _sha(run.get("head_sha")),
        **{key: run[key] for key in ("created_at", "updated_at", "run_attempt", "event") if key in run},
    }


def _workflow(root: Path, repository: str, approval: dict | None, receipts: list[dict], fingerprints: dict[str, str]) -> dict | None:
    relevant = [
        record for record in receipts
        if record.get("fingerprint") == fingerprints.get(record["id"]) and record.get("workflow_url")
    ]
    for record in sorted(relevant, key=lambda row: _timestamp(row["recorded_at"]), reverse=True):
        match = re.fullmatch(
            rf"https://github\.com/{re.escape(repository)}/actions/runs/(\d+)",
            str(record["workflow_url"]),
        )
        if not match:
            raise RefreshError("Selected receipt has a workflow URL outside the source repository")
        run = _gh(root, ["api", f"repos/{repository}/actions/runs/{match.group(1)}"])
        if not isinstance(run, dict) or run.get("id") != int(match.group(1)):
            raise RefreshError("GitHub returned a different publish workflow run")
        return _workflow_value(run, repository)
    commit = (approval.get("merge_sha") or approval.get("head_sha")) if approval and approval.get("state") != "stale" else None
    if not commit:
        return None
    payload = _gh(root, [
        "api", "--method", "GET", f"repos/{repository}/actions/workflows/publish.yml/runs",
        "-f", f"head_sha={_sha(commit)}", "-f", "per_page=100",
    ])
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list) or any(not isinstance(row, dict) for row in runs):
        raise RefreshError("Publish workflow listing is invalid")
    # A dispatch at the same SHA can target another queue. Without a matching
    # receipt, only the approval commit's push run establishes batch relevance.
    matching = [run for run in runs if run.get("head_sha") == commit and run.get("event") == "push"]
    if not matching:
        return {"status": "pending", "conclusion": None, "head_sha": commit, "url": None,
                "detail": "No matching publish workflow is visible yet."}
    run = max(matching, key=lambda row: (str(row.get("created_at") or ""), row.get("run_attempt", 1), row.get("id", 0)))
    return _workflow_value(run, repository)


def _cache_path(root: Path, queue: str) -> Path:
    return root / ".local" / "remote-delivery" / hashlib.sha256(queue.encode("utf-8")).hexdigest() / "snapshot.json"


def _write_cache(root: Path, snapshot: dict) -> Path:
    target = _cache_path(root, snapshot["queue"])
    if not target.resolve().is_relative_to(root):
        raise RefreshError("Delivery cache directory escapes the data repository")
    cache_root = target.parent.parent
    cache_root.mkdir(parents=True, exist_ok=True)
    ignore = cache_root / ".gitignore"
    if not ignore.resolve().is_relative_to(root):
        raise RefreshError("Delivery cache ignore path escapes the data repository")
    try:
        with ignore.open("x", encoding="utf-8") as handle:
            handle.write("*\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if ignore.read_text(encoding="utf-8").strip() != "*":
            raise RefreshError("Remote delivery cache has unexpected ignore rules; existing files were not changed")
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(f".{uuid.uuid4().hex}.pending")
    payload = {**snapshot, "integrity_sha256": _digest(snapshot)}
    try:
        with pending.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)
    return target


def refresh(queue_path: Path, repo_root: Path, approval: dict | None = None) -> dict:
    """Fetch origin/main and cache selected evidence without updating user files."""
    root = Path(repo_root).resolve()
    path = Path(queue_path)
    path = (path if path.is_absolute() else root / path).resolve()
    if path.parent != (root / "queue").resolve() or path.suffix != ".json":
        raise RefreshError("Select one JSON file directly inside the data repository's queue directory")
    posts = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(posts, list) or not posts or any(
        not isinstance(post, dict) or not isinstance(post.get("id"), str) or not post["id"]
        or not isinstance(post.get("visual"), dict) for post in posts
    ):
        raise RefreshError("Selected queue must contain identifiable posts with visual objects")
    selected = {post["id"] for post in posts}
    if len(selected) != len(posts):
        raise RefreshError("Selected queue contains duplicate post IDs")
    for post in posts:
        visual = post["visual"]
        paths = visual.get("files") if post.get("format") == "carousel" else [visual.get("file")]
        if paths is not None and not isinstance(paths, list):
            raise RefreshError("Selected media paths must be an ordered list")
        for raw in paths or []:
            if not raw:
                continue
            if not isinstance(raw, str):
                raise RefreshError("Selected media paths must be strings")
            media = publish_helpers.local_media_path(raw)
            media = (media if media.is_absolute() else root / media).resolve()
            if not media.is_relative_to(root / "assets"):
                raise RefreshError("Selected media must remain inside the data repository's assets directory")
    queue = path.relative_to(root).as_posix()
    fingerprints = {post["id"]: publish_helpers.post_fingerprint(post, root) for post in posts}
    _command(
        ["git", "--no-pager", "fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main"],
        root, timeout=180,
    )
    commit = _sha(_command(
        ["git", "--no-pager", "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"], root,
    ).strip())
    repository = _repository(root)
    receipts, legacy = _remote_evidence(root, commit, selected)
    for record in [*receipts, *legacy]:
        if record.get("workflow_url") and not re.fullmatch(
            rf"https://github\.com/{re.escape(repository)}/actions/runs/\d+", str(record["workflow_url"]),
        ):
            raise RefreshError("Selected evidence has a workflow URL outside the source repository")
    updated_approval = _approval(root, repository, queue, posts, approval)
    workflow = _workflow(root, repository, updated_approval, receipts, fingerprints)
    observed_at = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "schema_version": CACHE_VERSION, "queue": queue, "observed_at": observed_at,
        "source_commit": commit, "repository": repository, "fingerprints": fingerprints,
        "receipts": receipts, "legacy_records": legacy, "approval": updated_approval,
        "workflow": workflow,
    }
    target = _write_cache(root, snapshot)
    return {
        "queue": queue, "approval": updated_approval, "workflow": workflow,
        "observed_at": observed_at, "source_commit": commit,
        "receipt_count": len(receipts), "legacy_record_count": len(legacy),
        "cache_path": target.relative_to(root).as_posix(),
    }


def cached_evidence(repo_root: Path, posts: list[dict]) -> dict:
    """Read validated snapshots only; safe for GET with no Git/GH/provider calls."""
    root = Path(repo_root).resolve()
    selected = {post["id"]: post for post in posts}
    records: list[dict] = []
    contexts: dict[str, dict] = {}
    fingerprints: dict[str, str] = {}
    remote_event_ids: set[str] = set()
    cache_root = root / ".local" / "remote-delivery"
    if not cache_root.resolve().is_relative_to(root):
        raise RefreshError("Delivery cache directory escapes the data repository")
    snapshots = []
    for path in cache_root.glob("*/snapshot.json"):
        if not path.resolve().is_relative_to(cache_root.resolve()):
            raise RefreshError("Delivery cache path escapes the cache directory")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RefreshError("Delivery cache is not an object; refresh it explicitly")
        integrity = value.pop("integrity_sha256", None)
        if integrity != _digest(value) or value.get("schema_version") != CACHE_VERSION:
            raise RefreshError("Delivery cache integrity is invalid; refresh it explicitly")
        _sha(value.get("source_commit"))
        _timestamp(value.get("observed_at"))
        if not isinstance(value.get("fingerprints"), dict) or not isinstance(value.get("receipts"), list) or not isinstance(value.get("legacy_records"), list):
            raise RefreshError("Delivery cache has invalid evidence collections")
        if any(
            not isinstance(post_id, str) or not isinstance(fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            for post_id, fingerprint in value["fingerprints"].items()
        ):
            raise RefreshError("Delivery cache has invalid selected-post fingerprints")
        if value.get("workflow") is not None and not isinstance(value["workflow"], dict):
            raise RefreshError("Delivery cache has invalid workflow metadata")
        if value.get("approval") is not None and not isinstance(value["approval"], dict):
            raise RefreshError("Delivery cache has invalid approval metadata")
        snapshots.append(value)
    for snapshot in sorted(snapshots, key=lambda row: _timestamp(row["observed_at"])):
        ids = set(snapshot["fingerprints"])
        for legacy in snapshot["legacy_records"]:
            record = _receipt(legacy, ids, legacy=True)
            if record["id"] in selected:
                records.append(record)
        for receipt in snapshot["receipts"]:
            record = _receipt(receipt, ids)
            if record["id"] in selected:
                records.append(record)
                remote_event_ids.add(record["event_id"])
        for post_id in ids.intersection(selected):
            if post_id not in fingerprints:
                fingerprints[post_id] = publish_helpers.post_fingerprint(selected[post_id], root)
            if snapshot["fingerprints"][post_id] == fingerprints[post_id]:
                contexts[post_id] = {
                    "workflow": snapshot.get("workflow"), "approval": snapshot.get("approval"),
                    "observed_at": snapshot["observed_at"], "source_commit": snapshot["source_commit"],
                }
    return {"records": records, "contexts": contexts, "fingerprints": fingerprints, "remote_event_ids": remote_event_ids}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(os.environ.get("LAYER8_DATA_ROOT") or Path.cwd()))
    parser.add_argument("--approval-json", type=Path)
    args = parser.parse_args()
    try:
        approval_value = json.loads(args.approval_json.read_text(encoding="utf-8")) if args.approval_json else None
        print(json.dumps(refresh(args.queue_file, args.repo_root, approval_value), indent=2))
    except (RefreshError, OSError, ValueError) as exc:
        parser.exit(1, f"Delivery refresh failed: {exc}\n")
