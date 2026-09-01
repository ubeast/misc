"""Lookup tables for decoding DLMS / DTEB Interface Change filename codes.

These are domain reference data, not configuration -- they change only when
DLMS itself changes. ``.get(key, "Unknown")`` is the intended access pattern
everywhere so an unrecognised code is surfaced rather than silently mapped to
something plausible-but-wrong.
"""

from __future__ import annotations

__all__ = ["TRACK_MAP", "STATE_MAP", "DTEB_VER_MAP"]

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
