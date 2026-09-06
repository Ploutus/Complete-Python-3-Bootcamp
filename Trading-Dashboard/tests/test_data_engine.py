import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_engine import _finalize_holdings, _classify_stage, _raw_rs_score, _percentile_ranks


class FinalizeHoldingsTests(unittest.TestCase):
    def test_derives_new_soldout_increased_decreased_held(self):
        raw = [
            {"filer": "F", "ticker": "AAPL", "quarter": "Q4 2025", "shares": 0, "value": 0},
            {"filer": "F", "ticker": "AAPL", "quarter": "Q1 2026", "shares": 1000, "value": 100},
            {"filer": "F", "ticker": "AAPL", "quarter": "Q2 2026", "shares": 1200, "value": 120},
            {"filer": "F", "ticker": "MSFT", "quarter": "Q1 2026", "shares": 500, "value": 50},
            {"filer": "F", "ticker": "MSFT", "quarter": "Q2 2026", "shares": 0, "value": 0},
        ]
        out = _finalize_holdings(raw)
        by_q = {(r["ticker"], r["quarter"]): r for r in out}

        self.assertEqual(by_q[("AAPL", "Q4 2025")]["action"], "Held")  # first record, baseline
        self.assertEqual(by_q[("AAPL", "Q1 2026")]["action"], "New")
        self.assertEqual(by_q[("AAPL", "Q2 2026")]["action"], "Increased")
        self.assertEqual(by_q[("MSFT", "Q1 2026")]["action"], "Held")  # first record for this filer/ticker
        self.assertEqual(by_q[("MSFT", "Q2 2026")]["action"], "Sold Out")
        self.assertEqual(by_q[("MSFT", "Q2 2026")]["change_pct"], -100.0)

    def test_out_of_order_input_is_sorted_chronologically_first(self):
        raw = [
            {"filer": "F", "ticker": "AAPL", "quarter": "Q2 2026", "shares": 2000, "value": 200},
            {"filer": "F", "ticker": "AAPL", "quarter": "Q4 2025", "shares": 1000, "value": 100},
            {"filer": "F", "ticker": "AAPL", "quarter": "Q1 2026", "shares": 1000, "value": 100},
        ]
        out = _finalize_holdings(raw)
        by_q = {r["quarter"]: r for r in out}
        self.assertEqual(by_q["Q4 2025"]["action"], "Held")
        self.assertEqual(by_q["Q1 2026"]["action"], "Held")
        self.assertEqual(by_q["Q2 2026"]["action"], "Increased")
        self.assertEqual(by_q["Q2 2026"]["change_pct"], 100.0)


class StageClassificationTests(unittest.TestCase):
    def test_steady_uptrend_is_stage_2(self):
        closes = [100 * (1.01 ** i) for i in range(60)]
        self.assertEqual(_classify_stage(closes), 2)

    def test_steady_downtrend_is_stage_4(self):
        closes = [100 * (0.99 ** i) for i in range(60)]
        self.assertEqual(_classify_stage(closes), 4)

    def test_flat_series_is_stage_1_basing(self):
        closes = [100.0] * 60
        self.assertEqual(_classify_stage(closes), 1)

    def test_insufficient_history_defaults_to_stage_1(self):
        self.assertEqual(_classify_stage([100.0] * 10), 1)


class RsRatingTests(unittest.TestCase):
    def test_percentile_ranks_span_1_to_99_and_strongest_on_top(self):
        scores = {f"T{i}": i for i in range(20)}
        ranks = _percentile_ranks(scores)
        self.assertEqual(ranks["T19"], 99)
        self.assertEqual(ranks["T0"], 1)
        self.assertTrue(all(1 <= v <= 99 for v in ranks.values()))
        self.assertTrue(ranks["T19"] > ranks["T0"])

    def test_missing_history_defaults_to_neutral_50(self):
        ranks = _percentile_ranks({"A": 0.1, "B": None})
        self.assertEqual(ranks["B"], 50)

    def test_raw_rs_score_needs_full_12_month_history(self):
        closes = [100.0] * 100
        self.assertIsNone(_raw_rs_score(closes, 99))  # not enough history for 252-day lookback


if __name__ == "__main__":
    unittest.main()
