"""Daily AI briefing: Claude summarizes what the other widgets collected.

Runs after every other widget in the same collection run (late=True), reads their saved data, builds a
compact digest, and asks Claude for a short briefing (structured JSON -> bullets). Needs the
ANTHROPIC_API_KEY environment variable (a GitHub Actions secret); without it the card says so.

options:
  model: claude-opus-5-5
  effort: low                 # low | medium | high  (summaries don't need deep reasoning)
  bullets: 7                  # target number of bullets
  per_group: 6                # headlines taken from each list group
  inputs: [headlines-world, ai-news, markets, gas-aaa]   # widget ids to summarize, in priority order
"""
from __future__ import annotations

import json
import os

from ..core import CollectError, Context, read_json
from . import source

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "One-line headline for the day (max ~12 words)."},
        "bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "1-3 word label, e.g. Markets, World, AI, Gas, Sports"},
                    "text": {"type": "string", "description": "One or two plain sentences."},
                },
                "required": ["topic", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "bullets"],
    "additionalProperties": False,
}

SYSTEM = (
    "You write a short daily briefing for a personal information dashboard. You are given the data the "
    "dashboard collected today: headlines (with publisher), market numbers, gas prices, economic data, "
    "sports scores and upcoming dates.\n"
    "Rules:\n"
    "- Use only facts present in the data. Do not add outside knowledge, predictions, or advice.\n"
    "- Prefer the most significant items; merge headlines that describe the same story.\n"
    "- Include concrete numbers when the data has them (prices, % changes, scores, dates).\n"
    "- Neutral, plain language; no hype, no emoji, no markdown.\n"
    "- Each bullet: a short topic label and one or two sentences."
)


def _pct(v):
    return "n/a" if v is None else f"{v:+.2f}%"


def digest_widget(w: dict, per_group: int) -> str | None:
    """Plain-text digest of one widget's saved data, by view."""
    data, view, title = w.get("data") or {}, w.get("view"), w.get("title") or w.get("id")
    lines = []
    if view == "list":
        for g in data.get("groups") or []:
            items = (g.get("items") or [])[:per_group]
            if not items:
                continue
            if g.get("label"):
                lines.append(f"[{g['label']}]")
            lines += [f"- {i['title']}" + (f" ({i['source']})" if i.get("source") else "") for i in items]
    elif view == "stock":
        groups = data.get("groups") or [{"quotes": data.get("quotes") or []}]
        for g in groups:
            for q in g.get("quotes") or []:
                lines.append(f"- {q.get('label') or q.get('symbol')}: {q.get('price')} {q.get('currency', '')}, "
                             f"day {_pct(q.get('change_pct'))}, 1M {_pct(q.get('chg_1m_pct'))}, "
                             f"{data.get('range', '6mo')} {_pct(q.get('chg_range_pct'))}")
    elif view == "gas":
        grade = data.get("grade", "regular")
        for r in data.get("regions") or []:
            p = (r.get("prices") or {})
            cur, wk = (p.get("current") or {}).get(grade), (p.get("week_ago") or {}).get(grade)
            if cur is not None:
                lines.append(f"- {r['label']} average {grade}: ${cur:.3f}/gal" +
                             (f" (week ago ${wk:.3f})" if wk is not None else ""))
    elif view == "costco":
        whs = [x for x in data.get("warehouses") or [] if (x.get("prices") or {}).get("regular")]
        if whs:
            low = min(whs, key=lambda x: x["prices"]["regular"])
            lines.append(f"- Lowest Costco regular nearby: ${low['prices']['regular']:.3f} at {low['name']}")
    elif view == "macro":
        for r in data.get("rows") or []:
            cells = {k: c for k, c in (r.get("cells") or {}).items() if c}
            if cells:
                lines.append(f"- {r['label']}: " + ", ".join(f"{k} {c['v']:.1f}% ({c['period']})" for k, c in cells.items()))
    elif view == "scores":
        for g in data.get("groups") or []:
            for game in (g.get("games") or [])[:2]:
                teams = " vs ".join(f"{t['abbr']} {t.get('score')}" for t in game.get("teams") or [])
                lines.append(f"- {g['label']}: {teams} ({game.get('status')}, {str(game.get('date'))[:10]})")
    elif view == "calendar":
        for e in (data.get("events") or [])[:8]:
            lines.append(f"- {e['date']}: {e['title']}")
    elif view == "yield_curve":
        for s in data.get("spreads") or []:
            if s.get("points"):
                lines.append(f"- U.S. Treasury {s['label']} spread: {s['points'][-1][1]:+.2f} pp")
    if not lines:
        return None
    return f"## {title}\n" + "\n".join(lines)


def build_digest(ctx: Context, inputs: list[str], per_group: int) -> tuple[str, list[str]]:
    parts, used = [], []
    for wid in inputs:
        w = read_json(ctx.data_dir / "widgets" / f"{wid}.json")
        if not w or not w.get("data"):
            continue
        text = digest_widget(w, per_group)
        if text:
            parts.append(text)
            used.append(wid)
    return "\n\n".join(parts), used


def summarize(digest: str, *, model: str, effort: str, bullets: int, today: str) -> tuple[dict, dict]:
    import anthropic  # imported here so the rest of the dashboard works without the package

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=8000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if a safety classifier declines, Anthropic re-runs it on a suitable model
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Today is {today}. Write about {bullets} bullets from this data.\n\n{digest}",
        }],
    )
    if response.stop_reason == "refusal":
        raise CollectError("the model declined to summarize today's data")
    if response.stop_reason == "max_tokens":
        raise CollectError("summary was cut off (max_tokens)")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise CollectError("empty response from the model")
    out = json.loads(text)
    items = [b for b in out.get("bullets") or [] if b.get("text")]
    if not items:
        raise CollectError("the model returned no bullets")
    meta = {"model": response.model, "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens}
    return {"title": str(out.get("title") or "").strip(), "bullets": items}, meta


@source("ai_summary", view="summary", late=True)
def collect(ctx: Context) -> dict:
    o = ctx.options
    inputs = [str(x) for x in o.get("inputs") or []]
    if not inputs:
        raise CollectError("options.inputs (widget ids to summarize) is required")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise CollectError("ANTHROPIC_API_KEY is not set: add it as a repository secret to turn on the AI summary")
    effort = str(o.get("effort", "low"))
    if effort not in ("low", "medium", "high", "xhigh", "max"):
        raise CollectError("effort must be low, medium, high, xhigh or max")
    digest, used = build_digest(ctx, inputs, int(o.get("per_group", 6)))
    if not digest:
        raise CollectError("none of the input widgets has data yet")
    import anthropic
    try:
        result, meta = summarize(digest, model=str(o.get("model", "claude-opus-5-5")), effort=effort,
                                 bullets=int(o.get("bullets", 7)), today=ctx.today().isoformat())
    except anthropic.AuthenticationError:
        raise CollectError("the Anthropic API key was rejected (check the ANTHROPIC_API_KEY secret)")
    except anthropic.RateLimitError:
        raise CollectError("Anthropic API rate limit; will retry next run")
    except anthropic.APIStatusError as e:
        raise CollectError(f"Anthropic API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise CollectError("could not reach the Anthropic API")
    except json.JSONDecodeError:
        raise CollectError("the model's reply was not valid JSON")
    return {**result, "date": ctx.today().isoformat(), "sources": used, **meta}
