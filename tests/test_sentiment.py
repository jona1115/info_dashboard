import json
from datetime import date

import pytest

from collector.core import CollectError
from collector.sources import sentiment as sen
from conftest import fixture_bytes


def _cnn():
    return json.loads(fixture_bytes("cnn_fear_greed.json"))


def test_rating_bands_match_cnn():
    got = [sen.rating_for(v) for v in (0, 24.4, 24.6, 44, 45, 55, 55.4, 56, 75, 76, 100)]
    assert got == ["Extreme fear", "Extreme fear", "Fear", "Fear", "Neutral", "Neutral", "Neutral",
                   "Greed", "Greed", "Extreme greed", "Extreme greed"]


def test_parse_cnn_dates_score_and_components():
    c = sen.parse_cnn(_cnn())
    assert c["score"] == 15.0 and c["rating"] == "Extreme fear"
    assert c["previous"] == {"close": 15.23, "1_week": 15.87, "1_month": 29.83, "1_year": 17.66}
    # 00:00 UTC stamps are that trading day (Mon Nov 23, not Sun Nov 22); the live reading replaces
    # Wed Nov 25; the Thanksgiving padding point (Nov 26) is dropped.
    assert c["history"] == [["2026-11-20", 18.59], ["2026-11-23", 14.88], ["2026-11-24", 10.96], ["2026-11-25", 15.0]]
    assert [x["label"] for x in c["components"]] == [
        "Market momentum", "Stock price strength", "Stock price breadth", "Put and call options",
        "Market volatility", "Safe haven demand", "Junk bond demand"]
    assert c["components"][0] == {"key": "market_momentum_sp500", "label": "Market momentum",
                                  "what": "S&P 500 vs its 125-day average", "score": 2.8, "rating": "Extreme fear"}


def test_parse_cnn_rejects_missing_score_and_skips_bad_points():
    with pytest.raises(CollectError, match="no current score"):
        sen.parse_cnn({"fear_and_greed": {"score": "NaN"}})
    p = _cnn()
    p["fear_and_greed_historical"]["data"].append({"x": None, "y": 5})
    p["junk_bond_demand"]["score"] = float("nan")
    c = sen.parse_cnn(p)
    assert len(c["history"]) == 4 and len(c["components"]) == 6


def test_parse_crypto():
    c = sen.parse_crypto(json.loads(fixture_bytes("crypto_fng.json")))
    assert c == {"score": 12.0, "rating": "Extreme fear",
                 "history": [["2026-11-24", 11.0], ["2026-11-25", 13.0], ["2026-11-26", 11.0], ["2026-11-27", 12.0]]}
    with pytest.raises(CollectError):
        sen.parse_crypto({"data": []})


def _quote(sym, price):
    return {"symbol": sym, "name": sym, "price": price, "change": 0.1, "change_pct": 0.5,
            "market_time": None, "closes": [["2025-01-02", price], ["2026-11-25", price]], "extra": 1}


def test_collect_all_parts(monkeypatch, ctx):
    monkeypatch.setattr(sen, "fetch_cnn", lambda start: sen.parse_cnn(_cnn()))
    monkeypatch.setattr(sen, "fetch_crypto", lambda days: sen.parse_crypto(json.loads(fixture_bytes("crypto_fng.json"))))
    monkeypatch.setattr(sen, "fetch_quote", lambda s, rng: _quote(s, {"^VIX": 26.8, "^VIX3M": 26.29}[s]))
    monkeypatch.setattr(sen.time, "sleep", lambda s: None)
    c = ctx({"days": 365, "volatility": ["^VIX", {"symbol": "^vix3m", "label": "VIX 3-month", "note": "3 months"}]})
    monkeypatch.setattr(c, "today", lambda: date(2026, 11, 26))
    d = sen.collect(c)
    assert d["cnn"]["score"] == 15.0 and d["crypto"]["score"] == 12.0
    assert [(q["symbol"], q["label"], q["note"]) for q in d["volatility"]] == [
        ("^VIX", "^VIX", None), ("^VIX3M", "VIX 3-month", "3 months")]
    assert "extra" not in d["volatility"][0]
    assert d["volatility"][0]["closes"] == [["2026-11-25", 26.8]]          # trimmed to the 365-day window
    assert d["term"] == {"near": "^VIX", "far": "VIX 3-month", "ratio": 1.019, "state": "backwardation"}
    assert not c.warnings


def test_collect_keeps_last_good_parts_on_failure(monkeypatch, ctx):
    def boom(*a):
        raise CollectError("HTTP 418")
    monkeypatch.setattr(sen, "fetch_cnn", boom)
    monkeypatch.setattr(sen, "fetch_crypto", boom)
    monkeypatch.setattr(sen, "fetch_quote", lambda s, rng: _quote(s, 20.0) if s == "^VIX" else boom())
    monkeypatch.setattr(sen.time, "sleep", lambda s: None)
    prev = {"cnn": {"score": 40, "rating": "Fear", "history": [["2026-11-20", 40]], "components": []},
            "volatility": [{"symbol": "^VIX3M", "label": "old", "price": 22.0, "closes": []}]}
    c = ctx({"crypto": True, "volatility": ["^VIX", "^VIX3M", "^SKEW"]}, previous=prev)
    monkeypatch.setattr(c, "today", lambda: date(2026, 11, 26))
    d = sen.collect(c)
    assert d["cnn"]["stale"] and d["cnn"]["score"] == 40
    assert d["crypto"] is None                                   # nothing to fall back on
    assert [(q["symbol"], q.get("stale", False)) for q in d["volatility"]] == [("^VIX", False), ("^VIX3M", True)]
    assert d["term"]["ratio"] == round(20 / 22, 3) and d["term"]["state"] == "contango"
    assert len(c.warnings) == 4

    monkeypatch.setattr(sen, "fetch_quote", boom)
    with pytest.raises(CollectError, match="418"):
        sen.collect(ctx({"volatility": ["^VIX"]}))
    with pytest.raises(CollectError, match="days"):
        sen.collect(ctx({"days": 5}))
