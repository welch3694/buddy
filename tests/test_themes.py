"""Tests for buddy_tools.themes catalog and schema (#138)."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

import buddy_tools.themes.catalog as catalog_module
from buddy_tools.themes.catalog import (
    DEFAULT_THEME_ID,
    get_active_theme,
    get_active_theme_id,
    get_theme,
    list_themes,
    set_active_theme,
    set_themes_dir,
)
from buddy_tools.themes.schema import ThemeValidationError, parse_theme_dict


def _minimal_theme_yaml(theme_id: str = "default", name: str = "Default") -> str:
    return textwrap.dedent(
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
          mono: '"Share Tech Mono", ui-monospace, monospace'
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
          states:
            listening:
              core: "#1c5c68"
              glow: "rgba(61, 224, 200, 0.4)"
        """
    )


class ThemeSchemaTests(unittest.TestCase):
    def test_rejects_unknown_top_level_key(self) -> None:
        raw = {
            "id": "x",
            "name": "X",
            "palette": {k: "#000000" for k in (
                "void", "void_mid", "teal", "cyan", "ice", "amber", "muted", "danger"
            )},
            "fonts": {"display": "sans-serif", "mono": "monospace"},
            "orb": {
                "base": {
                    "core": "#111111",
                    "glow": "rgba(0,0,0,0.1)",
                    "ring": "rgba(0,0,0,0.2)",
                    "scale": 1,
                    "breathe_amp": 0.01,
                    "breathe_ms": 1000,
                    "bloom_opacity": 0.2,
                    "shimmer_opacity": 0,
                    "ring_spin_ms": 1000,
                    "saturate": 1,
                }
            },
            "css": "body { color: red }",
        }
        with self.assertRaises(ThemeValidationError):
            parse_theme_dict(raw)

    def test_rejects_url_in_color(self) -> None:
        with self.assertRaises(ThemeValidationError):
            parse_theme_dict(
                {
                    "id": "bad",
                    "name": "Bad",
                    "palette": {
                        "void": "url(evil)",
                        "void_mid": "#061428",
                        "teal": "#3de0c8",
                        "cyan": "#5ee7ff",
                        "ice": "#b8f4ff",
                        "amber": "#f0c14a",
                        "muted": "#4a6a7a",
                        "danger": "#7a8a92",
                    },
                    "fonts": {"display": "sans-serif", "mono": "monospace"},
                    "orb": {
                        "base": {
                            "core": "#111111",
                            "glow": "rgba(0,0,0,0.1)",
                            "ring": "rgba(0,0,0,0.2)",
                            "scale": 1,
                            "breathe_amp": 0.01,
                            "breathe_ms": 1000,
                            "bloom_opacity": 0.2,
                            "shimmer_opacity": 0,
                            "ring_spin_ms": 1000,
                            "saturate": 1,
                        }
                    },
                }
            )

    def test_to_css_tokens_maps_palette_and_orb(self) -> None:
        pack = parse_theme_dict(
            {
                "id": "sample",
                "name": "Sample",
                "palette": {
                    "void": "#020812",
                    "void_mid": "#061428",
                    "teal": "#3de0c8",
                    "cyan": "#5ee7ff",
                    "ice": "#b8f4ff",
                    "amber": "#f0c14a",
                    "muted": "#4a6a7a",
                    "danger": "#7a8a92",
                },
                "fonts": {
                    "display": '"Orbitron", sans-serif',
                    "mono": "monospace",
                },
                "orb": {
                    "base": {
                        "core": "#1a4a55",
                        "glow": "rgba(61, 224, 200, 0.35)",
                        "ring": "rgba(94, 231, 255, 0.55)",
                        "scale": 1,
                        "breathe_amp": 0.04,
                        "breathe_ms": 3200,
                        "bloom_opacity": 0.35,
                        "shimmer_opacity": 0,
                        "ring_spin_ms": 18000,
                        "saturate": 1,
                    },
                    "states": {"listening": {"core": "#1c5c68"}},
                },
            }
        )
        tokens = pack.to_css_tokens()
        self.assertEqual(tokens["--void"], "#020812")
        self.assertEqual(tokens["--void-mid"], "#061428")
        self.assertEqual(tokens["--font-display"], '"Orbitron", sans-serif')
        self.assertEqual(tokens["--orb-core"], "#1a4a55")
        self.assertEqual(tokens["--breathe-ms"], "3200ms")
        self.assertEqual(tokens["--orb-listening-core"], "#1c5c68")


class ThemeCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = catalog_module.get_themes_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.themes_root = Path(self._tmpdir.name)
        set_themes_dir(self.themes_root)

    def tearDown(self) -> None:
        set_themes_dir(self._original)
        self._tmpdir.cleanup()

    def _write_theme(self, theme_id: str, name: str | None = None) -> Path:
        theme_dir = self.themes_root / theme_id
        theme_dir.mkdir(parents=True)
        (theme_dir / "theme.yaml").write_text(
            _minimal_theme_yaml(theme_id, name or theme_id.title()),
            encoding="utf-8",
        )
        return theme_dir

    def test_list_themes_discovers_valid_packs(self) -> None:
        self._write_theme("default")
        self._write_theme("ember", "Ember")
        broken = self.themes_root / "broken"
        broken.mkdir()
        (broken / "theme.yaml").write_text("id: broken\n", encoding="utf-8")

        listed = list_themes()
        self.assertEqual(
            listed,
            [{"id": "default", "name": "Default"}, {"id": "ember", "name": "Ember"}],
        )

    def test_active_theme_persist_and_load(self) -> None:
        self._write_theme("default")
        self._write_theme("ember", "Ember")
        self.assertEqual(get_active_theme_id(), DEFAULT_THEME_ID)

        set_active_theme("ember")
        self.assertEqual(get_active_theme_id(), "ember")
        self.assertEqual(get_active_theme().name, "Ember")

    def test_get_theme_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            get_theme("missing")


class ProjectThemesTests(unittest.TestCase):
    def test_shipped_themes_parse(self) -> None:
        repo_themes = Path(__file__).resolve().parent.parent / "themes"
        for theme_id in ("default", "ember", "slate"):
            pack = get_theme(theme_id, repo_themes)
            self.assertEqual(pack.id, theme_id)
            tokens = pack.to_css_tokens()
            self.assertIn("--void", tokens)
            self.assertIn("--orb-core", tokens)


class ThemeSeedTests(unittest.TestCase):
    def test_seed_shipped_themes(self) -> None:
        from buddy_tools.infra.data_dir import (
            get_shipped_themes_dir,
            reset_data_dir_config,
            seed_shipped_themes,
        )

        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "themes"
            seeded = seed_shipped_themes(get_shipped_themes_dir(), user_dir)
            self.assertIn("default", seeded)
            self.assertIn("ember", seeded)
            self.assertIn("slate", seeded)
            # Second seed should skip existing valid packs
            again = seed_shipped_themes(get_shipped_themes_dir(), user_dir)
            self.assertEqual(again, [])
            reset_data_dir_config()


if __name__ == "__main__":
    unittest.main()
