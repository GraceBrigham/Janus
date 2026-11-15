"""Regression test: tool data must not leak between harness runs."""

import os
import unittest

os.environ.setdefault("OPENAI_API_KEY", "sk-offline-test-not-used")

from src.tools import calendar_tools as ct
from src.tools.data_store import reset_tool_state, set_active_data_set, set_inline_data_set


def _event(title):
    return {"id": 1, "title": title, "date": "2024-01-01", "time": "09:00",
            "description": "", "participants": [], "duration_minutes": 30}


class ToolStateTest(unittest.TestCase):
    def _start_run(self, name, title):
        set_inline_data_set(name, {"calendar_events": [_event(title)]})
        set_active_data_set(name)
        reset_tool_state()

    def test_each_run_sees_only_its_own_data(self):
        self._start_run("leak_test_A", "from A")
        self.assertIn("from A", ct.list_calendar_events("2024-01-01"))
        ct.create_calendar_event("made in run 1", "2024-01-01", "15:00")

        self._start_run("leak_test_B", "from B")
        listing = ct.list_calendar_events("2024-01-01")
        self.assertIn("from B", listing)
        self.assertNotIn("from A", listing)
        self.assertNotIn("made in run 1", listing)

    def test_same_data_set_rerun_starts_clean(self):
        self._start_run("leak_test_C", "seed")
        ct.create_calendar_event("made in rep 1", "2024-01-01", "15:00")
        self._start_run("leak_test_C", "seed")
        self.assertNotIn("made in rep 1", ct.list_calendar_events("2024-01-01"))


if __name__ == "__main__":
    unittest.main()
