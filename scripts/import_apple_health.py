#!/usr/bin/env python3
"""
TaiYiYuan (太医院) — Apple Health Import (Phase 2 Stub)

Imports health data from Apple Health XML exports into TaiYiYuan.

Usage:
    python import_apple_health.py --file export.xml
    python import_apple_health.py --file export.xml --types steps,heart_rate
    python import_apple_health.py --file export.xml --date-range 2026-01-01 2026-03-12

Status: Phase 2 — NOT YET IMPLEMENTED
"""

import argparse
import sys
from pathlib import Path
# import xml.etree.ElementTree as ET
# import sqlite3
# import os
# from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path


DB_PATH = str(get_db_path())

# Mapping from Apple Health HKQuantityTypeIdentifier to TaiYiYuan tables/metrics
APPLE_HEALTH_MAPPINGS = {
    # body_metrics table
    "HKQuantityTypeIdentifierStepCount": {
        "table": "body_metrics",
        "metric_type": "steps",
        "unit": "count",
        "aggregation": "sum_daily",  # Sum all entries per day
    },
    "HKQuantityTypeIdentifierHeartRate": {
        "table": "body_metrics",
        "metric_type": "heart_rate",
        "unit": "bpm",
        "aggregation": "individual",  # Keep each reading
    },
    "HKQuantityTypeIdentifierRestingHeartRate": {
        "table": "body_metrics",
        "metric_type": "resting_heart_rate",
        "unit": "bpm",
        "aggregation": "daily_min",
    },
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": {
        "table": "body_metrics",
        "metric_type": "hrv_sdnn",
        "unit": "ms",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierBodyMass": {
        "table": "body_metrics",
        "metric_type": "weight",
        "unit": "kg",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierBodyFatPercentage": {
        "table": "body_metrics",
        "metric_type": "body_fat",
        "unit": "%",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierBodyMassIndex": {
        "table": "body_metrics",
        "metric_type": "bmi",
        "unit": "kg/m2",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierBloodPressureSystolic": {
        "table": "body_metrics",
        "metric_type": "blood_pressure_sys",
        "unit": "mmHg",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierBloodPressureDiastolic": {
        "table": "body_metrics",
        "metric_type": "blood_pressure_dia",
        "unit": "mmHg",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierOxygenSaturation": {
        "table": "body_metrics",
        "metric_type": "spo2",
        "unit": "%",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierRespiratoryRate": {
        "table": "body_metrics",
        "metric_type": "respiratory_rate",
        "unit": "breaths/min",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierBodyTemperature": {
        "table": "body_metrics",
        "metric_type": "body_temperature",
        "unit": "degC",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierBloodGlucose": {
        "table": "body_metrics",
        "metric_type": "blood_glucose",
        "unit": "mg/dL",
        "aggregation": "individual",
    },
    "HKQuantityTypeIdentifierVO2Max": {
        "table": "body_metrics",
        "metric_type": "vo2_max",
        "unit": "mL/kg/min",
        "aggregation": "individual",
    },

    # exercise_entries table
    "HKQuantityTypeIdentifierDistanceWalkingRunning": {
        "table": "exercise_entries",
        "metric_type": "distance_walking_running",
        "unit": "km",
        "aggregation": "sum_daily",
    },
    "HKQuantityTypeIdentifierDistanceCycling": {
        "table": "exercise_entries",
        "metric_type": "distance_cycling",
        "unit": "km",
        "aggregation": "sum_daily",
    },
    "HKQuantityTypeIdentifierDistanceSwimming": {
        "table": "exercise_entries",
        "metric_type": "distance_swimming",
        "unit": "m",
        "aggregation": "sum_daily",
    },
    "HKQuantityTypeIdentifierActiveEnergyBurned": {
        "table": "body_metrics",
        "metric_type": "active_energy",
        "unit": "kcal",
        "aggregation": "sum_daily",
    },
    "HKQuantityTypeIdentifierBasalEnergyBurned": {
        "table": "body_metrics",
        "metric_type": "basal_energy",
        "unit": "kcal",
        "aggregation": "sum_daily",
    },
    "HKQuantityTypeIdentifierAppleExerciseTime": {
        "table": "body_metrics",
        "metric_type": "exercise_minutes",
        "unit": "min",
        "aggregation": "sum_daily",
    },
    "HKQuantityTypeIdentifierAppleStandTime": {
        "table": "body_metrics",
        "metric_type": "stand_hours",
        "unit": "hr",
        "aggregation": "sum_daily",
    },

    # Sleep — mapped to body_metrics as sleep stages
    "HKCategoryTypeIdentifierSleepAnalysis": {
        "table": "body_metrics",
        "metric_type": "sleep",
        "unit": "hr",
        "aggregation": "sleep_stages",  # Special processing for sleep
    },
}

