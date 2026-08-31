"""Unit tests for the LOCAL machine library pair: ``cam_create_machine`` (a machine built from a
template into the library) and ``cam_delete_machine`` (the same machine taken back out).

ONE fake machine library drives both tools, which is why both live in this file: the delete's
read-backs are only meaningful against the library state the create left behind, and a second copy
of the fake is a second contract. The fake serves the real ``_cam_common`` helpers the tools call
too, so the integration seam is exercised rather than stubbed. Its ``createQuery`` matches vendor
exactly and model by prefix - the shape ``resolve_machine`` is written against, whose widen path
re-splits a label into (vendor, model) because a label does not match the model field. The create
writes the name to both fields and gates on a re-resolve, so what the query indexes never has to be
assumed. ``importMachine`` hands the library its OWN copy with a distinct id, so a payload
assembled from the pre-store object reports a machine the library has not got.

What is pinned: the create's name-collision refusal that runs BEFORE anything is created (through
``resolve_machine`` - the SAME filtered query an assignment uses, never an unfiltered catalog walk,
which reads capabilities on every bundled machine and busts the handler cap), the
description/vendor/model write-and-read-back, the Local-library store, the gate that re-resolves
the new machine through that same query - and on the delete side the confirm_name gate, the
local-only refusal, the asset identity gate, and the two read-backs that decide whether a
``deleteAsset`` true is a delete at all.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool

ccm = load_tool("cam_create_machine")
cdm = load_tool("cam_delete_machine")

_LOCAL = ccm.adsk.cam.LibraryLocations.LocalLibraryLocation
_F360 = ccm.adsk.cam.LibraryLocations.Fusion360LibraryLocation


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class _Mach:
    """A Machine: the three settable identity fields plus the read-only facts the payload
    publishes. ``sticky=False`` models a setter the platform accepts and silently drops."""

    def __init__(self, description="Generic 3-axis", vendor="Autodesk",
                 model="Generic 3-axis Mill", machine_id="4f29e005-4946", has_sim=False,
                 sticky=True):
        self._f = {"description": description, "vendor": vendor, "model": model}
        self._sticky = sticky
        self.id = machine_id
        self.hasPost = False
        self.hasSimulationModel = has_sim
        self.capabilities = SimpleNamespace(isMillingSupported=True, isTurningSupported=False,
                                            isCuttingSupported=False, isAdditiveSupported=False)

    def _set(self, key, value):
        if self._sticky:
            self._f[key] = value

    description = property(lambda s: s._f["description"], lambda s, v: s._set("description", v))
    vendor = property(lambda s: s._f["vendor"], lambda s, v: s._set("vendor", v))
    model = property(lambda s: s._f["model"], lambda s, v: s._set("model", v))


class _MachUrl:
    """An asset URL: its leafName is the asset's NAME and its string form its address. Two assets
    in different folders share a leaf name and differ only by that address."""

    def __init__(self, leaf, folder=""):
        self.leafName = leaf
        self._folder = folder

    def toString(self):
        return "machine://local/" + self._folder + self.leafName


class _MachLib:
    """MachineLibrary: a Local and a Fusion360 pool behind createQuery, plus the
    importMachine -> machineAtURL round trip. ``import_mode`` picks the store outcome:
    'ok' stores and lists it, 'none' returns no URL, 'orphan' returns a URL nothing loads from,
    'unlisted' stores it at its URL but never lists it in the query pool."""

    def __init__(self, local=(), f360=(), import_mode="ok", assets=(), delete_mode="ok",
                 local_root="LOCAL_ROOT", folder_depth=0, local_query_raises=False):
        self.local = list(local)
        self.f360 = list(f360)
        self._local_query_raises = local_query_raises
        self.stored = {}
        self.imported = []
        self.deleted = []
        self._mode = import_mode
        self._delete_mode = delete_mode
        self._root = local_root
        # A chain of nested folders, so a walk deeper than the shared library walk's own bound
        # reports truncated - the incomplete search space every asset conclusion rests on.
        self._folder_depth = folder_depth
        # The Local location's child ASSETS - what the library walk lists and what deleteAsset
        # addresses. Each (leafName, machine) pair also loads back through machineAtURL.
        self.asset_urls = []
        for entry in assets:
            leaf, mach = entry[0], entry[1]
            u = _MachUrl(leaf, entry[2] if len(entry) > 2 else "")
            self.asset_urls.append(u)
            self.stored[u.toString()] = mach

    def urlByLocation(self, loc):
        return self._root if loc == _LOCAL else None

    def childFolderURLs(self, url):
        if not self._folder_depth:
            return []
        here = 0 if url == self._root else int(str(url).replace("FOLDER", "") or 0)
        return ["FOLDER%d" % (here + 1)] if here < self._folder_depth else []

    def childAssetURLs(self, url):
        return list(self.asset_urls) if url == self._root else []

    def deleteAsset(self, url):
        """'ok' removes the asset AND the machine it holds; 'false'/'raise' are the platform saying
        no; 'keeps_asset' and 'keeps_machine' are the swallowed no-ops the read-backs exist for."""
        if self._delete_mode == "raise":
            raise RuntimeError("library is read-only")
        if self._delete_mode == "false":
            return False
        if self._delete_mode == "root_gone":
            # the library location stops resolving between the delete and its read-back
            self._root = None
        if self._delete_mode == "walk_deepens":
            # the folder tree grows past the shared walk's own depth bound between the delete and
            # its read-back, so the POST-delete walk is the one that cannot finish
            self._folder_depth = 8
        key = url.toString()
        machine = self.stored.get(key)
        if self._delete_mode != "keeps_asset":
            self.stored.pop(key, None)
            self.asset_urls = [a for a in self.asset_urls if a.toString() != key]
        if self._delete_mode != "keeps_machine":
            self.local = [m for m in self.local if m is not machine]
        self.deleted.append(key)
        return True

    def createQuery(self, loc, vendor, model):
        if loc == _LOCAL and self._local_query_raises:
            raise RuntimeError("local machine library is unavailable")
        pool = self.local if loc == _LOCAL else (self.f360 if loc == _F360 else [])
        hits = [m for m in pool
                if (not vendor or (m.vendor or "").lower() == vendor.lower())
                and (not model or (m.model or "").lower().startswith(model.lower()))]
        return SimpleNamespace(execute=lambda: hits)

    def importMachine(self, machine, url, name):
        self.imported.append((machine, url, name))
        if self._mode == "none":
            return None
        # The library keeps its OWN copy of the machine, with its own id: the object handed in is
        # not the object an assignment later resolves, so a payload read off it is a request echo.
        copy = _Mach(description=machine.description, vendor=machine.vendor, model=machine.model,
                     machine_id=str(machine.id) + "-stored", has_sim=machine.hasSimulationModel)
        # The stored asset's leafName carries the '.mch' EXTENSION while the machine's name does
        # not - the live shape, and the one a leafName-equals-name match cannot find.
        u = _MachUrl(name + ".mch")
        if self._mode != "orphan":
            self.stored[u.toString()] = copy
        if self._mode == "ok":
            self.local.append(copy)
            self.asset_urls.append(u)
        return u

    def machineAtURL(self, url):
        return self.stored.get(url.toString())


@pytest.fixture
def env(monkeypatch):
    """Install the machine library + the MachineTemplate members + Machine.createFromTemplate.
    Returns a factory; the namespace it hands back carries the library (its `imported` list is the
    mutation record) and the templates actually asked for."""
    def _make(local=(), f360=(), import_mode="ok", machine=None, assets=(), delete_mode="ok",
              local_root="LOCAL_ROOT", folder_depth=0, local_query_raises=False):
        lib = _MachLib(local, f360, import_mode, assets, delete_mode, local_root, folder_depth,
                       local_query_raises)
        holder = SimpleNamespace(libraryManager=SimpleNamespace(machineLibrary=lib))
        monkeypatch.setattr(ccm.adsk.cam.CAMManager, "get", lambda: holder, raising=False)
        monkeypatch.setattr(ccm.adsk.cam, "MachineTemplate",
                            SimpleNamespace(**{m: m for m in ccm._TEMPLATES.values()}),
                            raising=False)
        made = machine if machine is not None else _Mach()
        asked = []

        def _from_template(member):
            asked.append(member)
            return made

        monkeypatch.setattr(ccm.adsk.cam.Machine, "createFromTemplate", _from_template,
                            raising=False)
        return SimpleNamespace(lib=lib, machine=made, asked=asked)
    return _make


# ── guards: nothing is created or stored when the request is refused ─────────

class TestRefusals:
    def test_unknown_template_is_refused_and_nothing_is_created(self, env):
        e = env()
        res = ccm.handler(name="Sweep3Axis", template="generic_6_axis")
        assert res["isError"] is True
        assert "generic_3_axis" in res["message"] and "generic_lathe" in res["message"]
        assert e.asked == [] and e.lib.imported == []

    def test_missing_name_is_refused(self, env):
        e = env()
        res = ccm.handler(name="   ", template="generic_3_axis")
        assert res["isError"] is True
        assert "'name'" in res["message"] and "cam_edit_setup" in res["message"]
        assert e.asked == [] and e.lib.imported == []

    def test_a_local_name_collision_is_refused_naming_the_existing_machine(self, env):
        # Compared case-insensitively and EXACTLY: 'sweep3axis' collides with 'Sweep3Axis' (a
        # machine this tool itself created carries its name on the model field too, which is what
        # makes it reachable by the assignment query the clash check runs).
        e = env(local=[_Mach(description="Sweep3Axis", vendor="SweepCo", model="Sweep3Axis")])
        res = ccm.handler(name="sweep3axis")
        assert res["isError"] is True
        assert "Sweep3Axis" in res["message"] and "local" in res["message"]
        assert "SweepCo" in res["message"]
        assert e.asked == [] and e.lib.imported == []

    def test_a_name_matching_an_existing_MODEL_is_refused(self, env):
        # The name lands on Machine.model, so taking 'VF-2' retargets every
        # cam_edit_setup(machine='VF-2') that reaches the Haas by its model rung.
        e = env(f360=[_Mach(description="Haas VF-2", vendor="Haas", model="VF-2")])
        res = ccm.handler(name="vf-2")
        assert res["isError"] is True
        assert "matches that machine's model" in res["message"] and "Haas VF-2" in res["message"]
        assert e.asked == [] and e.lib.imported == []

    def test_a_name_matching_an_existing_VENDOR_MODEL_is_refused(self, env):
        # 'Haas VF-2' as a vendor|model pair is the second rung the exact match selects on.
        e = env(f360=[_Mach(description="The Big One", vendor="Haas", model="VF-2")])
        res = ccm.handler(name="Haas VF-2")
        assert res["isError"] is True
        assert "matches that machine's vendor model" in res["message"]
        assert "The Big One" in res["message"]
        assert e.asked == [] and e.lib.imported == []

    def test_a_fusion360_name_collision_is_refused_too(self, env):
        # The catalog covers BOTH locations - a bundled machine's name is just as unusable.
        e = env(f360=[_Mach(description="Haas VF-2", vendor="Haas", model="VF-2")])
        res = ccm.handler(name="Haas VF-2")
        assert res["isError"] is True
        assert "fusion360" in res["message"]
        assert e.asked == [] and e.lib.imported == []

    def test_a_longer_existing_name_is_not_a_collision(self, env):
        # 'Sweep3Axis Mk2' merely CONTAINS the requested name; a substring check would refuse it.
        e = env(local=[_Mach(description="Sweep3Axis Mk2", vendor="SweepCo", model="S3")])
        out = _payload(ccm.handler(name="Sweep3Axis"))
        assert out["created"] is True and out["name"] == "Sweep3Axis"
        assert len(e.lib.imported) == 1

    def test_an_ambiguous_name_cannot_be_proven_free_and_is_refused(self, env, monkeypatch):
        # resolve_machine refusing on AMBIGUITY (several machines answer to the name) is a clash,
        # not freeness - the create must refuse rather than mint a third claimant.
        e = env()
        monkeypatch.setattr(ccm, "resolve_machine",
                            lambda name: (None, None, "'Sweep' matches several machines - "
                                          "Sweep3Axis, Sweep4Axis. Pick one."))
        res = ccm.handler(name="Sweep")
        assert res["isError"] is True and "cannot be proven free" in res["message"]
        assert "matches several" in res["message"]
        assert e.asked == [] and e.lib.imported == []


# ── the field writes: read back before anything is stored ───────────────────

class TestFieldWrites:
    def test_a_dropped_name_write_errors_before_the_library_is_touched(self, env):
        e = env(machine=_Mach(sticky=False))
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "Machine.description" in res["message"] and "did not land" in res["message"]
        assert e.lib.imported == []          # the refusal happens BEFORE the store

    def test_a_field_write_that_RAISES_is_reported_before_the_library_is_touched(self, env):
        # a setter the platform REFUSES is a different failure from one it silently drops, and
        # both must stop the create before anything reaches the library.
        class _RaisingField(_Mach):
            @property
            def description(self):
                return self._f["description"]

            @description.setter
            def description(self, value):
                raise RuntimeError("description is read-only")

        e = env(machine=_RaisingField())
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "Could not set Machine.description" in res["message"]
        assert "read-only" in res["message"]
        assert e.lib.imported == []

    def test_the_name_lands_on_description_and_model(self, env):
        # resolve_machine's widen path exists because a label does not match the model field its
        # query is keyed on, so a machine left carrying the template's shared model is not
        # reachable by its own name.
        e = env()
        out = _payload(ccm.handler(name="Sweep3Axis"))
        assert e.machine.description == "Sweep3Axis" and e.machine.model == "Sweep3Axis"
        assert out["model"] == "Sweep3Axis"
        assert e.machine.vendor == "Autodesk"      # untouched when no vendor is given

    def test_an_explicit_vendor_is_recorded(self, env):
        e = env()
        out = _payload(ccm.handler(name="Sweep3Axis", vendor="SweepCo"))
        assert e.machine.vendor == "SweepCo" and out["vendor"] == "SweepCo"
        assert e.machine.model == "Sweep3Axis"     # the name still owns the model field


# ── the store + the re-resolve gate ─────────────────────────────────────────

class TestCreatePlatformFailures:
    """Every platform step that can decline is reported as the failure it is - never swallowed into
    a create that reports success over a library nothing was written to."""

    def test_an_unreachable_machine_library_is_an_error(self, env, monkeypatch):
        env()
        monkeypatch.setattr(ccm, "machine_library",
                            lambda: (None, "Could not access the machine library (x)."))
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True and "Could not access the machine library" in res["message"]

    def test_a_template_member_this_build_lacks_is_refused_by_name(self, env, monkeypatch):
        e = env()
        # every OTHER member is present, so this is the member read failing, not the Choice
        monkeypatch.setattr(ccm.adsk.cam, "MachineTemplate",
                            SimpleNamespace(**{m: m for m in ccm._TEMPLATES.values()
                                               if m != "Generic4Axis"}), raising=False)
        res = ccm.handler(name="Sweep4Axis", template="generic_4_axis")
        assert res["isError"] is True
        assert "no 'Generic4Axis' member" in res["message"]
        assert e.asked == [] and e.lib.imported == []

    def test_a_raising_createFromTemplate_is_reported(self, env, monkeypatch):
        e = env()
        monkeypatch.setattr(ccm.adsk.cam.Machine, "createFromTemplate",
                            lambda member: (_ for _ in ()).throw(RuntimeError("no such kinematics")),
                            raising=False)
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "createFromTemplate('generic_3_axis') failed" in res["message"]
        assert "no such kinematics" in res["message"]
        assert e.lib.imported == []

    def test_a_createFromTemplate_that_returns_nothing_is_an_error(self, env, monkeypatch):
        e = env()
        monkeypatch.setattr(ccm.adsk.cam.Machine, "createFromTemplate", lambda member: None,
                            raising=False)
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "returned nothing - no machine was created" in res["message"]
        assert e.lib.imported == []

    def test_an_unresolvable_local_root_refuses_before_the_store(self, env):
        e = env(local_root=None)
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "Local machine library location to save into" in res["message"]
        assert e.lib.imported == []

    def test_a_raising_importMachine_is_reported(self, env, monkeypatch):
        e = env()
        monkeypatch.setattr(e.lib, "importMachine",
                            lambda m, u, n: (_ for _ in ()).throw(RuntimeError("library is full")))
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "Storing machine 'Sweep3Axis' in the Local machine library failed" in res["message"]
        assert "library is full" in res["message"]


class TestStoreAndGate:
    def test_no_url_from_the_store_is_an_error(self, env):
        env(import_mode="none")
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True and "returned no URL" in res["message"]

    def test_a_url_nothing_loads_from_is_an_error(self, env):
        env(import_mode="orphan")
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True and "no machine loads back from it" in res["message"]

    def test_a_machine_that_does_not_resolve_back_is_an_error_that_discloses_the_residue(self, env):
        # Stored at its URL but never listed by the query - the swallowed no-op this gate exists for.
        env(import_mode="unlisted")
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "does not resolve back" in res["message"]
        assert "machine://local/Sweep3Axis.mch" in res["message"]
        assert "still there" in res["message"]

    def test_a_name_resolving_to_a_different_machine_is_an_error(self, env, monkeypatch):
        # The PRE-check and the post-store gate share the resolver seam: the first answer must be
        # "free" so the create proceeds, the second is the wrong machine the gate must catch.
        env()
        answers = [(None, None, "No machine matches 'Sweep3Axis'."),
                   (_Mach(description="Someone Else"), "Someone Else", None)]
        monkeypatch.setattr(ccm, "resolve_machine", lambda name: answers.pop(0))
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "resolves to 'Someone Else'" in res["message"]

    def test_the_created_machine_is_reported_from_the_stored_copy_not_the_request(self, env):
        # Every published fact comes off the copy the library kept (its id carries '-stored'), so a
        # payload assembled from the in-memory object the request built cannot pass.
        e = env(local=[_Mach(description="Haas VF-2", vendor="Haas", model="VF-2")])
        out = _payload(ccm.handler(name="Sweep3Axis", vendor="SweepCo"))
        assert out["created"] is True
        assert out["name"] == "Sweep3Axis"                  # the label the resolver hands back
        assert out["machine_id"] == "4f29e005-4946-stored"
        assert out["vendor"] == "SweepCo" and out["model"] == "Sweep3Axis"
        assert e.machine.id == "4f29e005-4946"              # the request's own object, not published
        assert out["template"] == "generic_3_axis" and out["location"] == "local"
        assert (out["url"] == "machine://local/Sweep3Axis.mch"
                and out["asset_name"] == "Sweep3Axis.mch")   # the STORED name, extension and all
        assert out["kind"] == ["milling"]
        assert out["has_post"] is False and out["has_simulation_model"] is False
        assert e.asked == ["Generic3Axis"]                  # the wire value mapped to the member
        assert "cam_edit_setup(setup=..., machine='Sweep3Axis')" in out["note"]
        assert "persists in the local machine library" in out["note"]
        assert "until cam_delete_machine(name='Sweep3Axis') removes it" in out["note"]

    def test_each_wire_template_maps_to_its_own_member(self, env):
        for wire, member in ccm._TEMPLATES.items():
            e = env()
            out = _payload(ccm.handler(name="M " + wire, template=wire))
            assert e.asked == [member] and out["template"] == wire

    def test_a_simulation_ready_machine_note_names_the_strip_flag(self, env):
        env(machine=_Mach(has_sim=True))
        out = _payload(ccm.handler(name="Sweep3Axis"))
        assert out["has_simulation_model"] is True
        assert "machine_strip_simulation=true" in out["note"]

    def test_the_note_names_the_tool_that_removes_it_again(self, env):
        env()
        out = _payload(ccm.handler(name="Sweep3Axis"))
        assert "cam_delete_machine(name='Sweep3Axis')" in out["note"]


# ── the delete: the other half of the lifecycle ─────────────────────────────

def _local(name="SweepMach", vendor="SweepCo"):
    """A machine as this tool stores one: the name on BOTH description and model, which is what
    makes it reachable by the assignment query."""
    return _Mach(description=name, vendor=vendor, model=name, machine_id="id-" + name)


def _label_not_model(description="Shop Mill #3", vendor="SweepCo", model="VF-2"):
    """A machine authored OUTSIDE cam_create_machine: its description is not its model. The library
    query is keyed on (vendor, model) and does not reach it by that description - so the name it
    resolves BY ('VF-2'), the label it resolves TO ('Shop Mill #3') and its asset's leaf name are
    three different strings, and a read-back keyed on the wrong one of them answers nothing."""
    return _Mach(description=description, vendor=vendor, model=model, machine_id="id-" + model)


class _NoIdMach(_Mach):
    """A machine whose id cannot be read - the comparison that tells two machines apart is itself
    the read that failed."""

    @property
    def id(self):
        raise RuntimeError("id unreadable")

    @id.setter
    def id(self, value):
        pass


# The names one asset answers to (leafName as stored, and its stem) live in _cam_common, where
# cam_delete_machine and cam_delete_template both resolve their target through them; the contract
# cases are pinned in test__cam_common.py. What is pinned HERE is what the handler does with them.


class TestDeleteMachineGuards:
    """Nothing is deleted unless the machine resolved, sits in the LOCAL library, and the caller
    confirmed the name the resolver actually reached."""

    def test_missing_name_is_refused(self, env):
        e = env()
        res = cdm.handler(name="  ", confirm_name="x")
        assert res["isError"] is True and "'name'" in res["message"]
        assert e.lib.deleted == []

    def test_an_unreachable_machine_library_is_an_error(self, env, monkeypatch):
        env()
        monkeypatch.setattr(cdm, "machine_library",
                            lambda: (None, "Could not access the machine library (x)."))
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True and "Could not access the machine library" in res["message"]

    def test_missing_confirm_name_is_refused(self, env):
        e = env(local=[_local()], assets=[("SweepMach.mch", _local())])
        res = cdm.handler(name="SweepMach")
        assert res["isError"] is True and "'confirm_name'" in res["message"]
        assert e.lib.deleted == []

    def test_a_confirm_name_mismatch_is_refused_naming_both(self, env):
        m = _local()
        e = env(local=[m], assets=[("SweepMach.mch", m)])
        res = cdm.handler(name="SweepMach", confirm_name="sweepmach")
        assert res["isError"] is True
        assert "Name mismatch" in res["message"]
        assert "confirm_name was 'sweepmach'" in res["message"]
        assert "confirm_name='SweepMach'" in res["message"]
        assert e.lib.deleted == []

    def test_an_unknown_machine_is_refused(self, env):
        e = env()
        res = cdm.handler(name="Ghost", confirm_name="Ghost")
        assert res["isError"] is True
        assert "No machine matches" in res["message"] and "Nothing was deleted" in res["message"]
        assert e.lib.deleted == []

    def test_an_ambiguous_name_is_refused_with_the_resolver_candidates(self, env, monkeypatch):
        # Two machines answer to the name: the shared resolver refuses, and its refusal is handed
        # back verbatim rather than resolved to whichever machine the walk met first.
        e = env(local=[_local()], assets=[("SweepMach.mch", _local())])
        monkeypatch.setattr(cdm, "resolve_machine",
                            lambda name: (None, None, "Ambiguous machine 'SweepMach' - 2 matches: "
                                          "SweepMach A, SweepMach B. Pass one of these exact names."))
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "Ambiguous machine" in res["message"] and "SweepMach B" in res["message"]
        assert e.lib.deleted == []

    def test_a_location_that_could_not_be_READ_is_refused_too(self, env):
        # machine_location's THIRD answer: the Local query itself raises, so 'local or fusion360' is
        # all that can be said - nothing read establishes this machine as local. An irreversible
        # delete must fail CLOSED there, exactly as it does on a positively fusion360 machine.
        # (The machine sits in the bundled pool so the resolver, which skips a raising location,
        # still reaches it - the gate is what has to refuse, not the resolve.)
        m = _Mach(description="SweepMach", vendor="SweepCo", model="SweepMach")
        e = env(f360=[m], assets=[("SweepMach.mch", m)], local_query_raises=True)
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "local or fusion360" in res["message"]
        assert "LOCAL library only" in res["message"]
        assert e.lib.deleted == []

    def test_a_bundled_fusion360_machine_is_refused(self, env):
        # The Local library is the only one this tool deletes from - a machine reached from the
        # bundled location is refused by the location read, before any asset is addressed.
        e = env(f360=[_Mach(description="Haas VF-2", vendor="Haas", model="VF-2")])
        res = cdm.handler(name="Haas VF-2", confirm_name="Haas VF-2")
        assert res["isError"] is True
        assert "fusion360 machine library" in res["message"]
        assert "LOCAL library only" in res["message"]
        assert e.lib.deleted == []

    def test_an_unresolvable_local_root_is_refused(self, env):
        m = _local()
        e = env(local=[m], local_root=None)
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "Local machine library location" in res["message"]
        assert e.lib.deleted == []

    def test_no_asset_carrying_the_name_lists_what_the_library_holds(self, env):
        # The machine answers the query but no ASSET carries its name - the delete has nothing to
        # address, and the refusal names the assets that are actually there.
        m = _local()
        e = env(local=[m], assets=[("OtherMachine.mch", _Mach()), ("ThirdMachine.mch", _Mach())])
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "No asset in the Local machine library is named 'SweepMach'" in res["message"]
        # the listing renders each asset AS STORED, extension included - the string a caller has to
        # match against what Fusion's own library shows them
        assert "OtherMachine.mch" in res["message"] and "ThirdMachine.mch" in res["message"]
        assert e.lib.deleted == []

    def test_the_asset_is_matched_by_its_leaf_name_STEM(self, env):
        # A stored asset's leafName carries the file EXTENSION while the machine's name does not,
        # so the match runs on the stem - and stays EXACT: the sibling whose stem merely STARTS with
        # the name is a different asset and must survive untouched.
        m = _local()
        e = env(local=[m], assets=[("SweepMach.mch", m),
                                   ("SweepMach extra.mch", _Mach(description="SweepMach extra"))])
        out = _payload(cdm.handler(name="SweepMach", confirm_name="SweepMach"))
        assert out["asset_name"] == "SweepMach.mch"
        assert e.lib.deleted == ["machine://local/SweepMach.mch"]
        assert [a.leafName for a in e.lib.asset_urls] == ["SweepMach extra.mch"]
        assert out["local_assets_remaining"] == 1

    def test_an_asset_stored_without_an_extension_still_resolves(self, env):
        # The other half of the key set: a leaf with no dot at all has no stem, so it is the leaf
        # itself that has to answer. Matching on the stem ALONE leaves such an asset unreachable.
        m = _local()
        e = env(local=[m], assets=[("SweepMach", m)])
        out = _payload(cdm.handler(name="SweepMach", confirm_name="SweepMach"))
        assert out["deleted"] is True and out["asset_name"] == "SweepMach"
        assert e.lib.deleted == ["machine://local/SweepMach"]

    def test_a_dotted_machine_name_resolves_its_own_asset(self, env):
        # The split is on the LAST dot, and a machine name may hold one: 'Mill v1.2' stored as
        # 'Mill v1.2.mch'. A first-dot split reads the stem as 'Mill v1' and reproduces exactly the
        # zero-hit refusal this matcher exists to prevent - on a name no run stamp would ever mint.
        m = _local("Mill v1.2")
        e = env(local=[m], assets=[("Mill v1.2.mch", m)])
        out = _payload(cdm.handler(name="Mill v1.2", confirm_name="Mill v1.2"))
        assert out["deleted"] is True and out["asset_name"] == "Mill v1.2.mch"
        assert e.lib.deleted == ["machine://local/Mill v1.2.mch"]

    def test_an_asset_whose_name_merely_CONTAINS_the_machine_name_is_not_it(self, env):
        # 'SweepMach Mk2' is another machine's asset; a substring match here deletes the wrong file.
        m = _local()
        e = env(local=[m], assets=[("SweepMach Mk2.mch", _Mach(description="SweepMach Mk2"))])
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "No asset in the Local machine library is named 'SweepMach'" in res["message"]
        assert e.lib.deleted == []

    def test_two_assets_sharing_the_name_are_refused_not_guessed(self, env):
        m = _local()
        # one in the library root, one in a folder: same leaf NAME, different addresses.
        e = env(local=[m], assets=[("SweepMach.mch", m), ("SweepMach.mch", m, "Mills/")])
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "names 2 assets" in res["message"] and "refusing to guess" in res["message"]
        assert "machine://local/Mills/SweepMach.mch" in res["message"]
        assert e.lib.deleted == []

    def test_an_asset_holding_a_different_machine_is_refused(self, env):
        # The leaf NAME matched but the asset loads another machine - deleting it would remove a
        # machine the caller never confirmed.
        m = _local()
        e = env(local=[m], assets=[("SweepMach.mch", _Mach(description="Someone Else"))])
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "holds the machine 'Someone Else'" in res["message"]
        assert e.lib.deleted == []

    def test_an_incomplete_library_walk_refuses_the_delete(self, env):
        # The walk that would have shown a same-named duplicate is the read that did not finish, so
        # the one-asset conclusion has no support and an irreversible delete fails closed.
        m = _local()
        e = env(local=[m], assets=[("SweepMach.mch", m)], folder_depth=8)
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "hit its own bound" in res["message"] and "only ONE asset" in res["message"]
        assert e.lib.deleted == []

    def test_a_zero_hit_search_discloses_an_incomplete_walk(self, env):
        m = _local()
        e = env(local=[m], assets=[("OtherMachine.mch", _Mach())], folder_depth=8)
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "No asset in the Local machine library is named" in res["message"]
        assert "incomplete" in res["message"]
        assert e.lib.deleted == []

    def test_an_asset_that_loads_nothing_is_refused(self, env):
        m = _local()
        e = env(local=[m], assets=[("SweepMach.mch", None)])
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "does not load a machine" in res["message"]
        assert e.lib.deleted == []


class TestDeleteMachineEffect:
    """deleteAsset's own answer is never the proof: the library is re-walked and the name
    re-resolved, and either read still finding the machine is an error."""

    def test_deletes_the_local_machine_and_reads_the_library_back(self, env):
        m = _local()
        e = env(local=[m], assets=[("SweepMach.mch", m)])
        out = _payload(cdm.handler(name="SweepMach", confirm_name="SweepMach"))
        assert out["deleted"] is True and out["machine"] == "SweepMach"
        assert out["location"] == "local" and out["asset_name"] == "SweepMach.mch"
        assert out["url"] == "machine://local/SweepMach.mch"
        assert out["resolves_after_delete"] is False
        assert out["local_assets_remaining"] == 0
        assert e.lib.deleted == ["machine://local/SweepMach.mch"]
        assert e.lib.local == []
        # the asset walk is what the claim rests on, and the note says so rather than counting a
        # silent re-resolve as a second proof.
        assert "gone from a re-walk of the library's own assets" in out["note"]
        assert "proves nothing" in out["note"]
        # ...and it claims nothing about a SETUP, which this handler never reads.
        assert "no setup was read here" in out["note"]
        assert "setup" not in out["note"].split("no setup was read here")[0]

    def test_a_machine_created_here_can_be_deleted_by_the_name_it_was_created_under(self, env):
        # The round trip the sweep's teardown runs: create, then delete by the same name.
        e = env()
        made = _payload(ccm.handler(name="SweepMach3Axis", vendor="SweepCo"))
        out = _payload(cdm.handler(name=made["name"], confirm_name=made["name"]))
        assert out["deleted"] is True and out["resolves_after_delete"] is False
        assert e.lib.asset_urls == [] and e.lib.local == []

    def test_a_declined_delete_is_an_error_not_a_false_ok(self, env):
        m = _local()
        e = env(local=[m], assets=[("SweepMach.mch", m)], delete_mode="false")
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "deleteAsset returned false" in res["message"]
        assert e.lib.local == [m]

    def test_a_raising_delete_is_reported(self, env):
        m = _local()
        env(local=[m], assets=[("SweepMach.mch", m)], delete_mode="raise")
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "read-only" in res["message"] and "failed" in res["message"]

    def test_an_asset_still_listed_after_a_true_delete_is_an_error(self, env):
        m = _local()
        env(local=[m], assets=[("SweepMach.mch", m)], delete_mode="keeps_asset")
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        # named by the STORED leaf name - what the reader has to find in the library
        assert "still lists 'SweepMach.mch'" in res["message"]

    def test_a_name_still_resolving_to_the_same_machine_is_an_error(self, env):
        # The asset is gone but the assignment query still reaches the machine - a delete that
        # leaves the name working is not a delete.
        m = _local()
        env(local=[m], assets=[("SweepMach.mch", m)], delete_mode="keeps_machine")
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "still resolves to the same machine" in res["message"]

    def test_a_label_that_is_not_the_model_still_reads_its_own_asset_back(self, env):
        # THE FALSE OK: name 'VF-2', label 'Shop Mill #3', asset leaf 'VF-2'. A read-back searching
        # only the LABEL finds nothing and reports a delete that did not happen - the asset is still
        # listed, under the name the machine was reached by.
        m = _label_not_model()
        e = env(local=[m], assets=[("VF-2.mch", m)], delete_mode="keeps_asset")
        res = cdm.handler(name="VF-2", confirm_name="Shop Mill #3")
        assert res["isError"] is True
        assert "still lists 'VF-2.mch'" in res["message"]
        assert e.lib.deleted == ["machine://local/VF-2.mch"]

    def test_a_label_that_is_not_the_model_is_re_resolved_by_the_NAME(self, env):
        # The same rig with the machine left in the query pool: re-resolving by the LABEL answers
        # nothing for this machine whatever its state, so only the NAME can bite here.
        m = _label_not_model()
        env(local=[m], assets=[("VF-2.mch", m)], delete_mode="keeps_machine")
        res = cdm.handler(name="VF-2", confirm_name="Shop Mill #3")
        assert res["isError"] is True
        assert "still resolves to the same machine" in res["message"]

    def test_a_read_back_walk_that_cannot_answer_leaves_the_delete_unconfirmed(self, env):
        # The library location stops resolving between the delete and the read-back: an empty match
        # against a walk that answered nothing is not evidence, so the call refuses to claim one.
        m = _local()
        env(local=[m], assets=[("SweepMach.mch", m)], delete_mode="root_gone")
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "UNCONFIRMED" in res["message"] and "no longer resolves" in res["message"]

    def test_a_read_back_walk_that_did_not_FINISH_leaves_the_delete_unconfirmed(self, env):
        # the twin of the root-gone case, on the walk's other failure: the folder tree grows past
        # the shared walk's depth bound between the delete and its read-back. An empty match against
        # a walk that stopped early is not evidence the asset is gone - a same-named asset past the
        # bound would not have been seen - so the delete is reported UNCONFIRMED, not as done.
        m = _local()
        e = env(local=[m], assets=[("SweepMach.mch", m)], delete_mode="walk_deepens")
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "asset walk hit its own bound before finishing" in res["message"]
        assert "UNCONFIRMED" in res["message"]
        assert e.lib.deleted == ["machine://local/SweepMach.mch"]   # the delete DID fire

    def test_an_unreadable_id_on_both_sides_is_unconfirmed_not_a_delete(self, env):
        m = _NoIdMach(description="SweepMach", vendor="SweepCo", model="SweepMach")
        env(local=[m], assets=[("SweepMach.mch", m)], delete_mode="keeps_machine")
        res = cdm.handler(name="SweepMach", confirm_name="SweepMach")
        assert res["isError"] is True
        assert "id cannot be read" in res["message"] and "UNCONFIRMED" in res["message"]

    def test_a_name_that_now_reaches_a_DIFFERENT_machine_is_disclosed(self, env):
        # A bundled machine wearing the same name takes the name over once the local one goes; the
        # delete stands, and the note says which machine an assignment reaches from here on.
        m = _local()
        other = _Mach(description="SweepMach", vendor="Haas", model="SweepMach",
                      machine_id="id-bundled")
        e = env(local=[m], f360=[other], assets=[("SweepMach.mch", m)])
        out = _payload(cdm.handler(name="SweepMach", confirm_name="SweepMach"))
        assert out["deleted"] is True and out["resolves_after_delete"] is False
        assert "resolves to a DIFFERENT machine" in out["note"]
        assert e.lib.deleted == ["machine://local/SweepMach.mch"]
