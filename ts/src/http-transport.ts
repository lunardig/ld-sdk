import type { Transport } from "./transport.js";

/**
 * Talks to an engine behind an HTTP bridge (see `createEngineBridge` in
 * `@lunardig/ld-sdk/node`). Each transport owns one engine session on the server.
 *
 * Meant for development and tests: it lets a page drive a native engine binary
 * (the mock CLI today, ld-engine later) without a WebAssembly build.
 */
export class HttpTransport implements Transport {
  #base: string;
  #session: Promise<string> | null = null;
  #closed = false;
  #chain: Promise<unknown> = Promise.resolve();

  /** `base` is the bridge's mount point, e.g. `"/__ld-engine"`. */
  constructor(base: string) {
    this.#base = base.replace(/\/$/, "");
  }

  #open(): Promise<string> {
    this.#session ??= fetch(`${this.#base}/session`, { method: "POST" }).then(async (r) => {
      if (!r.ok) throw new Error(`engine bridge refused a session: HTTP ${r.status} ${await r.text()}`);
      return ((await r.json()) as { session: string }).session;
    });
    return this.#session;
  }

  send(request: string): Promise<string> {
    if (this.#closed) return Promise.reject(new Error("transport is closed"));
    // Serialize: the engine answers in request order, and so must we.
    const next = this.#chain.then(async () => {
      const session = await this.#open();
      const r = await fetch(`${this.#base}/session/${session}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: request,
      });
      if (!r.ok) throw new Error(`engine bridge error: HTTP ${r.status} ${await r.text()}`);
      return r.text();
    });
    this.#chain = next.catch(() => {});
    return next;
  }

  close(): void {
    if (this.#closed) return;
    this.#closed = true;
    const s = this.#session;
    if (s) s.then((id) => fetch(`${this.#base}/session/${id}`, { method: "DELETE" })).catch(() => {});
  }
}
