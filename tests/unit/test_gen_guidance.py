# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Tests for the canonical guidance data and the generator that renders the Claude skill from it.

Two halves. The SHIPPED half asserts the committed JSON against the live tool registry and the
committed skill against the renderer, so the client skill cannot drift from the data and a proof
step cannot point a client at a tool the server does not register. The SYNTHETIC half drives each
structural check with data built to break exactly it - duplicate ids, a rule filed under the wrong
section, a scenario the enum does not know, and each size cap at its exact boundary - because a
cap that never fires reads identically to one that cannot.

Whether a rule's WORDING is honest (that the named tool really shows what 'observe' claims) is a
reading judgment and stays in review; these tests check shape, routing, and size.
"""

import copy
import json
import re

import pytest

import gen_guidance
from conftest import load_tool, register_all_tools


@pytest.fixture(scope="module")
def tool_names():
    """Every registered tool name - the same inventory the wire-surface tests read."""
    return frozenset(item.get_name() for item in register_all_tools())


@pytest.fixture(scope="module")
def shipped():
    return gen_guidance.load()


# ── synthetic data: a minimal valid document, then one break per check ─────────

_SCENARIOS = ["alpha", "beta"]
_NAMES = frozenset({"doc_get", "design_get"})


def _rule(rule_id, section_id, scenarios=None, **over):
    rule = {
        "id": rule_id,
        "section": section_id,
        "scenarios": sorted(scenarios if scenarios is not None else _SCENARIOS),
        "when": "a case arises",
        "do": "take the action",
        "except": "the other case",
        "prove": [{"tool": "doc_get", "observe": "what the document reports"}],
    }
    rule.update(over)
    return rule


def _doc(**over):
    """A minimal document that validates: one rule per section, the kernel universal."""
    doc = {
        "guidance_id": "probe",
        "name": "probe",
        "title": "Probe",
        "description": "A probe document.",
        "summary": "A probe.",
        "scenarios": list(_SCENARIOS),
        "sections": [
            {
                "id": section_id,
                "title": section_id.capitalize(),
                "rules": [_rule(
                    f"{section_id}-rule", section_id,
                    scenarios=_SCENARIOS if section_id == gen_guidance.KERNEL else ["alpha"])],
            }
            for section_id in gen_guidance.SECTION_IDS
        ],
    }
    doc.update(over)
    return doc


def _section(doc, section_id):
    return doc["sections"][gen_guidance.SECTION_IDS.index(section_id)]


def _pad(doc, section_id, measure, target):
    """Grow one rule's 'do' by single words until `measure(doc)` is exactly `target`.

    Each pad word adds exactly one rendered word, which is what lets a cap be probed AT its
    boundary and one word over it rather than somewhere past it.
    """
    rule = _section(doc, section_id)["rules"][0]
    while measure(doc) < target:
        rule["do"] += " pad"
    assert measure(doc) == target
    return doc


def _kernel_words(doc):
    return gen_guidance.word_count(gen_guidance.section_text(doc, gen_guidance.KERNEL))


def _body_words(doc):
    return gen_guidance.word_count(gen_guidance.body(doc))


def _problems(doc, names=_NAMES):
    return gen_guidance.validate(doc, names)


def _one(problems, needle):
    hits = [p for p in problems if needle in p]
    assert hits, f"no problem mentioning {needle!r} in {problems}"
    return hits


# ── the shipped package ───────────────────────────────────────────────────────

class TestShippedGuidance:
    def test_the_canonical_json_validates_against_the_live_registry(self, shipped, tool_names):
        assert gen_guidance.validate(shipped, tool_names) == []

    def test_the_synthetic_baseline_is_itself_valid(self):
        # every bite below is one edit away from this document; a baseline that already failed
        # would make each of them pass for the wrong reason.
        assert _problems(_doc()) == []

    def test_every_prove_step_names_a_registered_tool(self, shipped, tool_names):
        cited = sorted({step["tool"] for rule in gen_guidance.rules(shipped)
                        for step in rule["prove"]})
        assert cited, "the guidance names no tool at all"
        assert not [name for name in cited if name not in tool_names]

    def test_the_committed_skill_is_exactly_what_the_generator_renders(self, shipped):
        with open(gen_guidance.SKILL_PATH, encoding="utf-8") as fh:
            committed = fh.read()
        assert committed == gen_guidance.render(shipped)

    def test_the_skill_keeps_the_frontmatter_shape_its_readers_parse(self, shipped):
        # the eval harness appends a skill's BODY by splitting on this exact frontmatter block,
        # and the client loader keys the skill by the name inside it.
        rendered = gen_guidance.render(shipped)
        match = re.match(r"^---\n.*?\n---\n+", rendered, re.S)
        assert match, "the render lost the frontmatter block"
        assert f"name: {shipped['name']}\n" in match.group(0)
        assert rendered[match.end():] == gen_guidance.body(shipped)

    def test_the_sections_are_the_canonical_seven_in_order(self, shipped):
        assert [s["id"] for s in shipped["sections"]] == list(gen_guidance.SECTION_IDS)

    def test_every_rule_is_a_member_of_exactly_one_section(self, shipped):
        ids = [rule["id"] for rule in gen_guidance.rules(shipped)]
        assert len(ids) == len(set(ids))
        for sec in shipped["sections"]:
            assert all(rule["section"] == sec["id"] for rule in sec["rules"])

    def test_the_kernel_is_within_its_rule_and_word_caps(self, shipped):
        kernel = _section(shipped, gen_guidance.KERNEL)
        assert len(kernel["rules"]) <= gen_guidance.KERNEL_MAX_RULES
        assert _kernel_words(shipped) <= gen_guidance.KERNEL_MAX_WORDS

    def test_the_whole_skill_is_within_its_word_cap(self, shipped):
        assert _body_words(shipped) <= gen_guidance.SKILL_MAX_WORDS

    def test_no_section_carries_more_than_one_example(self, shipped):
        for sec in shipped["sections"]:
            carried = [r["id"] for r in sec["rules"] if r.get("example")]
            assert len(carried) <= gen_guidance.MAX_EXAMPLES_PER_SECTION, carried

    def test_a_deterministic_safety_rule_declares_its_kind(self, shipped):
        kinds = {rule["id"]: rule.get("kind") for rule in gen_guidance.rules(shipped)}
        assert kinds["verify-the-write"] == gen_guidance.SAFETY_INVARIANT
        assert all(k in (None, gen_guidance.SAFETY_INVARIANT) for k in kinds.values())


# ── rendering ─────────────────────────────────────────────────────────────────

class TestRender:
    def test_a_rule_renders_its_four_clauses_and_its_proof_steps(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["prove"] = [
            {"tool": "doc_get", "observe": "the save state"},
            {"tool": "design_get", "observe": "the timeline"},
        ]
        text = gen_guidance.section_text(doc, "plan")
        assert "When a case arises: take the action. Except the other case." in text
        assert "Prove `doc_get`: the save state; `design_get`: the timeline." in text

    def test_an_example_renders_and_its_absence_leaves_no_line(self):
        with_example = _doc()
        _section(with_example, "plan")["rules"][0]["example"] = "a nut on a plain shaft"
        assert "Example: a nut on a plain shaft." in gen_guidance.section_text(with_example, "plan")
        assert "Example:" not in gen_guidance.section_text(_doc(), "plan")

    def test_a_safety_invariant_is_marked_in_the_render(self):
        doc = _doc()
        _section(doc, "finish")["rules"][0]["kind"] = gen_guidance.SAFETY_INVARIANT
        assert "**finish-rule** (safety invariant)" in gen_guidance.section_text(doc, "finish")
        assert "(safety invariant)" not in gen_guidance.section_text(_doc(), "finish")

    def test_the_body_carries_every_section_in_the_canonical_order(self):
        text = gen_guidance.body(_doc())
        positions = [text.index(f"## {s.capitalize()}") for s in gen_guidance.SECTION_IDS]
        assert positions == sorted(positions)


# ── each structural check, driven by data built to break it ───────────────────

class TestValidationBites:
    def test_an_unknown_prove_tool_fails_against_the_live_registry(self, shipped, tool_names):
        doc = copy.deepcopy(shipped)
        doc["sections"][0]["rules"][0]["prove"][0]["tool"] = "doc_get_everything"
        _one(gen_guidance.validate(doc, tool_names), "doc_get_everything")

    def test_a_prove_step_with_no_observation_is_reported(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["prove"] = [{"tool": "doc_get", "observe": "  "}]
        _one(_problems(doc), "needs a non-empty 'observe'")

    def test_a_duplicate_rule_id_is_reported_naming_both_sections(self):
        doc = _doc()
        _section(doc, "model")["rules"][0]["id"] = "plan-rule"
        _section(doc, "model")["rules"][0]["section"] = "model"
        hits = _one(_problems(doc), "duplicate rule id 'plan-rule'")
        assert "'plan'" in hits[0] and "'model'" in hits[0]

    def test_a_rule_filed_under_the_wrong_section_is_reported(self):
        doc = _doc()
        _section(doc, "sketch")["rules"][0]["section"] = "model"
        _one(_problems(doc), "declares section 'model' but sits in 'sketch'")

    def test_sections_out_of_order_are_reported(self):
        doc = _doc()
        doc["sections"][1], doc["sections"][2] = doc["sections"][2], doc["sections"][1]
        _one(_problems(doc), "sections must be exactly")

    def test_a_missing_required_field_is_reported(self):
        for field in gen_guidance.RULE_FIELDS:
            doc = _doc()
            del _section(doc, "plan")["rules"][0][field]
            _one(_problems(doc), f"is missing '{field}'")

    def test_an_unknown_scenario_is_reported(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["scenarios"] = ["alpha", "gamma"]
        _one(_problems(doc), "unknown scenario(s) ['gamma']")

    def test_unsorted_or_duplicated_rule_scenarios_are_reported(self):
        # the needle names the RULE: the document-level enum carries a message this one is a
        # prefix of, so a looser needle would pass on either branch firing.
        doc = _doc()
        _section(doc, "plan")["rules"][0]["scenarios"] = ["beta", "alpha"]
        _one(_problems(doc), "rule 'plan-rule' 'scenarios' must be sorted")
        doc = _doc()
        _section(doc, "plan")["rules"][0]["scenarios"] = ["alpha", "alpha"]
        _one(_problems(doc), "rule 'plan-rule' 'scenarios' must be sorted")

    def test_a_rule_with_an_empty_scenario_list_is_reported(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["scenarios"] = []
        _one(_problems(doc), "rule 'plan-rule' needs a non-empty 'scenarios' list")

    def test_a_scenario_no_rule_claims_is_reported(self):
        doc = _doc(scenarios=["alpha", "beta", "gamma"])
        _one(_problems(doc), "scenario 'gamma' is declared but no rule applies to it")

    def test_a_kernel_rule_that_is_not_universal_is_reported(self):
        doc = _doc()
        _section(doc, gen_guidance.KERNEL)["rules"][0]["scenarios"] = ["alpha"]
        _one(_problems(doc), "must declare every scenario")

    def test_a_kernel_rule_carrying_an_example_is_reported(self):
        doc = _doc()
        _section(doc, gen_guidance.KERNEL)["rules"][0]["example"] = "a worked case"
        _one(_problems(doc), "carries an example")

    def test_an_unknown_kind_is_reported(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["kind"] = "best_practice"
        _one(_problems(doc), "declares kind 'best_practice'")

    def test_a_non_ascii_string_is_reported_with_its_path(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["do"] = "tilt the face 5°"
        _one(_problems(doc), "guidance.sections[1].rules[0].do carries non-ASCII")

    def test_a_bad_rule_id_is_reported(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["id"] = "Plan_Rule"
        _one(_problems(doc), "id must be lowercase letters, digits and '-'")

    def test_an_empty_when_do_or_except_is_reported(self):
        # a present-but-blank clause renders as a sentence with a hole in it, which the
        # missing-key check never sees.
        for field in ("when", "do", "except"):
            doc = _doc()
            _section(doc, "plan")["rules"][0][field] = "   "
            _one(_problems(doc), f"rule 'plan-rule' '{field}' must be a non-empty string")

    def test_a_rule_with_no_prove_step_is_reported(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["prove"] = []
        _one(_problems(doc), "rule 'plan-rule' needs at least one 'prove' step")

    def test_a_prove_step_that_is_not_an_object_is_reported(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["prove"] = [["doc_get", "what changed"]]
        _one(_problems(doc), "prove step must carry 'tool' and 'observe'")

    def test_an_empty_example_is_reported(self):
        doc = _doc()
        _section(doc, "plan")["rules"][0]["example"] = "  "
        _one(_problems(doc), "'example' must be a non-empty string when present")


class TestDocumentLevelBites:
    """The checks above the rule loop: the header fields, the scenario enum itself, and each
    section's own shape."""

    def test_each_blank_top_level_field_is_reported(self):
        for key in ("guidance_id", "name", "title", "description", "summary"):
            _one(_problems(_doc(**{key: "   "})), f"top-level '{key}' must be a non-empty string")

    def test_a_document_declaring_no_scenario_is_reported(self):
        _one(_problems(_doc(scenarios=[])), "'scenarios' must declare at least one scenario")

    def test_a_blank_scenario_id_is_reported(self):
        _one(_problems(_doc(scenarios=["alpha", "  "])),
             "every entry in 'scenarios' must be a non-empty id string")

    def test_the_scenario_enum_must_be_sorted_and_free_of_duplicates(self):
        _one(_problems(_doc(scenarios=["beta", "alpha"])), "so routing is deterministic")
        _one(_problems(_doc(scenarios=["alpha", "alpha", "beta"])), "so routing is deterministic")

    def test_a_section_without_a_title_is_reported(self):
        doc = _doc()
        _section(doc, "sketch")["title"] = "  "
        _one(_problems(doc), "section 'sketch' needs a title")

    def test_a_section_with_no_rules_is_reported(self):
        doc = _doc()
        _section(doc, "model")["rules"] = []
        _one(_problems(doc), "section 'model' has no rules")


