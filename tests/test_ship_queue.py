import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import approval_guard
import batch_readiness
import ship_queue


def git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def post(post_id="20300905-layer8culture-instagram-1", fmt="single", **overrides):
    visual = {"source": "openai", "file": f"assets/generated/{post_id}.png"}
    if fmt == "carousel":
        visual.update(slides=[{"openai_prompt": "Scene"} for _ in range(3)],
                      files=[f"assets/generated/{post_id}-{n}.png" for n in (1, 2, 3)])
        visual["file"] = visual["files"][0]
    if fmt == "reel":
        visual["file"] = f"assets/generated/{post_id}.mp4"
        visual["cover"] = f"assets/generated/{post_id}-cover.png"
    result = {"id": post_id, "account": "layer8culture", "platform": "instagram",
              "format": fmt, "schedule_time": "2030-09-05T09:00:00-04:00",
              "text": "Caption", "visual": visual}
    result.update(overrides)
    return result


class ShipQueueRepo:
    """Only disposable local Git remotes; GitHub calls are always mocked."""
    def __init__(self, directory):
        directory = pathlib.Path(directory)
        self.remote, self.root = directory / "remote.git", directory / "work"
        git(["init", "--bare", "-b", "main", str(self.remote)], directory)
        git(["init", "-b", "main", str(self.root)], directory)
        git(["config", "user.name", "test"], self.root)
        git(["config", "user.email", "test@example.com"], self.root)
        git(["remote", "add", "origin", str(self.remote)], self.root)
        (self.root / "queue").mkdir()
        (self.root / "assets" / "generated").mkdir(parents=True)
        (self.root / ".gitignore").write_text("assets/generated/*\n.local/\n", encoding="utf-8")
        (self.root / "README.md").write_text("seed\n", encoding="utf-8")
        git(["add", "-A"], self.root)
        git(["commit", "-m", "seed"], self.root)
        git(["push", "origin", "main"], self.root)
        self.initial_main = self.remote_sha("main")

    def remote_sha(self, branch):
        return git([f"--git-dir={self.remote}", "rev-parse", branch], self.root)

    def remote_files(self, branch="main"):
        return set(git([f"--git-dir={self.remote}", "ls-tree", "-r", "--name-only", branch],
                       self.root).splitlines())

    def add_batch(self, posts=None, name="2030-09-05.json", render=True):
        posts = posts or [post()]
        qpath = self.root / "queue" / name
        qpath.write_text(json.dumps(posts, indent=2), encoding="utf-8")
        qpath.with_suffix(".summary.md").write_text("# Summary\n", encoding="utf-8")
        if render:
            for entry in posts:
                for rel in ship_queue.declared_media(entry):
                    path = self.root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path.suffix == ".mp4":
                        subprocess.run(
                            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                             "color=c=blue:s=32x32:d=0.12", "-pix_fmt", "yuv420p", "-y", str(path)],
                            check=True, capture_output=True,
                        )
                        Image.new("RGB", (32, 32), "blue").save(path.with_name(path.stem + "-cover.png"))
                    else:
                        Image.new("RGB", (32, 32), "blue").save(path)
        return qpath


