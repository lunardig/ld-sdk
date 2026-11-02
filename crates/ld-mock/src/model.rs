use std::collections::{BTreeMap, BTreeSet};

use ld_sdk::canonical::{hash_value, id, int, time, to_i128, to_u128, uint};
use ld_sdk::rat::Rat;
use ld_sdk::types::*;
use ld_sdk::{engine_error, Engine};
use serde::Serialize;
use serde_json::{json, Value};

use crate::catalog::{capacity_factor, env_next_change, env_value, int_of, Catalog};
use crate::{ENGINE_VERSION, TICK_US};

// ------------------------------------------------------------------ model

#[derive(Clone)]
struct Machine {
    blueprint: String,
    location: String,
    wear: u128,
    setpoint: Option<Rat>,
    rate: Rat,
}

#[derive(Clone)]
struct Buffer {
    template: String,
    location: String,
    contents: BTreeMap<String, u128>,
}

#[derive(Clone)]
struct Conn {
    template: String,
    kind: ConnectionInstanceKind,
    from: String,
    to: String,
}

#[derive(Clone, Default)]
struct Lot {
    location: String,
    composition: BTreeMap<String, u128>,
    products: BTreeMap<String, u128>,
}

#[derive(Clone)]
struct Job {
    process: String,
    machine: String,
    status: JobStatus,
    reason: Option<Reason>,
    priority: i128,
    started: bool,
    target: u128,
    done_extent: u128,
    required_work: u128,
    done_work: u128,
    created_us: i64,
    last_us: i64,
    rate: Rat,
    restart_wait: BTreeSet<String>,
}

impl Job {
    fn eligible(&self) -> bool {
        self.started && matches!(self.status, JobStatus::Queued | JobStatus::Running | JobStatus::Starved | JobStatus::Blocked)
    }
    fn active(&self) -> bool {
        !matches!(self.status, JobStatus::Completed | JobStatus::Cancelled)
    }
}

pub struct MockEngine {
    cat: Catalog,
    world_id: String,
    claim_id: String,
    genesis_hash: Hash,
    start_us: i64,
    time: i64,
    deposits: Vec<Deposit>,
    environment: Vec<String>,
    env_last: BTreeMap<String, Rat>,
    machines: BTreeMap<String, Machine>,
    buffers: BTreeMap<String, Buffer>,
    conns: BTreeMap<String, Conn>,
    lots: BTreeMap<String, Lot>,
    jobs: BTreeMap<String, Job>,
    heat_j: u128,
    supply_last: Rat,
    demand_last: Rat,
    events: Vec<Event>,
    events_base: u64,
    next_seq: u64,
    history_head: Hash,
    history_count: u128,
    lot_counter: u128,
    applied: BTreeSet<String>,
}

