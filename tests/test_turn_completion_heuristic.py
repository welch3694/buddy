"""Tests for tier-1 turn-completion heuristics (#80)."""

from __future__ import annotations

import unittest

from buddy_tools.voice.turn_completion_heuristic import (
    HeuristicConfig,
    TurnCompletionVerdict,
    classify_turn_completion_heuristic,
    reset_heuristic_config_for_tests,
)


class ClassifyTurnCompletionHeuristicTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_heuristic_config_for_tests()

    def tearDown(self) -> None:
        reset_heuristic_config_for_tests()

    def test_disfluency_suffixes_continue(self) -> None:
        for text in (
            "I was thinking um",
            "Let me see uh",
            "Well er",
            "Hmm",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_turn_completion_heuristic(text),
                    TurnCompletionVerdict.CONTINUE,
                )

    def test_dangling_conjunctions_continue(self) -> None:
        for text in (
            "I need to go and",
            "So because",
            "I want pizza but",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_turn_completion_heuristic(text),
                    TurnCompletionVerdict.CONTINUE,
                )

    def test_punctuation_cutoffs_continue(self) -> None:
        for text in (
            "I was going to say,",
            "Wait...",
            "One more thing—",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_turn_completion_heuristic(text),
                    TurnCompletionVerdict.CONTINUE,
                )

    def test_complete_utterances_unknown(self) -> None:
        for text in (
            "Hello there",
            "Thanks.",
            "Well.",
            "",
            "   ",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_turn_completion_heuristic(text),
                    TurnCompletionVerdict.UNKNOWN,
                )

    def test_custom_patterns_via_config(self) -> None:
        config = HeuristicConfig(extra_disfluency_suffixes=frozenset({"yknow"}))
        self.assertEqual(
            classify_turn_completion_heuristic("I mean yknow", config=config),
            TurnCompletionVerdict.CONTINUE,
        )

    def test_disabled_returns_unknown(self) -> None:
        config = HeuristicConfig(enabled=False)
        self.assertEqual(
            classify_turn_completion_heuristic("I was thinking um", config=config),
            TurnCompletionVerdict.UNKNOWN,
        )

    def test_max_continue_holds_from_config(self) -> None:
        config = HeuristicConfig(max_continue_holds=3)
        self.assertEqual(config.max_continue_holds, 3)


if __name__ == "__main__":
    unittest.main()
