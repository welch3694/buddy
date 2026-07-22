"""Tests for deterministic voice action intent router (#145)."""

from __future__ import annotations

import unittest

from buddy_tools.voice.action_intents import (
    ActionIntent,
    clear_action_intent,
    match_action_intent,
    peek_action_intent,
    pop_action_intent,
    reset_action_intent_stash_for_tests,
    stash_action_intent,
)
from buddy_tools.voice.listening_pause import (
    matches_start_listening,
    matches_stop_listening,
    normalize_transcript,
)


class ActionIntentMatchingTests(unittest.TestCase):
    def test_cancel_skill_phrases(self) -> None:
        for phrase in (
            "cancel skill",
            "Cancel skill!",
            "stop director",
            "stop the director please",
        ):
            with self.subTest(phrase=phrase):
                intent = match_action_intent(phrase)
                self.assertEqual(
                    intent,
                    ActionIntent(tool_name="skill", arguments={"action": "cancel"}),
                )

    def test_pause_skill_phrases(self) -> None:
        for phrase in (
            "pause skill",
            "Pause director.",
            "pause the director now",
        ):
            with self.subTest(phrase=phrase):
                intent = match_action_intent(phrase)
                self.assertEqual(
                    intent,
                    ActionIntent(tool_name="skill", arguments={"action": "pause"}),
                )

    def test_live_director_phrases(self) -> None:
        for phrase in (
            "start director",
            "Start live director!",
            "go live",
            "director flow",
            "go live with the stream",
        ):
            with self.subTest(phrase=phrase):
                intent = match_action_intent(phrase)
                self.assertEqual(
                    intent,
                    ActionIntent(
                        tool_name="skill",
                        arguments={"action": "start", "name": "live-director"},
                    ),
                )

    def test_remember_phrases(self) -> None:
        for phrase in (
            "remember that",
            "Remember that I like coffee",
            "don't forget my birthday",
            "dont forget the keys",
            "keep in mind we leave at noon",
        ):
            with self.subTest(phrase=phrase):
                intent = match_action_intent(phrase)
                self.assertEqual(
                    intent,
                    ActionIntent(
                        tool_name="skill",
                        arguments={"action": "start", "name": "remember"},
                    ),
                )

    def test_edit_personality_phrases(self) -> None:
        for phrase in (
            "edit personality",
            "change how you talk",
            "Change your personality a bit",
        ):
            with self.subTest(phrase=phrase):
                intent = match_action_intent(phrase)
                self.assertEqual(
                    intent,
                    ActionIntent(
                        tool_name="skill",
                        arguments={"action": "start", "name": "edit-personality"},
                    ),
                )

    def test_switch_personality_when_name_extractable(self) -> None:
        self.assertEqual(
            match_action_intent("become coach"),
            ActionIntent(
                tool_name="persona",
                arguments={"action": "switch", "personality_id": "coach"},
            ),
        )
        self.assertEqual(
            match_action_intent("Switch to Buddy!"),
            ActionIntent(
                tool_name="persona",
                arguments={"action": "switch", "personality_id": "buddy"},
            ),
        )
        self.assertEqual(
            match_action_intent("become cool coach"),
            ActionIntent(
                tool_name="persona",
                arguments={"action": "switch", "personality_id": "cool_coach"},
            ),
        )

    def test_switch_personality_rejects_empty_name(self) -> None:
        self.assertIsNone(match_action_intent("become"))
        self.assertIsNone(match_action_intent("switch to"))
        self.assertIsNone(match_action_intent("become!!!"))

    def test_non_matching_speech(self) -> None:
        for phrase in (
            "hello there",
            "what's the weather",
            "please cancel my subscription",
            "I want you to remember something someday",
            "can you become better at chess",
            "start the music",
        ):
            with self.subTest(phrase=phrase):
                self.assertIsNone(match_action_intent(phrase))

    def test_cancel_beats_other_prefixes_by_priority(self) -> None:
        # Control intents are checked before start_skill / switch rules.
        self.assertEqual(
            match_action_intent("cancel skill and go live"),
            ActionIntent(tool_name="skill", arguments={"action": "cancel"}),
        )

    def test_normalize_transcript_still_used(self) -> None:
        self.assertEqual(normalize_transcript("Go live!"), "go live")
        self.assertIsNotNone(match_action_intent("Go live!"))

    def test_listening_pause_matchers_unchanged(self) -> None:
        self.assertTrue(matches_stop_listening("stop listening"))
        self.assertTrue(matches_start_listening("start listening"))
        self.assertFalse(matches_stop_listening("stop director"))
        self.assertIsNone(match_action_intent("stop listening"))
        self.assertIsNone(match_action_intent("start listening"))


class ActionIntentStashTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_action_intent_stash_for_tests()

    def tearDown(self) -> None:
        reset_action_intent_stash_for_tests()

    def test_stash_pop_round_trip(self) -> None:
        intent = ActionIntent(tool_name="start_skill", arguments={"name": "remember"})
        stash_action_intent("turn_1", intent)
        self.assertEqual(pop_action_intent("turn_1"), intent)
        self.assertIsNone(pop_action_intent("turn_1"))

    def test_peek_does_not_remove(self) -> None:
        intent = ActionIntent(tool_name="start_skill", arguments={"name": "live-director"})
        stash_action_intent("turn_peek", intent)
        self.assertEqual(peek_action_intent("turn_peek"), intent)
        self.assertEqual(peek_action_intent("turn_peek"), intent)
        self.assertEqual(pop_action_intent("turn_peek"), intent)

    def test_clear_drops_without_returning(self) -> None:
        intent = ActionIntent(tool_name="cancel_skill", arguments={})
        stash_action_intent("turn_2", intent)
        clear_action_intent("turn_2")
        self.assertIsNone(pop_action_intent("turn_2"))

    def test_none_turn_id_is_noop(self) -> None:
        intent = ActionIntent(tool_name="pause_skill", arguments={})
        stash_action_intent(None, intent)
        self.assertIsNone(pop_action_intent(None))


if __name__ == "__main__":
    unittest.main()
