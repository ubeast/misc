"""Tests for ``imports_scan``.

Importable as ``imports_scan`` thanks to ``tests/conftest.py`` putting the tool
directory on ``sys.path``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import imports_scan as isc

SCRIPT = Path(__file__).resolve().parent.parent / "imports_scan.py"


# --- unit ------------------------------------------------------------ #


def test_distribution_for() -> None:
    assert isc.distribution_for("sklearn") == "scikit-learn"
    assert isc.distribution_for("cv2") == "opencv-python"
    assert isc.distribution_for("pandas") == "pandas"


@pytest.mark.parametrize(
    "top, level, local, expected",
    [
        ("os", 0, frozenset(), "stdlib"),
        ("pathlib", 0, frozenset(), "stdlib"),
        ("numpy", 0, frozenset(), "third_party"),
        ("mymod", 0, frozenset({"mymod"}), "local"),
        ("anything", 1, frozenset(), "relative"),
        ("os", 0, frozenset({"os"}), "local"),  # local shadows stdlib
    ],
)
def test_classify_module(top: str, level: int, local: frozenset[str], expected: str) -> None:
    assert isc.classify_module(top, level, local) == expected


def test_extract_imports_via_ast() -> None:
    src = "import os, sys\nfrom pandas import DataFrame\nimport a.b.c\n"
    assert isc._extract_imports(src) == [("os", 0), ("sys", 0), ("pandas", 0), ("a", 0)]


def test_extract_imports_relative() -> None:
    assert ("", 1) in isc._extract_imports("from . import x")
    assert ("pkg", 2) in isc._extract_imports("from ..pkg import y")


def test_extract_imports_regex_fallback_on_syntax_error() -> None:
    src = "def broken(:\n    import numpy\n    from requests import get\n"
    assert isc._extract_imports(src) == [("numpy", 0), ("requests", 0)]


def test_pip_install_specs() -> None:
    assert isc._pip_install_specs("%pip install rich==13.0 pandas") == ["rich==13.0", "pandas"]
    assert isc._pip_install_specs("!pip install -q boto3") == ["boto3"]
    assert isc._pip_install_specs("# MAGIC %pip install scikit-learn") == ["scikit-learn"]
    assert isc._pip_install_specs("%conda install numpy") == ["numpy"]
    assert isc._pip_install_specs("print('pip install fake')") == []


# --- scan (fixtures) ----------------------------------------------- #


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "helpers.py").write_text("VALUE = 1\n")
    (tmp_path / "job.py").write_text(
        textwrap.dedent(
            """\
            import os
            import pandas as pd
            import helpers
            from . import sibling
            if True:
                import requests
            """
        )
    )
    notebook = {
        "cells": [
            {"cell_type": "code", "source": ["%pip install requests boto3\n", "import requests\n"]},
            {"cell_type": "code", "source": "import numpy as np\nimport sklearn\n%matplotlib inline\n"},
            {"cell_type": "markdown", "source": ["# heading"]},
        ]
    }
    (tmp_path / "explore.ipynb").write_text(json.dumps(notebook))
    checkpoints = tmp_path / ".ipynb_checkpoints"
    checkpoints.mkdir()
    (checkpoints / "explore-checkpoint.ipynb").write_text(
        '{"cells": [{"cell_type": "code", "source": ["import should_be_ignored\n"]}]}'
    )
    return tmp_path


def test_scan_requirements(project: Path) -> None:
    result = isc.scan([project])
    assert result.requirements() == ["boto3", "numpy", "pandas", "requests", "scikit-learn"]


def test_scan_buckets(project: Path) -> None:
    result = isc.scan([project])
    assert set(result.stdlib) == {"os"}
    assert set(result.local) == {"helpers"}
    assert "." in result.relative
    assert set(result.third_party) == {"pandas", "requests", "numpy", "sklearn"}
    assert result.files_scanned == 3  # checkpoint dir excluded


def test_scan_records_pip_installs(project: Path) -> None:
    result = isc.scan([project])
    assert set(result.pip_installs) == {"requests", "boto3"}


def test_scan_reports_missing_path(tmp_path: Path) -> None:
    result = isc.scan([tmp_path / "nope.py"])
    assert result.errors
    assert result.requirements() == []


def test_scan_bad_notebook_json_is_an_error_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "broken.ipynb").write_text("{not json")
    result = isc.scan([tmp_path])
    assert any("broken.ipynb" in f for f, _ in result.errors)


def test_scan_single_file(project: Path) -> None:
    # Scanning one file in isolation: siblings are not on disk for us, so a
    # sibling import (helpers) cannot be recognized as local.
    result = isc.scan([project / "job.py"])
    assert "pandas" in result.third_party
    assert "helpers" in result.third_party  # misclassified without the sibling in scope
    assert result.local == {}
    assert "." in result.relative


# --- CLI --------------------------------------------------------- #


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_cli_selftest_passes() -> None:
    assert _run("--selftest").returncode == 0


def test_cli_default_output_is_requirements(project: Path) -> None:
    result = _run(str(project))
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["boto3", "numpy", "pandas", "requests", "scikit-learn"]


def test_cli_json(project: Path) -> None:
    payload = json.loads(_run(str(project), "--json").stdout)
    assert payload["requirements"] == ["boto3", "numpy", "pandas", "requests", "scikit-learn"]
    assert "os" in payload["stdlib"]
    assert payload["files_scanned"] == 3


def test_cli_all_flag_lists_stdlib(project: Path) -> None:
    out = _run(str(project), "--all").stdout
    assert "# stdlib" in out
    assert "# local" in out


def test_cli_strict_exit_code(tmp_path: Path) -> None:
    (tmp_path / "broken.ipynb").write_text("{not json")
    assert _run(str(tmp_path)).returncode == 0
    assert _run(str(tmp_path), "--strict").returncode == 1


def test_cli_with_files(project: Path) -> None:
    out = _run(str(project), "--with-files").stdout
    assert "pandas  # " in out
    assert "job.py" in out
