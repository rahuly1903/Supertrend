"""Small shared helpers: IST clock, step timer."""
from __future__ import annotations

import datetime as dt
import logging
import time
from contextlib import contextmanager
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def today_ist() -> dt.date:
    return now_ist().date()


def last_complete_session(ready_hhmm: str = "16:00") -> dt.date:
    """Latest date whose daily candle is final: today after `ready_hhmm` IST, else yesterday."""
    now = now_ist()
    hh, mm = map(int, ready_hhmm.split(":"))
    return now.date() if (now.hour, now.minute) >= (hh, mm) else now.date() - dt.timedelta(days=1)


@contextmanager
def timed(log: logging.Logger, step: str, timings: dict | None = None):
    t0 = time.perf_counter()
    yield
    ms = (time.perf_counter() - t0) * 1000
    if timings is not None:
        timings[step] = round(ms)
    log.info("%s done in %.0f ms", step, ms)
