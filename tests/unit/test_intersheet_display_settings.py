"""Global-label references follow project display settings on a display copy."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from eedesigncore import (
    Paper,
    ProjectFile,
    Schematic,
    SheetHierarchy,
    SheetNode,
    TextResolutionContext,
    context_for_sheet,
    create_global_label,
    resolve_document_text,
)


@pytest.mark.parametrize(
    "own,short,prefix,suffix,expected",
    [
        (True, False, "[", "]", "[1,2,7,A1]"),
        (False, False, "[", "]", "[2,7,A1]"),
        (False, True, "[", "]", "[2..A1]"),
        (False, False, "<", ">", "<2,7,A1>"),
    ],
)
@pytest.mark.parametrize("show", [False, True])
def test_reference_display_settings(own, short, prefix, suffix, expected, show):
    project = ProjectFile.from_string(
        json.dumps(
            {
                "schematic": {
                    "drawing": {
                        "intersheets_ref_show": show,
                        "intersheets_ref_own_page": own,
                        "intersheets_ref_short": short,
                        "intersheets_ref_prefix": prefix,
                        "intersheets_ref_suffix": suffix,
                    }
                }
            }
        )
    )
    source = Schematic(
        paper=Paper(),
        labels=[create_global_label("NET", at=(25.4, 25.4), show_intersheet_refs=not show)],
    )
    before = source.model_copy(deep=True)
    nodes = [
        SheetNode(
            path=Path("shared.kicad_sch"),
            schematic=source,
            name=f"Page {page}",
            kiid_path=f"/root/{page}",
            page=page,
        )
        for page in ("1", "2", "7", "A1")
    ]
    hierarchy = SheetHierarchy(project_root=Path.cwd(), project=project, roots=nodes)
    context = replace(context_for_sheet(hierarchy, nodes[0].kiid_path), resolve_connections=False)
    shown = resolve_document_text(source, context).document
    assert shown.labels[0].fields[0].value == expected
    assert shown.labels[0].fields[0].visible is show
    assert source == before


@pytest.mark.parametrize("visible", [False, True])
def test_missing_setting_preserves_saved_visibility(visible):
    source = Schematic(
        labels=[create_global_label("NET", at=(0, 0), show_intersheet_refs=visible)]
    )
    shown = resolve_document_text(
        source, TextResolutionContext(resolve_connections=False)
    ).document
    assert shown.labels[0].fields[0].visible is visible


def test_short_form_keeps_two_pages_as_list():
    project = ProjectFile.from_string('{"schematic":{"drawing":{"intersheets_ref_short":true}}}')
    source = Schematic(labels=[create_global_label("NET", at=(0, 0))])
    nodes = [
        SheetNode(
            path=Path("shared.kicad_sch"),
            schematic=source,
            name=page,
            kiid_path=f"/root/{page}",
            page=page,
        )
        for page in ("2", "7")
    ]
    hierarchy = SheetHierarchy(project_root=Path.cwd(), project=project, roots=nodes)
    context = replace(context_for_sheet(hierarchy, nodes[0].kiid_path), resolve_connections=False)
    assert resolve_document_text(source, context).document.labels[0].fields[0].value == "[2,7]"
