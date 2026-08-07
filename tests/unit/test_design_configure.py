"""Unit tests for ``design_configure`` — the configured-design build+switch tool.

The Fusion configurations API is mocked; what we pin is the tool's OWN logic: the action dispatch and
guards (unknown action, no design, not-yet-configured for column actions), creating a configured
design, adding configuration rows, and the four column kinds (parameter / suppress / visibility /
appearance-theme) including:
  - addressing cells by ROW NAME (getCellByRowName) — the robust path, live-verified,
  - parameter cells take an EXPRESSION string,
  - suppress cells take isSuppressed (bool), visibility cells take isVisible (bool),
  - the appearance-theme ORDERING: the body column must be added before extra theme rows, and each
    config row is linked to a theme row via the top table's parentTableColumn (ConfigurationThemeColumn).

Fakes are NAMED to match the real adsk classes where the handler reads type(x).__name__.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool

dc = load_tool("design_configure")


# ── fakes mirroring the configurations object model ─────────────────────────

class _Param:
    def __init__(self, name, expr="80 mm"):
        self.name = name
        self.expression = expr


class _Params:
    def __init__(self, params):
        self._p = list(params)
    def itemByName(self, name):
        return next((p for p in self._p if p.name == name), None)


class ConfigurationParameterCell:
    def __init__(self):
        self.expression = None


class ConfigurationSuppressCell:
    def __init__(self):
        self.isSuppressed = False


class ConfigurationVisibilityCell:
    def __init__(self):
        self.isVisible = True


class ConfigurationAppearanceCell:
    def __init__(self):
        self.appearance = None


class _Col:
    """A configuration column whose cells are addressed by row name."""
    def __init__(self, cls, kind="param"):
        self.id = "col-" + kind
        self._cls = cls
        self._cells = {}
        self.cell_factory = cls
    def _cell(self, rowname):
        if rowname not in self._cells:
            self._cells[rowname] = self.cell_factory()
        return self._cells[rowname]
    def getCellByRowName(self, name):
        return self._cell(name)
    def getCell(self, idx):
        # appearance theme path uses index; map to a synthetic name
        return self._cell("__idx_%d" % idx)
    def classType(self):
        return self._cls.__name__


class _Row:
    def __init__(self, name, idx, owner=None):
        self.name = name
        self.id = "row-" + name
        self.index = idx
        self.activated = False
        self._owner = owner
    def activate(self):
        self.activated = True
        if self._owner is not None:
            self._owner._active = self      # a real activate() moves the table's active row
        return True


class _Rows:
    def __init__(self, owner=None, on_add=None):
        self._r = []
        self._owner = owner
        self._on_add = on_add
    @property
    def count(self):
        return len(self._r)
    def item(self, i):
        return self._r[i]
    def __iter__(self):
        return iter(self._r)        # real ConfigurationRows is iterable (read tool's _find_row needs it)
    def add(self, name):
        prev = self._r[-1] if self._r else None
        r = _Row(name, len(self._r), self._owner)
        self._r.append(r)
        if self._owner is not None:
            self._owner._active = r         # adding a configuration row activates it
        if self._on_add is not None:
            self._on_add(r, prev)           # a new row copies the cell values of the row above it
        return r


class _ThemeCell:
    """The config->theme link cell. Its owner's 'cell_mode', read at assignment time, models the
    assignment landing ('honest'), being silently dropped ('silent'), or reading back a DIFFERENT row
    than the one assigned ('lies')."""
    def __init__(self, owner=None):
        self._owner = owner
        self._row = None

    @property
    def referencedTableRow(self):
        return self._row

    @referencedTableRow.setter
    def referencedTableRow(self, value):
        mode = getattr(self._owner, "cell_mode", "honest")
        if mode == "honest":
            self._row = value
        elif mode == "lies":
            self._row = getattr(self._owner, "substitute", None)
        # 'silent': the link is dropped and the cell keeps reading no row


class _ThemeColumn:
    """Models the live trap: getCell(index) and getCellByRowName(name) address DIFFERENT cells.
    The tool must use getCellByRowName for the config->theme link; a positional getCell() here returns
    a throwaway cell that the assertions never inspect, so an index-based tool would silently mislink."""
    def __init__(self, rows):
        self._rows = rows
        self.by_name = {}
        self._scratch = {}
        self.cell_mode = "honest"
        self.substitute = None
    def getCell(self, i):
        # NOT the addressing the tool should use — hand back a scratch cell unrelated to by_name.
        self._scratch.setdefault(i, _ThemeCell(self))
        return self._scratch[i]
    def getCellByRowName(self, name):
        if name not in self.by_name:
            self.by_name[name] = _ThemeCell(self)
        return self.by_name[name]


class _AppearanceTable:
    def __init__(self):
        self.rows = _Rows()
        self._columns_added = []
        self._theme_col = _ThemeColumn(self.rows)
        self.parentTableColumn = self._theme_col
    @property
    def columns(self):
        return self
    def add(self, body):
        # adding the body column creates the first theme row (the live gotcha)
        if self.rows.count == 0:
            self.rows.add("Theme 1")
        col = _Col(ConfigurationAppearanceCell, kind="appearance")
        self._columns_added.append(col)
        return col


class ConfigurationMaterialCell:
    """A material cell. Its table's 'cell_mode', read at assignment time, models the three outcomes
    an assignment can have: it lands ('honest'), it silently does not land ('silent' - the cell still
    reads no material), or the cell reports a DIFFERENT material than the one assigned ('lies')."""
    def __init__(self, owner=None):
        self._owner = owner
        self._material = None

    @property
    def material(self):
        return self._material

    @material.setter
    def material(self, value):
        mode = getattr(self._owner, "cell_mode", "honest")
        if mode == "honest":
            self._material = value
        elif mode == "lies":
            self._material = getattr(self._owner, "substitute", None)
        # 'silent': the assignment is dropped and the cell keeps reading no material


class _MaterialColumn:
    """A material column: cells are addressed positionally (getCell(theme_row_index)) and the column
    exposes .title/.id/.entity - a real ConfigurationMaterialColumn has NO .name."""
    def __init__(self, entity, title, table=None):
        self.entity = entity
        self.id = "matcol-" + title
        self.title = title
        self._table = table
        self._cells = {}

    @property
    def cell_mode(self):
        """This column's assignment behaviour: a column named in the table's silent_columns drops
        what it is handed, so one column can refuse while the others assign honestly."""
        if self.title in getattr(self._table, "silent_columns", ()):
            return "silent"
        return getattr(self._table, "cell_mode", "honest")

    @property
    def substitute(self):
        return getattr(self._table, "substitute", None)

    def getCell(self, idx):
        if idx not in self._cells:
            self._cells[idx] = ConfigurationMaterialCell(self)
        return self._cells[idx]

    def material_at(self, idx):
        """The material name this column holds on a theme row, or None - what a caller sees for the
        configurations linked to that row."""
        m = self._cells.get(idx)
        return getattr(m.material, "name", None) if m is not None else None


class _MaterialColumns:
    """materialTable.columns: the first non-root add ALSO mints a root-component column ahead of it,
    so the count jumps 0 -> 2 on a single add. A tool asserting count == 1 would be wrong."""
    def __init__(self, table):
        self._table = table
        self._cols = []
        self.added = []                           # the non-root columns, in add order

    @property
    def count(self):
        return len(self._cols)

    def item(self, i):
        return self._cols[i]

    def add(self, entity):
        if self._table.add_returns_null:
            return None
        existing = next((c for c in self._cols if c.entity is entity), None)
        if existing is not None:
            return existing                       # an entity that already has a column keeps it
        if not self._cols:                        # the auto-created root-component column
            self._cols.append(_MaterialColumn("RootComponent", "(Unsaved)", self._table))
        col = _MaterialColumn(entity, getattr(entity, "name", "Body"), self._table)
        self._cols.append(col)
        self.added.append(col)
        if self._table.rows.count == 0:
            # the column add mints the first theme row, and EVERY configuration starts out
            # referencing it (the platform seeds the links, so it bypasses the cell's mode)
            row = self._table.rows.add("Theme 1")
            for name in self._table.config_names():
                self._table.parentTableColumn.getCellByRowName(name)._row = row
        return col


class _MaterialRows(_Rows):
    """materialTable.rows: adding a name an existing row carries returns THAT row and adds nothing,
    and a genuinely new row starts as a COPY of the row above it - which is not the row the
    configuration being moved was referencing."""
    def __init__(self, table):
        super().__init__()
        self._table = table

    def add(self, name):
        taken = next((self.item(i) for i in range(self.count) if self.item(i).name == name), None)
        if taken is not None:
            return taken
        prev = self.count - 1
        row = super().add(name)
        if prev >= 0:
            for i in range(self._table.columns.count):
                col = self._table.columns.item(i)
                src = col.getCell(prev).material
                if src is not None:
                    col.getCell(self.count - 1)._material = src   # the platform copies, not the tool
        return row


class _MaterialTable:
    def __init__(self, top=None):
        self._top = top
        self.rows = _MaterialRows(self)
        self.columns = _MaterialColumns(self)
        self.parentTableColumn = _ThemeColumn(self.rows)
        self.add_returns_null = False
        self.cell_mode = "honest"
        self.substitute = None
        self.silent_columns = set()        # titles of columns that drop what they are assigned

    def config_names(self):
        top = self._top
        return [top.rows.item(i).name for i in range(top.rows.count)] if top is not None else []

    def row_index(self, name):
        return next(i for i in range(self.rows.count) if self.rows.item(i).name == name)


class ConfigurationInsertCell:
    def __init__(self):
        self.row = None        # set to a part ConfigurationRow


class _InsertCol(_Col):
    def __init__(self):
        super().__init__(ConfigurationInsertCell, "insert")
        self.cell_factory = ConfigurationInsertCell
        self.occurrence = None


class _Columns:
    def __init__(self):
        self.added = []
    def addParameterColumn(self, p):
        c = _Col(ConfigurationParameterCell, "param"); c.param = p; self.added.append(c); return c
    def addSuppressColumn(self, f):
        c = _Col(ConfigurationSuppressCell, "suppress"); c.feature = f; self.added.append(c); return c
    def addVisibilityColumn(self, e):
        c = _Col(ConfigurationVisibilityCell, "visibility"); c.entity = e; self.added.append(c); return c
    def addInsertColumn(self, occ):
        c = _InsertCol(); c.occurrence = occ; self.added.append(c); return c


class ConfigurationTopTable:
    def __init__(self):
        self._active = None
        # rows.add(...) and row.activate() both move _active; a new row also copies the row above
        self.rows = _Rows(owner=self, on_add=self._row_added)
        self.rows.add("Default")           # createConfiguredDesign yields one row
        self.columns = _Columns()
        self.appearanceTable = _AppearanceTable()
        self.materialTable = _MaterialTable(self)
        self.name = "Configurations"
        self.id = "1"

    @property
    def activeRow(self):
        return self._active

    def _row_added(self, row, prev):
        # a new configuration row copies the cell values of the row above it - including WHICH theme
        # row it references, so a configuration added later starts out sharing its neighbour's theme
        if prev is None:
            return
        tc = self.materialTable.parentTableColumn
        tc.getCellByRowName(row.name)._row = tc.getCellByRowName(prev.name).referencedTableRow


class _MaterialCollection:
    """design.materials - a count/item(i) collection of named materials (what iter_collection walks)."""
    def __init__(self, names):
        self._m = [SimpleNamespace(name=n, id="mat-%d" % i) for i, n in enumerate(names)]
    @property
    def count(self):
        return len(self._m)
    def item(self, i):
        return self._m[i]
    def named(self, name):
        return next(m for m in self._m if m.name == name)


class _PartRow:
    def __init__(self, name):
        self.name = name
        self.id = "part-" + name


class _PartTable:
    """Stand-in for the inserted part's configurationTable (rows addressable by name)."""
    def __init__(self, names):
        self._rows = [_PartRow(n) for n in names]
        self.rows = _Rows()
        for n in names:
            self.rows.add(n)
    def row(self, name):
        for i in range(self.rows.count):
            if self.rows.item(i).name == name:
                return self.rows.item(i)
        return None


