"""Local web UI for the ad-hoc (no-API) image run.

Drives the existing manual-image-mode scripts from a browser instead of a series
of hand-typed commands:

    pick a queue -> copy one prompt block -> drop ChatGPT's .zip ->
    reconcile -> ingest -> render reels -> review

It can also start a batch from nothing, running the same Copilot CLI command the
nightly workflows run, and remove queue files or individual posts (moving them to
a local .trash/ folder rather than deleting outright).

Nothing about the brand lives here. ``manual_media.plan_images`` stays the single
source of truth for which images a queue needs, the generation prompts stay in
``scripts/`` and ``clients/``, and the actual work is done by running
``scripts/manual_media_ingest.py``, ``scripts/reel_gen.py`` and ``copilot`` as
subprocesses, exactly as they run from a terminal.

Deliberately dependency-light: Python stdlib plus Pillow (already required by the
engine) purely to read image dimensions for the shape check.

Usage:
    python scripts/adhoc_server.py            # http://127.0.0.1:8765
    python scripts/adhoc_server.py --port 9000 --no-browser

Binds to 127.0.0.1 only and has no authentication: it is a single-user local tool
that can write into the working tree, so do not expose it to a network.
"""
import argparse
import datetime as dt
import functools
import hashlib
import json
import mimetypes
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote
from zoneinfo import ZoneInfo

import manual_media
import openai_gen
import app_state
import guided_workflow

from PIL import Image

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
CODE_ROOT = SCRIPTS_DIR.parent
REPO_ROOT = pathlib.Path(os.environ.get("LAYER8_DATA_ROOT", str(CODE_ROOT))).resolve()
QUEUE_DIR = REPO_ROOT / "queue"
INBOX_DIR = REPO_ROOT / manual_media.DEFAULT_INBOX
OUT_DIR = REPO_ROOT / manual_media.DEFAULT_OUT_DIR
WEBAPP_DIR = CODE_ROOT / "webapp"
TRASH_DIR = REPO_ROOT / ".trash"
FONTS_DIR = REPO_ROOT / "assets" / "fonts"

# The brand typefaces the UI serves, per brand/brand-guidelines-v2.md section 14:
# Space Grotesk for display, Inter for body. Self-hosted from assets/fonts/ so the
# UI renders correctly offline, and mapped through an explicit allowlist rather
# than a directory prefix so this route can never become a general file reader.
BRAND_FONTS = {
    "space-grotesk.ttf": "SpaceGrotesk-Variable.ttf",
    "inter.ttf": "Inter-Variable.ttf",
}

# Zip entries that don't match an expected image land here until a human assigns
# them. Kept inside the inbox so everything manual lives in one place, and named
# with a leading underscore so manual_media.find_source never walks into it.
UNASSIGNED_DIRNAME = "_unassigned"

# Queue files are always "<something>.json" directly inside queue/.
QUEUE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
# Media/staged filenames: no separators, no traversal.
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CSRF_TOKEN = secrets.token_urlsafe(32)
MUTATION_LOCK = threading.RLock()


@functools.lru_cache(maxsize=8)
def _store(root: str) -> app_state.StateStore:
    return app_state.StateStore(pathlib.Path(root))


def state_store() -> app_state.StateStore:
    return _store(str(REPO_ROOT.resolve()))


def readiness(qpath: pathlib.Path, *, require_future: bool = True) -> dict:
    import batch_readiness
    report = batch_readiness.report(qpath, REPO_ROOT, require_future=require_future)
    if not report.get("revision"):
        report["revision"] = hashlib.sha256(qpath.read_bytes()).hexdigest()
    return report

# Which brand lane a queue file belongs to, by filename prefix.
LANE_PREFIXES = (
    ("lofi-", "Layer8Culture Radio"),
    ("deallab-", "The Real Estate Deal Lab"),
    ("weekly-guide-", "Weekly Guide"),
)
DEFAULT_LANE = "Layer8Culture"

# The lanes a batch can be generated for, mirroring the workflows exactly:
#   generate-content.yml:36, generate-lofi.yml:46, generate-deallab.yml:27
# ``offset_days`` is what the prompt itself would pick relative to today in
# ``TZ``: the Layer8Culture prompt writes *tomorrow's* file, lofi and Deal Lab
# write *today's*. It is only the default -- the UI can ask for any date.
#
# Weekly Guide is deliberately absent: it is script-driven
# (research_weekly_guide.py + build_weekly_guide.py) and needs the sibling site
# repo, not a Copilot CLI run.
TZ = ZoneInfo("America/New_York")

# Escape hatch for a non-standard Copilot CLI install. See copilot_command().
COPILOT_ENV = "LAYER8_COPILOT_CLI"

