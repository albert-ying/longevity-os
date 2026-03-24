#!/usr/bin/env python3
"""
TaiYiYuan (太医院) — Schema migration runner.

Usage:
    python migrate.py              # Run pending migrations
    python migrate.py --status     # Show current version and pending migrations
    python migrate.py --rollback   # Not implemented (prints warning)
"""

import argparse
import os
import re
import shutil
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
SKILL_ROOT = REPO_ROOT
MIGRATIONS_DIR = SKILL_ROOT / "data" / "migrations"
BACKUP_DIR = get_project_root() / "data" / "backups"

# Pattern: 001_name.sql, 002_name.sql, etc.
MIGRATION_PATTERN = re.compile(r"^(\d{3})_.+\.sql$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Return current schema version, or 0 if table doesn't exist."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _discover_migrations() -> list[tuple[int, Path]]:
    """Scan migrations directory and return sorted list of (version, path)."""
    if not MIGRATIONS_DIR.exists():
        print(f"ERROR: Migrations directory not found: {MIGRATIONS_DIR}")
        sys.exit(1)

    migrations = []
    for f in sorted(MIGRATIONS_DIR.iterdir()):
        match = MIGRATION_PATTERN.match(f.name)
        if match:
            version = int(match.group(1))
            migrations.append((version, f))

    migrations.sort(key=lambda x: x[0])
    return migrations


def _backup_db(label: str) -> str | None:
    """Create a safety backup before applying migration."""
    if not os.path.exists(DB_PATH):
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"taiyiyuan-{label}-{ts}.db"
    backup_path = BACKUP_DIR / backup_name
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copy2(DB_PATH, backup_path)
    return str(backup_path)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status():
    """Show current schema version and list pending migrations."""
    print("TaiYiYuan migration status")
    print("=" * 60)

    all_migrations = _discover_migrations()

    if not os.path.exists(DB_PATH):
        print(f"\nDatabase not found: {DB_PATH}")
        print("Run 'python setup.py' first to initialize.\n")
        print(f"Pending migrations: {len(all_migrations)}")
        for version, path in all_migrations:
            print(f"  [{version:03d}] {path.name}")
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    current = _get_schema_version(conn)

    # List applied versions
    print(f"\nCurrent schema version: {current}")
    try:
        rows = conn.execute(
            "SELECT version, applied_at FROM schema_version ORDER BY version"
        ).fetchall()
        if rows:
            print("\nApplied migrations:")
            for row in rows:
                # Find the matching file name
                name = "???"
                for v, p in all_migrations:
                    if v == row[0]:
                        name = p.name
                        break
                print(f"  [{row[0]:03d}] {name}  (applied {row[1]})")
    except sqlite3.OperationalError:
        print("  (no schema_version table)")

    pending = [(v, p) for v, p in all_migrations if v > current]
    if pending:
        print(f"\nPending migrations: {len(pending)}")
        for version, path in pending:
            print(f"  [{version:03d}] {path.name}")
    else:
        print("\nNo pending migrations. Schema is up to date.")

    conn.close()


def cmd_migrate():
    """Run all pending migrations in order."""
    print("TaiYiYuan migration runner")
    print("=" * 60)

    all_migrations = _discover_migrations()

    if not os.path.exists(DB_PATH):
        print(f"\nDatabase not found: {DB_PATH}")
        print("Run 'python setup.py' first to initialize.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    current = _get_schema_version(conn)

    print(f"\nCurrent schema version: {current}")

    pending = [(v, p) for v, p in all_migrations if v > current]
    if not pending:
        print("No pending migrations. Schema is up to date.")
        conn.close()
        return

    print(f"Pending migrations: {len(pending)}\n")

    applied = 0
    for version, path in pending:
        print(f"--- Applying migration {version:03d}: {path.name} ---")

        # Back up before each migration
        backup_path = _backup_db(f"pre-migration-{version:03d}")
        if backup_path:
            print(f"  Backup: {backup_path}")

        # Read and execute
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
        except sqlite3.Error as e:
            print(f"  FAILED: {e}")
            print(f"\n  Database may be in an inconsistent state.")
            print(f"  Restore from backup: {backup_path}")
            conn.close()
            sys.exit(1)

        # Verify version was updated
        new_version = _get_schema_version(conn)
        if new_version >= version:
            print(f"  Schema version: {new_version}")
            print(f"  SUCCESS")
            applied += 1
        else:
            print(f"  WARNING: Expected version >= {version}, got {new_version}")
            print(f"  The migration may not have updated schema_version.")
            print(f"  Inserting version record manually...")
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (version, _now_iso()),
                )
                conn.commit()
                applied += 1
                print(f"  Manually set version to {version}")
            except sqlite3.Error as e:
                print(f"  Could not update version: {e}")
                conn.close()
                sys.exit(1)

        print()

    final_version = _get_schema_version(conn)
    conn.close()

    print("=" * 60)
    print(f"Applied {applied} migration(s). Schema version: {final_version}")


def cmd_rollback():
    """Rollback is not implemented. Print a warning."""
    print("TaiYiYuan migration rollback")
    print("=" * 60)
    print()
    print("WARNING: Rollback is not implemented.")
    print()
    print("SQLite does not support DROP COLUMN or other DDL rollback well.")
    print("To revert a migration:")
    print()
    print("  1. List available backups:")
    print("     python backup.py --list")
    print()
    print("  2. Restore from a pre-migration backup:")
    print("     python backup.py --restore <backup-file> --confirm")
    print()
    print("  Backups are created automatically before each migration.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TaiYiYuan schema migration runner"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current schema version and pending migrations"
    )
    parser.add_argument(
        "--rollback", action="store_true",
        help="Rollback last migration (not implemented)"
    )
    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.rollback:
        cmd_rollback()
    else:
        cmd_migrate()


if __name__ == "__main__":
    main()
