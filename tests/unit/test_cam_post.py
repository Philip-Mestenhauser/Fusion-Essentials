"""Unit tests for ``cam_post`` - create-or-reuse an NC Program for the scope, then post it to disk.

The adsk.cam API is mocked. What we pin is the tool's OWN logic: resolving the .cps post config,
refusing up front when nothing valid can post (via live_readiness), scoping document vs a named setup,
CREATE-OR-REUSE (an existing NC Program of the same name is updated, never duplicated), the output
parameters/operations/post reaching the program, orphan cleanup of a just-created program behind a
failed post, and - the honesty gate - success being contingent on a real file LANDING on disk
(postProcess returning true is not proof), including the partial case where the API flags failure but
a file appeared.

PostConfiguration.createFromContent and NCProgramPostProcessOptions.create are patched to inert
carriers; the fake NCPrograms collection acts on the real output folder the handler set via the
program's parameters, writing (or not writing) a file there.
"""

import json
import os
import types

from conftest import load_tool, _NamedCollection

cp = load_tool("cam_post")


# -- fakes ---------------------------------------------------------------------

def _unq(expr):
    s = str(expr)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


class _StrParam:
    def __init__(self, name):
        self.name = name
        self.expression = ""


class _ValParam:
    def __init__(self, name, value=None):
        self.name = name
        self.value = types.SimpleNamespace(value=value)


class _ChoiceParam:
    """A ChoiceParameterValue-shaped param: getChoices() -> (ok, names, values) out-params, and
    .value legal only as one of those values (the live binding rejects anything else)."""
    def __init__(self, name, names, values, current):
        self.name = name
        legal = list(values)
        cv = types.SimpleNamespace(value=current)
        cv.getChoices = lambda: (True, list(names), legal)
        self.value = cv


def _make_params(missing=()):
    """The output parameters an NC program exposes. `missing` drops names to simulate a program that
    lacks a parameter (e.g. no output-folder param)."""
    params = {
        "nc_program_name": _StrParam("nc_program_name"),
        "nc_program_output_folder": _StrParam("nc_program_output_folder"),
        "nc_program_comment": _StrParam("nc_program_comment"),
        "nc_program_openInEditor": _ValParam("nc_program_openInEditor", True),
        "nc_program_unit": _ChoiceParam("nc_program_unit", ["Document unit", "Inches", "Millimeters"],
                                        ["$doc", "$in", "$mm"], "$doc"),
    }
    for n in missing:
        params.pop(n, None)
    return _Params(params)


class _Params:
    def __init__(self, mapping):
        self._p = mapping
    def itemByName(self, name):
        return self._p.get(name)


class _NCInput:
    def __init__(self, missing=()):
        self.displayName = None
        self.operations = None
        self.parameters = _make_params(missing)


class _NCProgram:
    def __init__(self, name, cam, missing=(), has_error=False):
        self.name = name
        self._cam = cam
        self.operations = None
        self.postConfiguration = None
        self.parameters = _make_params(missing)
        self.deleted = False
        self.hasError = has_error
        self.error = "toolpath fault" if has_error else None
    def postProcess(self, options):
        return self._cam._do_post(self)
    def deleteMe(self):
        self.deleted = True
        self._cam._remove(self)
        return True


class _NCPrograms:
    def __init__(self, cam, missing=(), has_error=False):
        self._cam = cam
        self._items = []
        self._missing = missing
        self._has_error = has_error
        self.create_calls = 0
        self.add_calls = 0
    @property
    def count(self):
        return len(self._items)
    def item(self, i):
        return self._items[i]
    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None
    def createInput(self):
        self.create_calls += 1
        return _NCInput(self._missing)
    def add(self, nc_input):
        self.add_calls += 1
        prog = _NCProgram(nc_input.displayName, self._cam, has_error=self._has_error)
        prog.parameters = nc_input.parameters       # the params the handler set on the input
        prog.operations = nc_input.operations
        self._items.append(prog)
        return prog


class _Op:
    def __init__(self, name):
        self.name = name


class _Setup:
    def __init__(self, name, ops=()):
        self.name = name
        self._ops = list(ops)
    @property
    def allOperations(self):
        return _NamedCollection(self._ops)


