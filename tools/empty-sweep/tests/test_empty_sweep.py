"""Tests for ``empty_sweep``.

Importable as ``empty_sweep`` thanks to ``tests/conftest.py`` putting the tool
directory on ``sys.path``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import empty_sweep as es

SCRIPT = Path(__file__).resolve().parent.parent / "empty_sweep.py"

DBX_EMPTY = "# Databricks notebook source\n\n# COMMAND ----------\n\n# MAGIC %md\n"
DBX_WITH_CODE = "# Databricks notebook source\n\ndf = spark.read.table('t')\n"
DBX_WITH_MAGIC_CONTENT = (
    "# Databricks notebook source\n\n# MAGIC %md\n# MAGIC # Heading\n"
)


# --- unit: notebook emptiness ----------------------------------------- #


def test_is_empty_ipynb_no_cells() -> None:
    assert es.is_empty_ipynb(json.dumps({"cells": []}))


def test_is_empty_ipynb_blank_source() -> None:
    nb = {"cells": [{"cell_type": "code", "source": ["   ", "\n"]}]}
    assert es.is_empty_ipynb(json.dumps(nb))


def test_is_empty_ipynb_real_content() -> None:
    nb = {"cells": [{"cell_type": "code", "source": ["1 + 1"]}]}
    assert not es.is_empty_ipynb(json.dumps(nb))


def test_is_empty_ipynb_invalid_json_is_not_empty() -> None:
    assert not es.is_empty_ipynb("{not json")


def test_is_empty_ipynb_missing_cells_key_is_not_empty() -> None:
    assert not es.is_empty_ipynb(json.dumps({"metadata": {}}))


def test_is_empty_databricks_export_scaffolding_only() -> None:
    assert es.is_empty_databricks_export(DBX_EMPTY)


def test_is_empty_databricks_export_with_code() -> None:
    assert not es.is_empty_databricks_export(DBX_WITH_CODE)


def test_is_empty_databricks_export_with_magic_content() -> None:
    assert not es.is_empty_databricks_export(DBX_WITH_MAGIC_CONTENT)


# --- unit: is_empty dispatch ------------------------------------------ #


def test_is_empty_zero_byte_file(tmp_path: Path) -> None:
    f = tmp_path / "z.txt"
    f.write_bytes(b"")
    assert es.is_empty(f)


def test_is_empty_whitespace_only_file(tmp_path: Path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("   \n\t\n")
    assert es.is_empty(f)


def test_is_empty_bytes_only_ignores_whitespace(tmp_path: Path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("   \n")
    assert not es.is_empty(f, blank_counts=False)


def test_is_empty_real_content_file(tmp_path: Path) -> None:
    f = tmp_path / "r.txt"
    f.write_text("hello")
    assert not es.is_empty(f)


def test_is_empty_binary_nonzero_is_not_empty(tmp_path: Path) -> None:
    f = tmp_path / "b.bin"
    f.write_bytes(b"\xff\xfe\x00\x01")
    assert not es.is_empty(f)


def test_is_empty_ipynb_dispatch(tmp_path: Path) -> None:
    f = tmp_path / "nb.ipynb"
    f.write_text(json.dumps({"cells": [{"cell_type": "code", "source": []}]}))
    assert es.is_empty(f)


def test_is_empty_databricks_export_dispatch(tmp_path: Path) -> None:
    f = tmp_path / "job.py"
    f.write_text(DBX_EMPTY)
    assert es.is_empty(f)


def test_is_empty_missing_file_is_not_empty(tmp_path: Path) -> None:
    assert not es.is_empty(tmp_path / "nope.txt")


# --- unit: find_empty walking ------------------------------------------ #


def _make_tree(root: Path) -> None:
    (root / "zero.txt").write_bytes(b"")
    (root / "blank.txt").write_text("  \n")
    (root / "real.txt").write_text("hello")
    (root / "nb_empty.ipynb").write_text(json.dumps({"cells": []}))
    (root / "nb_real.ipynb").write_text(json.dumps({"cells": [{"cell_type": "code", "source": ["x"]}]}))
    (root / "job_empty.py").write_text(DBX_EMPTY)
    (root / "job_real.py").write_text(DBX_WITH_CODE)
    (root / ".git").mkdir()
    (root / ".git" / "ignored_empty.txt").write_bytes(b"")
    sub = root / "sub"
    sub.mkdir()
    (sub / "nested_empty.txt").write_bytes(b"")


def test_find_empty_top_level_only(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    report = es.find_empty([tmp_path])
    names = {p.name for p in report.empty}
    assert names == {"zero.txt", "blank.txt", "nb_empty.ipynb", "job_empty.py"}


def test_find_empty_recursive(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    report = es.find_empty([tmp_path], recursive=True)
    names = {p.name for p in report.empty}
    assert "nested_empty.txt" in names
    assert "ignored_empty.txt" not in names  # .git excluded by default


def test_find_empty_bytes_only(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    report = es.find_empty([tmp_path], recursive=True, blank_counts=False)
    names = {p.name for p in report.empty}
    assert names == {"zero.txt", "nested_empty.txt"}


def test_find_empty_missing_path_is_an_error_not_a_crash(tmp_path: Path) -> None:
    report = es.find_empty([tmp_path / "nope"])
    assert report.empty == []
    assert report.errors and "does not exist" in report.errors[0][1]


def test_find_empty_custom_exclude(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / "skipme").mkdir()
    (tmp_path / "skipme" / "e.txt").write_bytes(b"")
    report = es.find_empty([tmp_path], recursive=True, exclude_dirs=es.DEFAULT_EXCLUDE_DIRS | {"skipme"})
    assert "e.txt" not in {p.name for p in report.empty}


# --- unit: delete_files -------------------------------------------------- #


def test_delete_files(tmp_path: Path) -> None:
    f = tmp_path / "z.txt"
    f.write_bytes(b"")
    deleted, errors = es.delete_files([f])
    assert deleted == [f]
    assert not errors
    assert not f.exists()


def test_delete_files_reports_errors_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "nope.txt"
    deleted, errors = es.delete_files([missing])
    assert deleted == []
    assert errors and errors[0][0] == missing


# --- CLI ----------------------------------------------------------------- #


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_cli_selftest_passes() -> None:
    assert _run("--selftest").returncode == 0


def test_cli_dry_run_lists_and_keeps_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = _run(str(tmp_path))
    assert result.returncode == 0
    assert "zero.txt" in result.stdout
    assert "dry run" in result.stdout
    assert (tmp_path / "zero.txt").exists()


def test_cli_delete_removes_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = _run(str(tmp_path), "--delete")
    assert result.returncode == 0
    assert not (tmp_path / "zero.txt").exists()
    assert (tmp_path / "real.txt").exists()


def test_cli_recursive_flag(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = _run(str(tmp_path), "-r", "--json")
    payload = json.loads(result.stdout)
    assert any(p.endswith("nested_empty.txt") for p in payload["empty"])


def test_cli_json_output_shape(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    payload = json.loads(_run(str(tmp_path), "--json").stdout)
    assert set(payload) == {"empty", "scan_errors", "deleted", "delete_errors"}
    assert payload["deleted"] == []


def test_cli_no_empty_files_message(tmp_path: Path) -> None:
    (tmp_path / "real.txt").write_text("content")
    result = _run(str(tmp_path))
    assert "no empty files found" in result.stdout


def test_cli_missing_path_nonzero_exit(tmp_path: Path) -> None:
    result = _run(str(tmp_path / "nope"))
    assert result.returncode == 1
    assert "does not exist" in result.stderr
