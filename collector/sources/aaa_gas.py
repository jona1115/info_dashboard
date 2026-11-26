"""AAA average gas prices (national / state / metro), with history.

AAA only publishes today's averages (plus yesterday / week / month / year ago),
so every run records today's value into data/history/<widget id>.csv. Weekly
EIA retail prices are fetched as a backfill so the chart has history from day one.

options:
  regions:
    - {key: national, label: U.S.}
    - {key: TX, label: Texas}                                # a state (2-letter code)
    - {key: TX:Austin-San Marcos, label: Austin}             # a metro on that state's page
  chart_grade: regular       # regular | mid | premium | diesel
  window_days: 180
  eia:                       # optional weekly backfill, region key -> EIA series id
    national: EMM_EPMR_PTE_NUS_DPG
    TX: EMM_EPMR_PTE_STX_DPG
  # EIA key: env EIA_API_KEY (free at https://www.eia.gov/opendata/); falls back to DEMO_KEY.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..core import CollectError, Context, attempt, clean_text, http_get
from . import source

AAA = "https://gasprices.aaa.com/"
EIA = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"
EIA_HTML = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx"
EIA_REUSE = timedelta(hours=20)     # weekly data: no need to fetch it on every run
EIA_STALE_DAYS = 10                 # only warn once a weekly release has clearly been missed
GRADES = {"regular": "regular", "mid-grade": "mid", "mid": "mid", "premium": "premium", "diesel": "diesel", "e85": "e85"}
ROWS = {"current avg.": "current", "yesterday avg.": "yesterday", "week ago avg.": "week_ago",
        "month ago avg.": "month_ago", "year ago avg.": "year_ago"}
_PRICE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)")
_ASOF = re.compile(r"Price as of\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", re.I)


def parse_table(table) -> dict:
    """AAA price table -> {'current': {'regular': 3.1, ...}, 'yesterday': {...}, ...}."""
    head = [clean_text(th.get_text()).lower() for th in table.select("thead th")]
    cols = [GRADES.get(h) for h in head[1:]]
    out = {}
    for tr in table.select("tbody tr"):
        tds = [clean_text(td.get_text()) for td in tr.find_all("td")]
        if not tds:
            continue
        key = ROWS.get(tds[0].lower())
        if not key:
            continue
        row = {}
        for g, cell in zip(cols, tds[1:]):
            m = _PRICE.search(cell)
            if g and m:
                row[g] = float(m.group(1))
        out[key] = row
    if not out.get("current"):
        raise CollectError("price table had no current row")
    return out


def parse_main(html: str) -> tuple[dict, date | None]:
    """The page's headline table (national on the home page, the state on ?state=XX)."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1.nati")
    table = h1.find_next("table") if h1 else None
    if table is None:
        raise CollectError("AAA page layout changed: headline price table not found")
    return parse_table(table), parse_asof(html)


