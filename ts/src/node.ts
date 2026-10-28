/**
 * Node-only transports. Kept out of the main entry so browser bundles never see
 * `node:child_process`.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import { InProcessTransport, type Transport } from "./transport.js";
import { faultResponse, type MockWasmBindings } from "./wasm.js";

/**
 * Talks to a native engine binary over stdin/stdout, one JSON line each way
 * (`ld-mock-cli`, or the real engine's equivalent).
 */
export class ProcessTransport implements Transport {
  #child: ChildProcessWithoutNullStreams;
  #waiting: { resolve: (s: string) => void; reject: (e: Error) => void }[] = [];
  #exited: Error | null = null;

  constructor(binary: string, args: string[] = []) {
    this.#child = spawn(binary, args, { stdio: ["pipe", "pipe", "pipe"] });
    const lines = createInterface({ input: this.#child.stdout });
    lines.on("line", (line) => this.#waiting.shift()?.resolve(line));
    const fail = (err: Error) => {
      this.#exited = err;
      for (const w of this.#waiting.splice(0)) w.reject(err);
    };
    this.#child.on("error", fail);
    this.#child.on("exit", (code, signal) => fail(new Error(`engine process exited (${signal ?? code})`)));
  }

  send(request: string): Promise<string> {
    if (this.#exited) return Promise.reject(this.#exited);
    if (request.includes("\n")) return Promise.reject(new Error("request must be a single line"));
    return new Promise((resolve, reject) => {
      this.#waiting.push({ resolve, reject });
      this.#child.stdin.write(request + "\n");
    });
  }

  close(): void {
    this.#child.stdin.end();
    this.#child.kill();
  }
}

/**
 * Loads the `--target web` wasm build from a directory in Node and returns an
 * in-process transport over a fresh engine. A trap answers ENGINE_FAULT; the
 * instance is then discarded, so create a new transport to continue.
 */
export async function loadWasmTransport(wasmDir: string): Promise<Transport & { engineVersion: string }> {
  const bindings = (await import(pathToFileURL(`${wasmDir}/ld_mock_wasm.js`).href)) as MockWasmBindings;
  bindings.initSync({ module: readFileSync(`${wasmDir}/ld_mock_wasm_bg.wasm`) });
  let host: { handle(r: string): string; free(): void } | null = new bindings.MockHost();
  const inner = new InProcessTransport({
    handle(request) {
      if (!host) return faultResponse(request, "instance discarded after a trap");
      try {
        return host.handle(request);
      } catch (err) {
        host = null;
        return faultResponse(request, err);
      }
    },
  });
  return Object.assign(inner, { engineVersion: bindings.engine_version() });
}
