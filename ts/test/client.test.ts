import { test } from "node:test";
import assert from "node:assert/strict";
import { createEngine, drainEvents, LdEngineError, WorkerTransport, type Transport } from "../src/index.js";

const genesis = {} as any;
const catalog = {} as any;

/** Answers each request with `reply(request)`. */
function fake(reply: (req: any) => any): Transport & { sent: any[] } {
  const sent: any[] = [];
  return {
    sent,
    async send(raw) {
      const req = JSON.parse(raw);
      sent.push(req);
      return JSON.stringify(reply(req));
    },
    close() {},
  };
}

const ok = (req: any, result: any = { hash: "00" }) => ({ id: req.id, method: req.method, result });

test("requests carry increasing decimal ids and responses must echo them", async () => {
  const t = fake((req) => ok(req));
  const e = await createEngine(genesis, catalog, t);
  await e.stateHash();
  assert.deepEqual(t.sent.map((r) => r.id), ["1", "2"]);
  assert.deepEqual(t.sent.map((r) => r.method), ["create", "state_hash"]);

  const liar = fake((req) => (req.method === "create" ? ok(req) : { ...ok(req), id: "999" }));
  const e2 = await createEngine(genesis, catalog, liar);
  await assert.rejects(e2.stateHash(), (err: any) => err instanceof LdEngineError && err.code === "ENGINE_FAULT");
});

test("error responses become LdEngineError with the engine's code", async () => {
  const t = fake((req) =>
    req.method === "create" ? ok(req) : { id: req.id, method: "error", error: { code: "REJECTED_TIME", message: "past" } },
  );
  const e = await createEngine(genesis, catalog, t);
  await assert.rejects(e.advanceTo("5"), (err: any) => err instanceof LdEngineError && err.code === "REJECTED_TIME");
});

test("arguments are validated before anything is sent", async () => {
  const t = fake((req) => ok(req));
  const e = await createEngine(genesis, catalog, t);
  await assert.rejects(e.advanceTo("1.5"), TypeError);
  await assert.rejects(e.advanceTo(1 as any), TypeError);
  await assert.rejects(e.eventsSince(-1, 10), TypeError);
  assert.equal(t.sent.length, 1);
});

test("nextEventTime maps an absent time to null", async () => {
  const t = fake((req) => (req.method === "next_event_time" ? ok(req, {}) : ok(req)));
  const e = await createEngine(genesis, catalog, t);
  assert.equal(await e.nextEventTime(), null);
});

test("drainEvents pages until the cursor stops moving", async () => {
  const all = Array.from({ length: 5 }, (_, i) => ({ seq: String(i), kind: "X" }));
  const t = fake((req) => {
    if (req.method !== "events_since") return ok(req);
    const from = Number(req.params.cursor);
    const events = all.slice(from, from + Number(req.params.limit));
    return ok(req, { events, next_cursor: String(from + events.length) });
  });
  const e = await createEngine(genesis, catalog, t);
  const { events, cursor } = await drainEvents(e, "0", 2);
  assert.equal(events.length, 5);
  assert.equal(cursor, "5");
});

/** A stand-in Worker that runs the worker protocol on the same thread. */
class FakeWorker {
  static made = 0;
  onmessage: ((ev: { data: any }) => void) | null = null;
  onerror: ((ev: any) => void) | null = null;
  terminated = false;
  initialized = false;
  constructor(private handle: (req: any) => any | "trap") {
    FakeWorker.made++;
  }
  postMessage(msg: any) {
    queueMicrotask(() => {
      if (this.terminated) return;
      if (msg.type === "init") {
        this.onmessage?.({ data: { type: "ready", engineVersion: "fake-1" } });
        return;
      }
      const req = JSON.parse(msg.request);
      const out = this.handle(req);
      if (out === "trap") {
        const response = JSON.stringify({ id: req.id, method: "error", error: { code: "ENGINE_FAULT", message: "trap" } });
        this.onmessage?.({ data: { type: "fault", seq: msg.seq, response } });
      } else {
        this.onmessage?.({ data: { type: "response", seq: msg.seq, response: JSON.stringify(out) } });
      }
    });
  }
  terminate() {
    this.terminated = true;
  }
}

test("WorkerTransport keeps order and replaces a trapped worker", async () => {
  FakeWorker.made = 0;
  let initialized = false;
  const transport = await WorkerTransport.start({
    createWorker: () =>
      new FakeWorker((req) => {
        if (req.method === "create") {
          initialized = true;
          return ok(req);
        }
        if (req.method === "advance_to") return "trap";
        return initialized ? ok(req) : { id: req.id, method: "error", error: { code: "NOT_INITIALIZED", message: "" } };
      }) as unknown as Worker,
    bindingsUrl: "x",
    wasmUrl: "y",
  });
  assert.equal(transport.engineVersion, "fake-1");
  const e = await createEngine(genesis, catalog, transport);
  const [a, b] = await Promise.all([e.stateHash(), e.historyHead()]);
  assert.equal(a, "00");
  assert.equal(b, "00");

  await assert.rejects(e.advanceTo("10"), (err: any) => err.code === "ENGINE_FAULT");
  initialized = false; // the replacement worker starts empty
  await assert.rejects(e.stateHash(), (err: any) => err.code === "NOT_INITIALIZED");
  assert.equal(FakeWorker.made, 2);
  e.close();
  await assert.rejects(e.stateHash(), /closed/);
});