class _FakeDataFile:
    def __init__(self, name, configs):
        self.name = name
        self.id = "urn:" + name
        self.isConfiguredDesign = True
        self.configurationTable = _PartTable(configs)


class _FakeOccurrence:
    def __init__(self, row):
        self.name = "Inserted:1"
        self.isConfiguration = True
        self.configurationRow = row


class _Occurrences:
    def __init__(self):
        self.inserted = []
    def addFromConfiguration(self, row, transform):
        occ = _FakeOccurrence(row)
        self.inserted.append((row, transform))
        return occ


class _Root:
    def __init__(self):
        self.occurrences = _Occurrences()


class _Design:
    def __init__(self, configured=False, params=None, bodies=None, features=None,
                 appearances=None, datafiles=None, materials=()):
        self._top = ConfigurationTopTable() if configured else None
        self.allParameters = _Params(params or [])
        self.created = None
        self._bodies = bodies or {}
        self._features = features or {}
        self._appearances = appearances or {}
        self._datafiles = datafiles or {}
        self.materials = _MaterialCollection(materials)
        self.rootComponent = _Root()

    @property
    def configurationTopTable(self):
        return self._top

    def createConfiguredDesign(self):
        self._top = ConfigurationTopTable()
        self.created = self._top
        return self._top


def _install(monkeypatch, design, saved=True):
    monkeypatch.setattr(dc._common, "design", lambda: design)
    monkeypatch.setattr(dc, "_doc_is_saved", lambda: saved)      # default: pretend the doc is saved
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards / dispatch ────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_action(self, monkeypatch):
        _install(monkeypatch, _Design())
        res = dc.handler(action="frobnicate")
        assert res["isError"] is True and "action" in res["message"].lower()

    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(dc._common, "design", lambda: None)
        res = dc.handler(action="create")
        assert res["isError"] is True and "design" in res["message"].lower()

    def test_column_action_requires_configured_design(self, monkeypatch):
        # add_parameter on a non-configured design should error clearly, not crash
        _install(monkeypatch, _Design(configured=False, params=[_Param("plate_len")]))
        res = dc.handler(action="add_parameter", parameter="plate_len", values={"Default": "50 mm"})
        assert res["isError"] is True and "configured" in res["message"].lower()


