"""Unit tests for ``_cam_templates.py`` - the template library walk and by-name search.

Targets ``_find_template_by_name``: the location validation (unknown location -> hint),
case-insensitive matching, and recursive descent into subfolders; plus ``_walk_library``'s
asset-URL pairing, depth limit and node cap. A fake library models the ``childTemplates`` /
``childFolderURLs`` / ``urlByLocation`` surface the walker uses - no live CAM needed.
"""

import json
from types import SimpleNamespace

from conftest import load_tool

ct = load_tool("_cam_templates")

class FakeLib:
    """Minimal templateLibrary: a folder tree of named templates.

    ``tree`` maps a folder URL (any hashable) to (templates, subfolder_urls).
    ``urlByLocation`` returns the configured root url regardless of enum value.
    childAssetURLs answers ONE asset per DISTINCT template name in the folder - the shape measured
    across the shipped libraries (no folder holds two templates of one name), and the asset side
    the by-name resolve reads a repeated arrival's identity from. ``templates_by_url`` keys the
    ONE-asset resolve (templateAtURL) by the exact url string handed to it."""
    def __init__(self, root_url, tree, templates_by_url=None):
        self._root = root_url
        self._tree = tree
        self._templates_by_url = templates_by_url or {}

    def urlByLocation(self, loc):
        return self._root

    def childTemplates(self, folder_url):
        templates, _ = self._tree.get(folder_url, ([], []))
        return [SimpleNamespace(name=n) for n in templates]

    def childAssetURLs(self, folder_url):
        templates, _ = self._tree.get(folder_url, ([], []))
        return [_Url(f"lib://{folder_url}/{n}.f3dhsm-template")
                for n in dict.fromkeys(templates)]

    def childFolderURLs(self, folder_url):
        _, subs = self._tree.get(folder_url, ([], []))
        return subs

    def templateAtURL(self, url):
        return self._templates_by_url.get(url)


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

    def test_a_truncated_walk_with_one_hit_refuses_not_declares_unique(self, monkeypatch):
        # the walk hits its own node cap before "sub" - where a duplicate 'Face' sits - is ever
        # visited, so the one hit the root gave up is not shown to be the only one.
        monkeypatch.setattr(ct, "_MAX_NODES", 1)
        lib = FakeLib("root", {"root": (["Face"], ["sub"]), "sub": (["Face"], [])})
        template, hint = ct._find_template_by_name(lib, "cloud", "Face")
        assert template is None
        assert "One template named 'Face' was found under 'cloud'" in hint
        assert "the library walk stopped" in hint and "template_url" in hint
        assert ct.hint_is_self_contained(hint) is True


class TestHintIsSelfContained:
    """The ONE marker both cam_apply_template.py and cam_delete_template.py check through, so
    neither wraps a found-but-refused hint in a 'not found' sentence."""

    def test_the_ambiguous_hint_is_self_contained(self):
        assert ct.hint_is_self_contained("'X' is ambiguous - 2 templates share that name.") is True

    def test_the_truncated_hint_is_self_contained(self):
        assert ct.hint_is_self_contained(
            "One template named 'X' was found under 'local', but the library walk stopped "
            "at 1500 nodes, so it may not be the only one - pass template_url.") is True

    def test_a_plain_not_found_hint_is_not_self_contained(self):
        assert ct.hint_is_self_contained("Templates seen: A, B, C.") is False

    def test_no_hint_at_all_is_not_self_contained(self):
        assert ct.hint_is_self_contained(None) is False
        assert ct.hint_is_self_contained("") is False

    def test_a_name_arriving_twice_in_one_folder_still_resolves(self):
        # the folder's own listing hands one template back twice; refusing that as an ambiguity
        # leaves the template unreachable by name and by url alike.
        lib = FakeLib("root", {"root": (["Drill", "Drill"], [])})
        template, hint = ct._find_template_by_name(lib, "cloud", "Drill")
        assert template is not None and template.name == "Drill"
        assert hint is None

    def test_a_name_two_assets_in_one_folder_answer_is_refused(self):
        # two arrivals the asset side cannot tell apart: nothing here identifies one, so the
        # resolve refuses instead of applying whichever the walk reached first.
        lib = FakeLib("root", {"root": (["Drill", "Drill"], [])})
        lib.childAssetURLs = lambda folder_url: [_Url("lib://root/Drill.f3dhsm-template"),
                                                 _Url("lib://root/DRILL.f3dhsm-template")]
        template, hint = ct._find_template_by_name(lib, "cloud", "Drill")
        assert template is None and "ambiguous" in hint

    def test_one_name_in_two_folders_is_still_refused(self):
        # the boundary the de-dup must not cross: two FOLDERS carrying the name are two assets, and
        # picking either would apply a template the caller did not name.
        lib = FakeLib("root", {"root": (["Drill"], ["sub"]), "sub": (["Drill"], [])})
        template, hint = ct._find_template_by_name(lib, "cloud", "Drill")
        assert template is None and "ambiguous" in hint

    def test_one_asset_listed_under_two_folders_resolves(self):
        # MEASURED on the Local library: one saved template is listed under the root AND under its
        # own folder, both listings carrying the SAME asset url - one template, not an ambiguity.
        lib = FakeLib("root", {"root": (["Gyro"], ["sub"]), "sub": (["Gyro"], [])})
        lib.childAssetURLs = lambda folder_url: [_Url("lib://root/Gyro.f3dhsm-template")]
        template, hint = ct._find_template_by_name(lib, "cloud", "Gyro")
        assert template is not None and template.name == "Gyro"
        assert hint is None


