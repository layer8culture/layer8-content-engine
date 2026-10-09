"""Manual ChatGPT hero desk: clipboard + Downloads watcher + filing. Never controls chatgpt.com.

The user pastes one combined batch prompt into ChatGPT and clicks download. This module
only puts text on the clipboard, opens the site, watches Downloads, and files images.
Generic browser filenames require an explicit shot-number choice; arrival order is never
assumed unless the user deliberately presses ``n`` for the sequential fallback.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image

from .config import INBOX, media_dir

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
PARTIAL_EXTS = {".crdownload", ".tmp", ".partial", ".part", ".download"}
SHOT_RE = re.compile(r"^\s*(\d+)\.\s*\[([^\]]+)\]\s*(.*)$")
PROMPTS_RE = re.compile(r"^PROMPTS-(\d{4}-\d{2}-\d{2})\.md$")
CHATGPT_URL = "https://chatgpt.com"


@dataclass
class Shot:
    n: int
    filename: str
    text: str

    @property
    def aspect(self) -> str:
        return "9:16" if "9:16" in self.text else "4:5"


@dataclass
class Pack:
    date: date
    preamble: str
    shots: list[Shot]

    @property
    def step1(self) -> str:
        """Compatibility alias for callers/tests built around the original two-paste flow."""
        return self.preamble


def parse_prompts(text: str, d: date) -> Pack:
    lines = text.splitlines()

    def idx(prefix: str) -> int:
        for i, ln in enumerate(lines):
            if ln.strip().upper().startswith(prefix):
                return i
        raise ValueError(f"PROMPTS file has no '{prefix}' section")

    one = next((i for i, ln in enumerate(lines) if ln.strip().upper().startswith("## ONE-PASTE PROMPT")), None)
    if one is not None:
        shot_list = next((i for i in range(one + 1, len(lines)) if lines[i].strip().upper() == "SHOT LIST:"), None)
        if shot_list is None:
            raise ValueError("PROMPTS file has no 'SHOT LIST:' section")
        batch = next((i for i in range(one + 1, shot_list) if lines[i].strip().upper() == "BATCH REQUEST:"), shot_list)
        preamble = "\n".join(lines[one + 1:batch]).strip()
        shot_lines = lines[shot_list + 1:]
    else:
        s1, s2 = idx("## STEP 1"), idx("## STEP 2")
        preamble = "\n".join(lines[s1 + 1:s2]).strip()
        shot_lines = lines[s2 + 1:]
    shots = []
    for ln in shot_lines:
        m = SHOT_RE.match(ln)
        if m:
            shots.append(Shot(int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    shots.sort(key=lambda s: s.n)
    if not shots:
        raise ValueError("PROMPTS file has no numbered shots")
    return Pack(d, preamble, shots)


def step2_text(shots: list[Shot]) -> str:
    """Shot list for remaining shots, renumbered for a follow-up/continue request."""
    body = [f"{i}. [{s.filename}] {s.text}" for i, s in enumerate(shots, 1)]
    return "\n".join(["SHOT LIST:", *body])


def combined_prompt_text(pack: Pack, shots: list[Shot]) -> str:
    """One paste containing the locked style and all remaining shot instructions."""
    return "\n\n".join([
        pack.preamble.strip(),
        (
            "BATCH REQUEST:\n"
            f"Generate ALL {len(shots)} numbered shots from this single request when your image interface supports it. "
            "Each shot must be a SEPARATE downloadable image with the exact filename in brackets. "
            "Never combine shots into a collage, contact sheet, grid, diptych, or multi-panel image. "
            "Preserve each shot's requested aspect ratio and complete the full list without waiting for me to type “next”.\n\n"
            "If your current interface can only generate one image per response, generate the first remaining shot now. "
            "When I say “continue”, generate the rest in order without making me paste the style or list again."
        ),
        step2_text(shots),
        "Begin now. Return images only, each as its own downloadable file.",
    ])


def match_download(path: Path, shots: list[Shot]) -> Shot | None:
    """Reliably map only an exact expected filename/stem; generic names need confirmation."""
    stem = path.stem.casefold()
    return next((s for s in shots if Path(s.filename).stem.casefold() == stem), None)


def hero_present(filename: str, d: date, inbox: Path = INBOX) -> bool:
    """Already saved in inbox/, already ingested (inbox/_used), or already cropped into the day's media."""
    stem = Path(filename).stem
    for folder in (inbox, inbox / "_used"):
        for ext in IMAGE_EXTS:
            if (folder / f"{stem}{ext}").exists() or (folder / f"{stem}{ext.upper()}").exists():
                return True
    return (media_dir(d) / "heroes" / filename).exists()


