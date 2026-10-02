# PROMPT 6 — Discord bot (propose → validate → approve)

Copy everything below into Claude Code. Run this after Prompt 2 (it needs the ledger and `pending_contracts`). Build inside `second_deck_association_process/sda/discord_bot/`.

---

## Goal

A long-running `discord.py` bot that watches the league's announcements channel for multi-year contract signings, **validates each one against the league rules before anything is recorded**, and queues it for commissioner approval. Nothing reaches the ledger without passing validation AND approval.

## Configuration (all real values already known)

- Announcements channel ID: `1496593362135027963` → config key `discord_announcements_channel_id` (already in `config/season.yaml`).
- Bot token → `.env` as `DISCORD_BOT_TOKEN` (the commissioner holds it; never commit, never log it).
- The bot needs the **Message Content Intent** enabled in the Discord developer portal (already done).

## The flow

1. **Listen.** Watch the announcements channel for new messages.
2. **Parse.** The league's standard announcement format is:
   `📝 SIGNING: <player> — <N> years — <team>`
   Parse that strictly first; fall back to lenient free-text parsing (player name via the Prompt-1 alias map, a year count, a team name). If parsing fails, reply in-channel asking the manager to use the format — don't guess.
3. **Validate** (using the Prompt-4 rule functions — import them, don't reimplement):
   - Contract bounds for the acquisition type (drafted/called-up 1–7; in-season waiver/FA 1–3).
   - Cap room: team has space for the new years including IL relief and cap trades.
   - Roster limits (26 MLB / 15 minors max).
   - Re-sign tripwire: is this a same-team re-add of a dropped player with >3 years in-season? Reject outright.
4. **Queue.** Insert into `pending_contracts` with the raw message text, and post a **confirmation card** in the channel: parsed player/team/years + pass/fail per rule. Also write the parsed announcement (with its timestamp) to an `announcements` table — the 1-day signing rule keys off *announcement time*, not approval time.
5. **Approve.** The commissioner approves with a ✅ reaction on the card or `/contract approve <id>`; rejects with ❌ or `/contract reject <id>`. On approval → append the `SIGNED` event to the ledger (`source='discord'`, `approved_by` = commissioner) → trigger the report rebuild (call into `sda.reports.build`) → post the updated report link in the channel.
6. **One-day rule.** A separate lightweight loop (or a `/contract check` command) matches recent Fantrax adds against announcements: no announcement within 1 day of the add → append `DEFAULTED_1YR` (`source='system'`) and post a notice in the channel.

## Cap-trade announcements

Managers announce cap-space trades in the same announcements channel; the bot tracks them (cap tracking starts at 78 per team per season — no historical trades imported):

1. **Parse.** Strict format first:
   `📝 CAP TRADE: <from_team> sends <N> years to <to_team>`
   Fall back to lenient parsing (team names via config/`teams` table, a year count which may be fractional, e.g. 2.5). If parsing fails, reply asking for the format — don't guess.
2. **Validate.** Both teams resolve to known `team_id`s; N is positive and finite. (No cap-room check on the *sending* team — trading away space is always legal; it just reduces their effective cap.)
3. **Queue + confirm.** Insert into `pending_contracts` (with a `kind='cap_trade'` marker or equivalent — extend the table if needed) and post a confirmation card: from → to, years, each team's resulting effective cap.
4. **Approve.** Same ✅/❌ flow as signings. On approval → append **two** `CAP_TRADE` events: `-N` years on the sending team, `+N` years on the receiving team (`source='discord'`, `approved_by` = commissioner) → rebuild reports → post the updated cap-tracker link.

## Robustness requirements

- **Startup catch-up:** on launch, scan channel history since the last processed message ID (persist it in the DB). The laptop sleeps — nothing may be lost or double-processed.
- **Single-writer discipline:** respect the `data/.db.lock` from Prompt 2; never write while the nightly pipeline holds it.
- **Dry-run mode:** `--dry-run` validates and posts nothing, writes nothing. The commissioner will test with this first.
- **Slash-command v1 is acceptable:** if free-text parsing proves flaky, ship `/contract propose player: years: team:` first and add free-text later. Say which you shipped in the README section.

## Hosting note (for the README)

The bot runs on the commissioner's Mac via `launchd` (auto-start on boot). Document the plist setup. It is NOT serverless — GitHub Actions can't do this.

## What to build

- `sda/discord_bot/bot.py` — the client, intents, event handlers.
- `sda/discord_bot/parser.py` — strict + lenient announcement parsing (pure functions, fully tested).
- `sda/discord_bot/approvals.py` — reaction/slash-command handling, ledger append on approval.
- CLI: `python -m sda.discord_bot --dry-run`.

## Acceptance criteria

- Parser tests: the exact `📝 SIGNING:` format, three realistic free-text variants, and three malformed messages (which must NOT parse). Same coverage for the `📝 CAP TRADE:` format (including a fractional-years case).
- End-to-end dry run: a fake announcement for a player that would break the cap → confirmation card shows the cap rule failing → nothing written to the ledger or `pending_contracts` beyond the queued row.
- Restart test: kill mid-queue, restart, no duplicate announcements processed.
- The token appears nowhere in logs, the DB, or the repo (test asserts this).
