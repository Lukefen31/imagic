"""Photo ORM model — the central entity in the library.

Each row represents a single image file on disk and tracks it through the
full ingestion → analysis → processing → export pipeline.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from imagic.models.database import Base
from imagic.models.enums import PhotoStatus


class Photo(Base):
    """Represents a single photo in the Imagic library.

    Attributes:
        id: Auto-increment primary key.
        file_path: Absolute path to the original RAW file on disk.
        file_name: Base filename (e.g. ``IMG_1234.CR2``).
        file_extension: Lowercase extension including the dot (e.g. ``.cr2``).
        file_size_bytes: Size in bytes at the time of import.
        directory: Parent directory path.
        status: Current pipeline status (see ``PhotoStatus``).
        quality_score: AI-assigned quality score (0.0–1.0).  ``None`` if not
            yet analysed.
        perceptual_hash: Hex-encoded perceptual hash for duplicate detection.
        sidecar_path: Path to the associated XMP sidecar, if any.
        thumbnail_path: Path to the generated thumbnail JPEG.
        export_path: Path to the final exported output file.
        error_message: Human-readable error description if ``status == ERROR``.
        exif_make: Camera make from EXIF.
        exif_model: Camera model from EXIF.
        exif_date_taken: Date/time the photo was taken.
        exif_iso: ISO speed.
        exif_focal_length: Focal length in mm.
        exif_aperture: Aperture (f-number).
        exif_shutter_speed: Shutter speed as a string (e.g. ``"1/250"``).
        created_at: Timestamp of record creation.
        updated_at: Timestamp of last update.
    """

    __tablename__ = "photos"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # File identity
    file_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    directory: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Pipeline state
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PhotoStatus.PENDING.value
    )

    # AI analysis
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    perceptual_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Associated files
    sidecar_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    export_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    # Auto-profile selection
    scene_preset: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Cull reasons (JSON text — list of per-metric verdicts)
    cull_reasons: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Per-photo edit settings
    color_grade: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    auto_crop_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    manual_overrides: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # EXIF metadata (nullable — populated after metadata scan)
    exif_make: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    exif_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    exif_date_taken: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True
    )
    exif_iso: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    exif_focal_length: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exif_aperture: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exif_shutter_speed: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Timestamps
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Indexes for common queries
    __table_args__ = (
        Index("ix_photos_status", "status"),
        Index("ix_photos_quality_score", "quality_score"),
        Index("ix_photos_directory", "directory"),
        Index("ix_photos_perceptual_hash", "perceptual_hash"),
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        """Return the ``file_path`` as a ``pathlib.Path``."""
        return Path(self.file_path)

    def __repr__(self) -> str:
        return (
            f"<Photo(id={self.id}, name={self.file_name!r}, "
            f"status={self.status!r}, score={self.quality_score})>"
        )
