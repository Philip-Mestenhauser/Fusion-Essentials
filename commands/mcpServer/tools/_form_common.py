# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The T-spline Form substrate the form_ tools share: the design-wide Form walk, the create
lifecycle with its rollback, and the creation record stored on each Form."""

import json

import adsk.fusion

from ._common import safe
from . import _common
from . import _export
from . import _inputs

MAP_BLURB = ("the T-spline Form substrate: all_forms/form_label/is_form/resolve_form - the "
             "design-wide formFeatures walk, a Form's '<component>/<name>' address and its resolve "
             "(a shared name refused); health_label; body_box_cm; create_form/finish_edit/retire - "
             "the add, load and always-finish lifecycle and its delete-and-recount rollback; "
             "read_record/store_record - the fe_mcp creation record")

RECORD_GROUP, RECORD_NAME = "fe_mcp", "form"
_FORM_TYPE = "adsk::fusion::FormFeature"
_FINISH_FORM = "TSplineBaseFeatureStop"
# How long a raising finishEdit's design type is pumped for before the edit is judged still open.
_SETTLE_S = 2.0
_HEALTH = (("healthy", "HealthyFeatureHealthState"), ("warning", "WarningFeatureHealthState"),
           ("error", "ErrorFeatureHealthState"), ("suppressed", "SuppressedFeatureHealthState"),
           ("rolled_back", "RolledBackFeatureHealthState"))


def is_form(entity):
    """True when the entity reads as a FormFeature."""
    return safe(lambda: entity.objectType) == _FORM_TYPE


def form_label(ff):
    """The Form's '<component>/<name>' address - Form names repeat across components."""
    return f"{safe(lambda: ff.parentComponent.name) or '?'}/{safe(lambda: ff.name) or '?'}"


def all_forms(design):
    """[(component, FormFeature)] over every component's formFeatures, in component order."""
    return [(comp, ff) for comp in _common.all_components(design)
            for ff in _common.iter_collection(safe(lambda c=comp: c.features.formFeatures))]


def resolve_form(design, kind, raw):
    """(FormFeature, error): a timeline index through `kind`, else the one Form `raw` names."""
    want = (raw or "").strip() if isinstance(raw, str) else raw
    if isinstance(want, int) or (isinstance(want, str) and want.isdigit()):
        got, err = kind.resolve(str(want))
        if err or got is None:
            return None, err
        entity, name = got
        if not is_form(entity):
            found = (safe(lambda: entity.objectType) or type(entity).__name__).split("::")[-1]
            return None, (f"'{kind.name}': '{name}' is a {found}, not a Form - form_get lists the "
                          "Forms.")
        return entity, None
    # The formFeatures walk sees a Form inside a collapsed timeline group, which the timeline hides.
    forms = [ff for _comp, ff in all_forms(design)]
    low = str(want or "").lower()
    hits = ([ff for ff in forms if form_label(ff).lower() == low]
            or [ff for ff in forms if (safe(lambda f=ff: f.name) or "").strip().lower() == low])
    labels = _common.named_with_remainder([form_label(ff) for ff in (hits or forms)])
    if not hits:
        return None, f"'{kind.name}': no Form is named '{want}' - the Forms: {labels or '(none)'}."
    if len(hits) > 1:
        return None, (f"'{kind.name}': '{want}' names {len(hits)} Forms ({labels}) - name one as "
                      "'<component>/<name>' or by its timeline index.")
    return hits[0], None


def health_label(ff):
    """The Form's healthState as a word, or None when it does not read."""
    states = safe(lambda: adsk.fusion.FeatureHealthStates)
    hs = safe(lambda: ff.healthState)
    for label, member in _HEALTH:
        if hs is not None and hs == safe(lambda m=member: getattr(states, m)):
            return label
    return None


def body_box_cm(bodies):
    """([min], [max]) corners in cm over the bodies' boxes, or None when one does not read."""
    lo, hi = [None] * 3, [None] * 3
    for b in bodies:
        bb = safe(lambda b=b: b.boundingBox)
        for i, axis in enumerate("xyz"):
            a = _common.measured(lambda: getattr(bb.minPoint, axis), 1.0, 9)
            z = _common.measured(lambda: getattr(bb.maxPoint, axis), 1.0, 9)
            if a is None or z is None:
                return None
            lo[i] = a if lo[i] is None else min(lo[i], a)
            hi[i] = z if hi[i] is None else max(hi[i], z)
    return (lo, hi) if bodies else None


def read_record(ff):
    """The creation record form_create stored on the Form, or None."""
    attr = safe(lambda: ff.attributes.itemByName(RECORD_GROUP, RECORD_NAME))
    raw = safe(lambda: attr.value) if attr is not None else None
    try:
        rec = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def store_record(ff, record):
    """True when the record was attached and reads back identically."""
    text = json.dumps(record, separators=(",", ":"), sort_keys=True)
    try:
        ff.attributes.add(RECORD_GROUP, RECORD_NAME, text)
    except Exception:
        return False
    return safe(lambda: ff.attributes.itemByName(RECORD_GROUP, RECORD_NAME).value) == text


