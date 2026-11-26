"""`python -m spok1_guard` / `spok1-guard`: the PreToolUse hook entry point."""

import sys

from spok1_guard.hook import main

if __name__ == "__main__":
    sys.exit(main())
