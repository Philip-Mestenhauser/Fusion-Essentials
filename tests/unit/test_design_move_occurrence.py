"""Unit tests for ``design_move_occurrence.py`` - re-parent a component instance.

The fake carries the MEASURED contract of ``Occurrence.moveToComponent``, and each clause of it is a
way this tool can fail to move anything at all:
  - the argument is an OCCURRENCE; a Component - the root component included - raises the SWIG type
    error, so passing ``into_occ.component`` moves nothing and there is no top-level target;
  - ``sourceComponent`` reads the ROOT for every occurrence however deeply nested, so it can never
    answer "where does this instance sit now" - the current parent comes from the assembly PATH;
  - the call returns a NEW proxy whose fullPathName is the true assembly path, while the wrapper the
    caller held keeps answering with the path it had at mint time.
Also pinned: the world position the re-parent is measured to keep (evidence, not a claim), the
self-nesting refusal, and the swallowed no-op (a call that reports success while the tree is
unchanged) turned into an error.
"""

from types import SimpleNamespace

import pytest

from conftest import (FakeBoundingBox3D, FakePoint, MakeComp, MakeDesign, error_message, install,
                      load_tool, payload)

mo = load_tool("design_move_occurrence")


# ── fake assembly ────────────────────────────────────────────────────────────
#
# A component knows its name + entityToken (same_component compares tokens - component wrappers are
# never identity-stable) and the allOccurrences subtree view the cycle guard reads. Each occurrence
# record carries its parent, so a path is the chain; re-parenting one recomputes every path beneath.


def _comp(name):
    c = MakeComp(name=name)
    c.entityToken = "tok:" + name
    c.allOccurrences = []
    return c


def _path(node):
    return f"{_path(node.parent)}+{node.name}" if node.parent is not None else node.name


def _proxy(des, node):
    """One occurrence WRAPPER, as the API hands them out: every read of the tree mints a fresh one,
    carrying a SNAPSHOT of the path it had when it was minted - which is why a wrapper held across a
    move keeps answering with its pre-move path (measured: it goes stale silently, never raising).

    sourceComponent is the ROOT for every occurrence however deep (measured) - it names where the
    path BEGINS, not the immediate parent."""
    p = SimpleNamespace(component=node.component, name=node.name, fullPathName=_path(node),
                        isValid=True, sourceComponent=des.rootComponent)
    p.boundingBox2 = (lambda types, n=node: None if n.corner is None else
                      FakeBoundingBox3D(FakePoint(*n.corner), FakePoint(*n.corner)))
    p.moveToComponent = lambda target, n=node: _move(des, n, target)
    return p


def _refresh(des):
    des.rootComponent.allOccurrences = [_proxy(des, n) for n in des.tree]
    for comp in des.comps.values():
        if comp is des.rootComponent:
            continue
        inside = []
        for node in des.tree:
            anc = node.parent
            while anc is not None:
                if anc.component is comp:
                    inside.append(node)
                    break
                anc = anc.parent
        comp.allOccurrences = [_proxy(des, n) for n in inside]


def _instance(des, comp, parent=None, corner=None):
    des.counter[comp.name] = des.counter.get(comp.name, 0) + 1
    node = SimpleNamespace(component=comp, parent=parent, corner=corner,
                           name=f"{comp.name}:{des.counter[comp.name]}")
    des.tree.append(node)
    _refresh(des)
    return node


_TYPE_ERROR = ("in method 'Occurrence_moveToComponent', argument 2 of type "
               "'adsk::core::Ptr< adsk::fusion::Occurrence > const &'")


def _move(des, node, target):
    # MEASURED: the argument is an OCCURRENCE. A Component - the root component included - raises
    # this SWIG type error and moves nothing, which is what a tool passing into_occ.component hits.
    if not hasattr(target, "fullPathName"):
        raise TypeError(_TYPE_ERROR)
    des.moved_into = target.fullPathName
    if des.refuse == "raise":
        raise RuntimeError("the occurrence is locked")
    if des.refuse == "none":
        return None
    if des.refuse == "silent":
        return _proxy(des, node)                 # a live-looking proxy over an unchanged tree
    parent = next((n for n in des.tree if _path(n) == target.fullPathName), None)
    node.parent = parent
    # An occurrence lives in its parent's COMPONENT, so it appears under EVERY instance of that
    # component. Measured on the ADD side ('Frame:1+Bolt:2' and 'Frame:2+Bolt:2' from one call); the
    # move side carries the same shape here, and the sweep act confirms it live.
    for other in [n for n in list(des.tree)
                  if parent is not None and n.component is parent.component and n is not parent]:
        des.tree.append(SimpleNamespace(component=node.component, parent=other, corner=node.corner,
                                        name=node.name))
    if des.drift:
        node.corner = tuple(c + des.drift for c in (node.corner or (0.0, 0.0, 0.0)))
    _refresh(des)
    return _proxy(des, node)                     # the new proxy: its path IS the assembly path


