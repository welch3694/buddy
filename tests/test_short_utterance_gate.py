"""Tests for short-utterance discard gate (#124)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from buddy_tools.voice.short_utterance_gate import (
    DiscardReason,
    ShortUtteranceConfig,
    reset_short_utterance_config_for_tests,
    should_discard_utterance,
)


class ShortUtteranceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_short_utterance_config_for_tests()

    def tearDown(self) -> None:
        reset_short_utterance_config_for_tests()

    def test_filler_whole_utterance_discarded(self) -> None:
        for text in ("yeah", "Uh", "HMM!", "um", "mhm"):
            with self.subTest(text=text):
                self.assertEqual(
                    should_discard_utterance(text),
                    DiscardReason.FILLER,
                )

    def test_below_min_words_discarded(self) -> None:
        self.assertEqual(
            should_discard_utterance("hi"),
            DiscardReason.MIN_WORDS,
        )

    def test_allowlist_short_replies_pass(self) -> None:
        for text in ("yes", "No", "OK", "sure", "yep", "wait"):
            with self.subTest(text=text):
                self.assertIsNone(should_discard_utterance(text))

    def test_multi_word_passes(self) -> None:
        self.assertIsNone(should_discard_utterance("hello there"))

    def test_action_intent_passes(self) -> None:
        self.assertIsNone(should_discard_utterance("go live"))

    def test_gate_disabled_passes_everything(self) -> None:
        cfg = ShortUtteranceConfig(enabled=False)
        self.assertIsNone(should_discard_utterance("yeah", config=cfg))
        self.assertIsNone(should_discard_utterance("hi", config=cfg))

    def test_min_chars_when_enabled(self) -> None:
        cfg = ShortUtteranceConfig(min_words=1, min_chars=8)
        self.assertEqual(
            should_discard_utterance("hello", config=cfg),
            DiscardReason.MIN_CHARS,
        )
        self.assertIsNone(should_discard_utterance("hello there", config=cfg))

    def test_empty_discarded(self) -> None:
        self.assertEqual(should_discard_utterance("   "), DiscardReason.EMPTY)
        self.assertEqual(should_discard_utterance("..."), DiscardReason.EMPTY)

    def test_from_env_reads_knobs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BUDDY_SHORT_UTTERANCE_GATE": "off",
                "BUDDY_SHORT_UTTERANCE_MIN_WORDS": "3",
                "BUDDY_SHORT_UTTERANCE_MIN_CHARS": "5",
            },
            clear=False,
        ):
            reset_short_utterance_config_for_tests()
            cfg = ShortUtteranceConfig.from_env()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.min_words, 3)
        self.assertEqual(cfg.min_chars, 5)

    def test_filler_not_applied_to_longer_phrase(self) -> None:
        # Trailing disfluency is the heuristic's job; whole-utterance gate must not discard.
        self.assertIsNone(should_discard_utterance("I think uh"))


if __name__ == "__main__":
    unittest.main()
