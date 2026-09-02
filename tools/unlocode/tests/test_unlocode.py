"""Tests for ``unlocode`` (UN/LOCODE format validation and parsing).

Importable as ``unlocode`` thanks to ``tests/conftest.py`` putting the tool
directory on ``sys.path``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import unlocode

SCRIPT = Path(__file__).resolve().parent.parent / "unlocode.py"


# --- is_valid --------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("USNYC", True),
        ("US NYC", True),
        ("us nyc", True),
        ("us-nyc", True),
        ("DEHAM", True),
        ("NLRTM", True),
        ("SGSIN", True),
        ("XZUKN", True),        # international-waters special code
        ("USNY1", False),       # digit 1 disallowed in location
        ("USNY0", False),       # digit 0 disallowed
        ("ZZNYC", False),       # unknown country
        ("US", False),
        ("USNYCX", False),
        ("", False),
        ("garbage", False),
    ],
)
def test_is_valid(raw: str, expected: bool) -> None:
    assert unlocode.is_valid(raw) is expected


def test_is_valid_never_raises() -> None:
    for junk in ["", "!!!", "\x00", "U" * 20, "12345"]:
        assert unlocode.is_valid(junk) is False


# --- parse ---------------------------------------------------------- #


def test_parse_breaks_out_the_halves() -> None:
    loc = unlocode.parse("  de-ham ")
    assert (loc.country, loc.location) == ("DE", "HAM")
    assert loc.normalized == "DEHAM"
    assert loc.display == "DE HAM"
    assert loc.country_is_known is True
    assert loc.location_is_wellformed is True
    assert loc.is_valid is True


def test_parse_unknown_country_is_flagged_not_rejected() -> None:
    loc = unlocode.parse("ZZABC")
    assert loc.country_is_known is False
    assert loc.location_is_wellformed is True
    assert loc.is_valid is False


def test_parse_special_country_code_xz() -> None:
    loc = unlocode.parse("XZ UKN")
    assert loc.country_is_known is True
    assert loc.country_note is not None
    assert loc.is_valid is True


def test_parse_accepts_digits_2_through_9_in_location() -> None:
    loc = unlocode.parse("US2A9")
    assert loc.location == "2A9"
    assert loc.location_is_wellformed is True


@pytest.mark.parametrize(
    "bad",
    ["", "US", "USNYCC", "U1NYC", "US NY1", "USN.C", "12NYC", "USNY 1"],
)
def test_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        unlocode.parse(bad)


def test_parse_is_immutable() -> None:
    loc = unlocode.parse("USNYC")
    with pytest.raises(Exception):
        loc.country = "XX"  # type: ignore[misc]


# --- country list ------------------------------------------------- #


def test_country_list_is_reasonable() -> None:
    countries = unlocode.known_countries()
    assert {"US", "DE", "CN", "GB", "SG", "NL", "XZ"} <= countries
    assert len(countries) > 240
    assert all(len(code) == 2 and code.isupper() for code in countries)


# --- CLI --------------------------------------------------------- #


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_cli_selftest_passes() -> None:
    assert _run("--selftest").returncode == 0


def test_cli_countries_dump() -> None:
    result = _run("--countries")
    assert result.returncode == 0
    assert "US" in result.stdout.split()


def test_cli_exit_codes() -> None:
    assert _run("USNYC").returncode == 0        # valid
    assert _run("ZZNYC").returncode == 1        # well-formed, unknown country
    assert _run("NOPE").returncode == 2         # unparseable
    assert _run().returncode == 2               # no args


def test_cli_exit_code_is_worst_of_batch() -> None:
    assert _run("USNYC", "ZZNYC").returncode == 1
    assert _run("USNYC", "NOPE").returncode == 2


def test_cli_json_output() -> None:
    result = _run("de ham", "ZZABC", "--json")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 2
    good, bad = (json.loads(line) for line in lines)
    assert good["normalized"] == "DEHAM"
    assert good["is_valid"] is True
    assert bad["country_is_known"] is False
    assert bad["is_valid"] is False


def test_cli_human_output_mentions_validity() -> None:
    assert "valid" in _run("USNYC").stdout
