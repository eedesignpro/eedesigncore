"""Strict drawing-sheet grammar and KiCad item-order serialization."""

from __future__ import annotations

import base64
from pathlib import Path

from eedesigncore.common.models import Color
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.nodes import SExprNode
from eedesigncore.sexpr.parser import parse
from eedesigncore.sexpr.writer import emit_flat, prettify
from eedesigncore.wks.models import (
    DrawingSheet,
    DrawingSheetCoordinate,
    DrawingSheetItem,
    DrawingSheetSetup,
)
from eedesigncore.wks.text import legacy_text


def children(node: SExprNode) -> list[SExprNode]:
    if not isinstance(node.value, list):
        raise KicadParseError("Expected a drawing-sheet list.")
    return node.value


def atom(node: SExprNode) -> str | int | float:
    if isinstance(node.value, list):
        raise KicadParseError("Expected a drawing-sheet value.")
    return node.value


def field(node: SExprNode) -> tuple[str, list[SExprNode]]:
    values = children(node)
    if not values:
        raise KicadParseError("Empty drawing-sheet field.")
    return str(atom(values[0])), values[1:]


def coordinate(values: list[SExprNode]) -> DrawingSheetCoordinate:
    if len(values) not in (2, 3):
        raise KicadParseError(
            "A drawing-sheet coordinate needs two values and an optional anchor."
        )
    return DrawingSheetCoordinate.model_validate(
        dict(
            x=atom(values[0]),
            y=atom(values[1]),
            anchor=atom(values[2]) if len(values) == 3 else "rbcorner",
        )
    )


_NAMES = {
    "name": "name",
    "comment": "comment",
    "linewidth": "line_width",
    "rotate": "rotation",
    "repeat": "repeat",
    "maxlen": "max_length",
    "maxheight": "max_height",
    "incrlabel": "increment_label",
    "scale": "image_scale",
}
_COMMON = {"name", "comment", "option", "repeat", "incrx", "incry", "linewidth"}
_ALLOWED = {
    "line": _COMMON | {"start", "end"},
    "rect": _COMMON | {"start", "end"},
    "tbtext": (_COMMON - {"linewidth"})
    | {"pos", "rotate", "font", "justify", "maxlen", "maxheight", "incrlabel"},
    "polygon": _COMMON | {"pos", "rotate", "pts"},
    "bitmap": _COMMON | {"pos", "scale", "data", "pngdata"},
}
_SETUP = {
    "textsize": "text_size",
    "linewidth": "line_width",
    "textlinewidth": "text_line_width",
    "left_margin": "left_margin",
    "right_margin": "right_margin",
    "top_margin": "top_margin",
    "bottom_margin": "bottom_margin",
}


def load_wks(text: str) -> DrawingSheet:
    try:
        tag, nodes = field(parse(text))
        if tag not in ("kicad_wks", "drawing_sheet", "page_layout"):
            raise KicadParseError("Expected a drawing-sheet root.")
        result = DrawingSheet(version=0 if tag == "page_layout" else 20231118)
        if tag != "page_layout" and (not nodes or field(nodes[0])[0] != "version"):
            raise KicadParseError("The drawing sheet is missing its version.")
        for node in nodes:
            key, values = field(node)
            if key in ("version", "generator", "generator_version"):
                if len(values) != 1:
                    raise KicadParseError("Invalid drawing-sheet header.")
                if key == "version":
                    result.version = int(atom(values[0]))
                    if result.version > 20231118:
                        raise KicadParseError(
                            "The drawing-sheet version is newer than the supported format."
                        )
                else:
                    setattr(result, key, str(atom(values[0])))
            elif key == "setup":
                settings: dict[str, object] = {}
                for entry in values:
                    name, args = field(entry)
                    if name not in _SETUP or len(args) != (2 if name == "textsize" else 1):
                        raise KicadParseError(f"Invalid drawing-sheet setup field: {name}.")
                    settings[_SETUP[name]] = (
                        tuple(atom(v) for v in args) if name == "textsize" else atom(args[0])
                    )
                result.setup = DrawingSheetSetup.model_validate(settings)
            elif key in _ALLOWED:
                result.items.append(read_item(key, values))
            else:
                raise KicadParseError(f"Unknown drawing-sheet item: {key}.")
        result._source = text.replace("\r\n", "\n")
        result._snapshot = result.model_dump_json()
        return result
    except (ValueError, TypeError, IndexError, OverflowError) as error:
        if isinstance(error, KicadParseError):
            raise
        raise KicadParseError(f"Invalid drawing sheet: {error}") from error


