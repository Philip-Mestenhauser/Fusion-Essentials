"""Unit tests for ``set_sketch_text.py`` CREATE path — make new sketch text from scratch.

The edit path is covered via test_quoting (the quote helpers); these pin the new create=true branch:
target-sketch resolution, height/units scaling, and the guards (no sketch_name, bad height).
No live Fusion — fakes mimic Sketch.sketchTexts.createInput2 / add.
"""

import json
from conftest import load_tool

st = load_tool("sketch_set_text")


class FakeTextInput:
    def __init__(self, text, height):
        self.text = text
        self.height = height
        self.multiline = None
    def setAsMultiLine(self, p1, p2, ha, va, angle):
        self.multiline = (p1, p2)


class FakeText:
    def __init__(self, inp):
        self.inp = inp


class FakeTexts:
    def __init__(self):
        self.added = []
    def createInput2(self, text, height):
        return FakeTextInput(text, height)
    def add(self, inp):
        t = FakeText(inp); self.added.append(t); return t


class FakeSketch:
    def __init__(self, name):
        self.name = name
        self.sketchTexts = FakeTexts()


class FakeDesign:
    def __init__(self, sketches):
        self._sk = {s.name: s for s in sketches}
        self.rootComponent = type("R", (), {
            "sketches": type("SS", (), {"itemByName": staticmethod(lambda n: self._sk.get(n))})()
        })()


def _install(sketches):
    design = FakeDesign(sketches)
    st.app = type("A", (), {"activeProduct": design})()
    st._common.app = st.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
    ha = adsk.core.HorizontalAlignments; ha.LeftHorizontalAlignment = "L"
    va = adsk.core.VerticalAlignments; va.BottomVerticalAlignment = "B"
    return design


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestCreateText:
    def test_creates_text_in_named_sketch(self):
        s = FakeSketch("Nameplate")
        _install([s])
        out = _payload(st.handler(text="PART-007", sketch_name="Nameplate", create=True,
                                  height=8, x=2, y=3, units="mm"))
        assert out["created"] is True
        assert s.sketchTexts.added[-1].inp.text == "PART-007"
        # 8 mm height -> 0.8 cm to the API
        assert abs(s.sketchTexts.added[-1].inp.height - 0.8) < 1e-9

    def test_create_requires_sketch_name(self):
        _install([FakeSketch("S")])
        res = st.handler(text="X", create=True)
        assert res["isError"] is True and "sketch_name" in res["message"]

    def test_create_missing_sketch_errors(self):
        _install([FakeSketch("S")])
        res = st.handler(text="X", sketch_name="Ghost", create=True)
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_create_rejects_nonpositive_height(self):
        _install([FakeSketch("S")])
        res = st.handler(text="X", sketch_name="S", create=True, height=0)
        assert res["isError"] is True and "height" in res["message"]
