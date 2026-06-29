"""Unit tests for ``appearance_set.py`` — set a body/occurrence/component color.

The logic pinned here, no live Fusion: color parsing (#RRGGBB / RRGGBB / r,g,b, with the malformed
cases rejected), target resolution (body name, occurrence, component -> all bodies), the override idiom
(addByCopy a base appearance, set its ColorProperty.value to a Color, assign to .appearance), and the
guards (bad color, bad opacity, no design, missing target, no base appearance, component with no bodies).
Fakes capture the assignments so a regression to a wrong attribute fails here.
"""

import json

from conftest import load_tool

ap = load_tool("appearance_set")


# ── color parsing (pure) ───────────────────────────────────────────────────────

class TestParseColor:
    def test_hex_with_hash(self):
        assert ap._parse_color("#1E8E3E") == ((30, 142, 62), None)

    def test_hex_without_hash(self):
        assert ap._parse_color("FF0000") == ((255, 0, 0), None)

    def test_rgb_triplet(self):
        assert ap._parse_color("0, 128, 255") == ((0, 128, 255), None)

    def test_empty_rejected(self):
        rgb, err = ap._parse_color("")
        assert rgb is None and "color" in err.lower()

    def test_bad_hex_length_rejected(self):
        rgb, err = ap._parse_color("#FFF")
        assert rgb is None and "6-digit" in err

    def test_non_hex_rejected(self):
        rgb, err = ap._parse_color("#GGGGGG")
        assert rgb is None and "valid hex" in err

    def test_out_of_range_rgb_rejected(self):
        rgb, err = ap._parse_color("300,0,0")
        assert rgb is None and "0-255" in err

    def test_wrong_rgb_count_rejected(self):
        rgb, err = ap._parse_color("10,20")
        assert rgb is None and "r,g,b" in err

    def test_non_integer_rgb_rejected(self):
        rgb, err = ap._parse_color("10,x,30")
        assert rgb is None and "non-integer" in err

    def test_negative_component_rejected(self):
        rgb, err = ap._parse_color("-1,0,0")
        # '-1' parses as int -> caught by the 0-255 range check
        assert rgb is None and "0-255" in err


# ── fakes ──────────────────────────────────────────────────────────────────────

# The handler detects color props by type(p).__name__ == "ColorProperty" (mirrors the real API class
# name), so the fake's class must be literally named ColorProperty.
def FakeColorProperty():
    inst = type("ColorProperty", (), {})()
    inst.value = None  # the handler sets this to the Color
    return inst


class _OtherProperty:
    pass


class FakeProps:
    def __init__(self, items):
        self._items = items

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeAppearance:
    def __init__(self, name, color_props=1):
        self.name = name
        props = [FakeColorProperty() for _ in range(color_props)] + [_OtherProperty()]
        self.appearanceProperties = FakeProps(props)


class FakeAppearances:
    def __init__(self, existing=()):
        self._items = list(existing)
        self.copied = []

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def addByCopy(self, base, name):
        a = FakeAppearance(name)
        self.copied.append((base, name, a))
        return a


class FakeBody:
    def __init__(self, name):
        self.name = name
        self.appearance = None


class FakeBodies:
    def __init__(self, bodies):
        self._b = {b.name: b for b in bodies}
        self._list = bodies

    def itemByName(self, n):
        return self._b.get(n)

    @property
    def count(self):
        return len(self._list)

    def item(self, i):
        return self._list[i]


class FakeComponent:
    def __init__(self, name, bodies=()):
        self.name = name
        self.bRepBodies = FakeBodies(list(bodies))


class FakeOcc:
    def __init__(self, name, full_path=None, bodies=(), component=None):
        self.name = name
        self.fullPathName = full_path or name
        self.appearance = None
        self.bRepBodies = FakeBodies(list(bodies))
        self.component = component or FakeComponent(name + "_comp")


class FakeOccs:
    def __init__(self, occs):
        self._o = {o.name: o for o in occs}

    def itemByName(self, n):
        return self._o.get(n)


class FakeRoot:
    def __init__(self, name="Root", bodies=(), occurrences=()):
        self.name = name
        self.bRepBodies = FakeBodies(list(bodies))
        self.occurrences = FakeOccs(list(occurrences))
        self.allOccurrences = list(occurrences)


class FakeFace:
    """Stands in for adsk.fusion.BRepFace — has a settable .appearance, NO .name."""
    def __init__(self):
        self.appearance = None


class FakeDesign:
    def __init__(self, root, appearances, tokens=None):
        self.rootComponent = root
        self.appearances = appearances
        self._tokens = tokens or {}

    def findEntityByToken(self, t):
        e = self._tokens.get(t)
        return [e] if e is not None else []


