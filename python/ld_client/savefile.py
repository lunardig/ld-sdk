"""Save files the web game (lunardig.com) can load: Saved games, then Import a file.

A save is the engine's snapshot plus the catalog, with a little bookkeeping for the game
(`ld-web.save.v0`). It may carry a challenge (`ld-web.challenge.v0`): a story, goals and
a deadline the game shows in its Goal tab. docs/save-format.md describes both formats.

    from ld_client import Engine, alpha, save_file, challenge, goal_stored
    ...build a claim with e.submit and e.advance_by...
    doc = save_file(e, create, challenge=challenge("dry-crater", "Dry crater", story, brief,
                                                   goals=[goal_stored("h2o-l", 50)]))
    Path("dry-crater.json").write_text(json.dumps(doc))
"""
from __future__ import annotations

import re

SAVE_FORMAT = "ld-web.save.v0"
CHALLENGE_FORMAT = "ld-web.challenge.v0"


# ---------------------------------------------------------------- goals
def goal_stored(species: str, mol: float) -> dict:
    """At least `mol` moles of a substance in storage, all buffers together (e.g. "h2-g")."""
    return {"kind": "stored", "species": species, "mol": mol}


def goal_stored_kg(species: str, kg: float) -> dict:
    """At least `kg` of a bulk substance counted per kilogram, such as "regolith"."""
    return {"kind": "storedKg", "species": species, "kg": kg}


def goal_gain(credits: float) -> dict:
    """Net worth at least `credits` above its value when the challenge starts. Prices are
    the game's appraisal table (credits per mole or per kit), not part of the engine."""
    return {"kind": "gain", "credits": credits}


def goal_installed(template: str, count: int, label: str) -> dict:
    """At least `count` buffers of a store template installed, such as "hopper"."""
    return {"kind": "installed", "template": template, "count": count, "label": label}


def goal_running_between(from_us: int, to_us: int, label: str) -> dict:
    """A power-using machine's job RUNNING at some moment in [from_us, to_us) of world time.
    Once seen, it stays met."""
    return {"kind": "runningBetween", "from_us": str(from_us), "to_us": str(to_us), "label": label}


# ---------------------------------------------------------------- challenge
def challenge(id: str, title: str, story: str, brief: str, goals: list[dict], *, deadline_us: int | None = None,
              lesson: str | None = None, more: str | None = None, author: str | None = None) -> dict:
    """A challenge for the game's Goal tab. `deadline_us` is claim time allowed from when the
    save was made; None leaves it untimed. Goals come from the goal_* helpers."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", id):
        raise ValueError("challenge id: 1 to 64 letters, digits and hyphens")
    if not 1 <= len(goals) <= 12:
        raise ValueError("a challenge needs between 1 and 12 goals")
    if deadline_us is not None and deadline_us <= 0:
        raise ValueError("deadline_us must be positive")
    c = {"format": CHALLENGE_FORMAT, "id": id, "title": title, "story": story, "brief": brief, "goals": goals,
         "deadline_us": None if deadline_us is None else str(deadline_us)}
    for k, v in (("lesson", lesson), ("more", more), ("author", author)):
        if v is not None:
            c[k] = v
    return c


# ---------------------------------------------------------------- save file
def save_file(engine, create: dict, challenge: dict | None = None) -> dict:
    """The claim `engine` holds now, as an ld-web save file (a dict; write it as JSON).

    `create` is what the claim was created from (its catalog goes into the save, and its
    genesis start time anchors the game's clock). The game loads the snapshot into its own
    copy of the engine, so the claim continues exactly where the engine left it."""
    snap = engine.snapshot()
    ui: dict = {
        # The game numbers its own commands ui-0001, ui-0002...; the SDK's are cmd-00001...,
        # so they never collide. Its seq must continue past every command already applied.
        "next_command": 1,
        "next_seq": str(engine._seq + 1),
        "start_us": str(create["genesis"].get("start_us", "0")),
    }
    if challenge is not None:
        ui["meta"] = {"challenge": challenge}
    return {
        "format": SAVE_FORMAT,
        "saved_at_claim_time_us": str(snap["state"]["time_us"]),
        "snapshot": snap,
        "catalog": create["catalog"],
        "ui": ui,
    }