class _Setups:
    def __init__(self, setups):
        self._s = setups
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]


class _CAM:
    def __init__(self, setups, writes=True, returns=True, existing=(), missing=(), program_error=False):
        self.setups = _Setups(setups)
        self.personalPostFolder = "C:/nonexistent/personal"
        self.genericPostFolder = "C:/nonexistent/generic"
        self._writes = writes
        self._returns = returns
        self.ncPrograms = _NCPrograms(self, missing, program_error)
        for name in existing:
            self.ncPrograms._items.append(_NCProgram(name, self, missing, has_error=program_error))
        self.posted = []
    def _do_post(self, program):
        self.posted.append(program)
        folder = _unq(program.parameters.itemByName("nc_program_output_folder").expression)
        name = _unq(program.parameters.itemByName("nc_program_name").expression)
        if self._writes == "failed":
            # a failed post leaves only a '.failed' stub in the output folder (the real error is in the
            # post log, elsewhere) and postProcess returns False.
            with open(os.path.join(folder, str(name) + ".nc.failed"), "w") as f:
                f.write("%\n!Error: Failed to post data. See log for details.\n")
            return False
        if self._writes:
            with open(os.path.join(folder, str(name) + ".nc"), "w") as f:
                f.write("%\nO1000\nG0 X0 Y0\nM30\n%\n")
        return self._returns
    def _remove(self, program):
        if program in self.ncPrograms._items:
            self.ncPrograms._items.remove(program)


class _OC:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)


class _URL:
    """A fake adsk.core.URL: a full string plus its leaf name (the section after the last '/')."""
    def __init__(self, s):
        self._s = s
    @property
    def leafName(self):
        return self._s.rstrip("/").rsplit("/", 1)[-1]
    def toString(self):
        return self._s


class _FakePostLibrary:
    """A nested cloud/hub post library. `tree` maps a folder-url string to (child_folder_urls,
    asset_urls); `posts` maps an asset-url string to the PostConfiguration it loads."""
    def __init__(self, root, tree, posts):
        self._root = _URL(root)
        self._tree = tree
        self._posts = posts
    def urlByLocation(self, loc):
        return self._root
    def childFolderURLs(self, url):
        return list(self._tree.get(url.toString(), ([], []))[0])
    def childAssetURLs(self, url):
        return list(self._tree.get(url.toString(), ([], []))[1])
    def postConfigurationAtURL(self, url):
        return self._posts.get(url.toString())


def _cloud_lib_one_post():
    """A library with one post 'Generic Fanuc.cps' nested one folder deep, plus a sibling post."""
    root = "cloud://root"
    vendors = _URL("cloud://root/Vendors")
    fanuc = _URL("cloud://root/Vendors/Generic Fanuc.cps")
    haas = _URL("cloud://root/Vendors/Haas NGC.cps")
    tree = {
        "cloud://root": ([vendors], []),
        "cloud://root/Vendors": ([], [fanuc, haas]),
    }
    posts = {fanuc.toString(): object(), haas.toString(): object()}
    return _FakePostLibrary(root, tree, posts), fanuc.toString()


def _write_cps(tmp_path, name="fanuc.cps"):
    p = tmp_path / name
    p.write_text("// a fake post config\n")
    return p


def _install(monkeypatch, cam, valid=1):
    monkeypatch.setattr(cp, "get_cam", lambda: (cam, None))
    monkeypatch.setattr(cp, "live_readiness",
                        lambda: ({"valid": valid, "readiness": "ready to post."}, None))
    monkeypatch.setattr(cp.adsk.cam.PostConfiguration, "createFromContent",
                        lambda content: object(), raising=False)
    monkeypatch.setattr(cp.adsk.cam.NCProgramPostProcessOptions, "create",
                        lambda: object(), raising=False)
    monkeypatch.setattr(cp.adsk.core.ObjectCollection, "create",
                        lambda: _OC(), raising=False)
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# -- guards --------------------------------------------------------------------

