"""Tests for episodic relative date resolution."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from buddy_tools.episodic.dates import (
    extract_relative_date_from_query,
    resolve_episodic_date,
)


class EpisodicDateResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = ZoneInfo("America/New_York")
        self.now = datetime(2026, 7, 8, 16, 0, 0, tzinfo=UTC)

    def test_absolute_date_passthrough(self) -> None:
        self.assertEqual(
            resolve_episodic_date("2026-07-05", now=self.now, tz=self.tz),
            "2026-07-05",
        )

    def test_yesterday_uses_episodic_timezone(self) -> None:
        self.assertEqual(
            resolve_episodic_date("yesterday", now=self.now, tz=self.tz),
            "2026-07-07",
        )

    def test_today_uses_episodic_timezone(self) -> None:
        self.assertEqual(
            resolve_episodic_date("today", now=self.now, tz=self.tz),
            "2026-07-08",
        )

    def test_day_before_yesterday(self) -> None:
        self.assertEqual(
            resolve_episodic_date("day before yesterday", now=self.now, tz=self.tz),
            "2026-07-06",
        )

    def test_n_days_ago(self) -> None:
        self.assertEqual(
            resolve_episodic_date("3 days ago", now=self.now, tz=self.tz),
            "2026-07-05",
        )

    def test_extract_relative_date_from_query(self) -> None:
        self.assertEqual(
            extract_relative_date_from_query(
                "What did we talk about yesterday?",
                now=self.now,
                tz=self.tz,
            ),
            "2026-07-07",
        )

    def test_unknown_relative_term_returns_none(self) -> None:
        self.assertIsNone(resolve_episodic_date("last tuesday", now=self.now, tz=self.tz))


if __name__ == "__main__":
    unittest.main()
