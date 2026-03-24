#!/usr/bin/env python3
"""
TaiYiYuan (太医院) — Demo reset and seed workflow.

Resets the database, seeds deterministic demo data, and verifies the hero
scenes used by the README and demo script.

Usage:
    python scripts/demo_reset.py
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import describe_runtime_paths, get_db_path


DB_PATH = get_db_path()
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup.py"
SEED_SCRIPT = REPO_ROOT / "scripts" / "generate_demo_data.py"


TABLE_MIN_COUNTS = {
    "diet_entries": 250,
    "diet_ingredients": 600,
    "exercise_entries": 20,
    "body_metrics": 300,
    "biomarkers": 20,
    "supplements": 3,
    "trials": 2,
    "trial_observations": 40,
    "insights": 6,
}

HERO_QUERIES = [
    (
        "completed protein-sleep trial",
        "SELECT COUNT(*) FROM trials WHERE name = ? AND status = ?",
        ("Protein-Sleep Quality Trial", "completed"),
        1,
    ),
    (
        "active creatine trial",
        "SELECT COUNT(*) FROM trials WHERE name = ? AND status = ?",
        ("Creatine-Cognition Trial", "active"),
        1,
    ),
    (
        "protein-sleep insight",
        "SELECT COUNT(*) FROM insights WHERE description LIKE ?",
        ("%protein%sleep%",),
        1,
    ),
]


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.rstrip())
        raise SystemExit(result.returncode)


def verify_seed() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        print("\n3. Verifying seeded database...")
        for table, min_rows in TABLE_MIN_COUNTS.items():
            count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            status = "OK" if count >= min_rows else "FAIL"
            print(f"  [{status}] {table:20s} {count:>5} rows (expected >= {min_rows})")
            if count < min_rows:
                raise SystemExit(1)

        for label, sql, params, minimum in HERO_QUERIES:
            count = conn.execute(sql, params).fetchone()[0]
            status = "OK" if count >= minimum else "FAIL"
            print(f"  [{status}] {label:28s} {count:>5} matches")
            if count < minimum:
                raise SystemExit(1)

        date_bounds = conn.execute(
            "SELECT MIN(SUBSTR(timestamp, 1, 10)), MAX(SUBSTR(timestamp, 1, 10)) FROM diet_entries"
        ).fetchone()
        print(
            f"  [OK] diet date coverage          {date_bounds[0]} -> {date_bounds[1]}"
        )
    finally:
        conn.close()


def main() -> None:
    print("=" * 70)
    print("TaiYiYuan (太医院) — Demo Reset Workflow")
    print("=" * 70)
    print("\nResolved runtime paths:")
    for name, value in describe_runtime_paths().items():
        print(f"  {name:12s} {value}")

    print("\n1. Resetting and initializing database...")
    run([sys.executable, str(SETUP_SCRIPT), "--reset", "--confirm"])

    print("\n2. Seeding deterministic demo data...")
    run([sys.executable, str(SEED_SCRIPT), "--skip-reset"])

    verify_seed()

    print("\nDemo database is ready.")
    print(f"Database: {DB_PATH}")


if __name__ == "__main__":
    main()
