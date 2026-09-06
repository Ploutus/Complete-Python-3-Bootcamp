"""
Synthetic market data engine for the Trading Dashboard demo.

IMPORTANT: All prices, RS ratings, stage classifications and 13F holdings
produced here are SIMULATED. No live market data or real SEC filings are
fetched (this environment has no outbound access to Yahoo Finance / SEC
EDGAR). The module is written so the simulation can be swapped for a real
provider later -- see `README.md` -> "Plugging in real data".

Analytics implemented (deliberately simple, documented formulas):

  * RS Rating: IBD-style relative strength rating. A raw score is built
    from weighted trailing returns (3/6/9/12 months), then every stock in
    the universe is percentile-ranked against that raw score into a 1-99
    scale (99 = strongest), exactly like IBD's RS Rating.

  * Stage Analysis: Stan Weinstein's 4-stage model, approximated from the
    weekly close vs. its 30-week moving average and the MA's slope:
        Stage 2 (Advancing)   -> price above MA30W, MA rising
        Stage 3 (Topping)     -> price above MA30W, MA flattening/rolling
        Stage 4 (Declining)   -> price below MA30W, MA falling
        Stage 1 (Basing)      -> price below/around MA30W, MA flattening
"""
import hashlib
import math
import random
import statistics
from datetime import datetime, timedelta

TRADING_DAYS = 760  # ~3 years of business days
QUARTER_LABELS_BACK = 3

SECTORS = {
    "Technology": [
        ("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corp."),
        ("NVDA", "NVIDIA Corp."), ("AVGO", "Broadcom Inc."),
        ("ORCL", "Oracle Corp."),
    ],
    "Communication Services": [
        ("GOOGL", "Alphabet Inc."), ("META", "Meta Platforms"),
        ("NFLX", "Netflix Inc."), ("DIS", "Walt Disney Co."),
        ("TMUS", "T-Mobile US"),
    ],
    "Consumer Discretionary": [
        ("AMZN", "Amazon.com"), ("TSLA", "Tesla Inc."),
        ("HD", "Home Depot"), ("MCD", "McDonald's Corp."),
        ("NKE", "Nike Inc."),
    ],
    "Consumer Staples": [
        ("PG", "Procter & Gamble"), ("KO", "Coca-Cola Co."),
        ("PEP", "PepsiCo Inc."), ("WMT", "Walmart Inc."),
        ("COST", "Costco Wholesale"),
    ],
    "Financials": [
        ("JPM", "JPMorgan Chase"), ("BAC", "Bank of America"),
        ("GS", "Goldman Sachs"), ("MS", "Morgan Stanley"),
        ("V", "Visa Inc."),
    ],
    "Healthcare": [
        ("UNH", "UnitedHealth Group"), ("JNJ", "Johnson & Johnson"),
        ("LLY", "Eli Lilly"), ("PFE", "Pfizer Inc."),
        ("ABBV", "AbbVie Inc."),
    ],
    "Industrials": [
        ("CAT", "Caterpillar Inc."), ("BA", "Boeing Co."),
        ("HON", "Honeywell Intl."), ("UPS", "United Parcel Service"),
        ("GE", "GE Aerospace"),
    ],
    "Energy": [
        ("XOM", "Exxon Mobil"), ("CVX", "Chevron Corp."),
        ("COP", "ConocoPhillips"), ("SLB", "Schlumberger"),
        ("OXY", "Occidental Petroleum"),
    ],
    "Materials": [
        ("LIN", "Linde plc"), ("APD", "Air Products"),
        ("ECL", "Ecolab Inc."), ("NEM", "Newmont Corp."),
        ("FCX", "Freeport-McMoRan"),
    ],
    "Utilities": [
        ("NEE", "NextEra Energy"), ("DUK", "Duke Energy"),
        ("SO", "Southern Co."), ("D", "Dominion Energy"),
        ("AEP", "American Electric Power"),
    ],
    "Real Estate": [
        ("PLD", "Prologis Inc."), ("AMT", "American Tower"),
        ("EQIX", "Equinix Inc."), ("SPG", "Simon Property Group"),
        ("O", "Realty Income"),
    ],
}

