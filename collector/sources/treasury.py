"""U.S. Treasury par yield curve (daily, from home.treasury.gov), with 1-month / 1-year comparisons
and the 10Y-2Y and 10Y-3M spreads.

options:
  compare_days: [30, 365]     # extra curves: the last trading day on/before N days earlier
  spread_days: 365            # length of the spread trend lines
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

from ..core import CollectError, Context, attempt, http_get
from . import source

URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{y}/all"
# column label in Treasury's CSV -> (short label, maturity in months)
TENORS = {
    "1 Mo": ("1M", 1), "1.5 Month": ("6W", 1.5), "2 Mo": ("2M", 2), "3 Mo": ("3M", 3), "4 Mo": ("4M", 4),
    "6 Mo": ("6M", 6), "1 Yr": ("1Y", 12), "2 Yr": ("2Y", 24), "3 Yr": ("3Y", 36), "5 Yr": ("5Y", 60),
    "7 Yr": ("7Y", 84), "10 Yr": ("10Y", 120), "20 Yr": ("20Y", 240), "30 Yr": ("30Y", 360),
}


def parse_csv(text: str) -> dict[str, dict[str, float]]:
    """-> {'2026-11-20': {'1M': 4.19, ...}}"""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            d = datetime.strptime(row["Date"], "%m/%d/%Y").date().isoformat()
        except (KeyError, ValueError):
            continue
        vals = {}
        for col, (short, _) in TENORS.items():
            try:
                vals[short] = float(row[col])
            except (KeyError, TypeError, ValueError):
                pass
        if vals:
            out[d] = vals
    return out


def fetch_year(y: int) -> dict[str, dict[str, float]]:
    r = http_get(URL.format(y=y), params={
        "type": "daily_treasury_yield_curve", "field_tdr_date_value": y, "page": "", "_format": "csv"}, browser=True)
    return parse_csv(r.text)


def ago_label(n: int) -> str:
    if n % 365 == 0:
        k, unit = n // 365, "year"
    elif n % 30 == 0:
        k, unit = n // 30, "month"
    else:
        k, unit = n, "day"
    return f"{k} {unit}{'s' if k != 1 else ''} ago"


def on_or_before(days: dict, target: str) -> str | None:
    cands = [d for d in days if d <= target]
    return max(cands) if cands else None


@source("treasury_curve", view="yield_curve")
def collect(ctx: Context) -> dict:
    o = ctx.options
    compare = [int(x) for x in o.get("compare_days", [30, 365])]
    spread_days = int(o.get("spread_days", 365))
    today = ctx.today()
    need = (today - timedelta(days=max(compare + [spread_days]) + 10)).year
    days: dict[str, dict[str, float]] = {}
    for y in range(need, today.year + 1):
        got = attempt(ctx, f"Treasury {y}", lambda y=y: fetch_year(y))
        if got:
            days.update(got)
        elif got is None and y == today.year:  # download failed (an empty early-January file is fine)
            raise CollectError("; ".join(ctx.warnings))
    if not days:
        raise CollectError("no Treasury data")
    latest = max(days)
    order = sorted({v for v in TENORS.values()}, key=lambda t: t[1])
    tenors = [t[0] for t in order if t[0] in days[latest]]

    curves = [{"date": latest, "label": "Latest", "values": [days[latest].get(t) for t in tenors]}]
    for n in compare:
        d = on_or_before(days, (date.fromisoformat(latest) - timedelta(days=n)).isoformat())
        if d:
            curves.append({"date": d, "label": ago_label(n), "values": [days[d].get(t) for t in tenors]})

    since = (date.fromisoformat(latest) - timedelta(days=spread_days)).isoformat()
    spreads = []
    for name, a, b in (("10Y − 2Y", "10Y", "2Y"), ("10Y − 3M", "10Y", "3M")):
        pts = [[d, round(v[a] - v[b], 3)] for d, v in sorted(days.items()) if d >= since and a in v and b in v]
        if pts:
            spreads.append({"label": name, "points": pts})
    return {"tenors": tenors, "curves": curves, "spreads": spreads}
