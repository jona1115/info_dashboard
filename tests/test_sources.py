from datetime import date

import pytest

from collector.core import CollectError
from collector.sources import aaa_gas, arxiv, costco_gas, crossref, rss, stock
from conftest import fixture_bytes


# ------------------------------------------------------------------ rss

LG = "LG Electronics has established an artificial intelligence (AI) home advance base in Singapore"

def test_rss_parses_google_news_and_strips_source_suffix():
    items = rss.parse_feed(fixture_bytes("gnews.xml"))
    first = items[0]
    assert first["title"] == "LG Electronics has established an artificial intelligence (AI) home advance base in Singapore"
    assert first["source"] == "Reuters"
    assert first["date"] == "2026-11-23T01:00:00Z"


def test_rss_filters(monkeypatch):
    items = rss.parse_feed(fixture_bytes("gnews.xml"))
    # near-duplicate titles collapse, old items drop, non-Latin titles drop
    monkeypatch.setattr(rss, "utcnow", lambda: __import__("datetime").datetime(2026, 11, 23, 3, tzinfo=__import__("datetime").timezone.utc))
    out = rss.filter_items(items, max_age_days=5, latin_only=True, max_items=10)
    titles = [i["title"] for i in out]
    assert titles == [LG, "US intel chief warns about AI", "Intel stock jumps on AI deal"]
    # case-sensitive include via inline flag
    out = rss.filter_items(items, include=["(?-i:Intel)"], max_items=10)
    assert [i["title"] for i in out] == ["Intel stock jumps on AI deal"]
    # require = all must match; exclude wins
    out = rss.filter_items(items, require=["AI", "outpost"], max_items=10)
    assert [i["title"] for i in out] == [LG.replace("home advance base", "Home outpost")]
    out = rss.filter_items(items, exclude=["singapore"], max_items=2)
    assert len(out) == 2 and all("Singapore" not in i["title"] for i in out)


def test_rss_collect_groups_and_partial_failure(fake_http, ctx):
    fake_http(rss, {"good.xml": "gnews.xml", "bad.xml": CollectError("HTTP 403")})
    c = ctx({"groups": [
        {"label": "A", "feeds": [{"url": "https://x/good.xml", "require": ["(?-i:Intel)"]}]},
        {"label": "B", "feeds": ["https://x/bad.xml"]},
    ]}, previous={"groups": [{"label": "B", "items": [{"title": "old", "url": "https://o"}]}]})
    data = rss.collect(c)
    a, b = data["groups"]
    assert [i["title"] for i in a["items"]] == ["Intel stock jumps on AI deal"]
    assert b["stale"] and b["items"][0]["title"] == "old"
    assert any("403" in w for w in c.warnings)


def test_rss_all_feeds_fail_raises(fake_http, ctx):
    fake_http(rss, {"bad": CollectError("HTTP 500")})
    with pytest.raises(CollectError):
        rss.collect(ctx({"feeds": ["https://x/bad"]}))


# ------------------------------------------------------------------ arxiv

def test_arxiv_parse():
    [it] = arxiv.parse_arxiv(fixture_bytes("arxiv.xml"))
    assert it["title"] == "An FPGA Accelerator for Things"
    assert it["url"].startswith("https://arxiv.org/abs/")
    assert it["source"] == "arXiv cs.AR"
    assert it["meta"] == "A. One, B. Two, C. Three et al."


# ------------------------------------------------------------------ crossref

def test_crossref_filters_venue_and_front_matter():
    import json
    works = json.loads(fixture_bytes("crossref.json"))["message"]["items"]
    items = crossref.to_items(works, "FPL", r"field[- ]programmable logic")
    assert [i["title"] for i in items] == ["Jack of All Scales: A Tensor Block", "S2MM: Older FPL paper"]
    assert items[0]["date_label"] == "Sep 2026"
    assert items[0]["url"] == "https://doi.org/10.1109/FPL1.2026.2"
    assert items[0]["meta"] == "Ann Lee, Bo Chen"
    assert items[1]["date_label"] == "Sep 1, 2025"


# ------------------------------------------------------------------ stock

