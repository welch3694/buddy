"""Tests for turn receipts and action-claim heuristics."""

from __future__ import annotations

import unittest

from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_receipts import (
    ToolReceipt,
    claims_without_receipt,
    find_action_claims,
    make_tool_receipt,
)


class FindActionClaimsTests(unittest.TestCase):
    def test_starting_phrases(self) -> None:
        self.assertIn("i'm starting", find_action_claims("I'm starting the live-director skill now."))
        self.assertIn("i am starting", find_action_claims("I am starting the skill."))
        self.assertIn("i've started", find_action_claims("I've started the skill."))
        self.assertIn("starting", find_action_claims("Starting the skill for you."))

    def test_memory_and_config_phrases(self) -> None:
        self.assertIn("saved", find_action_claims("I've saved that to memory."))
        self.assertIn("remembered", find_action_claims("Okay, remembered."))
        self.assertIn("cancelled", find_action_claims("Skill cancelled."))
        self.assertIn("canceled", find_action_claims("Skill canceled."))
        self.assertIn("updated", find_action_claims("Config updated."))

    def test_done_phrases(self) -> None:
        self.assertIn("i'm done", find_action_claims("I'm done with that."))
        self.assertIn("all done", find_action_claims("All done!"))
        self.assertIn("done", find_action_claims("Done."))

    def test_neutral_text_has_no_claims(self) -> None:
        self.assertEqual(find_action_claims("Sure, I can help with that."), [])
        self.assertEqual(find_action_claims(""), [])
        self.assertEqual(find_action_claims("   "), [])


class ClaimsWithoutReceiptTests(unittest.TestCase):
    def test_claim_with_empty_receipts_is_bypass(self) -> None:
        self.assertTrue(
            claims_without_receipt(
                "I'm starting the live-director skill now.",
                [],
            )
        )

    def test_claim_with_any_receipt_is_not_bypass(self) -> None:
        receipt = ToolReceipt(tool="start_skill", args_summary={"name": "live-director"}, status="ok")
        self.assertFalse(
            claims_without_receipt(
                "I'm starting the live-director skill now.",
                [receipt],
            )
        )
        self.assertFalse(
            claims_without_receipt(
                "I'm starting the live-director skill now.",
                [ToolReceipt(tool="start_skill", args_summary=None, status="error")],
            )
        )
        self.assertFalse(
            claims_without_receipt(
                "I'm starting the live-director skill now.",
                [ToolReceipt(tool="start_skill", args_summary=None, status="skipped")],
            )
        )

    def test_neutral_text_is_not_bypass(self) -> None:
        self.assertFalse(claims_without_receipt("That sounds good.", []))


class MakeToolReceiptTests(unittest.TestCase):
    def test_status_from_result(self) -> None:
        ok = make_tool_receipt("list_skills", {}, result=ToolExecutionResult(output="[]"))
        self.assertEqual(ok.status, "ok")
        err = make_tool_receipt(
            "list_skills",
            {},
            result=ToolExecutionResult(output="Error: boom"),
        )
        self.assertEqual(err.status, "error")

    def test_explicit_skipped_status(self) -> None:
        receipt = make_tool_receipt("read_memory", {"scope": "user"}, status="skipped")
        self.assertEqual(receipt.status, "skipped")
        self.assertEqual(receipt.args_summary, {"scope": "user"})


if __name__ == "__main__":
    unittest.main()
