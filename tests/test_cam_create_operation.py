"""Unit tests for ``cam_create_operation`` — apply a CAM milling operation.

The adsk.cam API is mocked; what we pin is the tool's OWN logic: resolving the target setup by name,
validating the strategy against the setup's compatibleStrategies (by .name), fetching the tool from a
library by (library_url, index) — the reference handle cam_read_tool_library produces — assigning it to
the OperationInput, adding the operation, and (optionally) generating the toolpath. Plus the guards
(no CAM, setup not found, bad strategy, tool ref out of range).
"""

import json

from conftest import load_tool

cco = load_tool("cam_create_operation")


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
    def generateToolpath(self, op):
        op.hasToolpath = True
        op.isToolpathValid = True
        self.generated.append(op)
        return object()   # GenerateToolpathFuture stand-in


def _install(setups=("Setup1",), tools=2, doc_tools=()):
    cam = _CAM(list(setups), doc_tools=doc_tools)
    cco._get_cam = lambda: (cam, None)
    # tool-by-reference resolver: (library_url, index) -> Tool, mirrors cam_tool_library's shared handle
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
    def test_no_cam(self):
        cco._get_cam = lambda: (None, "no CAM data")
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=0)
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_setup_not_found(self):
        _install(setups=("Setup1",))
        res = cco.handler(setup="Ghost", strategy="face",
                          tool_library_url="u", tool_index=0)
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_bad_strategy(self):
        _install()
        res = cco.handler(setup="Setup1", strategy="frobnicate",
                          tool_library_url="u", tool_index=0)
        # the pre-check should name the bad strategy AND list the compatible ones (not just fail at createInput)
        assert res["isError"] is True and "frobnicate" in res["message"]
        assert "compatible" in res["message"].lower() and "face" in res["message"]

    def test_tool_ref_out_of_range(self):
        _install(tools=2)
        res = cco.handler(setup="Setup1", strategy="face",
                          tool_library_url="u", tool_index=9)
        assert res["isError"] is True and "range" in res["message"].lower()

    def test_missing_tool_ref(self):
        _install()
        res = cco.handler(setup="Setup1", strategy="face")
        assert res["isError"] is True and "tool" in res["message"].lower()


# ── create (no generate) ─────────────────────────────────────────────────────

class TestCreate:
    def test_creates_operation_with_tool(self):
        cam = _install()
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_library_url="u", tool_index=0, generate=False))
        op = cam.setups.item(0).operations.item(0)
        assert op.strategy == "face"
        assert op.tool is not None and op.tool.desc == "12mm Flat Endmill"
        assert out["operation"] == "Op1" and out["strategy"] == "face"
        assert out["generated"] is False
        # not generated -> no toolpath yet
        assert len(cam.generated) == 0

    def test_create_then_generate(self):
        cam = _install()
        out = _payload(cco.handler(setup="Setup1", strategy="adaptive",
                                   tool_library_url="u", tool_index=1, generate=True))
        assert out["generated"] is True
        assert out["has_toolpath"] is True and out["toolpath_valid"] is True
        assert len(cam.generated) == 1

    def test_default_generates(self):
        # generate defaults to True (the useful default — an operation with no toolpath is incomplete)
        cam = _install()
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_library_url="u", tool_index=0))
        assert out["generated"] is True and len(cam.generated) == 1


# ── document-library tool reference (the scriptless-CAM-chain fix) ───────────

class TestDocumentToolScope:
    def test_creates_op_from_document_library(self):
        # tool_scope='document' takes the tool from cam.documentToolLibrary by index — no url needed
        cam = _install(doc_tools=(_Tool("Demo Face Mill"), _Tool("Demo Flat Endmill")))
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_scope="document", tool_index=1, generate=False))
        op = cam.setups.item(0).operations.item(0)
        assert op.tool.desc == "Demo Flat Endmill"
        assert out["operation"] == "Op1"

    def test_document_index_out_of_range(self):
        _install(doc_tools=(_Tool("only one"),))
        res = cco.handler(setup="Setup1", strategy="face", tool_scope="document", tool_index=5)
        assert res["isError"] is True and "range" in res["message"].lower()

    def test_empty_document_library(self):
        _install(doc_tools=())
        res = cco.handler(setup="Setup1", strategy="face", tool_scope="document", tool_index=0)
        assert res["isError"] is True and "empty" in res["message"].lower()

    def test_document_scope_ignores_url(self):
        # with tool_scope=document, no tool_library_url is required
        cam = _install(doc_tools=(_Tool("Demo Tool"),))
        out = _payload(cco.handler(setup="Setup1", strategy="face",
                                   tool_scope="document", tool_index=0, generate=False))
        assert cam.setups.item(0).operations.item(0).tool.desc == "Demo Tool"

    def test_no_ref_at_all_errors(self):
        # neither tool_scope=document nor a url -> a clear error
        _install()
        res = cco.handler(setup="Setup1", strategy="face", tool_index=0)
        assert res["isError"] is True and "tool" in res["message"].lower()
