#!/usr/bin/env python3
"""Incremental, offline preparation of manual images and their required videos."""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys

import manual_media
import manual_media_ingest
import reel_gen


def invalidate(post_ids: list[str], repo_root: pathlib.Path) -> list[str]:
    """Invalidate these posts and recorded reuse dependents; retain every file."""
    return manual_media.invalidate_records(post_ids, pathlib.Path(repo_root).resolve())


def _source(spec, root: pathlib.Path, record: dict) -> pathlib.Path | None:
    inbox = root / manual_media.DEFAULT_INBOX
    source = spec.find_source(inbox)
    if source is None and record.get("source_path"):
        path = manual_media.filesystem_path(root / record["source_path"])
        source = path if path.is_file() else None
    if source is None:
        source = spec.find_source(inbox / manual_media.INGESTED_DIRNAME)
    return source


def _image_problem(spec, root: pathlib.Path, *, require_record: bool = False) -> str | None:
    record = manual_media.read_record(root, f"image:{spec.image_id}")
    source = _source(spec, root, record)
    if not record:
        if source or require_record:
            return f"{spec.image_id}: image needs preparation from its original source"
        return None  # CI verifies the committed manifest, not local-only receipts.
    if record.get("status") != "ready":
        return record.get("error") or f"{spec.image_id}: image preparation is stale"
    if source is None:
        return f"{spec.image_id}: original source missing"
    if record.get("inputs") != manual_media_ingest.image_inputs(spec, source, root):
        return f"{spec.image_id}: source, typography settings or renderer changed"
    if not manual_media.outputs_match(record, root):
        return f"{spec.image_id}: finished image missing or changed"
    return None


def _video_jobs(posts: list[dict]) -> list[dict]:
    jobs = []
    for post in posts:
        post_id = str(post.get("id", ""))
        visual = post.get("visual") or {}
        if post.get("format") == "reel":
            reuse = str(visual.get("source", "")).lower() == "reuse"
            jobs.append({"key": f"reel:{post_id}", "id": post_id, "post": post,
                         "reuse": reuse,
                         "dependencies": [str(visual.get("of", ""))] if reuse else []})
        elif post.get("format") == "carousel":
            for index, slide in enumerate(visual.get("slides") or [], 1):
                if str(slide.get("media_type", "")).lower() == "video":
                    slide_id = f"{post_id}-{index}"
                    jobs.append({"key": f"slide-video:{slide_id}", "id": slide_id,
                                 "post": post, "slide": index, "dependencies": [],
                                 "reuse": False})
    return jobs


def _renderer(root: pathlib.Path, post: dict) -> str:
    executable = shutil.which("ffmpeg")
    binary = pathlib.Path(executable) if executable else None
    identity = None
    if binary and binary.is_file():
        stat = binary.stat()
        identity = [str(binary.resolve()), stat.st_size, stat.st_mtime_ns]
    return manual_media.fingerprint({
        "code": manual_media.file_fingerprint(pathlib.Path(reel_gen.__file__)),
        "orchestrator": manual_media.file_fingerprint(pathlib.Path(__file__)),
        "ffmpeg": identity,
        "font": manual_media.file_fingerprint(reel_gen.typography_font_for(post, root)),
        "canvas": [reel_gen.WIDTH, reel_gen.HEIGHT, reel_gen.FPS],
    })