# ── list_cam_templates_handler: a template_url that addresses ONE ASSET, not a folder ──────────────

class _FakeTemplate:
    def __init__(self, name="Block Facing Pocket Template", description="", valid=True, hole=False):
        self.name = name
        self.description = description
        self.isValidTemplate = valid
        self.isHoleTemplate = hole


class TestListTemplatesAssetUrl:
    """cam_get(include=['templates'], template_url=...): a url ending '.f3dhsm-template' - the
    suffix every row's own 'url' already carries - resolves as the ONE template at it, the same
    templateAtURL read cam_apply_template's own template_url takes."""

    def _wire(self, monkeypatch, templates_by_url, tree=None):
        lib = FakeLib("root", tree or {}, templates_by_url)
        monkeypatch.setattr(ct, "_template_library", lambda: (lib, None))
        monkeypatch.setattr(ct.adsk.core.URL, "create", lambda s: s, raising=False)
        return lib

    def test_an_asset_url_publishes_the_one_row(self, monkeypatch):
        url = "user://MCP Demo Templates/Block_Facing_Pocket_Template.f3dhsm-template"
        self._wire(monkeypatch, {url: _FakeTemplate("Block Facing Pocket Template")})
        out = ct.list_cam_templates_handler(url=url)
        payload = json.loads(out["content"][0]["text"])
        assert payload["node_count"] == 1 and payload["truncated"] is False
        row = payload["tree"]["templates"][0]
        assert row["name"] == "Block Facing Pocket Template"
        assert row["url"] == url
        assert row["folder_url"] == "user://MCP Demo Templates"

    def test_an_unknown_asset_url_refuses_naming_the_folder(self, monkeypatch):
        url = "user://MCP Demo Templates/Ghost.f3dhsm-template"
        self._wire(monkeypatch, {})
        out = ct.list_cam_templates_handler(url=url)
        assert out["isError"] is True
        # asserts the FOLDER CLAUSE itself, not just the echoed input url the message already
        # carries elsewhere - a rsplit -> split mutant (folder url 'user:') must go red here.
        assert "Its folder url is 'user://MCP Demo Templates'" in out["message"]

    def test_a_folder_url_still_walks(self, monkeypatch):
        self._wire(monkeypatch, {}, tree={"root": (["Face"], [])})
        out = ct.list_cam_templates_handler(url="root")
        payload = json.loads(out["content"][0]["text"])
        assert payload["node_count"] == 1
        assert payload["tree"]["templates"][0]["name"] == "Face"


# ── _walk_library: asset-URL matching + depth limit + node cap ──────────────────────────────────────
# The string parse in _asset_url_for is a classic silent-wrong-string risk: it must pair a template
# NAME to its asset URL by the stem of "<folder>/<name>.f3dhsm-template". The depth limit must flag
# 'folders_truncated', and the global node cap must flag 'truncated'.


