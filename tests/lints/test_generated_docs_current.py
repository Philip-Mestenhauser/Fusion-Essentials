"""Lint: the generated docs (TOOL_MANIFEST/TOOL_POINTER_MAP/PERMISSION_POSTURE + the CLAUDE.md map) match the live tree.

Four scripts derive documentation from the registry/source instead of being hand-maintained:
``gen_manifest.py`` (TOOL_MANIFEST.md + the CLAUDE.md map from the registry), ``gen_wiring.py``
(tests/generated/TOOL_POINTER_MAP.md from the tool source), ``gen_posture.py`` (PERMISSION_POSTURE.md
from the registry's write-status truth), ``gen_api_surface.py`` (tests/api_surface.py from the
installed Fusion bindings). ``gen_all.py`` fronts all four in one process and its ``--check`` exits
non-zero if any output would differ from what is committed. Since agents run pytest constantly but
rarely remember to re-run a generator, this test shells that ``--check`` so a stale doc shows up as a
normal test failure instead of silently rotting.

That check costs ~6.5s (measured; almost all of it real work - a bare interpreter start is 0.03s), so
it runs only when something it reads or writes has CHANGED since a check last passed. The gate is a
content FINGERPRINT over both halves at once: every INPUT the generators read (the whole
commands/mcpServer tree plus the scripts under tests/) and every OUTPUT they write. A run whose
fingerprint matches one a passing check already saw cannot be looking at a stale artifact - staleness
means a byte differs somewhere in that set. Everything else misses and pays the full check: a first
run, an edited tool, a regenerated doc, a HAND-EDITED generated doc (which is why the outputs are in
the fingerprint, not just the inputs), and a Fusion update that moves the bindings.

The failure surface is unchanged - only the subprocess can report a verdict, and it reports the same
one with the same regen command. Adding a generator that reads something OUTSIDE those two sets means
adding it to ``_fingerprinted_paths`` in the same change, or its input goes unwatched.
"""

import glob
import hashlib
import os
import subprocess
import sys

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/lints/ -> tests/
REPO_ROOT = os.path.dirname(TESTS_DIR)

_CACHE_KEY = "fusion_essentials/generated_docs_fingerprint"

# What the generators READ: the whole add-in tree (tools + primitives + server - gen_manifest and
# gen_posture load every module through the registry, gen_wiring parses every tool's source) and the
# scripts under tests/ that do the deriving.
_INPUT_TREE = os.path.join(REPO_ROOT, "commands", "mcpServer")
# What they WRITE. The two CLAUDE.md files are SPLICED, so only a block of each is generated - the
# whole file is fingerprinted anyway, which costs a needless miss on an unrelated edit and never a
# missed staleness.
_OUTPUT_TREE = os.path.join(TESTS_DIR, "generated")
_OUTPUT_FILES = (
    os.path.join(TESTS_DIR, "api_surface.py"),
    os.path.join(REPO_ROOT, "CLAUDE.md"),
    os.path.join(REPO_ROOT, "commands", "mcpServer", "tools", "CLAUDE.md"),
)


def _bindings_identity():
    """A cheap stand-in for hashing the Fusion bindings' megabytes: their directory plus each
    scanned module's size and mtime. A Fusion update installs a NEW webdeploy build directory, so
    the path itself moves; size+mtime cover a module rewritten in place. gen_api_surface reads
    these and nothing in the repo records their content, so without this a Fusion update would
    leave tests/api_surface.py stale behind a fingerprint that never noticed."""
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    import gen_api_surface
    root = gen_api_surface.find_bindings()
    if root is None:
        # gen_api_surface --check FAILS without bindings; the miss lets it say so itself.
        return "bindings:absent"
    parts = [root]
    for mod in gen_api_surface._MODULES:
        path = os.path.join(root, mod + ".py")
        if os.path.isfile(path):
            st = os.stat(path)
            parts.append(f"{mod}:{st.st_size}:{st.st_mtime_ns}")
        else:
            parts.append(f"{mod}:absent")
    return "|".join(parts)


def _fingerprinted_paths():
    """Every file whose bytes decide whether the committed artifacts are current, sorted and
    de-duplicated (an output that also sits under the input tree is hashed once)."""
    paths = set(_OUTPUT_FILES)
    paths.update(glob.glob(os.path.join(TESTS_DIR, "*.py")))
    for root, _dirs, names in os.walk(_INPUT_TREE):
        if "__pycache__" in root:
            continue
        paths.update(os.path.join(root, n) for n in names if n.endswith(".py"))
    for root, _dirs, names in os.walk(_OUTPUT_TREE):
        paths.update(os.path.join(root, n) for n in names)
    return tuple(sorted(p for p in paths if os.path.isfile(p)))


