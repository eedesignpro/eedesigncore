"""Tests for the fp-lib-table / sym-lib-table loader and writer.

Golden fixtures under ``tests/fixtures/tables/`` are verbatim copies of the
KiCad 10.0 install templates:
  ``C:\\Program Files\\KiCad\\10.0\\share\\kicad\\template\\fp-lib-table``
  ``C:\\Program Files\\KiCad\\10.0\\share\\kicad\\template\\sym-lib-table``
Grammar authority: ``context/audit/verify-lib-table-grammar-2026-07-13.md`` (T3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.parser import parse
from eedesigncore.tables import (
    LibTable,
    LibTableRow,
    dump_lib_table,
    load_lib_table,
    write_lib_table,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "tables"


# ---------------------------------------------------------------------------
# Byte-identical round-trip against the real KiCad golden files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "kind", "root"),
    [
        ("fp-lib-table", "fp", "fp_lib_table"),
        ("sym-lib-table", "sym", "sym_lib_table"),
    ],
)
def test_golden_round_trip_byte_identical(
    tmp_path: Path, fixture: str, kind: str, root: str
) -> None:
    src = FIXTURES / fixture
    raw = src.read_bytes()
    table = LibTable.from_file(src)
    assert table.kind == kind
    assert table.root_token == root
    assert table.version == 7
    assert len(table.rows) > 0

    out = tmp_path / fixture
    table.to_file(out)
    assert out.read_bytes() == raw


# ---------------------------------------------------------------------------
# Writer shape (T3 §3): tab indent, single-line rows, trailing newline
# ---------------------------------------------------------------------------


def test_writer_shape() -> None:
    table = LibTable(
        kind="fp",
        rows=[
            LibTableRow(
                name="Audio_Module",
                type="KiCad",
                uri="${KICAD10_FOOTPRINT_DIR}/Audio_Module.pretty",
                options="",
                descr="Audio Module footprints",
            )
        ],
    )
    text = write_lib_table(table)
    assert text == (
        "(fp_lib_table\n"
        "\t(version 7)\n"
        '\t(lib (name "Audio_Module") (type "KiCad") '
        '(uri "${KICAD10_FOOTPRINT_DIR}/Audio_Module.pretty") '
        '(options "") (descr "Audio Module footprints"))\n'
        ")\n"
    )


def test_writer_always_emits_version_7() -> None:
    # Even when a different version was read, KiCad (and we) emit 7.
    table = LibTable(kind="sym", version=5, rows=[])
    text = write_lib_table(table)
    assert "(version 7)" in text
    assert text.startswith("(sym_lib_table\n\t(version 7)\n)\n")


# ---------------------------------------------------------------------------
# Inline flags (disabled / hidden)
# ---------------------------------------------------------------------------


def test_disabled_and_hidden_inline_order() -> None:
    row = LibTableRow(name="X", type="KiCad", uri="a", disabled=True, hidden=True)
    table = LibTable(kind="fp", rows=[row])
    text = write_lib_table(table)
    line = [ln for ln in text.splitlines() if "(lib " in ln][0]
    assert line.endswith('(descr "") (disabled) (hidden))')


def test_only_hidden_no_disabled() -> None:
    row = LibTableRow(name="X", type="KiCad", uri="a", hidden=True)
    text = write_lib_table(LibTable(kind="fp", rows=[row]))
    assert "(hidden)" in text
    assert "(disabled)" not in text


def test_flags_round_trip() -> None:
    row = LibTableRow(name="X", type="KiCad", uri="a", disabled=True, hidden=True)
    text = write_lib_table(LibTable(kind="fp", rows=[row]))
    reloaded = load_lib_table(parse(text))
    assert reloaded.rows[0].disabled is True
    assert reloaded.rows[0].hidden is True


# ---------------------------------------------------------------------------
# Empty options / descr
# ---------------------------------------------------------------------------


def test_empty_options_and_descr_emitted() -> None:
    row = LibTableRow(name="X", type="KiCad", uri="a")
    text = write_lib_table(LibTable(kind="fp", rows=[row]))
    assert '(options "")' in text
    assert '(descr "")' in text


# ---------------------------------------------------------------------------
# URI backslash normalization
# ---------------------------------------------------------------------------


def test_uri_backslashes_normalized() -> None:
    row = LibTableRow(name="X", type="KiCad", uri="C:\\libs\\Foo.pretty")
    text = write_lib_table(LibTable(kind="fp", rows=[row]))
    assert '(uri "C:/libs/Foo.pretty")' in text
    assert "\\" not in text.split("(uri ")[1].split(")")[0]


# ---------------------------------------------------------------------------
# Escaping: quotes and newlines in descr
# ---------------------------------------------------------------------------


def test_quotes_and_newline_escaped_in_descr() -> None:
    row = LibTableRow(name="X", type="KiCad", uri="a", descr='say "hi"\nline2')
    text = write_lib_table(LibTable(kind="fp", rows=[row]))
    assert r'(descr "say \"hi\"\nline2")' in text


# ---------------------------------------------------------------------------
# Duplicate nicknames preserved (list, not dict)
# ---------------------------------------------------------------------------


def test_duplicate_nicknames_preserved() -> None:
    flat = (
        "(fp_lib_table(version 7)"
        '(lib(name "Dup")(type "KiCad")(uri "a")(options "")(descr ""))'
        '(lib(name "Dup")(type "KiCad")(uri "b")(options "")(descr "")))'
    )
    table = load_lib_table(parse(flat))
    assert len(table.rows) == 2
    assert table.rows[0].uri == "a"
    assert table.rows[1].uri == "b"


# ---------------------------------------------------------------------------
# Version-absent input round-trips with version 7 added
# ---------------------------------------------------------------------------


def test_version_absent_read_then_writes_7() -> None:
    flat = '(fp_lib_table(lib(name "X")(type "KiCad")(uri "a")(options "")(descr "")))'
    table = load_lib_table(parse(flat))
    assert table.version is None
    text = write_lib_table(table)
    assert "\t(version 7)\n" in text


def test_bare_version_token_read() -> None:
    flat = "(fp_lib_table(version 7))"
    table = load_lib_table(parse(flat))
    assert table.version == 7
    assert table.rows == []


# ---------------------------------------------------------------------------
# Bare (unquoted) row values accepted (grammar PROPERTY_VALUE = quoted OR token)
# ---------------------------------------------------------------------------


def test_bare_token_values_accepted() -> None:
    flat = "(fp_lib_table(version 7)(lib(name Foo)(type KiCad)(uri path)))"
    table = load_lib_table(parse(flat))
    assert table.rows[0].name == "Foo"
    assert table.rows[0].type == "KiCad"
    assert table.rows[0].uri == "path"


# ---------------------------------------------------------------------------
# Strict parsing: unknown tokens hard-error
# ---------------------------------------------------------------------------


def test_unknown_root_token_raises() -> None:
    with pytest.raises(KicadParseError):
        load_lib_table(parse("(footprint (version 7))"))


def test_unknown_top_level_token_raises() -> None:
    flat = '(fp_lib_table(version 7)(bogus "x"))'
    with pytest.raises(KicadParseError):
        load_lib_table(parse(flat))


def test_unknown_row_member_raises() -> None:
    flat = '(fp_lib_table(lib(name "X")(type "K")(uri "a")(bogus "y")))'
    with pytest.raises(KicadParseError):
        load_lib_table(parse(flat))


def test_row_missing_required_key_raises() -> None:
    flat = '(fp_lib_table(lib(name "X")(type "K")))'
    with pytest.raises(KicadParseError):
        load_lib_table(parse(flat))


# ---------------------------------------------------------------------------
# sym kind + dump_lib_table flat helper
# ---------------------------------------------------------------------------


def test_sym_kind_root_token() -> None:
    table = LibTable(kind="sym", rows=[])
    assert dump_lib_table(table).startswith("(sym_lib_table")


def test_dump_flat_is_unindented() -> None:
    table = LibTable(kind="fp", rows=[LibTableRow(name="X", type="K", uri="a")])
    flat = dump_lib_table(table)
    assert "\n" not in flat
    assert "\t" not in flat
