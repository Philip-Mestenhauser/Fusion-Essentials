"""Unit tests for ``mesh_shell.py`` - the MeshShell feature over a MeshBody.

No live Fusion. The fakes model the slice of the API the tool touches: MeshShellFeatures
(createInput(mesh) -> input -> add(input) -> feature or None), a MeshShellFeatureInput whose
thickness accepts ONLY a ValueInput (its declared type), and a MeshBody the shell re-triangulates
and hollows IN PLACE (same body, same name).

Pinned (the DoD):
  - thickness crosses the wire as a ValueInput carrying internal cm, scaled from the call's units.
  - a zero / negative / non-numeric thickness is refused before any mutation.
  - the effect is judged on the body: triangle and vertex counts plus the enclosed volume, and a
    shell that moved none of them is an ERROR.
  - a DIRECT design returns no feature while the hollow LANDS - reported as success off the body
    census; a PARAMETRIC no-feature return stays an honest error.
  - the thickness that landed is read back off the feature's ModelParameter, never echoed.
  - a mesh nothing can be read off afterwards is reported UNVERIFIED, not as success.
"""

import types

import adsk.fusion
import pytest

from conftest import BRepBody, load_tool, payload, error_message

ms = load_tool("mesh_shell")


# ── fakes ────────────────────────────────────────────────────────────────────────────────────────

class _ValueInput:
    """Stands in for the adsk.core.ValueInput that MeshShellFeatureInput.thickness is typed to take.
    Carries the real number so a scaling assertion can read it back."""
    def __init__(self, real):
        self.real = real


class TriangleMesh:
    def __init__(self, tri, nodes):
        self.triangleCount = tri
        self.nodeCount = nodes


class MeshBody:
    """Stands in for adsk.fusion.MeshBody. `volume` RAISES when the mesh is not closed - an open
    mesh encloses no volume. `dead` models an invalidated wrapper, which raises on every read;
    `counts_readable=False` models a body whose displayMesh cannot be reached while its volume
    still can - the two halves of the census fail independently."""
    def __init__(self, name="Scan1", tri=12, nodes=8, is_closed=True, volume=1.0, token=None,
                 parent=None, counts_readable=True):
        self.name = name
        self.dead = False
        self._display = TriangleMesh(tri, nodes)
        self._is_closed = is_closed
        self.volume_cm3 = volume
        self.counts_readable = counts_readable
        self.entityToken = token or f"MTOK::{name}"
        self.parentComponent = parent

    def _live(self):
        if self.dead:
            raise RuntimeError("3 : object is no longer valid")

    @property
    def displayMesh(self):
        self._live()
        if not self.counts_readable:
            raise RuntimeError("3 : the display mesh is unavailable")
        return self._display

    @property
    def isClosed(self):
        self._live()
        return self._is_closed

    @property
    def volume(self):
        self._live()
        if not self._is_closed:
            raise RuntimeError("3 : the mesh is not closed and encloses no volume")
        return self.volume_cm3


class _Coll:
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


class _ShellInput:
    """MeshShellFeatureInput. thickness is declared core.ValueInput, so a raw number is refused
    here exactly as the live typed property refuses it."""
    def __init__(self, mesh):
        self.mesh = mesh
        self.targetBaseFeature = None
        self._thickness = None

    @property
    def thickness(self):
        return self._thickness

    @thickness.setter
    def thickness(self, value):
        if not isinstance(value, _ValueInput):
            raise TypeError("thickness expects a ValueInput, got " + type(value).__name__)
        self._thickness = value


class _ShellFeatures:
    """comp.features.meshShellFeatures. `on_add` is the in-place hollow the shell performs;
    `none_feature` is the measured DIRECT-mode return (None WITH the effect landed)."""
    def __init__(self, on_add=None, raise_on_add=False, none_feature=False,
                 create_input_returns_none=False, feature_thickness=None):
        self._on_add = on_add
        self.raise_on_add = raise_on_add
        self.none_feature = none_feature
        self._create_input_returns_none = create_input_returns_none
        self._feature_thickness = feature_thickness
        self.last_input = None
        self.create_arg = None
        self.add_called = False

    def createInput(self, mesh):
        self.create_arg = mesh
        if self._create_input_returns_none:
            return None
        self.last_input = _ShellInput(mesh)
        return self.last_input

    def add(self, inp):
        self.add_called = True
        if self.raise_on_add:
            raise RuntimeError("shell failed")
        if self._on_add is not None:
            self._on_add()
        if self.none_feature:
            return None
        landed = (self._feature_thickness if self._feature_thickness is not None
                  else getattr(getattr(self.last_input, "thickness", None), "real", None))
        feat = types.SimpleNamespace(name="MeshShell1")
        if landed is not None:
            feat.thickness = types.SimpleNamespace(value=float(landed))
        return feat


