"""Unit tests for the merged sketch_get read tool (chunk B of the refactor).

sketch_get (summary list) and sketch_get (one sketch's full structure) were collapsed into
ONE tool, sketch_get, switched by specificity: no sketch_name -> summary list; a sketch_name -> full
detail (delegated to the sketch_detail engine). The return is always about sketches; only the depth
changes. These tests pin the ROUTING — that the right engine is called for each case — without
needing a full fake design for both paths.
"""

from conftest import load_tool

sketches = load_tool("sketch_core")


class TestSketchGetRouting:
    def test_no_name_lists_summary(self, monkeypatch):
        called = {}

        def fake_summary():
            called["summary"] = True
            return {"isError": False}
        monkeypatch.setattr(sketches, "get_sketches_handler", fake_summary)
        # ensure detail is NOT used
        import sys
        res = sketches.sketch_get_handler(sketch_name="")
        assert called.get("summary") is True
        assert res["isError"] is False

    def test_name_delegates_to_detail_engine(self, monkeypatch):
        seen = {}

        class FakeDetail:
            @staticmethod
            def handler(sketch_name="", include_entities=False):
                seen["name"] = sketch_name
                seen["include_entities"] = include_entities
                return {"isError": False, "content": [{"type": "text", "text": "{}"}]}

        # the handler imports `from . import sketch_detail` lazily; install a stub module
        import sys
        sys.modules["mcpServer.tools.sketch_detail"] = FakeDetail
        monkeypatch.setitem(sys.modules, "mcpServer.tools.sketch_detail", FakeDetail)

        res = sketches.sketch_get_handler(sketch_name="Emblem", include_entities=True)
        assert seen.get("name") == "Emblem"     # routed to the detail engine with the name
        assert seen.get("include_entities") is True   # the zoom flag is threaded through
        assert res["isError"] is False

    def test_whitespace_name_treated_as_no_name(self, monkeypatch):
        called = {}

        def fake_summary():
            called["summary"] = True
            return {"isError": False}
        monkeypatch.setattr(sketches, "get_sketches_handler", fake_summary)
        sketches.sketch_get_handler(sketch_name="   ")
        assert called.get("summary") is True     # blank-ish name -> summary, not detail
