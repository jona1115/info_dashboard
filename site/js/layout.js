// Widget order per page tab, saved in this browser, and the layout export (?export in the URL).
//
// Default order: config order (for Home: site.home_order first, then other pinned widgets in config
// order). A browser's own order is kept in localStorage per section; widgets it doesn't list (new in
// the config, or newly pinned) go after every widget that comes before them by default.
import { HOME, isPinned } from "./sections.js";

const ORDER_KEY = "dash:order";

function readAll() {
  try {
    const v = JSON.parse(localStorage.getItem(ORDER_KEY) || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch (e) {
    return {};
  }
}

function writeAll(all) {
  try {
    if (Object.keys(all).length) localStorage.setItem(ORDER_KEY, JSON.stringify(all));
    else localStorage.removeItem(ORDER_KEY);
  } catch (e) { /* private mode */ }
}

/**
 * Order the ids in `base` by `preferred`: ids listed in preferred keep that order; each other id is
 * placed after all the ids that come before it in base (or first, if none does).
 */
export function arrange(base, preferred) {
  const inBase = new Set(base);
  const out = [];
  for (const id of preferred || []) if (inBase.has(id) && !out.includes(id)) out.push(id);
  base.forEach((id, i) => {
    if (out.includes(id)) return;
    let at = 0;
    for (let j = 0; j < i; j++) at = Math.max(at, out.indexOf(base[j]) + 1);
    out.splice(at, 0, id);
  });
  return out;
}

/** Default order of a section's widget ids (given in config order). */
export function defaultOrder(section, ids, site) {
  if (section !== HOME) return ids.slice();
  const listed = ((site && site.home_order) || []).filter((id, i, a) => ids.includes(id) && a.indexOf(id) === i);
  return [...listed, ...ids.filter((id) => !listed.includes(id))];
}

/** This browser's order of a section's widget ids (given in config order). */
export function sectionOrder(section, ids, site) {
  return arrange(defaultOrder(section, ids, site), readAll()[section]);
}

/** Remember an order; an order equal to the default is forgotten. */
export function saveOrder(section, ordered, ids, site) {
  const all = readAll();
  const def = defaultOrder(section, ids, site);
  if (ordered.join() === def.join()) delete all[section];
  else all[section] = ordered.slice();
  writeAll(all);
}

export function hasCustomOrder(section) {
  return section ? Boolean(readAll()[section]) : Object.keys(readAll()).length > 0;
}

export function resetOrder(section) {
  const all = readAll();
  if (section) delete all[section];
  else for (const k of Object.keys(all)) delete all[k];
  writeAll(all);
}

/** Everything that makes up this browser's layout, in a form that can be pasted back into the config. */
export function exportLayout(manifest, site) {
  const sections = (site.sections || []).filter((s) => s && s.id);
  const widgets = manifest.widgets || [];
  const ids = (pred) => widgets.filter(pred).map((w) => w.id);
  const out = {
    dashboard_layout: 1,
    exported: new Date().toISOString(),
    home: sectionOrder(HOME, ids(isPinned), site),
    sections: {},
    local_changes: { pins: {}, order: readAll() },
    widget_ui: {},
  };
  for (const s of sections) if (s.id !== HOME) out.sections[s.id] = sectionOrder(s.id, ids((w) => w.section === s.id), site);
  try { out.local_changes.pins = JSON.parse(localStorage.getItem("dash:pins") || "{}"); } catch (e) { /* ignore */ }
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith("dash:ui:")) out.widget_ui[k.slice(8)] = JSON.parse(localStorage.getItem(k));
    }
  } catch (e) { /* ignore */ }
  return out;
}
