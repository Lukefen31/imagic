"""Thumbnail generator — extracts embedded previews from RAW files.

Uses ``rawpy`` (libraw binding) when available to pull the embedded JPEG
preview and resize it via ``Pillow``.  When ``rawpy`` is not installed it
falls back to RawTherapee-cli or darktable-cli (whichever is configured)
to produce a temporary JPEG, then resizes with Pillow.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from PIL import Image

from imagic.utils.path_utils import discover_darktable_cli, discover_rawtherapee_cli
from imagic.utils.subprocess_utils import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)

# Extensions that Pillow can open directly — never shell out to a RAW CLI for these.
_PILLOW_NATIVE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
    ".gif",
}


def _subprocess_kwargs() -> dict:
    """Return subprocess kwargs that suppress console windows on Windows."""
    return hidden_subprocess_kwargs()

_RAW_THUMBNAIL_CONCURRENCY = max(
    1, int(os.environ.get("IMAGIC_RAW_THUMBNAIL_CONCURRENCY", "1") or "1")
)
_RAW_THUMBNAIL_SEMAPHORE = threading.BoundedSemaphore(_RAW_THUMBNAIL_CONCURRENCY)

# Whether rawpy is usable (checked once at import time).
_RAWPY_AVAILABLE = False
try:
    import rawpy  # type: ignore[import-untyped]

    _RAWPY_AVAILABLE = True
except ImportError:
    pass


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def generate_thumbnail(
    raw_path: Path,
    output_path: Path,
    max_size: tuple[int, int] = (320, 320),
    quality: int = 85,
    embedded_only: bool = False,
) -> Path | None:
    """Create a JPEG thumbnail for a RAW image.

    Strategy (first success wins):
    1. ``rawpy`` embedded JPEG extraction (fastest).
    2. ``rawpy`` full demosaic + resize.
    3. RawTherapee-cli export to temporary JPEG + resize.
    4. darktable-cli export to temporary JPEG + resize.

    Args:
        raw_path: Path to the source RAW file.
        output_path: Destination JPEG path.
        max_size: Maximum (width, height) for the thumbnail.
        quality: JPEG compression quality (1–100).

    Returns:
        The *output_path* on success, or ``None`` on failure.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Strategy 0: Pillow fast-path for non-RAW formats ---------------
    # TIFF / JPEG / PNG etc. are already decodable images — using rawpy or
    # the RAW CLIs for them is wasteful and (on Windows) pops a console
    # window per file.
    if raw_path.suffix.lower() in _PILLOW_NATIVE_EXTENSIONS:
        result = _generate_via_pillow(raw_path, output_path, max_size, quality)
        if result is not None:
            return result
        logger.debug("Pillow strategy failed for %s — trying RAW strategies.", raw_path)

    # --- Strategy 1 & 2: rawpy -------------------------------------------
    if _RAWPY_AVAILABLE:
        result = _generate_via_rawpy(raw_path, output_path, max_size, quality, embedded_only)
        if result is not None:
            return result
        logger.debug("rawpy strategies failed for %s — trying CLI fallback.", raw_path)

    # --- Strategy 3 & 4: CLI tools ---------------------------------------
    if embedded_only:
        logger.warning("No embedded RAW thumbnail available for %s", raw_path)
        return None

    result = _generate_via_cli(raw_path, output_path, max_size, quality)
    if result is not None:
        return result

    logger.error(
        "All thumbnail strategies failed for %s.  "
        "Install rawpy or configure RawTherapee / darktable CLI path.",
        raw_path,
    )
    return None


# ------------------------------------------------------------------
# rawpy path
# ------------------------------------------------------------------


def _generate_via_rawpy(
    raw_path: Path,
    output_path: Path,
    max_size: tuple[int, int],
    quality: int,
    embedded_only: bool,
) -> Path | None:
    """Try rawpy embedded preview, then full decode."""
    try:
        with _RAW_THUMBNAIL_SEMAPHORE, rawpy.imread(str(raw_path)) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = Image.open(io.BytesIO(thumb.data))
                    img.thumbnail(max_size, Image.LANCZOS)
                    with tempfile.NamedTemporaryFile(
                        dir=output_path.parent,
                        suffix=".jpg",
                        delete=False,
                    ) as handle:
                        temp_path = Path(handle.name)
                    try:
                        img.save(str(temp_path), "JPEG", quality=quality, optimize=True)
                        temp_path.replace(output_path)
                    finally:
                        temp_path.unlink(missing_ok=True)
                    logger.debug("Thumbnail (rawpy/embedded) created: %s", output_path)
                    return output_path
            except Exception:
                logger.debug("No embedded thumb for %s — trying full decode.", raw_path)

            if embedded_only:
                logger.warning("No embedded RAW thumbnail available for %s", raw_path)
                return None

            rgb = raw.postprocess(
                half_size=True,
                use_camera_wb=True,
                no_auto_bright=True,
                output_bps=8,
            )

        img = Image.fromarray(rgb)
        img.thumbnail(max_size, Image.LANCZOS)
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            suffix=".jpg",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        try:
            img.save(str(temp_path), "JPEG", quality=quality, optimize=True)
            temp_path.replace(output_path)
        finally:
            temp_path.unlink(missing_ok=True)
        logger.debug("Thumbnail (rawpy/decoded) created: %s", output_path)
        return output_path
    except Exception as exc:
        logger.debug("rawpy failed for %s: %s", raw_path, exc)
        return None


