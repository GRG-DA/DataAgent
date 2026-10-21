/**
 * WidgetEditor — Modal-based widget configuration editor (Data/Display/Format/Layout tabs)
 *
 * Upgraded:
 *  - Data tab: live "Test query" preview (runs the SQL and shows rows or the exact error)
 *              and a WHERE-clause filter builder that round-trips with the SQL textarea.
 *  - Format tab: native <input type=color> pickers + the current dashboard theme's
 *                palette as one-click swatches.
 *  - Display tab: KPI formatting controls (prefix / suffix / decimals / K-M-B abbreviate /
 *                show-trend), wired into the running KPI renderers.
 *  - Layout tab: free-form blankdash geometry (X/Y/Width/Height in px, z-index, lock,
 *                background + border colour) — the old cols/rows fields did nothing on
 *                the blankdash canvas.
 */
(function () {
  "use strict";

  var _FILTER_OPS = [
    ["contains", "Contains"],
    ["ncontains", "Doesn't contain"],
    ["=", "Equals"],
    ["!=", "Not equals"],
    [">", "Greater than"],
    [">=", "At least"],
    ["<", "Less than"],
    ["<=", "At most"],
    ["empty", "Is empty"],
    ["nempty", "Is not empty"],
  ];

  function _tabBtn(id, label, icon, active) {
    return '<button class="tab-btn px-3 py-2 text-xs font-medium whitespace-nowrap border-b-2 transition-colors ' +
      (active
        ? "border-primary-500 text-primary-600 dark:text-primary-400"
        : "border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300") +
      '" data-tab-id="' + id + '">' +
      (icon ? '<i class="fas ' + icon + ' mr-1"></i>' : "") +
      label + "</button>";
  }

  function _open(widget, onSave) {
    if (!window.Modal) return;
    var config = JSON.parse(JSON.stringify(widget.config || {}));
    var title = widget.title || widget.type;
    var type = widget.type;

    var dataHtml = _buildDataTab(config, type, widget);
    var displayHtml = _buildDisplayTab(config, type);
    var formatHtml = _buildFormatTab(config, type);

    var content =
      '<div id="widget-editor-tabs">' +
      '<div class="flex border-b border-gray-200 dark:border-gray-700 overflow-x-auto">' +
      _tabBtn("wed-data", "Data", "fa-database", true) +
      _tabBtn("wed-display", "Display", "fa-palette", false) +
      _tabBtn("wed-format", "Format", "fa-sliders", false) +
      '</div>' +
      '<div class="tab-content pt-3">' +
      '<div id="wed-data" class="tab-panel" data-tab-panel="wed-data">' + dataHtml + '</div>' +
      '<div id="wed-display" class="tab-panel hidden" data-tab-panel="wed-display">' + displayHtml + '</div>' +
      '<div id="wed-format-panel" class="tab-panel hidden" data-tab-panel="wed-format">' + formatHtml + '</div>' +
      '</div>' +
      '</div>';

    var modal = window.Modal.show("Edit: " + _esc(title), content, { size: "lg", footer: '<button class="btn btn-secondary btn-sm" id="wed-duplicate">Duplicate</button><button class="btn btn-primary btn-sm" id="wed-save">Save</button><button class="btn btn-secondary btn-sm modal-close">Cancel</button>' });

    // Tabs are switched by the delegated click handler on #widget-editor-tabs
    // below — they only toggle hidden classes (NO innerHTML rebuild), so typed
    // field values and listeners survive switching. Bindings live on the stable
    // container regardless.
    var tabsContainer = document.getElementById("widget-editor-tabs");

    // ── Per-tab wiring ─────────────────────────────────────────────────
    // NOTE: bound via DELEGATION on the tabs container (a stable node). Tabs
    // are switched above by toggling hidden classes — never rebuilding
    // innerHTML — so this modal's values and this listener both survive
    // switching between Data / Display / Format / Layout.
    tabsContainer.addEventListener("click", function (e) {
      var t = e.target;
      if (!t.closest) return;

      // Tab switching: just toggle hidden classes so the panels keep their
      // values and listeners (no innerHTML rebuild).
      var tabBtn = t.closest("[data-tab-id]");
      if (tabBtn) {
        var tabId = tabBtn.dataset.tabId;
        var already = tabBtn.classList.contains("border-primary-500");
        if (tabId && !already) {
          tabsContainer.querySelectorAll("[data-tab-panel]").forEach(function (p) {
            p.classList.add("hidden");
          });
          var panel = tabsContainer.querySelector('[data-tab-panel="' + tabId + '"]');
          if (panel) panel.classList.remove("hidden");
          tabsContainer.querySelectorAll(".tab-btn").forEach(function (b) {
            var on = b.dataset.tabId === tabId;
            b.classList.toggle("border-primary-500", on);
            b.classList.toggle("text-primary-600", on);
            b.classList.toggle("dark:text-primary-400", on);
            b.classList.toggle("border-transparent", !on);
            b.classList.toggle("text-gray-500", !on);
            b.classList.toggle("hover:text-gray-700", !on);
            b.classList.toggle("dark:hover:text-gray-300", !on);
          });
        }
        return;
      }

      // Format tab: toggling a swatch marks it selected (drives _collectFormat)
      // and drops it into the first colour cell so colours show immediately.
      var sw = t.closest(".wed-fmt-swatch");
      if (sw) {
        var on = sw.dataset.on === "1" ? "0" : "1";
        sw.dataset.on = on;
        sw.style.borderColor = on === "1" ? "#0f172a" : "transparent";
        if (on === "1") {
          var first = document.querySelector(".wed-fmt-hex[data-i='0']");
          var cp0 = document.querySelector(".wed-fmt-cpick[data-i='0']");
          if (first) first.value = sw.dataset.fmtColor || sw.getAttribute("data-fmt-color");
          if (cp0) cp0.value = sw.dataset.fmtColor || sw.getAttribute("data-fmt-color");
        }
        return;
      }

      // Filter builder: remove a row
      var del = t.closest(".wed-fdel");
      if (del) { del.closest(".wed-frow").remove(); _patchSqlFromFilters(); return; }

      // Filter builder: add a row
      if (t.closest("#wed-filter-add")) {
        _appendFilterRow(document.getElementById("wed-filters"), config.table || "", "");
        _patchSqlFromFilters();
        return;
      }

      // Test-query preview
      if (t.closest("#wed-test")) { _previewQuery(); return; }

      // Generate-SQL panel controls
      if (t.closest("#wed-gen-sql-btn")) {
        var gp = document.getElementById("wed-gen-sql-prompt");
        if (gp) {
          gp.classList.toggle("hidden");
          if (!gp.classList.contains("hidden")) {
            var gi = document.getElementById("wed-gen-sql-input");
            if (gi) gi.focus();
          }
        }
        return;
      }
      if (t.closest("#wed-gen-sql-cancel")) {
        var gp2 = document.getElementById("wed-gen-sql-prompt");
        if (gp2) gp2.classList.add("hidden");
        return;
      }
      if (t.closest("#wed-gen-sql-go")) { runGen(); return; }
    });

    // Field changes / colour-picker sync / filter value typing (delegated)
    // Colour picker ⇄ hex pairing is by data-i index (they live in separate
    // subtrees of the panel), falling back to same-cell proximity.
    tabsContainer.addEventListener("input", function (e) {
      var t = e.target;
      if (!t.classList) return;
      if (t.classList.contains("wed-fmt-cpick")) {
        var target = null;
        if (t.dataset.sync) target = document.getElementById(t.dataset.sync);
        else {
          if (t.dataset.i != null) target = document.querySelector(".wed-fmt-hex[data-i='" + t.dataset.i + "']");
          if (!target) {
            var cell = t.closest(".wed-fmt-color-cell");
            if (cell) target = cell.querySelector(".wed-fmt-hex");
          }
        }
        if (target) target.value = t.value.toUpperCase();
        return;
      }
      if (t.classList.contains("wed-fmt-hex")) {
        var v = t.value.trim();
        if (/^#[0-9a-fA-F]{6}$/.test(v)) {
          var cpx = null;
          if (t.dataset.i != null) cpx = document.querySelector(".wed-fmt-cpick[data-i='" + t.dataset.i + "']");
          if (!cpx) {
            var cell2 = t.closest(".wed-fmt-color-cell");
            if (cell2) cpx = cell2.querySelector(".wed-fmt-cpick");
          }
          if (cpx) cpx.value = v;
        }
        return;
      }
      if (t.closest && t.closest(".wed-fval")) _patchSqlFromFilters();
    });

    // Select changes: table→column refresh + filter operator/column picks
    tabsContainer.addEventListener("change", function (e) {
      var t = e.target;
      if (!t.closest) return;
      if (t.id === "wed-table") {
        config.table = t.value;
        _refreshColOptions(config);
        _refreshFilterCols(config.table);
        return;
      }
      if (t.closest(".wed-fop") || t.closest(".wed-fcol")) _patchSqlFromFilters();
      if (t.id === "wed-callevel") _applyCalendarGroup();
    });

    // Keyboard shortcuts: Ctrl+Enter to run the test query, Enter in the
    // generate-SQL prompt.
    tabsContainer.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.id === "wed-sql") {
        e.preventDefault();
        _previewQuery();
        return;
      }
      if (e.key === "Enter" && e.target.id === "wed-gen-sql-input") {
        e.preventDefault();
        runGen();
      }
    });

    // Duplicate handler — copies this widget as a new one via the page's hook
    var dupBtn = document.getElementById("wed-duplicate");
    if (dupBtn) {
      dupBtn.addEventListener("click", function() {
        _collectData(config, type);
        _collectDisplay(config, type);
        _collectFormat(config, type);
        widget.config = config;
        if (window.BIDash && BIDash.duplicateWidget) BIDash.duplicateWidget(widget);
        modal.close();
      });
    }

    // Save handler
    var saveBtn = document.getElementById("wed-save");
    if (saveBtn) {
      saveBtn.addEventListener("click", function() {
        _collectData(config, type);
        _collectDisplay(config, type);
        _collectFormat(config, type);
        // Auto-generate a default SQL when the user configured fields but left
        // the query empty, so a freshly-added widget renders immediately.
        if (!config.sql && config.table && window.App && App.state && App.state.tables) {
          var _ti = App.state.tables[config.table] || {};
          var _cols = (_ti.columns || []).map(function(c) {
            return typeof c === "string" ? c : (c.column_name || c.name || "");
          }).filter(Boolean);
          var _ct = {};
          (_ti.columns || []).forEach(function(c) {
            if (typeof c === "object") { var n = c.column_name || c.name || ""; _ct[n] = c.column_type || c.type || ""; }
          });
          config.sql = _generateSQL("", config.table, _cols, _ct, type, {
            agg: config.agg, sort: config.sort, limit: config.limit,
            series: config.series, stacked: config.stacked,
          });
        }
        widget.config = config;
        if (onSave) onSave(widget);
        modal.close();
      });
    }

    // ── Generate SQL button ──
    // Invoked via the delegated tab-container handlers above (click → runGen,
    // Enter in the prompt → runGen). Re-queries elements each call because the
    // panel nodes are rebuilt on every tab switch.
    function runGen() {
      var genPrompt = document.getElementById("wed-gen-sql-prompt");
      var genInput = document.getElementById("wed-gen-sql-input");
      var genGo = document.getElementById("wed-gen-sql-go");
      var genLoading = document.getElementById("wed-gen-sql-loading");
      if (!genInput) return;
      var prompt = genInput.value.trim();
      if (!prompt) return;
      var table = config.table || "";
      if (!table) {
        if (window.App) App.warning("Select a table first.");
        return;
      }
      if (genLoading) genLoading.classList.remove("hidden");
      if (genGo) genGo.disabled = true;

      // Build schema context for the generator
      var tables = (window.App && App.state && App.state.tables) || {};
      var tableInfo = tables[table] || {};
      var columns = (tableInfo.columns || []).map(function(c) {
        return typeof c === "string" ? c : (c.column_name || c.name || "");
      }).filter(Boolean);
      var colTypes = {};
      (tableInfo.columns || []).forEach(function(c) {
        if (typeof c === "object") {
          var cn = c.column_name || c.name || "";
          colTypes[cn] = c.column_type || c.type || "";
        }
      });

      // Build schema context string for LLM
      var schemaCtx = "TABLE " + table + "\nColumns:\n";
      columns.forEach(function(c) {
        schemaCtx += "  - " + c + " (" + (colTypes[c] || "unknown") + ")\n";
      });

      // Try LLM first, fall back to local generator
      var base = window.__API_BASE__ || "";
      var llmPayload = {
        prompt: "Write a DuckDB SQL query for this task: " + prompt +
          "\n\nWidget type: " + type +
          "\nTable: " + table +
          "\nColumns: " + columns.join(", ") +
          "\n\nRules:\n" +
          "- Return ONLY the SQL query, no explanation\n" +
          "- Use DuckDB syntax\n" +
          "- Use double quotes for identifiers\n" +
          "- For aggregation, use appropriate GROUP BY\n" +
          "- For trends, use date_trunc on date columns\n" +
          "- Quote string and date values in WHERE conditions with single quotes\n" +
          "- LIMIT results to 20 rows for GROUP BY queries\n" +
          "- The result columns should be named 'label' and 'value' for charts, or appropriate names for KPIs/tables",
        language: "sql",
        context: schemaCtx,
      };

      fetch(base + "/llm/generate-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(llmPayload),
      }).then(function(r) { return r.json(); }).then(function(result) {
        var sql = "";
        if (result && result.success && result.code) {
          sql = result.code.trim();
        } else {
          // LLM unavailable — fall back to local generator
          sql = _generateSQL(prompt, table, columns, colTypes, type, _domOpts());
        }
        var sqlEl = document.getElementById("wed-sql");
        if (sqlEl) sqlEl.value = sql;
        config.sql = sql;
        _syncFilterRowsFromSql(sql, table);
        var gp2 = document.getElementById("wed-gen-sql-prompt");
        if (gp2) gp2.classList.add("hidden");
        if (window.App) App.success("SQL generated" + (result && result.success ? " (via LLM)" : " (local)"));
      }).catch(function() {
        // Network error — fall back to local generator
        var sql = _generateSQL(prompt, table, columns, colTypes, type, _domOpts());
        var sqlEl = document.getElementById("wed-sql");
        if (sqlEl) sqlEl.value = sql;
        config.sql = sql;
        _syncFilterRowsFromSql(sql, table);
        var gp3 = document.getElementById("wed-gen-sql-prompt");
        if (gp3) gp3.classList.add("hidden");
        if (window.App) App.success("SQL generated (local fallback)");
      }).finally(function() {
        var gl = document.getElementById("wed-gen-sql-loading");
        var gg = document.getElementById("wed-gen-sql-go");
        var gis = document.getElementById("wed-gen-sql-input");
        if (gl) gl.classList.add("hidden");
        if (gg) gg.disabled = false;
        if (gis) gis.value = "";
      });
    }
  }

  function _aggOptions(sel) {
    var aggs = ["SUM", "AVG", "COUNT", "MIN", "MAX"];
    return aggs.map(function(a) {
      return '<option value="' + a + '" ' + (a === sel ? "selected" : "") + '>' + a + '</option>';
    }).join("");
  }

  function _sortOptions(sel) {
    var sorts = [
      ["value_desc", "Value (high → low)"],
      ["value_asc", "Value (low → high)"],
      ["label_asc", "Label (A → Z)"],
      ["label_desc", "Label (Z → A)"],
    ];
    return sorts.map(function(s) {
      return '<option value="' + s[0] + '" ' + (s[0] === sel ? "selected" : "") + '>' + s[1] + '</option>';
    }).join("");
  }

  /* One labelled editor field (used by the filter-widget sections below). */
  function _wedField(label, inner, hint) {
    return '<div><label class="form-label">' + label + '</label>' + inner +
      (hint ? '<div class="text-[10px] text-gray-400 mt-1">' + hint + '</div>' : '') + '</div>';
  }

  var _DISPLAY_EXEMPT = ["text", "image", "custom", "page", "button", "daterange", "searchbox", "slicer", "geomap", "countrymap"];
  function _numCapable(type) {
    return _DISPLAY_EXEMPT.indexOf(type) === -1;
  }

  function _sqlRelevant(type) {
    return type !== "text" && type !== "image" && type !== "page" && type !== "button" && type !== "custom";
  }

  function _buildDataTab(config, type, widget) {
    var tables = (window.App && App.state && App.state.tables) || {};
    var tableNames = Object.keys(tables);
    var currentTable = config.table || "";
    var sql = config.sql || "";

    // Build table options
    var tableOpts = '<option value="">Select table...</option>';
    tableNames.forEach(function(t) {
      tableOpts += '<option value="' + _esc(t) + '" ' + (currentTable === t ? "selected" : "") + '>' + _esc(t) + '</option>';
    });

    // Build column options + numeric column list
    var colOpts = '<option value="">Auto-detect</option>';
    var numericCols = [];
    if (currentTable && tables[currentTable] && tables[currentTable].columns) {
      tables[currentTable].columns.forEach(function(c) {
        var cn = typeof c === "string" ? c : (c.column_name || c.name || "");
        if (!cn) return;
        var ct = typeof c === "object" ? (c.column_type || c.type || "") : "";
        if (/int|float|double|decimal|numeric|bigint|number|real/i.test(ct)) numericCols.push(cn);
        colOpts += '<option value="' + _esc(cn) + '" ' + ((config.xCol === cn || config.yCol === cn) ? 'style="font-weight:600"' : '') + '>' + _esc(cn) + '</option>';
      });
    }

    var isChart = type !== "kpi" && type !== "gauge" && type !== "text" && type !== "table";
    var html = '<div class="space-y-3 text-sm">';

    // ── Table selector ──
    html += '<div>';
    html += '<label class="form-label">Data Source</label>';
    html += '<select class="form-input w-full text-xs" id="wed-table">' + tableOpts + '</select>';
    html += '</div>';

    // ── Column pickers (for chart types) ──
    if (isChart) {
      html += '<div class="grid grid-cols-2 gap-2">';
      html += '<div><label class="form-label">X axis / Label</label>';
      html += '<select class="form-input w-full text-xs" id="wed-xcol">' + colOpts + '</select></div>';
      html += '<div><label class="form-label">Y axis / Value</label>';
      html += '<select class="form-input w-full text-xs" id="wed-ycol">' + colOpts + '</select></div>';
      html += '</div>';
    }

    // ── Aggregation / sort / limit (charts + KPI) ──
    if (isChart || type === "kpi") {
      html += '<div class="grid grid-cols-3 gap-2">';
      html += '<div><label class="form-label">Aggregation</label>';
      html += '<select class="form-input w-full text-xs" id="wed-agg">' + _aggOptions(config.agg || "SUM") + '</select></div>';
      html += '<div><label class="form-label">Sort</label>';
      html += '<select class="form-input w-full text-xs" id="wed-sort">' + _sortOptions(config.sort || "value_desc") + '</select></div>';
      html += '<div><label class="form-label">Limit</label>';
      html += '<input class="form-input w-full text-xs" id="wed-limit" type="number" min="0" value="' + _esc(config.limit != null ? config.limit : 12) + '"></div>';
      html += '</div>';
    }

    // ── P3.11 — Calendar.Date hierarchy group-by (Year > Quarter > Month > Day).
    // Groups the chart by a level of the analytics.calendar dimension when the
    // X axis is a DATE/TIMESTAMP column, instead of raw date values.
    if (isChart) {
      var lvlOpts = '<option value="">None (raw dates)</option>';
      ["Year", "Quarter", "Month", "Day"].forEach(function (L) {
        lvlOpts += '<option value="' + L + '" ' + (config.hierarchyLevel === L ? "selected" : "") + '>' + L + ' ‣ Calendar</option>';
      });
      html += '<div>';
      html += '<label class="form-label"><i class="fas fa-calendar-days"></i> Calendar.Date hierarchy <span class="text-gray-400 font-normal">(join analytics.calendar)</span></label>';
      html += '<select class="form-input w-full text-xs" id="wed-callevel">' + lvlOpts + '</select>';
      html += '<div class="text-[10px] text-gray-400 mt-1">Pick a DATE/TIMESTAMP column on the X axis, then group Sales by a calendar level (drill Year › Quarter › Month › Day on the widget).</div>';
      html += '</div>';
    }

    // ── Additional series (multi-series charts + combo/radar) ──
    if (type === "bar" || type === "column" || type === "line" || type === "area" || type === "radar" || type === "combo") {
      var series = config.series || [];
      var seriesBoxes = numericCols.map(function(cn) {
        var on = series.indexOf(cn) !== -1 || (series.length === 0 && cn === config.yCol);
        return '<label class="inline-flex items-center gap-1 mr-2 text-xs"><input type="checkbox" class="wed-series" value="' + _esc(cn) + '" ' + (on ? "checked" : "") + '> ' + _esc(cn) + '</label>';
      }).join("");
      html += '<div>';
      html += '<label class="form-label">Series (additional measures)</label>';
      html += '<div>' + (seriesBoxes || '<span class="text-[10px] text-gray-400">no numeric columns in this table</span>') + '</div>';
      html += '</div>';
    }

    // ── Stacked (bar/column/area) ──
    if (type === "bar" || type === "column" || type === "area") {
      html += '<label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-stacked" ' + (config.stacked ? "checked" : "") + '> <span>Stacked</span></label>';
    }

    // ── Special role columns ──
    if (type === "heatmap") {
      html += '<div><label class="form-label">Heatmap category (rows)</label>';
      html += '<select class="form-input w-full text-xs" id="wed-categorycol">' + colOpts + '</select></div>';
    }
    if (type === "treemap") {
      html += '<div><label class="form-label">Treemap details (second dimension)</label>';
      html += '<select class="form-input w-full text-xs" id="wed-detailscol">' + colOpts + '</select></div>';
    }
    if (type === "bubble") {
      html += '<div><label class="form-label">Bubble size (measure)</label>';
      html += '<select class="form-input w-full text-xs" id="wed-sizecol">' + colOpts + '</select></div>';
    }

    // ── KPI-specific fields ──
    if (type === "kpi") {
      html += '<div>';
      html += '<label class="form-label">Trend / sparkline SQL <span class="text-gray-400 font-normal">(optional)</span></label>';
      html += '<textarea class="form-input w-full font-mono text-xs" id="wed-sparksql" rows="2" placeholder="SELECT strftime(...) AS label, SUM(...) AS value FROM ...">' + _esc(config.sparkSql || "") + '</textarea>';
      html += '</div>';
    }

    // ── Text widget content ──
    if (type === "text") {
      html += '<div>';
      html += '<label class="form-label">Text content <span class="text-gray-400 font-normal">(plain text)</span></label>';
      html += '<textarea class="form-input w-full text-sm" id="wed-content" rows="4" placeholder="Write your text here...">' + _esc(config.content || config.label || "") + '</textarea>';
      html += '<p class="text-[10px] text-gray-400 mt-1">Tip: double-click the text on the canvas to edit it directly.</p>';
      html += '</div>';
    }

    var showSqlTools = _sqlRelevant(type);

    // ── Filter-widget specific fields ──
    if (type === "slicer") {
      html += _wedField("Filter column", '<input class="form-input w-full text-xs" id="wed-slicercol" value="' + _esc(config.slicerColumn || "") + '" placeholder="e.g. Region">', "Blank = auto-detect");
      html += '<label class="checkbox-label flex items-center gap-2 mt-1"><input type="checkbox" id="wed-slicercascade" ' + (config.slicerCascade === true ? "checked" : "") + '> <span>Dependent on other filters (cascading options)</span></label>';
    }
    if (type === "numrange") {
      html += _wedField("Number column", '<input class="form-input w-full text-xs" id="wed-numcol" value="' + _esc(config.numCol || config.numColumn || "") + '" placeholder="e.g. Amount">', "Blank = auto-detect the numeric column");
    }
    if (type === "hierarchy") {
      html += _wedField("Hierarchy columns", '<input class="form-input w-full text-xs" id="wed-hcols" value="' + _esc(config.hCols || config.hierarchyCols || "") + '" placeholder="Region,Country,City">', "Comma-separated drill-down columns, deepest level last");
    }
    if (type === "relativedate") {
      html += _wedField("Date column", '<input class="form-input w-full text-xs" id="wed-datecol" value="' + _esc(config.dateColumn || "") + '" placeholder="e.g. Date">', "Blank = auto-detect the date column");
      html += _wedField("Presets", '<input class="form-input w-full text-xs" id="wed-presets" value="' + _esc(config.datePresets || "") + '" placeholder="today,last 7 days,ytd,last 12 months">', "Comma-separated: today, yesterday, last n days, this/last week|month|quarter|year, ytd, mtd, qtd, last 12 months");
    }
    if (type === "topn") {
      html += _wedField("Label column", '<input class="form-input w-full text-xs" id="wed-topncol" value="' + _esc(config.topnColumn || "") + '" placeholder="e.g. Product">', "Blank = auto-detect");
      html += _wedField("Ranking measure", '<input class="form-input w-full text-xs" id="wed-topnmeasure" value="' + _esc(config.topnMeasure || "") + '" placeholder="SUM(\"Amount\")">', "Blank = first numeric column, summed");
      html += '<div class="grid grid-cols-2 gap-2">' +
        '<div><label class="form-label">Which</label><select class="form-input w-full text-xs" id="wed-topnmode">' +
        '<option value="top"' + (config.topnMode !== "bottom" ? " selected" : "") + '>Top N</option>' +
        '<option value="bottom"' + (config.topnMode === "bottom" ? " selected" : "") + '>Bottom N</option>' +
        '</select></div>' +
        '<div><label class="form-label">N</label><input class="form-input w-full text-xs" id="wed-topnn" type="number" min="1" max="200" value="' + _esc(config.topnN || 10) + '"></div>' +
        '</div>';
    }
    if (type === "filterpane") {
      html += _wedField("Fields", '<input class="form-input w-full text-xs" id="wed-panefields" value="' + _esc(config.paneFields || "") + '" placeholder="Region,Category,Status">', "Comma-separated columns shown as small filter blocks");
    }
    if (type === "globalsearch") {
      html += _wedField("Search placeholder", '<input class="form-input w-full text-xs" id="wed-globalph" value="' + _esc(config.searchPlaceholder || "Search across all columns...") + '">', "Searches every text column of the widget's table");
    }

    // ── SQL Query (always visible, read+write) ──
    html += '<div>';
    html += '<div class="flex items-center justify-between mb-1">';
    html += '<label class="form-label mb-0">SQL Query <span class="text-gray-400 font-normal">(DuckDB syntax)</span></label>';
    html += '<button type="button" id="wed-gen-sql-btn" class="text-[11px] px-2 py-0.5 rounded bg-purple-50 text-purple-600 border border-purple-200 hover:bg-purple-100 font-medium transition-colors">';
    html += '<i class="fas fa-wand-magic-sparkles mr-1"></i>Generate SQL</button>';
    html += '</div>';
    html += '<div id="wed-gen-sql-prompt" class="hidden mb-2">';
    html += '<div class="flex gap-1.5">';
    html += '<input id="wed-gen-sql-input" class="form-input flex-1 text-xs" type="text" placeholder="e.g. total revenue by region, top 10 products, monthly trend…">';
    html += '<button type="button" id="wed-gen-sql-go" class="text-xs px-2.5 py-1 rounded bg-purple-500 text-white hover:bg-purple-600 font-medium shrink-0">Go</button>';
    html += '<button type="button" id="wed-gen-sql-cancel" class="text-xs px-2 py-1 rounded text-gray-400 hover:text-gray-600">✕</button>';
    html += '</div>';
    html += '<div id="wed-gen-sql-loading" class="hidden text-xs text-gray-400 mt-1"><i class="fas fa-spinner fa-spin mr-1"></i>Generating…</div>';
    html += '</div>';
    html += '<textarea class="form-input w-full font-mono text-xs" id="wed-sql" rows="4" placeholder="SELECT ... FROM table_name">' + _esc(sql) + '</textarea>';
    if (showSqlTools) {
      html += '<div class="flex items-center gap-2 mt-1.5">';
      html += '<button type="button" id="wed-test" class="text-[11px] px-2.5 py-1 rounded bg-emerald-50 text-emerald-600 border border-emerald-200 hover:bg-emerald-100 font-medium transition-colors"><i class="fas fa-play mr-1"></i>Test query</button>';
      html += '<span id="wed-test-status" class="text-[11px] text-gray-400"></span>';
      html += '</div>';
      html += '<div id="wed-preview" class="mt-2"></div>';
    }
    html += '</div>';

    // ── WHERE-clause filter builder ──
    if (showSqlTools) {
      var rows = _rowsFromSql(sql);
      var manualWhere = (rows === null);
      html += '<div id="wed-filters-wrap">';
      html += '<div class="flex items-center justify-between mb-1">';
      html += '<label class="form-label mb-0">Filters <span class="text-gray-400 font-normal">(WHERE builder)</span></label>';
      html += '<button type="button" id="wed-filter-add" class="text-[11px] px-2 py-0.5 rounded bg-indigo-50 text-indigo-600 border border-indigo-200 hover:bg-indigo-100 font-medium transition-colors">+ Add filter</button>';
      html += '</div>';
      html += '<div id="wed-filters" class="space-y-1.5"></div>';
      html += '<div id="wed-filter-note" class="' + (manualWhere ? '' : 'hidden') + ' text-[10px] text-amber-600 mt-1"><i class="fas fa-triangle-exclamation mr-1"></i>Manual WHERE clause detected on this query — the builder is read-only until it is removed. Filters you add are still previewed in the SQL below.</div>';
      html += '</div>';
    }

    html += '</div>';
    return html;
  }

  function _buildDisplayTab(config, type) {
    var title = config.title || "";
    var html = '<div class="space-y-3 text-sm">' +
      '<label class="form-label">Widget Title</label><input class="form-input" id="wed-title" type="text" value="' + _esc(title) + '">' +
      '<p class="text-[10px] text-gray-400">Number format, value formatting, KPI style and all styling now live in the <strong>Properties</strong> dock (select the widget). Series colours, legend, data labels, axes and card styling remain on the <strong>Format</strong> tab.</p>' +
      '</div>';
    return html;
  }

  // ── Format tab ── advanced styling, persisted as config.style and exported
  // to the .pbix via export/pbix_export.py::_style_objects.
  function _buildFormatTab(config, type) {
    var style = config.style || {};
    var colors = style.colors || (config.color ? [config.color] : []);
    var legend = style.legend || {};
    var labels = style.dataLabels || {};
    var axis = style.axis || {};
    var bg = style.background || "";
    var title = style.title || {};
    var palette = _themePalette();
    var swatches = palette.map(function (c) {
      var on = colors.indexOf(c) !== -1;
      return '<button type="button" class="wed-fmt-swatch" data-fmt-color="' + c + '" data-on="' + (on ? "1" : "0") + '" style="width:22px;height:22px;border-radius:5px;background:' + c + ';border:2px solid ' + (on ? "#0f172a" : "transparent") + ';cursor:pointer;outline:none;" title="' + c + '"></button>';
    }).join("");
    var hexCell = function (i) {
      var val = colors[i] || "";
      return '<div class="grid grid-cols-2 gap-2 items-center">' +
        '<div><label class="form-label">Colour ' + (i + 1) + "</label>" +
          '<input class="form-input wed-fmt-hex" data-i="' + i + '" type="text" value="' + _esc(val) + '" placeholder="#118DFF"></div>' +
        '<div class="wed-fmt-color-cell pt-1"><label class="form-label opacity-0">pick</label>' +
          '<input type="color" class="wed-fmt-cpick" data-i="' + i + '" value="' + _esc(val || '#118DFF') + '" style="width:100%;height:30px;padding:0;border:1px solid var(--color-card-border,#e2e8f0);border-radius:6px;background:none;cursor:pointer;"></div>' +
      '</div>';
    };
    var posOpts = function (sel, cur, list) {
      return list.map(function (p) {
        return '<option value="' + p + '"' + (cur === p ? " selected" : "") + ">" + p + "</option>";
      }).join("");
    };

    var typeExtras = "";
    if (["watertank", "progress", "radial", "targetactual"].indexOf(type) !== -1) {
      typeExtras += '<div class="border-t pt-3 mt-1"><div><label class="form-label">Target value <span class="text-gray-400 font-normal">(overrides the auto goal)</span></label>' +
        '<input class="form-input" id="wed-fmt-target" type="number" step="any" value="' + _esc((typeof config.target === "number") ? config.target : "") + '" placeholder="auto"></div></div>';
    }
    if (type === "waffle") {
      var wfCur = (style.waffleGrid === 64 || style.waffleGrid === 40) ? style.waffleGrid : 100;
      typeExtras += '<div class="border-t pt-3 mt-1"><div><label class="form-label">Grid size</label><select class="form-input w-full" id="wed-fmt-waffle">' +
        '<option value="100"' + (wfCur === 100 ? " selected" : "") + '>10 × 10 (100 cells)</option>' +
        '<option value="64"' + (wfCur === 64 ? " selected" : "") + '>8 × 8 (64 cells)</option>' +
        '<option value="40"' + (wfCur === 40 ? " selected" : "") + '>5 × 8 (40 cells)</option>' +
      '</select></div></div>';
    }
    if (type === "lollipop" || type === "diverging") {
      var posDef = type === "lollipop" ? "#3B82F6" : "#10B981";
      var negDef = type === "lollipop" ? "#F87171" : "#EF4444";
      typeExtras += '<div class="border-t pt-3 mt-1">' +
        '<div class="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">Direction colours</div>' +
        '<div class="wed-fmt-color-cell mb-2"><div class="grid grid-cols-2 gap-2 items-center">' +
          '<div><label class="form-label">Positive</label><input class="form-input wed-fmt-hex" id="wed-fmt-pos" type="text" value="' + _esc(style.positiveColor || "") + '" placeholder="' + posDef + '"></div>' +
          '<div class="pt-1"><label class="form-label opacity-0">pick</label><input type="color" class="wed-fmt-cpick" value="' + _esc(style.positiveColor || posDef) + '" style="width:100%;height:30px;padding:0;border:1px solid var(--color-card-border,#e2e8f0);border-radius:6px;background:none;cursor:pointer;"></div>' +
        '</div></div>' +
        '<div class="wed-fmt-color-cell"><div class="grid grid-cols-2 gap-2 items-center">' +
          '<div><label class="form-label">Negative</label><input class="form-input wed-fmt-hex" id="wed-fmt-neg" type="text" value="' + _esc(style.negativeColor || "") + '" placeholder="' + negDef + '"></div>' +
          '<div class="pt-1"><label class="form-label opacity-0">pick</label><input type="color" class="wed-fmt-cpick" value="' + _esc(style.negativeColor || negDef) + '" style="width:100%;height:30px;padding:0;border:1px solid var(--color-card-border,#e2e8f0);border-radius:6px;background:none;cursor:pointer;"></div>' +
        '</div></div>' +
      '</div>';
    }
    if (type === "ranking") {
      typeExtras += '<div class="border-t pt-3 mt-1"><label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-fmt-medals" ' + (style.medals !== false ? "checked" : "") + '> <span>Show medals on top 3 rows</span></label></div>';
    }

    var shadowVal = "None";
    if (style.shadow) {
      shadowVal = style.shadow.indexOf("1px 3px") !== -1 ? "Small" : style.shadow.indexOf("4px 12px") !== -1 ? "Medium" : style.shadow.indexOf("8px 24px") !== -1 ? "Large" : "Custom";
    }

    var chartBlocks = "";
    if (_numCapable(type)) {
      chartBlocks =
        '<div><label class="form-label">Series colours <span class="text-gray-400 font-normal">(data labels, legend & axes)</span></label>' +
          '<div class="flex flex-wrap gap-1.5">' + swatches + "</div></div>" +
        '<div class="space-y-2">' + [0, 1, 2].map(hexCell).join("") + "</div>" +
        '<div class="grid grid-cols-2 gap-2 items-end">' +
          '<label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-fmt-legend" ' + (legend.show !== false ? "checked" : "") + "> <span>Show legend</span></label>" +
          '<div><label class="form-label">Legend position</label><select class="form-input w-full" id="wed-fmt-legend-pos">' + posOpts("pos", legend.position, ["top", "bottom", "left", "right"]) + "</select></div>" +
        "</div>" +
        '<div class="grid grid-cols-2 gap-2 items-end">' +
          '<label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-fmt-labels" ' + (labels.show ? "checked" : "") + "> <span>Show data labels</span></label>" +
          '<div><label class="form-label">Label position</label><select class="form-input w-full" id="wed-fmt-labels-pos">' + posOpts("pos", labels.position, ["outsideEnd", "insideEnd", "center", "insideBase"]) + "</select></div>" +
        "</div>" +
        '<div class="grid grid-cols-2 gap-2">' +
          '<div><label class="form-label">X-axis title</label><input class="form-input" id="wed-fmt-axis-x" type="text" value="' + _esc(axis.categoryTitle || "") + '"></div>' +
          '<div><label class="form-label">Y-axis title</label><input class="form-input" id="wed-fmt-axis-y" type="text" value="' + _esc(axis.valueTitle || "") + '"></div>' +
        "</div>" +
        '<label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-fmt-grid" ' + (axis.showGrid !== false ? "checked" : "") + "> <span>Show value-axis gridlines</span></label>";
    }

    return '<div class="space-y-3 text-sm" id="wed-format-pane">' +
      chartBlocks +
      '<div class="grid grid-cols-3 gap-2 items-end">' +
        '<div><label class="form-label">Background</label>' +
          '<div class="flex gap-1"><input class="form-input" id="wed-fmt-bg" type="text" value="' + _esc(bg) + '" placeholder="auto">' +
          '<input type="color" class="wed-fmt-cpick wed-fmt-cpick-single" data-sync="wed-fmt-bg" value="' + _esc(bg || '#ffffff') + '" style="width:32px;height:30px;padding:0;border:1px solid var(--color-card-border,#e2e8f0);border-radius:6px;background:none;cursor:pointer;"></div></div>' +
        '<div><label class="form-label">Title colour</label>' +
          '<div class="flex gap-1"><input class="form-input" id="wed-fmt-title-color" type="text" value="' + _esc(title.color || "") + '" placeholder="auto">' +
          '<input type="color" class="wed-fmt-cpick wed-fmt-cpick-single" data-sync="wed-fmt-title-color" value="' + _esc(title.color || '#ffffff') + '" style="width:32px;height:30px;padding:0;border:1px solid var(--color-card-border,#e2e8f0);border-radius:6px;background:none;cursor:pointer;"></div></div>' +
        '<div><label class="form-label">Title size</label><input class="form-input" id="wed-fmt-title-size" type="number" min="8" max="40" value="' + _esc(title.fontSize || "12") + '"></div>' +
      "</div>" +
      '<div class="grid grid-cols-3 gap-2 items-end">' +
        '<div><label class="form-label">Title alignment</label><select class="form-input w-full" id="wed-fmt-title-align">' + posOpts("align", title.align, ["left", "center", "right"]) + "</select></div>" +
        '<div><label class="form-label">Corner radius</label><input class="form-input" id="wed-fmt-radius" type="number" min="0" max="32" value="' + _esc((typeof style.borderRadius === "number") ? style.borderRadius : "") + '" placeholder="0"></div>' +
        '<div><label class="form-label">Shadow</label><select class="form-input w-full" id="wed-fmt-shadow">' + posOpts("shadow", shadowVal, ["None", "Small", "Medium", "Large", "Custom"]) + "</select></div>" +
      "</div>" +
      typeExtras +
      (type === "kpi" && _sqlRelevant(type)
        ? '<p class="text-[10px] text-gray-400">The first colour is used as the KPI card background in the export.</p>'
        : "") +
      "</div>";
  }

  function _collectFormat(config, type) {
    var style = {};
    var colors = [];
    document.querySelectorAll(".wed-fmt-hex").forEach(function (inp) {
      if (inp.id === "wed-fmt-pos" || inp.id === "wed-fmt-neg") return;
      var v = inp.value.trim();
      if (v) colors.push(v);
    });
    document.querySelectorAll(".wed-fmt-swatch").forEach(function (sw) {
      if (sw.dataset.on === "1") {
        var c = sw.dataset.fmtColor || sw.getAttribute("data-fmt-color");
        if (colors.indexOf(c) === -1) colors.push(c);
      }
    });
    if (colors.length) style.colors = colors;

    var lg = document.getElementById("wed-fmt-legend");
    if (lg) {
      style.legend = { show: lg.checked, position: (document.getElementById("wed-fmt-legend-pos") || {}).value || "top" };
    }
    var dl = document.getElementById("wed-fmt-labels");
    if (dl) {
      style.dataLabels = { show: dl.checked, position: (document.getElementById("wed-fmt-labels-pos") || {}).value || "outsideEnd" };
    }
    var ax = document.getElementById("wed-fmt-axis-x");
    var ay = document.getElementById("wed-fmt-axis-y");
    var grid = document.getElementById("wed-fmt-grid");
    if (ax || ay || grid) {
      var a = {};
      if (ax && ax.value.trim()) a.categoryTitle = ax.value.trim();
      if (ay && ay.value.trim()) a.valueTitle = ay.value.trim();
      if (grid) a.showGrid = grid.checked;
      style.axis = a;
    }
    var bg = document.getElementById("wed-fmt-bg");
    if (bg && bg.value.trim()) style.background = bg.value.trim();
    var tc = document.getElementById("wed-fmt-title-color");
    var ts = document.getElementById("wed-fmt-title-size");
    if (tc || ts) {
      var t = {};
      if (tc && tc.value.trim()) t.color = tc.value.trim();
      if (ts) t.fontSize = parseInt(ts.value, 10) || 12;
      var al = document.getElementById("wed-fmt-title-align");
      if (al && al.value && al.value !== "left") t.align = al.value;
      style.title = t;
    }
    var rad = document.getElementById("wed-fmt-radius");
    if (rad) { if (rad.value !== "") style.borderRadius = parseInt(rad.value, 10) || 0; else delete style.borderRadius; }
    var sh = document.getElementById("wed-fmt-shadow");
    if (sh && sh.value !== "Custom") {
      var shadowMap = { None: "", Small: "0 1px 3px rgba(15,23,42,0.12)", Medium: "0 4px 12px rgba(15,23,42,0.15)", Large: "0 8px 24px rgba(15,23,42,0.18)" };
      var sv = shadowMap[sh.value] || "";
      if (sv) style.shadow = sv; else delete style.shadow;
    }

    if (["watertank", "progress", "radial", "targetactual"].indexOf(type) !== -1) {
      var tg = document.getElementById("wed-fmt-target");
      if (tg) { if (tg.value !== "") config.target = parseFloat(tg.value); else delete config.target; }
    }
    if (type === "waffle") {
      var wf = document.getElementById("wed-fmt-waffle");
      if (wf) style.waffleGrid = parseInt(wf.value, 10) || 100;
    }
    if (type === "lollipop" || type === "diverging") {
      var po = document.getElementById("wed-fmt-pos");
      var ne = document.getElementById("wed-fmt-neg");
      if (po) { var pv = po.value.trim(); if (pv) style.positiveColor = pv; else delete style.positiveColor; }
      if (ne) { var nv = ne.value.trim(); if (nv) style.negativeColor = nv; else delete style.negativeColor; }
    }
    if (type === "ranking") {
      var md = document.getElementById("wed-fmt-medals");
      if (md) { if (!md.checked) style.medals = false; else delete style.medals; }
    }

    if (Object.keys(style).length) config.style = style;
    else delete config.style;

    // KPI cards: the first style colour doubles as the card background.
    if (type === "kpi") {
      if (style.colors && style.colors[0]) config.color = style.colors[0];
      else delete config.color;
    }
  }

  function _refreshColOptions(config) {
    var tables = (window.App && App.state && App.state.tables) || {};
    var table = config.table || "";
    var colOpts = '<option value="">Auto-detect</option>';
    if (table && tables[table] && tables[table].columns) {
      tables[table].columns.forEach(function(c) {
        var cn = typeof c === "string" ? c : (c.column_name || c.name || "");
        if (cn) colOpts += '<option value="' + _esc(cn) + '">' + _esc(cn) + '</option>';
      });
    }
    var xEl = document.getElementById("wed-xcol");
    var yEl = document.getElementById("wed-ycol");
    if (xEl) { xEl.innerHTML = colOpts; if (config.xCol) xEl.value = config.xCol; }
    if (yEl) { yEl.innerHTML = colOpts; if (config.yCol) yEl.value = config.yCol; }
    var map = { "wed-categorycol": "categoryCol", "wed-detailscol": "detailsCol", "wed-sizecol": "sizeCol" };
    Object.keys(map).forEach(function(id) {
      var el = document.getElementById(id);
      if (el) { el.innerHTML = colOpts; if (config[map[id]]) el.value = config[map[id]]; }
    });
  }

  /* P3.11 — rebuild the widget SQL as a calendar-hierarchy group-by when the
     author picks a level of the analytics.calendar dimension (Year > Quarter >
     Month > Day), then re-run the live preview. */
  function _applyCalendarGroup() {
    var hy = document.getElementById("wed-callevel");
    if (!hy) return;
    var lvl = hy.value;
    var sqlEl = document.getElementById("wed-sql");
    if (!lvl || !sqlEl) return;
    var tblEl = document.getElementById("wed-table");
    var table = tblEl ? tblEl.value : "";
    var xEl = document.getElementById("wed-xcol");
    var dateCol = xEl ? xEl.value : "";
    var yEl = document.getElementById("wed-ycol");
    var measure = yEl ? yEl.value : "";
    var aggEl = document.getElementById("wed-agg");
    var agg = aggEl ? aggEl.value : "SUM";
    if (!table || !dateCol || !measure) {
      if (window.App && App.warning) App.warning("Pick a DATE column for the X axis and a measure for the Y axis first");
      return;
    }
    var q = function (n) { return '"' + String(n).replace(/"/g, '""') + '"'; };
    var lv = { Year: "year", Quarter: "year_quarter", Month: "year_month", Day: "date" };
    var calCol = lv[lvl] || "year_month";
    var aggExpr = String(agg).toUpperCase() === "COUNT"
      ? "COUNT(*)"
      : (String(agg).toUpperCase() === "COUNT_DISTINCT" ? "count(DISTINCT " + q(measure) + ")" : agg + "(" + q(measure) + ")");
    var limitEl = document.getElementById("wed-limit");
    var limit = Math.max(1, parseInt(limitEl ? limitEl.value : "", 10) || 12);
    sqlEl.value = 'SELECT c.' + q(calCol) + ' AS label, ' + aggExpr + ' AS value FROM ' + q(table) +
      ' JOIN analytics.calendar c ON c.' + q('date') + ' = CAST(' + q(table) + '.' + q(dateCol) + ' AS DATE) ' +
      'GROUP BY 1 ORDER BY 1 ASC LIMIT ' + limit;
    _previewQuery();
  }

  function _collectData(config, type) {
    var sql = document.getElementById("wed-sql");
    if (sql) config.sql = sql.value.trim();

    var tbl = document.getElementById("wed-table");
    if (tbl) config.table = tbl.value;

    var xcol = document.getElementById("wed-xcol");
    if (xcol) config.xCol = xcol.value;

    var ycol = document.getElementById("wed-ycol");
    if (ycol) config.yCol = ycol.value;

    var agg = document.getElementById("wed-agg");
    if (agg) config.agg = agg.value;
    var sort = document.getElementById("wed-sort");
    if (sort) config.sort = sort.value;
    var limit = document.getElementById("wed-limit");
    if (limit) config.limit = parseInt(limit.value) || 0;

    // P3.11 — Calendar.Date hierarchy grouping level (Year/Quarter/Month/Day).
    var cal = document.getElementById("wed-callevel");
    if (cal) {
      if (cal.value) config.hierarchyLevel = cal.value;
      else delete config.hierarchyLevel;
    }

    // series checkboxes
    var seriesChecks = document.querySelectorAll(".wed-series");
    if (seriesChecks.length) {
      var series = [];
      seriesChecks.forEach(function(c) { if (c.checked) series.push(c.value); });
      if (series.length) config.series = series;
      else delete config.series;
    }

    var stacked = document.getElementById("wed-stacked");
    if (stacked) config.stacked = stacked.checked;

    var catcol = document.getElementById("wed-categorycol");
    if (catcol) config.categoryCol = catcol.value;
    var detcol = document.getElementById("wed-detailscol");
    if (detcol) config.detailsCol = detcol.value;
    var sizecol = document.getElementById("wed-sizecol");
    if (sizecol) config.sizeCol = sizecol.value;

    if (type === "kpi") {
      var ss = document.getElementById("wed-sparksql");
      if (ss) config.sparkSql = ss.value.trim();
      // Only set value if the user typed something — otherwise leave null
      // so _refreshWidget computes it from SQL.
      config.value = null;
    }

    if (type === "text") {
      var content = document.getElementById("wed-content");
      if (content) config.content = content.value;
    }

    // ── Filter-widget specific fields ──
    var slicercol = document.getElementById("wed-slicercol");
    if (slicercol) { if (slicercol.value) config.slicerColumn = slicercol.value; else delete config.slicerColumn; }
    var slicercsc = document.getElementById("wed-slicercascade");
    if (slicercsc) { if (slicercsc.checked) config.slicerCascade = true; else delete config.slicerCascade; }
    var numcol = document.getElementById("wed-numcol");
    if (numcol) { if (numcol.value) config.numCol = numcol.value; else delete config.numCol; }
    var hcols = document.getElementById("wed-hcols");
    if (hcols) { if (hcols.value) config.hCols = hcols.value; else delete config.hCols; }
    var datecol = document.getElementById("wed-datecol");
    if (datecol) { if (datecol.value) config.dateColumn = datecol.value; else delete config.dateColumn; }
    var presets = document.getElementById("wed-presets");
    if (presets) { if (presets.value) config.datePresets = presets.value; else delete config.datePresets; }
    var topncol = document.getElementById("wed-topncol");
    if (topncol) { if (topncol.value) config.topnColumn = topncol.value; else delete config.topnColumn; }
    var topnmeasure = document.getElementById("wed-topnmeasure");
    if (topnmeasure) { if (topnmeasure.value) config.topnMeasure = topnmeasure.value; else delete config.topnMeasure; }
    var topnmode = document.getElementById("wed-topnmode");
    if (topnmode) config.topnMode = topnmode.value;
    var topnn = document.getElementById("wed-topnn");
    if (topnn) config.topnN = parseInt(topnn.value, 10) || 10;
    var panefields = document.getElementById("wed-panefields");
    if (panefields) { if (panefields.value) config.paneFields = panefields.value; else delete config.paneFields; }
    var globalph = document.getElementById("wed-globalph");
    if (globalph) { if (globalph.value) config.searchPlaceholder = globalph.value; else delete config.searchPlaceholder; }
  }

  function _collectDisplay(config, type) {
    var t = document.getElementById("wed-title");
    if (t) config.title = t.value;
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  LIVE SQL PREVIEW — runs the current query and inspects the result
  // ═══════════════════════════════════════════════════════════════════════

  function _previewQuery() {
    var sqlEl = document.getElementById("wed-sql");
    var sql = sqlEl ? sqlEl.value.trim() : "";
    var status = document.getElementById("wed-test-status");
    var wrap = document.getElementById("wed-preview");
    if (!sql) { if (status) status.textContent = "Enter a query first."; return; }
    if (status) { status.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Running…'; status.className = ""; }
    var api = window.API || null;
    var p = (api && api.biQuery) ? api.biQuery(sql) : Promise.reject(new Error("API unavailable"));
    p.then(function(res) {
      var rows = res && res.rows ? res.rows : (Array.isArray(res) ? res : []);
      var cols = res && res.columns ? res.columns : (rows.length ? Object.keys(rows[0]) : []);
      if (wrap) {
        if (!cols.length) {
          wrap.innerHTML = '<div class="text-xs text-gray-400">Query ran but returned no columns.</div>';
        } else {
          var cap = rows.slice(0, 8);
          var html = '<table class="w-full text-[11px] border-collapse"><thead><tr class="text-left text-gray-400">' +
            cols.map(function(c){ return '<th class="border-b border-gray-200 dark:border-gray-700 pb-1 pr-2 font-medium">' + _esc(c) + '</th>'; }).join("") +
            '</tr></thead><tbody>';
          cap.forEach(function(r) {
            html += '<tr>' + cols.map(function(c) {
              var v = r[c];
              if (v === null || v === undefined) v = "";
              return '<td class="border-b border-gray-100 dark:border-gray-800 py-1 pr-2 font-mono text-[10px] max-w-[140px] truncate">' + _esc(String(v)) + "</td>";
            }).join("") + '</tr>';
          });
          html += '</tbody></table>';
          html += '<div class="text-[10px] text-gray-400 mt-1">' + rows.length + " row" + (rows.length === 1 ? "" : "s") + (rows.length > 8 ? " (showing first 8)" : "") + '</div>';
          wrap.innerHTML = html;
        }
      }
      if (status) { status.textContent = "OK · " + rows.length + " row" + (rows.length === 1 ? "" : "s"); status.className = "text-emerald-600"; }
    }).catch(function(err) {
      var msg = (err && (err.message || err.error || err.detail)) || String(err);
      if (wrap) wrap.innerHTML = '<div class="text-xs text-red-500"><i class="fas fa-circle-exclamation mr-1"></i>' + _esc(msg) + "</div>";
      if (status) { status.textContent = "Failed"; status.className = "text-red-500"; }
    });
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  WHERE BUILDER — parse/patch a simple AND-conjunction WHERE clause
  // ═══════════════════════════════════════════════════════════════════════

  function _rowsFromSql(sql) {
    // Returns an array of {col,op,val} rows derived from an existing WHERE
    // clause, or null if there is a WHERE clause we don't recognise (so the
    // builder becomes read-only and we never touch the user's SQL).
    var m = /\bWHERE\b/i.exec(sql || "");
    if (!m) return [];
    var before = sql.slice(0, m.index);
    var body = sql.slice(m.index + m[0].length);
    var tail = /\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET)\b/i.exec(body);
    if (tail) body = body.slice(0, tail.index);
    var conds = body.split(/\s+\bAND\b\s+/i);
    var rows = [];
    for (var i = 0; i < conds.length; i++) {
      var row = _parseCond(conds[i].trim());
      if (!row && conds[i].trim() !== "") return null;
      rows.push(row);
    }
    return rows.filter(Boolean);
  }

  function _parseCond(s) {
    if (!s) return null;
    var m;
    m = /LOWER\("([^"]+)"\)\s+NOT LIKE\s+'%(.*?)%'/i.exec(s);
    if (m) return { col: m[1], op: "ncontains", val: m[2].replace(/''/g, "'") };
    m = /LOWER\("([^"]+)"\)\s+LIKE\s+'%(.*?)%'/i.exec(s);
    if (m) return { col: m[1], op: "contains", val: m[2].replace(/''/g, "'") };
    m = /"([^"]+)"\s*<>\s*''\s*$/i.exec(s);
    if (m) return { col: m[1], op: "nempty", val: "" };
    m = /"([^"]+)"\s*=\s*''\s*$/i.exec(s);
    if (m) return { col: m[1], op: "empty", val: "" };
    m = /"([^"]+)"\s*(=|!=|<>|>=|<=|>|<)\s*(?:'((?:[^']|'')*)'|(-?[0-9.eE+]+))/i.exec(s);
    if (m) {
      var op = m[2] === "<>" ? "!=" : m[2];
      var val = m[3] !== undefined ? m[3].replace(/''/g, "'") : m[4];
      return { col: m[1], op: op, val: val };
    }
    return null;
  }

  function _condSql(row, types) {
    var col = '"' + String(row.col).replace(/"/g, '""') + '"';
    var numeric = false;
    if (types) {
      var ct = String(types[row.col] || "").toLowerCase();
      numeric = /int|double|float|decimal|numeric|number|real|money|long|short|byte/.test(ct) && !/interval/.test(ct);
    }
    function qv(v) {
      var s = String(v == null ? "" : v);
      if (numeric) {
        if (/^-?[0-9]+(\.[0-9]+)?$/.test(s.trim())) return s.trim();
        return "'" + s.replace(/'/g, "''") + "'";
      }
      if (types && types[row.col]) {
        // Known non-numeric column type — always quote string/date values
        return "'" + s.replace(/'/g, "''") + "'";
      }
      if (/^-?[0-9]+(\.[0-9]+)?$/.test(s.trim())) return s.trim();
      return "'" + s.replace(/'/g, "''") + "'";
    }
    function like(v) { return "'%" + String(v).replace(/'/g, "''") + "%'"; }
    switch (row.op) {
      case "contains": return "LOWER(" + col + ") LIKE " + like(row.val);
      case "ncontains": return "LOWER(" + col + ") NOT LIKE " + like(row.val);
      case "empty": return col + " = ''";
      case "nempty": return col + " <> ''";
      default: return col + " " + row.op + " " + qv(row.val);
    }
  }

  function _patchFilters(sql, rows, types) {
    var conds = [];
    rows.forEach(function(r) { if (r.col) conds.push(_condSql(r, types)); });
    var whereStr = conds.length ? "WHERE " + conds.join(" AND ") : "";
    var current = _extractWhere(sql);
    if (current && current.parseable) {
      return sql.slice(0, current.start) + whereStr + (whereStr ? " " : "") + sql.slice(current.bodyEnd);
    }
    if (current === null) {
      var m = /\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET)\b/i.exec(sql);
      if (m) {
        return sql.slice(0, m.index) + whereStr + (whereStr ? " " : "") + sql.slice(m.index);
      }
      return sql.replace(/\s*$/, "") + (whereStr ? " " + whereStr : "");
    }
    return null; // unparseable existing WHERE — caller keeps SQL untouched
  }

  function _extractWhere(sql) {
    var m = /\bWHERE\b/i.exec(sql || "");
    if (!m) return null;
    var body = sql.slice(m.index + m[0].length);
    var tail = /\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET)\b/i.exec(body);
    var bodyEnd = tail ? (m.index + m[0].length + tail.index) : sql.length;
    var conds = body.split(/\s+\bAND\b\s+/i);
    var parseable = true;
    var rows = [];
    for (var i = 0; i < conds.length; i++) {
      var c = conds[i].trim();
      if (!c) continue;
      var r = _parseCond(c);
      if (!r) { parseable = false; break; }
      rows.push(r);
    }
    return { start: m.index, bodyEnd: bodyEnd, parseable: parseable, rows: rows };
  }

  function _patchSqlFromFilters() {
    var sqlEl = document.getElementById("wed-sql");
    var note = document.getElementById("wed-filter-note");
    if (!sqlEl) return;
    var tblEl = document.getElementById("wed-table");
    var table = tblEl ? tblEl.value : "";
    var types = {};
    var tables = (window.App && App.state && App.state.tables) || {};
    ((tables[table] && tables[table].columns) || []).forEach(function(c) {
      if (typeof c === "object") {
        var cn = c.column_name || c.name || "";
        if (cn) types[cn] = c.column_type || c.type || "";
      }
    });
    var rows = [];
    document.querySelectorAll("#wed-filters .wed-frow").forEach(function(r) {
      rows.push({
        col: (r.querySelector(".wed-fcol") || {}).value || "",
        op: (r.querySelector(".wed-fop") || {}).value || "=",
        val: (r.querySelector(".wed-fval") || {}).value || "",
      });
    });
    var patched = _patchFilters(sqlEl.value, rows, types);
    if (patched === null) {
      // There is a manual WHERE we don't own — leave the SQL alone.
      if (note) { note.classList.remove("hidden"); note.textContent = "Manual WHERE clause detected — filters won't be patched into this query automatically."; }
      return;
    }
    sqlEl.value = patched;
    if (note) note.classList.add("hidden");
  }

  function _fillColSelect(sel, table, selected) {
    if (!sel) return;
    var tables = (window.App && App.state && App.state.tables) || {};
    var cols = (tables[table] && tables[table].columns) ? tables[table].columns : [];
    var html = '<option value="">column…</option>';
    cols.forEach(function(c) {
      var cn = typeof c === "string" ? c : (c.column_name || c.name || "");
      if (cn) html += '<option value="' + _esc(cn) + '"' + (cn === selected ? " selected" : "") + ">" + _esc(cn) + "</option>";
    });
    sel.innerHTML = html;
    if (selected) sel.value = selected;
  }

  function _appendFilterRow(container, table, row) {
    if (!container) return;
    row = row || {};
    var opOpts = _FILTER_OPS.map(function(o) {
      return '<option value="' + o[0] + '"' + ((row.op || "=") === o[0] ? " selected" : "") + ">" + _esc(o[1]) + "</option>";
    }).join("");
    var n = document.createElement("div");
    n.className = "wed-frow grid grid-cols-[1fr_auto_1fr_auto] gap-1.5 items-center";
    var sel = document.createElement("select");
    sel.className = "form-input text-xs wed-fcol";
    _fillColSelect(sel, table, row.col || "");
    var op = document.createElement("select");
    op.className = "form-input text-xs wed-fop";
    op.innerHTML = opOpts;
    var val = document.createElement("input");
    val.className = "form-input text-xs wed-fval";
    val.type = "text";
    val.placeholder = "value";
    val.value = row.val || "";
    var del = document.createElement("button");
    del.type = "button";
    del.className = "wed-fdel text-gray-400 hover:text-red-500 text-sm px-1";
    del.title = "Remove filter";
    del.innerHTML = "✕";
    n.appendChild(sel); n.appendChild(op); n.appendChild(val); n.appendChild(del);
    container.appendChild(n);
  }

  function _refreshFilterCols(table) {
    document.querySelectorAll("#wed-filters .wed-fcol").forEach(function(s) {
      var cur = s.value;
      s.innerHTML = "";
      _fillColSelect(s, table, cur);
    });
  }

  function _syncFilterRowsFromSql(sql, table) {
    var container = document.getElementById("wed-filters");
    var note = document.getElementById("wed-filter-note");
    if (!container) return;
    container.innerHTML = "";
    var rows = _rowsFromSql(sql);
    if (rows === null) {
      if (note) { note.classList.remove("hidden"); note.textContent = "Manual WHERE clause detected on this query — the builder is read-only until it is removed."; }
      _appendFilterRow(container, table, {});
      return;
    }
    if (note) note.classList.add("hidden");
    if (!rows.length) { _appendFilterRow(container, table, {}); return; }
    rows.forEach(function(r) { _appendFilterRow(container, table, r); });
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  COLOUR HELPERS
  // ═══════════════════════════════════════════════════════════════════════

  function _themePalette() {
    var t = window.BIDashboardTheme;
    if (t && t.palette && t.palette.length) return t.palette.slice(0, 12);
    var cs = (window.Charts && Charts.PALETTE && Charts.PALETTE.categorical);
    if (cs && cs.length) return cs;
    return [
      "#118DFF", "#12239E", "#E66C37", "#6B007B", "#E044A7", "#744EC2",
      "#D9B300", "#D64550", "#197278", "#1AAB40", "#15C6F4", "#FFA058",
      "#B5A1FF", "#F472D0", "#00DBBC", "#FF8080",
    ];
  }

  function _bindColorSync(root) {
    if (!root) return;
    root.querySelectorAll(".wed-fmt-cpick").forEach(function(cp) {
      cp.addEventListener("input", function() {
        var target = null;
        if (cp.dataset.sync) target = document.getElementById(cp.dataset.sync);
        else {
          var cell = cp.closest(".wed-fmt-color-cell");
          if (cell) target = cell.querySelector(".wed-fmt-hex[data-i='" + cp.dataset.i + "']") || cell.querySelector(".wed-fmt-hex");
        }
        if (target) target.value = cp.value.toUpperCase();
      });
    });
    root.querySelectorAll(".wed-fmt-hex").forEach(function(hx) {
      hx.addEventListener("input", function() {
        var v = hx.value.trim();
        if (/^#[0-9a-fA-F]{6}$/.test(v)) {
          var cp = hx.closest(".wed-fmt-color-cell") ? hx.closest(".wed-fmt-color-cell").querySelector(".wed-fmt-cpick") : null;
          if (cp) cp.value = v;
        }
      });
    });
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  LOCAL SQL GENERATOR — maps natural language → DuckDB SQL
  //  Uses the selected table's column names and types to build queries.
  // ═══════════════════════════════════════════════════════════════════════

  function _orderBy(s) {
    if (s === "label_asc") return "label ASC";
    if (s === "label_desc") return "label DESC";
    if (s === "value_asc") return "value ASC";
    return "value DESC";
  }

  function _domOpts() {
    var o = { series: [] };
    var a = document.getElementById("wed-agg"); if (a) o.agg = a.value;
    var s = document.getElementById("wed-sort"); if (s) o.sort = s.value;
    var l = document.getElementById("wed-limit"); if (l) o.limit = parseInt(l.value) || 0;
    document.querySelectorAll(".wed-series").forEach(function(c) { if (c.checked) o.series.push(c.value); });
    var st = document.getElementById("wed-stacked"); if (st) o.stacked = st.checked;
    return o;
  }

  function _generateSQL(prompt, table, columns, colTypes, widgetType, opts) {
    opts = opts || {};
    var p = prompt.toLowerCase();
    var q = function(n) { return '"' + String(n).replace(/"/g, '""') + '"'; };

    // Classify columns
    var measures = [], dimensions = [], dates = [];
    var numericRe = /int|float|double|decimal|numeric|bigint|number|real|amount|revenue|profit|sales|cost|price|qty|quantity|count|score|rate|ltv|salary|expense|balance|margin|income/i;
    var dateRe = /date|timestamp|time/i;
    var dateNameRe = /^(created|updated|date|timestamp|datetime|order_date|ship_date|sale_date|transaction_date)/i;

    columns.forEach(function(c) {
      var t = (colTypes[c] || "").toLowerCase();
      if (dateRe.test(t) || dateNameRe.test(c)) dates.push(c);
      else if (numericRe.test(t) || numericRe.test(c)) measures.push(c);
      else dimensions.push(c);
    });

    // Helpers
    function bestMeasure() {
      var prefer = measures.filter(function(m) { return /revenue|profit|sales|amount|income|total|gross|ltv|salary/i.test(m); });
      return prefer[0] || measures[0] || "";
    }
    function bestDim() {
      var prefer = dimensions.filter(function(d) { return /region|category|product|segment|department|channel|status|type|country|city|store|customer|employee/i.test(d); });
      return prefer[0] || dimensions[0] || "";
    }
    function bestDate() {
      var prefer = dates.filter(function(d) { return /date|created|updated|order|transaction/i.test(d); });
      return prefer[0] || dates[0] || "";
    }

    var measure = bestMeasure();
    var dim = bestDim();
    var dateCol = bestDate();

    // ── Detect intent from prompt ──

    // Top N (dropdown limit wins when no explicit "top N" in the prompt)
    var topN = opts.limit > 0 ? opts.limit : 10;
    var topMatch = p.match(/top\s+(\d+)/);
    if (topMatch) topN = parseInt(topMatch[1]);

    // Aggregate functions (dropdown agg wins unless the prompt names one)
    var agg = opts.agg || "SUM";
    var sortBy = opts.sort || "value_desc";
    if (/average|avg|mean/.test(p)) agg = "AVG";
    else if (/count|number|how many|total rows/.test(p)) agg = "COUNT";
    else if (/min|lowest|smallest|minimum/.test(p)) agg = "MIN";
    else if (/max|highest|largest|maximum/.test(p)) agg = "MAX";

    // Specific column mentions — try to match prompt words to column names
    function findCol(words) {
      for (var i = 0; i < columns.length; i++) {
        var cl = columns[i].toLowerCase();
        for (var j = 0; j < words.length; j++) {
          if (cl.indexOf(words[j]) !== -1) return columns[i];
        }
      }
      return "";
    }

    // Try to find specific columns mentioned in the prompt
    var promptMeasure = findCol(measures.map(function(m) { return m.toLowerCase(); }).filter(Boolean));
    var promptDim = findCol(dimensions.map(function(d) { return d.toLowerCase(); }).filter(Boolean));
    var promptDate = findCol(dates.map(function(d) { return d.toLowerCase(); }).filter(Boolean));

    if (promptMeasure) measure = promptMeasure;
    if (promptDim) dim = promptDim;
    if (promptDate) dateCol = promptDate;

    // ── Trend / time series ──
    if (/trend|over time|time series|monthly|daily|weekly|yearly|by month|by date|by day|by year|chronolog/.test(p)) {
      var dateExpr = dateCol;
      var t = (colTypes[dateCol] || "").toLowerCase();
      if (/date|timestamp/.test(t)) {
        if (/month|monthly/.test(p)) dateExpr = "date_trunc('month', " + q(dateCol) + ")";
        else if (/week|weekly/.test(p)) dateExpr = "date_trunc('week', " + q(dateCol) + ")";
        else if (/year|yearly/.test(p)) dateExpr = "date_trunc('year', " + q(dateCol) + ")";
        else dateExpr = "date_trunc('month', " + q(dateCol) + ")";
      }
      if (measure) {
        return "SELECT " + dateExpr + " AS label, " + agg + "(" + q(measure) + ") AS value FROM " + q(table) + " GROUP BY label ORDER BY label";
      }
      return "SELECT " + dateExpr + " AS label, COUNT(*) AS value FROM " + q(table) + " GROUP BY label ORDER BY label";
    }

    // ── Breakdown / group by ──
    if (/breakdown|by |per |each |group|split|distribut/.test(p) && dim) {
      if (measure) {
        return "SELECT " + q(dim) + " AS label, " + agg + "(" + q(measure) + ") AS value FROM " + q(table) + " GROUP BY " + q(dim) + " ORDER BY value DESC LIMIT " + topN;
      }
      return "SELECT " + q(dim) + " AS label, COUNT(*) AS value FROM " + q(table) + " GROUP BY " + q(dim) + " ORDER BY value DESC LIMIT " + topN;
    }

    // ── Ranking / top N ──
    if (/top|rank|best|worst|highest|lowest|most|least|leading|largest|smallest/.test(p)) {
      var desc = /worst|lowest|least|smallest|bottom/.test(p) ? "ASC" : "DESC";
      if (measure) {
        return "SELECT " + q(dim || columns[0]) + " AS label, " + agg + "(" + q(measure) + ") AS value FROM " + q(table) + " GROUP BY " + q(dim || columns[0]) + " ORDER BY value " + desc + " LIMIT " + topN;
      }
      return "SELECT " + q(dim || columns[0]) + " AS label, COUNT(*) AS value FROM " + q(table) + " GROUP BY " + q(dim || columns[0]) + " ORDER BY value " + desc + " LIMIT " + topN;
    }

    // ── Sum / total / aggregate of specific column ──
    if (/sum|total|add up|aggregate/.test(p) && measure) {
      if (dim) {
        return "SELECT " + q(dim) + " AS label, SUM(" + q(measure) + ") AS value FROM " + q(table) + " GROUP BY " + q(dim) + " ORDER BY value DESC LIMIT " + topN;
      }
      return "SELECT SUM(" + q(measure) + ") AS value FROM " + q(table);
    }

    // ── Average / mean ──
    if (/average|avg|mean/.test(p) && measure) {
      if (dim) {
        return "SELECT " + q(dim) + " AS label, AVG(" + q(measure) + ") AS value FROM " + q(table) + " GROUP BY " + q(dim) + " ORDER BY value DESC LIMIT " + topN;
      }
      return "SELECT AVG(" + q(measure) + ") AS value FROM " + q(table);
    }

    // ── Count / how many ──
    if (/count|how many|number of|total rows|rows/.test(p)) {
      if (dim) {
        return "SELECT " + q(dim) + " AS label, COUNT(*) AS value FROM " + q(table) + " GROUP BY " + q(dim) + " ORDER BY value DESC LIMIT " + topN;
      }
      return "SELECT COUNT(*) AS value FROM " + q(table);
    }

    // ── Correlation / relationship ──
    if (/correlat|relationship|scatter|vs|against|compare/.test(p) && measures.length >= 2) {
      var m1 = measure;
      var m2 = measures.filter(function(m) { return m !== m1; })[0];
      if (dim) {
        return "SELECT " + q(dim) + " AS label, SUM(" + q(m1) + ') AS "' + m1 + '", SUM(' + q(m2) + ') AS "' + m2 + '" FROM ' + q(table) + " GROUP BY " + q(dim) + " ORDER BY " + q(m1) + " DESC LIMIT " + topN;
      }
      return "SELECT " + q(m1) + ' AS "' + m1 + '", ' + q(m2) + ' AS "' + m2 + '" FROM ' + q(table) + " LIMIT " + topN;
    }

    // ── Filter / where ──
    if (/filter|where|only|exclude|include/.test(p)) {
      // Try to extract a value filter from the prompt
      var filterWords = p.replace(/filter|where|only|exclude|include|rows?|from|the|that|which|are|is|has/g, "").trim().split(/\s+/).filter(function(w) { return w.length > 1; });
      if (dim && filterWords.length > 0) {
        var filterVal = filterWords[0];
        if (measure) {
          return "SELECT " + q(dim) + " AS label, SUM(" + q(measure) + ") AS value FROM " + q(table) + " WHERE LOWER(" + q(dim) + ") LIKE '%" + filterVal + "%' GROUP BY " + q(dim) + " ORDER BY value DESC LIMIT " + topN;
        }
        return "SELECT * FROM " + q(table) + " WHERE LOWER(" + q(dim) + ") LIKE '%" + filterVal + "%' LIMIT " + topN;
      }
    }

    // ── Default: build the most useful query for the widget type ──
    if (widgetType === "kpi" || widgetType === "gauge") {
      if (measure) return "SELECT " + agg + "(" + q(measure) + ") AS value FROM " + q(table);
      return "SELECT COUNT(*) AS value FROM " + q(table);
    }
    if (widgetType === "table") {
      return "SELECT * FROM " + q(table) + " LIMIT 50";
    }
    // Multi-series charts (2+ series selected) — dim + N measures
    if (opts.series && opts.series.length > 1 &&
        (widgetType === "bar" || widgetType === "column" || widgetType === "line" ||
         widgetType === "area" || widgetType === "radar" || widgetType === "combo")) {
      var selCols = opts.series.slice(0, 2).map(function(m) {
        return agg + "(" + q(m) + ') AS "' + m + '"';
      }).join(", ");
      return "SELECT " + q(dim || columns[0]) + " AS label, " + selCols + " FROM " + q(table) +
        " GROUP BY " + q(dim || columns[0]) + " ORDER BY label LIMIT " + topN;
    }
    // Rank-style visuals — category + measure, sorted
    if (widgetType === "funnel" || widgetType === "pie" || widgetType === "donut" ||
        widgetType === "bar" || widgetType === "column") {
      if (dim && measure) {
        return "SELECT " + q(dim) + " AS label, " + agg + "(" + q(measure) + ") AS value FROM " + q(table) +
          " GROUP BY " + q(dim) + " ORDER BY " + _orderBy(sortBy) + " LIMIT " + topN;
      }
      if (measure) return "SELECT " + agg + "(" + q(measure) + ") AS value FROM " + q(table);
    }
    // Ordered visuals (waterfall/treemap/heatmap/radar/combo/scatter) — sort by label
    if (widgetType === "waterfall" || widgetType === "treemap" || widgetType === "heatmap" ||
        widgetType === "radar" || widgetType === "combo" || widgetType === "scatter" ||
        widgetType === "line" || widgetType === "area") {
      if (dim && measure) {
        return "SELECT " + q(dim) + " AS label, " + agg + "(" + q(measure) + ") AS value FROM " + q(table) +
          " GROUP BY " + q(dim) + " ORDER BY label LIMIT " + topN;
      }
      if (measure) return "SELECT " + agg + "(" + q(measure) + ") AS value FROM " + q(table) + " LIMIT " + topN;
    }
    // Chart types — group by best dimension
    if (dim && measure) {
      return "SELECT " + q(dim) + " AS label, " + agg + "(" + q(measure) + ") AS value FROM " + q(table) +
        " GROUP BY " + q(dim) + " ORDER BY " + _orderBy(sortBy) + " LIMIT " + topN;
    }
    if (measure) {
      return "SELECT " + q(measure) + " AS value FROM " + q(table) + " LIMIT " + topN;
    }
    return "SELECT * FROM " + q(table) + " LIMIT " + topN;
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  window.WidgetEditor = { open: _open };
})();