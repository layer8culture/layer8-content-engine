import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ship_queue  # noqa: E402


def git(args, cwd):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


class ShipQueueRepo:
    """A throwaway repo wired to a bare remote, so pushes are real."""

    def __init__(self, tmp: Path):
        self.remote = tmp / "remote.git"
        self.root = tmp / "work"
        git(["init", "--bare", "-b", "main", str(self.remote)], tmp)
        git(["init", "-b", "main", str(self.root)], tmp)
        git(["config", "user.name", "test"], self.root)
        git(["config", "user.email", "test@example.com"], self.root)
        git(["remote", "add", "origin", str(self.remote)], self.root)

        (self.root / "queue").mkdir()
        (self.root / "assets" / "generated").mkdir(parents=True)
        (self.root / ".gitignore").write_text("assets/generated/*\n", encoding="utf-8")
        (self.root / "README.md").write_text("seed\n", encoding="utf-8")
        git(["add", "-A"], self.root)
        git(["commit", "-m", "seed"], self.root)
        git(["push", "origin", "main"], self.root)

    def add_batch(self, name="2026-08-20.json", posts=None, render=True):
        posts = posts if posts is not None else [self.post()]
        qpath = self.root / "queue" / name
        qpath.write_text(json.dumps(posts, indent=2), encoding="utf-8")
        qpath.with_suffix(".summary.md").write_text("# Summary\n", encoding="utf-8")
        if render:
            for post in posts:
                for rel in ship_queue.declared_media(post):
                    target = self.root / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"binary")
        return qpath

    @staticmethod
    def post(post_id="20260820-layer8culture-instagram-1", fmt="single",
             platform="instagram", **overrides):
        visual = {"source": "openai",
                  "file": f"assets/generated/{post_id}.png"}
        if fmt == "carousel":
            visual = {"source": "openai",
                      "files": [f"assets/generated/{post_id}-{n}.png"
                                for n in (1, 2, 3)]}
        post = {
            "id": post_id,
            "account": "layer8culture",
            "platform": platform,
            "format": fmt,
            "schedule_time": "2026-08-20T09:00:00-04:00",
            "text": "Caption",
            "visual": visual,
        }
        post.update(overrides)
        return post

    def remote_files(self):
        # safe.bareRepository=explicit is common locally, and -C does not count
        # as explicit; --git-dir does.
        out = subprocess.run(
            ["git", f"--git-dir={self.remote}", "ls-tree", "-r", "--name-only", "main"],
            capture_output=True, text=True, check=True,
        )
        return set(out.stdout.split())


