#!/usr/bin/env python3
"""Find (and optionally delete) empty files, including empty notebooks.

Single file, standard library only. Usable as a CLI or importable as a module.

    $ python empty_sweep.py .                    # dry run, current dir only
    $ python empty_sweep.py . -r                  # dry run, recurse into subfolders
    $ python empty_sweep.py . -r --delete          # actually delete them

What counts as "empty"
-----------------------
* Any zero-byte file.
* A ``.ipynb`` notebook whose cells all have blank (whitespace-only) source,
  or which has no cells at all -- boilerplate JSON with nothing typed into it.
* A Databricks source-export notebook (``.py`` / ``.sql`` / ``.scala`` / ``.r``
  starting with ``# Databricks notebook source`` or the ``--``/``//`` variant)
  whose cells all have blank content once the header, ``# COMMAND ----------``
  separators, ``# DBTITLE`` lines, and bare ``# MAGIC`` markers are discounted --
  those are notebook scaffolding, not content a person wrote.
* Any other text file whose content is whitespace-only (pass ``--bytes-only``
  to turn this off and only match true zero-byte files).

A file that can't be decoded as UTF-8 (binary) and isn't zero bytes is never
considered empty -- there is no way to tell "no content" from "content this
tool can't read," so it errs toward keeping the file.

Deletion is opt-in: without ``--delete`` this only lists what it found.

For developers
--------------
    from empty_sweep import find_empty, delete_files

    report = find_empty([Path("workspace")], recursive=True)
    report.empty            # -> [Path(...), ...]
    deleted, errors = delete_files(report.empty)

Run the built-in checks with:  python3 empty_sweep.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "EmptyReport",
    "is_empty",
    "is_empty_ipynb",
    "is_empty_databricks_export",
    "find_empty",
    "delete_files",
    "DEFAULT_EXCLUDE_DIRS",
]

# Directories never worth walking into: VCS internals, caches, dependency trees.
DEFAULT_EXCLUDE_DIRS = frozenset(
    {".git", "__pycache__", ".ipynb_checkpoints", "venv", ".venv", "node_modules"}
)

# A Databricks source export starts with this header, spelled with the comment
# token for whichever language the notebook's default cell is in.
_DBX_HEADER_RE = re.compile(r"^(#|--|//) Databricks notebook source\s*$")
_COMMAND_SEP_RE = re.compile(r"^(#|--|//) COMMAND -+\s*$")
_DBTITLE_RE = re.compile(r"^(#|--|//) DBTITLE\b")
_MAGIC_LINE_RE = re.compile(r"^(#|--|//) MAGIC\b")

# A bare cell-language switch (``# MAGIC %sql`` with nothing else on the line)
# is scaffolding from picking the cell's language, not written content.
_MAGIC_LANGUAGE_KEYWORDS = {"python", "py", "sql", "md", "markdown", "sh", "scala", "r"}


def _looks_like_databricks_export(text: str) -> bool:
    first_line = text.splitlines()[0] if text else ""
    return bool(_DBX_HEADER_RE.match(first_line))


def is_empty_databricks_export(text: str) -> bool:
    """True if a Databricks source-export notebook has no real cell content.

    The header line, ``# COMMAND ----------`` separators, ``# DBTITLE`` lines,
    and a bare cell-language switch (``# MAGIC %sql`` with nothing else on the
    line) are scaffolding that Databricks writes into every export, empty or
    not -- they don't count as content. A ``# MAGIC`` line carrying anything
    else (e.g. ``# MAGIC # Heading``) does count.

    >>> is_empty_databricks_export("# Databricks notebook source\\n\\n# COMMAND ----------\\n\\n")
    True
    >>> is_empty_databricks_export("# Databricks notebook source\\n\\ndf = 1\\n")
    False
    """
    for line in text.splitlines():
        if not line.strip():
            continue
        if _DBX_HEADER_RE.match(line) or _COMMAND_SEP_RE.match(line) or _DBTITLE_RE.match(line):
            continue
        magic = _MAGIC_LINE_RE.match(line)
        if magic:
            remainder = line[magic.end():].strip().lstrip("%").lower()
            if remainder not in _MAGIC_LANGUAGE_KEYWORDS:
                return False
            continue
        return False
    return True


def is_empty_ipynb(text: str) -> bool:
    """True if a Jupyter notebook has no cells with non-blank source.

    Returns ``False`` (not empty) if the text isn't valid notebook JSON --
    a file this tool can't parse is never assumed to be safe to delete.

    >>> is_empty_ipynb('{"cells": []}')
    True
    >>> is_empty_ipynb('{"cells": [{"cell_type": "code", "source": ["   "]}]}')
    True
    >>> is_empty_ipynb('{"cells": [{"cell_type": "code", "source": ["1 + 1"]}]}')
    False
    """
    try:
        data = json.loads(text)
    except ValueError:
        return False
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list):
        return False
    for cell in cells:
        src = cell.get("source", "") if isinstance(cell, dict) else ""
        source = "".join(src) if isinstance(src, list) else str(src)
        if source.strip():
            return False
    return True


def is_empty(path: Path, *, blank_counts: bool = True) -> bool:
    """Decide whether ``path`` should be treated as an empty file.

    See the module docstring for exactly what counts. ``blank_counts=False``
    restricts the check to true zero-byte files (skips the whitespace-only
    and notebook-cell checks).
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size == 0:
        return True
    if not blank_counts:
        return False

    if path.suffix == ".ipynb":
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        return is_empty_ipynb(text)

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    if _looks_like_databricks_export(text):
        return is_empty_databricks_export(text)

    return not text.strip()


# --- walking ------------------------------------------------------------ #


def _iter_files(root: Path, *, recursive: bool, exclude_dirs: frozenset[str]) -> list[Path]:
    if root.is_file():
        return [root]
    pattern = "**/*" if recursive else "*"
    return sorted(
        path
        for path in root.glob(pattern)
        if path.is_file() and not any(part in exclude_dirs for part in path.parts)
    )


@dataclass
class EmptyReport:
    """Result of a :func:`find_empty` scan."""

    empty: list[Path] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def find_empty(
    paths: list[Path],
    *,
    recursive: bool = False,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    blank_counts: bool = True,
) -> EmptyReport:
    """Scan ``paths`` (files and/or directories) for empty files.

    Directories are scanned top-level only unless ``recursive=True``.
    """
    report = EmptyReport()
    seen: set[Path] = set()
    for root in paths:
        if not root.exists():
            report.errors.append((str(root), "path does not exist"))
            continue
        for path in _iter_files(root, recursive=recursive, exclude_dirs=exclude_dirs):
            if path in seen:
                continue
            seen.add(path)
            if is_empty(path, blank_counts=blank_counts):
                report.empty.append(path)
    report.empty.sort()
    return report


def delete_files(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Unlink each path in ``paths``. Returns ``(deleted, errors)``."""
    deleted: list[Path] = []
    errors: list[tuple[Path, str]] = []
    for path in paths:
        try:
            path.unlink()
            deleted.append(path)
        except OSError as exc:
            errors.append((path, str(exc)))
    return deleted, errors


# --- CLI ------------------------------------------------------------ #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="*", default=["."], help="files or directories to scan (default: .)")
    parser.add_argument("-r", "--recursive", action="store_true", help="recurse into subfolders")
    parser.add_argument(
        "--delete", action="store_true",
        help="actually delete the empty files found (default: dry run, list only)",
    )
    parser.add_argument("--exclude", action="append", default=[], metavar="DIR", help="extra directory name to skip (repeatable)")
    parser.add_argument(
        "--bytes-only", action="store_true",
        help="only match true zero-byte files; skip the whitespace-only and notebook-cell checks",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--selftest", action="store_true", help="run the built-in doctests and assertions and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    exclude_dirs = DEFAULT_EXCLUDE_DIRS | frozenset(args.exclude)
    report = find_empty(
        [Path(p) for p in args.paths],
        recursive=args.recursive,
        exclude_dirs=exclude_dirs,
        blank_counts=not args.bytes_only,
    )

    deleted: list[Path] = []
    delete_errors: list[tuple[Path, str]] = []
    if args.delete and report.empty:
        deleted, delete_errors = delete_files(report.empty)

    if args.json:
        payload = {
            "empty": [str(p) for p in report.empty],
            "scan_errors": [{"path": p, "message": m} for p, m in report.errors],
            "deleted": [str(p) for p in deleted],
            "delete_errors": [{"path": str(p), "message": m} for p, m in delete_errors],
        }
        print(json.dumps(payload, indent=2))
    else:
        for path, message in report.errors:
            print(f"error: {path}: {message}", file=sys.stderr)

        if not report.empty:
            print("no empty files found")
        elif args.delete:
            for path in deleted:
                print(f"deleted {path}")
            for path, message in delete_errors:
                print(f"error: could not delete {path}: {message}", file=sys.stderr)
            print(f"\n{len(deleted)} file(s) deleted")
        else:
            for path in report.empty:
                print(path)
            print(f"\n{len(report.empty)} empty file(s) found (dry run -- pass --delete to remove them)")

    return 1 if report.errors or delete_errors else 0


def _selftest() -> int:
    import doctest
    import tempfile

    failures, _ = doctest.testmod(verbose=False)

    assert is_empty_ipynb('{"cells": []}')
    assert is_empty_ipynb('{"cells": [{"cell_type": "markdown", "source": []}]}')
    assert not is_empty_ipynb('{"cells": [{"cell_type": "code", "source": ["x = 1"]}]}')
    assert not is_empty_ipynb("not json")

    dbx_empty = "# Databricks notebook source\n\n# COMMAND ----------\n\n# MAGIC %md\n"
    dbx_full = "# Databricks notebook source\n\ndf = spark.read.table('t')\n"
    assert is_empty_databricks_export(dbx_empty)
    assert not is_empty_databricks_export(dbx_full)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "zero.txt").write_bytes(b"")
        (root / "blank.txt").write_text("   \n\n")
        (root / "real.txt").write_text("hello")
        (root / "nb_empty.ipynb").write_text(json.dumps({"cells": [{"cell_type": "code", "source": []}]}))
        (root / "nb_real.ipynb").write_text(json.dumps({"cells": [{"cell_type": "code", "source": ["1+1"]}]}))
        (root / "job_empty.py").write_text(dbx_empty)
        (root / "job_real.py").write_text(dbx_full)
        sub = root / "sub"
        sub.mkdir()
        (sub / "nested_empty.txt").write_bytes(b"")

        top_level = find_empty([root])
        names = {p.name for p in top_level.empty}
        assert names == {"zero.txt", "blank.txt", "nb_empty.ipynb", "job_empty.py"}, names

        recursive = find_empty([root], recursive=True)
        assert "nested_empty.txt" in {p.name for p in recursive.empty}

        bytes_only = find_empty([root], recursive=True, blank_counts=False)
        assert {p.name for p in bytes_only.empty} == {"zero.txt", "nested_empty.txt"}

        deleted, errors = delete_files(top_level.empty)
        assert not errors
        assert not (root / "zero.txt").exists()
        assert (root / "real.txt").exists()

        missing = find_empty([root / "does-not-exist"])
        assert missing.errors and "does not exist" in missing.errors[0][1]

    if failures:
        print(f"{failures} doctest failure(s)", file=sys.stderr)
        return 1
    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
