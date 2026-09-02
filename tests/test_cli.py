"""Tests for the directory scan and CSV/CLI helpers in ``ic_filename_parser``."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ic_filename_parser import (
    COLUMNS,
    iter_pdf_files,
    main,
    rows_to_csv,
    scan_directory,
)


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_bytes(b"%PDF-1.4\n")


def test_iter_pdf_files_case_insensitive_and_sorted(tmp_path: Path) -> None:
    _touch(tmp_path, "b.PDF", "a.pdf", "notes.txt")
    assert [p.name for p in iter_pdf_files(tmp_path)] == ["a.pdf", "b.PDF"]


def test_scan_directory_returns_records(tmp_path: Path) -> None:
    _touch(tmp_path, "004010M511_3_MA05.pdf", "random_document.pdf")
    rows = scan_directory(tmp_path)
    assert len(rows) == 2
    by_name = {r.FileName: r for r in rows}
    assert by_name["004010M511_3_MA05.pdf"].Transaction_Set == "511"


def test_rows_to_csv_empty_keeps_schema(tmp_path: Path) -> None:
    reader = csv.reader(io.StringIO(rows_to_csv(scan_directory(tmp_path))))
    assert next(reader) == COLUMNS
    assert next(reader, None) is None


def test_main_writes_csv(tmp_path: Path) -> None:
    _touch(tmp_path, "004010M511_3_MA05.pdf")
    out = tmp_path / "out.csv"
    rc = main([str(tmp_path), "-o", str(out)])
    assert rc == 0
    lines = out.read_text().splitlines()
    assert lines[0] == ",".join(COLUMNS)
    assert any("004010M511_3_MA05.pdf" in line for line in lines[1:])
