# Trading Dashboard — "Elvis for Trading"

A Bloomberg-terminal-styled trading dashboard: stage analysis (Stan
Weinstein's 4-stage model), IBD-style RS Ratings, sector strength
rankings, 13F institutional filings, and weekly OHLC charts — all in one
dark, dense, keyboard-friendly screen.

Prices come from **Yahoo Finance** and 13F holdings from **SEC EDGAR**
(both free, no API key) whenever the machine running it has outbound
internet access. Anything that can't be fetched — a ticker, an
institution, or everything if you're fully offline — falls back to a
built-in market simulation, per-ticker/per-institution, so the dashboard
never breaks. A badge in the top bar always tells you which mode you're
actually looking at (`LIVE DATA` / `SIMULERET DATA` / `BLANDET`), and a
small dot next to each ticker/13F row shows its own source.

> **Built and tested in a sandbox with zero outbound network access**
> (not even to `example.com`). The live-fetch code against Yahoo Finance
> and SEC EDGAR is written to their documented, stable public API shapes
> and covered by offline unit tests against realistic fixture payloads
> (`tests/test_providers.py`), but the actual HTTP round-trips have not
> been exercised against the real internet from this environment. The
> automatic per-item fallback means the dashboard is fully usable either
> way — **please do a quick local smoke test** (run it with internet
> access and check the top-bar badge turns green) after pulling this.
>
> The same applies to `Elvis.app`: this sandbox is Linux, so the
> `.app` bundle and its launcher script were built and verified here by
> running the actual launcher logic and `server.py`'s SIGTERM handling
> directly (confirmed: it starts the server, waits for it to respond,
> would call macOS's `open` on the right URL, and shuts down cleanly on
> a terminate signal) — but `open`, `osascript`, Gatekeeper, and the Dock
> itself are macOS-only and have not been exercised on a real Mac. It
> should just work per the steps below; if double-clicking it does
> nothing or the browser never opens, check `~/Library/Logs/Elvis.log`
> — if that's empty or missing, the app isn't finding `python3`.

## Run it

### macOS: as a regular app

`Elvis.app` in this folder is a self-contained double-clickable
app — no terminal needed day-to-day.

1. Pull/download this branch, then in Finder go into `Trading-Dashboard/`.
2. **First launch only:** macOS blocks unsigned apps by default. Either
   right-click `Elvis.app` → **Open** → **Open** in the dialog, or
   if it says the app "cannot be opened", go to **System Settings →
   Privacy & Security**, scroll down, and click **Open Anyway** next to
   the Elvis entry, then launch it again.
3. Double-click it. Your browser opens to the dashboard automatically —
   it needs a moment the very first time.
4. It behaves like any other app after that: it sits in the Dock while
   running, and **Cmd+Q** (or Dock icon → Quit) shuts the server down
   cleanly. You can drag `Elvis.app` anywhere (Desktop,
   Applications) — it's fully self-contained.

Requires Python 3 to already be on the Mac (macOS normally has it, or
installs it on first `python3` use via the Xcode Command Line Tools
prompt; otherwise get it from python.org). If it's missing, the app shows
an alert instead of silently failing.

If you change the dashboard's source files, regenerate the app with
`./build_mac_app.sh` — it always rebuilds `Elvis.app` from the
current `server.py` / `data_engine.py` / `providers.py` / `ai_analyst.py`
/ `static/`, which stay the actual source of truth (nothing inside the
`.app` should be hand-edited).

### Any OS: from the terminal

No third-party packages required to run the dashboard — pure Python 3
standard library on the backend, vanilla HTML/CSS/JS on the frontend.

```bash
cd Trading-Dashboard
python3 server.py
# -> http://localhost:8000
```

Optional, only for Claude-powered AI analysis instead of the free
rule-based one (see **AI Weinstein analysis** below):

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

Run the offline test suite any time with:

```bash
python3 -m unittest discover -s tests -v
```

## Data modes

Controlled by the `DATA_MODE` environment variable:

- `auto` (default) — tries live data for every ticker and institution,
  falling back to simulation only for the ones that fail (no network, a
  ticker Yahoo doesn't recognize, a filer that couldn't be matched, …).
  A single fast connectivity probe (~6s timeout) is done once per
  provider up front, so being fully offline costs one short delay, not
  one per ticker.
- `simulated` — skip all network calls entirely (fast, fully
  deterministic, good for demos/CI/this sandbox).

```bash
DATA_MODE=simulated python3 server.py
```

Live responses are cached to disk under `.cache/` (prices: 12h,
resolved institution CIKs: 30 days) so restarts don't re-hit the network
unnecessarily. Click **⟳ OPDATER** in the top bar to force a live refresh
(add `?force=1` semantics automatically — it also wipes `.cache/` and
re-probes connectivity, in case you started offline and got online since).

## What it does

- **Stage Analysis** — every stock is classified into Weinstein Stage 1
  (Basing), 2 (Advancing), 3 (Topping) or 4 (Declining), based on the
  weekly close vs. its 30-week moving average and that average's slope.
  Click a stage card to filter the screener and see which sectors have
  the most names in that stage.
- **RS Rating** — an IBD-style 1–99 Relative Strength rating. A raw score
  is built from weighted trailing returns (40% 3-month, 20% each of
  6/9/12-month), then every stock is percentile-ranked against the whole
  universe. Highest RS sits on top everywhere in the UI, exactly like IBD.
- **Sector Strength** — sectors are ranked by their members' average RS
  Rating, with a day-over-day delta arrow so you can see who's
  strengthening or fading. Click a sector to drill into its top names.
- **13F Filings** — institutional-ownership table (10 well-known funds)
  across the last 3 completed quarters, with New/Increased/Decreased/Sold
  Out tags, sourced from SEC EDGAR's Form 13F-HR filings when reachable.
  Click any row to jump straight to that stock's chart.
- **Weekly OHLC chart** — click any ticker to render a free, embedded
  **TradingView Advanced Chart** widget (weekly candles, 30-period MA
  study) for that symbol. If the widget can't load — offline, blocked by
  an ad-blocker, `s3.tradingview.com` unreachable — the dashboard falls
  back automatically to its own dependency-free `<canvas>` candlestick
  chart built from the same OHLCV data, so the panel is never empty.
- **AI assistant ticker** — a scrolling insight strip (top/bottom sector,
  Stage 2 leader, best Stage 1 base, notable 13F increase) generated by
  simple template rules in `Market.insights()`.
- **AI Weinstein analysis** — click any ticker and, below its chart, get a
  written technical assessment: which Weinstein stage it's in and why, the
  key level to watch, and what would confirm or invalidate the read. Uses
  Claude when available, otherwise a free rule-based analyzer using the
  same numbers — see **AI Weinstein analysis** below.

## Architecture

```
Trading-Dashboard/
  providers.py     # Yahoo Finance + SEC EDGAR clients (stdlib urllib only),
                    # fail-soft: every method returns None on any error
  data_engine.py   # Market: live-with-fallback OHLCV, RS rating, stage,
                    # sector aggregation, 13F, per-item source tracking
  ai_analyst.py    # per-ticker Weinstein assessment: Claude when available,
                    # else a free rule-based analyzer on the same facts
  server.py        # stdlib http.server backend, serves /api/* and static/
  static/
    index.html
    css/terminal.css
    js/app.js      # fetches the API, renders grids/panels, draws the chart
  tests/           # offline unit tests (fixtures, no network)
  .cache/          # disk cache for live fetches (gitignored)
```

`Market` (in `data_engine.py`) builds one snapshot per ticker: try Yahoo
Finance first (unless `DATA_MODE=simulated`), and only generate a
synthetic series for that specific ticker if the live fetch fails. 13F
works the same way per institution. All analytics (RS rating percentile
ranking, stage classification, sector aggregation, `_finalize_holdings`)
run identically regardless of whether the underlying data is live or
simulated — they only ever see plain OHLCV bars / holdings rows.

### API

| Endpoint | Returns |
|---|---|
| `GET /api/meta` | as-of date, universe size, disclaimer, live/simulated counts |
| `GET /api/universe` | all stocks with price, RS rating, stage, sector, `data_source` |
| `GET /api/sectors` | sector strength ranking + stage counts + top names |
| `GET /api/ohlc/<ticker>` | weekly OHLCV bars + 30W MA for the chart |
| `GET /api/13f` | full 13F holdings table (last 3 quarters) |
| `GET /api/13f/<ticker>` | holdings history for one ticker |
| `GET /api/insights` | templated "Elvis" commentary strings |
| `GET /api/analysis/<ticker>` | AI Weinstein assessment for one ticker (`source`: `llm` or `rule_based`) |
| `GET /api/refresh` | re-probes connectivity and rebuilds the snapshot; `?force=1` also wipes `.cache/` |

## AI Weinstein analysis

Click any ticker and the panel below its chart calls `ai_analyst.analyze()`
with the same computed facts used everywhere else in the dashboard (stage,
RS Rating and its 5-day trend, 30-week MA and its 10-week slope, 12/52-week
high-low range, recent 13F activity for that name) and returns a short,
specific write-up: which stage the stock is in and why, the level to watch,
and what would confirm or invalidate that read.

- **With Claude** (`pip install anthropic` + a credential — `ANTHROPIC_API_KEY`,
  or `ant auth login`): calls `claude-opus-5` with those facts and a system
  prompt that keeps it grounded in the numbers, in Danish, non-prescriptive
  (technical analysis, never a buy/sell call). **This costs a small amount
  per call** — results are cached per ticker (keyed on ticker + stage + RS
  Rating + price, so a real change invalidates it) for 6 hours, so re-clicking
  the same name doesn't re-spend. There's no separate on/off switch: just
  don't install `anthropic` / don't set a credential, and the free rule-based
  path runs automatically — the badge on the panel always says which one
  produced the analysis, and `/api/meta` -> `ai_analysis` reports whether the
  package is installed.
- **Without Claude** (the default here, and whenever the LLM call fails for
  any reason — no key, no network, rate-limited, refused): `rule_based_analysis()`
  in `ai_analyst.py` writes the same three-part assessment from the same
  facts with per-stage templates — free, instant, no dependency. The panel's
  badge reads "REGELBASERET" in this mode.
- Every failure mode of the LLM path (missing package, missing/invalid
  credential, network error, rate limit, non-2xx response, a refusal) is
  caught individually and degrades to the rule-based path rather than
  breaking the panel — verified with `tests/test_ai_analyst.py` against the
  real `anthropic` SDK's exception classes (installed in dev, not a hard
  runtime dependency), since this sandbox has no configured API key to
  exercise an actual live call end-to-end.
