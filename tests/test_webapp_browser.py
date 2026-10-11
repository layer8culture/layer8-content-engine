"""Optional browser regressions. Every request is intercepted; no live server or queue."""
from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import shutil
import unittest
from urllib.parse import unquote, urlparse
import uuid

try:
    from playwright.sync_api import Error as BrowserError, expect, sync_playwright
except ImportError:
    sync_playwright = None


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:18765"
NAME = "2099-09-06.json"
OTHER = "lofi-2099-09-06.json"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lZsAAAAASUVORK5CYII="
)


def batch(name=NAME):
    images = [{
        "image_id": f"post-1-{i}", "post_id": "post-1", "filename": f"post-1-{i}.png",
        "headline": f"Headline {i}", "subtext": f"Subtext {i}",
        "scene": f"A quiet studio with distinct scene {i}.",
        "slide_index": i, "format": "carousel", "aspect": "4:5", "size": "1024x1536",
        "status": "done", "preview": f"/api/media/generated/post-1-{i}.png",
        "original": f"/api/media/original/post-1-{i}.png",
        "prompt": f"Exact brand-composed prompt {i}", "has_history": False,
    } for i in range(1, 8)]
    previews = [{"name": image["filename"], "url": image["preview"], "is_video": False} for image in images]
    return {
        "name": name, "lane": "Layer8Culture Radio" if name == OTHER else "Layer8Culture",
        "revision": "r1", "images": images, "staged": [], "active_job": None,
        "readiness": {"ready": True, "media_ready": True, "schedule_ready": True, "blockers": [], "warnings": []},
        "prompt_groups": [], "approval": None, "delivery": [],
        "posts": [{
            "id": "post-1", "platform": "instagram", "format": "carousel",
            "account": "layer8culture", "category": "Builder", "text": "Full caption.\nLast sentence stays visible.",
            "hashtags": ["#Layer8Culture", "#Build"], "first_comment": "The full first comment.",
            "schedule_time": "2099-09-06T14:00:00-04:00", "previews": previews,
        }, {
            "id": "post-2", "platform": "youtube", "format": "reel", "account": "layer8culture",
            "text": "Short caption", "hashtags": [], "first_comment": "", "youtube_title": "A deliberate title",
            "schedule_time": "2099-09-06T17:00:00-04:00",
            "previews": [{"name": "post-2.mp4", "url": "/api/media/generated/post-2.mp4", "is_video": True}],
        }],
    }


