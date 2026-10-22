"""KiCad's string escape module, read side.

A faithful port of ``UnescapeString`` (``common/string_utils.cpp:313-394`` in the
KiCad 10.0.3 clone). It resolves the ``{token}`` runs KiCad resolves, and it
leaves the ones KiCad leaves.

This is a SEMANTIC helper, not a round-trip helper. KiCad's escape pair is lossy
in both directions, so the result is not guaranteed to escape back to the source
bytes. No loader and no writer in this library calls it. Use it when you need the
value KiCad holds in memory, for example to reproduce KiCad's footprint-filter
matching (``erc.cpp:1881-1889``).

Cost. One left-to-right pass over the value, so time is LINEAR in the length and
peak live memory is LINEAR in the length. Nesting depth does not raise either
one. KiCad itself is quadratic in both on the same input, because it recurses on
the token interior and copies that interior at every level. The output of the two
agrees character for character (ADR-084).

Source basis: ``context/audit/escape-string-module-2026-08-18.md`` sections 4.3,
5 and 8.4. Every line citation below is into that clone.
"""

from __future__ import annotations

import re

# The 14-entry table at ``common/string_utils.cpp:368-381``. Three entries have
# no producer anywhere in KiCad 10.0.3 and are read-only legacy: ``quote``,
# ``dollar`` and ``brace``. ``{return}`` produces a line feed, not a carriage
# return, so a lone carriage return does not survive KiCad's own pair.
_TOKENS = {
    "dblquote": '"',
    "quote": "'",
    "lt": "<",
    "gt": ">",
    "backslash": "\\",
    "slash": "/",
    "bar": "|",
    "comma": ",",
    "colon": ":",
    "space": " ",
    "dollar": "$",
    "tab": "\t",
    "return": "\n",
    "brace": "{",
}

# The markup guard at ``:364-367``: overbar, subscript, superscript, and
# variable reference. A brace group after one of these is preserved verbatim.
_MARKUP_PREFIXES = frozenset("$~^_")

# The length shortcut at ``:317-321``. The shortest escape is three characters.
_SHORTCUT_LENGTH = 2

# No table key holds a brace, so an interior longer than the longest key cannot
# resolve. The bound keeps the walk from ever copying a long interior.
_LONGEST_TOKEN = max(len(name) for name in _TOKENS)

# The only characters the walk stops on. Every other character is copied in a run.
_BRACES = re.compile("[{}]")


def unescape_string(value: str) -> str:
    """Resolve KiCad's ``{token}`` escapes, exactly as KiCad 10.0.3 resolves them.

    Evaluation order, per ``common/string_utils.cpp:313-394``:

    1. A value of two characters or fewer returns unchanged (``:317-321``).
    2. On ``{``, an inner scan collects the token interior with a depth counter
       (``:334-357``).
    3. An unterminated group emits ``{`` plus the unescaped interior, and the
       brace survives (``:360-363``).
    4. A group whose preceding character is ``$``, ``~``, ``^`` or ``_`` is
       preserved whole, interior unescaped (``:364-367``). This markup guard is
       why KiCad reads ``A_{space}B`` as ``A_{space}B``.
    5. A known token becomes its character (``:368-381``).
    6. Any other token is preserved whole, interior unescaped (``:382-385``).

    KiCad reaches that result by recursing on the token interior. This port
    reaches the same result in ONE left-to-right pass, with no recursion and no
    stack of levels, so a deeply nested value raises no ``RecursionError`` and
    costs linear time and linear memory. The equivalence rests on three facts
    proved in ADR-084: every brace KiCad does not resolve is copied through
    unchanged, the first ``}`` after an inner ``{`` always closes that inner
    ``{``, and the interior recursion never changes a resolution decision.

    This function is NOT byte-preserving. Use
    :func:`eedesigncore.split_fp_filters` for the round trip.
    """
    if len(value) <= _SHORTCUT_LENGTH:
        return value

    out: list[str] = []
    copied = 0
    braces = _BRACES.finditer(value)
    brace = next(braces, None)

    while brace is not None:
        opening = brace.start()
        if value[opening] == "}":
            brace = next(braces, None)
            continue

        following = next(braces, None)
        # A token interior holds no brace, so a group resolves only when the very
        # next brace is the closing one. Anything else keeps its ``{`` verbatim.
        if following is not None and value[following.start()] == "}":
            closing = following.start()
            guarded = opening > 0 and value[opening - 1] in _MARKUP_PREFIXES
            if not guarded and closing - opening - 1 <= _LONGEST_TOKEN:
                produced = _TOKENS.get(value[opening + 1 : closing])
                if produced is not None:
                    out.append(value[copied:opening])
                    out.append(produced)
                    copied = closing + 1
                    brace = next(braces, None)
                    continue

        brace = following

    out.append(value[copied:])
    return "".join(out)
