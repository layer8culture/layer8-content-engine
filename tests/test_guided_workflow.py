import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import guided_workflow as workflow
import manual_media


class GuidedMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.qpath = self.root / "queue" / "batch.json"
        self.posts = [{
            "id": "example", "format": "single", "text": "original",
            "visual": {"source": "openai", "openai_prompt": "scene",
                       "headline": "HEADLINE", "subtext": "support"},
        }]
        workflow.atomic_json(self.qpath, self.posts)
        self.spec = manual_media.plan_images(self.posts)[0]
        self.inbox = self.root / manual_media.DEFAULT_INBOX
        retired = self.inbox / manual_media.INGESTED_DIRNAME
        retired.mkdir(parents=True)
        Image.new("RGB", (32, 32), "red").save(retired / "example.png")
        patcher = patch.object(workflow, "invalidate")
        self.invalidate = patcher.start()
        self.addCleanup(patcher.stop)

    def image_bytes(self, color):
        output = io.BytesIO()
        Image.new("RGB", (32, 32), color).save(output, "PNG")
        return output.getvalue()

    def test_replacement_preserves_original_and_undo_restores_it(self):
        workflow.replace_image(self.root, self.qpath, self.spec, self.image_bytes("blue"))
        history = workflow.versions(self.root, "example")
        self.assertEqual(1, len(history))
        with Image.open(history[0].with_name("source.png")) as image:
            self.assertEqual((255, 0, 0), image.getpixel((0, 0)))
        workflow.undo_image(self.root, self.qpath, self.posts, self.spec)
        with Image.open(self.inbox / "example.png") as image:
            self.assertEqual((255, 0, 0), image.getpixel((0, 0)))
        self.assertEqual([], workflow.versions(self.root, "example"))
        self.invalidate.assert_called_with(self.root, ["example"])
        self.assertEqual(2, self.invalidate.call_count)

    def test_bad_replacement_does_not_change_original_or_history(self):
        with self.assertRaises(ValueError):
            workflow.replace_image(self.root, self.qpath, self.spec, b"broken")
        self.assertEqual([], workflow.versions(self.root, "example"))
        self.assertFalse((self.inbox / "example.png").exists())
        self.invalidate.assert_not_called()

    def test_image_undo_survives_a_rescheduled_queue_filename(self):
        workflow.replace_image(self.root, self.qpath, self.spec, self.image_bytes("blue"))
        renamed = self.qpath.with_name("2030-01-02.json")
        self.qpath.rename(renamed)
        workflow.undo_image(self.root, renamed, self.posts, self.spec)
        with Image.open(self.inbox / "example.png") as image:
            self.assertEqual((255, 0, 0), image.getpixel((0, 0)))

    def test_typography_edit_preserves_previous_fields(self):
        workflow.edit_image(self.root, self.qpath, self.posts, self.spec, "NEW", "New supporting text")
        self.assertEqual("NEW", self.posts[0]["visual"]["headline"])
        workflow.undo_image(self.root, self.qpath, self.posts, self.spec)
        self.assertEqual("HEADLINE", self.posts[0]["visual"]["headline"])
        self.assertEqual("support", self.posts[0]["visual"]["subtext"])

    def test_unknown_post_edit_fields_do_not_rewrite_queue(self):
        previous = self.qpath.read_bytes()
        with self.assertRaises(ValueError):
            workflow.edit_post(self.qpath, self.posts, "example", {"account": "lofi"})
        self.assertEqual(previous, self.qpath.read_bytes())

    def test_schedule_edit_requires_timezone(self):
        with self.assertRaises(ValueError):
            workflow.edit_post(self.qpath, self.posts, "example", {"schedule_time": "2026-10-01T12:00:00"})

    def test_caption_edit_keeps_media_and_identity(self):
        workflow.edit_post(self.qpath, self.posts, "example", {"text": "Revised", "first_comment": "Hello"})
        result = json.loads(self.qpath.read_text())
        self.assertEqual("Revised", result[0]["text"])
        self.assertEqual("example", result[0]["id"])
        self.assertEqual("HEADLINE", result[0]["visual"]["headline"])


if __name__ == "__main__":
    unittest.main()
