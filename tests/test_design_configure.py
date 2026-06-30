"""Unit tests for ``design_configure`` — the WRITE counterpart to design_get_configurations.

The Fusion configurations API is mocked; what we pin is the tool's OWN logic: the action dispatch and
guards (unknown action, no design, not-yet-configured for column actions), creating a configured
design, adding configuration rows, and the four column kinds (parameter / suppress / visibility /
appearance-theme) including:
  - addressing cells by ROW NAME (getCellByRowName) — the robust path the live spike found,
  - parameter cells take an EXPRESSION string,
  - suppress cells take isSuppressed (bool), visibility cells take isVisible (bool),
  - the appearance-theme ORDERING: the body column must be added before extra theme rows, and each
    config row is linked to a theme row via the top table's parentTableColumn (ConfigurationThemeColumn).

Fakes are NAMED to match the real adsk classes where the handler reads type(x).__name__.
"""

import json

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
    def __init__(self, name, idx):
        self.name = name
        self.id = "row-" + name
        self.index = idx
        self.activated = False
    def activate(self):
        self.activated = True
        return True


class _Rows:
    def __init__(self):
        self._r = []
    @property
    def count(self):
        return len(self._r)
    def item(self, i):
        return self._r[i]
    def __iter__(self):
        return iter(self._r)        # real ConfigurationRows is iterable (read tool's _find_row needs it)
    def add(self, name):
        r = _Row(name, len(self._r))
        self._r.append(r)
        return r


class _ThemeCell:
    def __init__(self):
        self.referencedTableRow = None


class _ThemeColumn:
    """Models the live trap: getCell(index) and getCellByRowName(name) address DIFFERENT cells.
    The tool must use getCellByRowName for the config->theme link; a positional getCell() here returns
    a throwaway cell that the assertions never inspect, so an index-based tool would silently mislink."""
    def __init__(self, rows):
        self._rows = rows
        self.by_name = {}
        self._scratch = {}
    def getCell(self, i):
        # NOT the addressing the tool should use — hand back a scratch cell unrelated to by_name.
        self._scratch.setdefault(i, _ThemeCell())
        return self._scratch[i]
    def getCellByRowName(self, name):
        if name not in self.by_name:
            self.by_name[name] = _ThemeCell()
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
        self.rows = _Rows()
        self.rows.add("Default")           # createConfiguredDesign yields one row
        self.columns = _Columns()
        self.appearanceTable = _AppearanceTable()
        self.name = "Configurations"
        self.id = "1"


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
                 appearances=None, datafiles=None):
        self._top = ConfigurationTopTable() if configured else None
        self.allParameters = _Params(params or [])
        self.created = None
        self._bodies = bodies or {}
        self._features = features or {}
        self._appearances = appearances or {}
        self._datafiles = datafiles or {}
        self.rootComponent = _Root()

    @property
    def configurationTopTable(self):
        return self._top

    def createConfiguredDesign(self):
        self._top = ConfigurationTopTable()
        self.created = self._top
        return self._top


def _install(design, saved=True):
    dc._common.design = lambda: design
    dc._doc_is_saved = lambda: saved      # default: pretend the doc is saved
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards / dispatch ────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_action(self):
        _install(_Design())
        res = dc.handler(action="frobnicate")
        assert res["isError"] is True and "action" in res["message"].lower()

    def test_no_active_design(self):
        dc._common.design = lambda: None
        res = dc.handler(action="create")
        assert res["isError"] is True and "design" in res["message"].lower()

    def test_column_action_requires_configured_design(self):
        # add_parameter on a non-configured design should error clearly, not crash
        _install(_Design(configured=False, params=[_Param("plate_len")]))
        res = dc.handler(action="add_parameter", parameter="plate_len", values={"Default": "50 mm"})
        assert res["isError"] is True and "configured" in res["message"].lower()


# ── create ───────────────────────────────────────────────────────────────────

