"""Generic RSS / Atom headlines (Google News searches, team sites, blogs...).

options:
  feeds: [url | {url, source}]          # one ungrouped list, or
  groups: [{label, feeds: [...]}]       # several labelled lists
  max_items: 10                         # per group
  max_age_days: 14                      # drop older entries (optional)
  include: [regex, ...]                 # keep only titles matching one (optional)
  exclude: [regex, ...]                 # drop titles matching any (optional)
  require: [regex, ...]                 # keep only titles matching ALL of them (optional)
  latin_only: true                      # drop titles mostly in non-Latin scripts (optional)
  source_include: [regex, ...]          # keep only items whose publisher matches one (optional)
  source_exclude: [regex, ...]          # drop items whose publisher matches any (optional)

Per feed only:
  strip: [regex, ...]                   # remove these parts from titles (after the feed's filters ran)

include / exclude / require may also be set on a single feed or a group; every level applies.
Regexes are case-insensitive; use (?-i:Intel) for a case-sensitive part.
"""
from __future__ import annotations

import re
from datetime import timedelta

import feedparser

from ..core import CollectError, Context, clean_text, http_get, parse_iso, struct_to_iso, utcnow
from . import source


def _norm_feeds(feeds) -> list[dict]:
    out = []
    for f in feeds or []:
        out.append({"url": f} if isinstance(f, str) else dict(f))
    for f in out:
        if not f.get("url"):
            raise ValueError("every feed needs a url")
    return out


def parse_feed(content: bytes, source_label: str | None = None) -> list[dict]:
    d = feedparser.parse(content)
    if not d.entries and d.bozo:
        raise CollectError(f"unreadable feed ({d.bozo_exception})")
    feed_title = clean_text(d.feed.get("title")) or None
    items = []
    for e in d.entries:
        title = clean_text(e.get("title"))
        link = e.get("link")
        if not title or not link:
            continue
        src = source_label
        gsrc = e.get("source")  # Google News puts the publisher here and appends " - Publisher" to titles
        if gsrc and gsrc.get("title"):
            publisher = clean_text(gsrc.get("title"))
            suffix = f" - {publisher}"
            if title.endswith(suffix):
                title = title[: -len(suffix)].rstrip()
            src = src or publisher
        when = struct_to_iso(e.get("published_parsed") or e.get("updated_parsed"))
        if when and parse_iso(when) > utcnow() + timedelta(hours=1):
            when = None  # future-dated (scheduled/op-ed) items would otherwise sit on top forever
        items.append({
            "title": title,
            "url": link,
            "source": src or feed_title,
            "date": when,
        })
    return items


def _title_key(title: str) -> str:
    # A 48-char prefix also catches the same story re-titled slightly by another outlet.
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:48]


def _mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    latin = sum(1 for c in letters if c < "ɐ")  # Basic Latin .. Latin Extended-B
    return latin / len(letters) >= 0.8


def match_filters(items: list[dict], include=None, exclude=None, require=None) -> list[dict]:
    inc = [re.compile(p, re.I) for p in include or []]
    exc = [re.compile(p, re.I) for p in exclude or []]
    req = [re.compile(p, re.I) for p in require or []]
    return [i for i in items
            if (not inc or any(r.search(i["title"]) for r in inc))
            and all(r.search(i["title"]) for r in req)
            and not any(r.search(i["title"]) for r in exc)]


def filter_sources(items: list[dict], include=None, exclude=None) -> list[dict]:
    inc = [re.compile(p, re.I) for p in include or []]
    exc = [re.compile(p, re.I) for p in exclude or []]
    return [i for i in items
            if (not inc or any(r.search(i.get("source") or "") for r in inc))
            and not any(r.search(i.get("source") or "") for r in exc)]


def filter_items(items: list[dict], *, include=None, exclude=None, require=None, max_age_days=None,
                 max_items=10, latin_only=False) -> list[dict]:
    items = match_filters(items, include, exclude, require)
    if latin_only:
        items = [i for i in items if _mostly_latin(i["title"])]
    cutoff = utcnow() - timedelta(days=float(max_age_days)) if max_age_days else None
    seen, out = set(), []
    # newest first; undated items go last
    items = sorted(items, key=lambda i: i.get("date") or "", reverse=True)
    for it in items:
        t = it["title"]
        if cutoff and it.get("date"):
            dt = parse_iso(it["date"])
            if dt and dt < cutoff:
                continue
        k = _title_key(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= int(max_items):
            break
    return out


def _previous_group(ctx: Context, label):
    for g in (ctx.previous or {}).get("groups", []):
        if g.get("label") == label:
            return g
    return None


@source("rss", view="list")
def collect(ctx: Context) -> dict:
    o = ctx.options
    if o.get("groups"):
        group_defs = [{**g, "feeds": _norm_feeds(g.get("feeds"))} for g in o["groups"]]
    else:
        group_defs = [{"label": None, "feeds": _norm_feeds(o.get("feeds"))}]
    if not any(g["feeds"] for g in group_defs):
        raise CollectError("no feeds configured")

    groups, any_ok = [], False
    for g in group_defs:
        raw, ok = [], False
        for f in g["feeds"]:
            try:
                r = http_get(f["url"], browser=True)
                got = parse_feed(r.content, f.get("source"))
                got = match_filters(got, f.get("include"), f.get("exclude"), f.get("require"))
                for rx in f.get("strip") or []:
                    for it in got:
                        it["title"] = re.sub(rx, "", it["title"]).strip()
                raw.extend(it for it in got if it["title"])
                ok = True
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"{f['url'].split('?')[0]}: {e}")
        raw = match_filters(raw, g.get("include"), g.get("exclude"), g.get("require"))
        raw = filter_sources(raw, o.get("source_include"), o.get("source_exclude"))
        items = filter_items(
            raw,
            include=o.get("include"),
            exclude=o.get("exclude"),
            require=o.get("require"),
            max_age_days=o.get("max_age_days"),
            max_items=o.get("max_items", 10),
            latin_only=bool(o.get("latin_only")),
        )
        if ok:
            any_ok = True
            groups.append({"label": g["label"], "items": items})
        else:
            prev = _previous_group(ctx, g["label"])
            groups.append({"label": g["label"], "items": prev["items"] if prev else [], "stale": True})
    if not any_ok:
        raise CollectError("all feeds failed")
    return {"groups": groups}
