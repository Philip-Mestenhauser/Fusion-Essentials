"""Unit tests for the matured ``joint.py`` autonomous geometry-snap resolver.

The MVP joint tool resolved inputs by joint-origin NAME only. The maturation adds
an AUTONOMOUS (no human selection) geometry snap: an input may instead be
'<occurrence>:<snap>' where snap is origin | center | top | bottom | cylinder,
and the tool finds that geometry in the occurrence, builds the matching
JointGeometry, and proxies it into the occurrence's assembly context.

These tests pin the PURE parts — snap-spec parsing (_parse_snap) and the
top/bottom/largest-face selection (_pick_face) — without a live Fusion. The
JointGeometry factory calls + createForAssemblyContext proxying are live-only and
exercised against the running session separately.
"""

from conftest import load_tool

jt = load_tool("joint")


# ── _parse_snap: split '<occurrence>:<snap>' ───────────────────────────────

class TestParseSnap:
    def test_plain_name_is_joint_origin(self):
        # No recognized snap suffix -> treat whole string as a JO name (back-compat).
        occ, snap = jt._parse_snap("Center of Model")
        assert occ is None and snap is None

    def test_occurrence_with_snap(self):
        occ, snap = jt._parse_snap("Boom:1:origin")
        assert occ == "Boom:1" and snap == "origin"

    def test_occurrence_with_top_snap(self):
        occ, snap = jt._parse_snap("TrussMast:1:top")
        assert occ == "TrussMast:1" and snap == "top"

    def test_cylinder_snap(self):
        occ, snap = jt._parse_snap("Cable:1:cylinder")
        assert occ == "Cable:1" and snap == "cylinder"

    def test_unknown_suffix_not_treated_as_snap(self):
        # 'A:1:wobble' — 'wobble' is not a snap keyword, so not a snap spec.
        occ, snap = jt._parse_snap("A:1:wobble")
        assert occ is None and snap is None

    def test_occurrence_colon_in_name_without_snap(self):
        # 'Mast:1' is a normal occurrence name (the ':1' is the instance), NOT a snap.
        occ, snap = jt._parse_snap("Mast:1")
        assert occ is None and snap is None


# ── _pick_face: directional faces (top/bottom/left/right/front/back) + center ──

class _Pt:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class _BBox:
    def __init__(self, mn, mx):
        self.minPoint = _Pt(*mn)
        self.maxPoint = _Pt(*mx)


class _Face:
    """Z-only convenience ctor kept for existing tests; full-XYZ via from_box()."""
    def __init__(self, name, zmin, zmax, area=1.0, is_planar=True):
        self.name = name
        self.boundingBox = _BBox((0.0, 0.0, zmin), (0.0, 0.0, zmax))
        self.area = area
        self.geometry = type("G", (), {"surfaceType": 0 if is_planar else 5})()

    @classmethod
    def from_box(cls, name, mn, mx, area=1.0, is_planar=True):
        f = cls.__new__(cls)
        f.name = name
        f.boundingBox = _BBox(mn, mx)
        f.area = area
        f.geometry = type("G", (), {"surfaceType": 0 if is_planar else 5})()
        return f


class _Faces:
    def __init__(self, faces):
        self._f = list(faces)

    @property
    def count(self):
        return len(self._f)

    def item(self, i):
        return self._f[i]

    def __iter__(self):
        return iter(self._f)


class TestPickFace:
    def _body(self):
        return _Faces([
            _Face("bottom", 0.0, 0.0, area=4.0),
            _Face("top", 2.0, 2.0, area=4.0),
            _Face("side", 0.0, 2.0, area=8.0),
        ])

    def test_top_picks_highest_face(self):
        f = jt._pick_face(self._body(), "top")
        assert f is not None and f.name == "top"

    def test_bottom_picks_lowest_face(self):
        f = jt._pick_face(self._body(), "bottom")
        assert f is not None and f.name == "bottom"

    def test_center_picks_largest_planar_face(self):
        # 'center' uses the largest planar face; here the side (area 8) is largest...
        # but 'side' is planar in this fake. Use a body where the largest is non-planar:
        body = _Faces([
            _Face("cap", 0.0, 0.0, area=3.0, is_planar=True),
            _Face("wall", 0.0, 2.0, area=9.0, is_planar=False),
        ])
        f = jt._pick_face(body, "center")
        assert f is not None and f.name == "cap"   # skips the bigger non-planar wall

    def test_top_bottom_skip_nonplanar_cylinder_wall(self):
        # THE CABLE BUG: a cylinder's curved side wall spans the whole height, so by raw
        # Z it would beat the flat end caps for both top and bottom — but createByPlanarFace
        # needs a PLANAR face, so top/bottom must skip the non-planar wall.
        body = _Faces([
            _Face("bottom_cap", 0.0, 0.0, area=1.0, is_planar=True),
            _Face("top_cap", 5.0, 5.0, area=1.0, is_planar=True),
            _Face("wall", -0.5, 5.5, area=20.0, is_planar=False),  # extends beyond both caps
        ])
        assert jt._pick_face(body, "top").name == "top_cap"
        assert jt._pick_face(body, "bottom").name == "bottom_cap"


class TestDirectionalFaces:
    """The 6 box faces by normal direction — needed to fully locate a part with constraints."""

    def _box(self):
        # a 10x10x10 box centered at origin; six axis-aligned faces.
        B = _Face.from_box
        return _Faces([
            B("right",  (5, -5, -5),  (5, 5, 5),   area=100),   # +X
            B("left",   (-5, -5, -5), (-5, 5, 5),   area=100),   # -X
            B("back",   (-5, 5, -5),  (5, 5, 5),    area=100),   # +Y
            B("front",  (-5, -5, -5), (5, -5, 5),   area=100),   # -Y
            B("top",    (-5, -5, 5),  (5, 5, 5),    area=100),   # +Z
            B("bottom", (-5, -5, -5), (5, 5, -5),   area=100),   # -Z
        ])

    def test_right_is_max_x(self):
        assert jt._pick_face(self._box(), "right").name == "right"

    def test_left_is_min_x(self):
        assert jt._pick_face(self._box(), "left").name == "left"

    def test_back_is_max_y(self):
        assert jt._pick_face(self._box(), "back").name == "back"

    def test_front_is_min_y(self):
        assert jt._pick_face(self._box(), "front").name == "front"

    def test_top_is_max_z(self):
        assert jt._pick_face(self._box(), "top").name == "top"

    def test_bottom_is_min_z(self):
        assert jt._pick_face(self._box(), "bottom").name == "bottom"

    def test_directional_snaps_parse(self):
        for kw in ("left", "right", "front", "back"):
            occ, snap = jt._parse_snap(f"Part:1:{kw}")
            assert occ == "Part:1" and snap == kw

    def test_empty_body_returns_none(self):
        assert jt._pick_face(_Faces([]), "top") is None
