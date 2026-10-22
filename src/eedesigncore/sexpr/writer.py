"""KiCad S-expression writer and Prettify post-pass.

Two public functions:

* ``emit_flat(node)`` — serialises an ``SExprNode`` tree to a flat,
  unindented S-expression string.
* ``prettify(flat, mode)`` — ports KiCad's ``KICAD_FORMAT::Prettify``
  (``kicad_io_utils.cpp``) to produce canonical indented output.
"""

from __future__ import annotations

from enum import Enum

from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType

# Short-form tokens: in COMPACT_TEXT_PROPERTIES mode these are kept on one line
# even if they contain sub-lists (port of kicad_io_utils.cpp isShortForm()).
_SHORT_FORM_TOKENS: frozenset[str] = frozenset(
    {"font", "stroke", "fill", "teardrop", "offset", "rotate", "scale"}
)

# Column limit for the (xy ...) special case
_XY_COLUMN_LIMIT = 99

# Characters that end a run of ordinary text inside :func:`prettify`.
#
# Outside a quote, whitespace and either parenthesis reach their own branch, and
# a backslash or a quote decides quote state. Inside a quote, whitespace and
# parentheses are ordinary text, so only the two state-deciding characters stop
# a run. Keeping the backslash and the quote OUT of every run is what makes the
# escaped-quote case safe.
_RUN_STOP = frozenset(' \t\n\r()"\\')
_RUN_STOP_IN_QUOTE = frozenset('"\\')

# Column threshold after which long token sequences wrap
_CONSECUTIVE_WRAP_THRESHOLD = 72


class FormatMode(Enum):
    """Prettify formatting modes, matching KiCad FORMAT_MODE enum."""

    NORMAL = "NORMAL"
    COMPACT_TEXT_PROPERTIES = "COMPACT_TEXT_PROPERTIES"
    LIBRARY_TABLE = "LIBRARY_TABLE"


def _format_double(value: float, raw: str | None) -> str:
    """Format a float for output.

    Uses ``raw`` when available (lossless round-trip).
    Otherwise formats with up to 4 decimal places, stripping trailing zeros
    but keeping at least one decimal place (e.g. ``1.0`` not ``1``).
    """
    if raw is not None:
        return raw
    # Format with 4 decimal places then strip trailing zeros
    formatted = f"{value:.4f}"
    # Remove trailing zeros after decimal point, keep at least one decimal
    formatted = formatted.rstrip("0")
    if formatted.endswith("."):
        formatted += "0"
    return formatted


