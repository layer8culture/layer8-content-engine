import json
from unittest.mock import patch

from PIL import Image

if __package__:
    from .test_ship_queue import RepoTestCase, approval_guard, batch_readiness, git, ship_queue
else:
    from test_ship_queue import RepoTestCase, approval_guard, batch_readiness, git, ship_queue


class ApprovalTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.qpath = self.repo.add_batch()
        self.prepared = self.ship()

    def test_approval_merges_only_exact_head_without_bypass_or_auto_merge(self):
        result = self.approve(self.prepared)
        self.assertEqual(result["state"], "merged")
        merge = next(args for args in self.github.calls if args[:2] == ["pr", "merge"])
        self.assertIn("--match-head-commit", merge)
        self.assertNotIn("--admin", merge)
        self.assertNotIn("--auto", merge)
        self.assertTrue(self.github.merged)

    def test_caption_or_media_edit_invalidates_approval(self):
        posts = json.loads(self.qpath.read_text())
        posts[0]["text"] = "Changed after review"
        self.qpath.write_text(json.dumps(posts), encoding="utf-8")
        with self.assertRaisesRegex(ship_queue.ShipError, "revision changed"):
            self.approve(self.prepared)
        posts[0]["text"] = "Caption"
        self.qpath.write_text(json.dumps(posts, indent=2), encoding="utf-8")
        Image.new("RGB", (32, 32), "red").save(self.repo.root / posts[0]["visual"]["file"])
        with self.assertRaisesRegex(ship_queue.ShipError, "revision changed"):
            self.approve(self.prepared)
        self.assertFalse(self.github.merged)

    def test_changed_pr_head_blocks_approval(self):
        git([f"--git-dir={self.repo.remote}", "update-ref", "refs/heads/posts/2030-09-05",
             self.repo.initial_main], self.repo.root)
        with self.assertRaisesRegex(ship_queue.ShipError, "head changed"):
            self.approve(self.prepared)
        self.assertFalse(self.github.merged)

    def test_pending_failed_skipped_or_spoofed_readiness_blocks_approval(self):
        for status, conclusion, app in [
            ("in_progress", None, "github-actions"),
            ("completed", "failure", "github-actions"),
            ("completed", "skipped", "github-actions"),
            ("completed", "success", "other-app"),
        ]:
            with self.subTest(status=status, conclusion=conclusion, app=app):
                self.github.check_status, self.github.check_conclusion = status, conclusion
                self.github.check_app = app
                with self.assertRaisesRegex(ship_queue.ShipError, "Batch readiness"):
                    self.approve(self.prepared)
        self.assertFalse(self.github.merged)

    def test_failed_pending_or_skipped_other_required_checks_block(self):
        for state, bucket in (("FAILURE", "fail"), ("PENDING", "pending"), ("SKIPPED", "skipping")):
            with self.subTest(state=state):
                self.github.required = [{"name": "required-unit", "state": state, "bucket": bucket}]
                with self.assertRaisesRegex(ship_queue.ShipError, "failed or pending"):
                    self.approve(self.prepared)
        self.assertFalse(self.github.merged)

    def test_review_or_branch_protection_block_is_not_bypassed(self):
        self.github.policy = {"mergeStateStatus": "BLOCKED", "reviewDecision": "REVIEW_REQUIRED"}
        with self.assertRaisesRegex(ship_queue.ShipError, "branch protection"):
            self.approve(self.prepared)
        self.assertFalse(self.github.merged)

    def test_expired_schedule_during_approval_blocks_merge(self):
        real = batch_readiness.report
        calls = 0
        def expires(*args, **kwargs):
            nonlocal calls
            result = real(*args, **kwargs)
            calls += 1
            if calls >= 3:
                result.update(ready=False, schedule_ready=False, blockers=["schedule expired"])
            return result
        with patch.object(batch_readiness, "report", side_effect=expires):
            with self.assertRaisesRegex(ship_queue.ShipError, "schedule expired"):
                self.approve(self.prepared)
        self.assertFalse(self.github.merged)