class TestSizeCapsBiteAtTheirBoundary:
    """A cap that fires one word early or one word late is the same defect as one that never
    fires, so each is probed AT the limit and one past it."""

    def _kernel_of(self, count):
        return [_rule(f"kernel-rule-{i}", gen_guidance.KERNEL) for i in range(count)]

    def _plan_of(self, count):
        return [_rule(f"plan-rule-{i}", "plan", scenarios=["alpha"]) for i in range(count)]

    def test_the_kernel_rule_count_cap(self):
        at_cap = _doc()
        _section(at_cap, gen_guidance.KERNEL)["rules"] = self._kernel_of(
            gen_guidance.KERNEL_MAX_RULES)
        assert _problems(at_cap) == []
        over = _doc()
        _section(over, gen_guidance.KERNEL)["rules"] = self._kernel_of(
            gen_guidance.KERNEL_MAX_RULES + 1)
        _one(_problems(over), f"the kernel holds {gen_guidance.KERNEL_MAX_RULES + 1} rules")

    def test_the_kernel_word_cap(self):
        at_cap = _pad(_doc(), gen_guidance.KERNEL, _kernel_words, gen_guidance.KERNEL_MAX_WORDS)
        assert _problems(at_cap) == []
        over = _pad(_doc(), gen_guidance.KERNEL, _kernel_words, gen_guidance.KERNEL_MAX_WORDS + 1)
        _one(_problems(over), f"the kernel renders {gen_guidance.KERNEL_MAX_WORDS + 1} words")

    def test_the_whole_skill_word_cap(self):
        at_cap = _pad(_doc(), "plan", _body_words, gen_guidance.SKILL_MAX_WORDS)
        assert _problems(at_cap) == []
        over = _pad(_doc(), "plan", _body_words, gen_guidance.SKILL_MAX_WORDS + 1)
        _one(_problems(over), f"the skill renders {gen_guidance.SKILL_MAX_WORDS + 1} words")

    def test_the_per_section_rule_cap(self):
        # the cap is on ANY section, not just the kernel: 'plan' carries the probe so the kernel's
        # own (lower) rule cap cannot be what fires.
        at_cap = _doc()
        _section(at_cap, "plan")["rules"] = self._plan_of(gen_guidance.MAX_SECTION_RULES)
        assert _problems(at_cap) == []
        over = _doc()
        _section(over, "plan")["rules"] = self._plan_of(gen_guidance.MAX_SECTION_RULES + 1)
        _one(_problems(over),
             f"section 'plan' holds {gen_guidance.MAX_SECTION_RULES + 1} rules")

    def test_the_examples_per_section_cap(self):
        at_cap = _doc()
        plan = _section(at_cap, "plan")
        plan["rules"] = [_rule("plan-rule", "plan", scenarios=["alpha"], example="one case"),
                         _rule("plan-rule-two", "plan", scenarios=["alpha"])]
        assert _problems(at_cap) == []
        over = copy.deepcopy(at_cap)
        _section(over, "plan")["rules"][1]["example"] = "a second case"
        _one(_problems(over), "carries 2 examples")


