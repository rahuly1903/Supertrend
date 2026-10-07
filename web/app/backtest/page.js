import Link from "next/link";
import DataTable from "@/components/DataTable";
import EquityChart from "@/components/charts/EquityChart";
import { Badge, Empty, Section, Stat } from "@/components/ui";
import { getBacktest, getBacktests } from "@/lib/data";
import { inr, niceDate, num, pct } from "@/lib/format";

export const metadata = { title: "Backtest" };

const SWEEP_COLUMNS = [
  { key: "name", header: "Run", type: "link", href: "/backtest?sweep={sweep_id}&id={id}" },
  { key: "st", header: "ST", type: "text" },
  { key: "weights", header: "Sector weights", type: "text" },
  { key: "top_n", header: "Top N", type: "int" },
  { key: "min_rs", header: "Min RS", type: "int" },
  { key: "min_score", header: "Min score", type: "int" },
  { key: "k", header: "K", type: "int" },
  { key: "entry_weeks", header: "Flip ≤ wks", type: "int" },
  { key: "exit_rule", header: "Exit", type: "text" },
  { key: "rank", header: "Rank by", type: "text" },
  { key: "regime", header: "Bear", type: "text" },
  { key: "cagr", header: "CAGR", type: "pct", d: 1 },
  { key: "oos_cagr", header: "CAGR (test span)", type: "pct", d: 1 },
  { key: "alpha", header: "vs Nifty 500", type: "pct", d: 1 },
  { key: "max_drawdown", header: "Max DD", type: "pct", d: 1 },
  { key: "mar", header: "MAR", type: "num", d: 2, tone: 0 },
  { key: "sharpe", header: "Sharpe", type: "num", d: 2, tone: 0 },
  { key: "profit_factor", header: "Profit factor", type: "num", d: 2, tone: 1 },
  { key: "win_rate", header: "Win rate", type: "pct", d: 0, unsigned: true },
  { key: "trades", header: "Trades", type: "int" },
  { key: "exposure", header: "Exposure", type: "pct", d: 0, unsigned: true },
];

const TRADE_COLUMNS = [
  { key: "symbol", header: "Symbol", type: "symbol" },
  { key: "industry", header: "Sector", type: "text" },
  { key: "signal_date", header: "Signal", type: "date" },
  { key: "entry_date", header: "Entry wk", type: "date" },
  { key: "entry_price", header: "Entry", type: "num" },
  { key: "exit_date", header: "Exit wk", type: "date" },
  { key: "exit_price", header: "Exit", type: "num" },
  { key: "qty", header: "Qty", type: "int" },
  { key: "pnl", header: "P&L ₹", type: "num", d: 0, tone: 0 },
  { key: "pnl_pct", header: "P&L %", type: "pct", d: 1 },
  { key: "weeks_held", header: "Weeks", type: "int" },
  { key: "exit_reason", header: "Exit reason", type: "text" },
  { key: "entry_score", header: "Score", type: "num", d: 1 },
];

const EXIT_LABEL = {
  supertrend: "Supertrend turned bearish", sector: "Sector left top N", stale: "Dropped from data", end: "Open at end",
  stop: "Protective / trailing stop", ma: "Close below weekly SMA", time: "Time stop (no progress)",
  score: "Score fell below the exit level", trim: "Trimmed (partial sale)", swap: "Swapped for a stronger signal",
  tp1: "Profit booked: 1st target", tp2: "Profit booked: 2nd target",
};
const EXIT_RULE = {
  st: "Supertrend only", "st+sector": "Supertrend or sector rotation", stop10: "+ 10% hard stop",
  trail20: "+ 15% stop trailing 20% below peak", ma10: "Supertrend or close < 10-wk SMA",
};

