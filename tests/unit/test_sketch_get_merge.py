"""Unit tests for sketch_get's routing between its two depths, and the summary's design-wide walk.

sketch_get is ONE tool switched by specificity: no sketch_name -> summary list; a sketch_name ->
full detail (delegated to the _sketch_detail engine). The return is always about sketches; only the
depth changes. The routing tests pin BOTH halves of each branch - the engine that must run, and the
other one, monkeypatched to a sentinel that fails the test if it is invoked at all (routing to both
depths costs a caller the heavy read they did not ask for, and reads green on a
'the right one ran' assertion alone). The walk tests pin that the summary reaches sketches in
SUB-components and tags each row with its owner.
"""

import json
import sys
import types

from conftest import load_tool

sketches = load_tool("sketch_core")


class _Coll:
    def __init__(self, items):
        self._items = list(items)
    @property
    def count(self):
        return len(self._items)
    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


def _never(what):
    """A stand-in for the depth that must NOT run on this call: invoked at all, it fails the test
    by name instead of quietly returning a result the assertions would never look at."""
    def _refuse(*args, **kwargs):
        raise AssertionError(f"{what} was invoked - sketch_get routed to the wrong depth")
    return _refuse


def _no_detail_engine(monkeypatch):
    """Install a _sketch_detail engine whose handler refuses to be called. Same module-table seam
    the delegation test installs its stub through, so it intercepts the same lookup."""
    monkeypatch.setitem(sys.modules, "mcpServer.tools._sketch_detail",
                        types.SimpleNamespace(handler=_never("the _sketch_detail engine")))


class TestSketchGetRouting:
    def test_no_name_lists_summary(self, monkeypatch):
        called = {}

        def fake_summary():
            called["summary"] = True
            return {"isError": False}
        monkeypatch.setattr(sketches, "get_sketches_handler", fake_summary)
        _no_detail_engine(monkeypatch)          # the detail slice must NOT be reached
        res = sketches.sketch_get_handler(sketch_name="")
        assert called.get("summary") is True
        assert res["isError"] is False

    def test_name_delegates_to_detail_engine(self, monkeypatch):
        seen = {}

        class FakeDetail:
            @staticmethod
            def handler(sketch_name="", include_entities=False, units="mm"):
                seen["name"] = sketch_name
                seen["include_entities"] = include_entities
                seen["units"] = units
                return {"isError": False, "content": [{"type": "text", "text": "{}"}]}

        # The handler resolves the engine by NAME in the module table on every call, so installing
        # the stub there routes it - whatever else has already imported the real engine. Binding the
        # engine through the package attribute instead makes this test pass or fail on load order.
        monkeypatch.setitem(sys.modules, "mcpServer.tools._sketch_detail", FakeDetail)
        # the reciprocal negative: a named sketch must not ALSO run the design-wide summary walk
        monkeypatch.setattr(sketches, "get_sketches_handler", _never("the summary walk"))

        res = sketches.sketch_get_handler(sketch_name="Emblem", include_entities=True, units="in")
        assert seen.get("name") == "Emblem"     # routed to the detail engine with the name
        assert seen.get("include_entities") is True   # the zoom flag is threaded through
        assert seen.get("units") == "in"        # the units param is threaded through too
        assert res["isError"] is False

    def test_whitespace_name_treated_as_no_name(self, monkeypatch):
        called = {}

        def fake_summary():
            called["summary"] = True
            return {"isError": False}
        monkeypatch.setattr(sketches, "get_sketches_handler", fake_summary)
        _no_detail_engine(monkeypatch)          # a blank-ish name is not a name to look up
        sketches.sketch_get_handler(sketch_name="   ")
        assert called.get("summary") is True     # blank-ish name -> summary, not detail


class TestSketchSummaryWalk:
    @staticmethod
    def _comp(name, sketch_names=()):
        # Like the live Component: no allComponents attribute (that collection is a Design
        # property), so reading it here raises AttributeError exactly as adsk does.
        sks = [type("Sk", (), {"name": n})() for n in sketch_names]
        return type("C", (), {"name": name, "sketches": _Coll(sks)})()

    def test_lists_sub_component_sketches_tagged_with_their_owner(self, monkeypatch):
        # A multi-part doc keeps each part's sketch in its own component; a walk that only reaches
        # the root reports sketch_count 0 for the whole design (observed live on a 3-component doc).
        root = self._comp("Root")
        frame = self._comp("Frame", ["FrameSketch"])
        ring = self._comp("OuterRing", ["OuterRingSketch"])
        d = type("D", (), {"rootComponent": root, "activeComponent": root,
                           "allComponents": _Coll([root, frame, ring])})()
        monkeypatch.setattr(sketches._common, "design", lambda: d)
        res = sketches.get_sketches_handler()
        payload = json.loads(res["content"][0]["text"])
        assert payload["sketch_count"] == 2
        assert {(r["name"], r["component"]) for r in payload["sketches"]} == {
            ("FrameSketch", "Frame"), ("OuterRingSketch", "OuterRing")}
