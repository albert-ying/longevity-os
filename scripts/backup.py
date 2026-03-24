#!/usr/bin/env python3
"""
TaiYiYuan (太医院) — Automated backup with retention policy.

Usage:
    python backup.py                  # Create daily backup
    python backup.py --prune          # Prune old backups per retention policy
    python backup.py --list           # List existing backups with sizes
    python backup.py --restore <file> # Restore from backup (requires --confirm)
    python backup.py --force          # Create backup even if today's exists
"""

import argparse
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path, get_project_root

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = str(get_db_path())
BACKUP_DIR = get_project_root() / "data" / "backups"

# Backup filename pattern: taiyiyuan-YYYY-MM-DD.db
BACKUP_PATTERN = re.compile(r"^taiyiyuan-(\d{4}-\d{2}-\d{2})\.db$")

# Retention policy
RETENTION_DAILY = 30   # Keep last 30 daily backups
RETENTION_MONTHLY = 12  # Keep 12 monthly backups (1st of month)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _backup_filename(date_str: str | None = None) -> str:
    if date_str is None:
        date_str = _today_str()
    return f"taiyiyuan-{date_str}.db"


def _discover_backups() -> list[tuple[str, Path]]:
    """
    Find all backups matching the naming pattern.
    Returns list of (date_str, path) sorted by date ascending.
    """
    if not BACKUP_DIR.exists():
        return []

    backups = []
    for f in BACKUP_DIR.iterdir():
        m = BACKUP_PATTERN.match(f.name)
        if m:
            backups.append((m.group(1), f))

    backups.sort(key=lambda x: x[0])
    return backups


def _format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _verify_db(path: str | Path) -> bool:
    """Quick integrity check on a SQLite database."""
    try:
        conn = sqlite3.connect(str(path))
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return result[0] == "ok"
    except (sqlite3.Error, TypeError):
        return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_backup(force: bool = False):
    """Create a daily backup of the database."""
    print("TaiYiYuan backup")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found: {DB_PATH}")
        print("Nothing to back up.")
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    today = _today_str()
    backup_name = _backup_filename(today)
    backup_path = BACKUP_DIR / backup_name

    if backup_path.exists() and not force:
        print(f"Today's backup already exists: {backup_path}")
        print(f"Size: {_format_size(backup_path.stat().st_size)}")
        print("\nUse --force to overwrite.")
        return

    # Verify source database integrity
    print(f"Source:      {DB_PATH}")
    print(f"Destination: {backup_path}")
    print()

    print("Verifying source database integrity...")
    if not _verify_db(DB_PATH):
        print("WARNING: Source database integrity check failed!")
        print("Creating backup anyway, but the database may be corrupted.")

    # Use SQLite's backup API for a safe copy (handles WAL mode)
    print("Creating backup...")
    try:
        src_conn = sqlite3.connect(DB_PATH)
        dst_conn = sqlite3.connect(str(backup_path))
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
    except sqlite3.Error as e:
        print(f"ERROR: Backup failed: {e}")
        # Fall back to file copy
        print("Falling back to file copy...")
        try:
            shutil.copy2(DB_PATH, backup_path)
        except OSError as e2:
            print(f"ERROR: File copy also failed: {e2}")
            sys.exit(1)

    # Set permissions
    try:
        os.chmod(backup_path, 0o600)
    except OSError:
        pass

    # Verify backup
    print("Verifying backup integrity...")
    if _verify_db(backup_path):
        print("  Integrity check: PASSED")
    else:
        print("  WARNING: Backup integrity check failed!")

    db_size = os.path.getsize(DB_PATH)
    bk_size = backup_path.stat().st_size

    print(f"\nBackup complete.")
    print(f"  Original:  {_format_size(db_size)}")
    print(f"  Backup:    {_format_size(bk_size)}")
    print(f"  Location:  {backup_path}")


def cmd_list():
    """List existing backups with sizes and dates."""
    print("TaiYiYuan backups")
    print("=" * 60)

    backups = _discover_backups()

    if not backups:
        print("\nNo backups found.")
        print(f"Backup directory: {BACKUP_DIR}")
        return

    # Also list non-pattern backups (pre-migration, pre-reset, etc.)
    other_backups = []
    if BACKUP_DIR.exists():
        for f in sorted(BACKUP_DIR.iterdir()):
            if f.suffix == ".db" and not BACKUP_PATTERN.match(f.name):
                other_backups.append(f)

    print(f"\nBackup directory: {BACKUP_DIR}")
    print(f"\nDaily backups ({len(backups)}):")
    print(f"  {'Date':<14s} {'Size':>10s}  {'Integrity':<10s}")
    print(f"  {'-' * 14} {'-' * 10}  {'-' * 10}")

    total_size = 0
    for date_str, path in backups:
        size = path.stat().st_size
        total_size += size
        # Quick integrity check on most recent 3 only (for speed)
        integrity = ""
        if backups.index((date_str, path)) >= len(backups) - 3:
            integrity = "ok" if _verify_db(path) else "FAILED"
        print(f"  {date_str:<14s} {_format_size(size):>10s}  {integrity}")

    if other_backups:
        print(f"\nOther backups ({len(other_backups)}):")
        for f in other_backups:
            size = f.stat().st_size
            total_size += size
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {f.name:<40s} {_format_size(size):>10s}  ({mtime})")

    print(f"\nTotal: {len(backups) + len(other_backups)} backup(s), {_format_size(total_size)}")

    # Show retention info
    today = datetime.now()
    cutoff_daily = (today - timedelta(days=RETENTION_DAILY)).strftime("%Y-%m-%d")
    print(f"\nRetention policy:")
    print(f"  Daily:   last {RETENTION_DAILY} days (keep >= {cutoff_daily})")
    print(f"  Monthly: last {RETENTION_MONTHLY} months (1st of month)")


