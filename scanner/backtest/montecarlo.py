"""Monte Carlo of the default strategy: how much of the result is the luck of which signals got the cash?

With ~10% of equity per stock, cash runs out and hundreds of valid signals are skipped. The default
buys the best-scoring signals first; this re-runs the same strategy N times buying each week's
eligible signals in a random order (`shuffle_seed`), optionally also missing `skip_pct` % of them
(holidays, late orders). The spread of outcomes is the honest range to plan around.

Saved like the experiment log (`params.experiment = "monte_carlo"`): the score-ordered run plus the
runs nearest the 5th / 25th / 50th / 75th / 95th CAGR percentiles, each carrying the full summary.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

import db
from backtest.engine import Params, PricePanel, build_signals, simulate
from backtest.runner import save_result, simulate_many, survivorship_flag
from common import timed
from config import Config
from jobs.weekly_scan import load_scan_inputs

log = logging.getLogger(__name__)

PCTS = [5, 25, 50, 75, 95]


def run_row(res) -> dict:
    m = res.metrics
    yearly = m.get("yearly", {})
    return {"cagr": m["cagr_pct"], "max_dd": m["max_drawdown_pct"], "sharpe": m["sharpe"] or 0.0,
            "trades": m["trades"], "final": m["final_equity"],
            "worst_year": min(yearly.values()) if yearly else np.nan,
            "ew_cagr": res.benchmark_metrics.get("universe_ew", {}).get("cagr_pct", np.nan)}


def summarize(df: pd.DataFrame, base: dict) -> dict:
    out = {"runs": len(df), "score_order": {k: round(float(v), 2) for k, v in base.items()}}
    for col in ("cagr", "max_dd", "sharpe", "worst_year", "trades"):
        out[col] = {f"p{q}": round(float(np.percentile(df[col], q)), 2) for q in PCTS}
        out[col]["mean"] = round(float(df[col].mean()), 2)
    ew = float(df["ew_cagr"].iloc[0])
    out["universe_ew_cagr"] = round(ew, 2)
    out["beat_universe_ew_pct"] = round(float((df["cagr"] > ew).mean() * 100), 1)
    out["score_order_percentile"] = round(float((df["cagr"] < base["cagr"]).mean() * 100), 1)
    return out


def run_monte_carlo(cfg: Config, runs: int = 500, skip_pct: float = 0, jobs: int | None = None,
                    save: bool = True) -> dict:
    import os

    t0 = time.perf_counter()
    b = cfg.backtest
    jobs = jobs or max(1, (os.cpu_count() or 2) - 1)
    timings: dict = {}
    with timed(log, "load inputs", timings), db.connection() as conn:
        inputs = load_scan_inputs(conn, cfg)
    prices = PricePanel.from_inputs(inputs, cfg.regime.benchmark)
    st = (cfg.supertrend.atr_period, cfg.supertrend.multiplier)
    with timed(log, "signals", timings):
        sig = build_signals(inputs, cfg, st, "config", cfg.sectors.weights, b.capital, b.start, b.end)

    base_res = simulate(sig, prices, Params.from_cfg(cfg))
    params = [Params.from_cfg(cfg, shuffle_seed=seed, skip_pct=skip_pct) for seed in range(1, runs + 1)]
    with timed(log, f"simulate {runs} shuffled runs", timings):
        results = simulate_many(sig, prices, params, jobs)
    df = pd.DataFrame([run_row(r) for r in results])
    df["seed"] = range(1, runs + 1)
    summary = summarize(df, run_row(base_res))
    summary["skip_pct"] = skip_pct

    saved = []
    if save:
        key = "monte_carlo" if not skip_pct else f"monte_carlo_skip{skip_pct:g}"
        s = summary
        verdict = (f"{runs} runs, random signal order{f' + {skip_pct:g}% of signals missed' if skip_pct else ''}: "
                   f"CAGR p5 {s['cagr']['p5']}% / median {s['cagr']['p50']}% / p95 {s['cagr']['p95']}%; "
                   f"max DD median {s['max_dd']['p50']}% (p5 {s['max_dd']['p5']}%). Score order "
                   f"{s['score_order']['cagr']}% sits at the {s['score_order_percentile']:g}th percentile. "
                   f"{s['beat_universe_ew_pct']:g}% of runs beat the equal-weight universe ({s['universe_ew_cagr']}%).")
        info = {"title": "Monte Carlo: random signal order" + (f", {skip_pct:g}% missed" if skip_pct else ""),
                "date": pd.Timestamp.today().strftime("%Y-%m-%d"),
                "question": "How much of the result depends on which signals happened to get the cash?",
                "verdict": verdict, "adopted": False, "order": 100}
        meta = {"supertrend": list(st), "sector_weights": "config", "sector_weight_values": cfg.sectors.weights,
                "variants": {}, "universe": cfg.universe.universe_index, "experiment": key,
                "experiment_info": info, "monte_carlo": summary}
        picks = [("Score order (default)", base_res, {})]
        for q in PCTS:
            target = np.percentile(df["cagr"], q)
            i = int((df["cagr"] - target).abs().idxmin())
            picks.append((f"Random order, p{q} (seed {df.at[i, 'seed']})", results[i],
                          {"shuffle_seed": int(df.at[i, "seed"]), "skip_pct": skip_pct}))
        with db.connection() as conn:
            biased = survivorship_flag(conn, sig.weeks[0].week_end, cfg.universe.universe_index)
            conn.execute("DELETE FROM backtest_runs WHERE sweep_id IS NULL AND params->>'experiment' = %s", (key,))
            for label, res, over in picks:
                rid = save_result(conn, res, f"MC · {label}", {**meta, "variant": label, "overrides": over},
                                  None, biased)
                saved.append({"id": rid, "variant": label, "cagr": round(res.metrics["cagr_pct"], 2)})
        summary["survivorship_bias"] = biased
    summary["saved"] = saved
    summary["duration_ms"] = round((time.perf_counter() - t0) * 1000)
    summary["timings"] = timings
    return summary
