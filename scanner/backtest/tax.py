"""Indian capital-gains tax on listed equity (STT paid), settled per financial year (April-March).

    short-term  held <= 12 months     long-term  held > 12 months, gains above a yearly exemption

Set-off: a short-term loss offsets short- or long-term gains; a long-term loss offsets only long-term
gains. Unused losses carry forward (the 8-year limit is ignored). Cess is added on top. Surcharge and
the 31-Jan-2018 grandfathering are ignored.

Regimes:
    current     today's rates for every year: STCG 20%, LTCG 12.5% above Rs 1.25 L (from 23-Jul-2024)
    historical  the rates of each year: STCG 15% and LTCG 0% until FY2017-18; LTCG 10% above Rs 1 L
                from FY2018-19; the current rates from FY2024-25 (most of that year's sales came after
                23-Jul-2024)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

CURRENT = (20.0, 12.5, 125_000.0)


def fy_of(d: pd.Timestamp) -> int:
    """Financial year by its starting calendar year (FY2024-25 -> 2024)."""
    return d.year if d.month >= 4 else d.year - 1


def rates(regime: str, fy: int) -> tuple[float, float, float]:
    """(STCG %, LTCG %, LTCG exemption Rs) for a financial year."""
    if regime == "current" or fy >= 2024:
        return CURRENT
    if fy >= 2018:
        return 15.0, 10.0, 100_000.0
    return 15.0, 0.0, 0.0


@dataclass
class TaxBook:
    regime: str
    cess_pct: float = 4.0
    carry_st: float = 0.0          # unused short-term loss (positive number)
    carry_lt: float = 0.0          # unused long-term loss
    realized: dict = field(default_factory=dict)   # fy -> [st, lt] net gains
    paid: dict = field(default_factory=dict)       # fy -> tax

    def add(self, entry: pd.Timestamp, exit_: pd.Timestamp, pnl: float) -> None:
        st_lt = self.realized.setdefault(fy_of(pd.Timestamp(exit_)), [0.0, 0.0])
        st_lt[1 if (pd.Timestamp(exit_) - pd.Timestamp(entry)).days > 365 else 0] += pnl

    def settle(self, before_fy: int | None = None) -> float:
        """Tax for every unsettled financial year before `before_fy` (all when None)."""
        due = 0.0
        for fy in sorted(self.realized):
            if before_fy is not None and fy >= before_fy:
                break
            st, lt = self.realized.pop(fy)
            due += self._year(fy, st, lt)
        return due

    def _year(self, fy: int, st: float, lt: float) -> float:
        if st < 0:                          # short-term loss -> long-term gains
            use = min(-st, max(lt, 0.0))
            lt -= use
            st += use
        if st < 0:
            self.carry_st += -st
            st = 0.0
        if lt < 0:
            self.carry_lt += -lt
            lt = 0.0
        use = min(self.carry_st, st)        # brought-forward short-term loss: short-term first...
        st -= use
        self.carry_st -= use
        use = min(self.carry_st, lt)        # ...then long-term
        lt -= use
        self.carry_st -= use
        use = min(self.carry_lt, lt)        # brought-forward long-term loss: long-term only
        lt -= use
        self.carry_lt -= use
        st_rate, lt_rate, exempt = rates(self.regime, fy)
        tax = (st * st_rate + max(lt - exempt, 0.0) * lt_rate) / 100 * (1 + self.cess_pct / 100)
        self.paid[fy] = self.paid.get(fy, 0.0) + tax
        return tax


def buy_and_hold_after_tax(start_value: float, end_value: float, regime: str = "current",
                           cess_pct: float = 4.0, end_fy: int = 2026) -> float:
    """Final value of a buy-and-hold (e.g. an index fund) sold at the end, held > 12 months."""
    _, lt_rate, exempt = rates(regime, end_fy)
    gain = end_value - start_value
    return end_value - max(gain - exempt, 0.0) * lt_rate / 100 * (1 + cess_pct / 100)
