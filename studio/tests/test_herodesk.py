import os
import json
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

from PIL import Image

from studio_lib import herodesk

D = date(2026, 10, 8)

PACK = """# ChatGPT hero batch — 2026-10-08

Save each image into `studio/inbox/` with the exact filename shown.

## STEP 1 — Paste once at the start of a new ChatGPT chat

You are my image studio for the Layer8Culture brand.
LOCKED STYLE (apply to every shot, never drift):
- No text.

## STEP 2 — Paste the shot list

SHOT LIST:
2. [20261008-hero-b.png] VERTICAL 9:16 (1080x1920). A reel shot.
1. [20261008-hero-a.png] A desk at night.
3. [20261008-radio-c.png] RADIO STYLE OVERRIDE ... A window.

Start with #1 now.
"""

ONE_PASTE_PACK = """# ChatGPT hero batch — 2026-10-08

## ONE-PASTE PROMPT

You are my image studio.
LOCKED STYLE:
- No text.

BATCH REQUEST:
Generate every shot as a separate image. Never a collage.

SHOT LIST:
1. [20261008-hero-a.png] A desk at night.
2. [20261008-hero-b.png] VERTICAL 9:16 (1080x1920). A reel shot.

Begin now.
"""


def _pack(tmp_path, monkeypatch):
    monkeypatch.setattr(herodesk, "media_dir", lambda d: tmp_path / "media" / d.isoformat())
    (tmp_path / f"PROMPTS-{D}.md").write_text(PACK, encoding="utf-8")
    return herodesk.load_pack(D, tmp_path)


def _img(path: Path, size=(1080, 1350), fmt="PNG", color="navy"):
    Image.new("RGB", size, color).save(path, fmt)
    return path


def test_parse_step1_and_ordered_shots(tmp_path, monkeypatch):
    p = _pack(tmp_path, monkeypatch)
    assert p.step1.startswith("You are my image studio") and "LOCKED STYLE" in p.step1 and "STEP 2" not in p.step1
    assert [s.filename for s in p.shots] == ["20261008-hero-a.png", "20261008-hero-b.png", "20261008-radio-c.png"]
    assert [s.aspect for s in p.shots] == ["4:5", "9:16", "4:5"]


def test_parse_one_paste_pack_and_combined_remaining_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(herodesk, "media_dir", lambda d: tmp_path / "media" / d.isoformat())
    (tmp_path / f"PROMPTS-{D}.md").write_text(ONE_PASTE_PACK, encoding="utf-8")
    p = herodesk.load_pack(D, tmp_path)
    assert "LOCKED STYLE" in p.preamble and "BATCH REQUEST" not in p.preamble
    assert [s.filename for s in p.shots] == ["20261008-hero-a.png", "20261008-hero-b.png"]
    txt = herodesk.combined_prompt_text(p, [p.shots[1]])
    assert "LOCKED STYLE" in txt
    assert "Generate ALL 1 numbered shots" in txt
    assert "SEPARATE downloadable image" in txt
    assert "Never combine shots into a collage" in txt
    assert "20261008-hero-b.png" in txt and "20261008-hero-a.png" not in txt
    assert "only generate one image per response" in txt


def test_remaining_skips_existing_and_renumbers_step2(tmp_path, monkeypatch):
    p = _pack(tmp_path, monkeypatch)
    _img(tmp_path / "20261008-hero-a.png")
    (tmp_path / "_used").mkdir()
    _img(tmp_path / "_used" / "20261008-radio-c.jpg", fmt="JPEG")
    rem = herodesk.remaining(p, tmp_path)
    assert [s.filename for s in rem] == ["20261008-hero-b.png"]
    txt = herodesk.step2_text(rem)
    assert "1. [20261008-hero-b.png]" in txt and "hero-a" not in txt