def _video_inputs(job: dict, root: pathlib.Path, *, offline: bool) -> dict:
    visual = job["post"].get("visual") or {}
    reel = visual.get("reel") or {}
    out = root / manual_media.DEFAULT_OUT_DIR
    settings = {"account": job["post"].get("account"),
                "visual": {k: v for k, v in visual.items()
                           if k not in ("file", "files", "cover")}}
    if job["reuse"]:
        source_id = str(visual.get("of", ""))
        sources = [out / f"{source_id}.mp4", out / f"{source_id}-cover.png"]
        source_record = manual_media.read_record(root, f"reel:{source_id}")
        settings = {"of": source_id, "source_provenance": source_record.get("inputs")}
    elif job.get("slide"):
        index = job["slide"]
        slide = manual_media.carousel_slide_visuals(visual)[index - 1]
        settings = {"account": job["post"].get("account"), "slide": slide}
        sources = [out / f"{job['id']}.png"]
    elif str(reel.get("mode", "")).lower() == "clip":
        clip = reel.get("clip") or {}
        sources = [root / str(clip.get("source_file", ""))]
        if clip.get("query") and not clip.get("start"):
            transcript = reel_gen.newest_vtt(root / reel_gen.TRANSCRIPTS_DIR)
            if transcript:
                sources.append(transcript)
    else:
        still = out / f"{job['id']}.png"
        text_only_sora = (not offline and str(reel.get("mode", "sora")).lower() != "motion"
                          and not still.is_file())
        sources = [] if text_only_sora else [still]
        bed = reel_gen.find_lofi_bed(root)
        if str(reel.get("audio", "lofi")).lower() == "lofi" and bed:
            sources.append(bed)
        settings["backend"] = "motion" if offline else str(reel.get("mode", "sora"))
    return {
        "sources": {manual_media.relative_path(p, root): manual_media.file_fingerprint(p)
                    for p in sources},
        "settings": manual_media.fingerprint(settings),
        "renderer": _renderer(root, job["post"]),
    }


def _video_problem(job: dict, root: pathlib.Path, *, require_record: bool = False) -> str | None:
    record = manual_media.read_record(root, job["key"])
    if not record:
        return f"{job['id']}: video needs preparation" if require_record else None
    if record.get("status") != "ready":
        return record.get("error") or f"{job['id']}: video preparation is stale"
    inputs = _video_inputs(job, root, offline=record.get("offline", True))
    if inputs != record.get("inputs"):
        return f"{job['id']}: video source, settings or renderer changed"
    if not manual_media.outputs_match(record, root):
        return f"{job['id']}: video or cover missing or changed"
    return None


def preparation_status(posts: list[dict], repo_root: pathlib.Path) -> dict:
    """Read local provenance without rendering, mutating, or importing readiness."""
    root = pathlib.Path(repo_root).resolve()
    blockers, warnings, blocked_posts = [], [], set()
    try:
        for spec in manual_media.plan_images(posts):
            problem = _image_problem(spec, root)
            if problem:
                blockers.append(problem)
                blocked_posts.add(spec.post_id)
            record = manual_media.read_record(root, f"image:{spec.image_id}")
            warnings.extend(record.get("warnings", []))
        jobs = _video_jobs(posts)
        for job in jobs:
            problem = _video_problem(job, root)
            if problem:
                blockers.append(problem)
                blocked_posts.add(str(job["post"].get("id")))
            record = manual_media.read_record(root, job["key"])
            warnings.extend(record.get("warnings", []))
        while True:
            added = {str(job["post"].get("id")) for job in jobs
                     if blocked_posts.intersection(job["dependencies"])} - blocked_posts
            if not added:
                break
            blockers.extend(f"{post_id}: source reel is not prepared" for post_id in sorted(added))
            blocked_posts.update(added)
    except (ValueError, TypeError, KeyError) as exc:
        blockers.append(f"Media provenance could not be read: {exc}")
    return {"blockers": list(dict.fromkeys(blockers)),
            "warnings": list(dict.fromkeys(warnings))}


def _warnings(job: dict, root: pathlib.Path, backend: str | None = None) -> list[str]:
    post_id = job["id"]
    reel = (job["post"].get("visual") or {}).get("reel") or {}
    if job["reuse"]:
        parent = manual_media.read_record(root, f"reel:{job['dependencies'][0]}")
        return [f"{post_id}: reuses {message}" for message in parent.get("warnings", [])]
    if job.get("slide"):
        return [f"{post_id}: carousel video has no audio"]
    if backend == "sora":
        audio = reel_gen.ffprobe_has_audio(
            root / manual_media.DEFAULT_OUT_DIR / f"{post_id}.mp4")
        if audio is False:
            return [f"{post_id}: rendered video has no audio"]
        if audio is None:
            return [f"{post_id}: rendered audio could not be verified"]
        return []
    if str(reel.get("mode", "")).lower() == "clip":
        path = root / str((reel.get("clip") or {}).get("source_file", ""))
        audio = reel_gen.ffprobe_has_audio(path)
        if audio is False:
            return [f"{post_id}: source has no audio; silent track used"]
        if audio is None:
            return [f"{post_id}: source audio could not be verified"]
        return []
    if str(reel.get("audio", "lofi")).lower() != "lofi" or not reel_gen.find_lofi_bed(root):
        return [f"{post_id}: silent audio; no music bed selected or available"]
    return []


