/**
 * Canned Time Intelligence — client-side mirror of api/measures.py
 * build_time_intel_sql().  Builds the exact same SQL strings the backend
 * produces (data-anchored windows via the analytics.calendar dimension), so
 * widgets on the blank canvas can resolve measure-based KPIs without a
 * round-trip per mode change.
 *
 * Exposes window.BITimeIntel:
 *   BITimeIntel.build(expression, table, dateColumn, mode)
 *     -> { sql, spark_sql, mode, cur_label, prev_label }   ('none' => plain total)
 *   BITimeIntel.modes  — { none, ytd, mtd, qtd, mom } labels
 */
(function () {
  "use strict";

  var MODES = {
    none: { cur: "Value", prev: "" },
    ytd: { cur: "YTD", prev: "Prior YTD" },
    mtd: { cur: "MTD", prev: "Prior MTD" },
    qtd: { cur: "QTD", prev: "Prior QTD" },
    mom: { cur: "MTD", prev: "Prev MTD" },
  };

  function q(name) {
    return '"' + String(name).replace(/"/g, '""') + '"';
  }

  /** One-row CTE `a` with the data-anchored reference date (mirror of backend). */
  function anchorCte(tq, dcq) {
    return (
      "WITH a AS (" +
      "SELECT COALESCE(LEAST(CURRENT_DATE, MAX(TRY_CAST(t." + dcq + " AS DATE))), CURRENT_DATE) AS ref " +
      "FROM analytics." + tq + " t" +
      ")"
    );
  }

  function periodConditions(mode) {
    switch (mode) {
      case "ytd":
        return [
          "c.year = YEAR(a.ref) AND c.date <= a.ref",
          "c.year = YEAR(a.ref) - 1 AND c.date <= a.ref - INTERVAL 1 YEAR",
          "c.year = YEAR(a.ref) - 1",
        ];
      case "mtd":
        return [
          "c.year = YEAR(a.ref) AND c.month = MONTH(a.ref) AND c.date <= a.ref",
          "c.date >= DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH) AND c.date <= a.ref - INTERVAL 1 MONTH",
          "c.date >= DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH) AND c.date < DATE_TRUNC('month', a.ref)",
        ];
      case "qtd":
        // DuckDB INTERVAL 1 QUARTER is 3 months (earlier date, same year);
        // prior-year alignment must use INTERVAL 1 YEAR on the anchor —
        // keep in sync with api/measures.py _period_conditions.
        return [
          "c.date >= DATE_TRUNC('quarter', a.ref) AND c.date <= a.ref",
          "c.date >= DATE_TRUNC('quarter', a.ref - INTERVAL 1 YEAR) AND c.date <= a.ref - INTERVAL 1 YEAR",
          "c.date >= DATE_TRUNC('quarter', a.ref - INTERVAL 1 YEAR)"
          + " AND c.date < DATE_TRUNC('quarter', a.ref - INTERVAL 1 YEAR) + INTERVAL 3 MONTH",
        ];
      case "mom":
        return [
          "c.date >= DATE_TRUNC('month', a.ref) AND c.date <= a.ref",
          "c.date >= DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH) AND c.date <= a.ref - INTERVAL 1 MONTH",
          "c.date >= DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH) AND c.date < DATE_TRUNC('month', a.ref)",
        ];
      default:
        return ["", "", ""];
    }
  }

  /**
   * Build { sql, spark_sql, mode, cur_label, prev_label } for a measure.
   * mode: 'none' | 'ytd' | 'mtd' | 'qtd' | 'mom'
   * opts.where: optional raw WHERE fragment (table-column predicates) to
   *             inject inside every scalar subquery so cross-filtering a
   *             measure KPI filters the base data, not the outer row.
   */
  function build(expression, table, dateColumn, mode, opts) {
    opts = opts || {};
    mode = (mode || "none").toLowerCase();
    if (!MODES[mode]) mode = "none";
    var curLabel = MODES[mode].cur;
    var prevLabel = MODES[mode].prev;
    var tq = q(table);
    var dcq = q(dateColumn);
    var wf = (opts.where || "").toString().trim();
    if (wf && /^WHERE\b/i.test(wf)) wf = wf.replace(/^\s*WHERE\s+/i, "");

    if (!dateColumn || mode === "none") {
      var plainSql = "SELECT " + expression + " AS value, NULL AS previous, NULL AS comparison FROM analytics." + tq + (wf ? " WHERE " + wf : "");
      return {
        sql: plainSql,
        spark_sql: "",
        mode: "none",
        cur_label: curLabel,
        prev_label: prevLabel,
      };
    }

    var conds = periodConditions(mode);
    var cte = anchorCte(tq, dcq);
    var base = "FROM analytics." + tq + " t JOIN analytics.calendar c ON c.date = CAST(t." + dcq + " AS DATE) JOIN a ON TRUE";
    function sub(cond) { return "(SELECT " + expression + " " + base + " WHERE " + cond + (wf ? " AND " + wf : "") + ")"; }
    var sql =
      cte + " " +
      "SELECT " +
      sub(conds[0]) + " AS value, " +
      sub(conds[1]) + " AS previous, " +
      sub(conds[2]) + " AS comparison, " +
      "(SELECT strftime(MAX(CAST(t2." + dcq + " AS DATE)), '%Y-%m-%d') FROM analytics." + tq + " t2" + (wf ? " WHERE " + wf : "") + ") AS anchor";
    var sparkSql =
      cte + " " +
      "SELECT strftime(c.date, '%Y-%m') AS label, " + expression + " AS value " +
      base + " " +
      "WHERE c.date >= CAST(strftime(a.ref - INTERVAL 11 MONTH, '%Y-%m-01') AS DATE) " +
      "AND c.date <= a.ref" + (wf ? " AND " + wf : "") + " " +
      "GROUP BY label ORDER BY label";

    return { sql: sql, spark_sql: sparkSql, mode: mode, cur_label: curLabel, prev_label: prevLabel };
  }

  window.BITimeIntel = {
    build: build,
    modes: MODES,
  };
})();