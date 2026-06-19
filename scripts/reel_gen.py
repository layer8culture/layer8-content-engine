#!/usr/bin/env python3
"""Generate Instagram Reels from queued Layer8Culture posts using ffmpeg.

Reads a queue JSON file, finds posts with format == "reel", renders
assets/generated/<post_id>.mp4 plus a cover frame, then writes the paths back to
the post's visual dict. Designed to run after openai_gen.py has already rendered
assets/generated/<post_id>.png for motion reels.

Cross-posts (e.g. TikTok reusing the day's Instagram reel) set visual.source ==
"reuse" and visual.of == "<source-post-id>"; instead of rendering, a second pass
copies that source reel's mp4 + cover to this post's id (zero extra render cost).

Requires: ffmpeg available on PATH. Uses Python stdlib + Pillow, and (for the
Sora-2 reel backend) requests. Reels render via Azure Sora-2 when the
AZURE_OPENAI_* env is configured, falling back to the ffmpeg "motion" renderer.
"""
import io
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable

import requests
from PIL import Image


WIDTH = 1080
HEIGHT = 1920
FPS = 30
MAX_REEL_SEC = 60.0
MAX_CLIP_SEC = 45.0

OUT_DIR = pathlib.Path("assets/generated")
FONT_PATH = pathlib.Path("assets/fonts/SpaceGrotesk-Variable.ttf")
TRANSCRIPTS_DIR = pathlib.Path("transcripts")
LOFI_BEDS = [
    pathlib.Path("assets/library/lofi-bed.mp3"),
    pathlib.Path("assets/library/lofi-bed.m4a"),
    pathlib.Path("assets/library/lofi-bed.wav"),
]

SOFT_WHITE = "0xF5F5F5"

# --- Azure Sora-2 video backend --------------------------------------------
# Reels render via Azure OpenAI Sora-2 when configured. It reuses the same
# resource (endpoint + key) as the gpt-image backend; only the deployment name
# differs. The REST surface is the OpenAI-style async "videos" API:
#   POST {endpoint}/openai/v1/videos?api-version=preview   (create job)
#   GET  {endpoint}/openai/v1/videos/{id}?api-version=preview        (poll)
#   GET  {endpoint}/openai/v1/videos/{id}/content?api-version=preview (download)
# The endpoint must be the *.openai.azure.com host. Sora keeps its own audio and
# the output is left clean (no composited branding). When Sora isn't configured
# or a job fails/times out, reel_gen falls back to the ffmpeg "motion" renderer.
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
SORA_DEPLOYMENT = os.environ.get("AZURE_OPENAI_VIDEO_DEPLOYMENT", "sora-2").strip()
SORA_API_VERSION = os.environ.get("AZURE_OPENAI_VIDEO_API_VERSION", "preview").strip()
# Brand aspect -> a Sora-supported canvas size (square is coerced to vertical).
SORA_SIZE = {"9:16": "720x1280", "16:9": "1280x720", "1:1": "720x1280"}
SORA_DEFAULT_SIZE = "720x1280"
SORA_ALLOWED_SECONDS = (4, 8, 12)
SORA_DEFAULT_SECONDS = 8
SORA_POLL_INTERVAL = 10.0
SORA_TIMEOUT_SEC = 600.0


def sora_configured() -> bool:
    return bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)


@dataclass
class Cue:
    start: float
    end: float
    text: str


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_seconds(value: float) -> str:
    value = max(0.0, value)
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = value % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def parse_timecode(value: str) -> float:
    """Parse WebVTT/ffmpeg-style timecodes into seconds."""
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"invalid timecode: {value!r}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_vtt(path: pathlib.Path) -> list[Cue]:
    """Parse a small, dependency-free subset of WebVTT cues."""
    cues: list[Cue] = []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    i = 0
    timing_re = re.compile(
        r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})"
        r"\s+-->\s+"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})"
    )
    while i < len(lines):
        line = lines[i].strip()
        match = timing_re.search(line)
        if not match:
            i += 1
            continue

        start = parse_timecode(match.group("start"))
        end = parse_timecode(match.group("end"))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = " ".join(text_lines)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text and end > start:
            cues.append(Cue(start=start, end=end, text=text))
        i += 1
    return cues


