from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_MEAL = REPO_ROOT / "scripts" / "log_meal.py"
LOG_METRICS = REPO_ROOT / "scripts" / "log_metrics.py"


class WritePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name)
        self.env = os.environ.copy()
        self.env["LONGEVITY_OS_PROJECT_DIR"] = str(self.project_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _db_path(self) -> Path:
        return self.project_dir / "data" / "taiyiyuan.db"

    def _run(self, script: Path, payload: dict) -> dict:
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

    def test_log_meal_writes_entry_and_ingredients(self) -> None:
        payload = {
            "timestamp": "2026-03-12T12:30:00+00:00",
            "meal_type": "lunch",
            "description": "Chicken rice bowl",
            "confidence_score": 0.8,
            "notes": "Estimated from user description",
            "ingredients": [
                {
                    "ingredient_name": "chicken breast",
                    "normalized_name": "chicken breast",
                    "amount_g": 150,
                    "calories": 248,
                    "protein_g": 46.5,
                    "carbs_g": 0,
                    "fat_g": 5.4,
                    "fiber_g": 0,
                },
                {
                    "ingredient_name": "brown rice",
                    "normalized_name": "brown rice",
                    "amount_g": 200,
                    "calories": 216,
                    "protein_g": 5.0,
                    "carbs_g": 45.0,
                    "fat_g": 1.8,
                    "fiber_g": 3.5,
                },
            ],
        }

        result = self._run(LOG_MEAL, payload)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rows_written"]["diet_entries"], 1)
        self.assertEqual(result["rows_written"]["diet_ingredients"], 2)
        self.assertAlmostEqual(result["totals"]["calories"], 464.0)
        self.assertAlmostEqual(result["totals"]["protein_g"], 51.5)

        conn = sqlite3.connect(self._db_path())
        entry = conn.execute(
            "SELECT meal_type, description, total_calories, total_protein_g FROM diet_entries"
        ).fetchone()
        ingredients = conn.execute(
            "SELECT COUNT(*) FROM diet_ingredients"
        ).fetchone()[0]
        conn.close()

        self.assertEqual(entry[0], "lunch")
        self.assertEqual(entry[1], "Chicken rice bowl")
        self.assertAlmostEqual(entry[2], 464.0)
        self.assertAlmostEqual(entry[3], 51.5)
        self.assertEqual(ingredients, 2)

    def test_log_metrics_writes_multiple_rows(self) -> None:
        payload = {
            "timestamp": "2026-03-12T07:00:00+00:00",
            "entries": [
                {
                    "metric_type": "weight",
                    "value": 72.5,
                    "unit": "kg",
                    "context": "morning fasted",
                },
                {
                    "metric_type": "blood_pressure_sys",
                    "value": 118,
                    "unit": "mmHg",
                    "context": "resting",
                },
                {
                    "metric_type": "blood_pressure_dia",
                    "value": 76,
                    "unit": "mmHg",
                    "context": "resting",
                },
                {
                    "metric_type": "resting_hr",
                    "value": 56,
                    "unit": "bpm",
                    "context": "resting",
                },
            ]
        }

        result = self._run(LOG_METRICS, payload)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["entries_created"], 4)

        conn = sqlite3.connect(self._db_path())
        rows = conn.execute(
            "SELECT metric_type, value, unit, context FROM body_metrics ORDER BY id"
        ).fetchall()
        conn.close()

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], ("weight", 72.5, "kg", "morning fasted"))
        self.assertEqual(rows[1], ("blood_pressure_sys", 118.0, "mmHg", "resting"))
        self.assertEqual(rows[2], ("blood_pressure_dia", 76.0, "mmHg", "resting"))
        self.assertEqual(rows[3], ("resting_hr", 56.0, "bpm", "resting"))


if __name__ == "__main__":
    unittest.main()
