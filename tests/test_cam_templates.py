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
