// Page sections (tabs at the top) and pinning widgets to Home.
// Sections come from config (site.sections); the first one, "home", shows pinned widgets.
// Default pins come from config (pinned: true); each browser's changes are kept in localStorage.

export const HOME = "home";
const PINS_KEY = "dash:pins";

function readPins() {
  try {
    const v = JSON.parse(localStorage.getItem(PINS_KEY) || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch (e) {
    return {};
  }
}

/** Is this widget pinned in this browser? (local choice wins over the config default) */
export function isPinned(entry) {
  const pins = readPins();
  return Object.prototype.hasOwnProperty.call(pins, entry.id) ? pins[entry.id] === true : entry.pinned === true;
}

export function setPinned(entry, on) {
  const pins = readPins();
  if (on === (entry.pinned === true)) delete pins[entry.id]; // back to the default: forget the override
  else pins[entry.id] = on;
  try { localStorage.setItem(PINS_KEY, JSON.stringify(pins)); } catch (e) { /* private mode */ }
}

/** Section id from the URL hash (#news), falling back to Home or the first section. */
export function sectionFromHash(sections, hash = location.hash) {
  const id = decodeURIComponent((hash || "").replace(/^#/, ""));
  return sections.some((s) => s.id === id) ? id : (sections[0] ? sections[0].id : HOME);
}

/** Should this widget's card be visible in the given section? */
export function visibleIn(entry, section, sections) {
  if (!sections || !sections.length) return true; // no sections configured: one long page
  if (section === HOME) return isPinned(entry);
  return entry.section === section;
}
