"""Market sentiment: CNN Fear & Greed Index (+ its 7 parts), crypto Fear & Greed, and volatility indexes.

Sources: CNN's Fear & Greed data feed (production.dataviz.cnn.io), alternative.me's crypto
Fear & Greed Index, and Yahoo Finance chart data for the volatility indexes.

options:
  days: 365                 # history kept for the chart (the page offers 1M / 3M / 6M / 1Y)
  crypto: true              # also plot the crypto Fear & Greed Index (same 0-100 scale)
  volatility:               # Yahoo symbols; the first three of the same unit share the chart tab
    - {symbol: "^VIX", label: VIX}
    - {symbol: "^VIX3M", label: VIX 3-month}
  term: ["^VIX", "^VIX3M"]  # ratio > 1 = near-term fear above longer-term (backwardation)

Each part fails on its own: a failed part keeps its last good data (marked stale) and adds a
warning; the widget fails only when nothing at all could be read.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..core import CollectError, Context, http_get, iso
from . import source
from .stock import _norm_tickers, fetch_quote

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"
CRYPTO_URL = "https://api.alternative.me/fng/"
NY = ZoneInfo("America/New_York")

# CNN's seven indicators, in the order CNN lists them.
COMPONENTS = [
    ("market_momentum_sp500", "Market momentum", "S&P 500 vs its 125-day average"),
    ("stock_price_strength", "Stock price strength", "NYSE 52-week highs vs lows"),
    ("stock_price_breadth", "Stock price breadth", "McClellan volume summation index"),
    ("put_call_options", "Put and call options", "5-day put/call ratio"),
    ("market_volatility_vix", "Market volatility", "VIX vs its 50-day average"),
    ("safe_haven_demand", "Safe haven demand", "Stocks vs bonds, 20-day returns"),
    ("junk_bond_demand", "Junk bond demand", "Junk vs investment-grade yield spread"),
]




def rating_for(score: float) -> str:
    """CNN's bands: 0-24 extreme fear, 25-44 fear, 45-55 neutral, 56-75 greed, 76-100 extreme greed."""
    s = round(score)
    if s < 25:
        return "Extreme fear"
    if s < 45:
        return "Fear"
    if s <= 55:
        return "Neutral"
    if s <= 75:
        return "Greed"
    return "Extreme greed"


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rating(text) -> str | None:
    return str(text).strip().capitalize() if text else None


def _ms_day(ms: float, tz=NY) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(tz).date().isoformat()


def _cnn_day(ms: float) -> str:
    """CNN stamps each day's close at 00:00 UTC of that trading day; the live reading has its real time."""
    dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
    if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
        return dt.date().isoformat()
    return dt.astimezone(NY).date().isoformat()


def parse_cnn(payload: dict) -> dict:
    fg = payload.get("fear_and_greed") or {}
    score = _num(fg.get("score"))
    if score is None:
        raise CollectError("CNN Fear & Greed: no current score")
    by_day: dict[str, float] = {}
    for p in (payload.get("fear_and_greed_historical") or {}).get("data") or []:
        x, y = _num(p.get("x")), _num(p.get("y"))
        if x is not None and y is not None:
            by_day[_cnn_day(x)] = round(y, 2)   # several points a day: the latest wins
    ts = fg.get("timestamp")
    try:
        today = datetime.fromisoformat(ts).astimezone(NY).date().isoformat() if ts else None
    except ValueError:
        today = None
    if today:
        by_day[today] = round(score, 2)
        # CNN pads market holidays with a copy of the last close dated after the live reading: drop it.
        by_day = {d: v for d, v in by_day.items() if d <= today}
    prev = {k: (round(v, 2) if (v := _num(fg.get(f"previous_{k}"))) is not None else None)
            for k in ("close", "1_week", "1_month", "1_year")}
    comps = []
    for key, label, what in COMPONENTS:
        c = payload.get(key) or {}
        s = _num(c.get("score"))
        if s is None:
            continue
        comps.append({"key": key, "label": label, "what": what, "score": round(s, 1),
                      "rating": _rating(c.get("rating")) or rating_for(s)})
    return {
        "score": round(score, 2),
        "rating": _rating(fg.get("rating")) or rating_for(score),
        "time": ts,
        "previous": prev,
        "history": [[d, by_day[d]] for d in sorted(by_day)],
        "components": comps,
    }


