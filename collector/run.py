"""Run the collectors that are due and write data/ for the site.

    python -m collector.run                 # normal scheduled run
    python -m collector.run --force         # run every widget now
    python -m collector.run --only gas-aaa  # just one (comma-separate for more)
    python -m collector.run --check         # validate config only

Output (all under --data-dir, default ./data):
    manifest.json          site settings + widget list/order/status
    widgets/<id>.json      one file per widget
    history/<id>.csv       time series that grow every run (gas prices)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .core import CollectError, Context, iso, parse_interval, parse_iso, read_json, utcnow, write_json
from .sources import REGISTRY

ROOT = Path(__file__).resolve().parent.parent
ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
SIZES = {"normal", "wide"}
HOME = "home"                      # section that shows pinned widgets
SLACK = timedelta(minutes=5)       # cron jitter tolerance
ERROR_RETRY = timedelta(hours=1)   # retry failed widgets at least this often


class ConfigError(Exception):
    pass


def load_config(path: Path) -> dict:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}")
    if not isinstance(raw, dict):
        raise ConfigError("top level must be a mapping with 'site' and 'widgets'")
    site = dict(raw.get("site") or {})
    site.setdefault("title", "Dashboard")
    site.setdefault("timezone", "America/Chicago")
    site.setdefault("refresh_minutes", 5)
    try:
        ZoneInfo(site["timezone"])
    except Exception:
        raise ConfigError(f"site.timezone {site['timezone']!r} is not a valid IANA time zone")
    if not isinstance(site["refresh_minutes"], (int, float)) or site["refresh_minutes"] < 1:
        raise ConfigError("site.refresh_minutes must be a number >= 1")
    sections = site.get("sections") or []
    if not isinstance(sections, list):
        raise ConfigError("site.sections must be a list of {id, title}")
    section_ids = []
    for j, sec in enumerate(sections):
        sid = sec.get("id") if isinstance(sec, dict) else None
        if not isinstance(sid, str) or not sid or not set(sid) <= ID_CHARS:
            raise ConfigError(f"site.sections[{j}].id must be lowercase letters, digits and dashes")
        if sid in section_ids:
            raise ConfigError(f"site.sections: duplicate id {sid!r}")
        section_ids.append(sid)
        sec.setdefault("title", sid)
    if sections and section_ids[0] != HOME:
        raise ConfigError(f"site.sections must start with {{id: {HOME}}} (the page that shows pinned widgets)")

    widgets, seen = [], set()
    for i, w in enumerate(raw.get("widgets") or []):
        where = f"widgets[{i}]"
        if not isinstance(w, dict):
            raise ConfigError(f"{where} must be a mapping")
        wid = w.get("id")
        if isinstance(wid, (bool, int, float)):
            raise ConfigError(f"{where}.id {wid!r} was read as a {type(wid).__name__}; quote it, e.g. id: \"on\"")
        if not isinstance(wid, str) or not wid or not set(wid) <= ID_CHARS:
            raise ConfigError(f"{where}.id must be lowercase letters, digits and dashes (got {wid!r})")
        where = f"widget '{wid}'"
        if wid in seen:
            raise ConfigError(f"{where}: duplicate id")
        seen.add(wid)
        if w.get("type") not in REGISTRY:
            raise ConfigError(f"{where}: unknown type {w.get('type')!r}; known: {', '.join(sorted(REGISTRY))}")
        try:
            parse_interval(w.get("refresh", "1h"))
        except ValueError as e:
            raise ConfigError(f"{where}: {e}")
        size = w.get("size", "normal")
        if size not in SIZES:
            raise ConfigError(f"{where}: size must be one of {sorted(SIZES)}")
        opts = w.get("options") or {}
        if not isinstance(opts, dict):
            raise ConfigError(f"{where}: options must be a mapping")
        section = w.get("section")
        if sections:
            if section not in section_ids or section == HOME:
                raise ConfigError(f"{where}: section must be one of {[x for x in section_ids if x != HOME]}"
                                  f" (got {section!r}); use pinned: true to also show it on {HOME}")
        elif section is not None:
            raise ConfigError(f"{where}: has a section but site.sections is not defined")
        if not isinstance(w.get("pinned", False), bool):
            raise ConfigError(f"{where}: pinned must be true or false")
        widgets.append({
            "id": wid,
            "type": w["type"],
            "title": str(w.get("title") or wid),
            "refresh": str(w.get("refresh", "1h")),
            "size": size,
            "enabled": bool(w.get("enabled", True)),
            "section": section,
            "pinned": bool(w.get("pinned", False)),
            "options": opts,
        })
    if not widgets:
        raise ConfigError("no widgets configured")
    # Optional default order of the Home page (pinned widgets); unlisted pinned widgets follow in config order.
    home_order = site.get("home_order")
    if home_order is not None:
        if not isinstance(home_order, list) or not all(isinstance(x, str) for x in home_order):
            raise ConfigError("site.home_order must be a list of widget ids")
        unknown = [x for x in home_order if x not in seen]
        if unknown:
            raise ConfigError(f"site.home_order: unknown widget id(s) {unknown}")
        if len(set(home_order)) != len(home_order):
            raise ConfigError("site.home_order: an id is listed twice")
    return {"site": site, "widgets": widgets}


def config_hash(w: dict) -> str:
    blob = json.dumps({"type": w["type"], "options": w["options"]}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def is_due(w: dict, prev: dict | None, now) -> bool:
    if not prev or prev.get("config_hash") != config_hash(w):
        return True
    last = parse_iso(prev.get("attempted"))
    if last is None:
        return True
    elapsed = now - last
    if elapsed >= parse_interval(w["refresh"]) - SLACK:
        return True
    return prev.get("status") != "ok" and elapsed >= min(parse_interval(w["refresh"]), ERROR_RETRY) - SLACK


def run_widget(w: dict, prev: dict | None, data_dir: Path, tz: ZoneInfo) -> dict:
    src = REGISTRY[w["type"]]
    ctx = Context(w["id"], w["options"], data_dir, tz, previous=(prev or {}).get("data"))
    now = utcnow()
    base = {
        "id": w["id"], "title": w["title"], "type": w["type"], "view": src.view,
        "config_hash": config_hash(w), "attempted": iso(now),
    }
    try:
        data = src.collect(ctx)
        return {**base, "status": "ok", "updated": iso(now), "error": None,
                "warnings": ctx.warnings, "data": data}
    except Exception as e:  # noqa: BLE001 - one widget must never break the others
        msg = str(e) if isinstance(e, CollectError) else f"{type(e).__name__}: {e}"
        if not isinstance(e, CollectError):
            traceback.print_exc()
        keep = prev if prev and prev.get("config_hash") == base["config_hash"] else None
        return {**base, "status": "error", "updated": (keep or {}).get("updated"), "error": msg,
                "warnings": ctx.warnings, "data": (keep or {}).get("data")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config" / "widgets.yaml"))
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--force", action="store_true", help="run every widget regardless of schedule")
    ap.add_argument("--only", default="", help="comma-separated widget ids to run")
    ap.add_argument("--check", action="store_true", help="validate the config and exit")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any widget that ran failed")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # never crash on a console that can't print Unicode
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        cfg = load_config(Path(a.config))
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    if a.check:
        print(f"config OK: {len(cfg['widgets'])} widgets")
        return 0

    data_dir = Path(a.data_dir)
    wdir = data_dir / "widgets"
    tz = ZoneInfo(cfg["site"]["timezone"])
    only = {s.strip() for s in a.only.split(",") if s.strip()}
    unknown = only - {w["id"] for w in cfg["widgets"]}
    if unknown:
        print(f"unknown widget id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    now = utcnow()
    failed = 0
    entries = []
    # Run phase: ordinary widgets first, then "late" ones (e.g. the AI summary, which reads the others).
    latest: dict[str, dict | None] = {}
    order = sorted(cfg["widgets"], key=lambda w: REGISTRY[w["type"]].late)
    for w in order:
        path = wdir / f"{w['id']}.json"
        prev = read_json(path)
        if not w["enabled"]:
            print(f"[off] {w['id']}")
            continue
        want = (w["id"] in only) if only else (a.force or is_due(w, prev, now))
        if want:
            t0 = time.monotonic()
            result = run_widget(w, prev, data_dir, tz)
            took = time.monotonic() - t0
            flag = "ok " if result["status"] == "ok" else "ERR"
            print(f"[{flag}] {w['id']:<22} {took:5.1f}s  {result['error'] or ''}")
            for warn in result["warnings"]:
                print(f"       warning: {warn}")
            if result["status"] != "ok":
                failed += 1
            write_json(path, result)
            prev = result
        else:
            print(f"[--] {w['id']:<22} not due")
        latest[w["id"]] = prev

    # Manifest in config order (= page order).
    for w in cfg["widgets"]:
        prev = latest.get(w["id"])
        if prev:
            entries.append({
                "id": w["id"], "title": w["title"], "view": prev.get("view"), "size": w["size"],
                "section": w["section"], "pinned": w["pinned"],
                "refresh": w["refresh"], "status": prev.get("status"), "updated": prev.get("updated"),
                "attempted": prev.get("attempted"),
            })

    # Drop files for widgets that were removed or disabled.
    keep = {f"{e['id']}.json" for e in entries}
    if wdir.exists():
        for f in wdir.glob("*.json"):
            if f.name not in keep:
                f.unlink()
                print(f"[rm] {f.name}")

    write_json(data_dir / "manifest.json", {
        "site": cfg["site"], "generated": iso(utcnow()), "widgets": entries,
    })
    return 1 if (a.strict and failed) else 0


if __name__ == "__main__":
    sys.exit(main())
