"""Real local HTTP + detached preparation, using only disposable content."""
import io
import json
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
from unittest.mock import patch

from PIL import Image

CODE_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT / "scripts"))
import adhoc_server
import app_state
import guided_workflow
import schedule_planner


class WorkflowDependencyTests(unittest.TestCase):
    def test_publisher_installs_video_verification_tools_before_submission(self):
        workflow = (CODE_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
        self.assertIn("apt-get install -y -qq ffmpeg", workflow)
        self.assertIn("ffprobe -version", workflow)
        self.assertLess(workflow.index("Install media verification tools"),
                        workflow.index("Schedule approved posts"))


class GuidedHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="l8-http-")
        self.root = pathlib.Path(self.tmp.name)
        self.queue = self.root / "queue"
        self.queue.mkdir()
        shutil.copytree(CODE_ROOT / "assets" / "fonts", self.root / "assets" / "fonts")
        shutil.copytree(CODE_ROOT / "config", self.root / "config")
        self.name = "2030-01-01.json"
        self.image_id = "20300101-layer8culture-instagram-1"
        self.qpath = self.queue / self.name
        posts = [{
            "id": self.image_id, "account": "layer8culture", "platform": "instagram",
            "format": "single", "schedule_time": "2030-01-01T09:10:00-05:00",
            "text": "A fixture caption.", "hashtags": ["#Layer8Culture"],
            "visual": {"source": "openai", "openai_prompt": "A clean scene.",
                       "headline": "MAKE IT CLEAR", "subtext": "A supporting sentence.",
                       "typography_preset": "editorial_drop", "aspect": "1:1"},
        }]
        guided_workflow.atomic_json(self.qpath, posts)
        schedule_planner.plan_file(self.qpath)
        self.patches = []
        for key, value in {
            "REPO_ROOT": self.root, "QUEUE_DIR": self.queue,
            "INBOX_DIR": self.root / "assets" / "manual-inbox",
            "OUT_DIR": self.root / "assets" / "generated",
            "TRASH_DIR": self.root / ".trash", "FONTS_DIR": self.root / "assets" / "fonts",
        }.items():
            patcher = patch.object(adhoc_server, key, value)
            patcher.start()
            self.patches.append(patcher)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), adhoc_server.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.session = self.get("/api/session")
        self.jobs = []

    def tearDown(self):
        store = app_state.StateStore(self.root)
        for job in self.jobs:
            if store.get(job)["status"] == "running":
                store.cancel(job)
            self.wait(job)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as response:
            return json.load(response)

    def batch(self):
        return self.get(f"/api/queue/{self.name}")

    def post(self, action, body=None, *, raw=None, revision=None, headers=None):
        values = {
            "X-Layer8-CSRF": self.session["csrf"],
            "If-Match": revision or self.batch()["revision"],
            "Content-Type": "application/json" if raw is None else "image/png",
        }
        values.update(headers or {})
        req = urllib.request.Request(
            f"{self.base}/api/queue/{self.name}/{action}",
            data=json.dumps(body or {}).encode() if raw is None else raw,
            headers=values, method="POST")
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    def wait(self, job_id):
        store = app_state.StateStore(self.root)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = store.payload(job_id)
            record = store.get(job_id)
            if job["status"] != "running" and (
                    not record["pid"] or app_state.process_identity(record["pid"]) != record["identity"]):
                return job
            time.sleep(0.05)
        self.fail("Preparation did not complete.")

    def prepare(self):
        response = self.post("prepare")
        job_id = response["job"]["id"]
        self.jobs.append(job_id)
        job = self.wait(job_id)
        self.assertEqual("done", job["status"], job)
        return self.batch()

    def image(self, color):
        data = io.BytesIO()
        Image.new("RGB", (1024, 1024), color).save(data, "PNG")
        return data.getvalue()

    def test_manual_import_prepare_replace_undo_edit_and_download(self):
        self.assertEqual(str(self.root), self.session["data_root"])
        initial = self.batch()
        self.assertFalse(initial["readiness"]["media_ready"])
        self.assertEqual(1, len(initial["prompt_groups"]))
        uploaded = self.post(
            "upload", raw=self.image((64, 90, 120)),
            headers={"X-Filename": self.image_id + ".png"})
        self.assertEqual("ready", uploaded["images"][0]["status"])
        self.assertFalse(uploaded["readiness"]["media_ready"])
        prepared = self.prepare()
        self.assertTrue(prepared["readiness"]["ready"], prepared["readiness"])
        self.assertEqual(1, len(prepared["posts"][0]["previews"]))
        self.assertIn("/api/media/source/", prepared["images"][0]["original"])
        with urllib.request.urlopen(self.base + prepared["images"][0]["original"]) as source:
            with Image.open(io.BytesIO(source.read())) as image:
                self.assertEqual((64, 90, 120), image.getpixel((0, 0)))

        replaced = self.post("replace", raw=self.image((120, 64, 90)),
                             headers={"X-Image-Id": self.image_id})
        self.assertFalse(replaced["readiness"]["media_ready"])
        self.assertTrue(replaced["images"][0]["final"])
        self.assertTrue(replaced["images"][0]["has_history"])
        self.post("undo-image", {"image_id": self.image_id})
        restored = self.prepare()
        revision = restored["revision"]
        edited = self.post("edit-post", {"post_id": self.image_id, "changes": {
            "text": "Revised caption.", "first_comment": "The complete first comment."}})
        self.assertTrue(edited["readiness"]["media_ready"], edited["readiness"])
        self.assertEqual("The complete first comment.", edited["posts"][0]["first_comment"])
        self.assertNotEqual(revision, edited["revision"])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("delete", revision=revision)
        self.assertEqual(409, error.exception.code)
        with urllib.request.urlopen(self.base + f"/api/queue/{self.name}/download") as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                self.assertIn("posts.json", archive.namelist())
                self.assertIn(f"assets/generated/{self.image_id}.png", archive.namelist())

    def test_malformed_draft_remains_visible_and_removable(self):
        self.qpath.write_text("{broken", encoding="utf-8")
        batch = self.batch()
        self.assertFalse(batch["readiness"]["ready"])
        self.assertTrue(batch["error"])
        self.assertTrue(batch["revision"])
        result = self.post("delete", revision=batch["revision"])
        self.assertEqual(self.name, result["deleted"]["queue"])
        self.assertFalse(self.qpath.exists())

    def test_merged_approval_directs_operator_to_delivery_not_resubmission(self):
        app_state.StateStore(self.root).save_approval(self.name, {"state": "merged"})
        summary = self.get("/api/queues")["queues"][0]
        self.assertEqual("delivery", summary["next_action"])
        self.assertEqual("has_merged_approval", summary["status"])

    def test_library_preview_uses_the_declared_file_not_a_generated_namesake(self):
        library = self.root / "assets" / "library" / "nested"
        generated = self.root / "assets" / "generated"
        library.mkdir(parents=True)
        generated.mkdir(parents=True)
        Image.new("RGB", (1024, 1024), "green").save(library / "promo.png")
        Image.new("RGB", (1024, 1024), "red").save(generated / "promo.png")
        posts = json.loads(self.qpath.read_text())
        posts[0]["visual"] = {"source": "library", "file": "assets/library/nested/promo.png"}
        guided_workflow.atomic_json(self.qpath, posts)
        batch = self.batch()
        self.assertEqual(1, len(batch["posts"][0]["previews"]))
        url = batch["posts"][0]["previews"][0]["url"]
        with urllib.request.urlopen(self.base + url) as response:
            with Image.open(io.BytesIO(response.read())) as image:
                self.assertEqual((0, 128, 0), image.getpixel((0, 0)))

    def test_carousel_preview_preserves_declared_repeated_slides(self):
        folder = self.root / "assets" / "generated"
        folder.mkdir(parents=True)
        Image.new("RGB", (32, 32), "blue").save(folder / "slide.png")
        payload = adhoc_server.post_payload({
            "id": "carousel", "format": "carousel",
            "visual": {"file": "assets/generated/slide.png",
                       "files": ["assets/generated/slide.png", "assets/generated/slide.png"]},
        })
        self.assertEqual(2, len(payload["previews"]))


if __name__ == "__main__":
    unittest.main()
