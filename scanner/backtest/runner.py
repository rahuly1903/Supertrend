"""Backtest orchestration: load inputs once, build signal sets, simulate, persist."""
from __future__ import annotations

import itertools
import json
import logging
import multiprocessing
import os
import time

import numpy as np
import pandas as pd

import db
from backtest.engine import Params, PricePanel, Result, build_signals, curve_metrics, simulate
from backtest.walkforward import RunRecord, slice_metrics, walk_forward
from common import timed
from config import Config
from engine.weekly import week_monday
from jobs.weekly_scan import load_scan_inputs

log = logging.getLogger(__name__)


def survivorship_flag(conn, start: pd.Timestamp, universe: str = "Nifty 500") -> bool:
    """True unless point-in-time membership of the universe index covers the whole test window."""
    first = conn.execute("""SELECT min(s.snapshot_date) FROM index_member_snapshots s
                            JOIN indices i ON i.id = s.index_id WHERE i.name = %s""", (universe,)).fetchone()[0]
    return first is None or pd.Timestamp(first) > start


def save_result(conn, res: Result, name: str, extra_params: dict, sweep_id: int | None, biased: bool) -> int:
    m, eq = res.metrics, res.equity
    params = {**res.params, **extra_params}
    run_id = conn.execute("""
        INSERT INTO backtest_runs (sweep_id, name, params, start_date, end_date, survivorship_bias, metrics,
                                   benchmark_metrics, cagr, max_drawdown, sharpe, profit_factor, win_rate, trades)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""", (
        sweep_id, name, json.dumps(params, default=str), eq["date"].iloc[0].date(), eq["date"].iloc[-1].date(),
        biased, json.dumps(m, default=str), json.dumps(res.benchmark_metrics, default=str),
        m["cagr_pct"], m["max_drawdown_pct"], m["sharpe"], m["profit_factor"], m["win_rate_pct"], m["trades"],
    )).fetchone()[0]
    if "nav" not in eq:
        eq = eq.assign(nav=eq["equity"])
    if "contributed" not in eq:
        eq = eq.assign(contributed=np.nan)
    db.copy_frame(conn, "backtest_equity", eq.assign(backtest_id=run_id)[
        ["backtest_id", "date", "equity", "benchmark", "invested_pct", "drawdown", "positions", "nav",
         "contributed"]])
    if not res.trades.empty:
        db.copy_frame(conn, "backtest_trades", res.trades.assign(backtest_id=run_id))
    return run_id


def universe_slice(prices: PricePanel, start, end) -> dict:
    ew = prices.universe_ew()
    mondays = pd.DatetimeIndex(week_monday([start, end]))
    seg = ew[(ew.index >= mondays[0]) & (ew.index <= mondays[1])]
    return curve_metrics(seg, pd.Series(seg.index)) if len(seg) > 1 else {}


SHORT = {"top_n_sectors": "top", "min_rs": "RS", "min_score": "S", "max_positions": "K"}


def sim_combos(b) -> list[tuple[str, dict]]:
    """(label suffix, Params overrides) for every grid x variants combination."""
    fields = set(Params.__dataclass_fields__)
    grid = b.sweep.grid
    for k in list(grid) + [k for axis in b.sweep.variants.values() for v in axis.values() for k in v]:
        if k not in fields:
            raise SystemExit(f"backtest.sweep: unknown parameter {k!r}")
    out = []
    axes = list(b.sweep.variants.items())
    for values in itertools.product(*grid.values()):
        g = dict(zip(grid, values))
        for picks in itertools.product(*[list(opts.items()) for _, opts in axes]):
            over, names = dict(g), []
            for (axis, _), (vname, vover) in zip(axes, picks):
                over.update(vover)
                over[f"variant_{axis}"] = vname
                names.append(vname)
            label = " ".join(f"{SHORT.get(k, k + '=')}{v:g}" if isinstance(v, (int, float)) else f"{k}={v}"
                             for k, v in g.items())
            out.append((" ".join([label, "/".join(names)]).strip(), over))
    return out


_SIG = _PRICES = None


def _simulate(p: Params) -> Result:
    return simulate(_SIG, _PRICES, p)


