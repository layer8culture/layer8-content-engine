#!/usr/bin/env python3
"""Publish approved posts to Postiz.

Runs on merge to main. Reads queue/<date>.json, uploads media, schedules each
post via the Postiz public API, then moves the file to posted/ and appends to
posted/log.json.

Format-aware (post["format"], default "single"):
  * single   -> one image, IG post_type "post"
  * carousel -> N images (visual.files), IG CAROUSEL (post_type "post")
  * reel     -> one mp4 (visual.file), IG REELS (a single video with post_type
                "post" is auto-published as a Reel; degrades to an image post if
                the mp4 is missing). Optional post["trial_reel"] -> is_trial_reel.
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
import json
import os
import sys
import pathlib
import requests
from datetime import datetime, timedelta, timezone
from typing import Any

from publish_helpers import (
    VIDEO_EXTS,
    append_new_log_records,
    archive_queue_file,
    build_caption,
    load_log,
    resolve_local_paths,
    scheduled_post_ids,
)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"Missing required env var {name}. Set the POSTIZ_URL and "
            f"POSTIZ_API_KEY GitHub secrets before merging an approval PR."
        )
    return val


POSTIZ_URL = _require_env("POSTIZ_URL").rstrip("/")
HEADERS = {"Authorization": _require_env("POSTIZ_API_KEY")}

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
# here: only the first 5 inbox-bound TikTok posts in a run are scheduled, the rest are
# skipped (not errored). DIRECT_POST TikTok posts (post-audit) publish directly and do
# NOT accumulate as pending drafts, so they are exempt.
TIKTOK_INBOX_CAP = 5

# YouTube uploads must carry a non-empty title (2-100 chars). We request type
# "public"; an unverified Google app forces every upload to PRIVATE regardless, so
# in practice Shorts land private until the user flips them in Studio (or the app is
# verified). YouTube caps the combined length of all tags at 500 chars.
YOUTUBE_TITLE_MAX = 100
YOUTUBE_TAGS_MAX = 500


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
        headers=HEADERS,
        params={"startDate": utc_iso(start_dt), "endDate": utc_iso(end_dt)},
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    posts = payload.get("posts") if isinstance(payload, dict) else []
    return [p for p in posts if isinstance(p, dict)]


def is_matching_existing_postiz_post(
    candidate: dict,
    integration_id: str,
    caption: str,
    schedule_time: str,
) -> bool:
    if str(candidate.get("state") or "").upper() in {"ERROR", "FAILED"}:
        return False
    integration = candidate.get("integration") or {}
    if not isinstance(integration, dict) or integration.get("id") != integration_id:
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
    for candidate in existing_posts:
        if is_matching_existing_postiz_post(candidate, integration_id, caption, schedule_time):
            return candidate
    return None


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
            headers=HEADERS,
            files={"file": f},
            timeout=120,
        )
    r.raise_for_status()
    return r.json()  # contains id + path


def first_nested_value(payload: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(payload, dict):
        for key in keys:
            if payload.get(key):
                return payload[key]
        for value in payload.values():
            nested = first_nested_value(value, keys)
            if nested:
                return nested
    if isinstance(payload, list):
        for item in payload:
            nested = first_nested_value(item, keys)
            if nested:
                return nested
    return None


def postiz_response_metadata(payload: Any) -> dict:
    metadata = {}
    postiz_id = first_nested_value(payload, ("id", "_id", "postId", "post_id"))
    if postiz_id:
        metadata["postiz_post_id"] = str(postiz_id)
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("state")
        if status:
            metadata["postiz_status"] = str(status)
    return metadata


def schedule(post: dict) -> dict:
    key = (post["account"], post["platform"])
    integration_id = INTEGRATIONS.get(key)
    if not integration_id or integration_id == "REPLACE_ME":
        print(f"  ! no integration mapped for {key}, skipping {post['id']}")
        return {"scheduled": False, "skip_reason": "missing_integration"}

    fmt = post.get("format", "single")
    paths = resolve_local_paths(post)
    is_video = bool(paths) and paths[0].lower().endswith(VIDEO_EXTS)

    if post["platform"] in ("tiktok", "youtube"):
        label = post["platform"]
        if not paths:
            print(f"  ! {label} post {post['id']} has no resolved media, skipping")
            return {"scheduled": False, "skip_reason": "missing_media"}
        if not is_video:
            print(f"  ! {label} post {post['id']} media is not a video "
                  f"({', '.join(VIDEO_EXTS)}), skipping")
            return {"scheduled": False, "skip_reason": "non_video_media"}

    # A reel needs a video; if the mp4 didn't render, fall back to publishing the
    # base still as a normal image post so the post still goes out.
    if fmt == "reel" and not is_video:
        print(f"  ! reel {post['id']} has no video, publishing image as a feed post")
        fmt = "single"

    caption, first_comment = build_caption(post)
    existing = find_existing_postiz_duplicate(
        integration_id,
        caption,
        post["schedule_time"],
    )
    if existing:
        postiz_id = str(existing.get("id") or "")
        state = str(existing.get("state") or "existing").lower()
        print(f"  ! {post['id']}: matching {state} Postiz post exists, skipping duplicate")
        return {
            "scheduled": False,
            "integration_id": integration_id,
            "skip_reason": "postiz_duplicate",
            **({"postiz_post_id": postiz_id} if postiz_id else {}),
        }

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
    r = requests.post(f"{POSTIZ_URL}/api/public/v1/posts",
                      headers={**HEADERS, "Content-Type": "application/json"},
                      json=payload, timeout=60)
    if r.ok:
        try:
            response_payload = r.json()
        except ValueError:
            response_payload = {}
        extra = f" +comment" if first_comment else ""
        print(f"  ✓ scheduled {post['id']} ({fmt}, {len(media)} media{extra}) "
              f"for {post['schedule_time']}")
        return {
            "scheduled": True,
            "integration_id": integration_id,
            **postiz_response_metadata(response_payload),
        }
    print(f"  ✗ {post['id']}: {r.status_code} {r.text[:200]}")
    return {
        "scheduled": False,
        "integration_id": integration_id,
        "skip_reason": "postiz_error",
        "postiz_status_code": r.status_code,
    }

def main(queue_file: str) -> None:
    qpath = pathlib.Path(queue_file)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    posted_dir = pathlib.Path("posted")
    posted_dir.mkdir(exist_ok=True)
    log_path = posted_dir / "log.json"
    log = load_log(log_path)
    already_scheduled = scheduled_post_ids(log)

    # Enforce TikTok's 5-pending-drafts/24h inbox cap: schedule only the first
    # TIKTOK_INBOX_CAP inbox-bound TikTok posts; skip the rest so they aren't
    # rejected by TikTok as spam. Order follows the queue file (time-spread by the
    # generator). Non-TikTok and DIRECT_POST posts are unaffected.
    results = []
    tiktok_inbox_seen = 0
    seen_post_ids = set(already_scheduled)
    for p in posts:
        post_id = str(p.get("id") or "")
        if post_id and post_id in seen_post_ids:
            print(f"  ! {post_id}: already seen as scheduled, skipping Postiz")
            results.append({**p, "scheduled": False, "skip_reason": "duplicate_post_id"})
            continue
        if post_id:
            seen_post_ids.add(post_id)
        if is_tiktok_inbox_post(p):
            tiktok_inbox_seen += 1
            if tiktok_inbox_seen > TIKTOK_INBOX_CAP:
                print(f"  ! TikTok inbox cap ({TIKTOK_INBOX_CAP} pending/24h) reached, "
                      f"skipping {p['id']} (a 6th+ upload is rejected as "
                      f"spam_risk_too_many_pending_share)")
                results.append({**p, "scheduled": False, "skip_reason": "tiktok_inbox_cap"})
                continue
        results.append({**p, **schedule(p)})

    log = append_new_log_records(log, results)
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    archive_queue_file(qpath, posted_dir)
    print(f"Done: {sum(p['scheduled'] for p in results)}/{len(results)} scheduled.")

if __name__ == "__main__":
    main(sys.argv[1])