class TestGuards:
    def test_no_cam(self, monkeypatch):
        monkeypatch.setattr(cp, "get_cam", lambda: (None, "no CAM data"))
        res = cp.handler(output_folder="x", program_name="1", post="p")
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_requires_output_folder(self, monkeypatch, tmp_path):
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        res = cp.handler(post=str(_write_cps(tmp_path)), program_name="1")
        assert res["isError"] is True and "output_folder" in res["message"]

    def test_requires_program_name(self, monkeypatch, tmp_path):
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        res = cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path))
        assert res["isError"] is True and "program_name" in res["message"]

    def test_post_config_not_found(self, monkeypatch, tmp_path):
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        res = cp.handler(post="no_such_post", output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True and "not found" in res["message"].lower()

    def test_bad_units_rejected(self, monkeypatch, tmp_path):
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        res = cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path),
                         program_name="1", units="furlongs")
        assert res["isError"] is True and "units" in res["message"].lower()

    def test_refuses_when_no_valid_toolpaths(self, monkeypatch, tmp_path):
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]), valid=0)
        res = cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True and "valid" in res["message"].lower()

    def test_unknown_scope_is_error(self, monkeypatch, tmp_path):
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        res = cp.handler(scope="Ghost", post=str(_write_cps(tmp_path)),
                         output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_duplicate_scope_name_across_setups_is_refused(self, monkeypatch, tmp_path):
        # "Drill1" exists in TWO setups - posting that scope must REFUSE with both setup paths and
        # post NOTHING, never post whichever setup's op the walk met first.
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Drill1")]),
                                          _Setup("S2", [_Op("Drill1")])]))
        res = cp.handler(scope="Drill1", post=str(_write_cps(tmp_path)),
                         output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert "S1 / Drill1" in res["message"] and "S2 / Drill1" in res["message"]
        assert cam.posted == [] and cam.ncPrograms.count == 0    # no program, no post


# -- post config resolution ----------------------------------------------------

class TestPostResolution:
    def test_resolves_post_by_name_in_personal_folder(self, monkeypatch, tmp_path):
        posts = tmp_path / "posts"
        posts.mkdir()
        _write_cps(posts, "generic fanuc.cps")
        out = tmp_path / "out"
        out.mkdir()
        cam = _CAM([_Setup("S1", [_Op("Face1")])])
        cam.personalPostFolder = str(posts)
        _install(monkeypatch, cam)
        data = _payload(cp.handler(post="generic fanuc", output_folder=str(out), program_name="1"))
        assert data["posted"] is True
        assert data["post_config"].endswith("generic fanuc.cps")


# -- post_scope: cloud/hub team post library -----------------------------------

class TestCloudPostScope:
    def test_local_scope_default_returns_ready_post_configuration(self, monkeypatch, tmp_path):
        # post_scope defaults to local and loads a .cps via createFromContent,
        # returning a ready PostConfiguration (not a path) - the handler does not load it.
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        cps = _write_cps(tmp_path)
        pc, label, err = cp._resolve_post_config(cam, str(cps), "local")
        assert err is None and pc is not None
        assert label.endswith("fanuc.cps")

    def test_cloud_resolves_post_by_name_and_posts(self, monkeypatch, tmp_path):
        # THE cloud bite: a name matches an asset url leafName in a nested folder, loads via
        # postConfigurationAtURL, and the handler posts a file with that PostConfiguration.
        lib, fanuc_url = _cloud_lib_one_post()
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        monkeypatch.setattr(cp, "_post_library", lambda: lib)
        data = _payload(cp.handler(post="Generic Fanuc", post_scope="cloud",
                                   output_folder=str(tmp_path), program_name="1001"))
        assert data["posted"] is True and data["post_scope"] == "cloud"
        assert data["post_config"] == fanuc_url          # the label is the matched post url
        prog = cam.ncPrograms.item(0)
        assert prog.postConfiguration is not None         # the loaded PostConfiguration was assigned

    def test_cloud_post_not_found_lists_candidates(self, monkeypatch, tmp_path):
        lib, _ = _cloud_lib_one_post()
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        monkeypatch.setattr(cp, "_post_library", lambda: lib)
        res = cp.handler(post="No Such Post", post_scope="cloud",
                         output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True
        assert "no cloud post named" in res["message"].lower()
        assert "Generic Fanuc.cps" in res["message"]      # the available posts are listed

    def test_cloud_ambiguous_name_refused(self, monkeypatch, tmp_path):
        # Two posts with the same leaf name in different folders - refuse rather than grab one.
        a = _URL("cloud://root/A/Fanuc.cps")
        b = _URL("cloud://root/B/Fanuc.cps")
        tree = {
            "cloud://root": ([_URL("cloud://root/A"), _URL("cloud://root/B")], []),
            "cloud://root/A": ([], [a]),
            "cloud://root/B": ([], [b]),
        }
        lib = _FakePostLibrary("cloud://root", tree, {a.toString(): object(), b.toString(): object()})
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        monkeypatch.setattr(cp, "_post_library", lambda: lib)
        res = cp.handler(post="Fanuc", post_scope="cloud",
                         output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()


# -- create-new vs reuse -------------------------------------------------------

class TestCreateOrReuse:
    def test_creates_program_when_none_exists(self, monkeypatch, tmp_path):
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        data = _payload(cp.handler(post=str(_write_cps(tmp_path)),
                                   output_folder=str(tmp_path), program_name="1001"))
        assert data["program_reused"] is False and data["nc_program"] == "created"
        assert cam.ncPrograms.add_calls == 1 and cam.ncPrograms.create_calls == 1
        assert cam.ncPrograms.count == 1                    # exactly one program now exists

    def test_reuses_existing_program_not_duplicated(self, monkeypatch, tmp_path):
        # THE reuse bite: a program already named 'JOB1' must be UPDATED in place, not add()-ed again.
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])], existing=["JOB1"]))
        data = _payload(cp.handler(post=str(_write_cps(tmp_path)),
                                   output_folder=str(tmp_path), program_name="JOB1"))
        assert data["program_reused"] is True and data["nc_program"] == "reused"
        assert cam.ncPrograms.add_calls == 0                # never created a second one
        assert cam.ncPrograms.count == 1                    # still exactly one program named JOB1
        prog = cam.ncPrograms.itemByName("JOB1")
        assert prog.postConfiguration is not None           # post config was (re)applied
        assert prog.operations is not None                  # operations were (re)assigned

    def test_output_params_reach_the_program(self, monkeypatch, tmp_path):
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        _payload(cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path),
                            program_name="7", program_comment="rev A", units="mm"))
        prog = cam.ncPrograms.item(0)
        params = prog.parameters
        assert _unq(params.itemByName("nc_program_name").expression) == "7"
        assert _unq(params.itemByName("nc_program_output_folder").expression) == \
            str(tmp_path).replace("\\", "/")
        assert _unq(params.itemByName("nc_program_comment").expression) == "rev A"
        assert params.itemByName("nc_program_openInEditor").value.value is False   # headless
        assert params.itemByName("nc_program_unit").value.value == "$mm"

    def test_operations_collection_carries_the_target_setup(self, monkeypatch, tmp_path):
        s1 = _Setup("Setup1", [_Op("Face1")])
        cam = _install(monkeypatch, _CAM([s1]))
        _payload(cp.handler(scope="Setup1", post=str(_write_cps(tmp_path)),
                            output_folder=str(tmp_path), program_name="9"))
        prog = cam.ncPrograms.item(0)
        assert prog.operations == [s1]                      # a plain LIST of exactly the named setup