INSTITUTIONS = [
    "Berkshire Hathaway Inc", "Vanguard Group Inc", "BlackRock Inc",
    "Renaissance Technologies LLC", "Citadel Advisors LLC",
    "State Street Corp", "Bridgewater Associates LP",
    "Point72 Asset Management", "Tiger Global Management",
    "Fidelity (FMR LLC)",
]

STAGE_LABELS = {
    1: "Stage 1 · Basing",
    2: "Stage 2 · Advancing",
    3: "Stage 3 · Topping",
    4: "Stage 4 · Declining",
}


def _seed_for(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest(), 16) % (2 ** 32)


def _trading_dates(n_days: int, end_date: datetime):
    dates = []
    d = end_date
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()
    return dates


def _sector_factor(n_days: int, seed: int):
    """A shared daily-return series representing sector-wide moves."""
    rng = random.Random(seed)
    regimes = []
    remaining = n_days
    while remaining > 0:
        length = min(rng.randint(50, 140), remaining)
        kind = rng.choices(["up", "down", "flat"], weights=[0.42, 0.30, 0.28])[0]
        drift = {
            "up": rng.uniform(0.0006, 0.0017),
            "down": rng.uniform(-0.0017, -0.0005),
            "flat": rng.uniform(-0.00025, 0.00025),
        }[kind]
        vol = rng.uniform(0.007, 0.016)
        regimes.append((length, drift, vol))
        remaining -= length

    returns = []
    for length, drift, vol in regimes:
        for _ in range(length):
            returns.append(rng.gauss(drift, vol))
    return returns[:n_days]


def _stock_series(ticker: str, base_price: float, sector_returns, beta: float):
    rng = random.Random(_seed_for(ticker))
    idio_regimes = []
    remaining = len(sector_returns)
    while remaining > 0:
        length = min(rng.randint(40, 130), remaining)
        kind = rng.choices(["up", "down", "flat"], weights=[0.38, 0.30, 0.32])[0]
        drift = {
            "up": rng.uniform(0.0004, 0.0014),
            "down": rng.uniform(-0.0015, -0.0004),
            "flat": rng.uniform(-0.0002, 0.0002),
        }[kind]
        vol = rng.uniform(0.010, 0.024)
        idio_regimes.append((length, drift, vol))
        remaining -= length

    # flatten idio regimes into a per-day list aligned with sector_returns
    idio_per_day = []
    for length, drift, vol in idio_regimes:
        for _ in range(length):
            idio_per_day.append((drift, vol))
    idio_per_day = idio_per_day[:len(sector_returns)]

    price = base_price
    bars = []
    prev_close = price
    for i, sret in enumerate(sector_returns):
        drift, vol = idio_per_day[i]
        day_ret = beta * sret * 0.6 + drift + rng.gauss(0, vol)
        price = max(1.0, prev_close * (1 + day_ret))
        intraday = abs(rng.gauss(0, vol * 0.7))
        high = price * (1 + intraday * rng.uniform(0.3, 1.0))
        low = price * (1 - intraday * rng.uniform(0.3, 1.0))
        open_px = prev_close * (1 + rng.gauss(0, vol * 0.3))
        high = max(high, open_px, price)
        low = min(low, open_px, price)
        volume = int(rng.uniform(2.0, 9.0) * 1_000_000 * (1 + abs(day_ret) * 12))
        bars.append({
            "open": round(open_px, 2), "high": round(high, 2),
            "low": round(low, 2), "close": round(price, 2),
            "volume": volume,
        })
        prev_close = price
    return bars


def _sma(values, window):
    out = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        if i >= window - 1:
            out[i] = running / window
    return out


