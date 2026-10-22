"""Explicit local outline-font loading and shaped SVG paths."""

from __future__ import annotations

import importlib
import io
import math
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from eedesigncore.common.font import StrokeFont, TextBox, default_font
from eedesigncore.common.font.markup import Node, parse
from eedesigncore.common.models import TextAttributes
from eedesigncore.common.paths import is_remote_uri
from eedesigncore.render.geometry import PointMap, SvgShape


def _modules() -> tuple[Any, Any]:
    try:
        return importlib.import_module("fontTools.ttLib"), importlib.import_module("uharfbuzz")
    except ImportError as error:
        raise ValueError("Outline fonts require the eedesigncore[fonts] dependencies.") from error


class OutlineFont:
    """A loaded TrueType or OpenType face. Rendering does not reopen the font file."""

    def __init__(self, path: str | Path, *, font_index: int = 0) -> None:
        if is_remote_uri(str(path)):
            raise ValueError("Font files require a local path.")
        self.path = Path(path).resolve(strict=True)
        if not self.path.is_file() or self.path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("A font must be a local file no larger than 64 MiB.")
        if font_index < 0:
            raise ValueError("The font collection index cannot be negative.")
        ttlib, hb = _modules()
        self._data = self.path.read_bytes()
        try:
            self._font = ttlib.TTFont(io.BytesIO(self._data), fontNumber=font_index)
            self._glyphs = self._font.getGlyphSet()
            self._order = self._font.getGlyphOrder()
            self._upem = int(self._font["head"].unitsPerEm)
            self._hb = hb.Font(hb.Face(self._data, font_index))
            self._hb.scale = (self._upem, self._upem)
            hb.ot_font_set_funcs(self._hb)
            header = self._font["hhea"]
            # KiCad divides the integer FreeType face height by units_per_EM before scaling.
            self._height = int((header.ascent - header.descent + header.lineGap) / self._upem)
            self._extent_height = float(header.ascent - header.descent) / self._upem
            self._bold = bool(self._font["head"].macStyle & 1)
            self._italic = bool(self._font["head"].macStyle & 2)
        except Exception as error:
            raise ValueError(
                "The selected file does not contain a supported outline font."
            ) from error
        self._named_family: str | None = None
        self._shape_cache: dict[str, tuple[tuple[int, int, int], ...]] = {}

    def for_style(self, bold: bool = False, italic: bool = False) -> OutlineFont:
        bold, italic = bold or self._bold, italic or self._italic
        if self._named_family and (bold != self._bold or italic != self._italic):
            try:
                return _named_font(self._named_family, bold, italic)
            except ValueError:
                pass
        return self

    def _shape(self, text: str) -> tuple[tuple[int, int, int], ...]:
        if len(text) > 100000:
            raise ValueError("Font shaping exceeds the text length limit.")
        if text in self._shape_cache:
            return self._shape_cache[text]
        _, hb = _modules()
        buffer = hb.Buffer()
        buffer.add_str(text)
        buffer.guess_segment_properties()
        hb.shape(self._hb, buffer)
        x = y = 0
        result = []
        for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions, strict=True):
            result.append((int(info.codepoint), x + int(pos.x_offset), y + int(pos.y_offset)))
            x += int(pos.x_advance)
            y += int(pos.y_advance)
        result.append((-1, x, y))
        if len(self._shape_cache) >= 512:
            self._shape_cache.pop(next(iter(self._shape_cache)))
        self._shape_cache[text] = tuple(result)
        return self._shape_cache[text]

    def _runs(
        self, text: str, size: tuple[int, int]
    ) -> tuple[float, list[tuple[int, float, float, float]], list[tuple[float, float, float]]]:
        glyphs: list[tuple[int, float, float, float]] = []
        bars: list[tuple[float, float, float]] = []

        def walk(nodes: tuple[Node, ...], cursor: float, factor: float, offset: float) -> float:
            for node in nodes:
                start = cursor
                if node.kind:
                    reduced = factor * 0.64 if node.kind in ("_", "^") else factor
                    shift = (
                        offset + reduced * size[1] * (0.35 if node.kind == "_" else -0.63)
                        if node.kind in ("_", "^")
                        else offset
                    )
                    cursor = walk(node.children, cursor, reduced, shift)
                    if node.kind == "~":
                        bars.append((start, cursor, offset - size[1] * factor * 1.23))
                else:
                    sx = size[0] * factor * 1.4 / self._upem
                    sy = size[1] * factor * 1.4 / self._upem
                    shaped = self._shape(node.text)
                    glyphs.extend(
                        (index, cursor + x * sx, offset - y * sy, factor)
                        for index, x, y in shaped[:-1]
                    )
                    cursor += shaped[-1][1] * sx
            return cursor

        return walk(parse(text), 0, 1, 0), glyphs, bars

    def advance(self, text: str, size: tuple[int, int]) -> int:
        return round(self._runs(text, size)[0])

    def line_pitch(self, height: float) -> float:
        return height * self._height * 1.68

    def native_text_box(
        self,
        text: str,
        size: tuple[int, int],
        thickness: int,
        *,
        bold: bool = False,
        italic: bool = False,
    ) -> TextBox:
        """Measure field placement with KiCad's integer outline-font cursor."""
        selected = self.for_style(bold, italic)
        face_size = int(16 * 64 * 1.4)

        def width(nodes: tuple[Node, ...], reduced: bool = False) -> int:
            result = 0
            scaler = round(face_size * 0.64) if reduced else face_size
            for node in nodes:
                if node.kind:
                    result += width(node.children, reduced or node.kind in ("_", "^"))
                else:
                    shaped = selected._shape(node.text)
                    cursor = sum(
                        int(round((b[1] - a[1]) * scaler * 16 / selected._upem) / 16)
                        for a, b in zip(shaped, shaped[1:], strict=False)
                    )
                    result += int(cursor * size[0] * 1.4 / face_size)
            return result

        return TextBox(0, 0, width(parse(text)), round(size[1] * 1.4 * selected._extent_height))

    def text_box(
        self,
        text: str,
        size: tuple[int, int],
        thickness: int,
        *,
        bold: bool = False,
        italic: bool = False,
    ) -> TextBox:
        selected = self.for_style(bold, italic)
        return TextBox(
            0, 0, selected.advance(text, size), round(size[1] * 1.4 * selected._extent_height)
        )

    def _commands(
        self, text: str, size: tuple[int, int], *, italic: bool = False
    ) -> tuple[float, Any]:
        recording = importlib.import_module("fontTools.pens.recordingPen")
        transform = importlib.import_module("fontTools.pens.transformPen")
        pen = recording.DecomposingRecordingPen(self._glyphs)
        width, glyphs, bars = self._runs(text, size)
        for index, x, y, factor in glyphs:
            sx, sy = (value * factor * 1.4 / self._upem for value in size)
            shear = sy * 0.125 if italic and not self._italic else 0.0
            target = transform.TransformPen(pen, (sx, 0, shear, -sy, x, y))
            self._glyphs[self._order[index]].draw(target)
        for start, end, y in bars:
            pen.moveTo((start, y))
            pen.lineTo((end, y))
            pen.lineTo((end, y + size[1] / 15))
            pen.lineTo((start, y + size[1] / 15))
            pen.closePath()
        return width, pen

    def svg_shape(
        self,
        text: str,
        position: tuple[float, float],
        angle: float,
        attrs: TextAttributes,
        precision: int,
        *,
        halign: str | None = None,
        valign: str | None = None,
        to_board: PointMap | None = None,
    ) -> SvgShape:
        selected = self.for_style(attrs.bold, attrs.italic)
        size = tuple(round(value * 1e6) for value in attrs.font_size)
        width, commands = selected._commands(text, (size[0], size[1]), italic=attrs.italic)
        horizontal = halign or attrs.justify_h.value
        vertical = valign or attrs.justify_v.value
        dx = -width * {"left": 0, "center": 0.5, "right": 1}[horizontal]
        dy = size[1] * (1 - 1.17 * {"top": 0, "center": 0.5, "bottom": 1}[vertical])
        radians = math.radians(angle)
        c, s = math.cos(radians), math.sin(radians)
        mirror = -1 if attrs.mirror else 1
        matrix = (
            mirror * c / 1e6,
            -mirror * s / 1e6,
            s / 1e6,
            c / 1e6,
            position[0] + (mirror * dx * c + dy * s) / 1e6,
            position[1] + (-mirror * dx * s + dy * c) / 1e6,
        )
        if to_board is not None:
            origin = to_board((0.0, 0.0))
            x_axis, y_axis = to_board((1.0, 0.0)), to_board((0.0, 1.0))
            a, b = x_axis[0] - origin[0], x_axis[1] - origin[1]
            c, d = y_axis[0] - origin[0], y_axis[1] - origin[1]
            xx, xy, yx, yy, tx, ty = matrix
            matrix = (
                a * xx + c * xy,
                b * xx + d * xy,
                a * yx + c * yy,
                b * yx + d * yy,
                a * tx + c * ty + origin[0],
                b * tx + d * ty + origin[1],
            )
        svg = importlib.import_module("fontTools.pens.svgPathPen")
        transform = importlib.import_module("fontTools.pens.transformPen")
        pen = svg.SVGPathPen(None, ntos=lambda n: f"{n:.{precision}f}")
        commands.replay(transform.TransformPen(pen, matrix))
        return SvgShape(
            "path", (("d", str(pen.getCommands())), ("fill-rule", "nonzero")), filled=True
        )

    def text_strokes(
        self,
        text: str,
        size: tuple[int, int],
        position: tuple[int, int],
        thickness: int,
        *,
        angle: float = 0.0,
        mirror: bool = False,
        italic: bool = False,
        halign: str = "center",
        valign: str = "center",
    ) -> list[list[tuple[int, int]]]:
        recording = importlib.import_module("fontTools.pens.recordingPen")
        transform = importlib.import_module("fontTools.pens.transformPen")
        curves = importlib.import_module("fontTools.pens.qu2cuPen")
        width, commands = self._commands(text, size, italic=italic)
        dx = -width * {"left": 0, "center": 0.5, "right": 1}[halign]
        dy = size[1] * (1 - 1.17 * {"top": 0, "center": 0.5, "bottom": 1}[valign])
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        sign = -1 if mirror else 1
        target = recording.RecordingPen()
        matrix = (
            sign * c,
            -sign * s,
            s,
            c,
            position[0] + sign * dx * c + dy * s,
            position[1] - sign * dx * s + dy * c,
        )
        commands.replay(
            curves.Qu2CuPen(transform.TransformPen(target, matrix), max_err=1.0, all_cubic=True)
        )
        runs: list[list[tuple[int, int]]] = []
        run: list[tuple[int, int]] = []
        for operation, points in target.value:
            if operation == "moveTo":
                run = [(round(points[0][0]), round(points[0][1]))]
            elif operation == "lineTo":
                run.append((round(points[0][0]), round(points[0][1])))
            elif operation == "curveTo":
                start = run[-1]
                a, b, end = points
                for step in range(1, 33):
                    t = step / 32
                    run.append(
                        tuple(
                            round(
                                (1 - t) ** 3 * start[i]
                                + 3 * (1 - t) ** 2 * t * a[i]
                                + 3 * (1 - t) * t * t * b[i]
                                + t**3 * end[i]
                            )
                            for i in (0, 1)
                        )
                    )
            elif operation in ("closePath", "endPath"):
                if operation == "closePath" and run:
                    run.append(run[0])
                if run:
                    runs.append(run)
                run = []
        return runs


