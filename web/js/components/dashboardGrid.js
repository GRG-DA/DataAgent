/**
 * DashboardGrid — Drag-and-drop dashboard widget grid
 *
 * CSS Grid-based layout engine with native HTML5 drag-and-drop and resize.
 * Usage:
 *   var grid = DashboardGrid.render('container-id', widgets, options);
 *   grid.addWidget(type, config); grid.removeWidget(id); grid.getLayout();
 */
(function () {
  "use strict";

  var _grids = {};

  function _genId() {
    return "w_" + Date.now() + "_" + Math.random().toString(36).slice(2, 6);
  }

  function _render(containerId, widgets, options) {
    var container = typeof containerId === "string" ? document.getElementById(containerId) : containerId;
    if (!container) return null;
    options = options || {};

    var state = {
      container: container,
      widgets: widgets || [],
      options: options,
      onChange: options.onChange || null,
      columns: options.columns || 12,
      rowHeight: options.rowHeight || 80,
      editMode: options.editMode !== false,
      dragSource: null,
    };
    _grids[containerId] = state;

    _buildGrid(state);
    return {
      addWidget: function (type, config, extra) { return _addWidget(state, type, config, extra); },
      removeWidget: function (id) { _removeWidget(state, id); },
      updateWidget: function (id, config) { _updateWidget(state, id, config); },
      getLayout: function () { return _getLayout(state); },
      setEditMode: function (mode) { state.editMode = mode; _buildGrid(state); },
      getState: function () { return state; },
    };
  }

  function _buildGrid(state) {
    var w = state.widgets;
    if (w.length === 0) {
      state.container.innerHTML =
        '<div class="empty-state" style="padding:40px"><i class="fas fa-plus-circle text-3xl text-primary-400"></i><p class="text-sm mt-2">Add widgets to build your dashboard</p></div>';
      return;
    }
    // Auto-pack: if widgets lack explicit row/col, fill rows left-to-right.
    var numCols = state.columns || 12;
    var curRow = 1, curCol = 1, maxRowH = 0;
    for (var i = 0; i < w.length; i++) {
      var wi = w[i];
      var wCols = wi.cols || 4;
      var wRows = wi.rows || 2;
      if (wi.row == null || wi.col == null) {
        // auto-pack: wrap to next row if this widget won't fit
        if (curCol + wCols - 1 > numCols && curCol > 1) {
          curRow += maxRowH;
          curCol = 1;
          maxRowH = 0;
        }
        wi.row = curRow;
        wi.col = curCol;
        curCol += wCols;
        if (wRows > maxRowH) maxRowH = wRows;
      } else {
        // explicit position — track the bottom edge for subsequent auto-packed widgets
        var bottom = wi.row + (wi.rows || 2);
        if (bottom > curRow + maxRowH) { curRow = bottom; curCol = 1; maxRowH = 0; }
      }
    }
    // Fixed-canvas mode: matches export geometry exactly (unit_w from page
    // width minus margins/gaps, fixed row height). Responsive 1fr otherwise.
    var fixedWidth = state.options.fixedWidth || 0;
    var gap = state.options.gap != null ? state.options.gap : 12;
    var pad = state.options.padding != null ? state.options.padding : 4;
    var rowH = state.options.rowHeight || 80;
    var gridStyle;
    if (fixedWidth > 0) {
      var unitW = (fixedWidth - 2 * pad - (numCols - 1) * gap) / numCols;
      gridStyle = "display:grid;width:" + fixedWidth + "px;box-sizing:border-box;" +
        "grid-template-columns:repeat(" + numCols + "," + unitW + "px);" +
        "grid-auto-rows:" + rowH + "px;gap:" + gap + "px;padding:" + pad + "px;";
    } else {
      gridStyle = "display:grid;grid-template-columns:repeat(" + numCols + ",1fr);" +
        "gap:" + gap + "px;padding:" + pad + "px;";
    }
    var html = '<div class="dashboard-grid" style="' + gridStyle + '">';
    for (var i = 0; i < w.length; i++) {
      var wi = w[i];
      wi._index = i;
      var row = wi.row || i + 1;
      var col = wi.col || 1;
      var rows = wi.rows || 2;
      var cols = wi.cols || 4;
      html += '<div class="dash-widget" data-id="' + wi.id + '" data-type="' + _esc(wi.type) + '" draggable="' + state.editMode + '" style="grid-row:' + row + '/span ' + rows + ';grid-column:' + col + '/span ' + cols + ';background:var(--color-card-bg,#fff);border:1px solid var(--color-card-border,var(--color-border));border-radius:12px;box-shadow:var(--shadow-sm);overflow:hidden;position:relative;min-height:60px;">';
      // Header
      html += '<div class="dash-widget-header" style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--color-card-border,var(--color-border));cursor:' + (state.editMode ? 'grab' : 'default') + ';background:var(--color-content-bg,var(--color-surface-2));">';
      html += '<span class="text-xs font-medium truncate">' + _esc(wi.title || wi.type) + '</span>';
      if (state.editMode) {
        html += '<div class="flex gap-1">';
        html += '<button class="dash-widget-config" data-id="' + wi.id + '" style="border:none;background:transparent;cursor:pointer;color:var(--color-text-muted);padding:2px;font-size:11px;" title="Configure"><i class="fas fa-cog"></i></button>';
        html += '<button class="dash-widget-remove" data-id="' + wi.id + '" style="border:none;background:transparent;cursor:pointer;color:var(--color-danger);padding:2px;font-size:11px;" title="Remove"><i class="fas fa-times"></i></button>';
        html += '</div>';
      }
      html += '</div>';
      // Body
      html += '<div class="dash-widget-body" data-id="' + wi.id + '" style="padding:0;overflow:auto;height:calc(100% - 36px);">';
      html += '<div class="dash-widget-content" data-id="' + wi.id + '"></div>';
      html += '</div>';
      // Resize handle
      if (state.editMode) {
        html += '<div class="dash-resize-handle" data-id="' + wi.id + '" style="position:absolute;bottom:0;right:0;width:16px;height:16px;cursor:se-resize;background:linear-gradient(135deg,transparent 50%,var(--color-text-muted) 50%);border-radius:0 0 12px 0;"></div>';
      }
      html += '</div>';
    }
    html += '</div>';
    state.container.innerHTML = html;

    // Bind events
    if (state.editMode) {
      _bindDragDrop(state);
      _bindResize(state);
    }
    _bindRemove(state);
    _bindConfig(state);
  }

  function _bindResize(state) {
    var container = state.container;
    var grid = container.querySelector(".dashboard-grid");
    var numCols = state.columns || 12;
    var gap = state.options.gap != null ? state.options.gap : 12;
    var pad = state.options.padding != null ? state.options.padding : 4;
    var rowH = state.options.rowHeight || 80;
    container.querySelectorAll(".dash-resize-handle").forEach(function (handle) {
      handle.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var id = handle.dataset.id;
        var w = null;
        for (var i = 0; i < state.widgets.length; i++) {
          if (state.widgets[i].id === id) { w = state.widgets[i]; break; }
        }
        if (!w) return;
        var rect = grid.getBoundingClientRect();
        var cellW = rect.width / numCols;          // includes a share of the gap
        var cellH = rowH + gap;
        var sx = e.clientX, sy = e.clientY;
        var sc = w.cols, sr = w.rows;

        function onMove(ev) {
          var dc = Math.round((ev.clientX - sx) / cellW);
          var dr = Math.round((ev.clientY - sy) / cellH);
          w.cols = Math.max(1, Math.min(numCols - (w.col || 1) + 1, sc + dc));
          w.rows = Math.max(1, Math.min(12, sr + dr));
          handle.style.opacity = "0.6";
        }
        function onUp() {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          handle.style.opacity = "";
          _buildGrid(state);
          if (state.onChange) state.onChange(_getLayout(state));
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    });
  }

  function _bindDragDrop(state) {
    var container = state.container;
    // Drag start
    container.querySelectorAll(".dash-widget").forEach(function(el) {
      el.addEventListener("dragstart", function(e) {
        state.dragSource = this.dataset.id;
        this.style.opacity = "0.5";
        e.dataTransfer.effectAllowed = "move";
      });
      el.addEventListener("dragend", function() {
        this.style.opacity = "1";
        container.querySelectorAll(".dash-widget").forEach(function(wi) { wi.style.outline = "none"; });
      });
      el.addEventListener("dragover", function(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        container.querySelectorAll(".dash-widget").forEach(function(wi) { wi.style.outline = "none"; });
        this.style.outline = "2px dashed var(--color-primary)";
      });
      el.addEventListener("dragleave", function() { this.style.outline = "none"; });
      el.addEventListener("drop", function(e) {
        e.preventDefault();
        this.style.outline = "none";
        var fromId = state.dragSource;
        var toId = this.dataset.id;
        if (fromId && toId && fromId !== toId) {
          var widgets = state.widgets;
          var fromIdx = -1, toIdx = -1;
          for (var i = 0; i < widgets.length; i++) {
            if (widgets[i].id === fromId) fromIdx = i;
            if (widgets[i].id === toId) toIdx = i;
          }
          if (fromIdx >= 0 && toIdx >= 0) {
            // Swap the two widgets' explicit grid positions so the drag really
            // moves the card (positions are what the exporter uses).
            var fromW = widgets[fromIdx], toW = widgets[toIdx];
            var fp = { col: fromW.col, row: fromW.row };
            var tp = { col: toW.col, row: toW.row };
            var moved = widgets.splice(fromIdx, 1)[0];
            widgets.splice(toIdx, 0, moved);
            moved.col = tp.col; moved.row = tp.row;
            toW.col = fp.col; toW.row = fp.row;
            _reindex(state);
            _buildGrid(state);
            if (state.onChange) state.onChange(_getLayout(state));
          }
        }
      });
    });
  }

  function _bindRemove(state) {
    state.container.querySelectorAll(".dash-widget-remove").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        _removeWidget(state, this.dataset.id);
      });
    });
  }

  function _bindConfig(state) {
    state.container.querySelectorAll(".dash-widget-config").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        var id = this.dataset.id;
        var widget = null;
        for (var i = 0; i < state.widgets.length; i++) {
          if (state.widgets[i].id === id) { widget = state.widgets[i]; break; }
        }
        if (widget && window.WidgetEditor) {
          window.WidgetEditor.open(widget, function(updated) {
            _updateWidget(state, id, updated);
          });
        }
      });
    });
  }

  function _addWidget(state, type, config, extra) {
    extra = extra || {};
    var isKpi = (type === "kpi" || type === "cardVisual" || type === "gauge");
    var isChart = (type === "chart");
    var isText = (type === "text");
    var isTable = (type === "table");
    var isTall = isChart && config && (config.chartType === "heatmap" || config.chartType === "treemap");
    var isSmall = isChart && config && (config.chartType === "sparkline");

    var defaultCols = isKpi ? 3 : (isText || isTable ? 12 : (isSmall ? 6 : 6));
    var defaultRows = isKpi ? 2 : (isText ? 2 : (isTall ? 4 : (isSmall ? 2 : 3)));

    state.widgets.push({
      id: _genId(),
      type: type,
      title: extra.title || config.title || type,
      cols: extra.cols || defaultCols,
      rows: extra.rows || defaultRows,
      config: config || {},
    });
    _buildGrid(state);
    if (state.onChange) state.onChange(_getLayout(state));
    return state.widgets[state.widgets.length - 1].id;
  }

  function _removeWidget(state, id) {
    for (var i = 0; i < state.widgets.length; i++) {
      if (state.widgets[i].id === id) {
        state.widgets.splice(i, 1);
        break;
      }
    }
    _buildGrid(state);
    if (state.onChange) state.onChange(_getLayout(state));
  }

  function _updateWidget(state, id, newConfig) {
    for (var i = 0; i < state.widgets.length; i++) {
      if (state.widgets[i].id === id) {
        Object.assign(state.widgets[i], newConfig);
        break;
      }
    }
    _buildGrid(state);
    if (state.onChange) state.onChange(_getLayout(state));
  }

  function _getLayout(state) {
    return state.widgets.map(function(w) {
      return { id: w.id, type: w.type, title: w.title, col: w.col, row: w.row, cols: w.cols, rows: w.rows, config: w.config };
    });
  }

  function _reindex(state) {
    for (var i = 0; i < state.widgets.length; i++) {
      state.widgets[i]._index = i;
    }
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  window.DashboardGrid = { render: _render };
})();