class TestCreate:
    def test_create_converts_design(self):
        d = _install(_Design(configured=False))
        out = _payload(dc.handler(action="create"))
        assert d.created is not None
        assert out["configured"] is True

    def test_create_is_idempotent_when_already_configured(self):
        d = _install(_Design(configured=True))
        out = _payload(dc.handler(action="create"))
        # already configured -> reports it, does NOT call createConfiguredDesign again
        assert d.created is None and out["configured"] is True

    def test_create_refuses_unsaved_document(self):
        # the conversion only materializes on save+reopen; converting an unsaved doc is refused
        # (and must NOT auto-save). It must also NOT have called createConfiguredDesign.
        d = _install(_Design(configured=False), saved=False)
        res = dc.handler(action="create")
        assert res["isError"] is True and "save" in res["message"].lower()
        assert d.created is None      # did not mutate

    def test_create_proceeds_when_saved(self):
        d = _install(_Design(configured=False), saved=True)
        out = _payload(dc.handler(action="create"))
        assert d.created is not None and out["created"] is True
        # the success note steers the user to save+reopen to see it in the UI
        assert "reopen" in out["note"].lower()


# ── add_configuration (row) ─────────────────────────────────────────────────

class TestAddConfiguration:
    def test_add_row(self):
        d = _install(_Design(configured=True))
        out = _payload(dc.handler(action="add_configuration", name="Large"))
        names = [d.configurationTopTable.rows.item(i).name
                 for i in range(d.configurationTopTable.rows.count)]
        assert "Large" in names and out["configuration"] == "Large"

    def test_add_row_requires_name(self):
        _install(_Design(configured=True))
        res = dc.handler(action="add_configuration", name="")
        assert res["isError"] is True and "name" in res["message"].lower()


class TestRenameConfiguration:
    def test_rename_changes_row_name(self):
        d = _install(_Design(configured=True))
        # default row is "Default" in the fake; rename to Medium
        out = _payload(dc.handler(action="rename_configuration", name="Default", new_name="Medium"))
        names = [d.configurationTopTable.rows.item(i).name
                 for i in range(d.configurationTopTable.rows.count)]
        assert "Medium" in names and "Default" not in names
        assert out["from"] == "Default" and out["to"] == "Medium"

    def test_rename_unknown_row_errors(self):
        _install(_Design(configured=True))
        res = dc.handler(action="rename_configuration", name="Ghost", new_name="X")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_rename_requires_both_names(self):
        _install(_Design(configured=True))
        res = dc.handler(action="rename_configuration", name="Default", new_name="")
        assert res["isError"] is True

    def test_rename_to_existing_name_errors(self):
        _install(_Design(configured=True))
        dc.handler(action="add_configuration", name="Large")
        res = dc.handler(action="rename_configuration", name="Default", new_name="Large")
        assert res["isError"] is True and "exists" in res["message"].lower()


# ── add_parameter ────────────────────────────────────────────────────────────

class TestAddParameter:
    def test_param_column_and_expressions_by_row_name(self):
        d = _install(_Design(configured=True, params=[_Param("plate_len")]))
        # add two rows so the values map onto real rows
        dc.handler(action="add_configuration", name="Small")
        dc.handler(action="add_configuration", name="Large")
        out = _payload(dc.handler(action="add_parameter", parameter="plate_len",
                                  values={"Small": "50 mm", "Large": "120 mm"}))
        col = d.configurationTopTable.columns.added[0]
        assert col.getCellByRowName("Small").expression == "50 mm"
        assert col.getCellByRowName("Large").expression == "120 mm"
        assert out["parameter"] == "plate_len" and out["set"] == 2

    def test_missing_parameter_errors(self):
        _install(_Design(configured=True, params=[]))
        res = dc.handler(action="add_parameter", parameter="ghost", values={"Default": "5 mm"})
        assert res["isError"] is True and "ghost" in res["message"]

    def test_value_for_unknown_row_is_reported(self):
        _install(_Design(configured=True, params=[_Param("plate_len")]))
        res = dc.handler(action="add_parameter", parameter="plate_len",
                         values={"Nonexistent": "5 mm"})
        # a value naming a row that doesn't exist should surface, not silently pass
        assert res["isError"] is True and "Nonexistent" in res["message"]


