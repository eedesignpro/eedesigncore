"""T3 — board root writer (full header skeleton emission)."""

from __future__ import annotations

from eedesigncore.common.page import Paper, TitleBlock
from eedesigncore.common.pcb_family import ZoneLayerProperty
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import (
    Board,
    BoardGeneral,
    BoardLayer,
    BoardStackup,
    BoardStackupLayer,
    BoardStackupSubLayer,
    BoardVariant,
    FrontBack,
    PcbGenerator,
    PcbTarget,
    PlotParams,
    Setup,
)
from eedesigncore.pcb.writer import dump_board, write_board


class TestGeneralPaperTitle:
    def test_general(self) -> None:
        b = Board(general=BoardGeneral(thickness=1.6, legacy_teardrops=False))
        flat = dump_board(b)
        assert "(thickness 1.6)" in flat
        assert "(legacy_teardrops no)" in flat

    def test_paper_simple(self) -> None:
        b = Board(paper=Paper(paper_type="B"))
        assert '(paper "B")' in dump_board(b)

    def test_paper_user_size(self) -> None:
        b = Board(paper=Paper(paper_type="User", width=210.0, height=297.0))
        assert '(paper "User" 210 297)' in dump_board(b)

    def test_paper_portrait(self) -> None:
        b = Board(paper=Paper(paper_type="A4", portrait=True))
        assert '(paper "A4" portrait)' in dump_board(b)

    def test_title_block_emitted_when_content(self) -> None:
        b = Board(title_block=TitleBlock(date="2026-05-26"))
        flat = dump_board(b)
        assert "(title_block" in flat
        assert '(date "2026-05-26")' in flat

    def test_title_block_omitted_when_empty(self) -> None:
        b = Board(title_block=TitleBlock())
        assert "title_block" not in dump_board(b)

    def test_title_block_comment_order(self) -> None:
        b = Board(title_block=TitleBlock(comments={2: "two", 1: "one"}))
        flat = dump_board(b)
        assert '(comment 1 "one") (comment 2 "two")' in flat


class TestLayers:
    def test_layer_row_with_user_name(self) -> None:
        b = Board(
            layers=[
                BoardLayer(
                    ordinal=0, canonical_name="F.Cu", layer_type="signal", user_name="TopLayer1.Cu"
                )
            ]
        )
        assert '(0 "F.Cu" signal "TopLayer1.Cu")' in dump_board(b)

    def test_layer_row_without_user_name(self) -> None:
        b = Board(layers=[BoardLayer(ordinal=27, canonical_name="Margin", layer_type="user")])
        assert '(27 "Margin" user)' in dump_board(b)


class TestSetup:
    def _setup(self) -> Setup:
        return Setup(
            stackup=BoardStackup(
                layers=[
                    BoardStackupLayer(
                        name="F.Cu",
                        layer_type="copper",
                        sublayers=[BoardStackupSubLayer(thickness=0.035)],
                    ),
                    BoardStackupLayer(
                        name="dielectric 1",
                        layer_type="core",
                        sublayers=[
                            BoardStackupSubLayer(
                                thickness=1.51, material="FR4", epsilon_r=4.5, loss_tangent=0.02
                            )
                        ],
                    ),
                ],
                copper_finish="None",
            ),
            pcbplotparams=PlotParams(outputdirectory="Gerber/"),
        )

    def test_stackup(self) -> None:
        flat = dump_board(Board(setup=self._setup()))
        assert '(layer "F.Cu"' in flat
        assert '(type "copper")' in flat
        assert "(thickness 0.035)" in flat
        assert "(epsilon_r 4.5)" in flat
        assert "(loss_tangent 0.02)" in flat
        assert '(copper_finish "None")' in flat
        assert "(dielectric_constraints no)" in flat

    def test_via_finishing_always_emitted(self) -> None:
        flat = dump_board(Board(setup=Setup(tenting=FrontBack(front=True, back=True))))
        assert "(tenting (front yes) (back yes))" in flat
        assert "(covering (front no) (back no))" in flat
        assert "(plugging (front no) (back no))" in flat
        assert "(capping no)" in flat
        assert "(filling no)" in flat
        assert "(pad_to_mask_clearance 0)" in flat

    def test_plot_params_gerberprecision_omitted(self) -> None:
        flat = dump_board(Board(setup=Setup()))
        assert "gerberprecision" not in flat

    def test_plot_params_gerberprecision_emitted(self) -> None:
        flat = dump_board(Board(setup=Setup(pcbplotparams=PlotParams(gerberprecision=6))))
        assert "(gerberprecision 6)" in flat

    def test_addsublayer(self) -> None:
        layer = BoardStackupLayer(
            name="dielectric 1",
            layer_type="prepreg",
            sublayers=[
                BoardStackupSubLayer(thickness=0.1),
                BoardStackupSubLayer(thickness=0.2),
            ],
        )
        flat = dump_board(Board(setup=Setup(stackup=BoardStackup(layers=[layer]))))
        assert "addsublayer" in flat

    def test_thickness_locked(self) -> None:
        layer = BoardStackupLayer(
            name="dielectric 1",
            layer_type="core",
            sublayers=[BoardStackupSubLayer(thickness=1.5, thickness_locked=True)],
        )
        flat = dump_board(Board(setup=Setup(stackup=BoardStackup(layers=[layer]))))
        assert "(thickness 1.5 locked)" in flat

    def test_zone_defaults_and_origins(self) -> None:
        s = Setup(
            zone_defaults=[ZoneLayerProperty(layer="F.Cu", hatch_position=(1.5, 2.5))],
            aux_axis_origin=(10.0, 20.0),
            grid_origin=(5.0, 6.0),
        )
        flat = dump_board(Board(setup=s))
        assert "(zone_defaults" in flat
        assert '(layer "F.Cu")' in flat
        assert "(hatch_position (xy 1.5 2.5))" in flat
        assert "(aux_axis_origin 10 20)" in flat
        assert "(grid_origin 5 6)" in flat

    def test_empty_zone_defaults_still_emitted(self) -> None:
        # KiCad emits a bare (zone_defaults) when the layer-properties map is
        # non-empty but no layer has a hatching_offset (:606-614, 3097-3099).
        # None = absent (not emitted); [] = present-but-empty (emitted bare).
        assert "(zone_defaults)" in dump_board(Board(setup=Setup(zone_defaults=[])))
        assert "zone_defaults" not in dump_board(Board(setup=Setup(zone_defaults=None)))


