import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_pulse.py"
SPEC = importlib.util.spec_from_file_location("update_pulse", SCRIPT)
assert SPEC and SPEC.loader
PULSE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PULSE
SPEC.loader.exec_module(PULSE)


class PulseTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 8, 31)
        self.start = self.today - timedelta(days=364)

    def fixture_calendar(self, active_offsets=()):
        active = set(active_offsets)
        days = [
            {
                "date": (self.start + timedelta(days=offset)).isoformat(),
                "contributionCount": 2 if offset in active else 0,
            }
            for offset in range(365)
        ]
        return {"weeks": [{"contributionDays": days}]}

    def test_query_contains_only_rendered_contribution_fields(self):
        self.assertNotIn("repositoriesContributedTo", PULSE.QUERY)
        self.assertNotIn("followers", PULSE.QUERY)
        self.assertIn("totalPullRequestContributions", PULSE.QUERY)
        self.assertIn("contributionCalendar", PULSE.QUERY)

    def test_window_is_exactly_365_days(self):
        days = PULSE.contribution_days(self.fixture_calendar(), self.start, self.today)
        self.assertEqual(365, len(days))
        self.assertEqual(self.start.isoformat(), days[0]["date"])
        self.assertEqual(self.today.isoformat(), days[-1]["date"])

    def test_current_streak_allows_unfinished_today(self):
        days = PULSE.contribution_days(
            self.fixture_calendar(range(360, 364)), self.start, self.today
        )
        current, longest, active = PULSE.streaks(days, self.today)
        self.assertEqual((4, 4, 4), (current, longest, active))

    def test_svg_is_valid_and_ends_on_today(self):
        days = PULSE.contribution_days(
            self.fixture_calendar((0, 363, 364)), self.start, self.today
        )
        now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        svg = PULSE.render_contribution_svg(days, now)
        ET.fromstring(svg)
        self.assertIn("2026-08-31", svg)
        self.assertIn("6 contributions · last 365 days", svg)
        self.assertEqual(366, svg.count("<title>"))  # one SVG title + 365 cell tooltips

    def test_marker_replacement_rejects_duplicates(self):
        source = f"before\n{PULSE.START_MARKER}\nold\n{PULSE.END_MARKER}\nafter"
        updated = PULSE.replace_block(source, "new")
        self.assertEqual("before\nnew\nafter", updated)
        with self.assertRaises(ValueError):
            PULSE.replace_block(source + PULSE.START_MARKER, "new")


if __name__ == "__main__":
    unittest.main()
