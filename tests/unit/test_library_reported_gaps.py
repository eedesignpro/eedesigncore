"""The five faults a consumer reported against 0.19.0, one class each.

Each class names the symptom the consumer measured, so a regression reads as
the reported failure and not as an abstract assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore import Property, Schematic, Symbol, inject_symbol
from eedesigncore.common.sch_family import Pin, PinElectricalType, PinShape
from eedesigncore.common.sch_family.symbol import SymbolUnit
from eedesigncore.library import definition_from_row
from eedesigncore.library.database import resolve_row
from eedesigncore.library.dbl import load_dbl
from eedesigncore.tables.resolver import LibTableResolver

SUB_LIBRARY = {
    "name": "Resistors",
    "table": "Resistors",
    "key": "KiCadKey",
    "symbols": "SymbolRef",
    "footprints": "FootprintRef",
    "properties": {"description": "AXDescription"},
    "fields": [
        {"column": "value", "name": "Value", "visible_on_add": True},
        # A column with no matching field on the drawn symbol, so the transform
        # has to CREATE the field. That is the path that carried no text style.
        {"column": "IPN", "name": "IPN"},
    ],
}
DBL = {"globally_unique_keys": True, "libraries": [SUB_LIBRARY]}
ROW = {
    "KiCadKey": "PN-00481_103183",
    "SymbolRef": "provisional_Resistors:RESISTOR",
    "FootprintRef": "provisional_Resistors:RESC1005X40L",
    "AXDescription": "RES 10K 1%",
    "value": "10k",
    "IPN": "PN-00481",
}


def _pin(number: str) -> Pin:
    return Pin(
        number=number,
        name="~",
        electrical_type=PinElectricalType.passive,
        shape=PinShape.line,
    )


def _drawn_resistor() -> Symbol:
    """A two-pin symbol shaped like the one the consumer's row referenced."""
    symbol = Symbol.new("RESISTOR", reference="R", value="RESISTOR")
    symbol.units = [
        SymbolUnit.for_symbol(
            "RESISTOR",
            unit_index=1,
            body_style_index=0,
            items=[_pin("1"), _pin("2")],
        )
    ]
    return symbol


def _row_definition(**kwargs: object) -> Symbol:
    library = load_dbl(DBL)
    return definition_from_row(
        ROW,
        library.libraries[0],
        globally_unique_keys=True,
        **kwargs,  # type: ignore[arg-type]
    )


class TestGapAUnitNamePrefix:
    """Reported: KiCad refuses the whole schematic.

    ``Invalid symbol unit name prefix RESISTOR_1_0``. The parent was renamed to
    the sanitized key and the unit kept the old name
    (``sch_io_kicad_sexpr_parser.cpp:501-505``).
    """

    def test_a_renamed_row_symbol_renames_its_units(self) -> None:
        definition = _row_definition(symbol_loader=lambda _id: _drawn_resistor())

        assert definition.name == "PN-00481_103183"
        assert [unit.name for unit in definition.units] == ["PN-00481_103183_1_0"]

    def test_every_unit_name_carries_the_parent_item_name_as_its_prefix(self) -> None:
        definition = _row_definition(symbol_loader=lambda _id: _drawn_resistor())
        item_name = definition.name.split(":")[-1]

        for unit in definition.units:
            assert unit.name.startswith(f"{item_name}_")
            suffix = unit.name[len(item_name) + 1 :]
            assert [part.isdigit() for part in suffix.split("_")] == [True, True]

    def test_renamed_drops_the_nickname_from_a_unit_name(self) -> None:
        renamed = _drawn_resistor().renamed("partsdb:PN-00457")

        assert renamed.name == "partsdb:PN-00457"
        assert [unit.name for unit in renamed.units] == ["PN-00457_1_0"]

    def test_renamed_leaves_the_original_alone(self) -> None:
        source = _drawn_resistor()
        source.renamed("OTHER")

        assert source.name == "RESISTOR"
        assert [unit.name for unit in source.units] == ["RESISTOR_1_0"]

    def test_the_collision_rename_also_renames_the_units(self) -> None:
        sch = Schematic()
        first = _drawn_resistor()
        inject_symbol(sch, first, at=(10.0, 10.0), lib_nickname="Lib")

        differing = _drawn_resistor()
        differing.fields.append(Property(name="Extra", value="x", visible=False))
        placed = inject_symbol(sch, differing, at=(20.0, 20.0), lib_nickname="Lib")

        registered = next(entry for entry in sch.lib_symbols if entry.name == placed.lib_id)
        assert placed.lib_id == "RESISTOR_1"
        assert [unit.name for unit in registered.units] == ["RESISTOR_1_1_0"]


