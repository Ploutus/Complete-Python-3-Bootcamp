"""
Offline tests for providers.py against realistic fixture payloads.

This sandbox has zero outbound network access, so the actual HTTP calls to
Yahoo Finance / SEC EDGAR cannot be exercised end-to-end here. These tests
instead validate the pure parsing/matching logic against fixtures shaped
exactly like the real APIs' documented responses, and verify the network
functions are monkeypatchable seams so the full pipeline logic (probe ->
resolve CIK -> list filings -> fetch info table -> match issuer -> emit
rows) is provably correct independent of the network layer.

Run: python3 -m unittest tests.test_providers -v   (from Trading-Dashboard/)
"""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers
from providers import (
    YahooFinanceProvider, SecEdgarProvider,
    normalize_issuer, quarter_sort_key, _quarter_label_from_date,
    _parse_holdings_xml, _match_issuer,
)


SAMPLE_YAHOO_CHART = {
    "chart": {
        "result": [{
            "meta": {"symbol": "AAPL"},
            "timestamp": [1700000000, 1700086400, 1700172800],
            "indicators": {
                "quote": [{
                    "open": [180.0, 181.5, None],
                    "high": [182.0, 183.0, 184.0],
                    "low": [179.0, 180.5, 181.0],
                    "close": [181.0, 182.5, 183.5],
                    "volume": [50_000_000, 48_000_000, None],
                }]
            },
        }],
        "error": None,
    }
}

# Namespaced 13F information table, shaped like SEC's published XSD.
SAMPLE_13F_XML_NAMESPACED = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>150000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>1000000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority><Sole>1000000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>MICROSOFT CORP</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>594918104</cusip>
    <value>90000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>250000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>SOME UNRELATED SMALLCAP CO</nameOfIssuer>
    <cusip>999999999</cusip>
    <value>500</value>
    <shrsOrPrnAmt><sshPrnamt>1000</sshPrnamt></shrsOrPrnAmt>
  </infoTable>
</informationTable>
"""

# Some real filers omit the namespace declaration - parser must not depend on it.
SAMPLE_13F_XML_NO_NAMESPACE = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable>
  <infoTable>
    <nameOfIssuer>ALPHABET INC-CL A</nameOfIssuer>
    <cusip>02079K305</cusip>
    <value>30000</value>
    <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt></shrsOrPrnAmt>
  </infoTable>
</informationTable>
"""


