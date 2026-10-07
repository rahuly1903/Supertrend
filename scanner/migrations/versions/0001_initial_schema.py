"""Initial schema: universe, candles, supertrend state, weekly snapshots.

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
-- ---------------------------------------------------------------------------
-- Universe
-- ---------------------------------------------------------------------------
CREATE TABLE symbols (
    id                  serial PRIMARY KEY,
    symbol              text NOT NULL UNIQUE,
    name                text,
    isin                text UNIQUE,                      -- stable key across symbol changes
    industry            text,
    series              text NOT NULL DEFAULT 'EQ',
    shares_outstanding  bigint,
    is_active           boolean NOT NULL DEFAULT true,    -- member of current Nifty 500 list
    status              text NOT NULL DEFAULT 'listed'
                        CHECK (status IN ('listed', 'suspended', 'delisted')),
    listed_date         date,
    delisted_date       date,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX symbols_industry_idx ON symbols (industry) WHERE is_active;

-- Old ticker -> current symbol (NSE renames; matched by ISIN)
CREATE TABLE symbol_aliases (
    old_symbol   text NOT NULL,
    symbol_id    integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    changed_on   date NOT NULL,
    PRIMARY KEY (old_symbol, changed_on)
);

CREATE TABLE indices (
    id          serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    slug        text UNIQUE,
    category    text NOT NULL CHECK (category IN ('broad', 'sectoral', 'thematic')),
    csv_url     text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Current membership (many-to-many)
CREATE TABLE index_members (
    index_id   integer NOT NULL REFERENCES indices(id) ON DELETE CASCADE,
    symbol_id  integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    added_on   date,
    PRIMARY KEY (index_id, symbol_id)
);
CREATE INDEX index_members_symbol_idx ON index_members (symbol_id);

-- Monthly membership snapshots -> point-in-time universe for backtests
CREATE TABLE index_member_snapshots (
    index_id       integer NOT NULL REFERENCES indices(id) ON DELETE CASCADE,
    snapshot_date  date NOT NULL,
    symbol_id      integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    PRIMARY KEY (index_id, snapshot_date, symbol_id)
);

-- NSE ASM / GSM surveillance lists, one snapshot per fetch
CREATE TABLE exclusion_list (
    as_of_date  date NOT NULL,
    list_type   text NOT NULL CHECK (list_type IN ('ASM', 'GSM')),
    symbol_id   integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    stage       text,
    PRIMARY KEY (as_of_date, list_type, symbol_id)
);

-- ---------------------------------------------------------------------------
-- Price data
-- ---------------------------------------------------------------------------
CREATE TABLE daily_candles (
    symbol_id     integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    date          date NOT NULL,
    open          double precision NOT NULL,
    high          double precision NOT NULL,
    low           double precision NOT NULL,
    close         double precision NOT NULL,
    volume        bigint,
    delivery_qty  bigint,
    delivery_pct  real,
    source        text NOT NULL DEFAULT 'yf' CHECK (source IN ('yf', 'bhav')),
    PRIMARY KEY (symbol_id, date),
    CONSTRAINT daily_candles_sane CHECK (low > 0 AND high >= low AND open > 0 AND close > 0)
);
-- BRIN pays off because loaders COPY rows sorted by date (append-mostly by date).
CREATE INDEX daily_candles_date_brin ON daily_candles USING brin (date);

-- week_end_date = last trading day of the W-FRI week (Thursday on a Friday holiday)
CREATE TABLE weekly_candles (
    symbol_id        integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    week_end_date    date NOT NULL,
    week_start_date  date NOT NULL,
    open             double precision NOT NULL,
    high             double precision NOT NULL,
    low              double precision NOT NULL,
    close            double precision NOT NULL,
    volume           bigint,
    trading_days     smallint NOT NULL,
    PRIMARY KEY (symbol_id, week_end_date)
);
CREATE INDEX weekly_candles_week_idx ON weekly_candles (week_end_date);

CREATE TABLE benchmark_candles (
    index_name  text NOT NULL,             -- 'NIFTY500', 'NIFTY50'
    date        date NOT NULL,
    open        double precision,
    high        double precision,
    low         double precision,
    close       double precision NOT NULL,
    volume      bigint,
    PRIMARY KEY (index_name, date)
);

-- ---------------------------------------------------------------------------
-- Supertrend
-- ---------------------------------------------------------------------------
-- Full weekly series: chart overlay + lets a re-run of week W restart from W-1.
CREATE TABLE weekly_supertrend (
    symbol_id      integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    week_end_date  date NOT NULL,
    atr            double precision,
    upper_band     double precision,
    lower_band     double precision,
    st_value       double precision,
    direction      smallint CHECK (direction IN (1, -1)),   -- 1 bullish, -1 bearish
    PRIMARY KEY (symbol_id, week_end_date)
);
CREATE INDEX weekly_supertrend_week_idx ON weekly_supertrend (week_end_date);

-- Latest state per symbol for O(1) incremental update
CREATE TABLE supertrend_state (
    symbol_id       integer PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    week_end_date   date NOT NULL,
    atr_period      smallint NOT NULL,
    multiplier      real NOT NULL,
    close           double precision NOT NULL,          -- prev close for next TR
    atr             double precision,
    upper_band      double precision,
    lower_band      double precision,
    st_value        double precision,
    direction       smallint CHECK (direction IN (1, -1)),
    flip_date       date,
    flip_price      double precision,
    weeks_in_trend  integer,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Equal-weight sector index + RRG coordinates, full weekly history
CREATE TABLE sector_index_weekly (
    industry       text NOT NULL,
    week_end_date  date NOT NULL,
    index_value    double precision NOT NULL,
    bench_value    double precision,
    rs_ratio_raw   double precision,          -- sector / Nifty 500
    rs_ratio       double precision,          -- JdK RS-Ratio (~100)
    rs_momentum    double precision,          -- JdK RS-Momentum (~100)
    st_breadth     double precision,          -- % constituents weekly-ST bullish
    PRIMARY KEY (industry, week_end_date)
);

-- ---------------------------------------------------------------------------
-- Weekly scan snapshots (frontend reads only these)
-- ---------------------------------------------------------------------------
CREATE TABLE scan_runs (
    id              serial PRIMARY KEY,
    week_end_date   date NOT NULL UNIQUE,
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'complete', 'failed')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    duration_ms     integer,
    stocks_scanned  integer,
    qualified_count integer,
    notes           text,
    error           text,
    config          jsonb,                    -- config.yaml used (reproducibility)
    timings         jsonb                     -- per-step ms
);
CREATE INDEX scan_runs_complete_idx ON scan_runs (week_end_date DESC) WHERE status = 'complete';

CREATE TABLE stock_scan_results (
    run_id               integer NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    symbol_id            integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    week_end_date        date NOT NULL,
    symbol               text NOT NULL,
    name                 text,
    industry             text,
    index_list           text,                -- 'Nifty 100|Nifty Bank|...'
    history_days         integer,

    -- price / supertrend
    price                double precision,
    direction            text CHECK (direction IN ('Bullish', 'Bearish')),
    weeks_in_trend       integer,
    flip_date            date,
    flip_price           double precision,
    pct_since_flip       double precision,
    is_new_flip          boolean,             -- direction changed this week
    st_value             double precision,
    pct_from_st          double precision,    -- (close - st)/close*100 = risk to stop

    -- 52w / moving averages
    high_52w             double precision,
    low_52w              double precision,
    pct_from_high        double precision,
    pct_above_low        double precision,
    sma50                double precision,
    sma100               double precision,
    sma150               double precision,
    sma200               double precision,
    sma200_slope_pct     double precision,    -- sma200 vs 20 sessions ago, %
    sma200_rising        boolean,

    -- returns (%)
    ret_1w               double precision,
    ret_1m               double precision,
    ret_3m               double precision,
    ret_6m               double precision,
    ret_9m               double precision,
    ret_12m              double precision,

    -- liquidity / volatility
    avg_vol_20d          double precision,
    avg_turnover_20d_cr  double precision,
    vol_ratio            double precision,    -- this week vol / 20w avg
    flip_vol_ratio       double precision,    -- flip-week vol / 20w avg
    atr_pct              double precision,
    tightness_ratio      double precision,    -- recent weekly range / prior (VCP)
    delivery_pct_20d     double precision,
    delivery_pct_latest  double precision,
    market_cap_cr        double precision,

    -- relative strength
    rs_raw               double precision,
    rs_rating            smallint,            -- 1..99
    rs_vs_benchmark      double precision,
    rs_vs_sector         double precision,

    -- sector context
    sector_score         double precision,
    sector_rank          smallint,
    sector_tradeable     boolean,
    in_asm_gsm           boolean,

    -- hard filters
    f_supertrend         boolean,
    f_sector             boolean,
    f_trend_template     boolean,
    f_52w_range          boolean,
    f_rs                 boolean,
    f_liquidity          boolean,
    f_price              boolean,
    f_not_asm            boolean,
    qualified            boolean NOT NULL DEFAULT false,

    -- soft signals / penalties
    sig_fresh_flip       boolean,
    sig_tight_stop       boolean,
    sig_vol_confirm      boolean,
    sig_vcp              boolean,
    sig_accumulation     boolean,
    sig_near_high        boolean,
    sig_sector_leader    boolean,
    pen_overextended     boolean,
    pen_high_atr         boolean,
    pen_late_stage       boolean,

    -- score components (each 0..100 before weighting)
    score_rs             double precision,
    score_sector         double precision,
    score_freshness      double precision,
    score_near_high      double precision,
    score_volume         double precision,
    score_tightness      double precision,
    score_risk           double precision,
    penalty              double precision,
    stock_score          double precision,
    grade                text CHECK (grade IN ('A+', 'A', 'B', 'Watch')),
    reasons              text,

    -- trade plan
    stop_price           double precision,
    risk_pct             double precision,
    position_qty         integer,
    position_value       double precision,

    PRIMARY KEY (run_id, symbol_id)
);
CREATE INDEX stock_scan_results_industry_idx ON stock_scan_results (run_id, industry);
CREATE INDEX stock_scan_results_direction_idx ON stock_scan_results (run_id, direction);
CREATE INDEX stock_scan_results_score_idx ON stock_scan_results (run_id, stock_score DESC NULLS LAST);
CREATE INDEX stock_scan_results_symbol_idx ON stock_scan_results (symbol_id, run_id);

CREATE TABLE sector_scan_results (
    run_id             integer NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    industry           text NOT NULL,
    week_end_date      date NOT NULL,
    stock_count        integer NOT NULL,
    bullish_count      integer,
    ret_1w             double precision,
    ret_4w             double precision,
    ret_13w            double precision,
    ret_26w            double precision,
    ret_52w            double precision,
    rs_ratio_raw       double precision,
    rs_trend           double precision,      -- rs_ratio_raw / SMA10w(rs_ratio_raw)
    rs_ratio           double precision,      -- JdK
    rs_momentum        double precision,      -- JdK
    rrg_quadrant       text CHECK (rrg_quadrant IN ('Leading', 'Weakening', 'Lagging', 'Improving')),
    st_breadth         double precision,
    pct_above_sma50    double precision,
    pct_above_sma200   double precision,
    pct_near_high      double precision,
    breadth_change_4w  double precision,
    vol_26w            double precision,
    risk_adj_return    double precision,
    rank_ret_13w       double precision,
    rank_rs_trend      double precision,
    rank_st_breadth    double precision,
    rank_risk_adj      double precision,
    sector_score       double precision,
    sector_rank        smallint,
    tradeable          boolean NOT NULL DEFAULT false,
    PRIMARY KEY (run_id, industry)
);
CREATE INDEX sector_scan_results_rank_idx ON sector_scan_results (run_id, sector_rank);
CREATE INDEX sector_scan_results_industry_idx ON sector_scan_results (industry, run_id);

CREATE TABLE market_regime (
    run_id              integer PRIMARY KEY REFERENCES scan_runs(id) ON DELETE CASCADE,
    week_end_date       date NOT NULL,
    nifty500_close      double precision,
    nifty500_sma200     double precision,
    sma200_rising       boolean,
    above_200dma        boolean,
    nifty50_close       double precision,
    market_breadth_pct  double precision,     -- % universe weekly-ST bullish
    pct_above_sma200    double precision,
    regime              text NOT NULL CHECK (regime IN ('bull', 'neutral', 'bear'))
);

-- ---------------------------------------------------------------------------
-- Ops / data quality
-- ---------------------------------------------------------------------------
CREATE TABLE ingest_log (
    source      text NOT NULL,               -- 'bhavcopy', 'yf_backfill', 'benchmark', 'asm_gsm', ...
    trade_date  date NOT NULL,
    status      text NOT NULL CHECK (status IN ('ok', 'missing', 'failed', 'holiday')),
    rows        integer,
    message     text,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, trade_date)
);

CREATE TABLE corporate_actions (
    id           serial PRIMARY KEY,
    symbol_id    integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ex_date      date NOT NULL,
    action_type  text NOT NULL CHECK (action_type IN ('split', 'bonus', 'unknown')),
    ratio        double precision,
    detected_at  timestamptz NOT NULL DEFAULT now(),
    resolved     boolean NOT NULL DEFAULT false,   -- true once symbol re-backfilled
    UNIQUE (symbol_id, ex_date)
);

CREATE TABLE data_quality_log (
    id          bigserial PRIMARY KEY,
    symbol_id   integer REFERENCES symbols(id) ON DELETE CASCADE,
    date        date,
    source      text,
    issue       text NOT NULL,              -- 'missing_day', 'high_lt_low', 'negative_price', ...
    details     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX data_quality_log_created_idx ON data_quality_log (created_at DESC);
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS data_quality_log, corporate_actions, ingest_log, market_regime,
    sector_scan_results, stock_scan_results, scan_runs, sector_index_weekly,
    supertrend_state, weekly_supertrend, benchmark_candles, weekly_candles,
    daily_candles, exclusion_list, index_member_snapshots, index_members, indices,
    symbol_aliases, symbols CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
