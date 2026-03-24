from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
PROJECT_DIR_ENV = "LONGEVITY_OS_PROJECT_DIR"
DB_PATH_ENV = "LONGEVITY_OS_DB_PATH"


def get_repo_root() -> Path:
    """Return the repository root for the checked-out skill/worktree."""
    return REPO_ROOT


def get_project_root() -> Path:
    """Return the mutable runtime project directory.

    By default this repo stores runtime data in a sibling directory named
    `longevity-os-data`. Callers can override it for tests or alternate
    environments with LONGEVITY_OS_PROJECT_DIR.
    """
    override = os.environ.get(PROJECT_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return (REPO_ROOT.parent / "longevity-os-data").resolve()


def get_data_dir() -> Path:
    return get_project_root() / "data"


def get_reports_dir() -> Path:
    return get_project_root() / "reports"


def get_db_path() -> Path:
    """Return the SQLite database path.

    LONGEVITY_OS_DB_PATH wins if present; otherwise the DB lives under the
    project data directory.
    """
    override = os.environ.get(DB_PATH_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return get_data_dir() / "taiyiyuan.db"


def describe_runtime_paths() -> dict[str, str]:
    """Expose the resolved runtime locations for docs and operator checks."""
    return {
        "repo_root": str(get_repo_root()),
        "project_root": str(get_project_root()),
        "data_dir": str(get_data_dir()),
        "reports_dir": str(get_reports_dir()),
        "db_path": str(get_db_path()),
    }