class _Features:
    def __init__(self, shell=None):
        self.meshShellFeatures = shell


class _Comp:
    def __init__(self, name="Comp", meshes=(), features=None):
        self.name = name
        self.dead = False
        self._meshes = _Coll(meshes)
        self.features = features
        self.bRepBodies = _Coll()

    @property
    def meshBodies(self):
        if self.dead:
            raise RuntimeError("3 : object is no longer valid")
        return self._meshes


class _Design:
    def __init__(self, comp, design_type=1, tokens=None, all_components=None):
        self.rootComponent = comp
        self.activeComponent = comp
        self.designType = design_type    # 1 parametric, 0 direct (current_design_type's int fallback)
        self._tokens = dict(tokens or {})
        self._all_components = list(all_components) if all_components is not None else [comp]

    @property
    def allComponents(self):
        return _Coll(self._all_components)

    def findEntityByToken(self, token):
        e = self._tokens.get(token)
        return [e] if e is not None else []


# ── rig ──────────────────────────────────────────────────────────────────────────────────────────

def _rig(monkeypatch, mesh=None, on_add=None, design_type=1, **feat_kw):
    """Wire one mesh + a meshShellFeatures collection into the tool. Returns (mesh, comp, feats)."""
    mesh = mesh if mesh is not None else MeshBody()
    comp = _Comp(meshes=[mesh])
    mesh.parentComponent = comp
    feats = _ShellFeatures(on_add=on_add, **feat_kw)
    comp.features = _Features(shell=feats)
    design = _Design(comp, design_type=design_type)
    monkeypatch.setattr(ms._common, "design", lambda: design)
    monkeypatch.setattr(ms._inputs._common, "design", lambda: design)
    monkeypatch.setattr(ms._MESH, "resolve", lambda raw: (mesh, None))
    monkeypatch.setattr(ms.adsk.core.ValueInput, "createByReal", _ValueInput)
    return mesh, comp, feats


def _hollow(mesh, tri=30370, nodes=15189, volume=0.1159):
    """The measured in-place hollow: the body is re-triangulated and its volume drops."""
    def _apply():
        mesh.displayMesh.triangleCount = tri
        mesh.displayMesh.nodeCount = nodes
        mesh.volume_cm3 = volume
    return _apply


@pytest.fixture
def rig(monkeypatch):
    """A shell that takes a 12-triangle closed box to 30,370 triangles and 11.6% of its volume."""
    mesh = MeshBody(tri=12, nodes=8, is_closed=True, volume=1.0)
    mesh_, comp, feats = _rig(monkeypatch, mesh=mesh, on_add=_hollow(mesh))
    return types.SimpleNamespace(mesh=mesh, comp=comp, feats=feats, monkeypatch=monkeypatch)


# ── the ValueInput-typed thickness ───────────────────────────────────────────────────────────────

class TestThicknessInput:
    def test_thickness_crosses_as_a_value_input_in_internal_cm(self, rig):
        payload(ms.handler(mesh="H", thickness=2.0, units="mm"))
        assert rig.feats.last_input.thickness.real == pytest.approx(0.2)

    def test_thickness_in_inches_is_scaled_by_2_54(self, rig):
        payload(ms.handler(mesh="H", thickness=1.0, units="in"))
        assert rig.feats.last_input.thickness.real == pytest.approx(2.54)

    def test_thickness_in_cm_passes_through_unscaled(self, rig):
        payload(ms.handler(mesh="H", thickness=0.5, units="cm"))
        assert rig.feats.last_input.thickness.real == pytest.approx(0.5)

    def test_a_raw_number_on_thickness_would_be_refused_by_the_api(self, rig):
        # the fake input rejects a non-ValueInput exactly as the live typed property does
        rig.monkeypatch.setattr(ms.adsk.core.ValueInput, "createByReal", lambda v: v)
        assert "thickness" in error_message(ms.handler(mesh="H", thickness=2.0))


# ── input guards (all BEFORE any mutation) ───────────────────────────────────────────────────────