class TestTheSectionCapIsOneNumberOnBothSides:
    """The authoring gate and the serving cap are the same number, asserted by driving BOTH: the
    largest section validate accepts comes back whole from sys_get_guidance, and the first section
    it refuses is the first one that truncates. A gate looser than the cap would ship rules no
    section read answers with; a gate tighter than it would refuse a document the tool serves fine."""

    def _plan_rules(self, count):
        return [_rule(f"plan-rule-{i}", "plan", scenarios=["alpha"]) for i in range(count)]

    def _served(self, monkeypatch, doc):
        tool = load_tool("sys_get_guidance")
        monkeypatch.setattr(tool.loader, "load", lambda path=None: (doc, "0" * 64))
        result = tool.handler(section="plan")
        assert result["isError"] is False, result
        return json.loads(result["content"][0]["text"])

    def test_the_largest_section_the_gate_accepts_is_served_whole(self, monkeypatch):
        doc = _doc()
        _section(doc, "plan")["rules"] = self._plan_rules(gen_guidance.MAX_SECTION_RULES)
        assert _problems(doc) == []
        out = self._served(monkeypatch, doc)
        assert out["rule_count"] == gen_guidance.MAX_SECTION_RULES
        assert "truncated" not in out

    def test_the_first_section_the_gate_refuses_is_the_first_one_that_truncates(self, monkeypatch):
        doc = _doc()
        _section(doc, "plan")["rules"] = self._plan_rules(gen_guidance.MAX_SECTION_RULES + 1)
        _one(_problems(doc), "section 'plan' holds")
        out = self._served(monkeypatch, doc)
        assert out["truncated"] is True
        assert out["rule_total"] == gen_guidance.MAX_SECTION_RULES + 1