# ── create ───────────────────────────────────────────────────────────────────

class TestCreate:
    def test_create_converts_design(self, monkeypatch):
        d = _install(monkeypatch, _Design(configured=False))
        out = _payload(dc.handler(action="create"))
        assert d.created is not None
        assert out["configured"] is True

    def test_create_is_idempotent_when_already_configured(self, monkeypatch):
        d = _install(monkeypatch, _Design(configured=True))
        out = _payload(dc.handler(action="create"))
        # already configured -> reports it, does NOT call createConfiguredDesign again
        assert d.created is None and out["configured"] is True

    def test_create_refuses_unsaved_document(self, monkeypatch):
        # the conversion only materializes on save+reopen; converting an unsaved doc is refused
        # (and must NOT auto-save). It must also NOT have called createConfiguredDesign.
        d = _install(monkeypatch, _Design(configured=False), saved=False)
        res = dc.handler(action="create")
        assert res["isError"] is True and "save" in res["message"].lower()
        assert d.created is None      # did not mutate

    def test_create_proceeds_when_saved(self, monkeypatch):
        d = _install(monkeypatch, _Design(configured=False), saved=True)
        out = _payload(dc.handler(action="create"))
        assert d.created is not None and out["created"] is True
        # the success note steers the user to save+reopen to see it in the UI
        assert "reopen" in out["note"].lower()


