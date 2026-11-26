"""Daily candlestick data with simple moving averages, one tab per ticker (Yahoo Finance chart data).

The page picks the visible period (1W, 1M, ... 1Y); candles are always daily. History is fetched far
enough back that the longest moving average is already valid at the start of the longest period.

options:
  tickers: [VOO, {symbol: QQQM, label: Invesco Nasdaq 100}]   # one tab each, in this order
  ma: [20, 50, 200]                                            # simple moving averages of the close
  periods: [1W, 1M, 3M, 6M, MTD, YTD, 1Y]                      # buttons shown (subset/order of these)
  default_period: 3M
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..core import CollectError, Context, http_get, iso
from . import source
from .stock import HOSTS

PERIODS = ["1W", "1M", "3M", "6M", "MTD", "YTD", "1Y"]
RANGE = "2y"   # >= 1 year shown + 200 trading days of warm-up for MA200


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_ohlc(payload: dict) -> dict:
    res = (payload.get("chart") or {}).get("result") or []
    if not res:
        err = (payload.get("chart") or {}).get("error") or {}
        raise CollectError(err.get("description") or "no chart data")
    r = res[0]
    m = r.get("meta") or {}
    tz = ZoneInfo(m.get("exchangeTimezoneName") or "America/New_York")
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close")]
    rows: dict[str, list] = {}
    for i, t in enumerate(r.get("timestamp") or []):
        o, h, l, c = (_f(col[i]) if i < len(col) else None for col in cols)
        if None in (o, h, l, c):
            continue  # incomplete candle (data gap)
        d = datetime.fromtimestamp(t, timezone.utc).astimezone(tz).date().isoformat()
        rows[d] = [d, round(o, 4), round(max(h, o, c), 4), round(min(l, o, c), 4), round(c, 4)]
    candles = [rows[d] for d in sorted(rows)]
    if not candles:
        raise CollectError("no complete daily candles")
    price = _f(m.get("regularMarketPrice"))
    mtime = m.get("regularMarketTime")
    if price is not None and mtime:
        mdate = datetime.fromtimestamp(mtime, timezone.utc).astimezone(tz).date().isoformat()
        last = candles[-1]
        if last[0] == mdate and last[4] != price:  # today's candle: close = latest price
            last[4] = round(price, 4)
            last[2] = max(last[2], last[4])
            last[3] = min(last[3], last[4])
    return {
        "symbol": m.get("symbol"),
        "name": m.get("longName") or m.get("shortName") or m.get("symbol"),
        "currency": m.get("currency") or "USD",
        "market_time": iso(datetime.fromtimestamp(mtime, timezone.utc)) if mtime else None,
        "candles": candles,
    }


def sma(values: list[float], n: int) -> list[float | None]:
    """Simple moving average; None until n values are available."""
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(round(total / n, 4) if i >= n - 1 else None)
    return out


def fetch_ohlc(symbol: str) -> dict:
    last = None
    for host in HOSTS:
        try:
            r = http_get(f"{host}/v8/finance/chart/{symbol}", params={"range": RANGE, "interval": "1d"},
                         browser=True, retries=1)
            return parse_ohlc(r.json())
        except (CollectError, ValueError) as e:
            last = e
    raise CollectError(f"{symbol}: {last}")


@source("candles", view="candles")
def collect(ctx: Context) -> dict:
    o = ctx.options
    tickers = []
    for t in o.get("tickers") or []:
        d = {"symbol": t} if isinstance(t, str) else dict(t)
        if not d.get("symbol"):
            raise CollectError("every ticker needs a symbol")
        d["symbol"] = str(d["symbol"]).upper()
        tickers.append(d)
    if not tickers:
        raise CollectError("options.tickers is required")
    mas = [int(n) for n in o.get("ma", [20, 50, 200])]
    if any(n < 2 for n in mas):
        raise CollectError("moving averages must be 2 days or longer")
    periods = [str(p).upper() for p in o.get("periods", PERIODS)]
    bad = [p for p in periods if p not in PERIODS]
    if bad or not periods:
        raise CollectError(f"unknown period(s) {bad}; choose from {PERIODS}")
    default = str(o.get("default_period", "3M")).upper()
    if default not in periods:
        default = periods[0]

    prev = {t.get("symbol"): t for t in (ctx.previous or {}).get("tabs", [])}
    tabs, ok = [], 0
    for i, t in enumerate(tickers):
        if i:
            time.sleep(0.25)
        try:
            q = fetch_ohlc(t["symbol"])
            closes = [c[4] for c in q["candles"]]
            q["ma"] = {str(n): sma(closes, n) for n in mas}
            ok += 1
        except Exception as e:  # noqa: BLE001
            ctx.warn(str(e))
            if t["symbol"] not in prev:
                continue
            q = {**prev[t["symbol"]], "stale": True}
        q["label"] = t.get("label") or t["symbol"]
        tabs.append(q)
    if not ok:
        raise CollectError("; ".join(ctx.warnings) or "no data")
    return {"periods": periods, "default_period": default, "ma": mas, "tabs": tabs}