# Friendly category names for display
IMPORT_CATEGORIES = {
    "Steps & Activity": [
        "steps", "active_energy", "basal_energy", "exercise_minutes",
        "stand_hours", "distance_walking_running",
    ],
    "Heart & Cardio": [
        "heart_rate", "resting_heart_rate", "hrv_sdnn", "vo2_max",
        "blood_pressure_sys", "blood_pressure_dia",
    ],
    "Sleep": ["sleep"],
    "Body Composition": ["weight", "body_fat", "bmi"],
    "Workouts": ["distance_cycling", "distance_swimming"],
    "Vitals": [
        "spo2", "respiratory_rate", "body_temperature", "blood_glucose",
    ],
}


# ---------------------------------------------------------------------------
# XML Parsing Skeleton (Phase 2 — commented out)
# ---------------------------------------------------------------------------

# def parse_apple_health_xml(xml_path: str, type_filter: set | None = None,
#                            date_start: str | None = None,
#                            date_end: str | None = None):
#     """
#     Parse Apple Health export.xml using iterative ElementTree parsing.
#
#     Apple Health XML structure:
#       <HealthData>
#         <ExportDate value="2026-03-12 10:00:00 -0500"/>
#         <Me ... />
#         <Record type="HKQuantityTypeIdentifierStepCount"
#                 sourceName="iPhone"
#                 sourceVersion="19.3"
#                 device="..."
#                 unit="count"
#                 creationDate="2026-03-12 09:15:00 -0500"
#                 startDate="2026-03-12 09:00:00 -0500"
#                 endDate="2026-03-12 09:15:00 -0500"
#                 value="1234"/>
#         <Workout workoutActivityType="HKWorkoutActivityTypeRunning"
#                  duration="30.5"
#                  durationUnit="min"
#                  totalDistance="5.2"
#                  totalDistanceUnit="km"
#                  totalEnergyBurned="320"
#                  totalEnergyBurnedUnit="kcal"
#                  sourceName="Apple Watch"
#                  startDate="2026-03-12 07:00:00 -0500"
#                  endDate="2026-03-12 07:30:30 -0500">
#           <WorkoutStatistics ... />
#         </Workout>
#       </HealthData>
#
#     Uses iterparse for memory efficiency (export.xml can be 1+ GB).
#     """
#     records = []
#     workouts = []
#
#     context = ET.iterparse(xml_path, events=("end",))
#     for event, elem in context:
#         if elem.tag == "Record":
#             record_type = elem.get("type", "")
#
#             # Filter by requested types
#             if type_filter and record_type not in type_filter:
#                 elem.clear()
#                 continue
#
#             # Filter by date range
#             start_date = elem.get("startDate", "")
#             if date_start and start_date < date_start:
#                 elem.clear()
#                 continue
#             if date_end and start_date > date_end:
#                 elem.clear()
#                 continue
#
#             mapping = APPLE_HEALTH_MAPPINGS.get(record_type)
#             if mapping:
#                 records.append({
#                     "type": record_type,
#                     "value": elem.get("value"),
#                     "unit": elem.get("unit", mapping["unit"]),
#                     "start_date": start_date,
#                     "end_date": elem.get("endDate", ""),
#                     "source": elem.get("sourceName", ""),
#                     "device": elem.get("device", ""),
#                     "mapping": mapping,
#                 })
#
#             elem.clear()  # Free memory
#
#         elif elem.tag == "Workout":
#             workout_type = elem.get("workoutActivityType", "")
#             start_date = elem.get("startDate", "")
#
#             if date_start and start_date < date_start:
#                 elem.clear()
#                 continue
#             if date_end and start_date > date_end:
#                 elem.clear()
#                 continue
#
#             workouts.append({
#                 "activity_type": workout_type.replace("HKWorkoutActivityType", "").lower(),
#                 "duration_minutes": float(elem.get("duration", 0)),
#                 "distance_km": float(elem.get("totalDistance", 0)) if elem.get("totalDistanceUnit") == "km" else None,
#                 "calories": float(elem.get("totalEnergyBurned", 0)),
#                 "start_date": start_date,
#                 "end_date": elem.get("endDate", ""),
#                 "source": elem.get("sourceName", ""),
#             })
#
#             elem.clear()
#
#     return records, workouts


