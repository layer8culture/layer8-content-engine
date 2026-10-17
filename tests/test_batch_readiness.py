import json
from datetime import datetime
from unittest.mock import patch

from PIL import Image

if __package__:
    from .test_ship_queue import RepoTestCase, batch_readiness, post
else:
    from test_ship_queue import RepoTestCase, batch_readiness, post


class BatchReadinessTests(RepoTestCase):
    def report(self, **kwargs):
        return batch_readiness.report(self.repo.root / "queue/2030-09-05.json", self.repo.root, **kwargs)

    def test_manifest_revision_tracks_caption_summary_and_exact_asset_bytes(self):
        qpath = self.repo.add_batch()
        initial = self.report()
        self.assertTrue(initial["ready"], initial["blockers"])
        posts = json.loads(qpath.read_text())
        qpath.write_text(json.dumps(posts, separators=(",", ":")), encoding="utf-8")
        self.assertEqual(initial["revision"], self.report()["revision"])
        posts[0]["text"] = "New caption"
        qpath.write_text(json.dumps(posts), encoding="utf-8")
        changed = self.report()
        self.assertNotEqual(initial["revision"], changed["revision"])
        Image.new("RGB", (32, 32), "red").save(self.repo.root / posts[0]["visual"]["file"])
        asset_changed = self.report()
        self.assertNotEqual(changed["revision"], asset_changed["revision"])
        qpath.with_suffix(".summary.md").write_text("New review summary", encoding="utf-8")
        self.assertNotEqual(asset_changed["revision"], self.report()["revision"])

    def test_invalid_content_is_a_serializable_blocker_not_an_exception(self):
        qpath = self.repo.add_batch()
        for raw in ('{', '[]', '{}', '[null]', '[{"id":"x"}]', '[NaN]'):
            with self.subTest(raw=raw):
                qpath.write_text(raw, encoding="utf-8")
                result = self.report()
                self.assertFalse(result["ready"])
                self.assertTrue(result["blockers"])
                json.dumps(result)
        for field, value in [("id", []), ("format", {}), ("platform", []), ("account", {}),
                             ("text", 1), ("schedule_time", None)]:
            entry = post()
            entry[field] = value
            qpath.write_text(json.dumps([entry]), encoding="utf-8")
            self.assertFalse(self.report()["ready"])

    def test_unsafe_paths_and_account_mismatch_block(self):
        for value in ("../outside.png", "C:\\private\\picture.png", "C:relative.png",
                      "https://example.com/image.png", "\\\\server\\share\\file.png",
                      "assets/generated/../../outside.png", "/etc/file.png"):
            with self.subTest(path=value):
                entry = post()
                entry["visual"]["file"] = value
                self.repo.add_batch([entry], render=False)
                self.assertFalse(self.report()["media_ready"])
        self.repo.add_batch([post(account="lofi")])
        self.assertIn("account must match", " ".join(self.report()["blockers"]))

    def test_generated_media_cannot_reference_another_post_or_lane(self):
        entry = post()
        entry["visual"]["file"] = "assets/generated/20300905-lofi-instagram-1.png"
        self.repo.add_batch([entry], render=False)
        self.assertIn("media must belong", " ".join(self.report()["blockers"]))

    def test_static_readiness_still_checks_schedule_date_and_window(self):
        entry = post(schedule_time="2030-09-06T23:30:00-04:00")
        self.repo.add_batch([entry])
        self.assertFalse(self.report(require_future=False)["schedule_ready"])

    def test_deleted_cover_selects_exact_queue(self):
        entry = post(fmt="reel")
        self.repo.add_batch([entry])
        selected = batch_readiness.affected_queues(self.repo.root, [entry["visual"]["cover"]])
        self.assertEqual([p.name for p in selected], ["2030-09-05.json"])

    def test_missing_video_inspection_tool_is_an_infrastructure_error(self):
        self.repo.add_batch([post(fmt="reel")])
        with patch.object(batch_readiness.subprocess, "run", side_effect=FileNotFoundError("ffprobe")):
            with self.assertRaises(FileNotFoundError):
                self.report()

    def test_carousel_order_gaps_duplicates_and_incomplete_writeback_block(self):
        entry = post(fmt="carousel")
        self.repo.add_batch([entry])
        good = self.report()
        self.assertTrue(good["ready"], good["blockers"])
        media = good["posts"][0]["media"]
        self.assertEqual(media, entry["visual"]["files"])
        for files in ([media[1], media[0], media[2]], [media[0], media[0], media[2]], media[:2]):
            with self.subTest(files=files):
                entry["visual"]["files"] = files
                entry["visual"]["file"] = files[0]
                self.repo.add_batch([entry], render=False)
                self.assertFalse(self.report()["media_ready"])

    def test_reuse_requires_matching_final_video_and_cover(self):
        source = post(fmt="reel")
        target = post(post_id="20300905-layer8culture-youtube-1", fmt="reel",
                      platform="youtube", youtube_title="A title",
                      schedule_time="2030-09-05T10:00:00-04:00")
        target["visual"].update(source="reuse", of=source["id"])
        self.repo.add_batch([source, target])
        result = self.report()
        self.assertTrue(result["media_ready"], result["blockers"])
        Image.new("RGB", (32, 32), "red").save(self.repo.root / target["visual"]["cover"])
        self.assertIn("reuse output is stale", " ".join(self.report()["blockers"]))
        target["visual"]["of"] = "not-in-batch"
        self.repo.add_batch([source, target], render=False)
        self.assertIn("original post in this batch", " ".join(self.report()["blockers"]))

    def test_corrupt_video_and_image_are_not_ready(self):
        entry = post(fmt="reel")
        self.repo.add_batch([entry])
        (self.repo.root / entry["visual"]["file"]).write_bytes(b"fake mp4")
        self.assertIn("unreadable video", " ".join(self.report()["blockers"]))

    def test_freshness_is_separate_and_static_ci_does_not_grant_freshness(self):
        self.repo.add_batch()
        now = datetime.fromisoformat("2030-09-05T08:50:00-04:00")
        fresh = self.report(now=now)
        static = self.report(now=now, require_future=False)
        self.assertFalse(fresh["schedule_ready"])
        self.assertTrue(fresh["media_ready"])
        self.assertTrue(static["schedule_ready"], static["blockers"])
        self.assertEqual(fresh["revision"], static["revision"])

    def test_preparation_failure_and_warning_are_preserved(self):
        self.repo.add_batch()
        with patch("prepare_media.preparation_status",
                   return_value={"blockers": ["stale typography"], "warnings": ["dark frame"]}):
            result = self.report()
        self.assertFalse(result["media_ready"])
        self.assertIn("stale typography", result["blockers"])
        self.assertIn("dark frame", result["warnings"])

    def test_infrastructure_errors_are_not_success_shaped(self):
        self.repo.add_batch()
        with patch.object(batch_readiness, "file_hash", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.report()

    def test_asset_only_change_selects_exact_referencing_queue(self):
        entry = post()
        self.repo.add_batch([entry])
        self.repo.add_batch([post(post_id="other")], name="2030-09-06.json")
        selected = batch_readiness.affected_queues(self.repo.root, [entry["visual"]["file"]])
        self.assertEqual([p.name for p in selected], ["2030-09-05.json"])