def _write_video_paths(job: dict, root: pathlib.Path) -> None:
    visual = job["post"].setdefault("visual", {})
    out = manual_media.DEFAULT_OUT_DIR
    video = (out / f"{job['id']}.mp4").as_posix()
    if job.get("slide"):
        index = job["slide"]
        files = list(visual.get("files") or [])
        while len(files) < len(visual.get("slides") or []):
            files.append((out / f"{job['post']['id']}-{len(files) + 1}.png").as_posix())
        files[index - 1] = video
        visual["files"], visual["file"] = files, files[0]
    else:
        visual["file"] = video
        visual["cover"] = (out / f"{job['id']}-cover.png").as_posix()


def _clear_video_paths(job: dict) -> None:
    visual = job["post"].get("visual") or {}
    visual.pop("file", None)
    if job.get("slide"):
        visual.pop("files", None)
    else:
        visual.pop("cover", None)


def prepare_videos(queue_file: pathlib.Path, repo_root: pathlib.Path, *,
                   offline: bool = True, require_images: bool = False) -> dict:
    root = pathlib.Path(repo_root).resolve()
    queue_file = root / queue_file
    posts = json.loads(queue_file.read_text(encoding="utf-8"))
    report = {"prepared": 0, "unchanged": 0, "failed": [], "warnings": []}
    jobs = _video_jobs(posts)
    if not jobs:
        return report
    by_id = {str(p.get("id")): p for p in posts}
    specs = {s.image_id: s for s in manual_media.plan_images(posts)}
    out = root / manual_media.DEFAULT_OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    failed_posts, completed = set(), set()
    pending = list(jobs)
    while pending:
        ready = [j for j in pending if not j["reuse"]
                 or set(j["dependencies"]).issubset(completed)
                 or any(d not in by_id or d in failed_posts for d in j["dependencies"])]
        if not ready:
            ready = pending[:]
        for job in ready:
            pending.remove(job)
            post_id = str(job["post"].get("id"))
            record = manual_media.read_record(root, job["key"])
            try:
                if job["reuse"]:
                    source_id = job["dependencies"][0]
                    if source_id not in completed or source_id in failed_posts:
                        raise RuntimeError(f"source reel {source_id!r} is missing, stale or cyclic")
                else:
                    for spec in (s for s in specs.values() if s.post_id == post_id):
                        problem = _image_problem(spec, root, require_record=require_images)
                        if problem:
                            raise RuntimeError(problem)
                if _video_problem(job, root, require_record=True) is None:
                    _write_video_paths(job, root)
                    report["unchanged"] += 1
                    report["warnings"].extend(record.get("warnings", []))
                    completed.add(post_id)
                    continue
                # Persist before rendering so interruption cannot re-advertise old bytes.
                manual_media.write_record(root, job["key"], {
                    **record, "post_id": post_id, "status": "preparing",
                    "dependencies": job["dependencies"],
                })
                _clear_video_paths(job)
                manual_media.atomic_json(queue_file, posts)
                if not reel_gen.ffmpeg_available():
                    raise RuntimeError("ffmpeg not found on PATH")
                if not job["reuse"] and not job.get("slide"):
                    visual = job["post"].get("visual") or {}
                    reel = visual.get("reel") or {}
                    text = (visual.get("headline") if reel.get("mode") == "clip"
                            else reel.get("overlay_beats") or reel.get("beats"))
                    font = reel_gen.typography_font_for(job["post"], root)
                    if text and not font.is_file():
                        raise RuntimeError(f"required typography font missing: {font}")
                inputs = _video_inputs(job, root, offline=offline)
                if not all(inputs["sources"].values()):
                    raise RuntimeError("required video source is missing")
                render_info = {}
                if job["reuse"]:
                    result = reel_gen.resolve_crosspost(job["post"], out, repo_root=root)
                elif job.get("slide"):
                    slide = (job["post"]["visual"]["slides"])[job["slide"] - 1]
                    result = reel_gen.render_carousel_video_slide(
                        job["post"], slide, job["slide"], out, repo_root=root)
                else:
                    result = reel_gen.generate(
                        job["post"], out, offline=offline, repo_root=root,
                        render_info=render_info)
                if not result:
                    raise RuntimeError("required video render failed; see render log")
                warnings = _warnings(job, root, render_info.get("backend"))
                outputs = [out / f"{job['id']}.mp4", out / f"{job['id']}-cover.png"]
                if not all(path.is_file() and path.stat().st_size for path in outputs):
                    raise RuntimeError("renderer did not produce both video and cover")
                manual_media.write_record(root, job["key"], {
                    "post_id": post_id, "status": "ready", "inputs": inputs,
                    "outputs": {manual_media.relative_path(p, root):
                                manual_media.file_fingerprint(p) for p in outputs},
                    "warnings": warnings, "dependencies": job["dependencies"],
                    "offline": offline, "backend": render_info.get("backend"),
                })
                _write_video_paths(job, root)
                report["prepared"] += 1
                report["warnings"].extend(warnings)
                completed.add(post_id)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                message = f"{job['id']}: {exc}"
                print(f"  x {message}")
                report["failed"].append(message)
                failed_posts.add(post_id)
                _clear_video_paths(job)
                manual_media.write_record(root, job["key"], {
                    **record, "post_id": post_id, "status": "failed", "error": message,
                    "dependencies": job["dependencies"], "warnings": [],
                })
            manual_media.atomic_json(queue_file, posts)
    for job in jobs:
        if str(job["post"].get("id")) in failed_posts:
            _clear_video_paths(job)
    manual_media.atomic_json(queue_file, posts)
    return report