function exitText(p) {
  const parts = ["Supertrend bearish"];
  if (p.sector_exit !== false) parts.push(`sector out of top N ${p.sector_exit_weeks ?? 2}w`);
  if (p.stop_pct) parts.push(`${p.stop_pct}% stop`);
  if (p.trail_pct) parts.push(`trail ${p.trail_pct}% below peak`);
  if (p.trail_ma_weeks) parts.push(`close < ${p.trail_ma_weeks}-wk SMA`);
  if (p.time_stop_weeks) parts.push(`time stop ${p.time_stop_weeks}w`);
  if (p.exit_score_below) parts.push(`score < ${p.exit_score_below}`);
  return parts.join(" · ");
}

function Metrics({ m, b }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
      <Stat label="CAGR" value={pct(m.cagr_pct, 1)} tone={m.cagr_pct >= b.cagr_pct ? "up" : "down"}
        sub={`Nifty 500 ${pct(b.cagr_pct, 1)}${b.universe_ew?.cagr_pct != null ? ` · equal-wt universe ${pct(b.universe_ew.cagr_pct, 1)}` : ""}`} />
      <Stat label="Total return" value={pct(m.total_return_pct, 1)} sub={`Nifty 500 ${pct(b.total_return_pct, 1)}`} />
      <Stat label="Max drawdown" value={pct(m.max_drawdown_pct, 1)} tone="down" sub={`Nifty 500 ${pct(b.max_drawdown_pct, 1)}`} />
      <Stat label="Sharpe" value={num(m.sharpe, 2)} sub={`Nifty 500 ${num(b.sharpe, 2)}`} />
      <Stat label="Win rate" value={pct(m.win_rate_pct, 0, false)} sub={`${m.trades} trades · ${num(m.trades_per_year, 0)}/yr`} />
      <Stat label="Profit factor" value={num(m.profit_factor, 2)} tone={m.profit_factor >= 1 ? "up" : "down"} />
      <Stat label="Avg win / loss" value={<><span className="text-up">{pct(m.avg_win_pct, 1)}</span> / <span className="text-down">{pct(m.avg_loss_pct, 1)}</span></>} />
      <Stat label="Expectancy" value={pct(m.expectancy_pct, 2)} sub={`${inr(m.expectancy_inr, 0)} per trade`} />
      <Stat label="Exposure" value={pct(m.exposure_pct, 0, false)} sub="avg % of equity invested" />
      <Stat label="Avg holding" value={`${num(m.avg_weeks_held, 1)} wks`} />
      <Stat label="Final equity" value={inr(m.final_equity, 0)}
        sub={m.capital_added ? `Nifty 500, same cash flows ${inr(m.benchmark_final, 0)}` : undefined} />
      {m.capital_added ? <>
        <Stat label="Capital put in" value={inr(m.capital_contributed, 0)} sub={`${inr(m.capital_added, 0)} added over time`} />
        <Stat label="Net profit" value={inr(m.net_profit, 0)} tone={m.net_profit >= 0 ? "up" : "down"}
          sub={`${pct(m.return_on_capital_pct, 0)} on capital put in`} />
        <Stat label="XIRR" value={pct(m.xirr_pct, 1)} tone={m.xirr_pct >= (m.benchmark_xirr_pct ?? -Infinity) ? "up" : "down"}
          sub={`money-weighted · Nifty 500 ${pct(m.benchmark_xirr_pct, 1)}`} />
      </> : null}
      <Stat label="Volatility" value={pct(m.volatility_pct, 1, false)} sub={`Nifty 500 ${pct(b.volatility_pct, 1, false)}`} />
      <Stat label="MAR" value={num(m.mar, 2)} sub="CAGR ÷ |max drawdown|" />
    </div>
  );
}

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? (s[(s.length - 1) >> 1] + s[s.length >> 1]) / 2 : null;
}

const cls = (v) => (v == null ? "text-muted" : v >= 0 ? "text-up" : "text-down");

function Table({ head, rows }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular">
        <thead className="text-xs text-muted text-left">
          <tr>{head.map((h) => <th key={h} className="py-1 pr-4 font-medium whitespace-nowrap">{h}</th>)}</tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );
}