def remaining(pack: Pack, inbox: Path = INBOX) -> list[Shot]:
    return [s for s in pack.shots if not hero_present(s.filename, pack.date, inbox)]


def load_pack(d: date, inbox: Path = INBOX) -> Pack:
    p = inbox / f"PROMPTS-{d.isoformat()}.md"
    if not p.exists():
        raise FileNotFoundError(f"no prompt pack at {p}")
    return parse_prompts(p.read_text(encoding="utf-8"), d)


def default_date(today: date, inbox: Path = INBOX) -> date | None:
    """Earliest PROMPTS date >= today that still has missing heroes."""
    dates = sorted(date.fromisoformat(m.group(1)) for f in inbox.glob("PROMPTS-*.md") if (m := PROMPTS_RE.match(f.name)))
    for d in dates:
        if d >= today and remaining(load_pack(d, inbox), inbox):
            return d
    return None


def downloads_dir(cfg: dict) -> Path:
    override = os.environ.get("STUDIO_DOWNLOADS_DIR") or ((cfg.get("heroes") or {}).get("downloads_dir") or "")
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

            u = UUID("374DE290-123F-4565-9164-39C4925E467B")  # FOLDERID_Downloads
            g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                return Path(path)
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Downloads"


class DownloadWatcher:
    """Reports image files that appear after start and have stopped growing."""

    def __init__(self, folder: Path, settle_seconds: float = 1.0, clock=time.monotonic):
        self.folder = folder
        self.settle = settle_seconds
        self.clock = clock
        self.started = time.time()
        self.preexisting = {(p.name, p.stat().st_mtime_ns) for p in folder.iterdir()} if folder.exists() else set()
        self.seen: set[str] = set()
        self.sizes: dict[str, tuple[int, float]] = {}

    def poll(self) -> list[Path]:
        ready = []
        if not self.folder.exists():
            return ready
        now = self.clock()
        for p in sorted(self.folder.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0):
            name = p.name
            if name in self.seen or not p.is_file():
                continue
            try:
                if (name, p.stat().st_mtime_ns) in self.preexisting:
                    continue
            except OSError:
                continue
            ext = p.suffix.lower()
            if ext in PARTIAL_EXTS or ext not in IMAGE_EXTS:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            prev = self.sizes.get(name)
            if prev is None or prev[0] != size or size == 0:
                self.sizes[name] = (size, now)
                continue
            if now - prev[1] >= self.settle:
                self.seen.add(name)
                ready.append(p)
        return ready

    def forget(self, name: str) -> None:
        """Called once a file is filed away, so a later download reusing the name is picked up."""
        self.seen.discard(name)
        self.sizes.pop(name, None)


@dataclass
class Desk:
    """Tracks explicit/out-of-order saves plus an opt-in sequential fallback."""
    shots: list[Shot]
    last: Shot | None = None
    redo_pending: bool = False
    skipped: list[Shot] = field(default_factory=list)
    saved: list[Shot] = field(default_factory=list)

    @property
    def current(self) -> Shot | None:
        return next((s for s in self.shots if s not in self.saved and s not in self.skipped), None)

    @property
    def done(self) -> bool:
        return self.current is None and not self.redo_pending

    def redo(self) -> Shot | None:
        if self.last:
            self.redo_pending = True
        return self.last

    def skip(self) -> Shot | None:
        s = self.current
        if s:
            self.skipped.append(s)
        return s

    def accept(self, shot: Shot) -> Shot:
        if shot not in self.saved:
            self.saved.append(shot)
        if shot in self.skipped:
            self.skipped.remove(shot)
        self.last = shot
        self.redo_pending = False
        return shot

    def target(self) -> Shot | None:
        """Choose the next shot only after the user explicitly requests sequential mapping."""
        if self.redo_pending and self.last:
            return self.accept(self.last)
        s = self.current
        if s:
            return self.accept(s)
        return None


def file_image(src: Path, shot: Shot, inbox: Path = INBOX) -> tuple[Path, str | None]:
    """Move a download into inbox/ as <shot filename>.png. Returns (dest, aspect warning or None)."""
    dest = inbox / f"{Path(shot.filename).stem}.png"
    inbox.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    with Image.open(src) as im:
        w, h = im.size
        if src.suffix.lower() == ".png":
            im.close()
            shutil.move(str(src), str(dest))
        else:
            im.convert("RGB").save(dest, "PNG", optimize=True)
            im.close()
            src.unlink(missing_ok=True)
    want = 9 / 16 if shot.aspect == "9:16" else 4 / 5
    warn = None
    if abs((w / h) - want) / want > 0.12:
        warn = f"image is {w}x{h} ({w / h:.2f}); expected about {shot.aspect} — it will be center-cropped"
    return dest, warn


