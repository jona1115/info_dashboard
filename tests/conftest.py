import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).resolve().parent / "fixtures"

from collector.core import Context  # noqa: E402


class FakeResponse:
    def __init__(self, body: bytes):
        self.content = body
        self.text = body.decode("utf-8")
        self.status_code = 200

    def json(self):
        return json.loads(self.text)


def fixture_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


@pytest.fixture
def fake_http(monkeypatch):
    """Route http_get in a source module to local fixtures: fake_http(module, {url_substring: fixture})."""
    def install(module, routes: dict, calls: list | None = None):
        def fake(url, **kw):
            if calls is not None:
                calls.append((url, kw.get("params")))
            key = url + json.dumps(kw.get("params") or {}, sort_keys=True)
            for frag, name in routes.items():
                if frag in key:
                    if isinstance(name, Exception):
                        raise name
                    return FakeResponse(fixture_bytes(name))
            raise AssertionError(f"unexpected URL in test: {key}")
        monkeypatch.setattr(module, "http_get", fake)
    return install


@pytest.fixture
def ctx(tmp_path):
    def make(options, wid="w", previous=None):
        return Context(wid, options, tmp_path, ZoneInfo("America/Chicago"), previous=previous)
    return make
