"""Explicit installed-font matching with a recorded substitution result."""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from eedesigncore.common.paths import is_remote_uri
from eedesigncore.render.fonts import OutlineFont, load_font


@dataclass(frozen=True)
class FontResolution:
    """Keep the requested family separate from the actual loaded face."""

    requested_family: str
    requested_bold: bool
    requested_italic: bool
    resolved_family: str
    resolved_style: str
    path: Path
    face_index: int
    substituted: bool
    source: Literal["installed", "embedded"]
    font: OutlineFont


def resolve_font(
    family: str,
    *,
    bold: bool = False,
    italic: bool = False,
    policy: Literal["strict", "kicad-system"] = "strict",
    embedded_files: Sequence[Path] = (),
) -> FontResolution:
    """Resolve an outline family without changing the requested design font.

    Strict matching preserves load_font behavior. KiCad-system uses Linux
    Fontconfig substitution with caller-supplied extracted embedded font files.
    Reuse the returned face for measurement and rendering. No global font state
    is modified by this function. Fontconfig retains its normal system behavior.
    """
    if not family or len(family) > 4096 or "\0" in family or is_remote_uri(family):
        raise ValueError("Supply a nonempty local font family name.")
    if policy == "strict":
        if embedded_files:
            raise ValueError("Embedded font registration requires the KiCad-system policy.")
        font = load_font(family, bold=bold, italic=italic)
        if not isinstance(font, OutlineFont):
            raise ValueError("Font resolution requires an outline font family.")
        return FontResolution(
            family, bold, italic, family, "", font.path, 0, False, "installed", font
        )
    if policy != "kicad-system":
        raise ValueError("Unknown font resolution policy.")
    if not sys.platform.startswith("linux"):
        raise ValueError("KiCad-system font resolution currently requires Linux Fontconfig.")
    if len(embedded_files) > 128:
        raise ValueError("Too many embedded font files.")
    paths = []
    for value in embedded_files:
        if is_remote_uri(str(value)):
            raise ValueError("Embedded fonts require local paths.")
        path = value.resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Embedded fonts require local files no larger than 64 MiB.")
        paths.append(path)
    effective_bold = bold or any(
        word in family.casefold() for word in ("bold", "heavy", "black", "thick", "dark")
    )
    filename, face_index, actual_family, style = _match(family, effective_bold, italic, paths)
    font = OutlineFont(filename, font_index=face_index)
    lower_style = style.casefold()
    actual_bold = any(word in lower_style for word in ("bold", "heavy", "black", "thick", "dark"))
    if any(word in lower_style for word in ("thin", "light", "regular", "roman", "book")):
        actual_bold = False
    elif any(word in lower_style for word in ("medium", "semibold", "demibold")):
        actual_bold = effective_bold
    actual_italic = any(word in lower_style for word in ("italic", "oblique", "slant"))
    same_family = actual_family.casefold().startswith(family.casefold())
    if same_family and ((effective_bold and not actual_bold) or (italic and not actual_italic)):
        raise ValueError("The matched face requires unsupported KiCad synthetic font styling.")
    substituted = not same_family or effective_bold != actual_bold or italic != actual_italic
    return FontResolution(
        family,
        bold,
        italic,
        actual_family,
        style,
        filename,
        face_index,
        substituted,
        "embedded" if filename in paths else "installed",
        font,
    )


def _match(
    family: str, bold: bool, italic: bool, embedded: Sequence[Path]
) -> tuple[Path, int, str, str]:
    library = ctypes.util.find_library("fontconfig")
    if not library:
        raise ValueError("Linux Fontconfig is unavailable.")
    fc = ctypes.CDLL(library)
    pointer = ctypes.c_void_p
    char_pointer = ctypes.POINTER(ctypes.c_char_p)
    declarations = {
        "FcInitLoadConfigAndFonts": ([], pointer),
        "FcConfigDestroy": ([pointer], None),
        "FcPatternCreate": ([], pointer),
        "FcPatternDestroy": ([pointer], None),
        "FcPatternAddString": ([pointer, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_int),
        "FcConfigAppFontAddFile": ([pointer, ctypes.c_char_p], ctypes.c_int),
        "FcConfigSubstitute": ([pointer, pointer, ctypes.c_int], ctypes.c_int),
        "FcDefaultSubstitute": ([pointer], None),
        "FcFontMatch": ([pointer, pointer, ctypes.POINTER(ctypes.c_int)], pointer),
        "FcPatternGetString": (
            [pointer, ctypes.c_char_p, ctypes.c_int, char_pointer],
            ctypes.c_int,
        ),
        "FcPatternGetInteger": (
            [pointer, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
            ctypes.c_int,
        ),
    }
    for name, (arguments, return_type) in declarations.items():
        function = getattr(fc, name)
        function.argtypes = arguments
        function.restype = return_type
    config, pattern, match = fc.FcInitLoadConfigAndFonts(), fc.FcPatternCreate(), None
    try:
        if not config or not pattern:
            raise ValueError("Fontconfig could not create a matching context.")
        for path in embedded:
            if not fc.FcConfigAppFontAddFile(config, str(path).encode()):
                raise ValueError("Fontconfig could not register an embedded font.")
        values = [(b"family", family.encode())]
        if bold:
            values.append((b"style", b"Bold"))
        if italic:
            values.append((b"style", b"Italic"))
        for key, value in values:
            if not fc.FcPatternAddString(pattern, key, value):
                raise ValueError("Fontconfig could not create the requested font pattern.")
        if not fc.FcConfigSubstitute(config, pattern, 0):
            raise ValueError("Fontconfig substitution failed.")
        fc.FcDefaultSubstitute(pattern)
        result = ctypes.c_int()
        match = fc.FcFontMatch(config, pattern, ctypes.byref(result))
        if not match or result.value != 0:
            raise ValueError("Fontconfig found no usable font.")

        def string(key: bytes) -> str:
            value = ctypes.c_char_p()
            if fc.FcPatternGetString(match, key, 0, ctypes.byref(value)) != 0 or not value.value:
                raise ValueError("Fontconfig returned incomplete font metadata.")
            return value.value.decode("utf-8")

        index = ctypes.c_int()
        if fc.FcPatternGetInteger(match, b"index", 0, ctypes.byref(index)) != 0:
            raise ValueError("Fontconfig returned no face index.")
        if not 0 <= index.value < 65536:
            raise ValueError("Named variable-font instances are not supported.")
        return (
            Path(string(b"file")).resolve(strict=True),
            index.value,
            string(b"family"),
            string(b"style"),
        )
    finally:
        if match:
            fc.FcPatternDestroy(match)
        if pattern:
            fc.FcPatternDestroy(pattern)
        if config:
            fc.FcConfigDestroy(config)
