"""Create parameters: a genesis and a catalog bundle. The SDK ships the alpha catalog."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .view import q

# The SDK layout: python/ld_client/ next to fixtures/alpha/create.json; a release puts
# the catalog next to the package as catalog/alpha/create.json.
_HERE = Path(__file__).resolve().parent
_CANDIDATES = [_HERE / "data" / "alpha.json", _HERE.parents[1] / "fixtures" / "alpha" / "create.json"]


def load_create(path: str | Path) -> dict:
    """Read create params ({genesis, catalog}) from a JSON file."""
    return json.loads(Path(path).read_text())


# The sites (LD-PEC-011): where a claim is, which fixes its deposit and its light. A site is
# a choice made at genesis; the catalog holds every site, so all claims in a world can pin one
# catalog hash (LD-PEC-010 Q-07). `processes` names what a controller runs there. Yields are
# mmol per kg of regolith: ice from 5 wt%, ilmenite from 10 or 1 wt%, the rest silicate.
SITES = {
    "training": {
        "name": "Proving Ground", "fictional": True,
        "summary": "The alpha's mixed deposit, for learning the loop: ice and ilmenite together, which no real site "
                   "has, under an equatorial day and night.",
        "deposit": "icy-crater", "regolith": "regolith", "bank": "regolith-bank", "environment": "irradiance",
        "processes": {"solar": "generate-solar", "excavate": "excavate", "beneficiate": "beneficiate"},
        "yield": {"ice": 2775, "ilmenite": 659, "silicate": 14148},
    },
    "polar": {
        "name": "Polar cold trap", "fictional": False,
        "summary": "Ice-bearing highland regolith in permanent shadow, with arrays on a nearly always lit ridge: "
                   "5 wt% ice, 1 wt% ilmenite, four dark days a lunar month.",
        "deposit": "polar-cold-trap", "regolith": "regolith-polar", "bank": "regolith-bank-polar", "environment": "irradiance-polar",
        "processes": {"solar": "generate-solar-polar", "excavate": "excavate-polar", "beneficiate": "beneficiate-polar"},
        "yield": {"ice": 2775, "ilmenite": 66, "silicate": 15646},
    },
    "mare": {
        "name": "High-Ti mare", "fictional": False,
        "summary": "Dry equatorial mare regolith: 10 wt% ilmenite, no ice, and a 354-hour night.",
        "deposit": "mare-basin", "regolith": "regolith-mare", "bank": "regolith-bank-mare", "environment": "irradiance",
        "processes": {"solar": "generate-solar", "excavate": "excavate-mare", "beneficiate": "beneficiate-mare"},
        "yield": {"ice": 0, "ilmenite": 659, "silicate": 14981},
    },
}
DEPOSIT_KG = "1000000000"


def site_genesis(site: str) -> dict:
    """The genesis fields a site sets: its deposit and its environment."""
    s = SITES[site]
    return {"deposits": [{"deposit_id": s["deposit"], "remaining": {s["bank"]: DEPOSIT_KG}, "extracted": {}}],
            "environment": [s["environment"]]}


def alpha(lots: list[dict] | None = None, start_us: int | None = None, claim_id: str | None = None, site: str = "training") -> dict:
    """The alpha catalog with its genesis: a site's deposit and light (`SITES`; the training
    site by default) and, unless `lots` replaces them, a starter kit (solar array, excavator,
    hopper). Pass the result to Engine.create."""
    path = next((p for p in _CANDIDATES if p.exists()), None)
    if path is None:
        raise FileNotFoundError("alpha catalog not found: expected " + " or ".join(str(p) for p in _CANDIDATES))
    c = copy.deepcopy(load_create(path))
    c["genesis"].update(site_genesis(site))
    if lots is not None:
        c["genesis"]["starting_lots"] = lots
    if start_us is not None:
        c["genesis"]["start_us"] = str(start_us)
    if claim_id is not None:
        c["genesis"]["claim_id"] = claim_id
    return c


def lunar_day(create: dict) -> tuple[int, int]:
    """(dusk, period) in world microseconds, from the claim's light function: the sun
    shines from 0 to dusk, and the cycle repeats every period."""
    light = create["genesis"]["environment"][0]
    fn = next(f for f in create["catalog"]["environment"] if f["function_id"] == light)
    dark = next(s for s in fn["segments"] if q(s["value"]) == 0)
    return int(dark["start_us"]), int(fn["period_us"])


# The loop scenario (LD-PEC-006 §3.1): the alpha MVP's end-to-end test, as a named start.
LOOP_KITS = {"solar-kit": 1, "battery-kit": 1, "excavator-kit": 1, "hopper-kit": 1, "beneficiator-kit": 1, "ice-bin-kit": 1,
             "ilmenite-bin-kit": 1, "tailings-pile-kit": 1, "melter-kit": 1, "electrolyzer-kit": 1, "bulk-tank-kit": 3,
             "reactor-kit": 1, "iron-bin-kit": 1, "rutile-bin-kit": 1, "fabricator-kit": 1, "scrap-bin-kit": 1,
             "fuel-cell-kit": 1}

LOOP_GOAL = ("Install a hopper fabricated from the claim's own iron, then keep a consumer (any job that is not "
             "generate-solar, fuel-cell or fission) RUNNING through the lunar night, observed in its last day "
             "strictly before dawn.")


def loop_lots() -> list[dict]:
    """The loop's starting lots: one of every alpha kit the loop needs, 50 mol of water in a
    bulk tank, and two reactor kits whose fuel is in their cores (ten years at rated power,
    LD-PEC-006 §3.7). ld-web's "The loop" challenge starts the same way."""
    reactors = [("reactor-1mwe", "fission-reactor-kit", "4200000"), ("reactor-400kwe", "small-reactor-kit", "1700000")]
    return [
        {"lot_id": "kits", "location": "inventory", "state": "AVAILABLE", "composition": {},
         "products": {k: str(n) for k, n in LOOP_KITS.items()}},
        {"lot_id": "lander-water", "location": "water-store", "state": "AVAILABLE", "composition": {"h2o-l": "50000"}, "products": {}},
    ] + [{"lot_id": lid, "location": "inventory", "state": "AVAILABLE", "composition": {}, "products": {kit: "1"},
          "core": {"fuel-fresh": fuel}} for lid, kit, fuel in reactors]


def loop_setup() -> list[tuple[str, dict]]:
    """Commands to submit at time 0, before playing: install the tank the water came in."""
    return [("INSTALL", {"lot_id": "kits", "product": "bulk-tank-kit", "location": "pad-a", "as_id": "water-store"})]


def alpha_loop(claim_id: str | None = None) -> dict:
    """Create params for the loop scenario: the alpha catalog with `loop_lots()`. Submit
    `loop_setup()` after create. The goal is `LOOP_GOAL`; the scenario runs one lunar
    day, dawn to dawn (`lunar_day`)."""
    return alpha(lots=loop_lots(), claim_id=claim_id)

