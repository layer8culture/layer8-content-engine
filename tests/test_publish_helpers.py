import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

os.environ.setdefault("POSTIZ_URL", "https://postiz.example.test")
os.environ.setdefault("POSTIZ_API_KEY", "test-key")
import changed_queue_files  # noqa: E402
import postiz_dedupe  # noqa: E402
import post_to_postiz  # noqa: E402
import publish_helpers  # noqa: E402


class ChangedQueueFilesTests(unittest.TestCase):
    def test_returns_changed_queue_json_files_that_still_exist(self):
        event = {
            "commits": [
                {
                    "added": [
                        "queue/2026-06-28.json",
                        "queue/2026-06-28.summary.md",
                    ],
                    "modified": ["README.md", "queue/lofi-2026-06-28.json"],
                    "removed": ["queue/old.json"],
                }
            ]
        }
        existing = {"queue/2026-06-28.json", "queue/lofi-2026-06-28.json"}

        files = changed_queue_files.changed_queue_files(event, exists=existing.__contains__)

        self.assertEqual(files, ["queue/2026-06-28.json", "queue/lofi-2026-06-28.json"])

    def test_dedupes_paths_across_commits(self):
        event = {
            "commits": [
                {"added": ["queue/2026-06-28.json"]},
                {"modified": ["queue/2026-06-28.json"]},
            ]
        }

        files = changed_queue_files.changed_queue_files(event, exists=lambda path: True)

        self.assertEqual(files, ["queue/2026-06-28.json"])

    def test_ignores_deleted_or_archived_queue_files(self):
        event = {"commits": [{"added": ["queue/2026-06-28.json"]}]}

        files = changed_queue_files.changed_queue_files(event, exists=lambda path: False)

        self.assertEqual(files, [])

    def test_git_changed_queue_files_filters_existing_queue_json(self):
        files = changed_queue_files.git_changed_queue_files(
            exists={"queue/recovery.json", "queue/other.json"}.__contains__,
            changed_paths=[
                "README.md",
                "queue/recovery.json",
                "queue/recovery.summary.md",
                "queue\\other.json",
            ],
        )

        self.assertEqual(files, ["queue/recovery.json", "queue/other.json"])

    def test_queue_files_for_publish_uses_trigger_range_when_event_has_no_queue_paths(self):
        event = {
            "before": "a" * 40,
            "after": "b" * 40,
            "commits": [],
            "head_commit": {},
        }
        calls = []

        def git_fallback(before, after):
            calls.append((before, after))
            return ["README.md", "queue/recovery.json"]

        files = changed_queue_files.queue_files_for_publish(
            event,
            fallback_all=True,
            exists={"queue/recovery.json"}.__contains__,
            git_fallback=git_fallback,
        )

        self.assertEqual(files, ["queue/recovery.json"])
        self.assertEqual(calls, [("a" * 40, "b" * 40)])

    def test_queue_files_for_publish_does_not_diff_unrelated_head(self):
        event = {"commits": [], "head_commit": {}}

        def fail_git_fallback(_before, _after):
            raise AssertionError("git fallback should require the triggering commit range")

        with self.assertRaises(ValueError):
            changed_queue_files.queue_files_for_publish(
                event, fallback_all=True, git_fallback=fail_git_fallback,
            )


