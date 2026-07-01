#!/usr/bin/env python3
"""Find and optionally delete duplicate queued Postiz posts."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

import post_to_postiz


def post_integration_id(post: dict) -> str | None:
    integration = post.get("integration") or {}
    if isinstance(integration, dict) and integration.get("id"):
        return str(integration["id"])
    if post.get("integrationId"):
        return str(post["integrationId"])
    return None


def duplicate_key(post: dict) -> tuple[str, str, str] | None:
    if str(post.get("state") or "").upper() != "QUEUE":
        return None
    integration_id = post_integration_id(post)
    content = post_to_postiz.normalize_postiz_content(post.get("content"))
    publish_date = post.get("publishDate")
    if not integration_id or not content or not publish_date:
        return None
    scheduled_at = post_to_postiz.parse_postiz_datetime(publish_date)
    return (str(integration_id), content, post_to_postiz.utc_iso(scheduled_at))


def duplicate_sort_key(post: dict) -> tuple[str, str]:
    return (str(post.get("createdAt") or post.get("publishDate") or ""), str(post.get("id") or ""))


def find_duplicate_groups(posts: list[dict], integration_id: str | None = None) -> list[list[dict]]:
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for post in posts:
        post_integration = post_integration_id(post)
        if integration_id and post_integration != integration_id:
            continue
        key = duplicate_key(post)
        if key:
            groups[key].append(post)
    duplicates = []
    for group in groups.values():
        if len(group) > 1:
            duplicates.append(
                sorted(
                    group,
                    key=duplicate_sort_key,
                )
            )
    return duplicates


def delete_post(post_id: str) -> None:
    r = requests.delete(
        f"{post_to_postiz.POSTIZ_URL}/api/public/v1/posts/{post_id}",
        headers=post_to_postiz.HEADERS,
        timeout=60,
    )
    if r.status_code == 404:
        print(f"  ! {post_id}: already deleted")
        return
    r.raise_for_status()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=45,
        help="Number of upcoming days to scan. Default: 45.",
    )
    parser.add_argument(
        "--start-date",
        help="UTC ISO start date. Defaults to 24 hours ago.",
    )
    parser.add_argument(
        "--end-date",
        help="UTC ISO end date. Defaults to now plus --days.",
    )
    parser.add_argument(
        "--integration-id",
        help="Optional Postiz integration ID to scan. Defaults to all integrations.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete duplicate queued posts. Default is dry-run.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    now = datetime.now(timezone.utc)
    start_dt = (
        post_to_postiz.parse_postiz_datetime(args.start_date)
        if args.start_date
        else now - timedelta(days=1)
    )
    end_dt = (
        post_to_postiz.parse_postiz_datetime(args.end_date)
        if args.end_date
        else now + timedelta(days=args.days)
    )

    posts = post_to_postiz.list_postiz_posts(start_dt, end_dt)
    duplicate_groups = find_duplicate_groups(posts, args.integration_id)

    if not duplicate_groups:
        scope = args.integration_id or "all integrations"
        print(f"No duplicate queued Postiz posts found for {scope}.")
        return

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"{mode}: found {len(duplicate_groups)} duplicate queued groups.")
    delete_count = 0
    for index, group in enumerate(duplicate_groups, start=1):
        keep = group[0]
        extras = group[1:]
        print(
            f"Group {index}: keep {keep.get('id')} "
            f"({post_integration_id(keep)}) at {keep.get('publishDate')}; "
            f"delete {len(extras)} duplicate(s)"
        )
        for post in extras:
            post_id = str(post.get("id") or "")
            if not post_id:
                continue
            print(f"  - {post_id}: {post.get('publishDate')}")
            if args.apply:
                delete_post(post_id)
            delete_count += 1

    if args.apply:
        print(f"Deleted {delete_count} duplicate queued Postiz post(s).")
    else:
        print(f"Dry run only. Re-run with --apply to delete {delete_count} duplicate(s).")


if __name__ == "__main__":
    main()
