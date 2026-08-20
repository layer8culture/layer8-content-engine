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


class VisualSourceGuardTests(unittest.TestCase):
    """The regression that silently broke the lofi lane for three weeks.

    The generator emitted visual.source "library" while still writing a full
    openai_prompt. plan_images only plans "openai" visuals, so the manual image
    run produced nothing, and every post then failed to publish with
    missing_media. Across 89 archived queues a non-"openai" source has never
    legitimately shipped a prompt, so this is safe to repair automatically.
    """

    def post(self, post_id, visual):
        return {
            "id": post_id,
            "account": "lofi",
            "platform": "instagram",
            "format": "single",
            "schedule_time": "2026-08-20T09:00:00-04:00",
            "text": "Caption",
            "visual": visual,
        }

    def write(self, tmp, posts):
        path = Path(tmp) / "queue.json"
        path.write_text(json.dumps(posts), encoding="utf-8")
        return path

    def test_repairs_library_source_that_carries_a_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.post("p1", {"source": "library", "openai_prompt": "a lofi studio"}),
            ])

            repaired = queue_json_guard.validate_or_repair(path, repair=True)

            self.assertTrue(repaired)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["visual"]["source"], "openai")
            self.assertEqual(payload[0]["visual"]["openai_prompt"], "a lofi studio")

    def test_repairs_carousel_slide_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.post("p1", {
                    "source": "library",
                    "slides": [{"openai_prompt": "cover"}, {"openai_prompt": "point"}],
                }),
            ])

            self.assertTrue(queue_json_guard.validate_or_repair(path, repair=True))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["visual"]["source"], "openai")

    def test_leaves_a_genuine_library_post_alone(self):
        """A real library post brings its own asset and has no prompt."""
        with tempfile.TemporaryDirectory() as tmp:
            visual = {"source": "library", "file": "assets/library/clip.mp4"}
            path = self.write(tmp, [self.post("p1", visual)])

            self.assertFalse(queue_json_guard.validate_or_repair(path, repair=True))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["visual"]["source"], "library")

    def test_leaves_reuse_cross_posts_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            visual = {"source": "reuse", "of": "p1", "aspect": "9:16"}
            path = self.write(tmp, [self.post("p2", visual)])

            self.assertFalse(queue_json_guard.validate_or_repair(path, repair=True))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["visual"]["source"], "reuse")

    def test_blank_prompt_is_not_treated_as_a_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.post("p1", {"source": "library", "openai_prompt": "   "}),
            ])

            self.assertFalse(queue_json_guard.validate_or_repair(path, repair=True))

    def test_fails_loudly_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.post("p1", {"source": "library", "openai_prompt": "a lofi studio"}),
            ])

            with self.assertRaises(ValueError) as raised:
                queue_json_guard.validate_or_repair(path, repair=False)

            self.assertIn("p1", str(raised.exception))
            self.assertIn("openai", str(raised.exception))

    def test_repairs_source_alongside_a_control_character(self):
        """Both repair passes must survive one run."""
        with tempfile.TemporaryDirectory() as tmp:
            posts = [self.post("p1", {"source": "library", "openai_prompt": "studio"})]
            raw = json.dumps(posts).replace("Caption", "Line one\nLine two")
            path = Path(tmp) / "queue.json"
            path.write_text(raw, encoding="utf-8")

            self.assertTrue(queue_json_guard.validate_or_repair(path, repair=True))

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["visual"]["source"], "openai")
            self.assertIn("\n", payload[0]["text"])

    def test_unknown_source_with_a_prompt_is_also_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.post("p1", {"source": "higgsfield", "openai_prompt": "studio"}),
            ])

            self.assertTrue(queue_json_guard.validate_or_repair(path, repair=True))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["visual"]["source"], "openai")