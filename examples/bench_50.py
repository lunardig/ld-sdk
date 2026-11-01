"""The 50-machine benchmark (LD-PEC-006 §3.5): ten parallel mining chains on the alpha
catalog (solar array, excavator, beneficiator, melter, electrolyzer: 50 machines and
70 stores), run through one lunar daylight period, about 14.8 days. Reports events,
event bytes and wall time per simulated day.

    python examples/bench_50.py --engine ../ld-engine/target/release/ld-engine-cli
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_controller import ALPHA, DAY, DUSK, HOUR, Engine  # noqa: E402

CHAINS = 10


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", required=True, help="path to ld-engine-cli, or a command such as 'node tools/wasm-cli.mjs'")
    ap.add_argument("--step-hours", type=int, default=6, help="advance this many hours between observations")
    a = ap.parse_args()
    create = json.loads(ALPHA.read_text())
    kits = {"solar-kit": CHAINS, "excavator-kit": CHAINS, "beneficiator-kit": CHAINS, "melter-kit": CHAINS, "electrolyzer-kit": CHAINS,
            "hopper-kit": CHAINS, "ice-bin-kit": CHAINS, "tailings-pile-kit": CHAINS, "bulk-tank-kit": 4 * CHAINS}
    create["genesis"]["starting_lots"] = [{"lot_id": "kits", "location": "inventory", "state": "AVAILABLE", "composition": {},
                                           "products": {k: str(n) for k, n in kits.items()}}]
    e = Engine(a.engine)
    e.call("create", create)
    for i in range(CHAINS):
        def inst(p, n):
            e.cmd("INSTALL", lot_id="kits", product=p, location="pad-a", as_id=f"{n}-{i}")
        for p, n in [("solar-kit", "solar"), ("excavator-kit", "dig"), ("beneficiator-kit", "sort"), ("melter-kit", "melt"),
                     ("electrolyzer-kit", "split"), ("hopper-kit", "hopper"), ("ice-bin-kit", "ice"), ("tailings-pile-kit", "tail"),
                     ("bulk-tank-kit", "water"), ("bulk-tank-kit", "h2"), ("bulk-tank-kit", "o2"), ("bulk-tank-kit", "spare")]:
            inst(p, n)
        for c, f, t in [("face", "icy-crater.face", "dig-{i}.face"), ("dug", "dig-{i}.out", "hopper-{i}.io"), ("feed", "hopper-{i}.io", "sort-{i}.feed"),
                        ("ice", "sort-{i}.ice", "ice-{i}.io"), ("ilm", "sort-{i}.ilmenite", "tail-{i}.io"), ("tail", "sort-{i}.tailings", "tail-{i}.io"),
                        ("melt", "ice-{i}.io", "melt-{i}.ice"), ("water", "melt-{i}.water", "water-{i}.io"), ("split", "water-{i}.io", "split-{i}.water"),
                        ("h2", "split-{i}.h2", "h2-{i}.io"), ("o2", "split-{i}.o2", "o2-{i}.io")]:
            e.cmd("CONNECT", connection_id=f"c-{c}-{i}", template="pipe", from_port=f.format(i=i), to_port=t.format(i=i))
        for jid, proc, m, pr in [("sun", "generate-solar", "solar", 100), ("dig", "excavate", "dig", 10), ("sort", "beneficiate", "sort", 10),
                                 ("melt", "melt-ice", "melt", 10), ("split", "electrolyze-water", "split", 5)]:
            e.cmd("CREATE_JOB", job_id=f"job-{jid}-{i}", process=proc, machine_id=f"{m}-{i}", target_extent=10**12, priority=pr)
            e.cmd("START", job_id=f"job-{jid}-{i}")
    machines = len(e.view()["machines"])
    t, start = 0, time.time()
    while t < DUSK:
        t = min(DUSK, t + a.step_hours * HOUR)
        e.advance(t)
        e.drain()
    wall = time.time() - start
    v = e.view()
    days = DUSK / DAY
    running = sum(1 for j in v["jobs"] if j["status"] == "RUNNING")
    print(json.dumps({"engine": a.engine, "machines": machines, "stores": len(v["buffers"]), "simulated_days": round(days, 2), "events": e.events,
                      "events_per_day": round(e.events / days), "event_kb_per_day": round(e.event_bytes / days / 1000),
                      "observe_kb": round(len(json.dumps(v)) / 1000, 1), "wall_s": round(wall, 1), "wall_s_per_day": round(wall / days, 2),
                      "running_at_dusk": running}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