def test_stock_parse_chart():
    import json
    q = stock.parse_chart(json.loads(fixture_bytes("yahoo.json")))
    assert q["price"] == 51.28
    assert q["closes"][-1] == ["2026-11-20", 51.28]   # last point = current price
    assert ["2026-11-18", None] not in q["closes"]      # null closes skipped
    assert q["prev_close"] == 55.72
    assert q["change"] == pytest.approx(-4.44)
    assert q["change_pct"] == pytest.approx(-7.968, abs=1e-3)


def test_stock_collect_rejects_bad_range(ctx):
    with pytest.raises(CollectError):
        stock.collect(ctx({"tickers": ["INTC"], "range": "7mo"}))


# ------------------------------------------------------------------ AAA

def test_aaa_parse_national_and_metro():
    prices, asof = aaa_gas.parse_main(fixture_bytes("aaa_national.html").decode())
    assert asof == date(2026, 11, 22)
    assert prices["current"] == {"regular": 3.6448, "mid": 4.1748, "premium": 4.5665, "diesel": 5.8445, "e85": 2.7432}
    assert prices["year_ago"]["regular"] == 3.0789
    tx = fixture_bytes("aaa_tx.html").decode()
    state, _ = aaa_gas.parse_main(tx)
    assert state["current"]["regular"] == 3.1045
    metro = aaa_gas.parse_metro(tx, "Austin-San Marcos")
    assert metro["current"]["regular"] == 2.9597
    with pytest.raises(CollectError):
        aaa_gas.parse_metro(tx, "Nowhere")


def test_aaa_collect_records_history(fake_http, ctx, monkeypatch):
    calls = []
    fake_http(aaa_gas, {'"state": "TX"': "aaa_tx.html", "gasprices.aaa.com/{}": "aaa_national.html",
                        "api.eia.gov": CollectError("HTTP 403")}, calls)
    opts = {"regions": [{"key": "national", "label": "U.S."}, {"key": "TX", "label": "Texas"},
                        {"key": "TX:Austin-San Marcos", "label": "Austin"}],
            "eia": {"national": "EMM_EPMR_PTE_NUS_DPG"}}
    c = ctx(opts, wid="gas")
    monkeypatch.setattr(c, "today", lambda: date(2026, 11, 22))
    data = aaa_gas.collect(c)
    assert data["history"]["national"] == [["2026-11-21", 3.6594], ["2026-11-22", 3.6448]]
    assert data["history"]["TX:Austin-San Marcos"][-1] == ["2026-11-22", 2.9597]
    assert any("EIA" in w for w in c.warnings)          # EIA failure is only a warning
    assert sum("aaa.com" in u for u, _ in calls) == 2   # TX page fetched once for state + metro
    csv_text = (c.data_dir / "history" / "gas.csv").read_text()
    assert "2026-11-22,TX:regular,3.1045" in csv_text


def test_aaa_collect_everything_fails(fake_http, ctx):
    fake_http(aaa_gas, {"aaa.com": CollectError("HTTP 403"), "eia.gov": CollectError("HTTP 403")})
    with pytest.raises(CollectError):
        aaa_gas.collect(ctx({"regions": [{"key": "national"}], "eia": {"national": "X"}}))


# ------------------------------------------------------------------ Costco

def test_costco_parse_and_collect(fake_http, ctx, monkeypatch):
    import json
    whs = costco_gas.parse_locator(json.loads(fixture_bytes("costco.json")))
    assert [w["id"] for w in whs] == ["681", "1152", "1629"]      # sorted by distance, no-gas dropped
    assert whs[0]["name"] == "Austin" and whs[0]["address"] == "10401 Research Blvd"
    assert whs[2]["address"] == "19086 IH 35"
    assert whs[1]["address"] == "4601 183A Toll Rd"
    assert "diesel" not in whs[2]["prices"] and whs[1]["prices"]["diesel"] == 3.999

    fake_http(costco_gas, {"costco.com": "costco.json"})
    c = ctx({"latitude": 30.29, "longitude": -97.77, "count": 2, "chart_count": 1}, wid="costco")
    monkeypatch.setattr(c, "today", lambda: date(2026, 11, 22))
    data = costco_gas.collect(c)
    assert len(data["warehouses"]) == 2
    assert data["history"] == {"681": [["2026-11-22", 2.759]]}