def _digest(paths, salt=""):
    """One hash over `paths`' NAMES and contents. The name is hashed beside the bytes so a file
    that moved - same content, different path - is a different fingerprint, not an invisible one."""
    h = hashlib.sha256()
    h.update(salt.encode("utf-8"))
    h.update(b"\0")
    for path in paths:
        h.update(os.path.relpath(path, REPO_ROOT).replace("\\", "/").encode("utf-8"))
        h.update(b"\0")
        with open(path, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def _fingerprint():
    return _digest(_fingerprinted_paths(), salt=_bindings_identity())


class TestGeneratedDocsAreCurrent:
    def test_generator_check_passes(self, pytestconfig):
        fingerprint = _fingerprint()
        cache = getattr(pytestconfig, "cache", None)
        if cache is not None and cache.get(_CACHE_KEY, None) == fingerprint:
            return   # every byte this check reads and writes is what a passing check already saw
        proc = subprocess.run(
            [sys.executable, os.path.join(TESTS_DIR, "gen_all.py"), "--check"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            "a generated doc is stale. Regenerate with `py -3 tests/gen_all.py` and commit the "
            f"result.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        if cache is not None:
            cache.set(_CACHE_KEY, fingerprint)


class TestTheFingerprintCannotGreenOverAChange:
    """The fingerprint is what decides the expensive check may be skipped, so it has to move for
    every change that could make an artifact stale. A digest that did not would silently turn this
    lint off."""

    def _files(self, tmp_path, contents):
        out = []
        for name, body in contents:
            p = tmp_path / name
            p.write_bytes(body)
            out.append(str(p))
        return out

    def test_one_changed_byte_changes_the_digest(self, tmp_path):
        paths = self._files(tmp_path, [("a.md", b"generated\n"), ("b.py", b"x = 1\n")])
        before = _digest(paths)
        (tmp_path / "a.md").write_bytes(b"generated!\n")
        assert _digest(paths) != before

    def test_identical_bytes_give_the_same_digest(self, tmp_path):
        paths = self._files(tmp_path, [("a.md", b"generated\n")])
        assert _digest(paths) == _digest(paths)

    def test_the_same_content_under_a_different_name_is_a_different_digest(self, tmp_path):
        one = self._files(tmp_path, [("a.md", b"same\n")])
        two = self._files(tmp_path, [("b.md", b"same\n")])
        assert _digest(one) != _digest(two)

    def test_swapping_two_files_contents_changes_the_digest(self, tmp_path):
        # a length-only or order-blind digest would hash the pair identically either way round
        paths = self._files(tmp_path, [("a.md", b"alpha\n"), ("b.md", b"bravo\n")])
        before = _digest(paths)
        (tmp_path / "a.md").write_bytes(b"bravo\n")
        (tmp_path / "b.md").write_bytes(b"alpha\n")
        assert _digest(paths) != before

    def test_the_bindings_identity_is_part_of_the_digest(self, tmp_path):
        # a Fusion update changes nothing in the repo, so only the salt can carry it
        paths = self._files(tmp_path, [("a.md", b"same\n")])
        assert _digest(paths, salt="build-A") != _digest(paths, salt="build-B")

    def test_the_bindings_identity_names_the_installed_build(self):
        ident = _bindings_identity()
        assert ident == "bindings:absent" or ident.count("|") == 4, ident


class TestTheFingerprintCoversEveryArtifact:
    """A path missing from the set is a hole the cache would sit in permanently: that artifact could
    be hand-edited, or its source changed, and the check would never be asked again."""

    def test_every_generated_artifact_is_fingerprinted(self):
        watched = set(_fingerprinted_paths())
        for artifact in ("tests/generated/TOOL_MANIFEST.md",
                         "tests/generated/TOOL_POINTER_MAP.md",
                         "tests/generated/PERMISSION_POSTURE.md",
                         "tests/api_surface.py",
                         "CLAUDE.md",
                         "commands/mcpServer/tools/CLAUDE.md"):
            path = os.path.join(REPO_ROOT, *artifact.split("/"))
            assert os.path.isfile(path), f"{artifact} is not where this lint looks for it"
            assert path in watched, f"{artifact} is generated but not fingerprinted"

    def test_every_generator_script_is_fingerprinted(self):
        watched = set(_fingerprinted_paths())
        for script in ("gen_all.py", "gen_manifest.py", "gen_wiring.py", "gen_posture.py",
                       "gen_api_surface.py", "conftest.py"):
            path = os.path.join(TESTS_DIR, script)
            assert path in watched, f"tests/{script} decides the output and must be fingerprinted"

    def test_the_whole_tool_tree_is_fingerprinted(self):
        """gen_wiring parses every tool module, so any one of them can stale TOOL_POINTER_MAP."""
        watched = set(_fingerprinted_paths())
        tools = glob.glob(os.path.join(_INPUT_TREE, "tools", "*.py"))
        assert len(tools) > 150, f"only {len(tools)} tool modules found - the tree moved"
        missing = sorted(p for p in tools if p not in watched)
        assert not missing, f"tool modules outside the fingerprint: {missing[:5]}"
