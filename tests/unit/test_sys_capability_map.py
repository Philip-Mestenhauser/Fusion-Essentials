"""Unit tests for ``sys_capability_map`` - the LIVE family index (breadth map).

No adsk.* (pure registry introspection). Patch get_tools to a known set of fake tools and assert: the
grouping by name-prefix family, summary/entry_tool/tool_count per family, the registry-derived fallback
for an UNMAPPED family, and that counts sum to the tool total (it's an index of the real registry).
"""

import json
from types import SimpleNamespace

from conftest import load_tool

cm = load_tool("sys_capability_map")


def _item(name):
    return SimpleNamespace(primitive=SimpleNamespace(name=name))


def _install(names):
    cm.get_tools = lambda: [_item(n) for n in names]


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestFamilyMap:
    def test_groups_by_prefix_with_summary_entry_and_count(self):
        _install(["cam_get", "cam_create_setup", "cam_generate",
                  "sketch_create", "sketch_constrain"])
        out = _payload(cm.handler())
        fams = {f["family"]: f for f in out["families"]}
        assert set(fams) == {"cam", "sketch"}
        assert fams["cam"]["tool_count"] == 3
        assert fams["cam"]["entry_tool"] == "cam_create_setup"     # the curated family fact
        assert fams["sketch"]["entry_tool"] == "sketch_create"
        assert fams["cam"]["summary"]                              # a factual line, present

    def test_counts_sum_to_tool_total(self):
        names = ["cam_get", "cam_generate", "model_extrude", "doc_get", "doc_save"]
        _install(names)
        out = _payload(cm.handler())
        assert out["tool_count"] == len(names)
        assert sum(f["tool_count"] for f in out["families"]) == len(names)
        assert out["family_count"] == 3                            # cam, model, doc

    def test_unmapped_family_falls_back_honestly(self):
        # a family with no curated entry -> entry derived (a *_get/_create if present, else first),
        # summary states only the fact, never invented advice.
        _install(["widget_frob", "widget_get", "widget_zap"])
        out = _payload(cm.handler())
        w = {f["family"]: f for f in out["families"]}["widget"]
        assert w["entry_tool"] == "widget_get"                     # derived: the _get
        assert w["tool_count"] == 3
        assert "widget" in w["summary"]

    def test_note_cross_links_to_find_tool(self):
        _install(["cam_get"])
        out = _payload(cm.handler())
        assert "sys_find_tool" in out["note"]                      # breadth <-> depth cross-link


class TestFamilyOf:
    def test_prefix_split(self):
        assert cm._family_of("cam_get") == "cam"
        assert cm._family_of("find_geometry") == "find"
        assert cm._family_of("workspace_orient") == "workspace"
