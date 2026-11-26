"""Static checks for infra/ (the scripts themselves are syntax-checked by shellcheck in CI)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

INFRA = Path(__file__).resolve().parents[3] / "infra"


def cloud_init() -> dict[str, Any]:
    text = (INFRA / "cloud-init.yaml").read_text(encoding="utf-8")
    assert text.startswith("#cloud-config\n")
    return cast(dict[str, Any], yaml.safe_load(text))


def test_cloud_init_is_valid_and_has_the_basics() -> None:
    data = cloud_init()
    packages = cast(list[str], data["packages"])
    for pkg in ("git", "tmux", "sqlite3", "ffmpeg", "unattended-upgrades"):
        assert pkg in packages
    users = [cast(dict[str, Any], u) for u in cast(list[Any], data["users"]) if isinstance(u, dict)]
    spok1 = next(u for u in users if u.get("name") == "spok1")
    assert "sudo" not in spok1  # the runtime user never gets sudo
    joined = " ".join(str(c) for c in cast(list[Any], data["runcmd"]))
    assert (
        "/swapfile" in joined and "tailscale.com/install.sh" in joined and "enable-linger" in joined
    )


def test_no_secrets_in_cloud_init() -> None:
    """Azure custom data is readable by anyone with access to the subscription."""
    text = (INFRA / "cloud-init.yaml").read_text(encoding="utf-8")
    for pattern in (
        r"tskey-",
        r"--auth-?key",
        r"m0-[A-Za-z0-9]{8}",
        r"gh[pousr]_[A-Za-z0-9]{8}",
        r"discord\.com/api/webhooks",
        r"BEGIN [A-Z ]*PRIVATE KEY",
        r"password\s*:",
    ):
        assert not re.search(pattern, text, re.IGNORECASE), pattern


def test_create_script_never_opens_inbound_ports() -> None:
    text = (INFRA / "azure-create.sh").read_text(encoding="utf-8")
    assert "--nsg-rule NONE" in text
    assert "nsg rule create" not in text
    assert "Standard_B2pts_v2" in text and "server-arm64" in text


def test_destroy_script_only_touches_spok1() -> None:
    text = (INFRA / "azure-destroy.sh").read_text(encoding="utf-8")
    assert "tags.project" in text and '"spok1"' in text
    assert "read -rp" in text  # typed confirmation
