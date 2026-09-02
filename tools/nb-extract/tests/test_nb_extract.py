"""Tests for ``nb_extract``.

Importable as ``nb_extract`` thanks to ``tests/conftest.py`` putting the tool
directory on ``sys.path``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import nb_extract as nbx

SCRIPT = Path(__file__).resolve().parent.parent / "nb_extract.py"


IPYNB = json.dumps(
    {
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n"]},
            {"cell_type": "code", "source": ["import os\n"], "metadata": {"tags": ["setup"]}},
            {"cell_type": "code", "source": ["%%sql\nSELECT 1\nFROM t\n"]},
            {"cell_type": "code", "source": ["%md\nnotes\n"]},
            {"cell_type": "code", "source": ["print('hi')\n"]},
        ],
    }
)

DBX = (
    "# Databricks notebook source\n"
    "# MAGIC %md\n"
    "# MAGIC # Heading\n"
    "\n"
    "# COMMAND ----------\n"
    "\n"
    '# DBTITLE 1,"Load"\n'
    "df = spark.read.table('t')\n"
    "\n"
    "# COMMAND ----------\n"
    "\n"
    "# MAGIC %sql\n"
    "# MAGIC SELECT count(*)\n"
    "# MAGIC FROM t\n"
)


# --- parsing: ipynb --------------------------------------------------- #


def test_parse_ipynb_languages_and_tags() -> None:
    cells = nbx.parse_notebook(IPYNB)
    assert [c.language for c in cells] == ["markdown", "python", "sql", "markdown", "python"]
    assert cells[1].tags == ["setup"]
    assert cells[2].source == "SELECT 1\nFROM t"     # %%sql line stripped
    assert cells[3].cell_type == "markdown"           # %md promotes to markdown


def test_parse_ipynb_from_path(tmp_path: Path) -> None:
    path = tmp_path / "n.ipynb"
    path.write_text(IPYNB)
    assert len(nbx.parse_notebook(path)) == 5


def test_notebook_language_defaults_to_python_when_missing() -> None:
    cells = nbx.parse_notebook('{"cells": [{"cell_type": "code", "source": ["x = 1"]}]}')
    assert cells[0].language == "python"


# --- parsing: Databricks ------------------------------------------- #


def test_parse_databricks_decodes_magic_cells() -> None:
    cells = nbx.parse_notebook(DBX)
    assert [c.language for c in cells] == ["markdown", "python", "sql"]
    assert cells[0].source == "# Heading"
    assert cells[1].title == "Load"
    assert "spark.read" in cells[1].source
    assert cells[2].source == "SELECT count(*)\nFROM t"


def test_parse_databricks_sql_export_uses_dashes() -> None:
    text = (
        "-- Databricks notebook source\n"
        "SELECT 1\n"
        "\n"
        "-- COMMAND ----------\n"
        "\n"
        "-- MAGIC %python\n"
        "-- MAGIC print(1)\n"
    )
    cells = nbx.parse_notebook(text)
    assert [c.language for c in cells] == ["sql", "python"]
    assert cells[1].source == "print(1)"


def test_format_detection_prefers_ipynb_for_json() -> None:
    assert nbx._looks_like_ipynb(IPYNB) is True
    assert nbx._looks_like_ipynb(DBX) is False


# --- selection ---------------------------------------------------- #


@pytest.fixture
def cells() -> list[nbx.Cell]:
    return nbx.parse_notebook(IPYNB)


def test_select_defaults_to_code_cells(cells: list[nbx.Cell]) -> None:
    got = nbx.select(cells)
    assert [c.language for c in got] == ["python", "sql", "python"]


def test_select_by_language(cells: list[nbx.Cell]) -> None:
    assert [c.source for c in nbx.select(cells, languages=["sql"])] == ["SELECT 1\nFROM t"]


def test_select_by_tag(cells: list[nbx.Cell]) -> None:
    assert len(nbx.select(cells, tags=["setup"])) == 1


def test_select_by_grep(cells: list[nbx.Cell]) -> None:
    assert len(nbx.select(cells, grep=r"SELECT")) == 1


def test_select_by_index(cells: list[nbx.Cell]) -> None:
    assert [c.index for c in nbx.select(cells, indices={0, 4})] == [0, 4]


def test_select_combines_selectors_with_and(cells: list[nbx.Cell]) -> None:
    assert nbx.select(cells, languages=["python"], tags=["setup"]) == [cells[1]]


def test_select_include_markdown(cells: list[nbx.Cell]) -> None:
    assert len(nbx.select(cells, include_markdown=True)) == 5


def test_parse_index_spec() -> None:
    assert nbx.parse_index_spec("0,2,5-7") == {0, 2, 5, 6, 7}
    assert nbx.parse_index_spec("3") == {3}
    assert nbx.parse_index_spec("") == set()


# --- rendering -------------------------------------------------- #


def test_render_with_separators(cells: list[nbx.Cell]) -> None:
    out = nbx.render(nbx.select(cells, languages=["sql"]))
    assert out.startswith("-- cell 2 [sql]")
    assert "SELECT 1" in out


def test_render_without_separators(cells: list[nbx.Cell]) -> None:
    out = nbx.render(nbx.select(cells, languages=["python"]), separators=False)
    assert "cell" not in out
    assert [ln for ln in out.splitlines() if ln] == ["import os", "print('hi')"]


def test_extension_for() -> None:
    assert nbx.extension_for("sql") == ".sql"
    assert nbx.extension_for("python") == ".py"
    assert nbx.extension_for("unknown") == ".txt"


# --- CLI ------------------------------------------------------ #


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


@pytest.fixture
def nb_file(tmp_path: Path) -> Path:
    path = tmp_path / "nb.ipynb"
    path.write_text(IPYNB)
    return path


def test_cli_selftest_passes() -> None:
    assert _run("--selftest").returncode == 0


def test_cli_text_output(nb_file: Path) -> None:
    result = _run(str(nb_file), "--lang", "sql")
    assert result.returncode == 0
    assert "SELECT 1" in result.stdout
    assert "-- cell 2" in result.stdout


def test_cli_json_output(nb_file: Path) -> None:
    payload = json.loads(_run(str(nb_file), "--format", "json").stdout)
    assert [c["language"] for c in payload] == ["python", "sql", "python"]


def test_cli_count(nb_file: Path) -> None:
    out = _run(str(nb_file), "--format", "count", "--include-markdown").stdout
    assert "total" in out
    assert "markdown" in out


def test_cli_out_file(nb_file: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out.sql"
    result = _run(str(nb_file), "--lang", "sql", "--out", str(dest))
    assert result.returncode == 0
    assert "SELECT 1" in dest.read_text()


def test_cli_split(nb_file: Path, tmp_path: Path) -> None:
    outdir = tmp_path / "split"
    _run(str(nb_file), "--split", str(outdir))
    names = sorted(p.name for p in outdir.iterdir())
    assert names == ["000_python.py", "001_sql.sql", "002_python.py"]


def test_cli_by_language(nb_file: Path, tmp_path: Path) -> None:
    outdir = tmp_path / "bylang"
    _run(str(nb_file), "--by-language", str(outdir), "--include-markdown")
    names = sorted(p.name for p in outdir.iterdir())
    assert names == ["markdown.md", "python.py", "sql.sql"]


def test_cli_missing_file() -> None:
    assert _run("/no/such/nb.ipynb").returncode == 2


def test_cli_requires_notebook() -> None:
    assert _run().returncode == 2
