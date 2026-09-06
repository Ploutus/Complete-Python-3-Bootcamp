"""
Live data providers — Yahoo Finance (prices) and SEC EDGAR (13F filings).

Both providers are stdlib-only (urllib, json, xml.etree) and fail soft:
every public method returns None (or a partial result) on any network,
parsing, or matching failure instead of raising, so `data_engine.Market`
can fall back to its synthetic simulation per-ticker / per-institution.

Each provider does one fast "is this reachable at all" probe before doing
real work, so that being fully offline (as in this sandbox) costs one
short timeout instead of one timeout per ticker/institution.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote
from xml.etree import ElementTree as ET

USER_AGENT = "TradingDashboard/1.0 (demo tool; contact demo@example.com)"
TIMEOUT = 6  # seconds — kept short so an offline environment fails fast

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


# ---------------------------------------------------------------- cache ----
def _cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return os.path.join(CACHE_DIR, safe + ".json")


def cache_get(key, max_age_seconds):
    path = _cache_path(key)
    if not os.path.isfile(path):
        return None
    if time.time() - os.path.getmtime(path) > max_age_seconds:
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def cache_set(key, data):
    try:
        with open(_cache_path(key), "w") as f:
            json.dump(data, f)
    except Exception:
        pass


# ---------------------------------------------------------------- http -----
def _http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ============================================================ Yahoo ========
class YahooFinanceProvider:
    """Daily OHLCV bars from Yahoo Finance's public chart endpoint."""

    _available = None  # tri-state: None=unknown, True/False after first probe

    @staticmethod
    def _raw_fetch(ticker, range_, interval):
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}"
            f"?range={range_}&interval={interval}"
        )
        try:
            data = _http_get_json(url)
        except Exception:
            return None
        try:
            result = data["chart"]["result"][0]
            timestamps = result["timestamp"]
            quote_data = result["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError):
            return None

        bars = []
        opens, highs, lows, closes, volumes = (
            quote_data.get("open", []), quote_data.get("high", []),
            quote_data.get("low", []), quote_data.get("close", []),
            quote_data.get("volume", []),
        )
        for i, ts in enumerate(timestamps):
            try:
                o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            except IndexError:
                continue
            if None in (o, h, l, c):
                continue
            v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            bars.append({
                "date": datetime.fromtimestamp(ts, tz=timezone.utc),
                "open": round(float(o), 2), "high": round(float(h), 2),
                "low": round(float(l), 2), "close": round(float(c), 2),
                "volume": int(v),
            })
        return bars

    @classmethod
    def is_available(cls):
        if cls._available is None:
            cls._available = cls._raw_fetch("AAPL", "5d", "1d") is not None
        return cls._available

    @classmethod
    def fetch_daily(cls, ticker, range_="3y", interval="1d", use_cache=True, min_bars=260):
        """Returns a list of {date(datetime), open, high, low, close, volume} or None."""
        cache_key = f"yf_{ticker}_{range_}_{interval}"
        if use_cache:
            cached = cache_get(cache_key, max_age_seconds=12 * 3600)
            if cached:
                return [dict(b, date=datetime.strptime(b["date"], "%Y-%m-%d")) for b in cached]

        if not cls.is_available():
            return None

        bars = cls._raw_fetch(ticker, range_, interval)
        if not bars or len(bars) < min_bars:
            return None

        if use_cache:
            cache_set(cache_key, [dict(b, date=b["date"].strftime("%Y-%m-%d")) for b in bars])
        return bars


# ========================================================= SEC EDGAR =======
_ISSUER_SUFFIXES = [
    " INCORPORATED", " CORPORATION", " COMPANY", " HOLDINGS", " HOLDING",
    " GROUP", " INC", " CORP", " CO", " PLC", " LTD", " LLC", " LP",
]
_ISSUER_NOISE_WORDS = {"CLASS", "CL", "COM", "DEL", "NEW", "THE", "SHS", "STK", "ADR", "SPONSORED"}


def normalize_issuer(name):
    s = name.upper()
    s = re.sub(r"[.,'\-]", " ", s)
    for suf in _ISSUER_SUFFIXES:
        s = s.replace(suf + " ", " ")
        if s.endswith(suf):
            s = s[: -len(suf)]
    words = [w for w in s.split() if w not in _ISSUER_NOISE_WORDS and not re.fullmatch(r"[A-Z]", w)]
    return " ".join(words).strip()


def quarter_sort_key(label):
    try:
        q, y = label.split()
        return (int(y), int(q[1:]))
    except Exception:
        return (0, 0)


def _quarter_label_from_date(date_str):
    if not date_str:
        return None
    try:
        y, m, _ = date_str.split("-")
        q = (int(m) - 1) // 3 + 1
        return f"Q{q} {y}"
    except Exception:
        return None


def _match_issuer(raw_issuer, norm_lookup):
    norm = normalize_issuer(raw_issuer)
    if not norm:
        return None
    if norm in norm_lookup:
        return norm_lookup[norm]
    words = norm.split()
    if not words:
        return None
    prefix = " ".join(words[:2]) if len(words) > 1 else words[0]
    for key, ticker in norm_lookup.items():
        if key == norm:
            return ticker
        if key.startswith(prefix) or prefix.startswith(key):
            return ticker
    return None


def _parse_holdings_xml(xml_text):
    """Parses a Form 13F information-table XML into raw issuer/cusip/value/shares rows."""
    holdings = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return holdings

    def local(tag):
        return tag.split("}")[-1]

    for node in root.iter():
        if local(node.tag) != "infoTable":
            continue
        row = {}
        for child in node.iter():
            name = local(child.tag)
            text = (child.text or "").strip()
            if name == "nameOfIssuer":
                row["issuer"] = text
            elif name == "cusip":
                row["cusip"] = text
            elif name == "value":
                try:
                    row["value_thousands"] = float(text)
                except ValueError:
                    row["value_thousands"] = 0.0
            elif name == "sshPrnamt":
                try:
                    row["shares"] = int(float(text))
                except ValueError:
                    row["shares"] = 0
        if row.get("issuer"):
            holdings.append(row)
    return holdings


class SecEdgarProvider:
    """13F-HR institutional holdings from SEC EDGAR (data.sec.gov / www.sec.gov)."""

    _available = None

    @staticmethod
    def resolve_cik(name):
        cache_key = f"cik_{name}"
        cached = cache_get(cache_key, max_age_seconds=30 * 24 * 3600)
        if cached is not None:
            return cached.get("cik")
        url = (
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&company={quote(name)}&type=13F-HR&dateb=&owner=include&count=10&output=atom"
        )
        try:
            text = _http_get_text(url)
        except Exception:
            return None
        m = re.search(r"CIK=(\d{10})", text)
        cik = m.group(1) if m else None
        cache_set(cache_key, {"cik": cik})
        return cik

    @classmethod
    def is_available(cls):
        if cls._available is None:
            cls._available = cls.resolve_cik("Berkshire Hathaway Inc") is not None
        return cls._available

    @staticmethod
    def _fetch_information_table_xml(cik, accession):
        cik_int = str(int(cik))
        acc_nodash = accession.replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
        try:
            idx = _http_get_json(index_url)
        except Exception:
            return None
        items = idx.get("directory", {}).get("item", [])
        xml_names = [it.get("name", "") for it in items if it.get("name", "").lower().endswith(".xml")]
        infotable = next((n for n in xml_names if "infotable" in n.lower()), None)
        if not infotable:
            infotable = next((n for n in xml_names if n.lower() != "primary_doc.xml"), None)
        if not infotable:
            return None
        time.sleep(0.1)  # be a courteous SEC citizen between calls
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{infotable}"
        try:
            return _http_get_text(xml_url)
        except Exception:
            return None

    @classmethod
    def fetch_institution_holdings(cls, name, universe_meta, max_quarters=3):
        """Returns raw rows [{filer, ticker, quarter, shares, value}] matched against
        `universe_meta` (ticker -> {"name": ...}), or None if nothing could be retrieved."""
        if not cls.is_available():
            return None

        cik = cls.resolve_cik(name)
        if not cik:
            return None

        try:
            subs = _http_get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
        except Exception:
            return None

        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        report_dates = recent.get("reportDate", [None] * len(forms))
        filing_dates = recent.get("filingDate", [None] * len(forms))

        picks = []
        for form, acc, rdate, fdate in zip(forms, accessions, report_dates, filing_dates):
            if form == "13F-HR":
                picks.append((acc, rdate or fdate))
            if len(picks) >= max_quarters:
                break
        if not picks:
            return None

        norm_lookup = {normalize_issuer(m["name"]): t for t, m in universe_meta.items()}

        rows = []
        for acc, rdate in picks:
            time.sleep(0.1)
            xml_text = cls._fetch_information_table_xml(cik, acc)
            if not xml_text:
                continue
            quarter = _quarter_label_from_date(rdate)
            if not quarter:
                continue
            for h in _parse_holdings_xml(xml_text):
                ticker = _match_issuer(h.get("issuer", ""), norm_lookup)
                if not ticker:
                    continue
                rows.append({
                    "filer": name,
                    "ticker": ticker,
                    "quarter": quarter,
                    "shares": h.get("shares", 0),
                    "value": round(h.get("value_thousands", 0.0) * 1000),
                })
        return rows if rows else None