/** Out-of-sample result: parameters picked each year from the years before it only. */
function WalkForward({ wf, wfRun, runs, baseline }) {
  const p = wfRun.params, cfg = p.walk_forward, b = wfRun.benchmark_metrics;
  const oos = runs.map((r) => r.oos_cagr).filter((v) => v != null);
  const hindsight = runs.reduce((best, r) => (r.oos_cagr ?? -1e9) > (best?.oos_cagr ?? -1e9) ? r : best, null);
  const name = Object.fromEntries(runs.map((r) => [r.id, r]));
  return (
    <Section title={`Walk-forward, out of sample: ${niceDate(wfRun.start_date)} → ${niceDate(wfRun.end_date)}`}
      right={<Link className="text-xs text-accent" href={`/backtest?sweep=${wf.sweep_id}&id=${wf.id}`}>equity & trades →</Link>}>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-4">
        <Stat label="Walk-forward CAGR" value={pct(wf.cagr, 1)} tone={wf.cagr >= b.cagr_pct ? "up" : "down"}
          sub={`max DD ${pct(wf.max_drawdown, 1)}`} />
        <Stat label="Nifty 500" value={pct(b.cagr_pct, 1)} sub={`max DD ${pct(b.max_drawdown_pct, 1)}`} />
        <Stat label="Equal-wt universe" value={pct(b.universe_ew?.cagr_pct, 1)} sub="buy & hold same stocks" />
        <Stat label="Baseline (config)" value={pct(baseline?.oos_cagr, 1)} sub={baseline ? `max DD ${pct(baseline.oos_dd, 1)}` : "not in grid"} />
        <Stat label="Median of all sets" value={pct(median(oos), 1)} sub={`${oos.filter((v) => v > b.cagr_pct).length} / ${oos.length} beat Nifty 500`} />
        <Stat label="Hindsight best" value={pct(hindsight?.oos_cagr, 1)} sub="not achievable: picked after the fact" />
      </div>
      <Table head={["Test period", "Return", "Nifty 500", `Chosen (top ${cfg.top_k} by ${cfg.metric.toUpperCase()} over the previous ${cfg.train_years}y)`]}
        rows={p.folds.map((f) => (
          <tr key={f.test_start} className="border-t border-line align-top">
            <td className="py-1.5 pr-4 whitespace-nowrap">{niceDate(f.test_start)} → {niceDate(f.test_end)}</td>
            <td className={`py-1.5 pr-4 ${cls(f.return_pct)}`}>{pct(f.return_pct, 1)}</td>
            <td className={`py-1.5 pr-4 ${cls(f.benchmark_return_pct)}`}>{pct(f.benchmark_return_pct, 1)}</td>
            <td className="py-1.5 pr-4 text-xs">
              {f.chosen.map((c) => (
                <div key={c.id}><Link className="hover:text-accent" href={`/backtest?sweep=${wf.sweep_id}&id=${c.id}`}>{name[c.id]?.name ?? c.name}</Link></div>
              ))}
            </td>
          </tr>
        ))} />
      <p className="text-xs text-muted mt-3">
        Each year the {cfg.top_k} best parameter sets over the previous {cfg.train_years} years (by {cfg.metric === "mar" ? "CAGR ÷ max drawdown" : cfg.metric})
        are traded as an equal blend for the next {cfg.test_years} year, then re-picked. No year is traded with parameters that saw it,
        so this is the fair estimate of what optimising the grid is worth. Compare it with the equal-weight universe: the gap
        between that and the Nifty 500 is roughly the survivorship bias.
      </p>
    </Section>
  );
}

const AXES = [
  ["st", "Supertrend"], ["top_n", "Top N sectors (0 = no gate)"], ["min_rs", "Min RS"], ["min_score", "Min score"],
  ["k", "Positions (K)"], ["entry_weeks", "Buy within N weeks of flip (0 = any)"], ["filters", "Entry filters"], ["exit_rule", "Exit rule"], ["rank", "Rank by"], ["regime", "Bear regime"], ["weights", "Sector weights"],
];

