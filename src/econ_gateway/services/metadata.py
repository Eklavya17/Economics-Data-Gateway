"""Build ``SeriesMeta`` from the catalog, FRED metadata, or BLS metadata.

Lookup order is cheapest first: the local catalog costs nothing; FRED metadata costs one
request (cached); BLS metadata costs one of the 500 daily BLS queries, so it is used only for
BLS series that are not in the catalog.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from econ_gateway.consts import BLS_SERIES_URL, FRED_SERIES_URL
from econ_gateway.models import SeriesMeta, Source
from econ_gateway.services.upstream import Upstream

FREQUENCY_WORDS = {
    "D": "daily",
    "W": "weekly",
    "BW": "biweekly",
    "M": "monthly",
    "Q": "quarterly",
    "S": "semiannual",
    "SA": "semiannual",
    "A": "annual",
}
NATIONAL = "United States"


def series_url(source: Source, series_id: str) -> str:
    """Human-readable page for a series at its source."""
    template = BLS_SERIES_URL if source == "BLS" else FRED_SERIES_URL
    return template.format(series_id=series_id)


def frequency_words(code: str | None) -> str:
    """Map frequency codes (``M``, ``Q``, ``Monthly``...) to lower-case words."""
    if not code:
        return "unknown"
    return FREQUENCY_WORDS.get(code.strip().upper(), code.strip().lower())


def is_rate(units: str) -> bool:
    """Whether values are percentages, whose changes should be read in percentage points.

    A percent change *of a rate* (unemployment 4.0% -> 4.4% is "+10%") is almost always
    misleading, so tools report percentage-point changes for these series instead. BLS
    metadata names units like "unemployment rate" (no "percent"; seen live for LAUS metros),
    so the word "rate" counts too.
    """
    text = units.lower()
    return "percent" in text or "%" in text or re.search(r"\brates?\b", text) is not None


def catalog_row(series_id: str, catalog: pd.DataFrame) -> dict[str, str] | None:
    """Return the catalog row for ``series_id`` as a dict, or ``None``."""
    hit = catalog.loc[catalog["series_id"] == series_id]
    return None if hit.empty else {str(k): str(v) for k, v in hit.iloc[0].to_dict().items()}


def meta_from_catalog(row: Mapping[str, str]) -> SeriesMeta:
    """Metadata from a curated catalog row."""
    source: Source = "BLS" if row["source"].upper() == "BLS" else "FRED"
    return SeriesMeta(
        series_id=row["series_id"],
        source=source,
        title=row["title"],
        units=row["units"] or "unknown",
        frequency=frequency_words(row["frequency"]),
        seasonal_adjustment=row["seasonal_adjustment"] or "unknown",
        geography=row.get("geography") or NATIONAL,
        url=series_url(source, row["series_id"]),
    )


def clean_notes(text: object) -> str | None:
    """Normalize source notes: FRED uses Windows line endings and blank padding."""
    if not text:
        return None
    lines = [line.strip() for line in str(text).replace("\r\n", "\n").split("\n")]
    cleaned = "\n".join(lines).strip()
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned or None


def meta_from_fred(info: Mapping[str, Any], *, include_notes: bool) -> SeriesMeta:
    """Metadata from the FRED ``series`` endpoint."""
    series_id = str(info["id"])
    return SeriesMeta(
        series_id=series_id,
        source="FRED",
        title=str(info.get("title", series_id)),
        units=str(info.get("units") or info.get("units_short") or "unknown"),
        frequency=frequency_words(str(info.get("frequency_short") or info.get("frequency") or "")),
        seasonal_adjustment=str(info.get("seasonal_adjustment_short") or "unknown"),
        url=series_url("FRED", series_id),
        notes=clean_notes(info.get("notes")) if include_notes else None,
    )


def meta_from_bls(
    series_id: str, info: Mapping[str, Any] | None, frequency_code: str | None = None
) -> SeriesMeta:
    """Metadata for a BLS series outside the catalog.

    BLS catalog metadata (``info``) needs a registered key. Without it, the title is a
    placeholder that says how to get the real one, rather than a guess.
    """
    if info:
        seasonality = str(info.get("seasonality", "")).lower()
        if "not" in seasonality:
            seasonal = "NSA"
        elif "seasonally" in seasonality:
            seasonal = "SA"
        else:
            seasonal = "unknown"
        title = str(info.get("series_title") or series_id)
        measure = str(info.get("measure_data_type") or "")
        # BLS rates ("unemployment rate", "quits rate") are published in percent.
        units = "percent" if is_rate(measure) else (measure or "see series page")
        geography = info.get("area")
    else:
        title = f"BLS series {series_id} (title unavailable without BLS_API_KEY; see url)"
        seasonal, units, geography = "unknown", "see series page", None
    return SeriesMeta(
        series_id=series_id,
        source="BLS",
        title=title,
        units=units,
        frequency=frequency_words(frequency_code),
        seasonal_adjustment=seasonal,
        geography=str(geography) if geography else None,
        url=series_url("BLS", series_id),
    )


def lookup_meta(
    series_id: str,
    source: Source,
    upstream: Upstream,
    *,
    include_notes: bool = False,
    frequency_code: str | None = None,
) -> SeriesMeta:
    """Best available metadata for a series, cheapest source first.

    Args:
        series_id: Upper-cased series ID.
        source: Publishing system.
        upstream: Data access.
        include_notes: Also fetch FRED's notes (one cached request) for catalog FRED series.
        frequency_code: Frequency observed in fetched data, used when metadata lacks it.

    Returns:
        Metadata; never raises for missing metadata (falls back to a placeholder title).
    """
    row = catalog_row(series_id, upstream.catalog())
    if row is not None:
        meta = meta_from_catalog(row)
        if include_notes and source == "FRED" and upstream.has_fred_key:
            meta.notes = clean_notes(upstream.fred_series_info(series_id).get("notes"))
        return meta
    if source == "FRED":
        return meta_from_fred(upstream.fred_series_info(series_id), include_notes=include_notes)
    return meta_from_bls(series_id, upstream.bls_series_info(series_id), frequency_code)