type Reject = (AdmissionStatus, &'static str, String);

fn state_err(code: &'static str, msg: impl Into<String>) -> Reject {
    (AdmissionStatus::RejectedState, code, msg.into())
}

fn same<T: Serialize>(a: &T, b: &T) -> bool {
    serde_json::to_value(a).unwrap() == serde_json::to_value(b).unwrap()
}

fn map_u(m: &BTreeMap<Id, Uint>) -> BTreeMap<String, u128> {
    m.iter().map(|(k, v)| (k.to_string(), to_u128(v))).collect()
}

fn species_amounts(m: &BTreeMap<String, u128>) -> SpeciesAmounts {
    SpeciesAmounts(m.iter().map(|(k, v)| (id(k), uint(*v))).collect())
}

// ------------------------------------------------------------------ process helpers

struct ProcInfo {
    work_a: Rat,
    react: Vec<BTreeMap<String, i128>>,
    energy: Vec<(InstructionMode, InstructionDir, Rat)>,
    wear: Vec<(InstructionPer, Rat)>,
    require: Vec<Predicate>,
    produces_energy: bool,
    last_stage: String,
}

fn proc_info(p: &Process) -> ProcInfo {
    let mut info = ProcInfo {
        work_a: Rat::ZERO,
        react: vec![],
        energy: vec![],
        wear: vec![],
        require: vec![],
        produces_energy: false,
        last_stage: p.stages.last().map(|s| s.stage_id.to_string()).unwrap_or_default(),
    };
    for stage in &p.stages {
        for op in &stage.ops {
            match op {
                Instruction::React { vector, .. } => {
                    info.react.push(vector.iter().map(|(k, v)| (k.to_string(), int_of(v))).collect())
                }
                Instruction::Work { a, .. } => info.work_a = info.work_a.add(Rat::from(a)),
                Instruction::Energy { mode, dir, amount, .. } => {
                    if *dir == InstructionDir::Produce {
                        info.produces_energy = true;
                    }
                    info.energy.push((*mode, *dir, Rat::from(amount)));
                }
                Instruction::Wear { per, amount } => info.wear.push((*per, Rat::from(amount))),
                Instruction::Require { predicate } => info.require.push(predicate.clone()),
                _ => {}
            }
        }
    }
    info
}

fn required_work(p: &Process, target: u128) -> u128 {
    let mut total = Rat::ZERO;
    for stage in &p.stages {
        for op in &stage.ops {
            if let Instruction::Work { a, b } = op {
                total = total.add(Rat::from(a).mul(Rat::int(target as i128))).add(Rat::from(b));
            }
        }
    }
    total.floor().max(0) as u128
}

fn input_port(p: &Process, species: &str) -> Option<String> {
    p.ports.in_.iter().find(|pt| pt.accepts.iter().any(|a| **a == species)).map(|pt| pt.port.to_string())
}

fn output_port(p: &Process, species: &str) -> Option<String> {
    for e in &p.edges {
        if e.to.starts_with("port:") && e.species.iter().any(|s| **s == species) {
            return Some(e.to["port:".len()..].to_string());
        }
    }
    p.ports.out.iter().find(|pt| pt.accepts.iter().any(|a| **a == species)).map(|pt| pt.port.to_string())
}

/// A job's stall reason, flattened into a mock pending event `(kind, subject)` so it
/// survives snapshot/restore. Without it a restored engine would re-announce the
/// same stall and its event sequence would drift from an uninterrupted run (V-09).
fn reason_marker(job_id: &str, reason: &Reason, info: &ProcInfo) -> (String, String) {
    let (code, subject) = match reason {
        Reason::StarvedInput { port } => ("starved-input", port.to_string()),
        Reason::BlockedOutput { port } => ("blocked-output", port.to_string()),
        Reason::BelowRestartThreshold { port } => ("below-restart-threshold", port.to_string()),
        Reason::Failed { event_id } => ("failed", event_id.to_string()),
        Reason::StarvedPower => ("starved-power", job_id.to_string()),
        Reason::Dwelling => ("dwelling", job_id.to_string()),
        Reason::Maintenance => ("maintenance", job_id.to_string()),
        // The mock never runs a job at its feed's rate (LD-PEC-004 A3, after M1).
        Reason::Fed { .. } | Reason::FeedUnsettled { .. } => ("fed", job_id.to_string()),
        Reason::StarvedRequire { predicate } => {
            let want = serde_json::to_value(predicate).unwrap();
            let i = info.require.iter().position(|p| serde_json::to_value(p).unwrap() == want).unwrap_or(0);
            ("starved-require", format!("require.{i}"))
        }
    };
    (format!("why.{code}"), subject)
}

fn reason_from_marker(kind: &str, subject: &str, info: &ProcInfo) -> Option<Reason> {
    Some(match kind.strip_prefix("why.")? {
        "starved-input" => Reason::StarvedInput { port: id(subject) },
        "blocked-output" => Reason::BlockedOutput { port: id(subject) },
        "below-restart-threshold" => Reason::BelowRestartThreshold { port: id(subject) },
        "failed" => Reason::Failed { event_id: id(subject) },
        "starved-power" => Reason::StarvedPower,
        "dwelling" => Reason::Dwelling,
        "maintenance" => Reason::Maintenance,
        "starved-require" => {
            let i: usize = subject.strip_prefix("require.")?.parse().ok()?;
            Reason::StarvedRequire { predicate: info.require.get(i)?.clone() }
        }
        _ => return None,
    })
}

// ------------------------------------------------------------------ engine

impl MockEngine {
    fn rel(&self, t: i64) -> i64 {
        t - self.start_us
    }

    fn emit(&mut self, make: impl FnOnce(Uint, TimeUs) -> Event) {
        let event = make(uint(self.next_seq as u128), time(self.time));
        self.events.push(event);
        self.next_seq += 1;
    }

    fn emit_job_status(&mut self, job_id: &str) {
        let j = &self.jobs[job_id];
        let (status, reason) = (j.status, j.reason.clone());
        let jid = id(job_id);
        self.emit(|seq, t| Event::JobStatus { job_id: jid, reason, seq, status, time_us: t });
    }

    fn set_job(&mut self, job_id: &str, status: JobStatus, reason: Option<Reason>) {
        let j = self.jobs.get_mut(job_id).unwrap();
        let changed = j.status != status || !same(&j.reason, &reason);
        j.status = status;
        j.reason = reason;
        if changed {
            self.emit_job_status(job_id);
        }
    }

    fn connected_buffer(&self, port_ref: &str) -> Option<String> {
        for c in self.conns.values() {
            let other = if c.to == port_ref {
                &c.from
            } else if c.from == port_ref {
                &c.to
            } else {
                continue;
            };
            if let Some(b) = other.strip_suffix(".io") {
                if self.buffers.contains_key(b) {
                    return Some(b.to_string());
                }
            }
        }
        None
    }

    fn buffer_capacity(&self, b: &str) -> u128 {
        let tpl = &self.cat.buffers[&self.buffers[b].template];
        to_u128(&tpl.capacity.amount)
    }

    fn buffer_total(&self, b: &str) -> u128 {
        self.buffers[b].contents.values().sum()
    }

    /// Environment functions are evaluated at world time (LD-PEC-002 §2, Q-10 notes).
    fn update_environment(&mut self, t: i64) {
        for f in self.environment.clone() {
            let v = env_value(&self.cat.environment[&f], t);
            if self.env_last.get(&f) != Some(&v) {
                self.env_last.insert(f.clone(), v);
                let (fid, value) = (id(&f), v.to_rational());
                self.emit(|seq, t| Event::Environment { function_id: fid, seq, time_us: t, value });
            }
        }
    }

    fn eligible_jobs(&self) -> Vec<String> {
        let mut ids: Vec<(i128, i64, String)> = self
            .jobs
            .iter()
            .filter(|(_, j)| j.eligible())
            .map(|(k, j)| (-j.priority, j.created_us, k.clone()))
            .collect();
        ids.sort();
        ids.into_iter().map(|(_, _, k)| k).collect()
    }

    /// One tick ending at `t` (on the grid).
    fn tick(&mut self, t: i64) {
        self.time = t;
        self.update_environment(t);
        let order = self.eligible_jobs();
        let mut lanes: BTreeMap<String, u128> = BTreeMap::new();
        let mut supply = Rat::ZERO;
        let mut demand = Rat::ZERO;
        for m in self.machines.values_mut() {
            m.rate = Rat::ZERO;
        }
        for pass in 0..2 {
            for job_id in &order {
                let job = self.jobs[job_id].clone();
                let process = self.cat.processes[&job.process].clone();
                let info = proc_info(&process);
                if (pass == 0) != info.produces_energy {
                    continue;
                }
                self.run_job(job_id, &job, &process, &info, &mut lanes, &mut supply, &mut demand, t);
            }
        }
        self.supply_last = supply;
        self.demand_last = demand;
    }

    #[allow(clippy::too_many_arguments)]
    fn run_job(
        &mut self,
        job_id: &str,
        job: &Job,
        process: &Process,
        info: &ProcInfo,
        lanes: &mut BTreeMap<String, u128>,
        supply: &mut Rat,
        demand: &mut Rat,
        t: i64,
    ) {
        let m = job.machine.clone();
        let machine = self.machines[&m].clone();
        let bp = self.cat.machines[&machine.blueprint].clone();
        let stall = |me: &mut Self, status: JobStatus, reason: Option<Reason>| {
            let j = me.jobs.get_mut(job_id).unwrap();
            j.rate = Rat::ZERO;
            j.last_us = t;
            me.set_job(job_id, status, reason);
        };

        let used = lanes.entry(m.clone()).or_insert(0);
        if *used >= to_u128(&bp.lanes) {
            return stall(self, JobStatus::Queued, None);
        }
        *used += 1;

        let cf = capacity_factor(&bp, machine.wear);
        if cf.is_zero() {
            let event_id = id(&format!("wear-limit.{m}"));
            return stall(self, JobStatus::Failed, Some(Reason::Failed { event_id }));
        }
        for pred in &info.require {
            let ok = match pred {
                Predicate::Capability { capability } => bp.capabilities.iter().any(|c| c == capability),
                Predicate::Tool { .. } => true,
                Predicate::Environment { function_id, min, max } => {
                    let v = self.env_last.get(function_id.as_str()).copied().unwrap_or(Rat::ZERO);
                    v >= Rat::from(min) && v <= Rat::from(max)
                }
            };
            if !ok {
                return stall(self, JobStatus::Starved, Some(Reason::StarvedRequire { predicate: pred.clone() }));
            }
        }

        // Work available this tick.
        let mut work = Rat::from(&bp.rated_work).mul(Rat::int(TICK_US as i128)).mul(cf);
        if let Some(sp) = machine.setpoint {
            work = work.min(sp.mul(Rat::int(TICK_US as i128)));
        }
        let work_cap = work.floor().max(0) as u128;
        let remaining = job.target.saturating_sub(job.done_extent);
        let mut extent = if info.work_a.is_zero() { remaining } else { (Rat::int(work_cap as i128).div(info.work_a)).floor().max(0) as u128 };
        extent = extent.min(remaining);
        let mut reason: Option<(JobStatus, Reason)> = None;
        let mut limit = |e: &mut u128, cap: u128, why: (JobStatus, Reason)| {
            if cap < *e {
                *e = cap;
                if cap == 0 && reason.is_none() {
                    reason = Some(why);
                }
            }
        };

        // Material flows: per buffer, per unit of extent.
        let mut take: BTreeMap<(String, String), u128> = BTreeMap::new(); // (buffer, species) -> per extent
        let mut give: BTreeMap<String, Vec<(String, u128)>> = BTreeMap::new(); // buffer -> [(species, per extent)]
        for vector in &info.react {
            for (species, &coeff) in vector {
                if coeff < 0 {
                    let port = input_port(process, species).unwrap_or_else(|| "in".into());
                    let why = (JobStatus::Starved, Reason::StarvedInput { port: id(&port) });
                    match self.connected_buffer(&format!("{m}.{port}")) {
                        None => limit(&mut extent, 0, why),
                        Some(b) => {
                            let avail = self.buffers[&b].contents.get(species).copied().unwrap_or(0);
                            if let Some(th) = process.restart_thresholds.get(&id(&port)) {
                                // Below one unit the job is simply starved; between one unit and the
                                // threshold it waits for the threshold (LD-PEC-004 §5).
                                if job.restart_wait.contains(&port) && avail >= (-coeff) as u128 && avail < to_u128(&th.restart_at) {
                                    limit(&mut extent, 0, (JobStatus::Starved, Reason::BelowRestartThreshold { port: id(&port) }));
                                }
                            }
                            limit(&mut extent, avail / (-coeff) as u128, why);
                            *take.entry((b, species.clone())).or_insert(0) += (-coeff) as u128;
                        }
                    }
                } else if coeff > 0 {
                    let port = output_port(process, species).unwrap_or_else(|| "out".into());
                    match self.connected_buffer(&format!("{m}.{port}")) {
                        None => limit(&mut extent, 0, (JobStatus::Blocked, Reason::BlockedOutput { port: id(&port) })),
                        Some(b) => give.entry(b).or_default().push((species.clone(), coeff as u128)),
                    }
                }
            }
        }
        for (b, items) in &give {
            let per: u128 = items.iter().map(|(_, c)| c).sum();
            let headroom = self.buffer_capacity(b).saturating_sub(self.buffer_total(b));
            let port = output_port(process, &items[0].0).unwrap_or_else(|| "out".into());
            limit(&mut extent, headroom / per.max(1), (JobStatus::Blocked, Reason::BlockedOutput { port: id(&port) }));
        }

        // Energy for consumers.
        let work_for = |e: u128| -> Rat {
            if info.work_a.is_zero() { Rat::int(work_cap as i128) } else { info.work_a.mul(Rat::int(e as i128)) }
        };
        let energy_for = |e: u128, dir: InstructionDir| -> Rat {
            let mut total = Rat::ZERO;
            for (mode, d, amount) in &info.energy {
                if *d != dir || e == 0 {
                    continue;
                }
                total = total.add(match mode {
                    InstructionMode::PerWork => amount.mul(work_for(e)),
                    InstructionMode::PerTime => amount.mul(Rat::int(TICK_US as i128)),
                    InstructionMode::Startup => Rat::ZERO,
                });
            }
            total
        };
        if !info.produces_energy && extent > 0 {
            let available = supply.sub(*demand);
            if energy_for(extent, InstructionDir::Draw) > available {
                // Largest extent the remaining power supports (energy is monotone in extent).
                let (mut lo, mut hi) = (0u128, extent);
                while lo < hi {
                    let mid = lo + (hi - lo).div_ceil(2);
                    if energy_for(mid, InstructionDir::Draw) <= available { lo = mid } else { hi = mid - 1 }
                }
                limit(&mut extent, lo, (JobStatus::Starved, Reason::StarvedPower));
            }
        }

        if extent == 0 {
            let j = self.jobs.get_mut(job_id).unwrap();
            if let Some((_, Reason::StarvedInput { port })) = &reason {
                if process.restart_thresholds.contains_key(port) {
                    j.restart_wait.insert(port.to_string());
                }
            }
            let (status, why) = reason.map(|(s, r)| (s, Some(r))).unwrap_or((JobStatus::Starved, None));
            return stall(self, status, why);
        }

        // Apply.
        for ((b, species), per) in &take {
            *self.buffers.get_mut(b).unwrap().contents.get_mut(species).unwrap() -= per * extent;
        }
        for (b, items) in &give {
            for (species, per) in items {
                *self.buffers.get_mut(b).unwrap().contents.entry(species.clone()).or_insert(0) += per * extent;
            }
        }
        let drawn = energy_for(extent, InstructionDir::Draw);
        *demand = demand.add(drawn);
        *supply = supply.add(energy_for(extent, InstructionDir::Produce));
        self.heat_j += drawn.floor().max(0) as u128;
        let work_used = work_for(extent).floor().max(0) as u128;
        let mut wear = Rat::ZERO;
        for (per, amount) in &info.wear {
            wear = wear.add(match per {
                InstructionPer::Work => amount.mul(Rat::int(work_used as i128)),
                InstructionPer::Time => amount.mul(Rat::int(TICK_US as i128)),
            });
        }
        let rate = Rat::new(extent as i128, TICK_US as i128);
        {
            let mm = self.machines.get_mut(&m).unwrap();
            mm.wear += wear.floor().max(0) as u128;
            mm.rate = mm.rate.add(rate);
        }
        let done = {
            let j = self.jobs.get_mut(job_id).unwrap();
            j.done_extent += extent;
            j.done_work += work_used;
            j.rate = rate;
            j.last_us = t;
            j.restart_wait.clear();
            j.done_extent >= j.target
        };
        if done {
            let j = self.jobs.get_mut(job_id).unwrap();
            j.rate = Rat::ZERO;
            let (jid, stage) = (id(job_id), id(&info.last_stage));
            self.emit(|seq, t| Event::StageCompleted { job_id: jid, seq, stage_id: stage, time_us: t });
            self.set_job(job_id, JobStatus::Completed, None);
        } else {
            self.set_job(job_id, JobStatus::Running, None);
        }
    }

    fn next_tick_after(&self, t: i64) -> i64 {
        self.start_us + (self.rel(t).div_euclid(TICK_US) + 1) * TICK_US
    }

    fn any_eligible(&self) -> bool {
        self.jobs.values().any(Job::eligible)
    }

    /// Earliest grid tick at which some environment value may change, after `t`.
    fn next_env_tick(&self, t: i64) -> Option<i64> {
        self.environment
            .iter()
            .map(|f| {
                let change = env_next_change(&self.cat.environment[f], t);
                // first grid tick at or after the change
                self.start_us + (self.rel(change) + TICK_US - 1).div_euclid(TICK_US) * TICK_US
            })
            .min()
    }

    fn record_history(&mut self, inputs_hash: Hash) {
        // Finished jobs leave the state (LD-PEC-004 A2-1); M1 jobs never hold material.
        self.jobs.retain(|_, j| !matches!(j.status, JobStatus::Completed | JobStatus::Cancelled | JobStatus::Failed));
        let state_hash = self.state_hash();
        let record = HistoryRecord {
            prev_hash: self.history_head.clone(),
            world: id(&self.world_id),
            claim: id(&self.claim_id),
            timestamp: time(self.time),
            inputs_hash,
            transition_hash: state_hash.clone(),
            resulting_state_hash: Some(state_hash),
        };
        self.history_head = hash_value("ld.pec.history.v1", &record);
        self.history_count += 1;
    }

    // -------------------------------------------------------------- commands

    fn apply(&mut self, cmd: &Command) -> Result<(), Reject> {
        match cmd {
            Command::Install { args, .. } => {
                let lot = self.lots.get(&*args.lot_id).ok_or_else(|| state_err("unknown-lot", format!("no lot {}", *args.lot_id)))?;
                if lot.products.get(&*args.product).copied().unwrap_or(0) == 0 {
                    return Err(state_err("product-not-in-lot", format!("lot {} holds no {}", *args.lot_id, *args.product)));
                }
                let product = self.cat.products.get(&*args.product).ok_or_else(|| state_err("unknown-product", (*args.product).clone()))?;
                let installs = product.installs_as.clone().ok_or_else(|| state_err("not-installable", format!("{} does not install", *args.product)))?;
                let new_id = args.as_id.to_string();
                if self.id_in_use(&new_id) {
                    return Err(state_err("id-in-use", format!("{new_id} is already used")));
                }
                match installs.kind {
                    ProductBlueprintInstallsAsKind::Machine => {
                        if !self.cat.machines.contains_key(&*installs.template) {
                            return Err(state_err("unknown-blueprint", installs.template.to_string()));
                        }
                        self.machines.insert(new_id, Machine {
                            blueprint: installs.template.to_string(),
                            location: args.location.to_string(),
                            wear: 0,
                            setpoint: None,
                            rate: Rat::ZERO,
                        });
                    }
                    ProductBlueprintInstallsAsKind::Buffer => {
                        if !self.cat.buffers.contains_key(&*installs.template) {
                            return Err(state_err("unknown-template", installs.template.to_string()));
                        }
                        // Lots already located at the new buffer's id are adopted into it.
                        let mut contents: BTreeMap<String, u128> = BTreeMap::new();
                        let adopted: Vec<String> = self.lots.iter().filter(|(_, l)| l.location == new_id && l.products.is_empty()).map(|(k, _)| k.clone()).collect();
                        for k in adopted {
                            for (s, q) in self.lots.remove(&k).unwrap().composition {
                                *contents.entry(s).or_insert(0) += q;
                            }
                        }
                        self.buffers.insert(new_id, Buffer { template: installs.template.to_string(), location: args.location.to_string(), contents });
                    }
                }
                let lot = self.lots.get_mut(&*args.lot_id).unwrap();
                *lot.products.get_mut(&*args.product).unwrap() -= 1;
                lot.products.retain(|_, n| *n > 0);
                if lot.products.is_empty() && lot.composition.values().all(|q| *q == 0) {
                    self.lots.remove(&*args.lot_id);
                }
                Ok(())
            }
            Command::Remove { args, .. } => {
                let target = args.target_id.to_string();
                let product = if let Some(machine) = self.machines.get(&target) {
                    if self.jobs.values().any(|j| j.machine == target && j.active() && j.status != JobStatus::Failed) {
                        return Err(state_err("machine-busy", format!("{target} has active jobs")));
                    }
                    self.cat.product_for(ProductBlueprintInstallsAsKind::Machine, &machine.blueprint)
                } else if let Some(buffer) = self.buffers.get(&target) {
                    if buffer.contents.values().any(|q| *q > 0) {
                        return Err(state_err("buffer-not-empty", format!("{target} is not empty")));
                    }
                    self.cat.product_for(ProductBlueprintInstallsAsKind::Buffer, &buffer.template)
                } else {
                    return Err(state_err("unknown-target", format!("no machine or buffer {target}")));
                };
                let prefix = format!("{target}.");
                if self.conns.values().any(|c| c.from.starts_with(&prefix) || c.to.starts_with(&prefix)) {
                    return Err(state_err("still-connected", format!("disconnect {target} first")));
                }
                self.machines.remove(&target);
                self.buffers.remove(&target);
                if let Some(p) = product {
                    let lot = self.lots.entry("returned-items".into()).or_insert_with(|| Lot { location: "inventory".into(), ..Default::default() });
                    *lot.products.entry(p).or_insert(0) += 1;
                }
                Ok(())
            }
            Command::Connect { args, .. } => {
                if self.id_in_use(&args.connection_id) {
                    return Err(state_err("id-in-use", args.connection_id.to_string()));
                }
                let (from, to) = (args.from_port.to_string(), args.to_port.to_string());
                for p in [&from, &to] {
                    if !self.port_exists(p) {
                        return Err(state_err("unknown-port", format!("no port {p}")));
                    }
                }
                if from.split('.').next() == to.split('.').next() {
                    return Err(state_err("self-connection", "ports belong to the same equipment"));
                }
                let Some(tpl) = self.cat.connections.get(&*args.template) else {
                    return Err(state_err("unknown-template", format!("no connection template {}", *args.template)));
                };
                let kind = match tpl.kind {
                    ConnectionTemplateKind::Feed => ConnectionInstanceKind::Feed,
                    ConnectionTemplateKind::Conveyor => ConnectionInstanceKind::Conveyor,
                };
                self.conns.insert(args.connection_id.to_string(), Conn { template: args.template.to_string(), kind, from, to });
                Ok(())
            }
            Command::Disconnect { args, .. } => {
                self.conns.remove(&*args.connection_id).map(|_| ()).ok_or_else(|| state_err("unknown-connection", args.connection_id.to_string()))
            }
            Command::CreateJob { args, effective_us, .. } => {
                let jid = args.job_id.to_string();
                if self.jobs.contains_key(&jid) {
                    return Err(state_err("id-in-use", jid));
                }
                let process = self.cat.processes.get(&*args.process).ok_or_else(|| state_err("unknown-process", args.process.to_string()))?;
                let machine = self.machines.get(&*args.machine_id).ok_or_else(|| state_err("unknown-machine", format!("no machine {}", *args.machine_id)))?;
                if !self.cat.machines[&machine.blueprint].processes.contains(&args.process) {
                    return Err(state_err("process-not-supported", format!("{} cannot run {}", *args.machine_id, *args.process)));
                }
                let target = to_u128(&args.target_extent);
                if target == 0 {
                    return Err((AdmissionStatus::RejectedSchema, "zero-extent", "target_extent must be positive".into()));
                }
                let created = to_i128(effective_us) as i64;
                self.jobs.insert(jid.clone(), Job {
                    process: args.process.to_string(),
                    machine: args.machine_id.to_string(),
                    status: JobStatus::Queued,
                    reason: None,
                    priority: to_i128(&args.priority),
                    started: false,
                    target,
                    done_extent: 0,
                    required_work: required_work(process, target),
                    done_work: 0,
                    created_us: created,
                    last_us: created,
                    rate: Rat::ZERO,
                    restart_wait: BTreeSet::new(),
                });
                self.emit_job_status(&jid);
                Ok(())
            }
            Command::SetPriority { args, .. } => {
                self.job_mut(&args.job_id)?.priority = to_i128(&args.priority);
                Ok(())
            }
            Command::Start { args, .. } => {
                let j = self.job_mut(&args.job_id)?;
                if j.status != JobStatus::Queued || j.started {
                    return Err(state_err("not-startable", "only a queued, unstarted job can start"));
                }
                j.started = true;
                Ok(())
            }
            Command::Pause { args, .. } => {
                if !self.job_mut(&args.job_id)?.eligible() {
                    return Err(state_err("not-pausable", "only a started, unfinished job can pause"));
                }
                let jid = args.job_id.to_string();
                self.jobs.get_mut(&jid).unwrap().rate = Rat::ZERO;
                self.set_job(&jid, JobStatus::Paused, None);
                Ok(())
            }
            Command::Resume { args, .. } => {
                let jid = args.job_id.to_string();
                let j = self.job_mut(&args.job_id)?.clone();
                match j.status {
                    JobStatus::Paused => {}
                    JobStatus::Failed => {
                        let m = &self.machines[&j.machine];
                        if capacity_factor(&self.cat.machines[&m.blueprint], m.wear).is_zero() {
                            return Err(state_err("machine-failed", "repair the machine first"));
                        }
                    }
                    _ => return Err(state_err("not-resumable", "only a paused or failed job can resume")),
                }
                self.jobs.get_mut(&jid).unwrap().started = true;
                self.set_job(&jid, JobStatus::Queued, None);
                Ok(())
            }
            Command::Cancel { args, .. } => {
                let jid = args.job_id.to_string();
                if !self.job_mut(&args.job_id)?.active() {
                    return Err(state_err("already-finished", "job already completed or cancelled"));
                }
                self.jobs.get_mut(&jid).unwrap().rate = Rat::ZERO;
                self.set_job(&jid, JobStatus::Cancelled, None);
                Ok(())
            }
            Command::TransferLocal { args, .. } => {
                let c = self.conns.get(&*args.connection_id).ok_or_else(|| state_err("unknown-connection", args.connection_id.to_string()))?.clone();
                if c.kind != ConnectionInstanceKind::Conveyor {
                    return Err(state_err("not-a-conveyor", "TRANSFER_LOCAL moves material over a CONVEYOR"));
                }
                let (Some(src), Some(dst)) = (c.from.strip_suffix(".io"), c.to.strip_suffix(".io")) else {
                    return Err(state_err("not-buffer-to-buffer", "transfers run between two buffers"));
                };
                let (src, dst, species) = (src.to_string(), dst.to_string(), args.species.to_string());
                let amount = to_u128(&args.amount);
                if self.buffers[&src].contents.get(&species).copied().unwrap_or(0) < amount {
                    return Err((AdmissionStatus::RejectedCapacity, "insufficient-material", format!("{src} holds less than {amount} {species}")));
                }
                if self.buffer_capacity(&dst).saturating_sub(self.buffer_total(&dst)) < amount {
                    return Err((AdmissionStatus::RejectedCapacity, "insufficient-headroom", format!("{dst} cannot take {amount}")));
                }
                *self.buffers.get_mut(&src).unwrap().contents.get_mut(&species).unwrap() -= amount;
                *self.buffers.get_mut(&dst).unwrap().contents.entry(species).or_insert(0) += amount;
                Ok(())
            }
            Command::SetOperatingPolicy { args, .. } => {
                let m = self.machines.get(&*args.machine_id).ok_or_else(|| state_err("unknown-machine", args.machine_id.to_string()))?;
                let rated = Rat::from(&self.cat.machines[&m.blueprint].rated_work);
                let sp = Rat::from(&args.rate_setpoint);
                if sp <= Rat::ZERO || sp > rated {
                    return Err(state_err("setpoint-out-of-range", "rate_setpoint must be positive and at most rated_work"));
                }
                self.machines.get_mut(&*args.machine_id).unwrap().setpoint = Some(sp);
                Ok(())
            }
            Command::ScheduleMaintenance { args, .. } => {
                let m = self.machines.get_mut(&*args.machine_id).ok_or_else(|| state_err("unknown-machine", args.machine_id.to_string()))?;
                m.wear = 0; // The mock restores condition instantly and ignores the maintenance process.
                Ok(())
            }
            Command::Checkpoint { .. } => Ok(()),
        }
    }

    fn job_mut(&mut self, job_id: &Id) -> Result<&mut Job, Reject> {
        self.jobs.get_mut(&**job_id).ok_or_else(|| state_err("unknown-job", format!("no job {}", **job_id)))
    }

    fn id_in_use(&self, s: &str) -> bool {
        self.machines.contains_key(s) || self.buffers.contains_key(s) || self.conns.contains_key(s) || self.lots.contains_key(s)
    }

    fn port_exists(&self, port: &str) -> bool {
        let Some((owner, name)) = port.split_once('.') else { return false };
        if let Some(m) = self.machines.get(owner) {
            return self.cat.machines[&m.blueprint].ports.iter().any(|p| *p.port == name && p.type_ == MachinePortType::Material);
        }
        self.buffers.contains_key(owner) && name == "io"
    }

    // -------------------------------------------------------------- persistence

    fn physical_state(&self) -> PhysicalState {
        let mut lots: Vec<Lot_> = self.lots.iter().map(|(k, l)| Lot_ {
            lot_id: id(k),
            location: id(&l.location),
            state: LotState::Available,
            composition: species_amounts(&l.composition),
            products: l.products.iter().map(|(p, n)| (id(p), uint(*n))).collect(),
            // The mock does not model worn kits (LD-PEC-004 A1-1); the real engine does.
            wear: None,
            charge: None,
            core: None,
        }).collect();
        for (b, buf) in &self.buffers {
            lots.push(Lot_ {
                lot_id: id(&format!("{b}.lot")),
                location: id(b),
                state: LotState::Available,
                composition: species_amounts(&buf.contents),
                products: BTreeMap::new(),
                wear: None,
                charge: None,
                core: None,
            });
        }
        lots.sort_by(|a, b| a.lot_id.cmp(&b.lot_id));
        let mut pending = Vec::new();
        for (jid, j) in &self.jobs {
            if let Some(r) = &j.reason {
                let (kind, subject) = reason_marker(jid, r, &proc_info(&self.cat.processes[&j.process]));
                pending.push(PendingEvent { event_id: id(&format!("{jid}.why")), kind: id(&kind), subject: id(&subject), time_us: time(self.time) });
            }
            for port in &j.restart_wait {
                pending.push(PendingEvent { event_id: id(&format!("{jid}.{port}")), kind: id("restart-wait"), subject: id(jid), time_us: time(self.time) });
            }
        }
        // The tick grid's origin is a mock-only claim constant, kept as a pending event of
        // the mock's own kind.
        pending.push(PendingEvent { event_id: id("genesis-start"), kind: id("genesis-start"), subject: id(&self.claim_id), time_us: time(self.start_us) });
        pending.sort_by(|a, b| a.event_id.cmp(&b.event_id));
        let mut counters = BTreeMap::new();
        counters.insert(id("event-seq"), uint(self.next_seq as u128));
        counters.insert(id("history-count"), uint(self.history_count));
        counters.insert(id("lot"), uint(self.lot_counter));
        PhysicalState {
            schema: json!("ld.physical-state.v0"),
            claim_id: id(&self.claim_id),
            time_us: time(self.time),
            catalog_hash: self.cat.hash.clone(),
            environment: self.environment.iter().map(|f| id(f)).collect(),
            deposits: self.deposits.clone(),
            lots,
            buffers: self.buffers.iter().map(|(b, buf)| BufferInstance {
                buffer_id: id(b),
                template: id(&buf.template),
                location: id(&buf.location),
                lot_ids: vec![id(&format!("{b}.lot"))],
                reservations: vec![],
            }).collect(),
            machines: self.machines.iter().map(|(k, m)| MachineInstance {
                machine_id: id(k),
                blueprint: id(&m.blueprint),
                location: id(&m.location),
                mode: self.machine_mode(k),
                wear_accumulator: uint(m.wear),
                rate_setpoint: m.setpoint.unwrap_or_else(|| Rat::from(&self.cat.machines[&m.blueprint].rated_work)).to_rational(),
                assigned_job_ids: self.assigned(k),
            }).collect(),
            connections: self.conns.iter().map(|(k, c)| {
                let tpl = &self.cat.connections[&c.template];
                ConnectionInstance {
                    connection_id: id(k),
                    template: id(&c.template),
                    kind: c.kind,
                    from_port: id(&c.from),
                    to_port: id(&c.to),
                    throughput: tpl.throughput.clone(),
                    delay_us: tpl.delay_us.clone(),
                    active: true,
                }
            }).collect(),
            energy_stores: vec![],
            jobs: self.jobs.iter().map(|(k, j)| JobInstance {
                job_id: id(k),
                process: id(&j.process),
                process_hash: self.cat.process_hashes[&j.process].clone(),
                machine_id: id(&j.machine),
                status: j.status,
                started: j.started,
                reason: j.reason.clone(),
                priority: int(j.priority),
                target_extent: uint(j.target),
                completed_extent: uint(j.done_extent),
                required_work: uint(j.required_work),
                completed_work: uint(j.done_work),
                current_stage: id(&self.cat.processes[&j.process].stages[0].stage_id),
                held_lot_ids: vec![],
                created_at_us: time(j.created_us),
                last_advanced_us: time(j.last_us),
                rate: None,
            }).collect(),
            in_transit: vec![],
            sinks: vec![SinkRecord { sink_id: id("waste-heat"), kind: SinkRecordKind::Heat, totals: SpeciesAmounts(BTreeMap::new()), energy: uint(self.heat_j) }],
            pending_events: pending,
            residuals: vec![],
            id_counters: counters,
        }
    }

    fn assigned(&self, machine: &str) -> Vec<Id> {
        self.jobs.iter().filter(|(_, j)| j.machine == machine && j.active()).map(|(k, _)| id(k)).collect()
    }

    fn machine_mode(&self, machine: &str) -> MachineMode {
        let m = &self.machines[machine];
        if capacity_factor(&self.cat.machines[&m.blueprint], m.wear).is_zero() {
            return MachineMode::Failed;
        }
        let statuses: Vec<JobStatus> = self.jobs.values().filter(|j| j.machine == machine).map(|j| j.status).collect();
        for (s, mode) in [(JobStatus::Running, MachineMode::Running), (JobStatus::Starved, MachineMode::Starved), (JobStatus::Blocked, MachineMode::Blocked)] {
            if statuses.contains(&s) {
                return mode;
            }
        }
        MachineMode::Idle
    }

    fn from_state(cat: Catalog, s: &PhysicalState, env: &ClaimEnvelope, environment: Vec<String>, start_us: i64) -> MockEngine {
        let mut buffers = BTreeMap::new();
        let lot_of: BTreeMap<String, &Lot_> = s.lots.iter().map(|l| (l.lot_id.to_string(), l)).collect();
        for b in &s.buffers {
            let contents = lot_of.get(&format!("{}.lot", *b.buffer_id)).map(|l| map_u(&l.composition.0)).unwrap_or_default();
            buffers.insert(b.buffer_id.to_string(), Buffer { template: b.template.to_string(), location: b.location.to_string(), contents });
        }
        let lots = s.lots.iter().filter(|l| !buffers.contains_key(&*l.location) || !l.lot_id.ends_with(".lot")).map(|l| (l.lot_id.to_string(), Lot {
            location: l.location.to_string(),
            composition: map_u(&l.composition.0),
            products: map_u(&l.products),
        })).collect();
        let mut jobs = BTreeMap::new();
        for j in &s.jobs {
            let jid = j.job_id.to_string();
            let restart_wait = s.pending_events.iter().filter(|e| *e.kind == "restart-wait" && *e.subject == jid)
                .map(|e| e.event_id[jid.len() + 1..].to_string()).collect();
            let info = proc_info(&cat.processes[&*j.process]);
            let why = format!("{jid}.why");
            let reason = s.pending_events.iter().find(|e| *e.event_id == why)
                .and_then(|e| reason_from_marker(&e.kind, &e.subject, &info));
            jobs.insert(jid.clone(), Job {
                process: j.process.to_string(),
                machine: j.machine_id.to_string(),
                status: j.status,
                reason,
                priority: to_i128(&j.priority),
                started: j.started,
                target: to_u128(&j.target_extent),
                done_extent: to_u128(&j.completed_extent),
                required_work: to_u128(&j.required_work),
                done_work: to_u128(&j.completed_work),
                created_us: to_i128(&j.created_at_us) as i64,
                last_us: to_i128(&j.last_advanced_us) as i64,
                rate: Rat::ZERO,
                restart_wait,
            });
        }
        let counter = |k: &str| s.id_counters.get(&id(k)).map(|v| to_u128(v)).unwrap_or(0);
        let time_now = to_i128(&s.time_us) as i64;
        let mut engine = MockEngine {
            world_id: env.world_id.to_string(),
            claim_id: s.claim_id.to_string(),
            genesis_hash: env.genesis_hash.clone(),
            start_us,
            time: time_now,
            deposits: s.deposits.clone(),
            environment,
            env_last: BTreeMap::new(),
            machines: s.machines.iter().map(|m| {
                let rated = Rat::from(&cat.machines[&*m.blueprint].rated_work);
                let sp = Rat::from(&m.rate_setpoint);
                (m.machine_id.to_string(), Machine {
                    blueprint: m.blueprint.to_string(),
                    location: m.location.to_string(),
                    wear: to_u128(&m.wear_accumulator),
                    setpoint: (sp != rated).then_some(sp),
                    rate: Rat::ZERO,
                })
            }).collect(),
            buffers,
            conns: s.connections.iter().map(|c| (c.connection_id.to_string(), Conn { template: c.template.to_string(), kind: c.kind, from: c.from_port.to_string(), to: c.to_port.to_string() })).collect(),
            lots,
            jobs,
            heat_j: s.sinks.iter().map(|k| to_u128(&k.energy)).sum(),
            supply_last: Rat::ZERO,
            demand_last: Rat::ZERO,
            events: vec![],
            events_base: counter("event-seq") as u64,
            next_seq: counter("event-seq") as u64,
            history_head: env.history_head.clone(),
            history_count: counter("history-count"),
            lot_counter: counter("lot"),
            applied: BTreeSet::new(),
            cat,
        };
        let rel = engine.rel(time_now);
        for f in engine.environment.clone() {
            let v = env_value(&engine.cat.environment[&f], rel);
            engine.env_last.insert(f, v);
        }
        engine
    }
}

// Local alias so the generated `Lot` type and the model's `Lot` do not collide.
use ld_sdk::types::Lot as Lot_;

impl Engine for MockEngine {
    fn create(genesis: &Genesis, catalog: &CatalogBundle) -> Result<Self, EngineError> {
        let cat = Catalog::load(catalog)?;
        if genesis.catalog_hash != cat.hash {
            return Err(engine_error(EngineErrorCode::RejectedSchema, "genesis.catalog_hash does not match the catalog"));
        }
        for f in &genesis.environment {
            if !cat.environment.contains_key(&**f) {
                return Err(engine_error(EngineErrorCode::RejectedSchema, format!("unknown environment function {}", **f)));
            }
        }
        let start_us = to_i128(&genesis.start_us) as i64;
        let genesis_hash = hash_value("ld.genesis.v0", genesis);
        let mut engine = MockEngine {
            world_id: genesis.world_id.to_string(),
            claim_id: genesis.claim_id.to_string(),
            genesis_hash: genesis_hash.clone(),
            start_us,
            time: start_us,
            deposits: genesis.deposits.clone(),
            environment: genesis.environment.iter().map(|f| f.to_string()).collect(),
            env_last: BTreeMap::new(),
            machines: BTreeMap::new(),
            buffers: BTreeMap::new(),
            conns: BTreeMap::new(),
            lots: genesis.starting_lots.iter().map(|l| (l.lot_id.to_string(), Lot {
                location: l.location.to_string(),
                composition: map_u(&l.composition.0),
                products: map_u(&l.products),
            })).collect(),
            jobs: BTreeMap::new(),
            heat_j: 0,
            supply_last: Rat::ZERO,
            demand_last: Rat::ZERO,
            events: vec![],
            events_base: 0,
            next_seq: 0,
            history_head: genesis_hash,
            history_count: 0,
            lot_counter: 0,
            applied: BTreeSet::new(),
            cat,
        };
        engine.update_environment(start_us);
        Ok(engine)
    }

    fn restore(snapshot: &Snapshot, catalog: &CatalogBundle) -> Result<Self, EngineError> {
        let reject = |m: &str| Err(engine_error(EngineErrorCode::RejectedSchema, m));
        if !snapshot.engine_version.starts_with("mock") {
            return reject("the mock engine restores only its own snapshots");
        }
        let mut body = serde_json::to_value(snapshot).unwrap();
        body.as_object_mut().unwrap().remove("snapshot_hash");
        if ld_sdk::canonical::domain_hash("ld.snapshot.v0", &serde_json::to_vec(&body).unwrap()) != *snapshot.snapshot_hash {
            return reject("snapshot_hash does not match the snapshot");
        }
        if hash_value("ld.pec.physical-state.v1", &snapshot.state) != snapshot.envelope.state_hash {
            return reject("envelope.state_hash does not match the state");
        }
        let cat = Catalog::load(catalog)?;
        if cat.hash != snapshot.catalog_hash {
            return reject("snapshot was taken with a different catalog");
        }
        // The grid origin and active environment functions are claim constants the
        // mock records as pending events of its own kinds (see physical_state).
        let start_us = snapshot.state.pending_events.iter().find(|e| *e.kind == "genesis-start").map(|e| to_i128(&e.time_us) as i64).unwrap_or(0);
        let environment = snapshot.state.environment.iter().map(|f| f.to_string()).collect();
        Ok(MockEngine::from_state(cat, &snapshot.state, &snapshot.envelope, environment, start_us))
    }

    fn submit(&mut self, cmd: Command) -> Admission {
        let (command_id, effective_us) = command_meta(&cmd);
        let cid = command_id.to_string();
        let result: Result<(), Reject> = if to_i128(&effective_us) as i64 != self.time {
            Err((AdmissionStatus::RejectedTime, "mock-current-time-only", format!("the mock applies commands at the current time {} only", self.time)))
        } else if self.applied.contains(&cid) {
            Err(state_err("duplicate-command", format!("command {cid} was already submitted")))
        } else {
            self.apply(&cmd)
        };
        let admission = match result {
            Ok(()) => {
                self.applied.insert(cid);
                self.record_history(hash_value("ld.command.v0", &cmd));
                Admission { command_id: command_id.clone(), status: AdmissionStatus::Accepted, detail: None }
            }
            Err((status, code, message)) => Admission {
                command_id: command_id.clone(),
                status,
                detail: Some(ErrorDetail { code: id(code), message }),
            },
        };
        let status = admission.status;
        self.emit(|seq, t| Event::Command { command_id, seq, status, time_us: t });
        admission
    }

    fn advance_to(&mut self, t_us: i64) -> Result<AdvanceReport, EngineError> {
        if t_us < self.time {
            return Err(engine_error(EngineErrorCode::RejectedTime, format!("cannot advance backwards from {} to {t_us}", self.time)));
        }
        let first_seq = self.next_seq;
        loop {
            let next = if self.any_eligible() {
                Some(self.next_tick_after(self.time))
            } else {
                // Nothing runs: jump straight to the next environment change, if any.
                self.next_env_tick(self.time)
            };
            match next {
                Some(tick) if tick <= t_us => self.tick(tick),
                _ => break,
            }
        }
        if self.time != t_us && !self.any_eligible() {
            self.supply_last = Rat::ZERO;
            self.demand_last = Rat::ZERO;
        }
        self.time = t_us;
        Ok(AdvanceReport {
            time_us: time(self.time),
            events_appended: uint((self.next_seq - first_seq) as u128),
            history_records: uint(0),
        })
    }

    fn next_event_time(&self) -> Option<i64> {
        if self.any_eligible() { Some(self.next_tick_after(self.time)) } else { self.next_env_tick(self.time) }
    }

    fn observe(&self) -> ClaimView {
        let tick = Rat::int(TICK_US as i128);
        ClaimView {
            deposits: vec![],
            sinks: vec![],
            time_us: time(self.time),
            state_hash: self.state_hash(),
            environment: self.env_last.iter().map(|(k, v)| (id(k), v.to_rational())).collect(),
            machines: self.machines.iter().map(|(k, m)| {
                let bp = &self.cat.machines[&m.blueprint];
                MachineView {
                    machine_id: id(k),
                    blueprint: id(&m.blueprint),
                    mode: self.machine_mode(k),
                    capacity_factor: capacity_factor(bp, m.wear).to_rational(),
                    wear_accumulator: uint(m.wear),
                    assigned_job_ids: self.assigned(k),
                    rate: m.rate.to_rational(),
                    unpowered: None,
                    core: None,
                }
            }).collect(),
            jobs: self.jobs.iter().map(|(k, j)| JobView {
                job_id: id(k),
                process: id(&j.process),
                machine_id: id(&j.machine),
                status: j.status,
                priority: int(j.priority),
                reason: j.reason.clone(),
                completed_work: uint(j.done_work),
                required_work: uint(j.required_work),
                energy_held: None,
                rate: j.rate.to_rational(),
            }).collect(),
            buffers: self.buffers.iter().map(|(k, b)| {
                let tpl = &self.cat.buffers[&b.template];
                BufferView { buffer_id: id(k), template: Some(id(&b.template)), contents: species_amounts(&b.contents), capacity: tpl.capacity.clone(), reserved: SpeciesAmounts(BTreeMap::new()) }
            }).collect(),
            energy: EnergyView {
                unit: id("j"),
                supply: self.supply_last.div(tick).to_rational(),
                demand: self.demand_last.div(tick).to_rational(),
                stores: vec![],
            },
            connections: self.conns.iter().map(|(k, c)| ConnectionView {
                connection_id: id(k),
                from_port: id(&c.from),
                to_port: id(&c.to),
                throughput: Rat::int(1_000_000).to_rational(),
                in_transit_lot_ids: vec![],
            }).collect(),
            lots: self.physical_state().lots,
        }
    }

    fn events_since(&self, cursor: u64, limit: u32) -> EventPage {
        let start = cursor.max(self.events_base);
        let from = (start - self.events_base) as usize;
        let events: Vec<Event> = self.events.iter().skip(from).take(limit as usize).cloned().collect();
        let next = start + events.len() as u64;
        EventPage { events, next_cursor: uint(next as u128) }
    }

    fn snapshot(&self) -> Snapshot {
        let state = self.physical_state();
        let envelope = ClaimEnvelope {
            state_hash: hash_value("ld.pec.physical-state.v1", &state),
            history_head: self.history_head.clone(),
            world_id: id(&self.world_id),
            claim_id: id(&self.claim_id),
            genesis_hash: self.genesis_hash.clone(),
        };
        let mut body = json!({
            "schema": "ld.snapshot.v0",
            "state": state,
            "envelope": envelope,
            "catalog_hash": self.cat.hash,
            "engine_version": ENGINE_VERSION,
        });
        let snapshot_hash = ld_sdk::canonical::domain_hash("ld.snapshot.v0", &serde_json::to_vec(&body).unwrap());
        body.as_object_mut().unwrap().insert("snapshot_hash".into(), Value::String(snapshot_hash));
        serde_json::from_value(body).expect("mock snapshots match the I-3 schema")
    }

    fn state_hash(&self) -> Hash {
        hash_value("ld.pec.physical-state.v1", &self.physical_state())
    }

    fn history_head(&self) -> Hash {
        self.history_head.clone()
    }
}

fn command_meta(cmd: &Command) -> (Id, TimeUs) {
    let v = serde_json::to_value(cmd).unwrap();
    (
        v["command_id"].as_str().unwrap().parse().unwrap(),
        v["effective_us"].as_str().unwrap().parse().unwrap(),
    )
}
