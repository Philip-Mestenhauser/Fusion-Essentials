"""Unit tests for ``cam_templates.py`` navigation logic.

Targets ``_find_template_by_name``: the ``_LOCATIONS`` validation (unknown
location -> hint), case-insensitive matching, and recursive descent into
subfolders. A fake library models the ``childTemplates`` / ``childFolderURLs`` /
``urlByLocation`` surface the walker uses — no live CAM needed.
"""

from types import SimpleNamespace

from conftest import load_tool

ct = load_tool("cam_templates")


class FakeLib:
    """Minimal templateLibrary: a folder tree of named templates.

    ``tree`` maps a folder URL (any hashable) to (templates, subfolder_urls).
    ``urlByLocation`` returns the configured root url regardless of enum value.
    """
    def __init__(self, root_url, tree):
        self._root = root_url
        self._tree = tree

    def urlByLocation(self, loc):
        return self._root

    def childTemplates(self, folder_url):
        templates, _ = self._tree.get(folder_url, ([], []))
        return [SimpleNamespace(name=n) for n in templates]

    def childFolderURLs(self, folder_url):
        _, subs = self._tree.get(folder_url, ([], []))
        return subs


class TestApplyTemplateEnumValidation:
    # apply validates its enums up front (before any CAM work): an unrecognized 'generate' must be a
    # hard error, NOT silently coerced to skip (which would create operations but generate no toolpaths).
    def test_unknown_generate_is_rejected_not_silently_skipped(self):
        res = ct.apply_template_to_setup_handler(setup="S", template_name="T", generate="bogus")
        assert res["isError"] is True
        assert "generate" in res["message"].lower()

    def test_unknown_location_is_rejected(self):
        res = ct.apply_template_to_setup_handler(setup="S", template_name="T", location="atlantis")
        assert res["isError"] is True
        assert "location" in res["message"].lower()


class TestFindTemplateByName:
    def test_unknown_location_is_rejected(self):
        lib = FakeLib("root", {"root": (["A"], [])})
        template, hint = ct._find_template_by_name(lib, "atlantis", "A")
        assert template is None
        assert "atlantis" in hint

    def test_finds_template_in_root(self):
        lib = FakeLib("root", {"root": (["2D Adaptive", "Face"], [])})
        template, hint = ct._find_template_by_name(lib, "cloud", "Face")
        assert template is not None
        assert template.name == "Face"
        assert hint is None

    def test_match_is_case_insensitive(self):
        lib = FakeLib("root", {"root": (["2D Adaptive"], [])})
        template, hint = ct._find_template_by_name(lib, "cloud", "2d adaptive")
        assert template is not None
        assert template.name == "2D Adaptive"

    def test_descends_into_subfolders(self):
        lib = FakeLib("root", {
            "root": ([], ["sub"]),
            "sub": (["Deep Template"], []),
        })
        template, hint = ct._find_template_by_name(lib, "cloud", "Deep Template")
        assert template is not None
        assert template.name == "Deep Template"

    def test_not_found_returns_none(self):
        lib = FakeLib("root", {"root": (["A", "B"], [])})
        template, hint = ct._find_template_by_name(lib, "cloud", "Missing")
        assert template is None


# ── _as_cam_template: normalise createFromOperations' contradictory return ──────────────────────
#
# The live API annotation is list[Operation] but the docstring claims a CAMTemplate. Save code
# that assumes a single CAMTemplate and sets .name on it / passes it to importTemplate breaks if
# the binding actually returns a list. These pin the normalisation for BOTH shapes so the save
# path is correct whichever the installed Fusion returns. (CAMTemplate.cast is the discriminator.)

class _FakeTemplate:
    def __init__(self, name="T"):
        self.name = name
        self.isValidTemplate = True


def _patch_cast(monkey_is_template):
    """Point adsk.cam.CAMTemplate.cast at a predicate: it returns the object iff it's a template."""
    import adsk.cam
    adsk.cam.CAMTemplate.cast = staticmethod(
        lambda x: x if monkey_is_template(x) else None)


