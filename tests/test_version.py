"""Version story: one version source (version.py), reported on the wire and matched by the changelog."""

import importlib
import os
import re
import sys

from conftest import COMMANDS_DIR, load_mcp_server

CHANGELOG_PATH = os.path.join(COMMANDS_DIR, "mcpServer", "CHANGELOG.md")


def _version():
    if COMMANDS_DIR not in sys.path:
        sys.path.insert(0, COMMANDS_DIR)
    return importlib.import_module("mcpServer.version").__version__


def test_version_is_semver_shaped():
    assert re.match(r"^\d+\.\d+\.\d+$", _version())


def test_changelog_top_entry_matches_version():
    with open(CHANGELOG_PATH, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^## (\d+\.\d+\.\d+)", text, re.MULTILINE)
    assert m, "no '## <semver>' heading found in CHANGELOG.md"
    assert m.group(1) == _version(), (
        f"CHANGELOG top entry {m.group(1)} != version.py {_version()} - bump both together")


def test_server_info_version_comes_from_version_module():
    mcp_server = load_mcp_server()
    srv = mcp_server.SimpleMCPServer()
    assert srv.server_info["version"] == _version()
