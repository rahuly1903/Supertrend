import Link from "next/link";
import { getDashboard, getRule1, resolveRun } from "@/lib/data";
import { RULE1 } from "@/lib/filters";
import { inr, num, niceDate, pct } from "@/lib/format";
import {
  Badge, Empty, GradeBadge, NoRuns, Pct, QuadrantBadge, Section, SectorLink, Stat, StockLink,
} from "@/components/ui";

export const metadata = { title: "Dashboard" };

const REGIME = {
  bull: { tone: "border-up bg-up-bg", title: "Bull regime", text: "Trend and breadth support new entries." },
  neutral: { tone: "border-warn bg-warn-bg", title: "Neutral regime", text: "Mixed signals: be selective, size normally." },
  bear: {
    tone: "border-down bg-down-bg", title: "Bear regime",
    text: "Nifty 500 is in a downtrend. Picks are flagged: reduce size / avoid new entries.",
  },
};

function RegimeBanner({ r }) {
  if (!r) return null;
  const cfg = REGIME[r.regime];
  return (
    <div className={`rounded-lg border-l-4 px-4 py-3 ${cfg.tone} flex flex-wrap items-center gap-x-8 gap-y-2`}>
      <div>
        <div className="font-semibold">{cfg.title}</div>
        <div className="text-sm text-muted">{cfg.text}</div>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm tabular">
        <span>Nifty 500 <b>{num(r.nifty500_close, 0)}</b></span>
        <span>SMA200 <b>{num(r.nifty500_sma200, 0)}</b>
          <span className={r.above_200dma ? "text-up" : "text-down"}> ({r.above_200dma ? "above" : "below"})</span></span>
        <span>SMA200 <b className={r.sma200_rising ? "text-up" : "text-down"}>{r.sma200_rising ? "rising" : "falling"}</b></span>
        <span>ST breadth <b>{pct(r.market_breadth_pct, 0, false)}</b></span>
        <span>% above SMA200 <b>{pct(r.pct_above_sma200, 0, false)}</b></span>
      </div>
    </div>
  );
}

function SectorCard({ s, week }) {
  return (
    <div className="card p-4 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-xs text-muted">#{s.sector_rank}</div>
          <div className="font-semibold leading-tight"><SectorLink industry={s.industry} week={week} /></div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular">{num(s.sector_score, 0)}</div>
          <div className="text-[11px] text-muted">score</div>
        </div>
      </div>
      <div className="flex gap-1.5 flex-wrap">
        <QuadrantBadge q={s.rrg_quadrant} />
        {s.tradeable ? <Badge tone="up">Tradeable</Badge> : <Badge>Not tradeable</Badge>}
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs tabular">
        <dt className="text-muted">13W return</dt><dd className="text-right"><Pct v={s.ret_13w} /></dd>
        <dt className="text-muted">ST breadth</dt><dd className="text-right">{pct(s.st_breadth, 0, false)}</dd>
        <dt className="text-muted">Breadth Δ4W</dt><dd className="text-right"><Pct v={s.breadth_change_4w} d={0} suffix=" pts" /></dd>
        <dt className="text-muted">RS trend</dt>
        <dd className={`text-right ${s.rs_trend > 1 ? "text-up" : "text-down"}`}>{num(s.rs_trend, 3)}</dd>
      </dl>
    </div>
  );
}

/** Stock score coloured by the Rule 1 levels: green >= buy level, red below the exit level. */
function Score({ v }) {
  if (v == null) return <span className="text-muted">—</span>;
  const tone = v >= RULE1.minScore ? "text-up font-semibold" : RULE1.exitScore && v < RULE1.exitScore ? "text-down" : "";
  return <span className={tone}>{num(v, 1)}</span>;
}

