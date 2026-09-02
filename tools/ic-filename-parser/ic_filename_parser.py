#!/usr/bin/env python3
"""Parse EBSO / DLMS / DTEB Interface Change (IC) PDF filenames into a table.

Single file, standard library only -- meant to be copied and pasted into a
Python session (or dropped onto a machine with nothing installed) and run
as-is. This is the whole implementation; there is no package.

    # CSV for every *.pdf in ./downloads, to stdout
    python ic_filename_parser.py ./downloads

    # to a file, and also onto the clipboard
    python ic_filename_parser.py ./downloads -o ics.csv --clipboard

    # as a library (put this file on sys.path, then import it by name)
    from ic_filename_parser import parse_filename, scan_directory, rows_to_csv
    rec = parse_filename("004010M511_3_MA05_20220803_ADC_1234.pdf")
    rows = scan_directory(Path("./downloads"))          # list[ICRecord]
    print(rows_to_csv(rows))

Three filename families are recognised, tried in this order:

1. DLMS  -- e.g. ``004010M511_3_MA05_20220803_ADC_1234.pdf``
   ``<x12 version><format><txn set>[_suffix]_<gen>[_<track>]<state><rev>``
   optionally followed by ``_<pubdate>`` and/or ``_ADC_<n>``.
2. DTEB  -- e.g. ``41D856_B.pdf`` -> ``<ver prefix>D<txn set>[_<release>]``.
3. Fallback -- anything else is recorded as a non-standard convention with
   empty structured fields.

Known limitation: the DLMS pattern's ``<txn set>`` is exactly three digits. A
filename whose transaction-set segment is written with a leading digit that is
really part of the generation number (``...M0511...``) will mis-split, or fail
the DLMS pattern outright. Such a name is still visible: an IC-shaped name
(``<4-6 digits><letter><3 digits>...``) that no pattern matches gets its full
base echoed into ``Unparsed_Trailing`` rather than being filed silently as
"Non-Standard EDI Convention". A partial match keeps its leftover text there
too.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from datetime import date
from enum import Enum
from pathlib import Path

__all__ = [
    "DatePrecision",
    "ParsedDate",
    "parse_ebso_date",
    "format_ebso_date",
    "MONTH_MAP",
    "TRACK_MAP",
    "STATE_MAP",
    "DTEB_VER_MAP",
    "ICRecord",
    "COLUMNS",
    "parse_filename",
    "iter_pdf_files",
    "scan_directory",
    "rows_to_csv",
    "main",
]

# ---------------------------------------------------------------------------
# Reference data. Domain constants, not configuration -- they change only when
# DLMS itself changes. ``.get(key, "Unknown")`` is the intended access pattern
# so an unrecognised code is surfaced rather than silently mapped to something
# plausible-but-wrong.
# ---------------------------------------------------------------------------

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

# Single-letter "track" code -> human description. The track groups related
# transaction sets (requisitioning, maintenance, billing, ...).
TRACK_MAP: dict[str, str] = {
    "M": "Modification & Maintenance",
    "R": "Requisitioning & Receipt",
    "G": "Government-Furnished Materiel / MCA",
    "D": "Due-In / Demand Data Exchange",
    "L": "Logistics Financials & Billing",
    "C": "Contract / Catalog / Cancellation",
    "A": "Asset Management & Advice",
    "W": "War Materiel, Waste & SDR",
    "N": "Notice / New Item Cataloging",
    "I": "Issue & Physical Inventory",
    "P": "Physical Inventory & Quality / Procurement",
    "Q": "Quality Control & Stock Readiness",
    "F": "Functional Acknowledgement / Freeze Controls",
    "S": "Shipping, Supply Status & Staging",
    "E": "Embedded Items & GFP",
}

# Single-letter baseline "state" code -> human description.
STATE_MAP: dict[str, str] = {
    "A": "Approved / Active Baseline",
    "P": "Proposed (PDC Baseline)",
    "D": "Internal Working Draft",
}

# DTEB version prefix (2 digits) -> full 6-digit X12 version/release code.
DTEB_VER_MAP: dict[str, str] = {
    "41": "004010",
    "42": "004020",
    "43": "004030",
    "51": "005010",
}

# ---------------------------------------------------------------------------
# Publication-date parsing.
#
# The date tokens embedded in IC filenames are wildly inconsistent: numeric
# ``YYYYMMDD``, ``Aug2024`` (month + year only), ``Aug152024`` (full date,
# 4-digit year), ``Aug1524`` (full date, 2-digit year). ``parse_ebso_date``
# tries a fixed, documented sequence of shapes and returns the first that
# yields a real calendar date.
#
# The patterns are tried most specific first. The "month + year" shape
# (pattern 4) is checked before "month + day + 2-digit year" (pattern 5) but
# after "month + day + 4-digit year" (pattern 3). That ordering is what stops
# ``Aug2024`` being read as "the 20th of August" by a greedy day group.
#
# A 4-digit run is treated as a *year* only inside the plausible-publication-
# year window (patterns 3 and 4); otherwise pattern 5 reads the last two
# digits as ``YY``. Widen the window if the corpus ever grows to include
# 1900s or 2100s publication dates.
# ---------------------------------------------------------------------------

# Tokens that mean "no date". Compared case-insensitively after whitespace and
# separator characters have been stripped.
_NULL_TOKENS: frozenset[str] = frozenset(
    {"", "-", "n/a", "na", "none", "nonetype", "nan", "nat", "null"}
)

_MIN_FULL_YEAR = 2000
_MAX_FULL_YEAR = 2099
_CENTURY_BASE = 2000  # 2-digit years are assumed to be 20YY.

_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YYYYMMDD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_MONTH_DAY_YEAR4_RE = re.compile(r"^(?P<month>[A-Za-z]+)(?P<day>\d{1,2})(?P<year>\d{4})$")
_MONTH_YEAR_RE = re.compile(r"^(?P<month>[A-Za-z]+)(?P<year>\d{4})$")
_MONTH_DAY_YEAR2_RE = re.compile(r"^(?P<month>[A-Za-z]+)(?P<day>\d{1,2})(?P<year>\d{2})$")

# Separators to drop before shape-matching ("_Aug_2024_" -> "Aug2024"). ISO
# dates are matched first, so their hyphens are never reached by this.
_SEP_RE = re.compile(r"[_\s]+")


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
    and "could not be understood" (``"garbage"``, ``"Foo2024"``).
    """
    if raw is None:
        return None
    token = _SEP_RE.sub("", str(raw).strip())
    if token.lower() in _NULL_TOKENS:
        return None

    # 1. ISO 8601 -- already normalised, just validate.
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

    # 3. <Month><day><4-digit year>, e.g. "Aug152024". The 4-digit run is only
    #    accepted as a year inside the plausible-year window, same as pattern 4.
    m = _MONTH_DAY_YEAR4_RE.match(token)
    if m:
        mon = _month_number(m["month"])
        year = int(m["year"])
        if mon and _MIN_FULL_YEAR <= year <= _MAX_FULL_YEAR:
            d = _safe_date(year, mon, int(m["day"]))
            if d:
                return ParsedDate(d, DatePrecision.DAY)

    # 4. <Month><4-digit year>, e.g. "Aug2024" -> first of month.
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