# ── add_configuration (row) ─────────────────────────────────────────────────

class TestAddConfiguration:
    def test_add_row(self, monkeypatch):
        d = _install(monkeypatch, _Design(configured=True))
        out = _payload(dc.handler(action="add_configuration", name="Large"))
        names = [d.configurationTopTable.rows.item(i).name
                 for i in range(d.configurationTopTable.rows.count)]
        assert "Large" in names and out["configuration"] == "Large"

    def test_add_row_discloses_that_it_activated_the_new_configuration(self, monkeypatch):
        # adding a row switches the design to it - a payload that stayed silent would leave the
        # caller believing the configuration active before the call is still what the model shows
        _install(monkeypatch, _Design(configured=True))
        out = _payload(dc.handler(action="add_configuration", name="Large"))
        assert out["active_configuration"] == "Large"
        assert "activated it" in out["note"].lower()

    def test_add_row_requires_name(self, monkeypatch):
        _install(monkeypatch, _Design(configured=True))
        res = dc.handler(action="add_configuration", name="")
        assert res["isError"] is True and "name" in res["message"].lower()


class TestRenameConfiguration:
    def test_rename_changes_row_name(self, monkeypatch):
        d = _install(monkeypatch, _Design(configured=True))
        # default row is "Default" in the fake; rename to Medium
        out = _payload(dc.handler(action="rename_configuration", name="Default", new_name="Medium"))
        names = [d.configurationTopTable.rows.item(i).name
                 for i in range(d.configurationTopTable.rows.count)]
        assert "Medium" in names and "Default" not in names
        assert out["from"] == "Default" and out["to"] == "Medium"

    def test_rename_unknown_row_errors(self, monkeypatch):
        _install(monkeypatch, _Design(configured=True))
        res = dc.handler(action="rename_configuration", name="Ghost", new_name="X")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_rename_requires_both_names(self, monkeypatch):
        _install(monkeypatch, _Design(configured=True))
        res = dc.handler(action="rename_configuration", name="Default", new_name="")
        assert res["isError"] is True
        assert "'new_name'" in res["message"]

    def test_rename_to_existing_name_errors(self, monkeypatch):
        _install(monkeypatch, _Design(configured=True))
        dc.handler(action="add_configuration", name="Large")
        res = dc.handler(action="rename_configuration", name="Default", new_name="Large")
        assert res["isError"] is True and "exists" in res["message"].lower()


# ── add_parameter ────────────────────────────────────────────────────────────

class TestAddParameter:
    def test_param_column_and_expressions_by_row_name(self, monkeypatch):
        d = _install(monkeypatch, _Design(configured=True, params=[_Param("plate_len")]))
        # add two rows so the values map onto real rows
        dc.handler(action="add_configuration", name="Small")
        dc.handler(action="add_configuration", name="Large")
        out = _payload(dc.handler(action="add_parameter", parameter="plate_len",
                                  values={"Small": "50 mm", "Large": "120 mm"}))
        col = d.configurationTopTable.columns.added[0]
        assert col.getCellByRowName("Small").expression == "50 mm"
        assert col.getCellByRowName("Large").expression == "120 mm"
        assert out["parameter"] == "plate_len" and out["set"] == 2

    def test_missing_parameter_errors(self, monkeypatch):
        _install(monkeypatch, _Design(configured=True, params=[]))
        res = dc.handler(action="add_parameter", parameter="ghost", values={"Default": "5 mm"})
        assert res["isError"] is True and "ghost" in res["message"]

    def test_value_for_unknown_row_is_reported(self, monkeypatch):
        _install(monkeypatch, _Design(configured=True, params=[_Param("plate_len")]))
        res = dc.handler(action="add_parameter", parameter="plate_len",
                         values={"Nonexistent": "5 mm"})
        # a value naming a row that doesn't exist should surface, not silently pass
        assert res["isError"] is True and "Nonexistent" in res["message"]


# ── suppress / visibility (need a resolvable feature/body) ──────────────────

class _FakeFeature:
    def __init__(self, name):
        self.name = name


class TestSuppressVisibility:
    def test_suppress_sets_is_suppressed(self, monkeypatch):
        feat = _FakeFeature("Fillet1")
        d = _install(monkeypatch, _Design(configured=True, features={"Fillet1": feat}))
        # patch the resolver seam the tool uses to find a timeline feature by name
        monkeypatch.setattr(dc, "_resolve_feature", lambda design, name: d._features.get(name))
        dc.handler(action="add_configuration", name="Small")
        out = _payload(dc.handler(action="add_suppress", feature="Fillet1",
                                  suppressed_in=["Small"]))
        col = d.configurationTopTable.columns.added[0]
        assert col.getCellByRowName("Small").isSuppressed is True
        assert out["feature"] == "Fillet1"

    def test_visibility_sets_is_visible(self, monkeypatch):
        body = _FakeFeature("Body1")
        d = _install(monkeypatch, _Design(configured=True, bodies={"Body1": body}))
        monkeypatch.setattr(dc._BODY, "resolve", lambda raw: (d._bodies.get(raw), None) if raw in d._bodies
                            else (None, f"No body named '{raw}'."))
        dc.handler(action="add_configuration", name="Large")
        out = _payload(dc.handler(action="add_visibility", body="Body1",
                                  hidden_in=["Large"]))
        col = d.configurationTopTable.columns.added[0]
        assert col.getCellByRowName("Large").isVisible is False
        assert out["body"] == "Body1"


