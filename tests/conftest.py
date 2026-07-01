"""Test harness for the Fusion-Essentials MCP tools.

The tools under ``commands/mcpServer/tools/`` are written for a *live* Fusion
session: each one does ``import adsk.core`` and runs ``app =
adsk.core.Application.get()`` at module top level, and reaches Fusion geometry
through ``adsk.fusion``. To unit-test their pure logic outside Fusion we:

  1. Inject lightweight mock ``adsk`` modules into ``sys.modules`` BEFORE any
     tool is imported (so the module-top ``Application.get()`` succeeds).
  2. Load a single tool module in isolation — without running
     ``commands/__init__.py`` (which builds Fusion UI panels) or
     ``tools/__init__.py`` (which imports all ~30 tools, most needing adsk).

``load_tool("model_inspect")`` does both and returns the module so a
test can call its ``handler(...)`` and private helpers directly.

The mocks here are deliberately small: they implement only what a tool actually
touches. Extend them as you add tests for more tools — that is the intended
workflow, mirroring the bootstrap kit's ``mock_adsk.py`` philosophy, adapted to
this project's package layout (no ``<prefix>_`` packages, no webapp).
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import Mock

import pytest

# The tool loader spec-loads source files directly; Python would write
# __pycache__/*.pyc next to the real source. That bytecode is keyed by mtime, so
# rapidly editing-then-restoring a tool (as a regression check does) can leave a
# stale .pyc that masks the restored source. Suppress bytecode writing for the
# whole test process so the cache can never go out of sync with the source.
sys.dont_write_bytecode = True

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS_DIR = os.path.join(REPO_ROOT, "commands")
TOOLS_DIR = os.path.join(COMMANDS_DIR, "mcpServer", "tools")


# ── adsk mock installation ─────────────────────────────────────────────────

def install_mock_adsk():
    """Inject mock ``adsk`` / ``adsk.core`` / ``adsk.fusion`` into sys.modules.

    Returns ``(adsk, core, fusion)``. ``core``/``fusion`` are ``Mock`` objects,
    so any attribute a tool reads that we haven't explicitly modelled returns a
    child Mock rather than raising — convenient, but it means a test must assert
    on concrete values to actually pin behaviour down (a Mock is truthy and not
    ``None``). The type classes a tool uses for ``isinstance`` / ``.cast`` /
    ``type(x).__name__`` checks ARE modelled below, because those drive real
    branching.
    """
    core = Mock()
    app = Mock()
    # Tools commonly tack `"active_document": app.activeDocument.name` onto a
    # payload that then gets json.dumps'd. A bare Mock().name is itself a Mock
    # (not JSON-serializable), so give the stock app a real string name. Tests
    # that care about document state override app.activeDocument explicitly.
    app.activeDocument.name = "TestDoc"
    app.version = "0.0.0-test"
    core.Application.get.return_value = app

    fusion = Mock()
    # Design.cast(product) is used to fetch the active design. Model it as a
    # pass-through: whatever the test puts on app.activeProduct comes back.
    fusion.Design = Mock()
    fusion.Design.cast = Mock(side_effect=lambda x: x)

    # adsk.cam is needed by the CAM tools (cam_info, cam_templates, generate_toolpaths).
    cam = Mock()
    # Tools cast raw collection items with adsk.cam.Operation.cast(op) and skip
    # anything that casts to None. A bare Mock().cast returns a truthy Mock, which
    # would mask that filter; make cast a pass-through so a test's fake op (or a
    # real None) flows through unchanged.
    cam.Operation.cast = Mock(side_effect=lambda x: x)

    adsk = types.ModuleType("adsk")
    adsk.core = core
    adsk.fusion = fusion
    adsk.cam = cam

    sys.modules["adsk"] = adsk
    sys.modules["adsk.core"] = core
    sys.modules["adsk.fusion"] = fusion
    sys.modules["adsk.cam"] = cam
    return adsk, core, fusion


def load_tool(module_name):
    """Import one tool module in isolation, with mocked adsk already installed.

    Skips ``commands/__init__.py`` (Fusion UI side effects) and
    ``tools/__init__.py`` (imports every tool) by putting ``commands/`` on the
    path, importing only the cheap ``mcpServer`` + ``mcp_primitives`` packages,
    stubbing ``mcpServer.tools`` as an empty package, then spec-loading the
    single requested module so its ``from ..mcp_primitives ...`` relative
    imports resolve.
    """
    if COMMANDS_DIR not in sys.path:
        sys.path.insert(0, COMMANDS_DIR)

    import mcpServer                  # noqa: F401  empty __init__, cheap
    import mcpServer.mcp_primitives   # noqa: F401  adsk-free, cheap

    if "mcpServer.tools" not in sys.modules:
        tools_pkg = types.ModuleType("mcpServer.tools")
        tools_pkg.__path__ = [TOOLS_DIR]
        tools_pkg.__package__ = "mcpServer"
        sys.modules["mcpServer.tools"] = tools_pkg

    full_name = f"mcpServer.tools.{module_name}"
    # Reuse an already-loaded module instead of re-execing it into a NEW object. This is essential for
    # the shared substrate (_common/_inputs): tools do `from . import _common`, binding whatever
    # _common object is in sys.modules at load time. If load_tool re-execs _common later, that creates
    # a SECOND _common — and a tool loaded earlier keeps pointing at the first, while the conftest
    # snapshot/restore (and a later test's patch) act on the second. The two diverge and the handler
    # reads a stale _common.app -> _common.design() returns None mid-suite ("No active design"). One
    # canonical module per name keeps the seam single-identity. Tests re-patch app/Design.cast in their
    # own _install, so reusing the module object is safe.
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(
        full_name, os.path.join(TOOLS_DIR, f"{module_name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


# Pristine seam per tool module, captured ONCE the first time the module is seen — before any test
# patches it — so the autouse fixture can restore to it.
#
# A tool module reads the active design through one of a few module-level "seam" names: `app`
# (read by _common.design()), the imported-by-value `design`/`target_component`/`_design` helpers it
# calls bare, or `_data` (data tools). A test patches these on its OWN module object (e.g.
# `fl.target_component = lambda x: comp`, `mo.app = ...`). Because load_tool returns ONE canonical
# module object per tool for the whole session, such a patch LEAKS into the next test that uses the
# same module unless restored — producing order-dependent failures (the handler builds onto a stale
# fake). So the fixture restores these seam attrs on EVERY loaded tool module after each test, not
# just the substrate few. (`_design` added because a handful of tools bind a local `_design`.)
_PRISTINE_SEAMS = {}
_SEAM_ATTRS = ("design", "target_component", "_design", "app", "_data")


def _loaded_tool_modules():
    """Every currently-loaded `mcpServer.tools.*` module object (substrate + tools)."""
    return {name: mod for name, mod in sys.modules.items()
            if name.startswith("mcpServer.tools.") and mod is not None}


def _pristine_seam(key, mod):
    if key not in _PRISTINE_SEAMS:
        _PRISTINE_SEAMS[key] = {a: getattr(mod, a) for a in _SEAM_ATTRS if hasattr(mod, a)}
    return _PRISTINE_SEAMS[key]


# Install mocks at collection time, before any test module imports a tool.
install_mock_adsk()


# Pristine snapshot of each adsk mock module's attribute namespace, captured ONCE right after install
# (before any test patches it). Tests routinely assign fake TYPE CLASSES and enum values onto these
# shared mocks — e.g. `adsk.fusion.BRepBody = SomeClass`, `adsk.fusion.MeshBody = ...`,
# `adsk.fusion.DesignTypes.ParametricDesignType = 1`, `adsk.core.Point3D.create = ...`. Because the
# mock modules are shared for the whole session, those assignments LEAK into later tests unless undone
# (a later test then isinstance-checks against the wrong class, or reads a stale enum, and fails only
# in certain orderings). Restoring each module's __dict__ to this snapshot after every test removes
# test-added attributes and reverts changed ones, making the suite order-independent.
def _snapshot_adsk_dicts():
    snap = {}
    for name in ("adsk.core", "adsk.fusion", "adsk.cam"):
        mod = sys.modules[name]
        # Shallow copy of the module-mock's own attribute dict, plus a shallow copy of each child
        # Mock's __dict__ one level down (that's where Type.cast / Type.create / Enum.MEMBER live).
        children = {}
        for attr, child in list(vars(mod).items()):
            cd = getattr(child, "__dict__", None)
            if isinstance(cd, dict):
                children[attr] = dict(cd)
        snap[name] = (dict(vars(mod)), children)
    return snap


def _restore_adsk_dicts(snap):
    for name, (top, children) in snap.items():
        mod = sys.modules[name]
        md = vars(mod)
        md.clear()
        md.update(top)
        for attr, cd in children.items():
            child = md.get(attr)
            chd = getattr(child, "__dict__", None)
            if isinstance(chd, dict):
                chd.clear()
                chd.update(cd)


_PRISTINE_ADSK = _snapshot_adsk_dicts()


@pytest.fixture(autouse=True)
def _restore_shared_adsk_mocks():
    """Snapshot-and-restore the mutable ``adsk`` mock attributes around every test.

    Several tool tests (inspect_view, section_view, show_toolpath, parameters,
    extrude, patterns) install per-test behaviour by REASSIGNING shared mock
    callables — ``adsk.fusion.Design.cast``, ``adsk.cam.CAM.cast``/
    ``Operation.cast``, ``adsk.core.Point3D.create``/``Vector3D.create``/
    ``ValueInput.createByString``/``createByReal``/``ObjectCollection.create``.
    Because these live on module-level Mocks shared by the whole session, a
    reassignment would otherwise LEAK into later tests (e.g. clobbering
    ``Design.cast``'s pass-through so measure_bounding_box sees no design). This
    autouse fixture records the originals before each test and puts them back
    after, keeping tests order-independent without per-file teardown.
    """
    # The whole adsk.core/fusion/cam mock namespace is snapshot/restored from _PRISTINE_ADSK (captured
    # once at install). That covers BOTH the reassigned callables (Design.cast, Point3D.create,
    # ValueInput.create*, ObjectCollection.create, …) AND the fake TYPE CLASSES / enum members tests
    # assign onto the shared mocks (adsk.fusion.BRepBody/MeshBody/BaseFeature, DesignTypes.*, …) —
    # the latter were the source of order-dependent isinstance/enum failures.
    # Tools that share _common.design()/target_component()/app (instead of a local _design()) are
    # tested by patching those on the _common module (the seam _inputs.py uses). Snapshot them at SETUP
    # and restore after, so a test's patch can't leak into a later test. _common is loaded lazily by
    # load_tool, so it may not exist yet on the very first tests — guard for that.
    # Capture pristine substrate seams at SETUP (before the test body patches them). _pristine_seam
    # only records the first time it sees each module — so the very first test to load a substrate
    # captures it clean, and later (patched) setups don't overwrite the cache.
    for _name, _mod in _loaded_tool_modules().items():
        _pristine_seam(_name, _mod)
    # A few tests stub a tool's filesystem check by assigning `mod.os.path.isfile = lambda …`. Because
    # `mod.os` is the REAL os module, that mutates process-wide os.path and would leak into a later test
    # (e.g. a left-behind `isfile -> False` makes an unrelated upload report "file not found"). Snapshot
    # the real os.path predicates and restore them after the test.
    import os.path as _ospath
    _os_saved = [(_ospath, a, getattr(_ospath, a)) for a in ("isfile", "isdir", "exists")
                 if hasattr(_ospath, a)]
    try:
        yield
    finally:
        for _obj, _attr, _orig in _os_saved:
            setattr(_obj, _attr, _orig)
        _restore_adsk_dicts(_PRISTINE_ADSK)
        # load_tool returns ONE canonical object per tool module for the whole session, so a test that
        # patches a module-level seam (app/design/target_component/_design/_data) leaks into the next
        # test using that same module. Restore every loaded tool module's seam attrs to PRISTINE
        # (captured the first time the module is seen, before any patch — a per-test snapshot would
        # chain a prior test's patch forward and lose the original). This keeps the suite
        # order-independent without per-file teardown.
        for _name, _mod in _loaded_tool_modules().items():
            for attr, val in _pristine_seam(_name, _mod).items():
                setattr(_mod, attr, val)


# ── small Fusion-shaped fakes a tool's logic branches on ───────────────────
#
# These mimic the *interface* a tool reads, not the whole API. Names match the
# Fusion type names because tools branch on ``type(entity).__name__``.

class FakePoint:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def vectorTo(self, other):
        # Mirrors adsk.core.Point3D.vectorTo -> Vector3D(other - self).
        return FakeVector3D(other.x - self.x, other.y - self.y, other.z - self.z)


class FakeBoundingBox3D:
    def __init__(self, min_pt, max_pt):
        self.minPoint = min_pt
        self.maxPoint = max_pt


class BRepBody:
    """Matches type(entity).__name__ == 'BRepBody' in the tool's logic."""
    def __init__(self, name="Body", bbox=None):
        self.name = name
        self.boundingBox = bbox


class _NamedCollection:
    """Counted collection (Fusion's count/item(i)) with itemByName lookup."""
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def itemByName(self, name):
        for it in self._items:
            if getattr(it, "name", None) == name:
                return it
        return None


class Component:
    """Matches a Component: owns bRepBodies but no boundingBox of its own here.

    ``type(self).__name__ == 'Component'`` — the tool treats this as "not
    directly measurable" and falls back to a body.
    """
    def __init__(self, name="Comp", bodies=()):
        self.name = name
        self.bRepBodies = _NamedCollection(bodies)
        # A component's own boundingBox (used by the world-aligned path).
        self.boundingBox = None


@pytest.fixture
def bbox():
    """Factory: FakeBoundingBox3D from (min xyz, max xyz) in cm (Fusion's unit)."""
    def _make(minp, maxp):
        return FakeBoundingBox3D(FakePoint(*minp), FakePoint(*maxp))
    return _make


# ── vectors / surfaces / curves for selection.py classification ────────────
#
# selection.py branches on type(surface).__name__ ("Plane", "Cylinder", ...)
# and type(curve).__name__ ("Line3D", "Circle3D", ...), so the geometry fakes
# below are NAMED to match those runtime type names exactly.

class FakeVector3D:
    """Vector with the .copy()/.normalize() interface _unit() prefers.

    normalize() mutates in place and returns False for a (near-)zero vector,
    True otherwise — matching adsk.core.Vector3D's contract that _unit relies on.
    """
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def copy(self):
        return FakeVector3D(self.x, self.y, self.z)

    def normalize(self):
        mag = (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5
        if mag < 1e-12:
            return False
        self.x, self.y, self.z = self.x / mag, self.y / mag, self.z / mag
        return True

    def vectorTo(self, other):
        return FakeVector3D(other.x - self.x, other.y - self.y, other.z - self.z)


class Plane:
    def __init__(self, normal):
        self.normal = normal


class Cylinder:
    def __init__(self, axis):
        self.axis = axis


class Sphere:
    pass


class Line3D:
    pass


class Circle3D:
    def __init__(self, normal):
        self.normal = normal


class _Vertex:
    def __init__(self, point):
        self.geometry = point


class BRepFace:
    """Matches type(entity).__name__ == 'BRepFace'. `geometry` is the surface.

    `_classify` reads area/centroid/edges.count/body.name when it builds a face
    record; supply benign defaults so the result is JSON-serializable. Override
    via kwargs in tests that assert on them.
    """
    def __init__(self, surface, area=0.0, centroid=None, edge_count=0, body_name=None):
        self.geometry = surface
        self.area = area
        self.centroid = centroid
        self.edges = _NamedCollection([None] * edge_count)
        self.body = _SimpleNamed(body_name) if body_name else None


class _SimpleNamed:
    def __init__(self, name):
        self.name = name


class BRepEdge:
    """Matches type(entity).__name__ == 'BRepEdge'. `geometry` is the curve."""
    def __init__(self, curve, start=None, end=None):
        self.geometry = curve
        self.startVertex = _Vertex(start) if start else None
        self.endVertex = _Vertex(end) if end else None


# ── shared fake-design builder + dual-seam install (the test-plumbing convention) ───────────────────
#
# Why this exists: nearly every test_<tool>.py re-implemented the SAME wiring — build a FakeComp/
# FakeDesign, then patch the design onto BOTH seams a tool reads it through:
#   • the tool's own  `mod._common.design()` / `target_component()`  (and `mod.app`), and
#   • the input-kinds' `mod._inputs._common.design()` / `target_component()`  (BodyRef/PlaneRef/… use
#     _inputs, which has its OWN bound _common reference).
# Forgetting the _inputs seam is a silent trap: the handler resolves but the INPUT kind resolves
# against the wrong (or stale) design, so the test passes while testing the wrong thing. Centralising
# it here makes "patch both seams" structural instead of a remembered rule, and removes the per-file
# `adsk.fusion.<Type> = LocalClass` / `ObjectCollection.create` reassignments that leaked across files.

class MakeComp:
    """A component with the standard Fusion collection protocol (count/item/itemByName).

    Pass bodies as names (str) or objects with a `.name`. Extra collections a specific tool needs
    (e.g. a fake `features`) can be attached by the caller after construction, or pass a ready-made
    component to `make_design(comp=...)` instead.
    """
    def __init__(self, name="Root", bodies=(), occurrences=()):
        self.name = name
        norm = [b if hasattr(b, "name") else BRepBody(b) for b in bodies]
        self.bRepBodies = _NamedCollection(norm)
        self.occurrences = _NamedCollection(list(occurrences))
        self.allOccurrences = list(occurrences)
        self.boundingBox = None


class MakeDesign:
    """A design exposing the attributes tools/inputs read: rootComponent, activeComponent (defaults to
    root), allOccurrences, allComponents, and findEntityByToken(token) backed by a `tokens` map."""
    def __init__(self, comp=None, tokens=None, all_components=None):
        self.rootComponent = comp if comp is not None else MakeComp()
        self.activeComponent = self.rootComponent
        self._tokens = dict(tokens or {})
        self._all_components = list(all_components) if all_components is not None else [self.rootComponent]

    @property
    def allOccurrences(self):
        return list(self.rootComponent.allOccurrences)

    @property
    def allComponents(self):
        return list(self._all_components)

    def findEntityByToken(self, token):
        e = self._tokens.get(token)
        return [e] if e is not None else []


def make_design(bodies=(), occurrences=(), tokens=None, comp=None, all_components=None):
    """Build a standard FakeDesign. Use `comp=` to supply a tool-specific component (one carrying a
    fake `features`/`exportManager`/… surface); otherwise a plain MakeComp(bodies, occurrences)."""
    if comp is None:
        comp = MakeComp(bodies=bodies, occurrences=occurrences)
    return MakeDesign(comp=comp, tokens=tokens, all_components=all_components)


def install(mod, design, *, cast_design=True, object_collection=True):
    """Wire `design` into a tool module under both seams, the way every _install() did by hand.

    Patches: mod.app (+ mod._common.app), and — when present — mod._inputs._common.design /
    target_component, plus adsk.fusion.Design.cast (pass-through for our design) and
    adsk.core.ObjectCollection.create (a minimal counted collection). All of these are reverted to
    pristine after each test by the autouse fixture, so the patch can't leak.

    Returns `design` for convenience (e.g. `design = install(mod, make_design(...))`).
    """
    import adsk.fusion
    import adsk.core

    comp = getattr(design, "rootComponent", None)
    target = lambda _d=None: getattr(design, "activeComponent", None) or comp

    mod.app = types.SimpleNamespace(activeProduct=design)
    # The tool's own seam (it imported _common; some import `target_component` bare too).
    if hasattr(mod, "_common"):
        mod._common.app = mod.app
        mod._common.design = lambda: design
        mod._common.target_component = target
    # The input-kinds seam (BodyRef/PlaneRef/AxisRef/GeometryHandle resolve through _inputs._common).
    if hasattr(mod, "_inputs") and hasattr(mod._inputs, "_common"):
        mod._inputs._common.design = lambda: design
        mod._inputs._common.target_component = target
    # A tool that imported `target_component`/`design` bare as module globals (e.g. model_fillet_chamfer).
    if hasattr(mod, "target_component"):
        mod.target_component = target
    if hasattr(mod, "design"):
        mod.design = lambda: design

    if cast_design:
        adsk.fusion.Design.cast = lambda x: x if x is design or isinstance(x, MakeDesign) else None
    if object_collection:
        adsk.core.ObjectCollection.create = staticmethod(_make_object_collection)
    return design


class _FakeObjectCollection:
    def __init__(self):
        self._items = []
    def add(self, x):
        self._items.append(x)
    @property
    def count(self):
        return len(self._items)
    def __iter__(self):
        return iter(self._items)
    def item(self, i):
        return self._items[i]


def _make_object_collection():
    return _FakeObjectCollection()


def payload(result):
    """The success JSON body of a tool result; asserts it is NOT an error first."""
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def error_message(result):
    """Assert a tool result IS an error and return its message (for guard assertions)."""
    assert result["isError"] is True, result
    return result["message"]


def assert_no_active_design(mod, handler, **valid_kwargs):
    """A write/read tool that needs a design must return a clean 'no active design' error (not crash)
    when there is none. Point both seams at None, call the handler with otherwise-valid args, and
    assert the error mentions the design. Surfaces tools that skip the guard and NPE instead."""
    if hasattr(mod, "_common"):
        mod._common.design = lambda: None
    if hasattr(mod, "_inputs") and hasattr(mod._inputs, "_common"):
        mod._inputs._common.design = lambda: None
    res = handler(**valid_kwargs)
    msg = error_message(res).lower()
    assert "design" in msg or "document" in msg, res


def rich_read_caller(mod, design):
    """Wire `design` into `mod` (both seams, via install) and return a caller that invokes the tool's
    handler and returns the decoded ok() payload. The one-liner a rich-read test's fixture uses:

        @pytest.fixture
        def call(): return rich_read_caller(dg, make_design(...))
        def test_default(call): assert "mode" in call()           # no include= → orientation slice
        def test_slice(call):   assert "tree" in call(include=["tree"])

    Setup happens INSIDE the fixture, so the autouse restore tears the seam down in scope order — a
    leaked seam is structurally impossible (the cure for the suite's order-dependence; see
    CLAUDE.md 'Tests')."""
    install(mod, design)
    return lambda **kw: payload(mod.handler(**kw))


def assert_unknown_units(handler, units_param="units", **valid_kwargs):
    """A tool taking a unit must reject an unknown one with a clear error (naming the unit), not
    silently scale by a bogus factor. Pass the other valid args; this sets units='furlong'."""
    res = handler(**{**valid_kwargs, units_param: "furlong"})
    msg = error_message(res).lower()
    assert "unit" in msg, res
