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
# The live API annotation is list[Operation] but the docstring claims a CAMTemplate. The old save
# code assumed a single CAMTemplate and set .name on it / passed it to importTemplate — which breaks
# if the binding actually returns a list. These pin the normalisation for BOTH shapes so the save
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
    monkeypatch.setattr(ct, "_get_cam", lambda: (cam, None))
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
