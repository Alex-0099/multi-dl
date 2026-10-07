"""
Tests for the EngineUpdater and CLI update commands.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.updater import EngineUpdater


class TestEngineUpdater(unittest.TestCase):
    def setUp(self):
        self.updater = EngineUpdater()

    def test_get_pkg_version_existing(self):
        version = self.updater._get_pkg_version("pytest")
        self.assertIsNotNone(version)
        self.assertIsInstance(version, str)

    def test_get_pkg_version_nonexistent(self):
        version = self.updater._get_pkg_version("non_existent_engine_package_xyz_123")
        self.assertIsNone(version)

    @patch("subprocess.run")
    def test_pip_upgrade_already_up_to_date(self, mock_subproc):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="Requirement already satisfied", stderr="")
        with patch.object(self.updater, "_get_pkg_version", side_effect=["2026.1.0", "2026.1.0"]):
            ok, msg = self.updater._pip_upgrade("test-pkg")
            self.assertTrue(ok)
            self.assertIn("Up to date", msg)

    @patch("subprocess.run")
    def test_pip_upgrade_success(self, mock_subproc):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="Successfully installed", stderr="")
        with patch.object(self.updater, "_get_pkg_version", side_effect=["2026.1.0", "2026.2.0"]):
            ok, msg = self.updater._pip_upgrade("test-pkg")
            self.assertTrue(ok)
            self.assertIn("Updated", msg)

    @patch("subprocess.run")
    def test_pip_upgrade_failure(self, mock_subproc):
        mock_subproc.return_value = MagicMock(returncode=1, stdout="", stderr="ERROR: No matching distribution found")
        ok, msg = self.updater._pip_upgrade("test-pkg")
        self.assertFalse(ok)
        self.assertIn("pip update error", msg)

    @patch("shutil.which", return_value="git")
    @patch("subprocess.run")
    def test_update_git_repo_existing(self, mock_subproc, mock_which):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()
            (repo_path / ".git").mkdir()

            # rev-parse before -> pull -> rev-parse after
            mock_subproc.side_effect = [
                MagicMock(returncode=0, stdout="abc1234", stderr=""),
                MagicMock(returncode=0, stdout="Already up to date", stderr=""),
                MagicMock(returncode=0, stdout="abc1234", stderr=""),
            ]

            ok, msg = self.updater._update_or_clone_git_repo(
                "Test-Repo", "https://github.com/test/repo.git", [repo_path]
            )
            self.assertTrue(ok)
            self.assertIn("Up to date", msg)

    @patch("shutil.which", return_value="git")
    @patch("subprocess.run")
    def test_update_git_repo_clone_if_missing(self, mock_subproc, mock_which):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "non_existent_subfolder"
            self.updater.engines_dir = Path(tmpdir) / "engines"

            # clone -> rev-parse
            mock_subproc.side_effect = [
                MagicMock(returncode=0, stdout="Cloning...", stderr=""),
                MagicMock(returncode=0, stdout="def5678", stderr=""),
            ]

            ok, msg = self.updater._update_or_clone_git_repo(
                "Test-Repo", "https://github.com/test/repo.git", [missing_path]
            )
            self.assertTrue(ok)
            self.assertIn("Cloned & initialized", msg)

    @patch.object(EngineUpdater, "update_self", return_value=(True, "Up to date"))
    @patch.object(EngineUpdater, "update_ytdlp", return_value=(True, "Up to date"))
    @patch.object(EngineUpdater, "update_gallerydl", return_value=(True, "Up to date"))
    @patch.object(EngineUpdater, "update_curl_cffi", return_value=(True, "Up to date"))
    @patch.object(EngineUpdater, "update_telegramdl", return_value=(True, "Up to date"))
    @patch.object(EngineUpdater, "update_teraboxdl", return_value=(True, "Up to date"))
    @patch.object(EngineUpdater, "update_cyberdropdl", return_value=(True, "Skipped"))
    def test_update_all_runs_smoothly(self, *mocks):
        results = self.updater.update_all()
        self.assertIn("multi-dl", results)
        self.assertIn("yt-dlp", results)
        self.assertIn("gallery-dl", results)
        self.assertIn("telegram-dl", results)
        self.assertIn("terabox-dl", results)
        self.assertTrue(all(results.values()))


if __name__ == "__main__":
    unittest.main()
