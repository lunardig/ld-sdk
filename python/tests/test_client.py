"""ld_client against a real engine. LD_ENGINE names the engine command (default: an
ld-engine-cli on PATH).

    LD_ENGINE=/path/to/ld-engine-cli python -m unittest discover python/tests
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld_client import DAY, MIN, Engine, Rejected, alpha, alpha_loop, loop_setup, lunar_day, recipe  # noqa: E402

ENGINE = os.environ.get("LD_ENGINE", "ld-engine-cli")


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.e = Engine(ENGINE)
        self.c = alpha()
        self.e.create(**self.c)

    def tearDown(self):
        self.e.close()

    def plant(self):
        e, v = self.e, self.e.view()
        e.submit("INSTALL", lot_id=v.kit_lot("solar-kit"), product="solar-kit", location="pad-a", as_id="solar-1")
        e.submit("CREATE_JOB", job_id="sun", process="generate-solar", machine_id="solar-1", target_extent=10**12, priority=100)
        e.submit("START", job_id="sun")

    def test_commands_take_effect_now_and_are_numbered(self):
        self.plant()
        self.e.advance_by(MIN)
        self.assertEqual(self.e.view().time, MIN)
        self.assertTrue(self.e.view().running("sun"))
        self.assertEqual(self.e.view().machine("solar-1")["mode"], "RUNNING")

    def test_a_rejection_raises_with_its_code(self):
        with self.assertRaises(Rejected) as r:
            self.e.submit("INSTALL", lot_id="no-such-lot", product="solar-kit", location="pad-a", as_id="x")
        self.assertEqual(r.exception.code, "unknown-lot")
        a = self.e.submit("INSTALL", check=False, lot_id="no-such-lot", product="solar-kit", location="pad-a", as_id="x")
        self.assertEqual(a["status"], "REJECTED_STATE")

    def test_events_drain_once(self):
        self.plant()
        self.e.advance_by(MIN)
        first = list(self.e.events())
        self.assertTrue(any(ev["kind"] == "COMMAND" for ev in first))
        self.assertEqual(list(self.e.events()), [])

    def test_next_event_is_dusk_and_the_sun_sets_there(self):
        self.plant()
        dusk, period = lunar_day(self.c)
        self.assertEqual(self.e.next_event_time(), dusk)
        self.e.advance_to(dusk + MIN)
        self.assertEqual(self.e.view().job("sun")["status"], "STARVED")
        self.assertGreater(period, dusk)

    def test_snapshot_restore_round_trips(self):
        self.plant()
        self.e.advance_by(DAY)
        snap, h = self.e.snapshot(), self.e.state_hash()
        other = Engine(ENGINE)
        try:
            other.restore(snap, self.c["catalog"])
            self.assertEqual(other.state_hash(), h)
            self.assertEqual(other.history_head(), self.e.history_head())
            other.submit("PAUSE", job_id="sun")  # numbering continues after restore
        finally:
            other.close()

    def test_events_after_restore_in_the_same_engine_are_not_missed(self):
        # SDK-001 (external acceptance, Nov 2): restore restarts the engine's event log,
        # and the client's cursor must restart with it.
        e = self.e
        e.submit("INSTALL", lot_id="starter-kit", product="solar-kit", location="pad-a", as_id="sun")
        list(e.events())
        saved = e.snapshot()
        e.submit("INSTALL", lot_id="starter-kit", product="excavator-kit", location="pad-a", as_id="temporary-digger")
        list(e.events())
        e.restore(saved, self.c["catalog"])
        e.submit("INSTALL", lot_id="starter-kit", product="excavator-kit", location="pad-a", as_id="restored-digger")
        received = list(e.events())
        direct = e.request("events_since", {"cursor": "0", "limit": "100"})["events"]
        self.assertTrue(direct)
        self.assertEqual(received, direct)
        self.assertEqual(list(e.events()), [])

    def test_issued_by_is_recorded_on_the_command_event(self):
        by = {"origin": "https://sheet.example", "session": "s-1"}
        self.e.submit("INSTALL", issued_by=by, lot_id="starter-kit", product="solar-kit", location="pad-a", as_id="sun")
        ev = [x for x in self.e.events() if x["kind"] == "COMMAND"]
        self.assertEqual(ev[-1]["issued_by"], by)


class RecipeTest(unittest.TestCase):
    def test_reduction_and_hopper_recipes(self):
        c = alpha()
        red = recipe(c, "reduce-ilmenite", 400_000)
        self.assertEqual(red.inputs, {"ilmenite": {"fetio3-s": 800_000}, "h2": {"h2-g": 400_000}})
        self.assertEqual(red.outputs["iron"], {"fe-s": 340_000})
        self.assertEqual(red.outputs["unreacted"], {"fetio3-s": 400_000})
        fab = recipe(c, "fabricate-hopper", 1)
        self.assertEqual(fab.inputs, {"iron": {"fe-s": 1_900_000}})
        self.assertEqual(fab.products, {"hopper-kit": 1})

    def test_the_loop_scenario_starts_as_documented(self):
        c = alpha_loop()
        kits = c["genesis"]["starting_lots"][0]["products"]
        self.assertEqual(kits["fabricator-kit"], "1")
        self.assertEqual(loop_setup()[0][1]["as_id"], "water-store")
        self.assertTrue(any(l.get("core") for l in c["genesis"]["starting_lots"]))


if __name__ == "__main__":
    unittest.main()
