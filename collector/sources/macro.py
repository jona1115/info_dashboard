"""World economy table: GDP growth, inflation, unemployment, policy rate, 10-year yield by country.

Free sources, no API keys:
  OECD (sdmx.oecd.org)    quarterly real GDP growth y/y, monthly unemployment rate, monthly 10Y govt bond yield
  BIS  (stats.bis.org)    monthly CPI inflation y/y, central-bank policy rate (end of month)
  OpenDOSM (data.gov.my)  Malaysia GDP growth, CPI inflation, unemployment (official, usually fresher)
For each cell the newest observation among the sources wins; the period is shown with the value.

options:
  countries:
    - {label: U.S., oecd: USA, bis: US}      # oecd = ISO3 code (EA20 = euro area), bis = ISO2 (XM = euro area)
    - {label: Malaysia, oecd: MYS, bis: MY, dosm: true}
  history_months: 24                          # length of the inflation trend line
"""
from __future__ import annotations

import csv
import io
import math
from datetime import date

from ..core import CollectError, Context, attempt, http_get
from . import source

OECD = "https://sdmx.oecd.org/public/rest/data"
BIS = "https://stats.bis.org/api/v2/data/dataflow/BIS"
DOSM = "https://api.data.gov.my/data-catalogue"

COLUMNS = [
    {"key": "gdp", "label": "GDP growth", "desc": "Real GDP, % change vs same quarter a year earlier"},
    {"key": "cpi", "label": "Inflation", "desc": "Consumer prices, % change vs a year earlier"},
    {"key": "unemp", "label": "Unemployment", "desc": "% of labour force, seasonally adjusted where available"},
    {"key": "policy", "label": "Policy rate", "desc": "Central bank policy rate, %, end of month"},
    {"key": "y10", "label": "10Y yield", "desc": "Long-term (≈10-year) government bond yield, monthly average, %"},
]


def _csv_rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _by_area(rows: list[dict], area_col="REF_AREA") -> dict[str, list[list]]:
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        try:
            v = float(r["OBS_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(v):  # sources mark missing observations as NaN
            continue
        out.setdefault(r[area_col], {})[r["TIME_PERIOD"]] = v
    return {a: [[p, round(v, 4)] for p, v in sorted(d.items())] for a, d in out.items()}


def oecd(flow: str, key: str, start: str) -> dict[str, list[list]]:
    r = http_get(f"{OECD}/{flow}/{key}", params={
        "startPeriod": start, "dimensionAtObservation": "AllDimensions", "format": "csvfile"}, timeout=120)
    return _by_area(_csv_rows(r.text))


def bis(flow: str, key: str, start: str) -> dict[str, list[list]]:
    r = http_get(f"{BIS}/{flow}/1.0/{key}", params={"startPeriod": start, "format": "csv"}, timeout=90)
    return _by_area(_csv_rows(r.text))


def dosm(dataset: str, value_field: str, filt: dict | None = None, quarterly=False, limit=80) -> list[list]:
    r = http_get(DOSM, params={"id": dataset, "sort": "-date", "limit": limit})
    rows = r.json()
    if not isinstance(rows, list):
        raise CollectError(f"unexpected data.gov.my response for {dataset}")
    pts = {}
    for row in rows:
        if filt and any(row.get(k) != v for k, v in filt.items()):
            continue
        try:
            y, m, _ = (int(x) for x in row["date"].split("-"))
            v = float(row[value_field])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        p = f"{y}-Q{(m - 1) // 3 + 1}" if quarterly else f"{y}-{m:02d}"
        pts[p] = round(v, 4)
    return [[p, v] for p, v in sorted(pts.items())]


def _cell(series: list[list] | None, src: str) -> dict | None:
    if not series:
        return None
    p, v = series[-1]
    prev = series[-2][1] if len(series) >= 2 else None
    return {"v": v, "period": p, "prev": prev, "src": src}


def _newer(a: dict | None, b: dict | None) -> dict | None:
    if not a:
        return b
    if not b:
        return a
    return b if b["period"] > a["period"] else a


@source("macro", view="macro")
def collect(ctx: Context) -> dict:
    countries = ctx.options.get("countries") or []
    if not countries:
        raise CollectError("options.countries is required")
    months = int(ctx.options.get("history_months", 24))
    today = ctx.today()
    y0 = today.year - (months // 12 + 1)
    start_m, start_q = f"{y0}-{today.month:02d}", f"{y0}-Q1"
    o3 = "+".join(c["oecd"] for c in countries if c.get("oecd"))
    b2 = "+".join(c["bis"] for c in countries if c.get("bis"))

    got = {
        "gdp": attempt(ctx, "OECD GDP", lambda: oecd(
            "OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_G20,", f"Q.Y.{o3}...B1GQ......GY.", start_q)),
        "unemp": attempt(ctx, "OECD unemployment", lambda: oecd(
            "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,", f"{o3}.UNE_LF_M...Y._T.Y_GE15..M", start_m)),
        "y10": attempt(ctx, "OECD 10Y yields", lambda: oecd(
            "OECD.SDD.STES,DSD_STES@DF_FINMARK,", f"{o3}.M.IRLT.PA.....", start_m)),
        "cpi": attempt(ctx, "BIS CPI", lambda: bis("WS_LONG_CPI", f"M.{b2}.771", start_m)),
        "policy": attempt(ctx, "BIS policy rates", lambda: bis("WS_CBPOL", f"M.{b2}", start_m)),
    }
    my = {}
    if any(c.get("dosm") for c in countries):
        my = {
            "gdp": attempt(ctx, "data.gov.my GDP", lambda: dosm("gdp_qtr_real", "value", {"series": "growth_yoy"}, quarterly=True)),
            "cpi": attempt(ctx, "data.gov.my CPI", lambda: dosm("cpi_headline_inflation", "inflation_yoy", {"division": "overall"}, limit=400)),
            "unemp": attempt(ctx, "data.gov.my unemployment", lambda: dosm("lfs_month", "u_rate")),
        }
    if not any(got.values()) and not any(my.values()):
        raise CollectError("; ".join(ctx.warnings) or "no data")

    src_of = {"gdp": "OECD", "unemp": "OECD", "y10": "OECD", "cpi": "BIS", "policy": "BIS"}
    rows = []
    for c in countries:
        cells, cpi_hist = {}, []
        for col in COLUMNS:
            k = col["key"]
            code = c.get("oecd") if src_of[k] == "OECD" else c.get("bis")
            series = (got.get(k) or {}).get(code) if code else None
            cell = _cell(series, src_of[k])
            if c.get("dosm") and my.get(k):
                dcell = _cell(my[k], "DOSM")
                if dcell and (not cell or dcell["period"] >= cell["period"]):
                    cell, series = dcell, my[k]
            cells[k] = cell
            if k == "cpi" and series:
                cpi_hist = series[-months:]
        rows.append({"label": c.get("label") or c.get("oecd") or c.get("bis"), "cells": cells, "cpi_hist": cpi_hist})
    return {"columns": COLUMNS, "rows": rows, "as_of": today.isoformat()}
