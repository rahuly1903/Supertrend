import Link from "next/link";
import { Badge, Section, Stat } from "@/components/ui";
import { getBacktestLog } from "@/lib/data";
import { inr, niceDate, num, pct } from "@/lib/format";

export const metadata = { title: "Backtests" };

// Tests that are not saved as runs here: they need another universe, a combined signal set, or are
// per-signal analyses rather than portfolio backtests. Results as recorded when they ran (README).
const OTHER_TESTS = [
  { date: "2026-10-02", title: "Universe: Nifty 500 vs Total Market vs all NSE", verdict: "Total Market adopted",
    adopted: true, result: "Nifty 500 28.7% / −22.4% · Total Market (750) 31.6% / −24.0% · all NSE (2,593) 18.5% / −31.7%",
    links: [[3102, "Nifty 500 run"], [3103, "first 750-stock run"]] },
  { date: "2026-10-02", title: "Supertrend (7,3) + (10,3) combined entries", verdict: "Not adopted", adopted: false,
    result: "25.6% / −26.3% vs 28.7% / −22.4% (Nifty 500): the extra entries come late in the trend", links: [] },
  { date: "2026-10-01", title: "Every flip by score bucket (no portfolio)", verdict: "Analysis", adopted: null,
    result: "Win rate ~50% below a score of 80; 60% at 80–90 and 80% at 90+. The 70 cutoff barely separates winners",
    links: [] },
  { date: "2026-09-30", title: "Rule 1 vs v2 (sector gate, RS 80, sector exit) and filter variants",
    verdict: "Rule 1 adopted", adopted: true,
    result: "Rule 1 28.8% / −22.5% · v2 27.0% / −28.2% · + all hard filters & RS 80 18.9% / −27.3%",
    links: [[3000, "Rule 1"], [2993, "v2"], [3002, "+ hard filters"]] },
];

const runLink = (id) => `/backtest?id=${id}`;

function Verdict({ adopted, children }) {
  return <Badge tone={adopted === true ? "up" : adopted === false ? "muted" : "accent"}>{children}</Badge>;
}

function Delta({ v, base, invert = false }) {
  if (v == null || base == null) return null;
  const d = v - base;
  if (Math.abs(d) < 0.05) return null;
  const good = invert ? d < 0 : d > 0;
  return <span className={`text-xs ml-1 ${good ? "text-up" : "text-down"}`}>{d > 0 ? "+" : "−"}{num(Math.abs(d), 1)}</span>;
}

