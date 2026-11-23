"""Shared helpers: HTTP, time handling, history storage, file writes."""
from __future__ import annotations

import csv
import html
import io
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
BOT_UA = "personal-dashboard/1.0 (+https://pages.github.com)"


class CollectError(Exception):
    """Raised by a collector when it cannot produce any usable data."""


# --------------------------------------------------------------------------- time

_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([mhd])\s*$")


def parse_interval(text: str) -> timedelta:
    """'30m' / '6h' / '1d' -> timedelta."""
    m = _INTERVAL_RE.match(str(text))
    if not m:
        raise ValueError(f"bad interval {text!r}; use e.g. 30m, 6h, 1d")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError(f"interval must be positive: {text!r}")
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def struct_to_iso(st) -> str | None:
    """feedparser time.struct_time (UTC) -> ISO string."""
    if not st:
        return None
    try:
        return iso(datetime(*st[:6], tzinfo=timezone.utc))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- http

def http_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    browser: bool = False,
    timeout: float = 30,
    retries: int = 2,
) -> requests.Response:
    """GET with a few retries on network errors / 429 / 5xx. Raises CollectError."""
    hdrs = {"User-Agent": BROWSER_UA if browser else BOT_UA}
    if browser:
        hdrs.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
        })
    if headers:
        hdrs.update(headers)
    last = "unknown error"
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code} from {r.url.split('?')[0]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise CollectError(last)


# --------------------------------------------------------------------------- history

class History:
    """Small append/upsert time-series store: CSV with columns date,series,value.

    One value per (date, series). Re-writing the same date replaces it, so the
    latest reading of the day wins. Rows are never removed by normal use.
    """

    HEADER = ["date", "series", "value"]

    def __init__(self, path: Path):
        self.path = Path(path)
        self.rows: dict[tuple[str, str], float] = {}
        self._loaded_count = 0
        if self.path.exists():
            with self.path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        self.rows[(row["date"], row["series"])] = float(row["value"])
                    except (KeyError, TypeError, ValueError):
                        continue
            self._loaded_count = len(self.rows)

    def upsert(self, day: str | date, series: str, value: float) -> None:
        day = day.isoformat() if isinstance(day, date) else str(day)
        date.fromisoformat(day)  # validate format
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"non-finite value for {series} on {day}")
        self.rows[(day, series)] = round(value, 4)

    def has(self, day: str, series: str) -> bool:
        return (day, series) in self.rows

    def series(self, name: str, since: str | None = None) -> list[list]:
        pts = sorted((d, v) for (d, s), v in self.rows.items() if s == name and (since is None or d >= since))
        return [[d, v] for d, v in pts]

    def names(self) -> list[str]:
        return sorted({s for _, s in self.rows})

    def save(self) -> None:
        if len(self.rows) < self._loaded_count:  # safety net: history only grows
            raise RuntimeError(f"refusing to shrink history {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(self.HEADER)
        for (d, s), v in sorted(self.rows.items()):
            w.writerow([d, s, f"{v:g}"])
        write_text_atomic(self.path, buf.getvalue())
        self._loaded_count = len(self.rows)


# --------------------------------------------------------------------------- files

def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _finite(obj: Any) -> Any:
    """NaN / Infinity are not valid JSON (browsers reject the whole file): turn them into null."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def write_json(path: Path, obj: Any) -> None:
    text = json.dumps(_finite(obj), ensure_ascii=False, indent=1, sort_keys=False, allow_nan=False)
    write_text_atomic(path, text + "\n")


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- context

@dataclass
class Context:
    """What a collector receives."""

    widget_id: str
    options: dict
    data_dir: Path
    tz: ZoneInfo
    previous: dict | None = None  # last saved data payload, if any
    warnings: list[str] = field(default_factory=list)
    _histories: dict[str, History] = field(default_factory=dict)

    def history(self, name: str | None = None) -> History:
        name = name or self.widget_id
        if name not in self._histories:
            self._histories[name] = History(self.data_dir / "history" / f"{name}.csv")
        return self._histories[name]

    def save_histories(self) -> None:
        for h in self._histories.values():
            h.save()

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def attempt(ctx: Context, label: str, fn: Callable[[], Any]) -> Any | None:
    """Run one part of a collector; on failure record a warning and return None."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - we want every failure as a warning
        ctx.warn(f"{label}: {e}")
        return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(text: str | None) -> str:
    """Strip tags / collapse whitespace."""
    if not text:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", text))).strip()
