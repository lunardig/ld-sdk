/**
 * Web Worker entry: hosts one engine instance and answers I-1 requests posted by
 * {@link WorkerTransport}. Load it as a module worker.
 *
 * The wasm glue is imported from a URL given at init, so this file has no build-time
 * dependency on the generated bindings and the app decides where they are served.
 */
import type { FromWorker, ToWorker } from "./protocol.js";
import { faultResponse, type MockWasmBindings } from "./wasm.js";

interface WorkerScope {
  onmessage: ((ev: MessageEvent<ToWorker>) => void) | null;
  postMessage(msg: FromWorker): void;
}
const scope = globalThis as unknown as WorkerScope;
let host: { handle(request: string): string } | null = null;

scope.onmessage = async (ev) => {
  const msg = ev.data;
  if (msg.type === "init") {
    try {
      const bindings = (await import(/* @vite-ignore */ msg.bindingsUrl)) as MockWasmBindings;
      await bindings.default({ module_or_path: msg.wasmUrl });
      host = new bindings.MockHost();
      scope.postMessage({ type: "ready", engineVersion: bindings.engine_version() });
    } catch (err) {
      scope.postMessage({ type: "init-failed", message: err instanceof Error ? err.message : String(err) });
    }
    return;
  }
  if (!host) {
    scope.postMessage({ type: "fault", seq: msg.seq, response: faultResponse(msg.request, "worker not initialized") });
    return;
  }
  try {
    scope.postMessage({ type: "response", seq: msg.seq, response: host.handle(msg.request) });
  } catch (err) {
    // A wasm trap: the instance's memory is no longer trustworthy.
    host = null;
    scope.postMessage({ type: "fault", seq: msg.seq, response: faultResponse(msg.request, err) });
  }
};
