"""Shared writer helper functions reused across format sub-packages."""

from __future__ import annotations

from eedesigncore.common.models import (
    Color,
    EmbeddedFile,
    Fill,
    FillType,
    Stroke,
    TextAttributes,
)
from eedesigncore.common.page import Paper, TitleBlock

# KiCad's MIME_BASE64_LENGTH constant from embedded_files.cpp line 185.
MIME_BASE64_LENGTH = 76


# ---------------------------------------------------------------------------
# Float / string formatting helpers
# ---------------------------------------------------------------------------


def fmt_iu(value: float) -> str:
    """Format an internal-unit value (coords, sizes, widths, lengths).

    Mirrors KiCad ``EDA_UNIT_UTILS::FormatInternalUnits`` (``eda_units.cpp:194-225``):
    for ``0 < |v| <= 0.0001`` use ``{:.10f}`` then strip trailing zeros and the
    decimal point; otherwise ``{:.10g}``. NOTE: FormatInternalUnits DOES have the
    small-value fixed branch (contra arch-plan section 3); it differs from
    ``fmt_double`` only in fractional precision (10 vs 16).
    """
    if value != 0.0 and abs(value) <= 0.0001:
        buf = f"{value:.10f}"
        buf = buf.rstrip("0")
        if buf.endswith("."):
            buf = buf[:-1]
        return buf
    return f"{value:.10g}"


def fmt_double(value: float) -> str:
    """Format a bare double (ratios, model xyz, alpha) per ``FormatDouble2Str``.

    Mirrors KiCad ``FormatDouble2Str`` (``string_utils.cpp:1446-1473``): for
    ``0 < |v| <= 0.0001`` use ``{:.16f}`` then strip trailing zeros and the
    decimal point; otherwise ``{:.10g}``.
    """
    if value != 0.0 and abs(value) <= 0.0001:
        buf = f"{value:.16f}"
        buf = buf.rstrip("0")
        if buf.endswith("."):
            buf = buf[:-1]
        return buf
    return f"{value:.10g}"


def fmt_angle(value: float) -> str:
    """Format an angle using pure ``{:.10g}``.

    Mirrors KiCad ``EDA_UNIT_UTILS::FormatAngle`` (``eda_units.cpp:186-191``):
    pure ``{:.10g}`` with NO small-value fixed branch (unlike fmt_iu/fmt_double).
    """
    return f"{value:.10g}"


