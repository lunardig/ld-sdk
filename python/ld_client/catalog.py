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


def alpha(lots: list[dict] | None = None, start_us: int | None = None, claim_id: str | None = None) -> dict:
    """The alpha catalog with its genesis: the icy crater deposit and, unless `lots` replaces
    them, a starter kit (solar array, excavator, hopper). Pass the result to Engine.create."""
    path = next((p for p in _CANDIDATES if p.exists()), None)
    if path is None:
        raise FileNotFoundError("alpha catalog not found: expected " + " or ".join(str(p) for p in _CANDIDATES))
    c = copy.deepcopy(load_create(path))
    if lots is not None:
        c["genesis"]["starting_lots"] = lots
    if start_us is not None:
        c["genesis"]["start_us"] = str(start_us)
    if claim_id is not None:
        c["genesis"]["claim_id"] = claim_id
    return c


def lunar_day(create: dict) -> tuple[int, int]:
    """(dusk, period) in world microseconds, from the catalog's irradiance function: the
    sun shines from 0 to dusk, and the cycle repeats every period."""
    fn = next(f for f in create["catalog"]["environment"] if f["function_id"] == "irradiance")
    dark = next(s for s in fn["segments"] if q(s["value"]) == 0)
    return int(dark["start_us"]), int(fn["period_us"])
