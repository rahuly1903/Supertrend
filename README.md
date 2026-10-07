# Weekly Supertrend Sector-Rotation Scanner (Nifty 500)

Weekly scan of Nifty 500: rank sectors, find weekly-Supertrend-bullish stocks inside
leading sectors, score them.

| Part | Tech | Folder |
|---|---|---|
| Ingestion + calculations | Python 3.12, pandas, numba, psycopg 3 | `scanner/` |
| Storage (single source of truth) | PostgreSQL 16 | Alembic migrations in `scanner/migrations/` |
| UI + read-only API | Next.js App Router (JS), Prisma | `web/` |
| Hosting | Railway: Postgres + Python cron + Next.js | |

## Build status

- [x] 1. DB schema + migrations
- [x] 2. Universe/index loaders + yfinance backfill
- [x] 3. Weekly candles + numba Supertrend
- [x] 4. Indicators, RS, sectors, regime, scoring
- [x] 5. Bhavcopy daily ingest + weekly job + cron
- [x] 6. Next.js pages
- [x] 7. CSV export (web export matches `python main.py export` cell for cell)
- [x] 8. Backtest
- [ ] 9. Telegram alert

## Migrations: who owns the schema

**Python/Alembic owns all migrations.** Migrations are raw SQL (`op.execute`) so BRIN,
partial, and DESC indexes plus CHECK constraints are exactly what we write.

`web/prisma/schema.prisma` is a **read-only mirror**. Never run `prisma migrate` or
`prisma db push`. After adding an Alembic migration, update the Prisma file by hand and
check it against the live DB:

```bash
cd web && npx prisma@6 db pull --print
```

New migration:

```bash
cd scanner && .venv/bin/alembic revision -m "add backtest tables"
```

## Schema overview

| Group | Tables |
|---|---|
| Universe | `symbols` (ISIN unique, handles renames), `symbol_aliases`, `indices`, `index_members`, `index_member_snapshots` (point-in-time membership for backtests), `exclusion_list` (ASM/GSM) |
| Prices | `daily_candles` (PK symbol+date, BRIN on date, sanity CHECK), `weekly_candles`, `benchmark_candles` |
| Supertrend | `weekly_supertrend` (full series: chart overlay + safe re-runs), `supertrend_state` (latest state, O(1) weekly update) |
| Sectors | `sector_index_weekly` (equal-weight index, JdK RS-Ratio/Momentum, breadth history → RRG tails) |
| Snapshots (UI reads these) | `scan_runs`, `stock_scan_results`, `sector_scan_results`, `market_regime` |
| Ops | `ingest_log` (what has been downloaded, so ingestion is incremental), `corporate_actions`, `data_quality_log` |

Design notes:
- Every weekly scan is kept (`scan_runs.week_end_date` UNIQUE). Re-running a week
  deletes and rewrites that run's rows in one transaction, so readers never see a partial week.
- The UI shows the latest run with `status = 'complete'`. A failed run leaves the previous snapshot live.
- `weekly_supertrend` stores every week, so a re-run of week W restarts from W−1's state.
  `supertrend_state` carries the ATR period and multiplier; a config change triggers a full recompute.
- `daily_candles` rows are COPY'd sorted by date, which keeps the BRIN index useful.
- Charts read one symbol's candles by primary key. Everything else reads snapshot tables only.

## Bulk write helpers (`scanner/db.py`)

