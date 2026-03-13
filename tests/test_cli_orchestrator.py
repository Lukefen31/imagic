"""Unit tests for the CLIOrchestrator."""

from pathlib import Path
from unittest.mock import patch

from imagic.services.cli_orchestrator import CLIOrchestrator, CLIResult


class TestCLIOrchestrator:
    """Tests for ``CLIOrchestrator``."""

    def test_validate_tools_empty(self) -> None:
        """With no paths configured, all tools should report False."""
        cli = CLIOrchestrator()
        status = cli.validate_tools()
        assert status["darktable-cli"] is False
        assert status["rawtherapee-cli"] is False
        assert status["exiftool"] is False

    def test_darktable_export_no_path_raises(self, tmp_path: Path) -> None:
        """Should raise ValueError when darktable-cli is not configured."""
        cli = CLIOrchestrator(darktable_cli="")
        import pytest
        with pytest.raises(ValueError, match="darktable-cli"):
            cli.darktable_export(
                input_path=tmp_path / "test.cr2",
                output_path=tmp_path / "out.jpg",
            )

    def test_run_captures_stdout_stderr(self, tmp_path: Path) -> None:
        """The internal _run method should capture process output."""
        import sys
        cli = CLIOrchestrator(max_retries=0)
        # Use a simple cross-platform command.
        result = cli._run([sys.executable, "-c", "print('hello')"])
        assert result.success
        assert "hello" in result.stdout

    def test_run_handles_missing_executable(self) -> None:
        """A missing executable should return a failed CLIResult, not raise."""
        cli = CLIOrchestrator(max_retries=0)
        result = cli._run(["this_does_not_exist_abc123"])
        assert not result.success
        assert "not found" in result.stderr.lower() or result.return_code == -1

    @patch("imagic.services.cli_orchestrator.subprocess.run")
    def test_retry_logic(self, mock_run) -> None:
        """Failed attempts should be retried up to max_retries times."""
        import subprocess

        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fail"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]

        cli = CLIOrchestrator(max_retries=1, timeout=10)
        result = cli._run(["fake_tool"])
        assert result.success
        assert mock_run.call_count == 2
