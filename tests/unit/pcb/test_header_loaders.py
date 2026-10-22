"""T2 — board header block loaders (general/paper/title_block/layers/setup/etc.)."""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.pcb.loader import load_pcb
from eedesigncore.sexpr.parser import parse

HEADER = """
(kicad_pcb
    (version 20260206)
    (generator "pcbnew")
    (generator_version "10.0")
    (general
        (thickness 1.6)
        (legacy_teardrops no)
    )
    (paper "B")
    (title_block
        (date "2026-05-26")
    )
    (layers
        (0 "F.Cu" signal "TopLayer1.Cu")
        (2 "B.Cu" signal "BottomLayer2.Cu")
        (5 "F.SilkS" user)
        (27 "Margin" user)
        (65 "User.14" front "TopPCBDXF.U14")
    )
    (setup
        (stackup
            (layer "F.Cu"
                (type "copper")
                (thickness 0.035)
            )
            (layer "dielectric 1"
                (type "core")
                (thickness 1.51)
                (material "FR4")
                (epsilon_r 4.5)
                (loss_tangent 0.02)
            )
            (layer "B.Cu"
                (type "copper")
                (thickness 0.035)
            )
            (copper_finish "None")
            (dielectric_constraints no)
        )
        (pad_to_mask_clearance 0)
        (allow_soldermask_bridges_in_footprints no)
        (tenting
            (front yes)
            (back yes)
        )
        (covering
            (front no)
            (back no)
        )
        (plugging
            (front no)
            (back no)
        )
        (capping no)
        (filling no)
        (pcbplotparams
            (layerselection 0x002a2000_00000000_55555555_5757f5ff)
            (plot_on_all_layers_selection 0x00000000_00000000_00000000_00000000)
            (disableapertmacros no)
            (usegerberextensions no)
            (usegerberattributes yes)
            (usegerberadvancedattributes yes)
            (creategerberjobfile yes)
            (dashed_line_dash_ratio 12)
            (dashed_line_gap_ratio 3)
            (svgprecision 4)
            (plotframeref no)
            (mode 1)
            (useauxorigin no)
            (pdf_front_fp_property_popups yes)
            (pdf_back_fp_property_popups yes)
            (pdf_metadata yes)
            (pdf_single_document no)
            (dxfpolygonmode yes)
            (dxfimperialunits yes)
            (dxfusepcbnewfont yes)
            (psnegative no)
            (psa4output no)
            (plot_black_and_white yes)
            (sketchpadsonfab no)
            (plotpadnumbers no)
            (hidednponfab no)
            (sketchdnponfab yes)
            (crossoutdnponfab yes)
            (subtractmaskfromsilk no)
            (outputformat 1)
            (mirror no)
            (drillshape 0)
            (scaleselection 1)
            (outputdirectory "Gerber/")
        )
    )
    (property "ARRAY_TAB" "03")
    (property "PART_ID" "PN-XXXX")
    (variants
        (variant
            (name "Default")
            (description "The base variant")
        )
        (variant
            (name "NoDesc")
        )
    )
)
"""


def _load():
    return load_pcb(parse(HEADER))


class TestGeneralPaperTitle:
    def test_general(self) -> None:
        b = _load()
        assert b.general is not None
        assert b.general.thickness == 1.6
        assert b.general.legacy_teardrops is False

    def test_paper(self) -> None:
        b = _load()
        assert b.paper is not None
        assert b.paper.paper_type == "B"
        assert b.paper.width is None
        assert b.paper.portrait is False

    def test_title_block(self) -> None:
        b = _load()
        assert b.title_block is not None
        assert b.title_block.date == "2026-05-26"
        assert b.title_block.title == ""