def newest_vtt(transcripts_dir: pathlib.Path = TRANSCRIPTS_DIR) -> pathlib.Path | None:
    files = list(transcripts_dir.glob("*.vtt"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", text.lower())
        if len(token) > 2
    }


def best_cue_for_query(cues: Iterable[Cue], query: str) -> tuple[Cue | None, float]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return None, 0.0
    best: Cue | None = None
    best_score = 0.0
    query_lower = " ".join(query.lower().split())
    for cue in cues:
        cue_lower = " ".join(cue.text.lower().split())
        cue_tokens = tokenize(cue.text)
        overlap = len(query_tokens & cue_tokens)
        score = overlap / max(1, len(query_tokens))
        if query_lower and query_lower in cue_lower:
            score += 1.0
        if score > best_score:
            best = cue
            best_score = score
    return best, best_score


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def run_ffmpeg(args: list[str], post_id: str, label: str) -> bool:
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip().splitlines()
        tail = " | ".join(stderr[-4:]) if stderr else str(e)
        print(f"  x {post_id}: ffmpeg {label} failed ({tail})")
        return False


def ffprobe_has_audio(path: pathlib.Path) -> bool | None:
    probe = ffprobe_path()
    if not probe:
        return None
    try:
        result = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return None


def escape_filter_path(path: pathlib.Path) -> str:
    """Use ffmpeg-friendly relative paths inside filter arguments."""
    return path.as_posix().replace("\\", "/").replace(":", r"\:")


def escape_drawtext(text: str) -> str:
    text = text.replace("\\", r"\\")
    text = text.replace("'", r"\'")
    text = text.replace(":", r"\:")
    text = text.replace("%", r"\%")
    text = text.replace("\n", r"\n")
    return text


def wrap_text(text: str, max_chars: int = 21, max_lines: int = 3) -> str:
    words = text.upper().split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if not current or len(trial) <= max_chars:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    remaining = words[sum(len(line.split()) for line in lines):]
    if remaining and lines:
        lines[-1] = f"{lines[-1]}…"
    return "\n".join(lines)


def drawtext_filter(
    text: str,
    y_expr: str,
    fontsize: int,
    start: float | None = None,
    end: float | None = None,
) -> str:
    alpha = "1"
    enable = ""
    if start is not None and end is not None:
        fade = min(0.45, max(0.15, (end - start) / 5))
        alpha = (
            f"if(lt(t,{start:.3f}),0,"
            f"if(lt(t,{start + fade:.3f}),(t-{start:.3f})/{fade:.3f},"
            f"if(lt(t,{end - fade:.3f}),1,"
            f"if(lt(t,{end:.3f}),({end:.3f}-t)/{fade:.3f},0))))"
        )
        enable = f":enable='between(t,{start:.3f},{end:.3f})'"

    return (
        "drawtext="
        f"fontfile='{escape_filter_path(FONT_PATH)}'"
        f":text='{escape_drawtext(text)}'"
        f":fontcolor={SOFT_WHITE}"
        f":fontsize={fontsize}"
        ":line_spacing=10"
        ":x=(w-text_w)/2"
        f":y={y_expr}"
        ":box=1:boxcolor=black@0.48:boxborderw=28"
        ":shadowcolor=black@0.90:shadowx=3:shadowy=3"
        f":alpha='{alpha}'"
        f"{enable}"
    )


def motion_video_filter(duration: float, beats: list[str]) -> str:
    frames = max(1, int(math.ceil(duration * FPS)))
    zoom = (
        "zoompan="
        f"z='1+0.045*on/{max(1, frames - 1)}'"
        ":x='iw/2-(iw/zoom/2)+12*sin(on/90)'"
        ":y='ih/2-(ih/zoom/2)-10*sin(on/110)'"
        f":d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS}"
    )
    filters = [
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={WIDTH}:{HEIGHT}",
        "setsar=1",
        zoom,
        f"trim=duration={duration:.3f}",
        "setpts=PTS-STARTPTS",
    ]

    if beats:
        slot = duration / len(beats)
        for index, beat in enumerate(beats):
            start = max(0.0, index * slot + 0.15)
            end = min(duration - 0.05, (index + 1) * slot + 0.25)
            filters.append(
                drawtext_filter(
                    wrap_text(str(beat)),
                    "h*0.19",
                    62,
                    start=start,
                    end=end,
                )
            )
    return ",".join(filters)


def audio_filter(duration: float) -> str:
    fade_out_start = max(0.0, duration - 0.75)
    return (
        f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
        "afade=t=in:st=0:d=0.35,"
        f"afade=t=out:st={fade_out_start:.3f}:d=0.75"
    )


def export_cover(video_path: pathlib.Path, cover_path: pathlib.Path, post_id: str,
                 timestamp: float = 0.1) -> bool:
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, timestamp):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}",
        str(cover_path),
    ]
    return run_ffmpeg(args, post_id, "cover export")


