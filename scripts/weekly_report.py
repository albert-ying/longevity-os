#!/usr/bin/env python3
"""
Generate a grounded weekly report directly from stored TaiYiYuan rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path, get_reports_dir


def _date_or_die(value: str) -> date:
    return date.fromisoformat(value)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _sum_or_zero(value):
    return float(value) if value is not None else 0.0


def _fetch_daily_series(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple,
) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def _completeness(days_with_data: int, total_days: int) -> str:
    return f"{days_with_data}/{total_days} days"


def _weight_trend(conn: sqlite3.Connection, start: str, end: str) -> str | None:
    rows = conn.execute(
        """
        SELECT SUBSTR(timestamp, 1, 10) AS day, AVG(value) AS value
        FROM body_metrics
        WHERE metric_type = 'weight' AND SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
        GROUP BY SUBSTR(timestamp, 1, 10)
        ORDER BY day
        """,
        (start, end),
    ).fetchall()
    if len(rows) < 2:
        return None
    first = float(rows[0]["value"])
    last = float(rows[-1]["value"])
    delta = last - first
    direction = "stable"
    if abs(delta) >= 0.2:
        direction = "up" if delta > 0 else "down"
    return f"Weight {direction} from {first:.1f} to {last:.1f} kg"


def _sleep_summary(conn: sqlite3.Connection, start: str, end: str) -> str | None:
    row = conn.execute(
        """
        SELECT AVG(value) AS avg_sleep, COUNT(DISTINCT SUBSTR(timestamp, 1, 10)) AS days
        FROM body_metrics
        WHERE metric_type = 'sleep_duration' AND SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()
    if not row or row["avg_sleep"] is None:
        return None
    return f"Sleep averaged {float(row['avg_sleep']):.1f} hours across {row['days']} days"


