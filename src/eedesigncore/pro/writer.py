"""JSON writer that reproduces KiCad's nlohmann serialization byte for byte.

KiCad dumps a project file with ``buffer << std::setw( 2 ) << toSave <<
std::endl`` (``common/settings/json_settings.cpp:515-516``), which reaches
``nlohmann::detail::serializer::dump`` with ``pretty_print = true``,
``indent_step = 2``, and ``ensure_ascii = false``. This module ports that
serializer. The audit ``context/audit/json-save-path-2026-07-28.md`` sections
3.4, 4, and 9.1 hold the citations for every rule below.

Layout rules:

- Object keys sort by UTF-8 byte order, because nlohmann stores an object in a
  ``std::map<std::string, ...>`` (audit section 3.1). Python string order is
  code-point order, which UTF-8 preserves, so the two orders agree.
- An array keeps its stored order, EXCEPT the three whose KiCad parameter is
  backed by a sorted container (:data:`_SORTED_ARRAY_PATHS`, ADR-065).
- Two spaces per depth level, ``": "`` between a key and its value, and ``,\\n``
  between members.
- An empty object and an empty array stay inline as ``{}`` and ``[]``.
- Exactly one trailing line feed ends the document.
- A non-finite float becomes ``null``.

The stdlib ``json`` module cannot do this job. With ``indent`` set, CPython uses
the pure-Python encoder, whose float branch calls ``float.__repr__`` directly
and so ignores the literal a :class:`~eedesigncore.pro.tree.JsonFloat` carries
(audit section 9.3).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from decimal import Decimal

from eedesigncore.pro.tree import JsonFloat, JsonInt, JsonValue

_INDENT_STEP = 2

# The fixed-notation window of nlohmann's format_buffer: kMinExp = -4 and
# kMaxExp = numeric_limits<double>::digits10 = 15 (json.hpp:18013-18015).
_MIN_EXP = -4
_MAX_EXP = 15

# ---------------------------------------------------------------------------
# The three arrays KiCad's settings save path emits SORTED
#
# Object keys sort by themselves, because nlohmann stores an object in a
# std::map, so the JSON pipeline is mutation-safe for every object. An ARRAY is
# not, and it sorts only when the parameter behind it is backed by a sorted
# container. The sweep report section 5.2 enumerates every such parameter in the
# KiCad 10 tree, and there are exactly three (ADR-065).
#
# A pattern element of "*" matches any object key. "[]" is the path element of
# an array item, so an array nested inside an array cannot match by accident.
# ---------------------------------------------------------------------------

# `NETCLASS::SetName` marks the class named exactly "Default" as the default one
# (`include/netclass.h:92-100`), and `NET_SETTINGS` emits it first.
_DEFAULT_NETCLASS_NAME = "Default"


def _string_array_key(item: JsonValue) -> tuple[int, str]:
    """Sort key for an array of strings that KiCad holds in a ``std::set``.

    A non-string item cannot appear in a KiCad-written file. It sorts last and,
    because the sort is stable, keeps its stored position among its own kind.
    """
    return (0, item) if isinstance(item, str) else (1, "")


def _netclass_key(item: JsonValue) -> tuple[int, str]:
    """Sort key for one ``net_settings.classes`` entry.

    ``NET_SETTINGS`` emits the default netclass first and then walks
    ``std::map<wxString, …>`` by name (``common/project/net_settings.cpp:189-201``,
    ``include/project/net_settings.h:231``). An entry KiCad would drop on load,
    meaning one that is not an object or carries no string ``name``
    (``net_settings.cpp:211-212``), sorts last and is never dropped here.
    """
    if not isinstance(item, dict):
        return (2, "")
    name = item.get("name")
    if not isinstance(name, str):
        return (2, "")
    return (0 if name == _DEFAULT_NETCLASS_NAME else 1, name)


_SORTED_ARRAY_PATHS: tuple[tuple[tuple[str, ...], Callable[[JsonValue], tuple[int, str]]], ...] = (
    # .kicad_prl: PARAM_SET<wxString>, so a std::set
    # (project_local_settings.cpp:193, parameters.h:627-633).
    (("board", "hidden_netclasses"), _string_array_key),
    # .kicad_pro: the default netclass, then a name-keyed std::map.
    (("net_settings", "classes"), _netclass_key),
    # .kicad_pro: each inner value is a std::set<wxString>
    # (net_settings.cpp:252-271, net_settings.h:234).
    (("net_settings", "netclass_assignments", "*"), _string_array_key),
)


def _array_sort_key(path: Sequence[str]) -> Callable[[JsonValue], tuple[int, str]] | None:
    """Return the sort key for the array at *path*, or None to keep stored order."""
    for pattern, key in _SORTED_ARRAY_PATHS:
        if len(pattern) != len(path):
            continue
        if all(want in ("*", have) for want, have in zip(pattern, path, strict=True)):
            return key
    return None


_SHORT_ESCAPES = {
    "\x08": "\\b",
    "\x09": "\\t",
    "\x0a": "\\n",
    "\x0c": "\\f",
    "\x0d": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}
_NEEDS_ESCAPE = re.compile(r'["\\\x00-\x1f]')


def _escape_string(value: str) -> str:
    """Quote and escape *value* exactly as ``dump_escaped`` does.

    With ``ensure_ascii = false`` nlohmann escapes seven characters by name,
    escapes any other C0 control character as lowercase ``\\u00xx``, and copies
    everything else, including ``0x7f`` and all non-ASCII, as raw UTF-8
    (``json.hpp:18399-18512``).
    """
    if not _NEEDS_ESCAPE.search(value):
        return f'"{value}"'
    out = ['"']
    for char in value:
        short = _SHORT_ESCAPES.get(char)
        if short is not None:
            out.append(short)
        elif char < "\x20":
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def format_exponent(exponent: int) -> str:
    """Render a decimal exponent as ``append_exponent`` does.

    The sign is always present, and the digit count is at least two
    (``json.hpp:17835-17874``).
    """
    sign = "-" if exponent < 0 else "+"
    return f"{sign}{abs(exponent):02d}"


def format_float(value: float) -> str:
    """Render *value* as ``dump_float`` and ``to_chars`` do.

    A non-finite value becomes ``null`` (``json.hpp:18810-18814``). Every finite
    value carries either a decimal point or an exponent, so an integral double
    reads ``50.0`` and never ``50``.

    The shortest digit string comes from CPython ``repr``, which is correctly
    rounded. nlohmann uses Grisu2, which its own comment describes as not
    provably shortest (``json.hpp:17777-17779``). The two agree on the
    overwhelming majority of doubles. Preserving the source literal, which
    :class:`~eedesigncore.pro.tree.JsonFloat` does, removes even that risk for
    a value the caller never changed.
    """
    if math.isnan(value) or math.isinf(value):
        return "null"
    sign = "-" if math.copysign(1.0, value) < 0 else ""
    magnitude = abs(value)
    if magnitude == 0.0:
        return f"{sign}0.0"

    parts = Decimal(repr(magnitude)).normalize().as_tuple()
    digits = "".join(str(digit) for digit in parts.digits)
    exponent = parts.exponent
    assert isinstance(exponent, int)
    count = len(digits)
    point = count + exponent

    if count <= point <= _MAX_EXP:
        return f"{sign}{digits}{'0' * (point - count)}.0"
    if 0 < point <= _MAX_EXP:
        return f"{sign}{digits[:point]}.{digits[point:]}"
    if _MIN_EXP < point <= 0:
        return f"{sign}0.{'0' * -point}{digits}"
    mantissa = digits if count == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{sign}{mantissa}e{format_exponent(point - 1)}"


def _format_number(value: int | float, canonical_numbers: bool) -> str:
    """Render one number, preferring its source literal."""
    if not canonical_numbers and isinstance(value, JsonInt | JsonFloat):
        return value.literal
    if isinstance(value, int):
        return str(int(value))
    return format_float(float(value))


def _emit(
    value: JsonValue,
    depth: int,
    out: list[str],
    canonical_numbers: bool,
    path: tuple[str, ...] = (),
) -> None:
    """Append the serialization of *value* to *out*.

    *path* is the object-key path of *value*, which selects the three arrays
    KiCad emits sorted. Every other array keeps its stored order.
    """
    if value is None:
        out.append("null")
        return
    if isinstance(value, bool):
        out.append("true" if value else "false")
        return
    if isinstance(value, int | float):
        out.append(_format_number(value, canonical_numbers))
        return
    if isinstance(value, str):
        out.append(_escape_string(value))
        return

    child_indent = " " * (_INDENT_STEP * (depth + 1))
    close_indent = " " * (_INDENT_STEP * depth)

    if isinstance(value, list):
        if not value:
            out.append("[]")
            return
        sort_key = _array_sort_key(path)
        items = sorted(value, key=sort_key) if sort_key is not None else value
        out.append("[\n")
        for index, item in enumerate(items):
            if index:
                out.append(",\n")
            out.append(child_indent)
            _emit(item, depth + 1, out, canonical_numbers, (*path, "[]"))
        out.append(f"\n{close_indent}]")
        return

    if not value:
        out.append("{}")
        return
    out.append("{\n")
    for index, key in enumerate(sorted(value)):
        if index:
            out.append(",\n")
        out.append(child_indent)
        out.append(_escape_string(key))
        out.append(": ")
        _emit(value[key], depth + 1, out, canonical_numbers, (*path, key))
    out.append(f"\n{close_indent}}}")


def dump_json(value: JsonValue, *, canonical_numbers: bool = False) -> str:
    """Serialise *value* the way KiCad serialises a project file.

    Object keys always emit sorted, and the three arrays of
    :data:`_SORTED_ARRAY_PATHS` emit sorted too. Every other array keeps its
    stored order, because KiCad emits those from a ``std::vector``.

    Args:
        value: The tree to write.
        canonical_numbers: When True, ignore every stored number literal and
            render each number from its numeric value. The corpus harness uses
            this to test whether a file's own text matches the layout KiCad
            would produce.

    Returns:
        The document text, ending in exactly one line feed.
    """
    out: list[str] = []
    _emit(value, 0, out, canonical_numbers)
    out.append("\n")
    return "".join(out)
