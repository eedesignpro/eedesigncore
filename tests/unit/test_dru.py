"""Custom rule preservation, grammar, and independent writer boundaries."""

from pathlib import Path

import pytest

from eedesigncore import (
    DrcBound,
    DrcClause,
    DrcConstraint,
    DrcConstraintType,
    DrcFile,
    DrcRule,
    KicadParseError,
    update_drc_block,
)

SOURCE = """# document comment (kept)
(version 1)
# BEGIN USER RULES
(rule "human" (condition "A.NetClass == 'HV'")
    # keep the reason ) (
    (constraint clearance (min 20mil)))
# END USER RULES
# BEGIN GENERATED SPACING RULES
(rule "z" (constraint clearance (min 0.2mm)))
# END GENERATED SPACING RULES
# between generators
# BEGIN VIA RULES
(rule "a" (constraint via_count (max 2)))
# END VIA RULES
# trailing comment"""


def clearance(name: str, expression: str = "0.5mm") -> DrcRule:
    return DrcRule(
        name=name,
        clauses=[
            DrcConstraint(
                type=DrcConstraintType.CLEARANCE,
                bounds=[DrcBound(kind="min", expression=expression)],
            )
        ],
    )


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("final", ["", "\n"])
@pytest.mark.parametrize("bom", ["", "\ufeff"])
def test_file_bytes_are_unchanged(tmp_path: Path, ending: str, final: str, bom: str) -> None:
    path = tmp_path / "test.kicad_dru"
    original = (bom + SOURCE + final).replace("\n", ending).encode()
    path.write_bytes(original)
    document = DrcFile.from_file(path)
    document.to_file(path)
    assert path.read_bytes() == original
    assert document.to_string() == bom + SOURCE + final


def test_nested_edit_only_changes_the_bound() -> None:
    document = DrcFile.from_string(SOURCE)
    constraint = document.rules[0].clauses[1]
    assert isinstance(constraint, DrcConstraint)
    constraint.bounds[0].expression = "(10 + 15)mil"
    assert document.to_string() == SOURCE.replace("20mil", "(10 + 15)mil")


def test_clause_edit_preserves_order_and_comments() -> None:
    document = DrcFile.from_string(SOURCE)
    condition = document.rules[0].clauses[0]
    assert isinstance(condition, DrcClause)
    condition.value = "A.NetClass == 'LV'"
    assert document.to_string() == SOURCE.replace("'HV'", "'LV'")


def test_rule_add_remove_and_reorder() -> None:
    document = DrcFile.from_string("(version 1)\n(rule z)\n(rule a)")
    document.rules.reverse()
    assert document.to_string() == "(version 1)\n(rule a)\n(rule z)"
    document.rules.pop()
    document.rules.append(clearance("new"))
    reparsed = DrcFile.from_string(document.to_string())
    assert [rule.name for rule in reparsed.rules] == ["a", "new"]
    document.rules.append(clearance("last"))
    assert DrcFile.from_string(document.to_string()).rules[-1].name == "last"


def test_duplicate_names_remain_distinct() -> None:
    source = "(version 1)\n(rule same (severity warning))\n(rule same (severity error))"
    document = DrcFile.from_string(source)
    assert len(document.rules) == 2
    assert document.to_string() == source


def test_block_replacement_preserves_every_outside_byte(tmp_path: Path) -> None:
    path = tmp_path / "test.kicad_dru"
    path.write_bytes(SOURCE.replace("\n", "\r\n").encode())
    replacement = '(rule "replacement" (constraint clearance (min 0.5mm)))\r\n'
    expected = path.read_bytes().replace(
        b'(rule "z" (constraint clearance (min 0.2mm)))\r\n', replacement.encode()
    )
    update_drc_block(path, name="GENERATED SPACING RULES", rules=replacement)
    assert path.read_bytes() == expected
    update_drc_block(path, name="GENERATED SPACING RULES", rules=replacement)
    assert path.read_bytes() == expected
    assert not path.with_suffix(".kicad_dru.lock").exists()


def test_other_generator_reads_fresh_content(tmp_path: Path) -> None:
    path = tmp_path / "test.kicad_dru"
    path.write_bytes(SOURCE.encode())
    update_drc_block(path, name="GENERATED SPACING RULES", rules=[clearance("spacing")])
    update_drc_block(path, name="VIA RULES", rules='(rule "vias" (constraint via_count (max 1)))')
    document = DrcFile.from_file(path)
    assert [rule.name for rule in document.rules] == ["human", "spacing", "vias"]


def test_stale_save_does_not_overwrite_human_edits(tmp_path: Path) -> None:
    path = tmp_path / "test.kicad_dru"
    path.write_bytes(SOURCE.encode())
    old = DrcFile.from_file(path)
    new = SOURCE.replace("20mil", "30mil").encode()
    path.write_bytes(new)
    old.replace_block("VIA RULES", [clearance("changed")])
    with pytest.raises(ValueError, match="changed after"):
        old.to_file(path)
    assert path.read_bytes() == new
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