def _make(names=("A", "B"), refuse=None, drift=0.0, corner=(1.0, 2.0, 3.0)):
    """A design with one root instance per named component. 'refuse' models the failures the
    read-back exists to catch; 'drift' moves the part in world space during the re-parent."""
    root = _comp("Root")
    des = MakeDesign(comp=root)
    des.tree, des.counter, des.comps = [], {}, {"Root": root}
    des.refuse, des.drift, des.moved_into = refuse, drift, None
    for name in names:
        des.comps[name] = _comp(name)
        _instance(des, des.comps[name], corner=corner)
    des._all_components = list(des.comps.values())
    _refresh(des)
    return des


@pytest.fixture
def wire():
    def _install(**kw):
        des = _make(**kw)
        install(mo, des)
        return des
    return _install


def _paths(des):
    return sorted(o.fullPathName for o in des.rootComponent.allOccurrences)


# ── the re-parent, read back off the tree ────────────────────────────────────

class TestReParent:
    def test_moves_into_the_target_and_publishes_both_paths(self, wire):
        des = wire()
        out = payload(mo.handler(occurrence="A:1", into_component="B:1"))
        assert out["moved"] is True and out["changed"] is True
        assert out["previous_path"] == "A:1"
        assert out["full_path"] == "B:1+A:1"
        assert _paths(des) == ["B:1", "B:1+A:1"]

    def test_passes_the_target_OCCURRENCE_never_its_component(self, wire):
        # measured: moveToComponent takes an Occurrence - the fake raises the SWIG type error on a
        # Component, so a handler passing into_occ.component cannot get past this call.
        des = wire()
        payload(mo.handler(occurrence="A:1", into_component="B:1"))
        assert des.moved_into == "B:1"                     # the occurrence path, not 'B'

    def test_the_fake_refuses_a_component_argument(self, wire):
        # the guard above is only worth something if the fake really does raise on a Component.
        des = wire()
        occ = des.rootComponent.allOccurrences[0]
        with pytest.raises(TypeError, match="Ptr< adsk::fusion::Occurrence >"):
            occ.moveToComponent(des.comps["B"])
        with pytest.raises(TypeError):
            occ.moveToComponent(des.rootComponent)

    def test_a_nested_instance_moves_to_a_DIFFERENT_parent(self, wire):
        # the multi-segment case: the parent read is path arithmetic, and 'B:1+A:1' -> 'C:1' is a
        # real move, not the no-op branch.
        des = wire(names=("A", "B", "C"))
        des.tree[0].parent = des.tree[1]                          # nested: B:1+A:1
        _refresh(des)
        out = payload(mo.handler(occurrence="B:1+A:1", into_component="C:1"))
        assert out["changed"] is True
        assert out["previous_path"] == "B:1+A:1" and out["full_path"] == "C:1+A:1"
        assert _paths(des) == ["B:1", "C:1", "C:1+A:1"]

    def test_the_published_path_is_the_target_instance_that_was_NAMED(self, wire):
        # a target component with two instances: the move lands a path under each, and the answer is
        # the one the caller asked for - which is what the RETURNED proxy reports, not whichever path
        # sorts first. (The one-path-per-instance shape is measured on the ADD side; the sweep act
        # confirms it for a move.)
        des = wire(names=("A", "B"))
        _instance(des, des.comps["B"])                            # a second B
        out = payload(mo.handler(occurrence="A:1", into_component="B:2"))
        assert out["full_path"] == "B:2+A:1"                      # not the first-sorted 'B:1+A:1'
        assert out["paths"] == ["B:1+A:1", "B:2+A:1"]
        assert des.moved_into == "B:2"

    def test_children_follow_and_the_deepest_path_is_not_mistaken_for_the_moved_one(self, wire):
        des = wire()
        child = _instance(des, _comp("C"), parent=des.tree[0])   # A:1+C:1
        des.comps["C"] = child.component
        des._all_components.append(child.component)
        out = payload(mo.handler(occurrence="A:1", into_component="B:1"))
        assert out["full_path"] == "B:1+A:1"
        assert out["paths"] == ["B:1+A:1", "B:1+A:1+C:1"]
        assert "B:1+A:1+C:1" in _paths(des)

    def test_declared_outputs_present(self, wire):
        wire()
        out = payload(mo.handler(occurrence="A:1", into_component="B:1"))
        for r in mo.RETURNS:
            assert r.assert_present(out) == "", r.key


# ── no root target: the direction the API does not offer ─────────────────────

class TestNoRootTarget:
    def test_an_empty_target_refuses_and_says_why_root_is_unreachable(self, wire):
        des = wire()
        des.tree[0].parent = des.tree[1]                          # nested: B:1+A:1
        _refresh(des)
        msg = error_message(mo.handler(occurrence="B:1+A:1", into_component=""))
        assert "TOP LEVEL" in msg and "root component is not" in msg
        assert des.moved_into is None                             # nothing was attempted
        assert _paths(des) == ["B:1", "B:1+A:1"]

    def test_the_word_root_is_refused_the_same_way(self, wire):
        des = wire()
        assert "TOP LEVEL" in error_message(mo.handler(occurrence="A:1", into_component="ROOT"))
        assert des.moved_into is None


# ── the stale handle: nothing is read off the pre-move occurrence ────────────

