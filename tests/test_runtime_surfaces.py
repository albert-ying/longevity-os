from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_EXERCISE = REPO_ROOT / "scripts" / "log_exercise.py"
LOG_BIOMARKERS = REPO_ROOT / "scripts" / "log_biomarkers.py"
MANAGE_SUPPLEMENTS = REPO_ROOT / "scripts" / "manage_supplements.py"
QUERY_SQLITE = REPO_ROOT / "scripts" / "query_sqlite.py"


class RuntimeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name)
        self.env = os.environ.copy()
        self.env["LONGEVITY_OS_PROJECT_DIR"] = str(self.project_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _db_path(self) -> Path:
        return self.project_dir / "data" / "taiyiyuan.db"

    def _run_json(self, script: Path, payload: dict) -> dict:
        proc = subprocess.run(
            ["python3", str(script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.env,
            cwd=str(REPO_ROOT),
            check=True,
        )
        return json.loads(proc.stdout)

    def test_log_exercise_writes_session_and_details(self) -> None:
        payload = {
            "timestamp": "2026-03-12T18:00:00+00:00",
            "activity_type": "strength",
            "duration_minutes": 55,
            "avg_hr": 132,
            "rpe": 7,
            "details": [
                {"exercise_name": "barbell squat", "sets": 4, "reps": 8, "weight_kg": 100},
                {"exercise_name": "bench press", "sets": 3, "reps": 10, "weight_kg": 80},
            ],
        }

        result = self._run_json(LOG_EXERCISE, payload)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rows_written"]["exercise_entries"], 1)
        self.assertEqual(result["rows_written"]["exercise_details"], 2)

        conn = sqlite3.connect(self._db_path())
        entry = conn.execute(
            "SELECT activity_type, duration_minutes, avg_hr, rpe FROM exercise_entries"
        ).fetchone()
        detail_count = conn.execute("SELECT COUNT(*) FROM exercise_details").fetchone()[0]
        conn.close()

        self.assertEqual(entry, ("strength", 55.0, 132.0, 7))
        self.assertEqual(detail_count, 2)

    def test_log_biomarkers_writes_batch(self) -> None:
        payload = {
            "timestamp": "2026-03-12T08:00:00+00:00",
            "lab_source": "Quest",
            "entries": [
                {
                    "panel_name": "Lipid Panel",
                    "marker_name": "LDL",
                    "value": 112,
                    "unit": "mg/dL",
                    "reference_low": 0,
                    "reference_high": 130,
                    "optimal_low": 0,
                    "optimal_high": 100,
                },
                {
                    "panel_name": "Metabolic",
                    "marker_name": "HbA1c",
                    "value": 5.1,
                    "unit": "%",
                    "reference_low": 0,
                    "reference_high": 5.7,
                    "optimal_low": 0,
                    "optimal_high": 5.3,
                },
            ],
        }

        result = self._run_json(LOG_BIOMARKERS, payload)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["entries_created"], 2)

        conn = sqlite3.connect(self._db_path())
        rows = conn.execute(
            "SELECT marker_name, value, unit, lab_source FROM biomarkers ORDER BY id"
        ).fetchall()
        conn.close()

        self.assertEqual(rows[0], ("LDL", 112.0, "mg/dL", "Quest"))
        self.assertEqual(rows[1], ("HbA1c", 5.1, "%", "Quest"))

    def test_manage_supplements_add_update_stop_and_query(self) -> None:
        added = self._run_json(
            MANAGE_SUPPLEMENTS,
            {
                "action": "add",
                "compound_name": "Creatine monohydrate",
                "dosage": 5,
                "dosage_unit": "g",
                "frequency": "daily",
                "timing": "morning",
                "start_date": "2026-03-12",
                "reason": "cognition",
            },
        )
        self.assertEqual(added["action"], "added")
        supplement_id = added["supplement"]["id"]

        updated = self._run_json(
            MANAGE_SUPPLEMENTS,
            {
                "action": "update",
                "supplement_id": supplement_id,
                "updates": {"timing": "post-workout"},
            },
        )
        self.assertEqual(updated["action"], "updated")
        self.assertEqual(updated["supplement"]["timing"], "post-workout")

        proc = subprocess.run(
            [
                "python3",
                str(QUERY_SQLITE),
                "--sql",
                "SELECT compound_name, timing FROM supplements WHERE end_date IS NULL ORDER BY id",
            ],
            text=True,
            capture_output=True,
            env=self.env,
            cwd=str(REPO_ROOT),
            check=True,
        )
        query_result = json.loads(proc.stdout)
        self.assertEqual(query_result["row_count"], 1)
        self.assertEqual(query_result["rows"][0]["timing"], "post-workout")

        stopped = self._run_json(
            MANAGE_SUPPLEMENTS,
            {
                "action": "stop",
                "supplement_id": supplement_id,
                "end_date": "2026-03-19",
            },
        )
        self.assertEqual(stopped["action"], "stopped")
        self.assertEqual(stopped["supplement"]["end_date"], "2026-03-19")
        self.assertEqual(stopped["stack_overview"], [])


if __name__ == "__main__":
    unittest.main()
