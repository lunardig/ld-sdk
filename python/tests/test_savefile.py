"""Save files for the web game. LD_ENGINE names the engine command, as in test_client.py."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld_client import (HOUR, MIN, Engine, alpha, challenge, goal_gain, goal_running_between, goal_stored,  # noqa: E402
                       save_file)

ENGINE = os.environ.get("LD_ENGINE", "ld-engine-cli")


class SaveFileTest(unittest.TestCase):
    def setUp(self):
        self.create = alpha()
        self.e = Engine(ENGINE)
        self.e.create(**self.create)
        v = self.e.view()
        self.e.submit("INSTALL", lot_id=v.kit_lot("solar-kit"), product="solar-kit", location="pad-a", as_id="solar-1")
        self.e.submit("CREATE_JOB", job_id="sun", process="generate-solar", machine_id="solar-1", target_extent=10**12, priority=100)
        self.e.submit("START", job_id="sun")
        self.e.advance_by(MIN)

    def tearDown(self):
        self.e.close()

    def test_a_save_restores_to_the_same_claim(self):
        doc = json.loads(json.dumps(save_file(self.e, self.create)))   # as written to disk
        self.assertEqual(doc["format"], "ld-web.save.v0")
        self.assertEqual(doc["saved_at_claim_time_us"], str(MIN))
        self.assertEqual(doc["ui"]["next_seq"], "4")                    # three commands so far
        self.assertNotIn("meta", doc["ui"])
        with Engine(ENGINE) as f:
            f.restore(doc["snapshot"], doc["catalog"])
            self.assertEqual(f.state_hash(), self.e.state_hash())
            self.assertEqual(f.history_head(), self.e.history_head())

    def test_a_challenge_rides_in_the_meta(self):
        ch = challenge("first-hour", "First hour", "A story.", "A brief.",
                       goals=[goal_stored("h2-g", 10), goal_gain(500), goal_running_between(HOUR, 2 * HOUR, "Working")],
                       deadline_us=HOUR, author="Tests")
        doc = save_file(self.e, self.create, challenge=ch)
        meta = doc["ui"]["meta"]["challenge"]
        self.assertEqual(meta["format"], "ld-web.challenge.v0")
        self.assertEqual(meta["deadline_us"], str(HOUR))
        self.assertEqual(meta["goals"][2], {"kind": "runningBetween", "from_us": str(HOUR), "to_us": str(2 * HOUR), "label": "Working"})

    def test_bad_challenges_are_refused_here_not_in_the_game(self):
        with self.assertRaises(ValueError):
            challenge("has spaces", "T", "S", "B", goals=[goal_gain(1)])
        with self.assertRaises(ValueError):
            challenge("no-goals", "T", "S", "B", goals=[])
        with self.assertRaises(ValueError):
            challenge("late", "T", "S", "B", goals=[goal_gain(1)], deadline_us=0)


if __name__ == "__main__":
    unittest.main()