def parse_metro(html: str, name: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for h3 in soup.select("h3[data-title]"):
        if clean_text(h3.get_text()).lower() == name.lower():
            table = h3.find_next("table")
            if table is not None:
                return parse_table(table)
    raise CollectError(f"metro {name!r} not found on AAA page")


def parse_asof(html: str) -> date | None:
    m = _ASOF.search(html)
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def fetch_eia_api(series_id: str, start: date) -> list[list]:
    key = os.environ.get("EIA_API_KEY") or "DEMO_KEY"
    r = http_get(EIA, params={
        "api_key": key, "frequency": "weekly", "data[0]": "value",
        "facets[series][]": series_id, "start": start.isoformat(),
        "sort[0][column]": "period", "sort[0][direction]": "asc", "length": 5000,
    }, timeout=30, retries=0)
    rows = (r.json().get("response") or {}).get("data") or []
    pts = []
    for row in rows:
        try:
            pts.append([row["period"], round(float(row["value"]), 4)])
        except (KeyError, TypeError, ValueError):
            continue
    if not pts:
        raise CollectError(f"EIA API returned no data for {series_id}")
    return pts


MONTH = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def parse_eia_history(html: str) -> list[list]:
    """EIA's weekly history page: rows of "YYYY-Mon" followed by (MM/DD, price) pairs."""
    soup = BeautifulSoup(html, "html.parser")
    pts = []
    for tr in soup.find_all("tr"):
        head = tr.find("td", class_="B6")
        if head is None:
            continue
        m = re.match(r"(\d{4})-([A-Za-z]{3})", clean_text(head.get_text()))
        if not m or m.group(2) not in MONTH:
            continue
        year, row_month = int(m.group(1)), MONTH[m.group(2)]
        dates = [clean_text(td.get_text()) for td in tr.find_all("td", class_="B5")]
        vals = [clean_text(td.get_text()) for td in tr.find_all("td", class_="B3")]
        for d, v in zip(dates, vals):
            dm = re.match(r"(\d{1,2})/(\d{1,2})", d)
            try:
                value = float(v)
            except ValueError:
                continue
            if not dm:
                continue
            mo, day = int(dm.group(1)), int(dm.group(2))
            y = year + 1 if mo < row_month else year  # a week dated in January listed under December
            try:
                pts.append([date(y, mo, day).isoformat(), round(value, 4)])
            except ValueError:
                continue
    if not pts:
        raise CollectError("EIA history page had no data (layout changed?)")
    return sorted(pts)


def fetch_eia_html(series_id: str, start: date) -> list[list]:
    r = http_get(EIA_HTML, params={"n": "PET", "s": series_id, "f": "W"}, browser=True, timeout=45)
    return [p for p in parse_eia_history(r.text) if p[0] >= start.isoformat()]


def fetch_eia(series_id: str, start: date, try_api: bool = True) -> tuple[list[list], bool]:
    """EIA's API first; its public history page if the API is down (it often times out).
    Returns (points, api_ok)."""
    api_err = "skipped (failed earlier this run)"
    if try_api:
        try:
            return fetch_eia_api(series_id, start), True
        except Exception as e:  # noqa: BLE001
            api_err = e
    try:
        return fetch_eia_html(series_id, start), False
    except Exception as html_err:  # noqa: BLE001
        raise CollectError(f"API: {api_err}; history page: {html_err}")


@source("aaa_gas", view="gas")
def collect(ctx: Context) -> dict:
    o = ctx.options
    regions = o.get("regions") or [{"key": "national", "label": "U.S."}]
    grade = o.get("chart_grade", "regular")
    window = int(o.get("window_days", 180))
    eia_map = o.get("eia") or {}
    hist = ctx.history()
    pages: dict[str, str] = {}

    def page(state: str | None) -> str:
        k = state or "national"
        if k not in pages:
            pages[k] = http_get(AAA, params={"state": state} if state else None, browser=True).text
        return pages[k]

    def load(key: str):
        if key == "national":
            return parse_main(page(None))
        state, _, metro = key.partition(":")
        if not re.fullmatch(r"[A-Z]{2}", state):
            raise CollectError(f"bad region key {key!r}")
        html = page(state)
        if metro:
            return parse_metro(html, metro), parse_asof(html)
        return parse_main(html)

    today = ctx.today()
    out_regions, aaa_ok = [], 0
    for reg in regions:
        key, label = reg["key"], reg.get("label", reg["key"])
        res = attempt(ctx, f"AAA {label}", lambda key=key: load(key))
        entry = {"key": key, "label": label, "prices": None, "as_of": None}
        if res:
            prices, asof = res
            day = asof or today
            entry.update(prices=prices, as_of=day.isoformat())
            for g, v in prices["current"].items():
                hist.upsert(day, f"{key}:{g}", v)
            # Seed yesterday once, so a new chart is not a single dot.
            yday = (day - timedelta(days=1)).isoformat()
            for g, v in (prices.get("yesterday") or {}).items():
                if not hist.has(yday, f"{key}:{g}"):
                    hist.upsert(yday, f"{key}:{g}", v)
            aaa_ok += 1
        else:
            for old in (ctx.previous or {}).get("regions", []):
                if old.get("key") == key:
                    entry.update(prices=old.get("prices"), as_of=old.get("as_of"), stale=True)
        out_regions.append(entry)

    since = (today - timedelta(days=window)).isoformat()
    prev = ctx.previous or {}
    prev_fetched = prev.get("eia_fetched") or {}
    eia_series, eia_fetched, eia_ok, api_up = {}, {}, 0, True
    now = datetime.now(timezone.utc)
    for key, sid in eia_map.items():
        old, old_at = (prev.get("eia") or {}).get(key), prev_fetched.get(key)
        if old and old_at and now - datetime.fromisoformat(old_at) < EIA_REUSE:
            eia_series[key] = [p for p in old if p[0] >= since]
            eia_fetched[key] = old_at
            eia_ok += 1
            continue
        try:
            pts, api_up = fetch_eia(sid, today - timedelta(days=window), try_api=api_up)
            eia_series[key] = [p for p in pts if p[0] >= since]
            eia_fetched[key] = now.isoformat(timespec="seconds")
            eia_ok += 1
        except Exception as e:  # noqa: BLE001
            if old:
                eia_series[key] = [p for p in old if p[0] >= since]
                if old_at:
                    eia_fetched[key] = old_at  # keep the old time, so the next run tries again
                age = (today - date.fromisoformat(old[-1][0])).days if old else None
                if age is None or age > EIA_STALE_DAYS:
                    ctx.warn(f"EIA {key}: {e} (showing data from {old[-1][0]})")
                else:
                    print(f"       note: EIA {key} unavailable, reusing data through {old[-1][0]}: {e}")
            else:
                ctx.warn(f"EIA {key}: {e}")

    if not aaa_ok and not eia_ok:
        raise CollectError("; ".join(ctx.warnings) or "no data")
    ctx.save_histories()
    return {
        "grade": grade,
        "window_days": window,
        "regions": out_regions,
        "history": {r["key"]: hist.series(f"{r['key']}:{grade}", since) for r in regions},
        "eia": eia_series,
        "eia_fetched": eia_fetched,
        "fetched": datetime.now(ctx.tz).isoformat(timespec="minutes"),
    }
