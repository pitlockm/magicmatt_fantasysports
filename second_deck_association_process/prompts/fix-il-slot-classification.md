# PATCH — IL classification: status enum IS the Fantrax slot (verified)

Copy everything below into Claude Code. Run against the implemented `sda/fantrax/normalize.py`. Build inside `second_deck_association_process/`.

---

## Verified payload shape (from the live `data/raw/<date>/team_rosters.json`)

Each `rosterItem` contains exactly three fields:

- `id` — Fantrax player ID
- `position` — the baseball position (1B, OF, SP, …). **Not** the roster slot.
- `status` — one of `ACTIVE`, `MINORS`, `RESERVE`, `INJURED_RESERVE` (case may vary).

There is no separate `rosterSlot`/`slot`/`fantasyStatus`/`rosterStatus` field.

## The ruling (binding, constitution §2.4 + commissioner)

**The `status` field IS the manager's Fantrax roster-slot assignment — not real-life injury status.**
`ACTIVE`/`RESERVE`/`MINORS` are fantasy placements (a real-life status is never "RESERVE"); by parallel structure, `INJURED_RESERVE` is the fantasy IR slot, i.e. the manager moved the player there. (Fantrax may validate IR *eligibility* against the real-life IL, but the *placement* is a manager action — which is exactly what the slot-based rule keys on.)

Classification — case-insensitive **exact** match on `status`:

| status | roster_level |
|---|---|
| `MINORS` | `minors` |
| `INJURED_RESERVE` | `IL` |
| `ACTIVE`, `RESERVE` | `MLB` |
| missing / unrecognized | `MLB` + warning log (never crash) |

Rules:
- A real-life-IL player the manager keeps active/bench shows `ACTIVE`/`RESERVE` → **MLB, not IL**. (Sanity check you can run: pick a known real-life-IL player left in an active slot and confirm his `status` is `ACTIVE`.)
- Minor leaguers can never be IL (constitution §2.4: "Only players officially placed on the MLB Injured List are eligible"). With a single enum this can't collide, but keep the guard.
- `il_slots_used` = count of `INJURED_RESERVE` entries.
- `position` feeds the player's positions info only — never slot inference.
- Replace the fuzzy IL label-matching (`"injured reserve"`/`"injured list"`/`il` regexes over status text) as the primary path. Keep the old heuristics **only** as a fallback for unrecognized payload shapes, logging a warning whenever the fallback fires — a clean enum must never be overruled by fuzzy text.
- Document the enum and the ruling in a comment/docstring citing constitution §2.4.

## Tests to add/update

- `status: INJURED_RESERVE` → `roster_level == "IL"`, counted in `il_slots_used`.
- `status: ACTIVE` → `"MLB"` (even if the player is known to be on the real-life IL — the payload doesn't carry that, and that's the point).
- `status: MINORS` → `"minors"`, never `"IL"`.
- `status: RESERVE` → `"MLB"`.
- Unknown/missing status → `"MLB"` + no crash.
- No fuzzy status text influences the result when the enum is present (e.g. a `status` of `ACTIVE` with an injury-ish note elsewhere still classifies MLB).

## Acceptance criteria

- `pytest` passes, including the new enum tests.
- `grep` confirms no injury-label regex decides IL classification when `status` holds a recognized enum value.
