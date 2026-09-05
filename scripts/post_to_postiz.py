#!/usr/bin/env python3
"""Publish approved posts to Postiz.

Runs on merge to main. Reads queue/<date>.json, uploads media, schedules each
post via the Postiz public API, then moves completed queues to posted/. Each
attempt writes immutable events to posted/receipts/; posted/log.json is read-only
legacy history. Unknown submissions are reconciled, never blindly retried.
Direct invocation requires --commit <approved full SHA>; GitHub Actions may
instead supply GITHUB_SHA. Both paths verify merged-PR provenance themselves.

Format-aware (post["format"], default "single"):
  * single   -> one image, IG post_type "post"
  * carousel -> N images (visual.files), IG CAROUSEL (post_type "post")
  * reel     -> one mp4 (visual.file), IG REELS (a single video with post_type
                "post" is auto-published as a Reel; missing video blocks delivery).
                Optional post["trial_reel"] -> is_trial_reel.
  * story    -> one image/video, IG post_type "story"

Growth extras: post["first_comment"] is posted as the first comment (Postiz sends
extra value[] entries as comments); set post["hashtags_in_first_comment"] to keep
the caption clean and drop the tags into that first comment instead.
post["collaborators"] (list of IG handles) maps to IG collab tags.

Env vars: POSTIZ_URL, POSTIZ_API_KEY
Optional env: LOFI_IG_CHANNEL_ID, the Postiz channel ID for the lofi
(Layer8CultureRadio) Instagram account; overrides its INTEGRATIONS placeholder.
Optional env: TIKTOK_CHANNEL_ID, the Postiz channel ID for the layer8culture
TikTok account; fills its INTEGRATIONS placeholder (unset -> TikTok posts skipped).
Optional env: YOUTUBE_LAYER8_CHANNEL_ID / YOUTUBE_LOFI_CHANNEL_ID, the Postiz
channel IDs for each brand's YouTube channel; fill their INTEGRATIONS placeholders
(unset -> that brand's YouTube Shorts are skipped, not errored).
Optional env: DEALLAB_IG_CHANNEL_ID, the Postiz channel ID for The Real Estate
Deal Lab Instagram account. Unset -> Deal Lab posts are skipped, not errored.
Note: integration IDs map your accounts/platforms to Postiz channels.
Fill INTEGRATIONS after connecting your accounts in the Postiz UI
(Settings -> API shows channel IDs).
"""
import argparse
import json
import os
import sys
import pathlib
import uuid
import requests
from datetime import datetime, timedelta, timezone
from typing import Any

from publish_helpers import (
    VIDEO_EXTS,
    append_new_log_records,
    append_receipt,
    archive_queue_file,
    build_caption,
    ACCEPTED_STATUSES,
    UNCERTAIN_STATUSES,
    latest_records,
    load_delivery_records,
    load_log,
    missing_local_paths,
    post_fingerprint,
    record_status,
    require_approved_payload,
    require_publish_ready,
    response_post_id,
    resolve_local_paths,
    scheduled_post_ids,
)


POSTIZ_URL = os.environ.get("POSTIZ_URL", "").rstrip("/")
HEADERS = {"Authorization": os.environ.get("POSTIZ_API_KEY", "")}


def request_headers() -> dict:
    if not POSTIZ_URL:
        raise ValueError("Missing POSTIZ_URL")
    if not HEADERS.get("Authorization"):
        raise ValueError("Missing POSTIZ_API_KEY")
    return HEADERS