class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class TestAsCamTemplate:
    def test_passthrough_when_already_a_template(self):
        t = _FakeTemplate("Slot Mill")
        _patch_cast(lambda x: isinstance(x, _FakeTemplate))
        assert ct._as_cam_template(t) is t

    def test_recovers_template_from_a_list_result(self):
        # the annotated list[Operation] shape, but containing the template object
        t = _FakeTemplate("Bundle")
        _patch_cast(lambda x: isinstance(x, _FakeTemplate))
        assert ct._as_cam_template([t]) is t

    def test_recovers_template_from_a_collection_result(self):
        t = _FakeTemplate("Bundle")
        _patch_cast(lambda x: isinstance(x, _FakeTemplate))
        assert ct._as_cam_template(_Coll([t])) is t

    def test_returns_none_when_no_template_present(self):
        # a pure list of non-template operations -> None (caller reports it instead of crashing)
        _patch_cast(lambda x: isinstance(x, _FakeTemplate))
        assert ct._as_cam_template(["op1", "op2"]) is None
        assert ct._as_cam_template(object()) is None


# ── _walk_library: asset-URL matching + depth limit + node cap ──────────────────────────────────────
# The string parse in _asset_url_for is a classic silent-wrong-string risk: it must pair a template
# NAME to its asset URL by the stem of "<folder>/<name>.f3dhsm-template". The depth limit must flag
# 'folders_truncated', and the global node cap must flag 'truncated'.

import json


class _Url:
    def __init__(self, s): self._s = s
    def toString(self): return self._s


class _WalkLib:
    """A library tree keyed by folder url-string: name -> (template_names, [subfolder url-strings],
    [asset_url_strings])."""
    def __init__(self, tree):
        self._tree = tree
    def displayName(self, url):
        return f"folder<{url.toString()}>"
    def childTemplates(self, url):
        names, _, _ = self._tree.get(url.toString(), ([], [], []))
        return [SimpleNamespace(name=n, description="", isValidTemplate=True,
                                isHoleTemplate=False) for n in names]
    def childFolderURLs(self, url):
        _, subs, _ = self._tree.get(url.toString(), ([], [], []))
        return [_Url(s) for s in subs]
    def childAssetURLs(self, url):
        _, _, assets = self._tree.get(url.toString(), ([], [], []))
        return [_Url(a) for a in assets]


