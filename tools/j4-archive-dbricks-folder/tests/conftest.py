"""Make the single-file script importable as ``j4_archive_dbricks_folder``.

The implementation lives one directory up, at
``tools/j4-archive-dbricks-folder/j4_archive_dbricks_folder.py`` (one file, so it
can be copy-pasted into a Databricks workspace where nothing is installed).
Putting that directory on ``sys.path`` lets the test suite
``import j4_archive_dbricks_folder`` normally.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