def find_lofi_bed() -> pathlib.Path | None:
    for path in LOFI_BEDS:
        if path.exists():
            return path
    return None


def generate_motion(post: dict, out_dir: pathlib.Path) -> tuple[str, str] | None:
    post_id = str(post.get("id", "")).strip()
    visual = post.setdefault("visual", {})
    reel = visual.get("reel") or {}
    still_path = out_dir / f"{post_id}.png"
    if not still_path.exists():
        print(f"  ! {post_id}: still image {still_path} not found; reel skipped")
        return None

    duration = clamp(float(reel.get("duration_sec", 8) or 8), 5.0, 15.0)
    beats = [str(beat) for beat in (reel.get("beats") or []) if str(beat).strip()]
    out_path = out_dir / f"{post_id}.mp4"
    cover_path = out_dir / f"{post_id}-cover.png"
    audio_choice = str(reel.get("audio", "lofi")).lower()
    lofi_bed = find_lofi_bed() if audio_choice == "lofi" else None

    args = ["ffmpeg", "-y", "-i", str(still_path)]
    if lofi_bed:
        args.extend(["-stream_loop", "-1", "-i", str(lofi_bed)])
        audio_input = "1:a"
        print(f"  > {post_id}: using lofi bed {lofi_bed}")
    else:
        if audio_choice == "lofi":
            print(f"  ! {post_id}: lofi bed missing; using silent AAC track")
        args.extend([
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
        ])
        audio_input = "1:a"

    filter_complex = (
        f"[0:v]{motion_video_filter(duration, beats)}[v];"
        f"[{audio_input}]{audio_filter(duration)}[a]"
    )
    args.extend([
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{duration:.3f}",
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out_path),
    ])
    if not run_ffmpeg(args, post_id, "motion render"):
        return None
    if not export_cover(out_path, cover_path, post_id):
        return None
    print(f"  + {post_id} -> {out_path}")
    return str(out_path), str(cover_path)


def clip_times_from_query(query: str) -> tuple[float, float] | None:
    vtt = newest_vtt()
    if not vtt:
        return None
    cues = parse_vtt(vtt)
    cue, score = best_cue_for_query(cues, query)
    if not cue or score <= 0:
        return None
    start = max(0.0, cue.start - 1.0)
    end = min(cue.end + 1.0, start + MAX_CLIP_SEC)
    return start, end


def clip_times(clip: dict, reel: dict) -> tuple[float, float] | None:
    start_value = clip.get("start")
    end_value = clip.get("end")
    if start_value and end_value:
        start = parse_timecode(str(start_value))
        end = parse_timecode(str(end_value))
    elif start_value:
        start = parse_timecode(str(start_value))
        end = start + clamp(float(reel.get("duration_sec", 30) or 30), 1.0, MAX_CLIP_SEC)
    elif clip.get("query"):
        times = clip_times_from_query(str(clip["query"]))
        if not times:
            return None
        start, end = times
    else:
        return None

    if end <= start:
        return None
    end = min(end, start + MAX_CLIP_SEC, start + MAX_REEL_SEC)
    return start, end


