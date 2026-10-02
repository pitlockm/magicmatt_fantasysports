# PATCH — IL classification must be slot-based, never status-based

Copy everything below into Claude Code. Run this against the already-implemented `sda/fantrax/normalize.py`. Build inside `second_deck_association_process/`.

---

## Context

`sda/fantrax/normalize.py` currently classifies a roster entry as IL when **either** its real-life injury status **or** its Fantrax roster slot looks IL-ish (`_is_il_slot(status, roster_slot)` inspects `status`/`rosterStatus`/`fantasyStatus` as well as slot fields).

Commissioner ruling (binding), grounded in constitution §2.4:

- A player counts as **on the IL iff the manager placed him in a Fantrax IL roster slot** — a manager action. A player on the real-life MLB injured list who sits in an active/bench slot counts as **MLB, not IL** (managers sometimes choose not to use the slot).
- **Minor-league players can never be classified IL.** Constitution §2.4: "Only players officially placed on the MLB Injured List are eligible." The current code already gives minors precedence over IL — keep that.
- Real-life injury status is used only for **eligibility validation** (Prompt 4), never for classification.

## What to change

1. **Verify the live payload shape first.** Open the latest `data/raw/<date>/team_rosters.json` snapshot and confirm which fields reflect the manager's slot assignment (e.g. `rosterSlot`/`slot`/`position`) versus the player's real-life status (e.g. `status`/`rosterStatus`/`fantasyStatus`). If the slot cannot be distinguished from status in the actual payload, **stop and report the field shapes** instead of guessing.
2. In `normalize.py`, determine IL **only** from the Fantrax roster-slot fields. Remove `status`-family fields from the IL determination entirely.
3. Keep the precedence order: **minors > IL > MLB**. A minor leaguer is never IL, even with IL-ish markers.
4. `il_slots_used` counts slot-based IL placements only.
5. Document the rule in a comment/docstring citing constitution §2.4.

## Tests to add/update

- Real-life-IL player sitting in an active slot → `roster_level == "MLB"`, not counted in `il_slots_used`.
- Player in a Fantrax IL slot → `roster_level == "IL"`, counted.
- Minor leaguer with IL-ish markers → `"minors"`, never `"IL"`.
- Entry with no recognizable slot → classify `"MLB"` and log a warning, never crash.

## Acceptance criteria

- `pytest` passes, including the new IL tests.
- No `status`/`rosterStatus`/`fantasyStatus` field influences IL classification anywhere in the module (grep to confirm).
