import copy
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import changed_queue_files as changed
import post_to_ghl as ghl
import post_to_postiz as postiz
import postiz_dedupe
import publish_helpers as helpers


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        self.addCleanup(patch.stopall)
        self.approval = {"state": "approved", "commit": "a" * 40, "revision": "approved-revision"}
        patch.object(requests.sessions.Session, "send", side_effect=AssertionError("Live provider requests forbidden")).start()
        patch.object(postiz, "require_approved_payload", return_value=self.approval).start()
        patch.object(ghl, "require_approved_payload", return_value=self.approval).start()
        patch.object(postiz, "require_publish_ready", return_value={"ready": True, "revision": "approved-revision"}).start()
        patch.object(ghl, "require_publish_ready", return_value={"ready": True, "revision": "approved-revision"}).start()
        patch.object(postiz, "POSTIZ_URL", "https://example.invalid").start()
        patch.object(postiz, "HEADERS", {"Authorization": "synthetic"}).start()
        patch.dict(os.environ, {
            "GHL_ACCESS_TOKEN": "synthetic", "GHL_LOCATION_ID": "location",
            "GHL_DEALLAB_INSTAGRAM_ACCOUNT_ID": "deallab-ig",
            "GHL_USER_ID": "synthetic-user",
            "GHL_MEDIA_BASE_URL": "https://example.invalid/media",
        }).start()
        (self.root / "queue").mkdir()
        self.qpath = self.root / "queue" / "2035-07-01.json"

    def post(self, number=1, account="layer8culture", platform="instagram"):
        post_id = f"post-{number}"
        ext = "mp4" if platform in {"tiktok", "youtube"} else "png"
        relative = f"assets/generated/{post_id}.{ext}"
        path = self.root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"synthetic-{number}".encode())
        return {
            "id": post_id, "account": account, "platform": platform,
            "format": "reel" if ext == "mp4" else "single",
            "schedule_time": f"2035-07-01T{8 + number:02d}:00:00-04:00",
            "text": f"Caption {number}", "visual": {"source": "openai", "file": relative},
        }

    def write_queue(self, posts):
        self.qpath.write_text(json.dumps(posts), encoding="utf-8")

    def accept(self, post, before_submit=None, **_kwargs):
        if before_submit:
            before_submit({"integration_id": "instagram-channel"})
        return {
            "scheduled": True, "delivery_status": "queued",
            "postiz_post_id": "remote-" + post["id"], "publisher": "postiz",
        }

    def test_failed_result_can_append_a_success_without_editing_history(self):
        old = {"id": "p", "scheduled": False, "skip_reason": "postiz_error"}
        history = [copy.deepcopy(old)]
        helpers.append_new_log_records(history, [{"id": "p", "scheduled": True}])
        self.assertEqual(history[0], old)
        self.assertEqual(len(history), 2)
        self.assertIn("p", helpers.scheduled_post_ids(history))

    def test_both_direct_publishers_reject_unapproved_commit_before_all_provider_calls(self):
        import approval_guard

        for module, entry, account in (
            (postiz, postiz.main, "layer8culture"), (ghl, ghl.process_queue, "deallab"),
        ):
            self.write_queue([self.post(account=account)])
            with self.subTest(publisher=account), patch.object(
                module, "require_approved_payload", side_effect=helpers.require_approved_payload,
            ), patch.object(approval_guard, "verify", side_effect=ValueError("never-approved or uncommitted payload")) as verify, patch.object(
                module, "schedule",
            ) as schedule, patch.object(requests, "post") as create, patch.object(requests, "get") as lookup:
                with self.assertRaisesRegex(ValueError, "never-approved"):
                    entry(str(self.qpath), repo_root=self.root, commit="a" * 40)
                verify.assert_called_once_with(self.qpath, "a" * 40, repo_root=self.root)
                schedule.assert_not_called()
                create.assert_not_called()
                lookup.assert_not_called()
        self.assertTrue(self.qpath.exists())
        self.assertFalse((self.root / "posted").exists())

    def test_direct_publishers_require_exact_commit_and_ignore_untrusted_environment(self):
        import approval_guard

        for module, entry, account in (
            (postiz, postiz.main, "layer8culture"), (ghl, ghl.process_queue, "deallab"),
        ):
            self.write_queue([self.post(account=account)])
            with patch.object(module, "require_approved_payload", side_effect=helpers.require_approved_payload), patch.dict(
                os.environ, {"GITHUB_ACTIONS": "false", "GITHUB_SHA": "a" * 40, "DELIVERY_APPROVAL_COMMIT": "a" * 40},
            ), patch.object(approval_guard, "verify") as verify, patch.object(module, "schedule") as schedule:
                for commit in (None, "main", True):
                    with self.subTest(publisher=account, commit=commit), self.assertRaises(ValueError):
                        entry(str(self.qpath), repo_root=self.root, commit=commit)
                verify.assert_not_called()
                schedule.assert_not_called()

    def test_approved_direct_publishers_still_submit_and_bind_receipt_to_verified_commit(self):
        import approval_guard

        for number, module, entry, account in (
            (1, postiz, postiz.main, "layer8culture"), (2, ghl, ghl.process_queue, "deallab"),
        ):
            self.write_queue([self.post(number, account=account)])

            def schedule(post, before_submit=None, **_kwargs):
                if module is postiz:
                    before_submit({"integration_id": "instagram-channel"})
                else:
                    before_submit()
                return {"delivery_status": "queued", "scheduled": True, "postiz_post_id": "remote"}

            with self.subTest(publisher=account), patch.object(
                module, "require_approved_payload", side_effect=helpers.require_approved_payload,
            ), patch.object(approval_guard, "verify", return_value=self.approval) as verify, patch.object(
                module, "schedule", side_effect=schedule,
            ) as provider, patch.dict(os.environ, {"DELIVERY_APPROVAL_COMMIT": "b" * 40}):
                result = entry(str(self.qpath), repo_root=self.root, commit="a" * 40)
                verify.assert_called_once_with(self.qpath, "a" * 40, repo_root=self.root)
                provider.assert_called_once()
                self.assertEqual(result[0]["delivery_status"], "queued")
                self.assertEqual(result[0]["commit"], "a" * 40)

    def test_trusted_github_sha_is_verified_not_used_as_a_bypass(self):
        import approval_guard

        self.write_queue([self.post()])
        with patch.object(postiz, "require_approved_payload", side_effect=helpers.require_approved_payload), patch.dict(
            os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_SHA": "a" * 40},
        ), patch.object(approval_guard, "verify", return_value=self.approval) as verify, patch.object(
            postiz, "schedule", side_effect=self.accept,
        ):
            postiz.main(str(self.qpath), repo_root=self.root)
        verify.assert_called_once_with(self.qpath, "a" * 40, repo_root=self.root)

    def test_changed_payload_after_provenance_check_blocks_each_adapter(self):
        import batch_readiness

        for module, entry, account in (
            (postiz, postiz.main, "layer8culture"), (ghl, ghl.process_queue, "deallab"),
        ):
            self.write_queue([self.post(account=account)])
            with self.subTest(publisher=account), patch.object(
                module, "require_publish_ready", side_effect=helpers.require_publish_ready,
            ), patch.object(batch_readiness, "report", return_value={"ready": True, "revision": "changed"}), patch.object(
                module, "schedule",
            ) as provider:
                with self.assertRaisesRegex(ValueError, "changed after merged-PR verification"):
                    entry(str(self.qpath), repo_root=self.root, commit="a" * 40)
                provider.assert_not_called()

    def test_status_log_path_contract_handles_missing_history_without_network(self):
        post = self.post()
        result = helpers.delivery_status([post], self.root / "posted" / "log.json")[0]
        self.assertEqual(result["state"], "submission_pending")
        self.assertIn("refresh remote receipts", result["detail"])
        self.assertTrue(result["remote_refresh_required"])
        self.assertFalse(result["live_status"])
        self.assertNotIn("provider_id", result)
        self.assertFalse((self.root / "posted").exists())

    def test_asset_only_approval_selects_only_its_referencing_queue(self):
        post = self.post()
        self.write_queue([post])
        other = self.post(2, account="lofi")
        (self.root / "queue" / "lofi-2035-07-01.json").write_text(json.dumps([other]), encoding="utf-8")
        selected = changed.git_changed_queue_files(changed_paths=[post["visual"]["file"]], repo_root=self.root)
        self.assertEqual(selected, ["queue/2035-07-01.json"])

    def test_asset_only_trigger_uses_exact_event_commit_range(self):
        post = self.post()
        self.write_queue([post])
        calls = []

        def diff(before, after):
            calls.append((before, after))
            return [post["visual"]["file"]]

        selected = changed.queue_files_for_publish(
            {"before": "a" * 40, "after": "b" * 40}, False, git_fallback=diff,
        )
        self.assertEqual(calls, [("a" * 40, "b" * 40)])
        self.assertEqual(selected, ["queue/2035-07-01.json"])

    def test_media_helpers_resolve_explicit_data_root_from_code_directory(self):
        post = self.post()
        os.chdir(ROOT)
        expected = str(self.root / post["visual"]["file"])
        self.assertEqual(helpers.resolve_local_paths(post, self.root), [expected])
        self.assertEqual(helpers.missing_local_paths(post, self.root), [])

    def test_postiz_reads_and_archives_only_explicit_data_root(self):
        post = self.post()
        self.write_queue([post])
        os.chdir(ROOT)
        response = Mock(ok=True)
        response.json.return_value = {"postId": "data-root-post", "status": "scheduled"}
        with patch.object(postiz, "find_existing_postiz_duplicate", return_value=None), patch.object(postiz, "upload_media", return_value={"id": "media", "path": "/image.png"}) as upload, patch.object(requests, "post", return_value=response):
            result = postiz.main("queue/2035-07-01.json", repo_root=self.root)
        upload.assert_called_once_with(str(self.root / post["visual"]["file"]))
        self.assertEqual(result[0]["postiz_post_id"], "data-root-post")
        self.assertFalse(self.qpath.exists())
        self.assertTrue((self.root / "posted" / self.qpath.name).is_file())
        self.assertEqual(helpers.delivery_status([post], self.root / "posted" / "log.json")[0]["state"], "queued")

    def test_publisher_uses_data_root_environment_when_not_explicit(self):
        post = self.post()
        self.write_queue([post])
        os.chdir(ROOT)
        with patch.dict(os.environ, {"LAYER8_DATA_ROOT": str(self.root)}), patch.object(postiz, "schedule", side_effect=self.accept) as schedule:
            postiz.main("queue/2035-07-01.json")
        self.assertEqual(schedule.call_args.kwargs["repo_root"], self.root)
        self.assertTrue((self.root / "posted" / self.qpath.name).is_file())

    def test_ghl_data_root_produces_relative_public_urls(self):
        post = self.post(account="deallab")
        self.write_queue([post])
        os.chdir(ROOT)
        response = Mock(ok=True)
        response.json.return_value = {"id": "ghl-data-post", "status": "scheduled"}
        with patch.object(ghl, "matching_existing", return_value=None), patch.object(requests, "post", return_value=response) as create:
            result = ghl.process_queue("queue/2035-07-01.json", repo_root=self.root)
        payload = create.call_args.kwargs["json"]
        self.assertEqual(payload["media"][0]["url"], "https://example.invalid/media/assets/generated/post-1.png")
        self.assertEqual(result[0]["ghl_post_id"], "ghl-data-post")
        self.assertTrue((self.root / "posted" / self.qpath.name).is_file())

    def test_status_reads_successful_retry_from_adjacent_receipts(self):
        post = self.post()
        fingerprint = helpers.post_fingerprint(post, self.root)
        helpers.append_receipt(self.root, post, {"delivery_status": "failed"}, fingerprint)
        helpers.append_receipt(self.root, post, {
            "delivery_status": "queued", "postiz_post_id": "remote-retry",
            "workflow_url": "https://github.com/example/repository/actions/runs/123",
        }, fingerprint)
        result = helpers.delivery_status([post], self.root / "posted" / "log.json")[0]
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["provider_id"], "remote-retry")
        self.assertTrue(result["url"].endswith("/123"))
        self.assertIn("not been refreshed", result["detail"])

    def test_mismatched_receipt_does_not_assign_provider_id_to_current_draft(self):
        post = self.post()
        helpers.append_receipt(self.root, post, {
            "delivery_status": "queued", "postiz_post_id": "older-revision",
        }, helpers.post_fingerprint(post, self.root))
        post["text"] = "A different draft"
        result = helpers.delivery_status([post], self.root / "posted" / "log.json")[0]
        self.assertEqual(result["state"], "revision_changed")
        self.assertNotIn("provider_id", result)
        self.assertEqual(result["previous_provider_id"], "older-revision")
        self.assertIn("not confirmed submitted", result["detail"])

    def test_legacy_success_is_visible_but_current_revision_stays_unknown(self):
        post = self.post()
        log = self.root / "posted" / "log.json"
        log.parent.mkdir()
        log.write_text(json.dumps([
            {"id": post["id"], "scheduled": False, "skip_reason": "postiz_error"},
            {"id": post["id"], "scheduled": True, "postiz_post_id": "legacy-success"},
        ]), encoding="utf-8")
        result = helpers.delivery_status([post], log)[0]
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["last_recorded_state"], "accepted")
        self.assertIn("latest legacy receipt reports accepted", result["detail"])
        self.assertNotIn("provider_id", result)

    def test_partial_timeout_keeps_success_and_unknown_receipts_and_legacy_bytes(self):
        posts = [self.post(1), self.post(2)]
        self.write_queue(posts)
        (self.root / "posted").mkdir()
        legacy = self.root / "posted" / "log.json"
        legacy.write_bytes(b'[\n  {"id": "legacy", "scheduled": true}\n]\n')
        before = legacy.read_bytes()

        def schedule(post, before_submit=None, **kwargs):
            if post["id"] == "post-1":
                return self.accept(post, before_submit, **kwargs)
            before_submit({"integration_id": "instagram-channel"})
            raise requests.Timeout("synthetic timeout")

        with patch.object(postiz, "schedule", side_effect=schedule), self.assertRaises(SystemExit):
            postiz.main(str(self.qpath))
        self.assertTrue(self.qpath.exists())
        self.assertEqual(legacy.read_bytes(), before)
        latest = helpers.latest_records(helpers.load_delivery_records(self.root))
        self.assertEqual(latest["post-1"]["delivery_status"], "queued")
        self.assertEqual(latest["post-2"]["delivery_status"], "unknown")
        self.assertEqual(latest["post-2"]["skip_reason"], "unknown_submission")
        self.assertEqual(latest["post-2"]["integration_id"], "instagram-channel")
        attempts = [row for row in helpers.load_delivery_records(self.root) if row["id"] == "post-2"]
        self.assertEqual(attempts[0]["attempt_id"], attempts[1]["attempt_id"])

    def test_recovery_reconciles_unknown_and_never_reposts_accepted_post(self):
        posts = [self.post(1), self.post(2)]
        self.write_queue(posts)
        for post, status in zip(posts, ("queued", "unknown")):
            helpers.append_receipt(self.root, post, {
                "scheduled": status == "queued", "delivery_status": status, "publisher": "postiz",
            }, helpers.post_fingerprint(post, self.root))
        candidate = {"id": "remote-2", "state": "QUEUE"}
        with patch.object(postiz, "find_existing_postiz_duplicate", return_value=candidate) as lookup, patch.object(postiz, "upload_media") as upload, patch.object(requests, "post") as create:
            result = postiz.main(str(self.qpath))
        self.assertEqual(lookup.call_count, 1)
        upload.assert_not_called()
        create.assert_not_called()
        self.assertTrue(all(row["delivery_status"] == "queued" for row in result))
        self.assertFalse(self.qpath.exists())

    def test_unknown_without_remote_match_never_blindly_retries(self):
        post = self.post()
        self.write_queue([post])
        helpers.append_receipt(self.root, post, {"delivery_status": "submitting"}, helpers.post_fingerprint(post, self.root))
        with patch.object(postiz, "find_existing_postiz_duplicate", return_value=None), patch.object(postiz, "upload_media") as upload, patch.object(requests, "post") as create:
            for _ in range(2):
                with self.assertRaises(SystemExit):
                    postiz.main(str(self.qpath))
        upload.assert_not_called()
        create.assert_not_called()
        self.assertTrue(self.qpath.exists())

    def test_success_without_post_id_is_unknown_not_media_id(self):
        post = self.post()
        response = Mock(ok=True)
        response.json.return_value = {"integration": {"id": "not-a-post"}, "media": [{"id": "not-a-post-either"}]}
        before_submit = Mock()
        with patch.object(postiz, "find_existing_postiz_duplicate", return_value=None), patch.object(postiz, "upload_media", return_value={"id": "media", "path": "/image.png"}), patch.object(requests, "post", return_value=response):
            result = postiz.schedule(post, before_submit=before_submit)
        before_submit.assert_called_once()
        self.assertEqual(result["delivery_status"], "unknown")
        self.assertFalse(result["scheduled"])

    def test_missing_post_id_can_be_reconciled_after_acceptance(self):
        post = self.post()
        response = Mock(ok=True)
        response.json.return_value = {}
        with patch.object(postiz, "find_existing_postiz_duplicate", side_effect=[None, {"id": "remote", "state": "QUEUE"}]), patch.object(postiz, "upload_media", return_value={"id": "media", "path": "/image.png"}), patch.object(requests, "post", return_value=response):
            result = postiz.schedule(post, before_submit=Mock())
        self.assertEqual(result["postiz_post_id"], "remote")
        self.assertEqual(result["delivery_status"], "queued")

    def test_lost_configuration_does_not_clear_an_uncertain_attempt(self):
        post = self.post(platform="youtube")
        self.write_queue([post])
        helpers.append_receipt(self.root, post, {"delivery_status": "unknown"}, helpers.post_fingerprint(post, self.root))
        with patch.dict(postiz.INTEGRATIONS, {("layer8culture", "youtube"): "REPLACE_ME"}), self.assertRaises(SystemExit):
            postiz.main(str(self.qpath))
        self.assertEqual(helpers.delivery_status([post], self.root)[0]["delivery_status"], "unknown")
        self.assertTrue(self.qpath.exists())

    def test_post_identifiers_do_not_use_envelope_or_multiple_post_ids(self):
        self.assertEqual(helpers.response_post_id({"id": "envelope", "posts": [{"id": "post"}]}), "post")
        self.assertIsNone(helpers.response_post_id({"posts": [{"id": "one"}, {"id": "two"}]}))
        self.assertIsNone(helpers.response_post_id({"integration": {"id": "not-a-post"}}))

    def test_provider_draft_or_error_is_not_reported_as_queued(self):
        post = self.post()
        draft = postiz.accepted_result(post, {"provider_status": "DRAFT", "postiz_post_id": "draft"})
        failed = postiz.accepted_result(post, {"provider_status": "ERROR", "postiz_post_id": "failed"})
        self.assertFalse(draft["scheduled"])
        self.assertEqual(draft["delivery_status"], "unknown")
        self.assertFalse(failed["scheduled"])
        self.assertEqual(failed["delivery_status"], "failed")

    def test_changed_revision_cannot_erase_prior_acceptance_and_resubmit(self):
        post = self.post()
        helpers.append_receipt(self.root, post, self.accept(post), helpers.post_fingerprint(post, self.root))
        post["text"] = "New approved text still requires reconciling the old remote post"
        self.write_queue([post])
        with patch.object(postiz, "schedule") as schedule:
            for _ in range(2):
                with self.assertRaises(SystemExit):
                    postiz.main(str(self.qpath))
        schedule.assert_not_called()
        self.assertEqual(helpers.delivery_status([post], self.root)[0]["delivery_status"], "revision_changed")

    def test_preflight_blocks_all_provider_access(self):
        self.write_queue([self.post()])
        with patch.object(postiz, "require_publish_ready", side_effect=ValueError("expired")), patch.object(postiz, "schedule") as schedule, self.assertRaises(ValueError):
            postiz.main(str(self.qpath))
        schedule.assert_not_called()
        self.assertFalse((self.root / "posted").exists())

    def test_shared_preflight_contract_requires_future_media_ready_boolean(self):
        import batch_readiness

        post = self.post()
        self.write_queue([post])
        with patch.object(batch_readiness, "report", return_value={"ready": True}) as report:
            helpers.require_publish_ready(self.qpath, self.root)
        report.assert_called_once_with(self.qpath, self.root, now=None, require_future=True)
        with patch.object(batch_readiness, "report", return_value={"ready": False, "blockers": ["expired"]}), self.assertRaises(ValueError):
            helpers.require_publish_ready(self.qpath, self.root)

    def test_real_preflight_rejects_expired_unprepared_queue_without_provider_calls(self):
        post = self.post()
        post["schedule_time"] = "2020-07-01T09:00:00-04:00"
        self.write_queue([post])
        with patch.object(postiz, "require_publish_ready", side_effect=helpers.require_publish_ready), patch.object(postiz, "schedule") as schedule, self.assertRaises(ValueError):
            postiz.main(str(self.qpath))
        schedule.assert_not_called()

    def test_receipts_survive_failure_to_write_a_later_receipt(self):
        posts = [self.post(1), self.post(2)]
        self.write_queue(posts)
        real_append = helpers.append_receipt

        def append(root, post, result, fingerprint):
            if post["id"] == "post-2":
                raise OSError("disk full")
            return real_append(root, post, result, fingerprint)

        with patch.object(postiz, "schedule", side_effect=self.accept), patch.object(postiz, "append_receipt", side_effect=append), self.assertRaises(OSError):
            postiz.main(str(self.qpath))
        latest = helpers.latest_records(helpers.load_delivery_records(self.root))
        self.assertEqual(latest["post-1"]["delivery_status"], "queued")

    def test_inbox_cap_counts_other_batch_receipts_and_unknown_attempts(self):
        with patch.dict(postiz.INTEGRATIONS, {("layer8culture", "tiktok"): "tt"}):
            for index in range(1, 6):
                post = self.post(index, platform="tiktok")
                helpers.append_receipt(self.root, post, {
                    "integration_id": "tt", "delivery_mode": "inbox",
                    "delivery_status": "unknown" if index == 5 else "queued",
                }, helpers.post_fingerprint(post, self.root))
            candidate = self.post(6, platform="tiktok")
            self.assertTrue(postiz.inbox_limit_reached(candidate, helpers.load_delivery_records(self.root)))
            candidate["schedule_time"] = "2035-07-03T14:00:00-04:00"
            self.assertFalse(postiz.inbox_limit_reached(candidate, helpers.load_delivery_records(self.root)))

    def test_status_distinguishes_inbox_private_and_provider_queue(self):
        tiktok = self.post(platform="tiktok")
        result = postiz.accepted_result(tiktok, {"provider_status": "QUEUE", "postiz_post_id": "remote"})
        self.assertEqual(result["delivery_status"], "queued")
        self.assertEqual(result["delivery_mode"], "inbox")
        self.assertEqual(result["visibility"], "private")
        youtube = self.post(2, platform="youtube")
        youtube["youtube_settings"] = {"type": "private"}
        result = postiz.accepted_result(youtube, {"provider_status": "PUBLISHED", "postiz_post_id": "remote"})
        self.assertEqual(result["delivery_status"], "private")

    def test_missing_optional_channel_is_explicit_nonfatal_skip(self):
        post = self.post(platform="youtube")
        self.write_queue([post])
        with patch.dict(postiz.INTEGRATIONS, {("layer8culture", "youtube"): "REPLACE_ME"}):
            result = postiz.main(str(self.qpath))
        self.assertEqual(result[0]["delivery_status"], "skipped")
        self.assertEqual(result[0]["skip_reason"], "missing_integration")

    def test_malformed_receipt_fails_closed(self):
        path = self.root / "posted" / "receipts" / "corrupt.json"
        path.parent.mkdir(parents=True)
        path.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            helpers.load_delivery_records(self.root)

    def test_ghl_is_restricted_to_client_lane_before_network(self):
        self.write_queue([self.post()])
        with patch.object(ghl, "schedule") as schedule, self.assertRaises(ValueError):
            ghl.process_queue(str(self.qpath))
        schedule.assert_not_called()

    def test_ghl_timeout_receipt_blocks_automatic_resubmission(self):
        post = self.post(account="deallab")
        self.write_queue([post])

        def timeout(_post, before_submit=None, **_kwargs):
            before_submit()
            raise requests.Timeout("synthetic")

        with patch.object(ghl, "schedule", side_effect=timeout), self.assertRaises(SystemExit):
            ghl.process_queue(str(self.qpath))
        with patch.object(ghl, "matching_existing", return_value=None), patch.object(requests, "post") as create, self.assertRaises(SystemExit):
            ghl.process_queue(str(self.qpath))
        create.assert_not_called()
        self.assertEqual(helpers.delivery_status([post], self.root)[0]["delivery_status"], "unknown")

    def test_ghl_payload_preserves_comment_and_uses_explicit_public_media(self):
        post = self.post(account="deallab")
        post["first_comment"] = "First comment"
        payload = ghl.ghl_payload(post, [post["visual"]["file"]])
        self.assertEqual(payload["summary"], post["text"])
        self.assertEqual(payload["followUpComment"], "First comment")
        self.assertEqual(payload["accountIds"], ["deallab-ig"])
        self.assertEqual(payload["userId"], "synthetic-user")
        self.assertTrue(payload["media"][0]["url"].startswith("https://example.invalid/media/assets/"))

    def test_ghl_caption_hashtags_stay_in_caption_and_comment_stays_separate(self):
        post = self.post(account="deallab")
        post.update(first_comment="Approved comment", hashtags=["#DealLab", "#RealEstate"])
        payload = ghl.ghl_payload(post, [post["visual"]["file"]])
        self.assertEqual(payload["summary"], post["text"] + "\n\n#DealLab #RealEstate")
        self.assertEqual(payload["followUpComment"], "Approved comment")

    def test_ghl_comment_hashtags_stay_out_of_caption(self):
        post = self.post(account="deallab")
        post.update(first_comment="Approved comment", hashtags=["#DealLab"], hashtags_in_first_comment=True)
        payload = ghl.ghl_payload(post, [post["visual"]["file"]])
        self.assertEqual(payload["summary"], post["text"])
        self.assertEqual(payload["followUpComment"], "Approved comment\n\n#DealLab")

    def test_ghl_hashtag_only_comment_and_caption_limits_remain_separate(self):
        post = self.post(account="deallab")
        post.update(text="x" * 2200, hashtags=["#DealLab"], hashtags_in_first_comment=True)
        payload = ghl.ghl_payload(post, [post["visual"]["file"]])
        self.assertEqual(payload["summary"], "x" * 2200)
        self.assertEqual(payload["followUpComment"], "#DealLab")

    def test_ghl_reconciliation_rejects_different_approved_comment(self):
        post = self.post(account="deallab")
        post["first_comment"] = "Approved comment"
        response = Mock()
        response.json.return_value = {"posts": [{
            "id": "existing", "accountIds": ["deallab-ig"], "status": "scheduled",
            "summary": post["text"], "scheduleDate": post["schedule_time"],
            "followUpComment": "A different comment",
        }]}
        with patch.object(requests, "post", return_value=response), self.assertRaisesRegex(ValueError, "first comment"):
            ghl.matching_existing(post)


