#!/usr/bin/env python3
"""Shared helpers for the engine's manual (no-API) image mode.

The engine normally renders every ``visual.source == "openai"`` image through the
Images API in ``scripts/openai_gen.py``. Manual mode lets the whole pipeline run
with no image API at all:

  1. ``python scripts/openai_gen.py <queue> --manual`` writes a copy-paste prompt
     pack next to the queue (``queue/<name>.prompts.md``).
  2. You paste each prompt into ChatGPT / Copilot and save the result into
     ``assets/manual-inbox/`` using the exact filename the pack gives.
  3. ``python scripts/manual_media_ingest.py <queue>`` crops, upscales, composites
     brand typography, and writes the paths back into the queue.

This module owns the single source of truth for *which* images a queue needs and
what each one is called, so the prompt pack and the ingest step can never drift.
It is stdlib-only (no Pillow, no openai) and is safe to import anywhere.
"""
import hashlib
import json
import os
import pathlib
import shutil
import uuid
from dataclasses import dataclass

# Where hand-generated images are dropped, and where finished ones land.
DEFAULT_INBOX = pathlib.Path("assets/manual-inbox")
DEFAULT_OUT_DIR = pathlib.Path("assets/generated")
# Consumed source files are moved here so a re-run never re-composites type.
INGESTED_DIRNAME = "_ingested"

# Brand aspect -> the canvas to ask for when generating by hand. Mirrors
# openai_gen.ASPECT_SIZE (duplicated so this module stays import-light).
ASPECT_SIZE = {
    "1:1": "1024x1024",
    "9:16": "1024x1536",
    "16:9": "1536x1024",
}
ASPECT_LABEL = {
    "1:1": "square",
    "9:16": "portrait (vertical)",
    "16:9": "landscape",
}
DEFAULT_ASPECT = "1:1"
DEFAULT_SIZE = "1024x1024"
# Formats that are vertical by nature; matches openai_gen.generate().
VERTICAL_FORMATS = ("story", "reel")
# Anything a chat assistant is likely to hand back. Everything is normalized to
# PNG on ingest.
ACCEPTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

PROMPT_PACK_SUFFIX = ".prompts.md"


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()


def file_fingerprint(path: pathlib.Path) -> str | None:
    path = filesystem_path(pathlib.Path(path))
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def staging_path(path: pathlib.Path) -> pathlib.Path:
    """Keep staged writes on the destination volume for atomic promotion."""
    return path.with_name(f".{uuid.uuid4().hex}{path.suffix}")


def filesystem_path(path: pathlib.Path) -> pathlib.Path:
    """Content-addressed versions can exceed legacy Windows path limits."""
    absolute = str(path.absolute())
    if os.name == "nt" and len(absolute) >= 240 and not absolute.startswith("\\\\?\\"):
        if absolute.startswith("\\\\"):
            absolute = "\\\\?\\UNC\\" + absolute[2:]
        else:
            absolute = "\\\\?\\" + absolute
        return pathlib.Path(absolute)
    return path


def atomic_json(path: pathlib.Path, value) -> None:
    text = json.dumps(value, indent=2, ensure_ascii=True)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = staging_path(path)
    try:
        with stage.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        stage.replace(path)
    finally:
        stage.unlink(missing_ok=True)


def snapshot_file(source: pathlib.Path, versions_root: pathlib.Path) -> pathlib.Path:
    """Preserve bytes under <versions_root>/<sha256><lowercase suffix>."""
    digest = file_fingerprint(source)
    if digest is None:
        raise FileNotFoundError(source)
    target = filesystem_path(versions_root / f"{digest}{source.suffix.lower()}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        stage = staging_path(target)
        try:
            shutil.copyfile(source, stage)
            stage.replace(target)
        finally:
            stage.unlink(missing_ok=True)
    elif file_fingerprint(target) != digest:
        raise ValueError(f"Immutable media snapshot is corrupt: {target}")
    return target


def record_path(repo_root: pathlib.Path, key: str) -> pathlib.Path:
    return filesystem_path(
        repo_root / ".local" / "media" / "artifacts" / f"{fingerprint(key)}.json")


def read_record(repo_root: pathlib.Path, key: str) -> dict:
    path = record_path(repo_root, key)
    if not path.is_file():
        return {}
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"Invalid media provenance object: {path}")
    for field in ("inputs", "outputs"):
        if field in record and not isinstance(record[field], dict):
            raise ValueError(f"Invalid media provenance {field}: {path}")
    for field in ("warnings", "dependencies"):
        if field in record and (not isinstance(record[field], list)
                                or any(not isinstance(v, str) for v in record[field])):
            raise ValueError(f"Invalid media provenance {field}: {path}")
    return record


