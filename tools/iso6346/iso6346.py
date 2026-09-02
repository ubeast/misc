#!/usr/bin/env python3
"""ISO 6346 shipping-container number validation and check-digit calculation.

Single file, standard library only. Usable as a CLI or importable as a module.

A complete container number is 11 characters:

    C S Q U 3 0 5 4 3 8 3
    └─┬─┘ │ └──┬───┘ │
      │   │    │     └── check digit  (1 digit)
      │   │    └──────── serial number (6 digits, assigned by the owner)
      │   └───────────── equipment category identifier (1 letter: U, J or Z)
      └───────────────── owner code (3 letters, registered with the BIC)

A 10-character number (everything except the check digit) is also accepted; its
check digit is computed but there is nothing to validate it against.

Check-digit algorithm (ISO 6346, Annex A)
----------------------------------------
1. Convert the first 10 characters to numeric values.
   - Digits keep their face value.
   - Letters are numbered from 10 (A), counting up but skipping every multiple
     of 11:  A=10 B=12 C=13 ... K=21 L=23 ... U=32 V=34 ... Z=38
2. Multiply each value by 2 ** position  (position 0..9)  ->  weights
   1, 2, 4, 8, 16, 32, 64, 128, 256, 512.
3. Sum the products and take the sum modulo 11.
4. That remainder is the check digit, EXCEPT a remainder of 10 becomes 0.
   (Serial numbers whose remainder is 10 are therefore ambiguous with genuine
   0-check-digit numbers and are discouraged by the BIC, but 0 is the value
   defined by the standard.)

For developers
-------------
Drop this file into your project and import from it. Public API:

    from iso6346 import is_valid, check_digit, parse

    is_valid("CSQU3054383")     -> True        # validate a full 11-char number
    is_valid("CSQU3054384")     -> False       # bad check digit
    is_valid("nonsense")        -> False       # never raises

    check_digit("CSQU305438")   -> 3           # calculate the check digit
    check_digit("CSQU3054383")  -> 3           # 11-char input: last char ignored

    c = parse("CSQU3054383")                   # full breakdown; raises ValueError
    c.owner_code, c.category_identifier        # ('CSQ', 'U')
    c.serial_number, c.check_digit             # ('305438', 3)
    c.is_valid, c.is_complete, c.normalized

All three accept lower/upper case and ignore spaces and hyphens.

Sources:
    https://en.wikipedia.org/wiki/ISO_6346
    https://www.bic-code.org/check-digit-calculator/

Run the built-in checks with:  python3 iso6346.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import string
import sys
from dataclasses import asdict, dataclass

__all__ = [
    "is_valid",
    "check_digit",
    "parse",
    "ContainerNumber",
    "reference_table",
    "LETTER_VALUES",
]

# --- constants (nothing magic buried in the code below) ----------------------

FIRST_LETTER_VALUE = 10
CHECK_DIGIT_MODULUS = 11
BODY_LENGTH = 10  # owner code + category identifier + serial number
STANDARD_CATEGORY_IDENTIFIERS = frozenset({"U", "J", "Z"})

# Characters stripped before parsing (common separators in printed numbers).
_SEPARATOR_RE = re.compile(r"[\s\-]+")

# owner(3 letters) + category(1 letter) + serial(6 digits) + optional check(1 digit)
_CONTAINER_RE = re.compile(r"([A-Z]{3})([A-Z])([0-9]{6})([0-9])?")

# Shape of the 10-character body (owner + category + serial), no check digit.
_BODY_RE = re.compile(r"[A-Z]{4}[0-9]{6}")


def _build_letter_values() -> dict[str, int]:
    """Return {letter: value} for A-Z per step 1 of the algorithm.

    Count integers upward from FIRST_LETTER_VALUE, skipping multiples of 11,
    and assign them to A, B, C, ... in order.
    """
    values: dict[str, int] = {}
    n = FIRST_LETTER_VALUE
    for letter in string.ascii_uppercase:
        while n % CHECK_DIGIT_MODULUS == 0:
            n += 1
        values[letter] = n
        n += 1
    return values


LETTER_VALUES: dict[str, int] = _build_letter_values()

# Weight applied to each of the 10 body characters.
_WEIGHTS: tuple[int, ...] = tuple(2**position for position in range(BODY_LENGTH))


# --- core ------------------------------------------------------------------- #


def _checksum_of_body(body: str) -> int:
    """Core algorithm: ``body`` is exactly 4 uppercase letters + 6 digits."""
    total = sum(
        weight * (LETTER_VALUES[char] if char.isalpha() else int(char))
        for weight, char in zip(_WEIGHTS, body)
    )
    remainder = total % CHECK_DIGIT_MODULUS
    return 0 if remainder == 10 else remainder


def check_digit(number: str) -> int:
    """Return the ISO 6346 check digit (0-9) for a container number.

    Accepts the 10-character number (owner code + category identifier + serial
    number) or a full 11-character number, in which case the existing 11th
    character is ignored. Case-insensitive; spaces and hyphens are ignored.

    >>> check_digit("CSQU305438")
    3
    >>> check_digit("csqu 305438")
    3
    >>> check_digit("CSQU3054383")   # 11-char input, last char ignored
    3
    >>> check_digit("CSQU000007")    # remainder 10 -> 0
    0

    Raises:
        ValueError: if the input is not a well-formed container number.
    """
    body = _SEPARATOR_RE.sub("", number).upper()[:BODY_LENGTH]
    if not _BODY_RE.fullmatch(body):
        raise ValueError(
            f"not a container number: {number!r} "
            f"(expected 4 letters + 6 digits, plus an optional check digit)"
        )
    return _checksum_of_body(body)


@dataclass(frozen=True)
class ContainerNumber:
    """A parsed container number and everything derived from it."""

    owner_code: str
    category_identifier: str
    serial_number: str
    check_digit: int
    """The correct check digit, computed from owner code + serial number."""
    provided_check_digit: int | None
    """The 11th character as supplied by the caller, or None if absent."""

    @property
    def is_complete(self) -> bool:
        """True if the input included a check digit to validate."""
        return self.provided_check_digit is not None

    @property
    def is_valid(self) -> bool:
        """True if a check digit was provided and it matches the computed one."""
        return self.provided_check_digit == self.check_digit

    @property
    def category_is_standard(self) -> bool:
        """True if the category identifier is one ISO 6346 defines (U, J, Z).

        A non-standard identifier (e.g. an AAR rail code) can still have a
        matching check digit; callers decide whether they care.
        """
        return self.category_identifier in STANDARD_CATEGORY_IDENTIFIERS

    @property
    def normalized(self) -> str:
        """The canonical 11-character number, using the computed check digit."""
        return (
            f"{self.owner_code}{self.category_identifier}"
            f"{self.serial_number}{self.check_digit}"
        )


def parse(raw: str) -> ContainerNumber:
    """Parse a container number, tolerating spaces and hyphens.

    Accepts a 10- or 11-character number (case-insensitive).

    >>> c = parse("csqu 305438-3")
    >>> (c.owner_code, c.category_identifier, c.serial_number, c.check_digit)
    ('CSQ', 'U', '305438', 3)
    >>> c.is_valid
    True
    >>> parse("CSQU3054384").is_valid          # wrong check digit
    False
    >>> parse("CSQU305438").is_complete        # no check digit supplied
    False
    >>> parse("CSQU305438").normalized
    'CSQU3054383'

    Raises:
        ValueError: if the cleaned input is not a well-formed container number.
    """
    cleaned = _SEPARATOR_RE.sub("", raw).upper()
    match = _CONTAINER_RE.fullmatch(cleaned)
    if match is None:
        raise ValueError(
            f"not a container number: {raw!r} "
            f"(expected 3 letters + 1 letter + 6 digits + optional check digit)"
        )
    owner_code, category_identifier, serial_number, provided = match.groups()
    return ContainerNumber(
        owner_code=owner_code,
        category_identifier=category_identifier,
        serial_number=serial_number,
        check_digit=_checksum_of_body(owner_code + category_identifier + serial_number),
        provided_check_digit=int(provided) if provided is not None else None,
    )


def is_valid(number: str) -> bool:
    """Return True iff ``number`` is a complete 11-character container number
    whose check digit is correct.

    Never raises: malformed input, or a 10-character number with no check digit
    to compare against, returns False.

    >>> is_valid("CSQU3054383")
    True
    >>> is_valid("CSQU3054384")
    False
    >>> is_valid("CSQU305438")    # no check digit supplied
    False
    >>> is_valid("not a container")
    False
    """
    try:
        container = parse(number)
    except ValueError:
        return False
    return container.is_complete and container.is_valid


def reference_table() -> dict[str, int]:
    """Return the full letter -> value mapping (a copy of LETTER_VALUES)."""
    return dict(LETTER_VALUES)


# --- CLI ------------------------------------------------------------------- #


def _format_human(container: ContainerNumber) -> str:
    lines = [
        f"input check digit : {container.provided_check_digit if container.is_complete else '(none)'}",
        f"owner code        : {container.owner_code}",
        f"category          : {container.category_identifier}"
        f"{'' if container.category_is_standard else '  (non-standard: not U/J/Z)'}",
        f"serial number     : {container.serial_number}",
        f"computed check    : {container.check_digit}",
        f"normalized        : {container.normalized}",
    ]
    if container.is_complete:
        lines.append(f"valid             : {container.is_valid}")
    else:
        lines.append("valid             : unknown (no check digit supplied)")
    return "\n".join(lines)


def _print_reference_table() -> None:
    letters = list(LETTER_VALUES)
    half = (len(letters) + 1) // 2
    for group in (letters[:half], letters[half:]):
        print(" ".join(f"{ch}={LETTER_VALUES[ch]:>2}" for ch in group))


def _selftest() -> int:
    import doctest

    failures, _ = doctest.testmod(verbose=False)

    # A few known-good numbers and edge cases not covered by the doctests.
    assert reference_table()["L"] == 23  # 22 skipped
    assert reference_table()["U"] == 32
    assert reference_table()["Z"] == 38
    assert check_digit("CSQU305438") == 3
    assert check_digit("CSQU3054383") == 3  # 11-char input, check digit ignored
    assert is_valid("CSQU3054383") is True
    assert is_valid("CSQU3054384") is False
    assert is_valid("CSQU305438") is False  # incomplete
    assert is_valid("garbage") is False
    assert parse("CSQU3054383").is_valid is True
    assert parse("CSQU3054383").category_is_standard is True
    assert parse("ABCR000000").category_is_standard is False
    assert parse(" hlb u-123456 ").owner_code == "HLB"  # separators + case tolerated

    for bad in ("", "SHORT", "CSQU30543", "1SQU305438", "CSQU30543A", "CSQU3054383X"):
        try:
            parse(bad)
        except ValueError:
            pass
        else:  # pragma: no cover - only runs on regression
            raise AssertionError(f"expected ValueError for {bad!r}")

    if failures:
        print(f"{failures} doctest failure(s)", file=sys.stderr)
        return 1
    print("selftest OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "numbers",
        nargs="*",
        help="container number(s); 10 or 11 characters, spaces/hyphens ok",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit one JSON object per line instead of human-readable text",
    )
    parser.add_argument(
        "--ref",
        action="store_true",
        help="print the letter -> value reference table and exit",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run the built-in doctests and assertions and exit",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.ref:
        _print_reference_table()
        return 0
    if not args.numbers:
        parser.error("provide at least one container number (or use --ref / --selftest)")

    exit_code = 0
    for raw in args.numbers:
        try:
            container = parse(raw)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue

        if args.json:
            record = asdict(container)
            record.update(
                is_complete=container.is_complete,
                is_valid=container.is_valid if container.is_complete else None,
                category_is_standard=container.category_is_standard,
                normalized=container.normalized,
            )
            print(json.dumps(record))
        else:
            if len(args.numbers) > 1:
                print(f"# {raw}")
            print(_format_human(container))
            if len(args.numbers) > 1:
                print()

        if container.is_complete and not container.is_valid:
            exit_code = max(exit_code, 1)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