# ── appearance theme (ordering + linkage) ───────────────────────────────────

class TestAppearanceTheme:
    def test_appearance_adds_column_before_rows_then_links(self, monkeypatch):
        body = _FakeFeature("Body1")
        d = _install(monkeypatch, _Design(configured=True, bodies={"Body1": body},
                             appearances={"Red": object(), "Blue": object()}))
        monkeypatch.setattr(dc._BODY, "resolve", lambda raw: (d._bodies.get(raw), None) if raw in d._bodies
                            else (None, f"No body named '{raw}'."))
        monkeypatch.setattr(dc, "_resolve_appearance", lambda design, name: d._appearances.get(name))
        dc.handler(action="add_configuration", name="Small")
        out = _payload(dc.handler(action="set_appearance", body="Body1",
                                  appearances={"Default": "Red", "Small": "Blue"}))
        appt = d.configurationTopTable.appearanceTable
        # the body column was added (which auto-created the first theme row)
        assert len(appt._columns_added) == 1
        assert out["body"] == "Body1" and out["themes"] >= 2
        # CRITICAL: each CONFIG must link to the theme row carrying ITS appearance — addressed by NAME,
        # not positional index (the live bug). The theme cell for 'Default' and 'Small' must each have a
        # referencedTableRow set, and they must be DIFFERENT theme rows.
        theme_col = appt.parentTableColumn
        ref_default = theme_col.getCellByRowName("Default").referencedTableRow
        ref_small = theme_col.getCellByRowName("Small").referencedTableRow
        assert ref_default is not None and ref_small is not None
        assert ref_default is not ref_small      # distinct configs -> distinct theme rows
        # and each linked theme row carries the right appearance
        col = appt._columns_added[0]
        # find which theme index each ref points at, then check that column's cell appearance
        def appearance_for(ref_row):
            for i in range(appt.rows.count):
                if appt.rows.item(i) is ref_row:
                    return col.getCell(i).appearance
            return None
        assert appearance_for(ref_default) is d._appearances["Red"]
        assert appearance_for(ref_small) is d._appearances["Blue"]


# ── add_material: per-configuration physical material (the material theme table) ─────────────

@pytest.fixture
def mat_design(monkeypatch):
    """A configured design with three document materials, two bodies, and the body resolver stubbed."""
    d = _Design(configured=True,
                bodies={"Body1": _FakeFeature("Body1"), "Body2": _FakeFeature("Body2")},
                materials=("Steel", "ABS Plastic", "Aluminum"))
    _install(monkeypatch, d)
    monkeypatch.setattr(dc._BODY, "resolve",
                        lambda raw: (d._bodies.get(raw), None) if raw in d._bodies
                        else (None, f"No body named '{raw}'."))
    return d


def _mat_table(design):
    return design.configurationTopTable.materialTable


def _material_for(design, config, column_title):
    """What one column holds for one CONFIGURATION: follow the config's theme link to a row, then
    read that column's cell on it - the way a caller experiences the table."""
    mtbl = _mat_table(design)
    row = mtbl.parentTableColumn.getCellByRowName(config).referencedTableRow
    if row is None:
        return None
    col = next(c for c in mtbl.columns.added if c.title == column_title)
    return col.material_at(mtbl.row_index(row.name))


