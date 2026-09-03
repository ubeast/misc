"""Rewrite the committed sample ARCHIVE_NOTES.md files in this folder.

Run after a deliberate change to `render_archive_notes`; the samples double as
documentation (developers read them to see what a run produces) and as a golden
check in `test_render_matches_committed_sample`.

    python tools/dbricks-folder-archiver/tests/regenerate_samples.py

Run directly (not under pytest), so `conftest.py` is not loaded - put the tool
directory on `sys.path` here the same way it does.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # for `import dbricks_folder_archiver`

from test_dbricks_folder_archiver import (  # noqa: E402
    _sample_complete_report,
    _sample_incomplete_report,
    dwa,
)


def main() -> None:
    for filename, builder in (
        ("sample_archive_notes_complete.md", _sample_complete_report),
        ("sample_archive_notes_incomplete.md", _sample_incomplete_report),
    ):
        (_HERE / filename).write_text(dwa.render_archive_notes(builder()), encoding="utf-8")
        print(f"wrote {filename}")


if __name__ == "__main__":
    main()
