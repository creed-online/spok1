"""Discord REST client for agentd (the manager's plugin owns the gateway; we never connect to it).

Honours rate limits: per-route `X-RateLimit-*` headers and 429 `retry_after`. Never logs the token.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, cast

import httpx

API = "https://discord.com/api/v10"
TEXT, CATEGORY = 0, 4


class DiscordError(RuntimeError):
    pass


class Discord:
    def __init__(
        self, token: str, client: httpx.Client | None = None, sleep: Any = time.sleep
    ) -> None:
        self._client = client or httpx.Client(timeout=20)
        self._headers = {
            "Authorization": f"Bot {token}",
            "User-Agent": "spok1-agentd (https://github.com/creed-online/spok1, 0.1)",
        }
        self._sleep = sleep

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        for _attempt in range(5):
            try:
                resp = self._client.request(method, API + path, headers=self._headers, **kwargs)
            except httpx.HTTPError as exc:
                raise DiscordError(f"{method} {path}: {type(exc).__name__}") from exc
            if resp.status_code == 429:
                retry = 1.0
                with contextlib.suppress(ValueError):
                    retry = float(cast(dict[str, Any], resp.json()).get("retry_after", 1.0))
                self._sleep(min(retry, 30.0) + 0.1)
                continue
            if resp.status_code >= 400:
                raise DiscordError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}")
            if resp.headers.get("X-RateLimit-Remaining") == "0":
                self._sleep(min(float(resp.headers.get("X-RateLimit-Reset-After", "1")), 10.0))
            return resp.json() if resp.content else None
        raise DiscordError(f"{method} {path}: still rate limited")

    # ── guild setup ────────────────────────────────────────────────────
    def channels(self, guild_id: str) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.request("GET", f"/guilds/{guild_id}/channels"))

    def create_channel(
        self,
        guild_id: str,
        name: str,
        kind: int = TEXT,
        parent_id: str | None = None,
        topic: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "type": kind}
        if parent_id:
            body["parent_id"] = parent_id
        if topic:
            body["topic"] = topic
        return cast(dict[str, Any], self.request("POST", f"/guilds/{guild_id}/channels", json=body))

    def webhooks(self, channel_id: str) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.request("GET", f"/channels/{channel_id}/webhooks"))

    def create_webhook(self, channel_id: str, name: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self.request("POST", f"/channels/{channel_id}/webhooks", json={"name": name}),
        )

    # ── messages ───────────────────────────────────────────────────────
    def send(
        self,
        channel_id: str,
        content: str,
        *,
        mention_user: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        allowed: dict[str, Any] = {"parse": []}
        if mention_user:
            content = f"<@{mention_user}> {content}"
            allowed = {"users": [mention_user]}
        body: dict[str, Any] = {"content": content[:2000], "allowed_mentions": allowed}
        if embeds:
            body["embeds"] = embeds[:10]
        if reply_to:
            body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
        return cast(
            dict[str, Any], self.request("POST", f"/channels/{channel_id}/messages", json=body)
        )

    def messages_after(
        self, channel_id: str, after: str | None, limit: int = 50
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        msgs = cast(
            list[dict[str, Any]],
            self.request("GET", f"/channels/{channel_id}/messages", params=params),
        )
        return sorted(msgs, key=lambda m: int(str(m["id"])))  # oldest first

    def react(self, channel_id: str, message_id: str, emoji: str) -> None:
        from urllib.parse import quote

        self.request(
            "PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji)}/@me"
        )
