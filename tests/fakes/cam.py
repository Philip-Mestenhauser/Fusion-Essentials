# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The CAM worlds: the setup/folder/operation tree, the inspection results, and the job objects."""

import types

import live_api_facts as _api_facts

from tests.fakes.geometry import FakePoint, FakeVector3D
from tests.fakes.scaffold import _NamedCollection, fusion_fake


# ── CAM tree fakes (setup / folder / operation / container / base node) ────
#
# The shared object model for the CAM setup tree every cam_* tool walks. Two load-bearing
# behaviors live here ONCE. `children` is the ONE ordered list a parent keeps every child in, and
# the walk classifies each by CAST - so each child carries the `_cam_kind` marker the cast stubs
# answer from, and a child a collection never lists (a container, a base node) is reachable there
# and nowhere else. allOperations mirrors the measured live flatten (BEHAVIOR flags): folder and
# pattern children are flattened IN while the CONTAINERS are DROPPED.

@fusion_fake(live_type="Operation", facts=("shape-dump-cam-world",))
class FakeOperation:
    """A CAM Operation leaf. Every attribute is real per live_api_facts.SHAPES['Operation'].

    state_readable=False drives the DEFENSIVE branch for an operationState that will not read (the
    _InspPoint shape) - no walk of a live document here has answered one, so it is a guard rather
    than a measured live shape. Callers read the member through safe() with no default, so the facts
    carry operation_state None - every value operationState CAN answer is itself a state, so a
    coerced default would publish one of them off a read that never happened."""
    def __init__(self, name, has_toolpath=True, valid=True, suppressed=False, shown=False,
                 operation_state=0, has_error=False, error="", has_warning=False, warning="",
                 state_readable=True, strategy="contour2d", parameters=None, tool=None,
                 parent_shown=True):
        self.name = name
        # An operation with no `parameters` given still answers a collection - the read every CAM
        # tool makes; `tool` unset answers None, the empty read a tool row has to survive.
        self.parameters = FakeCAMParameters() if parameters is None else parameters
        self.tool = tool
        # Operation.strategy - 'manual' is the Manual NC pass-through, which carries no toolpath by
        # construction and is excluded from the empty-toolpath class.
        self.strategy = strategy
        self.hasToolpath = has_toolpath
        self.isToolpathValid = valid
        self.isSuppressed = suppressed
        self.isLightBulbOn = shown
        self.hasError = has_error
        self.error = error
        self.hasWarning = has_warning
        self.warning = warning
        self.isGenerating = False
        self.generatingProgress = None
        self._operation_state = operation_state
        self._state_readable = state_readable
        # isVisible is the ACTUAL on-screen state (measured): this op's own bulb AND its parent's -
        # parent_shown stands in for an ancestor folder/setup being lit, default True (no parent
        # hiding it).
        self._parent_shown = parent_shown

    @property
    def operationState(self):
        if not self._state_readable:
            raise RuntimeError("operationState cannot be read on this operation")
        return self._operation_state

    @property
    def isVisible(self):
        return bool(self.isLightBulbOn) and bool(self._parent_shown)


class _FolderCollection(_NamedCollection):
    """A CAMFolder's or Setup's .folders: count/item/itemByName plus addFolder, which stamps the
    new child's .parent to the collection's OWNER (measured) - CAMFolders itself has no live SHAPES
    dump to sweep against (test_fake_shapes_exist)."""

    def __init__(self, items, owner):
        super().__init__(list(items))
        self._owner = owner
        for f in self._items:
            f.parent = owner

    def addFolder(self, name):
        f = FakeCAMFolder(name)
        f.parent = self._owner
        self._items.append(f)
        return f


