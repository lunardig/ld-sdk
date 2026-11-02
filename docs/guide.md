# Lunar Dig SDK: developer guide

For developers writing programs that play a Lunar Dig claim: controllers, bots,
analysis tools, alternative clients. You need this SDK and nothing else. If you find you
need something that is not here, that is a defect in the SDK; please report it.

## 1. What is in the SDK

| Path | What it is |
| --- | --- |
| `engine/` | The alpha engine as binaries: `<platform>/ld-engine-cli`, and `wasm/`, the same engine compiled to WebAssembly, with `wasm-cli.mjs` to run it under Node (`engine/README.md`) |
| `python/ld_client/` | A Python client (standard library only): start an engine, submit commands, advance time, read the claim, drain events |
| `ts/` | `@lunardig/ld-sdk`, the TypeScript client the web game uses: Web Worker, in-process WebAssembly and child-process transports |
| `fixtures/alpha/` | The alpha catalog and genesis, ready to pass to the engine (`create.json`); `docs/alpha-catalog.md` describes it |
| `vendor/ld-interfaces/` | The interface contract: JSON Schemas for the protocol (I-1), catalogs (I-2), state and history (I-3) and conformance vectors (I-4), generated Rust and TypeScript types, and the vectors themselves |
| `examples/` | `first_controller.py` (this guide's walkthrough), `loop_controller.py` (plays the final campaign challenge end to end), `bench_50.py` (the 50-machine benchmark), `make_scenario.py` (a challenge for the web game) |
| `tools/` | `run_vectors.py` and `check_fixtures.py`: check any engine against the vectors and fixtures |
| `crates/`, `fixtures/*` | A Rust engine trait and protocol host, and a mock engine that implements only the first milestone (M1). Use the real engine in `engine/` for everything in this guide |

**Licenses.** Everything here is Apache-2.0 except the engine binaries, which are under
the Functional Source License 1.1 (FSL-1.1-ALv2) with an Additional Permission: you may
play, build and sell player tools, verify claims, run private worlds and build clients for
official worlds. What is not allowed is a competing use, such as running a paid world
that substitutes for an official one. `engine/NOTICE.md` has the details, and each
engine version becomes Apache-2.0 two years after its release.

You need Python 3.10 or later. Node 18 or later is needed only for the WebAssembly engine
and the TypeScript client.

## 2. Getting started

**Check the download.** Each release publishes `SHA256SUMS` beside its archives. With both
files in one folder, `sha256sum -c SHA256SUMS --ignore-missing` (on macOS
`shasum -a 256 -c`) confirms you have the published file. `tools/verify_release.py` does the
same with `--sums SHA256SUMS` before its functional checks; without it, it checks only that
the release works, not that it is the published file, and says so.

Check that the engine runs. The engine speaks I-1 over standard input and output: one JSON
request per line in, one JSON response per line out.

```sh
engine/linux-x86_64/ld-engine-cli < fixtures/running-loop/requests.jsonl | head -3
```

On macOS use `engine/macos-arm64/`; on Windows, `engine\windows-x86_64\ld-engine-cli.exe`.
The WebAssembly build behaves identically:

```sh
node engine/wasm-cli.mjs < fixtures/running-loop/requests.jsonl | head -3
```

Then run the walkthrough and the full example:

```sh
python examples/first_controller.py --engine engine/linux-x86_64/ld-engine-cli
python examples/loop_controller.py  --engine engine/linux-x86_64/ld-engine-cli --night fuel-cell
```

**The loop scenario.** The alpha MVP's end-to-end test is a named start:
`alpha_loop()` gives create params with one of every kit the loop needs, 50 mol of water and
two reactor kits fuelled in their cores; submit `loop_setup()` at time 0, and the goal is
`LOOP_GOAL` (a hopper made from the claim's own iron, then a consumer running through the
night). The same is in `fixtures/alpha/loop.json` for other languages. `examples/loop_controller.py`
is one way to play it; you need not read it to start.

**Recipes.** `recipe(create, process, extent)` says what a run takes and makes, from the
catalog's operations in exact rationals: inputs and outputs by port, products, work and
energy. `docs/alpha-catalog.md` tabulates every process per unit and works through sizing a
reduction batch and a hopper. A controller can pull a batch once its buffers hold
`recipe(...).inputs`.

To use the client from your own code, install it (`pip install ./python`) or put `python/`
on `PYTHONPATH`:

```python
from ld_client import Engine, alpha, MIN

e = Engine("engine/linux-x86_64/ld-engine-cli")   # or "node engine/wasm-cli.mjs"
e.create(**alpha())
v = e.view()
print(v.time, [(lot["lot_id"], lot["products"]) for lot in v.lots])
```

## 3. The model in one page

**A claim** is one player's mining site: its equipment, materials, jobs and history. The
engine runs one claim. Everything is exact and deterministic: the same catalog, genesis
and commands give byte-identical states and hashes on every platform.

**Time** is an integer number of microseconds. Nothing happens until you advance it.
Commands take effect at the claim's current time. Between events every rate is constant,
so advancing one day in one call or in a thousand gives the same result.

**The catalog** defines the physics: substances and their formulas, processes, machine
blueprints, store templates, connection templates and products. The alpha catalog is in
`fixtures/alpha/` and described in `docs/alpha-catalog.md`. **The genesis** gives the
claim's starting time, its deposits (such as the `icy-crater` regolith deposit) and its
starting lots.

**Lots** hold material and products. Kits are products in a lot in `inventory` (the
cargo hold). `INSTALL` turns one kit into a **machine** or a **buffer** (a store: tank,
bin, hopper). A machine kit can carry extra state that travels with it: wear, a battery's
charge, a reactor's core.

**Ports and connections.** Machines have material ports (in or out) and may have an
electrical port. A FEED connection joins one machine material port to one buffer's `io`
port, or a deposit's `face` to a machine input. A CONVEYOR joins two buffers, with a
transit time. Electrical ports need no connection: every machine shares the claim's
power bus.

**Jobs** run processes on machines. `CREATE_JOB` names the process, the machine, a target
extent (how much to make, in the process's extent unit) and a priority; `START` makes it
eligible. A FLOW job runs continuously at a rate; a BATCH job captures its whole charge of
inputs when it starts, works, and releases its outputs at the end. A finished job
(COMPLETED, CANCELLED or FAILED) leaves the claim; its last JOB_STATUS event is its record.
A failed job still holding material stays until its machine is removed.

**Dispatch.** At every event the engine visits started jobs in priority order (highest
first) and either gives a job a constant rate or stops it with a **reason**:
`STARVED_INPUT` (an input buffer is empty), `BELOW_RESTART_THRESHOLD` (it stopped and is
waiting for its input to refill to a level), `BLOCKED_OUTPUT` (an output buffer is full),
`STARVED_POWER`, `STARVED_REQUIRE` (an environment condition, such as sunlight), `DWELLING`
(a timed stage) or `FAILED`. Stopped jobs restart by themselves when their reason clears.
A running job may also carry a reason: `FED` when it is held to the rate a buffer it alone
draws from is being filled, rather than stopping and restarting (down to its machine's
minimum stable rate), and `FEED_UNSETTLED` when that happens in a loop whose rates did
not settle, so it runs just under its feed (LD-PEC-004 Amendment 3).

**Part load.** Some machines draw more energy per unit of work at low load: a fixed share
of rated power while running, or, for the PEM electrolyzer, an efficiency curve that is
best near half load. `docs/alpha-catalog.md` lists each machine's minimum stable rate.

**Power.** Generators produce onto the bus; consumers draw from it in priority order. Some
generators follow the load (fuel cells, fission reactors) down to a minimum; surplus
becomes waste heat. Batteries charge from surplus and cover shortfalls.

**The lunar day.** Sunlight lasts about 14.8 days, then night for as long. Solar arrays
stop at dusk. `lunar_day(create)` in the client returns dusk and the period.

**Conservation.** Elements and energy are conserved exactly. Material can be stored,
moved, transformed, embodied in products and kits, sent to sinks (tailings, waste heat),
but never created or destroyed. An output that has nowhere to go stops its machine rather
than vanish.

**Evidence.** The claim's history is a hash chain over every command and every event.
`history_head()` is the commitment a player publishes; anyone with the catalog, genesis
and commands can re-execute the claim and check it.

## 4. The protocol (I-1)

Every request is `{"id": "<n>", "method": "<name>", "params": {...}}`; every response is
`{"id": ..., "method": ..., "result": ...}` or `{"id": ..., "method": ..., "error": {"code", "message"}}`.
Numbers are decimal strings; rationals are `{"n": "...", "d": "..."}`. The schemas in
`vendor/ld-interfaces/schemas/i1-protocol.schema.json` are normative.

| Method | Params | Result | Client |
| --- | --- | --- | --- |
| `create` | `genesis`, `catalog` | empty | `Engine.create(**create_params)` |
| `submit` | `command` | an Admission: `{command_id, status, detail?}` | `Engine.submit(op, **args)` |
| `advance_to` | `t_us` | `{time_us, events_appended, history_records}` | `advance_to(t)`, `advance_by(dt)` |
| `next_event_time` | — | `{t_us}`, or no `t_us` if nothing is scheduled | `next_event_time()` |
| `observe` | — | the ClaimView | `view()` |
| `events_since` | `cursor`, `limit` | `{events, next_cursor}` | `events()` drains from the last cursor |
| `snapshot` | — | the full state with its hashes | `snapshot()` |
| `restore` | `snapshot`, `catalog` | empty | `restore(snapshot, catalog)`: into a new engine or the same one. The event log restarts from the restore, and so does `events()` |
| `state_hash` | — | `{hash}` | `state_hash()` |
| `history_head` | — | `{hash}` | `history_head()` |

**Commands.** Every command carries `command_id`, `effective_us` (the current time; this
engine version accepts no other), `seq` (strictly increasing), `op` and `args`. The client
fills in all but `op` and `args`.

| Op | Args | Notes |
| --- | --- | --- |
| `INSTALL` | `lot_id`, `product`, `location`, `as_id` | One kit from a hold lot becomes machine or buffer `as_id`. A buffer installed under the name of a site with material waiting takes that material in |
| `REMOVE` | `target_id` | Back to a kit in the hold. Disconnect first; a machine may have no unfinished jobs; a buffer must be empty |
| `CONNECT` | `connection_id`, `template`, `from_port`, `to_port` | Ports are `<owner>.<port>`: `hopper-1.io`, `dig-1.out`, `icy-crater.face` |
| `DISCONNECT` | `connection_id` | Refused while the machine on a feed is running or holding a batch: pause it first |
| `CREATE_JOB` | `job_id`, `process`, `machine_id`, `target_extent`, `priority` | Queued until started. A job id may be reused once the job has finished |
| `START`, `PAUSE`, `RESUME`, `CANCEL` | `job_id` | |
| `SET_PRIORITY` | `job_id`, `priority` | Higher runs first when jobs compete for power or stock |
| `SET_OPERATING_POLICY` | `machine_id`, `rate_setpoint` | A rational ceiling on the machine's work rate, in work per µs |
| `TRANSFER_LOCAL` | `connection_id`, `species`, `amount` | Ship material along a conveyor |
| `SCHEDULE_MAINTENANCE`, `CHECKPOINT` | | Not available in alpha |

A command that cannot apply is **rejected** and changes nothing; the Admission says why
(`REJECTED_STATE`, `REJECTED_SCHEMA` or `REJECTED_TIME`, with a `detail.code` such as
`unknown-lot` or `core-port`). The client raises `Rejected` unless you pass `check=False`.
An **error** is different: the request itself failed (a malformed request, advancing
backwards); the client raises `EngineError`.

**The claim view** (`View.raw`): `time_us`, `state_hash`, `environment` (current values),
`machines` (mode, capacity factor, wear, rate, assigned jobs, `core` for machines with
one), `jobs` (status, `reason`, completed and required work, rate, priority), `buffers`
(contents, capacity, reserved), `connections`, `lots`, `deposits`, `sinks` and `energy`
(supply, demand and batteries). `View` adds `job()`, `machine()`, `buffer()`, `amount()`,
`kits()`, `kit_lot()`, `running()` and `power`.

**Events**, in order: `JOB_STATUS` (a job's status or reason changed), `STAGE_COMPLETED`,
`ARRIVAL` (a shipment reached its buffer), `THRESHOLD`, `ENVIRONMENT` (sunrise and
sunset) and `COMMAND` (every submitted command, with its admission status). Each has a
`seq` and `time_us`.

## 5. Walkthrough: your first controller

`examples/first_controller.py` builds a small plant and controls it. It is about sixty
lines; read it alongside this section.

**Create the claim.** `alpha(lots=...)` returns the alpha catalog and genesis with your own
starting lots: here one solar kit, one excavator kit and two hopper kits in the hold.

```python
kits = {"solar-kit": "1", "excavator-kit": "1", "hopper-kit": "2"}
e = Engine(engine_command)
e.create(**alpha(lots=[{"lot_id": "kits", "location": "inventory", "state": "AVAILABLE",
                        "composition": {}, "products": kits}]))
```

**Build.** Install each kit, connect the deposit's face to the excavator and the excavator
to the first hopper, then create and start a job for the solar array (priority 100, so it
always runs) and one for the excavator.

```python
e.submit("INSTALL", lot_id="kits", product="excavator-kit", location="pad-a", as_id="dig-1")
e.submit("CONNECT", connection_id="c-out", template="pipe", from_port="dig-1.out", to_port="hopper-1.io")
e.submit("CREATE_JOB", job_id="dig", process="excavate", machine_id="dig-1", target_extent=10**12, priority=10)
e.submit("START", job_id="dig")
```

**Control.** A controller is a loop: advance, read, act. Advancing to
`next_event_time()` means you look exactly when something changed by itself, such as a
job blocking or dusk. A cap (ten minutes here) also lets you watch levels that change
smoothly.

```python
nxt = e.next_event_time()
e.advance_to(min(e.now + 10 * MIN, nxt) if nxt and nxt > e.now else e.now + 10 * MIN)
for ev in e.events():
    ...                      # what happened since the last look
v = e.view()
if v.amount("hopper-1", "regolith") >= FULL:
    ...                      # act
```

**Act within the rules.** To send the excavator's output elsewhere, the feed must be cut,
and a feed cannot be cut while its machine runs. So: `PAUSE`, `DISCONNECT`, `CONNECT` to
the new hopper, `RESUME`. Had you tried `DISCONNECT` first, the engine would have
rejected it with `machine-busy` and nothing would have changed. When both hoppers are
full, the excavator stops by itself with `BLOCKED_OUTPUT`: the engine never creates or
destroys regolith to make room.

**Publish.** `history_head()` is the hash that commits to everything that happened.

From here, `examples/loop_controller.py` shows a complete strategy: the whole mining
chain, iron reduction in batches, fabricating a hopper from the claim's own iron, and
crossing the lunar night on a reactor or a fuel cell.

## 6. Checking an engine

The SDK can check that an engine conforms. Every build of the engine must pass both:

```sh
python tools/run_vectors.py  engine/linux-x86_64/ld-engine-cli vendor/ld-interfaces/vectors
python tools/check_fixtures.py engine/linux-x86_64/ld-engine-cli
```

Both need `pip install -r vendor/ld-interfaces/requirements.txt`. They also take the
browser build: `"node engine/wasm-cli.mjs"` works wherever an engine path does.

## 7. Scenarios for the web game

A claim you build with the engine can be loaded into the web game at lunardig.com:
`save_file(e, create, challenge=...)` writes the game's save format, optionally with a
challenge (a story, goals and a deadline) for its Goal tab, and players load it with
**Saved games**, then **Import a file**. `examples/make_scenario.py` builds a jammed mining
plant, writes it as a challenge, and with `--solve` proves it can be won.
[`save-format.md`](save-format.md) is the reference for both formats.

## 8. Alpha limits

- Commands apply at the current time only; there are no future-dated commands yet.
- There is one claim per engine and no multiplayer protocol yet. A player's evidence is
  their history head.
- `SCHEDULE_MAINTENANCE` and `CHECKPOINT` are refused, and repairing worn machines comes
  later.
- The catalog's numbers are a reviewed but provisional calibration; some are accelerated
  for play.
