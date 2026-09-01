"""Unit tests for ``cam_create_operation`` — apply a CAM milling operation.

The adsk.cam API is mocked; what we pin is the tool's OWN logic: resolving the target setup by name,
validating the strategy against the setup's compatibleStrategies (by .name), fetching the tool from a
library by (library_url, index) - the reference handle cam_get(include=['library']) produces -
assigning it to the OperationInput, adding the operation, and (optionally) generating the toolpath.
Plus the guards (no CAM, setup not found, bad strategy, tool ref out of range).
"""

import json

from conftest import load_tool

cco = load_tool("cam_create_operation")
_cam = load_tool("_cam_common")


# ── fakes mirroring the proven adsk.cam create path ─────────────────────────

class _Strategy:
    def __init__(self, name):
        self.name = name


class _OperationInput:
    def __init__(self, strategy):
        self.strategy = strategy
        self.tool = None


class _Operation:
    def __init__(self, inp):
        self.name = "Op1"
        self.strategy = inp.strategy
        self.tool = inp.tool
        self.hasToolpath = False
        self.isToolpathValid = False


class _Operations:
    def __init__(self, strategies):
        self.compatibleStrategies = [_Strategy(s) for s in strategies]
        self.added = []
        self._count = 0
    @property
    def count(self):
        return len(self.added)
    def item(self, i):
        return self.added[i]
    def createInput(self, strategy):
        if strategy not in [s.name for s in self.compatibleStrategies]:
            raise RuntimeError("invalid strategy")
        return _OperationInput(strategy)
    def add(self, inp):
        op = _Operation(inp)
        self.added.append(op)
        return op


class _Setup:
    def __init__(self, name, strategies):
        self.name = name
        self.operations = _Operations(strategies)


class _Setups:
    def __init__(self, setups):
        self._s = setups
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]


class _Tool:
    def __init__(self, desc):
        self.desc = desc


class _ToolLib:
    def __init__(self, tools):
        self._t = tools
    @property
    def count(self):
        return len(self._t)
    def item(self, i):
        return self._t[i]


class _CAM:
    def __init__(self, setups, strategies=("face", "adaptive", "drill", "bore"), doc_tools=()):
        self.setups = _Setups([_Setup(n, strategies) for n in setups])
        self.documentToolLibrary = _ToolLib(list(doc_tools))   # this doc's tools (real adsk shape)
        self.generated = []
        self.futures = []
    def generateToolpath(self, op):
        op.hasToolpath = True
        op.isToolpathValid = True
        self.generated.append(op)
        # GenerateToolpathFuture stand-in, kept so a test can assert THIS object was registered.
        fut = type("Fut", (), {"numberOfOperations": 1})()
        self.futures.append(fut)
        return fut


def _install(monkeypatch, setups=("Setup1",), tools=2, doc_tools=()):
    cam = _CAM(list(setups), doc_tools=doc_tools)
    monkeypatch.setattr(cco, "get_cam", lambda: (cam, None))
    # tool-by-reference resolver: (library_url, index) -> Tool, mirrors cam_edit_tools's shared handle
    lib = _ToolLib([_Tool("12mm Flat Endmill"), _Tool("6mm Ball Endmill")][:tools])
    cco._tool_at = lambda url, idx: (lib.item(idx) if 0 <= idx < lib.count else None,
                                     None if 0 <= idx < lib.count else "tool index %d out of range" % idx)
    # the document-library path is NOT patched — it runs the real _doc_tool_at against cam.documentToolLibrary
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_cam(self, monkeypatch):
        monkeypatch.setattr(cco, "get_cam", lambda: (None, "no CAM data"))
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=0)
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_setup_not_found(self, monkeypatch):
        _install(monkeypatch, setups=("Setup1",))
        res = cco.handler(setup="Ghost", strategy="face",
                          tool_library_url="u", tool_index=0)
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_bad_strategy(self, monkeypatch):
        _install(monkeypatch)
        res = cco.handler(setup="Setup1", strategy="frobnicate",
                          tool_library_url="u", tool_index=0)
        # the pre-check should name the bad strategy AND list the compatible ones (not just fail at createInput)
        assert res["isError"] is True and "frobnicate" in res["message"]
        assert "compatible" in res["message"].lower() and "face" in res["message"]

    def test_tool_ref_out_of_range(self, monkeypatch):
        _install(monkeypatch, tools=2)
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=9)
        assert res["isError"] is True and "range" in res["message"].lower()

    def test_missing_tool_ref(self, monkeypatch):
        _install(monkeypatch)
        res = cco.handler(setup="Setup1", strategy="face")
        assert res["isError"] is True and "tool" in res["message"].lower()


# ── the operation has to LAND in the setup, not just come back from add() ────
#
# operations.add hands back an Operation object, and that return is not evidence the setup took it.
# The count read either side of the add is what the created=... claim rests on.


