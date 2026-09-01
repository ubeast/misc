"""Print a pandas DataFrame as a GitHub-flavored Markdown table.

Standalone helper — not part of the ic_filename_parser package.
"""

from __future__ import annotations

import pandas as pd


def to_markdown(df: pd.DataFrame, record_count: int = 5) -> None:
    def esc(value: object) -> str:
        return str(value).replace("|", r"\|")

    header = f"| {' | '.join(esc(_) for _ in df.columns)} |"
    header_sep = "|" + "|".join("----" for _ in df.columns) + "|"
    rows = [list(_) for _ in df.fillna("").to_records(index=False)]

    print(header)
    print(header_sep)

    for row in rows[:record_count]:
        md = [esc(_) for _ in row]
        print(f"| {' | '.join(md)} |")
