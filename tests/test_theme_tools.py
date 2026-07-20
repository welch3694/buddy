"""Tests for list_themes / switch_theme tools (#138)."""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

import buddy_tools.themes.catalog as catalog_module
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.themes.catalog import set_themes_dir
from buddy_tools.themes.tools import THEME_TOOL_NAMES, execute_theme_tool


def _write_theme(root: Path, theme_id: str, name: str) -> None:
    theme_dir = root / theme_id
    theme_dir.mkdir(parents=True)
    (theme_dir / "theme.yaml").write_text(
        textwrap.dedent(
            f"""\
            id: {theme_id}
            name: {name}
            palette:
              void: "#020812"
              void_mid: "#061428"
              teal: "#3de0c8"
              cyan: "#5ee7ff"
              ice: "#b8f4ff"
              amber: "#f0c14a"
              muted: "#4a6a7a"
              danger: "#7a8a92"
            fonts:
              display: '"Orbitron", sans-serif'
              mono: monospace
            orb:
              base:
                core: "#1a4a55"
                glow: "rgba(61, 224, 200, 0.35)"
                ring: "rgba(94, 231, 255, 0.55)"
                scale: 1
                breathe_amp: 0.04
                breathe_ms: 3200
                bloom_opacity: 0.35
                shimmer_opacity: 0
                ring_spin_ms: 18000
                saturate: 1
            """
        ),
        encoding="utf-8",
    )


class ThemeToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = catalog_module.get_themes_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.themes_root = Path(self._tmpdir.name)
        set_themes_dir(self.themes_root)
        _write_theme(self.themes_root, "default", "Default")
        _write_theme(self.themes_root, "ember", "Ember")

    def tearDown(self) -> None:
        set_themes_dir(self._original)
        self._tmpdir.cleanup()

    def test_tool_names(self) -> None:
        self.assertEqual(THEME_TOOL_NAMES, frozenset({"list_themes", "switch_theme"}))

    def test_list_themes(self) -> None:
        result = execute_theme_tool("list_themes", {})
        payload = json.loads(result.output)
        self.assertEqual(payload["active"], "default")
        ids = [entry["id"] for entry in payload["themes"]]
        self.assertEqual(ids, ["default", "ember"])

    def test_switch_theme_returns_switch_id(self) -> None:
        result = execute_theme_tool("switch_theme", {"theme_id": "ember"})
        self.assertIsInstance(result, ToolExecutionResult)
        self.assertEqual(result.theme_switch_id, "ember")
        self.assertIn("Ember", result.output)

    def test_switch_theme_unknown(self) -> None:
        result = execute_theme_tool("switch_theme", {"theme_id": "missing"})
        self.assertTrue(result.output.startswith("Error:"))
        self.assertIsNone(result.theme_switch_id)

    def test_registry_dispatches_theme_tools(self) -> None:
        from buddy_tools.core.registry import execute_tool

        with tempfile.TemporaryDirectory() as mem:
            result = execute_tool(Path(mem), "list_themes", "{}", persona_namespace="buddy")
            payload = json.loads(result.output)
            self.assertIn("themes", payload)

    def test_apply_theme_emits_event(self) -> None:
        from buddy_tools.companion.publisher import (
            CompanionEventPublisher,
            reset_companion_publisher_for_tests,
            set_companion_publisher,
        )
        from buddy_tools.themes.session import apply_theme

        publisher = CompanionEventPublisher()
        set_companion_publisher(publisher)
        try:
            pack = apply_theme("ember")
            self.assertEqual(pack.id, "ember")
            events = publisher.drain()
            theme_events = [e for e in events if e["type"] == "theme"]
            self.assertEqual(len(theme_events), 1)
            self.assertEqual(theme_events[0]["id"], "ember")
            self.assertIn("--void", theme_events[0]["tokens"])
        finally:
            reset_companion_publisher_for_tests()


if __name__ == "__main__":
    unittest.main()
