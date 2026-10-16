"""Supported-UI StreamYard transcript downloads with explicit local browser login."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .config import DATA, ROOT
from .health import update_job
from .locks import FileLock

TRANSCRIPTS = ROOT / "transcripts"
INDEX = DATA / "transcripts" / "index.json"
PROFILE = DATA / "browser" / "streamyard-profile"
VIDEO_RE = re.compile(r"/videos/([a-z0-9]+)", re.I)
DATE_PATTERNS = (
    "%b %d, %Y %I:%M %p",
    "%B %d, %Y %I:%M %p",
    "%b %d, %Y",
    "%B %d, %Y",
)
CUE_RE = re.compile(
    r"(?m)^(?:\d+:)?\d{2}:\d{2}\.\d{3}\s+-->\s+(?:\d+:)?\d{2}:\d{2}\.\d{3}"
)


class StreamYardError(RuntimeError):
    pass


class StreamYardLoginRequired(StreamYardError):
    pass


class StreamYardTranscriptPending(StreamYardError):
    pass


@dataclass(frozen=True)
class Recording:
    recording_id: str
    title: str
    aired_at: datetime
    url: str
    show: str


def validate_vtt(raw: str) -> int:
    if not raw.lstrip("\ufeff").startswith("WEBVTT"):
        raise StreamYardError("download is not WEBVTT")
    cues = len(CUE_RE.findall(raw))
    if cues < 3:
        raise StreamYardError(f"WEBVTT has only {cues} timestamped cues")
    return cues


def _slug(title: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return (value[:60].rstrip("-") or "tech-thursday")


def _classify_show(title: str) -> str:
    if re.search(r"\bTech Thursday\b", title, re.I):
        return "tech-thursday"
    if re.search(r"\bworkshop\b", title, re.I):
        return "workshop"
    return "other"


def _parse_air_date(text: str) -> datetime | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    candidates = re.findall(
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]* "
        r"\d{1,2}, \d{4}(?:,? \d{1,2}:\d{2} [AP]M)?",
        normalized,
        flags=re.I,
    )
    for candidate in candidates:
        candidate = re.sub(r"^Sept\b", "Sep", candidate, flags=re.I)
        candidate = re.sub(r", (?=\d{1,2}:\d{2} [AP]M$)", " ", candidate)
        for pattern in DATE_PATTERNS:
            try:
                return datetime.strptime(candidate, pattern)
            except ValueError:
                continue
    return None


def _load_index() -> dict:
    if not INDEX.exists():
        return {"files": []}
    try:
        value = json.loads(INDEX.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StreamYardError(f"transcript index is unreadable: {exc}") from exc
    return value if isinstance(value, dict) and isinstance(value.get("files"), list) else {"files": []}


def _save_index(value: dict) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_name(f".{INDEX.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INDEX)


def import_vtt(recording: Recording, source: Path) -> tuple[Path, bool, int]:
    raw = source.read_text(encoding="utf-8-sig", errors="replace")
    cues = validate_vtt(raw)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with FileLock.acquire(INDEX.with_suffix(".lock"), "streamyard-transcript-index", stale_seconds=15 * 60):
        index = _load_index()
        rows = index["files"]
        existing = next(
            (row for row in rows if row.get("recording_id") == recording.recording_id),
            None,
        )
        if existing:
            path = TRANSCRIPTS / existing["file"]
            if path.exists() and existing.get("sha256") == digest:
                return path, False, cues
            raise StreamYardError(
                f"recording {recording.recording_id} was already indexed with different content"
            )
        duplicate = next(
            (
                row for row in rows
                if row.get("sha256") == digest and (TRANSCRIPTS / str(row.get("file", ""))).exists()
            ),
            None,
        )
        filename = (
            f"{recording.aired_at.date().isoformat()}-{recording.recording_id}-"
            f"{_slug(recording.title)}.vtt"
        )
        destination = TRANSCRIPTS / duplicate["file"] if duplicate else TRANSCRIPTS / filename
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        if duplicate:
            filename = duplicate["file"]
        elif destination.exists():
            current_raw = destination.read_text(encoding="utf-8-sig", errors="replace")
            current = hashlib.sha256(current_raw.encode("utf-8")).hexdigest()
            if current != digest:
                raise StreamYardError(f"destination already exists with different content: {destination}")
        else:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".vtt", delete=False, dir=TRANSCRIPTS
            ) as handle:
                handle.write(raw)
                temp_path = Path(handle.name)
            temp_path.replace(destination)
        rows.append({
            "file": filename,
            "date": recording.aired_at.date().isoformat(),
            "show": recording.show,
            "recording_id": recording.recording_id,
            "title": recording.title,
            "source_url": recording.url,
            "sha256": digest,
            "cues": cues,
            "imported_at": datetime.now().astimezone().isoformat(),
        })
        rows.sort(key=lambda row: (row.get("date", ""), row.get("recording_id", "")))
        _save_index(index)
        return destination, not bool(duplicate), cues


def _recordings(page, folder_url: str) -> list[Recording]:
    page.goto(folder_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_000)
    anchors = page.locator('a[href*="/videos/"]')
    if anchors.count() == 0:
        raise StreamYardLoginRequired(
            "StreamYard login is not available to the scheduled browser profile; "
            "run `python studio.py streamyard-login` locally"
        )
    found: dict[str, Recording] = {}
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        href = anchor.get_attribute("href") or ""
        match = VIDEO_RE.search(href)
        if not match:
            continue
        recording_id = match.group(1)
        text = anchor.evaluate(
            """el => {
                let node = el;
                for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
                    const text = (node.innerText || '').trim();
                    if (/\\b\\d{4}\\b/.test(text)) return text;
                }
                return (el.innerText || '').trim();
            }"""
        )
        aired_at = _parse_air_date(text)
        if not aired_at:
            continue
        title = (anchor.inner_text() or text.splitlines()[0]).strip()
        url = href if href.startswith("http") else f"https://streamyard.com{href}"
        show = _classify_show(title)
        found[recording_id] = Recording(recording_id, title, aired_at, url, show)
    if not found:
        raise StreamYardError("recording cards were found, but no actual air dates could be read")
    return sorted(found.values(), key=lambda row: (row.aired_at, row.recording_id), reverse=True)


def _download_vtt(page, recording: Recording, download_dir: Path) -> Path:
    page.goto(recording.url, wait_until="domcontentloaded", timeout=60_000)
    page.get_by_role("button", name=re.compile(r"^Download$", re.I)).click(timeout=30_000)
    preparing = page.get_by_text(re.compile(r"Preparing for download", re.I))
    if preparing.count() and preparing.first.is_visible():
        raise StreamYardTranscriptPending(
            f"VTT is still being generated for {recording.recording_id}"
        )
    button = page.get_by_test_id("download-row-download-button-vtt")
    if button.count() == 0:
        raise StreamYardTranscriptPending(
            f"VTT is not available yet for {recording.recording_id}; StreamYard may still be generating it"
        )
    with page.expect_download(timeout=60_000) as event:
        button.click()
    download = event.value
    target = download_dir / f"{recording.recording_id}.vtt"
    download.save_as(target)
    return target


def _launch_context(playwright, *, headless: bool, channel: str | None):
    kwargs = {
        "user_data_dir": str(PROFILE),
        "headless": headless,
        "accept_downloads": True,
    }
    if channel:
        kwargs["channel"] = channel
    return playwright.chromium.launch_persistent_context(**kwargs)


def _chrome_executable() -> Path:
    found = shutil.which("chrome")
    candidates = [
        Path(found) if found else None,
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise StreamYardError("Google Chrome is not installed in a supported Windows location")


def _open_login_browser(folder_url: str):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([
        str(_chrome_executable()),
        f"--user-data-dir={PROFILE}",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        folder_url,
    ])


def login(
    folder_url: str, *, timeout_minutes: int = 15,
    browser_channel: str | None = "chrome", log=print,
) -> None:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    PROFILE.mkdir(parents=True, exist_ok=True)
    log("Opening a normal Chrome window with Studio's dedicated local profile.")
    log("Sign in to StreamYard, open the recordings folder, then close that entire Chrome window.")
    log("Studio never receives or logs your password.")
    process = _open_login_browser(folder_url)
    try:
        process.wait(timeout=max(1, timeout_minutes) * 60)
    except subprocess.TimeoutExpired as exc:
        update_job("streamyard-login", "failed", error="setup Chrome window stayed open past timeout")
        raise StreamYardLoginRequired(
            "close the dedicated Chrome window after StreamYard is signed in, then rerun setup"
        ) from exc
    deadline = time.monotonic() + 2 * 60
    with sync_playwright() as playwright:
        while time.monotonic() < deadline:
            browser = None
            try:
                browser = _launch_context(
                    playwright, headless=True, channel=browser_channel,
                )
                page = browser.pages[0] if browser.pages else browser.new_page()
                recordings = _recordings(page, folder_url)
                if recordings:
                    update_job(
                        "streamyard-login", "complete", folder_url=folder_url,
                        recordings_visible=len(recordings),
                    )
                    log(f"StreamYard login verified; {len(recordings)} recording(s) are visible.")
                    return
            except (StreamYardError, PlaywrightError, OSError):
                time.sleep(2)
            finally:
                if browser:
                    try:
                        browser.close()
                    except PlaywrightError:
                        pass
    update_job("streamyard-login", "failed", error="login was not verified before timeout")
    raise StreamYardLoginRequired("StreamYard login was not verified before the setup timeout")


def _import_latest(cfg: dict, *, limit: int = 3, retries: int = 2, log=print) -> list[dict]:
    from playwright.sync_api import sync_playwright

    settings = cfg.get("streamyard") or {}
    folder_url = str(settings.get("folder_url") or "").strip()
    browser_channel = str(settings.get("browser_channel") or "").strip() or None
    if not folder_url:
        raise StreamYardError("streamyard.folder_url is not configured")
    if not PROFILE.exists():
        raise StreamYardLoginRequired(
            "dedicated StreamYard browser login is not configured; "
            "run `python studio.py streamyard-login` locally"
        )
    indexed = {row.get("recording_id") for row in _load_index().get("files", [])}
    imported = []
    waiting = []
    last_error: Exception | None = None
    with sync_playwright() as playwright:
        browser = _launch_context(
            playwright, headless=True, channel=browser_channel,
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        try:
            recordings = _recordings(page, folder_url)
            pending = [row for row in recordings if row.recording_id not in indexed][:max(1, limit)]
            if not pending:
                update_job(
                    "streamyard-import", "complete", imported=0,
                    latest_recording=recordings[0].recording_id if recordings else None,
                    detail="no new recordings",
                )
                return []
            for recording in reversed(pending):
                for attempt in range(1, max(1, retries) + 1):
                    try:
                        with tempfile.TemporaryDirectory(prefix="layer8-streamyard-") as tmp:
                            source = _download_vtt(page, recording, Path(tmp))
                            destination, changed, cues = import_vtt(recording, source)
                        imported.append({
                            "recording_id": recording.recording_id,
                            "date": recording.aired_at.date().isoformat(),
                            "file": destination.name,
                            "show": recording.show,
                            "cues": cues,
                            "changed": changed,
                        })
                        log(
                            f"  imported {recording.recording_id} -> {destination.name} "
                            f"({cues} cues)"
                        )
                        break
                    except StreamYardTranscriptPending as exc:
                        last_error = exc
                        if attempt < retries:
                            time.sleep(5)
                            continue
                        log(f"  waiting: {exc}")
                        waiting.append({
                            "recording_id": recording.recording_id,
                            "date": recording.aired_at.date().isoformat(),
                            "error": str(exc),
                        })
                else:
                    continue
        finally:
            browser.close()
    if imported:
        update_job(
            "streamyard-import", "partial" if waiting else "complete",
            imported=len(imported), files=imported, waiting=waiting,
        )
        return imported
    if last_error:
        update_job("streamyard-import", "waiting", error=str(last_error))
        raise last_error
    update_job("streamyard-import", "complete", imported=0, detail="no downloadable transcripts")
    return []


def import_latest(cfg: dict, *, limit: int = 3, retries: int = 2, log=print) -> list[dict]:
    update_job("streamyard-import", "running")
    try:
        return _import_latest(cfg, limit=limit, retries=retries, log=log)
    except StreamYardTranscriptPending:
        raise
    except Exception as exc:
        update_job("streamyard-import", "failed", error=f"{type(exc).__name__}: {exc}")
        raise
