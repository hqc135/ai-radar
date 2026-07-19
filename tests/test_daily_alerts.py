from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_daily_alerts import evaluate_alerts


class DailyAlertTests(unittest.TestCase):
    def write_summary(self, directory: Path, day: str, **overrides: object) -> None:
        summary = {
            "content_health": "ok",
            "quality_needs_review": False,
            "quality_score": 99,
            "llm_failed": 0,
        }
        summary.update(overrides)
        directory.joinpath(f"{day}.json").write_text(json.dumps(summary), encoding="utf-8")

    def test_single_low_quality_day_does_not_open_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_summary(directory, "2026-07-17")
            self.write_summary(directory, "2026-07-18", content_health="low")
            result = evaluate_alerts(directory)
            self.assertFalse(result.consecutive_low_quality)

    def test_two_consecutive_low_quality_days_open_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_summary(directory, "2026-07-17", content_health="low")
            self.write_summary(directory, "2026-07-18", quality_needs_review=True)
            result = evaluate_alerts(directory)
            self.assertTrue(result.consecutive_low_quality)

    def test_llm_failure_opens_high_priority_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_summary(directory, "2026-07-18", llm_failed=2)
            result = evaluate_alerts(directory)
            self.assertTrue(result.api_failure)


if __name__ == "__main__":
    unittest.main()