# ---------------------------------------------------------------------------
# Filename parsing.
# ---------------------------------------------------------------------------

# Trailing " (1)" / " (2)" that browsers append to duplicate downloads.
_DUPLICATE_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")

DLMS_PATTERN = re.compile(
    r"^(?P<x12_ver>\d{4,6})"
    r"(?P<format_type>[A-Za-z])"
    r"(?P<trans_set>\d{3})"
    r"(?:_?(?P<dlms_suffix>[A-Za-z/]+?))?"
    r"_?(?P<major_gen>\d{1,2})"
    r"_?(?:(?P<track>[A-Za-z]))?"
    r"(?P<state>[A-Za-z])"
    r"(?P<rev_num>\d{2})"
    # The negative lookahead keeps an "_ADC_1234" / "_ADC1234" segment that has
    # no preceding date from being swallowed here as a bogus date token.
    r"(?:_(?P<pub_date>(?!ADC(?:[_\s]|\d))[A-Za-z0-9]+))?"
    r"(?:_ADC[_\s]*(?P<adc_num>[A-Za-z0-9]+))?"
    r"(?P<trailing>.*)$",
    re.IGNORECASE,
)

DTEB_PATTERN = re.compile(
    r"^(?P<dteb_ver>\d{2})"
    r"(?P<format_type>D)"
    r"(?P<trans_set>\d{3})"
    r"(?:_(?P<release>[A-Za-z0-9]+))?"
    r"(?P<trailing>.*)$",
    re.IGNORECASE,
)

