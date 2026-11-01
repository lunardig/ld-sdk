"""The developer guide's walkthrough (docs/guide.md §5): an excavator fills a hopper; when
the hopper is nearly full, the controller switches the excavator to an empty one. It reads
the claim, issues commands, and advances from one engine event to the next.

    python examples/first_controller.py --engine path/to/ld-engine-cli
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from ld_client import HOUR, MIN, Engine, alpha  # noqa: E402

FULL = 4_800  # kg: switch hoppers here (each holds 5,000 kg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", required=True, help="path to ld-engine-cli, or 'node engine/wasm-cli.mjs engine/wasm'")
    a = ap.parse_args()

    # 1. A claim: the alpha catalog, with our own starting kits in the cargo hold.
    kits = {"solar-kit": "1", "excavator-kit": "1", "hopper-kit": "2"}
    e = Engine(a.engine)
    e.create(**alpha(lots=[{"lot_id": "kits", "location": "inventory", "state": "AVAILABLE", "composition": {}, "products": kits}]))

    # 2. Build: install from kits, connect ports, create and start jobs.
    for product, as_id in [("solar-kit", "solar-1"), ("excavator-kit", "dig-1"), ("hopper-kit", "hopper-1"), ("hopper-kit", "hopper-2")]:
        e.submit("INSTALL", lot_id="kits", product=product, location="pad-a", as_id=as_id)
    e.submit("CONNECT", connection_id="c-face", template="pipe", from_port="icy-crater.face", to_port="dig-1.face")
    e.submit("CONNECT", connection_id="c-out", template="pipe", from_port="dig-1.out", to_port="hopper-1.io")
    for job, process, machine, priority in [("sun", "generate-solar", "solar-1", 100), ("dig", "excavate", "dig-1", 10)]:
        e.submit("CREATE_JOB", job_id=job, process=process, machine_id=machine, target_extent=10**12, priority=priority)
        e.submit("START", job_id=job)

    # 3. Control: advance to the engine's next change, but at most ten minutes; look; act.
    target, spare = "hopper-1", ["hopper-2"]
    while e.now < 6 * HOUR:
        nxt = e.next_event_time()
        e.advance_to(min(e.now + 10 * MIN, nxt) if nxt and nxt > e.now else e.now + 10 * MIN)
        for ev in e.events():  # everything that happened since the last look, in order
            if ev["kind"] == "JOB_STATUS" and ev["job_id"] == "dig":
                why = f" ({ev['reason']['code']})" if "reason" in ev else ""
                print(f"{int(ev['time_us']) / HOUR:5.2f} h  dig {ev['status']}{why}")
        v = e.view()
        if v.amount(target, "regolith") < FULL:
            continue
        if not spare:
            print(f"{e.now / HOUR:5.2f} h  every hopper is full; digging stops by itself (BLOCKED_OUTPUT)")
            break
        # A feed cannot be cut while its machine runs: pause, reconnect, resume.
        e.submit("PAUSE", job_id="dig")
        e.submit("DISCONNECT", connection_id="c-out")
        target = spare.pop(0)
        e.submit("CONNECT", connection_id="c-out", template="pipe", from_port="dig-1.out", to_port=f"{target}.io")
        e.submit("RESUME", job_id="dig")
        print(f"{e.now / HOUR:5.2f} h  switched the excavator to {target}")

    v = e.view()
    print("levels:", {b: v.amount(b, "regolith") for b in ("hopper-1", "hopper-2")}, "kg")
    # The history head commits to everything that happened; a player publishes it.
    print(f"history head {e.history_head()}")
    e.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
