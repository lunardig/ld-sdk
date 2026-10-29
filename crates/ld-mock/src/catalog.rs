//! Catalog loading: index entries by id and check them against the manifest hashes.

use std::collections::BTreeMap;

use ld_sdk::canonical::{hash_value, to_i128, to_u128};
use ld_sdk::rat::Rat;
use ld_sdk::types::*;
use ld_sdk::engine_error;
use serde::Serialize;

pub struct Catalog {
    pub hash: Hash,
    pub processes: BTreeMap<String, Process>,
    pub process_hashes: BTreeMap<String, Hash>,
    pub machines: BTreeMap<String, MachineBlueprint>,
    pub products: BTreeMap<String, ProductBlueprint>,
    pub buffers: BTreeMap<String, BufferTemplate>,
    pub connections: BTreeMap<String, ConnectionTemplate>,
    pub environment: BTreeMap<String, EnvironmentFunction>,
}

fn reject(msg: String) -> EngineError {
    engine_error(EngineErrorCode::RejectedSchema, msg)
}

impl Catalog {
    pub fn load(bundle: &CatalogBundle) -> Result<Catalog, EngineError> {
        // Recompute every entry hash and compare with the manifest (LD-PEC-002 §3).
        let mut computed: Vec<(String, String, String)> = Vec::new();
        fn add<T: Serialize>(out: &mut Vec<(String, String, String)>, kind: &str, id: &str, item: &T) {
            out.push((kind.into(), id.into(), hash_value(&format!("ld.{kind}.v0"), item).to_string()));
        }
        bundle.elements.iter().for_each(|e| add(&mut computed, "element", &e.element_id, e));
        bundle.units.iter().for_each(|e| add(&mut computed, "unit", &e.unit_id, e));
        bundle.substances.iter().for_each(|e| add(&mut computed, "substance", &e.substance_id, e));
        bundle.processes.iter().for_each(|e| add(&mut computed, "process", &e.process_id, e));
        bundle.machines.iter().for_each(|e| add(&mut computed, "machine", &e.blueprint_id, e));
        bundle.products.iter().for_each(|e| add(&mut computed, "product", &e.product_id, e));
        bundle.buffers.iter().for_each(|e| add(&mut computed, "buffer", &e.template_id, e));
        bundle.connections.iter().for_each(|e| add(&mut computed, "connection", &e.template_id, e));
        bundle.environment.iter().for_each(|e| add(&mut computed, "environment", &e.function_id, e));
        computed.sort();

        let mut declared: Vec<(String, String, String)> = bundle
            .manifest
            .entries
            .iter()
            .map(|e| {
                let kind = serde_json::to_value(e.kind).unwrap().as_str().unwrap().to_string();
                (kind, e.id.to_string(), e.hash.to_string())
            })
            .collect();
        declared.sort();
        if computed != declared {
            let first = computed
                .iter()
                .zip(declared.iter())
                .find(|(a, b)| a != b)
                .map(|(a, _)| format!("{} {}", a.0, a.1))
                .unwrap_or_else(|| "entry count".into());
            return Err(reject(format!("catalog does not match its manifest (first difference: {first})")));
        }

        let hash = hash_value("ld.catalog.v0", &bundle.manifest);
        let mut process_hashes = BTreeMap::new();
        for p in &bundle.processes {
            process_hashes.insert(p.process_id.to_string(), hash_value("ld.process.v0", p));
        }
        Ok(Catalog {
            hash,
            processes: bundle.processes.iter().map(|p| (p.process_id.to_string(), p.clone())).collect(),
            process_hashes,
            machines: bundle.machines.iter().map(|m| (m.blueprint_id.to_string(), m.clone())).collect(),
            products: bundle.products.iter().map(|p| (p.product_id.to_string(), p.clone())).collect(),
            buffers: bundle.buffers.iter().map(|b| (b.template_id.to_string(), b.clone())).collect(),
            connections: bundle.connections.iter().map(|c| (c.template_id.to_string(), c.clone())).collect(),
            environment: bundle.environment.iter().map(|f| (f.function_id.to_string(), f.clone())).collect(),
        })
    }

    /// The product that installs as the given machine blueprint or buffer template.
    pub fn product_for(&self, kind: ProductBlueprintInstallsAsKind, template: &str) -> Option<String> {
        self.products
            .values()
            .find(|p| p.installs_as.as_ref().is_some_and(|i| i.kind == kind && *i.template == template))
            .map(|p| p.product_id.to_string())
    }
}

/// Capacity factor from a blueprint's condition table: the last step at or below `wear`.
pub fn capacity_factor(bp: &MachineBlueprint, wear: u128) -> Rat {
    let mut factor = Rat::ONE;
    for step in &bp.condition_table {
        if to_u128(&step.wear_at) <= wear {
            factor = Rat::from(&step.capacity_factor);
        }
    }
    factor
}

/// Value of an environment function at time `rel_us` after genesis.
pub fn env_value(f: &EnvironmentFunction, rel_us: i64) -> Rat {
    let period = to_u128(&f.period_us) as i128;
    let phase = (rel_us as i128).rem_euclid(period);
    let mut value = Rat::from(&f.segments[0].value);
    for seg in &f.segments {
        if to_u128(&seg.start_us) as i128 <= phase {
            value = Rat::from(&seg.value);
        }
    }
    value
}

/// First time strictly after `rel_us` at which the function's value may change.
pub fn env_next_change(f: &EnvironmentFunction, rel_us: i64) -> i64 {
    let period = to_u128(&f.period_us) as i128;
    let base = (rel_us as i128).div_euclid(period) * period;
    let phase = (rel_us as i128).rem_euclid(period);
    let starts: Vec<i128> = f.segments.iter().map(|s| to_u128(&s.start_us) as i128).collect();
    let next = starts.iter().find(|&&s| s > phase).map(|&s| base + s).unwrap_or(base + period + starts[0]);
    next as i64
}

pub fn int_of(i: &Int) -> i128 {
    to_i128(i)
}
