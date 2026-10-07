"""Scanner CLI.

    python main.py migrate            # apply Alembic migrations (alembic upgrade head)
    python main.py db-status          # row counts per table + current revision
    python main.py universe           # Nifty 500 list, index memberships, share counts (monthly)
    python main.py shares             # share counts only
    python main.py exclusions         # NSE ASM/GSM lists (weekly)
    python main.py backfill           # yfinance daily history, incremental
    python main.py backfill --symbols TCS,INFY --full   # wipe + re-download
    python main.py backfill --years 12 --extend         # prepend older history
    python main.py supertrend         # weekly candles + weekly Supertrend (incremental)
    python main.py supertrend --full  # recompute everything from daily candles
    python main.py weekly             # candles + Supertrend + full scan of the latest week
    python main.py weekly --history 26         # (re)build the last 26 weekly snapshots
    python main.py weekly --week 2026-06-26    # re-run one week
    python main.py export [--week D] [--extended] [-o out.csv]   # scanner CSV
    python main.py daily [--days N] [--date D]   # bhavcopy ingest (cron Mon-Fri 18:45 IST)
    python main.py repair-gaps        # fill historical gaps from bhavcopy
    python main.py weekly --ingest    # the Saturday cron: catch-up + ASM/GSM + guarded scan
    python main.py full-rescan [--weeks 52]      # full candle/ST recompute + rebuild snapshots
    python main.py backtest [--name N]           # default parameters from config.yaml
    python main.py backtest --sweep              # grid in backtest.sweep + walk-forward -> backtest_* tables
    python main.py walk-forward --sweep 4        # redo only the walk-forward of a stored sweep
    python main.py experiments [--only k1,k2]    # re-run + save the experiment log (web: /backtests)
    python main.py pit-download [--start 2013-01-01]         # NSE bhavcopy archive -> data_cache/
    SCANNER_CONFIG=config_pit.yaml DATABASE_URL=.../supertrend_pit python main.py pit-build   # survivorship-free DB
    python main.py monte-carlo [--runs 500] [--skip-pct 20]   # random signal order -> outcome distribution

Later steps add: | full-rescan | backtest
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from config import get_config
from settings import get_settings

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("scanner")


def setup_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)


def cmd_migrate(_args: argparse.Namespace) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    log.info("migrations applied")


def cmd_db_status(_args: argparse.Namespace) -> None:
    import db

    with db.connection() as conn:
        rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = [
            r[0]
            for r in conn.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
                     AND table_name <> 'alembic_version' ORDER BY table_name"""
            ).fetchall()
        ]
        print(f"alembic revision: {rev[0] if rev else '-'}")
        for t in tables:
            n = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            print(f"  {t:<24} {n:>12,}")
    db.close_pool()


def _print(result: dict) -> None:
    print(json.dumps(result, indent=2, default=str))


def cmd_universe(args: argparse.Namespace) -> None:
    from jobs.universe import refresh_universe

    _print(refresh_universe(get_config(), with_shares=not args.skip_shares))


def cmd_shares(_args: argparse.Namespace) -> None:
    from jobs.universe import refresh_shares

    _print(refresh_shares(get_config()))


def cmd_exclusions(_args: argparse.Namespace) -> None:
    from jobs.universe import refresh_exclusions

    _print(refresh_exclusions(get_config()))


def cmd_backfill(args: argparse.Namespace) -> None:
    from jobs.backfill import run_backfill

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    _print(run_backfill(get_config(), years=args.years, symbols=symbols, full=args.full,
                       extend=args.extend))


def cmd_supertrend(args: argparse.Namespace) -> None:
    import datetime as dt

    from jobs.weekly_scan import run_supertrend

    cutoff = dt.date.fromisoformat(args.asof) if args.asof else None
    _print(run_supertrend(get_config(), full=args.full, cutoff=cutoff))


