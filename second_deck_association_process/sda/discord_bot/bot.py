"""Discord client for signing and cap-trade proposals."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.discord_bot.common import create_message_intents, catch_up_channel
from sda.discord_bot.approvals import (
    ApprovalError,
    approve_pending,
    mark_message_processed,
    player_aliases,
    queue_cap_trade,
    queue_signing,
    reject_pending,
    team_resolver,
    validate_cap_trade,
    validate_signing,
)
from sda.discord_bot.parser import parse_cap_trade, parse_signing
from sda.reports.build import build_site
from sda.validation.engine import run_all

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "season.yaml"
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "data" / "player_aliases.json"
DEFAULT_REPORT_URL = "https://pitlockm.github.io/magicmatt_fantasysports/"
class SdaContractBot(commands.Bot):
    """Watch one announcements channel and queue validated proposals for review."""

    def __init__(
        self,
        *,
        channel_id: int,
        season: int,
        database_path: Path,
        alias_path: Path,
        commissioner_user_id: int | None = None,
        report_url: str = DEFAULT_REPORT_URL,
    ) -> None:
        super().__init__(command_prefix=commands.when_mentioned, intents=create_message_intents())
        self.channel_id = int(channel_id)
        self.season = int(season)
        self.database_path = Path(database_path)
        self.alias_path = Path(alias_path)
        self.commissioner_user_id = commissioner_user_id
        self.report_url = report_url.rstrip("/")
        self._catchup_complete = False

    async def setup_hook(self) -> None:
        """Register slash commands before connecting to Discord."""
        @self.tree.command(name="approve", description="Approve a pending contract or cap trade")
        @app_commands.describe(pending_id="Queue ID shown on the confirmation card")
        async def approve_command(interaction: discord.Interaction, pending_id: int) -> None:
            if not self._is_commissioner(interaction.user):
                await interaction.response.send_message("Commissioner approval is required.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                result = await self._approve(pending_id, str(interaction.user.id))
            except ApprovalError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            await interaction.followup.send(result, ephemeral=True)
            if interaction.channel is not None:
                await interaction.channel.send(result)

        @self.tree.command(name="reject", description="Reject a pending contract or cap trade")
        @app_commands.describe(pending_id="Queue ID shown on the confirmation card")
        async def reject_command(interaction: discord.Interaction, pending_id: int) -> None:
            if not self._is_commissioner(interaction.user):
                await interaction.response.send_message("Commissioner approval is required.", ephemeral=True)
                return
            try:
                reject_pending(pending_id, self.database_path)
            except ApprovalError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return
            await interaction.response.send_message(f"Proposal {pending_id} rejected.", ephemeral=True)

        @self.tree.command(name="check", description="Apply the one-day default to unannounced adds")
        async def check_command(interaction: discord.Interaction) -> None:
            if not self._is_commissioner(interaction.user):
                await interaction.response.send_message("Commissioner permission is required.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                report = await asyncio.to_thread(run_all, self.season, self.database_path)
            except (OSError, RuntimeError, ValueError):
                LOGGER.exception("Validation check could not be completed")
                await interaction.followup.send("Validation could not be completed.", ephemeral=True)
                return
            summary = report["summary"]
            summary_message = (
                f"Validation complete: {summary['failed']} failure(s), "
                f"{len(summary['actions_taken'])} default action(s)."
            )
            await interaction.followup.send(summary_message, ephemeral=True)
            if summary["actions_taken"] and interaction.channel is not None:
                for action in summary["actions_taken"]:
                    await interaction.channel.send(
                        f"One-day signing rule: {action['fantrax_id']} on team "
                        f"{action['team_id']} defaulted to one year."
                    )

        await self.tree.sync()

    async def on_ready(self) -> None:
        """Catch up on channel history once after the gateway is ready."""
        if self._catchup_complete:
            return
        self._catchup_complete = await catch_up_channel(
            self,
            self.channel_id,
            self.database_path,
            self._handle_announcement,
            LOGGER,
        )

    async def on_message(self, message: discord.Message) -> None:
        """Handle webhook relays and ignore bot chatter or other channels."""
        if message.channel.id != self.channel_id:
            return
        if message.author.bot and message.webhook_id is None:
            return
        await self._handle_announcement(message)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Allow commissioner check/cross reactions on proposal cards."""
        if payload.channel_id != self.channel_id or (self.user and payload.user_id == self.user.id):
            return
        emoji = str(payload.emoji)
        if emoji not in {"✅", "❌"}:
            return
        guild = self.get_guild(payload.guild_id) if payload.guild_id else None
        member = guild.get_member(payload.user_id) if guild else None
        if member is None and guild:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if member is None or not self._is_commissioner(member):
            return
        channel = self.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            card = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        pending_id = _pending_id_from_embed(card.embeds)
        if pending_id is None:
            return
        if emoji == "✅":
            try:
                result = await self._approve(pending_id, str(member.id))
            except ApprovalError as error:
                await channel.send(f"Proposal {pending_id} was not approved: {error}")
                return
            await channel.send(result)
        else:
            try:
                reject_pending(pending_id, self.database_path)
            except ApprovalError as error:
                await channel.send(str(error))
                return
            await channel.send(f"Proposal {pending_id} rejected.")

    async def _handle_announcement(self, message: discord.Message) -> None:
        """Parse, validate, and durably queue a message once."""
        content = message.content
        aliases = player_aliases(self.database_path, self.alias_path)
        teams = team_resolver(self.database_path)
        cap_trade = parse_cap_trade(content, teams)
        if cap_trade:
            validation = validate_cap_trade(cap_trade, self.database_path, season=self.season)
            pending_id, duplicate = queue_cap_trade(
                cap_trade,
                content,
                str(message.id),
                str(message.channel.id),
                self.database_path,
            )
            if duplicate or pending_id is None:
                return
            card = _confirmation_embed(
                pending_id,
                f"Cap trade: {cap_trade.from_team_name} → {cap_trade.to_team_name}",
                f"{cap_trade.years:g} years",
                validation,
            )
            response = await message.reply(embed=card, mention_author=False)
            self._save_approval_message_id(pending_id, str(response.id))
            await response.add_reaction("✅")
            await response.add_reaction("❌")
            return

        signing = parse_signing(content, aliases, teams)
        if signing is None:
            if not mark_message_processed(str(message.id), str(message.channel.id), "ignored_non_relay", self.database_path):
                return
            LOGGER.debug("Ignored a non-relay announcement message")
            return

        validation = validate_signing(
            signing,
            self.database_path,
            now=signing.submitted_at,
            acquisition_type=signing.acquisition_type,
        )
        pending_id, duplicate = queue_signing(
            signing,
            content,
            str(message.id),
            str(message.channel.id),
            self.database_path,
        )
        if duplicate or pending_id is None:
            return
        card = _confirmation_embed(
            pending_id,
            f"Signing: {signing.player_name} / {signing.team_name}",
            f"{signing.years:g} years",
            validation,
        )
        card.set_footer(text=f"pending_id={pending_id}")
        response = await message.reply(embed=card, mention_author=False)
        self._save_approval_message_id(pending_id, str(response.id))
        await response.add_reaction("✅")
        await response.add_reaction("❌")

    async def _approve(self, pending_id: int, approver: str) -> str:
        """Approve a pending item, rebuild static pages, and return their public link."""
        try:
            await asyncio.to_thread(approve_pending, pending_id, approver, self.database_path)
            await asyncio.to_thread(build_site, self.season, self.database_path)
        except ApprovalError:
            raise
        except (OSError, RuntimeError, ValueError):
            LOGGER.exception("Could not approve pending proposal %s", pending_id)
            raise ApprovalError("Approval or report rebuild failed; contact the commissioner") from None
        return f"Proposal {pending_id} processed and approved. Updated reports: {self.report_url}/index.html"

    def _is_commissioner(self, user: discord.abc.User) -> bool:
        if self.commissioner_user_id is not None:
            return user.id == self.commissioner_user_id
        permissions = getattr(user, "guild_permissions", None)
        return bool(permissions and permissions.manage_guild)

    def _save_approval_message_id(self, pending_id: int, message_id: str) -> None:
        with open_database(self.database_path) as connection:
            connection.execute(
                "UPDATE pending_contracts SET approval_message_id = ? WHERE pending_id = ?",
                [message_id, pending_id],
            )
            from sda.db.snapshots import export_text_snapshot

            export_text_snapshot(self.database_path)