class TestInputGuards:
    def test_a_missing_thickness_is_refused(self, rig):
        assert "thickness" in error_message(ms.handler(mesh="H"))
        assert rig.feats.add_called is False

    def test_a_zero_thickness_is_refused(self, rig):
        msg = error_message(ms.handler(mesh="H", thickness=0))
        assert "non-zero" in msg and "thickness" in msg
        assert rig.feats.add_called is False

    def test_a_negative_thickness_is_refused_naming_the_value(self, rig):
        msg = error_message(ms.handler(mesh="H", thickness=-2.0))
        assert "positive" in msg and "-2.0" in msg
        assert rig.feats.add_called is False

    def test_a_non_numeric_thickness_is_refused(self, rig):
        assert "number" in error_message(ms.handler(mesh="H", thickness="thick"))
        assert rig.feats.add_called is False

    def test_unknown_units_are_refused(self, rig):
        assert "units" in error_message(ms.handler(mesh="H", thickness=2.0,
                                                   units="furlong")).lower()
        assert rig.feats.add_called is False

    def test_no_active_design_is_an_error(self, rig):
        rig.monkeypatch.setattr(ms._common, "design", lambda: None)
        assert "No active design" in error_message(ms.handler(mesh="H", thickness=2.0))

    def test_a_missing_shell_features_collection_is_an_error(self, monkeypatch):
        _mesh, comp, _feats = _rig(monkeypatch)
        comp.features = _Features(shell=None)
        assert "meshShellFeatures" in error_message(ms.handler(mesh="H", thickness=2.0))

    def test_create_input_returning_none_is_an_error(self, monkeypatch):
        _rig(monkeypatch, create_input_returns_none=True)
        assert "returned nothing" in error_message(ms.handler(mesh="H", thickness=2.0))

    def test_an_add_failure_surfaces_and_is_not_swallowed(self, monkeypatch):
        _rig(monkeypatch, raise_on_add=True)
        assert "meshShellFeatures.add raised" in error_message(ms.handler(mesh="H", thickness=2.0))

    def test_a_brep_body_is_redirected_to_the_mesh_tools(self, monkeypatch):
        _mesh, comp, feats = _rig(monkeypatch)
        monkeypatch.setattr(adsk.fusion, "MeshBody", MeshBody)
        monkeypatch.setattr(adsk.fusion, "BRepBody", BRepBody)
        brep = BRepBody(name="Body1", entity_token="BTOK::Body1")
        monkeypatch.setattr(ms._MESH, "resolve", ms._inputs.MeshBodyRef("mesh").resolve)
        design = _Design(comp, tokens={"BTOK::Body1": brep})
        monkeypatch.setattr(ms._common, "design", lambda: design)
        monkeypatch.setattr(ms._inputs._common, "design", lambda: design)
        msg = error_message(ms.handler(mesh="BTOK::Body1", thickness=2.0))
        assert "MESH body" in msg and "SOLID" in msg
        assert feats.add_called is False


# ── verify-the-effect ────────────────────────────────────────────────────────────────────────────

class TestVerification:
    def test_a_successful_shell_reports_the_counts_and_the_volume_drop(self, rig):
        out = payload(ms.handler(mesh="H", thickness=2.0, units="mm"))
        assert out["hollowed"] is True
        assert out["before"] == {"triangle_count": 12, "vertex_count": 8}
        assert out["after"] == {"triangle_count": 30370, "vertex_count": 15189}
        assert out["changed"] == ["triangle_count", "vertex_count", "volume"]
        # 1.0 cm3 -> 0.1159 cm3 is a drop of 0.8841 cm3 = 884.1 mm3
        assert out["volume_change"] == pytest.approx(-884.1)
        assert out["units"] == "mm"
        assert out["feature"] == "MeshShell1"

    def test_a_shell_that_changed_nothing_is_an_error(self, monkeypatch):
        _rig(monkeypatch)   # add() fires no side effect at all
        msg = error_message(ms.handler(mesh="H", thickness=2.0))
        assert "unchanged" in msg and "12 triangles, 8 vertices, the same volume" in msg

    def test_the_no_effect_error_omits_a_volume_it_could_not_read(self, monkeypatch):
        # an open mesh's volume is unreadable at both ends, so "the same volume" would be a claim
        # about a number nobody measured
        _rig(monkeypatch, mesh=MeshBody(tri=12, nodes=8, is_closed=False))
        msg = error_message(ms.handler(mesh="H", thickness=2.0))
        assert "12 triangles, 8 vertices)" in msg
        assert "volume" not in msg

    def test_the_no_effect_error_omits_counts_it_could_not_read(self, monkeypatch):
        # the mirror case: the display mesh is unreachable, so "None triangles" must not appear
        _rig(monkeypatch, mesh=MeshBody(volume=1.0, counts_readable=False))
        msg = error_message(ms.handler(mesh="H", thickness=2.0))
        assert "(the same volume)" in msg
        assert "triangles" not in msg and "None" not in msg

    def test_a_volume_that_did_not_drop_is_reported_as_unconfirmed_not_as_a_hollow(self, monkeypatch):
        # triangles moved, so something happened - but a shell whose volume held is not a hollow,
        # and neither the flag nor the note may claim one.
        mesh = MeshBody(tri=12, nodes=8, volume=1.0)
        _rig(monkeypatch, mesh=mesh, on_add=_hollow(mesh, volume=1.0))
        out = payload(ms.handler(mesh="H", thickness=2.0))
        assert out["hollowed"] is False
        assert out["changed"] == ["triangle_count", "vertex_count"]
        assert "did not drop" in out["note"]

    def test_an_open_mesh_reports_no_volume_change_rather_than_a_wrong_one(self, monkeypatch):
        mesh = MeshBody(tri=12, nodes=8, is_closed=False)
        _rig(monkeypatch, mesh=mesh, on_add=_hollow(mesh))
        out = payload(ms.handler(mesh="H", thickness=2.0))
        assert out["volume_change"] is None
        assert out["hollowed"] is False
        assert out["changed"] == ["triangle_count", "vertex_count"]
        assert "could not be read" in out["note"]

    def test_an_unreadable_mesh_is_reported_as_unverified(self, monkeypatch):
        mesh = MeshBody()
        _m, comp, feats = _rig(monkeypatch, mesh=mesh)

        def _wipe():
            mesh.dead = True
            comp.dead = True
        feats._on_add = _wipe
        assert "UNVERIFIED" in error_message(ms.handler(mesh="H", thickness=2.0))


