//! Canonical JSON and domain-separated hashing (LD-PEC-001 §2.4, LD-PEC-002 §2).

use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::types::{Hash, Id, Int, PositiveInt, TimeUs, Uint};

/// JCS bytes for the LD-PEC-002 profile. `serde_json::Map` is sorted (no `preserve_order`),
/// object keys are ASCII identifiers, and the profile contains no JSON numbers.
pub fn canonical_bytes<T: Serialize>(value: &T) -> Vec<u8> {
    let value = serde_json::to_value(value).expect("canonical values serialize");
    debug_assert!(!contains_number(&value), "canonical values must not contain JSON numbers");
    serde_json::to_vec(&value).expect("canonical values serialize")
}

fn contains_number(v: &Value) -> bool {
    match v {
        Value::Number(_) => true,
        Value::Array(a) => a.iter().any(contains_number),
        Value::Object(o) => o.values().any(contains_number),
        _ => false,
    }
}

/// `H(domain, payload) = SHA256(UTF8(domain) || 0x00 || uint64_be(len) || payload)`.
pub fn domain_hash(domain: &str, payload: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(domain.as_bytes());
    h.update([0u8]);
    h.update((payload.len() as u64).to_be_bytes());
    h.update(payload);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Hash of a value's canonical bytes in a domain.
pub fn hash_value<T: Serialize>(domain: &str, value: &T) -> Hash {
    hash(domain_hash(domain, &canonical_bytes(value)))
}

// ------------------------------------------------------------- scalar constructors
// The generated newtypes validate their pattern on construction; these never fail for
// the inputs they accept.

pub fn uint(v: u128) -> Uint {
    v.to_string().parse().expect("decimal digits are a valid Uint")
}
pub fn int(v: i128) -> Int {
    v.to_string().parse().expect("decimal digits are a valid Int")
}
pub fn positive(v: u128) -> PositiveInt {
    assert!(v > 0, "PositiveInt must be positive");
    v.to_string().parse().expect("decimal digits are a valid PositiveInt")
}
pub fn time(v: i64) -> TimeUs {
    v.to_string().parse().expect("decimal digits are a valid TimeUs")
}
pub fn hash(hex: String) -> Hash {
    hex.parse().expect("sha256 hex is a valid Hash")
}
/// An identifier. Panics on an invalid id, so use it only for ids the engine composes
/// from ids that are already valid; validate untrusted strings with `str::parse`.
pub fn id(s: &str) -> Id {
    s.parse().unwrap_or_else(|_| panic!("invalid identifier {s:?}"))
}

/// Parses a decimal-string scalar the schema has already validated.
pub fn to_u128(s: &str) -> u128 {
    s.parse().expect("schema-validated unsigned integer")
}
pub fn to_i128(s: &str) -> i128 {
    s.parse().expect("schema-validated signed integer")
}
