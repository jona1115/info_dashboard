import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from collector import run
from collector.core import CollectError, History, parse_interval, utcnow, iso
from collector.sources import REGISTRY, Source
from conftest import ROOT


# ------------------------------------------------------------------ core

def test_parse_interval():
    assert parse_interval("30m") == timedelta(minutes=30)
    assert parse_interval("6h") == timedelta(hours=6)
    assert parse_interval(" 1d ") == timedelta(days=1)
    for bad in ("", "5", "1w", "0h", "-1h"):
        with pytest.raises(ValueError):
            parse_interval(bad)


def test_history_upsert_save_reload_and_never_shrinks(tmp_path):
    p = tmp_path / "h.csv"
    h = History(p)
    h.upsert("2026-11-21", "a", 1.5)
    h.upsert("2026-11-22", "a", 2)
    h.upsert("2026-11-22", "a", 2.25)     # same day replaces
    h.upsert("2026-11-22", "b", 9)
    h.save()
    h2 = History(p)
    assert h2.series("a") == [["2026-11-21", 1.5], ["2026-11-22", 2.25]]
    assert h2.series("a", since="2026-11-22") == [["2026-11-22", 2.25]]
    with pytest.raises(ValueError):
        h2.upsert("11/22/2026", "a", 1)
    h2.rows.pop(("2026-11-21", "a"))
    with pytest.raises(RuntimeError):
        h2.save()


# ------------------------------------------------------------------ config

def test_real_config_is_valid():
    cfg = run.load_config(ROOT / "config" / "widgets.yaml")
    assert cfg["widgets"], "config has widgets"


def _cfg(tmp_path, text):
    p = tmp_path / "w.yaml"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize("text, msg", [
    ("widgets: [{id: a, type: nope}]", "unknown type"),
    ("widgets: [{id: A, type: rss}]", "lowercase"),
    ("widgets: [{id: a, type: rss}, {id: a, type: rss}]", "duplicate"),
    ("widgets: [{id: a, type: rss, refresh: 2w}]", "interval"),
    ("widgets: [{id: a, type: rss, size: huge}]", "size"),
    ("site: {timezone: Mars/Base}\nwidgets: [{id: a, type: rss}]", "time zone"),
    ("widgets: []", "no widgets"),
    ("widgets: [{id: a, type: rss, options: [1]}]", "options"),
    ("widgets: [{id: off, type: rss}]", "quote it"),
])
def test_bad_configs(tmp_path, text, msg):
    with pytest.raises(run.ConfigError, match=msg):
        run.load_config(_cfg(tmp_path, text))


# ------------------------------------------------------------------ scheduling

def test_is_due():
    w = {"type": "rss", "options": {}, "refresh": "1h"}
    now = utcnow()
    h = run.config_hash(w)
    assert run.is_due(w, None, now)
    assert run.is_due(w, {"config_hash": "other", "attempted": iso(now)}, now)
    ok = {"config_hash": h, "status": "ok"}
    assert not run.is_due(w, {**ok, "attempted": iso(now - timedelta(minutes=30))}, now)
    assert run.is_due(w, {**ok, "attempted": iso(now - timedelta(minutes=56))}, now)   # cron jitter slack
    w6 = {**w, "refresh": "6h"}
    err = {"config_hash": run.config_hash(w6), "status": "error"}
    assert not run.is_due(w6, {**err, "attempted": iso(now - timedelta(minutes=30))}, now)
    assert run.is_due(w6, {**err, "attempted": iso(now - timedelta(minutes=58))}, now)  # errors retry hourly


# ------------------------------------------------------------------ end-to-end runner (fake source)

@pytest.fixture
def fake_source(monkeypatch):
    state = {"fail": False, "calls": 0}

    def collect(ctx):
        state["calls"] += 1
        if state["fail"]:
            raise CollectError("boom")
        ctx.warn("one feed was slow")
        return {"groups": [{"label": None, "items": [{"title": "hi", "url": "https://e.x"}]}]}

    monkeypatch.setitem(REGISTRY, "fake", Source("fake", "list", collect))
    return state


def test_runner_end_to_end(tmp_path, fake_source):
    data = tmp_path / "data"
    cfg = _cfg(tmp_path, """
site: {title: Test}
widgets:
  - {id: one, type: fake, refresh: 1h, size: wide}
  - {id: two, type: fake, refresh: 1h}
  - {id: hidden, type: fake, enabled: false}
""")
    assert run.main(["--config", str(cfg), "--data-dir", str(data)]) == 0
    m = json.loads((data / "manifest.json").read_text())
    assert m["site"]["title"] == "Test"
    assert [w["id"] for w in m["widgets"]] == ["one", "two"]
    assert m["widgets"][0]["size"] == "wide" and m["widgets"][0]["view"] == "list"
    one = json.loads((data / "widgets" / "one.json").read_text())
    assert one["status"] == "ok" and one["warnings"] == ["one feed was slow"]
    assert fake_source["calls"] == 2

    # Not due -> not re-run.
    run.main(["--config", str(cfg), "--data-dir", str(data)])
    assert fake_source["calls"] == 2

    # A failure keeps the previous data and marks the error.
    fake_source["fail"] = True
    assert run.main(["--config", str(cfg), "--data-dir", str(data), "--only", "one"]) == 0
    one = json.loads((data / "widgets" / "one.json").read_text())
    assert one["status"] == "error" and one["error"] == "boom"
    assert one["data"]["groups"][0]["items"][0]["title"] == "hi"
    assert one["updated"] is not None
    assert run.main(["--config", str(cfg), "--data-dir", str(data), "--only", "one", "--strict"]) == 1

    # Removing a widget from the config removes its file and manifest entry.
    cfg2 = _cfg(tmp_path, "widgets: [{id: one, type: fake}]")
    run.main(["--config", str(cfg2), "--data-dir", str(data)])
    assert not (data / "widgets" / "two.json").exists()
    m = json.loads((data / "manifest.json").read_text())
    assert [w["id"] for w in m["widgets"]] == ["one"]

    assert run.main(["--config", str(cfg), "--data-dir", str(data), "--only", "nope"]) == 2


