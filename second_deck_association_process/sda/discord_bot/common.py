"""Shared Discord client setup and restart-safe announcement catch-up."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import discord

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database


async def catch_up_channel(
    bot: Any,
    channel_id: int,
    database_path: Path,
    handler: Callable[[discord.Message], Awaitable[None]],
    logger: logging.Logger,
) -> bool:
    """Process unseen channel history in order; processed-message IDs make retries idempotent."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            logger.error("Could not open the configured announcements channel")
            return False
    if not isinstance(channel, discord.TextChannel):
        logger.error("Configured announcements channel is not a text channel")
        return False

    cursor = _last_message_id(database_path, channel_id, logger)
    after = discord.Object(id=int(cursor)) if cursor else None
    logger.info("Starting announcements catch-up")
    try:
        async for message in channel.history(limit=None, after=after, oldest_first=True):
            await handler(message)
    except discord.HTTPException:
        logger.error("Announcements catch-up failed; it will retry on reconnect")
        return False
    logger.info("Announcements catch-up complete")
    return True


def create_message_intents() -> discord.Intents:
    """Enable the message intents required to read webhook relay content."""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    return intents


def _last_message_id(
    database_path: Path = DEFAULT_DATABASE_PATH,
    channel_id: int | str = "",
    logger: logging.Logger | None = None,
) -> str | None:
    key = f"last_message_id:{channel_id}"
    try:
        with open_database(database_path, read_only=True) as connection:
            row = connection.execute(
                "SELECT state_value FROM discord_bot_state WHERE state_key = ?", [key]
            ).fetchone()
    except (OSError, RuntimeError):
        (logger or logging.getLogger(__name__)).error("Could not load Discord catch-up cursor")
        return None
    return str(row[0]) if row else None