def cmd_weekly(args: argparse.Namespace) -> None:
    import datetime as dt

    from jobs.weekly_scan import run_weekly

    week = dt.date.fromisoformat(args.week) if args.week else None
    _print(run_weekly(get_config(), week=week, history=args.history, full=args.full, ingest=args.ingest))


def cmd_export(args: argparse.Namespace) -> None:
    import db
    from engine.export import to_csv

    with db.connection() as conn:
        run = conn.execute(
            """SELECT id, week_end_date FROM scan_runs WHERE status = 'complete'
               AND (%(w)s::date IS NULL OR week_end_date BETWEEN %(w)s::date AND %(w)s::date + 6)
               ORDER BY week_end_date DESC LIMIT 1""", {"w": args.week}).fetchone()
        if not run:
            raise SystemExit("no complete scan run found")
        stocks = db.read_frame(conn, "SELECT * FROM stock_scan_results WHERE run_id = %s", (run[0],))
    csv = to_csv(stocks, extended=args.extended)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(csv)
        log.info("wrote %s (%d rows, week %s)", args.output, len(stocks), run[1])
    else:
        sys.stdout.write(csv)


def cmd_daily(args: argparse.Namespace) -> None:
    import datetime as dt

    from jobs.daily_ingest import run_daily

    dates = [dt.date.fromisoformat(d) for d in args.date] if args.date else None
    _print(run_daily(get_config(), days=args.days, dates=dates))


def cmd_repair_gaps(_args: argparse.Namespace) -> None:
    from jobs.daily_ingest import run_repair_gaps

    _print(run_repair_gaps(get_config()))


def cmd_full_rescan(args: argparse.Namespace) -> None:
    from jobs.weekly_scan import run_weekly

    _print(run_weekly(get_config(), history=args.weeks, full=True))


def cmd_walk_forward(args: argparse.Namespace) -> None:
    from backtest.runner import run_walk_forward

    _print(run_walk_forward(get_config(), args.sweep))


def cmd_experiments(args: argparse.Namespace) -> None:
    from backtest.experiments import run_experiments

    _print(run_experiments(get_config(), only=args.only.split(",") if args.only else None))


def cmd_pit_download(args: argparse.Namespace) -> None:
    import datetime as dt

    from sources import bhav_archive

    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    _print(bhav_archive.download(dt.date.fromisoformat(args.start), end, workers=args.workers))


def cmd_pit_build(args: argparse.Namespace) -> None:
    import datetime as dt

    from jobs.pit_universe import build

    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    _print(build(get_config(), args.source, dt.date.fromisoformat(args.start), end, args.top, args.min_traded_pct))


def cmd_monte_carlo(args: argparse.Namespace) -> None:
    from backtest.montecarlo import run_monte_carlo

    _print(run_monte_carlo(get_config(), runs=args.runs, skip_pct=args.skip_pct, jobs=args.jobs,
                           save=not args.no_save))


