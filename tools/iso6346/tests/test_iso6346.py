"""Tests for ``iso6346`` (ISO 6346 container-number check digit).

Importable as ``iso6346`` thanks to ``tests/conftest.py`` putting the tool
directory on ``sys.path``.

Includes a cross-check of the check-digit algorithm against an independent
reimplementation written straight from the ISO 6346 description, so a typo in
the production weights/table would surface even though both live in this repo.
"""

from __future__ import annotations

import random
import string
import subprocess
import sys
from pathlib import Path

import pytest

import iso6346

SCRIPT = Path(__file__).resolve().parent.parent / "iso6346.py"

# Canonical example from ISO 6346 / the BIC calculator.
CANONICAL = "CSQU3054383"


# --- independent reference implementation ---------------------------------- #


def _reference_check_digit(body: str) -> int:
    """ISO 6346 check digit, reimplemented from scratch for cross-checking."""
    letter_value: dict[str, int] = {}
    n = 10
    for char in string.ascii_uppercase:
        while n % 11 == 0:
            n += 1
        letter_value[char] = n
        n += 1
    total = 0
    for position, char in enumerate(body.upper()):
        value = letter_value[char] if char.isalpha() else int(char)
        total += value * (2**position)
    remainder = total % 11
    return 0 if remainder == 10 else remainder


def _random_body(rng: random.Random) -> str:
    owner = "".join(rng.choice(string.ascii_uppercase) for _ in range(3))
    category = rng.choice("UJZ")
    serial = f"{rng.randint(0, 999999):06d}"
    return owner + category + serial


# --- reference table ------------------------------------------------------- #

EXPECTED_TABLE = {
    "A": 10, "B": 12, "C": 13, "D": 14, "E": 15, "F": 16, "G": 17, "H": 18,
    "I": 19, "J": 20, "K": 21, "L": 23, "M": 24, "N": 25, "O": 26, "P": 27,
    "Q": 28, "R": 29, "S": 30, "T": 31, "U": 32, "V": 34, "W": 35, "X": 36,
    "Y": 37, "Z": 38,
}


def test_reference_table_matches_standard() -> None:
    assert iso6346.reference_table() == EXPECTED_TABLE


def test_reference_table_is_a_copy() -> None:
    table = iso6346.reference_table()
    table["A"] = 999
    assert iso6346.reference_table()["A"] == 10


# --- check_digit --------------------------------------------------------- #


@pytest.mark.parametrize(
    "number, expected",
    [
        ("CSQU305438", 3),          # 10-char body
        ("csqu305438", 3),          # lower case
        ("csqu 305438", 3),         # spaces
        ("CSQU-305438", 3),         # hyphen
        ("CSQU3054389", 3),         # 11-char: trailing char ignored
        ("CSQU000007", 0),          # remainder 10 -> 0
        ("MSKU123456", 5),
    ],
)
def test_check_digit(number: str, expected: int) -> None:
    assert iso6346.check_digit(number) == expected


@pytest.mark.parametrize(
    "bad",
    ["", "SHORT", "CSQ3054380", "CSQU3054A", "CSQU3054AB", "TOOLONGNUMBER", "12345678"],
)
def test_check_digit_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        iso6346.check_digit(bad)


def test_check_digit_matches_reference_on_many_random_numbers() -> None:
    rng = random.Random(0)
    seen_remainder_10 = 0
    for _ in range(20000):
        body = _random_body(rng)
        got = iso6346.check_digit(body)
        assert got == _reference_check_digit(body), body
        if got == 0 and _reference_check_digit(body) == 0:
            # confirm the remainder-10 branch is actually exercised
            weighted = sum(
                (iso6346.LETTER_VALUES[c] if c.isalpha() else int(c)) * (2**i)
                for i, c in enumerate(body)
            )
            if weighted % 11 == 10:
                seen_remainder_10 += 1
    assert seen_remainder_10 > 0, "remainder-10 path never hit; test is not covering it"


# --- is_valid ------------------------------------------------------------ #