def write_record(repo_root: pathlib.Path, key: str, record: dict) -> None:
    atomic_json(record_path(repo_root, key), {**record, "key": key, "version": 1})


def relative_path(path: pathlib.Path, repo_root: pathlib.Path) -> str:
    def normalized(value):
        resolved = str(value.resolve())
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
        return pathlib.Path(resolved)
    try:
        return normalized(path).relative_to(normalized(repo_root)).as_posix()
    except ValueError:
        return normalized(path).as_posix()


def outputs_match(record: dict, repo_root: pathlib.Path) -> bool:
    outputs = record.get("outputs") or {}
    return bool(outputs) and all(
        digest and file_fingerprint(repo_root / name) == digest
        for name, digest in outputs.items()
    )


def invalidate_records(post_ids, repo_root: pathlib.Path,
                       *, images: bool = True) -> list[str]:
    """Mark dependents stale without deleting originals or finished versions."""
    affected = set(post_ids)
    records = []
    for path in (repo_root / ".local" / "media" / "artifacts").glob("*.json"):
        records.append(json.loads(filesystem_path(path).read_text(encoding="utf-8")))
    while True:
        expanded = affected | {
            record["post_id"] for record in records
            if affected.intersection(record.get("dependencies", []))
        }
        if expanded == affected:
            break
        affected = expanded
    for record in records:
        if record.get("post_id") not in affected:
            continue
        if not images and record["key"].startswith("image:"):
            continue
        record["status"] = "stale"
        write_record(repo_root, record["key"], record)
    return sorted(affected)


@dataclass
class ImageSpec:
    """One image a queue needs: its filename stem, prompt, and brand context."""

    image_id: str
    post_id: str
    prompt: str
    aspect: str
    visual: dict
    account: str | None = None
    fmt: str = "single"
    slide_index: int | None = None
    slide_role: str | None = None

    @property
    def filename(self) -> str:
        return f"{self.image_id}.png"

    @property
    def size(self) -> str:
        return ASPECT_SIZE.get(self.aspect, DEFAULT_SIZE)

    @property
    def headline(self) -> str:
        return str(self.visual.get("headline") or "").strip()

    @property
    def subtext(self) -> str:
        return str(self.visual.get("subtext") or "").strip()

    def output_path(self, out_dir: pathlib.Path = DEFAULT_OUT_DIR) -> pathlib.Path:
        return pathlib.Path(out_dir) / self.filename

    def inbox_path(self, inbox: pathlib.Path = DEFAULT_INBOX) -> pathlib.Path:
        """The exact file to save a hand-generated image as (PNG is preferred,
        but any ACCEPTED_SUFFIXES extension is picked up by find_source)."""
        return pathlib.Path(inbox) / self.filename

    def find_source(self, inbox: pathlib.Path = DEFAULT_INBOX) -> pathlib.Path | None:
        """Locate the dropped file for this image, if present.

        Matches ``<image_id>.<ext>`` for any accepted extension, preferring PNG,
        and falls back to a case-insensitive stem match so a file saved as
        ``<ID>.PNG`` still lands.
        """
        inbox = pathlib.Path(inbox)
        if not inbox.is_dir():
            return None
        for suffix in ACCEPTED_SUFFIXES:
            candidate = inbox / f"{self.image_id}{suffix}"
            if candidate.is_file():
                return candidate
        target = self.image_id.lower()
        matches = [
            p for p in sorted(inbox.iterdir())
            if p.is_file()
            and p.stem.lower() == target
            and p.suffix.lower() in ACCEPTED_SUFFIXES
        ]
        return matches[0] if matches else None


