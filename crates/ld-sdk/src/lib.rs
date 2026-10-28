//! Lunar Dig SDK core.
//!
//! * [`Engine`]: the I-1 interface every engine implements (LD-PEC-002 §5.1). The mock
//!   engine in `ld-mock` and the real engine in `ld-engine` both implement it.
//! * [`Host`]: turns I-1 protocol messages (§5.0) into `Engine` calls. A Web Worker, a CLI
//!   or any other transport only has to move JSON strings in and out of a `Host`.
//! * [`canonical`] and [`rat`]: canonical JSON, domain-separated hashing, exact rationals.

pub mod canonical;
pub mod rat;

pub use ld_interfaces_types as types;

use serde_json::Value;
use types::*;

/// The engine interface (LD-PEC-002 §5.1). Implementations must be deterministic: no
/// clocks, no randomness, no host environment, no floating point, sorted iteration.
pub trait Engine: Sized {
    fn create(genesis: &Genesis, catalog: &CatalogBundle) -> Result<Self, EngineError>;
    fn restore(snapshot: &Snapshot, catalog: &CatalogBundle) -> Result<Self, EngineError>;

    /// Validates a command and queues or applies it. Never fails the call itself: a bad
    /// command yields a rejected [`Admission`] and changes no state.
    fn submit(&mut self, cmd: Command) -> Admission;

    /// Advances simulated time. Subdividing a span must give identical results (V-05).
    fn advance_to(&mut self, t_us: i64) -> Result<AdvanceReport, EngineError>;
    fn next_event_time(&self) -> Option<i64>;

    fn observe(&self) -> ClaimView;
    fn events_since(&self, cursor: u64, limit: u32) -> EventPage;

    fn snapshot(&self) -> Snapshot;
    fn state_hash(&self) -> Hash;
    fn history_head(&self) -> Hash;
}

/// Builds an [`EngineError`].
pub fn engine_error(code: EngineErrorCode, message: impl Into<String>) -> EngineError {
    EngineError { code, message: message.into(), detail: None }
}

/// Holds at most one engine and answers I-1 requests for it.
pub struct Host<E: Engine> {
    engine: Option<E>,
}

impl<E: Engine> Default for Host<E> {
    fn default() -> Self {
        Self { engine: None }
    }
}

impl<E: Engine> Host<E> {
    pub fn new() -> Self {
        Self::default()
    }

    /// Answers one request. `create` and `restore` replace any existing engine.
    pub fn handle(&mut self, request: Request) -> Response {
        match request {
            Request::Create { id, params } => match E::create(&params.genesis, &params.catalog) {
                Ok(engine) => {
                    let hash = engine.state_hash();
                    self.engine = Some(engine);
                    Response::Create { id, result: HashResult { hash } }
                }
                Err(error) => Response::Error { id, error },
            },
            Request::Restore { id, params } => match E::restore(&params.snapshot, &params.catalog) {
                Ok(engine) => {
                    let hash = engine.state_hash();
                    self.engine = Some(engine);
                    Response::Restore { id, result: HashResult { hash } }
                }
                Err(error) => Response::Error { id, error },
            },
            other => {
                let id = request_id(&other).clone();
                let Some(engine) = self.engine.as_mut() else {
                    return Response::Error {
                        id,
                        error: engine_error(EngineErrorCode::NotInitialized, "call create or restore first"),
                    };
                };
                dispatch(engine, other)
            }
        }
    }

    /// Answers one JSON-encoded request with a JSON-encoded response. Malformed input
    /// yields an `error` response with code `REJECTED_SCHEMA` and changes no state.
    pub fn handle_json(&mut self, request: &str) -> String {
        let response = match serde_json::from_str::<Request>(request) {
            Ok(req) => self.handle(req),
            Err(e) => Response::Error {
                id: salvage_id(request),
                error: engine_error(EngineErrorCode::RejectedSchema, format!("invalid request: {e}")),
            },
        };
        serde_json::to_string(&response).expect("responses always serialize")
    }
}

fn dispatch<E: Engine>(engine: &mut E, request: Request) -> Response {
    match request {
        Request::Submit { id, params } => Response::Submit { id, result: engine.submit(params.command) },
        Request::AdvanceTo { id, params } => match params.t_us.parse::<i64>() {
            Ok(t) => match engine.advance_to(t) {
                Ok(result) => Response::AdvanceTo { id, result },
                Err(error) => Response::Error { id, error },
            },
            Err(_) => Response::Error {
                id,
                error: engine_error(EngineErrorCode::RejectedSchema, "t_us is outside the signed 64-bit range"),
            },
        },
        Request::NextEventTime { id, .. } => Response::NextEventTime {
            id,
            result: NextEventTime { t_us: engine.next_event_time().map(canonical::time) },
        },
        Request::Observe { id, .. } => Response::Observe { id, result: engine.observe() },
        Request::EventsSince { id, params } => {
            let cursor = params.cursor.parse::<u64>().unwrap_or(u64::MAX);
            let limit = params.limit.parse::<u64>().unwrap_or(u64::MAX).min(u32::MAX as u64) as u32;
            Response::EventsSince { id, result: engine.events_since(cursor, limit) }
        }
        Request::Snapshot { id, .. } => Response::Snapshot { id, result: engine.snapshot() },
        Request::StateHash { id, .. } => Response::StateHash { id, result: HashResult { hash: engine.state_hash() } },
        Request::HistoryHead { id, .. } => {
            Response::HistoryHead { id, result: HashResult { hash: engine.history_head() } }
        }
        Request::Create { .. } | Request::Restore { .. } => unreachable!("handled by Host::handle"),
    }
}

fn request_id(request: &Request) -> &Uint {
    match request {
        Request::Create { id, .. }
        | Request::Restore { id, .. }
        | Request::Submit { id, .. }
        | Request::AdvanceTo { id, .. }
        | Request::NextEventTime { id, .. }
        | Request::Observe { id, .. }
        | Request::EventsSince { id, .. }
        | Request::Snapshot { id, .. }
        | Request::StateHash { id, .. }
        | Request::HistoryHead { id, .. } => id,
    }
}

/// Recovers the request id from malformed input when possible, else "0".
fn salvage_id(raw: &str) -> Uint {
    serde_json::from_str::<Value>(raw)
        .ok()
        .and_then(|v| v.get("id").and_then(Value::as_str).and_then(|s| s.parse::<Uint>().ok()))
        .unwrap_or_else(|| canonical::uint(0))
}
