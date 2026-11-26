"""Launcher for the installed guard: `python -I -S run_guard.py --policy <file>`.

`-I` (isolated) ignores PYTHON* env vars and the user site-packages; `-S` skips `site`,
which also makes startup faster. The guard needs only the standard library, so it runs
from the directory this file sits in (an admin-owned folder once installed).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from spok1_guard.hook import main

sys.exit(main())
