"""Python client for Lunar Dig engines over I-1 (LD-PEC-002 §5).

    from ld_client import Engine, alpha, S, MIN, HOUR, DAY
"""
from .engine import Engine, EngineError, Rejected
from .view import View, q
from .catalog import alpha, load_create, lunar_day
from .units import S, MIN, HOUR, DAY

__all__ = ["Engine", "EngineError", "Rejected", "View", "q", "alpha", "load_create", "lunar_day", "S", "MIN", "HOUR", "DAY"]
__version__ = "0.1.0a1"
