// The browser build of ld-engine over stdin/stdout, like ld-engine-cli: one I-1 request
// per line in, one response per line out. Runs the same WebAssembly the game runs (on
// V8, under Node 18 or later), so clients and tools written for the native engine drive
// it unchanged:
//
//     node engine/wasm-cli.mjs [wasm-dir] < requests.jsonl
//     python examples/loop_controller.py --engine "node engine/wasm-cli.mjs engine/wasm"
//
// wasm-dir holds ld_engine_wasm.js and ld_engine_wasm_bg.wasm; it defaults to wasm/ next
// to this file.
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const dir = resolve(process.argv[2] ?? join(fileURLToPath(new URL(".", import.meta.url)), "wasm"));
const mod = await import(pathToFileURL(join(dir, "ld_engine_wasm.js")).href);
await mod.default({ module_or_path: readFileSync(join(dir, "ld_engine_wasm_bg.wasm")) });
const host = new mod.MockHost();
for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  if (line.trim()) process.stdout.write(host.handle(line) + "\n");
}
