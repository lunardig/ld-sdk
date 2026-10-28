#!/usr/bin/env bash
# Build the mock engine for the browser: ts/wasm/ld_mock_wasm.js + ld_mock_wasm_bg.wasm
# (wasm-bindgen --target web; loadable in a module Worker and, via initSync, in Node).
# Needs: rustup target add wasm32-unknown-unknown; cargo install wasm-bindgen-cli --version 0.2.100
set -euo pipefail
cd "$(dirname "$0")/.."
cargo build --release --target wasm32-unknown-unknown -p ld-mock-wasm
rm -rf ts/wasm
wasm-bindgen --target web --out-dir ts/wasm target/wasm32-unknown-unknown/release/ld_mock_wasm.wasm
ls -l ts/wasm