def clip_video_filter(headline: str | None) -> str:
    filters = [
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={WIDTH}:{HEIGHT}",
        "setsar=1",
    ]
    if headline:
        filters.append(
            drawtext_filter(
                wrap_text(headline, max_chars=18, max_lines=3),
                "h-(text_h+220)",
                64,
            )
        )
    return ",".join(filters)


def generate_clip(post: dict, out_dir: pathlib.Path) -> tuple[str, str] | None:
    post_id = str(post.get("id", "")).strip()
    visual = post.setdefault("visual", {})
    reel = visual.get("reel") or {}
    clip = reel.get("clip") or {}
    source_value = clip.get("source_file")
    if not source_value:
        print(f"  ! {post_id}: clip.source_file missing; reel skipped")
        return None

    source_path = pathlib.Path(str(source_value))
    if not source_path.exists() or not source_path.is_file():
        print(f"  ! {post_id}: source video {source_path} missing; reel skipped")
        return None

    try:
        times = clip_times(clip, reel)
    except ValueError as e:
        print(f"  ! {post_id}: invalid clip timecode ({e}); reel skipped")
        return None
    if not times:
        print(f"  ! {post_id}: could not resolve clip timecodes; reel skipped")
        return None

    start, end = times
    duration = min(end - start, MAX_CLIP_SEC, MAX_REEL_SEC)
    end = start + duration
    out_path = out_dir / f"{post_id}.mp4"
    cover_path = out_dir / f"{post_id}-cover.png"
    has_audio = ffprobe_has_audio(source_path)

    args = [
        "ffmpeg",
        "-y",
        "-ss",
        format_seconds(start),
        "-to",
        format_seconds(end),
        "-i",
        str(source_path),
    ]
    if has_audio is False:
        args.extend([
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
        ])
        audio_chain = "[1:a]atrim=0:{:.3f},asetpts=PTS-STARTPTS[a]".format(duration)
    else:
        if has_audio is None:
            print(f"  ! {post_id}: could not probe audio; assuming source has audio")
        audio_chain = "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"

    filter_complex = (
        f"[0:v]{clip_video_filter(visual.get('headline'))}[v];"
        f"{audio_chain}"
    )
    args.extend([
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{duration:.3f}",
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(out_path),
    ])
    if not run_ffmpeg(args, post_id, "clip render"):
        return None
    if not export_cover(out_path, cover_path, post_id, timestamp=duration / 2):
        return None
    print(f"  + {post_id} -> {out_path}")
    return str(out_path), str(cover_path)


def _sora_seconds(reel: dict) -> int:
    """Resolve and snap the requested clip length to a Sora-supported value."""
    raw = reel.get("seconds", reel.get("duration_sec"))
    try:
        val = int(round(float(raw)))
    except (TypeError, ValueError):
        return SORA_DEFAULT_SECONDS
    return min(SORA_ALLOWED_SECONDS, key=lambda s: abs(s - val))


def _sora_size(visual: dict, reel: dict) -> str:
    aspect = reel.get("aspect") or visual.get("aspect") or "9:16"
    return SORA_SIZE.get(aspect, SORA_DEFAULT_SIZE)


def _sora_headers(json_body: bool = False) -> dict:
    headers = {"api-key": AZURE_OPENAI_API_KEY}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _sora_create_job(prompt: str, size: str, seconds: int,
                     still_path: pathlib.Path | None) -> requests.Response:
    """Create a Sora-2 video job. With ``still_path`` set, animate that still as the
    first frame via image+text (``input_reference``); otherwise text-to-video.
    """
    url = f"{AZURE_OPENAI_ENDPOINT}/openai/v1/videos?api-version={SORA_API_VERSION}"
    data = {
        "model": SORA_DEPLOYMENT,
        "prompt": prompt,
        "size": size,
        "seconds": str(seconds),
    }
    if still_path is not None:
        width, height = (int(part) for part in size.split("x"))
        with Image.open(still_path) as im:
            # input_reference must match the requested size exactly.
            resized = im.convert("RGB").resize((width, height))
            buf = io.BytesIO()
            resized.save(buf, format="PNG")
        buf.seek(0)
        files = {"input_reference": ("still.png", buf, "image/png")}
        return requests.post(url, headers=_sora_headers(), data=data,
                             files=files, timeout=60)
    return requests.post(url, headers=_sora_headers(json_body=True), json=data,
                         timeout=60)


