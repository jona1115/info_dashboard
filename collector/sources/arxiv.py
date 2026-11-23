"""Newest arXiv preprints matching a search query.

options:
  query: 'cat:cs.AR AND (abs:FPGA OR abs:ASIC)'   # arXiv API search syntax
  max_items: 10
"""
from __future__ import annotations

import feedparser

from ..core import CollectError, Context, clean_text, http_get, struct_to_iso
from . import source

API = "https://export.arxiv.org/api/query"


def parse_arxiv(content: bytes) -> list[dict]:
    d = feedparser.parse(content)
    if not d.entries and d.bozo:
        raise CollectError(f"unreadable arXiv response ({d.bozo_exception})")
    items = []
    for e in d.entries:
        title = clean_text(e.get("title"))
        link = (e.get("link") or e.get("id") or "").replace("http://", "https://")
        if not title or not link:
            continue
        authors = [a.get("name") for a in e.get("authors", []) if a.get("name")]
        by = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        cat = (e.get("arxiv_primary_category") or {}).get("term")
        items.append({
            "title": title,
            "url": link,
            "source": f"arXiv {cat}" if cat else "arXiv",
            "date": struct_to_iso(e.get("published_parsed")),
            "meta": by or None,
        })
    return items


@source("arxiv", view="list")
def collect(ctx: Context) -> dict:
    q = ctx.options.get("query")
    if not q:
        raise CollectError("options.query is required")
    n = int(ctx.options.get("max_items", 10))
    r = http_get(API, params={
        "search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": n,
    }, timeout=60)
    return {"groups": [{"label": None, "items": parse_arxiv(r.content)[:n]}]}