class FakeGitHub:
    def __init__(self, repo):
        self.repo = repo
        self.prs = {}
        self.calls = []
        self.check_status = "completed"
        self.check_conclusion = "success"
        self.required = [{"name": "unit", "state": "SUCCESS", "bucket": "pass"}]
        self.policy = {"mergeStateStatus": "CLEAN", "reviewDecision": ""}
        self.merged = False
        self.associated = True
        self.check_app = "github-actions"

    def add_pr(self, branch, number=7):
        self.prs[number] = {
            "number": number, "state": "open", "draft": False,
            "html_url": f"https://github.com/example/engine/pull/{number}",
            "base": {"ref": "main", "sha": self.repo.initial_main,
                     "repo": {"full_name": "example/engine"}},
            "head": {"ref": branch, "sha": self.repo.remote_sha(branch),
                     "repo": {"full_name": "example/engine"}},
            "merged": False, "merged_at": None, "merge_commit_sha": None,
        }

    def refresh(self, number):
        pr = self.prs[number]
        if not pr["merged"]:
            pr["head"]["sha"] = self.repo.remote_sha(pr["head"]["ref"])
        return pr

    def __call__(self, args, cwd, *, check=True, input_text=None):
        self.calls.append(args)
        code, data, error = 0, {}, ""
        if args[:2] == ["repo", "view"]:
            data = {"nameWithOwner": "example/engine"}
        elif args[0] == "api":
            endpoint = args[1]
            if "/check-runs" in endpoint:
                head = endpoint.split("/commits/")[1].split("/")[0]
                data = [{"check_runs": [{"name": "Batch readiness", "head_sha": head, "id": 12,
                                         "app": {"slug": self.check_app}, "status": self.check_status,
                                         "conclusion": self.check_conclusion}]}]
            elif "/commits/" in endpoint:
                data = [[pr for pr in self.prs.values() if self.associated]]
            elif "/files?" in endpoint:
                number = int(endpoint.split("/pulls/")[1].split("/")[0])
                pr = self.refresh(number)
                names = git([f"--git-dir={self.repo.remote}", "diff", "--name-only",
                             f"{pr['base']['sha']}...{pr['head']['sha']}"], self.repo.root).splitlines()
                data = [[{"filename": name} for name in names]]
            elif "/pulls?" in endpoint:
                data = [[self.refresh(n) for n in self.prs if self.prs[n]["state"] == "open"]]
            else:
                data = self.refresh(int(endpoint.rsplit("/", 1)[1]))
        elif args[:2] == ["pr", "create"]:
            self.add_pr(args[args.index("--head") + 1])
            data = self.prs[7]["html_url"]
        elif args[:2] == ["pr", "edit"]:
            self.refresh(int(args[2]))
        elif args[:2] == ["pr", "checks"]:
            data = self.required
            if any(item["bucket"] == "pending" for item in data):
                code = 8
            elif any(item["bucket"] != "pass" for item in data):
                code = 1
        elif args[:2] == ["pr", "view"]:
            data = {**self.policy, "headRefOid": self.refresh(int(args[2]))["head"]["sha"]}
        elif args[:2] == ["pr", "merge"]:
            number = int(args[2])
            pr = self.refresh(number)
            assert args[args.index("--match-head-commit") + 1] == pr["head"]["sha"]
            git([f"--git-dir={self.repo.remote}", "update-ref", "refs/heads/main", pr["head"]["sha"]],
                self.repo.root)
            pr.update(state="closed", merged=True, merged_at="2030-09-05T01:00:00Z",
                      merge_commit_sha=pr["head"]["sha"])
            self.merged = True
        else:
            raise AssertionError(f"Unexpected gh call: {args}")
        return subprocess.CompletedProcess(["gh", *args], code, json.dumps(data), error)


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".local" / "approval-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.repo = ShipQueueRepo(self.directory.name)
        self.github = FakeGitHub(self.repo)
        self.addCleanup(patch.stopall)
        patch.object(approval_guard, "run_gh", self.github).start()
        patch.object(ship_queue, "run_gh", self.github).start()

    def ship(self, **kwargs):
        return ship_queue.ship("queue/2030-09-05.json", repo_root=self.repo.root,
                               log=lambda *_: None, **kwargs)

    def approve(self, result):
        return ship_queue.approve("queue/2030-09-05.json", result["revision"],
                                  result["head_sha"], result["pr_number"], repo_root=self.repo.root)


