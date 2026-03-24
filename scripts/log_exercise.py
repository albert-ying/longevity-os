#!/usr/bin/env python3
"""
Log a structured exercise payload to the TaiYiYuan database.

The script reads a JSON payload from stdin by default:

{
  "timestamp": "2026-03-12T18:00:00+00:00",
  "activity_type": "strength",
  "duration_minutes": 55,
  "distance_km": null,
  "avg_hr": 132,
  "rpe": 7,
  "notes": "Leg day",
  "details": [
    {"exercise_name": "barbell squat", "sets": 4, "reps": 8, "weight_kg": 100}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.db import TaiYiYuanDB
from paths import get_db_path


def _read_payload(source: str) -> dict:
    if source == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(source).read_text(encoding="utf-8")

    if not raw.strip():
        raise ValueError("Expected a JSON payload on stdin or via --input")

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Exercise payload must be a JSON object")
    return payload


def _require(payload: dict, key: str):
    value = payload.get(key)
    if value in (None, ""):
        raise ValueError(f"Missing required field: {key}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Log an exercise session to the TaiYiYuan database")
    parser.add_argument(
        "--input",
        default="-",
        help="Path to a JSON payload file, or '-' to read from stdin (default)",
    )
    args = parser.parse_args()

    try:
        payload = _read_payload(args.input)
        timestamp = _require(payload, "timestamp")
        activity_type = _require(payload, "activity_type")
        duration_minutes = float(_require(payload, "duration_minutes"))
        details = payload.get("details")
        if details is not None and not isinstance(details, list):
            raise ValueError("details must be a JSON array when provided")

        with TaiYiYuanDB() as db:
            entry_id = db.log_exercise(
                timestamp=timestamp,
                activity_type=activity_type,
                duration_minutes=duration_minutes,
                details=details,
                distance_km=payload.get("distance_km"),
                avg_hr=payload.get("avg_hr"),
                rpe=payload.get("rpe"),
                notes=payload.get("notes"),
            )
            entry = db._execute(
                """
                SELECT activity_type, duration_minutes, distance_km, avg_hr, rpe, notes
                FROM exercise_entries
                WHERE id = ?
                """,
                (entry_id,),
            ).fetchone()

        result = {
            "status": "success",
            "entry_id": entry_id,
            "database": str(get_db_path()),
            "rows_written": {
                "exercise_entries": 1,
                "exercise_details": len(details or []),
            },
            "entry": {
                "activity_type": entry["activity_type"],
                "duration_minutes": entry["duration_minutes"],
                "distance_km": entry["distance_km"],
                "avg_hr": entry["avg_hr"],
                "rpe": entry["rpe"],
                "notes": entry["notes"],
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
