# PROMPT 6 — Discord bots: signings watcher + cap-trade watcher (propose → validate → approve)

Copy everything below into Claude Code. Run this after Prompt 2 (it needs the ledger and `pending_contracts`). Build inside `second_deck_association_process/sda/discord_bot/`.

---

## Goal

**Two** long-running `discord.py` bots sharing one codebase (`sda/discord_bot/`), each watching its own announcements channel. Both **validate each announcement against the league rules before anything is recorded**, and queue it for commissioner approval. Nothing reaches the ledger without passing validation AND approval.

- **Bot 1 — Signing bot.** Watches the multi-year signings channel for contract announcements.
- **Bot 2 — Cap-trade bot.** Watches a **new, dedicated** salary-cap-trade announcements channel.

Two bots (not one) because the channels, message formats, and ledger effects are fully independent — one bot going down must not blind the other channel.

## Configuration (all real values already known)

- Signing announcements channel ID: `1496593362135027963` → config key `discord_announcements_channel_id` (already in `config/season.yaml`).
- Cap-trade announcements channel ID: **TBD — the commissioner will create this channel and add its ID** → config key `discord_captrade_channel_id` (add to `config/season.yaml`; the bot must fail fast with a clear error if it is missing).
- Bot tokens → `.env` as `DISCORD_SIGNING_BOT_TOKEN` and `DISCORD_CAPTRADE_BOT_TOKEN` (the commissioner holds both; never commit, never log them).
- Both bots need the **Message Content Intent** enabled in the Discord developer portal.

## Commissioner prerequisites (do these before running the bots)

1. In the league's Discord server, create a new channel (suggested name `#salary-cap-trades`) for cap-trade announcements. Copy its channel ID → `discord_captrade_channel_id` in `config/season.yaml`.
2. In the Discord developer portal, create a **second** bot application for the cap-trade watcher. Enable Message Content Intent, copy its token → `DISCORD_CAPTRADE_BOT_TOKEN` in `.env`, and invite it to the server with read/send permission **only** in the new cap-trade channel.
3. Confirm the existing signing bot's token is in `.env` as `DISCORD_SIGNING_BOT_TOKEN` (rename from the old `DISCORD_BOT_TOKEN`).

## Shared infrastructure (both bots)

- One package, two entry points: `python -m sda.discord_bot.signing` and `python -m sda.discord_bot.captrade` (each also accepts `--dry-run`).
- Shared modules: `common.py` (client setup, intents, startup catch-up, single-writer lock handling), `parser.py` (pure parsing functions, fully tested), `approvals.py` (reaction/slash-command handling, ledger append on approval).
- **Startup catch-up (per bot):** on launch, each bot scans its own channel's history since its own last processed message ID (persist each bot's watermark separately in the DB). The laptop sleeps — nothing may be lost or double-processed.
- **Single-writer discipline:** both bots respect the `data/.db.lock` from Prompt 2; never write while the nightly pipeline (or the other bot) holds it.
- **Approval flow (shared):** each bot posts a confirmation card in its own channel; the commissioner approves with ✅ or `/approve <id>`, rejects with ❌ or `/reject <id>`. On approval → append ledger event(s) (`source='discord'`, `approved_by` = commissioner) → trigger the report rebuild (call into `sda.reports.build`) → post the updated report link in the channel.
- **Dry-run mode:** `--dry-run` validates and posts nothing, writes nothing. The commissioner will test with this first.
- The token appears nowhere in logs, the DB, or the repo (test asserts this for **both** tokens).

## Bot 1 — Signing bot

1. **Listen.** Watch the signings announcements channel for new messages.
2. **Parse.** The league's standard announcement format is:
   `📝 SIGNING: <player> — <N> years — <team>`
   Parse that strictly first; fall back to lenient free-text parsing (player name via the Prompt-1 alias map, a year count, a team name). If parsing fails, reply in-channel asking the manager to use the format — don't guess.