class ShipQueueTests(RepoTestCase):
    def test_prepares_exact_payload_not_main_or_unrelated_staged_files(self):
        self.repo.add_batch()
        stray = self.repo.root / "assets/generated/20300905-layer8culture-instagram-1-old.png"
        stray.write_bytes(b"not declared")
        (self.repo.root / "UNRELATED.md").write_text("not this batch", encoding="utf-8")
        git(["add", "UNRELATED.md"], self.repo.root)
        result = self.ship()
        self.assertEqual(result["state"], "awaiting_approval")
        self.assertEqual(self.repo.initial_main, self.repo.remote_sha("main"))
        files = self.repo.remote_files("posts/2030-09-05")
        self.assertIn("queue/2030-09-05.json", files)
        self.assertIn("queue/2030-09-05.summary.md", files)
        self.assertIn("assets/generated/20300905-layer8culture-instagram-1.png", files)
        self.assertNotIn("UNRELATED.md", files)
        self.assertNotIn(stray.relative_to(self.repo.root).as_posix(), files)

    def test_updates_same_pr_and_reuses_existing_nightly_branch(self):
        qpath = self.repo.add_batch()
        first = self.ship()
        self.github.prs[7]["head"]["ref"] = "lofi/posts-nightly-123"
        git([f"--git-dir={self.repo.remote}", "update-ref", "refs/heads/lofi/posts-nightly-123",
             first["head_sha"]], self.repo.root)
        posts = json.loads(qpath.read_text(encoding="utf-8"))
        posts[0]["text"] = "Updated reviewed caption"
        qpath.write_text(json.dumps(posts), encoding="utf-8")
        again = self.ship()
        self.assertEqual(again["pr_number"], first["pr_number"])
        self.assertNotEqual(again["head_sha"], first["head_sha"])
        self.assertEqual(len([c for c in self.github.calls if c[:2] == ["pr", "create"]]), 1)
        self.assertEqual(self.repo.initial_main, self.repo.remote_sha("main"))

    def test_same_batch_is_idempotent(self):
        self.repo.add_batch()
        first = self.ship()
        again = self.ship()
        self.assertEqual(first, again)

    def test_recovers_branch_after_pr_creation_failed(self):
        self.repo.add_batch()
        first = self.ship()
        self.github.prs.clear()
        recovered = self.ship()
        self.assertEqual(first["head_sha"], recovered["head_sha"])
        self.assertEqual(len(self.github.prs), 1)

    def test_existing_nightly_pr_drops_exact_planned_intermediate_stills(self):
        entry = post(fmt="reel")
        entry["visual"]["openai_prompt"] = "A cinematic room"
        self.repo.add_batch([entry])
        still = self.repo.root / "assets/generated" / f"{entry['id']}.png"
        Image.new("RGB", (32, 32), "blue").save(still)
        git(["switch", "-c", "posts/2030-09-05"], self.repo.root)
        git(["add", "-f", "queue", "assets/generated"], self.repo.root)
        git(["commit", "-m", "nightly generated"], self.repo.root)
        git(["push", "origin", "posts/2030-09-05"], self.repo.root)
        self.github.add_pr("posts/2030-09-05")
        result = self.ship()
        self.assertNotIn(still.relative_to(self.repo.root).as_posix(),
                         self.repo.remote_files("posts/2030-09-05"))
        self.assertEqual(result["pr_number"], 7)
        self.approve(result)

    def test_existing_pr_with_unrelated_code_is_not_silently_merged(self):
        self.repo.add_batch()
        first = self.ship()
        with approval_guard.commit_worktree(self.repo.root, first["head_sha"]) as worktree:
            (worktree / "UNRELATED.py").write_text("print('not this batch')", encoding="utf-8")
            git(["add", "."], worktree)
            git(["commit", "-m", "unrelated code"], worktree)
            git(["push", "origin", "HEAD:posts/2030-09-05"], worktree)
        with self.assertRaisesRegex(ship_queue.ShipError, "unrelated files"):
            self.ship()

    def test_copied_manifest_is_checked_after_copy(self):
        self.repo.add_batch()
        real_copy = ship_queue.copy_into_worktree
        def corrupt_copy(paths, root, worktree):
            copied = real_copy(paths, root, worktree)
            (worktree / "assets/generated/20300905-layer8culture-instagram-1.png").write_bytes(b"bad")
            return copied
        with patch.object(ship_queue, "copy_into_worktree", side_effect=corrupt_copy):
            with self.assertRaisesRegex(ship_queue.ShipError, "unreadable"):
                self.ship()
        self.assertEqual(self.repo.initial_main, self.repo.remote_sha("main"))

    def test_builds_from_fresh_main_not_stale_checkout(self):
        self.repo.add_batch()
        other = pathlib.Path(self.directory.name) / "other"
        git(["clone", str(self.repo.remote), str(other)], self.repo.root)
        git(["config", "user.name", "test"], other)
        git(["config", "user.email", "test@example.com"], other)
        (other / "LANDED.md").write_text("must survive", encoding="utf-8")
        git(["add", "."], other)
        git(["commit", "-m", "landed"], other)
        git(["push", "origin", "main"], other)
        self.ship()
        self.assertIn("LANDED.md", self.repo.remote_files("posts/2030-09-05"))

    def test_dry_run_is_read_only(self):
        self.repo.add_batch()
        before = git(["status", "--porcelain"], self.repo.root)
        with patch.object(ship_queue, "run_git", side_effect=AssertionError("git mutation")):
            result = self.ship(dry_run=True)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(self.github.calls, [])
        self.assertEqual(before, git(["status", "--porcelain"], self.repo.root))
        self.assertFalse((self.repo.root / ".local").exists())

    def test_missing_and_unreadable_final_media_fail_before_github(self):
        qpath = self.repo.add_batch(render=False)
        with self.assertRaisesRegex(ship_queue.ShipError, "missing final"):
            self.ship()
        rel = json.loads(qpath.read_text())[0]["visual"]["file"]
        (self.repo.root / rel).write_bytes(b"not an image")
        with self.assertRaisesRegex(ship_queue.ShipError, "unreadable image"):
            self.ship()
        self.assertEqual(self.github.calls, [])

    def test_paths_outside_queue_and_direct_branch_shipping_rejected(self):
        self.repo.add_batch()
        for path in ("notes.json", "queue/../notes.json", "queue/nope.json"):
            with self.subTest(path=path), self.assertRaises(ship_queue.ShipError):
                ship_queue.ship(path, repo_root=self.repo.root, dry_run=True)
        with self.assertRaises(ship_queue.ShipError):
            self.ship(branch="other")

    def test_missing_carousel_slide_and_reel_cover_block(self):
        entry = post(fmt="carousel")
        self.repo.add_batch([entry])
        (self.repo.root / entry["visual"]["files"][1]).unlink()
        with self.assertRaisesRegex(ship_queue.ShipError, "-2.png"):
            self.ship()
        entry = post(fmt="reel")
        self.repo.add_batch([entry])
        (self.repo.root / entry["visual"]["cover"]).unlink()
        with self.assertRaisesRegex(ship_queue.ShipError, "-cover.png"):
            self.ship()

    def test_concurrent_pr_head_change_is_not_overwritten(self):
        self.repo.add_batch()
        self.ship()
        real_copy = ship_queue.copy_into_worktree
        def move_remote(*args):
            copied = real_copy(*args)
            git([f"--git-dir={self.repo.remote}", "update-ref", "refs/heads/posts/2030-09-05",
                 self.repo.initial_main], self.repo.root)
            return copied
        with patch.object(ship_queue, "copy_into_worktree", side_effect=move_remote):
            with self.assertRaisesRegex(ship_queue.ShipError, "push.*failed"):
                self.ship()
        self.assertEqual(self.repo.initial_main, self.repo.remote_sha("posts/2030-09-05"))


if __name__ == "__main__":
    unittest.main()
