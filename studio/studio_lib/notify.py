"""Optional webhook and SMTP notifications. Secrets and the recipient live in .env."""
from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import requests

from .config import DATA, ROOT


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


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _email_settings() -> tuple[dict | None, str | None]:
    recipient = os.environ.get("NOTIFY_EMAIL_TO", "").strip()
    host = os.environ.get("SMTP_HOST", "").strip()
    sender = os.environ.get("SMTP_FROM", "").strip()
    if not recipient:
        return None, "NOTIFY_EMAIL_TO is not configured"
    if not host or not sender:
        return None, "SMTP_HOST and SMTP_FROM are required"
    try:
        port = int(os.environ.get("SMTP_PORT", "465" if _truthy("SMTP_USE_SSL") else "587"))
    except ValueError:
        return None, "SMTP_PORT must be an integer"
    return {
        "to": recipient, "host": host, "port": port, "from": sender,
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "ssl": _truthy("SMTP_USE_SSL"),
        "starttls": _truthy("SMTP_STARTTLS", True),
    }, None


def send_email(subject: str, body: str, *, attachment: Path | None = None, log=print) -> bool:
    settings, reason = _email_settings()
    if reason:
        log(f"  email not sent: {reason}")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings["from"], settings["to"]
    msg.set_content(body)
    if attachment and attachment.exists():
        msg.add_attachment(attachment.read_bytes(), maintype="text", subtype="markdown", filename=attachment.name)
    try:
        if settings["ssl"]:
            smtp = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=30,
                                    context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(settings["host"], settings["port"], timeout=30)
        with smtp:
            if settings["starttls"] and not settings["ssl"]:
                smtp.starttls(context=ssl.create_default_context())
            if settings["user"]:
                smtp.login(settings["user"], settings["password"])
            smtp.send_message(msg)
        log(f"  email sent to {settings['to']}")
        return True
    except Exception as exc:  # noqa: BLE001 - notification failure must not discard content
        log(f"  email failed: {exc}")
        return False


def _email_marker(d: date) -> Path:
    return DATA / "notifications" / f"daily-email-{d.isoformat()}.json"


def send_daily_ready(plan: dict, d: date, results: dict, page: Path, hero_rows: list[dict], log=print) -> bool:
    """Send one completion email per content date. Publish retries cannot duplicate it."""
    marker = _email_marker(d)
    if marker.exists():
        log(f"  daily email already sent for {d}")
        return True
    lines = [
        f"Layer8 Studio prepared content for {d}.",
        "",
        "PLANNED TIMES (not yet proof of Postiz scheduling):",
    ]
    for post in plan.get("posts", []):
        for platform, when in (post.get("schedule") or {}).items():
            lines.append(f"- {when}: {post['id']} — {post['brand']} {platform} {post['format']}")
    blocked = [f"- {pid}: {'; '.join(r['errors'])}" for pid, r in results.items() if not r["pass"]]
    lines += ["", f"QUALITY GATES: {sum(r['pass'] for r in results.values())}/{len(results)} passed."]
    if blocked:
        lines += ["BLOCKED POSTS:", *blocked]
    missing = [r["filename"] for r in hero_rows if not r.get("ready")]
    lines += ["", "HERO IMAGES:"]
    lines += ([f"- Missing: {name}" for name in missing] if missing else ["- All hero images are ready."])
    lines += [
        "",
        f"Preview: {page}",
        f"Prompt pack: studio/inbox/PROMPTS-{d}.md",
        "",
        "The prompt pack is attached. Paste it once into ChatGPT; if only one image is returned, say “continue”.",
    ]
    prompt = ROOT / "inbox" / f"PROMPTS-{d}.md"
    ok = send_email(f"Layer8 Studio: {d} content ready", "\n".join(lines), attachment=prompt, log=log)
    if ok:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"date": d.isoformat(), "to": os.environ.get("NOTIFY_EMAIL_TO")}),
                          encoding="utf-8")
    return ok


def send_daily_failure(d: date, error: Exception, log=print) -> bool:
    return send_email(
        f"Layer8 Studio: {d} preparation FAILED",
        f"Layer8 Studio could not prepare content for {d}.\n\nError: {type(error).__name__}: {error}\n\n"
        "No success notification was recorded. Check data/logs/task-run-daily.log.",
        log=log,
    )