def _weekly_bars(daily_bars, dates):
    """Resample daily OHLCV into Friday-ending weekly bars."""
    weeks = []
    cur = None
    cur_dates = []
    for bar, d in zip(daily_bars, dates):
        iso_year, iso_week, _ = d.isocalendar()
        key = (iso_year, iso_week)
        if cur is None or cur["key"] != key:
            if cur is not None:
                weeks.append(cur)
            cur = {
                "key": key, "date": d, "open": bar["open"],
                "high": bar["high"], "low": bar["low"],
                "close": bar["close"], "volume": bar["volume"],
            }
        else:
            cur["date"] = d
            cur["high"] = max(cur["high"], bar["high"])
            cur["low"] = min(cur["low"], bar["low"])
            cur["close"] = bar["close"]
            cur["volume"] += bar["volume"]
    if cur is not None:
        weeks.append(cur)
    for w in weeks:
        del w["key"]
    return weeks


def _raw_rs_score(closes, idx):
    """Weighted trailing-return score a la IBD (40/20/20/20 over 3/6/9/12mo)."""
    def ret(months_days):
        j = idx - months_days
        if j < 0:
            return None
        base = closes[j]
        if base <= 0:
            return None
        return (closes[idx] / base) - 1.0

    r3, r6, r9, r12 = ret(63), ret(126), ret(189), ret(252)
    if None in (r3, r6, r9, r12):
        return None
    return 0.40 * r3 + 0.20 * r6 + 0.20 * r9 + 0.20 * r12


def _percentile_ranks(scores: dict):
    """Map ticker->raw score into ticker->1..99 RS rating."""
    items = [(t, s) for t, s in scores.items() if s is not None]
    items.sort(key=lambda kv: kv[1])
    n = len(items)
    ranks = {}
    for i, (t, _) in enumerate(items):
        pct = i / max(1, n - 1)
        ranks[t] = max(1, min(99, round(1 + pct * 98)))
    for t, s in scores.items():
        ranks.setdefault(t, 50)
    return ranks


def _classify_stage(weekly_closes):
    ma = _sma(weekly_closes, 30)
    if len(weekly_closes) < 40 or ma[-1] is None or ma[-10] is None:
        return 1
    price = weekly_closes[-1]
    ma_now = ma[-1]
    ma_prev = ma[-10]
    slope = (ma_now - ma_prev) / ma_prev if ma_prev else 0.0
    above = price >= ma_now
    if above and slope > 0.01:
        return 2
    if above and slope <= 0.01:
        return 3
    if (not above) and slope < -0.01:
        return 4
    return 1


