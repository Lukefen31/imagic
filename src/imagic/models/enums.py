"""Enumerations for photo processing status and file types."""

from enum import Enum, auto


class PhotoStatus(str, Enum):
    """Tracks a photo through the 4-stage automation pipeline.

    Attributes:
        PENDING: Newly ingested, awaiting analysis.
        ANALYZING: Currently being scored by the AI analyst.
        CULLED: AI analysis complete; score assigned.
        KEEP: Decision engine marked for processing.
        TRASH: Decision engine marked for deletion.
        PROCESSING: Batch editing in progress (darktable-cli / RawTherapee).
        EXPORTED: Final output produced successfully.
        ERROR: An unrecoverable error occurred for this photo.
    """

    PENDING = "pending"
    ANALYZING = "analyzing"
    CULLED = "culled"
    KEEP = "keep"
    TRASH = "trash"
    PROCESSING = "processing"
    EXPORTED = "exported"
    ERROR = "error"


class TaskStatus(str, Enum):
    """Status of a queued background task.

    Attributes:
        QUEUED: Waiting in the task queue.
        RUNNING: Currently executing.
        COMPLETED: Finished successfully.
        FAILED: Finished with an error.
        CANCELLED: Cancelled by the user.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SupportedFormat(str, Enum):
    """RAW and output image formats the application can handle.

    Attributes:
        CR2: Canon RAW.
        CR3: Canon RAW (newer).
        NEF: Nikon RAW.
        ARW: Sony RAW.
        RAF: Fujifilm RAW.
        ORF: Olympus RAW.
        RW2: Panasonic RAW.
        DNG: Adobe Digital Negative.
        PEF: Pentax RAW.
        TIF: TIFF image (.tif).
        TIFF: TIFF image (.tiff).
        JPEG: JPEG output.
        PNG: PNG output.
    """

    CR2 = ".cr2"
    CR3 = ".cr3"
    NEF = ".nef"
    ARW = ".arw"
    RAF = ".raf"
    ORF = ".orf"
    RW2 = ".rw2"
    DNG = ".dng"
    PEF = ".pef"
    TIF = ".tif"
    TIFF = ".tiff"
    JPEG = ".jpg"
    PNG = ".png"

    @classmethod
    def raw_extensions(cls) -> set[str]:
        """Return the set of recognised RAW file extensions (lowercase)."""
        return {
            cls.CR2.value, cls.CR3.value, cls.NEF.value, cls.ARW.value,
            cls.RAF.value, cls.ORF.value, cls.RW2.value, cls.DNG.value,
            cls.PEF.value, cls.TIF.value, cls.TIFF.value,
        }

    @classmethod
    def output_extensions(cls) -> set[str]:
        """Return the set of recognised output file extensions (lowercase)."""
        return {cls.JPEG.value, cls.TIFF.value, cls.TIF.value, cls.PNG.value}

    @classmethod
    def all_extensions(cls) -> set[str]:
        """Return every supported extension."""
        return cls.raw_extensions() | cls.output_extensions()


class ExportFormat(str, Enum):
    """Output format to request from the CLI tool."""

    JPEG = "jpeg"
    TIFF = "tiff"
    PNG = "png"
