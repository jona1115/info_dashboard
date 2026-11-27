"""Front-end tests: run tests/browser/*.test.html in headless Chrome against the real site files.

Each test page writes a JSON list of {name, ok, err} into <pre id="results">.
Skipped when Chrome isn't installed, and in CI (so a flaky browser can't block data collection).
Set CHROME=/path/to/chrome to choose the browser.
"""
from __future__ import annotations

import functools
import html
import json
import os
import re
import shutil
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = sorted((ROOT / "tests" / "browser").glob("*.test.html"))
CANDIDATES = [
    os.environ.get("CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chrome") or "",
]
CHROME = next((c for c in CANDIDATES if c and Path(c).exists()), None)

pytestmark = pytest.mark.skipif(CHROME is None or bool(os.environ.get("CI")),
                                reason="needs a local Chrome/Edge; skipped in CI")


def write_fixture_data(root: Path) -> None:
    """A small data/ folder for app-level tests (times are relative to now)."""
    from datetime import date, datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    ts = lambda delta: (now - delta).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    items = lambda n: [{"title": f"Item {i}", "url": f"https://example.com/{i}", "source": "Src"} for i in range(n)]  # noqa: E731
    d0, candles = date(2025, 1, 1), []
    while len(candles) < 300:
        if d0.weekday() < 5:
            i = len(candles)
            o = 100 + i * 0.2
            c = o + (1 if i % 2 else -1)
            candles.append([d0.isoformat(), o, max(o, c) + 0.5, min(o, c) - 0.5, c, 1_000_000 + i * 1000])
        d0 += timedelta(days=1)
    closes = [c[4] for c in candles]
    sma = lambda n: [None if i < n - 1 else sum(closes[i - n + 1:i + 1]) / n for i in range(len(closes))]  # noqa: E731
    widgets = [
        ("gas-a", "Gas A", "list", "gas", True, {"groups": [{"label": None, "items": items(2)}]}),
        ("mkt", "Markets A", "list", "markets", True, {"groups": [{"label": None, "items": items(3)}]}),
        ("chart", "Charts", "candles", "markets", False, {
            "periods": ["1M", "3M"], "default_period": "3M", "ma": [20, 50],
            "tabs": [{"symbol": "AAA", "label": "AAA", "name": "AAA fund", "currency": "USD", "candles": candles,
                      "ma": {"20": sma(20), "50": sma(50)}}]}),
        ("news-a", "News A", "list", "news", False, {"groups": [{"label": None, "items": items(4)}]}),
    ]
    data = root / "data"
    (data / "widgets").mkdir(parents=True, exist_ok=True)
    entries = []
    for wid, title, view, section, pinned, payload in widgets:
        w = {"id": wid, "title": title, "type": "x", "view": view, "status": "ok", "updated": ts(timedelta(hours=11)),
             "attempted": ts(timedelta(hours=11)), "error": None, "warnings": [], "data": payload}
        (data / "widgets" / f"{wid}.json").write_text(json.dumps(w), encoding="utf-8")
        entries.append({"id": wid, "title": title, "view": view, "size": "wide" if view == "candles" else "normal",
                        "section": section, "pinned": pinned, "refresh": "30m", "status": "ok",
                        "updated": w["updated"], "attempted": w["attempted"]})
    manifest = {"site": {"title": "Test", "timezone": "America/Chicago", "refresh_minutes": 5, "sections": [
        {"id": "home", "title": "Home"}, {"id": "markets", "title": "Markets & Economy"},
        {"id": "news", "title": "News"}, {"id": "gas", "title": "Gas Prices"}]},
        "generated": ts(timedelta(hours=11)), "widgets": entries}
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Same site under /stale/, built as for deploy (versioned code URLs), with data collected 13 hours
    # ago (past the 12-hour warning).
    from collector.build_site import build
    stale = root / "stale"
    build(stale, "test123")
    shutil.copytree(data, stale / "data", dirs_exist_ok=True)
    (stale / "data" / "manifest.json").write_text(
        json.dumps(dict(manifest, generated=ts(timedelta(hours=13)))), encoding="utf-8")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    shutil.copytree(ROOT / "site", root, dirs_exist_ok=True)
    (root / "__tests__").mkdir()
    for p in PAGES:
        shutil.copy(p, root / "__tests__" / p.name)
    write_fixture_data(root)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path_factory.mktemp("chrome")
    httpd.shutdown()


@pytest.mark.parametrize("page", [p.name for p in PAGES])
def test_browser_page(server, page):
    base, profile = server
    out = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={profile}", "--window-size=1200,900", "--virtual-time-budget=15000",
         "--dump-dom", f"{base}/__tests__/{page}"],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    m = re.search(r'<pre id="results">(.*?)</pre>', out.stdout, re.S)
    assert m, f"no results in page output:\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}"
    text = html.unescape(m.group(1))
    assert text != "pending", "test page did not finish (JavaScript error before results were written?)"
    results = json.loads(text)
    failed = [r for r in results if not r["ok"]]
    assert results, "no tests ran"
    assert not failed, "\n\n".join(f"{r['name']}:\n{r['err']}" for r in failed)
