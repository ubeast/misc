"""Tests for ``colname_normalize``.

Importable as ``colname_normalize`` thanks to ``tests/conftest.py`` putting the
tool directory on ``sys.path``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import colname_normalize as cn

SCRIPT = Path(__file__).resolve().parent.parent / "colname_normalize.py"


# --- normalize ----------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Unit Price ($)", "unit_price_usd"),
        ("customerID", "customer_id"),
        ("customer_id", "customer_id"),          # already clean -> unchanged
        ("Ship-To  Country", "ship_to_country"),
        ("  notes  ", "notes"),
        ("HTTPServerError", "http_server_error"),
        ("userId", "user_id"),
        ("v2Model", "v2_model"),
        ("2020 Revenue", "_2020_revenue"),
        ("Ünit Prïce", "unit_price"),
        ("Total (USD)", "total_usd"),
        ("weight / kg", "weight_kg"),
        ("A & B", "a_and_b"),
        ("temp °C", "temp_deg_c"),
        ("", "column"),
        ("---", "column"),
        ("   ", "column"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert cn.normalize(raw) == expected


def test_normalize_is_idempotent() -> None:
    probes = [
        "Weird Name #1", "col-2", "ABCDef", "  ", "é", "Total (USD)",
        "2020", "from", "SELECT", "already_ok", "MixOf99Things",
    ]
    for probe in probes:
        once = cn.normalize(probe)
        assert cn.normalize(once) == once, probe


def test_python_keyword_gets_trailing_underscore() -> None:
    assert cn.normalize("from") == "from_"
    assert cn.normalize("class") == "class_"
    assert cn.normalize("None") == "none"  # only lowercased forms are keywords


def test_sql_reserved_is_opt_in() -> None:
    assert cn.normalize("select") == "select"
    opts = cn.Options(avoid_sql_keywords=True)
    assert cn.normalize("select", opts) == "select_"
    assert cn.normalize("Group By", opts) == "group_by"  # phrase, not a bare keyword


def test_options_toggle_behaviour() -> None:
    assert cn.normalize("customerID", cn.Options(split_camel_case=False)) == "customerid"
    assert cn.normalize("50%", cn.Options(replace_symbols=False)) == "_50"
    assert cn.normalize("CustomerID", cn.Options(lower=False)) == "Customer_ID"
    assert cn.normalize("2020", cn.Options(digit_prefix="col_")) == "col_2020"


def test_max_length_truncates_without_trailing_underscore() -> None:
    assert cn.normalize("a_very_long_name", cn.Options(max_length=6)) == "a_very"
    assert cn.normalize("ab_cd_ef", cn.Options(max_length=3)) == "ab"  # rstrip the "_"


# --- normalize_all / build_mapping -------------------------------------- #


def test_normalize_all_dedupes_in_order() -> None:
    assert cn.normalize_all(["Total", "total", "TOTAL"]) == ["total", "total_2", "total_3"]
    assert cn.normalize_all(["a", "b", "a"]) == ["a", "b", "a_2"]
    assert cn.normalize_all([]) == []


def test_normalize_all_dedup_suffix_can_itself_collide() -> None:
    assert cn.normalize_all(["dup", "dup", "dup_2"]) == ["dup", "dup_2", "dup_2_2"]


def test_normalize_all_dedup_respects_max_length() -> None:
    assert cn.normalize_all(["abcdef", "abcdef"], cn.Options(max_length=6)) == ["abcdef", "abcd_2"]


def test_build_mapping() -> None:
    assert cn.build_mapping(["A B", "A-B"]) == {"A B": "a_b", "A-B": "a_b_2"}


def test_result_is_always_a_valid_python_identifier() -> None:
    junk = ["", "1", "!", "class", "def", "  spaces  ", "é", "99 bottles", "---", "a.b.c"]
    for name in cn.normalize_all(junk):
        assert name.isidentifier(), name


# --- CLI --------------------------------------------------------------- #


def _run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        input=stdin,
    )


def test_cli_selftest_passes() -> None:
    assert _run("--selftest").returncode == 0


def test_cli_positional_names() -> None:
    result = _run("Unit Price ($)", "customerID")
    assert result.returncode == 0
    assert "unit_price_usd" in result.stdout
    assert "customer_id" in result.stdout


def test_cli_json_output() -> None:
    result = _run("A B", "A-B", "--json")
    payload = json.loads(result.stdout)
    assert payload == [
        {"original": "A B", "normalized": "a_b"},
        {"original": "A-B", "normalized": "a_b_2"},
    ]


def test_cli_names_only() -> None:
    result = _run("Foo Bar", "Baz", "--names-only")
    assert result.stdout.splitlines() == ["foo_bar", "baz"]


def test_cli_check_mode_exit_codes() -> None:
    assert _run("already_ok", "--check").returncode == 0
    dirty = _run("Not OK", "--check")
    assert dirty.returncode == 1
    assert "Not OK" in dirty.stderr


def test_cli_reads_csv_header_from_stdin() -> None:
    result = _run("--csv", "-", stdin="First Name,Last Name,Age\nfoo,bar,1\n")
    assert result.stdout.splitlines() == [
        "First Name  first_name",
        "Last Name   last_name",
        "Age         age",
    ]


def test_cli_reads_names_from_stdin_lines() -> None:
    result = _run("--stdin", "--names-only", stdin="Col One\nCol Two\n\n")
    assert result.stdout.splitlines() == ["col_one", "col_two"]


def test_cli_requires_input() -> None:
    assert _run().returncode == 2