def test_default_date_picks_next_pack_with_missing(tmp_path, monkeypatch):
    _pack(tmp_path, monkeypatch)
    old = PACK.replace("2026-10-08", "2026-10-07").replace("20261008", "20261007")
    (tmp_path / "PROMPTS-2026-10-07.md").write_text(old, encoding="utf-8")
    assert herodesk.default_date(date(2026, 10, 8), tmp_path) == D
    assert herodesk.default_date(date(2026, 10, 7), tmp_path) == date(2026, 10, 7)
    for s in ("hero-a", "hero-b", "radio-c"):
        _img(tmp_path / f"20261007-{s}.png")
    assert herodesk.default_date(date(2026, 10, 7), tmp_path) == D
    assert herodesk.default_date(date(2026, 10, 9), tmp_path) is None


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_watcher_ignores_partial_preexisting_and_waits_for_stable(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    _img(dl / "old.png")
    clock = Clock()
    w = herodesk.DownloadWatcher(dl, settle_seconds=1.0, clock=clock)
    (dl / "x.png.crdownload").write_bytes(b"partial")
    (dl / "y.tmp").write_bytes(b"t")
    (dl / "notes.txt").write_text("hi")
    _img(dl / "new.png")
    assert w.poll() == []           # first sighting
    clock.t = 0.5
    assert w.poll() == []           # not settled yet
    clock.t = 1.6
    assert [p.name for p in w.poll()] == ["new.png"]
    clock.t = 3
    assert w.poll() == []           # reported once
    with open(dl / "grow.png", "wb") as fh:
        fh.write(b"a" * 10)
    w.poll()
    clock.t = 5
    with open(dl / "grow.png", "ab") as fh:
        fh.write(b"b" * 10)
    assert w.poll() == []           # size changed -> restart settle timer
    clock.t = 6.5
    assert [p.name for p in w.poll()] == ["grow.png"]


def test_desk_order_redo_skip():
    shots = [herodesk.Shot(i, f"s{i}.png", "") for i in (1, 2, 3)]
    desk = herodesk.Desk(shots)
    assert desk.target().filename == "s1.png"
    assert desk.redo().filename == "s1.png"
    assert desk.target().filename == "s1.png"   # redo replaces last
    assert desk.skip().filename == "s2.png"
    assert desk.target().filename == "s3.png"
    assert desk.done and [s.filename for s in desk.skipped] == ["s2.png"]


def test_file_image_converts_and_warns(tmp_path):
    inbox = tmp_path / "inbox"
    src = _img(tmp_path / "dl.webp", size=(1024, 1024), fmt="WEBP")
    dest, warn = herodesk.file_image(src, herodesk.Shot(1, "20261008-hero-b.png", "VERTICAL 9:16"), inbox)
    assert dest == inbox / "20261008-hero-b.png" and not src.exists()
    with Image.open(dest) as im:
        assert im.format == "PNG"
    assert warn and "9:16" in warn
    src2 = _img(tmp_path / "ok.png", size=(1080, 1350))
    _, warn2 = herodesk.file_image(src2, herodesk.Shot(1, "a.png", ""), inbox)
    assert warn2 is None


def test_download_mapping_requires_exact_name_or_explicit_choice(tmp_path):
    shots = [herodesk.Shot(1, "hero-a.png", ""), herodesk.Shot(2, "hero-b.png", "")]
    assert herodesk.match_download(tmp_path / "hero-b.jpg", shots) == shots[1]
    assert herodesk.match_download(tmp_path / "ChatGPT Image Oct 9.png", shots) is None
    assert herodesk.match_download(tmp_path / "ChatGPT Image Oct 9 (1).png", shots) is None


def test_visual_match_accepts_only_confident_unique_results(tmp_path, monkeypatch):
    monkeypatch.setattr(herodesk.shutil, "which", lambda command: "copilot")
    files = [_img(tmp_path / "generic-a.png"), _img(tmp_path / "generic-b.png", size=(1080, 1920))]
    shots = [herodesk.Shot(1, "hero-a.png", "a dark hallway and blue door"),
             herodesk.Shot(2, "hero-b.png", "a cyclist facing blue terrain")]
    payload = {"matches": [
        {"file": files[0].name, "shot": 1, "confidence": .96, "runner_up_shot": 2,
         "runner_up_confidence": .12, "evidence": "hallway"},
        {"file": files[1].name, "shot": 2, "confidence": .74, "runner_up_shot": 1,
         "runner_up_confidence": .30, "evidence": "unclear"},
    ], "ambiguous": []}

    def runner(args, **kwargs):
        assert "--attachment" in args and "Ignore attachment order" in args[2]
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    matched, uncertain = herodesk.visual_match_downloads(
        files, shots, {"model": "claude-sonnet-5.5", "heroes": {}}, runner=runner)
    assert matched[files[0]].shot == shots[0]
    assert files[1] in uncertain and "below 0.85" in uncertain[files[1]]


def test_visual_match_rejects_duplicate_shot_assignments(tmp_path, monkeypatch):
    monkeypatch.setattr(herodesk.shutil, "which", lambda command: "copilot")
    files = [_img(tmp_path / "a.png"), _img(tmp_path / "b.png")]
    shots = [herodesk.Shot(1, "hero-a.png", "scene a"), herodesk.Shot(2, "hero-b.png", "scene b")]
    payload = {"matches": [
        {"file": p.name, "shot": 1, "confidence": .95, "runner_up_confidence": .05, "evidence": "same"}
        for p in files
    ], "ambiguous": []}
    runner = lambda args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
    matched, uncertain = herodesk.visual_match_downloads(
        files, shots, {"model": "claude-sonnet-5.5", "heroes": {}}, runner=runner)
    assert not matched
    assert all("multiple downloads matched shot 1" in uncertain[p] for p in files)


def test_run_batch_end_to_end_maps_out_of_order_and_confirms_generic(tmp_path, monkeypatch):
    p = _pack(tmp_path, monkeypatch)
    dl = tmp_path / "dl"
    dl.mkdir()
    clock = Clock()
    w = herodesk.DownloadWatcher(dl, settle_seconds=0.5, clock=clock)
    clips = []
    # Exact filenames may arrive out of order and auto-map. A generic filename remains
    # pending until the user explicitly presses the shot number.
    script = [
        (lambda: _img(dl / "20261008-radio-c.png"), None),
        (None, None),                                      # exact -> shot 3
        (lambda: _img(dl / "ChatGPT Image 1.png", color="maroon"), None),
        (None, None),                                      # generic -> pending, not filed
        (None, "1"),                                       # explicitly map to shot 1
        (lambda: _img(dl / "20261008-hero-b.png", size=(1080, 1920)), None),
        (None, None),                                      # exact -> shot 2
    ]
    ticks = iter(script)

    def keys():
        act, key = next(ticks, (None, "q"))
        if act:
            act()
        clock.t += 1
        return key

    logs = []
    def visual_matcher(paths, shots, cfg, log):
        return {}, {path: "test ambiguity" for path in paths}

    desk = herodesk.run(p, {}, log=logs.append, open_browser=False, keys=keys, sleep=lambda s: None,
                        watcher=w, clipboard=lambda t: clips.append(t) or True, inbox=tmp_path,
                        visual_matcher=visual_matcher)
    assert desk.done
    assert len(clips) == 1 and "BATCH REQUEST" in clips[0] and "SEPARATE downloadable image" in clips[0]
    assert all((tmp_path / n).exists() for n in
               ("20261008-hero-a.png", "20261008-hero-b.png", "20261008-radio-c.png"))
    assert any("test ambiguity" in m for m in logs)
    assert list(dl.iterdir()) == []


def test_run_visually_maps_generic_batch_and_ignores_duplicate_bytes(tmp_path, monkeypatch):
    p = _pack(tmp_path, monkeypatch)
    dl = tmp_path / "dl"
    dl.mkdir()
    clock = Clock()
    w = herodesk.DownloadWatcher(dl, settle_seconds=0.5, clock=clock)
    script = [
        (lambda: (_img(dl / "generic.png"), shutil.copyfile(dl / "generic.png", dl / "duplicate.png")), None),
        (None, None),
    ]
    ticks = iter(script)

    def keys():
        act, key = next(ticks, (None, "q"))
        if act:
            act()
        clock.t += 1
        return key

    calls = []

    def visual_matcher(paths, shots, cfg, log):
        calls.append(paths)
        path = paths[0]
        return {path: herodesk.VisualMatch(shots[0], .97, .05, "desk")}, {}

    logs = []
    herodesk.run(p, {}, log=logs.append, open_browser=False, keys=keys, sleep=lambda s: None,
                 watcher=w, clipboard=lambda t: True, inbox=tmp_path, visual_matcher=visual_matcher)
    assert len(calls) == 1 and len(calls[0]) == 1
    assert (tmp_path / "20261008-hero-a.png").exists()
    assert any("ignored duplicate image bytes" in line for line in logs)


def test_downloads_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_DOWNLOADS_DIR", str(tmp_path))
    assert herodesk.downloads_dir({}) == tmp_path
    monkeypatch.delenv("STUDIO_DOWNLOADS_DIR")
    assert herodesk.downloads_dir({"heroes": {"downloads_dir": str(tmp_path / "x")}}) == tmp_path / "x"
    real = herodesk.downloads_dir({})
    assert real.name and (os.name != "nt" or real.is_absolute())
