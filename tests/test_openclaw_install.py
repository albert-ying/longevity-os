from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_openclaw_skill.py"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_clawhub_bundle.py"
SKILL_SOURCE = REPO_ROOT / "SKILL.md"


class OpenClawInstallTests(unittest.TestCase):
    def test_repo_skill_has_valid_frontmatter_header(self) -> None:
        source_text = SKILL_SOURCE.read_text(encoding="utf-8")
        self.assertTrue(
            source_text.startswith("---\n"),
            "Repo SKILL.md must begin with YAML frontmatter",
        )
        self.assertIn("name: longevity\n", source_text)
        self.assertIn("{baseDir}", source_text)
        self.assertNotIn("{SCRIPTS_DIR}", source_text)

    def test_bundle_validator_passes_portable_bundle_rules(self) -> None:
        proc = subprocess.run(
            ["python3", str(CHECK_SCRIPT)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=True,
        )
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["problems"], [])

    def test_install_and_check_portable_openclaw_skill(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir:
            workspace_root = Path(workspace_dir)

            install_proc = subprocess.run(
                [
                    "python3",
                    str(INSTALL_SCRIPT),
                    "--workspace",
                    str(workspace_root),
                    "--repo-root",
                    str(REPO_ROOT),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=True,
            )
            install_result = json.loads(install_proc.stdout)
            self.assertEqual(install_result["status"], "success")

            install_root = workspace_root / "skills" / "longevity"
            skill_file = install_root / "SKILL.md"
            agents_dir = workspace_root / "skills" / "longevity" / "agents"
            self.assertTrue(skill_file.exists())
            self.assertTrue(agents_dir.exists())
            self.assertTrue((agents_dir / "shiyi.md").exists())
            self.assertTrue((install_root / "scripts" / "setup.py").exists())
            self.assertTrue((install_root / "modeling" / "engine.py").exists())
            self.assertTrue((install_root / "dashboard" / "dashboard.html").exists())
            self.assertTrue((install_root / "requirements.txt").exists())

            skill_text = skill_file.read_text(encoding="utf-8")
            self.assertTrue(skill_text.startswith("---\n"))
            self.assertIn("name: longevity\n", skill_text)
            self.assertIn("{baseDir}", skill_text)
            self.assertNotIn("{SCRIPTS_DIR}", skill_text)

            check_proc = subprocess.run(
                [
                    "python3",
                    str(INSTALL_SCRIPT),
                    "--workspace",
                    str(workspace_root),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--check",
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=True,
            )
            check_result = json.loads(check_proc.stdout)
            self.assertEqual(check_result["status"], "ok")
            self.assertEqual(check_result["problems"], [])

    def test_installed_bundle_default_runtime_paths_work_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir:
            workspace_root = Path(workspace_dir)
            install_root = workspace_root / "skills" / "longevity"

            subprocess.run(
                [
                    "python3",
                    str(INSTALL_SCRIPT),
                    "--workspace",
                    str(workspace_root),
                    "--repo-root",
                    str(REPO_ROOT),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=True,
            )

            subprocess.run(
                ["python3", str(install_root / "scripts" / "setup.py")],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=True,
            )

            query_proc = subprocess.run(
                [
                    "python3",
                    str(install_root / "scripts" / "query_sqlite.py"),
                    "--sql",
                    "SELECT 1 AS ok",
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=True,
            )
            query_result = json.loads(query_proc.stdout)
            self.assertEqual(query_result["status"], "success")
            self.assertEqual(query_result["row_count"], 1)
            self.assertEqual(query_result["rows"][0]["ok"], 1)

    def test_check_reports_stale_installed_files_and_reinstall_removes_them(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir:
            workspace_root = Path(workspace_dir)
            install_root = workspace_root / "skills" / "longevity"
            stale_file = install_root / "scripts" / "stale_only.py"

            subprocess.run(
                [
                    "python3",
                    str(INSTALL_SCRIPT),
                    "--workspace",
                    str(workspace_root),
                    "--repo-root",
                    str(REPO_ROOT),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=True,
            )

            stale_file.write_text("print('stale')\n", encoding="utf-8")

            check_proc = subprocess.run(
                [
                    "python3",
                    str(INSTALL_SCRIPT),
                    "--workspace",
                    str(workspace_root),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--check",
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(check_proc.returncode, 1)
            check_result = json.loads(check_proc.stdout)
            self.assertEqual(check_result["status"], "error")
            self.assertIn("Unexpected installed files: scripts/stale_only.py", check_result["problems"])

            subprocess.run(
                [
                    "python3",
                    str(INSTALL_SCRIPT),
                    "--workspace",
                    str(workspace_root),
                    "--repo-root",
                    str(REPO_ROOT),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertFalse(stale_file.exists())

            final_check_proc = subprocess.run(
                [
                    "python3",
                    str(INSTALL_SCRIPT),
                    "--workspace",
                    str(workspace_root),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--check",
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                check=True,
            )
            final_check_result = json.loads(final_check_proc.stdout)
            self.assertEqual(final_check_result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
