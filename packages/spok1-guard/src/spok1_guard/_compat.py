"""Startup-cost helpers. The guard runs on every tool call, so it avoids importing
`typing`, `dataclasses`, `pathlib` and `tempfile` at runtime (~60 ms on Windows)."""

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import cast as cast
else:

    def cast(_type, value):  # type: ignore[no-untyped-def]
        return value