# ------------------------------------------------------------------
# Pillow fast-path (TIFF / JPEG / PNG / etc.)
# ------------------------------------------------------------------


def _generate_via_pillow(
    src_path: Path,
    output_path: Path,
    max_size: tuple[int, int],
    quality: int,
) -> Path | None:
    """Decode a non-RAW image with Pillow and write a JPEG thumbnail."""
    try:
        with Image.open(src_path) as img:
            img.load()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail(max_size, Image.LANCZOS)
            with tempfile.NamedTemporaryFile(
                dir=output_path.parent,
                suffix=".jpg",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
            try:
                img.save(str(temp_path), "JPEG", quality=quality, optimize=True)
                temp_path.replace(output_path)
            finally:
                temp_path.unlink(missing_ok=True)
        logger.debug("Thumbnail (Pillow) created: %s", output_path)
        return output_path
    except Exception as exc:
        logger.debug("Pillow failed for %s: %s", src_path, exc)
        return None


# ------------------------------------------------------------------
# CLI fallback path (RawTherapee / darktable)
# ------------------------------------------------------------------


def _resolve_cli_tools() -> tuple[str, str]:
    """Return (rawtherapee_cli, darktable_cli) paths from settings.

    Falls back to PATH-based discovery if settings are not yet
    initialised (e.g. during early startup or testing).
    """
    rt_path = ""
    dt_path = ""
    try:
        from imagic.config.settings import Settings

        settings = Settings.get()
        cli = settings.data.get("cli_tools", {})
        rt_path = cli.get("rawtherapee_cli", "")
        dt_path = cli.get("darktable_cli", "")
    except Exception:
        pass

    # Last-resort discovery via PATH.
    if not rt_path:
        rt_path = discover_rawtherapee_cli()
    if not dt_path:
        dt_path = discover_darktable_cli()
    return rt_path, dt_path


def _generate_via_cli(
    raw_path: Path,
    output_path: Path,
    max_size: tuple[int, int],
    quality: int,
) -> Path | None:
    """Generate a thumbnail by shelling out to RawTherapee or darktable."""
    rt_path, dt_path = _resolve_cli_tools()

    if rt_path:
        result = _try_rawtherapee(rt_path, raw_path, output_path, max_size, quality)
        if result is not None:
            return result

    if dt_path:
        result = _try_darktable(dt_path, raw_path, output_path, max_size, quality)
        if result is not None:
            return result

    return None


def _try_rawtherapee(
    cli: str,
    raw_path: Path,
    output_path: Path,
    max_size: tuple[int, int],
    quality: int,
) -> Path | None:
    """Use rawtherapee-cli to produce a temporary JPEG, then resize."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                cli,
                "-o",
                tmpdir,
                f"-j{quality}",
                "-c",
                str(raw_path),
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                **_subprocess_kwargs(),
            )
            if proc.returncode != 0:
                logger.debug(
                    "rawtherapee-cli failed (%d): %s", proc.returncode, proc.stderr.strip()
                )
                return None

            # RawTherapee writes <stem>.jpg into the output directory.
            exported = list(Path(tmpdir).glob("*.jpg")) + list(Path(tmpdir).glob("*.JPG"))
            if not exported:
                logger.debug("rawtherapee-cli produced no JPEG output for %s.", raw_path)
                return None

            img = Image.open(exported[0])
            img.thumbnail(max_size, Image.LANCZOS)
            img.save(str(output_path), "JPEG", quality=quality)
            logger.debug("Thumbnail (rawtherapee-cli) created: %s", output_path)
            return output_path
    except Exception as exc:
        logger.debug("rawtherapee-cli thumbnail failed for %s: %s", raw_path, exc)
        return None


def _try_darktable(
    cli: str,
    raw_path: Path,
    output_path: Path,
    max_size: tuple[int, int],
    quality: int,
) -> Path | None:
    """Use darktable-cli to produce a temporary JPEG, then resize."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_out = Path(tmpdir) / f"{raw_path.stem}.jpg"
            cmd = [
                cli,
                str(raw_path),
                str(tmp_out),
                "--width",
                str(max_size[0]),
                "--height",
                str(max_size[1]),
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                **_subprocess_kwargs(),
            )
            if proc.returncode != 0 or not tmp_out.is_file():
                logger.debug("darktable-cli failed (%d): %s", proc.returncode, proc.stderr.strip())
                return None

            img = Image.open(tmp_out)
            img.thumbnail(max_size, Image.LANCZOS)
            img.save(str(output_path), "JPEG", quality=quality)
            logger.debug("Thumbnail (darktable-cli) created: %s", output_path)
            return output_path
    except Exception as exc:
        logger.debug("darktable-cli thumbnail failed for %s: %s", raw_path, exc)
        return None