/** Median result for each value of each parameter, holding everything else across the grid. */
function AxisEffects({ runs }) {
  const bench = runs[0].bench_cagr, ew = runs[0].ew_cagr;
  const beat = (g) => (ew != null ? g.filter((r) => r.cagr > ew).length : g.filter((r) => r.cagr > bench).length);
  const hasOos = runs.some((r) => r.oos_cagr != null);
  const rows = [];
  for (const [key, label] of AXES) {
    const groups = {};
    for (const r of runs) (groups[String(r[key])] ??= []).push(r);
    if (Object.keys(groups).length < 2) continue;
    Object.entries(groups).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true })).forEach(([v, g], i) => {
      const med = (f) => median(g.map(f).filter((x) => x != null));
      rows.push(
        <tr key={key + v} className={i === 0 ? "border-t-2 border-line" : "border-t border-line"}>
          <td className="py-1.5 pr-4 text-muted whitespace-nowrap">{i === 0 ? label : ""}</td>
          <td className="py-1.5 pr-4 whitespace-nowrap">{key === "exit_rule" ? (EXIT_RULE[v] ?? v) : v}</td>
          <td className={`py-1.5 pr-4 ${cls(med((r) => r.cagr))}`}>{pct(med((r) => r.cagr), 1)}</td>
          {hasOos ? <td className={`py-1.5 pr-4 ${cls(med((r) => r.oos_cagr))}`}>{pct(med((r) => r.oos_cagr), 1)}</td> : null}
          <td className="py-1.5 pr-4">{pct(med((r) => r.max_drawdown), 1)}</td>
          <td className="py-1.5 pr-4">{num(med((r) => r.mar), 2)}</td>
          <td className="py-1.5 pr-4">{pct(med((r) => r.win_rate), 0, false)}</td>
          <td className="py-1.5 pr-4">{beat(g)} / {g.length}</td>
        </tr>,
      );
    });
  }
  const cagrs = runs.map((r) => r.cagr);
  return (
    <Section title="What each setting is worth (medians across the grid)">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-4">
        <Stat label="Beat Nifty 500" value={`${runs.filter((r) => r.cagr > bench).length} / ${runs.length}`} sub={`index CAGR ${pct(bench, 1)}`} />
        {ew != null ? <Stat label="Beat equal-wt universe" value={`${beat(runs)} / ${runs.length}`} sub={`buy & hold same stocks ${pct(ew, 1)}`} /> : null}
        <Stat label="Median CAGR" value={pct(median(cagrs), 1)} />
        <Stat label="Worst / best" value={<><span className="text-down">{pct(Math.min(...cagrs), 1)}</span> / <span className="text-up">{pct(Math.max(...cagrs), 1)}</span></>} />
        <Stat label="Profit factor > 1" value={`${runs.filter((r) => (r.profit_factor ?? 0) > 1).length} / ${runs.length}`} />
        <Stat label="Median max DD" value={pct(median(runs.map((r) => r.max_drawdown)), 1)} tone="down" />
      </div>
      <Table head={["Parameter", "Value", "Median CAGR", ...(hasOos ? ["CAGR (test span)"] : []), "Median max DD", "Median MAR", "Median win rate", ew != null ? "Beat eq-wt universe" : "Beat index"]} rows={rows} />
      <p className="text-xs text-muted mt-3">
        Each row is the median over every run with that value, so it shows a setting&apos;s effect across all the other
        choices rather than in one lucky combination. Prefer settings that help in both CAGR columns. &quot;Beat&quot; compares with
        buying every stock of the same (survivor) list equally, which strips out most of the survivorship bias.
      </p>
    </Section>
  );
}

