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


if __name__ == "__main__":
    unittest.main()
