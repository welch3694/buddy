"""Tests for action-parameter tool consolidation helpers (#109)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buddy_tools.core.consolidate import (
    ActionSpec,
    action_legacy_map,
    build_action_tool,
    resolve_action_args,
)
from buddy_tools.core.registry import execute_tool
from buddy_tools.core.result import ToolExecutionResult

_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(action="list", legacy_name="list_widgets"),
    ActionSpec(
        action="create",
        legacy_name="create_widget",
        required=("name",),
        properties={"name": {"type": "string", "description": "Widget name"}},
    ),
)


class BuildActionToolTests(unittest.TestCase):
    def test_requires_action_enum_and_only_action_in_required(self) -> None:
        tool = build_action_tool(
            name="widget",
            description="Widget operations.",
            actions=_ACTIONS,
        )
        self.assertEqual(tool.name, "widget")
        self.assertEqual(tool.parameters["required"], ["action"])
        action_prop = tool.parameters["properties"]["action"]
        self.assertEqual(action_prop["type"], "string")
        self.assertEqual(action_prop["enum"], ["list", "create"])
        self.assertIn("name", tool.parameters["properties"])

    def test_raises_without_actions(self) -> None:
        with self.assertRaises(ValueError):
            build_action_tool(name="empty", description="x", actions=())

    def test_rejects_reserved_action_property_name(self) -> None:
        bad_actions = (
            ActionSpec(
                action="list",
                legacy_name="list_widgets",
                properties={"action": {"type": "string"}},
            ),
        )
        with self.assertRaises(ValueError):
            build_action_tool(name="widget", description="x", actions=bad_actions)


class ResolveActionArgsTests(unittest.TestCase):
    def test_missing_action_is_error(self) -> None:
        result = resolve_action_args("widget", {}, _ACTIONS)
        self.assertIsInstance(result, ToolExecutionResult)
        assert isinstance(result, ToolExecutionResult)
        self.assertTrue(result.output.startswith("Error:"))
        self.assertIn("action is required", result.output)

    def test_blank_action_is_error(self) -> None:
        result = resolve_action_args("widget", {"action": "   "}, _ACTIONS)
        self.assertIsInstance(result, ToolExecutionResult)
        assert isinstance(result, ToolExecutionResult)
        self.assertIn("action is required", result.output)

    def test_unknown_action_is_error(self) -> None:
        result = resolve_action_args("widget", {"action": "delete"}, _ACTIONS)
        self.assertIsInstance(result, ToolExecutionResult)
        assert isinstance(result, ToolExecutionResult)
        self.assertTrue(result.output.startswith("Error:"))
        self.assertIn("unknown action", result.output)
        self.assertIn("delete", result.output)
        self.assertIn("list", result.output)
        self.assertIn("create", result.output)

    def test_missing_required_field_is_error(self) -> None:
        result = resolve_action_args("widget", {"action": "create"}, _ACTIONS)
        self.assertIsInstance(result, ToolExecutionResult)
        assert isinstance(result, ToolExecutionResult)
        self.assertTrue(result.output.startswith("Error:"))
        self.assertIn("create", result.output)
        self.assertIn("name", result.output)

    def test_happy_path_returns_legacy_name_and_remaining_args(self) -> None:
        resolved = resolve_action_args(
            "widget",
            {"action": "create", "name": "sprocket"},
            _ACTIONS,
        )
        self.assertNotIsInstance(resolved, ToolExecutionResult)
        legacy_name, remaining = resolved  # type: ignore[misc]
        self.assertEqual(legacy_name, "create_widget")
        self.assertEqual(remaining, {"name": "sprocket"})
        self.assertNotIn("action", remaining)

    def test_happy_path_no_required_fields(self) -> None:
        resolved = resolve_action_args("widget", {"action": "list"}, _ACTIONS)
        self.assertNotIsInstance(resolved, ToolExecutionResult)
        legacy_name, remaining = resolved  # type: ignore[misc]
        self.assertEqual(legacy_name, "list_widgets")
        self.assertEqual(remaining, {})


class ActionLegacyMapTests(unittest.TestCase):
    def test_returns_action_to_legacy_name_mapping(self) -> None:
        self.assertEqual(
            action_legacy_map(_ACTIONS),
            {"list": "list_widgets", "create": "create_widget"},
        )


class RegistryRoundTripTests(unittest.TestCase):
    """Round-trip a real consolidated tool (memory) through the registry."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_execute_tool_memory_action_list(self) -> None:
        result = execute_tool(
            self.memory_root,
            "memory",
            json.dumps({"action": "list"}),
            persona_namespace="buddy",
        )
        self.assertFalse(result.output.startswith("Error:"))
        payload = json.loads(result.output)
        self.assertIn("global", payload)
        self.assertIn("persona", payload)

    def test_execute_tool_memory_missing_action_errors(self) -> None:
        result = execute_tool(
            self.memory_root,
            "memory",
            json.dumps({}),
            persona_namespace="buddy",
        )
        self.assertTrue(result.output.startswith("Error:"))
        self.assertIn("action is required", result.output)


if __name__ == "__main__":
    unittest.main()