class TestGapBFieldPositionsAreAbsolute:
    """Reported: every field landed near the sheet origin, not on the part.

    KiCad stores a placed field position as a sheet coordinate and converts on
    construction with ``m_pos + libField->GetTextPos()``
    (``sch_symbol.cpp:1408``, ``:1417``).
    """

    def test_a_zero_offset_field_lands_exactly_on_the_symbol(self) -> None:
        sch = Schematic()
        placed = inject_symbol(sch, _drawn_resistor(), at=(120.0, 180.0), lib_nickname="Lib")

        for name in ("Footprint", "Datasheet", "Description"):
            field = next(f for f in placed.fields if f.name == name)
            assert field.position == (120.0, 180.0)

    def test_a_field_with_an_offset_moves_by_plus_x_and_minus_y(self) -> None:
        """X is added and Y is SUBTRACTED, per ADR-103 decision 1.

        KiCad's parser inverts Y for a library field and not for a placed one,
        so its in-memory offset is the negated file value and its vector add
        comes out as ``at.y - file_y``. The iteration-20 QA review found that
        this test had been written with the same ``+`` the code used on both
        axes, so it passed while the code was wrong. The authoritative test
        loads the definition from a real file:
        ``tests/unit/test_library_review_fixes.py::TestPlacedFieldYSign``.
        """
        symbol = _drawn_resistor()
        next(f for f in symbol.fields if f.name == "Reference").position = (3.81, -2.54)

        placed = inject_symbol(Schematic(), symbol, at=(100.0, 50.0), lib_nickname="Lib")

        reference = next(f for f in placed.fields if f.name == "Reference")
        assert reference.position == (103.81, 52.54)

    def test_the_library_definition_keeps_its_relative_offsets(self) -> None:
        symbol = _drawn_resistor()
        next(f for f in symbol.fields if f.name == "Reference").position = (3.81, -2.54)
        sch = Schematic()

        placed = inject_symbol(sch, symbol, at=(100.0, 50.0), lib_nickname="Lib")
        registered = next(entry for entry in sch.lib_symbols if entry.name == placed.lib_id)

        offsets = {f.name: f.position for f in registered.fields}
        assert offsets["Reference"] == (3.81, -2.54)

    def test_a_rotation_does_not_move_the_offsets(self) -> None:
        """KiCad's own Rotate moves a field by the symbol's displacement only.

        ``SCH_SYMBOL::Rotate`` (``sch_symbol.cpp:2810-2826``). What tidies a
        visible field after a turn is the autoplacer, which this library does
        not implement.
        """
        symbol = _drawn_resistor()
        next(f for f in symbol.fields if f.name == "Reference").position = (3.81, 0.0)

        placed = inject_symbol(
            Schematic(), symbol, at=(100.0, 50.0), rotation=90.0, lib_nickname="Lib"
        )

        # Same position the same part gets at rotation 0: the turn moves the
        # body, not the offsets.
        assert next(f.position for f in placed.fields if f.name == "Reference") == (103.81, 50.0)


class TestGapCReferencePrefixFallback:
    """Reported: a resistor was annotated U2, with no signal.

    The designator itself was a faithful reading of the definition handed in: a
    metadata-only symbol carries the Reference ``U`` that KiCad's own
    ``LIB_SYMBOL`` constructor seeds (``lib_symbol.cpp:247-251``). So the fault
    was upstream, in Gap D and Gap E, and the signal lives there. No refusal is
    raised here, because KiCad falls back to ``U`` too and a caller may
    legitimately place a bare ``Symbol(name=…)``.
    """

    def test_an_explicit_reference_still_places_a_fieldless_definition(self) -> None:
        placed = inject_symbol(
            Schematic(), Symbol(name="NO_FIELDS"), at=(10.0, 10.0), reference="TP1"
        )

        assert placed.instances[0].paths[0].reference == "TP1"

    def test_a_real_prefix_is_used_rather_than_the_u_fallback(self) -> None:
        placed = inject_symbol(Schematic(), _drawn_resistor(), at=(10.0, 10.0), lib_nickname="Lib")

        assert placed.instances[0].paths[0].reference == "R1"


