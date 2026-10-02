# PROMPT 3 — Sheets migration (one-time backfill)

Copy everything below into Claude Code. Run this after Prompt 2. Build inside `second_deck_association_process/sda/db/` (as `migrate.py`, or a `sda/migrate/` package if it gets big).

---

## Goal

One-time import of the league's existing contract data from the Google Sheet **"SDA - Major League Contract Tracker"** into the DuckDB ledger as `contract_events` with `source='migration'`. After this, the Sheet becomes a read-only reference — the ledger is the system of record.

## Source data

Local CSV exports live at:

```
/Users/matthewpitlock/Development/SDACommishprocess/data/contractmigrationdata/
```

**One CSV per team tab is required**: Boe, Maloun, Hoffman, Pecora, C. Pelton, W. Pelton, Pitlock, Riggen, A. Rolain, M. Rolain. Export via Google Sheets: File → Download → Comma-separated values, switching tabs along the bottom. If any tab's CSV is missing, the script must fail loudly listing exactly which are absent — never silently migrate a partial league.

**Deliberately out of scope (built from scratch, not migrated):** the **Salary Cap Tracking**, **Estimated Free Agent Class**, and **League History** tabs. Cap tracking restarts at 78 years per team per season with future cap trades announced in Discord (Prompt 6); the free-agent projection is derived from `fa_year` (Prompt 5, Report 2); league history accumulates from the Fantrax API going forward (Prompt 7). Do not read those tabs at all.

## Column mapping (team tabs)

| Sheet column | → Ledger field / handling |
|---|---|
| Player Name | → match to `players.fantrax_id` via the Prompt-1 alias map. **Unmatched names go to an exceptions list, never guessed.** |
| Pos. | → `players.positions` |
| Free Agent Year | → `fa_year` |
| Move Type: Draft / Waiver | → note field; determines the contract-bounds rule that applied |
| Move Type: **DFA** | → **no contract event**: these rows were planning placeholders ("future MLB signings"), not deals. The row appears in the DFA/minors audit only. If `Dropped=Yes` on a DFA row, ignore it (there was never a contract to drop) and note that in the audit line. |
| Move Type: NULL / blank | → exceptions list for commissioner review |
| Legal / Legal - Minors | → informational only; the new system *computes* legality (Prompt 4), it doesn't import the flag |
| IL (Yes) | → cross-check against Fantrax Inj Res slots; record in note |
| Dropped (Yes) + Years value (e.g. 0.5) | → `DROPPED` event with the penalty years shown |
| Years (e.g. 1, 0.5) | → `years` for the SIGNED event being reconstructed |
| Year added | → the `ts` year for the reconstructed SIGNED event (use Jan 1 of that year if no exact date) |

Reconstruction logic per player row (non-minors rows only): one `SIGNED` event (years, fa_year, ts from Year added) plus, if Dropped=Yes, one `DROPPED` event (penalty years). `approved_by='migration'`, note=`migrated from <tab>`.

## Cap trades

Do NOT import historical cap trades — cap tracking starts fresh at 78 contract-years per team per season, and future cap trades are announced in Discord and recorded by the bot (Prompt 6). The ledger begins with zero `CAP_TRADE` events.

## League history

Do NOT seed from the tracker's League History tab. History accumulates from the Fantrax API going forward (Prompt 7).

## The reconciliation report (the real deliverable)

Before anything is committed to the ledger, print a report:

1. **Player match rate**: X/Y Sheet players matched to Fantrax IDs; every unmatched name listed with its tab.
2. **Orphans**: players on a Fantrax MLB roster with no Sheet row (they need contracts!), and Sheet players on no Fantrax roster.
3. **Cap sanity**: recomputed committed years per team from the migrated events — any team over 78 + IL relief is listed as a migration error (the ledger must start legal).
4. **DFA/minors audit**: every DFA row listed for spot-check (these rows produce no contract events — the audit is their only footprint).

The commissioner reviews this report and resolves the exceptions list. Only then does the script write to the ledger (gate it behind a `--commit` flag; default is dry-run printing the report).

## Acceptance criteria

- Dry run (default) prints the full reconciliation report and writes nothing.
- `--commit` writes exactly the reviewed events; re-running `--commit` is idempotent (won't double-import — key off a `migration_batch` marker in `note`).
- Missing-tab detection: fails loudly naming the absent tabs.
- pytest: DFA-rows-produce-no-events test, drop-penalty reconstruction test, idempotency test.
