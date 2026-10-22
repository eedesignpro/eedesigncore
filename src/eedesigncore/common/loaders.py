"""Shared loader helper functions reused across format sub-packages.

Functions here are extracted from ``sym/loader.py`` and are format-agnostic
(they only depend on ``common/models.py`` and the S-expression node types).
"""

from __future__ import annotations

import math
import re

from eedesigncore.common.models import (
    Color,
    EmbeddedFile,
    Fill,
    FillType,
    JustifyH,
    JustifyV,
    Stroke,
    StrokeType,
    TextAttributes,
)
from eedesigncore.common.page import Paper, TitleBlock
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType

# ---------------------------------------------------------------------------
# Low-level node helpers (re-exported for convenience)
# ---------------------------------------------------------------------------


def require_sym(node: SExprNode, name: str) -> None:
    """Raise ``KicadParseError`` if the first atom of *node* does not match *name*."""
    if node.node_type != SExprNodeType.LIST:
        raise KicadParseError(f"Expected list with '{name}', got scalar")
    children = node.value
    assert isinstance(children, list)
    if not children:
        raise KicadParseError(f"Expected '{name}' token, got empty list")
    first = children[0]
    if first.node_type != SExprNodeType.SYMBOL or first.value != name:
        raise KicadParseError(f"Expected token '{name}', got '{first.value}'")


def sym_name(node: SExprNode) -> str:
    """Return the symbol (keyword) name of the first child of a list node."""
    assert isinstance(node.value, list)
    return str(node.value[0].value)


def children(node: SExprNode) -> list[SExprNode]:
    """Return children of a LIST node (excluding the first keyword atom).

    Use :func:`elements` instead when the list has NO keyword atom, meaning its
    first element is data. ``(jumper_pin_groups ("1" "3"))`` is the live case.
    """
    assert isinstance(node.value, list)
    return node.value[1:]


def elements(node: SExprNode) -> list[SExprNode]:
    """Return EVERY element of a LIST node, including the first.

    KiCad writes a few lists whose first element is data rather than a keyword.
    A jumper group is one: ``("1" "3")`` names two pins and no token. Passing
    such a list to :func:`children` silently drops its first member, because
    that helper assumes element zero is a keyword to skip.
    """
    assert isinstance(node.value, list)
    return node.value


def get_str(node: SExprNode) -> str:
    """Return string value of a STRING or SYMBOL node."""
    if node.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL):
        return str(node.value)
    raise KicadParseError(f"Expected string/symbol, got {node.node_type}")