@fusion_fake(live_type="CAMFolder", facts=("shape-dump-cam-world", "cam-alloperations-shape"))
class FakeCAMFolder:
    """A CAM folder/pattern container: .operations/.folders/.patterns hold the DIRECT children
    (Fusion's count/item protocol) and `children` hands them back as one list - this fake's own
    three collections in construction order then `others`, where live it is the browser's
    interleaved creation order. Use it for a pattern too: one handed in as `patterns=` casts as
    CAMPattern; `others` takes the children no per-kind collection lists at all."""

    _cam_kind = "folder"        # the cast the walk classifies it by (see cam_kind)

    def __init__(self, name, ops=(), folders=(), patterns=(), others=()):
        self.name = name
        self.operations = _NamedCollection(list(ops))
        self.folders = _FolderCollection(folders, self)
        self.patterns = _NamedCollection(list(patterns))
        self._others = list(others)
        # one fake serves the folder and the pattern alike, so the collection a child was handed in
        # under is what re-stamps which of the two it casts as.
        for child in patterns:
            child._cam_kind = "pattern"

    @property
    def children(self):
        """Every direct child, a FRESH read each time (see _NodeRead) - the one list the tree walk
        reads and classifies by cast."""
        return _ChildrenCollection(list(self.operations) + list(self.folders)
                                   + list(self.patterns) + list(self._others))

    @property
    def allOperations(self):
        flat = list(self.operations)
        for coll in (self.folders, self.patterns):
            for child in coll:
                if _api_facts.BEHAVIOR["alloperations_flattens_folder_children"]:
                    flat.extend(child.allOperations)
                if not _api_facts.BEHAVIOR["alloperations_drops_folder_objects"]:
                    flat.append(child)
        # MEASURED: a container's OPERATIONS are in the setup's allOperations while the container
        # itself and the base nodes - which answer no allOperations at all - are not.
        for child in self._others:
            flat.extend(list(getattr(child, "allOperations", ()) or ()))
        return _NamedCollection(flat)


@fusion_fake(live_type="Setup", facts=("shape-dump-cam-world", "cam-alloperations-shape"))
class FakeSetup(FakeCAMFolder):
    """A CAM Setup: the same container protocol + measured allOperations flatten as FakeCAMFolder,
    plus Setup.parameters - the collection the WCS binding is written to and read back from
    (build one with wcs_params below; None models a setup whose parameters do not read) - and the
    activate()/isActive pair. activate() returns a bool AND flips isActive, as the live one does;
    'activate_lies' models it returning true while the setup never becomes active.

    `machine` and the `has_error`/`error` fault pair are set only when given, so a setup whose
    machine or fault channel does not read stays a testable state; live every setup carries all
    three, so an unset one is this fake's DECLARED absence rather than a shape live presents.
    `machine=None` is the measured "no machine assigned" answer (what setup_blockers calls
    no_machine_selected), and `has_error=False` the plain no-fault answer live_readiness reads.
    `operation_type` is unset the same way; pass adsk.cam.OperationTypes.AdditiveOperation for the
    additive exclusions (is_additive_setup). `models` is unset the same way too - a setup whose
    Setup.models does not read (measured: it can raise) stays a testable state; pass a list of
    objects each carrying `entityToken` to model the bodies this setup's stock is defined against.
    `stock_solids` mirrors `models` for Setup.stockSolids - pass a list of objects each carrying
    `boundingBox` to model the setup's stock geometry a camera fit reads."""

    _UNSET = object()

    _cam_kind = "setup"         # a Setup casts as neither Operation nor CAMFolder

    def __init__(self, name, ops=(), folders=(), patterns=(), others=(), parameters=None,
                 is_active=False, activate_ok=True, activate_lies=False, machine=_UNSET,
                 has_error=_UNSET, error=_UNSET, operation_type=_UNSET, models=_UNSET,
                 stock_solids=_UNSET):
        super().__init__(name, ops=ops, folders=folders, patterns=patterns, others=others)
        # Operation.parentSetup names the owning Setup; an op already carrying one keeps it, and a
        # bare sentinel standing in for an op takes no attribute.
        for op in FakeCAMFolder.allOperations.fget(self):
            if getattr(op, "parentSetup", None) is None and hasattr(op, "__dict__"):
                op.parentSetup = self
        self.parameters = parameters
        if machine is not FakeSetup._UNSET:
            self.machine = machine
        if has_error is not FakeSetup._UNSET:
            self.hasError = has_error
        if error is not FakeSetup._UNSET:
            self.error = error
        if operation_type is not FakeSetup._UNSET:
            self.operationType = operation_type
        if models is not FakeSetup._UNSET:
            self.models = models
        if stock_solids is not FakeSetup._UNSET:
            self.stockSolids = stock_solids
        self.isActive = is_active
        self._activate_ok = activate_ok
        self._activate_lies = activate_lies
        self._activate_calls = 0     # bookkeeping, private: the live Setup has no such member

    def activate(self):
        self._activate_calls += 1
        if self._activate_ok and not self._activate_lies:
            self.isActive = True
        return self._activate_ok


