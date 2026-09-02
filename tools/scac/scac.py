#!/usr/bin/env python3
"""SCAC (Standard Carrier Alpha Code) format validation and classification.

Single file, standard library only. Usable as a CLI or importable as a module.

A SCAC is a 2-to-4-letter code that uniquely identifies a transportation
company. Codes are issued and maintained by the NMFTA (National Motor Freight
Traffic Association); this tool does **not** know which codes have actually been
issued -- it validates the *shape* of a code and reports the reserved final
letter, if any.

Reserved final letters
----------------------
Three trailing letters are reserved for identifying *equipment*, not carrier
type (per the SCAC standard, echoed by Wikipedia):

    ...U   freight containers (aligned with ISO 6346 / BIC container-owner
           prefixes, which end in U)
    ...X   privately owned railroad cars
    ...Z   truck chassis and trailers used in intermodal service

A carrier that owns such equipment often registers a SCAC matching its
equipment prefix -- which is why many ocean lines' SCACs end in ``U``
(``MAEU`` Maersk, ``MSCU`` MSC). But the convention is loose: plenty of
container carriers' SCACs do **not** end in ``U`` (e.g. ``EGLV`` Evergreen,
``ONEY`` Ocean Network Express), and a ``U``/``X``/``Z`` ending does not by
itself prove what a code is for. Treat ``parse(...).suffix`` as a hint, not a
classification.

The suffix is only split off when at least two letters remain in front of it,
so ``MAEU`` -> base ``MAE`` + ``U``, but ``UPS`` is just ``UPS``.

For developers
--------------
    from scac import is_valid, parse, Scac

    is_valid("MAEU")        -> True
    is_valid("maeu")        -> True     (case-insensitive)
    is_valid("TOOLONG")     -> False    (never raises)

    s = parse("SCAX")                   # raises ValueError on bad shape
    s.code, s.base, s.suffix            # ('SCAX', 'SCA', 'X')
    s.suffix_meaning                    # 'privately owned railroad cars'

Sources:
    https://en.wikipedia.org/wiki/Standard_Carrier_Alpha_Code
    https://nmfta.org/scac/

Run the built-in checks with:  python3 scac.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass

__all__ = ["is_valid", "parse", "Scac", "RESERVED_SUFFIXES"]

MIN_LENGTH = 2
MAX_LENGTH = 4

# Final letters reserved for identifying equipment (not carrier type). The
# suffix is only split off when at least _MIN_BASE_LENGTH letters remain.
RESERVED_SUFFIXES: dict[str, str] = {
    "U": "freight containers (aligned with ISO 6346 / BIC container-owner prefixes)",
    "X": "privately owned railroad cars",
    "Z": "truck chassis and trailers used in intermodal service",
}
_MIN_BASE_LENGTH = 2

_SEPARATOR_RE = re.compile(r"[\s\-]+")
_SCAC_RE = re.compile(r"[A-Z]{2,4}")


@dataclass(frozen=True)
class Scac:
    """A parsed SCAC and everything derived from it."""

    code: str
    """The normalized code: upper-case, separators removed."""
    base: str
    """The code without its reserved suffix (equal to ``code`` if there is none)."""
    suffix: str | None
    """The reserved final letter (``U``/``X``/``Z``), or ``None``."""

    @property
    def length(self) -> int:
        return len(self.code)

    @property
    def suffix_meaning(self) -> str | None:
        return RESERVED_SUFFIXES.get(self.suffix) if self.suffix else None

    @property
    def is_valid(self) -> bool:
        """True if the code is 2-4 letters. Parsing already guarantees this;
        the property exists so callers that hold a ``Scac`` need not re-check."""
        return bool(_SCAC_RE.fullmatch(self.code))


def _normalize(raw: str) -> str:
    return _SEPARATOR_RE.sub("", raw).upper()


def parse(raw: str) -> Scac:
    """Parse a SCAC, tolerating surrounding spaces/hyphens and any case.

    >>> s = parse("maeu")
    >>> (s.code, s.base, s.suffix)
    ('MAEU', 'MAE', 'U')
    >>> parse("SCAX").suffix_meaning
    'privately owned railroad cars'
    >>> parse("UPS").suffix is None
    True
    >>> parse("FX").base            # 2-letter code, nothing to split
    'FX'

    Raises:
        ValueError: if the cleaned input is not 2-4 ASCII letters.
    """
    code = _normalize(raw)
    if not _SCAC_RE.fullmatch(code):
        raise ValueError(
            f"not a SCAC: {raw!r} (expected {MIN_LENGTH}-{MAX_LENGTH} letters)"
        )

    last = code[-1]
    if last in RESERVED_SUFFIXES and len(code) - 1 >= _MIN_BASE_LENGTH:
        return Scac(code=code, base=code[:-1], suffix=last)
    return Scac(code=code, base=code, suffix=None)


def is_valid(raw: str) -> bool:
    """Return True iff ``raw`` is shaped like a SCAC (2-4 letters). Never raises.

    >>> is_valid("MAEU")
    True
    >>> is_valid("a-b-c")
    True
    >>> is_valid("TOOLONG")
    False
    >>> is_valid("A1")
    False
    >>> is_valid("")
    False
    """
    return _SCAC_RE.fullmatch(_normalize(raw)) is not None


# --- CLI ------------------------------------------------------------------- #


def _format_human(scac: Scac) -> str:
    lines = [
        f"code   : {scac.code}",
        f"length : {scac.length}",
        f"base   : {scac.base}",
    ]
    if scac.suffix:
        lines.append(f"suffix : {scac.suffix}  ({scac.suffix_meaning})")
    else:
        lines.append("suffix : (none)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("codes", nargs="*", help="SCAC code(s); 2-4 letters, spaces/hyphens ok")
    parser.add_argument("--json", action="store_true", help="emit one JSON object per line")
    parser.add_argument(
        "--suffixes",
        action="store_true",
        help="print the reserved final-letter table and exit",
    )
    parser.add_argument("--selftest", action="store_true", help="run the built-in doctests and assertions and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.suffixes:
        for letter, meaning in RESERVED_SUFFIXES.items():
            print(f"...{letter}  {meaning}")
        return 0
    if not args.codes:
        parser.error("provide at least one SCAC code (or use --suffixes / --selftest)")

    exit_code = 0
    for raw in args.codes:
        try:
            scac = parse(raw)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue

        if args.json:
            record = asdict(scac)
            record.update(
                length=scac.length,
                suffix_meaning=scac.suffix_meaning,
                is_valid=scac.is_valid,
            )
            print(json.dumps(record))
        else:
            if len(args.codes) > 1:
                print(f"# {raw}")
            print(_format_human(scac))
            if len(args.codes) > 1:
                print()

    return exit_code


def _selftest() -> int:
    import doctest

    failures, _ = doctest.testmod(verbose=False)

    assert is_valid("MAEU") is True
    assert is_valid("FX") is True
    assert is_valid("ABCD") is True
    assert is_valid("A") is False
    assert is_valid("ABCDE") is False
    assert is_valid("AB1") is False
    assert is_valid("") is False
    assert is_valid(" s c a c ") is True

    assert parse("MAEU").base == "MAE"
    assert parse("MAEU").suffix == "U"
    assert parse("SCAX").suffix == "X"
    assert parse("CASZ").suffix == "Z"
    assert parse("CSXT").suffix is None       # ends in T
    assert parse("UPSX").base == "UPS"
    assert parse("FX").suffix is None         # only 1 letter would remain
    assert parse("XU").suffix is None         # base "X" too short
    assert parse("hlxu").code == "HLXU"

    for bad in ("", "A", "ABCDE", "AB1", "12", "A-B-C-D-E"):
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