class Market:
    """Builds and caches one full synthetic universe snapshot."""

    def __init__(self, seed=1337, end_date=None):
        self.seed = seed
        self.end_date = end_date or datetime.utcnow()
        self.dates = _trading_dates(TRADING_DAYS, self.end_date)
        self._build()

    def _build(self):
        rng = random.Random(self.seed)
        self.daily = {}
        self.meta = {}
        for sector, stocks in SECTORS.items():
            sector_returns = _sector_factor(TRADING_DAYS, _seed_for(sector + str(self.seed)))
            for ticker, name in stocks:
                base_price = rng.uniform(35, 480)
                beta = rng.uniform(0.7, 1.4)
                bars = _stock_series(ticker, base_price, sector_returns, beta)
                self.daily[ticker] = bars
                self.meta[ticker] = {"name": name, "sector": sector, "beta": round(beta, 2)}

        self.closes = {t: [b["close"] for b in bars] for t, bars in self.daily.items()}
        self.weekly = {t: _weekly_bars(bars, self.dates) for t, bars in self.daily.items()}

        last_idx = TRADING_DAYS - 1
        raw_today = {t: _raw_rs_score(c, last_idx) for t, c in self.closes.items()}
        raw_prev = {t: _raw_rs_score(c, last_idx - 1) for t, c in self.closes.items()}
        raw_week_ago = {t: _raw_rs_score(c, last_idx - 5) for t, c in self.closes.items()}
        self.rs_today = _percentile_ranks(raw_today)
        self.rs_prev = _percentile_ranks(raw_prev)
        self.rs_week_ago = _percentile_ranks(raw_week_ago)

        self.stage = {t: _classify_stage([w["close"] for w in wk]) for t, wk in self.weekly.items()}

    def universe_rows(self):
        rows = []
        for t, meta in self.meta.items():
            bars = self.daily[t]
            last = bars[-1]
            prev = bars[-2]
            week_ago_close = self.closes[t][-6] if len(self.closes[t]) > 6 else prev["close"]
            day_chg = (last["close"] / prev["close"] - 1.0) * 100
            week_chg = (last["close"] / week_ago_close - 1.0) * 100
            stage = self.stage[t]
            rows.append({
                "ticker": t,
                "name": meta["name"],
                "sector": meta["sector"],
                "price": last["close"],
                "day_chg_pct": round(day_chg, 2),
                "week_chg_pct": round(week_chg, 2),
                "rs_rating": self.rs_today.get(t, 50),
                "rs_rating_prev": self.rs_prev.get(t, 50),
                "rs_rating_week_ago": self.rs_week_ago.get(t, 50),
                "stage": stage,
                "stage_label": STAGE_LABELS[stage],
                "volume": last["volume"],
            })
        rows.sort(key=lambda r: r["rs_rating"], reverse=True)
        return rows

    def ohlc_weekly(self, ticker):
        ticker = ticker.upper()
        if ticker not in self.weekly:
            return None
        weekly = self.weekly[ticker]
        closes = [w["close"] for w in weekly]
        ma = _sma(closes, 30)
        bars = []
        for w, m in zip(weekly, ma):
            bars.append({
                "date": w["date"].strftime("%Y-%m-%d"),
                "open": w["open"], "high": w["high"], "low": w["low"],
                "close": w["close"], "volume": w["volume"],
                "ma30w": round(m, 2) if m is not None else None,
            })
        meta = self.meta[ticker]
        return {
            "ticker": ticker, "name": meta["name"], "sector": meta["sector"],
            "stage": self.stage[ticker], "stage_label": STAGE_LABELS[self.stage[ticker]],
            "rs_rating": self.rs_today.get(ticker, 50),
            "bars": bars,
        }

    def thirteen_f(self):
        """Synthetic 13F institutional holdings across the last few quarters."""
        rng = random.Random(_seed_for("13f-" + str(self.seed)))
        today = self.end_date
        quarter_end = datetime(today.year, ((today.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
        quarters = []
        q = quarter_end
        for _ in range(QUARTER_LABELS_BACK):
            label = f"Q{((q.month - 1)//3)+1} {q.year}"
            quarters.append(label)
            first_of_q = datetime(q.year, ((q.month - 1)//3)*3 + 1, 1)
            q = first_of_q - timedelta(days=1)
        quarters.reverse()  # oldest -> newest

        tickers = list(self.meta.keys())
        holdings = []
        for inst in INSTITUTIONS:
            inst_rng = random.Random(_seed_for(inst + str(self.seed)))
            picks = inst_rng.sample(tickers, k=inst_rng.randint(12, 22))
            for t in picks:
                price = self.closes[t][-1]
                shares_prev = inst_rng.randint(200_000, 9_000_000)
                for qi, label in enumerate(quarters):
                    action_roll = inst_rng.random()
                    if qi == 0:
                        shares = shares_prev
                        action = "Held"
                        chg_pct = 0.0
                    else:
                        if action_roll < 0.12:
                            shares = 0
                            action = "Sold Out"
                        elif action_roll < 0.45:
                            shares = int(shares_prev * inst_rng.uniform(1.05, 1.6))
                            action = "Increased"
                        elif action_roll < 0.75:
                            shares = int(shares_prev * inst_rng.uniform(0.5, 0.95))
                            action = "Decreased"
                        else:
                            shares = shares_prev
                            action = "Held"
                        chg_pct = round(((shares - shares_prev) / shares_prev) * 100, 1) if shares_prev else 0.0
                    holdings.append({
                        "filer": inst, "ticker": t, "quarter": label,
                        "shares": shares, "value": round(shares * price, 0),
                        "change_pct": chg_pct, "action": action,
                    })
                    shares_prev = shares if shares > 0 else inst_rng.randint(200_000, 9_000_000)
        latest_q = quarters[-1]
        latest = [h for h in holdings if h["quarter"] == latest_q]
        latest.sort(key=lambda h: h["value"], reverse=True)
        return {"quarters": quarters, "latest_quarter": latest_q, "holdings": holdings, "latest": latest}

    def thirteen_f_for_ticker(self, ticker):
        data = self.thirteen_f()
        ticker = ticker.upper()
        quarter_order = {q: i for i, q in enumerate(data["quarters"])}
        rows = [h for h in data["holdings"] if h["ticker"] == ticker]
        rows.sort(key=lambda h: (quarter_order[h["quarter"]], -h["value"]))
        return rows

    def sector_summary(self):
        rows = self.universe_rows()
        by_sector = {}
        for r in rows:
            by_sector.setdefault(r["sector"], []).append(r)
        out = []
        for sector, stocks in by_sector.items():
            avg_rs = statistics.mean(s["rs_rating"] for s in stocks)
            avg_rs_prev = statistics.mean(s["rs_rating_prev"] for s in stocks)
            avg_day_chg = statistics.mean(s["day_chg_pct"] for s in stocks)
            stage_counts = {1: 0, 2: 0, 3: 0, 4: 0}
            for s in stocks:
                stage_counts[s["stage"]] += 1
            top_stocks = sorted(stocks, key=lambda s: s["rs_rating"], reverse=True)[:5]
            out.append({
                "sector": sector,
                "avg_rs": round(avg_rs, 1),
                "avg_rs_change": round(avg_rs - avg_rs_prev, 1),
                "avg_day_chg_pct": round(avg_day_chg, 2),
                "stage_counts": stage_counts,
                "count": len(stocks),
                "top_stocks": [s["ticker"] for s in top_stocks],
            })
        out.sort(key=lambda s: s["avg_rs"], reverse=True)
        return out

    def insights(self):
        rows = self.universe_rows()
        sectors = self.sector_summary()
        lines = []
        if sectors:
            top = sectors[0]
            trend = "strengthening" if top["avg_rs_change"] >= 0 else "cooling off"
            lines.append(
                f"{top['sector']} is the strongest sector today (avg RS {top['avg_rs']}, "
                f"{'+' if top['avg_rs_change']>=0 else ''}{top['avg_rs_change']} vs. yesterday) and is {trend}."
            )
            weak = sectors[-1]
            lines.append(
                f"{weak['sector']} is the weakest sector (avg RS {weak['avg_rs']})."
            )
        stage2 = [r for r in rows if r["stage"] == 2]
        stage2.sort(key=lambda r: r["rs_rating"], reverse=True)
        if stage2:
            leader = stage2[0]
            lines.append(
                f"{leader['ticker']} leads Stage 2 breakouts with an RS Rating of {leader['rs_rating']} "
                f"in {leader['sector']}."
            )
        base_candidates = [r for r in rows if r["stage"] == 1]
        base_candidates.sort(key=lambda r: r["rs_rating"], reverse=True)
        if base_candidates:
            b = base_candidates[0]
            lines.append(
                f"Watch {b['ticker']} ({b['sector']}) — highest RS Rating ({b['rs_rating']}) "
                f"among Stage 1 base-building names."
            )
        thirteen_f = self.thirteen_f()
        increases = [h for h in thirteen_f["latest"] if h["action"] == "Increased"]
        increases.sort(key=lambda h: h["value"], reverse=True)
        if increases:
            top_inc = increases[0]
            lines.append(
                f"{top_inc['filer']} increased its {top_inc['ticker']} stake by "
                f"{top_inc['change_pct']}% in {top_inc['quarter']} (13F)."
            )
        return lines


_MARKET_CACHE = None


def get_market():
    global _MARKET_CACHE
    if _MARKET_CACHE is None:
        _MARKET_CACHE = Market()
    return _MARKET_CACHE