def cmd_prune():
    """Delete backups outside the retention window."""
    print("TaiYiYuan backup pruning")
    print("=" * 60)

    backups = _discover_backups()
    if not backups:
        print("\nNo backups found. Nothing to prune.")
        return

    today = datetime.now()
    cutoff_daily = (today - timedelta(days=RETENTION_DAILY)).strftime("%Y-%m-%d")

    # Determine which monthly backups to keep (1st of month, last N months)
    monthly_keep = set()
    for i in range(RETENTION_MONTHLY):
        # Go back i months from current month
        dt = today.replace(day=1) - timedelta(days=30 * i)
        first_of_month = dt.replace(day=1).strftime("%Y-%m-%d")
        monthly_keep.add(first_of_month)

    keep = []
    prune = []

    for date_str, path in backups:
        is_recent = date_str >= cutoff_daily
        is_monthly = date_str in monthly_keep

        if is_recent or is_monthly:
            keep.append((date_str, path))
        else:
            prune.append((date_str, path))

    print(f"\nTotal backups:    {len(backups)}")
    print(f"Keeping:          {len(keep)}")
    print(f"Pruning:          {len(prune)}")

    if not prune:
        print("\nNothing to prune. All backups are within retention window.")
        return

    print(f"\nBackups to remove:")
    freed = 0
    for date_str, path in prune:
        size = path.stat().st_size
        freed += size
        print(f"  {date_str}  ({_format_size(size)})")

    print(f"\nSpace to free: {_format_size(freed)}")
    print()

    # Execute pruning
    removed = 0
    for date_str, path in prune:
        try:
            path.unlink()
            removed += 1
        except OSError as e:
            print(f"  ERROR removing {path.name}: {e}")

    print(f"Removed {removed} backup(s). Freed {_format_size(freed)}.")


def cmd_restore(backup_file: str, confirm: bool):
    """Restore database from a backup."""
    if not confirm:
        print("ERROR: --restore requires --confirm flag to prevent accidental overwrite.")
        print("Usage: python backup.py --restore <file> --confirm")
        sys.exit(1)

    print("TaiYiYuan database restore")
    print("=" * 60)

    # Resolve backup path
    backup_path = Path(backup_file)
    if not backup_path.is_absolute():
        backup_path = BACKUP_DIR / backup_file

    if not backup_path.exists():
        print(f"ERROR: Backup file not found: {backup_path}")
        print("\nAvailable backups:")
        for date_str, path in _discover_backups():
            print(f"  {path.name}")
        sys.exit(1)

    # Verify backup integrity
    print(f"Backup:    {backup_path}")
    print(f"Database:  {DB_PATH}")
    print()

    print("Verifying backup integrity...")
    if not _verify_db(backup_path):
        print("ERROR: Backup file failed integrity check. Aborting.")
        sys.exit(1)
    print("  Integrity check: PASSED")

    # Create safety backup of current database before overwriting
    if os.path.exists(DB_PATH):
        print("\nCreating safety backup of current database...")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        safety_name = f"taiyiyuan-pre-restore-{ts}.db"
        safety_path = BACKUP_DIR / safety_name
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            src_conn = sqlite3.connect(DB_PATH)
            dst_conn = sqlite3.connect(str(safety_path))
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
            print(f"  Safety backup: {safety_path}")
        except sqlite3.Error:
            shutil.copy2(DB_PATH, safety_path)
            print(f"  Safety backup (copy): {safety_path}")

    # Restore: copy backup over current database
    print("\nRestoring...")
    try:
        # Remove WAL and SHM files
        for suffix in ("-wal", "-shm"):
            wal = DB_PATH + suffix
            if os.path.exists(wal):
                os.remove(wal)

        shutil.copy2(str(backup_path), DB_PATH)
        os.chmod(DB_PATH, 0o600)
    except OSError as e:
        print(f"ERROR: Restore failed: {e}")
        sys.exit(1)

    # Verify restored database
    print("Verifying restored database...")
    if _verify_db(DB_PATH):
        print("  Integrity check: PASSED")
    else:
        print("  WARNING: Restored database failed integrity check!")

    # Show schema version
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        version = row[0] if row and row[0] else "unknown"
        conn.close()
        print(f"  Schema version: {version}")
    except sqlite3.Error:
        pass

    print(f"\nRestore complete. Database restored from {backup_path.name}.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TaiYiYuan automated backup with retention policy"
    )
    parser.add_argument(
        "--prune", action="store_true",
        help="Prune old backups per retention policy"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List existing backups with sizes"
    )
    parser.add_argument(
        "--restore", metavar="FILE",
        help="Restore from a backup file (requires --confirm)"
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required with --restore to confirm overwrite"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite today's backup if it already exists"
    )
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.prune:
        cmd_prune()
    elif args.restore:
        cmd_restore(args.restore, args.confirm)
    else:
        cmd_backup(force=args.force)


if __name__ == "__main__":
    main()
