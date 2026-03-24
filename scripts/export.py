#!/usr/bin/env python3
"""
TaiYiYuan (太医院) — Full data export to JSON or CSV.

Usage:
    python export.py                                    # Export all tables to export/
    python export.py --table diet_entries                # Export single table
    python export.py --format csv                       # Export as CSV
    python export.py --output /path/to/dir              # Custom output directory
    python export.py --date-range 2026-01-01 2026-03-12 # Filter by date
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path, get_project_root

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = str(get_db_path())
PROJECT_ROOT = get_project_root()

# Tables and their timestamp columns for date filtering
TABLE_DATE_COLUMNS: dict[str, str | None] = {
    "schema_version": None,
    "diet_entries": "timestamp",
    "diet_ingredients": "created_at",
    "recipe_library": "created_at",
    "exercise_entries": "timestamp",
    "exercise_details": None,  # Joined via entry_id
    "body_metrics": "timestamp",
    "custom_metric_definitions": "created_at",
    "biomarkers": "timestamp",
    "supplements": "start_date",
    "trials": "created_at",
    "trial_observations": "date",
    "insights": "timestamp",
    "model_runs": "timestamp",
    "model_cache": "computed_at",
    "nutrition_cache": "fetched_at",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_tables(conn: sqlite3.Connection) -> list[str]:
    """Return list of user table names."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _get_column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names for a table."""
    cursor = conn.execute(f"PRAGMA table_info([{table}])")
    return [row[1] for row in cursor.fetchall()]


def _query_table(
    conn: sqlite3.Connection,
    table: str,
    date_start: str | None = None,
    date_end: str | None = None,
) -> list[dict]:
    """Query all rows from a table, optionally filtered by date range."""
    date_col = TABLE_DATE_COLUMNS.get(table)

    if date_start and date_end and date_col:
        query = f"SELECT * FROM [{table}] WHERE [{date_col}] >= ? AND [{date_col}] <= ? ORDER BY [{date_col}]"
        cursor = conn.execute(query, (date_start, date_end + "T23:59:59"))
    elif date_start and date_col:
        query = f"SELECT * FROM [{table}] WHERE [{date_col}] >= ? ORDER BY [{date_col}]"
        cursor = conn.execute(query, (date_start,))
    elif date_end and date_col:
        query = f"SELECT * FROM [{table}] WHERE [{date_col}] <= ? ORDER BY [{date_col}]"
        cursor = conn.execute(query, (date_end + "T23:59:59",))
    else:
        query = f"SELECT * FROM [{table}]"
        cursor = conn.execute(query)

    columns = _get_column_names(conn, table)
    rows = []
    for row in cursor.fetchall():
        rows.append(dict(zip(columns, row)))
    return rows


# ---------------------------------------------------------------------------
# Export functions
# ---------------------------------------------------------------------------

def export_table_json(rows: list[dict], output_path: Path):
    """Write rows to a JSON file."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False, default=str)


def export_table_csv(rows: list[dict], output_path: Path):
    """Write rows to a CSV file."""
    if not rows:
        output_path.write_text("", encoding="utf-8")
        return

    fieldnames = list(rows[0].keys())
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_manifest(
    output_dir: Path,
    table_stats: dict[str, int],
    schema_version: int,
    fmt: str,
    date_start: str | None,
    date_end: str | None,
):
    """Write an export manifest file."""
    manifest = {
        "export_timestamp": _now_iso(),
        "schema_version": schema_version,
        "format": fmt,
        "date_filter": {
            "start": date_start,
            "end": date_end,
        } if date_start or date_end else None,
        "tables": {
            name: {"rows": count, "file": f"{name}.{fmt}"}
            for name, count in table_stats.items()
        },
        "total_rows": sum(table_stats.values()),
    }

    manifest_path = output_dir / "export_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return manifest_path


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_export(
    table_filter: str | None,
    fmt: str,
    output_dir: str | None,
    date_start: str | None,
    date_end: str | None,
):
    """Export data from the database."""
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found: {DB_PATH}")
        print("Run 'python setup.py' first.")
        sys.exit(1)

    # Determine output directory
    if output_dir:
        out_dir = Path(output_dir)
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = PROJECT_ROOT / "data" / "exports" / today

    out_dir.mkdir(parents=True, exist_ok=True)

    print("TaiYiYuan data export")
    print("=" * 60)
    print(f"Database:   {DB_PATH}")
    print(f"Output:     {out_dir}")
    print(f"Format:     {fmt.upper()}")
    if date_start or date_end:
        print(f"Date range: {date_start or '...'} to {date_end or '...'}")

    conn = sqlite3.connect(DB_PATH)
    schema_version = _get_schema_version(conn)
    all_tables = _get_tables(conn)

    # Filter to single table if requested
    if table_filter:
        if table_filter not in all_tables:
            print(f"\nERROR: Table '{table_filter}' not found.")
            print(f"Available tables: {', '.join(all_tables)}")
            conn.close()
            sys.exit(1)
        tables = [table_filter]
    else:
        tables = all_tables

    print(f"\nExporting {len(tables)} table(s)...\n")

    table_stats: dict[str, int] = {}
    total_rows = 0

    for table in tables:
        rows = _query_table(conn, table, date_start, date_end)
        count = len(rows)
        table_stats[table] = count
        total_rows += count

        ext = fmt
        output_path = out_dir / f"{table}.{ext}"

        if fmt == "json":
            export_table_json(rows, output_path)
        elif fmt == "csv":
            export_table_csv(rows, output_path)

        size = output_path.stat().st_size
        flag = "" if count > 0 else "  (empty)"
        print(f"  {table:35s} {count:>6,} rows  ({size:>8,} bytes){flag}")

    # Write manifest
    manifest_path = write_manifest(
        out_dir, table_stats, schema_version, fmt, date_start, date_end
    )

    conn.close()

    print(f"\n{'=' * 60}")
    print(f"Export complete.")
    print(f"  Tables:   {len(tables)}")
    print(f"  Rows:     {total_rows:,}")
    print(f"  Schema:   v{schema_version}")
    print(f"  Manifest: {manifest_path}")
    print(f"  Output:   {out_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TaiYiYuan data export to JSON or CSV"
    )
    parser.add_argument(
        "--table", metavar="NAME",
        help="Export a single table (default: all tables)"
    )
    parser.add_argument(
        "--format", choices=["json", "csv"], default="json",
        help="Output format (default: json)"
    )
    parser.add_argument(
        "--output", metavar="DIR",
        help="Output directory (default: data/exports/YYYY-MM-DD/)"
    )
    parser.add_argument(
        "--date-range", nargs=2, metavar=("START", "END"),
        help="Filter by date range: YYYY-MM-DD YYYY-MM-DD"
    )
    args = parser.parse_args()

    date_start = None
    date_end = None
    if args.date_range:
        date_start, date_end = args.date_range
        # Validate date formats
        for d in (date_start, date_end):
            try:
                datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                print(f"ERROR: Invalid date format: {d}. Use YYYY-MM-DD.")
                sys.exit(1)

    cmd_export(
        table_filter=args.table,
        fmt=args.format,
        output_dir=args.output,
        date_start=date_start,
        date_end=date_end,
    )


if __name__ == "__main__":
    main()