def build_weekly_report(start: date, end: date) -> dict:
    total_days = (end - start).days + 1
    start_s = start.isoformat()
    end_s = end.isoformat()
    previous_start = (start - timedelta(days=7)).isoformat()
    previous_end = (end - timedelta(days=7)).isoformat()

    conn = _connect()
    try:
        diet_days = _fetch_daily_series(
            conn,
            """
            SELECT SUBSTR(timestamp, 1, 10) AS day,
                   SUM(total_calories) AS calories,
                   SUM(total_protein_g) AS protein_g,
                   SUM(total_carbs_g) AS carbs_g,
                   SUM(total_fat_g) AS fat_g
            FROM diet_entries
            WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
            GROUP BY SUBSTR(timestamp, 1, 10)
            ORDER BY day
            """,
            (start_s, end_s),
        )
        exercise_days = _fetch_daily_series(
            conn,
            """
            SELECT SUBSTR(timestamp, 1, 10) AS day,
                   COUNT(*) AS sessions,
                   SUM(duration_minutes) AS minutes
            FROM exercise_entries
            WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
            GROUP BY SUBSTR(timestamp, 1, 10)
            ORDER BY day
            """,
            (start_s, end_s),
        )
        metric_days = _fetch_daily_series(
            conn,
            """
            SELECT SUBSTR(timestamp, 1, 10) AS day, COUNT(*) AS entries
            FROM body_metrics
            WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
            GROUP BY SUBSTR(timestamp, 1, 10)
            ORDER BY day
            """,
            (start_s, end_s),
        )
        biomarkers = conn.execute(
            """
            SELECT COUNT(*) AS rows
            FROM biomarkers
            WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
            """,
            (start_s, end_s),
        ).fetchone()
        active_supplements = conn.execute(
            "SELECT COUNT(*) AS rows FROM supplements WHERE end_date IS NULL"
        ).fetchone()
        insights = conn.execute(
            """
            SELECT description
            FROM insights
            WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
            ORDER BY evidence_level DESC, timestamp DESC
            LIMIT 3
            """,
            (start_s, end_s),
        ).fetchall()
        active_trials = conn.execute(
            "SELECT id, name, primary_outcome_metric FROM trials WHERE status = 'active' ORDER BY start_date"
        ).fetchall()
        previous_protein = conn.execute(
            """
            SELECT AVG(protein_g) AS avg_protein
            FROM (
              SELECT SUBSTR(timestamp, 1, 10) AS day, SUM(total_protein_g) AS protein_g
              FROM diet_entries
              WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
              GROUP BY SUBSTR(timestamp, 1, 10)
            )
            """,
            (previous_start, previous_end),
        ).fetchone()

        avg_daily_calories = (
            sum(_sum_or_zero(row["calories"]) for row in diet_days) / len(diet_days)
            if diet_days
            else 0.0
        )
        avg_daily_protein = (
            sum(_sum_or_zero(row["protein_g"]) for row in diet_days) / len(diet_days)
            if diet_days
            else 0.0
        )
        total_exercise_minutes = int(
            sum(int(row["minutes"] or 0) for row in exercise_days)
        )
        exercise_sessions = int(
            sum(int(row["sessions"] or 0) for row in exercise_days)
        )

        trial_lines = []
        for trial in active_trials:
            obs = conn.execute(
                """
                SELECT phase, COUNT(*) AS observations, AVG(compliance_score) AS compliance
                FROM trial_observations
                WHERE trial_id = ? AND date BETWEEN ? AND ?
                GROUP BY phase
                ORDER BY MIN(date)
                """,
                (trial["id"], start_s, end_s),
            ).fetchall()
            if obs:
                phase = obs[-1]["phase"]
                observations = sum(int(row["observations"] or 0) for row in obs)
                compliance = obs[-1]["compliance"]
                trial_lines.append(
                    f"{trial['name']}: {phase} phase, {observations} observations this week, "
                    f"latest compliance {float(compliance or 0):.2f}"
                )
            else:
                trial_lines.append(f"{trial['name']}: active, no observations in this range")

        key_trends = []
        weight_trend = _weight_trend(conn, start_s, end_s)
        if weight_trend:
            key_trends.append(weight_trend)
        sleep_summary = _sleep_summary(conn, start_s, end_s)
        if sleep_summary:
            key_trends.append(sleep_summary)
        if avg_daily_protein:
            previous_avg = previous_protein["avg_protein"] if previous_protein else None
            if previous_avg is not None:
                delta = avg_daily_protein - float(previous_avg)
                key_trends.append(
                    f"Average protein was {avg_daily_protein:.0f} g/day ({delta:+.0f} g vs previous week)"
                )
            else:
                key_trends.append(f"Average protein was {avg_daily_protein:.0f} g/day")

        alerts = []
        high_alert_metrics = conn.execute(
            """
            SELECT metric_type, value, SUBSTR(timestamp, 1, 10) AS day
            FROM body_metrics
            WHERE metric_type IN ('resting_hr', 'blood_pressure_sys', 'blood_pressure_dia')
              AND SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?
              AND (
                (metric_type = 'resting_hr' AND value > 100) OR
                (metric_type = 'blood_pressure_sys' AND value >= 140) OR
                (metric_type = 'blood_pressure_dia' AND value >= 90)
              )
            ORDER BY timestamp DESC
            LIMIT 3
            """,
            (start_s, end_s),
        ).fetchall()
        for row in high_alert_metrics:
            alerts.append(
                f"{row['metric_type']} hit {float(row['value']):.0f} on {row['day']}"
            )

        markdown_lines = [
            f"# Weekly Health Report: {start.strftime('%b %-d')} - {end.strftime('%b %-d, %Y')}",
            "",
            "## Overview",
            f"- Diet logged on {_completeness(len(diet_days), total_days)}",
            f"- Exercise logged on {_completeness(len(exercise_days), total_days)}",
            f"- Metrics logged on {_completeness(len(metric_days), total_days)}",
            "",
            "## Diet",
            f"- Average daily calories: {avg_daily_calories:.0f} kcal",
            f"- Average daily protein: {avg_daily_protein:.0f} g",
            "",
            "## Exercise",
            f"- Sessions: {exercise_sessions}",
            f"- Total minutes: {total_exercise_minutes}",
            "",
            "## Body Metrics",
        ]
        if weight_trend:
            markdown_lines.append(f"- {weight_trend}")
        if sleep_summary:
            markdown_lines.append(f"- {sleep_summary}")
        if not weight_trend and not sleep_summary:
            markdown_lines.append("- No weight or sleep metrics recorded in this period")

        markdown_lines.extend(
            [
                "",
                "## Biomarkers",
                (
                    f"- {int(biomarkers['rows'])} biomarker rows recorded this week"
                    if biomarkers and biomarkers["rows"]
                    else "- No biomarker rows recorded this week"
                ),
                "",
                "## Supplements",
                f"- Active stack size: {int(active_supplements['rows'])}",
                "",
                "## Active Trials",
            ]
        )
        markdown_lines.extend(
            [f"- {line}" for line in trial_lines] or ["- No active trials"]
        )
        markdown_lines.extend(["", "## Cross-Module Insights"])
        markdown_lines.extend(
            [f"- {row['description']}" for row in insights] or ["- No insights generated in this period"]
        )
        markdown_lines.extend(["", "## Alerts"])
        markdown_lines.extend([f"- {alert}" for alert in alerts] or ["- No alerts in this period"])

        markdown = "\n".join(markdown_lines) + "\n"

        reports_dir = get_reports_dir()
        reports_dir.mkdir(parents=True, exist_ok=True)
        iso_year, iso_week, _ = start.isocalendar()
        report_path = reports_dir / f"weekly-{iso_year}-W{iso_week:02d}.md"
        report_path.write_text(markdown, encoding="utf-8")

        return {
            "report_type": "weekly",
            "date_range": {"start": start_s, "end": end_s},
            "markdown": markdown,
            "saved_to": str(report_path),
            "summary": {
                "data_completeness": {
                    "diet": _completeness(len(diet_days), total_days),
                    "exercise": _completeness(len(exercise_days), total_days),
                    "metrics": _completeness(len(metric_days), total_days),
                },
                "key_trends": key_trends,
                "alerts": alerts,
                "trial_progress": trial_lines,
            },
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a grounded weekly report")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    try:
        result = build_weekly_report(_date_or_die(args.start), _date_or_die(args.end))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