function Yearly({ m }) {
  const years = Object.keys(m.yearly ?? {});
  if (years.length < 2) return null;
  const yb = m.yearly_benchmark ?? {};
  return (
    <Section title="Calendar-year returns">
      <Table head={["", ...years]} rows={[
        <tr key="s" className="border-t border-line"><td className="py-1.5 pr-4 text-muted">Strategy</td>
          {years.map((y) => <td key={y} className={`py-1.5 pr-4 ${cls(m.yearly[y])}`}>{pct(m.yearly[y], 1)}</td>)}</tr>,
        <tr key="b" className="border-t border-line"><td className="py-1.5 pr-4 text-muted">Nifty 500</td>
          {years.map((y) => <td key={y} className={`py-1.5 pr-4 ${cls(yb[y])}`}>{pct(yb[y], 1)}</td>)}</tr>,
      ]} />
      <p className="text-xs text-muted mt-2">First and last years are partial.</p>
    </Section>
  );
}

export default async function BacktestPage({ searchParams }) {
  const sp = await searchParams;
  const { sweeps, sweep, sweepRuns, walkForward, single } = await getBacktests(sp.sweep ? Number(sp.sweep) : undefined);
  const all = [...sweepRuns, ...single];
  if (!all.length) {
    return (
      <div className="card p-8 text-center">
        <h1 className="text-lg font-semibold">No backtests yet</h1>
        <p className="text-sm text-muted mt-2">Run <code>python main.py backtest --sweep</code> in <code>scanner/</code>.</p>
      </div>
    );
  }
  // Default to the walk-forward result (or the config baseline), never the best in-sample run:
  // the top CAGR of a large grid is mostly luck.
  const baseline = sweepRuns.find((r) => r.st === "10, 3" && r.weights === "default" && r.top_n === 5 && r.min_rs === 70
    && !r.min_score && (r.k === 10 || r.k == null) && r.exit_rule === "st" && r.rank === "scanner" && r.regime === "half")
    ?? sweepRuns.find((r) => r.st === "10, 3" && r.weights === "default" && r.top_n === 5 && r.min_rs === 70);
  const id = sp.id && /^\d+$/.test(sp.id) ? Number(sp.id) : (walkForward ?? baseline ?? sweepRuns[0] ?? single[0]).id;
  const run = await getBacktest(id);
  const wfRun = walkForward ? (walkForward.id === id ? run : await getBacktest(walkForward.id)) : null;
  const m = run?.metrics, b = run?.benchmark_metrics, p = run?.params;
  const isWf = p?.kind === "walk_forward";

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Backtest</h1>
        {run ? <span className="text-xs text-muted">{niceDate(run.start_date)} → {niceDate(run.end_date)} · {num(m.years, 1)} years · weekly rebalance, fills at next week&apos;s open</span> : null}
      </div>

      {sweeps.length > 1 ? (
        <div className="flex items-center gap-2 flex-wrap text-sm">
          <span className="text-xs text-muted">Sweep</span>
          {sweeps.map((x) => (
            <Link key={x.id} href={`/backtest?sweep=${x.id}`}
              className={`btn ${sweep?.id === x.id ? "btn-active" : ""}`}>#{x.id} {x.name}</Link>
          ))}
        </div>
      ) : null}

      {run?.survivorship_bias ? (
        <div className="rounded-lg border-l-4 border-warn bg-warn-bg px-4 py-2.5 text-sm">
          <b>Survivorship bias:</b> the test uses today&apos;s universe constituents for every past week, because
          point-in-time membership is only recorded from the first universe refresh onward. Stocks that fell out of the
          index (often the losers) are missing, so results are optimistic.
        </div>
      ) : null}

      {run ? (
        <>
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="font-semibold">{run.name}</h2>
            {isWf ? <Badge tone="accent">parameters re-picked every {p.walk_forward.test_years}y · see the table below</Badge> : <>
            <Badge>ST ({(p.supertrend ?? []).join(", ")})</Badge>
            <Badge>weights: {p.sector_weights}</Badge>
            <Badge>top {p.top_n_sectors} sectors</Badge>
            <Badge>RS ≥ {p.min_rs}</Badge>
            <Badge>{p.max_positions > 0 ? `K = ${p.max_positions}` : "no position limit"}</Badge>
            <Badge>{p.sizing !== "fixed" ? `${p.sizing} sizing`
              : p.position_pct ? `${p.position_pct}% of equity per stock${p.position_size ? ` (min ${inr(p.position_size, 0)})` : ""}`
              : `${inr(p.position_size, 0)} per stock`}</Badge>
            {p.add_capital ? <Badge tone="accent">adds capital when cash runs short</Badge> : null}
            {p.entry_max_weeks_in_trend ? <Badge>entry: week 1–{p.entry_max_weeks_in_trend} of trend</Badge> : null}
            <Badge>bear: {p.bear_mode}</Badge>
            {p.min_score ? <Badge>score ≥ {p.min_score}</Badge> : null}
            {p.score_weights ? <Badge>rank: {Object.entries(p.score_weights).map(([k, v]) => `${k} ${v}`).join(", ")}{p.score_penalties === false ? ", no penalties" : ""}</Badge> : null}
            <Badge tone="accent">exit: {exitText(p)}</Badge>
            <Badge>costs {p.cost_round_trip_pct}% RT + {p.slippage_pct}%/side</Badge>
            </>}
          </div>
          <Metrics m={m} b={b} />
          <Section title="Equity vs Nifty 500 (buy & hold) · drawdown and invested %">
            <EquityChart equity={run.equity} />
          </Section>
          <Yearly m={m} />
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
            <Section title="Exit reasons">
              <ul className="text-sm divide-y divide-line -my-2">
                {Object.entries(m.exit_reasons ?? {}).map(([k, n]) => {
                  const t = run.tradeRows.filter((x) => x.exit_reason === k);
                  const avg = t.reduce((s, x) => s + x.pnl_pct, 0) / (t.length || 1);
                  return (
                    <li key={k} className="py-2 flex justify-between gap-2">
                      <span>{EXIT_LABEL[k] ?? k}</span>
                      <span className="tabular text-right">{n} <span className={`text-xs ${avg >= 0 ? "text-up" : "text-down"}`}>({pct(avg, 1)} avg)</span></span>
                    </li>
                  );
                })}
              </ul>
            </Section>
            <Section title={`Trades (${run.tradeRows.length})`} className="lg:col-span-3">
              <div className="-m-4">
                <DataTable rows={run.tradeRows} columns={TRADE_COLUMNS} rowKey="id" maxHeight="420px" />
              </div>
            </Section>
          </div>
        </>
      ) : <Empty>Backtest {id} not found.</Empty>}

      {walkForward && wfRun ? <WalkForward wf={walkForward} wfRun={wfRun} runs={sweepRuns} baseline={baseline} /> : null}
      {sweepRuns.length ? <AxisEffects runs={sweepRuns} /> : null}
      {sweepRuns.length ? (
        <Section title={`Parameter sweep "${sweep.name}" — ${sweepRuns.length} runs, best CAGR first`}>
          <div className="-m-4">
            <DataTable rows={sweepRuns} columns={SWEEP_COLUMNS} rowKey="id" maxHeight="480px" />
          </div>
        </Section>
      ) : null}
      {single.length ? (
        <Section title="Single runs">
          <div className="-m-4"><DataTable rows={single} columns={SWEEP_COLUMNS} rowKey="id" maxHeight="none" /></div>
        </Section>
      ) : null}
      <p className="text-xs text-muted">
        Rules: every week buy the highest-ranked stocks that pass the filters (hard filters, RS ≥ min, rank score ≥ min score, sector in the top N
        that pass the tradeable tests, or any sector when N = 0) up to K positions. Exits are decided on the weekly close and fill at the
        next week&apos;s open; protective stops fill intra-week at the stop (or the open on a gap). Regime: half-size entries or ignored,
        per run. This is research on historical data, not investment advice.
      </p>
    </div>
  );
}
