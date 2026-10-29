import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
export const CLI = process.env.LD_MOCK_CLI ?? join(ROOT, "target/release/ld-mock-cli");
export const WASM_DIR = process.env.LD_WASM_DIR ?? join(ROOT, "ts/wasm");
export const haveCli = existsSync(CLI);
export const WASM_NAME = process.env.LD_WASM_NAME ?? "ld_mock_wasm";
export const haveWasm = existsSync(join(WASM_DIR, `${WASM_NAME}_bg.wasm`));

export interface Fixture {
  name: string;
  final_time_us: string;
  expect: {
    jobs?: Record<string, [string, string | null]>;
    machines?: Record<string, string>;
    admissions?: Record<string, string>;
    events?: string[];
  };
  requests: { id: string; method: string; params: any }[];
}

export function fixtures(): Fixture[] {
  const dir = join(ROOT, "fixtures");
  return readdirSync(dir)
    .filter((n) => existsSync(join(dir, n, "requests.jsonl")))
    .sort()
    .map((n) => ({
      ...JSON.parse(readFileSync(join(dir, n, "fixture.json"), "utf8")),
      name: n,
      requests: readFileSync(join(dir, n, "requests.jsonl"), "utf8")
        .split("\n")
        .filter((l) => l.trim())
        .map((l) => JSON.parse(l)),
    }));
}