# account+platform -> Postiz integration (channel) ID. FILL THESE IN.
# Note: ("layer8culture", "tiktok") is active. The layer8culture pipeline now
# generates TikTok videos (see calendar/topics.md + scripts/generation-prompt.md).
# Its channel ID is supplied via the TIKTOK_CHANNEL_ID secret below (kept out of
# code). YouTube is active for BOTH brands. The pipelines cross-post reels as
# YouTube Shorts; channel IDs come from the YOUTUBE_*_CHANNEL_ID secrets below.
# ("lofi", "tiktok") stays provisioning-only. Nothing generates for lofi TikTok
# unless topics-lofi.md explicitly asks for it.
INTEGRATIONS = {
    ("layer8culture", "tiktok"): "REPLACE_ME",
    ("layer8culture", "instagram"): "cmqd9915w0001o5717h436ivp",
    ("layer8culture", "x"): "REPLACE_ME",
    ("layer8culture", "youtube"): "REPLACE_ME",
    ("lofi", "instagram"): "cmqgzb1xv000qo571zuz5lqfm",
    ("lofi", "x"): "REPLACE_ME",
    ("lofi", "tiktok"): "REPLACE_ME",
    ("lofi", "youtube"): "REPLACE_ME",
    ("deallab", "instagram"): "REPLACE_ME",
}

# The lofi (Layer8CultureRadio) Instagram channel is wired above with its Postiz
# integration ID. The LOFI_IG_CHANNEL_ID secret, if set, overrides it without a code
# change (e.g. if the channel is re-connected and gets a new ID).
_lofi_ig = os.environ.get("LOFI_IG_CHANNEL_ID")
if _lofi_ig:
    INTEGRATIONS[("lofi", "instagram")] = _lofi_ig

# The layer8culture TikTok channel ID is supplied via the TIKTOK_CHANNEL_ID secret
# (kept out of code). When unset the mapping stays REPLACE_ME and TikTok posts are
# skipped, not errored, so the engine ships TikTok posts only once the channel is
# connected in Postiz and the secret is set.
_tiktok_channel = os.environ.get("TIKTOK_CHANNEL_ID")
if _tiktok_channel:
    INTEGRATIONS[("layer8culture", "tiktok")] = _tiktok_channel

# YouTube channel IDs (one per brand) come from secrets, kept out of code. Unset ->
# REPLACE_ME -> those Shorts are skipped (not errored), so YouTube ships only once
# each channel is connected in Postiz and its secret is set. (Note: distinct from
# fetch_youtube.py's YT_CHANNEL_ID, which is the RSS channel id for "Now Live" promos.)
_yt_layer8 = os.environ.get("YOUTUBE_LAYER8_CHANNEL_ID")
if _yt_layer8:
    INTEGRATIONS[("layer8culture", "youtube")] = _yt_layer8
_yt_lofi = os.environ.get("YOUTUBE_LOFI_CHANNEL_ID")
if _yt_lofi:
    INTEGRATIONS[("lofi", "youtube")] = _yt_lofi

# The Real Estate Deal Lab is a client brand and must stay isolated from the
# Layer8Culture accounts. Its Postiz channel ID is supplied only via secret.
_deallab_ig = os.environ.get("DEALLAB_IG_CHANNEL_ID")
if _deallab_ig:
    INTEGRATIONS[("deallab", "instagram")] = _deallab_ig

# Per-platform base post settings required by the Postiz API. For Instagram the
# api requires settings.post_type ("post" or "story"); we add it per-format in
# platform_settings(). The others are placeholders for when those channels wire up.
#
# TikTok mirrors Postiz's TikTokDto. Every TikTok video this engine ships is
# AI-generated (Sora-2), so video_made_with_ai is disclosed (TikTok policy +
# honesty). content_posting_method is "UPLOAD": Postiz sends the video to the
# creator's TikTok inbox as a DRAFT (endpoint /post/publish/inbox/video/init/), so it
# appears under Drafts in the TikTok mobile app for you to review and publish by hand.
# This is the working flow for an UNAUDITED app. Finishing the post manually lets you
# publish PUBLICLY, whereas an unaudited DIRECT_POST can only post SELF_ONLY (private)
# and never lands in Drafts. privacy_level is IGNORED in UPLOAD mode but is still
# required by Postiz's TikTokDto validation, so we keep a valid value. Once the app
# passes TikTok's audit, you can auto-post directly instead: set content_posting_method
# back to "DIRECT_POST" and privacy_level to "PUBLIC_TO_EVERYONE" (here or per post via
# "tiktok_settings"). NOTE 1: TikTok pulls the video via PULL_FROM_URL, so the Postiz
# media domain must be verified as a URL property in the TikTok dev portal or posts fail
# with url_ownership_unverified. NOTE 2: TikTok caps inbox uploads at 5 PENDING drafts
# per 24h, so the generator keeps TikTok masters at <=5/day (scripts/generation-prompt.md)
# and main() enforces the same cap as a safety net (see TIKTOK_INBOX_CAP below).
PLATFORM_SETTINGS = {
    "instagram": {"post_type": "post"},  # base; story/reel adjust this below
    "tiktok": {
        "privacy_level": "SELF_ONLY",  # ignored in UPLOAD mode; kept for DTO validation
        "duet": True,
        "stitch": True,
        "comment": True,
        "autoAddMusic": "no",
        "brand_content_toggle": False,
        "brand_organic_toggle": False,
        "video_made_with_ai": True,
        "content_posting_method": "UPLOAD",  # -> TikTok inbox as a DRAFT (publish by hand)
    },
}