@fusion_fake(live_type="CAMAdditiveContainer", facts=("shape-cam-additive-container",))
class _AdditiveContainer:
    """adsk::cam::CAMAdditiveContainer - what an additive strategy folder lands as, holding its
    operations in `children` / `allOperations` ALONE: operations, folders, patterns and hasToolpath
    are ABSENT on it (a reach raises AttributeError, as live), so this fake carries none."""

    _cam_kind = "container"

    def __init__(self, name, strategy="additive_support_folder", ops=(), parent=None,
                 suppressed=False):
        self.name = name
        self.strategy = strategy
        self.isSuppressed = suppressed
        self.parent = parent            # live: the SETUP's name, read off the container
        self._ops = list(ops)

    @property
    def children(self):
        return _ChildrenCollection(list(self._ops))

    @property
    def allOperations(self):
        return _NamedCollection(list(self._ops))


@fusion_fake(live_type="OperationBase", facts=("shape-cam-additive-container",))
class _BaseNode:
    """A bare adsk::cam::OperationBase - where hole recognition and the additive individual
    strategies land: a name, a strategy and the suppression flag; `children` and `parent` are
    ABSENT on it (a reach raises AttributeError, as live), so this fake carries neither."""

    _cam_kind = "base"

    def __init__(self, name, strategy="hole_recognition", suppressed=False):
        self.name = name
        self.strategy = strategy
        self.isSuppressed = suppressed


def cam_kind(obj):
    """The CAM type a fake stands for when the walk CASTS it - 'folder' / 'pattern' / 'container' /
    'base', off the `_cam_kind` marker its parent stamped or it declares. None for an operation and
    for anything else, which is what makes Operation.cast the only cast that answers for those."""
    return getattr(obj, "_cam_kind", None)


def cam_cast(kind):
    """adsk.cam.<CAMFolder|CAMPattern|CAMAdditiveContainer>.cast over the shared fakes: the fake
    standing for `kind` passes through and every other object casts None, as live refuses a type
    that is not its own."""
    def cast(obj):
        return obj if cam_kind(obj) == kind else None
    return cast


def operation_cast(obj):
    """adsk.cam.Operation.cast over the shared fakes: a container, a folder, a pattern or a base
    node answers None the way live does, and everything else passes through - so a test's own
    double still flows unchanged."""
    return None if cam_kind(obj) else obj


@fusion_fake(factory_for="_NamedCollection")
def wcs_params(origin_mode=None, orientation_mode=None, origin=None, z_axis=None):
    """A Setup.parameters collection of FakeCAMParameter, holding the WCS parameters as the
    read-back sees them - each one's .value.value is the payload (the mode string on a
    ChoiceParameterValue, the bound entities on a CadObjectParameterValue). A mode is its string;
    `origin`/`z_axis` are lists of (object_type, name) entity specs - None omits that parameter,
    [] models a present-but-unbound one, and name=None an entity with no readable name."""
    def _entity(spec):
        object_type, name = spec
        ent = types.SimpleNamespace(objectType=object_type)
        if name is not None:
            ent.name = name
        return ent

    params = []
    for pname, mode in (("wcs_origin_mode", origin_mode),
                        ("wcs_orientation_mode", orientation_mode)):
        if mode is not None:
            params.append(FakeCAMParameter(pname, value=mode))
    for pname, specs in (("wcs_origin_point", origin), ("wcs_orientation_axisZ", z_axis)):
        if specs is not None:
            params.append(FakeCAMParameter(pname, value=[_entity(s) for s in specs]))
    return _NamedCollection(params)


