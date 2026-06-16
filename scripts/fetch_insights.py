#!/usr/bin/env python3
"""Fetch Instagram analytics for published Layer8Culture posts.

Environment variables:
  IG_USER_ID: Instagram Business account user id.
  IG_GRAPH_TOKEN: Long-lived Instagram Graph API token with instagram_basic,
    instagram_manage_insights, and pages_read_engagement permissions.
  POSTIZ_URL + POSTIZ_API_KEY: Best-effort fallback if Graph credentials are
    absent. Postiz does not currently expose a stable public analytics endpoint,
    so this script tries known-looking endpoints and exits cleanly if unavailable.
  INSIGHTS_FIXTURE: Optional local JSON fixture for offline testing. Equivalent
    to passing --fixture.

The script reads posted/log.json, resolves each post to Instagram media by
caption/time similarity, stores per-post metrics in analytics/insights.json,
maintains analytics/followers.json, and writes analytics/insights-digest.md.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

GRAPH_VERSION = "v23.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
DEFAULT_POSTED_LOG = Path("posted") / "log.json"
DEFAULT_ANALYTICS_DIR = Path("analytics")
FOLLOWER_GOAL = 4000
HTTP_TIMEOUT = 20
MAX_MEDIA = 50
MATCH_WINDOW_SECONDS = 4 * 24 * 60 * 60

FEED_METRICS = [
    "reach",
    "saved",
    "shares",
    "total_interactions",
    "profile_visits",
    "follows",
]
REELS_METRICS = [
    "reach",
    "saved",
    "shares",
    "total_interactions",
    "ig_reels_video_view_total",
    "ig_reels_avg_watch_time",
]

STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "your",
    "you",
    "are",
    "was",
    "but",
    "not",
    "our",
    "its",
    "into",
    "about",
    "layer8culture",
}


def log(message: str) -> None:
    print(f"[insights] {message}", flush=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Could not read {path}: {exc}; using empty default.")
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if re.search(r"[+-]\d{4}$", text):
        text = f"{text[:-5]}{text[-5:-2]}:{text[-2:]}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def posting_hour(schedule_time: str | None) -> str | None:
    dt = parse_datetime(schedule_time)
    if not dt:
        return None
    return f"{dt.hour:02d}:00"


def post_caption(post: dict[str, Any]) -> str:
    text = str(post.get("text") or "")
    hashtags = post.get("hashtags") or []
    if isinstance(hashtags, list) and hashtags:
        text = f"{text}\n\n{' '.join(str(tag) for tag in hashtags)}"
    return text


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return {word for word in words if word not in STOP_WORDS}


def caption_similarity(our_caption: str, ig_caption: str | None) -> float:
    if not our_caption or not ig_caption:
        return 0.0
    ours_norm = normalize_spaces(our_caption).lower()
    ig_norm = normalize_spaces(ig_caption).lower()
    our_prefix = ours_norm[:80]
    prefix_boost = 0.0
    if len(our_prefix) >= 30 and ig_norm.startswith(our_prefix[:40]):
        prefix_boost = 0.35

    ours = tokens(our_prefix or ours_norm[:240])
    theirs = tokens(ig_norm[:320])
    if not ours or not theirs:
        return prefix_boost
    overlap = len(ours & theirs) / max(1, min(len(ours), len(theirs)))
    return min(1.0, overlap + prefix_boost)


def extract_metric_value(payload: dict[str, Any]) -> Any:
    if "total_value" in payload:
        total = payload.get("total_value") or {}
        if isinstance(total, dict) and "value" in total:
            return total["value"]
    values = payload.get("values")
    if isinstance(values, list) and values:
        last = values[-1]
        if isinstance(last, dict) and "value" in last:
            return last["value"]
    return None


class GraphClient:
    def __init__(self, user_id: str, token: str) -> None:
        self.user_id = user_id
        self.token = token

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        params["access_token"] = self.token
        url = f"{GRAPH_BASE}/{path.lstrip('/')}"
        response = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        if not response.ok:
            raise RuntimeError(f"GET {path}: {response.status_code} {response.text[:240]}")
        return response.json()

    def get_url(self, url: str) -> dict[str, Any]:
        response = requests.get(url, timeout=HTTP_TIMEOUT)
        if not response.ok:
            raise RuntimeError(f"GET paging url: {response.status_code} {response.text[:240]}")
        return response.json()

    def recent_media(self, limit: int = MAX_MEDIA) -> list[dict[str, Any]]:
        fields = (
            "id,caption,media_type,media_product_type,permalink,timestamp,"
            "like_count,comments_count"
        )
        media: list[dict[str, Any]] = []
        payload = self.get(f"{self.user_id}/media", {"fields": fields, "limit": min(limit, 50)})
        while True:
            media.extend(payload.get("data") or [])
            if len(media) >= limit:
                return media[:limit]
            next_url = ((payload.get("paging") or {}).get("next"))
            if not next_url:
                return media
            payload = self.get_url(next_url)

    def media_insights(self, media: dict[str, Any]) -> dict[str, Any]:
        product_type = str(media.get("media_product_type") or "").upper()
        media_type = str(media.get("media_type") or "").upper()
        metric_names = REELS_METRICS if product_type == "REELS" else FEED_METRICS
        metrics: dict[str, Any] = {}

        for count_name in ("like_count", "comments_count"):
            if media.get(count_name) is not None:
                metrics[count_name] = media[count_name]

        for metric in metric_names:
            try:
                payload = self.get(f"{media['id']}/insights", {"metric": metric})
                data = payload.get("data") or []
                if data:
                    value = extract_metric_value(data[0])
                    if value is not None:
                        metrics[metric] = value
            except Exception as exc:
                log(
                    "Skipping metric "
                    f"{metric} for media {media.get('id')} ({product_type or media_type}): {exc}"
                )
        return metrics

    def followers_count(self) -> int | None:
        try:
            payload = self.get(self.user_id, {"fields": "followers_count"})
            value = payload.get("followers_count")
            return int(value) if value is not None else None
        except Exception as exc:
            log(f"Could not fetch follower count: {exc}")
            return None

    def account_insights(self) -> dict[str, Any]:
        try:
            payload = self.get(
                f"{self.user_id}/insights",
                {"metric": "reach,profile_views", "period": "day"},
            )
            result: dict[str, Any] = {}
            for item in payload.get("data") or []:
                name = item.get("name")
                if name:
                    result[name] = item.get("values") or []
            return result
        except Exception as exc:
            log(f"Could not fetch account insights: {exc}")
            return {}


class FixtureClient:
    def __init__(self, path: Path) -> None:
        self.payload = load_json(path, {})

    def recent_media(self, limit: int = MAX_MEDIA) -> list[dict[str, Any]]:
        media = self.payload.get("media") or self.payload.get("recent_media") or []
        return list(media)[:limit]

    def media_insights(self, media: dict[str, Any]) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for count_name in ("like_count", "comments_count"):
            if media.get(count_name) is not None:
                metrics[count_name] = media[count_name]
        insights = media.get("insights") or media.get("metrics") or {}
        if isinstance(insights, dict):
            metrics.update(insights)
        return metrics

    def followers_count(self) -> int | None:
        value = self.payload.get("followers_count")
        return int(value) if value is not None else None

    def account_insights(self) -> dict[str, Any]:
        return self.payload.get("account_insights") or {}


def postiz_fallback() -> None:
    postiz_url = os.environ.get("POSTIZ_URL")
    api_key = os.environ.get("POSTIZ_API_KEY")
    if not postiz_url or not api_key:
        log("No Instagram Graph credentials or Postiz fallback credentials; exiting cleanly.")
        return

    headers = {"Authorization": api_key}
    base = postiz_url.rstrip("/")
    candidates = (
        "/api/public/v1/analytics",
        "/api/public/v1/posts/analytics",
    )
    for suffix in candidates:
        url = urljoin(f"{base}/", suffix.lstrip("/"))
        try:
            response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            if response.ok:
                log(
                    "Postiz analytics endpoint responded, but its schema is not "
                    "mapped into Instagram Insights yet; Graph credentials are preferred."
                )
                return
            log(f"Postiz fallback {suffix} unavailable: HTTP {response.status_code}")
        except Exception as exc:
            log(f"Postiz fallback {suffix} failed: {exc}")
    log("Postiz fallback unavailable; exiting cleanly.")


def load_posts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        log(f"{path} not found; no published posts to analyze.")
        return []
    payload = load_json(path, [])
    if not isinstance(payload, list):
        log(f"{path} is not a JSON array; no posts loaded.")
        return []
    posts = []
    for post in payload:
        if not isinstance(post, dict):
            continue
        if str(post.get("platform") or "").lower() != "instagram":
            continue
        if post.get("account") not in (None, "layer8culture"):
            continue
        if post.get("scheduled") is False:
            continue
        posts.append(post)
    return posts


def load_insights_store(path: Path) -> dict[str, Any]:
    payload = load_json(path, {"version": 1, "posts": [], "account": {}})
    if isinstance(payload, list):
        payload = {"version": 1, "posts": payload, "account": {}}
    if not isinstance(payload, dict):
        payload = {"version": 1, "posts": [], "account": {}}
    payload.setdefault("version", 1)
    payload.setdefault("posts", [])
    payload.setdefault("account", {})
    return payload


def matched_media_id(existing_posts: list[dict[str, Any]], post_id: str | None) -> str | None:
    for record in existing_posts:
        if record.get("post_id") == post_id and record.get("media_id"):
            return str(record["media_id"])
    return None


def match_posts_to_media(
    posts: list[dict[str, Any]],
    media_items: list[dict[str, Any]],
    existing_posts: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    media_by_id = {str(item.get("id")): item for item in media_items if item.get("id")}
    assigned: set[str] = set()
    matches: dict[str, dict[str, Any]] = {}

    ordered_posts = sorted(posts, key=lambda item: item.get("schedule_time") or "")
    for post in ordered_posts:
        post_id = str(post.get("id") or "")
        if not post_id:
            continue
        existing_id = matched_media_id(existing_posts, post_id)
        if existing_id and existing_id in media_by_id:
            matches[post_id] = media_by_id[existing_id]
            assigned.add(existing_id)
            continue

        schedule_dt = as_utc(parse_datetime(post.get("schedule_time")))
        if not schedule_dt:
            log(f"Skipping match for {post_id}: missing/invalid schedule_time.")
            continue

        best_score = 0.0
        best_media: dict[str, Any] | None = None
        our_caption = post_caption(post)
        for media in media_items:
            media_id = str(media.get("id") or "")
            if not media_id or media_id in assigned:
                continue
            media_dt = as_utc(parse_datetime(media.get("timestamp")))
            if not media_dt:
                continue
            delta = abs((media_dt - schedule_dt).total_seconds())
            if delta > MATCH_WINDOW_SECONDS:
                continue

            caption_score = caption_similarity(our_caption, media.get("caption") or "")
            if caption_score < 0.25:
                continue
            days_apart = delta / 86400
            score = caption_score + max(0.0, 0.25 - (days_apart * 0.06))
            if score > best_score:
                best_score = score
                best_media = media

        if best_media:
            media_id = str(best_media.get("id"))
            matches[post_id] = best_media
            assigned.add(media_id)
            log(f"Matched {post_id} -> IG media {media_id} (score {best_score:.2f}).")
        else:
            log(f"No IG media match found for {post_id}.")
    return matches


def upsert_posts(
    store: dict[str, Any],
    posts: list[dict[str, Any]],
    matches: dict[str, dict[str, Any]],
    client: GraphClient | FixtureClient,
    fetched_at: str,
) -> int:
    existing = {
        str(record.get("post_id")): record
        for record in store.get("posts", [])
        if isinstance(record, dict) and record.get("post_id")
    }
    changed = 0
    for post in posts:
        post_id = str(post.get("id") or "")
        media = matches.get(post_id)
        if not post_id or not media:
            continue
        try:
            metrics = client.media_insights(media)
        except Exception as exc:
            log(f"Could not fetch insights for {post_id}/{media.get('id')}: {exc}")
            metrics = {}

        record = dict(existing.get(post_id) or {})
        merged_metrics = dict(record.get("metrics") or {})
        merged_metrics.update(metrics)
        record.update(
            {
                "post_id": post_id,
                "media_id": str(media.get("id")),
                "account": post.get("account") or "layer8culture",
                "format": post.get("format") or "single",
                "category": post.get("category") or "uncategorized",
                "permalink": media.get("permalink") or record.get("permalink"),
                "schedule_time": post.get("schedule_time"),
                "posting_hour": posting_hour(post.get("schedule_time")),
                "text": post.get("text") or "",
                "metrics": merged_metrics,
                "fetched_at": fetched_at,
            }
        )
        existing[post_id] = record
        changed += 1

    store["posts"] = sorted(existing.values(), key=lambda item: item.get("schedule_time") or "")
    return changed


def update_followers(
    analytics_dir: Path,
    store: dict[str, Any],
    client: GraphClient | FixtureClient,
    fetched_at: str,
) -> list[dict[str, Any]]:
    followers_path = analytics_dir / "followers.json"
    series = load_json(followers_path, [])
    if not isinstance(series, list):
        series = []

    followers_count = client.followers_count()
    account_insights = client.account_insights()
    account = dict(store.get("account") or {})
    if followers_count is not None:
        account["followers_count"] = followers_count
    if account_insights:
        account["daily_insights"] = account_insights
    account["fetched_at"] = fetched_at
    store["account"] = account

    if followers_count is not None:
        date_key = fetched_at[:10]
        by_date = {
            str(item.get("date")): item
            for item in series
            if isinstance(item, dict) and item.get("date")
        }
        by_date[date_key] = {"date": date_key, "followers_count": followers_count}
        series = [by_date[key] for key in sorted(by_date)]
        write_json(followers_path, series)
        log(f"Follower count: {followers_count}")
    else:
        log("Follower count unavailable.")
    return series


def metric(record: dict[str, Any], name: str) -> float:
    value = ((record.get("metrics") or {}).get(name))
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def int_metric(record: dict[str, Any], name: str) -> int:
    return int(metric(record, name))


def caption_hook(record: dict[str, Any], words: int = 8) -> str:
    text = normalize_spaces(str(record.get("text") or ""))
    text = re.sub(r"#\w+", "", text).strip()
    parts = text.split()
    if not parts:
        return "(caption unavailable)"
    hook = " ".join(parts[:words])
    if len(parts) > words:
        hook += "…"
    return hook


def median(values: list[float]) -> float:
    clean = [value for value in values if value is not None and not math.isnan(value)]
    return statistics.median(clean) if clean else 0.0


def best_group(posts: list[dict[str, Any]], field: str) -> tuple[str, int, int] | None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in posts:
        grouped[str(record.get(field) or "unknown")].append(record)
    if not grouped:
        return None
    scored = []
    for name, records in grouped.items():
        med_saves = int(round(median([metric(record, "saved") for record in records])))
        med_reach = int(round(median([metric(record, "reach") for record in records])))
        scored.append((med_saves, med_reach, len(records), name))
    med_saves, med_reach, _count, name = max(scored)
    return name, med_saves, med_reach


def follower_delta(series: list[dict[str, Any]], current: int | None) -> str:
    if current is None:
        return "Follower count unavailable."
    need = max(0, FOLLOWER_GOAL - current)
    goal_text = f"{current:,} → need {need:,} more to reach {FOLLOWER_GOAL:,}"
    clean = [
        item
        for item in series
        if isinstance(item, dict) and item.get("date") and item.get("followers_count") is not None
    ]
    if len(clean) < 2:
        return f"{goal_text}; 7-day change: not enough history."

    current_date = datetime.fromisoformat(clean[-1]["date"])
    target = current_date.timestamp() - (7 * 86400)
    baseline = None
    for item in reversed(clean[:-1]):
        item_date = datetime.fromisoformat(item["date"]).timestamp()
        if item_date <= target:
            baseline = item
            break
    if baseline is None and len(clean) >= 7:
        baseline = clean[0]
    if baseline is None:
        return f"{goal_text}; 7-day change: not enough history."
    change = current - int(baseline["followers_count"])
    sign = "+" if change >= 0 else ""
    return f"{goal_text}; 7-day change: {sign}{change:,}."


def post_line(record: dict[str, Any], metric_name: str) -> str:
    return (
        f"- {record.get('format') or 'single'} / {record.get('category') or 'uncategorized'}: "
        f"“{caption_hook(record)}” at {record.get('posting_hour') or 'unknown'} — "
        f"{int_metric(record, metric_name):,} {metric_name.replace('_', ' ')}"
    )


def best_hours(posts: list[dict[str, Any]]) -> str:
    top_posts = sorted(
        posts,
        key=lambda record: (metric(record, "saved"), metric(record, "reach")),
        reverse=True,
    )[:10]
    counts = Counter(record.get("posting_hour") for record in top_posts if record.get("posting_hour"))
    if not counts:
        return "Not enough posting-hour data yet."
    return ", ".join(f"{hour} ({count} top post{'s' if count != 1 else ''})" for hour, count in counts.most_common(3))


def write_digest(analytics_dir: Path, store: dict[str, Any], follower_series: list[dict[str, Any]]) -> str:
    posts = [
        record
        for record in store.get("posts", [])
        if isinstance(record, dict) and record.get("metrics")
    ]
    current_followers = (store.get("account") or {}).get("followers_count")
    if current_followers is not None:
        current_followers = int(current_followers)

    lines = [
        "# Instagram Insights Digest",
        "",
        f"**Followers:** {follower_delta(follower_series, current_followers)}",
        "",
    ]

    if not posts:
        lines.extend(
            [
                "No matched post insights yet. Keep testing clear hooks, save-worthy",
                "builder takeaways, and posting windows until Graph data lands.",
                "",
            ]
        )
    else:
        by_saves = sorted(posts, key=lambda record: metric(record, "saved"), reverse=True)[:5]
        by_reach = sorted(posts, key=lambda record: metric(record, "reach"), reverse=True)[:5]
        bottom_reach = sorted(posts, key=lambda record: metric(record, "reach"))[:3]
        best_format = best_group(posts, "format")
        best_category = best_group(posts, "category")

        lines.append("**Top saves:**")
        lines.extend(post_line(record, "saved") for record in by_saves)
        lines.extend(["", "**Top reach:**"])
        lines.extend(post_line(record, "reach") for record in by_reach)
        lines.extend(["", "**Lowest reach to learn from:**"])
        lines.extend(post_line(record, "reach") for record in bottom_reach)
        lines.append("")

        if best_format:
            name, med_saves, med_reach = best_format
            lines.append(f"**Best format:** {name} (median {med_saves:,} saves / {med_reach:,} reach).")
        if best_category:
            name, med_saves, med_reach = best_category
            lines.append(f"**Best category:** {name} (median {med_saves:,} saves / {med_reach:,} reach).")
        lines.append(f"**Best posting hours:** {best_hours(posts)}")

    digest = "\n".join(lines).strip() + "\n"
    digest_path = analytics_dir / "insights-digest.md"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(digest, encoding="utf-8")
    return digest


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Instagram Insights for published posts.")
    parser.add_argument("--fixture", default=os.environ.get("INSIGHTS_FIXTURE"), help="Offline Graph fixture JSON.")
    parser.add_argument(
        "--posted-log",
        default=os.environ.get("POSTED_LOG_PATH", str(DEFAULT_POSTED_LOG)),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--analytics-dir",
        default=os.environ.get("ANALYTICS_DIR", str(DEFAULT_ANALYTICS_DIR)),
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    posted_log = Path(args.posted_log)
    analytics_dir = Path(args.analytics_dir)
    insights_path = analytics_dir / "insights.json"

    posts = load_posts(posted_log)
    if not posts:
        return 0
    log(f"Loaded {len(posts)} published Instagram posts.")

    fixture = Path(args.fixture) if args.fixture else None
    if fixture:
        log(f"Using offline fixture: {fixture}")
        client: GraphClient | FixtureClient = FixtureClient(fixture)
    else:
        ig_user_id = os.environ.get("IG_USER_ID")
        ig_token = os.environ.get("IG_GRAPH_TOKEN")
        if ig_user_id and ig_token:
            client = GraphClient(ig_user_id, ig_token)
        else:
            postiz_fallback()
            return 0

    fetched_at = utc_now_iso()
    store = load_insights_store(insights_path)

    try:
        media_items = client.recent_media(MAX_MEDIA)
        log(f"Loaded {len(media_items)} recent IG media items.")
    except Exception as exc:
        log(f"Could not fetch recent media: {exc}; exiting cleanly.")
        return 0

    matches = match_posts_to_media(posts, media_items, store.get("posts") or [])
    changed = upsert_posts(store, posts, matches, client, fetched_at)
    follower_series = update_followers(analytics_dir, store, client, fetched_at)
    store["updated_at"] = fetched_at
    write_json(insights_path, store)
    digest = write_digest(analytics_dir, store, follower_series)
    log(f"Updated {changed} post insight records.")
    log("Wrote analytics/insights.json, analytics/followers.json, and analytics/insights-digest.md.")
    log("Digest preview:")
    print(digest)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"Unexpected error: {exc}; exiting cleanly so CI does not fail.")
        raise SystemExit(0)
