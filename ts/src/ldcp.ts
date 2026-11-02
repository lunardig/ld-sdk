/**
 * A controller's side of ldcp-0, the claim host protocol (LD-PEC-009 §4–§8).
 *
 * A controller is a page opened by the Lunar Dig host's launcher. It finds the host as
 * `window.opener`, says hello, and once the player allows it, observes and commands the
 * claim the host is running:
 *
 *     const host = await LdcpClient.connect({ app: { name: "My Sheet" }, scopes: ["read", "operate"] });
 *     const { view } = await host.observe();
 *     host.subscribe({ kinds: ["JOB_STATUS"] }, (events) => render(events));
 *     const outcome = await host.submit("PAUSE", { job_id: "job-1" });
 *
 * Commands go through the host, which attributes them to this page's origin in the claim's
 * history. The host owns the clock; a controller can read it but not change it.
 */
import type { CatalogBundle, ClaimView, Command, Event } from "@lunardig/ld-interfaces";

export const LDCP_PROTOCOL = "ldcp-0";
export const LUNARDIG_ORIGIN = "https://lunardig.com";

export type LdcpScope = "read" | "operate" | "build";

export interface LdcpClock {
  time_us: string;
  /** Claim microseconds per real second while running. */
  us_per_s: string;
  running: boolean;
}

export interface LdcpOutcome {
  command_id: string;
  outcome: "applied" | "rejected";
  /** For `applied`: the command's place in the claim's order, and when it took effect. */
  seq?: string;
  time_us?: string;
  /** For `rejected`: `host` (scope, malformed, duplicate) or `engine` (admission). */
  stage?: "host" | "engine";
  code?: string;
  message?: string;
}

export interface LdcpFilter {
  kinds?: Event["kind"][];
  /** Ids an event is about: a job, connection, lot, buffer or machine, a command. */
  subjects?: string[];
  /** Resume from here (the `cursor` of the last batch handled). Default: now. */
  cursor?: string;
}

export interface LdcpHistoryRecord {
  command: Command;
  status: string;
}

/** What the host announces besides answers. */
export interface LdcpHandlers {
  /** A batch of subscribed events. `cursor` is where to resume; `time_us` the claim time. */
  events?: (events: Event[], cursor: string, timeUs: string) => void;
  /** The host no longer holds the events asked for: observe afresh. */
  gap?: (cursor: string) => void;
  /** The host started a new event stream (a claim loaded or restored): observe afresh. */
  reset?: (claim: string, cursor: string) => void;
  clock?: (clock: LdcpClock) => void;
  scopes?: (scopes: LdcpScope[]) => void;
  ended?: (reason: string) => void;
}

/** How the client reaches the host. In a browser, `windowPort()`; in tests, anything. */
export interface LdcpPort {
  post(msg: object): void;
  listen(fn: (msg: Record<string, any>) => void): () => void;
}

/** The opener window, with every message checked to come from it and from `hostOrigin`. */
export function windowPort(hostOrigin = LUNARDIG_ORIGIN, opener: Window | null = typeof window === "undefined" ? null : window.opener): LdcpPort {
  if (!opener) throw new LdcpError("NO_HOST", "this page was not opened by a Lunar Dig host: open it from the host's Controllers dialog");
  return {
    post: (msg) => opener.postMessage(msg, hostOrigin),
    listen: (fn) => {
      const h = (e: MessageEvent) => {
        if (e.source === opener && e.origin === hostOrigin && e.data && typeof e.data.ldcp === "string") fn(e.data);
      };
      window.addEventListener("message", h);
      return () => window.removeEventListener("message", h);
    },
  };
}

export class LdcpError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "LdcpError";
  }
}

export interface ConnectOptions {
  app: { name: string; version?: string };
  scopes?: LdcpScope[];
  /** The host's origin. Default https://lunardig.com. */
  hostOrigin?: string;
  port?: LdcpPort;
  handlers?: LdcpHandlers;
  /** How long to wait for the player to answer. Default: no limit. */
  timeoutMs?: number;
}

export class LdcpClient {
  readonly session: string;
  readonly claim: string;
  scopes: LdcpScope[];
  clock: LdcpClock;
  /** The cursor of the latest view or batch seen. */
  cursor: string;
  handlers: LdcpHandlers;

  #port: LdcpPort;
  #unlisten: () => void;
  #nextId = 1;
  #calls = new Map<number, { resolve: (v: any) => void; reject: (e: Error) => void }>();
  #submits = new Map<number, { resolve: (o: LdcpOutcome) => void; received?: (commandId: string) => void }>();
  #ended: string | null = null;

  private constructor(port: LdcpPort, welcome: Record<string, any>, handlers: LdcpHandlers) {
    this.#port = port;
    this.session = welcome.session;
    this.claim = welcome.claim;
    this.scopes = welcome.scopes;
    this.clock = welcome.clock;
    this.cursor = welcome.cursor;
    this.handlers = handlers;
    this.#unlisten = port.listen((m) => this.#onMessage(m));
  }

