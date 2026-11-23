# Dashboard

A single-page personal dashboard. It's hosted free on GitHub Pages, and GitHub Actions collects the data on a schedule.

```
GitHub Actions (every 30 min)                       GitHub Pages
  config/widgets.yaml ─► collector/ (Python) ─► data/ ─► site/ (static HTML/JS) ─► your domain
                                                 │
                                    "data" branch (history CSVs + latest JSON)
```

- **Widgets come from config**: `config/widgets.yaml` defines everything that's shown. Add, remove, reorder or retune widgets there.
- **Each widget has its own schedule** (`refresh: 30m | 3h | 1d`). The workflow wakes every 30 minutes and runs only the widgets that are due.
- **Failures stay contained.** If a source fails, its card keeps the last good data and shows a warning, and the other widgets carry on.
- **History** (gas prices) builds up in `data/history/*.csv` on the `data` branch. The branch is kept as a single commit, so the repo stays small. The CSVs are also served at `https://<your-site>/data/history/<widget-id>.csv` if you want a copy.
- **An open tab checks for new data** every `site.refresh_minutes` (5 by default), and again whenever the tab becomes visible.

## Widgets included

| id | type | source |
|---|---|---|
| gas-aaa | `aaa_gas` | gasprices.aaa.com (U.S., Texas, Austin), recorded daily, plus EIA weekly prices for six months of history from day one |
| costco-gas | `costco_gas` | costco.com warehouse locator: the 5 warehouses nearest downtown Austin, recorded daily |
| intc-stock | `stock` | Yahoo Finance chart data: price, daily change, 6-month closes |
| markets | `stock` (table) | S&P 500, Dow, Nasdaq; Asia (KLCI, Shanghai, Nikkei 225, KOSPI, TAIEX, STI); VOO, VGT: price, day / 1-month / 6-month change, trend line |
| sectors | `stock` (table) | The 11 SPDR sector ETFs (XLK, XLF, ...), sorted by today's change. Trend lines are red when the 6-month change is negative |
| world-economy | `macro` | GDP growth, inflation, unemployment, policy rate, 10Y yield for U.S., euro area, Germany, U.K., Japan, China, India, Korea, Malaysia (OECD, BIS, data.gov.my) |
| us-yield-curve | `treasury_curve` | U.S. Treasury yield curve vs 1 month / 1 year ago; 10Y−2Y and 10Y−3M spreads |
| intel-news | `rss` | Yahoo Finance INTC headlines + Google News |
| headlines-world | `rss` | CNN, WSJ, Reuters (via Google News), Fox News, NYT, BBC |
| headlines-malaysia | `rss` | Sin Chew 星洲日报 (Chinese headlines via Google News; its own site blocks bots), NST, The Star (via Google News), Malaysiakini EN + 中文 |
| ai-news | `rss` | Google News + TechCrunch AI + The Verge AI, U.S. publishers only (`source_include`) |
| isu-sports | `espn_team` | Iowa State football, men's and women's basketball: final and quarter/half scores, live games (ESPN) |
| fpga-venues | `crossref` | Newest TRETS articles and FPL / FPT proceedings papers (Crossref) |
| fpga-arxiv | `arxiv` | Newest FPGA/ASIC preprints on arXiv |
| fpga-news | `rss` | Google News for FPGA / ASIC chip news (filters out Australia's ASIC regulator) |

## Adding or changing widgets

Edit `config/widgets.yaml` and push. The workflow runs on push; new or changed widgets are collected right away.

Most new widgets are just another `rss` entry. Any RSS/Atom feed works, including a Google News search:
`https://news.google.com/rss/search?q=YOUR+QUERY+when:7d&hl=en-US&gl=US&ceid=US:en`

```yaml
  - id: nvda-stock
    type: stock
    title: NVIDIA
    refresh: 1h
    options: { tickers: [NVDA], range: 1y }
```

Options for each type are documented at the top of its file in `collector/sources/`. RSS filters are `include` (any must match), `require` (all must match), `exclude` and `latin_only`. They can be set per feed, per group or per widget.

**A new kind of widget** takes two files: a collector in `collector/sources/` (a function decorated with `@source("type", view="...")`, imported in `sources/__init__.py`) and, if it needs a new look, a renderer in `site/js/renderers/` registered in `renderers/index.js`. A test checks that every collector view has a renderer.

YAML gotcha: unquoted `on`, `off`, `yes` and `no` become true/false. Quote them.

## One-time setup

1. **Create a public GitHub repo** (for example `dashboard`), empty, with no README. Public is required for free Pages and Actions minutes.
2. **Push this folder** to it (`main` branch).
3. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
4. **Actions tab → "Collect & deploy" → Run workflow** (tick *force*). The first run creates the `data` branch and deploys the site at `https://<user>.github.io/<repo>/`.
5. **Custom domain**
   - At your DNS provider, a subdomain is recommended. Add a `CNAME` record: `dash` → `<user>.github.io`.
   - For the bare domain instead: `A` records for `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153` (optionally `AAAA` records for `2606:50c0:8000::153`, `…8001::153`, `…8002::153`, `…8003::153`).
   - In GitHub, go to Settings → Pages → Custom domain, enter the domain, save, then tick **Enforce HTTPS** once the certificate is issued (minutes to about an hour).
   - Recommended: verify the domain under your *account* Settings → Pages, so nobody else can claim it.
6. **Optional: EIA API key.** The gas chart's weekly history uses EIA's shared `DEMO_KEY`, which is rate-limited. A free personal key takes a minute at <https://www.eia.gov/opendata/register.php>. Add it as a repo secret named `EIA_API_KEY` (Settings → Secrets and variables → Actions → New repository secret).

## Running locally

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q tests                                  # offline tests
.venv\Scripts\python -m collector.run --data-dir _local/data --force     # live collection
# preview: copy site/ and the data folder together, then serve
robocopy site _local\_site /E; robocopy _local\data _local\_site\data /E
.venv\Scripts\python -m http.server 8000 -d _local/_site                 # http://localhost:8000
```

`python -m collector.run --check` validates the config. `--only id1,id2` runs specific widgets.

## Things to know

- **Scraped sources can break.** AAA and Costco are read from their public pages and endpoints, so a site redesign or a bot check can break a widget. The card shows the error and keeps the last good data. Costco is the most likely to block GitHub's servers.
- GitHub's cron is best-effort: runs can start a few minutes late, and are occasionally skipped.
- GitHub turns off scheduled workflows in public repos after 60 days without repository activity, and emails you first. If that happens, click *Enable workflow* on the Actions tab.
- Everything on the page and in `data/` is public.
