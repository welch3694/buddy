"""Tests for buddy_tools.infra.logging_config."""

from __future__ import annotations

import logging
import unittest

from buddy_tools.infra.logging_config import quiet_http_client_loggers


class QuietHttpClientLoggersTests(unittest.TestCase):
    def setUp(self) -> None:
        self._httpx = logging.getLogger("httpx")
        self._httpcore = logging.getLogger("httpcore")
        self._prev_httpx = self._httpx.level
        self._prev_httpcore = self._httpcore.level

    def tearDown(self) -> None:
        self._httpx.setLevel(self._prev_httpx)
        self._httpcore.setLevel(self._prev_httpcore)

    def test_raises_httpx_and_httpcore_to_warning(self) -> None:
        self._httpx.setLevel(logging.NOTSET)
        self._httpcore.setLevel(logging.NOTSET)

        quiet_http_client_loggers()

        self.assertEqual(self._httpx.level, logging.WARNING)
        self.assertEqual(self._httpcore.level, logging.WARNING)

    def test_levels_survive_root_info(self) -> None:
        quiet_http_client_loggers()
        root = logging.getLogger()
        previous_root_level = root.level
        root.setLevel(logging.INFO)
        try:
            self.assertEqual(self._httpx.getEffectiveLevel(), logging.WARNING)
            self.assertEqual(self._httpcore.getEffectiveLevel(), logging.WARNING)
            self.assertFalse(self._httpx.isEnabledFor(logging.INFO))
            self.assertTrue(self._httpx.isEnabledFor(logging.WARNING))
        finally:
            root.setLevel(previous_root_level)


if __name__ == "__main__":
    unittest.main()