class TestWalkLibrary:
    def test_asset_url_paired_to_template_by_stem(self):
        tree = {"root": (["Face", "2D Adaptive"], [],
                         ["lib://root/Face.f3dhsm-template",
                          "lib://root/2D Adaptive.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        urls = {t["name"]: t["url"] for t in node["templates"]}
        assert urls["Face"] == "lib://root/Face.f3dhsm-template"
        assert urls["2D Adaptive"] == "lib://root/2D Adaptive.f3dhsm-template"

    def test_template_without_matching_asset_gets_none_url(self):
        tree = {"root": (["Lonely"], [], ["lib://root/Other.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        assert node["templates"][0]["url"] is None

    def test_descends_and_reports_nested_templates(self):
        tree = {"root": ([], ["sub"], []), "sub": (["Deep"], [], [])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        assert node["folders"][0]["templates"][0]["name"] == "Deep"

    def test_depth_limit_flags_folders_truncated(self):
        tree = {"root": ([], ["sub"], []), "sub": (["Deep"], [], [])}
        # max_depth=1 -> we are at depth 0, depth+1 (1) is NOT < 1, so we don't descend.
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 1, {"n": 0, "truncated": False})
        assert node["folders"] == []
        assert node.get("folders_truncated") is True


# ── save_operations_as_template_handler: input validation + missing-op detection ────────────────────

def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _SaveSetup:
    def __init__(self, name, op_names):
        self.name = name
        self.allOperations = [SimpleNamespace(name=n) for n in op_names]


class _SaveCAM:
    def __init__(self, setups):
        s = list(setups)
        self.setups = SimpleNamespace(count=len(s), item=lambda i: s[i])


def _wire_save(monkeypatch, cam):
    monkeypatch.setattr(ct, "get_cam", lambda: (cam, None))
    monkeypatch.setattr(ct, "_template_library", lambda: (SimpleNamespace(), None))
    import adsk.cam
    adsk.cam.Operation.cast = staticmethod(lambda x: x)


class TestSaveOperationsValidation:
    def test_missing_template_name(self):
        res = ct.save_operations_as_template_handler(template_name="", operations="a", setup="S")
        assert res["isError"] is True and "template_name" in res["message"]

    def test_missing_operations_list(self):
        res = ct.save_operations_as_template_handler(template_name="T", operations="  ,  ", setup="S")
        assert res["isError"] is True and "operations" in res["message"]

    def test_setup_not_found_lists_available(self, monkeypatch):
        _wire_save(monkeypatch, _SaveCAM([_SaveSetup("Roughing", ["a"])]))
        res = ct.save_operations_as_template_handler(
            template_name="T", operations="a", setup="Ghost")
        assert res["isError"] is True and "Roughing" in res["message"]

    def test_missing_operations_named_in_error(self, monkeypatch):
        _wire_save(monkeypatch, _SaveCAM([_SaveSetup("S", ["Face1", "Contour"])]))
        res = ct.save_operations_as_template_handler(
            template_name="T", operations="Face1, Ghost, AlsoGone", setup="S")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "AlsoGone" in res["message"]
        assert "Face1" not in res["message"].split("Available")[0]   # Face1 was found, not missing


class _RaiseOnName:
    """A template whose .name setter raises - simulates a read-only or locked property."""
    isValidTemplate = True
    description = ""

    def __getattribute__(self, item):
        if item == "name":
            return "original"
        return super().__getattribute__(item)

    def __setattr__(self, key, value):
        if key == "name":
            raise AttributeError("name is read-only")
        super().__setattr__(key, value)


class TestSaveTemplateRename:
    """Rename/description mutations must raise rather than silently save under the original name."""

    def _wire_full_save(self, monkeypatch, template_obj):
        cam = _SaveCAM([_SaveSetup("S", ["Face1"])])
        monkeypatch.setattr(ct, "get_cam", lambda: (cam, None))
        lib = SimpleNamespace(urlByLocation=lambda loc: "root://", childFolderURLs=lambda u: [],
                              importTemplate=lambda t, d: _Url("root://T1.f3dhsm-template"),
                              templateAtURL=lambda u: _FakeTemplate("T1"))
        monkeypatch.setattr(ct, "_template_library", lambda: (lib, None))
        import adsk.cam
        adsk.cam.Operation.cast = staticmethod(lambda x: x)
        adsk.cam.CAMTemplate.createFromOperations = staticmethod(lambda ops: template_obj)
        adsk.cam.CAMTemplate.cast = staticmethod(
            lambda x: x if isinstance(x, _FakeTemplate) or isinstance(x, _RaiseOnName) else None)
        return lib

    def test_rename_failure_propagates(self, monkeypatch):
        # A read-only .name assignment must raise and propagate out of the handler - a silent
        # no-op would save under the wrong name while reporting saved=True.
        import pytest
        t = _RaiseOnName()
        self._wire_full_save(monkeypatch, t)
        with pytest.raises(AttributeError, match="name is read-only"):
            ct.save_operations_as_template_handler(
                template_name="My Template", operations="Face1", setup="S")

    def test_rename_succeeds_reports_correct_name(self, monkeypatch):
        t = _FakeTemplate("old")
        self._wire_full_save(monkeypatch, t)
        out = _payload(ct.save_operations_as_template_handler(
            template_name="New Name", operations="Face1", setup="S"))
        assert out["saved"] is True and t.name == "New Name"

    def test_saved_template_that_does_not_load_back_bites(self, monkeypatch):
        # importTemplate returned a URL but nothing loads back from it -> error, not saved=True
        t = _FakeTemplate("old")
        lib = self._wire_full_save(monkeypatch, t)
        lib.templateAtURL = lambda u: None
        res = ct.save_operations_as_template_handler(
            template_name="Ghost", operations="Face1", setup="S")
        assert res["isError"] is True
        assert "did not land" in res["message"]


# ── cam_delete_template: the guarded, LOCAL-only removal ────────────────────────────────────────
#
# The mirror of cam_delete_machine. ONE fake template library serves the resolve, the asset walk,
# the identity gate and both read-backs, so the read the delete is ADDRESSED through is the same one
# it is CONFIRMED by - a delete "proved" against a second, differently-built view is the false ok
# this whole chain exists to prevent.

import pytest

_LOCAL = ct.adsk.cam.LibraryLocations.LocalLibraryLocation


class _Tmpl:
    """A CAMTemplate: the name a search matches and an identity gate compares."""

    def __init__(self, name):
        self.name = name
        self.description = ""
        self.isValidTemplate = True
        self.isHoleTemplate = False


class _TmplUrl:
    """An asset URL: leafName is the asset's NAME as stored, toString() its address. Two assets in
    different folders share a leaf name and differ only by that address."""

    def __init__(self, leaf, folder=""):
        self.leafName = leaf
        self._folder = folder

    def toString(self):
        return "template://local/" + (self._folder + "/" if self._folder else "") + self.leafName


class _TmplLib:
    """A template library rooted at one LOCAL location.

    ``tree`` maps a folder key ('' = the root) to the templates childTemplates lists there;
    ``assets`` is a list of (leafName, template-or-None, folder_key) the asset walk lists and
    templateAtURL loads from. ``delete_mode`` picks the outcome deleteAsset reports and leaves
    behind: 'ok', 'false', 'raise', 'keeps_asset' (the walk still lists it), 'keeps_loading'
    (the walk drops it but the url still loads a template) or 'walk_deepens' (the delete lands, but
    the folder tree grows past the walk's depth bound so the read-back cannot finish).
    ``root_limit`` makes urlByLocation stop answering after N calls - the location that resolves for
    one read and not the next.
    """

    def __init__(self, tree=None, assets=(), delete_mode="ok", root="LOCAL_ROOT",
                 folders=(), folder_depth=0, root_limit=None):
        self.tree = {k: list(v) for k, v in (tree or {}).items()}
        self._root = root
        self._folders = list(folders)
        self._folder_depth = folder_depth
        self._delete_mode = delete_mode
        self._root_limit = root_limit
        self._root_reads = 0
        self.deleted = []
        self.stored = {}
        self.asset_urls = []
        for entry in assets:
            leaf, tmpl = entry[0], entry[1]
            u = _TmplUrl(leaf, entry[2] if len(entry) > 2 else "")
            self.asset_urls.append(u)
            self.stored[u.toString()] = tmpl

    def urlByLocation(self, loc):
        if loc != _LOCAL:
            return "CLOUD_ROOT"
        self._root_reads += 1
        if self._root_limit is not None and self._root_reads > self._root_limit:
            return None
        return self._root

    def displayName(self, url):
        return str(url)

    def _key(self, url):
        return "" if url == self._root else str(url)

    def childTemplates(self, url):
        return list(self.tree.get(self._key(url), []))

    def childFolderURLs(self, url):
        if self._folder_depth:
            here = 0 if url == self._root else int(str(url).replace("FOLDER", "") or 0)
            return ["FOLDER%d" % (here + 1)] if here < self._folder_depth else []
        return list(self._folders) if url == self._root else []

    def childAssetURLs(self, url):
        key = self._key(url)
        return [u for u in self.asset_urls if u._folder == key]

    def templateAtURL(self, url):
        return self.stored.get(url.toString())

    def importTemplate(self, template, dest_url):
        u = _TmplUrl(template.name + ".f3dhsm-template", self._key(dest_url))
        self.stored[u.toString()] = template
        self.asset_urls.append(u)
        self.tree.setdefault(self._key(dest_url), []).append(template)
        return u

    def deleteAsset(self, url):
        if self._delete_mode == "raise":
            raise RuntimeError("library is read-only")
        if self._delete_mode == "false":
            return False
        if self._delete_mode == "root_gone":
            self._root = None
        if self._delete_mode == "walk_deepens":
            # the folder tree grows past the shared walk's own depth bound between the delete and
            # its read-back, so the POST-delete walk is the one that cannot finish
            self._folder_depth = 8
        key = url.toString()
        template = self.stored.get(key)
        if self._delete_mode != "keeps_asset":
            self.asset_urls = [a for a in self.asset_urls if a.toString() != key]
            for folder in self.tree.values():
                if template in folder:
                    folder.remove(template)
        if self._delete_mode not in ("keeps_asset", "keeps_loading"):
            self.stored.pop(key, None)
        self.deleted.append(key)
        return True


@pytest.fixture
def tlib(monkeypatch):
    """Install a template library for the delete path. Returns a factory handing back the library
    (its `deleted` list is the mutation record)."""
    def _make(**kwargs):
        lib = _TmplLib(**kwargs)
        monkeypatch.setattr(ct, "_template_library", lambda: (lib, None))
        return lib
    return _make


def _one(name="GyroTmpl", leaf=None, folder=""):
    """A template plus the asset entry that stores it - the pair every happy path needs."""
    t = _Tmpl(name)
    return t, (leaf if leaf is not None else name + ".f3dhsm-template", t, folder)


class TestDeleteTemplateGuards:
    """Nothing is deleted unless the template resolved in the LOCAL library, exactly one asset
    carries its name, and the caller confirmed the name the search actually reached."""

    def test_missing_name_is_refused(self, tlib):
        lib = tlib()
        res = ct.delete_template_handler(name="  ", confirm_name="x")
        assert res["isError"] is True and "'name'" in res["message"]
        assert lib.deleted == []

    def test_missing_confirm_name_is_refused(self, tlib):
        t, a = _one()
        lib = tlib(tree={"": [t]}, assets=[a])
        res = ct.delete_template_handler(name="GyroTmpl")
        assert res["isError"] is True and "'confirm_name'" in res["message"]
        assert lib.deleted == []

    def test_an_unknown_template_is_refused_naming_the_local_scope(self, tlib):
        # A template that lives only in the cloud library is simply not reached: this tool searches
        # the LOCAL root alone, and the refusal has to say so or the caller retries the same call.
        lib = tlib(tree={"": [_Tmpl("Other")]})
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "LOCAL template library" in res["message"]
        assert "Other" in res["message"] and "Nothing was deleted" in res["message"]
        assert lib.deleted == []

    def test_a_name_two_folders_share_is_refused_not_raced(self, tlib):
        t1, a1 = _one()
        t2, a2 = _one(leaf="GyroTmpl.f3dhsm-template", folder="Mills")
        lib = tlib(tree={"": [t1], "Mills": [t2]}, assets=[a1, a2], folders=["Mills"])
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "ambiguous" in res["message"] and "Nothing was deleted" in res["message"]
        assert lib.deleted == []

    def test_a_confirm_name_mismatch_is_refused_naming_both(self, tlib):
        # the search matches case-insensitively, the confirmation does NOT: the caller confirms the
        # name the library actually holds.
        t, a = _one()
        lib = tlib(tree={"": [t]}, assets=[a])
        res = ct.delete_template_handler(name="gyrotmpl", confirm_name="gyrotmpl")
        assert res["isError"] is True
        assert "Name mismatch" in res["message"]
        assert "confirm_name was 'gyrotmpl'" in res["message"]
        assert "confirm_name='GyroTmpl'" in res["message"]
        assert lib.deleted == []

    def test_an_unresolvable_local_root_for_the_asset_walk_is_refused(self, tlib):
        # the location answers the by-name search and then stops answering: the asset cannot be
        # addressed, so nothing is deleted rather than deleted blind.
        t, a = _one()
        lib = tlib(tree={"": [t]}, assets=[a], root_limit=1)
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "Local template library location" in res["message"]
        assert lib.deleted == []

    def test_no_asset_carrying_the_name_lists_what_the_library_holds(self, tlib):
        # the template answers childTemplates but no ASSET carries its name - the delete has nothing
        # to address, and the refusal names the assets that are there.
        t = _Tmpl("GyroTmpl")
        lib = tlib(tree={"": [t]},
                   assets=[("Other.f3dhsm-template", _Tmpl("Other")),
                           ("Third.f3dhsm-template", _Tmpl("Third"))])
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "No asset in the Local template library is named 'GyroTmpl'" in res["message"]
        # each asset rendered AS STORED, extension included - what Fusion's own library shows
        assert "Other.f3dhsm-template" in res["message"]
        assert lib.deleted == []

    def test_the_asset_is_matched_by_its_leaf_name_STEM(self, tlib):
        # the stored leafName carries the extension the template's name does not, so the match runs
        # on the stem - and stays EXACT: the sibling whose stem merely STARTS with the name survives.
        t, a = _one()
        lib = tlib(tree={"": [t]},
                   assets=[a, ("GyroTmpl Mk2.f3dhsm-template", _Tmpl("GyroTmpl Mk2"))])
        out = _payload(ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl"))
        assert out["asset_name"] == "GyroTmpl.f3dhsm-template"
        assert lib.deleted == ["template://local/GyroTmpl.f3dhsm-template"]
        assert [u.leafName for u in lib.asset_urls] == ["GyroTmpl Mk2.f3dhsm-template"]
        assert out["local_assets_remaining"] == 1

    def test_an_asset_stored_without_an_extension_still_resolves(self, tlib):
        t, a = _one(leaf="GyroTmpl")
        lib = tlib(tree={"": [t]}, assets=[a])
        out = _payload(ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl"))
        assert out["deleted"] is True and out["asset_name"] == "GyroTmpl"
        assert lib.deleted == ["template://local/GyroTmpl"]

    def test_two_assets_sharing_the_leaf_name_are_refused_not_guessed(self, tlib):
        # one asset in the root, one in a folder childTemplates does NOT list: the by-name search
        # sees a single template while the asset walk sees two candidates, and an irreversible
        # delete may not pick one.
        t, a = _one()
        lib = tlib(tree={"": [t]},
                   assets=[a, ("GyroTmpl.f3dhsm-template", _Tmpl("GyroTmpl"), "Mills")],
                   folders=["Mills"])
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "names 2 assets" in res["message"] and "refusing to guess" in res["message"]
        assert "template://local/Mills/GyroTmpl.f3dhsm-template" in res["message"]
        assert lib.deleted == []

    def test_an_incomplete_asset_walk_refuses_the_delete(self, tlib):
        # the walk that would have shown a same-named duplicate is the read that did not finish, so
        # the one-asset conclusion has no support and the delete fails CLOSED.
        t, a = _one()
        lib = tlib(tree={"": [t]}, assets=[a], folder_depth=8)
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "hit its own bound" in res["message"] and "only ONE asset" in res["message"]
        assert lib.deleted == []

    def test_a_zero_hit_search_discloses_an_incomplete_walk(self, tlib):
        lib = tlib(tree={"": [_Tmpl("GyroTmpl")]},
                   assets=[("Other.f3dhsm-template", _Tmpl("Other"))], folder_depth=8)
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "No asset in the Local template library is named" in res["message"]
        assert "incomplete" in res["message"]
        assert lib.deleted == []

    def test_an_asset_that_loads_nothing_is_refused(self, tlib):
        t = _Tmpl("GyroTmpl")
        lib = tlib(tree={"": [t]}, assets=[("GyroTmpl.f3dhsm-template", None)])
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "does not load a template" in res["message"]
        assert lib.deleted == []

    def test_an_asset_holding_a_different_template_is_refused(self, tlib):
        # the leaf NAME matched but the asset loads another template - deleting it would remove a
        # template the caller never confirmed.
        t = _Tmpl("GyroTmpl")
        lib = tlib(tree={"": [t]},
                   assets=[("GyroTmpl.f3dhsm-template", _Tmpl("Someone Else"))])
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "holds the template 'Someone Else'" in res["message"]
        assert lib.deleted == []


class TestDeleteTemplateEffect:
    """deleteAsset's own answer is never the proof: the library's assets are re-walked and the
    deleted url re-loaded, and either read still finding the template is an error."""

    def test_deletes_the_local_template_and_reads_the_library_back(self, tlib):
        t, a = _one()
        lib = tlib(tree={"": [t]}, assets=[a])
        out = _payload(ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl"))
        assert out["deleted"] is True and out["template"] == "GyroTmpl"
        assert out["location"] == "local"
        assert out["asset_name"] == "GyroTmpl.f3dhsm-template"
        assert out["url"] == "template://local/GyroTmpl.f3dhsm-template"
        assert out["loads_after_delete"] is False
        assert out["local_assets_remaining"] == 0
        assert lib.deleted == ["template://local/GyroTmpl.f3dhsm-template"]
        # the asset walk is what the claim rests on, and the note says so
        assert "re-walk of the library's own assets" in out["note"]
        assert "cam_save_template" in out["note"]

    def test_a_template_saved_here_can_be_deleted_by_the_name_it_was_saved_under(self, monkeypatch,
                                                                                 tlib):
        # the sweep teardown's own round trip: save into the local library, then take it back out.
        lib = tlib()
        cam = _SaveCAM([_SaveSetup("S", ["Face1"])])
        monkeypatch.setattr(ct, "get_cam", lambda: (cam, None))
        import adsk.cam
        adsk.cam.Operation.cast = staticmethod(lambda x: x)
        made = _Tmpl("old")
        adsk.cam.CAMTemplate.createFromOperations = staticmethod(lambda ops: made)
        adsk.cam.CAMTemplate.cast = staticmethod(lambda x: x if isinstance(x, _Tmpl) else None)
        saved = _payload(ct.save_operations_as_template_handler(
            template_name="GyroTmpl 20260830", operations="Face1", setup="S", location="local"))
        assert saved["saved"] is True
        out = _payload(ct.delete_template_handler(name=saved["template"],
                                                  confirm_name=saved["template"]))
        assert out["deleted"] is True and out["loads_after_delete"] is False
        assert lib.asset_urls == []

    def test_a_declined_delete_is_an_error_not_a_false_ok(self, tlib):
        t, a = _one()
        lib = tlib(tree={"": [t]}, assets=[a], delete_mode="false")
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "deleteAsset returned false" in res["message"]
        assert [u.leafName for u in lib.asset_urls] == ["GyroTmpl.f3dhsm-template"]

    def test_a_raising_delete_is_reported(self, tlib):
        t, a = _one()
        tlib(tree={"": [t]}, assets=[a], delete_mode="raise")
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "read-only" in res["message"] and "failed" in res["message"]

    def test_an_asset_still_listed_after_a_true_delete_is_an_error(self, tlib):
        t, a = _one()
        tlib(tree={"": [t]}, assets=[a], delete_mode="keeps_asset")
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        # named by the STORED leaf name - what the reader has to find in the library
        assert "still lists 'GyroTmpl.f3dhsm-template'" in res["message"]

    def test_a_url_that_still_loads_a_template_is_not_a_confirmed_delete(self, tlib):
        # the asset is gone from the walk but the address still serves a template: two reads
        # disagreeing is not a delete, whatever deleteAsset answered.
        t, a = _one()
        tlib(tree={"": [t]}, assets=[a], delete_mode="keeps_loading")
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "still loads from its url" in res["message"]
        assert "UNCONFIRMED" in res["message"]

    def test_a_read_back_walk_that_did_not_FINISH_leaves_the_delete_unconfirmed(self, tlib):
        # the twin of the root-gone case, on the walk's other failure: the folder tree grows past
        # the shared walk's depth bound between the delete and its read-back. An empty match against
        # a walk that stopped early is not evidence the asset is gone - a same-named asset past the
        # bound would not have been seen - so the delete is reported UNCONFIRMED, not as done.
        t, a = _one()
        lib = tlib(tree={"": [t]}, assets=[a], delete_mode="walk_deepens")
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "asset walk hit its own bound before finishing" in res["message"]
        assert "UNCONFIRMED" in res["message"]
        assert lib.deleted == ["template://local/GyroTmpl.f3dhsm-template"]   # the delete DID fire

    def test_a_read_back_walk_that_cannot_answer_leaves_the_delete_unconfirmed(self, tlib):
        # the library location stops resolving between the delete and the read-back: an empty match
        # against a walk that answered nothing is not evidence, so no delete is claimed.
        t, a = _one()
        tlib(tree={"": [t]}, assets=[a], delete_mode="root_gone")
        res = ct.delete_template_handler(name="GyroTmpl", confirm_name="GyroTmpl")
        assert res["isError"] is True
        assert "UNCONFIRMED" in res["message"] and "no longer resolves" in res["message"]