class ProvenanceTests(RepoTestCase):
    def test_clean_approved_snapshot_is_ready_without_manual_sources_or_receipts(self):
        qpath = self.repo.add_batch()
        posts = json.loads(qpath.read_text(encoding="utf-8"))
        posts[0]["visual"]["openai_prompt"] = "A cinematic room"
        qpath.write_text(json.dumps(posts), encoding="utf-8")
        result = self.ship()
        self.approve(result)
        with approval_guard.commit_worktree(self.repo.root, result["head_sha"]) as checkout:
            self.assertFalse((checkout / ".local" / "media").exists())
            self.assertFalse((checkout / "assets" / "manual-inbox").exists())
            ready = batch_readiness.report(checkout / "queue" / qpath.name, checkout)
            self.assertTrue(ready["ready"], ready["blockers"])
            self.assertEqual(ready["revision"], result["revision"])
            self.assertFalse((checkout / ".local" / "media").exists())

    def test_recovery_accepts_older_approved_commit_after_receipt_only_commit(self):
        self.repo.add_batch()
        result = self.ship()
        self.approve(result)
        with approval_guard.commit_worktree(self.repo.root, result["head_sha"]) as checkout:
            receipts = checkout / "posted"
            receipts.mkdir()
            (receipts / "receipt.json").write_text('{"state":"queued"}', encoding="utf-8")
            git(["add", "posted"], checkout)
            git(["commit", "-m", "persist delivery receipt"], checkout)
            self.assertNotEqual(git(["rev-parse", "HEAD"], checkout), result["head_sha"])
            proof = approval_guard.verify("queue/2030-09-05.json", result["head_sha"],
                                          repo_root=checkout)
            self.assertEqual(proof["state"], "approved")
            self.assertEqual(proof["commit"], result["head_sha"])

    def test_merged_nightly_pr_with_matching_payload_is_valid(self):
        self.repo.add_batch()
        result = self.ship()
        # Human GitHub merge is sufficient; no app receipt or app merge call.
        pr = self.github.prs[7]
        pr.update(state="closed", merged=True, merged_at="2030-09-05T01:00:00Z",
                  merge_commit_sha=result["head_sha"])
        proof = approval_guard.verify("queue/2030-09-05.json", result["head_sha"],
                                      repo_root=self.repo.root)
        self.assertEqual(proof["state"], "approved")
        self.assertEqual(proof["head_sha"], result["head_sha"])
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.github.calls))

    def test_direct_main_push_is_not_approval(self):
        self.repo.add_batch()
        git(["add", "-f", "queue", "assets/generated"], self.repo.root)
        git(["commit", "-m", "direct main content"], self.repo.root)
        git(["push", "origin", "main"], self.repo.root)
        commit = self.repo.remote_sha("main")
        with self.assertRaisesRegex(approval_guard.ApprovalError, "no approved provenance"):
            approval_guard.verify("queue/2030-09-05.json", commit, repo_root=self.repo.root)

    def test_open_unmerged_pr_is_not_provenance(self):
        self.repo.add_batch()
        result = self.ship()
        with self.assertRaisesRegex(approval_guard.ApprovalError, "no approved provenance"):
            approval_guard.verify("queue/2030-09-05.json", result["head_sha"],
                                  repo_root=self.repo.root)

    def test_local_changed_asset_does_not_match_workflow_commit(self):
        qpath = self.repo.add_batch()
        result = self.ship()
        self.approve(result)
        entry = json.loads(qpath.read_text())[0]
        Image.new("RGB", (32, 32), "red").save(self.repo.root / entry["visual"]["file"])
        with self.assertRaisesRegex(approval_guard.ApprovalError, "differs from the workflow"):
            approval_guard.verify("queue/2030-09-05.json", result["head_sha"],
                                  repo_root=self.repo.root)

    def test_post_merge_direct_edit_cannot_borrow_previous_pr_approval(self):
        qpath = self.repo.add_batch()
        result = self.ship()
        self.approve(result)
        git(["fetch", "origin", "main"], self.repo.root)
        with approval_guard.commit_worktree(self.repo.root, result["head_sha"]) as worktree:
            entries = json.loads(qpath.read_text())
            entries[0]["text"] = "Unapproved change"
            changed = json.dumps(entries)
            (worktree / "queue" / qpath.name).write_text(changed, encoding="utf-8")
            qpath.write_text(changed, encoding="utf-8")
            git(["add", "queue"], worktree)
            git(["commit", "-m", "unapproved"], worktree)
            git(["push", "origin", "HEAD:main"], worktree)
            commit = git(["rev-parse", "HEAD"], worktree)
        with self.assertRaisesRegex(approval_guard.ApprovalError, "reviewed head"):
            approval_guard.verify("queue/2030-09-05.json", commit, repo_root=self.repo.root)
