"""Turn a single IC PDF filename into a structured :class:`ICRecord`.

Three filename families are recognised, tried in this order:

1. **DLMS** -- e.g. ``004010M511_3_MA05_20220803_ADC_1234.pdf``
   ``<x12 version><format><txn set>[_suffix]_<gen>[_<track>]<state><rev>``
   optionally followed by ``_<pubdate>`` and/or ``_ADC_<n>``.
2. **DTEB** -- e.g. ``41D856_B.pdf``
   ``<ver prefix>D<txn set>[_<release>]``.
3. **Fallback** -- anything else is recorded as a non-standard convention
   with empty structured fields.

Known limitation: the DLMS pattern's ``<txn set>`` is exactly three digits.
A filename whose transaction-set segment is written with a leading digit that
is really part of the generation number (``...M0511...``) will mis-split. If
you hit that, capture real examples and tighten the pattern -- do not widen
``\\d{3}`` blindly. Whatever the pattern could not consume is preserved in
``Unparsed_Trailing`` so these cases are visible instead of silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path

from .dates import parse_ebso_date
from .mappings import DTEB_VER_MAP, STATE_MAP, TRACK_MAP

__all__ = ["ICRecord", "COLUMNS", "parse_filename"]

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
    r"(?:_(?P<pub_date>[A-Za-z0-9]+))?"
    r"(?:_ADC[_\s]*(?P<adc_num>\w+))?"
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


def _trailing(match: re.Match[str]) -> str:
    return (match.groupdict().get("trailing") or "").lstrip("_ ")


def _build_dlms(file_name: str, cleaned_base: str, match: re.Match[str]) -> ICRecord:
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
        X12_Version=data["x12_ver"].strip().zfill(6),
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


def _build_dteb(file_name: str, cleaned_base: str, match: re.Match[str]) -> ICRecord:
    data = match.groupdict()
    ver_prefix = data["dteb_ver"]
    # ``or "A"`` (not ``.get(..., "A")``) because the optional group leaves
    # the key present with value ``None`` when no release is in the filename.
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
        Unparsed_Trailing="",
    )
