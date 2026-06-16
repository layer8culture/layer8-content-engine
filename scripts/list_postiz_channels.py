#!/usr/bin/env python3
"""List the channels (integrations) connected to your Postiz instance.

A read-only helper to (a) verify your Postiz API auth works and (b) print each
connected channel's **integration ID** — the cuid-style value the engine needs for
scripts/post_to_postiz.py (INTEGRATIONS) and the LOFI_IG_CHANNEL_ID secret.

Postiz terminology: the UI says "channel", the API says "integration" — same thing.

Env vars: POSTIZ_URL, POSTIZ_API_KEY (the same secrets the publish step uses).
Usage:   python scripts/list_postiz_channels.py
"""
import json
import os
import sys

import requests


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"Missing required env var {name}. Set POSTIZ_URL and POSTIZ_API_KEY "
            f"(same values as the GitHub secrets) before running this."
        )
    return val


def main() -> None:
    base = _require_env("POSTIZ_URL").rstrip("/")
    headers = {"Authorization": _require_env("POSTIZ_API_KEY")}
    url = f"{base}/api/public/v1/integrations"

    try:
        r = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as exc:
        sys.exit(f"Request to {url} failed: {exc}")

    if r.status_code == 401:
        sys.exit("401 Unauthorized — POSTIZ_API_KEY is missing or not recognised.")
    if not r.ok:
        sys.exit(f"{r.status_code} from {url}: {r.text[:500]}")

    try:
        data = r.json()
    except ValueError:
        sys.exit(f"Non-JSON response from {url}: {r.text[:500]}")

    # The API returns a list of integrations; tolerate a wrapped shape too.
    channels = data if isinstance(data, list) else (
        data.get("integrations") or data.get("data") or []
    )
    if not channels:
        print("Auth OK, but no channels are connected yet. Connect one in the "
              "Postiz UI (Add Channel), then re-run.")
        return

    print(f"Auth OK — {len(channels)} channel(s) connected:\n")
    for ch in channels:
        if not isinstance(ch, dict):
            continue
        # Field names vary slightly by Postiz version; probe the common ones.
        cid = ch.get("id") or ch.get("integrationId") or "?"
        name = ch.get("name") or ch.get("profile") or ch.get("display") or "?"
        platform = (ch.get("identifier") or ch.get("providerIdentifier")
                    or ch.get("platform") or "?")
        disabled = ch.get("disabled") or ch.get("disableComment")
        flags = " (disabled)" if disabled else ""
        print(f"  • {name}  [{platform}]{flags}")
        print(f"      integration id: {cid}")
    print("\nUse the integration id above for scripts/post_to_postiz.py "
          "(INTEGRATIONS) or the LOFI_IG_CHANNEL_ID secret.")
    # Also dump the raw JSON for anything the summary missed.
    print("\n--- raw ---")
    print(json.dumps(channels, indent=2)[:4000])


if __name__ == "__main__":
    main()
