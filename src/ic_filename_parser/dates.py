"""Parse EBSO / DLMS publication-date tokens into ISO ``YYYY-MM-DD`` strings.

The date tokens embedded in Interface Change (IC) filenames are wildly
inconsistent: numeric ``YYYYMMDD``, ``Aug2024`` (month + year only),
``Aug152024`` (full date, 4-digit year), ``Aug1524`` (full date, 2-digit
year). :func:`parse_ebso_date` tries a fixed, documented sequence of shapes
and returns the first that yields a real calendar date.

Design notes for a follow-on developer:

* The patterns are tried **most specific first**. In particular the
  "month + year" shape (pattern 4) is checked *before* the "month + day +
  2-digit year" shape (pattern 5) but *after* the "month + day + 4-digit
  year" shape (pattern 3). This ordering is what fixes the historical bug
  where ``Aug2024`` was read as "the 20th of August, 2024" because a
  greedy ``\\d{1,2}`` day group happily ate the first two digits of the
  year.
* A 4-digit run is treated as a *year* only when it falls inside
  :data:`_MIN_FULL_YEAR` .. :data:`_MAX_FULL_YEAR` (patterns 3 and 4);
  otherwise the "month + day + 2-digit year" shape (pattern 5) reads the
  last two digits as ``YY``. Widen that window if the corpus ever grows to
  include 1900s or 2100s publication dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

__all__ = ["DatePrecision", "ParsedDate", "parse_ebso_date", "format_ebso_date", "MONTH_MAP"]

# Tokens that mean "no date". Compared case-insensitively after whitespace
# and separator characters have been stripped.
_NULL_TOKENS: frozenset[str] = frozenset(
    {"", "-", "n/a", "na", "none", "nonetype", "nan", "nat", "null"}
)

MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Window in which a bare 4-digit number is interpreted as a calendar year
# rather than as a ``DDYY`` fragment. EBSO / DLMS change documents are all
# published this century.
_MIN_FULL_YEAR = 2000
_MAX_FULL_YEAR = 2099
# 2-digit years are assumed to be 20YY (same rationale).
_CENTURY_BASE = 2000


class DatePrecision(str, Enum):
    """How much of the returned date was actually present in the token."""

    DAY = "day"
    MONTH = "month"  # day-of-month was defaulted to the 1st


@dataclass(frozen=True)
class ParsedDate:
    value: date
    precision: DatePrecision

    @property
    def iso(self) -> str:
        return self.value.isoformat()


_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YYYYMMDD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_MONTH_DAY_YEAR4_RE = re.compile(r"^(?P<month>[A-Za-z]+)(?P<day>\d{1,2})(?P<year>\d{4})$")
_MONTH_YEAR_RE = re.compile(r"^(?P<month>[A-Za-z]+)(?P<year>\d{4})$")
_MONTH_DAY_YEAR2_RE = re.compile(r"^(?P<month>[A-Za-z]+)(?P<day>\d{1,2})(?P<year>\d{2})$")

# Separators to drop before shape-matching ("_Aug_2024_" -> "Aug2024").
# ISO dates are matched first, so their hyphens are never reached by this.
_SEP_RE = re.compile(r"[_\s]+")


def _month_number(token: str) -> int | None:
    return MONTH_MAP.get(token.lower())


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_ebso_date(raw: str | None) -> ParsedDate | None:
    """Return the calendar date encoded in ``raw``, or ``None`` if there is none.

    ``None`` covers both "explicitly no date" (``"N/A"``, ``None``, ``"nan"``)
    and "could not be understood" (``"garbage"``, ``"Foo2024"``). Callers that
    need a string should use :func:`format_ebso_date`.
    """
    if raw is None:
        return None
    token = _SEP_RE.sub("", str(raw).strip())
    if token.lower() in _NULL_TOKENS:
        return None

    # 1. ISO 8601 — already normalised, just validate.
    m = _ISO_RE.match(token)
    if m:
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        return ParsedDate(d, DatePrecision.DAY) if d else None

    # 2. Numeric YYYYMMDD.
    m = _YYYYMMDD_RE.match(token)
    if m:
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        if d:
            return ParsedDate(d, DatePrecision.DAY)

    # 3. <Month><day><4-digit year>, e.g. "Aug152024". A bare month + year
    #    cannot reach here: the day group needs >= 1 digit that a 4-digit
    #    year would otherwise require. The 4-digit run is only accepted as a
    #    year inside the plausible-publication-year window, same as pattern 4.
    m = _MONTH_DAY_YEAR4_RE.match(token)
    if m:
        mon = _month_number(m["month"])
        year = int(m["year"])
        if mon and _MIN_FULL_YEAR <= year <= _MAX_FULL_YEAR:
            d = _safe_date(year, mon, int(m["day"]))
            if d:
                return ParsedDate(d, DatePrecision.DAY)

    # 4. <Month><4-digit year>, e.g. "Aug2024" -> first of month. Only when
    #    the digits look like a plausible publication year; otherwise fall
    #    through to pattern 5 and read them as DDYY.
    m = _MONTH_YEAR_RE.match(token)
    if m:
        mon = _month_number(m["month"])
        year = int(m["year"])
        if mon and _MIN_FULL_YEAR <= year <= _MAX_FULL_YEAR:
            d = _safe_date(year, mon, 1)
            if d:
                return ParsedDate(d, DatePrecision.MONTH)

    # 5. <Month><day><2-digit year>, e.g. "Aug1524" -> 15 Aug 2024.
    m = _MONTH_DAY_YEAR2_RE.match(token)
    if m:
        mon = _month_number(m["month"])
        if mon:
            d = _safe_date(_CENTURY_BASE + int(m["year"]), mon, int(m["day"]))
            if d:
                return ParsedDate(d, DatePrecision.DAY)

    return None


def format_ebso_date(raw: str | None) -> str:
    """Like :func:`parse_ebso_date` but returns ``""`` instead of ``None``."""
    parsed = parse_ebso_date(raw)
    return parsed.iso if parsed else ""
