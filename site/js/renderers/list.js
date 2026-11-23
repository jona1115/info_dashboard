// Headline / paper lists. data: {groups: [{label, items: [{title, url, source, date, date_label, meta}], stale}]}
import { el, relTime, safeUrl } from "../util.js";

function item(it) {
  const url = safeUrl(it.url);
  const title = url ? el("a", { class: "t", href: url, target: "_blank", rel: "noopener noreferrer" }, it.title)
                    : el("span", { class: "t" }, it.title);
  const bits = [it.source, it.date_label || relTime(it.date), it.meta].filter(Boolean);
  return el("li", {}, title, bits.length ? el("span", { class: "s" }, bits.join(" · ")) : null);
}

export function render(body, data, widget) {
  const groups = (data && data.groups) || [];
  const grouped = groups.length > 1 || (groups[0] && groups[0].label);
  if (!groups.some((g) => g.items && g.items.length)) {
    body.append(el("p", { class: "muted" }, "Nothing new right now."));
    return;
  }
  if (!grouped) {
    const ul = el("ul", { class: widget.size === "wide" ? "items cols" : "items" }, groups[0].items.map(item));
    body.append(ul);
    return;
  }
  const wrap = el("div", { class: "groups" });
  for (const g of groups) {
    const sec = el("section", { class: "group" }, el("h3", {}, g.label || ""));
    if (g.stale) sec.append(el("p", { class: "muted small" }, "Couldn't refresh; showing earlier items."));
    sec.append(g.items && g.items.length ? el("ul", { class: "items" }, g.items.map(item))
                                         : el("p", { class: "muted small" }, "Nothing recent."));
    wrap.append(sec);
  }
  body.append(wrap);
}
