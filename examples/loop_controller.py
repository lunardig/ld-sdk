"""An external controller that plays *The loop* (LD-PEC-006 §3.1) over I-1, with ld_client.

It knows nothing about the engine beyond what I-1 shows: it reads the claim view, issues
commands, and advances time. It starts from the scenario ld-web calls "The loop" (alpha
catalog, kits only, a little water, and two reactors whose fuel is in their cores), then:

1. builds the mining chain: excavator, beneficiator, melter, electrolyzer, reactor;
2. runs reduction batches while there is ilmenite and hydrogen for one;
3. fabricates a hopper kit from the claim's own iron and installs it;
4. powers the night with fission or with the fuel cell (--night), and keeps the plant
   producing until the last day of the night.

It exits 0 when both goals hold: two hoppers installed, and a consumer job RUNNING during
the last day and a half before dawn. It prints a short log and, with --report, the event
and byte counts per simulated day.

    python examples/loop_controller.py --engine path/to/ld-engine-cli [--night fission|small-fission|fuel-cell]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from ld_client import DAY, HOUR, MIN, Engine, View, alpha, lunar_day  # noqa: E402

HOPPER_IRON = 1_900_000  # mmol of iron one hopper takes: 1 800 embodied plus 100 scrap
BATCH = 400_000  # rxn-mmol per reduction batch: 800 mol of ilmenite fed, half reacted
HOPPERS_WANTED = 2  # hopper kits to fabricate: one to install and one spare
GENERATORS = {"generate-solar", "fuel-cell", "fission"}


def loop_genesis() -> list[dict]:
    """The scenario's starting lots; ld-web's src/game/campaign.ts ("the-loop") has the same."""
    kits = {"solar-kit": 1, "battery-kit": 1, "excavator-kit": 1, "hopper-kit": 1, "beneficiator-kit": 1, "ice-bin-kit": 1,
            "ilmenite-bin-kit": 1, "tailings-pile-kit": 1, "melter-kit": 1, "electrolyzer-kit": 1, "bulk-tank-kit": 3,
            "reactor-kit": 1, "iron-bin-kit": 1, "rutile-bin-kit": 1, "fabricator-kit": 1, "scrap-bin-kit": 1,
            "fuel-cell-kit": 1}
    # Each reactor ships with a full core: ten years at rated power (LD-PEC-006 §3.7).
    reactors = [("reactor-1mwe", "fission-reactor-kit", "4200000"), ("reactor-400kwe", "small-reactor-kit", "1700000")]
    return [
        {"lot_id": "kits", "location": "inventory", "state": "AVAILABLE", "composition": {}, "products": {k: str(n) for k, n in kits.items()}},
        {"lot_id": "lander-water", "location": "water-store", "state": "AVAILABLE", "composition": {"h2o-l": "50000"}, "products": {}},
    ] + [{"lot_id": lid, "location": "inventory", "state": "AVAILABLE", "composition": {}, "products": {kit: "1"}, "core": {"fuel-fresh": fuel}}
         for lid, kit, fuel in reactors]


def loop_setup() -> list[tuple[str, dict]]:
    """What the scenario installs before the player arrives: the tank the water came in."""
    return [("INSTALL", {"lot_id": "kits", "product": "bulk-tank-kit", "location": "pad-a", "as_id": "water-store"})]


class Controller:
    def __init__(self, e: Engine, night: str, dusk: int, log=print):
        self.e, self.night, self.dusk, self.log = e, night, dusk, log
        self.batches = 0
        self.fabs = 0
        self.rerouted = False
        self.night_ready = False
        self.v = View({"jobs": [], "machines": [], "buffers": [], "lots": [], "time_us": "0"})

    # ---------------------------------------------------------------- reading the view
    def active(self, process: str) -> bool:
        """Finished jobs leave the state (LD-PEC-004 A2-1), so any job listed is unfinished
        unless it failed holding material."""
        return any(j["status"] not in ("COMPLETED", "CANCELLED", "FAILED") for j in self.v.jobs_for(process))

    def hoppers(self) -> int:
        return sum(1 for b in self.v.buffers if b.get("template") == "hopper")

    def consumers_running(self) -> list[str]:
        return [j["job_id"] for j in self.v.jobs if j["process"] not in GENERATORS and j["status"] == "RUNNING"]

    # ---------------------------------------------------------------- acting
    def install(self, product: str, as_id: str) -> None:
        self.e.submit("INSTALL", lot_id=self.v.kit_lot(product), product=product, location="pad-a", as_id=as_id)

    def pipe(self, cid: str, frm: str, to: str) -> None:
        self.e.submit("CONNECT", connection_id=cid, template="pipe", from_port=frm, to_port=to)

    def job(self, jid: str, process: str, machine: str, target: int, priority: int) -> None:
        self.e.submit("CREATE_JOB", job_id=jid, process=process, machine_id=machine, target_extent=target, priority=priority)
        self.e.submit("START", job_id=jid)

    def build(self) -> None:
        self.v = self.e.view()
        for k, a in [("solar-kit", "solar-1"), ("battery-kit", "battery-1"), ("excavator-kit", "excavator-1"), ("hopper-kit", "hopper-1"),
                     ("beneficiator-kit", "beneficiator-1"), ("ice-bin-kit", "ice-bin"), ("ilmenite-bin-kit", "ilmenite-bin"),
                     ("tailings-pile-kit", "tailings-pile"), ("melter-kit", "melter-1"), ("electrolyzer-kit", "electrolyzer-1"),
                     ("bulk-tank-kit", "h2-store"), ("bulk-tank-kit", "o2-store"), ("reactor-kit", "reactor-1"), ("iron-bin-kit", "iron-bin"),
                     ("rutile-bin-kit", "rutile-bin"), ("fabricator-kit", "fabricator-1")]:
            self.install(k, a)
            self.v = self.e.view()
        for c, f, t in [("c-face", "icy-crater.face", "excavator-1.face"), ("c-dig", "excavator-1.out", "hopper-1.io"),
                        ("c-feed", "hopper-1.io", "beneficiator-1.feed"), ("c-ice", "beneficiator-1.ice", "ice-bin.io"),
                        ("c-ilm", "beneficiator-1.ilmenite", "ilmenite-bin.io"), ("c-tail", "beneficiator-1.tailings", "tailings-pile.io"),
                        ("c-melt", "ice-bin.io", "melter-1.ice"), ("c-water", "melter-1.water", "water-store.io"),
                        ("c-split", "water-store.io", "electrolyzer-1.water"), ("c-h2", "electrolyzer-1.h2", "h2-store.io"),
                        ("c-o2", "electrolyzer-1.o2", "o2-store.io"), ("c-r-ilm", "ilmenite-bin.io", "reactor-1.ilmenite"),
                        ("c-r-h2", "h2-store.io", "reactor-1.h2"), ("c-r-fe", "reactor-1.iron", "iron-bin.io"),
                        ("c-r-ti", "reactor-1.rutile", "rutile-bin.io"), ("c-r-w", "reactor-1.water", "water-store.io"),
                        ("c-r-back", "reactor-1.unreacted", "ilmenite-bin.io"), ("c-f-fe", "iron-bin.io", "fabricator-1.iron"),
                        # Offcuts go back to the iron bin to be melted again; a scrap bin
                        # would fill after ten hoppers.
                        ("c-f-scrap", "fabricator-1.scrap", "iron-bin.io")]:
            self.pipe(c, f, t)
        big = 10**12
        self.job("job-solar", "generate-solar", "solar-1", big, 100)
        self.job("job-dig", "excavate", "excavator-1", big, 10)
        self.job("job-sort", "beneficiate", "beneficiator-1", big, 10)
        self.job("job-melt", "melt-ice", "melter-1", big, 10)
        self.job("job-split", "electrolyze-water", "electrolyzer-1", big, 5)
        self.log(f"{self.clock()}  plant built: 7 machines, 19 pipes")

    def prepare_night(self) -> None:
        """Install the night supply a little before dusk."""
        if self.night in ("fission", "small-fission"):
            # The reactor brings its fuel in its core; nothing to connect.
            self.install("fission-reactor-kit" if self.night == "fission" else "small-reactor-kit", "fission-1")
            self.job("job-fission", "fission", "fission-1", 10**12, 50)
        else:
            self.install("fuel-cell-kit", "cell-1")
            self.pipe("c-cell-h2", "h2-store.io", "cell-1.h2")
            self.pipe("c-cell-o2", "o2-store.io", "cell-1.o2")
            self.pipe("c-cell-w", "cell-1.water", "water-store.io")
            self.job("job-cell", "fuel-cell", "cell-1", 10**12, 50)
            # Splitting water with fuel-cell power only loses energy: stop it overnight.
            for j in self.v.jobs_for("electrolyze-water"):
                if j["status"] not in ("COMPLETED", "CANCELLED", "FAILED", "PAUSED"):
                    self.e.submit("PAUSE", job_id=j["job_id"])
        self.night_ready = True
        self.log(f"{self.clock()}  night supply ready: {self.night}")

    def step(self) -> None:
        """Look at the claim and act; called between advances."""
        self.v = self.e.view()
        enough = self.fabs >= HOPPERS_WANTED
        # A reduction batch when the bins hold one and the reactor is free, until there is
        # iron for the hoppers wanted.
        if (not enough and not self.active("reduce-ilmenite") and self.v.amount("ilmenite-bin", "fetio3-s") >= 2 * BATCH
                and self.v.amount("h2-store", "h2-g") >= BATCH):
            self.batches += 1
            self.job(f"job-reduce-{self.batches}", "reduce-ilmenite", "reactor-1", BATCH, 20)
            self.log(f"{self.clock()}  reduction batch {self.batches}: iron bin {self.v.amount('iron-bin', 'fe-s') / 1000:.0f} mol")
        if self.hoppers() < 2 and self.v.kits("hopper-kit"):
            self.install("hopper-kit", "hopper-2")
            self.log(f"{self.clock()}  second hopper installed, made from the claim's own iron")
        if not enough and not self.active("fabricate-hopper") and self.v.amount("iron-bin", "fe-s") >= HOPPER_IRON:
            self.fabs += 1
            self.job(f"job-fab-{self.fabs}", "fabricate-hopper", "fabricator-1", 1, 30)
            self.log(f"{self.clock()}  fabricating hopper kit {self.fabs} from {HOPPER_IRON // 1000} mol of iron")
        # With the iron made, ilmenite has nowhere to go: the bin fills and stops the
        # sorter, and the ice and water behind it. Send it to the tailings pile instead.
        if enough and not self.rerouted and not self.active("reduce-ilmenite"):
            self.e.submit("DISCONNECT", connection_id="c-ilm")
            self.pipe("c-ilm-tail", "beneficiator-1.ilmenite", "tailings-pile.io")
            self.rerouted = True
            self.log(f"{self.clock()}  enough iron: ilmenite now goes to the tailings pile")
        if not self.night_ready and self.e.now >= self.dusk - HOUR:
            self.prepare_night()

    def clock(self) -> str:
        t = self.e.now
        return f"day {t // DAY:2d} {t % DAY // HOUR:02d}:{t % HOUR // MIN:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", required=True, help="path to ld-engine-cli, or a command such as 'node engine/wasm-cli.mjs engine/wasm'")
    ap.add_argument("--night", choices=["fission", "small-fission", "fuel-cell"], default="fission")
    ap.add_argument("--report", action="store_true", help="print events and bytes per simulated day")
    a = ap.parse_args()
    create = alpha(lots=loop_genesis())
    dusk, period = lunar_day(create)
    e = Engine(a.engine)
    e.create(**create)
    for op, args in loop_setup():
        e.submit(op, **args)
    c = Controller(e, a.night, dusk)
    c.build()
    events, bytes0 = 0, e.bytes_in
    # Small steps while the plant fills and the hopper is made, then hourly.
    t, late_night, night_ok = 0, dusk + 13 * DAY, False
    while t < period:
        t = min(period, t + (5 * MIN if c.hoppers() < 2 else HOUR))
        e.advance_to(t)
        events += sum(1 for _ in e.events())
        c.step()
        # Strictly before dawn: at dawn the sun is back and any plant runs again.
        if late_night <= t < period and c.consumers_running():
            night_ok = True
    hoppers_ok = c.hoppers() >= 2
    c.log(f"{c.clock()}  dawn. hoppers: {c.hoppers()}; producing in the last night: {night_ok}; batches {c.batches}; "
          f"hydrogen {c.v.amount('h2-store', 'h2-g') / 1000:,.0f} mol; spent fuel {c.v.amount('fission-1-core', 'fuel-spent') / 1000:,.1f} mol")
    if a.report:
        days = period / DAY
        c.log(f"report: {events} events ({events / days:.0f} per simulated day); {e.requests} requests; "
              f"{(e.bytes_in - bytes0) / 1e6:.1f} MB of responses")
    e.close()
    return 0 if hoppers_ok and night_ok else 1


if __name__ == "__main__":
    sys.exit(main())
