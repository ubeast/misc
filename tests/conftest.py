"""Make the single-file script importable as ``ic_filename_parser``.

The implementation lives at ``scripts/ic_filename_parser.py`` (one file, so it
can be copy-pasted where nothing is installed). Putting ``scripts/`` on
``sys.path`` lets the test suite ``import ic_filename_parser`` normally.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
