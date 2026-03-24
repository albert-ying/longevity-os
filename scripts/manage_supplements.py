#!/usr/bin/env python3
"""
Manage structured supplement payloads in the TaiYiYuan database.

The script reads a JSON payload from stdin by default:

{
  "action": "add",
  "compound_name": "Creatine monohydrate",
  "dosage": 5,
  "dosage_unit": "g",
  "frequency": "daily",
  "timing": "morning",
  "start_date": "2026-03-19"
}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
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
        raise ValueError("Supplement payload must be a JSON object")
    return payload


def _require(payload: dict, key: str):
    value = payload.get(key)
    if value in (None, ""):
        raise ValueError(f"Missing required field: {key}")
    return value


def _find_target(db: TaiYiYuanDB, payload: dict) -> dict:
    supplement_id = payload.get("supplement_id")
    compound_name = payload.get("compound_name")
    supplements = db.get_supplements(active_only=False)

    if supplement_id is not None:
        for supplement in supplements:
            if supplement["id"] == supplement_id:
                return supplement
        raise ValueError(f"Supplement {supplement_id} not found")

    if compound_name:
        active_matches = [
            supplement
            for supplement in supplements
            if supplement["compound_name"] == compound_name and supplement["end_date"] is None
        ]
        if len(active_matches) == 1:
            return active_matches[0]
        if len(active_matches) > 1:
            raise ValueError(f"Multiple active supplements found for {compound_name}; pass supplement_id")
        raise ValueError(f"No active supplement found for {compound_name}")

    raise ValueError("Pass supplement_id or compound_name")


def _stack_overview(db: TaiYiYuanDB) -> list[dict]:
    active = db.get_supplements(active_only=True)
    return [
        {
            "id": supplement["id"],
            "name": supplement["compound_name"],
            "dosage": supplement["dosage"],
            "unit": supplement["dosage_unit"],
            "frequency": supplement["frequency"],
            "timing": supplement["timing"],
            "since": supplement["start_date"],
        }
        for supplement in active
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage supplements in the TaiYiYuan database")
    parser.add_argument(
        "--input",
        default="-",
        help="Path to a JSON payload file, or '-' to read from stdin (default)",
    )
    args = parser.parse_args()

    try:
        payload = _read_payload(args.input)
        action = _require(payload, "action")
        if action not in {"add", "update", "stop", "list"}:
            raise ValueError("action must be one of: add, update, stop, list")

        with TaiYiYuanDB() as db:
            changed = None
            result_action = action

            if action == "add":
                supplement_id = db.log_supplement(
                    compound_name=_require(payload, "compound_name"),
                    dosage=float(_require(payload, "dosage")),
                    dosage_unit=_require(payload, "dosage_unit"),
                    frequency=_require(payload, "frequency"),
                    timing=_require(payload, "timing"),
                    start_date=_require(payload, "start_date"),
                    reason=payload.get("reason"),
                    brand=payload.get("brand"),
                )
                changed = _find_target(db, {"supplement_id": supplement_id})
                result_action = "added"
            elif action == "update":
                target = _find_target(db, payload)
                updates = payload.get("updates")
                if not isinstance(updates, dict) or not updates:
                    raise ValueError("updates must be a non-empty JSON object")
                db.update_supplement(target["id"], **updates)
                changed = _find_target(db, {"supplement_id": target["id"]})
                result_action = "updated"
            elif action == "stop":
                target = _find_target(db, payload)
                end_date = payload.get("end_date") or payload.get("timestamp") or date.today().isoformat()
                db.stop_supplement(target["id"], end_date)
                changed = _find_target(db, {"supplement_id": target["id"]})
                result_action = "stopped"

            result = {
                "status": "success",
                "action": result_action,
                "database": str(get_db_path()),
                "supplement": changed,
                "stack_overview": _stack_overview(db),
            }

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