@pytest.mark.parametrize(
    "number, expected",
    [
        (CANONICAL, True),
        ("csqu 3054383", True),
        ("CSQU3054383".replace("3", "4", 1), False),  # corrupt a leading digit
        ("CSQU3054384", False),                       # wrong check digit
        ("CSQU305438", False),                        # 10-char: nothing to check
        ("not a container", False),
        ("", False),
        ("CSQU3054383X", False),                      # 12 chars
    ],
)
def test_is_valid(number: str, expected: bool) -> None:
    assert iso6346.is_valid(number) is expected


def test_is_valid_never_raises() -> None:
    for junk in ["", "!!!", "\x00", "CSQU" * 9, "12345678901"]:
        assert iso6346.is_valid(junk) is False


# --- parse ------------------------------------------------------------- #


def test_parse_breaks_out_the_fields() -> None:
    c = iso6346.parse("  csqu 305438-3 ")
    assert (c.owner_code, c.category_identifier, c.serial_number) == ("CSQ", "U", "305438")
    assert c.check_digit == 3
    assert c.provided_check_digit == 3
    assert c.is_complete is True
    assert c.is_valid is True
    assert c.category_is_standard is True
    assert c.normalized == CANONICAL


def test_parse_without_check_digit() -> None:
    c = iso6346.parse("CSQU305438")
    assert c.provided_check_digit is None
    assert c.is_complete is False
    assert c.is_valid is False          # nothing provided to match
    assert c.normalized == CANONICAL    # computed digit appended


def test_parse_wrong_check_digit() -> None:
    c = iso6346.parse("CSQU3054384")
    assert c.is_complete is True
    assert c.is_valid is False
    assert c.check_digit == 3
    assert c.provided_check_digit == 4


@pytest.mark.parametrize(
    "bad",
    ["", "SHORT", "CSQU30543", "CSQU3054383X", "1SQU305438", "CSQU30543A", "CSQUU305438"],
)
def test_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        iso6346.parse(bad)


def test_parse_is_immutable() -> None:
    c = iso6346.parse(CANONICAL)
    with pytest.raises(Exception):
        c.owner_code = "XXX"  # type: ignore[misc]


# --- category identifier -------------------------------------------------- #


def test_non_standard_category_is_flagged_but_can_still_validate() -> None:
    c = iso6346.parse("ABCR000000")          # 'R' is not U/J/Z
    assert c.category_is_standard is False
    assert iso6346.is_valid(c.normalized) is True


# --- round trip --------------------------------------------------------- #


def test_normalized_numbers_always_validate() -> None:
    rng = random.Random(1)
    for _ in range(5000):
        normalized = iso6346.parse(_random_body(rng)).normalized
        assert iso6346.is_valid(normalized) is True


# --- CLI --------------------------------------------------------------- #


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_cli_selftest_passes() -> None:
    assert _run("--selftest").returncode == 0


def test_cli_reference_table() -> None:
    result = _run("--ref")
    assert result.returncode == 0
    assert "Z=38" in result.stdout
    assert "L=23" in result.stdout


def test_cli_exit_codes() -> None:
    assert _run(CANONICAL).returncode == 0            # valid
    assert _run("CSQU3054384").returncode == 1        # invalid check digit
    assert _run("NOPE").returncode == 2               # unparseable
    assert _run().returncode == 2                     # no arguments


def test_cli_exit_code_is_worst_of_batch() -> None:
    assert _run(CANONICAL, "CSQU3054384").returncode == 1
    assert _run(CANONICAL, "NOPE").returncode == 2


def test_cli_json_output() -> None:
    result = _run("csqu 305438", CANONICAL, "--json")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 2

    import json

    incomplete, complete = (json.loads(line) for line in lines)
    assert incomplete["is_valid"] is None      # 10-char input
    assert incomplete["check_digit"] == 3
    assert complete["is_valid"] is True
    assert complete["normalized"] == CANONICAL


def test_cli_human_output_mentions_validity() -> None:
    assert "valid" in _run(CANONICAL).stdout
