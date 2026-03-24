#!/usr/bin/env python3
"""
TaiYiYuan (太医院) — Database initialization and project setup.

Usage:
    python setup.py              # Full setup
    python setup.py --check      # Verify setup is complete
    python setup.py --reset      # Reset database (requires --confirm)
"""

import argparse
import os
import shutil
import sqlite3
import stat
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
SCHEMA_PATH = SKILL_ROOT / "data" / "schema.sql"

PROJECT_ROOT = get_project_root()
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"

# Directories that must exist for the project
REQUIRED_DIRS = [
    PROJECT_ROOT / "data",
    PROJECT_ROOT / "data" / "backups",
    PROJECT_ROOT / "data" / "exports",
    PROJECT_ROOT / "reports",
    PROJECT_ROOT / "trials",
    PROJECT_ROOT / "photos",
]

# Tables expected after running 001_init.sql
EXPECTED_TABLES = [
    "schema_version",
    "diet_entries",
    "diet_ingredients",
    "recipe_library",
    "exercise_entries",
    "exercise_details",
    "body_metrics",
    "custom_metric_definitions",
    "biomarkers",
    "supplements",
    "trials",
    "trial_observations",
    "insights",
    "model_runs",
    "model_cache",
    "nutrition_cache",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_ok(msg: str):
    print(f"  [OK] {msg}")


def _print_fail(msg: str):
    print(f"  [!!] {msg}")


def _print_info(msg: str):
    print(f"  [--] {msg}")


def _get_tables(conn: sqlite3.Connection) -> list[str]:
    """Return list of user table names in the database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows]


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Return current schema version, or 0 if table doesn't exist."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _backup_db(label: str = "pre-reset") -> str | None:
    """Create a safety backup of the database. Returns backup path or None."""
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

def cmd_setup():
    """Full project setup: create dirs, run initial migration, verify."""
    print("TaiYiYuan setup")
    print("=" * 60)

    # 1. Create directories
    print("\n1. Creating project directories...")
    for d in REQUIRED_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        _print_ok(str(d))

    # 2. Verify migration files exist
    print("\n2. Checking migration files...")
    init_migration = MIGRATIONS_DIR / "001_init.sql"
    if not init_migration.exists():
        _print_fail(f"Initial migration not found: {init_migration}")
        sys.exit(1)
    _print_ok(f"Found {init_migration.name}")

    # 3. Run initial migration
    print("\n3. Initializing database...")
    db_existed = os.path.exists(DB_PATH)
    if db_existed:
        _print_info("Database already exists, checking schema...")
    else:
        _print_info(f"Creating new database at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    version = _get_schema_version(conn)
    if version == 0:
        _print_info("Running initial migration (001_init.sql)...")
        sql = init_migration.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            _print_ok("Migration 001 applied successfully")
        except sqlite3.Error as e:
            _print_fail(f"Migration failed: {e}")
            conn.close()
            sys.exit(1)
    else:
        _print_ok(f"Schema already at version {version}")

    # 4. Set file permissions
    print("\n4. Setting file permissions...")
    try:
        os.chmod(DB_PATH, 0o600)
        _print_ok(f"Database permissions set to 0600")
    except OSError as e:
        _print_fail(f"Could not set permissions: {e}")

    # 5. Verify tables
    print("\n5. Verifying tables...")
    tables = _get_tables(conn)
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    if missing:
        _print_fail(f"Missing tables: {', '.join(missing)}")
    else:
        _print_ok(f"All {len(EXPECTED_TABLES)} tables present")

    version = _get_schema_version(conn)
    conn.close()

    # 6. Summary
    print("\n" + "=" * 60)
    print("Setup summary:")
    print(f"  Database:       {DB_PATH}")
    print(f"  Schema version: {version}")
    print(f"  Tables:         {len(tables)}")
    print(f"  Directories:    {len(REQUIRED_DIRS)} created/verified")
    db_size = os.path.getsize(DB_PATH)
    print(f"  DB size:        {db_size:,} bytes")
    print(f"  Permissions:    {oct(os.stat(DB_PATH).st_mode & 0o777)}")
    print("=" * 60)
    print("Setup complete.")


def cmd_check():
    """Verify that setup is complete and healthy."""
    print("TaiYiYuan setup check")
    print("=" * 60)
    all_ok = True

    # Check database exists
    print("\nDatabase:")
    if os.path.exists(DB_PATH):
        _print_ok(f"Exists: {DB_PATH}")
        db_size = os.path.getsize(DB_PATH)
        _print_info(f"Size: {db_size:,} bytes")
    else:
        _print_fail(f"Database not found: {DB_PATH}")
        all_ok = False
        print("\nRun 'python setup.py' to initialize.")
        sys.exit(1)

    # Check permissions
    print("\nPermissions:")
    mode = os.stat(DB_PATH).st_mode & 0o777
    if mode == 0o600:
        _print_ok(f"File permissions: {oct(mode)} (owner read/write only)")
    else:
        _print_fail(f"File permissions: {oct(mode)} (expected 0o600)")
        all_ok = False

    # Check schema
    print("\nSchema:")
    conn = sqlite3.connect(DB_PATH)
    version = _get_schema_version(conn)
    if version > 0:
        _print_ok(f"Schema version: {version}")
    else:
        _print_fail("No schema version found")
        all_ok = False

    # Check tables
    print("\nTables:")
    tables = _get_tables(conn)
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    extra = [t for t in tables if t not in EXPECTED_TABLES]
    if not missing:
        _print_ok(f"All {len(EXPECTED_TABLES)} expected tables present")
    else:
        _print_fail(f"Missing: {', '.join(missing)}")
        all_ok = False
    if extra:
        _print_info(f"Additional tables: {', '.join(extra)}")

    # Check row counts
    print("\nRow counts:")
    for table in sorted(tables):
        if table == "schema_version":
            continue
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            if count > 0:
                _print_info(f"{table}: {count:,} rows")
        except sqlite3.Error:
            pass
    conn.close()

    # Check directories
    print("\nDirectories:")
    for d in REQUIRED_DIRS:
        if d.exists():
            _print_ok(str(d))
        else:
            _print_fail(f"Missing: {d}")
            all_ok = False

    # Check migration files
    print("\nMigrations:")
    if MIGRATIONS_DIR.exists():
        migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
        _print_ok(f"{len(migrations)} migration file(s) found")
        for m in migrations:
            _print_info(f"  {m.name}")
    else:
        _print_fail(f"Migrations directory not found: {MIGRATIONS_DIR}")
        all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("All checks passed.")
    else:
        print("Some checks failed. Run 'python setup.py' to fix.")
    sys.exit(0 if all_ok else 1)


def cmd_reset(confirm: bool):
    """Reset the database: back up, drop all tables, re-run migrations."""
    if not confirm:
        print("ERROR: --reset requires --confirm flag to prevent accidental data loss.")
        print("Usage: python setup.py --reset --confirm")
        sys.exit(1)

    print("TaiYiYuan database reset")
    print("=" * 60)
    print("WARNING: This will destroy all existing data.\n")

    # Back up current database
    if os.path.exists(DB_PATH):
        print("1. Backing up current database...")
        backup_path = _backup_db("pre-reset")
        if backup_path:
            _print_ok(f"Backup saved: {backup_path}")
        else:
            _print_info("No existing database to back up")

        # Remove current database and WAL/SHM files
        print("\n2. Removing existing database...")
        for suffix in ("", "-wal", "-shm"):
            p = DB_PATH + suffix
            if os.path.exists(p):
                os.remove(p)
                _print_ok(f"Removed: {p}")
    else:
        _print_info("No existing database found")

    # Re-run setup
    print("\n3. Re-running setup...\n")
    cmd_setup()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TaiYiYuan database initialization and project setup"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Verify setup is complete without making changes"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Reset database (drops all data, re-runs migrations)"
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required with --reset to confirm destructive operation"
    )
    args = parser.parse_args()

    if args.check:
        cmd_check()
    elif args.reset:
        cmd_reset(confirm=args.confirm)
    else:
        cmd_setup()


if __name__ == "__main__":
    main()
