//! The mock engine behind a wasm-bindgen boundary.
//!
//! The boundary is one string in, one string out: an I-1 request as JSON, an I-1
//! response as JSON (LD-PEC-002 §5.0). Typed objects never cross it, so the TS side
//! depends only on the ld-interfaces schemas and the real engine can replace this
//! module without changing a line of the client.
//!
//! WebAssembly has no unwinding here: an engine panic traps and leaves the instance
//! unusable. The TS worker catches the trap, answers ENGINE_FAULT, and instantiates
//! a fresh module; the caller restores from its last snapshot.

use ld_mock::MockEngine;
use ld_sdk::Host;
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub struct MockHost {
    host: Host<MockEngine>,
}

#[wasm_bindgen]
impl MockHost {
    #[wasm_bindgen(constructor)]
    pub fn new() -> MockHost {
        MockHost { host: Host::new() }
    }

    /// Handle one I-1 request (JSON text) and return the response (JSON text).
    pub fn handle(&mut self, request: &str) -> String {
        self.host.handle_json(request)
    }
}

impl Default for MockHost {
    fn default() -> Self {
        Self::new()
    }
}

/// The engine version string reported in snapshots.
#[wasm_bindgen]
pub fn engine_version() -> String {
    ld_mock::ENGINE_VERSION.to_string()
}
