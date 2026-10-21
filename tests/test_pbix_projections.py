"""PBIX projection/field extraction tests.

Real Power BI visuals carry their data bindings in ``singleVisual.projections``:
each entry maps a queryRef (e.g. ``Sales.Sum of Amount``) to roles like Y,
Category, Values, X, Series. ``api.pbix_spec`` must surface these on every
widget so the plan builder can bind data deterministically instead of guessing
from titles alone.
"""

import json

from api import pbix_spec


# ───────────────────────────── helpers ───────────────────────────────


def _vc_with_projections(vtype, title=None, text=None, projections=None, box=None, page_w=1280.0, page_h=720.0):
    """A stringified visual container whose singleVisual carries projections."""
    pos = dict({"x": 10, "y": 10, "width": 300, "height": 200, "z": 0}, **(box or {}))
    vis = {"visualType": vtype}
    if text is not None:
        vis["textRange"] = {"text": text, "fontSize": 15}
    if projections is not None:
        vis["projections"] = projections
    if title is not None:
        vis["objects"] = {
            "general": [
                {"properties": {"title": {"expr": {"Literal": {"Value": json.dumps(title)}}}}},
            ]
        }
    vis["layouts"] = [{"position": pos}]
    return json.dumps({
        "singleVisual": vis,
        "layout": {"x": pos["x"], "y": pos["y"], "width": pos["width"],
                   "height": pos["height"], "z": pos.get("z", 0)},
    })


def _proj(query_ref, role):
    return {"queryRef": query_ref, "projections": [role]}


def _pbix(sections):
    buf_entries = {"Report/Layout": json.dumps({"sections": sections})}
    import io
    import zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in buf_entries.items():
            zf.writestr(name, content)
    return b.getvalue()


def _page(sections_widgets):
    return {
        "displayName": "Sales",
        "name": "Sales",
        "layout": {"width": 1280, "height": 720},
        "visualContainers": sections_widgets,
    }


# ───────────────────────────── parser ────────────────────────────────


def test_projection_roles_extracted_as_fields():
    """Y/Category roles land on the widget as structured field hints."""
    vc = _vc_with_projections(
        "clusteredColumnChart", title="Revenue by region",
        projections=[_proj("Sales.Sum of Amount", "Y"), _proj("Sales.Region", "Category")],
        box={"x": 10, "y": 190, "width": 500, "height": 500},
    )
    spec = pbix_spec.pbix_to_spec(_pbix([_page([vc])]))
    w = spec["pages"][0]["widgets"][0]
    assert "fields" in w
    assert w["fields"] == [
        {"ref": "Sales.Sum of Amount", "role": "Y"},
        {"ref": "Sales.Region", "role": "Category"},
    ]


def test_projection_field_names_and_agg_split():
    """'Sales.Sum of Amount' → column 'Amount', agg 'SUM'; plain ref → no agg."""
    fields = pbix_spec._field_parts("Sales.Sum of Amount")
    assert fields == ("Amount", "SUM")

    fields2 = pbix_spec._field_parts("Sales.Region")
    assert fields2 == ("Region", None)

    fields3 = pbix_spec._field_parts("Sales.Average of Discount")
    assert fields3 == ("Discount", "AVG")


def test_projection_multiple_roles_first_wins_on_duplicate_refs():
    """A queryRef projected into two roles keeps its first role."""
    vc = _vc_with_projections(
        "tableEx", title="Detail",
        projections=[_proj("Sales.Product", "Values"), _proj("Sales.Product", "Rows")],
    )
    spec = pbix_spec.pbix_to_spec(_pbix([_page([vc])]))
    w = spec["pages"][0]["widgets"][0]
    assert w["fields"] == [{"ref": "Sales.Product", "role": "Values"}]


def test_widget_without_projections_has_empty_fields():
    vc = _vc_with_projections("textbox", text="hello")
    spec = pbix_spec.pbix_to_spec(_pbix([_page([vc])]))
    w = spec["pages"][0]["widgets"][0]
    assert w["fields"] == []


def test_aggregations_variant_prefixes():
    assert pbix_spec._field_parts("Sales.Count of Product")[1] == "COUNT"
    assert pbix_spec._field_parts("Sales.Avg of Price")[1] == "AVG"
    assert pbix_spec._field_parts("Sales.Count Distinct of ID")[1] == "COUNT DISTINCT"
    assert pbix_spec._field_parts("Sales.Min of Date")[1] == "MIN"
    assert pbix_spec._field_parts("Sales.Max of Date")[1] == "MAX"


# ───────────────────────── plan binding ──────────────────────────────


def _plan_widget(idx, family, title, fields=None):
    return {"index": idx, "family": family, "title": title, "x": 5, "y": 5,
            "w": 40, "h": 40, "content": None, "fields": fields or []}


def test_pick_column_uses_field_role_over_fuzzy_title():
    """A Y-role field beats the fuzzy title match when they disagree."""
    cols = ["region", "territory", "amount"]
    col = _pick_column(_plan_widget(0, "column", "Revenue by region", [
        {"ref": "Sales.Sum of Amount", "role": "Y"},
    ]), cols, role="measure")
    assert col == "amount"


def test_pick_column_falls_back_to_fuzzy_title():
    cols = ["region", "amount"]
    col = _pick_column(_plan_widget(0, "column", "Amount by region"), cols, role="measure")
    assert col == "amount"


def test_pick_column_fuzzy_when_field_missing_from_table():
    cols = ["region", "amount"]
    col = _pick_column(_plan_widget(0, "column", "Revenue by region", [
        {"ref": "Sales.Sum of Revenue", "role": "Y"},
    ]), cols, role="measure")
    assert col == "amount"


def test_pick_column_categorical_prefers_category_role():
    cols = ["region", "territory", "amount"]
    col = _pick_column(_plan_widget(0, "bar", "Sales by territory", [
        {"ref": "Sales.Region", "role": "Category"},
    ]), cols, role="dimension")
    assert col == "region"


def test_pick_column_slicer_uses_roleless_field():
    """Slicers project fields without roles — the ref alone binds."""
    cols = ["country", "amount"]
    col = _pick_column(_plan_widget(0, "slicer", "Country", [
        {"ref": "Sales.Country", "role": ""},
    ]), cols, role="dimension")
    assert col == "country"


# ─────────────────────── dashstudio glue under test ──────────────────

# Imported lazily inside each test on purpose: importing api.dashstudio pulls
# the whole app (api.org opens the warehouse at import time), so a lazy import
# isolates environmental lock failures to these tests instead of collection.


def _pick_column(widget, cols, role):
    from api import dashstudio as _ds
    return _ds._pick_pbix_column(widget, cols, role)
