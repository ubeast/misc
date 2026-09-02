"""Make the single-file script importable as ``scac``.

The implementation lives one directory up, at ``tools/scac/scac.py`` (one file,
so it can be copy-pasted where nothing is installed). Putting that directory on
``sys.path`` lets the test suite ``import scac`` normally.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