class _DeafOperations(_Operations):
    """add() builds the operation and hands it back without the setup ever taking it."""
    def add(self, inp):
        return _Operation(inp)


class _MuteOperations(_Operations):
    """The count cannot be READ at all - the state a gate written as `before is not None and after
    is not None and after <= before` skips entirely, letting an unconfirmed create report ok."""
    @property
    def count(self):
        raise RuntimeError("operations.count is unreadable")


class _NonNumericCount(_Operations):
    """count answers with something that is not a number at all - what an unmodelled property hands
    back. Comparing the two sides of the add against it is not a count comparison."""
    @property
    def count(self):
        return object()


class _NonNumericCountBeforeAdd(_Operations):
    """count answers with something that is not a number BEFORE the add and a real count after it -
    the one-sided shape, which the two sides being read separately makes possible."""
    def __init__(self, strategies):
        super().__init__(strategies)
        self._landed = False

    @property
    def count(self):
        return len(self.added) if self._landed else object()

    def add(self, inp):
        op = super().add(inp)
        self._landed = True
        return op


class _CountStopsAfterAdd(_Operations):
    """count answers BEFORE the add and stops answering after it - only one side of the gate is
    missing, and the message has to name which."""
    def __init__(self, strategies):
        super().__init__(strategies)
        self._mute = False

    @property
    def count(self):
        if self._mute:
            raise RuntimeError("operations.count is unreadable")
        return len(self.added)

    def add(self, inp):
        op = super().add(inp)
        self._mute = True
        return op


def _with_operations(cam, cls):
    setup = cam.setups.item(0)
    setup.operations = cls([s.name for s in setup.operations.compatibleStrategies])
    return setup


class TestOperationLanding:
    def test_an_operation_that_never_lands_in_the_setup_is_an_error(self, monkeypatch):
        cam = _install(monkeypatch)
        setup = _with_operations(cam, _DeafOperations)
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=0, generate=False)
        assert res["isError"] is True
        assert "did not land" in res["message"]
        assert setup.operations.count == 0
        # the other side of `ops_after <= ops_before` is TestCreate.test_creates_operation_with_tool,
        # where the same add grows the setup by exactly one and the call succeeds.

    def test_an_unreadable_count_is_unconfirmed_not_a_pass(self, monkeypatch):
        # a count that does not answer cannot CLEAR the landing, so the gate may not be skipped for
        # it - an unlanded operation would report ok on exactly this read.
        cam = _install(monkeypatch)
        _with_operations(cam, _MuteOperations)
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=0, generate=False)
        assert res["isError"] is True
        assert "UNCONFIRMED" in res["message"]
        assert "could not be read before and after the add" in res["message"]

    def test_a_count_that_is_not_a_number_is_unconfirmed_too(self, monkeypatch):
        # the count is read through _common.counted, so a non-int answer is UNKNOWN rather than a
        # value to compare: `after <= before` against a bare object either raises or compares
        # something that is not a count at all.
        cam = _install(monkeypatch)
        _with_operations(cam, _NonNumericCount)
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=0, generate=False)
        assert res["isError"] is True and "UNCONFIRMED" in res["message"]
        # BOTH sides answered a non-count here, so both are named: a message naming one side is a
        # gate reading only the OTHER side as a count.
        assert "could not be read before and after the add" in res["message"]

    def test_a_non_numeric_count_on_the_BEFORE_side_alone_is_unconfirmed(self, monkeypatch):
        # The two sides are read separately, so each needs its own not-a-number read: a gate that
        # took the before count as whatever answered would compare an int against a bare object.
        cam = _install(monkeypatch)
        _with_operations(cam, _NonNumericCountBeforeAdd)
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=0, generate=False)
        assert res["isError"] is True and "UNCONFIRMED" in res["message"]
        assert "could not be read before the add" in res["message"]
        assert "before and after" not in res["message"]

    def test_the_message_names_which_side_of_the_add_went_unread(self, monkeypatch):
        # both counts unreadable and only the after one are different observations; naming the side
        # is what tells the reader whether the before-count was ever taken.
        cam = _install(monkeypatch)
        _with_operations(cam, _CountStopsAfterAdd)
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=0, generate=False)
        assert res["isError"] is True
        assert "could not be read after the add" in res["message"]
        assert "before and after" not in res["message"]


# ── create (no generate) ─────────────────────────────────────────────────────