# def insert_health_records(records, workouts, db_path=DB_PATH):
#     """Insert parsed Apple Health records into TaiYiYuan database."""
#     conn = sqlite3.connect(db_path)
#     conn.execute("PRAGMA journal_mode=WAL")
#     conn.execute("PRAGMA foreign_keys=ON")
#     now = datetime.now(timezone.utc).isoformat()
#
#     metrics_inserted = 0
#     workouts_inserted = 0
#
#     # Insert body metrics
#     for record in records:
#         mapping = record["mapping"]
#         if mapping["table"] == "body_metrics":
#             conn.execute(
#                 """INSERT INTO body_metrics
#                    (timestamp, metric_type, value, unit, device_method, created_at)
#                    VALUES (?, ?, ?, ?, ?, ?)""",
#                 (
#                     record["start_date"],
#                     mapping["metric_type"],
#                     float(record["value"]),
#                     mapping["unit"],
#                     record.get("source", "Apple Health"),
#                     now,
#                 ),
#             )
#             metrics_inserted += 1
#
#     # Insert workouts as exercise_entries
#     for workout in workouts:
#         conn.execute(
#             """INSERT INTO exercise_entries
#                (timestamp, activity_type, duration_minutes, distance_km,
#                 notes, created_at, updated_at)
#                VALUES (?, ?, ?, ?, ?, ?, ?)""",
#             (
#                 workout["start_date"],
#                 workout["activity_type"],
#                 workout["duration_minutes"],
#                 workout["distance_km"],
#                 f"Source: {workout.get('source', 'Apple Health')}",
#                 now,
#                 now,
#             ),
#         )
#         workouts_inserted += 1
#
#     conn.commit()
#     conn.close()
#     return metrics_inserted, workouts_inserted


# ---------------------------------------------------------------------------
# Phase 2 stub
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TaiYiYuan Apple Health importer (Phase 2)"
    )
    parser.add_argument(
        "--file", metavar="PATH", required=True,
        help="Path to Apple Health export.xml"
    )
    parser.add_argument(
        "--types", metavar="LIST",
        help="Comma-separated list of metric types to import (default: all)"
    )
    parser.add_argument(
        "--date-range", nargs=2, metavar=("START", "END"),
        help="Filter by date range: YYYY-MM-DD YYYY-MM-DD"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and show summary without inserting into database"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Apple Health Import --- Phase 2 Feature")
    print("=" * 60)
    print()
    print("This feature is planned but not yet implemented.")
    print()
    print(f"File specified: {args.file}")
    print()
    print("When implemented, this script will import:")
    print()

    for category, metrics in IMPORT_CATEGORIES.items():
        print(f"  {category}:")
        for m in metrics:
            # Find the Apple Health type for this metric
            apple_type = None
            for hk_type, mapping in APPLE_HEALTH_MAPPINGS.items():
                if mapping["metric_type"] == m:
                    apple_type = hk_type
                    break
            target = None
            for hk_type, mapping in APPLE_HEALTH_MAPPINGS.items():
                if mapping["metric_type"] == m:
                    target = mapping["table"]
                    break
            print(f"    - {m:<30s} -> {target or '?'}")
        print()

    print(f"Total supported record types: {len(APPLE_HEALTH_MAPPINGS)}")
    print()
    print("Data flow:")
    print("  export.xml -> parse (iterparse) -> deduplicate -> insert")
    print()
    print("Target tables:")
    print("  - body_metrics   (steps, HR, HRV, weight, BP, SpO2, etc.)")
    print("  - exercise_entries (workouts with duration, distance, calories)")
    print()
    print("To request this feature, open an issue or contact the developer.")
    sys.exit(0)


if __name__ == "__main__":
    main()