# ── the thickness read-back (never an echo) ──────────────────────────────────────────────────────

class TestThicknessReadBack:
    def test_the_landed_thickness_is_reported_in_the_call_units(self, rig):
        out = payload(ms.handler(mesh="H", thickness=2.0, units="mm"))
        assert out["thickness"] == pytest.approx(2.0)

    def test_a_thickness_the_api_did_not_take_is_an_error_not_an_echo(self, monkeypatch):
        mesh = MeshBody(tri=12, nodes=8, volume=1.0)
        _rig(monkeypatch, mesh=mesh, on_add=_hollow(mesh), feature_thickness=0.05)
        msg = error_message(ms.handler(mesh="H", thickness=2.0, units="mm"))
        assert "0.5 mm" in msg and "2.0 mm was" in msg

    def test_an_unreadable_thickness_is_reported_as_unverified_not_as_the_request(self, monkeypatch):
        mesh = MeshBody(tri=12, nodes=8, volume=1.0)
        _rig(monkeypatch, mesh=mesh, on_add=_hollow(mesh), design_type=0, none_feature=True)
        out = payload(ms.handler(mesh="H", thickness=2.0))
        assert out["thickness"] is None
        assert "thickness_unverified" in out


# ── mode routing (the measured direct-mode None return) ──────────────────────────────────────────

class TestModeRouting:
    def test_direct_mode_returns_no_feature_yet_the_landed_hollow_is_success(self, monkeypatch):
        mesh = MeshBody(tri=12, nodes=8, volume=1.0)
        _rig(monkeypatch, mesh=mesh, on_add=_hollow(mesh), design_type=0, none_feature=True)
        out = payload(ms.handler(mesh="H", thickness=2.0))
        assert out["hollowed"] is True
        assert out["no_timeline_feature"] is True
        assert "feature" not in out
        assert "DIRECT mode" in out["note"]

    def test_direct_mode_with_no_effect_is_still_an_error(self, monkeypatch):
        # a None return is never itself proof of an effect - the census decides
        _rig(monkeypatch, design_type=0, none_feature=True)
        assert "unchanged" in error_message(ms.handler(mesh="H", thickness=2.0))

    def test_parametric_no_feature_return_stays_an_honest_error(self, monkeypatch):
        mesh = MeshBody(tri=12, nodes=8, volume=1.0)
        _rig(monkeypatch, mesh=mesh, on_add=_hollow(mesh), design_type=1, none_feature=True)
        assert "returned no feature" in error_message(ms.handler(mesh="H", thickness=2.0))

    def test_no_base_feature_scope_is_opened(self, rig):
        # measured: meshShellFeatures.add returns a real feature at PLAIN parametric scope
        payload(ms.handler(mesh="H", thickness=2.0))
        assert rig.feats.last_input.targetBaseFeature is None
