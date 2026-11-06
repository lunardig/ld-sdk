"""Python client for Lunar Dig engines over I-1 (LD-PEC-002 §5).

    from ld_client import Engine, alpha, S, MIN, HOUR, DAY
"""
from .engine import Engine, EngineError, Rejected
from .view import View, q
from .catalog import LOOP_GOAL, SITES, alpha, alpha_loop, load_create, loop_lots, loop_setup, lunar_day, site_genesis
from .units import S, MIN, HOUR, DAY
from .recipe import Recipe, recipe
from .savefile import (save_file, challenge, goal_stored, goal_stored_kg, goal_gain, goal_installed,
                       goal_running_between)

__all__ = ["Engine", "EngineError", "Rejected", "View", "q", "alpha", "SITES", "site_genesis", "alpha_loop", "loop_lots", "loop_setup", "LOOP_GOAL", "load_create", "lunar_day", "Recipe", "recipe", "S", "MIN", "HOUR", "DAY",
           "save_file", "challenge", "goal_stored", "goal_stored_kg", "goal_gain", "goal_installed", "goal_running_between"]
__version__ = "0.1.0a5"