class TestStaleHandle:
    def test_the_pre_move_wrapper_still_reads_its_OLD_path_and_is_not_published(self, wire):
        # measured: the held wrapper keeps answering with the path it had - silently, no raise. A
        # payload built from post-move reads of it would report the move as having gone nowhere.
        des = wire()
        held = des.rootComponent.allOccurrences[0]
        out = payload(mo.handler(occurrence="A:1", into_component="B:1"))
        assert held.fullPathName == "A:1"                 # the stale wrapper, still answering
        assert out["occurrence"] == "A:1"
        assert out["previous_path"] == "A:1"
        assert out["full_path"] == "B:1+A:1"              # read off the RETURNED proxy / the tree


# ── world position: the measured contract, verified rather than asserted ─────

class TestWorldPosition:
    def test_an_unmoved_part_reports_the_position_preserved(self, wire):
        wire()
        out = payload(mo.handler(occurrence="A:1", into_component="B:1"))
        assert out["world_position_preserved"] is True
        assert "position_warning" not in out

    def test_a_part_that_drifted_is_reported_not_hidden(self, wire):
        wire(drift=0.5)
        out = payload(mo.handler(occurrence="A:1", into_component="B:1"))
        assert out["world_position_preserved"] is False
        assert "5.0 mm" in out["position_warning"]

    def test_no_body_geometry_reports_unknown_rather_than_preserved(self, wire):
        wire(corner=None)
        out = payload(mo.handler(occurrence="A:1", into_component="B:1"))
        assert out["world_position_preserved"] is None
        assert "position_warning" not in out


# ── honesty ──────────────────────────────────────────────────────────────────

class TestHonesty:
    def test_an_unchanged_tree_is_an_error_not_a_false_ok(self, wire):
        wire(refuse="silent")
        msg = error_message(mo.handler(occurrence="A:1", into_component="B:1"))
        assert "the assembly is unchanged" in msg and "A:1" in msg

    def test_nothing_returned_is_an_error(self, wire):
        wire(refuse="none")
        assert "returned nothing" in error_message(mo.handler(occurrence="A:1", into_component="B:1"))

    def test_a_raising_move_reports_its_reason(self, wire):
        wire(refuse="raise")
        msg = error_message(mo.handler(occurrence="A:1", into_component="B:1"))
        assert "Could not move 'A:1'" in msg and "locked" in msg

    def test_moving_where_it_already_sits_changes_nothing(self, wire):
        # the current parent comes from the PATH ('B:1+A:1' -> 'B:1'), the one read sourceComponent
        # cannot give: it reads the ROOT for every occurrence, so it would call every move a no-op.
        des = wire()
        des.tree[0].parent = des.tree[1]                          # nested: B:1+A:1
        _refresh(des)
        out = payload(mo.handler(occurrence="B:1+A:1", into_component="B:1"))
        assert out["changed"] is False and out["full_path"] == "B:1+A:1"
        assert des.moved_into is None                  # the mutation was never called


# ── the self-nesting refusal ─────────────────────────────────────────────────

class TestSelfNestingRefused:
    def test_moving_an_instance_into_its_own_component_is_refused(self, wire):
        des = wire()
        _instance(des, des.comps["A"])                  # a second instance: A:2
        msg = error_message(mo.handler(occurrence="A:1", into_component="A:2"))
        assert "instance of itself" in msg
        assert des.moved_into is None

    def test_moving_into_its_own_descendant_is_refused(self, wire):
        des = wire()
        child = _comp("C")
        des.comps["C"] = child
        des._all_components.append(child)
        _instance(des, child, parent=des.tree[0])       # A:1+C:1
        msg = error_message(mo.handler(occurrence="A:1", into_component="A:1+C:1"))
        assert "sits inside it" in msg
        assert des.moved_into is None

    def test_an_unrelated_target_is_not_refused(self, wire):
        des = wire()
        payload(mo.handler(occurrence="A:1", into_component="B:1"))
        assert des.moved_into == "B:1"


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_missing_occurrence_refused(self, wire):
        wire()
        assert "'occurrence' is required" in error_message(
            mo.handler(occurrence="", into_component="B:1"))

    def test_unknown_occurrence_refused(self, wire):
        wire()
        assert "no occurrence matching" in error_message(
            mo.handler(occurrence="Ghost", into_component="B:1"))

    def test_ambiguous_occurrence_refused_without_moving(self, wire):
        des = wire()
        _instance(des, des.comps["A"], parent=des.tree[1])    # B:1+A:2 - 'A' now matches two
        assert "ambiguous" in error_message(mo.handler(occurrence="A", into_component="B:1")).lower()
        assert des.moved_into is None

    def test_unknown_target_refused_without_moving(self, wire):
        des = wire()
        assert "no occurrence matching" in error_message(
            mo.handler(occurrence="A:1", into_component="Ghost"))
        assert des.moved_into is None

    def test_no_active_design_refused(self, monkeypatch):
        monkeypatch.setattr(mo._common, "design", lambda: None)
        monkeypatch.setattr(mo._inputs._common, "design", lambda: None)
        assert "No active design" in error_message(
            mo.handler(occurrence="A:1", into_component="B:1"))
