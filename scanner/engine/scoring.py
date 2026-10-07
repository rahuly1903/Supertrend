"""Stock filters, soft signals, penalties, score, grade, reasons and trade plan.

Input: one row per stock with indicator, Supertrend and sector fields (see engine/scan.py).
All thresholds and weights come from config.yaml.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import Config
from engine.relative_strength import pct_rank


def lin(x: pd.Series, zero_at: float, full_at: float) -> pd.Series:
    """Linear 0..100 score: 0 at `zero_at`, 100 at `full_at` (works for either direction)."""
    return ((x - zero_at) / (full_at - zero_at)).clip(0, 1) * 100


def _b(s: pd.Series) -> pd.Series:
    return s.fillna(False).astype(bool)


def apply_filters(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    f = cfg.filters
    bull = df["direction"] == "Bullish"
    out = pd.DataFrame(index=df.index)
    out["f_supertrend"] = bull
    out["f_sector"] = _b(df["sector_tradeable"])
    out["f_trend_template"] = _b(
        (df["price"] > df["sma50"]) & (df["sma50"] > df["sma150"]) & (df["sma150"] > df["sma200"])
        & (df["sma200"] > df["sma200_prev"]))
    out["f_52w_range"] = _b((df["pct_above_low"] >= f.min_above_low_pct) & (df["pct_from_high"] <= f.max_from_high_pct))
    out["f_rs"] = _b(df["rs_rating"] >= f.min_rs_rating)
    out["f_liquidity"] = _b(df["avg_turnover_20d_cr"] >= f.min_turnover_cr)
    out["f_price"] = _b(df["price"] >= f.min_price)
    out["f_not_asm"] = ~_b(df["in_asm_gsm"])
    out["qualified"] = out.all(axis=1)
    return out


def apply_signals(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    s, p = cfg.signals, cfg.penalties
    bull = df["direction"] == "Bullish"
    wk = df["weeks_in_trend"]
    out = pd.DataFrame(index=df.index)
    out["sig_fresh_flip"] = _b(bull & wk.between(1, s.fresh_flip_max_weeks))
    out["sig_tight_stop"] = _b(bull & (df["pct_from_st"] <= s.tight_stop_pct))
    out["sig_vol_confirm"] = _b(bull & (df["confirm_vol_ratio"] >= s.vol_confirm_ratio))
    out["sig_vcp"] = _b((df["tightness_ratio"] <= s.vcp_max_tightness) & (df["atr_pct"] < df["atr_pct_prev"]))
    out["sig_accumulation"] = _b(df["delivery_pct_latest"] > df["delivery_pct_20d"])
    out["sig_near_high"] = _b(df["pct_from_high"] <= s.near_high_pct)
    out["sig_sector_leader"] = _b(df["rs_vs_sector"] > 0)

    atr_cut = df["atr_pct"].quantile(p.high_atr_percentile / 100)
    out["pen_overextended"] = _b(((df["price"] / df["sma50"] - 1) * 100 > p.overextended_sma50_pct)
                                 | (bull & (df["pct_since_flip"] > p.overextended_since_flip_pct)))
    out["pen_high_atr"] = _b(df["atr_pct"] >= atr_cut)
    out["pen_late_stage"] = _b(bull & (wk > p.late_stage_weeks))
    return out


def score(df: pd.DataFrame, sig: pd.DataFrame, n_sectors: int, cfg: Config) -> pd.DataFrame:
    sc, s = cfg.scoring, cfg.signals
    bull = df["direction"] == "Bullish"
    wk = df["weeks_in_trend"]
    out = pd.DataFrame(index=df.index)
    out["score_rs"] = pct_rank(df["rs_rating"])
    out["score_sector"] = (100 * (n_sectors - df["sector_rank"]) / max(n_sectors - 1, 1)).clip(0, 100)
    fresh = (100 - (wk - s.fresh_flip_max_weeks).clip(lower=0) * sc.freshness_decay_per_week).clip(0, 100)
    out["score_freshness"] = fresh.where(bull, 0.0)
    out["score_near_high"] = pct_rank(-df["pct_from_high"])
    out["score_volume"] = lin(df["confirm_vol_ratio"], sc.volume_ratio_floor, sc.volume_ratio_full)
    contraction = lin(df["tightness_ratio"], sc.tightness_worst, sc.tightness_best)
    atr_falling = (df["atr_pct"] < df["atr_pct_prev"]).astype(float) * 100
    out["score_tightness"] = 0.7 * contraction.fillna(0) + 0.3 * atr_falling
    out["score_risk"] = lin(df["pct_from_st"], sc.risk_zero_pct, sc.risk_full_pct).where(bull, 0.0)

    pts = cfg.penalties.points
    out["penalty"] = (sig["pen_overextended"] * pts["overextended"] + sig["pen_high_atr"] * pts["high_atr"]
                      + sig["pen_late_stage"] * pts["late_stage"]).astype(float)
    w = sc.weights
    total = sum(w[k] * out[f"score_{k}"].fillna(0) for k in w)
    out["stock_score"] = (total - out["penalty"]).clip(0, 100).round(2)

    g = sc.grades
    grade = np.select([out["stock_score"] >= g["A+"], out["stock_score"] >= g["A"], out["stock_score"] >= g["B"]],
                      ["A+", "A", "B"], default="Watch")
    out["grade"] = pd.Series(grade, index=df.index).where(bull, None)
    return out


def reasons(df: pd.DataFrame, flt: pd.DataFrame, sig: pd.DataFrame, bear_regime: bool) -> pd.Series:
    """Short human-readable list: signals hit, penalties, failed filters."""
    out = []
    for i in df.index:
        r, f, s = df.loc[i], flt.loc[i], sig.loc[i]
        parts = []
        if r["direction"] != "Bullish":
            parts.append("Bearish" + (" (new flip)" if r["is_new_flip"] else ""))
        else:
            if s["sig_fresh_flip"]:
                parts.append(f"Fresh flip {int(r['weeks_in_trend'])}w")
            if s["sig_near_high"]:
                parts.append(f"Near high {r['pct_from_high']:.1f}%")
            if s["sig_vol_confirm"]:
                parts.append(f"Vol {r['confirm_vol_ratio']:.1f}x")
            if s["sig_tight_stop"]:
                parts.append(f"Stop {r['pct_from_st']:.1f}%")
            if s["sig_vcp"]:
                parts.append("VCP")
            if s["sig_accumulation"]:
                parts.append("Accumulation")
            if s["sig_sector_leader"]:
                parts.append("Sector leader")
        if s["pen_overextended"]:
            parts.append("Overextended")
        if s["pen_high_atr"]:
            parts.append("High ATR")
        if s["pen_late_stage"]:
            parts.append(f"Late stage {int(r['weeks_in_trend'])}w")
        fails = []
        if not f["f_sector"]:
            fails.append("Sector")
        if not f["f_trend_template"]:
            fails.append("Trend template")
        if not f["f_52w_range"]:
            fails.append("52W range")
        if not f["f_rs"]:
            fails.append(f"RS {int(r['rs_rating'])}" if pd.notna(r["rs_rating"]) else "RS n/a")
        if not f["f_liquidity"]:
            fails.append("Liquidity")
        if not f["f_price"]:
            fails.append("Price")
        if not f["f_not_asm"]:
            fails.append("ASM/GSM")
        if fails:
            parts.append("✗ " + ", ".join(fails))
        if bear_regime and r["direction"] == "Bullish":
            parts.append("Bear regime: reduce size")
        out.append(", ".join(parts))
    return pd.Series(out, index=df.index)


def trade_plan(df: pd.DataFrame, capital: float, risk_pct: float, max_position_pct: float,
               size_factor: float = 1.0) -> pd.DataFrame:
    bull = df["direction"] == "Bullish"
    stop = df["st_value"].where(bull)
    per_share = (df["price"] - stop).where(lambda x: x > 0)
    risk_amount = capital * risk_pct / 100 * size_factor
    qty = np.floor(risk_amount / per_share)
    qty = np.minimum(qty, np.floor(capital * max_position_pct / 100 / df["price"]))
    return pd.DataFrame({
        "stop_price": stop,
        "risk_pct": (per_share / df["price"] * 100),
        "position_qty": qty,
        "position_value": qty * df["price"],
    }, index=df.index)


def score_stocks(df: pd.DataFrame, n_sectors: int, cfg: Config, capital: float, risk_pct: float,
                 regime: str) -> pd.DataFrame:
    flt = apply_filters(df, cfg)
    sig = apply_signals(df, cfg)
    sc = score(df, sig, n_sectors, cfg)
    bear = regime == "bear"
    tp = trade_plan(df, capital, risk_pct, cfg.trade_plan.max_position_pct,
                    cfg.regime.bear_size_factor if bear else 1.0)
    out = pd.concat([df, flt, sig, sc, tp], axis=1)
    out["reasons"] = reasons(df, flt, sig, bear)
    return out
