"""Unit tests for ``cam_create_machine`` - a machine built from a template into the Local library.

The adsk.cam API is mocked. What is pinned here is the tool's own contract: the name-collision
refusal that runs BEFORE anything is created, the description/vendor/model write-and-read-back, the
Local-library store, and the gate that re-resolves the new machine through the SAME query
``cam_edit_setup`` assigns from - a create that cannot be found again is an error, not a false ok.

The machine library fake serves BOTH this tool and the real ``cam_edit_setup`` helpers it calls
(``read_machines`` for the catalog, ``_resolve_machine`` for the gate), so the integration seam is
exercised rather than stubbed. Its ``createQuery`` matches vendor exactly and model by prefix - the
shape ``cam_edit_setup``'s own resolver is written against, whose widen block (:193-208) re-splits a
label into (vendor, model) because a label does not match the model field. Whether the live query
ALSO indexes the description is unmeasured, which is why the tool writes the name to both fields and
gates on a re-resolve rather than assuming. ``importMachine`` hands the library its OWN copy with a
distinct id, so a payload assembled from the pre-store object reports a machine the library has not
got.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool

ccm = load_tool("cam_create_machine")

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
    def __init__(self, leaf):
        self.leafName = leaf

    def toString(self):
        return "machine://local/" + self.leafName


class _MachLib:
    """MachineLibrary: a Local and a Fusion360 pool behind createQuery, plus the
    importMachine -> machineAtURL round trip. ``import_mode`` picks the store outcome:
    'ok' stores and lists it, 'none' returns no URL, 'orphan' returns a URL nothing loads from,
    'unlisted' stores it at its URL but never lists it in the query pool."""

    def __init__(self, local=(), f360=(), import_mode="ok"):
        self.local = list(local)
        self.f360 = list(f360)
        self.stored = {}
        self.imported = []
        self._mode = import_mode

    def urlByLocation(self, loc):
        return "LOCAL_ROOT" if loc == _LOCAL else None

    def createQuery(self, loc, vendor, model):
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
        u = _MachUrl(name)
        if self._mode != "orphan":
            self.stored[u.toString()] = copy
        if self._mode == "ok":
            self.local.append(copy)
        return u

    def machineAtURL(self, url):
        return self.stored.get(url.toString())


@pytest.fixture
def env(monkeypatch):
    """Install the machine library + the MachineTemplate members + Machine.createFromTemplate.
    Returns a factory; the namespace it hands back carries the library (its `imported` list is the
    mutation record) and the templates actually asked for."""
    def _make(local=(), f360=(), import_mode="ok", machine=None):
        lib = _MachLib(local, f360, import_mode)
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
        # Compared case-insensitively and EXACTLY: 'sweep3axis' collides with 'Sweep3Axis'.
        e = env(local=[_Mach(description="Sweep3Axis", vendor="SweepCo", model="S3")])
        res = ccm.handler(name="sweep3axis")
        assert res["isError"] is True
        assert "Sweep3Axis" in res["message"] and "local" in res["message"]
        assert "SweepCo" in res["message"] and "S3" in res["message"]
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
        # 'Haas VF-2' as a vendor|model pair is the second rung _exact_machine selects on.
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

    def test_a_capped_catalog_read_refuses_rather_than_risking_a_duplicate(self, env, monkeypatch):
        # The collision check's evidence is the catalog read; a truncated one proves nothing.
        e = env()
        monkeypatch.setattr(ccm, "read_machines",
                            lambda *a, **k: ccm.ok({"machines": [], "count": 0, "truncated": True}))
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True and "cannot be proven free" in res["message"]
        assert e.asked == [] and e.lib.imported == []


# ── the field writes: read back before anything is stored ───────────────────

class TestFieldWrites:
    def test_a_dropped_name_write_errors_before_the_library_is_touched(self, env):
        e = env(machine=_Mach(sticky=False))
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "Machine.description" in res["message"] and "did not land" in res["message"]
        assert e.lib.imported == []          # the refusal happens BEFORE the store

    def test_the_name_lands_on_description_and_model(self, env):
        # cam_edit_setup's widen block (:193-208) exists because a label does not match the model
        # field its query is keyed on, so a machine left carrying the template's shared
        # 'Generic 3-axis Mill' model is not reachable by its own name.
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
        assert "machine://local/Sweep3Axis" in res["message"]
        assert "still there" in res["message"]

    def test_a_name_resolving_to_a_different_machine_is_an_error(self, env, monkeypatch):
        env()
        monkeypatch.setattr(ccm, "_resolve_machine",
                            lambda name: (_Mach(description="Someone Else"), "Someone Else", None))
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
        assert out["url"] == "machine://local/Sweep3Axis" and out["asset_name"] == "Sweep3Axis"
        assert out["kind"] == ["milling"]
        assert out["has_post"] is False and out["has_simulation_model"] is False
        assert e.asked == ["Generic3Axis"]                  # the wire value mapped to the member
        assert "cam_edit_setup(setup=..., machine='Sweep3Axis')" in out["note"]
        assert "persists in the local machine library" in out["note"]
        assert "no tool that removes a machine" in out["note"]

    def test_a_machine_the_catalog_does_not_list_afterwards_is_an_error(self, env, monkeypatch):
        # The catalog re-read is the payload's evidence for the cam_get listing; if the row is not
        # there, the create did not land where an assignment reads.
        env()
        real = ccm._catalog_clash
        seen = []

        def _clash(name):
            seen.append(name)
            return real(name) if len(seen) == 1 else (None, None, None)

        monkeypatch.setattr(ccm, "_catalog_clash", _clash)
        res = ccm.handler(name="Sweep3Axis")
        assert res["isError"] is True
        assert "catalog does not list that name" in res["message"]
        assert "still there" in res["message"]

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