def test_costco_requires_location(ctx):
    with pytest.raises(CollectError):
        costco_gas.collect(ctx({}))


def test_stock_pct_since():
    pts = [["2026-10-01", 100.0], ["2026-10-20", 110.0], ["2026-10-21", 90.0], ["2026-11-20", 99.0]]
    assert stock._pct_since(pts, 30) == pytest.approx(10.0)    # base = close on/before Oct 21
    assert stock._pct_since(pts, 365) is None                  # not enough history
    assert stock._pct_since(pts[:1], 30) is None


def test_stock_groups_labels_sort_and_stale(monkeypatch, ctx):
    import json
    base = json.loads(fixture_bytes("yahoo.json"))

    def fake_fetch(symbol, rng):
        if symbol == "BAD":
            raise CollectError("BAD: HTTP 404")
        q = stock.parse_chart(base)
        q["symbol"] = symbol
        q["change_pct"] = {"AAA": 1.0, "BBB": 3.0}.get(symbol, 0.0)
        return q

    monkeypatch.setattr(stock, "fetch_quote", fake_fetch)
    monkeypatch.setattr(stock.time, "sleep", lambda s: None)
    prev = {"groups": [{"label": "G2", "quotes": [{"symbol": "BAD", "price": 1.0}]}]}
    data = stock.collect(ctx({
        "display": "table", "sort": "day",
        "groups": [{"label": "G1", "tickers": ["aaa", {"symbol": "BBB", "label": "Bee"}]},
                   {"label": "G2", "tickers": ["BAD", "CCC"]}],
    }, previous=prev))
    g1, g2 = data["groups"]
    assert data["display"] == "table"
    assert [q["symbol"] for q in g1["quotes"]] == ["BBB", "AAA"]        # sorted by day change
    assert g1["quotes"][0]["label"] == "Bee"
    assert g1["quotes"][1]["label"] == "Intel Corporation"              # falls back to name
    assert g2["quotes"][0]["symbol"] == "CCC"
    assert g2["quotes"][1]["stale"] and g2["quotes"][1]["symbol"] == "BAD"   # kept from previous run
    with pytest.raises(CollectError):
        stock.collect(ctx({"tickers": ["A"], "display": "grid"}))


# ------------------------------------------------------------------ headlines fixes

