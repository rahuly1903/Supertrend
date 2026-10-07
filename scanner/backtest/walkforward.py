"""Walk-forward selection over a parameter sweep.

Every parameter set is simulated once over the whole history (each run is causal on its own).
Then, rolling forward:

    train window  [T - train_years, T)   score every run on its returns inside the window
    test window   [T, T + test_years)    trade the top-k runs (equal blend of their weekly
                                         returns) without having seen this window
    T += test_years

The stitched test windows form an out-of-sample equity curve: the parameters used in any week
were chosen from data that ended before it. Comparing it with the in-sample best run shows how
much of the sweep's "best" result was luck.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.engine import benchmark_nav, curve_metrics, metrics


@dataclass
class RunRecord:
    id: int
    name: str
    equity: pd.DataFrame      # date, equity, invested_pct, positions, benchmark [, nav, flow]
    trades: pd.DataFrame


def window_score(values: np.ndarray, years: float, metric: str) -> float:
    if len(values) < 2 or years <= 0:
        return -math.inf
    cagr = (values[-1] / values[0]) ** (1 / years) - 1
    if metric == "cagr":
        return cagr
    if metric == "sharpe":
        r = np.diff(values) / values[:-1]
        return r.mean() / r.std() * np.sqrt(52) if r.std() > 0 else -math.inf
    dd = (values / np.maximum.accumulate(values) - 1).min()
    return cagr / -dd if dd < 0 else cagr * 100


def folds(dates: pd.DatetimeIndex, train_years: float, test_years: float, anchored: bool):
    first, last = dates[0], dates[-1]
    off = lambda y: pd.DateOffset(months=round(y * 12))  # noqa: E731
    t = first + off(train_years)
    while t < last:
        yield (first if anchored else t - off(train_years)), t, min(t + off(test_years), last)
        t = t + off(test_years)


def slice_metrics(eq: pd.DataFrame, start, end) -> dict:
    seg = eq[(eq["date"] >= start) & (eq["date"] <= end)]
    if len(seg) < 2:
        return {}
    m = curve_metrics(seg["nav"] if "nav" in seg else seg["equity"], seg["date"])
    m["mar"] = m["cagr_pct"] / -m["max_drawdown_pct"] if m["max_drawdown_pct"] < 0 else None
    b = curve_metrics(benchmark_nav(seg, 1.0), seg["date"])
    m["benchmark_cagr_pct"] = b["cagr_pct"]
    return m


TRADE_KEY = ["symbol_id", "symbol", "industry", "signal_date", "entry_date", "entry_price", "exit_date",
             "exit_price", "weeks_held", "exit_reason"]


def merge_trades(t: pd.DataFrame) -> pd.DataFrame:
    """Blended sets often hold the same stock over the same weeks: report it once, sizes summed."""
    key = [k for k in TRADE_KEY if k in t.columns]
    if not key:
        return t
    agg = {c: ("sum" if c in ("qty", "pnl") else "mean") for c in t.columns if c not in key}
    out = t.groupby(key, dropna=False, sort=False).agg(agg).reset_index()
    out["qty"] = out["qty"].round().astype(int)
    order = [c for c in ("entry_date", "symbol") if c in out.columns]
    return out[t.columns].sort_values(order, kind="stable").reset_index(drop=True)


def walk_forward(runs: list[RunRecord], capital: float, train_years: float, test_years: float,
                 anchored: bool, metric: str, top_k: int, min_trades_per_year: float) -> dict:
    dates = pd.DatetimeIndex(runs[0].equity["date"])
    for r in runs:
        if not pd.DatetimeIndex(r.equity["date"]).equals(dates):
            raise ValueError(f"run {r.name} has a different calendar; walk-forward needs one calendar")
    # time-weighted curves: runs that add capital must not count it as return
    eq = np.column_stack([r.equity["nav" if "nav" in r.equity else "equity"].to_numpy(float) for r in runs])
    inv = np.column_stack([r.equity["invested_pct"].to_numpy(float) for r in runs])
    pos = np.column_stack([r.equity["positions"].to_numpy(float) for r in runs])
    rets = eq[1:] / eq[:-1] - 1                      # return realised at dates[1:]
    bench = benchmark_nav(runs[0].equity, capital).to_numpy(float)
    entry = [pd.DatetimeIndex(r.trades["entry_date"]) if not r.trades.empty else pd.DatetimeIndex([])
             for r in runs]

    out_rows, out_trades, fold_info = [], [], []
    value = capital
    for tr_start, t0, t1 in folds(dates, train_years, test_years, anchored):
        tr = (dates >= tr_start) & (dates <= t0)
        yrs = (dates[tr][-1] - dates[tr][0]).days / 365.25
        scores = []
        for j, r in enumerate(runs):
            n_tr = ((entry[j] > tr_start) & (entry[j] <= t0)).sum()
            if yrs <= 0 or n_tr / yrs < min_trades_per_year:
                continue
            scores.append((window_score(eq[tr, j], yrs, metric), j))
        scores.sort(reverse=True)
        chosen = [j for _, j in scores[:top_k]]
        if not chosen:
            continue
        te = np.where((dates[1:] > t0) & (dates[1:] <= t1))[0]      # indexes into rets
        if not len(te):
            continue
        if not out_rows:
            out_rows.append({"date": dates[te[0]], "equity": value, "invested_pct": 0.0, "positions": 0,
                             "benchmark": bench[te[0]]})
        start_value = value
        for k in te:
            value *= 1 + rets[k, chosen].mean()
            out_rows.append({"date": dates[k + 1], "equity": value, "invested_pct": inv[k + 1, chosen].mean(),
                             "positions": round(pos[k + 1, chosen].mean()), "benchmark": bench[k + 1]})
        for j in chosen:
            t = runs[j].trades
            if t.empty:
                continue
            sel = t[(t["entry_date"] > t0) & (t["entry_date"] <= t1)].copy()
            # resize to this portfolio: the run's equity at the fold start may be far from ours
            scale = start_value / eq[te[0], j] / len(chosen)
            sel["pnl"] = sel["pnl"] * scale
            sel["qty"] = sel["qty"] * scale
            out_trades.append(sel)
        fold_info.append({
            "train_start": str(pd.Timestamp(tr_start).date()), "test_start": str(pd.Timestamp(t0).date()),
            "test_end": str(pd.Timestamp(t1).date()), "return_pct": (value / start_value - 1) * 100,
            "benchmark_return_pct": (bench[te[-1] + 1] / bench[te[0]] - 1) * 100,
            "chosen": [{"id": runs[j].id, "name": runs[j].name,
                        "train_score": next(s for s, i in scores if i == j)} for j in chosen],
        })

    if not out_rows:
        raise SystemExit("history too short for walk-forward: need more than train_years of backtest weeks")
    equity = pd.DataFrame(out_rows)
    equity["benchmark"] = equity["benchmark"] / equity["benchmark"].iloc[0] * capital
    equity["drawdown"] = (equity["equity"] / equity["equity"].cummax() - 1) * 100
    trades = merge_trades(pd.concat(out_trades, ignore_index=True)) if out_trades else pd.DataFrame(
        columns=runs[0].trades.columns)
    return {"equity": equity, "trades": trades, "metrics": metrics(equity, trades, capital),
            "benchmark_metrics": curve_metrics(equity["benchmark"], equity["date"]), "folds": fold_info,
            "oos_start": equity["date"].iloc[0], "oos_end": equity["date"].iloc[-1]}