class PostToPostizTests(unittest.TestCase):
    def test_resolve_local_paths_accepts_windows_style_relative_paths(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                assets = Path("assets") / "generated"
                assets.mkdir(parents=True)
                first = assets / "post-1-1.png"
                second = assets / "post-1-2.png"
                first.write_bytes(b"image-1")
                second.write_bytes(b"image-2")

                paths = publish_helpers.resolve_local_paths(
                    {
                        "format": "carousel",
                        "visual": {
                            "files": [
                                "assets\\generated\\post-1-1.png",
                                "assets\\generated\\post-1-2.png",
                            ]
                        },
                    }
                )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(
                paths,
                [
                    str(Path("assets") / "generated" / "post-1-1.png"),
                    str(Path("assets") / "generated" / "post-1-2.png"),
                ],
            )

    def test_append_new_log_records_skips_duplicate_ids(self):
        log = [{"id": "post-1", "scheduled": True}]
        results = [
            {"id": "post-1", "scheduled": True},
            {"id": "post-2", "scheduled": True},
        ]

        updated = post_to_postiz.append_new_log_records(log, results)

        self.assertEqual([record["id"] for record in updated], ["post-1", "post-2"])

    def test_postiz_response_metadata_extracts_nested_post_id(self):
        payload = {"status": "scheduled", "posts": [{"id": "postiz-123"}]}

        metadata = post_to_postiz.postiz_response_metadata(payload)

        self.assertEqual(
            metadata,
            {"postiz_post_id": "postiz-123", "postiz_status": "scheduled", "provider_status": "scheduled"},
        )

    def test_matching_postiz_post_requires_same_content_and_time(self):
        candidate = {
            "state": "QUEUE",
            "content": "Caption\n\n#Layer8Culture",
            "publishDate": "2026-06-29T14:00:00.000Z",
            "integration": {"id": "ig-1"},
        }

        self.assertTrue(
            post_to_postiz.is_matching_existing_postiz_post(
                candidate,
                "ig-1",
                "Caption\n\n#Layer8Culture",
                "2026-06-29T10:00:00-04:00",
            )
        )
        self.assertFalse(
            post_to_postiz.is_matching_existing_postiz_post(
                candidate,
                "ig-1",
                "Different caption",
                "2026-06-29T10:00:00-04:00",
            )
        )
        self.assertTrue(
            post_to_postiz.is_matching_existing_postiz_post(
                {**candidate, "state": "PUBLISHED"},
                "ig-1",
                "Caption\n\n#Layer8Culture",
                "2026-06-29T10:00:00-04:00",
            )
        )
        self.assertFalse(
            post_to_postiz.is_matching_existing_postiz_post(
                {**candidate, "state": "ERROR"},
                "ig-1",
                "Caption\n\n#Layer8Culture",
                "2026-06-29T10:00:00-04:00",
            )
        )

    def test_schedule_rejects_instagram_carousel_with_missing_media(self):
        original_find = post_to_postiz.find_existing_postiz_duplicate
        original_upload = post_to_postiz.upload_media

        def fail_find(*_args):
            raise AssertionError("duplicate lookup should not run when media is missing")

        def fail_upload(_path):
            raise AssertionError("upload should not run when media is missing")

        post_to_postiz.find_existing_postiz_duplicate = fail_find
        post_to_postiz.upload_media = fail_upload
        try:
            result = post_to_postiz.schedule(
                {
                    "id": "post-missing-media",
                    "account": "layer8culture",
                    "platform": "instagram",
                    "format": "carousel",
                    "schedule_time": "2026-06-29T10:00:00-04:00",
                    "text": "Caption",
                    "visual": {"files": ["assets\\generated\\missing.png"]},
                }
            )
        finally:
            post_to_postiz.find_existing_postiz_duplicate = original_find
            post_to_postiz.upload_media = original_upload

        self.assertEqual(result["scheduled"], False)
        self.assertEqual(result["skip_reason"], "missing_media")
        self.assertEqual(
            result["integration_id"],
            post_to_postiz.INTEGRATIONS[("layer8culture", "instagram")],
        )
        self.assertTrue(post_to_postiz.fatal_publish_failures([result]))

    def test_schedule_skips_existing_queued_duplicate_before_uploading(self):
        original_find = post_to_postiz.find_existing_postiz_duplicate
        original_upload = post_to_postiz.upload_media
        upload_called = False

        def fake_find(integration_id, caption, schedule_time):
            self.assertEqual(integration_id, post_to_postiz.INTEGRATIONS[("layer8culture", "instagram")])
            self.assertEqual(caption, "Caption\n\n#Layer8Culture")
            self.assertEqual(schedule_time, "2026-06-29T10:00:00-04:00")
            return {"id": "postiz-existing", "state": "QUEUE"}

        def fake_upload(_path):
            nonlocal upload_called
            upload_called = True
            return {"id": "media-1", "path": "/media.png"}

        post_to_postiz.find_existing_postiz_duplicate = fake_find
        post_to_postiz.upload_media = fake_upload
        try:
            with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
                media = Path(tmp) / "post-1.png"
                media.write_bytes(b"image")
                result = post_to_postiz.schedule(
                    {
                        "id": "post-1",
                        "account": "layer8culture",
                        "platform": "instagram",
                        "format": "single",
                        "schedule_time": "2026-06-29T10:00:00-04:00",
                        "text": "Caption",
                        "hashtags": ["#Layer8Culture"],
                        "visual": {"file": str(media)},
                    }
                )
        finally:
            post_to_postiz.find_existing_postiz_duplicate = original_find
            post_to_postiz.upload_media = original_upload

        self.assertFalse(upload_called)
        self.assertTrue(result["scheduled"])
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["delivery_status"], "queued")
        self.assertEqual(result["postiz_post_id"], "postiz-existing")

    def test_main_leaves_queue_unarchived_on_fatal_publish_failure(self):
        original_schedule = post_to_postiz.schedule

        def fake_schedule(_post, **_kwargs):
            return {
                "scheduled": False,
                "integration_id": "ig-1",
                "skip_reason": "missing_media",
                "skip_detail": "no resolved media",
            }

        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                queue_dir = Path("queue")
                queue_dir.mkdir()
                qpath = queue_dir / "post.json"
                qpath_abs = qpath.resolve()
                archived_abs = Path("posted").resolve() / "post.json"
                qpath.write_text(
                    "[{\"id\":\"post-1\",\"account\":\"layer8culture\","
                    "\"platform\":\"instagram\",\"format\":\"single\","
                    "\"schedule_time\":\"2026-06-29T10:00:00-04:00\","
                    "\"text\":\"Caption\",\"visual\":{}}]",
                    encoding="utf-8",
                )
                post_to_postiz.schedule = fake_schedule

                with patch.object(post_to_postiz, "require_publish_ready"), patch.object(
                    post_to_postiz, "require_approved_payload",
                    return_value={"state": "approved", "commit": "a" * 40, "revision": "approved"},
                ), self.assertRaises(SystemExit) as raised:
                    post_to_postiz.main(str(qpath))
            finally:
                post_to_postiz.schedule = original_schedule
                os.chdir(original_cwd)

            self.assertNotEqual(raised.exception.code, 0)
            self.assertTrue(qpath_abs.exists())
            self.assertFalse(archived_abs.exists())

    def test_postiz_dedupe_groups_only_queued_duplicates_for_integration(self):
        posts = [
            {
                "id": "keep",
                "state": "QUEUE",
                "content": "Caption",
                "publishDate": "2026-06-29T14:00:00.000Z",
                "createdAt": "2026-06-29T12:00:00.000Z",
                "integration": {"id": "ig-1"},
            },
            {
                "id": "delete",
                "state": "QUEUE",
                "content": "Caption",
                "publishDate": "2026-06-29T14:00:00.000Z",
                "createdAt": "2026-06-29T12:01:00.000Z",
                "integration": {"id": "ig-1"},
            },
            {
                "id": "published",
                "state": "PUBLISHED",
                "content": "Caption",
                "publishDate": "2026-06-29T14:00:00.000Z",
                "integration": {"id": "ig-1"},
            },
            {
                "id": "other-integration",
                "state": "QUEUE",
                "content": "Caption",
                "publishDate": "2026-06-29T14:00:00.000Z",
                "integration": {"id": "ig-2"},
            },
        ]

        groups = postiz_dedupe.find_duplicate_groups(posts, "ig-1")

        self.assertEqual([[post["id"] for post in group] for group in groups], [["keep", "delete"]])

    def test_postiz_dedupe_groups_all_integrations_by_default(self):
        posts = [
            {
                "id": "ig-keep",
                "state": "QUEUE",
                "content": "Caption",
                "publishDate": "2026-06-29T14:00:00.000Z",
                "createdAt": "2026-06-29T12:00:00.000Z",
                "integration": {"id": "ig-1"},
            },
            {
                "id": "ig-delete",
                "state": "QUEUE",
                "content": "Caption",
                "publishDate": "2026-06-29T14:00:00.000Z",
                "createdAt": "2026-06-29T12:01:00.000Z",
                "integration": {"id": "ig-1"},
            },
            {
                "id": "yt-keep",
                "state": "QUEUE",
                "content": "Video",
                "publishDate": "2026-06-29T15:00:00.000Z",
                "createdAt": "2026-06-29T12:00:00.000Z",
                "integration": {"id": "yt-1"},
            },
            {
                "id": "yt-delete",
                "state": "QUEUE",
                "content": "Video",
                "publishDate": "2026-06-29T15:00:00.000Z",
                "createdAt": "2026-06-29T12:01:00.000Z",
                "integration": {"id": "yt-1"},
            },
        ]

        groups = postiz_dedupe.find_duplicate_groups(posts)

        self.assertEqual(
            [[post["id"] for post in group] for group in groups],
            [["ig-keep", "ig-delete"], ["yt-keep", "yt-delete"]],
        )


if __name__ == "__main__":
    unittest.main()
