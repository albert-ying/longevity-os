"""
TaiYiYuan (太医院) — Database Interface

SQLite-backed data layer for personal longevity tracking.
All timestamps stored as UTC ISO 8601 with timezone offset.
Uses only stdlib: sqlite3, os, json, datetime.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional


# Default database location
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path


DEFAULT_DB_PATH = str(get_db_path())

# Schema file lives alongside this module
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class TaiYiYuanDB:
    """SQLite database interface for TaiYiYuan health tracking.

    Usage:
        with TaiYiYuanDB() as db:
            db.log_metric("2026-03-12T08:00:00+00:00", "weight", 72.5, "kg")
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # -----------------------------------------------------------------------
    # Connection management
    # -----------------------------------------------------------------------

    def _connect(self):
        """Open connection and ensure schema exists."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        new_db = not os.path.exists(self.db_path)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

        if new_db:
            self._ensure_schema()
            # Restrict file permissions — sensitive health data
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass  # May fail on some filesystems

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def _ensure_schema(self):
        """Create all tables from schema.sql if they don't exist."""
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        self.conn.executescript(schema_sql)

        # Seed initial schema version if not present
        existing = self._execute(
            "SELECT version FROM schema_version WHERE version = 1", ()
        ).fetchone()
        if not existing:
            self._execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (1, self._now()),
            )
            self.conn.commit()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        """Return current UTC time as ISO 8601 with timezone offset."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_date_range(start_date: str, end_date: str) -> tuple[str, str]:
        """Normalize date-only strings to full timestamp bounds.

        If start_date looks like a bare date (YYYY-MM-DD), append T00:00:00.
        If end_date looks like a bare date, append T23:59:59.999999 so that
        all timestamps on that day are included.
        """
        if start_date and len(start_date) == 10:
            start_date = start_date + "T00:00:00"
        if end_date and len(end_date) == 10:
            end_date = end_date + "T23:59:59.999999"
        return start_date, end_date

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a parameterized query with error handling."""
        if self.conn is None:
            raise RuntimeError("Database connection is not open. Use 'with' context manager.")
        try:
            return self.conn.execute(sql, params)
        except sqlite3.Error as e:
            raise RuntimeError(f"Database error executing: {sql[:120]}... — {e}") from e

    def _executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """Execute parameterized query for multiple rows."""
        if self.conn is None:
            raise RuntimeError("Database connection is not open.")
        try:
            return self.conn.executemany(sql, params_list)
        except sqlite3.Error as e:
            raise RuntimeError(f"Database error in executemany: {sql[:120]}... — {e}") from e

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a plain dict."""
        return dict(row)

    def _rows_to_dicts(self, rows: list) -> list[dict]:
        """Convert a list of sqlite3.Row to list of dicts."""
        return [self._row_to_dict(r) for r in rows]

    # ===================================================================
    # DIET MODULE
    # ===================================================================

    def log_meal(
        self,
        timestamp: str,
        meal_type: str,
        description: str,
        ingredients: list[dict],
        confidence_score: float = None,
        photo_path: str = None,
        notes: str = None,
    ) -> int:
        """Log a meal with its ingredients.

        Args:
            timestamp: ISO 8601 UTC timestamp of the meal.
            meal_type: One of 'breakfast', 'lunch', 'dinner', 'snack'.
            description: Free-text meal description.
            ingredients: List of dicts, each with keys matching diet_ingredients columns.
                Required: 'ingredient_name'. Optional: 'normalized_name', 'amount_g',
                'calories', 'protein_g', 'carbs_g', 'fat_g', 'fiber_g', and all
                vitamin/mineral columns.
            confidence_score: 0-1 confidence in nutrition estimates.
            photo_path: Path to meal photo.
            notes: Additional notes.

        Returns:
            The diet_entries row id.
        """
        now = self._now()

        # Compute totals from ingredients
        total_cal = sum(i.get("calories", 0) or 0 for i in ingredients)
        total_pro = sum(i.get("protein_g", 0) or 0 for i in ingredients)
        total_carb = sum(i.get("carbs_g", 0) or 0 for i in ingredients)
        total_fat = sum(i.get("fat_g", 0) or 0 for i in ingredients)
        total_fiber = sum(i.get("fiber_g", 0) or 0 for i in ingredients)

        cursor = self._execute(
            """INSERT INTO diet_entries
               (timestamp, meal_type, description, total_calories, total_protein_g,
                total_carbs_g, total_fat_g, total_fiber_g, photo_path,
                confidence_score, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp, meal_type, description,
                total_cal, total_pro, total_carb, total_fat, total_fiber,
                photo_path, confidence_score, notes, now, now,
            ),
        )
        entry_id = cursor.lastrowid

        # Insert each ingredient
        ingredient_cols = [
            "ingredient_name", "normalized_name", "amount_g", "calories",
            "protein_g", "carbs_g", "fat_g", "fiber_g",
            "vitamin_a_mcg", "vitamin_b1_mg", "vitamin_b2_mg", "vitamin_b3_mg",
            "vitamin_b5_mg", "vitamin_b6_mg", "vitamin_b7_mcg", "vitamin_b9_mcg",
            "vitamin_b12_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_e_mg",
            "vitamin_k_mcg", "calcium_mg", "iron_mg", "magnesium_mg",
            "zinc_mg", "potassium_mg", "sodium_mg",
        ]
        placeholders = ", ".join(["?"] * (len(ingredient_cols) + 2))  # +entry_id, +created_at
        col_names = ", ".join(["entry_id"] + ingredient_cols + ["created_at"])

        for ing in ingredients:
            values = [entry_id] + [ing.get(c) for c in ingredient_cols] + [now]
            self._execute(
                f"INSERT INTO diet_ingredients ({col_names}) VALUES ({placeholders})",
                tuple(values),
            )

        self.conn.commit()
        return entry_id

    def get_meals(self, start_date: str, end_date: str) -> list[dict]:
        """Get diet entries with their ingredients in a date range.

        Args:
            start_date: ISO 8601 start (inclusive).
            end_date: ISO 8601 end (inclusive).

        Returns:
            List of diet entry dicts, each with an 'ingredients' key.
        """
        start_date, end_date = self._normalize_date_range(start_date, end_date)
        rows = self._execute(
            "SELECT * FROM diet_entries WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_date, end_date),
        ).fetchall()

        results = []
        for row in rows:
            entry = self._row_to_dict(row)
            ingredients = self._execute(
                "SELECT * FROM diet_ingredients WHERE entry_id = ?",
                (entry["id"],),
            ).fetchall()
            entry["ingredients"] = self._rows_to_dicts(ingredients)
            results.append(entry)
        return results

    # ===================================================================
    # RECIPE LIBRARY
    # ===================================================================

    def save_recipe(
        self,
        name: str,
        description: str,
        ingredients_json: str,
        nutrition_json: str,
    ) -> int:
        """Upsert a recipe into the library.

        Args:
            name: Recipe name (unique key for upsert).
            description: Recipe description.
            ingredients_json: JSON string of ingredients list.
            nutrition_json: JSON string of total nutrition.

        Returns:
            The recipe_library row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO recipe_library
               (name, description, ingredients_json, total_nutrition_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   description = excluded.description,
                   ingredients_json = excluded.ingredients_json,
                   total_nutrition_json = excluded.total_nutrition_json,
                   updated_at = excluded.updated_at""",
            (name, description, ingredients_json, nutrition_json, now, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def find_recipe(self, name: str) -> list[dict]:
        """Fuzzy search the recipe library by name.

        Args:
            name: Search term (uses LIKE matching).

        Returns:
            List of matching recipe dicts.
        """
        rows = self._execute(
            "SELECT * FROM recipe_library WHERE name LIKE ? ORDER BY times_logged DESC",
            (f"%{name}%",),
        ).fetchall()
        return self._rows_to_dicts(rows)

    def get_recipe(self, recipe_id: int) -> Optional[dict]:
        """Get a single recipe by id."""
        row = self._execute(
            "SELECT * FROM recipe_library WHERE id = ?", (recipe_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    # ===================================================================
    # EXERCISE MODULE
    # ===================================================================

    def log_exercise(
        self,
        timestamp: str,
        activity_type: str,
        duration_minutes: float,
        details: list[dict] = None,
        distance_km: float = None,
        avg_hr: float = None,
        rpe: int = None,
        notes: str = None,
    ) -> int:
        """Log an exercise session with optional per-exercise details.

        Args:
            timestamp: ISO 8601 UTC timestamp.
            activity_type: e.g., 'running', 'weightlifting'.
            duration_minutes: Total duration in minutes.
            details: List of dicts with keys: 'exercise_name', 'sets', 'reps',
                     'weight_kg', 'duration_seconds', 'notes'.
            distance_km: Distance covered (for cardio).
            avg_hr: Average heart rate.
            rpe: Rate of perceived exertion (1-10).
            notes: Additional notes.

        Returns:
            The exercise_entries row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO exercise_entries
               (timestamp, activity_type, duration_minutes, distance_km,
                avg_hr, rpe, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, activity_type, duration_minutes, distance_km,
             avg_hr, rpe, notes, now, now),
        )
        entry_id = cursor.lastrowid

        if details:
            for d in details:
                self._execute(
                    """INSERT INTO exercise_details
                       (entry_id, exercise_name, sets, reps, weight_kg,
                        duration_seconds, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry_id,
                        d.get("exercise_name", "unknown"),
                        d.get("sets"),
                        d.get("reps"),
                        d.get("weight_kg"),
                        d.get("duration_seconds"),
                        d.get("notes"),
                    ),
                )

        self.conn.commit()
        return entry_id

    def get_exercises(self, start_date: str, end_date: str) -> list[dict]:
        """Get exercise entries with details in a date range."""
        start_date, end_date = self._normalize_date_range(start_date, end_date)
        rows = self._execute(
            "SELECT * FROM exercise_entries WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_date, end_date),
        ).fetchall()

        results = []
        for row in rows:
            entry = self._row_to_dict(row)
            details = self._execute(
                "SELECT * FROM exercise_details WHERE entry_id = ?",
                (entry["id"],),
            ).fetchall()
            entry["details"] = self._rows_to_dicts(details)
            results.append(entry)
        return results

    # ===================================================================
    # BODY METRICS MODULE
    # ===================================================================

    def log_metric(
        self,
        timestamp: str,
        metric_type: str,
        value: float,
        unit: str,
        context: str = None,
        device_method: str = None,
        notes: str = None,
    ) -> int:
        """Log a body metric measurement.

        Args:
            timestamp: ISO 8601 UTC timestamp.
            metric_type: e.g., 'weight', 'body_fat', 'resting_hr'.
            value: Numeric value.
            unit: e.g., 'kg', '%', 'bpm'.
            context: e.g., 'morning fasted'.
            device_method: e.g., 'Withings scale'.
            notes: Additional notes.

        Returns:
            The body_metrics row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO body_metrics
               (timestamp, metric_type, value, unit, context, device_method, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, metric_type, value, unit, context, device_method, notes, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_metrics(
        self,
        metric_type: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Get body metrics of a specific type in a date range."""
        start_date, end_date = self._normalize_date_range(start_date, end_date)
        rows = self._execute(
            """SELECT * FROM body_metrics
               WHERE metric_type = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp""",
            (metric_type, start_date, end_date),
        ).fetchall()
        return self._rows_to_dicts(rows)

    def get_metric_series(self, metric_type: str, days: int = 30) -> list[dict]:
        """Get a time series of a metric type for the last N days.

        Args:
            metric_type: The metric to query.
            days: Number of days to look back (default 30).

        Returns:
            List of metric dicts ordered by timestamp.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._execute(
            """SELECT * FROM body_metrics
               WHERE metric_type = ? AND timestamp >= ?
               ORDER BY timestamp""",
            (metric_type, cutoff),
        ).fetchall()
        return self._rows_to_dicts(rows)

    def define_custom_metric(
        self,
        name: str,
        unit: str,
        metric_type: str,
        valid_min: float = None,
        valid_max: float = None,
        description: str = None,
    ) -> int:
        """Define a custom metric type.

        Args:
            name: Unique metric name.
            unit: Unit of measurement.
            metric_type: One of 'continuous', 'categorical', 'ordinal'.
            valid_min: Minimum valid value.
            valid_max: Maximum valid value.
            description: Description of the metric.

        Returns:
            The custom_metric_definitions row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO custom_metric_definitions
               (name, unit, metric_type, valid_min, valid_max, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, unit, metric_type, valid_min, valid_max, description, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_custom_metric_definitions(self) -> list[dict]:
        """Get all custom metric definitions."""
        rows = self._execute(
            "SELECT * FROM custom_metric_definitions ORDER BY name"
        ).fetchall()
        return self._rows_to_dicts(rows)

    # ===================================================================
    # BIOMARKERS MODULE
    # ===================================================================

    def log_biomarker(
        self,
        timestamp: str,
        panel_name: str,
        marker_name: str,
        value: float,
        unit: str,
        reference_low: float = None,
        reference_high: float = None,
        optimal_low: float = None,
        optimal_high: float = None,
        notes: str = None,
        lab_source: str = None,
    ) -> int:
        """Log a biomarker result.

        Args:
            timestamp: ISO 8601 UTC timestamp of blood draw / test.
            panel_name: e.g., 'CMP', 'CBC'.
            marker_name: e.g., 'HbA1c', 'LDL'.
            value: Numeric result.
            unit: e.g., 'mg/dL', '%'.
            reference_low: Lab reference range low.
            reference_high: Lab reference range high.
            optimal_low: Longevity-optimal range low.
            optimal_high: Longevity-optimal range high.
            notes: Additional notes.
            lab_source: e.g., 'Quest Diagnostics'.

        Returns:
            The biomarkers row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO biomarkers
               (timestamp, panel_name, marker_name, value, unit,
                reference_low, reference_high, optimal_low, optimal_high,
                notes, lab_source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp, panel_name, marker_name, value, unit,
                reference_low, reference_high, optimal_low, optimal_high,
                notes, lab_source, now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_biomarkers(
        self,
        marker_name: str = None,
        start_date: str = None,
        end_date: str = None,
    ) -> list[dict]:
        """Get biomarker results, optionally filtered.

        Args:
            marker_name: Filter by marker name (exact match).
            start_date: ISO 8601 start (inclusive).
            end_date: ISO 8601 end (inclusive).

        Returns:
            List of biomarker dicts.
        """
        if start_date or end_date:
            start_date, end_date = self._normalize_date_range(
                start_date or "", end_date or ""
            )
        conditions = []
        params = []

        if marker_name:
            conditions.append("marker_name = ?")
            params.append(marker_name)
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._execute(
            f"SELECT * FROM biomarkers{where} ORDER BY timestamp",
            tuple(params),
        ).fetchall()
        return self._rows_to_dicts(rows)

    # ===================================================================
    # SUPPLEMENTS MODULE
    # ===================================================================

    def log_supplement(
        self,
        compound_name: str,
        dosage: float,
        dosage_unit: str,
        frequency: str,
        timing: str,
        start_date: str,
        reason: str = None,
        brand: str = None,
    ) -> int:
        """Log a new supplement.

        Args:
            compound_name: e.g., 'Vitamin D3'.
            dosage: Numeric dosage.
            dosage_unit: e.g., 'IU', 'mg'.
            frequency: e.g., 'daily', 'twice daily'.
            timing: e.g., 'morning with food'.
            start_date: ISO 8601 date when started.
            reason: Why taking this supplement.
            brand: Brand name.

        Returns:
            The supplements row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO supplements
               (compound_name, dosage, dosage_unit, frequency, timing,
                start_date, reason, brand, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (compound_name, dosage, dosage_unit, frequency, timing,
             start_date, reason, brand, now, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def update_supplement(self, supplement_id: int, **kwargs) -> None:
        """Update a supplement entry.

        Args:
            supplement_id: Row id to update.
            **kwargs: Column-value pairs to update. Valid columns:
                compound_name, dosage, dosage_unit, frequency, timing,
                start_date, end_date, reason, brand, cost_per_unit, notes.
        """
        valid_cols = {
            "compound_name", "dosage", "dosage_unit", "frequency", "timing",
            "start_date", "end_date", "reason", "brand", "cost_per_unit", "notes",
        }
        updates = {k: v for k, v in kwargs.items() if k in valid_cols}
        if not updates:
            return

        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [supplement_id]

        self._execute(
            f"UPDATE supplements SET {set_clause} WHERE id = ?",
            tuple(params),
        )
        self.conn.commit()

    def stop_supplement(self, supplement_id: int, end_date: str) -> None:
        """Stop a supplement by setting its end date.

        Args:
            supplement_id: Row id.
            end_date: ISO 8601 date when stopped.
        """
        self.update_supplement(supplement_id, end_date=end_date)

    def get_supplements(self, active_only: bool = True) -> list[dict]:
        """Get supplements.

        Args:
            active_only: If True (default), only return supplements with no end_date.

        Returns:
            List of supplement dicts.
        """
        if active_only:
            rows = self._execute(
                "SELECT * FROM supplements WHERE end_date IS NULL ORDER BY compound_name"
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM supplements ORDER BY compound_name, start_date"
            ).fetchall()
        return self._rows_to_dicts(rows)

    # ===================================================================
    # N-OF-1 TRIALS MODULE
    # ===================================================================

    def create_trial(
        self,
        name: str,
        hypothesis: str,
        intervention: str,
        primary_outcome: str,
        design: str,
        phase_duration: int,
        washout_duration: int,
        min_obs: int,
        literature_evidence: str,
        secondary_outcomes: str = None,
    ) -> int:
        """Create a new trial with status='proposed'.

        Args:
            name: Trial name.
            hypothesis: What you expect to happen.
            intervention: What you're testing.
            primary_outcome: Primary metric to track.
            design: 'ABA' or 'crossover'.
            phase_duration: Days per phase.
            washout_duration: Days between phases.
            min_obs: Minimum observations per phase.
            literature_evidence: JSON string of supporting evidence.
            secondary_outcomes: JSON string of secondary metric names.

        Returns:
            The trials row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO trials
               (name, hypothesis, intervention, primary_outcome_metric,
                secondary_outcomes_json, design, phase_duration_days,
                washout_duration_days, min_observations_per_phase,
                status, literature_evidence_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)""",
            (
                name, hypothesis, intervention, primary_outcome,
                secondary_outcomes, design, phase_duration,
                washout_duration, min_obs, literature_evidence, now, now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def approve_trial(self, trial_id: int) -> None:
        """Set trial status to 'approved'."""
        now = self._now()
        self._execute(
            "UPDATE trials SET status = 'approved', updated_at = ? WHERE id = ?",
            (now, trial_id),
        )
        self.conn.commit()

    def start_trial(self, trial_id: int) -> None:
        """Set trial status to 'active' and record start_date."""
        now = self._now()
        self._execute(
            "UPDATE trials SET status = 'active', start_date = ?, updated_at = ? WHERE id = ?",
            (now, now, trial_id),
        )
        self.conn.commit()

    def log_trial_observation(
        self,
        trial_id: int,
        date: str,
        phase: str,
        metric_name: str,
        value: float,
        compliance_score: float = None,
        notes: str = None,
    ) -> int:
        """Log an observation for a trial.

        Args:
            trial_id: The trial this observation belongs to.
            date: ISO 8601 date of observation.
            phase: One of 'baseline', 'intervention', 'washout', 'control'.
            metric_name: Which metric was measured.
            value: Numeric value.
            compliance_score: 0-1 how well protocol was followed.
            notes: Additional notes.

        Returns:
            The trial_observations row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO trial_observations
               (trial_id, date, phase, metric_name, value,
                compliance_score, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (trial_id, date, phase, metric_name, value,
             compliance_score, notes, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def complete_trial(self, trial_id: int) -> None:
        """Set trial status to 'completed' and record end_date."""
        now = self._now()
        self._execute(
            "UPDATE trials SET status = 'completed', end_date = ?, updated_at = ? WHERE id = ?",
            (now, now, trial_id),
        )
        self.conn.commit()

    def abandon_trial(self, trial_id: int) -> None:
        """Set trial status to 'abandoned' and record end_date."""
        now = self._now()
        self._execute(
            "UPDATE trials SET status = 'abandoned', end_date = ?, updated_at = ? WHERE id = ?",
            (now, now, trial_id),
        )
        self.conn.commit()

    def get_trial(self, trial_id: int) -> Optional[dict]:
        """Get a trial with all its observations.

        Returns:
            Trial dict with an 'observations' key, or None.
        """
        row = self._execute(
            "SELECT * FROM trials WHERE id = ?", (trial_id,)
        ).fetchone()
        if not row:
            return None

        trial = self._row_to_dict(row)
        obs = self._execute(
            "SELECT * FROM trial_observations WHERE trial_id = ? ORDER BY date",
            (trial_id,),
        ).fetchall()
        trial["observations"] = self._rows_to_dicts(obs)
        return trial

    def get_active_trials(self) -> list[dict]:
        """Get all trials with status='active'."""
        rows = self._execute(
            "SELECT * FROM trials WHERE status = 'active' ORDER BY start_date"
        ).fetchall()
        return self._rows_to_dicts(rows)

    # ===================================================================
    # INSIGHTS & MODEL MODULE
    # ===================================================================

    def save_insight(
        self,
        insight_type: str,
        source_modules: list[str],
        description: str,
        statistical_detail: dict,
        effect_size: float = None,
        p_value: float = None,
        confidence_level: str = "medium",
        evidence_level: int = 1,
        actionable: bool = False,
        trial_candidate: bool = False,
    ) -> int:
        """Save a generated insight.

        Args:
            insight_type: One of 'correlation', 'trend', 'anomaly', 'pattern'.
            source_modules: List of modules that contributed (e.g., ['diet', 'exercise']).
            description: Human-readable description.
            statistical_detail: Dict of statistical output.
            effect_size: Cohen's d or similar.
            p_value: Statistical significance.
            confidence_level: 'low', 'medium', or 'high'.
            evidence_level: 1-5 scale.
            actionable: Whether this insight suggests action.
            trial_candidate: Whether to suggest as n-of-1 trial.

        Returns:
            The insights row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO insights
               (timestamp, insight_type, source_modules_json, description,
                statistical_detail_json, effect_size, p_value,
                confidence_level, evidence_level, actionable, trial_candidate,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now, insight_type, json.dumps(source_modules), description,
                json.dumps(statistical_detail), effect_size, p_value,
                confidence_level, evidence_level,
                1 if actionable else 0,
                1 if trial_candidate else 0,
                now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_recent_insights(self, days: int = 7) -> list[dict]:
        """Get insights from the last N days.

        Args:
            days: Number of days to look back.

        Returns:
            List of insight dicts.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._execute(
            "SELECT * FROM insights WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
        return self._rows_to_dicts(rows)

    def save_model_cache(
        self,
        metric_name: str,
        window_type: str,
        stats_dict: dict,
    ) -> int:
        """Save or update cached model statistics for a metric.

        Args:
            metric_name: The metric name.
            window_type: One of '7d', '30d', '90d'.
            stats_dict: Dict with keys: mean, std, min, max, n, trend_slope.
                        May include 'extra' key for additional JSON data.

        Returns:
            The model_cache row id.
        """
        now = self._now()
        extra = stats_dict.get("extra")
        extra_json = json.dumps(extra) if extra else None

        cursor = self._execute(
            """INSERT INTO model_cache
               (metric_name, window_type, computed_at, mean, std, min, max,
                n, trend_slope, extra_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(metric_name, window_type) DO UPDATE SET
                   computed_at = excluded.computed_at,
                   mean = excluded.mean,
                   std = excluded.std,
                   min = excluded.min,
                   max = excluded.max,
                   n = excluded.n,
                   trend_slope = excluded.trend_slope,
                   extra_json = excluded.extra_json""",
            (
                metric_name, window_type, now,
                stats_dict.get("mean"),
                stats_dict.get("std"),
                stats_dict.get("min"),
                stats_dict.get("max"),
                stats_dict.get("n"),
                stats_dict.get("trend_slope"),
                extra_json,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_model_cache(self, metric_name: str, window_type: str) -> Optional[dict]:
        """Get cached model statistics.

        Args:
            metric_name: The metric name.
            window_type: One of '7d', '30d', '90d'.

        Returns:
            Cache dict or None if not cached.
        """
        row = self._execute(
            "SELECT * FROM model_cache WHERE metric_name = ? AND window_type = ?",
            (metric_name, window_type),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def log_model_run(
        self,
        run_type: str,
        modules_analyzed: list[str],
        duration_seconds: float,
        insights_generated: int,
        notes: str = None,
    ) -> int:
        """Log a model analysis run.

        Args:
            run_type: One of 'passive', 'batch', 'deep'.
            modules_analyzed: List of module names analyzed.
            duration_seconds: How long the run took.
            insights_generated: Number of insights produced.
            notes: Additional notes.

        Returns:
            The model_runs row id.
        """
        now = self._now()
        cursor = self._execute(
            """INSERT INTO model_runs
               (timestamp, run_type, modules_analyzed_json,
                duration_seconds, insights_generated, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now, run_type, json.dumps(modules_analyzed),
             duration_seconds, insights_generated, notes),
        )
        self.conn.commit()
        return cursor.lastrowid

    # ===================================================================
    # NUTRITION CACHE
    # ===================================================================

    def cache_nutrition(
        self,
        normalized_ingredient: str,
        fdc_id: int,
        nutrients: dict,
        source: str,
    ) -> int:
        """Cache a nutrition lookup result with 90-day expiry.

        Args:
            normalized_ingredient: Canonical ingredient name.
            fdc_id: USDA FoodData Central ID (or None).
            nutrients: Dict of nutrient values.
            source: One of 'usda', 'openfoodfacts', 'estimate'.

        Returns:
            The nutrition_cache row id.
        """
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=90)

        cursor = self._execute(
            """INSERT INTO nutrition_cache
               (normalized_ingredient, fdc_id, nutrients_json, source,
                fetched_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(normalized_ingredient) DO UPDATE SET
                   fdc_id = excluded.fdc_id,
                   nutrients_json = excluded.nutrients_json,
                   source = excluded.source,
                   fetched_at = excluded.fetched_at,
                   expires_at = excluded.expires_at""",
            (
                normalized_ingredient, fdc_id, json.dumps(nutrients),
                source, now.isoformat(), expires.isoformat(),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_cached_nutrition(self, normalized_ingredient: str) -> Optional[dict]:
        """Get cached nutrition data if not expired.

        Args:
            normalized_ingredient: Canonical ingredient name.

        Returns:
            Dict with nutrition data, or None if not cached or expired.
        """
        now = self._now()
        row = self._execute(
            """SELECT * FROM nutrition_cache
               WHERE normalized_ingredient = ? AND expires_at > ?""",
            (normalized_ingredient, now),
        ).fetchone()
        if row:
            result = self._row_to_dict(row)
            result["nutrients"] = json.loads(result["nutrients_json"])
            return result
        return None

    # ===================================================================
    # DAILY SUMMARY
    # ===================================================================

    def get_daily_summary(self, date: str) -> dict:
        """Get all entries for a single day across all modules.

        Args:
            date: ISO 8601 date string (e.g., '2026-03-12'). Will match
                  timestamps that start with this date.

        Returns:
            Dict with keys: meals, exercises, metrics, biomarkers, supplements,
                            trial_observations, insights.
        """
        date_prefix = date[:10]  # Ensure we only use YYYY-MM-DD
        start = f"{date_prefix}T00:00:00"
        end = f"{date_prefix}T23:59:59"

        return {
            "date": date_prefix,
            "meals": self.get_meals(start, end),
            "exercises": self.get_exercises(start, end),
            "metrics": self._rows_to_dicts(
                self._execute(
                    "SELECT * FROM body_metrics WHERE timestamp >= ? AND timestamp <= ?",
                    (start, end),
                ).fetchall()
            ),
            "biomarkers": self._rows_to_dicts(
                self._execute(
                    "SELECT * FROM biomarkers WHERE timestamp >= ? AND timestamp <= ?",
                    (start, end),
                ).fetchall()
            ),
            "supplements": self._rows_to_dicts(
                self._execute(
                    """SELECT * FROM supplements
                       WHERE start_date <= ? AND (end_date IS NULL OR end_date >= ?)""",
                    (date_prefix, date_prefix),
                ).fetchall()
            ),
            "trial_observations": self._rows_to_dicts(
                self._execute(
                    "SELECT * FROM trial_observations WHERE date = ?",
                    (date_prefix,),
                ).fetchall()
            ),
            "insights": self._rows_to_dicts(
                self._execute(
                    "SELECT * FROM insights WHERE timestamp >= ? AND timestamp <= ?",
                    (start, end),
                ).fetchall()
            ),
        }

    # ===================================================================
    # DELETION OPERATIONS
    # ===================================================================

    def delete_entry(self, table: str, entry_id: int) -> bool:
        """Delete a single entry by id.

        Args:
            table: Table name (validated against allowlist).
            entry_id: Row id to delete.

        Returns:
            True if a row was deleted, False otherwise.
        """
        valid_tables = {
            "diet_entries", "diet_ingredients", "recipe_library",
            "exercise_entries", "exercise_details", "body_metrics",
            "custom_metric_definitions", "biomarkers", "supplements",
            "trials", "trial_observations", "insights", "model_runs",
            "model_cache", "nutrition_cache",
        }
        if table not in valid_tables:
            raise ValueError(f"Invalid table name: {table}. Must be one of: {valid_tables}")

        cursor = self._execute(
            f"DELETE FROM {table} WHERE id = ?",
            (entry_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def purge_date_range(self, table: str, start_date: str, end_date: str) -> int:
        """Delete entries in a date range.

        Works with tables that have a 'timestamp' column (diet_entries,
        exercise_entries, body_metrics, biomarkers, insights) or a 'date'
        column (trial_observations).

        Args:
            table: Table name.
            start_date: ISO 8601 start (inclusive).
            end_date: ISO 8601 end (inclusive).

        Returns:
            Number of rows deleted.
        """
        timestamp_tables = {
            "diet_entries", "exercise_entries", "body_metrics",
            "biomarkers", "insights",
        }
        date_tables = {"trial_observations"}

        if table in timestamp_tables:
            col = "timestamp"
        elif table in date_tables:
            col = "date"
        else:
            raise ValueError(
                f"Cannot purge by date range from table: {table}. "
                f"Valid tables: {timestamp_tables | date_tables}"
            )

        cursor = self._execute(
            f"DELETE FROM {table} WHERE {col} >= ? AND {col} <= ?",
            (start_date, end_date),
        )
        self.conn.commit()
        return cursor.rowcount

    def full_wipe(self, confirm: bool = False) -> None:
        """Drop ALL data from ALL tables. Destructive operation.

        Args:
            confirm: Must be True to proceed. Safety check.

        Raises:
            ValueError: If confirm is not True.
        """
        if not confirm:
            raise ValueError(
                "full_wipe() requires confirm=True. "
                "This will permanently delete ALL data."
            )

        tables = [
            "trial_observations", "trial_observations",
            "diet_ingredients", "diet_entries",
            "exercise_details", "exercise_entries",
            "body_metrics", "custom_metric_definitions",
            "biomarkers", "supplements",
            "trials", "insights",
            "model_runs", "model_cache",
            "nutrition_cache", "recipe_library",
            "schema_version",
        ]
        for table in tables:
            self._execute(f"DELETE FROM {table}")
        self.conn.commit()
