import json
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import queue_json_guard  # noqa: E402


class QueueJsonGuardTests(unittest.TestCase):
    def sample_post(self):
        return {
            "id": "post-1",
            "account": "lofi",
            "platform": "instagram",
            "format": "single",
            "schedule_time": "2026-07-04T09:00:00-04:00",
            "text": "Caption",
            "visual": {"source": "openai"},
        }

    def test_valid_queue_passes_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            path.write_text(json.dumps([self.sample_post()]), encoding="utf-8")

            repaired = queue_json_guard.validate_or_repair(path, repair=True)

            self.assertFalse(repaired)

    def test_repairs_raw_newline_inside_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            post = self.sample_post()
            raw = json.dumps([post]).replace("Caption", "Caption\nwith raw newline")
            path.write_text(raw, encoding="utf-8")

            repaired = queue_json_guard.validate_or_repair(path, repair=True)
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(repaired)
            self.assertEqual(payload[0]["text"], "Caption\nwith raw newline")
            self.assertTrue(path.with_suffix(".json.invalid").exists())

    def test_unrecoverable_json_fails_with_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            path.write_text("[{", encoding="utf-8")

            with self.assertRaises(ValueError) as raised:
                queue_json_guard.validate_or_repair(path, repair=True)

            self.assertIn("line 1", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
