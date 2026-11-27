"""`spok1-agentd run | setup | status | test-alert`."""

from __future__ import annotations

import logging
import sys

from spok1_agentd import settings as settings_mod
from spok1_agentd.daemon import Daemon
from spok1_agentd.db import DB
from spok1_agentd.discord_api import Discord, DiscordError


def build() -> Daemon:
    s = settings_mod.load()
    db = DB(s.db_path)
    discord = Discord(s.bot_token) if s.bot_token else None
    return Daemon(s, db, discord)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else "run"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = settings_mod.load()
    if cmd == "status":
        missing = settings_mod.missing(s)
        print("settings:", "ok" if not missing else "missing " + ", ".join(missing))
        print("healthcheck:", "set" if s.healthcheck_url else "not set")
        print("headless token:", "set" if s.oauth_token else "not set (agent-run jobs can't run)")
        d = build()
        print(
            "channels:",
            ", ".join(f"#{k}" for k in sorted(d.channels)) or "none (run: spok1-agentd setup)",
        )
        print("jobs:", d.db.one("SELECT COUNT(*) AS n FROM jobs WHERE enabled=1")["n"])  # type: ignore[index]
        return 0
    missing = settings_mod.missing(s)
    if missing:
        print(
            f"✗ missing in {settings_mod.DEFAULT_ENV_FILE}: {', '.join(missing)}", file=sys.stderr
        )
        return 1
    d = build()
    if cmd == "setup":
        try:
            for line in d.setup_discord() or ["HQ channels already exist"]:
                print("✓", line)
        except DiscordError as exc:
            print(
                f"✗ {exc}\n  (does the bot have Manage Channels? re-authorize it with that permission)",
                file=sys.stderr,
            )
            return 1
        return 0
    if cmd == "test-alert":
        alert_id = d.notifier.notify(
            "P2",
            "agentd test alert",
            "If you can read this, agentd can reach #alerts. Try: `ack " + "N`.",
            source="test",
            dedup_key=f"test:{d.clock()}",
        )
        print(f"sent alert #{alert_id}")
        return 0
    if cmd == "learn-now":
        print(d.learn_now())
        return 0
    if cmd == "run":
        try:
            for line in d.setup_discord():
                logging.info("setup: %s", line)
        except DiscordError as exc:  # keep running: watchers, deadman and reminders still work
            logging.warning("Discord setup failed: %s", exc)
        d.run_forever()
        return 0
    print("usage: spok1-agentd run | setup | status | test-alert | learn-now", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