# An IC-shaped name (version + format letter + 3-digit txn set) that the DLMS
# pattern could not fully consume. Used only to route the leftover text into
# ``Unparsed_Trailing`` instead of losing it to the fallback.
_IC_SHAPED_RE = re.compile(r"^\d{4,6}[A-Za-z]\d{3}.+$", re.IGNORECASE)


@dataclass
class ICRecord:
    """One parsed filename. Field order is the output column order."""

    FileName: str
    IC_Identifier: str
    X12_Version: str
    Format: str
    Transaction_Set: str
    DLMS_Suffix: str
    Major_Gen: str
    Track: str
    Track_Description: str
    State: str
    State_Description: str
    Revision_Number: str
    Version_Suffix: str
    Publication_Date: str
    Publication_Date_Precision: str
    ADC_Reference: str
    Track_Inferred: bool
    Unparsed_Trailing: str


COLUMNS: list[str] = [f.name for f in fields(ICRecord)]


def parse_filename(file_name: str) -> ICRecord:
    """Parse one ``*.pdf`` filename (with or without the extension)."""
    base_name = Path(file_name).stem
    cleaned_base = _DUPLICATE_SUFFIX_RE.sub("", base_name).strip()

    dlms = DLMS_PATTERN.match(cleaned_base)
    if dlms:
        return _build_dlms(file_name, cleaned_base, dlms)

    dteb = DTEB_PATTERN.match(cleaned_base)
    if dteb:
        return _build_dteb(file_name, cleaned_base, dteb)

    return _build_fallback(file_name, cleaned_base)


def _trailing(match: "re.Match[str]") -> str:
    return (match.groupdict().get("trailing") or "").lstrip("_ ")


def _build_dlms(file_name: str, cleaned_base: str, match: "re.Match[str]") -> ICRecord:
    data = match.groupdict()

    trans_set = data["trans_set"]
    major_gen = str(int(data["major_gen"]))
    raw_rev = data["rev_num"]

    # Track: use the parsed letter if present; otherwise infer one and flag it.
    if data.get("track"):
        track_val = data["track"].upper()
        track_inferred = False
    elif trans_set == "997":
        track_val = "F"  # X12 997 == Functional Acknowledgement
        track_inferred = True
    else:
        track_val = "S"  # best-effort default; see Track_Inferred
        track_inferred = True

    state_val = data["state"].upper()
    parsed_date = parse_ebso_date(data.get("pub_date"))

    return ICRecord(
        FileName=file_name,
        IC_Identifier=cleaned_base.split("_")[0],
        X12_Version=data["x12_ver"].zfill(6),
        Format=data["format_type"].upper(),
        Transaction_Set=trans_set,
        DLMS_Suffix=data["dlms_suffix"] or "",
        Major_Gen=major_gen,
        Track=track_val,
        Track_Description=TRACK_MAP.get(track_val, "Unknown"),
        State=state_val,
        State_Description=STATE_MAP.get(state_val, "Unknown"),
        Revision_Number=str(int(raw_rev)),
        Version_Suffix=f"{major_gen}{track_val}{state_val}{raw_rev}",
        Publication_Date=parsed_date.iso if parsed_date else "",
        Publication_Date_Precision=parsed_date.precision.value if parsed_date else "",
        ADC_Reference=f"ADC_{data['adc_num']}" if data.get("adc_num") else "",
        Track_Inferred=track_inferred,
        Unparsed_Trailing=_trailing(match),
    )


