#!/usr/bin/env python3
"""Install the portable Longevity OS skill bundle into a local OpenClaw workspace."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SKILL_NAME = "longevity"
BUNDLE_DIRS = ("agents", "dashboard", "data", "modeling", "scripts")
BUNDLE_FILES = ("SKILL.md", "paths.py", "requirements.txt")
IGNORED_DIR_NAMES = {"__pycache__"}
IGNORED_FILE_NAMES = {".DS_Store"}
IGNORED_FILE_SUFFIXES = {".pyc", ".pyo"}
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


def _default_workspace_root() -> Path:
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        workspace = config.get("agents", {}).get("defaults", {}).get("workspace")
        if workspace:
            return Path(workspace).expanduser().resolve()
    return (Path.home() / ".openclaw" / "workspace").resolve()


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _is_ignored_relative_path(relative_path: Path) -> bool:
    if any(part in IGNORED_DIR_NAMES for part in relative_path.parts):
        return True
    if relative_path.name in IGNORED_FILE_NAMES:
        return True
    if relative_path.suffix in IGNORED_FILE_SUFFIXES:
        return True
    return False


def _bundle_file_relpaths(repo_root: Path) -> list[Path]:
    relpaths: list[Path] = [Path(relative_file) for relative_file in BUNDLE_FILES]
    for relative_dir in BUNDLE_DIRS:
        src_dir = repo_root / relative_dir
        for src_path in sorted(src_dir.rglob("*")):
            if not src_path.is_file():
                continue
            relative_path = src_path.relative_to(repo_root)
            if _is_ignored_relative_path(relative_path):
                continue
            relpaths.append(relative_path)
    return sorted(relpaths)


def _copy_bundle(repo_root: Path, install_root: Path) -> None:
    if install_root.exists():
        shutil.rmtree(install_root)
    install_root.mkdir(parents=True, exist_ok=True)
    for relative_path in _bundle_file_relpaths(repo_root):
        _copy_file(repo_root / relative_path, install_root / relative_path)


def _required_agent_files(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "agents").glob("*.md"))


def install_skill(repo_root: Path, workspace_root: Path) -> dict:
    install_root = workspace_root / "skills" / SKILL_NAME
    _copy_bundle(repo_root, install_root)

    return {
        "status": "success",
        "repo_root": str(repo_root),
        "workspace_root": str(workspace_root),
        "install_root": str(install_root),
        "skill_file": str(install_root / "SKILL.md"),
        "agents_dir": str(install_root / "agents"),
        "agents_installed": len(_required_agent_files(repo_root)),
        "bundle_dirs": list(BUNDLE_DIRS),
        "bundle_files": list(BUNDLE_FILES),
    }


def check_install(repo_root: Path, workspace_root: Path) -> dict:
    install_root = workspace_root / "skills" / SKILL_NAME
    skill_file = install_root / "SKILL.md"
    agents_root = install_root / "agents"
    expected_agents = _required_agent_files(repo_root)
    expected_bundle_files = {path.as_posix() for path in _bundle_file_relpaths(repo_root)}

    problems = []
    if not skill_file.exists():
        problems.append(f"Missing skill file: {skill_file}")
    if not agents_root.exists():
        problems.append(f"Missing agents directory: {agents_root}")
    for relative_dir in BUNDLE_DIRS:
        if not (install_root / relative_dir).exists():
            problems.append(f"Missing bundle directory: {install_root / relative_dir}")
    for relative_file in BUNDLE_FILES:
        if not (install_root / relative_file).exists():
            problems.append(f"Missing bundle file: {install_root / relative_file}")

    if skill_file.exists():
        skill_text = skill_file.read_text(encoding="utf-8")
        if not skill_text.startswith("---\n"):
            problems.append("Installed SKILL.md does not start with valid YAML frontmatter")
        if "{baseDir}" not in skill_text:
            problems.append("Installed SKILL.md does not use {baseDir} for portable bundle paths")
        for token in FORBIDDEN_PLACEHOLDERS:
            if token in skill_text:
                problems.append(f"Installed SKILL.md still contains unsupported placeholder {token}")

    missing_agents = []
    unresolved_agents = []
    for agent_source in expected_agents:
        target = agents_root / agent_source.name
        if not target.exists():
            missing_agents.append(agent_source.name)
            continue
        text = target.read_text(encoding="utf-8")
        if any(token in text for token in FORBIDDEN_PLACEHOLDERS):
            unresolved_agents.append(agent_source.name)

    if missing_agents:
        problems.append(f"Missing agent files: {', '.join(missing_agents)}")
    if unresolved_agents:
        problems.append(f"Unsupported placeholders remain in: {', '.join(unresolved_agents)}")

    actual_bundle_files: set[str] = set()
    if install_root.exists():
        for installed_path in sorted(install_root.rglob("*")):
            if not installed_path.is_file():
                continue
            relative_path = installed_path.relative_to(install_root)
            if _is_ignored_relative_path(relative_path):
                continue
            actual_bundle_files.add(relative_path.as_posix())

    unexpected_files = sorted(actual_bundle_files - expected_bundle_files)
    if unexpected_files:
        problems.append(f"Unexpected installed files: {', '.join(unexpected_files)}")

    return {
        "status": "ok" if not problems else "error",
        "workspace_root": str(workspace_root),
        "install_root": str(install_root),
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Longevity OS skill into OpenClaw")
    parser.add_argument("--workspace", help="OpenClaw workspace root; defaults from ~/.openclaw/openclaw.json")
    parser.add_argument("--repo-root", help="Repo root to copy from; defaults to this checkout")
    parser.add_argument("--check", action="store_true", help="Verify the current installation instead of writing files")
    parser.add_argument("--clean", action="store_true", help="Remove existing installed agent files before copying")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else REPO_ROOT
    workspace_root = Path(args.workspace).expanduser().resolve() if args.workspace else _default_workspace_root()

    try:
        if args.clean:
            install_root = workspace_root / "skills" / SKILL_NAME
            if install_root.exists():
                shutil.rmtree(install_root)

        result = check_install(repo_root, workspace_root) if args.check else install_skill(repo_root, workspace_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"success", "ok"} else 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