def _install(root, existing_appearances=("Base",), tokens=None):
    apps = FakeAppearances([FakeAppearance(n) for n in existing_appearances])
    design = FakeDesign(root, apps, tokens)
    ap._common.design = lambda: design
    import adsk.core, adsk.fusion
    adsk.core.Color.create = staticmethod(lambda r, g, b, o: ("color", r, g, b, o))
    # isinstance checks in the handle path need these bound to the fakes
    adsk.fusion.BRepFace = FakeFace
    adsk.fusion.BRepBody = FakeBody
    # handle_token: the handler calls _inputs.handle_token(name) before findEntityByToken
    ap._inputs.handle_token = lambda s: s
    return design, apps


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── apply to a body / occurrence / component ───────────────────────────────────

class TestApply:
    def test_color_a_body_by_name(self):
        body = FakeBody("Body1")
        root = FakeRoot(bodies=[body])
        design, apps = _install(root)
        out = _payload(ap.handler(target="Body1", color="#1E8E3E"))
        assert out["applied"] is True
        assert out["color_hex"] == "#1E8E3E"
        assert out["kind"] == "body"
        # the body's appearance got the copied, colored appearance
        assert body.appearance is apps.copied[0][2]
        # and the color property was set to the created Color (0-255, opaque)
        cp = body.appearance.appearanceProperties.item(0)
        assert cp.value == ("color", 30, 142, 62, 255)

    def test_color_a_single_face_by_handle(self):
        # a find_geometry FACE handle colors just that one face (BRepFace.appearance), not the body
        face = FakeFace()
        h = "/v" + "F" * 70
        root = FakeRoot(bodies=[FakeBody("Body1")])
        design, apps = _install(root, tokens={h: face})
        out = _payload(ap.handler(target=h, color="#FF6D00"))
        assert out["applied"] is True
        assert out["kind"] == "face"
        # the override landed on the FACE
        assert face.appearance is apps.copied[0][2]
        cp = face.appearance.appearanceProperties.item(0)
        assert cp.value == ("color", 255, 109, 0, 255)
        # applied_to falls back to the description (a face has no .name)
        assert out["applied_to"] and "face" in out["applied_to"][0].lower()

    def test_body_handle_still_colors_the_body(self):
        # the handle path must still resolve a BODY (not only faces)
        body = FakeBody("Body1")
        h = "/v" + "B" * 70
        root = FakeRoot(bodies=[body])
        design, apps = _install(root, tokens={h: body})
        out = _payload(ap.handler(target=h, color="#000000"))
        assert out["kind"] == "body"
        assert body.appearance is apps.copied[0][2]

    def test_long_body_name_not_mistaken_for_handle(self):
        # PR-review #7: the old `len(name) > 60` heuristic mis-routed a 60+ char NAME into the handle
        # path. Resolution now tries _resolve_token_entity first (returns None for a non-token) and
        # falls through to the name lookup — so a long body name resolves by NAME.
        long_name = "Left-Outrigger-Pivot-Bracket-Weldment-Subassembly-Body-Number-Seven"
        assert len(long_name) > 60
        body = FakeBody(long_name)
        root = FakeRoot(bodies=[body])
        design, apps = _install(root, tokens={})              # NOT a token
        out = _payload(ap.handler(target=long_name, color="#101010"))
        assert out["kind"] == "body"
        assert body.appearance is apps.copied[0][2]           # the colored appearance landed on it

    def test_color_an_occurrence(self):
        occ = FakeOcc("Wheel:1")
        root = FakeRoot(occurrences=[occ])
        _install(root)
        out = _payload(ap.handler(target="Wheel:1", color="0,0,0"))
        assert out["kind"] == "occurrence"
        assert occ.appearance is not None

    def test_color_a_component_applies_to_all_bodies(self):
        b1, b2 = FakeBody("B1"), FakeBody("B2")
        comp = FakeComponent("Tire", bodies=[b1, b2])
        occ = FakeOcc("Tire:1", component=comp)
        root = FakeRoot(occurrences=[occ])
        _install(root)
        out = _payload(ap.handler(target="Tire", color="#FFFFFF"))
        assert out["kind"] == "component"
        assert b1.appearance is not None and b2.appearance is not None
        assert set(out["applied_to"]) == {"B1", "B2"}

    def test_opacity_passed_through(self):
        body = FakeBody("Body1")
        _install(FakeRoot(bodies=[body]))
        out = _payload(ap.handler(target="Body1", color="#102030", opacity=128))
        assert out["opacity"] == 128
        assert body.appearance.appearanceProperties.item(0).value == ("color", 16, 32, 48, 128)

    def test_default_appearance_name_from_color(self):
        body = FakeBody("Body1")
        _install(FakeRoot(bodies=[body]))
        out = _payload(ap.handler(target="Body1", color="#1E8E3E"))
        assert out["appearance"] == "AgentColor_1E8E3E"


