"""Unit tests for ``_joints.py`` - the shared joint substrate: the by-name resolution every joint
tool edits/drives/links through (the list form and the resolve-one that refuses a name several
joints carry), and the Joint-Origin leaf ops whose root test decides between "already in assembly
context" and "must be proxied into its occurrence".
"""

from conftest import load_tool

jt = load_tool("_joints")


class _Coll:
    """A Fusion collection: count/item (the walk) plus itemByName."""

    def __init__(self, items):
        self._i = list(items)

    @property
    def count(self):
        return len(self._i)

    def item(self, i):
        return self._i[i]

    def itemByName(self, name):
        return next((x for x in self._i if x.name == name), None)


def _joint(name, token, comp_name):
    """A joint the way the live walk reads one: its own entityToken (the key that collapses the two
    root proxies) and the parentComponent whose name a refusal names."""
    return type("J", (), {"name": name, "entityToken": token,
                          "parentComponent": type("C", (), {"name": comp_name})()})()


def _comp(joints=(), asbuilt=()):
    return type("Comp", (), {"joints": _Coll(joints), "asBuiltJoints": _Coll(asbuilt)})()


def _design(root, subs=()):
    return type("D", (), {"rootComponent": root, "allComponents": list(subs)})()


# ── find_joints_by_name: the list form (never grab the first) ────────────────

class TestFindJointsByName:
    def test_no_hit_is_empty(self):
        des = _design(_comp([_joint("Revolute1", "t1", "Root")]))
        assert jt.find_joints_by_name(des, "Ghost") == []

    def test_one_hit(self):
        j = _joint("Revolute1", "t1", "Root")
        assert jt.find_joints_by_name(_design(_comp([j])), "Revolute1") == [j]

    def test_every_hit_across_components_is_listed(self):
        a = _joint("Revolute1", "t1", "Arm")
        b = _joint("Revolute1", "t2", "Gripper")
        des = _design(_comp([a]), [_comp([b])])
        assert jt.find_joints_by_name(des, "Revolute1") == [a, b]

    def test_an_as_built_joint_counts_as_a_hit(self):
        # asBuiltJoints is a separate collection; a name shared across the two is still shared.
        a = _joint("Fix1", "t1", "Root")
        b = _joint("Fix1", "t2", "Arm")
        des = _design(_comp([a]), [_comp(asbuilt=[b])])
        assert jt.find_joints_by_name(des, "Fix1") == [a, b]

    def test_a_blank_name_matches_nothing(self):
        des = _design(_comp([_joint("Revolute1", "t1", "Root")]))
        assert jt.find_joints_by_name(des, "   ") == []

    def test_the_match_is_exact_not_a_substring(self):
        des = _design(_comp([_joint("Revolute10", "t1", "Root")]))
        assert jt.find_joints_by_name(des, "Revolute1") == []


# ── find_joint: one resolves, several refuse ────────────────────────────────

class TestFindJoint:
    def test_one_hit_resolves(self):
        j = _joint("Revolute1", "t1", "Arm")
        des = _design(_comp([j]), [_comp([_joint("Revolute2", "t2", "Gripper")])])
        assert jt.find_joint(des, "Revolute1") == (j, None)

    def test_no_hit_is_none_with_no_error(self):
        # A miss carries no error text: each caller words its own not-found message.
        des = _design(_comp([_joint("Revolute1", "t1", "Arm")]))
        assert jt.find_joint(des, "Ghost") == (None, None)

    def test_two_components_sharing_a_name_are_refused_naming_both(self):
        # The boundary: at 2 hits the resolver must refuse. Returning either one silently
        # drives/edits an arbitrary sub-assembly's joint.
        des = _design(_comp([_joint("Revolute1", "t1", "Arm")]),
                      [_comp([_joint("Revolute1", "t2", "Gripper")])])
        j, err = jt.find_joint(des, "Revolute1")
        assert j is None
        assert "2 joints" in err and "Arm" in err and "Gripper" in err and "Revolute1" in err

    def test_a_third_hit_is_counted_and_named(self):
        des = _design(_comp([_joint("R1", "t1", "A")]),
                      [_comp([_joint("R1", "t2", "B")]), _comp([_joint("R1", "t3", "C")])])
        _j, err = jt.find_joint(des, "R1")
        assert "3 joints" in err and "'R1' in C" in err

    def test_an_unreadable_owning_component_is_named_as_unreadable(self):
        class _Blind:
            name = "R1"
            entityToken = "t2"

            @property
            def parentComponent(self):
                raise RuntimeError("no component")
        des = _design(_comp([_joint("R1", "t1", "Arm")]), [_comp([_Blind()])])
        _j, err = jt.find_joint(des, "R1")
        assert "(unreadable component)" in err and "Arm" in err

    def test_one_joint_reached_through_both_root_proxies_is_not_ambiguous(self):
        # allComponents lists the root as a proxy DISTINCT from design.rootComponent, so one root
        # joint is reached twice. Without the shared-entityToken de-dup every root joint would
        # refuse as a pair of itself.
        j = _joint("Revolute1", "t1", "Root")
        des = _design(_comp([j]), [_comp([j])])
        assert jt.find_joint(des, "Revolute1") == (j, None)


