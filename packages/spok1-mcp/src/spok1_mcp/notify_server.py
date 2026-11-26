"""`spok1-notify`: a send-only MCP server that lets any Claude Code session ping the owner.

It posts through Discord webhooks only (no bot token), using spok1_core.discord.
Run by Claude Code as a stdio MCP server: `spok1-notify-mcp`.
"""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from spok1_core import discord

server = MCPServer(
    name="spok1-notify",
    instructions=(
        "Send the owner a Discord notification. Use P1 only when something needs them now "
        "(it pings their phone even in quiet hours), P2 for normal alerts, P3 for digest items. "
        "Never include secrets, tokens or other people's personal data in a message."
    ),
)


@server.tool()
def notify(
    message: str,
    priority: Literal["P1", "P2", "P3"] = "P2",
    title: str | None = None,
) -> str:
    """Send a Discord message to the owner.

    Args:
        message: The text to send (Discord markdown; truncated at 2000 characters).
        priority: P1 = urgent with an @mention, P2 = alert, P3 = goes to the daily digest.
        title: Optional short title shown in bold.
    """
    try:
        discord.send(message, priority, title)
    except discord.NotifyError as exc:
        return f"Not sent: {exc}"
    return f"Sent ({priority})."


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
