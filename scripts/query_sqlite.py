#!/usr/bin/env python3
"""
Run a single read-only SQLite query against the TaiYiYuan runtime database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path


def _load_sql(args: argparse.Namespace) -> str:
    if args.sql:
        sql = args.sql
    elif args.input and args.input != "-":
        sql = Path(args.input).read_text(encoding="utf-8")
    else:
        sql = sys.stdin.read()

    sql = sql.strip()
    if not sql:
        raise ValueError("Expected SQL from --sql, --input, or stdin")
    return sql


def _validate_read_only(sql: str) -> str:
    normalized = sql.strip().rstrip(";").lstrip()
    head = normalized.lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise ValueError("Only single read-only SELECT statements are allowed")
    return normalized


def _load_params(raw: str | None) -> list:
    if raw is None:
        return []
    params = json.loads(raw)
    if not isinstance(params, list):
        raise ValueError("--params must be a JSON array")
    return params


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only query against TaiYiYuan SQLite")
    parser.add_argument("--sql", help="SQL to execute")
    parser.add_argument("--input", default="-", help="Path to a SQL file, or '-' to read from stdin")
    parser.add_argument("--params", help="JSON array of positional parameters")
    args = parser.parse_args()

    try:
        sql = _validate_read_only(_load_sql(args))
        params = _load_params(args.params)

        conn = sqlite3.connect(str(get_db_path()))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()

        payload = {
            "status": "success",
            "database": str(get_db_path()),
            "row_count": len(rows),
            "rows": [dict(row) for row in rows],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
