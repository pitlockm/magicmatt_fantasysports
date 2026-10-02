# PROMPT 5 — HTML reports (read-only; separate pages, one site)

Copy everything below into Claude Code. Run this after Prompts 2 and 4. Build inside `second_deck_association_process/sda/reports/`.

---

## Goal

Generate a **static HTML site** (Jinja2 templates, no JS framework, no backend) that replaces the Google Sheet as what league members look at. Six separate report pages plus supporting views, all built from the DuckDB ledger + Fantrax snapshots. Output goes to `second_deck_association_process/site/` (gitignored build dir; the pipeline commits it for GitHub Pages).

Design: clean, mobile-friendly, one shared CSS file, top nav linking the six reports. Client-side table sorting (tiny vanilla-JS snippet) where noted.

## The six reports

### Report 1 — Multi-year grid (the centerpiece)
Reproduces the spreadsheet's visualization: **teams × the next 7 seasons**, each cell showing committed cap years, with drill-down to the players behind the number. Requirements:
- Includes **dead cap**: dropped multi-year players whose penalties still hit the cap, shown as a separate line within each cell (e.g. `64.5 + 2.5 dead`).
- **Minor leaguers are a separate section below the grid** — never inside the contract area. Each minor leaguer shows: name, age (from `players.birthdate`), career MLB AB / IP (from `players.career_ab` / `career_ip`), and owning team — so managers can project MLB arrival. Unknown bio fields render as "—".
- Per the commissioner: contract views cover **multi-year players only**; 1-year/expiring players don't get future rows.
- Each team's row also shows remaining space: `78 + IL relief − team_cap_committed(team, season)` (the ledger function already nets cap-trade adjustments).

### Report 2 — Free-agent projection
FA class by year (2027, 2028, …): which multi-year deals expire when, grouped by team, expiring stars highlighted. This is where 1-year/expiring players live.
- **FA-year formula (binding):** `fa_year` on the contract event is the *first year the player is a free agent* = signing season + contract years (no minus one). A 3-year deal signed in 2026 means team control for 26/27/28 → "signed through 2028 — FA 2029". Display both halves: "through {fa_year − 1}" and "FA in {fa_year}".

### Report 3 — Waiver wire
Top 10 **highest-rostered players currently on waivers** (recently dropped — not free agents). Fewer than 10 is fine; show what's there. Columns: player, position, dropped-by team, days on waivers, roster%.
- Data source: confirm during build — first try the player-pool info in `getLeagueInfo`; fallback is the Fantrax players-page CSV export filtered by waiver status. Document whichever works in a code comment.

### Report 4 — Standings
Sortable table: every team with its **category totals and category points** (R, HR, RBI, SB, OBP / QS, K, ERA, WHIP, SVH). Click any column header to sort. Source: `getStandings` + `getMatchupScores`.

### Report 5 — League history (built from scratch)
Two sections, both from the history tables (Prompt 2 patch) — never from the old Sheet:
- **All-time leaderboard**: one row per team — seasons played, all-time W-L-T, championships, runner-ups, regular-season 1sts, playoff appearances (from the `team_all_time_history` view). Sortable.
- **Yearly records**: one row per season — champion, runner-up, regular-season best record, prize winnings (from `league_history` + `team_season_history`).

### Report 6 — Salary cap tracker (built from scratch)
Replaces the old "Salary Cap Tracking" tab — computed, never hand-typed. Per team per season: base cap (78), + IL relief (# players in Fantrax IL slots), ± net cap trades, = effective cap; committed years (active + dead cap); remaining space. Historical cap trades are NOT imported — tracking starts at 78 for everyone, and Discord-announced trades (Prompt 6) accumulate from there.

## Supporting views
- **Contract ledger**: the append-only event log, newest first — the audit trail.
- **Validation report**: the Prompt-4 engine output rendered per team (pass/fail with details).
- **Transaction log**: Fantrax transaction history (CSV-fallback is fine for v1).

## What to build

- `sda/reports/build.py` — `build_site(season)` → renders everything into `site/`. One function per report (`report_multiyear_grid(...)`, …), each taking plain data structures (not DB handles) so they're testable.
- `sda/reports/templates/` — one Jinja2 template per page + `base.html` (nav, CSS link).
- `sda/reports/static/style.css` — single stylesheet, mobile-first.
- CLI: `python -m sda.reports --season 2027`.

## Constraints

- **No secrets in the output.** The build must never embed the Discord token, `userSecretId`, or anything from `.env`. Add a test that scans `site/` for those values.
- Pure static output: the site must work opened from `file://` with no server (GitHub Pages serves it as-is).

## Acceptance criteria

- `python -m sda.reports --season 2027` builds the full site from a **fixture DB** (ship one under `tests/fixtures/`) — all six reports + supporting views render with no errors.
- The multi-year grid fixture must include: a dead-cap case, a cap-trade adjustment, and an IL-relief case, all visibly correct in the output.
- The history fixture must include 2+ seasons so the all-time leaderboard aggregates visibly (championships, runner-ups, playoff appearances).
- The cap-tracker fixture must show a team with a cap trade and a team with IL relief, with remaining space computed correctly.
- No-sorcery test: grep the built site for the secret values — must find nothing.