function RunTable({ rows, base }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular">
        <thead className="text-xs text-muted text-left">
          <tr className="border-b border-line">
            {["Variant", "CAGR", "Max DD", "Sharpe", "Trades", "Win", "Worst year", "Exposure", ""].map((h) => (
              <th key={h} className="px-3 py-1.5 font-medium whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isBase = base && r.id === base.id;
            return (
              <tr key={r.id} className={`border-b border-line last:border-0 hover:bg-panel-2 ${isBase ? "bg-panel-2" : ""}`}>
                <td className="px-3 py-1.5">{isBase ? <b>Current default</b> : r.variant ?? r.name}</td>
                <td className="px-3 py-1.5 whitespace-nowrap">{pct(r.cagr, 1, false)}{!isBase ? <Delta v={r.cagr} base={base?.cagr} /> : null}</td>
                <td className="px-3 py-1.5 whitespace-nowrap">{pct(r.dd, 1, false)}{!isBase ? <Delta v={r.dd} base={base?.dd} /> : null}</td>
                <td className="px-3 py-1.5">{num(r.sharpe, 2)}</td>
                <td className="px-3 py-1.5">{r.trades}</td>
                <td className="px-3 py-1.5">{pct(r.win, 0, false)}</td>
                <td className="px-3 py-1.5">{pct(r.yearsLow, 1)}</td>
                <td className="px-3 py-1.5">{pct(r.exposure, 0, false)}</td>
                <td className="px-3 py-1.5 text-right whitespace-nowrap">
                  <Link className="text-accent text-xs hover:underline" href={runLink(r.id)}>#{r.id} →</Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default async function BacktestsPage() {
  const { baseline, experiments, saved, sweeps } = await getBacktestLog();
  const universe = experiments[0]?.universe;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Backtests</h1>
        <span className="text-xs text-muted">
          Every test so far, one page. Parameter sweeps are on <Link className="text-accent" href="/backtest">Sweeps</Link>.
        </span>
      </div>

      {baseline ? (
        <Section title="Current default: Rule 1"
          right={<Link className="text-xs text-accent hover:underline" href={runLink(baseline.id)}>equity & trades #{baseline.id} →</Link>}>
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
            <Stat label="CAGR" value={pct(baseline.cagr, 1)} tone="up" />
            <Stat label="Max drawdown" value={pct(baseline.dd, 1)} tone="down" />
            <Stat label="Sharpe" value={num(baseline.sharpe, 2)} />
            <Stat label="Trades" value={baseline.trades} sub={`win ${pct(baseline.win, 0, false)}`} />
            <Stat label="Worst year" value={pct(baseline.yearsLow, 1)} />
            <Stat label="Final equity" value={inr(baseline.final, 0)} sub="from ₹10 L" />
            <Stat label="Period" value={`${String(baseline.start).slice(0, 4)}–${String(baseline.end).slice(0, 4)}`}
              sub={`${niceDate(baseline.start)} → ${niceDate(baseline.end)}`} />
          </div>
          <p className="text-xs text-muted mt-3">
            {universe ?? "Universe"} · buy the week the weekly Supertrend (7,3) flips bullish with score ≥ 70 (turnover ≥ ₹10 Cr,
            price ≥ ₹50, not ASM/GSM) · 10% of equity per stock from own cash · sell when the Supertrend turns bearish or the
            score falls below 30 · fills at the next week&apos;s open, costs included.
            {baseline.biased ? " Survivorship-biased: today's constituents for every past week." : ""}
          </p>
        </Section>
      ) : (
        <Section title="Experiments">
          <p className="text-sm text-muted">No experiment runs yet. Run <code>python main.py experiments</code>.</p>
        </Section>
      )}

      {experiments.length ? (
        <p className="text-xs text-muted -mb-2">
          Each experiment changes a few settings of the current default and is re-run on today&apos;s data
          (<code>python main.py experiments</code>), so numbers can differ from those quoted when a test first ran.
          Green/red deltas are versus the default (for max drawdown, smaller is green).
        </p>
      ) : null}

      {experiments.map((e) => (
        <Section key={e.key}
          title={<span className="flex items-center gap-2 flex-wrap">{e.title}
            <Verdict adopted={e.adopted}>{e.adopted ? "Adopted" : "Not adopted"}</Verdict>
            <span className="text-xs font-normal text-muted">first tested {niceDate(e.date)}</span></span>}>
          <p className="text-sm mb-1"><b>Question:</b> {e.question}</p>
          <p className="text-sm text-muted mb-3"><b className="text-text">Finding:</b> {e.verdict}</p>
          <RunTable rows={[...(baseline ? [baseline] : []), ...e.runs]} base={baseline} />
        </Section>
      ))}

      <Section title="Other tests (not saved as runs here)">
        <ul className="text-sm divide-y divide-line -my-2">
          {OTHER_TESTS.map((t) => (
            <li key={t.title} className="py-2.5 flex flex-col gap-1">
              <div className="flex items-center gap-2 flex-wrap">
                <b>{t.title}</b> <Verdict adopted={t.adopted}>{t.verdict}</Verdict>
                <span className="text-xs text-muted">{niceDate(t.date)}</span>
              </div>
              <div className="text-muted">{t.result}</div>
              {t.links.length ? (
                <div className="flex gap-3 text-xs">
                  {t.links.map(([id, label]) => (
                    <Link key={id} className="text-accent hover:underline" href={runLink(id)}>{label} #{id} →</Link>
                  ))}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </Section>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <Section title={`Saved single runs (${saved.length})`}>
          <div className="overflow-x-auto max-h-[28rem]">
            <table className="w-full text-sm tabular">
              <thead className="text-xs text-muted text-left sticky top-0 bg-panel">
                <tr className="border-b border-line">
                  {["#", "Date", "Name", "CAGR", "Max DD", "Trades", "Period"].map((h) => (
                    <th key={h} className="px-3 py-1.5 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {saved.map((r) => (
                  <tr key={r.id} className="border-b border-line last:border-0 hover:bg-panel-2">
                    <td className="px-3 py-1.5"><Link className="text-accent hover:underline" href={runLink(r.id)}>{r.id}</Link></td>
                    <td className="px-3 py-1.5 whitespace-nowrap text-muted">{String(r.created).slice(0, 10)}</td>
                    <td className="px-3 py-1.5"><Link className="hover:underline" href={runLink(r.id)}>{r.name}</Link></td>
                    <td className="px-3 py-1.5">{pct(r.cagr, 1, false)}</td>
                    <td className="px-3 py-1.5">{pct(r.dd, 1, false)}</td>
                    <td className="px-3 py-1.5">{r.trades}</td>
                    <td className="px-3 py-1.5 whitespace-nowrap text-muted">{String(r.start).slice(0, 4)}–{String(r.end).slice(0, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section title={`Sweeps (${sweeps.length})`}>
          <table className="w-full text-sm tabular">
            <thead className="text-xs text-muted text-left">
              <tr className="border-b border-line">
                {["#", "Date", "Name", "Runs", "Best CAGR", ""].map((h) => <th key={h} className="px-3 py-1.5 font-medium">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {sweeps.map((s) => (
                <tr key={s.id} className="border-b border-line last:border-0 hover:bg-panel-2">
                  <td className="px-3 py-1.5">{s.id}</td>
                  <td className="px-3 py-1.5 whitespace-nowrap text-muted">{String(s.created_at).slice(0, 10)}</td>
                  <td className="px-3 py-1.5">{s.name}</td>
                  <td className="px-3 py-1.5">{s.runs}</td>
                  <td className="px-3 py-1.5">{pct(s.best, 1, false)}</td>
                  <td className="px-3 py-1.5 text-right">
                    <Link className="text-accent text-xs hover:underline" href={`/backtest?sweep=${s.id}`}>open →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-muted mt-3">
            The best CAGR of a large grid is mostly luck; each sweep page shows the walk-forward result where one was run.
          </p>
        </Section>
      </div>
    </div>
  );
}
