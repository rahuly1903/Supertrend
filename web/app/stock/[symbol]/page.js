import { notFound } from "next/navigation";
import { DailyChart, WeeklyChart } from "@/components/charts/StockCharts";
import {
  Badge, DirectionBadge, GradeBadge, Pct, Section, SectorLink, Stat,
} from "@/components/ui";
import { getStock, resolveRun } from "@/lib/data";
import { RULE1 } from "@/lib/filters";
import { compact, date, inr, niceDate, num, pct } from "@/lib/format";

export async function generateMetadata({ params }) {
  return { title: decodeURIComponent((await params).symbol) };
}

const RULE1_BADGE = {
  buy: <Badge tone="up">Rule 1 buy</Badge>,
  hold: <Badge tone="accent">Rule 1 hold</Badge>,
  sell: <Badge tone="down">Rule 1 sell</Badge>,
};
const RULE1_CELL = {
  buy: <Badge tone="up">Buy</Badge>,
  hold: <span className="text-accent">Hold</span>,
  sell: <Badge tone="down">Sell</Badge>,
};

const COMPONENTS = [
  ["rs", "RS rating", "score_rs"], ["sector", "Sector strength", "score_sector"],
  ["freshness", "Trend freshness", "score_freshness"], ["near_high", "Near 52W high", "score_near_high"],
  ["volume", "Volume confirmation", "score_volume"], ["tightness", "Tightness (VCP)", "score_tightness"],
  ["risk", "Risk (distance to stop)", "score_risk"],
];

function Check({ ok, label, detail }) {
  return (
    <li className="flex items-start gap-2 py-1">
      <span className={`mt-0.5 text-xs font-bold ${ok ? "text-up" : "text-down"}`}>{ok ? "✓" : "✗"}</span>
      <span className="flex-1">{label}</span>
      <span className="text-xs text-muted tabular text-right">{detail}</span>
    </li>
  );
}

function ScoreBreakdown({ s, weights }) {
  return (
    <div className="flex flex-col gap-2">
      {COMPONENTS.map(([k, label, col]) => {
        const v = s[col] ?? 0;
        const w = weights?.[k] ?? 0;
        return (
          <div key={k} className="text-sm">
            <div className="flex justify-between text-xs mb-0.5">
              <span>{label} <span className="text-muted">× {num(w * 100, 0)}%</span></span>
              <span className="tabular"><span className="text-muted">{num(v, 0)} →</span> <b>{num(v * w, 1)}</b></span>
            </div>
            <div className="h-1.5 bg-panel-2 rounded"><div className="h-1.5 rounded bg-accent" style={{ width: `${Math.min(v, 100)}%` }} /></div>
          </div>
        );
      })}
      <div className="flex justify-between text-sm border-t border-line pt-2 mt-1 tabular">
        <span>Penalties</span><b className={s.penalty ? "text-down" : ""}>−{num(s.penalty ?? 0, 1)}</b>
      </div>
      <div className="flex justify-between text-base tabular">
        <span className="font-semibold">Stock score</span>
        <span className="flex items-center gap-2"><b>{num(s.stock_score, 1)}</b> <GradeBadge grade={s.grade} /></span>
      </div>
    </div>
  );
}

