"""The part search: KiCad's weights, KiCad's arithmetic, and the field tests.

Every expected number here is computed from the cited KiCad source, not from
this library's output.
"""

from __future__ import annotations

from eedesigncore.library import (
    FieldTest,
    PartRecord,
    score_record,
    search_records,
    search_terms,
    split_query,
)


def _record(**overrides: object) -> PartRecord:
    base: dict[str, object] = {
        "name": "R",
        "nickname": "Device",
        "fields": {
            "Reference": "R",
            "Value": "R",
            "Footprint": "",
            "Datasheet": "",
            "Description": "Resistor",
        },
        "keywords": "R res resistor",
    }
    base.update(overrides)
    return PartRecord(**base)  # type: ignore[arg-type]


class TestQuerySplit:
    def test_the_split_matches_kicad(self) -> None:
        assert split_query("10k  0402\tX7R\nNP0\r") == ["10k", "0402", "x7r", "np0"]

    def test_an_empty_query_has_no_words(self) -> None:
        assert split_query("   \t ") == []

    def test_the_word_count_is_capped_at_one_hundred(self) -> None:
        assert len(split_query(" ".join(str(n) for n in range(150)))) == 100


class TestTermSet:
    def test_kicads_six_terms_come_first_with_kicads_weights(self) -> None:
        terms = search_terms(_record(), include_every_field=False)
        assert [(term.label, term.weight) for term in terms] == [
            ("nickname", 4),
            ("name", 8),
            ("lib_id", 16),
            ("keyword_token", 4),
            ("keyword_token", 4),
            ("keyword_token", 4),
            ("keywords", 1),
            ("description", 1),
        ]

    def test_an_empty_footprint_field_adds_no_term(self) -> None:
        labels = [t.label for t in search_terms(_record(), include_every_field=False)]
        assert "footprint" not in labels

    def test_a_set_footprint_field_adds_one_term(self) -> None:
        record = _record(fields={"Footprint": "Resistor_SMD:R_0402"})
        terms = search_terms(record, include_every_field=False)
        assert [t for t in terms if t.label == "footprint"][0].weight == 1

    def test_every_other_field_becomes_a_term_at_weight_one(self) -> None:
        record = _record(fields={"IPN": "PN-00457", "Tolerance": "1%"})
        terms = {t.label: t.weight for t in search_terms(record)}
        assert terms["field:IPN"] == 1
        assert terms["field:Tolerance"] == 1

    def test_a_field_weight_override_applies(self) -> None:
        record = _record(fields={"IPN": "PN-00457"})
        terms = {t.label: t.weight for t in search_terms(record, field_weights={"IPN": 12})}
        assert terms["field:IPN"] == 12

    def test_a_zero_weight_drops_the_field(self) -> None:
        record = _record(fields={"IPN": "PN-00457"})
        labels = [t.label for t in search_terms(record, field_weights={"IPN": 0})]
        assert "field:IPN" not in labels

    def test_the_description_and_footprint_are_not_counted_twice(self) -> None:
        record = _record(fields={"Description": "Resistor", "Footprint": "X:Y"})
        labels = [t.label for t in search_terms(record)]
        assert "field:Description" not in labels
        assert "field:Footprint" not in labels

    def test_a_term_is_lowered_trimmed_and_capped(self) -> None:
        record = _record(fields={"Long": "  " + "A" * 1200 + "  "})
        term = next(t for t in search_terms(record) if t.label == "field:Long")
        assert term.text == "a" * 1000


class TestScoring:
    def test_an_empty_query_scores_the_base_one(self) -> None:
        assert score_record(_record(), "").score == 1

    def test_an_exact_name_match_scores_eight_times_its_weight(self) -> None:
        # Every term the query word "r" touches, with its weight and its
        # multiplier, computed from the cited source and nothing else:
        #   name "r"                exact    8 x 8 = 64
        #   lib_id "device:r"       substring   16 = 16
        #   keyword token "r"       exact    4 x 8 = 32
        #   keyword token "res"     prefix   4 x 2 =  8
        #   keyword token "resistor" prefix  4 x 2 =  8
        #   keywords whole          prefix   1 x 2 =  2
        #   description "resistor"  prefix   1 x 2 =  2
        #   field:Reference "r"     exact    1 x 8 =  8
        #   field:Value "r"         exact    1 x 8 =  8
        # nickname "device" holds no r; Footprint and Datasheet are empty.
        match = score_record(_record(), "r")
        assert match.score == 1 + 64 + 16 + 32 + 8 + 8 + 2 + 2 + 8 + 8

    def test_a_prefix_match_scores_two_times_its_weight(self) -> None:
        record = PartRecord(name="Resistor", nickname="Dev", fields={}, keywords="")
        # name "resistor" prefix -> 16; lib_id "dev:resistor" substring -> 16.
        assert score_record(record, "res").score == 1 + 16 + 16

    def test_a_substring_match_scores_its_weight_once(self) -> None:
        record = PartRecord(name="XResistor", nickname="Dev", fields={}, keywords="")
        assert score_record(record, "res").score == 1 + 8 + 16

    def test_a_word_matching_nothing_zeroes_the_whole_score(self) -> None:
        assert score_record(_record(), "r inductor").score == 0

    def test_two_matching_words_both_add(self) -> None:
        record = PartRecord(name="C", nickname="Device", fields={"Value": "100nF"}, keywords="cap")
        one = score_record(record, "c").score
        two = score_record(record, "c 100nf").score
        assert two > one

    def test_a_reason_names_the_term_and_the_kind(self) -> None:
        match = score_record(_record(), "r")
        best = match.reasons[0]
        assert best.term == "name"
        assert best.kind == "exact"
        assert best.points == 64
        assert best.query_word == "r"

    def test_a_custom_field_can_be_the_reason(self) -> None:
        record = PartRecord(name="R", nickname="x", fields={"Tolerance": "1%"}, keywords="")
        match = score_record(record, "1%")
        assert [r.term for r in match.reasons] == ["field:Tolerance"]