def test_existing_lock_refuses_update(tmp_path: Path) -> None:
    path = tmp_path / "test.kicad_dru"
    path.write_bytes(SOURCE.encode())
    lock = path.with_name(path.name + ".lock")
    lock.write_text("another writer")
    with pytest.raises(ValueError, match="lock"):
        update_drc_block(path, name="VIA RULES", rules=[])
    assert lock.read_text() == "another writer"
    assert path.read_bytes() == SOURCE.encode()


@pytest.mark.parametrize(
    "markers",
    [
        "# BEGIN A\n",
        "# END A\n",
        "# BEGIN A\n# END B\n",
        "# BEGIN A\n# BEGIN B\n# END B\n# END A\n",
        "# BEGIN A\n# END A\n# BEGIN A\n# END A\n",
        "(rule x\n# BEGIN A\n# END A\n)\n",
    ],
)
def test_ambiguous_markers_refuse_replacement(markers: str) -> None:
    document = DrcFile.from_string("(version 1)\n" + markers)
    with pytest.raises(ValueError):
        document.replace_block("A", [])


def test_create_block_uses_explicit_position() -> None:
    document = DrcFile.from_string(SOURCE)
    document.create_block("STACKUP RULES", [clearance("stackup")], before="VIA RULES")
    assert document.block_names() == (
        "USER RULES",
        "GENERATED SPACING RULES",
        "STACKUP RULES",
        "VIA RULES",
    )
    assert [rule.name for rule in document.rules] == ["human", "z", "stackup", "a"]
    with pytest.raises(ValueError, match="already exists"):
        document.create_block("STACKUP RULES", [])
    with pytest.raises(ValueError, match="missing"):
        document.replace_block("NO SUCH BLOCK", [])


@pytest.mark.parametrize(
    "body",
    [
        "(version 1)",
        "# BEGIN X\n# END X",
        "(rule x) trailing",
        "(rule broken",
    ],
)
def test_invalid_block_does_not_change_document(body: str) -> None:
    document = DrcFile.from_string(SOURCE)
    with pytest.raises(ValueError):
        document.replace_block("VIA RULES", body)
    assert document.to_string() == SOURCE


@pytest.mark.parametrize(
    "body",
    [
        "(rule x)",
        "(version)",
        "(version future)",
        "(version 1) trailing",
        "(version 1) (unknown x)",
        "(version 1) (rule x (unknown 1))",
        "(version 1) (rule x (constraint nope (min 1)))",
        "(version 1) (rule x (constraint clearance (min)))",
        "(version 1) (rule x (constraint clearance (within_diff_pairs)))",
        "(version 1) (rule x (constraint disallow npth))",
        "(version 1) (rule x (constraint zone_connection wrong))",
        "(version 1) (rule x (constraint min_resolved_spokes banana))",
        "(version 1) (rule x (constraint assertion))",
        "(version 1) (rule x (severity fatal))",
        '(version 1) (rule x (condition "a\nb"))',
        '(version 1) (rule x (condition "unterminated))',
        "(version 1) (rule x (constraint hole (min 1)) (constraint hole_size (min 2)))",
        "(version 1) (rule x) # inline comments are not accepted",
    ],
)
def test_malformed_outer_syntax_has_location(body: str) -> None:
    with pytest.raises(KicadParseError, match="line"):
        DrcFile.from_string(body)


@pytest.mark.parametrize("kind", list(DrcConstraintType))
def test_all_constraint_forms(kind: DrcConstraintType) -> None:
    if kind == DrcConstraintType.DISALLOW:
        arguments = ["track", "via", "buried_via", "pad"]
    elif kind == DrcConstraintType.ASSERTION:
        arguments = ["A.NetClass == 'HV'"]
    elif kind == DrcConstraintType.ZONE_CONNECTION:
        arguments = ["thermal_reliefs"]
    elif kind == DrcConstraintType.MIN_RESOLVED_SPOKES:
        arguments = ["3"]
    else:
        arguments = []
    constraint = DrcConstraint(type=kind, arguments=arguments)
    if not arguments:
        constraint.bounds = [DrcBound(kind="max", expression="1")]
    document = DrcFile(rules=[DrcRule(name="constraint", clauses=[constraint])])
    result = DrcFile.from_string(document.to_string())
    assert result.model_dump() == document.model_dump()


def test_skew_arithmetic_units_and_variables_are_preserved() -> None:
    text = """(version 1)
(rule "delay"
 (condition "A.NetName == '${NET}'")
 (constraint skew (within_diff_pairs) (max (5 + 3)ps)))"""
    assert DrcFile.from_string(text).to_string() == text


def test_lexer_decodes_kicad_byte_escapes() -> None:
    text = r'(version 1) (rule "\xc3\xa9\101\x42\q" (condition "# (quoted)"))'
    document = DrcFile.from_string(text)
    assert document.rules[0].name == "éAB\\q"
    assert document.to_string() == text


