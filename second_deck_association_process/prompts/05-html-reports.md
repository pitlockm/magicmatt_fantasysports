# PROMPT 5 — HTML reports (read-only; separate pages, one site)

Copy everything below into Claude Code. Run this after Prompts 2 and 4. Build inside `second_deck_association_process/sda/reports/`.

---

## Goal

Generate a **static HTML site** (Jinja2 templates, no JS framework, no backend) that replaces the Google Sheet as what league members look at. Five primary report pages plus supporting views, all built from the DuckDB ledger + Fantrax snapshots. Output goes to `second_deck_association_process/site/` (gitignored build dir; the pipeline commits it for GitHub Pages).

Design: clean, mobile-friendly, one shared CSS file, top nav linking the five primary reports. Client-side table sorting/filtering (small vanilla-JS utilities) where noted.

## The five primary reports

### Report 1 — Contracts and cap grid (the centerpiece)
Reproduces the spreadsheet's visualization: **teams × the next 7 seasons**, each cell showing cap totals with expandable details. Merge the old Contracts grid and Salary Cap Tracker; do not render a separate cap-tracker page. Requirements:
- Includes **dead cap**: dropped multi-year players whose penalties still hit the cap, shown as a separate line within each cell (e.g. `64.5 + 2.5 dead`).
- Expand a nonzero dead-cap line to list the dropped player, position, remaining dead-cap amount, and FA year. An empty dead-cap list remains explicit.
- Show remaining cap, effective cap, IL relief, and cap-trade adjustment in the cell.
- Provide expandable lists for multi-year contracts, one-year contracts, cap-trade events, and injured-list players. Current-season IL names come from the saved roster snapshot; future IL rosters are not projected and must be labeled unavailable.
- One-year official deals count toward cap even though they are not migrated from the previous season.
- **Minor leaguers are a separate section below the grid** — never inside the contract area. Group them in one expandable roster per team, listing each player's name and position from the current saved Fantrax snapshot. Age-by-year is deferred until bio coverage is verified/populated; when added, derive age from birthdate as of July 1 of each grid year. Future roster ownership is not projected.
- Each team's row also shows remaining space: `78 + IL relief − team_cap_committed(team, season)` (the ledger function already nets cap-trade adjustments).

### Report 2 — Free-agent projection
One long, sortable/filterable table across FA years, with player, team, position, age, latest ADP, FA year, and last-completed-season stats. Hitters show R, OBP, HR, SB; pitchers show K, ERA, WHIP, QS, SV/Holds. Non-applicable cells are blank. Remove redundant "Signed through" and "Deal" columns. Filters: FA year, position, team, and text search. Display the ADP snapshot date and completed-stats season; missing data renders as "—".
- **Stats source:** join by Fantrax player ID. Use a documented Fantrax API source if one is confirmed; otherwise accept an official Fantrax stats CSV with a Fantrax ID column. Select the latest completed season not after the requested stats season. Current ADP is a separate Fantrax `getAdp` snapshot and must not be substituted for historical stats.
- **FA-year formula (binding):** `fa_year` on the contract event is the *first year the player is a free agent* = signing season + contract years (no minus one). A 3-year deal signed in 2026 means team control for 26/27/28 → FA 2029.

### Report 3 — Waiver wire
Top 10 **highest-rostered players currently on waivers** (recently dropped — not free agents). Fewer than 10 is fine; show what's there. Columns: player, position, dropped-by team, days on waivers, roster%.
- Data source: confirm during build — first try the player-pool info in `getLeagueInfo`; fallback is the Fantrax players-page CSV export filtered by waiver status. Document whichever works in a code comment.

### Report 4 — Standings
Sortable table: every team with its **category totals and category points** (R, HR, RBI, SB, OBP / QS, K, ERA, WHIP, SVH). Click any column header to sort. Source: `getStandings` + `getMatchupScores`.

### Report 5 — League history (built from scratch)
Two sections, both from the history tables (Prompt 2 patch) — never from the old Sheet:
- **All-time leaderboard**: one row per team — seasons played, all-time W-L-T, championships, runner-ups, regular-season 1sts, playoff appearances (from the `team_all_time_history` view). Sortable.
- **Yearly records**: one row per season — champion, runner-up, regular-season best record, prize winnings (from `league_history` + `team_season_history`).

The old Report 6 — Salary cap tracker — is merged into Report 1 and is not a separate page.

## Supporting views
- **Contract ledger**: the append-only event log, newest first — the audit trail.
- **Validation report**: the Prompt-4 engine output rendered per team (pass/fail with details).
- **Transaction log**: Fantrax transaction history (CSV-fallback is fine for v1).

## What to build

- `sda/reports/build.py` — `build_site(season)` → renders everything into `site/`. One function per report (`report_multiyear_grid(...)`, …), each taking plain data structures (not DB handles) so they're testable.
- `sda/reports/templates/` — one Jinja2 template per primary/audit page + `base.html` (nav, CSS link); no separate cap-tracker page.
- `sda/reports/static/style.css` — single stylesheet, mobile-first.
- CLI: `python -m sda.reports --season 2027`.

## Constraints

- **No secrets in the output.** The build must never embed the Discord token, `userSecretId`, or anything from `.env`. Add a test that scans `site/` for those values.
- Pure static output: the site must work opened from `file://` with no server (GitHub Pages serves it as-is).

## Acceptance criteria

- `python -m sda.reports --season 2027` builds the full site from a **fixture DB** (ship one under `tests/fixtures/`) — all five primary reports + supporting views render with no errors.
- The multi-year grid fixture must include: a dead-cap case, a cap-trade adjustment, and an IL-relief case, all visibly correct in the output.
- The history fixture must include 2+ seasons so the all-time leaderboard aggregates visibly (championships, runner-ups, playoff appearances).
- The Contracts grid fixture must show expandable one-year/multi-year deals, a dropped player with dead cap and position, cap trades, current-season IL players, and team-grouped minors with position and per-year age, with effective cap, committed cap, and remaining space computed correctly.
- No-sorcery test: grep the built site for the secret values — must find nothing.