class TestGapDMetadataOnlySymbol:
    """Reported: a pin-less symbol was placed and looked like a real part."""

    def test_a_resolved_row_reports_a_complete_symbol(self) -> None:
        library = load_dbl(DBL)
        built = resolve_row(
            ROW,
            library.libraries[0],
            globally_unique_keys=True,
            symbol_loader=lambda _id: _drawn_resistor(),
        )

        assert built.source_found is True
        assert built.metadata_only is False
        assert built.has_pins is True
        assert built.unresolved_reason == ""

    def test_a_resolved_row_reports_the_metadata_only_case(self) -> None:
        library = load_dbl(DBL)
        built = resolve_row(
            ROW,
            library.libraries[0],
            globally_unique_keys=True,
            symbol_loader=lambda _id: None,
        )

        assert built.metadata_only is True
        assert built.has_pins is False
        assert built.symbol_reference == "provisional_Resistors:RESISTOR"
        assert "found nothing" in built.unresolved_reason
        assert "skipped" in built.unresolved_reason

    def test_a_missing_loader_says_so(self) -> None:
        library = load_dbl(DBL)
        built = resolve_row(ROW, library.libraries[0], globally_unique_keys=True)

        assert built.metadata_only is True
        assert "no symbol loader was given" in built.unresolved_reason

    def test_an_invalid_reference_says_so(self) -> None:
        library = load_dbl(DBL)
        built = resolve_row(
            dict(ROW, SymbolRef="no-nickname"),
            library.libraries[0],
            globally_unique_keys=True,
            symbol_loader=lambda _id: _drawn_resistor(),
        )

        assert built.metadata_only is True
        assert "not a valid library id" in built.unresolved_reason

    def test_require_source_raises_instead_of_returning_a_bodyless_part(self) -> None:
        with pytest.raises(ValueError, match="found nothing"):
            _row_definition(symbol_loader=lambda _id: None, require_source=True)

    def test_the_default_still_matches_kicad_and_returns_the_symbol(self) -> None:
        definition = _row_definition(symbol_loader=lambda _id: None)

        assert definition.name == "PN-00481_103183"
        assert definition.units == []


