# Engines

In a release, this folder holds ld-engine, the Lunar Dig alpha engine, as binaries:

| Path | What it is |
| --- | --- |
| `<platform>/ld-engine-cli` | The native engine: I-1 over stdin/stdout, one JSON request per line in, one response per line out. Platforms: `linux-x86_64`, `macos-arm64`, `windows-x86_64` (`ld-engine-cli.exe`) |
| `wasm/` | The same engine compiled to WebAssembly, exactly what lunardig.com runs |
| `wasm-cli.mjs` | Runs `wasm/` under Node with the same line protocol as the native engine |
| `ENGINE.json` | Which ld-engine commit and engine version these were built from, and the date this version was made available |
| `LICENSE.md` | The engine's license: the Functional Source License 1.1 with the Apache 2.0 future license (FSL-1.1-ALv2) |
| `NOTICE.md` | A plain summary, and ioMetics LC's Additional Permission: playing, player tools (free or sold), verification, private worlds and clients of official worlds are all allowed |
| `THIRD-PARTY-NOTICES.md` | Components by others compiled into the binaries, with their licenses |

The engine's source is not part of the SDK. You may run, modify and redistribute these
binaries for any purpose except a competing use, as `NOTICE.md` explains; each version
also becomes Apache-2.0 two years after it was made available. Every build of every platform must give
byte-identical results for identical inputs; the conformance vectors in
`vendor/ld-interfaces/vectors` check that (`tools/run_vectors.py`).

In a clone of the repository, this folder holds only `wasm-cli.mjs` and this file.