  /** Say hello to the host and wait for the player to allow this application. */
  static connect(opts: ConnectOptions): Promise<LdcpClient> {
    const port = opts.port ?? windowPort(opts.hostOrigin);
    return new Promise((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const stop = port.listen((m) => {
        if (m.ldcp === "welcome") {
          stop();
          if (timer) clearTimeout(timer);
          resolve(new LdcpClient(port, m, opts.handlers ?? {}));
        } else if (m.ldcp === "denied") {
          stop();
          if (timer) clearTimeout(timer);
          reject(new LdcpError("DENIED", m.reason ?? "the host refused the connection"));
        }
      });
      if (opts.timeoutMs) {
        timer = setTimeout(() => {
          stop();
          reject(new LdcpError("TIMEOUT", "the host did not answer"));
        }, opts.timeoutMs);
      }
      port.post({ ldcp: "hello", protocol: LDCP_PROTOCOL, app: opts.app, scopes: opts.scopes ?? ["read"] });
    });
  }

  get ended() {
    return this.#ended;
  }

  /** The claim view now, and the cursor of the events it reflects. */
  async observe(): Promise<{ view: ClaimView; cursor: string }> {
    const r = await this.#call("observe");
    this.cursor = r.cursor;
    return r;
  }

  catalog(): Promise<CatalogBundle> {
    return this.#call("catalog");
  }

  async readClock(): Promise<LdcpClock> {
    return (this.clock = await this.#call("clock"));
  }

  history(from = 0, limit = 200): Promise<{ records: LdcpHistoryRecord[]; next: number; total: number }> {
    return this.#call("history", { from, limit });
  }

  /** Receive events from `filter.cursor` (default now) through `handlers.events`. If the host
   *  no longer holds them, the result has `gap: true`: observe afresh. */
  async subscribe(filter: LdcpFilter = {}, onEvents?: LdcpHandlers["events"]): Promise<{ cursor: string; gap: boolean }> {
    if (onEvents) this.handlers.events = onEvents;
    return this.#call("subscribe", filter);
  }

  unsubscribe(): Promise<void> {
    return this.#call("unsubscribe");
  }

  /**
   * Submit a command; resolves with its final outcome, applied or rejected (a rejection
   * resolves, it does not throw). `onReceived` is called once the host has queued it.
   * Give `command_id` to retry safely: the host refuses a repeated id as a duplicate.
   */
  submit<O extends Command["op"]>(
    op: O,
    args: Extract<Command, { op: O }>["args"],
    opts: { command_id?: string; onReceived?: (commandId: string) => void } = {},
  ): Promise<LdcpOutcome> {
    if (this.#ended) return Promise.reject(new LdcpError("ENDED", this.#ended));
    const id = this.#nextId++;
    return new Promise((resolve) => {
      this.#submits.set(id, { resolve, received: opts.onReceived });
      this.#port.post({ ldcp: "request", session: this.session, id, method: "submit", params: { op, args, command_id: opts.command_id } });
    });
  }

  /** Leave: the host ends this session. */
  close() {
    if (!this.#ended) this.#port.post({ ldcp: "bye", session: this.session });
    this.#finish("closed by controller");
  }

  #call(method: string, params: object = {}): Promise<any> {
    if (this.#ended) return Promise.reject(new LdcpError("ENDED", this.#ended));
    const id = this.#nextId++;
    return new Promise((resolve, reject) => {
      this.#calls.set(id, { resolve, reject });
      this.#port.post({ ldcp: "request", session: this.session, id, method, params });
    });
  }

  #onMessage(m: Record<string, any>) {
    if (m.session !== this.session) return;
    const h = this.handlers;
    switch (m.ldcp) {
      case "response":
      case "error": {
        const c = this.#calls.get(m.id);
        if (c) {
          this.#calls.delete(m.id);
          if (m.ldcp === "response") c.resolve(m.result);
          else c.reject(new LdcpError(m.code, m.message));
          return;
        }
        // A submit refused before it reached an outcome (an unknown op, say).
        const s = this.#submits.get(m.id);
        if (s && m.ldcp === "error") {
          this.#submits.delete(m.id);
          s.resolve({ command_id: "", outcome: "rejected", stage: "host", code: m.code, message: m.message });
        }
        return;
      }
      case "outcome": {
        const s = this.#submits.get(m.id);
        if (!s) return;
        if (m.outcome === "received") return s.received?.(m.command_id);
        this.#submits.delete(m.id);
        const { ldcp: _l, session: _s, id: _i, ...outcome } = m;
        s.resolve(outcome as LdcpOutcome);
        return;
      }
      case "events":
        this.cursor = m.cursor;
        this.clock = { ...this.clock, time_us: m.time_us };
        h.events?.(m.events, m.cursor, m.time_us);
        return;
      case "gap":
        this.cursor = m.cursor;
        h.gap?.(m.cursor);
        return;
      case "reset":
        this.cursor = m.cursor;
        h.reset?.(m.claim, m.cursor);
        return;
      case "clock":
        this.clock = m.clock;
        h.clock?.(m.clock);
        return;
      case "scopes":
        this.scopes = m.scopes;
        h.scopes?.(m.scopes);
        return;
      case "ended":
        this.#finish(m.reason);
        h.ended?.(m.reason);
        return;
    }
  }

  #finish(reason: string) {
    if (this.#ended) return;
    this.#ended = reason;
    this.#unlisten();
    for (const c of this.#calls.values()) c.reject(new LdcpError("ENDED", reason));
    for (const s of this.#submits.values()) s.resolve({ command_id: "", outcome: "rejected", stage: "host", code: "ENDED", message: reason });
    this.#calls.clear();
    this.#submits.clear();
  }
}
