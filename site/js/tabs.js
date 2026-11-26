// Reusable UI pieces for interactive widgets: a tab bar, a segmented button group,
// and per-widget UI state that survives data refreshes and page reloads.
import { el } from "./util.js";

const memory = new Map();

/** Remembered UI choices for one widget (e.g. selected tab / period). */
export function uiState(widgetId) {
  const key = `dash:ui:${widgetId}`;
  if (!memory.has(key)) {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(key) || "{}") || {}; } catch (e) { saved = {}; }
    memory.set(key, saved);
  }
  const state = memory.get(key);
  return {
    get: (name, fallback) => (state[name] !== undefined ? state[name] : fallback),
    set: (name, value) => {
      state[name] = value;
      try { localStorage.setItem(key, JSON.stringify(state)); } catch (e) { /* private mode */ }
    },
  };
}

/**
 * A row of buttons where exactly one is active.
 * items: [{key, label, title?}], kind: "tabs" (big, underlined) | "seg" (small segmented)
 */
export function buttonGroup(items, selected, onSelect, { kind = "seg", label = "" } = {}) {
  const group = el("div", { class: kind === "tabs" ? "tabbar" : "seg", role: kind === "tabs" ? "tablist" : "group",
    "aria-label": label || null });
  const buttons = items.map((it) => {
    const b = el("button", {
      type: "button",
      class: it.key === selected ? "active" : "",
      role: kind === "tabs" ? "tab" : null,
      "aria-selected": kind === "tabs" ? String(it.key === selected) : null,
      "aria-pressed": kind === "tabs" ? null : String(it.key === selected),
      "data-key": it.key,
      title: it.title || null,
    }, it.label);
    b.addEventListener("click", () => {
      buttons.forEach((x) => {
        const on = x === b;
        x.classList.toggle("active", on);
        if (kind === "tabs") x.setAttribute("aria-selected", String(on));
        else x.setAttribute("aria-pressed", String(on));
      });
      onSelect(it.key);
    });
    return b;
  });
  group.append(...buttons);
  return group;
}
