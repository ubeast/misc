"""Tests for :mod:`ic_filename_parser.parser`."""

from __future__ import annotations

from ic_filename_parser.parser import COLUMNS, parse_filename


def test_dlms_basic() -> None:
    r = parse_filename("004010M511_3_MA05.pdf")
    assert r.X12_Version == "004010"
    assert r.Format == "M"
    assert r.Transaction_Set == "511"
    assert r.Major_Gen == "3"
    assert r.Track == "M"
    assert r.Track_Description == "Modification & Maintenance"
    assert r.State == "A"
    assert r.Revision_Number == "5"
    assert r.Version_Suffix == "3MA05"
    assert r.Track_Inferred is False
    assert r.Unparsed_Trailing == ""


def test_dlms_with_month_year_publication_date() -> None:
    r = parse_filename("004010M511_3_MA05_Aug2024.pdf")
    assert r.Publication_Date == "2024-08-01"
    assert r.Publication_Date_Precision == "month"


def test_dlms_with_iso_date_and_adc() -> None:
    r = parse_filename("004010M511_3_MA05_20220803_ADC_1234.pdf")
    assert r.Publication_Date == "2022-08-03"
    assert r.Publication_Date_Precision == "day"
    assert r.ADC_Reference == "ADC_1234"


def test_dlms_adc_without_publication_date() -> None:
    # Regression: the pub_date group used to swallow "ADC", losing the
    # reference and dumping the number into Unparsed_Trailing.
    r = parse_filename("004010M511_3_MA05_ADC_1234.pdf")
    assert r.ADC_Reference == "ADC_1234"
    assert r.Publication_Date == ""
    assert r.Unparsed_Trailing == ""


def test_dlms_suffix_parsed() -> None:
    r = parse_filename("004010M511_INV_3_MA05.pdf")
    assert r.DLMS_Suffix == "INV"
    assert r.Major_Gen == "3"
    assert r.Track == "M"
    assert r.Unparsed_Trailing == ""


def test_dlms_997_infers_functional_ack_track() -> None:
    r = parse_filename("004010F997_1_A01.pdf")
    assert r.Track == "F"
    assert r.Track_Inferred is True


def test_dlms_unknown_track_labeled_unknown() -> None:
    # Regression: unknown tracks used to be labelled "Functional Acknowledgement".
    r = parse_filename("004010M511_3_ZA05.pdf")
    assert r.Track == "Z"
    assert r.Track_Description == "Unknown"


def test_dteb_without_release_does_not_crash() -> None:
    # Regression: optional release group -> None.upper() AttributeError.
    r = parse_filename("41D856.pdf")
    assert r.Format == "D"
    assert r.Transaction_Set == "856"
    assert r.X12_Version == "004010"
    assert r.Version_Suffix == "Release_A"


def test_dteb_with_release() -> None:
    r = parse_filename("41D856_B.pdf")
    assert r.Version_Suffix == "Release_B"


def test_fallback_non_standard() -> None:
    r = parse_filename("random_document.pdf")
    assert r.Track_Description == "Non-Standard EDI Convention"
    assert r.X12_Version == ""
    assert r.Format == ""
    assert r.Unparsed_Trailing == ""


def test_ic_shaped_but_unparsable_is_flagged() -> None:
    # The documented "generation digit glued to the transaction set" case:
    # no pattern matches, but the name is clearly IC-shaped, so the base is
    # echoed into Unparsed_Trailing rather than filed silently as non-standard.
    r = parse_filename("004010M0511_3_MA05.pdf")
    assert r.Track_Description == "Non-Standard EDI Convention"
    assert r.Unparsed_Trailing == "004010M0511_3_MA05"


def test_duplicate_download_suffix_stripped() -> None:
    r = parse_filename("004010M511_3_MA05 (1).pdf")
    assert r.Transaction_Set == "511"
    assert r.Revision_Number == "5"


def test_record_field_order_matches_columns() -> None:
    from dataclasses import asdict

    r = parse_filename("random_document.pdf")
    assert list(asdict(r).keys()) == COLUMNS
