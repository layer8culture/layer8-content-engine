import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import delivery_refresh
import publish_helpers


def git(root, *args):
    result = subprocess.run(
        ["git", "--no-pager", "-c", "core.longpaths=true", *args],
        cwd=str(root), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class DeliveryRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.remote = self.base / "remote.git"
        self.author = self.base / "author"
        self.root = self.base / "data"
        git(self.base, "init", "--bare", "-b", "main", str(self.remote))
        git(self.base, "init", "-b", "main", str(self.author))
        git(self.author, "config", "user.name", "fixture")
        git(self.author, "config", "user.email", "fixture@example.test")
        git(self.author, "remote", "add", "origin", str(self.remote))
        self.post = {
            "id": "20350701-layer8culture-instagram-1", "account": "layer8culture",
            "platform": "instagram", "format": "single",
            "schedule_time": "2035-07-01T09:00:00-04:00", "text": "Reviewed caption",
            "visual": {"source": "openai", "file": "assets/generated/selected.png"},
        }
        self.queue = "queue/2035-07-01.json"
        for name in ("queue", "assets/generated", "posted"):
            (self.author / name).mkdir(parents=True, exist_ok=True)
        (self.author / self.queue).write_text(json.dumps([self.post], indent=2), encoding="utf-8")
        (self.author / self.post["visual"]["file"]).write_bytes(b"immutable image fixture")
        (self.author / "posted" / "log.json").write_bytes(b'[\n  {"id":"unrelated","scheduled":true}\n]\n')
        (self.author / "README.md").write_text("base\n", encoding="utf-8")
        (self.author / ".gitignore").write_text(".local/\n", encoding="utf-8")
        self.seed_sha = self.push("seed")
        git(self.base, "clone", str(self.remote), str(self.root))
        (self.root / "README.md").write_text("user's uncommitted edit\n", encoding="utf-8")
        (self.root / "user-notes.txt").write_text("untracked user work\n", encoding="utf-8")
        self.qpath = self.root / self.queue
        self.log = self.root / "posted" / "log.json"
        self.initial_log = self.log.read_bytes()
        self.initial_head = git(self.root, "rev-parse", "HEAD")
        self.initial_status = git(self.root, "status", "--porcelain", "--untracked-files=all")
        self.run = {
            "id": 123, "path": ".github/workflows/publish.yml", "head_sha": self.seed_sha,
            "status": "completed", "conclusion": "success", "event": "push",
            "created_at": "2026-09-05T10:00:00Z", "updated_at": "2026-09-05T10:01:00Z",
            "run_attempt": 1,
        }
        self.pr = {
            "number": 7, "url": "https://github.com/example/engine/pull/7",
            "state": "MERGED", "mergedAt": "2026-09-05T09:59:00Z",
            "mergeCommit": {"oid": self.seed_sha}, "headRefOid": self.seed_sha,
            "baseRefName": "main", "isCrossRepository": False,
            "files": [{"path": self.queue}],
        }
        self.approval = {"pr_number": 7, "head_sha": self.seed_sha, "revision": "reviewed", "state": "awaiting_approval"}
        self.runs = None
        self.gh = patch.object(delivery_refresh, "_gh", side_effect=self.fake_gh).start()
        self.addCleanup(patch.stopall)

    def push(self, message):
        git(self.author, "add", "-A")
        git(self.author, "commit", "-m", message)
        git(self.author, "push", "origin", "main")
        return git(self.author, "rev-parse", "HEAD")

    def fake_gh(self, root, args):
        self.assertEqual(Path(root), self.root)
        if args[:2] == ["repo", "view"]:
            return {"nameWithOwner": "example/engine"}
        if args[:2] == ["pr", "view"]:
            return copy.deepcopy(self.pr)
        if args[0] == "api" and args[-1].endswith("/pulls"):
            return [{"number": 7, "base": {"ref": "main"}}]
        if args[:2] == ["api", "repos/example/engine/actions/runs/123"]:
            return copy.deepcopy(self.run)
        if "repos/example/engine/actions/workflows/publish.yml/runs" in args:
            return {"workflow_runs": copy.deepcopy(self.runs if self.runs is not None else [self.run])}
        raise AssertionError(f"Unexpected mocked GitHub read: {args}")

    def remote_receipt(self, *, post=None, status="queued", recorded_at=None):
        post = post or self.post
        outcome = {
            "publisher": "postiz", "delivery_status": status,
            "scheduled": status in publish_helpers.ACCEPTED_STATUSES,
        }
        if status in publish_helpers.ACCEPTED_STATUSES:
            outcome["postiz_post_id"] = "remote-post"
        with patch.dict(os.environ, {
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_REPOSITORY": "example/engine", "GITHUB_SHA": self.seed_sha,
        }):
            record = publish_helpers.append_receipt(
                self.author, post, outcome, publish_helpers.post_fingerprint(post, self.author),
            )
        if recorded_at:
            record["recorded_at"] = recorded_at
            path = next((self.author / "posted" / "receipts").rglob(f"*{record['event_id']}.json"))
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        self.push("append receipt")
        return record

    def assert_user_work_untouched(self):
        self.assertEqual(self.log.read_bytes(), self.initial_log)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.initial_head)
        self.assertEqual(git(self.root, "status", "--porcelain", "--untracked-files=all"), self.initial_status)
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "user's uncommitted edit\n")
        self.assertEqual((self.root / "user-notes.txt").read_text(encoding="utf-8"), "untracked user work\n")

    def test_remote_only_receipt_becomes_visible_without_worktree_update_or_get_network(self):
        self.remote_receipt()
        self.assertFalse((self.root / "posted" / "receipts").exists())
        self.assertEqual(publish_helpers.delivery_status([self.post], self.log)[0]["state"], "submission_pending")
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(result["receipt_count"], 1)
        self.assertEqual(result["approval"]["state"], "merged")
        self.assertEqual(result["workflow"]["conclusion"], "success")
        with patch.object(delivery_refresh, "_command", side_effect=AssertionError("GET must not run commands")), patch.object(delivery_refresh, "_gh", side_effect=AssertionError("GET must not use GitHub")):
            status = publish_helpers.delivery_status([self.post], self.log)[0]
        self.assertEqual(status["state"], "queued")
        self.assertEqual(status["provider_id"], "remote-post")
        self.assertEqual(status["receipt_source"], "remote")
        self.assertFalse(status["live_status"])
        self.assertFalse((self.root / "posted" / "receipts").exists())
        self.assert_user_work_untouched()

    def test_only_selected_posts_are_retrieved_and_cached(self):
        self.remote_receipt()
        other = self.author / "posted" / "receipts" / "123-1" / "unrelated.json"
        other.write_text('{"id": "another-post", "broken": true}', encoding="utf-8")
        self.push("unrelated receipt")
        result = delivery_refresh.refresh(self.qpath, self.root, None)
        cache = json.loads((self.root / result["cache_path"]).read_text(encoding="utf-8"))
        self.assertEqual([row["id"] for row in cache["receipts"]], [self.post["id"]])
        self.assertEqual(cache["legacy_records"], [])
        self.assert_user_work_untouched()

    def test_cache_stays_ignored_without_changing_data_repository_ignore_rules(self):
        (self.root / ".gitignore").write_text("# user's unchanged ignore policy\n", encoding="utf-8")
        self.initial_status = git(self.root, "status", "--porcelain", "--untracked-files=all")
        self.remote_receipt()
        result = delivery_refresh.refresh(self.qpath, self.root, None)
        self.assertEqual(git(self.root, "check-ignore", result["cache_path"]), result["cache_path"])
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"), "# user's unchanged ignore policy\n")
        self.assert_user_work_untouched()

    def test_successful_workflow_without_receipt_never_becomes_provider_queued(self):
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(result["workflow"]["conclusion"], "success")
        status = publish_helpers.delivery_status([self.post], self.log)[0]
        self.assertEqual(status["state"], "unknown")
        self.assertNotIn("provider_id", status)
        self.assertIn("green workflow", status["detail"])

    def test_failed_workflow_without_receipt_is_persisted_as_unknown_provider_outcome(self):
        self.run["conclusion"] = "failure"
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(result["receipt_count"], 0)
        self.assertEqual(result["workflow"]["conclusion"], "failure")
        status = publish_helpers.delivery_status([self.post], self.log)[0]
        self.assertEqual(status["state"], "unknown")
        self.assertTrue(status["workflow_failed"])
        self.assertIn("failure", status["detail"])
        self.assertIn("reconcile before retrying", status["detail"])
        self.assertEqual(status["url"], "https://github.com/example/engine/actions/runs/123")
        self.assert_user_work_untouched()

    def test_failed_batch_workflow_does_not_erase_this_posts_successful_receipt(self):
        self.remote_receipt()
        self.run["conclusion"] = "failure"
        delivery_refresh.refresh(self.qpath, self.root, self.approval)
        status = publish_helpers.delivery_status([self.post], self.log)[0]
        self.assertEqual(status["state"], "queued")
        self.assertTrue(status["workflow_failed"])
        self.assertEqual(status["provider_id"], "remote-post")

    def test_absent_workflow_stays_pending(self):
        self.runs = []
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(result["workflow"]["status"], "pending")
        self.assertEqual(publish_helpers.delivery_status([self.post], self.log)[0]["state"], "submission_pending")

    def test_unrelated_dispatch_at_same_sha_is_not_attributed_to_selected_queue(self):
        other = {**self.run, "id": 456, "event": "workflow_dispatch", "conclusion": "failure",
                 "created_at": "2026-09-05T12:00:00Z"}
        self.runs = [self.run, other]
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(result["workflow"]["id"], 123)
        self.assertEqual(result["workflow"]["conclusion"], "success")

    def test_workflow_failure_remains_visible_when_pr_association_is_unavailable(self):
        self.run["conclusion"] = "failure"
        original = self.fake_gh

        def gh(root, args):
            if args[0] == "api" and args[-1].endswith("/pulls"):
                return []
            return original(root, args)

        self.gh.side_effect = gh
        result = delivery_refresh.refresh(self.qpath, self.root, {"merge_sha": self.seed_sha})
        self.assertEqual(result["approval"]["state"], "unknown")
        self.assertEqual(result["workflow"]["conclusion"], "failure")
        self.assertTrue(publish_helpers.delivery_status([self.post], self.log)[0]["workflow_failed"])

    def test_revision_changes_never_inherit_cached_submission_or_workflow_state(self):
        self.remote_receipt()
        self.run["conclusion"] = "failure"
        delivery_refresh.refresh(self.qpath, self.root, self.approval)
        changed = {**self.post, "text": "A new, unapproved caption"}
        self.qpath.write_text(json.dumps([changed]), encoding="utf-8")
        status = publish_helpers.delivery_status([changed], self.log)[0]
        self.assertEqual(status["state"], "revision_changed")
        self.assertNotIn("provider_id", status)
        self.assertEqual(status["previous_provider_id"], "remote-post")
        self.assertNotIn("workflow_failed", status)

    def test_new_remote_successful_retry_replaces_cached_failure(self):
        self.remote_receipt(status="failed", recorded_at="2026-09-05T10:00:00+00:00")
        delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(publish_helpers.delivery_status([self.post], self.log)[0]["state"], "failed")
        self.remote_receipt(recorded_at="2026-09-05T10:01:00+00:00")
        delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(publish_helpers.delivery_status([self.post], self.log)[0]["state"], "queued")
        self.assert_user_work_untouched()

    def test_newer_local_event_is_not_overwritten_by_old_remote_cache(self):
        self.remote_receipt(recorded_at="2026-01-01T10:00:00+00:00")
        delivery_refresh.refresh(self.qpath, self.root, self.approval)
        publish_helpers.append_receipt(self.root, self.post, {
            "delivery_status": "failed", "scheduled": False, "skip_reason": "provider_error",
        }, publish_helpers.post_fingerprint(self.post, self.root))
        self.assertEqual(publish_helpers.delivery_status([self.post], self.log)[0]["state"], "failed")
        self.assertEqual(self.log.read_bytes(), self.initial_log)

    def test_remote_legacy_success_is_visible_but_not_verified_for_current_media(self):
        path = self.author / "posted" / "log.json"
        path.write_text(json.dumps([
            {"id": "unrelated", "scheduled": True},
            {"id": self.post["id"], "scheduled": True, "postiz_post_id": "legacy-post"},
        ]), encoding="utf-8")
        self.push("legacy evidence")
        result = delivery_refresh.refresh(self.qpath, self.root, None)
        self.assertEqual(result["legacy_record_count"], 1)
        status = publish_helpers.delivery_status([self.post], self.log)[0]
        self.assertEqual(status["state"], "unknown")
        self.assertEqual(status["last_recorded_state"], "accepted")
        self.assertNotIn("provider_id", status)
        self.assert_user_work_untouched()

    def test_fetch_failure_raises_without_exposing_stderr_or_replacing_cache(self):
        self.remote_receipt()
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        path = self.root / result["cache_path"]
        before = path.read_bytes()
        failure = Mock(returncode=128, stdout="", stderr="https://DO_NOT_EXPOSE@github.com/private/repository")
        with patch.object(subprocess, "run", return_value=failure), self.assertRaises(delivery_refresh.RefreshError) as error:
            delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertIn("fetch failed", str(error.exception))
        self.assertNotIn("DO_NOT_EXPOSE", str(error.exception))
        self.assertEqual(path.read_bytes(), before)
        self.assert_user_work_untouched()

    def test_github_failure_keeps_previous_cache_and_raises(self):
        self.remote_receipt()
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        path = self.root / result["cache_path"]
        before = path.read_bytes()
        with patch.object(delivery_refresh, "_gh", side_effect=delivery_refresh.RefreshError("GitHub read failed")), self.assertRaises(delivery_refresh.RefreshError):
            delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(path.read_bytes(), before)
        self.assert_user_work_untouched()

    def test_malformed_selected_receipt_is_an_error_not_success(self):
        self.remote_receipt()
        path = self.author / "posted" / "receipts" / "123-1" / "invalid.json"
        path.write_text(json.dumps({"id": self.post["id"], "event_id": "bad"}), encoding="utf-8")
        self.push("bad selected evidence")
        with self.assertRaises(delivery_refresh.RefreshError):
            delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertFalse((self.root / ".local" / "remote-delivery").exists())
        self.assert_user_work_untouched()

    def test_cache_corruption_is_not_read_as_delivery_evidence(self):
        self.remote_receipt()
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        path = self.root / result["cache_path"]
        value = json.loads(path.read_text(encoding="utf-8"))
        value["receipts"][0]["postiz_post_id"] = "tampered"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(delivery_refresh.RefreshError):
            publish_helpers.delivery_status([self.post], self.log)

    def test_stale_pr_head_does_not_update_saved_approval_to_merged(self):
        self.pr["headRefOid"] = "b" * 40
        result = delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertEqual(result["approval"]["state"], "stale")
        self.assertEqual(result["approval"]["head_sha"], self.seed_sha)
        self.assertNotIn("merge_sha", result["approval"])
        self.assertIsNone(result["workflow"])

    def test_unrelated_pr_is_rejected(self):
        self.pr["files"] = [{"path": "queue/unrelated.json"}]
        with self.assertRaises(delivery_refresh.RefreshError):
            delivery_refresh.refresh(self.qpath, self.root, self.approval)
        self.assertFalse((self.root / ".local" / "remote-delivery").exists())

    def test_head_sha_can_resolve_known_approval_pr(self):
        result = delivery_refresh.refresh(self.qpath, self.root, {"head_sha": self.seed_sha})
        self.assertEqual(result["approval"]["pr_number"], 7)
        self.assertEqual(result["approval"]["state"], "merged")

    def test_outside_queue_is_rejected_before_fetch(self):
        with patch.object(delivery_refresh, "_command") as command, self.assertRaises(delivery_refresh.RefreshError):
            delivery_refresh.refresh(self.root / "README.md", self.root, None)
        command.assert_not_called()

    def test_media_outside_assets_is_not_read_or_fetched(self):
        post = {**self.post, "visual": {"file": "README.md"}}
        self.qpath.write_text(json.dumps([post]), encoding="utf-8")
        with patch.object(delivery_refresh, "_command") as command, patch.object(publish_helpers, "post_fingerprint") as fingerprint, self.assertRaises(delivery_refresh.RefreshError):
            delivery_refresh.refresh(self.qpath, self.root, None)
        command.assert_not_called()
        fingerprint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