class TestPropertiesVariantsTail:
    def test_properties_sorted(self) -> None:
        from eedesigncore.common.models import Property

        b = Board(properties=[Property(name="ZZZ", value="1"), Property(name="AAA", value="2")])
        flat = dump_board(b)
        assert flat.index('"AAA"') < flat.index('"ZZZ"')

    def test_variants(self) -> None:
        b = Board(
            variants=[
                BoardVariant(name="Default", description="base"),
                BoardVariant(name="NoDesc"),
            ]
        )
        flat = dump_board(b)
        assert '(variant (name "Default")' in flat
        assert '(description "base")' in flat
        assert '(variant (name "NoDesc")' in flat

    def test_embedded_fonts_always_emitted(self) -> None:
        assert "(embedded_fonts no)" in dump_board(Board())

    def test_generator_version_omitted(self) -> None:
        assert "generator_version" not in dump_board(Board())


class TestSyntheticRoundTrip:
    def test_write_parse_write_idempotent(self) -> None:
        board = Board(
            version=20260206,
            generator="pcbnew",
            generator_version="10.0",
            general=BoardGeneral(thickness=1.6),
            paper=Paper(paper_type="A4"),
            title_block=TitleBlock(rev="A"),
            layers=[
                BoardLayer(ordinal=0, canonical_name="F.Cu", layer_type="signal"),
                BoardLayer(ordinal=2, canonical_name="B.Cu", layer_type="signal"),
            ],
            setup=Setup(),
        )
        first = write_board(board)
        second = Board.from_string(first).to_string()
        assert first == second

    def test_ends_with_single_newline(self) -> None:
        out = write_board(Board())
        assert out.endswith(")\n")
        assert not out.endswith("\n\n")


class TestUnmodeledStreamGuards:
    def test_footprint_emitted_in_board_context(self) -> None:
        # T7: footprints emit in BOARD context (no version block, full FPID).
        out = dump_board(Board(footprints=[Footprint(fp_id="Lib:Name")]))
        assert '(footprint "Lib:Name"' in out
        assert "(version " not in out.split('(footprint "Lib:Name"', 1)[1]

    def test_embedded_files_root_block_emitted(self) -> None:
        # T9: board root emits the consolidated (embedded_files …) block WITH data.
        from eedesigncore.common.models import EmbeddedFile

        ef = EmbeddedFile(name="x", file_type="other", checksum="c", data="QUJD")
        out = dump_board(Board(embedded_files=[ef]))
        tail = out.split("(embedded_fonts", 1)[1]
        assert "(embedded_files" in tail
        assert "QUJD" in tail

    def test_generator_emitted(self) -> None:
        # A non-ghost generator (has members) emits a (generated …) block.
        out = dump_board(Board(generators=[PcbGenerator(uuid="g", members=["m1"])]))
        assert "(generated" in out

    def test_ghost_generator_emits_nothing(self) -> None:
        # A tuning_pattern with no members is a ghost (A3 §1) -> nothing.
        out = dump_board(Board(generators=[PcbGenerator()]))
        assert "(generated" not in out

    def test_target_emitted(self) -> None:
        out = dump_board(Board(targets=[PcbTarget()]))
        assert "(target plus" in out
