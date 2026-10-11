"""Unit tests for the ad-hoc web UI server (scripts/adhoc_server.py).

Covers the pure, security-relevant helpers: zip entry sanitising, safe
extraction, the aspect/shape check, path traversal guards, the batch prompt and
the staging/assignment round trip, plus the /fonts/ allowlist over real HTTP.
The rest of the HTTP layer and the subprocess job runner are exercised by hand
(see docs/adhoc-web-ui.md), not here.

Run with:
    python -m unittest tests.test_adhoc_server
"""
import datetime as dt
import io
import json
import mimetypes
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import adhoc_server  # noqa: E402
import manual_media  # noqa: E402

from PIL import Image  # noqa: E402


def png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 12, 20)).save(buf, "PNG")
    return buf.getvalue()


class SanitizeEntryNameTests(unittest.TestCase):
    def test_keeps_an_already_safe_name(self):
        self.assertEqual(
            adhoc_server.sanitize_entry_name("20260818-layer8culture-tiktok-1.png"),
            "20260818-layer8culture-tiktok-1.png",
        )

    def test_flattens_nested_directories(self):
        self.assertEqual(
            adhoc_server.sanitize_entry_name("ChatGPT Images/nested/shot.png"),
            "shot.png",
        )

    def test_reduces_traversal_to_a_basename(self):
        self.assertEqual(
            adhoc_server.sanitize_entry_name("../../escaped.png"), "escaped.png")
        self.assertEqual(
            adhoc_server.sanitize_entry_name("..\\..\\escaped.png"), "escaped.png")

    def test_slugifies_assistant_style_names(self):
        # These are the names ChatGPT actually hands back; dropping them would
        # discard the very files the reconciliation step exists to place.
        self.assertEqual(
            adhoc_server.sanitize_entry_name("Generated image (4).png"),
            "Generated_image_4.png",
        )
        self.assertEqual(
            adhoc_server.sanitize_entry_name("ChatGPT Image Aug 18, 2026, 09_41_12 AM.png"),
            "ChatGPT_Image_Aug_18_2026_09_41_12_AM.png",
        )

    def test_slug_always_satisfies_the_safe_name_pattern(self):
        for raw in ("a b.png", "%%%.png", "-leading.png", ".hidden/ok!.png",
                    "ünïcode.png", "(((.png"):
            name = adhoc_server.sanitize_entry_name(raw)
            if name is not None:
                self.assertRegex(name, adhoc_server.SAFE_NAME_RE)

    def test_skips_noise_and_non_images(self):
        for raw in ("__MACOSX/._shot.png", "folder/__MACOSX/._shot.png",
                    ".DS_Store", "notes.txt", "archive.zip", "dir/", ""):
            self.assertIsNone(adhoc_server.sanitize_entry_name(raw), raw)


class ExtractZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dest = self.tmp / "staging"

    def build(self, entries: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, payload in entries.items():
                z.writestr(name, payload)
        return buf.getvalue()

    def test_stores_images_and_skips_noise(self):
        data = self.build({
            "shot-a.png": png_bytes(64, 64),
            "sub/shot-b.png": png_bytes(64, 64),
            "__MACOSX/._shot-a.png": b"junk",
            ".DS_Store": b"junk",
            "readme.txt": b"junk",
        })
        stored, skipped = adhoc_server.extract_zip(data, self.dest)
        self.assertEqual(sorted(stored), ["shot-a.png", "shot-b.png"])
        self.assertEqual(len(skipped), 3)

    def test_zip_slip_cannot_escape_the_destination(self):
        data = self.build({"../../pwned.png": png_bytes(8, 8)})
        stored, _ = adhoc_server.extract_zip(data, self.dest)
        self.assertEqual(stored, ["pwned.png"])
        self.assertTrue((self.dest / "pwned.png").is_file())
        self.assertFalse((self.tmp / "pwned.png").exists())
        self.assertFalse((self.tmp.parent / "pwned.png").exists())

    def test_absolute_path_entry_cannot_escape(self):
        data = self.build({"C:/Windows/evil.png": png_bytes(8, 8)})
        stored, _ = adhoc_server.extract_zip(data, self.dest)
        for name in stored:
            self.assertTrue((self.dest / name).is_file())

    def test_duplicate_names_do_not_overwrite(self):
        data = self.build({"a/shot.png": png_bytes(64, 64),
                           "b/shot.png": png_bytes(32, 32)})
        stored, _ = adhoc_server.extract_zip(data, self.dest)
        self.assertEqual(len(stored), 2)
        self.assertEqual(len(set(stored)), 2)

    def test_rejects_a_non_zip_payload(self):
        with self.assertRaises(ValueError):
            adhoc_server.extract_zip(b"definitely not a zip", self.dest)


class ShapeCheckTests(unittest.TestCase):
    def test_accepts_the_generation_canvas_and_the_final_crop(self):
        # The prompt asks for 1024x1536 (0.667); ingest crops it to a true 9:16.
        self.assertTrue(adhoc_server.shape_matches((1024, 1536), "9:16"))
        self.assertTrue(adhoc_server.shape_matches((1152, 2048), "9:16"))
        self.assertTrue(adhoc_server.shape_matches((1024, 1024), "1:1"))
        self.assertTrue(adhoc_server.shape_matches((2048, 2048), "1:1"))
        self.assertTrue(adhoc_server.shape_matches((1536, 1024), "16:9"))

    def test_rejects_a_shape_from_a_different_aspect(self):
        self.assertFalse(adhoc_server.shape_matches((1024, 1024), "9:16"))
        self.assertFalse(adhoc_server.shape_matches((1024, 1536), "1:1"))
        self.assertFalse(adhoc_server.shape_matches((1536, 1024), "9:16"))

    def test_unknown_inputs_report_undecidable_rather_than_wrong(self):
        self.assertIsNone(adhoc_server.shape_matches(None, "9:16"))
        self.assertIsNone(adhoc_server.shape_matches((1024, 1024), "3:2"))
        self.assertIsNone(adhoc_server.shape_matches((0, 100), "1:1"))

    def test_every_brand_aspect_has_a_ratio(self):
        for aspect in ("1:1", "9:16", "16:9"):
            self.assertTrue(adhoc_server.acceptable_ratios(aspect), aspect)

    def test_image_size_survives_a_corrupt_file(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        broken = tmp / "broken.png"
        broken.write_bytes(b"not an image")
        self.assertIsNone(adhoc_server.image_size(broken))


class PathGuardTests(unittest.TestCase):
    def test_rejects_queue_names_that_are_not_plain_json_files(self):
        for name in ("../secrets.json", "sub/dir.json", "queue.txt", "",
                     "..\\escape.json", "/etc/passwd"):
            with self.assertRaises(ValueError, msg=name):
                adhoc_server.safe_queue_path(name)

    def test_rejects_a_well_formed_name_that_does_not_exist(self):
        with self.assertRaises(ValueError):
            adhoc_server.safe_queue_path("definitely-not-here.json")

    def test_rejects_unknown_media_roots_and_traversal(self):
        for kind, name in (("generated", "../../plan.md"),
                           ("generated", "sub/x.png"),
                           ("bogus", "x.png"),
                           ("unassigned", "..\\..\\x.png")):
            with self.assertRaises(ValueError, msg=f"{kind}/{name}"):
                adhoc_server.safe_media_path(kind, name)


class QueuePlanTests(unittest.TestCase):
    """Staging, matching and assignment against a throwaway inbox."""

    POSTS = [
        {
            "id": "utest-story", "account": "layer8culture", "platform": "instagram",
            "format": "story", "text": "t", "hashtags": [],
            "schedule_time": "2026-08-19T09:00:00-04:00",
            "visual": {"source": "openai", "aspect": "9:16", "headline": "H",
                       "openai_prompt": "p"},
        },
        {
            "id": "utest-square", "account": "layer8culture", "platform": "instagram",
            "format": "single", "text": "t", "hashtags": [],
            "schedule_time": "2026-08-19T10:00:00-04:00",
            "visual": {"source": "openai", "aspect": "1:1", "headline": "H",
                       "openai_prompt": "p"},
        },
    ]

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inbox = self.tmp / "manual-inbox"
        self.out = self.tmp / "generated"
        self.inbox.mkdir(parents=True)
        self.out.mkdir(parents=True)

        for attr, value in (("INBOX_DIR", self.inbox), ("OUT_DIR", self.out)):
            original = getattr(adhoc_server, attr)
            setattr(adhoc_server, attr, value)
            self.addCleanup(setattr, adhoc_server, attr, original)

        self.specs = adhoc_server.plan_for(self.POSTS)

    def stage(self, name: str, size: tuple[int, int]) -> pathlib.Path:
        staged = adhoc_server.unassigned_dir(create=True) / name
        staged.write_bytes(png_bytes(*size))
        return staged

    def test_plan_covers_every_post(self):
        self.assertEqual([s.image_id for s in self.specs],
                         ["utest-story", "utest-square"])

    def test_auto_reconcile_accepts_a_correct_name_and_shape(self):
        self.stage("utest-story.png", (1024, 1536))
        moved = adhoc_server.auto_reconcile(self.specs)
        self.assertEqual(moved, [{"image_id": "utest-story",
                                  "file": "utest-story.png"}])
        self.assertTrue((self.inbox / "utest-story.png").is_file())
        self.assertEqual(adhoc_server.list_unassigned(), [])

    def test_auto_reconcile_matches_case_insensitively(self):
        self.stage("UTEST-SQUARE.PNG", (1024, 1024))
        moved = adhoc_server.auto_reconcile(self.specs)
        self.assertEqual([m["image_id"] for m in moved], ["utest-square"])
        self.assertTrue((self.inbox / "utest-square.png").is_file())

    def test_a_right_name_with_the_wrong_shape_stays_staged_as_suspect(self):
        # The exact failure that a positional/name-only mapping missed before.
        self.stage("utest-square.png", (1024, 1536))
        moved = adhoc_server.auto_reconcile(self.specs)
        self.assertEqual(moved, [])
        staged = adhoc_server.staged_payload(self.specs)
        self.assertEqual([s["reason"] for s in staged], ["suspect"])
        self.assertEqual(staged[0]["name_matches"], "utest-square")

    def test_an_unrecognised_name_stays_staged_as_unmatched(self):
        self.stage("Generated_image_4.png", (1024, 1536))
        self.assertEqual(adhoc_server.auto_reconcile(self.specs), [])
        staged = adhoc_server.staged_payload(self.specs)
        self.assertEqual([s["reason"] for s in staged], ["unmatched"])
        self.assertIsNone(staged[0]["name_matches"])

    def test_assigning_a_staged_file_fills_the_slot(self):
        self.stage("Generated_image_4.png", (1024, 1536))
        result = adhoc_server.assign_staged(
            self.specs, "Generated_image_4.png", "utest-story")
        self.assertEqual(result["image_id"], "utest-story")
        self.assertTrue((self.inbox / "utest-story.png").is_file())
        self.assertEqual(adhoc_server.list_unassigned(), [])
        self.assertEqual(adhoc_server.spec_status(self.specs[0])[0], "ready")

    def test_assignment_rejects_bad_input(self):
        self.stage("loose.png", (1024, 1536))
        for filename, image_id in (("../../plan.md", "utest-story"),
                                   ("_unassigned/loose.png", "utest-story"),
                                   ("missing.png", "utest-story"),
                                   ("loose.png", "not-a-real-image")):
            with self.assertRaises(ValueError, msg=f"{filename}->{image_id}"):
                adhoc_server.assign_staged(self.specs, filename, image_id)

    def test_assignment_refuses_a_slot_that_is_already_filled(self):
        (self.inbox / "utest-story.png").write_bytes(png_bytes(1024, 1536))
        self.stage("loose.png", (1024, 1536))
        with self.assertRaises(ValueError):
            adhoc_server.assign_staged(self.specs, "loose.png", "utest-story")

    def test_status_reports_done_once_the_output_exists(self):
        (self.out / "utest-story.png").write_bytes(png_bytes(1152, 2048))
        self.assertEqual(adhoc_server.spec_status(self.specs[0])[0], "done")


class BatchPromptTests(unittest.TestCase):
    def setUp(self):
        self.specs = adhoc_server.plan_for(QueuePlanTests.POSTS)

    def test_names_every_file_and_asks_for_one_zip(self):
        text = adhoc_server.batch_prompt(self.specs)
        self.assertIn("single .zip", text)
        self.assertIn("utest-story.png", text)
        self.assertIn("utest-square.png", text)
        self.assertIn("IMAGE 1 of 2", text)
        self.assertIn("IMAGE 2 of 2", text)

    def test_carries_the_canvas_and_the_no_text_rule(self):
        text = adhoc_server.batch_prompt(self.specs)
        self.assertIn(manual_media.ASPECT_SIZE["9:16"], text)
        self.assertIn(manual_media.ASPECT_SIZE["1:1"], text)
        self.assertIn("no text", text.lower())

    def test_can_be_narrowed_to_the_outstanding_images(self):
        text = adhoc_server.batch_prompt(self.specs, only=["utest-square"])
        self.assertIn("utest-square.png", text)
        self.assertNotIn("utest-story.png", text)
        self.assertIn("IMAGE 1 of 1", text)

    def test_says_so_when_there_is_nothing_to_do(self):
        self.assertIn("Nothing to generate",
                      adhoc_server.batch_prompt(self.specs, only=[]))


class LaneConfigTests(unittest.TestCase):
    """Lane resolution, dates and the argv handed to the Copilot CLI."""

    def test_rejects_an_unknown_lane(self):
        with self.assertRaises(ValueError):
            adhoc_server.lane_config("weekly-guide")

    def test_default_dates_match_each_workflows_own_choice(self):
        today = dt.datetime.now(adhoc_server.TZ).date()
        self.assertEqual(adhoc_server.default_date("layer8culture"),
                         (today + dt.timedelta(days=1)).isoformat())
        self.assertEqual(adhoc_server.default_date("lofi"), today.isoformat())
        self.assertEqual(adhoc_server.default_date("deallab"), today.isoformat())

    def test_queue_names_carry_the_lane_prefix(self):
        self.assertEqual(adhoc_server.queue_name_for("layer8culture", "2026-08-19"),
                         "2026-08-19.json")
        self.assertEqual(adhoc_server.queue_name_for("lofi", "2026-08-19"),
                         "lofi-2026-08-19.json")
        self.assertEqual(adhoc_server.queue_name_for("deallab", "2026-08-19"),
                         "deallab-2026-08-19.json")

    def test_validate_date_accepts_an_iso_date(self):
        self.assertEqual(adhoc_server.validate_date("2026-08-19"), "2026-08-19")

    def test_validate_date_rejects_junk_and_impossible_days(self):
        for bad in ("", "19-08-2026", "2026-8-9", "tomorrow", "2026-02-30",
                    "2026-13-01", "../../etc"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                adhoc_server.validate_date(bad)

    def test_date_override_names_the_date_and_the_target_file(self):
        text = adhoc_server.date_override("lofi", "2026-08-19")
        self.assertIn("2026-08-19", text)
        self.assertIn("queue/lofi-2026-08-19.json", text)
        self.assertIn("HIGHEST PRIORITY", text)

    def test_command_mirrors_the_layer8culture_workflow_flags(self):
        cmd = adhoc_server.generation_command(
            "layer8culture", "2026-08-19", ["copilot"], "BODY")
        self.assertEqual(cmd[0], "copilot")
        self.assertEqual(cmd[1], "-p")
        self.assertIn("-s", cmd)
        self.assertIn("--allow-tool=read,write(queue/*),web-fetch", cmd)
        self.assertIn("--allow-all-urls", cmd)
        self.assertEqual(cmd[-1], "--no-ask-user")

    def test_command_keeps_the_narrow_lanes_narrow(self):
        for lane in ("lofi", "deallab"):
            cmd = adhoc_server.generation_command(
                lane, "2026-08-19", ["copilot"], "BODY")
            with self.subTest(lane=lane):
                self.assertIn("--allow-tool=read,write(queue/*)", cmd)
                self.assertNotIn("--allow-all-urls", cmd)

    def test_prompt_is_a_single_argv_element_carrying_the_override(self):
        # It is thousands of characters of quotes, backticks and newlines. If it
        # were ever split or shell-joined the run would silently misbehave.
        cmd = adhoc_server.generation_command(
            "layer8culture", "2026-08-19", ["node", "loader.js"], "BODY")
        self.assertEqual(cmd[:2], ["node", "loader.js"])
        prompt = cmd[cmd.index("-p") + 1]
        self.assertTrue(prompt.startswith("BODY"))
        self.assertIn("queue/2026-08-19.json", prompt)
        self.assertEqual(sum(1 for part in cmd if part.startswith("BODY")), 1)

    def test_lanes_payload_reports_each_prompt_file(self):
        payload = adhoc_server.lanes_payload()
        self.assertEqual([entry["lane"] for entry in payload],
                         ["layer8culture", "lofi", "deallab"])
        for entry in payload:
            with self.subTest(lane=entry["lane"]):
                self.assertTrue(entry["prompt_ok"], entry["prompt"])
                self.assertTrue(entry["queue_name"].endswith(".json"))


class CopilotCommandTests(unittest.TestCase):
    """Resolving a CLI that actually receives the prompt intact."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def override(self, path):
        patcher = mock.patch.dict(
            os.environ, {adhoc_server.COPILOT_ENV: str(path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejects_a_shell_wrapper(self):
        # A .bat/.ps1 shim forwards argv through cmd.exe and PowerShell, which
        # empties a multi-line prompt. Failing loudly beats generating nothing.
        for suffix in (".bat", ".cmd", ".ps1"):
            shim = self.tmp / f"copilot{suffix}"
            shim.write_text("echo hi", encoding="utf-8")
            self.override(shim)
            with self.subTest(suffix=suffix), self.assertRaises(ValueError) as ctx:
                adhoc_server.copilot_command()
            self.assertIn("wrapper", str(ctx.exception))

    def test_rejects_a_path_that_is_not_there(self):
        self.override(self.tmp / "nope.exe")
        with self.assertRaises(ValueError):
            adhoc_server.copilot_command()

    def test_accepts_a_native_executable(self):
        exe = self.tmp / "copilot.exe"
        exe.write_bytes(b"MZ")
        self.override(exe)
        self.assertEqual(adhoc_server.copilot_command(), [str(exe)])

    def test_runs_a_js_entry_through_node(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        entry = self.tmp / "npm-loader.js"
        entry.write_text("// entry", encoding="utf-8")
        self.override(entry)
        self.assertEqual(adhoc_server.copilot_command()[1], str(entry))


class TrashTests(unittest.TestCase):
    """Removing posts and queue files, and what must survive it."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.queue = self.tmp / "queue"
        self.out = self.tmp / "generated"
        self.queue.mkdir()
        self.out.mkdir()
        for attr, value in (("REPO_ROOT", self.tmp), ("QUEUE_DIR", self.queue),
                            ("OUT_DIR", self.out),
                            ("TRASH_DIR", self.tmp / ".trash")):
            original = getattr(adhoc_server, attr)
            setattr(adhoc_server, attr, value)
            self.addCleanup(setattr, adhoc_server, attr, original)

    def post(self, post_id, fmt="single", **visual):
        base = {"source": "openai", "aspect": "9:16", "headline": "H",
                "openai_prompt": "p"}
        base.update(visual)
        return {"id": post_id, "account": "layer8culture", "platform": "instagram",
                "format": fmt, "text": "t", "hashtags": [],
                "schedule_time": "2026-08-19T09:00:00-04:00", "visual": base}

    def media(self, *names):
        for name in names:
            (self.out / name).write_bytes(b"x")

    def write_queue(self, name, posts):
        path = self.queue / name
        path.write_text(json.dumps(posts, indent=2), encoding="utf-8")
        return path

    # -- resolving a post's own files ------------------------------------
    def test_single_post_owns_one_still(self):
        self.assertEqual(adhoc_server.post_media_names(self.post("utest-a")),
                         {"utest-a.png"})

    def test_reel_owns_its_video_and_cover(self):
        names = adhoc_server.post_media_names(self.post("utest-r", fmt="reel"))
        self.assertIn("utest-r.mp4", names)
        self.assertIn("utest-r-cover.png", names)

    def test_recorded_paths_are_reduced_to_basenames(self):
        post = self.post("utest-v", file="assets/generated/utest-v.mp4",
                         cover="assets/generated/utest-v-cover.png")
        names = adhoc_server.post_media_names(post)
        self.assertIn("utest-v.mp4", names)
        self.assertIn("utest-v-cover.png", names)

    def test_a_shared_file_is_kept_while_another_post_still_uses_it(self):
        # The cross-post case: reel_gen copies the TikTok master's video to the
        # Instagram post's own id, but the queue still records the master's path.
        # Deleting either one must never strip the other of its video.
        master = self.post("utest-master", fmt="reel")
        crosspost = self.post("utest-copy", fmt="reel", source="reuse",
                              of="utest-master",
                              file="assets/generated/utest-master.mp4")
        self.media("utest-master.mp4", "utest-master-cover.png",
                   "utest-copy.mp4", "utest-copy-cover.png")

        doomed = adhoc_server.media_to_trash([crosspost], [master])
        self.assertEqual([p.name for p in doomed],
                         ["utest-copy-cover.png", "utest-copy.mp4"])

        doomed = adhoc_server.media_to_trash([master], [crosspost])
        self.assertNotIn("utest-master.mp4", [p.name for p in doomed])
        self.assertIn("utest-master-cover.png", [p.name for p in doomed])

    def test_only_files_that_exist_are_listed(self):
        self.assertEqual(adhoc_server.media_to_trash([self.post("utest-gone")], []), [])

    # -- deleting one post -----------------------------------------------
    def test_delete_post_rewrites_the_queue_and_banks_the_media(self):
        qpath = self.write_queue(
            "2026-08-19.json", [self.post("utest-a"), self.post("utest-b")])
        self.media("utest-a.png", "utest-b.png")

        result = adhoc_server.delete_post(qpath, "utest-a")

        self.assertEqual([p["id"] for p in json.loads(qpath.read_text())],
                         ["utest-b"])
        self.assertFalse((self.out / "utest-a.png").exists())
        self.assertTrue((self.out / "utest-b.png").is_file())

        dest = self.tmp / result["trash"]
        self.assertTrue((dest / "utest-a.png").is_file())
        self.assertEqual(
            [p["id"] for p in json.loads((dest / "post.json").read_text())],
            ["utest-a"])
        manifest = json.loads((dest / "manifest.json").read_text())
        self.assertEqual(manifest["kind"], "post")
        self.assertEqual(manifest["post_id"], "utest-a")

    def test_delete_post_flags_a_summary_it_cannot_rewrite(self):
        qpath = self.write_queue("2026-08-19.json", [self.post("utest-a"),
                                                     self.post("utest-b")])
        self.assertFalse(adhoc_server.delete_post(qpath, "utest-a")["summary_stale"])

        qpath = self.write_queue("2026-08-20.json", [self.post("utest-c"),
                                                     self.post("utest-d")])
        (self.queue / "2026-08-20.summary.md").write_text("narrative",
                                                          encoding="utf-8")
        self.assertTrue(adhoc_server.delete_post(qpath, "utest-c")["summary_stale"])

    def test_delete_post_refuses_an_id_that_is_not_there(self):
        qpath = self.write_queue("2026-08-19.json", [self.post("utest-a")])
        for bad in ("", "utest-zz"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                adhoc_server.delete_post(qpath, bad)

    # -- deleting a whole queue ------------------------------------------
    def test_delete_queue_takes_its_siblings_and_media_with_it(self):
        qpath = self.write_queue("2026-08-19.json", [self.post("utest-a"),
                                                     self.post("utest-b")])
        (self.queue / "2026-08-19.summary.md").write_text("s", encoding="utf-8")
        (self.queue / "2026-08-19.prompts.md").write_text("p", encoding="utf-8")
        self.media("utest-a.png", "utest-b.png", "unrelated.png")

        result = adhoc_server.delete_queue(qpath)

        self.assertFalse(qpath.exists())
        self.assertFalse((self.queue / "2026-08-19.summary.md").exists())
        self.assertTrue((self.out / "unrelated.png").is_file())

        dest = self.tmp / result["trash"]
        for name in ("2026-08-19.json", "2026-08-19.summary.md",
                     "2026-08-19.prompts.md", "utest-a.png", "utest-b.png"):
            with self.subTest(name=name):
                self.assertTrue((dest / name).is_file())
        self.assertEqual(json.loads((dest / "manifest.json").read_text())["posts"], 2)

    def test_a_corrupt_queue_file_is_still_removable(self):
        qpath = self.queue / "2026-08-19.json"
        qpath.write_text("{not json", encoding="utf-8")
        result = adhoc_server.delete_queue(qpath)
        self.assertFalse(qpath.exists())
        self.assertEqual(result["posts"], 0)
        self.assertTrue((self.tmp / result["trash"] / "2026-08-19.json").is_file())

    def test_two_removals_in_the_same_second_do_not_collide(self):
        first = adhoc_server.new_trash_dir("2026-08-19")
        second = adhoc_server.new_trash_dir("2026-08-19")
        self.assertNotEqual(first, second)
        self.assertTrue(second.is_dir())

    def test_queue_row_reports_whether_a_summary_exists(self):
        # The PR command block copies the summary only when there is one --
        # naming a file that isn't there fails the copy and pushes an empty branch.
        qpath = self.write_queue("2026-08-19.json", [self.post("utest-a")])
        self.assertFalse(adhoc_server.queue_summary(qpath)["summary"])
        (self.queue / "2026-08-19.summary.md").write_text("s", encoding="utf-8")
        self.assertTrue(adhoc_server.queue_summary(qpath)["summary"])


if __name__ == "__main__":
    unittest.main()


class BrandFontRouteTests(unittest.TestCase):
    """The /fonts/ route, which self-hosts the two brand typefaces.

    Runs a real server on an ephemeral port: the allowlist is the whole security
    boundary for this route, so it is worth exercising over HTTP rather than by
    calling the handler directly.
    """

    @classmethod
    def setUpClass(cls):
        adhoc_server.Handler.quiet = True
        mimetypes.add_type("font/ttf", ".ttf")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), adhoc_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def get(self, path):
        return urllib.request.urlopen(f"{self.base}{path}", timeout=10)

    def test_every_allowlisted_font_is_served(self):
        for url_name in adhoc_server.BRAND_FONTS:
            with self.subTest(font=url_name):
                res = self.get(f"/fonts/{url_name}")
                self.assertEqual(res.status, 200)
                self.assertIn("font", res.headers["Content-Type"])
                # A TrueType file starts with 0x00010000 or "true"/"ttcf".
                self.assertTrue(res.read(4) in (b"\x00\x01\x00\x00", b"true", b"ttcf"))

    def test_allowlist_points_at_files_that_exist(self):
        # A font rename would otherwise only show up as silent fallback type.
        for source in adhoc_server.BRAND_FONTS.values():
            with self.subTest(font=source):
                self.assertTrue((adhoc_server.FONTS_DIR / source).is_file())

    def test_unknown_font_name_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/fonts/helvetica.ttf")
        self.assertEqual(ctx.exception.code, 404)

    def test_route_cannot_be_used_to_read_other_repo_files(self):
        # The allowlist is a dict lookup, so a traversal string is simply not a
        # key -- but assert it, because a switch to a path join would regress it.
        for probe in ("../../scripts/adhoc_server.py", "..%2f..%2fREADME.md",
                      "Inter-Variable.ttf", "space-grotesk.ttf/../inter.ttf"):
            with self.subTest(probe=probe):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.get(f"/fonts/{probe}")
                self.assertIn(ctx.exception.code, (400, 404))

    def test_verify_layout_requires_the_brand_fonts(self):
        empty = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, True)
        with mock.patch.object(adhoc_server, "FONTS_DIR", empty):
            with self.assertRaises(SystemExit) as ctx:
                adhoc_server.verify_layout()
        for source in adhoc_server.BRAND_FONTS.values():
            self.assertIn(source, str(ctx.exception))


class PublishJobTests(unittest.TestCase):
    """Step 6 publishes straight to main, so the wiring gets its own tests.

    The real push is covered by tests/test_ship_queue.py against a throwaway
    remote; here the concern is only that the route reaches the right script
    with the right flags, and that a dry run genuinely cannot push.
    """

    def qpath(self):
        return adhoc_server.QUEUE_DIR / "2026-08-20.json"

    def spawn_args(self, *args, **kwargs):
        with mock.patch.object(adhoc_server, "start_command_job") as spawn:
            spawn.return_value = "jobid"
            adhoc_server.start_job(*args, **kwargs)
        return spawn.call_args

    def test_publish_runs_ship_queue_against_the_chosen_queue(self):
        call = self.spawn_args("publish", self.qpath())
        kind, command = call.args[0], call.args[1]

        self.assertEqual(kind, "publish")
        self.assertTrue(command[2].endswith("ship_queue.py"))
        self.assertEqual(command[3], "queue/2026-08-20.json")
        self.assertNotIn("--dry-run", command)

    def test_a_check_passes_dry_run_so_nothing_can_be_pushed(self):
        call = self.spawn_args("publish", self.qpath(), extra=["--dry-run"])

        self.assertEqual(call.args[1][-1], "--dry-run")
        self.assertIn("--dry-run", call.kwargs["display"])

    def test_ingest_and_reels_are_unchanged_by_the_extra_argument(self):
        call = self.spawn_args("ingest", self.qpath())

        self.assertTrue(call.args[1][2].endswith("manual_media_ingest.py"))
        self.assertEqual(len(call.args[1]), 4)

    def test_verify_layout_requires_the_publish_script(self):
        empty = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, True)
        with mock.patch.object(adhoc_server, "SCRIPTS_DIR", empty):
            with self.assertRaises(SystemExit) as ctx:
                adhoc_server.verify_layout()
        self.assertIn("ship_queue.py", str(ctx.exception))

class PublishRouteTests(unittest.TestCase):
    """Mutations require a current revision and local session; publishing needs a PR."""

    @classmethod
    def setUpClass(cls):
        adhoc_server.Handler.quiet = True
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), adhoc_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        queue = root / "queue"
        queue.mkdir()
        for name, value in (("REPO_ROOT", root), ("QUEUE_DIR", queue),
                            ("OUT_DIR", root / "assets" / "generated"),
                            ("INBOX_DIR", root / "assets" / "manual-inbox"),
                            ("TRASH_DIR", root / ".trash")):
            patcher = mock.patch.object(adhoc_server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(adhoc_server, "readiness", return_value={
            "revision": "fixture-revision", "ready": False, "media_ready": False,
            "schedule_ready": False, "blockers": ["Missing media"], "warnings": [],
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.name = "zz-publish-route-test.json"
        self.path = adhoc_server.QUEUE_DIR / self.name
        self.path.write_text(json.dumps([{
            "id": "20260820-layer8culture-instagram-1",
            "account": "layer8culture",
            "platform": "instagram",
            "format": "single",
            "schedule_time": "2026-08-20T09:00:00-04:00",
            "text": "Caption",
            "visual": {"source": "openai", "openai_prompt": "a scene"},
        }]), encoding="utf-8")
        self.token = json.loads(urllib.request.urlopen(self.base + "/api/session").read())["csrf"]

    def post(self, action, headers=None):
        request_headers = {"X-Layer8-CSRF": self.token, "If-Match": "fixture-revision"}
        request_headers.update(headers or {})
        req = urllib.request.Request(
            f"{self.base}/api/queue/{self.name}/{action}", data=b"{}",
            headers=request_headers, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def test_direct_publish_is_disabled(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("publish")
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("Direct publishing is disabled", ctx.exception.read().decode())

    def test_publish_check_reports_readiness_without_starting_job(self):
        with mock.patch.object(adhoc_server, "start_guided_job") as spawn:
            body = self.post("publish-check")
        spawn.assert_not_called()
        self.assertFalse(body["readiness"]["ready"])

    def test_missing_session_is_forbidden(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("publish-check", {"X-Layer8-CSRF": ""})
        self.assertEqual(ctx.exception.code, 403)

    def test_foreign_origin_is_forbidden(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("publish-check", {"Origin": "https://example.com"})
        self.assertEqual(ctx.exception.code, 403)

    def test_stale_revision_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("delete", {"If-Match": "old-version"})
        self.assertEqual(ctx.exception.code, 409)
        self.assertTrue(self.path.exists())

    def test_an_unknown_action_is_still_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("publish-now")
        self.assertEqual(ctx.exception.code, 404)