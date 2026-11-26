"""Send-only Discord delivery through webhooks (laptops never hold the bot token).

Priority routing (PRD §1): P1 → #alerts with an @mention, P2 → #alerts, P3 → #daily-digest.
Webhook URLs are secrets and come from the environment / `~/.config/agent/.env`.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from spok1_core import config

Priority = Literal["P1", "P2", "P3"]

MAX_CONTENT = 2000  # Discord's message content limit

WEBHOOK_ALERTS = "DISCORD_WEBHOOK_ALERTS"
WEBHOOK_DIGEST = "DISCORD_WEBHOOK_DIGEST"
DISCORD_USER_ID = "DISCORD_USER_ID"


class NotifyError(RuntimeError):
    pass


def webhook_for(priority: Priority) -> str:
    """The webhook URL for a priority. P3 falls back to #alerts if no digest hook is set."""
    if priority == "P3":
        url = config.get_setting(WEBHOOK_DIGEST) or config.get_setting(WEBHOOK_ALERTS)
    else:
        url = config.get_setting(WEBHOOK_ALERTS)
    if not url:
        raise NotifyError(
            f"No Discord webhook configured: set {WEBHOOK_ALERTS} in {config.env_file()}"
        )
    return url


def build_payload(
    message: str,
    priority: Priority = "P2",
    title: str | None = None,
    machine: str | None = None,
    mention_user_id: str | None = None,
) -> dict[str, Any]:
    """Build a webhook payload. Only a P1 may ping, and only the configured user."""
    icon = {"P1": "🚨", "P2": "🔔", "P3": "📝"}[priority]
    header = f"{icon} **{priority}**"
    if title:
        header += f" · {title}"
    if machine:
        header += f" · `{machine}`"
    mention = f"<@{mention_user_id}> " if priority == "P1" and mention_user_id else ""
    content = f"{mention}{header}\n{message}"
    if len(content) > MAX_CONTENT:
        content = content[: MAX_CONTENT - 1] + "…"
    allowed: dict[str, Any] = {"parse": []}
    if mention:
        allowed["users"] = [mention_user_id]
    return {"content": content, "allowed_mentions": allowed}


def send(
    message: str,
    priority: Priority = "P2",
    title: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> None:
    """Post a notification to Discord. Raises NotifyError on failure."""
    payload = build_payload(
        message,
        priority,
        title,
        machine=config.machine_name(),
        mention_user_id=config.get_setting(DISCORD_USER_ID),
    )
    url = webhook_for(priority)
    owned = client is None
    http = client or httpx.Client(timeout=10)
    try:
        response = http.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise NotifyError(f"Discord webhook request failed: {type(exc).__name__}") from exc
    finally:
        if owned:
            http.close()
    if response.status_code >= 300:
        raise NotifyError(f"Discord webhook returned HTTP {response.status_code}")
