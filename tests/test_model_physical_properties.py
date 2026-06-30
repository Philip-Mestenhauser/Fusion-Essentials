"""Unit tests for ``model_physical_properties`` — mass/CoM/inertia/principal/gyration.

The Fusion API is mocked; what we pin is the tool's OWN logic: the guards (bad units/accuracy, no
design, unresolvable target), the accuracy-name -> enum mapping, and — the real bug surface — the unit
SCALING of each quantity out of the API's centimetre/kg*cm^2 basis (volume by f^3, area by f^2, CoM by
f, inertia by f^2, radius of gyration by f), plus the unpacking of the API's [ok, *values] list returns.
"""

import json

from conftest import load_tool

pp_tool = load_tool("model_physical_properties")


# ── fakes mimicking adsk PhysicalProperties + a measurable entity ────────────

class _Pt:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _Vec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class FakePhysProps:
    """All values in the API's native units: mass kg, volume cm^3, area cm^2, density kg/cm^3,
    centerOfMass cm, moments kg*cm^2, gyration cm. The get* methods return [ok, *values] lists."""
    def __init__(self, mass=10.0, volume=8.0, area=24.0, density=1.25,
                 com=(2.0, 4.0, 6.0), accuracy=1):
        self.mass = mass
        self.volume = volume
        self.area = area
        self.density = density
        self.centerOfMass = _Pt(*com)
        self.accuracy = accuracy

    def getXYZMomentsOfInertia(self):
        return [True, 100.0, 200.0, 300.0, 10.0, 20.0, 30.0]   # xx,yy,zz,xy,yz,xz (kg*cm^2)

    def getPrincipalMomentsOfInertia(self):
        return [True, 90.0, 180.0, 270.0]

    def getPrincipalAxes(self):
        return [True, _Vec(1, 0, 0), _Vec(0, 1, 0), _Vec(0, 0, 1)]

    def getRadiusOfGyration(self):
        return [True, 3.0, 5.0, 7.0]   # cm

    def getRotationToPrincipal(self):
        return [True, 0.1, 0.2, 0.3]   # radians (NOT a length — must not be unit-scaled)


class FakeEntity:
    def __init__(self, pp=None):
        self._pp = pp or FakePhysProps()
    def getPhysicalProperties(self, acc):
        self._last_acc = acc
        return self._pp


def _run(entity=None, target="x", units="mm", accuracy="medium", per_body=False):
    """Drive handler() against a specific entity by stubbing target resolution + the design seam."""
    entity = entity if entity is not None else FakeEntity()
    orig_resolve = pp_tool._resolve_target
    orig_design = pp_tool._common.design
    pp_tool._common.design = lambda: object()                  # truthy design
    pp_tool._resolve_target = lambda design, t: (entity, "test target")
    try:
        return pp_tool.handler(target=target, units=units, accuracy=accuracy, per_body=per_body)
    finally:
        pp_tool._resolve_target = orig_resolve
        pp_tool._common.design = orig_design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_units(self):
        res = _run(units="furlong")
        assert res["isError"] is True and "units" in res["message"].lower()

    def test_unknown_accuracy(self):
        res = _run(accuracy="perfect")
        assert res["isError"] is True and "accuracy" in res["message"].lower()

    def test_no_active_design(self):
        orig = pp_tool._common.design
        pp_tool._common.design = lambda: None
        try:
            res = pp_tool.handler(target="x")
            assert res["isError"] is True and "design" in res["message"].lower()
        finally:
            pp_tool._common.design = orig

    def test_unresolvable_target(self):
        orig_resolve = pp_tool._resolve_target
        orig_design = pp_tool._common.design
        pp_tool._common.design = lambda: object()
        pp_tool._resolve_target = lambda design, t: (None, None)
        try:
            res = pp_tool.handler(target="ghost")
            assert res["isError"] is True and "resolve" in res["message"].lower()
        finally:
            pp_tool._resolve_target = orig_resolve
            pp_tool._common.design = orig_design

    def test_no_measurable_solid(self):
        # getPhysicalProperties returns None (surface-only / empty) -> clean error, not a crash.
        ent = FakeEntity()
        ent.getPhysicalProperties = lambda acc: None
        res = _run(entity=ent)
        assert res["isError"] is True and "physical properties" in res["message"].lower()


# ── scalar quantities + unit scaling ────────────────────────────────────────

class TestScalars:
    def test_mass_is_kg_regardless_of_units(self):
        # mass is ALWAYS kg — never scaled by the length unit.
        out = _payload(_run(units="in"))
        assert out["mass_kg"] == 10.0

    def test_volume_scales_by_cube_of_length(self):
        # 8 cm^3 -> mm^3: factor (cm->mm)=10 so 8 * 10^3 = 8000 mm^3
        out = _payload(_run(units="mm"))
        assert out["volume"] == 8000.0

    def test_area_scales_by_square_of_length(self):
        # 24 cm^2 -> mm^2: 24 * 10^2 = 2400 mm^2
        out = _payload(_run(units="mm"))
        assert out["area"] == 2400.0

    def test_center_of_mass_scales_by_length(self):
        # CoM (2,4,6) cm -> mm: x10
        out = _payload(_run(units="mm"))
        assert out["center_of_mass"] == [20.0, 40.0, 60.0]

    def test_inches_volume_and_com(self):
        out = _payload(_run(units="in"))
        # 8 cm^3 -> in^3: (1/2.54)^3
        assert abs(out["volume"] - 8.0 * (1 / 2.54) ** 3) < 1e-6
        assert abs(out["center_of_mass"][0] - 2.0 / 2.54) < 1e-6

    def test_density_reported_in_kg_per_cm3(self):
        out = _payload(_run(units="mm"))
        assert out["density_kg_per_cm3"] == 1.25   # not unit-scaled


