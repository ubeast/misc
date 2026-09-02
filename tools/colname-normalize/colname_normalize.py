#!/usr/bin/env python3
"""Normalize messy column names into safe, consistent snake_case identifiers.

Single file, standard library only. Usable as a CLI or importable as a module.

The problem
-----------
A spreadsheet or query result arrives with headers like ``"Unit Price ($)"``,
``"customerID"``, ``"Ship-To Country"``, ``" notes "`` -- and two different
columns both literally called ``"Total"``.  Before that becomes a Spark or
pandas DataFrame you want names that are lower_snake_case, ASCII-only, free of
punctuation, valid as Python identifiers, and unique.

What ``normalize`` does, in order
---------------------------------
1.  Unicode-normalize (NFKD) and drop accents:  ``"Ünit"`` -> ``"Unit"``.
2.  Replace a few symbols with words (optional): ``%`` -> ``pct``, ``&`` -> ``and``,
    ``#`` -> ``num``, ``@`` -> ``at``, ``$`` -> ``usd``, ``+`` -> ``plus``,
    ``°`` -> ``deg``.
3.  Split camelCase / PascalCase / acronym runs: ``"customerID"`` ->
    ``"customer_ID"``; ``"HTTPServerError"`` -> ``"HTTP_Server_Error"``.
4.  Replace every run of non-alphanumeric characters with a single ``_``.
5.  Collapse repeated ``_``, strip leading/trailing ``_``, lower-case.
6.  An empty result becomes ``fallback`` (default ``"column"``).
7.  A name starting with a digit gets ``digit_prefix`` (default ``"n"``), since
    a bare identifier cannot start with a digit. Set it to ``""`` to opt out.
8.  A name that is a Python keyword gets a trailing ``_``; with
    ``avoid_sql_keywords`` a common-SQL reserved word does too.
9.  With ``max_length`` > 0 the name is truncated to that many characters.

``normalize`` is idempotent: ``normalize(normalize(x)) == normalize(x)``.

``normalize_all`` additionally de-duplicates the whole list: a name that would
collide with an earlier one gets ``_2``, ``_3``, ... appended.

For developers
--------------
    from colname_normalize import normalize, normalize_all, build_mapping, Options

    normalize("Unit Price ($)")              -> "unit_price_usd"
    normalize("customerID")                  -> "customer_id"
    normalize_all(["Total", "total", "TOTAL"]) -> ["total", "total_2", "total_3"]
    build_mapping(["A B", "A-B"])            -> {"A B": "a_b", "A-B": "a_b_2"}

Every function takes an optional ``options=Options(...)`` to change the defaults.

Run the built-in checks with:  python3 colname_normalize.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import keyword
import re
import sys
import unicodedata
from dataclasses import dataclass, field, replace

__all__ = [
    "normalize",
    "normalize_all",
    "build_mapping",
    "Options",
    "SQL_RESERVED",
    "DEFAULT_SYMBOL_WORDS",
]

# --- constants (nothing magic buried in the code below) --------------------- #

DEFAULT_FALLBACK = "column"
DEFAULT_DIGIT_PREFIX = "n"

# Symbols mapped to words in step 2. Applied as surrounded-by-spaces text
# substitution so "100%" becomes "100 pct " -> "100_pct".
DEFAULT_SYMBOL_WORDS: dict[str, str] = {
    "%": "pct",
    "&": "and",
    "#": "num",
    "@": "at",
    "$": "usd",
    "+": "plus",
    "°": "deg",
    "€": "eur",
    "£": "gbp",
}

# A deliberately small, dialect-agnostic set of words that are reserved in most
# SQL engines (ANSI core + the ones that bite in Spark / BigQuery / Snowflake).
# Off by default; enable with Options(avoid_sql_keywords=True).
SQL_RESERVED: frozenset[str] = frozenset(
    {
        "all", "and", "any", "array", "as", "asc", "between", "by", "case",
        "cast", "check", "column", "constraint", "create", "cross", "current",
        "current_date", "current_time", "current_timestamp", "database",
        "default", "delete", "desc", "distinct", "drop", "else", "end",
        "except", "exists", "false", "fetch", "filter", "for", "foreign",
        "from", "full", "function", "grant", "group", "grouping", "having",
        "in", "inner", "insert", "intersect", "interval", "into", "is", "join",
        "lateral", "left", "like", "limit", "natural", "not", "null", "of",
        "on", "or", "order", "outer", "over", "partition", "primary", "range",
        "references", "right", "rollup", "row", "rows", "select", "semi",
        "set", "some", "table", "then", "to", "true", "union", "unique",
        "update", "user", "using", "values", "when", "where", "window", "with",
    }
)

# camelCase / acronym boundaries -- applied in this order.
_ACRONYM_THEN_WORD = re.compile(r"([A-Z]+)([A-Z][a-z])")        # HTTPServer -> HTTP_Server
_LOWER_DIGIT_THEN_UPPER = re.compile(r"([a-z0-9])([A-Z])")      # userId -> user_Id

_NON_ALNUM_RUN = re.compile(r"[^0-9A-Za-z]+")
_UNDERSCORE_RUN = re.compile(r"_+")
_LEADING_DIGIT = re.compile(r"^[0-9]")


@dataclass(frozen=True)
class Options:
    """Knobs for :func:`normalize` / :func:`normalize_all`.

    Attributes:
        fallback: name used when normalization leaves nothing (e.g. ``"---"``).
        digit_prefix: prepended when the result would start with a digit.
        replace_symbols: run step 2 (``%`` -> ``pct`` etc.).
        symbol_words: the symbol -> word map used by step 2.
        split_camel_case: run step 3 (insert ``_`` at camelCase boundaries).
        lower: lower-case the result (turn off to keep ``UPPER_SNAKE``).
        ascii_only: drop non-ASCII characters left after NFKD normalization.
        avoid_python_keywords: append ``_`` to a Python keyword.
        avoid_sql_keywords: also append ``_`` to a word in :data:`SQL_RESERVED`.
        max_length: truncate to this many characters (0 = no limit).
    """

    fallback: str = DEFAULT_FALLBACK
    digit_prefix: str = DEFAULT_DIGIT_PREFIX
    replace_symbols: bool = True
    symbol_words: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SYMBOL_WORDS))
    split_camel_case: bool = True
    lower: bool = True
    ascii_only: bool = True
    avoid_python_keywords: bool = True
    avoid_sql_keywords: bool = False
    max_length: int = 0


DEFAULT_OPTIONS = Options()


# --- core ------------------------------------------------------------------- #


def _strip_accents(text: str, ascii_only: bool) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    if ascii_only:
        without_marks = without_marks.encode("ascii", "ignore").decode("ascii")
    return without_marks


def _apply_symbol_words(text: str, symbol_words: dict[str, str]) -> str:
    for symbol, word in symbol_words.items():
        if symbol in text:
            text = text.replace(symbol, f" {word} ")
    return text


def _split_camel_case(text: str) -> str:
    text = _ACRONYM_THEN_WORD.sub(r"\1_\2", text)
    text = _LOWER_DIGIT_THEN_UPPER.sub(r"\1_\2", text)
    return text


def normalize(name: str, options: Options = DEFAULT_OPTIONS) -> str:
    """Return a single normalized column name. See module docstring for the steps.

    >>> normalize("Unit Price ($)")
    'unit_price_usd'
    >>> normalize("customerID")
    'customer_id'
    >>> normalize("  Ship-To  Country  ")
    'ship_to_country'
    >>> normalize("2020 Revenue")
    'n2020_revenue'
    >>> normalize("from")
    'from_'
    >>> normalize("---")
    'column'
    >>> normalize(normalize("HTTPServerError")) == normalize("HTTPServerError")
    True
    """
    if options.replace_symbols and options.symbol_words:
        text = _apply_symbol_words(name, options.symbol_words)
    else:
        text = name

    text = _strip_accents(text, options.ascii_only)

    if options.split_camel_case:
        text = _split_camel_case(text)

    text = _NON_ALNUM_RUN.sub("_", text)
    text = _UNDERSCORE_RUN.sub("_", text).strip("_")

    if options.lower:
        text = text.lower()

    if not text:
        text = options.fallback

    if _LEADING_DIGIT.match(text):
        text = f"{options.digit_prefix}{text}"

    lowered = text.lower()
    if options.avoid_python_keywords and keyword.iskeyword(lowered):
        text = f"{text}_"
    elif options.avoid_sql_keywords and lowered in SQL_RESERVED:
        text = f"{text}_"

    if options.max_length and len(text) > options.max_length:
        text = text[: options.max_length].rstrip("_") or options.fallback

    return text


def normalize_all(names: list[str], options: Options = DEFAULT_OPTIONS) -> list[str]:
    """Normalize every name and make the resulting list unique, preserving order.

    A name that would collide with an earlier one gets ``_2``, ``_3``, ...
    appended (truncated to ``options.max_length`` if that is set).

    >>> normalize_all(["Total", "total", "TOTAL"])
    ['total', 'total_2', 'total_3']
    >>> normalize_all(["a", "b", "a"])
    ['a', 'b', 'a_2']
    """
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        base = normalize(name, options)
        candidate = base
        counter = 2
        while candidate in seen:
            suffix = f"_{counter}"
            stem = base
            if options.max_length and len(stem) + len(suffix) > options.max_length:
                stem = stem[: options.max_length - len(suffix)].rstrip("_")
            candidate = f"{stem}{suffix}"
            counter += 1
        seen.add(candidate)
        out.append(candidate)
    return out


def build_mapping(names: list[str], options: Options = DEFAULT_OPTIONS) -> dict[str, str]:
    """Return ``{original: normalized}`` for ``names`` (de-duplicated as a list).

    If the same original string appears twice, the last mapping wins -- use
    :func:`normalize_all` when you need one entry per input position.

    >>> build_mapping(["A B", "A-B"])
    {'A B': 'a_b', 'A-B': 'a_b_2'}
    """
    return dict(zip(names, normalize_all(names, options)))


# --- CLI ------------------------------------------------------------------- #


def _read_names(args: argparse.Namespace) -> list[str]:
    if args.names:
        return list(args.names)

    source = sys.stdin if args.csv == "-" or args.stdin else None
    if args.csv and args.csv != "-":
        with open(args.csv, newline="", encoding="utf-8") as handle:
            return next(csv.reader(handle), [])
    if args.csv == "-":
        return next(csv.reader(sys.stdin), [])
    if source is not None:  # --stdin: one name per line
        return [line.rstrip("\n") for line in source if line.strip()]
    return []


def _options_from_args(args: argparse.Namespace) -> Options:
    return replace(
        Options(),
        replace_symbols=not args.no_symbols,
        split_camel_case=not args.no_split_camel,
        lower=not args.keep_case,
        avoid_sql_keywords=args.sql_reserved,
        max_length=args.max_length,
        digit_prefix=args.digit_prefix,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("names", nargs="*", help="column name(s) to normalize")
    parser.add_argument(
        "--csv",
        metavar="FILE",
        help="read the header row from FILE (or '-' for stdin as CSV)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="read names from stdin, one per line",
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON array of {original, normalized}")
    parser.add_argument("--names-only", action="store_true", help="print only the normalized names, one per line")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 (and list the changes on stderr) if any name is not already normalized",
    )
    parser.add_argument("--no-symbols", action="store_true", help="do not turn %% & # @ $ + into words")
    parser.add_argument("--no-split-camel", action="store_true", help="do not split camelCase / PascalCase")
    parser.add_argument("--keep-case", action="store_true", help="do not lower-case (keep UPPER_SNAKE)")
    parser.add_argument("--sql-reserved", action="store_true", help="also append _ to common SQL reserved words")
    parser.add_argument("--max-length", type=int, default=0, metavar="N", help="truncate names to N characters (0 = no limit)")
    parser.add_argument("--digit-prefix", default=DEFAULT_DIGIT_PREFIX, metavar="STR", help="prefix for names starting with a digit (default 'n'; '' to keep the digit)")
    parser.add_argument("--selftest", action="store_true", help="run the built-in doctests and assertions and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    names = _read_names(args)
    if not names:
        parser.error("provide column name(s), or --csv FILE, or --stdin")

    options = _options_from_args(args)
    normalized = normalize_all(names, options)
    pairs = list(zip(names, normalized))

    if args.check:
        changed = [(old, new) for old, new in pairs if old != new]
        for old, new in changed:
            print(f"{old!r} -> {new!r}", file=sys.stderr)
        return 1 if changed else 0

    if args.json:
        print(json.dumps([{"original": o, "normalized": n} for o, n in pairs], indent=2))
    elif args.names_only:
        for _, new in pairs:
            print(new)
    else:
        width = max((len(o) for o in names), default=0)
        for old, new in pairs:
            print(f"{old:<{width}}  {new}")
    return 0


def _selftest() -> int:
    import doctest

    failures, _ = doctest.testmod(verbose=False)

    assert normalize("Unit Price ($)") == "unit_price_usd"
    assert normalize("customerID") == "customer_id"
    assert normalize("HTTPServerError") == "http_server_error"
    assert normalize("v2Model") == "v2_model"
    assert normalize("  notes  ") == "notes"
    assert normalize("2020") == "n2020"
    assert normalize("2020", Options(digit_prefix="")) == "2020"
    assert normalize("class") == "class_"
    assert normalize("select", Options(avoid_sql_keywords=True)) == "select_"
    assert normalize("select") == "select"  # not a Python keyword
    assert normalize("Ünit Prïce") == "unit_price"
    assert normalize("!!!") == "column"
    assert normalize("a_very_long_name", Options(max_length=6)) == "a_very"
    for probe in ["Weird Name #1", "col-2", "ABCDef", "  ", "é", "Total (USD)"]:
        assert normalize(normalize(probe)) == normalize(probe), probe

    assert normalize_all(["Total", "total", "TOTAL"]) == ["total", "total_2", "total_3"]
    assert normalize_all(["x", "y"]) == ["x", "y"]
    assert normalize_all(["dup", "dup", "dup_2"]) == ["dup", "dup_2", "dup_2_2"]
    assert build_mapping(["A B", "A-B"]) == {"A B": "a_b", "A-B": "a_b_2"}

    long_dedup = normalize_all(["abcdef", "abcdef"], Options(max_length=6))
    assert long_dedup == ["abcdef", "abcd_2"], long_dedup

    if failures:
        print(f"{failures} doctest failure(s)", file=sys.stderr)
        return 1
    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
