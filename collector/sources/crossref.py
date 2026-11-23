"""Newest papers from journals / conference proceedings, via the Crossref API.

options:
  max_items: 6                 # per venue
  venues:
    - label: TRETS
      issn: 1936-7406          # a journal: newest works by ISSN
    - label: FPL
      container_query: International Conference on Field Programmable Logic and Applications
      match: 'field[- ]programmable logic'   # regex the proceedings title must match
      since_days: 800          # how far back to look (proceedings are yearly)
"""
from __future__ import annotations

import re
from datetime import timedelta

from ..core import CollectError, Context, clean_text, http_get
from . import source

API = "https://api.crossref.org"
SELECT = "DOI,title,container-title,published,author,type"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

# Proceedings front matter that is not a paper.
SKIP_TITLE = re.compile(
    r"^(table of contents|front matter|back matter|author index|title page|copyright|"
    r"message from|welcome|committees?|program committee|organizing committee|reviewers|"
    r"sponsors|keynote|cover|index|preface|foreword|editorial|\[?front cover)",
    re.I,
)
_SUPSUB = re.compile(r"\s*<(sup|sub)>\s*(.*?)\s*</\1>\s*", re.I | re.S)


def _title(raw: str) -> str:
    return clean_text(_SUPSUB.sub(r"\2", raw or ""))


def _date(parts) -> tuple[str | None, str | None]:
    """Crossref date-parts -> (sortable ISO, display label)."""
    try:
        p = list(parts[0])
    except (TypeError, IndexError):
        return None, None
    if not p or p[0] is None:
        return None, None
    y, m, d = (p + [1, 1])[:3]
    m = m or 1
    d = d or 1
    label = f"{MONTHS[m - 1]} {d}, {y}" if len(p) >= 3 else (f"{MONTHS[m - 1]} {y}" if len(p) == 2 else str(y))
    return f"{y:04d}-{m:02d}-{d:02d}T00:00:00Z", label


def to_items(works: list[dict], label: str, match: str | None = None) -> list[dict]:
    rx = re.compile(match, re.I) if match else None
    items = []
    for w in works:
        title = _title((w.get("title") or [""])[0])
        container = clean_text((w.get("container-title") or [""])[0])
        if not title or SKIP_TITLE.match(title):
            continue
        if rx and not rx.search(container):
            continue
        sort_iso, date_label = _date((w.get("published") or {}).get("date-parts"))
        authors = []
        for a in w.get("author") or []:
            name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name")
            if name:
                authors.append(name)
        by = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        items.append({
            "title": title,
            "url": f"https://doi.org/{w['DOI']}" if w.get("DOI") else None,
            "source": label,
            "date": sort_iso,
            "date_label": date_label,
            "meta": by or None,
        })
    items = [i for i in items if i["url"]]
    items.sort(key=lambda i: i.get("date") or "", reverse=True)
    return items


def _fetch_venue(ctx: Context, v: dict, n: int) -> list[dict]:
    label = v.get("label") or v.get("issn") or v.get("container_query")
    if v.get("issn"):
        r = http_get(f"{API}/journals/{v['issn']}/works", params={
            "sort": "published", "order": "desc", "rows": max(n * 3, 20), "select": SELECT,
        }, timeout=60)
        works = r.json()["message"]["items"]
        return to_items(works, label)[:n]
    if v.get("container_query"):
        since = (ctx.today() - timedelta(days=int(v.get("since_days", 800)))).isoformat()
        r = http_get(f"{API}/works", params={
            "query.container-title": v["container_query"],
            "filter": f"from-pub-date:{since}",
            "rows": 300, "select": SELECT,
        }, timeout=90)
        works = r.json()["message"]["items"]
        return to_items(works, label, v.get("match") or re.escape(v["container_query"]))[:n]
    raise CollectError(f"venue {label!r} needs issn or container_query")


@source("crossref", view="list")
def collect(ctx: Context) -> dict:
    venues = ctx.options.get("venues") or []
    if not venues:
        raise CollectError("options.venues is required")
    n = int(ctx.options.get("max_items", 6))
    prev = {g.get("label"): g for g in (ctx.previous or {}).get("groups", [])}
    groups, ok = [], 0
    for v in venues:
        label = v.get("label") or v.get("issn") or v.get("container_query")
        try:
            items = _fetch_venue(ctx, v, n)
            groups.append({"label": label, "items": items})
            ok += 1
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"{label}: {e}")
            old = prev.get(label)
            groups.append({"label": label, "items": old["items"] if old else [], "stale": True})
    if not ok:
        raise CollectError("all venues failed")
    return {"groups": groups}