def test_rss_strips_google_suffix_even_with_label_and_ignores_future_dates():
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Big story - CNN</title><link>https://n/1</link><pubDate>Mon, 23 Nov 2026 01:00:00 GMT</pubDate>
      <source url="https://cnn.com">CNN</source></item>
    <item><title>Op-ed from the future</title><link>https://n/2</link><pubDate>Fri, 01 Jan 2100 00:00:00 GMT</pubDate></item>
    </channel></rss>"""
    a, b = rss.parse_feed(xml, "CNN (label)")
    assert a["title"] == "Big story" and a["source"] == "CNN (label)"
    assert b["date"] is None


# ------------------------------------------------------------------ treasury

from collector.sources import treasury, macro  # noqa: E402

TREASURY_CSV = '''Date,"1 Mo","1.5 Month","2 Mo","3 Mo","6 Mo","1 Yr","2 Yr","10 Yr","30 Yr"
11/20/2026,4.19,4.26,4.28,4.32,4.38,4.51,4.63,4.92,5.31
10/21/2026,4.09,4.12,4.16,4.25,4.35,4.52,4.81,5.13,5.42
11/20/2025,4.04,,4.02,3.94,3.79,3.65,3.55,4.10,4.73
'''


def test_treasury_parse_and_collect(monkeypatch, ctx):
    days = treasury.parse_csv(TREASURY_CSV)
    assert days["2026-11-20"]["10Y"] == 4.92 and "6W" not in days["2025-11-20"]   # blank cell skipped
    assert treasury.ago_label(30) == "1 month ago" and treasury.ago_label(730) == "2 years ago"
    monkeypatch.setattr(treasury, "fetch_year", lambda y: days if y == 2026 else {})
    c = ctx({"compare_days": [30, 365], "spread_days": 400})
    monkeypatch.setattr(c, "today", lambda: date(2026, 11, 22))
    d = treasury.collect(c)
    assert d["tenors"] == ["1M", "6W", "2M", "3M", "6M", "1Y", "2Y", "10Y", "30Y"]   # sorted by maturity
    assert [x["date"] for x in d["curves"]] == ["2026-11-20", "2026-10-21", "2025-11-20"]
    assert d["curves"][2]["values"][1] is None                                       # missing tenor -> gap
    tens2 = d["spreads"][0]
    assert tens2["label"] == "10Y − 2Y" and tens2["points"][-1] == ["2026-11-20", 0.29]


def test_treasury_current_year_failure_raises(monkeypatch, ctx):
    def boom(y):
        raise CollectError("HTTP 503")
    monkeypatch.setattr(treasury, "fetch_year", boom)
    with pytest.raises(CollectError):
        treasury.collect(ctx({}))


# ------------------------------------------------------------------ macro

def test_macro_picks_newest_source_and_handles_gaps(monkeypatch, ctx):
    def fake_oecd(flow, key, start):
        if "QNA" in flow:
            return {"USA": [["2026-Q2", 1.8], ["2026-Q3", 2.07]], "MYS": [["2026-Q2", 4.4]]}
        if "LFS" in flow:
            return {"USA": [["2026-10", 4.1]]}
        raise CollectError("HTTP 500")          # 10Y yields fail -> warning only
    def fake_bis(flow, key, start):
        if flow == "WS_LONG_CPI":
            return {"US": [["2026-08", 3.5], ["2026-09", 3.74]], "MY": [["2026-09", 1.78]]}
        return {"US": [["2026-10", 3.875]], "MY": [["2026-10", 2.75]]}
    def fake_dosm(dataset, field, filt=None, quarterly=False, limit=80):
        return {"gdp_qtr_real": [["2026-Q3", 5.5]], "cpi_headline_inflation": [["2026-10", 1.9]],
                "lfs_month": [["2026-09", 2.9]]}[dataset]
    monkeypatch.setattr(macro, "oecd", fake_oecd)
    monkeypatch.setattr(macro, "bis", fake_bis)
    monkeypatch.setattr(macro, "dosm", fake_dosm)
    c = ctx({"countries": [{"label": "U.S.", "oecd": "USA", "bis": "US"},
                           {"label": "Malaysia", "oecd": "MYS", "bis": "MY", "dosm": True}]})
    d = macro.collect(c)
    us, my = d["rows"]
    assert us["cells"]["gdp"] == {"v": 2.07, "period": "2026-Q3", "prev": 1.8, "src": "OECD"}
    assert us["cells"]["y10"] is None and any("10Y" in w for w in c.warnings)
    assert us["cpi_hist"] == [["2026-08", 3.5], ["2026-09", 3.74]]
    assert my["cells"]["gdp"]["src"] == "DOSM" and my["cells"]["gdp"]["v"] == 5.5     # newer than OECD Q2
    assert my["cells"]["cpi"]["period"] == "2026-10" and my["cells"]["policy"]["v"] == 2.75


def test_dosm_parsing(fake_http, monkeypatch):
    import json as _json
    payload = _json.dumps([
        {"date": "2026-07-01", "series": "growth_yoy", "value": 5.5},
        {"date": "2026-07-01", "series": "abs", "value": 467403.5},
        {"date": "2026-04-01", "series": "growth_yoy", "value": 4.4},
    ]).encode()
    from conftest import FakeResponse
    monkeypatch.setattr(macro, "http_get", lambda url, **kw: FakeResponse(payload))
    assert macro.dosm("gdp_qtr_real", "value", {"series": "growth_yoy"}, quarterly=True) == [["2026-Q2", 4.4], ["2026-Q3", 5.5]]


# ------------------------------------------------------------------ espn scores

from collector.sources import espn  # noqa: E402


def _ev(i, state, d):
    return {"id": str(i), "date": d, "competitions": [{"status": {"type": {"state": state}}}]}


def test_espn_pick_live_first_then_newest_finals():
    evs = [_ev(1, "post", "2026-11-01"), _ev(2, "post", "2026-11-08"), _ev(3, "post", "2026-11-15"),
           _ev(4, "in", "2026-11-22"), _ev(5, "pre", "2026-11-29")]
    assert [e["id"] for e in espn.pick(evs, 2)] == ["4", "3", "2"]
    assert [e["id"] for e in espn.pick(evs, 0)] == ["4"]


def test_espn_game_parsing(monkeypatch):
    from conftest import FakeResponse
    import json as _json
    summary = {"header": {"competitions": [{"status": {"type": {"state": "post", "shortDetail": "Final/OT"}},
        "competitors": [
            {"homeAway": "home", "score": "48", "winner": True, "team": {"id": "2116", "abbreviation": "UCF", "displayName": "UCF Knights"},
             "linescores": [{"displayValue": "3"}, {"displayValue": "21"}, {"displayValue": "7"}, {"displayValue": "10"}, {"displayValue": "7"}]},
            {"homeAway": "away", "score": "41", "winner": False, "team": {"id": "66", "abbreviation": "ISU", "displayName": "Iowa State Cyclones"},
             "linescores": [{"displayValue": "3"}, {"displayValue": "7"}, {"displayValue": "7"}, {"displayValue": "17"}, {"displayValue": "7"}]},
        ]}]}}
    monkeypatch.setattr(espn, "http_get", lambda url, **kw: FakeResponse(_json.dumps(summary).encode()))
    g = espn.game("football/college-football", {"id": "99", "date": "2026-11-20T23:00Z"}, 66)
    assert g["url"] == "https://www.espn.com/college-football/game/_/gameId/99"
    assert g["status"] == "Final/OT"
    away, home = g["teams"]
    assert away["abbr"] == "ISU" and away["us"] and away["lines"] == [3, 7, 7, 17, 7] and away["score"] == 41
    assert home["winner"] and not home["us"]


def test_espn_collect_keeps_stale_sport(monkeypatch, ctx):
    def sched(path, team_id):
        if "football" in path:
            raise CollectError("HTTP 503")
        return [_ev(1, "post", "2026-11-10")]
    monkeypatch.setattr(espn, "schedule", sched)
    monkeypatch.setattr(espn, "game", lambda path, e, tid: {"id": e["id"], "teams": []})
    prev = {"groups": [{"label": "Football", "periods": 4, "games": [{"id": "old"}]}]}
    d = espn.collect(ctx({"team_id": 66, "sports": [
        {"label": "Football", "path": "football/college-football"},
        {"label": "MBB", "path": "basketball/mens-college-basketball", "periods": 2}]}, previous=prev))
    fb, mbb = d["groups"]
    assert fb["stale"] and fb["games"] == [{"id": "old"}]
    assert mbb["periods"] == 2 and mbb["games"] == [{"id": "1", "teams": []}]


# ------------------------------------------------------------------ rss source filter + strip

def test_rss_source_filter_and_strip(monkeypatch, ctx):
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>US story - CNBC</title><link>https://n/1</link><pubDate>Mon, 23 Nov 2026 01:00:00 GMT</pubDate><source url="https://cnbc.com">CNBC</source></item>
    <item><title>Foreign story - The Hindu</title><link>https://n/2</link><pubDate>Mon, 23 Nov 2026 01:00:00 GMT</pubDate><source url="https://h.in">The Hindu</source></item>
    <item><title>卫部审查确认 - 国内 - 即时国内 - Sin Chew Daily</title><link>https://n/3</link><pubDate>Mon, 23 Nov 2026 01:00:00 GMT</pubDate><source url="https://sinchew.com.my">Sin Chew Daily</source></item>
    <item><title>陈汉光专栏 - 星洲人 - VIP文 - Sin Chew Daily</title><link>https://n/4</link><pubDate>Mon, 23 Nov 2026 01:00:00 GMT</pubDate><source url="https://sinchew.com.my">Sin Chew Daily</source></item>
    </channel></rss>""".encode()
    from conftest import FakeResponse
    monkeypatch.setattr(rss, "http_get", lambda url, **kw: FakeResponse(xml))
    d = rss.collect(ctx({"source_include": ["^CNBC$"], "feeds": ["https://x/feed"]}))
    assert [i["title"] for i in d["groups"][0]["items"]] == ["US story"]
    d = rss.collect(ctx({"feeds": [{"url": "https://x/feed", "source": "星洲日报", "exclude": ["VIP"],
                                     "require": ["[\u4e00-\u9fff]"], "strip": [" - .*$"]}]}))
    assert [i["title"] for i in d["groups"][0]["items"]] == ["卫部审查确认"]
    assert d["groups"][0]["items"][0]["source"] == "星洲日报"