def _build_dteb(file_name: str, cleaned_base: str, match: "re.Match[str]") -> ICRecord:
    data = match.groupdict()
    ver_prefix = data["dteb_ver"]
    # ``or "A"`` (not ``.get(..., "A")``) because the optional group leaves the
    # key present with value ``None`` when no release is in the filename.
    release = (data.get("release") or "A").upper()

    return ICRecord(
        FileName=file_name,
        IC_Identifier=cleaned_base,
        X12_Version=DTEB_VER_MAP.get(ver_prefix, f"00{ver_prefix}00").zfill(6),
        Format="D",
        Transaction_Set=data["trans_set"],
        DLMS_Suffix="",
        Major_Gen="1",
        Track="D",
        Track_Description="Defense Transportation Electronic Business (DTEB)",
        State="A",
        State_Description="Approved / Active Baseline",
        Revision_Number="1",
        Version_Suffix=f"Release_{release}",
        Publication_Date="",
        Publication_Date_Precision="",
        ADC_Reference="",
        Track_Inferred=False,
        Unparsed_Trailing=_trailing(match),
    )


def _build_fallback(file_name: str, cleaned_base: str) -> ICRecord:
    # If the name is IC-shaped but no pattern matched (e.g. the "...M0511..."
    # glued-digit case), surface the whole base in Unparsed_Trailing so it is
    # reviewable rather than quietly filed as a non-standard convention.
    ic_shaped = _IC_SHAPED_RE.match(cleaned_base) is not None
    return ICRecord(
        FileName=file_name,
        IC_Identifier=cleaned_base,
        X12_Version="",
        Format="",
        Transaction_Set="",
        DLMS_Suffix="",
        Major_Gen="",
        Track="",
        Track_Description="Non-Standard EDI Convention",
        State="",
        State_Description="",
        Revision_Number="",
        Version_Suffix="",
        Publication_Date="",
        Publication_Date_Precision="",
        ADC_Reference="",
        Track_Inferred=False,
        Unparsed_Trailing=cleaned_base if ic_shaped else "",
    )


# ---------------------------------------------------------------------------
# Directory scan + CSV output.
# ---------------------------------------------------------------------------


def iter_pdf_files(directory: Path) -> list[Path]:
    """Return the ``*.pdf`` files in ``directory``, case-insensitive, sorted.

    Not recursive -- point it at the folder that holds the PDFs.
    """
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


def scan_directory(directory: Path) -> list[ICRecord]:
    """Parse every PDF filename in ``directory`` into a list of records."""
    return [parse_filename(p.name) for p in iter_pdf_files(directory)]


def rows_to_csv(rows: list[ICRecord]) -> str:
    """Render records as CSV text with the fixed :data:`COLUMNS` header.

    The header is always written, even for an empty ``rows``, so downstream
    consumers get a stable schema.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(asdict(row))
    return buffer.getvalue()


def _copy_to_clipboard(text: str) -> None:
    """Best-effort clipboard copy using whatever platform tool is present."""
    candidates: list[list[str]]
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]
    elif sys.platform.startswith("win"):
        candidates = [["clip"]]
    else:
        candidates = [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]

    for cmd in candidates:
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return
        except (OSError, subprocess.CalledProcessError):
            continue
    print("warning: could not copy to clipboard (no pbcopy/clip/xclip/xsel)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse EBSO/DLMS/DTEB IC PDF filenames into a table."
    )
    parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=Path.cwd(),
        help="Directory to scan for *.pdf files (default: current directory).",
    )
    parser.add_argument(
        "-o", "--output", type=Path,
        help="Write CSV to this path instead of stdout.",
    )
    parser.add_argument(
        "--clipboard", action="store_true",
        help="Also copy the table to the system clipboard.",
    )
    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        parser.error(f"not a directory: {args.directory}")

    csv_text = rows_to_csv(scan_directory(args.directory))

    if args.output:
        args.output.write_text(csv_text, newline="")
    else:
        sys.stdout.write(csv_text)

    if args.clipboard:
        _copy_to_clipboard(csv_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