3. **Validate** (using the Prompt-4 rule functions — import them, don't reimplement):
   - Contract bounds for the acquisition type (drafted/called-up 1–7; in-season waiver/FA 1–3).
   - Cap room: team has space for the new years including IL relief and cap trades.
   - Roster limits (26 MLB / 15 minors max).
   - Re-sign tripwire: is this a same-team re-add of a dropped player with >3 years in-season? Reject outright.
4. **Queue.** Insert into `pending_contracts` with the raw message text, and post a **confirmation card** in the channel: parsed player/team/years + pass/fail per rule. Also write the parsed announcement (with its timestamp) to an `announcements` table — the 1-day signing rule keys off *announcement time*, not approval time.
5. **One-day rule.** A separate lightweight loop (or a `/check` command) matches recent Fantrax adds against announcements: no announcement within 1 day of the add → append `DEFAULTED_1YR` (`source='system'`) and post a notice in the channel.

## Bot 2 — Cap-trade bot

Managers announce cap-space trades in the **dedicated cap-trade channel** (cap tracking starts at 78 per team per season — no historical trades imported):

1. **Listen.** Watch only the cap-trade channel; ignore the signings channel entirely.
2. **Parse.** Strict format first:
   `📝 CAP TRADE: <from_team> sends <N> years to <to_team>`
   Fall back to lenient parsing (team names via config/`teams` table, a year count which may be fractional, e.g. 2.5). If parsing fails, reply asking for the format — don't guess.
3. **Validate.** Both teams resolve to known `team_id`s; N is positive and finite. (No cap-room check on the *sending* team — trading away space is always legal; it just reduces their effective cap.)
4. **Queue + confirm.** Insert into the pending queue (same `pending_contracts` table with a `kind='cap_trade'` marker, or a `pending_cap_trades` table — pick one and document it) and post a confirmation card: from → to, years, **each team's resulting effective cap and remaining space** after the trade.
5. **Approve.** Same ✅/❌ flow as the signing bot. On approval → append **two** `CAP_TRADE` events: `-N` years on the sending team, `+N` years on the receiving team (`source='discord'`, `approved_by` = commissioner) → rebuild reports → post the updated cap-tracker link in the channel.

## Hosting note (for the README)

Both bots run on the commissioner's Mac via `launchd` (auto-start on boot) — **two plist files, one per bot**, so either can be restarted independently. Document both plist setups. They are NOT serverless — GitHub Actions can't do this.

## What to build

- `sda/discord_bot/common.py` — shared client setup, intents, per-bot watermark catch-up, lock handling.
- `sda/discord_bot/signing_bot.py` — signing watcher (`python -m sda.discord_bot.signing`).
- `sda/discord_bot/captrade_bot.py` — cap-trade watcher (`python -m sda.discord_bot.captrade`).
- `sda/discord_bot/parser.py` — strict + lenient parsing for both formats (pure functions, fully tested).
- `sda/discord_bot/approvals.py` — shared reaction/slash-command handling, ledger append on approval.
- CLI: both entry points accept `--dry-run`.

## Acceptance criteria

- Parser tests: the exact `📝 SIGNING:` format, three realistic free-text variants, and three malformed messages (which must NOT parse). Same coverage for the `📝 CAP TRADE:` format (including a fractional-years case).
- End-to-end dry run (signing bot): a fake announcement for a player that would break the cap → confirmation card shows the cap rule failing → nothing written to the ledger or pending queue beyond the queued row.
- End-to-end dry run (cap-trade bot): `📝 CAP TRADE: <team_a> sends 2.5 years to <team_b>` → card shows both teams' resulting effective cap/remaining → approval writes exactly two `CAP_TRADE` events (`-2.5` sender, `+2.5` receiver).
- Restart test (each bot): kill mid-queue, restart, no duplicate announcements processed; watermarks are per-bot.
- Both tokens appear nowhere in logs, the DB, or the repo (test asserts this).
- README documents: the two-bot setup, the commissioner's three prerequisites above, both `launchd` plists, and which parsing mode shipped (free-text vs slash-command-first).