# ── the root test behind the JO leaf ops (same_component, not a name compare) ─

class _JO:
    def __init__(self, name="Frame"):
        self.name = name
        self.context = None

    def createForAssemblyContext(self, occ):
        self.context = occ
        return ("proxy", occ)


def _occ(path):
    return type("Occ", (), {"fullPathName": path, "name": path.split("+")[-1]})()


class _Root:
    """A root component wrapper: a name, an entityToken, and the by-component occurrence lookup the
    JO leaf ops proxy through."""

    def __init__(self, occs=(), name="Root", token="ROOT"):
        self.name = name
        self.entityToken = token
        self._occs = list(occs)

    def allOccurrencesByComponent(self, _c):
        return self._occs


class TestJointOriginRootTest:
    def test_a_second_root_wrapper_sharing_the_token_is_the_root(self):
        # Component wrappers are never identity-stable: design.rootComponent and the component a JO
        # reports are two objects sharing ONE entityToken. The JO is already in assembly context, so
        # it is returned as-is - no occurrence proxy, no refusal.
        des = type("D", (), {"rootComponent": _Root()})()
        jo = _JO()
        assert jt.jo_assembly_proxy(des, jo, _Root()) == (jo, None)
        assert jo.context is None
        assert jt.jo_reference_names(des, jo, _Root()) == ["Frame"]

    def test_a_sub_component_carrying_the_root_s_NAME_is_not_the_root(self):
        # A component named like the document's root component (renaming a part after the document
        # is the everyday way to get one) shares the NAME but not the entityToken. Comparing names
        # calls it the root and hands back the NATIVE JO, which Fusion answers with "Provided input
        # paths for joint are not valid"; comparing tokens proxies it into its occurrence.
        occ = _occ("Root:1")
        des = type("D", (), {"rootComponent": _Root([occ])})()
        twin = type("Sub", (), {"name": "Root", "entityToken": "TWIN"})()
        jo = _JO()
        assert jt.jo_assembly_proxy(des, jo, twin) == (("proxy", occ), None)
        assert jt.jo_reference_names(des, jo, twin) == ["Root:1:Frame"]

    def test_a_sub_component_jo_is_proxied_into_its_single_occurrence(self):
        # A DIFFERENT token means a different component: the native JO is refused by Fusion, so it
        # must be proxied into the occurrence that carries the frame.
        occ = _occ("Arm:1")
        des = type("D", (), {"rootComponent": _Root([occ])})()
        sub = type("Sub", (), {"name": "Arm", "entityToken": "ARM"})()
        jo = _JO()
        assert jt.jo_assembly_proxy(des, jo, sub) == (("proxy", occ), None)
        assert jt.jo_reference_names(des, jo, sub) == ["Arm:1:Frame"]

    def test_a_component_instanced_twice_is_refused_with_both_qualified_names(self):
        occs = [_occ("Arm:1"), _occ("Arm:2")]
        des = type("D", (), {"rootComponent": _Root(occs)})()
        sub = type("Sub", (), {"name": "Arm", "entityToken": "ARM"})()
        obj, err = jt.jo_assembly_proxy(des, _JO(), sub)
        assert obj is None and "instanced 2 times" in err
        assert jt.jo_reference_names(des, _JO(), sub) == ["Arm:1:Frame", "Arm:2:Frame"]