def evaluate_message(
    message: str,
    database_path: Path,
    alias_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Offline dry-run evaluator; it validates but never queues or writes."""
    aliases = player_aliases(database_path, alias_path)
    teams = team_resolver(database_path)
    trade = parse_cap_trade(message, teams)
    if trade:
        result = validate_cap_trade(trade, database_path)
        return {"kind": "cap_trade", "proposal": trade, "validation": result}
    signing = parse_signing(message, aliases, teams)
    if signing:
        result = validate_signing(signing, database_path, now=now)
        return {"kind": "signing", "proposal": signing, "validation": result}
    return {"kind": "unparsed", "proposal": None, "validation": {"passed": False, "checks": []}}


def _confirmation_embed(
    pending_id: int,
    title: str,
    summary: str,
    validation: Mapping[str, Any],
) -> discord.Embed:
    passed = bool(validation.get("passed"))
    embed = discord.Embed(
        title=title,
        description=f"{summary}\nQueue #{pending_id}\n" + ("All checks pass." if passed else "One or more checks failed; approval is blocked."),
        color=discord.Color.green() if passed else discord.Color.red(),
    )
    checks = validation.get("checks", [])
    for check in checks:
        status = "PASS" if check.get("passed") else "FAIL"
        embed.add_field(
            name=f"{status} / {check.get('rule_id', 'CHECK')}",
            value=str(check.get("detail", "No details"))[:1000],
            inline=False,
        )
    embed.set_footer(text=f"pending_id={pending_id}")
    return embed


def _pending_id_from_embed(embeds: list[discord.Embed]) -> int | None:
    for embed in embeds:
        text = embed.footer.text or ""
        if text.startswith("pending_id="):
            try:
                return int(text.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict):
        raise ValueError(f"Invalid season configuration: {path}")
    return config


def main(argv: list[str] | None = None) -> int:
    """Run the bot, or validate one sample announcement without network or writes."""
    parser = argparse.ArgumentParser(description="Run the SDA contract approvals bot.")
    parser.add_argument("--dry-run", action="store_true", help="Validate without connecting or writing.")
    parser.add_argument("--message", help="Announcement text to validate in dry-run mode.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIAS_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = _load_config(args.config)
        if args.dry_run:
            if not args.message:
                print("Dry run: no connection or writes. Provide --message to validate a sample announcement.")
                return 0
            evaluation = evaluate_message(args.message, args.database, args.aliases)
            return _print_evaluation(evaluation)

        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            LOGGER.error("DISCORD_BOT_TOKEN is not set")
            return 2
        channel_id = config.get("discord_announcements_channel_id")
        if channel_id is None:
            LOGGER.error("discord_announcements_channel_id is missing from config")
            return 2
        commissioner_id = config.get("discord_commissioner_user_id")
        report_url = os.getenv("SDA_REPORT_URL", DEFAULT_REPORT_URL)
        bot = SdaContractBot(
            channel_id=int(channel_id),
            season=int(config["season"]),
            database_path=args.database,
            alias_path=args.aliases,
            commissioner_user_id=int(commissioner_id) if commissioner_id else None,
            report_url=report_url,
        )
        bot.run(token, log_handler=None)
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Discord bot could not start: %s", error)
        return 1
    return 0


def _print_evaluation(evaluation: Mapping[str, Any]) -> int:
    proposal = evaluation.get("proposal")
    if proposal is None:
        print("FAIL / PARSE: message did not resolve to a known player/team announcement")
        print("Expected the strict two-line Google Forms relay format.")
        return 1
    print(f"DRY RUN / {evaluation['kind']}: {proposal}")
    for result in evaluation["validation"]["checks"]:
        label = "PASS" if result["passed"] else "FAIL"
        print(f"{label} / {result['rule_id']}: {result['detail']}")
    print("No Discord connection, queue writes, announcements, or contract events were made.")
    return 0 if evaluation["validation"]["passed"] else 1