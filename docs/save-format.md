# Save files and challenges for the web game

The web game at lunardig.com saves a claim as a JSON file and can load one back:
**Saved games**, then **Import a file**. A program built on this SDK can write the same
file, so anything you can build with the engine (a half-finished plant, a broken one, a
claim at dusk with the night still to plan) can be handed to a player to continue in the
game. A save may also carry a **challenge**: a story, goals and a deadline that the game
shows in its Goal tab.

`ld_client.save_file` writes both formats; `examples/make_scenario.py` is a complete
example. This page is the reference.

**Alpha.** Both formats are version 0 and carry no compatibility promise. A save loads in
the game when the game's engine accepts its snapshot and catalog; if the engine or the
catalog changes in a way the snapshot cannot express, old saves stop loading. Say which
SDK release a scenario was made with.

## The save file: `ld-web.save.v0`

```json
{
  "format": "ld-web.save.v0",
  "saved_at_claim_time_us": "3600000000",
  "snapshot": { "...": "the engine's snapshot result, unchanged" },
  "catalog": { "...": "the catalog bundle the claim was created with" },
  "ui": {
    "next_command": 1,
    "next_seq": "29",
    "start_us": "0",
    "meta": { "challenge": { "...": "optional, below" } }
  }
}
```

| Field | Meaning |
| --- | --- |
| `format` | Exactly `ld-web.save.v0` |
| `saved_at_claim_time_us` | The claim time of the snapshot, as a decimal string |
| `snapshot` | The `snapshot` result, as the engine returned it. The game restores it with `restore`, which checks its hashes: an edited snapshot does not load |
| `catalog` | The catalog bundle from the claim's `create` parameters |
| `ui.next_command` | The number the game gives its next command id (`ui-0001`, …). The SDK client's ids are `cmd-00001`, …, so `1` never collides |
| `ui.next_seq` | The `seq` for the game's next command: one more than the last command applied |
| `ui.start_us` | The genesis start time; the game's clock shows time since it ("Day 1, 01:00:00") |
| `ui.meta` | Optional. `challenge` (below), or `campaign`, which the game writes for its own scenarios and nothing else should |

The game reads only the snapshot's claim; it does not replay commands. Whatever state the
engine was in when you took the snapshot is where the player starts.

## The challenge: `ld-web.challenge.v0`

```json
{
  "format": "ld-web.challenge.v0",
  "id": "ice-jam",
  "title": "Ice jam",
  "story": "The night shift left the mine running...",
  "brief": "Store 10,000 mol of water within two hours.",
  "goals": [{ "kind": "stored", "species": "h2o-l", "mol": 10000 }],
  "deadline_us": "7200000000",
  "lesson": "A jam travels backwards...",
  "more": "Background for the curious.",
  "author": "Your name"
}
```

| Field | Meaning |
| --- | --- |
| `id` | 1 to 64 letters, digits and hyphens |
| `title`, `story`, `brief` | Required text: the name, the situation, and what the player must do |
| `goals` | 1 to 12 goals, all of which must be met (below) |
| `deadline_us` | Claim time allowed, in µs from when the challenge begins, or `null` for none. The clock cannot advance past it |
| `lesson`, `more`, `author` | Optional: the idea it teaches (shown on completion), background text, and a credit line |
| `begin_us`, `baseline_worth` | Leave these out. The game fills them in on first load (the save's claim time and the claim's net worth then) and keeps them in later saves |

**Goals.** Each is one of these; amounts are JSON numbers, times decimal strings of µs.

| `kind` | Fields | Met when | Helper |
| --- | --- | --- | --- |
| `stored` | `species`, `mol` | At least `mol` moles of the substance are in storage, all buffers together | `goal_stored` |
| `storedKg` | `species`, `kg` | At least `kg` of a bulk substance counted per kilogram (`regolith`) are in storage | `goal_stored_kg` |
| `gain` | `credits` | Net worth has risen by `credits` since the challenge began | `goal_gain` |
| `installed` | `template`, `count`, `label` | At least `count` buffers of a store template are installed (`hopper`) | `goal_installed` |
| `runningBetween` | `from_us`, `to_us`, `label` | A power-using machine's job is RUNNING at some moment in [`from_us`, `to_us`) of world time. Once seen, it stays met | `goal_running_between` |

Species and templates are the catalog's ids (`docs/alpha-catalog.md`). Net worth uses the
game's appraisal table, which belongs to the game, not the catalog or the engine.

**What a challenge cannot carry.** The campaign's guided steps, callouts and "Show me"
are code in the game and cannot be imported. A malformed challenge is refused when the
save is loaded, with the reason. Imported challenges do not count toward the campaign's
progress or score.

**Try again.** For an imported challenge, the game's Try again loads the same file again.

## Making one

```python
import json
from pathlib import Path
from ld_client import Engine, alpha, save_file, challenge, goal_stored, HOUR

create = alpha(lots=[...])                       # what the player starts with
with Engine("engine/linux-x86_64/ld-engine-cli") as e:
    e.create(**create)
    ...                                          # build, run, let trouble happen
    ch = challenge("ice-jam", "Ice jam", story, brief, goals=[goal_stored("h2o-l", 10_000)], deadline_us=2 * HOUR)
    Path("ice-jam.json").write_text(json.dumps(save_file(e, create, challenge=ch)))
```

Prove a challenge can be won before you share it: restore the saved snapshot in a fresh
engine and play it to the goals, as `examples/make_scenario.py --solve` does. The engine
is deterministic, so a winning command list for your file wins in the game too.
