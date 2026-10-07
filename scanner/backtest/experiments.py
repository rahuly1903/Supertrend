"""Experiment log: every strategy test, re-run on the current data and saved as linkable runs.

Each experiment changes a few backtest parameters against the current defaults (config.yaml
`backtest`). Runs are stored like single backtests (sweep_id NULL) with `params.experiment` set, so
the web "Backtests" page can group them; `python main.py experiments` deletes and re-saves them all.
Numbers therefore follow the current universe and defaults, not the data each test first ran on.
"""
from __future__ import annotations

import logging
import time

import db
from backtest.engine import Params, PricePanel, build_signals, simulate
from backtest.runner import save_result, survivorship_flag
from common import timed
from config import Config
from jobs.weekly_scan import load_scan_inputs

log = logging.getLogger(__name__)

# (key, title, first tested, question, verdict, adopted, {variant label: parameter overrides})
EXPERIMENTS: list[dict] = [
    dict(key="entry_capital", title="Entry window and adding capital", date="2026-10-01",
         question="Buy up to week 3 of a trend and take every signal, adding new money when cash runs out?",
         verdict="Weeks 1–3 add some CAGR on today's data but deepen the drawdown in every sizing (about −27% vs "
                 "−23%). A fixed ₹1 L per stock doesn't compound; growing it needs large yearly top-ups. Kept: flip "
                 "week, 10% of equity, own cash only.",
         adopted=False, variants={
             "Weeks 1–3, ₹1 L each, add capital": dict(entry_max_weeks_in_trend=3, position_pct=0,
                                                         position_size=100_000, add_capital=True, min_fill_pct=0),
             "Flip week, ₹1 L each, add capital": dict(position_pct=0, position_size=100_000, add_capital=True,
                                                        min_fill_pct=0),
             "Weeks 1–3, ₹1 L or 2% of equity, add capital": dict(entry_max_weeks_in_trend=3, position_pct=2,
                                                                     position_size=100_000, add_capital=True),
             "Weeks 1–3, ₹1 L or 5% of equity, add capital": dict(entry_max_weeks_in_trend=3, position_pct=5,
                                                                     position_size=100_000, add_capital=True),
             "Weeks 1–3, 10 equal positions": dict(entry_max_weeks_in_trend=3, sizing="equal", max_positions=10),
             "Weeks 1–3, 10% of equity": dict(entry_max_weeks_in_trend=3),
         }),
    dict(key="positions", title="Number of positions", date="2026-10-02",
         question="Hold more, smaller positions (or fewer, bigger ones)?",
         verdict="10% of equity (about 8–10 stocks) ≈ K10 equal. Smaller positions depend less on the top winners "
                 "but cost several % CAGR; K5 is luck from concentration.",
         adopted=False, variants={
             "K10 equal": dict(sizing="equal", max_positions=10, max_position_pct=20),
             "K5 equal": dict(sizing="equal", max_positions=5, max_position_pct=20),
             "5% of equity, max 20": dict(position_pct=5, max_positions=20),
             "3% of equity, max 33": dict(position_pct=3, max_positions=33),
         }),
    dict(key="risk_filters", title="Bear regime, sector cap, price > SMA200", date="2026-10-01",
         question="Do market-regime, sector-concentration or 200-day filters reduce risk?",
         verdict="Each costs CAGR and barely moves the drawdown. 'Half' doesn't de-risk with %-of-equity sizing; "
                 "'skip' misses the flips right after sell-offs.",
         adopted=False, variants={
             "Bear regime: half size": dict(bear_mode="half"),
             "Bear regime: no new buys": dict(bear_mode="skip"),
             "Max 3 stocks per sector": dict(max_per_sector=3),
             "Price > SMA200 at entry": dict(above_sma200=True),
             "All three (half, cap 3, > SMA200)": dict(bear_mode="half", max_per_sector=3, above_sma200=True),
         }),
    dict(key="score_cutoff", title="Entry score cutoff", date="2026-10-02",
         question="Is a minimum score above 70 better?",
         verdict="Higher cutoffs win more often but leave cash idle; CAGR falls past 80. 70 kept.",
         adopted=False, variants={
             "Score ≥ 75": dict(min_score=75), "Score ≥ 77": dict(min_score=77), "Score ≥ 80": dict(min_score=80),
             "Score ≥ 82": dict(min_score=82), "Score ≥ 85": dict(min_score=85),
             "Score ≥ 80, 15% of equity": dict(min_score=80, position_pct=15, max_position_pct=100),
         }),
    dict(key="trim_swap", title="Trim winners, swap the weakest", date="2026-10-02",
         question="Free cash for more signals by trimming big winners or replacing weak holdings?",
         verdict="Trimming barely frees cash; the best swap setting is an isolated peak. Not adopted.",
         adopted=False, variants={
             "Trim above 15% back to 10%": dict(trim_above_pct=15, trim_to_pct=10),
             "Trim above 25% back to 10%": dict(trim_above_pct=25, trim_to_pct=10),
             "7% each, trim above 12% back to 7%": dict(position_pct=7, trim_above_pct=12, trim_to_pct=7),
             "Swap: new ≥ 80 replaces holding < 60": dict(swap_min_score=80, swap_below_score=60),
             "Swap: new ≥ 80 replaces holding < 65": dict(swap_min_score=80, swap_below_score=65),
         }),
    dict(key="score_exit", title="Exit when the score falls", date="2026-10-02",
         question="Also sell when the stock score drops below a level?",
         verdict="Score < 30 helped on both universes (small, consistent gain) and was adopted. 40 was a spike; "
                 "45+ exits too early.",
         adopted=True, variants={
             "No score exit (Supertrend only)": dict(exit_score_below=0),
             "Score < 20": dict(exit_score_below=20), "Score < 35": dict(exit_score_below=35),
             "Score < 40": dict(exit_score_below=40), "Score < 45": dict(exit_score_below=45),
             "Score < 50": dict(exit_score_below=50),
         }),
    dict(key="profit_booking", title="Book profits at +50% and +100%", date="2026-10-05",
         question="Sell 40% at +50%, then 40% of the rest at +100%, and hold the remainder to the normal exit?",
         verdict="Smoother and far less dependent on a few stocks (top-5 share 70% → 44%; without the top 10 "
                 "stocks 17.7% vs 12.2% CAGR), but CAGR 33.3% → 28.0% and final equity ₹2.49 Cr → ₹1.57 Cr: it "
                 "sells the multibaggers early. Equal in 2015–20, behind in the 2021–26 boom. Booking later "
                 "(+100% / +200%) keeps more of the return. Not adopted.",
         adopted=False, variants={
             "40% at +50%, 40% of rest at +100% (limit orders)": dict(take_profit=((50, 0.4), (100, 0.4))),
             "40% at +50%, 40% of rest at +100% (weekly close)": dict(take_profit=((50, 0.4), (100, 0.4)),
                                                                       tp_fill="close"),
             "Same, remainder exits only when bearish": dict(take_profit=((50, 0.4), (100, 0.4)),
                                                             exit_score_below=0),
             "No booking, exit only when bearish": dict(exit_score_below=0),
             "40% at +30%, 40% of rest at +60%": dict(take_profit=((30, 0.4), (60, 0.4))),
             "40% at +100%, 40% of rest at +200%": dict(take_profit=((100, 0.4), (200, 0.4))),
         }),
    dict(key="ma_entry", title="Moving-average rule at entry", date="2026-10-05",
         question="Require the trend template (price > SMA50 > SMA150 > SMA200, SMA200 rising) or part of it?",
         verdict="Better trades one by one, but it rejects the early flips that made most of the profit: CAGR "
                 "halves with the full template. Not adopted.",
         adopted=False, variants={
             "Full trend template": dict(entry_ma="template"),
             "MA stack, not 'rising'": dict(entry_ma="stack"),
             "SMA200 rising only": dict(entry_ma="rising200"),
             "Price > SMA200": dict(above_sma200=True),
         }),
]


