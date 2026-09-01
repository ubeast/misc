"""Tests for :mod:`ic_filename_parser.cli`."""

from __future__ import annotations

from pathlib import Path

from ic_filename_parser.cli import iter_pdf_files, main, scan_directory
from ic_filename_parser.parser import COLUMNS


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_bytes(b"%PDF-1.4\n")


def test_iter_pdf_files_case_insensitive_and_sorted(tmp_path: Path) -> None:
    _touch(tmp_path, "b.PDF", "a.pdf", "notes.txt")
    assert [p.name for p in iter_pdf_files(tmp_path)] == ["a.pdf", "b.PDF"]


def test_scan_directory_empty_keeps_schema(tmp_path: Path) -> None:
    df = scan_directory(tmp_path)
    assert list(df.columns) == COLUMNS
    assert len(df) == 0


def test_scan_directory_parses_rows(tmp_path: Path) -> None:
    _touch(tmp_path, "004010M511_3_MA05.pdf", "random_document.pdf")
    df = scan_directory(tmp_path)
    assert len(df) == 2
    row = df.loc[df["FileName"] == "004010M511_3_MA05.pdf"].iloc[0]
    assert row["Transaction_Set"] == "511"


def test_main_writes_csv(tmp_path: Path, capsys) -> None:
    _touch(tmp_path, "004010M511_3_MA05.pdf")
    out = tmp_path / "out.csv"
    rc = main([str(tmp_path), "-o", str(out)])
    assert rc == 0
    text = out.read_text()
    assert text.splitlines()[0] == ",".join(COLUMNS)
    assert "004010M511_3_MA05.pdf" in text
