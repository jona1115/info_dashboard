"""Game news (results) from several leagues, via ESPN's public news API.

ESPN tags each article with a type; "Recap" articles are game results ("X beat Y 39-35"), so by default
only those are kept (no trade rumours, opinion, or video). One labelled list per group.

options:
  types: [Recap]              # ESPN article types to keep (add Preview for upcoming games)
  max_items: 5                # per group
  max_age_days: 3
  hide_empty: true            # leave out groups with nothing recent (e.g. leagues in their off-season)
  groups:
    - {label: NBA, espn: [basketball/nba]}
    - {label: NCAA Basketball, espn: [basketball/mens-college-basketball, basketball/womens-college-basketball]}
    # A group can use RSS feeds instead (e.g. the Olympics, which ESPN has no feed for), with the rss
    # widget's include / exclude / require title filters, and can be limited to date ranges:
    - label: Olympics
      rss: ["https://news.google.com/rss/search?q=Olympics+when:2d&hl=en-US&gl=US&ceid=US:en"]
      require: ['olympi']
      active: [[2028-07-14, 2028-07-30]]   # only collected/shown between these dates (inclusive)
"""
from __future__ import annotations

from datetime import date, timedelta

from ..core import CollectError, Context, clean_text, http_get, parse_iso, utcnow
from . import source
from .rss import _title_key, match_filters, parse_feed

API = "https://site.api.espn.com/apis/site/v2/sports"


def parse_espn_news(payload: dict, types: set[str]) -> list[dict]:
    items = []
    for a in payload.get("articles") or []:
        if types and a.get("type") not in types:
            continue
        title = clean_text(a.get("headline"))
        url = ((a.get("links") or {}).get("web") or {}).get("href")
        if not title or not url:
            continue
        items.append({
            "title": title,
            "url": url.replace("http://", "https://", 1),
            "source": "ESPN",
            "date": a.get("published"),
        })
    return items


def active_now(windows, today: date) -> bool:
    if not windows:
        return True
    for w in windows:
        start, end = (date.fromisoformat(str(x)) for x in w)
        if start <= today <= end:
            return True
    return False


def _group_items(ctx: Context, g: dict, types: set[str]) -> list[dict]:
    items, ok, errors = [], 0, []
    for path in g.get("espn") or []:
        try:
            r = http_get(f"{API}/{path}/news", params={"limit": 50})
            items.extend(parse_espn_news(r.json(), types))
            ok += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{path}: {e}")
    for url in g.get("rss") or []:
        try:
            r = http_get(url, browser=True)
            items.extend(parse_feed(r.content))
            ok += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url.split('?')[0]}: {e}")
    for e in errors:
        ctx.warn(f"{g.get('label')}: {e}")
    if not ok:
        raise CollectError("all sources failed")
    return match_filters(items, g.get("include"), g.get("exclude"), g.get("require"))


@source("espn_news", view="list")
def collect(ctx: Context) -> dict:
    o = ctx.options
    groups_cfg = o.get("groups") or []
    if not groups_cfg:
        raise CollectError("options.groups is required")
    for g in groups_cfg:
        if not g.get("espn") and not g.get("rss"):
            raise CollectError(f"group {g.get('label')!r} needs espn paths or rss feeds")
    types = set(o.get("types") or ["Recap"])
    n = int(o.get("max_items", 5))
    cutoff = utcnow() - timedelta(days=float(o.get("max_age_days", 3)))
    hide_empty = bool(o.get("hide_empty", True))
    prev = {g.get("label"): g for g in (ctx.previous or {}).get("groups", [])}
    today = ctx.today()

    out, ok = [], 0
    for g in groups_cfg:
        label = g.get("label")
        if not active_now(g.get("active"), today):
            continue
        try:
            raw = _group_items(ctx, g, types)
            ok += 1
        except CollectError:
            old = prev.get(label)
            if old and old.get("items"):
                out.append({"label": label, "items": old["items"], "stale": True})
            continue
        seen, items = set(), []
        for it in sorted(raw, key=lambda i: i.get("date") or "", reverse=True):
            when = parse_iso(it.get("date"))
            if when is None or when < cutoff or when > utcnow() + timedelta(hours=1):
                continue
            k = _title_key(it["title"])
            if k in seen:
                continue
            seen.add(k)
            items.append(it)
            if len(items) >= n:
                break
        if items or not hide_empty:
            out.append({"label": label, "items": items})
    if not ok and any(active_now(g.get("active"), today) for g in groups_cfg):
        raise CollectError("; ".join(ctx.warnings) or "no data")
    return {"groups": out}
