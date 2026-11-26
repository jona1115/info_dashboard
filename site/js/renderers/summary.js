// AI daily briefing. data: {title, bullets: [{topic, text}], date, sources, model}
import { el } from "../util.js";

export function render(body, data) {
  if (data.title) body.append(el("p", { class: "brief-title" }, data.title));
  body.append(el("ul", { class: "brief" }, (data.bullets || []).map((b) =>
    el("li", {}, el("span", { class: "tag" }, b.topic || "•"), " ", b.text))));
  const day = data.date
    ? new Date(`${data.date}T12:00:00Z`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" })
    : "";
  body.append(el("p", { class: "muted small" },
    `AI-written summary of this dashboard's own data${day ? ` for ${day}` : ""}` +
    `${data.model ? ` (${data.model})` : ""}. It can make mistakes; check the cards for details.`));
}
