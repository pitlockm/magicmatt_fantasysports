# PROMPT 6 — Discord commishbot (form relay → validate → approve)

Copy everything below into Claude Code. Run this after Prompt 2 (it needs the ledger and `pending_contracts`). Build inside `second_deck_association_process/sda/discord_bot/`.

---

## Goal

**One** long-running `discord.py` bot — the commishbot — watching the league's
announcements channel. The Google Form system (Prompt 8) is the **sole input
process** for long-term signings and cap trades: managers never post
announcements by hand. The form's Apps Script relays each accepted submission
to this channel via a Discord webhook, and the bot does the rest: **validate
each relayed submission against the league rules before anything is recorded**,
and queue it for commissioner approval. Nothing reaches the ledger without
passing validation AND approval.

This supersedes the old two-bot plan (separate signing watcher + cap-trade
watcher, free-text `📝 SIGNING:` parsing). There is no second bot, no second
channel, no second token.

## Configuration (all real values already known)

- Announcements channel ID: `1496593362135027963` → config key `discord_announcements_channel_id` (already in `config/season.yaml`).
- Bot token → `.env` as `DISCORD_BOT_TOKEN` (the commissioner holds it; never commit, never log it).
- The bot needs the **Message Content Intent** enabled in the Discord developer portal.
- The form relay posts via a Discord **webhook** whose URL lives in the Apps
  Script's Script Properties — never in this repo. The bot does not need the
  webhook URL; it only reads the channel.

## Commissioner prerequisites (do these before running the bot)

1. In the Discord developer portal, create (or reuse) **one** bot application for the commishbot. Enable Message Content Intent, copy its token → `DISCORD_BOT_TOKEN` in `.env`, and invite it to the server with read/send permission in the announcements channel.
2. Set the announcements channel to **read-only for managers** (or establish the rule): the form relay is the only writer besides the bot. The bot ignores any message that doesn't match the relay formats below.

## Relay message formats (written by the Prompt-8 Apps Script — parse these strictly)

Signing:
```
📝 SIGNING: <player> — <N> years — <team>
type: waiver | drafted | called_up · submitted: <ISO-8601 UTC> · by: <team> · ref: <uuid>
```

Cap trade:
```
📝 CAP TRADE: <from_team> sends <N> years to <to_team>
submitted: <ISO-8601 UTC> · by: <from_team> · ref: <uuid>
```

Rules: parse line 1 strictly. Line 2 is metadata — `submitted` is the
**announcement timestamp** for the one-day signing rule; `ref` is a submission
UUID for deduplication. **Ignore (log at debug, do not queue) any message that
doesn't match these formats** — including hand-typed manager messages.

## Bot behavior

1. **Listen.** Watch only the announcements channel.
2. **Dedupe.** If a `ref` UUID was already processed (webhook retries, double
   form submits), skip it. Also treat same kind + same player/teams + same
   years + same team within 24h as a duplicate.
3. **Validate** (using the Prompt-4 rule functions — import them, don't reimplement):
   - *Signings:* contract bounds for the acquisition type (`type` line: drafted/called_up 1–7; waiver 1–3); cap room including IL relief and cap trades; roster limits (26 MLB / 15 minors max); re-sign tripwire (same-team re-add of a dropped player with >3 years in-season → reject outright).
   - *Cap trades:* both teams resolve to known `team_id`s; N positive and finite. (No cap-room check on the *sending* team — trading away space is always legal.)
4. **Queue.** Insert into `pending_contracts` (with a `kind` marker: `signing` / `cap_trade`) along with the raw relay text and the `submitted` timestamp, and post a **confirmation card** in the channel: parsed details + pass/fail per rule + the resulting effective cap / remaining space for the affected team(s).
5. **Approve.** The commissioner approves with ✅ or `/approve <id>`, rejects with ❌ or `/reject <id>`.
   - Signing approval → append one `SIGNED` event (`source='form'`, `approved_by` = commissioner).
   - Cap-trade approval → append **two** `CAP_TRADE` events: `-N` on the sender, `+N` on the receiver (`source='form'`, `approved_by` = commissioner).
   - Then trigger the report rebuild (call into `sda.reports.build`) and post the updated report link + a "processed" notice in the channel.
6. **One-day signing rule.** A lightweight loop (or `/check` command) matches recent Fantrax MLB adds against relayed signings by `submitted` timestamp: no form submission within 1 day of the add → append `DEFAULTED_1YR` (`source='system'`) and post a notice in the channel.

## Infrastructure (kept from the old spec)

- Single entry point: `python -m sda.discord_bot.commish` (also accepts `--dry-run`).
- **Startup catch-up:** on launch, scan channel history since the last processed message ID (persist the watermark in the DB). The laptop sleeps — nothing may be lost or double-processed.
- **Single-writer discipline:** respect the `data/.db.lock` from Prompt 2; never write while the nightly pipeline holds it.
- **Dry-run mode:** `--dry-run` validates and posts nothing, writes nothing.
- The token appears nowhere in logs, the DB, or the repo (test asserts this).

## Hosting note (for the README)

The bot runs on the commissioner's Mac via `launchd` (auto-start on boot) — **one** plist file. Document the setup. It is NOT serverless — GitHub Actions can't do this.

## What to build

- `sda/discord_bot/common.py` — client setup, intents, startup catch-up, lock handling.
- `sda/discord_bot/commish.py` — the bot (`python -m sda.discord_bot.commish`).
- `sda/discord_bot/parser.py` — strict parsing of the two relay formats (pure functions, fully tested). No lenient free-text parsing — the form is the only input.
- `sda/discord_bot/approvals.py` — reaction/slash-command handling, ledger append on approval.
- CLI: entry point accepts `--dry-run`.

## Acceptance criteria

- Parser tests: exact relay formats for signing (all three acquisition types) and cap trade (including a fractional-years case, e.g. 2.5); malformed messages and hand-typed variants must NOT parse / must be ignored.
- Dedupe test: same `ref` twice, and same deal within 24h with different `ref`, each processed exactly once.
- End-to-end dry run (signing): a relayed signing that would break the cap → confirmation card shows the cap rule failing → nothing written beyond the queued row.
- End-to-end dry run (cap trade): relayed `📝 CAP TRADE: <team_a> sends 2.5 years to <team_b>` → card shows both teams' resulting effective cap/remaining → approval writes exactly two `CAP_TRADE` events (`-2.5` sender, `+2.5` receiver).
- Restart test: kill mid-queue, restart, no duplicate processing; watermark intact.
- Token appears nowhere in logs, the DB, or the repo (test asserts this).
- README documents: the form-relay input model, the commissioner's prerequisites above, the `launchd` plist, and the "channel is bot-written only" rule for managers.
