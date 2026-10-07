// Server-side data access. Pages and API routes read precomputed snapshot tables only;
// the stock charts read one symbol's candles by primary key.
import { memo, runKey } from "./cache";
import { SCANNER_FIELDS } from "./columns";
import { RULE1 } from "./filters";
import { prisma } from "./prisma";
import { plain } from "./serialize";

const select = (fields) => Object.fromEntries(fields.map((f) => [f, true]));
const toDate = (s) => new Date(`${s}T00:00:00Z`);

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------
export async function getRuns({ completeOnly = false } = {}) {
  const runs = await prisma.scanRun.findMany({
    where: completeOnly ? { status: "complete" } : undefined,
    orderBy: { week_end_date: "desc" },
    select: {
      id: true, week_end_date: true, status: true, started_at: true, finished_at: true,
      duration_ms: true, stocks_scanned: true, qualified_count: true, notes: true, error: true, timings: true,
    },
  });
  return runs.map((r) => ({ ...plain(r), finishedAt: plain(r.finished_at) }));
}

/** The complete run for ?week=YYYY-MM-DD (or ?run=id); defaults to the latest complete run. */
export async function resolveRun(sp = {}) {
  const where = { status: "complete" };
  if (sp.run && /^\d+$/.test(sp.run)) where.id = Number(sp.run);
  else if (sp.week && /^\d{4}-\d{2}-\d{2}$/.test(sp.week)) where.week_end_date = { lte: toDate(sp.week) };
  const r = await prisma.scanRun.findFirst({
    where, orderBy: { week_end_date: "desc" },
    select: { id: true, week_end_date: true, finished_at: true, stocks_scanned: true, qualified_count: true, notes: true, config: true },
  });
  if (!r) return null;
  const { config, ...rest } = r;
  const st = config?.supertrend;     // the parameters this scan actually used
  return { ...plain(rest), finishedAt: plain(r.finished_at), week: plain(r.week_end_date),
    supertrend: st ? `${st.atr_period}, ${st.multiplier}` : null };
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
/**
 * Rule 1 signals as of `run`, rebuilt from the stored weekly snapshots:
 *   buys   first entry signal of a bullish run this week (week 1-3 of the run, score >= 70,
 *          tradeable) -> buy at next week's open
 *   open   earlier entry signals not exited yet
 *   exits  entry signals whose Supertrend turned bearish or whose score fell below
 *          RULE1.exitScore this week (first time since the signal) -> sell at next week's open
 * A run can qualify in several of its first weeks; only the first one is the signal. History
 * starts at the first stored snapshot.
 */
export function getRule1(run) {
  return memo(runKey("rule1", run), async () => {
    const runs = await prisma.scanRun.findMany({
      where: { status: "complete", week_end_date: { lte: toDate(run.week) } },
      orderBy: { week_end_date: "asc" }, select: { id: true, week_end_date: true },
    });
    const cur = runs.length - 1;
    const pos = new Map(runs.map((r, i) => [r.id, i]));
    const signals = await prisma.stockScanResult.findMany({
      where: {
        run_id: { in: runs.map((r) => r.id) }, direction: "Bullish", weeks_in_trend: { lte: RULE1.maxWeeks },
        stock_score: { gte: RULE1.minScore }, f_liquidity: true, f_price: true, f_not_asm: true,
      },
      select: { run_id: true, symbol_id: true, symbol: true, industry: true, price: true, stock_score: true, weeks_in_trend: true },
    });
    const latest = new Map();          // symbol -> first entry signal of its most recent bullish run
    for (const e of signals) {
      const i = pos.get(e.run_id);
      const start = i - e.weeks_in_trend + 1;      // index of the run's flip week
      const cur_ = latest.get(e.symbol_id);
      if (!cur_ || start > cur_.start || (start === cur_.start && i < cur_.i)) latest.set(e.symbol_id, { ...e, i, start });
    }
    const ids = [...latest.keys()];
    const first = Math.min(cur, ...[...latest.values()].map((e) => e.i));
    // every week since the oldest signal: the first bearish week or score below the exit level ends it
    const hist = ids.length ? await prisma.stockScanResult.findMany({
      where: { run_id: { in: runs.slice(first).map((r) => r.id) }, symbol_id: { in: ids } },
      select: { run_id: true, symbol_id: true, direction: true, price: true, st_value: true, stock_score: true, grade: true },
    }) : [];
    const at = new Map(hist.map((h) => [`${h.symbol_id}:${pos.get(h.run_id)}`, h]));
    const exitWeek = (sid, from) => {
      for (let k = from + 1; k <= cur; k++) {
        const h = at.get(`${sid}:${k}`);
        if (!h) return { k, reason: "stale" };
        if (h.direction !== "Bullish") return { k, reason: "supertrend" };
        if (RULE1.exitScore && h.stock_score < RULE1.exitScore) return { k, reason: "score" };
      }
      return null;
    };

    const buys = [], open = [], exits = [];
    for (const [sid, e] of latest) {
      const r = at.get(`${sid}:${cur}`);
      if (!r) continue;
      const row = {
        symbol: e.symbol, industry: e.industry, signal_week: runs[e.i].week_end_date, signal_price: e.price,
        entry_score: e.stock_score, price: r.price, score: r.stock_score, grade: r.grade, stop: r.st_value,
        weeks: cur - e.i, change_pct: (r.price / e.price - 1) * 100,
        stop_dist_pct: r.st_value ? (r.price / r.st_value - 1) * 100 : null,
      };
      if (e.i === cur) { buys.push(row); continue; }
      const x = exitWeek(sid, e.i);
      if (!x) open.push(row);
      else if (x.k === cur) exits.push({ ...row, exit_reason: x.reason });
    }
    buys.sort((a, b) => b.score - a.score);
    open.sort((a, b) => b.change_pct - a.change_pct);
    exits.sort((a, b) => b.change_pct - a.change_pct);
    return plain({ buys, open, exits, since: runs[0]?.week_end_date ?? null, rule: RULE1,
      portfolio: await getRule1Portfolio(run) });
  });
}

const isRule1Params = (p) => p.entry_max_weeks_in_trend === RULE1.maxWeeks && p.min_score === RULE1.minScore
  && p.top_n_sectors === 0 && p.sector_exit === false && p.entry_filters === "tradeable";

/**
 * The model portfolio: positions still open at the end of the latest Rule 1 backtest (at most K;
 * K = 0 means no limit), shown only when that backtest ends on this run's week. A holding that turned
 * bearish this week is sold at the next open; free slots take this week's buys, best score first.
 */
async function getRule1Portfolio(run) {
  const recent = await prisma.backtestRun.findMany({
    where: { sweep_id: null }, orderBy: { id: "desc" }, take: 150,
    select: { id: true, name: true, params: true, end_date: true },
  });
  const bt = recent.find((r) => !r.params.experiment && isRule1Params(r.params));
  if (!bt) return null;
  const endWeek = plain(bt.end_date);
  if (endWeek !== run.week) return { id: bt.id, endWeek, stale: true };
  const trades = await prisma.backtestTrade.findMany({
    where: { backtest_id: bt.id, exit_reason: "end" }, orderBy: { entry_date: "asc" },
    select: { symbol_id: true, symbol: true, industry: true, entry_date: true, entry_price: true, qty: true, entry_score: true },
  });
  const rows = await prisma.stockScanResult.findMany({
    where: { run_id: run.id, symbol_id: { in: trades.map((t) => t.symbol_id) } },
    select: { symbol_id: true, direction: true, price: true, st_value: true, stock_score: true },
  });
  const by = new Map(rows.map((r) => [r.symbol_id, r]));
  const holdings = trades.map((t) => {
    const r = by.get(t.symbol_id) ?? {};
    return {
      symbol: t.symbol, industry: t.industry, entry_week: t.entry_date, entry_price: t.entry_price, qty: t.qty,
      score: r.stock_score ?? null,
      price: r.price ?? null, stop: r.st_value ?? null,
      sell: r.direction !== "Bullish" || (!!bt.params.exit_score_below && r.stock_score < bt.params.exit_score_below),
      change_pct: r.price ? (r.price / t.entry_price - 1) * 100 : null,
      stop_dist_pct: r.price && r.st_value ? (r.price / r.st_value - 1) * 100 : null,
    };
  });
  const p = bt.params;
  const k = p.max_positions > 0 ? p.max_positions : null;      // null = no limit
  return plain({ id: bt.id, endWeek, stale: false, k, holdings,
    positionSize: p.sizing === "fixed" && p.position_size > 0 ? p.position_size : null,
    positionPct: p.sizing === "fixed" && p.position_pct > 0 ? p.position_pct : null, addCapital: !!p.add_capital,
    freeSlots: k == null ? null : k - holdings.filter((h) => !h.sell).length });
}
export function getDashboard(run) {
  return memo(runKey("dashboard", run), async () => {
    const [regime, sectors, top, counts] = await Promise.all([
      prisma.marketRegime.findUnique({ where: { run_id: run.id } }),
      prisma.sectorScanResult.findMany({ where: { run_id: run.id }, orderBy: { sector_rank: "asc" }, take: 5 }),
      prisma.stockScanResult.findMany({
        where: { run_id: run.id, direction: "Bullish", grade: { in: ["A+", "A"] } },
        orderBy: [{ qualified: "desc" }, { stock_score: "desc" }], take: 10,
        select: select(["symbol", "name", "industry", "price", "stock_score", "grade", "qualified",
          "weeks_in_trend", "rs_rating", "pct_from_st", "pct_from_high", "reasons", "sector_rank"]),
      }),
      prisma.stockScanResult.groupBy({
        by: ["direction", "is_new_flip"], where: { run_id: run.id }, _count: { _all: true },
      }),
    ]);
    const c = (dir, flip) => counts.filter((x) => x.direction === dir && (flip === undefined || x.is_new_flip === flip))
      .reduce((s, x) => s + x._count._all, 0);
    return plain({
      regime, sectors, top,
      counts: {
        bullish: c("Bullish"), bearish: c("Bearish"),
        newBullish: c("Bullish", true), newBearish: c("Bearish", true),
      },
    });
  });
}

// ---------------------------------------------------------------------------
// Sectors
// ---------------------------------------------------------------------------
export function getSectors(run, { tailWeeks = 4, historyWeeks = 26 } = {}) {
  return memo(runKey("sectors", run, tailWeeks, historyWeeks), async () => {
    const week = toDate(run.week);
    const [rows, tailRows, historyRuns] = await Promise.all([
      prisma.sectorScanResult.findMany({ where: { run_id: run.id }, orderBy: { sector_rank: "asc" } }),
      prisma.sectorIndexWeekly.findMany({
        where: { week_end_date: { lte: week, gt: new Date(week.getTime() - (tailWeeks + 1) * 7 * 864e5) } },
        orderBy: { week_end_date: "desc" },
        select: { industry: true, week_end_date: true, rs_ratio: true, rs_momentum: true },
      }),
      prisma.scanRun.findMany({
        where: { status: "complete", week_end_date: { lte: week } }, orderBy: { week_end_date: "desc" },
        take: historyWeeks, select: { id: true, week_end_date: true },
      }),
    ]);
    const history = await prisma.sectorScanResult.findMany({
      where: { run_id: { in: historyRuns.map((r) => r.id) } },
      select: { industry: true, week_end_date: true, sector_score: true, sector_rank: true },
      orderBy: { week_end_date: "asc" },
    });
    // RRG tails: last (tailWeeks + 1) points per sector, oldest first
    const tails = {};
    for (const r of plain(tailRows)) {
      if (r.rs_ratio == null || r.rs_momentum == null) continue;
      (tails[r.industry] ??= []).push(r);
    }
    for (const k of Object.keys(tails)) tails[k] = tails[k].slice(0, tailWeeks + 1).reverse();
    return plain({ rows, tails, history });
  });
}

export function getSectorDetail(run, industry, { weeks = 156 } = {}) {
  return memo(runKey("sector", run, industry, weeks), async () => {
    const week = toDate(run.week);
    const [row, stocks, series] = await Promise.all([
      prisma.sectorScanResult.findUnique({ where: { run_id_industry: { run_id: run.id, industry } } }),
      prisma.stockScanResult.findMany({ where: { run_id: run.id, industry }, select: select(SCANNER_FIELDS) }),
      prisma.sectorIndexWeekly.findMany({
        where: { industry, week_end_date: { lte: week } }, orderBy: { week_end_date: "desc" }, take: weeks,
        select: { week_end_date: true, index_value: true, bench_value: true, st_breadth: true },
      }),
    ]);
    if (!row) return null;
    return plain({ row, stocks, series: series.reverse() });
  });
}

// ---------------------------------------------------------------------------
// Scanner
// ---------------------------------------------------------------------------
export function getScannerRows(run) {
  return memo(runKey("scanner", run), async () =>
    plain(await prisma.stockScanResult.findMany({ where: { run_id: run.id }, select: select(SCANNER_FIELDS) })),
  );
}

export async function getFilterOptions(run) {
  return memo(runKey("filteropts", run), async () => {
    const [sectors, indices] = await Promise.all([
      prisma.sectorScanResult.findMany({ where: { run_id: run.id }, select: { industry: true }, orderBy: { industry: "asc" } }),
      prisma.index.findMany({ select: { name: true, category: true }, orderBy: [{ category: "asc" }, { id: "asc" }] }),
    ]);
    return { sectors: sectors.map((s) => s.industry), indices: indices.map((i) => i.name).filter((n) => n !== "Nifty Total Market") };
  });
}

// ---------------------------------------------------------------------------
// Stock detail
// ---------------------------------------------------------------------------
function sma(values, n) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

export async function getStock(symbol, run, { dailyDays = 520 } = {}) {
  const sym = await prisma.symbol.findUnique({
    where: { symbol },
    select: {
      id: true, symbol: true, name: true, industry: true, isin: true, listed_date: true, is_active: true,
      index_members: { select: { index: { select: { name: true, category: true } } } },
    },
  });
  if (!sym) return null;
  const until = run ? toDate(run.week) : undefined;
  const dateFilter = until ? { lte: until } : undefined;

  const [scan, runCfg, weekly, st, daily, history] = await Promise.all([
    run ? prisma.stockScanResult.findUnique({ where: { run_id_symbol_id: { run_id: run.id, symbol_id: sym.id } } }) : null,
    run ? prisma.scanRun.findUnique({ where: { id: run.id }, select: { config: true } }) : null,
    prisma.weeklyCandle.findMany({
      where: { symbol_id: sym.id, week_end_date: dateFilter }, orderBy: { week_end_date: "asc" },
      select: { week_end_date: true, open: true, high: true, low: true, close: true, volume: true },
    }),
    prisma.weeklySupertrend.findMany({
      where: { symbol_id: sym.id, week_end_date: dateFilter }, orderBy: { week_end_date: "asc" },
      select: { week_end_date: true, st_value: true, direction: true },
    }),
    prisma.dailyCandle.findMany({
      where: { symbol_id: sym.id, date: dateFilter }, orderBy: { date: "asc" },
      select: { date: true, open: true, high: true, low: true, close: true, volume: true, delivery_pct: true },
    }),
    prisma.stockScanResult.findMany({
      where: { symbol_id: sym.id, run: { status: "complete" }, ...(until ? { week_end_date: { lte: until } } : {}) },
      orderBy: { week_end_date: "desc" }, take: 156,   // extra weeks so Rule 1 state is known at the window start
      select: { week_end_date: true, direction: true, grade: true, stock_score: true, qualified: true,
        sector_rank: true, price: true, weeks_in_trend: true, f_liquidity: true, f_price: true, f_not_asm: true },
    }),
  ]);

  const closes = daily.map((d) => d.close);
  const smas = Object.fromEntries([50, 100, 150, 200].map((n) => [n, sma(closes, n)]));
  const start = Math.max(0, daily.length - dailyDays);
  const dailyOut = daily.slice(start).map((d, i) => ({
    ...plain(d),
    sma50: smas[50][start + i], sma100: smas[100][start + i], sma150: smas[150][start + i], sma200: smas[200][start + i],
  }));

  const catOrder = { broad: 0, sectoral: 1, thematic: 2 };
  const indices = sym.index_members.map((m) => m.index)
    .sort((a, b) => catOrder[a.category] - catOrder[b.category] || a.name.localeCompare(b.name));

  const cfg = runCfg?.config ?? {};
  return plain({
    symbol: { ...sym, index_members: undefined }, indices, scan,
    config: { scoring: cfg.scoring, filters: cfg.filters, penalties: cfg.penalties, signals: cfg.signals },
    weekly, supertrend: st, daily: dailyOut, history: rule1States(history).slice(0, 52),
  });
}

/**
 * Rule 1 signal state per stored week (newest first, like `rows`): "buy" in an entry week (bullish
 * flip within RULE1.maxWeeks, score >= minScore, tradeable), "hold" while that signal lasts, "sell"
 * in the week the Supertrend turns bearish or the score falls below exitScore. Signals only: the
 * backtest skips buys when its cash is used up.
 */
function rule1States(rows) {
  let holding = false;
  const out = [...rows].reverse().map((h) => {
    let rule1 = null;
    if (holding) {
      const exit = h.direction !== "Bullish" || (RULE1.exitScore && h.stock_score < RULE1.exitScore);
      rule1 = exit ? "sell" : "hold";
      holding = !exit;
    } else if (h.direction === "Bullish" && h.weeks_in_trend <= RULE1.maxWeeks && h.stock_score >= RULE1.minScore
      && h.f_liquidity && h.f_price && h.f_not_asm) {
      rule1 = "buy";
      holding = true;
    }
    return { ...h, rule1 };
  });
  return out.reverse();
}

// ---------------------------------------------------------------------------
// Run diff
// ---------------------------------------------------------------------------
const FILTER_LABELS = {
  f_trend_template: "trend template", f_52w_range: "52W range", f_rs: "RS rating",
  f_liquidity: "liquidity", f_price: "price", f_not_asm: "ASM/GSM",
};
export function getRunDiff(run) {
  return memo(runKey("diff", run), async () => {
    const prev = await prisma.scanRun.findFirst({
      where: { status: "complete", week_end_date: { lt: toDate(run.week) } },
      orderBy: { week_end_date: "desc" }, select: { id: true, week_end_date: true },
    });
    const fields = select(["symbol_id", "symbol", "industry", "direction", "qualified", "grade", "stock_score",
      "sector_tradeable", "is_new_flip", "weeks_in_trend", "reasons", "price", ...Object.keys(FILTER_LABELS)]);
    const [cur, old] = await Promise.all([
      prisma.stockScanResult.findMany({ where: { run_id: run.id }, select: fields }),
      prev ? prisma.stockScanResult.findMany({ where: { run_id: prev.id }, select: fields }) : [],
    ]);
    const before = new Map(old.map((r) => [r.symbol_id, r]));
    const entries = [], exits = [];
    for (const r of cur) {
      const p = before.get(r.symbol_id);
      if (r.qualified && !p?.qualified) entries.push(r);
      if (p?.qualified && !r.qualified) {
        const failed = Object.entries(FILTER_LABELS).filter(([k]) => r[k] === false).map(([, l]) => l);
        const why = r.direction === "Bearish" ? "Turned bearish"
          : !r.sector_tradeable ? "Sector dropped out"
          : `Failed: ${failed.join(", ") || "filters"}`;
        exits.push({ ...r, why });
      }
    }
    const byScore = (a, b) => (b.stock_score ?? 0) - (a.stock_score ?? 0);
    return plain({
      prev: prev ? { id: prev.id, week: plain(prev.week_end_date) } : null,
      entries: entries.sort(byScore), exits: exits.sort(byScore),
      bullFlips: cur.filter((r) => r.is_new_flip && r.direction === "Bullish").sort(byScore),
      bearFlips: cur.filter((r) => r.is_new_flip && r.direction === "Bearish"),
    });
  });
}

// ---------------------------------------------------------------------------
// Backtests
// ---------------------------------------------------------------------------
const BT_LIST = {
  id: true, sweep_id: true, name: true, params: true, start_date: true, end_date: true, survivorship_bias: true,
  cagr: true, max_drawdown: true, sharpe: true, profit_factor: true, win_rate: true, trades: true, created_at: true,
  metrics: true, benchmark_metrics: true,
};

/** Latest sweep (all its runs, best CAGR first) + the most recent standalone runs. */
export async function getBacktests(sweepId) {
  const sweeps = await prisma.backtestSweep.findMany({ orderBy: { id: "desc" }, select: { id: true, name: true, created_at: true } });
  const sweep = sweeps.find((x) => x.id === sweepId) ?? sweeps[0] ?? null;
  const [sweepRuns, single] = await Promise.all([
    sweep ? prisma.backtestRun.findMany({ where: { sweep_id: sweep.id }, orderBy: { cagr: "desc" }, select: BT_LIST }) : [],
    prisma.backtestRun.findMany({ where: { sweep_id: null }, orderBy: { id: "desc" }, take: 150, select: BT_LIST })
      .then((rs) => rs.filter((r) => !r.params.experiment).slice(0, 10)),   // experiments live on /backtests
  ]);
  const flat = (r) => {
    const p = r.params, m = r.metrics, v = p.variants ?? {};
    return {
      id: r.id, sweep_id: r.sweep_id, name: r.name, kind: p.kind ?? "run",
      cagr: r.cagr, max_drawdown: r.max_drawdown, sharpe: r.sharpe, profit_factor: r.profit_factor,
      win_rate: r.win_rate, trades: r.trades,
      st: (p.supertrend ?? []).join(", "), weights: p.sector_weights,
      top_n: p.top_n_sectors, min_rs: p.min_rs, min_score: p.min_score ?? 0, k: p.max_positions, sizing: p.sizing,
      entry_weeks: p.entry_max_weeks_in_trend ?? 0, filters: p.entry_filters ?? "all",
      // runs saved before the option existed always exited on sector rotation too
      exit_rule: v.exit ?? (p.sector_exit === false ? "st" : "st+sector"),
      rank: v.rank ?? "scanner", regime: v.regime ?? p.bear_mode,
      mar: m.mar ?? null, exposure: m.exposure_pct, bench_cagr: r.benchmark_metrics.cagr_pct,
      ew_cagr: r.benchmark_metrics.universe_ew?.cagr_pct ?? null,
      alpha: r.cagr - r.benchmark_metrics.cagr_pct,
      oos_cagr: m.oos?.cagr_pct ?? null, oos_dd: m.oos?.max_drawdown_pct ?? null,
      oos_bench: m.oos?.benchmark_cagr_pct ?? null,
    };
  };
  const runs = sweepRuns.map(flat);
  return plain({
    sweeps, sweep, sweepRuns: runs.filter((r) => r.kind !== "walk_forward"),
    walkForward: runs.find((r) => r.kind === "walk_forward") ?? null, single: single.map(flat),
  });
}

/**
 * Everything on /backtests: the experiment log (runs saved by `python main.py experiments`, grouped by
 * params.experiment), the other standalone runs, and the sweeps.
 */
export async function getBacktestLog() {
  const [runs, sweeps, counts] = await Promise.all([
    prisma.backtestRun.findMany({
      where: { sweep_id: null }, orderBy: { id: "asc" },
      select: { id: true, name: true, params: true, start_date: true, end_date: true, created_at: true, cagr: true,
        max_drawdown: true, sharpe: true, win_rate: true, trades: true, metrics: true, survivorship_bias: true },
    }),
    prisma.backtestSweep.findMany({ orderBy: { id: "asc" }, select: { id: true, name: true, created_at: true } }),
    prisma.backtestRun.groupBy({ by: ["sweep_id"], where: { sweep_id: { not: null } }, _count: { _all: true }, _max: { cagr: true } }),
  ]);
  const row = (r) => ({
    id: r.id, name: r.name, variant: r.params.variant ?? null, start: r.start_date, end: r.end_date, created: r.created_at,
    cagr: r.cagr, dd: r.max_drawdown, sharpe: r.sharpe, win: r.win_rate, trades: r.trades,
    exposure: r.metrics.exposure_pct ?? null, final: r.metrics.final_equity ?? null,
    yearsLow: r.metrics.yearly ? Math.min(...Object.values(r.metrics.yearly)) : null, biased: r.survivorship_bias,
  });
  const groups = new Map();
  for (const r of runs.filter((x) => x.params.experiment)) {
    const k = r.params.experiment;
    if (!groups.has(k)) groups.set(k, { key: k, ...(r.params.experiment_info ?? {}), universe: r.params.universe, runs: [] });
    groups.get(k).runs.push(row(r));
  }
  const baseline = groups.get("baseline")?.runs[0] ?? null;
  groups.delete("baseline");
  const by = new Map(counts.map((c) => [c.sweep_id, c]));
  return plain({
    baseline, experiments: [...groups.values()].sort((a, b) => (a.order ?? 99) - (b.order ?? 99)),
    saved: runs.filter((r) => !r.params.experiment).map(row).reverse(),
    sweeps: sweeps.map((s) => ({ ...s, runs: by.get(s.id)?._count._all ?? 0, best: by.get(s.id)?._max.cagr ?? null })).reverse(),
  });
}

export async function getBacktest(id) {
  const run = await prisma.backtestRun.findUnique({
    where: { id },
    include: {
      equity: { orderBy: { date: "asc" } },
      tradeRows: { orderBy: { entry_date: "asc" } },
    },
  });
  return run ? plain(run) : null;
}
