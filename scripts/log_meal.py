#!/usr/bin/env python3
"""
Log a structured meal payload to the TaiYiYuan database.

The script reads a JSON payload from stdin by default:

{
  "timestamp": "2026-03-12T19:00:00+00:00",
  "meal_type": "dinner",
  "description": "Salmon, rice, and broccoli",
  "ingredients": [
    {"ingredient_name": "salmon", "amount_g": 170, "calories": 353, "protein_g": 34.0}
  ],
  "confidence_score": 0.8,
  "notes": "Portions estimated from user description"
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
        raise ValueError("Meal payload must be a JSON object")
    return payload


def _require(payload: dict, key: str):
    value = payload.get(key)
    if value in (None, ""):
        raise ValueError(f"Missing required field: {key}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Log a meal to the TaiYiYuan database")
    parser.add_argument(
        "--input",
        default="-",
        help="Path to a JSON payload file, or '-' to read from stdin (default)",
    )
    args = parser.parse_args()

    try:
        payload = _read_payload(args.input)
        timestamp = _require(payload, "timestamp")
        meal_type = _require(payload, "meal_type")
        description = _require(payload, "description")
        ingredients = _require(payload, "ingredients")
        if not isinstance(ingredients, list) or not ingredients:
            raise ValueError("ingredients must be a non-empty JSON array")

        with TaiYiYuanDB() as db:
            entry_id = db.log_meal(
                timestamp=timestamp,
                meal_type=meal_type,
                description=description,
                ingredients=ingredients,
                confidence_score=payload.get("confidence_score"),
                photo_path=payload.get("photo_path"),
                notes=payload.get("notes"),
            )

            entry = db._execute(
                """
                SELECT total_calories, total_protein_g, total_carbs_g, total_fat_g, total_fiber_g
                FROM diet_entries
                WHERE id = ?
                """,
                (entry_id,),
            ).fetchone()

        result = {
            "status": "success",
            "entry_id": entry_id,
            "database": str(get_db_path()),
            "rows_written": {
                "diet_entries": 1,
                "diet_ingredients": len(ingredients),
            },
            "totals": {
                "calories": entry["total_calories"],
                "protein_g": entry["total_protein_g"],
                "carbs_g": entry["total_carbs_g"],
                "fat_g": entry["total_fat_g"],
                "fiber_g": entry["total_fiber_g"],
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
