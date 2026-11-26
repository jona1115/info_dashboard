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
