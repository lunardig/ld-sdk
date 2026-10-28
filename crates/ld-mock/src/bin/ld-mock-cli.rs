//! The mock engine over stdin/stdout: one I-1 request per line in, one response per line
//! out (LD-PEC-002 §5.0). Lets any language drive the mock without WebAssembly.
//!
//!     ld-mock-cli < fixtures/running-loop/requests.jsonl

use std::io::{self, BufRead, Write};
use std::panic::{catch_unwind, AssertUnwindSafe};

use ld_mock::MockEngine;
use ld_sdk::types::{EngineErrorCode, Response};
use ld_sdk::{engine_error, Host};

fn main() {
    std::panic::set_hook(Box::new(|_| {})); // report panics as ENGINE_FAULT responses, not stderr noise
    let mut host: Host<MockEngine> = Host::new();
    let stdin = io::stdin();
    let mut out = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = line.expect("stdin is readable");
        if line.trim().is_empty() {
            continue;
        }
        let reply = match catch_unwind(AssertUnwindSafe(|| host.handle_json(&line))) {
            Ok(reply) => reply,
            Err(panic) => {
                let msg = panic
                    .downcast_ref::<String>()
                    .cloned()
                    .or_else(|| panic.downcast_ref::<&str>().map(|s| s.to_string()))
                    .unwrap_or_else(|| "engine panic".into());
                // The engine may be inconsistent after a fault; drop it.
                host = Host::new();
                let id = serde_json::from_str::<serde_json::Value>(&line)
                    .ok()
                    .and_then(|v| v.get("id").and_then(|i| i.as_str()).and_then(|s| s.parse().ok()))
                    .unwrap_or_else(|| ld_sdk::canonical::uint(0));
                let resp = Response::Error { id, error: engine_error(EngineErrorCode::EngineFault, msg) };
                serde_json::to_string(&resp).unwrap()
            }
        };
        writeln!(out, "{reply}").unwrap();
        out.flush().unwrap();
    }
}
