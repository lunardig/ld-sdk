/**
 * Replays the scripted fixtures through the SDK's public API against the native
 * mock (ld-mock-cli) and, when built, the WebAssembly mock. Native and wasm must
 * agree byte for byte on every response.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createEngine, restoreEngine, drainEvents, type LdEngine, type Transport } from "../src/index.js";
import { ProcessTransport, loadWasmTransport } from "../src/node.js";
import { CLI, WASM_DIR, fixtures, haveCli, haveWasm, type Fixture } from "./helpers.js";

async function replay(f: Fixture, transport: Transport): Promise<LdEngine> {
  const [create, ...rest] = f.requests;
  assert.equal(create.method, "create");
  const engine = await createEngine(create.params.genesis, create.params.catalog, transport);
  for (const r of rest) {
    if (r.method === "submit") await engine.submit(r.params.command);
    else if (r.method === "advance_to") await engine.advanceTo(r.params.t_us);
  }
  return engine;
}

async function checkExpectations(f: Fixture, engine: LdEngine) {
  const view = await engine.observe();
  for (const [jobId, [status, code]] of Object.entries(f.expect.jobs ?? {})) {
    const job = view.jobs.find((j) => j.job_id === jobId);
    assert.equal(job?.status, status, `${f.name}: ${jobId} status`);
    assert.equal((job as any)?.reason?.code ?? null, code, `${f.name}: ${jobId} reason`);
  }
  for (const [m, mode] of Object.entries(f.expect.machines ?? {})) {
    assert.equal(view.machines.find((x) => x.machine_id === m)?.mode, mode, `${f.name}: ${m} mode`);
  }
  const { events } = await drainEvents(engine);
  for (const code of f.expect.events ?? []) {
    assert.ok(events.some((e: any) => e.reason?.code === code), `${f.name}: event ${code}`);
  }
}

test("fixtures through the native mock", { skip: !haveCli && `no ${CLI}; run cargo build --release` }, async (t) => {
  for (const f of fixtures()) {
    await t.test(f.name, async () => {
      const engine = await replay(f, new ProcessTransport(CLI));
      await checkExpectations(f, engine);
      const snap = await engine.snapshot();
      const later = String(BigInt(f.final_time_us) + 20_000_000n);
      await engine.advanceTo(later);
      const straight = await engine.stateHash();
      engine.close();

      const resumed = await restoreEngine(snap, f.requests[0].params.catalog, new ProcessTransport(CLI));
      await resumed.advanceTo(later);
      assert.equal(await resumed.stateHash(), straight, `${f.name}: restore diverged`);
      resumed.close();
    });
  }
});

test("wasm and native agree on every response", { skip: !(haveCli && haveWasm) && "needs ld-mock-cli and ts/wasm (CI builds both)" }, async (t) => {
  for (const f of fixtures()) {
    await t.test(f.name, async () => {
      const native = new ProcessTransport(CLI);
      const wasm = await loadWasmTransport(WASM_DIR);
      for (const r of f.requests) {
        const line = JSON.stringify(r);
        assert.equal(await wasm.send(line), await native.send(line), `${f.name}: response ${r.id} differs`);
      }
      const wasmEngine = await replay(f, await loadWasmTransport(WASM_DIR));
      await checkExpectations(f, wasmEngine);
      native.close();
      wasm.close();
    });
  }
});