class _Url:
    """A library URL: toString() plus the leafName the shared asset_leaf_keys reading takes - the
    last path segment, as adsk.core.URL answers it."""
    def __init__(self, s): self._s = s
    def toString(self): return self._s
    @property
    def leafName(self): return self._s.rstrip("/").rsplit("/", 1)[-1]


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
        # two assets beside one template: no name matches and the lists are different lengths, so
        # there is no position that addresses this template either.
        tree = {"root": (["Lonely"], [], ["lib://root/Other.f3dhsm-template",
                                          "lib://root/Third.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        assert node["templates"][0]["url"] is None
        assert "url_basis" not in node["templates"][0]

    def test_a_folder_whose_leaf_names_match_no_template_pairs_by_position(self):
        # the shipped hole templates' shape: real asset urls the walk publishes as null because no
        # leafName spells the template's name, which leaves template_url unreachable for them.
        tree = {"root": (["Drill", "Bore"], [],
                         ["lib://root/hole_a.f3dhsm-template", "lib://root/hole_b.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        rows = {t["name"]: t for t in node["templates"]}
        assert rows["Drill"]["url"] == "lib://root/hole_a.f3dhsm-template"
        assert rows["Bore"]["url"] == "lib://root/hole_b.f3dhsm-template"
        assert rows["Drill"]["url_basis"] == "folder_position"

    def test_one_name_matching_holds_the_rest_back_from_position_pairing(self):
        # a mixed folder: the name match proves the leafName spelling is in use here, so the odd
        # template out is NOT addressed by an index that would name another template's asset.
        tree = {"root": (["Face", "Bore"], [],
                         ["lib://root/Face.f3dhsm-template", "lib://root/hole_b.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        rows = {t["name"]: t for t in node["templates"]}
        assert rows["Face"]["url"] == "lib://root/Face.f3dhsm-template"
        assert rows["Bore"]["url"] is None

    def test_a_name_listed_twice_in_one_folder_is_one_row(self):
        # the flattened listing hands the same template back twice; two rows would double the
        # library's own census and refuse the name as ambiguous everywhere it is resolved.
        tree = {"root": (["Drill", "Drill"], [], ["lib://root/Drill.f3dhsm-template"])}
        counter = {"n": 0, "truncated": False}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, counter)
        assert [t["name"] for t in node["templates"]] == ["Drill"]
        assert node["templates"][0]["url"] == "lib://root/Drill.f3dhsm-template"
        assert node["templates_collided"] == 1     # dropped, and counted rather than silent
        assert counter["n"] == 1

    def test_two_assets_answering_one_name_stay_two_rows(self):
        # the boundary the identity keying exists for: two DISTINCT assets whose leafNames both
        # read 'drill' are two templates, and collapsing them would drop one from the library's
        # own census. Neither name identifies an asset, so both rows fall to the index pairing.
        tree = {"root": (["Drill", "Drill"], [],
                         ["lib://root/Drill.f3dhsm-template", "lib://root/DRILL.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        assert [t["name"] for t in node["templates"]] == ["Drill", "Drill"]
        assert [t["url"] for t in node["templates"]] == ["lib://root/Drill.f3dhsm-template",
                                                         "lib://root/DRILL.f3dhsm-template"]
        assert all(t["url_basis"] == "folder_position" for t in node["templates"])
        assert "templates_collided" not in node

    def test_one_asset_listed_in_two_folders_is_one_row(self):
        # the Local library's own shape: the saved template is listed under the root AND under its
        # folder, both rows carrying the same asset url. Two rows would publish one template twice
        # and refuse its name as ambiguous everywhere it is resolved.
        tree = {"root": (["Gyro"], ["sub"], ["lib://root/Gyro.f3dhsm-template"]),
                "sub": (["Gyro"], [], ["lib://root/Gyro.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        assert [t["url"] for t in node["templates"]] == ["lib://root/Gyro.f3dhsm-template"]
        assert node["folders"][0]["templates"] == []
        assert node["folders"][0]["templates_collided"] == 1

    def test_a_dropped_row_holds_the_rest_back_from_position_pairing(self):
        # the folder lists a template already seen under the root AND one no leafName names. With
        # the duplicate dropped the two lists are the same length again, and pairing by INDEX would
        # hand the surviving template the dropped one's asset url.
        tree = {"root": (["Gyro"], ["sub"], ["lib://root/Gyro.f3dhsm-template"]),
                "sub": (["Gyro", "Bore"], [], ["lib://root/Gyro.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        sub = node["folders"][0]
        assert [t["name"] for t in sub["templates"]] == ["Bore"]
        assert sub["templates"][0]["url"] is None
        assert "url_basis" not in sub["templates"][0]
        assert sub["templates_collided"] == 1

    def test_two_distinct_assets_in_two_folders_stay_two_rows(self):
        # the boundary the walk-wide keying must not cross: two DISTINCT urls are two templates.
        tree = {"root": (["Gyro"], ["sub"], ["lib://root/Gyro.f3dhsm-template"]),
                "sub": (["Gyro"], [], ["lib://sub/Gyro.f3dhsm-template"])}
        node = ct._walk_library(_WalkLib(tree), _Url("root"), 0, 4, {"n": 0, "truncated": False})
        assert [t["url"] for t in node["templates"]] == ["lib://root/Gyro.f3dhsm-template"]
        assert [t["url"] for t in node["folders"][0]["templates"]] == [
            "lib://sub/Gyro.f3dhsm-template"]

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