# TikTok caps its Drafts inbox at 5 PENDING uploads per 24h; a 6th UPLOAD is rejected
# (spam_risk_too_many_pending_share) and silently never reaches Drafts. The generator is
# meant to keep TikTok masters at <=5/day, but as a safety net we also enforce the cap
# here across persisted batches in every rolling 24h scheduling window; excess
# posts are explicitly skipped. DIRECT_POST TikTok posts publish directly and do
# NOT accumulate as pending drafts, so they are exempt.
TIKTOK_INBOX_CAP = 5

# YouTube uploads must carry a non-empty title (2-100 chars). We request type
# "public"; an unverified Google app forces every upload to PRIVATE regardless, so
# in practice Shorts land private until the user flips them in Studio (or the app is
# verified). YouTube caps the combined length of all tags at 500 chars.
YOUTUBE_TITLE_MAX = 100
YOUTUBE_TAGS_MAX = 500
FATAL_SKIP_REASONS = {
    "missing_media", "non_video_media", "postiz_error", "network_error",
    "unknown_submission", "revision_conflict", "provider_error",
}


def parse_postiz_datetime(value: str) -> datetime:
    """Parse an ISO datetime and normalize it to UTC."""
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_postiz_content(value: str | None) -> str:
    if not value:
        return ""
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def list_postiz_posts(start_dt: datetime, end_dt: datetime) -> list[dict]:
    r = requests.get(
        f"{POSTIZ_URL}/api/public/v1/posts",
        headers=request_headers(),
        params={"startDate": utc_iso(start_dt), "endDate": utc_iso(end_dt)},
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    posts = payload.get("posts") if isinstance(payload, dict) else None
    if not isinstance(posts, list) or any(not isinstance(post, dict) for post in posts):
        raise ValueError("Postiz returned an invalid post list; reconciliation stopped")
    return posts


def is_matching_existing_postiz_post(
    candidate: dict,
    integration_id: str,
    caption: str,
    schedule_time: str,
) -> bool:
    if str(candidate.get("state") or "").upper() not in {"QUEUE", "SCHEDULED", "PUBLISHED"}:
        return False
    integration = candidate.get("integration") or {}
    candidate_integration = integration.get("id") if isinstance(integration, dict) else None
    if (candidate_integration or candidate.get("integrationId")) != integration_id:
        return False
    if normalize_postiz_content(candidate.get("content")) != normalize_postiz_content(caption):
        return False
    publish_date = candidate.get("publishDate")
    if not publish_date:
        return False
    return parse_postiz_datetime(publish_date) == parse_postiz_datetime(schedule_time)


def find_existing_postiz_duplicate(
    integration_id: str,
    caption: str,
    schedule_time: str,
) -> dict | None:
    scheduled_at = parse_postiz_datetime(schedule_time)
    existing_posts = list_postiz_posts(
        scheduled_at - timedelta(hours=12),
        scheduled_at + timedelta(hours=12),
    )
    matches = [
        candidate for candidate in existing_posts
        if is_matching_existing_postiz_post(candidate, integration_id, caption, schedule_time)
    ]
    if len(matches) > 1:
        raise ValueError("Multiple matching Postiz posts; reconcile explicitly before retrying")
    return matches[0] if matches else None


def _youtube_title(post: dict) -> str:
    """A valid YouTube title (2-100 chars): youtube_title -> headline -> 1st line."""
    visual = post.get("visual") or {}
    candidates = [
        post.get("youtube_title"),
        visual.get("headline"),
        (post.get("text") or "").strip().split("\n", 1)[0],
    ]
    for c in candidates:
        title = (c or "").strip()
        if len(title) >= 2:
            return title[:YOUTUBE_TITLE_MAX]
    return "Layer8Culture"


def _youtube_tags(post: dict) -> list[dict]:
    """hashtags -> Postiz YoutubeTagsSettings, kept under the 500-char total."""
    tags, total = [], 0
    for t in (post.get("hashtags") or []):
        label = str(t).lstrip("#").strip()
        if not label:
            continue
        cost = len(label) + (2 if any(ch.isspace() for ch in label) else 0)
        if total + cost > YOUTUBE_TAGS_MAX:
            break
        tags.append({"value": label, "label": label})
        total += cost
    return tags


def youtube_settings(post: dict) -> dict:
    """Build Postiz's YoutubeSettingsDto for a YouTube (Shorts) upload.

    A per-post "youtube_settings" dict (e.g. type "unlisted") merges on top.
    """
    settings = {
        "title": _youtube_title(post),
        "type": "public",            # forced private until the Google app is verified
        "selfDeclaredMadeForKids": "no",
    }
    tags = _youtube_tags(post)
    if tags:
        settings["tags"] = tags
    overrides = post.get("youtube_settings")
    if isinstance(overrides, dict):
        settings.update(overrides)
    return settings


def platform_settings(post: dict, fmt: str) -> dict:
    """Build the Postiz per-platform settings for this post + resolved format.

    Instagram requires post_type ("post"/"story"); a single video sent as "post"
    is published as a Reel automatically. Optional growth flags pass through:
    trial_reel -> is_trial_reel (Reel shown to non-followers first), and
    collaborators -> IG collab tags.

    TikTok returns the TikTokDto defaults (content_posting_method "UPLOAD" -> the
    video is sent to the TikTok app inbox as a draft to publish by hand), with any
    per-post "tiktok_settings" dict merged on top. YouTube returns the
    YoutubeSettingsDto (title + type + tags).
    """
    platform = post["platform"]
    if platform == "tiktok":
        settings = dict(PLATFORM_SETTINGS.get("tiktok", {}))
        overrides = post.get("tiktok_settings")
        if isinstance(overrides, dict):
            settings.update(overrides)
        return settings
    if platform == "youtube":
        return youtube_settings(post)
    if platform != "instagram":
        return dict(PLATFORM_SETTINGS.get(platform, {}))
    settings = {"post_type": "story" if fmt == "story" else "post"}
    if fmt == "reel" and post.get("trial_reel"):
        settings["is_trial_reel"] = True
    collaborators = post.get("collaborators")
    if collaborators:
        settings["collaborators"] = [{"label": str(c).lstrip("@")} for c in collaborators]
    return settings


def is_tiktok_inbox_post(post: dict) -> bool:
    """True if the post is a TikTok upload that lands in the Drafts inbox.

    Only these count toward TikTok's 5-pending-drafts/24h cap. DIRECT_POST TikTok
    posts (used once the app is audited) publish directly and are exempt.
    """
    if post.get("platform") != "tiktok":
        return False
    settings = platform_settings(post, post.get("format", "single"))
    return settings.get("content_posting_method", "UPLOAD") == "UPLOAD"


def upload_media(filepath: str) -> dict:
    with open(filepath, "rb") as f:
        r = requests.post(
            f"{POSTIZ_URL}/api/public/v1/upload",
            headers=request_headers(),
            files={"file": f},
            timeout=120,
        )
    r.raise_for_status()
    return r.json()  # contains id + path


def postiz_response_metadata(payload: Any) -> dict:
    metadata = {}
    post_id = response_post_id(payload)
    if post_id:
        metadata["postiz_post_id"] = post_id
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("state")
        if status:
            metadata["postiz_status"] = str(status)
            metadata["provider_status"] = str(status)
        if payload.get("visibility") in {"public", "private", "unlisted"}:
            metadata["visibility"] = payload["visibility"]
    return metadata


def skip_result(
    reason: str,
    integration_id: str | None = None,
    detail: str | None = None,
) -> dict:
    result = {
        "scheduled": False,
        "delivery_status": "failed" if reason in FATAL_SKIP_REASONS else "skipped",
        "publisher": "postiz",
        "skip_reason": reason,
    }
    if reason == "unknown_submission":
        result["delivery_status"] = "unknown"
    if integration_id:
        result["integration_id"] = integration_id
    if detail:
        result["skip_detail"] = detail
    return result


def fatal_publish_failures(results: list[dict]) -> list[dict]:
    return [
        result
        for result in results
        if not result.get("scheduled")
        and result.get("skip_reason") in FATAL_SKIP_REASONS
    ]


def missing_media_detail(paths: list[str]) -> str | None:
    if not paths:
        return None
    return ", ".join(paths[:5]) + (" ..." if len(paths) > 5 else "")


def instagram_media_failure(post: dict, fmt: str, paths: list[str], missing: list[str]) -> str | None:
    visual = post.get("visual") or {}
    if not paths:
        return "no resolved media"
    if fmt == "carousel":
        declared = [path for path in (visual.get("files") or []) if path]
        if not declared:
            return "carousel has no visual.files"
        if missing or len(paths) != len(declared):
            detail = missing_media_detail(missing)
            return f"missing declared carousel media: {detail}" if detail else "missing declared carousel media"
    return None


def delivery_mode(post: dict) -> dict:
    settings = platform_settings(post, post.get("format", "single"))
    if post["platform"] == "tiktok":
        inbox = settings.get("content_posting_method") == "UPLOAD"
        return {
            "delivery_mode": "inbox" if inbox else "direct",
            "visibility": "private" if inbox or settings.get("privacy_level") == "SELF_ONLY" else "public",
        }
    if post["platform"] == "youtube":
        requested = settings.get("type", "public")
        return {
            "delivery_mode": "youtube_upload",
            "visibility": requested if requested in {"private", "unlisted"} else "unknown",
            "requested_visibility": requested,
        }
    return {"delivery_mode": "direct", "visibility": "unknown"}


def accepted_result(post: dict, metadata: dict, existing: bool = False) -> dict:
    state = str(metadata.get("provider_status") or metadata.get("postiz_status") or "").upper()
    if state in {"ERROR", "FAILED"}:
        return {**skip_result("postiz_error"), **metadata}
    if state == "DRAFT":
        return {**skip_result("unknown_submission", detail="Provider returned a draft rather than a scheduled post"), **metadata}
    status = "published" if state == "PUBLISHED" else "queued" if state in {"QUEUE", "SCHEDULED"} else "accepted"
    mode = {**delivery_mode(post), **{key: metadata[key] for key in ("visibility",) if key in metadata}}
    if status == "published" and mode["delivery_mode"] == "inbox":
        status = "inbox"
    elif status == "published" and mode["visibility"] == "private":
        status = "private"
    return {
        "scheduled": True, "delivery_status": status, "publisher": "postiz",
        **mode, **metadata, **({"reconciled": True} if existing else {}),
    }


def schedule(post: dict, before_submit=None, reconcile_only: bool = False,
             repo_root: pathlib.Path | None = None) -> dict:
    key = (post["account"], post["platform"])
    integration_id = INTEGRATIONS.get(key)
    if not integration_id or integration_id == "REPLACE_ME":
        print(f"  ! no integration mapped for {key}, skipping {post['id']}")
        if reconcile_only:
            return skip_result("unknown_submission", detail="Integration unavailable; earlier submission remains unresolved")
        return skip_result("missing_integration")

    fmt = post.get("format", "single")
    paths = resolve_local_paths(post, repo_root)
    missing = missing_local_paths(post, repo_root)
    is_video = bool(paths) and paths[0].lower().endswith(VIDEO_EXTS)

    if post["platform"] == "instagram":
        media_failure = instagram_media_failure(post, fmt, paths, missing)
        if media_failure:
            print(f"  ! Instagram post {post['id']} has invalid media: {media_failure}")
            return skip_result("missing_media", integration_id, media_failure)

    if post["platform"] in ("tiktok", "youtube"):
        label = post["platform"]
        if not paths:
            print(f"  ! {label} post {post['id']} has no resolved media, skipping")
            return skip_result("missing_media", integration_id)
        if not is_video:
            print(f"  ! {label} post {post['id']} media is not a video "
                  f"({', '.join(VIDEO_EXTS)}), skipping")
            return skip_result("non_video_media", integration_id, ", ".join(paths[:5]))

    if fmt == "reel" and not is_video:
        return skip_result("non_video_media", integration_id, "A reel requires a finalized video")

    caption, first_comment = build_caption(post)
    existing = find_existing_postiz_duplicate(
        integration_id,
        caption,
        post["schedule_time"],
    )
    if existing:
        metadata = postiz_response_metadata(existing)
        if not metadata.get("postiz_post_id"):
            return skip_result("unknown_submission", integration_id, "Matching post has no identifier")
        return accepted_result(post, {"integration_id": integration_id, **metadata}, existing=True)
    if reconcile_only:
        return skip_result(
            "unknown_submission", integration_id,
            "No unique provider match for an earlier uncertain attempt. No new post was submitted.",
        )

    media = []
    for p in paths:
        m = upload_media(p)
        media.append({"id": m["id"], "path": m["path"]})

    value = [{"content": caption, "image": media}]
    if first_comment:
        value.append({"content": first_comment, "image": []})

    payload = {
        "type": "schedule",
        "date": post["schedule_time"],
        "shortLink": False,
        "tags": [],
        "posts": [{
            "integration": {"id": integration_id},
            "value": value,
            "settings": platform_settings(post, fmt),
        }],
    }
    if before_submit:
        before_submit({"integration_id": integration_id, "media_ids": [item["id"] for item in media]})
    r = requests.post(f"{POSTIZ_URL}/api/public/v1/posts",
                      headers={**request_headers(), "Content-Type": "application/json"},
                      json=payload, timeout=60)
    if r.ok:
        try:
            response_payload = r.json()
        except ValueError:
            response_payload = {}
        metadata = postiz_response_metadata(response_payload)
        if not metadata.get("postiz_post_id"):
            existing = find_existing_postiz_duplicate(integration_id, caption, post["schedule_time"])
            if not existing or not postiz_response_metadata(existing).get("postiz_post_id"):
                return skip_result("unknown_submission", integration_id, "Provider accepted request without a verifiable post ID")
            metadata = postiz_response_metadata(existing)
        return accepted_result(post, {"integration_id": integration_id, **metadata})
    result = skip_result("unknown_submission" if r.status_code >= 500 else "postiz_error", integration_id)
    result["postiz_status_code"] = r.status_code
    return result

def inbox_limit_reached(post: dict, records: list[dict]) -> bool:
    if not is_tiktok_inbox_post(post):
        return False
    integration_id = INTEGRATIONS.get((post["account"], post["platform"]))
    candidate = parse_postiz_datetime(post["schedule_time"])
    times = [candidate]
    for previous in latest_records(records).values():
        if str(previous.get("id")) == str(post["id"]):
            continue
        previous_integration = previous.get("integration_id") or INTEGRATIONS.get(
            (previous.get("account"), previous.get("platform"))
        )
        if previous_integration != integration_id:
            continue
        if previous.get("delivery_mode") != "inbox" and not (
            previous.get("platform") == "tiktok" and "delivery_mode" not in previous
        ):
            continue
        if record_status(previous) not in ACCEPTED_STATUSES | UNCERTAIN_STATUSES:
            continue
        moment = parse_postiz_datetime(previous["schedule_time"])
        if abs(moment - candidate) < timedelta(days=1):
            times.append(moment)
    times.sort()
    for index, start in enumerate(times):
        window = [moment for moment in times[index:] if moment - start < timedelta(days=1)]
        if candidate in window and len(window) > TIKTOK_INBOX_CAP:
            return True
    return False


def main(queue_file: str, repo_root: pathlib.Path | None = None, *, commit: str | None = None) -> list[dict]:
    root = pathlib.Path(repo_root or os.environ.get("LAYER8_DATA_ROOT") or pathlib.Path.cwd()).resolve()
    qpath = pathlib.Path(queue_file)
    qpath = (qpath if qpath.is_absolute() else root / qpath).resolve()
    approval = require_approved_payload(qpath, root, commit)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    require_publish_ready(qpath, root, approved_revision=approval["revision"])
    posted_dir = root / "posted"
    posted_dir.mkdir(exist_ok=True)
    records = load_delivery_records(root)
    results = []
    for p in posts:
        fingerprint = post_fingerprint(p, root)
        previous = latest_records(records).get(str(p["id"]), {})
        previous_status = record_status(previous)
        submitted = False
        context = {
            "attempt_id": uuid.uuid4().hex,
            "integration_id": INTEGRATIONS.get((p["account"], p["platform"])),
            "commit": approval["commit"],
            **delivery_mode(p),
        }

        def before_submit(metadata):
            nonlocal submitted
            require_publish_ready(qpath, root, approved_revision=approval["revision"])
            context.update(metadata)
            record = append_receipt(root, p, {
                "publisher": "postiz", "scheduled": False, "delivery_status": "submitting",
                **context,
            }, fingerprint)
            records.append(record)
            submitted = True

        if previous_status in ACCEPTED_STATUSES | UNCERTAIN_STATUSES and previous.get("fingerprint") != fingerprint:
            result = skip_result("revision_conflict", detail="Existing delivery belongs to another or legacy revision; reconcile before replacing it.")
        elif previous_status in ACCEPTED_STATUSES:
            results.append(previous)
            continue
        elif previous_status not in UNCERTAIN_STATUSES and inbox_limit_reached(p, records):
            result = {**skip_result("tiktok_inbox_cap"), **delivery_mode(p)}
        else:
            try:
                result = schedule(
                    p, before_submit=before_submit,
                    reconcile_only=previous_status in UNCERTAIN_STATUSES, repo_root=root,
                )
            except (requests.RequestException, ValueError, OSError) as exc:
                uncertain = submitted or previous_status in UNCERTAIN_STATUSES
                result = skip_result(
                    "unknown_submission" if uncertain else "provider_error",
                    detail=f"{type(exc).__name__}: provider operation did not complete; inspect the receipt before retrying",
                )
        result = {**context, **result}
        record = append_receipt(root, p, result, fingerprint)
        records.append(record)
        results.append(record)
        print(json.dumps({"id": p["id"], **result}, sort_keys=True))

    failures = fatal_publish_failures(results)
    if failures:
        print(f"Fatal publish failures in {queue_file}; leaving queue file unarchived.")
        for failure in failures:
            print(
                f"  ! {failure.get('id')}: {failure.get('skip_reason')}"
                f"{' - ' + failure['skip_detail'] if failure.get('skip_detail') else ''}"
            )
        sys.exit(1)

    archive_queue_file(qpath, posted_dir)
    print(json.dumps({"posts": results}, sort_keys=True))
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file")
    parser.add_argument("--repo-root", type=pathlib.Path)
    parser.add_argument("--commit", help="Full immutable commit SHA of the reviewed, merged payload")
    args = parser.parse_args()
    main(args.queue_file, repo_root=args.repo_root, commit=args.commit)
