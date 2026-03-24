#!/usr/bin/env python3
"""Validate that the checked-in skill bundle is portable and publishable."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_FILE = REPO_ROOT / "SKILL.md"
REQUIRED_DIRS = ("agents", "dashboard", "data", "modeling", "scripts")
REQUIRED_FILES = ("SKILL.md", "paths.py", "requirements.txt")
FORBIDDEN_PLACEHOLDERS = (
    "{SKILL_DIR}",
    "{AGENTS_DIR}",
    "{MODELING_DIR}",
    "{DATA_DIR}",
    "{SCRIPTS_DIR}",
    "{PROJECT_DIR}",
    "{DATABASE}",
    "{REPORTS_DIR}",
    "{PHOTOS_DIR}",
    "{TRIALS_DIR}",
    "{SCHEMA_FILE}",
)
COMMON_LOCAL_ONLY_PATHS = (
    ".agent",
    ".agents",
    ".claude",
    ".factory",
    ".kiro",
    ".windsurf",
    "notes",
    "skills",
    "skills-lock.json",
)


def _check_paths(repo_root: Path) -> list[str]:
    problems: list[str] = []
    for relative_dir in REQUIRED_DIRS:
        if not (repo_root / relative_dir).is_dir():
            problems.append(f"Missing required directory: {relative_dir}")
    for relative_file in REQUIRED_FILES:
        if not (repo_root / relative_file).is_file():
            problems.append(f"Missing required file: {relative_file}")
    return problems


def _check_skill_text(skill_text: str, repo_root: Path) -> list[str]:
    problems: list[str] = []
    if not skill_text.startswith("---\n"):
        problems.append("SKILL.md must begin with YAML frontmatter")
    if "metadata:" not in skill_text:
        problems.append("SKILL.md should include metadata.openclaw gating")
    if "{baseDir}" not in skill_text:
        problems.append("SKILL.md should use {baseDir} for skill-relative paths")
    for token in FORBIDDEN_PLACEHOLDERS:
        if token in skill_text:
            problems.append(f"Unsupported placeholder remains in SKILL.md: {token}")
    for agent_file in sorted((repo_root / "agents").glob("*.md")):
        text = agent_file.read_text(encoding="utf-8")
        for token in FORBIDDEN_PLACEHOLDERS:
            if token in text:
                problems.append(f"Unsupported placeholder remains in {agent_file.relative_to(repo_root)}: {token}")
                break
    return problems


def _check_local_only_paths(repo_root: Path) -> list[str]:
    warnings: list[str] = []
    for relative in COMMON_LOCAL_ONLY_PATHS:
        if (repo_root / relative).exists():
            warnings.append(f"Local-only path present in repo root: {relative}")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Longevity OS ClawHub bundle")
    parser.add_argument("--strict-local", action="store_true", help="Treat local-only files as hard errors")
    args = parser.parse_args()

    skill_text = SKILL_FILE.read_text(encoding="utf-8")
    problems = _check_paths(REPO_ROOT)
    problems.extend(_check_skill_text(skill_text, REPO_ROOT))
    warnings = _check_local_only_paths(REPO_ROOT)
    if args.strict_local:
        problems.extend(warnings)
        warnings = []

    result = {
        "status": "ok" if not problems else "error",
        "repo_root": str(REPO_ROOT),
        "problems": problems,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
