import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

os.environ.setdefault("POSTIZ_URL", "https://postiz.example.test")
os.environ.setdefault("POSTIZ_API_KEY", "test-key")
import changed_queue_files  # noqa: E402
import postiz_dedupe  # noqa: E402
import post_to_postiz  # noqa: E402


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


class PostToPostizTests(unittest.TestCase):
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
            {"postiz_post_id": "postiz-123", "postiz_status": "scheduled"},
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

    def test_schedule_skips_existing_queued_duplicate_before_uploading(self):
        original_find = post_to_postiz.find_existing_postiz_duplicate
        original_upload = post_to_postiz.upload_media
        upload_called = False

        def fake_find(integration_id, caption, schedule_time):
            self.assertEqual(integration_id, post_to_postiz.INTEGRATIONS[("layer8culture", "instagram")])
            self.assertEqual(caption, "Caption\n\n#Layer8Culture")
            self.assertEqual(schedule_time, "2026-06-29T10:00:00-04:00")
            return {"id": "postiz-existing"}

        def fake_upload(_path):
            nonlocal upload_called
            upload_called = True
            return {"id": "media-1", "path": "/media.png"}

        post_to_postiz.find_existing_postiz_duplicate = fake_find
        post_to_postiz.upload_media = fake_upload
        try:
            result = post_to_postiz.schedule(
                {
                    "id": "post-1",
                    "account": "layer8culture",
                    "platform": "instagram",
                    "format": "single",
                    "schedule_time": "2026-06-29T10:00:00-04:00",
                    "text": "Caption",
                    "hashtags": ["#Layer8Culture"],
                    "visual": {},
                }
            )
        finally:
            post_to_postiz.find_existing_postiz_duplicate = original_find
            post_to_postiz.upload_media = original_upload

        self.assertFalse(upload_called)
        self.assertEqual(
            result,
            {
                "scheduled": False,
                "integration_id": post_to_postiz.INTEGRATIONS[("layer8culture", "instagram")],
                "skip_reason": "postiz_duplicate",
                "postiz_post_id": "postiz-existing",
            },
        )

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
