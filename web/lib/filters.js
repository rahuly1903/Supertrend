// Scanner filters: parsed from URL search params, applied identically on the server
// (API, CSV export) and in the browser (interactive table).

// Rule 1 (backtest default): enter the week the weekly Supertrend flips bullish with score >= 70,
// any sector, only the tradeability checks; 10% of equity per stock from own cash. Exit when the
// Supertrend turns bearish or the score falls below 30. Keep in sync with scanner/config.yaml `backtest`.
export const RULE1 = { minScore: 70, maxWeeks: 1, exitScore: 30 };

export const PRESETS = {
  rule1: { label: "Rule 1 buys", params: { dir: "Bullish", wmax: String(RULE1.maxWeeks), minScore: String(RULE1.minScore), tradeable: "1" } },
  best: { label: "Best Picks", params: { qualified: "1", grade: "A+,A" } },
  fresh: { label: "Fresh Flips", params: { dir: "Bullish", fresh: "6" } },
  nearhigh: { label: "Near 52W High", params: { dir: "Bullish", maxFromHigh: "5" } },
  bearflips: { label: "Bearish Flips (exit list)", params: { dir: "Bearish", wmax: "1" } },
};

export const FILTER_KEYS = [
  "q", "dir", "sector", "index", "qualified", "grade", "wmin", "wmax", "maxFromHigh",
  "minMcap", "minTurnover", "fresh", "above", "minScore", "tradeable",
];

const list = (v) => (v ? String(v).split(",").map((s) => s.trim()).filter(Boolean) : []);
const numOrNull = (v) => (v === undefined || v === null || v === "" || Number.isNaN(Number(v)) ? null : Number(v));

/** Normalise a plain object / URLSearchParams into a filter spec (a preset fills in defaults). */
export function parseFilters(input) {
  const get = (k) => (input instanceof URLSearchParams ? input.get(k) : input?.[k]) ?? undefined;
  const preset = PRESETS[get("preset")]?.params ?? {};
  const pick = (k) => get(k) ?? preset[k];
  return {
    q: (pick("q") ?? "").toString().trim().toUpperCase(),
    dir: pick("dir") ?? "",
    sector: list(pick("sector")),
    index: list(pick("index")),
    qualified: pick("qualified") === "1",
    grade: list(pick("grade")),
    wmin: numOrNull(pick("wmin")),
    wmax: numOrNull(pick("wmax")),
    maxFromHigh: numOrNull(pick("maxFromHigh")),
    minMcap: numOrNull(pick("minMcap")),
    minTurnover: numOrNull(pick("minTurnover")),
    fresh: numOrNull(pick("fresh")),
    above: list(pick("above")),
    minScore: numOrNull(pick("minScore")),
    tradeable: pick("tradeable") === "1",
  };
}

/** Liquidity, price and not in ASM/GSM: the only hard filters Rule 1 keeps. */
export const isTradeable = (r) => Boolean(r.f_liquidity && r.f_price && r.f_not_asm);

export const isRule1Entry = (r) => r.direction === "Bullish" && r.weeks_in_trend <= RULE1.maxWeeks
  && r.stock_score >= RULE1.minScore && isTradeable(r);

export function applyFilters(rows, f) {
  return rows.filter((r) => {
    if (f.q && !r.symbol.includes(f.q) && !(r.name ?? "").toUpperCase().includes(f.q)) return false;
    if (f.dir && r.direction !== f.dir) return false;
    if (f.sector.length && !f.sector.includes(r.industry)) return false;
    if (f.index.length) {
      const idx = (r.index_list ?? "").split("|");
      if (!f.index.some((i) => idx.includes(i))) return false;
    }
    if (f.qualified && !r.qualified) return false;
    if (f.grade.length && !f.grade.includes(r.grade)) return false;
    if (f.wmin !== null && !(r.weeks_in_trend >= f.wmin)) return false;
    if (f.wmax !== null && !(r.weeks_in_trend <= f.wmax)) return false;
    if (f.maxFromHigh !== null && !(r.pct_from_high <= f.maxFromHigh)) return false;
    if (f.minMcap !== null && !(r.market_cap_cr >= f.minMcap)) return false;
    if (f.minTurnover !== null && !(r.avg_turnover_20d_cr >= f.minTurnover)) return false;
    if (f.fresh !== null && !(r.direction === "Bullish" && r.weeks_in_trend <= f.fresh)) return false;
    if (f.minScore !== null && !(r.stock_score >= f.minScore)) return false;
    if (f.tradeable && !isTradeable(r)) return false;
    for (const n of f.above) {
      const sma = r[`sma${n}`];
      if (!(sma != null && r.price > sma)) return false;
    }
    return true;
  });
}

/** Default ordering: bullish first, then stock score (descending). */
export function defaultSort(a, b) {
  if (a.direction !== b.direction) return a.direction === "Bullish" ? -1 : 1;
  return (b.stock_score ?? -1) - (a.stock_score ?? -1);
}

export function sortRows(rows, sort, desc) {
  if (!sort) return [...rows].sort(defaultSort);
  const dir = desc ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = a[sort], y = b[sort];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (x > y ? 1 : x < y ? -1 : 0) * dir;
  });
}