def _sora_poll(job_id: str) -> dict | None:
    """Poll a Sora job until it reaches a terminal state or the timeout elapses."""
    url = f"{AZURE_OPENAI_ENDPOINT}/openai/v1/videos/{job_id}?api-version={SORA_API_VERSION}"
    deadline = time.time() + SORA_TIMEOUT_SEC
    while time.time() < deadline:
        time.sleep(SORA_POLL_INTERVAL)
        try:
            status = requests.get(url, headers=_sora_headers(), timeout=60).json()
        except (requests.RequestException, ValueError):
            continue
        if status.get("status") in ("completed", "succeeded", "failed", "cancelled"):
            return status
    return None


def _sora_download(job_id: str, out_path: pathlib.Path) -> bool:
    url = (f"{AZURE_OPENAI_ENDPOINT}/openai/v1/videos/{job_id}/content"
           f"?api-version={SORA_API_VERSION}")
    try:
        resp = requests.get(url, headers=_sora_headers(), timeout=120)
    except requests.RequestException:
        return False
    if not resp.ok or not resp.content:
        return False
    out_path.write_bytes(resp.content)
    return True


def generate_sora(post: dict, out_dir: pathlib.Path) -> tuple[str, str] | None:
    """Render a reel via Azure Sora-2 (image+text when a still exists, else text).

    Returns ``(video_path, cover_path)`` on success, or ``None`` so the caller can
    fall back to the ffmpeg motion renderer (Sora unset, job failed/blocked/timed
    out, or download error). The output keeps Sora's native audio and is left clean
    (no composited branding).
    """
    if not sora_configured():
        return None
    post_id = str(post.get("id", "")).strip()
    visual = post.setdefault("visual", {})
    reel = visual.get("reel") or {}
    prompt = reel.get("sora_prompt") or visual.get("openai_prompt")
    if not prompt:
        print(f"  ! {post_id}: no reel.sora_prompt/visual.openai_prompt; Sora skipped")
        return None

    size = _sora_size(visual, reel)
    seconds = _sora_seconds(reel)
    still_path = out_dir / f"{post_id}.png"
    out_path = out_dir / f"{post_id}.mp4"
    cover_path = out_dir / f"{post_id}-cover.png"

    # Prefer image+text (anchors brand visuals); on a moderation block, retry as
    # plain text-to-video so the reel still renders.
    attempts: list[pathlib.Path | None] = []
    if still_path.exists():
        attempts.append(still_path)
    attempts.append(None)

    for ref in attempts:
        label = "image+text" if ref is not None else "text"
        try:
            resp = _sora_create_job(prompt, size, seconds, ref)
        except requests.RequestException as e:
            print(f"  x {post_id}: Sora create failed ({e})")
            return None
        if not resp.ok:
            print(f"  x {post_id}: Sora create HTTP {resp.status_code} "
                  f"({resp.text[:160]})")
            continue
        job_id = resp.json().get("id")
        print(f"  > {post_id}: Sora job {job_id} ({label}, {size}, {seconds}s) — polling")
        final = _sora_poll(job_id)
        if final is None:
            print(f"  x {post_id}: Sora job {job_id} timed out")
            return None
        status = final.get("status")
        if status in ("completed", "succeeded"):
            if not _sora_download(job_id, out_path):
                print(f"  x {post_id}: Sora download failed")
                return None
            if not export_cover(out_path, cover_path, post_id,
                                timestamp=min(1.0, seconds / 2)):
                return None
            print(f"  + {post_id} -> {out_path} (Sora {label})")
            return str(out_path), str(cover_path)
        err = (final.get("error") or {}).get("code")
        print(f"  ! {post_id}: Sora job {status} ({err})")
        if err == "moderation_blocked" and ref is not None:
            print(f"  > {post_id}: retrying Sora as text-only after moderation block")
            continue
        return None
    return None