# ``isNumber`` from KiCad's own lexer (``common/dsnlexer.cpp:491-493``), whose
# documented regex is ``^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?``. Our tokeniser
# is narrower: it needs a dot before an exponent and it rejects a leading plus,
# so ``-1e-06`` and ``+5`` arrive as SYMBOL atoms.
#
# The classification is NOT widened to match, on purpose. KiCad's rule also
# calls a legacy hex-looking tstamp such as ``586432E5`` a number, and four
# files in the corpus carry one. Turning those into a float would write back
# ``5.86432e+12`` and destroy the identifier.
#
# KiCad never has that problem, because it never converts at the lexer.
# ``DSNLEXER::parseDouble`` (``dsnlexer.cpp:864-878``) reads ``CurStr()`` and
# runs ``fast_float::from_chars`` on the TEXT, and a tstamp reads the same text
# through ``CurStrToKIID``. The token type is only an acceptance gate. So the
# gate is what this mirrors: a numeric accessor accepts an atom whose TEXT is a
# KiCad number, and the node keeps its type for everything else.
_KICAD_NUMBER = re.compile(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?")


def _numeric_text(node: SExprNode) -> str | None:
    """Return the atom's text when KiCad's lexer would call it a number."""
    if node.node_type != SExprNodeType.SYMBOL:
        return None
    text = str(node.value)
    return text if _KICAD_NUMBER.fullmatch(text) else None


def get_float(node: SExprNode) -> float:
    """Return numeric value of a DOUBLE or INTEGER node as float.

    A non-finite value is rejected here, at the one point where a file number
    enters the model layer (LOW-10). ``float("1.0e400")`` returns infinity rather
    than raising, and an infinity later reaches ``math.floor`` in ``kiround`` or a
    bare ``int()`` in a save-order comparator, both of which raise
    ``OverflowError``. That escapes the ``KicadParseError`` contract, so a caller
    guarding a batch job aborts instead of recording one bad file. An INTEGER atom
    too wide for a float overflows in the same way.
    """
    if node.node_type == SExprNodeType.DOUBLE:
        assert isinstance(node.value, float)
        if not math.isfinite(node.value):
            token = node.raw if node.raw is not None else repr(node.value)
            raise KicadParseError(f"Expected finite number, got '{token}'")
        return node.value
    if node.node_type == SExprNodeType.INTEGER:
        assert isinstance(node.value, int)
        try:
            value = float(node.value)
        except OverflowError:
            raise KicadParseError(f"Integer too large for a coordinate: {node.value!r}") from None
        return value
    text = _numeric_text(node)
    if text is not None:
        value = float(text)
        if not math.isfinite(value):
            raise KicadParseError(f"Expected finite number, got '{text}'")
        return value
    raise KicadParseError(f"Expected number, got {node.node_type} ({node.value!r})")


def get_int(node: SExprNode) -> int:
    """Return integer value of an INTEGER node."""
    if node.node_type == SExprNodeType.INTEGER:
        assert isinstance(node.value, int)
        return node.value
    text = _numeric_text(node)
    if text is not None:
        # KiCad reaches the same value: parseInt calls strtol on CurText, which
        # stops at the first character it cannot use.
        try:
            return int(text)
        except ValueError:
            return int(float(text))
    raise KicadParseError(f"Expected integer, got {node.node_type} ({node.value!r})")


def find_child(parent: SExprNode, name: str) -> SExprNode | None:
    """Find the first child list with the given keyword name, or None."""
    assert isinstance(parent.value, list)
    for child in parent.value[1:]:
        if child.node_type == SExprNodeType.LIST:
            c = child.value
            assert isinstance(c, list)
            if c and c[0].node_type == SExprNodeType.SYMBOL and c[0].value == name:
                return child
    return None


def find_all_children(parent: SExprNode, name: str) -> list[SExprNode]:
    """Find all child lists with the given keyword name."""
    result = []
    assert isinstance(parent.value, list)
    for child in parent.value[1:]:
        if child.node_type == SExprNodeType.LIST:
            c = child.value
            assert isinstance(c, list)
            if c and c[0].node_type == SExprNodeType.SYMBOL and c[0].value == name:
                result.append(child)
    return result


def bool_token(node: SExprNode) -> bool:
    """Parse ``yes``/``no`` from the second atom of a list like ``(hide yes)``."""
    assert isinstance(node.value, list)
    val = node.value[1].value
    if val == "yes":
        return True
    if val == "no":
        return False
    raise KicadParseError(f"Expected yes/no boolean, got {val!r}")


def opt_bool_token(node: SExprNode) -> bool | None:
    """Parse ``yes``/``no``/``none`` from a list like ``(front none)``.

    ``parseOptBool`` (``pcb_io_kicad_sexpr_parser.cpp:245-259``) takes THREE
    tokens, not two. ``none`` is an empty optional, meaning the value is not set
    rather than set to false. Every via and pad protection value uses this
    reader, so :func:`bool_token` raises on a file KiCad writes.
    """
    assert isinstance(node.value, list)
    val = node.value[1].value
    if val == "yes":
        return True
    if val == "no":
        return False
    if val == "none":
        return None
    raise KicadParseError(f"Expected yes/no/none opt-bool, got {val!r}")


def front_back_opt_bool(
    node: SExprNode, *, allow_legacy: bool = False
) -> tuple[bool | None, bool | None]:
    """Parse a ``(tenting|covering|plugging …)`` block into two optionals.

    Port of ``parseFrontBackOptBool``
    (``pcb_io_kicad_sexpr_parser.cpp:7658-7723``). Two spellings exist.

    The modern one nests a list per side, and each value is a tri-state::

        (tenting (front yes) (back none))

    The legacy one names the sides as bare words, and it carries no value::

        (tenting front back)     both set
        (tenting front)          front set only
        (tenting none)           neither set

    *allow_legacy* mirrors the C++ parameter. Four call sites pass it, all for
    ``tenting``: board setup (``:2742``), pad (``:6340``), padstack layer
    (``:6799``) and via (``:7505``). ``covering`` and ``plugging`` never take the
    legacy form, so a bare word there is a parse error in KiCad too.

    A side the block does not mention stays None. The caller decides what that
    means: board setup collapses it with ``value_or(false)`` at ``:2744-2745``,
    while a pad and a via keep the optional.
    """
    front: bool | None = None
    back: bool | None = None
    kids = children(node)

    if kids and kids[0].node_type != SExprNodeType.LIST:
        if not allow_legacy:
            raise KicadParseError(
                f"Expected (front …)/(back …) in {sym_name(node)!r}, got a bare word"
            )
        for kid in kids:
            word = kid.value
            if word == "front":
                front = True
            elif word == "back":
                back = True
            elif word == "none":
                front = None
                back = None
            else:
                raise KicadParseError(f"Expected front, back or none, got {word!r}")
        return front, back

    for kid in kids:
        if kid.node_type != SExprNodeType.LIST:
            continue
        name = sym_name(kid)
        if name == "front":
            front = opt_bool_token(kid)
        elif name == "back":
            back = opt_bool_token(kid)
        else:
            raise KicadParseError(f"Expected front or back, got {name!r}")
    return front, back


# ---------------------------------------------------------------------------
# Color, Stroke, Fill
# ---------------------------------------------------------------------------


def load_color(node: SExprNode) -> Color:
    """Parse ``(color r g b a)``."""
    require_sym(node, "color")
    ch = children(node)
    return Color(
        r=int(get_float(ch[0])),
        g=int(get_float(ch[1])),
        b=int(get_float(ch[2])),
        a=get_float(ch[3]),
    )


def load_stroke(node: SExprNode) -> Stroke:
    """Parse ``(stroke (width w) (type t) (color r g b a)?)``."""
    require_sym(node, "stroke")
    width = 0.0
    stroke_type = StrokeType.default
    color = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        name = sym_name(child)
        if name == "width":
            width = get_float(children(child)[0])
        elif name == "type":
            type_val = str(children(child)[0].value)
            try:
                stroke_type = StrokeType(type_val)
            except ValueError as err:
                raise KicadParseError(f"Unknown stroke type: {type_val!r}") from err
        elif name == "color":
            color = load_color(child)
        else:
            raise KicadParseError(f"Unknown token in stroke: {name!r}")

    return Stroke(width=width, type=stroke_type, color=color)


def load_fill(node: SExprNode) -> Fill:
    """Parse ``(fill (type t) (color r g b a)?)`` OR ``(fill yes|no|hatch|...)``."""
    require_sym(node, "fill")
    ch = children(node)

    # Handle simple bool form: (fill yes) or (fill no)
    # Also handle bare keywords: (fill hatch) (fill reverse_hatch) etc.
    if ch and ch[0].node_type == SExprNodeType.SYMBOL:
        val = str(ch[0].value)
        if val == "yes":
            return Fill(type=FillType.outline)
        elif val == "no":
            return Fill(type=FillType.none)
        elif val == "hatch":
            return Fill(type=FillType.hatch)
        elif val == "reverse_hatch":
            return Fill(type=FillType.reverse_hatch)
        elif val == "cross_hatch":
            return Fill(type=FillType.cross_hatch)

    fill_type = FillType.none
    color = None

    for child in ch:
        if child.node_type != SExprNodeType.LIST:
            continue
        name = sym_name(child)
        if name == "type":
            type_val = str(children(child)[0].value)
            try:
                fill_type = FillType(type_val)
            except ValueError as err:
                raise KicadParseError(f"Unknown fill type: {type_val!r}") from err
        elif name == "color":
            color = load_color(child)
        else:
            raise KicadParseError(f"Unknown token in fill: {name!r}")

    return Fill(type=fill_type, color=color)


# ---------------------------------------------------------------------------
# Text effects
# ---------------------------------------------------------------------------


def load_effects(node: SExprNode) -> TextAttributes:
    """Parse an ``effects`` block into a ``TextAttributes`` object."""
    require_sym(node, "effects")

    font_face = None
    font_size = (1.27, 1.27)
    thickness = None
    bold = False
    italic = False
    line_spacing = None
    font_color = None
    justify_h = JustifyH.center
    justify_v = JustifyV.center
    mirror = False
    visible = True
    href = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        name = sym_name(child)

        if name == "font":
            for fc in children(child):
                if fc.node_type != SExprNodeType.LIST:
                    if fc.node_type == SExprNodeType.SYMBOL:
                        if fc.value == "bold":
                            bold = True
                        elif fc.value == "italic":
                            italic = True
                    continue
                fname = sym_name(fc)
                if fname == "face":
                    font_face = get_str(children(fc)[0])
                elif fname == "size":
                    fch = children(fc)
                    font_size = (get_float(fch[0]), get_float(fch[1]))
                elif fname == "thickness":
                    thickness = get_float(children(fc)[0])
                elif fname == "bold":
                    bold = bool_token(fc)
                elif fname == "italic":
                    italic = bool_token(fc)
                elif fname == "color":
                    font_color = load_color(fc)
                elif fname == "line_spacing":
                    line_spacing = get_float(children(fc)[0])
                else:
                    raise KicadParseError(f"Unknown token in font: {fname!r}")

        elif name == "justify":
            jch = children(child)
            for jnode in jch:
                jval = str(jnode.value)
                if jval == "left":
                    justify_h = JustifyH.left
                elif jval == "right":
                    justify_h = JustifyH.right
                elif jval == "top":
                    justify_v = JustifyV.top
                elif jval == "bottom":
                    justify_v = JustifyV.bottom
                elif jval == "mirror":
                    mirror = True

        elif name == "hide":
            visible = not bool_token(child)
        elif name == "href":
            href = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in effects: {name!r}")

    return TextAttributes(
        font_face=font_face,
        font_size=font_size,
        font_thickness=thickness,
        bold=bold,
        italic=italic,
        line_spacing=line_spacing,
        color=font_color,
        justify_h=justify_h,
        justify_v=justify_v,
        mirror=mirror,
        visible=visible,
        href=href,
    )


# ---------------------------------------------------------------------------
# Embedded files
# ---------------------------------------------------------------------------


def load_embedded_file(node: SExprNode) -> EmbeddedFile:
    """Parse a single ``(file ...)`` block inside ``(embedded_files ...)``."""
    require_sym(node, "file")
    name = ""
    file_type = ""
    data = ""
    checksum = ""

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "name":
            name = get_str(children(child)[0])
        elif cname == "type":
            file_type = get_str(children(child)[0])
        elif cname == "data":
            # After T1, the pipe-delimited |...| block is parsed as a single STRING node.
            data_children = children(child)
            if data_children and data_children[0].node_type == SExprNodeType.STRING:
                data = get_str(data_children[0])
        elif cname == "checksum":
            checksum = get_str(children(child)[0])

    return EmbeddedFile(name=name, file_type=file_type, data=data, checksum=checksum)


def load_embedded_files(node: SExprNode) -> list[EmbeddedFile]:
    """Parse an ``(embedded_files ...)`` block."""
    require_sym(node, "embedded_files")
    files = []
    for child in children(node):
        if child.node_type == SExprNodeType.LIST and sym_name(child) == "file":
            files.append(load_embedded_file(child))
    return files


# ---------------------------------------------------------------------------
# Page setup (paper / title_block) — cross-application common code
# ---------------------------------------------------------------------------


def load_paper(node: SExprNode) -> Paper:
    """Parse ``(paper "<Type>" [w h] [portrait])`` (page_info.cpp:238-256)."""
    ch = children(node)
    if not ch:
        raise KicadParseError("paper missing type")
    paper_type = get_str(ch[0])
    width = None
    height = None
    portrait = False
    rest = ch[1:]
    idx = 0
    # Optional custom width/height (two numbers) for User type.
    if idx + 1 < len(rest) and rest[idx].node_type in (
        SExprNodeType.DOUBLE,
        SExprNodeType.INTEGER,
    ):
        width = get_float(rest[idx])
        height = get_float(rest[idx + 1])
        idx += 2
    if idx < len(rest) and get_str(rest[idx]) == "portrait":
        portrait = True
        idx += 1
    return Paper(paper_type=paper_type, width=width, height=height, portrait=portrait)


def load_title_block(node: SExprNode) -> TitleBlock:
    """Parse ``(title_block …)`` (title_block.cpp:31-72)."""
    title = ""
    date = ""
    rev = ""
    company = ""
    comments: dict[int, str] = {}
    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        cc = children(child)
        if cname == "title":
            title = get_str(cc[0])
        elif cname == "date":
            date = get_str(cc[0])
        elif cname == "rev":
            rev = get_str(cc[0])
        elif cname == "company":
            company = get_str(cc[0])
        elif cname == "comment":
            comments[get_int(cc[0])] = get_str(cc[1])
        else:
            raise KicadParseError(f"Unknown token in title_block: {cname!r}")
    return TitleBlock(title=title, date=date, rev=rev, company=company, comments=comments)
