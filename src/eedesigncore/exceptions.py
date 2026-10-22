"""Exceptions raised by eedesigncore."""


class KicadParseError(ValueError):
    """Raised when a KiCad file cannot be parsed."""

    def __init__(
        self,
        message: str,
        file_path: str | None = None,
        line: int | None = None,
    ) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.line = line


class KicadWriteError(OSError):
    """Raised when a KiCad file cannot be written."""


class LibTableResolutionError(ValueError):
    """Raised when library-table discovery cannot complete.

    Causes: a nested-table chain deeper than the depth cap, a chain that
    exceeds the table-file budget, a reference outside the allowed roots, a
    file over the read cap, and, in strict mode, an unreadable or malformed
    table or a nested-table cycle.
    """


class SheetHierarchyError(ValueError):
    """Raised when a project sheet tree cannot be walked.

    Causes: a start path that is neither a ``.kicad_pro`` nor a ``.kicad_sch``
    file, a missing top-level sheet file, a sheet reference outside the project
    root, a file that appears as its own ancestor, a tree deeper than the depth
    cap, a tree over the file budget, a file over the read cap, and, in strict
    mode, a missing sub-sheet file.
    """


class ZstdBackendUnavailableError(RuntimeError):
    """Raised when no zstd backend imports.

    The library compresses and decompresses embedded files with zstd. It uses
    the standard library module ``compression.zstd`` on Python 3.14 and later.
    It uses the ``zstandard`` package on Python 3.11 to 3.13.
    """


class ZstdDecompressionError(KicadParseError):
    """Raised when a zstd frame is corrupt or larger than the decode limit."""


class EmbeddedFileChecksumError(KicadParseError):
    """Raised when embedded-file content does not match the stored checksum."""
