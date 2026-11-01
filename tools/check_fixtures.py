"""Runs every fixture through an I-1 engine binary and checks it.

    python tools/check_fixtures.py [path/to/ld-mock-cli]

For each fixture:
  1. every response validates against the ld-interfaces I-1 Response schema,
  2. no response is an error, and the final state matches fixture.json's expectations,
  3. determinism: a second run gives the same state hash,
  4. subdivision: splitting every advance into seven steps gives the same state hash (V-05),
  5. restore: snapshot, restore in a fresh engine, advance 30 s; equals continuing uninterrupted (V-09).
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor/ld-interfaces/tools"))
import ld  # noqa: E402

# A path, or a command line such as "node engine/wasm-cli.mjs".
BIN = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "target/release/ld-mock-cli")
FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def run(requests: list[dict]) -> list[dict]:
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    out = subprocess.run(shlex.split(BIN), input=payload, capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines()]


def renumber(requests: list[dict]) -> list[dict]:
    return [{**r, "id": str(i + 1)} for i, r in enumerate(requests)]


def body(requests: list[dict]) -> list[dict]:
    """The fixture without its trailing observe/events/snapshot/state_hash reads."""
    return [r for r in requests if r["method"] in ("create", "restore", "submit", "advance_to")]


def subdivided(requests: list[dict]) -> list[dict]:
    out, now = [], 0
    for r in requests:
        if r["method"] == "advance_to":
            target = int(r["params"]["t_us"])
            for k in range(1, 7):
                # Uneven split points, deliberately off the tick grid.
                out.append({"id": "0", "method": "advance_to", "params": {"t_us": str(now + (target - now) * k // 7 + 123)}})
            out.append(r)
            now = target
        else:
            out.append(r)
    return renumber(out)


def last(responses: list[dict], method: str) -> dict:
    return [r for r in responses if r["method"] == method][-1]["result"]


def main() -> int:
    reg = ld.registry()
    response_validator = ld.validator_for("i1-protocol.schema.json#/$defs/Response", reg)
    for fdir in sorted(p for p in (ROOT / "fixtures").iterdir() if (p / "requests.jsonl").exists()):
        name = fdir.name
        fixture = ld.load_json(fdir / "fixture.json")
        requests = ld.load_jsonl(fdir / "requests.jsonl")
        responses = run(requests)

        bad = [f"response {r.get('id')}: {e.message[:120]}" for r in responses for e in response_validator.iter_errors(r)]
        check(not bad and len(responses) == len(requests), f"{name}: {len(responses)} responses match the I-1 schema" + (f" ({bad[0]})" if bad else ""))
        check(all(r["id"] == q["id"] for r, q in zip(responses, requests)), f"{name}: response ids echo request ids")
        errors = [r["error"] for r in responses if r["method"] == "error"]
        check(not errors, f"{name}: no error responses" + (f" ({errors[0]})" if errors else ""))

        view = last(responses, "observe")
        jobs = {j["job_id"]: j for j in view["jobs"]}
        for job_id, (status, reason) in fixture["expect"].get("jobs", {}).items():
            j = jobs.get(job_id, {})
            got = (j.get("status"), (j.get("reason") or {}).get("code"))
            check(got == (status, reason), f"{name}: {job_id} is {status}/{reason} (got {got[0]}/{got[1]})")
        machines = {m["machine_id"]: m["mode"] for m in view["machines"]}
        for m, mode in fixture["expect"].get("machines", {}).items():
            check(machines.get(m) == mode, f"{name}: machine {m} is {mode}")
        admissions = {r["result"]["command_id"]: r["result"]["status"] for r in responses if r["method"] == "submit"}
        for cid, status in fixture["expect"].get("admissions", {}).items():
            check(admissions.get(cid) == status, f"{name}: {cid} admitted as {status}")
        for code in fixture["expect"].get("events", []):
            seen = any((e.get("reason") or {}).get("code") == code for e in last(responses, "events_since")["events"])
            check(seen, f"{name}: event log contains {code}")

        final_hash = last(responses, "state_hash")["hash"]
        check(last(responses, "snapshot")["envelope"]["state_hash"] == final_hash, f"{name}: snapshot envelope hash equals state_hash")
        check(last(run(requests), "state_hash")["hash"] == final_hash, f"{name}: deterministic across runs")

        tail = [{"id": "0", "method": "state_hash", "params": {}}]
        sub = run(subdivided(body(requests)) + tail)
        check(sub[-1]["result"]["hash"] == final_hash, f"{name}: subdivided advances give the same state (V-05)")

        later = str(int(fixture["final_time_us"]) + 30_000_000)
        go_on = [{"id": "0", "method": "advance_to", "params": {"t_us": later}}, {"id": "0", "method": "state_hash", "params": {}}]
        straight = run(renumber(body(requests) + go_on))[-1]["result"]["hash"]
        snapshot = last(responses, "snapshot")
        create = requests[0]["params"]
        resumed = run(renumber([{"id": "0", "method": "restore", "params": {"snapshot": snapshot, "catalog": create["catalog"]}}] + go_on))
        check(resumed[0]["method"] == "restore", f"{name}: snapshot restores" + ("" if resumed[0]["method"] == "restore" else f" ({resumed[0]})"))
        check(resumed[-1]["result"]["hash"] == straight, f"{name}: restore then advance equals uninterrupted run (V-09)")

    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
