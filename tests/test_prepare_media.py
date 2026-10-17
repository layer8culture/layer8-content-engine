import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import manual_media
import manual_media_ingest
import openai_gen
import prepare_media
import reel_gen


def image_post(post_id="p1", fmt="single"):
    return {"id": post_id, "format": fmt, "account": "layer8culture",
            "visual": {"source": "openai", "openai_prompt": "test scene"}}


def reuse_post(post_id="copy", source="p1"):
    return {"id": post_id, "format": "reel",
            "visual": {"source": "reuse", "of": source}}


class PrepareTests(unittest.TestCase):
    def setUp(self):
        fixtures = ROOT / ".local" / "media-test-fixtures"
        fixtures.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=fixtures)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / manual_media.DEFAULT_INBOX
        self.out = self.root / manual_media.DEFAULT_OUT_DIR
        self.inbox.mkdir(parents=True)
        self.out.mkdir(parents=True)
        self.queue = self.root / "queue" / "fixture.json"
        self.queue.parent.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(openai_gen, "IMAGE_LONG_EDGE", 64).start()
        patch.object(openai_gen, "IMAGE_2K", True).start()
        self.real_validate_video = reel_gen.validate_video
        self.real_generate_sora = reel_gen.generate_sora
        patch.object(reel_gen, "validate_video").start()
        patch.object(reel_gen, "ffmpeg_available", return_value=True).start()
        self.sora = patch.object(reel_gen, "generate_sora",
                                 side_effect=AssertionError("live Sora is forbidden")).start()

    def write_posts(self, posts):
        self.queue.write_text(json.dumps(posts), encoding="utf-8")

    def posts(self):
        return json.loads(self.queue.read_text(encoding="utf-8"))

    def drop(self, image_id="p1", color="gray"):
        path = self.inbox / f"{image_id}.png"
        Image.new("RGB", (64, 64), color).save(path)
        return path

    def run_prepare(self):
        return prepare_media.prepare(self.queue, self.root)

    def motion(self, post, out, repo_root):
        post_id = post["id"]
        video, cover = out / f"{post_id}.mp4", out / f"{post_id}-cover.png"
        video.write_bytes(b"fixture video:" + (out / f"{post_id}.png").read_bytes())
        Image.new("RGB", (16, 16), "gray").save(cover)
        return str(video), str(cover)

    def test_image_only_skips_video_and_noop_preserves_output_and_queue_mtime(self):
        self.write_posts([image_post()])
        self.drop()
        with patch.object(reel_gen, "generate") as render:
            first = self.run_prepare()
            paths = [self.queue, self.out / "p1.png"]
            before = {p: p.stat().st_mtime_ns for p in paths}
            second = self.run_prepare()
        render.assert_not_called()
        self.assertEqual([], first["failed"])
        self.assertEqual(1, first["prepared"])
        self.assertEqual(0, second["prepared"])
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in paths})

    def test_successful_reel_and_reuse_are_preserved_on_noop(self):
        self.write_posts([reuse_post(), image_post(fmt="reel")])
        self.drop()
        with patch.object(reel_gen, "generate_motion", side_effect=self.motion) as render:
            first = self.run_prepare()
            paths = [self.queue, *self.out.glob("*")]
            before = {p: p.stat().st_mtime_ns for p in paths}
            second = self.run_prepare()
        self.assertEqual([], first["failed"])
        self.assertEqual([], second["failed"])
        self.assertEqual(1, render.call_count)
        self.assertEqual(0, second["prepared"])
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in paths})
        self.assertTrue(self.posts()[1]["visual"]["file"].endswith(".mp4"))
        self.assertEqual([], prepare_media.preparation_status(self.posts(), self.root)["blockers"])
        self.assertTrue(any("silent audio" in w for w in second["warnings"]))
        self.sora.assert_not_called()

    def test_fresh_ci_api_outputs_do_not_require_manual_provenance(self):
        self.write_posts([image_post(fmt="reel"), reuse_post()])
        Image.new("RGB", (64, 64), "gray").save(self.out / "p1.png")
        with patch.object(reel_gen, "generate_sora", side_effect=self.motion):
            result = prepare_media.prepare_videos(self.queue, self.root, offline=False)
        self.assertEqual([], result["failed"])
        self.assertEqual({}, manual_media.read_record(self.root, "image:p1"))
        self.assertEqual([], prepare_media.preparation_status(self.posts(), self.root)["blockers"])

    def test_clean_export_of_reviewed_manifest_is_ready_without_local_provenance(self):
        import batch_readiness
        if __package__:
            from .test_ship_queue import post
        else:
            from test_ship_queue import post

        entry = post(fmt="carousel")
        qpath = self.queue.with_name("2030-09-05.json")
        qpath.write_text(json.dumps([entry]), encoding="utf-8")
        for name in entry["visual"]["files"]:
            Image.new("RGB", (32, 32), "blue").save(self.root / name)
        now = datetime.fromisoformat("2030-09-05T08:00:00-04:00")
        reviewed = batch_readiness.report(qpath, self.root, now=now)
        self.assertTrue(reviewed["ready"], reviewed["blockers"])

        exported = self.root / "export"
        exported_queue = exported / "queue" / qpath.name
        exported_queue.parent.mkdir(parents=True)
        shutil.copyfile(qpath, exported_queue)
        for artifact in reviewed["manifest"]:
            destination = exported / artifact["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / artifact["path"], destination)

        manual_media.write_record(self.root, f"image:{entry['id']}-1", {
            "post_id": entry["id"], "status": "stale",
        })
        local = batch_readiness.report(qpath, self.root, now=now)
        self.assertFalse(local["media_ready"])
        clean_ci = batch_readiness.report(exported_queue, exported, now=now)
        self.assertTrue(clean_ci["ready"], clean_ci["blockers"])
        self.assertEqual(reviewed["revision"], clean_ci["revision"])
        self.assertEqual(reviewed["manifest"], clean_ci["manifest"])
        self.assertFalse((exported / ".local").exists())
        self.assertFalse((exported / manual_media.DEFAULT_INBOX).exists())

    def test_ci_text_only_sora_remains_supported_without_a_manual_still(self):
        post = image_post(fmt="reel")
        post["visual"] = {"source": "openai", "reel": {"sora_prompt": "test cinematic"}}
        self.write_posts([post])

        def sora_fixture(post, out, root):
            video, cover = out / "p1.mp4", out / "p1-cover.png"
            video.write_bytes(b"fixture text-only video")
            Image.new("RGB", (16, 16), "gray").save(cover)
            return str(video), str(cover)

        with patch.object(reel_gen, "generate_sora", side_effect=sora_fixture), \
                patch.object(reel_gen, "generate_motion") as motion:
            result = prepare_media.prepare_videos(self.queue, self.root, offline=False)
        motion.assert_not_called()
        self.assertEqual([], result["failed"])
        self.assertEqual([], prepare_media.preparation_status(self.posts(), self.root)["blockers"])

    def test_replacement_refreshes_stale_copies_and_preserves_versions(self):
        self.write_posts([reuse_post("second", "copy"), reuse_post(), image_post(fmt="reel")])
        self.drop(color="gray")
        with patch.object(reel_gen, "generate_motion", side_effect=self.motion) as render:
            self.assertEqual([], self.run_prepare()["failed"])
            before = (self.out / "copy.mp4").read_bytes()
            self.drop(color="red")
            self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])
            self.assertEqual([], self.run_prepare()["failed"])
        self.assertEqual(2, render.call_count)
        self.assertNotEqual(before, (self.out / "copy.mp4").read_bytes())
        self.assertEqual((self.out / "p1.mp4").read_bytes(), (self.out / "second.mp4").read_bytes())
        versions = self.inbox / "_ingested" / "_versions" / "p1"
        self.assertEqual(2, len(list(versions.glob("*.png"))))
        old_output = self.root / ".local" / "media" / "outputs" / "copy"
        self.assertTrue(list(old_output.glob("*.mp4")))

    def test_changed_source_extension_keeps_new_original_selected_on_resume(self):
        self.write_posts([image_post()])
        self.drop(color="gray")
        self.run_prepare()
        Image.new("RGB", (64, 64), "red").save(self.inbox / "p1.jpg")
        self.assertEqual(1, self.run_prepare()["prepared"])
        before = (self.out / "p1.png").read_bytes()
        self.assertEqual(0, self.run_prepare()["prepared"])
        self.assertEqual(before, (self.out / "p1.png").read_bytes())
        record = manual_media.read_record(self.root, "image:p1")
        self.assertTrue(record["source_path"].endswith(".jpg"))

    def test_reel_setting_change_preserves_still_but_refreshes_video(self):
        self.write_posts([image_post(fmt="reel"), reuse_post()])
        self.drop()
        with patch.object(reel_gen, "generate_motion", side_effect=self.motion):
            self.run_prepare()
            before = (self.out / "p1.png").stat().st_mtime_ns
            posts = self.posts()
            posts[0]["visual"]["reel"] = {"duration_sec": 7}
            self.write_posts(posts)
            report = self.run_prepare()
        self.assertEqual([], report["failed"])
        self.assertEqual(0, report["images_prepared"])
        self.assertEqual(2, report["videos_prepared"])
        self.assertEqual(before, (self.out / "p1.png").stat().st_mtime_ns)

    def test_failure_resumes_only_unfinished_video_and_never_copies_old_master(self):
        self.write_posts([image_post(fmt="reel"), reuse_post()])
        self.drop()
        with patch.object(reel_gen, "generate_motion", side_effect=self.motion):
            self.assertEqual([], self.run_prepare()["failed"])
        old_master = (self.out / "p1.mp4").read_bytes()
        old_copy = (self.out / "copy.mp4").read_bytes()
        self.drop(color="blue")
        with patch.object(reel_gen, "generate_motion", return_value=None), \
                patch.object(reel_gen, "resolve_crosspost") as reuse:
            failure = self.run_prepare()
        reuse.assert_not_called()
        self.assertTrue(failure["failed"])
        self.assertEqual(old_master, (self.out / "p1.mp4").read_bytes())
        self.assertEqual(old_copy, (self.out / "copy.mp4").read_bytes())
        for post in self.posts():
            self.assertNotIn("file", post["visual"])
        still_mtime = (self.out / "p1.png").stat().st_mtime_ns
        with patch.object(reel_gen, "generate_motion", side_effect=self.motion):
            retry = self.run_prepare()
        self.assertEqual([], retry["failed"])
        self.assertEqual(0, retry["images_prepared"])
        self.assertEqual(2, retry["videos_prepared"])
        self.assertEqual(still_mtime, (self.out / "p1.png").stat().st_mtime_ns)

    def test_missing_fonts_block_and_keep_original_for_retry(self):
        post = image_post()
        post["visual"]["headline"] = "REQUIRED"
        self.write_posts([post])
        source = self.drop()
        failure = self.run_prepare()
        self.assertTrue(any("typography" in e for e in failure["failed"]))
        self.assertTrue(source.is_file())
        self.assertFalse((self.out / "p1.png").exists())
        self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])
        fonts = self.root / "assets" / "fonts"
        shutil.copytree(ROOT / "assets" / "fonts", fonts)
        self.assertEqual([], self.run_prepare()["failed"])

    def test_interrupted_image_resumes_from_persisted_original_version(self):
        self.write_posts([image_post()])
        source = self.drop()
        with patch.object(manual_media_ingest, "ingest_image", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_prepare()
        record = manual_media.read_record(self.root, "image:p1")
        self.assertEqual("preparing", record["status"])
        self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])
        source.unlink()
        self.assertEqual([], self.run_prepare()["failed"])

    def test_required_video_font_failure_is_structured_and_blocking(self):
        post = image_post(fmt="reel")
        post["visual"]["reel"] = {"overlay_beats": ["REQUIRED"]}
        self.write_posts([post])
        self.drop()
        result = self.run_prepare()
        self.assertTrue(any("required typography font missing" in e for e in result["failed"]))
        self.assertNotIn("file", self.posts()[0]["visual"])
        self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])

    def test_corrupt_source_fails_even_if_previous_output_exists(self):
        self.write_posts([image_post()])
        self.drop()
        self.assertEqual([], self.run_prepare()["failed"])
        output = self.out / "p1.png"
        before = output.read_bytes()
        corrupt = self.inbox / "p1.png"
        corrupt.write_bytes(b"corrupt")
        result = self.run_prepare()
        self.assertTrue(result["failed"])
        self.assertEqual(before, output.read_bytes())
        self.assertEqual(b"corrupt", corrupt.read_bytes())
        self.assertNotIn("file", self.posts()[0]["visual"])

    def test_settings_edit_recomposites_from_original_and_does_not_invalidate_caption(self):
        self.write_posts([image_post()])
        self.drop()
        self.assertEqual([], self.run_prepare()["failed"])
        post = self.posts()[0]
        post["caption"] = "caption-only edit"
        self.write_posts([post])
        self.assertEqual(0, self.run_prepare()["prepared"])
        post["visual"]["aspect"] = "9:16"
        self.write_posts([post])
        self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])
        self.assertEqual(1, self.run_prepare()["images_prepared"])
        with Image.open(self.out / "p1.png") as image:
            self.assertLess(image.width, image.height)

    def test_darkness_is_persistent_warning_not_failure(self):
        self.write_posts([image_post()])
        self.drop(color="black")
        first = self.run_prepare()
        second = self.run_prepare()
        self.assertEqual([], first["failed"])
        self.assertTrue(any("dark" in w for w in first["warnings"]))
        self.assertEqual(first["warnings"], second["warnings"])

    def test_fingerprints_detect_output_tampering_and_renderer_changes(self):
        self.write_posts([image_post()])
        self.drop()
        self.run_prepare()
        (self.out / "p1.png").write_bytes(b"changed")
        self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])
        self.assertEqual(1, self.run_prepare()["prepared"])
        record = manual_media.read_record(self.root, "image:p1")
        record["inputs"]["renderer"] = "previous renderer"
        manual_media.write_record(self.root, "image:p1", record)
        self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])
        self.assertEqual(1, self.run_prepare()["prepared"])

    def test_readiness_propagates_provenance_permission_errors(self):
        self.write_posts([image_post()])
        with patch.object(manual_media, "read_record", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                prepare_media.preparation_status(self.posts(), self.root)

    def test_readiness_reports_malformed_provenance_as_content_blocker(self):
        self.write_posts([image_post()])
        path = manual_media.record_path(self.root, "image:p1")
        manual_media.atomic_json(path, ["invalid record"])
        result = prepare_media.preparation_status(self.posts(), self.root)
        self.assertTrue(any("Invalid media provenance object" in b for b in result["blockers"]))

    def test_invalidate_is_transitive_and_keeps_media(self):
        self.write_posts([image_post(fmt="reel"), reuse_post()])
        self.drop()
        with patch.object(reel_gen, "generate_motion", side_effect=self.motion):
            self.run_prepare()
        self.assertEqual(["copy", "p1"], prepare_media.invalidate(["p1"], self.root))
        self.assertTrue((self.out / "copy.mp4").is_file())
        self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])

    def test_parent_normalized_replacement_invalidates_even_identical_existing_outputs(self):
        self.write_posts([image_post(fmt="reel"), reuse_post()])
        self.drop()
        with patch.object(reel_gen, "generate_motion", side_effect=self.motion) as render:
            self.assertEqual([], self.run_prepare()["failed"])
            self.drop()  # Parent supplies a normalized original, then invalidates.
            prepare_media.invalidate(["p1"], self.root)
            self.assertTrue((self.out / "p1.mp4").is_file())
            self.assertTrue(prepare_media.preparation_status(self.posts(), self.root)["blockers"])
            result = self.run_prepare()
        self.assertEqual([], result["failed"])
        self.assertEqual(1, result["images_prepared"])
        self.assertEqual(2, result["videos_prepared"])
        self.assertEqual(2, render.call_count)

    def test_partial_carousel_video_failure_does_not_advertise_partial_files(self):
        post = image_post(fmt="carousel")
        post["visual"]["slides"] = [
            {"openai_prompt": "one", "media_type": "video"},
            {"openai_prompt": "two", "media_type": "video"}]
        self.write_posts([post])
        self.drop("p1-1")
        self.drop("p1-2")
        with patch.object(reel_gen, "_render_carousel_video_slide", return_value=None):
            result = self.run_prepare()
        self.assertTrue(result["failed"])
        self.assertNotIn("files", self.posts()[0]["visual"])

    def test_failed_partial_render_preserves_complete_previous_outputs(self):
        post = image_post(fmt="reel")
        Image.new("RGB", (12, 12), "gray").save(self.out / "p1.png")
        (self.out / "p1.mp4").write_bytes(b"previous")
        Image.new("RGB", (12, 12), "red").save(self.out / "p1-cover.png")
        before = (self.out / "p1-cover.png").read_bytes()

        def fail(post, out, root):
            (out / "p1.mp4").write_bytes(b"partial")
            return None

        with patch.object(reel_gen, "generate_motion", side_effect=fail):
            self.assertIsNone(reel_gen.generate(post, self.out, offline=True, repo_root=self.root))
        self.assertEqual(b"previous", (self.out / "p1.mp4").read_bytes())
        self.assertEqual(before, (self.out / "p1-cover.png").read_bytes())
        self.assertEqual([], list(self.out.glob(".render-*")))

    def test_missing_required_overlay_font_never_falls_back_to_clean_video(self):
        post = image_post(fmt="reel")
        post["visual"]["reel"] = {"overlay_beats": ["REQUIRED"]}
        Image.new("RGB", (12, 12), "gray").save(self.out / "p1.png")
        with patch.object(reel_gen, "run_ffmpeg") as ffmpeg:
            self.assertIsNone(reel_gen.generate(post, self.out, offline=True, repo_root=self.root))
        ffmpeg.assert_not_called()
        self.assertFalse((self.out / "p1.mp4").exists())

    def test_cli_writes_failure_report_and_exits_nonzero_for_corrupt_source(self):
        self.write_posts([image_post()])
        (self.inbox / "p1.png").write_bytes(b"corrupt")
        report = self.root / "report.json"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "prepare_media.py"), str(self.queue),
             "--repo-root", str(self.root), "--json", str(report)],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertTrue(json.loads(report.read_text())["failed"])

    def test_cli_uses_clean_code_with_explicit_separate_data_root(self):
        self.write_posts([image_post()])
        self.drop()
        report = self.root / "report.json"
        self.assertFalse((self.root / "scripts").exists())
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "prepare_media.py"),
             str(Path("queue") / self.queue.name),
             "--repo-root", str(self.root), "--json", str(report)],
            capture_output=True, text=True, cwd=ROOT,
            env={**os.environ, "OPENAI_IMAGE_LONG_EDGE": "64"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        outcome = json.loads(report.read_text())
        self.assertEqual([], outcome["failed"])
        self.assertEqual(1, outcome["images_prepared"])
        self.assertEqual("assets/generated/p1.png", self.posts()[0]["visual"]["file"])
        self.assertTrue((self.out / "p1.png").is_file())
        self.assertFalse((self.root / "scripts").exists())

    def test_readiness_imports_on_pillow_only_install(self):
        program = (
            "import sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); "
            "sys.modules.update({'requests': None, 'openai': None}); "
            "import prepare_media; "
            "assert prepare_media.preparation_status([], Path(sys.argv[2])) "
            "== {'blockers': [], 'warnings': []}"
        )
        result = subprocess.run(
            [sys.executable, "-c", program, str(ROOT / "scripts"), str(self.root)],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_failed_sora_typography_never_returns_clean_video_as_success(self):
        post = image_post(fmt="reel")
        post["visual"]["reel"] = {"overlay_beats": ["REQUIRED"]}
        font = ROOT / "assets" / "fonts" / "BebasNeue-Regular.ttf"
        with patch.object(reel_gen, "sora_configured", return_value=True), \
                patch.object(reel_gen, "typography_font_for", return_value=font), \
                patch.object(reel_gen, "_sora_create_job") as create, \
                patch.object(reel_gen, "_sora_poll", return_value={"status": "completed"}), \
                patch.object(reel_gen, "_sora_download", return_value=True), \
                patch.object(reel_gen, "overlay_beats_on_video", return_value=False), \
                patch.object(reel_gen, "export_cover") as cover:
            create.return_value.ok = True
            create.return_value.json.return_value = {"id": "fixture"}
            self.assertIsNone(self.real_generate_sora(post, self.out, self.root))
        cover.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg tools absent")
    def test_offline_motion_fixture_is_playable_and_second_prepare_is_noop(self):
        self.write_posts([image_post(fmt="reel"), reuse_post()])
        self.drop()
        with patch.object(reel_gen, "validate_video", side_effect=self.real_validate_video), \
                patch.object(reel_gen, "WIDTH", 64), patch.object(reel_gen, "HEIGHT", 96), \
                patch.object(reel_gen, "FPS", 2):
            first = self.run_prepare()
            self.assertEqual([], first["failed"])
            self.real_validate_video(self.out / "p1.mp4")
            before = (self.out / "p1.mp4").stat().st_mtime_ns
            second = self.run_prepare()
        self.assertEqual([], second["failed"])
        self.assertEqual(0, second["prepared"])
        self.assertEqual(before, (self.out / "p1.mp4").stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
