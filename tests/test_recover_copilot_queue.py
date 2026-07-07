import json
import os
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import recover_copilot_queue  # noqa: E402


class RecoverCopilotQueueTests(unittest.TestCase):
    def tempdir(self):
        return tempfile.TemporaryDirectory(prefix="recover-copilot-", dir=ROOT)

    def sample_post(self, post_id="post-1"):
        return {
            "id": post_id,
            "account": "layer8culture",
            "platform": "instagram",
            "format": "single",
            "schedule_time": "2026-07-08T09:00:00-04:00",
            "text": "Caption",
            "visual": {"source": "openai"},
        }

    def write_artifact(self, session_root, session_name, name, content, mtime):
        files = Path(session_root) / session_name / "files"
        files.mkdir(parents=True, exist_ok=True)
        path = files / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content), encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def test_recovers_newest_valid_queue_and_same_session_summary(self):
        with self.tempdir() as tmp:
            root = Path(tmp)
            session_root = root / "session-state"
            target = root / "queue" / "2026-07-08.json"
            summary = root / "queue" / "2026-07-08.summary.md"
            self.write_artifact(session_root, "old", "2026-07-08.json", [self.sample_post("old")], 100)
            new_artifact = self.write_artifact(session_root, "new", "2026-07-08.json", [self.sample_post("new")], 200)
            self.write_artifact(session_root, "new", "2026-07-08.summary.md", "summary for review\n", 201)

            selected = recover_copilot_queue.recover_queue(
                target,
                summary_file=summary,
                date="2026-07-08",
                session_root=session_root,
            )

            self.assertEqual(selected.path, new_artifact)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))[0]["id"], "new")
            self.assertEqual(summary.read_text(encoding="utf-8"), "summary for review\n")

    def test_skips_invalid_newest_artifact_and_recovers_older_valid_one(self):
        with self.tempdir() as tmp:
            root = Path(tmp)
            session_root = root / "session-state"
            target = root / "queue" / "2026-07-08.json"
            valid_artifact = self.write_artifact(session_root, "old", "2026-07-08.json", [self.sample_post("valid")], 100)
            self.write_artifact(session_root, "new", "2026-07-08.json", {"not": "a-list"}, 200)

            selected = recover_copilot_queue.recover_queue(
                target,
                date="2026-07-08",
                session_root=session_root,
            )

            self.assertEqual(selected.path, valid_artifact)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))[0]["id"], "valid")

    def test_does_not_recover_when_only_invalid_artifacts_exist(self):
        with self.tempdir() as tmp:
            root = Path(tmp)
            session_root = root / "session-state"
            target = root / "queue" / "2026-07-08.json"
            self.write_artifact(session_root, "bad", "2026-07-08.json", [], 100)

            selected = recover_copilot_queue.recover_queue(
                target,
                date="2026-07-08",
                session_root=session_root,
            )

            self.assertIsNone(selected)
            self.assertFalse(target.exists())

    def test_existing_invalid_target_fails_instead_of_overwriting(self):
        with self.tempdir() as tmp:
            root = Path(tmp)
            session_root = root / "session-state"
            target = root / "queue" / "2026-07-08.json"
            target.parent.mkdir()
            target.write_text("[]", encoding="utf-8")
            self.write_artifact(session_root, "valid", "2026-07-08.json", [self.sample_post("valid")], 100)

            with self.assertRaises(ValueError):
                recover_copilot_queue.recover_queue(
                    target,
                    date="2026-07-08",
                    session_root=session_root,
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "[]")


if __name__ == "__main__":
    unittest.main()