class DiscoveryAndWorkflowTests(unittest.TestCase):
    def test_dedupe_apply_requires_explicit_scope(self):
        with patch.object(sys, "argv", ["postiz_dedupe.py", "--apply"]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            postiz_dedupe.parse_args()

    def test_invalid_commit_ranges_fail_closed(self):
        for event in ({"before": "a" * 40}, {"before": "0" * 40, "after": "b" * 40}, {"before": "HEAD~1", "after": "HEAD"}):
            with self.subTest(event=event), self.assertRaises(ValueError):
                changed.queue_files_for_publish(event, False)

    def test_git_failure_is_not_converted_to_empty_success(self):
        with patch.object(changed, "git_changed_paths", side_effect=RuntimeError("missing commit")), self.assertRaises(RuntimeError):
            changed.queue_files_for_publish({"before": "a" * 40, "after": "b" * 40}, False)

    def test_only_direct_queue_files_are_selected(self):
        files = changed.git_changed_queue_files(
            exists=lambda _: True,
            changed_paths=["queue/../secret.json", "queue/sub/post.json", "queue/good.json", "queue/good.summary.md"],
        )
        self.assertEqual(files, ["queue/good.json"])

    def test_workflow_requires_provenance_and_preserves_receipts_on_failure(self):
        workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
        self.assertIn('python scripts/approval_guard.py "$QUEUE_FILE" --commit "$APPROVAL_COMMIT"', workflow)
        self.assertIn('CHANGED_FILES="$(python scripts/changed_queue_files.py)"', workflow)
        self.assertNotIn("< <(python", workflow)
        self.assertNotIn("postiz_dedupe.py --apply", workflow)
        self.assertIn("name: Commit receipts and completed archives\n        if: always()", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("pip install openai requests pillow", workflow)
        self.assertIn('"assets/generated/**"', workflow)
        self.assertIn('python scripts/post_to_postiz.py "$QUEUE_FILE" --commit "$APPROVAL_COMMIT"', workflow)
        self.assertIn('python scripts/post_to_ghl.py "$QUEUE_FILE" --commit "$APPROVAL_COMMIT"', workflow)
        self.assertIn("RECOVERY_APPROVAL_COMMIT", workflow)


if __name__ == "__main__":
    unittest.main()