class _Strategy:
    """An OperationStrategy as the entitlement seam reads it - only isGenerationAllowed. Named
    without a Fake prefix because OperationStrategy has no live SHAPES dump to sweep against
    (test_fake_shapes_exist), like the _Insp* trio below. allowed=None makes the flag itself RAISE."""

    def __init__(self, allowed):
        self._allowed = allowed

    @property
    def isGenerationAllowed(self):
        if self._allowed is None:
            raise RuntimeError("isGenerationAllowed is unreadable on this build")
        return self._allowed


def strategy_factory(table, seen=None):
    """A stand-in for _cam_common._create_strategy (the OperationStrategy.createFromString seam
    workspace_orient's capability block and cam_generate's launch pre-flight share): `table` maps a
    strategy name -> True / False / None (None => the flag raises when read). A name ABSENT from the
    table RAISES the way the live factory does on an unknown/renamed strategy, which must degrade to
    null. `seen` counts the calls per name, for pinning the probe-once-per-strategy contract."""
    def create(name):
        if seen is not None:
            seen[name] = seen.get(name, 0) + 1
        if name not in table:
            raise RuntimeError(f"3 : Unknown strategy: {name}")
        return _Strategy(table[name])
    return create


def underlying(obj):
    """The node one read stands for - the object itself for anything that is not a read. A test
    telling one fake from another by isinstance goes through this: a read is a DISTINCT object of
    its own type, exactly as a live wrapper is."""
    return object.__getattribute__(obj, "_node") if isinstance(obj, _NodeRead) else obj


class _NodeRead:
    """ONE read of a CAM node out of a Fusion collection: a DISTINCT Python object per read that
    compares EQUAL to any other read of the same node (cam-setup-read-identity), so a resolver
    comparing `is` across two walks matches nothing here either. Reads and writes forward to the
    node. Setup is measured to REFUSE hash(); an Operation read's hashability is unmeasured, so no
    read offers one rather than teaching an answer nothing read."""

    __slots__ = ("_node",)

    def __init__(self, node):
        object.__setattr__(self, "_node", node)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_node"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_node"), name, value)

    def __eq__(self, other):
        return underlying(self) is underlying(other)

    __hash__ = None         # see the docstring: measured on Setup, unmeasured on an Operation


class _SetupRead(_NodeRead):
    """One read of a SETUP. MEASURED on one setup read twice (cam-setup-read-identity): `is` False,
    `==` True, `==` another setup False, and hash() RAISES TypeError - the live wrapper defines
    equality and NO hash."""

    __slots__ = ()


class _ChildrenCollection(_NamedCollection):
    """A parent's `children`: the counted/by-name walk, handing back a FRESH read per lookup the
    way live does."""

    def item(self, i):
        got = super().item(i)
        return None if got is None else _NodeRead(got)

    def itemByName(self, name):
        got = super().itemByName(name)
        return None if got is None else _NodeRead(got)

    def __iter__(self):
        return (_NodeRead(c) for c in super().__iter__())


class _SetupsCollection(_NamedCollection):
    """cam.setups: the same fresh-read-per-lookup walk, handing back the SETUP read - the one that
    also refuses hash()."""

    def item(self, i):
        got = super().item(i)
        return None if got is None else _SetupRead(got)

    def itemByName(self, name):
        got = super().itemByName(name)
        return None if got is None else _SetupRead(got)

    def __iter__(self):
        return (_SetupRead(s) for s in super().__iter__())


