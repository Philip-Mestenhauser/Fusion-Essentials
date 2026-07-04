"""Unit tests for ``sys_reload_addin._purge_addin_modules`` -- the sys.modules cache-bust that
lets a reload re-import EDITED files instead of handing back the stale cached objects. The only
pure logic here is which module names it purges vs keeps, by comparing each module's __file__
against the add-in's root folder; that decision is exercised directly against the real
``sys.modules`` dict via ``monkeypatch.setitem`` (auto-restored, so the test can't leak a fake
module entry into the rest of the suite).
"""

import os
import types

from conftest import load_tool

ra = load_tool("sys_reload_addin")

_FAKE_ROOT = os.path.abspath(os.path.join(os.sep, "FakeAddinRoot"))


def _module_at(path):
    m = types.ModuleType("fake")
    m.__file__ = path
    return m


def _install_root(monkeypatch, root=_FAKE_ROOT):
    monkeypatch.setattr(ra, "_addin_root_folder", lambda: root)


class TestPurgeAddinModules:
    def test_purges_a_module_whose_file_is_under_the_addin_root(self, monkeypatch):
        _install_root(monkeypatch)
        name = "fake_addin_tool_under_root"
        monkeypatch.setitem(ra.sys.modules, name,
                            _module_at(os.path.join(_FAKE_ROOT, "commands", "mcpServer", "tools", "foo.py")))
        ra._purge_addin_modules()
        assert name not in ra.sys.modules

    def test_keeps_a_module_whose_file_is_outside_the_addin_root(self, monkeypatch):
        _install_root(monkeypatch)
        name = "fake_unrelated_module"
        outside = os.path.abspath(os.path.join(os.sep, "SomewhereElse", "bar.py"))
        monkeypatch.setitem(ra.sys.modules, name, _module_at(outside))
        ra._purge_addin_modules()
        assert name in ra.sys.modules

    def test_keeps_a_module_with_no_file_attribute(self, monkeypatch):
        # built-ins / namespace packages have no __file__ - must never be touched.
        _install_root(monkeypatch)
        name = "fake_builtin_like_module"
        monkeypatch.setitem(ra.sys.modules, name, types.ModuleType("fake"))
        ra._purge_addin_modules()
        assert name in ra.sys.modules

    def test_skips_none_entries_without_raising(self, monkeypatch):
        # sys.modules can hold None as a placeholder for a failed/blocked import.
        _install_root(monkeypatch)
        name = "fake_none_placeholder"
        monkeypatch.setitem(ra.sys.modules, name, None)
        ra._purge_addin_modules()          # must not raise
        assert ra.sys.modules[name] is None

    def test_return_value_counts_only_the_purged_modules(self, monkeypatch):
        _install_root(monkeypatch)
        under_name = "fake_addin_tool_under_root_2"
        outside_name = "fake_unrelated_module_2"
        monkeypatch.setitem(ra.sys.modules, under_name,
                            _module_at(os.path.join(_FAKE_ROOT, "tools", "bar.py")))
        monkeypatch.setitem(ra.sys.modules, outside_name,
                            _module_at(os.path.abspath(os.path.join(os.sep, "Other", "baz.py"))))
        before = ra._purge_addin_modules()
        assert under_name not in ra.sys.modules
        assert outside_name in ra.sys.modules
        assert before >= 1
