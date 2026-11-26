import json
from datetime import date
from types import SimpleNamespace

import pytest

from collector import run
from collector.core import CollectError, write_json
from collector.sources import ai_summary, calendar as cal

# ------------------------------------------------------------------ NYSE holidays


def _closures(y):
    return [(e["date"], e["title"]) for e in cal.nyse_holidays(y) if e["title"].startswith("Markets closed")]


def test_easter():
    assert cal.easter(2026) == date(2026, 4, 5)
    assert cal.easter(2027) == date(2027, 3, 28)
    assert cal.easter(2024) == date(2024, 3, 31)


def test_nyse_holidays_2026_2027():
    assert [d for d, _ in _closures(2026)] == [
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
        "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"]          # Jul 4 is a Saturday -> Friday
    assert [d for d, _ in _closures(2027)] == [
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
        "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"]          # Sat -> Fri; Sun Jul 4 -> Mon
    early26 = [e["date"] for e in cal.nyse_holidays(2026) if e["title"].startswith("Early close")]
    assert early26 == ["2026-11-27", "2026-12-24"]
    # New Year's Day on a Saturday is not observed on the Friday before (Jan 1, 2022 was a Saturday)
    assert "2021-12-31" not in [d for d, _ in _closures(2021)]
    assert "2022-01-01" not in [d for d, _ in _closures(2022)]


# ------------------------------------------------------------------ FOMC / BEA / earnings parsing

FOMC_HTML = """
<div class="panel"><div class="panel-heading"><h4>2027 FOMC Meetings</h4></div>
 <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>January</strong></div>
   <div class="fomc-meeting__date">26-27</div></div>
 <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>April/May</strong></div>
   <div class="fomc-meeting__date">30-1*</div></div>
 <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>August</strong></div>
   <div class="fomc-meeting__date">22 (notation vote)</div></div>
</div>
<div class="panel"><div class="panel-heading">FOMC Search</div></div>"""

BEA_HTML = """<table>
<tr><th>Year 2026</th><th></th><th>Release</th></tr>
<tr><td>December 2 8:30 AM</td><td>News</td><td>GDP by County and Personal Income by County, 2025</td></tr>
<tr><td>December 23 8:30 AM</td><td>News</td><td>GDP (Third Estimate), Industries, Corporate Profits</td></tr>
<tr><td>December 23 8:30 AM</td><td>News</td><td>Personal Income and Outlays, November 2026</td></tr>
<tr><th>Year 2027</th><th></th><th>Release</th></tr>
<tr><td>January 29 10:00 AM</td><td>News</td><td>Personal Income and Outlays, December 2026</td></tr>
</table>"""


def test_parse_fomc():
    ev = cal.parse_fomc(FOMC_HTML)
    assert [(e["date"], e["end"]) for e in ev] == [("2027-01-26", "2027-01-27"), ("2027-04-30", "2027-05-01")]
    assert "projections" in ev[1]["title"] and "projections" not in ev[0]["title"]
    with pytest.raises(CollectError):
        cal.parse_fomc("<html>down for maintenance</html>")


def test_parse_bea_filters_and_tracks_year():
    ev = cal.parse_bea(BEA_HTML, [r"^GDP \(", "Personal Income and Outlays"])
    assert [(e["date"], e["time"], e["title"][:26]) for e in ev] == [
        ("2026-12-23", "8:30 AM ET", "GDP (Third Estimate), Indu"),
        ("2026-12-23", "8:30 AM ET", "Personal Income and Outlay"),
        ("2027-01-29", "10:00 AM ET", "Personal Income and Outlay")]


def test_parse_nasdaq_earnings():
    got = cal.parse_nasdaq_earnings({"data": {"reportText": "Costco is expected* to report earnings on  12/10/2026 "
                                                            "after market close.  The report will be for..."}}, "COST")
    assert got["date"] == "2026-12-10" and got["title"] == "COST earnings (after the close)"
    assert cal.parse_nasdaq_earnings({"data": {"reportText": "Our vendor hasn't provided us with the date."}}, "INTC") is None


def test_calendar_collect(monkeypatch, ctx):
    monkeypatch.setattr(cal, "http_get", lambda url, **kw: SimpleNamespace(
        text=FOMC_HTML if "federalreserve" in url else BEA_HTML))

    def earnings(sym):
        if sym == "BAD":
            raise CollectError("HTTP 403")
        return None if sym == "INTC" else cal._ev(date(2026, 12, 10), f"{sym} earnings", "Earnings")

    monkeypatch.setattr(cal, "fetch_earnings", earnings)
    monkeypatch.setattr(cal.time, "sleep", lambda s: None)
    prev = {"events": [{"date": "2026-12-20", "end": None, "title": "BAD earnings", "category": "Earnings"}]}
    c = ctx({"horizon_days": 40, "earnings": ["INTC", "COST", "BAD"],
             "events": [{"date": "2026-12-11", "title": "CPI (November)", "category": "Data", "time": "8:30 AM ET"},
                        {"date": "2026-11-01", "title": "already past"}]}, previous=prev)
    monkeypatch.setattr(c, "today", lambda: date(2026, 11, 26))
    d = cal.collect(c)
    titles = [(e["date"], e["title"]) for e in d["events"]]
    assert ("2026-11-26", "Markets closed · Thanksgiving") in titles          # today counts as upcoming
    assert ("2026-12-10", "COST earnings") in titles
    assert ("2026-12-20", "BAD earnings") in titles                          # kept from last run on failure
    assert ("2026-12-11", "CPI (November)") in titles
    assert all(t != "already past" for _, t in titles)
    assert all(e["date"] <= "2027-01-05" or e["category"] == "Fed" for e in d["events"])   # 40-day horizon
    assert d["events"][-1]["category"] == "Fed" and d["events"][-1]["date"] == "2027-01-26"  # next FOMC always
    dates = [e["date"] for e in d["events"][:-1]]
    assert dates == sorted(dates)
    assert any("BAD earnings date" in w for w in c.warnings)
    assert not any("INTC" in w for w in c.warnings)                         # "not announced" is not an error
    with pytest.raises(CollectError):
        cal.collect(ctx({"fomc": False, "bea": [], "events": [{"title": "no date"}]}))