function Rule1Table({ rows, week, kind }) {
  const head = ["Symbol", "Stock score", "Sector", "Signal week", "Signal close", "Price",
    ...(kind === "buy" ? [] : ["Since signal"]), ...(kind === "exit" ? ["Held"] : ["Stop (ST)", "To stop"])];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular">
        <thead className="text-xs text-muted text-left">
          <tr className="border-b border-line">{head.map((h) => <th key={h} className="px-3 py-1.5 font-medium whitespace-nowrap">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol} className="border-b border-line last:border-0 hover:bg-panel-2">
              <td className="px-3 py-1.5"><StockLink symbol={r.symbol} week={week} /></td>
              <td className="px-3 py-1.5"><Score v={r.score} /></td>
              <td className="px-3 py-1.5 text-muted whitespace-nowrap"><SectorLink industry={r.industry} week={week} /></td>
              <td className="px-3 py-1.5 whitespace-nowrap">{niceDate(r.signal_week)}</td>
              <td className="px-3 py-1.5">{num(r.signal_price)}</td>
              <td className="px-3 py-1.5">{num(r.price)}</td>
              {kind === "buy" ? null : <td className="px-3 py-1.5"><Pct v={r.change_pct} /></td>}
              {kind === "exit" ? <td className="px-3 py-1.5">{r.weeks} wks</td> : <>
                <td className="px-3 py-1.5">{num(r.stop)}</td>
                <td className="px-3 py-1.5">{pct(r.stop_dist_pct, 1, false)}</td></>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** This week's Rule 1 actions: buy the new flips, sell the ones whose Supertrend turned bearish. */
function Portfolio({ pf, buys }) {
  if (!pf) return <p className="text-sm text-muted">Run <code>python main.py backtest</code> with the Rule 1 defaults to see the model portfolio.</p>;
  if (pf.stale) {
    return (
      <p className="text-sm text-muted">
        The latest Rule 1 backtest (<Link className="text-accent" href={`/backtest?id=${pf.id}`}>#{pf.id}</Link>) ends on {niceDate(pf.endWeek)},
        not this week. Run <code>python main.py backtest</code> after the weekly scan to refresh the model portfolio.
      </p>
    );
  }
  const take = pf.k == null ? buys : buys.slice(0, Math.max(pf.freeSlots, 0));
  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto">
        <table className="w-full text-sm tabular">
          <thead className="text-xs text-muted text-left">
            <tr className="border-b border-line">
              {["Symbol", "Stock score", "Sector", "Bought (wk)", "Entry", "Price", "P&L", "Stop (ST)", "To stop", "Action"].map((h) => (
                <th key={h} className="px-3 py-1.5 font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pf.holdings.map((h) => (
              <tr key={h.symbol} className="border-b border-line last:border-0 hover:bg-panel-2">
                <td className="px-3 py-1.5"><StockLink symbol={h.symbol} /></td>
                <td className="px-3 py-1.5"><Score v={h.score} /></td>
                <td className="px-3 py-1.5 text-muted whitespace-nowrap"><SectorLink industry={h.industry} /></td>
                <td className="px-3 py-1.5 whitespace-nowrap">{niceDate(h.entry_week)}</td>
                <td className="px-3 py-1.5">{num(h.entry_price)}</td>
                <td className="px-3 py-1.5">{num(h.price)}</td>
                <td className="px-3 py-1.5"><Pct v={h.change_pct} /></td>
                <td className="px-3 py-1.5">{num(h.stop)}</td>
                <td className="px-3 py-1.5">{pct(h.stop_dist_pct, 1, false)}</td>
                <td className="px-3 py-1.5">{h.sell ? <Badge tone="down">Sell at open</Badge> : <Badge tone="up">Hold</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">
        {pf.k == null
          ? <>{pf.holdings.length} positions
            {pf.positionPct ? <> · {pf.positionPct}% of equity each{pf.positionSize ? <> (min {inr(pf.positionSize, 0)})</> : null}</>
              : pf.positionSize ? <> · {inr(pf.positionSize, 0)} each</> : null}
            {pf.addCapital ? " · new capital added when cash runs short" : " · buys only what cash covers"}</>
          : <>{pf.holdings.length} of {pf.k} slots used · {pf.freeSlots} free after this week&apos;s sells</>}
        {take.length ? <> · buy at next open: <b>{take.map((b) => b.symbol).join(", ")}</b></> : " · no buy signal to fill them"}.
        From backtest <Link className="text-accent" href={`/backtest?id=${pf.id}`}>#{pf.id}</Link>; entry is the fill at the open after the signal.
      </p>
    </div>
  );
}

function Rule1Card({ r1, week }) {
  const { buys, open, exits, since, rule, portfolio } = r1;
  const block = (title, tone, rows, kind, empty) => (
    <div>
      <div className="flex items-center gap-2 mb-1.5">
        <Badge tone={tone}>{rows.length}</Badge><h3 className="text-sm font-semibold">{title}</h3>
      </div>
      {rows.length ? <div className="-mx-1"><Rule1Table rows={rows} week={week} kind={kind} /></div>
        : <p className="text-sm text-muted">{empty}</p>}
    </div>
  );
  return (
    <Section title="Rule 1 signals"
      right={<Link href={`/scanner?preset=rule1${week ? `&week=${week}` : ""}`} className="text-xs text-accent hover:underline">Rule 1 preset →</Link>}>
      <div className="flex flex-col gap-5">
        {block(rule.maxWeeks > 1 ? `Buy next week's open: first Rule 1 signal this week (week 1–${rule.maxWeeks} of a bullish run)`
          : "Buy next week's open: flipped bullish this week", "up", buys, "buy",
          rule.maxWeeks > 1 ? `No stock in week 1–${rule.maxWeeks} of a bullish run reached score ≥ ${rule.minScore} (with liquidity, price, not ASM/GSM) for the first time this week.`
          : `No stock flipped bullish with score ≥ ${rule.minScore} (and liquidity, price, not ASM/GSM) this week.`)}
        {block(`Sell next week's open: Supertrend turned bearish${rule.exitScore ? ` or score fell below ${rule.exitScore}` : ""}`, "down", exits, "exit",
          `No Rule 1 signal turned bearish${rule.exitScore ? ` or fell below score ${rule.exitScore}` : ""} this week.`)}
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <Badge tone="accent">{portfolio?.holdings?.length ?? 0}</Badge><h3 className="text-sm font-semibold">Model portfolio ({portfolio?.k ? `max ${portfolio.k} positions` : "no position limit"})</h3>
          </div>
          <Portfolio pf={portfolio} buys={buys} />
        </div>
        <details>
          <summary className="text-sm font-semibold cursor-pointer">
            All open signals: {open.length} stocks still bullish since a Rule 1 signal
          </summary>
          <div className="mt-2 -mx-1">{open.length ? <Rule1Table rows={open} week={week} kind="open" /> : <p className="text-sm text-muted">No open signals.</p>}</div>
        </details>
        <p className="text-xs text-muted">
          Rule 1 (backtest default): buy {rule.maxWeeks > 1 ? `within the first ${rule.maxWeeks} weeks of a bullish weekly Supertrend run` : "in the week the weekly Supertrend flips bullish"} when
          the score is ≥ {rule.minScore}, any sector, and hold until it turns bearish{rule.exitScore ? ` or the score falls below ${rule.exitScore}` : ""}. The backtest sizes each buy as a share of equity and
          buys only what its cash covers, best score first. Tracked from the first stored scan ({niceDate(since)}). &quot;Since signal&quot; is measured from the signal week&apos;s close;
          the backtest fills at the next week&apos;s open. Research tool, not investment advice.
        </p>
      </div>
    </Section>
  );
}

export default async function Dashboard({ searchParams }) {
  const sp = await searchParams;
  const run = await resolveRun(sp);
  if (!run) return <NoRuns />;
  const [{ regime, sectors, top, counts }, r1] = await Promise.all([getDashboard(run), getRule1(run)]);
  const week = sp.week;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Week ending {niceDate(run.week)}</h1>
        <span className="text-xs text-muted">{run.stocks_scanned} stocks scanned · {run.qualified_count} pass the strict 8 filters</span>
      </div>

      <RegimeBanner r={regime} />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Bullish (weekly ST)" value={counts.bullish} tone="up"
          sub={`${pct((counts.bullish / (counts.bullish + counts.bearish)) * 100, 0, false)} of universe`} />
        <Stat label="Bearish" value={counts.bearish} tone="down" />
        <Stat label="New bullish flips" value={counts.newBullish} tone="up" sub="flipped this week" />
        <Stat label="New bearish flips" value={counts.newBearish} tone="down" sub="flipped this week" />
      </div>

      <Rule1Card r1={r1} week={week} />

      <div>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold">Top sectors</h2>
          <Link href={`/sectors${week ? `?week=${week}` : ""}`} className="text-xs text-accent hover:underline">All sectors →</Link>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {sectors.map((s) => <SectorCard key={s.industry} s={s} week={week} />)}
        </div>
      </div>

      <Section title="Top A+ / A stocks"
        right={<Link href={`/scanner?preset=best${week ? `&week=${week}` : ""}`} className="text-xs text-accent hover:underline">Best Picks →</Link>}>
        {top.length === 0 ? <Empty>No A+/A grades this week.</Empty> : (
          <div className="overflow-x-auto -m-4">
            <table className="w-full text-sm tabular">
              <thead className="text-xs text-muted text-left">
                <tr className="border-b border-line">
                  {["Symbol", "Sector", "Price", "Grade", "Stock score", "RS", "Weeks", "Stop dist.", "From high", "Strict 8 Filter", "Reasons"].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {top.map((s) => (
                  <tr key={s.symbol} className="border-b border-line last:border-0 hover:bg-panel-2">
                    <td className="px-3 py-2"><StockLink symbol={s.symbol} week={week} /></td>
                    <td className="px-3 py-2 whitespace-nowrap text-muted">
                      <SectorLink industry={s.industry} week={week} /> <span className="text-xs">#{s.sector_rank}</span>
                    </td>
                    <td className="px-3 py-2">{num(s.price)}</td>
                    <td className="px-3 py-2"><GradeBadge grade={s.grade} /></td>
                    <td className="px-3 py-2"><Score v={s.stock_score} /></td>
                    <td className="px-3 py-2">{s.rs_rating}</td>
                    <td className="px-3 py-2">{s.weeks_in_trend}</td>
                    <td className="px-3 py-2">{pct(s.pct_from_st, 1, false)}</td>
                    <td className="px-3 py-2">{pct(s.pct_from_high, 1, false)}</td>
                    <td className="px-3 py-2">{s.qualified ? <Badge tone="up">Yes</Badge> : <Badge>No</Badge>}</td>
                    <td className="px-3 py-2 text-xs text-muted min-w-[280px]">{s.reasons}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      {run.notes ? <p className="text-xs text-muted">Run notes: {run.notes}</p> : null}
    </div>
  );
}

