// Upcoming key dates. data: {as_of, horizon_days, events: [{date, end, time, title, category, url, note}]}
import { el, safeUrl } from "../util.js";

const DAY = 86400000;
const dms = (d) => { const [y, m, dd] = d.split("-").map(Number); return Date.UTC(y, m - 1, dd); };
const fmt = (d, opts) => new Date(dms(d)).toLocaleDateString(undefined, { timeZone: "UTC", ...opts });

/** Whole days from today (in the dashboard's time zone) to a date. */
export function daysUntil(date, tz, now = new Date()) {
  const today = now.toLocaleDateString("en-CA", { timeZone: tz || undefined }); // YYYY-MM-DD
  return Math.round((dms(date) - dms(today)) / DAY);
}

export function countdown(n) {
  if (n < 0) return "now";
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n < 14) return `in ${n} days`;
  return `in ${Math.round(n / 7)} weeks`;
}

function dateLabel(e) {
  const start = fmt(e.date, { month: "short", day: "numeric" });
  if (!e.end || e.end === e.date) return start;
  const sameMonth = e.end.slice(0, 7) === e.date.slice(0, 7);
  return `${start}–${sameMonth ? fmt(e.end, { day: "numeric" }) : fmt(e.end, { month: "short", day: "numeric" })}`;
}

export function render(body, data, widget, site) {
  const events = data.events || [];
  if (!events.length) {
    body.append(el("p", { class: "muted" }, "Nothing scheduled in the next few months."));
    return;
  }
  const ul = el("ul", { class: "cal" });
  for (const e of events) {
    const n = daysUntil(e.date, site && site.timezone);
    const url = safeUrl(e.url);
    const title = url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title;
    ul.append(el("li", { class: n <= 1 ? "soon" : "" },
      el("span", { class: "cal-date" },
        el("strong", {}, dateLabel(e)), el("span", { class: "sub" }, fmt(e.date, { weekday: "short" }))),
      el("span", { class: "cal-what" }, title,
        el("span", { class: "sub" }, [e.time, e.note && !/^(BEA|expected)$/.test(e.note) ? e.note : null,
          e.note === "expected" ? "date per Nasdaq (expected)" : null].filter(Boolean).join(" · "))),
      el("span", { class: "cal-when" }, el("span", { class: "tag" }, e.category), el("span", { class: "sub" }, countdown(n)))));
  }
  body.append(ul);
  body.append(el("p", { class: "muted small" },
    "Fed meetings: federalreserve.gov · GDP and PCE releases: bea.gov · market holidays: NYSE rules · " +
    "earnings: Nasdaq. CPI and jobs-report dates can be added in the config (bls.gov blocks automated readers)."));
}
