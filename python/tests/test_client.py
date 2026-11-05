"""ld_client against a real engine. LD_ENGINE names the engine command (default: an
ld-engine-cli on PATH).

    LD_ENGINE=/path/to/ld-engine-cli python -m unittest discover python/tests
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld_client import DAY, HOUR, MIN, SITES, Engine, Rejected, alpha, alpha_loop, loop_setup, lunar_day, recipe  # noqa: E402

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


class SiteTest(unittest.TestCase):
    """The sites (LD-PEC-011): each digs and sorts its own ground under its own light."""

    KITS = {"solar-kit": "1", "excavator-kit": "1", "hopper-kit": "1", "beneficiator-kit": "1", "ice-bin-kit": "1",
            "ilmenite-bin-kit": "1", "tailings-pile-kit": "1"}

    def start(self, site):
        self.addCleanup(lambda: self.e.close())
        self.c = alpha(site=site, lots=[{"lot_id": "kits", "location": "inventory", "state": "AVAILABLE", "composition": {}, "products": self.KITS}])
        self.e = Engine(ENGINE)
        self.e.create(**self.c)

    def plant(self, site):
        """The mining chain, running from dawn. A mare claim leaves the sorter's ice port open."""
        e, s = self.e, SITES[site]
        for kit, as_id in [("solar-kit", "solar-1"), ("excavator-kit", "dig-1"), ("hopper-kit", "hopper-1"), ("beneficiator-kit", "sort-1"),
                           ("ice-bin-kit", "ice-1"), ("ilmenite-bin-kit", "ilmenite-1"), ("tailings-pile-kit", "tailings-1")]:
            e.submit("INSTALL", lot_id="kits", product=kit, location="pad-a", as_id=as_id)
        links = [("c-face", f"{s['deposit']}.face", "dig-1.face"), ("c-dig", "dig-1.out", "hopper-1.io"), ("c-feed", "hopper-1.io", "sort-1.feed"),
                 ("c-ilm", "sort-1.ilmenite", "ilmenite-1.io"), ("c-tail", "sort-1.tailings", "tailings-1.io")]
        if s["yield"]["ice"]:
            links.append(("c-ice", "sort-1.ice", "ice-1.io"))
        for cid, a, b in links:
            e.submit("CONNECT", connection_id=cid, template="pipe", from_port=a, to_port=b)
        for jid, process, machine, priority in [("sun", s["processes"]["solar"], "solar-1", 100), ("dig", s["processes"]["excavate"], "dig-1", 10),
                                                ("sort", s["processes"]["beneficiate"], "sort-1", 10)]:
            e.submit("CREATE_JOB", job_id=jid, process=process, machine_id=machine, target_extent=10**12, priority=priority)
            e.submit("START", job_id=jid)

    def test_every_site_digs_and_sorts_its_own_ground(self):
        for site, s in SITES.items():
            with self.subTest(site=site):
                self.start(site)
                self.plant(site)
                self.e.advance_by(HOUR)
                v, y = self.e.view(), s["yield"]
                self.assertEqual(v.job("sun")["status"], "RUNNING")
                # Sorted so far: the hopper's regolith went somewhere, in the site's proportions.
                ilmenite, ice = v.amount("ilmenite-1", "fetio3-s"), v.amount("ice-1", "h2o-s")
                kg = ilmenite * 2 / y["ilmenite"]
                self.assertGreater(kg, 1000)
                self.assertAlmostEqual(float(ice), float(kg * y["ice"] * 13 / 20), delta=y["ice"] + 1)
                self.e.close()

    def test_a_kilogram_of_each_site_weighs_a_kilogram(self):
        for site, s in SITES.items():
            y = s["yield"]
            grams = (y["ice"] * 18.015 + y["ilmenite"] * 151.71 + y["silicate"] * 60.08) / 1000
            self.assertAlmostEqual(grams, 1000, delta=1, msg=site)

    def test_the_polar_night_is_four_days(self):
        self.start("polar")
        self.plant("polar")
        dusk, period = lunar_day(self.c)
        self.assertEqual(period - dusk, 4 * DAY)
        self.e.advance_to(dusk + MIN)
        self.assertEqual(self.e.view().job("sun")["status"], "STARVED")
        self.e.advance_to(period + MIN)
        self.assertEqual(self.e.view().job("sun")["status"], "RUNNING")

    def test_the_mare_night_is_a_half_month(self):
        self.start("mare")
        dusk, period = lunar_day(self.c)
        self.assertEqual(dusk, period // 2)

    def test_a_site_has_only_its_own_light_and_ground(self):
        self.start("mare")
        e = self.e
        e.submit("INSTALL", lot_id="kits", product="solar-kit", location="pad-a", as_id="solar-1")
        e.submit("CREATE_JOB", job_id="sun", process="generate-solar-polar", machine_id="solar-1", target_extent=10**12, priority=100)
        e.submit("START", job_id="sun")
        e.advance_by(MIN)
        job = e.view().job("sun")
        self.assertEqual((job["status"], job["reason"]["code"]), ("STARVED", "STARVED_REQUIRE"))
        e.submit("INSTALL", lot_id="kits", product="excavator-kit", location="pad-a", as_id="dig-1")
        e.submit("INSTALL", lot_id="kits", product="hopper-kit", location="pad-a", as_id="hopper-1")
        e.submit("CONNECT", connection_id="c-face", template="pipe", from_port="mare-basin.face", to_port="dig-1.face")
        e.submit("CONNECT", connection_id="c-dig", template="pipe", from_port="dig-1.out", to_port="hopper-1.io")
        e.submit("CREATE_JOB", job_id="dig", process="excavate-polar", machine_id="dig-1", target_extent=10**12, priority=10)
        e.submit("START", job_id="dig")
        e.advance_by(MIN)
        self.assertEqual(e.view().amount("hopper-1", "regolith-polar"), 0)

    def test_the_default_is_the_training_site(self):
        self.assertEqual(alpha()["genesis"]["deposits"], alpha(site="training")["genesis"]["deposits"])
        self.assertEqual(alpha()["genesis"]["environment"], ["irradiance"])
        self.assertTrue(SITES["training"]["fictional"])


if __name__ == "__main__":
    unittest.main()
