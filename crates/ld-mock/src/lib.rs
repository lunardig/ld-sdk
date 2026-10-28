//! The mock engine (LD-PEC-002 §6).
//!
//! It implements I-1 exactly, with deliberately simplified physics, so the web UI and
//! external controllers can be built before the real engine exists:
//!
//! * Time advances in 1-second ticks on a fixed grid anchored at genesis. Any split of an
//!   advance into smaller advances therefore produces identical state.
//! * REACT moves material between connected buffers; WORK, ENERGY, WEAR and REQUIRE act
//!   as specified. PARTITION, COMBINE, PHASE, ASSEMBLE, LOSS and DWELL are accepted but
//!   move nothing. BATCH processes run like FLOW processes.
//! * One implicit electrical bus with no storage. Each tick, energy producers run first,
//!   then consumers take power in priority order; a consumer that cannot get power stalls
//!   with `STARVED_POWER`.
//! * Connections move material instantly. Commands apply at the current time only.
//! * Machines fail when their condition table reaches a zero capacity factor.
//!
//! Everything is integer or exact rational arithmetic over sorted maps.

mod catalog;
mod model;

pub use model::MockEngine;

pub const ENGINE_VERSION: &str = "mock-0.1.0";
pub const TICK_US: i64 = 1_000_000;
