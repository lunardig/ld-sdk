"""Make a challenge the web game can load: build a claim with the engine, let trouble
happen, and write it as a save file with a story, goals and a deadline.

    python examples/make_scenario.py --engine engine/linux-x86_64/ld-engine-cli --out ice-jam.json
    python examples/make_scenario.py --engine ... --out ice-jam.json --solve   # also prove it can be won

Then open lunardig.com, choose Saved games, Import a file, and pick ice-jam.json. The Goal
tab shows the challenge. docs/save-format.md describes the file.

The scenario: a mining plant has run for an hour and jammed. Its ice bin is full, so the
sorter is blocked, so the hopper is full, so the excavator is blocked. An ice melter and a
bulk tank wait in the hold. The player must store 10,000 mol of water within two hours.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from ld_client import HOUR, MIN, Engine, alpha, challenge, goal_stored, save_file  # noqa: E402

WATER_MOL = 10_000
KITS = {"solar-kit": 2, "battery-kit": 1, "excavator-kit": 1, "hopper-kit": 1, "beneficiator-kit": 1, "ice-bin-kit": 1,
        "ilmenite-bin-kit": 1, "tailings-pile-kit": 1, "melter-kit": 1, "bulk-tank-kit": 1}


def build(e: Engine) -> None:
    """The plant as the previous shift left it: everything but the melter and its tank."""
    for kit, name in [("solar-kit", "solar-1"), ("solar-kit", "solar-2"), ("battery-kit", "battery-1"),
                      ("excavator-kit", "dig-1"), ("hopper-kit", "hopper-1"), ("beneficiator-kit", "sorter-1"),
                      ("ice-bin-kit", "ice-bin"), ("ilmenite-bin-kit", "ilmenite-bin"), ("tailings-pile-kit", "tailings")]:
        e.submit("INSTALL", lot_id="kits", product=kit, location="pad-a", as_id=name)
    for cid, a, b in [("c-face", "icy-crater.face", "dig-1.face"), ("c-dig", "dig-1.out", "hopper-1.io"),
                      ("c-feed", "hopper-1.io", "sorter-1.feed"), ("c-ice", "sorter-1.ice", "ice-bin.io"),
                      ("c-ilm", "sorter-1.ilmenite", "ilmenite-bin.io"), ("c-tail", "sorter-1.tailings", "tailings.io")]:
        e.submit("CONNECT", connection_id=cid, template="pipe", from_port=a, to_port=b)
    for job, proc, m, pr in [("sun-1", "generate-solar", "solar-1", 100), ("sun-2", "generate-solar", "solar-2", 100),
                             ("dig", "excavate", "dig-1", 10), ("sort", "beneficiate", "sorter-1", 10)]:
        e.submit("CREATE_JOB", job_id=job, process=proc, machine_id=m, target_extent=10**12, priority=pr)
        e.submit("START", job_id=job)


def solve(e: Engine) -> None:
    """One way to win: melt the ice into a bulk tank."""
    e.submit("INSTALL", lot_id="kits", product="melter-kit", location="pad-a", as_id="melter-1")
    e.submit("INSTALL", lot_id="kits", product="bulk-tank-kit", location="pad-a", as_id="water-store")
    e.submit("CONNECT", connection_id="c-melt-in", template="pipe", from_port="ice-bin.io", to_port="melter-1.ice")
    e.submit("CONNECT", connection_id="c-melt-out", template="pipe", from_port="melter-1.water", to_port="water-store.io")
    e.submit("CREATE_JOB", job_id="melt", process="melt-ice", machine_id="melter-1", target_extent=10**12, priority=10)
    e.submit("START", job_id="melt")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", required=True, help="path to ld-engine-cli, or a command such as 'node engine/wasm-cli.mjs'")
    ap.add_argument("--out", required=True, help="where to write the save file")
    ap.add_argument("--solve", action="store_true", help="also play the challenge from the save and check it can be won")
    a = ap.parse_args()

    create = alpha(lots=[{"lot_id": "kits", "location": "inventory", "state": "AVAILABLE", "composition": {},
                          "products": {k: str(n) for k, n in KITS.items()}}])
    with Engine(a.engine) as e:
        e.create(**create)
        build(e)
        e.advance_by(HOUR)                       # the jam happens on the previous shift
        v = e.view()
        stuck = {j["job_id"]: j["status"] for j in v.jobs}
        print("after one hour:", stuck)
        ch = challenge(
            "ice-jam", "Ice jam",
            story="The night shift left the mine running. An hour later everything has stopped: the ice bin is full, "
                  "so the sorter cannot put ice anywhere, so the hopper has filled, so the excavator has stopped too. "
                  "An ice melter and a bulk tank are still in the hold.",
            brief=f"Store {WATER_MOL:,} mol of water within two hours.",
            goals=[goal_stored("h2o-l", WATER_MOL)],
            deadline_us=2 * HOUR,
            lesson="A jam travels backwards: one full bin stops every machine upstream of it. Fix the end of the chain "
                   "and the whole chain restarts by itself.",
            more="Made with examples/make_scenario.py from the Lunar Dig SDK.",
            author="Lunar Dig SDK example",
        )
        doc = save_file(e, create, challenge=ch)
        Path(a.out).write_text(json.dumps(doc))
        print(f"wrote {a.out}: {len(json.dumps(doc)) // 1024} KiB, claim time {doc['saved_at_claim_time_us']} µs")

    if a.solve:
        # Load the save the way the game does (restore its snapshot) and play it.
        with Engine(a.engine) as e:
            e.restore(doc["snapshot"], doc["catalog"])
            solve(e)
            deadline = int(doc["saved_at_claim_time_us"]) + 2 * HOUR
            while e.now < deadline and e.view().amount("water-store", "h2o-l") < WATER_MOL * 1000:
                e.advance_by(10 * MIN)
            water = e.view().amount("water-store", "h2o-l") / 1000
            won = water >= WATER_MOL and e.now <= deadline
            print(f"solved: {won} ({water:,.0f} mol of water after {(e.now - int(doc['saved_at_claim_time_us'])) // MIN} min)")
            return 0 if won else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
