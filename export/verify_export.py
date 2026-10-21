"""
verify_export.py — structural validator for generated Power BI (.pbit) files.
================================================================================

Checks a generated ``.pbit`` against the structure Power BI Desktop actually
writes (captured from the authentic samples in ``powerbi sample data/`` into
``ref_visual_structure.json``). The primary goal is catching the "opens with no
data" class of bugs — most importantly a visualContainer missing its ``query``
and ``dataTransforms`` fields.

Usage:
    python -m export.verify_export v1.pbit
    python -m export.verify_export --dashboard <id> --out v1.pbit   # build then verify
    python -m export.verify_export --dashboard <id> --all           # verify every saved dashboard

Exit code is 0 when all checks pass, 1 otherwise.
"""

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Project root (this file lives in <root>/export/).
ROOT = Path(__file__).resolve().parent.parent
REF_PATH = Path(__file__).resolve().parent / "ref_visual_structure.json"


class Result:
    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.counts: Dict[str, int] = {}

    def ok(self, msg: str) -> None:
        print(f"  [ OK ] {msg}")

    def fail(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"  [FAIL] {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  [WARN] {msg}")


def _read_layout(pbit_path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (report_layout, model) decoded from the .pbit zip."""
    with zipfile.ZipFile(pbit_path) as z:
        names = z.namelist()
        model_raw = z.read("DataModelSchema")
        layout_raw = z.read("Report/Layout")
        try:
            model = json.loads(model_raw.decode("utf-16-le"))
        except Exception:
            raise RuntimeError("DataModelSchema is not UTF-16LE JSON")
        try:
            layout = json.loads(layout_raw.decode("utf-16-le"))
        except Exception:
            raise RuntimeError("Report/Layout is not UTF-16LE JSON")
    return layout, model


def _get_vc_name(vc: Dict[str, Any]) -> str:
    try:
        cfg = json.loads(vc.get("config", "{}"))
        return cfg.get("name", "?")
    except Exception:
        return "?"


def _get_vc_visual_type(vc: Dict[str, Any]) -> str:
    try:
        cfg = json.loads(vc.get("config", "{}"))
        return (cfg.get("singleVisual") or {}).get("visualType", "")
    except Exception:
        return ""


def verify_zip_structure(pbit_path: str, r: Result) -> None:
    """Required zip parts + correct JSON encodings."""
    required_base = {
        "Version", "[Content_Types].xml", "Settings", "Metadata",
        "DiagramLayout", "DiagramState", "SecurityBindings", "Report/Layout",
        "DataModelSchema",
    }
    required = required_base.copy()
    with zipfile.ZipFile(pbit_path) as z:
        names = set(z.namelist())
        theme_present = any(
            n.startswith("Report/StaticResources/SharedResources/BaseThemes/") and n.endswith(".json")
            for n in names
        )
        if not theme_present:
            required.add("Report/StaticResources/SharedResources/BaseThemes/CY24SU02.json")
    with zipfile.ZipFile(pbit_path) as z:
        names = set(z.namelist())
        missing = required - names
        if missing:
            for m in sorted(missing):
                r.fail(f"missing zip part: {m}")
        else:
            r.ok(f"all {len(required)} required zip parts present")
        if "DataMashup" in names:
            dm = z.read("DataMashup")
            # QDEFF layout: [Version=0 (4B)][len(packageParts) (4B)][inner mashup ZIP]...
            if dm[8:12] != b"PK\x03\x04":
                r.fail("DataMashup does not start with the inner mashup ZIP magic after the QDEFF header")
            elif len(dm) < 200:
                r.fail("DataMashup part is suspiciously small")
            else:
                r.ok(f"DataMashup part present ({len(dm):,} bytes, inner ZIP magic OK)")
        if "Version" in names:
            version = z.read("Version").decode("utf-16-le").strip()
            if version.startswith("﻿"):
                r.fail("Version has a BOM (must be BOM-free UTF-16LE)")
            else:
                r.ok(f"Version = {version!r}")
        # BOM check on the JSON parts (missing ones were reported above already).
        for part in ("Settings", "Metadata", "DiagramLayout", "Report/Layout", "DataModelSchema"):
            if part not in names:
                continue
            raw = z.read(part)
            if raw[:2] == b"\xff\xfe":
                r.fail(f"{part} starts with a UTF-16 BOM (must be BOM-free UTF-16LE)")
            else:
                r.ok(f"{part} is BOM-free UTF-16LE")


def verify_model(model: Dict[str, Any], r: Result) -> Dict[str, Any]:
    """Model tables/columns/relationships/measures sanity."""
    tables = (model.get("model") or {}).get("tables") or []
    r.counts["tables"] = len(tables)
    r.ok(f"{len(tables)} model tables")
    if not tables:
        r.fail("model has no tables")
        return {}
    table_by_name = {t.get("name"): t for t in tables}
    for t in tables:
        tname = t.get("name", "?")
        parts = t.get("partitions") or []
        if not parts:
            r.fail(f"table {tname!r} has no partitions")
            continue
        src_types = {(p.get("source") or {}).get("type") for p in parts}
        if not (src_types & {"calculated", "m"}):
            r.fail(f"table {tname!r} has no data partition (need 'calculated' or 'm')")
        for p in parts:
            src = p.get("source") or {}
            stype = src.get("type")
            expr = src.get("expression", "")
            if stype == "calculated":
                if not expr.lstrip().startswith("DATATABLE("):
                    r.fail(f"table {tname!r} calculated partition is not a DATATABLE expression")
                elif len(expr) < 30:
                    r.fail(f"table {tname!r} has a suspiciously short calculated partition")
            elif stype == "m":
                if not expr.lstrip().startswith("let"):
                    r.fail(f"table {tname!r} M partition is not a 'let ...' expression")
                elif "#table" not in expr:
                    r.fail(f"table {tname!r} M partition has no #table literal")
                elif len(expr) < 40:
                    r.fail(f"table {tname!r} has a suspiciously short M partition")
        for col in t.get("columns") or []:
            if not col.get("name"):
                r.fail(f"table {tname!r} has a column without a name")
        if not any(c.get("type") == "rowNumber" for c in (t.get("columns") or [])):
            r.fail(f"table {tname!r} has no hidden rowNumber key column")
        for m in t.get("measures") or []:
            if not m.get("expression"):
                r.fail(f"table {tname!r} measure {m.get('name')!r} has empty expression")

    rels = (model.get("model") or {}).get("relationships") or []
    r.counts["relationships"] = len(rels)
    r.ok(f"{len(rels)} relationships")
    for rel in rels:
        ft, fc = rel.get("fromTable"), rel.get("fromColumn")
        tt, tc = rel.get("toTable"), rel.get("toColumn")
        if rel.get("fromCardinality") != "many":
            r.fail(f"relationship {rel.get('name')!r}: fromCardinality must be 'many'")
        if rel.get("toCardinality") != "one":
            r.fail(f"relationship {rel.get('name')!r}: toCardinality must be 'one'")
        if ft not in table_by_name:
            r.fail(f"relationship {rel.get('name')!r}: fromTable {ft!r} missing")
        if tt not in table_by_name:
            r.fail(f"relationship {rel.get('name')!r}: toTable {tt!r} missing")
        for tbl, col in ((ft, fc), (tt, tc)):
            if tbl in table_by_name:
                colnames = {c.get("name") for c in table_by_name[tbl].get("columns") or []}
                if col not in colnames:
                    r.fail(f"relationship {rel.get('name')!r}: column {tbl}.{col!r} missing")
    return table_by_name


def verify_report(layout: Dict[str, Any], table_by_name: Dict[str, Any], r: Result) -> None:
    """Every data visual must carry query + dataTransforms; refs must resolve."""
    sections = layout.get("sections") or []
    if not sections:
        r.fail("report has no sections")
        return
    vcs = sections[0].get("visualContainers") or []
    r.counts["visuals"] = len(vcs)
    r.ok(f"{len(vcs)} visualContainers")
    if not vcs:
        return

    data_vc = 0
    missing_query = []
    for vc in vcs:
        vt = _get_vc_visual_type(vc)
        if vt == "textbox":
            continue
        data_vc += 1
        if "query" not in vc or "dataTransforms" not in vc:
            missing_query.append(f"{vt}:{_get_vc_name(vc)}")
            continue
        # ---- resolve every From entity ----
        try:
            q = json.loads(vc["query"])
            dt = json.loads(vc["dataTransforms"])
        except Exception as e:
            r.fail(f"visual {_get_vc_name(vc)} query/dataTransforms not JSON: {e}")
            continue
        for cmd in q.get("Commands", []):
            sq = cmd.get("SemanticQueryDataShapeCommand", {})
            pq = sq.get("Query", {})
            for f in pq.get("From") or []:
                if f.get("Entity") not in table_by_name:
                    r.fail(f"visual {_get_vc_name(vc)}: From entity {f.get('Entity')!r} not in model")
            # ---- projection queryRefs must exist among Select names ----
            select_names = [s.get("Name") for s in (pq.get("Select") or [])]
            cfg = json.loads(vc["config"])
            projs = (cfg.get("singleVisual") or {}).get("projections") or {}
            for role, refs in projs.items():
                refs_list = refs if isinstance(refs, list) else [refs]
                for ref in refs_list:
                    if isinstance(ref, dict) and ref.get("queryRef") not in select_names:
                        r.fail(f"visual {_get_vc_name(vc)}: projection {role}.{ref.get('queryRef')!r} not in Select")
            # ---- Binding indices in range ----
            for g in (sq.get("Binding") or {}).get("Primary", {}).get("Groupings", []):
                for i in g.get("Projections", []):
                    if not (isinstance(i, int) and 0 <= i < len(select_names)):
                        r.fail(f"visual {_get_vc_name(vc)}: Binding projection index {i} out of range")
            # ---- dataTransforms internal consistency ----
            meta_names = [s.get("Name") for s in (dt.get("queryMetadata") or {}).get("Select", [])]
            if meta_names and set(meta_names) != set(select_names):
                missing_in_meta = set(select_names) - set(meta_names)
                if missing_in_meta:
                    r.fail(f"visual {_get_vc_name(vc)}: queryMetadata missing selects {sorted(missing_in_meta)}")
    if missing_query:
        r.fail(f"{len(missing_query)} data visuals are missing query/dataTransforms: {missing_query}")
    else:
        r.ok(f"all {data_vc} data visuals carry query + dataTransforms")

    if data_vc == 0:
        r.fail("no data visuals in report")


def verify_role_bindings(layout: Dict[str, Any], r: Result) -> None:
    """Check per-select role bindings match the real-PBI invariants.

    This is the guard for "charts render empty": each dataTransforms select must
    fill exactly the role(s) owning its projection index, DataRoles must mirror
    projectionOrdering, aggregates must carry Function, cards format, and chart
    queries OrderBy + Binding.Aggregates.
    """
    sections = layout.get("sections") or []
    vcs = sections[0].get("visualContainers") or [] if sections else []
    checked = 0
    for vc in vcs:
        vt = _get_vc_visual_type(vc)
        if vt == "textbox" or "dataTransforms" not in vc:
            continue
        checked += 1
        name = _get_vc_name(vc)
        try:
            dt = json.loads(vc["dataTransforms"])
            q = json.loads(vc["query"])
        except Exception as e:
            r.fail(f"visual {name}: query/dataTransforms not JSON: {e}")
            continue

        ordering = dt.get("projectionOrdering") or {}
        selects = dt.get("selects") or []

        # 1) each select fills exactly its owning role(s)
        for i, sel in enumerate(selects):
            owning = {role for role, idxs in ordering.items() if i in idxs}
            if not owning:
                r.fail(f"{vt} {name}: select {i} not owned by any role")
                continue
            for role in ordering:
                if bool(sel.get("roles", {}).get(role)) != (role in owning):
                    r.fail(
                        f"{vt} {name}: select {i} ({sel.get('queryName')}) role {role} = "
                        f"{sel.get('roles', {}).get(role)}, expected {role in owning}")

        # 2) DataRoles mirror ordering; isActive only on category-like roles
        for ve in dt.get("visualElements") or []:
            for dr in ve.get("DataRoles") or []:
                if dr.get("Projection") not in ordering.get(dr.get("Name"), []):
                    r.fail(f"{vt} {name}: DataRole {dr.get('Name')}:{dr.get('Projection')} not in ordering")
                if bool(dr.get("isActive")) != (dr.get("Name") in ("Category", "X", "Group")):
                    # "Group" is treemap's category role and is active in real
                    # treemap files (verified from powerbi sample data/).
                    r.fail(f"{vt} {name}: DataRole {dr.get('Name')} isActive {dr.get('isActive')} unexpected")

        # 3) aggregate selects carry Function; cards carry format '0'
        for sel in selects:
            agg = (sel.get("expr") or {}).get("Aggregation")
            measure = (sel.get("expr") or {}).get("Measure")
            if agg and "Function" not in agg:
                r.fail(f"{vt} {name}: aggregate select {sel.get('queryName')} missing Function")
            # Cards must have either an Aggregation or a Measure (DAX) select —
            # plain Column selects render empty for card visuals.
            if vt in ("cardVisual", "card", "multiRowCard"):
                if agg is None and measure is None:
                    r.fail(f"{vt} {name}: card expected an aggregate or measure select")
                elif agg is not None and sel.get("format") != "0":
                    r.fail(f"{vt} {name}: card value select missing format '0'")

        # 4) category-like selects carry sort
        for sel in selects:
            if not (sel.get("expr") or {}).get("Aggregation") and sel.get("roles", {}).get("Category"):
                if sel.get("sort") != 1 or sel.get("sortOrder") != 0:
                    r.fail(f"{vt} {name}: category select {sel.get('queryName')} missing sort/sortOrder")

        # 5) chart queries carry OrderBy (with category) + Binding.Aggregates
        for cmd in q.get("Commands", []):
            sq = cmd.get("SemanticQueryDataShapeCommand", {})
            query = sq.get("Query") or {}
            binding = sq.get("Binding") or {}
            has_agg = any("Aggregation" in s for s in query.get("Select") or [])
            if has_agg and not binding.get("Aggregates"):
                r.fail(f"{vt} {name}: missing Binding.Aggregates")
            if has_agg and "Category" in ordering and not query.get("OrderBy"):
                r.fail(f"{vt} {name}: chart missing OrderBy")

    r.ok(f"role bindings checked on {checked} data visuals")


def verify_against_reference(layout: Dict[str, Any], r: Result) -> None:
    """Compare the emitted query/dataTransforms shapes against the real PBI golden reference."""
    if not REF_PATH.exists():
        r.warn("golden reference ref_visual_structure.json not found — skipping shape comparison")
        return
    try:
        ref = json.loads(REF_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        r.warn(f"could not read golden reference: {e}")
        return

    ref_data_keys = set(ref.get("visualContainer_keys_data_visual", []))
    sections = layout.get("sections") or []
    vcs = sections[0].get("visualContainers") or [] if sections else []

    sampled = {}
    for vc in vcs:
        vt = _get_vc_visual_type(vc)
        if "query" not in vc:
            continue
        if vt == "slicer" and "slicer" not in sampled:
            sampled["slicer"] = vc
        elif vt not in ("slicer", "textbox") and "chart" not in sampled:
            sampled["chart"] = vc
    for kind, vc in sampled.items():
        if not set(vc.keys()).issuperset(ref_data_keys):
            r.fail(f"{kind} visual missing reference keys {sorted(ref_data_keys - set(vc.keys()))}")
    if "chart" in sampled:
        vc = sampled["chart"]
        try:
            dt = json.loads(vc["dataTransforms"])
            meta = (dt.get("queryMetadata") or {}).get("Select", [])
            ref_meta = (ref.get("dataTransforms") or {}).get("queryMetadata_Select", [])
            ref_types = {s.get("Type") for s in ref_meta}
            our_types = {s.get("Type") for s in meta}
            if ref_types and not our_types.issubset(ref_types):
                r.fail(f"dataTransforms Select Type codes {sorted(our_types)} not in reference {sorted(ref_types)}")
            else:
                r.ok(f"dataTransforms Select Type codes match reference ({sorted(ref_types)})")
        except Exception as e:
            r.fail(f"could not compare dataTransforms shape: {e}")


def build_and_verify(dashboard_id: str, out_path: str) -> None:
    """Build a .pbit for a dashboard id via export.pbix_export, then verify it."""
    from export import pbix_export  # noqa: F401  (imports run under -m)

    sys.path.insert(0, str(ROOT))
    import duckdb
    from config import settings
    from api.dashboards import get_dashboard_manager

    con = duckdb.connect(str(settings.duckdb.database_path))
    con.execute(f"SET search_path = '{settings.duckdb.schema}'")

    def run_query(sql: str):
        return con.execute(sql).df()

    def load_table(table: str, limit: int):
        return con.execute(f'SELECT * FROM "{table}" LIMIT {max(1, int(limit))}').df()

    mgr = get_dashboard_manager()
    dash = mgr.get(dashboard_id)
    if not dash:
        print(f"dashboard {dashboard_id!r} not found")
        sys.exit(1)
    print(f"building .pbit for dashboard: {dash.get('name')} -> {out_path}")
    data = pbix_export.build_powerbi_pbit(dash, run_query, load_table)
    Path(out_path).write_bytes(data)
    print(f"wrote {out_path} ({len(data):,} bytes)\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate a generated .pbit file")
    ap.add_argument("pbit", nargs="?", help="path to an existing .pbit file")
    ap.add_argument("--dashboard", help="saved dashboard id to build+verify")
    ap.add_argument("--out", default="verified.pbit", help="output path for --dashboard")
    ap.add_argument("--all", action="store_true", help="verify every saved dashboard")
    args = ap.parse_args()

    if not args.pbit and not args.dashboard and not args.all:
        ap.error("provide a .pbit path, --dashboard <id>, or --all")

    if args.dashboard:
        build_and_verify(args.dashboard, args.out)
        args.pbit = args.out

    targets = [args.pbit]
    if args.all:
        import duckdb
        from config import settings
        con = duckdb.connect(str(settings.duckdb.database_path))
        rows = con.execute("SELECT id, widgets FROM analytics.dashboards").fetchall()
        targets = []
        for i, (dash_id, widgets_json) in enumerate(rows):
            try:
                wcnt = len(json.loads(widgets_json)) if widgets_json else 0
            except Exception:
                wcnt = 0
            if wcnt == 0:
                print(f"skipping empty dashboard {dash_id} (no widgets)")
                continue
            out = f"verify_{i}.pbit"
            build_and_verify(dash_id, out)
            targets.append(out)

    overall = True
    for pbit in targets:
        if not pbit or not os.path.exists(pbit):
            print(f"file not found: {pbit}")
            continue
        print(f"\n=== verifying {pbit} ===")
        r = Result()
        try:
            layout, model = _read_layout(pbit)
            verify_zip_structure(pbit, r)
            table_by_name = verify_model(model, r)
            verify_report(layout, table_by_name, r)
            verify_role_bindings(layout, r)
            verify_against_reference(layout, r)
        except Exception as e:
            r.fail(f"unexpected error: {e}")
        print(f"\n--- {pbit}: {len(r.errors)} error(s), {len(r.warnings)} warning(s) ---")
        if r.errors:
            overall = False
        print()
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