@fusion_fake(factory_for="_NamedCollection")
def make_cam(*setups, machining_times=None, check_validity=None):
    """A minimal CAM product carrying `setups` (count/item protocol) - pair with
    `monkeypatch.setattr(mod, "get_cam", lambda: (cam, None))`.

    getMachiningTime answers off `machining_times`, a {operation name: seconds} mapping - the
    second signal the EMPTY-toolpath class reads (an operation that generated an empty toolpath
    still reads hasToolpath True and answers 0.0 s). An operation the mapping does not carry RAISES
    '3 : Machining time could not be calculated.', which is what an operation holding no toolpath
    answers, so a test that wants a time has to name the operation it wants one for. The call
    records into .machining_time_calls, for a test that cares how often it was made.

    checkValidity is the platform's re-check of every operation against the CURRENT model, which
    get_cam runs on every call: `check_validity` is a callable standing in for what it does (flip a
    stale operation's state, or raise). Each call records into .check_validity_calls."""
    times = dict(machining_times or {})
    calls = []
    validity_calls = []

    def _machining_time(obj, *knobs):
        calls.append((obj, knobs))
        name = getattr(obj, "name", None)
        if name not in times:
            raise RuntimeError("3 : Machining time could not be calculated.")
        return types.SimpleNamespace(machiningTime=times[name])

    def _check_validity():
        validity_calls.append(True)
        if check_validity is not None:
            check_validity()

    return types.SimpleNamespace(setups=_SetupsCollection(list(setups)),
                                 getMachiningTime=_machining_time,
                                 machining_time_calls=calls,
                                 checkValidity=_check_validity,
                                 check_validity_calls=validity_calls)


# ── inspection results (CAM.inspectionResults) - the recorded probing measurements ────────────────
#
# The member sets are exactly the live ones. _InspMeasure has NO .name on purpose: a measure folder
# exposes none (the browser name is not readable, and no call enumerates the legal names), so a fake
# that carried one would teach a round-trip that cannot exist. Named without a Fake prefix because
# these types have no live SHAPES dump to sweep against (test_fake_shapes_exist).

class _InspPoint:
    """InspectionPointResult: nominalPosition/projectedPoint/contact (Point3D), delta (Vector3D),
    offset/deviation/error (CM) and state. readable=False models a property that raises."""

    def __init__(self, state, deviation=0.0, error=0.0, offset=0.0, nominal=(0.0, 0.0, 0.0),
                 contact=(0.0, 0.0, 0.0), projected=(0.0, 0.0, 0.0), delta=(0.0, 0.0, 0.0),
                 readable=True):
        self.state = state
        self._deviation = deviation
        self.error = error
        self.offset = offset
        self.nominalPosition = FakePoint(*nominal)
        self.contact = FakePoint(*contact)
        self.projectedPoint = FakePoint(*projected)
        self.delta = FakeVector3D(*delta)
        self._readable = readable

    @property
    def deviation(self):
        if not self._readable:
            raise RuntimeError("deviation cannot be read on this point")
        return self._deviation


@fusion_fake(factory_for="_NamedCollection")
def resolved_path(curves):
    """A Curve3DPath - the connected run a CurveSelection's outputGeometry holds, count/item(i) over
    shared Line3D / Circle3D fakes. Named without a Fake prefix: Curve3DPath has no live SHAPES dump
    to sweep against (test_fake_shapes_exist)."""
    return _NamedCollection(list(curves))


class _InspPath:
    """InspectionPathResult: pointResults is its only member (a counted collection; the shared
    _NamedCollection's itemByName goes unused - the live one carries only count/item)."""

    def __init__(self, points):
        self.pointResults = _NamedCollection(list(points))


class _InspMeasure:
    """CAMMeasure: inspectionPathResults is its only member. paths=None models the documented
    'null if none found'."""

    def __init__(self, paths):
        self.inspectionPathResults = None if paths is None else _NamedCollection(list(paths))


@fusion_fake(factory_for="_NamedCollection")
def make_inspection_cam(measures):
    """A CAM product whose inspectionResults is a collection of `measures`. measures=None drives the
    DEFENSIVE branch for the property answering None: a CAM product that never recorded a probe
    reads a COUNT-0 CAMInspectionResults instead, and a document carrying no CAM product never
    reaches the read at all (itemByProductType raises)."""
    return types.SimpleNamespace(
        inspectionResults=None if measures is None else _NamedCollection(list(measures)))