def run_experiments(cfg: Config, only: list[str] | None = None) -> dict:
    t0 = time.perf_counter()
    b = cfg.backtest
    timings: dict = {}
    with timed(log, "load inputs", timings), db.connection() as conn:
        inputs = load_scan_inputs(conn, cfg)
    prices = PricePanel.from_inputs(inputs, cfg.regime.benchmark)
    st = (cfg.supertrend.atr_period, cfg.supertrend.multiplier)
    with timed(log, "signals", timings):
        sig = build_signals(inputs, cfg, st, "config", cfg.sectors.weights, b.capital, b.start, b.end)
    exps = [e for e in EXPERIMENTS if not only or e["key"] in only]
    base = {"supertrend": list(st), "sector_weights": "config", "sector_weight_values": cfg.sectors.weights,
            "variants": {}, "universe": cfg.universe.universe_index}
    out = []
    with db.connection() as conn:
        biased = survivorship_flag(conn, sig.weeks[0].week_end, cfg.universe.universe_index)
        keys = [e["key"] for e in exps] + ([] if only else ["baseline"])
        n = conn.execute("DELETE FROM backtest_runs WHERE sweep_id IS NULL AND params->>'experiment' = ANY(%s)",
                         (keys,)).rowcount
        log.info("replaced %d stored experiment runs", n)
        order = {e["key"]: i for i, e in enumerate(EXPERIMENTS)}
        info = {e["key"]: {**{k: e[k] for k in ("title", "date", "question", "verdict", "adopted")},
                           "order": order[e["key"]]} for e in exps}
        info["baseline"] = {"title": "Current default (Rule 1)", "date": None, "question": None, "verdict": None,
                            "adopted": True}
        jobs = [] if only else [("baseline", "Current default (Rule 1)", {})]
        jobs += [(e["key"], label, over) for e in exps for label, over in e["variants"].items()]
        with timed(log, f"simulate + save {len(jobs)} runs", timings):
            for key, label, over in jobs:
                res = simulate(sig, prices, Params.from_cfg(cfg, **over))
                meta = {**base, "experiment": key, "variant": label, "overrides": over,
                        "experiment_info": info[key]}
                rid = save_result(conn, res, f"Exp · {label}", meta, None, biased)
                m = res.metrics
                out.append({"id": rid, "experiment": key, "variant": label, "cagr": round(m["cagr_pct"], 1),
                            "max_dd": round(m["max_drawdown_pct"], 1), "trades": m["trades"]})
    return {"runs": len(out), "results": out, "duration_ms": round((time.perf_counter() - t0) * 1000),
            "timings": timings}