def cmd_backtest(args: argparse.Namespace) -> None:
    from backtest.runner import run_backtest

    _print(run_backtest(get_config(), sweep=args.sweep, name=args.name, jobs=args.jobs))


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    parser = argparse.ArgumentParser(prog="scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply DB migrations").set_defaults(func=cmd_migrate)
    sub.add_parser("db-status", help="row counts per table").set_defaults(func=cmd_db_status)

    p = sub.add_parser("universe", help="refresh Nifty 500 list + index memberships (+ shares)")
    p.add_argument("--skip-shares", action="store_true")
    p.set_defaults(func=cmd_universe)
    sub.add_parser("shares", help="refresh shares outstanding").set_defaults(func=cmd_shares)
    sub.add_parser("exclusions", help="refresh ASM/GSM lists").set_defaults(func=cmd_exclusions)

    p = sub.add_parser("backfill", help="incremental yfinance daily backfill")
    p.add_argument("--years", type=int, help="history depth for symbols with no data")
    p.add_argument("--symbols", help="comma-separated subset")
    p.add_argument("--full", action="store_true", help="wipe + re-download --symbols")
    p.add_argument("--extend", action="store_true", help="prepend history back to --years before the first candle")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("supertrend", help="weekly candles + weekly Supertrend")
    p.add_argument("--full", action="store_true", help="full recompute")
    p.add_argument("--asof", help="treat this date as the last closed session (YYYY-MM-DD)")
    p.set_defaults(func=cmd_supertrend)

    p = sub.add_parser("weekly", help="weekly scan -> snapshot tables")
    p.add_argument("--week", help="scan the stored week containing this date (YYYY-MM-DD)")
    p.add_argument("--history", type=int, default=0, help="scan the last N weeks")
    p.add_argument("--full", action="store_true", help="full candle + Supertrend recompute first")
    p.add_argument("--ingest", action="store_true",
                   help="catch up daily data + ASM/GSM first, and guard the latest week (cron mode)")
    p.set_defaults(func=cmd_weekly)

    p = sub.add_parser("daily", help="bhavcopy ingest + benchmarks + corporate actions")
    p.add_argument("--days", type=int, help="re-process the last N calendar days (delivery backfill)")
    p.add_argument("--date", action="append", help="specific date(s) YYYY-MM-DD")
    p.set_defaults(func=cmd_daily)
    sub.add_parser("repair-gaps", help="fill missing sessions from bhavcopy").set_defaults(func=cmd_repair_gaps)
    p = sub.add_parser("full-rescan", help="full recompute + rebuild weekly snapshots")
    p.add_argument("--weeks", type=int, default=52)
    p.set_defaults(func=cmd_full_rescan)

    p = sub.add_parser("backtest", help="weekly-rebalance backtest (single or sweep)")
    p.add_argument("--sweep", action="store_true", help="run the parameter grid in backtest.sweep")
    p.add_argument("--name", help="label for the run / sweep")
    p.add_argument("--jobs", type=int, help="parallel simulation processes (default: CPUs - 1)")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("experiments", help="re-run the experiment log (backtest/experiments.py) and save each run")
    p.add_argument("--only", help="comma-separated experiment keys")
    p.set_defaults(func=cmd_experiments)

    p = sub.add_parser("pit-download", help="download the NSE CM bhavcopy archive (all stocks) to data_cache/")
    p.add_argument("--start", default="2013-01-01")
    p.add_argument("--end")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(func=cmd_pit_download)
    p = sub.add_parser("pit-build", help="point-in-time universe DB from the archive (empty DB, config_pit.yaml)")
    p.add_argument("--source", default="postgresql://localhost:5432/supertrend",
                   help="live DB to copy industries, share counts and benchmarks from")
    p.add_argument("--start", default="2013-01-01")
    p.add_argument("--end")
    p.add_argument("--top", type=int, default=750, help="universe size per snapshot")
    p.add_argument("--min-traded-pct", type=float, default=80, help="min %% of sessions traded in the window")
    p.set_defaults(func=cmd_pit_build)

    p = sub.add_parser("monte-carlo", help="re-run the default with a random signal order N times")
    p.add_argument("--runs", type=int, default=500)
    p.add_argument("--skip-pct", type=float, default=0, help="also miss this %% of signals at random")
    p.add_argument("--jobs", type=int, help="parallel simulation processes (default: CPUs - 1)")
    p.add_argument("--no-save", action="store_true", help="print only; don't store the representative runs")
    p.set_defaults(func=cmd_monte_carlo)

    p = sub.add_parser("walk-forward", help="recompute a stored sweep's walk-forward (backtest.walk_forward)")
    p.add_argument("--sweep", type=int, required=True, help="sweep id")
    p.set_defaults(func=cmd_walk_forward)

    p = sub.add_parser("export", help="scanner CSV for a completed run")
    p.add_argument("--week", help="week containing this date (default: latest complete)")
    p.add_argument("--extended", action="store_true", help="append extended columns")
    p.add_argument("-o", "--output", help="file path (default: stdout)")
    p.set_defaults(func=cmd_export)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
