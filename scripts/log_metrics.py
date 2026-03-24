#!/usr/bin/env python3
"""
Log one or more structured body metric payloads to the TaiYiYuan database.

The script reads a JSON payload from stdin by default:

{
  "timestamp": "2026-03-12T07:00:00+00:00",
  "entries": [
    {"timestamp": "2026-03-12T07:00:00+00:00", "metric_type": "weight", "value": 72.5, "unit": "kg"},
    {"timestamp": "2026-03-12T07:00:00+00:00", "metric_type": "resting_hr", "value": 56, "unit": "bpm"}
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
        raise ValueError("Metric payload must be a JSON object")
    return payload


def _require(entry: dict, key: str):
    value = entry.get(key)
    if value in (None, ""):
        raise ValueError(f"Missing required field: {key}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Log body metrics to the TaiYiYuan database")
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
        default_context = payload.get("context")
        default_device_method = payload.get("device_method")
        default_notes = payload.get("notes")
        with TaiYiYuanDB() as db:
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("Each metric entry must be a JSON object")
                timestamp = entry.get("timestamp", default_timestamp)
                if timestamp in (None, ""):
                    raise ValueError("Missing required field: timestamp")
                metric_id = db.log_metric(
                    timestamp=timestamp,
                    metric_type=_require(entry, "metric_type"),
                    value=float(_require(entry, "value")),
                    unit=_require(entry, "unit"),
                    context=entry.get("context", default_context),
                    device_method=entry.get("device_method", default_device_method),
                    notes=entry.get("notes", default_notes),
                )
                created.append(
                    {
                        "id": metric_id,
                        "metric_type": entry["metric_type"],
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