# ── scenario routing ──────────────────────────────────────────────────────────

# Rules that CANNOT apply to a scenario, one entry per declared scenario: a mechanism practice has
# nothing to say about a single part, an audit of an imported body authors no sketch, pattern or
# configuration, a fixed mechanism is one built assembly rather than a variant table, and a
# configurable template is authored as configurations rather than as a mechanism to drive. A rule
# that declared one of these would route guidance into a case it does not fit, which is a data
# defect no prose check could see. Each entry is a judgment about applicability, so it names the
# rules that must never route here - not every rule that happens not to today.
_INAPPLICABLE = {
    "configurable_template": ("connected-reference-path", "exercise-the-mechanism"),
    "fixed_mechanism": ("variants-are-configurations",),
    "simple_part": ("connected-reference-path", "exercise-the-mechanism",
                    "variants-are-configurations"),
    "parametric_family": ("connected-reference-path", "exercise-the-mechanism"),
    "floating_mechanism": ("connected-reference-path",),
    "imported_audit": ("couple-what-moves-together", "constraints-carry-relationship",
                       "pattern-only-identical-intent", "let-the-process-shape-the-part",
                       "name-parts-for-what-they-are", "variants-are-configurations"),
}


class TestScenarioRouting:
    def test_every_declared_scenario_routes_to_the_whole_kernel(self, shipped):
        kernel_ids = {r["id"] for r in _section(shipped, gen_guidance.KERNEL)["rules"]}
        for scenario in gen_guidance.scenario_ids(shipped):
            reached = {r["id"] for r in gen_guidance.rules_for(shipped, scenario)}
            assert kernel_ids <= reached, scenario

    def test_no_rule_reaches_a_scenario_it_cannot_apply_to(self, shipped):
        for scenario, excluded in _INAPPLICABLE.items():
            reached = {r["id"] for r in gen_guidance.rules_for(shipped, scenario)}
            assert reached, f"{scenario} routes to nothing"
            assert not reached & set(excluded), scenario

    def test_the_exclusions_name_rules_that_exist(self, shipped):
        known = {r["id"] for r in gen_guidance.rules(shipped)}
        named = {rid for ids in _INAPPLICABLE.values() for rid in ids}
        assert named <= known, sorted(named - known)

    def test_every_declared_scenario_carries_a_negative_oracle(self, shipped):
        # EQUALITY, not containment: a scenario with no entry has no negative oracle at all, so
        # handing it a rule it cannot apply to leaves validate clean, the render byte-identical and
        # the suite green - the routing would be silently mutable.
        assert set(_INAPPLICABLE) == set(gen_guidance.scenario_ids(shipped))

    def test_routing_follows_the_declaration_and_nothing_else(self):
        doc = _doc()
        assert [r["id"] for r in gen_guidance.rules_for(doc, "beta")] == ["kernel-rule"]
        _section(doc, "model")["rules"][0]["scenarios"] = ["alpha", "beta"]
        assert [r["id"] for r in gen_guidance.rules_for(doc, "beta")] == ["kernel-rule", "model-rule"]

    def test_an_undeclared_scenario_routes_to_nothing(self):
        assert gen_guidance.rules_for(_doc(), "gamma") == []