def read_item(kind: str, values: list[SExprNode]) -> DrawingSheetItem:
    data: dict[str, object] = {"kind": kind}
    if kind == "tbtext":
        data["text"] = str(atom(values[0]))
        values = values[1:]
    increment = [0.0, 0.0]
    polygons: list[list[tuple[float, float]]] = []
    for entry in values:
        key, args = field(entry)
        if key not in _ALLOWED[kind]:
            raise KicadParseError(f"Unknown {kind} field: {key}.")
        if key in _NAMES:
            if len(args) != 1:
                raise KicadParseError(f"Expected one value for {key}.")
            data[_NAMES[key]] = atom(args[0])
        elif key in ("pos", "start", "end"):
            data["end" if key == "end" else "position"] = coordinate(args)
        elif key in ("incrx", "incry"):
            if len(args) != 1:
                raise KicadParseError("Invalid repeat increment.")
            increment[key == "incry"] = float(atom(args[0]))
        elif key == "option":
            if not args or any(atom(a) not in ("page1only", "notonpage1") for a in args):
                raise KicadParseError("Invalid drawing-sheet page option.")
            data["page_option"] = atom(args[-1])
        elif key == "pts":
            points = []
            for point in args:
                name, xy = field(point)
                if name != "xy" or len(xy) != 2:
                    raise KicadParseError("Invalid polygon point.")
                points.append((float(atom(xy[0])), float(atom(xy[1]))))
            polygons.append(points)
        elif key == "font":
            for setting in args:
                if not isinstance(setting.value, list):
                    flag = str(atom(setting))
                    if flag not in ("bold", "italic"):
                        raise KicadParseError("Invalid font flag.")
                    data[flag] = True
                    continue
                name, parts = field(setting)
                if name == "color" and len(parts) == 4:
                    data["color"] = Color(
                        r=int(atom(parts[0])),
                        g=int(atom(parts[1])),
                        b=int(atom(parts[2])),
                        a=float(atom(parts[3])),
                    )
                elif name == "size" and len(parts) == 2:
                    data["text_size"] = tuple(float(atom(v)) for v in parts)
                elif name in ("face", "linewidth") and len(parts) == 1:
                    data["font_face" if name == "face" else "line_width"] = atom(parts[0])
                else:
                    raise KicadParseError(f"Invalid drawing-sheet font field: {name}.")
        elif key == "justify":
            for value in args:
                word = str(atom(value))
                if word == "center":
                    data.update(justify_h="center", justify_v="center")
                elif word in ("left", "right"):
                    data["justify_h"] = word
                elif word in ("top", "bottom"):
                    data["justify_v"] = word
                else:
                    raise KicadParseError("Invalid text justification.")
        elif key == "data":
            data["image_data"] = "".join(str(atom(v)) for v in args)
        elif key == "pngdata":
            chunks = []
            for chunk in args:
                name, parts = field(chunk)
                if name != "data" or len(parts) != 1:
                    raise KicadParseError("Invalid legacy image data.")
                chunks.append(str(atom(parts[0])))
            data["image_data"] = base64.b64encode(bytes.fromhex(" ".join(chunks))).decode("ascii")
    data.update(increment=tuple(increment), polygons=polygons)
    result = DrawingSheetItem.model_validate(data)
    return result


def node(tag: str, *values: str | float | int | SExprNode) -> SExprNode:
    def make(value: str | float | int | SExprNode) -> SExprNode:
        if isinstance(value, SExprNode):
            return value
        if isinstance(value, str):
            return SExprNode.string(value)
        if isinstance(value, int):
            return SExprNode.integer(value)
        number = (
            format(value, ".16f").rstrip("0").rstrip(".")
            if value != 0 and abs(value) <= 0.0001
            else format(value, ".10g")
        )
        return SExprNode.double(value, raw=number)

    return SExprNode.make_list([SExprNode.symbol(tag), *(make(v) for v in values)])