def test_new_rule_escapes_user_strings() -> None:
    document = DrcFile(
        rules=[
            DrcRule(
                name='a "quote" \\ café',
                clauses=[DrcClause(kind="condition", value="A.NetName == 'a \"quote\"'")],
            )
        ]
    )
    assert DrcFile.from_string(document.to_string()).model_dump() == document.model_dump()


def test_empty_and_comment_only_files() -> None:
    for source in ["", "# notes\n"]:
        assert DrcFile.from_string(source).to_string() == source


def test_resource_limits() -> None:
    with pytest.raises(KicadParseError, match="depth"):
        DrcFile.from_string("(" * 101 + ")" * 101)


def test_save_failure_keeps_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import eedesigncore.dru.models as models

    path = tmp_path / "test.kicad_dru"
    path.write_bytes(SOURCE.encode())

    def fail(*args: object) -> None:
        raise OSError("replacement failed")

    monkeypatch.setattr(models.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failed"):
        update_drc_block(path, name="VIA RULES", rules=[])
    assert path.read_bytes() == SOURCE.encode()
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


def test_deleting_a_rule_does_not_shift_block_ownership() -> None:
    document = DrcFile.from_string(SOURCE)
    removed = document.rules.pop(0)
    assert document.to_string() == SOURCE.replace(removed._render(), "")
    document.replace_block("VIA RULES", [clearance("new via block")])
    assert [rule.name for rule in document.rules] == ["z", "new via block"]


def test_marked_file_requires_block_api_for_structural_additions() -> None:
    document = DrcFile.from_string(SOURCE)
    document.rules.append(clearance("ambiguous owner"))
    with pytest.raises(ValueError, match="block replacement"):
        document.to_string()


def test_adjacent_rules_can_be_deleted_and_replaced() -> None:
    document = DrcFile.from_string("(version 1)(rule a)(rule b)")
    document.rules.pop()
    document.rules.append(DrcRule(name="new"))
    assert [rule.name for rule in DrcFile.from_string(document.to_string()).rules] == ["a", "new"]


def test_copied_crlf_rule_has_no_double_carriage_return(tmp_path: Path) -> None:
    source = "(version 1)\r\n(rule a\r\n (severity warning))\r\n"
    document = DrcFile.from_string(source)
    document.rules.append(document.rules[0].model_copy(deep=True))
    path = tmp_path / "copy.kicad_dru"
    document.to_file(path)
    assert b"\r\r\n" not in path.read_bytes()
    assert len(DrcFile.from_file(path).rules) == 2


def test_version_cannot_be_owned_by_a_plugin() -> None:
    document = DrcFile.from_string("# BEGIN A\n(version 1)\n# END A\n")
    with pytest.raises(ValueError, match="version"):
        document.replace_block("A", [])


def test_bound_cannot_inject_another_clause() -> None:
    with pytest.raises(ValueError):
        DrcBound(kind="min", expression="1)(max 2")


def test_list_mutations_are_validated_before_save() -> None:
    constraint = DrcConstraint(type=DrcConstraintType.CLEARANCE)
    document = DrcFile(rules=[DrcRule(name="invalid", clauses=[constraint])])
    constraint.arguments.append("0.5mm")
    with pytest.raises(ValueError):
        document.to_string()


def test_oversized_version_has_parse_error() -> None:
    with pytest.raises(KicadParseError, match="integer limit"):
        DrcFile.from_string("(version " + "1" * 5000 + ")")


def test_token_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import eedesigncore.dru._syntax as syntax

    monkeypatch.setattr(syntax, "MAX_NODES", 8)
    with pytest.raises(KicadParseError, match="token limit"):
        DrcFile.from_string("(version 1) (rule a) (rule b)")


@pytest.mark.parametrize(
    "source",
    [
        '("version" 1)',
        '(version "1")',
        "(version 1)(rule 123)",
        "(version 1)(rule x (condition 1))",
        '(version 1)(rule x (constraint "clearance" (min 1)))',
        '(version 1)(rule x (constraint min_resolved_spokes "2"))',
    ],
)
def test_quoted_keywords_and_bare_numeric_names_are_rejected(source: str) -> None:
    with pytest.raises(KicadParseError):
        DrcFile.from_string(source)


def test_documentation_examples_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import re

    document_path = Path(__file__).parents[2] / "docs/custom-design-rules.md"
    examples = re.findall(r"```python\n(.*?)```", document_path.read_text(encoding="utf-8"), re.S)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "design.kicad_dru").write_bytes(SOURCE.encode())
    namespace: dict[str, object] = {}
    for example in examples:
        exec(compile(example, str(document_path), "exec"), namespace)
    result = DrcFile.from_file(tmp_path / "design.kicad_dru")
    assert [rule.name for rule in result.rules] == ["human", "high voltage clearance", "a"]
