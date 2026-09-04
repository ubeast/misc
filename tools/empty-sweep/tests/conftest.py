"""Make the single-file script importable as ``empty_sweep``.

The implementation lives one directory up, at ``tools/empty-sweep/empty_sweep.py``
(one file, so it can be copy-pasted where nothing is installed). Putting that
directory on ``sys.path`` lets the test suite ``import empty_sweep`` normally.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
