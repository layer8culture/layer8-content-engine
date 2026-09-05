import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from app_state import StateStore, process_identity


class DurableJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.store = StateStore(self.root)

    def tearDown(self):
        active = self.store.active()
        if active:
            self.store.cancel(active)
            self.wait(active)
        self.tmp.cleanup()

    def wait(self, job):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.store.payload(job)
            record = self.store.get(job)
            if result["status"] != "running" and (
                    not record["pid"] or process_identity(record["pid"]) != record["identity"]):
                return result
            time.sleep(0.05)
        self.fail("Worker did not finish.")

    def test_completion_and_logs_survive_store_recreation(self):
        job = self.store.start("prepare", [sys.executable, "-u", "-c", "print('prepared')"])
        self.store = StateStore(self.root)
        result = self.wait(job)
        self.assertEqual("done", result["status"], result)
        self.assertIn("prepared", result["lines"])
        self.assertEqual([], self.store.payload(job, result["next"])["lines"])

    def test_active_job_blocks_second_job_and_mutations(self):
        job = self.store.start("prepare", [sys.executable, "-c", "import time;time.sleep(2)"])
        with self.assertRaises(RuntimeError):
            self.store.start("prepare", [sys.executable, "-c", "pass"])
        with self.assertRaises(RuntimeError):
            with self.store.mutation():
                self.fail("An active job allowed mutation.")
        self.assertEqual(job, self.store.active())

    def test_cancel_only_owns_its_local_job(self):
        job = self.store.start("prepare", [sys.executable, "-c", "import time;time.sleep(10)"])
        self.store.cancel(job)
        self.assertEqual("cancelled", self.wait(job)["status"])

    def test_missing_worker_is_interrupted_not_success(self):
        with self.store.connection() as db:
            db.execute("INSERT INTO jobs(id,kind,queue,label,command,argv,status,started,pid,identity) "
                       "VALUES('gone','prepare','','fixture','','[]','running',0,0,'gone')")
        self.assertIsNone(self.store.active())
        self.assertEqual("interrupted", self.store.payload("gone")["status"])

    def test_approval_survives_restart_and_can_be_invalidated(self):
        value = {"revision": "abc", "head_sha": "def", "pr_number": 42}
        self.store.save_approval("batch.json", value)
        self.assertEqual(value, StateStore(self.root).approval("batch.json"))
        with self.store.mutation() as db:
            self.store.invalidate_approval("batch.json", db)
        self.assertIsNone(self.store.approval("batch.json"))

    def test_delivery_observation_survives_restart(self):
        value = {"revision": "abc", "workflow": {"conclusion": "failure"},
                 "observed_at": "2030-01-01T12:00:00Z"}
        self.store.save_observation("batch.json", value)
        self.assertEqual(value, StateStore(self.root).observation("batch.json"))


if __name__ == "__main__":
    unittest.main()