class TestWildcardAndRegex:
    def test_a_wildcard_word_matches_the_way_kicad_compiles_it(self) -> None:
        record = PartRecord(name="R_Small", nickname="Device", fields={}, keywords="")
        assert score_record(record, "r_*all").score > 0
        assert score_record(record, "r_*zzz").score == 0

    def test_a_question_mark_matches_one_character(self) -> None:
        record = PartRecord(name="R12", nickname="Device", fields={}, keywords="")
        assert score_record(record, "r1?").score > 0
        assert score_record(record, "r1??").score == 0

    def test_an_explicit_regex_is_honoured(self) -> None:
        record = PartRecord(name="R_100", nickname="Device", fields={}, keywords="")
        assert score_record(record, "/^r_[0-9]+$/").score > 0
        assert score_record(record, "/^c_[0-9]+$/").score == 0

    def test_a_bare_word_is_not_treated_as_a_regex(self) -> None:
        # KiCad requires ^...$ or /.../ for a regex (eda_pattern_match.cpp:96-99).
        record = PartRecord(name="R.1", nickname="Device", fields={}, keywords="")
        assert score_record(record, "r.1").score > 0
        other = PartRecord(name="RX1", nickname="Device", fields={}, keywords="")
        assert score_record(other, "r.1").score == 0

    def test_a_broken_regex_does_not_raise(self) -> None:
        record = PartRecord(name="R", nickname="Device", fields={}, keywords="")
        assert score_record(record, "/[unclosed/").score == 0


class TestFieldTests:
    def test_equals_ignores_case_by_default(self) -> None:
        record = _record(fields={"SEPackage": "0402"})
        assert FieldTest(field="sepackage", op="equals", value="0402").matches(record)

    def test_a_cased_test_is_exact(self) -> None:
        record = _record(fields={"Grade": "A"})
        assert not FieldTest(field="Grade", op="equals", value="a", cased=True).matches(record)

    def test_contains_and_one_of(self) -> None:
        record = _record(fields={"AXDescription": "RES_10K_1%_0.05W_0201"})
        assert FieldTest(field="AXDescription", op="contains", value="10k").matches(record)
        assert FieldTest(
            field="AXDescription", op="one_of", values=["nope", "RES_10K_1%_0.05W_0201"]
        ).matches(record)

    def test_present_and_absent(self) -> None:
        record = _record(fields={"Set": "yes", "Empty": ""})
        assert FieldTest(field="Set", op="present").matches(record)
        assert FieldTest(field="Empty", op="absent").matches(record)
        assert FieldTest(field="NotThere", op="absent").matches(record)

    def test_a_numeric_test_reads_a_plain_number(self) -> None:
        record = _record(fields={"value_numeric": "10000"})
        assert FieldTest(field="value_numeric", op="ge", number=10000).matches(record)
        assert not FieldTest(field="value_numeric", op="lt", number=10000).matches(record)

    def test_a_value_with_a_unit_fails_rather_than_raising(self) -> None:
        record = _record(fields={"ResistanceValue": "10K Ohm"})
        assert not FieldTest(field="ResistanceValue", op="gt", number=1).matches(record)

    def test_a_member_can_be_tested_like_a_field(self) -> None:
        record = _record()
        assert FieldTest(field="nickname", op="equals", value="Device").matches(record)
        assert FieldTest(field="lib_id", op="equals", value="Device:R").matches(record)


class TestSearchRecords:
    def test_a_test_drops_a_record_before_scoring(self) -> None:
        keep = _record(fields={"SEPackage": "0402"})
        drop = _record(name="R2", fields={"SEPackage": "0603"})
        found = search_records(
            [keep, drop], "r", tests=[FieldTest(field="SEPackage", value="0402")]
        )
        assert [m.record.name for m in found] == ["R"]

    def test_the_order_is_score_then_lib_id(self) -> None:
        exact = PartRecord(name="GND", nickname="power", fields={}, keywords="")
        prefixed = PartRecord(name="GND1", nickname="power", fields={}, keywords="")
        other = PartRecord(name="GND2", nickname="power", fields={}, keywords="")
        found = search_records([other, prefixed, exact], "gnd")
        assert [m.lib_id for m in found] == ["power:GND", "power:GND1", "power:GND2"]

    def test_an_empty_query_keeps_every_record(self) -> None:
        assert len(search_records([_record(), _record(name="C")], "")) == 2

    def test_the_limit_truncates_after_the_sort(self) -> None:
        records = [PartRecord(name=f"R{n}", nickname="d") for n in range(5)]
        assert len(search_records(records, "r", limit=2)) == 2
