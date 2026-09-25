# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The Form world: a component's FormFeatures, one FormFeature with its edit scope, and the
T-spline bodies it holds."""

import live_api_facts as _api_facts

from tests.fakes.design import FakeTimelineObject
from tests.fakes.scaffold import _NamedCollection, fusion_fake


class _AttributeStore:
    """An entity's attributes: add(group, name, value) and itemByName(group, name). `lands` False
    is the add that answers but keeps nothing."""

    def __init__(self, lands=True):
        self._items, self._lands = {}, lands

    def add(self, group, name, value):
        attr = type("Attr", (), {"value": value})()
        if self._lands:
            self._items[(group, name)] = attr
        return attr

    def itemByName(self, group, name):
        return self._items.get((group, name))


@fusion_fake(live_type="TSplineBody", facts=("shape-dump-form-world",))
class FakeTSplineBody:
    """One T-spline body: its name and the TSM text getTSMDescription hands back. `rename` maps a
    requested name to the one that lands - a dedupe when it differs."""

    def __init__(self, name="Body1", tsm="", parent=None, rename=None):
        self._name = name
        self._rename = rename
        self._tsm = tsm
        self.parentFormFeature = parent
        self.entityToken = "tb-" + name

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = self._rename(value) if self._rename else value

    def getTSMDescription(self):
        return self._tsm


@fusion_fake(live_type="TSplineBodies", facts=("shape-dump-form-world",))
class FakeTSplineBodies:
    """FormFeature.tSplineBodies: the counted walk plus addByTSMDescription, which RAISES the
    measured rejection when `raises` is set and otherwise lands a body whose read-back is
    `readback(text)` - the text itself by default, an exact round trip."""

    def __init__(self, bodies=(), raises=None, readback=None, owner=None, rename=None):
        self._bodies = list(bodies)
        self._raises = raises
        self._readback = readback
        self._owner = owner
        self._rename = rename
        self._loads = []

    @property
    def count(self):
        return len(self._bodies)

    def item(self, i):
        return _NamedCollection(self._bodies).item(i)

    def itemByName(self, name):
        return _NamedCollection(self._bodies).itemByName(name)

    def addByTSMDescription(self, text):
        self._loads.append(text)
        if self._owner is not None and not self._owner._open:
            raise RuntimeError("3 : FormFeature is not active")
        if self._raises:
            raise RuntimeError(self._raises)
        back = self._readback(text) if callable(self._readback) else text
        body = FakeTSplineBody("Body%d" % (len(self._bodies) + 1), back, self._owner,
                               self._rename)
        self._bodies.append(body)
        return body


@fusion_fake(live_type="FormFeature", facts=("shape-dump-form-world",))
class FakeFormFeature:
    """One Form and its EDIT SCOPE: startEdit/finishEdit answer the bool the caller gates on (or
    raise the given text), `on_finish` stands for the conversion that fills `bodies`, and
    deleteMe takes the Form's row off the timeline it was added to. While the edit is open the
    component's design reads designType direct and its timeline raises, as measured; a finishEdit
    that RAISES leaves the edit open (measured) - `raise_closes=True` drives the closing branch."""

    def __init__(self, name="Form1", component=None, start_ok=True, finish_ok=True,
                 delete_ok=True, bodies=(), tspline_bodies=None, on_finish=None, health=None,
                 message="", suppressed=False, attributes=None, raise_closes=False):
        self.name = name
        self.objectType = "adsk::fusion::FormFeature"
        self.parentComponent = component
        self.entityToken = "ff-" + name
        self.healthState = (_api_facts.ENUMS["fusion.FeatureHealthStates"][
            "HealthyFeatureHealthState"] if health is None else health)
        self.errorOrWarningMessage = message
        self.isSuppressed = suppressed
        self.bodies = _NamedCollection(list(bodies))
        self.tSplineBodies = (FakeTSplineBodies(owner=self) if tspline_bodies is None
                              else tspline_bodies)
        self.attributes = _AttributeStore() if attributes is None else attributes
        self.timelineObject = None
        self._start_ok, self._finish_ok, self._delete_ok = start_ok, finish_ok, delete_ok
        self._on_finish = on_finish
        self._raise_closes = raise_closes
        self._timeline = None
        self._open = False
        self._starts = self._finishes = self._deletes = 0

    def _scope(self, is_open):
        """Open or close the edit on the component's design, when the Form has one."""
        self._open = is_open
        design = getattr(self.parentComponent, "parentDesign", None)
        if design is None:
            return
        types = _api_facts.ENUMS["fusion.DesignTypes"]
        design.designType = types["DirectDesignType" if is_open else "ParametricDesignType"]
        timeline = getattr(design, "timeline", None)
        if timeline is not None:
            timeline._raises = "3 : this is not a parametric design" if is_open else None

    def startEdit(self):
        self._starts += 1
        if isinstance(self._start_ok, str):
            raise RuntimeError(self._start_ok)
        if self._start_ok:
            self._scope(True)
        return self._start_ok

    def finishEdit(self):
        self._finishes += 1
        if isinstance(self._finish_ok, str):
            if self._raise_closes:
                self._scope(False)
            raise RuntimeError(self._finish_ok)
        if self._finish_ok:
            self._scope(False)
            if self._on_finish is not None:
                self._on_finish(self)
        return self._finish_ok

    def deleteMe(self):
        self._deletes += 1
        if self._delete_ok and self._timeline is not None:
            self._timeline._items = [o for o in self._timeline._items if o.entity is not self]
        return self._delete_ok


@fusion_fake(live_type="FormFeatures", facts=("shape-dump-form-world",))
class FakeFormFeatures:
    """component.features.formFeatures: add() answers `made` (or a fresh Form) and puts its row on
    `timeline` at the marker, before any rolled-back rows (measured); a Form whose edit is open is
    missing from count/item/itemByName, as measured."""

    def __init__(self, forms=(), made=None, timeline=None, component=None):
        self._forms = list(forms)
        self._made = made
        self._timeline = timeline
        self._component = component
        self._adds = 0

    def _visible(self):
        return [f for f in self._forms if not getattr(f, "_open", False)]

    @property
    def count(self):
        return len(self._visible())

    def item(self, i):
        return _NamedCollection(self._visible()).item(i)

    def itemByName(self, name):
        return _NamedCollection(self._visible()).itemByName(name)

    def add(self):
        self._adds += 1
        ff = self._made if self._made is not None else FakeFormFeature(
            "Form%d" % (len(self._forms) + 1), component=self._component)
        if ff.parentComponent is None:
            ff.parentComponent = self._component
        self._forms.append(ff)
        if self._timeline is not None:
            tl = ff._timeline = self._timeline
            at = min(tl._marker, len(tl._items))
            row = FakeTimelineObject(name=ff.name, index=at, entity=ff)
            ff.timelineObject = row
            tl._items.insert(at, row)
            for i, later in enumerate(tl._items[at + 1:], at + 1):
                later.index = i
            tl._marker = at + 1
        return ff
