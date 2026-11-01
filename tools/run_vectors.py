"""Conformance harness for I-4 EXECUTION vectors (LD-PEC-002 §7).

    python tools/run_vectors.py <engine-cli> <vectors-dir> [--write] [--only V-05a]

Drives any engine that speaks I-1 over stdin/stdout (ld-engine-cli, ld-mock-cli, the
reference evaluator) through each vector:

* `create` from genesis.json and the catalog directory;
* each script step, submitting every command whose effective time has been reached
  first: the harness advances to a command's effective_us, then submits it;
* `snapshot` steps keep the snapshot by name; `restore` steps restore it into a fresh
  engine instance (the event log of a restored engine starts again at seq 0).

It records admissions, the drained event log, the state hash and history head after
every step, and the final PhysicalState. Without --write it compares them byte for
byte with expected/ (ADR-16: no tolerance). With --write it (re)writes expected/.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor/ld-interfaces/tools"))
import ld  # noqa: E402


class Engine:
    def __init__(self, binary: str):
        # A path, or a command line such as "node engine/wasm-cli.mjs".
        self.p = subprocess.Popen(shlex.split(binary), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.n = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.p.stdin.write(json.dumps({"id": str(self.n), "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        r = json.loads(self.p.stdout.readline())
        if r["method"] == "error":
            raise RuntimeError(f"{method}: {r['error']}")
        return r["result"]

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait()


def run(binary: str, vdir: Path) -> dict:
    genesis = ld.load_json(vdir / "genesis.json")
    catalog = ld.bundle_for(vdir / "catalog")
    commands = ld.load_jsonl(vdir / "commands.jsonl")
    script = ld.load_jsonl(vdir / "script.jsonl")
    e = Engine(binary)
    e.call("create", {"genesis": genesis, "catalog": catalog})
    now, cursor = int(genesis["start_us"]), 0
    admissions, events, hashes, snapshots = [], [], [], {}

    def drain() -> None:
        nonlocal cursor
        while True:
            page = e.call("events_since", {"cursor": str(cursor), "limit": "1000"})
            events.extend(page["events"])
            if not page["events"]:
                return
            cursor = int(page["next_cursor"])

    def submit_due(t: int) -> None:
        nonlocal now
        while commands and int(commands[0]["effective_us"]) <= t:
            c = commands.pop(0)
            eff = int(c["effective_us"])
            if eff > now:
                e.call("advance_to", {"t_us": str(eff)})
                now = eff
            admissions.append(e.call("submit", {"command": c}))

    for i, step in enumerate(script):
        if step["step"] == "advance_to":
            t = int(step["t_us"])
            submit_due(t)
            e.call("advance_to", {"t_us": str(t)})
            now = t
        elif step["step"] == "snapshot":
            snapshots[step["name"]] = e.call("snapshot")
        elif step["step"] == "restore":
            drain()
            e.call("restore", {"snapshot": snapshots[step["name"]], "catalog": catalog})
            cursor = 0
            now = int(snapshots[step["name"]]["state"]["time_us"])
        drain()
        hashes.append({"step": str(i), "state_hash": e.call("state_hash")["hash"], "history_head": e.call("history_head")["hash"]})
    submit_due(now)
    drain()
    state = e.call("snapshot")["state"]
    e.close()
    return {"admissions": admissions, "events": events, "hashes": hashes, "state": state}


def jsonl(rows: list) -> str:
    return "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows)


def files(result: dict) -> dict[str, str]:
    return {
        "admissions.jsonl": jsonl(result["admissions"]),
        "events.jsonl": jsonl(result["events"]),
        "hashes.json": json.dumps(result["hashes"], indent=2) + "\n",
        "state.json": json.dumps(result["state"], indent=2, sort_keys=True) + "\n",
    }


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("engine")
    a.add_argument("vectors", type=Path)
    a.add_argument("--write", action="store_true", help="write expected/ from this engine")
    a.add_argument("--only", help="run only vectors whose id starts with this")
    args = a.parse_args()
    failures = 0
    for vdir in sorted(p for p in args.vectors.iterdir() if (p / "vector.json").exists()):
        manifest = ld.load_json(vdir / "vector.json")
        if manifest["kind"] != "EXECUTION" or (args.only and not manifest["id"].startswith(args.only)):
            continue
        try:
            out = files(run(args.engine, vdir))
        except Exception as exc:  # noqa: BLE001 - report and continue with the next vector
            print(f"FAIL {manifest['id']}: {exc}")
            failures += 1
            continue
        exp = vdir / "expected"
        if args.write:
            exp.mkdir(exist_ok=True)
            for name, text in out.items():
                (exp / name).write_text(text)
            print(f"wrote {manifest['id']} ({vdir.name})")
            continue
        bad = [name for name, text in out.items() if not (exp / name).exists() or (exp / name).read_text() != text]
        print(("ok   " if not bad else "FAIL ") + f"{manifest['id']} ({manifest['status']})" + (f": {', '.join(bad)} differ" if bad else ""))
        failures += bool(bad)
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