# ------------------------------------------------------------------ AI summary


def _widget(tmp, wid, view, data, title=None):
    write_json(tmp / "widgets" / f"{wid}.json", {"id": wid, "title": title or wid, "view": view, "data": data})


def test_digest_covers_views(tmp_path, ctx):
    _widget(tmp_path, "news", "list", {"groups": [{"label": "CNN", "items": [{"title": "Big story", "source": "CNN"}]}]})
    _widget(tmp_path, "mk", "stock", {"range": "6mo", "groups": [{"quotes": [
        {"label": "S&P 500", "price": 7182.78, "currency": "USD", "change_pct": -0.25, "chg_1m_pct": -6.4,
         "chg_range_pct": -4.5}]}]})
    _widget(tmp_path, "gas", "gas", {"grade": "regular", "regions": [
        {"label": "Texas", "prices": {"current": {"regular": 3.072}, "week_ago": {"regular": 3.2}}}]})
    _widget(tmp_path, "empty", "list", {"groups": [{"label": "x", "items": []}]})
    digest, used = ai_summary.build_digest(ctx({}), ["news", "mk", "gas", "empty", "missing"], 5)
    assert used == ["news", "mk", "gas"]
    assert "- Big story (CNN)" in digest
    assert "S&P 500: 7182.78 USD, day -0.25%" in digest
    assert "Texas average regular: $3.072/gal (week ago $3.200)" in digest


class _FakeClient:
    calls = []
    reply = {"title": "Stocks slip; gas eases", "bullets": [{"topic": "Markets", "text": "S&P 500 fell 0.25%."}]}
    stop = "end_turn"

    def __init__(self, *a, **k):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        _FakeClient.calls.append(kw)
        return SimpleNamespace(
            stop_reason=_FakeClient.stop, model=kw["model"],
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=json.dumps(_FakeClient.reply))],
            usage=SimpleNamespace(input_tokens=3000, output_tokens=400))


def test_ai_summary_collect(monkeypatch, tmp_path, ctx):
    import anthropic
    _widget(tmp_path, "news", "list", {"groups": [{"label": None, "items": [{"title": "Big story", "source": "CNN"}]}]})
    c = ctx({"inputs": ["news"]})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(CollectError, match="ANTHROPIC_API_KEY is not set"):
        ai_summary.collect(c)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", _FakeClient)
    monkeypatch.setattr(_FakeClient, "stop", "end_turn")
    _FakeClient.calls.clear()
    d = ai_summary.collect(c)
    assert d["title"] == "Stocks slip; gas eases" and d["bullets"][0]["topic"] == "Markets"
    assert d["sources"] == ["news"] and d["model"] == "claude-opus-5-5" and d["input_tokens"] == 3000
    kw = _FakeClient.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in kw and "temperature" not in kw       # Opus 5.5: adaptive by default, no sampling params
    assert "Big story (CNN)" in kw["messages"][0]["content"]

    monkeypatch.setattr(_FakeClient, "stop", "refusal")
    with pytest.raises(CollectError, match="declined"):
        ai_summary.collect(c)
    monkeypatch.setattr(_FakeClient, "stop", "end_turn")
    monkeypatch.setattr(_FakeClient, "reply", {"title": "x", "bullets": []})
    with pytest.raises(CollectError, match="no bullets"):
        ai_summary.collect(c)
    with pytest.raises(CollectError, match="none of the input widgets"):
        ai_summary.collect(ctx({"inputs": ["missing"]}))


def test_late_widgets_run_after_the_others(tmp_path, monkeypatch):
    from collector.sources import REGISTRY, Source
    order = []
    monkeypatch.setitem(REGISTRY, "early", Source("early", "list", lambda c: order.append(c.widget_id) or {"groups": []}))
    monkeypatch.setitem(REGISTRY, "latetype",
                        Source("latetype", "list", lambda c: order.append(c.widget_id) or {"groups": []}, True))
    cfg = tmp_path / "w.yaml"
    cfg.write_text("widgets:\n  - {id: summary, type: latetype}\n  - {id: a, type: early}\n  - {id: b, type: early}\n")
    assert run.main(["--config", str(cfg), "--data-dir", str(tmp_path / "data")]) == 0
    assert order == ["a", "b", "summary"]
    m = json.loads((tmp_path / "data" / "manifest.json").read_text())
    assert [w["id"] for w in m["widgets"]] == ["summary", "a", "b"]   # page order still follows the config