# -- the honesty gate: a real file must land -----------------------------------

class TestPostWritesFile:
    def test_posts_document_and_reports_written_file(self, monkeypatch, tmp_path):
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        data = _payload(cp.handler(post=str(_write_cps(tmp_path)),
                                   output_folder=str(tmp_path), program_name="1001"))
        assert data["posted"] is True and data["scope"] == "document"
        assert data["file_count"] == 1
        rec = data["files"][0]
        assert rec["file_path"].endswith("1001.nc") and rec["size_bytes"] > 0
        assert os.path.isfile(rec["file_path"])

    def test_declared_output_is_minted(self, monkeypatch, tmp_path):
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        data = _payload(cp.handler(post=str(_write_cps(tmp_path)),
                                   output_folder=str(tmp_path), program_name="1"))
        for out in cp.RETURNS:
            assert out.assert_present(data) == "", out.assert_present(data)

    def test_no_file_written_is_error_even_when_api_returns_true(self, monkeypatch, tmp_path):
        # THE honesty gate: postProcess returns True but writes nothing -> must be isError, never a false ok.
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])], writes=False, returns=True))
        res = cp.handler(post=str(_write_cps(tmp_path)),
                         output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True and "usable nc file" in res["message"].lower()

    def test_created_program_is_rolled_back_on_failed_post(self, monkeypatch, tmp_path):
        # Orphan cleanup: a just-created program that produced no file is deleted, not left behind.
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])], writes=False))
        res = cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path), program_name="X")
        assert res["isError"] is True
        assert cam.ncPrograms.count == 0                    # the orphan was removed
        assert "removed" in res["message"].lower()

    def test_reused_program_is_not_deleted_on_failed_post(self, monkeypatch, tmp_path):
        # A pre-existing program is the caller's - a failed re-post must NOT delete it.
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])], writes=False, existing=["JOB1"]))
        res = cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path), program_name="JOB1")
        assert res["isError"] is True
        assert cam.ncPrograms.count == 1                    # still there
        assert cam.ncPrograms.itemByName("JOB1").deleted is False

    def test_missing_output_folder_param_is_error(self, monkeypatch, tmp_path):
        # Without nc_program_output_folder the file can't be aimed at out_dir - fail loudly, name it,
        # and roll back the just-created program.
        cam = _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])],
                                          missing=("nc_program_output_folder",)))
        res = cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True and "nc_program_output_folder" in res["message"]
        assert cam.ncPrograms.count == 0

    def test_program_error_is_partial_even_with_file(self, monkeypatch, tmp_path):
        # A file landed and postProcess returned true, but the NC Program faulted (hasError) -> partial,
        # surface the program error rather than claim clean success.
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])], program_error=True))
        data = _payload(cp.handler(post=str(_write_cps(tmp_path)),
                                   output_folder=str(tmp_path), program_name="1"))
        assert data["partial"] is True and data["program_error"] == "toolpath fault"
        assert data["file_count"] == 1

    def test_partial_when_api_false_but_file_appeared(self, monkeypatch, tmp_path):
        # A file landed but the API flagged failure - report both facts, do not claim clean success.
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])], writes=True, returns=False))
        data = _payload(cp.handler(post=str(_write_cps(tmp_path)),
                                   output_folder=str(tmp_path), program_name="1"))
        assert data["posted"] is False and data["partial"] is True and data["file_count"] == 1

    def test_post_raising_is_error_and_rolls_back(self, monkeypatch, tmp_path):
        cam = _CAM([_Setup("S1", [_Op("Face1")])])
        def _boom(program):
            raise RuntimeError("post kaboom")
        cam._do_post = _boom
        _install(monkeypatch, cam)
        res = cp.handler(post=str(_write_cps(tmp_path)),
                         output_folder=str(tmp_path), program_name="1")
        assert res["isError"] is True and "kaboom" in res["message"]
        assert cam.ncPrograms.count == 0                    # orphan removed after the raise


