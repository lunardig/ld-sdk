# Engines

In a release, this folder holds ld-engine, the Lunar Dig alpha engine, as binaries:

| Path | What it is |
| --- | --- |
| `<platform>/ld-engine-cli` | The native engine: I-1 over stdin/stdout, one JSON request per line in, one response per line out. Platforms: `linux-x86_64`, `macos-arm64`, `windows-x86_64` (`ld-engine-cli.exe`) |
| `wasm/` | The same engine compiled to WebAssembly, exactly what lunardig.com runs |
| `wasm-cli.mjs` | Runs `wasm/` under Node with the same line protocol as the native engine |
| `ENGINE.json` | Which ld-engine commit and engine version these were built from |
| `LICENSE-ENGINE` | The terms under which the engine binaries are distributed |

The engine's source is not part of the SDK. Every build of every platform must give
byte-identical results for identical inputs; the conformance vectors in
`vendor/ld-interfaces/vectors` check that (`tools/run_vectors.py`).

In a clone of the repository, this folder holds only `wasm-cli.mjs` and this file.
