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

``load_tool("measure_bounding_box")`` does both and returns the module so a
test can call its ``handler(...)`` and private helpers directly.

The mocks here are deliberately small: they implement only what a tool actually
touches. Extend them as you add tests for more tools — that is the intended
workflow, mirroring the bootstrap kit's ``mock_adsk.py`` philosophy, adapted to
this project's package layout (no ``<prefix>_`` packages, no webapp).
"""

import importlib.util
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
    spec = importlib.util.spec_from_file_location(
        full_name, os.path.join(TOOLS_DIR, f"{module_name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


# Install mocks at collection time, before any test module imports a tool.
install_mock_adsk()


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
    core = sys.modules["adsk.core"]
    fusion = sys.modules["adsk.fusion"]
    cam = sys.modules["adsk.cam"]
    saved = [
        (fusion.Design, "cast", fusion.Design.cast),
        (cam.CAM, "cast", cam.CAM.cast),
        (cam.Operation, "cast", cam.Operation.cast),
        (core.Point3D, "create", core.Point3D.create),
        (core.Vector3D, "create", core.Vector3D.create),
        (core.ValueInput, "createByString", core.ValueInput.createByString),
        (core.ValueInput, "createByReal", core.ValueInput.createByReal),
        (core.ObjectCollection, "create", core.ObjectCollection.create),
    ]
    try:
        yield
    finally:
        for obj, attr, original in saved:
            setattr(obj, attr, original)


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