def _font_paths() -> list[Path]:
    paths: set[Path] = set()
    if sys.platform == "win32":
        import winreg

        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
                ) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        _, value, _ = winreg.EnumValue(key, index)
                        if isinstance(value, str):
                            path = Path(value)
                            if not is_remote_uri(value):
                                paths.add(path if path.is_absolute() else fonts / path)
            except FileNotFoundError:
                continue
    else:
        roots = (
            [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
            if sys.platform == "darwin"
            else [
                Path("/usr/share/fonts"),
                Path("/usr/local/share/fonts"),
                Path.home() / ".local/share/fonts",
                Path.home() / ".fonts",
            ]
        )
        for root in roots:
            if root.is_dir():
                paths.update(
                    p for p in root.rglob("*") if p.suffix.lower() in (".ttf", ".otf", ".ttc")
                )
    return sorted(paths)


@lru_cache(maxsize=32)
def _named_font(name: str, bold: bool, italic: bool) -> OutlineFont:
    ttlib, _ = _modules()
    for path in _font_paths():
        try:
            with ttlib.TTFont(path, fontNumber=0, lazy=True) as face:
                names = {
                    record.toUnicode().casefold()
                    for record in face["name"].names
                    if record.nameID in (1, 4, 16)
                }
                selection = int(face["OS/2"].fsSelection) if "OS/2" in face else 0
                style = (1 if selection & 32 else 0) | (2 if selection & 1 else 0)
                matches = (
                    name.casefold() in names
                    and bool(style & 1) == bold
                    and bool(style & 2) == italic
                )
            if matches:
                result = OutlineFont(path)
                result._named_family = name
                return result
        except (OSError, ValueError, ttlib.TTLibError):
            continue
    raise ValueError(f"The requested installed font and style were not found: {name}.")


def load_font(
    source: str | Path, *, bold: bool = False, italic: bool = False, font_index: int = 0
) -> OutlineFont | StrokeFont:
    """Load a local font file or an installed family. Use KiCad Font for Newstroke."""
    if str(source).casefold() in ("kicad font", "default font"):
        return default_font()
    if (
        isinstance(source, Path)
        or Path(source).suffix.lower() in (".ttf", ".otf", ".ttc")
        or "/" in str(source)
        or "\\" in str(source)
    ):
        return OutlineFont(source, font_index=font_index)
    return _named_font(source, bold, italic)