class TestAddMaterial:
    def test_a_materials_map_arriving_as_json_text_is_parsed_not_char_iterated(self, mat_design):
        # An object-typed input can cross the wire as its JSON TEXT (a stale client schema does
        # this); iterating that string as a map sprays per-character unknown-configuration refusals.
        out = _payload(dc.handler(action="add_material", body="Body1",
                                  materials='{"Default": "Steel"}'))
        assert _material_for(mat_design, "Default", "Body1") == "Steel"
        assert out["materials"] == {"Default": "Steel"}

    def test_unparseable_map_text_is_refused_naming_the_field(self, mat_design):
        res = dc.handler(action="add_material", body="Body1", materials="not json {")
        assert res["isError"] is True
        assert "'materials'" in res["message"] and "JSON object" in res["message"]

    def test_links_each_config_to_its_own_theme_row_by_row_name(self, mat_design):
        dc.handler(action="add_configuration", name="Small")
        out = _payload(dc.handler(action="add_material", body="Body1",
                                  materials={"Default": "Steel", "Small": "ABS Plastic"}))
        mtbl = _mat_table(mat_design)
        # one theme row per configuration, no over-provisioning
        assert mtbl.rows.count == 2
        # each CONFIG links a theme row addressed BY NAME - a positional getCell() on the theme
        # column hands back an unrelated scratch cell, so an index-linked tool leaves these unset
        theme_col = mtbl.parentTableColumn
        ref_default = theme_col.getCellByRowName("Default").referencedTableRow
        ref_small = theme_col.getCellByRowName("Small").referencedTableRow
        assert ref_default is not None and ref_small is not None
        assert ref_default is not ref_small
        # and the theme row each config points at carries ITS material
        assert _material_for(mat_design, "Default", "Body1") == "Steel"
        assert _material_for(mat_design, "Small", "Body1") == "ABS Plastic"
        # the payload publishes what the CELLS read back, per configuration
        assert out["materials"] == {"Default": "Steel", "Small": "ABS Plastic"}
        assert out["themes"] == 2 and out["column_title"] == "Body1"
        assert "configurations_unset" not in out

    def test_a_second_body_does_not_re_point_the_first_bodys_configurations(self, mat_design):
        # theme rows and the config->theme link are TABLE-global: allocating rows positionally on the
        # second call silently moves configurations off the rows the first call gave them, changing
        # the first body's materials while every read-back of the second call still passes
        dc.handler(action="add_configuration", name="Small")
        _payload(dc.handler(action="add_material", body="Body1",
                            materials={"Default": "Steel", "Small": "ABS Plastic"}))
        out = _payload(dc.handler(action="add_material", body="Body2",
                                  materials={"Small": "Aluminum"}))
        # the first body keeps exactly what it was configured with
        assert _material_for(mat_design, "Default", "Body1") == "Steel"
        assert _material_for(mat_design, "Small", "Body1") == "ABS Plastic"
        # and the second body is Aluminum in Small ONLY - not in Default too
        assert _material_for(mat_design, "Small", "Body2") == "Aluminum"
        assert _material_for(mat_design, "Default", "Body2") != "Aluminum"
        assert out["materials"] == {"Small": "Aluminum"}
        assert out["configurations_unset"] == ["Default"]

    def test_a_configuration_moved_to_its_own_theme_row_keeps_the_other_bodys_material(self, mat_design):
        # 'Large' is added after the first column and starts out sharing 'Small's theme row, so it
        # shows Body1 as ABS Plastic. Setting Body2 for it has to mint a row and carry that across -
        # a minted row otherwise copies the row ABOVE it and Body1 silently becomes Steel.
        dc.handler(action="add_configuration", name="Small")
        _payload(dc.handler(action="add_material", body="Body1",
                            materials={"Default": "Steel", "Small": "ABS Plastic"}))
        dc.handler(action="add_configuration", name="Large")
        assert _material_for(mat_design, "Large", "Body1") == "ABS Plastic"
        _payload(dc.handler(action="add_material", body="Body2", materials={"Large": "Aluminum"}))
        assert _material_for(mat_design, "Large", "Body1") == "ABS Plastic"
        assert _material_for(mat_design, "Small", "Body1") == "ABS Plastic"
        assert _material_for(mat_design, "Default", "Body1") == "Steel"
        assert _material_for(mat_design, "Large", "Body2") == "Aluminum"

    def test_a_minted_theme_row_never_takes_a_name_the_table_already_carries(self, mat_design):
        # rows.add(<a name an existing row carries>) returns THAT row and adds nothing, so a
        # count-based name that collides would put two configurations on one row
        mtbl = _mat_table(mat_design)
        mtbl.rows.add("Material 2")          # the name the count-based scheme reaches for first
        dc.handler(action="add_configuration", name="Small")
        _payload(dc.handler(action="add_material", body="Body1",
                            materials={"Default": "Steel", "Small": "ABS Plastic"}))
        theme_col = mtbl.parentTableColumn
        ref_default = theme_col.getCellByRowName("Default").referencedTableRow
        ref_small = theme_col.getCellByRowName("Small").referencedTableRow
        assert ref_default is not None and ref_default is not ref_small
        assert _material_for(mat_design, "Default", "Body1") == "Steel"
        assert _material_for(mat_design, "Small", "Body1") == "ABS Plastic"

    def test_a_repeat_call_for_the_same_body_updates_its_existing_column(self, mat_design):
        # columns.add(<a body that already has a column>) returns the EXISTING column, so a second
        # call re-materials that body rather than building a duplicate column
        _payload(dc.handler(action="add_material", body="Body1", materials={"Default": "Steel"}))
        _payload(dc.handler(action="add_material", body="Body1", materials={"Default": "ABS Plastic"}))
        mtbl = _mat_table(mat_design)
        assert mtbl.columns.count == 2 and len(mtbl.columns.added) == 1
        assert _material_for(mat_design, "Default", "Body1") == "ABS Plastic"

    def test_a_carry_that_does_not_take_is_an_error(self, mat_design):
        # moving a configuration to its own theme row must bring every OTHER column's material with
        # it; a column that drops the copy leaves that configuration mis-materialled, so the call
        # fails naming the column instead of reporting success
        dc.handler(action="add_configuration", name="Small")
        _payload(dc.handler(action="add_material", body="Body1",
                            materials={"Default": "Steel", "Small": "ABS Plastic"}))
        dc.handler(action="add_configuration", name="Large")     # inherits Small's theme row
        mtbl = _mat_table(mat_design)
        root = mtbl.columns.item(0)                              # the root-component column
        root.getCell(mtbl.row_index("Theme 1"))._material = mat_design.materials.named("Steel")
        mtbl.silent_columns.add(root.title)
        res = dc.handler(action="add_material", body="Body2", materials={"Large": "Aluminum"})
        assert res["isError"] is True and root.title in res["message"]

    def test_configurations_left_out_of_the_map_are_published(self, mat_design):
        # every configuration starts on the one auto-created theme row, so an unnamed configuration
        # keeps another configuration's material - say so instead of implying it was set
        dc.handler(action="add_configuration", name="Small")
        dc.handler(action="add_configuration", name="Large")
        out = _payload(dc.handler(action="add_material", body="Body1", materials={"Small": "Steel"}))
        assert out["configurations_unset"] == ["Default", "Large"]
        assert "Default" in out["note"] and "Large" in out["note"]

    def test_dropped_theme_link_is_an_error(self, mat_design):
        # two configurations, so 'Default' has to MOVE to a theme row of its own: the link assignment
        # is silently ignored, leaving it on the shared row while the payload claims it was linked
        dc.handler(action="add_configuration", name="Small")
        _mat_table(mat_design).parentTableColumn.cell_mode = "silent"
        res = dc.handler(action="add_material", body="Body1",
                         materials={"Default": "Steel", "Small": "ABS Plastic"})
        assert res["isError"] is True and "theme link did not take" in res["message"]

    def test_theme_link_pointing_at_another_row_is_an_error(self, mat_design):
        theme_col = _mat_table(mat_design).parentTableColumn
        theme_col.cell_mode = "lies"
        theme_col.substitute = SimpleNamespace(name="Theme 9")
        res = dc.handler(action="add_material", body="Body1", materials={"Default": "Steel"})
        assert res["isError"] is True
        # the error names BOTH the row it landed on and the row it should have linked
        assert "Theme 9" in res["message"] and "Theme 1" in res["message"]

    def test_auto_root_column_does_not_fail_the_call(self, mat_design):
        # adding the first non-root column also mints a root-component column: the count lands on 2
        # after ONE add, and the call must not gate on it
        out = _payload(dc.handler(action="add_material", body="Body1", materials={"Default": "Steel"}))
        assert _mat_table(mat_design).columns.count == 2
        assert out["themes"] == 1

    def test_unconfirmed_readback_is_an_error(self, mat_design):
        # the cell silently keeps no material: reporting ok here would publish a configuration
        # carrying the wrong material
        _mat_table(mat_design).cell_mode = "silent"
        res = dc.handler(action="add_material", body="Body1", materials={"Default": "Steel"})
        assert res["isError"] is True and "could not be confirmed" in res["message"]

    def test_readback_of_a_different_material_is_an_error(self, mat_design):
        mtbl = _mat_table(mat_design)
        mtbl.cell_mode = "lies"
        mtbl.substitute = SimpleNamespace(name="Brass")
        res = dc.handler(action="add_material", body="Body1", materials={"Default": "Steel"})
        assert res["isError"] is True
        assert "Brass" in res["message"] and "Steel" in res["message"]

    def test_unknown_configuration_is_refused_naming_it(self, mat_design):
        res = dc.handler(action="add_material", body="Body1", materials={"Nonexistent": "Steel"})
        assert res["isError"] is True and "Nonexistent" in res["message"]
        assert _mat_table(mat_design).columns.count == 0

    def test_material_missing_from_the_design_is_refused_before_mutating(self, mat_design):
        dc.handler(action="add_configuration", name="Small")
        res = dc.handler(action="add_material", body="Body1",
                         materials={"Default": "Steel", "Small": "Unobtanium"})
        assert res["isError"] is True and "Unobtanium" in res["message"]
        # nothing was built: a half-populated theme table is exactly what the up-front resolve prevents
        mtbl = _mat_table(mat_design)
        assert mtbl.columns.count == 0 and mtbl.rows.count == 0

    def test_duplicate_document_material_name_is_refused(self, monkeypatch):
        d = _Design(configured=True, bodies={"Body1": _FakeFeature("Body1")},
                    materials=("Steel", "Steel"))
        _install(monkeypatch, d)
        monkeypatch.setattr(dc._BODY, "resolve", lambda raw: (d._bodies.get(raw), None))
        res = dc.handler(action="add_material", body="Body1", materials={"Default": "Steel"})
        assert res["isError"] is True and "refusing to pick one" in res["message"]

    def test_empty_material_map_is_refused(self, mat_design):
        res = dc.handler(action="add_material", body="Body1", materials={})
        assert res["isError"] is True and "materials" in res["message"]
        assert _mat_table(mat_design).columns.count == 0

    def test_null_column_add_is_an_error(self, mat_design):
        _mat_table(mat_design).add_returns_null = True
        res = dc.handler(action="add_material", body="Body1", materials={"Default": "Steel"})
        assert res["isError"] is True and "null" in res["message"]


