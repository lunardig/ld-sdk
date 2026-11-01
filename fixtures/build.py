"""Builds the mock fixtures (LD-PEC-002 §6): a catalog and six scenarios.

Each scenario is written as fixtures/<name>/requests.jsonl, a plain sequence of I-1
requests that any client can replay against any engine, plus fixture.json describing
the state it reaches. Run from the repository root:

    python fixtures/build.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor/ld-interfaces/tools"))
import ld  # noqa: E402

CAT = ROOT / "fixtures/catalog"
S = 1_000_000  # microseconds per second
LUNAR_DAY_US = 2_551_442_877_000  # 29.530588853 days
DUSK_US = LUNAR_DAY_US // 2


def R(n, d=1):
    return {"n": str(n), "d": str(d)}


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


# ------------------------------------------------------------------ catalog
def build_catalog() -> None:
    if CAT.exists():
        shutil.rmtree(CAT)
    write(CAT / "elements/h.json", {"element_id": "h", "symbol": "H", "molar_mass": R(126, 125)})
    write(CAT / "elements/o.json", {"element_id": "o", "symbol": "O", "molar_mass": R(15999, 1000)})
    for uid, dim, scale in [("mmol", "amount", R(1, 1000)), ("rxn-mmol", "amount", R(1, 1000)), ("j", "energy", R(1)),
                            ("w", "power", R(1)), ("wu", "work", R(1)), ("s", "time", R(1)), ("w-m2", "power", R(1))]:
        write(CAT / f"units/{uid}.json", {"unit_id": uid, "dimension": dim, "scale": scale})
    for sid, formula, phase in [("h2o-l", {"h": "2", "o": "1"}, "liquid"), ("h2-g", {"h": "2"}, "gas"), ("o2-g", {"o": "2"}, "gas")]:
        write(CAT / f"substances/{sid}.json", {"substance_id": sid, "formula": formula, "phase": phase, "quantity_unit": "mmol"})

    write(CAT / "processes/electrolyze-water.json", {
        "process_id": "electrolyze-water", "pset_version": "0", "mode": "FLOW",
        "extent": {"unit": "rxn-mmol", "min": "0", "max": "1000000000000"},
        "ports": {"in": [{"port": "water", "accepts": ["h2o-l"]}],
                  "out": [{"port": "h2", "accepts": ["h2-g"]}, {"port": "o2", "accepts": ["o2-g"]}]},
        "stages": [{"stage_id": "split", "ops": [
            {"op": "REACT", "vector": {"h2-g": "2", "h2o-l": "-2", "o2-g": "1"}, "extent": {"kind": "ref", "name": "extent"}},
            {"op": "WORK", "a": R(1), "b": R(0)},
            {"op": "ENERGY", "mode": "PER_WORK", "dir": "DRAW", "amount": R(572), "unit": "j", "heat_sink": "waste-heat"},
            {"op": "WEAR", "per": "WORK", "amount": R(1, 1000)}]}],
        "edges": [{"from": "port:water", "to": "stage:split"},
                  {"from": "stage:split", "to": "port:h2", "species": ["h2-g"]},
                  {"from": "stage:split", "to": "port:o2", "species": ["o2-g"]}],
        "failure_disposition": {"split": "RELEASE"},
        "restart_thresholds": {"water": {"restart_at": "5000", "unit": "mmol"}},
        "constants": {}})
    write(CAT / "processes/generate-solar.json", {
        "process_id": "generate-solar", "pset_version": "0", "mode": "FLOW",
        "extent": {"unit": "s", "min": "0", "max": "1000000000000"},
        "ports": {"in": [], "out": []},
        "stages": [{"stage_id": "generate", "ops": [
            {"op": "REQUIRE", "predicate": {"kind": "environment", "function_id": "irradiance", "min": R(100), "max": R(2000)}},
            {"op": "WORK", "a": R(1), "b": R(0)},
            {"op": "ENERGY", "mode": "PER_WORK", "dir": "PRODUCE", "amount": R(1_000_000), "unit": "j", "heat_sink": "waste-heat"}]}],
        "edges": [], "failure_disposition": {"generate": "RETAIN"}, "restart_thresholds": {}, "constants": {}})

    ports_e = [{"port": "water", "type": "MATERIAL", "direction": "IN"}, {"port": "h2", "type": "MATERIAL", "direction": "OUT"},
               {"port": "o2", "type": "MATERIAL", "direction": "OUT"}, {"port": "power", "type": "ELECTRICAL", "direction": "IN"}]
    def machine(bid, processes, rated, ports, table, caps):
        write(CAT / f"machines/{bid}.json", {
            "blueprint_id": bid, "capabilities": caps, "processes": processes, "rated_work": rated, "lanes": "1",
            "min_stable_rate": R(1, 1_000_000_000), "idle_power": {"amount": R(0), "unit": "w"}, "ports": ports,
            "condition_table": table, "embodied": {}})
    machine("electrolyzer", ["electrolyze-water"], R(1, 1000), ports_e,
            [{"wear_at": "0", "capacity_factor": R(1)}, {"wear_at": "1000000000", "capacity_factor": R(1, 2)}], ["electrolysis"])
    machine("electrolyzer-fragile", ["electrolyze-water"], R(1, 1000), ports_e,
            [{"wear_at": "0", "capacity_factor": R(1)}, {"wear_at": "100", "capacity_factor": R(0)}], ["electrolysis"])
    machine("solar-array", ["generate-solar"], R(1, 1_000_000), [{"port": "power", "type": "ELECTRICAL", "direction": "OUT"}],
            [{"wear_at": "0", "capacity_factor": R(1)}], ["solar"])

    for tid, cap in [("tank-small", "1000000"), ("tank-tiny", "10000")]:
        write(CAT / f"buffers/{tid}.json", {"template_id": tid, "accepts": ["h2o-l", "h2-g", "o2-g"], "capacity": {"amount": cap, "unit": "mmol"}})
    # A pipe couples a machine port to a tank; a conveyor carries shipments between tanks.
    write(CAT / "connections/pipe.json", {"template_id": "pipe", "kind": "FEED", "throughput": R(1, 100), "unit": "mmol", "delay_us": "0"})
    write(CAT / "connections/conveyor.json", {"template_id": "conveyor", "kind": "CONVEYOR", "throughput": R(1, 1000), "unit": "mmol", "delay_us": "2000000"})
    for pid, kind, tpl in [("electrolyzer-kit", "MACHINE", "electrolyzer"), ("fragile-electrolyzer-kit", "MACHINE", "electrolyzer-fragile"),
                           ("solar-kit", "MACHINE", "solar-array"), ("tank-kit", "BUFFER", "tank-small"), ("tiny-tank-kit", "BUFFER", "tank-tiny")]:
        write(CAT / f"products/{pid}.json", {"product_id": pid, "embodied": {}, "installs_as": {"kind": kind, "template": tpl}})
    write(CAT / "environment/irradiance.json", {
        "function_id": "irradiance", "unit": "w-m2", "period_us": str(LUNAR_DAY_US),
        "segments": [{"start_us": "0", "value": R(1361)}, {"start_us": str(DUSK_US), "value": R(0)}]})
    write(CAT / "manifest.json", ld.manifest_for("mock-alpha", ld.read_catalog_entries(CAT)))


# ------------------------------------------------------------------ scenarios
class Script:
    """Accumulates I-1 requests; commands always take effect at the current time."""

    def __init__(self, water_mmol: int, reserve_mmol: int = 0):
        self.requests: list[dict] = []
        self.now = 0
        self.seq = 0
        genesis = {
            "schema": "ld.genesis.v0", "world_id": "mock-world", "claim_id": "claim-0001",
            "catalog_hash": ld.catalog_hash(ld.load_json(CAT / "manifest.json")), "start_us": "0",
            "deposits": [], "environment": ["irradiance"],
            "starting_lots": [
                {"lot_id": "starter-kit", "location": "inventory", "state": "AVAILABLE", "composition": {},
                 "products": {"electrolyzer-kit": "2", "fragile-electrolyzer-kit": "1", "solar-kit": "1", "tank-kit": "4", "tiny-tank-kit": "1"}},
                {"lot_id": "water-1", "location": "water-tank", "state": "AVAILABLE", "composition": {"h2o-l": str(water_mmol)}, "products": {}},
            ] + ([{"lot_id": "water-2", "location": "reserve-tank", "state": "AVAILABLE", "composition": {"h2o-l": str(reserve_mmol)}, "products": {}}]
                 if reserve_mmol else [])}
        self.req("create", {"genesis": genesis, "catalog": ld.bundle_for(CAT)})

    def req(self, method: str, params: dict) -> None:
        self.requests.append({"id": str(len(self.requests) + 1), "method": method, "params": params})

    def cmd(self, op: str, **args) -> str:
        self.seq += 1
        cid = f"cmd-{self.seq:04d}"
        self.req("submit", {"command": {"command_id": cid, "effective_us": str(self.now), "seq": str(self.seq), "op": op,
                                        "args": {k: str(v) if isinstance(v, int) else v for k, v in args.items()}}})
        return cid

    def advance(self, t_us: int) -> None:
        self.now = t_us
        self.req("advance_to", {"t_us": str(t_us)})

    def install(self, product: str, as_id: str, location: str = "pad-a") -> None:
        self.cmd("INSTALL", lot_id="starter-kit", product=product, location=location, as_id=as_id)

    def plant(self, electrolyzer_kit="electrolyzer-kit", h2_tank_kit="tank-kit") -> None:
        """Solar array, one electrolyzer, three tanks, connected. Jobs not yet created."""
        self.install("solar-kit", "solar-1")
        self.install(electrolyzer_kit, "electrolyzer-1")
        self.install("tank-kit", "water-tank")
        self.install(h2_tank_kit, "h2-tank")
        self.install("tank-kit", "o2-tank")
        self.cmd("CONNECT", template="pipe", connection_id="c-water", from_port="water-tank.io", to_port="electrolyzer-1.water")
        self.cmd("CONNECT", template="pipe", connection_id="c-h2", from_port="electrolyzer-1.h2", to_port="h2-tank.io")
        self.cmd("CONNECT", template="pipe", connection_id="c-o2", from_port="electrolyzer-1.o2", to_port="o2-tank.io")

    def jobs(self) -> None:
        self.cmd("CREATE_JOB", job_id="job-solar", process="generate-solar", machine_id="solar-1", target_extent=10**12, priority=100)
        self.cmd("START", job_id="job-solar")
        self.cmd("CREATE_JOB", job_id="job-electrolysis", process="electrolyze-water", machine_id="electrolyzer-1", target_extent=10**9, priority=10)
        self.cmd("START", job_id="job-electrolysis")

    def finish(self) -> None:
        self.req("observe", {})
        self.req("events_since", {"cursor": "0", "limit": "1000"})
        self.req("snapshot", {})
        self.req("state_hash", {})


def scenarios() -> dict[str, tuple[str, Script, dict]]:
    out = {}

    s = Script(water_mmol=500_000); s.plant(); s.jobs(); s.advance(60 * S); s.finish()
    out["running-loop"] = ("Solar power feeding an electrolyzer that splits water into hydrogen and oxygen tanks.", s,
                           {"jobs": {"job-solar": ["RUNNING", None], "job-electrolysis": ["RUNNING", None]}})

    s = Script(water_mmol=50_000, reserve_mmol=2_000); s.plant(); s.install("tank-kit", "reserve-tank"); s.jobs(); s.advance(60 * S)
    s.cmd("CONNECT", template="conveyor", connection_id="c-reserve", from_port="reserve-tank.io", to_port="water-tank.io")
    s.cmd("TRANSFER_LOCAL", connection_id="c-reserve", species="h2o-l", amount=2_000)
    s.advance(120 * S); s.finish()
    out["starved-input"] = ("The water tank runs dry after 25 s and the job reports STARVED_INPUT. At 60 s a conveyor brings 2 mol from a "
                            "reserve tank, which is less than the 5 mol restart threshold, so the job waits below it.", s,
                            {"jobs": {"job-electrolysis": ["STARVED", "BELOW_RESTART_THRESHOLD"]}, "events": ["STARVED_INPUT"]})

    s = Script(water_mmol=500_000); s.plant(h2_tank_kit="tiny-tank-kit"); s.jobs(); s.advance(60 * S); s.finish()
    out["blocked-output"] = ("The hydrogen tank is tiny and fills in 5 s; the electrolyzer blocks on its h2 output.", s,
                             {"jobs": {"job-electrolysis": ["BLOCKED", "BLOCKED_OUTPUT"]}})

    s = Script(water_mmol=500_000); s.plant(); s.advance(DUSK_US - 10 * S); s.jobs(); s.advance(DUSK_US + 60 * S); s.finish()
    out["lunar-night"] = ("The plant starts 10 s before lunar dusk. At nightfall solar output stops and the electrolyzer loses power.", s,
                          {"jobs": {"job-solar": ["STARVED", "STARVED_REQUIRE"], "job-electrolysis": ["STARVED", "STARVED_POWER"]}})

    s = Script(water_mmol=500_000); s.plant(electrolyzer_kit="fragile-electrolyzer-kit"); s.jobs(); s.advance(120 * S); s.finish()
    out["failed-machine"] = ("A fragile electrolyzer wears out after 100 s of work and fails; the job reports FAILED in its last event and leaves the state (LD-PEC-004 A2-1).", s,
                             {"machines": {"electrolyzer-1": "FAILED"}, "events": ["FAILED"]})

    s = Script(water_mmol=500_000); s.plant()
    bad = s.cmd("CREATE_JOB", job_id="job-orphan", process="electrolyze-water", machine_id="no-such-machine", target_extent=1000, priority=1)
    s.finish()
    out["rejected-command"] = ("A job is created for a machine that does not exist; the command is rejected and nothing changes.", s,
                               {"admissions": {bad: "REJECTED_STATE"}})
    return out


# ------------------------------------------------------------------ the alpha catalog
ALPHA = ROOT / "fixtures/alpha"


def build_alpha() -> None:
    """The starter catalog plus the M2a mining chain (LD-PEC-005 §4) and the M2c loop
    closers (§8.4: reduction, fabrication, a battery), for clients on ld-engine. Not a mock fixture: the mock engine stops at M1, so this has no
    requests.jsonl, only create.json (I-1 create params) and the catalog."""
    import m2_vectors as m2  # the mining chemistry, from ld-interfaces
    import m2c_vectors as m2c  # the loop closers
    import m3a_vectors as m3a  # the M3a sorter and reactor (LD-PEC-006 §3.3)
    import m3b_vectors as m3b  # the fuel cell and fission reactor (LD-PEC-006 §3.2)

    cat = ALPHA / "catalog"
    if ALPHA.exists():
        shutil.rmtree(ALPHA)
    shutil.copytree(CAT, cat)
    (cat / "manifest.json").unlink()
    entries = {
        "elements": [e for e in m2.elements() if e["element_id"] in ("si", "fe", "ti")] + [m3b.URANIUM],
        "units": [u for u in m2c.m2c_units() if u["unit_id"] in ("kg-reg", "count")],
        # Every substance carries its energy content (LD-PEC-006 §3.2.1). Fission fuel holds
        # 300 MJ per mmol of UO2 (1.55 % FIMA, about 1.1 TJ/kg), after review (Q-13a).
        "substances": [x for x in m2c.m2c_substances()
                       if x["substance_id"] in ("regolith-bank", "regolith", "h2o-s", "h2o-l", "h2-g", "o2-g", "fetio3-s", "sio2-s", m2c.IRON, m2c.RUTILE)]
        + m3b.fuel_substances(300_000_000),
    }
    for key, items in entries.items():
        for item in items:
            write(cat / key / f"{item[next(k for k in item if k.endswith('_id'))]}.json", item)

    # Rates for play: the excavator takes 100 kg in 50 s (2 kg/s), which the
    # beneficiator matches; the melter keeps up with the ice that makes.
    excavate, melt = m2.excavate(), m2.melt()
    beneficiate, reduce, fabricate = m3a.alpha_beneficiate(), m3a.alpha_reduce(), m2c.fabricate_hopper()
    # Fabrication melts and forms the iron: 200 MJ per hopper (LD-PEC-006 §2.1).
    fabricate["stages"][0]["ops"][2]["amount"] = R(200_000_000)
    # Electrolysis at about 70 % of the ideal: 800 J per mmol of reaction, not the 572 J
    # minimum (LD-PEC-006 §2.1, Q-06b). The starter catalog keeps the ideal.
    electrolyze = m2.electrolyze()
    electrolyze["stages"][0]["ops"][2]["amount"] = R(800)
    electrolyze["stages"][0]["ops"].append({"op": "WEAR", "per": "WORK", "amount": R(1, 1000)})
    electrolyze["stages"][0]["stage_id"] = "split"
    electrolyze["edges"] = [{"from": "port:water", "to": "stage:split"}, {"from": "stage:split", "to": "port:h2", "species": ["h2-g"]},
                            {"from": "stage:split", "to": "port:o2", "species": ["o2-g"]}]
    electrolyze["failure_disposition"] = {"split": "RELEASE"}
    # Level switches sized by run time (LD-PEC-006 §2.5, Q-23 review): the melter restarts
    # at 1,600 mol of ice, about 4 minutes at rated output, at most 3.6 starts an hour;
    # the electrolyzer at 3,700 mol of water, an hour at rated output, since full stops
    # wear PEM stacks. Engineering estimates. Each stop and restart is also an event: a
    # 20 mol band cycled every 20 s and cost 10,000 events per simulated day.
    melt["restart_thresholds"] = {"ice": {"restart_at": "1600000", "unit": "mmol"}}
    electrolyze["restart_thresholds"] = {"water": {"restart_at": "3700000", "unit": "mmol"}}
    # The fuel cell sells 286 J of the 571.66 J a mmol of reaction releases; fission sells
    # 25 % of its fuel's energy (Stirling or Brayton conversion). Both follow load.
    fuel_cell, fission = m3b.fuel_cell(286), m3b.fission(75_000_000)
    for proc in (excavate, beneficiate, melt, reduce, fabricate, electrolyze, fuel_cell, fission):
        write(cat / f"processes/{proc['process_id']}.json", proc)
    power = {"port": "power", "type": "ELECTRICAL", "direction": "IN"}

    def mport(name, direction):
        return {"port": name, "type": "MATERIAL", "direction": direction}

    for bid, procs, rated, ports, caps in [
        ("excavator", ["excavate"], R(1, 50_000_000), [mport("face", "IN"), mport("out", "OUT"), power], ["excavation"]),
        ("beneficiator", ["beneficiate"], R(1, 500_000), [mport("feed", "IN"), mport("ice", "OUT"), mport("ilmenite", "OUT"), mport("tailings", "OUT"), power],
         ["beneficiation"]),
        ("ice-melter", ["melt-ice"], R(6, 1000), [mport("ice", "IN"), mport("water", "OUT"), power], ["melting"]),
        # Feeds 1 mol/s of ilmenite and reacts half of it (LD-PEC-006 §3.3).
        ("reactor", ["reduce-ilmenite"], R(1, 2000), [mport("ilmenite", "IN"), mport("h2", "IN"), mport("iron", "OUT"), mport("rutile", "OUT"),
                                                     mport("water", "OUT"), mport("unreacted", "OUT"), power], ["reduction"]),
        # Ten minutes per hopper: 200 MJ at about 333 kW (LD-PEC-006 §2.1).
        ("fabricator", ["fabricate-hopper"], R(1, 600_000_000), [mport("iron", "IN"), mport("scrap", "OUT"), power], ["fabrication"]),
        ("battery", [], R(1, 1_000_000), [power], ["storage"]),
        # 400 kW: 1.4 mol of reaction per second, sized for a 300 kW load with margin (Q-13b).
        ("fuel-cell", ["fuel-cell"], R(7, 5000), [mport("h2", "IN"), mport("o2", "IN"), mport("water", "OUT"),
                                                 {"port": "power", "type": "ELECTRICAL", "direction": "OUT"}], ["fuel-cell"]),
        # 1 MWe (4 MWth); the core cannot run below 30 % (Q-13a). Below the load, the
        # surplus goes to a dump load: waste heat (LD-PEC-006 §3.2.2).
        ("fission-reactor", ["fission"], R(1, 75_000_000), [mport("fuel", "IN"), mport("spent", "OUT"),
                                                             {"port": "power", "type": "ELECTRICAL", "direction": "OUT"}], ["fission"]),
        # The small tier: 400 kWe (1.6 MWth), for a firm night load without a megawatt's
        # dump (LD-PEC-006 Q-28, Q-24 review). Same fuel and conversion; 30 % floor, 120 kWe.
        ("fission-reactor-small", ["fission"], R(1, 187_500_000), [mport("fuel", "IN"), mport("spent", "OUT"),
                                                                    {"port": "power", "type": "ELECTRICAL", "direction": "OUT"}], ["fission"]),
    ]:
        write(cat / f"machines/{bid}.json", {
            "blueprint_id": bid, "capabilities": caps, "processes": procs, "rated_work": rated, "lanes": "1",
            "min_stable_rate": {"fission-reactor": R(1, 250_000_000), "fission-reactor-small": R(1, 625_000_000)}.get(bid, R(1, 1_000_000_000)),
            "idle_power": {"amount": R(0), "unit": "w"}, "ports": ports,
            "condition_table": [{"wear_at": "0", "capacity_factor": R(1)}, {"wear_at": "1000000", "capacity_factor": R(1, 2)}], "embodied": {},
            # 100 kWh, 400 kW in, 600 kW out: enough to carry the electrolyzer a few
            # minutes past dusk. Placeholders (LD-PEC-005 Q-06).
            **({"energy_store": {"capacity": {"amount": R(360_000_000), "unit": "j"}, "max_charge": {"amount": R(400_000), "unit": "w"},
                                 "max_discharge": {"amount": R(600_000), "unit": "w"}, "charge_efficiency": R(9, 10), "heat_sink": "battery-heat"}}
               if bid == "battery" else {}),
            # A reactor carries its fuel in a core that travels with its kit (LD-PEC-004 A2-3,
            # LD-PEC-006 §3.7): fission draws fresh fuel from it and returns spent fuel to it.
            **({"core": {"buffer": f"{bid}-core", "connection": "core-link", "ports": ["fuel", "spent"]}}
               if bid.startswith("fission-reactor") else {})})
    # The link between a reactor and its core: internal, fast enough never to limit it.
    write(cat / "connections/core-link.json", {"template_id": "core-link", "kind": "FEED", "throughput": R(1000), "unit": "mmol", "delay_us": "0"})
    for tid, accepts, amount, unit in [("hopper", ["regolith"], "5000", "kg-reg"), ("ice-bin", ["h2o-s"], "2000000", "mmol"),
                                       ("ilmenite-bin", ["fetio3-s"], "2000000", "mmol"), ("iron-bin", [m2c.IRON], "5000000", "mmol"),
                                       ("rutile-bin", [m2c.RUTILE, m2c.IRON], "1000000000", "mmol"), ("scrap-bin", [m2c.IRON], "1000000", "mmol"),
                                       # Everything the plant does not recover, mostly silicate: about 14 mol per kg dug.
                                       # 2 kg/s leaves about 4,700 t (78 million mol) a lunar month; the pile
                                       # holds 100 million mol, about 38 days (LD-PEC-006 §3.1, Q-25 review).
                                       # The rutile bin takes a month of reduction: about 1 million mol.
                                       ("tailings-pile", ["sio2-s", "h2o-s", "fetio3-s"], "100000000000", "mmol"),
                                       # Night storage is big: 5 million mol (LD-PEC-006 §3.2.4).
                                       ("bulk-tank", ["h2-g", "o2-g", "h2o-l"], "5000000000", "mmol"),
                                       # Reactor cores, loaded for ten years at rated power (LD-PEC-006 §3.7):
                                       # 1.15 mol a day at 4 MWth, so 4,200 mol; 1,700 for the small unit.
                                       # Capacity leaves 1 % headroom: spent fuel needs room reserved before
                                       # fresh fuel is drawn, even in the same buffer (LD-PEC-004 A2-3).
                                       ("fission-reactor-core", [m3b.FRESH, m3b.SPENT], "4242000", "mmol"),
                                       ("fission-reactor-small-core", [m3b.FRESH, m3b.SPENT], "1717000", "mmol")]:
        write(cat / f"buffers/{tid}.json", {"template_id": tid, "accepts": accepts, "capacity": {"amount": amount, "unit": unit}})
    for pid, kind, tpl in [("excavator-kit", "MACHINE", "excavator"), ("beneficiator-kit", "MACHINE", "beneficiator"),
                           ("melter-kit", "MACHINE", "ice-melter"), ("hopper-kit", "BUFFER", "hopper"), ("ice-bin-kit", "BUFFER", "ice-bin"),
                           ("ilmenite-bin-kit", "BUFFER", "ilmenite-bin"), ("reactor-kit", "MACHINE", "reactor"),
                           ("fabricator-kit", "MACHINE", "fabricator"), ("battery-kit", "MACHINE", "battery"), ("iron-bin-kit", "BUFFER", "iron-bin"),
                           ("rutile-bin-kit", "BUFFER", "rutile-bin"), ("scrap-bin-kit", "BUFFER", "scrap-bin"),
                           ("tailings-pile-kit", "BUFFER", "tailings-pile"), ("fuel-cell-kit", "MACHINE", "fuel-cell"),
                           ("fission-reactor-kit", "MACHINE", "fission-reactor"), ("small-reactor-kit", "MACHINE", "fission-reactor-small"), ("bulk-tank-kit", "BUFFER", "bulk-tank"),
                           ]:
        # A hopper embodies 1 800 mol of iron, about 100 kg (LD-PEC-005 §8.4).
        embodied = {m2c.IRON: str(m2c.HOPPER_IRON)} if pid == "hopper-kit" else {}
        write(cat / f"products/{pid}.json", {"product_id": pid, "embodied": embodied, "installs_as": {"kind": kind, "template": tpl}})
    write(cat / "manifest.json", ld.manifest_for("alpha-m3b", ld.read_catalog_entries(cat)))

    genesis = {
        "schema": "ld.genesis.v0", "world_id": "alpha-world", "claim_id": "claim-0001",
        "catalog_hash": ld.catalog_hash(ld.load_json(cat / "manifest.json")), "start_us": "0",
        "deposits": [{"deposit_id": "icy-crater", "remaining": {"regolith-bank": "1000000000"}, "extracted": {}}],
        "environment": ["irradiance"],
        "starting_lots": [{"lot_id": "starter-kit", "location": "inventory", "state": "AVAILABLE", "composition": {},
                           "products": {"solar-kit": "1", "excavator-kit": "1", "hopper-kit": "1"}}],
    }
    write(ALPHA / "create.json", {"genesis": genesis, "catalog": ld.bundle_for(cat)})
    print(f"alpha: catalog {ld.catalog_hash(ld.load_json(cat / 'manifest.json'))[:12]}")


def main() -> None:
    build_catalog()
    build_alpha()
    for name, (description, script, expect) in scenarios().items():
        d = ROOT / "fixtures" / name
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        (d / "requests.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in script.requests))
        write(d / "fixture.json", {"name": name, "description": description, "final_time_us": str(script.now), "expect": expect})
        print(f"{name}: {len(script.requests)} requests")


if __name__ == "__main__":
    main()
