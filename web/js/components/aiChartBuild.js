/**
 * AIChartBuild — shared helpers to turn LLM chart/insight recommendations into
 * blank-dashboard widget objects. Used by Dashboard Studio (AI Auto-Build) and
 * the blank dashboard editor (AI Generate Charts).
 *
 * Relies on the global `API` object (web/js/api.js) for the LLM endpoints and
 * the widget schema used by blankdash.js.
 */
(function () {
  "use strict";

  // Map backend chart_type -> widget `type` + config.chartType.
  // Custom-render types (histogram, boxplot) use their own top-level type.
  function _mapType(chartType) {
    var t = String(chartType || "bar").toLowerCase();
    if (t === "histogram") return { wtype: "histogram", chartType: "histogram" };
    if (t === "box" || t === "boxplot") return { wtype: "boxplot", chartType: "box" };
    if (t === "scatter") return { wtype: "chart", chartType: "scatter" };
    if (t === "heatmap") return { wtype: "chart", chartType: "heatmap" };
    if (t === "doughnut") return { wtype: "chart", chartType: "doughnut" };
    if (t === "line") return { wtype: "chart", chartType: "line" };
    if (t === "pie") return { wtype: "chart", chartType: "pie" };
    if (t === "area") return { wtype: "chart", chartType: "area" };
    // default bar
    return { wtype: "chart", chartType: "bar" };
  }

  function _q(v) {
    return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
  }

  /* Quote a column ref. Dotted refs ("rel.col") become "rel"."col". */
  function _ref(col) {
    var s = String(col == null ? "" : col).trim();
    if (!s) return '""';
    var i = s.indexOf(".");
    if (i > 0) return _q(s.slice(0, i)) + "." + _q(s.slice(i + 1));
    return _q(s);
  }

  /* ════════════════════════════════════════════════════════════════════════
   * Power BI typography recipe (measured from powerbi sample data/ — see
   * pbix_analysis/TYPOGRAPHY_INSIGHTS_REPORT.md §8). Applied ONLY to widgets
   * that AI auto-buils (header banner, insight panel, KPI cards, page
   * navigator) so manually-authored dashboards stay untouched.
   *
   *   label  < title  < value/callout   (10pt < 12pt < 20-45pt ladder)
   *   Font stack  → Segoe UI → system-ui → Helvetica/Arial (never serif)
   *   Dark readable ink  → #1C2D3E
   *   Page title  → 18-36pt (most common 18pt) / bold 800
   *   KPI value  → 20-25px / 800 ; label 11px semibold uppercase
   *   Insight body → 14px / line-height 1.6 ; micro-slugs uppercase 10px/800
   */
  const _PBI_FONT_STACK = "'Segoe UI','Segoe UI Variable',system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif";
  const _PBI_INK = "#1c2d3e";
  const _PBI_MUTED = "#64748b";
  const _PBI_ACCENT = "#6366f1";

  function _fontStackCss(extra) {
    return "font-family:" + _PBI_FONT_STACK + ";" + (extra || "");
  }

  function _hexRgb(hex) {
    var c = String(hex || "").replace("#", "");
    if (c.length === 3) c = c.split("").map(function (ch) { return ch + ch; }).join("");
    return [parseInt(c.substring(0, 2), 16) || 0, parseInt(c.substring(2, 4), 16) || 0, parseInt(c.substring(4, 6), 16) || 0];
  }
  function _lighten(hex, amt) {
    var p = _hexRgb(hex);
    return "rgb(" + Math.round(p[0] + (255 - p[0]) * amt) + "," + Math.round(p[1] + (255 - p[1]) * amt) + "," + Math.round(p[2] + (255 - p[2]) * amt) + ")";
  }
  function _isDarkHex(hex) {
    var p = _hexRgb(hex);
    return (0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]) < 140;
  }

  /* LEFT JOIN clause for aiChartBuild-supported joins. */
  function _joinClause(base, joins) {
    if (!joins) return "";
    var out = "";
    Object.keys(joins).forEach(function (rel) {
      var k = joins[rel];
      if (!k) return;
      out += " LEFT JOIN " + _q(rel) + " ON " + _q(base) + "." + _q(k) + " = " + _q(rel) + "." + _q(k);
    });
    return out;
  }

  /**
   * Build DuckDB SQL for a recommendation. Returns "" for unknown unsupported
   * combos so the caller can skip that chart.
   * opts: { joins: {relatedTable: joinKey} } to LEFT JOIN related tables; base
   * columns are referenced unprefixed, related columns as "rel"."col".
   */
  function buildChartSql(chartType, table, x, y, color, opts) {
    opts = opts || {};
    var t = String(chartType || "bar").toLowerCase();
    var xc = x || (y ? y : null);
    var yc = y || null;
    var cc = color || null;
    var tq = _q(table);
    var jn = _joinClause(table, opts.joins);
    if (!xc) return "";
    // histogram: single numeric column
    if (t === "histogram") return "SELECT " + _ref(xc) + " AS value FROM " + tq + jn;
    // box / boxplot: label + value
    if (t === "box" || t === "boxplot") {
      if (!yc) return "";
      return "SELECT " + _ref(xc) + " AS label, " + _ref(yc) + " AS value FROM " + tq + jn + " LIMIT 500";
    }
    // scatter: two numeric columns
    if (t === "scatter") {
      if (!yc) return "";
      return "SELECT " + _ref(xc) + " AS x, " + _ref(yc) + " AS y FROM " + tq + jn + " LIMIT 300";
    }
    // heatmap: label + category + count
    if (t === "heatmap") {
      var cat = cc || yc;
      if (!cat) return "";
      return "SELECT " + _ref(xc) + " AS label, " + _ref(cat) + " AS category, COUNT(*) AS value FROM " + tq + jn +
        " GROUP BY " + _ref(xc) + ", " + _ref(cat) + " LIMIT 100";
    }
    // pie / doughnut: single-series proportion breakdown. When no value measure
    // is provided, fall back to a row count so the share chart still builds
    // (the LLM is told to leave y_axis null for pie/histogram).
    if (t === "pie" || t === "doughnut") {
      var aggShare = yc ? ("SUM(" + _ref(yc) + ")") : "COUNT(*)";
      return "SELECT " + _ref(xc) + " AS label, " + aggShare + " AS value FROM " + tq + jn +
        " GROUP BY " + _ref(xc) + " ORDER BY value DESC LIMIT 12";
    }
    // bar / line / area: label + aggregated value. A color/series column is
    // carried through as `series` so the client can pivot into grouped
    // datasets (grouped bar, multi-line/area).
    var series = "";
    var groupCols = _ref(xc);
    var agg = yc ? ("SUM(" + _ref(yc) + ")") : "COUNT(*)";
    if (cc) {
      series = ", " + _ref(cc) + " AS series";
      groupCols += ", " + _ref(cc);
    }
    // Time series over a date column must stay chronological, not value-sorted.
    var order = ((t === "line" || t === "area") && opts.timeX) ? "ORDER BY " + _ref(xc) + " ASC" : "ORDER BY value DESC";
    return "SELECT " + _ref(xc) + " AS label" + series + ", " + agg + " AS value FROM " + tq + jn +
      " GROUP BY " + groupCols + " " + order + " LIMIT 24";
  }

  /**
   * Build a single chart widget object from one `/llm/chart-recommend` entry,
   * with explicit pixel position/size.
   */
  function chartWidgetFromRec(rec, table, opts) {
    opts = opts || {};
    var recType = rec.chart_type || rec.chartType || "bar";
    var mapped = _mapType(recType);
    var x = rec.x_axis || rec.xAxis || "";
    var y = rec.y_axis || rec.yAxis || "";
    var color = rec.color || rec.group_col || rec.series || "";
    // A "series" column that is the same as the x-axis or the value adds no
    // grouping — drop it so we never render a misleading "by X" chart.
    if (color === x || color === y) color = "";
    var title = rec.title || rec.label || (String(recType) + " chart");
    var joins = {};
    if (opts.joins && typeof opts.joins === "object") {
      Object.keys(opts.joins).forEach(function (k) { joins[k] = opts.joins[k]; });
    }
    if (rec.join_keys && typeof rec.join_keys === "object") {
      var joinKeys = rec.join_keys;
      Object.keys(joinKeys).forEach(function (rel) {
        if (rel === table || !joinKeys[rel]) return;
        joins[rel] = joinKeys[rel];
      });
    }
    // Detect whether x is a date/time column so trend charts order chronologically.
    var timeX = false;
    try {
      var cls = classifyColumns(table);
      if (x === cls.date || (cls.dates || []).indexOf(x) !== -1) timeX = true;
    } catch (e) { /* column metadata unavailable */ }
    var sql = (rec.sql || "").trim() || buildChartSql(recType, table, x, y, color, { joins: joins, timeX: timeX });
    if (!sql) return null;

    var width = opts.width || 620;
    var height = opts.height || 300;
    var wConfig = {
      table: table,
      sql: sql,
      chartType: mapped.chartType,
      xCol: x || "label",
      yCol: y || "value",
      label: title,
      filterField: title,
      agg: "SUM",
      limit: 12
    };
    // Only bar/line/area actually render a color/series split; pie & doughnut
    // are single-series, so never tag them with a stale series column.
    if (color && mapped.wtype === "chart" && ["bar", "line", "area"].indexOf(mapped.chartType) !== -1) {
      wConfig.seriesCol = color;
    }
    if (Object.keys(joins).length) { wConfig.tables = [table].concat(Object.keys(joins)); wConfig.joins = joins; }
    if (Array.isArray(opts.colors) && opts.colors.length) wConfig.colors = opts.colors.slice();
    return {
      id: "w" + Date.now().toString(36) + "_" + (opts.index || 0),
      type: mapped.wtype,
      title: title,
      x: opts.x || 16,
      y: opts.y || 16,
      width: width,
      height: height,
      zIndex: (opts.index || 0) + 1,
      locked: false,
      plain: false,
      config: wConfig
    };
  }

  /**
   * Lay widgets out on a grid left->right, top->bottom, `cols` per row.
   * Mutates and returns the array.
   */
  function layoutWidgets(widgets, cols, gap, originX, originY) {
    cols = cols || 2;
    gap = gap || 24;
    originX = originX || 24;
    originY = originY || 24;
    widgets.forEach(function (w, i) {
      var col = i % cols;
      var row = Math.floor(i / cols);
      w.x = originX + col * (w.width + gap);
      w.y = originY + row * (w.height + gap);
    });
    return widgets;
  }

  /**
   * Build the narrative insight text widget from the `/analysis/insights`
   * response.
   */
  function insightString(insights) {
    insights = insights || {};
    var parts = [];
    if (insights.summary) parts.push("SUMMARY\n" + insights.summary);
    var find = insights.key_findings || [];
    if (find.length) parts.push("KEY FINDINGS\n" + find.map(function (f) { return "- " + f; }).join("\n"));
    var corr = insights.correlation_highlights || [];
    if (corr.length) parts.push("CORRELATIONS\n" + corr.map(function (c) { return "- " + c; }).join("\n"));
    var recs = insights.recommendations || [];
    if (recs.length) parts.push("RECOMMENDATIONS\n" + recs.map(function (r) { return "- " + r; }).join("\n"));
    return parts.join("\n\n");
  }

  function insightTextWidget(table, insights, opts) {
    opts = opts || {};
    var text = "Source: " + _insightSourceLabel(insights) + "\n\n" + (insightString(insights) || "No narrative insights available for this table yet.");
    return {
      id: "w" + Date.now().toString(36) + "_ins",
      type: "text",
      title: opts.title || "AI Insights",
      x: opts.x || 16,
      y: opts.y || 16,
      width: opts.width || 620,
      height: opts.height || 260,
      zIndex: (opts.index || 0) + 1,
      locked: false,
      plain: true,
      config: {
        table: table,
        content: text,
        label: opts.title || "AI Insights",
        plain: true,
        textFormat: "markdown"
      }
    };
  }

  /* Lightweight, XSS-safe markdown → HTML for AI narrative text. Everything is
     HTML-escaped first, then only a whitelist of markdown patterns is applied:
     code fences, inline code, links, bold, italic, headings, bullet + numbered
     lists, paragraph gaps and line breaks. Unknown syntax stays inert text. */
  function mdToHtml(src) {
    if (!src) return "";
    var t = String(src).replace(/\r\n?/g, "\n");
    var esc = function (s) {
      var d = document.createElement("div");
      d.appendChild(document.createTextNode(String(s == null ? "" : s)));
      return d.innerHTML;
    };
    var fences = [];
    t = t.replace(/```(\w*)[\r\n]*([\s\S]*?)```/g, function (m, lang, code) {
      fences.push('<pre class="bd-md-code-fence">' + esc(code.replace(/[\r\n]+$/, "")) + "</pre>");
      return "\u0000F" + (fences.length - 1) + "\u0000";
    });
    t = esc(t);
    t = t.replace(/`([^`]+)`/g, '<code class="bd-md-code">$1</code>');
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function (m, txt, url) {
      return '<a class="bd-md-link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + txt + "</a>";
    });
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    t = t.replace(/_([^_\n]+)_/g, "<em>$1</em>");
    t = t.replace(/^###\s+(.+)$/gm, '<div class="bd-md-h3">$1</div>');
    t = t.replace(/^##\s+(.+)$/gm, '<div class="bd-md-h2">$1</div>');
    t = t.replace(/^#\s+(.+)$/gm, '<div class="bd-md-h1">$1</div>');
    /* Lists. Items may be separated by a single blank line (common in AI-generated
       markdown) — the regex allows \n{1,2} between items so they group into ONE
       <ul>/<ol> instead of numbering afresh ("1. 1. 1."). At least two items are
       required so a lone "3. …" line stays literal text. */
    t = t.replace(/(^|\n)((?:[-*•] [^\n]+(?:\n{1,2}(?=[-*•] )[^\n]+)+))/g, function (m, pre, block) {
      var items = block.split(/\n/).filter(function (l) { return l.trim() !== ""; }).map(function (ln) { return ln.replace(/^[-*•] /, ""); });
      return (pre || "") + '<ul class="bd-md-ul">' + items.map(function (it) { return "<li>" + it + "</li>"; }).join("") + "</ul>";
    });
    t = t.replace(/(^|\n)((?:\d+[.)] [^\n]+(?:\n{1,2}(?=\d+[.)] )[^\n]+)+))/g, function (m, pre, block) {
      var items = block.split(/\n/).filter(function (l) { return l.trim() !== ""; }).map(function (ln) { return ln.replace(/^\d+[.)] /, ""); });
      return (pre || "") + '<ol class="bd-md-ol">' + items.map(function (it) { return "<li>" + it + "</li>"; }).join("") + "</ol>";
    });
    t = t.replace(/\n{2,}/g, '<div class="bd-md-gap"></div>');
    t = t.replace(/\n/g, "<br>");
    t = t.replace(/\u0000F(\d+)\u0000/g, function (m, i) { return fences[Number(i)] || ""; });
    return t;
  }

  function _insightSourceLabel(insights) {
    return (insights && insights.source === "llm") ? "LLM-generated" : "Automatic (rule-based)";
  }

  /* Drop near-duplicate recommendations that would render as the same visual:
     same chart type + same x axis + same y measure. Series color is ignored so
     "by Team" repeats of the same (x,y) aggregate can't stack identical charts. */
  function dedupeRecommendations(recs) {
    var seen = {}, out = [];
    (recs || []).forEach(function (r) {
      var rt = String(r.chart_type || r.chartType || "bar").toLowerCase();
      var x = r.x_axis || r.xAxis || "";
      var y = r.y_axis || r.yAxis || "";
      if (!x) return;
      var key = [rt, String(x), String(y || "")].join("|");
      if (seen[key]) return;
      seen[key] = true;
      out.push(r);
    });
    return out;
  }

  /* Related tables of the base table that share a usable join key (mirrors the
     blank-dashboard canvas helper) — used to enable multi-table AI charts. */
  function relatedTableKeys(base) {
    var out = {};
    if (!base || !window.App || !App.state || !App.state.tables) return out;
    function colsOf(t) {
      var entry = App.state.tables[t];
      if (!entry || !Array.isArray(entry.columns)) return [];
      return entry.columns.map(function (c) { return String(c.column_name || c.name || ""); });
    }
    function joinKey(a, b) {
      var ca = colsOf(a), cb = colsOf(b), nb = {}, matches = [];
      cb.forEach(function (n) { nb[n] = true; });
      ca.forEach(function (n) { if (nb[n]) matches.push(n); });
      if (!matches.length) return null;
      for (var i = 0; i < matches.length; i++) if (/_id$/.test(matches[i])) return matches[i];
      for (var j = 0; j < matches.length; j++) if (/^(id|sid|uid|key|code)$/i.test(matches[j])) return matches[j];
      for (var k = 0; k < matches.length; k++) {
        if (!/(name|date|time|status|type|category|region|city|country|label|desc)/i.test(matches[k])) return matches[k];
      }
      return matches.length === 1 ? matches[0] : null;
    }
    Object.keys(App.state.tables).forEach(function (t) {
      if (t === base) return;
      var k = joinKey(base, t);
      if (k) out[t] = k;
    });
    return out;
  }

  /**
   * Ask the LLM (with a non-LLM heuristic fallback) for chart recommendations
   * for a table. Resolves to `{ recommendations: [...], used: "llm"|"auto" }`.
   */
  async function recommendCharts(table, prompt, relatedTables) {
    if (!table) return { recommendations: [], used: "auto" };
    try {
      var llmResolve = { __bounded: true };
      var llmBound = new Promise(function (res) { setTimeout(function () { res(llmResolve); }, 150000); });
      var llm = await Promise.race([API.llmChartRecommend(table, prompt || "", relatedTables || []), llmBound]);
      if (llm && !llm.__bounded && llm.success && Array.isArray(llm.charts) && llm.charts.length) {
        return { recommendations: dedupeRecommendations(llm.charts).slice(0, 6), used: "llm", tables: llm.tables, joins: llm.joins };
      }
    } catch (e) { /* fall through to heuristic */ }
    // Heuristic fallback (statistical column-type based)
    try {
      var auto = await API.chartRecommend(table, 5);
      var recs = (auto && (auto.charts || auto.recommendations)) || [];
      return { recommendations: dedupeRecommendations(recs).slice(0, 6), used: "auto" };
    } catch (e2) {
      return { recommendations: [], used: "auto" };
    }
  }

  /**
   * Fetch narrative insights for a table (LLM first, auto fallback). The
   * returned insights object is tagged with a `source` ("llm" | "auto") so the
   * dashboard panels can label whether the narrative is genuinely AI-generated.
   */
  async function fetchInsights(table, prompt) {
    try {
      var llmResolve = { __bounded: true };
      var llmBound = new Promise(function (res) { setTimeout(function () { res(llmResolve); }, 150000); });
      var llm = await Promise.race([API.llmInsights(table, prompt || ""), llmBound]);
      if (llm && !llm.__bounded && llm.success && llm.insights) {
        llm.insights.source = "llm";
        return llm.insights;
      }
    } catch (e) { /* fall back to auto */ }
    try {
      var res = await API.insights(table);
      if (res && res.success && res.insights) {
        res.insights.source = "auto";
        return res.insights;
      }
    } catch (e) { /* ignore */ }
    return null;
  }

  /* ════════════════════════════════════════════════════════════════════════
   * Professional Power-BI-style dashboard builder.
   *
   * Turns a table + AI chart recommendations + narrative insights into a full
   * professional dashboard layout: a header banner, a KPI band, a slicer row,
   * a 2-up chart grid and an executive insight panel.
   *
   * Widget shapes reuse the exact schema the blank-dashboard free-form canvas
   * already understands (type = kpi / chart / slicer / custom), so everything
   * renders and cross-filters out of the box.
   * ════════════════════════════════════════════════════════════════════════ */

  var _UID = 0;
  function _uid(prefix) {
    _UID++;
    return "w" + Date.now().toString(36) + "_" + (prefix || "ai") + _UID;
  }

  /* Measure / dimension / date column classification from App.state.tables */

  function _tc(v) { return String(v).toLowerCase(); }

  function _tableColumns(table) {
    try {
      var entry = (window.App && App.state && App.state.tables) ? App.state.tables[table] : null;
      if (entry && Array.isArray(entry.columns)) return entry.columns;
    } catch (e) { /* ignore */ }
    return [];
  }

  function _colName(c) { return c.column_name || c.name || c; }
  function _colType(c) { return _tc(c.column_type || c.type || ""); }

  function classifyColumns(table) {
    var cols = _tableColumns(table);
    var numeric = [], dims = [], dates = [];
    function typeIsNumeric(t) { return /int|float|double|decimal|numeric|real|bigint|smallint|tinyint|money|long|short|fixed/i.test(t); }
    function typeIsDate(t) { return /date|time|timestamp/i.test(t); }
    (cols || []).forEach(function (c) {
      var t = _colType(c);
      if (typeIsDate(t)) dates.push(c);
      else if (typeIsNumeric(t)) numeric.push(c);
      else dims.push(c);
    });
    // Fallback when no typed columns are present (e.g. untyped/generic schema):
    // guess measures by name, otherwise treat nothing as a measure.
    if (!numeric.length) {
      (cols || []).forEach(function (c) {
        var n = _colName(c).toLowerCase();
        if (/revenue|sales|amount|profit|income|value|cost|price|margin|gmv|qty|quantity|units|count|score|rating|salary|hours|expense|balance|rate|sum|total|roi|volume|customers|boxes/i.test(n)) {
          if (numeric.indexOf(c) === -1) numeric.push(c);
        }
      });
    }
    function pick(list, re) { for (var i = 0; i < list.length; i++) if (re.test(_colName(list[i]).toLowerCase())) return list[i]; return list[0]; }
    function pickName(list, re) { for (var i = 0; i < list.length; i++) if (re.test(_colName(list[i]).toLowerCase())) return list[i]; return null; }
    var m = pickName(numeric, /revenue|sales|amount|profit|value|margin|income|cost|price|gmv|total/i) || numeric[0];
    var m2 = null;
    for (var i = 0; i < numeric.length; i++) { if (_colName(numeric[i]).toLowerCase() !== _colName(m).toLowerCase()) { m2 = numeric[i]; break; } }
    var dim = pick(dims, /region|country|category|segment|department|team|channel|product|city|state|type|status|salesperson|customer/i) || dims[0];
    var dim2 = null;
    for (var j = 0; j < dims.length; j++) { if (_colName(dims[j]).toLowerCase() !== String(dim && _colName(dim)).toLowerCase()) { dim2 = dims[j]; break; } }
    var date = dates[0];
    return {
      numeric: numeric.map(_colName),
      dims: dims.map(_colName),
      dates: dates.map(_colName),
      m: m ? _colName(m) : "",
      m2: m2 ? _colName(m2) : "",
      dim: dim ? _colName(dim) : "",
      dim2: dim2 ? _colName(dim2) : "",
      date: date ? _colName(date) : "",
      hasColumns: cols.length > 0
    };
  }

  /* ── Header banner (custom HTML so we can use styled markup) ── */
  function headerWidget(table, title, subtitle, opts) {
    opts = opts || {};
    var esc = function (s) { var d = document.createElement("div"); d.appendChild(document.createTextNode(String(s == null ? "" : s))); return d.innerHTML; };
    /* Image-recreated headers: no branding badge, background from the
       screenshot's own header colors instead of the default gradient. */
    var bg = "linear-gradient(120deg,var(--w-theme-accent,#6366f1) 0%,var(--w-theme-accent-2,#1e293b) 110%)";
    if (opts.bgStart) {
      bg = opts.bgEnd && opts.bgEnd !== opts.bgStart
        ? "linear-gradient(120deg," + opts.bgStart + " 0%," + opts.bgEnd + " 110%)"
        : opts.bgStart;
    }
    var branding = opts.branding === false ? "" :
      '<div style="text-align:right;font-size:11px;opacity:.9;font-weight:700;letter-spacing:.5px;text-transform:uppercase"><div>Data Agent</div><div style="opacity:.7;margin-top:2px">AI-generated overview</div></div>';
    var html =
      '<div style="display:flex;align-items:center;justify-content:space-between;height:100%;padding:18px 26px;' + _fontStackCss() +
        'background:' + bg + ';border-radius:14px;color:#fff;box-shadow:0 8px 22px rgba(15,23,42,.18)">' +
        '<div><div style="font-size:23px;font-weight:800;letter-spacing:.2px;line-height:1.2">' + esc(title) + '</div>' +
        '<div style="font-size:12px;opacity:.82;margin-top:4px;font-weight:500;letter-spacing:.3px">' + esc(subtitle) + '</div></div>' +
        branding +
      '</div>';
    return {
      id: _uid("hdr"),
      type: "custom",
      title: title || "Dashboard",
      x: opts.x || 24,
      y: opts.y || 24,
      width: opts.width || 1872,
      height: opts.height || 96,
      zIndex: (opts.index || 0) + 1,
      locked: true,
      plain: false,
      showTitle: false,
      config: { table: table, html: html, label: title, plain: true, style: { border: "none" } }
    };
  }

  /* ── KPI card widgets ── */
  var _KPI_VARIANTS = [
    { agg: "SUM", prefix: "Total ", cls: "sum", icon: "chart-line" },
    { agg: "AVG", prefix: "Avg ", cls: "avg", icon: "chart-simple" },
    { agg: "MAX", prefix: "Peak ", cls: "max", icon: "arrow-trend-up" },
    { agg: "COUNT", prefix: "Records", cls: "cnt", icon: "layer-group" }
  ];

  function _trendSql(table, dateCol, m, bucket) {
    if (!dateCol || !m) return "";
    var fmt = "'%Y-%m'";
    if (bucket === "week") fmt = "'%Y-W%W'";
    else if (bucket === "year") fmt = "'%Y'";
    else if (bucket === "day") fmt = "'%Y-%m-%d'";
    return 'SELECT strftime(CAST(' + _q(dateCol) + ' AS TIMESTAMP), ' + fmt + ') AS label, SUM(' + _q(m) + ') AS value FROM ' + _q(table) + ' GROUP BY label ORDER BY label LIMIT 12';
  }

  function kpiWidgets(table, cls, opts) {
    opts = opts || {};
    var out = [];
    var startX = opts.x || 24;
    var startY = opts.y || 24;
    var gap = 16;
    var totalW = opts.width || 1872;
    var h = opts.height || 152;
    /* Registered measures for this table take priority over guessed columns */
    var regs = [];
    try { regs = (window.App && App.measures) || []; } catch (e) {}
    var mine = regs.filter(function (m) { return m.table_name === table; });
    if (mine.length) {
      var n = Math.min(mine.length, 4);
      var w = Math.floor((totalW - gap * (n - 1)) / n);
      for (var i = 0; i < n; i++) {
        var mm = mine[i];
        var cfg = {
          table: table,
          sql: "SELECT " + mm.expression + " AS value FROM " + _q(table),
          xCol: "", yCol: "value", label: mm.name,
          measureExpr: mm.expression, measureId: mm.id, measureName: mm.name,
          measureFormat: mm.format || "auto", format: mm.format || "auto",
          timeIntel: mm.date_column ? "ytd" : "none", dateCol: mm.date_column || "", periodLabel: ""
        };
        if (mm.date_column) {
          cfg.sparkSql = 'SELECT strftime(CAST(' + _q(mm.date_column) + " AS TIMESTAMP), '%Y-%m') AS label, " + mm.expression + " AS value FROM " + _q(table) + " GROUP BY label ORDER BY label LIMIT 12";
        }
        if (Array.isArray(opts.colors) && opts.colors.length) cfg.colors = opts.colors.slice();
        out.push({
          id: _uid("kpi" + i),
          type: "kpi",
          title: mm.name,
          x: startX + i * (w + gap),
          y: startY,
          width: w,
          height: h,
          zIndex: (opts.index || 0) + i + 1,
          locked: false,
          config: cfg
        });
      }
      return out;
    }
    // Order measures so the "best" (revenue/margin-like) measure appears first,
    // then the runner-up, then any remaining numeric columns, capped at 4.
    var ordered = [];
    if (cls.m) ordered.push(cls.m);
    if (cls.m2 && ordered.indexOf(cls.m2) === -1) ordered.push(cls.m2);
    (cls.numeric || []).forEach(function (n) { if (ordered.indexOf(n) === -1) ordered.push(n); });
    var measures = ordered.slice(0, 4);
    if (!measures.length) return out;
    var n = measures.length;
    var w = Math.floor((totalW - gap * (n - 1)) / n);
    for (var i = 0; i < n; i++) {
      var mName = measures[i];
      var v = _KPI_VARIANTS[i % _KPI_VARIANTS.length];
      var agg = v.agg === "COUNT" ? "COUNT(*)" : (v.agg + "(" + _q(mName) + ")");
      var title = v.agg === "COUNT" ? "Total Records" : (v.prefix + _humanize(mName));
      var cfg = {
        table: table,
        sql: "SELECT " + agg + " AS value FROM " + _q(table),
        xCol: "", yCol: "value", label: title
      };
      if (Array.isArray(opts.colors) && opts.colors.length) cfg.colors = opts.colors.slice();
      if (cls.date && v.agg === "SUM") cfg.sparkSql = _trendSql(table, cls.date, mName, "month");
      out.push({
        id: _uid("kpi" + i),
        type: "kpi",
        title: title,
        x: startX + i * (w + gap),
        y: startY,
        width: w,
        height: h,
        zIndex: (opts.index || 0) + i + 1,
        locked: false,
        config: cfg
      });
    }
    return out;
  }

  function _humanize(s) {
    s = String(s || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim();
    return s ? (s.charAt(0).toUpperCase() + s.slice(1)) : "Value";
  }

  /* ── Slicer (filter) widgets ── */
  function slicerWidgets(table, cls, opts) {
    opts = opts || {};
    var out = [];
    var fields = [];
    if (cls.dim) fields.push(cls.dim);
    if (cls.dim2 && fields.indexOf(cls.dim2) === -1) fields.push(cls.dim2);
    if (cls.date) fields.push(cls.date);
    fields = fields.slice(0, 3);
    if (!fields.length) return out;
    var n = fields.length;
    var startX = opts.x || 24;
    var startY = opts.y || 24;
    var gap = 16;
    var totalW = opts.width || 1872;
    var w = Math.floor((totalW - gap * (n - 1)) / n);
    var h = opts.height || 84;
    for (var i = 0; i < n; i++) {
      var f = fields[i];
      var kind = (cls.dates && cls.dates.indexOf(f) !== -1) ? "daterange" : "slicer";
      var sql = kind === "daterange"
        ? "SELECT MIN(" + _q(f) + ") AS min_val, MAX(" + _q(f) + ") AS max_val FROM " + _q(table)
        : "SELECT DISTINCT " + _q(f) + " AS label FROM " + _q(table) + " ORDER BY label";
      out.push({
        id: _uid("flt" + i),
        type: kind,
        title: _humanize(f),
        x: startX + i * (w + gap),
        y: startY,
        width: w,
        height: h,
        zIndex: (opts.index || 0) + i + 1,
        locked: false,
        config: { table: table, sql: sql, filterField: f, label: _humanize(f) }
      });
    }
    return out;
  }

  /* ── Executive insight panel (custom HTML) ── */
  /* Estimate the natural height the insight panel needs to show all its
     narrative rows without clipping, so the layout reserves enough space.
     Typography inside the panel is fixed-size, so the estimate is in raw
     logical px (independent of SC); the canvas auto-fits it to the real
     height after render as a final correction. */
  function estimateInsightHeight(table, ins, contentW) {
    var parts = [];
    if (ins.summary) parts.push(String(ins.summary));
    (ins.key_findings || []).slice(0, 3).forEach(function (f) { parts.push(String(f)); });
    (ins.recommendations || []).slice(0, 3).forEach(function (r) { parts.push(String(r)); });
    (ins.correlation_highlights || []).slice(0, 2).forEach(function (c) { parts.push(String(c)); });
    if (!parts.length) parts.push("No narrative insights available yet for this table.");
    var textW = Math.max(140, contentW - 190);   // panel padding (48) + borders + badge (126) + gap (14)
    var lineH = Math.round(22.4);                // 14px * 1.6
    var rowGap = 13;                             // per-row margin-bottom
    var badgeH = 22;                             // badge line incl. top margin
    var cjk = parts.some(function (p) { return /[\u3040-\u30ff\u4e00-\u9fff]/.test(String(p)); });
    var cpl = Math.max(24, Math.floor(textW / (cjk ? 14.2 : 7.6)));
    var h = 72;                                  // panel padding (16+16) + header block (~22 + 12 margin) + buffer
    parts.forEach(function (p) {
      var clean = String(p).replace(/\s+/g, " ").trim();
      var lines = Math.max(1, Math.ceil(clean.length / cpl));
      h += Math.max(badgeH, lines * lineH) + rowGap + 2;
    });
    return h + 8;                                // breathing room + safety buffer so the layout never under-reserves
  }

  function insightPanelWidget(table, insights, opts) {
    opts = opts || {};
    var esc = function (s) { var d = document.createElement("div"); d.appendChild(document.createTextNode(String(s == null ? "" : s))); return d.innerHTML; };
    var ins = insights || {};
    var parts = [];
    if (ins.summary) parts.push(["Executive Summary", String(ins.summary)]);
    (ins.key_findings || []).slice(0, 3).forEach(function (f) { parts.push(["Key Finding", String(f)]); });
    (ins.recommendations || []).slice(0, 3).forEach(function (r) { parts.push(["Recommendation", String(r)]); });
    (ins.correlation_highlights || []).slice(0, 2).forEach(function (c) { parts.push(["Correlation", String(c)]); });
    if (!parts.length) {
      parts.push(["AI Insights", "No narrative insights available yet for this table."]);
    }
    var colorMap = {
      "Executive Summary": opts.execColor || "#6366f1",
      "Key Finding": opts.findingColor || "#0ea5e9",
      "Recommendation": opts.recColor || "#10b981",
      "Correlation": opts.corrColor || "#f59e0b"
    };
    var body = parts.map(function (p) {
      var badge = '<span style="display:inline-block;min-width:126px;text-align:center;background:' + (colorMap[p[0]] || "#64748b") + '22;color:' + (colorMap[p[0]] || "#64748b") + ';font-size:10px;font-weight:800;letter-spacing:.8px;text-transform:uppercase;padding:4px 9px;border-radius:999px;margin-top:2px">' + p[0] + "</span>";
      return '<div style="display:flex;align-items:flex-start;gap:14px;margin-bottom:13px;line-height:1.6;font-size:14px;color:var(--w-theme-text,#334155)">' +
        badge + '<div style="flex:1;min-width:0">' + mdToHtml(p[1]) + "</div></div>";
    }).join("");
    var html =
      '<div class="bd-insight-panel" style="height:100%;padding:16px 24px;background:var(--w-theme-card-bg,#ffffff);border:1px solid var(--w-theme-card-border,#e2e8f0);border-radius:14px;display:flex;flex-direction:column;' + _fontStackCss() + '">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex:none">' +
          '<div style="font-weight:800;font-size:16px;color:var(--w-theme-text,#0f172a)"><i class="fas fa-robot" style="color:var(--w-theme-accent,#6366f1);margin-right:8px"></i>AI Executive Insights</div>' +
          '<span style="font-size:11px;color:var(--w-theme-text-2,#94a3b8);font-weight:600;letter-spacing:.5px;text-transform:uppercase">' + esc(table) +
            ' &middot; ' + _insightSourceLabel(ins) + '</span></div>' +
        '<div class="bd-insight-body" style="overflow-y:auto;flex:1;min-height:0;padding-right:6px">' + body + '</div>' +
      '</div>';
    return {
      id: _uid("ins"),
      type: "custom",
      title: "AI Executive Insights",
      x: opts.x || 24,
      y: opts.y || 24,
      width: opts.width || 1872,
      height: opts.height || 250,
      zIndex: (opts.index || 0) + 1,
      locked: false,
      plain: true,
      showTitle: false,
      config: { table: table, html: html, label: "AI Executive Insights", plain: true, autoFit: true }
    };
  }

  /* ── Full professional layout ──
   * Composes header + KPI band + slicers + chart grid + insight panel.
   * All coordinates are for the given canvas width (default 1920). */
  function buildAIDashboardLayout(table, recs, insights, opts) {
    opts = opts || {};
    var th = opts.theme || {};
    var W = opts.canvasW || 1920;
    var GAP = 16, M = 24;
    var cls = classifyColumns(table);
    var widgets = [];
    var z = 1;
    var contentW = W - M * 2;
    var accent = opts.accent || th.accent || "#6366f1";
    var accent2 = opts.accent2 || th.accent2 || th.secondary || "#1e293b";
    var palette = opts.colors || (Array.isArray(th.palette) ? th.palette : null);

    // 1. Header banner
    widgets.push(headerWidget(table, opts.title || "AI Dashboard", opts.subtitle || ("Data source: " + table), { x: M, y: M, width: contentW, height: 96, index: z++, accent: accent, accent2: accent2 }));

    // 2. KPI band
    var kpiY = M + 96 + GAP;
    var kpis = kpiWidgets(table, cls, { x: M, y: kpiY, width: contentW, height: 150, colors: palette });
    widgets = widgets.concat(kpis);

    // 2b. Executive insight panel — directly below the KPI band
    if (insights && (insights.summary || (insights.key_findings && insights.key_findings.length))) {
      var insH = Math.max(170, estimateInsightHeight(table, insights, contentW));
      var insY = (kpis.length ? kpiY + 150 + GAP : kpiY + GAP);
      widgets.push(insightPanelWidget(table, insights, { x: M, y: insY, width: contentW, height: insH, index: z++, theme: th, accent: accent }));
    }

    // 3. Slicer row
    var fltY = ((kpis.length ? kpiY + 150 + GAP : kpiY + GAP) + (insights && (insights.summary || (insights.key_findings && insights.key_findings.length)) ? (Math.max(170, estimateInsightHeight(table, insights, contentW))) + GAP : 0));
    var slicers = slicerWidgets(table, cls, { x: M, y: fltY, width: contentW, height: 82 });
    widgets = widgets.concat(slicers);

    // 4. Chart grid (2-up) from recommendations
    var chartY = (slicers.length ? fltY + 82 + GAP : fltY + GAP);
    var chartW = Math.floor((contentW - GAP) / 2);
    var chartH = opts.chartH || 300;
    var jm = opts.joins || {};
    // Build all chart widgets first, dropping any that couldn't resolve to SQL.
    var builtCharts = (recs || []).slice(0, 6)
      .map(function (rec, i) { return chartWidgetFromRec(rec, table, { index: i, width: chartW, height: chartH, colors: palette, joins: jm }); })
      .filter(Boolean);
    var charts = builtCharts.map(function (wobj, i) {
      var col = i % 2, row = Math.floor(i / 2);
      wobj.x = M + col * (chartW + GAP);
      wobj.y = chartY + row * (chartH + GAP);
      wobj.zIndex = z++;
      return wobj;
    });
    widgets = widgets.concat(charts);

    // Normalise z-index so stacked ordering is deterministic
    widgets.forEach(function (w, i) { w.zIndex = (w.zIndex || 0); });

    return { widgets: widgets, cls: cls, chartCount: charts.length };
  }

  /* ════════════════════════════════════════════════════════════════════════
   * Data-Driven Plan Consumer (Power BI canonical layout)
   *
   * Consumes the validated plan returned by POST /dashboard/auto-build-plan:
   *   plan.kpis[]   → KPI card band (one row, 3-5 cards)
   *   plan.filters[] → slicer row
   *   plan.zones[]  → trend / breakdown / comparison charts in band order
   *   plan.insights → AI executive insight panel
   *
   * Every widget uses the pre-validated `sql` from the plan, so no blank or
   * irrelevant widgets are ever produced. Layout follows the canonical Power
   * BI structure: Title → KPI band → Executive insights → Filters → Trend and
   * Breakdown → Comparison and Comparison, between each zone.
   * ════════════════════════════════════════════════════════════════════════ */

  // Map backend zone chartType to the canvas widget.
  // Some visuals need a top-level widget `type` (histogram/boxplot/pareto/maps),
  // the rest are type "chart" with a config.chartType the chart renderer knows.
  function _planWidgetType(ct) {
    var t = String(ct || "bar").toLowerCase();
    if (t === "histogram") return { type: "histogram", chartType: "histogram" };
    if (t === "box" || t === "boxplot") return { type: "boxplot", chartType: "box" };
    if (t === "pareto") return { type: "pareto", chartType: "pareto" };
    if (t === "countrymap" || t === "geomap" || t === "shapemap" ||
        t === "dotmap" || t === "heatmapmap" || t === "flowmap" || t === "glmap") {
      return { type: t, chartType: t, map: true };
    }
    if (t === "donut" || t === "pie") return { type: "chart", chartType: "doughnut" };
    if (t === "area") return { type: "chart", chartType: "area" };
    if (t === "combo") return { type: "chart", chartType: "combo" };
    if (t === "treemap") return { type: "chart", chartType: "treemap" };
    if (t === "funnel") return { type: "chart", chartType: "funnel" };
    if (t === "waterfall") return { type: "chart", chartType: "waterfall" };
    if (t === "sunburst") return { type: "sunburst", chartType: "sunburst" };
    if (t === "scatter") return { type: "chart", chartType: "scatter" };
    if (t === "bubble") return { type: "chart", chartType: "bubble" };
    if (t === "heatmap") return { type: "chart", chartType: "heatmap" };
    if (t === "radar") return { type: "chart", chartType: "radar" };
    if (t === "sankey") return { type: "sankey", chartType: "sankey" };
    if (t === "horizontalbar") return { type: "chart", chartType: "horizontalbar" };
    if (t === "stackedbar" || t === "stacked") return { type: "chart", chartType: "stackedbar" };
    if (t === "line") return { type: "chart", chartType: "line" };
    if (t === "gauge") return { type: "chart", chartType: "gauge" };
    return { type: "chart", chartType: "bar" };
  }

  function _planChartType(ct) {
    var mapped = _planWidgetType(ct);
    return mapped.chartType || String(ct || "bar").toLowerCase();
  }

  // A subtle band separating dashboard zones, colored with the theme accent.
  function dividerWidget(label, accent, opts) {
    opts = opts || {};
    return {
      id: _uid("div"),
      type: "divider",
      title: label || "",
      x: opts.x || 24,
      y: opts.y || 24,
      width: opts.width || 1872,
      height: opts.height || 34,
      zIndex: (opts.index || 0) + 1,
      locked: false,
      plain: true,
      config: {
        label: label || "",
        html: '<div style="display:flex;align-items:center;gap:10px;height:100%">' +
          '<div style="font-size:13px;font-weight:800;letter-spacing:.8px;text-transform:uppercase;color:' + accent + '">' + String(label || "Section").toUpperCase() + '</div>' +
          '<div style="flex:1;height:2px;border-radius:2px;background:linear-gradient(90deg,' + accent + '55,transparent)"></div></div>',
        plain: true
      }
    };
  }

  // Build a chart widget directly from a validated plan zone.
  function chartWidgetFromPlan(zone, table, opts) {
    opts = opts || {};
    var mapped = _planWidgetType(zone.chartType);
    var ct = mapped.chartType || "bar";
    var wtype = mapped.type || "chart";
    var cfg = {
      table: table,
      sql: zone.sql,
      chartType: ct,
      xCol: zone.x || "label",
      yCol: zone.y || "value",
      label: zone.title,
      filterField: zone.title,
      agg: "SUM",
      limit: 12
    };
    // Merge validated zone.config extras (seriesCol, source/target, etc.)
    var zc = (zone && zone.config && typeof zone.config === "object") ? zone.config : {};
    Object.keys(zc).forEach(function (k) { cfg[k] = zc[k]; });
    if (mapped.map) {
      // Map visuals: the canvas resolves the geo column + measure from the SQL.
      cfg.geoField = zone.x || "label";
      cfg.measureField = zone.y || "value";
      cfg.aggregate = true;
    }
    if (ct === "stackedbar" || ct === "stacked") {
      cfg.stacked = true;
      if (!cfg.seriesCol) cfg.seriesCol = "series";
    }
    if (ct === "horizontalbar") cfg.indexAxis = "y";
    if (Array.isArray(opts.colors) && opts.colors.length) cfg.colors = opts.colors.slice();
    return {
      id: _uid("zn" + (opts.index || 0)),
      type: wtype,
      title: zone.title,
      x: opts.x || 24,
      y: opts.y || 24,
      width: opts.width || (mapped.map ? 1872 : 916),
      height: opts.height || 300,
      zIndex: (opts.index || 0) + 1,
      locked: false,
      plain: false,
      config: cfg
    };
  }

  // Build a KPI card widget from a validated plan KPI. The plan may tag each KPI
  // with a richer widget `variant` (gauge / trendkpi / prevkpi / percentagekpi).
  function kpiWidgetFromPlan(kpi, table, opts) {
    opts = opts || {};
    var variant = String(kpi.variant || "kpi");
    if (["gauge", "radial", "progress"].indexOf(variant) !== -1) {
      var gcfg = {
        table: table,
        sql: kpi.sql || ("SELECT SUM(1) AS value FROM " + _q(table)),
        xCol: "", yCol: "value",
        label: kpi.label,
        format: kpi.format || "auto",
        chartType: variant === "gauge" ? "gauge" : "radial"
      };
      if (variant === "radial") { gcfg.target = "auto"; gcfg.value = 0; }
      if (Array.isArray(opts.colors) && opts.colors.length) gcfg.colors = opts.colors.slice();
      return {
        id: _uid("kpi" + (opts.index || 0)),
        type: variant === "gauge" ? "chart" : variant, // gauge renders via chartType "gauge"; radial/progress are registered types
        title: kpi.label,
        x: opts.x || 24,
        y: opts.y || 24,
        width: opts.width || 460,
        height: opts.height || 152,
        zIndex: (opts.index || 0) + 1,
        locked: false,
        config: gcfg
      };
    }
    var cfg = {
      table: table,
      sql: kpi.sql,
      xCol: "", yCol: "value", label: kpi.label,
      format: "auto"
    };
    if (kpi.spark && kpi.sparkSql) cfg.sparkSql = kpi.sparkSql;
    /* Unified, aligned KPI card for AI-built dashboards: value / label / small
       delta pill. The widget type (trend/prev/percent) is kept so the data
       loader still resolves previous/comparison values, but rendering uses the
       single 'plan' style so every card lines up identically. */
    cfg.kpiStyle = "plan";
    if (Array.isArray(opts.colors) && opts.colors.length) {
      cfg.colors = opts.colors.slice();
      cfg.color = opts.colors[(opts.index || 0) % opts.colors.length];
    }
    var kpiType = (["trendkpi", "prevkpi", "percentagekpi"].indexOf(variant) !== -1) ? variant : "kpi";
    return {
      id: _uid("kpi" + (opts.index || 0)),
      type: kpiType,
      title: kpi.label,
      x: opts.x || 24,
      y: opts.y || 24,
      width: opts.width || 460,
      height: opts.height || 152,
      zIndex: (opts.index || 0) + 1,
      locked: false,
      config: cfg
    };
  }

  // Compose a whole professional dashboard page from a backend plan using a
  // Power BI-style anatomy: 16:9 canvas, header band → compact slicer row →
  // KPI band → full-width AI insight band → curated big chart panels that
  // exactly fill the chart zone. Every widget carries its own title header, so
  // no divider bands are needed and panels render large.
  function zoneChartShape(zn) {
    var mapped = _planWidgetType((zn && zn.chartType) || "");
    var ct = String((mapped && mapped.chartType) || (zn && zn.chartType) || "").toLowerCase();
    var fam = (zn && zn.zone) || "";
    if ((mapped && mapped.map) || /map|geo/.test(ct) || fam === "geo") return { map: true };
    if (/line|area|combo/.test(ct) || fam === "trend") return { span: 3, aspect: 5.2 };
    if (/table|heatmap|matrix|grid/.test(ct)) return { span: 2, aspect: 4.2 };
    if (/donut|pie|sunburst|funnel|histogram/.test(ct) || fam === "distribution") return { span: 1, aspect: 2.2 };
    if (/corr|scatter|bubble/.test(ct) || fam === "correlation") return { span: 1, aspect: 2.6 };
    return { span: 1, aspect: 3.4 };
  }

  /* Pack chart shapes into rows of four column-slots, expanding the last panel
     of each row to fill the width exactly, then compute each row's natural
     height from the family aspect. Rows come back unscaled; the caller scales
     once to exactly fit the zone height. */
  function packChartGrid(items, zoneW, GAP) {
    var slotW = (zoneW - 3 * GAP) / 4;
    var rows = [];
    var cur = { items: [], used: 0 };
    items.forEach(function (p) {
      if (cur.used + p.span > 4) { rows.push(cur); cur = { items: [], used: 0 }; }
      cur.items.push(p);
      cur.used += p.span;
    });
    rows.push(cur);
    rows.forEach(function (r) {
      if (r.used < 4) {
        var last = r.items[r.items.length - 1];
        last.span += (4 - r.used);
        r.used = 4;
      }
      r.height = Math.max.apply(null, r.items.map(function (p) {
        return (slotW * p.span + GAP * (p.span - 1)) / p.aspect;
      }));
    });
    return { slotW: slotW, rows: rows };
  }

  function buildDashboardFromPlan(plan, opts) {
    opts = opts || {};
    var th = opts.theme || {};
    var table = plan.table || "";
    var W = opts.canvasW || 1920;
    var H = opts.canvasH || 1080;
    /* Snap landscape canvases to 16:9 so panels keep print-style proportions. */
    if (W >= H && Math.abs(W / H - 16 / 9) > 0.03) H = Math.round(W * 9 / 16);
    var SC = Math.min(1.35, Math.max(0.8, H / 720));
    var GAP = Math.round(12 * SC);
    var M = 16;
    var z = 2;
    var slim = !!opts.slim;
    // Website-style left sidebar navigation: when the caller opts in, the whole
    // content column shifts right by the sidebar width so nothing overlaps it.
    var sbW = opts.sidebar ? (opts.sidebarW || 176) : 0;
    var X0 = M + sbW;
    var contentW = W - M * 2 - sbW;
    var accent = opts.accent || th.accent || "#6366f1";
    var accent2 = opts.accent2 || th.accent2 || "#1e293b";
    var palette = opts.colors || (Array.isArray(th.palette) ? th.palette : null);
    var widgets = [];
    var y = M;
    var hasInsight = !!(plan.insights && (plan.insights.summary || (plan.insights.key_findings && plan.insights.key_findings.length)));

// Chrome heights sized to the widgets' real content so nothing clips: the
    // header carries a 26px title + subtitle, slicers are compact single
    // dropdowns (compact:true below), and KPI cards fit a big value + delta.
    var headerH = Math.round(72 * SC);
    var slicerH = Math.round(76 * SC);
    var kpiH = Math.round(112 * SC);
    var insightH = hasInsight
      ? Math.max(Math.round(118 * SC), Math.min(Math.round(H * 0.34), estimateInsightHeight(table, plan.insights, contentW)))
      : 0;

    // 1. Title / header banner
    widgets.push(headerWidget(table, opts.title || plan.title || "Dashboard",
      opts.subtitle || ("Data source: " + table + " · AI data-driven layout"),
      { x: X0, y: y, width: contentW, height: headerH, index: z++, accent: accent, accent2: accent2 }));
    y += headerH + GAP;

    // 2. Compact filter / slicer row (slicer / daterange / numrange). Detail
    //    pages (opts.slim) carry fewer slicers and a slimmer KPI band so the
    //    charts get the space — mirroring how Power BI detail pages focus on
    //    the analysis while the exec/overview page owns the KPI band.
    var filters = (Array.isArray(plan.filters) ? plan.filters : []).slice(0, slim ? 3 : 6);
    if (filters.length) {
      var fw = Math.floor((contentW - GAP * (filters.length - 1)) / filters.length);
      filters.forEach(function (f, i) {
        var ftype = String(f.type || "");
        var isDate = ftype === "daterange";
        var isNum = ftype === "numrange";
        var fsql = f.sql;
        if (!fsql) {
          fsql = isDate
            ? "SELECT MIN(" + _q(f.field) + ") AS min_val, MAX(" + _q(f.field) + ") AS max_val FROM " + _q(table)
            : isNum
              ? "SELECT MIN(CAST(" + _q(f.field) + " AS DOUBLE)) AS min_val, MAX(CAST(" + _q(f.field) + " AS DOUBLE)) AS max_val FROM " + _q(table)
              : "SELECT DISTINCT " + _q(f.field) + " AS label FROM " + _q(table) + " ORDER BY label";
        }
        widgets.push({
          id: _uid("flt" + i),
          type: isDate ? "daterange" : (isNum ? "numrange" : "slicer"),
          title: f.label || _humanize(f.field),
          x: X0 + i * (fw + GAP), y: y, width: fw, height: slicerH, zIndex: z++, locked: false,
          config: { table: table, sql: fsql, filterField: f.field, label: f.label || _humanize(f.field),
            slicerStyle: "dropdown", compact: true }
        });
      });
      y += slicerH + GAP;
    }

    // 3. KPI band — one compact row of KPI cards.
    var kpis = Array.isArray(plan.kpis) ? plan.kpis.slice(0, slim ? 3 : 6) : [];
    if (kpis.length) {
      var kw = Math.floor((contentW - GAP * (kpis.length - 1)) / kpis.length);
      kpis.forEach(function (k, i) {
        widgets.push(kpiWidgetFromPlan(k, table, {
          x: X0 + i * (kw + GAP), y: y, width: kw, height: kpiH, index: z++, colors: palette
        }));
      });
      y += kpiH + GAP;
    }

    // 3b. AI Executive insight panel — directly below the KPI band. Overview
    //    pages carry it; slim detail pages drop it so charts dominate.
    //    Fit-to-canvas guard: the chrome (header + slicers + KPI + insight)
    //    must leave a real chart zone, otherwise widgets would spill below the
    //    canvas. The insight panel is the flexible band — shrink it, then drop
    //    it, so the chart grid always stays inside the dashboard bounds.
    var minZoneH = Math.max(140, Math.round(H * 0.28));
    var chromeH = headerH + GAP + (filters.length ? slicerH + GAP : 0) + (kpis.length ? kpiH + GAP : 0) + (hasInsight && !slim ? insightH + GAP : 0);
    var availChrome = (H - M) - minZoneH;
    if (hasInsight && !slim && chromeH > availChrome) {
      var over = chromeH - availChrome;
      var newInsightH = Math.max(0, insightH - over);
      if (newInsightH < 60) { hasInsight = false; insightH = 0; }
      else insightH = newInsightH;
    }
    if (hasInsight && !slim) {
      widgets.push(insightPanelWidget(table, plan.insights, {
        x: X0, y: y, width: contentW, height: insightH, index: z++, theme: th, accent: accent
      }));
      y += insightH + GAP;
    }

    // 4. Chart zone. Curate the zones (or take the caller's per-page pick),
    //    split real map/geo zones into full-width strips, and pack the rest
    //    into a slot grid of big panels. Panels are trimmed (lowest priority
    //    first) until the natural grid height fits the zone without shrinking
    //    below ~80% — so panels stay large and nothing falls off the canvas.
    var budget = opts.chartBudget || 9;
    var zones = Array.isArray(opts.pageZones)
      ? opts.pageZones.slice(0, budget)
      : (Array.isArray(plan.zones) ? plan.zones.slice(0, budget) : []);
    var PRIORITY = { trend: 0, breakdown: 1, comparison: 2, distribution: 3,
                     correlation: 4, special: 5, geo: 6 };
    var gridItems = [], mapItems = [];
    zones.forEach(function (zn) {
      var sh = zoneChartShape(zn);
      if (sh.map) mapItems.push(zn);
      else gridItems.push({ zn: zn, span: sh.span, aspect: sh.aspect });
    });
    gridItems = gridItems.sort(function (a, b) {
      var ra = PRIORITY[a.zn.zone] != null ? PRIORITY[a.zn.zone] : 3;
      var rb = PRIORITY[b.zn.zone] != null ? PRIORITY[b.zn.zone] : 3;
      return ra - rb;
    });

    var zoneTop = y;
    var zoneH = Math.max(minZoneH, (H - M) - zoneTop);
    var zoneW = contentW;

    // Full-width geometry strips get a fixed slice; the grid gets the rest.
    var mapH0 = Math.round(Math.min(zoneW / 3.0, Math.max(150 * SC, zoneH * 0.34)));
    var mapRegion = mapItems.length ? mapItems.length * mapH0 + (mapItems.length ? GAP : 0) : 0;
    if (mapRegion > zoneH * 0.62 && mapItems.length) {
      mapH0 = Math.round(Math.max(120 * SC, (zoneH * 0.6 - GAP) / mapItems.length));
      mapRegion = mapItems.length * mapH0 + GAP;
    }
    var gridZoneH = Math.max(0, zoneH - mapRegion);
    // If the map strip would swallow the whole chart zone, drop it so the grid
    // (the primary content) always gets the full zone and nothing overflows.
    if (gridZoneH < 60 && mapItems.length) { mapItems = []; mapRegion = 0; gridZoneH = zoneH; }

    var yy = zoneTop;
    if (gridItems.length) {
      // Pack into rows of four column-slots. A page gets at most three rows
      // (four when the zone is tall) — any overflow panels are dropped (lowest
      // priority first) so every row stays big. Heights are fixed fractions of
      // the zone, so the grid always fills it exactly with large panels.
      var packed = packChartGrid(gridItems, zoneW, GAP);
      var rows = packed.rows;
      var maxRows = Math.min(4, Math.max(2, Math.round(gridZoneH / 210)));
      while (rows.length > maxRows && gridItems.length > 1) {
        gridItems.pop();
        packed = packChartGrid(gridItems, zoneW, GAP);
        rows = packed.rows;
      }
      var R = rows.length;
      var gapH = GAP * (R - 1);
      var bits = R === 3 ? [0.40, 0.33, 0.27] : (R === 2 ? [0.55, 0.45] : [1]);
      var rowH = bits.map(function (b, i) {
        return (i === R - 1)
          ? Math.max(60, (gridZoneH - gapH) - bits.slice(0, R - 1).reduce(function (n, x) {
              return n + Math.round((gridZoneH - gapH) * x);
            }, 0))
          : Math.round(Math.max(60, (gridZoneH - gapH) * b));
      });
      // Final safety: if the per-row floors still overshoot the zone, scale the
      // rows down proportionally (40px floor) so the grid never spills below.
      var totalH = rowH.reduce(function (n, x) { return n + x; }, 0) + gapH;
      if (totalH > gridZoneH) {
        var gscale = (gridZoneH - gapH) / (totalH - gapH);
        rowH = rowH.map(function (h) { return Math.max(40, Math.round(h * gscale)); });
      }
      var slotW = packed.slotW;
      rows.forEach(function (r, ri) {
        var xx = X0;
        r.items.forEach(function (p) {
          var pw = (slotW * p.span + GAP * (p.span - 1));
          widgets.push(chartWidgetFromPlan(p.zn, table, {
            x: Math.round(xx), y: Math.round(yy),
            width: Math.round(pw), height: rowH[ri], index: z++, colors: palette
          }));
          xx += pw + GAP;
        });
        yy += rowH[ri] + GAP;
      });
    }
    mapItems.forEach(function (zn) {
      widgets.push(chartWidgetFromPlan(zn, table, {
        x: X0, y: Math.round(yy), width: zoneW, height: mapH0, index: z++, colors: palette
      }));
      yy += mapH0 + GAP;
    });

    widgets.forEach(function (w) { w.zIndex = (w.zIndex || 0); });

    return { widgets: widgets, cls: classifyColumns(table), chartCount: gridItems.length + mapItems.length };
  }

  /* ── Multi-page AI layout ──
     Split one validated plan into dashboard pages. Page 1 is the executive
     Overview (KPI band + slicers + trend + primary breakdown + insights); the
     extra pages are focused per the wizard's per-page chips. Every page gets
     its own header, KPI band and slicers, plus fewer charts and its own
     fit-to-canvas so widgets render large instead of spanning a 4000px strip. */

  /* Power BI-style page navigator widget. Emits a `page` (Page Link) widget
     that renders a compact horizontal pill row listing every page of the
     dashboard; clicking one jumps to that page (targetPage = page index).
     On all but the first call the "current" page is highlighted via accent. */
  function pageNavigatorWidget(pages, currentIdx, opts) {
    opts = opts || {};
    var th = opts.theme || {};
    var acc = opts.accent || th.accent || th.primary || _PBI_ACCENT;
    var ink = th.text || _PBI_INK;
    var pillBg = _lighten(acc, 0.92);
    var pillBorder = _lighten(acc, 0.8);
    var esc = function (s) {
      var d = document.createElement("div");
      d.appendChild(document.createTextNode(String(s == null ? "" : s)));
      return d.innerHTML;
    };
    function pageNameOf(item) {
      if (item == null) return "";
      return typeof item === "string" ? item : (item.name || "");
    }
    var pageNames_ = (pages || []).map(pageNameOf);
    var pills = pageNames_.map(function (nm, i) {
      var active = i === currentIdx;
      return '<span onclick="event.stopPropagation();window.BlankDash&&BlankDash._switchPage(' + i + ')" style="display:inline-block;padding:6px 14px;margin:2px 4px;border-radius:999px;cursor:pointer;' +
        'font-size:12px;font-weight:700;letter-spacing:.3px;white-space:nowrap;' +
        (active
          ? 'background:' + acc + ';color:#fff;'
          : 'background:' + pillBg + ';color:' + ink + ';border:1px solid ' + pillBorder + ';') +
        '">' + (i + 1) + '&nbsp;&middot;&nbsp;' + esc(nm) + '</span>';
    }).join("");
    var nextIdx = currentIdx + 1;
    var hasNext = nextIdx < pageNames_.length;
    var nextLabel = hasNext
      ? '<span onclick="event.stopPropagation();window.BlankDash&&BlankDash._switchPage(' + nextIdx + ')" style="display:inline-block;padding:6px 14px;margin:2px 4px;border-radius:999px;cursor:pointer;white-space:nowrap;color:' + acc + ';border:1px dashed ' + acc + ';font-size:12px;font-weight:700;letter-spacing:.3px;background:rgba(123,97,255,0.06);">' +
          '<i class="fas fa-arrow-right" style="font-size:10px;margin-right:6px"></i>Next:&nbsp;' + esc(pageNames_[nextIdx]) +
        '</span>'
      : '';
    var html =
      '<div style="height:100%;display:flex;align-items:center;gap:10px;padding:4px 10px;overflow-x:auto;' + _fontStackCss() + '">' +
        '<i class="fas fa-layer-group" style="color:' + acc + ';font-size:13px;flex:none"></i>' +
        '<div style="display:flex;align-items:center;flex-wrap:nowrap">' + pills + '</div>' +
        '<div style="flex:1"></div>' +
        nextLabel +
      '</div>';
    return {
      id: _uid("nav"),
      type: "custom",
      title: "Page Navigator",
      x: opts.x || 24,
      y: opts.y || 24,
      width: opts.width || 900,
      height: opts.height || 40,
      zIndex: (opts.index || 0) + 1,
      locked: false,
      plain: true,
      showTitle: false,
      config: {
        table: "",
        html: html,
        label: "Page Navigator",
        plain: true,
        autoFit: true
      }
    };
  }

  var _PAGE_FOCUS_SETS = {
    breakdown: ["breakdown", "comparison"],
    comparison: ["comparison", "breakdown"],
    distribution: ["distribution", "correlation"],
    correlation: ["correlation", "distribution"],
    special: ["special", "geo"],
    geo: ["geo", "special"]
  };
  var _PAGE_FOCUS_LABELS = {
    breakdown: "Breakdown & Comparison",
    comparison: "Comparisons & Breakdown",
    distribution: "Distributions & Correlations",
    correlation: "Correlations & Distributions",
    special: "Analysis & Geography",
    geo: "Geography & Analysis",
    data: "Data"
  };

  /* Website-style left sidebar navigation (Power BI / dashboard reference).
     A locked, full-height custom widget pinned to the left edge of the canvas.
     Brand block on top, one nav item per page (icon + label, active highlight),
     and a footer with prev/next arrows + "N / M". Clicking an item jumps to that
     page via the same _switchPage mechanism the pill navigator uses, so it works
     in edit and present mode alike. */
  function sidebarNavWidget(pages, currentIdx, opts) {
    opts = opts || {};
    var th = opts.theme || {};
    var bg1 = th.cardBg || "#1e1b4b";
    var bg2 = th.cardBg ? _lighten(th.cardBg, 0.28) : "#312e81";
    var ink = th.text || "#c7d2fe";
    var muted = th.textSecondary || "#a5b4fc";
    var accent = opts.accent || th.accent || th.primary || _PBI_ACCENT;
    var accent2 = opts.accent2 || th.accent2 || (th.gradient && th.gradient[1]) || th.secondary || "#6366f1";
    var dark = _isDarkHex(bg1);
    var hoverBg = dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)";
    var hoverInk = dark ? "#ffffff" : "#0f172a";
    var borderC = dark ? "rgba(255,255,255,0.10)" : "rgba(0,0,0,0.10)";
    var esc = function (s) {
      var d = document.createElement("div");
      d.appendChild(document.createTextNode(String(s == null ? "" : s)));
      return d.innerHTML;
    };
    function pageNameOf(item) {
      if (item == null) return "";
      return typeof item === "string" ? item : (item.name || "");
    }
    function iconFor(nm) {
      var n = String(nm || "").toLowerCase();
      if (/overview|summary|executive/.test(n)) return "fa-chart-pie";
      if (/breakdown|share|category/.test(n)) return "fa-chart-column";
      if (/comparison|compare|rank/.test(n)) return "fa-scale-balanced";
      if (/distribution|histogram/.test(n)) return "fa-chart-bar";
      if (/correlation|scatter|heat/.test(n)) return "fa-circle-nodes";
      if (/geo|map|region/.test(n)) return "fa-earth-americas";
      if (/data|table|raw|preview/.test(n)) return "fa-table";
      if (/analysis|deep|special|waterfall/.test(n)) return "fa-magnifying-glass-chart";
      if (/trend|time|monthly|daily/.test(n)) return "fa-chart-line";
      return "fa-chart-column";
    }
    var pageNames_ = (pages || []).map(pageNameOf);
    var table = opts.table || "";
    var items = pageNames_.map(function (nm, i) {
      var active = i === currentIdx;
      return '<div class="ai-sidenav-item' + (active ? " active" : "") + '" onclick="event.stopPropagation();window.BlankDash&&BlankDash._switchPage(' + i + ')" title="' + esc(nm) + '">' +
        '<i class="fas ' + iconFor(nm) + '"></i><span>' + esc(nm) + '</span>' +
        (active ? '<i class="fas fa-chevron-right ai-sidenav-caret"></i>' : "") +
      '</div>';
    }).join("");
    var prevIdx = currentIdx - 1;
    var nextIdx = currentIdx + 1;
    var hasPrev = prevIdx >= 0;
    var hasNext = nextIdx < pageNames_.length;
    var html =
      '<div class="ai-sidenav" style="' + _fontStackCss() + '--ai-snav-bg1:' + bg1 + ';--ai-snav-bg2:' + bg2 + ';--ai-snav-ink:' + ink + ';--ai-snav-muted:' + muted + ';--ai-snav-accent:' + accent + ';--ai-snav-accent2:' + accent2 + ';--ai-snav-hover:' + hoverBg + ';--ai-snav-hover-ink:' + hoverInk + ';--ai-snav-border:' + borderC + '">' +
        '<div class="ai-sidenav-brand">' +
          '<div class="ai-sidenav-logo"><i class="fas fa-chart-pie"></i></div>' +
          '<div class="ai-sidenav-brand-txt">' +
            '<div class="ai-sidenav-title">Dashboard</div>' +
            (table ? '<div class="ai-sidenav-sub">' + esc(table) + '</div>' : "") +
          '</div>' +
        '</div>' +
        '<div class="ai-sidenav-items">' + items + '</div>' +
        '<div class="ai-sidenav-footer">' +
          '<button type="button" class="ai-sidenav-arrow"' + (hasPrev ? ' onclick="event.stopPropagation();window.BlankDash&&BlankDash._switchPage(' + prevIdx + ')"' : ' disabled') + '><i class="fas fa-chevron-left"></i></button>' +
          '<span class="ai-sidenav-count">' + (currentIdx + 1) + ' / ' + pageNames_.length + '</span>' +
          '<button type="button" class="ai-sidenav-arrow"' + (hasNext ? ' onclick="event.stopPropagation();window.BlankDash&&BlankDash._switchPage(' + nextIdx + ')"' : ' disabled') + '><i class="fas fa-chevron-right"></i></button>' +
        '</div>' +
      '</div>';
    return {
      id: _uid("nav"),
      type: "custom",
      title: "Page Navigator",
      x: opts.x || 0,
      y: opts.y || 0,
      width: opts.width || 176,
      height: opts.height || 1080,
      zIndex: (opts.index || 0) + 1000,
      locked: true,
      plain: true,
      showTitle: false,
      config: {
        table: table,
        html: html,
        label: "Page Navigator",
        plain: true,
        autoFit: true,
        sidebar: true
      }
    };
  }

  /* Full-page raw-data table for a "Data" page. Header band + one big table
     widget that fills the rest of the canvas (offset right of the sidebar). */
  function tableWidgetFromPlan(plan, opts) {
    opts = opts || {};
    var table = plan.table || "";
    var W = opts.canvasW || 1920;
    var H = opts.canvasH || 1080;
    if (W >= H && Math.abs(W / H - 16 / 9) > 0.03) H = Math.round(W * 9 / 16);
    var SC = Math.min(1.35, Math.max(0.8, H / 720));
    var GAP = Math.round(12 * SC);
    var M = 16;
    var sbW = opts.sidebar ? (opts.sidebarW || 176) : 0;
    var X0 = M + sbW;
    var contentW = W - M * 2 - sbW;
    var headerH = Math.round(72 * SC);
    var accent = opts.accent || "#6366f1";
    var accent2 = opts.accent2 || "#1e293b";
    var widgets = [];
    var y = M;
    widgets.push(headerWidget(table, opts.title || "Data", "Data source: " + table + " · Raw data preview", {
      x: X0, y: y, width: contentW, height: headerH, index: 2, accent: accent, accent2: accent2
    }));
    y += headerH + GAP;
    var tableH = Math.max(120, (H - M) - y);
    widgets.push({
      id: _uid("tbl"),
      type: "table",
      title: "Data Preview",
      x: X0, y: y, width: contentW, height: tableH, zIndex: 3, locked: false,
      config: { table: table, sql: 'SELECT * FROM ' + _q(table) + ' LIMIT 100', xCol: "", yCol: "" }
    });
    return { widgets: widgets, chartCount: 0 };
  }

  function buildDashboardFromPlanPages(plan, opts, spec) {
    spec = spec || {};
    var n = Math.max(2, parseInt(spec.pages, 10) || 2);
    var focus = Array.isArray(spec.pageFocus) ? spec.pageFocus : [];
    var prompts = Array.isArray(spec.pagePrompt) ? spec.pagePrompt : [];
    var table = plan.table || "";
    var fams = { trend: [], breakdown: [], comparison: [], distribution: [], correlation: [], special: [], geo: [] };
    var zoneOrder = ["trend", "breakdown", "comparison", "distribution", "correlation", "special", "geo"];
    zoneOrder.forEach(function (k) { fams[k] = []; });
    (Array.isArray(plan.zones) ? plan.zones : []).forEach(function (zn) {
      (fams[zn.zone] ? fams[zn.zone] : fams.special).push(zn);
    });
    var kpis = Array.isArray(plan.kpis) ? plan.kpis.slice() : [];
    var filters = Array.isArray(plan.filters) ? plan.filters.slice() : [];
    var hasInsight = !!(plan.insights && (plan.insights.summary || (plan.insights.key_findings && plan.insights.key_findings.length)));
    var insights = hasInsight ? plan.insights : null;

    function pageTitle(label, prompt) {
      var t = String(prompt || "").trim();
      return t ? (t.length > 40 ? t.slice(0, 40) : t) : label;
    }
    function subPlan(name, zones) {
      return { table: table, title: name, kpis: kpis, filters: filters, zones: zones,
               insights: name === "Overview" ? insights : null };
    }
    function byFocus(list, fid) {
      var set = _PAGE_FOCUS_SETS[fid] || _PAGE_FOCUS_SETS.breakdown;
      return list.slice().sort(function (a, b) {
        var ra = set.indexOf(a.zone) === -1 ? 99 : set.indexOf(a.zone);
        var rb = set.indexOf(b.zone) === -1 ? 99 : set.indexOf(b.zone);
        return ra - rb;
      });
    }

    // Overview: KPI band + slicers + the key trends + insights, one chart row
    // so the page sits inside the canvas; everything else goes to the focus pages.
    var overviewZones = fams.trend.slice(0, 2);
    // Everything that did not land on the overview, in band order.
    var rest = [];
    zoneOrder.forEach(function (k) {
      if (k === "trend") return;
      fams[k].forEach(function (zn) { rest.push(zn); });
    });

    var pages = [{ id: "page_1", name: "Overview" }];
    for (var pi = 1; pi < n; pi++) {
      var fid = focus[pi] || (pi % 2 === 1 ? "breakdown" : "correlation");
      var label = _PAGE_FOCUS_LABELS[fid] || "Deep Dive";
      var prm = prompts[pi] || "";
      pages.push({ id: "page_" + (pi + 1), name: pageTitle(label, prm) });
    }

    var zonePlans = {};
    zonePlans[1] = overviewZones.slice(0, 4);
    if (n === 2) {
      var f2 = focus[1] || "breakdown";
      zonePlans[2] = f2 === "data" ? [] : byFocus(rest, f2).slice(0, 6);
    } else {
      // Data pages render a raw table and consume no chart zones, so spread the
      // zone budget only across the pages that actually show charts.
      var zonePageCount = 0;
      for (var _zi = 1; _zi < n; _zi++) {
        var _fid = focus[_zi] || (_zi % 2 === 1 ? "breakdown" : "correlation");
        if (_fid !== "data") zonePageCount++;
      }
      var perPage = Math.max(2, Math.floor(rest.length / Math.max(1, zonePageCount)));
      for (var zi = 2; zi <= n; zi++) {
        var fid = focus[zi - 1] || (zi % 2 === 0 ? "breakdown" : "correlation");
        // Data pages carry no chart zones — they render a full raw-data table.
        if (fid === "data") { zonePlans[zi] = []; continue; }
        var setF = _PAGE_FOCUS_SETS[fid] || _PAGE_FOCUS_SETS.breakdown;
        var assigned = [];
        for (var zk = 2; zk < zi; zk++) {
          (zonePlans[zk] || []).forEach(function (z) { assigned.push(z); });
        }
        zonePlans[zi] = rest.filter(function (zn) {
          if (assigned.indexOf(zn) !== -1) return false;
          return setF.indexOf(zn.zone) !== -1;
        }).slice(0, perPage);
        if (!zonePlans[zi].length) {
          var remaining = rest.filter(function (zn) { return assigned.indexOf(zn) === -1; });
          zonePlans[zi] = remaining.slice(0, perPage);
        }
      }
    }

    var outPages = [];
    var pageNames = pages.map(function (pg) { return pg.name; });
    var pageCanvasW = (opts && opts.canvasW) || 1920;
    var pageCanvasH = (opts && opts.canvasH) || 1080;
    if (pageCanvasW >= pageCanvasH && Math.abs(pageCanvasW / pageCanvasH - 16 / 9) > 0.03) pageCanvasH = Math.round(pageCanvasW * 9 / 16);
    var pageM = 16;
    var SBW = Math.round(Math.max(140, Math.min(200, pageCanvasW * 0.095)));
    pages.forEach(function (pg, pi) {
      var isOverview = pi === 0;
      var fid = focus[pi] || (pi % 2 === 1 ? "breakdown" : "correlation");
      var so = Object.assign({}, opts, {
        title: pg.name,
        pageZones: zonePlans[pi + 1] || [],
        chartBudget: isOverview ? 6 : 5,
        slim: !isOverview,
        sidebar: true,
        sidebarW: SBW
      });
      var res;
      if (fid === "data") {
        res = tableWidgetFromPlan(subPlan(pg.name, []), so);
      } else {
        res = buildDashboardFromPlan(subPlan(pg.name, zonePlans[pi + 1] || []), so);
      }
      var widgets = res.widgets;
      // Website-style left sidebar navigation: every page carries the same
      // locked sidebar (left edge, full height) so users navigate the report
      // in-canvas — matching the Power BI / website dashboard reference.
      widgets = widgets.concat([sidebarNavWidget(pageNames, pi, {
        x: 0, y: 0, width: SBW, height: pageCanvasH,
        index: widgets.length, accent: opts.accent, accent2: opts.accent2, theme: opts.theme, table: table
      })]);
      outPages.push({ id: pg.id, name: pg.name, widgets: widgets, chartCount: res.chartCount });
    });
    return { pages: outPages, cls: classifyColumns(table), chartCount: plan.zones ? plan.zones.length : outPages.length };
  }

  window.AIBuild = {
    buildChartSql: buildChartSql,
    chartWidgetFromRec: chartWidgetFromRec,
    layoutWidgets: layoutWidgets,
    insightString: insightString,
    insightTextWidget: insightTextWidget,
    recommendCharts: recommendCharts,
    fetchInsights: fetchInsights,
    classifyColumns: classifyColumns,
    kpiWidgets: kpiWidgets,
    slicerWidgets: slicerWidgets,
    headerWidget: headerWidget,
    insightPanelWidget: insightPanelWidget,
    buildAIDashboardLayout: buildAIDashboardLayout,
    buildDashboardFromPlan: buildDashboardFromPlan,
    buildDashboardFromPlanPages: buildDashboardFromPlanPages,
    pageNavigatorWidget: pageNavigatorWidget,
    sidebarNavWidget: sidebarNavWidget,
    tableWidgetFromPlan: tableWidgetFromPlan,
    chartWidgetFromPlan: chartWidgetFromPlan,
    kpiWidgetFromPlan: kpiWidgetFromPlan,
    dividerWidget: dividerWidget,
    dedupeRecommendations: dedupeRecommendations,
    relatedTableKeys: relatedTableKeys
  };
})();
