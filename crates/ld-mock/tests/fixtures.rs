//! Drives the mock through the scripted fixtures in-process (no CLI) and checks the
//! properties tools/check_fixtures.py checks against the binary: determinism,
//! subdivision independence (V-05), and snapshot/restore equivalence (V-09).
//! The Python harness additionally validates every response against the schemas.

use std::path::PathBuf;

use ld_mock::MockEngine;
use ld_sdk::Host;
use serde_json::{json, Value};

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../fixtures")
}

fn fixture_names() -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(fixtures_dir())
        .unwrap()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().join("requests.jsonl").exists())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    assert!(names.len() >= 6, "expected the six scripted fixtures, found {names:?}");
    names
}

fn requests(name: &str) -> Vec<Value> {
    std::fs::read_to_string(fixtures_dir().join(name).join("requests.jsonl"))
        .unwrap()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

fn run(host: &mut Host<MockEngine>, reqs: &[Value]) -> Vec<Value> {
    reqs.iter()
        .enumerate()
        .map(|(i, r)| {
            let mut r = r.clone();
            r["id"] = json!((i + 1).to_string());
            serde_json::from_str(&host.handle_json(&r.to_string())).unwrap()
        })
        .collect()
}

fn body(reqs: &[Value]) -> Vec<Value> {
    reqs.iter()
        .filter(|r| matches!(r["method"].as_str(), Some("create" | "restore" | "submit" | "advance_to")))
        .cloned()
        .collect()
}

fn advance(t: i64) -> Value {
    json!({"id": "0", "method": "advance_to", "params": {"t_us": t.to_string()}})
}

fn req(method: &str) -> Value {
    json!({"id": "0", "method": method, "params": {}})
}

fn state_hash(host: &mut Host<MockEngine>) -> String {
    let r = run(host, &[req("state_hash")]);
    r[0]["result"]["hash"].as_str().unwrap().to_string()
}

fn fresh_run(reqs: &[Value]) -> (Host<MockEngine>, Vec<Value>) {
    let mut host = Host::new();
    let out = run(&mut host, reqs);
    (host, out)
}

#[test]
fn fixtures_run_without_errors_and_deterministically() {
    for name in fixture_names() {
        let reqs = requests(&name);
        let (mut a, out) = fresh_run(&reqs);
        for r in &out {
            assert_ne!(r["method"], "error", "{name}: {r}");
        }
        let (mut b, _) = fresh_run(&reqs);
        assert_eq!(state_hash(&mut a), state_hash(&mut b), "{name}: two runs diverged");
    }
}

#[test]
fn subdivided_advances_reach_the_same_state() {
    for name in fixture_names() {
        let reqs = body(&requests(&name));
        let (mut whole, _) = fresh_run(&reqs);

        let mut split = Vec::new();
        let mut now = 0i64;
        for r in &reqs {
            if r["method"] == "advance_to" {
                let target: i64 = r["params"]["t_us"].as_str().unwrap().parse().unwrap();
                for k in 1..=4 {
                    split.push(advance(now + (target - now) * k / 5 + 777));
                }
                now = target;
            }
            split.push(r.clone());
        }
        let (mut parts, _) = fresh_run(&split);
        assert_eq!(state_hash(&mut whole), state_hash(&mut parts), "{name}: subdivision changed the result");
    }
}

#[test]
fn restore_then_advance_equals_uninterrupted_run() {
    for name in fixture_names() {
        let reqs = body(&requests(&name));
        let catalog = reqs[0]["params"]["catalog"].clone();
        let (mut straight, out) = fresh_run(&reqs);
        let t_end: i64 = reqs
            .iter()
            .filter(|r| r["method"] == "advance_to")
            .map(|r| r["params"]["t_us"].as_str().unwrap().parse::<i64>().unwrap())
            .max()
            .unwrap_or(0);
        assert!(out.iter().all(|r| r["method"] != "error"));
        let snap = run(&mut straight, &[req("snapshot")])[0]["result"].clone();
        run(&mut straight, &[advance(t_end + 45_000_000)]);

        let mut resumed: Host<MockEngine> = Host::new();
        let r = run(&mut resumed, &[json!({"id": "0", "method": "restore", "params": {"snapshot": snap, "catalog": catalog}})]);
        assert_eq!(r[0]["method"], "restore", "{name}: {}", r[0]);
        run(&mut resumed, &[advance(t_end + 45_000_000)]);
        assert_eq!(state_hash(&mut straight), state_hash(&mut resumed), "{name}: restore diverged");
    }
}

#[test]
fn tampered_snapshot_is_rejected() {
    let reqs = body(&requests("running-loop"));
    let catalog = reqs[0]["params"]["catalog"].clone();
    let (mut host, _) = fresh_run(&reqs);
    let mut snap = run(&mut host, &[req("snapshot")])[0]["result"].clone();
    snap["state"]["machines"][0]["wear_accumulator"] = json!("1");
    let r = run(&mut Host::<MockEngine>::new(), &[json!({"id": "0", "method": "restore", "params": {"snapshot": snap, "catalog": catalog}})]);
    assert_eq!(r[0]["method"], "error");
}

#[test]
fn host_guards_uninitialized_and_malformed_requests() {
    let mut host: Host<MockEngine> = Host::new();
    let r: Value = serde_json::from_str(&host.handle_json(r#"{"id":"7","method":"observe","params":{}}"#)).unwrap();
    assert_eq!(r["method"], "error");
    assert_eq!(r["error"]["code"], "NOT_INITIALIZED");
    assert_eq!(r["id"], "7");

    let r: Value = serde_json::from_str(&host.handle_json(r#"{"id":"8","method":"teleport"}"#)).unwrap();
    assert_eq!(r["error"]["code"], "REJECTED_SCHEMA");
    assert_eq!(r["id"], "8", "the id is salvaged from a request that fails to parse");
}

#[test]
fn time_never_moves_backwards() {
    let reqs = body(&requests("running-loop"));
    let (mut host, _) = fresh_run(&reqs);
    let r = run(&mut host, &[advance(1)]);
    assert_eq!(r[0]["method"], "error", "advancing into the past must fail: {}", r[0]);
}