# ── the generator's own --check contract ──────────────────────────────────────

class TestCheckDetectsAStaleSkill:
    def _run(self, monkeypatch, argv):
        monkeypatch.setattr(gen_guidance.sys, "argv", ["gen_guidance.py"] + argv)
        return gen_guidance.main()

    def test_check_passes_on_the_committed_skill(self, monkeypatch, capsys):
        assert self._run(monkeypatch, ["--check"]) == 0
        assert "up to date" in capsys.readouterr().out

    def test_check_reports_a_stale_skill_and_writes_nothing(self, monkeypatch, tmp_path, capsys):
        stale = tmp_path / "SKILL.md"
        stale.write_text("---\nname: parametric-cad-design\n---\n\n# Stale\n", encoding="utf-8")
        monkeypatch.setattr(gen_guidance, "SKILL_PATH", str(stale))
        assert self._run(monkeypatch, ["--check"]) == 1
        assert "Stale" in capsys.readouterr().err
        assert stale.read_text(encoding="utf-8").endswith("# Stale\n")

    def test_check_reports_a_missing_skill(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gen_guidance, "SKILL_PATH", str(tmp_path / "absent.md"))
        assert self._run(monkeypatch, ["--check"]) == 1

    def test_invalid_data_fails_before_anything_is_written(self, monkeypatch, tmp_path, capsys):
        target = tmp_path / "SKILL.md"
        monkeypatch.setattr(gen_guidance, "SKILL_PATH", str(target))
        broken = _doc()
        _section(broken, "plan")["rules"][0]["prove"][0]["tool"] = "no_such_tool"
        monkeypatch.setattr(gen_guidance, "load", lambda *a, **k: broken)
        assert self._run(monkeypatch, []) == 1
        assert "no_such_tool" in capsys.readouterr().err
        assert not target.exists()

    def test_generating_writes_the_render_and_then_checks_clean(self, monkeypatch, tmp_path):
        target = tmp_path / "SKILL.md"
        monkeypatch.setattr(gen_guidance, "SKILL_PATH", str(target))
        assert self._run(monkeypatch, []) == 0
        assert target.read_text(encoding="utf-8") == gen_guidance.render(gen_guidance.load())
        assert self._run(monkeypatch, ["--check"]) == 0
