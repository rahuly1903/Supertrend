"""Index constituent CSVs (niftyindices.com) and ASM/GSM surveillance lists (NSE)."""
from __future__ import annotations

import io
import logging

import pandas as pd

from sources.http import HttpClient, NseClient, SourceError

log = logging.getLogger(__name__)

_CSV_COLUMNS = {
    "Company Name": "name",
    "Industry": "industry",
    "Symbol": "symbol",
    "Series": "series",
    "ISIN Code": "isin",
}

ASM_URL = "https://www.nseindia.com/api/reportASM"
GSM_URL = "https://www.nseindia.com/api/reportGSM"


def parse_constituents(text: str) -> pd.DataFrame:
    """Parse a niftyindices constituent CSV -> [symbol, name, industry, series, isin]."""
    head = text.lstrip()[:200]
    if not head.startswith("Company Name") or "Symbol" not in head:
        raise SourceError("not a constituent CSV (got HTML or unexpected header)")
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    missing = set(_CSV_COLUMNS) - set(df.columns)
    if missing:
        raise SourceError(f"constituent CSV missing columns {sorted(missing)}")
    df = df.rename(columns=_CSV_COLUMNS)[["symbol", "name", "industry", "series", "isin"]]
    for c in df.columns:
        df[c] = df[c].str.strip()
    df = df.dropna(subset=["symbol", "isin"]).drop_duplicates("isin")
    if df.empty:
        raise SourceError("constituent CSV is empty")
    return df.reset_index(drop=True)


def fetch_constituents(client: HttpClient, base_url: str, file: str) -> pd.DataFrame:
    return parse_constituents(client.get(base_url + file).text)


def parse_asm_gsm(asm: dict, gsm: list) -> pd.DataFrame:
    """-> [list_type, symbol, isin, stage]"""
    rows = []
    for term, prefix in (("longterm", "LT"), ("shortterm", "ST")):
        for r in (asm.get(term) or {}).get("data") or []:
            rows.append(("ASM", r.get("symbol"), r.get("isin"),
                         f"{prefix} {r.get('asmSurvIndicator') or ''}".strip()))
    for r in gsm or []:
        rows.append(("GSM", r.get("symbol"), r.get("isin"), r.get("gsmStage")))
    df = pd.DataFrame(rows, columns=["list_type", "symbol", "isin", "stage"])
    return df.dropna(subset=["symbol"]).drop_duplicates(["list_type", "symbol"])


def fetch_asm_gsm(client: NseClient) -> pd.DataFrame:
    asm = client.get(ASM_URL).json()
    gsm = client.get(GSM_URL).json()
    if not isinstance(asm, dict) or not isinstance(gsm, list):
        raise SourceError("unexpected ASM/GSM payload shape")
    return parse_asm_gsm(asm, gsm)
