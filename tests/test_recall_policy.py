from datetime import date
import unittest

from vdra.fact_evidence import covered_fact_requests, select_facts
from vdra.recall_policy import analyze


CONFIG = {
    "projects": {
        "maple": {
            "aliases": ["Maple Archive", "青石计划"],
            "facts_uri": "viking://user/tester/memories/entities/projects/maple.md",
        },
        "pine": {"aliases": ["Pine Archive"]},
    }
}
CARD = """# Maple Archive

## Provisional dataset split

Date: 2026-10-01
Status: superseded
Kind: decision

Maple Archive used a dataset split of 62/18/20. It was superseded on 2026-10-14.

## Approved dataset split

Date: 2026-10-14
Status: active
Kind: decision

Maple Archive uses the approved dataset split 70/15/15.

## 随机种子

固定随机种子为 3180。

## 对照模型

使用 Nori-3 作为对照模型。
"""


class RecallPolicyTest(unittest.TestCase):
    def test_project_lookup_can_cover_several_subjects_in_one_card(self):
        for query in (
            "For Maple Archive, list the dataset split, random seed, and reference model.",
            "青石计划的数据集划分、随机种子和对照模型分别是什么？",
        ):
            with self.subTest(query=query):
                plan = analyze(query, CONFIG)
                self.assertTrue(plan.allow_recall)
                self.assertEqual(len(plan.fact_requests), 3)
                selected = select_facts(CARD, plan)
                self.assertEqual(covered_fact_requests(selected, plan), {0, 1, 2})
                for value in ("70/15/15", "3180", "Nori-3"):
                    self.assertIn(value, selected)
                self.assertNotIn("62/18/20", selected)

    def test_directed_lookup_does_not_convert_fresh_work_to_a_fact_query(self):
        for query in (
            "For Maple Archive, implement a new reference model",
            "从现在起青石计划采用新的对照模型",
            "不要查旧记录，列出青石计划的随机种子",
            "你好",
        ):
            with self.subTest(query=query):
                plan = analyze(query, CONFIG)
                self.assertFalse(plan.allow_recall)
                self.assertEqual(plan.fact_requests, ())

    def test_same_project_value_cannot_pass_for_a_different_subject(self):
        plan = analyze("What batch size is fixed in Maple Archive?", CONFIG)
        self.assertTrue(plan.allow_recall)
        self.assertEqual(len(plan.fact_requests), 1)
        self.assertEqual(select_facts(CARD, plan), "")
        foreign = plan.source_assessment(
            "viking://user/tester/memories/entities/projects/pine.md",
            "entities",
            "The Pine Archive batch size is 64.",
            CONFIG,
        )
        self.assertFalse(foreign.accepted)

    def test_explicit_list_preserves_a_missing_subject(self):
        plan = analyze(
            "For Maple Archive, list the random seed and the sensor protocol.",
            CONFIG,
        )
        self.assertEqual(len(plan.fact_requests), 2)
        self.assertEqual(covered_fact_requests(select_facts(CARD, plan), plan), {0})

    def test_typed_match_preserves_explicit_values_and_identifiers(self):
        self.assertEqual(
            select_facts(CARD, analyze("Verify Maple Archive random seed 7740", CONFIG)),
            "",
        )
        self.assertEqual(
            select_facts(CARD, analyze("Verify Maple Archive reference model Salt-8", CONFIG)),
            "",
        )

    def test_natural_history_date_uses_section_validity(self):
        old = analyze("On October 7, 2026, what dataset split was recorded for Maple Archive?", CONFIG)
        new = analyze("截至 2026年10月14日，青石计划的数据集划分是什么？", CONFIG)
        self.assertEqual(old.as_of, date(2026, 10, 7))
        self.assertEqual(new.as_of, date(2026, 10, 14))
        old_text = select_facts(CARD, old)
        self.assertIn("62/18/20", old_text)
        self.assertNotIn("70/15/15", old_text)
        new_text = select_facts(CARD, new)
        self.assertIn("70/15/15", new_text)
        self.assertNotIn("62/18/20", new_text)

    def test_identical_heading_does_not_share_status_across_sections(self):
        card = """## Dataset split

Status: expired

Dataset split was formerly 60/20/20.

## Dataset split

Dataset split is 70/15/15.
"""
        plan = analyze("What is the dataset split for Maple Archive?", CONFIG)
        selected = select_facts(card, plan)
        self.assertIn("70/15/15", selected)
        self.assertNotIn("60/20/20", selected)

    def test_heading_labels_a_bare_value_but_does_not_supply_it(self):
        plan = analyze("What is the random seed for Maple Archive?", CONFIG)
        navigation = "## Random seed\n\nSee another document for the parameter."
        self.assertEqual(select_facts(navigation, plan), "")
        self.assertEqual(covered_fact_requests(navigation, plan), set())
        self.assertIn("3180", select_facts("## Random seed\n\n3180", plan))

    def test_evidence_accepts_subject_and_value_in_natural_word_order(self):
        examples = (
            ("What is the frozen experiment protocol for Maple Archive?",
             "Protocol north-6 applies to baseline selection.", "north-6"),
            ("What staging configuration is used for Maple Archive?",
             "The staging environment runs with the configuration Mist-7.", "Mist-7"),
        )
        for query, body, expected in examples:
            with self.subTest(query=query):
                plan = analyze(query, CONFIG)
                self.assertIn(expected, select_facts(body, plan))

    def test_parent_status_and_dates_apply_to_nested_facts(self):
        card = """## Decision

Date: 2026-10-18
Status: superseded

### Random seed

Random seed 4180.

## Current decision

### Random seed

Random seed 3180.
"""
        plan = analyze("What is the random seed for Maple Archive?", CONFIG)
        self.assertIn("3180", select_facts(card, plan))
        self.assertNotIn("4180", select_facts(card, plan))
        past = analyze("On October 10, 2026, what was the random seed for Maple Archive?", CONFIG)
        self.assertNotIn("4180", select_facts(card, past))

    def test_current_lookup_checks_explicit_validity_window(self):
        plan = analyze("What is the random seed for Maple Archive?", CONFIG)
        for field in (
            "Date: 2099-01-01",
            "Valid_from: 2099-01-01",
            "Valid until: 2000-01-01",
        ):
            with self.subTest(field=field):
                card = f"## Random seed\n\n{field}\nStatus: active\n\nRandom seed 7612."
                self.assertEqual(select_facts(card, plan), "")

    def test_ambiguous_or_unmatched_project_does_not_enable_typed_lookup(self):
        for query in (
            "What is the random seed for ExtraMaple Archive?",
            "For Maple Archive and Pine Archive, list the random seed.",
        ):
            with self.subTest(query=query):
                self.assertEqual(analyze(query, CONFIG).fact_requests, ())