# ------------------------------------------------------------------ candles

from collector.sources import candles  # noqa: E402


def test_sma():
    assert candles.sma([1, 2, 3, 4, 5], 2) == [None, 1.5, 2.5, 3.5, 4.5]
    assert candles.sma([1, 2, 3], 3) == [None, None, 2.0]
    assert candles.sma([1, 2], 5) == [None, None]


def test_parse_ohlc_skips_gaps_fixes_bounds_and_uses_live_price():
    payload = {"chart": {"result": [{
        "meta": {"symbol": "VOO", "currency": "USD", "regularMarketPrice": 661.0, "regularMarketTime": 1795208401,
                 "exchangeTimezoneName": "America/New_York", "longName": "Vanguard S&P 500 ETF"},
        "timestamp": [1794925800, 1795012200, 1795098600, 1795185000],   # Nov 17..20, 2026 (ET)
        "indicators": {"quote": [{
            "open":  [650.0, None, 655.0, 657.0],
            "high":  [652.0, 660.0, 654.0, 662.0],    # Nov 19 high below its open: widen to cover the body
            "low":   [648.0, 650.0, 653.0, 656.0],
            "close": [651.0, 659.0, 653.5, 660.0],
        }]}}], "error": None}}
    q = candles.parse_ohlc(payload)
    assert [c[0] for c in q["candles"]] == ["2026-11-17", "2026-11-19", "2026-11-20"]   # Nov 18 incomplete -> skipped
    assert q["candles"][1] == ["2026-11-19", 655.0, 655.0, 653.0, 653.5]
    assert q["candles"][2][4] == 661.0 and q["candles"][2][2] == 662.0                 # live price replaces close
    assert q["name"] == "Vanguard S&P 500 ETF"