# ── add_insert: nested configuration (insert a configured part, map per assembly config) ─────

class TestAddInsert:
    def _setup(self, monkeypatch):
        # an assembly design with two configs, and a configured part DataFile resolvable by name
        d = _install(monkeypatch, _Design(configured=True,
                             datafiles={"Bracket": _FakeDataFile("Bracket", ["Medium", "Small", "Large"])}))
        monkeypatch.setattr(dc, "_resolve_datafile", lambda design, name: d._datafiles.get(name))
        dc.handler(action="add_configuration", name="HeavyDuty")   # rows: Default, HeavyDuty
        return d

    def test_insert_and_map_each_config_by_name(self, monkeypatch):
        d = self._setup(monkeypatch)
        out = _payload(dc.handler(action="add_insert", insert_part="Bracket",
                                  insert_config="Medium",
                                  insert_map={"Default": "Medium", "HeavyDuty": "Large"}))
        root = d.rootComponent
        # the part was inserted via addFromConfiguration with the 'Medium' part row
        assert len(root.occurrences.inserted) == 1
        inserted_row, _ = root.occurrences.inserted[0]
        assert inserted_row.name == "Medium"
        # an insert column was added and each assembly config's cell .row is the RIGHT part row (by name)
        col = d.configurationTopTable.columns.added[-1]
        assert col.getCellByRowName("Default").row.name == "Medium"
        assert col.getCellByRowName("HeavyDuty").row.name == "Large"
        assert out["inserted_part"] == "Bracket" and out["mapped"] == 2

    def test_unknown_part_errors(self, monkeypatch):
        self._setup(monkeypatch)
        res = dc.handler(action="add_insert", insert_part="Ghost",
                         insert_map={"Default": "Medium"})
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_map_to_unknown_part_config_errors(self, monkeypatch):
        self._setup(monkeypatch)
        res = dc.handler(action="add_insert", insert_part="Bracket",
                         insert_config="Medium",
                         insert_map={"Default": "Gigantic"})
        # a part config that doesn't exist must be reported, naming it
        assert res["isError"] is True and "Gigantic" in res["message"]

    def test_map_to_unknown_assembly_config_errors(self, monkeypatch):
        self._setup(monkeypatch)
        res = dc.handler(action="add_insert", insert_part="Bracket",
                         insert_config="Medium",
                         insert_map={"Nonexistent": "Medium"})
        assert res["isError"] is True and "Nonexistent" in res["message"]

    def test_insert_config_defaults_to_first_part_row(self, monkeypatch):
        d = self._setup(monkeypatch)
        _payload(dc.handler(action="add_insert", insert_part="Bracket",
                            insert_map={"Default": "Medium", "HeavyDuty": "Small"}))
        # no insert_config given -> inserts the part's first row (Medium)
        inserted_row, _ = d.rootComponent.occurrences.inserted[0]
        assert inserted_row.name == "Medium"