GENERATION_LANES = {
    "layer8culture": {
        "label": "Layer8Culture",
        "prompt": "scripts/generation-prompt.md",
        "prefix": "",
        "offset_days": 1,
        "allow_tool": "read,write(queue/*),web-fetch",
        "allow_all_urls": True,
        "blurb": "4-5 viral short-form videos + Instagram carousels and a story.",
    },
    "lofi": {
        "label": "Layer8Culture Radio",
        "prompt": "scripts/generation-prompt-lofi.md",
        "prefix": "lofi-",
        "offset_days": 0,
        "allow_tool": "read,write(queue/*)",
        "allow_all_urls": False,
        "blurb": "Evergreen focus-music content and 24/7 livestream promos.",
    },
    "deallab": {
        "label": "The Real Estate Deal Lab",
        "prompt": "clients/therealestatedeallab/generation-prompt.md",
        "prefix": "deallab-",
        "offset_days": 0,
        "allow_tool": "read,write(queue/*)",
        "allow_all_urls": False,
        "blurb": "Client lane: premium real estate deal education, Instagram only.",
    },
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Expected width/height ratio per brand aspect, used for the shape check.
ASPECT_RATIO = {"1:1": 1.0, "9:16": 9 / 16, "16:9": 16 / 9}
# A hand-generated image is rarely pixel-exact; allow a little slack but stay far
# tighter than the gap between any two brand aspects (0.5625 vs 1.0 vs 1.778).
SHAPE_TOLERANCE = 0.08


def acceptable_ratios(aspect: str) -> list[float]:
    """Every width/height ratio a source image for ``aspect`` may legitimately have.

    Covers both the nominal brand ratio and the generation canvas named in the
    prompt (``manual_media.ASPECT_SIZE``), since a just-generated portrait is
    1024x1536 and only becomes a true 9:16 once ingest crops it.
    """
    ratios: list[float] = []
    nominal = ASPECT_RATIO.get(aspect)
    if nominal:
        ratios.append(nominal)
    canvas = getattr(manual_media, "ASPECT_SIZE", {}).get(aspect)
    if canvas and "x" in canvas:
        try:
            width, height = (int(part) for part in canvas.lower().split("x", 1))
        except ValueError:
            return ratios
        if width > 0 and height > 0:
            ratio = width / height
            if all(abs(ratio - known) > 1e-6 for known in ratios):
                ratios.append(ratio)
    return ratios

# Guard rails for uploads: a batch is ~20 images, so this is generous.
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 500


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------
def _is_inside(path: pathlib.Path, parent: pathlib.Path) -> bool:
    """True when ``path`` resolves to somewhere inside ``parent``."""
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def safe_queue_path(name: str) -> pathlib.Path:
    """Resolve a queue file name to a path inside queue/, or raise ValueError."""
    name = unquote(name or "")
    if not QUEUE_NAME_RE.match(name):
        raise ValueError(f"invalid queue name: {name!r}")
    path = QUEUE_DIR / name
    if not _is_inside(path, QUEUE_DIR):
        raise ValueError(f"queue path escapes queue/: {name!r}")
    if not path.is_file():
        raise ValueError(f"no such queue file: {name!r}")
    return path


MEDIA_ROOTS = {
    "generated": lambda: OUT_DIR,
    "library": lambda: REPO_ROOT / "assets" / "library",
    "inbox": lambda: INBOX_DIR,
    "unassigned": lambda: INBOX_DIR / UNASSIGNED_DIRNAME,
    "original": lambda: INBOX_DIR / manual_media.INGESTED_DIRNAME,
}


def safe_media_path(root: str, name: str) -> pathlib.Path:
    """Resolve ``<root>/<name>`` for serving, or raise ValueError."""
    name = unquote(name or "")
    if root not in MEDIA_ROOTS:
        raise ValueError(f"unknown media root: {root!r}")
    if root == "library":
        relative = pathlib.PurePosixPath(name.replace("\\", "/"))
        if (relative.is_absolute() or not relative.parts
                or any(part in (".", "..") or not SAFE_NAME_RE.fullmatch(part)
                       for part in relative.parts)):
            raise ValueError(f"invalid media path: {name!r}")
        name = relative.as_posix()
    elif not SAFE_NAME_RE.match(name):
        raise ValueError(f"invalid media name: {name!r}")
    base = MEDIA_ROOTS[root]()
    path = base / name
    if not _is_inside(path, base):
        raise ValueError(f"media path escapes {root}: {name!r}")
    return path


# --------------------------------------------------------------------------
# Image shape check
# --------------------------------------------------------------------------
def image_size(path: pathlib.Path) -> tuple[int, int] | None:
    """(width, height) without decoding the whole image, or None if unreadable."""
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - a corrupt drop must not break the report
        return None


def shape_matches(size: tuple[int, int] | None, aspect: str,
                  tolerance: float = SHAPE_TOLERANCE) -> bool | None:
    """Does a file's pixel shape agree with the aspect the queue asked for?

    Two ratios are acceptable per aspect: the nominal brand ratio (what a finished,
    cropped asset looks like) and the generation canvas the prompt actually asks
    for -- "9:16" is generated at 1024x1536 (0.667) and only becomes a true 0.5625
    after ingest crops it. Checking the nominal ratio alone would flag every
    freshly generated portrait as suspect.

    Returns None when the size is unknown or the aspect is one we don't model, so
    callers can distinguish "wrong shape" from "couldn't tell".
    """
    expected = acceptable_ratios(aspect)
    if not size or not expected:
        return None
    width, height = size
    if width <= 0 or height <= 0:
        return None
    actual = width / height
    return any(abs(actual - ratio) / ratio <= tolerance for ratio in expected)


def describe_shape(size: tuple[int, int] | None) -> str:
    return f"{size[0]}x{size[1]}" if size else "unreadable"


# --------------------------------------------------------------------------
# Queue + image plan
# --------------------------------------------------------------------------
def lane_for(name: str) -> str:
    for prefix, label in LANE_PREFIXES:
        if name.startswith(prefix):
            return label
    return DEFAULT_LANE


def load_posts(qpath: pathlib.Path) -> list[dict]:
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    if not isinstance(posts, list):
        raise ValueError(f"{qpath.name}: queue JSON must be a list of posts")
    return posts


def plan_for(posts: list[dict]) -> list[manual_media.ImageSpec]:
    """The images this queue needs, using the same defaults the ingest uses."""
    return manual_media.plan_images(
        posts, openai_gen.IMAGE_QUALITY, openai_gen.DEFAULT_OVERLAY_POSITION)


def unassigned_dir(create: bool = False, queue_name: str | None = None) -> pathlib.Path:
    path = INBOX_DIR / UNASSIGNED_DIRNAME
    if queue_name is not None:
        if not QUEUE_NAME_RE.fullmatch(queue_name):
            raise ValueError("Invalid batch name.")
        path = path / queue_name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def list_unassigned(directory: pathlib.Path | None = None) -> list[pathlib.Path]:
    path = directory if directory is not None else unassigned_dir()
    if not path.is_dir():
        return []
    return sorted(
        p for p in path.iterdir()
        if p.is_file() and p.suffix.lower() in manual_media.ACCEPTED_SUFFIXES
    )


def spec_status(spec: manual_media.ImageSpec) -> tuple[str, pathlib.Path | None]:
    """('done'|'ready'|'pending', the file backing that status)."""
    dropped = spec.find_source(INBOX_DIR)
    if dropped is not None:
        return "ready", dropped
    finished = spec.output_path(OUT_DIR)
    if finished.is_file() and image_size(finished):
        return "done", finished
    return "pending", None


def spec_payload(spec: manual_media.ImageSpec) -> dict:
    status, backing = spec_status(spec)
    payload = {
        "image_id": spec.image_id,
        "post_id": spec.post_id,
        "filename": spec.filename,
        "aspect": spec.aspect,
        "size": spec.size,
        "format": spec.fmt,
        "slide_index": spec.slide_index,
        "slide_role": spec.slide_role,
        "headline": spec.headline,
        "subtext": spec.subtext,
        "scene": spec.prompt,
        "prompt": manual_media.copy_prompt(spec),
        "status": status,
        "shape_ok": None,
        "actual_size": None,
    }
    if backing is not None:
        size = image_size(backing)
        payload["actual_size"] = describe_shape(size)
        payload["shape_ok"] = shape_matches(size, spec.aspect)
        root = "generated" if status == "done" else "inbox"
        payload["preview"] = f"/api/media/{root}/{backing.name}"
    source = guided_workflow.original(spec, REPO_ROOT)
    if source is not None:
        payload["original"] = f"/api/media/original/{quote(source.name)}"
    finished = spec.output_path(OUT_DIR)
    if finished.is_file():
        payload["final"] = f"/api/media/generated/{quote(finished.name)}"
    payload["has_history"] = bool(guided_workflow.versions(REPO_ROOT, spec.image_id))
    return payload


def post_payload(post: dict) -> dict:
    """Just enough of a post for the review step."""
    import publish_helpers
    visual = post.get("visual") or {}
    media: list[str] = []
    if post.get("format") == "carousel":
        media.extend(str(value) for value in visual.get("files") or [])
    elif visual.get("file"):
        media.append(str(visual["file"]))
    if visual.get("cover") and post.get("format") != "carousel":
        media.append(str(visual["cover"]))

    previews = []
    for rel in (media if post.get("format") == "carousel" else dict.fromkeys(media)):
        parts = pathlib.PurePosixPath(rel.replace("\\", "/")).parts
        if len(parts) < 3 or parts[:2] not in (("assets", "generated"), ("assets", "library")):
            continue
        root = parts[1]
        name = "/".join(parts[2:])
        path = safe_media_path(root, name)
        if path.is_file():
            previews.append({
                "name": name,
                "url": f"/api/media/{root}/{quote(name, safe='')}",
                "is_video": name.lower().endswith(publish_helpers.VIDEO_EXTS),
            })
    return {
        "id": post.get("id"),
        "platform": post.get("platform"),
        "format": post.get("format"),
        "account": post.get("account"),
        "category": post.get("category"),
        "schedule_time": post.get("schedule_time"),
        "hook_score": post.get("hook_score"),
        "viral_pillar": post.get("viral_pillar"),
        "youtube_title": post.get("youtube_title"),
        "text": post.get("text"),
        "hashtags": post.get("hashtags") or [],
        "first_comment": post.get("first_comment"),
        "visual_source": visual.get("source"),
        "previews": previews,
    }


def queue_summary(qpath: pathlib.Path) -> dict:
    """Headline counts for the queue picker; tolerant of unreadable files."""
    entry = {
        "name": qpath.name,
        "lane": lane_for(qpath.name),
        "modified": qpath.stat().st_mtime,
        "summary": (qpath.parent / f"{qpath.stem}.summary.md").is_file(),
        "posts": 0,
        "images": 0,
        "done": 0,
        "ready": 0,
        "pending": 0,
        "error": None,
    }
    try:
        posts = load_posts(qpath)
        specs = plan_for(posts)
    except Exception as e:  # noqa: BLE001 - a broken queue should still be listed
        entry["error"] = str(e)
        return entry
    entry["posts"] = len(posts)
    entry["images"] = len(specs)
    for spec in specs:
        entry[spec_status(spec)[0]] += 1
    entry["status"] = "needs_review"
    entry["next_action"] = "review"
    if entry["pending"]:
        entry.update(status="needs_images", next_action="images")
    elif entry["ready"]:
        entry.update(status="needs_preparation", next_action="prepare")
    try:
        if any(dt.datetime.fromisoformat(p["schedule_time"]) <= dt.datetime.now(TZ)
               for p in posts):
            entry.update(status="schedule_expired", next_action="reschedule")
    except (ValueError, KeyError, TypeError):
        entry.update(status="invalid_schedule", next_action="reschedule")
    approval = state_store().approval(qpath.name)
    if approval and approval.get("state") == "merged":
        entry.update(status="has_merged_approval", next_action="delivery")
    return entry


def queue_payload(qpath: pathlib.Path) -> dict:
    import publish_helpers
    import queue_json_guard
    report = readiness(qpath)
    try:
        posts = load_posts(qpath)
        queue_json_guard.validate_queue_shape(posts, qpath)
    except ValueError as exc:
        return {
            "name": qpath.name, "lane": lane_for(qpath.name), "error": str(exc),
            "images": [], "staged": [], "posts": [], "prompt_groups": [],
            "readiness": report, "revision": report["revision"], "approval": None,
            "delivery": [], "active_job": active_job_id(),
        }
    specs = plan_for(posts)
    outstanding = [s.image_id for s in specs if spec_status(s)[0] == "pending"]
    groups = []
    for index, start in enumerate(range(0, len(outstanding), 4)):
        ids = outstanding[start:start + 4]
        groups.append({"index": index, "image_ids": ids, "prompt": batch_prompt(specs, ids)})
    approval = state_store().approval(qpath.name)
    observed = state_store().observation(qpath.name)
    if observed.get("revision") != report["revision"]:
        observed = {}
    if approval and approval.get("revision") != report["revision"]:
        approval = dict(approval, state="stale", detail="Content changed. Prepare a new approval revision.")
    images = [spec_payload(s) for s in specs]
    for image in images:
        if image.get("original"):
            image["original"] = f"/api/media/source/{quote(qpath.name)}/{quote(image['image_id'])}"
    return {
        "name": qpath.name, "lane": lane_for(qpath.name),
        "images": images,
        "staged": staged_payload(specs, unassigned_dir(queue_name=qpath.name), qpath.name),
        "posts": [post_payload(p) for p in posts], "active_job": active_job_id(),
        "readiness": report, "revision": report["revision"],
        "prompt_groups": groups, "approval": approval,
        "delivery": publish_helpers.delivery_status(posts, REPO_ROOT),
        "workflow": observed.get("workflow"),
        "delivery_observed_at": observed.get("observed_at"),
        "observed_at": observed.get("observed_at"),
    }


def diagnostics() -> list[dict]:
    entries = [
        {"name": "Python", "ok": True, "detail": sys.version.split()[0]},
        {"name": "ffmpeg", "ok": shutil.which("ffmpeg") is not None,
         "detail": "Required only for video preparation."},
        {"name": "GitHub CLI", "ok": shutil.which("gh") is not None,
         "detail": "Required for approval. Sign in with gh auth login if requested."},
        {"name": "Brand fonts", "ok": all((FONTS_DIR / v).is_file() for v in BRAND_FONTS.values()),
         "detail": "Self-hosted fonts; no external font service."},
    ]
    try:
        copilot_command()
    except ValueError as exc:
        entries.append({"name": "Copilot CLI", "ok": False, "detail": str(exc)})
    else:
        entries.append({"name": "Copilot CLI", "ok": True,
                        "detail": "Installed. An authenticated session is required for new batch generation."})
    return entries


# --------------------------------------------------------------------------
# The one-paste prompt block
# --------------------------------------------------------------------------
RULE = "=" * 72
THIN = "-" * 72


def batch_prompt(specs: list[manual_media.ImageSpec],
                 only: list[str] | None = None) -> str:
    """Everything needed for one paste into ChatGPT, asking for one named zip."""
    if only is not None:
        wanted = set(only)
        specs = [s for s in specs if s.image_id in wanted]
    if not specs:
        return "Nothing to generate — every image for this queue is already done."

    out = [
        f"I need {len(specs)} images generated. Please produce all of them and "
        "return a single .zip file.",
        "",
        "RULES",
        "1. Name every file EXACTLY as the FILENAME given for that image — no "
        "prefixes, no numbering, no renaming, keep the .png extension.",
        f"2. Return ONE .zip containing all {len(specs)} files at the top level.",
        "3. Render no text, letters, numbers, logos or watermarks in any image.",
        "4. Use the exact aspect ratio and canvas size stated for each image.",
        "",
    ]
    for index, spec in enumerate(specs, 1):
        out.append(RULE)
        out.append(f"IMAGE {index} of {len(specs)}")
        out.append(f"FILENAME: {spec.filename}")
        out.append(THIN)
        out.append(manual_media.copy_prompt(spec))
        out.append("")
    out.append(RULE)
    out.append(
        f"Reminder: return all {len(specs)} images in one .zip, each named exactly "
        "as its FILENAME above."
    )
    return "\n".join(out)


# --------------------------------------------------------------------------
# Zip intake
# --------------------------------------------------------------------------
def slugify_name(name: str) -> str | None:
    """Rewrite a basename so it satisfies ``SAFE_NAME_RE``, or None if hopeless.

    Image tools hand back names like ``Generated image (4).png`` or
    ``ChatGPT Image Aug 18, 2026, 09_41_12 AM.png``. Those are perfectly safe once
    reduced to a basename, so they are cleaned rather than dropped -- rejecting
    them would discard exactly the files the reconciliation step exists to place.
    """
    suffix = pathlib.PurePosixPath(name).suffix
    stem = name[:len(name) - len(suffix)] if suffix else name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_")
    cleaned = re.sub(r"_{2,}", "_", cleaned).lstrip(".-")
    if not cleaned:
        return None
    if not cleaned[0].isalnum():
        cleaned = f"x{cleaned}"
    candidate = f"{cleaned[:120]}{suffix.lower()}"
    return candidate if SAFE_NAME_RE.match(candidate) else None


def sanitize_entry_name(raw: str) -> str | None:
    """The safe basename to store a zip entry under, or None to skip it.

    Never trusts the archive's path: directories are dropped and only the final
    component is kept, so a crafted entry cannot escape the target directory.
    """
    if not raw or raw.endswith("/"):
        return None
    normalized = raw.replace("\\", "/")
    if normalized.startswith("__MACOSX/") or "/__MACOSX/" in normalized:
        return None
    name = pathlib.PurePosixPath(normalized).name
    if not name or name.startswith(".") or name in ("..", "."):
        return None
    if pathlib.PurePosixPath(name).suffix.lower() not in manual_media.ACCEPTED_SUFFIXES:
        return None
    if SAFE_NAME_RE.match(name):
        return name
    return slugify_name(name)


def unique_destination(directory: pathlib.Path, name: str) -> pathlib.Path:
    """``name`` inside ``directory``, suffixed if something is already there."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = pathlib.Path(name).stem
    suffix = pathlib.Path(name).suffix
    for n in range(2, 1000):
        candidate = directory / f"{stem}--{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError(f"cannot find a free filename for {name}")


def extract_zip(data: bytes, dest: pathlib.Path) -> tuple[list[str], list[str]]:
    """Extract every usable image in ``data`` into ``dest``.

    Returns (stored filenames, skipped descriptions). Raises ValueError when the
    payload isn't a readable zip.
    """
    dest.mkdir(parents=True, exist_ok=True)
    stored: list[str] = []
    skipped: list[str] = []
    try:
        archive = zipfile.ZipFile(_BytesReader(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"not a readable .zip file ({e})") from e

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError(f"zip has {len(infos)} entries; refusing more than {MAX_ENTRIES}")
        for info in infos:
            if info.is_dir():
                continue
            name = sanitize_entry_name(info.filename)
            if name is None:
                skipped.append(info.filename)
                continue
            if info.file_size > MAX_ENTRY_BYTES:
                skipped.append(f"{info.filename} (too large)")
                continue
            target = unique_destination(dest, name)
            if not _is_inside(target, dest):
                skipped.append(f"{info.filename} (unsafe path)")
                continue
            with archive.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 256)
            stored.append(target.name)
    return stored, skipped


class _BytesReader:
    """Minimal seekable wrapper so ZipFile can read an in-memory upload."""

    def __init__(self, data: bytes):
        self._buf = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._buf[self._pos:]
            self._pos = len(self._buf)
            return chunk
        chunk = self._buf[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        else:
            self._pos = len(self._buf) + offset
        self._pos = max(0, min(self._pos, len(self._buf)))
        return self._pos

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return True


def auto_reconcile(specs: list[manual_media.ImageSpec],
                   directory: pathlib.Path | None = None) -> list[dict]:
    """Move confidently-matching staged files into the inbox.

    A staged file is auto-accepted only when its stem matches an outstanding image
    id AND its pixel shape agrees with that image's aspect. A name match with the
    wrong shape is left staged and reported as "suspect" — filenames alone have
    already proved untrustworthy once.
    """
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    outstanding = {}
    for spec in specs:
        if spec_status(spec)[0] == "pending":
            outstanding[spec.image_id.lower()] = spec

    moved: list[dict] = []
    for staged in list_unassigned(directory):
        spec = outstanding.get(staged.stem.lower())
        if spec is None:
            continue
        size = image_size(staged)
        if shape_matches(size, spec.aspect) is not True:
            continue
        try:
            with Image.open(staged) as image:
                image.verify()
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
        target = INBOX_DIR / f"{spec.image_id}{staged.suffix.lower()}"
        if target.exists():
            continue
        staged.replace(target)
        outstanding.pop(spec.image_id.lower(), None)
        moved.append({"image_id": spec.image_id, "file": target.name})
    return moved


def staged_payload(specs: list[manual_media.ImageSpec],
                   directory: pathlib.Path | None = None,
                   queue_name: str | None = None) -> list[dict]:
    """Staged files with the reason they're still staged and their shape."""
    by_id = {spec.image_id.lower(): spec for spec in specs}
    entries = []
    for staged in list_unassigned(directory):
        size = image_size(staged)
        spec = by_id.get(staged.stem.lower())
        reason = "unmatched"
        if spec is not None:
            status = spec_status(spec)[0]
            if status != "pending":
                reason = "slot already filled"
            elif shape_matches(size, spec.aspect) is False:
                reason = "suspect"
        url = (f"/api/media/staged/{quote(queue_name)}/{quote(staged.name)}"
               if queue_name else f"/api/media/unassigned/{quote(staged.name)}")
        entries.append({
            "file": staged.name,
            "url": url,
            "size": describe_shape(size),
            "ratio": round(size[0] / size[1], 4) if size else None,
            "name_matches": spec.image_id if spec is not None else None,
            "reason": reason,
        })
    return entries


def assign_staged(specs: list[manual_media.ImageSpec], filename: str,
                  image_id: str, directory: pathlib.Path | None = None) -> dict:
    """Bind one staged file to one expected image by renaming it into the inbox."""
    if not SAFE_NAME_RE.match(filename or ""):
        raise ValueError(f"invalid staged filename: {filename!r}")
    directory = directory if directory is not None else unassigned_dir()
    source = directory / filename
    if not _is_inside(source, directory) or not source.is_file():
        raise ValueError(f"no staged file named {filename!r}")
    with Image.open(source) as image:
        image.verify()

    spec = next((s for s in specs if s.image_id == image_id), None)
    if spec is None:
        raise ValueError(f"{image_id!r} is not an image this queue needs")
    status = spec_status(spec)[0]
    if status != "pending":
        raise ValueError(f"{image_id} is already {status}")

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    target = INBOX_DIR / f"{spec.image_id}{source.suffix.lower()}"
    source.replace(target)
    return {"image_id": spec.image_id, "file": target.name}


# --------------------------------------------------------------------------
# Jobs (generate / ingest / reels)
# --------------------------------------------------------------------------
JOB_SCRIPTS = {
    "ingest": "manual_media_ingest.py",
    "reels": "reel_gen.py",
    "publish": "ship_queue.py",
    "prepare": "prepare_media.py",
}

def active_job_id() -> str | None:
    return state_store().active()


def start_command_job(kind: str, command: list[str], *, label: str,
                      queue_name: str = "",
                      display: str | None = None,
                      after: "callable | None" = None) -> str:
    """Run ``command`` as a subprocess, streaming its output into the job log.

    ``display`` is what the UI shows as the command line -- generation passes the
    whole prompt on argv, which is thousands of characters, so the real argv is
    never shown verbatim. ``after`` runs once the process exits and may append a
    final line and override the status (see the generate job's file check).
    """
    if after is not None:
        raise ValueError("Job follow-up must run in the durable guided_actions worker.")
    return state_store().start(kind, command, queue=queue_name, label=label,
                               command=display or label)


def start_job(kind: str, qpath: pathlib.Path,
              extra: list[str] | None = None) -> str:
    """Run one engine script against a queue file (the ingest / reels buttons)."""
    if kind not in JOB_SCRIPTS:
        raise ValueError(f"unknown job kind: {kind!r}")
    script = SCRIPTS_DIR / JOB_SCRIPTS[kind]
    if not script.is_file():
        raise ValueError(f"missing script: {script.name}")

    extra = list(extra or [])
    rel_queue = qpath.relative_to(REPO_ROOT).as_posix()
    return start_command_job(
        kind,
        [sys.executable, "-u", str(script), rel_queue, *extra],
        label=f"{kind} {qpath.name}",
        queue_name=qpath.name,
        display=" ".join([pathlib.Path(sys.executable).name, "-u",
                          f"scripts/{JOB_SCRIPTS[kind]}", rel_queue, *extra]),
    )


def job_payload(job_id: str, since: int = 0) -> dict:
    return state_store().payload(job_id, since)


def start_guided_job(kind: str, queue_name: str = "", payload: dict | None = None) -> str:
    payload = dict(payload or {}, queue=queue_name)
    command = [sys.executable, "-u", str(SCRIPTS_DIR / "guided_actions.py"),
               kind, json.dumps(payload)]
    return start_command_job(kind, command, queue_name=queue_name,
                             label=f"{kind} {queue_name}", display=f"{kind} {queue_name}")


# --------------------------------------------------------------------------
# Generating a batch (Copilot CLI, same command the workflows run)
# --------------------------------------------------------------------------
def lane_config(lane: str) -> dict:
    config = GENERATION_LANES.get(lane)
    if config is None:
        raise ValueError(f"unknown lane: {lane!r}")
    return config


def default_date(lane: str) -> str:
    """The date the lane's own prompt would pick if left alone."""
    config = lane_config(lane)
    today = dt.datetime.now(TZ).date()
    return (today + dt.timedelta(days=config["offset_days"])).isoformat()


def queue_name_for(lane: str, date: str) -> str:
    return f"{lane_config(lane)['prefix']}{date}.json"


def validate_date(date: str) -> str:
    if not DATE_RE.match(date or ""):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    try:
        dt.date.fromisoformat(date)
    except ValueError as e:
        raise ValueError(f"not a real date: {date!r}") from e
    return date


def date_override(lane: str, date: str) -> str:
    """The instruction appended to the prompt so it targets an explicit date.

    Appended at runtime rather than edited into the prompt files: those are the
    canonical, workflow-shared instructions and must not drift.
    """
    config = lane_config(lane)
    name = queue_name_for(lane, date)
    return (
        "\n\n"
        "================================================================\n"
        "DATE OVERRIDE — HIGHEST PRIORITY, overrides every earlier instruction\n"
        "about which day to generate for (\"tomorrow\", \"today\", the runner clock).\n"
        f"Generate this batch for {date}.\n"
        f"Write the posts to queue/{name} and, if you write a summary, to "
        f"queue/{config['prefix']}{date}.summary.md.\n"
        "Every schedule_time in the batch must fall on that date.\n"
        "Do not write any other queue file.\n"
        "================================================================\n"
    )


SHIM_SUFFIXES = {".bat", ".cmd", ".ps1"}


def npm_copilot_entry() -> pathlib.Path | None:
    """The npm-installed CLI's JS entry point, runnable directly with node."""
    roots: list[pathlib.Path] = []
    node = shutil.which("node")
    if node:
        roots.append(pathlib.Path(node).parent)
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(pathlib.Path(appdata) / "npm")
    roots += [pathlib.Path(p) for p in
              ("/usr/local/lib", "/usr/lib", "/opt/homebrew/lib")]

    for root in roots:
        package = root / "node_modules" / "@github" / "copilot"
        manifest = package / "package.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        bin_field = data.get("bin")
        relative = (bin_field.get("copilot")
                    if isinstance(bin_field, dict) else bin_field)
        if not relative:
            continue
        entry = package / str(relative)
        if entry.is_file():
            return entry
    return None


def copilot_command() -> list[str]:
    """argv prefix that reaches the Copilot CLI with no shell in between.

    This is deliberately fussy. The `copilot` first found on PATH is often a
    .bat/.ps1 bootstrapper -- VS Code ships one -- and those forward arguments
    through cmd.exe *and* PowerShell. A multi-line prompt does not survive that
    (measured: it arrives empty), so a shim is never acceptable here. Only a
    native executable, or the npm entry point run through node, will do.
    """
    override = os.environ.get(COPILOT_ENV)
    if override:
        path = pathlib.Path(override)
        if not path.is_file():
            raise ValueError(
                f"{COPILOT_ENV} points at {override!r}, which is not a file.")
        if path.suffix.lower() == ".js":
            node = shutil.which("node")
            if not node:
                raise ValueError(
                    f"{COPILOT_ENV} points at a .js entry but node is not on PATH.")
            return [node, str(path)]
        if path.suffix.lower() in SHIM_SUFFIXES:
            raise ValueError(
                f"{COPILOT_ENV} points at {path.name}, a shell wrapper. Those "
                "mangle the prompt -- use the real executable or the npm "
                "npm-loader.js instead.")
        return [str(path)]

    if os.name == "nt":
        native = shutil.which("copilot.exe")
        if native:
            return [native]
    else:
        found = shutil.which("copilot")
        if found and pathlib.Path(found).suffix.lower() not in SHIM_SUFFIXES:
            return [found]

    entry = npm_copilot_entry()
    node = shutil.which("node")
    if entry and node:
        return [node, str(entry)]

    hint = ""
    if shutil.which("copilot"):
        hint = (" The only 'copilot' on PATH is "
                f"{pathlib.Path(shutil.which('copilot')).name}, a shell wrapper "
                "that mangles long prompts, so it cannot be used.")
    raise ValueError(
        "could not find a usable Copilot CLI. Install it with "
        "`npm install -g @github/copilot` and sign in, then restart this "
        f"server. Set {COPILOT_ENV} to override." + hint)


def generation_command(lane: str, date: str, base: list[str],
                       prompt_text: str) -> list[str]:
    """The argv for one generation run, mirroring the workflow flags per lane.

    The prompt is a single argv element -- never a shell string. It is thousands
    of characters and full of quotes, backticks and newlines.
    """
    config = lane_config(lane)
    command = [
        *base,
        "-p", prompt_text + date_override(lane, date),
        "-s",
        f"--allow-tool={config['allow_tool']}",
    ]
    if config["allow_all_urls"]:
        command.append("--allow-all-urls")
    command.append("--no-ask-user")
    return command


def start_generation(lane: str, date: str) -> str:
    config = lane_config(lane)
    validate_date(date)
    copilot_command()

    prompt_path = REPO_ROOT / config["prompt"]
    if not prompt_path.is_file():
        raise ValueError(f"missing prompt file: {config['prompt']}")

    name = queue_name_for(lane, date)
    expected = QUEUE_DIR / name
    if expected.exists():
        raise ValueError(
            f"queue/{name} already exists. Delete it first if you want to "
            "regenerate that day.")

    return start_guided_job("generate", name, {"lane": lane, "date": date})


def lanes_payload() -> list[dict]:
    entries = []
    for lane, config in GENERATION_LANES.items():
        date = default_date(lane)
        name = queue_name_for(lane, date)
        entries.append({
            "lane": lane,
            "label": config["label"],
            "blurb": config["blurb"],
            "prompt": config["prompt"],
            "prompt_ok": (REPO_ROOT / config["prompt"]).is_file(),
            "default_date": date,
            "queue_name": name,
            "exists": (QUEUE_DIR / name).exists(),
        })
    return entries


# --------------------------------------------------------------------------
# Removing posts and queue files (moved to .trash/, never unlinked)
# --------------------------------------------------------------------------
def post_media_names(post: dict) -> set[str]:
    """Every file in assets/generated/ that belongs to this post.

    Resolved exactly -- from the image plan and the paths the queue actually
    records -- rather than by globbing the post id, which would over-match
    sibling ids (``x-1`` also matching ``x-10``).
    """
    names: set[str] = set()
    try:
        for spec in manual_media.plan_images(
                [post], openai_gen.IMAGE_QUALITY,
                openai_gen.DEFAULT_OVERLAY_POSITION):
            names.add(spec.output_path(OUT_DIR).name)
    except Exception:  # noqa: BLE001 - a malformed post must stay deletable
        pass

    visual = post.get("visual") or {}
    for key in ("file", "cover"):
        value = visual.get(key)
        if value:
            names.add(pathlib.Path(str(value)).name)
    for value in visual.get("files") or []:
        if value:
            names.add(pathlib.Path(str(value)).name)

    # Reel artefacts follow a fixed convention and may exist before the queue
    # has been updated with their paths.
    post_id = str(post.get("id") or "")
    if post_id and post.get("format") == "reel":
        names.add(f"{post_id}.mp4")
        names.add(f"{post_id}-cover.png")

    return {name for name in names if name}


def media_to_trash(removed: list[dict],
                   remaining: list[dict]) -> list[pathlib.Path]:
    """Existing media belonging to ``removed`` and to nothing that survives.

    The subtraction matters: a cross-posted reel (``visual.source: "reuse"``)
    can share files with its master, and deleting the copy must never take the
    master's mp4 with it.
    """
    keep: set[str] = set()
    for post in remaining:
        keep |= post_media_names(post)

    wanted: set[str] = set()
    for post in removed:
        wanted |= post_media_names(post)

    files = []
    for name in sorted(wanted - keep):
        if not SAFE_NAME_RE.match(name):
            continue
        path = OUT_DIR / name
        if _is_inside(path, OUT_DIR) and path.is_file():
            files.append(path)
    return files


def new_trash_dir(label: str) -> pathlib.Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.") or "items"
    base = TRASH_DIR / f"{stamp}-{slug[:80]}"
    path = base
    for n in range(2, 100):
        if not path.exists():
            break
        path = pathlib.Path(f"{base}-{n}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _move_into(dest: pathlib.Path, path: pathlib.Path) -> str:
    target = unique_destination(dest, path.name)
    shutil.move(str(path), str(target))
    return target.name


def delete_post(qpath: pathlib.Path, post_id: str) -> dict:
    """Drop one post from a queue file, moving it and its media to .trash/."""
    post_id = (post_id or "").strip()
    if not post_id:
        raise ValueError("post_id is required")

    posts = load_posts(qpath)
    removed = [p for p in posts if str(p.get("id")) == post_id]
    if not removed:
        raise ValueError(f"{post_id!r} is not a post in {qpath.name}")
    remaining = [p for p in posts if str(p.get("id")) != post_id]
    dependents = [p.get("id") for p in remaining + other_queue_posts(qpath)
                  if (p.get("visual") or {}).get("of") == post_id]
    if dependents:
        raise ValueError("This post supplies media to " + ", ".join(map(str, dependents)) +
                         ". Remove or change those cross-posts first.")

    files = media_to_trash(removed, remaining + other_queue_posts(qpath))
    dest = new_trash_dir(f"{qpath.stem}-{post_id}")
    moved = [_move_into(dest, path) for path in files]

    (dest / "post.json").write_text(
        json.dumps(removed, indent=2), encoding="utf-8")
    qpath.write_text(json.dumps(remaining, indent=2), encoding="utf-8")

    manifest = {
        "kind": "post",
        "queue": qpath.name,
        "post_id": post_id,
        "media": moved,
        "removed_at": dt.datetime.now().isoformat(timespec="seconds"),
        "restore": "copy post.json back into the queue array and the media "
                   "files back into assets/generated/",
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "post_id": post_id,
        "media": moved,
        "remaining": len(remaining),
        "trash": dest.relative_to(REPO_ROOT).as_posix(),
        "summary_stale": (qpath.parent / f"{qpath.stem}.summary.md").is_file(),
    }


def delete_queue(qpath: pathlib.Path) -> dict:
    """Move a whole queue file, its siblings and all its media to .trash/."""
    try:
        posts = load_posts(qpath)
    except Exception:  # noqa: BLE001 - a broken queue must still be removable
        posts = []

    files = media_to_trash(posts, other_queue_posts(qpath)) if posts else []
    dest = new_trash_dir(qpath.stem)
    moved = [_move_into(dest, path) for path in files]

    siblings = []
    for suffix in (".summary.md", ".prompts.md"):
        sibling = qpath.parent / f"{qpath.stem}{suffix}"
        if sibling.is_file() and _is_inside(sibling, QUEUE_DIR):
            siblings.append(_move_into(dest, sibling))
    queue_copy = _move_into(dest, qpath)

    manifest = {
        "kind": "queue",
        "queue": qpath.name,
        "stored_as": queue_copy,
        "posts": len(posts),
        "siblings": siblings,
        "media": moved,
        "removed_at": dt.datetime.now().isoformat(timespec="seconds"),
        "restore": "move the .json (and any siblings) back into queue/ and the "
                   "media files back into assets/generated/",
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "queue": qpath.name,
        "posts": len(posts),
        "media": moved,
        "siblings": siblings,
        "trash": dest.relative_to(REPO_ROOT).as_posix(),
    }


def other_queue_posts(qpath: pathlib.Path) -> list[dict]:
    posts = []
    for candidate in QUEUE_DIR.glob("*.json"):
        if candidate != qpath:
            # A corrupt neighboring batch must not make deletion guess at ownership.
            posts.extend(load_posts(candidate))
    return posts


def trash_entries() -> list[dict]:
    if not TRASH_DIR.is_dir():
        return []
    entries = []
    for path in sorted(TRASH_DIR.glob("*/manifest.json"), reverse=True):
        if not path.with_name("restored").exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            entries.append({"entry": path.parent.name, **data})
    return entries


def restore_trash(entry: str) -> dict:
    if not SAFE_NAME_RE.fullmatch(entry or ""):
        raise ValueError("Invalid restore entry.")
    folder = TRASH_DIR / entry
    if not _is_inside(folder, TRASH_DIR):
        raise ValueError("Invalid restore path.")
    manifest = folder / "manifest.json"
    if not manifest.is_file() or (folder / "restored").exists():
        raise ValueError("That entry is not available to restore.")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    queue_name = data["queue"]
    if not QUEUE_NAME_RE.fullmatch(queue_name):
        raise ValueError("Invalid queue in restore manifest.")
    qpath = QUEUE_DIR / queue_name
    copies = []
    for name in data.get("media", []):
        if not SAFE_NAME_RE.fullmatch(name):
            raise ValueError("Invalid media in restore manifest.")
        copies.append((folder / name, OUT_DIR / name))
    if data["kind"] == "queue":
        copies.append((folder / data["stored_as"], qpath))
        for name in data.get("siblings", []):
            if not SAFE_NAME_RE.fullmatch(name):
                raise ValueError("Invalid sibling in restore manifest.")
            copies.append((folder / name, QUEUE_DIR / name))
    else:
        posts = load_posts(safe_queue_path(queue_name))
        removed = json.loads((folder / "post.json").read_text(encoding="utf-8"))
        if any(p.get("id") in {x.get("id") for x in posts} for p in removed):
            raise ValueError("The post already exists; restore would overwrite it.")
    for source, destination in copies:
        if not _is_inside(source, folder) or not source.is_file():
            raise ValueError("Restore source is missing or invalid.")
        if destination.exists():
            raise ValueError(f"Restore would overwrite {destination.name}; nothing restored.")
    restored = []
    try:
        for source, destination in copies:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            restored.append(destination)
    except OSError:
        for destination in restored:
            destination.unlink(missing_ok=True)
        raise
    if data["kind"] == "post":
        guided_workflow.atomic_json(qpath, posts + removed)
    (folder / "restored").touch()
    return {"queue": queue_name, "restored": entry}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "Layer8AdhocUI/1.0"
    quiet = True

    def _trusted_request(self, *, mutation: bool = False) -> None:
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            raise PermissionError("This app accepts localhost requests only.")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}" for host in hosts}:
            raise PermissionError("Cross-origin requests are not allowed.")
        if mutation and not secrets.compare_digest(
                self.headers.get("X-Layer8-CSRF", ""), CSRF_TOKEN):
            raise PermissionError("Refresh the app before making changes (session token missing or expired).")

    # -- helpers ----------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib hook
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _text(self, text: str, status: int = 200) -> None:
        self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _read_body(self) -> bytes:
        if getattr(self, "_request_body", None) is not None:
            return self._request_body
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("invalid Content-Length")
        if length < 0:
            raise ValueError("invalid Content-Length")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError(f"upload too large ({length} bytes)")
        return self.rfile.read(length) if length else b""

    def _serve_file(self, path: pathlib.Path) -> None:
        if not path.is_file():
            self._error(404, f"not found: {path.name}")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), content_type)

    # -- routing ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        query = parse_qs(parsed.query)
        try:
            self._trusted_request()
            self._route_get(parts, query)
        except PermissionError as e:
            self._error(403, str(e))
        except ValueError as e:
            self._error(400, str(e))
        except Exception as e:  # noqa: BLE001 - never kill the dev server
            self._error(500, f"{type(e).__name__}: {e}")

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib hook
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        self._request_body = None
        self.connection.settimeout(30)
        try:
            self._trusted_request(mutation=True)
            # Consume authenticated uploads before rejecting a stale revision.
            # Closing a socket with unread input can reset it before the client sees 409.
            self._request_body = self._read_body()
            with MUTATION_LOCK:
                queue = None
                if len(parts) == 4 and parts[:2] == ["api", "queue"]:
                    queue = safe_queue_path(parts[2])
                    expected = self.headers.get("If-Match", "").strip('"')
                    if expected != readiness(queue)["revision"]:
                        raise RuntimeError("This batch changed. Refresh it before making another change.")
                action = parts[-1] if parts else ""
                starts_job = action in {"generate", "prepare", "ingest", "reels",
                                        "stage-approval", "approve", "reschedule", "refresh-delivery"}
                if starts_job or (len(parts) == 4 and parts[1] == "jobs"):
                    result = self._route_post(parts)
                else:
                    with state_store().mutation() as db:
                        result = self._route_post(parts)
                        if queue and action not in {"publish-check"}:
                            state_store().invalidate_approval(queue.name, db)
                        elif action == "restore" and result.get("queue"):
                            state_store().invalidate_approval(result["queue"], db)
            name = result.pop("_refresh_queue", None)
            if name:
                result.update(queue_payload(safe_queue_path(name)))
            self._json(result)
        except PermissionError as e:
            length = self.headers.get("Content-Length", "")
            if self._request_body is None and length.isdecimal() and int(length) <= 65536:
                self.connection.settimeout(1)
                try:
                    self.rfile.read(int(length))
                except (TimeoutError, ConnectionError) as disconnected:
                    self.log_message("Rejected request body was incomplete: %s", disconnected)
            self._error(403, str(e))
        except LookupError as e:
            self._error(404, str(e))
        except ValueError as e:
            self._error(400, str(e))
        except RuntimeError as e:
            self._error(409, str(e))
        except Exception as e:  # noqa: BLE001
            self._error(500, f"{type(e).__name__}: {e}")

    def _route_get(self, parts: list[str], query: dict) -> None:
        if not parts:
            self._serve_file(WEBAPP_DIR / "index.html")
            return
        if parts[0] == "static" and len(parts) == 2:
            if not SAFE_NAME_RE.match(parts[1]):
                self._error(400, "bad asset name")
                return
            asset = WEBAPP_DIR / parts[1]
            if not _is_inside(asset, WEBAPP_DIR):
                self._error(400, "bad asset path")
                return
            self._serve_file(asset)
            return
        if parts[0] == "fonts" and len(parts) == 2:
            source = BRAND_FONTS.get(parts[1])
            if source is None:
                self._error(404, "unknown font")
                return
            self._serve_file(FONTS_DIR / source)
            return
        if parts[0] == "favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if parts[0] != "api":
            self._error(404, "not found")
            return

        if parts == ["api", "session"]:
            self._json({"csrf": CSRF_TOKEN, "diagnostics": diagnostics(),
                        "active_job": active_job_id(), "data_root": str(REPO_ROOT)})
            return
        if parts == ["api", "trash"]:
            self._json({"entries": trash_entries()})
            return
        if len(parts) == 5 and parts[:3] == ["api", "media", "staged"]:
            safe_queue_path(parts[3])
            name = unquote(parts[4])
            directory = unassigned_dir(queue_name=parts[3])
            path = directory / name
            if not SAFE_NAME_RE.fullmatch(name) or not _is_inside(path, directory):
                raise ValueError("Invalid staged image path.")
            self._serve_file(path)
            return
        if len(parts) == 5 and parts[:3] == ["api", "media", "source"]:
            qpath = safe_queue_path(parts[3])
            image_id = unquote(parts[4])
            spec = next((s for s in plan_for(load_posts(qpath)) if s.image_id == image_id), None)
            if spec is None:
                raise ValueError("Unknown image in this batch.")
            source = guided_workflow.original(spec, REPO_ROOT)
            if source is None:
                raise ValueError("No original source is available.")
            self._serve_file(source)
            return

        # /api/media/<root>/<name>
        if len(parts) == 4 and parts[1] == "media":
            self._serve_file(safe_media_path(parts[2], parts[3]))
            return
        # /api/lanes
        if len(parts) == 2 and parts[1] == "lanes":
            try:
                found = copilot_command()
                copilot = {"ok": True, "command": pathlib.Path(found[-1]).name}
            except ValueError as e:
                copilot = {"ok": False, "error": str(e)}
            self._json({"lanes": lanes_payload(),
                        "copilot": copilot,
                        "active_job": active_job_id()})
            return
        # /api/queues
        if len(parts) == 2 and parts[1] == "queues":
            entries = []
            if QUEUE_DIR.is_dir():
                def batch_order(path):
                    date = re.search(r"\d{4}-\d{2}-\d{2}", path.stem)
                    return (date.group(0) if date else "", path.name)
                for qpath in sorted(QUEUE_DIR.glob("*.json"), key=batch_order, reverse=True):
                    entries.append(queue_summary(qpath))
            self._json({"queues": entries})
            return
        # /api/jobs/<id>
        if len(parts) == 3 and parts[1] == "jobs":
            since = int((query.get("since") or ["0"])[0])
            self._json(job_payload(parts[2], since))
            return
        # /api/queue/<name>[/batch-prompt]
        if len(parts) >= 3 and parts[1] == "queue":
            qpath = safe_queue_path(parts[2])
            if len(parts) == 3:
                self._json(queue_payload(qpath))
                return
            posts = load_posts(qpath)
            specs = plan_for(posts)
            if len(parts) == 4 and parts[3] == "download":
                report = readiness(qpath, require_future=False)
                if not report["media_ready"]:
                    raise ValueError("Finish preparing the media before downloading the bundle.")
                bundle = guided_workflow.download_bundle(posts, report["manifest"], REPO_ROOT)
                self._send(200, bundle, "application/zip", {
                    "Content-Disposition": f'attachment; filename="{qpath.stem}-ready.zip"'})
                return
            if len(parts) == 4 and parts[3] == "batch-prompt":
                only = None
                if (query.get("pending") or ["1"])[0] == "1":
                    only = [s.image_id for s in specs
                            if spec_status(s)[0] == "pending"]
                self._text(batch_prompt(specs, only))
                return
        self._error(404, "not found")

    def _route_post(self, parts: list[str]) -> dict:
        # /api/generate  {lane, date}
        if parts == ["api", "generate"]:
            payload = json.loads(self._read_body() or b"{}")
            lane = str(payload.get("lane") or "")
            lane_config(lane)  # reject an unknown lane before touching the date
            date = str(payload.get("date") or "") or default_date(lane)
            job_id = start_generation(lane, date)
            return {"job": job_payload(job_id)}

        if parts == ["api", "restore"]:
            payload = json.loads(self._read_body() or b"{}")
            return restore_trash(payload.get("entry", ""))
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            state_store().cancel(parts[2])
            return {"job": job_payload(parts[2])}

        if len(parts) != 4 or parts[0] != "api" or parts[1] != "queue":
            raise LookupError("Unknown action.")
        qpath = safe_queue_path(parts[2])
        action = parts[3]

        # Removal must not require the queue to be parseable or planned.
        if action == "delete":
            return {"deleted": delete_queue(qpath)}

        if action == "delete-post":
            payload = json.loads(self._read_body() or b"{}")
            result = delete_post(qpath, payload.get("post_id", ""))
            return {"deleted": result, "_refresh_queue": qpath.name}

        posts = load_posts(qpath)
        specs = plan_for(posts)
        directory = unassigned_dir(queue_name=qpath.name)

        if action == "upload":
            data = self._read_body()
            if not data:
                raise ValueError("empty upload")
            filename = unquote(self.headers.get("X-Filename", "images.zip"))
            directory.mkdir(parents=True, exist_ok=True)
            if filename.lower().endswith(".zip"):
                stored, skipped = extract_zip(data, directory)
            else:
                safe = sanitize_entry_name(filename)
                if not safe:
                    raise ValueError("Choose PNG, JPEG, WebP images or a ZIP.")
                with guided_workflow.decode_image(data) as image:
                    target = unique_destination(directory, pathlib.Path(safe).stem + ".png")
                    image.save(target, "PNG")
                stored, skipped = [target.name], []
            moved = auto_reconcile(specs, directory)
            return {
                "stored": stored,
                "skipped": skipped,
                "matched": moved,
                "_refresh_queue": qpath.name,
            }

        if action == "assign":
            payload = json.loads(self._read_body() or b"{}")
            result = assign_staged(specs, payload.get("file", ""),
                                   payload.get("image_id", ""), directory)
            return {"assigned": result, "_refresh_queue": qpath.name}

        if action in {"replace", "undo-image", "edit-image"}:
            if action == "replace":
                payload = {"image_id": self.headers.get("X-Image-Id", "")}
            else:
                payload = json.loads(self._read_body() or b"{}")
            spec = next((s for s in specs if s.image_id == payload.get("image_id")), None)
            if spec is None:
                raise ValueError("Choose an image from this batch.")
            if action == "replace":
                guided_workflow.replace_image(REPO_ROOT, qpath, spec, self._read_body())
            elif action == "undo-image":
                guided_workflow.undo_image(REPO_ROOT, qpath, posts, spec)
            else:
                guided_workflow.edit_image(REPO_ROOT, qpath, posts, spec,
                                           payload.get("headline"), payload.get("subtext", ""))
            return {"_refresh_queue": qpath.name}

        if action == "edit-post":
            payload = json.loads(self._read_body() or b"{}")
            guided_workflow.edit_post(qpath, posts, payload.get("post_id"), payload.get("changes"))
            return {"_refresh_queue": qpath.name}
        if action in {"prepare", "ingest", "reels", "stage-approval", "approve", "reschedule", "refresh-delivery"}:
            payload = json.loads(self._read_body() or b"{}")
            payload["revision"] = self.headers.get("If-Match", "").strip('"')
            kind = "prepare" if action in {"ingest", "reels"} else action
            job_id = start_guided_job(kind, qpath.name, payload)
            return {"job": job_payload(job_id)}
        if action == "publish-check":
            return {"readiness": readiness(qpath)}
        if action == "publish":
            raise ValueError("Direct publishing is disabled. Prepare an approval PR, then approve its reviewed revision.")
        raise LookupError("Unknown action.")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def _display_path(path: pathlib.Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    A relative_to() that assumes every path sits under REPO_ROOT raises
    ValueError and masks the error it was meant to report.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def verify_layout() -> None:
    """Fail fast when the server isn't sitting in a content-engine checkout."""
    required = [
        SCRIPTS_DIR / "manual_media.py",
        SCRIPTS_DIR / "manual_media_ingest.py",
        SCRIPTS_DIR / "reel_gen.py",
        SCRIPTS_DIR / "ship_queue.py",
        WEBAPP_DIR / "index.html",
        *(FONTS_DIR / name for name in sorted(BRAND_FONTS.values())),
    ]
    missing = [p for p in required if not p.is_file()]
    if missing or not QUEUE_DIR.is_dir():
        names = [_display_path(p) for p in missing]
        if not QUEUE_DIR.is_dir():
            names.append("queue/")
        raise SystemExit(
            "adhoc_server.py must run from a layer8-content-engine checkout; "
            f"missing: {', '.join(names)}"
        )


def configure_root(root: pathlib.Path) -> None:
    global REPO_ROOT, QUEUE_DIR, INBOX_DIR, OUT_DIR, TRASH_DIR, FONTS_DIR
    REPO_ROOT = root.resolve()
    QUEUE_DIR = REPO_ROOT / "queue"
    INBOX_DIR = REPO_ROOT / manual_media.DEFAULT_INBOX
    OUT_DIR = REPO_ROOT / manual_media.DEFAULT_OUT_DIR
    TRASH_DIR = REPO_ROOT / ".trash"
    FONTS_DIR = REPO_ROOT / "assets" / "fonts"
    os.environ["LAYER8_DATA_ROOT"] = str(REPO_ROOT)


def main(port: int = 8765, open_browser: bool = True, verbose: bool = False,
         data_root: pathlib.Path | None = None) -> None:
    if data_root is not None:
        configure_root(data_root)
    verify_layout()
    Handler.quiet = not verbose
    mimetypes.add_type("image/webp", ".webp")
    mimetypes.add_type("video/mp4", ".mp4")
    mimetypes.add_type("font/ttf", ".ttf")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Layer8Culture Content Engine  ->  {url}")
    print(f"  repo:  {REPO_ROOT}")
    print(f"  inbox: {INBOX_DIR.relative_to(REPO_ROOT).as_posix()}/")
    print("Local only, no authentication. Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Local web UI for the ad-hoc (no-API) image run.")
    parser.add_argument("--port", type=int, default=8765,
                        help="port to listen on (default: 8765)")
    parser.add_argument("--no-browser", action="store_true",
                        help="don't open a browser window on start")
    parser.add_argument("--verbose", action="store_true",
                        help="log every HTTP request")
    parser.add_argument("--data-root", type=pathlib.Path,
                        help="Use queue/assets from this checkout while serving this version of the app.")
    args = parser.parse_args()
    main(args.port, not args.no_browser, args.verbose, args.data_root)
