// Scanner columns in the exact published order. Mirrors scanner/engine/export.py.
// type: text | num | pct | int | date | bool ; d = decimals for display

export const BASE_COLUMNS = [
  { key: "symbol", header: "Symbol", type: "text" },
  { key: "price", header: "Price", type: "num" },
  { key: "direction", header: "Direction", type: "text" },
  { key: "weeks_in_trend", header: "Weeks in Trend", type: "int" },
  { key: "flip_date", header: "Flip Date", type: "date" },
  { key: "flip_price", header: "Flip Price", type: "num" },
  { key: "pct_since_flip", header: "% Since Flip", type: "pct" },
  { key: "high_52w", header: "52W High", type: "num" },
  { key: "low_52w", header: "52W Low", type: "num" },
  { key: "pct_from_high", header: "% from High", type: "pct", unsigned: true },
  { key: "sma50", header: "SMA50", type: "num" },
  { key: "sma150", header: "SMA150", type: "num" },
  { key: "sma200", header: "SMA200", type: "num" },
  { key: "ret_1m", header: "1M%", type: "pct" },
  { key: "ret_3m", header: "3M%", type: "pct" },
  { key: "avg_vol_20d", header: "Avg Vol (20d)", type: "int" },
  { key: "market_cap_cr", header: "Market Cap (Cr)", type: "num", d: 0 },
  { key: "industry", header: "Sector", type: "text" },
  { key: "index_list", header: "Index", type: "text" },
];

export const EXTENDED_COLUMNS = [
  { key: "rs_rating", header: "RS Rating", type: "int" },
  { key: "stock_score", header: "Stock Score", type: "num", d: 1 },
  { key: "grade", header: "Grade", type: "text" },
  { key: "sector_rank", header: "Sector Rank", type: "int" },
  { key: "sector_score", header: "Sector Score", type: "num", d: 1 },
  { key: "st_value", header: "ST Value (Stop)", type: "num" },
  { key: "risk_pct", header: "Risk %", type: "pct", unsigned: true },
  { key: "atr_pct", header: "ATR%", type: "pct", unsigned: true },
  { key: "vol_ratio", header: "Vol Ratio", type: "num" },
  { key: "delivery_pct_20d", header: "Delivery %", type: "pct", unsigned: true },
  { key: "sma100", header: "SMA100", type: "num" },
  { key: "ret_6m", header: "6M%", type: "pct" },
  { key: "ret_12m", header: "12M%", type: "pct" },
  { key: "qualified", header: "Strict 8 Filter", type: "bool" },
  { key: "reasons", header: "Reasons", type: "text" },
];

// Extra fields the scanner needs for filtering / badges (not exported).
export const FILTER_FIELDS = [
  "symbol_id", "name", "sector_tradeable", "is_new_flip", "avg_turnover_20d_cr", "stock_score", "grade",
  "qualified", "sma50", "sma150", "sma200", "rs_rating", "f_liquidity", "f_price", "f_not_asm",
];

export const SCANNER_FIELDS = [
  ...new Set([...BASE_COLUMNS, ...EXTENDED_COLUMNS].map((c) => c.key).concat(FILTER_FIELDS)),
];

export function columnsFor(extended) {
  return extended ? [...BASE_COLUMNS, ...EXTENDED_COLUMNS] : BASE_COLUMNS;
}

// Table rendering types for the scanner columns (links, badges, colour-coded %).
const TABLE_TYPE = {
  symbol: "symbol", direction: "direction", industry: "sector", index_list: "index", grade: "grade",
  reasons: "reasons", qualified: "bool",
};

export function tableColumns(extended, { lead = [], omit = [] } = {}) {
  const cols = columnsFor(extended).filter((c) => !omit.includes(c.key))
    .map((c) => ({ ...c, type: TABLE_TYPE[c.key] ?? c.type }));
  // Optional leading columns (e.g. score/grade first on sector pages)
  const leadCols = lead.map((k) => cols.find((c) => c.key === k)).filter(Boolean);
  return [cols[0], ...leadCols, ...cols.slice(1).filter((c) => !lead.includes(c.key))];
}
