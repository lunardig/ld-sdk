"""What a process takes and makes per run, from its catalog definition (SDK-003).

    from ld_client import alpha, recipe
    r = recipe(alpha(), "reduce-ilmenite", extent=400_000)
    r.inputs["ilmenite"]    # {"fetio3-s": Fraction(800000)}
    r.outputs["iron"]       # {"fe-s": Fraction(340000)}

Exact rationals. A FLOW process delivers fractional outputs as cumulative floors, so a run's
whole quantities can differ from these by under one unit of the substance; a BATCH delivers
them whole at the end. Covers the operations the alpha catalog uses (REACT, PHASE,
PARTITION, ASSEMBLE, LOSS, WORK, ENERGY, DWELL); anything else raises ValueError.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction


def _q(x) -> Fraction:
    return Fraction(int(x["n"]), int(x["d"])) if isinstance(x, dict) else Fraction(x)


def _expr(e: dict, extent: Fraction) -> Fraction:
    k = e["kind"]
    if k == "const":
        return _q(e["value"])
    if k == "ref" and e["name"] == "extent":
        return extent
    if k == "op":
        args = [_expr(a, extent) for a in e["args"]]
        if e["op"] == "mul":
            out = Fraction(1)
            for a in args:
                out *= a
            return out
        if e["op"] == "add":
            return sum(args, Fraction(0))
    raise ValueError(f"unsupported expression {e}")


@dataclass
class Recipe:
    process: str
    mode: str
    extent: Fraction
    extent_unit: str
    #: Material drawn through each input port, by species.
    inputs: dict[str, dict[str, Fraction]] = field(default_factory=dict)
    #: Material delivered through each output port, by species.
    outputs: dict[str, dict[str, Fraction]] = field(default_factory=dict)
    #: Discrete products made (ASSEMBLE), by product id.
    products: dict[str, Fraction] = field(default_factory=dict)
    #: Material sent to sinks (LOSS), by sink and species.
    losses: dict[str, dict[str, Fraction]] = field(default_factory=dict)
    #: Work quanta the run takes; divide by the machine's rated_work for microseconds.
    work: Fraction = Fraction(0)
    #: Bus energy drawn and produced over the run, in joules: the plain per-work energy, before
    #: any part-load loss (LD-PEC-004 A3-4), and before idle and startup draw.
    energy_drawn_j: Fraction = Fraction(0)
    energy_produced_j: Fraction = Fraction(0)
    dwell_us: Fraction = Fraction(0)


def _add(d: dict, k: str, sp: str, q: Fraction) -> None:
    if q:
        d.setdefault(k, {})
        d[k][sp] = d[k].get(sp, Fraction(0)) + q


def recipe(create_or_catalog: dict, process_id: str, extent=1) -> Recipe:
    """A run of `extent` units of a process: what it draws, delivers and costs."""
    cat = create_or_catalog.get("catalog", create_or_catalog)
    p = next(x for x in cat["processes"] if x["process_id"] == process_id)
    units = {u["unit_id"]: _q(u["scale"]) for u in cat["units"]}
    x = Fraction(extent)
    r = Recipe(process_id, p["mode"], x, p["extent"]["unit"])
    edges = p["edges"]
    in_ports = {i["port"]: set(i["accepts"]) for i in p["ports"]["in"]}
    # Streams waiting to enter each stage, and named streams a stage produced.
    arriving: dict[str, dict[str, Fraction]] = {}
    for st in p["stages"]:
        sid = st["stage_id"]
        pool: dict[str, Fraction] = dict(arriving.pop(sid, {}))
        feeding = [e["from"][5:] for e in edges if e["to"] == f"stage:{sid}" and e["from"].startswith("port:")]
        named: dict[str, dict[str, Fraction]] = {}

        def draw(sp: str, need: Fraction, port: str | None = None) -> None:
            """Take `need` of a species from the pool, drawing the shortfall from a port."""
            have = pool.get(sp, Fraction(0))
            short = need - have
            if short > 0:
                src = port or next((pt for pt in feeding if sp in in_ports.get(pt, ())), None)
                if src is None:
                    raise ValueError(f"{process_id}: no input for {sp} in stage {sid}")
                _add(r.inputs, src, sp, short)
                have = need
            pool[sp] = have - need

        for op in st["ops"]:
            kind = op["op"]
            if kind == "REACT":
                n = _expr(op["extent"], x)
                for sp, c in sorted(op["vector"].items(), key=lambda kv: Fraction(kv[1])):
                    c = Fraction(c) * n
                    if c < 0:
                        draw(sp, -c)
                    else:
                        pool[sp] = pool.get(sp, Fraction(0)) + c
            elif kind == "PHASE":
                n = _expr(op["quantity"], x)
                draw(op["from"], n)
                pool[op["to"]] = pool.get(op["to"], Fraction(0)) + n
            elif kind == "LOSS":
                n = _expr(op["quantity"], x)
                draw(op["species"], n)
                _add(r.losses, op["sink"], op["species"], n)
            elif kind == "PARTITION":
                if op["input"] == "pool":
                    src = pool
                    pool = {}
                else:
                    src = {}
                    for sp, e in op.get("draw", {}).items():
                        n = _expr(e, x)
                        _add(r.inputs, op["input"], sp, n)
                        src[sp] = n
                rest = dict(src)
                for o in op["outputs"]:
                    for sp, f in o["fractions"].items():
                        take = src.get(sp, Fraction(0)) * _q(f)
                        named.setdefault(o["stream"], {})
                        named[o["stream"]][sp] = named[o["stream"]].get(sp, Fraction(0)) + take
                        rest[sp] -= take
                named[op["residue"]] = {sp: q for sp, q in rest.items() if q}
            elif kind == "ASSEMBLE":
                count = _expr(op["count"], x)
                for sp, q in op["materials"].items():
                    draw(sp, Fraction(q) * count)
                r.products[op["product"]] = r.products.get(op["product"], Fraction(0)) + count
                if op.get("scrap"):
                    named[op["scrap_stream"]] = {sp: Fraction(q) * count for sp, q in op["scrap"].items()}
            elif kind == "WORK":
                r.work += _q(op["a"]) * x + _q(op["b"])
            elif kind == "ENERGY":
                if op["mode"] == "PER_WORK":
                    j = _q(op["amount"]) * units.get(op["unit"], Fraction(1))
                    if op["dir"] == "DRAW":
                        r.energy_drawn_j += j  # per work; scaled below
                    else:
                        r.energy_produced_j += j
            elif kind == "DWELL":
                r.dwell_us += _expr(op["duration_us"], x)
            elif kind in ("REQUIRE", "WEAR"):
                pass
            else:
                raise ValueError(f"{process_id}: operation {kind} is not covered")
        # Route this stage's streams: named streams and the remaining pool.
        for e in edges:
            if not e["from"].startswith(f"stage:{sid}"):
                continue
            stream = e["from"][len(f"stage:{sid}"):].lstrip(".")
            what = named.get(stream, {}) if stream else {sp: q for sp, q in pool.items() if q and sp in e.get("species", pool)}
            for sp, q in what.items():
                if not q:
                    continue
                if e["to"].startswith("port:"):
                    _add(r.outputs, e["to"][5:], sp, q)
                else:
                    tgt = e["to"][6:]
                    arriving.setdefault(tgt, {})
                    arriving[tgt][sp] = arriving[tgt].get(sp, Fraction(0)) + q
    # Per-work energy over the run's work.
    r.energy_drawn_j *= r.work
    r.energy_produced_j *= r.work
    return r