# ── inertia tensor + principal frame ────────────────────────────────────────

class TestInertia:
    def test_world_inertia_scaled_by_length_squared(self):
        # kg*cm^2 -> kg*mm^2: factor (cm->mm)^2 = 100
        out = _payload(_run(units="mm"))
        inert = out["inertia_world"]
        assert inert["Ixx"] == 100.0 * 100  # 100 kg*cm^2 -> 10000 kg*mm^2
        assert inert["Iyy"] == 200.0 * 100
        assert inert["Ixz"] == 30.0 * 100

    def test_principal_moments_present_and_scaled(self):
        out = _payload(_run(units="mm"))
        pm = out["principal_moments"]
        assert pm["i1"] == 90.0 * 100 and pm["i3"] == 270.0 * 100

    def test_principal_axes_are_unit_vectors_not_scaled(self):
        out = _payload(_run(units="mm"))
        # axes are directions — must NOT be unit-scaled
        assert out["principal_axes"]["x"] == [1.0, 0.0, 0.0]
        assert out["principal_axes"]["z"] == [0.0, 0.0, 1.0]

    def test_radius_of_gyration_scaled_by_length(self):
        out = _payload(_run(units="mm"))
        g = out["radius_of_gyration"]
        assert g["kx"] == 30.0 and g["kz"] == 70.0   # 3cm->30mm, 7cm->70mm

    def test_rotation_to_principal_is_radians_not_scaled(self):
        out = _payload(_run(units="mm"))
        r = out["rotation_to_principal_rad"]
        assert r["rx"] == 0.1 and r["rz"] == 0.3     # radians, never unit-scaled

    def test_failed_inertia_ok_flag_is_skipped(self):
        # if the API returns ok=False, the field is omitted rather than reporting garbage.
        ent = FakeEntity()
        ent._pp.getXYZMomentsOfInertia = lambda: [False, 0, 0, 0, 0, 0, 0]
        out = _payload(_run(entity=ent))
        assert "inertia_world" not in out


# ── accuracy reporting ──────────────────────────────────────────────────────

class TestAccuracy:
    def test_accuracy_passed_through_and_reported(self):
        import adsk.fusion
        out = _payload(_run(accuracy="high"))
        assert out["accuracy"] == "high"

    def test_accuracy_used_maps_enum_back_to_name(self):
        # pp.accuracy (enum int) is mapped back to a readable name.
        ent = FakeEntity(FakePhysProps(accuracy=pp_tool._ACCURACY["very_high"]))
        out = _payload(_run(entity=ent, accuracy="very_high"))
        assert out["accuracy_used"] == "very_high"


# ── per-body breakdown ───────────────────────────────────────────────────────

class _OccColl:
    def __init__(self, occs):
        self._o = list(occs)
    @property
    def count(self):
        return len(self._o)
    def item(self, i):
        return self._o[i]


class _FakeOcc:
    def __init__(self, name, mass, com):
        self.name = name
        self._pp = FakePhysProps(mass=mass, com=com)
    def getPhysicalProperties(self, acc):
        return self._pp


class TestPerBody:
    def test_per_occurrence_breakdown(self):
        # the target is the root: handler reads entity.occurrences for the breakdown. Build a root-like
        # entity whose .occurrences yields two occurrences with distinct masses.
        root = FakeEntity()
        root.occurrences = _OccColl([_FakeOcc("A:1", 3.0, (1, 0, 0)),
                                     _FakeOcc("B:1", 7.0, (0, 2, 0))])
        # make _resolve_target return this entity AND the handler treat it as root: patch design.rootComponent
        orig_resolve = pp_tool._resolve_target
        orig_design = pp_tool._common.design
        fake_design = type("D", (), {"rootComponent": root})()
        pp_tool._common.design = lambda: fake_design
        pp_tool._resolve_target = lambda design, t: (root, "whole design")
        try:
            out = _payload(pp_tool.handler(target="", units="mm", per_body=True))
        finally:
            pp_tool._resolve_target = orig_resolve
            pp_tool._common.design = orig_design
        assert out["per_occurrence_count"] == 2
        names = {b["occurrence"]: b["mass_kg"] for b in out["per_occurrence"]}
        assert names == {"A:1": 3.0, "B:1": 7.0}
        # CoM scaled to mm
        b0 = next(b for b in out["per_occurrence"] if b["occurrence"] == "A:1")
        assert b0["center_of_mass"] == [10.0, 0.0, 0.0]

    def test_per_body_false_omits_breakdown(self):
        out = _payload(_run(per_body=False))
        assert "per_occurrence" not in out
