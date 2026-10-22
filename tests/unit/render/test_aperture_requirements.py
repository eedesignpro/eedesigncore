"""Unrelated design rules do not require aperture rule evaluation."""

import pytest

from eedesigncore.dru import DrcFile
from eedesigncore.render.aperture_requirements import aperture_rule_dependencies


def test_unrelated_constraints_and_comments_do_not_trigger():
    document = DrcFile.from_string(
        "(version 1)\n# solder_mask_expansion is not an active constraint\n"
        '(rule "via size" (constraint hole_size (min 0.2mm)))'
    )
    assert aperture_rule_dependencies(document, "F.Mask") == ()
    assert aperture_rule_dependencies(document, "B.Paste") == ()


def test_mask_and_paste_rules_remain_separate_and_unchanged():
    source = (
        "(version 1)\n"
        '(rule "mask" (condition "A.Reference == \'U1\'") '
        "(constraint solder_mask_expansion (min 0.05mm)))\n"
        '(rule "paste" (constraint solder_paste_abs_margin (opt -0.02mm)))\n'
        '(rule "paste" (constraint solder_paste_rel_margin (opt -0.1)))'
    )
    document = DrcFile.from_string(source)
    before = document.model_dump()
    mask = aperture_rule_dependencies(document, "B.Mask")
    paste = aperture_rule_dependencies(document, "F.Paste")
    assert [(row.rule_index, row.rule_name) for row in mask] == [(0, "mask")]
    assert [row.rule_index for row in paste] == [1, 2]
    assert document.model_dump() == before


def test_unknown_layer_rejects():
    with pytest.raises(ValueError, match="mask or paste"):
        aperture_rule_dependencies(DrcFile(), "F.Cu")