def coord(tag: str, value: DrawingSheetCoordinate) -> SExprNode:
    return node(
        tag,
        value.x,
        value.y,
        *([SExprNode.symbol(value.anchor)] if value.anchor != "rbcorner" else []),
    )


def dump_wks(sheet: DrawingSheet) -> str:
    DrawingSheet.model_validate(sheet.model_dump())
    if sheet._source is not None and sheet.model_dump_json() == sheet._snapshot:
        return sheet._source
    sheet = DrawingSheet.model_validate(sheet.model_dump())
    setup = sheet.setup
    fields = [
        node("version", 20231118),
        node("generator", sheet.generator),
        node("generator_version", sheet.generator_version),
    ]
    fields.append(
        node(
            "setup",
            *(
                node(
                    key, *(getattr(setup, value) if key == "textsize" else [getattr(setup, value)])
                )
                for key, value in _SETUP.items()
            ),
        )
    )
    for item in sheet.items:
        if item.kind == "bitmap" and not item.image_data:
            continue
        values: list[str | float | int | SExprNode] = (
            [legacy_text(item.text, sheet.version)] if item.kind == "tbtext" else []
        )
        values += [
            node("name", item.name),
            coord("start" if item.kind in ("line", "rect") else "pos", item.position),
        ]
        if item.kind in ("line", "rect"):
            values.append(coord("end", item.end))
        if item.page_option != "all":
            values.append(node("option", SExprNode.symbol(item.page_option)))
        if item.kind == "tbtext":
            if item.rotation:
                values.append(node("rotate", item.rotation))
            font = []
            if item.font_face:
                font.append(node("face", item.font_face))
            if item.line_width:
                font.append(node("linewidth", item.line_width))
            if any(item.text_size):
                font.append(node("size", *item.text_size))
            if item.bold:
                font.append(SExprNode.symbol("bold"))
            if item.italic:
                font.append(SExprNode.symbol("italic"))
            if item.color:
                font.append(node("color", item.color.r, item.color.g, item.color.b, item.color.a))
            if font:
                values.append(node("font", *font))
            justify = []
            if item.justify_h != "left":
                justify.append(SExprNode.symbol(item.justify_h))
            if item.justify_v != "center":
                justify.append(SExprNode.symbol(item.justify_v))
            if justify:
                values.append(node("justify", *justify))
            if item.max_length:
                values.append(node("maxlen", item.max_length))
            if item.max_height:
                values.append(node("maxheight", item.max_height))
        elif (
            item.kind in ("line", "rect")
            and item.line_width
            and item.line_width != setup.line_width
        ):
            values.append(node("linewidth", item.line_width))
        elif item.kind == "bitmap":
            values.append(node("scale", item.image_scale))
        if item.repeat > 1:
            values.append(node("repeat", item.repeat))
            if item.increment[0]:
                values.append(node("incrx", item.increment[0]))
            if item.increment[1]:
                values.append(node("incry", item.increment[1]))
            if item.kind == "tbtext" and item.increment_label != 1:
                values.append(node("incrlabel", item.increment_label))
        if item.kind == "polygon":
            if item.rotation:
                values.append(node("rotate", item.rotation))
            if item.line_width:
                values.append(node("linewidth", item.line_width))
        if item.comment:
            values.append(node("comment", item.comment))
        if item.kind == "polygon":
            values.extend(
                node("pts", *(node("xy", *p) for p in contour)) for contour in item.polygons
            )
        if item.kind == "bitmap":
            values.append(
                node(
                    "data",
                    *(item.image_data[i : i + 76] for i in range(0, len(item.image_data), 76)),
                )
            )
        fields.append(node(item.kind, *values))
    return prettify(emit_flat(node("kicad_wks", *fields)))


def write_wks(sheet: DrawingSheet, path: str | Path) -> None:
    sheet.to_file(path)
