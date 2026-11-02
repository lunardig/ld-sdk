import { test } from "node:test";
import assert from "node:assert/strict";
import { LdcpClient, type LdcpPort } from "../src/index.js";

/** A scripted host on the other end of a port: answers each request with `reply`. */
function fakeHost(reply: (m: Record<string, any>, send: (x: object) => void) => void) {
  const listeners = new Set<(m: Record<string, any>) => void>();
  const send = (x: object) => queueMicrotask(() => listeners.forEach((f) => f({ session: "s1", ...x })));
  const port: LdcpPort = {
    post: (m: Record<string, any>) => reply(m, send),
    listen: (f) => (listeners.add(f), () => listeners.delete(f)),
  };
  return { port, send };
}

test("connects, observes, subscribes and follows a command to its outcome", async () => {
  const seen: string[] = [];
  const { port, send } = fakeHost((m, send) => {
    if (m.ldcp === "hello") {
      assert.deepEqual(m.scopes, ["read", "operate"]);
      send({ ldcp: "welcome", claim: "c", scopes: m.scopes, clock: { time_us: "0", us_per_s: "1000000", running: false }, cursor: "4" });
    } else if (m.method === "observe") send({ ldcp: "response", id: m.id, result: { view: { time_us: "0" }, cursor: "4" } });
    else if (m.method === "subscribe") send({ ldcp: "response", id: m.id, result: { cursor: "4", gap: false } });
    else if (m.method === "submit") {
      send({ ldcp: "outcome", id: m.id, command_id: "s1-1", outcome: "received" });
      send({ ldcp: "outcome", id: m.id, command_id: "s1-1", outcome: "applied", seq: "9", time_us: "0" });
    }
  });
  const c = await LdcpClient.connect({ app: { name: "t" }, scopes: ["read", "operate"], port });
  assert.equal(c.cursor, "4");
  assert.equal((await c.observe()).cursor, "4");
  await c.subscribe({ kinds: ["COMMAND"] }, (events, cursor) => seen.push(`${events.length}@${cursor}`));
  let received = "";
  const out = await c.submit("PAUSE", { job_id: "j" }, { onReceived: (id) => (received = id) });
  assert.equal(received, "s1-1");
  assert.deepEqual(out, { command_id: "s1-1", outcome: "applied", seq: "9", time_us: "0" });
  send({ ldcp: "events", events: [{ seq: "4", time_us: "0", kind: "COMMAND" }], cursor: "5", time_us: "0" });
  send({ ldcp: "clock", clock: { time_us: "0", us_per_s: "60000000", running: true } });
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(seen, ["1@5"]);
  assert.equal(c.cursor, "5");
  assert.equal(c.clock.running, true);
  // The session ends: pending and later calls fail with ENDED.
  const pending = c.history();
  send({ ldcp: "ended", reason: "revoked" });
  await assert.rejects(pending, /revoked/);
  await assert.rejects(c.observe(), /revoked/);
  assert.equal(c.ended, "revoked");
});

test("a refused connection rejects; messages for other sessions are ignored", async () => {
  const { port } = fakeHost((m, send) => m.ldcp === "hello" && send({ ldcp: "denied", reason: "the player declined" }));
  await assert.rejects(LdcpClient.connect({ app: { name: "t" }, port }), /declined/);
  const other = fakeHost((m, send) => {
    if (m.ldcp === "hello") send({ ldcp: "welcome", claim: "c", scopes: ["read"], clock: {}, cursor: "0" });
    else send({ ldcp: "response", id: m.id, session: "s2", result: "wrong" });
  });
  const c = await LdcpClient.connect({ app: { name: "t" }, port: other.port });
  const r = await Promise.race([c.catalog(), new Promise((res) => setTimeout(() => res("no answer"), 20))]);
  assert.equal(r, "no answer");
});
