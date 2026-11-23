"""Costco gas prices at the warehouses nearest a location, with history.

Uses the JSON endpoint behind costco.com's warehouse locator.

options:
  latitude: 30.2936
  longitude: -97.7627
  count: 5              # nearest N warehouses that sell gas
  chart_count: 3        # how many of them to plot (history builds up over time)
  window_days: 180
"""
from __future__ import annotations

from datetime import timedelta

from ..core import CollectError, Context, clean_text, http_get
from . import source

URL = "https://www.costco.com/AjaxWarehouseBrowseLookupView"
GRADES = ("regular", "premium", "diesel")


def _price(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0 < f < 20 else None


_KEEP_UPPER = {"IH", "US", "FM", "RR", "SH", "TX", "NE", "NW", "SE", "SW", "I", "II"}


def _title(text: str) -> str:
    """'4601 183A TOLL RD' -> '4601 183A Toll Rd', keeping highway codes like IH / US upper-case."""
    def fix(w: str) -> str:
        if w.upper() in _KEEP_UPPER:
            return w.upper()
        if any(c.isdigit() for c in w):
            return w.upper()
        return w.capitalize()
    return " ".join(fix(w) for w in clean_text(text).split())


def parse_locator(payload) -> list[dict]:
    if not isinstance(payload, list) or len(payload) < 1:
        raise CollectError("unexpected Costco response")
    out = []
    for w in payload[1:]:
        if not isinstance(w, dict) or not w.get("hasGasDepartment"):
            continue
        gp = w.get("gasPrices") or {}
        prices = {g: _price(gp.get(g)) for g in GRADES}
        prices = {g: v for g, v in prices.items() if v is not None}
        out.append({
            "id": str(w.get("stlocID") or w.get("identifier")),
            "name": _title(w.get("city") or w.get("locationName") or ""),
            "address": _title(w.get("address1", "")),
            "distance_mi": round(float(w.get("distance") or 0), 1),
            "prices": prices,
            "gas_hours": [f"{clean_text(h.get('title'))} {clean_text(h.get('time'))}".strip()
                          for h in w.get("gasStationHours") or [] if isinstance(h, dict)],
        })
    out.sort(key=lambda w: w["distance_mi"])
    return out


@source("costco_gas", view="costco")
def collect(ctx: Context) -> dict:
    o = ctx.options
    try:
        lat, lon = float(o["latitude"]), float(o["longitude"])
    except (KeyError, TypeError, ValueError):
        raise CollectError("options.latitude and options.longitude are required")
    count = int(o.get("count", 5))
    window = int(o.get("window_days", 180))
    r = http_get(URL, params={
        "langId": -1, "storeId": 10301, "numOfWarehouses": max(count, 5), "hasGas": "true",
        "populateWarehouseDetails": "true", "latitude": lat, "longitude": lon, "countryCode": "US",
    }, browser=True, headers={"Accept": "application/json, text/plain, */*", "Referer": "https://www.costco.com/warehouse-locations"})
    try:
        payload = r.json()
    except ValueError:
        raise CollectError("Costco returned a non-JSON page (probably a bot check)")
    whs = [w for w in parse_locator(payload) if w["prices"]][:count]
    if not whs:
        raise CollectError("no warehouses with gas prices in response")

    hist = ctx.history()
    today = ctx.today()
    for w in whs:
        for g, v in w["prices"].items():
            hist.upsert(today, f"{w['id']}:{g}", v)
    ctx.save_histories()
    since = (today - timedelta(days=window)).isoformat()
    chart = whs[: int(o.get("chart_count", 3))]
    return {
        "as_of": today.isoformat(),
        "warehouses": whs,
        "history": {w["id"]: hist.series(f"{w['id']}:regular", since) for w in chart},
    }
