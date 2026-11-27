// View registry: a collector's "view" name -> renderer. Add a new view by adding a file and a line here.
import { render as list } from "./list.js";
import { render as gas } from "./gas.js";
import { render as stock } from "./stock.js";
import { render as costco } from "./costco.js";
import { render as macro } from "./macro.js";
import { render as yield_curve } from "./yield_curve.js";
import { render as scores } from "./scores.js";
import { render as candles } from "./candles.js";
import { render as calendar } from "./calendar.js";
import { render as summary } from "./summary.js";
import { render as sentiment } from "./sentiment.js";
import { el } from "../util.js";

function raw(body, data) {
  body.append(el("pre", { class: "raw" }, JSON.stringify(data, null, 1)));
}

export const RENDERERS = { list, gas, stock, costco, macro, yield_curve, scores, candles, calendar, summary, sentiment, raw };
