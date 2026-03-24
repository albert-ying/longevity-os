#!/usr/bin/env python3
"""
Log one or more structured biomarker payloads to the TaiYiYuan database.

The script reads a JSON payload from stdin by default:

{
  "timestamp": "2026-03-12T08:00:00+00:00",
  "lab_source": "Quest Diagnostics",
  "entries": [
    {
      "panel_name": "Lipid Panel",
      "marker_name": "LDL",
      "value": 112,
      "unit": "mg/dL",
      "reference_low": 0,
      "reference_high": 130,
      "optimal_low": 0,
      "optimal_high": 100
    }
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
        raise ValueError("Biomarker payload must be a JSON object")
    return payload


def _require(entry: dict, key: str):
    value = entry.get(key)
    if value in (None, ""):
        raise ValueError(f"Missing required field: {key}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Log biomarker results to the TaiYiYuan database")
    parser.add_argument(
        "--input",
        default="-",
        help="Path to a JSON payload file, or '-' to read from stdin (default)",
    )
    args = parser.parse_args()

    try:
        payload = _read_payload(args.input)
        entries = payload.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError("entries must be a non-empty JSON array")

        created = []
        default_timestamp = payload.get("timestamp")
        default_lab_source = payload.get("lab_source")
        default_notes = payload.get("notes")
        with TaiYiYuanDB() as db:
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("Each biomarker entry must be a JSON object")
                timestamp = entry.get("timestamp", default_timestamp)
                if timestamp in (None, ""):
                    raise ValueError("Missing required field: timestamp")
                biomarker_id = db.log_biomarker(
                    timestamp=timestamp,
                    panel_name=_require(entry, "panel_name"),
                    marker_name=_require(entry, "marker_name"),
                    value=float(_require(entry, "value")),
                    unit=_require(entry, "unit"),
                    reference_low=entry.get("reference_low"),
                    reference_high=entry.get("reference_high"),
                    optimal_low=entry.get("optimal_low"),
                    optimal_high=entry.get("optimal_high"),
                    notes=entry.get("notes", default_notes),
                    lab_source=entry.get("lab_source", default_lab_source),
                )
                created.append(
                    {
                        "id": biomarker_id,
                        "panel_name": entry["panel_name"],
                        "marker_name": entry["marker_name"],
                        "value": entry["value"],
                        "unit": entry["unit"],
                    }
                )

        result = {
            "status": "success",
            "database": str(get_db_path()),
            "entries_created": len(created),
            "entry_ids": [entry["id"] for entry in created],
            "entries": created,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