def make_gated_cam(member="inspectionResults", text="preview feature is not enabled"):
    """A CAM product whose `member` RAISES on read - the gated-member shape (measured on
    stockMaterialLibrary), which is a different answer from the property reading None."""
    def _raise(self):
        raise RuntimeError(text)
    return type("_GatedCam", (), {member: property(_raise)})()


# ── CAM job world ─────────────────────────────────────────────────────────

@fusion_fake(live_type="CAMParameter",
             facts=("shape-dump-cam-job-world", "cam-parameter-expressions",
                    "cam-parameter-locked-write-lands", "cam-parameter-bad-reference"))
class FakeCAMParameter:
    """One CAM parameter as the CAM tools read it: name/title, the `expression` a write goes through
    and reads back (measured), the isEditable/isEnabled/isVisible flags a selection filters on,
    `value` - whose OWN .value is the payload, the second hop every value read makes - and the
    error/warning channels a post-write read consults (`warning` never gates; `error` does).

    The cam-parameter-bad-reference row is what `error` stands on: an expression naming a parameter
    that does not exist is stored verbatim and reports success, and only .error names the failure.
    An expression holding 'NoSuch' answers that text here, read from BEHAVIOR (the literal below is
    the fallback until that row's regen lands the key). Pass `error` to pin the channel instead,
    and `choices` to make .value answer getChoices() the way a ChoiceParameterValue does."""
    def __init__(self, name, expression="", value=None, title=None, editable=True, enabled=True,
                 visible=True, error=None, warning="", choices=None):
        self.name = name
        self._expression = expression
        self.value = types.SimpleNamespace(value=value)
        if choices is not None:
            # A ChoiceParameterValue answers getChoices() -> (ok, titles, values); every other
            # value class carries no such member at all.
            legal = list(choices)
            self.value.getChoices = lambda: (True, [str(v).title() for v in legal], list(legal))
        self.title = name if title is None else title
        self.isEditable = editable
        self.isEnabled = enabled
        self.isVisible = visible
        self._error = error
        self.warning = warning

    @property
    def error(self):
        if self._error is not None:
            return self._error
        expr = self._expression
        text = _api_facts.BEHAVIOR["cam_bad_reference_error_text"]
        return text if isinstance(expr, str) and "NoSuch" in expr else ""

    @property
    def expression(self):
        return self._expression

    @expression.setter
    def expression(self, value):
        # Measured: isEditable False does not make the platform drop a write - a locked parameter
        # takes it, expression and value both change. The flag is the fallback this fake falls to
        # only if that measurement ever reads False.
        if _api_facts.BEHAVIOR["cam_locked_parameter_write_lands"] or self.isEditable:
            self._expression = value


@fusion_fake(live_type="CAMParameters", facts=("shape-dump-cam-job-world",))
class FakeCAMParameters:
    """An operation's, setup's or tool's parameters: itemByName is the lookup every CAM read and
    write goes through, None on a miss."""
    def __init__(self, parameters=()):
        self._coll = _NamedCollection(list(parameters))

    @property
    def count(self):
        return self._coll.count

    def item(self, i):
        return self._coll.item(i)

    def itemByName(self, name):
        return self._coll.itemByName(name)


@fusion_fake(live_type="Tool", facts=("shape-dump-cam-job-world",
                                      "cam-tool-dimension-parameter-names"))
class FakeTool:
    """An operation's cutting tool: the `description` every tool row publishes, the `parameters`
    its dimensions and feeds are read by name from, the `presets` a preset read walks, and toJson()
    - the text a tool copy is rebuilt from."""
    def __init__(self, description="", parameters=None, presets=(), json_text="{}"):
        self.description = description
        self.parameters = FakeCAMParameters() if parameters is None else parameters
        self.presets = _NamedCollection(list(presets))
        self._json = json_text

    def toJson(self):
        return self._json


@fusion_fake(live_type="Machine", facts=("shape-dump-cam-job-world",
                                         "cam-machine-query-keyed-on-model"))
