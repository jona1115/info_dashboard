"""Upcoming key dates: Fed (FOMC) meetings, BEA data releases, market holidays, earnings, your own events.

Every date comes from the publisher itself (or NYSE's published holiday rules):
  FOMC       federalreserve.gov meeting calendar (rate decision on the last day)
  BEA        bea.gov release schedule (GDP, PCE inflation = "Personal Income and Outlays", ...)
  Holidays   computed from NYSE rules (closures and 1 pm early closes)
  Earnings   Nasdaq's earnings-date page, once the company has announced the date
  Custom     your own list below (e.g. CPI / jobs-report dates from bls.gov, which blocks automated readers)

options:
  horizon_days: 90            # show events up to this far ahead (the next FOMC meeting is always shown)
  max_items: 14
  fomc: true
  bea: ['^GDP', 'Personal Income and Outlays']   # regexes for BEA release titles to include ([] = off)
  market_holidays: true
  earnings: [INTC]            # tickers
  events:                     # your own dates
    - {date: 2027-01-13, title: CPI (December), time: 8:30 AM ET, category: Data}
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from ..core import CollectError, Context, attempt, clean_text, http_get
from . import source

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BEA_URL = "https://www.bea.gov/news/schedule"
NASDAQ_EARNINGS = "https://api.nasdaq.com/api/analyst/{sym}/earnings-date"
MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
MON3 = {m[:3]: i for m, i in MONTHS.items()}
CATEGORIES = ("Fed", "Data", "Market", "Earnings", "Custom")


def _ev(d: date, title: str, category: str, *, end: date | None = None, time_: str | None = None,
        url: str | None = None, note: str | None = None) -> dict:
    return {"date": d.isoformat(), "end": end.isoformat() if end else None, "time": time_,
            "title": title, "category": category, "url": url, "note": note}


# ----------------------------------------------------------------------------------- FOMC

def parse_fomc(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for panel in soup.select("div.panel"):
        head = panel.select_one(".panel-heading")
        m = re.search(r"(\d{4}) FOMC Meetings", head.get_text(" ", strip=True) if head else "")
        if not m:
            continue
        year = int(m.group(1))
        for row in panel.select(".fomc-meeting"):
            mon = clean_text((row.select_one(".fomc-meeting__month") or row).get_text(" "))
            day = clean_text((row.select_one(".fomc-meeting__date") or row).get_text(" "))
            if "notation" in day.lower() or "unscheduled" in day.lower() or "cancel" in day.lower():
                continue
            months = [MONTHS.get(x.strip().title()) or MON3.get(x.strip()[:3].title()) for x in mon.split("/")]
            days = re.findall(r"\d{1,2}", day)
            if not days or not months or None in months:
                continue
            m1, m2 = months[0], months[-1]
            d1, d2 = int(days[0]), int(days[-1])
            if len(months) == 1 and d2 < d1:
                m2 = m1 + 1  # e.g. "30-1" written under one month
            y2 = year + 1 if m2 < m1 else year
            try:
                start, end = date(year, m1, d1), date(y2, m2, d2)
            except ValueError:
                continue
            sep = "*" in day
            out.append(_ev(start, "FOMC meeting · rate decision" + (" + economic projections" if sep else ""), "Fed",
                           end=end if end != start else None, time_="2:00 PM ET (decision)", url=FOMC_URL,
                           note=f"Decision {end.strftime('%b')} {end.day}"))
    if not out:
        raise CollectError("FOMC calendar page had no meetings (layout changed?)")
    return out


# ----------------------------------------------------------------------------------- BEA

def parse_bea(html: str, include: list[str]) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rx = [re.compile(p, re.I) for p in include]
    year, out, rows = None, [], 0
    for tr in soup.find_all("tr"):
        cells = [clean_text(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
        if not cells:
            continue
        y = re.match(r"Year (\d{4})", cells[0])
        if y:
            year = int(y.group(1))
            continue
        m = re.match(r"([A-Z][a-z]+) (\d{1,2})(?: (\d{1,2}:\d{2} [AP]M))?", cells[0])
        if not m or year is None or m.group(1) not in MONTHS:
            continue
        rows += 1
        title = cells[-1]
        if rx and not any(r.search(title) for r in rx):
            continue
        try:
            d = date(year, MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            continue
        out.append(_ev(d, title, "Data", time_=f"{m.group(3)} ET" if m.group(3) else None, url=BEA_URL,
                       note="BEA"))
    if not rows:
        raise CollectError("BEA schedule page had no releases (layout changed?)")
    return out


# ----------------------------------------------------------------------------------- NYSE holidays

def easter(y: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(y, month, day)


def _nth_weekday(y, m, weekday, n):  # n-th (1-based) weekday of a month; n=-1 = last
    if n > 0:
        d = date(y, m, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date, saturday_rolls_back=True) -> date | None:
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_rolls_back else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(y: int) -> list[dict]:
    """NYSE full-day closures and 1 pm early closes for a calendar year."""
    out = []

    def closed(d, name):
        if d:
            out.append(_ev(d, f"Markets closed · {name}", "Market"))

    closed(_observed(date(y, 1, 1), saturday_rolls_back=False), "New Year's Day")  # Sat: no Friday closure
    closed(_nth_weekday(y, 1, 0, 3), "Martin Luther King Jr. Day")
    closed(_nth_weekday(y, 2, 0, 3), "Presidents' Day")
    closed(easter(y) - timedelta(days=2), "Good Friday")
    closed(_nth_weekday(y, 5, 0, -1), "Memorial Day")
    closed(_observed(date(y, 6, 19)), "Juneteenth")
    closed(_observed(date(y, 7, 4)), "Independence Day")
    closed(_nth_weekday(y, 9, 0, 1), "Labor Day")
    thanksgiving = _nth_weekday(y, 11, 3, 4)
    closed(thanksgiving, "Thanksgiving")
    closed(_observed(date(y, 12, 25)), "Christmas")
    early = [(thanksgiving + timedelta(days=1), "day after Thanksgiving")]
    if date(y, 12, 24).weekday() < 4:  # Mon-Thu
        early.append((date(y, 12, 24), "Christmas Eve"))
    if date(y, 7, 3).weekday() < 4 and date(y, 7, 4).weekday() < 5:
        early.append((date(y, 7, 3), "day before Independence Day"))
    for d, why in early:
        out.append(_ev(d, f"Early close 1:00 PM ET · {why}", "Market"))
    return out


# ----------------------------------------------------------------------------------- earnings

_EARN = re.compile(r"report earnings on\s+(\d{1,2})/(\d{1,2})/(\d{4})\s*(before market open|after market close)?", re.I)


def parse_nasdaq_earnings(payload: dict, sym: str) -> dict | None:
    text = clean_text(((payload or {}).get("data") or {}).get("reportText") or "")
    m = _EARN.search(text)
    if not m:
        return None  # not announced yet
    d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    when = {"before market open": "before the open", "after market close": "after the close"}.get(
        (m.group(4) or "").lower())
    return _ev(d, f"{sym} earnings" + (f" ({when})" if when else ""), "Earnings",
               url=f"https://www.nasdaq.com/market-activity/stocks/{sym.lower()}/earnings", note="expected")


def fetch_earnings(sym: str) -> dict | None:
    r = http_get(NASDAQ_EARNINGS.format(sym=sym.upper()), browser=True, headers={"Accept": "application/json"})
    return parse_nasdaq_earnings(r.json(), sym.upper())


# ----------------------------------------------------------------------------------- widget

def _custom(events) -> list[dict]:
    out = []
    for e in events or []:
        try:
            d = date.fromisoformat(str(e["date"]))
        except (KeyError, ValueError):
            raise CollectError(f"custom event needs date: YYYY-MM-DD ({e!r})")
        end = date.fromisoformat(str(e["end"])) if e.get("end") else None
        cat = e.get("category", "Custom")
        out.append(_ev(d, str(e.get("title") or "Event"), cat if cat in CATEGORIES else "Custom",
                       end=end, time_=e.get("time"), url=e.get("url")))
    return out


@source("calendar", view="calendar")
def collect(ctx: Context) -> dict:
    o = ctx.options
    today = ctx.today()
    horizon = today + timedelta(days=int(o.get("horizon_days", 90)))
    prev = (ctx.previous or {}).get("events") or []
    events: list[dict] = []
    fetched_any = False

    def keep_previous(category):
        events.extend(e for e in prev if e.get("category") == category)

    if o.get("fomc", True):
        got = attempt(ctx, "FOMC calendar", lambda: parse_fomc(http_get(FOMC_URL, browser=True).text))
        if got is None:
            keep_previous("Fed")
        else:
            events += got
            fetched_any = True
    bea = o.get("bea", ["^GDP", "Personal Income and Outlays"])
    if bea:
        got = attempt(ctx, "BEA schedule", lambda: parse_bea(http_get(BEA_URL, browser=True).text, bea))
        if got is None:
            keep_previous("Data")
        else:
            events += got
            fetched_any = True
    if o.get("market_holidays", True):
        for y in (today.year, today.year + 1):
            events += nyse_holidays(y)
    for i, sym in enumerate(o.get("earnings") or []):
        sym = str(sym).upper()
        if i:
            time.sleep(0.3)
        try:
            got = fetch_earnings(sym)  # None = date not announced yet (not an error)
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"{sym} earnings date: {e}")
            events.extend(ev for ev in prev if ev.get("category") == "Earnings" and ev["title"].startswith(f"{sym} "))
            continue
        fetched_any = True
        if got:
            events.append(got)
    events += _custom(o.get("events"))

    # Upcoming only (a multi-day event stays until its last day), de-duplicated, sorted.
    seen, upcoming = set(), []
    for e in sorted(events, key=lambda e: (e["date"], e["category"], e["title"])):
        last = e.get("end") or e["date"]
        if last < today.isoformat():
            continue
        k = (e["date"], e["title"])
        if k in seen:
            continue
        seen.add(k)
        upcoming.append(e)
    shown = [e for e in upcoming if e["date"] <= horizon.isoformat()][: int(o.get("max_items", 14))]
    next_fomc = next((e for e in upcoming if e["category"] == "Fed"), None)
    if next_fomc and next_fomc not in shown:
        shown.append(next_fomc)  # always show the next Fed meeting, even beyond the horizon
    if not shown and not fetched_any and ctx.warnings:
        raise CollectError("; ".join(ctx.warnings))
    return {"as_of": today.isoformat(), "horizon_days": int(o.get("horizon_days", 90)), "events": shown}
