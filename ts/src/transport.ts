/**
 * A transport carries one I-1 request (JSON text) to an engine and brings back its
 * response (JSON text), in order (LD-PEC-002 §5.0). The client never sees anything
 * else, so the mock and the real engine are interchangeable behind any transport.
 */
export interface Transport {
  send(request: string): Promise<string>;
  close(): void;
}

/** Anything with the wasm-bindgen `MockHost.handle` shape: JSON in, JSON out. */
export interface JsonHandler {
  handle(request: string): string;
}

/**
 * Runs a handler on the calling thread. Right for Node, tests and tools; in a page
 * a long `advanceTo` would block rendering, so use {@link WorkerTransport} there.
 */
export class InProcessTransport implements Transport {
  #handler: JsonHandler | null;

  constructor(handler: JsonHandler) {
    this.#handler = handler;
  }

  async send(request: string): Promise<string> {
    if (!this.#handler) throw new Error("transport is closed");
    return this.#handler.handle(request);
  }

  close(): void {
    const h = this.#handler as (JsonHandler & { free?: () => void }) | null;
    h?.free?.();
    this.#handler = null;
  }
}