class TestLayers:
    def test_row_count_and_ordinals(self) -> None:
        b = _load()
        assert [row.ordinal for row in b.layers] == [0, 2, 5, 27, 65]

    def test_copper_type_and_user_name(self) -> None:
        b = _load()
        f_cu = b.layers[0]
        assert f_cu.canonical_name == "F.Cu"
        assert f_cu.layer_type == "signal"
        assert f_cu.user_name == "TopLayer1.Cu"

    def test_tech_row_user_type_no_username(self) -> None:
        b = _load()
        silks = b.layers[2]
        assert silks.canonical_name == "F.SilkS"
        assert silks.layer_type == "user"
        assert silks.user_name is None

    def test_user_layer_front_type(self) -> None:
        b = _load()
        u14 = b.layers[4]
        assert u14.canonical_name == "User.14"
        assert u14.layer_type == "front"
        assert u14.user_name == "TopPCBDXF.U14"

    def test_invalid_layer_name_raises(self) -> None:
        text = '(kicad_pcb (layers (25 "Bogus.Layer" user)))'
        with pytest.raises(KicadParseError, match="Unknown layer name"):
            load_pcb(parse(text))

    def test_invalid_layer_type_raises(self) -> None:
        text = '(kicad_pcb (layers (0 "F.Cu" wobble)))'
        with pytest.raises(KicadParseError, match="Unknown layer type"):
            load_pcb(parse(text))

    def test_hide_token_tolerated(self) -> None:
        text = '(kicad_pcb (layers (0 "F.Cu" signal hide)))'
        b = load_pcb(parse(text))
        assert b.layers[0].user_name is None


class TestSetup:
    def test_stackup_layers(self) -> None:
        b = _load()
        assert b.setup is not None
        st = b.setup.stackup
        assert st is not None
        assert [layer.name for layer in st.layers] == ["F.Cu", "dielectric 1", "B.Cu"]
        assert st.copper_finish == "None"
        assert st.dielectric_constraints is False

    def test_stackup_dielectric_sublayer(self) -> None:
        b = _load()
        assert b.setup is not None and b.setup.stackup is not None
        diel = b.setup.stackup.layers[1]
        assert diel.layer_type == "core"
        sub = diel.sublayers[0]
        assert sub.thickness == 1.51
        assert sub.material == "FR4"
        assert sub.epsilon_r == 4.5
        assert sub.loss_tangent == 0.02

    def test_via_finishing_defaults(self) -> None:
        b = _load()
        assert b.setup is not None
        assert b.setup.tenting.front is True
        assert b.setup.tenting.back is True
        assert b.setup.covering.front is False
        assert b.setup.capping is False
        assert b.setup.filling is False

    def test_pcbplotparams(self) -> None:
        b = _load()
        assert b.setup is not None
        pp = b.setup.pcbplotparams
        assert pp.layerselection == "0x002a2000_00000000_55555555_5757f5ff"
        assert pp.usegerberattributes is True
        assert pp.dashed_line_dash_ratio == 12.0
        assert pp.svgprecision == 4
        assert pp.outputdirectory == "Gerber/"
        assert pp.gerberprecision is None  # absent -> default

    def test_unknown_plot_token_dropped(self) -> None:
        # A2 contract: legacy/unknown pcbplotparams children (plotreference/
        # plotvalue/plotfptext, etc.) are accepted-and-dropped on read (mirroring
        # KiCad's `default: skipCurrent()`), never re-emitted.
        text = (
            "(kicad_pcb (setup (pcbplotparams (plotreference yes) (plotvalue no)"
            ' (plotfptext yes) (outputdirectory "Gerber/"))))'
        )
        board = load_pcb(parse(text))
        assert board.setup is not None
        assert board.setup.pcbplotparams.outputdirectory == "Gerber/"
        flat = board.to_string()
        assert "plotreference" not in flat
        assert "plotvalue" not in flat
        assert "plotfptext" not in flat


class TestPropertiesAndVariants:
    def test_properties(self) -> None:
        b = _load()
        assert [(p.name, p.value) for p in b.properties] == [
            ("ARRAY_TAB", "03"),
            ("PART_ID", "PN-XXXX"),
        ]

    def test_variants(self) -> None:
        b = _load()
        assert [v.name for v in b.variants] == ["Default", "NoDesc"]
        assert b.variants[0].description == "The base variant"
        assert b.variants[1].description is None


class TestZoneDefaultsAndOrigins:
    def test_zone_defaults_and_origins(self) -> None:
        text = (
            "(kicad_pcb (setup "
            '(zone_defaults (property (layer "F.Cu") (hatch_position (xy 1.5 2.5)))) '
            "(aux_axis_origin 10 20) (grid_origin 5 6)))"
        )
        b = load_pcb(parse(text))
        assert b.setup is not None
        assert b.setup.zone_defaults is not None
        zd = b.setup.zone_defaults[0]
        assert zd.layer == "F.Cu"
        assert zd.hatch_position == (1.5, 2.5)
        assert b.setup.aux_axis_origin == (10.0, 20.0)
        assert b.setup.grid_origin == (5.0, 6.0)