class YahooParsingTests(unittest.TestCase):
    def test_raw_fetch_parses_chart_json_and_skips_null_bars(self):
        original = providers._http_get_json
        providers._http_get_json = lambda url: SAMPLE_YAHOO_CHART
        try:
            bars = YahooFinanceProvider._raw_fetch("AAPL", "3y", "1d")
        finally:
            providers._http_get_json = original

        # the 3rd bar has open=None and volume=None -> the open=None bar must be dropped
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["close"], 181.0)
        self.assertEqual(bars[0]["volume"], 50_000_000)
        self.assertIsInstance(bars[0]["date"], datetime)
        self.assertEqual(bars[0]["date"].tzinfo, timezone.utc)

    def test_raw_fetch_returns_none_on_malformed_payload(self):
        original = providers._http_get_json
        providers._http_get_json = lambda url: {"chart": {"result": None}}
        try:
            self.assertIsNone(YahooFinanceProvider._raw_fetch("AAPL", "3y", "1d"))
        finally:
            providers._http_get_json = original

    def test_raw_fetch_returns_none_on_network_error(self):
        original = providers._http_get_json
        def boom(url):
            raise OSError("no route to host")
        providers._http_get_json = boom
        try:
            self.assertIsNone(YahooFinanceProvider._raw_fetch("AAPL", "3y", "1d"))
        finally:
            providers._http_get_json = original

    def test_fetch_daily_rejects_short_history_and_falls_back(self):
        original = YahooFinanceProvider._raw_fetch
        original_avail = YahooFinanceProvider._available
        YahooFinanceProvider._raw_fetch = staticmethod(lambda ticker, r, i: [{"date": datetime.now(timezone.utc), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 5)
        YahooFinanceProvider._available = True
        try:
            self.assertIsNone(YahooFinanceProvider.fetch_daily("AAPL", use_cache=False, min_bars=260))
        finally:
            YahooFinanceProvider._raw_fetch = original
            YahooFinanceProvider._available = original_avail


class IssuerNameMatchingTests(unittest.TestCase):
    def test_normalize_strips_suffixes_and_class_markers(self):
        self.assertEqual(normalize_issuer("APPLE INC"), "APPLE")
        self.assertEqual(normalize_issuer("MICROSOFT CORP"), "MICROSOFT")
        self.assertEqual(normalize_issuer("ALPHABET INC-CL A"), "ALPHABET")
        self.assertEqual(normalize_issuer("META PLATFORMS INC-CLASS A"), "META PLATFORMS")

    def test_match_issuer_exact_and_fuzzy(self):
        lookup = {
            normalize_issuer("Apple Inc."): "AAPL",
            normalize_issuer("Microsoft Corp."): "MSFT",
            normalize_issuer("Alphabet Inc."): "GOOGL",
        }
        self.assertEqual(_match_issuer("APPLE INC", lookup), "AAPL")
        self.assertEqual(_match_issuer("MICROSOFT CORPORATION", lookup), "MSFT")
        self.assertEqual(_match_issuer("ALPHABET INC-CL A", lookup), "GOOGL")
        self.assertIsNone(_match_issuer("SOME UNRELATED SMALLCAP CO", lookup))


class ThirteenFXmlParsingTests(unittest.TestCase):
    def test_parses_namespaced_information_table(self):
        rows = _parse_holdings_xml(SAMPLE_13F_XML_NAMESPACED)
        self.assertEqual(len(rows), 3)
        aapl = next(r for r in rows if r["issuer"] == "APPLE INC")
        self.assertEqual(aapl["cusip"], "037833100")
        self.assertEqual(aapl["shares"], 1_000_000)
        self.assertEqual(aapl["value_thousands"], 150_000_000.0)

    def test_parses_information_table_without_namespace(self):
        rows = _parse_holdings_xml(SAMPLE_13F_XML_NO_NAMESPACE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["issuer"], "ALPHABET INC-CL A")
        self.assertEqual(rows[0]["shares"], 5000)

    def test_malformed_xml_returns_empty_list_not_exception(self):
        self.assertEqual(_parse_holdings_xml("<not><valid"), [])

    def test_quarter_label_from_report_date(self):
        self.assertEqual(_quarter_label_from_date("2026-06-30"), "Q2 2026")
        self.assertEqual(_quarter_label_from_date("2025-12-31"), "Q4 2025")
        self.assertIsNone(_quarter_label_from_date(None))

    def test_quarter_sort_key_orders_chronologically_not_alphabetically(self):
        labels = ["Q4 2025", "Q1 2026", "Q2 2026"]
        self.assertEqual(sorted(labels, key=quarter_sort_key), ["Q4 2025", "Q1 2026", "Q2 2026"])
        # sanity: plain string sort would get this wrong, which is exactly the bug we fixed
        self.assertNotEqual(sorted(labels), sorted(labels, key=quarter_sort_key))


class SecEdgarPipelineTests(unittest.TestCase):
    """End-to-end fetch_institution_holdings with every network call monkeypatched,
    proving the CIK -> submissions -> info-table -> issuer-match wiring is correct."""

    def setUp(self):
        SecEdgarProvider._available = None
        self._orig_resolve_cik = SecEdgarProvider.resolve_cik
        self._orig_fetch_xml = SecEdgarProvider._fetch_information_table_xml
        self._orig_http_json = providers._http_get_json

    def tearDown(self):
        SecEdgarProvider.resolve_cik = self._orig_resolve_cik
        SecEdgarProvider._fetch_information_table_xml = self._orig_fetch_xml
        providers._http_get_json = self._orig_http_json
        SecEdgarProvider._available = None

    def test_full_pipeline_matches_holdings_against_universe(self):
        SecEdgarProvider.resolve_cik = staticmethod(lambda name: "0001067983")

        submissions_json = {
            "filings": {
                "recent": {
                    "form": ["13F-HR", "13F-NT", "13F-HR"],
                    "accessionNumber": ["0001067983-26-000002", "0001067983-26-000001", "0001067983-25-000009"],
                    "reportDate": ["2026-06-30", "2026-03-31", "2025-12-31"],
                    "filingDate": ["2026-08-10", "2026-05-12", "2026-02-11"],
                }
            }
        }
        providers._http_get_json = lambda url: submissions_json

        def fake_fetch_xml(cik, accession):
            return SAMPLE_13F_XML_NAMESPACED if accession.endswith("000002") else SAMPLE_13F_XML_NO_NAMESPACE
        SecEdgarProvider._fetch_information_table_xml = staticmethod(fake_fetch_xml)

        universe_meta = {
            "AAPL": {"name": "Apple Inc.", "sector": "Technology"},
            "MSFT": {"name": "Microsoft Corp.", "sector": "Technology"},
            "GOOGL": {"name": "Alphabet Inc.", "sector": "Communication Services"},
        }

        rows = SecEdgarProvider.fetch_institution_holdings("Berkshire Hathaway Inc", universe_meta, max_quarters=2)

        self.assertIsNotNone(rows)
        tickers_seen = {r["ticker"] for r in rows}
        self.assertEqual(tickers_seen, {"AAPL", "MSFT", "GOOGL"})
        aapl_row = next(r for r in rows if r["ticker"] == "AAPL")
        self.assertEqual(aapl_row["quarter"], "Q2 2026")
        self.assertEqual(aapl_row["shares"], 1_000_000)
        self.assertEqual(aapl_row["value"], 150_000_000_000)  # value_thousands * 1000
        self.assertEqual(aapl_row["filer"], "Berkshire Hathaway Inc")
        # the unrelated smallcap in the fixture must never surface -- no ticker match
        self.assertNotIn("999999999", [r.get("cusip") for r in rows])

    def test_returns_none_when_cik_cannot_be_resolved(self):
        SecEdgarProvider.resolve_cik = staticmethod(lambda name: None)
        self.assertIsNone(SecEdgarProvider.fetch_institution_holdings("Nonexistent Fund LLC", {}, max_quarters=2))

    def test_returns_none_when_probe_unreachable(self):
        SecEdgarProvider._available = False
        SecEdgarProvider.resolve_cik = staticmethod(lambda name: "0001067983")
        self.assertIsNone(SecEdgarProvider.fetch_institution_holdings("Berkshire Hathaway Inc", {}, max_quarters=2))


if __name__ == "__main__":
    unittest.main()