def copy_to_clipboard(text: str) -> bool:
    if os.name != "nt":
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        tmp = fh.name
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Get-Content -Raw -Encoding UTF8 -LiteralPath '{tmp}' | Set-Clipboard"],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    finally:
        os.unlink(tmp)


def toast(title: str, body: str) -> bool:
    """Best-effort Windows toast; silently does nothing if WinRT notifications aren't available."""
    if os.name != "nt":
        return False
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace("'", "''")  # noqa: E731
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
        "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text><text>{esc(body)}</text></binding></visual></toast>');"
        "$app='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def read_key() -> str | None:
    if os.name != "nt":
        return None
    import msvcrt

    if msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()
            return None
        return "enter" if ch in ("\r", "\n") else ch.lower()
    return None


def run(pack: Pack, cfg: dict, *, log=print, open_browser: bool = True, keys=read_key, sleep=time.sleep,
        watcher: DownloadWatcher | None = None, clipboard=copy_to_clipboard, inbox: Path = INBOX) -> Desk:
    todo = remaining(pack, inbox)
    desk = Desk(todo)
    if not todo:
        log(f"All {len(pack.shots)} heroes for {pack.date} are already in. Nothing to do.")
        return desk
    folder = downloads_dir(cfg)
    watcher = watcher or DownloadWatcher(folder, float((cfg.get("heroes") or {}).get("settle_seconds", 1.0)))
    log(f"Hero desk — {pack.date}: {len(todo)} of {len(pack.shots)} shots to make")
    for i, s in enumerate(todo, 1):
        log(f"  {i}. {s.filename} ({s.aspect})")
    log(f"Watching downloads in: {folder}")
    ok = clipboard(combined_prompt_text(pack, todo))
    if open_browser:
        os.startfile(CHATGPT_URL) if os.name == "nt" else None  # just opens the URL; we never control the page
    log("")
    log("ONE combined prompt is " + ("on your clipboard" if ok else "NOT on the clipboard (copy it from the PROMPTS file)")
        + " — start a NEW ChatGPT chat, paste once, and send.")
    log("Ask ChatGPT to continue if it returns only one image; multi-image output is interface-dependent, not guaranteed by Pro.")
    log("Download every image. Exact filenames map automatically; generic names wait for your explicit shot number.")
    log("Keys: 1-9 = map pending download to shot   n = map to next shot (sequential fallback)")
    log("      Enter = copy a prompt for remaining shots   r = redo last   s = skip next   q = quit")
    pending: list[Path] = []

    def save(src: Path, shot: Shot) -> None:
        try:
            dest, warn = file_image(src, desk.accept(shot), inbox)
        except Exception as exc:  # noqa: BLE001
            log(f"  ✗ could not read {src.name}: {exc}")
            desk.redo_pending = True
            return
        watcher.forget(src.name)
        n = todo.index(shot) + 1
        log(f"✓ {n}/{len(todo)} saved as {dest.name}" + (f"  ⚠ {warn}" if warn else ""))

    while not desk.done:
        k = keys()
        if k == "enter":
            left = [s for s in todo if s not in desk.saved and s not in desk.skipped]
            ok2 = clipboard(combined_prompt_text(pack, left))
            log("Remaining-shots prompt copied — paste it into the same ChatGPT chat."
                if ok2 else "Could not copy; use the PROMPTS file and ask ChatGPT to continue.")
        elif k and k.isdigit() and k != "0" and pending:
            i = int(k) - 1
            if i >= len(todo):
                log(f"shot {k} does not exist")
            else:
                save(pending.pop(0), todo[i])
        elif k == "n" and pending:
            shot = desk.target()
            if shot:
                save(pending.pop(0), shot)
        elif k == "r":
            s = desk.redo()
            log(f"↺ redo {s.filename}: download it again; exact name auto-maps, or select its number."
                if s else "nothing to redo yet")
        elif k == "s":
            s = desk.skip()
            if s:
                log(f"» skipped {s.filename} (the template version will be used)")
        elif k == "q":
            log("quit — heroes saved so far are kept")
            break
        for f in watcher.poll():
            shot = match_download(f, todo)
            if shot:
                save(f, shot)
            else:
                pending.append(f)
                log(f"? {f.name} has no reliable filename mapping. Press 1-{len(todo)} to assign it, "
                    "or n for the next missing shot.")
        if not desk.done:
            sleep(0.4)
    return desk