# ── nc_program_unit: a ChoiceParameterValue rejects a bare int index; set the choice STRING, and ──
# ── surface an explicit note (never post silently-wrong units) when the set still fails. ────────────

class TestUnitParam:
    def test_missing_unit_param_is_no_note(self):
        val, note = cp._set_unit_param(_Params({}), "mm")
        assert val is cp._MISSING and note is None

    def test_no_choices_exposed_is_error_note_not_wrong_typed_set(self):
        # a value with no getChoices() must NOT be set blind (the platform rejects a bare int with a
        # std::string type error) - surface the error + note instead.
        params = _Params({"nc_program_unit": _ValParam("nc_program_unit", 0)})
        val, note = cp._set_unit_param(params, "mm")
        assert isinstance(val, str) and "error" in val
        assert note and "units" in note.lower()

    def test_choice_value_picked_by_unit_word_in_its_name(self):
        # getChoices() returns (ok, names, values); the value whose NAME carries the unit word is set -
        # never an index guess into the names.
        cv = types.SimpleNamespace(value="$doc")
        cv.getChoices = lambda: (True, ["Document unit", "Inches", "Millimeters"],
                                 ["$doc", "$in", "$mm"])
        params = _Params({"nc_program_unit": types.SimpleNamespace(name="nc_program_unit", value=cv)})
        val, note = cp._set_unit_param(params, "mm")
        assert note is None and cv.value == "$mm"

    def test_set_failure_returns_error_value_and_surfaced_note(self):
        # the live defect: setting the value raises 'ChoiceParameterValue__set_value'. The failure must
        # come back as an error value + a human note, never a swallowed success.
        class _Reject:
            def getChoices(self):
                return (True, ["Document unit", "Inches", "Millimeters"], ["$doc", "$in", "$mm"])
            @property
            def value(self):
                return "$doc"
            @value.setter
            def value(self, v):
                raise RuntimeError("error in ChoiceParameterValue__set_value")

        params = _Params({"nc_program_unit": types.SimpleNamespace(name="nc_program_unit", value=_Reject())})
        val, note = cp._set_unit_param(params, "mm")
        assert isinstance(val, str) and "error" in val
        assert note and "units" in note.lower()

    def test_handler_surfaces_unit_note_but_still_posts(self, monkeypatch, tmp_path):
        # a units failure is NON-fatal (the file posts in the program's current units) but must be
        # surfaced explicitly in units_note + the note, not buried in params_applied.
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])]))
        monkeypatch.setattr(cp, "_set_unit_param",
                            lambda params, units_key: ("<error: boom>",
                                                       "Output units could not be set to 'mm'."))
        data = _payload(cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path),
                                   program_name="1", units="mm"))
        assert data["file_count"] == 1                          # non-fatal: the file still posted
        assert "units_note" in data and "units" in data["units_note"].lower()
        assert "Output units could not be set" in data["note"]


