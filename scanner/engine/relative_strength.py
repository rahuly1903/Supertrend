"""Relative strength: IBD-style RS Rating (1-99 percentile across the universe)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def pct_rank(s: pd.Series) -> pd.Series:
    """Percentile rank 0-100 (higher value -> higher rank); NaN stays NaN."""
    return s.rank(pct=True, method="average") * 100


def rs_rating(df: pd.DataFrame, weights: dict[str, float]) -> tuple[pd.Series, pd.Series]:
    """rs_raw = sum(w_k * ret_k) over available horizons (weights renormalised), ret_3m required.

    New listings without a 12M history still get a rating from the horizons they have.
    Returns (rs_raw, rs_rating 1..99).
    """
    rets = df[list(weights)]
    w = pd.Series(weights)
    avail = rets.notna()
    raw = (rets.fillna(0) * w).sum(axis=1) / (avail * w).sum(axis=1).replace(0, np.nan)
    raw[rets[list(weights)[0]].isna()] = np.nan
    pct = raw.rank(pct=True, method="average")
    rating = np.ceil(pct * 99).clip(1, 99)
    return raw, rating