class FakeMachine:
    """A machine from the library or off a setup: the description/vendor/model a label is built
    from (adsk.cam.Machine has no .name), its id, the capabilities flags a kind list reads, the
    elements tree a limits read walks, and the hasPost/hasSimulationModel pair a create publishes."""
    def __init__(self, description="", vendor="", model="", machine_id=None, capabilities=None,
                 elements=None, has_post=False, has_simulation_model=False):
        self.description = description
        self.vendor = vendor
        self.model = model
        self.id = machine_id
        self.capabilities = capabilities
        self.elements = elements
        self.hasPost = has_post
        self.hasSimulationModel = has_simulation_model


@fusion_fake(live_type="PrintSetting", facts=("shape-cam-print-setting",))
class FakePrintSetting:
    """A print setting from the library or off an additive setup: the name a request resolves by,
    the technology a filter reads, and the description - the one member two shipped settings that
    agree on everything else differ in, so it is what says WHICH one landed."""
    def __init__(self, name, technology="SLM", description="a print setting"):
        self.name = name
        self.technology = technology
        self.description = description


@fusion_fake(live_type="SetupInput", facts=("shape-dump-cam-job-world",))
class FakeSetupInput:
    """The input cam.setups.createInput() hands back: the operationType it was created for, and the
    name/models/machine/printSetting/stockMode a create assigns before add() consumes it.
    printSetting is the ADDITIVE half - a milling create leaves it None."""
    def __init__(self, operation_type=None, parameters=None):
        self.operationType = operation_type
        self.name = ""
        self.models = []
        self.machine = None
        self.printSetting = None
        self.stockMode = None
        self.parameters = FakeCAMParameters() if parameters is None else parameters


# MEASURED on 2705.1.15: an AdditiveOperation setup lands already holding these two operations, so
# a create on that arm reads a non-zero operation_count that is the PLATFORM's, not the call's.
ADDITIVE_SETUP_SEEDS = ("Body Preset1", "Additive Toolpath1")


@fusion_fake(live_type="Setups", facts=("shape-dump-cam-job-world", "shape-dump-cam-world"))
class FakeSetups:
    """cam.setups: the counted/by-name walk plus createInput/add. add() appends `new_setup` - or a
    FakeSetup named after the input - and hands it back, so a create read-back finds the setup in
    the walk the way live does. `seeds` names the operations the PLATFORM puts in a setup of its
    own accord (ADDITIVE_SETUP_SEEDS below), so a fake additive create is not born empty the way no
    live one is."""

    def __init__(self, setups=(), new_setup=None, setup_input=None, seeds=()):
        self._setups = list(setups)
        self._new = new_setup
        self._input = setup_input
        self._seeds = tuple(seeds)
        self._added = []

    @property
    def count(self):
        return len(self._setups)

    def item(self, i):
        return _SetupsCollection(self._setups).item(i)

    def itemByName(self, name):
        return _SetupsCollection(self._setups).itemByName(name)

    def createInput(self, operation_type):
        return FakeSetupInput(operation_type) if self._input is None else self._input

    def add(self, setup_input):
        self._added.append(setup_input)
        setup = (self._new if self._new is not None
                 else FakeSetup(getattr(setup_input, "name", "") or "Setup1"))
        # The created setup CARRIES what the input was given: live, Setup.operationType /
        # .machine / .printSetting are what a create reads back to prove the assignment took.
        for member in ("operationType", "machine", "printSetting"):
            if getattr(setup_input, member, None) is not None:
                setattr(setup, member, getattr(setup_input, member))
        for seeded in self._seeds:
            setup.operations._items.append(FakeOperation(seeded, has_toolpath=False, valid=False))
        self._setups.append(setup)
        return setup


@fusion_fake(factory_for="FakeCAMParameters")
def make_cam_parameters(*rows):
    """A CAMParameters collection from (name, expression) or (name, expression, value) rows - the
    by-name lookup every CAM read and write goes through."""
    return FakeCAMParameters([FakeCAMParameter(*row) for row in rows])