def prepare(queue_file: pathlib.Path, repo_root: pathlib.Path) -> dict:
    """Prepare stale manual assets locally; never call an image or video API."""
    root = pathlib.Path(repo_root).resolve()
    queue_file = root / queue_file
    images = manual_media_ingest.ingest(
        queue_file, root / manual_media.DEFAULT_INBOX, root / manual_media.DEFAULT_OUT_DIR,
        repo_root=root)
    videos = prepare_videos(queue_file, root, offline=True, require_images=True)
    failed = images["failed"] + [f"{i}: original image missing" for i in images["missing"]]
    report = {
        "prepared": images["prepared"] + videos["prepared"],
        "unchanged": images["unchanged"] + videos["unchanged"],
        "failed": list(dict.fromkeys(failed + videos["failed"])),
        "warnings": list(dict.fromkeys(images["warnings"] + videos["warnings"])),
        "images_prepared": images["prepared"], "videos_prepared": videos["prepared"],
    }
    status = preparation_status(json.loads(queue_file.read_text(encoding="utf-8")), root)
    report["failed"] = list(dict.fromkeys(report["failed"] + status["blockers"]))
    report["warnings"] = list(dict.fromkeys(report["warnings"] + status["warnings"]))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue_file", type=pathlib.Path)
    parser.add_argument("--repo-root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--json", type=pathlib.Path, help="write the structured outcome to this file")
    args = parser.parse_args()
    try:
        report = prepare(args.queue_file, args.repo_root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report = {"failed": [str(exc)], "warnings": [], "prepared": 0, "unchanged": 0}
    if args.json:
        manual_media.atomic_json(args.json, report)
    for message in report["warnings"]:
        print(f"  ! {message}")
    for message in report["failed"]:
        print(f"  x {message}")
    print(f"Prepared {report['prepared']}; unchanged {report['unchanged']}; "
          f"failed {len(report['failed'])}; warnings {len(report['warnings'])}.")
    return int(bool(report["failed"]))


if __name__ == "__main__":
    sys.exit(main())
