// Dashboard shell: loads data/manifest.json, draws one card per widget, and re-checks periodically.
import { RENDERERS } from "./renderers/index.js";
import { absTime, el, relTime } from "./util.js";
import { HOME, isPinned, sectionFromHash, setPinned, visibleIn } from "./sections.js";
import { exportLayout, hasCustomOrder, resetOrder, saveOrder, sectionOrder } from "./layout.js";

const DATA = "data/";
const grid = document.getElementById("grid");
const generatedEl = document.getElementById("generated");
const nav = document.getElementById("sections");
const STALE_PAGE_MS = 12 * 3600e3;   // GitHub's scheduler runs a few times a day; warn only when it has stopped
const cache = new Map();   // id -> {key, json}
let site = { title: "Dashboard", timezone: undefined, refresh_minutes: 5 };
let manifest = null;
let lastCheck = null;
let timer = null;
const renderErrors = [];
let dragging = null;   // the card being dragged (layout edit mode)

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

function pinButton(card) {
  const b = el("button", { type: "button", class: "pin" });
  b.addEventListener("click", () => {
    const entry = card._entry;
    setPinned(entry, !isPinned(entry));
    applySections();
  });
  return b;
}

// ---------------------------------------------------------------- layout (reorder) mode

function currentSection() {
  return sectionFromHash((site.sections || []).filter((s) => s && s.id));
}

/** Ids of a section's widgets, in config order. */
function sectionIds(section) {
  const sections = (site.sections || []).filter((s) => s && s.id);
  return manifest.widgets.filter((w) => visibleIn(w, section, sections)).map((w) => w.id);
}

const shownCards = () => [...grid.querySelectorAll(":scope > .card:not([hidden])")];

function saveShownOrder() {
  const section = currentSection();
  saveOrder(section, shownCards().map((c) => c.id.slice(2)), sectionIds(section), site);
  updateLayoutUI();
}

function moveBy(card, step) {
  const cards = shownCards();
  const i = cards.indexOf(card), j = i + step;
  if (i < 0 || j < 0 || j >= cards.length) return;
  if (step < 0) grid.insertBefore(card, cards[j]);
  else grid.insertBefore(card, cards[j].nextSibling);
  saveShownOrder();
  const again = card.querySelector(step < 0 ? ".move-up" : ".move-down");
  (again.disabled ? card.querySelector(step < 0 ? ".move-down" : ".move-up") : again).focus();
}

function moveTools(card) {
  const name = () => (card._entry ? card._entry.title : "widget");
  const handle = el("button", { type: "button", class: "move drag", title: "Drag to move" }, "⠿");
  const up = el("button", { type: "button", class: "move move-up", title: "Move earlier" }, "↑");
  const down = el("button", { type: "button", class: "move move-down", title: "Move later" }, "↓");
  handle.addEventListener("pointerdown", (e) => startDrag(e, card));
  up.addEventListener("click", () => moveBy(card, -1));
  down.addEventListener("click", () => moveBy(card, 1));
  const label = () => {
    handle.setAttribute("aria-label", `Drag ${name()}`);
    up.setAttribute("aria-label", `Move ${name()} earlier`);
    down.setAttribute("aria-label", `Move ${name()} later`);
  };
  [handle, up, down].forEach((b) => b.addEventListener("focus", label));
  return [handle, up, down];
}

/** Move the dragged card to where the pointer at (x, y) says it should go. */
function dragTo(card, x, y) {
  const target = shownCards().find((c) => {
    if (c === card) return false;
    const r = c.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  });
  if (!target) return;
  const r = target.getBoundingClientRect();
  const fullRow = r.width > grid.clientWidth * 0.6;      // one card per row: compare heights, else widths
  const before = fullRow ? y < r.top + r.height / 2 : x < r.left + r.width / 2;
  const ref = before ? target : target.nextSibling;
  if (ref !== card && card.nextSibling !== ref) grid.insertBefore(card, ref);
}

