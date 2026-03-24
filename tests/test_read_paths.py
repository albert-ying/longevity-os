from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_RESET = REPO_ROOT / "scripts" / "demo_reset.py"
WEEKLY_REPORT = REPO_ROOT / "scripts" / "weekly_report.py"
TRIAL_STATUS = REPO_ROOT / "scripts" / "trial_status.py"


class ReadPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name)
        self.env = os.environ.copy()
        self.env["LONGEVITY_OS_PROJECT_DIR"] = str(self.project_dir)
        subprocess.run(
            ["python3", str(DEMO_RESET)],
            cwd=str(REPO_ROOT),
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_weekly_report_reads_seeded_state_and_saves_file(self) -> None:
        proc = subprocess.run(
            ["python3", str(WEEKLY_REPORT), "--start", "2026-03-23", "--end", "2026-03-29"],
            cwd=str(REPO_ROOT),
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(proc.stdout)
        self.assertEqual(result["report_type"], "weekly")
        self.assertIn("Weekly Health Report", result["markdown"])
        self.assertTrue(Path(result["saved_to"]).exists())
        self.assertIn("diet", result["summary"]["data_completeness"])
        self.assertTrue(result["summary"]["key_trends"])
        self.assertTrue(result["summary"]["trial_progress"])

    def test_trial_status_reads_active_creatine_trial(self) -> None:
        proc = subprocess.run(
            ["python3", str(TRIAL_STATUS), "--all-active", "--as-of-date", "2026-03-19"],
            cwd=str(REPO_ROOT),
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(proc.stdout)
        self.assertEqual(len(result["trials"]), 1)
        trial = result["trials"][0]
        self.assertEqual(trial["name"], "Creatine-Cognition Trial")
        self.assertEqual(trial["status"], "active")
        self.assertEqual(trial["current_phase"], "intervention")
        self.assertGreater(trial["total_observations"], 0)
        self.assertIn("working_memory_score", trial["next_action"])


if __name__ == "__main__":
    unittest.main()
