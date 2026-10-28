import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { createEngine, HttpTransport } from "../src/index.js";
import { createEngineBridge, ProcessTransport } from "../src/node.js";
import { CLI, fixtures, haveCli } from "./helpers.js";

test("HttpTransport through the engine bridge matches a direct process", { skip: !haveCli && "no ld-mock-cli" }, async () => {
  const bridge = createEngineBridge({ binary: CLI, maxSessions: 2 });
  const server = createServer((req, res) => bridge(req, res));
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/__ld-engine`;
  try {
    const f = fixtures().find((x) => x.name === "running-loop")!;
    const create = f.requests[0].params;
    const run = async (t: import("../src/index.js").Transport) => {
      const e = await createEngine(create.genesis, create.catalog, t);
      for (const r of f.requests.slice(1)) {
        if (r.method === "submit") await e.submit(r.params.command);
        if (r.method === "advance_to") await e.advanceTo(r.params.t_us);
      }
      const view = await e.observe();
      const hash = await e.stateHash();
      e.close();
      return { hash, view };
    };
    const [viaHttp, direct] = await Promise.all([run(new HttpTransport(base)), run(new ProcessTransport(CLI))]);
    assert.equal(viaHttp.hash, direct.hash);
    assert.ok(viaHttp.view.lots.some((l) => l.location === "inventory"), "inventory lots are observable");

    const missing = await fetch(`${base}/session/nope`, { method: "POST", body: "{}" });
    assert.equal(missing.status, 404);
  } finally {
    bridge.shutdown();
    server.close();
  }
});
