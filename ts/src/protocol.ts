/** Messages between {@link WorkerTransport} and the worker entry (src/worker.ts). */
export type ToWorker =
  | { type: "init"; bindingsUrl: string; wasmUrl: string }
  | { type: "request"; seq: number; request: string };

export type FromWorker =
  | { type: "ready"; engineVersion: string }
  | { type: "response"; seq: number; response: string }
  /** The engine trapped. The worker is unusable; the transport replaces it. */
  | { type: "fault"; seq: number; response: string }
  | { type: "init-failed"; message: string };