def esc(text: str) -> str:
    """Escape special characters for a quoted string token.

    Matches KiCad richio.cpp::Quotes(): escapes \\, ", \\n, \\r (not \\t).
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def q(text: str) -> str:
    """Wrap text in double quotes with escaping."""
    return f'"{esc(text)}"'


def dump_bool(value: bool) -> str:
    return "yes" if value else "no"


# ---------------------------------------------------------------------------
# Color, Stroke, Fill
# ---------------------------------------------------------------------------


def dump_color(color: Color) -> str:
    # alpha via FormatDouble2Str (stroke_params.cpp:319, eda_text.cpp:1095,
    # sch_io_kicad_sexpr_common.cpp:59 all use FormatDouble2Str for .a)
    return f"(color {color.r} {color.g} {color.b} {fmt_double(color.a)})"


def dump_stroke(stroke: Stroke) -> str:
    # width via FormatInternalUnits (stroke_params.cpp:314)
    parts = [f"(width {fmt_iu(stroke.width)})", f"(type {stroke.type.value})"]
    if stroke.color is not None:
        parts.append(dump_color(stroke.color))
    return "(stroke " + " ".join(parts) + ")"


def dump_fill_pcb(fill: Fill) -> str:
    """Emit the PCB fp_rect/fp_circle/fp_poly fill token.

    KiCad emits:
    - FILLED_SHAPE: ``(fill yes)``
    - HATCH:        ``(fill hatch)``
    - REVERSE_HATCH: ``(fill reverse_hatch)``
    - CROSS_HATCH:  ``(fill cross_hatch)``
    - default:       ``(fill no)``

    This is ALWAYS written for rect/circle/poly.
    """
    if fill.type == FillType.hatch:
        return "(fill hatch)"
    elif fill.type == FillType.reverse_hatch:
        return "(fill reverse_hatch)"
    elif fill.type == FillType.cross_hatch:
        return "(fill cross_hatch)"
    elif fill.type in (FillType.outline, FillType.background, FillType.color):
        # In PCB context "outline" means filled (FILLED_SHAPE)
        return "(fill yes)"
    else:
        return "(fill no)"


def dump_fill_sch(fill: Fill) -> str:
    """Emit the SCH shape fill token ``(fill (type ...) [(color ...)])``."""
    if fill.type in (FillType.color, FillType.hatch, FillType.reverse_hatch, FillType.cross_hatch):
        color_part = ""
        if fill.color is not None:
            color_part = f" {dump_color(fill.color)}"
        return f"(fill (type {fill.type.value}){color_part})"
    return f"(fill (type {fill.type.value}))"


# ---------------------------------------------------------------------------
# Text effects
# ---------------------------------------------------------------------------


def dump_effects(attrs: TextAttributes, *, hide: bool = False) -> str:
    """Emit an ``effects`` block from ``TextAttributes``.

    Mirrors ``EDA_TEXT::Format`` (eda_text.cpp:1060-1119): emits font, optional
    justify, optional href only. It NEVER emits ``hide`` inside ``(effects ...)``
    -- KiCad 10 emits visibility at item level (PCB_FIELD / saveField / savePin),
    never in the effects body. See context/audit/verify-dump-effects-hide-2026-07-13.md.

    *hide* is the one exception, for the KiCad 9 schematic field layout:
    ``SCH_FIELD::Format`` (9.0.9 ``eeschema/sch_field.cpp:517-586``) emits
    ``(hide yes)`` between ``(justify …)`` and ``(href …)``. Only the legacy
    field writers pass it.
    """
    h, w = attrs.font_size
    font_parts = []
    if attrs.font_face is not None:
        font_parts.append(f"(face {q(attrs.font_face)})")
    # size, thickness via FormatInternalUnits; line_spacing via FormatDouble2Str
    # (eda_text.cpp:1071-1082)
    font_parts.append(f"(size {fmt_iu(h)} {fmt_iu(w)})")

    if attrs.font_thickness is not None:
        font_parts.append(f"(thickness {fmt_iu(attrs.font_thickness)})")
    if attrs.bold:
        font_parts.append("(bold yes)")
    if attrs.italic:
        font_parts.append("(italic yes)")
    if attrs.color is not None:
        font_parts.append(dump_color(attrs.color))
    if attrs.line_spacing is not None:
        font_parts.append(f"(line_spacing {fmt_double(attrs.line_spacing)})")

    parts = ["(font " + " ".join(font_parts) + ")"]

    justify_tokens = []
    if attrs.justify_h.value != "center":
        justify_tokens.append(attrs.justify_h.value)
    if attrs.justify_v.value != "center":
        justify_tokens.append(attrs.justify_v.value)
    if attrs.mirror:
        justify_tokens.append("mirror")
    if justify_tokens:
        parts.append("(justify " + " ".join(justify_tokens) + ")")

    if hide:
        parts.append("(hide yes)")

    # (href …) hyperlink, emitted after justify (eda_text.cpp:1116-1117)
    if attrs.href is not None:
        parts.append(f"(href {q(attrs.href)})")

    return "(effects " + " ".join(parts) + ")"


def dump_field_effects(attrs: TextAttributes | None, *, hide: bool = False) -> str:
    """Emit the ``effects`` block of a property, defaulting the way KiCad does.

    ``EDA_TEXT::Format`` has no early exit (``common/eda_text.cpp:1061-1119``), so
    every field carries an ``(effects …)`` node whatever its attributes hold. Both
    save paths call it unconditionally: the symbol side at
    ``sch_io_kicad_sexpr_lib_cache.cpp:682`` and the PCB side at
    ``pcb_io_kicad_sexpr.cpp:2309``.

    A property built without attributes therefore writes the KiCad default of a
    fresh field, which is a font of ``DEFAULT_SIZE_TEXT`` mils square
    (``include/eda_text.h:81``, ``common/eda_text.cpp:105-106``). That is 1.27 mm
    on both internal-unit scales (ADR-068).

    *hide* puts ``(hide yes)`` inside the block, the KiCad 9 field layout.
    """
    return dump_effects(attrs if attrs is not None else TextAttributes(), hide=hide)


# ---------------------------------------------------------------------------
# Page setup (paper / title_block)
# ---------------------------------------------------------------------------
#
# Both blocks are cross-application KiCad common code (``PAGE_INFO::Format`` /
# ``TITLE_BLOCK::Format``) emitted identically by the board and schematic
# writers, so the emitters live here and are shared by pcb/ and sch/.


def dump_paper(p: Paper) -> str:
    parts = [f"(paper {q(p.paper_type)}"]
    if p.width is not None and p.height is not None:
        parts.append(f"{fmt_double(p.width)} {fmt_double(p.height)}")
    if p.portrait:
        parts.append("portrait")
    return " ".join(parts) + ")"


def title_block_has_content(tb: TitleBlock) -> bool:
    return bool(tb.title or tb.date or tb.rev or tb.company or tb.comments)


def dump_title_block(tb: TitleBlock) -> str:
    parts = ["(title_block"]
    if tb.title:
        parts.append(f"(title {q(tb.title)})")
    if tb.date:
        parts.append(f"(date {q(tb.date)})")
    if tb.rev:
        parts.append(f"(rev {q(tb.rev)})")
    if tb.company:
        parts.append(f"(company {q(tb.company)})")
    for idx in sorted(tb.comments):
        parts.append(f"(comment {idx} {q(tb.comments[idx])})")
    parts.append(")")
    return " ".join(parts)


def _chunk_base64(data: str) -> str:
    """Split a base64 string into 76-char chunks and wrap in pipe delimiters.

    Produces ``|chunk1\\nchunk2\\n...lastchunk|`` matching KiCad's
    ``embedded_files.cpp`` format.  The surrounding ``(data ...)`` list is
    added by the caller; Prettify converts the embedded newlines into
    tab-indented continuation lines.
    """
    chunks = [data[i : i + MIME_BASE64_LENGTH] for i in range(0, len(data), MIME_BASE64_LENGTH)]
    return "|" + "\n".join(chunks) + "|"


def dump_embedded_files(files: list[EmbeddedFile], *, write_data: bool = True) -> str:
    """Emit a flat ``(embedded_files ...)`` block.

    The returned string is a flat (unindented) S-expression fragment that
    Prettify will convert to the canonical indented form matching KiCad's
    output from ``embedded_files.cpp::WriteEmbeddedFiles()``.

    When *write_data* is ``False`` the ``(data ...)`` block is omitted entirely
    (name/type/checksum stub only), matching ``WriteEmbeddedFiles(aWriteData)``
    with ``aWriteData=false`` (``embedded_files.cpp:214``) — the deferred form a
    board-embedded footprint writes while the board root owns the bytes.
    """
    if not files:
        return ""

    parts = ["(embedded_files"]
    for f in files:
        file_parts = [
            "(file",
            f"(name {q(f.name)})",
            f"(type {f.file_type})",
        ]
        if write_data:
            if f.data:
                # Emit (data\n|chunk1\nchunk2\n...\nlastchunk|\n) so Prettify
                # inserts a space before | and indents continuation lines with
                # tabs.
                pipe_block = _chunk_base64(f.data)
                file_parts.append(f"(data\n{pipe_block}\n)")
            else:
                file_parts.append("(data)")
        file_parts.append(f"(checksum {q(f.checksum)})")
        file_parts.append(")")
        parts.append(" ".join(file_parts))
    parts.append(")")
    return " ".join(parts)