def fetch_cnn(start: str) -> dict:
    r = http_get(CNN_URL.format(start=start), browser=True, retries=1,
                 headers={"Accept": "application/json", "Referer": "https://www.cnn.com/markets/fear-and-greed"})
    try:
        return parse_cnn(r.json())
    except ValueError as e:
        raise CollectError(f"CNN Fear & Greed: bad JSON ({e})") from e


def parse_crypto(payload: dict) -> dict:
    rows = []
    for p in payload.get("data") or []:
        v, t = _num(p.get("value")), _num(p.get("timestamp"))
        if v is not None and t is not None:
            rows.append([_ms_day(t * 1000, timezone.utc), v, p.get("value_classification")])
    if not rows:
        raise CollectError("crypto Fear & Greed: no data")
    rows.sort()
    last = rows[-1]
    return {"score": last[1], "rating": _rating(last[2]) or rating_for(last[1]),
            "history": [[d, v] for d, v, _ in rows]}


def fetch_crypto(days: int) -> dict:
    r = http_get(CRYPTO_URL, params={"limit": days, "format": "json"}, retries=1)
    try:
        return parse_crypto(r.json())
    except ValueError as e:
        raise CollectError(f"crypto Fear & Greed: bad JSON ({e})") from e


def _yahoo_range(days: int) -> str:
    for rng, d in (("3mo", 92), ("6mo", 183), ("1y", 366), ("2y", 731)):
        if days <= d:
            return rng
    return "5y"


@source("sentiment", view="sentiment")
def collect(ctx: Context) -> dict:
    o = ctx.options
    days = int(o.get("days", 365))
    if not 30 <= days <= 1500:
        raise CollectError("options.days must be between 30 and 1500")
    since = (ctx.today() - timedelta(days=days)).isoformat()
    prev = ctx.previous or {}
    parts_ok = 0

    # CNN Fear & Greed
    try:
        cnn = fetch_cnn(since)
        parts_ok += 1
    except CollectError as e:
        ctx.warn(str(e))
        cnn = {**prev["cnn"], "stale": True} if prev.get("cnn") else None

    # Crypto Fear & Greed
    crypto = None
    if o.get("crypto", True):
        try:
            crypto = fetch_crypto(days)
            parts_ok += 1
        except CollectError as e:
            ctx.warn(str(e))
            crypto = {**prev["crypto"], "stale": True} if prev.get("crypto") else None

    # Volatility indexes
    vol_cfg = _norm_tickers(o.get("volatility") or [{"symbol": "^VIX", "label": "VIX"}])
    old_vol = {q.get("symbol"): q for q in prev.get("volatility") or []}
    rng = _yahoo_range(days)
    vol = []
    for i, t in enumerate(vol_cfg):
        if i:
            time.sleep(0.25)
        try:
            q = fetch_quote(t["symbol"], rng)
            q = {k: q[k] for k in ("symbol", "name", "price", "change", "change_pct", "market_time", "closes")}
            parts_ok += 1
        except CollectError as e:
            ctx.warn(str(e))
            if t["symbol"] not in old_vol:
                continue
            q = {**old_vol[t["symbol"]], "stale": True}
        q["label"] = t.get("label") or t["symbol"]
        q["note"] = t.get("note")
        vol.append(q)

    if not parts_ok:
        raise CollectError("; ".join(ctx.warnings) or "no sentiment data")

    term = None
    pair = [str(s).upper() for s in o.get("term", ["^VIX", "^VIX3M"])]
    got = {q["symbol"]: q for q in vol}
    if len(pair) == 2 and all(s in got for s in pair) and got[pair[1]]["price"]:
        a, b = got[pair[0]], got[pair[1]]
        ratio = round(a["price"] / b["price"], 3)
        term = {"near": a["label"], "far": b["label"], "ratio": ratio,
                "state": "backwardation" if ratio > 1 else "contango"}

    for part in (cnn, crypto):   # keep the configured window only
        if part:
            part["history"] = [p for p in part["history"] if p[0] >= since]
    for q in vol:
        q["closes"] = [p for p in q["closes"] if p[0] >= since]

    return {"days": days, "cnn": cnn, "crypto": crypto, "volatility": vol, "term": term,
            "as_of": iso(datetime.now(timezone.utc))}