class ShipQueueTests(unittest.TestCase):
    def repo(self, tmp):
        return ShipQueueRepo(Path(tmp))

    # --- guard rails -----------------------------------------------------

    def test_rejects_a_path_outside_the_queue_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            stray = repo.root / "notes.json"
            stray.write_text("[]", encoding="utf-8")

            with self.assertRaises(ship_queue.ShipError):
                ship_queue.ship("notes.json", repo_root=repo.root,
                                dry_run=True, log=lambda *_: None)

    def test_rejects_a_missing_queue_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)

            with self.assertRaises(ship_queue.ShipError):
                ship_queue.ship("queue/nope.json", repo_root=repo.root,
                                dry_run=True, log=lambda *_: None)

    def test_refuses_to_publish_when_an_image_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            repo.add_batch(render=False)

            with self.assertRaises(ship_queue.ShipError) as ctx:
                ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                                log=lambda *_: None)

            self.assertIn("missing", str(ctx.exception))
            self.assertNotIn("queue/2026-08-20.json", repo.remote_files())

    def test_refuses_a_carousel_with_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            post = repo.post(fmt="carousel")
            repo.add_batch(posts=[post])
            (repo.root / post["visual"]["files"][1]).unlink()

            with self.assertRaises(ship_queue.ShipError) as ctx:
                ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                                log=lambda *_: None)

            self.assertIn("-2.png", str(ctx.exception))

    def test_refuses_a_video_platform_holding_a_still(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            post = repo.post(post_id="20260820-layer8culture-tiktok-1",
                             platform="tiktok")
            repo.add_batch(posts=[post])

            with self.assertRaises(ship_queue.ShipError) as ctx:
                ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                                log=lambda *_: None)

            self.assertIn("needs a video", str(ctx.exception))

    def test_dry_run_reports_readiness_without_pushing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            repo.add_batch()

            result = ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                                     dry_run=True, log=lambda *_: None)

            self.assertTrue(result["dry_run"])
            self.assertFalse(result["pushed"])
            self.assertEqual(result["assets"], 1)
            self.assertNotIn("queue/2026-08-20.json", repo.remote_files())

    # --- the happy path --------------------------------------------------

    def test_publishes_queue_summary_and_assets_to_the_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            repo.add_batch(posts=[
                repo.post(),
                repo.post(post_id="20260820-layer8culture-instagram-2",
                          fmt="carousel"),
            ])

            result = ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                                     log=lambda *_: None)

            self.assertTrue(result["pushed"])
            shipped = repo.remote_files()
            self.assertIn("queue/2026-08-20.json", shipped)
            self.assertIn("queue/2026-08-20.summary.md", shipped)
            self.assertIn("assets/generated/20260820-layer8culture-instagram-1.png",
                          shipped)
            self.assertIn("assets/generated/20260820-layer8culture-instagram-2-3.png",
                          shipped)

    def test_ignores_assets_belonging_to_another_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            repo.add_batch()
            stray = repo.root / "assets/generated/20260819-layer8culture-instagram-1.png"
            stray.write_bytes(b"other day")

            ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                            log=lambda *_: None)

            self.assertNotIn("assets/generated/20260819-layer8culture-instagram-1.png",
                             repo.remote_files())

    def test_carries_reel_covers_that_the_queue_never_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            post = repo.post(post_id="20260820-layer8culture-tiktok-1",
                             platform="tiktok")
            post["visual"]["file"] = "assets/generated/20260820-layer8culture-tiktok-1.mp4"
            repo.add_batch(posts=[post])
            cover = repo.root / "assets/generated/20260820-layer8culture-tiktok-1-cover.png"
            cover.write_bytes(b"cover")

            ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                            log=lambda *_: None)

            self.assertIn("assets/generated/20260820-layer8culture-tiktok-1-cover.png",
                          repo.remote_files())

    def test_publishing_the_same_batch_twice_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            repo.add_batch()
            ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                            log=lambda *_: None)

            again = ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                                    log=lambda *_: None)

            self.assertFalse(again["pushed"])
            self.assertTrue(again["already_published"])

    def test_rebases_onto_work_that_landed_first(self):
        """The remote moving mid-publish must not clobber it."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            repo.add_batch()

            other = Path(tmp) / "other"
            git(["clone", str(repo.remote), str(other)], Path(tmp))
            git(["config", "user.name", "other"], other)
            git(["config", "user.email", "other@example.com"], other)
            (other / "LANDED.md").write_text("first\n", encoding="utf-8")
            git(["add", "-A"], other)
            git(["commit", "-m", "landed first"], other)
            git(["push", "origin", "main"], other)

            result = ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                                     log=lambda *_: None)

            self.assertTrue(result["pushed"])
            shipped = repo.remote_files()
            self.assertIn("queue/2026-08-20.json", shipped)
            self.assertIn("LANDED.md", shipped)

    def test_publishes_from_the_remote_not_the_stale_local_checkout(self):
        """The clone is usually behind main; publishing must not revert it."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            repo.add_batch()

            other = Path(tmp) / "other"
            git(["clone", str(repo.remote), str(other)], Path(tmp))
            git(["config", "user.name", "other"], other)
            git(["config", "user.email", "other@example.com"], other)
            (other / "IMPORTANT.md").write_text("must survive\n", encoding="utf-8")
            git(["add", "-A"], other)
            git(["commit", "-m", "important work"], other)
            git(["push", "origin", "main"], other)
            # repo.root never pulls this, so it is now a commit behind.

            ship_queue.ship("queue/2026-08-20.json", repo_root=repo.root,
                            log=lambda *_: None)

            self.assertIn("IMPORTANT.md", repo.remote_files())


if __name__ == "__main__":
    unittest.main()