function startDrag(e, card) {
  if (!document.body.classList.contains("editing") || (e.pointerType === "mouse" && e.button !== 0)) return;
  e.preventDefault();
  try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
  dragging = card;
  card.classList.add("dragging");
  let x = e.clientX, y = e.clientY, raf = 0;
  const scroll = () => {   // auto-scroll near the top / bottom edge of the window
    const edge = 60, dy = y < edge ? -14 : y > window.innerHeight - edge ? 14 : 0;
    if (dy) { window.scrollBy(0, dy); dragTo(card, x, y); }
    raf = requestAnimationFrame(scroll);
  };
  const move = (ev) => { x = ev.clientX; y = ev.clientY; dragTo(card, x, y); };
  const end = () => {
    cancelAnimationFrame(raf);
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
    card.classList.remove("dragging");
    dragging = null;
    saveShownOrder();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
  raf = requestAnimationFrame(scroll);
}

function setEditing(on) {
  document.body.classList.toggle("editing", on);
  document.getElementById("layout").setAttribute("aria-pressed", String(on));
  document.getElementById("layout-bar").hidden = !on;
  updateLayoutUI();
}

function updateLayoutUI() {
  const cards = shownCards();
  cards.forEach((c, i) => {
    c.querySelector(".move-up").disabled = i === 0;
    c.querySelector(".move-down").disabled = i === cards.length - 1;
  });
  if (manifest) document.getElementById("layout-reset").disabled = !hasCustomOrder(currentSection());
}

/** Hidden option (?export in the URL): this browser's layout as JSON, to make it the default. */
function showExport() {
  const text = JSON.stringify(exportLayout(manifest, site), null, 2);
  let dlg = document.getElementById("export");
  if (!dlg) {
    dlg = el("dialog", { id: "export", class: "export" },
      el("form", { method: "dialog" },
        el("p", {}, el("strong", {}, "Layout export"),
          " · copy all of this and send it to Claude to make it the dashboard's default layout."),
        el("textarea", { readonly: true, rows: 18, spellcheck: "false", "aria-label": "Layout export" }),
        el("p", {}, el("button", { type: "submit" }, "Close"))));
    document.body.append(dlg);
  }
  const ta = dlg.querySelector("textarea");
  ta.value = text;
  if (!dlg.open) dlg.showModal();
  ta.focus();
  ta.select();
  ta.scrollTop = 0;
  console.log(text);
  return text;
}

function cardFor(entry) {
  let card = document.getElementById(`w-${entry.id}`);
  if (!card) {
    card = el("section", { class: "card", id: `w-${entry.id}` },
      el("div", { class: "card-head" }, el("h2", {}),
        el("span", { class: "card-tools" }, el("span", { class: "card-meta" }))),
      el("div", { class: "card-body" }, el("p", { class: "muted small" }, "Loading…")));
    card.querySelector(".card-tools").append(...moveTools(card), pinButton(card));
  }
  card._entry = entry;
  card.classList.toggle("wide", entry.size === "wide");
  card.querySelector("h2").textContent = entry.title;
  return card;
}

/** Show the cards that belong to the current section; update the tab bar and pin buttons. */
function applySections() {
  if (!manifest) return;
  const sections = (site.sections || []).filter((s) => s && s.id);
  const current = sectionFromHash(sections);
  nav.hidden = !sections.length;
  nav.replaceChildren(...sections.map((s) => el("a", {
    href: `#${s.id}`, class: s.id === current ? "active" : "", "aria-current": s.id === current ? "page" : null,
  }, s.title)));
  // Put this section's cards in this browser's order (moving cards only when the order differs).
  if (!dragging) {
    const want = sectionOrder(current, sectionIds(current), site)
      .map((id) => document.getElementById(`w-${id}`)).filter(Boolean);
    const have = [...grid.querySelectorAll(":scope > .card")].filter((c) => want.includes(c));
    if (want.some((c, i) => c !== have[i])) want.forEach((c) => grid.append(c));
  }
  let shown = 0;
  for (const entry of manifest.widgets) {
    const card = document.getElementById(`w-${entry.id}`);
    if (!card) continue;
    const vis = visibleIn(entry, current, sections);
    const appearing = vis && card.hidden;
    card.hidden = !vis;
    // Chart.js doesn't notice a chart drawn while its card was hidden (0 x 0) becoming visible: resize it.
    if (appearing && window.Chart) {
      card.querySelectorAll("canvas").forEach((c) => { const ch = window.Chart.getChart(c); if (ch) ch.resize(); });
    }
    if (vis) shown++;
    const pin = card.querySelector(".pin");
    const on = isPinned(entry);
    pin.hidden = !sections.length;
    pin.textContent = on ? "★" : "☆";
    pin.setAttribute("aria-pressed", String(on));
    pin.title = on ? "Pinned to Home (click to unpin)" : "Pin to Home";
    pin.setAttribute("aria-label", `${on ? "Unpin" : "Pin"} ${entry.title} ${on ? "from" : "to"} Home`);
  }
  let empty = document.getElementById("empty-section");
  if (!shown && manifest.widgets.length) {
    if (!empty) { empty = el("p", { id: "empty-section", class: "muted" }); grid.append(empty); }
    empty.textContent = current === HOME
      ? "Nothing pinned yet. Use ☆ on any card to pin it here."
      : "No widgets in this section.";
    empty.hidden = false;
  } else if (empty) {
    empty.hidden = true;
  }
  updateLayoutUI();
  const title = sections.find((s) => s.id === current);
  document.title = title && current !== HOME ? `${title.title} · ${site.title}` : site.title;
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
  } else if (updated && Date.now() - Date.parse(updated) > Math.max(3 * intervalMs(entry.refresh), STALE_PAGE_MS) + 3600e3) {
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
  document.getElementById("site-title").textContent = site.title;
  document.getElementById("boot")?.remove();

  // Reconcile cards with the manifest: order, additions, removals.
  const ids = new Set(manifest.widgets.map((w) => w.id));
  for (const card of [...grid.querySelectorAll(".card")]) {
    if (!ids.has(card.id.slice(2))) { destroyCharts(card); card.remove(); cache.delete(card.id.slice(2)); }
  }
  for (const entry of manifest.widgets) {
    const card = cardFor(entry);
    if (!card.isConnected) grid.append(card);
  }
  applySections();   // order and visibility before the data arrives
  const jobs = manifest.widgets.map(async (entry) => {
    const card = cardFor(entry);
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
  applySections();
  tick();
  document.body.dataset.state = renderErrors.length ? "render-error" : "ready";
  schedule();
  if (wantExport) { wantExport = false; showExport(); }
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
    // GitHub's scheduler runs a few times a day at irregular times; flag only a long silence.
    const age = Date.now() - Date.parse(manifest.generated);
    const stuck = age > STALE_PAGE_MS;
    generatedEl.classList.toggle("stale", stuck);
    generatedEl.textContent = stuck
      ? `⚠ Data last collected ${relTime(manifest.generated)}: collection may be stuck`
      : `Data collected ${relTime(manifest.generated)}`;
    generatedEl.title = `Collected ${absTime(manifest.generated, site.timezone)}; page checked ${absTime(new Date(lastCheck).toISOString(), site.timezone)}`;
  }
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(refresh, Math.max(1, Number(site.refresh_minutes) || 5) * 60e3);
}

// Hidden option: add ?export to the URL to show this browser's layout as text (pins, order, tab choices).
let wantExport = new URLSearchParams(location.search).has("export");

document.getElementById("refresh").addEventListener("click", refresh);
document.getElementById("layout").addEventListener("click", () => setEditing(!document.body.classList.contains("editing")));
document.getElementById("layout-done").addEventListener("click", () => setEditing(false));
document.getElementById("layout-reset").addEventListener("click", () => {
  resetOrder(currentSection());
  applySections();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.body.classList.contains("editing") && !document.getElementById("export")?.open) {
    setEditing(false);
  }
});
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
window.addEventListener("hashchange", () => { applySections(); window.scrollTo(0, 0); });
setInterval(tick, 30e3);
window.__dashboard = { renderErrors, refresh, applySections, showExport, setEditing };
refresh();
