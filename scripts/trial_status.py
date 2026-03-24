#!/usr/bin/env python3
"""
Return grounded status summaries for one or more active TaiYiYuan trials.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    return date.fromisoformat(value)


def _phase_summaries(observations: list[sqlite3.Row], min_required: int) -> list[dict]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in observations:
        grouped[row["phase"]].append(row)

    summaries = []
    for phase, rows in grouped.items():
        rows = sorted(rows, key=lambda row: row["date"])
        compliance_values = [float(row["compliance_score"]) for row in rows if row["compliance_score"] is not None]
        summaries.append(
            {
                "phase": phase,
                "start": rows[0]["date"],
                "end": rows[-1]["date"],
                "observations": len(rows),
                "compliance": round(sum(compliance_values) / len(compliance_values), 2) if compliance_values else None,
                "required": min_required,
                "complete": len(rows) >= min_required,
            }
        )
    return summaries


def _current_phase(phase_summaries: list[dict], as_of: date) -> dict | None:
    if not phase_summaries:
        return None
    for phase in phase_summaries:
        start = date.fromisoformat(phase["start"])
        end = date.fromisoformat(phase["end"])
        if start <= as_of <= end:
            return phase
    if as_of > date.fromisoformat(phase_summaries[-1]["end"]):
        return phase_summaries[-1]
    return phase_summaries[0]


def build_trial_status(trial_row: sqlite3.Row, observations: list[sqlite3.Row], as_of: date | None) -> dict:
    start_date = _parse_date(trial_row["start_date"])
    as_of_date = as_of or (date.fromisoformat(observations[-1]["date"]) if observations else start_date or date.today())
    phase_summaries = _phase_summaries(observations, int(trial_row["min_observations_per_phase"] or 0))
    current_phase = _current_phase(phase_summaries, as_of_date)
    current_phase_name = current_phase["phase"] if current_phase else None

    day_in_trial = None
    if start_date:
        day_in_trial = (as_of_date - start_date).days + 1

    day_in_phase = None
    if current_phase:
        day_in_phase = (as_of_date - date.fromisoformat(current_phase["start"])).days + 1

    compliance_values = [float(row["compliance_score"]) for row in observations if row["compliance_score"] is not None]
    issues = []
    for phase in phase_summaries:
        if phase["observations"] < phase["required"]:
            issues.append(
                {
                    "type": "low_observations",
                    "phase": phase["phase"],
                    "message": (
                        f"{phase['phase']} phase has {phase['observations']} observations, "
                        f"below required minimum of {phase['required']}."
                    ),
                }
            )
        if phase["compliance"] is not None and phase["compliance"] < 0.8:
            issues.append(
                {
                    "type": "low_compliance",
                    "phase": phase["phase"],
                    "message": f"{phase['phase']} compliance is {phase['compliance']:.2f}, below 0.80.",
                }
            )

    next_action = None
    if trial_row["status"] == "active":
        next_action = f"Continue logging {trial_row['primary_outcome_metric']} during {current_phase_name} phase."

    return {
        "trial_id": trial_row["id"],
        "name": trial_row["name"],
        "hypothesis": trial_row["hypothesis"],
        "status": trial_row["status"],
        "design": trial_row["design"],
        "as_of_date": as_of_date.isoformat(),
        "current_phase": current_phase_name,
        "day_in_phase": day_in_phase,
        "day_in_trial": day_in_trial,
        "total_observations": len(observations),
        "compliance_summary": {
            "overall": round(sum(compliance_values) / len(compliance_values), 2) if compliance_values else None,
            "by_phase": {
                phase["phase"]: {
                    "score": phase["compliance"],
                    "observations": phase["observations"],
                    "required": phase["required"],
                    "complete": phase["complete"],
                }
                for phase in phase_summaries
            },
        },
        "phase_schedule": [
            {
                "phase": phase["phase"],
                "start": phase["start"],
                "end": phase["end"],
                "observations": phase["observations"],
                "status": "in_progress" if phase["phase"] == current_phase_name else "complete",
            }
            for phase in phase_summaries
        ],
        "issues": issues,
        "next_action": next_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Get grounded trial status")
    parser.add_argument("--trial-id", type=int, help="Specific trial id to inspect")
    parser.add_argument("--all-active", action="store_true", help="Return all active trials")
    parser.add_argument("--as-of-date", help="Date for status summary in YYYY-MM-DD")
    args = parser.parse_args()

    if not args.trial_id and not args.all_active:
        parser.error("Pass either --trial-id or --all-active")

    conn = _connect()
    try:
        as_of = _parse_date(args.as_of_date)
        if args.all_active:
            trial_rows = conn.execute(
                "SELECT * FROM trials WHERE status = 'active' ORDER BY start_date"
            ).fetchall()
            result = []
            for trial in trial_rows:
                observations = conn.execute(
                    "SELECT * FROM trial_observations WHERE trial_id = ? ORDER BY date, metric_name",
                    (trial["id"],),
                ).fetchall()
                result.append(build_trial_status(trial, observations, as_of))
            print(json.dumps({"trials": result}, ensure_ascii=False, indent=2))
            return 0

        trial = conn.execute("SELECT * FROM trials WHERE id = ?", (args.trial_id,)).fetchone()
        if not trial:
            raise ValueError(f"Trial {args.trial_id} not found")
        observations = conn.execute(
            "SELECT * FROM trial_observations WHERE trial_id = ? ORDER BY date, metric_name",
            (args.trial_id,),
        ).fetchall()
        print(json.dumps(build_trial_status(trial, observations, as_of), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
