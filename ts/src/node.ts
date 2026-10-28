/**
 * Node-only transports. Kept out of the main entry so browser bundles never see
 * `node:child_process`.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
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

export interface EngineBridgeOptions {
  /** Engine binary speaking I-1 over stdin/stdout, e.g. target/release/ld-mock-cli. */
  binary: string;
  /** URL prefix the handler answers under. Default "/__ld-engine". */
  mount?: string;
  /** Concurrent sessions; the least recently used is closed beyond this. Default 8. */
  maxSessions?: number;
  /** Close sessions idle this long. Default 30 minutes. */
  idleMs?: number;
  /** Largest accepted request body. Default 32 MiB (catalogs and snapshots are big). */
  maxBodyBytes?: number;
}

/**
 * An HTTP front for engine binaries, one child process per session, for dev servers
 * and tests. Connect-style: `(req, res, next)`. Bind it to localhost only; it runs
 * whatever binary it was given for anyone who can reach it.
 *
 *     POST   {mount}/session        -> {"session": "<id>"}
 *     POST   {mount}/session/<id>   body: one I-1 request -> one I-1 response
 *     DELETE {mount}/session/<id>
 */
export function createEngineBridge(opts: EngineBridgeOptions) {
  const mount = (opts.mount ?? "/__ld-engine").replace(/\/$/, "");
  const max = opts.maxSessions ?? 8;
  const idleMs = opts.idleMs ?? 30 * 60_000;
  const maxBody = opts.maxBodyBytes ?? 32 * 1024 * 1024;
  const sessions = new Map<string, { t: ProcessTransport; used: number }>();

  const reap = () => {
    const now = Date.now();
    for (const [id, s] of sessions) if (now - s.used > idleMs) close(id);
    while (sessions.size > max) close([...sessions.entries()].sort((a, b) => a[1].used - b[1].used)[0][0]);
  };
  const close = (id: string) => {
    sessions.get(id)?.t.close();
    sessions.delete(id);
  };
  const timer = setInterval(reap, 60_000);
  timer.unref?.();

  const body = (req: IncomingMessage) =>
    new Promise<string>((resolve, reject) => {
      const chunks: Buffer[] = [];
      let size = 0;
      req.on("data", (c: Buffer) => {
        size += c.length;
        if (size > maxBody) {
          reject(new Error("request too large"));
          req.destroy();
        } else chunks.push(c);
      });
      req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
      req.on("error", reject);
    });

  const send = (res: ServerResponse, status: number, text: string, type = "text/plain") => {
    res.statusCode = status;
    res.setHeader("content-type", type);
    res.setHeader("cache-control", "no-store");
    res.end(text);
  };

  const handler = async (req: IncomingMessage, res: ServerResponse, next?: () => void) => {
    const url = (req.url ?? "").split("?")[0];
    if (!url.startsWith(mount + "/")) return next ? next() : send(res, 404, "not found");
    const rest = url.slice(mount.length + 1).split("/");
    try {
      if (rest[0] !== "session") return send(res, 404, "not found");
      if (rest.length === 1 && req.method === "POST") {
        const id = randomUUID();
        sessions.set(id, { t: new ProcessTransport(opts.binary), used: Date.now() });
        reap();
        return send(res, 200, JSON.stringify({ session: id }), "application/json");
      }
      const s = sessions.get(rest[1] ?? "");
      if (!s) return send(res, 404, "no such session");
      if (req.method === "DELETE") {
        close(rest[1]);
        return send(res, 204, "");
      }
      if (req.method !== "POST") return send(res, 405, "method not allowed");
      const line = (await body(req)).trim();
      if (line.includes("\n")) return send(res, 400, "one request per call");
      s.used = Date.now();
      return send(res, 200, await s.t.send(line), "application/json");
    } catch (err) {
      return send(res, 500, err instanceof Error ? err.message : String(err));
    }
  };
  return Object.assign(handler, {
    /** Close every session and stop the idle timer. */
    shutdown() {
      clearInterval(timer);
      for (const id of [...sessions.keys()]) close(id);
    },
  });
}
