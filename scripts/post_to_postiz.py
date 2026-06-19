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
Optional env: LOFI_IG_CHANNEL_ID — the Postiz channel ID for the lofi
(Layer8CultureRadio) Instagram account; overrides its INTEGRATIONS placeholder.
Optional env: TIKTOK_CHANNEL_ID — the Postiz channel ID for the layer8culture
TikTok account; fills its INTEGRATIONS placeholder (unset -> TikTok posts skipped).
Optional env: YOUTUBE_LAYER8_CHANNEL_ID / YOUTUBE_LOFI_CHANNEL_ID — the Postiz
channel IDs for each brand's YouTube channel; fill their INTEGRATIONS placeholders
(unset -> that brand's YouTube Shorts are skipped, not errored).
Note: integration IDs map your accounts/platforms to Postiz channels.
Fill INTEGRATIONS after connecting your accounts in the Postiz UI
(Settings -> API shows channel IDs).
"""
import json
import os
import sys
import pathlib
import requests


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
# Note: ("layer8culture", "tiktok") is active — the layer8culture pipeline now
# generates TikTok videos (see calendar/topics.md + scripts/generation-prompt.md).
# Its channel ID is supplied via the TIKTOK_CHANNEL_ID secret below (kept out of
# code). YouTube is active for BOTH brands — the pipelines cross-post reels as
# YouTube Shorts; channel IDs come from the YOUTUBE_*_CHANNEL_ID secrets below.
# ("lofi", "tiktok") stays provisioning-only — nothing generates for lofi TikTok
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
}

# The lofi (Layer8CultureRadio) Instagram channel is wired above with its Postiz
# integration ID. The LOFI_IG_CHANNEL_ID secret, if set, overrides it without a code
# change (e.g. if the channel is re-connected and gets a new ID).
_lofi_ig = os.environ.get("LOFI_IG_CHANNEL_ID")
if _lofi_ig:
    INTEGRATIONS[("lofi", "instagram")] = _lofi_ig

# The layer8culture TikTok channel ID is supplied via the TIKTOK_CHANNEL_ID secret
# (kept out of code). When unset the mapping stays REPLACE_ME and TikTok posts are
# skipped — not errored — so the engine ships TikTok posts only once the channel is
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

VIDEO_EXTS = (".mp4", ".mov")

# Per-platform base post settings required by the Postiz API. For Instagram the
# api requires settings.post_type ("post" or "story"); we add it per-format in
# platform_settings(). The others are placeholders for when those channels wire up.
#
# TikTok mirrors Postiz's TikTokDto. Every TikTok video this engine ships is
# AI-generated (Sora-2), so video_made_with_ai is disclosed (TikTok policy +
# honesty). privacy_level is SELF_ONLY (private) because the TikTok app is NOT yet
# audited — an unaudited Direct Post client can only post privately (TikTok rejects
# PUBLIC_TO_EVERYONE with unaudited_client_can_only_post_to_private_accounts). Once
# the app passes TikTok's audit, flip privacy_level to "PUBLIC_TO_EVERYONE" here (or
# per post via "tiktok_settings") to publish publicly. NOTE: TikTok pulls the video
# via PULL_FROM_URL, so the Postiz media domain must be verified as a URL property in
# the TikTok dev portal or posts fail with url_ownership_unverified.
PLATFORM_SETTINGS = {
    "instagram": {"post_type": "post"},  # base; story/reel adjust this below
    "tiktok": {
        "privacy_level": "SELF_ONLY",  # unaudited app -> private only; flip after audit
        "duet": True,
        "stitch": True,
        "comment": True,
        "autoAddMusic": "no",
        "brand_content_toggle": False,
        "brand_organic_toggle": False,
        "video_made_with_ai": True,
        "content_posting_method": "DIRECT_POST",
    },
}

# YouTube uploads must carry a non-empty title (2-100 chars). We request type
# "public"; an unverified Google app forces every upload to PRIVATE regardless, so
# in practice Shorts land private until the user flips them in Studio (or the app is
# verified). YouTube caps the combined length of all tags at 500 chars.
YOUTUBE_TITLE_MAX = 100
YOUTUBE_TAGS_MAX = 500


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

    TikTok returns the reach-favoring TikTokDto defaults, with any per-post
    "tiktok_settings" dict merged on top (e.g. to set privacy_level/SELF_ONLY for
    an unaudited app). YouTube returns the YoutubeSettingsDto (title + type + tags).
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


def resolve_local_paths(post: dict) -> list[str]:
    """Ordered list of existing local media paths for the post.

    Carousels use visual.files (one path per slide); every other format uses the
    single visual.file. Falls back to a naive assets/library match when the post
    is sourced from the library.
    """
    visual = post.get("visual", {})
    if post.get("format") == "carousel":
        files = [f for f in (visual.get("files") or [])
                 if f and pathlib.Path(f).exists()]
        if files:
            return files
        # fall through to single-file handling if slide files are missing

    path = visual.get("file")
    if not path and visual.get("source") == "library":
        # naive library match: first file whose name contains a hint word
        hint = (visual.get("library_hint") or "").lower().split()
        lib = pathlib.Path("assets/library")
        for f in lib.glob("*"):
            if any(w in f.name.lower() for w in hint) or not hint:
                path = str(f)
                break
    if path and pathlib.Path(path).exists():
        return [path]
    return []


def build_caption(post: dict) -> tuple[str, str | None]:
    """Return (caption, first_comment). Hashtags go in the caption by default, or
    into the first comment when hashtags_in_first_comment is set (keeps captions
    clean — a common growth tactic)."""
    caption = post["text"]
    hashtags = post.get("hashtags") or []
    first_comment = (post.get("first_comment") or "").strip()
    tag_line = " ".join(hashtags)
    if post.get("hashtags_in_first_comment") and hashtags:
        first_comment = (f"{first_comment}\n\n{tag_line}".strip()
                         if first_comment else tag_line)
    elif hashtags:
        caption += "\n\n" + tag_line
    return caption, (first_comment or None)


def schedule(post: dict) -> bool:
    key = (post["account"], post["platform"])
    integration_id = INTEGRATIONS.get(key)
    if not integration_id or integration_id == "REPLACE_ME":
        print(f"  ! no integration mapped for {key}, skipping {post['id']}")
        return False

    fmt = post.get("format", "single")
    paths = resolve_local_paths(post)
    is_video = bool(paths) and paths[0].lower().endswith(VIDEO_EXTS)

    if post["platform"] in ("tiktok", "youtube"):
        label = post["platform"]
        if not paths:
            print(f"  ! {label} post {post['id']} has no resolved media, skipping")
            return False
        if not is_video:
            print(f"  ! {label} post {post['id']} media is not a video "
                  f"({', '.join(VIDEO_EXTS)}), skipping")
            return False

    # A reel needs a video; if the mp4 didn't render, fall back to publishing the
    # base still as a normal image post so the post still goes out.
    if fmt == "reel" and not is_video:
        print(f"  ! reel {post['id']} has no video, publishing image as a feed post")
        fmt = "single"

    media = []
    for p in paths:
        m = upload_media(p)
        media.append({"id": m["id"], "path": m["path"]})

    caption, first_comment = build_caption(post)
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
        extra = f" +comment" if first_comment else ""
        print(f"  ✓ scheduled {post['id']} ({fmt}, {len(media)} media{extra}) "
              f"for {post['schedule_time']}")
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
