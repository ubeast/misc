#!/usr/bin/env python3
"""UN/LOCODE format validation and parsing.

Single file, standard library only. Usable as a CLI or importable as a module.

A UN/LOCODE (United Nations Code for Trade and Transport Locations) identifies a
port, airport, inland freight terminal, border crossing, etc.  It is five
characters:

    U S   N Y C
    └┬┘   └─┬─┘
     │      └── location code: 3 letters, or letters + the digits 2-9
     └───────── country code:  2 letters (ISO 3166-1 alpha-2)

The two halves are often printed with a space ("US NYC"); the canonical form has
none ("USNYC").  The digits 0 and 1 are not used in the location code -- they
are too easily confused with the letters O and I.

Scope
-----
This tool validates the **shape** and checks the **country code** against the
bundled ISO 3166-1 alpha-2 list (plus ``XZ``, which UN/LOCODE uses for
installations in international waters).  It does **not** know whether a given
location code has actually been assigned -- the UN/LOCODE database has ~100,000
entries and is republished twice a year, far too large to embed.  A well-formed
code with a real country therefore returns ``is_valid`` True even if UNECE has
never issued it.

For developers
--------------
    from unlocode import is_valid, parse, UnLocode

    is_valid("USNYC")       -> True
    is_valid("US NYC")      -> True     (space tolerated)
    is_valid("us nyc")      -> True     (case-insensitive)
    is_valid("USNY1")       -> False    (digit 1 not allowed; never raises)

    loc = parse("de ham")               # raises ValueError on a bad shape
    loc.country, loc.location           # ('DE', 'HAM')
    loc.normalized, loc.display         # ('DEHAM', 'DE HAM')
    loc.country_is_known, loc.is_valid  # (True, True)

Sources:
    https://unece.org/trade/uncefact/unlocode
    https://en.wikipedia.org/wiki/UN/LOCODE

Run the built-in checks with:  python3 unlocode.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass

__all__ = ["is_valid", "parse", "UnLocode", "ISO_3166_1_ALPHA_2", "known_countries"]

# ISO 3166-1 alpha-2 officially assigned codes (current as of 2024) plus the
# UN/LOCODE special code XZ for international-waters installations.
ISO_3166_1_ALPHA_2: frozenset[str] = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
    BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
    DE DJ DK DM DO DZ
    EC EE EG EH ER ES ET
    FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
    HK HM HN HR HT HU
    ID IE IL IM IN IO IQ IR IS IT
    JE JM JO JP
    KE KG KH KI KM KN KP KR KW KY KZ
    LA LB LC LI LK LR LS LT LU LV LY
    MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
    NA NC NE NF NG NI NL NO NP NR NU NZ
    OM
    PA PE PF PG PH PK PL PM PN PR PS PT PW PY
    QA
    RE RO RS RU RW
    SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
    TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
    UA UG UM US UY UZ
    VA VC VE VG VI VN VU
    WF WS
    YE YT
    ZA ZM ZW
    XZ
    """.split()
)

SPECIAL_COUNTRY_CODES: dict[str, str] = {
    "XZ": "installations in international waters (UN/LOCODE special code)",
}

_SEPARATOR_RE = re.compile(r"[\s\-]+")
_COUNTRY_RE = re.compile(r"[A-Z]{2}")
# Location code: 3 chars, letters A-Z and digits 2-9 (no 0/1).
_LOCATION_RE = re.compile(r"[A-Z2-9]{3}")
_UNLOCODE_RE = re.compile(r"([A-Z]{2})[ ]?([A-Z2-9]{3})")


@dataclass(frozen=True)
class UnLocode:
    """A parsed UN/LOCODE and everything derived from it."""

    country: str
    """The 2-letter country code, upper-cased."""
    location: str
    """The 3-character location code, upper-cased."""

    @property
    def normalized(self) -> str:
        """Canonical 5-character form, no space."""
        return f"{self.country}{self.location}"

    @property
    def display(self) -> str:
        """Human form with a space between the halves."""
        return f"{self.country} {self.location}"

    @property
    def country_is_known(self) -> bool:
        """True if the country code is in the bundled ISO 3166-1 list (or XZ)."""
        return self.country in ISO_3166_1_ALPHA_2

    @property
    def country_note(self) -> str | None:
        """Explanatory note for special country codes (e.g. XZ), else None."""
        return SPECIAL_COUNTRY_CODES.get(self.country)

    @property
    def location_is_wellformed(self) -> bool:
        """True if the location code is 3 chars of A-Z / 2-9 (no 0 or 1)."""
        return _LOCATION_RE.fullmatch(self.location) is not None

    @property
    def is_valid(self) -> bool:
        """True if the shape is right *and* the country code is recognized.

        Note: a real location code is not required -- see the module docstring.
        """
        return self.country_is_known and self.location_is_wellformed


def _normalize(raw: str) -> str:
    return _SEPARATOR_RE.sub("", raw).upper()