| Function | Use |
|---|---|
| `copy_frame(conn, table, df)` | Plain `COPY FROM STDIN` (CSV). Coerces ints, bools, dates, JSON, NaN/inf → NULL |
| `upsert_frame(conn, table, df, key_cols, update_cols=None)` | COPY → temp table → one `INSERT … ON CONFLICT DO UPDATE` (or `DO NOTHING` with `update_cols=[]`) |
| `update_from_frame(conn, table, df, key_cols)` | Partial-column bulk `UPDATE … FROM` temp table (upsert can't do this: Postgres checks NOT NULL before `ON CONFLICT`) |
| `replace_rows(conn, table, df, where)` | Idempotent snapshot: `DELETE WHERE …` + COPY in the same transaction |
| `read_frame(conn, sql, params)` | Query → DataFrame |

## Local setup

Needs Python 3.12 and Postgres 16 (local, or Docker).

```bash
cp .env.example .env            # set DATABASE_URL
cd scanner
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
createdb supertrend             # skip if using docker compose
.venv/bin/python main.py migrate
.venv/bin/python main.py db-status
.venv/bin/pytest -q tests
```

Frontend:

```bash
cd web
npm install                  # runs prisma generate
echo "DATABASE_URL=postgresql://$USER@localhost:5432/supertrend" > .env
npm run dev                  # http://localhost:3000
```

With Docker (Postgres exposed on host port 5433; `web` waits for `scanner` to finish migrating):

```bash
docker compose up -d db
docker compose run --rm scanner python main.py migrate
docker compose up --build web   # http://localhost:3000
```

## CLI

Run from `scanner/` with the venv (`.venv/bin/python main.py …`).

| Command | What it does | Schedule |
|---|---|---|
| `migrate` | `alembic upgrade head` | every deploy |
| `db-status` | Current revision + row count per table | |
| `universe [--skip-shares]` | Nifty 500 list → `symbols` (matched by ISIN), all index memberships, shares outstanding | monthly |
| `shares` | Shares outstanding only (yfinance `fast_info`, threaded) | monthly |
| `exclusions` | NSE ASM/GSM lists → `exclusion_list` snapshot for today | weekly |
| `backfill [--years N]` | Benchmarks + daily candles via yfinance. Incremental: only dates after the last stored candle | one-time, safe to re-run |
| `backfill --symbols A,B --full` | Replace one symbol's history (after a split/bonus) | on demand |
| `backfill --years 12 --extend` | Prepend older history before the first stored candle (backtest depth) | one-time |
| `daily [--days N] [--date D]` | NSE bhavcopy → daily candles + delivery %, benchmarks, new-listing history, corporate actions. `--days 35` re-processes a window (delivery backfill) | Mon–Fri 18:45 IST |
| `repair-gaps` | Fill historical missing sessions from bhavcopy, then rebuild the affected weekly candles/Supertrend | after backfill / on demand |
| `weekly --ingest` | **Cron mode**: daily catch-up → ASM/GSM → candles/ST → freshness guard → scan | Saturday 08:00 IST |
| `weekly` | Candles + Supertrend (incremental), then full scan of the latest complete week → snapshot tables | manual |
| `full-rescan [--weeks 52]` | Full candle + Supertrend recompute, then rebuild the last N snapshots | after config changes |
| `weekly --history N` | (Re)build the last N weekly snapshots (sector history charts, RRG tails) | once after backfill |
| `weekly --week DATE` | Re-run the stored week containing DATE (overwrites it atomically) | on demand |
| `backtest [--name N]` | One backtest with the parameters in `config.yaml` (Rule 1, ~80 s over 11 years) | on demand |
| `backtest --sweep [--name N] [--jobs J]` | Grid × variants in `backtest.sweep` + walk-forward (1,440 runs ≈ 5 min, parallel) | on demand |
| `walk-forward --sweep ID` | Redo only the walk-forward of a stored sweep with the current `backtest.walk_forward` | on demand |
| `monte-carlo [--runs N] [--skip-pct P]` | Re-run the default N times with a random signal order (optionally missing P% of signals); saves the p5–p95 runs | on demand |
| `pit-download` / `pit-build` | NSE bhavcopy archive → survivorship-free point-in-time DB (see *Survivorship-free test*) | one-time |
| `export [--week D] [--extended] [-o f.csv]` | Scanner CSV in the exact published column order | |
| `supertrend [--full] [--asof DATE]` | Weekly candles + weekly Supertrend, incremental. `--asof` treats DATE as the last closed session | weekly (part of `weekly` from step 5) |


### First-time load

```bash
cd scanner
.venv/bin/python main.py migrate
.venv/bin/python main.py universe      # ~45 s  (28 index CSVs, 1 s throttle each + share counts)
.venv/bin/python main.py exclusions    # ~2 s
.venv/bin/python main.py backfill      # ~50 s  (500 symbols x 3 years = ~350k rows)
.venv/bin/python main.py supertrend    # ~4 s   (75k weekly candles + Supertrend, full build)
.venv/bin/python main.py daily --days 35      # ~30 s  delivery % for the 20-day averages
.venv/bin/python main.py repair-gaps          # ~90 s  fill Yahoo outage days from bhavcopy
.venv/bin/python main.py weekly --history 78  # ~25 s  78 weekly snapshots
# for backtesting: 12 years of history (~60 s download, ~810k more rows), then rebuild weekly + ST
.venv/bin/python main.py backfill --years 12 --extend
.venv/bin/python main.py supertrend --full
```

## Data sources

Adapters live in `scanner/sources/`. Prices come through the `PriceSource` protocol
(`sources/base.py`); to move to Upstox or Kite, implement `download_daily`,
`download_index` and `shares_outstanding` and pass it to `run_backfill(source=...)`.

| Source | Module | Notes |
|---|---|---|
| niftyindices.com constituent CSVs | `sources/nse_lists.py` | Header check rejects HTML error pages. A failed index keeps its previous membership |
| NSE `api/reportASM`, `api/reportGSM` | `sources/nse_lists.py` | `NseClient` warms cookies from the homepage (403 is fine) and re-warms on 401/403 |
| yfinance | `sources/yfinance_src.py` | Batches of 100, threaded, one retry pass for tickers that came back empty |

All HTTP goes through `sources/http.py`: browser User-Agent, per-client throttle,
exponential backoff with jitter on connection errors, timeouts, 429 and 5xx.

### Symbol lifecycle

- **Identity is ISIN.** A ticker rename updates `symbols.symbol` and records the old ticker in `symbol_aliases`.
- A stock leaving Nifty 500 gets `is_active = false`. Its history is kept.
- New listings (first candle well after the backfill start) get `listed_date`; indicators needing 200 days stay NULL for them.
- NSE placeholder tickers (`DUMMY*`, used during demergers) are skipped (`universe.exclude_symbol_regex`).

### Data validation (`sources/validate.py`)

Every downloaded batch is checked before it is written. Issues go to `data_quality_log`.

| Check | Action |
|---|---|
| Missing or non-positive O/H/L/C | row dropped (`bad_price`) |
| **Phantom candles**: volume 0 and O = H = L = C | row dropped, one log row per date (`phantom_candle`). Yahoo emits these for NSE holidays and outage days |
| High/low not covering open/close | clamped; logged if off by > 0.5% (`ohlc_inconsistent`) |
| Negative volume | set NULL |
| Missing sessions between a symbol's first and last candle | logged per symbol (`missing_days`) |

The trading calendar for gap detection is the stored **NIFTY50** benchmark series
(`data.calendar_benchmark`); stock-level Yahoo data is unreliable on holidays.

Known Yahoo gaps in the current 3-year window: 2025-03-18 (≈470 symbols) and 2024-01-15
(≈95 symbols). They get repaired in step 5 from NSE bhavcopy, scaled by
`stored close(D−1) / bhav PREV_CLOSE(D)`, so the fill matches the adjusted history.

```sql
-- inspect data issues
SELECT issue, count(*) FROM data_quality_log GROUP BY 1;
SELECT d, count(*) FROM (SELECT jsonb_array_elements_text(details->'dates') d
  FROM data_quality_log WHERE issue = 'missing_days') x GROUP BY 1 ORDER BY 2 DESC;
```

### Price adjustment

`data.yf_auto_adjust: true` (as specified) gives split- **and dividend-**adjusted history.
Bhavcopy rows appended later are raw, so they join continuously at the backfill date;
future dividends leave a small (~1%) step. Set it to `false` for split-only adjustment,
which matches TradingView's default charts exactly. Splits and bonuses are caught by
the corporate-action check (step 5), which re-runs `backfill --symbols X --full`.
Daily yfinance rows are inserted with `ON CONFLICT DO NOTHING`, so they never overwrite
bhavcopy rows (those carry delivery %).

## Weekly candles (`engine/weekly.py`)

- Weeks are **Monday-anchored**; `week_end_date` is the last trading day of the week
  (Thursday when Friday is a holiday). For Mon–Fri sessions this is identical to pandas
  `W-FRI`. Weekend special sessions (Budget Saturday, Muhurat) stay in their own week, as
  on TradingView. `W-FRI` would push a Saturday into the following week.
- A week is **complete** once its Friday session has closed (`data.candle_ready_ist`).
  The current week is never stored.
- Incremental runs rebuild the last `weekly.rebuild_weeks` (3) weeks from daily data and
  rewrite only symbols whose candles changed. A late correction inside that window is
  picked up automatically. Older corrections need `supertrend --full`.

## Weekly Supertrend (`engine/supertrend.py`)

Exact port of TradingView Pine v5 `ta.supertrend(3, 10)`:
- ATR is `ta.rma(ta.tr(true), 10)`: Wilder's RMA, seeded with the SMA of the first 10 true ranges.
- Bands ratchet (`nz(band[1])` semantics). The first valid bar is bearish, as in Pine.

**Direction:** `1` = Bullish, `-1` = Bearish. This is the opposite sign to Pine's `direction` output.

**One numba kernel for all symbols.** It takes a per-symbol initial state, so a full
build and a weekly update run the same code and produce identical numbers.

Each run routes every symbol (`jobs/weekly_scan.plan_supertrend`):

| Situation | Action |
|---|---|
| New weekly candle(s) after the state week | **incremental**: continue from `supertrend_state`, O(1) per new week |
| A week at or before the state week changed (bhavcopy repair, corporate action) | full recompute for that symbol |
| No state, state still in warm-up, or `atr_period`/`multiplier` changed in config | full recompute for that symbol |
| Nothing new | nothing written (re-runs are no-ops) |

State carries `flip_date` (week of the last direction change), `flip_price` (close
of that week), and `weeks_in_trend` (the flip week counts as 1). Symbols that have not
flipped since their data starts have `flip_date = NULL`.

Checked on real data: building to 2026-09-11 and then rolling forward week by week gives
0 differences from a full build, in both state and series.

### Tests

- `tests/test_supertrend.py`:
  - **Oracle:** an independent, line-by-line Python transcription of Pine
    `ta.tr` / `ta.rma` / `ta.supertrend`. The kernel matches it to 1e-12 for (10,3), (10,2), (7,3) and (3,1).
  - **Hand check:** ATR seed and RMA recursion computed by hand.
  - **Consistency:** symbols are computed independently; incremental equals full; flip tracking is correct.
- **TradingView fixture** (optional): add real TradingView readings to
  `tests/fixtures/tradingview_supertrend.csv` (`symbol,week_end_date,st_value,direction`)
  and the test runs against the DB. Because TradingView charts are split-adjusted only,
  compare with `data.yf_auto_adjust: false` data.
- `tests/test_weekly.py`: aggregation, holiday Friday, Budget Saturday, incomplete-week cutoff, change detection, routing.

## Weekly scan (`engine/scan.py`)

`Scanner` builds every indicator once, as wide panels (rows = sessions or week Mondays,
columns = symbols). All windows look backwards, so a scan of week W just reads panel rows
at W. The cross-sectional ranks (RS Rating, sector ranks, percentiles) are then computed
for that week. `tests/test_scan.py::test_no_lookahead` checks that scanning W with all
data equals scanning with data truncated at W.

Timings on the full universe (500 stocks, 3 years):

| Stage | Time |
|---|---|
| Weekly candles + Supertrend (incremental, nothing new) | 0.15 s |
| Load inputs (one COPY per table) | 1.0 s |
| Build panels | 0.7 s |
| Scan one week | 0.14 s |
| Write snapshot (one transaction) | 0.2 s |
| **Total `weekly`** | **≈ 3.5 s** (target < 30 s) |

### Indicators (`engine/indicators.py`)

| Field | Definition |
|---|---|
| SMA 50/100/150/200 | Simple mean of daily close. NULL until the stock has N sessions (new listings) |
| SMA200 rising | SMA200 today > SMA200 `filters.sma200_rising_days` (20) sessions ago |
| 52W High / Low | Max high / min low over 252 sessions |
| 1M / 3M / 6M / 9M / 12M % | Close vs 21 / 63 / 126 / 189 / 252 sessions ago |
| 1W % | Weekly close vs previous weekly close |
| Avg Vol / Turnover 20d | 20-session mean volume; mean(close × volume) / 1e7 (₹ Cr) |
| Vol Ratio | This week's volume / mean of the previous 20 weeks |
| Flip-week vol ratio | The same ratio, measured at the Supertrend flip week |
| ATR% | Wilder ATR(14) / close |
| Tightness ratio (VCP) | Mean weekly range of the last 3 weeks / the 3 weeks before |
| Delivery % | 20-session mean and 5-session mean (filled by bhavcopy from step 5) |
| Market Cap (Cr) | close × shares_outstanding / 1e7 |

**Gaps:** daily closes are forward-filled up to `indicators.ffill_limit_days` (5), which
bridges Yahoo outage days. A stock with no weekly candle for `indicators.stale_weeks` is
left out of that week's scan.

### Relative strength (`engine/relative_strength.py`)

- **RS Rating:** `rs_raw = 0.4·3M + 0.2·6M + 0.2·9M + 0.2·12M`, then the percentile across
  the universe, mapped to 1–99. New listings use the horizons they have (weights renormalised);
  3M is required.
- **RS vs Nifty 500:** `(stock/benchmark) / SMA10w(stock/benchmark)`, weekly.
- **RS vs sector:** the stock's 13W return minus its sector's 13W return.

### Sectors (`engine/sectors.py`)

- **Equal-weight sector index:** the mean daily return of constituents (clipped to ±50%/day), compounded from 100.
- **RRG (JdK approximation, weekly):**
  - `rs = 100·sector/Nifty500`
  - `RS-Ratio = 100 + zscore(rs, 10)`
  - `RS-Momentum = 100 + zscore(100·RS-Ratio/RS-Ratio[1], 10)`
  - Quadrants: Leading ≥100/≥100, Weakening ≥100/<100, Lagging <100/<100, Improving <100/≥100.
- **Breadth:** % weekly ST bullish, % above SMA50 / SMA200, % within 10% of the 52W high, and ST breadth change vs 4 weeks ago.
- **Score:** `0.30·rank(ret_13w) + 0.30·rank(rs_trend) + 0.20·rank(st_breadth) + 0.20·rank(ret_26w/vol_26w)`, each a 0–100 percentile across sectors.
- **Tradeable:** rank ≤ 5, ST breadth ≥ 60%, RS trend > 1, quadrant Leading or Improving, and ≥ 3 stocks.
- **History:** `sector_index_weekly` holds the full index, RS and breadth history for charts and RRG tails.

### Market regime (`engine/regime.py`)

| Regime | Rule |
|---|---|
| bull | Nifty 500 > SMA200, SMA200 rising, and market ST breadth ≥ 50% |
| bear | Nifty 500 < SMA200, and (SMA200 falling or breadth < 50%) |
| neutral | anything else |

In a bear regime, position sizes are multiplied by `regime.bear_size_factor` (0.5) and each
bullish stock's reasons get "Bear regime: reduce size".

### Stock scoring (`engine/scoring.py`)

- **Strict 8 Filter** (column `qualified`; shown as "Qualified" before 2026-10-05) = every hard filter passes: weekly ST bullish, sector tradeable, trend template
  (close > SMA50 > SMA150 > SMA200, SMA200 rising), ≥ 30% above the 52W low and ≤ 25% below
  the 52W high, RS ≥ 70, turnover ≥ ₹10 Cr, price ≥ ₹50, not on ASM/GSM.
- **Score components** (each 0–100, weighted per `scoring.weights`):

  | Component | How it scores |
  |---|---|
  | RS | percentile of RS Rating |
  | Sector | from sector rank |
  | Freshness | 100 for weeks 1–6, then −6 per week; 0 if bearish |
  | Near high | percentile of −% from high |
  | Volume | linear from 0.8× (0) to 2× (100), using max(this week, flip week while fresh) |
  | Tightness | 70% range contraction + 30% ATR% falling |
  | Risk | 100 at ≤ 3% to stop, 0 at ≥ 15% |

- **Penalties:** overextended (> 25% above SMA50 or > 40% since flip) −10; ATR% in the top
  10% −5; more than 20 weeks in trend −10.
- **Grade** (bullish only): A+ ≥ 80, A ≥ 70, B ≥ 60, else Watch. Bearish stocks have grade NULL.
- **Reasons:** signals hit, penalties, then `✗` followed by the failed filters,
  e.g. `Fresh flip 5w, Near high 2.8%, Vol 5.6x, VCP, Sector leader, ✗ Sector`.
- **Trade plan:** stop = weekly ST value; risk % = (close − stop) / close;
  qty = floor(capital × risk% / (close − stop)), capped at `trade_plan.max_position_pct` of
  capital. `CAPITAL` and `RISK_PER_TRADE_PCT` come from `.env`.

### Run bookkeeping

- A new week gets a `scan_runs` row with status `running`, then `complete`. On error it becomes
  `failed` with the traceback in `error`. The UI shows the latest `complete` run, so the
  previous week stays live.
- Re-running a week that is already `complete` keeps it `complete` throughout. The new
  snapshot replaces the old one in one transaction; if it fails, the old snapshot and status
  stay and only `error` is set.
- `scan_runs.config` stores the exact config used, and `timings` the per-stage milliseconds.

### Tuning (`scanner/config.yaml`)

Every threshold and weight above is a key in `config.yaml` (sections `indicators`,
`relative_strength`, `sectors`, `regime`, `filters`, `signals`, `penalties`, `scoring`,
`trade_plan`). Unknown keys fail at startup. After editing, re-run
`weekly --history N` to recompute past snapshots with the new settings.
Changing `supertrend.atr_period` or `supertrend.multiplier` triggers a full Supertrend recompute on the next run.

**Survivorship:** historical snapshots use today's Nifty 500 list and industries.
`index_member_snapshots` starts recording point-in-time membership from the first
`universe` run; the backtest (step 8) flags this.

## Daily ingest (`jobs/daily_ingest.py`, `sources/bhavcopy.py`)

Source: `nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`. It has
OHLC, `PREV_CLOSE`, volume and delivery. Series EQ, then BE, then BZ are accepted
(`daily.series`); delivery is NULL for BE (`-` in the file).

**Bhavcopy quirks handled:**
- On a holiday the archive returns the **previous** session's file with HTTP 200. `DATE1` is
  checked against the requested date, and a mismatch is logged as a `holiday`.
- Today's file appears around 18:00–19:00 IST. The job polls every `daily.poll_minutes`, for up to `daily.wait_minutes`.
- Missed days are caught up automatically: up to `daily.max_catchup_days` since the last ingested date.

For each date D and symbol:

| Case | Action |
|---|---|
| D after the symbol's last candle | **append** the raw row |
| Candle for D already exists | **update delivery columns only**. Stored prices are never rewritten, so adjusted (yfinance) and raw (bhav) prices never mix inside history |
| D inside a gap | **repair**: insert the row scaled by `stored close(D−1) / PREV_CLOSE(D)`, so it lines up with the adjusted history |
| Symbol has no history yet | skipped; its 3-year history comes from yfinance first, in the same job |

### Corporate actions

NSE adjusts `PREV_CLOSE` on split/bonus ex-dates. When a row is appended,
`stored close(D−1) / PREV_CLOSE(D)` is checked. If it differs from 1 by more than
`daily.corp_action_threshold` (3%), a `corporate_actions` row is recorded. Dividend-sized
steps are ignored.

Resolution runs in the same job, and again on every later run until it succeeds:
1. Re-download the symbol's history **before** the ex-date from yfinance. Yahoo has
   usually adjusted it by then.
2. Accept it only if the join is now continuous. Delivery % is kept.
3. Replace the pre-ex-date rows and rebuild that symbol's weekly candles and Supertrend.

Tested end to end by faking a 1:2 split on TCS: it was detected (ratio 2.000), resolved, 737 rows were replaced, and the history came back intact.

### Gap repair

`repair-gaps` looks for sessions missing between each symbol's first and last candle, using the
NIFTY50 calendar. It repairs them from bhavcopy in date order, then rebuilds weekly candles and
Supertrend for the affected symbols.

First run on the current data: **665 → 87 gaps**. The 87 left are:
- **FORCEMOT, 75 sessions (Oct 2023–Feb 2024):** FORCEMOT is absent from NSE's own bhavcopy for those dates as well.
- **About 12 renamed tickers on the 2024-01-15 / 2025-03-18 outage days** (e.g. ETERNAL was ZOMATO then). The bhavcopy has no ISIN to match them by.

### Freshness guard (`weekly --ingest`)

Before scanning the latest week, every session in it must have candles for at least
`daily.min_coverage` (90%) of active symbols. A weekday counts as a holiday if bhavcopy said
so, or if the NIFTY50 calendar skips it. If the check fails, the week's run is marked
`failed` with the missing sessions listed, and the previous snapshot stays live. Historical
re-runs (`--history`, `--week`) skip the guard.

## Deploying to Railway

One Postgres plus three **cron services** built from the same `scanner/Dockerfile`. Each one
runs `migrate`, then its job, and exits. Railway cron schedules are **UTC**, and a run is
skipped if the previous one is still going.

| Service | Config file (absolute path) | Schedule (UTC) | IST | Command |
|---|---|---|---|---|
| scanner-daily | `/scanner/railway/daily.json` | `15 13 * * 1-5` | Mon–Fri 18:45 | `python main.py daily` |
| scanner-weekly | `/scanner/railway/weekly.json` | `30 2 * * 6` | Sat 08:00 | `python main.py weekly --ingest` |
| scanner-monthly | `/scanner/railway/monthly.json` | `30 1 1 * *` | 1st, 07:00 | `python main.py universe` |

Setup:
1. Create a project, then add **PostgreSQL**.
2. For each of the three services:
   - **New Service → GitHub repo**.
   - **Settings → Root Directory** = `/scanner`.
   - **Config-as-code file** = the absolute path from the table above (Railway does not resolve it relative to the root directory).
3. Service variables: `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `CAPITAL`, `RISK_PER_TRADE_PCT`, and optionally `TELEGRAM_*`.
4. One-off initial load: from a local shell with `DATABASE_URL` pointed at Railway's public Postgres URL, run the "First-time load" commands above.

A failed job exits non-zero, so Railway marks the run failed. Run history and errors are also kept in `scan_runs` and `ingest_log`.

## Frontend (`web/`)

Stack:
- Next.js 16 (App Router, JavaScript) and React 19.
- Prisma 6 (`prisma-client-js`; Prisma 7+ generates TypeScript-only clients).
- Tailwind 4, TanStack Table 8, and TradingView Lightweight Charts 5.

Dark mode is the default; ◐ toggles it, and the choice is remembered.

Every page has a **week selector**. It defaults to the latest `complete` run, and `?week=YYYY-MM-DD` pins
a week (links keep it). Pages render on the server from the snapshot tables. Only the stock
charts read candles, and only for one symbol, by primary key.

| Page | Contents |
|---|---|
| `/` Dashboard | Regime banner (Nifty 500 vs SMA200, slope, ST breadth, % above SMA200), bullish/bearish counts, new flips both ways, **Rule 1 signals** (this week's buys = bullish flips with score ≥ 70 that pass the tradeability checks; sells = earlier signals whose Supertrend turned bearish; the model portfolio of the latest Rule 1 backtest, at most 10 positions, with hold/sell actions and which buys fill the free slots; all open signals, collapsed), top-5 sector cards, top 10 A+/A stocks |
| `/sectors` | Sortable leaderboard (every sector metric), **RRG** with 4-week tails, sector score history (26 runs, pick sectors), returns heatmap |
| `/sectors/[industry]` | Sector KPIs, equal-weight index vs Nifty 500 (rebased to 100), all stocks with scanner columns (bullish first, by score) |
| `/scanner` | The exact output columns, plus an extended toggle. Filters: direction, sector, index, qualified, grade, weeks in trend, min score, tradeable only (liquidity, price, not ASM/GSM), fresh flips, % from high, market cap, turnover, price > SMA 50/150/200, search. Presets: **Rule 1 buys**, Best Picks, Fresh Flips, Near 52W High, Bearish Flips. Sticky header and first column, column sort, **Export CSV**. All state lives in the URL, so links are shareable |
| `/stock/[symbol]` | Weekly candles + Supertrend (broken at flips, Buy/Exit markers) + volume; daily candles + SMA 50/100/150/200; score breakdown using the run's own weights; filter checklist; signals/penalties; trade plan; index memberships; 52-week direction history |
| `/runs` | Every run with status, duration, stage timings and errors; diff vs the previous week: new qualified entries, exits (turned bearish / sector dropped out / named failed filter), new bullish and bearish flips |

### API (read-only, JSON)

All endpoints accept `?week=YYYY-MM-DD` or `?run=<id>`; without either they use the latest complete run.

| Endpoint | Returns |
|---|---|
| `GET /api/runs` | All runs with status, timings, counts |
| `GET /api/sectors` | Sector rows, RRG tails, 26-week score history |
| `GET /api/sectors/{industry}` | Sector row, its stocks, weekly index vs benchmark |
| `GET /api/scanner` | Filtered, sorted, paginated stocks. Takes the scanner page's filter params plus `preset`, `sort`, `desc=1`, `page`, `pageSize` (≤ 500) |
| `GET /api/stock/{symbol}` | Symbol, index memberships, scan row, weekly candles + Supertrend, daily candles + SMAs, history |
| `GET /api/export.csv` | Scanner CSV with the same filters; `ext=1` appends the extended columns |

Example: `/api/scanner?preset=best&sort=rs_rating&desc=1` or `/api/export.csv?week=2026-09-25&dir=Bullish&sector=Healthcare&ext=1`.

**Caching:**
- **Browser/CDN:** a response pinned to a week is served with `s-maxage=86400`; "latest" gets `s-maxage=300`.
- **In-process:** the server also memoises snapshot reads, keyed by run id and `finished_at`, so re-running a week invalidates them automatically. The memo is off in development.

**CSV parity:** `lib/csv.js` mirrors `engine/export.py`, including numpy's round-half-to-even. For week 2026-09-25 the web export and `python main.py export` match on every cell, base (19 columns) and extended (34 columns), 500 rows.

### Deploying the web service on Railway

Add a fourth service from the same repo:
- Root Directory `/web`;
- config file `/web/railway.json` (Dockerfile build, health check `GET /api/runs`);
- variable `DATABASE_URL=${{Postgres.DATABASE_URL}}`.

Railway sets `PORT`, and `npm start` honours it.

## Backtest (`scanner/backtest/`)

**Signals.** The live `Scanner` generates them week by week. It has no lookahead
(`test_no_lookahead`), so each week sees exactly what the Saturday job would have seen.
The test starts once 80% of the stocks *trading that day* have 200 days of history: 2015-07-24 with
12 years of data (`backfill --years 12 --extend` prepends older yfinance history before the first stored candle).

**Rules.** Each week W:
1. **Exit** a holding whose weekly Supertrend is bearish at W's close, or that left the data.
   With `backtest.sector_exit: true`, also exit when the sector has been outside the top N
   for `sector_exit_weeks` (2) weeks in a row. The default is `false`: Supertrend-only exits.
   Optional: `trail_ma_weeks` (exit on a weekly close below that SMA) and `time_stop_weeks`
   (exit when the gain is still ≤ `time_stop_min_pct` after N weeks).
2. **Enter** the highest-ranked eligible stocks until there are K (10) positions. Eligible
   means all hard filters pass, RS ≥ `min_rs`, rank score ≥ `min_score`, and the sector is in the
   top N sectors that also pass the tradeable tests (breadth ≥ 60%, RS trend > 1, RRG Leading or
   Improving); `top_n_sectors: 0` removes the sector gate. The rank score is the scanner's stock
   score, or its parts re-weighted with `score_weights` (e.g. `{rs: 0.35, near_high: 0.35, ...}`).
3. **Fill** every order at the **open of week W+1**, never at the signal bar.
4. **Stops** (optional): `stop_pct` below entry, ratcheted to `trail_pct` below the highest weekly
   close. Hit intra-week on the weekly low; filled at the stop, or at the open on a gap through it.
5. **Mark** equity at the close of W+1.

**Costs and sizing.**
- **Costs:** `cost_round_trip_pct` (0.2%) split across both sides, plus `slippage_pct` (0.05%) per side.
- **Sizing:** `equal` (equity / K) or `risk` (risk_pct of equity to the ST stop), capped at `max_position_pct` and by available cash.
- **Bear regime:** `bear_mode` gives half-size entries (default), no entries, or ignores the regime.

**Metrics** (vs Nifty 500 buy and hold, and vs an **equal-weight buy-and-hold of the same stock
list**, which carries the same survivorship bias and so is the fair yardstick): CAGR, MAR (CAGR ÷ |max DD|),
calendar-year returns, total return, max drawdown, Sharpe,
volatility, win rate, average win and loss, profit factor, expectancy (% and ₹ per trade),
exposure, trades per year, average holding, and exit-reason breakdown.

**Survivorship bias.** Every run records `survivorship_bias`. It is `true` until
`index_member_snapshots` covers the whole test window, since point-in-time Nifty 500
membership is only recorded from the first `universe` run onward. The UI shows a warning banner.

**Stored in** `backtest_sweeps`, `backtest_runs` (params + metrics JSON + sortable columns),
`backtest_equity` (weekly equity, benchmark, drawdown, invested %) and `backtest_trades`.
Shown on `/backtest` and via `/api/backtest`, `/api/backtest/{id}`.

**Verification.** `tests/test_backtest.py` checks the rules on a hand-built market:
- fills at the next open, costs, and the cash cap;
- the top-N / score / K ordering, filters and min RS;
- the sector-exit streak, both bear modes, and risk sizing;
- accounting (final equity = capital + Σ trade P&L) and the metrics.

On the real run: 0 fills away from the next open, 0 fills at or before the signal week,
never more than K positions.

### Sweep grid and walk-forward

`backtest.sweep` has signal axes (`supertrend`, `sector_weights`: each pair rebuilds the scanner
signals, ~45 s over 11 years) and simulation axes: `grid` (any backtest key → list of values) times
named `variants` (override sets per axis, e.g. `exit: {st: ..., stop10: ...}`). Simulations run in
parallel (`--jobs`, default CPUs − 1): 1,440 runs take about 5 minutes.

**Walk-forward** (`backtest.walk_forward`): every run is simulated once over the whole history. Then,
each year, the `top_k` runs with the best `metric` over the previous `train_years` are traded as an
equal blend for the next `test_years`, and re-picked. No week is traded with parameters that saw it.
The result is saved as an extra run in the sweep (`params.kind = "walk_forward"`), and every run also
gets `metrics.oos` (its own numbers over the same out-of-sample span).

```bash
python main.py backtest --sweep --name "12y walk-forward"   # grid + walk-forward
python main.py walk-forward --sweep 4                       # redo only the walk-forward with new settings
```

`tests/test_walkforward.py` checks that picks use only the training window, the rolling folds, and top-k blending.

### 12-year sweep #4 (2015-07-24 → 2026-09-25, 1,440 runs)

Yardsticks over the full span: Nifty 500 **+11.0%** CAGR (max DD −34%); equal-weight buy-and-hold
of today's Nifty 500 list **+23.5%** (max DD −42%). The ~12-point gap is mostly survivorship bias,
so a setting only shows skill if it beats the second number.

| | CAGR | Max DD | Win rate | PF | Out-of-sample span¹ |
|---|---|---|---|---|---|
| Old baseline (config until 2026-09-30): ST(10,3), top 5, RS 70, K 10, ST-only exit, old score, bear half | 16.7% | −26.9% | 52.5% | 4.26 | 23.1% |
| + sector-rotation exit | 23.6% | −36.3% | 51.4% | 2.24 | 33.3% |
| **Candidate v2**: ST(7,3), top 5, RS 80, K 10, ST + sector exit, momentum rank, bear ignored | **27.0%** | −28.2% | **54.6%** | 2.34 | 40.2% |
| Aggressive: same, K 5 | 30.5% | −36.3% | 50.5% | 2.52 | 47.8% |
| **Walk-forward** (top 10 by CAGR, 4y rolling train, 1y test) | — | — | 48.4% | 2.12 | **35.9%** (DD −32.8%) |

¹ 2019-07-19 → 2026-09-25. Over that span: Nifty 500 +13.1%, equal-weight universe +30.3%,
median of all 1,440 runs +24.6%.

**Adopted 2026-09-30 (candidate v2 is now the config).**
- Live scanner: `supertrend` (7, 3); `scoring.weights` = {rs 0.35, near_high 0.35, volume 0.10, tightness 0.10, sector 0.10};
  `penalties.points` = 0. The flags still appear in reasons.
- Backtest defaults were `min_rs` 80, `sector_exit: true` and `bear_mode: ignore`, reproducing the v2 row exactly
  (26.99% CAGR). They were later replaced by Rule 1 (below); `bear_mode: ignore` is kept.
- The old values are kept as comments in `config.yaml`. The sweep's `rank` axis now compares `scanner` (the new
  weights) with `legacy` (the old weights, without penalties).

**Settings that helped in both the full span and the out-of-sample span** (medians across the grid):
- Exit on Supertrend **or** sector rotation: 19.2% CAGR and 52% win rate, vs 15.1% and 47% for Supertrend only.
  A 10-week SMA exit is similar on CAGR (18.5%) but wins only 42% of trades.
- ST(7,3) over (10,3) and (10,2): 18.1% vs 16.2%.
- Ranking by RS + nearness to the 52-week high (`momentum`) over the scanner score: 17.5% vs 15.7%.
- Ignoring the bear regime rather than halving size: 17.5% vs 16.0%.
- No sector gate: 18.7% but max DD −34%. Top 3 or 5 sectors keep drawdowns lower.

**Settings that didn't help:**
- Hard 10% stops and trailing stops: 15.3–15.5%. They cut winners that later recover.
- Min score 70: 15.5%. It trades half as often; the extra selectivity doesn't pay.
- RS 70 vs 80: no difference.

**Walk-forward selection metric matters.** Picking by CAGR gave 33–39% out of sample for every
top-k (1–100) and for rolling or anchored windows. Picking by MAR or Sharpe gave 13–27%: it chose
defensive sets that lagged. Sweep #3 kept the MAR result for reference. Choosing the selection
metric is itself a choice made after seeing results, so treat 35.9% as optimistic too.

**Caveats.**
- Every number is survivorship-biased.
- Most of the edge came in 2020–2024. The walk-forward lost 12.6% in 2024-25, when Nifty 500 made +1.8%.
- yfinance history is adjusted for dividends and splits; before about 2016, only 318 of today's stocks traded.

### Survivorship-free test and Monte Carlo (2026-10-06)

**Point-in-time universe** (`jobs/pit_universe.py`, separate DB `supertrend_pit`, `config_pit.yaml`). Every NSE stock
from the bhavcopy archive since 2013, delisted ones included (3,393 sessions). Every March and September the
universe is the top 750 equity ISINs by average traded value over the 6 months to January / July (traded on
≥ 80% of sessions). 1,751 stocks were members at some point; 266 have since delisted. Splits, bonuses and
consolidations come from NSE's corporate-action list, and each is applied only when the ex-date price gap confirms it.
The old-format bhavcopy `PREVCLOSE` is **not** adjusted. 167 large gaps stay unadjusted, mostly demergers. In Rule 1
this hits one trade (FEL 2016, −86%, about −0.7 pt/yr). Prices exclude dividends, and so does the yardstick.

```bash
python main.py pit-download                     # ~3,400 archive files -> data_cache/ (~15 min, re-runnable)
createdb supertrend_pit
export SCANNER_CONFIG=config_pit.yaml DATABASE_URL=postgresql://localhost:5432/supertrend_pit
python main.py migrate && python main.py pit-build     # ~4 min
python main.py supertrend --full && python main.py backtest && python main.py monte-carlo --runs 300
```

Same rules, same window (2015-07-31 → 2026-10-01):

| | CAGR | Max DD | Sharpe | 2015–20 | 2021–26 | Worst year | Top-5 share | Yardstick: equal-wt universe | Nifty 500 |
|---|---|---|---|---|---|---|---|---|---|
| Live DB: today's 750 (survivors) | 33.4% | −22.7% | 1.47 | 18.6% | 48.5% | −11.8% | 70% | 24.5% (DD −47%) | 10.6% |
| **Point-in-time 750** | **19.4%** | **−28.9%** | **0.99** | 15.9% | 22.3% | −19.3% | 72% | **11.9%** (DD −64%) | 10.6% |
| Point-in-time, score without the sector part¹ | 24.9% | −30.1% | 1.14 | 14.3% | 35.4% | −22.1% | 85% | 11.9% | 10.6% |

¹ 1,016 point-in-time stocks have no industry (NSE's quote API is blocked; delisted stocks have none anyway), so
the sector part of the score is noise there. Without it the score-ordered run makes 24.9%, but most of the gain is
one 2026 trade (STLTECH, +607%). Its Monte Carlo median is the same as the default's (18.6% vs 18.9%).

**Monte Carlo** (`python main.py monte-carlo [--runs N] [--skip-pct P]`; `shuffle_seed` / `skip_pct`): with ~10%
per stock, cash runs out and hundreds of valid signals are skipped. Each run buys a week's eligible signals in a
random order instead of best score first.

| Random signal order | CAGR p5 / median / p95 | Max DD median (p5) | Score order is at |
|---|---|---|---|
| Live DB, 500 runs | 25.2% / 30.6% / 33.6% | −23.1% (−24.1%) | 90th pct |
| Live DB, 500 runs, 20% of signals missed | 21.4% / 26.8% / 32.7% | −25.0% (−31.8%) | 97th pct |
| Point-in-time, 300 runs | 14.4% / 18.9% / 24.7% | −28.8% (−32.0%) | ~55th pct |

**Reading.**
- Survivorship bias was worth about **14 points of CAGR**. Its drawdown, Sharpe and worst year are all worse than claimed.
- The edge is real but smaller. With no lookahead, the strategy made about 19% against 11.9% for the equal-weight
  universe and 10.6% for Nifty 500. Every Monte Carlo run beat both. Its drawdown is less than half the equal-weight
  universe's (−29% vs −64%).
- The live result sits near the top of its own distribution (90th–97th percentile), so scoring order and luck added
  a few points. On the point-in-time data, best score first is no better than a random pick.
- Plan around **~15–20% CAGR and −30% drawdowns, with years of −20%**. Missing signals (holidays, late orders) costs
  another ~4 points.

**Against momentum indices** (niftyindices.com back-data, **total-return** indices, weekly closes; cached in
`scanner/data_cache/nifty_momentum_indices.csv`). Rule 1 point-in-time excludes dividends, so it is slightly understated.

| CAGR / max DD | 2015-07 → 2026-10 | 2015–20 | 2021–26 |
|---|---|---|---|
| Rule 1, point-in-time | **19.4%** / −28.9% | 15.9% | 22.3% |
| Nifty500 Momentum 50 TRI | 16.2% / −30.9% | 12.1% | 19.9% |
| Nifty200 Momentum 30 TRI | 14.9% / −30.0% | 14.5% | 15.0% |
| Nifty 500 TRI | 11.8% / −34.2% | 10.3% | 12.8% |

| Year | 2015¹ | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026¹ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Rule 1, point-in-time | 2.2 | −0.2 | 51.9 | −19.3 | 12.0 | 58.5 | 42.9 | 5.1 | 98.9 | 29.7 | −11.0 | −5.8 |
| Nifty500 Momentum 50 TRI | −6.0 | −0.1 | 69.5 | −10.9 | 9.6 | 19.3 | 80.1 | −7.6 | 47.7 | 29.5 | −8.5 | −0.6 |
| Difference | +8.2 | −0.1 | −17.6 | −8.4 | +2.4 | +39.2 | −37.2 | +12.7 | +51.2 | +0.2 | −2.5 | −5.2 |

¹ Partial years (from 2015-07-31; to 2026-10-01). Rule 1 beat Nifty500 Momentum 50 in 6 of 12 years and in 78% of
rolling 3-year windows (median +7.8 pts/yr, range −11 to +22). Rule 1 is before tax; the index fund defers tax.

**After tax** (`backtest.tax`, default `current` since 2026-10-06; `historical` or `none` also work; `backtest/tax.py`). Capital-gains tax is settled every financial
year from cash with Indian set-off rules (short-term loss → any gain, long-term loss → long-term only, carry forward),
4% cess. Positions still open at the end are taxed as sold. The index funds are bought once and sold at the end
(long-term), with a 0.5%/yr expense ratio.

| Point-in-time, Jul 2015 → Oct 2026 | Before tax | After tax (today's rates) | Final on ₹10 L |
|---|---|---|---|
| Rule 1 | 19.4% | **17.8%** (historical rates 18.1%) | ₹62 L |
| Nifty500 Momentum 50 index fund | 15.7% | **14.6%** | ₹46 L |
| Nifty200 Momentum 30 index fund | 14.3% | 13.3% | ₹40 L |
| Nifty 500 index fund | 11.2% | 10.3% | ₹30 L |

Tax costs Rule 1 only ~1.6 pts/yr: 34 of 115 trades are held over a year and make 73% of the profit, and losers offset
winners. Max drawdown −31.0% after tax (taxes leave in April). Monte Carlo after tax (500 random signal orders, stored as runs #9–14 in `supertrend_pit`):
CAGR 13.1% / 16.9% / 22.3% (p5 / median / p95), max DD median −31.4% (p5 −34.9%), worst year median −18.5%; about
80% of runs beat the Nifty500 Momentum 50 fund after tax (14.6%), 99% beat the equal-weight universe.

### Experiment log and the Backtests page

Every strategy test is listed on **`/backtests`** (nav: Backtests): the current default, each experiment with its
question, finding and a link per variant, the other single runs, and the sweeps (still on `/backtest`, nav: Sweeps).
The experiments live in `scanner/backtest/experiments.py`; this re-runs them on the current data and defaults and
saves each variant as a run (`params.experiment` set; they don't feed the dashboard's model portfolio):

```bash
python main.py experiments                 # all (about 1.5 min)
python main.py experiments --only score_exit,ma_entry
```

**Profit booking (tested 2026-10-05, not adopted).** `take_profit` (new): `[[50, 0.4], [100, 0.4]]` sells 40% at
+50%, then 40% of the rest at +100%; `tp_fill: intraweek` fills as a limit order at the target, `close` at the next
open after a weekly close above it.

| Live default vs | CAGR | Max DD | Sharpe | 2015–20 | 2021–26 | Worst year | w/o top 5 / 10 | Top-5 share | Final |
|---|---|---|---|---|---|---|---|---|---|
| **Rule 1 (no booking)** | **33.3%** | −22.7% | 1.47 | 18.6% | **48.5%** | −11.8% | 21.8% / 12.2% | 70% | **₹2.49 Cr** |
| 40% at +50%, 40% at +100% (limit) | 28.0% | −24.1% | **1.53** | 18.2% | 37.7% | −12.9% | 22.3% / **17.7%** | **44%** | ₹1.57 Cr |
| Same, weekly-close fills | 26.8% | −26.8% | 1.45 | 16.7% | 36.9% | −16.5% | 18.9% / 17.3% | 47% | ₹1.42 Cr |
| Same, remainder exits only when bearish | 27.0% | **−19.6%** | 1.49 | 18.5% | 35.3% | −10.7% | 18.0% / 15.9% | 47% | ₹1.44 Cr |
| 40% at +30%, 40% at +60% | 24.6% | −27.4% | 1.40 | 16.9% | 32.1% | −18.0% | 22.7% / 18.8% | 36% | ₹1.17 Cr |
| 40% at +100%, 40% at +200% | 29.4% | −21.6% | 1.50 | **20.1%** | 38.6% | −13.1% | 22.5% / 17.6% | 53% | ₹1.78 Cr |

Booking profits smooths the curve and halves the dependence on the top stocks, but sells the multibaggers early:
the 2021–26 boom drops from 48.5% to about 38%. The earlier the targets, the lower the CAGR.

### Live default since 2026-10-02: 750 stocks + score exit

- **Universe:** Nifty Total Market (Nifty 500 + Microcap 250, ~750 stocks). The benchmark stays Nifty 500.
- **Exit:** the weekly Supertrend turns bearish **or** the stock score falls below 30 (`exit_score_below: 30`).
- Everything else as in Rule 1 below: flip-week entry, score ≥ 70, 10% of equity per stock from own cash.

| Backtest #3103, Jul 2015 – Oct 2026 | CAGR | Max DD | Sharpe | Trades | Win | PF | Avg hold | Final (₹10 L start) |
|---|---|---|---|---|---|---|---|---|
| **750 stocks, exit bearish or score < 30** | **33.4%** | **−22.7%** | **1.47** | 104 | 60% | 9.3 | 43 wk | ₹2.49 Cr |

Exits: 77 Supertrend, 18 score, 9 still open. Survivorship bias applies (and is stronger for microcaps).

Moving an existing database to the wider universe (done locally; repeat on Railway):

```bash
python main.py universe && python main.py backfill --years 12 && python main.py supertrend --full
python main.py full-rescan --weeks 80 && python main.py backtest
```

**Moving-average entry rule (tested 2026-10-05, not adopted).** `entry_ma` (new): none | template (price > SMA50 >
SMA150 > SMA200 and SMA200 rising, the scanner's trend-template filter) | stack | above50 | rising200. Live default
(750 stocks, exit bearish or score < 30):

| Entry MA rule | CAGR | Max DD | Sharpe | Trades | Win | Exposure | 2015–20 | 2021–26 | Worst year | w/o top 5 / 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| **None (default)** | **33.3%** | **−22.7%** | **1.47** | 104 | 60% | 87% | 18.6% | 48.5% | −11.8% | 21.8% / 12.2% |
| Full trend template | 16.6% | −31.8% | 1.04 | 87 | 54% | 70% | 14.4% | 18.4% | −17.1% | 13.0% / 12.7% |
| MA stack, not "rising" | 18.1% | −31.8% | 1.11 | 88 | 55% | 71% | 17.1% | 18.6% | −17.1% | 14.4% / 13.3% |
| SMA200 rising only | 22.0% | −27.0% | 1.12 | 107 | 51% | 82% | 14.8% | 28.6% | −15.5% | 18.5% / 18.3% |
| Price > SMA200 | 30.4% | −25.3% | 1.41 | 104 | 55% | 84% | 15.2% | 45.9% | −16.6% | 17.2% / 10.7% |
| Price > SMA50 | 33.3% | −22.7% | 1.47 | 104 | 60% | 87% | (identical: a flip-week stock is always above its SMA50) | | | |

Per trade, the template helps: flips that pass it win 56% (avg +42%) vs 49% (avg +25%) for those that fail, across
all 624 score ≥ 70 flips. But only 1 in 4 flips passes, and the failures include the early flips out of a bottom.
In the default run they made ₹186 L of the ₹239 L profit. Requiring the template buys later in each cycle, leaves
30% of the account idle and halves CAGR.

### Rule 1, sized at 10% of equity (default from 2026-10-01 to 2026-10-02)

Flip-week entry, score ≥ 70, any sector, tradeable filters only, exit when the weekly Supertrend turns bearish.
Each buy is 10% of current equity, paid from the account's own cash: no position limit, no new capital. Signals
are taken best score first; a buy that cash covers below half its size is skipped (`min_fill_pct: 50`). Config:
`entry_max_weeks_in_trend: 1`, `max_positions: 0`, `sizing: fixed`, `position_size: 0`, `position_pct: 10`,
`add_capital: false`, `min_fill_pct: 50`.

| | CAGR | Max DD | Sharpe | Win rate | Trades | Final (₹10 L start) |
|---|---|---|---|---|---|---|
| **10% of equity, own cash** (`python main.py backtest`) | **28.7%** | **−22.4%** | **1.41** | 56.1% | 107 | ₹1.68 Cr |
| 10 equal positions (previous) | 28.8% | −22.5% | 1.41 | 56.4% | 110 | ₹1.69 Cr |
| 5% of equity (about 20 stocks) | 22.7% | −21.1% | 1.30 | 54% | 208 | – |

Chosen over the variants below because it needs no new money, and the flip week beat weeks 1–3 on drawdown in every
sizing tested (−22% vs −27% to −34%).

**Risk filters tested on this default (2026-10-01, none adopted).** `bear_mode`, `max_per_sector` (new) and
`above_sma200` (new; entry only above the 200-day SMA):

| bear / sector cap 3 / > SMA200 | CAGR | Max DD | Sharpe | 2015–20 | 2021–26 | Worst year |
|---|---|---|---|---|---|---|
| **ignore / – / – (default)** | **28.7%** | −22.4% | **1.41** | 17.8% | 39.5% | −12.0% |
| ignore / – / yes | 25.4% | −22.2% | 1.30 | 14.8% | 35.9% | −15.0% |
| ignore / yes / – | 25.3% | −21.8% | 1.32 | 14.1% | 36.5% | −14.3% |
| ignore / yes / yes | 22.9% | −21.3% | 1.24 | 12.6% | 33.0% | −15.8% |
| half / – / – | 23.0% | −23.7% | 1.25 | 14.4% | 31.2% | −13.1% |
| half / yes / yes | 19.6% | −23.1% | 1.15 | 11.0% | 27.8% | −15.8% |
| skip / – / – | 18.1% | −35.1% | 1.01 | 8.4% | 27.8% | −14.1% |

- Every filter costs 3–10% CAGR and moves max drawdown by 1 point at most.
- `half` doesn't de-risk with %-of-equity sizing: half-size buys just let twice as many fit, so exposure stays ~85%.
- `skip` misses the flips right after a sell-off. The regime is "bear" 33% of weeks and turns late, so it skips the
  best entries.
- `above_sma200` barely changes the trade count (107 → 106) yet costs 3.3% CAGR. A flip-week stock with score ≥ 70 is
  nearly always above its 200-day SMA already; a few different fills change the result this much, which shows how
  much it hangs on a few positions.

**5% per stock, up to 20 at a time (tested 2026-10-01, not adopted).**

| Sizing | CAGR | Max DD | Sharpe | Trades | Avg / max positions | 2015–20 | 2021–26 | Worst year | CAGR without top 5 / top 10 stocks | Signals skipped (no cash) |
|---|---|---|---|---|---|---|---|---|---|---|
| **10% each (default)** | **28.7%** | −22.4% | **1.41** | 107 | 8.4 / 11 | 17.8% | 39.5% | −12.0% | 20.4% / 9.6% | 385 |
| 5% each, max 20 | 22.5% | −21.0% | 1.30 | 185 | 14.8 / 20 | 15.3% | 29.4% | −14.8% | 16.4% / 12.1% | 307 |
| 20 equal positions | 22.7% | −21.0% | 1.30 | 194 | 15.7 / 20 | 15.3% | 29.8% | −15.5% | 16.5% / 12.2% | 298 |

The 20 cap never binds at 5% (cash runs out first), so "max 20" equals "no cap". Halving the size costs 6.2% CAGR
for 1.4 points of drawdown. It depends less on the very top names (without the top 10 stocks: 12.1% vs 9.6%), but it
is behind in both halves of the history.

**Score cutoff 75–85 (tested 2026-10-01, 70 kept).** Rule 1, 10% of equity per stock unless noted.

| Min score | CAGR | Max DD | Sharpe | Trades | Win | Exposure | 2015–20 | 2021–26 | Worst year | without top 5 / 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| **70 (default)** | **28.7%** | −22.4% | 1.41 | 107 | 56% | 88% | **17.8%** | 39.5% | −12.0% | 20.4% / 9.6% |
| 75 | 27.4% | −23.6% | 1.38 | 87 | 56% | 80% | 12.9% | 42.4% | −17.8% | 19.4% / 8.8% |
| 77 | 30.0% | −24.6% | 1.49 | 77 | 58% | 76% | 12.3% | 48.7% | −15.8% | 19.2% / 11.1% |
| 80 | 26.8% | −23.0% | 1.44 | 65 | 66% | 65% | 12.2% | 42.0% | −11.2% | 16.1% / 9.0% |
| 82 | 21.9% | −17.5% | 1.35 | 54 | 69% | 53% | 9.9% | 34.0% | −2.4% | 9.4% / 4.3% |
| 85 | 16.1% | −20.8% | 1.16 | 44 | 68% | 45% | 8.0% | 24.2% | −6.8% | 3.8% / 2.2% |
| 80, 15% each | 29.8% | −23.3% | 1.44 | 46 | 70% | 68% | 16.8% | 42.9% | −6.6% | 18.9% / 15.4% |
| 5% × 20, score 80 | 18.9% | −17.0% | 1.39 | 112 | 63% | 53% | 10.7% | 26.8% | −6.9% | 11.2% / 7.3% |

A higher cutoff raises the win rate (56% → 69%) but leaves cash idle (exposure 88% → 45%), so CAGR falls past 80.
The 77 peak sits between two lower values and comes only from 2021–26, so it looks like noise. 80 at 15% each
edges out the default but on 46 trades and about 4 positions at a time. 70 has the best 2015–20 result of every
cutoff, so it stays.

**More trades, less dependence on a few winners (tested 2026-10-02, none adopted).** New options: `trim_above_pct` /
`trim_to_pct` (sell a winner back to a target weight) and `swap_min_score` / `swap_below_score` (out of cash, a
strong new signal replaces the weakest holding). The Supertrend combination counts a (10,3) flip as an entry week
while (7,3) is bullish, with exits on (7,3); it was run from a script, not a config option. "Top-5 share" = the share
of total profit from the 5 best stocks.

| Variant | CAGR | Max DD | Sharpe | Buys | Win | 2015–20 | 2021–26 | without top 5 / 10 | Top-5 share |
|---|---|---|---|---|---|---|---|---|---|
| **Default (10% each)** | **28.7%** | −22.4% | 1.41 | 107 | 56% | 17.8% | 39.5% | 20.4% / 9.6% | 66% |
| Trim >15% → 10% | 26.5% | −21.1% | 1.38 | 114 | 58% | 16.7% | 36.0% | 20.4% / 11.2% | 62% |
| Trim >25% → 10% | 27.7% | −21.7% | 1.43 | 108 | 56% | 17.8% | 37.5% | 17.8% / 12.5% | 69% |
| 7% each, trim >12% → 7% | 22.6% | −22.2% | 1.32 | 148 | 57% | 14.8% | 29.8% | 17.5% / 10.9% | 51% |
| Swap: new ≥ 80, weakest < 60 | 30.9% | −23.6% | 1.46 | 123 | 62% | 19.3% | 43.0% | 16.4% / 12.4% | 62% |
| Swap, 11 neighbouring settings (median) | 27.1% | −25.6% | 1.29 | 124 | 56% | 17.0% | 38.4% | – | – |
| ST(7,3) + (10,3) | 25.6% | −26.3% | 1.29 | 106 | 55% | 15.9% | 35.0% | 14.4% / 8.9% | 70% |
| ST combo, 7% + trim >12% | 22.3% | −21.1% | 1.27 | 161 | 55% | 14.3% | 29.9% | 15.9% / 13.4% | 49% |
| 5% × 20 (for reference) | 22.5% | −21.0% | 1.30 | 185 | 55% | 15.3% | 29.4% | 16.4% / 12.1% | 47% |

- Trimming barely frees cash: with 10% sizing a position rarely passes 20–25%, and the stocks bought with the
  proceeds are weaker than the winners sold.
- The best swap setting is an isolated peak: its 11 neighbours give a median of 27.1% with deeper drawdowns.
- The ST combination adds late entries into trends already under way, the same weakness as entering in weeks 1–3.
- Only smaller positions really lower the dependence (top-5 share 66% → 47–51%), and they cost about 6% CAGR.

**Wider universe: Nifty Total Market (tested 2026-10-02, live scanner still on Nifty 500).** Nifty 500 +
Microcap 250 = 750 stocks, 12 years backfilled, in a separate database so the live data is untouched:

```bash
createdb supertrend_wide && pg_dump -Fc -d supertrend | pg_restore -d supertrend_wide
export SCANNER_CONFIG=config_wide.yaml DATABASE_URL=postgresql://localhost:5432/supertrend_wide
python main.py universe && python main.py backfill --years 12 && python main.py supertrend --full
```

`SCANNER_CONFIG` (new) points the CLI at another config file; `config_wide.yaml` differs from `config.yaml` only in
the universe. Rule 1 defaults, Jul 2015 – Sep 2026:

| Universe / sizing | CAGR | Max DD | Sharpe | Trades | Win | 2015–20 | 2021–26 | Worst year | without top 5 / 10 | Top-5 share | Equal-wt universe |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Nifty 500, 10% each | 28.7% | −22.4% | 1.41 | 107 | 56% | 17.8% | 39.5% | −12.0% | 20.4% / 9.6% | 66% | 23.5% |
| **Total Market, 10% each** | **31.6%** | −24.0% | **1.48** | 99 | **61%** | 18.5% | 44.8% | −12.7% | 18.0% / **12.6%** | 72% | 24.9% |
| Nifty 500, 5% × 20 | 22.5% | −21.0% | 1.30 | 185 | 55% | 15.3% | 29.4% | −14.8% | 16.4% / 12.1% | 50% | |
| Total Market, 5% × 20 | 24.9% | −25.1% | 1.37 | 186 | 58% | 15.9% | 33.5% | −20.3% | 19.2% / 15.1% | 50% | |
| Total Market, 3% × 33 | 22.8% | −24.1% | 1.36 | 284 | 56% | 12.5% | 32.9% | −16.4% | 18.0% / 15.9% | 38% | |

- About 2.5% CAGR more at every size, and better without the top 10 stocks. Part of it is the universe itself:
  equal-weight Total Market made 24.9% vs 23.5%.
- Only 13–44 trades are in microcaps (46–52% win), yet the biggest winner is one: STLTECH, ₹67 L of profit at 10%
  sizing. That raises the top-5 share to 72%.
- Survivorship bias is stronger here: today's Microcap 250 holds the small caps that survived and grew into it.
- Signals skipped for lack of cash rise from 385 to 525, so a wider universe mostly adds choice, not trades, unless
  positions get smaller.

**All NSE stocks (tested 2026-10-02, worse).** Every EQ/BE/BZ stock in NSE's `EQUITY_L.csv` (2,593), in a third
database `supertrend_all` (deleted after the test). Industry comes from the index lists; the 1,836 stocks outside them are "Unclassified".
Built with a one-off script (symbols upsert from `EQUITY_L.csv`), then `exclusions`, `backfill --years 12` and
`supertrend --full` as above. Rule 1 defaults, ST (7,3):

| Universe | CAGR | Max DD | Sharpe | Trades | Win | 2015–20 | 2021–26 | Worst year | Equal-wt universe (max DD) |
|---|---|---|---|---|---|---|---|---|---|
| Nifty 500 (live) | **28.7%** | **−22.4%** | 1.41 | 107 | 56% | 17.8% | 39.5% | −12.0% | 23.5% (−42%) |
| Total Market (750) | 31.6% | −24.0% | 1.48 | 99 | 61% | 18.5% | 44.8% | −12.7% | 24.9% (−47%) |
| All NSE (2,593) | 18.5% | −31.7% | 0.92 | 113 | 54% | 15.9% | 20.6% | −18.4% | 21.9% (−62%) |
| All NSE, 5% × 20 | 17.6% | −28.6% | 0.96 | 212 | 53% | 14.2% | 20.4% | −19.6% | |
| All NSE scores, entries only in Total Market | 23.4% | −28.6% | 1.15 | 103 | 56% | | | | |
| All NSE scores, entries only in Nifty 500 | 24.9% | −25.9% | 1.26 | 106 | 56% | | | | |

Two effects, roughly equal: ranking RS and scores against 2,593 stocks picks worse names even inside Nifty 500
(28.7% → 24.9%), and the extra stocks are worse trades (24.9% → 18.5%; 38% win outside the Total Market). The
strategy stops beating its own equal-weight universe. Stay on Nifty 500, or Total Market at most.

**Exit when the score falls (tested 2026-10-02, not adopted).** `exit_score_below` (new): besides the Supertrend
exit, sell when the stock's current score drops below X. Rule 1, 10% of equity per stock:

| Exit when score < | 750: CAGR | 750: Max DD | 750: 2015–20 / 2021–26 | 750: w/o top 10 | Nifty 500: CAGR | Nifty 500: Max DD | Nifty 500: worst year | Avg hold (N500) |
|---|---|---|---|---|---|---|---|---|
| off (default) | 31.6% | −24.0% | 18.5% / 44.8% | 12.6% | 28.7% | −22.4% | −12.0% | 46 wk |
| 20 | 32.0% | −24.0% | 18.4% / 45.9% | 12.3% | – | – | – | – |
| **30** | **32.9%** | **−22.7%** | 18.6% / 47.6% | 12.5% | **30.2%** | −23.1% | **−6.7%** | 43 wk |
| 35 | 32.2% | −23.9% | 16.7% / 48.3% | 12.8% | 29.8% | −26.0% | −10.0% | 38 wk |
| 40 | 35.0% | −24.0% | 19.3% / 51.2% | 17.9% | 27.9% | −28.9% | −10.0% | 33 wk |
| 45 | 24.9% | −27.0% | 14.8% / 35.2% | 14.6% | 23.6% | −26.5% | −17.5% | 29 wk |
| 50 | 27.6% | −26.0% | 14.5% / 41.2% | 18.1% | 22.7% | −26.4% | −14.4% | 25 wk |

On 5% × 20 (750 stocks): off 24.9%, < 40 28.1%, < 30 24.4%, < 20 25.7%.

- Score exits are mostly profitable sales (+28% to +51% on average): they take profit when momentum fades.
- 40 is a spike on 750 stocks (45 gives 24.9%) and loses on Nifty 500 (27.9%, −28.9% max DD): not robust.
- 30 is the only level that helps on both universes, by about 1.5% CAGR, with a much better worst year on Nifty 500.
  The gain is small enough to be noise. At 45 and above, exits come too early.

### Rule 1 with every signal taken (tested 2026-10-01, superseded)

Two changes to Rule 1 below:
- **Entry window:** week 1–3 of a bullish weekly Supertrend run (`entry_max_weeks_in_trend: 3`). Only the first
  qualifying week of a run is a signal.
- **Take every signal:** ₹1 lakh per stock, no position limit (`max_positions: 0`, `sizing: fixed`,
  `position_size: 100000`). When cash can't cover a buy, the shortfall is added as new capital (`add_capital: true`).
  Sale proceeds and profits stay as cash and fund later buys first.

Added capital is a cash flow, not a return. CAGR, drawdown, Sharpe and the yearly table come from the time-weighted
curve (`nav` in `backtest_equity`). XIRR is the money-weighted return on the rupees actually put in. The Nifty 500
comparison invests the same rupees on the same weeks.

| 11.2y, ST(7,3), score ≥ 70 | TWR CAGR | XIRR | Max DD | Sharpe | Trades | Win | Capital added | Final | Nifty 500, same flows |
|---|---|---|---|---|---|---|---|---|---|
| Old Rule 1: flip week, 10 equal | 28.8% | 28.8% | −22.5% | 1.41 | 110 | 56% | 0 | ₹1.69 Cr | – |
| Week 1–3, 10 equal | 26.4% | 26.4% | −33.9% | 1.22 | 122 | 52% | 0 | ₹1.37 Cr | – |
| Flip week, ₹1L each + add capital | 13.7% | 15.7% | −23.5% | 1.10 | 492 | 51% | ₹58 L | ₹2.34 Cr | ₹1.84 Cr |
| **Week 1–3, ₹1L each + add capital** | **14.0%** | **16.1%** | **−25.5%** | **1.10** | **712** | **49%** | **₹73 L** | **₹3.06 Cr** | **₹2.32 Cr** |

The fixed ₹1 lakh size doesn't compound: profits pile up as idle cash (invested falls from about 85% to 35–40% after
2022), so the percentage return roughly halves, while the rupee result grows because more money goes in.

**Growing the size with the account** (`position_pct`, tested 2026-10-01, not the default). Size =
max(₹1 lakh, X% of equity). New capital covers at most the ₹1 lakh floor; anything bigger is paid from the
account's own cash. Without that cap the top-ups feed back on themselves (2% × 132 positions = 264% of equity).

| Week 1–3, every signal | TWR CAGR | XIRR | Max DD | Sharpe | PF | Capital added | Final | Nifty 500, same flows |
|---|---|---|---|---|---|---|---|---|
| Fixed ₹1L (default) | 14.0% | 16.1% | −25.5% | 1.10 | 5.16 | ₹0.73 Cr | ₹3.06 Cr | ₹2.32 Cr |
| max(₹1L, 1%) | 17.8% | 20.6% | −25.5% | 1.19 | 4.01 | ₹1.41 Cr | ₹5.10 Cr | ₹2.98 Cr |
| max(₹1L, 2%) | 20.2% | 24.5% | −25.8% | 1.26 | 4.61 | ₹2.95 Cr | ₹9.57 Cr | ₹5.01 Cr |
| max(₹1L, 3%) | 22.1% | 27.4% | −24.1% | 1.29 | 5.64 | ₹3.93 Cr | ₹14.9 Cr | ₹6.77 Cr |
| max(₹1L, 5%) | 24.6% | 30.7% | −23.5% | 1.34 | 5.94 | ₹4.73 Cr | ₹22.2 Cr | ₹8.35 Cr |
| max(₹1L, 10%) | 27.1% | 33.9% | −26.9% | 1.37 | 6.91 | ₹5.36 Cr | ₹30.9 Cr | ₹9.64 Cr |

A bigger X keeps more of the account invested (about 90–98% vs 44% over the last 3 years). The catch is the added
capital: with 2–5%, ₹70–110 lakh a year in 2025–26, because every signal beyond what the cash covers still needs
₹1 lakh of new money.

### Rule 1: the backtest default from 2026-09-30 to 2026-10-01

Buy in the week the weekly Supertrend flips bullish when the stock score is ≥ 70. Any sector qualifies,
and the only filters are tradeability (liquidity, price, not ASM). Hold until the Supertrend turns
bearish. 10 equal positions; the bear regime is ignored. Config: `top_n_sectors: 0`, `min_rs: 0`,
`min_score: 70`, `entry_max_weeks_in_trend: 1`, `entry_filters: tradeable`, `sector_exit: false`.

| | CAGR | Max DD | Sharpe | Win rate | Trades | Avg hold |
|---|---|---|---|---|---|---|
| **Rule 1** (`python main.py backtest`) | **28.8%** | **−22.5%** | **1.41** | 56.4% | 110 | 46 wk |
| v2: top-5 sectors, RS 80, Supertrend + sector exit | 27.0% | −28.2% | 1.17 | 54.6% | 403 | 11.5 wk |
| Rule 1 at score ≥ 80 | 26.9% | −23.0% | 1.44 | 67.6% | 68 | 53 wk |
| Rule 1 + all hard filters + RS 80 | 18.9% | −27.3% | 1.10 | 52.9% | 68 | 48 wk |

**Sensitivity (sweep #5, 90 runs).**
- Minimum score 65–75 is a plateau (median 26.0–26.4% CAGR); 60 and 80 are lower.
- Buying only in the flip week has the lowest median drawdown (−25%, vs −30% when allowing 2–3 weeks).
- ST(7,3) beats (10,3): median 26.4% vs 22.7%.
- 57 of 90 runs beat equal-weight buy-and-hold of the same stocks (23.5%).

**Stress tests.**
- Costs ×5 still gives 27.2%.
- The drawdown stays around −22% even when the best stocks are removed, so the lower risk is structural.
- The **extra return is fragile**. Removing the 3 / 5 / 10 most profitable stocks cuts CAGR to 23.7% / 20.4% / 12.4%.
- 2015–2020 gave 17.3% vs 17.7% for the equal-weight universe; 2021–2026 gave 40.3% vs 28.9%.

The dashboard's Rule 1 card and the Scanner's "Rule 1 buys" preset show the live signals. The model portfolio
comes from the latest Rule 1 backtest, so run `python main.py backtest` after each weekly scan to refresh it.
Rule 1 was adopted for its lower drawdown and simplicity; its return edge over the survivor universe is
uncertain until the test is run on historically accurate index membership.

### Exit rule comparison, 2-year window (superseded by the 12-year sweep)

| Sweep | Exit rule | Beat index | Median CAGR | Best | Median max DD | PF > 1 | Avg trades | Avg hold |
|---|---|---|---|---|---|---|---|---|
| #1 grid v1 | Supertrend **or** sector out of top N 2 weeks | 34 / 54 | −0.3% | +11.3% | −21.9% | 24 / 54 | 96 | 9 wks |
| #2 ST-exit only | Supertrend only | 8 / 54 | −5.8% | 0.0% | −29.2% | 0 / 54 | 46 | 21 wks |

Supertrend-only exits hold positions about 21 weeks. Average losses stay large (about −16%, the width of the
weekly stop). With all K slots tied up in stocks from sectors that have rotated out, the
portfolio cannot move into the new leading sectors. In this window, the sector-rotation exit
was the main source of return. Set `backtest.sector_exit: true` to go back to rule #1.

### First sweep, 2-year window (2024-07-26 → 2026-09-25; Nifty 500 CAGR −1.4%, max DD −18.8%)

| Signal set | Median CAGR | Median max DD | Median PF | Beat index |
|---|---|---|---|---|
| ST(7,3) · default weights | **+4.6%** | −19.2% | 1.21 | 9 / 9 |
| ST(10,2) · default | +0.1% | −19.8% | 1.00 | 6 / 9 |
| ST(10,2) · momentum | −0.5% | −21.0% | 0.98 | 8 / 9 |
| ST(7,3) · momentum | −0.7% | −25.7% | 0.97 | 6 / 9 |
| ST(10,3) · momentum | −1.9% | −25.3% | 0.91 | 2 / 9 |
| ST(10,3) · default (config at the time) | −2.1% | −23.0% | 0.90 | 3 / 9 |

Across all 54 runs the median CAGR is −0.3%, 24 of 54 have a profit factor above 1, and the
median max drawdown is −21.9%. That is worse than the index's −18.8%.

**Reading.** The configured (10,3) Supertrend showed no edge in this window. Its wide weekly
stops cost about 16% on average when hit, while sector-rotation exits cut winners early. A
faster ST(7,3) did better on every portfolio variant.

**Caveats.**
- Two years is one market phase.
- Survivorship bias inflates every number.
- Picking the best of 54 variants overfits.

Treat these results as a direction to test further, not as evidence to trade. Better tests:
- extend the history (`backfill --years 10`);
- keep collecting point-in-time membership;
- re-test out of sample before changing `config.yaml`.
