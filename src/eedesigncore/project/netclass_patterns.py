"""KiCad net class matching, from common/eda_pattern_match.cpp:109-244,416-423."""

from __future__ import annotations

from contextlib import suppress
from typing import Protocol, cast

import pcre2  # type: ignore[import-untyped]

from eedesigncore.exceptions import KicadParseError

_PCRE2_ALT_BSUX = 0x00000002
_LIMITS = "(*LIMIT_MATCH=100000)(*LIMIT_DEPTH=1000)"
_ESCAPED_WILDCARD_CHARACTERS = frozenset(".*+?^${}()|[]/\\")


class _Match(Protocol):
    def start(self) -> int: ...


class _Pattern(Protocol):
    def search(self, subject: str) -> _Match | None: ...


def _compile(expression: str) -> _Pattern:
    # wxWidgets uses UTF, DOTALL, ALT_BSUX, and no Unicode character properties.
    flags = int(pcre2.UNICODE | pcre2.DOTALL | pcre2.ASCII) | _PCRE2_ALT_BSUX
    return cast(_Pattern, pcre2.compile(_LIMITS + expression, flags=flags, jit=False))


class NetclassPattern:
    """Test both anchored pattern forms, including KiCad's literal fallback."""

    def __init__(self, pattern: str) -> None:
        if "\0" in pattern:
            raise KicadParseError("A net class pattern contains a null character")
        expression = pattern if pattern.startswith("^") else "^" + pattern
        expression = expression if expression.endswith("$") else expression + "$"
        self._patterns: list[_Pattern] = []
        with suppress(pcre2.PatternError):
            self._patterns.append(_compile(expression))
        wildcard = "^"
        for char in pattern:
            if char == "*":
                wildcard += ".*"
            elif char == "?":
                wildcard += "."
            elif char in _ESCAPED_WILDCARD_CHARACTERS:
                wildcard += "\\" + char
            else:
                wildcard += char
        try:
            self._patterns.append(_compile(wildcard + "$"))
        except (pcre2.PatternError, pcre2.LibraryError) as exc:
            raise KicadParseError("Cannot compile the net class wildcard pattern") from exc

    def matches(self, net_name: str) -> bool:
        """Return a match at position zero, as KiCad StartsWith does."""
        try:
            for pattern in self._patterns:
                match = pattern.search(net_name)
                if match is not None and match.start() == 0:
                    return True
        except pcre2.LibraryError as exc:
            raise KicadParseError("Net class pattern matching exceeded an engine limit") from exc
        return False