export default async function StockPage({ params, searchParams }) {
  const [{ symbol: raw }, sp] = await Promise.all([params, searchParams]);
  const run = await resolveRun(sp);
  const data = await getStock(decodeURIComponent(raw).toUpperCase(), run);
  if (!data) notFound();
  const { symbol: sym, indices, scan: s, weekly, supertrend, daily, history, config } = data;
  const f = config.filters ?? {};
  const pen = config.penalties?.points ?? {};

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="text-xs text-muted">
            {sym.industry ? <SectorLink industry={sym.industry} week={sp.week} /> : "—"}
            {s ? <> · sector rank #{s.sector_rank}</> : null} · ISIN {sym.isin}
          </div>
          <h1 className="text-2xl font-semibold">{sym.symbol} <span className="text-base font-normal text-muted">{sym.name}</span></h1>
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {s ? <DirectionBadge direction={s.direction} isNew={s.is_new_flip} /> : null}
            {RULE1_BADGE[history[0]?.week_end_date === s?.week_end_date ? history[0]?.rule1 : null] ?? null}
            {s?.qualified ? <Badge tone="up">Strict 8 Filter ✓</Badge> : null}
            {s?.in_asm_gsm ? <Badge tone="down">ASM/GSM</Badge> : null}
            {!sym.is_active ? <Badge tone="warn">Not in the universe</Badge> : null}
            {indices.map((i) => <Badge key={i.name}>{i.name}</Badge>)}
          </div>
        </div>
        {s ? (
          <div className="text-right">
            <div className="text-3xl font-semibold tabular">{inr(s.price)}</div>
            <div className="text-xs text-muted">close, week ending {niceDate(s.week_end_date)}</div>
          </div>
        ) : null}
      </div>

      {s ? (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 xl:grid-cols-9 gap-3">
          <Stat label="Weekly ST" value={num(s.st_value)} sub={`${pct(s.pct_from_st, 1)} from line`} />
          <Stat label="Weeks in trend" value={s.weeks_in_trend} sub={`flip ${date(s.flip_date)} @ ${num(s.flip_price)}`} />
          <Stat label="Since flip" value={<Pct v={s.pct_since_flip} />} />
          <Stat label="Stock score" value={num(s.stock_score, 1)}
            tone={s.stock_score >= RULE1.minScore ? "up" : RULE1.exitScore && s.stock_score < RULE1.exitScore ? "down" : undefined}
            sub={`grade ${s.grade ?? "—"} · buy ≥${RULE1.minScore}${RULE1.exitScore ? ` · exit <${RULE1.exitScore}` : ""}`} />
          <Stat label="RS rating" value={s.rs_rating ?? "—"} sub={<>vs sector <Pct v={s.rs_vs_sector} /></>} />
          <Stat label="52W high" value={num(s.high_52w)} sub={`${pct(s.pct_from_high, 1, false)} below`} />
          <Stat label="3M / 12M" value={<Pct v={s.ret_3m} />} sub={<>12M <Pct v={s.ret_12m} /></>} />
          <Stat label="Turnover 20d" value={`₹${num(s.avg_turnover_20d_cr, 1)} Cr`} sub={`mcap ₹${num(s.market_cap_cr, 0)} Cr`} />
          <Stat label="ATR% / Delivery" value={pct(s.atr_pct, 1, false)} sub={`delivery ${pct(s.delivery_pct_20d, 0, false)}`} />
        </div>
      ) : <div className="card p-4 text-sm text-muted">Not in the selected week&apos;s scan.</div>}

      <Section title="Daily candles · SMA 50 / 100 / 150 / 200">
        <DailyChart daily={daily} />
      </Section>

      <Section title={`Weekly candles · Supertrend (${run?.supertrend ?? "7, 3"}) · flips`}>
        <WeeklyChart weekly={weekly} supertrend={supertrend} />
      </Section>

      {s ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <Section title="Score breakdown"><ScoreBreakdown s={s} weights={config.scoring?.weights} /></Section>

          <Section title="Filters & signals">
            <ul className="text-sm divide-y divide-line">
              <Check ok={s.f_supertrend} label="Weekly Supertrend bullish" detail={s.direction} />
              <Check ok={s.f_sector} label="Sector tradeable" detail={`rank #${s.sector_rank}`} />
              <Check ok={s.f_trend_template} label="Trend template (C > 50 > 150 > 200, 200 rising)"
                detail={`${num(s.sma50, 0)} / ${num(s.sma150, 0)} / ${num(s.sma200, 0)}`} />
              <Check ok={s.f_52w_range} label={`≥ ${f.min_above_low_pct ?? 30}% above low, ≤ ${f.max_from_high_pct ?? 25}% from high`}
                detail={`${pct(s.pct_above_low, 0, false)} / ${pct(s.pct_from_high, 0, false)}`} />
              <Check ok={s.f_rs} label={`RS rating ≥ ${f.min_rs_rating ?? 70}`} detail={s.rs_rating} />
              <Check ok={s.f_liquidity} label={`Turnover ≥ ₹${f.min_turnover_cr ?? 10} Cr`} detail={`₹${num(s.avg_turnover_20d_cr, 1)} Cr`} />
              <Check ok={s.f_price} label={`Price ≥ ₹${f.min_price ?? 50}`} detail={num(s.price)} />
              <Check ok={s.f_not_asm} label="Not on ASM/GSM" detail={s.in_asm_gsm ? "listed" : "clear"} />
            </ul>
            <div className="flex flex-wrap gap-1.5 mt-3">
              {[["sig_fresh_flip", "Fresh flip"], ["sig_tight_stop", "Tight stop"], ["sig_vol_confirm", "Volume"],
                ["sig_vcp", "VCP"], ["sig_accumulation", "Accumulation"], ["sig_near_high", "Near high"],
                ["sig_sector_leader", "Sector leader"]].map(([k, l]) => (
                <Badge key={k} tone={s[k] ? "up" : "muted"}>{s[k] ? "✓" : "·"} {l}</Badge>
              ))}
              {[["pen_overextended", "Overextended", pen.overextended], ["pen_high_atr", "High ATR", pen.high_atr],
                ["pen_late_stage", "Late stage", pen.late_stage]].filter(([k]) => s[k]).map(([k, l, p]) => (
                <Badge key={k} tone="down">{l} −{p}</Badge>
              ))}
            </div>
          </Section>

          <Section title="Trade plan">
            {s.direction === "Bullish" ? (
              <dl className="grid grid-cols-2 gap-y-2 text-sm tabular">
                <dt className="text-muted">Entry (last close)</dt><dd className="text-right">{inr(s.price)}</dd>
                <dt className="text-muted">Stop (weekly ST)</dt><dd className="text-right text-down">{inr(s.stop_price)}</dd>
                <dt className="text-muted">Risk per share</dt><dd className="text-right">{inr(s.price - s.stop_price)} ({pct(s.risk_pct, 1, false)})</dd>
                <dt className="text-muted">Position size</dt><dd className="text-right font-semibold">{num(s.position_qty, 0)} shares</dd>
                <dt className="text-muted">Position value</dt><dd className="text-right">{inr(s.position_value, 0)}</dd>
                <dt className="text-muted col-span-2 text-xs pt-2 border-t border-line">
                  Sized so a stop-out loses the configured risk per trade (CAPITAL × RISK_PER_TRADE_PCT),
                  capped per position; halved in a bear regime.
                </dt>
              </dl>
            ) : <p className="text-sm text-muted">Bearish on the weekly Supertrend: no long setup. Stop line at {inr(s.st_value)}.</p>}
            <p className="text-xs text-muted mt-3 border-t border-line pt-2">{s.reasons}</p>
          </Section>
        </div>
      ) : null}

      <Section title={`Weekly history (last ${history.length} scans)`}>
        <div className="flex flex-wrap gap-1 mb-3" aria-label="Direction by week">
          {[...history].reverse().map((h) => (
            <span key={h.week_end_date} title={`${h.week_end_date}: ${h.direction}${h.grade ? ` · ${h.grade}` : ""}`}
              className={`h-5 w-3 rounded-sm flex items-end justify-center ${h.direction === "Bullish" ? "bg-up" : "bg-down"} ${h.qualified ? "ring-2 ring-accent" : "opacity-70"}`}>
              {h.rule1 === "buy" ? <span className="mb-0.5 h-1.5 w-1.5 rounded-full bg-white" /> : null}
            </span>
          ))}
        </div>
        <div className="text-xs text-muted mb-2">Green = bullish, red = bearish, ringed = passes the Strict 8 Filter, dot = Rule 1 buy. Oldest → newest.</div>
        <div className="overflow-x-auto max-h-72">
          <table className="w-full text-xs tabular">
            <thead className="text-muted text-left sticky top-0 bg-panel">
              <tr>{[["Week"], ["Direction"], ["Weeks"], ["Grade"], ["Score"],
                ["Rule 1", `Rule 1 signal: buy = bullish flip with score ≥ ${RULE1.minScore} (tradeable); sell = Supertrend bearish or score < ${RULE1.exitScore}`],
                ["Strict 8 Filter", "Passes all 8 hard scanner filters (see below). Rule 1 does not use it."],
                ["Sector rank"], ["Price"]].map(([h, tip]) => <th key={h} title={tip} className="py-1 pr-3 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.week_end_date} className="border-t border-line">
                  <td className="py-1 pr-3">{h.week_end_date}</td>
                  <td className="py-1 pr-3"><DirectionBadge direction={h.direction} /></td>
                  <td className="py-1 pr-3">{h.weeks_in_trend}</td>
                  <td className="py-1 pr-3"><GradeBadge grade={h.grade} /></td>
                  <td className="py-1 pr-3">{num(h.stock_score, 1)}</td>
                  <td className="py-1 pr-3">{RULE1_CELL[h.rule1] ?? <span className="text-muted">—</span>}</td>
                  <td className="py-1 pr-3">{h.qualified ? "Yes" : "—"}</td>
                  <td className="py-1 pr-3">{h.sector_rank}</td>
                  <td className="py-1 pr-3">{num(h.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid md:grid-cols-2 gap-4 mt-4 text-xs text-muted leading-relaxed">
          <div>
            <div className="font-semibold text-text mb-1">Rule 1: the trading signal</div>
            <ul className="list-disc pl-4 space-y-0.5">
              <li><b>Buy</b> (at the next week&apos;s open): the weekly Supertrend flipped bullish this week
                {RULE1.maxWeeks > 1 ? ` (or within ${RULE1.maxWeeks} weeks)` : ""}, score ≥ {RULE1.minScore}, turnover ≥ ₹10 Cr,
                price ≥ ₹50, not in ASM/GSM. Any sector; no RS or moving-average requirement.</li>
              <li><b>Hold</b>: the signal is still on.</li>
              <li><b>Sell</b> (at the next week&apos;s open): the Supertrend turned bearish
                {RULE1.exitScore ? <> or the score fell below {RULE1.exitScore}</> : null}.</li>
              <li>Size: 10% of equity per stock from the account&apos;s own cash. A signal, not a position: the backtest skips
                a buy when its cash is used up.</li>
            </ul>
          </div>
          <div>
            <div className="font-semibold text-text mb-1">Strict 8 Filter: a quality screen, not a trade signal</div>
            <ol className="list-decimal pl-4 space-y-0.5">
              <li>Weekly Supertrend bullish</li>
              <li>Sector tradeable (top-5 rank, ≥ 60% of its stocks bullish, RS trend &gt; 1, Leading/Improving)</li>
              <li>Trend template: price &gt; SMA50 &gt; SMA150 &gt; SMA200, SMA200 rising</li>
              <li>≥ 30% above the 52-week low and ≤ 25% below the 52-week high</li>
              <li>RS rating ≥ 70</li>
              <li>Turnover ≥ ₹10 Cr</li>
              <li>Price ≥ ₹50</li>
              <li>Not in ASM/GSM</li>
            </ol>
            <p className="mt-1">Stocks usually pass weeks after a flip, once the averages line up. Rule 1 does not require it:
              in the backtest the requirement halved returns, because the early flips it rejects made most of the profit.</p>
          </div>
        </div>
      </Section>
      <p className="text-xs text-muted">Avg volume 20d {compact(s?.avg_vol_20d)} · data through {niceDate(run?.week)}</p>
    </div>
  );
}