@unittest.skipUnless(sync_playwright, "Optional Playwright is not installed.")
class GuidedWorkspaceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = ROOT / ".local" / f"webapp-fixture-{uuid.uuid4().hex}"
        cls.scratch.mkdir(parents=True)
        cls.old_environment = {key: os.environ.get(key) for key in ("TMP", "TEMP", "TMPDIR")}
        os.environ.update({key: str(cls.scratch) for key in cls.old_environment})
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except BrowserError as error:
            cls.playwright.stop()
            cls.clean_scratch()
            raise unittest.SkipTest(f"Optional Chromium is not available: {error}") from error

    @classmethod
    def clean_scratch(cls):
        for key, value in cls.old_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(cls.scratch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.clean_scratch()

    def setUp(self):
        self.batches = {NAME: batch(), OTHER: batch(OTHER)}
        self.requests = []
        self.errors = []
        self.active_job = None
        self.jobs = {}
        self.hold_jobs = False
        self.hold_queue = None
        self.upload_error = False
        self.reschedule_target = None
        self.refreshed_delivery = []
        self.refreshed_workflow = {"url": "https://github.com/example/fixture/actions/runs/88", "status": "completed", "conclusion": "failure"}
        self.held = []
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = self.context.new_page()
        self.page.set_default_timeout(6000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.route("**/*", self.route)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def respond(self, route, body, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def bump(self, data):
        data["revision"] = f"r{int(data['revision'][1:]) + 1}"
        data["approval"] = None

    def route(self, route):
        request = route.request
        parsed = urlparse(request.url)
        path = unquote(parsed.path)
        self.requests.append((request.method, path, request.headers, request.post_data_buffer))
        if path == "/":
            route.fulfill(path=str(ROOT / "webapp" / "index.html"), content_type="text/html")
            return
        if path.startswith("/static/"):
            route.fulfill(path=str(ROOT / "webapp" / path.rsplit("/", 1)[1]))
            return
        if path.startswith("/fonts/"):
            filename = "Inter-Variable.ttf" if "inter" in path else "SpaceGrotesk-Variable.ttf"
            route.fulfill(path=str(ROOT / "assets" / "fonts" / filename), content_type="font/ttf")
            return
        if path.startswith("/api/media/"):
            route.fulfill(status=200, content_type="image/png", body=PNG)
            return
        if path == "/api/session":
            self.respond(route, {"csrf": "fixture-csrf", "diagnostics": [{"name": "Pillow", "ok": True, "detail": "Available"}], "active_job": self.active_job})
            return
        if path == "/api/lanes":
            self.respond(route, {"copilot": {"ok": True}, "lanes": [{"lane": "layer8culture", "label": "Layer8Culture", "prompt_ok": True, "default_date": "2099-09-06"}]})
            return
        if path == "/api/queues":
            self.respond(route, {"queues": [{"name": name, "lane": data["lane"], "posts": len(data["posts"]), "readiness": data["readiness"]} for name, data in self.batches.items()]})
            return
        if path == "/api/trash":
            self.respond(route, {"entries": [{"entry": "removed-fixture", "queue": NAME}]})
            return
        if path == "/api/restore":
            if request.headers.get("x-layer8-csrf") != "fixture-csrf":
                self.respond(route, {"error": "Missing CSRF"}, 403)
            else:
                self.respond(route, {"queue": NAME, "restored": "removed-fixture"})
            return
        if path.startswith("/api/jobs/"):
            if self.hold_jobs:
                self.held.append((route, copy.deepcopy(self.jobs.get(path.split("/")[3], {}))))
                return
            job = self.jobs[path.split("/")[3]]
            if job["kind"] == "stage-approval":
                self.batches[NAME]["approval"] = {"pr_number": 42, "pr_url": "https://github.com/example/fixture/pull/42", "head_sha": "a" * 40, "revision": self.batches[NAME]["revision"], "state": "open"}
            elif job["kind"] == "approve":
                self.batches[NAME]["approval"]["state"] = "merged"
            elif job["kind"] == "prepare":
                self.batches[NAME]["readiness"].update(ready=True, media_ready=True)
                for image in self.batches[NAME]["images"]:
                    image["status"] = "done"
            elif job["kind"] == "reschedule" and self.reschedule_target:
                data = self.batches.pop(NAME)
                data["name"] = self.reschedule_target
                data["approval"] = None
                self.batches[self.reschedule_target] = data
                job["result"] = {"queue": self.reschedule_target, "previous_queue": NAME}
            elif job["kind"] == "refresh-delivery":
                data = self.batches[NAME]
                data["delivery"] = copy.deepcopy(self.refreshed_delivery)
                data["workflow"] = copy.deepcopy(self.refreshed_workflow)
                data["observed_at"] = "2099-09-06T19:00:00Z"
                job["result"] = {"queue": NAME, "workflow": data["workflow"], "observed_at": data["observed_at"]}
            self.active_job = None
            self.respond(route, {**job, "status": "done", "next": 1, "lines": ["Fixture complete"], "result": job.get("result", {})})
            return
        if path.startswith("/api/queue/"):
            parts = path.split("/")
            data = self.batches[parts[3]]
            if len(parts) == 4:
                if self.hold_queue == parts[3]:
                    self.held.append((route, copy.deepcopy(data)))
                    return
                self.respond(route, data)
                return
            action = parts[4]
            if request.method == "GET" and action == "download":
                route.fulfill(content_type="application/zip", headers={"Content-Disposition": 'attachment; filename="fixture.zip"'}, body=b"fixture")
                return
            if request.headers.get("x-layer8-csrf") != "fixture-csrf":
                self.respond(route, {"error": "Missing CSRF"}, 403)
                return
            if request.headers.get("if-match") != data["revision"]:
                self.respond(route, {"error": "Stale revision"}, 409)
                return
            body = request.post_data_json if request.headers.get("content-type") == "application/json" else {}
            if action in ("stage-approval", "approve", "prepare", "reschedule", "refresh-delivery"):
                job = {"id": f"job-{action}", "kind": action, "queue": parts[3], "status": "running"}
                self.jobs[job["id"]] = job
                self.respond(route, {"job": job})
                return
            if action == "upload":
                if self.upload_error:
                    self.respond(route, {"error": "Unreadable image; no source accepted."}, 400)
                    return
                pending = next(image for image in data["images"] if image["status"] == "pending")
                pending.update(status="ready", original="/api/media/original/upload.png")
                self.bump(data)
                self.respond(route, {"matched": [pending["image_id"]], "staged": [], "skipped": []})
                return
            if action == "replace":
                image = next(image for image in data["images"] if image["image_id"] == request.headers["x-image-id"])
                image.update(status="ready", has_history=True, original="/api/media/original/replacement.png")
                data["readiness"].update(ready=False, media_ready=False)
            elif action == "undo-image":
                image = next(image for image in data["images"] if image["image_id"] == body["image_id"])
                image.update(status="done", has_history=False)
            elif action == "edit-post":
                item = next(item for item in data["posts"] if item["id"] == body["post_id"])
                item.update(body["changes"])
            elif action == "edit-image":
                image = next(image for image in data["images"] if image["image_id"] == body["image_id"])
                image.update(headline=body["headline"], subtext=body["subtext"])
            elif action == "assign":
                image = next(image for image in data["images"] if image["image_id"] == body["image_id"])
                image.update(status="ready", original="/api/media/original/assigned.png")
                data["staged"] = []
            self.bump(data)
            self.respond(route, {"ok": True})
            return
        self.respond(route, {"error": f"Unexpected fixture request: {path}"}, 404)

    def open_batch(self):
        self.page.goto(URL)
        self.page.locator(f'[data-queue="{NAME}"]').click()
        expect(self.page.locator("#batch-name")).to_have_text(NAME)

    def posts(self, action):
        return [request for request in self.requests if request[0] == "POST" and request[1].endswith(f"/{action}")]

    def test_all_slides_complete_text_mobile_and_keyboard_dialog(self):
        self.batches[NAME]["posts"][0]["previews"].insert(0, copy.deepcopy(self.batches[NAME]["posts"][0]["previews"][0]))
        self.open_batch()
        carousel = self.page.locator('[data-post-id="post-1"]')
        expect(carousel.locator("figure")).to_have_count(7)
        self.assertEqual(carousel.locator("figcaption").all_text_contents(), [f"Slide {i} of 7 - Headline {i}" for i in range(1, 8)])
        expect(carousel.locator(".caption")).to_contain_text("Last sentence stays visible.")
        expect(carousel.locator(".first-comment")).to_have_text("The full first comment.")
        expect(self.page.locator("#review-list")).to_contain_text("A deliberate title")
        expect(carousel.locator("time")).to_contain_text("Eastern Daylight Time")
        expect(carousel.locator("time")).to_contain_text("America/New_York")
        self.page.set_viewport_size({"width": 390, "height": 844})
        for screen in ("batches", "images", "prepare", "review", "delivery"):
            self.page.locator(f'[data-screen="{screen}"]').click()
            self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"), screen)
        self.page.locator('[data-screen="review"]').click()
        opener = carousel.locator(".media-button").nth(4)
        opener.focus()
        self.page.keyboard.press("Enter")
        expect(self.page.locator("#media-dialog")).to_be_visible()
        expect(self.page.locator("#media-comparison img")).to_have_count(2)
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.page.keyboard.press("Escape")
        expect(self.page.locator("#media-dialog")).not_to_be_visible()
        expect(opener).to_be_focused()
        self.assertFalse(self.posts("approve"))

    def test_sequential_individual_import_refreshes_each_revision(self):
        data = self.batches[NAME]
        data["readiness"].update(ready=False, media_ready=False)
        for image in data["images"][:2]:
            image.update(status="pending", original=None, preview=None)
        data["prompt_groups"] = [{"index": 0, "image_ids": [image["image_id"] for image in data["images"][:2]], "prompt": "Two exact prompts"}]
        self.open_batch()
        expect(self.page.locator("#next-action")).to_have_text("Copy this prompt group")
        self.assertEqual(self.page.locator("#group-images .image-card").count(), 2)
        upload = self.page.locator("#image-files")
        upload.focus()
        expect(upload).to_be_focused()
        upload.set_input_files([{"name": f"image {i}.png", "mimeType": "image/png", "buffer": PNG} for i in (1, 2)])
        expect(self.page.locator("#next-action")).to_have_text("Continue to previews")
        requests = self.posts("upload")
        self.assertEqual([item[2]["if-match"] for item in requests], ["r1", "r2"])
        self.assertEqual([item[2]["x-filename"] for item in requests], ["image%201.png", "image%202.png"])
        self.assertTrue(all(item[2]["x-layer8-csrf"] == "fixture-csrf" for item in requests))
        self.page.locator("#next-action").click()
        self.page.locator("#next-action").click()
        expect(self.page.locator("#screen-review")).to_be_visible()
        self.assertEqual(len(self.posts("prepare")), 1)

    def test_stage_then_explicit_exact_approval_and_honest_delivery(self):
        self.open_batch()
        self.page.locator("#next-action").click()
        expect(self.page.locator("#next-action")).to_have_text("Approve displayed revision")
        self.assertEqual(len(self.posts("stage-approval")), 1)
        self.assertEqual(self.posts("approve"), [])
        self.page.locator("#next-action").click()
        expect(self.page.locator("#approve-submit")).to_be_disabled()
        expect(self.page.locator("#approve-identity")).to_contain_text("a" * 40)
        self.page.locator("#approve-confirm").check()
        self.page.locator("#approve-submit").click()
        expect(self.page.locator("#screen-delivery")).to_be_visible()
        request = self.posts("approve")[0]
        self.assertEqual(json.loads(request[3]), {"pr_number": 42, "head_sha": "a" * 40, "revision": "r1"})
        expect(self.page.locator("#delivery-list")).to_contain_text("No provider receipts recorded")
        self.assertFalse(any(path.endswith("/publish") for _, path, _, _ in self.requests))

    def test_replacement_undo_caption_edit_and_download(self):
        self.open_batch()
        self.page.locator('[data-post-id="post-1"] .media-button').first.click()
        self.page.locator("[data-replace-image]").set_input_files({"name": "replacement.webp", "mimeType": "image/webp", "buffer": PNG})
        expect(self.page.locator("#media-dialog")).not_to_be_visible()
        self.assertEqual(self.posts("replace")[0][2]["x-image-id"], "post-1-1")
        self.page.locator('[data-screen="prepare"]').click()
        self.page.locator('#prepare-images [data-action="compare"]').first.click()
        self.page.get_by_role("button", name="Undo last replacement").click()
        expect(self.page.locator("#media-dialog")).not_to_be_visible()
        self.assertEqual(len(self.posts("undo-image")), 1)
        self.page.locator('[data-screen="review"]').click()
        self.page.locator('[data-post-id="post-1"] [data-action="edit-post"]').click()
        self.page.locator('[name="text"]').fill("An edited complete caption.")
        self.page.locator('[name="first_comment"]').fill("An edited first comment.")
        self.page.locator('[name="hashtags"]').fill("#One #Two")
        self.page.get_by_role("button", name="Save changes").click()
        expect(self.page.locator("#edit-dialog")).not_to_be_visible()
        expect(self.page.locator('[data-post-id="post-1"] .caption')).to_have_text("An edited complete caption.")
        payload = json.loads(self.posts("edit-post")[0][3])
        self.assertEqual(payload["changes"]["hashtags"], ["#One", "#Two"])
        self.batches[NAME]["readiness"].update(media_ready=True, ready=True)
        self.page.locator("#refresh-batch").click()
        expect(self.page.locator("#download-batch")).to_be_visible()
        with self.page.expect_download():
            self.page.locator("#download-batch").click()

    def test_expired_schedule_requires_deliberate_reschedule(self):
        self.batches[NAME]["readiness"].update(ready=False, schedule_ready=False, blockers=[{"code": "schedule_expired", "detail": "The previous date has expired."}])
        self.open_batch()
        expect(self.page.locator("#next-action")).to_have_text("Choose a fresh schedule")
        expect(self.page.locator("#readiness")).to_contain_text("expired")
        self.page.locator("#next-action").click()
        self.page.locator("#reschedule-date").fill("2099-10-01")
        self.page.get_by_role("button", name="Apply new schedule").click()
        expect(self.page.locator("#reschedule-dialog")).not_to_be_visible()
        self.assertEqual(json.loads(self.posts("reschedule")[0][3]), {"date": "2099-10-01"})
        self.assertEqual(self.posts("approve"), [])

    def test_reload_recovers_job_with_single_inflight_poll(self):
        self.open_batch()
        self.active_job = "resume-job"
        self.jobs[self.active_job] = {"id": self.active_job, "kind": "prepare", "queue": NAME}
        self.hold_jobs = True
        self.page.reload()
        expect(self.page.locator("#batch-name")).to_have_text(NAME)
        self.page.wait_for_timeout(1300)
        requests = [item for item in self.requests if item[1] == "/api/jobs/resume-job"]
        self.assertEqual(len(requests), 1, "Slow job polling must not overlap.")
        expect(self.page.locator("#next-action")).to_be_disabled()
        self.hold_jobs = False
        self.active_job = None
        route, job = self.held.pop()
        self.respond(route, {**job, "status": "done", "next": 1, "lines": ["Resumed fixture"], "result": {}})
        expect(self.page.locator("#next-action")).to_be_enabled()
        self.assertEqual(self.posts("prepare"), [])
        self.assertEqual(self.page.evaluate("localStorage.getItem('layer8-guided-batch')"), NAME)
        self.assertIsNone(self.page.evaluate("localStorage.getItem('csrf')"))

    def test_reschedule_without_explicit_flag_cannot_be_cancelled(self):
        self.open_batch()
        self.active_job = "reschedule-job"
        self.jobs[self.active_job] = {"id": self.active_job, "kind": "reschedule", "queue": NAME}
        self.hold_jobs = True
        self.page.reload()
        expect(self.page.locator("#batch-name")).to_have_text(NAME)
        route, job = self.held.pop()
        self.respond(route, {**job, "status": "running", "next": 0, "lines": [], "result": {}})
        expect(self.page.locator("#job-status")).to_contain_text("Planning schedule: running")
        expect(self.page.locator("#cancel-job")).not_to_be_visible()
        message = self.page.evaluate("cancelJob().catch(error => error.message)")
        self.assertIn("cannot be cancelled safely", message)
        self.assertEqual(self.posts("cancel"), [])

    def test_successful_reschedule_selects_returned_filename(self):
        self.reschedule_target = "2099-10-01.json"
        self.batches[NAME]["readiness"].update(ready=False, schedule_ready=False)
        self.open_batch()
        self.page.locator("#next-action").click()
        self.page.locator("#reschedule-date").fill("2099-10-01")
        self.page.get_by_role("button", name="Apply new schedule").click()
        expect(self.page.locator("#batch-name")).to_have_text(self.reschedule_target)
        self.assertEqual(self.page.evaluate("localStorage.getItem('layer8-guided-batch')"), self.reschedule_target)
        self.assertIsNone(self.batches[self.reschedule_target]["approval"])

    def test_late_queue_response_cannot_overwrite_new_selection(self):
        self.page.add_init_script("window.AbortController = class { abort() {} };")
        self.hold_queue = NAME
        self.page.goto(URL)
        self.page.locator(f'[data-queue="{NAME}"]').click()
        self.page.locator(f'[data-queue="{OTHER}"]').click()
        expect(self.page.locator("#batch-name")).to_have_text(OTHER)
        self.assertEqual(len(self.held), 1)
        route, old = self.held.pop()
        self.respond(route, old)
        self.page.wait_for_timeout(100)
        expect(self.page.locator("#batch-name")).to_have_text(OTHER)
        self.assertEqual(self.page.evaluate("localStorage.getItem('layer8-guided-batch')"), OTHER)

    def test_conflicting_approval_fails_visible_without_retry(self):
        self.open_batch()
        self.page.locator("#next-action").click()
        expect(self.page.locator("#next-action")).to_have_text("Approve displayed revision")
        self.page.locator("#next-action").click()
        self.page.locator("#approve-confirm").check()
        self.batches[NAME]["revision"] = "r2"
        self.page.locator("#approve-submit").click()
        expect(self.page.locator("#error-text")).to_contain_text("Stale revision")
        expect(self.page.locator("#approve-dialog")).not_to_be_visible()
        self.assertEqual(len(self.posts("approve")), 1)
        expect(self.page.locator("#next-action")).to_have_text("Stage approval PR")

    def test_four_image_groups_zip_partial_import_and_matching(self):
        data = self.batches[NAME]
        data["readiness"].update(ready=False, media_ready=False)
        for image in data["images"]:
            image.update(status="pending", original=None, preview=None)
        data["prompt_groups"] = [
            {"index": 0, "image_ids": [image["image_id"] for image in data["images"][:4]], "prompt": "Group one"},
            {"index": 1, "image_ids": [image["image_id"] for image in data["images"][4:]], "prompt": "Group two"},
        ]
        self.open_batch()
        expect(self.page.locator("#group-images .image-card")).to_have_count(4)
        expect(self.page.get_by_role("link", name="Open ChatGPT")).to_have_attribute("href", "https://chatgpt.com/")
        self.page.locator("#image-files").set_input_files({"name": "partial pack.zip", "mimeType": "application/zip", "buffer": b"fixture-zip"})
        expect(self.page.locator("#group-progress")).to_contain_text("1 of 4 sources imported")
        self.assertEqual(self.posts("upload")[0][2]["content-type"], "application/zip")
        self.assertEqual(self.posts("upload")[0][2]["x-filename"], "partial%20pack.zip")
        data["staged"] = [{"file": "unmatched.png", "url": "/api/media/staged/fixture.png", "reason": "Check the scene", "size": "1024x1536"}]
        self.page.locator("#refresh-batch").click()
        expect(self.page.locator("#next-action")).to_have_text("Match imported images")
        self.page.locator("#assignment-0").select_option("post-1-2")
        self.page.get_by_role("button", name="Assign image").click()
        expect(self.page.locator("#reconcile")).not_to_be_visible()
        self.assertEqual(json.loads(self.posts("assign")[0][3]), {"file": "unmatched.png", "image_id": "post-1-2"})
        self.page.locator("#prompt-group").select_option("1")
        expect(self.page.locator("#group-images .image-card")).to_have_count(3)
        expect(self.page.locator("#group-progress")).to_contain_text("Group 2")

    def test_upload_failure_stops_remaining_files_and_never_enables_approval(self):
        data = self.batches[NAME]
        data["readiness"].update(ready=False, media_ready=False)
        for image in data["images"][:2]:
            image.update(status="pending", original=None, preview=None)
        self.upload_error = True
        self.open_batch()
        self.page.locator("#image-files").set_input_files([{"name": f"bad-{i}.png", "mimeType": "image/png", "buffer": PNG} for i in (1, 2)])
        expect(self.page.locator("#error-text")).to_contain_text("Unreadable image")
        expect(self.page.locator("#upload-report")).to_contain_text("remaining files were not retried")
        self.assertEqual(len(self.posts("upload")), 1)
        self.page.locator('[data-screen="review"]').click()
        expect(self.page.locator("#next-action")).to_have_text("Prepare missing previews")
        self.assertEqual(self.posts("stage-approval"), [])

    def test_restore_is_explicit_and_uses_session_header(self):
        self.open_batch()
        self.page.locator('[data-screen="batches"]').click()
        self.page.locator("#trash-details summary").click()
        expect(self.page.locator("[data-restore]")).to_be_visible()
        self.assertEqual(self.posts("restore"), [])
        self.page.locator("[data-restore]").click()
        expect(self.page.locator("#notice")).to_contain_text("Restored the removed item")
        request = self.posts("restore")[0]
        self.assertEqual(request[2]["x-layer8-csrf"], "fixture-csrf")
        self.assertEqual(json.loads(request[3]), {"entry": "removed-fixture"})

    def test_receipt_helper_states_keep_private_visibility_distinct(self):
        self.batches[NAME]["delivery"] = [
            {"id": "post-2", "delivery_status": "queued", "visibility": "private", "delivery_mode": "upload", "postiz_post_id": "receipt-123", "workflow_url": "https://github.com/example/fixture/actions/runs/1"},
            {"id": "post-1", "delivery_status": "not_submitted", "source": "none"},
            {"id": "post-3", "state": "submission_pending", "detail": "Refresh to check for a submission receipt."},
        ]
        self.open_batch()
        self.page.locator('[data-screen="delivery"]').click()
        expect(self.page.locator("#delivery-list")).to_contain_text("Provider queued")
        expect(self.page.locator("#delivery-list")).to_contain_text("Visibility: private - not a public post.")
        expect(self.page.locator("#delivery-list")).to_contain_text("Not submitted")
        expect(self.page.locator("#delivery-list")).to_contain_text("Awaiting submission evidence")
        expect(self.page.get_by_role("link", name="View workflow")).to_be_visible()
        self.assertEqual(self.posts("approve"), [])

    def test_explicit_delivery_refresh_watches_job_and_shows_workflow_failure(self):
        self.open_batch()
        self.page.locator('[data-screen="delivery"]').click()
        expect(self.page.locator("#delivery-workflow")).to_contain_text("No publishing workflow status observed")
        self.page.locator("#refresh-batch").click()
        expect(self.page.locator("#next-action")).to_be_enabled()
        self.assertEqual(self.posts("refresh-delivery"), [], "Reading the local cache must not fetch remote evidence.")
        self.page.locator("#next-action").click()
        expect(self.page.locator("#delivery-workflow")).to_contain_text("Publishing workflow needs attention: failure")
        expect(self.page.locator("#screen-delivery")).to_be_visible()
        expect(self.page.locator("#delivery-list")).to_contain_text("No provider receipts recorded")
        expect(self.page.locator("#delivery-workflow")).to_contain_text("Evidence observed")
        expect(self.page.locator("#delivery-workflow")).to_contain_text("not a live provider check")
        expect(self.page.locator("#delivery-workflow a")).to_have_attribute("href", self.refreshed_workflow["url"])
        request = self.posts("refresh-delivery")[0]
        self.assertEqual(request[2]["x-layer8-csrf"], "fixture-csrf")
        self.assertEqual(request[2]["if-match"], "r1")
        self.assertEqual(json.loads(request[3]), {})
        self.assertTrue(any(path == "/api/jobs/job-refresh-delivery" for _, path, _, _ in self.requests))
        self.assertEqual(len(self.posts("refresh-delivery")), 1)
        self.assertEqual([path for method, path, _, _ in self.requests if method == "POST"], [f"/api/queue/{NAME}/refresh-delivery"])
        expect(self.page.get_by_role("button", name="Retry", exact=True)).to_have_count(0)

    def test_delivery_refresh_labels_new_receipts_as_observed_not_live(self):
        self.refreshed_workflow["conclusion"] = "success"
        self.refreshed_delivery = [{"id": "post-1", "state": "queued", "provider_id": "observed-123", "source": "receipt", "detail": "Accepted in the publishing receipt."}]
        self.open_batch()
        self.page.locator('[data-screen="delivery"]').click()
        self.page.locator("#next-action").click()
        expect(self.page.locator("#delivery-list")).to_contain_text("Observed receipt status")
        expect(self.page.locator("#delivery-list")).to_contain_text("Provider queued")
        expect(self.page.locator("#delivery-workflow")).to_contain_text("workflow success alone is not proof of publication")
        self.page.reload()
        self.page.locator('[data-screen="delivery"]').click()
        expect(self.page.locator("#delivery-list")).to_contain_text("Observed receipt status")
        self.assertEqual(len(self.posts("refresh-delivery")), 1, "Reload must not repeat the explicit refresh job.")


if __name__ == "__main__":
    unittest.main()