def _parametric(design):
    """(settled, mode) for pump_until: settled once the design reads parametric."""
    mode = _inputs.current_design_type(design)
    return mode == _inputs.MODE_PARAMETRIC, mode


def finish_edit(ff, design):
    """(state, detail): 'closed'; after a raise 'raised', 'open' or 'unread'; else 'unconfirmed'."""
    try:
        done = ff.finishEdit()
    except Exception as e:
        # No Finish Form here: it would rerun the conversion that just raised. The design type
        # reads direct while an edit is open and parametric once it closes (measured).
        mode = _export.pump_until(lambda: _parametric(design), _SETTLE_S, 0.02)[1]
        state = {_inputs.MODE_PARAMETRIC: "raised", _inputs.MODE_DIRECT: "open"}.get(mode, "unread")
        return state, str(e) or type(e).__name__
    if done is True:
        return "closed", None
    ran = safe(lambda: _common.app.userInterface.commandDefinitions.itemById(
        _FINISH_FORM).execute())
    return "unconfirmed", f"finishEdit returned {done}; Finish Form's execute() returned {ran}"


def _crossing(detail, retry):
    """The self-intersection Fusion's raise names, and with `retry` the call that follows it."""
    if "SELF_INTERSECTS" not in detail:
        return " Correct the cage and call form_create again." if retry else ""
    return " The cage's surface passes through itself" + (
        " - move the grips that cross apart and call form_create again." if retry else ".")


def retire(design, ff, label, count_before):
    """'' when deleteMe removed the Form and the count is restored, else the leftover sentence."""
    try:
        did = ff.deleteMe()
        why = f"deleteMe returned {did}"
    except Exception as e:
        did, why = None, f"deleteMe raised: {e}"
    now = _common.counted(lambda: design.timeline.count)
    if did is True and now is not None and now == count_before:
        return ""
    # MEASURED: a Form whose load failed reads healthy while it holds no body, so its health
    # cannot say it is a leftover - the count is what does.
    return (f" Its removal did not confirm ({why}; the timeline reads {now} item(s), "
            f"{count_before} before): '{label}' may remain - remove it with "
            f"design_delete_feature(feature='{label}').")


def create_form(design, comp, text, name):
    """(facts, error): add, load and always-finish a Form in comp; a failed open or load is deleted."""
    marker = _common.timeline_marker(design)[0]
    count = _common.counted(lambda: design.timeline.count)
    forms = safe(lambda: comp.features.formFeatures)
    if forms is None:
        return None, "This component exposes no formFeatures collection; nothing was created."
    ff = forms.add()
    if not ff:
        return None, ("FormFeatures.add() returned nothing; design_get(include=['timeline']) "
                      "shows whether a Form was added.")
    label = form_label(ff)
    # Design-wide reads raise inside the edit (measured), so the Form is read before it opens; an
    # unnamed body takes the Form's own name.
    want = (name or "").strip() or safe(lambda: ff.name) or ""
    try:
        started = ff.startEdit()
    except Exception as e:
        started = f"raised: {e}"
    if started is not True:
        return None, (f"The Form's edit did not open (startEdit {started})."
                      + (retire(design, ff, label, count) or " The Form was removed."))
    tb, load_error, rename_warning, back, tb_name = None, None, None, None, None
    try:
        try:
            tb = ff.tSplineBodies.addByTSMDescription(text)
        except Exception as e:
            load_error = str(e) or type(e).__name__
        if tb is None and load_error is None:
            load_error = "addByTSMDescription returned nothing"
        if tb is not None:
            # Named at once, the B-Rep the finish converts takes the same name.
            tb_name, rename_warning = _common.apply_rename(tb, want)
            back = safe(lambda: tb.getTSMDescription())
    finally:
        state, detail = finish_edit(ff, design)
    remove = f" remove '{label}' with design_delete_feature(feature='{label}')."
    raised = (f"finishEdit on '{label}' raised: {(detail or '').rstrip('. ')}"
              + (f" (the load: {load_error})" if load_error else "") + ".")
    if state == "raised":
        return None, (raised + (retire(design, ff, label, count) or " The Form was removed.")
                      + _crossing(detail, True))
    if state == "open":
        return None, (raised + f" The design still reads direct, so the Form edit is still open "
                      f"and '{label}' was left in place." + _crossing(detail, False)
                      + " Ask the user to correct the cage in that edit and click Finish Form, "
                      "then" + remove)
    if state == "unread":
        return None, (raised + " The design type did not read after it, so whether the Form edit "
                      f"is open is unknown; '{label}' was left in place. If workspace_orient "
                      "reports in_form_edit, ask the user to click Finish Form; then" + remove)
    if state != "closed":
        return None, (f"'{label}' was {'not loaded' if load_error else 'loaded'}, but its edit did "
                      f"not confirm closed ({detail}), so nothing was verified or deleted. If "
                      "workspace_orient reports in_form_edit, ask the user to click Finish Form; "
                      "then" + remove)
    if load_error:
        return None, (f"Fusion refused the cage: {load_error}."
                      + (retire(design, ff, label, count) or " The Form made for it was removed."))
    return {"ff": ff, "label": label, "count_before": count, "marker_before": marker,
            "tspline_body": tb_name, "rename_warning": rename_warning, "readback": back}, None
