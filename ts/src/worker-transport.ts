import type { FromWorker, ToWorker } from "./protocol.js";
import type { Transport } from "./transport.js";

export interface WorkerTransportOptions {
  /** Creates the worker running `@lunardig/ld-sdk/worker`, e.g.
   *  `() => new Worker(new URL("@lunardig/ld-sdk/worker", import.meta.url), { type: "module" })`. */
  createWorker: () => Worker;
  /** URL of the wasm-bindgen JS glue (`ld_mock_wasm.js`, built with `--target web`). */
  bindingsUrl: string;
  /** URL of the `.wasm` binary. */
  wasmUrl: string;
}

interface Pending {
  request: string;
  resolve: (response: string) => void;
  reject: (err: Error) => void;
}

/**
 * Runs the engine in a Web Worker so a long `advanceTo` never blocks the page.
 * One request is in flight at a time, which keeps responses in request order and
 * means a trap loses nothing queued behind it.
 *
 * If the engine traps, the worker is replaced with a fresh one and the failing
 * request gets an ENGINE_FAULT error. The fresh engine is uninitialized: restore
 * from your last snapshot.
 */
export class WorkerTransport implements Transport {
  #opts: WorkerTransportOptions;
  #worker: Worker | null = null;
  #ready: Promise<string> | null = null;
  #queue: Pending[] = [];
  #inFlight: (Pending & { seq: number }) | null = null;
  #seq = 0;
  #closed = false;
  /** Engine version reported by the most recent worker. */
  engineVersion = "";

  private constructor(opts: WorkerTransportOptions) {
    this.#opts = opts;
  }

  static async start(opts: WorkerTransportOptions): Promise<WorkerTransport> {
    const t = new WorkerTransport(opts);
    await t.#spawn();
    return t;
  }

  #spawn(): Promise<string> {
    const worker = this.#opts.createWorker();
    this.#worker = worker;
    this.#ready = new Promise<string>((resolve, reject) => {
      worker.onmessage = (ev: MessageEvent<FromWorker>) => {
        const msg = ev.data;
        switch (msg.type) {
          case "ready":
            this.engineVersion = msg.engineVersion;
            resolve(msg.engineVersion);
            this.#pump();
            break;
          case "init-failed":
            reject(new Error(`engine worker failed to start: ${msg.message}`));
            break;
          case "response":
            this.#settle(msg.seq, msg.response);
            break;
          case "fault":
            worker.terminate();
            this.#settle(msg.seq, msg.response, true);
            break;
        }
      };
      worker.onerror = (ev: ErrorEvent) => {
        const err = new Error(`engine worker error: ${ev.message}`);
        reject(err);
        const p = this.#inFlight;
        this.#inFlight = null;
        p?.reject(err);
      };
    });
    const init: ToWorker = { type: "init", bindingsUrl: this.#opts.bindingsUrl, wasmUrl: this.#opts.wasmUrl };
    worker.postMessage(init);
    return this.#ready;
  }

  #settle(seq: number, response: string, fault = false) {
    const p = this.#inFlight;
    if (!p || p.seq !== seq) return;
    this.#inFlight = null;
    p.resolve(response);
    if (fault && !this.#closed) {
      this.#spawn().catch((err) => this.#failAll(err));
    } else {
      this.#pump();
    }
  }

  #failAll(err: Error) {
    for (const p of this.#queue.splice(0)) p.reject(err);
  }

  #pump() {
    if (this.#inFlight || !this.#worker || this.#queue.length === 0) return;
    const next = this.#queue.shift()!;
    this.#inFlight = { ...next, seq: ++this.#seq };
    const msg: ToWorker = { type: "request", seq: this.#seq, request: next.request };
    this.#worker.postMessage(msg);
  }

  send(request: string): Promise<string> {
    if (this.#closed) return Promise.reject(new Error("transport is closed"));
    return new Promise<string>((resolve, reject) => {
      this.#queue.push({ request, resolve, reject });
      this.#ready?.then(() => this.#pump(), () => {});
    });
  }

  close(): void {
    this.#closed = true;
    this.#worker?.terminate();
    this.#worker = null;
    const err = new Error("transport is closed");
    this.#inFlight?.reject(err);
    this.#inFlight = null;
    this.#failAll(err);
  }
}
