"""OEWS (Occupational Employment and Wage Statistics) annual bulk files.

Wages by occupation (SOC) for the nation, states and metro areas, May reference period.
Files: https://www.bls.gov/oes/tables.htm

Each zip is downloaded once into ``RAW_DIR/oews/``; the cleaned Excel sheet is cached as parquet
in ``CACHE_DIR/oews/``. bls.gov rejects anonymous scripts, so set ``BLS_USER_AGENT`` (e.g.
``"EconGateway/1.0 (you@example.com)"``), or download the zip by hand into ``RAW_DIR/oews/``.

Wage cell flags in the source: ``*`` not available, ``**`` employment estimate not available,
``#`` wage at or above the top-coded cap. All become NaN; ``#`` also sets ``<col>_capped``.

Origin: Project 1 (``labor_agent.data.oews``, commit bdfb586), now maintained here.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Literal

import pandas as pd

from econ_gateway.sources.http import make_session
from econ_gateway.sources.settings import get_source_settings

log = logging.getLogger(__name__)

Level = Literal["metro", "state", "national", "all"]
_SUFFIX = {"metro": "ma", "state": "st", "national": "nat", "all": "all"}
OEWS_URL = "https://www.bls.gov/oes/special-requests/oesm{yy}{suffix}.zip"
DOWNLOAD_TIMEOUT_S = 300  # the metro zip is tens of MB

WAGE_COLS = [
    "h_mean", "a_mean", "h_pct10", "h_pct25", "h_median", "h_pct75", "h_pct90",
    "a_pct10", "a_pct25", "a_median", "a_pct75", "a_pct90",
]  # fmt: skip
NUM_COLS = ["tot_emp", "emp_prse", "jobs_1000", "loc_quotient", "pct_total", "pct_rpt", "mean_prse"]
CODE_COLS = ["area", "naics", "occ_code", "own_code", "prim_state", "area_type"]


class OEWSError(RuntimeError):
    """An OEWS file is unavailable (not released yet, blocked, or not a valid zip)."""


def _numeric(column: pd.Series) -> pd.Series:
    return pd.Series(pd.to_numeric(column.str.replace(",", "", regex=False), errors="coerce"))


def clean_oews(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names, codes and numbers; flag top-coded wages in ``<col>_capped``."""
    frame = frame.copy()
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    for col in CODE_COLS:
        if col in frame.columns:
            frame[col] = frame[col].astype(str).str.strip()
    for col in WAGE_COLS:
        if col in frame.columns:
            raw = pd.Series(frame[col]).astype(str).str.strip()
            frame[f"{col}_capped"] = raw.eq("#")
            frame[col] = _numeric(raw)
    for col in NUM_COLS:
        if col in frame.columns:
            frame[col] = _numeric(pd.Series(frame[col]).astype(str))
    for col in ("hourly", "annual"):
        if col in frame.columns:
            frame[col] = frame[col].astype(str).str.strip().str.upper().eq("TRUE")
    return frame


def _zip_path(year: int, level: Level, raw_dir: Path) -> Path:
    return raw_dir / "oews" / f"oesm{year % 100:02d}{_SUFFIX[level]}.zip"


def download_oews(year: int, level: Level = "metro", *, force: bool = False) -> Path:
    """Download (once) the OEWS zip for May ``year`` at ``level``; returns its path.

    Raises:
        OEWSError: Refused (403), not released (404), or not a zip.
    """
    path = _zip_path(year, level, get_source_settings().raw_dir)
    if path.exists() and not force:
        return path
    url = OEWS_URL.format(yy=f"{year % 100:02d}", suffix=_SUFFIX[level])
    path.parent.mkdir(parents=True, exist_ok=True)
    session = make_session()
    session.headers.update({"Accept": "application/zip,*/*", "Accept-Language": "en-US,en;q=0.9"})
    log.info("Downloading %s", url)
    response = session.get(url, timeout=DOWNLOAD_TIMEOUT_S, stream=True)
    if response.status_code == 403:
        raise OEWSError(
            "bls.gov refused the download (403). Set BLS_USER_AGENT in .env to something like "
            f"'EconGateway/1.0 (your-email)', or download {url} manually into {path.parent}."
        )
    if response.status_code == 404:
        raise OEWSError(f"OEWS file not found: {url} (has May {year} data been released yet?)")
    response.raise_for_status()
    partial = path.with_suffix(".part")
    with open(partial, "wb") as handle:
        for chunk in response.iter_content(1 << 20):
            handle.write(chunk)
    if not zipfile.is_zipfile(partial):
        partial.unlink(missing_ok=True)
        raise OEWSError(f"Downloaded file from {url} is not a zip (blocked or moved?).")
    partial.replace(path)
    return path


def _read_zip(path: Path) -> pd.DataFrame:
    """The largest data sheet inside an OEWS zip, as strings."""
    with zipfile.ZipFile(path) as archive:
        sheets = [
            name
            for name in archive.namelist()
            if name.lower().endswith((".xlsx", ".xls"))
            and "field_descriptions" not in name.lower()
            and not Path(name).name.startswith("~")
        ]
        if not sheets:
            raise OEWSError(f"No Excel file inside {path}")
        main = max(sheets, key=lambda name: archive.getinfo(name).file_size)
        log.info("Reading %s from %s (this can take a minute)", main, path.name)
        with archive.open(main) as handle:
            return pd.read_excel(io.BytesIO(handle.read()), dtype=str)


def load_oews(year: int, level: Level = "metro", *, refresh: bool = False) -> pd.DataFrame:
    """Cleaned OEWS table for a May reference year (e.g. 2025), cached as parquet.

    Raises:
        OEWSError: When the file cannot be downloaded (see ``download_oews``).
    """
    cached = get_source_settings().cache_dir / "oews" / f"oews_{level}_{year}.parquet"
    if cached.exists() and not refresh:
        return pd.read_parquet(cached)
    frame = clean_oews(_read_zip(download_oews(year, level)))
    frame["year"] = year
    cached.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(cached, index=False)
    return frame
