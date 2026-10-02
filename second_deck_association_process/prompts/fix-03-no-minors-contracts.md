# PATCH — No contracts for minor leaguers (DFA planning rows are not deals)

Copy everything below into Claude Code. Run against the implemented `sda/db/` (`ledger.py`, `schema.py`, `migrate.py`) and `sda/validation/` code. Build inside `second_deck_association_process/`.

---

## The ruling (binding, constitution: "Minor league players do not receive contract years until they are added to your Major league roster")

The old tracker's DFA rows with "Player Contract Years" were a **planning tool** ("future MLB signings"), not deals. A player gets a contract **only** when called up to an active MLB slot *and* a multi-year deal is announced (the `CALLED_UP` event). There is no production data yet, so no data migration is needed — only code changes.

## 1. Migration (`sda/db/migrate.py`)

- Rows whose Move Type maps to minors (`DFA` / `minors` / `draft minors`): create **no contract events at all** — no `SIGNED`, and no `DROPPED` even if `Dropped=Yes` (there was never a contract to drop; note that in the minors-audit line, not the exceptions list).
- These rows still appear in the **DFA/minors audit** (planning visibility) and still count toward `matched_player_rows` when the name resolves.
- `commit_migration`: raise `ValueError` if any planned event has `event_type in ('SIGNED', 'EXTENDED')` with `roster_level='minors'` — defense in depth.
- Update tests: the DFA test now asserts **zero** events for a DFA row (audit entry present, no cap impact).

## 2. Ledger (`sda/db/schema.py`, `sda/db/ledger.py`)

- `current_contracts` view: exclude planning placeholders — add `AND roster_level <> 'minors'` to the final `WHERE`. Rationale: the filter keys on the event's level **at signing**. A player signed at MLB level then demoted still counts (constitution 4.2); a `SIGNED` recorded at minors level never counts. (`CREATE OR REPLACE VIEW` keeps this idempotent.)
- `_validate_event`: reject `SIGNED`/`EXTENDED` with `roster_level='minors'` → `ValueError("Minor leaguers cannot hold contracts; use CALLED_UP when promoted")`. `CALLED_UP` carries `roster_level='MLB'` (the schema default).
- `team_cap_committed` needs no change if it reads the view — verify it does, and that no raw `contract_events` cap query bypasses the view. The validation engine duplicates this SQL: apply the same view-based read there (or the identical filter) so both stay consistent.

## 3. Validation (`sda/validation/`)

- V2 loop: skip events with `roster_level='minors'` (they cannot exist going forward; this is belt-and-suspenders for any legacy rows).
- V8's demoted-player logic is unchanged — demoted players' `SIGNED` events are MLB-level, so they still count. Add a test proving a demoted-but-contracted player remains in the cap total.

## Tests

- Ledger: `SIGNED` with `roster_level='minors'` raises; `team_cap_committed` excludes a minors-level contract but includes a demoted player's MLB-level deal.
- Migration: DFA row → zero events, audit entry, no cap impact; `--commit` still idempotent.
- Validation: V2 ignores a legacy minors-level event; V1 cap unchanged by a minors placeholder.

## Acceptance criteria

- `pytest` passes (updated + new tests).
- `grep` for `roster_level='minors'` event creation in `migrate.py` finds none.
- The `current_contracts` view definition contains the minors exclusion.