def test_candles_collect_options_and_stale(monkeypatch, ctx):
    def fake(sym):
        if sym == "BAD":
            raise CollectError("BAD: HTTP 404")
        return {"symbol": sym, "candles": [[f"2026-01-{i:02d}", 1, 2, 0.5, float(i)] for i in range(1, 6)]}
    monkeypatch.setattr(candles, "fetch_ohlc", fake)
    monkeypatch.setattr(candles.time, "sleep", lambda s: None)
    prev = {"tabs": [{"symbol": "BAD", "candles": [], "ma": {}}]}
    d = candles.collect(ctx({"tickers": ["voo", {"symbol": "BAD", "label": "Bad"}], "ma": [2, 3],
                             "periods": ["1m", "1Y"], "default_period": "6M"}, previous=prev))
    assert d["periods"] == ["1M", "1Y"] and d["default_period"] == "1M"   # default not offered -> first
    voo, bad = d["tabs"]
    assert voo["symbol"] == "VOO" and voo["label"] == "VOO"
    assert voo["ma"]["2"] == [None, 1.5, 2.5, 3.5, 4.5] and voo["ma"]["3"][:3] == [None, None, 2.0]
    assert bad["stale"] and bad["label"] == "Bad"
    for bad_opts in ({"tickers": ["A"], "periods": ["2Y"]}, {"tickers": ["A"], "ma": [1]}, {"tickers": []}):
        with pytest.raises(CollectError):
            candles.collect(ctx(bad_opts))
