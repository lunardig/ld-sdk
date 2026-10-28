import type { JsonHandler } from "./transport.js";

/** The parts of the wasm-bindgen `--target web` glue (`ld_mock_wasm.js`) the SDK uses. */
export interface MockWasmBindings {
  default(input?: { module_or_path: unknown }): Promise<unknown>;
  initSync(input: { module: unknown }): unknown;
  MockHost: new () => JsonHandler & { free(): void };
  engine_version(): string;
}

/** Turns a JSON request into the ENGINE_FAULT response for a trapped engine. */
export function faultResponse(request: string, err: unknown): string {
  let id = "0";
  try {
    const parsed = JSON.parse(request) as { id?: unknown };
    if (typeof parsed.id === "string") id = parsed.id;
  } catch {
    /* keep id 0 */
  }
  const message = `engine trapped: ${err instanceof Error ? err.message : String(err)}`;
  return JSON.stringify({ id, method: "error", error: { code: "ENGINE_FAULT", message } });
}
