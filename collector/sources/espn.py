"""Game scores for one team: final + quarter/half scores (ESPN's public scoreboard API).

options:
  team_id: 66                 # ESPN team id (66 = Iowa State)
  recent: 3                   # completed games per sport (live games are always included)
  sports:
    - {label: Football, path: football/college-football, periods: 4}
    - {label: Men's Basketball, path: basketball/mens-college-basketball, periods: 2}
    - {label: Women's Basketball, path: basketball/womens-college-basketball, periods: 4}
"""
from __future__ import annotations

from ..core import CollectError, Context, attempt, http_get
from . import source

API = "https://site.api.espn.com/apis/site/v2/sports"
WEB = "https://www.espn.com"


def schedule(path: str, team_id) -> list[dict]:
    """Regular season + postseason events, oldest first."""
    events = {}
    for season_type in (2, 3):
        r = http_get(f"{API}/{path}/teams/{team_id}/schedule", params={"seasontype": season_type})
        for e in r.json().get("events") or []:
            events[e["id"]] = e
    return sorted(events.values(), key=lambda e: e.get("date") or "")


def _state(e: dict) -> str:
    try:
        return e["competitions"][0]["status"]["type"]["state"]  # pre | in | post
    except (KeyError, IndexError, TypeError):
        return "pre"


def pick(events: list[dict], recent: int) -> list[dict]:
    live = [e for e in events if _state(e) == "in"]
    done = [e for e in events if _state(e) == "post"][-recent:] if recent > 0 else []
    return live + list(reversed(done))  # live first, then newest final first


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def game(path: str, event: dict, team_id) -> dict:
    r = http_get(f"{API}/{path}/summary", params={"event": event["id"]})
    comp = r.json()["header"]["competitions"][0]
    status = comp.get("status", {}).get("type", {})
    teams = []
    for t in comp.get("competitors") or []:
        info = t.get("team") or {}
        teams.append({
            "abbr": info.get("abbreviation") or info.get("shortDisplayName") or "?",
            "name": info.get("displayName") or info.get("location") or "",
            "home": t.get("homeAway") == "home",
            "score": _num(t.get("score")),
            "lines": [_num(l.get("displayValue", l.get("value"))) for l in t.get("linescores") or []],
            "winner": bool(t.get("winner")),
            "us": str(info.get("id")) == str(team_id),
        })
    teams.sort(key=lambda t: t["home"])  # away first, home second (scoreboard convention)
    league = path.split("/")[-1]
    return {
        "id": event["id"],
        "date": event.get("date"),
        "state": status.get("state"),
        "status": status.get("shortDetail") or status.get("description") or "",
        "url": f"{WEB}/{league}/game/_/gameId/{event['id']}",
        "teams": teams,
    }


@source("espn_team", view="scores")
def collect(ctx: Context) -> dict:
    o = ctx.options
    team_id = o.get("team_id")
    sports = o.get("sports") or []
    if not team_id or not sports:
        raise CollectError("options.team_id and options.sports are required")
    recent = int(o.get("recent", 3))
    prev = {g.get("label"): g for g in (ctx.previous or {}).get("groups", [])}
    groups, ok = [], 0
    for s in sports:
        label = s.get("label") or s["path"]
        events = attempt(ctx, f"{label} schedule", lambda s=s: schedule(s["path"], team_id))
        if events is None:
            old = prev.get(label)
            groups.append({**(old or {"label": label, "periods": int(s.get("periods", 4)), "games": []}), "stale": True})
            continue
        games = []
        for e in pick(events, recent):
            g = attempt(ctx, f"{label} game {e.get('id')}", lambda e=e, s=s: game(s["path"], e, team_id))
            if g:
                games.append(g)
        ok += 1
        groups.append({"label": label, "periods": int(s.get("periods", 4)), "games": games})
    if not ok:
        raise CollectError("; ".join(ctx.warnings) or "no data")
    return {"groups": groups}