class TestCreate:
    def test_creates_operation_with_tool(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_library_url="u", tool_index=0, generate=False))
        op = cam.setups.item(0).operations.item(0)
        assert op.strategy == "face"
        assert op.tool is not None and op.tool.desc == "12mm Flat Endmill"
        assert out["operation"] == "Op1" and out["strategy"] == "face"
        assert out["generation_started"] is False
        # not generated -> no toolpath yet
        assert len(cam.generated) == 0

    def test_create_then_generate(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(cco.handler(setup="Setup1", strategy="adaptive",
                                   tool_library_url="u", tool_index=1, generate=True))
        assert out["generation_started"] is True
        # generation is ASYNC: hasToolpath read at launch time is stale, so the payload must NOT
        # carry it (a false negative would send the agent chasing a phantom failure)
        assert "has_toolpath" not in out and "toolpath_valid" not in out
        assert len(cam.generated) == 1

    def test_generation_future_is_REGISTERED_not_dropped(self, monkeypatch):
        # Fusion ABANDONS an in-progress generation whose Future is garbage-collected, so the launch
        # must hand it to _cam_common.register_future - the one thing keeping it alive - and return
        # the handle cam_get_status polls. Discarding it reports generation_started with nothing
        # actually generating.
        _cam._GENERATIONS.clear()
        cam = _install(monkeypatch)
        out = _payload(cco.handler(setup="Setup1", strategy="adaptive",
                                   tool_library_url="u", tool_index=1, generate=True))
        assert out["generation_started"] is True
        handle = out["generation_handle"]
        entry = _cam._GENERATIONS[handle]
        assert entry["future"] is cam.futures[-1]      # THE launched future, still referenced
        _cam._GENERATIONS.clear()

    def test_a_null_future_is_reported_not_claimed_as_started(self, monkeypatch):
        # generateToolpath returning nothing means no generation is running; saying otherwise sends
        # the agent to post a job whose toolpath was never computed.
        cam = _install(monkeypatch)
        monkeypatch.setattr(cam, "generateToolpath", lambda op: None)
        out = _payload(cco.handler(setup="Setup1", strategy="adaptive",
                                   tool_library_url="u", tool_index=1, generate=True))
        assert out["generation_started"] is False
        assert "no future" in out["generate_error"]

    def test_default_does_not_generate(self, monkeypatch):
        # generate defaults to FALSE: a selection-driven strategy generated before its geometry is
        # selected does not fail - it lands a warned op with no toolpath that still reads valid
        # (measured on a live 2D Contour), so the honest default is to wait for cam_select_geometry.
        cam = _install(monkeypatch)
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_library_url="u", tool_index=0))
        assert out["generation_started"] is False and cam.generated == []
        assert "generation_handle" not in out

    def test_the_default_note_names_the_order_geometry_then_generate(self, monkeypatch):
        # the note is the only place a caller learns why nothing generated and what comes next.
        _install(monkeypatch)
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_library_url="u", tool_index=0))
        assert "cam_select_geometry" in out["note"] and "cam_generate" in out["note"]

    def test_the_wire_default_matches_the_handler_default(self, monkeypatch):
        # the schema an agent reads must not promise a different default than the handler applies.
        prop = cco.tool.to_dict()["inputSchema"]["properties"]["generate"]
        assert "default false" in prop["description"]
        assert "default true" not in cco.TOOL_DESCRIPTION


# ── document-library tool reference (the scriptless-CAM-chain fix) ───────────

class TestDocumentToolScope:
    def test_creates_op_from_document_library(self, monkeypatch):
        # tool_scope='document' takes the tool from cam.documentToolLibrary by index — no url needed
        cam = _install(monkeypatch, doc_tools=(_Tool("Demo Face Mill"), _Tool("Demo Flat Endmill")))
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_scope="document", tool_index=1, generate=False))
        op = cam.setups.item(0).operations.item(0)
        assert op.tool.desc == "Demo Flat Endmill"
        assert out["operation"] == "Op1"

    def test_document_index_out_of_range(self, monkeypatch):
        _install(monkeypatch, doc_tools=(_Tool("only one"),))
        res = cco.handler(setup="Setup1", strategy="face", tool_scope="document", tool_index=5)
        assert res["isError"] is True and "range" in res["message"].lower()

    def test_empty_document_library(self, monkeypatch):
        _install(monkeypatch, doc_tools=())
        res = cco.handler(setup="Setup1", strategy="face", tool_scope="document", tool_index=0)
        assert res["isError"] is True and "empty" in res["message"].lower()

    def test_document_scope_ignores_url(self, monkeypatch):
        # tool_scope=document WINS over a supplied url: the tool comes from the document library and
        # url resolution is never attempted - a bogus url must not even be looked at.
        cam = _install(monkeypatch, doc_tools=(_Tool("Demo Tool"),))

        def _boom(url, idx):
            raise AssertionError("url resolution attempted despite tool_scope=document")
        monkeypatch.setattr(cco, "_tool_at", _boom)
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_scope="document", tool_index=0, generate=False,
                                   tool_library_url="bogus://not-a-library"))
        assert cam.setups.item(0).operations.item(0).tool.desc == "Demo Tool"
        assert out["operation"] == "Op1"

    def test_no_ref_at_all_errors(self, monkeypatch):
        # neither tool_scope=document nor a url -> a clear error
        _install(monkeypatch)
        res = cco.handler(setup="Setup1", strategy="face", tool_index=0)
        assert res["isError"] is True and "tool" in res["message"].lower()
