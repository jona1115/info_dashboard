// Team scores. data: {groups: [{label, periods, stale, games: [{date, state, status, url, teams: [{abbr, name, home, score, lines, winner, us}]}]}]}
import { el, safeUrl } from "../util.js";

function periodLabels(n, periods) {
  return Array.from({ length: n }, (_, i) => {
    if (i < periods) return periods === 2 ? `H${i + 1}` : `Q${i + 1}`;
    const ot = i - periods + 1;
    return ot === 1 ? "OT" : `${ot}OT`;
  });
}

function when(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function gameBox(g, periods) {
  const us = g.teams.find((t) => t.us);
  const them = g.teams.find((t) => !t.us);
  const where = us && them ? `${us.home ? "vs" : "at"} ${them.name || them.abbr}` : "";
  const n = Math.max(periods, ...g.teams.map((t) => (t.lines || []).length));
  const labels = periodLabels(n, periods);
  const url = safeUrl(g.url);
  const title = el("div", { class: "game-head" },
    url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, where || "Game") : where,
    el("span", { class: "s" }, ` · ${when(g.date)} · `),
    el("span", { class: g.state === "in" ? "s live" : "s" }, g.state === "in" ? `LIVE ${g.status}` : g.status));
  const head = el("tr", {}, el("th", {}, ""), labels.map((l) => el("th", {}, l)), el("th", {}, "T"));
  const rows = g.teams.map((t) => el("tr", { class: t.winner ? "win" : "" },
    el("td", {}, t.abbr),
    labels.map((_, i) => el("td", {}, t.lines && t.lines[i] != null ? String(t.lines[i]) : "")),
    el("td", {}, el("strong", {}, t.score == null ? "—" : String(t.score)))));
  return el("div", { class: "game" }, title,
    el("table", { class: "box" }, el("thead", {}, head), el("tbody", {}, rows)));
}

export function render(body, data) {
  const wrap = el("div", { class: "groups" });
  for (const grp of data.groups || []) {
    const sec = el("section", { class: "group" }, el("h3", {}, grp.label));
    if (grp.stale) sec.append(el("p", { class: "muted small" }, "Couldn't refresh; showing earlier scores."));
    if (!(grp.games || []).length) sec.append(el("p", { class: "muted small" }, "No games yet this season."));
    for (const g of grp.games || []) sec.append(gameBox(g, grp.periods || 4));
    wrap.append(sec);
  }
  body.append(wrap);
}