def carousel_slide_visuals(visual: dict,
                           default_quality: str = "high",
                           default_overlay_position: str = "lower-left") -> list[dict]:
    """Merge post-level visual defaults into each carousel slide.

    Shared by openai_gen.render_carousel and the manual image planner so both
    resolve slide aspect/quality/typography identically. Returns one effective
    visual dict per ``visual.slides`` entry, in order.
    """
    slides = visual.get("slides") or []
    if not slides:
        return []
    base = {
        "aspect": visual.get("aspect", DEFAULT_ASPECT),
        "quality": visual.get("quality", default_quality),
        "overlay_position": visual.get("overlay_position", default_overlay_position),
    }
    if visual.get("typography_preset") is not None:
        base["typography_preset"] = visual["typography_preset"]
    for key in ("logo_position", "logo_subtle", "logo_opacity"):
        if visual.get(key) is not None:
            base[key] = visual[key]
    return [
        {**base, **{k: v for k, v in slide.items() if v is not None}}
        for slide in slides
    ]


def plan_images(posts: list[dict],
                default_quality: str = "high",
                default_overlay_position: str = "lower-left") -> list[ImageSpec]:
    """Enumerate every image a queue needs, in queue order.

    Mirrors openai_gen's dispatch exactly:
      * single / story / reel -> one image at ``<post_id>.png`` (story and reel
        default to 9:16 when the generator didn't pin an aspect)
      * carousel              -> ``<post_id>-<n>.png`` per ``visual.slides`` entry

    Posts whose ``visual.source`` isn't "openai" are skipped: "library" posts
    bring their own asset and "reuse" cross-posts get their media from
    scripts/reel_gen.py. Entries without an ``openai_prompt`` are skipped too,
    matching openai_gen._render_image.
    """
    specs: list[ImageSpec] = []
    for post in posts:
        visual = post.get("visual") or {}
        if str(visual.get("source", "")).lower() != "openai":
            continue
        post_id = str(post.get("id", "")).strip()
        if not post_id:
            continue
        fmt = post.get("format", "single")
        account = post.get("account")
        if fmt == "carousel":
            for index, slide in enumerate(
                carousel_slide_visuals(visual, default_quality,
                                       default_overlay_position), 1):
                prompt = str(slide.get("openai_prompt") or "").strip()
                if not prompt:
                    continue
                specs.append(ImageSpec(
                    image_id=f"{post_id}-{index}",
                    post_id=post_id,
                    prompt=prompt,
                    aspect=str(slide.get("aspect") or DEFAULT_ASPECT),
                    visual=slide,
                    account=account,
                    fmt=fmt,
                    slide_index=index,
                    slide_role=slide.get("role"),
                ))
            continue
        prompt = str(visual.get("openai_prompt") or "").strip()
        if not prompt:
            continue
        effective = dict(visual)
        if fmt in VERTICAL_FORMATS and "aspect" not in visual:
            effective["aspect"] = "9:16"
        specs.append(ImageSpec(
            image_id=post_id,
            post_id=post_id,
            prompt=prompt,
            aspect=str(effective.get("aspect") or DEFAULT_ASPECT),
            visual=effective,
            account=account,
            fmt=fmt,
        ))
    return specs


def prompt_pack_path(queue_path: pathlib.Path) -> pathlib.Path:
    """queue/2026-08-18.json -> queue/2026-08-18.prompts.md (matches .summary.md)."""
    queue_path = pathlib.Path(queue_path)
    return queue_path.with_name(f"{queue_path.stem}{PROMPT_PACK_SUFFIX}")


def _clean_area_note(spec: ImageSpec) -> str:
    """Where the generated art must stay calm, so composited type stays legible."""
    preset = str(spec.visual.get("typography_preset") or "").strip().lower()
    if preset == "editorial_drop":
        return ("Keep the bottom half calm and low-detail — an oversized headline "
                "is composited there afterward.")
    position = str(spec.visual.get("overlay_position") or "lower-left").lower()
    area = "upper third" if position.startswith("upper") else "lower third"
    note = (f"Keep the {area} as clean negative space — brand typography is "
            f"composited there afterward.")
    if spec.fmt == "reel":
        note += " Keep the upper third calm too (animated text beats land there)."
    return note


def copy_prompt(spec: ImageSpec) -> str:
    """The exact block to paste into ChatGPT / Copilot for one image.

    The post's ``openai_prompt`` (already composed per brand/visual-style.md by
    the generation step) plus the canvas, the no-text rule, and the negative-space
    reminder that the API path gets from its size/quality parameters.
    """
    label = ASPECT_LABEL.get(spec.aspect, spec.aspect)
    return "\n".join([
        spec.prompt,
        "",
        f"Aspect ratio: {spec.aspect} {label}. Generate at {spec.size}.",
        "Render no text, letters, numbers, logos, or watermarks anywhere in the image.",
        _clean_area_note(spec),
    ])


