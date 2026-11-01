"""Read helpers over an I-1 ClaimView. The underlying dict is `View.raw`."""
from __future__ import annotations

from fractions import Fraction


def q(x) -> Fraction:
    """An I-1 Rational ({n, d}) or decimal string as an exact Fraction."""
    if isinstance(x, dict):
        return Fraction(int(x["n"]), int(x["d"]))
    return Fraction(x)


class View:
    def __init__(self, raw: dict):
        self.raw = raw

    @property
    def time(self) -> int:
        return int(self.raw["time_us"])

    @property
    def jobs(self) -> list[dict]:
        return self.raw["jobs"]

    @property
    def machines(self) -> list[dict]:
        return self.raw["machines"]

    @property
    def buffers(self) -> list[dict]:
        return self.raw["buffers"]

    @property
    def lots(self) -> list[dict]:
        return self.raw["lots"]

    def job(self, job_id: str) -> dict | None:
        """A job in the state, or None: finished jobs leave it (LD-PEC-004 A2-1)."""
        return next((j for j in self.jobs if j["job_id"] == job_id), None)

    def jobs_for(self, process: str) -> list[dict]:
        return [j for j in self.jobs if j["process"] == process]

    def machine(self, machine_id: str) -> dict | None:
        return next((m for m in self.machines if m["machine_id"] == machine_id), None)

    def buffer(self, buffer_id: str) -> dict | None:
        return next((b for b in self.buffers if b["buffer_id"] == buffer_id), None)

    def amount(self, buffer_id: str, species: str) -> int:
        """Quantity of a species in a buffer, in the species' catalog unit (mmol for most)."""
        b = self.buffer(buffer_id)
        return int(b["contents"].get(species, "0")) if b else 0

    def free(self, buffer_id: str) -> int:
        """Capacity not yet filled, in the buffer's capacity unit, assuming one unit for all contents."""
        b = self.buffer(buffer_id)
        return int(b["capacity"]["amount"]) - sum(int(x) for x in b["contents"].values()) if b else 0

    def kits(self, product: str) -> int:
        """How many of a product are in the cargo hold."""
        return sum(int(lot["products"].get(product, "0")) for lot in self.lots if lot["location"] == "inventory")

    def kit_lot(self, product: str) -> str | None:
        """A hold lot holding the product, for INSTALL."""
        return next((lot["lot_id"] for lot in self.lots if lot["location"] == "inventory" and int(lot["products"].get(product, "0")) > 0), None)

    def running(self, job_id: str) -> bool:
        j = self.job(job_id)
        return bool(j) and j["status"] == "RUNNING"

    @property
    def power(self) -> tuple[Fraction, Fraction]:
        """Bus supply and demand, in the catalog's energy unit per microsecond."""
        e = self.raw["energy"]
        return q(e["supply"]), q(e["demand"])
