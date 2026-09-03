"""Make the single-file script importable as ``dbricks_folder_archiver``.

The implementation lives one directory up, at
``tools/dbricks-folder-archiver/dbricks_folder_archiver.py`` (one file, so it
can be copy-pasted into a Databricks workspace where nothing is installed).
Putting that directory on ``sys.path`` lets the test suite
``import dbricks_folder_archiver`` normally.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
