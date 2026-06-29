"""Lint: every tool that DECLARES outputs (a RETURNS spec) must honour the contract.

The _outputs.py framework lets a producer declare the stable ids it mints (RETURNS = [...]). This lint
holds the declaration to account so it can't drift from reality:

  1. Each declared output's payload KEY must appear in the tool's source — a producer that renames
     'handle'->'token' but forgets to update RETURNS (or vice-versa) fails here, instead of silently
     lying to the ~18 consumers whose input notes reference it (architecture §10's enforcement gap).
  2. The tool's description must carry the generated PRODUCES: block — so the chain prose is the
     single-source-of-truth declaration, present on the producer's surface.
  3. RETURNS entries are OutputKinds (not ad-hoc dicts).

It also discovers EVERY tool module with a RETURNS, so adopting the framework on a new tool
automatically opts it into these checks (no per-tool wiring).
"""

import os

from conftest import load_tool, TOOLS_DIR

_outputs = load_tool("_outputs")


def _tools_with_returns():
    """(module_name, module) for every tools/*.py that declares a RETURNS spec."""
    found = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        name = fn[:-3]
        mod = load_tool(name)
        if getattr(mod, "RETURNS", None):
            found.append((name, mod))
    return found


class TestDeclaredOutputs:
    def test_at_least_the_known_producers_declare_returns(self):
        # A floor so this lint can't pass vacuously if discovery breaks.
        names = {n for n, _ in _tools_with_returns()}
        assert {"find_geometry", "doc_get_active_id", "cam_generate"} <= names

    def test_returns_entries_are_output_kinds(self):
        for name, mod in _tools_with_returns():
            for o in mod.RETURNS:
                assert isinstance(o, _outputs.OutputKind), (
                    f"{name}.RETURNS has a non-OutputKind entry: {o!r}")

    def test_declared_key_appears_in_source(self):
        offenders = []
        for name, mod in _tools_with_returns():
            src = open(os.path.join(TOOLS_DIR, f"{name}.py"), encoding="utf-8").read()
            for o in mod.RETURNS:
                # The key must be written somewhere in the module (the payload that mints it).
                if f'"{o.key}"' not in src and f"'{o.key}'" not in src:
                    offenders.append(f"{name}: declared output '{o.key}' never appears in source")
        assert not offenders, "\n".join(offenders)

    def test_description_carries_the_produces_block(self):
        offenders = []
        for name, mod in _tools_with_returns():
            # Find the tool's description on its registered primitive(s).
            descs = []
            from mcpServer.mcp_primitives import registry
            registry.reset_registry()
            rt = getattr(mod, "register_tool", None)
            if callable(rt):
                rt()
            for it in registry.get_tools():
                d = it.to_dict().get("description", "")
                if d:
                    descs.append(d)
            blob = "\n".join(descs)
            for o in mod.RETURNS:
                if o.produces_note() not in blob:
                    offenders.append(f"{name}: PRODUCES line for '{o.key}' not in any description")
        assert not offenders, "\n".join(offenders)