def _escape_string(text: str) -> str:
    """Escape a string for S-expression output.

    Matches KiCad richio.cpp::Quotes() exactly: escapes \\, ", \\n, \\r.
    Tabs are NOT escaped (richio.cpp passes them through literally).
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def emit_flat(node: SExprNode) -> str:
    """Serialise an ``SExprNode`` tree to a flat, unindented S-expression string."""
    if node.node_type == SExprNodeType.LIST:
        assert isinstance(node.value, list)
        children = " ".join(emit_flat(c) for c in node.value)
        return f"({children})"
    elif node.node_type == SExprNodeType.SYMBOL:
        assert isinstance(node.value, str)
        return node.value
    elif node.node_type == SExprNodeType.STRING:
        assert isinstance(node.value, str)
        return f'"{_escape_string(node.value)}"'
    elif node.node_type == SExprNodeType.INTEGER:
        assert isinstance(node.value, int)
        return str(node.value)
    elif node.node_type == SExprNodeType.DOUBLE:
        assert isinstance(node.value, float)
        return _format_double(node.value, node.raw)
    else:
        raise ValueError(f"Unknown node type: {node.node_type}")  # pragma: no cover


def prettify(flat: str, mode: str = "NORMAL") -> str:
    """Port of KiCad's ``KICAD_FORMAT::Prettify`` from ``kicad_io_utils.cpp``.

    Transforms a flat S-expression string into KiCad's canonical indented format.

    Args:
        flat: Flat (unindented) S-expression string.
        mode: One of ``"NORMAL"``, ``"COMPACT"``, or ``"COMPACT_TEXT_PROPERTIES"``.
              ``"COMPACT"`` is an alias for ``"COMPACT_TEXT_PROPERTIES"``.

    Returns:
        Indented S-expression string ending with a single ``\\n``.
    """
    # Normalise mode string
    if mode in ("COMPACT", "COMPACT_TEXT_PROPERTIES"):
        fmt_mode = FormatMode.COMPACT_TEXT_PROPERTIES
    elif mode == "LIBRARY_TABLE":
        fmt_mode = FormatMode.LIBRARY_TABLE
    else:
        fmt_mode = FormatMode.NORMAL

    text_special_case = fmt_mode == FormatMode.COMPACT_TEXT_PROPERTIES
    lib_special_case = fmt_mode == FormatMode.LIBRARY_TABLE

    source = flat
    formatted: list[str] = []

    list_depth = 0
    lib_depth = 0
    last_non_whitespace: str = ""
    in_quote = False
    has_inserted_space = False
    in_multi_line_list = False
    in_xy = False
    in_short_form = False
    in_lib_row = False
    short_form_depth = 0
    column = 0
    backslash_count = 0

    # Bound to locals for the character loop. CPython resolves a local faster
    # than a global or a closure cell, and this loop runs once per character of
    # a whole board file.
    length = len(source)
    append = formatted.append

    def is_whitespace(c: str) -> bool:
        return c in " \t\n\r"

    def next_non_whitespace(start: int) -> str:
        """Return the next non-whitespace character at or after *start*."""
        i = start
        while i < length and is_whitespace(source[i]):
            i += 1
        if i >= length:
            return "\x00"
        return source[i]

    def check_is_xy(start: int) -> bool:
        """Return True if the token starting after '(' at *start* is 'xy '."""
        i = start + 1
        if i >= len(source) or source[i] != "x":
            return False
        i += 1
        if i >= len(source) or source[i] != "y":
            return False
        i += 1
        return not (i >= len(source) or source[i] != " ")

    def check_is_short_form(start: int) -> bool:
        """Return True if the token after '(' matches a short-form token name."""
        i = start + 1
        token = []
        while i < len(source) and source[i].isalpha():
            token.append(source[i])
            i += 1
        return "".join(token) in _SHORT_FORM_TOKENS

    def check_is_lib(start: int) -> bool:
        """Return True if the token after '(' is 'lib'."""
        i = start + 1
        token = []
        while i < len(source) and source[i].isalpha():
            token.append(source[i])
            i += 1
        return "".join(token) == "lib"

    def push(s: str) -> None:
        nonlocal column
        append(s)
        # Track column for last char
        if "\n" in s:
            col_part = s.rsplit("\n", 1)[1]
            column = len(col_part)
        else:
            column += len(s)

    i = 0
    while i < length:
        cursor = source[i]

        if is_whitespace(cursor) and not in_quote:
            # Only this branch reads the lookahead, so only this branch pays for
            # it. Computing it once per character cost more than the rest of the
            # loop body on a large board.
            nxt = next_non_whitespace(i)
            if (
                not has_inserted_space
                and list_depth > 0
                and last_non_whitespace != "("
                and nxt != ")"
                and nxt != "("
            ):
                if in_xy or column < _CONSECUTIVE_WRAP_THRESHOLD or in_short_form or in_lib_row:
                    push(" ")
                    has_inserted_space = True
                else:
                    indent = "\t" * list_depth
                    push(f"\n{indent}")
                    in_multi_line_list = True
                    has_inserted_space = True
        else:
            has_inserted_space = False

            if cursor == "(" and not in_quote:
                current_is_xy = check_is_xy(i)
                current_is_short_form = text_special_case and check_is_short_form(i)
                current_is_lib = lib_special_case and check_is_lib(i)

                if not formatted:
                    push("(")
                elif in_xy and current_is_xy and column < _XY_COLUMN_LIMIT:
                    # List-of-points special case: keep on same line
                    push(" (")
                elif in_short_form or in_lib_row:
                    push(" (")
                else:
                    indent = "\t" * list_depth
                    push(f"\n{indent}(")

                in_xy = current_is_xy

                if current_is_short_form:
                    in_short_form = True
                    short_form_depth = list_depth
                elif current_is_lib:
                    in_lib_row = True
                    lib_depth = list_depth

                list_depth += 1
                last_non_whitespace = cursor

            elif cursor == ")" and not in_quote:
                if list_depth > 0:
                    list_depth -= 1

                if in_short_form:
                    push(")")
                elif in_lib_row and list_depth == lib_depth:
                    push(")")
                    in_lib_row = False
                elif last_non_whitespace == ")" or in_multi_line_list:
                    indent = "\t" * list_depth
                    push(f"\n{indent})")
                    in_multi_line_list = False
                else:
                    push(")")

                if short_form_depth == list_depth:
                    in_short_form = False
                    short_form_depth = 0

                last_non_whitespace = cursor

            elif cursor == "\\" or cursor == '"':
                # A backslash or a quote decides quote state, so each one is
                # handled alone and the parity logic is untouched.
                if cursor == "\\":
                    backslash_count += 1
                else:
                    if (backslash_count & 1) == 0:
                        in_quote = not in_quote
                    backslash_count = 0

                push(cursor)
                last_non_whitespace = cursor

            else:
                # A RUN of ordinary characters, appended once instead of once
                # per character. This is the whole speed-up, and it is also
                # where an earlier attempt went wrong: it let a run swallow an
                # escaped quote, so the next pass met that quote with the
                # backslash count already reset and closed the string one quote
                # early. Two files out of 1074 showed it, both carrying \" in a
                # title. A run therefore stops at every backslash and every
                # quote, which the branch above then handles singly.
                #
                # Inside a quote, whitespace and parentheses are ordinary, which
                # is why the stop set differs.
                stop = _RUN_STOP_IN_QUOTE if in_quote else _RUN_STOP
                j = i + 1
                while j < length and source[j] not in stop:
                    j += 1
                run = source[i:j]

                backslash_count = 0
                push(run)
                last_non_whitespace = run[-1]
                i = j
                continue

        i += 1

    # POSIX newline at end of file
    result = "".join(formatted) + "\n"
    return result