# ── activate: switch a configuration + surface a rebuild that breaks the timeline ─────────────

class _ActRow:
    def __init__(self, name, table):
        self.name = name
        self.id = "row-" + name
        self._table = table
    def activate(self):
        self._table._active = self          # a real switch updates the table's active row
        return True


class _ActRows:
    def __init__(self, table, names):
        self._r = [_ActRow(n, table) for n in names]
    @property
    def count(self):
        return len(self._r)
    def item(self, i):
        return self._r[i]
    def __iter__(self):
        return iter(self._r)


class _ActTable:
    def __init__(self, names):
        self.rows = _ActRows(self, names)
        self._active = self.rows.item(0)
    @property
    def activeRow(self):
        return self._active


class _ActTimeline:
    """A timeline with N features in error (healthState 2) - what _common.timeline_health reads."""
    def __init__(self, n_errors):
        self._items = [SimpleNamespace(healthState=2, name="F%d" % i) for i in range(n_errors)]
    @property
    def count(self):
        return len(self._items)
    def item(self, i):
        return self._items[i]


class _ActDesign:
    """A configured design whose rebuild (computeAll) can flip features into error, so the activate
    guard's before/after timeline_health comparison has something to catch."""
    def __init__(self, table, errors_before=0, errors_after=0):
        self._top = table
        self._computed = False
        self._before, self._after = errors_before, errors_after
    @property
    def configurationTopTable(self):
        return self._top
    def computeAll(self):
        self._computed = True
    @property
    def timeline(self):
        return _ActTimeline(self._after if self._computed else self._before)


class TestActivate:
    def test_switches_configuration_clean(self, monkeypatch):
        d = _ActDesign(_ActTable(["Default", "Large"]))
        monkeypatch.setattr(dc._common, "design", lambda: d)
        out = _payload(dc.handler(action="activate", name="Large"))
        assert out["activated"] is True and out["now_active"] == "Large"
        assert out["previous"] == "Default"
        assert "timeline_warning" not in out

    def test_unknown_configuration_errors(self, monkeypatch):
        d = _ActDesign(_ActTable(["Default", "Large"]))
        monkeypatch.setattr(dc._common, "design", lambda: d)
        res = dc.handler(action="activate", name="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_new_timeline_error_after_switch_is_surfaced(self, monkeypatch):
        # the rebuilt configuration over/under-constrains the model: the switch stands, but the new
        # timeline error must be reported rather than a clean success over a broken model.
        d = _ActDesign(_ActTable(["Default", "Large"]), errors_before=0, errors_after=1)
        monkeypatch.setattr(dc._common, "design", lambda: d)
        out = _payload(dc.handler(action="activate", name="Large"))
        assert out["activated"] is True
        assert "timeline_warning" in out and "new error" in out["timeline_warning"].lower()
