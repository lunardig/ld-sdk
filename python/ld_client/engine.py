"""An engine process speaking I-1: one JSON request per line in, one response per line out."""
from __future__ import annotations

import json
import shlex
import subprocess
from typing import Iterator

from .view import View


class EngineError(Exception):
    """The engine answered with an error (I-1 `EngineError`): bad time, bad schema, a fault."""

    def __init__(self, method: str, error: dict):
        self.method, self.code, self.detail = method, error.get("code"), error
        super().__init__(f"{method}: {error.get('code')}: {error.get('message', '')}")


class Rejected(Exception):
    """A command was not admitted. `admission` is the engine's I-1 Admission."""

    def __init__(self, op: str, args: dict, admission: dict):
        self.op, self.args, self.admission = op, args, admission
        d = admission.get("detail") or {}
        self.code = d.get("code")
        super().__init__(f"{op} {admission.get('status')}: {d.get('code')}: {d.get('message', '')}")


class Engine:
    """A running engine.

    `command` is a path to an engine binary or a command line, for example
    "ld-engine-cli" or "node engine/wasm-cli.mjs engine/wasm". Commands take effect at
    the claim's current time and are numbered for you; `submit` raises `Rejected` unless
    `check=False`, in which case it returns the admission.
    """

    def __init__(self, command: str):
        self._p = subprocess.Popen(shlex.split(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self._id = 0
        self._seq = 0
        self.now = 0
        self.cursor = "0"
        self.requests = 0
        self.bytes_in = 0

    # ---------------------------------------------------------------- transport
    def request(self, method: str, params: dict | None = None):
        """Send one I-1 request and return its result, or raise EngineError."""
        self._id += 1
        self.requests += 1
        self._p.stdin.write(json.dumps({"id": str(self._id), "method": method, "params": params or {}}) + "\n")
        self._p.stdin.flush()
        line = self._p.stdout.readline()
        if not line:
            raise EngineError(method, {"code": "ENGINE_EXITED", "message": "the engine process closed its output"})
        self.bytes_in += len(line)
        r = json.loads(line)
        if "error" in r:
            raise EngineError(method, r["error"])
        return r["result"]

    def close(self) -> None:
        self._p.stdin.close()
        self._p.wait(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- lifecycle
    def create(self, genesis: dict, catalog: dict) -> None:
        """Start a claim from a genesis and a catalog bundle (see `alpha`)."""
        self.request("create", {"genesis": genesis, "catalog": catalog})
        self.now = int(genesis.get("start_us", "0"))
        self._seq, self.cursor = 0, "0"

    def snapshot(self) -> dict:
        return self.request("snapshot")

    def restore(self, snapshot: dict, catalog: dict) -> None:
        """Replaces the claim with a snapshot's, in this same engine process. The engine's
        event log starts again from the restore, so `events()` starts again from its first
        event: nothing accepted after the restore is missed."""
        self.request("restore", {"snapshot": snapshot, "catalog": catalog})
        self.cursor = "0"
        self.now = int(snapshot["state"]["time_us"])
        self._seq = int(snapshot["state"].get("id_counters", {}).get("command-seq", "0"))

    # ---------------------------------------------------------------- commands
    def submit(self, op: str, check: bool = True, **args) -> dict:
        """Submit a command effective now. Integer arguments become decimal strings."""
        self._seq += 1
        cmd = {"command_id": f"cmd-{self._seq:05d}", "effective_us": str(self.now), "seq": str(self._seq), "op": op,
               "args": {k: str(v) if isinstance(v, int) and not isinstance(v, bool) else v for k, v in args.items()}}
        a = self.request("submit", {"command": cmd})
        if check and a.get("status") != "ACCEPTED":
            raise Rejected(op, args, a)
        return a

    # ---------------------------------------------------------------- time
    def advance_to(self, t_us: int) -> dict:
        """Advance claim time to `t_us`; returns the I-1 AdvanceReport."""
        r = self.request("advance_to", {"t_us": str(t_us)})
        self.now = int(r["time_us"])
        return r

    def advance_by(self, dt_us: int) -> dict:
        return self.advance_to(self.now + dt_us)

    def next_event_time(self) -> int | None:
        """When the engine next changes anything by itself, or None if nothing is scheduled."""
        r = self.request("next_event_time")
        t = r.get("t_us") if isinstance(r, dict) else r
        return None if t is None else int(t)

    # ---------------------------------------------------------------- reading
    def view(self) -> View:
        """The claim as the engine reports it now (I-1 ClaimView)."""
        return View(self.request("observe"))

    def events(self, limit: int = 1000) -> Iterator[dict]:
        """Every event since the last call, oldest first."""
        while True:
            r = self.request("events_since", {"cursor": self.cursor, "limit": str(limit)})
            self.cursor = r["next_cursor"]
            yield from r["events"]
            if len(r["events"]) < limit:
                return

    def state_hash(self) -> str:
        """SHA-256 of the physical state (LD-PEC-004 §8)."""
        return self.request("state_hash")["hash"]

    def history_head(self) -> str:
        """The head of the claim's history chain: what a player commits to the multiplayer game."""
        return self.request("history_head")["hash"]