- Not financial advice — the system prompt explicitly tells the model not
  to recommend buying or selling, and the panel says so under every
  analysis.

## Chart widget: TradingView, not Yahoo

The chart panel embeds TradingView's free **Advanced Real-Time Chart**
widget (`s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js`)
for whichever ticker is selected, weekly interval, with a 30-period moving
average study to match the same 30-week MA used for stage classification.
This is purely a display choice — it does **not** change where prices for
RS Rating / stage analysis come from (that's still Yahoo Finance, see
below). TradingView doesn't offer a free public data API you can pull raw
OHLCV out of for your own calculations (their Charting Library expects
*you* to supply the data); their supported free path is exactly this kind
of embeddable widget, which is what's used here.

- `data_engine.TICKER_EXCHANGE` maps each ticker to its primary exchange
  (e.g. `NASDAQ:AAPL`) so the widget resolves the right instrument. It's a
  best-effort hand-maintained map for this 55-ticker universe — if a
  chart ever shows the wrong instrument, fix its entry there.
- If the widget script fails to load or never renders an iframe within 4
  seconds (offline, corporate firewall, an ad-blocker filter list that
  flags TradingView's embed domain — a real possibility, not hypothetical),
  the frontend automatically falls back to its own `<canvas>` candlestick
  chart built from the `/api/ohlc/<ticker>` data already being fetched
  anyway. A small note appears under the chart when this happens.
- Confirmed in this sandbox (which has zero outbound network access at
  all): the widget fails to load and the fallback engages correctly and
  instantly. The actual TradingView widget rendering has **not** been
  seen working from here — verify it locally with internet access.

## How the live providers work

**`YahooFinanceProvider`** (`providers.py`) hits Yahoo's public chart
endpoint (`query1.finance.yahoo.com/v8/finance/chart/<ticker>`), parses
daily OHLCV out of the JSON, and requires at least ~260 daily bars
(enough for the 12-month RS lookback and 30-week MA) or it returns `None`.

**`SecEdgarProvider`** does the full real pipeline: resolve an
institution's CIK via EDGAR's company-search atom feed (`www.sec.gov/
cgi-bin/browse-edgar?action=getcompany&type=13F-HR`), list its recent
`13F-HR` filings via `data.sec.gov/submissions/CIK{cik}.json`, fetch each
filing's information-table XML, parse `nameOfIssuer` / `cusip` / `value` /
`sshPrnamt` per holding, and fuzzy-match the issuer name (stripping
`INC`/`CORP`/`CL A`/etc.) against our ticker universe's company names —
13F filings report CUSIPs and issuer names, not ticker symbols, and there
is no free CUSIP↔ticker mapping service, so name-matching is the
practical approach for a small, fixed universe like this one. A
descriptive `User-Agent` is sent on every SEC request per their
fair-access policy, with a small delay between requests.

Both providers do one cheap "is this reachable" probe before doing any
real work (`is_available()`), so a fully offline run fails fast instead of
timing out once per ticker/institution — confirmed here: a full offline
`Market` build (55 tickers + 10 institutions) completes in ~1 second.

### Extending it

- **More tickers/sectors**: add to `SECTORS` in `data_engine.py` — no
  other code changes needed, Yahoo Finance covers essentially any listed
  ticker.
- **More institutions**: add the fund's registered name to `INSTITUTIONS`;
  `SecEdgarProvider.resolve_cik` looks it up automatically.
- **A different price provider**: implement the same
  `fetch_daily(ticker) -> [{date, open, high, low, close, volume}] | None`
  contract and swap it in `Market._build()`.
