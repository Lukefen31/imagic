"""CLI Orchestrator — subprocess wrapper for darktable-cli / RawTherapee-cli.

Every external process call is:
1. Non-blocking (designed to run inside a ``TaskQueue`` worker).
2. Wrapped in robust error handling — ``stderr`` is captured and logged.
3. Configurable via the ``Settings`` system (tool paths come from YAML).

Retry logic is included: a transient failure will be retried up to
``max_retries`` times with exponential back-off.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CLIResult:
    """Captures the outcome of a single CLI invocation.

    Attributes:
        success: ``True`` if the process exited with code 0.
        return_code: Process exit code.
        stdout: Captured standard output.
        stderr: Captured standard error.
        command: The full command list that was executed.
        duration_s: Wall-clock seconds the process ran.
    """

    success: bool
    return_code: int
    stdout: str
    stderr: str
    command: List[str]
    duration_s: float


class CLIOrchestrator:
    """Manages external CLI tool invocations.

    Args:
        darktable_cli: Absolute path to ``darktable-cli`` executable.
        rawtherapee_cli: Absolute path to ``rawtherapee-cli`` executable.
        exiftool: Absolute path to ``exiftool`` executable.
        timeout: Per-invocation timeout in seconds (``None`` = no limit).
        max_retries: Number of retries on transient failure.
    """

    def __init__(
        self,
        darktable_cli: str = "",
        rawtherapee_cli: str = "",
        exiftool: str = "",
        timeout: Optional[int] = 300,
        max_retries: int = 2,
    ) -> None:
        self.darktable_cli = darktable_cli
        self.rawtherapee_cli = rawtherapee_cli
        self.exiftool = exiftool
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Generic runner
    # ------------------------------------------------------------------
    def _run(
        self,
        cmd: List[str],
        retries: Optional[int] = None,
    ) -> CLIResult:
        """Execute *cmd* as a subprocess with retry logic.

        Args:
            cmd: Command and arguments to execute.
            retries: Override for ``self.max_retries``.

        Returns:
            A ``CLIResult`` with captured output.
        """
        max_attempts = (retries if retries is not None else self.max_retries) + 1

        for attempt in range(1, max_attempts + 1):
            logger.debug("Running (attempt %d/%d): %s", attempt, max_attempts, cmd)
            start = time.monotonic()

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
                elapsed = time.monotonic() - start

                result = CLIResult(
                    success=proc.returncode == 0,
                    return_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    command=cmd,
                    duration_s=round(elapsed, 3),
                )

                if result.success:
                    logger.info("CLI success in %.2fs: %s", elapsed, cmd[0])
                    return result

                logger.warning(
                    "CLI non-zero exit (%d) for %s — stderr: %s",
                    proc.returncode, cmd[0], proc.stderr.strip(),
                )

            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - start
                logger.error("CLI timeout after %.0fs: %s", elapsed, cmd[0])
                result = CLIResult(
                    success=False,
                    return_code=-1,
                    stdout="",
                    stderr=f"Process timed out after {self.timeout}s.",
                    command=cmd,
                    duration_s=round(elapsed, 3),
                )

            except FileNotFoundError:
                logger.error("CLI executable not found: %s", cmd[0])
                return CLIResult(
                    success=False,
                    return_code=-1,
                    stdout="",
                    stderr=f"Executable not found: {cmd[0]}",
                    command=cmd,
                    duration_s=0.0,
                )

            except Exception as exc:
                elapsed = time.monotonic() - start
                logger.exception("Unexpected error running CLI: %s", exc)
                result = CLIResult(
                    success=False,
                    return_code=-1,
                    stdout="",
                    stderr=str(exc),
                    command=cmd,
                    duration_s=round(elapsed, 3),
                )

            # Exponential back-off before retry.
            if attempt < max_attempts:
                delay = 2 ** (attempt - 1)
                logger.info("Retrying in %ds…", delay)
                time.sleep(delay)

        return result  # type: ignore[possibly-undefined]

    # ------------------------------------------------------------------
    # Darktable
    # ------------------------------------------------------------------
    def darktable_export(
        self,
        input_path: Path,
        output_path: Path,
        xmp_path: Optional[Path] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> CLIResult:
        """Export a single RAW file using ``darktable-cli``.

        Args:
            input_path: Path to the source RAW file.
            output_path: Desired output file path (extension determines format).
            xmp_path: Optional XMP sidecar with edit instructions.
            width: Optional output width in pixels.
            height: Optional output height in pixels.

        Returns:
            ``CLIResult`` describing the outcome.

        Raises:
            ValueError: If the darktable-cli path is not configured.
        """
        if not self.darktable_cli:
            raise ValueError(
                "darktable-cli path is not configured.  "
                "Set it in Settings → CLI Tools."
            )

        cmd: List[str] = [self.darktable_cli]

        if xmp_path and xmp_path.is_file():
            cmd += [str(input_path), str(xmp_path), str(output_path)]
        else:
            cmd += [str(input_path), str(output_path)]

        if width:
            cmd += ["--width", str(width)]
        if height:
            cmd += ["--height", str(height)]

        # Ensure the output directory exists.
        output_path.parent.mkdir(parents=True, exist_ok=True)

        return self._run(cmd)

    # ------------------------------------------------------------------
    # RawTherapee
    # ------------------------------------------------------------------
    def rawtherapee_export(
        self,
        input_path: Path,
        output_dir: Path,
        pp3_path: Optional[Path] = None,
        output_format: str = "jpg",
    ) -> CLIResult:
        """Export a single RAW file using ``rawtherapee-cli``.

        Args:
            input_path: Path to the source RAW file.
            output_dir: Directory for the exported file.
            pp3_path: Optional PP3 processing profile.
            output_format: ``"jpg"``, ``"tif"``, or ``"png"``.

        Returns:
            ``CLIResult`` describing the outcome.

        Raises:
            ValueError: If the rawtherapee-cli path is not configured.
        """
        if not self.rawtherapee_cli:
            raise ValueError(
                "rawtherapee-cli path is not configured.  "
                "Set it in Settings → CLI Tools."
            )

        output_dir.mkdir(parents=True, exist_ok=True)

        cmd: List[str] = [self.rawtherapee_cli, "-o", str(output_dir)]

        if pp3_path and pp3_path.is_file():
            cmd += ["-p", str(pp3_path)]

        fmt_flag = {"jpg": "-j100", "tif": "-t", "png": "-n"}
        cmd.append(fmt_flag.get(output_format.lower(), "-j100"))
        cmd += ["-c", str(input_path)]

        return self._run(cmd)

    # ------------------------------------------------------------------
    # ExifTool (metadata read)
    # ------------------------------------------------------------------
    def read_exif(self, file_path: Path) -> CLIResult:
        """Read EXIF data from *file_path* using ``exiftool``.

        Args:
            file_path: Path to the image.

        Returns:
            ``CLIResult`` whose ``stdout`` contains JSON EXIF data.

        Raises:
            ValueError: If exiftool path is not configured.
        """
        if not self.exiftool:
            raise ValueError(
                "exiftool path is not configured.  "
                "Set it in Settings → CLI Tools."
            )

        cmd = [self.exiftool, "-json", "-n", str(file_path)]
        return self._run(cmd, retries=0)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate_tools(self) -> dict[str, bool]:
        """Check which configured CLI tools are reachable.

        Returns:
            Mapping of tool name → ``True`` if the executable was found.
        """
        status: dict[str, bool] = {}
        for name, path in [
            ("darktable-cli", self.darktable_cli),
            ("rawtherapee-cli", self.rawtherapee_cli),
            ("exiftool", self.exiftool),
        ]:
            if not path:
                status[name] = False
                continue
            try:
                subprocess.run(
                    [path, "--version"],
                    capture_output=True,
                    timeout=10,
                )
                status[name] = True
            except Exception:
                status[name] = False
        return status
