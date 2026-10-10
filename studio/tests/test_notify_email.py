import json
from datetime import date
from pathlib import Path

from studio_lib import notify


class FakeSMTP:
    sent = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, **kwargs):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        self.sent.append(message)


def _smtp_env(monkeypatch):
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "owner@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "studio@example.com")


def test_daily_email_attaches_prompt_labels_times_and_deduplicates(monkeypatch, tmp_path):
    _smtp_env(monkeypatch)
    FakeSMTP.sent.clear()
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify, "ROOT", tmp_path)
    monkeypatch.setattr(notify, "DATA", tmp_path / "data")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "PROMPTS-2026-10-11.md").write_text("ONE PASTE", encoding="utf-8")
    plan = {"posts": [{
        "id": "p1", "brand": "layer8culture", "format": "single",
        "schedule": {"instagram": "2026-10-11T09:30:00-04:00"},
    }]}
    results = {"p1": {"pass": True, "errors": []}}
    heroes = [{"filename": "hero.png", "ready": False}]
    d = date(2026, 10, 11)
    assert notify.send_daily_ready(plan, d, results, Path("preview.html"), heroes, log=lambda _: None)
    assert notify.send_daily_ready(plan, d, results, Path("preview.html"), heroes, log=lambda _: None)
    assert len(FakeSMTP.sent) == 1
    body = FakeSMTP.sent[0].get_body().get_content()
    assert "PLANNED TIMES (not yet proof of Postiz scheduling)" in body
    assert "Missing: hero.png" in body
    assert list(FakeSMTP.sent[0].iter_attachments())[0].get_filename() == "PROMPTS-2026-10-11.md"
    marker = tmp_path / "data" / "notifications" / "daily-email-2026-10-11.json"
    assert json.loads(marker.read_text())["to"] == "owner@example.com"


def test_missing_smtp_is_an_explicit_blocker(monkeypatch):
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "owner@example.com")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    logs = []
    assert not notify.send_email("subject", "body", log=logs.append)
    assert any("SMTP_HOST and SMTP_FROM are required" in line for line in logs)
