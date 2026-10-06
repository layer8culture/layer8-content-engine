"""Optional Discord / Slack summary webhooks (DISCORD_WEBHOOK_URL / SLACK_WEBHOOK_URL in .env)."""
from __future__ import annotations

import os

import requests


def send(text: str, log=print) -> None:
    text = text[:1900]
    for env, key in (("DISCORD_WEBHOOK_URL", "content"), ("SLACK_WEBHOOK_URL", "text")):
        url = os.environ.get(env)
        if not url:
            continue
        try:
            requests.post(url, json={key: text}, timeout=20).raise_for_status()
        except Exception as exc:  # noqa: BLE001 - notifications never break a run
            log(f"  notify via {env} failed: {exc}")
