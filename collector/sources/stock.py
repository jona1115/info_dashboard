"""Stock / ETF / index quotes + daily closing-price history (Yahoo Finance chart data).

options:
  tickers: [INTC, {symbol: "^GSPC", label: S&P 500}]   # plain symbols or {symbol, label}
  # ...or several labelled sections instead of tickers:
  groups: [{label: Indexes, tickers: [...]}, {label: Funds, tickers: [...]}]
  range: 6mo          # 1mo, 3mo, 6mo, 1y, 2y, 5y
  display: cards      # cards (big price + chart each) | table (compact rows with sparklines)
  sort: none          # none | day  (table rows by today's % change, best first)
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..core import CollectError, Context, http_get, iso
from . import source

HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y"}


def parse_chart(payload: dict) -> dict:
    res = (payload.get("chart") or {}).get("result") or []
    if not res:
        err = (payload.get("chart") or {}).get("error") or {}
        raise CollectError(err.get("description") or "no chart data")
    r = res[0]
    m = r.get("meta") or {}
    tz = ZoneInfo(m.get("exchangeTimezoneName") or "America/New_York")
    ts = r.get("timestamp") or []
    closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    points = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, timezone.utc).astimezone(tz).date().isoformat()
        if points and points[-1][0] == d:
            points[-1][1] = round(c, 4)
        else:
            points.append([d, round(c, 4)])
    price = m.get("regularMarketPrice")
    if price is None:
        if not points:
            raise CollectError("no price")
        price = points[-1][1]
    mtime = m.get("regularMarketTime")
    mdate = datetime.fromtimestamp(mtime, timezone.utc).astimezone(tz).date().isoformat() if mtime else None
    # Make the series end at the current price, then the previous close is the point before.
    if mdate:
        if points and points[-1][0] == mdate:
            points[-1][1] = round(price, 4)
        elif not points or points[-1][0] < mdate:
            points.append([mdate, round(price, 4)])
    prev = points[-2][1] if len(points) >= 2 else None
    change = round(price - prev, 4) if prev is not None else None
    pct = round(change / prev * 100, 3) if prev else None
    return {
        "symbol": m.get("symbol"),
        "type": m.get("instrumentType"),
        "name": m.get("longName") or m.get("shortName") or m.get("symbol"),
        "currency": m.get("currency") or "USD",
        "exchange": m.get("fullExchangeName") or m.get("exchangeName"),
        "price": round(price, 4),
        "prev_close": prev,
        "change": change,
        "change_pct": pct,
        "market_time": iso(datetime.fromtimestamp(mtime, timezone.utc)) if mtime else None,
        "chg_1m_pct": _pct_since(points, 30),
        "chg_range_pct": round((price / points[0][1] - 1) * 100, 3) if points and points[0][1] else None,
        "closes": points,
    }


def _pct_since(points: list[list], days: int) -> float | None:
    """% change from the last close on/before (last date - days) to the last close."""
    if len(points) < 2:
        return None
    target = (date.fromisoformat(points[-1][0]) - timedelta(days=days)).isoformat()
    base = None
    for d, v in points:
        if d <= target:
            base = v
        else:
            break
    if not base:
        return None
    return round((points[-1][1] / base - 1) * 100, 3)


def fetch_quote(symbol: str, rng: str) -> dict:
    last = None
    for host in HOSTS:
        try:
            r = http_get(f"{host}/v8/finance/chart/{symbol}", params={"range": rng, "interval": "1d"},
                         browser=True, retries=1)
            return parse_chart(r.json())
        except (CollectError, ValueError) as e:
            last = e
    raise CollectError(f"{symbol}: {last}")


def _norm_tickers(items) -> list[dict]:
    out = []
    for t in items or []:
        d = {"symbol": t} if isinstance(t, str) else dict(t)
        if not d.get("symbol"):
            raise CollectError("every ticker needs a symbol")
        d["symbol"] = str(d["symbol"]).upper()
        out.append(d)
    return out


@source("stock", view="stock")
def collect(ctx: Context) -> dict:
    o = ctx.options
    if o.get("groups"):
        groups = [{"label": g.get("label"), "tickers": _norm_tickers(g.get("tickers"))} for g in o["groups"]]
    else:
        groups = [{"label": None, "tickers": _norm_tickers(o.get("tickers"))}]
    if not any(g["tickers"] for g in groups):
        raise CollectError("options.tickers (or groups) is required")
    rng = str(o.get("range", "6mo"))
    if rng not in RANGES:
        raise CollectError(f"range must be one of {sorted(RANGES)}")
    display = o.get("display", "cards")
    if display not in ("cards", "table"):
        raise CollectError("display must be cards or table")
    prev = {}
    for g in (ctx.previous or {}).get("groups", []):
        for q in g.get("quotes", []):
            prev[q.get("symbol")] = q
    out, ok, first = [], 0, True
    for g in groups:
        quotes = []
        for t in g["tickers"]:
            if not first:
                time.sleep(0.25)  # be gentle with many tickers
            first = False
            try:
                q = fetch_quote(t["symbol"], rng)
                ok += 1
            except Exception as e:  # noqa: BLE001
                ctx.warn(str(e))
                if t["symbol"] not in prev:
                    continue
                q = {**prev[t["symbol"]], "stale": True}
            q["label"] = t.get("label") or q.get("name") or t["symbol"]
            quotes.append(q)
        if o.get("sort") == "day":
            quotes.sort(key=lambda q: q.get("change_pct") if q.get("change_pct") is not None else -1e9, reverse=True)
        out.append({"label": g["label"], "quotes": quotes})
    if not ok:
        raise CollectError("no quotes could be fetched")
    return {"range": rng, "display": display, "groups": out}
