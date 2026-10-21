/**
 * BIFilters — preview-only WHERE-clause builder + SQL injection for the
 * Power BI Studio (#bidashboard).
 *
 * Cross-filtering in the studio is done entirely on the client: when a slicer
 * (or a clicked chart slice) selects values, each widget's preview SQL is
 * re-run with a WHERE clause appended. This module builds the WHERE text and
 * splices it into the generated widget SQL. It is **preview-only** — filters
 * never reach the exporter.
 *
 *   window.BIFilters = {
 *     literal(v)                       -> SQL string literal
 *     dimFromSql(sql)                  -> the dimension column aliased `label`
 *     clausesFor(filters, slicers, table) -> "a" IN ('x','y') AND "b" >= '..'
 *     injectWhere(sql, where)          -> splice `WHERE (where)` after FROM
 *   }
 */
window.BIFilters = (function () {
  "use strict";

  /** Quote a value as a SQL string literal. */
  function literal(v) {
    var s = String(v == null ? "" : v).replace(/'/g, "''");
    return "'" + s + "'";
  }

  /**
   * Best-effort guess of a widget's dimension column: the column aliased
   * `label`, or the GROUP BY column. Mirrors the exporter's dimension lookup.
   */
  function dimFromSql(sql) {
    if (!sql) return "";
    var m = sql.match(/"([^"]+)"\s+AS\s+label\b/i) ||
            sql.match(/\b([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+label\b/i);
    if (m) return m[1];
    m = sql.match(/\bGROUP\s+BY\s+"([^"]+)"/i);
    if (m) return m[1];
    m = sql.match(/\bGROUP\s+BY\s+([A-Za-z_][A-Za-z0-9_]*)/i);
    if (m) return m[1];
    return "";
  }

  /**
   * Build the combined WHERE text for a widget whose SQL reads from `table`.
   *
   * @param filters  {slicerField: [values] | [{min, max}]}
   * @param slicers  the dashboard's layout.slicers array
   * @param table    the resolved widget source table ("" = unknown)
   */
  function clausesFor(filters, slicers, table) {
    if (!filters || !slicers || !slicers.length) return "";
    var wTable = String(table || "").toLowerCase();
    var parts = [];

    slicers.forEach(function (sl) {
      if (!sl || !sl.field) return;
      // Only filter widgets that read from the slicer's own table so a
      // "Country" slicer on table A never filters a widget on table B.
      var slTable = String(sl.table || "").toLowerCase();
      if (slTable && wTable && slTable !== wTable) return;

      var sel = filters[sl.field];
      if (!sel || (Array.isArray(sel) && sel.length === 0)) return;

      var fq = '"' + sl.field + '"';
      var kind = sl.kind || "select";
      if (kind === "date" || kind === "range") {
        var rng = Array.isArray(sel) ? sel[0] : sel;
        rng = rng && typeof rng === "object" ? rng : {};
        var mn = rng.min, mx = rng.max;
        if (mn !== undefined && mn !== null && mn !== "") parts.push(fq + " >= " + literal(mn));
        if (mx !== undefined && mx !== null && mx !== "") parts.push(fq + " <= " + literal(mx));
      } else {
        var vals = sel.map(literal).join(",");
        parts.push(fq + " IN (" + vals + ")");
      }
    });

    return parts.join(" AND ");
  }

  /**
   * Splice `WHERE (<where>)` into a generated widget SQL right after the first
   * top-level `FROM <table>`. Handles a pre-existing WHERE (merges as AND),
   * GROUP BY / ORDER BY / LIMIT, and bare SELECT statements. If the SQL has no
   * FROM (e.g. a scalar) it is returned untouched.
   */
  function injectWhere(sql, where) {
    if (!where) return sql;
    if (!sql) return sql;

    var from = sql.match(/\bFROM\s+(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))/i);
    if (!from) return sql; // no table to attach the filter to
    var afterFrom = from.index + from[0].length;
    var rest = sql.slice(afterFrom);

    // Existing WHERE -> merge as AND.
    var ew = rest.match(/^\s*WHERE\b/i);
    if (ew) {
      var insEnd = afterFrom + ew.index + ew[0].length;
      return sql.slice(0, insEnd) + " (" + where + ") AND" + rest.slice(ew[0].length);
    }

    // Insert before the next clause keyword.
    var nxt = rest.match(/\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|OFFSET)\b/i);
    if (nxt) {
      var pos = afterFrom + nxt.index;
      return sql.slice(0, pos) + "WHERE (" + where + ") " + sql.slice(pos);
    }

    return sql.slice(0, afterFrom) + " WHERE (" + where + ") " + rest;
  }

  return {
    literal: literal,
    dimFromSql: dimFromSql,
    clausesFor: clausesFor,
    injectWhere: injectWhere,
  };
})();