# ── post-log surfacing: a failed post's real error lives in the log, not the output folder ──────────

class TestPostLog:
    def _make_log(self, root, program, lines):
        d = os.path.join(str(root), "sess-1", "5")
        os.makedirs(d)
        with open(os.path.join(d, program + ".log"), "w") as f:
            f.write(lines)

    def test_is_failure_marker(self):
        assert cp._is_failure_marker("C:/x/1001.nc.failed") is True
        assert cp._is_failure_marker("C:/x/1001.nc") is False

    def test_reads_error_and_warning_lines_skipping_information(self, monkeypatch, tmp_path):
        root = tmp_path / "Fusion360CAM"
        self._make_log(root, "1001",
                       "Information: start\n"
                       "Error: Program number 'NaN' is out of range. Please enter 1-99999.\n"
                       "Warning: check units\nInformation: end\n")
        monkeypatch.setattr(cp, "_CAM_LOG_ROOT", str(root))
        errs = cp._post_log_errors("1001", 0.0)
        assert any("out of range" in e for e in errs)
        assert any(e.lower().startswith("warning") for e in errs)
        assert all(not e.lower().startswith("information") for e in errs)

    def test_no_log_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cp, "_CAM_LOG_ROOT", str(tmp_path / "nothing"))
        assert cp._post_log_errors("1001", 0.0) == []

    def test_failed_stub_is_error_with_the_log_reason_not_listed_as_a_deliverable(self, monkeypatch, tmp_path):
        # A '.failed' stub is the ONLY thing the post wrote -> no deliverable NC file -> error that
        # surfaces the LOG's actionable reason, and never lists the stub as if it were G-code.
        root = tmp_path / "Fusion360CAM"
        self._make_log(root, "1001", "Error: Program number 'NaN' is out of range.\n")
        monkeypatch.setattr(cp, "_CAM_LOG_ROOT", str(root))
        _install(monkeypatch, _CAM([_Setup("S1", [_Op("Face1")])], writes="failed"))
        res = cp.handler(post=str(_write_cps(tmp_path)), output_folder=str(tmp_path), program_name="1001")
        assert res["isError"] is True
        assert "out of range" in res["message"]            # the real reason, from the log
        assert ".failed" not in str(res.get("data", ""))   # the stub is not paraded as a deliverable