class TestGapESilentChainLoss:
    """Reported: a nested Table row was dropped with no signal, both kinds."""

    def _chain(self, tmp_path: Path, kind: str) -> tuple[Path, Path]:
        """Build a global table naming a nested table by absolute path."""
        settings = tmp_path / "settings"
        settings.mkdir(parents=True)
        store = tmp_path / "library-store"
        store.mkdir()

        root = "sym_lib_table" if kind == "sym" else "fp_lib_table"
        file_name = "sym-lib-table" if kind == "sym" else "fp-lib-table"

        if kind == "sym":
            leaf = store / "Parts"
            leaf.mkdir()
            (leaf / "PART.kicad_sym").write_text(
                '(kicad_symbol_lib (symbol "PART"\n\t)\n)\n', encoding="utf-8"
            )
        else:
            leaf = store / "Parts.pretty"
            leaf.mkdir()
            (leaf / "PART.kicad_mod").write_text("(footprint)", encoding="utf-8")

        nested = store / "nested_table"
        nested.write_text(
            f'({root}\n  (lib (name "Leaf")(type "KiCad")(uri "{leaf.as_posix()}")'
            '(options "")(descr ""))\n)\n',
            encoding="utf-8",
        )
        (settings / file_name).write_text(
            f'({root}\n  (lib (name "Chain")(type "Table")(uri "{nested.as_posix()}")'
            '(options "")(descr ""))\n)\n',
            encoding="utf-8",
        )
        return settings, leaf

    @pytest.mark.parametrize("kind", ["sym", "fp"])
    def test_a_global_chain_to_an_absolute_path_now_resolves(
        self, tmp_path: Path, kind: str
    ) -> None:
        settings, _leaf = self._chain(tmp_path, kind)
        resolver = LibTableResolver.discover(
            settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0", env={}
        )

        table = resolver.resolve(kind)  # type: ignore[arg-type]
        assert table.nicknames == ["Leaf"]
        assert table.skipped == []

    @pytest.mark.parametrize("kind", ["sym", "fp"])
    def test_the_library_behind_the_chain_is_searchable(self, tmp_path: Path, kind: str) -> None:
        settings, _leaf = self._chain(tmp_path, kind)
        table = LibTableResolver.discover(
            settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0", env={}
        ).resolve(kind)  # type: ignore[arg-type]

        assert table.nickname_for("PART") == "Leaf"

    @pytest.mark.parametrize("kind", ["sym", "fp"])
    def test_turning_the_chain_trust_off_records_the_loss(self, tmp_path: Path, kind: str) -> None:
        settings, _leaf = self._chain(tmp_path, kind)
        table = LibTableResolver.discover(
            settings_dir=settings,
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={},
            trust_table_chain=False,
        ).resolve(kind)  # type: ignore[arg-type]

        assert table.nicknames == []
        assert len(table.skipped) == 1
        assert "outside the allowed roots" in table.skipped[0].reason
        assert "allowed_roots" in table.skipped[0].reason
        assert table.skipped[0].scope == "global"

    def test_the_project_tier_is_not_widened(self, tmp_path: Path) -> None:
        """A project is often a repository the caller did not write (MEDIUM-2)."""
        project = tmp_path / "proj"
        project.mkdir()
        store = tmp_path / "outside"
        leaf = store / "Parts.pretty"
        leaf.mkdir(parents=True)
        (leaf / "PART.kicad_mod").write_text("(footprint)", encoding="utf-8")
        nested = store / "nested_table"
        nested.write_text(
            f'(fp_lib_table\n  (lib (name "Leaf")(type "KiCad")(uri "{leaf.as_posix()}")'
            '(options "")(descr ""))\n)\n',
            encoding="utf-8",
        )
        (project / "fp-lib-table").write_text(
            f'(fp_lib_table\n  (lib (name "Chain")(type "Table")(uri "{nested.as_posix()}")'
            '(options "")(descr ""))\n)\n',
            encoding="utf-8",
        )

        table = LibTableResolver.discover(
            settings_dir=tmp_path / "settings",
            project_dir=project,
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={},
        ).resolve("fp")

        # Two skips: the absent global table, and the refused project chain.
        project_skips = [s for s in table.skipped if s.scope == "project"]
        assert table.nicknames == []
        assert len(project_skips) == 1
        assert "outside the allowed roots" in project_skips[0].reason

    def test_a_missing_table_is_recorded_too(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        table = LibTableResolver.discover(
            settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0", env={}
        ).resolve("sym")

        assert [s.reason.startswith("No library table at") for s in table.skipped] == [True]

    def test_a_malformed_table_is_recorded(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        (settings / "sym-lib-table").write_text("(sym_lib_table (lib", encoding="utf-8")
        table = LibTableResolver.discover(
            settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0", env={}
        ).resolve("sym")

        assert len(table.skipped) == 1
        assert "Malformed" in table.skipped[0].reason

    def test_a_clean_resolve_records_nothing(self, tmp_path: Path) -> None:
        settings, _leaf = self._chain(tmp_path, "sym")
        table = LibTableResolver.discover(
            settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0", env={}
        ).resolve("sym")

        assert table.skipped == []

    def test_a_resolver_reused_for_two_resolves_does_not_accumulate(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        resolver = LibTableResolver.discover(
            settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0", env={}
        )

        first = resolver.resolve("sym")
        second = resolver.resolve("sym")

        assert len(first.skipped) == len(second.skipped) == 1


class TestGapFPlacedFieldTextStyle:
    """Reported: field text is still not working right.

    Every field the row transform created carried NO text-style block. KiCad
    writes one on every placed field: SCH_FIELD allows multiline text, so
    ``EDA_TEXT::IsDefaultFormatting`` is false (``common/eda_text.cpp:1054``)
    and ``saveField`` emits the effects unconditionally
    (``sch_io_kicad_sexpr.cpp:1021-1025``). Measured on 200 corpus schematics:
    55338 placed fields carry the block, and the 61 that do not are all legacy
    demo files.
    """

    def test_a_field_the_row_transform_creates_carries_default_effects(self) -> None:
        definition = _row_definition(symbol_loader=lambda _id: _drawn_resistor())

        created = next(f for f in definition.fields if f.name == "IPN")
        assert created.attributes is not None
        assert created.attributes.font_size == (1.27, 1.27)

    def test_every_field_of_a_metadata_only_definition_carries_effects(self) -> None:
        definition = _row_definition(symbol_loader=lambda _id: None)

        assert definition.fields
        for field in definition.fields:
            assert field.attributes is not None, field.name

    def test_a_placed_field_writes_the_effects_block_kicad_writes(self) -> None:
        definition = _row_definition(symbol_loader=lambda _id: _drawn_resistor())
        sch = Schematic()
        inject_symbol(sch, definition, at=(50.0, 50.0), lib_nickname="partsdb")

        text = sch.to_string()
        start = text.index('(property "IPN"')
        block = text[start : text.index("(property", start + 10)]
        assert "(effects" in block
        assert "(size 1.27 1.27)" in block

    def test_symbol_new_seeds_the_same_default(self) -> None:
        for field in Symbol.new("PART").fields:
            assert field.attributes is not None, field.name
            assert field.attributes.font_size == (1.27, 1.27)
