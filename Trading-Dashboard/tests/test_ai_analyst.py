"""
Offline tests for ai_analyst.py.

No real Claude API calls are made here (this sandbox has no ANTHROPIC_API_KEY
configured anyway). The LLM path is exercised by monkeypatching
`ai_analyst._ensure_client` to return a fake client whose `.messages.create`
raises the exact exception types the real `anthropic` SDK raises (imported
for real -- `pip install anthropic` -- so the `except` clauses in
`_try_llm_analysis` are tested against the real classes, not guesses), or
returns a small fake Message-shaped object. The rule-based fallback is
tested directly against realistic facts dicts shaped like
`Market.analysis_facts()`.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_analyst


def make_facts(stage, **overrides):
    facts = {
        "ticker": "MCD", "name": "McDonald's Corp.", "sector": "Consumer Discretionary",
        "price": 200.86, "day_chg_pct": 1.12, "week_chg_pct": -2.63,
        "stage": stage, "stage_label": {
            1: "Stage 1 · Basing", 2: "Stage 2 · Advancing",
            3: "Stage 3 · Topping", 4: "Stage 4 · Declining",
        }[stage],
        "rs_rating": 80, "rs_rating_5d_ago": 75,
        "ma30w": 176.09, "ma30w_slope_10w_pct": 16.1,
        "high_12w": 233.3, "low_12w": 175.78,
        "high_52w": 233.3, "low_52w": 114.2,
        "data_source": "simulated",
        "recent_13f": [{
            "filer": "Citadel Advisors LLC", "quarter": "Q2 2026",
            "action": "Increased", "change_pct": 40.5,
        }],
    }
    facts.update(overrides)
    return facts


class FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeMessage:
    def __init__(self, text, stop_reason="end_turn", model="claude-opus-5"):
        self.content = [FakeTextBlock(text)]
        self.stop_reason = stop_reason
        self.model = model


class FakeMessagesNamespace:
    def __init__(self, side_effect=None, return_value=None):
        self._side_effect = side_effect
        self._return_value = return_value

    def create(self, **kwargs):
        if self._side_effect is not None:
            raise self._side_effect
        return self._return_value


class FakeClient:
    def __init__(self, side_effect=None, return_value=None):
        self.messages = FakeMessagesNamespace(side_effect=side_effect, return_value=return_value)


class RuleBasedAnalysisTests(unittest.TestCase):
    def test_stage_2_mentions_advancing_and_ma_break_level(self):
        result = ai_analyst.rule_based_analysis(make_facts(2))
        self.assertEqual(result["source"], "rule_based")
        self.assertIn("Stage 2", result["summary"])
        self.assertIn("176.09", result["summary"])  # the MA level, as the key level to watch

    def test_stage_1_mentions_basing_and_breakout_confirmation(self):
        result = ai_analyst.rule_based_analysis(make_facts(1))
        self.assertIn("Stage 1", result["summary"])
        self.assertIn("233.3", result["summary"])  # 12w high as the breakout confirmation level

    def test_stage_3_mentions_topping_and_stage_4_transition(self):
        result = ai_analyst.rule_based_analysis(make_facts(3))
        self.assertIn("Stage 3", result["summary"])
        self.assertIn("Stage 4", result["summary"])

    def test_stage_4_mentions_declining_and_52w_low(self):
        result = ai_analyst.rule_based_analysis(make_facts(4))
        self.assertIn("Stage 4", result["summary"])
        self.assertIn("114.2", result["summary"])

    def test_includes_13f_activity_when_present(self):
        result = ai_analyst.rule_based_analysis(make_facts(2))
        self.assertIn("Citadel Advisors LLC", result["summary"])
        self.assertIn("+40.5", result["summary"])

    def test_omits_13f_section_when_absent(self):
        result = ai_analyst.rule_based_analysis(make_facts(2, recent_13f=[]))
        self.assertNotIn("Institutionel aktivitet", result["summary"])

    def test_flags_simulated_data_source(self):
        result = ai_analyst.rule_based_analysis(make_facts(2, data_source="simulated"))
        self.assertIn("SIMULERET", result["summary"])

    def test_does_not_flag_live_data_source(self):
        result = ai_analyst.rule_based_analysis(make_facts(2, data_source="live"))
        self.assertNotIn("SIMULERET", result["summary"])


class BuildPromptFactsTests(unittest.TestCase):
    def test_includes_key_numbers_and_stage(self):
        text = ai_analyst.build_prompt_facts(make_facts(2))
        self.assertIn("MCD", text)
        self.assertIn("Stage 2", text)
        self.assertIn("176.09", text)
        self.assertIn("Citadel Advisors LLC", text)

    def test_flags_simulated_data(self):
        text = ai_analyst.build_prompt_facts(make_facts(2, data_source="simulated"))
        self.assertIn("SIMULERET", text)


class TryLlmAnalysisTests(unittest.TestCase):
    def setUp(self):
        self._orig_ensure_client = ai_analyst._ensure_client

    def tearDown(self):
        ai_analyst._ensure_client = self._orig_ensure_client

    def test_no_client_returns_reason(self):
        ai_analyst._ensure_client = lambda: None
        ai_analyst._client_unavailable_reason = "anthropic_not_installed"
        result, reason = ai_analyst._try_llm_analysis(make_facts(2))
        self.assertIsNone(result)
        self.assertEqual(reason, "anthropic_not_installed")

    def _fake_http_response(self, status_code):
        import httpx2
        return httpx2.Response(
            status_code=status_code,
            request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
        )

    def test_authentication_error_falls_back_with_reason(self):
        import anthropic
        exc = anthropic.AuthenticationError("bad key", response=self._fake_http_response(401), body=None)
        ai_analyst._ensure_client = lambda: FakeClient(side_effect=exc)
        result, reason = ai_analyst._try_llm_analysis(make_facts(2))
        self.assertIsNone(result)
        self.assertEqual(reason, "invalid_api_key")

    def test_rate_limit_error_falls_back_with_reason(self):
        import anthropic
        exc = anthropic.RateLimitError("slow down", response=self._fake_http_response(429), body=None)
        ai_analyst._ensure_client = lambda: FakeClient(side_effect=exc)
        result, reason = ai_analyst._try_llm_analysis(make_facts(2))
        self.assertIsNone(result)
        self.assertEqual(reason, "rate_limited")

    def test_api_status_error_falls_back_with_status_code(self):
        import anthropic
        exc = anthropic.APIStatusError("server error", response=self._fake_http_response(503), body=None)
        ai_analyst._ensure_client = lambda: FakeClient(side_effect=exc)
        result, reason = ai_analyst._try_llm_analysis(make_facts(2))
        self.assertIsNone(result)
        self.assertEqual(reason, "api_error_503")

    def test_connection_error_falls_back_with_reason(self):
        import anthropic
        exc = anthropic.APIConnectionError(request=types.SimpleNamespace())
        ai_analyst._ensure_client = lambda: FakeClient(side_effect=exc)
        result, reason = ai_analyst._try_llm_analysis(make_facts(2))
        self.assertIsNone(result)
        self.assertEqual(reason, "network_error")

    def test_refusal_stop_reason_falls_back(self):
        ai_analyst._ensure_client = lambda: FakeClient(
            return_value=FakeMessage("", stop_reason="refusal")
        )
        result, reason = ai_analyst._try_llm_analysis(make_facts(2))
        self.assertIsNone(result)
        self.assertEqual(reason, "refused")

    def test_empty_text_falls_back(self):
        ai_analyst._ensure_client = lambda: FakeClient(return_value=FakeMessage("   "))
        result, reason = ai_analyst._try_llm_analysis(make_facts(2))
        self.assertIsNone(result)
        self.assertEqual(reason, "empty_response")

    def test_success_returns_summary_and_model(self):
        ai_analyst._ensure_client = lambda: FakeClient(
            return_value=FakeMessage("MCD er i Stage 2...", model="claude-opus-5")
        )
        result, reason = ai_analyst._try_llm_analysis(make_facts(2))
        self.assertIsNone(reason)
        self.assertEqual(result["source"], "llm")
        self.assertEqual(result["summary"], "MCD er i Stage 2...")
        self.assertEqual(result["model"], "claude-opus-5")


class AnalyzeFallbackTests(unittest.TestCase):
    def setUp(self):
        self._orig = ai_analyst._try_llm_analysis

    def tearDown(self):
        ai_analyst._try_llm_analysis = self._orig

    def test_falls_back_to_rule_based_and_carries_reason(self):
        ai_analyst._try_llm_analysis = lambda facts: (None, "invalid_api_key")
        result = ai_analyst.analyze(make_facts(2))
        self.assertEqual(result["source"], "rule_based")
        self.assertEqual(result["llm_unavailable_reason"], "invalid_api_key")

    def test_uses_llm_result_when_available(self):
        ai_analyst._try_llm_analysis = lambda facts: ({"source": "llm", "summary": "x", "model": "m"}, None)
        result = ai_analyst.analyze(make_facts(2))
        self.assertEqual(result["source"], "llm")


class AnalyzeCachedTests(unittest.TestCase):
    def setUp(self):
        self._orig_get = ai_analyst.cache_get
        self._orig_set = ai_analyst.cache_set
        self._orig_analyze = ai_analyst.analyze
        self._store = {}
        ai_analyst.cache_get = lambda key, max_age_seconds: self._store.get(key)
        ai_analyst.cache_set = lambda key, data: self._store.__setitem__(key, data)

    def tearDown(self):
        ai_analyst.cache_get = self._orig_get
        ai_analyst.cache_set = self._orig_set
        ai_analyst.analyze = self._orig_analyze

    def test_second_call_is_served_from_cache(self):
        calls = []

        def fake_analyze(facts):
            calls.append(facts["ticker"])
            return {"source": "rule_based", "summary": "first"}

        ai_analyst.analyze = fake_analyze
        first = ai_analyst.analyze_cached(make_facts(2))
        second = ai_analyst.analyze_cached(make_facts(2))

        self.assertEqual(len(calls), 1)  # only computed once
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["summary"], "first")

    def test_cache_key_changes_when_stage_changes(self):
        seen_keys = []
        real_set = ai_analyst.cache_set

        def spy_set(key, data):
            seen_keys.append(key)
            real_set(key, data)

        ai_analyst.cache_set = spy_set
        ai_analyst.analyze = lambda facts: {"source": "rule_based", "summary": "x"}

        ai_analyst.analyze_cached(make_facts(2))
        ai_analyst.analyze_cached(make_facts(4))

        self.assertEqual(len(set(seen_keys)), 2)  # different stage -> different cache key


if __name__ == "__main__":
    unittest.main()
