import type {
  Admission,
  AdvanceReport,
  CatalogBundle,
  ClaimView,
  Command,
  EngineError,
  EventPage,
  Genesis,
  Request,
  Response,
  Snapshot,
} from "@lunardig/ld-interfaces";
import type { Transport } from "./transport.js";

/** An engine-reported failure (`"method": "error"`), carrying the I-1 error object. */
export class LdEngineError extends Error {
  readonly code: EngineError["code"];
  readonly detail: EngineError["detail"];

  constructor(error: EngineError) {
    super(`${error.code}: ${error.message}`);
    this.name = "LdEngineError";
    this.code = error.code;
    this.detail = error.detail;
  }
}

/**
 * The engine as the UI sees it (LD-PEC-002 §5.2). Every method is one I-1 round trip.
 * Quantities and times are decimal strings: convert to numbers for display only.
 */
export interface LdEngine {
  submit(cmd: Command): Promise<Admission>;
  advanceTo(tUs: string): Promise<AdvanceReport>;
  nextEventTime(): Promise<string | null>;
  observe(): Promise<ClaimView>;
  eventsSince(cursor: number | string, limit: number | string): Promise<EventPage>;
  snapshot(): Promise<Snapshot>;
  stateHash(): Promise<string>;
  historyHead(): Promise<string>;
  /** Release the transport (terminates a worker or child process). */
  close(): void;
}

type Method = Request["method"];
type ParamsOf<M extends Method> = Extract<Request, { method: M }>["params"];
type ResultOf<M extends Method> = Extract<Response, { method: M }> extends { result: infer R } ? R : never;

const DECIMAL = /^(0|[1-9][0-9]*)$/;

function decimal(v: number | string, what: string): string {
  const s = typeof v === "number" ? (Number.isSafeInteger(v) && v >= 0 ? String(v) : "") : typeof v === "string" ? v : "";
  if (!DECIMAL.test(s)) throw new TypeError(`${what} must be a non-negative integer, got ${JSON.stringify(v)}`);
  return s;
}

class Client implements LdEngine {
  #transport: Transport;
  #nextId = 1n;

  constructor(transport: Transport) {
    this.#transport = transport;
  }

  async call<M extends Method>(method: M, params: ParamsOf<M>): Promise<ResultOf<M>> {
    const id = String(this.#nextId++);
    const raw = await this.#transport.send(JSON.stringify({ id, method, params }));
    let response: Response;
    try {
      response = JSON.parse(raw) as Response;
    } catch {
      throw new LdEngineError({ code: "ENGINE_FAULT", message: `unparseable response: ${raw.slice(0, 200)}` });
    }
    if (response.method === "error") throw new LdEngineError(response.error);
    if (response.id !== id || response.method !== method) {
      throw new LdEngineError({
        code: "ENGINE_FAULT",
        message: `response ${response.method}#${response.id} does not answer ${method}#${id}`,
      });
    }
    return (response as unknown as { result: ResultOf<M> }).result;
  }

  submit(command: Command) {
    return this.call("submit", { command });
  }
  async advanceTo(tUs: string) {
    if (typeof tUs !== "string" || !/^-?(0|[1-9][0-9]*)$/.test(tUs)) throw new TypeError(`tUs must be a decimal integer string, got ${JSON.stringify(tUs)}`);
    return this.call("advance_to", { t_us: tUs });
  }
  async nextEventTime() {
    return (await this.call("next_event_time", {})).t_us ?? null;
  }
  observe() {
    return this.call("observe", {});
  }
  async eventsSince(cursor: number | string, limit: number | string) {
    return this.call("events_since", { cursor: decimal(cursor, "cursor"), limit: decimal(limit, "limit") });
  }
  snapshot() {
    return this.call("snapshot", {});
  }
  async stateHash() {
    return (await this.call("state_hash", {})).hash;
  }
  async historyHead() {
    return (await this.call("history_head", {})).hash;
  }
  close() {
    this.#transport.close();
  }
}

/** Start a claim at genesis. The transport is owned by the returned engine. */
export async function createEngine(genesis: Genesis, catalog: CatalogBundle, transport: Transport): Promise<LdEngine> {
  const client = new Client(transport);
  await client.call("create", { genesis, catalog });
  return client;
}

/** Resume a claim from a snapshot. The engine verifies the snapshot's hashes. */
export async function restoreEngine(snapshot: Snapshot, catalog: CatalogBundle, transport: Transport): Promise<LdEngine> {
  const client = new Client(transport);
  await client.call("restore", { snapshot, catalog });
  return client;
}

/**
 * Read every event from `cursor` onward, a page at a time.
 * Returns the events and the cursor to resume from.
 */
export async function drainEvents(engine: LdEngine, cursor = "0", pageSize = 256) {
  const events: EventPage["events"] = [];
  for (;;) {
    const page = await engine.eventsSince(cursor, pageSize);
    events.push(...page.events);
    if (page.next_cursor === cursor || page.events.length === 0) return { events, cursor: page.next_cursor };
    cursor = page.next_cursor;
  }
}
