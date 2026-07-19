"""Unit tests for token estimation and billing logic."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.calculator import MODEL_PRICES, UsageCalculator, estimate_tokens


class EstimateTokensTests(unittest.TestCase):
    def test_estimate_tokens_for_empty_string(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)

    def test_estimate_tokens_for_none(self) -> None:
        self.assertEqual(estimate_tokens(None), 0)

    def test_estimate_tokens_for_english_text(self) -> None:
        self.assertEqual(estimate_tokens("abcd"), 1)

    def test_estimate_tokens_for_chinese_text(self) -> None:
        self.assertEqual(estimate_tokens("你好世界"), 3)


class UsageCalculatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calculator = UsageCalculator()

    def test_cached_input_discount_for_gpt_5_5(self) -> None:
        cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=0,
        )
        self.assertEqual(cost, 0.5)

    def test_zero_token_counts_return_zero_cost(self) -> None:
        cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.4",
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        )
        self.assertEqual(cost, 0.0)

    def test_gpt_5_6_sol_uses_cc_switch_four_bucket_pricing(self) -> None:
        cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.6-sol",
            input_tokens=1_000_000,
            cached_input_tokens=600_000,
            cache_write_tokens=100_000,
            output_tokens=200_000,
            reasoning_tokens=50_000,
        )

        self.assertEqual(cost, 8.425)

    def test_reasoning_is_not_billed_twice_when_included_in_output(self) -> None:
        cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.6-sol",
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=1_000_000,
            reasoning_tokens=400_000,
        )

        self.assertEqual(cost, 30.0)

    def test_model_prefix_matching_uses_base_price_table(self) -> None:
        fallback_cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.5-fallback",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=0,
        )
        base_cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=0,
        )
        self.assertEqual(fallback_cost, base_cost)
        self.assertEqual(fallback_cost, MODEL_PRICES["gpt-5.5"]["cached_input"])

    def test_base_url_specific_price_wins_for_same_model_name(self) -> None:
        calculator = UsageCalculator(
            {
                "shared-model": {
                    "input": 1.0,
                    "cached_input": 1.0,
                    "output": 1.0,
                    "reasoning": 1.0,
                },
                "vendor-a/shared-model": {
                    "model": "shared-model",
                    "provider": "vendor-a",
                    "base_url": "https://api.vendor-a.example/v1",
                    "input": 3.0,
                    "cached_input": 3.0,
                    "output": 3.0,
                    "reasoning": 3.0,
                },
                "vendor-b/shared-model": {
                    "model": "shared-model",
                    "provider": "vendor-b",
                    "base_url": "https://api.vendor-b.example/v1",
                    "input": 7.0,
                    "cached_input": 7.0,
                    "output": 7.0,
                    "reasoning": 7.0,
                },
            }
        )

        vendor_a_cost = calculator.calculate_cost_usd(
            model_name="shared-model",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            provider="vendor-a",
            base_url="https://api.vendor-a.example/v1",
        )
        fallback_cost = calculator.calculate_cost_usd(
            model_name="shared-model",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            provider="unknown",
            base_url="https://unknown.example/v1",
        )

        self.assertEqual(vendor_a_cost, 3.0)
        self.assertEqual(fallback_cost, 1.0)

    def test_wildcard_model_profile_matches_unknown_model_prefix(self) -> None:
        calculator = UsageCalculator(
            {
                "custom-family": {
                    "model": "custom-*",
                    "input": 2.0,
                    "cached_input": 2.0,
                    "output": 2.0,
                    "reasoning": 2.0,
                }
            }
        )

        cost = calculator.calculate_cost_usd(
            model_name="custom-large",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
        )

        self.assertEqual(cost, 2.0)


if __name__ == "__main__":
    unittest.main()
