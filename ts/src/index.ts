/**
 * @lunardig/ld-sdk — talk to a Lunar Dig engine over I-1 (LD-PEC-002 §5).
 *
 *     const transport = await WorkerTransport.start({ createWorker, bindingsUrl, wasmUrl });
 *     const engine = await createEngine(genesis, catalog, transport);
 *     await engine.advanceTo("60000000");
 *     const view = await engine.observe();
 *
 * Node transports live in `@lunardig/ld-sdk/node`; the worker entry in
 * `@lunardig/ld-sdk/worker`.
 */
export { createEngine, restoreEngine, drainEvents, LdEngineError, type LdEngine } from "./engine.js";
export { InProcessTransport, type Transport, type JsonHandler } from "./transport.js";
export { HttpTransport } from "./http-transport.js";
export { WorkerTransport, type WorkerTransportOptions } from "./worker-transport.js";
export type { MockWasmBindings } from "./wasm.js";
export type * from "@lunardig/ld-interfaces";