# ── guards ─────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_bad_color_errors_before_touching_design(self):
        res = ap.handler(target="Body1", color="nope")
        assert res["isError"] is True and "hex" in res["message"].lower()

    def test_bad_opacity_errors(self):
        _install(FakeRoot(bodies=[FakeBody("Body1")]))
        res = ap.handler(target="Body1", color="#000000", opacity=999)
        assert res["isError"] is True and "0-255" in res["message"]

    def test_no_active_design_errors(self):
        ap._common.design = lambda: None
        res = ap.handler(target="Body1", color="#000000")
        assert res["isError"] is True and "no active design" in res["message"].lower()

    def test_missing_target_errors(self):
        _install(FakeRoot(bodies=[FakeBody("Body1")]))
        res = ap.handler(target="Ghost", color="#000000")
        assert res["isError"] is True and "matching 'ghost'" in res["message"].lower()

    def test_no_base_appearance_errors(self):
        # design has no appearances AND no material libraries with any -> honest failure
        root = FakeRoot(bodies=[FakeBody("Body1")])
        _install(root, existing_appearances=())
        import adsk.core
        adsk.core.Application.get().materialLibraries = FakeProps([])  # no libs
        res = ap.handler(target="Body1", color="#000000")
        assert res["isError"] is True and "base appearance" in res["message"].lower()

    def test_component_with_no_bodies_errors(self):
        comp = FakeComponent("Empty", bodies=[])
        occ = FakeOcc("Empty:1", component=comp)
        _install(FakeRoot(occurrences=[occ]))
        res = ap.handler(target="Empty", color="#000000")
        assert res["isError"] is True and "no bodies" in res["message"].lower()

    def test_handle_resolving_to_neither_face_nor_body_errors(self):
        h = "/v" + "Q" * 70
        root = FakeRoot(bodies=[FakeBody("Body1")])
        _install(root, tokens={h: object()})   # resolves to a non-face/non-body
        res = ap.handler(target=h, color="#000000")
        assert res["isError"] is True and "matching" in res["message"].lower()

    def test_no_editable_color_property_errors(self):
        # the COPIED appearance exposes no ColorProperty -> honest failure, not a silent no-op
        body = FakeBody("Body1")
        root = FakeRoot(bodies=[body])
        design, apps = _install(root)

        # make addByCopy return a propertyless appearance (no ColorProperty to set)
        def copy_no_color(base, name):
            a = FakeAppearance(name, color_props=0)
            a.appearanceProperties = FakeProps([_OtherProperty()])
            apps.copied.append((base, name, a))
            return a
        apps.addByCopy = copy_no_color

        res = ap.handler(target="Body1", color="#000000")
        assert res["isError"] is True and "color property" in res["message"].lower()


# ── target resolution: root component name + base-appearance fallback ─────────

class TestResolveExtra:
    def test_root_component_by_name_resolves_to_root(self):
        root = FakeRoot(name="Assembly", bodies=[FakeBody("B")])
        design, apps = _install(root)
        out = _payload(ap.handler(target="Assembly", color="#010203"))
        # matched the root component by its name -> applies to its bodies
        assert out["kind"] == "component"
        assert out["applied_to"] == ["B"]

    def test_empty_target_is_whole_design_root(self):
        root = FakeRoot(name="Root", bodies=[FakeBody("B")])
        _install(root)
        out = _payload(ap.handler(target="", color="#010203"))
        assert out["kind"] == "component"
        assert "root component" in out["target"].lower()


class TestBaseAppearanceFallback:
    def test_falls_back_to_material_library_when_design_has_none(self):
        # design has NO appearances; a material library exposes one -> that one is the base
        body = FakeBody("Body1")
        root = FakeRoot(bodies=[body])
        apps = FakeAppearances([])                      # empty design appearances
        design = FakeDesign(root, apps)
        ap._common.design = lambda: design
        import adsk.core, adsk.fusion
        adsk.core.Color.create = staticmethod(lambda r, g, b, o: ("color", r, g, b, o))
        adsk.fusion.BRepFace = FakeFace
        adsk.fusion.BRepBody = FakeBody
        ap._inputs.handle_token = lambda s: s

        lib_appr = FakeAppearance("LibBase")
        lib = type("Lib", (), {"appearances": FakeProps([lib_appr])})()
        adsk.core.Application.get().materialLibraries = FakeProps([lib])

        out = _payload(ap.handler(target="Body1", color="#0A0B0C"))
        assert out["applied"] is True
        # the copy was made from the library appearance
        assert apps.copied[0][0] is lib_appr