# ── suppress / visibility (need a resolvable feature/body) ──────────────────

class _FakeFeature:
    def __init__(self, name):
        self.name = name


class TestSuppressVisibility:
    def test_suppress_sets_is_suppressed(self):
        feat = _FakeFeature("Fillet1")
        d = _install(_Design(configured=True, features={"Fillet1": feat}))
        # patch the resolver seam the tool uses to find a timeline feature by name
        dc._resolve_feature = lambda design, name: d._features.get(name)
        dc.handler(action="add_configuration", name="Small")
        out = _payload(dc.handler(action="add_suppress", feature="Fillet1",
                                  suppressed_in=["Small"]))
        col = d.configurationTopTable.columns.added[0]
        assert col.getCellByRowName("Small").isSuppressed is True
        assert out["feature"] == "Fillet1"

    def test_visibility_sets_is_visible(self):
        body = _FakeFeature("Body1")
        d = _install(_Design(configured=True, bodies={"Body1": body}))
        dc._resolve_body = lambda design, name: d._bodies.get(name)
        dc.handler(action="add_configuration", name="Large")
        out = _payload(dc.handler(action="add_visibility", body="Body1",
                                  hidden_in=["Large"]))
        col = d.configurationTopTable.columns.added[0]
        assert col.getCellByRowName("Large").isVisible is False
        assert out["body"] == "Body1"


# ── appearance theme (ordering + linkage) ───────────────────────────────────

class TestAppearanceTheme:
    def test_appearance_adds_column_before_rows_then_links(self):
        body = _FakeFeature("Body1")
        d = _install(_Design(configured=True, bodies={"Body1": body},
                             appearances={"Red": object(), "Blue": object()}))
        dc._resolve_body = lambda design, name: d._bodies.get(name)
        dc._resolve_appearance = lambda design, name: d._appearances.get(name)
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


# ── add_insert: nested configuration (insert a configured part, map per assembly config) ─────

class TestAddInsert:
    def _setup(self):
        # an assembly design with two configs, and a configured part DataFile resolvable by name
        d = _install(_Design(configured=True,
                             datafiles={"Bracket": _FakeDataFile("Bracket", ["Medium", "Small", "Large"])}))
        dc._resolve_datafile = lambda design, name: d._datafiles.get(name)
        dc.handler(action="add_configuration", name="HeavyDuty")   # rows: Default, HeavyDuty
        return d

    def test_insert_and_map_each_config_by_name(self):
        d = self._setup()
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

    def test_unknown_part_errors(self):
        self._setup()
        res = dc.handler(action="add_insert", insert_part="Ghost",
                         insert_map={"Default": "Medium"})
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_map_to_unknown_part_config_errors(self):
        self._setup()
        res = dc.handler(action="add_insert", insert_part="Bracket",
                         insert_config="Medium",
                         insert_map={"Default": "Gigantic"})
        # a part config that doesn't exist must be reported, naming it
        assert res["isError"] is True and "Gigantic" in res["message"]

    def test_map_to_unknown_assembly_config_errors(self):
        self._setup()
        res = dc.handler(action="add_insert", insert_part="Bracket",
                         insert_config="Medium",
                         insert_map={"Nonexistent": "Medium"})
        assert res["isError"] is True and "Nonexistent" in res["message"]

    def test_insert_config_defaults_to_first_part_row(self):
        d = self._setup()
        _payload(dc.handler(action="add_insert", insert_part="Bracket",
                            insert_map={"Default": "Medium", "HeavyDuty": "Small"}))
        # no insert_config given -> inserts the part's first row (Medium)
        inserted_row, _ = d.rootComponent.occurrences.inserted[0]
        assert inserted_row.name == "Medium"