def generate(post: dict, out_dir: pathlib.Path) -> tuple[str, str] | None:
    post_id = str(post.get("id", "")).strip()
    if not post_id:
        print("  ! reel post missing id; skipped")
        return None
    visual = post.setdefault("visual", {})
    reel = visual.get("reel") or {}
    mode = str(reel.get("mode", "sora")).lower()
    try:
        if mode == "clip":
            return generate_clip(post, out_dir)
        if mode == "motion":
            return generate_motion(post, out_dir)
        if mode != "sora":
            print(f"  ! {post_id}: unknown reel mode {mode!r}; using sora")
        result = generate_sora(post, out_dir)
        if result:
            return result
        if sora_configured():
            print(f"  > {post_id}: Sora unavailable/failed; falling back to motion")
        return generate_motion(post, out_dir)
    except Exception as e:  # noqa: BLE001 - one bad reel must not abort the queue
        print(f"  x {post_id}: reel generation failed ({e})")
        return None


def resolve_crosspost(post: dict, out_dir: pathlib.Path) -> bool:
    """Reuse another post's rendered reel for a cross-post (e.g. TikTok).

    A cross-post sets ``visual.source == "reuse"`` and ``visual.of ==
    "<source-post-id>"`` (typically the same day's Instagram reel). We copy that
    post's rendered mp4 + cover frame to THIS post's id, so every downstream tool
    (the PR preview keyed by id, the publisher via ``visual.file``) works with no
    special-casing. No new render — zero extra video-gen cost.
    """
    post_id = str(post.get("id", "")).strip()
    if not post_id:
        print("  ! cross-post missing id; skipped")
        return False
    visual = post.setdefault("visual", {})
    source_id = str(visual.get("of", "")).strip()
    if not source_id:
        print(f"  ! {post_id}: cross-post has no visual.of source id; skipped")
        return False
    src_mp4 = out_dir / f"{source_id}.mp4"
    if not src_mp4.exists():
        print(f"  ! {post_id}: source reel {src_mp4} not found "
              f"(of={source_id!r}); cross-post skipped")
        return False
    dst_mp4 = out_dir / f"{post_id}.mp4"
    shutil.copyfile(src_mp4, dst_mp4)
    visual["file"] = str(dst_mp4)
    src_cover = out_dir / f"{source_id}-cover.png"
    if src_cover.exists():
        dst_cover = out_dir / f"{post_id}-cover.png"
        shutil.copyfile(src_cover, dst_cover)
        visual["cover"] = str(dst_cover)
    print(f"  = {post_id} <- {source_id} (cross-post reuse)")
    return True


def main(queue_file: str) -> None:
    if not ffmpeg_available():
        print("  x ffmpeg not found on PATH; reel generation skipped.")
        return

    qpath = pathlib.Path(queue_file)
    posts = json.loads(qpath.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def is_reuse(post: dict) -> bool:
        return str((post.get("visual") or {}).get("source", "")).lower() == "reuse"

    # Pass 1: render every reel that owns its media (source != "reuse").
    for post in posts:
        if post.get("format") == "reel" and not is_reuse(post):
            result = generate(post, OUT_DIR)
            if result:
                file_path, cover_path = result
                visual = post.setdefault("visual", {})
                visual["file"] = file_path
                visual["cover"] = cover_path

    # Pass 2: resolve cross-posts (e.g. TikTok) that reuse a pass-1 reel's mp4.
    # Run second so it's independent of post order in the queue.
    for post in posts:
        if post.get("format") == "reel" and is_reuse(post):
            resolve_crosspost(post, OUT_DIR)

    qpath.write_text(json.dumps(posts, indent=2), encoding="utf-8")
    print("Queue updated with generated reel paths.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts\\reel_gen.py <queue_file>")
        sys.exit(2)
    main(sys.argv[1])