# ------------------------------------------------------------------ site <-> collector contract

def test_every_view_has_a_renderer():
    index = (ROOT / "site" / "js" / "renderers" / "index.js").read_text(encoding="utf-8")
    exported = set(re.search(r"RENDERERS\s*=\s*\{([^}]*)\}", index).group(1).replace(" ", "").split(","))
    for src in REGISTRY.values():
        if src.type == "fake":
            continue
        assert src.view in exported, f"no renderer for view {src.view!r} (type {src.type})"
    for f in re.findall(r'from "\./([\w-]+\.js)"', index):
        assert (ROOT / "site" / "js" / "renderers" / f).exists(), f


def test_site_files_exist():
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:src|href)="((?!data:|http)[^"]+)"', html):
        assert (ROOT / "site" / ref).exists(), ref


def test_write_json_never_emits_nan(tmp_path):
    from collector.core import write_json
    write_json(tmp_path / "x.json", {"a": float("nan"), "b": [1.0, float("inf")], "c": 2.5})
    assert json.loads((tmp_path / "x.json").read_text()) == {"a": None, "b": [1.0, None], "c": 2.5}
    h = History(tmp_path / "h.csv")
    with pytest.raises(ValueError):
        h.upsert("2026-11-22", "a", float("nan"))


def test_macro_skips_nan_observations():
    from collector.sources.macro import _by_area
    rows = [{"REF_AREA": "US", "TIME_PERIOD": "2025-10", "OBS_VALUE": "NaN"},
            {"REF_AREA": "US", "TIME_PERIOD": "2025-11", "OBS_VALUE": "3.1"}]
    assert _by_area(rows) == {"US": [["2025-11", 3.1]]}


# ------------------------------------------------------------------ sections / pins

SECTIONS = """site:
  sections:
    - {id: home, title: Home}
    - {id: markets, title: Markets}
widgets:
"""


@pytest.mark.parametrize("widgets, msg", [
    ("  - {id: a, type: rss}", "section must be one of"),                      # missing section
    ("  - {id: a, type: rss, section: home}", "section must be one of"),       # home is for pins only
    ("  - {id: a, type: rss, section: nope}", "section must be one of"),
    ("  - {id: a, type: rss, section: markets, pinned: maybe}", "pinned must be true or false"),
])
def test_bad_section_configs(tmp_path, widgets, msg):
    with pytest.raises(run.ConfigError, match=msg):
        run.load_config(_cfg(tmp_path, SECTIONS + widgets))


def test_sections_must_start_with_home_and_be_unique(tmp_path):
    with pytest.raises(run.ConfigError, match="start with"):
        run.load_config(_cfg(tmp_path, "site: {sections: [{id: news}]}\nwidgets: [{id: a, type: rss, section: news}]"))
    with pytest.raises(run.ConfigError, match="duplicate"):
        run.load_config(_cfg(tmp_path, "site: {sections: [{id: home}, {id: home}]}\nwidgets: [{id: a, type: rss}]"))
    with pytest.raises(run.ConfigError, match="site.sections is not defined"):
        run.load_config(_cfg(tmp_path, "widgets: [{id: a, type: rss, section: news}]"))


def test_manifest_carries_section_and_pin(tmp_path, fake_source):
    data = tmp_path / "data"
    cfg = _cfg(tmp_path, SECTIONS + "  - {id: one, type: fake, section: markets, pinned: true}\n"
                                   "  - {id: two, type: fake, section: markets}\n")
    assert run.main(["--config", str(cfg), "--data-dir", str(data)]) == 0
    m = json.loads((data / "manifest.json").read_text())
    assert [s["id"] for s in m["site"]["sections"]] == ["home", "markets"]
    assert [(w["id"], w["section"], w["pinned"]) for w in m["widgets"]] == [("one", "markets", True), ("two", "markets", False)]


def test_real_config_sections():
    cfg = run.load_config(ROOT / "config" / "widgets.yaml")
    pinned = [w["id"] for w in cfg["widgets"] if w["pinned"]]
    assert pinned == ["gas-aaa", "costco-gas", "usd-myr", "markets"]
    assert {w["section"] for w in cfg["widgets"]} == {"markets", "news", "sports", "gas"}
    assert [s["id"] for s in cfg["site"]["sections"]] == ["home", "markets", "news", "sports", "gas"]
    assert not [w["id"] for w in cfg["widgets"] if w["section"] in ("news", "sports") and w["pinned"]],         "no news/sports widget pinned by default"
    assert next(w for w in cfg["widgets"] if w["id"] == "isu-sports")["section"] == "sports"
    assert "intc-stock" not in {w["id"] for w in cfg["widgets"]}
    candles = next(w for w in cfg["widgets"] if w["type"] == "candles")
    assert [t["symbol"] for t in candles["options"]["tickers"]] == ["VOO", "QQQM", "INTC"]
    assert "MTD" not in candles["options"]["periods"] and "YTD" in candles["options"]["periods"]