def parse(raw: str) -> UnLocode:
    """Parse a UN/LOCODE, tolerating a space/hyphen between the halves and any case.

    >>> loc = parse("de ham")
    >>> (loc.country, loc.location, loc.normalized, loc.display)
    ('DE', 'HAM', 'DEHAM', 'DE HAM')
    >>> parse("USNYC").is_valid
    True
    >>> parse("ZZABC").country_is_known
    False
    >>> parse("XZUKN").country_note
    'installations in international waters (UN/LOCODE special code)'

    Raises:
        ValueError: if the cleaned input is not 2 letters + 3 chars of A-Z/2-9.
    """
    cleaned = _normalize(raw)
    match = _UNLOCODE_RE.fullmatch(cleaned)
    if match is None:
        raise ValueError(
            f"not a UN/LOCODE: {raw!r} "
            f"(expected 2 letters + 3 characters of A-Z or 2-9)"
        )
    country, location = match.groups()
    return UnLocode(country=country, location=location)


def is_valid(raw: str) -> bool:
    """Return True iff ``raw`` is a well-formed UN/LOCODE with a known country.

    Never raises. See the module docstring: a real (issued) location code is not
    required, only the correct shape and a recognized country.

    >>> is_valid("USNYC")
    True
    >>> is_valid("US NYC")
    True
    >>> is_valid("USNY1")       # digit 1 not allowed
    False
    >>> is_valid("ZZNYC")       # unknown country
    False
    >>> is_valid("nonsense")
    False
    """
    try:
        return parse(raw).is_valid
    except ValueError:
        return False


def known_countries() -> frozenset[str]:
    """Return the bundled set of accepted country codes."""
    return ISO_3166_1_ALPHA_2


# --- CLI ------------------------------------------------------------------- #


def _format_human(loc: UnLocode) -> str:
    lines = [
        f"country       : {loc.country}"
        f"{'' if loc.country_is_known else '  (unknown -- not in ISO 3166-1)'}",
        f"location      : {loc.location}",
        f"normalized    : {loc.normalized}",
        f"display       : {loc.display}",
    ]
    if loc.country_note:
        lines.append(f"country note  : {loc.country_note}")
    lines.append(f"valid (shape+country) : {loc.is_valid}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("codes", nargs="*", help="UN/LOCODE(s); 5 chars, optional space/hyphen between halves")
    parser.add_argument("--json", action="store_true", help="emit one JSON object per line")
    parser.add_argument("--countries", action="store_true", help="print the accepted country codes and exit")
    parser.add_argument("--selftest", action="store_true", help="run the built-in doctests and assertions and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.countries:
        print(" ".join(sorted(ISO_3166_1_ALPHA_2)))
        return 0
    if not args.codes:
        parser.error("provide at least one UN/LOCODE (or use --countries / --selftest)")

    exit_code = 0
    for raw in args.codes:
        try:
            loc = parse(raw)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue

        if args.json:
            record = asdict(loc)
            record.update(
                normalized=loc.normalized,
                display=loc.display,
                country_is_known=loc.country_is_known,
                country_note=loc.country_note,
                location_is_wellformed=loc.location_is_wellformed,
                is_valid=loc.is_valid,
            )
            print(json.dumps(record))
        else:
            if len(args.codes) > 1:
                print(f"# {raw}")
            print(_format_human(loc))
            if len(args.codes) > 1:
                print()

        if not loc.is_valid:
            exit_code = max(exit_code, 1)

    return exit_code


def _selftest() -> int:
    import doctest

    failures, _ = doctest.testmod(verbose=False)

    assert is_valid("USNYC") is True
    assert is_valid("US NYC") is True
    assert is_valid("us-nyc") is True
    assert is_valid("DEHAM") is True
    assert is_valid("NLRTM") is True
    assert is_valid("XZUKN") is True          # international waters
    assert is_valid("USNY1") is False         # digit 1
    assert is_valid("USNY0") is False         # digit 0
    assert is_valid("ZZNYC") is False         # unknown country
    assert is_valid("US") is False
    assert is_valid("") is False
    assert is_valid("garbage") is False

    loc = parse("  sg-sin ")
    assert (loc.country, loc.location) == ("SG", "SIN")
    assert loc.normalized == "SGSIN"
    assert loc.display == "SG SIN"

    assert parse("ZZABC").is_valid is False
    assert parse("ZZABC").location_is_wellformed is True
    assert "US" in known_countries()
    assert len(known_countries()) > 240

    for bad in ("", "US", "USNYCC", "U1NYC", "US NY1", "USN.C", "12NYC"):
        try:
            parse(bad)
        except ValueError:
            pass
        else:  # pragma: no cover - only on regression
            raise AssertionError(f"expected ValueError for {bad!r}")

    if failures:
        print(f"{failures} doctest failure(s)", file=sys.stderr)
        return 1
    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
