"""Tests for :mod:`ic_filename_parser.dates`."""

from __future__ import annotations

import pytest

from ic_filename_parser.dates import (
    DatePrecision,
    format_ebso_date,
    parse_ebso_date,
)


@pytest.mark.parametrize(
    "raw, iso, precision",
    [
        ("20220803", "2022-08-03", DatePrecision.DAY),
        ("2022-08-03", "2022-08-03", DatePrecision.DAY),
        ("Aug152024", "2024-08-15", DatePrecision.DAY),
        ("August52024", "2024-08-05", DatePrecision.DAY),
        ("Aug1524", "2024-08-15", DatePrecision.DAY),          # DDYY, 2-digit year
        ("Aug2024", "2024-08-01", DatePrecision.MONTH),         # month + year only
        ("August2024", "2024-08-01", DatePrecision.MONTH),
        ("Sept2024", "2024-09-01", DatePrecision.MONTH),
        ("_Aug_2024_", "2024-08-01", DatePrecision.MONTH),      # separators stripped
    ],
)
def test_parses(raw: str, iso: str, precision: DatePrecision) -> None:
    parsed = parse_ebso_date(raw)
    assert parsed is not None
    assert parsed.iso == iso
    assert parsed.precision is precision


def test_month_year_not_shadowed_by_day_pattern() -> None:
    """Regression: 'Aug2024' used to be read as the 20th of August."""
    parsed = parse_ebso_date("Aug2024")
    assert parsed is not None
    assert parsed.iso == "2024-08-01"
    assert parsed.precision is DatePrecision.MONTH


def test_implausible_four_digits_read_as_ddyy() -> None:
    # "1524" is not a plausible publication year -> 15th, year 2024.
    parsed = parse_ebso_date("Aug1524")
    assert parsed is not None
    assert parsed.iso == "2024-08-15"


@pytest.mark.parametrize(
    "raw",
    [None, "", "N/A", "None", "nan", "NaT", "-", "garbage", "Foo2024", "20221345"],
)
def test_rejects(raw: str | None) -> None:
    assert parse_ebso_date(raw) is None
    assert format_ebso_date(raw) == ""