def _fence(body: str) -> str:
    """A code fence long enough to safely wrap ``body`` (prompts may contain ```)."""
    longest = 0
    run = 0
    for char in body:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _cell(value: str) -> str:
    """Escape a value for a markdown table cell."""
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def build_prompt_pack(specs: list[ImageSpec],
                      queue_path: pathlib.Path,
                      inbox: pathlib.Path = DEFAULT_INBOX) -> str:
    """Render the copy-paste prompt pack markdown for a queue."""
    queue_path = pathlib.Path(queue_path)
    queue_ref = queue_path.as_posix()
    inbox_ref = pathlib.Path(inbox).as_posix()
    accounts = sorted({str(s.account) for s in specs if s.account})

    lines: list[str] = [
        f"# Image prompt pack — {queue_path.stem}",
        "",
        f"Manual (no-API) image mode for `{queue_ref}`.",
    ]
    if accounts:
        lines.append(f"Account(s): {', '.join(accounts)}.")
    lines += [
        "",
        f"**{len(specs)} image(s)** to generate by hand.",
        "",
        "## How to use",
        "",
        "1. Copy one prompt block below into ChatGPT or Copilot and ask for the "
        "image at the size given.",
        f"2. Save the result into `{inbox_ref}/` using the exact filename shown "
        "(`.png`, `.jpg` or `.webp` all work).",
        f"3. Run `python scripts/manual_media_ingest.py {queue_ref}` — it crops to "
        "aspect, upscales to a 2K master, composites the brand headline, and "
        "writes the paths back into the queue.",
        "",
        "> Ask for the artwork only. Never ask for text in the image: headlines and "
        "supporting lines are composited by the engine after ingest, so the art "
        "just needs clean negative space where they land.",
        "",
    ]

    if not specs:
        lines += [
            "## Nothing to generate",
            "",
            "No post in this queue has a `visual.source` of `openai` with an "
            "`openai_prompt`, so there is no image to make by hand.",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "## Checklist",
        "",
        "| # | Save as | Post | Format | Aspect | Request | Headline |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for n, spec in enumerate(specs, 1):
        post_label = spec.post_id
        if spec.slide_index:
            post_label = f"{spec.post_id} (slide {spec.slide_index})"
        lines.append(
            f"| {n} | `{_cell(spec.filename)}` | `{_cell(post_label)}` | "
            f"{_cell(spec.fmt)} | {_cell(spec.aspect)} | {_cell(spec.size)} | "
            f"{_cell(spec.headline)} |"
        )
    lines.append("")

    for n, spec in enumerate(specs, 1):
        body = copy_prompt(spec)
        fence = _fence(body)
        detail = f"{spec.fmt}"
        if spec.slide_index:
            detail += f" slide {spec.slide_index}"
            if spec.slide_role:
                detail += f" ({spec.slide_role})"
        if spec.account:
            detail += f" · {spec.account}"
        lines += [
            "---",
            "",
            f"## {n}. `{spec.filename}`",
            "",
            f"- **Post:** `{spec.post_id}` — {detail}",
            f"- **Aspect:** {spec.aspect} "
            f"{ASPECT_LABEL.get(spec.aspect, '')} — request **{spec.size}**",
            f"- **Save as:** `{spec.inbox_path(inbox).as_posix()}`",
        ]
        if spec.headline:
            composited = f'headline "{spec.headline}"'
            if spec.subtext:
                composited += f' · subtext "{spec.subtext}"'
            lines.append(f"- **Composited after ingest:** {composited}")
        lines += ["", fence + "text", body, fence, ""]

    return "\n".join(lines)


def write_prompt_pack(specs: list[ImageSpec],
                      queue_path: pathlib.Path,
                      out_path: pathlib.Path | None = None,
                      inbox: pathlib.Path = DEFAULT_INBOX) -> pathlib.Path:
    """Write the prompt pack markdown and return the path it landed at."""
    target = pathlib.Path(out_path) if out_path else prompt_pack_path(queue_path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_prompt_pack(specs, queue_path, inbox), encoding="utf-8")
    return target
