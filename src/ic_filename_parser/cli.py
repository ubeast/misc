"""Scan a directory of IC PDFs and emit the parsed table as CSV.

Replaces the original script's implicit behaviour (glob the cwd, dump to the
clipboard on import). Now: an explicit directory argument, CSV to stdout or a
file, clipboard only on ``--clipboard``.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .parser import COLUMNS, parse_filename

__all__ = ["iter_pdf_files", "scan_directory", "main"]


def iter_pdf_files(directory: Path) -> list[Path]:
    """Return the ``*.pdf`` files in ``directory``, case-insensitive, sorted."""
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


def scan_directory(directory: Path) -> pd.DataFrame:
    """Parse every PDF filename in ``directory`` into a DataFrame.

    The column set is fixed (:data:`ic_filename_parser.parser.COLUMNS`) even
    when the directory contains no PDFs, so downstream consumers get a stable
    schema.
    """
    records = [asdict(parse_filename(p.name)) for p in iter_pdf_files(directory)]
    return pd.DataFrame(records, columns=COLUMNS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse EBSO/DLMS/DTEB IC PDF filenames into a table."
    )
    parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=Path.cwd(),
        help="Directory to scan for *.pdf files (default: current directory).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write CSV to this path instead of stdout.",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Also copy the table to the system clipboard (needs pandas clipboard deps).",
    )
    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        parser.error(f"not a directory: {args.directory}")

    df = scan_directory(args.directory)

    if args.output:
        df.to_csv(args.output, index=False)
    else:
        print(df.to_csv(index=False), end="")

    if args.clipboard:
        df.to_clipboard(index=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
