# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Unit tests for the doc generators (gen_manifest / gen_wiring).

test_generated_docs_current.py pins output FRESHNESS (the committed doc matches the generator);
these pin the generators' LOGIC (the generator matches the truth). A generator defect that lies
consistently sails through a freshness check, so the load-bearing transforms are pinned here on
synthetic input - above all gen_wiring's per-tool attribution, where sibling tools sharing a name
stem must never swap notes.
"""

import ast

import pytest

import gen_manifest
import gen_wiring


# ── gen_wiring: registration call-site attribution ───────────────────────────

class TestToolHandlerMap:
    def _map(self, src):
        return gen_wiring._tool_handler_map(ast.parse(src))

    def test_sibling_tools_sharing_a_stem_keep_their_own_handlers(self):
        src = (
            'tool_a = Tool.create_simple(name="doc_save", description=D)\n'
            'item_a = Item.create_tool_item(tool=tool_a, write="write", handler=save_handler)\n'
            'tool_b = Tool.create_simple(name="doc_save_as", description=D2)\n'
            'item_b = Item.create_tool_item(tool=tool_b, write="write", handler=save_as_handler)\n'
        )
        m = self._map(src)
        assert m == {"doc_save": "save_handler", "doc_save_as": "save_as_handler"}

    def test_chained_builder_calls_still_resolve(self):
        src = (
            'tool = (Tool.create_simple(name="view_screenshot", description=D)\n'
            '        .add_input_property("width", {})\n'
            '        .strict_schema())\n'
            'item = Item.create_tool_item(tool=tool, write="read", handler=handler)\n'
        )
        assert self._map(src) == {"view_screenshot": "handler"}

    def test_create_with_string_input_form_resolves(self):
        src = (
            'tool = Tool.create_with_string_input(name="param_set", description=D,\n'
            '                                     input_param_name="name")\n'
            'item = Item.create_tool_item(tool=tool, write="write", handler=set_handler)\n'
        )
        assert self._map(src) == {"param_set": "set_handler"}

    def test_registration_without_a_name_or_handler_is_absent(self):
        src = (
            'tool = Tool.create_simple(description=D)\n'
            'item = Item.create_tool_item(tool=tool, write="read", handler=handler)\n'
            'other = Item.create_tool_item(tool=unknown_var, write="read", handler=h2)\n'
        )
        assert self._map(src) == {}


# ── gen_manifest: family grouping + CLAUDE.md splice ──────────────────────────

class TestGenManifestFamilies:
    def test_first_matching_prefix_wins_and_leftovers_group_as_other(self):
        tools = [{"name": "model_extrude"}, {"name": "cam_get"}, {"name": "zzz_thing"}]
        fam = gen_manifest.families(tools)
        assert any(t["name"] == "model_extrude" for t in fam.get("model", []))
        assert any(t["name"] == "cam_get" for t in fam.get("cam", []))
        assert any(t["name"] == "zzz_thing" for t in fam.get("other", []))

    def test_catalog_escapes_pipes_in_kind_hints(self):
        data = {"kinds": [{"kind": "UnitField", "hint": "mm | cm | in selector", "summary": ""}],
                "tools": [{"name": "model_extrude"}], "helpers": []}
        out = gen_manifest.render_catalog(data)
        assert "mm \\| cm \\| in selector" in out          # an unescaped pipe would break the table


class TestSplice:
    # _splice is generic (path + begin/end markers); the same seam splices the root families census
    # and the tools/CLAUDE.md catalog. Exercised here on a temp file with the catalog markers.
    def _doc(self, tmp_path, body):
        p = tmp_path / "DOC.md"
        p.write_text(body, encoding="utf-8")
        return str(p)

    def test_splice_replaces_only_between_markers(self, tmp_path):
        b, e = gen_manifest._CAT_BEGIN, gen_manifest._CAT_END
        path = self._doc(tmp_path, "before\n" + b + "\nstale\n" + e + "\nafter\n")
        block = b + "\nfresh\n" + e
        assert gen_manifest._splice(path, b, e, block) is False       # it changed something
        text = open(path, encoding="utf-8").read()
        assert "fresh" in text and "stale" not in text
        assert text.startswith("before\n") and text.endswith("after\n")

    def test_check_mode_reports_stale_without_writing(self, tmp_path):
        b, e = gen_manifest._CAT_BEGIN, gen_manifest._CAT_END
        path = self._doc(tmp_path, b + "\nstale\n" + e)
        block = b + "\nfresh\n" + e
        assert gen_manifest._splice(path, b, e, block, check=True) is False
        assert "stale" in open(path, encoding="utf-8").read()          # untouched

    def test_current_content_reports_true(self, tmp_path):
        b, e = gen_manifest._CAT_BEGIN, gen_manifest._CAT_END
        block = b + "\ncurrent\n" + e
        path = self._doc(tmp_path, block)
        assert gen_manifest._splice(path, b, e, block, check=True) is True

    def test_missing_markers_raise_systemexit(self, tmp_path):
        path = self._doc(tmp_path, "no markers here\n")
        with pytest.raises(SystemExit):
            gen_manifest._splice(path, gen_manifest._CAT_BEGIN, gen_manifest._CAT_END, "block")
