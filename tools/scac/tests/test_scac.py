"""Tests for ``scac`` (Standard Carrier Alpha Code format + classification).

Importable as ``scac`` thanks to ``tests/conftest.py`` putting the tool
directory on ``sys.path``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scac

SCRIPT = Path(__file__).resolve().parent.parent / "scac.py"


# --- is_valid ---------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("MAEU", True),
        ("maeu", True),
        (" m a e u ", True),
        ("m-a-e-u", True),
        ("FX", True),
        ("ABCD", True),
        ("A", False),
        ("ABCDE", False),
        ("AB1", False),
        ("AB.", False),
        ("", False),
    ],
)
def test_is_valid(raw: str, expected: bool) -> None:
    assert scac.is_valid(raw) is expected


def test_is_valid_never_raises() -> None:
    for junk in ["", "!!!", "\x00", "A" * 99, "12345"]:
        assert scac.is_valid(junk) is False


# --- parse ----------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, base, suffix",
    [
        ("MAEU", "MAE", "U"),
        ("SCAX", "SCA", "X"),
        ("CASZ", "CAS", "Z"),
        ("CSXT", "CSXT", None),   # ends in T -> no reserved suffix
        ("UPSX", "UPS", "X"),
        ("FX", "FX", None),       # only 1 letter would remain in front
        ("XU", "XU", None),       # base "X" too short to split
        ("ABCD", "ABCD", None),
    ],
)
def test_parse_splits_reserved_suffix(raw: str, base: str, suffix: str | None) -> None:
    parsed = scac.parse(raw)
    assert parsed.base == base
    assert parsed.suffix == suffix
    assert parsed.code == raw.upper()
    assert parsed.is_valid is True


def test_parse_suffix_meaning() -> None:
    assert scac.parse("MAEU").suffix_meaning == scac.RESERVED_SUFFIXES["U"]
    assert scac.parse("CSXT").suffix_meaning is None


def test_parse_normalizes_case_and_separators() -> None:
    assert scac.parse("  hl-xu ").code == "HLXU"


def test_parse_length() -> None:
    assert scac.parse("FX").length == 2
    assert scac.parse("ABCD").length == 4


@pytest.mark.parametrize("bad", ["", "A", "ABCDE", "AB1", "12", "A B C D E", "@@"])
def test_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        scac.parse(bad)


def test_parse_is_immutable() -> None:
    parsed = scac.parse("MAEU")
    with pytest.raises(Exception):
        parsed.code = "XXXX"  # type: ignore[misc]


# --- CLI ------------------------------------------------------------ #


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_cli_selftest_passes() -> None:
    assert _run("--selftest").returncode == 0


def test_cli_suffix_table() -> None:
    result = _run("--suffixes")
    assert result.returncode == 0
    assert "...U" in result.stdout
    assert "...X" in result.stdout
    assert "...Z" in result.stdout


def test_cli_exit_codes() -> None:
    assert _run("MAEU").returncode == 0
    assert _run("NOPE!").returncode == 2
    assert _run().returncode == 2


def test_cli_json_output() -> None:
    result = _run("maeu", "csxt", "--json")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["base"] == "MAE"
    assert first["suffix"] == "U"
    assert second["suffix"] is None
    assert second["is_valid"] is True


def test_cli_human_output() -> None:
    out = _run("SCAX").stdout
    assert "base   : SCA" in out
    assert "railroad" in out