def simulate_many(sig, prices, params: list[Params], jobs: int) -> list[Result]:
    global _SIG, _PRICES
    _SIG, _PRICES = sig, prices
    if jobs <= 1 or len(params) < 8:
        return [_simulate(p) for p in params]
    ctx = multiprocessing.get_context("fork")   # workers inherit the signals without pickling them
    with ctx.Pool(jobs) as pool:
        return pool.map(_simulate, params, chunksize=max(1, len(params) // (jobs * 4)))


def run_backtest(cfg: Config, sweep: bool = False, name: str | None = None, jobs: int | None = None) -> dict:
    t0 = time.perf_counter()
    b = cfg.backtest
    wf = b.walk_forward
    jobs = jobs or max(1, (os.cpu_count() or 2) - 1)
    timings: dict = {}
    with timed(log, "load inputs", timings), db.connection() as conn:
        inputs = load_scan_inputs(conn, cfg)
    prices = PricePanel.from_inputs(inputs, cfg.regime.benchmark)

    if sweep:
        st_grid = b.sweep.supertrend
        weights_grid = b.sweep.sector_weights
        combos = sim_combos(b)
    else:
        st_grid = [(cfg.supertrend.atr_period, cfg.supertrend.multiplier)]
        weights_grid = {"config": cfg.sectors.weights}
        combos = [("", {})]
    log.info("%d signal sets x %d parameter sets", len(st_grid) * len(weights_grid), len(combos))

    sweep_id = None
    if sweep:
        with db.connection() as conn:
            grid = {**b.sweep.model_dump(), "walk_forward": wf.model_dump(), "combos": len(combos)}
            sweep_id = conn.execute("INSERT INTO backtest_sweeps (name, grid) VALUES (%s, %s) RETURNING id",
                                    (name or "sweep", json.dumps(grid))).fetchone()[0]

    rows, records, results, biased = [], [], [], None
    for st_params, (wname, weights) in itertools.product(st_grid, weights_grid.items()):
        with timed(log, f"signals ST{tuple(st_params)} {wname}", timings):
            sig = build_signals(inputs, cfg, tuple(st_params), wname, weights, b.capital, b.start, b.end)
        if len(sig.weeks) < 10:
            raise SystemExit(f"only {len(sig.weeks)} weeks after warm-up; need more history")
        params = [Params.from_cfg(cfg, **{k: v for k, v in over.items() if not k.startswith("variant_")})
                  for _, over in combos]
        with timed(log, f"simulate {len(params)} ST{tuple(st_params)} {wname}", timings):
            sims = simulate_many(sig, prices, params, jobs)
        extra = {"supertrend": list(st_params), "sector_weights": wname, "sector_weight_values": weights}
        for (suffix, over), res in zip(combos, sims):
            label = name if (name and not sweep) else f"{sig.key} {suffix}".strip()
            variants = {k[8:]: v for k, v in over.items() if k.startswith("variant_")}
            results.append((label, {**extra, "variants": variants}, res))
        if biased is None:
            with db.connection() as conn:
                biased = survivorship_flag(conn, sig.weeks[0].week_end, cfg.universe.universe_index)

    wf_res = None
    if sweep and wf.enabled:
        with timed(log, "walk-forward", timings):
            recs = [RunRecord(i, lbl, r.equity, r.trades) for i, (lbl, _, r) in enumerate(results)]
            wf_res = walk_forward(recs, b.capital, wf.train_years, wf.test_years, wf.anchored, wf.metric,
                                  wf.top_k, wf.min_trades_per_year)
        # every run also reports its own metrics over the same out-of-sample span
        for _, _, r in results:
            r.metrics["oos"] = slice_metrics(r.equity, wf_res["oos_start"], wf_res["oos_end"])

    with timed(log, f"save {len(results)} runs", timings), db.connection() as conn:
        ids = []
        for label, extra, res in results:
            run_id = save_result(conn, res, label, extra, sweep_id, biased)
            ids.append(run_id)
            m = res.metrics
            rows.append({"id": run_id, "name": label, "cagr": round(m["cagr_pct"], 2),
                         "max_dd": round(m["max_drawdown_pct"], 2), "trades": m["trades"],
                         "win_rate": round(m["win_rate_pct"] or 0, 1),
                         "pf": round(m["profit_factor"], 2) if m["profit_factor"] else None,
                         "oos_cagr": round(m["oos"]["cagr_pct"], 2) if m.get("oos") else None})
        wf_summary = None
        if wf_res:
            for f in wf_res["folds"]:
                for c in f["chosen"]:
                    c["id"] = ids[c["id"]]
            wf_id = save_wf(conn, wf_res, wf, prices, sweep_id, biased)
            wm = wf_res["metrics"]
            wf_summary = {"id": wf_id, "oos_start": str(wf_res["oos_start"].date()), "cagr": round(wm["cagr_pct"], 2),
                          "max_dd": round(wm["max_drawdown_pct"], 2),
                          "benchmark_cagr": round(wf_res["benchmark_metrics"]["cagr_pct"], 2),
                          "folds": [{k: f[k] for k in ("test_start", "return_pct", "benchmark_return_pct")}
                                    | {"chosen": [c["name"] for c in f["chosen"]]} for f in wf_res["folds"]]}
    duration = round((time.perf_counter() - t0) * 1000)
    if sweep_id:
        with db.connection() as conn:
            conn.execute("UPDATE backtest_sweeps SET duration_ms = %s WHERE id = %s", (duration, sweep_id))
    best = sorted(rows, key=lambda r: r["cagr"], reverse=True)
    res = results[-1][2]
    return {"sweep_id": sweep_id, "runs": len(rows), "survivorship_bias": biased,
            "benchmark": res.benchmark_metrics, "top": best[:10], "walk_forward": wf_summary,
            "duration_ms": duration, "timings": timings}


def load_sweep_runs(conn, sweep_id: int) -> list[RunRecord]:
    runs = db.read_frame(conn, """SELECT id, name FROM backtest_runs
                                  WHERE sweep_id = %s AND params->>'kind' IS NULL ORDER BY id""", [sweep_id])
    if runs.empty:
        raise SystemExit(f"sweep {sweep_id} has no runs")
    eq = db.read_frame_copy(conn, f"""
        SELECT e.backtest_id, e.date, e.equity, e.benchmark, e.invested_pct, e.positions,
               coalesce(e.nav, e.equity) AS nav, e.contributed
        FROM backtest_equity e JOIN backtest_runs r ON r.id = e.backtest_id
        WHERE r.sweep_id = {int(sweep_id)} AND r.params->>'kind' IS NULL ORDER BY e.backtest_id, e.date""",
                            parse_dates=["date"])
    tr = db.read_frame_copy(conn, f"""
        SELECT t.backtest_id, t.symbol_id, t.symbol, t.industry, t.signal_date, t.entry_date, t.entry_price,
               t.exit_date, t.exit_price, t.qty, t.pnl, t.pnl_pct, t.weeks_held, t.exit_reason, t.entry_score
        FROM backtest_trades t JOIN backtest_runs r ON r.id = t.backtest_id
        WHERE r.sweep_id = {int(sweep_id)} AND r.params->>'kind' IS NULL""",
                            parse_dates=["signal_date", "entry_date", "exit_date"])
    eq["flow"] = eq.groupby("backtest_id")["contributed"].diff().fillna(0)
    eqg = {k: g.drop(columns=["backtest_id", "contributed"]).reset_index(drop=True)
           for k, g in eq.groupby("backtest_id")}
    trg = {k: g.drop(columns="backtest_id") for k, g in tr.groupby("backtest_id")}
    empty = tr.drop(columns="backtest_id").iloc[:0]
    return [RunRecord(int(i), n, eqg[i], trg.get(i, empty)) for i, n in zip(runs["id"], runs["name"])]


def run_walk_forward(cfg: Config, sweep_id: int) -> dict:
    """Recompute the walk-forward result of a stored sweep (e.g. after changing backtest.walk_forward)."""
    wf, b = cfg.backtest.walk_forward, cfg.backtest
    timings: dict = {}
    with timed(log, "load sweep", timings), db.connection() as conn:
        recs = load_sweep_runs(conn, sweep_id)
        biased = conn.execute("SELECT bool_or(survivorship_bias) FROM backtest_runs WHERE sweep_id = %s",
                              (sweep_id,)).fetchone()[0]
        inputs = load_scan_inputs(conn, cfg)
    prices = PricePanel.from_inputs(inputs, cfg.regime.benchmark)
    with timed(log, "walk-forward", timings):
        res = walk_forward(recs, b.capital, wf.train_years, wf.test_years, wf.anchored, wf.metric, wf.top_k,
                           wf.min_trades_per_year)
    oos = {r.id: slice_metrics(r.equity, res["oos_start"], res["oos_end"]) for r in recs}
    with timed(log, "save", timings), db.connection() as conn:
        conn.execute("DELETE FROM backtest_runs WHERE sweep_id = %s AND params->>'kind' = 'walk_forward'",
                     (sweep_id,))
        for rid, m in oos.items():
            conn.execute("UPDATE backtest_runs SET metrics = jsonb_set(metrics, '{oos}', %s::jsonb) WHERE id = %s",
                         (json.dumps(m, default=str), rid))
        wf_id = save_wf(conn, res, wf, prices, sweep_id, biased)
    m = res["metrics"]
    return {"sweep_id": sweep_id, "id": wf_id, "oos_start": str(res["oos_start"].date()),
            "cagr": round(m["cagr_pct"], 2), "max_dd": round(m["max_drawdown_pct"], 2),
            "benchmark_cagr": round(res["benchmark_metrics"]["cagr_pct"], 2), "timings": timings}


def save_wf(conn, res: dict, wf, prices: PricePanel, sweep_id: int, biased: bool) -> int:
    run = Result(res["equity"], res["trades"], res["metrics"],
                 {**res["benchmark_metrics"], "universe_ew": universe_slice(prices, res["oos_start"], res["oos_end"])},
                 {"kind": "walk_forward", "walk_forward": wf.model_dump(), "folds": res["folds"]})
    label = (f"Walk-forward OOS: top{wf.top_k} by {wf.metric.upper()}, "
             f"{wf.train_years:g}y {'anchored' if wf.anchored else 'rolling'} train / {wf.test_years:g}y test")
    return save_result(conn, run, label, {}, sweep_id, biased)
