// Dashboard shell: loads data/manifest.json, draws one card per widget, and re-checks periodically.
import { RENDERERS } from "./renderers/index.js";
import { absTime, el, relTime } from "./util.js";

const DATA = "data/";
const grid = document.getElementById("grid");
const generatedEl = document.getElementById("generated");
const cache = new Map();   // id -> {key, json}
let site = { title: "Dashboard", timezone: undefined, refresh_minutes: 5 };
let manifest = null;
let lastCheck = null;
let timer = null;
const renderErrors = [];

async function getJSON(path) {
  const r = await fetch(`${DATA}${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

const INTERVAL_MS = { m: 60e3, h: 3600e3, d: 86400e3 };
function intervalMs(s) {
  const m = /^(\d+)\s*([mhd])$/.exec(String(s || "").trim());
  return m ? Number(m[1]) * INTERVAL_MS[m[2]] : 3600e3;
}

function rel(iso) {
  return el("span", { "data-rel": iso || "" }, relTime(iso));
}

function cardFor(entry) {
  let card = document.getElementById(`w-${entry.id}`);
  if (!card) {
    card = el("section", { class: "card", id: `w-${entry.id}` },
      el("div", { class: "card-head" }, el("h2", {}), el("span", { class: "card-meta" })),
      el("div", { class: "card-body" }, el("p", { class: "muted small" }, "Loading…")));
  }
  card.classList.toggle("wide", entry.size === "wide");
  card.querySelector("h2").textContent = entry.title;
  return card;
}

function destroyCharts(node) {
  if (!window.Chart) return;
  node.querySelectorAll("canvas").forEach((c) => { const ch = window.Chart.getChart(c); if (ch) ch.destroy(); });
}

function renderCard(card, entry, w) {
  const meta = card.querySelector(".card-meta");
  const body = card.querySelector(".card-body");
  destroyCharts(body);
  body.replaceChildren();
  const updated = w.updated || null;
  meta.replaceChildren(updated ? rel(updated) : "no data yet");
  meta.title = [
    updated ? `Updated ${absTime(updated, site.timezone)}` : null,
    w.attempted ? `Last checked ${absTime(w.attempted, site.timezone)}` : null,
    `Refreshes every ${entry.refresh}`,
  ].filter(Boolean).join("\n");

  if (w.status === "error") {
    body.append(el("p", { class: "notice err" },
      `Couldn't update ${relTime(w.attempted)}: ${w.error || "unknown error"}.`,
      w.data ? ` Showing data from ${relTime(updated)}.` : ""));
  } else if (updated && Date.now() - Date.parse(updated) > 3 * intervalMs(entry.refresh) + 3600e3) {
    body.append(el("p", { class: "notice" }, `This data is ${relTime(updated).replace(" ago", "")} old.`));
  }
  if (w.warnings && w.warnings.length) {
    body.append(el("details", { class: "notice small" },
      el("summary", {}, `${w.warnings.length} source${w.warnings.length > 1 ? "s" : ""} failed this time`),
      el("ul", {}, w.warnings.map((x) => el("li", {}, x)))));
  }
  if (!w.data) return;
  const fn = RENDERERS[w.view] || RENDERERS.raw;
  try {
    fn(body, w.data, { ...entry, ...w }, site);
  } catch (e) {
    console.error(`render ${entry.id}`, e);
    renderErrors.push(`${entry.id}: ${e.message}`);
    body.append(el("p", { class: "notice err" }, `Display error: ${e.message}`));
  }
}

async function refresh() {
  try {
    manifest = await getJSON("manifest.json");
  } catch (e) {
    generatedEl.textContent = "Can't reach data right now; will retry.";
    document.body.dataset.state = "error";
    return;
  }
  lastCheck = Date.now();
  site = { ...site, ...(manifest.site || {}) };
  document.title = site.title;
  document.getElementById("site-title").textContent = site.title;
  document.getElementById("boot")?.remove();

  // Reconcile cards with the manifest: order, additions, removals.
  const ids = new Set(manifest.widgets.map((w) => w.id));
  for (const card of [...grid.querySelectorAll(".card")]) {
    if (!ids.has(card.id.slice(2))) { destroyCharts(card); card.remove(); cache.delete(card.id.slice(2)); }
  }
  const jobs = manifest.widgets.map(async (entry) => {
    const card = cardFor(entry);
    grid.append(card); // append moves existing nodes, so this also applies the order
    const key = `${entry.attempted}|${entry.status}`;
    const hit = cache.get(entry.id);
    if (hit && hit.key === key) return;
    try {
      const w = await getJSON(`widgets/${entry.id}.json`);
      cache.set(entry.id, { key, json: w });
      renderCard(card, entry, w);
    } catch (e) {
      if (!hit) card.querySelector(".card-body").replaceChildren(el("p", { class: "notice err" }, `Couldn't load: ${e.message}`));
    }
  });
  await Promise.all(jobs);
  if (!manifest.widgets.length) grid.replaceChildren(el("p", { class: "muted" }, "No widgets configured."));
  tick();
  document.body.dataset.state = renderErrors.length ? "render-error" : "ready";
  schedule();
}

function rerenderAll() {
  if (!manifest) return;
  for (const entry of manifest.widgets) {
    const hit = cache.get(entry.id);
    const card = document.getElementById(`w-${entry.id}`);
    if (hit && card) renderCard(card, entry, hit.json);
  }
}

function tick() {
  document.querySelectorAll("[data-rel]").forEach((n) => { n.textContent = relTime(n.dataset.rel); });
  if (manifest) {
    generatedEl.textContent = `Data collected ${relTime(manifest.generated)}`;
    generatedEl.title = `Collected ${absTime(manifest.generated, site.timezone)}; page checked ${absTime(new Date(lastCheck).toISOString(), site.timezone)}`;
  }
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(refresh, Math.max(1, Number(site.refresh_minutes) || 5) * 60e3);
}

document.getElementById("refresh").addEventListener("click", refresh);
document.getElementById("theme").addEventListener("click", () => {
  const dark = getComputedStyle(document.documentElement).colorScheme.includes("dark");
  const next = dark ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
  rerenderAll();
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rerenderAll);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && lastCheck && Date.now() - lastCheck > 60e3) refresh();
});
setInterval(tick, 30e3);
window.__dashboard = { renderErrors, refresh };
refresh();
