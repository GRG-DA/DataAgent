/**
 * Blank Dashboard Builder — Start from scratch with an empty canvas.
 * 85 widget types: 78 palette charts/controls + 7 map visuals.
 * Features: cross-filtering, drill-down, multi-page support.
 */
(function () {
  "use strict";

  const PAGE = "#blankdash";
  var _dash = {
    id: null, name: "Untitled Dashboard", table: "",
    pages: [{ id: "page_1", name: "Page 1", widgets: [] }],
    currentPage: 0, mode: "edit",
    canvasMinW: 1900, canvasMinH: 1080, fixedCanvas: true,
    gridMode: "dots",
    groups: {},
    stylePresets: {}
  };
  var _grid = null;
  var _seq = 0;
  var _filters = {};
  var _drillStack = [];
  /* P3.9 — report tooltip page (Power BI tooltip-page semantics): a widget tagged
     config.isTooltip is re-rendered in a floating panel, filtered by the hovered
     chart data point. */
  var _tooltipWidgetId = null;
  var _tooltipHoverFilters = null;
  var _autoRefreshTimer = null;
  var _watchSweepT1 = null, _watchSweepT2 = null;
  var _copilotHistory = [];   // chat thread: [{ role, text, html, badge }]
  var _copilotSession = "";   // backend session id for memory continuity
  var _copilotBusy = false;   // a streaming answer is in flight
  var _copilotPendingRecs = null; // cached "Recommend visuals" output for quick-add
  var _copilotHistoryLoaded = false; // lazy-load sessionStorage thread once
  var _copilotMsgSeq = 0;     // unique id for each chat message
  var _copilotVizLib = {};    // streamed chart previews: id -> plotly html

  /* ── Status bar / zoom popover / minimap module state ── */
  var _bdStatusThrottle = false;  // rAF throttle for status-bar pointer coords
  var _bdStatusLast = { x: null, y: null }; // last grid position shown
  var _bdZoomMenuEl = null;       // live zoom popover element
  var _bdMinimapEl = null;        // live minimap element
  var _bdMinimapOn = false;       // minimap visibility (edit mode)
  var _bdMinimapSkip = false;     // suppress minimap refresh during pan/scroll
  var _bdDraggingWidget = false;  // set during widget drag (minimap + status sync)
  var _bdGrowRafPending = false;  // rAF throttle for _growCanvas

  /* ── Query result cache (per dashboard session) ──
     Widget queries are deterministic per (page, widget config, table, filters),
     so identical SQL returning the exact same result set can be served from
     cache instead of re-hitting the warehouse on every render / filter change /
     auto-refresh sweep. Cap it so memory stays bounded. */
  var _queryCache = new Map();
  var _QUERY_CACHE_MAX = 150;
  function _queryCacheKey(sql) {
    return String(sql == null ? "" : sql)
      .replace(/\s+/g, " ").trim()
      .replace(/,\s*/g, ",")  // normalise ", " -> ","
      .replace(/\(\s*/g, "(").replace(/\s*\)/g, ")")
      .replace(/\)\s*U\s*N\s*I\s*O\s*N\s*\(/gi, ") UNION (");
  }
  function _queryCacheGet(sql) {
    var k = _queryCacheKey(sql);
    if (!_queryCache.has(k)) return null;
    var hit = _queryCache.get(k);
    hit.hits = (hit.hits || 0) + 1;
    return hit.value;
  }
  function _queryCacheSet(sql, value) {
    var k = _queryCacheKey(sql);
    _queryCache.delete(k);
    _queryCache.set(k, { value: value, time: Date.now() });
    if (_queryCache.size > _QUERY_CACHE_MAX) {
      var oldest = null;
      _queryCache.forEach(function (v, key) {
        if (!oldest || v.time < oldest.time) oldest = { key: key, time: v.time };
      });
      if (oldest) _queryCache.delete(oldest.key);
    }
  }
  function _queryCacheClear(dataChanged) {
    _queryCache.clear();
  }

  /* Full _dash config snapshot (used for server persistence + local cache). */
  function _dashConfigSnapshot() {
    return {
      id: _dash.id || null,
      name: _dash.name || "Untitled Dashboard",
      table: _dash.table || "",
      mode: _dash.mode || "edit",
      refreshSec: _dash.refreshSec || 0,
      theme: _dash.theme || "auto", themeMode: _dash.themeMode || "full",
      shineGradient: !!_dash.shineGradient, shineColors: _dash.shineColors,
      sharePublic: !!_dash.sharePublic,
      snap: _dash.snap, minW: _dash.minW, minH: _dash.minH,
      maxW: _dash.maxW, maxH: _dash.maxH,
      canvasMinW: _dash.canvasMinW, canvasMinH: _dash.canvasMinH,
      fixedCanvas: !!_dash.fixedCanvas,
      grid: _dash.grid,
      gridMode: _dash.gridMode || "dots",
      pages: _dash.pages, currentPage: _dash.currentPage,
      groups: _dash.groups || {},
      stylePresets: _dash.stylePresets || {},
      carouselSec: _dash.carouselSec || 5,
      _bookmarks: _dash._bookmarks || null,
      time: Date.now()
    };
  }

  // Resolves to { timedOut } after `ms` if `promise` is still pending, so long
  // LLM calls can never leave the copilot stuck on a spinner forever.
  function _copilotWithTimeout(promise, ms) {
    return new Promise(function (resolve) {
      var done = false;
      var timer = setTimeout(function () {
        if (!done) { done = true; resolve({ timedOut: true }); }
      }, ms);
      promise.then(function (value) {
        if (!done) { done = true; clearTimeout(timer); resolve({ timedOut: false, value: value }); }
      }, function (err) {
        if (!done) { done = true; clearTimeout(timer); resolve({ timedOut: false, error: err }); }
      });
    });
  }

  /* ── Copilot thread persistence (sessionStorage, survives reloads) ── */
  var _COPILOT_STORE_KEY = "bd_copilot_history_v1";
  function _copilotLoadHistory() {
    try {
      var raw = sessionStorage.getItem(_COPILOT_STORE_KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr.slice(-40) : [];
    } catch (e) { return []; }
  }
  function _copilotSaveHistory() {
    try { sessionStorage.setItem(_COPILOT_STORE_KEY, JSON.stringify(_copilotHistory.slice(-40))); } catch (e) { /* ignore */ }
  }
  function _copilotMsgUid() {
    _copilotMsgSeq++;
    return "cm" + Date.now().toString(36) + "_" + _copilotMsgSeq;
  }

  /* ── Free-form canvas metrics ── */
  var _UNIT_W = 120;   // px per catalogue column
  var _UNIT_H = 92;    // px per catalogue row
  var _GAP = 16;       // spacing between placed widgets
  var _CANVAS_MIN_W = 1600;
  var _CANVAS_MIN_H = 1200;
  var _zoom = 1;          // canvas zoom factor
  var _snap = 12;         // snap grid size in px (0 = no snapping)
  var _minW = 120, _minH = 80, _maxW = 2000, _maxH = 2000;
  var _selectedId = null; // primary / anchor widget id of the current selection
  var _selectedIds = [];  // full multi-selection list (primary first)
  var _copyPool = [];     // clipboard copy of selected widgets
  var _styleClipboard = null; // A4 — copied widget style (config.style + style-relevant config keys)
  var _nudgeNeedsBaseline = false; // keyboard nudge burst undo baselining flag
  var _dirty = false;         // unsaved-changes flag (drives the save badge / guards)
  var _autoSaveTimer = null;  // debounce handle for automatic saves
  var _TEMPLATE_KEY = "bd_widget_templates";
  var _lastPointerCX = null, _lastPointerCY = null; // last pointer on the canvas (for paste-at-cursor)
  var _showGrid = true;   // show dotted grid overlay on the canvas
  var _addSizeKey = "m";  // size preset in the Add Widget modal: s | m | l
  var _layerCollapsed = {}; // layer-group collapse state (Layers panel)
  var _lrSectionCollapsed = {}; // left-rail section collapse state: fields / measures / layers
  var _leftRailCollapsed = false; // Layers dock (left rail) collapse state
  var _relatedTables = [];      // extra tables whose fields appear in the Fields pane (joined)
  var _layerSearch = "";    // Layers panel search term
  var _fieldSearch = "";    // Fields pane search term
  var _measureSearch = "";  // Measures pane search term
  var _PRESET_CANVAS = { desktop: [1900, 1080], laptop: [1366, 768], tablet: [1024, 768], mobile: [390, 844] };
  function _canvasSizeKey() {
    var w = _dash.canvasMinW || _CANVAS_MIN_W, h = _dash.canvasMinH || _CANVAS_MIN_H;
    for (var k in _PRESET_CANVAS) if (_PRESET_CANVAS[k][0] === w && _PRESET_CANVAS[k][1] === h) return k;
    return "custom";
  }
  var _propsWidget = null;// widget object bound to the properties dock
  /* P3.12 — auto-playing bookmark/page carousel (present mode kiosk) */
  var _carousel = { on: false, timer: null, sec: 5, bIdx: -1 };
  /* C10 — session-only live annotations (per page id) */
  var _annotations = {};
  var _annTool = null;      // pen | highlight | arrow | box | text | eraser | null
  var _annColor = "#ff3b30";
  var _annDrawing = null;   // in-progress stroke
  var _annLayer = null;     // SVG overlay element
  var _annToolbarOn = false;
  var _undoStack = [];
  var _redoStack = [];
  var _lastSnap = null; // last-known widget state, used to record pre-change snapshots
  function gap() { return _GAP; }
  function _toPx(cols, rows) { return { width: cols * _UNIT_W, height: rows * _UNIT_H }; }
  /* First NON-EMPTY string among the arguments ("" / null / undefined are
     skipped). The chart-recommendation API returns the explanation under
     `reasoning` (both /llm/chart-recommend and the rule-based
     /analysis/chart-recommend), so the card must read that key — reading
     `why`/`reason` alone showed every recommendation as a bare title.
     `||` is wrong here too: an empty string would shadow a real value. */
  function _firstNonEmptyString() {
    for (var i = 0; i < arguments.length; i++) {
      var v = arguments[i];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    return "";
  }
  /* Convert a legacy grid widget ({col,row,cols,rows}) into a free-form pixel widget in-place */
  function _migrateFreeForm(list) {
    /* Layout-less widgets (only cols/rows, e.g. template builds) flow into a tiled grid that fits the canvas width */
    var flowX = _GAP, flowY = _GAP, rowH = 0;
    var cw = _baseCanvasW();
    var flowLimit = cw - _GAP; /* one full row wraps before leaving the canvas */
    var pitch = (cw - _GAP) / 12; /* 12-col grid pitched to the chosen canvas width */
    for (var i = 0; i < list.length; i++) {
      var w = list[i];
      if (w == null) continue;
      var size = null;
      if (w.x == null && w.col != null) {
        var cols = Math.max(1, w.cols || 4), rows = Math.max(1, w.rows || 2);
        var col = Math.max(1, w.col || 1), row = Math.max(1, w.row || 1);
        w.x = (col - 1) * (_UNIT_W + _GAP) + _GAP;
        w.y = (row - 1) * (_UNIT_H + _GAP) + _GAP;
        size = { width: cols * _UNIT_W + (cols - 1) * _GAP, height: rows * _UNIT_H + (rows - 1) * _GAP };
      } else if (w.x == null) {
        var fc = Math.max(1, w.cols || 4), fr = Math.max(1, w.rows || 2);
        size = { width: Math.max(40, Math.round(pitch * fc - _GAP)), height: fr * _UNIT_H + (fr - 1) * _GAP };
        if (flowX > _GAP && flowX + size.width > flowLimit) {
          flowX = _GAP;
          flowY += (rowH > 0 ? rowH : size.height) + _GAP;
          rowH = 0;
        }
        w.x = flowX;
        w.y = flowY;
        if (size.height > rowH) rowH = size.height;
        flowX += size.width + _GAP;
      }
      if (size) {
        if (w.width == null) w.width = size.width;
        if (w.height == null) w.height = size.height;
        if (w.zIndex == null) w.zIndex = i + 1;
        w.locked = !!w.locked;
      }
    }
  }
  function _clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  /* Per-type minimum widget size (KPI small chips, tables large, charts mid) */
  function _minSizeFor(w) {
    var t = w && w.type;
    if (["kpi", "gauge", "radial", "progress", "status", "bullet", "sparkline", "comparison", "targetactual", "watertank", "trendkpi", "percentagekpi", "prevkpi", "iconkpi", "multikpi", "sparkkpi", "sparkbarkpi", "minilinekpi", "sparkdeltakpi", "donutkpi"].indexOf(t) !== -1) return { w: 160, h: 100 };
    if (t === "table" || t === "grid") return { w: 320, h: 220 };
    if (_FILTER_TYPES.indexOf(t) !== -1) return { w: 220, h: 96 };
    if (t === "text" || t === "button" || t === "image" || t === "pageLink") return { w: 160, h: 60 };
    return { w: 280, h: 180 };
  }
  /* Build a labeled field row for the properties dock */
  function _propsField(label, controlHtml) {
    return '<div class="bd-props-field"><label class="bd-props-label">' + label + '</label><div class="bd-props-ctrl">' + controlHtml + '</div></div>';
  }
  /* Normalise any CSS color to a #rrggbb for a color input; blank -> default */
  function _toHex(color) {
    if (!color) return "#ffffff";
    var el = document.createElement("div");
    el.style.color = color;
    var rgb = el.style.color || "";
    var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(rgb);
    if (m) return "#" + ((1 << 24) + (parseInt(m[1],10)<<16) + (parseInt(m[2],10)<<8) + parseInt(m[3],10)).toString(16).slice(1);
    return /^#[0-9a-fA-F]{6}$/.test(color) ? color : "#ffffff";
  }

  /* ── CrossFilterBus ── */
  var CrossFilterBus = {
    _handlers: [],
    dispatch: function (field, value, sourceId, force) {
      if (value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0)) {
        delete _filters[field];
      } else if (!force && _filters[field] !== undefined && JSON.stringify(_filters[field]) === JSON.stringify(value)) {
        delete _filters[field];
      } else {
        _filters[field] = value;
      }
      this._handlers.forEach(function (h) { try { h(field, _filters[field], sourceId); } catch (e) {} });
      BlankDash._repaintOnFilter();
      return _filters[field];
    },
    listen: function (fn) { this._handlers.push(fn); },
    clear: function () {
      _filters = {};
      this._handlers.forEach(function (h) { try { h(null, null, null); } catch (e) {} });
      BlankDash._repaintOnFilter();
    },
    getFilters: function () { return Object.assign({}, _filters); }
  };
  window.CrossFilterBus = CrossFilterBus;

  /* Per-page filter memory (Power BI: each page keeps its own filter state).
     The current page's filter snapshot is stored on the page object itself
     (page.filters) so drill-through state survives a reload and a save. */
  function _pageFilterSnapshot() {
    return JSON.parse(JSON.stringify(Object.assign({}, _filters)));
  }
  function _storePageFilters(idx, snap) {
    if (_dash.pages[idx]) _dash.pages[idx].filters = snap;
  }
  function _restorePageFilters(idx) {
    var pg = _dash.pages[idx];
    var stored = (pg && pg.filters && typeof pg.filters === "object") ? pg.filters : null;
    _filters = {};
    if (stored) Object.keys(stored).forEach(function (k) {
      if (stored[k] != null) _filters[k] = JSON.parse(JSON.stringify(stored[k]));
    });
  }
  /* Resolve a target page from a number ("2"), a page name, or a page id. */
  function _resolvePageIndex(val) {
    var v = String(val == null ? "" : val).trim();
    var tIdx = -1;
    if (/^\d+$/.test(v)) tIdx = parseInt(v, 10) - 1;
    else if (v) {
      for (var p = 0; p < _dash.pages.length; p++)
        if (_dash.pages[p].name === v || _dash.pages[p].id === v) { tIdx = p; break; }
    }
    return (tIdx >= 0 && tIdx < _dash.pages.length) ? tIdx : -1;
  }

  function _currentWidgets() { return _dash.pages[_dash.currentPage].widgets; }
  function _findWidget(id) {
    for (var p = 0; p < _dash.pages.length; p++) {
      for (var i = 0; i < _dash.pages[p].widgets.length; i++) {
        if (_dash.pages[p].widgets[i].id === id) return _dash.pages[p].widgets[i];
      }
    }
    return null;
  }

  /* Base canvas dimensions (configurable via Settings, default to the module minimum) */
  function _baseCanvasW() { return Math.max(320, parseInt(_dash.canvasMinW, 10) || _CANVAS_MIN_W); }
  function _baseCanvasH() { return Math.max(240, parseInt(_dash.canvasMinH, 10) || _CANVAS_MIN_H); }

  /* Fill width for the canvas at 100% zoom: the wrap's visible content width, so a
     dashboard that is narrower than the viewport never leaves a blank right-hand gap. */
  function _canvasViewportW() {
    var wrap = document.querySelector(".bd-canvas-wrap");
    if (!wrap) return 0;
    var wps = getComputedStyle(wrap);
    return Math.max(0, wrap.clientWidth - (parseFloat(wps.paddingLeft) || 0) - (parseFloat(wps.paddingRight) || 0));
  }

  /* Repair common structural problems in a loaded dashboard config in place.
     Returns true when anything was corrected (so callers can inform the user). */
  function _sanitizeDashboard(dash) {
    if (!dash || typeof dash !== "object") return false;
    var changed = false;
    var dropped = 0;
    if (!Array.isArray(dash.pages)) {
      dash.pages = [{ id: "page_1", name: "Page 1", widgets: Array.isArray(dash.widgets) ? dash.widgets : [] }];
      changed = true;
    }
    if (!dash.pages.length) { dash.pages = [{ id: "page_1", name: "Page 1", widgets: [] }]; changed = true; }
    var pageCount = dash.pages.length;
    var seen = {};
    function _num(v, dflt) { var n = parseFloat(v); return isFinite(n) ? n : dflt; }
    var _ALIASES = { donut: "doughnut" };
    for (var p = 0; p < dash.pages.length; p++) {
      var pg = dash.pages[p];
      if (!pg || typeof pg !== "object") { dash.pages[p] = { id: "page_" + (p + 1), name: "Page " + (p + 1), widgets: [] }; changed = true; pg = dash.pages[p]; }
      if (!pg.id) { pg.id = "page_" + (p + 1); changed = true; }
      if (!Array.isArray(pg.widgets)) { pg.widgets = []; changed = true; }
      for (var i = pg.widgets.length - 1; i >= 0; i--) {
        var w = pg.widgets[i];
        if (!w || typeof w !== "object") { pg.widgets.splice(i, 1); changed = true; dropped++; continue; }
        if (typeof w.type !== "string" || !w.type) {
          pg.widgets.splice(i, 1); changed = true; dropped++; continue;
        }
        if (typeof w.id !== "string" || !w.id) {
          /* Generate a stable id rather than silently dropping the widget. */
          w.id = "w" + String(Date.now() % 0xFFFFFFF).toString(36) + "_" + i + "_" + (i + p);
          changed = true;
        }
        if (_ALIASES[w.type]) { w.type = _ALIASES[w.type]; }
        var isLayoutless = (w.x == null) && (w.col != null || w.cols != null || w.rows != null);
        if (isLayoutless) {
          /* Layout-less template widgets keep cols/rows so _migrateFreeForm can tile them */
          w.cols = parseInt(w.cols, 10) || 4;
          w.rows = parseInt(w.rows, 10) || 2;
          if (w.col != null) w.col = parseInt(w.col, 10) || 1;
          if (w.row != null) w.row = parseInt(w.row, 10) || 1;
          if (w.zIndex == null) w.zIndex = i + 1;
        } else {
          w.x = Math.max(0, Math.floor(_num(w.x, 0)));
          w.y = Math.max(0, Math.floor(_num(w.y, 0)));
          w.width = Math.max(40, Math.floor(_num(w.width, 300)));
          w.height = Math.max(30, Math.floor(_num(w.height, 150)));
        }
        w.zIndex = Math.max(0, Math.floor(_num(w.zIndex, i + 1)));
        if (typeof w.title !== "string" || !w.title) { w.title = "Widget"; changed = true; }
        if (seen[w.id]) { w.id = "w" + Date.now().toString(36) + "_r" + i; changed = true; }
        seen[w.id] = true;
        if (w.config && typeof w.config === "object" && typeof w.config.targetPage === "number") {
          var tp = Math.floor(w.config.targetPage);
          if (tp < 0 || tp > pageCount - 1) { w.config.targetPage = Math.max(0, Math.min(pageCount - 1, tp)); changed = true; }
        }
        if (typeof w.locked !== "boolean") w.locked = !!w.locked;
      }
    }
    if (typeof dash.currentPage !== "number" || dash.currentPage < 0 || dash.currentPage > pageCount - 1) {
      dash.currentPage = 0; changed = true;
    }
    /* Return the number of widgets dropped (truthy when reparis happened) so
       callers can show a truthful message instead of a generic "repaired". */
    if (dropped) return dropped;
    return changed;
  }

  /* ── Widget catalogue ── */
  var _CATALOGUE = [
    { type: "kpi",          label: "KPI Card",         icon: "fa-chart-simple",           color: "#2563eb", size: [3,2],  cat: "Basics" },
    { type: "table",        label: "Data Table",       icon: "fa-table",                  color: "#64748B", size: [12,3], cat: "Basics" },
    { type: "text",         label: "Text Box",         icon: "fa-font",                   color: "#4F46E5", size: [12,2], cat: "Basics" },
    { type: "line",         label: "Line Chart",       icon: "fa-chart-line",             color: "#2563eb", size: [6,3],  cat: "Standard Charts" },
    { type: "bar",          label: "Bar Chart",        icon: "fa-chart-bar",              color: "#10B981", size: [6,3],  cat: "Standard Charts" },
    { type: "column",       label: "Column Chart",     icon: "fa-chart-column",           color: "#b45309", size: [6,3],  cat: "Standard Charts" },
    { type: "area",         label: "Area Chart",       icon: "fa-chart-area",             color: "#0d9488", size: [6,3],  cat: "Standard Charts" },
    { type: "pie",          label: "Pie Chart",        icon: "fa-chart-pie",              color: "#be123c", size: [4,3],  cat: "Standard Charts" },
    { type: "doughnut",     label: "Doughnut",         icon: "fa-circle-notch",           color: "#7c3aed", size: [4,3],  cat: "Standard Charts" },
    { type: "scatter",      label: "Scatter Plot",     icon: "fa-braille",                color: "#1d4ed8", size: [6,3],  cat: "Advanced Charts" },
    { type: "bubble",       label: "Bubble Chart",     icon: "fa-circle",                 color: "#1d4ed8", size: [6,3],  cat: "Advanced Charts" },
    { type: "radar",        label: "Radar Chart",      icon: "fa-spider",                 color: "#2563eb", size: [6,3],  cat: "Advanced Charts" },
    { type: "combo",        label: "Combo Chart",      icon: "fa-chart-line fa-chart-bar",color: "#7C3AED", size: [6,3],  cat: "Advanced Charts" },
    { type: "horizontalbar",label: "Horizontal Bar",   icon: "fa-bars",                   color: "#059669", size: [6,3],  cat: "Advanced Charts" },
    { type: "stackedbar",   label: "Stacked Bar",      icon: "fa-layer-group",            color: "#b91c1c", size: [6,3],  cat: "Advanced Charts" },
    { type: "stackedarea",  label: "Stacked Area",     icon: "fa-layer-group",            color: "#0891B2", size: [6,3],  cat: "Advanced Charts" },
    { type: "percentbar",   label: "100% Stacked Bar", icon: "fa-percent",                color: "#9333EA", size: [6,3],  cat: "Advanced Charts" },
    { type: "sparkline",    label: "Sparkline",        icon: "fa-wave-square",            color: "#0d9488", size: [6,2],  cat: "Advanced Charts" },
    { type: "heatmap",      label: "Heatmap",          icon: "fa-border-all",             color: "#D946EF", size: [6,4],  cat: "Data Viz" },
    { type: "gauge",        label: "Gauge",            icon: "fa-gauge-high",             color: "#F97316", size: [3,3],  cat: "Data Viz" },
    { type: "funnel",       label: "Funnel",           icon: "fa-filter",                 color: "#b91c1c", size: [6,3],  cat: "Data Viz" },
    { type: "treemap",      label: "Treemap",          icon: "fa-table-cells-large",      color: "#0891B2", size: [6,4],  cat: "Data Viz" },
    { type: "waterfall",    label: "Waterfall",        icon: "fa-water",                  color: "#2563EB", size: [6,3],  cat: "Data Viz" },
    { type: "polar",        label: "Polar Area",       icon: "fa-compass",                color: "#EA580C", size: [6,3],  cat: "Data Viz" },
    { type: "boxplot",      label: "Box Plot",         icon: "fa-square",                 color: "#475569", size: [6,3],  cat: "Statistical" },
    { type: "histogram",    label: "Histogram",        icon: "fa-chart-bar",              color: "#2563eb", size: [6,3],  cat: "Statistical" },
    { type: "bullet",       label: "Bullet Chart",     icon: "fa-bullseye",               color: "#B91C1C", size: [6,3],  cat: "Statistical" },
    { type: "pyramid",      label: "Pyramid",          icon: "fa-caret-down",             color: "#b45309", size: [6,4],  cat: "Specialized" },
    { type: "sunburst",     label: "Sunburst",         icon: "fa-sun",                    color: "#E11D48", size: [6,4],  cat: "Specialized" },
    { type: "wordcloud",    label: "Word Cloud",       icon: "fa-cloud",                  color: "#7C3AED", size: [6,3],  cat: "Specialized" },
    { type: "progress",     label: "Progress Ring",    icon: "fa-circle-half-stroke",     color: "#10B981", size: [3,3],  cat: "Specialized" },
    { type: "slicer",       label: "Dropdown Slicer",  icon: "fa-list-check",             color: "#2563EB", size: [3,2],  cat: "Filters" },
    { type: "daterange",    label: "Date Range",       icon: "fa-calendar-days",          color: "#7C3AED", size: [3,2],  cat: "Filters" },
    { type: "searchbox",    label: "Search Filter",    icon: "fa-magnifying-glass",       color: "#0d9488", size: [3,2],  cat: "Filters" },
    { type: "image",        label: "Image",            icon: "fa-image",                  color: "#15803d", size: [4,3],  cat: "Media" },
    { type: "shape",        label: "Shape",            icon: "fa-shapes",                 color: "#2563eb", size: [3,2],  cat: "Media" },
    { type: "divider",      label: "Divider",          icon: "fa-minus",                  color: "#94A3B8", size: [12,1], cat: "Media" },
    { type: "custom",       label: "Custom HTML",      icon: "fa-code",                   color: "#0F766E", size: [6,3],  cat: "Media" },
    { type: "page",         label: "Page Link",        icon: "fa-arrow-right-to-bracket", color: "#1d4ed8", size: [3,2],  cat: "Navigation" },
    { type: "button",       label: "Action Button",    icon: "fa-circle-play",            color: "#7C3AED", size: [3,2],  cat: "Navigation" },
    { type: "radial",       label: "Radial Progress",  icon: "fa-circle-dot",             color: "#10B981", size: [3,3],  cat: "Basics" },
    { type: "status",       label: "Status Indicator", icon: "fa-circle-check",           color: "#2563EB", size: [3,3],  cat: "Basics" },
    { type: "comparison",   label: "Comparison KPI",   icon: "fa-scale-balanced",         color: "#7C3AED", size: [4,3],  cat: "Basics" },
    { type: "targetactual", label: "Target vs Actual", icon: "fa-crosshairs",             color: "#b45309", size: [3,3],  cat: "Basics" },
    { type: "stepline",     label: "Step Line Chart",  icon: "fa-step-forward",           color: "#2563eb", size: [6,3],  cat: "Standard Charts" },
    { type: "smoothline",   label: "Smooth Line",      icon: "fa-wave-sine",              color: "#1d4ed8", size: [6,3],  cat: "Standard Charts" },
    { type: "splinearea",   label: "Smooth Area",      icon: "fa-mountain-sun",           color: "#0d9488", size: [6,3],  cat: "Standard Charts" },
    { type: "lollipop",     label: "Lollipop Chart",   icon: "fa-candy-cane",             color: "#DB2777", size: [6,3],  cat: "Advanced Charts" },
    { type: "pareto",       label: "Pareto Chart",     icon: "fa-bars-progress",          color: "#F97316", size: [6,3],  cat: "Statistical" },
    { type: "rose",         label: "Rose Chart",       icon: "fa-asterisk",               color: "#be123c", size: [6,4],  cat: "Specialized" },
    { type: "radialbar",    label: "Radial Bar Chart", icon: "fa-record-vinyl",           color: "#7c3aed", size: [6,4],  cat: "Specialized" },
    { type: "waffle",       label: "Waffle Chart",     icon: "fa-table-cells",            color: "#0d9488", size: [6,4],  cat: "Data Viz" },
    { type: "calendar",     label: "Calendar Heatmap",icon: "fa-calendar",               color: "#0d9488", size: [6,4],  cat: "Data Viz" },
    { type: "bubblegrid",   label: "Bubble Grid",     icon: "fa-atom",                   color: "#15803d", size: [6,4],  cat: "Data Viz" },
    { type: "watertank",    label: "Water Tank",      icon: "fa-glass-water",            color: "#1d4ed8", size: [3,3],  cat: "Data Viz" },
    { type: "ranking",      label: "Leaderboard",     icon: "fa-ranking-star",           color: "#EAB308", size: [6,3],  cat: "Specialized" },
    { type: "gantt",        label: "Gantt Timeline",  icon: "fa-timeline",               color: "#2563eb", size: [6,3],  cat: "Specialized" },
    { type: "diverging",    label: "Diverging Bar",   icon: "fa-bars-staggered",         color: "#b91c1c", size: [6,3],  cat: "Statistical" },
    { type: "trendkpi",     label: "Trend KPI",       icon: "fa-arrow-trend-up",         color: "#15803d", size: [3,2],  cat: "Basics" },
    { type: "percentagekpi",label: "% Change KPI",    icon: "fa-percent",                color: "#1d4ed8", size: [3,2],  cat: "Basics" },
    { type: "prevkpi",      label: "Prev Period KPI", icon: "fa-clock-rotate-left",      color: "#b45309", size: [3,2],  cat: "Basics" },
    { type: "iconkpi",      label: "Icon KPI",        icon: "fa-star",                   color: "#EAB308", size: [3,2],  cat: "Basics" },
    { type: "multikpi",     label: "Multi-Value KPI", icon: "fa-cubes",                  color: "#7c3aed", size: [4,2],  cat: "Basics" },
    { type: "metriccard",   label: "Metric Card",    icon: "fa-th-large",               color: "#7C3AED", size: [8,2],  cat: "Basics" },
    { type: "sparkkpi",     label: "Sparkline KPI",   icon: "fa-wave-square",            color: "#0d9488", size: [3,2],  cat: "Basics" },
    { type: "sparkbarkpi",  label: "Mini Bar KPI",    icon: "fa-signal",                 color: "#10B981", size: [3,2],  cat: "Basics" },
    { type: "minilinekpi",  label: "Mini Line KPI",   icon: "fa-chart-line",             color: "#2563eb", size: [3,2],  cat: "Basics" },
    { type: "sparkdeltakpi", label: "Sparkline Delta KPI", icon: "fa-wave-square",       color: "#0d9488", size: [3,2],  cat: "Basics" },
    { type: "donutkpi",      label: "Donut KPI",       icon: "fa-circle-notch",           color: "#7c3aed", size: [3,2],  cat: "Basics" },
    { type: "slope",        label: "Slope Chart",     icon: "fa-arrow-trend-up",         color: "#228BE6", size: [6,3],  cat: "Specialized" },
    { type: "sankey",       label: "Sankey Diagram",  icon: "fa-code-branch",           color: "#0d9488", size: [6,4],  cat: "Specialized" },
    { type: "chord",        label: "Chord Diagram",  icon: "fa-circle-nodes",          color: "#7c3aed", size: [6,4],  cat: "Specialized" },
    { type: "ridgeline",    label: "Ridgeline Plot", icon: "fa-water-ladder",          color: "#1d4ed8", size: [6,4],  cat: "Statistical" },
    { type: "beeswarm",     label: "Beeswarm Plot",  icon: "fa-braille",               color: "#b45309", size: [6,4],  cat: "Statistical" },
    { type: "stream",       label: "Stream Graph",    icon: "fa-wind",                   color: "#1d4ed8", size: [6,4],  cat: "Advanced Charts" },
    { type: "population",   label: "Population Pyramid", icon: "fa-people-roof",         color: "#b45309", size: [6,4],  cat: "Statistical" },
    { type: "venn",         label: "Venn Diagram",    icon: "fa-circle-nodes",           color: "#7c3aed", size: [5,4],  cat: "Data Viz" },
    { type: "numrange",     label: "Number Range",    icon: "fa-sliders",                color: "#1d4ed8", size: [4,2],  cat: "Filters" },
    { type: "hierarchy",    label: "Hierarchy Slicer",icon: "fa-sitemap",                color: "#b45309", size: [4,3],  cat: "Filters" },
    { type: "relativedate", label: "Relative Date",   icon: "fa-clock",                  color: "#10B981", size: [3,2],  cat: "Filters" },
    { type: "topn",         label: "Top N Filter",    icon: "fa-ranking-star",           color: "#b91c1c", size: [3,3],  cat: "Filters" },
    { type: "filterpane",   label: "Filter Pane",     icon: "fa-filter",                 color: "#2563EB", size: [4,4],  cat: "Filters" },
    { type: "globalsearch", label: "Global Search",   icon: "fa-magnifying-glass",       color: "#0d9488", size: [4,2],  cat: "Filters" },
  ];

  var _TITLES = {};
  _CATALOGUE.forEach(function (c) { _TITLES[c.type] = c.label; });

  /* ── Geographic map visuals (interactive Leaflet.js maps) ── */
  var _MAP_CATALOGUE = [
    { type: "geomap",   label: "Marker Map",   icon: "fa-location-dot",    color: "#2563EB", size: [6,4], cat: "Maps" },
    { type: "countrymap", label: "Bubble Sizing Map", icon: "fa-earth-americas", color: "#7C3AED", size: [6,4], cat: "Maps" },
    { type: "shapemap", label: "Shape Map",   icon: "fa-draw-polygon",    color: "#0EA5A4", size: [6,4], cat: "Maps" },
    { type: "dotmap",   label: "Dot Distribution Map", icon: "fa-circle-dot", color: "#b45309", size: [6,4], cat: "Maps" },
    { type: "heatmapmap", label: "Heatmap Map", icon: "fa-temperature-high", color: "#b91c1c", size: [6,4], cat: "Maps" },
    { type: "flowmap",  label: "Flow Map",    icon: "fa-right-left",     color: "#10B981", size: [6,4], cat: "Maps" },
    { type: "glmap",    label: "GL Map (mapcn)", icon: "fa-map",         color: "#FF6B6B", size: [6,4], cat: "Maps" },
  ];
  /* Groups any map-type widget to the "Maps" category for lookup/ordering */
  _MAP_CATALOGUE.forEach(function (m) { _TITLES[m.type] = m.label; });

  function _isMapType(type) {
    for (var i = 0; i < _MAP_CATALOGUE.length; i++) if (_MAP_CATALOGUE[i].type === type) return true;
    return false;
  }
  function _allCatalogue() { return _CATALOGUE.concat(_MAP_CATALOGUE); }
  // Global reference so other pages (Dashboard Studio AI Auto-Build widget picker)
  // can reuse the exact same catalogue instead of duplicating the list.
  window.DashVisCatalogue = _allCatalogue();

  function _lookup(type) {
    var all = _allCatalogue();
    for (var i = 0; i < all.length; i++) {
      if (all[i].type === type) return all[i];
    }
    return { type: type, label: _TITLES[type] || _capitalize(type), icon: "fa-chart-simple", color: "#2563eb", size: [6,3], cat: "Other" };
  }

  /* ── Chart sub-types (rendered by bundle via type="chart") ── */
  var _CHART_SUBTYPES = ["line","bar","column","area","pie","doughnut","scatter","bubble","radar",
    "heatmap","funnel","treemap","waterfall","combo","horizontalbar","stackedbar","sparkline","gauge"];

  /* ── Filter widget types ── */
  var _FILTER_TYPES = ["slicer", "daterange", "searchbox", "numrange", "hierarchy", "relativedate", "topn", "filterpane", "globalsearch"];

  /* ── Registered measures (persistent calculation layer, /measures API) ── */
  var _measures = [];
  function _measureById(id) {
    for (var i = 0; i < _measures.length; i++) if (_measures[i].id === id) return _measures[i];
    return null;
  }

  /* ── Types that need their own top-level renderer ── */
  var _CUSTOM_RENDER = ["polar","boxplot","histogram","bullet","pyramid","sunburst","wordcloud","progress","stackedarea","percentbar","population","sankey","slope","stream","venn","chord","ridgeline","beeswarm"];

  /* ── Default SQL templates ── */
  /* P3.8 — multi-metric card: one UNION query (a row per measure: label,
     value, previous) plus an optional 12-month strip UNION (metric, value).
     Reuses BITimeIntel.build() per measure so periods stay uniform. */
  function _metricCardSql(measures, table, dateCol, where) {
    if (!measures || !measures.length || !table || !window.BITimeIntel) return { sql: "", sparkSql: "" };
    var parts = [], sparkParts = [];
    measures.forEach(function (m) {
      if (!m) return;
      /* measure-select edits persist only the id; resolve expr/date/format from
         the registry here so the very first fetch after a reload is correct */
      if (!m.expr && m.id) {
        var reg = _measureById(m.id);
        if (reg) {
          m.expr = reg.expression;
          m.format = m.format || reg.format || "auto";
          if (reg.date_column) m.dateCol = reg.date_column;
        }
      }
      if (!m.expr) return;
      var mb = window.BITimeIntel.build(m.expr, table, m.dateCol || dateCol || "", m.timeIntel || "none", where ? { where: where } : null);
      if (!mb || !mb.sql) return;
      var lbl = String(m.label == null ? m.id : m.label).replace(/'/g, "''");
      parts.push("SELECT '" + lbl + "' AS label, w.value, w.previous FROM (" + mb.sql + ") w");
      if (mb.mode !== "none" && mb.spark_sql) {
        sparkParts.push("SELECT '" + lbl + "' AS metric, w.value FROM (" + mb.spark_sql + ") w");
      }
    });
    return { sql: parts.join(" UNION ALL "), sparkSql: sparkParts.join(" UNION ALL ") };
  }
  function _defaultSql(type, table) {
    if (!table) return "";
    var t = '"' + table + '"';
    var cols = [];
    try { cols = (App.state.tables[table] || {}).columns || []; } catch (e) {}
    var colNames = cols.map(function(c) { return c.column_name || c.name || c; });
    var _ctype = function(c) { return (c.column_type || c.type || "").toLowerCase(); };
    var labelCol = null;
    var _ctype = function(c) { return (c.column_type || c.type || "").toLowerCase(); };
    var _notCatLike = /date|day|month|year|time|^dt$|^id$|_id$|uuid|guid|hash|email|url|phone|zip|postal|address|_key$|key$/i;
    var _catPrefer = /category|subcat|region|channel|segment|product|brand|country|state|province|city|dept|department|team|group|status|priority|gender|generation|tier|type|person|salesperson|rep|owner|employee|analyst|advisor|seller|name$/i;
    for (var pass = 0; pass < 2 && !labelCol; pass++) {
      for (var i = 0; i < cols.length; i++) {
        var cn = cols[i].column_name || cols[i].name || cols[i];
        var ct = _ctype(cols[i]);
        var isText = ct.indexOf("varchar") !== -1 || ct.indexOf("string") !== -1 || ct.indexOf("text") === 0 || ct === "" || ct === "enum" || ct === "json";
        if (!isText) continue;
        if (_notCatLike.test(cn)) continue;
        if (pass === 0 && !_catPrefer.test(cn)) continue;
        labelCol = cn; break;
      }
    }
    if (!labelCol) labelCol = colNames[0] || '"label"';
    else labelCol = '"' + labelCol + '"';
    var valCol = null;
    for (var i = 0; i < cols.length; i++) {
      var cn = cols[i].column_name || cols[i].name || cols[i];
      var ct = _ctype(cols[i]);
      if (ct.indexOf("int") !== -1 || ct.indexOf("float") !== -1 || ct.indexOf("double") !== -1 || ct.indexOf("decimal") !== -1 || ct.indexOf("numeric") !== -1 || cn.toLowerCase().indexOf("revenue") !== -1 || cn.toLowerCase().indexOf("profit") !== -1 || cn.toLowerCase().indexOf("quantity") !== -1) {
        valCol = cn; break;
      }
    }
    if (!valCol) valCol = '"value"';
    else valCol = '"' + valCol + '"';
    // Geo column resolver for map visuals: prefer a real geographic field.
    var GEO_ORDER = ["country", "region", "state", "province", "city", "territory", "area", "location", "market"];
    var geoColName = null;
    for (var gi = 0; gi < GEO_ORDER.length && !geoColName; gi++) {
      for (var gj = 0; gj < cols.length; gj++) {
        var gcn = (cols[gj].column_name || cols[gj].name || cols[gj]).toLowerCase().replace(/[_\s]/g, "");
        if (gcn.indexOf(GEO_ORDER[gi]) !== -1) { geoColName = cols[gj].column_name || cols[gj].name || cols[gj]; break; }
      }
    }
    // For daterange, prefer a date/timestamp column
    var dateCol = null, dateColRaw = null;
    for (var i = 0; i < cols.length; i++) {
      var cn = cols[i].column_name || cols[i].name || cols[i];
      var ct = _ctype(cols[i]);
      if (ct.indexOf("date") !== -1 || ct.indexOf("timestamp") !== -1) { dateCol = '"' + cn + '"'; dateColRaw = cn; break; }
    }

    var _filtered = function(sql) {
      /* Filters are injected dynamically by _applyFilters() at render time —
         never baked into a widget's stored SQL here. Baking the CURRENT global
         filter state at creation both goes stale and produces invalid clauses
         ("WHERE ... FROM ..." instead of "FROM ... WHERE ..."). */
      return sql;
    };

    switch (type) {
      case "metriccard": {
        var mcMs = [];
        try {
          (_measures || []).slice(0, 4).forEach(function (m) {
            if (m && m.expression) mcMs.push({ id: m.id, label: m.name || m.id, expr: m.expression, timeIntel: m.date_column ? "ytd" : "none", format: m.format || "auto" });
          });
        } catch (e) {}
        var mcB = mcMs.length ? _metricCardSql(mcMs, table, dateColRaw || "", "") : null;
        if (mcB && mcB.sql) return mcB.sql;
        return _filtered('SELECT SUM(' + valCol + ') AS value, NULL AS previous FROM ' + t);
      }
      case "kpi":       return _filtered('SELECT COUNT(*) AS value FROM ' + t);
      case "gauge":     return _filtered('SELECT SUM(' + valCol + ') AS value FROM ' + t);
      case "progress":  return _filtered('SELECT SUM(' + valCol + ') AS value, (SELECT SUM(' + valCol + ') FROM ' + t + ' WHERE 1=1) AS total FROM ' + t + ' LIMIT 1');
      case "radial":    return _filtered('SELECT SUM(' + valCol + ') AS value, 100 AS target FROM ' + t + ' LIMIT 1');
      case "watertank": return _filtered('SELECT SUM(' + valCol + ') AS value, 100 AS target FROM ' + t + ' LIMIT 1');
      case "status":    return _filtered('SELECT COUNT(*) AS value FROM ' + t);
      case "comparison":return _filtered('SELECT SUM(' + valCol + ') AS value, ROUND(AVG(' + valCol + '), 2) AS comparison FROM ' + t);
      case "targetactual": return _filtered('SELECT SUM(' + valCol + ') AS actual, ROUND(AVG(' + valCol + '), 2) AS target FROM ' + t);
      case "trendkpi":
      case "percentagekpi":
      case "prevkpi":
      case "iconkpi":   return _filtered('SELECT SUM(' + valCol + ') AS value, ROUND(AVG(' + valCol + '), 2) AS comparison FROM ' + t);
      case "multikpi":  return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 4');
      case "sparkkpi":
      case "sparkbarkpi":
      case "minilinekpi":
      case "sparkdeltakpi": return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 12');
      case "donutkpi":  return _filtered('SELECT SUM(' + valCol + ') AS value, 100 AS target FROM ' + t + ' LIMIT 1');
      case "table":     return _filtered('SELECT * FROM ' + t + ' LIMIT 50');
      case "text":      return "";
      case "shape":
      case "divider":   return "";
      case "slicer":    return 'SELECT DISTINCT ' + labelCol + ' AS label FROM ' + t + ' ORDER BY label';
      case "daterange": return 'SELECT MIN(' + (dateCol || labelCol) + ') AS min_val, MAX(' + (dateCol || labelCol) + ') AS max_val FROM ' + t;
      case "searchbox": return 'SELECT DISTINCT ' + labelCol + ' AS label FROM ' + t + ' ORDER BY label';
      case "numrange":  return 'SELECT MIN(CAST(' + valCol + ' AS DOUBLE)) AS min_val, MAX(CAST(' + valCol + ' AS DOUBLE)) AS max_val FROM ' + t;
      case "hierarchy": return 'SELECT DISTINCT ' + labelCol + ' AS label FROM ' + t + ' ORDER BY label';
      case "relativedate": return '';
      case "topn":      return 'SELECT ' + labelCol + ' AS label, SUM(CAST(' + valCol + ' AS DOUBLE)) AS n FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY n DESC LIMIT 10';
      case "filterpane":  return '';
      case "globalsearch": return '';
      case "boxplot":   return _filtered('SELECT ' + labelCol + ' AS label, ' + valCol + ' AS value FROM ' + t);
      case "histogram": return _filtered('SELECT ' + valCol + ' AS value FROM ' + t);
      case "bullet":    return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value, AVG(' + valCol + ') AS target FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 8');
      case "funnel":    return _filtered('SELECT ' + labelCol + ' AS label, COUNT(*) AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 8');
      case "treemap":   return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 12');
      case "heatmap":   return _filtered('SELECT ' + labelCol + ' AS label, ' + (colNames[1] && colNames[1] !== labelCol.replace(/"/g,'') ? '"' + colNames[1] + '"' : labelCol) + ' AS category, COUNT(*) AS value FROM ' + t + ' GROUP BY ' + labelCol + ', ' + (colNames[1] && colNames[1] !== labelCol.replace(/"/g,'') ? '"' + colNames[1] + '"' : labelCol) + ' LIMIT 100');
      case "radar":     return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 8');
      case "waterfall": return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 8');
      case "sparkline": return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 20');
      case "polar":     return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 8');
      case "pyramid":   return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 8');
      case "sunburst":  return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 12');
      case "wordcloud": return _filtered('SELECT ' + labelCol + ' AS label, COUNT(*) AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 40');
      case "combo":     return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value, AVG(' + valCol + ') AS avg_val FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 12');
      case "geomap":
      case "countrymap":
      case "glmap":
        var gCol = geoColName ? ('"' + geoColName + '"') : labelCol;
        return _filtered('SELECT ' + gCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + gCol + ' ORDER BY value DESC LIMIT 12');
      case "dotmap":
        var gCol3 = geoColName ? ('"' + geoColName + '"') : labelCol;
        // One dot per record: COUNT(*) per region is the density to scatter.
        return _filtered('SELECT ' + gCol3 + ' AS label, COUNT(*) AS value FROM ' + t + ' GROUP BY ' + gCol3 + ' ORDER BY value DESC LIMIT 300');
      case "heatmapmap":
        var gCol4 = geoColName ? ('"' + geoColName + '"') : labelCol;
        return _filtered('SELECT ' + gCol4 + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + gCol4 + ' ORDER BY value DESC LIMIT 300');
      case "shapemap":
        var gCol2 = geoColName ? ('"' + geoColName + '"') : labelCol;
        return _filtered('SELECT ' + gCol2 + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + gCol2 + ' ORDER BY value DESC LIMIT 300');
      case "flowmap":
      case "sankey":
      case "chord": {
        // Origin/destination/measure detection for connection arcs.
        var originCol2 = null, destCol2 = null;
        var colLC2 = cols.map(function (c) { return ((c.column_name || c.name || c).toString()); });
        var _hc2 = function (re) { for (var k = 0; k < colLC2.length; k++) { if (re.test(colLC2[k])) return colLC2[k]; } return null; };
        originCol2 = _hc2(/^from$|^origin$|^source$|^sender$|^departure|^src$|_from$|_origin$|_source$/i) ||
                    (colLC2.find(function (x) { return /city|state|country|region|location|airport|port|origin|from/i.test(x); }));
        destCol2 = _hc2(/^to$|^dest$|^destination$|^target$|^recipient$|^arrival$|^dst$|_to$|_dest$|_destination$|^arrival$/i) ||
                  (colLC2.find(function (x) { return /city|state|country|region|location|airport|port|dest|to/i.test(x) && x !== originCol2; }));
        if (!originCol2 && !destCol2) {
          if (colLC2.length >= 2) { originCol2 = colLC2[0]; destCol2 = colLC2[1]; }
        }
        var oQ2 = (originCol2 || labelCol).replace(/"/g, '""');
        var dQ2 = (destCol2 || oQ2).replace(/"/g, '""');
        // valCol is already a quoted identifier (e.g. "Quantity"), reuse as-is.
        var mQ2 = valCol;
        var srcAlias = (type === "sankey" || type === "chord") ? "source" : "origin";
        var dstAlias = (type === "sankey" || type === "chord") ? "target" : "destination";
        return _filtered('SELECT "' + oQ2 + '" AS ' + srcAlias + ', "' + dQ2 + '" AS ' + dstAlias + ', SUM(' + mQ2 + ') AS value FROM ' + t + ' GROUP BY 1, 2 ORDER BY value DESC LIMIT 300');
      }
      case "ridgeline": {
        // Distribution ridges: category + value rows (one row per record group).
        return _filtered('SELECT ' + labelCol + ' AS label, ' + valCol + ' AS value FROM ' + t + ' ORDER BY ' + labelCol + ', value LIMIT 500');
      }
      case "beeswarm": {
        // One dot per record: category + value (raw rows, no aggregation).
        return _filtered('SELECT ' + labelCol + ' AS label, ' + valCol + ' AS value FROM ' + t + ' ORDER BY ' + labelCol + ', value LIMIT 500');
      }
      case "slope": {
        // Start→End per category when a time axis exists, else compare vs baseline.
        if (dateCol) {
          return 'WITH _b AS (SELECT ' + labelCol + ' AS _lbl, date_trunc(\'day\', ' + dateCol + ') AS _d, SUM(' + valCol + ') AS _v FROM ' + t +
            ' GROUP BY 1, 2), _p AS (SELECT _lbl, min_by(_v, _d) AS start_v, max_by(_v, _d) AS end_v, COUNT(*) AS _n FROM _b GROUP BY 1) ' +
            'SELECT _lbl AS label, start_v, end_v FROM _p WHERE _n >= 2 ORDER BY (end_v - start_v) DESC LIMIT 12';
        }
        return _filtered('SELECT ' + labelCol + ' AS label, 0 AS start_v, SUM(' + valCol + ') AS end_v FROM ' + t + ' GROUP BY 1 ORDER BY end_v DESC LIMIT 12');
      }
      case "stream": {
        // Multi-series streamgraph: category + two measures (second numeric col if any).
        var s2Col = null;
        for (var si = 0; si < cols.length; si++) {
          var scn = (cols[si].column_name || cols[si].name || cols[si]).toLowerCase();
          var sct = _ctype(cols[si]);
          var scnQ = '"' + (cols[si].column_name || cols[si].name || cols[si]).replace(/"/g, '""') + '"';
          if (scnQ !== valCol && (sct.indexOf("int") !== -1 || sct.indexOf("float") !== -1 || sct.indexOf("double") !== -1 || sct.indexOf("decimal") !== -1 || scn.indexOf("revenue") !== -1 || scn.indexOf("profit") !== -1 || scn.indexOf("quantity") !== -1 || scn.indexOf("count") !== -1)) { s2Col = scnQ; break; }
        }
        return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS s1, ' + (s2Col ? 'SUM(' + s2Col + ')' : 'AVG(' + valCol + ')') + ' AS s2 FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY s1 DESC LIMIT 12');
      }
      case "population": {
        // Pyramid when the table has male/female columns, else mirror two aggregates.
        var mCol = null, fCol = null;
        for (var pi = 0; pi < cols.length; pi++) {
          var pcn = ((cols[pi].column_name || cols[pi].name || cols[pi]).toString()).toLowerCase();
          var pct = _ctype(cols[pi]);
          if (pct.indexOf("int") !== -1 || pct.indexOf("float") !== -1 || pct.indexOf("double") !== -1 || pct.indexOf("decimal") !== -1 || pct.indexOf("numeric") !== -1) {
            if (/^(male|men|males)$/.test(pcn) || /^(male|men|males)_/.test(pcn)) mCol = cols[pi].column_name || cols[pi].name || cols[pi];
            if (/^(female|women|females)$/.test(pcn) || /^(female|women|females)_/.test(pcn)) fCol = cols[pi].column_name || cols[pi].name || cols[pi];
          }
        }
        if (mCol && fCol) {
          return _filtered('SELECT ' + labelCol + ' AS label, SUM("' + mCol.replace(/"/g, '""') + '") AS male, SUM("' + fCol.replace(/"/g, '""') + '") AS female FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY label LIMIT 20');
        }
        return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS male, COUNT(*) AS female FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY label LIMIT 20');
      }
      case "venn":
        return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 3');
      default:          return _filtered('SELECT ' + labelCol + ' AS label, SUM(' + valCol + ') AS value FROM ' + t + ' GROUP BY ' + labelCol + ' ORDER BY value DESC LIMIT 12');
    }
  }

    function _detectFilterField(table, type) {
    if (_FILTER_TYPES.indexOf(type) === -1) return null;
    var cols = [];
    try { cols = (App.state.tables[table] || {}).columns || []; } catch (e) {}
    // Date range / relative date should target a date column
    if (type === "daterange" || type === "relativedate") {
      for (var i = 0; i < cols.length; i++) {
        var cn = cols[i].column_name || cols[i].name || cols[i];
        var ct = (cols[i].column_type || cols[i].type || "").toLowerCase();
        if (ct.indexOf("date") !== -1 || ct.indexOf("timestamp") !== -1) return cn;
      }
      return cols.length ? (cols[0].column_name || cols[0].name || cols[0]) : null;
    }
    // Number range / top N should target a numeric column
    if (type === "numrange" || type === "topn") {
      for (var i = 0; i < cols.length; i++) {
        var cn = cols[i].column_name || cols[i].name || cols[i];
        var ct = (cols[i].column_type || cols[i].type || "").toLowerCase();
        if (ct.indexOf("int") !== -1 || ct.indexOf("float") !== -1 || ct.indexOf("double") !== -1 || ct.indexOf("decimal") !== -1 || ct.indexOf("numeric") !== -1 || ct.indexOf("bigint") !== -1) return cn;
      }
      return cols.length ? (cols[0].column_name || cols[0].name || cols[0]) : null;
    }
    // Slicer/search should target categorical
    for (var i = 0; i < cols.length; i++) {
      var cn = cols[i].column_name || cols[i].name || cols[i];
      var ct = (cols[i].column_type || cols[i].type || "").toLowerCase();
      if (ct === "varchar" || ct === "string" || ct.indexOf("char") !== -1 ||
          cn.toLowerCase().indexOf("category") !== -1 || cn.toLowerCase().indexOf("region") !== -1 ||
          cn.toLowerCase().indexOf("country") !== -1 || cn.toLowerCase().indexOf("city") !== -1) {
        return cn;
      }
    }
    return cols.length ? (cols[0].column_name || cols[0].name || cols[0]) : null;
  }

  /* Resolve a relative date preset label to an inclusive [start, end] pair
     of YYYY-MM-DD strings. Shared by the Date Range presets and the relative
     date widget. */
  function _resolveRange(preset) {
    var p = String(preset || "").trim().toLowerCase();
    var now = new Date();
    var fmt = function (d) {
      var mm = String(d.getMonth() + 1).padStart(2, "0");
      var dd = String(d.getDate()).padStart(2, "0");
      return d.getFullYear() + "-" + mm + "-" + dd;
    };
    var day = 864e5;
    var maybeEnd = function (start) {
      var e = new Date(now.getTime());
      if (e < start) e = new Date(start.getTime());
      return e;
    };
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var map = {
      today: [today, today],
      yesterday: [new Date(today.getTime() - day), new Date(today.getTime() - day)],
      "last 7 days": [new Date(today.getTime() - 6 * day), today],
      "last 14 days": [new Date(today.getTime() - 13 * day), today],
      "last 30 days": [new Date(today.getTime() - 29 * day), today],
      "last 60 days": [new Date(today.getTime() - 59 * day), today],
      "last 90 days": [new Date(today.getTime() - 89 * day), today],
      "last 180 days": [new Date(today.getTime() - 179 * day), today],
      "last 365 days": [new Date(today.getTime() - 364 * day), today],
      "this week": [new Date(today.getTime() - ((today.getDay() + 6) % 7) * day), today],
      "last week": (function () {
        var dow = (today.getDay() + 6) % 7;
        var monday = new Date(today.getTime() - dow * day);
        return [new Date(monday.getTime() - 7 * day), new Date(monday.getTime() - day)];
      })(),
      "this month": [new Date(now.getFullYear(), now.getMonth(), 1), today],
      "last month": [new Date(now.getFullYear(), now.getMonth() - 1, 1), new Date(now.getFullYear(), now.getMonth(), 0)],
      "this quarter": [new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1), today],
      "last quarter": (function () {
        var q = Math.floor(now.getMonth() / 3);
        var qy = now.getFullYear() + (q === 0 ? -1 : 0);
        var qs = ((q * 3) + 9) % 12;
        var qStart = new Date(qy, qs, 1);
        return [qStart, new Date(qStart.getFullYear(), qStart.getMonth() + 3, 0)];
      })(),
      "this year": [new Date(now.getFullYear(), 0, 1), today],
      "last year": [new Date(now.getFullYear() - 1, 0, 1), new Date(now.getFullYear(), 0, 0)],
      ytd: [new Date(now.getFullYear(), 0, 1), today],
      mtd: [new Date(now.getFullYear(), now.getMonth(), 1), today],
      qtd: [new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1), today],
      "last 12 months": [new Date(today.getFullYear(), today.getMonth() - 11, 1), today]
    };
    var pair = map[p];
    if (!pair) return null;
    return [fmt(pair[0]), fmt(maybeEnd(pair[1]))];
  }


  function _splitSqlTail(sql) {
    var s = String(sql || "");
    var tail = "";
    for (var i = 0; i < 5; i++) {
      var m = s.match(/\s+(LIMIT|OFFSET)\s+[\d\s,]+$/i);
      if (!m) break;
      tail = m[0] + tail;
      s = s.slice(0, m.index);
    }
    var m2 = s.match(/\s+ORDER\s+BY[\s\S]*$/i);
    if (m2) {
      tail = m2[0] + tail;
      s = s.slice(0, m2.index);
    }
    s = s.replace(/;\s*$/, "");
    return { core: s, tail: tail.trim() };
  }

  /* Inject active filters as a WHERE clause into a widget's SQL.
     Measure-backed KPIs get the filters placed INSIDE the time-intel scalar
     subqueries (their outer SELECT returns a single aggregate row); plain SQL
     gets the filters injected into the outermost query (before GROUP BY /
     HAVING, or appended after the whole WHERE block) so the raw filter columns
     remain reachable — a wrapped "SELECT * FROM (<base>) __xf WHERE ..." cannot
     see columns that a grouped base did not also select. */
  /* Resolve the REAL source column behind a chart's label axis for
     cross-filtering. The widget SQL is typically `SELECT "product" AS label,
     SUM("revenue") AS value ...` — the first SELECT expression's source
     column (before AS) is the filterable dimension. Falls back to matching
     the result column name against the table's real columns. */
  function _resolveDimensionColumn(widget, resultCols) {
    var c = widget.config || {};
    var sql = (widget._origSql || c.sql || "").trim();
    if (!sql) return null;
    /* Grab the first SELECT ... FROM segment */
    var m = /select\s+(.+?)\s+from\s+/i.exec(sql.replace(/\s+/g, " "));
    if (!m) return null;
    var firstExpr = m[1].split(",")[0].trim();
    /* Strip an aggregate wrapper if present: MAX(x), MIN(x), ANY(x) */
    var agg = /^\s*(max|min|any|first)\s*\(\s*"?([^")]+?)"?\s*\)\s*$/i.exec(firstExpr);
    if (agg) firstExpr = '"' + agg[2] + '"';
    /* `expr AS alias` → take the expr side; bare quoted col → itself */
    var asM = /^(.+?)\s+as\s+[\s\S]+$/i.exec(firstExpr);
    if (asM) firstExpr = asM[1].trim();
    var q = /^"([^"]+)"$/.exec(firstExpr);
    var col = q ? q[1] : firstExpr.replace(/\s+/g, "");
    if (!col || /^\d+$/.test(col)) return null;
    /* Validate against the table's real columns (case-insensitive) so the
       filter's WHERE clause actually matches. */
    var tName = c.table || _dash.table;
    var tCols = [];
    try { tCols = (App.state.tables[tName] || {}).columns || []; } catch (e) {}
    for (var i = 0; i < tCols.length; i++) {
      var cn = tCols[i].column_name || tCols[i].name || tCols[i];
      if (cn === col || String(cn).toLowerCase() === String(col).toLowerCase()) return cn;
    }
    /* No table metadata (or cross-table SQL): accept the parsed name only if
       it isn't the generic alias. */
    if (col !== "label" && col !== "value" && resultCols && resultCols.indexOf(col) !== -1) return col;
    return null;
  }

  function _applyFilters(widget, extraFilters) {
    var c = widget.config || {};
    var cols = [];
    try { cols = (App.state.tables[c.table || _dash.table] || {}).columns || []; } catch (e) {}
    var where = _buildWhereClause(cols, extraFilters);
    /* Multi-metric card: rebuild the per-measure UNION with the filters injected
       inside each time-intel subquery (the outer rows carry no filter columns). */
    if (c.measures && c.measures.length && window.BITimeIntel) {
      try {
        var mc = _metricCardSql(c.measures, c.table || _dash.table, c.dateCol, where || "");
        if (mc && mc.sql) return mc.sql;
      } catch (e) { /* fall through to the plain-SQL wrap */ }
    }
    if (c.measureExpr && window.BITimeIntel) {
      try {
        return window.BITimeIntel.build(c.measureExpr, c.table || _dash.table,
          c.dateCol, c.timeIntel || "none", where ? { where: where } : null).sql;
      } catch (e) { /* fall through to the plain-SQL wrap */ }
    }
    var baseSql = widget._origSql || c.sql;
    if (!baseSql || (Object.keys(_filters).length === 0 && !(extraFilters && Object.keys(extraFilters).length))) return baseSql;
    if (c.pivot && c.pivot.rowCol && c.pivot.valueCol) return baseSql;
    if (!where) return baseSql;
    var parts = _splitSqlTail(baseSql);
    if (!parts.core || !parts.core.trim()) return baseSql;
    try {
      return _injectWhereInto(baseSql, where);
    } catch (e) { return baseSql; }
  }

  /* Turn a map of {field: value} into a WHERE fragment. Supports:
       scalar equality · arrays → IN · {start,end} → date range ·
       {min,max} → numeric range · {search, cols} → OR LIKE across columns ·
       {not: value|array} → negation. Only fields that resolve to a known
       column in `cols` are applied. */
  function _pushSearch(parts, colName, val, negate) {
    var q = String(val.search == null ? "" : val.search).toLowerCase();
    if (!q) return;
    var searchCols = (Array.isArray(val.cols) && val.cols.length) ? val.cols : (colName ? [colName] : []);
    var sub = [];
    for (var i = 0; i < searchCols.length; i++) {
      var scn = String(searchCols[i]).replace(/^"|"$/g, "");
      if (!scn) continue;
      sub.push('LOWER("' + scn + '") LIKE \'%' + q.replace(/'/g, "''") + '%\'');
    }
    if (!sub.length) return;
    parts.push((negate ? "NOT (" : "(") + sub.join(" OR ") + ")");
  }
  function _mapsToWhere(filterMap, cols, exclude) {
    var parts = [];
    var ex = exclude || [];
    Object.keys(filterMap || {}).forEach(function (field) {
      if (ex.indexOf(field) !== -1) return;
      var val = filterMap[field];
      if (val === null || val === undefined || val === "") return;
      var colName = null;
      for (var i = 0; i < cols.length; i++) {
        var cn = cols[i].column_name || cols[i].name || cols[i];
        if (cn === field || cn.toLowerCase() === field.toLowerCase()) { colName = cn; break; }
      }
      /* Search objects are applied even when the key matches no column (the
         search carries its own column list) — but only to columns that exist
         in this widget's table. */
      if (typeof val === "object" && val !== null && !Array.isArray(val) && ("search" in val)) {
        var searchTargets = [];
        (val.cols || []).forEach(function (sc) {
          for (var scI = 0; scI < cols.length; scI++) {
            var scn2 = cols[scI].column_name || cols[scI].name || cols[scI];
            if (String(scn2) === String(sc) || String(scn2).toLowerCase() === String(sc).toLowerCase()) { searchTargets.push(sc); break; }
          }
        });
        if (!searchTargets.length && colName) searchTargets.push(colName);
        if (searchTargets.length) _pushSearch(parts, null, { search: val.search, cols: searchTargets }, false);
        return;
      }
      if (!colName) return;
      var negate = false;
      if (typeof val === "object" && val !== null && !Array.isArray(val) && ("not" in val)) {
        negate = true;
        var inner = val.not;
        if (inner === null || inner === undefined || inner === "" || (Array.isArray(inner) && inner.length === 0)) return;
        val = inner;
      }
      if (typeof val === "object" && val !== null && !Array.isArray(val) && ("start" in val || "end" in val)) {
        if (val.start) parts.push('"' + colName + '" ' + (negate ? "< " : ">= ") + 'TIMESTAMP \'' + String(val.start).replace(/'/g, "''") + '\'');
        if (val.end) parts.push('"' + colName + '" ' + (negate ? "> " : "<= ") + 'TIMESTAMP \'' + String(val.end).replace(/'/g, "''") + ' 23:59:59\'');
      } else if (typeof val === "object" && val !== null && !Array.isArray(val) && (typeof val.min === "number" || typeof val.max === "number")) {
        if (val.min !== null && val.min !== undefined && val.min !== "") parts.push('"' + colName + '" ' + (negate ? "< " : ">= ") + String(val.min));
        if (val.max !== null && val.max !== undefined && val.max !== "") parts.push('"' + colName + '" ' + (negate ? "> " : "<= ") + String(val.max));
      } else if (Array.isArray(val)) {
        var escaped = val.map(function(v) { return "'" + String(v).replace(/'/g, "''") + "'"; });
        parts.push('"' + colName + '" ' + (negate ? "NOT IN (" : "IN (") + escaped.join(",") + ")");
      } else {
        parts.push('"' + colName + '" ' + (negate ? "<> " : "= ") + '\'' + String(val).replace(/'/g, "''") + '\'');
      }
    });
    return parts.join(" AND ");
  }
  /* WHERE for the currently active filters, optionally excluding some fields
     (used by cascading option lists so a slicer doesn't filter itself). */
  function _whereFromFilters(cols, exclude) {
    return _mapsToWhere(_filters, cols, exclude || []);
  }

  function _buildWhereClause(cols, extra) {
    var merged = {};
    Object.keys(_filters).forEach(function (f) { merged[f] = _filters[f]; });
    if (extra) {
      Object.keys(extra).forEach(function (f) {
        var v = extra[f];
        if (v !== null && v !== undefined && v !== "") merged[f] = v;
      });
    }
    return _mapsToWhere(merged, cols, []);
  }

  /* Insert "WHERE <where>" into a plain SQL, inside the outermost query so the
     filter columns (which live in the table/from-clause, not necessarily the
     select list) remain reachable. Wrapping a grouped aggregate in an outer
     SELECT fails — the subquery exposes no raw filter column — so the filter
     has to go inside the aggregate's own query. Finds only TOP-LEVEL (outside
     any parentheses) WHERE / GROUP BY / HAVING so scalar subqueries such as
     "(SELECT SUM(x) FROM t WHERE 1=1)" are never treated as anchors. */
  function _clausePositions(sql) {
    var positions = { where: -1, groupBy: -1, having: -1 };
    var depth = 0, i = 0, n = String(sql || "").length;
    var core = String(sql || "");
    while (i < n) {
      var ch = core[i];
      if (ch === "'") {
        i++;
        while (i < n) { if (core[i] === "\\") i++; i++; if (i < n && core[i] === "'") { i++; break; } }
        continue;
      }
      if (ch === "(") { depth++; i++; continue; }
      if (ch === ")") { depth--; i++; continue; }
      if (depth === 0 && /^[a-zA-Z]/.test(ch)) {
        var m = core.slice(i, i + 12).match(/^(where|group|having)\b/i);
        if (m) {
          var k = m[1].toLowerCase();
          if (k === "where" && positions.where === -1) positions.where = i;
          else if (k === "group") {
            if (positions.groupBy === -1 && /^\s+by\b/i.test(core.slice(i + 5))) positions.groupBy = i;
          }
          else if (k === "having" && positions.having === -1) positions.having = i;
        }
      }
      i++;
    }
    return positions;
  }
  function _injectWhereInto(sql, where) {
    if (!where) return sql;
    var s = String(sql || "");
    var parts = _splitSqlTail(s);
    var core = parts.core;
    var cls = _clausePositions(core);
    var tail = parts.tail ? " " + parts.tail : "";
    if (cls.where !== -1) {
      var wEnd = core.length;
      [cls.groupBy, cls.having].forEach(function (x) { if (x !== -1 && x > cls.where && x < wEnd) wEnd = x; });
      return core.slice(0, wEnd) + " AND " + where + core.slice(wEnd) + tail;
    }
    var at = cls.having !== -1 ? cls.having : (cls.groupBy !== -1 ? cls.groupBy : core.length);
    return core.slice(0, at) + " WHERE " + where + core.slice(at) + tail;
  }

  /* ── Main page object ── */
  const BlankDash = {
    async init() {
      _dash = {
        id: null, name: "Untitled Dashboard", table: "",
        pages: [{ id: "page_1", name: "Page 1", widgets: [] }],
        currentPage: 0, mode: "edit",
        canvasMinW: 1900, canvasMinH: 1080, fixedCanvas: true
      };
      _grid = null; _seq = 0; _filters = {}; _drillStack = [];
      // Server deep-links: #blankdash/<serverId> opens an existing saved
      // dashboard for editing; #blankdash/shared/<token> opens a shared,
      // read-only (present) view. Both are parsed before the client-side
      // sessionStorage context so server state wins.
      var m = /^#blankdash\/shared\/([A-Za-z0-9_-]+)/.exec(window.location.hash || "");
      if (m) { await this._loadSharedView(m[1]); }
      var m2 = /^#blankdash\/([A-Za-z0-9\-_]+)/.exec(window.location.hash || "");
      if (m2 && m2[1] !== "shared") { await this._loadSavedServer(m2[1]); }
      var ctxRaw = sessionStorage.getItem("blankdash_context");
      if (ctxRaw) {
        try {
          var ctx = JSON.parse(ctxRaw);
          if (ctx.table) _dash.table = ctx.table;
          if (ctx.name) _dash.name = ctx.name;
          if (ctx.canvasW) _dash.canvasMinW = Math.max(320, parseInt(ctx.canvasW, 10) || _CANVAS_MIN_W);
          if (ctx.canvasH) _dash.canvasMinH = Math.max(240, parseInt(ctx.canvasH, 10) || _CANVAS_MIN_H);
          if (ctx.fixedCanvas !== undefined) _dash.fixedCanvas = !!ctx.fixedCanvas;
          if (ctx.grid) _dash.grid = Math.max(4, Math.min(96, parseInt(ctx.grid, 10) || 12));
          if (ctx.widgets) _dash.pages[0].widgets = ctx.widgets;
          if (ctx.pages) _dash.pages = ctx.pages;
          if (ctx.id) _dash.id = ctx.id;
          // Theme chosen in the Dashboard Studio design wizard (e.g. "dark",
          // "midnight") is carried through so the canvas chrome matches.
          if (ctx.theme) _dash.theme = ctx.theme;
          if (ctx.themeMode) _dash.themeMode = _normThemeMode(ctx.themeMode);
          if (ctx.sharePublic) _dash.sharePublic = true;
          sessionStorage.removeItem("blankdash_context");
        } catch (e) {}
      }
      if (sessionStorage.getItem("blankdash_present")) {
        sessionStorage.removeItem("blankdash_present");
        if (_dash.id) { _dash.mode = "present"; this.render(); }
        if (App.info) App.info("Presentation mode — ←/→ page, Home/End first/last, 1–9 jump, F fullscreen, Esc exit");
      }
      var _san2 = _sanitizeDashboard(_dash);
      if (_san2) {
        App.info(_san2 > 0
          ? _san2 + " widget" + (_san2 === 1 ? "" : "s") + " were not recognized and were removed on load"
          : "Some layout values were repaired on load");
      }
      var pm = /^#blankdash\/[A-Za-z0-9\-_]+\/p(\d+)/.exec(window.location.hash || "");
      if (pm) { var pi = parseInt(pm[1], 10) - 1; if (pi >= 0 && pi < _dash.pages.length) _dash.currentPage = pi; }
      /* C9 — kiosk mode: ?autoplay=1&interval=<s> auto-starts the carousel in
         present mode on load. Params may live in location.search or inside the
         hash (SPA routes keep them after the #). */
      try {
        var qs = window.location.search || "";
        var hq = (window.location.hash || "").split("?")[1] || "";
        var qp = new URLSearchParams(qs + (qs && hq ? "&" : "") + hq);
        if (qp.get("autoplay") === "1") {
          var iv = parseFloat(qp.get("interval"));
          if (iv > 0 && iv <= 60) _dash.carouselSec = iv;
          _dash.mode = "present";
          var self0 = this;
          setTimeout(function () {
            if (_dash.mode === "present") {
              self0._carouselStart();
              self0._presentStart = Date.now();
              self0._startPresentTimer();
            }
          }, 400);
        }
      } catch (e) {}
      _restorePageFilters(_dash.currentPage);
      _registerCustomRenderers();
      _registerExtraRenderers();
      this._restartAutoRefresh();
    },

    async destroy() {
      _grid = null;
      this._stopAutoRefresh();
      clearTimeout(_watchSweepT1);
      clearTimeout(_watchSweepT2);
      _watchSweepT1 = null; _watchSweepT2 = null;
    },

    /* ── Server persistence helpers ── */
    /* Build the full _dash config to send to the /dashboards backend. The whole
       object is stored server-side (via the layout column) so it round-trips. */
    _buildServerPayload: function () {
      return {
        name: _dash.name || "Untitled Dashboard",
        owner_id: localStorage.getItem("username") || "anonymous",
        widgetCount: _currentWidgets().length,
        is_shared: !!_dash.sharePublic,
        config: _dashConfigSnapshot()
      };
    },

    /* Convert a backend dashboard record back into _dash (lossless). */
    _dashFromServer: function (dash) {
      var cfg = null;
      try {
        var l = dash.layout;
        if (l && typeof l === "object" && l._blankdash) cfg = l._blankdash;
        else if (l && typeof l === "object") cfg = l;
      } catch (e) {}
      if (!cfg && Array.isArray(dash.widgets)) {
        cfg = { pages: [{ id: "page_1", name: "Page 1", widgets: dash.widgets }], name: dash.name };
      }
      if (!cfg) return;
      var pages = cfg.pages && Array.isArray(cfg.pages) ? cfg.pages
        : [{ id: "page_1", name: "Page 1", widgets: (cfg.widgets || []) }];
      var nd = {
        id: dash.id || (cfg.id ? String(cfg.id).replace(/^blankdash_/, "") : null),
        name: cfg.name || dash.name || "Untitled Dashboard",
        table: cfg.table || (dash.owner_id === "anonymous" ? "" : (cfg.table || "")) || "",
        mode: "edit",
        refreshSec: cfg.refreshSec || 0,
        theme: cfg.theme || "auto", themeMode: _normThemeMode(cfg.themeMode),
        shineGradient: !!cfg.shineGradient, shineColors: cfg.shineColors,
        sharePublic: !!(cfg.sharePublic !== undefined ? cfg.sharePublic : dash.is_shared),
        snap: cfg.snap, minW: cfg.minW, minH: cfg.minH,
        maxW: cfg.maxW, maxH: cfg.maxH,
        canvasMinW: cfg.canvasMinW, canvasMinH: cfg.canvasMinH,
        fixedCanvas: !!cfg.fixedCanvas,
        grid: cfg.grid,
        gridMode: (cfg.gridMode === "cols" || cfg.gridMode === "off") ? cfg.gridMode : "dots",
        pages: pages, currentPage: cfg.currentPage || 0,
        groups: (cfg.groups && typeof cfg.groups === "object") ? cfg.groups : {},
        stylePresets: (cfg.stylePresets && typeof cfg.stylePresets === "object") ? cfg.stylePresets : {},
        carouselSec: cfg.carouselSec || 5,
        _bookmarks: Array.isArray(cfg._bookmarks) ? cfg._bookmarks : null
      };
      return nd;
    },

    _applyDash: function (nd) {
      _dash = nd;
      var san = _sanitizeDashboard(_dash);
      if (san) {
        App.info(san > 0
          ? san + " widget" + (san === 1 ? "" : "s") + " were not recognized and were removed on load"
          : "Repaired an issue in this dashboard on load");
      }
      this._restartAutoRefresh();
      this._clearDirty();
      this.render();
      // After render(), the settings/canvas are synced from _dash fields.
      this._syncCanvasSettings();
    },

    _loadSavedServer: async function (id) {
      var self = this;
      try {
        var res = await API.getDashboard(id);
        var dash = res && res.dashboard ? res.dashboard : res;
        if (!dash) { App.warning("Dashboard not found on server"); return; }
        var nd = this._dashFromServer(dash);
        if (nd) this._applyDash(nd);
        this._checkRecoverySnapshot(dash.updated_at ? new Date(dash.updated_at).getTime() : 0);
      } catch (e) {
        console.warn("Failed to load dashboard from server:", e);
        App.warning("Could not load that dashboard: " + (e && e.message ? e.message : "server error"));
      }
    },

    /* Open a shared dashboard in read-only (present) mode via its token. */
    _loadSharedView: async function (token) {
      var self = this;
      try {
        var res = await API.getSharedDashboard(token);
        var dash = res && res.dashboard ? res.dashboard : res;
        if (!dash) { App.warning("Shared dashboard not found or has expired"); return; }
        var nd = this._dashFromServer(dash);
        if (nd) { nd.mode = "present"; this._applyDash(nd); }
      } catch (e) {
        console.warn("Failed to open shared dashboard:", e);
        App.warning("Could not open shared dashboard: " + (e && e.message ? e.message : "server error"));
      }
    },

    async render() {
      var pc = document.getElementById("page-content");
      if (!pc) return;
      var present = _dash.mode === "present";
      var wCount = _currentWidgets().length;
      pc.innerHTML =
        '<div class="animate-fade-in bd-page bd-immersive' + (present ? ' bd-present' : '') + '">' +
          _renderToolbar() +
          '<div class="bd-layout' + (_leftRailCollapsed ? ' bd-rail-collapsed' : '') + '">' +
            (present ? '' : _renderPalette()) +
            '<div class="bd-canvas-wrap' + (present ? ' bd-canvas-wrap-present' : '') + (_dash.fixedCanvas ? ' bd-fixed-canvas' : '') + '">' +
              (present ? '' : _renderFilterBar()) +
              (present ? '<div class="bd-present-stage" id="bd-present-stage"><div class="bd-canvas" id="bd-canvas"></div></div>' : '<div class="bd-canvas" id="bd-canvas"></div>') +
              (present ? '' : _renderStatusBar()) +
              (present ? '' : _renderMinimap()) +
            '</div>' +
            '<button class="bd-palette-reopen" onclick="BlankDash._togglePalette()" title="Show widgets"><i class="fas fa-angles-left"></i></button>' +
            '<button class="bd-lr-reopen" onclick="BlankDash._toggleLeftRail()" title="Show layers"><i class="fas fa-angles-right"></i></button>' +
            (present ? '' : _renderLeftRail()) +
          '</div>' +
          _renderPageTabs() +
          (present ? _renderPresentNav() : '') +
        '</div>';
      if (!present) {
        this._loadTables();
        this._setupDragDrop();
        this._loadMeasures().then(function () {
          var lr = document.querySelector(".bd-leftrail");
          if (!lr || _dash.mode === "present") return;
          var tmp = document.createElement("div");
          tmp.innerHTML = "<div>" + _renderLeftRail() + "</div>";
          lr.replaceWith(tmp.firstElementChild);
        }).catch(function () {});
      }
      this._syncCanvasSettings();
      this._syncDashboardTheme();
      this._initGrid();
      var self = this;
      if (present) {
        document.querySelectorAll(".bd-page-tab").forEach(function (tab) {
          tab.addEventListener("click", function (e) {
            if (e.target.classList.contains("bd-page-tab-close") || e.target.classList.contains("bd-page-tab-add")) return;
          });
        });
        document.addEventListener("keydown", self._onEditKey, true);
        document.addEventListener("keydown", self._onPresentKey, true);
        document.addEventListener("fullscreenchange", self._onFsChange, true);
        self._updatePresentMeta();
        self._bindPresentIdle();
        self._fitPresentZoom();
        self._syncMobileFlow();
        self._renderAnnToolbar();
        self._renderAnnLayer();
        if (!window.__bdPresentFitBound) {
          window.__bdPresentFitBound = true;
          window.addEventListener("resize", function () {
            if (_dash.mode !== "present") return;
            BlankDash._syncMobileFlow();
            BlankDash._fitPresentZoom();
          });
        }
      } else {
        document.removeEventListener("keydown", self._onPresentKey, true);
        document.removeEventListener("fullscreenchange", self._onFsChange, true);
        document.addEventListener("keydown", self._onEditKey, true);
        if (!window.__bdUnloadBound) {
          window.__bdUnloadBound = true;
          window.addEventListener("beforeunload", self._onBeforeUnload);
        }
        this._applyZoom(_zoom);
        this._attachRulerScroll();
        this._renderRulers();
        this._bindStatusPointer();
        this._updateStatusBar();
        _bdMinimapOn = localStorage.getItem("bd_minimap_on") === "1";
        this._bindMinimap();
        _minimapRefresh();
        _minimapPin();
        /* Auto-fit populated dashboards so nothing starts clipped off-screen */
        if (wCount > 0 && !window.__bdAutoFitInit) {
          window.__bdAutoFitInit = true;
          window.__bdAutoFitLive = true;
          this._maybeAutoFit();
        }
        var self2 = this;
        var canWrap = document.querySelector(".bd-canvas-wrap");
        if (canWrap && !canWrap.__bdOverflowBound) {
          canWrap.__bdOverflowBound = true;
          canWrap.addEventListener("scroll", function () {
            self2._updateOverflowCues();
            _minimapPin();
            _minimapRefreshThrottled();
          });
        }
this._updateOverflowCues();
        this._syncSidePanels();
        if (!window.__bdAutoFitBound) {
          window.__bdAutoFitBound = true;
          var debounceTimer = null;
          window.addEventListener("resize", function () {
            if (debounceTimer) clearTimeout(debounceTimer);
            debounceTimer = setTimeout(function () { BlankDash._maybeAutoFit(); }, 220);
          });
        }
        _lastSnap = { pageId: _dash.pages[_dash.currentPage].id, widgets: JSON.parse(JSON.stringify(_currentWidgets())) };
        var gbtn = document.getElementById("bd-grid-toggle");
        if (gbtn) gbtn.classList.toggle("active", !_showGrid);
        var canvas = document.getElementById("bd-canvas");
        if (canvas) {
          canvas.classList.toggle("fff-grid-off", !_showGrid || _dash.gridMode === "off");
          canvas.classList.toggle("bd-grid-cols", _showGrid && _dash.gridMode === "cols");
        }
      }
    },

    _filterPalette: function (query) {
      var q = (query || "").toLowerCase();
      var catSel = document.getElementById("bd-palette-cat");
      if (catSel && catSel.value) catSel.value = "";
      var items = document.querySelectorAll("#bd-palette-items .bd-palette-item");
      var groups = document.querySelectorAll("#bd-palette-items .bd-palette-group");
      if (!q) {
        items.forEach(function (el) { el.style.display = ""; });
        groups.forEach(function (g) { g.style.display = ""; });
        return;
      }
      var counts = {};
      items.forEach(function (el) {
        var label = (el.getAttribute("data-label") || el.getAttribute("data-type") || "").toLowerCase();
        var show = label.indexOf(q) !== -1;
        el.style.display = show ? "" : "none";
        if (show) {
          var g = el.closest(".bd-palette-group");
          if (g && g.dataset.cat) counts[g.dataset.cat] = (counts[g.dataset.cat] || 0) + 1;
        }
      });
      groups.forEach(function (g) { g.style.display = counts[g.dataset.cat] ? "" : "none"; });
      groups.forEach(function (g) { if (counts[g.dataset.cat]) g.classList.add("expanded"); });
    },

    _filterPaletteCat: function (val) {
      var search = document.getElementById("bd-palette-search");
      if (search) search.value = "";
      var groups = document.querySelectorAll("#bd-palette-items .bd-palette-group[data-cat]");
      groups.forEach(function (g) {
        if (!val) { g.style.display = ""; return; }
        var on = g.dataset.cat === val;
        g.style.display = on ? "" : "none";
        if (on) g.classList.add("expanded");
      });
    },

    _togglePalette: function () {
      var layout = document.querySelector(".bd-layout");
      var palette = document.getElementById("bd-palette");
      if (!layout || !palette) return;
      var hide = !palette.classList.contains("bd-collapsed");
      palette.classList.toggle("bd-collapsed", hide);
      layout.classList.toggle("bd-palette-collapsed", hide);
      var btn = palette.querySelector(".bd-palette-toggle");
      if (btn) btn.innerHTML = hide ? '<i class="fas fa-angles-left"></i>' : '<i class="fas fa-angles-right"></i>';
      var reopen = document.querySelector(".bd-palette-reopen");
      if (reopen) reopen.style.display = hide ? "" : "none";
    },

    _toggleLeftRail: function () {
      var layout = document.querySelector(".bd-layout");
      var rail = document.querySelector(".bd-leftrail");
      if (!layout || !rail) return;
      var hide = !_leftRailCollapsed;
      _leftRailCollapsed = hide;
      rail.classList.toggle("bd-collapsed", hide);
      layout.classList.toggle("bd-rail-collapsed", hide);
      var btn = rail.querySelector(".bd-lr-hide");
      if (btn) btn.innerHTML = hide ? '<i class="fas fa-angles-right"></i>' : '<i class="fas fa-angles-left"></i>';
      var reopen = document.querySelector(".bd-lr-reopen");
      if (reopen) reopen.style.display = hide ? "" : "none";
    },

    _toggleSidePanels: function () {
      var layout = document.querySelector(".bd-layout");
      if (!layout) return;
      var bothHidden = layout.classList.contains("bd-palette-collapsed") &&
                       layout.classList.contains("bd-rail-collapsed");
      var hide = !bothHidden;
      if (layout.classList.contains("bd-palette-collapsed") !== hide) this._togglePalette();
      if (layout.classList.contains("bd-rail-collapsed") !== hide) this._toggleLeftRail();
      this._syncSidePanels();
    },

    _syncSidePanels: function () {
      var btn = document.getElementById("bd-panels-toggle");
      if (!btn) return;
      var layout = document.querySelector(".bd-layout");
      var visible = !!(layout &&
        !(layout.classList.contains("bd-palette-collapsed") &&
          layout.classList.contains("bd-rail-collapsed")));
      btn.classList.toggle("active", visible);
    },

    async _loadTables() {
      var sel = document.getElementById("bd-table-select");
      if (!sel) return;
      try {
        var result = await API.tables();
        var tbls = result && result.tables ? result.tables : result;
        var names = [];
        if (Array.isArray(tbls)) names = tbls.map(function (t) { return t.table_name || t.name; }).filter(Boolean);
        else if (tbls && typeof tbls === "object") names = Object.keys(tbls);
        names.forEach(function (n) {
          var opt = document.createElement("option");
          opt.value = n; opt.textContent = _friendlyName(n);
          if (_dash.table === n) opt.selected = true;
          sel.appendChild(opt);
        });
} catch (e) {}
    },

    /* ── Fields pane: click to add, drag to bind, related-table joins ── */
    _focusTableSelect: function () {
      var sel = document.getElementById("bd-table-select");
      if (sel) { sel.focus(); sel.scrollIntoView({ block: "center", behavior: "smooth" }); }
    },
    _onFieldClick: function (chip) {
      if (!chip) return;
      var t = chip.getAttribute("data-table");
      var f = chip.getAttribute("data-field");
      var k = chip.getAttribute("data-kind") || "dim";
      if (!t || !f) return;
      this._addFieldWidget(t, f, k);
    },
    _toggleRelatedTable: function (t) {
      if (!t || !_dash.table) return;
      var i = _relatedTables.indexOf(t);
      if (i === -1) _relatedTables.push(t);
      else _relatedTables.splice(i, 1);
      this._renderLeftRailLive();
    },
    _addFieldWidget: function (table, field, kind) {
      if (!table) table = _dash.table;
      if (!table || !field) { App.warning("Select a data source first"); return; }
      var friendly = _humanizeCol(field);
      var qt = _qi(table), qc = _qi(field);
      var pos, widget;
      var dim = kind === "measure" ? _toPx(3, 2) : _toPx(6, 3);
      var z = _currentWidgets().length + 1;
      if (kind === "measure") {
        widget = {
          id: "w" + Date.now().toString(36) + "_" + (++_seq),
          type: "kpi", title: friendly,
          x: 0, y: 0, width: dim.width, height: dim.height,
          zIndex: z, locked: false, plain: false,
          config: {
            table: table,
            sql: "SELECT SUM(" + qc + ") AS value FROM " + qt,
            xCol: "", yCol: "value", label: friendly, agg: "SUM", measures: [field], filterField: field
          }
        };
      } else {
        widget = {
          id: "w" + Date.now().toString(36) + "_" + (++_seq),
          type: "chart", title: friendly,
          x: 0, y: 0, width: dim.width, height: dim.height,
          zIndex: z, locked: false, plain: false,
          config: {
            table: table,
            chartType: kind === "date" ? "line" : "bar",
            sql: "SELECT " + qc + " AS label, COUNT(*) AS value FROM " + qt + " GROUP BY " + qc + " ORDER BY value DESC LIMIT 12",
            xCol: field, yCol: "value", label: friendly, agg: "COUNT",
            filterField: field, measures: [], limit: 12
          }
        };
      }
      pos = this._nextFreePos(dim);
      widget.x = pos.x; widget.y = pos.y;
      var emptyEl = document.getElementById("bd-empty-state");
      if (emptyEl) emptyEl.remove();
      _currentWidgets().push(widget);
      this._initGrid();
      this._selectWidget(widget.id);
      this._refreshHistory();
      this._markDirty();
      App.success("Added " + friendly);
    },
    /* Bind a dropped field onto an existing widget. Cross-table fields are
       auto-joined (Phase 2b): a shared key is detected and the widget's SQL
       becomes a LEFT JOIN so both tables co-query. */
    _bindFieldToWidget: function (id, table, field, kind) {
      var w = _findWidget(id);
      if (!w) return;
      if (w.locked) { App.warning("Unlock the widget before binding fields"); return; }
      if (w.type === "text" || w.type === "image" || w.type === "page" || w.type === "button" || w.type === "custom" ||
        w.type === "shape" || w.type === "divider" ||
        w.type === "table" || _FILTER_TYPES.indexOf(w.type) !== -1) {
        App.warning("This widget does not use bindable data fields"); return;
      }
      var cfg = w.config || (w.config = {});
      var base = cfg.table || _dash.table || "";
      if (!base) { cfg.table = table; base = table; }
      var joins = cfg.joins || (cfg.joins = []);
      /* Cross-table → resolve a join key and track it on the widget. */
      if (table && table !== base) {
        var hasJoin = joins.some(function (j) { return j.table === table; });
        if (!hasJoin) {
          var key = _joinKey(base, table);
          if (!key) { App.error("No shared join key found between " + base + " and " + table); return; }
          joins.push({ table: table, key: key, type: "left" });
          if (_relatedTables.indexOf(table) === -1) _relatedTables.push(table);
        }
      }
      var isKpi = _KPIV_FAMILY.indexOf(w.type) !== -1;
      if (kind === "measure") {
        cfg.measures = cfg.measures || [];
        if (cfg.agg === "COUNT" && !cfg.measures.length) cfg.agg = "SUM";
        if (cfg.measures.indexOf(field) === -1) cfg.measures.push(field);
        else App.info(field + " is already on the chart");
        cfg.yCol = "value";
      } else {
        cfg.xCol = field;
        cfg.filterField = field;
        cfg.agg = cfg.agg === "COUNT" && (!cfg.measures || !cfg.measures.length) ? "COUNT" : (cfg.agg || "SUM");
        if (kind === "date" && !cfg.chartType) cfg.chartType = "line";
        if (!cfg.measures || !cfg.measures.length) cfg.measures = [];
      }
      cfg.sql = _buildWidgetSql(w);
      this._onWidgetEdited(w.id);
      this._markDirty();
      App.success('Bound "' + field + '" to ' + (_TITLES[w.type] || w.type));
    },

    _initGrid: function () {
      var container = document.getElementById("bd-canvas");
      if (!container) return;
      /* Normalise any legacy grid widgets (col/row/cols/rows) to free-form pixels across every page */
      for (var p = 0; p < _dash.pages.length; p++) {
        if (_dash.pages[p] && _dash.pages[p].widgets) _migrateFreeForm(_dash.pages[p].widgets);
      }
      var present = _dash.mode === "present";
      container.innerHTML = "";
      var fixedCanvas = !!_dash.fixedCanvas;
      if (fixedCanvas) {
        container.style.width = _baseCanvasW() + "px";
        container.style.height = _baseCanvasH() + "px";
      }
      if (_currentWidgets().length === 0) {
        if (!present) container.insertAdjacentHTML("beforeend", _bdEmptyStateHtml(_dash.table || ""));
        return;
      }
      var self = this;

      /* Auto-grow the canvas to fit placed widgets (free-form "infinite" canvas) —
         skipped when the dashboard uses a fixed canvas size. */
      if (!fixedCanvas) {
        var maxRight = _baseCanvasW(), maxBottom = _baseCanvasH();
        _currentWidgets().forEach(function (w) {
          if (w.x + w.width > maxRight) maxRight = w.x + w.width;
          if (w.y + w.height > maxBottom) maxBottom = w.y + w.height;
        });
        container.style.width = (Math.max(maxRight + 80, _baseCanvasW())) + "px";
        container.style.height = (maxBottom + 80) + "px";
      }

      var frag = document.createDocumentFragment();
      /* sort by z-index so stacking reflects zOrder */
      var ordered = _currentWidgets().slice().sort(function (a, b) { return (a.zIndex || 0) - (b.zIndex || 0); });
      ordered.forEach(function (w) {
        frag.appendChild(self._buildWidgetEl(w, present));
      });
      container.appendChild(frag);
      /* P3.11 — calendar-hierarchy drill bars (charts with a date grouping level) */
      ordered.forEach(function (w) { self._renderCalendarDrill(w.id); });
      if (_dash.fixedCanvas && !present) self._updateClipBadge();

      /* Bind selection, toolbar, resize, drag (edit mode only) */
      if (!present) {
        this._bindSelection();
        this._bindTextEdit();
        this._bindWidgetChrome();
        this._bindResizeHandles();
        this._bindMove();
        this._bindCanvasClickDeselect();
        var cmenuself = this;
        container.addEventListener("contextmenu", function (e) {
          e.preventDefault();
          if (_dash.mode !== "edit") return;
          var widgetEl = e.target.closest(".ff-widget");
          if (widgetEl && !e.target.closest(".ff-widget-btn") && !e.target.closest(".ff-resize-handle")) {
            cmenuself._openWidgetMenuAt(widgetEl.dataset.id, e.clientX, e.clientY);
          } else {
            cmenuself._showCanvasMenu(e.clientX, e.clientY);
          }
        });
      }
      this._attachContentWatcher();
      _minimapRefresh();
      _minimapPin();
      /* Auto-size AI insight panels to their exact content (see method) */
      var fitSelf = this;
      this._autoFitInsightWidgets();
      setTimeout(function () { fitSelf._autoFitInsightWidgets(); }, 650);
    },

    /* Self-heal widget content: re-render anything drawn before the grid had
       settled or when the viewport / a tile is resized, so every visual
       matches its box (charts auto-fit, gauges re-scale, d3 renders redraw).
       Reschedules its early sweeps on every render (a one-time install leaves
       re-entered dashboards blank when the first async paint races a teardown)
       and re-renders any data cell that still shows nothing (heal pass). */
    _attachContentWatcher: function () {
      var _this = this, timer = null;
      var last = {};
      function sizeOf(cell) {
        var r = cell.getBoundingClientRect();
        return { w: r.width, h: r.height };
      }
      function isEmptyVisual(cell) {
        if (!cell) return true;
        if (cell.querySelector("canvas") || cell.querySelector("svg")) return false;
        var txt = (cell.textContent || "").trim();
        if (txt.length > 2) return false;
        return !cell.children.length;
      }
      function sweep() {
        if (!_dash) return;
        document.querySelectorAll(".ff-widget-content").forEach(function (cell) {
          if (!cell || !cell.closest) return;
          var widgetEl = cell.closest(".ff-widget");
          var id = widgetEl ? widgetEl.dataset.id : "";
          if (!id) return;
          var sz = sizeOf(cell);
          var prev = last[id];
          // Skip sub-pixel layout noise (fractional DPR/scrollbar rounding must
          // not re-render charts on every filter/zoom cycle).
          if (prev && Math.abs(sz.w - prev.w) < 1 && Math.abs(sz.h - prev.h) < 1 && !isEmptyVisual(cell)) return;
          last[id] = sz;
          if (sz.w === 0 && sz.h === 0 && !isEmptyVisual(cell)) return;
          var w = _findWidget(id);
          if (!w) return;
          if (["text", "custom", "image", "page", "shape", "divider"].indexOf(w.type) !== -1) return;
          if (_FILTER_TYPES.indexOf(w.type) !== -1) return;
          _this._renderWidgetContent(w, cell);
        });
      }
      function scheduleSweeps() {
        clearTimeout(_watchSweepT1);
        clearTimeout(_watchSweepT2);
        _watchSweepT1 = setTimeout(sweep, 400);
        _watchSweepT2 = setTimeout(sweep, 1600);
      }
      function debounce() { clearTimeout(timer); timer = setTimeout(function () { sweep(); _this._autoFitInsightWidgets(); }, 140); }
      if (!window.__bdResizeWatchBound) {
        window.__bdResizeWatchBound = true;
        window.addEventListener("resize", debounce);
      }
      scheduleSweeps();
    },

    /* Auto-size executive-insight panels (custom widgets flagged config.autoFit)
       to their exact content so nothing clips and the author never has to drag
       the bottom edge to make it fit. Grows or shrinks the panel and nudges any
       widgets below it by the same delta so the gap stays even; panels the
       author has manually resized (config.userSized) are left untouched. */
    _autoFitInsightWidgets: function () {
      var self = this;
      if (!_dash) return;
      _currentWidgets().forEach(function (w) {
        if (w.type !== "custom" || !w.config || !w.config.autoFit || w.config.userSized) return;
        var el = document.querySelector('.ff-widget[data-id="' + w.id + '"]');
        if (!el) return;
        var panel = el.querySelector(".bd-insight-panel");
        var body = el.querySelector(".bd-insight-body");
        if (!panel || !body) return;
        var cs = getComputedStyle(panel);
        var padV = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
        var borderV = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
        var head = panel.firstElementChild;
        var headH = head ? head.getBoundingClientRect().height : 0;
        if (head) {
          var hs = getComputedStyle(head);
          headH += (parseFloat(hs.marginTop) || 0) + (parseFloat(hs.marginBottom) || 0);
        }
        /* measure the body's true content height at full text width (no
           scrollbar distortion) by temporarily un-clipping it */
        var prevO = body.style.overflowY, prevH = body.style.height;
        body.style.overflowY = "visible";
        body.style.height = "auto";
        var contentH = body.scrollHeight;
        body.style.overflowY = prevO;
        body.style.height = prevH;
        var oldH = w.height;
        var oldBottom = w.y + oldH;
        var want = Math.max(96, Math.round(padV + borderV + headH + contentH + 16));
        /* converge: an 8px scrollbar can re-narrow the body and rewrap lines,
           so re-measure after applying and grow until nothing overflows */
        for (var i = 0; i < 4; i++) {
          w.height = want;
          el.style.height = want + "px";
          var over = body.scrollHeight - body.clientHeight;
          if (over <= 0) break;
          want += over + 2;
        }
if (Math.abs(want - oldH) < 4) { w.height = oldH; el.style.height = oldH + "px"; return; }
        var delta = want - oldH;
        /* Fixed canvas: growing the insight pushes everything below it down, and
           the canvas can't grow — so cap the growth to the room below, otherwise
           widgets get shoved off the bottom edge. The insight body scrolls
           internally, so a capped height is safe. */
        if (delta > 0 && _dash && _dash.fixedCanvas) {
          var baseH = _baseCanvasH();
          var lowestBottom = w.y + oldH;
          _currentWidgets().forEach(function (o) {
            if (o === w || o.y < oldBottom - 1) return;
            if (o.y + o.height > lowestBottom) lowestBottom = o.y + o.height;
          });
          var room = baseH - lowestBottom;
          if (delta > room) {
            want = oldH + Math.max(0, room);
            delta = want - oldH;
          }
        }
        /* shrinking: skip if another widget sits in the strip we'd reclaim */
        if (delta < 0 && _currentWidgets().some(function (o) {
          return o !== w && o.y < oldBottom - 1 && o.y + o.height > w.y + want + 1;
        })) return;
        w.height = want;
        el.style.height = want + "px";
        _currentWidgets().forEach(function (o) {
          if (o === w || o.y < oldBottom - 1) return;
          o.y += delta;
          var oe = document.querySelector('.ff-widget[data-id="' + o.id + '"]');
          if (oe) oe.style.top = o.y + "px";
        });
        self._growCanvas();
        _minimapRefresh();
      });
    },

    /* Build one free-form widget element (returns the outer .ff-widget node) */
    _buildWidgetEl: function (w, present) {
      var el = document.createElement("div");
      var isKpiType = _KPIV_FAMILY.indexOf(w.type) !== -1;
      el.className = "ff-widget" + (isKpiType ? " ff-widget--kpi" : " ff-widget--other") + (w.locked ? " ff-locked" : "") + (this._isSelected(w.id) && !present ? " ff-selected" : "");
      el.dataset.id = w.id;
      el.dataset.responsive = w.responsive || "auto";
      /* P3.9 — report tooltip page: hidden from the report canvas in present
         mode (renders only as the floating hover tooltip), badged in edit mode. */
      if (w.config && w.config.isTooltip) {
        el.classList.add("ff-tooltip-page");
        if (present) el.style.display = "none";
      }
      if (w.config && w.config.compact) el.classList.add("bd-compact");
      el.style.cssText =
        "position:absolute;left:" + w.x + "px;top:" + w.y + "px;" +
        "width:" + w.width + "px;height:" + w.height + "px;z-index:" + (w.zIndex || 0) + ";";
      if (w.bgColor) el.style.backgroundColor = w.bgColor;
      if (w.borderColor) el.style.borderColor = w.borderColor;
      if (w.plain || (w.type === "text" && !w.bgColor && !w.borderColor)) el.dataset.plain = "1";

      var body = document.createElement("div");
      body.className = "ff-widget-body";
      /* Titles visible by default (opt-out via props Show Title toggle) */
      if (w.showTitle !== false && w.title) {
        var t = document.createElement("div");
        t.className = "ff-widget-title-inner";
        t.dataset.id = w.id;
        t.textContent = w.title;
        body.appendChild(t);
      }
      var content = document.createElement("div");
      content.className = "ff-widget-content";
      content.dataset.id = w.id;
      body.appendChild(content);
      el.appendChild(body);

      /* P3.9 — report tooltip page: edit-mode badge so authors can find/manage the
         tooltip page widget on the canvas. */
      if (w.config && w.config.isTooltip && !present) {
        var rb = document.createElement("div");
        rb.className = "bd-tooltip-ribbon";
        rb.title = "This widget is the report's tooltip page (renders on chart hover)";
        rb.innerHTML = '<i class="fas fa-comment-dots"></i> Tooltip page';
        el.appendChild(rb);
      }

      /* per-widget actions menu (edit mode, unlocked) */
      if (!present && !w.locked) {
        var mb = document.createElement("button");
        mb.className = "ff-widget-menu-btn ff-widget-btn";
        mb.dataset.id = w.id;
        mb.title = "Widget actions";
        mb.innerHTML = '<i class="fas fa-ellipsis-vertical"></i>';
        el.appendChild(mb);
      }

      /* resize handles (edit mode, unlocked) */
      if (!present && !w.locked) {
        ["nw","n","ne","e","se","s","sw","w"].forEach(function (dir) {
          var h = document.createElement("div");
          h.className = "ff-resize-handle";
          h.dataset.resize = dir;
          h.dataset.id = w.id;
          el.appendChild(h);
        });
        var _this = this;
        var ch = document.createElement("div");
        ch.className = "bd-corner-handle";
        ch.title = "Corner: 0 square · 100 round (no circle)";
        var _curPct = (w.config && w.config.borderShapePct) || 0;
        ch.innerHTML = '<i class="fas fa-shapes"></i>' +
          '<input type="range" class="bd-corner-range" min="0" max="100" step="1" value="' + _curPct + '" aria-label="Corner shape" />' +
          '<span class="bd-corner-badge" data-v="' + _curPct + '">' + _curPct + '</span>';
        el.appendChild(ch);
        ch.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
        ch.addEventListener("input", function (ev) {
          ev.stopPropagation();
          var v = parseFloat(ev.target.value) || 0;
          if (!w.config) w.config = {};
          w.config.borderShapePct = v;
          var badge = ch.querySelector(".bd-corner-badge");
          if (badge) { badge.textContent = v; badge.dataset.v = v; }
          _applyCardStyle(w, el);
        });
        ch.addEventListener("change", function (ev) {
          ev.stopPropagation();
          var v = parseFloat(ev.target.value) || 0;
          if (!w.config) w.config = {};
          w.config.borderShapePct = v;
          var badge = ch.querySelector(".bd-corner-badge");
          if (badge) badge.textContent = v;
          _this._onWidgetEdited(w.id);
        });
      }
      if (w.locked && !present) {
        var lockBadge = document.createElement("div");
        lockBadge.className = "ff-lock-badge";
        lockBadge.innerHTML = '<i class="fas fa-lock"></i>';
        el.appendChild(lockBadge);
      }

      this._renderWidgetContent(w, content);
      _applyCardStyle(w, el);
      return el;
    },

    /* Render the actual visual/content into a widget's content container, based on type */
    _renderWidgetContent: function (w, cell) {
      if (!cell) return;
      if (_isMapType(w.type)) {
        // Dispose any previous Leaflet map on this cell before re-rendering,
        // otherwise re-renders leave an orphaned map whose markers error on
        // the now-detached DOM (`_leaflet_pos` on undefined).
        if (cell._bdMap) {
          var oldHost = cell.querySelector && (cell.querySelector(".bd-leaflet-host") || cell.querySelector(".bd-glmap-host"));
          if (oldHost && oldHost._bdResizeObserver) { try { oldHost._bdResizeObserver.disconnect(); } catch (e) {} }
          try { cell._bdMap.stop(); } catch (e) {}
          try { cell._bdMap.remove(); } catch (e) {}
          cell._bdMap = null;
        }
        cell._bdMapToken = (cell._bdMapToken || 0) + 1;
        var mc = w.config || {};
        if (!mc.sql) {
          cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-map-location-dot"></i><div>Configure a Geo field and a Measure</div></div>';
          return;
        }
        this._fetchMapAndRender(w, cell);
        return;
      }
      if (w.type === "image") {
        var ic = w.config || {};
        var src = (ic.imageUrl || ic.src) || "";
        if (src) {
          var ist = [];
          if (ic.imageFit) ist.push("object-fit:" + ic.imageFit);
          if (ic.imagePosition) ist.push("object-position:" + ic.imagePosition);
          if (ic.imageBorderRadius != null) ist.push("border-radius:" + ic.imageBorderRadius + "px");
          var img = '<img class="ff-image" src="' + _esc(src) + '" alt="' + _esc(ic.imageAlt || "") + '"' +
            (ist.length ? ' style="' + ist.join(";") + '"' : "") + ">";
          cell.innerHTML = ic.imageLink
            ? '<a href="' + _esc(ic.imageLink) + '"' + (ic.imageOpenNewTab ? ' target="_blank" rel="noopener"' : "") + ' style="display:block;height:100%;">' + img + '</a>'
            : img;
        } else {
          cell.innerHTML = '<div class="ff-placeholder"><i class="fas fa-image"></i><div>Set an image URL in settings</div></div>';
        }
        return;
      }
      if (w.type === "custom") {
        var html = (w.config && w.config.html) || "<div>Custom HTML</div>";
        var vspec = (w.config && w.config.spec) || null;
        if (vspec && this._renderVizSpec(cell, vspec)) return;
        try {
          var data = (w.config && (w.config.labels || w.config.data)) ? w.config : null;
          if (w.config && w.config.sandbox === true) {
            var host = cell.querySelector(":scope > .bd-sandbox-host");
            if (!host) {
              host = document.createElement("div");
              host.className = "bd-sandbox-host";
              host.style.cssText = "width:100%;height:100%;overflow:auto;";
              cell.innerHTML = "";
              cell.appendChild(host);
            }
            if (!host.shadowRoot) host.attachShadow({ mode: "open" });
            host.shadowRoot.innerHTML = html;
          } else {
            cell.innerHTML = html;
            this._execWidgetScripts(cell);
          }
        } catch (e) { cell.innerHTML = "<div>Custom HTML</div>"; }
        return;
      }
      if (_FILTER_TYPES.indexOf(w.type) !== -1) {
        if (w.type === "relativedate" || w.type === "globalsearch") {
          this._renderFilterWidget(w, cell);
        } else {
          this._fetchFilterData(w, cell);
        }
        return;
      }
      if (w.type === "text") {
        var tc = w.config || {};
        var ctext = (tc.content != null && String(tc.content) !== "") ? String(tc.content)
          : (tc.label) || (w.title || "");
        var fmt = tc.textFormat || "plain";
        var body;
        if (fmt === "html") body = ctext;
        else if (fmt === "markdown" && this._renderMarkdown) body = this._renderMarkdown(ctext);
        else body = _esc(ctext);
        var st = [];
        if (tc.valueFontSize != null) st.push("font-size:" + tc.valueFontSize + "px");
        if (tc.valueFontWeight) st.push("font-weight:" + tc.valueFontWeight);
        if (tc.valueFontStyle) st.push("font-style:" + tc.valueFontStyle);
        if (tc.valueFontFamily) st.push("font-family:" + tc.valueFontFamily);
        if (tc.valueColor) st.push("color:" + tc.valueColor);
        if (tc.valueLetterSpacing != null) st.push("letter-spacing:" + tc.valueLetterSpacing + "px");
        if (tc.valueLineHeight != null) st.push("line-height:" + tc.valueLineHeight);
        if (tc.valueAlign) st.push("text-align:" + tc.valueAlign);
        if (tc.valueTransform) st.push("text-transform:" + tc.valueTransform);
        if (tc.valueDecoration) st.push("text-decoration:" + tc.valueDecoration);
        cell.innerHTML = '<div class="ff-text" data-id="' + w.id + '" contenteditable="false" data-placeholder="Type text..."' +
          (st.length ? ' style="' + st.join(";") + '"' : "") + ">" + body + "</div>";
        return;
      }
      if (w.type === "page") {
        var tIdx = (w.config && typeof w.config.targetPage === "number") ? w.config.targetPage : _dash.currentPage;
        var pg = _dash.pages[tIdx];
        var pgName = pg ? _esc(pg.name || ("Page " + (tIdx + 1))) : _esc("Page " + (tIdx + 1));
        cell.innerHTML = '<div class="bd-page-link" onclick="event.stopPropagation();BlankDash._goToPage(\'' + w.id + '\')">' +
          '<div class="bd-page-link-icon"><i class="fas fa-arrow-right-to-bracket"></i></div>' +
          '<div class="bd-page-link-text"><span class="bd-page-link-label">' + _esc(w.title || "Page Link") + "</span>" +
          '<span class="bd-page-link-target"><i class="fas fa-file-lines"></i> ' + pgName + "</span></div>" +
          '<i class="fas fa-angle-right bd-page-link-arrow"></i>' +
        "</div>";
        return;
      }
      if (w.type === "button") {
        var bc = w.config || {};
        var act = bc.action || "reset";
        var val = bc.value != null ? String(bc.value) : "";
        var meta = {
          reset: { i: "fa-broom", t: "Clear All Filters", n: "Reset filters" },
          bookmark: { i: "fa-bookmark", t: val ? ("Apply \u201C" + val + "\u201D") : "Apply Bookmark", n: val || "Last saved view" },
          page: { i: "fa-arrow-right-to-bracket", t: "Go to page", n: val || "Next page" },
          drill: { i: "fa-crosshairs", t: "Drill through", n: val || "Next page" },
          refresh: { i: "fa-rotate", t: "Refresh Data", n: "Re-query all widgets" }
        };
        var m = meta[act] || meta.reset;
        cell.innerHTML = '<div class="bd-action-btn" data-widget-id="' + w.id + '" onclick="event.stopPropagation();BlankDash._actionBtn(\'' + w.id + '\')">' +
          '<i class="fas ' + m.i + '"></i>' +
          '<div class="bd-action-btn-txt"><span class="bd-action-btn-label">' + _esc(w.title || bc.label || m.t) + "</span>" +
          '<span class="bd-action-btn-sub">' + _esc(m.n) + "</span></div>" +
          '<i class="fas fa-angle-right bd-action-btn-arrow"></i>' +
        "</div>";
        return;
      }
      if ((w.config && w.config.sql) || w.config && w.config.labels) {
        this._fetchAndRender(w, cell);
        return;
      }
      if (DashboardRenderer) DashboardRenderer.render(w, cell);
      this._maybeAttachAIRecreate(cell, w);
    },

    _isSelected: function (id) {
      return _selectedIds.indexOf(id) !== -1;
    },

    /* Set the selection to exactly this single widget (primary = id) */
    _selectWidget: function (id, skipProps) {
      _selectedId = id;
      _selectedIds = [id];
      this._refreshSelection(skipProps);
    },

    /* Replace the whole selection; first id becomes the primary anchor */
    _setSelection: function (ids) {
      var valid = (ids || []).filter(function (x) { return _findWidget(x) != null; });
      _selectedIds = valid;
      _selectedId = valid[0] || null;
      this._refreshSelection();
    },

    /* Toggle one widget in/out of the selection (Shift-click). */
    _toggleSelectWidget: function (id) {
      var idx = _selectedIds.indexOf(id);
      if (idx !== -1) {
        _selectedIds.splice(idx, 1);
        if (_selectedId === id) _selectedId = _selectedIds[0] || null;
      } else {
        _selectedIds.push(id);
        if (_selectedId == null) _selectedId = id;
      }
      this._refreshSelection();
    },

    /* Make this widget the primary anchor of the current multi-selection. */
    _makePrimary: function (id) {
      var idx = _selectedIds.indexOf(id);
      if (idx > 0) { _selectedIds.splice(idx, 1); _selectedIds.unshift(id); }
      _selectedId = id;
      this._refreshSelection();
    },

    /* Sync DOM selection chrome + props dock + floating toolbar with the model */
    _refreshSelection: function (skipProps) {
      var self = this;
      document.querySelectorAll(".ff-widget").forEach(function (el) {
        el.classList.toggle("ff-selected", self._isSelected(el.dataset.id));
      });
      if (_selectedId) {
        if (!skipProps) {
          this._propsWidget = _findWidget(_selectedId) || null;
          this._showFloatingToolbar(_selectedId);
          this._openProps();
        } else {
          this._showFloatingToolbar(_selectedId);
        }
      } else {
        this._propsWidget = null;
        this._hideFloatingToolbar();
        this._closeProps();
      }
      this._updateStatusBar();
      this._refreshGroupOutline();
    },

    /* Draw a dashed outline around the persistent group of the primary selected
       widget (if any) so users can see the group's extent while editing. */
    _refreshGroupOutline: function () {
      var canvas = document.getElementById("bd-canvas");
      var old = document.getElementById("bd-group-outline");
      if (old) old.remove();
      if (!canvas || !_selectedId) return;
      var w = _findWidget(_selectedId);
      if (!w || !w.group || !_dash.groups || !_dash.groups[w.group]) return;
      var b = this._groupBounds(w.group);
      if (!b) return;
      var el = document.createElement("div");
      el.id = "bd-group-outline";
      el.className = "bd-group-outline";
      el.style.left = b.x + "px";
      el.style.top = b.y + "px";
      el.style.width = b.w + "px";
      el.style.height = b.h + "px";
      canvas.appendChild(el);
    },

    _bindSelection: function () {
      var self = this;
      document.querySelectorAll(".ff-widget").forEach(function (el) {
        el.addEventListener("click", function (e) {
          if (e.target.closest(".ff-widget-btn") || e.target.closest(".ff-resize-handle")) return;
          var id = this.dataset.id;
          if (self._dragJustMoved === id) { self._dragJustMoved = null; return; }
          if (e.shiftKey) {
            self._toggleSelectWidget(id);
          } else if (self._selectedId === id) {
            self._deselect();
          } else if (self._isSelected(id)) {
            self._makePrimary(id);
          } else {
            self._selectWidget(id);
          }
        });
      });
    },

    /* Inline text editing: dbl-click a Text Box to type directly on the canvas */
    _bindTextEdit: function () {
      var self = this;
      document.querySelectorAll(".ff-text").forEach(function (t) {
        t.addEventListener("dblclick", function (e) {
          e.stopPropagation();
          self._startTextEdit(t.dataset.id, t);
        });
      });
    },

    _startTextEdit: function (id, node) {
      var w = _findWidget(id);
      if (!w || !node) return;
      this._selectWidget(id);
      node.contentEditable = "true";
      node.classList.add("ff-text-editing");
      node.focus();
      var r = document.createRange();
      r.selectNodeContents(node);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
      var self = this;
      var committed = false;
      function commit() {
        if (committed) return;
        committed = true;
        node.removeEventListener("blur", commit);
        node.removeEventListener("keydown", onKey);
        var val = node.innerText != null ? node.innerText : node.textContent;
        w.config = w.config || {};
        w.config.content = val;
        node.contentEditable = "false";
        node.classList.remove("ff-text-editing");
        self._refreshHistory();
        self._refreshProps();
      }
      function onKey(e) {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); node.blur(); }
        else if (e.key === "Escape") { node.innerText = (w.config && w.config.content) || ""; node.blur(); }
      }
      node.addEventListener("blur", commit);
      node.addEventListener("keydown", onKey);
    },

    _bindCanvasClickDeselect: function () {
      var self = this;
      if (!window.__bdPointerBound) {
        window.__bdPointerBound = true;
        document.addEventListener("mousemove", function (e) {
          _lastPointerCX = e.clientX; _lastPointerCY = e.clientY;
        });
      }
      var canvas = document.getElementById("bd-canvas");
      if (!canvas) return;
      canvas.addEventListener("mousedown", function (e) {
        _lastPointerCX = e.clientX; _lastPointerCY = e.clientY;
        if (e.button !== 0) return;
        if (e.target.closest(".ff-widget") || e.target.closest(".ff-float-toolbar") ||
            e.target.closest("button") || e.target.closest(".ff-resize-handle") ||
            e.target.closest("select") || e.target.closest("input") || e.target.closest("textarea") ||
            e.target.closest(".bd-ruler")) return;
        var rect = canvas.getBoundingClientRect();
        var sx = e.clientX, sy = e.clientY;
        var lastX = sx, lastY = sy;
        var mq = null;
        var moved = false;
        function onMove(ev) {
          _lastPointerCX = ev.clientX; _lastPointerCY = ev.clientY;
          lastX = ev.clientX; lastY = ev.clientY;
          if (!moved && (Math.abs(lastX - sx) + Math.abs(lastY - sy)) > 4) {
            moved = true;
            mq = document.createElement("div");
            mq.className = "bd-marquee";
            canvas.appendChild(mq);
          }
          if (!mq) return;
          var x1 = (Math.min(sx, lastX) - rect.left) / _zoom, y1 = (Math.min(sy, lastY) - rect.top) / _zoom;
          var x2 = (Math.max(sx, lastX) - rect.left) / _zoom, y2 = (Math.max(sy, lastY) - rect.top) / _zoom;
          mq.style.left = x1 + "px";
          mq.style.top = y1 + "px";
          mq.style.width = (x2 - x1) + "px";
          mq.style.height = (y2 - y1) + "px";
        }
        function onUp() {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          if (mq && mq.parentNode) mq.remove();
          if (!moved) {
            if (!e.shiftKey) self._deselect();
            return;
          }
          var xl = (Math.min(sx, lastX) - rect.left) / _zoom, xr = (Math.max(sx, lastX) - rect.left) / _zoom;
          var yt = (Math.min(sy, lastY) - rect.top) / _zoom, yb = (Math.max(sy, lastY) - rect.top) / _zoom;
          var hit = _currentWidgets().filter(function (w) {
            return w.x < xr && w.x + w.width > xl && w.y < yb && w.y + w.height > yt;
          }).sort(function (a, b) { return (a.zIndex || 0) - (b.zIndex || 0); });
          if (hit.length === 0) {
            if (!e.shiftKey) self._deselect();
          } else if (e.shiftKey) {
            var merged = _selectedIds.slice();
            hit.forEach(function (w) { if (merged.indexOf(w.id) === -1) merged.push(w.id); });
            self._setSelection(merged);
          } else {
            self._setSelection(hit.map(function (w) { return w.id; }));
          }
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
      canvas.addEventListener("dblclick", function (e) {
        if (e.target.closest(".ff-widget")) return;
        self._addTextAtPoint(e);
      });
    },

    /* Double-click an empty canvas area to drop a text annotation there */
    _addTextAtPoint: function (e) {
      var canvas = document.getElementById("bd-canvas");
      if (!canvas) return;
      var rect = canvas.getBoundingClientRect();
      var x = Math.max(0, (e.clientX - rect.left) / _zoom - 60);
      var y = Math.max(0, (e.clientY - rect.top) / _zoom - 15);
      var id = "w" + Date.now().toString(36) + "_" + (++_seq);
      var widget = {
        id: id, type: "text", title: "Text",
        x: Math.round(x), y: Math.round(y),
        width: 240, height: 60,
        zIndex: _currentWidgets().length + 1, locked: false,
        plain: true,
        config: { table: _dash.table, sql: "", label: "Text", content: "Double-click to edit this text." }
      };
      var emptyEl = document.getElementById("bd-empty-state");
      if (emptyEl) emptyEl.remove();
      _currentWidgets().push(widget);
      this._initGrid();
      this._selectWidget(id);
      var node = document.querySelector('.ff-text[data-id="' + id + '"]');
      if (node) this._startTextEdit(id, node);
      this._refreshHistory();
      App.success("Text added");
    },

    _deselect: function () {
      _selectedId = null;
      _selectedIds = [];
      this._propsWidget = null;
      document.querySelectorAll(".ff-widget").forEach(function (el) { el.classList.remove("ff-selected"); });
      this._hideFloatingToolbar();
      this._closeProps();
      var go = document.getElementById("bd-group-outline");
      if (go) go.remove();
    },

    /* ── Properties panel (right-side dock — schema-driven) ── */
    _openProps: function () {
      if (!this._propsWidget) { this._closeProps(); return; }
      if (window.PropsPanel) {
        PropsPanel.open(this._propsWidget);
      } else {
        this._openPropsLegacy();
      }
    },

    /* Legacy fallback when PropsPanel is not loaded */
    _openPropsLegacy: function () {
      var dock = document.getElementById("bd-props");
      if (!dock) {
        dock = document.createElement("div");
        dock.id = "bd-props";
        dock.className = "bd-props";
        dock.innerHTML = '<div class="bd-props-head"><span class="bd-props-title">Properties</span><button class="ff-widget-btn" id="bd-props-close" title="Close"><i class="fas fa-times"></i></button></div><div class="bd-props-body" id="bd-props-body"></div>';
        document.querySelector(".bd-page").appendChild(dock);
        var self = this;
        document.getElementById("bd-props-close").addEventListener("click", function () { self._deselect(); });
      }
      this._renderProps();
    },

    _renderProps: function () {
      var body = document.getElementById("bd-props-body");
      var w = this._propsWidget;
      if (!body || !w) return;
      var c = w.config || {};
      var typeEsc = _esc(w.type || "");
      var targetIdx = (c.targetPage != null) ? c.targetPage : _dash.currentPage;
      var destHtml = w.type === "page"
        ? '<div class="bd-props-section">Destination</div>' +
          _propsField("Go to page", '<select class="form-input" id="bdp-target-page">' + _dash.pages.map(function (p, i) {
            return '<option value="' + i + '"' + (targetIdx === i ? " selected" : "") + ">" + _esc(p.name || ("Page " + (i + 1))) + "</option>";
          }).join("") + "</select>")
        : "";
      var actHtml = w.type === "button"
        ? '<div class="bd-props-section">Behavior</div>' +
          _propsField("Action", '<select class="form-input" id="bdp-act">' +
            [["reset", "Clear all filters"], ["bookmark", "Apply bookmark"], ["page", "Go to page"], ["refresh", "Refresh data"]].map(function (a) {
              return '<option value="' + a[0] + '"' + ((c.action || "reset") === a[0] ? " selected" : "") + ">" + a[1] + "</option>";
            }).join("") + "</select>") +
          _propsField("Value (bookmark id/name, page # or name)", '<input type="text" class="form-input" id="bdp-act-val" placeholder="Optional" value="' + _esc(c.value || "") + '">')
        : "";
      var html =
        '<div class="bd-props-type">' + typeEsc + '</div>' +
        _propsField("Title", '<input type="text" class="form-input" id="bdp-title" value="' + _esc(w.title || "") + '">') +
        destHtml +
        actHtml +
        '<div class="bd-props-section">Layout</div>' +
        '<div class="bd-props-grid">' +
          _propsField("X", '<input type="number" class="form-input" id="bdp-x" value="' + (w.x || 0) + '">') +
          _propsField("Y", '<input type="number" class="form-input" id="bdp-y" value="' + (w.y || 0) + '">') +
          _propsField("Width", '<input type="number" class="form-input" id="bdp-w" value="' + (w.width || 0) + '">') +
          _propsField("Height", '<input type="number" class="form-input" id="bdp-h" value="' + (w.height || 0) + '">') +
        '</div>' +
        '<div class="bd-props-grid">' +
          _propsField("Z-Index", '<input type="number" class="form-input" id="bdp-z" value="' + (w.zIndex || 0) + '">') +
          _propsField("Lock", '<label class="bd-props-switch"><input type="checkbox" id="bdp-lock" ' + (w.locked ? "checked" : "") + '><span class="bd-props-slider"></span></label>') +
          _propsField("Background", '<input type="color" class="form-input bdp-color" id="bdp-bg" value="' + _toHex(w.bgColor || "#ffffff") + '">') +
          _propsField("Border", '<input type="color" class="form-input bdp-color" id="bdp-border" value="' + _toHex(w.borderColor || "#e2e8f0") + '">') +
        '</div>' +
        '<div class="bd-props-grid">' +
          _propsField("Show title", '<label class="bd-props-switch"><input type="checkbox" id="bdp-showtitle" ' + (w.showTitle ? "checked" : "") + '><span class="bd-props-slider"></span></label>') +
          (w.type === "text" ? _propsField("Transparent", '<label class="bd-props-switch"><input type="checkbox" id="bdp-plain" ' + (w.plain ? "checked" : "") + '><span class="bd-props-slider"></span></label>') : "") +
        '</div>' +
        (_FILTER_TYPES.indexOf(w.type) !== -1 || w.type === "text" || w.type === "image" || w.type === "custom" || w.type === "page"
          || w.type === "shape" || w.type === "divider"
          ? ""
          : '<div class="bd-props-section">Data</div>' +
            '<button class="btn btn-sm btn-ghost bd-props-data-btn" id="bdp-edit-data"><i class="fas fa-database"></i> Edit Data / Settings</button>') +
        '<div class="bd-props-actions">' +
          '<button class="btn btn-sm btn-ghost" id="bdp-dup"><i class="fas fa-copy"></i> Duplicate</button>' +
          '<button class="btn btn-sm btn-danger" id="bdp-del"><i class="fas fa-trash"></i> Delete</button>' +
        '</div>';
      body.innerHTML = html;
      this._bindProps(w);
    },

    _bindProps: function (w) {
      var self = this;
      function bind(id, prop, apply) {
        var el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("input", function () {
          var val = el.type === "number" ? (parseFloat(el.value) || 0) : el.value;
          w[prop] = val;
          if (apply) apply();
          self._syncPropsWidget(w, prop);
        });
      }
      bind("bdp-title", "title");
      var tp = document.getElementById("bdp-target-page");
      if (tp) tp.addEventListener("change", function () {
        w.config = w.config || {};
        w.config.targetPage = parseInt(tp.value, 10);
        var cell = document.querySelector('.ff-widget-content[data-id="' + w.id + '"]');
        if (cell) self._renderWidgetContent(w, cell);
        self._refreshHistory();
      });
      var pact = document.getElementById("bdp-act");
      var pactVal = document.getElementById("bdp-act-val");
      var saveBtnCfg = function () {
        w.config = w.config || {};
        w.config.action = pact ? pact.value : (w.config.action || "reset");
        w.config.value = pactVal ? pactVal.value : (w.config.value || "");
        var cell = document.querySelector('.ff-widget-content[data-id="' + w.id + '"]');
        if (cell) self._renderWidgetContent(w, cell);
        self._markDirty();
        self._refreshHistory();
        self._selectWidget(w.id);
      };
      if (pact) pact.addEventListener("change", saveBtnCfg);
      if (pactVal) pactVal.addEventListener("change", saveBtnCfg);
      bind("bdp-x", "x", function () { self._positionWidget(w); });
      bind("bdp-y", "y", function () { self._positionWidget(w); });
      bind("bdp-w", "width", function () { self._positionWidget(w); self._resizeWidget(w.id); });
      bind("bdp-h", "height", function () { self._positionWidget(w); self._resizeWidget(w.id); });
      bind("bdp-z", "zIndex", function () { self._positionWidget(w); });
      bind("bdp-bg", "bgColor", function () { self._paintWidget(w); });
      bind("bdp-border", "borderColor", function () { self._paintWidget(w); });
      var lock = document.getElementById("bdp-lock");
      if (lock) lock.addEventListener("change", function () {
        w.locked = lock.checked;
        self._initGrid();
        self._selectWidget(w.id);
        self._refreshHistory();
      });
      var st = document.getElementById("bdp-showtitle");
      if (st) st.addEventListener("change", function () {
        w.showTitle = st.checked;
        self._initGrid();
        self._selectWidget(w.id);
        self._refreshHistory();
      });
      var pl = document.getElementById("bdp-plain");
      if (pl) pl.addEventListener("change", function () {
        w.plain = pl.checked;
        self._initGrid();
        self._selectWidget(w.id);
        self._refreshHistory();
      });
      var dup = document.getElementById("bdp-dup");
      if (dup) dup.addEventListener("click", function () { self._duplicateWidget(w.id); });
      var del = document.getElementById("bdp-del");
      if (del) del.addEventListener("click", function () { self._deleteWidget(w.id); });
      var ed = document.getElementById("bdp-edit-data");
      if (ed) ed.addEventListener("click", function () {
        if (window.WidgetEditor) window.WidgetEditor.open(w, function (updated) {
          self._onWidgetEdited(w.id);
          self._markDirty();
          self._refreshHistory();
        });
      });
    },

    /* Reflect a property edit back onto the widget DOM (title, colors, etc.) */
    _syncPropsWidget: function (w, prop) {
      var el = document.querySelector('.ff-widget[data-id="' + w.id + '"]');
      if (el && prop === "title") {
        var t = el.querySelector(".ff-widget-title-inner");
        if (t) t.textContent = w.title;
      }
      if (prop === "bgColor" || prop === "borderColor") this._paintWidget(w);
      if (prop === "zIndex") this._growCanvas();
    },

    _paintWidget: function (w) {
      var el = document.querySelector('.ff-widget[data-id="' + w.id + '"]');
      if (!el) return;
      if (w.bgColor) el.style.backgroundColor = w.bgColor;
      else if (el.style.backgroundColor) el.style.backgroundColor = "";
      if (w.borderColor) el.style.borderColor = w.borderColor;
      else if (el.style.borderColor) el.style.borderColor = "";
      if (w.plain || (w.type === "text" && !w.bgColor && !w.borderColor)) el.dataset.plain = "1";
      else delete el.dataset.plain;
      /* P3.9 — reflect the report tooltip-page flag dynamically so the schema
         toggle badge and present-mode hiding update without a full re-render. */
      var isTip = !!(w.config && w.config.isTooltip);
      var presentMode = _dash.mode === "present";
      el.classList.toggle("ff-tooltip-page", isTip);
      var rb = el.querySelector(".bd-tooltip-ribbon");
      if (!presentMode && isTip && !rb) {
        var rb2 = document.createElement("div");
        rb2.className = "bd-tooltip-ribbon";
        rb2.title = "This widget is the report's tooltip page (renders on chart hover)";
        rb2.innerHTML = '<i class="fas fa-comment-dots"></i> Tooltip page';
        el.appendChild(rb2);
      } else if (!isTip && rb) rb.remove();
      if (isTip) el.style.display = presentMode ? "none" : "";
      _applyCardStyle(w, el);
    },

    _closeProps: function () {
      if (window.PropsPanel) PropsPanel.close();
      var dock = document.getElementById("bd-props");
      if (dock) dock.remove();
    },

    _refreshProps: function () {
      if (window.PropsPanel && this._propsWidget) {
        PropsPanel.refresh();
      } else if (this._propsWidget) {
        this._renderProps();
      }
    },

    /* Bind the header action buttons (menu / config / remove) for all widgets */
    _bindWidgetChrome: function () {
      var self = this;
      document.querySelectorAll(".ff-widget-config-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          var id = btn.dataset.id;
          var w = _findWidget(id);
          if (w && w.type === "page") { self._selectWidget(id); return; }
          if (w && window.WidgetEditor) {
            window.WidgetEditor.open(w, function (updated) {
              self._onWidgetEdited(id);
              self._markDirty();
              self._refreshHistory();
            });
          }
        });
      });
      document.querySelectorAll(".ff-widget-remove-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          self._deleteWidget(btn.dataset.id);
        });
      });
      document.querySelectorAll(".ff-widget-menu-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          self._openWidgetMenu(btn, btn.dataset.id);
        });
      });
    },

    /* Open the per-widget floating actions menu (refresh/edit/duplicate/lock/z/remove) */
    _openWidgetMenu: function (anchorBtn, id) {
      this._closeContextMenus();
      this._deselect();
      var menu = this._buildWidgetMenu(id);
      if (!menu) return;
      var r = anchorBtn.getBoundingClientRect();
      this._placeMenu(menu, r.left, r.bottom + 2);
    },
    _openWidgetMenuAt: function (id, clientX, clientY) {
      this._closeContextMenus();
      this._selectWidget(id);
      var menu = this._buildWidgetMenu(id);
      if (!menu) return;
      this._placeMenu(menu, clientX, clientY);
    },
    _buildWidgetMenu: function (id) {
      var self = this;
      var w = _findWidget(id);
      if (!w) return null;
      var html =
        '<div style="padding:6px 10px;font-size:11px;color:var(--color-text-muted);border-bottom:1px solid var(--color-card-border,var(--color-border));margin-bottom:4px;">' + _esc(w.title || w.type) + "</div>" +
        (this._isDataWidget(w) ? '<button data-act="recreate-ai"><i class="fas fa-wand-magic-sparkles" style="color:var(--color-accent-purple)"></i> Recreate with AI</button>' : "") +
        '<button data-act="edit"><i class="fas fa-cog"></i> Settings</button>' +
        '<button data-act="refresh"><i class="fas fa-sync"></i> Refresh data</button>' +
        '<button data-act="duplicate"><i class="fas fa-copy"></i> Duplicate</button>' +
        (w.config && w.config.measureExpr ? '<button data-act="period"><i class="fas fa-clock"></i> Time period: <b>' + _esc((w.config.timeIntel || "none").toUpperCase()) + '</b></button>' : "") +
        '<button data-act="template"><i class="fas fa-bookmark"></i> Save as template</button>' +
        '<button data-act="responsive"><i class="fas fa-mobile-screen"></i> Responsive: <b>' + _esc((w.responsive || "auto").toUpperCase()) + '</b></button>' +
        '<button data-act="copy-style"><i class="fas fa-paintbrush"></i> Copy Style</button>' +
        (window.WidgetPresets && WidgetPresets.hasStyle() ? '<button data-act="paste-style"><i class="fas fa-paintbrush"></i> Paste Style</button>' : '') +
        (w.config && w.config.drillPage ? '<button data-act="drillthrough"><i class="fas fa-crosshairs"></i> Drill through ▸ ' + _esc(String(w.config.drillPage)) + '</button>' : '') +
        '<button data-act="lock"><i class="fas ' + (w.locked ? "fa-lock-open" : "fa-lock") + '"></i> ' + (w.locked ? "Unlock" : "Lock") + '</button>' +
        '<button data-act="front"><i class="fas fa-arrow-up-wide-short"></i> Bring to front</button>' +
        '<button data-act="back"><i class="fas fa-arrow-down-wide-short"></i> Send to back</button>' +
        '<div style="border-top:1px solid var(--color-card-border,var(--color-border));margin:4px 0;"></div>' +
        '<button data-act="remove" class="ff-menu-danger"><i class="fas fa-trash"></i> Remove</button>';
      var menu = document.createElement("div");
      menu.className = "ff-widget-menu";
      menu.innerHTML = html;
      menu.addEventListener("click", function (e) {
        var btn = e.target.closest("button");
        if (!btn) return;
        var act = btn.dataset.act;
        menu.remove();
        self._runMenuAction(act, id);
      });
      return menu;
    },
    /* Attach a menu element at viewport coords, flipping to keep it on screen */
    _placeMenu: function (menu, x, y) {
      document.body.appendChild(menu);
      menu.style.visibility = "hidden";
      var mw = menu.offsetWidth || 200, mh = menu.offsetHeight || 260;
      menu.style.visibility = "";
      var left = x, top = y;
      if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
      if (top + mh > window.innerHeight - 8) top = Math.max(8, y - mh - 8);
      menu.style.left = Math.max(4, left) + "px";
      menu.style.top = Math.max(4, top) + "px";
      menu.style.position = "fixed";
      var self = this;
      var close = function () { if (menu.parentNode) menu.remove(); };
      setTimeout(function () {
        document.addEventListener("click", function outer(e) {
          if (!menu.contains(e.target)) { close(); document.removeEventListener("click", outer, true); }
        }, true);
      }, 0);
      return close;
    },

    _runMenuAction: function (act, id) {
      var self = this;
      if (act === "edit") {
        var w = _findWidget(id);
        if (w && w.type === "page") { this._selectWidget(id); return; }
        if (w && window.WidgetEditor) window.WidgetEditor.open(w, function (updated) { self._onWidgetEdited(id); self._markDirty(); self._refreshHistory(); });
      } else if (act === "recreate-ai") {
        var wr = _findWidget(id);
        if (wr) this._openAIRecreate(id);
      } else if (act === "copy-style") {
        var wc = _findWidget(id);
        if (wc && window.WidgetPresets) { WidgetPresets.copyStyle(wc); this._selectWidget(id); }
      } else if (act === "paste-style") {
        var wp = _findWidget(id);
        if (wp && window.WidgetPresets) { WidgetPresets.pasteStyle(wp); this._onWidgetEdited(id); this._refreshHistory(); this._selectWidget(id); }
      } else if (act === "refresh") {
        this._refreshWidget(id);
      } else if (act === "duplicate") {
        this._duplicateWidget(id);
      } else if (act === "template") {
        this._saveAsTemplate(id);
      } else if (act === "responsive") {
        var wr2 = _findWidget(id);
        if (!wr2) return;
        var modes = [["auto", "Auto"], ["full", "Full width"], ["half", "Half width"], ["hide", "Hide on small screens"]];
        var sub = document.createElement("div");
        sub.className = "ff-widget-menu";
        sub.innerHTML = '<div style="padding:6px 10px;font-size:11px;color:var(--color-text-muted);border-bottom:1px solid var(--color-card-border,var(--color-border));margin-bottom:4px;">Responsive intent</div>' +
          modes.map(function (mm) {
            var active = (wr2.responsive || "auto") === mm[0];
            return '<button data-m="' + mm[0] + '"' + (active ? ' class="ff-menu-active"' : "") + '>' +
              '<i class="fas ' + (active ? "fa-check" : "fa-mobile-screen") + '"></i> ' + mm[1] + '</button>';
          }).join("");
        sub.addEventListener("click", function (e2) {
          var b2 = e2.target.closest("button");
          if (!b2) return;
          var m = b2.dataset.m;
          sub.remove();
          if ((wr2.responsive || "auto") === m) return;
          wr2.responsive = m;
          self._markDirty();
          self._refreshHistory();
          self._selectWidget(id);
          App.success("Responsive intent set to " + m);
        });
        var wEl2 = document.querySelector('.ff-widget[data-id="' + id + '"]');
        var bx2 = window.innerWidth / 2, by2 = window.innerHeight / 2;
        if (wEl2) { var r2 = wEl2.getBoundingClientRect(); bx2 = r2.left; by2 = r2.bottom + 4; }
        sub.style.position = "fixed";
        sub.style.left = bx2 + "px";
        sub.style.top = by2 + "px";
        document.body.appendChild(sub);
        var close2 = function () { if (sub.parentNode) sub.remove(); };
        setTimeout(function () {
          document.addEventListener("click", function outer2(e3) {
            if (!sub.contains(e3.target)) { close2(); document.removeEventListener("click", outer2, true); }
          }, true);
        }, 0);
      } else if (act === "lock") {
        var wl = _findWidget(id);
        if (wl) { wl.locked = !wl.locked; this._initGrid(); this._selectWidget(id); this._refreshHistory(); }
      } else if (act === "front") {
        var wf = _findWidget(id);
        if (wf) {
          var maxZ = 0;
          _currentWidgets().forEach(function (x) { if (x.zIndex > maxZ) maxZ = x.zIndex; });
          wf.zIndex = maxZ + 1;
          this._initGrid(); this._selectWidget(id); this._refreshHistory();
        }
      } else if (act === "back") {
        var wb = _findWidget(id);
        if (wb) {
          var minZ = Infinity;
          _currentWidgets().forEach(function (x) { if (x.zIndex < minZ) minZ = x.zIndex; });
          wb.zIndex = Math.max(1, minZ - 1);
          this._initGrid(); this._selectWidget(id); this._refreshHistory();
        }
      } else if (act === "period") {
        var wm = _findWidget(id);
        if (!wm || !wm.config) return;
        var modes = [["none", "Value"], ["ytd", "YTD"], ["mtd", "MTD"], ["qtd", "QTD"], ["mom", "MoM"]];
        var sub = document.createElement("div");
        sub.className = "ff-widget-menu";
        sub.innerHTML = '<div style="padding:6px 10px;font-size:11px;color:var(--color-text-muted);border-bottom:1px solid var(--color-card-border,var(--color-border));margin-bottom:4px;">Time period</div>' +
          modes.map(function (mm) {
            var active = (wm.config.timeIntel || "none") === mm[0];
            return '<button data-m="' + mm[0] + '"' + (active ? ' class="ff-menu-active"' : "") + '>' +
              '<i class="fas ' + (active ? "fa-check" : "fa-clock") + '"></i> ' + mm[1] + '</button>';
          }).join("");
        sub.addEventListener("click", function (e2) {
          var b2 = e2.target.closest("button");
          if (!b2) return;
          var m = b2.dataset.m;
          sub.remove();
          if ((wm.config.timeIntel || "none") === m) return;
          wm.config.timeIntel = m;
          var rebuilt = window.BITimeIntel ? window.BITimeIntel.build(wm.config.measureExpr, wm.config.table, wm.config.dateCol || "", m) : null;
          if (rebuilt) { wm.config.sql = rebuilt.sql; wm.config.sparkSql = rebuilt.spark_sql || ""; }
          self._markDirty();
          self._refreshWidget(id);
          self._refreshHistory();
          self._selectWidget(id);
        });
        var wEl = document.querySelector('.ff-widget[data-id="' + id + '"]');
        var bx = window.innerWidth / 2, by = window.innerHeight / 2;
        if (wEl) { var r = wEl.getBoundingClientRect(); bx = r.left; by = r.bottom + 4; }
        this._placeMenu(sub, bx, by);
      } else if (act === "drillthrough") {
        var wd = _findWidget(id);
        if (wd && wd.config && wd.config.drillPage) self._drillThrough(wd.config.drillPage);
      } else if (act === "remove") {
        this._deleteWidget(id);
      } else if (act === "size-s" || act === "size-m" || act === "size-l") {
        this._resizeWidgetSel(id, act.slice(5));
      }
    },

    _deleteWidget: function (id) {
      var self = this;
      var doDelete = function () {
        var arr = _currentWidgets();
        for (var i = 0; i < arr.length; i++) {
          if (arr[i].id === id) { arr.splice(i, 1); break; }
        }
        if (_selectedId === id) self._deselect();
        self._pruneGroups();
        self._initGrid();
        self._refreshHistory();
      };
      if (window.Modal && window.Modal.confirm) {
        window.Modal.confirm("Remove widget?", "Remove this widget from the canvas?", { variant: "danger", confirmLabel: "Remove" }).then(function (ok) {
          if (ok) doDelete();
        });
      } else {
        doDelete();
      }
    },

    _duplicateWidget: function (id) {
      var self = this;
      var w = _findWidget(id);
      if (!w) return;
      var copy = JSON.parse(JSON.stringify(w));
      copy.id = "w" + Date.now().toString(36) + "_" + (++_seq);
      copy.x = w.x + 24; copy.y = w.y + 24;
      copy.zIndex = (w.zIndex || 0) + 1;
      copy.title = (copy.title || "Widget") + " (copy)";
      _currentWidgets().push(copy);
      this._initGrid();
      this._selectWidget(copy.id);
      this._refreshHistory();
      App.success("Duplicated widget");
    },

    /* ── Multi-selection operations: align / distribute / match / copy / paste ── */

    _selectedUnlocked: function () {
      var self = this;
      return _currentWidgets().filter(function (x) { return self._isSelected(x.id) && !x.locked; });
    },

    _deleteSelection: function () {
      var sel = this._selectedUnlocked();
      if (sel.length === 0) return;
      var ids = {};
      sel.forEach(function (x) { ids[x.id] = true; });
      var arr = _currentWidgets();
      for (var i = arr.length - 1; i >= 0; i--) {
        if (ids[arr[i].id]) arr.splice(i, 1);
      }
      this._deselect();
      this._pruneGroups();
      this._initGrid();
      this._refreshHistory();
    },

    _copySelection: function () {
      var sel = this._selectedUnlocked();
      if (sel.length === 0) { App.warning("Nothing selected to copy"); return; }
      _copyPool = sel.map(function (w) { return JSON.parse(JSON.stringify(w)); });
      App.success("Copied " + _copyPool.length + " widget" + (_copyPool.length > 1 ? "s" : ""));
    },

    _pasteClipboard: function () {
      if (_copyPool.length === 0) { App.warning("Clipboard is empty"); return; }
      var maxZ = 0;
      _currentWidgets().forEach(function (w) { if ((w.zIndex || 0) > maxZ) maxZ = w.zIndex || 0; });
      /* Anchor the paste near the last pointer on the canvas (fall back to +24 offset) */
      var minX = Infinity, minY = Infinity;
      _copyPool.forEach(function (s) {
        minX = Math.min(minX, s.x); minY = Math.min(minY, s.y);
      });
      var canvas = document.getElementById("bd-canvas");
      var rect = canvas ? canvas.getBoundingClientRect() : null;
      var ox = 24, oy = 24;
      if (rect && _lastPointerCX != null && _lastPointerCY != null) {
        var px = (_lastPointerCX - rect.left) / _zoom;
        var py = (_lastPointerCY - rect.top) / _zoom;
        if (px >= 0 && py >= 0) { ox = Math.round(px - minX); oy = Math.round(py - minY); }
      }
      var pasted = [];
      var self = this;
      _copyPool.forEach(function (src) {
        var copy = JSON.parse(JSON.stringify(src));
        copy.id = "w" + Date.now().toString(36) + "_" + (++_seq);
        copy.x = Math.max(0, src.x + ox); copy.y = Math.max(0, src.y + oy);
        copy.zIndex = ++maxZ;
        pasted.push(copy);
        _currentWidgets().push(copy);
      });
      this._initGrid();
      this._setSelection(pasted.map(function (w) { return w.id; }));
      this._growCanvas();
      this._refreshHistory();
      App.success("Pasted " + pasted.length + " widget" + (pasted.length > 1 ? "s" : ""));
    },

    _duplicateSelection: function () {
      var sel = this._selectedUnlocked();
      if (sel.length === 0) { App.warning("Nothing selected"); return; }
      var maxZ = 0;
      _currentWidgets().forEach(function (w) { if ((w.zIndex || 0) > maxZ) maxZ = w.zIndex || 0; });
      var dups = [];
      sel.forEach(function (w) {
        var copy = JSON.parse(JSON.stringify(w));
        copy.id = "w" + Date.now().toString(36) + "_" + (++_seq);
        copy.x = w.x + 24; copy.y = w.y + 24;
        copy.zIndex = ++maxZ;
        dups.push(copy);
        _currentWidgets().push(copy);
      });
      this._initGrid();
      this._setSelection(dups.map(function (w) { return w.id; }));
      this._refreshHistory();
      App.success("Duplicated " + dups.length + " widget" + (dups.length > 1 ? "s" : ""));
    },

    /* ── A4 — Style copy / paste & style library ── */

    /* The style-relevant subset of a widget: config.style plus the handful of
       top-level config keys that drive card appearance. */
    _styleOf: function (w) {
      var c = w.config || {};
      var st = (c.style && typeof c.style === "object") ? JSON.parse(JSON.stringify(c.style)) : {};
      var out = { style: st };
      ["borderShapePct", "borderShape", "borderRadius", "titleFontSize", "showTitle"].forEach(function (k) {
        if (c[k] !== undefined) out[k] = c[k];
      });
      return out;
    },

    _copyStyle: function () {
      var sel = this._selectedUnlocked();
      if (sel.length === 0) { App.warning("Nothing selected to copy style from"); return; }
      _styleClipboard = this._styleOf(sel[0]);
      App.success("Style copied from \"" + (sel[0].title || "widget") + "\"");
    },

    _pasteStyle: function () {
      if (!_styleClipboard) { App.warning("No style copied yet — select a widget and use Copy Style first"); return; }
      var sel = this._selectedUnlocked();
      if (sel.length === 0) { App.warning("Nothing selected to paste style onto"); return; }
      var self = this;
      sel.forEach(function (w) {
        w.config = w.config || {};
        w.config.style = JSON.parse(JSON.stringify(_styleClipboard.style));
        Object.keys(_styleClipboard).forEach(function (k) {
          if (k === "style") return;
          if (_styleClipboard[k] === undefined) delete w.config[k];
          else w.config[k] = _styleClipboard[k];
        });
        _applyCardStyle(w, document.querySelector('[data-id="' + w.id + '"]'));
      });
      this._initGrid();
      this._refreshHistory();
      this._refreshProps();
      App.success("Style pasted onto " + sel.length + " widget" + (sel.length > 1 ? "s" : ""));
    },

    /* Save the current selection's style as a named preset in _dash.stylePresets. */
    _saveStylePreset: function (name) {
      var sel = this._selectedUnlocked();
      if (sel.length === 0) { App.warning("Select a widget first"); return; }
      name = (name || "").trim();
      if (!name) { App.warning("Give the preset a name"); return; }
      _dash.stylePresets = _dash.stylePresets || {};
      _dash.stylePresets[name] = this._styleOf(sel[0]);
      this._markDirty();
      App.success("Saved style preset \"" + name + "\"");
    },

    _applyStylePreset: function (name) {
      var presets = _dash.stylePresets || {};
      var p = presets[name];
      if (!p) { App.warning("Style preset \"" + name + "\" not found"); return; }
      var sel = this._selectedUnlocked();
      if (sel.length === 0) { App.warning("Nothing selected"); return; }
      var self = this;
      sel.forEach(function (w) {
        w.config = w.config || {};
        w.config.style = JSON.parse(JSON.stringify(p.style));
        Object.keys(p).forEach(function (k) {
          if (k === "style") return;
          if (p[k] === undefined) delete w.config[k];
          else w.config[k] = p[k];
        });
        _applyCardStyle(w, document.querySelector('[data-id="' + w.id + '"]'));
      });
      this._initGrid();
      this._refreshHistory();
      this._refreshProps();
      App.success("Applied style preset \"" + name + "\"");
    },

    _deleteStylePreset: function (name) {
      if (!_dash.stylePresets || !_dash.stylePresets[name]) return;
      delete _dash.stylePresets[name];
      this._markDirty();
      App.success("Deleted style preset \"" + name + "\"");
    },

    /* Style Library flyout: list presets, apply / save / delete. */
    _showStyleLibrary: function (anchorBtn) {
      this._closeContextMenus();
      var self = this;
      var presets = _dash.stylePresets || {};
      var names = Object.keys(presets);
      var menu = document.createElement("div");
      menu.className = "bd-arrange-menu bd-style-library";
      var rows = names.length
        ? names.map(function (n) {
            return '<div class="bd-style-preset-row">' +
              '<button class="btn btn-sm btn-ghost bd-style-preset-apply" data-name="' + _esc(n) + '" title="Apply to selection">' + _esc(n) + '</button>' +
              '<button class="btn btn-sm btn-ghost bd-style-preset-del" data-name="' + _esc(n) + '" title="Delete preset"><i class="fas fa-trash"></i></button>' +
            '</div>';
          }).join("")
        : '<div class="bd-arrange-count">No saved styles yet</div>';
      menu.innerHTML =
        '<div class="bd-arrange-count">Style Library</div>' +
        '<div class="bd-style-preset-list">' + rows + '</div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-style-preset-save">' +
          '<input class="form-control form-control-sm bd-style-preset-name" placeholder="Preset name…" />' +
          '<button class="btn btn-sm btn-primary bd-style-preset-save-btn" ' + (_selectedIds.length ? "" : "disabled") + '>Save</button>' +
        '</div>';
      var backdrop = document.createElement("div");
      backdrop.className = "bd-menu-backdrop";
      document.body.appendChild(backdrop);
      document.body.appendChild(menu);
      var r = anchorBtn && anchorBtn.getBoundingClientRect();
      var mw = menu.offsetWidth || 260, mh = menu.offsetHeight || 300;
      var px = anchorBtn ? r.left : (_lastPointerCX != null ? _lastPointerCX : 40);
      var py = anchorBtn ? r.bottom + 6 : (_lastPointerCY != null ? _lastPointerCY : 60);
      var left = px, top = py;
      if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
      if (top + mh > window.innerHeight - 8) top = (anchorBtn ? r.top - mh - 6 : Math.max(8, py - mh - 8));
      menu.style.left = Math.max(8, left) + "px";
      menu.style.top = Math.max(8, top) + "px";
      menu.addEventListener("click", function (e) {
        var apply = e.target.closest(".bd-style-preset-apply");
        var del = e.target.closest(".bd-style-preset-del");
        var save = e.target.closest(".bd-style-preset-save-btn");
        if (apply) { self._applyStylePreset(apply.dataset.name); self._closeStyleLibrary(); }
        else if (del) { self._deleteStylePreset(del.dataset.name); self._showStyleLibrary(anchorBtn); }
        else if (save) {
          var inp = menu.querySelector(".bd-style-preset-name");
          var nm = inp ? inp.value : "";
          self._saveStylePreset(nm);
          self._closeStyleLibrary();
        }
      });
      if (backdrop.addEventListener) backdrop.addEventListener("click", function () { self._closeStyleLibrary(); });
    },

    _closeStyleLibrary: function () {
      var m = document.querySelector(".bd-style-library");
      if (m) m.remove();
      var b = document.querySelector(".bd-menu-backdrop");
      if (b) b.remove();
    },

    _alignSelection: function (mode) {
      var sel = this._selectedUnlocked();
      if (sel.length < 2) return;
      var minX = Infinity, minY = Infinity, maxR = -Infinity, maxB = -Infinity;
      sel.forEach(function (w) {
        minX = Math.min(minX, w.x); minY = Math.min(minY, w.y);
        maxR = Math.max(maxR, w.x + w.width); maxB = Math.max(maxB, w.y + w.height);
      });
      var midX = (minX + maxR) / 2, midY = (minY + maxB) / 2;
      /* Canvas-relative alignment (A2): align to the canvas edges / centre axes. */
      var cw = _baseCanvasW(), ch = _baseCanvasH();
      var cMidX = cw / 2, cMidY = ch / 2;
      sel.forEach(function (w) {
        if (mode === "left") w.x = Math.round(minX);
        else if (mode === "center") w.x = Math.round(midX - w.width / 2);
        else if (mode === "right") w.x = Math.round(maxR - w.width);
        else if (mode === "top") w.y = Math.round(minY);
        else if (mode === "middle") w.y = Math.round(midY - w.height / 2);
        else if (mode === "bottom") w.y = Math.round(maxB - w.height);
        else if (mode === "canvas-left") w.x = 0;
        else if (mode === "canvas-center") w.x = Math.round(cMidX - w.width / 2);
        else if (mode === "canvas-right") w.x = Math.round(cw - w.width);
        else if (mode === "canvas-top") w.y = 0;
        else if (mode === "canvas-middle") w.y = Math.round(cMidY - w.height / 2);
        else if (mode === "canvas-bottom") w.y = Math.round(ch - w.height);
        if (w.x < 0) w.x = 0;
        if (w.y < 0) w.y = 0;
      });
      this._commitTransform();
    },

    /* Equal-gap distribution (A2): space the selection so the gaps between
       consecutive widgets are identical, anchored on the first widget's leading
       edge. Falls back to the existing span-based distribution when widgets
       overlap (negative span). */
    _distributeSpacing: function (axis) {
      var sel = this._selectedUnlocked();
      if (sel.length < 3) return;
      var sorted = sel.slice().sort(function (a, b) {
        return (axis === "h" ? a.x : a.y) - (axis === "h" ? b.x : b.y);
      });
      var total = 0;
      sel.forEach(function (w) { total += (axis === "h" ? w.width : w.height); });
      var span = (axis === "h" ? sorted[sorted.length - 1].x : sorted[sorted.length - 1].y) -
                 (axis === "h" ? sorted[0].x : sorted[0].y);
      var gap = (span - total) / (sorted.length - 1);
      if (gap < 0) { this._distributeSelection(axis); return; }
      var cursor = axis === "h" ? sorted[0].x : sorted[0].y;
      sorted.forEach(function (w, i) {
        if (i === 0) return;
        if (axis === "h") w.x = Math.max(0, Math.round(cursor + w.width + gap));
        else w.y = Math.max(0, Math.round(cursor + w.height + gap));
        cursor += (axis === "h" ? w.width : w.height) + gap;
      });
      this._commitTransform();
    },

    _distributeSelection: function (axis) {
      var sel = this._selectedUnlocked();
      if (sel.length < 3) return;
      var sorted = sel.slice().sort(function (a, b) {
        return (axis === "h" ? a.x : a.y) - (axis === "h" ? b.x : b.y);
      });
      var total = 0;
      sel.forEach(function (w) { total += (axis === "h" ? w.width : w.height); });
      var span = (axis === "h" ? sorted[sorted.length - 1].x : sorted[sorted.length - 1].y) -
                 (axis === "h" ? sorted[0].x : sorted[0].y);
      var gap = (span - total) / (sorted.length - 1);
      var cursor = axis === "h" ? sorted[0].x : sorted[0].y;
      sorted.forEach(function (w, i) {
        if (i === 0) return;
        if (axis === "h") w.x = Math.max(0, Math.round(cursor + w.width));
        else w.y = Math.max(0, Math.round(cursor + w.height));
        cursor += (axis === "h" ? w.width : w.height) + gap;
      });
      this._commitTransform();
    },

    _matchSize: function (dim) {
      var sel = this._selectedUnlocked();
      if (sel.length < 2) return;
      var ref = _findWidget(_selectedId);
      if (!ref) return;
      sel.forEach(function (w) {
        if (dim === "w") w.width = ref.width;
        else if (dim === "h") w.height = ref.height;
        else { w.width = ref.width; w.height = ref.height; }
      });
      this._commitTransform();
    },

    _commitTransform: function () {
      this._initGrid();
      this._refreshHistory();
      this._refreshProps();
      this._refreshSelection();
    },

    _sendSelectedFront: function () {
      var sel = this._selectedUnlocked();
      if (sel.length === 0) return;
      this._initGrid();
      var maxZ = 0;
      _currentWidgets().forEach(function (x) { maxZ = Math.max(maxZ, x.zIndex || 0); });
      sel.forEach(function (w) { w.zIndex = ++maxZ; });
      this._initGrid();
      this._refreshSelection();
      this._refreshHistory();
    },

    _sendSelectedBack: function () {
      var sel = this._selectedUnlocked();
      if (sel.length === 0) return;
      this._initGrid();
      var minZ = Infinity;
      _currentWidgets().forEach(function (x) { minZ = Math.min(minZ, x.zIndex || 0); });
      var z = minZ - 1;
      sel.forEach(function (w) { w.zIndex = Math.max(1, z--); });
      this._initGrid();
      this._refreshSelection();
      this._refreshHistory();
    },

    /* ── Persistent grouping (A1) ──
       A group is a named set of widget ids stored in _dash.groups. Widgets carry
       a `group` field naming their group. Groups persist through save/reload and
       move/resize as a unit. */

    _groupIds: function (gid) {
      var g = (_dash.groups || {})[gid];
      return g && Array.isArray(g.members) ? g.members : [];
    },

    _groupOf: function (id) {
      var w = _findWidget(id);
      return w && w.group ? w.group : null;
    },

    _groupWidgets: function (gid) {
      var self = this;
      var ids = {};
      this._groupIds(gid).forEach(function (i) { ids[i] = true; });
      return _currentWidgets().filter(function (w) { return ids[w.id]; });
    },

    _groupBounds: function (gid) {
      var ws = this._groupWidgets(gid);
      if (ws.length === 0) return null;
      var minX = Infinity, minY = Infinity, maxR = -Infinity, maxB = -Infinity;
      ws.forEach(function (w) {
        minX = Math.min(minX, w.x); minY = Math.min(minY, w.y);
        maxR = Math.max(maxR, w.x + w.width); maxB = Math.max(maxB, w.y + w.height);
      });
      return { x: minX, y: minY, w: maxR - minX, h: maxB - minY };
    },

    _groupSelection: function () {
      var sel = this._selectedUnlocked();
      if (sel.length < 2) { App.warning("Select at least two widgets to group"); return; }
      if (!_dash.groups) _dash.groups = {};
      var gid = "g" + Date.now().toString(36) + "_" + (++_seq);
      _dash.groups[gid] = { id: gid, name: "Group " + Object.keys(_dash.groups).length, members: [] };
      var self = this;
      sel.forEach(function (w) {
        if (w.group && _dash.groups[w.group]) {
          var old = _dash.groups[w.group].members;
          var i = old.indexOf(w.id);
          if (i !== -1) old.splice(i, 1);
          if (old.length === 0) delete _dash.groups[w.group];
        }
        w.group = gid;
        _dash.groups[gid].members.push(w.id);
      });
      this._initGrid();
      this._refreshSelection();
      this._refreshHistory();
      this._markDirty();
      App.success("Grouped " + sel.length + " widgets");
    },

    _ungroupSelection: function () {
      var sel = this._selectedUnlocked();
      if (sel.length === 0) return;
      var touched = {};
      var self = this;
      sel.forEach(function (w) {
        if (!w.group) return;
        var gid = w.group;
        var g = _dash.groups && _dash.groups[gid];
        if (g) {
          var i = g.members.indexOf(w.id);
          if (i !== -1) g.members.splice(i, 1);
          if (g.members.length === 0) delete _dash.groups[gid];
        }
        delete w.group;
        touched[gid] = true;
      });
      this._initGrid();
      this._refreshSelection();
      this._refreshHistory();
      this._markDirty();
      App.success("Ungrouped selection");
    },

    _selectGroup: function (gid) {
      var ids = this._groupIds(gid);
      if (ids.length === 0) return;
      this._setSelection(ids);
    },

    /* Move every widget in a group by (dx, dy) as a rigid unit. */
    _translateGroup: function (gid, dx, dy) {
      var self = this;
      this._groupWidgets(gid).forEach(function (w) {
        w.x = Math.max(0, Math.round(w.x + dx));
        w.y = Math.max(0, Math.round(w.y + dy));
        self._positionWidget(w);
      });
    },

    /* Resize a group by scaling member positions/sizes about the group's top-left
       corner by (sx, sy) factors, keeping relative layout. */
    _scaleGroup: function (gid, sx, sy) {
      var self = this;
      var b = this._groupBounds(gid);
      if (!b) return;
      this._groupWidgets(gid).forEach(function (w) {
        w.x = Math.max(0, Math.round(b.x + (w.x - b.x) * sx));
        w.y = Math.max(0, Math.round(b.y + (w.y - b.y) * sy));
        w.width = Math.max(40, Math.round(w.width * sx));
        w.height = Math.max(30, Math.round(w.height * sy));
        self._positionWidget(w);
      });
    },

    /* Drop empty groups and clear stale `group` refs after widget deletion. */
    _pruneGroups: function () {
      if (!_dash.groups) return;
      var live = {};
      _currentWidgets().forEach(function (w) { live[w.id] = true; });
      var self = this;
      Object.keys(_dash.groups).forEach(function (gid) {
        var g = _dash.groups[gid];
        if (!g) return;
        g.members = g.members.filter(function (id) { return live[id]; });
        if (g.members.length === 0) delete _dash.groups[gid];
      });
      _currentWidgets().forEach(function (w) {
        if (w.group && !_dash.groups[w.group]) delete w.group;
      });
    },

    /* Smart-edge snapping: aligns the dragged widget's edges/centres with every
       other (non-group) widget, plus the canvas axes. Returns a translation and
       guide coordinates, or null when nothing is within the snap threshold. */
    _smartSnap: function (w, group) {
      var snapT = 6;
      var others = _currentWidgets().filter(function (x) {
        return x.id !== w.id && !group.some(function (g) { return g.id === x.id; });
      });
      var bestDX = null, bestGX = null;
      var cand = [];
      others.forEach(function (o) {
        [o.x, o.x + o.width / 2, o.x + o.width].forEach(function (peg) {
          [-1, 0, 1].forEach(function (k) {
            cand.push({ peg: peg, own: [w.x, w.x + w.width / 2, w.x + w.width][k + 1], g: peg });
          });
        });
      });
      cand.push({ peg: 0, own: w.x, g: 0 });
      /* A2: snap the widget's centre to the canvas centre axis. */
      var cMidX = _baseCanvasW() / 2;
      cand.push({ peg: cMidX, own: w.x + w.width / 2, g: cMidX });
      cand.forEach(function (c) {
        var d = c.peg - c.own;
        if (bestDX == null || Math.abs(d) < Math.abs(bestDX)) { bestDX = d; bestGX = c.g; }
      });
      var bestDY = null, bestGY = null;
      var candY = [];
      others.forEach(function (o) {
        [o.y, o.y + o.height / 2, o.y + o.height].forEach(function (peg) {
          [-1, 0, 1].forEach(function (k) {
            candY.push({ peg: peg, own: [w.y, w.y + w.height / 2, w.y + w.height][k + 1], g: peg });
          });
        });
      });
      candY.push({ peg: 0, own: w.y, g: 0 });
      var cMidY = _baseCanvasH() / 2;
      candY.push({ peg: cMidY, own: w.y + w.height / 2, g: cMidY });
      candY.forEach(function (c) {
        var d = c.peg - c.own;
        if (bestDY == null || Math.abs(d) < Math.abs(bestDY)) { bestDY = d; bestGY = c.g; }
      });
      if (bestDX != null && Math.abs(bestDX) > snapT) { bestDX = null; bestGX = null; }
      if (bestDY != null && Math.abs(bestDY) > snapT) { bestDY = null; bestGY = null; }
      if (bestDX == null && bestDY == null) return null;
      return { dx: bestDX || 0, dy: bestDY || 0, guides: { vx: bestGX, hy: bestGY } };
    },

    _drawGuides: function (guides) {
      this._clearGuides();
      var canvas = document.getElementById("bd-canvas");
      if (!canvas || !guides) return;
      if (guides.vx != null) {
        var v = document.createElement("div");
        v.className = "bd-guide bd-guide-v";
        v.style.left = guides.vx + "px";
        v.style.height = (parseFloat(canvas.style.height) || 1200) + "px";
        canvas.appendChild(v);
      }
      if (guides.hy != null) {
        var h = document.createElement("div");
        h.className = "bd-guide bd-guide-h";
        h.style.top = guides.hy + "px";
        h.style.width = (parseFloat(canvas.style.width) || 1600) + "px";
        canvas.appendChild(h);
      }
    },

    /* Snap a widget being resized so its moving edge(s) align to other widgets'
       edges / midpoints. Called with the cursor-driven geometry BEFORE _clamp
       rounding; returns deltas to apply + the guide positions (or null). */
    _snapResize: function (w, dir, nX, nY, nW, nH) {
      var snapT = 6;
      var others = _currentWidgets().filter(function (x) { return x.id !== w.id; });
      var res = { dx: 0, dy: 0, dw: 0, dh: 0, guides: {} };
      var bestX = null, bestY = null;
      if (dir.indexOf("e") !== -1) {
        var right = nX + nW;
        others.forEach(function (o) {
          [o.x, o.x + o.width / 2, o.x + o.width].forEach(function (peg) {
            var d = peg - right;
            if (bestX == null || Math.abs(d) < Math.abs(bestX)) { bestX = d; res.guides.vx = peg; }
          });
        });
        if (bestX == null || Math.abs(bestX) > snapT) { res.dw = 0; res.guides.vx = null; }
        else res.dw = bestX;
      } else if (dir.indexOf("w") !== -1) {
        var left = nX;
        others.forEach(function (o) {
          [o.x, o.x + o.width / 2, o.x + o.width].forEach(function (peg) {
            var d = peg - left;
            if (bestX == null || Math.abs(d) < Math.abs(bestX)) { bestX = d; res.guides.vx = peg; }
          });
        });
        if (bestX == null || Math.abs(bestX) > snapT) { res.dx = 0; res.guides.vx = null; }
        else { res.dx = bestX; res.dw = -bestX; }
      }
      if (dir.indexOf("s") !== -1) {
        var bottom = nY + nH;
        others.forEach(function (o) {
          [o.y, o.y + o.height / 2, o.y + o.height].forEach(function (peg) {
            var d = peg - bottom;
            if (bestY == null || Math.abs(d) < Math.abs(bestY)) { bestY = d; res.guides.hy = peg; }
          });
        });
        if (bestY == null || Math.abs(bestY) > snapT) { res.dh = 0; res.guides.hy = null; }
        else res.dh = bestY;
      } else if (dir.indexOf("n") !== -1) {
        var top = nY;
        others.forEach(function (o) {
          [o.y, o.y + o.height / 2, o.y + o.height].forEach(function (peg) {
            var d = peg - top;
            if (bestY == null || Math.abs(d) < Math.abs(bestY)) { bestY = d; res.guides.hy = peg; }
          });
        });
        if (bestY == null || Math.abs(bestY) > snapT) { res.dy = 0; res.guides.hy = null; }
        else { res.dy = bestY; res.dh = -bestY; }
      }
      if (!(res.dx || res.dy || res.dw || res.dh)) return null;
      return res;
    },

    _clearGuides: function () {
      document.querySelectorAll(".bd-guide").forEach(function (g) { g.remove(); });
    },

    /* ── Arrange flyout menu (align / distribute / copy / paste / z-order) ── */
    _showArrangeMenu: function (anchorBtn) {
      this._closeContextMenus();
      var n = _selectedIds.length;
      var dis = function (need) { return n < need ? ' disabled' : ''; };
      var countNote = n > 0
        ? '<div class="bd-arrange-count">' + n + (n === 1 ? ' widget selected' : ' widgets selected') + '</div>'
        : '<div class="bd-arrange-count">Nothing selected</div>';
      var menu = document.createElement("div");
      menu.className = "bd-arrange-menu";
      menu.innerHTML =
        countNote +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Align</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-left" title="Align left edges" ' + dis(2) + '>Left</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-center" title="Align horizontal centres" ' + dis(2) + '>Center</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-right" title="Align right edges" ' + dis(2) + '>Right</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Vertical</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-top" title="Align top edges" ' + dis(2) + '>Top</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-middle" title="Align vertical centres" ' + dis(2) + '>Middle</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-bottom" title="Align bottom edges" ' + dis(2) + '>Bottom</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Canvas</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="canvas-left" title="Align to canvas left edge" ' + dis(1) + '>Left</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="canvas-center" title="Centre on canvas horizontally" ' + dis(1) + '>Center</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="canvas-right" title="Align to canvas right edge" ' + dis(1) + '>Right</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Canvas</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="canvas-top" title="Align to canvas top edge" ' + dis(1) + '>Top</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="canvas-middle" title="Centre on canvas vertically" ' + dis(1) + '>Middle</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="canvas-bottom" title="Align to canvas bottom edge" ' + dis(1) + '>Bottom</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Distribute</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="dist-h" title="Even horizontal spacing" ' + dis(3) + '>Horizontal</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="dist-v" title="Even vertical spacing" ' + dis(3) + '>Vertical</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="dist-h-gap" title="Equal horizontal gaps" ' + dis(3) + '>Gaps H</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="dist-v-gap" title="Equal vertical gaps" ' + dis(3) + '>Gaps V</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Size</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="match-w" title="Match primary width" ' + dis(2) + '>Width</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="match-h" title="Match primary height" ' + dis(2) + '>Height</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Group</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="group" title="Group selection (Ctrl+G)" ' + dis(2) + '><i class="fas fa-object-group"></i> Group</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="ungroup" title="Ungroup selection (Ctrl+Shift+G)" ' + dis(1) + '><i class="fas fa-object-ungroup"></i> Ungroup</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Style</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="copy-style" title="Copy style (Ctrl+Shift+C)" ' + dis(1) + '><i class="fas fa-paintbrush"></i> Copy</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="paste-style" title="Paste style (Ctrl+Shift+V)" ' + dis(1) + '><i class="fas fa-paint-roller"></i> Paste</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="style-library" title="Style Library"><i class="fas fa-book"></i> Library</button>' +
        '</div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row">' +
          '<button class="btn btn-sm btn-ghost" data-act="copy" title="Copy (Ctrl+C)" ' + dis(1) + '><i class="fas fa-copy"></i> Copy</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="paste" title="Paste (Ctrl+V)"><i class="fas fa-paste"></i> Paste</button>' +
        '</div>' +
        '<div class="bd-arrange-row">' +
          '<button class="btn btn-sm btn-ghost" data-act="duplicate" title="Duplicate selection" ' + dis(1) + '><i class="fas fa-clone"></i> Duplicate</button>' +
          '<button class="btn btn-sm btn-ghost bd-arrange-danger" data-act="delete" title="Delete selection (Delete)" ' + dis(1) + '><i class="fas fa-trash"></i> Delete</button>' +
        '</div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row">' +
          '<button class="btn btn-sm btn-ghost" data-act="front" title="Bring to front" ' + dis(1) + '><i class="fas fa-arrow-up"></i> Front</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="back" title="Send to back" ' + dis(1) + '><i class="fas fa-arrow-down"></i> Back</button>' +
        '</div>';
      var backdrop = document.createElement("div");
      backdrop.className = "bd-menu-backdrop";
      document.body.appendChild(backdrop);
      document.body.appendChild(menu);
      var r = anchorBtn && anchorBtn.getBoundingClientRect();
      var mw = menu.offsetWidth || 220, mh = menu.offsetHeight || 320;
      var px = anchorBtn ? r.left : (_lastPointerCX != null ? _lastPointerCX : 40);
      var py = anchorBtn ? r.bottom + 6 : (_lastPointerCY != null ? _lastPointerCY : 60);
      var left = px, top = py;
      if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
      if (top + mh > window.innerHeight - 8) top = (anchorBtn ? r.top - mh - 6 : Math.max(8, py - mh - 8));
      menu.style.left = Math.max(8, left) + "px";
      menu.style.top = Math.max(8, top) + "px";
      var self = this;
      var acts = {
        "align-left": function () { self._alignSelection("left"); },
        "align-center": function () { self._alignSelection("center"); },
        "align-right": function () { self._alignSelection("right"); },
        "align-top": function () { self._alignSelection("top"); },
        "align-middle": function () { self._alignSelection("middle"); },
        "align-bottom": function () { self._alignSelection("bottom"); },
        "canvas-left": function () { self._alignSelection("canvas-left"); },
        "canvas-center": function () { self._alignSelection("canvas-center"); },
        "canvas-right": function () { self._alignSelection("canvas-right"); },
        "canvas-top": function () { self._alignSelection("canvas-top"); },
        "canvas-middle": function () { self._alignSelection("canvas-middle"); },
        "canvas-bottom": function () { self._alignSelection("canvas-bottom"); },
        "dist-h": function () { self._distributeSelection("h"); },
        "dist-v": function () { self._distributeSelection("v"); },
        "dist-h-gap": function () { self._distributeSpacing("h"); },
        "dist-v-gap": function () { self._distributeSpacing("v"); },
        "match-w": function () { self._matchSize("w"); },
        "match-h": function () { self._matchSize("h"); },
        "group": function () { self._groupSelection(); },
        "ungroup": function () { self._ungroupSelection(); },
        "copy-style": function () { self._copyStyle(); },
        "paste-style": function () { self._pasteStyle(); },
        "style-library": function () { self._showStyleLibrary(); },
        "copy": function () { self._copySelection(); },
        "paste": function () { self._pasteClipboard(); },
        "duplicate": function () { self._duplicateSelection(); },
        "delete": function () { self._deleteSelection(); },
        "front": function () { self._sendSelectedFront(); },
        "back": function () { self._sendSelectedBack(); }
      };
      menu.addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (!b || b.disabled) return;
        var fn = acts[b.dataset.act];
        if (fn) fn();
        self._closeArrangeMenu();
      });
      if (backdrop.addEventListener) backdrop.addEventListener("click", function () { self._closeArrangeMenu(); });
    },

    _closeArrangeMenu: function () {
      var m = document.querySelector(".bd-arrange-menu");
      if (m) m.remove();
      var b = document.querySelector(".bd-menu-backdrop");
      if (b) b.remove();
    },

    /* Overflow ("•••") menu in the header toolbar — lower-frequency actions live here */
    _showToolbarOverflowMenu: function (anchorBtn) {
      this._closeContextMenus();
      var self = this;
      var sizes = [["desktop", "Desktop · 1900×1080"], ["laptop", "Laptop · 1366×768"], ["tablet", "Tablet · 1024×768"], ["mobile", "Mobile · 390×844"], ["custom", "Custom…"]];
      var sizeOpts = sizes.map(function (s) {
        return '<option value="' + s[0] + '" ' + (_canvasSizeKey() === s[0] ? "selected" : "") + '>' + s[1] + '</option>';
      }).join("");
      var menu = document.createElement("div");
      menu.className = "bd-arrange-menu bd-overflow-menu";
      menu.innerHTML =
        '<div class="bd-arrange-row"><button class="btn btn-sm btn-ghost" data-act="gallery"><i class="fas fa-folder-open"></i> My Dashboards</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="templates"><i class="fas fa-bookmark"></i> Templates</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="bookmarks"><i class="fas fa-flag"></i> Bookmarks</button></div>' +
        '<div class="bd-arrange-row"><button class="btn btn-sm btn-ghost" data-act="export" data-feature="dashboard_export"><i class="fas fa-file-export"></i> Export…</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="theme"><i class="fas fa-palette"></i> Theme…</button></div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label" style="line-height:32px;">Canvas size</span>' +
          '<select class="bd-toolbar-select" id="bd-size-select" style="min-width:170px;">' + sizeOpts + '</select></div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row"><button class="btn btn-sm btn-ghost" data-act="settings"><i class="fas fa-sliders"></i> Settings…</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="shortcuts"><i class="fas fa-keyboard"></i> Shortcuts</button></div>' +
        '<div class="bd-arrange-row"><button class="btn btn-sm btn-ghost" data-act="drill" ' + (_drillStack.length > 0 ? '' : 'disabled style="opacity:0.4"') + '><i class="fas fa-arrow-up"></i> Drill Up</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="revert"><i class="fas fa-rotate-left"></i> Revert to last save</button></div>';
      var backdrop = document.createElement("div");
      backdrop.className = "bd-menu-backdrop";
      document.body.appendChild(backdrop);
      document.body.appendChild(menu);
      if (window.RBAC && window.RBAC.applyToNav) {
        try { window.RBAC.applyToNav(); } catch (e) { console.warn("Failed to apply RBAC gating:", e); }
      }
      var r = anchorBtn && anchorBtn.getBoundingClientRect();
      var px = r ? r.right - 230 : 40;
      var py = r ? r.bottom + 6 : 60;
      var left = px > window.innerWidth - 240 ? window.innerWidth - 240 : px;
      menu.style.left = Math.max(8, left) + "px";
      menu.style.top = (py > window.innerHeight - 260 ? r.top - 230 : py) + "px";
      var acts = {
        "gallery": function () { self._showGallery(); },
        "templates": function () { self._showTemplatesModal(); },
        "bookmarks": function () { self._showBookmarksModal(); },
        "export": function () { self._exportDash(); },
        "theme": function () { self._openThemePicker(); },
        "settings": function () { self._showSettings(); },
        "shortcuts": function () { self._showShortcuts(); },
        "drill": function () { if (_drillStack.length > 0) self._drillUp(); },
        "revert": function () { self._revertToSaved(); }
      };
      var sizeSel = menu.querySelector("#bd-size-select");
      sizeSel.addEventListener("change", function () { self._setCanvasSize(sizeSel.value); self._closeArrangeMenu(); });
      menu.addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (!b || b.disabled) return;
        var fn = acts[b.dataset.act];
        if (fn) fn();
        self._closeArrangeMenu();
      });
      backdrop.addEventListener("click", function () { self._closeArrangeMenu(); });
    },

    /* Close every open context/floating menu so menus never stack on each other */
    _closeContextMenus: function () {
      var wms = document.querySelectorAll(".ff-widget-menu");
      for (var i = 0; i < wms.length; i++) wms[i].remove();
      var m = document.querySelector(".bd-arrange-menu");
      if (m) m.remove();
      var b = document.querySelector(".bd-menu-backdrop");
      if (b) b.remove();
    },

    _selectAll: function () {
      var all = _currentWidgets().map(function (w) { return w.id; });
      if (!all.length) { App.warning("Nothing to select"); return; }
      this._setSelection(all);
      this._refreshProps();
    },

    /* Right-click on an empty canvas area opens a canvas context menu */
    _showCanvasMenu: function (clientX, clientY) {
      var self = this;
      this._closeContextMenus();
      var n = _selectedIds.length;
      var dis = function (need) { return n < need ? ' disabled' : ''; };
      var menu = document.createElement("div");
      menu.className = "bd-arrange-menu bd-canvas-menu";
      menu.innerHTML =
        '<div class="bd-arrange-count">' + (n ? (n + (n === 1 ? " widget selected" : " widgets selected")) : "Canvas") + '</div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row">' +
          '<button class="btn btn-sm btn-ghost" data-act="select-all" title="Select all widgets (Ctrl+A)"><i class="fas fa-square-check"></i> Select all</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="paste" title="Paste clipboard here (Ctrl+V)"><i class="fas fa-paste"></i> Paste here</button>' +
        '</div>' +
        '<div class="bd-arrange-row">' +
          '<button class="btn btn-sm btn-ghost" data-act="duplicate" title="Duplicate selection" ' + dis(1) + '><i class="fas fa-clone"></i> Duplicate</button>' +
          '<button class="btn btn-sm btn-ghost bd-arrange-danger" data-act="delete" title="Delete selection" ' + dis(1) + '><i class="fas fa-trash"></i> Delete</button>' +
        '</div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Align</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-left" ' + dis(2) + '>Left</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-center" ' + dis(2) + '>Center</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-right" ' + dis(2) + '>Right</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Vertical</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-top" ' + dis(2) + '>Top</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-middle" ' + dis(2) + '>Middle</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="align-bottom" ' + dis(2) + '>Bottom</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Distribute</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="dist-h" ' + dis(3) + '>Horizontal</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="dist-v" ' + dis(3) + '>Vertical</button>' +
        '</div>' +
        '<div class="bd-arrange-row"><span class="bd-arrange-label">Size</span>' +
          '<button class="btn btn-sm btn-ghost" data-act="match-w" ' + dis(2) + '>Width</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="match-h" ' + dis(2) + '>Height</button>' +
        '</div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row">' +
          '<button class="btn btn-sm btn-ghost" data-act="front" ' + dis(1) + '><i class="fas fa-arrow-up"></i> Front</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="back" ' + dis(1) + '><i class="fas fa-arrow-down"></i> Back</button>' +
        '</div>' +
        '<div class="bd-arrange-sep"></div>' +
        '<div class="bd-arrange-row">' +
          '<button class="btn btn-sm btn-secondary" data-act="add"><i class="fas fa-plus"></i> Add widget</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="arrange"><i class="fas fa-object-group"></i> Arrange</button>' +
        '</div>' +
        '<div class="bd-arrange-row">' +
          '<button class="btn btn-sm btn-ghost" data-act="templates"><i class="fas fa-bookmark"></i> Templates</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="bookmarks"><i class="fas fa-flag"></i> Bookmarks</button>' +
          '<button class="btn btn-sm btn-ghost" data-act="fit"><i class="fas fa-maximize"></i> Fit view</button>' +
        '</div>';
      var backdrop = document.createElement("div");
      backdrop.className = "bd-menu-backdrop";
      document.body.appendChild(backdrop);
      document.body.appendChild(menu);
      var mw = menu.offsetWidth || 240, mh = menu.offsetHeight || 360;
      var left = clientX, top = clientY;
      if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
      if (top + mh > window.innerHeight - 8) top = Math.max(8, clientY - mh - 8);
      menu.style.left = Math.max(8, left) + "px";
      menu.style.top = Math.max(8, top) + "px";
      var acts = {
        "select-all": function () { self._selectAll(); },
        "paste": function () { self._pasteClipboard(); },
        "duplicate": function () { self._duplicateSelection(); },
        "delete": function () { self._deleteSelection(); },
        "align-left": function () { self._alignSelection("left"); },
        "align-center": function () { self._alignSelection("center"); },
        "align-right": function () { self._alignSelection("right"); },
        "align-top": function () { self._alignSelection("top"); },
        "align-middle": function () { self._alignSelection("middle"); },
        "align-bottom": function () { self._alignSelection("bottom"); },
        "dist-h": function () { self._distributeSelection("h"); },
        "dist-v": function () { self._distributeSelection("v"); },
        "match-w": function () { self._matchSize("w"); },
        "match-h": function () { self._matchSize("h"); },
        "front": function () { self._sendSelectedFront(); },
        "back": function () { self._sendSelectedBack(); },
        "add": function () { self._showAddModal(); },
        "arrange": function () {
          var cm = document.querySelector(".bd-canvas-menu");
          if (cm) {
            var cRect = cm.getBoundingClientRect();
            self._showArrangeMenu({ getBoundingClientRect: function () { return cRect; } });
          } else {
            self._showArrangeMenu(null);
          }
        },
        "templates": function () { self._showTemplatesModal(); },
        "bookmarks": function () { self._showBookmarksModal(); },
        "fit": function () { self._fitZoom(); }
      };
      menu.addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (!b || b.disabled) return;
        var fn = acts[b.dataset.act];
        if (fn) {
          var keep = b.dataset.act === "arrange";
          fn();
          if (!keep) self._closeArrangeMenu();
        } else {
          self._closeArrangeMenu();
        }
      });
      if (backdrop.addEventListener) backdrop.addEventListener("click", function () { self._closeArrangeMenu(); });
    },

    _showFloatingToolbar: function (id) {
      this._hideFloatingToolbar();
      var el = document.querySelector('.ff-widget[data-id="' + id + '"]');
      if (!el) return;
      var w = _findWidget(id);
      if (!w || w.locked) return;
      var tb = document.createElement("div");
      tb.className = "ff-float-toolbar";
      tb.innerHTML =
        '<button data-act="size-s" title="Small size (S)">S</button>' +
        '<button data-act="size-m" title="Medium / default size (M)">M</button>' +
        '<button data-act="size-l" title="Large size (L)">L</button>' +
        '<button data-act="duplicate" title="Duplicate"><i class="fas fa-copy"></i></button>' +
        '<button data-act="lock" title="Lock"><i class="fas fa-lock"></i></button>' +
        '<button data-act="front" title="Bring forward"><i class="fas fa-arrow-up"></i></button>' +
        '<button data-act="back" title="Send backward"><i class="fas fa-arrow-down"></i></button>' +
        '<button data-act="edit" title="Settings"><i class="fas fa-cog"></i></button>' +
        '<button data-act="remove" class="ff-ft-danger" title="Delete"><i class="fas fa-trash"></i></button>';
      tb.dataset.id = id;
      document.querySelector(".bd-canvas").appendChild(tb);
      this._positionFloatingToolbar(tb);
      var self = this;
      tb.addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (!b) return;
        self._runMenuAction(b.dataset.act, id);
      });
    },

    _positionFloatingToolbar: function (tb) {
      var canvas = document.querySelector(".bd-canvas");
      var widgetEl = document.querySelector('.ff-widget[data-id="' + (tb.dataset.id) + '"]');
      if (!widgetEl) return;
      var canvasRect = canvas.getBoundingClientRect();
      var wRect = widgetEl.getBoundingClientRect();
      tb.style.left = (wRect.left - canvasRect.left) + "px";
      var above = wRect.top - canvasRect.top - 40;
      if (above < 6) {
        tb.style.top = Math.min(canvasRect.height - 34, Math.max(6, wRect.bottom - canvasRect.top + 8)) + "px";
      } else {
        tb.style.top = above + "px";
      }
    },

    _hideFloatingToolbar: function () {
      var tb = document.querySelector(".ff-float-toolbar");
      if (tb) tb.remove();
    },

    /* Default pixel size (catalogue-based) for a widget — used by the S/M/L size
       presets. Chart sub-types store their subtype in config.chartType. */
    _defaultSizeOf: function (w) {
      var t = w.type;
      if (t === "chart" && w.config && w.config.chartType && _CHART_SUBTYPES.indexOf(w.config.chartType) !== -1) t = w.config.chartType;
      var info = _lookup(t);
      var s = info.size || [6, 3];
      return _toPx(s[0], s[1]);
    },

    /* Apply an S/M/L size preset to the current selection (relative to each
       widget's default catalogue size; M restores the default). */
    _resizeWidgetSel: function (id, k) {
      var sel = _selectedIds.indexOf(id) !== -1 ? this._selectedUnlocked() : (function (self) { var w = _findWidget(id); return w && !w.locked ? [w] : []; })(this);
      if (sel.length === 0) return;
      var self = this;
      sel.forEach(function (w) {
        var base = self._defaultSizeOf(w);
        if (k === "s") { w.width = Math.max(80, Math.round(base.width * 0.66)); w.height = Math.max(60, Math.round(base.height * 0.66)); }
        else if (k === "l") { w.width = Math.round(base.width * 1.5); w.height = Math.round(base.height * 1.5); }
        else { w.width = base.width; w.height = base.height; }
      });
      this._initGrid();
      this._refreshHistory();
      this._refreshProps();
      this._refreshSelection();
    },

    /* Placeholder history hook — populated with real undo/redo in Stage 5 */
    _refreshHistory: function () {
      var snap = { pageId: _dash.pages[_dash.currentPage].id, widgets: JSON.parse(JSON.stringify(_currentWidgets())), groups: JSON.parse(JSON.stringify(_dash.groups || {})) };
      if (_lastSnap) _undoStack.push(_lastSnap);
      _lastSnap = snap;
      if (_undoStack.length > 200) _undoStack.shift();
      _redoStack.length = 0;
      this._onCanvasChanged();
      if (!_dirty) { _dirty = true; this._updateSaveBadge(); }
      this._persistLocalSnapshot();
    },

    /* Pixel-based free-form resize from any of the 8 handles */
    _bindResizeHandles: function () {
      var self = this;
      document.querySelectorAll(".ff-resize-handle").forEach(function (handle) {
        handle.addEventListener("mousedown", function (e) {
          e.preventDefault();
          e.stopPropagation();
          var id = handle.dataset.id;
          var dir = handle.dataset.resize || "se";
          var w = _findWidget(id);
          if (!w) return;
          var canvas = document.getElementById("bd-canvas");
          var canvasRect = canvas.getBoundingClientRect();
          var cx = (e.clientX - canvasRect.left) / _zoom;
          var cy = (e.clientY - canvasRect.top) / _zoom;
          var startX = w.x + w.width, startY = w.y + w.height;
          /* Group-aware resize: resizing a member of a persistent group scales the
             whole group about its top-left corner (PowerPoint-style). */
          var gid = (w.group && _dash.groups && _dash.groups[w.group]) ? w.group : null;
          var gBounds = gid ? self._groupBounds(gid) : null;
          var gStart = gBounds ? { x: gBounds.x, y: gBounds.y, w: gBounds.w, h: gBounds.h } : null;
          document.body.classList.add("bd-resizing");
          self._selectWidget(id, true);
          function onMove(ev) {
            var nx = (ev.clientX - canvasRect.left) / _zoom, ny = (ev.clientY - canvasRect.top) / _zoom;
            var newX = w.x, newY = w.y, newW = w.width, newH = w.height;
            var min = _minSizeFor(w);
            if (dir.indexOf("e") !== -1) newW = _clamp(nx - w.x, min.w, _maxW);
            if (dir.indexOf("w") !== -1) { newX = Math.min(startX - min.w, nx); newW = _clamp(startX - newX, min.w, _maxW); newX = startX - newW; }
            if (dir.indexOf("s") !== -1) newH = _clamp(ny - w.y, min.h, _maxH);
            if (dir.indexOf("n") !== -1) { newY = Math.min(startY - min.h, ny); newH = _clamp(startY - newY, min.h, _maxH); newY = startY - newH; }
            newX = Math.max(0, newX); newY = Math.max(0, newY);
            /* Alignment snapping for the moving edges; hold Ctrl/Alt to disable
               snapping (and guides) for pixel-accurate resizing. */
            if (!ev.ctrlKey && !ev.altKey) {
              var snapRes = self._snapResize(w, dir, newX, newY, newW, newH);
              if (snapRes) {
                self._drawGuides(snapRes.guides);
                newX += snapRes.dx; newY += snapRes.dy;
                newW += snapRes.dw; newH += snapRes.dh;
              } else {
                self._clearGuides();
              }
            } else {
              self._clearGuides();
            }
            if (gid && gStart) {
              /* Scale the whole group about its top-left corner. */
              var sx = (newW + (newX - gStart.x)) / gStart.w;
              var sy = (newH + (newY - gStart.y)) / gStart.h;
              sx = Math.max(0.2, sx); sy = Math.max(0.2, sy);
              self._scaleGroup(gid, sx, sy);
            } else {
              w.x = Math.round(newX); w.y = Math.round(newY);
              w.width = Math.round(newW); w.height = Math.round(newH);
              self._positionWidget(w);
            }
          }
          function onUp() {
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            document.body.classList.remove("bd-resizing");
            self._clearGuides();
            self._growCanvas();
            self._updateClipBadge();
            self._resizeWidget(id);
            if (w.config) w.config.userSized = true; /* respect manual sizing in auto-fit */
            self._refreshHistory();
            self._refreshProps();
          }
          document.addEventListener("mousemove", onMove);
          document.addEventListener("mouseup", onUp);
        });
      });
    },

    /* Free-form drag to move a widget anywhere on the canvas (multi-drag aware) */
    _bindMove: function () {
      var self = this;
      document.querySelectorAll(".ff-widget").forEach(function (el) {
        if (el.classList.contains("ff-locked")) return;
        el.addEventListener("mousedown", function (e) {
          if (e.target.closest(".ff-resize-handle") || e.target.closest(".ff-widget-btn") ||
              e.target.closest(".bd-page-link") || e.target.closest(".ff-text[contenteditable='true']") ||
              e.target.closest("button") || e.target.closest("a") || e.target.closest("select") ||
              e.target.closest("input") || e.target.closest("textarea")) return;
          var w = _findWidget(el.dataset.id);
          if (!w) return;
          var canvas = document.getElementById("bd-canvas");
          if (!canvas) return;
          var canvasRect = canvas.getBoundingClientRect();
          var startMX = e.clientX, startMY = e.clientY;
          /* Dragging an already-selected widget moves the whole unlocked selection.
             If the widget belongs to a persistent group, the whole group moves as a
             rigid unit (unless the user is dragging a multi-selection that spans
             beyond the group — then the selection wins). */
          var group = [];
          if (self._isSelected(w.id)) {
            group = _currentWidgets().filter(function (x) { return self._isSelected(x.id) && !x.locked; });
          } else if (w.group && _dash.groups && _dash.groups[w.group]) {
            group = self._groupWidgets(w.group).filter(function (x) { return !x.locked; });
          } else {
            group = [w];
          }
          var origMap = group.map(function (x) { return { x: x.x, y: x.y }; });
          var moved = false;
          function onMove(ev) {
            _lastPointerCX = ev.clientX; _lastPointerCY = ev.clientY;
            var dx = ev.clientX - startMX, dy = ev.clientY - startMY;
            if (!moved && (Math.abs(dx) + Math.abs(dy)) > 4) {
              moved = true;
              if (!self._isSelected(w.id)) self._selectWidget(w.id, true);
            }
            if (!moved) return;
            w.x = Math.max(0, Math.round(origMap[0].x + dx / _zoom));
            w.y = Math.max(0, Math.round(origMap[0].y + dy / _zoom));
            var dxF, dyF, snapRes;
            /* Hold Ctrl/Alt while dragging to disable snapping (guides + grid)
               for pixel-accurate placement, PowerPoint-style. */
            if (ev.ctrlKey || ev.altKey) {
              self._clearGuides();
              dxF = dx / _zoom; dyF = dy / _zoom;
            } else if ((snapRes = self._smartSnap(w, group))) {
              self._drawGuides(snapRes.guides);
              dxF = (w.x + snapRes.dx) - origMap[0].x;
              dyF = (w.y + snapRes.dy) - origMap[0].y;
            } else {
              self._clearGuides();
              var nx = Math.max(0, origMap[0].x + dx / _zoom);
              var ny = Math.max(0, origMap[0].y + dy / _zoom);
              if (_snap > 0) {
                nx = Math.round(nx / _snap) * _snap;
                ny = Math.round(ny / _snap) * _snap;
              }
              dxF = nx - origMap[0].x; dyF = ny - origMap[0].y;
            }
            group.forEach(function (g, i) {
              g.x = Math.max(0, Math.round(origMap[i].x + dxF));
              g.y = Math.max(0, Math.round(origMap[i].y + dyF));
              self._positionWidget(g);
            });
          }
          function onUp() {
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            self._clearGuides();
            if (moved) {
              self._dragJustMoved = w.id;
              self._growCanvas();
              self._updateClipBadge();
              self._refreshHistory();
              self._refreshProps();
            }
          }
          document.addEventListener("mousemove", onMove);
          document.addEventListener("mouseup", onUp);
        });
      });
    },

    /* Apply a widget's x/y/width/height/zIndex to its DOM element */
    _positionWidget: function (w) {
      var el = document.querySelector('.ff-widget[data-id="' + w.id + '"]');
      if (!el) return;
      el.style.left = w.x + "px";
      el.style.top = w.y + "px";
      el.style.width = w.width + "px";
      el.style.height = w.height + "px";
      el.style.zIndex = (w.zIndex || 0);
      this._growCanvas();
      _minimapRefreshThrottled();
      var tb = document.querySelector('.ff-float-toolbar[data-id="' + w.id + '"]');
      if (tb) this._positionFloatingToolbar(tb);
    },

    /* Grow the canvas when a widget approaches its edge (free-form infinite canvas).
       rAF-throttled: drag/resize events fire many times per frame; the layout math
       only has to run once per painted frame. */
_growCanvas: function () {
      if (_bdGrowRafPending) return;
      if (_dash && _dash.fixedCanvas) return;
      requestAnimationFrame(function () {
        _bdGrowRafPending = false;
        var canvas = document.getElementById("bd-canvas");
        if (!canvas) return;
        var maxRight = _baseCanvasW(), maxBottom = _baseCanvasH();
        _currentWidgets().forEach(function (w) {
          if (w.x + w.width > maxRight) maxRight = w.x + w.width;
          if (w.y + w.height > maxBottom) maxBottom = w.y + w.height;
        });
        var needW = Math.max(maxRight + 80, _canvasViewportW()), needH = maxBottom + 80;
        if (needW > canvas.offsetWidth) canvas.style.width = needW + "px";
        if (needH > canvas.offsetHeight) canvas.style.height = needH + "px";
      });
    },

    /* Fixed-canvas guardrail: when a widget extends past the canvas edge it is
       silently clipped, so surface a small badge naming the count instead. */
    _updateClipBadge: function () {
      var wrap = document.querySelector(".bd-canvas-wrap");
      if (!wrap) return;
      var badge = document.getElementById("bd-clip-badge");
      if (!_dash || !_dash.fixedCanvas || _dash.mode === "present") {
        if (badge) badge.remove();
        return;
      }
      var baseW = _baseCanvasW(), baseH = _baseCanvasH();
      var clipped = 0;
      _currentWidgets().forEach(function (w) {
        if (w.x + w.width > baseW || w.y + w.height > baseH) clipped++;
      });
      if (clipped === 0) {
        if (badge) badge.remove();
        return;
      }
      if (!badge) {
        badge = document.createElement("div");
        badge.id = "bd-clip-badge";
        badge.style.cssText = "position:absolute;top:8px;left:50%;transform:translateX(-50%);z-index:40;" +
          "background:rgba(245,158,11,.95);color:#1c1917;font-size:12px;font-weight:600;" +
          "padding:4px 12px;border-radius:999px;box-shadow:0 2px 8px rgba(0,0,0,.25);pointer-events:none;";
        wrap.appendChild(badge);
      }
      badge.textContent = clipped + " widget" + (clipped === 1 ? "" : "s") + " extend past the canvas edge — enlarge the canvas size or move them";
    },

    async _fetchAndRender(widget, cell, extraFilters) {
      try {
        var sql = _applyFilters(widget, extraFilters);
        var result = _queryCacheGet(sql);
        if (!result) {
          result = await API.biQuery(sql);
          /* A filter-wrapped query can fail when the grouped result has no raw
             filter column (e.g. Date on an aggregate table). Fall back to the
             unfiltered SQL — Power BI shows the visual unfiltered as well. */
          if ((!result || result.success === false) && Object.keys(_filters).length > 0 && sql !== (widget._origSql || (widget.config || {}).sql)) {
            try { result = await API.biQuery(widget._origSql || (widget.config || {}).sql) || result; } catch (e2) {}
          }
          /* Transient empty result (concurrent reads on the shared DuckDB connection
             can return no rows): one retry of the same SQL before painting an empty
             visual — applies to charts too, so a lost race can't show "No data". */
          if (result && result.success !== false && (!result.rows || !result.rows.length)) {
            try { result = await API.biQuery(sql) || result; } catch (e3) {}
          }
          if (result) _queryCacheSet(sql, result);
        }
        var rows = result && result.rows ? result.rows : (Array.isArray(result) ? result : []);
        var cols = result && result.columns ? result.columns : (rows.length ? Object.keys(rows[0]) : []);
        var c = widget.config;
        /* Measure-backed KPI: a failed or empty fetch (concurrent reads on the
           shared DuckDB connection can transiently return no rows) must never
           regress the displayed value — keep the last good result. */
        if (c.measureExpr && (!result || result.success === false || !rows.length)) {
          if (DashboardRenderer) DashboardRenderer.render(widget, cell);
          return;
        }
        /* Measure-backed KPI: resolve the registered measure against the
           canned time intelligence, label the current/prior period, and
           (re)arm the 12-month trend SQL for the sparkline. */
        if (c.measureExpr && window.BITimeIntel) {
          var mb = window.BITimeIntel.build(c.measureExpr, c.table || _dash.table, c.dateCol, c.timeIntel || "none");
          if (mb.mode && mb.mode !== "none") {
            c.periodLabel = mb.prev_label ? ("vs " + mb.prev_label) : "vs prior period";
          } else {
            c.periodLabel = "";
          }
          c.sparkSql = mb.spark_sql || "";
          if (!c.format || c.format === "auto") c.format = c.measureFormat || "auto";
          if (c.measureName && !c.label) c.label = c.measureName;
        }
        if (widget.type === "kpi") {
          /* config.value is a user override ("Leave blank to use SQL result");
             only fill it from the query when the user hasn't one. A NON-empty
             value produced by an EARLIER query must NOT freeze the widget:
             filters change the result, so refetch and re-fill unless the user
             explicitly locked a static value (config.valueIsManual). Measure-backed
             KPIs always take the query result (time-period switcher refetches). */
          if (c.measureExpr || c.valueIsManual !== true || c.value === undefined || c.value === null || c.value === "") {
            c.value = rows.length && rows[0] ? (rows[0].value || rows[0][Object.keys(rows[0])[0]] || 0) : 0;
          }
          /* Fetch sparkline data if sparkSql is configured */
          if (c.sparkSql) {
            try {
              var sparkResult = _queryCacheGet(c.sparkSql);
              if (!sparkResult) {
                sparkResult = await API.biQuery(c.sparkSql);
                if (sparkResult) _queryCacheSet(c.sparkSql, sparkResult);
              }
              var sparkRows = sparkResult && sparkResult.rows ? sparkResult.rows : (Array.isArray(sparkResult) ? sparkResult : []);
              if (sparkRows.length) {
                var sparkKey = Object.keys(sparkRows[0])[0];
                c.sparklineData = sparkRows.map(function (r) { return parseFloat(r[sparkKey] || 0); });
              }
            } catch (se) { /* sparkline fetch failed silently */ }
          }
        } else if (widget.type === "metriccard") {
          /* P3.8 — metric card: resync measure exprs from the registry, stash the
             value rows, then fetch the 12-month strip UNION (per-measure sparks). */
          var mcList = c.measures || [];
          mcList.forEach(function (m) {
            if (!m || !m.id) return;
            var reg = _measureById(m.id);
            if (reg) {
              m.expr = reg.expression;
              m.formula = reg.expression;
              if (reg.name && !m.label) m.label = reg.name;
              m.format = m.format || reg.format || "auto";
              if (reg.date_column) m.dateCol = reg.date_column;
            }
          });
          c._metricRows = rows.filter(function (r) { return r != null && r.label != null; })
            .map(function (r) { return { label: r.label, value: r.value, previous: r.previous }; });
          try {
            var mcWhere = "";
            try {
              var mcols = (App.state.tables[c.table || _dash.table] || {}).columns || [];
              mcWhere = _buildWhereClause(mcols, extraFilters) || "";
            } catch (ww) {}
            var mcBu = _metricCardSql(mcList, c.table || _dash.table, c.dateCol, mcWhere);
            if (mcBu && mcBu.sparkSql) {
              var sparkRes2 = _queryCacheGet(mcBu.sparkSql);
              if (!sparkRes2) {
                sparkRes2 = await API.biQuery(mcBu.sparkSql);
                if (sparkRes2) _queryCacheSet(mcBu.sparkSql, sparkRes2);
              }
              var srows2 = sparkRes2 && sparkRes2.rows ? sparkRes2.rows : (Array.isArray(sparkRes2) ? sparkRes2 : []);
              c._sparkRows = srows2;
            } else {
              c._sparkRows = [];
            }
          } catch (se) { c._sparkRows = []; }
        } else if (widget.type === "progress") {
          if (c.valueIsManual !== true && (c.value === undefined || c.value === null || c.value === "")) {
            c.value = rows.length && rows[0] ? parseFloat(rows[0].value || 0) : 0;
          }
          c.total = (typeof c.target === "number") ? c.target : (rows.length && rows[0] ? parseFloat(rows[0].total || rows[0].value || 100) : 100);
        } else if (widget.type === "table") {
          c.columns = cols;
          c.rows = rows;
          if (c.pivot && c.pivot.rowCol && c.pivot.valueCol) {
            c.pivotMatrix = _buildPivotMatrix(rows, c.pivot, cols);
          } else {
            c.pivotMatrix = null;
            delete c.pivotError;
          }
        } else if (widget.config.chartType === "gauge" || widget.type === "gauge") {
          if (c.value === undefined || c.value === null || c.value === "") {
            c.value = rows.length && rows[0] ? parseFloat(rows[0].value || rows[0][Object.keys(rows[0])[0]] || 0) : 0;
          }
          if (!c.label) c.label = widget.title;
        } else if (["radial", "watertank", "status", "comparison", "targetactual", "trendkpi", "percentagekpi", "prevkpi", "iconkpi", "donutkpi"].indexOf(widget.type) !== -1) {
          var r0 = rows[0] || {};
          var k0 = Object.keys(r0)[0] || "value";
          /* Measure-backed KPIs re-resolve on every fetch (period switcher etc.);
             plain ones also refill on filter changes unless the user locked a
             static value (config.valueIsManual). */
          var takeRow = !!c.measureExpr || c.valueIsManual !== true;
          if (takeRow || c.value === undefined || c.value === null || c.value === "") {
            c.value = r0.value != null ? parseFloat(r0.value) : parseFloat(r0[k0] || 0);
          }
          if (takeRow || c.comparisonValue === undefined || c.comparisonValue === null || c.comparisonValue === "") {
            c.comparisonValue = r0.comparison != null ? parseFloat(r0.comparison) : (r0.avg_val != null ? parseFloat(r0.avg_val) : 0);
          }
          if (takeRow || c.previousValue === undefined || c.previousValue === null || c.previousValue === "") {
            c.previousValue = r0.previous != null ? parseFloat(r0.previous) : c.comparisonValue;
          }
          if (r0.anchor != null && r0.anchor !== "") c.anchor = String(r0.anchor).slice(0, 10);
          if (takeRow || c.actual === undefined || c.actual === null || c.actual === "") {
            c.actual = r0.actual != null ? parseFloat(r0.actual) : c.value;
          }
          if (typeof c.target === "number" && !takeRow) {
            /* keep user-supplied target */
          } else {
            c.target = r0.target != null ? parseFloat(r0.target) : (c.comparisonValue || 100);
          }
          if (!c.label) c.label = widget.title;
        } else if (rows.length) {
          var xCol = c.xCol || cols[0] || "label";
          var yCol = c.yCol || (cols.length > 1 ? cols[1] : cols[0]);
          c.labels = rows.map(function (r) { return String(r[xCol] || r[cols[0]] || ""); });
          c.data = rows.map(function (r) { return parseFloat(r[yCol] || r[cols[1]] || 0); });
          /* Cross-filtering + tooltip pages need the REAL source column behind
             the label alias (SQL is `SELECT "product" AS label, ...` so cols[0]
             is just "label"). Resolve it once from the SQL and persist as
             c.dimension so _tooltipField()/click-to-filter target the actual
             table column. */
          if (!c.dimension) {
            var dimSrc = _resolveDimensionColumn(widget, cols);
            if (dimSrc) c.dimension = dimSrc;
          }
          // AI charts carry a series (color) column → pivot rows into one
          // dataset per series value, aligned on the x labels. Labels keep the
          // chronological order returned by the SQL (date trends stay intact).
          if (c.seriesCol) {
            var sLabels = [], sIndex = {}, sSeries = {};
            rows.forEach(function (r) {
              var lx = String(r.label != null ? r.label : r[xCol] != null ? r[xCol] : (r[cols[0]] != null ? r[cols[0]] : ""));
              if (!(lx in sIndex)) { sIndex[lx] = sLabels.length; sLabels.push(lx); }
              var sv = String(r.series != null ? r.series : (r[c.seriesCol] != null ? r[c.seriesCol] : ""));
              if (!sSeries[sv]) sSeries[sv] = sLabels.map(function () { return 0; });
              sSeries[sv][sIndex[lx]] = parseFloat(r.value != null ? r.value : (r[yCol] != null ? r[yCol] : 0)) || 0;
            });
            var sKeys = Object.keys(sSeries);
            if (sKeys.length > 8) {
              sKeys.sort(function (a, b) {
                var sa = sSeries[a].reduce(function (s, v) { return s + Math.abs(v); }, 0);
                var sb = sSeries[b].reduce(function (s, v) { return s + Math.abs(v); }, 0);
                return sb - sa;
              });
              sKeys = sKeys.slice(0, 8);
            }
            c.labels = sLabels.length ? sLabels : c.labels;
            c.datasets = sKeys.map(function (k) { return { label: k, data: sSeries[k] }; });
            if (c.datasets.length === 1) c.data = c.datasets[0].data;
          } else if (cols.length > 2) {
            c.datasets = cols.slice(1).map(function (col) {
              return { label: col, data: rows.map(function (r) { return parseFloat(r[col] || 0); }) };
            });
          }
          if (widget.config.chartType === "scatter" || widget.config.chartType === "bubble" || widget.type === "scatter" || widget.type === "bubble") {
            var xC = cols[0], yC = cols[1] || cols[0];
            c.dataPoints = rows.map(function (r) {
              return { x: parseFloat(r[xC] || 0), y: parseFloat(r[yC] || 0), r: 6 };
            });
          }
          if (widget.config.chartType === "sparkline" || widget.type === "sparkline") {
            c.data = c.data || [];
          }
          if ((widget.config.chartType === "heatmap" || widget.type === "heatmap") && cols.length >= 3) {
            c.xLabels = c.labels;
            c.yLabels = Array.from(new Set(rows.map(function (r) { return String(r[cols[1]] || ""); })));
            var matrix = [];
            c.yLabels.forEach(function (yVal) {
              var row = [];
              c.xLabels.forEach(function (xVal) {
                var match = rows.find(function (r) { return String(r[cols[0]]) === xVal && String(r[cols[1]]) === yVal; });
                row.push(match ? parseFloat(match[cols[2]] || 0) : 0);
              });
              matrix.push(row);
            });
            c.matrix = matrix;
          }
          if (widget.type === "histogram") {
            c.values = rows.map(function (r) { return parseFloat(r[cols[0]] || 0); });
          }
          if (widget.type === "boxplot") {
            c.values = rows.map(function (r) { return parseFloat(r[cols[1]] || r[cols[0]] || 0); });
            c.categories = rows.map(function (r) { return String(r[cols[0]] || ""); });
          }
          if (widget.type === "wordcloud") {
            c.words = rows.map(function (r) { return { text: String(r[cols[0]] || ""), value: parseFloat(r[cols[1]] || 1) }; });
          }
          if (widget.type === "bullet") {
            c.target = rows.map(function (r) { return parseFloat(r.target || r.avg_val || 0); });
          }
          if (widget.type === "sankey" || widget.type === "chord") {
            c.source = rows.map(function (r) { return String(r.source != null ? r.source : (r[cols[0]] != null ? r[cols[0]] : "")); });
            c.target = rows.map(function (r) { return String(r.target != null ? r.target : (r[cols[1]] != null ? r[cols[1]] : "")); });
            c.value = rows.map(function (r) { return parseFloat(r.value != null ? r.value : (r[cols[2]] != null ? r[cols[2]] : 0)); });
            c.flow = rows.map(function (r, i) {
              return { source: c.source[i], target: c.target[i], value: c.value[i] };
            });
            c.label = c.label || widget.title;
          }
          if (widget.type === "ridgeline" || widget.type === "beeswarm") {
            /* Group raw values per category label */
            var grp = {}, gOrder = [];
            rows.forEach(function (r) {
              var k = String(r.label != null ? r.label : (r[cols[0]] != null ? r[cols[0]] : ""));
              if (!(k in grp)) { grp[k] = []; gOrder.push(k); }
              var v = parseFloat(r.value != null ? r.value : (r[cols[1]] != null ? r[cols[1]] : 0));
              if (!isNaN(v)) grp[k].push(v);
            });
            c.labels = gOrder;
            c.groups = gOrder.map(function (k) { return { label: k, values: grp[k].slice().sort(function (a, b) { return a - b; }) }; });
          }
          if (widget.type === "venn") {
            c.vennA = String(c.labels[0] != null ? c.labels[0] : "A");
            c.vennB = String(c.labels[1] != null ? c.labels[1] : "B");
            c.vennSizes = [c.data[0] || 0, c.data[1] || 0, c.data[2] || 0];
          }
        }
        /* Measure-backed KPI sparkline: 12-month rolling trend from the
           calendar dimension (see BITimeIntel.build().spark_sql). */
        if (c.measureExpr && c.sparkSql) {
          try {
            var spRes = _queryCacheGet(c.sparkSql);
            if (!spRes) {
              spRes = await API.biQuery(c.sparkSql);
              if (spRes) _queryCacheSet(c.sparkSql, spRes);
            }
            var spRows = spRes && spRes.rows ? spRes.rows : (Array.isArray(spRes) ? spRes : []);
            if (spRows.length) {
              c.sparklineData = spRows.map(function (r) {
                return parseFloat(r.value != null ? r.value : r[Object.keys(r)[0]] || 0);
              });
            }
          } catch (se) { /* sparkline fetch failed silently */ }
        }
        if (DashboardRenderer) DashboardRenderer.render(widget, cell);
        /* Dashed placeholder instead of a blank chart when a chart query returns nothing.
           Skip when the widget already carries inline data (labels/data/datasets/matrix/
           dataPoints) — the render above drew a real chart, so don't wipe it out. */
        var _c = widget.config || {};
        var _hasInline = !!((_c.labels && _c.labels.length) || (_c.data && _c.data.length) || (_c.datasets && _c.datasets.length) || (_c.matrix && _c.matrix.length) || (_c.dataPoints && _c.dataPoints.length));
        if (!rows.length && !_hasInline && _c.chartType && ["kpi", "gauge", "progress", "radial", "bullet", "status", "table"].indexOf(widget.type) === -1) {
          cell.innerHTML = '<div class="ff-placeholder"><i class="fas fa-chart-line"></i><div>No data to display</div></div>';
        }
        this._maybeAttachAIRecreate(cell, widget);
      } catch (e) {
        cell.innerHTML = '<div class="text-center text-gray-400 text-xs py-4"><i class="fas fa-exclamation-triangle mr-1"></i>Error loading data</div>';
        this._maybeAttachAIRecreate(cell, widget);
      }
    },

    /* ── P3.9 — Report tooltip page (Power BI tooltip-page semantics) ──
       A widget tagged config.isTooltip acts as the report's tooltip page. When a
       chart data point is hovered it is re-rendered (SQL filtered by that point)
       inside a floating panel that follows the cursor, then hides on mouseleave. */
    _tooltipPageWidget: function () {
      var list = _currentWidgets();
      for (var i = 0; i < list.length; i++) {
        if (list[i].config && list[i].config.isTooltip) return list[i];
      }
      return null;
    },
    /* The filterable column a chart's hover label refers to (set from SQL cols by
       _fetchAndRender as xCol/dimension; used to build the tooltip-page filter). */
    _tooltipField: function (w) {
      var c = (w && w.config) || {};
      return c.xCol || c.dimension || c.categoryField || c.grandCol || null;
    },
    _tooltipLayerEl: function () {
      var layer = document.querySelector(".bd-tooltip-page");
      if (layer) return layer;
      layer = document.createElement("div");
      layer.className = "bd-tooltip-page";
      layer.setAttribute("data-tooltip-page", "1");
      document.body.appendChild(layer);
      return layer;
    },
    _tooltipCellEl: function () {
      var layer = this._tooltipLayerEl();
      var cell = layer.querySelector(".bd-tooltip-cell");
      if (!cell) {
        cell = document.createElement("div");
        cell.className = "bd-tooltip-cell";
        layer.appendChild(cell);
      }
      return cell;
    },
    _positionReportTooltip: function (x, y) {
      var layer = document.querySelector(".bd-tooltip-page");
      if (!layer || layer.style.display === "none") return;
      var offX = 14, offY = 14, margin = 8;
      var w = layer.offsetWidth, h = layer.offsetHeight;
      var vw = window.innerWidth || document.documentElement.clientWidth;
      var vh = window.innerHeight || document.documentElement.clientHeight;
      var left = (x || 0) + offX, top = (y || 0) + offY;
      if (w && left + w > vw - margin) left = Math.max(margin, (x || 0) - w - offX);
      if (h && top + h > vh - margin) top = Math.max(margin, (y || 0) - h - offY);
      layer.style.left = left + "px";
      layer.style.top = top + "px";
    },
    _showReportTooltip: function (filters, x, y) {
      var w = this._tooltipPageWidget();
      if (!w) return false;
      /* Clone the tagged widget so the on-canvas visual is never mutated by the
         hover re-fetch (the hovered value must not leak onto the report). */
      var tw = { id: w.id + "_tip", type: w.type, locked: true, x: 0, y: 0, width: w.width, height: w.height, title: w.title || "" };
      tw.config = JSON.parse(JSON.stringify(w.config || {}));
      if (w._origSql) tw._origSql = w._origSql;
      /* Always recompute on hover re-fetch: drop last-fetched KPI values so the
         value/previous guards take the query row for the hovered point. */
      delete tw.config.value;
      delete tw.config.previousValue;
      delete tw.config.comparisonValue;
      var layer = this._tooltipLayerEl();
      var cell = this._tooltipCellEl();
      _tooltipWidgetId = w.id;
      _tooltipHoverFilters = filters || null;
      layer.style.display = "block";
      this._positionReportTooltip(x, y);
      var self = this;
      var applyExtra = filters;
      /* Measure/metric-card pages inject the hover filter inside their own
         time-intel SQL generation (_applyFilters) — pass the filters through.
         Plain-sql pages: inject WHERE into the SQL here (wrapping an aggregate
         fails) and fetch without extra filters to avoid double filtering. */
      var isInjected = !!(tw.config.measureExpr || (tw.config.measures && tw.config.measures.length));
      if (!(isInjected && window.BITimeIntel)) {
        var cols = [];
        try { cols = (App.state.tables[tw.config.table || _dash.table] || {}).columns || []; } catch (e) {}
        var where = _buildWhereClause(cols, filters);
        if (where) tw.config.sql = _injectWhereInto(tw.config.sql || "", where);
        applyExtra = null;
      }
      this._fetchAndRender(tw, cell, applyExtra).then(function () {
        self._positionReportTooltip(x, y);
        self._renderTooltipDescriptions(w, cell);
      });
      return true;
    },
    _moveReportTooltip: function (x, y) {
      var layer = document.querySelector(".bd-tooltip-page");
      if (layer && layer.style.display !== "none") this._positionReportTooltip(x, y);
    },
    _hideReportTooltip: function () {
      var layer = document.querySelector(".bd-tooltip-page");
      if (layer) layer.style.display = "none";
      _tooltipHoverFilters = null;
    },
    /* P3.10 — surface measure descriptions inside the floating report tooltip.
       Resolved from the measure registry by the tooltip page's own measure refs
       (config.measureId for KPIs, config.measures[].id for the metric card). */
    _renderTooltipDescriptions: function (w, cell) {
      var descs = [];
      var c = (w && w.config) || {};
      if (c.measureId) {
        var mk = _measureById(c.measureId);
        if (mk && mk.description) descs.push(mk.description);
      }
      (c.measures || []).forEach(function (mc) {
        var md = mc && mc.id ? _measureById(mc.id) : null;
        if (md && md.description && descs.indexOf(md.description) === -1) descs.push(md.description);
      });
      var el = cell ? cell.querySelector(".bd-tooltip-desc") : null;
      if (descs.length) {
        var html = descs.map(function (d) {
          return '<div class="bd-tooltip-desc-line">' + _esc(d) + '</div>';
        }).join("");
        if (!el) {
          el = document.createElement("div");
          el.className = "bd-tooltip-desc";
          cell.appendChild(el);
        }
        el.innerHTML = html;
      } else if (el) {
        el.remove();
      }
    },
    /* Called by chart renderers on data-point hover to power the report tooltip
       page (a widget re-rendered filtered by the hovered value). */
    _chartHover: function (w, label, value, ev) {
      if (!this._tooltipPageWidget()) return;
      var field = this._tooltipField(w);
      if (!field || label == null || label === "") return;
      var f = {};
      f[field] = [String(label)];
      var x = ev && (ev.clientX != null ? ev.clientX : ev.pageX);
      var y = ev && (ev.clientY != null ? ev.clientY : ev.pageY);
      this._showReportTooltip(f, x, y);
    },

    /* ── Cross-filtering (Power BI): click a chart data point to filter every
       other widget on the page by that point's dimension value. Clicking the
       same point again clears the filter. Disabled per-widget via
       config.crossFilter === false. The filter flows through the same
       CrossFilterBus the slicer widgets use, so the filter bar, bookmarks,
       and per-page filter memory all stay in sync. ── */
    _chartClick: function (w, label, ev) {
      if (!w) return;
      var c = w.config || {};
      if (c.crossFilter === false) return;
      var field = this._tooltipField(w);
      if (!field) return;
      var val = (label == null || label === "") ? null : String(label);
      if (val === null) return;
      /* Toggle: clicking the already-filtered value clears it (Power BI semantics) */
      var cur = _filters[field];
      var isSame = cur !== undefined && JSON.stringify(cur) === JSON.stringify([val]);
      window.CrossFilterBus.dispatch(field, isSame ? null : [val], w.id);
      this._showStatusToast(isSame
        ? "Cleared filter: " + field
        : "Filtered by " + field + " = " + val);
    },
    /* True when the widget's dimension field has an active cross-filter value */
    _chartFilterActive: function (w, label) {
      var field = this._tooltipField(w);
      if (!field) return false;
      var cur = _filters[field];
      return cur !== undefined && JSON.stringify(cur) === JSON.stringify([String(label)]);
    },

    /* ── Geographic map visuals (interactive Leaflet.js maps) ── */

    // Built-in geocoding lookup for common places so maps render without external geocoding.
    // Each entry: normalized name -> [lat, lng]
    _PLACE_COORDS: (function () {
      var US = {
        "alabama":[32.8,-86.8],"alaska":[64.2,-152.5],"arizona":[34.0,-111.7],"arkansas":[34.8,-92.2],"california":[36.8,-119.8],
        "colorado":[39.0,-105.5],"connecticut":[41.6,-72.7],"delaware":[39.0,-75.5],"florida":[27.8,-81.7],"georgia":[32.6,-83.4],
        "hawaii":[21.3,-157.8],"idaho":[44.1,-114.2],"illinois":[40.0,-89.2],"indiana":[39.8,-86.3],"iowa":[42.0,-93.5],
        "kansas":[38.5,-98.0],"kentucky":[37.8,-85.6],"louisiana":[31.2,-91.9],"maine":[45.4,-68.0],"maryland":[39.0,-76.7],
        "massachusetts":[42.4,-71.1],"michigan":[44.3,-85.0],"minnesota":[46.4,-93.4],"mississippi":[32.7,-89.7],
        "missouri":[38.5,-92.3],"montana":[46.9,-110.0],"nebraska":[41.5,-99.9],"nevada":[38.8,-116.4],
        "new hampshire":[43.7,-71.5],"new jersey":[40.0,-74.5],"new mexico":[34.4,-106.1],"new york":[43.0,-75.0],
        "north carolina":[35.8,-79.0],"north dakota":[47.6,-100.3],"ohio":[40.3,-82.7],"oklahoma":[35.6,-96.9],
        "oregon":[44.1,-120.5],"pennsylvania":[41.0,-77.5],"rhode island":[41.7,-71.5],"south carolina":[33.9,-81.0],
        "south dakota":[44.4,-100.2],"tennessee":[35.7,-86.3],"texas":[31.5,-99.3],"utah":[40.2,-111.7],
        "vermont":[44.0,-72.7],"virginia":[37.5,-79.0],"washington":[47.2,-120.5],"west virginia":[38.6,-80.5],
        "wisconsin":[44.6,-89.4],"wyoming":[43.1,-107.5],
        "united states":[39.8,-98.6],"usa":[39.8,-98.6],"us":[39.8,-98.6],"america":[39.8,-98.6],"united states of america":[39.8,-98.6]
      };
      var WORLD = {
        "india":[20.6,78.9],"china":[35.9,104.2],"japan":[36.2,138.3],"south korea":[35.9,127.8],"north korea":[40.3,127.5],
        "indonesia":[-0.8,113.9],"thailand":[15.9,101.0],"vietnam":[14.1,108.3],"philippines":[12.9,121.8],"malaysia":[4.2,102.0],
        "singapore":[1.35,103.8],"myanmar":[21.9,95.9],"bangladesh":[23.7,90.4],"pakistan":[30.4,69.3],"afghanistan":[33.9,67.7],
        "nepal":[28.4,84.1],"sri lanka":[7.9,80.8],"iran":[32.4,53.7],"iraq":[33.2,43.7],"saudi arabia":[23.9,45.1],
        "united arab emirates":[23.4,53.8],"uae":[23.4,53.8],"qatar":[25.3,51.2],"kuwait":[29.3,47.5],"israel":[31.0,34.9],
        "turkey":[39.0,35.2],"russia":[61.5,105.3],"ukraine":[48.4,31.2],"poland":[51.9,19.1],"germany":[51.2,10.4],
        "france":[46.7,2.2],"spain":[40.4,-3.7],"portugal":[39.4,-8.2],"italy":[41.9,12.6],"greece":[39.1,21.8],
        "netherlands":[52.1,5.3],"belgium":[50.5,4.5],"switzerland":[46.8,8.2],"austria":[47.5,14.6],
        "united kingdom":[54.0,-2.4],"uk":[54.0,-2.4],"britain":[54.0,-2.4],"england":[52.4,-1.2],"scotland":[56.5,-4.2],
        "ireland":[53.4,-8.0],"sweden":[60.5,15.7],"norway":[61.9,8.6],"denmark":[56.3,10.0],"finland":[63.4,25.9],
        "iceland":[64.9,-18.6],"canada":[56.1,-106.3],"mexico":[23.6,-102.6],"brazil":[-14.2,-51.9],"argentina":[-34.0,-64.0],
        "chile":[-35.7,-71.5],"peru":[-9.2,-75.0],"colombia":[4.6,-74.0],"venezuela":[6.4,-66.6],"australia":[-25.0,134.0],
        "new zealand":[-40.9,174.9],"south africa":[-30.6,22.9],"egypt":[26.8,30.8],"nigeria":[9.1,8.7],
        "kenya":[-0.9,37.8],"morocco":[31.8,-7.1],"algeria":[28.0,1.7],"ethiopia":[9.1,40.5],"ghana":[7.9,-1.0],
        "africa":[0.0,20.0],"asia":[29.0,88.0],"europe":[50.0,10.0],"north america":[40.0,-100.0],
        "south america":[-14.2,-59.0],"oceania":[-22.7,140.0],"australasia":[-25.0,134.0],
        /* Business / sales dataset region aliases (so flow + dot + heat maps
           can resolve common report groupings that aren't sovereign states). */
        "asia pacific":[5.0,120.0],"apac":[5.0,120.0],"asia-pacific":[5.0,120.0],"asia paci c":[5.0,120.0],
        "latin america":[-12.0,-60.0],"latam":[-12.0,-60.0],"south america":[-14.2,-59.0],
        "middle east":[29.0,45.0],"middle east / africa":[20.0,15.0],"mea":[20.0,15.0],
        "emea":[48.0,10.0],"emea & apac":[5.0,120.0],"north america / caribbean":[25.0,-85.0],
        "usa":[39.0,-98.0],"u s a":[39.0,-98.0],"uae":[24.0,54.0],"saudi arabia":[23.9,45.1],
        "south korea":[36.0,127.8],"republic of korea":[36.0,127.8]
      };
      var out = {};
      Object.keys(US).forEach(function (k) { out[k] = US[k]; });
      Object.keys(WORLD).forEach(function (k) { out[k] = WORLD[k]; });
      return out;
    })(),

    _cityCoordsPromise: null,

    /* Lazy-load the bundled offline city dictionary (/js/data/cities.json) and
       merge it into _PLACE_COORDS so point/bubble/dot/flow maps can resolve city
       names with zero API keys. Cached after first successful load. */
    _loadCityCoords: function () {
      var self = this;
      if (this._cityCoordsPromise) return this._cityCoordsPromise;
      // Eagerly-available global (script-tag path) avoids a fetch round-trip.
      if (window.__BD_CITIES__ && !this._PLACE_COORDS._cityMerged) {
        var pre = window.__BD_CITIES__;
        Object.keys(pre).forEach(function (k) { self._PLACE_COORDS[k] = pre[k]; });
        self._PLACE_COORDS._cityMerged = true;
        self._cityCoordsPromise = Promise.resolve();
        return self._cityCoordsPromise;
      }
      this._cityCoordsPromise = fetch("/js/data/cities.json", { method: "GET" })
        .then(function (r) { if (!r.ok) throw new Error("cities unavailable"); return r.json(); })
        .then(function (j) {
          Object.keys(j || {}).forEach(function (k) { self._PLACE_COORDS[k] = j[k]; });
          self._PLACE_COORDS._cityMerged = true;
          return j;
        })
        .catch(function (e) { self._cityCoordsPromise = null; /* allow retry */ return null; });
      return this._cityCoordsPromise;
    },

    _geoPoint: function (name, lat, lng) {
      // Prefer exact lat/lng values from the row
      if (lat != null && lng != null && isFinite(parseFloat(lat)) && isFinite(parseFloat(lng)))
        return [parseFloat(lat), parseFloat(lng)];
      if (name == null) return null;
      var pc = this._PLACE_COORDS || {};
      var key = String(name).toLowerCase().trim().replace(/[_\s]+/g, " ").replace(/[\.']/g, "").trim();
      // Try full name, then without trailing qualifiers like "state"/"region"/"province"
      if (pc[key]) return pc[key];
      var bare = key.replace(/\b(state|province|region|county|district|territory)\b/g, "").trim();
      if (pc[bare]) return pc[bare];
      return null;
    },

    /* Take the aggregated region -> value data and place it as points with real coordinates:
       uses lat/lng columns when present in the query, else the built-in place dictionary. */
    _attachCoordBuffers: function (c, rows, geoCol, latCol, lngCol) {
      var labels = c.geoLabels || [], values = c.geoData || [];
      var coords = [], unresolved = [];
      for (var i = 0; i < labels.length; i++) {
        var row = null;
        for (var k = 0; k < rows.length; k++) {
          if (String(rows[k][geoCol] != null ? rows[k][geoCol] : "").trim().toLowerCase() === labels[i]) { row = rows[k]; break; }
        }
        var lat = row && latCol != null ? row[latCol] : null;
        var lng = row && lngCol != null ? row[lngCol] : null;
        var pt = this._geoPoint(labels[i], lat, lng);
        if (pt) coords[i] = { lat: pt[0], lng: pt[1] };
        else unresolved.push(labels[i]);
      }
      c.geoCoords = coords;
      c.geoUnresolved = unresolved;
      return c;
    },

    /* ── Shape Map (Power BI-style choropleth) internals ──
       All boundary GeoJSON is bundled locally (web/js/data/*.geojson) and
       served keyless + offline — no external geojson/tile API is required. */

    _SHAPE_SETS: {
      "world":        { file: "js/data/world-countries.geojson", keyProps: ["name", "iso_a2", "iso_a3"], label: "World countries" },
      "us-states":    { file: "js/data/us-states.geojson",       keyProps: ["name", "code"], label: "US states" },
      "uk-counties":  { file: "js/data/uk-counties.geojson",     keyProps: ["name"], label: "UK counties" },
      "india-states": { file: "js/data/india-states.geojson",    keyProps: ["name", "code"], label: "India states" },
      "brazil-states":{ file: "js/data/brazil-states.geojson",   keyProps: ["name", "code"], label: "Brazil states" },
      "us-counties":  { file: "js/data/us-counties.geojson",     keyProps: ["name", "fips"], label: "US counties" },
      "eu-nuts2":     { file: "js/data/eu-nuts2.geojson",        keyProps: ["name", "code"], label: "EU NUTS-2 regions" },
      "china-provinces": { file: "js/data/china-provinces.geojson", keyProps: ["name", "code"], label: "China provinces" },
      "japan-prefectures": { file: "js/data/japan-prefectures.geojson", keyProps: ["name", "code"], label: "Japan prefectures" }
    },
    _shapeCache: {},
    _SHAPE_ALIASES: {
      "usa": "united states", "us": "united states", "u s": "united states", "america": "united states",
      "united states of america": "united states",
      "uk": "united kingdom", "u k": "united kingdom", "gb": "united kingdom", "great britain": "united kingdom", "britain": "united kingdom",
      "russia": "russian federation", "uae": "united arab emirates", "drc": "democratic republic of the congo",
      "ivory coast": "cote d ivoire", "south korea": "republic of korea", "north korea": "democratic peoples republic of korea",
      "czech republic": "czechia", "macedonia": "north macedonia", "swaziland": "eswatini", "burma": "myanmar",
      "holland": "netherlands", "trinidad": "trinidad and tobago", "laos": "lao peoples democratic republic",
      "syria": "syrian arab republic", "bosnia": "bosnia and herzegovina", "congo": "republic of the congo",
      "moldova": "republic of moldova", "tanzania": "united republic of tanzania", "the gambia": "gambia",
      "east timor": "timor leste", "cabo verde": "cape verde", "brunei": "brunei darussalam",
      "palestine": "west bank and gaza", "gaza": "west bank and gaza", "bolivia": "bolivia"
    },
    _normRegionKey: function (s) {
      s = String(s == null ? "" : s).trim();
      if (!s) return "";
      s = s.toLowerCase();
      try { s = s.normalize("NFD").replace(/[\u0300-\u036f]/g, ""); } catch (e) {}
      s = s.replace(/[^a-z0-9\s'.]+/g, " ").replace(/\s+/g, " ").trim();
      s = s.replace(/\b(the)\s+/g, "").trim();
      s = s.replace(/\s+(state|province|region|county|district|territory|prefecture|island)\s*$/, "").trim();
      return s;
    },
    _indexShapes: function (gj) {
      var self = this;
      var idx = {};
      (gj.features || []).forEach(function (f, i) {
        f.__fi = i;
        var p = f.properties || {};
        var keys = [];
        var base = p.name != null ? self._normRegionKey(p.name) : "";
        if (base) keys.push(base);
        if (p.iso_a2 != null) keys.push(self._normRegionKey(p.iso_a2));
        if (p.iso_a3 != null) keys.push(self._normRegionKey(p.iso_a3));
        if (p.code != null) keys.push(self._normRegionKey(p.code));
        if (p.name_state != null) keys.push(self._normRegionKey(p.name_state));
        if (base) keys.push(base + " county");
        keys.forEach(function (k) {
          if (idx[k] == null) idx[k] = i;
        });
        keys.forEach(function (k) {
          var noApx = k.replace(/['']/g, "");
          if (noApx && noApx !== k && idx[noApx] == null) idx[noApx] = i;
        });
      });
      return idx;
    },
    _loadShapeSet: function (id) {
      var self = this;
      var rec = self._SHAPE_SETS[id] || self._SHAPE_SETS["world"];
      if (self._shapeCache[rec.file]) return Promise.resolve(self._shapeCache[rec.file]);
      return fetch("/" + rec.file)
        .then(function (r) { if (!r.ok) throw new Error("shape set " + id + " unavailable"); return r.json(); })
        .then(function (j) { self._shapeCache[rec.file] = j; return j; });
    },
    _resolveShapeSet: async function (entries, geoCol, desired) {
      var self = this;
      if (desired && desired !== "auto") {
        var gj = await self._loadShapeSet(desired);
        return { id: desired, gj: gj, idx: self._indexShapes(gj) };
      }
      var geo = String(geoCol || "").toLowerCase();
      var cands;
      if (geo.indexOf("county") !== -1 || geo.indexOf("shire") !== -1) cands = ["us-counties", "uk-counties", "us-states", "world"];
      else if (geo.indexOf("state") !== -1 || geo.indexOf("province") !== -1 || geo.indexOf("territory") !== -1
        || geo.indexOf("region") !== -1) cands = ["us-states", "india-states", "brazil-states", "china-provinces", "us-counties", "uk-counties", "world"];
      else if (geo.indexOf("prefecture") !== -1 || geo.indexOf("province") !== -1) cands = ["japan-prefectures", "china-provinces", "world", "us-states"];
      else cands = ["world", "us-states", "uk-counties", "india-states", "brazil-states", "china-provinces", "japan-prefectures", "eu-nuts2", "us-counties"];
      for (var i = 0; i < cands.length; i++) {
        var gj2 = await self._loadShapeSet(cands[i]);
        var idx2 = self._indexShapes(gj2);
        var matched = 0;
        entries.forEach(function (en) {
          var keys = [en.key, en.key.replace(/['']/g, "")];
          var alias = self._SHAPE_ALIASES[en.key];
          if (alias) keys.push(alias);
          for (var k = 0; k < keys.length; k++) if (idx2[keys[k]] != null) { matched++; break; }
        });
        if (matched > 0) return { id: cands[i], gj: gj2, idx: idx2 };
      }
      var gjf = await self._loadShapeSet("world");
      return { id: "world", gj: gjf, idx: self._indexShapes(gjf) };
    },

    async _fetchMapAndRender(widget, cell) {
      try {
        // Ensure the bundled offline city dictionary is merged into _PLACE_COORDS
        // so any map type can resolve city names (keyless + offline).
        await this._loadCityCoords();
        // Capture the render generation at dispatch; if the cell was re-created
        // while the query was in flight, skip rendering into the detached cell.
        var gen = cell._bdMapToken || 0;
        var alive = function () { return cell.isConnected && gen === (cell._bdMapToken || 0); };
        var sql = _applyFilters(widget);
        var result = _queryCacheGet(sql);
        if (!result) {
          result = await API.biQuery(sql);
          if (result) _queryCacheSet(sql, result);
        }
        if (!alive()) return;
        var rows = result && result.rows ? result.rows : (Array.isArray(result) ? result : []);
        var c = widget.config || {};
        if (!rows.length) {
          c.geoLabels = []; c.geoData = []; c.geoCoords = [];
          if (widget.type === "shapemap") { this._renderShapeMap(widget, cell, [], null, null); return; }
          this._renderLeafletMap(widget, cell);
          return;
        }
        var colSet = Object.keys(rows[0] || {});
        var isNum = function (v) { return typeof v === "number" || (!isNaN(parseFloat(v)) && /^\s*-?[\d.eE+]+/.test(String(v))); };
        // Locate explicit lat/lng columns if the query exposes them
        var lowCol = {};
        colSet.forEach(function (x) { lowCol[x.toLowerCase()] = x; });
        var latCol = c.latField && lowCol[c.latField.toLowerCase()] ? lowCol[c.latField.toLowerCase()]
          : (lowCol["lat"] || lowCol["latitude"]);
        var lngCol = c.lngField && lowCol[c.lngField.toLowerCase()] ? lowCol[c.lngField.toLowerCase()]
          : (lowCol["lng"] || lowCol["lon"] || lowCol["long"] || lowCol["longitude"]);
        // Compute a per-column "geo-ness" score so we pick an actual geographic field,
        // not the first arbitrary text column (e.g. a sales-person name).
        var GEO_HINTS = ["country","region","state","city","territory","geo","location","market","area","province","district","lat","long","lng","zip","postal","place","address"];
        var nonNum = colSet.filter(function (x) { return !isNum(rows[0][x]) && x !== "value"; });
        var score = function (col) {
          var low = col.toLowerCase().replace(/[_\s]/g, "");
          var s = 0;
          GEO_HINTS.forEach(function (h) { if (low.indexOf(h) !== -1) s++; });
          return s;
        };
        var bestGeo = nonNum.slice().sort(function (a, b) { return score(b) - score(a); })[0];
        var geoCol = (c.geoField && colSet.indexOf(c.geoField) !== -1) ? c.geoField
          : (nonNum.length && score(bestGeo) > 0 ? bestGeo : (colSet.find(function (x) { return !isNum(rows[0][x]) && x !== "value"; }) || "label"));
        // Detect measure column: explicit, else first numeric (excluding the geo col), else "value"
        var measCol = (c.measureField && colSet.indexOf(c.measureField) !== -1) ? c.measureField
          : (colSet.find(function (x) { return x !== geoCol && isNum(rows[0][x]); }) || "value");
        if (!c.geoField) c.geoField = geoCol;
        if (!c.measureField) c.measureField = measCol;
        if (latCol && !c.latField) c.latField = latCol;
        if (lngCol && !c.lngField) c.lngField = lngCol;

        var lookup = {};
        rows.forEach(function (r) {
          var key = String(r[geoCol] != null ? r[geoCol] : "").trim().toLowerCase();
          if (!key) return;
          var v = parseFloat(r[measCol] != null ? r[measCol] : 0);
          lookup[key] = (lookup[key] || 0) + v;
        });
        c.geoLabels = Object.keys(lookup);
        c.geoData = c.geoLabels.map(function (k) { return lookup[k]; });
        if (widget.type === "shapemap") { this._renderShapeMap(widget, cell, rows, geoCol, measCol); return; }
        this._attachCoordBuffers(c, rows, geoCol, latCol, lngCol);
        if (widget.type === "glmap") { this._renderMapLibreMap(widget, cell); return; }
        if (widget.type === "dotmap") { this._renderDotMap(widget, cell); return; }
        if (widget.type === "heatmapmap") { this._renderHeatmapMap(widget, cell); return; }
        if (widget.type === "flowmap") { this._renderFlowMap(widget, cell, rows); return; }
        this._renderLeafletMap(widget, cell);
      } catch (e) {
        cell.innerHTML = '<div class="text-center text-gray-400 text-xs py-4"><i class="fas fa-exclamation-triangle mr-1"></i>Error loading map data</div>';
      }
    },

    _renderLeafletMap: function (widget, cell) {
      var c = widget.config || {};
      var labels = c.geoLabels || [];
      var values = c.geoData || [];
      var coords = c.geoCoords || [];
      if (!labels.length) {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-map-location-dot"></i><div>No geo data</div></div>';
        return;
      }
      if (typeof L === "undefined") {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-map-location-dot"></i><div>Leaflet not loaded (no internet / missing vendor script)</div></div>';
        return;
      }

      cell.innerHTML = '<div class="bd-leaflet-host" style="width:100%;height:100%;min-height:220px;"></div>';
      var host = cell.querySelector(".bd-leaflet-host");
      var onLng = widget.type === "geomap"; // markers for "Marker Map"
      var byType = widget.type === "countrymap";

      /* ── Theme resolution: palette for markers + light/dark basemap ── */
      var themeOpts = _aiThemeOpts();
      var themeBag = themeOpts.theme || {};
      var palette = Array.isArray(themeOpts.palette) && themeOpts.palette.length ? themeOpts.palette.slice() : null;
      var cssBg = "";
      try {
        cssBg = getComputedStyle(document.documentElement).getPropertyValue("--color-card-bg").trim() ||
          getComputedStyle(document.documentElement).getPropertyValue("--color-content-bg").trim();
      } catch (e2) {}
      var dark = _isDarkHex(themeBag.cardBg || themeBag.background) ||
        (cssBg && _isDarkHex(cssBg));

      // Keyless basemap (OpenStreetMap tiles — no API key ever required).
      // The professional light/dark look is done in CSS (subtle de-saturation,
      // and an invert-style dark treatment), so no third-party tile API key is
      // needed on any network.
      var tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        subdomains: "abc",
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      });

      var map = L.map(host, {
        zoomControl: true,
        attributionControl: true,
        zoomAnimation: false,
        fadeAnimation: true,
        zoomSnap: 0.5
      });
      tiles.addTo(map);
      host.classList.add(dark ? "bd-map-host-dark" : "bd-map-host-light");

      // Build the point list, skipping labels with no coordinates
      var max = 1;
      values.forEach(function (v) { if (v > max) max = v; });
      var total = 0;
      values.forEach(function (v) { total += v; });
      total = Math.max(total, max);
      var pts = [], unresolved = 0;
      for (var i = 0; i < labels.length; i++) {
        var pt = coords[i];
        if (!pt) unresolved++;
        pts.push({ label: labels[i], value: values[i], lat: pt ? pt.lat : null, lng: pt ? pt.lng : null });
      }
      var placed = pts.filter(function (p) { return p.lat != null && p.lng != null; });
      if (!placed.length) {
        /* Region values aren't geocodable (e.g. "Region A/B/C") — fall back to a
           horizontal bar chart of the same aggregation instead of a dead map. */
        if ((widget.type === "countrymap" || widget.type === "geomap") && labels.length && window.DashboardRenderer && window.Charts) {
          var fc = Object.assign({}, c, {
            chartType: "horizontalbar",
            labels: labels,
            data: values,
            label: widget.title || c.label || "By Region"
          });
          window.DashboardRenderer.render({ type: "chart", id: widget.id, title: widget.title, config: fc }, cell);
          return;
        }
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-earth-americas"></i><div>' +
          _esc('No coordinates found. Use columns named lat/lng, or known region names (countries, US states).', true) + '</div></div>';
        return;
      }
      // Rank placed points (highest value first) for the tooltip leaderboard line
      placed.sort(function (a, b) { return b.value - a.value; });

      // Choose initial center + zoom from the placed points
      var latSum = 0, lngSum = 0;
      placed.forEach(function (p) {
        latSum += p.lat; lngSum += p.lng;
      });
      var center = [latSum / placed.length, lngSum / placed.length];
      var spread = 0;
      placed.forEach(function (p) {
        var d = Math.sqrt(Math.pow(p.lat - center[0], 2) + Math.pow(p.lng - center[1], 2));
        if (d > spread) spread = d;
      });
      var zoom = spread > 60 ? 2 : spread > 30 ? 3 : spread > 15 ? 4 : 5;
      if (placed.length === 1) zoom = 6;

      map.setView(center, Math.max(1, zoom));

      // Actual geographic bounds so the map fits exactly
      var latArr = placed.map(function (p) { return p.lat; });
      var lngArr = placed.map(function (p) { return p.lng; });
      var fit = L.latLngBounds(
        [Math.min.apply(null, latArr), Math.min.apply(null, lngArr)],
        [Math.max.apply(null, latArr), Math.max.apply(null, lngArr)]
      );

      /* ── Professional markers: halo + core, palette-tinted, ratio-proportional ── */
      var acc = themeOpts.accent || "#2563eb";
      var rampHex = function (t) {
        t = Math.max(0, Math.min(1, t));
        if (palette && palette.length > 1) {
          var seg = t * (palette.length - 1);
          var idx = Math.min(Math.floor(seg), palette.length - 2);
          return _mixHex(palette[idx], palette[idx + 1], seg - idx);
        }
        return _interpColor(t);
      };
      var fmtShare = function (v) { return (100 * v / total) < 0.05 ? "<0.1%" : (100 * v / total).toFixed(1) + "%"; };

      placed.forEach(function (p, rank) {
        var t = p.value / max;
        var baseR = byType ? (5 + 22 * Math.sqrt(t)) : (5 + 8 * Math.sqrt(t));
        var fill = rampHex(t);
        var haloPad = byType ? 8 : 5;
        // Soft glow halo under the marker (drawn first = underneath)
        L.circleMarker([p.lat, p.lng], {
          radius: baseR + haloPad,
          stroke: false,
          fillColor: fill,
          fillOpacity: dark ? 0.28 : 0.18
        }).addTo(map);
        var core = L.circleMarker([p.lat, p.lng], {
          radius: baseR,
          color: dark ? "rgba(15,23,42,0.9)" : "#ffffff",
          weight: dark ? 1.2 : 1.8,
          fillColor: fill,
          fillOpacity: 0.92
        }).addTo(map);
        core.on("mouseover", function () { core.setStyle({ weight: dark ? 2.2 : 2.6, fillOpacity: 1 }); });
        core.on("mouseout", function () { core.setStyle({ weight: dark ? 1.2 : 1.8, fillOpacity: 0.92 }); });
        var tip = '<div class="bd-map-tip-wrap"><div class="bd-map-tip-rank">#' + (rank + 1) + ' by value</div>' +
          '<div class="bd-map-tip-label">' + _esc(p.label) + '</div>' +
          '<div class="bd-map-tip-val" style="color:' + rampHex(t) + '">' + fmtNum(p.value) + '</div>' +
          '<div class="bd-map-tip-share">' + fmtShare(p.value) + ' of total</div></div>';
        core.bindTooltip(tip, { sticky: true, direction: "top", offset: [0, -6], className: "bd-map-tip" });
      });

      /* ── Scale bar (bottom-left) ── */
      L.control.scale({ metric: true, imperial: false, position: "bottomleft", maxWidth: 120 }).addTo(map);

      /* ── Value legend (bottom-right) ── */
      var legend = L.control({ position: "bottomright" });
      legend.onAdd = function () {
        var div = L.DomUtil.create("div", "bd-map-legend");
        var stops = palette && palette.length ? palette.slice(0, Math.min(palette.length, 6))
          : ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];
        var gradPos = stops.map(function (col, i) { return col + " " + Math.round(100 * i / Math.max(1, stops.length - 1)) + "%"; }).join(",");
        div.innerHTML =
          '<div class="bd-map-legend-title">Value scale</div>' +
          '<div class="bd-map-legend-strip" style="background:linear-gradient(90deg,' + gradPos + ')"></div>' +
          '<div class="bd-map-legend-row"><span>' + fmtNum(placed[placed.length - 1].value) + '</span><span>' + fmtNum(max) + '</span></div>' +
          '<div class="bd-map-legend-note">' + placed.length + ' of ' + labels.length + ' regions</div>';
        return div;
      };
      legend.addTo(map);

      if (unresolved > 0) {
        var info = L.control({ position: "topleft" });
        info.onAdd = function () {
          var div = L.DomUtil.create("div", "bd-map-info");
          div.innerHTML = '<i class="fas fa-circle-exclamation"></i> ' + unresolved + ' region(s) without coordinates not plotted';
          return div;
        };
        info.addTo(map);
      }

      // Fit bounds if multiple, else keep the zoomed center
      if (placed.length > 1) map.fitBounds(fit, { padding: [24, 24], maxZoom: 12 });

      // Force correct sizing when the widget finishes layout / is re-shown
      var aliveMap = function () { return map && map._container && map._container.isConnected; };
      var sizing = function () {
        if (!aliveMap()) return;
        setTimeout(function () { if (aliveMap()) { map.invalidateSize(); } }, 60);
      };
      sizing();
      setTimeout(sizing, 400);
      try {
        var ro = new ResizeObserver(function () { if (aliveMap()) map.invalidateSize(); });
        ro.observe(host);
        host._bdResizeObserver = ro;
      } catch (e) {}
      // Dispose the map when the widget cell is re-rendered or removed
      if (host && host.isConnected) {
        cell._bdMap = map;
      } else {
        try { map.remove(); } catch (e2) {}
      }
    },

    /* ── GL Map (mapcn.dev-style): MapLibre GL vector map with CARTO
       basemaps, value-proportional circle markers, hover tooltips and
       click popups. Keyless — free CARTO/OpenFreeMap raster+vector tiles. */
    _renderMapLibreMap: function (widget, cell) {
      var c = widget.config || {};
      var labels = c.geoLabels || [];
      var values = c.geoData || [];
      var coords = c.geoCoords || [];
      if (!labels.length) {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-map"></i><div>No geo data</div></div>';
        return;
      }
      if (typeof maplibregl === "undefined") {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-map"></i><div>MapLibre GL not loaded (missing vendor script)</div></div>';
        return;
      }

      /* Keyless basemap styles (mapcn defaults: free CARTO basemaps). */
      var STYLES = {
        default:     "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
        positron:    "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        darkmatter:  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        osm:         "https://tiles.openfreemap.org/styles/bright",
        blank:       { version: 8, sources: {}, layers: [] }
      };
      var styleKey = c.glBasemap || "default";
      var styleUrl = STYLES[styleKey] || STYLES.default;

      /* Theme resolution: palette ramp for markers, dark-aware basemap. */
      var themeOpts = _aiThemeOpts();
      var themeBag = themeOpts.theme || {};
      var palette = Array.isArray(themeOpts.palette) && themeOpts.palette.length ? themeOpts.palette.slice() : null;
      var cssBg = "";
      try {
        cssBg = getComputedStyle(document.documentElement).getPropertyValue("--color-card-bg").trim() ||
          getComputedStyle(document.documentElement).getPropertyValue("--color-content-bg").trim();
      } catch (e2) {}
      var dark = _isDarkHex(themeBag.cardBg || themeBag.background) || (cssBg && _isDarkHex(cssBg));
      if (styleKey === "default" && dark) styleUrl = STYLES.darkmatter;

      cell.innerHTML = '<div class="bd-glmap-host" style="width:100%;height:100%;min-height:220px;"></div>';
      var host = cell.querySelector(".bd-glmap-host");

      /* Build the placed point list (labels with resolved coordinates). */
      var max = 1, total = 0;
      values.forEach(function (v) { if (v > max) max = v; total += Math.max(v, 0); });
      total = Math.max(total, max);
      var pts = [], unresolved = 0;
      for (var i = 0; i < labels.length; i++) {
        var pt = coords[i];
        if (!pt) unresolved++;
        else pts.push({ label: labels[i], value: values[i], lat: pt.lat, lng: pt.lng });
      }
      if (!pts.length) {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-map"></i><div>' +
          _esc('No coordinates found. Use columns named lat/lng, or known region names (countries, US states).', true) + '</div></div>';
        return;
      }
      pts.sort(function (a, b) { return b.value - a.value; });

      /* Center + zoom from the placed points. */
      var latSum = 0, lngSum = 0;
      pts.forEach(function (p) { latSum += p.lat; lngSum += p.lng; });
      var center = [lngSum / pts.length, latSum / pts.length]; // MapLibre: [lng, lat]
      var spread = 0;
      pts.forEach(function (p) {
        var d = Math.sqrt(Math.pow(p.lat - center[1], 2) + Math.pow(p.lng - center[0], 2));
        if (d > spread) spread = d;
      });
      var zoom = spread > 60 ? 1 : spread > 30 ? 2 : spread > 15 ? 3 : spread > 5 ? 4 : 5;
      if (pts.length === 1) zoom = 8;

      var map = new maplibregl.Map({
        container: host,
        style: styleUrl,
        center: center,
        zoom: zoom,
        pitch: Math.max(0, Math.min(60, parseFloat(c.glPitch) || 0)),
        attributionControl: true
      });
      map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
      try { map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left"); } catch (e3) {}

      var sizeScale = Math.max(0.2, Math.min(1.5, parseFloat(c.bubbleSize) || 0.5));
      var acc = themeOpts.accent || "#2563eb";
      var rampHex = function (t) {
        t = Math.max(0, Math.min(1, t));
        if (palette && palette.length > 1) {
          var seg = t * (palette.length - 1);
          var idx = Math.min(Math.floor(seg), palette.length - 2);
          return _mixHex(palette[idx], palette[idx + 1], seg - idx);
        }
        return _interpColor(t);
      };
      var fmtShare = function (v) { return (100 * v / total) < 0.05 ? "<0.1%" : (100 * v / total).toFixed(1) + "%"; };

      map.on("load", function () {
        /* GeoJSON source with per-point value + color properties. */
        var features = pts.map(function (p) {
          var t = p.value / max;
          return {
            type: "Feature",
            geometry: { type: "Point", coordinates: [p.lng, p.lat] },
            properties: { label: p.label, value: p.value, color: rampHex(t), ratio: t }
          };
        });
        map.addSource("bd-gl-points", {
          type: "geojson",
          data: { type: "FeatureCollection", features: features }
        });

        /* Soft glow halo layer underneath (mapcn marker look). */
        map.addLayer({
          id: "bd-gl-halo",
          type: "circle",
          source: "bd-gl-points",
          paint: {
            "circle-color": ["get", "color"],
            "circle-opacity": dark ? 0.25 : 0.15,
            "circle-radius": ["interpolate", ["linear"], ["get", "ratio"], 0, 10 * sizeScale, 1, 34 * sizeScale]
          }
        });
        /* Core marker circle with white/dark stroke. */
        map.addLayer({
          id: "bd-gl-core",
          type: "circle",
          source: "bd-gl-points",
          paint: {
            "circle-color": ["get", "color"],
            "circle-opacity": 0.92,
            "circle-stroke-color": dark ? "rgba(15,23,42,0.9)" : "#ffffff",
            "circle-stroke-width": 1.6,
            "circle-radius": ["interpolate", ["linear"], ["get", "ratio"], 0, 6 * sizeScale, 1, 22 * sizeScale]
          }
        });

        /* Hover tooltip (mapcn-style popup card). */
        var popup = new maplibregl.Popup({
          closeButton: false, closeOnClick: false,
          offset: 12, maxWidth: "240px"
        });
        var tipHtml = function (props, rank) {
          return '<div class="bd-map-tip-wrap"><div class="bd-map-tip-rank">#' + (rank + 1) + ' by value</div>' +
            '<div class="bd-map-tip-label">' + _esc(props.label) + '</div>' +
            '<div class="bd-map-tip-val" style="color:' + props.color + '">' + fmtNum(props.value) + '</div>' +
            '<div class="bd-map-tip-share">' + fmtShare(props.value) + ' of total</div></div>';
        };
        var rankOf = {};
        pts.forEach(function (p, i) { rankOf[p.label] = i; });
        map.on("mouseenter", "bd-gl-core", function (e) {
          map.getCanvas().style.cursor = "pointer";
          var f = e.features && e.features[0];
          if (!f) return;
          var props = f.properties;
          popup.setLngLat(e.lngLat).setHTML(tipHtml(props, rankOf[props.label] != null ? rankOf[props.label] : 0)).addTo(map);
        });
        map.on("mouseleave", "bd-gl-core", function () {
          map.getCanvas().style.cursor = "";
          popup.remove();
        });
        if (c.glShowPopups !== false) {
          map.on("click", "bd-gl-core", function (e) {
            var f = e.features && e.features[0];
            if (!f) return;
            var props = f.properties;
            var coordinates = f.geometry.coordinates.slice();
            while (Math.abs(e.lngLat.lng - coordinates[0]) > 180) {
              coordinates[0] += e.lngLat.lng > coordinates[0] ? 360 : -360;
            }
            new maplibregl.Popup({ offset: 12, maxWidth: "240px" })
              .setLngLat(coordinates)
              .setHTML(tipHtml(props, rankOf[props.label] != null ? rankOf[props.label] : 0))
              .addTo(map);
          });
        }

        /* Fit bounds to the placed points. */
        if (pts.length > 1) {
          var lngs = pts.map(function (p) { return p.lng; });
          var lats = pts.map(function (p) { return p.lat; });
          var bounds = new maplibregl.LngLatBounds(
            [Math.min.apply(null, lngs), Math.min.apply(null, lats)],
            [Math.max.apply(null, lngs), Math.max.apply(null, lats)]
          );
          try { map.fitBounds(bounds, { padding: 32, maxZoom: 10, duration: 0 }); } catch (e4) {}
        }

        /* Value legend (bottom-right, DOM overlay). */
        var legend = document.createElement("div");
        legend.className = "bd-map-legend bd-glmap-legend";
        var stops = palette && palette.length ? palette.slice(0, Math.min(palette.length, 6))
          : ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];
        var gradPos = stops.map(function (col, i) { return col + " " + Math.round(100 * i / Math.max(1, stops.length - 1)) + "%"; }).join(",");
        legend.innerHTML =
          '<div class="bd-map-legend-title">Value scale</div>' +
          '<div class="bd-map-legend-strip" style="background:linear-gradient(90deg,' + gradPos + ')"></div>' +
          '<div class="bd-map-legend-row"><span>' + fmtNum(pts[pts.length - 1].value) + '</span><span>' + fmtNum(max) + '</span></div>' +
          '<div class="bd-map-legend-note">' + pts.length + ' of ' + labels.length + ' regions</div>';
        host.appendChild(legend);

        if (unresolved > 0) {
          var info = document.createElement("div");
          info.className = "bd-map-info bd-glmap-info";
          info.innerHTML = '<i class="fas fa-circle-exclamation"></i> ' + unresolved + ' region(s) without coordinates not plotted';
          host.appendChild(info);
        }
      });

      /* Force correct sizing when the widget finishes layout / is re-shown. */
      var aliveMap = function () { return map && map.getContainer() && map.getContainer().isConnected; };
      var sizing = function () {
        if (!aliveMap()) return;
        setTimeout(function () { if (aliveMap()) map.resize(); }, 60);
      };
      sizing();
      setTimeout(sizing, 400);
      try {
        var ro = new ResizeObserver(function () { if (aliveMap()) map.resize(); });
        ro.observe(host);
        host._bdResizeObserver = ro;
      } catch (e) {}
      /* Dispose the GL map when the widget cell is re-rendered or removed.
         Removing while the style/tiles are still loading makes MapLibre abort
         its in-flight fetches, which logs a console 'AbortError' — harmless
         but noisy. Wait for style load (bounded) before removing. */
      var _disposeGl = function () {
        try { map.remove(); } catch (e5) {}
      };
      var _disposeWhenReady = function () {
        var tries = 0;
        var tick = function () {
          tries++;
          var loaded = false;
          try { loaded = map.isStyleLoaded(); } catch (e6) { loaded = true; }
          if (loaded || tries > 40) { _disposeGl(); return; }   // cap ≈ 4s
          setTimeout(tick, 100);
        };
        tick();
      };
      if (host && host.isConnected) {
        cell._bdMap = { remove: _disposeWhenReady, stop: function () {} };
        cell._bdMap._gl = map;
      } else {
        _disposeWhenReady();
      }
    },

    /* Render a Power BI-style Shape Map: filled regions on a sequential color
       ramp; regions with 0 / missing data render neutral gray. */
    async _renderShapeMap(widget, cell, rows, geoCol, measCol) {
      var self = this;
      var c = widget.config || {};
      if (typeof L === "undefined") {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-map-location-dot"></i><div>Leaflet not loaded</div></div>';
        return;
      }
      var gen = cell._bdMapToken || 0;
      var alive = function () { return cell.isConnected && gen === (cell._bdMapToken || 0); };

      // Aggregate the raw region labels (keeps original case for display).
      var by = {}, order = [];
      (rows || []).forEach(function (r) {
        var raw = String(r[geoCol] != null ? r[geoCol] : "");
        var key = self._normRegionKey(raw);
        if (!key) return;
        var v = parseFloat(r[measCol] != null ? r[measCol] : 0);
        if (isNaN(v)) v = 0;
        if (by[key] == null) { by[key] = { raw: raw, key: key, value: 0 }; order.push(key); }
        by[key].value += v;
      });
      var entries = order.map(function (k) { return by[k]; });
      if (!entries.length) {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-draw-polygon"></i><div>No region data</div></div>';
        return;
      }

      // Load boundaries (bundled GeoJSON, cached) and pick the best-matching set.
      var resolved;
      try {
        resolved = await this._resolveShapeSet(entries, geoCol, c.shapeSet);
      } catch (e2) {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-map-location-dot"></i><div>Shape data unavailable</div></div>';
        return;
      }
      if (!alive()) return;

      var idx = resolved.idx;
      var feats = resolved.gj.features;
      var valByFeature = {}, rawOf = {}, matched = 0, unmatched = [];
      entries.forEach(function (en) {
        var node = idx[en.key];
        if (node == null) {
          var alias = self._SHAPE_ALIASES[en.key];
          if (alias && idx[alias] != null) node = idx[alias];
        }
        if (node == null) {
          var noApx = en.key.replace(/['']/g, "");
          if (idx[noApx] != null) node = idx[noApx];
        }
        if (node != null) {
          valByFeature[node] = (valByFeature[node] || 0) + en.value;
          rawOf[node] = en.raw;
          matched++;
        } else unmatched.push(en.raw);
      });

      var max = 1, total = 0;
      Object.keys(valByFeature).forEach(function (fi) {
        var v = valByFeature[fi];
        if (v > max) max = v;
        total += v;
      });

      cell.innerHTML = '<div class="bd-leaflet-host" style="width:100%;height:100%;min-height:220px;"></div>';
      var host = cell.querySelector(".bd-leaflet-host");

      var themeOpts = _aiThemeOpts();
      var themeBag = themeOpts.theme || {};
      var palette = Array.isArray(themeOpts.palette) && themeOpts.palette.length ? themeOpts.palette.slice() : null;
      var cssBg = "";
      try {
        cssBg = getComputedStyle(document.documentElement).getPropertyValue("--color-card-bg").trim() ||
          getComputedStyle(document.documentElement).getPropertyValue("--color-content-bg").trim();
      } catch (e4) {}
      var dark = _isDarkHex(themeBag.cardBg || themeBag.background) ||
        (cssBg && _isDarkHex(cssBg));

      var tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        subdomains: "abc", maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      });
      var map = L.map(host, { zoomControl: true, attributionControl: true, zoomAnimation: false, fadeAnimation: true, zoomSnap: 0.5 });
      tiles.addTo(map);
      host.classList.add(dark ? "bd-map-host-dark" : "bd-map-host-light");

      var rampHex = function (t) {
        t = Math.max(0, Math.min(1, t));
        if (palette && palette.length > 1) {
          var seg = t * (palette.length - 1);
          var idx2 = Math.min(Math.floor(seg), palette.length - 2);
          return _mixHex(palette[idx2], palette[idx2 + 1], seg - idx2);
        }
        return _interpColor(t);
      };
      // Diverging ramp: neutral in the middle, cool low, warm high.
      var divHex = function (t) { // t in [-1, 1]
        var neg = (dark ? "#7c3aed" : "#2563eb"); // below median
        var neu = dark ? "#334155" : "#e2e8f0";   // ~median
        var pos = (dark ? "#b45309" : "#b91c1c"); // above median
        t = Math.max(-1, Math.min(1, t));
        if (t === 0) return neu;
        if (t < 0) return _mixHex(neu, neg, -t);
        return _mixHex(neu, pos, t);
      };
      var mode = c.shapeMode || "linear";
      // Build a fill-color function per mode and, for the legend, a color-swatch spec.
      var values = [];
      Object.keys(valByFeature).forEach(function (fi) { values.push(valByFeature[fi]); });
      values.sort(function (a, b) { return a - b; });
      var fillFor, legStops, legTitle;
      if (mode === "quantile") {
        var NB = 5, breaks = [], seen = [];
        for (var bi = 0; bi < NB; bi++) breaks.push(values[Math.min(values.length - 1, Math.floor((bi + 1) * values.length / NB))]);
        for (var bi2 = 0; bi2 < NB; bi2++) if (seen.indexOf(breaks[bi2]) === -1) seen.push(breaks[bi2]);
        var bucketOf = function (v) {
          for (var x = 0; x < NB; x++) if (v <= breaks[x]) return x;
          return NB - 1;
        };
        var quantCols = seen.map(function (b, i) { return rampHex(seen.length > 1 ? i / (seen.length - 1) : 0); });
        fillFor = function (v) { return quantCols[bucketOf(v)]; };
        legStops = quantCols.map(function (col, i) { return { color: col, label: (i === 0 ? "≤ " : "") + fmtNum(seen[i] || 0) }; });
        legTitle = "Quantile buckets";
      } else if (mode === "center") {
        var med = values.length ? values[Math.floor(values.length / 2)] : (max || 1);
        var divHi = (max - med) || 1, divLo = med || 1;
        fillFor = function (v) {
          if (v === med) return divHex(0);
          var t = v < med ? -Math.max(0.15, (med - v) / divLo)
                          : Math.max(0.15, (v - med) / divHi);
          return divHex(t);
        };
        legStops = [{ color: divHex(-1), label: "Low" }, { color: divHex(0), label: "Median " + fmtNum(med) }, { color: divHex(1), label: "High" }];
        legTitle = "Center-out (vs median)";
      } else {
        fillFor = function (v) { return rampHex(v / (max || 1)); };
        legStops = null; legTitle = "Value scale";
      }
      var fmtShare = function (v) { return (100 * v / total) < 0.05 ? "<0.1%" : (100 * v / total).toFixed(1) + "%"; };
      var noDataFill = dark ? "#475569" : "#cbd5e1";
      var borderCol = dark ? "rgba(15,23,42,0.85)" : "rgba(255,255,255,0.9)";
      var noDataBorder = dark ? "#64748b" : "#e2e8f0";
      var strokeW = dark ? 0.8 : 0.7;

      valByFeature._max = max; // keep max reachable from handlers without closure churn

      var geo = L.geoJSON(resolved.gj, {
        style: function (feature) {
          var v = valByFeature[feature.__fi];
          var has = v != null && isFinite(v) && v > 0;
          return {
            color: has ? borderCol : noDataBorder,
            weight: has ? strokeW : 0.5,
            opacity: has ? 1 : 0.6,
            fillColor: has ? fillFor(v) : noDataFill,
            fillOpacity: has ? 0.92 : 0.5
          };
        },
        onEachFeature: function (feature, layer) {
          var fi = feature.__fi;
          var v = valByFeature[fi];
          var has = v != null && isFinite(v) && v > 0;
          var nameProp = (feature.properties && feature.properties.name) || rawOf[fi] || "";
          if (has) {
            var tip = '<div class="bd-map-tip-wrap"><div class="bd-map-tip-label">' + _esc(rawOf[fi] || nameProp) + '</div>' +
              '<div class="bd-map-tip-val" style="color:' + fillFor(v) + '">' + fmtNum(v) + '</div>' +
              '<div class="bd-map-tip-share">' + fmtShare(v) + ' of total' + (mode === "center" ? " · median " + fmtNum(med) : mode === "quantile" ? " · bucket " + (bucketOf(v) + 1) + "/" + seen.length : "") + '</div></div>';
            layer.on({
              mouseover: function () { layer.setStyle({ weight: dark ? 2 : 2.2, fillOpacity: 1 }); if (layer.bringToFront) layer.bringToFront(); },
              mouseout: function () { layer.setStyle({ weight: strokeW, fillOpacity: 0.92 }); },
              click: function () { map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 8 }); }
            });
            layer.bindTooltip(tip, { sticky: true, className: "bd-map-tip" });
          } else {
            layer.bindTooltip(
              '<div class="bd-map-tip-wrap"><div class="bd-map-tip-label">' + _esc(nameProp || "") + '</div>' +
              '<div class="bd-map-tip-share">No data for this region</div></div>',
              { sticky: true, className: "bd-map-tip" });
          }
        }
      });
      geo.addTo(map);
      try { map.fitBounds(geo.getBounds(), { padding: [16, 16], maxZoom: 8 }); }
      catch (e3) { map.setView([20, 0], 2); }

      /* ── Scale bar (bottom-left) ── */
      L.control.scale({ metric: true, imperial: false, position: "bottomleft", maxWidth: 120 }).addTo(map);

      /* ── Legend (mode-aware) + gray "no data" swatch ── */
      var legend = L.control({ position: "bottomright" });
      legend.onAdd = function () {
        var div = L.DomUtil.create("div", "bd-map-legend");
        var body = "";
        if (legStops) {
          var rows = legStops.map(function (s) {
            return '<div class="bd-map-legend-swatch"><span class="sw-box" style="background:' + s.color + ';border-color:' + (dark ? "#0f172a" : "#fff") + '"></span><span>' + s.label + '</span></div>';
          }).join("");
          body = rows;
        } else {
          var stops = palette && palette.length ? palette.slice(0, Math.min(palette.length, 6))
            : ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];
          var gradPos = stops.map(function (col, i) { return col + " " + Math.round(100 * i / Math.max(1, stops.length - 1)) + "%"; }).join(",");
          body = '<div class="bd-map-legend-strip" style="background:linear-gradient(90deg,' + gradPos + ')"></div>' +
            '<div class="bd-map-legend-row"><span>' + fmtNum(1) + '</span><span>' + fmtNum(max) + '</span></div>';
        }
        div.innerHTML =
          '<div class="bd-map-legend-title">' + (legTitle || "Value scale") + '</div>' +
          body +
          '<div class="bd-map-legend-sw"><span class="sw-box" style="background:' + noDataFill + ';border-color:' + noDataBorder + '"></span><span>0 / no data</span></div>' +
          '<div class="bd-map-legend-note">' + matched + ' of ' + entries.length + ' regions on map · ' + _esc(resolved.id) + '</div>';
        return div;
      };
      legend.addTo(map);

      if (unmatched.length) {
        var info = L.control({ position: "topleft" });
        info.onAdd = function () {
          var div = L.DomUtil.create("div", "bd-map-info");
          div.innerHTML = '<i class="fas fa-circle-exclamation"></i> ' + unmatched.length + ' region(s) not on this map (' + _esc(unmatched.slice(0, 3).join(", ")) + (unmatched.length > 3 ? "…" : "") + ")";
          return div;
        };
        info.addTo(map);
      }

      var aliveMap = function () { return map && map._container && map._container.isConnected; };
      var sizing = function () {
        if (!aliveMap()) return;
        setTimeout(function () { if (aliveMap()) map.invalidateSize(); }, 60);
      };
      sizing();
      setTimeout(sizing, 400);
      try {
        var ro = new ResizeObserver(function () { if (aliveMap()) map.invalidateSize(); });
        ro.observe(host);
        host._bdResizeObserver = ro;
      } catch (e) {}
      if (host && host.isConnected) {
        cell._bdMap = map;
      } else {
        try { map.remove(); } catch (e2) {}
      }
    },

    /* ── Dot Distribution Map ────────────────────────────────────────────
       One small dot per record (scattered around the region centroid with
       deterministic jitter) to convey distribution density — visually distinct
       from the radius-scaled bubble map. Keyless/offline: coords come from the
       bundled _PLACE_COORDS (+ city dict). */
    _renderDotMap: function (widget, cell) {
      var c = widget.config || {};
      var labels = c.geoLabels || [], values = c.geoData || [], coords = c.geoCoords || [];
      var pinColor = (widget.config && widget.config.pinColor) || "";
      if (typeof L === "undefined") { cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-circle-dot"></i><div>Leaflet not loaded</div></div>'; return; }
      if (!labels.length) { cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-circle-dot"></i><div>No geo data</div></div>'; return; }

      // Deterministic PRNG so the jitter is stable across re-renders.
      var seeded = function (seed) {
        var s = seed;
        return function () { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
      };

      var points = [];   // {lat,lng,count,label}
      var unresolved = [];
      for (var i = 0; i < labels.length; i++) {
        var pt = coords[i];
        if (!pt) { unresolved.push(labels[i]); continue; }
        points.push({ label: labels[i], lat: pt.lat, lng: pt.lng, count: Math.round(values[i] || 0) });
      }
      if (!points.length) {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-earth-americas"></i><div>' + _esc('No coordinates found for these regions', true) + '</div></div>';
        return;
      }

      var host = document.createElement("div");
      host.className = "bd-leaflet-host";
      host.style.cssText = "width:100%;height:100%;min-height:220px;";
      cell.innerHTML = "";
      cell.appendChild(host);

      var themeOpts = _aiThemeOpts();
      var themeBag = themeOpts.theme || {};
      var palette = Array.isArray(themeOpts.palette) && themeOpts.palette.length ? themeOpts.palette.slice() : null;
      var cssBg = "";
      try { cssBg = getComputedStyle(document.documentElement).getPropertyValue("--color-card-bg").trim(); } catch (e) {}
      var dark = _isDarkHex(themeBag.cardBg || themeBag.background) || (cssBg && _isDarkHex(cssBg));
      var acc = pinColor || themeOpts.accent || "#b45309";

      var tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        subdomains: "abc", maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      });
      var map = L.map(host, { zoomControl: true, attributionControl: true, zoomAnimation: false, fadeAnimation: true, zoomSnap: 0.5 });
      tiles.addTo(map);
      host.classList.add(dark ? "bd-map-host-dark" : "bd-map-host-light");

      // Density ramp: higher count => stronger fill on the cluster of dots.
      var maxCount = 1;
      points.forEach(function (p) { if (p.count > maxCount) maxCount = p.count; });
      var totalDots = 0;
      points.forEach(function (p) { totalDots += Math.min(p.count, 400); });
      var fmtDotsShare = function (v) { return (100 * v / maxCount).toFixed(0) + "% of max"; };

      var DOT_CAP = 400;          // per region
      var dotR = 2.4;
      var clusterLat = 0.35, clusterLng = 0.45; // ~40km jitter box in degrees

      points.forEach(function (p, rank) {
        // Draw the density halo first (soft fill under the dot cluster)
        var t = p.count / maxCount;
        var intensity = 0.22 + 0.5 * t;
        L.circleMarker([p.lat, p.lng], { radius: 3 + 9 * t, stroke: false, fillColor: acc, fillOpacity: dark ? 0.22 : 0.14 }).addTo(map);
        // Scatter the dots deterministically
        var n = Math.min(p.count, DOT_CAP);
        var rnd = seeded(rank * 7919 + 17);
        var dotLayer = L.layerGroup();
        for (var k = 0; k < n; k++) {
          var dLat = (rnd() * 2 - 1) * clusterLat;
          var dLng = (rnd() * 2 - 1) * clusterLng;
          L.circleMarker([p.lat + dLat, p.lng + dLng], {
            radius: dotR,
            stroke: false,
            fillColor: acc,
            fillOpacity: dark ? 0.7 : 0.55
          }).addTo(dotLayer);
        }
        dotLayer.addTo(map);
        // Tooltip + weight on the cluster (bind to the halo)
        var cluster = L.circleMarker([p.lat, p.lng], { radius: 3 + 9 * t, stroke: false, fillColor: acc, fillOpacity: 0 }).addTo(map);
        var tip = '<div class="bd-map-tip-wrap"><div class="bd-map-tip-label">' + _esc(p.label) + '</div>' +
          '<div class="bd-map-tip-val" style="color:' + acc + '">' + fmtNum(p.count) + ' records</div>' +
          '<div class="bd-map-tip-share">' + fmtDotsShare(p.count) + '</div></div>';
        cluster.bindTooltip(tip, { sticky: true, className: "bd-map-tip" });
      });

      // Fit bounds
      var latArr = points.map(function (p) { return p.lat; });
      var lngArr = points.map(function (p) { return p.lng; });
      if (points.length > 1) {
        try { map.fitBounds(L.latLngBounds([Math.min.apply(null, latArr), Math.min.apply(null, lngArr)], [Math.max.apply(null, latArr), Math.max.apply(null, lngArr)]), { padding: [24, 24], maxZoom: 12 }); }
        catch (e) { map.setView([20, 0], 2); }
      } else { map.setView([latArr[0], lngArr[0]], 6); }

      // Legend
      var legend = L.control({ position: "bottomright" });
      legend.onAdd = function () {
        var div = L.DomUtil.create("div", "bd-map-legend");
        div.innerHTML =
          '<div class="bd-map-legend-title">Dot density</div>' +
          '<div class="bd-map-legend-row"><span>1 dot = 1 record</span></div>' +
          '<div class="bd-map-legend-note">' + points.length + ' region(s) · ' + totalDots + ' dots · ' + _esc("capped at " + DOT_CAP + "/region") + '</div>';
        return div;
      };
      legend.addTo(map);

      L.control.scale({ metric: true, imperial: false, position: "bottomleft", maxWidth: 120 }).addTo(map);

      if (unresolved.length) {
        var info = L.control({ position: "topleft" });
        info.onAdd = function () {
          var d = L.DomUtil.create("div", "bd-map-info");
          d.innerHTML = '<i class="fas fa-circle-exclamation"></i> ' + unresolved.length + ' region(s) without coordinates not plotted';
          return d;
        };
        info.addTo(map);
      }

      var aliveMap = function () { return map && map._container && map._container.isConnected; };
      setTimeout(function () { if (aliveMap()) map.invalidateSize(); }, 60);
      setTimeout(function () { if (aliveMap()) map.invalidateSize(); }, 400);
      try { var ro = new ResizeObserver(function () { if (aliveMap()) map.invalidateSize(); }); ro.observe(host); host._bdResizeObserver = ro; } catch (e) {}
      if (host && host.isConnected) {
        cell._bdMap = map;
      } else {
        try { map.remove(); } catch (e2) {}
      }
    },

    /* ── Heatmap Map (intensity blend) ───────────────────────────────────
       Distinct visual language: overlapping radial "glow" blobs whose color is
       driven purely by value intensity (additive screen-blend), not by radius
       scaling. Concentric translucent circles fake a smooth radial gradient
       without any external heatmap library. Keyless/offline. */
    _renderHeatmapMap: function (widget, cell) {
      var c = widget.config || {};
      var labels = c.geoLabels || [], values = c.geoData || [], coords = c.geoCoords || [];
      if (typeof L === "undefined") { cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-temperature-high"></i><div>Leaflet not loaded</div></div>'; return; }
      if (!labels.length) { cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-temperature-high"></i><div>No geo data</div></div>'; return; }

      var points = [], unresolved = [];
      for (var i = 0; i < labels.length; i++) {
        var pt = coords[i];
        if (!pt) { unresolved.push(labels[i]); continue; }
        points.push({ label: labels[i], lat: pt.lat, lng: pt.lng, value: values[i] || 0 });
      }
      if (!points.length) {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-earth-americas"></i><div>' + _esc('No coordinates found for these regions', true) + '</div></div>';
        return;
      }

      var host = document.createElement("div");
      host.className = "bd-leaflet-host";
      host.style.cssText = "width:100%;height:100%;min-height:220px;";
      cell.innerHTML = "";
      cell.appendChild(host);

      var themeOpts = _aiThemeOpts();
      var themeBag = themeOpts.theme || {};
      var palette = Array.isArray(themeOpts.palette) && themeOpts.palette.length ? themeOpts.palette.slice() : null;
      var cssBg = "";
      try { cssBg = getComputedStyle(document.documentElement).getPropertyValue("--color-card-bg").trim(); } catch (e) {}
      var dark = _isDarkHex(themeBag.cardBg || themeBag.background) || (cssBg && _isDarkHex(cssBg));

      // Classic heat ramp blue(0) -> cyan -> yellow -> red(max), or theme palette.
      var heatStops = (palette && palette.length >= 2)
        ? palette.slice(0, Math.min(palette.length, 5))
        : ["#2563eb", "#22d3ee", "#facc15", "#f97316", "#b91c1c"];
      var heatColor = function (t) {
        t = Math.max(0, Math.min(1, t));
        var seg = t * (heatStops.length - 1);
        var idx = Math.min(Math.floor(seg), heatStops.length - 2);
        return _mixHex(heatStops[idx], heatStops[idx + 1], seg - idx);
      };

      var tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        subdomains: "abc", maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      });
      var map = L.map(host, { zoomControl: true, attributionControl: true, zoomAnimation: false, fadeAnimation: true, zoomSnap: 0.5 });
      tiles.addTo(map);
      host.classList.add(dark ? "bd-map-host-dark" : "bd-map-host-light");

      var max = 1;
      points.forEach(function (p) { if (p.value > max) max = p.value; });

      // Heat blobs: custom SVG overlay layer. Uses real SVG <circle>s positioned at
      // container-pixel coords (recomputed on move/zoom), so it renders reliably
      // regardless of the Leaflet path renderer. Keyless/offline.
      var max = 1;
      points.forEach(function (p) { if (p.value > max) max = p.value; });

      var blobMeters = (function () {
        var latS = Math.min.apply(null, points.map(function (p) { return p.lat; }));
        var latN = Math.max.apply(null, points.map(function (p) { return p.lat; }));
        var lngW = Math.min.apply(null, points.map(function (p) { return p.lng; }));
        var lngE = Math.max.apply(null, points.map(function (p) { return p.lng; }));
        var dLat = (latN - latS) || 0.5, dLng = (lngE - lngW) || 0.5;
        var mid = (latN + latS) / 2;
        var mPerLat = 111320, mPerLng = 111320 * Math.cos(mid * Math.PI / 180);
        var diagM = Math.sqrt(Math.pow(dLat * mPerLat, 2) + Math.pow(dLng * mPerLng, 2)) || 1e6;
        // Keep blobs a modest, controlled fraction of the map's geographic
        // diagonal so a hotspot reads as a focused glow, not a giant bubble.
        return Math.max(20000, diagM * 0.05);
      })();
      // User-adjustable bubble size: default 0.5 maps to a 1.0 scale factor.
      var sizeScale = parseFloat(c.bubbleSize);
      if (!isFinite(sizeScale)) sizeScale = 0.5;
      sizeScale = Math.max(0.2, Math.min(1.5, sizeScale)) / 0.5;
      var heatRadiusM = function (t) { return blobMeters * sizeScale * (0.55 + 0.75 * t); };

      var HeatLayer = L.Layer.extend({
        onAdd: function (mp) {
          var self = this;
          this._map = mp;
          var pane = (mp.getPane && mp.getPane("overlay")) || mp.getContainer().querySelector(".leaflet-overlay-pane") || mp.getContainer();
          this._svg = (L.SVG.create ? L.SVG.create("svg") : document.createElementNS("http://www.w3.org/2000/svg", "svg"));
          this._svg.setAttribute("class", "leaflet-zoom-animated bd-heat-svg");
          this._svg.style.pointerEvents = "none";
          if (pane) pane.appendChild(this._svg);
          this._redraw = function () { self._repaint(mp); };
          mp.on("zoomend viewreset move", this._redraw, this);
          this._repaint(mp);
        },
        onRemove: function (mp) {
          if (this._svg && this._svg.parentNode) this._svg.parentNode.removeChild(this._svg);
          mp.off("zoomend viewreset move", this._redraw, this);
        },
        _repaint: function (mp) {
          var size = mp.getSize();
          var svg = this._svg;
          if (!svg) return;
          svg.setAttribute("width", size.x);
          svg.setAttribute("height", size.y);
          var g = document.createElementNS("http://www.w3.org/2000/svg", "g");
          var bounds = mp.getBounds();
          var ySpanDeg = Math.max(0.001, bounds.getNorth() - bounds.getSouth());
          var degPerPx = ySpanDeg / Math.max(1, size.y);
          var metersPerPx = 111320 * degPerPx;
          for (var i = 0; i < points.length; i++) {
            var p = points[i];
            var t = p.value / max;
            var px = mp.latLngToContainerPoint([p.lat, p.lng]);
            var r = Math.max(6, heatRadiusM(t) / Math.max(1, metersPerPx));
            var c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
            c.setAttribute("cx", px.x);
            c.setAttribute("cy", px.y);
            c.setAttribute("r", r);
            c.setAttribute("fill", heatColor(t));
            c.setAttribute("fill-opacity", String(0.22 + 0.5 * t));
            g.appendChild(c);
          }
          svg.innerHTML = "";
          svg.appendChild(g);
        }
      });

      // Tooltip per-hotspot: bind to a transparent interactive circleMarker.
      points.forEach(function (p) {
        var t = p.value / max;
        var pick = L.circleMarker([p.lat, p.lng], { radius: 8, stroke: false, fillColor: "transparent", fillOpacity: 0, interactive: true }).addTo(map);
        var tip = '<div class="bd-map-tip-wrap"><div class="bd-map-tip-label">' + _esc(p.label) + '</div>' +
          '<div class="bd-map-tip-val" style="color:' + heatColor(t) + '">' + fmtNum(p.value) + '</div>' +
          '<div class="bd-map-tip-share">' + (100 * t).toFixed(0) + '% of max intensity</div></div>';
        pick.bindTooltip(tip, { sticky: true, className: "bd-map-tip" });
      });

      var latArr = points.map(function (p) { return p.lat; });
      var lngArr = points.map(function (p) { return p.lng; });
      if (points.length > 1) {
        try { map.fitBounds(L.latLngBounds([Math.min.apply(null, latArr), Math.min.apply(null, lngArr)], [Math.max.apply(null, latArr), Math.max.apply(null, lngArr)]), { padding: [24, 24], maxZoom: 10 }); }
        catch (e) { map.setView([20, 0], 2); }
      } else { map.setView([latArr[0], lngArr[0]], 6); }

      // Add the heat SVG overlay after fitBounds so it positions correctly.
      map.whenReady(function () {
        new HeatLayer().addTo(map);
      });

      // Heat color legend
      var legend = L.control({ position: "bottomright" });
      legend.onAdd = function () {
        var div = L.DomUtil.create("div", "bd-map-legend");
        var gradPos = heatStops.map(function (col, i) { return col + " " + Math.round(100 * i / Math.max(1, heatStops.length - 1)) + "%"; }).join(",");
        div.innerHTML =
          '<div class="bd-map-legend-title">Heat intensity</div>' +
          '<div class="bd-map-legend-strip" style="background:linear-gradient(90deg,' + gradPos + ')"></div>' +
          '<div class="bd-map-legend-row"><span>low</span><span>high</span></div>' +
          '<div class="bd-map-legend-note">' + points.length + ' region(s) · hotspot blend</div>';
        return div;
      };
      legend.addTo(map);
      L.control.scale({ metric: true, imperial: false, position: "bottomleft", maxWidth: 120 }).addTo(map);

      if (unresolved.length) {
        var info = L.control({ position: "topleft" });
        info.onAdd = function () {
          var d = L.DomUtil.create("div", "bd-map-info");
          d.innerHTML = '<i class="fas fa-circle-exclamation"></i> ' + unresolved.length + ' region(s) without coordinates not plotted';
          return d;
        };
        info.addTo(map);
      }

      var aliveMap = function () { return map && map._container && map._container.isConnected; };
      setTimeout(function () { if (aliveMap()) map.invalidateSize(); }, 60);
      setTimeout(function () { if (aliveMap()) map.invalidateSize(); }, 400);
      try { var ro = new ResizeObserver(function () { if (aliveMap()) map.invalidateSize(); }); ro.observe(host); host._bdResizeObserver = ro; } catch (e) {}
      if (host && host.isConnected) {
        cell._bdMap = map;
      } else {
        try { map.remove(); } catch (e2) {}
      }
    },

    /* ── Flow Map (connections) ──────────────────────────────────────────
       Great-circle arcs between origin -> destination point pairs, weighted &
       colored by a measure. Endpoints resolved against the bundled offline
       coordinate dictionary (countries, US states, and the top-city dict). */

    _renderFlowMap: function (widget, cell, rows) {
      var c = widget.config || {};
      if (typeof L === "undefined") { cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-right-left"></i><div>Leaflet not loaded</div></div>'; return; }
      if (!rows || !rows.length) { cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-right-left"></i><div>No flow data</div></div>'; return; }

      // Column detection: origin / destination / measure.
      var colSet = Object.keys(rows[0]);
      var isNum = function (v) { return v != null && v !== "" && !isNaN(parseFloat(v)); };
      var srcCol = (c.originField && colSet.indexOf(c.originField) !== -1) ? c.originField : null;
      var dstCol = (c.destField && colSet.indexOf(c.destField) !== -1) ? c.destField : null;
      if (!srcCol || !dstCol) {
        var desc = function (n) { return (n + "").toLowerCase(); };
        var isSrc = function (n) { return /^from$|^origin$|^source$|^sender$|^departure|^src$|_from$|_origin$|_source$/.test(n); };
        var isDst = function (n) { return /^to$|^dest$|^destination$|^target$|^recipient$|^arrival$|^dst$|_to$|_dest$|_destination$/.test(n); };
        var src = colSet.find(function (x) { return isSrc(desc(x)); });
        var dst = colSet.find(function (x) { return isDst(desc(x)) && x !== src; });
        if (!src) src = colSet.find(function (x) { return /city|state|country|region|location|airport|port|from/.test(desc(x)); });
        if (!dst) dst = colSet.find(function (x) { return /city|state|country|region|location|airport|port|to|dest/.test(desc(x)) && x !== src; });
        srcCol = src || null; dstCol = dst || (colSet.length > 1 ? colSet[1] : null);
      }
      var measCol = (c.measureField && colSet.indexOf(c.measureField) !== -1) ? c.measureField
        : (colSet.find(function (x) { return x !== srcCol && x !== dstCol && isNum(rows[0][x]); }) || "value");

      // Build aggregated origin->dest edges with summed measure.
      var edges = {};
      rows.forEach(function (r) {
        var o = r[srcCol] != null ? String(r[srcCol]).trim() : "";
        var d = r[dstCol] != null ? String(r[dstCol]).trim() : "";
        if (!o || !d || o === d) return;
        var key = o + "\u0001" + d;
        var v = parseFloat(r[measCol] != null ? r[measCol] : 0) || 0;
        var e = edges[key] || (edges[key] = { o: o, d: d, v: 0 });
        e.v += v;
      });
      var edgeList = Object.keys(edges).map(function (k) { return edges[k]; });
      if (!edgeList.length) { cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-right-left"></i><div>No distinct origin→destination pairs</div></div>'; return; }
      edgeList.sort(function (a, b) { return b.v - a.v; });
      if (edgeList.length > 220) edgeList = edgeList.slice(0, 220);

      // Resolve node coords from the offline dictionary.
      var nodeCoord = {};
      var unresolvedNodes = [], unresolvedEdges = [];
      var self = this;
      function resolve(name) {
        if (nodeCoord[name] !== undefined) return nodeCoord[name];
        var pt = self._geoPoint(name, null, null);
        pt = (Array.isArray(pt) && pt.length >= 2) ? { lat: pt[0], lng: pt[1] } : (pt || null);
        nodeCoord[name] = pt;
        if (!pt) unresolvedNodes.push(name);
        return pt;
      }
      var edgeCoords = [];
      edgeList.forEach(function (e) {
        var a = resolve(e.o), b = resolve(e.d);
        if (a && b && isFinite(a.lat) && isFinite(a.lng) && isFinite(b.lat) && isFinite(b.lng)) edgeCoords.push({ o: e.o, d: e.d, v: e.v, oPt: a, dPt: b });
        else unresolvedEdges.push(e.o + " → " + e.d);
      });
      if (edgeCoords.length === 0) {
        cell.innerHTML = '<div class="bd-map-placeholder"><i class="fas fa-earth-americas"></i><div>No origin/destination coordinates found</div></div>';
        return;
      }

      var host = document.createElement("div");
      host.className = "bd-leaflet-host";
      host.style.cssText = "width:100%;height:100%;min-height:220px;";
      cell.innerHTML = "";
      cell.appendChild(host);

      var themeOpts = _aiThemeOpts();
      var themeBag = themeOpts.theme || {};
      var palette = Array.isArray(themeOpts.palette) && themeOpts.palette.length ? themeOpts.palette.slice() : null;
      var cssBg = "";
      try { cssBg = getComputedStyle(document.documentElement).getPropertyValue("--color-card-bg").trim(); } catch (e) {}
      var dark = _isDarkHex(themeBag.cardBg || themeBag.background) || (cssBg && _isDarkHex(cssBg));
      // Ocean backdrop keeps the map readable even when OSM tiles fail offline.
      host.style.backgroundColor = dark ? "#1a2332" : "#dbeafe";
      var baseStyle = dark ? "url(https://tile.openstreetmap.org/{z}/{x}/{y}.png)" : "url(https://tile.openstreetmap.org/{z}/{x}/{y}.png)";
      var map = L.map(host, { zoomControl: true, attributionControl: false, zoomSnap: 0.25 });
      L.tileLayer(baseStyle, {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
        subdomains: "abc",
        className: "bd-map-tiles" + (dark ? " bd-map-tiles-dark" : "")
      }).addTo(map);

      // Offline vector basemap: render the bundled world landmasses so the map
      // shows real geography (countries) even when no tile server is reachable.
      var landStyle = {
        color: dark ? "#31435c" : "#93c5fd",
        weight: dark ? 0.8 : 0.7,
        fillColor: dark ? "#24344d" : "#bfdbfe",
        fillOpacity: dark ? 0.55 : 0.65,
        interactive: false
      };
      var selfL = this;
      Promise.resolve()
        .then(function () { return selfL._loadShapeSet("world"); })
        .then(function (gj) {
          if (!map || !map._container || !map._container.isConnected) return;
          var land = L.geoJSON(gj, { style: landStyle, interactive: false });
          land.addTo(map);
          if (land.bringToBack) land.bringToBack();
          if (land._map) {/* refresh so it draws under existing overlay paths */}
        })
        .catch(function () {});

      // A faint lat/lng graticule adds orientation on top of the ocean/land.
      var gColor = dark ? "rgba(148,163,184,0.16)" : "rgba(255,255,255,0.35)";
      var gratLines = [];
      for (var glat = -60; glat <= 60; glat += 30) {
        var lp = [];
        for (var glngi = -180; glngi <= 180; glngi += 6) lp.push([glat, glngi]);
        gratLines.push(L.polyline(lp, { color: gColor, weight: 1, opacity: 1, interactive: false }));
      }
      for (var glng = -180; glng <= 180; glng += 30) {
        var gtp = [];
        for (var glati = -85; glati <= 85; glati += 5) gtp.push([glati, glng]);
        gratLines.push(L.polyline(gtp, { color: gColor, weight: 1, opacity: 1, interactive: false }));
      }
      var grat = L.layerGroup(gratLines);
      grat.addTo(map);

      // Color scale across hose measure.
      var maxV = edgeCoords[0] ? edgeCoords[0].v : 1;
      var cA = (palette && palette.length >= 2) ? palette[0] : "#1d4ed8";
      var cB = (palette && palette.length >= 3) ? palette[1] : "#b91c1c";
      var flowColor = function (t) { return _mixHex(cA, cB, t); };

      // Build flow arcs (weighted width/opacity by value). Arcs are drawn
      // subtle (gently curved, not full great-circle) so many connections stay
      // readable instead of tangling. Smaller arcs render underneath.
      var arcs = [];
      var drawn = edgeCoords.slice().reverse(); // smallest first -> largest on top
      drawn.forEach(function (e) {
        var t = maxV ? Math.max(0, Math.min(1, e.v / maxV)) : 0;
        var lat1 = e.oPt.lat, lng1 = e.oPt.lng, lat2 = e.dPt.lat, lng2 = e.dPt.lng;
        // Shortest signed longitude difference (handles dateline wrap cleanly).
        var dLng = ((lng2 - lng1 + 540) % 360) - 180;
        var midLat = (lat1 + lat2) / 2;
        var steps = 40;
        // Gentle, clamped lift so arcs read as a clean curve (never a giant bulge).
        var liftDeg = Math.max(0, Math.min(30, Math.abs(dLng) * Math.cos(midLat * Math.PI / 180) * 0.18));
        var pts = [];
        for (var k = 0; k <= steps; k++) {
          var f = k / steps;
          var Lat = lat1 + (lat2 - lat1) * f + liftDeg * Math.sin(Math.PI * f);
          var Lng = lng1 + dLng * f;
          pts.push([Lat, Lng]);
        }
        var line = L.polyline(pts, {
          color: flowColor(Math.min(1, 0.25 + 0.75 * t)),
          weight: 0.6 + 3.4 * t,
          opacity: 0.25 + 0.6 * t,
          smoothFactor: 0
        });
        line.bindTooltip(
          '<div class="bd-map-tip-wrap"><div class="bd-map-tip-label">' + _esc(e.o) + ' → ' + _esc(e.d) + '</div>' +
          '<div class="bd-map-tip-val" style="color:' + flowColor(Math.min(1, 0.25 + 0.75 * t)) + '">' + fmtNum(e.v) + '</div></div>',
          { sticky: true, className: "bd-map-tip" }
        );
        arcs.push(line);
      });

      // Node markers (sized by total outflow+inflow).
      var nodeSum = {};
      edgeCoords.forEach(function (e) { nodeSum[e.o] = (nodeSum[e.o] || 0) + e.v; nodeSum[e.d] = (nodeSum[e.d] || 0) + e.v; });
      var maxNode = 1;
      Object.keys(nodeSum).forEach(function (n) { if (nodeSum[n] > maxNode) maxNode = nodeSum[n]; });
      var nodeColors = {};
      Object.keys(nodeSum).forEach(function (n) {
        var cc = resolve(n);
        var rr = 4 + 7 * (nodeSum[n] / maxNode);
        var m = L.circleMarker([cc.lat, cc.lng], { radius: rr, color: "#ffffff", weight: 1.5, fillColor: flowColor(nodeSum[n] / maxNode), fillOpacity: 0.9 });
        m.bindTooltip('<div class="bd-map-tip-wrap"><div class="bd-map-tip-label">' + _esc(n) + '</div><div class="bd-map-tip-val">' + fmtNum(nodeSum[n]) + '</div></div>', { sticky: true, className: "bd-map-tip" });
        nodeColors[n] = m;
      });

      var arcLayer = L.layerGroup(arcs);
      arcLayer.addTo(map);
      var nodeLayer = L.layerGroup(Object.keys(nodeColors).map(function (n) { return nodeColors[n]; }));
      nodeLayer.addTo(map);

      // Fit bounds to all node coords.
      var allLat = [], allLng = [];
      Object.keys(nodeSum).forEach(function (n) { allLat.push(resolve(n).lat); allLng.push(resolve(n).lng); });
      if (allLat.length > 1) {
        try { map.fitBounds(L.latLngBounds([Math.min.apply(null, allLat), Math.min.apply(null, allLng)], [Math.max.apply(null, allLat), Math.max.apply(null, allLng)]), { padding: [30, 30], maxZoom: 6 }); }
        catch (e) { map.setView([20, 0], 2); }
      } else if (allLat.length === 1) { map.setView([allLat[0], allLng[0]], 6); }
      else { map.setView([20, 0], 2); }

      // Legend.
      var legend = L.control({ position: "bottomright" });
      legend.onAdd = function () {
        var div = L.DomUtil.create("div", "bd-map-legend");
        div.innerHTML =
          '<div class="bd-map-legend-title">Flow volume</div>' +
          '<div class="bd-map-legend-strip" style="background:linear-gradient(90deg,' + cA + ',' + cB + ')"></div>' +
          '<div class="bd-map-legend-row"><span>low</span><span>high</span></div>' +
          '<div class="bd-map-legend-note">' + edgeCoords.length + ' connection(s) · ' + Object.keys(nodeSum).length + ' node(s)</div>';
        return div;
      };
      legend.addTo(map);
      L.control.scale({ metric: true, imperial: false, position: "bottomleft", maxWidth: 120 }).addTo(map);

      if (unresolvedNodes.length) {
        var info = L.control({ position: "topleft" });
        info.onAdd = function () {
          var d = L.DomUtil.create("div", "bd-map-info");
          d.innerHTML = '<i class="fas fa-circle-exclamation"></i> ' + unresolvedNodes.length + ' location(s) without coordinates not plotted';
          return d;
        };
        info.addTo(map);
      }

      var aliveMap = function () { return map && map._container && map._container.isConnected; };
      setTimeout(function () { if (aliveMap()) map.invalidateSize(); }, 60);
      setTimeout(function () { if (aliveMap()) map.invalidateSize(); }, 400);
      try { var ro = new ResizeObserver(function () { if (aliveMap()) map.invalidateSize(); }); ro.observe(host); host._bdResizeObserver = ro; } catch (e) {}
      if (host && host.isConnected) {
        cell._bdMap = map;
      } else {
        try { map.remove(); } catch (e2) {}
      }
    },

    /* ── Filter widgets ── */
    async _fetchFilterData(widget, cell) {
      try {
        widget.__rq = (widget.__rq || 0) + 1;
        var rq = widget.__rq;
        var c = widget.config || (widget.config = {});
        var table = c.table || _dash.table || "";
        var cols = [];
        try { cols = (App.state.tables[table] || {}).columns || []; } catch (e) {}

        /* ── Number range: numeric min/max for the slider ── */
        if (widget.type === "numrange") {
          var nCol = c.numColumn || c.xCol || c.filterField || c.label || _detectFilterField(table, "numrange") || "";
          if (nCol && table) {
            var q = "SELECT MIN(CAST(" + _qi(nCol) + " AS DOUBLE)) AS min_val, MAX(CAST(" + _qi(nCol) + " AS DOUBLE)) AS max_val FROM " + _qi(table);
            var r = _queryCacheGet(q);
            if (!r) { r = await API.biQuery(q); if (r) _queryCacheSet(q, r); }
            if (r && r.rows && r.rows[0]) {
              c.numCol = nCol;
              c.minVal = (r.rows[0].min_val == null ? 0 : Number(r.rows[0].min_val));
              c.maxVal = (r.rows[0].max_val == null ? 0 : Number(r.rows[0].max_val));
            }
          }
          c.labels = c.labels || [];
          if (widget.__rq === rq) this._renderFilterWidget(widget, cell);
          return;
        }

        /* ── Hierarchy: distinct values of the current drill level ── */
        if (widget.type === "hierarchy") {
          var hCols = String(c.hCols || c.hierarchyCols || c.hierarchyColumns || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
          c.hCols = hCols;
          if (!hCols.length) { c.levelCol = ""; c.labels = []; if (widget.__rq === rq) this._renderFilterWidget(widget, cell); return; }
          var cur = 0;
          for (var hi = 0; hi < hCols.length; hi++) {
            var hv = _filters[hCols[hi]];
            if (hv && typeof hv === "string") cur = hi + 1;
            else break;
          }
          c.hDepth = cur;
          var lvlCol = cur < hCols.length ? hCols[cur] : hCols[hCols.length - 1];
          c.levelCol = lvlCol;
          var whereH = _whereFromFilters(cols, [lvlCol]);
          var q = "SELECT DISTINCT " + _qi(lvlCol) + " AS label FROM " + _qi(table) + (whereH ? " WHERE " + whereH : "") + " ORDER BY label";
          var r = _queryCacheGet(q);
          if (!r) { r = await API.biQuery(q); if (r) _queryCacheSet(q, r); }
          c.labels = (r && r.rows ? r.rows : []).map(function (row) { return String(row.label != null ? row.label : (row[Object.keys(row)[0]] != null ? row[Object.keys(row)[0]] : "")); }).filter(Boolean);
          if (widget.__rq === rq) this._renderFilterWidget(widget, cell);
          return;
        }

        /* ── Top N: top/bottom values by measure ── */
        if (widget.type === "topn") {
          var tCol = c.topnColumn || c.xCol || c.filterField || c.label || _detectFilterField(table, "topn") || "";
          var tMsr = c.topnMeasure || c.yCol || "";
          if (!tMsr) {
            for (var ti = 0; ti < cols.length; ti++) {
              var ct = (cols[ti].column_type || cols[ti].type || "").toLowerCase();
              if (ct.indexOf("int") !== -1 || ct.indexOf("float") !== -1 || ct.indexOf("double") !== -1 || ct.indexOf("decimal") !== -1 || ct.indexOf("numeric") !== -1) { tMsr = cols[ti].column_name || cols[ti].name || cols[ti]; break; }
            }
          }
          var n = Math.max(1, parseInt(c.topnN, 10) || 10);
          var dir = c.topnMode === "bottom" ? "ASC" : "DESC";
          c.topnN = n; c.topnMode = c.topnMode === "bottom" ? "bottom" : "top";
          c.topnColumn = tCol; if (tMsr) c.topnMeasure = tMsr;
          if (tCol && tMsr && table) {
            var whereT = _whereFromFilters(cols, [tCol]);
            var q = "SELECT " + _qi(tCol) + " AS label, SUM(CAST(" + _qi(tMsr) + " AS DOUBLE)) AS n FROM " + _qi(table) + (whereT ? " WHERE " + whereT : "") + " GROUP BY 1 ORDER BY n " + dir + " LIMIT " + n;
            var r = _queryCacheGet(q);
            if (!r) { r = await API.biQuery(q); if (r) _queryCacheSet(q, r); }
            var rowsT = (r && r.rows) ? r.rows : [];
            c.labels = rowsT.map(function (row) { return String(row.label != null ? row.label : (row[Object.keys(row)[0]] != null ? row[Object.keys(row)[0]] : "")); }).filter(Boolean);
            c.topnCounts = {};
            rowsT.forEach(function (row) { if (row.label != null) c.topnCounts[String(row.label)] = row.n; });
          } else { c.labels = []; }
          if (widget.__rq === rq) this._renderFilterWidget(widget, cell);
          return;
        }

        /* ── Filter Pane: option lists for each configured field ── */
        if (widget.type === "filterpane") {
          var pFields = String(c.paneFields || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
          if (!pFields.length) {
            pFields = cols.filter(function (cc) {
              var ct = (cc.column_type || cc.type || "").toLowerCase();
              return ct === "varchar" || ct === "string" || ct.indexOf("char") !== -1;
            }).slice(0, 6).map(function (cc) { return cc.column_name || cc.name || cc; });
          }
          c.paneFields = pFields;
          var pane = {};
          var fetches = pFields.map(async function (f) {
            var whereP = _whereFromFilters(cols, [f]);
            var q = "SELECT DISTINCT " + _qi(f) + " AS label FROM " + _qi(table) + (whereP ? " WHERE " + whereP : "") + " ORDER BY label";
            var r = _queryCacheGet(q);
            if (!r) { r = await API.biQuery(q); if (r) _queryCacheSet(q, r); }
            pane[f] = (r && r.rows ? r.rows : []).map(function (row) { return String(row.label != null ? row.label : row[Object.keys(row)[0]]); }).filter(Boolean);
          });
          try { await Promise.all(fetches); } catch (e2) {}
          c.paneData = pane;
          if (widget.__rq === rq) this._renderFilterWidget(widget, cell);
          return;
        }

        /* ── Slicer / Search box / Date range ── */
        var sql = this._filterOptionsSql(widget, table, cols);
        var result = _queryCacheGet(sql);
        if (!result) {
          if (sql) {
            result = await API.biQuery(sql);
            if (result) _queryCacheSet(sql, result);
          } else {
            result = { rows: [] };
          }
        }
        var rows = result && result.rows ? result.rows : (Array.isArray(result) ? result : []);
        if (widget.type === "daterange") {
          c.minVal = rows.length && rows[0] ? (rows[0].min_val || rows[0][Object.keys(rows[0])[0]]) : null;
          c.maxVal = rows.length && rows[0] ? (rows[0].max_val || rows[0][Object.keys(rows[0])[1]]) : null;
        } else {
          c.labels = rows.map(function (r) { return String(r.label || r[Object.keys(r)[0]] || ""); }).filter(Boolean);
        }
        if (widget.__rq === rq) this._renderFilterWidget(widget, cell);
      } catch (e) {
        cell.innerHTML = '<div class="text-center text-gray-400 text-xs py-4"><i class="fas fa-exclamation-triangle mr-1"></i>Error</div>';
      }
    },
    /* Build the option-list query for a slicer/searchbox/daterange, optionally
       slicing the options by the OTHER active filters (cascading slicers). */
    _filterOptionsSql: function (widget, table, cols) {
      var c = widget.config || {};
      var base = c.sql || "";
      if (c.slicerCascade !== true) return base;
      var fField = c.slicerColumn || c.xCol || c.filterField || c.label || (widget.type === "slicer" ? _detectFilterField(table, "slicer") : "") || "";
      var where = _whereFromFilters(cols, [fField]);
      if (!where) return base;
      var sb = _splitSqlTail(base);
      var core = sb.core;
      var g = /\s+GROUP\s+BY[\s\S]*$/i.exec(core);
      var at = g ? g.index : core.length;
      var hasW = /\s+WHERE\s+[\s\S]*$/i.test(core);
      return core.slice(0, at) + " " + (hasW ? "AND " : "WHERE ") + where + (g ? g[0] : "") + (sb.tail ? " " + sb.tail : "");
    },
    _renderFilterWidget: function (widget, cell) {
      var c = widget.config || {};
      var self = this;

      /* ── Number Range: dual-handle slider + numeric inputs ── */
      if (widget.type === "numrange") {
        var numCol = c.numCol || c.numColumn || c.xCol || c.filterField || c.label || "";
        var lo = (c.minVal == null ? 0 : Number(c.minVal));
        var hi = (c.maxVal == null ? 0 : Number(c.maxVal));
        if (!isFinite(lo)) lo = 0;
        if (!isFinite(hi)) hi = 0;
        if (lo > hi) { var ntmp = lo; lo = hi; hi = ntmp; }
        var stepN = (c.numStep != null && Number(c.numStep) > 0) ? Number(c.numStep) : ((hi - lo) <= 10 ? 0.1 : 1);
        if (!isFinite(stepN) || stepN <= 0) stepN = 1;
        var curF = _filters[numCol] || {};
        var curMin = (typeof curF.min === "number") ? curF.min : lo;
        var curMax = (typeof curF.max === "number") ? curF.max : hi;
        var htmlN = '<div class="bd-filter-widget bd-numrange">' +
          '<div class="bd-numrange-track">' +
            '<div class="bd-numrange-fill"></div>' +
            '<input type="range" class="bd-numrange-slider bd-numrange-lo" min="' + lo + '" max="' + hi + '" step="' + stepN + '" value="' + curMin + '">' +
            '<input type="range" class="bd-numrange-slider bd-numrange-hi" min="' + lo + '" max="' + hi + '" step="' + stepN + '" value="' + curMax + '">' +
          '</div>' +
          '<div class="bd-numrange-inputs">' +
            '<input type="number" class="bd-numrange-min" min="' + lo + '" max="' + hi + '" step="' + stepN + '" value="' + curMin + '">' +
            '<span class="bd-filter-sep">to</span>' +
            '<input type="number" class="bd-numrange-max" min="' + lo + '" max="' + hi + '" step="' + stepN + '" value="' + curMax + '">' +
            '<button type="button" class="bd-numrange-clear" title="Clear range"><i class="fas fa-xmark"></i></button>' +
          '</div>' +
        '</div>';
        cell.innerHTML = htmlN;
        var loSlider = cell.querySelector(".bd-numrange-lo");
        var hiSlider = cell.querySelector(".bd-numrange-hi");
        var minInp = cell.querySelector(".bd-numrange-min");
        var maxInp = cell.querySelector(".bd-numrange-max");
        var fillEl = cell.querySelector(".bd-numrange-fill");
        var updateFill = function () {
          if (!fillEl || hi <= lo) return;
          var pctLo = ((Number(loSlider.value) - lo) / (hi - lo)) * 100;
          var pctHi = ((Number(hiSlider.value) - lo) / (hi - lo)) * 100;
          if (pctLo < 0) pctLo = 0; if (pctHi > 100) pctHi = 100;
          fillEl.style.left = pctLo + "%";
          fillEl.style.width = Math.max(0, pctHi - pctLo) + "%";
        };
        var pushN = function () {
          var a = Number(minInp.value), b = Number(maxInp.value);
          if (!isFinite(a)) a = lo;
          if (!isFinite(b)) b = hi;
          var mn = Math.min(a, b), mx = Math.max(a, b);
          if (mn < lo) mn = lo;
          if (mx > hi) mx = hi;
          if (mn > mx) { var st = mn; mn = mx; mx = st; }
          minInp.value = mn; maxInp.value = mx;
          loSlider.value = mn; hiSlider.value = mx;
          updateFill();
          var isFilterN = (mn > lo + 1e-9 || mx < hi - 1e-9);
          CrossFilterBus.dispatch(numCol, isFilterN ? { min: mn, max: mx } : null, widget.id, true);
        };
        updateFill();
        var nRangeTimer = null;
        var pushNDebounced = function () {
          if (nRangeTimer) clearTimeout(nRangeTimer);
          nRangeTimer = setTimeout(pushN, 160);
        };
        loSlider.addEventListener("input", function () {
          var v = Number(loSlider.value);
          if (v <= Number(hiSlider.value)) { minInp.value = v; updateFill(); pushNDebounced(); }
        });
        hiSlider.addEventListener("input", function () {
          var v = Number(hiSlider.value);
          if (v >= Number(loSlider.value)) { maxInp.value = v; updateFill(); pushNDebounced(); }
        });
        minInp.addEventListener("change", pushN);
        minInp.addEventListener("input", function () { if (Number(minInp.value) >= lo) { loSlider.value = Math.min(Number(minInp.value), Number(hiSlider.value)); updateFill(); } });
        maxInp.addEventListener("change", pushN);
        maxInp.addEventListener("input", function () { if (Number(maxInp.value) <= hi) { hiSlider.value = Math.max(Number(maxInp.value), Number(loSlider.value)); updateFill(); } });
        var clrN = cell.querySelector(".bd-numrange-clear");
        if (clrN) clrN.addEventListener("click", function () {
          minInp.value = lo; maxInp.value = hi; loSlider.value = lo; hiSlider.value = hi;
          updateFill();
          CrossFilterBus.dispatch(numCol, null, widget.id, true);
        });
        return;
      }

      /* ── Hierarchy slicer: breadcrumb + drill-down level lists ── */
      if (widget.type === "hierarchy") {
        var hCols = c.hCols || String(c.hierarchyCols || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        var depthH = c.hDepth || 0;
        var levelColH = c.levelCol || hCols[0] || "";
        if (!hCols.length || !levelColH) {
          cell.innerHTML = '<div class="bd-filter-widget bd-hier-empty"><i class="fas fa-sitemap"></i> Set hierarchy columns (e.g. Region,Country,City) in settings.</div>';
          return;
        }
        var breadH = '<div class="bd-hier-bread">';
        for (var bi = 0; bi <= depthH && bi < hCols.length; bi++) {
          var bcol = hCols[bi];
          var bval = (bi < depthH) ? (_filters[hCols[bi]] || "") : "";
          breadH += '<span class="bd-hier-crumb' + (bi === depthH ? " current" : "") + '" data-hlvl="' + bi + '" title="' + _esc(bcol) + '">' +
            (bi < depthH ? (_esc(String(bval)) + ' <i class="fas fa-chevron-right bd-hier-crumb-arrow"></i>') : _esc(bcol)) +
            '</span>';
        }
        breadH += '</div>';
        var itemsH = c.labels || [];
        var selH = _filters[levelColH];
        var selArrH = (typeof selH === "string") ? [selH] : (Array.isArray(selH) ? selH : []);
        var htmlH = '<div class="bd-filter-widget bd-hier-widget">' + breadH +
          '<div class="bd-hier-list">' +
            (itemsH.length ? itemsH.map(function (it) {
              var on = selArrH.indexOf(it) !== -1;
              return '<button type="button" class="bd-hier-opt' + (on ? " is-on" : "") + '" data-hlvl="' + depthH + '" data-v="' + _esc(it) + '">' + _esc(it) + "</button>";
            }).join('') : '<div class="bd-hier-empty">No options at this level</div>') +
          '</div>' +
          (itemsH.length ? '<div class="bd-hier-count">' + itemsH.length + ' option(s)' + (depthH < hCols.length ? ' · ' + _esc(hCols[depthH]) : "") + '</div>' : '') +
        '</div>';
        cell.innerHTML = htmlH;
        var clearFromH = function (lvl) {
          for (var bi = lvl; bi < hCols.length; bi++) CrossFilterBus.dispatch(hCols[bi], null, widget.id);
        };
        cell.querySelectorAll(".bd-hier-crumb").forEach(function (crumb) {
          crumb.addEventListener("click", function () { clearFromH(parseInt(crumb.dataset.hlvl, 10)); });
        });
        cell.querySelectorAll(".bd-hier-opt").forEach(function (opt) {
          opt.addEventListener("click", function () {
            var lvl = parseInt(opt.dataset.hlvl, 10);
            var col = hCols[lvl];
            var v = opt.dataset.v;
            var cur = _filters[col];
            if (typeof cur === "string" && cur === v) { CrossFilterBus.dispatch(col, null, widget.id); return; }
            for (var bi = lvl + 1; bi < hCols.length; bi++) CrossFilterBus.dispatch(hCols[bi], null, widget.id);
            CrossFilterBus.dispatch(col, v, widget.id);
          });
        });
        return;
      }

      /* ── Relative date: quick preset chips ── */
      if (widget.type === "relativedate") {
        var rCol = c.dateColumn || c.xCol || c.filterField || c.label || "Date";
        var availR = ["today", "yesterday", "last 7 days", "last 14 days", "last 30 days", "last 90 days", "this month", "last month", "this quarter", "last quarter", "this year", "last year", "ytd", "mtd", "last 12 months"];
        var presR = c.datePresets ? String(c.datePresets).split(",").map(function (s) { return s.trim(); }).filter(Boolean) : availR;
        var lblR = { today: "Today", yesterday: "Yesterday", "last 7 days": "Last 7 days", "last 14 days": "Last 14 days", "last 30 days": "Last 30 days", "last 60 days": "Last 60 days", "last 90 days": "Last 90 days", "last 180 days": "Last 180 days", "last 365 days": "Last 365 days", "this week": "This week", "last week": "Last week", "this month": "This month", "last month": "Last month", "this quarter": "This quarter", "last quarter": "Last quarter", "this year": "This year", "last year": "Last year", ytd: "YTD", mtd: "MTD", qtd: "QTD", "last 12 months": "Last 12 months" };
        var activeR = _filters[rCol];
        var htmlR = '<div class="bd-filter-widget bd-rel-date-widget">' +
          '<div class="bd-rel-date-chips">' +
            presR.map(function (p) {
              var key = String(p).trim().toLowerCase();
              var isOn = false;
              if (activeR && typeof activeR === "object" && !Array.isArray(activeR) && activeR.start) {
                var rr = _resolveRange(key);
                if (rr && rr[0] === String(activeR.start).slice(0, 10)) isOn = true;
              }
              return '<button type="button" class="bd-rel-chip' + (isOn ? " is-on" : "") + '" data-p="' + key + '">' + _esc(lblR[key] || _capitalize(key)) + "</button>";
            }).join('') +
          '</div>' +
          '<button type="button" class="bd-rel-clear"><i class="fas fa-xmark"></i> Clear</button>' +
        '</div>';
        cell.innerHTML = htmlR;
        cell.querySelectorAll(".bd-rel-chip").forEach(function (cb) {
          cb.addEventListener("click", function () {
            var rr = _resolveRange(cb.dataset.p);
            if (!rr) return;
            CrossFilterBus.dispatch(rCol, { start: rr[0], end: rr[1] }, widget.id, true);
          });
        });
        var clrR = cell.querySelector(".bd-rel-clear");
        if (clrR) clrR.addEventListener("click", function () { CrossFilterBus.dispatch(rCol, null, widget.id); });
        return;
      }

      /* ── Top N: count + direction + multi-select of the qualifying values ── */
      if (widget.type === "topn") {
        var tNCol = c.topnColumn || c.xCol || c.filterField || c.label || "";
        var nN = c.topnN || 10;
        var modeN = c.topnMode === "bottom" ? "bottom" : "top";
        var itemsT = c.labels || [];
        var selT = _filters[tNCol] || [];
        if (typeof selT === "string") selT = [selT];
        else if (!Array.isArray(selT)) selT = [];
        var countsT = c.topnCounts || {};
        var htmlT = '<div class="bd-filter-widget bd-topn-widget">' +
          '<div class="bd-topn-tools">' +
            '<span class="bd-topn-tool-label">Show</span>' +
            '<select class="bd-topn-mode" title="Top or bottom"><option value="top"' + (modeN === "top" ? " selected" : "") + '>Top</option><option value="bottom"' + (modeN === "bottom" ? " selected" : "") + '>Bottom</option></select>' +
            '<input type="number" class="bd-topn-n" min="1" max="200" value="' + nN + '" title="N">' +
            '<button type="button" class="bd-topn-go" title="Refresh list"><i class="fas fa-arrows-rotate"></i></button>' +
            '<button type="button" class="bd-topn-clear" title="Clear filter"><i class="fas fa-xmark"></i></button>' +
          '</div>' +
          '<div class="bd-topn-list">' +
            (itemsT.length ? itemsT.map(function (it) {
              var on = selT.indexOf(it) !== -1;
              var cn = countsT[it];
              return '<label class="bd-slicer-row"><input type="checkbox" class="bd-topn-opt" value="' + _esc(it) + '"' + (on ? " checked" : "") + '> <span class="bd-slicer-lbl">' + _esc(it) + "</span>" + (cn != null ? ' <span class="bd-slicer-n">' + _fmtNumShort(cn) + "</span>" : "") + "</label>";
            }).join('') : '<div class="bd-topn-empty"><i class="fas fa-inbox"></i> No data — pick a field + measure in settings</div>') +
          '</div>' +
        '</div>';
        cell.innerHTML = htmlT;
        cell.querySelectorAll(".bd-topn-opt").forEach(function (cb) {
          cb.addEventListener("change", function () {
            var vals = Array.from(cell.querySelectorAll(".bd-topn-opt:checked")).map(function (o) { return o.value; });
            CrossFilterBus.dispatch(tNCol, vals.length ? vals : null, widget.id);
          });
        });
        var modeEl = cell.querySelector(".bd-topn-mode");
        var nEl = cell.querySelector(".bd-topn-n");
        var goEl = cell.querySelector(".bd-topn-go");
        var runT = function () {
          var w = _findWidget(widget.id);
          if (!w || !w.config) return;
          w.config.topnMode = modeEl.value;
          w.config.topnN = Math.max(1, parseInt(nEl.value, 10) || 10);
          if (self && self._markDirty) self._markDirty();
          if (self && self._refreshWidget) self._refreshWidget(widget.id);
        };
        if (goEl) goEl.addEventListener("click", runT);
        if (modeEl) modeEl.addEventListener("change", runT);
        if (nEl) nEl.addEventListener("change", runT);
        var clrT = cell.querySelector(".bd-topn-clear");
        if (clrT) clrT.addEventListener("click", function () {
          cell.querySelectorAll(".bd-topn-opt").forEach(function (o) { o.checked = false; });
          CrossFilterBus.dispatch(tNCol, null, widget.id);
        });
        return;
      }

      /* ── Filter Pane: several per-field mini filter blocks ── */
      if (widget.type === "filterpane") {
        var pFields = c.paneFields || [];
        var paneData = c.paneData || {};
        var paneHtml = '<div class="bd-filter-pane">';
        if (!pFields.length) {
          paneHtml += '<div class="bd-filter-pane-empty"><i class="fas fa-filter"></i> Configure fields (comma-separated columns) in settings.</div>';
        }
        pFields.forEach(function (pf) {
          var optsP = paneData[pf] || [];
          var selP = _filters[pf] || [];
          if (typeof selP === "string") selP = [selP];
          else if (!Array.isArray(selP)) selP = [];
          paneHtml += '<div class="bd-pane-field' + (selP.length ? " is-active" : "") + '">' +
            '<div class="bd-pane-head">' +
              '<span class="bd-pane-title">' + _esc(pf) + '</span>' +
              (selP.length ? '<span class="bd-pane-badge">' + selP.length + '</span>' : '') +
              '<button type="button" class="bd-pane-clear" data-f="' + _esc(pf) + '" title="Clear"><i class="fas fa-broom"></i></button>' +
              '<button type="button" class="bd-pane-caret" data-f="' + _esc(pf) + '" title="Expand / collapse"><i class="fas fa-chevron-down"></i></button>' +
            '</div>' +
            '<div class="bd-pane-body" data-f="' + _esc(pf) + '">' +
              '<input type="text" class="bd-pane-search" data-f="' + _esc(pf) + '" placeholder="Search options...">' +
              '<div class="bd-pane-list" data-f="' + _esc(pf) + '">' +
                (optsP.length ? optsP.map(function (o) {
                  var on = selP.indexOf(o) !== -1;
                  return '<label class="bd-slicer-row"><input type="checkbox" class="bd-pane-opt" data-f="' + _esc(pf) + '" value="' + _esc(o) + '"' + (on ? " checked" : "") + '> <span class="bd-slicer-lbl">' + _esc(o) + "</span></label>";
                }).join('') : '<div class="bd-pane-noopts">No options</div>') +
              '</div>' +
            '</div>' +
          '</div>';
        });
        paneHtml += '</div>';
        cell.innerHTML = paneHtml;
        var collectP = function (field) {
          var vals = Array.from(cell.querySelectorAll('.bd-pane-opt[data-f="' + _esc(field) + '"]:checked')).map(function (o) { return o.value; });
          var totalP = (paneData[field] || []).length;
          CrossFilterBus.dispatch(field, (vals.length && vals.length < totalP) ? vals : null, widget.id);
        };
        cell.querySelectorAll(".bd-pane-opt").forEach(function (cb) {
          cb.addEventListener("change", function () { collectP(cb.dataset.f); });
        });
        cell.querySelectorAll(".bd-pane-clear").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var f = btn.dataset.f;
            cell.querySelectorAll('.bd-pane-opt[data-f="' + _esc(f) + '"]').forEach(function (cb) { cb.checked = false; });
            CrossFilterBus.dispatch(f, null, widget.id);
          });
        });
        cell.querySelectorAll(".bd-pane-caret").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var body = cell.querySelector('.bd-pane-body[data-f="' + _esc(btn.dataset.f) + '"]');
            if (!body) return;
            body.classList.toggle("collapsed");
            btn.querySelector("i").className = body.classList.contains("collapsed") ? "fas fa-chevron-right" : "fas fa-chevron-down";
          });
        });
        cell.querySelectorAll(".bd-pane-search").forEach(function (si) {
          si.addEventListener("input", function () {
            var f = si.dataset.f;
            var q = si.value.toLowerCase();
            cell.querySelectorAll('.bd-pane-list[data-f="' + _esc(f) + '"] .bd-slicer-row').forEach(function (row) {
              row.style.display = (q && row.textContent.toLowerCase().indexOf(q) === -1) ? "none" : "";
            });
          });
        });
        return;
      }

      /* ── Global search: one box searching across the whole table ── */
      if (widget.type === "globalsearch") {
        var tableG = c.table || _dash.table || "";
        var gCols = [];
        try {
          (App.state.tables[tableG] || {}).columns.forEach(function (cc) {
            var ct = (cc.column_type || cc.type || "").toLowerCase();
            if (cc.column_name && (ct === "varchar" || ct === "string" || ct.indexOf("char") !== -1)) gCols.push(cc.column_name);
          });
        } catch (e) {}
        if (!gCols.length) gCols = [c.xCol || c.filterField || c.label || "label"];
        var activeG = _filters["__global__"];
        var gq = (activeG && typeof activeG === "object" && activeG.search) ? String(activeG.search) : "";
        var htmlG = '<div class="bd-filter-widget bd-search-widget bd-global-search">' +
          '<i class="fas fa-magnifying-glass bd-search-ic"></i>' +
          '<input type="text" class="bd-filter-search" placeholder="' + _esc(c.searchPlaceholder || "Search across all columns...") + '" value="' + _esc(gq) + '" data-clear="1">' +
          '<span class="bd-global-meta">' + gCols.length + ' column(s)</span>' +
          '</div>';
        cell.innerHTML = htmlG;
        var inpG = cell.querySelector("input");
        var debG;
        var runG = function () {
          var v = inpG.value;
          CrossFilterBus.dispatch("__global__", v ? { search: v, cols: gCols } : null, widget.id, true);
        };
        inpG.addEventListener("input", function () {
          clearTimeout(debG);
          debG = setTimeout(runG, 350);
        });
        return;
      }

      if (widget.type === "slicer") {
        var items = c.labels || [];
        /* Filter column priority: explicit slicer column → editor "X axis /
           Label" → legacy auto-detected filterField → label. The auto-detected
           filterField from widget creation is often wrong (first table column);
           the editor's explicit choice must win, otherwise editing a slicer
           would still filter on the stale field. */
        var fField = c.slicerColumn || c.xCol || c.filterField || c.label;
        var sel = _filters[fField] || [];
        if (typeof sel === "string") sel = [sel];
        else if (!Array.isArray(sel)) sel = [];
        var defaults = String(c.slicerDefault || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        if (sel.length === 0 && defaults.length) sel = defaults;
        var multi = c.slicerMultiSelect === true;
        var style = c.slicerStyle || "list";
        var html = '<div class="bd-filter-widget">';
        if (c.slicerSearch === true && items.length > 4) {
          html += '<input type="text" class="bd-filter-search" placeholder="Search options..." data-widget-id="' + widget.id + '">';
        }
        if (c.slicerSelectAll) {
          html += '<label class="bd-slicer-ta"><input type="checkbox" class="bd-slicer-all"' + (sel.length && sel.length === items.length ? " checked" : "") + '> Select all</label>';
        }
        if (style === "buttons") {
          html += '<div class="bd-slicer-btns" data-widget-id="' + widget.id + '">';
          items.forEach(function (item) {
            html += '<button type="button" class="bd-slicer-chip' + (sel.indexOf(item) !== -1 ? " is-on" : "") + '" data-v="' + _esc(item) + '" data-f="' + _esc(fField) + '">' + _esc(item) + "</button>";
          });
          html += "</div>";
        } else if (style === "checkbox") {
          if (c.slicerSelectAll) {
            html += '<div class="bd-slicer-tools"><button type="button" class="bd-slicer-allbtn">Select all</button>' +
              '<button type="button" class="bd-slicer-nonebtn">Clear</button>' +
              '<span class="bd-slicer-count"></span></div>';
          }
          html += '<div class="bd-slicer-cb" data-widget-id="' + widget.id + '">';
          items.forEach(function (item) {
            html += '<label class="bd-slicer-row"><input type="checkbox" class="bd-slicer-opt" data-f="' + _esc(fField) + '" value="' + _esc(item) + '"' + (sel.indexOf(item) !== -1 ? " checked" : "") + "> <span class=\"bd-slicer-lbl\">" + _esc(item) + "</span></label>";
          });
          html += "</div>";
        } else if (style === "dropdown") {
          if (multi) {
            html += '<select class="bd-filter-select bd-slicer-multi" multiple data-filter-field="' + _esc(fField) + '" size="' + Math.max(4, Math.min(items.length, 8)) + '">';
          } else {
            html += '<select class="bd-filter-select bd-slicer-drop" data-filter-field="' + _esc(fField) + '"><option value="">All ' + _esc(fField) + "</option>";
          }
          items.forEach(function (item) {
            html += '<option value="' + _esc(item) + '"' + (sel.indexOf(item) !== -1 ? " selected" : "") + ">" + _esc(item) + "</option>";
          });
          html += "</select>";
        } else {
          html += '<select class="bd-filter-select" multiple data-filter-field="' + _esc(fField) + '">';
          items.forEach(function (item) {
            html += '<option value="' + _esc(item) + '"' + (sel.indexOf(item) !== -1 ? " selected" : "") + ">" + _esc(item) + "</option>";
          });
          html += "</select>";
        }
        html += "</div>";
        cell.innerHTML = html;
        var chipEls = cell.querySelectorAll(".bd-slicer-chip");
        if (chipEls.length) {
          Array.from(chipEls).forEach(function (b) {
            b.addEventListener("click", function () {
              var v = b.dataset.v;
              b.classList.toggle("is-on");
              if (multi) {
                var on = Array.from(cell.querySelectorAll(".bd-slicer-chip.is-on")).map(function (x) { return x.dataset.v; });
                CrossFilterBus.dispatch(fField, on.length ? on : null, widget.id);
              } else {
                CrossFilterBus.dispatch(fField, b.classList.contains("is-on") ? v : null, widget.id);
              }
            });
          });
        } else if (cell.querySelector(".bd-slicer-cb")) {
          var cbAll = cell.querySelector(".bd-slicer-all");
          var collect = function () {
            var vals = Array.from(cell.querySelectorAll(".bd-slicer-opt:checked")).map(function (o) { return o.value; });
            /* PBI semantics: every option checked == no filter */
            CrossFilterBus.dispatch(fField, (vals.length && vals.length < items.length) ? vals : null, widget.id);
          };
          cell.querySelectorAll(".bd-slicer-opt").forEach(function (cb) {
            cb.addEventListener("change", collect);
          });
          if (cbAll) cbAll.addEventListener("change", function () {
            cell.querySelectorAll(".bd-slicer-opt").forEach(function (cb) { cb.checked = cbAll.checked; });
            collect();
          });
          var allBtn = cell.querySelector(".bd-slicer-allbtn");
          var noneBtn = cell.querySelector(".bd-slicer-nonebtn");
          if (allBtn) allBtn.addEventListener("click", function () {
            cell.querySelectorAll(".bd-slicer-opt").forEach(function (cb) { cb.checked = true; });
            collect();
          });
          if (noneBtn) noneBtn.addEventListener("click", function () {
            cell.querySelectorAll(".bd-slicer-opt").forEach(function (cb) { cb.checked = false; });
            collect();
          });
        } else {
          var selEl = cell.querySelector("select");
          if (selEl) {
            selEl.addEventListener("change", function () {
              if (style === "dropdown" && !multi) CrossFilterBus.dispatch(fField, selEl.value || null, widget.id);
              else {
                var vals = Array.from(selEl.selectedOptions).map(function (o) { return o.value; });
                CrossFilterBus.dispatch(fField, vals.length ? vals : null, widget.id);
              }
            });
          }
        }
        var sInp = cell.querySelector('input[placeholder="Search options..."]');
        if (sInp) {
          sInp.addEventListener("input", function () {
            var q = sInp.value.toLowerCase();
            cell.querySelectorAll(".bd-filter-select option").forEach(function (opt) {
              if (opt.value === "") return;
              opt.hidden = !!q && opt.textContent.toLowerCase().indexOf(q) === -1;
            });
            cell.querySelectorAll(".bd-slicer-row, .bd-slicer-chip").forEach(function (el) {
              el.style.display = (!!q && el.textContent.toLowerCase().indexOf(q) === -1) ? "none" : "";
            });
          });
        }
        if (c.slicerCounts === true && c.table && fField) {
          var countsQ = "SELECT \"" + fField.replace(/"/g, '""') + "\" AS lbl, COUNT(*) AS n FROM " + c.table + " GROUP BY 1 ORDER BY 1";
          var self2 = self;
          API.biQuery(countsQ).then(function (r) {
            if (!r || !r.success || !r.rows) return;
            var map = {};
            r.rows.forEach(function (row) { map[String(row.lbl)] = row.n; });
            var total = r.rows.reduce(function (a, row) { return a + row.n; }, 0);
            cell.querySelectorAll(".bd-slicer-row, .bd-slicer-chip, .bd-slicer-drop option, .bd-slicer-multi option").forEach(function (el) {
              var v = el.getAttribute("value") || (el.querySelector && el.querySelector(".bd-slicer-lbl") ? el.querySelector(".bd-slicer-lbl").textContent : "");
              if (el.classList.contains("bd-slicer-chip")) v = el.dataset.v;
              var n = v != null && map[v] != null ? map[v] : null;
              if (n == null) return;
              var badge = document.createElement("span");
              badge.className = "bd-slicer-n";
              badge.textContent = n;
              var holder = el.querySelector(".bd-slicer-lbl");
              if (holder) holder.appendChild(badge);
              else if (el.tagName === "OPTION") el.textContent = el.textContent.replace(/\s*$/, "") + " (" + n + ")";
              else if (el.classList.contains("bd-slicer-chip")) el.appendChild(badge);
            });
            var cntEl = cell.querySelector(".bd-slicer-count");
            if (cntEl) cntEl.textContent = items.length + " options · " + total + " rows";
          }).catch(function () {});
        }
      } else if (widget.type === "searchbox") {
        var ph = c.searchPlaceholder || "Type to filter...";
        var minLen = c.searchMinLength != null ? c.searchMinLength : 1;
        var debMs = c.searchDebounce != null ? c.searchDebounce : 300;
        var fField2 = c.xCol || c.filterField || c.label;
        var html2 = '<div class="bd-filter-widget bd-search-widget">' +
          (c.searchIcon === false ? "" : '<i class="fas fa-search bd-search-ic"></i>') +
          '<input type="text" class="bd-filter-search" placeholder="' + _esc(ph) + '" ' +
          (c.searchClearable === false ? "" : 'data-clear="1"') +
          ' data-widget-id="' + widget.id + '" data-filter-field="' + _esc(fField2) + '">' +
          '</div>';
        cell.innerHTML = html2;
        var inp2 = cell.querySelector("input");
        var debounce2;
        var run = function () {
          var v = inp2.value;
          if (v.length < minLen) v = "";
          var searchCols2 = c.searchColumns ? String(c.searchColumns).split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];
          var multi2 = searchCols2.length > 1 || (searchCols2.length === 1 && String(searchCols2[0]).toLowerCase() !== String(fField2 == null ? "" : fField2).toLowerCase());
          if (multi2) {
            if (v) CrossFilterBus.dispatch(fField2, { search: v, cols: searchCols2 }, widget.id, true);
            else CrossFilterBus.dispatch(fField2, null, widget.id);
          } else {
            CrossFilterBus.dispatch(fField2, v || null, widget.id, true);
          }
        };
        inp2.addEventListener("input", function () {
          clearTimeout(debounce2);
          debounce2 = setTimeout(run, debMs);
        });
        if (c.searchClearable === false) {
          inp2.style.paddingRight = "12px";
        }
      } else if (widget.type === "daterange") {
        var startDef = c.dateRangeStart || "";
        var endDef = c.dateRangeEnd || "";
        var presets = (c.dateRangePresets || c.datePresets) ? String(c.dateRangePresets || c.datePresets).split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];
        var drStyle = c.dateRangeStyle || "calendar";
        var dField = c.dateColumn || c.xCol || c.yCol || c.filterField || _detectFilterField(table, "daterange") || "Date";
        var dRngActive = _filters[dField] || {};
        var dispStart = (dRngActive && dRngActive.start) ? String(dRngActive.start).slice(0, 10) : startDef;
        var dispEnd = (dRngActive && dRngActive.end) ? String(dRngActive.end).slice(0, 10) : endDef;
        var fieldsHtml = '<input type="date" class="bd-filter-date" data-role="start" value="' + _esc(dispStart) + '">' +
          '<span class="bd-filter-sep">to</span>' +
          '<input type="date" class="bd-filter-date" data-role="end" value="' + _esc(dispEnd) + '">';
        var presetsHtml = presets.length ? '<div class="bd-daterange-presets">' + presets.map(function (p) { return '<button type="button" data-p="' + _esc(p) + '">' + _esc(p) + "</button>"; }).join("") + "</div>" : "";
        var html3;
        if (drStyle === "dropdown") {
          var lblDr = (dispStart || dispEnd) ? (dispStart + " → " + dispEnd) : "All dates";
          html3 = '<div class="bd-filter-widget bd-daterange-widget bd-dr-dropdown">' +
            '<button type="button" class="bd-dr-toggle" data-role="toggle"><i class="fas fa-calendar-days"></i> <span class="bd-dr-toggle-label">' + _esc(lblDr) + '</span></button>' +
            '<div class="bd-dr-pop" data-role="pop">' + (c.label ? '<span class="bd-filter-sep">' + _esc(c.label) + "</span>" : "") + fieldsHtml + presetsHtml + "</div>" +
          "</div>";
        } else {
          var pillsClass = drStyle === "pills" ? " bd-dr-pills" : "";
          html3 = '<div class="bd-filter-widget bd-daterange-widget' + pillsClass + '">' +
            (c.label ? '<span class="bd-filter-sep">' + _esc(c.label) + "</span>" : "") +
            fieldsHtml +
            (drStyle === "pills" ? presetsHtml : "") +
          "</div>";
          if (drStyle !== "pills") html3 += presetsHtml;
        }
        cell.innerHTML = html3;
        if (drStyle === "dropdown") {
          var tgDr = cell.querySelector('[data-role=toggle]');
          if (tgDr) tgDr.addEventListener("click", function (e) {
            e.stopPropagation();
            var pop = cell.querySelector('[data-role=pop]');
            if (pop) pop.classList.toggle("open");
          });
          document.addEventListener("click", function (ev) {
            if (!cell.isConnected) { document.removeEventListener("click", arguments.callee); return; }
            if (!cell.contains(ev.target)) {
              var pop = cell.querySelector('[data-role=pop]');
              if (pop) pop.classList.remove("open");
            }
          });
        }
        var closeDrPop = function () {
          var pop = cell.querySelector('[data-role=pop]');
          if (pop) pop.classList.remove("open");
        };
        var dispatchRange = function () {
          var startEl = cell.querySelector('[data-role=start]');
          var endEl = cell.querySelector('[data-role=end]');
          if (!startEl || !endEl) return;
          var start = startEl.value, end = endEl.value;
          if (start || end) CrossFilterBus.dispatch(dField, { start: start, end: end }, widget.id, true);
          else CrossFilterBus.dispatch(dField, null, widget.id);
        };
        cell.querySelectorAll("input[type=date]").forEach(function (inp) {
          inp.addEventListener("change", dispatchRange);
        });
        cell.querySelectorAll(".bd-daterange-presets button").forEach(function (b) {
          b.addEventListener("click", function () {
            var p = b.dataset.p;
            var now = new Date();
            var fmt = function (d) {
              var mm = String(d.getMonth() + 1).padStart(2, "0");
              var dd = String(d.getDate()).padStart(2, "0");
              return d.getFullYear() + "-" + mm + "-" + dd;
            };
            var ranges = {
              "today": [now, now],
              "last 7 days": [new Date(now.getTime() - 6 * 864e5), now],
              "last 30 days": [new Date(now.getTime() - 29 * 864e5), now],
              "this month": [new Date(now.getFullYear(), now.getMonth(), 1), now],
              "last month": [new Date(now.getFullYear(), now.getMonth() - 1, 1), new Date(now.getFullYear(), now.getMonth(), 0)],
              "this quarter": [new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1), now],
              "this year": [new Date(now.getFullYear(), 0, 1), now],
            };
            var r = ranges[p.toLowerCase()];
            if (r) {
              cell.querySelector('[data-role=start]').value = fmt(r[0]);
              cell.querySelector('[data-role=end]').value = fmt(r[1]);
              dispatchRange();
              closeDrPop();
              return;
            }
            var tokens = {
              "this ytd": "ytd", "ytd": "ytd", "last qtd": "pq", "prior quarte": "pq",
              "last quarter": "pq", "last quartern": "pq", "prev quarter": "pq",
              "this qtd": "qtd", "qtd": "qtd", "this quarter": "qtd",
              "this month": "mtd", "mtd": "mtd",
              "last month": "pm", "prior month": "pm", "prior ytd": "py", "prev ytd": "py"
            };
            var tok = tokens[String(p).toLowerCase()];
            if (!tok) return;
            var tbl = c.table;
            if (!tbl && _dash && _dash.pages) {
              var ws = _dash.pages[_dash.currentPage] ? _dash.pages[_dash.currentPage].widgets : [];
              for (var wi = 0; wi < ws.length; wi++) {
                if (ws[wi].config && ws[wi].config.table) { tbl = ws[wi].config.table; break; }
              }
            }
            if (!tbl) tbl = "analytics.sales_data";
            var tq = "SELECT strftime(DATE_TRUNC('year', a.ref), '%Y-%m-%d') AS ytd, strftime(DATE_TRUNC('quarter', a.ref), '%Y-%m-%d') AS qtd, " +
              "strftime(DATE_TRUNC('month', a.ref), '%Y-%m-%d') AS mtd, strftime(a.ref, '%Y-%m-%d') AS ref, " +
              "strftime(DATE_TRUNC('quarter', a.ref - INTERVAL 1 YEAR), '%Y-%m-%d') AS pq, strftime(a.ref - INTERVAL 1 YEAR, '%Y-%m-%d') AS py, " +
              "strftime(DATE_TRUNC('year', a.ref - INTERVAL 1 YEAR), '%Y-%m-%d') AS pyy, " +
              "strftime(DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH), '%Y-%m-%d') AS pm, strftime(DATE_TRUNC('month', a.ref) - INTERVAL 1 DAY, '%Y-%m-%d') AS pme " +
              "FROM (SELECT COALESCE(LEAST(CURRENT_DATE, MAX(TRY_CAST(\"Date\" AS DATE))), CURRENT_DATE) AS ref FROM " + tbl + ") a";
            var btn = b;
            API.biQuery(tq).then(function (qr) {
              if (!qr || !qr.success || !qr.rows || !qr.rows[0]) return;
              var d0 = qr.rows[0];
              var mm = { ytd: [d0.ytd, d0.ref], qtd: [d0.qtd, d0.ref], mtd: [d0.mtd, d0.ref], py: [d0.pyy, d0.py], pq: [d0.pq, d0.py], pm: [d0.pm, d0.pme] };
              var pair = mm[tok];
              if (!pair) return;
              btn.disabled = false;
              btn.textContent = btn.dataset.p;
              c.dateRangeStart = pair[0];
              c.dateRangeEnd = pair[1];
              if (self && self._markDirty) self._markDirty();
              var live = document.querySelector('.ff-widget-content[data-id="' + widget.id + '"]');
              var sEl = live && live.querySelector('[data-role=start]');
              var eEl = live && live.querySelector('[data-role=end]');
              if (sEl && eEl) {
                sEl.value = pair[0];
                eEl.value = pair[1];
              }
              closeDrPop();
              dispatchRange();
            }).catch(function () { btn.disabled = false; btn.textContent = btn.dataset.p; });
            btn.disabled = true;
            btn.textContent = "...";
          });
        });
      }
    },

    _refreshAll: function () {
      this.render();
    },

    /* ── Widget management hooks (called from the grid action menu) ── */
    _onWidgetEdited: function (id, changedKey) {
      var w = _findWidget(id);
      var cell = document.querySelector('.ff-widget-content[data-id="' + id + '"]');
      if (!w || !cell) return;
      var el = document.querySelector('.ff-widget[data-id="' + id + '"]');
      if (el) el.classList.toggle("ff-locked", !!w.locked);
      /* Rebuild or patch SQL only when a data-affecting prop changed (or when
         no specific key is known, e.g. the widget-editor save flow) so purely
         visual edits in the Properties dock never clobber the stored query.
         When xCol and measures are explicitly configured we can rebuild from
         scratch; otherwise just update the LIMIT in the existing SQL to avoid
         breaking the query. A direct SQL edit is always preserved verbatim. */
      var c = w.config || {};
      if (c.sql) {
        var isSqlEdit = changedKey === "config.sql" || changedKey === "sql";
        var dataAffecting = !changedKey || _isSqlAffectingKey(changedKey);
        var needsRebuild = dataAffecting && !isSqlEdit && (c.xCol || (c.measures && c.measures.length));
        if (needsRebuild && typeof _buildWidgetSql === "function") {
          try { c.sql = _buildWidgetSql(w); w._origSql = null; } catch (e) { /* keep old sql */ }
        } else if (!isSqlEdit && c.limit != null) {
          /* Patch the LIMIT clause in the existing SQL */
          c.sql = c.sql.replace(/LIMIT\s+\d+/i, "LIMIT " + c.limit);
          w._origSql = null;
        }
      }
      this._positionWidget(w);
      this._paintWidget(w);
      this._renderWidgetContent(w, cell);
    },
    /* Re-create inline <script> tags after injecting HTML so Plotly-style
       charts embedded in custom widgets actually render (mirrors chat.js). */
    _execWidgetScripts: function (host) {
      if (!host) return;
      host.querySelectorAll("script").forEach(function (old) {
        var s = document.createElement("script");
        Array.prototype.forEach.call(old.attributes, function (a) { s.setAttribute(a.name, a.value); });
        var inline = !old.src && !old.getAttribute("src");
        if (inline && /Plotly/.test(old.text)) {
          s.text = "(function _waitPlotly(){ if (window.Plotly) { " + old.text + " } else { setTimeout(_waitPlotly, 60); } })();";
        } else {
          s.text = old.text;
          s.async = false;
        }
        old.replaceWith(s);
      });
    },
    _refreshWidget: function (id) {
      var cell = document.querySelector('.ff-widget-content[data-id="' + id + '"]');
      this._renderWidgetContent(_findWidget(id), cell);
      App.success("Data refreshed");
    },
    /* Filter change: repaint ONLY widget contents + the active-filter bar — no
       chrome rebuild, no full-page loading overlay — so dragging a slider or
       picking chips feels live. Queries run in API quiet mode. */
    _repaintOnFilter: function () {
      var present = _dash.mode === "present";
      var self = this;
      var missing = false;
      API.quiet(true);
      try {
        _currentWidgets().forEach(function (w) {
          var cell = document.querySelector('.ff-widget-content[data-id="' + w.id + '"]');
          if (!cell) { missing = true; return; }
          self._renderWidgetContent(w, cell);
        });
        if (!present && typeof _renderFilterBar === "function") {
          var wrap = document.querySelector(".bd-canvas-wrap");
          if (wrap) {
            var frag = document.createElement("div");
            frag.innerHTML = _renderFilterBar() || "<div></div>";
            var bar = frag.firstElementChild;
            var old = wrap.querySelector(":scope > .bd-filter-bar");
            if (bar) { if (old) old.replaceWith(bar); else wrap.insertBefore(bar, wrap.firstElementChild); }
            else if (old) old.remove();
          }
        }
      } finally {
        API.quiet(false);
      }
      if (missing) this.render();
    },
    /* Refit a widget's content to its (recently resized) container without re-querying */
    _resizeWidget: function (id) {
      var w = _findWidget(id);
      var cell = document.querySelector('.ff-widget-content[data-id="' + id + '"]');
      if (!w || !cell) return;
      if (_FILTER_TYPES.indexOf(w.type) !== -1) this._renderFilterWidget(w, cell);
      else if (w.type === "page" || w.type === "text" || w.type === "image" || w.type === "custom" || w.type === "shape" || w.type === "divider") this._renderWidgetContent(w, cell);
      else if (DashboardRenderer) DashboardRenderer.render(w, cell);
    },

    /* ── P3.11 — calendar-hierarchy drill (Year › Quarter › Month › Day) ──
       Sets the chart's grouping level against the analytics.calendar dimension
       and re-renders inline. Author edits persist (markDirty); present-mode
       drilling is presentation-only. Clicking the active level resets back to
       the raw date grouping. */
    _applyCalendarDrill: function (id, level) {
      var w = _findWidget(id);
      if (!w) return;
      var cfg = w.config || (w.config = {});
      var cur = String(cfg.hierarchyLevel || "").toLowerCase();
      var want = String(level || "").toLowerCase();
      if (want === "none" || want === "" || want === cur) delete cfg.hierarchyLevel;
      else cfg.hierarchyLevel = level;
      if (cfg.table || _dash.table) cfg.sql = _buildWidgetSql(w);
      this._refreshWidget(id);
      this._renderCalendarDrill(id);
      if (_dash.mode !== "present") { this._markDirty(); this._refreshHistory(); }
    },
    _renderCalendarDrill: function (id) {
      var el = document.querySelector('.ff-widget[data-id="' + _esc(id) + '"]');
      if (!el) return;
      var host = el.querySelector(".bd-cal-drill");
      if (host) host.remove();
      var w = _findWidget(id);
      if (!w || w.config && w.config.isTooltip) return;
      if (_DRILL_EXEMPT.indexOf(w.type) !== -1 || !_widgetCalendarAvail(w)) return;
      var cur = (w.config && w.config.hierarchyLevel) || "";
      var html = '<span class="bd-cal-drill-label" title="Calendar.Date hierarchy (analytics.calendar)"><i class="fas fa-calendar-days"></i> </span>';
      _CAL_LEVELS.forEach(function (L) {
        html += '<button type="button" class="bd-cal-level' + (cur === L.level ? " active" : "") + '" title="Group by ' + L.level + '" onclick="BlankDash._applyCalendarDrill(\'' + _esc(id) + '\',\'' + L.level + '\')">' + L.level + '</button>';
      });
      var bar = document.createElement("div");
      bar.className = "bd-cal-drill";
      bar.innerHTML = html;
      var bodyEl = el.querySelector(".ff-widget-body");
      if (!bodyEl) return;
      var title = el.querySelector(".ff-widget-title-inner");
      if (title) bodyEl.insertBefore(bar, title.nextSibling);
      else bodyEl.insertBefore(bar, bodyEl.firstChild);
    },

    /* ── Layers panel: visibility / lock / selection helpers ── */
    _toggleLayerHidden: function (id) {
      var w = _findWidget(id);
      if (!w) return;
      w.hidden = !w.hidden;
      var el = document.querySelector('.ff-widget[data-id="' + id + '"]');
      if (el) el.classList.toggle("ff-hidden", !!w.hidden);
      if (w.hidden) {
        var i = _selectedIds.indexOf(id);
        if (i !== -1) { _selectedIds.splice(i, 1); this._refreshSelection(); }
      }
      this._renderLeftRailLive();
      this._markDirty();
    },
    _toggleLayerLocked: function (id) {
      var w = _findWidget(id);
      if (!w) return;
      w.locked = !w.locked;
      var el = document.querySelector('.ff-widget[data-id="' + id + '"]');
      if (el) {
        el.classList.toggle("ff-locked", !!w.locked);
        _syncResizeChrome(el, w);
      }
      this._renderLeftRailLive();
      this._markDirty();
    },
    _toggleLayerGroup: function (btn) {
      var g = btn && btn.closest(".bd-lr-group");
      if (!g) return;
      var name = g.getAttribute("data-group") || "";
      _layerCollapsed[name] = !_layerCollapsed[name];
      g.classList.toggle("collapsed", !!_layerCollapsed[name]);
    },
    _toggleLrSection: function (which, ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      _lrSectionCollapsed[which] = !_lrSectionCollapsed[which];
      var cls = { fields: "bd-fields-section", measures: "bd-measures-section", layers: "bd-layers-section" }[which];
      var sec = cls && document.querySelector('.bd-leftrail .' + cls);
      if (sec) sec.classList.toggle("collapsed", !!_lrSectionCollapsed[which]);
    },
    _filterLayers: function (val) {
      _layerSearch = String(val || "").toLowerCase().trim();
      this._applyLayerFilter();
    },
    _filterFields: function (val) {
      _fieldSearch = String(val || "").toLowerCase().trim();
      this._applyFieldFilter();
    },
    _applyFieldFilter: function () {
      var rail = document.querySelector(".bd-leftrail");
      if (!rail) return;
      var q = _fieldSearch;
      var groups = rail.querySelectorAll(".bd-fields-group");
      for (var i = 0; i < groups.length; i++) {
        var any = false;
        var chips = groups[i].querySelectorAll(".bd-field");
        for (var j = 0; j < chips.length; j++) {
          var match = !q || (chips[j].getAttribute("data-search") || "").indexOf(q) !== -1;
          chips[j].style.display = match ? "" : "none";
          if (match) any = true;
        }
        groups[i].style.display = any ? "" : "none";
      }
      var tables = rail.querySelectorAll(".bd-fields-table");
      for (var k = 0; k < tables.length; k++) {
        var tAny = false;
        var tGroups = tables[k].querySelectorAll(".bd-fields-group");
        for (var m = 0; m < tGroups.length; m++) {
          if (tGroups[m].style.display !== "none") { tAny = true; break; }
        }
        tables[k].style.display = tAny ? "" : "none";
      }
    },
    _filterMeasures: function (val) {
      _measureSearch = String(val || "").toLowerCase().trim();
      this._applyMeasureFilter();
    },
    _applyMeasureFilter: function () {
      var rail = document.querySelector(".bd-leftrail");
      if (!rail) return;
      var q = _measureSearch;
      var rows = rail.querySelectorAll(".bd-lr-measure");
      for (var i = 0; i < rows.length; i++) {
        var match = !q || (rows[i].getAttribute("data-search") || "").indexOf(q) !== -1;
        rows[i].style.display = match ? "" : "none";
      }
    },
    _applyLayerFilter: function () {
      var rail = document.querySelector(".bd-leftrail");
      if (!rail) return;
      var q = _layerSearch;
      var groups = rail.querySelectorAll(".bd-lr-group");
      for (var i = 0; i < groups.length; i++) {
        var any = false;
        var rows = groups[i].querySelectorAll(".bd-lr-layer");
        for (var j = 0; j < rows.length; j++) {
          var match = !q || (rows[j].getAttribute("data-search") || "").indexOf(q) !== -1;
          rows[j].style.display = match ? "" : "none";
          if (match) any = true;
        }
        groups[i].style.display = any ? "" : "none";
      }
    },
    _renderLeftRailLive: function () {
      var rail = document.querySelector(".bd-leftrail");
      if (!rail) return;
      var inputs = rail.querySelectorAll(".bd-lr-search");
      var vals = [], carets = [];
      for (var i = 0; i < inputs.length; i++) {
        vals.push(inputs[i].value);
        carets.push(inputs[i].selectionStart);
      }
      var wrap = document.createElement("div");
      wrap.innerHTML = _renderLeftRail();
      var fresh = wrap.firstElementChild;
      if (fresh && rail.parentNode) rail.parentNode.replaceChild(fresh, rail);
      if (_leftRailCollapsed) {
        fresh.classList.add("bd-collapsed");
        var layout = fresh.closest(".bd-layout");
        if (layout) layout.classList.add("bd-rail-collapsed");
      }
      var ni = fresh.querySelectorAll(".bd-lr-search");
      for (var j = 0; j < ni.length && j < vals.length; j++) {
        if (vals[j]) {
          ni[j].value = vals[j];
          ni[j].focus();
          try { ni[j].setSelectionRange(carets[j], carets[j]); } catch (e) {}
        }
      }
      if (vals[0]) this._applyFieldFilter();
      if (vals[1]) this._applyMeasureFilter();
      if (vals[2]) this._applyLayerFilter();
    },
    _scrollToWidget: function (id) {
      var w = _findWidget(id);
      if (!w) return;
      var wrap = document.querySelector(".bd-canvas-wrap");
      if (!wrap || !wrap.scrollTo) return;
      wrap.scrollLeft = Math.max(0, w.x * _zoom - wrap.clientWidth / 2 + (w.width * _zoom) / 2);
      wrap.scrollTop = Math.max(0, w.y * _zoom - wrap.clientHeight / 2 + (w.height * _zoom) / 2);
    },

    /* ── Multi-page tabs ── */
    _syncPageHash: function () {
      try {
        if (_dash.pages.length < 2) return;
        var h = window.location.hash || "";
        var m = /^(#blankdash\/[A-Za-z0-9\-_]+)(?:\/p\d+)?/.exec(h);
        if (m) history.replaceState(null, "", m[1] + "/p" + (_dash.currentPage + 1));
      } catch (e) {}
    },
    _switchPage: function (idx, opts) {
      if (idx < 0 || idx >= _dash.pages.length || idx === _dash.currentPage) return;
      var cur = _dash.currentPage;
      var snap = _pageFilterSnapshot();
      _storePageFilters(cur, snap);
      if (opts && opts.carry) {
        /* Drill-through navigation: current filters become the target page's
           state (page context carries filter values) and a back entry is kept. */
        _drillStack.push({ from: cur, filters: snap });
        _storePageFilters(idx, JSON.parse(JSON.stringify(snap)));
      } else {
        _restorePageFilters(idx);
      }
      _dash.currentPage = idx;
      this._syncPageHash();
      this.render();
      if (_dash.mode === "present") {
        this._animatePageTransition(cur, idx);
        this._updatePresenterView();
        this._renderAnnLayer();
      }
    },
    /* C7 — present-mode page transition. render() rebuilds the canvas, so the
       incoming page is animated in (fade / slide / zoom / none) using the
       target page's transition setting. Reduced-motion users get a plain fade. */
    _animatePageTransition: function (fromIdx, toIdx) {
      var canvas = document.getElementById("bd-canvas");
      if (!canvas) return;
      var pg = _dash.pages[toIdx] || {};
      var t = pg.transition || "fade";
      var ms = Math.max(0, Math.min(1200, pg.transitionMs != null ? pg.transitionMs : 350));
      var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduce) t = "fade";
      var dir = toIdx > fromIdx ? 1 : -1;
      var cls = "bd-trans-" + t + (t === "slide" ? (dir > 0 ? " bd-trans-right" : " bd-trans-left") : "");
      var tokens = ["bd-trans-in"].concat(cls.split(/\s+/));
      tokens.forEach(function (c) { canvas.classList.add(c); });
      setTimeout(function () {
        tokens.forEach(function (c) { canvas.classList.remove(c); });
      }, ms);
    },

    _drillThrough: function (pageRef, drillField) {
      var tIdx = _resolvePageIndex(pageRef);
      if (tIdx === -1) { App.warning("Target page not found: " + pageRef); return; }
      var snap = _pageFilterSnapshot();
      if (drillField && drillField.field && drillField.value != null) snap[drillField.field] = drillField.value;
      if (_dash.pages[tIdx]) _dash.pages[tIdx].filters = JSON.parse(JSON.stringify(snap));
      _drillStack.push({ from: _dash.currentPage, filters: _pageFilterSnapshot() });
      _storePageFilters(_dash.currentPage, _pageFilterSnapshot());
      _filters = JSON.parse(JSON.stringify(snap));
      _dash.currentPage = tIdx;
      this._syncPageHash();
      this.render();
      App.info("Drilled through to \u201C" + (_dash.pages[tIdx].name || ("Page " + (tIdx + 1))) + "\u201D");
    },
    _goToPage: function (id) {
      var w = _findWidget(id);
      if (!w) return;
      var tIdx = (w.config && typeof w.config.targetPage === "number") ? w.config.targetPage : _dash.currentPage;
      this._switchPage(tIdx);
    },
    _actionBtn: function (id) {
      var w = _findWidget(id);
      if (!w) return;
      var c = w.config || {};
      var act = c.action || "reset";
      var val = c.value != null ? String(c.value) : "";
      if (act === "reset") {
        CrossFilterBus.clear();
        App.info("All filters cleared");
      } else if (act === "bookmark") {
        var list = this._listBookmarks();
        var bm = null;
        if (val) {
          for (var i = 0; i < list.length; i++) {
            if (list[i].id === val || list[i].name === val) { bm = list[i]; break; }
          }
        } else if (list.length) bm = list[list.length - 1];
        if (bm) this._applyBookmark(bm.id);
        else App.warning("No bookmark found" + (val ? ": " + val : ""));
      } else if (act === "page") {
        var aIdx = _resolvePageIndex(val);
        if (aIdx === -1) aIdx = (_dash.currentPage + 1 < _dash.pages.length) ? _dash.currentPage + 1 : 0;
        this._switchPage(aIdx);
      } else if (act === "drill") {
        if (!val) {
          val = _dash.pages[_dash.currentPage + 1] ? String(_dash.currentPage + 2) : "1";
        }
        this._drillThrough(val);
      } else if (act === "refresh") {
        var ws = _currentWidgets();
        for (var i2 = 0; i2 < ws.length; i2++) {
          var w2 = ws[i2];
          if (!w2 || w2.type === "text" || w2.type === "image" || w2.type === "custom" || w2.type === "page" || w2.type === "button" || w2.type === "shape" || w2.type === "divider") continue;
          var cell = document.querySelector('.ff-widget-content[data-id="' + w2.id + '"]');
          if (cell) this._renderWidgetContent(w2, cell);
        }
        App.info("Widgets refreshed");
      }
    },
    _addPage: function () {
      var id = "page_" + Date.now().toString(36);
      _dash.pages.push({ id: id, name: "Page " + (_dash.pages.length + 1), widgets: [], notes: "" });
      _dash.currentPage = _dash.pages.length - 1;
      this._markDirty();
      this.render();
      this._refreshPagesModal();
    },
    _removePage: function (idx) {
      if (_dash.pages.length <= 1) { App.warning("You need at least one page"); return; }
      if (window.Modal && window.Modal.confirm) {
        var self = this;
        Modal.confirm("Delete page?", "Delete \"" + _esc(_dash.pages[idx].name || "Page " + (idx + 1)) + "\" and its " + (_dash.pages[idx].widgets || []).length + " widgets? This cannot be undone.", { variant: "danger", confirmLabel: "Delete" }).then(function (ok) {
          if (ok) self._removePageNow(idx);
        });
      } else {
        this._removePageNow(idx);
      }
    },
    _removePageNow: function (idx) {
      _dash.pages.splice(idx, 1);
      if (_dash.currentPage >= _dash.pages.length) _dash.currentPage = _dash.pages.length - 1;
      this._markDirty();
      this.render();
      this._refreshPagesModal();
    },
    _duplicatePage: function (idx) {
      var src = _dash.pages[idx];
      if (!src) return;
      var copy = JSON.parse(JSON.stringify(src));
      copy.id = "page_" + Date.now().toString(36);
      copy.name = (src.name || ("Page " + (idx + 1))) + " (copy)";
      copy.widgets = (copy.widgets || []).map(function (w) {
        w.id = "w" + Date.now().toString(36) + "_" + (++_seq);
        return w;
      });
      _dash.pages.splice(idx + 1, 0, copy);
      _dash.currentPage = idx + 1;
      this._markDirty();
      this.render();
      this._refreshPagesModal();
      App.success("Page duplicated");
    },
    /* Move a page up/down; remaps page-link targetPage indices so links stay on the same page. */
    _movePage: function (idx, dir) {
      var len = _dash.pages.length;
      var to = idx + dir;
      if (to < 0 || to >= len) { App.warning("Already at the edge"); return; }
      var pg = _dash.pages.splice(idx, 1)[0];
      _dash.pages.splice(to, 0, pg);
      if (_dash.currentPage === idx) _dash.currentPage = to;
      else if (idx < _dash.currentPage && to >= _dash.currentPage) _dash.currentPage--;
      else if (idx > _dash.currentPage && to <= _dash.currentPage) _dash.currentPage++;
      for (var p = 0; p < _dash.pages.length; p++) {
        (_dash.pages[p].widgets || []).forEach(function (w) {
          if (w && w.config && typeof w.config.targetPage === "number") {
            var t = w.config.targetPage;
            if (t === idx) w.config.targetPage = to;
            else if (idx < t && t <= to) w.config.targetPage = t - 1;
            else if (to <= t && t < idx) w.config.targetPage = t + 1;
          }
        });
      }
      this._markDirty();
      this.render();
      this._refreshPagesModal();
    },
    /* Per-page "…” actions (rename / duplicate / move / delete) */
    _togglePageMenu: function (idx, btn) {
      this._closeContextMenus();
      var pg = _dash.pages[idx];
      if (!pg) return;
      var len = _dash.pages.length;
      var menu = document.createElement("div");
      menu.className = "ff-widget-menu";
      menu.style.minWidth = "170px";
      menu.innerHTML =
        '<div style="padding:6px 10px;font-size:11px;color:var(--color-text-muted);border-bottom:1px solid var(--color-card-border,var(--color-border));margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _esc(pg.name || ("Page " + (idx + 1))) + "</div>" +
        '<button data-a="rename"><i class="fas fa-pen"></i> Rename</button>' +
        '<button data-a="dup"><i class="fas fa-copy"></i> Duplicate</button>' +
        '<button data-a="left"' + (idx > 0 ? "" : " disabled") + '><i class="fas fa-arrow-left"></i> Move left</button>' +
        '<button data-a="right"' + (idx < len - 1 ? "" : " disabled") + '><i class="fas fa-arrow-right"></i> Move right</button>' +
        '<div style="border-top:1px solid var(--color-card-border,var(--color-border));margin:4px 0;"></div>' +
        '<button data-a="del" class="ff-menu-danger"' + (len > 1 ? "" : " disabled") + '><i class="fas fa-trash"></i> Delete page</button>';
      var self = this;
      menu.addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (!b || b.disabled) return;
        var act = b.dataset.a;
        menu.remove();
        if (act === "rename") self._promptRenamePage(idx);
        else if (act === "dup") self._duplicatePage(idx);
        else if (act === "left") self._movePage(idx, -1);
        else if (act === "right") self._movePage(idx, 1);
        else if (act === "del") self._removePage(idx);
      });
      var r = btn.getBoundingClientRect();
      this._placeMenu(menu, r.left, r.bottom + 2);
    },
    _renamePage: function (idx, name) {
      if (_dash.pages[idx]) _dash.pages[idx].name = name || "Page " + (idx + 1);
      this._markDirty();
      this._refreshPagesModal();
    },

    /* ── Pages manager ── */
    _pagesModalRows: function () {
      var self = this;
      var rows = _dash.pages.map(function (pg, i) {
        var active = i === _dash.currentPage;
        var wcount = (pg.widgets || []).length;
        var t = pg.transition || "fade";
        var tOpts = [["none", "None"], ["fade", "Fade"], ["slide", "Slide"], ["zoom", "Zoom"]];
        var tSel = '<select class="form-input bd-pg-trans" title="Present-mode transition" onchange="BlankDash._setPageTransition(' + i + ', this.value)">' +
          tOpts.map(function (o) { return '<option value="' + o[0] + '"' + (t === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") +
          "</select>";
        return '<div class="bd-pg-row' + (active ? " bd-pg-active" : "") + '" data-idx="' + i + '">' +
          '<span class="bd-pg-idx">' + (i + 1) + '</span>' +
          '<div class="bd-pg-main">' +
            '<div class="bd-pg-line">' +
              '<input class="form-input bd-pg-name" value="' + _esc(pg.name || "") + '" onchange="BlankDash._renamePage(' + i + ', this.value)" aria-label="Page name">' +
              tSel +
              '<span class="bd-pg-count">' + wcount + ' widget' + (wcount === 1 ? "" : "s") + '</span>' +
              '<span class="bd-pg-actions">' +
                '<button class="btn btn-sm btn-ghost" title="Move up" onclick="BlankDash._movePage(' + i + ', -1)"><i class="fas fa-arrow-up"></i></button>' +
                '<button class="btn btn-sm btn-ghost" title="Move down" onclick="BlankDash._movePage(' + i + ', 1)"><i class="fas fa-arrow-down"></i></button>' +
                '<button class="btn btn-sm btn-ghost" title="Duplicate page" onclick="BlankDash._duplicatePage(' + i + ')"><i class="fas fa-copy"></i></button>' +
                '<button class="btn btn-sm btn-ghost bd-pg-del" title="Delete page"' + (_dash.pages.length > 1 ? "" : " disabled") + ' onclick="BlankDash._removePage(' + i + ')"><i class="fas fa-trash"></i></button>' +
              '</span>' +
            '</div>' +
            '<textarea class="form-input bd-pg-notes" placeholder="Speaker notes (shown in presenter view)" onchange="BlankDash._setPageNotes(' + i + ', this.value)">' + _esc(pg.notes || "") + '</textarea>' +
          '</div>' +
        '</div>';
      }).join("");
      if (!_dash.pages.length) rows = '<div class="bd-gal-empty">No pages yet.</div>';
      return rows;
    },
    _setPageTransition: function (idx, val) {
      var pg = _dash.pages[idx];
      if (!pg) return;
      pg.transition = val;
      this._markDirty();
      App.info("Page " + (idx + 1) + " transition set to " + (val || "none"));
    },
    _setPageNotes: function (idx, val) {
      var pg = _dash.pages[idx];
      if (!pg) return;
      pg.notes = val;
      this._markDirty();
    },
    _refreshPagesModal: function () {
      var list = document.getElementById("bd-pages-list");
      if (!list) return;
      list.innerHTML = this._pagesModalRows();
    },
    _showPagesModal: function () {
      var html = '<div class="modal-overlay bd-pg-overlay" onclick="if(event.target===this)this.remove()">' +
        '<div class="modal-content" style="max-width:560px;">' +
          '<h3 style="margin:0 0 4px;font-size:18px;font-weight:700;"><i class="fas fa-folder"></i> Pages</h3>' +
          '<p style="font-size:12px;color:var(--color-content-text-secondary);margin:0 0 14px;">Re-order, duplicate or delete pages. Page-link widgets follow their target automatically.</p>' +
          '<div id="bd-pages-list" style="display:flex;flex-direction:column;gap:8px;max-height:340px;overflow:auto;">' + this._pagesModalRows() + '</div>' +
          '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:16px;">' +
            '<button class="btn btn-sm btn-secondary" onclick="BlankDash._addPage()"><i class="fas fa-plus"></i> Add page</button>' +
            '<button class="btn btn-sm btn-ghost modal-close" onclick="this.closest(\'.modal-overlay\').remove()">Close</button>' +
          '</div>' +
        '</div>' +
      '</div>';
      document.body.insertAdjacentHTML("beforeend", html);
    },

    /* ── Widget templates ── */
    _listTemplates: function () {
      try { return JSON.parse(localStorage.getItem(_TEMPLATE_KEY) || "[]"); } catch (e) { return []; }
    },
    _storeTemplates: function (list) {
      try { localStorage.setItem(_TEMPLATE_KEY, JSON.stringify(list.length ? list : [])); } catch (e) {}
    },
    _saveAsTemplate: function (id) {
      var w = _findWidget(id);
      if (!w) return;
      var info = _lookup(w.type) || {};
      var t = {
        key: "t" + Date.now().toString(36) + "_" + (++_seq),
        type: w.type, title: w.title || info.label || w.type,
        width: w.width, height: w.height,
        config: JSON.parse(JSON.stringify(w.config || {})),
        icon: info.icon || "fa-chart-simple", color: info.color || "#2563eb", time: Date.now()
      };
      var list = this._listTemplates();
      list.push(t);
      this._storeTemplates(list);
      App.success("Saved \"" + t.title + "\" as a template");
    },
    _deleteTemplate: function (key, btn) {
      this._storeTemplates(this._listTemplates().filter(function (t) { return t.key !== key; }));
      if (btn) { var item = btn.closest(".bd-tpl-item"); if (item) item.remove(); }
      App.info("Template removed");
    },
    _insertTemplate: function (key, close) {
      var t = null;
      this._listTemplates().forEach(function (x) { if (x.key === key) t = x; });
      if (!t) { App.warning("Template not found"); return; }
      var dim = { width: t.width || 600, height: t.height || 300 };
      var pos = this._nextFreePos(dim);
      var widget = {
        id: "w" + Date.now().toString(36) + "_" + (++_seq),
        type: t.type, title: t.title,
        x: pos.x, y: pos.y, width: dim.width, height: dim.height,
        zIndex: _currentWidgets().length + 1, locked: false,
        plain: t.type === "text",
        config: JSON.parse(JSON.stringify(t.config || {}))
      };
      _currentWidgets().push(widget);
      this._initGrid();
      this._selectWidget(widget.id);
      this._refreshHistory();
      if (close !== false) { var ov = document.querySelector(".bd-tpl-overlay"); if (ov) ov.remove(); }
      App.success("Inserted template \"" + t.title + "\"");
    },

    /* ── Measures registry (persistent calculation layer) ── */
    _loadMeasures: async function () {
      try {
        var res = await API.measures();
        _measures = (res && res.measures) || [];
        window.App.measures = _measures;
      } catch (e) {
        _measures = (window.App && App.measures) || [];
      }
      if (_dash.mode !== "present") {
        var lr = document.querySelector(".bd-leftrail");
        if (lr) {
          var tmp = document.createElement("div");
          tmp.innerHTML = "<div>" + _renderLeftRail() + "</div>";
          lr.replaceWith(tmp.firstElementChild);
        }
      }
      return _measures;
    },
    _openMeasureModal: function (id) {
      var existing = id ? _measureById(id) : null;
      var tables = [];
      try { tables = Object.keys(App.state.tables || {}); } catch (e) {}
      tables.sort();
      var tableOpts = tables.map(function (t) {
        return '<option value="' + _esc(t) + '"' + (existing && existing.table_name === t ? " selected" : "") + '>' + _esc(t) + "</option>";
      }).join("");
      var cols = (existing ? _fieldColumns(existing.table_name) : (tables[0] ? _fieldColumns(tables[0]) : []));
      var numericCols = cols.filter(_isNumericColumn);
      var measureCols = numericCols.length ? numericCols : cols;
      var colOpts = measureCols.map(function (c, i) {
        return '<option value="' + _esc(c.name) + '"' + (i === 0 && !existing ? " selected" : "") + '>' + _esc(c.name) + "</option>";
      }).join("")
        || '<option value="">— no columns —</option>';
      var dateCols = cols.filter(function (c) {
        var ct = (c.type || (c.column_type || "")).toLowerCase();
        return ct.indexOf("date") !== -1 || ct.indexOf("timestamp") !== -1 || ct.indexOf("time") !== -1;
      });
      var dateOpts = ['<option value="">— none (plain total) —</option>'].concat(dateCols.map(function (c) {
        return '<option value="' + _esc(c.name) + '"' + (existing && existing.date_column === c.name ? " selected" : "") + '>' + _esc(c.name) + "</option>";
      })).join("");
      var fmtOpts = ["auto", "currency", "number", "percent", "compact", "plain"].map(function (f) {
        return '<option value="' + f + '"' + (existing && existing.format === f ? " selected" : "") + '>' + f + "</option>";
      }).join("");
      var html = '<div class="bd-gal-overlay bd-measure-modal" onclick="if(event.target===this)this.remove()">' +
        '<div class="bd-gal-modal" style="max-width:540px;">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:17px;font-weight:700;"><i class="fas fa-calculator"></i> ' + (existing ? "Edit Measure" : "New Measure") + '</h3>' +
          '<button class="btn btn-sm btn-ghost" onclick="this.closest(\'.bd-gal-overlay\').remove()">&times;</button></div>' +
          '<div class="bd-measure-form" style="padding:14px 16px;display:grid;gap:10px;max-height:62vh;overflow:auto;">' +
            '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Name</label>' +
              '<input type="text" id="bd-meas-name" value="' + _esc(existing ? existing.name : "") + '" placeholder="e.g. Total Revenue"></div>' +
            '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Source table</label>' +
              '<select id="bd-meas-table" onchange="BlankDash._measureModalCols(this.value)">' + (tableOpts || '<option value="">— no tables —</option>') + '</select></div>' +
            '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Aggregation</label>' +
              '<select id="bd-meas-agg" onchange="BlankDash._measureExprSync()">' +
                '<option value="SUM">SUM</option><option value="AVG">AVG</option><option value="MIN">MIN</option><option value="MAX">MAX</option><option value="COUNT">COUNT(*)</option>' +
                '<option value="custom"' + (existing ? " selected" : "") + '>Custom expression</option>' +
              '</select></div>' +
            '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Column</label>' +
              '<select id="bd-meas-col" onchange="BlankDash._measureExprSync()">' + (colOpts || '<option value="">—</option>') + '</select></div>' +
            '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Expression</label>' +
              '<textarea id="bd-meas-expr" rows="2" style="font-family:monospace;font-size:12px" placeholder="SUM(revenue)" oninput="BlankDash._measureExprDirty()">' + _esc(existing ? existing.expression : "") + '</textarea></div>' +
            '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Date column <span style="font-weight:400;color:var(--color-text-muted)">(enables YTD/MTD/QTD/MoM)</span></label>' +
              '<select id="bd-meas-date">' + dateOpts + '</select></div>' +
            '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Format</label><select id="bd-meas-fmt">' + fmtOpts + '</select></div>' +
            '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Description</label>' +
              '<input type="text" id="bd-meas-desc" value="' + _esc(existing ? (existing.description || "") : "") + '"></div>' +
            (existing
              ? '<div class="bd-props-field" style="display:grid;gap:4px"><label class="bd-props-label">Preview</label>' +
                  '<div style="display:flex;gap:6px;align-items:center">' +
                  '<select id="bd-meas-prev-mode" style="flex:1">' + ["none", "ytd", "mtd", "qtd", "mom"].map(function (mm) { return '<option value="' + mm + '">' + mm.toUpperCase() + '</option>'; }).join("") + '</select>' +
                  '<button type="button" class="btn btn-sm btn-secondary" onclick="BlankDash._previewMeasure(\'' + _esc(id) + '\')"><i class="fas fa-play"></i> Run</button></div>' +
                  '<div id="bd-meas-preview" style="margin-top:8px;font-size:12px;color:var(--color-text-muted)"></div></div>'
              : '') +
          '</div>' +
          '<div class="bd-gal-foot" style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--color-card-border,var(--color-border))">' +
            '<button type="button" class="btn btn-sm btn-ghost" onclick="this.closest(\'.bd-gal-overlay\').remove()">Cancel</button>' +
            '<button type="button" class="btn btn-sm btn-primary" onclick="BlankDash._saveMeasure(\'' + _esc(id || "") + '\')"><i class="fas fa-check"></i> Save</button>' +
          '</div>' +
        '</div></div>';
      document.body.insertAdjacentHTML("beforeend", html);
      this._measureExprSync();
    },
    /* Repopulate a measure modal's column + date-column selects for a table */
    _measureModalCols: function (table) {
      var cols = _fieldColumns(table || "");
      var colSel = document.getElementById("bd-meas-col");
      if (colSel) {
        var numCols = cols.filter(_isNumericColumn);
        var measureCols = numCols.length ? numCols : cols;
        colSel.innerHTML = (measureCols.map(function (c, i) {
          return '<option value="' + _esc(c.name) + '"' + (i === 0 ? " selected" : "") + '>' + _esc(c.name) + "</option>";
        }).join("")) || '<option value="">— no columns —</option>';
      }
      var dateSel = document.getElementById("bd-meas-date");
      if (dateSel) {
        var dcols = cols.filter(function (c) {
          var ct = (c.type || (c.column_type || "")).toLowerCase();
          return ct.indexOf("date") !== -1 || ct.indexOf("timestamp") !== -1 || ct.indexOf("time") !== -1;
        });
        dateSel.innerHTML = ['<option value="">— none (plain total) —</option>'].concat(
          dcols.map(function (c) { return '<option value="' + _esc(c.name) + '">' + _esc(c.name) + "</option>"; })).join("");
      }
      this._measureExprSync();
    },
    _measureExprDirty: function () {
      var el = document.getElementById("bd-meas-expr");
      if (el) el.dataset.custom = "1";
    },
    _measureExprSync: function () {
      var aggSel = document.getElementById("bd-meas-agg");
      var colSel = document.getElementById("bd-meas-col");
      var exprEl = document.getElementById("bd-meas-expr");
      if (!aggSel || !colSel || !exprEl) return;
      if (aggSel.value === "custom") { exprEl.dataset.custom = "1"; return; }
      if (aggSel.value === "COUNT") { exprEl.value = "COUNT(*)"; exprEl.dataset.custom = ""; return; }
      var col = colSel.value;
      if (!col) { exprEl.value = ""; return; }
      exprEl.value = aggSel.value + "(" + col + ")";
      exprEl.dataset.custom = "";
    },
    _saveMeasure: async function (id) {
      var val = function (sel) { var el = document.getElementById(sel); return el ? el.value : ""; };
      var name = val("bd-meas-name").trim();
      var table = val("bd-meas-table").trim();
      var expr = val("bd-meas-expr").trim();
      if (!name) { App.warning("Measure name is required"); return; }
      if (!table) { App.warning("Source table is required"); return; }
      if (!expr) { App.warning("Expression is required"); return; }
      var payload = {
        name: name, table_name: table, expression: expr,
        date_column: val("bd-meas-date").trim(),
        format: val("bd-meas-fmt") || "auto",
        description: val("bd-meas-desc").trim()
      };
      try {
        if (id) await API.updateMeasure(id, payload);
        else await API.createMeasure(payload);
      } catch (e) { App.error((e && e.message) || "Could not save measure"); return; }
      var ov = document.querySelector(".bd-measure-modal");
      if (ov) ov.remove();
      await this._loadMeasures();
      App.success(id ? "Measure updated" : "Measure registered");
    },
    _deleteMeasure: async function (id) {
      var m = _measureById(id);
      var self = this;
      var go = function () {
        API.deleteMeasure(id).then(function () {
          self._loadMeasures().catch(function () {});
          App.success("Measure deleted");
        }).catch(function (e) { App.error((e && e.message) || "Delete failed"); });
      };
      if (window.Modal && window.Modal.confirm) {
        window.Modal.confirm("Delete measure?", m ? ('"' + m.name + '" will be removed. KPIs already on the canvas keep their own expression copy.') : "Delete this measure?", { variant: "danger", confirmLabel: "Delete" }).then(function (ok) { if (ok) go(); });
      } else { go(); }
    },
    _previewMeasure: async function (id) {
      var modeSel = document.getElementById("bd-meas-prev-mode");
      var out = document.getElementById("bd-meas-preview");
      if (!modeSel || !out) return;
      var mode = modeSel.value || "none";
      out.innerHTML = '<i class="fas fa-spinner fa-spin"></i> running...';
      try {
        var p = await API.previewMeasure(id, mode);
        out.innerHTML = _measurePreviewHtml(p);
      } catch (e) {
        out.innerHTML = '<span style="color:var(--color-danger)">' + _esc((e && e.message) || "preview failed") + "</span>";
      }
    },
    /* Drop a registered measure onto the canvas as a time-intelligence KPI */
    _measureAddKpi: function (mid, mode) {
      var m = _measureById(mid);
      if (!m) { App.warning("Measure not found — refresh the dashboard"); return; }
      var table = m.table_name;
      var tm = (mode || "ytd");
      if (!m.date_column) tm = "none";
      var built = window.BITimeIntel ? window.BITimeIntel.build(m.expression, table, m.date_column || "", tm) : null;
      var dim = { width: 220, height: 120 };
      var pos = this._nextFreePos(dim);
      var cfg = {
        table: table,
        measureExpr: m.expression,
        measureId: m.id,
        measureName: m.name,
        measureFormat: m.format || "auto",
        timeIntel: tm,
        dateCol: m.date_column || "",
        format: m.format || "auto",
        label: m.name,
        periodLabel: "",
        sql: built ? built.sql : ("SELECT " + m.expression + " AS value, NULL AS previous, NULL AS comparison FROM analytics." + _qi(table)),
        sparkSql: built ? (built.spark_sql || "") : ""
      };
      var widget = {
        id: "w" + Date.now().toString(36) + "_" + (++_seq),
        type: "prevkpi", title: m.name,
        x: pos.x, y: pos.y, width: dim.width, height: dim.height,
        zIndex: _currentWidgets().length + 1, locked: false, showTitle: false,
        config: cfg
      };
      _currentWidgets().push(widget);
      this._initGrid();
      this._selectWidget(widget.id);
      this._refreshHistory();
      this._markDirty();
      App.success('Added "' + m.name + '" ' + (tm === "none" ? "KPI" : tm.toUpperCase() + " KPI"));
    },

    /* P1.2 — metric switcher: swap the measure driving a KPI in place.
       Keeps the selected period, rebuilds sql/sparkSql, refetches, persists. */
    switchMeasure: function (id, measureId) {
      var w = _findWidget(id);
      if (!w) return;
      var mLst = (window.App && App.measures) || [];
      var m = null;
      for (var i = 0; i < mLst.length; i++) if (String(mLst[i].id) === String(measureId)) { m = mLst[i]; break; }
      if (!m || !(w.config || {}).measureExpr) return;
      if ((w.config.measureId || "") === String(measureId)) return;
      var c = w.config;
      var tm = c.timeIntel || "ytd";
      if (!m.date_column) tm = "none";
      var built = window.BITimeIntel ? window.BITimeIntel.build(m.expression, m.table_name, m.date_column || "", tm) : null;
      c.measureId = String(measureId);
      c.measureName = m.name;
      c.measureExpr = m.expression;
      c.measureFormat = m.format || "auto";
      c.table = m.table_name;
      c.dateCol = m.date_column || "";
      c.timeIntel = tm;
      c.format = m.format || "auto";
      c.label = m.name;
      c.periodLabel = built && built.mode !== "none" ? ("vs " + (built.prev_label || "prior period")) : "";
      c.sql = built ? built.sql : ("SELECT " + m.expression + " AS value, NULL AS previous, NULL AS comparison FROM analytics." + _qi(m.table_name));
      c.sparkSql = built ? (built.spark_sql || "") : "";
      delete c.sparklineData; delete c.labels; delete c.data; delete c.anchor;
      this._refreshWidget(id);
      this._markDirty();
      App.success('Switched to "' + m.name + '"');
    },

    _showTemplatesModal: function () {
      var list = this._listTemplates();
      var items = list.length
        ? list.map(function (t) {
            return '<div class="bd-tpl-item" data-key="' + _esc(t.key) + '">' +
              '<div class="bd-tpl-icon" style="background:' + _esc(t.color) + '"><i class="fas ' + t.icon + '"></i></div>' +
              '<div class="bd-tpl-info">' +
                '<div class="bd-tpl-name">' + _esc(t.title) + '</div>' +
                '<div class="bd-tpl-meta">' + _esc(t.type) + ' · ' + Math.round(t.width) + "×" + Math.round(t.height) + "px</div>" +
              '</div>' +
              '<div class="bd-tpl-actions">' +
                '<button class="btn btn-sm btn-secondary" onclick="BlankDash._insertTemplate(\'' + t.key + '\')"><i class="fas fa-plus"></i> Insert</button>' +
                '<button class="btn btn-sm btn-ghost" title="Delete template" onclick="event.stopPropagation();BlankDash._deleteTemplate(\'' + t.key + '\', this)"><i class="fas fa-trash"></i></button>' +
              '</div>' +
            '</div>';
          }).join("")
        : '<div class="bd-gal-empty">No templates yet — select a widget, open its ⋯ menu and choose "Save as template".</div>';
      var html = '<div class="bd-gal-overlay bd-tpl-overlay" onclick="if(event.target===this)this.remove()">' +
        '<div class="bd-gal-modal">' +
          '<div class="bd-gal-head">' +
            '<h3 style="margin:0;font-size:17px;font-weight:700;"><i class="fas fa-bookmark"></i> Widget Templates</h3>' +
            '<button class="btn btn-sm btn-ghost" onclick="this.closest(\'.bd-gal-overlay\').remove()">&times;</button>' +
          '</div>' +
          '<div class="bd-gal-list">' + items + '</div>' +
        '</div>' +
      '</div>';
      document.body.insertAdjacentHTML("beforeend", html);
    },

    /* ── Bookmarks navigator (P1.3) ──
       Named snapshots of the current page's canvas. Saved inside the
       dashboard layout (so they persist with it), applied by swapping the
       widget list and re-mounting the grid. */
    _listBookmarks: function () {
      return ((_dash && _dash._bookmarks) || []).slice();
    },
    _storeBookmarks: function (list) {
      if (!_dash) return;
      _dash._bookmarks = list;
      this._markDirty();
    },
    _saveBookmark: function (name) {
      name = (name || "").trim();
      if (!name) { App.warning("Give the bookmark a name"); return; }
      var list = this._listBookmarks();
      var snap = JSON.parse(JSON.stringify(_currentWidgets()));
      var bm = {
        id: "bm" + Date.now().toString(36),
        name: name,
        pageId: _dash.currentPage,
        at: Date.now(),
        widgets: snap,
        filters: JSON.parse(JSON.stringify(window.CrossFilterBus ? window.CrossFilterBus.getFilters() : _filters || {}))
      };
      list.push(bm);
      this._storeBookmarks(list);
      App.success('Bookmark "' + name + '" saved');
      this._showBookmarksModal();
      return bm.id;
    },
    _applyBookmark: function (id, silent) {
      var list = this._listBookmarks();
      var bm = null;
      for (var i = 0; i < list.length; i++) if (list[i].id === id) { bm = list[i]; break; }
      if (!bm || !Array.isArray(bm.widgets)) return;
      var snap = JSON.parse(JSON.stringify(bm.widgets));
      if (bm.pageId) {
        var curId = _dash.pages[_dash.currentPage] ? _dash.pages[_dash.currentPage].id : null;
        if (curId !== bm.pageId) {
          _storePageFilters(_dash.currentPage, _pageFilterSnapshot());
          var bIdx = -1;
          for (var pi = 0; pi < _dash.pages.length; pi++) if (_dash.pages[pi].id === bm.pageId) { bIdx = pi; break; }
          if (bIdx < 0) bIdx = 0;
          _dash.currentPage = bIdx;
          _renderPageTabs();
        }
      }
      var arr = _currentWidgets();
      /* Exact-restore semantics (Power BI bookmarks): the page's widget list
         becomes the captured snapshot, in the captured order. */
      arr.length = 0;
      snap.forEach(function (bw) {
        if (!bw || !bw.id) return;
        arr.push(JSON.parse(JSON.stringify(bw)));
      });
      if (window.CrossFilterBus) {
        var fl = bm.filters || {};
        window.CrossFilterBus.clear();
        Object.keys(fl).forEach(function (k) {
          if (fl[k] != null) window.CrossFilterBus.dispatch(k, fl[k], "bookmark");
        });
      }
      if (silent && _dash.mode === "present") {
        this._initGrid();
        return;
      }
      this._markDirty();
      this._initGrid();
      this._refreshHistory();
      App.success('Applied "' + bm.name + '"');
    },
    _deleteBookmark: function (id) {
      this._storeBookmarks(this._listBookmarks().filter(function (b) { return b.id !== id; }));
      this._showBookmarksModal();
    },
    _showBookmarksModal: function () {
      var self = this;
      var list = this._listBookmarks();
      var items = list.length
        ? list.map(function (b) {
            var n = (b.widgets || []).length;
            var when = new Date(b.at).toLocaleString();
            return '<div class="bd-tpl-item">' +
              '<div class="bd-tpl-icon" style="background:var(--color-text)"><i class="fas fa-bookmark"></i></div>' +
              '<div class="bd-tpl-info">' +
                '<div class="bd-tpl-name">' + _esc(b.name) + '</div>' +
                '<div class="bd-tpl-meta">' + n + " widget" + (n === 1 ? "" : "s") + " · " + _esc(when) + "</div>" +
              '</div>' +
              '<div class="bd-tpl-actions">' +
                '<button class="btn btn-sm btn-secondary" onclick="BlankDash._applyBookmark(\'' + _esc(b.id) + '\');this.closest(\'.bd-gal-overlay\').remove()"><i class="fas fa-arrow-right"></i> Apply</button>' +
                '<button class="btn btn-sm btn-ghost" title="Delete bookmark" onclick="event.stopPropagation();BlankDash._deleteBookmark(\'' + _esc(b.id) + '\')"><i class="fas fa-trash"></i></button>' +
              '</div>' +
            '</div>';
          }).join("")
        : '<div class="bd-gal-empty">No bookmarks yet — “Save current view” to snapshot the canvas, then revisit it any time.</div>';
      var html = '<div class="bd-gal-overlay bd-tpl-overlay" onclick="if(event.target===this)this.remove()">' +
        '<div class="bd-gal-modal">' +
          '<div class="bd-gal-head">' +
            '<h3 style="margin:0;font-size:17px;font-weight:700;"><i class="fas fa-bookmark"></i> Bookmarks</h3>' +
            '<button class="btn btn-sm btn-ghost" onclick="this.closest(\'.bd-gal-overlay\').remove()">&times;</button>' +
          '</div>' +
          '<div style="display:flex;gap:8px;padding:10px 14px;border-bottom:1px solid var(--bd-border,#e8e6f0);">' +
            '<input type="text" id="bd-bookmark-name" placeholder="Save current view as…" style="flex:1;min-width:0;padding:6px 10px;border:1px solid var(--bd-border,#e8e6f0);border-radius:8px;font-size:12px;" onkeydown="if(event.key===\'Enter\')BlankDash._saveBookmark(this.value)">' +
            '<button class="btn btn-sm btn-primary" onclick="BlankDash._saveBookmark(document.getElementById(\'bd-bookmark-name\').value)"><i class="fas fa-save"></i> Save</button>' +
          '</div>' +
          '<div class="bd-gal-list">' + items + '</div>' +
        '</div>' +
      '</div>';
      document.body.insertAdjacentHTML("beforeend", html);
    },

    /* ── Drag & Drop ── */
    _setupDragDrop: function () {
      var palette = document.getElementById("bd-palette");
      var canvas = document.getElementById("bd-canvas");
      if (!palette || !canvas) return;
      palette.querySelectorAll(".bd-palette-item").forEach(function (item) {
        item.addEventListener("dragstart", function (e) {
          e.dataTransfer.setData("text/plain", item.getAttribute("data-type"));
          e.dataTransfer.effectAllowed = "copy";
          item.classList.add("dragging");
          item.__bdDragged = true;
        });
        item.addEventListener("dragend", function () {
          item.classList.remove("dragging");
          var self = item;
          setTimeout(function () { self.__bdDragged = false; }, 60);
        });
        item.addEventListener("click", function (e) {
          if (item.__bdDragged || e.defaultPrevented) return;
          var type = item.getAttribute("data-type");
          if (type) BlankDash._addWidget(type);
        });
      });
      canvas.addEventListener("dragover", function (e) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
      canvas.addEventListener("drop", function (e) {
        e.preventDefault();
        var raw = e.dataTransfer.getData("text/plain") || "";
        /* Measure KPI drop: register a measure-backed KPI widget. */
        if (raw.indexOf("bd-measure||") === 0) {
          var mid = raw.split("||")[1];
          if (mid) BlankDash._measureAddKpi(mid);
          return;
        }
        /* Field chip drag: drops on a widget bind the field, else add a widget. */
        if (raw.indexOf("bd-field||") === 0) {
          var parts = raw.split("||");
          var t = decodeURIComponent(parts[1] || ""), f = decodeURIComponent(parts[2] || ""), k = parts[3] || "dim";
          if (!t || !f) return;
          var wWrap = e.target && e.target.closest ? e.target.closest(".ff-widget[data-id]") : null;
          if (wWrap) { BlankDash._bindFieldToWidget(wWrap.getAttribute("data-id"), t, f, k); return; }
          BlankDash._addFieldWidget(t, f, k);
          return;
        }
        var type = raw;
        if (type) BlankDash._addWidget(type);
      });
      /* Delegated drag handlers for the Fields pane chips (bound once). */
      if (!window.__bdFieldDragBound) {
        window.__bdFieldDragBound = true;
        var dragStart = function (e) {
          var chip = e.target && e.target.closest ? e.target.closest(".bd-field") : null;
          if (!chip || !chip.draggable) return;
          e.dataTransfer.setData("text/plain", "bd-field||" + encodeURIComponent(chip.getAttribute("data-table") || "") + "||" + encodeURIComponent(chip.getAttribute("data-field") || "") + "||" + (chip.getAttribute("data-kind") || "dim"));
          e.dataTransfer.effectAllowed = "copy";
          chip.classList.add("dragging");
        };
        var dragEnd = function (e) {
          var chip = e.target && e.target.closest ? e.target.closest(".bd-field") : null;
          if (chip) chip.classList.remove("dragging");
          var mrow = e.target && e.target.closest ? e.target.closest(".bd-lr-measure") : null;
          if (mrow) mrow.classList.remove("dragging");
        };
        var measureDragStart = function (e) {
          var row = e.target && e.target.closest ? e.target.closest(".bd-lr-measure") : null;
          if (!row || !row.draggable) return;
          e.dataTransfer.setData("text/plain", "bd-measure||" + (row.getAttribute("data-measure-id") || ""));
          e.dataTransfer.effectAllowed = "copy";
          row.classList.add("dragging");
        };
        document.addEventListener("dragstart", dragStart);
        document.addEventListener("dragstart", measureDragStart);
        document.addEventListener("dragend", dragEnd);
      }
    },

    /* ── Add Widget ── */
    _addWidget: function (type, sizeKey) {
      var table = _dash.table;
      if (!table && type !== "text" && type !== "image" && type !== "custom" && type !== "page" && type !== "shape" && type !== "divider") { App.warning("Please select a data source first"); return; }
      var info = _lookup(type);
      var id = "w" + Date.now().toString(36) + "_" + (++_seq);
      var s = info.size;
      var title = info.label;
      var dim = _toPx(s[0], s[1]);
      var k = sizeKey || _addSizeKey || "m";
      if (k === "s") dim = { width: Math.max(80, Math.round(dim.width * 0.66)), height: Math.max(60, Math.round(dim.height * 0.66)) };
      else if (k === "l") dim = { width: Math.round(dim.width * 1.5), height: Math.round(dim.height * 1.5) };

      var sql = _defaultSql(type, table);
      var filterField = _detectFilterField(table, type);
      var config = { table: table, sql: sql, label: title, filterField: filterField || title };
      if (type === "image") config.src = "";
      if (type === "custom") config.html = "<div style=\"padding:12px;font-size:13px;color:var(--color-text,#0f172a)\"><b>" + title + "</b><br>Insert your custom HTML, CSS or a data-driven template here.</div>";
      if (type === "page") config.targetPage = (_dash.currentPage + 1 < _dash.pages.length) ? _dash.currentPage + 1 : 0;
      if (type === "button") { config.action = "reset"; config.value = ""; }
      if (type === "text") config.content = "Double-click to edit this text.";
      if (type === "shape") config.shapeShape = "rect";
      if (type === "metriccard") {
        config.measures = [];
        try {
          (_measures || []).slice(0, 4).forEach(function (m) {
            if (m && m.expression) config.measures.push({ id: m.id, label: m.name || m.id, expr: m.expression, timeIntel: m.date_column ? "ytd" : "none", format: m.format || "auto" });
          });
        } catch (e) {}
      }
      if (type === "divider") config.dividerColor = "#cbd5e1";

      if (_CHART_SUBTYPES.indexOf(type) !== -1) {
        config.chartType = type === "horizontalbar" ? "bar" : type === "stackedbar" ? "bar" : type;
        if (type === "horizontalbar") config.indexAxis = "y";
        if (type === "stackedbar") config.stacked = true;
        if (type === "stackedarea") config.stacked = true;
        if (type === "percentbar") { config.stacked = true; config.percent = true; }
      }

      var pos = this._nextFreePos(dim);
      var widget = {
        id: id, type: "chart", title: title,
        x: pos.x, y: pos.y,
        width: dim.width, height: dim.height,
        zIndex: _currentWidgets().length + 1, locked: false,
        showTitle: type !== "shape",
        plain: type === "text" || type === "divider",
        responsive: "auto",
        config: config
      };
      if (_CHART_SUBTYPES.indexOf(type) === -1) {
        widget.type = type;
      }

      var emptyEl = document.getElementById("bd-empty-state");
      if (emptyEl) emptyEl.remove();
      _currentWidgets().push(widget);
      this._initGrid();
      this._selectWidget(id);
      this._refreshHistory();
      this._markDirty();
      App.success("Added " + title);
    },

    /* Find the next free top-left position so new widgets don't fully overlap */
    _nextFreePos: function (size) {
      var used = _currentWidgets();
      var gridStep = _UNIT_W;
      var cw = _baseCanvasW();
      var chh = _baseCanvasH();
      var placed = used.map(function (w) {
        return { x: w.x, y: w.y, w: w.width + _GAP, h: w.height + _GAP };
      });
      var maxCol = Math.max(0, Math.floor((cw - size.width) / gridStep));
      var maxRow = Math.max(0, Math.floor((chh - size.height) / Math.ceil(_UNIT_H)));
      for (var row = 0; row <= maxRow; row++) {
        for (var col = 0; col <= maxCol; col++) {
          var x = col * gridStep, y = row * Math.ceil(_UNIT_H);
          var fits = true;
          for (var i = 0; i < placed.length; i++) {
            var p = placed[i];
            if (x < p.x + p.w && x + size.width > p.x && y < p.y + p.h && y + size.height > p.y) { fits = false; break; }
          }
          if (fits) return { x: x, y: y };
        }
      }
      // No free cell inside the canvas: pick the in-bounds cell with the least
      // overlap so the widget stays on-canvas instead of drifting off it.
      var best = null, bestOv = Infinity;
      for (var r = 0; r <= maxRow; r++) {
        for (var c = 0; c <= maxCol; c++) {
          var cx = c * gridStep, cy = r * Math.ceil(_UNIT_H);
          var ov = 0;
          for (var j = 0; j < placed.length; j++) {
            var q = placed[j];
            var ox = Math.max(0, Math.min(cx + size.width, q.x + q.w) - Math.max(cx, q.x));
            var oy = Math.max(0, Math.min(cy + size.height, q.y + q.h) - Math.max(cy, q.y));
            ov += ox * oy;
          }
          if (ov < bestOv) { bestOv = ov; best = { x: cx, y: cy }; }
        }
      }
      var x0 = 16 + Math.round(Math.max(140, Math.min(200, cw * 0.095)));
      return best || { x: x0, y: Math.max(0, chh - size.height) };
    },
    _hasFreePos: function (size) {
      var used = _currentWidgets();
      var gridStep = _UNIT_W;
      var cw = _baseCanvasW();
      var chh = _baseCanvasH();
      var placed = used.map(function (w) {
        return { x: w.x, y: w.y, w: w.width + _GAP, h: w.height + _GAP };
      });
      var maxCol = Math.max(0, Math.floor((cw - size.width) / gridStep));
      var maxRow = Math.max(0, Math.floor((chh - size.height) / Math.ceil(_UNIT_H)));
      for (var row = 0; row <= maxRow; row++) {
        for (var col = 0; col <= maxCol; col++) {
          var x = col * gridStep, y = row * Math.ceil(_UNIT_H);
          var fits = true;
          for (var i = 0; i < placed.length; i++) {
            var p = placed[i];
            if (x < p.x + p.w && x + size.width > p.x && y < p.y + p.h && y + size.height > p.y) { fits = false; break; }
          }
          if (fits) return true;
        }
      }
      return false;
    },

    _refreshSidebars: function () {
      if (!window.AIBuild || !AIBuild.sidebarNavWidget || !_dash || !_dash.pages) return;
      var names = _dash.pages.map(function (p) { return p.name; });
      _dash.pages.forEach(function (pg, pi) {
        pg.widgets.forEach(function (w) {
          if (w && w.config && w.config.sidebar && w.title === "Page Navigator") {
            var aiTheme = _aiNavTheme(w);
            var nb = AIBuild.sidebarNavWidget(names, pi, {
              x: w.x, y: w.y, width: w.width, height: w.height,
              index: (w.zIndex || 1000) - 1000, accent: aiTheme.accent, accent2: aiTheme.accent2, theme: aiTheme.theme || {}, table: w.config.table || _dash.table
            });
            w.config.html = nb.config.html;
            w.config.label = nb.config.label;
          }
        });
      });
    },

    /* ── Add modal ── */
    _addSize: function () { return _addSizeKey; },

    _setAddSize: function (k, el) {
      _addSizeKey = k;
      if (el) {
        var wrap = el.parentElement;
        Array.prototype.forEach.call(wrap.querySelectorAll("button"), function (b) {
          b.classList.remove("btn-primary"); b.classList.add("btn-ghost");
        });
        el.classList.remove("btn-ghost"); el.classList.add("btn-primary");
      }
    },

    _showAddModal: function () {
      if (!_dash.table) { App.warning("Please select a data source first"); return; }
      var cats = {};
      _CATALOGUE.forEach(function (item) {
        if (!cats[item.cat]) cats[item.cat] = [];
        cats[item.cat].push(item);
      });
      var html = '<div class="modal-overlay" onclick="if(event.target===this)this.remove()">' +
        '<div class="modal-content wide">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">' +
            '<h3 style="margin:0;font-size:18px;font-weight:700;">Add Widget <span style="font-weight:400;font-size:13px;color:var(--color-content-text-secondary);">(' + _CATALOGUE.length + ' types)</span></h3>' +
            '<input type="text" class="form-input" style="width:200px;font-size:12px;" placeholder="Search types..." oninput="document.querySelectorAll(\'.bd-add-item\').forEach(function(el){el.style.display=(el.getAttribute(\'data-label\').toLowerCase().indexOf(this.value.toLowerCase())!==-1)?\'\':\'none\'}.bind(this))">' +
          '</div>' +
          '<div style="display:flex;align-items:center;gap:6px;margin-bottom:14px;">' +
            '<span style="font-size:12px;color:var(--color-content-text-secondary);">Default size:</span>' +
            '<button class="btn btn-sm base-chip' + (_addSizeKey === "s" ? " btn-primary" : " btn-ghost") + '" data-k="s" onclick="BlankDash._setAddSize(\'s\',this)">Small</button>' +
            '<button class="btn btn-sm base-chip' + (_addSizeKey === "m" ? " btn-primary" : " btn-ghost") + '" data-k="m" onclick="BlankDash._setAddSize(\'m\',this)">Medium</button>' +
            '<button class="btn btn-sm base-chip' + (_addSizeKey === "l" ? " btn-primary" : " btn-ghost") + '" data-k="l" onclick="BlankDash._setAddSize(\'l\',this)">Large</button>' +
            '<span style="font-size:11px;color:var(--color-content-text-secondary);">(applies when you click a widget type)</span>' +
          '</div>';
      Object.keys(cats).forEach(function (catName) {
        html += '<div class="bd-modal-section">';
        html += '<div class="bd-modal-section-title">' + catName + '</div>';
        html += '<div class="bd-add-grid">';
        cats[catName].forEach(function (item) {
          html += '<div class="bd-add-item" data-label="' + _esc(item.label) + '" onclick="BlankDash._addWidget(\'' + item.type + '\', BlankDash._addSize());this.closest(\'.modal-overlay\').remove()">' +
            '<div class="bd-add-icon" style="background:' + item.color + '"><i class="fas ' + item.icon + '"></i></div>' +
            '<div class="bd-add-label">' + item.label + '</div>' +
          '</div>';
        });
        html += '</div></div>';
      });
      html += '</div></div>';
      document.body.insertAdjacentHTML("beforeend", html);
    },

    /* ── AI Copilot Assistant (right-docked, chat-style) ────────────── */
    _toggleCopilot: function () {
      var p = document.getElementById("bd-copilot");
      if (!p) { this._renderCopilot(); return; }
      var open = p.classList.toggle("bd-copilot-open");
      var btn = document.querySelector(".ai-gen-btn");
      if (btn) btn.style.boxShadow = open ? "0 0 0 2px rgba(99,102,241,.5)" : "";
      if (open) {
        if (!_copilotHistoryLoaded) {
          _copilotHistory = _copilotLoadHistory();
          _copilotHistoryLoaded = true;
          this._copilotRenderThread();
        }
        var b = p.querySelector(".bd-copilot-body");
        if (b) b.scrollTop = b.scrollHeight;
        var ta = p.querySelector(".bd-copilot-input");
        if (ta && !_copilotBusy) ta.focus();
      }
    },
    _renderCopilot: function () {
      var self = this;
      if (!_copilotHistoryLoaded) {
        _copilotHistory = _copilotLoadHistory();
        _copilotHistoryLoaded = true;
      }
      var old = document.getElementById("bd-copilot");
      if (old) old.remove();
      var dock = document.createElement("div");
      dock.id = "bd-copilot";
      dock.className = "bd-copilot";
      dock.innerHTML =
        '<div class="bd-copilot-head">' +
          '<div class="bd-copilot-title"><i class="fas fa-robot"></i> AI Assistant</div>' +
          '<div class="bd-copilot-head-actions">' +
            '<button class="ff-widget-btn" onclick="BlankDash._copilotClear()" title="Clear conversation"><i class="fas fa-eraser"></i></button>' +
            '<button class="ff-widget-btn" onclick="BlankDash._toggleCopilot()" title="Close"><i class="fas fa-times"></i></button>' +
          '</div>' +
        '</div>' +
        '<div class="bd-copilot-sub">Ask about <b id="bd-copilot-table"></b>. Type a question or pick a quick action.</div>' +
        '<div class="bd-copilot-actions">' +
          '<button class="bd-copilot-action" data-act="generate"><i class="fas fa-wand-magic-sparkles"></i><span>Generate</span></button>' +
          '<button class="bd-copilot-action" data-act="explain"><i class="fas fa-file-lines"></i><span>Explain</span></button>' +
          '<button class="bd-copilot-action" data-act="insights"><i class="fas fa-lightbulb"></i><span>Insights</span></button>' +
          '<button class="bd-copilot-action" data-act="recommend"><i class="fas fa-chart-simple"></i><span>Visuals</span></button>' +
        '</div>' +
        '<div class="bd-copilot-body" id="bd-copilot-body"></div>' +
        '<div class="bd-copilot-input-row">' +
          '<textarea class="bd-copilot-input" id="bd-copilot-input" rows="1" placeholder="Ask about your data...  (Enter to send)"></textarea>' +
          '<button class="bd-copilot-send" id="bd-copilot-send" title="Send"><i class="fas fa-paper-plane"></i></button>' +
        '</div>';
      (document.querySelector(".bd-layout") || document.body).appendChild(dock);
      var tableEl = dock.querySelector("#bd-copilot-table");
      if (tableEl) tableEl.textContent = _dash.table || "no data source";
      var ta = dock.querySelector("#bd-copilot-input");
      var sendBtn = dock.querySelector("#bd-copilot-send");
      var send = function () { self._copilotSend(); };
      sendBtn.addEventListener("click", send);
      ta.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
      });
      dock.querySelectorAll(".bd-copilot-action").forEach(function (b) {
        b.addEventListener("click", function () { self._runCopilotAction(this.getAttribute("data-act")); });
      });
      this._copilotRenderThread();
      requestAnimationFrame(function () {
        dock.classList.add("bd-copilot-open");
        if (ta && !_copilotBusy) ta.focus();
      });
    },
    _copilotRenderThread: function () {
      var b = document.getElementById("bd-copilot-body");
      if (!b) return;
      var self = this;
      if (!_copilotHistory.length) {
        b.innerHTML =
          '<div class="bd-copilot-empty">' +
            '<i class="fas fa-wand-magic-sparkles" style="color:var(--color-primary);font-size:20px;display:block;margin:0 auto 10px;text-align:center"></i>' +
            'Ask anything about your data, get it explained, or let AI design your dashboard. Say it in your own words.' +
            '<div class="bd-copilot-suggs">' +
              '<button class="bd-copilot-chip" data-q="Summarize this dataset with the key numbers">Summarize dataset</button>' +
              '<button class="bd-copilot-chip" data-q="What are the key trends and top segments?">Trends and segments</button>' +
              '<button class="bd-copilot-chip" data-q="What should we focus on next?">Focus areas</button>' +
              '<button class="bd-copilot-chip" data-q="Suggest 3 useful charts for this data">Best charts</button>' +
            '</div>' +
          '</div>';
        b.querySelectorAll(".bd-copilot-chip").forEach(function (ch) {
          ch.addEventListener("click", function () { self._copilotChip(this); });
        });
        return;
      }
      var html = "";
      _copilotHistory.forEach(function (m) {
        var role = m.role === "user" ? "bd-copilot-msg-user" : "bd-copilot-msg-ai";
        html += '<div class="bd-copilot-msg ' + role + '">' +
          '<div class="bd-copilot-msg-body">' + (m.html != null ? m.html : _esc(m.text)) + '</div>' +
          (m.badge ? '<div class="bd-copilot-msg-badge">' + _esc(m.badge) + '</div>' : '') +
          (m.add ? '<div class="bd-copilot-actions-row"><button class="bd-copilot-add" onclick="BlankDash._copilotAddByUid(\'' + m.uid + '\')"><i class="fas fa-download"></i> Add to canvas</button></div>' : '') +
        '</div>';
      });
      b.innerHTML = html;
      b.scrollTop = b.scrollHeight;
      this._copilotHydrateVizs();
    },
    /* Plot a stored Plotly `spec` into `host` — spec-first, mirroring chat.js
       `_renderVizSpecs`. Returns true when the spec is handled (queued while
       the Plotly CDN is still loading), false so callers can fall back to the
       legacy html path. */
    _renderVizSpec: function (host, spec) {
      if (!host || !spec) return false;
      if (!window.Plotly) {
        var self = this;
        setTimeout(function () { self._renderVizSpec(host, spec); }, 80);
        return true;
      }
      try {
        Plotly.newPlot(host, spec.data || [], spec.layout || {}, spec.config || {});
        host.setAttribute("data-vizok", "1");
        return true;
      } catch (e) {
        return false;
      }
    },
    /* Re-inject a streamed chart after a thread re-render: specs plot via
       Plotly.newPlot, legacy html re-runs its inline script (Plotly.newPlot). */
    _copilotHydrateVizs: function () {
      var lib = _copilotVizLib;
      var self = this;
      Object.keys(lib).forEach(function (id) {
        var host = document.getElementById(id);
        if (!host || host.getAttribute("data-vizok")) return;
        var entry = lib[id];
        var html = typeof entry === "string" ? entry : (entry && entry.html) || "";
        var spec = (entry && entry.spec) || null;
        if (spec && self._renderVizSpec(host, spec)) return;
        if (!html) return;
        try {
          var tmp = document.createElement("div");
          tmp.innerHTML = html;
          while (tmp.firstChild) host.appendChild(tmp.firstChild);
          host.querySelectorAll("script").forEach(function (old) {
            var s = document.createElement("script");
            Array.prototype.forEach.call(old.attributes, function (a) { s.setAttribute(a.name, a.value); });
            var inline = !old.src && !old.getAttribute("src");
            if (inline && /Plotly/.test(old.text)) {
              s.text = "(function _waitPlotly(){ if (window.Plotly) { " + old.text + " } else { setTimeout(_waitPlotly, 60); } })();";
            } else {
              s.text = old.text;
              s.async = false;
            }
            old.replaceWith(s);
          });
          host.setAttribute("data-vizok", "1");
        } catch (e) { /* leave skeleton */ }
      });
    },
    _copilotVizToCanvas: function (vid) {
      var entry = _copilotVizLib[vid];
      if (typeof entry === "string") entry = { html: entry, spec: null };
      if (!entry || (!entry.html && !entry.spec)) { App.warning("That chart has expired — ask again."); return; }
      this._addCopilotWidget("custom", entry.html || "", "AI chart", entry.spec || null);
    },
    _copilotChip: function (btn) {
      if (_copilotBusy) return;
      var q = btn && btn.getAttribute ? btn.getAttribute("data-q") : "";
      var ta = document.getElementById("bd-copilot-input");
      if (ta) ta.value = q || "";
      this._copilotSend();
    },
    _copilotSend: function () {
      if (_copilotBusy) return;
      var ta = document.getElementById("bd-copilot-input");
      var question = (ta ? ta.value : "").trim();
      if (!question) { if (ta) ta.focus(); return; }
      this._copilotAppend("user", _esc(question));
      if (ta) ta.value = "";
      var intent = this._copilotIntent(question);
      if (intent === "generate") { this._runAIGenerate("", true); return; }
      else if (intent === "explain") { this._copilotExplain(); return; }
      else if (intent === "insights") { this._copilotInsights(); return; }
      else if (intent === "recommend") { this._copilotRecommend(); return; }
      _copilotBusy = true;
      this._setCopilotBusy(true);
      this._copilotStream(question);
    },
    _copilotIntent: function (q) {
      q = "  " + q.toLowerCase() + "  ";
      if (/generate|auto.?build|build (me |us |a |an )?(new |full |ai )?(dashboard|layout)|create a dashboard/.test(q) && !/explain|describe/.test(q)) return "generate";
      if (/explain|describe|what (does|is) |tell me about|walk (me )?through|how (does|is|are)/.test(q)) return "explain";
      if (/insight|finding|anomal|correlat|trend|interesting|focus on/.test(q)) return "insights";
      if (/recommend|suggest|which chart|what chart|best visual|pick (a |the )?chart|visuals? for/.test(q)) return "recommend";
      return "chat";
    },
    _copilotStream: async function (question) {
      var self = this;
      var table = _dash.table;
      var tables = table ? [table] : [];
      var history = _copilotHistory
        .filter(function (m) { return m.role === "user" || m.role === "assistant"; })
        .map(function (m) { return { role: m.role, content: m.text || "" }; });
      var body = document.getElementById("bd-copilot-body");
      var bubble = document.createElement("div");
      bubble.className = "bd-copilot-msg bd-copilot-msg-ai";
      bubble.innerHTML = '<div class="bd-copilot-msg-body" id="bd-copilot-stream"><div class="bd-copilot-loading"><i class="fas fa-spinner fa-spin"></i> Thinking...</div></div>';
      body.appendChild(bubble);
      body.scrollTop = body.scrollHeight;
      var streamEl = bubble.querySelector("#bd-copilot-stream");
      var streamed = "";
      var streamVizs = [];
      try {
        try {
          var gen = API.askStream(question, history, _copilotSession, tables);
          for await (var token of gen) {
            if (token && typeof token === "object") {
              if (token.__visualizations && Array.isArray(token.__visualizations)) {
                streamVizs = streamVizs.concat(token.__visualizations.filter(function (v) { return v && (v.html || v.spec); }));
              }
              continue;
            }
            streamed += token;
            streamEl.innerHTML = self._renderMarkdown(streamed);
            body.scrollTop = body.scrollHeight;
          }
          if (!streamed.trim()) throw new Error("Empty stream response");
        } catch (e1) {
          streamed = "";
          var ans = await API.ask(question, { tables: tables, sessionId: _copilotSession });
          if (ans && ans.session_id) _copilotSession = ans.session_id;
          streamed = (ans && ans.answer) || "No answer returned.";
          if (ans && Array.isArray(ans.visualizations)) {
            streamVizs = streamVizs.concat(ans.visualizations.filter(function (v) { return v && (v.html || v.spec); }));
          }
        }
        _copilotHistory.push({ role: "assistant", uid: _copilotMsgUid(), text: streamed, html: self._renderMarkdown(streamed), badge: "AI", add: { kind: "text", payload: streamed, label: "Chat answer" } });
        if (streamVizs.length) {
          self._copilotAppend("assistant", self._copilotVizBubbleHtml(streamVizs), "AI Charts");
        }
      } catch (e2) {
        _copilotHistory.push({ role: "assistant", uid: _copilotMsgUid(), text: "", html: '<div class="bd-copilot-empty">Sorry, I could not reach the assistant. Please try again.</div>', badge: "AI" });
      } finally {
        _copilotBusy = false;
        self._setCopilotBusy(false);
        self._copilotRenderThread();
        _copilotSaveHistory();
      }
    },
    _copilotVizBubbleHtml: function (vizs) {
      if (!vizs || !vizs.length) return '<div class="bd-copilot-empty">No charts.</div>';
      var out = '<div class="bd-copilot-result"><div class="bd-copilot-result-title"><i class="fas fa-chart-simple"></i> Visualizations — AI generated ' + vizs.length + ' chart(s). Add any to the canvas:</div>';
      vizs.forEach(function (v) {
        var vid = "bdviz" + (++_copilotMsgSeq) + "_" + Date.now().toString(36);
        _copilotVizLib[vid] = { html: v.html || "", spec: v.spec || null };
        out += '<div class="bd-copilot-vizbox"><div class="bd-copilot-viz" id="' + vid + '"><div class="bd-copilot-viz-hint"><i class="fas fa-spinner fa-spin"></i> Loading chart...</div></div>' +
          '<div class="bd-copilot-actions-row"><button class="bd-copilot-add" onclick="BlankDash._copilotVizToCanvas(\'' + vid + '\')"><i class="fas fa-download"></i> Add to canvas</button></div></div>';
      });
      return out + '</div>';
    },
    _copilotAppend: function (role, html, badge, add) {
      _copilotHistory.push({ role: role, uid: _copilotMsgUid(), text: "", html: html, badge: badge || "", add: add || null });
      this._copilotRenderThread();
      _copilotSaveHistory();
    },
    _copilotLoading: function (msg) {
      _copilotHistory.push({ role: "assistant", uid: _copilotMsgUid(), text: "", html: '<div class="bd-copilot-loading"><i class="fas fa-spinner fa-spin"></i> ' + _esc(msg || "Thinking...") + '</div>', badge: "" });
      this._copilotRenderThread();
    },
    _copilotReplaceLast: function (html, badge, add) {
      if (_copilotHistory.length) {
        var last = _copilotHistory[_copilotHistory.length - 1];
        last.text = "";
        last.html = html;
        last.badge = badge || "";
        last.uid = last.uid || _copilotMsgUid();
        last.add = add != null ? add : null;
      }
      this._copilotRenderThread();
      _copilotSaveHistory();
    },
    _copilotClear: function () {
      _copilotHistory = [];
      _copilotPendingRecs = null;
      _copilotVizLib = {};
      this._copilotRenderThread();
      _copilotSaveHistory();
      var ta = document.getElementById("bd-copilot-input");
      if (ta && !_copilotBusy) ta.focus();
    },
    /* Drop an AI response (or streamed chart) onto the canvas as a widget. */
    _copilotAddByUid: function (uid) {
      var m = null;
      for (var i = 0; i < _copilotHistory.length; i++) {
        if (_copilotHistory[i].uid === uid) { m = _copilotHistory[i]; break; }
      }
      if (!m) { App.warning("That response is gone."); return; }
      if (!m.add || !m.add.kind) { App.warning("Nothing to add for that response."); return; }
      this._addCopilotWidget(m.add.kind, m.add.payload, m.add.label);
    },
    _addCopilotWidget: function (kind, payload, label, spec) {
      var table = _dash.table || "";
      var pos = this._nextFreePos({ width: 640, height: 380 });
      var id = "w" + Date.now().toString(36) + "_" + (++_seq);
      var widget;
      if (kind === "text") {
        widget = {
          id: id, type: "text", title: label || "Chat answer",
          x: pos.x, y: pos.y, width: 640, height: 380,
          zIndex: _currentWidgets().length + 1, locked: false, showTitle: true, plain: true,
          config: { table: table, content: payload || "", textFormat: "markdown", label: label || "Chat answer" }
        };
      } else {
        widget = {
          id: id, type: "custom", title: label || "AI result",
          x: pos.x, y: pos.y, width: 640, height: 380,
          zIndex: _currentWidgets().length + 1, locked: false, showTitle: true, plain: false,
          config: { table: table, html: payload || "<div></div>", label: label || "AI result", spec: spec || null }
        };
      }
      var emptyEl = document.getElementById("bd-empty-state");
      if (emptyEl) emptyEl.remove();
      _currentWidgets().push(widget);
      this._initGrid();
      this._refreshHistory();
      this._markDirty();
      this._selectWidget(id);
      if (App.success) App.success("Added \"" + (label || "response") + "\" to the canvas");
      return id;
    },
    _runCopilotAction: function (act) {
      if (!_dash.table) { App.warning("Please select a data source first"); return; }
      if (act === "generate") { this._runAIGenerate("", true); return; }
      else if (act === "explain") this._copilotExplain();
      else if (act === "insights") this._copilotInsights();
      else if (act === "recommend") this._copilotRecommend();
    },
    _copilotExplain: async function () {
      if (!_dash.table) { App.warning("Please select a data source first"); return; }
      var table = _dash.table;
      var ctx = this._copilotContext();
      var source = "LLM";
      this._copilotLoading("Analyzing " + table + "...");
      var ins = null;
      try {
        var question = "Explain the data in this dashboard: the main story, key trends, top segments and an executive summary with exact figures from the columns." + (ctx ? " Canvas context: " + ctx : "");
        var r = await _copilotWithTimeout(API.llmInsights(table, question), 150000);
        if (r.error) throw r.error;
        if (r.timedOut) {
          source = "Auto";
          ins = await this._copilotAutoInsights(table);
          if (!ins) { this._copilotReplaceLast('<div class="bd-copilot-empty">The AI narrative service is slow right now and automatic insights are unavailable. Try again shortly.</div>', "Auto"); return; }
        } else {
          ins = (r.value && r.value.insights) || r.value || null;
        }
      } catch (e) {
        source = "Auto";
        ins = await this._copilotAutoInsights(table);
      }
      var summary = (ins && (ins.summary || ins.narrative || ins.analysis)) || "";
      var findings = (ins && (ins.key_findings || ins.findings)) || [];
      var out = '<div class="bd-copilot-result"><div class="bd-copilot-result-title"><i class="fas fa-file-lines"></i> Explain Data</div>';
      if (summary) out += '<p class="bd-copilot-prose">' + _esc(summary) + '</p>';
      if (findings.length) {
        out += '<div class="bd-copilot-list-title">Key findings</div><ul class="bd-copilot-list">';
        findings.forEach(function (f) { out += '<li>' + _esc(typeof f === "string" ? f : (f.text || JSON.stringify(f))) + '</li>'; });
        out += '</ul>';
      }
      if (!summary && !findings.length) out += '<div class="bd-copilot-empty">No explanation available for this table.</div>';
      this._copilotReplaceLast(out + '</div>', source, { kind: "custom", payload: out + '</div>', label: "Explain " + table });
    },
    _copilotInsights: async function () {
      if (!_dash.table) { App.warning("Please select a data source first"); return; }
      var table = _dash.table;
      var source = "LLM";
      this._copilotLoading("Mining insights from " + table + "...");
      var ins = null;
      try {
        var r = await _copilotWithTimeout(API.llmInsights(table, "Provide structured data insights: key findings, recommendations, correlations and anomalies, each as a short bullet with exact numbers."), 150000);
        if (r.error) throw r.error;
        if (r.timedOut) {
          source = "Auto";
          ins = await this._copilotAutoInsights(table);
          if (!ins) { this._copilotReplaceLast('<div class="bd-copilot-empty">The AI narrative service is slow right now and automatic insights are unavailable. Try again shortly.</div>', "Auto"); return; }
        } else {
          ins = (r.value && r.value.insights) || r.value || null;
        }
      } catch (e) {
        source = "Auto";
        ins = await this._copilotAutoInsights(table);
      }
      var out = '<div class="bd-copilot-result"><div class="bd-copilot-result-title"><i class="fas fa-lightbulb"></i> Insights</div>';
      var sections = [
        { label: "Key findings", arr: ins && (ins.key_findings || ins.findings) },
        { label: "Recommendations", arr: ins && ins.recommendations },
        { label: "Correlations", arr: ins && (ins.correlation_highlights || ins.correlations) }
      ];
      sections.forEach(function (s) {
        if (s.arr && s.arr.length) {
          out += '<div class="bd-copilot-list-title">' + s.label + '</div><ul class="bd-copilot-list">';
          s.arr.forEach(function (f) { out += '<li>' + _esc(typeof f === "string" ? f : (f.text || f.name || JSON.stringify(f))) + '</li>'; });
          out += '</ul>';
        }
      });
      if (!sections.some(function (s) { return s.arr && s.arr.length; })) out += '<div class="bd-copilot-empty">No structured insights returned.</div>';
      this._copilotReplaceLast(out + '</div>', source, { kind: "custom", payload: out + '</div>', label: "Insights for " + table });
    },
    _copilotAutoInsights: async function (table) {
      try {
        var auto = await API.insights(table);
        if (auto && auto.success && auto.insights) return auto.insights;
      } catch (e) {}
      return null;
    },
    _copilotRecommend: async function () {
      if (!_dash.table) { App.warning("Please select a data source first"); return; }
      var table = _dash.table;
      this._copilotLoading("Recommending visuals for " + table + "...");
      try {
        var recResult = { recommendations: [], used: "auto" };
        try {
          var rw = await _copilotWithTimeout(AIBuild.recommendCharts(table, "", Object.keys(_relatedTableKeys(table))), 150000);
          if (!rw.timedOut && !rw.error && rw.value) recResult = rw.value;
        } catch (e) {}
        var recs = recResult.recommendations || [];
        _copilotPendingRecs = recs;
        var out = '<div class="bd-copilot-result"><div class="bd-copilot-result-title"><i class="fas fa-chart-simple"></i> Recommended Visuals</div>';
        if (recs.length) {
          out += '<p class="bd-copilot-hint">Based on the columns in <b>' + _esc(table) + '</b>. Click <b>Add</b> to drop a visual on the canvas:</p><ul class="bd-copilot-list">';
          recs.forEach(function (r, i) {
            var label = r.title || r.label || r.name || r.type || "Visual";
            var why = _firstNonEmptyString(r.reasoning, r.why, r.reason);
            out += '<li><div class="bd-copilot-rec"><div><b>' + _esc(label) + '</b>' + (why ? '<span class="bd-copilot-why"> — ' + _esc(why) + '</span>' : '') + '</div>' +
              '<button class="bd-copilot-add" onclick="BlankDash._copilotAddChart(' + i + ')"><i class="fas fa-plus"></i> Add</button></div></li>';
          });
          out += '</ul><div class="bd-copilot-hint">Tip: <b>Generate</b> positions a full dashboard for you automatically.</div>';
        } else {
          out += '<div class="bd-copilot-empty">The AI could not recommend visuals for this table.</div>';
        }
        this._copilotReplaceLast(out + '</div>', recResult.used === "llm" ? "LLM" : "Auto");
      } catch (e) { this._copilotReplaceLast('<div class="bd-copilot-empty">Could not fetch recommendations.</div>', "AI"); }
    },
    _copilotAddChart: function (i) {
      if (!_dash.table) { App.warning("Please select a data source first"); return; }
      if (!_copilotPendingRecs || i == null || !_copilotPendingRecs[i]) { App.warning("No recommendations cached — run Visuals again."); return; }
      var rec = _copilotPendingRecs[i];
      var dim = _toPx(6, 3);
      var aiTheme = _aiThemeOpts();
      var w = AIBuild.chartWidgetFromRec(rec, _dash.table, { index: Date.now() % 100000, width: dim.width, height: dim.height, colors: aiTheme.palette });
      if (!w) { App.warning("Could not build that visual."); return; }
      var widgets = _currentWidgets();
      var pos = this._nextFreePos({ width: w.width, height: w.height });
      w.x = pos.x; w.y = pos.y;
      w.zIndex = widgets.length + 1;
      widgets.push(w);
      this._initGrid();
      this._refreshHistory();
      this._markDirty();
      var label = rec.title || rec.label || "visual";
      if (App.success) App.success('Added "' + label + '" to the canvas');
    },
    _copilotContext: function () {
      var parts = [];
      if (_selectedId) {
        var w = _findWidget(_selectedId);
        if (w) parts.push("Selected widget: '" + (w.title || w.type || "untitled") + "' (type " + w.type + (w.config && w.config.chartType ? ", " + w.config.chartType : "") + ").");
      }
      parts.push("Canvas: dashboard '" + (_dash.name || "untitled") + "' on table '" + (_dash.table || "none") + "' with " + _currentWidgets().length + " widget(s).");
      return parts.join(" ");
    },
    _renderMarkdown: function (text) {
      if (!text) return "";
      text = _esc(String(text));
      // code fences first so nothing inside them is re-processed
      text = text.replace(/```(\w*)[\r\n]*([\s\S]*?)```/g, function (m, lang, code) {
        return '<pre class="bd-copilot-code">' + _esc(code.replace(/[\r\n]+$/, "")) + '</pre>';
      });
      text = text.replace(/```(\w*)[\r\n]*([\s\S]*?)$/g, function (m, lang, code) {
        return '<pre class="bd-copilot-code">' + _esc(code.replace(/[\r\n]+$/, "")) + '</pre>';
      });
      text = text.replace(/`([^`]+)`/g, '<code class="bd-copilot-code-inline">$1</code>');
      text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      text = text.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
      text = text.replace(/^#{1,6}\s+(.+)$/gm, '<div class="bd-copilot-md-head">$1</div>');
      text = text.replace(/(?:^|\n)((?:[-*] )[^\n]+(?:\n[-*] [^\n]+)*)/g, function (m, block) {
        var items = block.split(/\n/).map(function (ln) { return ln.replace(/^[-*] /, ""); });
        return '<ul class="bd-copilot-md-list">' + items.map(function (it) { return '<li>' + it + '</li>'; }).join("") + '</ul>';
      });
      text = text.replace(/(?:^|\n)((?:\d+\. )[^\n]+(?:\n\d+\. [^\n]+)*)/g, function (m, block) {
        var items = block.split(/\n/).map(function (ln) { return ln.replace(/^\d+\. /, ""); });
        return '<ol class="bd-copilot-md-list">' + items.map(function (it) { return '<li>' + it + '</li>'; }).join("") + '</ol>';
      });
      // pipe tables (header + separator row) → styled table
      text = text.replace(/[ \t]+([\r\n])/g, '$1');
      text = text.replace(/\|(.+)\|[\r\n]+\|[\s:|-]+\|[\r\n]+((?:\|.*\|[\r\n]*)+)/g, function (m, header, body) {
        var cells = function (row) {
          return row.trim().replace(/^\|+|\|+$/g, "").split("|").map(function (c) { return c.trim(); })
            .filter(function (c, i, a) { return !(i === 0 && c === "" && a.length > 1); });
        };
        var h = cells(header);
        var rows = body.trim().split(/\r?\n/).filter(function (r) { return r.trim().indexOf("|") === 0; });
        return '<div class="bd-copilot-md-table"><table><thead>' +
          h.map(function (c) { return "<th>" + c + "</th>"; }).join("") + '</thead><tbody>' +
          rows.map(function (r) { return "<tr>" + cells(r).map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>"; }).join("") +
          '</tbody></table></div>';
      });
      // paragraph gaps + line breaks for anything the block rules didn't consume
      text = text.replace(/\r\n?/g, "\n");
      text = text.replace(/\n{2,}/g, '<div class="bd-copilot-md-gap"></div>');
      text = text.replace(/\n/g, "<br>");
      return text;
    },
    _setCopilotBusy: function (b) {
      _copilotBusy = b;
      var sendBtn = document.getElementById("bd-copilot-send");
      if (sendBtn) {
        sendBtn.disabled = b;
        sendBtn.innerHTML = b ? '<span class="bd-copilot-sendspin"><i class="fas fa-spinner fa-spin"></i></span>' : '<i class="fas fa-paper-plane"></i>';
      }
      var ta = document.getElementById("bd-copilot-input");
      if (ta) ta.disabled = b;
    },

    /* ── AI Generate Charts ─────────────────────────────────────────── */
    _aiGenerateCharts: function () {
      if (!_dash.table) { App.warning("Please select a data source first"); return; }
      var self = this;
      var ov = document.createElement("div");
      ov.className = "modal-overlay";
      ov.innerHTML =
        '<div class="modal-content" style="max-width:520px">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">' +
            '<h3 style="margin:0;font-size:18px;font-weight:700;"><i class="fas fa-wand-magic-sparkles" style="color:var(--color-primary);margin-right:6px;"></i>AI Generate Charts</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button>' +
          '</div>' +
          '<p style="font-size:12px;color:var(--color-content-text-secondary,var(--color-text-muted));margin:0 0 8px;">Data source: <b>' + _esc(_dash.table) + '</b>. ' +
            'Describe what you want to visualize (optional) and AI will add recommended chart widgets to the canvas.</p>' +
          '<textarea class="form-input" id="bd-ai-prompt" rows="3" placeholder="e.g. monthly revenue trend, top products by sales, regional breakdown..."></textarea>' +
          '<div class="bd-ai-hints" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">' +
            '<button type="button" class="bd-ai-hint" data-hint="monthly sales trend and top products">Trend + top products</button>' +
            '<button type="button" class="bd-ai-hint" data-hint="revenue by region and segment">By region/segment</button>' +
            '<button type="button" class="bd-ai-hint" data-hint="correlations between numeric metrics">Correlations</button>' +
          '</div>' +
          '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:14px">' +
            '<label class="bd-ai-insights-check" style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--color-content-text-secondary);cursor:pointer">' +
              '<input type="checkbox" id="bd-ai-insights" checked> Also add an AI narrative insights widget' +
            '</label>' +
          '</div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;border-top:1px solid var(--color-card-border,var(--color-border));padding-top:12px">' +
            '<button type="button" class="btn btn-sm btn-secondary" data-close="1">Cancel</button>' +
            '<button type="button" class="btn btn-sm btn-primary" data-gen="1" id="bd-ai-gen-btn"><i class="fas fa-wand-magic-sparkles"></i> Generate</button>' +
          '</div>' +
        '</div>';
      var ta = ov.querySelector("#bd-ai-prompt");
      ov.querySelectorAll(".bd-ai-hint").forEach(function (b) {
        b.addEventListener("click", function () { ta.value = this.getAttribute("data-hint"); });
      });
      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); return; }
        if (e.target.closest("[data-gen]")) {
          var prompt = (ta.value || "").trim();
          var withInsights = ov.querySelector("#bd-ai-insights").checked;
          ov.remove();
          self._runAIGenerate(prompt, withInsights);
        } else if (e.target === ov) {
          ov.remove();
        }
      });
      document.body.appendChild(ov);
      setTimeout(function () { if (ta) ta.focus(); }, 50);
    },

    _aiBuildTitle: function (prompt, table) {
      var _capWord = function (w) {
        if (/[A-Z]/.test(w)) return w;
        if (/^(ai|kpi|sql|crm|sku|roi|etl|hr|api|url|csv|usa|uk)$/i.test(w)) return w.toUpperCase();
        return w.charAt(0).toUpperCase() + w.slice(1);
      };
      if ((prompt || "").trim()) {
        var s = String(prompt).replace(/\s+/g, " ").trim();
        s = s.length > 64 ? s.slice(0, 64).replace(/\s+\S*$/, "") + "…" : s;
        s = s.split(" ").map(_capWord).join(" ");
        return s.replace(/[.\s]+$/, "");
      }
      var base = _titleCaseName(String(table || "Dashboard")) || "Dashboard";
      base = base.split(" ").map(_capWord).join(" ");
      if (/overview|dashboard|report|summary|analysis$/i.test(base)) return base;
      return base + " Overview";
    },
    _nameUntitledAiTitle: function (prompt) {
      if (!_dash.name || /^untitled/i.test(_dash.name) || !_dash.name.trim()) {
        _dash.name = this._aiBuildTitle(prompt, _dash.table);
        var nameEl = document.getElementById("bd-name");
        if (nameEl) nameEl.value = _dash.name;
        this._markDirty();
      }
    },

    /* Map chart recommendations (the `recs` from AIBuild.recommendCharts, which
       carry chart_type / x_axis / y_axis / color / title but no validated
       `zone`, KPI or filter bands) into a plan object shaped for
       AIBuild.buildDashboardFromPlanPages — so the inline copilot "generate"
       can produce the same multi-page Power BI layout Dashboard Studio builds. */
    _recsToPlan: function (table, recs, insights) {
      var cls = null;
      try { cls = AIBuild.classifyColumns(table); } catch (e) { /* no metadata */ }
      cls = cls || {};
      function qcol(c) { return '"' + String(c == null ? "" : c).replace(/"/g, '""') + '"'; }
      function hum(c) {
        return String(c || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim();
      }
      // KPI band from the best numeric measures (mirrors the backend's plan KPIs).
      var kpis = [];
      var ordered = [];
      if (cls.m) ordered.push(cls.m);
      if (cls.m2 && ordered.indexOf(cls.m2) === -1) ordered.push(cls.m2);
      (cls.numeric || []).forEach(function (n) { if (ordered.indexOf(n) === -1) ordered.push(n); });
      ordered.slice(0, 4).forEach(function (m) {
        kpis.push({
          label: "Total " + hum(m),
          variant: "kpi", agg: "SUM", column: m,
          expression: "SUM(" + qcol(m) + ")",
          sql: "SELECT SUM(" + qcol(m) + ") AS value FROM " + qcol(table),
          spark: !!cls.date
        });
      });
      if (!kpis.length) {
        kpis.push({ label: "Total Records", variant: "kpi", agg: "COUNT_DISTINCT", column: "",
          expression: "COUNT(*)", spark: false,
          sql: "SELECT COUNT(*) AS value FROM " + qcol(table) });
      }
      // Slicer row from categorical columns + date + numeric range.
      var filters = [];
      (cls.cat || cls.dims || []).slice(0, 3).forEach(function (c) {
        filters.push({ field: c, type: "multi", label: hum(c),
          sql: "SELECT DISTINCT " + qcol(c) + " AS label FROM " + qcol(table) + " ORDER BY label" });
      });
      if (cls.numeric && cls.numeric.length) {
        var nc = cls.numeric[0];
        filters.push({ field: nc, type: "numrange", label: hum(nc),
          sql: "SELECT MIN(CAST(" + qcol(nc) + " AS DOUBLE)) AS min_val, MAX(CAST(" + qcol(nc) + " AS DOUBLE)) AS max_val FROM " + qcol(table) });
      }
      if (cls.date) {
        filters.push({ field: cls.date, type: "daterange", label: hum(cls.date),
          sql: "SELECT MIN(" + qcol(cls.date) + ") AS min_val, MAX(" + qcol(cls.date) + ") AS max_val FROM " + qcol(table) });
      }
      // Zones from the recommendations: assign a Power BI family by chart type.
      function zoneFamily(ct) {
        ct = String(ct || "").toLowerCase();
        if (/line|area|combo|sparkline/.test(ct)) return "trend";
        if (/donut|pie|sunburst|treemap|funnel|radar/.test(ct)) return "breakdown";
        if (/bar|column|pareto|pivot|table/.test(ct)) return "comparison";
        if (/histogram|box/.test(ct)) return "distribution";
        if (/scatter|bubble|heatmap/.test(ct)) return "correlation";
        if (/waterfall|sankey|slope/.test(ct)) return "special";
        if (/map|geo/.test(ct)) return "geo";
        return "breakdown";
      }
      var zones = [];
      var seenKey = {};
      (recs || []).forEach(function (r) {
        var ct = r.chart_type || r.chartType || "bar";
        var x = r.x_axis || r.xAxis || "";
        var y = r.y_axis || r.yAxis || "";
        var cc = r.color || r.group_col || r.series || "";
        if (cc === x || cc === y) cc = "";
        var key = String(ct) + "|" + x + "|" + y;
        if (seenKey[key]) return;
        seenKey[key] = 1;
        var timeX = false;
        if (x === cls.date || (cls.dates || []).indexOf(x) !== -1) timeX = true;
        var sql = "";
        try { sql = AIBuild.buildChartSql(ct, table, x, y, cc, { timeX: timeX }); } catch (e) { /* leave blank */ }
        zones.push({
          zone: zoneFamily(ct),
          chartType: ct,
          x: x, y: y, color: cc,
          title: r.title || r.label || (hum(x) + ((y && y !== x) ? " by " + hum(y) : "")),
          sql: sql,
          config: {}
        });
      });
      return {
        table: table,
        title: _dash.name || "Dashboard",
        kpis: kpis, filters: filters, zones: zones,
        insights: insights && (insights.summary || (insights.key_findings && insights.key_findings.length)) ? insights : null
      };
    },

    _runAIGenerate: async function (prompt, withInsights) {
      if (!_dash.table) { App.warning("Please select a data source first"); return; }
      if (App.info) App.info("AI is analyzing your data...");
      var recResult = { recommendations: [], used: "auto" };
      try {
        var rg = await _copilotWithTimeout(AIBuild.recommendCharts(_dash.table, prompt || "", Object.keys(_relatedTableKeys(_dash.table))), 150000);
        if (!rg.timedOut && !rg.error && rg.value) recResult = rg.value;
      } catch (e) { /* fallback */ }
      var recs = recResult.recommendations || [];
      if (!recs.length) {
        if (App.warning) App.warning("The AI could not suggest any charts for this table.");
        return;
      }
      var added = 0;
      var table = _dash.table;
      this._nameUntitledAiTitle(prompt);
      var widgets = _currentWidgets();
      // Match the theme of an existing wizard-stamped sidebar if there is one,
      // so AI-added charts/pages inherit the dashboard's chosen style.
      var navSrc = null;
      (_dash.pages || []).forEach(function (pg) {
        if (navSrc) return;
        navSrc = (pg.widgets || []).find(function (w) { return w && w.config && w.config.sidebar; });
      });
      var aiTheme = _aiNavTheme(navSrc);
      // Insights are used for the professional panel on a fresh canvas.
      var insights = null;
      if (withInsights) { try { insights = await AIBuild.fetchInsights(table, prompt || ""); } catch (e) { insights = null; } }

      // Fresh canvas → build the full professional Power-BI-style layout.
      if (widgets.length === 0 && window.AIBuild && AIBuild.buildAIDashboardLayout) {
        var cw = _baseCanvasW();
        var chh = _baseCanvasH();
        // Multi-page when the data has enough distinct families to split (the
        // copilot now matches Dashboard Studio's buildDashboardFromPlanPages,
        // and every generated page auto-carries a website-style left sidebar
        // navigation). Page 3 is a raw-data preview page when data is rich.
        var plan = this._recsToPlan(table, recs, insights);
        var pageCount = Math.min(3, Math.max(1, recs.length >= 4 ? 3 : 1));
        if (pageCount > 1 && AIBuild.buildDashboardFromPlanPages) {
          var pagesRes = AIBuild.buildDashboardFromPlanPages(plan, {
            canvasW: cw,
            canvasH: chh,
            title: _dash.name || "Dashboard",
            subtitle: "Data source: " + table,
            chartH: 300,
            insightH: 260,
            accent: aiTheme.accent,
            accent2: aiTheme.accent2,
            colors: aiTheme.palette,
            theme: aiTheme.theme || {}
          }, { pages: pageCount, pageFocus: ["overview", "breakdown", "data"], pagePrompt: [] });
          if (Array.isArray(pagesRes.pages) && pagesRes.pages.length) {
            _dash.pages = pagesRes.pages.map(function (pg) {
              return { id: pg.id, name: pg.name, widgets: pg.widgets || [] };
            });
            _dash.currentPage = 0;
            added = _dash.pages.reduce(function (n, pg) { return n + pg.widgets.length; }, 0);
          } else {
            pageCount = 1;
          }
        }
        if (pageCount === 1) {
          var layout = AIBuild.buildAIDashboardLayout(table, recs, insights, {
            canvasW: cw,
            canvasH: chh,
            title: _dash.name || "Dashboard",
            subtitle: "Data source: " + table,
            chartH: 310,
            insightH: 260,
            accent: aiTheme.accent,
            accent2: aiTheme.accent2,
            colors: aiTheme.palette,
            theme: aiTheme.theme || {}
          });
          widgets = layout.widgets;
          added = widgets.length;
          _dash.pages[_dash.currentPage].widgets = widgets;
        }
      } else {
        var dim = _toPx(6, 3);
        var targetWidgets = widgets;
        if (!this._hasFreePos({ width: dim.width, height: dim.height })) {
          // The current page is full — put the generated charts on a new page
          // so nothing is placed off-canvas or on top of existing content.
          var newPage = { id: "pg" + Date.now().toString(36), name: "AI Generated", widgets: [] };
          _dash.pages.push(newPage);
          _dash.currentPage = _dash.pages.length - 1;
          targetWidgets = newPage.widgets;
          if (window.AIBuild && AIBuild.sidebarNavWidget) {
            var sbw = Math.round(Math.max(140, Math.min(200, _baseCanvasW() * 0.095)));
            targetWidgets.push(AIBuild.sidebarNavWidget(
              _dash.pages.map(function (p) { return p.name; }),
              _dash.pages.length - 1,
              { x: 0, y: 0, width: sbw, height: _baseCanvasH(), index: 0, accent: aiTheme.accent, accent2: aiTheme.accent2, theme: aiTheme.theme || {}, table: table }
            ));
          }
          this._refreshSidebars();
        }
        recs.slice(0, 6).forEach(function (rec, i) {
          var w = AIBuild.chartWidgetFromRec(rec, table, {
            index: i,
            width: dim.width,
            height: dim.height,
            colors: aiTheme.palette
          });
          if (!w) return;
          var pos = this._nextFreePos({ width: w.width, height: w.height });
          w.x = pos.x; w.y = pos.y;
          w.zIndex = targetWidgets.length + 1;
          targetWidgets.push(w);
          added++;
        }.bind(this));
        if (withInsights && insights && !_hasInsightWidget() && this._hasFreePos({ width: _toPx(9, 3).width, height: _toPx(9, 3).height })) {
          var dimI = _toPx(9, 3);
          var posI = this._nextFreePos({ width: dimI.width, height: dimI.height });
          var iw = AIBuild.insightTextWidget(table, insights, {
            x: posI.x, y: posI.y, width: dimI.width, height: dimI.height,
            index: targetWidgets.length
          });
          targetWidgets.push(iw);
          added++;
        }
      }
      if (added > 0) {
        var emptyEl = document.getElementById("bd-empty-state");
        if (emptyEl) emptyEl.remove();
        this._initGrid();
        // If the copilot generated multiple pages, re-render so the new page
        // tabs + page-navigator widgets appear immediately.
        if (_dash.pages && _dash.pages.length > 1) { this.render(); }
        this._refreshHistory();
        this._markDirty();
        if (recResult.used === "llm") {
          if (App.success) App.success("Added " + added + " AI-generated widget" + (added > 1 ? "s" : ""));
        } else if (App.info) {
          App.info("AI assistant unavailable — used automatic chart suggestions");
        }
        if (document.getElementById("bd-copilot")) {
          var pagesNote = (_dash.pages && _dash.pages.length > 1)
            ? ' across <b>' + _dash.pages.length + '</b> pages' : ' to the canvas';
          var note = (recResult.used === "llm")
            ? '<div class="bd-copilot-result"><b><i class="fas fa-wand-magic-sparkles"></i> Dashboard generated</b> — AI added <b>' + added + '</b> widget' + (added > 1 ? "s" : "") + pagesNote + '.</div>'
            : '<div class="bd-copilot-result"><b><i class="fas fa-wand-magic-sparkles"></i> Dashboard generated</b> — added ' + added + ' widget' + (added > 1 ? "s" : "") + pagesNote + ' using automatic suggestions (LLM offline).</div>';
          this._copilotAppend("assistant", note, recResult.used === "llm" ? "LLM" : "Auto");
        }
      }
    },

    /* ── Recreate with AI (Power BI Q&A style) ──
       Rebuild a widget that failed/blanked (or any data widget) from a
       natural-language prompt, replacing it in place (position/size/id kept). */
    _AI_DECOR_TYPE: /^(text|image|page|button|shape|divider)$/,
    _isDataWidget: function (w) {
      if (!w) return false;
      var t = String(w.type || "");
      if (this._AI_DECOR_TYPE.test(t)) return false;
      var c = w.config || {};
      if (c.sql || c.measureExpr || c.labels || c.data || c.measures || c.table) return true;
      return ["chart", "kpi", "table", "metriccard", "slicer", "daterange", "searchbox",
        "histogram", "boxplot", "wordcloud", "sankey", "stream", "slope", "population", "venn"].indexOf(t) !== -1;
    },
    _aiRecreateHint: function (w) {
      var c = w.config || {};
      var table = c.table || _dash.table || "";
      var t = String(w.type || "").toLowerCase();
      if (c.chartType && t === "chart") t = String(c.chartType).toLowerCase();
      var cls = null;
      if (table && window.AIBuild && AIBuild.classifyColumns) { try { cls = AIBuild.classifyColumns(table); } catch (e) {} }
      var m = c.yCol || (cls && cls.m) || "";
      var d = c.xCol || (cls && cls.dim) || "";
      var dateC = c.dateCol || (cls && cls.date) || "";
      var typeWord = {
        slope: "a slope / mini-trend visual", sankey: "a flow (sankey) diagram",
        stream: "a stacked stream / area over time", population: "a population pyramid",
        venn: "a Venn diagram of category overlap",
        histogram: "a histogram distribution", boxplot: "a box plot", scatter: "a scatter plot",
        line: "a line / trend chart", area: "an area chart", pie: "a pie chart",
        doughnut: "a doughnut chart", bar: "a bar chart", horizontalbar: "a horizontal bar chart",
        heatmap: "a heatmap", gauge: "a gauge", kpi: "a KPI card", table: "a data table"
      }[t] || "a bar or line chart";
      var parts = [];
      if (t === "slope" || t === "stream" || t === "population") {
        if (m) parts.push(m);
        if (dateC || (cls && cls.date)) parts.push("over time");
      } else if (m && d) parts.push(m + " by " + d);
      else if (m) parts.push(m);
      else if (d) parts.push("breakdown by " + d);
      var base = "Create " + typeWord + " for " + (w.title || w.type || "this data");
      if (parts.length) base += " showing " + parts.join(" ");
      if (cls && (cls.dim || cls.m)) {
        var avail = [];
        if (cls.dim) avail.push(cls.dim);
        if (cls.dim2) avail.push(cls.dim2);
        if (cls.m) avail.push(cls.m);
        if (cls.date) avail.push(cls.date);
        base += " (columns: " + avail.join(", ") + ")";
      }
      if (base.length > 160) base = base.slice(0, 157).replace(/\s+\S*$/, "") + "…";
      return base;
    },
    _openAIRecreate: function (id) {
      var w = _findWidget(id);
      if (!w) return;
      var table = (w.config && w.config.table) || _dash.table || "";
      var self = this;
      if (!table) { App.warning("This widget has no data source to rebuild from."); return; }
      if (!window.AIBuild) { App.warning("AI visual builder is unavailable."); return; }
      var cls = null;
      try { cls = AIBuild.classifyColumns(table); } catch (e) {}
      var hintDim = (cls && cls.dim) || "category";
      var hintMeasure = (cls && cls.m) || "value";
      var hintDate = (cls && cls.date) || "date";
      var title = 'Recreate with AI' + (w.title ? ' — ' + w.title : '');
      var html =
        '<div style="font-size:12px;color:var(--color-content-text-secondary,var(--color-text-muted));margin-bottom:10px;">' +
          'Data source: <b>' + _esc(table) + '</b>. Describe the visual you want and AI will rebuild this widget ' +
          '<i>in place</i> (its position, size and links are kept).</div>' +
        '<textarea class="form-input" id="bd-ai-recreate-input" rows="3" placeholder="e.g. monthly revenue trend, top products by sales, regional breakdown..."></textarea>' +
        '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">' +
          '<button type="button" class="bd-ai-hint" data-hint="' + _esc('bar chart of ' + hintMeasure + ' by ' + hintDim) + '">Best breakdown</button>' +
          '<button type="button" class="bd-ai-hint" data-hint="' + _esc('line chart of ' + hintMeasure + ' over ' + hintDate) + '">Trend over time</button>' +
          '<button type="button" class="bd-ai-hint" data-hint="' + _esc('pie chart showing the share of ' + hintDim) + '">Pie by category</button>' +
          '<button type="button" class="bd-ai-hint" data-img="1" title="Describe the visual from a screenshot"><i class="fas fa-image"></i> Upload image</button>' +
        '</div>' +
        '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;border-top:1px solid var(--color-card-border,var(--color-border));padding-top:12px">' +
          '<button type="button" class="btn btn-sm btn-secondary" data-close="1">Cancel</button>' +
          '<button type="button" class="btn btn-sm btn-primary" data-gen="1" id="bd-ai-recreate-gen"><i class="fas fa-wand-magic-sparkles"></i> Recreate</button>' +
        '</div>';
      var ctrl = Modal.show(title, html, { size: "md" });
      var overlay = document.getElementById(ctrl.id);
      if (!overlay) return;
      var ta = overlay.querySelector("#bd-ai-recreate-input");
      if (ta) ta.value = this._aiRecreateHint(w);
      overlay.querySelectorAll(".bd-ai-hint").forEach(function (b) {
        b.addEventListener("click", function () { if (ta) ta.value = this.getAttribute("data-hint"); });
      });
      overlay.querySelectorAll("[data-img]").forEach(function (b) {
        b.addEventListener("click", function () {
          var inputFile = document.createElement("input");
          inputFile.type = "file";
          inputFile.accept = "image/*";
          inputFile.style.display = "none";
          document.body.appendChild(inputFile);
          inputFile.addEventListener("change", function () {
            var f = inputFile.files && inputFile.files[0];
            inputFile.remove();
            if (!f) return;
            if (f.size > 8 * 1024 * 1024) { App.warning("Image is larger than 8MB — upload a smaller screenshot."); return; }
            App.info("Reading your dashboard image…");
            API.analyzeImage(f).then(function (res) {
              if (!res || !res.success) { App.warning((res && res.message) || "Could not analyze the image."); return; }
              var brief = res.prompt || "";
              if (ta) ta.value = brief ? ("Match this visual: " + brief) : "Recreate this widget from the uploaded image";
              App.success("Image analyzed — review the prompt and hit Recreate.");
            }).catch(function (e) {
              App.warning((e && e.message) || "Vision analysis failed.");
            });
          });
          inputFile.click();
        });
      });
      overlay.querySelectorAll("[data-close]").forEach(function (b) {
        b.addEventListener("click", function () { ctrl.close(); });
      });
      var gen = overlay.querySelector("[data-gen]");
      if (gen) gen.addEventListener("click", function () {
        var prompt = (ta ? ta.value : "") || "";
        ctrl.close();
        self._runAIRecreate(id, prompt);
      });
      if (ta) setTimeout(function () { ta.focus(); }, 50);
    },
    _pickRecreateRec: function (recs, prompt) {
      var p = String(prompt || "").toLowerCase();
      var wanted = null;
      var tokens = {
        bar: ["bar"], line: ["line", "trend", "over time"], pie: ["pie"], doughnut: ["doughnut", "donut"],
        area: ["area"], scatter: ["scatter", "correlation", "relationship"], heatmap: ["heatmap", "heat map"],
        histogram: ["histogram", "distribution"], box: ["box", "boxplot", "box plot"]
      };
      Object.keys(tokens).forEach(function (t) {
        if (wanted) return;
        tokens[t].forEach(function (tok) { if (p.indexOf(tok) !== -1) wanted = t; });
      });
      if (wanted) {
        for (var i = 0; i < (recs || []).length; i++) {
          var ct = String((recs[i] && (recs[i].chart_type || recs[i].chartType)) || "").toLowerCase();
          if (ct === wanted) return recs[i];
        }
      }
      return (recs || [])[0];
    },
    /* If the prompt explicitly requests one of the custom-render types, return
       that type so we can build it directly (the LLM endpoint only knows the
       classic chart types). */
    _aiRequestedCustomType: function (prompt) {
      var p = String(prompt || "").toLowerCase();
      if (/sankey|flow diagram|flow between/i.test(p)) return "sankey";
      if (/streamgraph|stream graph|stream chart|stacked stream/i.test(p)) return "stream";
      if (/\bslope\b|mini[- ]?trend/i.test(p)) return "slope";
      if (/population pyramid|population by (male|gender)|pyramid chart/i.test(p)) return "population";
      if (/\bvenn\b|diagram of overlap|overlap diagram/i.test(p)) return "venn";
      return null;
    },
    _runAIRecreate: async function (id, prompt) {
      var w = _findWidget(id);
      if (!w) return;
      var table = (w.config && w.config.table) || _dash.table;
      if (!table) { App.warning("No data source for this widget."); return; }
      if (!window.AIBuild) { App.warning("AI visual builder is unavailable."); return; }
      var title = this._aiBuildTitle(prompt, table);
      /* Custom-render types: build directly from their built-in default SQL —
         reliable and respects the requested visual type. */
      var customType = this._aiRequestedCustomType(prompt);
      if (customType) {
        var cw = {
          id: w.id, type: customType, title: title,
          x: w.x, y: w.y, width: w.width, height: w.height, zIndex: w.zIndex,
          locked: !!w.locked, showTitle: w.showTitle != null ? w.showTitle : true,
          plain: false,
          config: { table: table, sql: _defaultSql(customType, table), label: title, filterField: _detectFilterField(table, customType) || title }
        };
        var arr0 = _currentWidgets();
        for (var j = 0; j < arr0.length; j++) { if (arr0[j].id === w.id) { arr0[j] = cw; break; } }
        this._onWidgetEdited(cw.id);
        this._markDirty();
        this._refreshHistory();
        this._selectWidget(cw.id);
        if (App.success) App.success("Visual recreated with AI");
        return;
      }
      if (App.info) App.info("AI is creating your visual...");
      var recResult = { recommendations: [], used: "auto" };
      try {
        var rg = await _copilotWithTimeout(AIBuild.recommendCharts(table, prompt || "", Object.keys(_relatedTableKeys(table))), 150000);
        if (!rg.timedOut && !rg.error && rg.value) recResult = rg.value;
      } catch (e) { /* fall through */ }
      var recs = recResult.recommendations || [];
      if (!recs.length) { if (App.warning) App.warning("AI could not build a visual from that request."); return; }
      var rec = this._pickRecreateRec(recs, prompt);
      var aiTheme = _aiThemeOpts();
      var nw = AIBuild.chartWidgetFromRec(rec, table, {
        index: 0, width: w.width, height: w.height, colors: aiTheme.palette
      });
      if (!nw) { if (App.warning) App.warning("Could not build a widget for that request."); return; }
      /* Replace in place: keep identity, position, size and flags so the canvas
         slot, selections and drill links all stay valid. */
      nw.id = w.id;
      nw.x = w.x; nw.y = w.y; nw.width = w.width; nw.height = w.height; nw.zIndex = w.zIndex;
      nw.locked = !!w.locked;
      if (w.plain != null) nw.plain = !!w.plain;
      if (w.showTitle != null) nw.showTitle = w.showTitle;
      var arr = _currentWidgets();
      for (var i = 0; i < arr.length; i++) {
        if (arr[i].id === w.id) { arr[i] = nw; break; }
      }
      this._onWidgetEdited(nw.id);
      this._markDirty();
      this._refreshHistory();
      this._selectWidget(nw.id);
      if (recResult.used === "llm") { if (App.success) App.success("Visual recreated with AI"); }
      else if (App.info) App.info("Recreated using automatic chart suggestions");
    },
    /* Attach a dashed "Recreate with AI" button onto a widget cell that failed to
       render (error / guard placeholder / no data / unknown type) or is blank. */
    _maybeAttachAIRecreate: function (cell, w) {
      if (!cell || !w) return;
      if (!this._isDataWidget(w)) return;
      /* Table widgets render their <table> on a 50ms defer (DataTable), so the
         cell looks empty at wrap time and a working table would get flagged as
         "no data". Skip them — an empty table already communicates the state. */
      if (w.type === "table") return;
      if (cell.querySelector(".bd-ai-recreate")) return;
      var txt = (cell.textContent || "").replace(/[\s\u00a0]+/g, " ").trim();
      var hasFail = /Error loading data|No data to display|Unknown widget type|Two columns needed|Needs several measure|Set an image URL/i.test(txt);
      var hasVisual = !!cell.querySelector("svg,canvas,img,table,.bd-leaflet-host,.bd-page-link,.bd-action-btn");
      if (!hasFail && !(!txt && !hasVisual)) return;
      var wrap = document.createElement("div");
      wrap.className = "bd-ai-rc-wrap";
      wrap.style.cssText = "position:relative;display:flex;flex-direction:column;height:100%;min-height:0;";
      var body = document.createElement("div");
      body.style.cssText = "flex:1;display:flex;align-items:center;justify-content:center;width:100%;min-height:0;overflow:hidden;";
      while (cell.firstChild) body.appendChild(cell.firstChild);
      var foot = document.createElement("div");
      foot.style.cssText = "flex:none;padding:6px 8px 8px;";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bd-ai-recreate";
      btn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> Recreate with AI';
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        BlankDash._openAIRecreate(w.id);
      });
      foot.appendChild(btn);
      wrap.appendChild(body);
      wrap.appendChild(foot);
      cell.appendChild(wrap);
    },

    _rename: function (val) { _dash.name = _titleCaseName(val) || "Untitled Dashboard"; this._markDirty(); },
    _changeTable: function (val) { _dash.table = val; _queryCacheClear(); this._markDirty(); this._renderLeftRailLive(); this._refreshCopilotTable(); },
    _refreshCopilotTable: function () {
      var tableEl = document.getElementById("bd-copilot-table");
      if (tableEl) tableEl.textContent = _dash.table || "no data source";
    },

    /* ── Unsaved-changes tracking ── */
    _markDirty: function () {
      _dirty = true;
      this._updateSaveBadge();
      this._autoSave();
      this._persistLocalSnapshot();
    },
    _clearDirty: function () {
      _dirty = false;
      this._updateSaveBadge();
    },

    /* A3 — crash-recovery snapshots. Every edit writes a lightweight copy of the
       current dashboard to localStorage so a browser crash / accidental close
       never loses more than the last edit. The snapshot is cleared on a
       successful server save and offered for restore on next load. */
    _recoveryKey: function () {
      var id = String(_dash.id || "").replace(/^blankdash_/, "");
      return "blankdash_recovery_" + (id || "unsaved");
    },
    _persistLocalSnapshot: function () {
      try {
        var snap = _dashConfigSnapshot();
        snap._recoveryAt = Date.now();
        localStorage.setItem(this._recoveryKey(), JSON.stringify(snap));
      } catch (e) { /* storage full / private mode — ignore */ }
    },
    _clearRecoverySnapshot: function () {
      try { localStorage.removeItem(this._recoveryKey()); } catch (e) {}
    },
    /* Called after a dashboard loads from the server. If a crash-recovery
       snapshot exists that is newer than the server copy, offer to restore it. */
    _checkRecoverySnapshot: function (serverTime) {
      var self = this;
      var raw = null;
      try { raw = localStorage.getItem(this._recoveryKey()); } catch (e) {}
      if (!raw) return;
      var snap = null;
      try { snap = JSON.parse(raw); } catch (e) {}
      if (!snap || !snap.pages) return;
      var recTime = snap._recoveryAt || 0;
      var srvTime = serverTime || (snap.time || 0);
      if (recTime <= srvTime) return;
      var doRestore = function () {
        var nd = self._dashFromServer({ layout: { _blankdash: snap } });
        if (nd) {
          self._applyDash(nd);
          App.info("Restored your unsaved changes from a crash-recovery snapshot");
        }
      };
      if (window.Modal && window.Modal.confirm) {
        Modal.confirm("Recover unsaved changes?", "You have unsaved edits from " + new Date(recTime).toLocaleTimeString() + ". Restore them?", { confirmLabel: "Restore" }).then(function (ok) { if (ok) doRestore(); });
      } else {
        doRestore();
      }
    },
    _updateSaveBadge: function () {
      var el = document.getElementById("bd-save-badge");
      if (!el) return;
      el.textContent = _dirty ? "Changes" : "Saved";
      el.classList.toggle("bd-save-dirty", _dirty);
      this._updateStatusBar();
    },
    _onBeforeUnload: function (e) {
      if (_dash.mode === "present" || !_dirty) return;
      e.preventDefault();
      e.returnValue = "";
    },
    _goBack: function () {
      if (_dirty && window.Modal && window.Modal.confirm) {
        Modal.confirm("Discard changes?", "You have unsaved changes in \"" + _esc(_dash.name || "this dashboard") + "\". Leave without saving?", { variant: "danger", confirmLabel: "Leave" }).then(function (ok) {
          if (ok) App.navigate("#dashboardstudio");
        });
      } else {
        App.navigate("#dashboardstudio");
      }
    },
    _revertToSaved: function () {
      var self = this;
      if (!_dash.id) { App.warning("This dashboard has not been saved yet"); return; }
      var doRev = function () { self._loadSaved(_dash.id); App.info("Reverted to the version saved at " + new Date().toLocaleTimeString()); };
      if (window.Modal && window.Modal.confirm) {
        Modal.confirm("Revert to last save?", "All unsaved changes will be lost.", { variant: "danger", confirmLabel: "Revert" }).then(function (ok) { if (ok) doRev(); });
      } else {
        doRev();
      }
    },

    _save: function (silent) {
      var self = this;
      var id = String(_dash.id || "").replace(/^blankdash_/, "");
      var payload = this._buildServerPayload();
      // Local cache (offline fallback) — keep a client-side copy too.
      var cacheKey = id ? ("blankdash_" + id) : ("blankdash_" + Date.now());
      _dash.id = id || cacheKey.replace(/^blankdash_/, "");
      var localCopy = JSON.parse(JSON.stringify(this._buildServerPayload()));
      try { localStorage.setItem("blankdash_" + _dash.id, JSON.stringify(localCopy)); } catch (e) {}
      var doNotify = function (serverId) {
        if (serverId && serverId !== _dash.id) {
          _dash.id = serverId;
          try { localStorage.setItem("blankdash_" + serverId, JSON.stringify(_dashConfigSnapshot())); } catch (e) {}
          // keep the URL deep-link in sync for this tab
          try {
            var h = window.location.hash.replace(/^#blankdash\/[^\/]+/, "#blankdash/" + serverId);
            if (h.indexOf("#blankdash/" + serverId) === -1 && !/^#blankdash\/shared\//.test(window.location.hash || "")) {
              history.replaceState(null, "", "#blankdash/" + serverId);
            }
          } catch (e) {}
        }
        var _histKey = (window.RBAC && window.RBAC.storeKey) ? window.RBAC.storeKey("studio_history") : "studio_history";
        var hist = [];
        try { hist = JSON.parse(localStorage.getItem(_histKey) || "[]"); } catch (e) { hist = []; }
        if (!hist.length || hist[hist.length - 1].name !== _dash.name || hist[hist.length - 1].time < Date.now() - 30000) {
          hist.push({ type: "blank", table: _dash.table, name: _dash.name, time: Date.now() });
        }
        try { localStorage.setItem(_histKey, JSON.stringify(hist.slice(-50))); } catch (e) {}
        _dirty = false;
        self._updateSaveBadge();
        self._clearRecoverySnapshot();
        if (silent && App.toast) {
          var b = document.getElementById("bd-save-badge");
          if (b) b.textContent = "Auto-saved " + new Date().toLocaleTimeString();
        }
        if (!silent) App.success("Dashboard saved!");
      };
      var apiCall;
      if (id) { apiCall = API.updateDashboard(id, payload); }
      else { apiCall = API.createDashboard(payload); }
      apiCall.then(function (res) {
        var dash = res && res.dashboard ? res.dashboard : res;
        var serverId = (dash && dash.id) || id;
        doNotify(serverId);
      }).catch(function (e) {
        console.warn("Server save failed, kept local copy:", e);
        _dirty = false;
        self._updateSaveBadge();
        if (silent) return;
        App.warning("Could not reach server — changes saved locally only");
      });
    },

    /* Debounced automatic save after any edit (power-user convenience) */
    _autoSave: function () {
      var self = this;
      clearTimeout(_autoSaveTimer);
      _autoSaveTimer = setTimeout(function () { self._save(true); }, 1500);
    },

    _loadSaved: function (id) {
      var self = this;
      var serverId = String(id || "").replace(/^blankdash_[^\/]*\//, "").replace(/^blankdash_/, "").split("/")[0];
      if (!serverId) { App.warning("No dashboard id"); return; }
      // Try the server first; fall back to the local cache copy.
      API.getDashboard(serverId).then(function (res) {
        var dash = res && res.dashboard ? res.dashboard : res;
        if (!dash) throw new Error("not found");
        var nd = self._dashFromServer(dash);
        if (nd) self._applyDash(nd);
        self._checkRecoverySnapshot(dash.updated_at ? new Date(dash.updated_at).getTime() : 0);
      }).catch(function (e) {
        console.warn("Server load failed, trying local cache:", e);
        try {
          var raw = localStorage.getItem("blankdash_" + serverId) || localStorage.getItem("blankdash_blankdash_" + serverId);
          if (!raw) { App.warning("Dashboard not found"); return; }
          var data = JSON.parse(raw);
          var cfg = data.config || data;
          _dash = {
            id: serverId, name: cfg.name || data.name || "Untitled",
            table: cfg.table || data.table || "",
            refreshSec: cfg.refreshSec || data.refreshSec || 0,
            theme: cfg.theme || data.theme || "auto", themeMode: _normThemeMode(cfg.themeMode || data.themeMode),
            shineGradient: !!(cfg.shineGradient !== undefined ? cfg.shineGradient : data.shineGradient),
            shineColors: cfg.shineColors || data.shineColors,
            sharePublic: !!(cfg.sharePublic !== undefined ? cfg.sharePublic : data.sharePublic),
            snap: cfg.snap !== undefined ? cfg.snap : data.snap,
            minW: cfg.minW !== undefined ? cfg.minW : data.minW,
            minH: cfg.minH !== undefined ? cfg.minH : data.minH,
            maxW: cfg.maxW !== undefined ? cfg.maxW : data.maxW,
            maxH: cfg.maxH !== undefined ? cfg.maxH : data.maxH,
            canvasMinW: cfg.canvasMinW !== undefined ? cfg.canvasMinW : data.canvasMinW,
            canvasMinH: cfg.canvasMinH !== undefined ? cfg.canvasMinH : data.canvasMinH,
            fixedCanvas: !!(cfg.fixedCanvas !== undefined ? cfg.fixedCanvas : data.fixedCanvas),
            grid: cfg.grid !== undefined ? cfg.grid : data.grid,
            carouselSec: cfg.carouselSec || data.carouselSec || 5,
            pages: cfg.pages || data.pages || [{ id: "page_1", name: "Page 1", widgets: (cfg.widgets || data.widgets || []) }],
            currentPage: cfg.currentPage || data.currentPage || 0, mode: "edit"
          };
          var _san = _sanitizeDashboard(_dash);
          if (_san) {
            App.info(_san > 0
              ? _san + " widget" + (_san === 1 ? "" : "s") + " were not recognized and were removed on load"
              : "Repaired an issue in this dashboard on load");
          }
          self._restartAutoRefresh();
          self._clearDirty();
          self._syncCanvasSettings();
          self.render();
        } catch (e2) { App.warning("Could not load dashboard"); }
      });
    },

    /* ── Export ── */
    /* Dependency-free mini-map thumbnail of the current page (procedural 2D canvas). */
    _captureThumb: function () {
      var ws = _currentWidgets();
      if (!ws || !ws.length) return null;
      var maxW = 360;
      var cw = _baseCanvasW(), ch = _baseCanvasH();
      ws.forEach(function (w) {
        cw = Math.max(cw, Math.round(w.x + w.width));
        ch = Math.max(ch, Math.round(w.y + w.height));
      });
      var k = Math.min(1, (maxW / cw) || 1);
      var W = Math.max(24, Math.round(cw * k)), H = Math.max(18, Math.round(ch * k));
      var cnv = document.createElement("canvas");
      cnv.width = W; cnv.height = H;
      var ctx2 = cnv.getContext("2d");
      if (!ctx2) return null;
      var dark = _dash.themeMode === "dark" || (_dash.themeMode === "auto" && document.documentElement.getAttribute("data-theme") === "dark");
      ctx2.fillStyle = dark ? "#0f172a" : "#f8fafc";
      ctx2.fillRect(0, 0, W, H);
      ctx2.strokeStyle = dark ? "rgba(148,163,184,0.15)" : "rgba(148,163,184,0.22)";
      ctx2.lineWidth = 1;
      ctx2.beginPath();
      for (var gx = 0; gx <= cw; gx += 120) { ctx2.moveTo(gx * k, 0); ctx2.lineTo(gx * k, H); }
      for (var gy = 0; gy <= ch; gy += 96) { ctx2.moveTo(0, gy * k); ctx2.lineTo(W, gy * k); }
      ctx2.stroke();
      var ordered = ws.slice().sort(function (a, b) { return (a.zIndex || 0) - (b.zIndex || 0); });
      function roundRect(x2, y2, w2, h2, r) {
        r = Math.max(0, Math.min(r, w2 / 2, h2 / 2));
        ctx2.beginPath();
        ctx2.moveTo(x2 + r, y2);
        ctx2.arcTo(x2 + w2, y2, x2 + w2, y2 + h2, r);
        ctx2.arcTo(x2 + w2, y2 + h2, x2, y2 + h2, r);
        ctx2.arcTo(x2, y2 + h2, x2, y2, r);
        ctx2.arcTo(x2, y2, x2 + w2, y2, r);
        ctx2.closePath();
      }
      ordered.forEach(function (w) {
        var x = w.x * k, y = w.y * k, ww = Math.max(4, w.width * k), hh = Math.max(4, w.height * k);
        var info = _lookup(w.type) || {};
        var col = info.color || "#2563eb";
        var rad = Math.round(6 * k);
        roundRect(x, y, ww, hh, rad);
        ctx2.fillStyle = col + "26";
        ctx2.fill();
        ctx2.globalAlpha = 0.6;
        ctx2.strokeStyle = col;
        ctx2.lineWidth = Math.max(1, k);
        roundRect(x, y, ww, hh, rad);
        ctx2.stroke();
        ctx2.globalAlpha = 1;
        var title = "";
        if (w.type === "text") {
          title = String((w.config && w.config.content) || "Text").replace(/<[^>]*>/g, "").trim();
        } else {
          title = w.title || info.label || "";
        }
        if (title) {
          ctx2.font = "600 " + Math.max(8, Math.round(11 * k)) + "px system-ui, sans-serif";
          ctx2.fillStyle = dark ? "#e2e8f0" : "#475569";
          ctx2.textBaseline = "top";
          ctx2.fillText(title.slice(0, 34), x + Math.max(2, 3 * k), y + Math.max(2, 3 * k), ww - Math.max(4, 6 * k));
        }
      });
      try { return cnv.toDataURL("image/jpeg", 0.6); } catch (e) { return null; }
    },

    _exportDash: function (format) {
      var self = this;
      if (!format) {
        var menu = document.createElement("div");
        menu.className = "dash-widget-menu";
        menu.style.cssText = "position:fixed;z-index:10000;min-width:190px;background:var(--color-card-bg,#fff);border:1px solid var(--color-card-border,#e2e8f0);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.15);padding:4px;";
        menu.innerHTML =
          '<div style="padding:6px 10px;font-size:11px;color:var(--color-text-muted);border-bottom:1px solid var(--color-card-border,var(--color-border));margin-bottom:4px;">Export dashboard</div>' +
          '<button data-fmt="pbix" style="display:flex;align-items:center;gap:8px;width:100%;border:none;background:transparent;cursor:pointer;padding:7px 10px;font-size:12px;color:var(--color-text,var(--color-text));text-align:left;border-radius:6px;"><i class="fas fa-chart-column" style="width:14px;color:var(--color-warning);"></i>Power BI (.pbix)</button>' +
          '<button data-fmt="pptx" style="display:flex;align-items:center;gap:8px;width:100%;border:none;background:transparent;cursor:pointer;padding:7px 10px;font-size:12px;color:var(--color-text,var(--color-text));text-align:left;border-radius:6px;"><i class="fas fa-file-powerpoint" style="width:14px;color:var(--color-danger);"></i>PowerPoint (.pptx)</button>' +
          '<button data-fmt="xlsx" style="display:flex;align-items:center;gap:8px;width:100%;border:none;background:transparent;cursor:pointer;padding:7px 10px;font-size:12px;color:var(--color-text,var(--color-text));text-align:left;border-radius:6px;"><i class="fas fa-file-excel" style="width:14px;color:var(--color-success);"></i>Excel (.xlsx)</button>' +
          '<div style="height:1px;background:var(--color-card-border,var(--color-border));margin:4px 0;"></div>' +
          '<button data-fmt="json" style="display:flex;align-items:center;gap:8px;width:100%;border:none;background:transparent;cursor:pointer;padding:7px 10px;font-size:12px;color:var(--color-text,var(--color-text));text-align:left;border-radius:6px;"><i class="fas fa-file-code" style="width:14px;color:var(--color-warning);"></i>JSON file</button>' +
          '<button data-fmt="copy" style="display:flex;align-items:center;gap:8px;width:100%;border:none;background:transparent;cursor:pointer;padding:7px 10px;font-size:12px;color:var(--color-text,var(--color-text));text-align:left;border-radius:6px;"><i class="fas fa-clipboard" style="width:14px;color:var(--color-accent-purple);"></i>Copy config (JSON)</button>' +
          '<button data-fmt="png" style="display:flex;align-items:center;gap:8px;width:100%;border:none;background:transparent;cursor:pointer;padding:7px 10px;font-size:12px;color:var(--color-text,var(--color-text));text-align:left;border-radius:6px;"><i class="fas fa-image" style="width:14px;color:var(--color-primary);"></i>Screenshot (PNG)</button>' +
          '<button data-fmt="print" style="display:flex;align-items:center;gap:8px;width:100%;border:none;background:transparent;cursor:pointer;padding:7px 10px;font-size:12px;color:var(--color-text,var(--color-text));text-align:left;border-radius:6px;"><i class="fas fa-print" style="width:14px;color:var(--color-text-muted);"></i>Print / PDF</button>';
        document.body.appendChild(menu);
        var r = { width: window.innerWidth, height: window.innerHeight };
        menu.style.top = Math.max(4, r.height - 210) + "px";
        menu.style.left = Math.max(4, r.width - 210) + "px";
        var close = function () { if (menu.parentNode) menu.parentNode.removeChild(menu); };
        menu.querySelectorAll("button[data-fmt]").forEach(function (b) {
          b.addEventListener("click", function (e) {
            e.stopPropagation();
            var f = b.getAttribute("data-fmt");
            close();
            self._exportDash(f);
          });
        });
        setTimeout(function () { document.addEventListener("click", close, { once: true }); }, 0);
        return;
      }
      if (format === "pbix") {
        /* ── Server-side Power BI export — a real .pbix report that opens in
           Power BI Desktop with the data model populated (not an empty .pbit
           template). Gated server-side; 403 triggers the upgrade overlay. ── */
        var id = String(_dash.id || "").replace(/^blankdash_/, "");
        if (!id) { App.warning("Save the dashboard first, then export."); return; }
        App.info("Building Power BI export\u2026");
        API.exportDashboardPbit(id).then(function (blob) {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = (_dash.name || "dashboard").replace(/[^\w\-]+/g, "_") + ".pbix";
          document.body.appendChild(a);
          a.click();
          a.remove();
          App.success("Power BI export downloaded \u2014 open it in Power BI Desktop");
        }).catch(function (e) {
          console.warn("pbit export failed:", e);
          App.warning("Power BI export failed: " + (e && e.message ? e.message : "server error"));
        });
      } else if (format === "json") {
        var payload = Object.assign({ version: 1, exported: Date.now() }, _dashConfigSnapshot());
        var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = (_dash.name || "dashboard").replace(/[^\w\-]+/g, "_") + ".json";
        a.click();
        App.success("Dashboard exported as JSON");
      } else if (format === "pptx" || format === "xlsx") {
        /* ── Server-side PPTX / XLSX export via async job ── */
        var id = String(_dash.id || "").replace(/^blankdash_/, "");
        if (!id) { App.warning("Save the dashboard first, then export."); return; }
        var ext = format === "pptx" ? "pptx" : "xlsx";
        var label = format === "pptx" ? "PowerPoint" : "Excel";
        App.info("Creating " + label + " export\u2026");
        API.exportDashboardJob(id, format).then(function (res) {
          var jobId = res && res.jobId;
          if (!jobId) { App.warning("Export job did not start"); return; }
          self._pollExportJob(jobId, ext);
        }).catch(function (e) {
          console.warn("Export job failed:", e);
          App.warning("Export failed: " + (e && e.message ? e.message : "server error"));
        });
      } else if (format === "copy") {
        var txt = JSON.stringify(Object.assign({ version: 1, exported: Date.now() }, _dashConfigSnapshot()), null, 2);
        var done = function () { App.success("Config copied to clipboard"); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(txt).then(done, function () {
            var ta = document.createElement("textarea");
            ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
            document.body.appendChild(ta); ta.select();
            try { document.execCommand("copy"); done(); } catch (e) { App.warning("Copy failed"); }
            ta.remove();
          });
        } else {
          var ta2 = document.createElement("textarea");
          ta2.value = txt; ta2.style.position = "fixed"; ta2.style.opacity = "0";
          document.body.appendChild(ta2); ta2.select();
          try { document.execCommand("copy"); done(); } catch (e) { App.warning("Copy failed"); }
          ta2.remove();
        }
      } else if (format === "print") {
        window.print();
      } else if (format === "png") {
        var canvas = document.getElementById("bd-canvas");
        if (!canvas) { App.warning("Nothing to capture"); return; }
        var self = this;
        this._loadHtml2canvas().then(function () {
          if (!window.html2canvas) {
            App.warning("Screenshot export isn't available right now (html2canvas could not be loaded)");
            return;
          }
          return window.html2canvas(canvas).then(function (c) {
            var a = document.createElement("a");
            a.href = c.toDataURL("image/png");
            a.download = (_dash.name || "dashboard").replace(/[^\w\-]+/g, "_") + ".png";
            a.click();
            App.success("Screenshot saved");
          });
        }).catch(function () {
          App.warning("Screenshot export isn't available right now (html2canvas could not be loaded)");
        });
      }
    },

    /* Lazy-load html2canvas once so whole-dashboard PNG export works without
       a build-time dependency. Caches the resolved promise for repeat saves. */
    _loadHtml2canvas: function () {
      if (window.html2canvas) return Promise.resolve();
      if (this._h2cPromise) return this._h2cPromise;
      var self = this;
      this._h2cPromise = new Promise(function (resolve, reject) {
        var s = document.createElement("script");
        s.src = "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js";
        s.async = true;
        s.onload = function () { resolve(window.html2canvas); };
        s.onerror = function () { delete self._h2cPromise; reject(new Error("html2canvas load failed")); };
        document.head.appendChild(s);
      });
      return this._h2cPromise;
    },

    /* ── Server-side export job polling (PPTX / XLSX) ── */
    _pollExportJob: function (jobId, ext) {
      var self = this;
      var attempts = 0;
      var maxAttempts = 120;
      var poll = function () {
        attempts++;
        if (attempts > maxAttempts) { App.warning("Export timed out"); return; }
        API.exportJobStatus(jobId).then(function (res) {
          var job = res && res.job;
          if (!job) { App.warning("Export job not found"); return; }
          if (job.status === "succeeded") {
            var a = document.createElement("a");
            a.href = (window.__API_BASE__ || "") + "/dashboards/export/download/" + encodeURIComponent(jobId);
            a.download = (_dash.name || "dashboard").replace(/[^\w\-]+/g, "_") + "." + ext;
            document.body.appendChild(a);
            a.click();
            a.remove();
            App.success("Dashboard exported as " + ext.toUpperCase());
          } else if (job.status === "failed") {
            App.warning("Export failed: " + (job.error || "unknown error"));
          } else {
            setTimeout(poll, 1500);
          }
        }).catch(function (e) {
          console.warn("Export poll error:", e);
          if (attempts < 5) setTimeout(poll, 2000);
          else App.warning("Export poll failed");
        });
      };
      poll();
    },

    /* ── Saved dashboards gallery ── */
    _showGallery: function () {
      var self = this;
      var overlay;
      var html = '<div class="bd-gal-overlay" onclick="if(event.target===this)this.remove()">' +
        '<div class="bd-gal-modal">' +
          '<div class="bd-gal-head">' +
            '<h3 style="margin:0;font-size:17px;font-weight:700;">My Dashboards</h3>' +
            '<button class="btn btn-sm btn-ghost" onclick="this.closest(\'.bd-gal-overlay\').remove()">&times;</button>' +
          '</div>' +
          '<div class="bd-gal-list" id="bd-gal-list"><div class="bd-gal-empty">Loading...</div></div>' +
        '</div>' +
      '</div>';
      document.body.insertAdjacentHTML("beforeend", html);
      overlay = document.querySelector(".bd-gal-modal");
      self._renderGalleryItems(overlay);
    },
    _renderGalleryItems: function (overlay) {
      var self = this;
      var listEl = overlay ? overlay.querySelector("#bd-gal-list") : null;
      var render = function (list) {
        if (!listEl) return;
        if (!list || !list.length) { listEl.innerHTML = '<div class="bd-gal-empty">No saved dashboards yet.</div>'; return; }
        listEl.innerHTML = list.map(function (d, i) {
          var id = _esc(String(d.id));
          var active = _dash.id === String(d.id) ? ' style="outline:2px solid var(--color-primary)"' : "";
          var table = d.table || "";
          var time = d.time || (d.updated_at ? new Date(d.updated_at).getTime() : 0);
          return '<div class="bd-gal-item" data-id="' + id + '"' + active + '>' +
            '<div class="bd-gal-thumb"><div class="bd-gal-icon"><i class="fas fa-chart-pie"></i></div></div>' +
            '<div class="bd-gal-info">' +
              '<div class="bd-gal-name">' + _esc(d.name || "Untitled Dashboard") + '</div>' +
              '<div class="bd-gal-meta">' + (d.pageCount || 1) + ' page' + ((d.pageCount || 1) > 1 ? "s" : "") + (table ? " · " + _esc(table) : "") + (time ? " · " + new Date(time).toLocaleDateString() : "") + '</div>' +
            '</div>' +
            '<div class="bd-gal-actions">' +
              '<button class="btn btn-sm btn-secondary" onclick="event.stopPropagation();BlankDash._loadSaved(\'' + id + '\');document.querySelector(\'.bd-gal-overlay\')?.remove()">Open</button>' +
              '<button class="btn btn-sm btn-ghost" onclick="event.stopPropagation();BlankDash._deleteSaved(\'' + id + '\', this)"><i class="fas fa-trash"></i></button>' +
            '</div>' +
          '</div>';
        }).join("");
      };
      API.dashboards().then(function (res) {
        var list = (res && res.dashboards) || [];
        var items = list.map(function (d) {
          var cfg = null;
          try { cfg = (d.layout && d.layout._blankdash) || (d.layout && typeof d.layout === "object" ? d.layout : null); } catch (e) {}
          var widgets = (d.widgets || []).length;
          if (cfg && Array.isArray(cfg.pages)) widgets = cfg.pages.reduce(function (a, p) { return a + (p.widgets ? p.widgets.length : 0); }, 0);
          return {
            id: d.id,
            name: cfg && cfg.name ? cfg.name : (d.name || "Untitled Dashboard"),
            table: cfg && cfg.table ? cfg.table : "",
            time: (cfg && cfg.time) || (d.updated_at ? new Date(d.updated_at).getTime() : 0),
            pageCount: (cfg && cfg.pages) ? cfg.pages.length : 1,
            widgetCount: widgets
          };
        });
        render(items);
      }).catch(function () {
        render([]);
      });
    },
    _deleteSaved: function (id, btn) {
      var self = this;
      try { localStorage.removeItem("blankdash_" + String(id).replace(/^blankdash_/, "")); } catch (e) {}
      if (btn) { var item = btn.closest(".bd-gal-item"); if (item) item.remove(); }
      var serverId = String(id).replace(/^blankdash_/, "").split("/")[0];
      API.deleteDashboard(serverId).then(function () {
        App.success("Dashboard deleted");
      }).catch(function (e) {
        console.warn("Server delete failed, removed local copy only:", e);
        App.warning("Removed local copy; server delete failed");
      });
    },

    /* ── Present / View mode ── */
    _present: function () {
      _dash.mode = "present";
      this._carouselStop(true);
      this._presentStart = Date.now();
      this._startPresentTimer();
      this.render();
      App.info("Presentation mode — ←/→ page, Home/End first/last, 1–9 jump, Space auto-play, F fullscreen, Esc exit");
    },
    _exitPresent: function () {
      this._carouselStop(true);
      this._stopPresentTimer();
      _dash.mode = "edit";
      if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
      this.render();
    },
    _startPresentTimer: function () {
      this._stopPresentTimer();
      var self = this;
      this._presentTimer = setInterval(function () {
        var el = document.getElementById("bd-present-timer");
        if (!el) { self._stopPresentTimer(); return; }
        var s = Math.floor((Date.now() - self._presentStart) / 1000);
        var mm = String(Math.floor(s / 60)).padStart(2, "0");
        var ss = String(s % 60).padStart(2, "0");
        el.textContent = mm + ":" + ss;
      }, 1000);
    },
    _stopPresentTimer: function () {
      if (this._presentTimer) { clearInterval(this._presentTimer); this._presentTimer = null; }
    },
    _presentRestart: function () {
      this._carouselStop(true);
      this._presentStart = Date.now();
      var el = document.getElementById("bd-present-timer");
      if (el) el.textContent = "00:00";
      if (_dash.currentPage !== 0) this._switchPage(0);
      else this._carouselStart();
      App.info("Restarted presentation");
    },
    _toggleFullscreen: function () {
      if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
      else {
        var el = document.getElementById("page-content");
        if (el && el.requestFullscreen) el.requestFullscreen();
      }
    },
    _onFsChange: function () {
      var self = BlankDash;
      if (document.fullscreenElement) {
        var meta = document.querySelector(".bd-present-page");
        if (meta) self._updatePresentMeta();
        if (window.__bdPresentIdleHide) window.__bdPresentIdleHide();
        /* The fullscreen transition fires a resize before the layout settles,
           so recompute the fit once the fullscreen element has its final size. */
        setTimeout(function () { self._fitPresentZoom(); }, 150);
      } else {
        if (window.__bdPresentIdleReveal) window.__bdPresentIdleReveal();
        setTimeout(function () { self._fitPresentZoom(); }, 150);
      }
    },
        _updatePresentMeta: function () {
      var meta = document.querySelector(".bd-present-page");
      if (meta) meta.textContent = "Page " + (_dash.currentPage + 1) + " of " + _dash.pages.length;
    },

    /* C8 — presenter view: current page notes + running timer + next-page preview. */
    _togglePresenterView: function () {
      var pv = document.getElementById("bd-presenter-view");
      if (pv) { pv.remove(); this._stopPresenterViewTimer(); return; }
      if (_dash.mode !== "present") return;
      this._pvStart = Date.now();
      var html = '<div class="bd-presenter-view" id="bd-presenter-view">' +
        '<div class="bd-pv-header">' +
          '<span class="bd-pv-title"><i class="fas fa-clipboard-list"></i> Presenter view</span>' +
          '<span class="bd-pv-timer" id="bd-pv-timer">00:00</span>' +
          '<button class="bd-pv-close" onclick="BlankDash._togglePresenterView()" title="Close (N)" aria-label="Close presenter view"><i class="fas fa-times"></i></button>' +
        '</div>' +
        '<div class="bd-pv-body">' +
          '<div class="bd-pv-notes">' +
            '<div class="bd-pv-label">Notes — <span id="bd-pv-page"></span></div>' +
            '<div class="bd-pv-notes-text" id="bd-pv-notes-text"></div>' +
          '</div>' +
          '<div class="bd-pv-preview">' +
            '<div class="bd-pv-label">Next page</div>' +
            '<div class="bd-pv-preview-canvas" id="bd-pv-preview-canvas"></div>' +
          '</div>' +
        '</div>' +
      '</div>';
      document.body.insertAdjacentHTML("beforeend", html);
      this._updatePresenterView();
      this._startPresenterViewTimer();
    },
    _startPresenterViewTimer: function () {
      this._stopPresenterViewTimer();
      var self = this;
      this._pvTimer = setInterval(function () {
        var el = document.getElementById("bd-pv-timer");
        if (!el) { self._stopPresenterViewTimer(); return; }
        var s = Math.floor((Date.now() - self._pvStart) / 1000);
        var mm = String(Math.floor(s / 60)).padStart(2, "0");
        var ss = String(s % 60).padStart(2, "0");
        el.textContent = mm + ":" + ss;
      }, 1000);
    },
    _stopPresenterViewTimer: function () {
      if (this._pvTimer) { clearInterval(this._pvTimer); this._pvTimer = null; }
    },
    _updatePresenterView: function () {
      var pv = document.getElementById("bd-presenter-view");
      if (!pv) return;
      var pg = _dash.pages[_dash.currentPage] || {};
      var pageEl = document.getElementById("bd-pv-page");
      if (pageEl) pageEl.textContent = pg.name || ("Page " + (_dash.currentPage + 1));
      var notesEl = document.getElementById("bd-pv-notes-text");
      if (notesEl) {
        var notes = (pg.notes || "").trim();
        notesEl.innerHTML = notes ? notes.replace(/\n/g, "<br>") : '<span class="bd-pv-empty">No notes for this page.</span>';
      }
      this._renderPresentPreview();
    },
    _renderPresentPreview: function () {
      var box = document.getElementById("bd-pv-preview-canvas");
      if (!box) return;
      var next = _dash.pages[_dash.currentPage + 1];
      if (!next) {
        box.innerHTML = '<div class="bd-pv-empty">Last page.</div>';
        return;
      }
      var ws = next.widgets || [];
      var cw = _baseCanvasW(), ch = _baseCanvasH();
      var items = ws.map(function (w) {
        var left = Math.round((w.x / cw) * 100 * 100) / 100;
        var top = Math.round((w.y / ch) * 100 * 100) / 100;
        var wPct = Math.round((w.width / cw) * 100 * 100) / 100;
        var hPct = Math.round((w.height / ch) * 100 * 100) / 100;
        var color = (_lookup(w.type) || {}).color || "#2563eb";
        return '<div class="bd-pv-widget" style="left:' + left + '%;top:' + top + '%;width:' + wPct + '%;height:' + hPct + '%;background:' + color + ';" title="' + _esc(w.title || w.type) + '"></div>';
      }).join("");
      box.innerHTML = '<div class="bd-pv-page-name">' + _esc(next.name || ("Page " + (_dash.currentPage + 2))) + '</div>' + items;
    },

    /* ── Canvas controls: zoom, grid, undo/redo ── */
    _zoomIn: function () { window.__bdAutoFitLive = false; this._applyZoom(_clamp(_zoom * 1.2, 0.4, 2.5)); },
    _zoomOut: function () { window.__bdAutoFitLive = false; this._applyZoom(_clamp(_zoom / 1.2, 0.4, 2.5)); },
    _resetZoom: function () { window.__bdAutoFitLive = false; this._applyZoom(1); },

    /* Keep the whole canvas fitting the viewport until the user zooms manually. */
    _maybeAutoFit: function () {
      if (_dash.mode === "present" || !window.__bdAutoFitLive) return;
      this._fitZoom();
      this._updateOverflowCues();
    },
    /* Fade-edge cues when the canvas overflows the visible viewport */
    _updateOverflowCues: function () {
      var wrap = document.querySelector(".bd-canvas-wrap");
      if (!wrap) return;
      var sl = wrap.scrollLeft, sw = wrap.scrollWidth, cw = wrap.clientWidth;
      wrap.classList.toggle("bd-overflow-l", sl > 2);
      wrap.classList.toggle("bd-overflow-r", sl + cw < sw - 2);
    },
    _applyZoom: function (z) {
      _zoom = z;
      var canvas = document.getElementById("bd-canvas");
      var wrap = document.querySelector(".bd-canvas-wrap");
      if (canvas) {
        canvas.style.transformOrigin = "top left";
        canvas.style.transform = "scale(" + _zoom + ")";
      }
      var ann = document.getElementById("bd-ann-layer");
      if (ann) ann.style.transform = "scale(" + _zoom + ")";
      if (wrap && _dash.mode !== "present") wrap.style.paddingBottom = "40px";
      var pct = document.getElementById("bd-zoom-pct");
      if (pct) pct.textContent = Math.round(_zoom * 100) + "%";
      if (_dash.mode !== "present") this._renderRulers();
      this._updateStatusBar();
    },

    /* ── Status bar (edit mode) ──
       Power BI-style utility strip under the canvas: pointer grid position,
       selection count, canvas size, current page, zoom. _updateStatusBar is a
       cheap innerText patch so it can be called from mousemove / zoom / selection. */
    _updateStatusBar: function () {
      var sb = document.getElementById("bd-statusbar");
      if (!sb) return;
      var pos = document.getElementById("bd-status-pos");
      if (pos) pos.textContent = (_bdStatusLast.x != null ? ("Line " + _bdStatusLast.y + ", Col " + _bdStatusLast.x) : "Line 0, Col 0");
      var sel = document.getElementById("bd-status-sel");
      if (sel) sel.textContent = _selectedIds.length ? (_selectedIds.length + " selected") : "No selection";
      var size = document.getElementById("bd-status-size");
      if (size) {
        var canvas = document.getElementById("bd-canvas");
        var w = canvas ? (Math.round(parseFloat(canvas.style.width) || _baseCanvasW())) : Math.round(_baseCanvasW());
        size.textContent = w + " × " + (canvas ? (Math.round(parseFloat(canvas.style.height) || _baseCanvasH())) : Math.round(_baseCanvasH()));
      }
      var page = document.getElementById("bd-status-page");
      if (page) page.textContent = "Page " + (_dash.currentPage + 1) + " of " + _dash.pages.length;
      var zoom = document.getElementById("bd-status-zoom");
      if (zoom) zoom.textContent = Math.round(_zoom * 100) + "%";
      var save = document.getElementById("bd-status-save");
      if (save) save.textContent = _dirty ? "● Unsaved changes" : "Saved";
    },
    /* Track the pointer over the wrapped canvas, rAF-throttled, and echo the
       grid position (unscaled by zoom) into the status bar. */
    _bindStatusPointer: function () {
      var wrap = document.querySelector(".bd-canvas-wrap");
      if (!wrap || wrap.__bdStatusBound) return;
      wrap.__bdStatusBound = true;
      var self = this;
      wrap.addEventListener("mousemove", function (e) {
        if (_dash.mode !== "edit") return;
        if (_bdStatusThrottle) return;
        _bdStatusThrottle = true;
        requestAnimationFrame(function () {
          _bdStatusThrottle = false;
          var canvas = document.getElementById("bd-canvas");
          if (!canvas) return;
          var r = canvas.getBoundingClientRect();
          _bdStatusLast.x = Math.max(0, Math.floor((e.clientX - r.left) / _zoom));
          _bdStatusLast.y = Math.max(0, Math.floor((e.clientY - r.top) / _zoom));
          self._updateStatusBar();
        });
      });
      wrap.addEventListener("mouseleave", function () {
        _bdStatusLast.x = null; _bdStatusLast.y = null;
        self._updateStatusBar();
      });
    },
    _showStatusToast: function (msg) {
      var sb = document.getElementById("bd-statusbar");
      if (!sb) return;
      var el = document.createElement("span");
      el.className = "bd-status-toast";
      el.textContent = msg;
      sb.appendChild(el);
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 1600);
    },

    /* ── Minimap toggle (C) ── */
    _toggleMinimap: function (force) {
      _bdMinimapOn = typeof force === "boolean" ? force : !_bdMinimapOn;
      try { localStorage.setItem("bd_minimap_on", _bdMinimapOn ? "1" : "0"); } catch (e) {}
      var btn = document.getElementById("bd-minimap-toggle");
      if (btn) btn.classList.toggle("active", _bdMinimapOn);
      _minimapRefresh();
      _minimapPin();
      if (_bdMinimapOn) this._showStatusToast("Navigator on — press M to hide");
      return _bdMinimapOn;
    },
    _bindMinimap: function () {
      var mm = document.getElementById("bd-minimap");
      if (!mm) return;
      var tbtn = document.getElementById("bd-minimap-toggle");
      if (tbtn) tbtn.classList.toggle("active", !!_bdMinimapOn);
      if (mm.__bdMinimapBound) return;
      mm.__bdMinimapBound = true;
      mm.__bdMinimapBound = true;
      var wrap = document.querySelector(".bd-canvas-wrap");
      if (!wrap) return;
      var panning = false;
      function panTo(e) {
        var draw = document.getElementById("bd-minimap-canvas");
        if (!draw) return;
        var rect = draw.getBoundingClientRect();
        var mw = draw.offsetWidth, mh = draw.offsetHeight;
        if (mw <= 0 || mh <= 0) return;
        var fx = (e.clientX - rect.left) / mw;
        var fy = (e.clientY - rect.top) / mh;
        var sMaxX = Math.max(1, wrap.scrollWidth - wrap.clientWidth);
        var sMaxY = Math.max(1, wrap.scrollHeight - wrap.clientHeight);
        wrap.scrollLeft = fx * sMaxX;
        wrap.scrollTop = fy * sMaxY;
        _minimapPin();
        _minimapRefresh();
      }
      mm.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
        panning = true;
        panTo(e);
      });
      document.addEventListener("mousemove", function (e) {
        if (panning) panTo(e);
      });
      document.addEventListener("mouseup", function () { panning = false; });
    },

    /* ── Zoom popover (A4) ── */
    _toggleZoomMenu: function (e) {
      if (e) e.stopPropagation();
      if (_bdZoomMenuEl) { this._closeZoomMenu(); return; }
      var btn = document.getElementById("bd-zoom-pct");
      if (!btn) return;
      var r = btn.getBoundingClientRect();
      var menu = document.createElement("div");
      menu.className = "bd-zoom-menu";
      menu.style.top = (r.bottom + 6) + "px";
      menu.style.right = (Math.max(4, window.innerWidth - r.right)) + "px";
      function preset(p, label) {
        return '<button class="bd-zoom-row' + (Math.abs(_zoom - p) < 0.001 ? " active" : "") + '" onclick="BlankDash._zoomTo(' + p + ')"><span>' + label + '</span><span class="bd-zoom-row-pct">' + Math.round(p * 100) + '%</span></button>';
      }
      menu.innerHTML =
        '<div class="bd-zoom-title">Zoom</div>' +
        '<button class="bd-zoom-row" onclick="BlankDash._fitZoom()"><span>Fit to screen</span><span class="bd-zoom-row-pct"><i class="fas fa-maximize"></i></span></button>' +
        '<button class="bd-zoom-row' + (Math.abs(_zoom - 1) < 0.001 ? " active" : "") + '" onclick="BlankDash._zoomTo(1)"><span>Actual size</span><span class="bd-zoom-row-pct">100%</span></button>' +
        '<div class="bd-zoom-sep"></div>' +
        preset(0.5, "Fit half") +
        preset(0.75, "75%") +
        preset(1.25, "125%") +
        preset(1.5, "150%") +
        preset(2, "200%") +
        '<div class="bd-zoom-custom"><input id="bd-zoom-custom" type="number" min="40" max="250" value="' + Math.round(_zoom * 100) + '" onkeydown="if(event.key===\'Enter\')BlankDash._zoomToPct(this.value)">' +
        '<button class="btn btn-sm btn-ghost" onclick="BlankDash._zoomToPct(document.getElementById(\'bd-zoom-custom\').value)">Apply</button></div>';
      document.body.appendChild(menu);
      _bdZoomMenuEl = menu;
      setTimeout(function () {
        document.addEventListener("mousedown", function close() {
          document.removeEventListener("mousedown", close);
          if (window.BlankDash) window.BlankDash._closeZoomMenu();
        });
      }, 0);
    },
    _closeZoomMenu: function () {
      if (_bdZoomMenuEl && _bdZoomMenuEl.parentNode) _bdZoomMenuEl.parentNode.removeChild(_bdZoomMenuEl);
      _bdZoomMenuEl = null;
    },
    _zoomTo: function (p) { window.__bdAutoFitLive = false; this._closeZoomMenu(); this._applyZoom(_clamp(p, 0.4, 2.5)); },
    _zoomToPct: function (pct) {
      var v = parseFloat(pct);
      if (isNaN(v)) return;
      window.__bdAutoFitLive = false;
      this._closeZoomMenu();
      this._applyZoom(_clamp(v / 100, 0.4, 2.5));
    },

    /* Present mode: scale the dashboard so the whole canvas (and all widgets)
       fits on the screen, centred — widgets then size to the display. */
    _fitPresentZoom: function () {
      if (_dash.mode !== "present") return;
      if (this._mobileFlowActive()) return; /* responsive flow layout instead */
      var wrap = document.querySelector(".bd-canvas-wrap");
      var canvas = document.getElementById("bd-canvas");
      if (!wrap || !canvas) return;
      var cw = parseFloat(canvas.style.width) || _baseCanvasW();
      var ch = parseFloat(canvas.style.height) || _baseCanvasH();
      if (cw <= 0 || ch <= 0) return;
      var cs = getComputedStyle(wrap);
      var padL = parseFloat(cs.paddingLeft) || 0;
      var padR = parseFloat(cs.paddingRight) || 0;
      var padT = parseFloat(cs.paddingTop) || 0;
      var padB = parseFloat(cs.paddingBottom) || 0;
      /* In fullscreen the wrap spans the whole viewport (toolbar/tabs are
         floating overlays), so drop the safety margin and fill the screen. */
      var margin = document.fullscreenElement ? 0 : 16;
      var availW = Math.max(120, wrap.clientWidth - padL - padR - margin);
      var availH = Math.max(100, wrap.clientHeight - padT - padB - margin);
      var z = _clamp(Math.min(availW / cw, availH / ch), 0.15, 1.5);
      this._applyZoom(z);
      /* The present-stage wrapper is sized to the *visual* (scaled) canvas and
         centred by `margin: auto`, so the full dashboard is always visible and
         centred without phantom scrollbars. */
      var stage = document.getElementById("bd-present-stage");
      if (stage) {
        stage.style.width = (cw * z) + "px";
        stage.style.height = (ch * z) + "px";
      }
      wrap.scrollLeft = 0;
      wrap.scrollTop = 0;
    },

    /* ── C10 — live annotations (present mode) ──
       Session-only drawing over the canvas: pen / highlight / arrow / box /
       text / eraser. Stored per page id in _annotations, rendered into an SVG
       layer that shares the canvas transform so strokes stay zoom-scaled. */
    _annPageId: function () {
      return _dash.pages[_dash.currentPage] ? _dash.pages[_dash.currentPage].id : "page_" + _dash.currentPage;
    },
    _toggleAnnotations: function () {
      if (this._mobileFlowActive()) { App.info("Annotations are available on desktop present view"); return; }
      _annToolbarOn = !_annToolbarOn;
      if (!_annToolbarOn) _annTool = null;
      this._renderAnnToolbar();
      this._renderAnnLayer();
    },
    _setAnnTool: function (t) {
      _annTool = (_annTool === t) ? null : t;
      this._renderAnnToolbar();
      var layer = document.getElementById("bd-ann-layer");
      if (layer) layer.style.cursor = _annTool ? "crosshair" : "default";
    },
    _setAnnColor: function (c) {
      _annColor = c;
      this._renderAnnToolbar();
    },
    _renderAnnToolbar: function () {
      var old = document.getElementById("bd-ann-toolbar");
      if (old) old.remove();
      if (!_annToolbarOn || _dash.mode !== "present") return;
      if (this._mobileFlowActive()) return;
      var tools = [
        ["pen", "pen", "Pen (1)"], ["highlight", "highlighter", "Highlight (2)"],
        ["arrow", "arrow-right", "Arrow (3)"], ["box", "square", "Box (4)"],
        ["text", "font", "Text (5)"], ["eraser", "eraser", "Eraser (6)"]
      ];
      var colors = ["#ff3b30", "#ff9500", "#ffd60a", "#34c759", "#0a84ff", "#2563eb", "#ffffff"];
      var h = '<div class="bd-ann-toolbar" id="bd-ann-toolbar">';
      tools.forEach(function (t) {
        h += '<button class="bd-ann-btn' + (_annTool === t[0] ? ' active' : '') + '" onclick="BlankDash._setAnnTool(\'' + t[0] + '\')" title="' + t[2] + '"><i class="fas fa-' + t[1] + '"></i></button>';
      });
      h += '<span class="bd-ann-sep"></span>';
      colors.forEach(function (c) {
        h += '<button class="bd-ann-color' + (_annColor === c ? ' active' : '') + '" style="background:' + c + '" onclick="BlankDash._setAnnColor(\'' + c + '\')" title="Color"></button>';
      });
      h += '<span class="bd-ann-sep"></span>';
      h += '<button class="bd-ann-btn" onclick="BlankDash._annClearPage()" title="Clear this page"><i class="fas fa-trash"></i></button>';
      h += '<button class="bd-ann-btn" onclick="BlankDash._toggleAnnotations()" title="Close annotations (A)"><i class="fas fa-xmark"></i></button>';
      h += '</div>';
      var stage = document.getElementById("bd-present-stage");
      if (stage) {
        var tmp = document.createElement("div");
        tmp.innerHTML = h;
        stage.appendChild(tmp.firstElementChild);
      }
    },
    _annClearPage: function () {
      _annotations[this._annPageId()] = [];
      this._renderAnnLayer();
    },
    _renderAnnLayer: function () {
      var old = document.getElementById("bd-ann-layer");
      if (old) old.remove();
      _annLayer = null;
      if (_dash.mode !== "present") return;
      if (this._mobileFlowActive()) return; /* responsive flow stacks widgets; canvas coords no longer map */
      var stage = document.getElementById("bd-present-stage");
      var canvas = document.getElementById("bd-canvas");
      if (!stage || !canvas) return;
      var cw = parseFloat(canvas.style.width) || _baseCanvasW();
      var ch = parseFloat(canvas.style.height) || _baseCanvasH();
      var ns = "http://www.w3.org/2000/svg";
      var svg = document.createElementNS(ns, "svg");
      svg.setAttribute("id", "bd-ann-layer");
      svg.setAttribute("width", cw);
      svg.setAttribute("height", ch);
      svg.setAttribute("viewBox", "0 0 " + cw + " " + ch);
      svg.style.position = "absolute";
      svg.style.top = "0";
      svg.style.left = "0";
      svg.style.transformOrigin = "top left";
      svg.style.transform = "scale(" + _zoom + ")";
      svg.style.pointerEvents = _annTool ? "auto" : "none";
      svg.style.zIndex = "5";
      var pageId = this._annPageId();
      var self = this;
      (_annotations[pageId] || []).forEach(function (a) { self._annDrawShape(svg, a); });
      svg.addEventListener("pointerdown", function (e) { self._annPointerDown(e, svg); });
      svg.addEventListener("pointermove", function (e) { self._annPointerMove(e, svg); });
      svg.addEventListener("pointerup", function (e) { self._annPointerUp(e, svg); });
      svg.addEventListener("pointerleave", function () { self._annPointerUp(null, svg); });
      stage.appendChild(svg);
      _annLayer = svg;
    },
    _annToCanvas: function (e, svg) {
      var r = svg.getBoundingClientRect();
      return { x: (e.clientX - r.left) / _zoom, y: (e.clientY - r.top) / _zoom };
    },
    _annDrawShape: function (svg, a) {
      var ns = "http://www.w3.org/2000/svg";
      var el;
      if (a.type === "pen" || a.type === "highlight") {
        el = document.createElementNS(ns, "polyline");
        el.setAttribute("points", a.points.map(function (p) { return p.x + "," + p.y; }).join(" "));
        el.setAttribute("fill", "none");
        el.setAttribute("stroke", a.color);
        el.setAttribute("stroke-width", a.type === "highlight" ? 22 : 3);
        el.setAttribute("stroke-linecap", "round");
        el.setAttribute("stroke-linejoin", "round");
        if (a.type === "highlight") el.setAttribute("opacity", "0.35");
      } else if (a.type === "arrow") {
        el = document.createElementNS(ns, "g");
        var line = document.createElementNS(ns, "line");
        line.setAttribute("x1", a.x1); line.setAttribute("y1", a.y1);
        line.setAttribute("x2", a.x2); line.setAttribute("y2", a.y2);
        line.setAttribute("stroke", a.color); line.setAttribute("stroke-width", 3);
        line.setAttribute("stroke-linecap", "round");
        el.appendChild(line);
        var ang = Math.atan2(a.y2 - a.y1, a.x2 - a.x1);
        var hw = 14, hl = 18;
        var p1 = { x: a.x2 - hl * Math.cos(ang - 0.45), y: a.y2 - hl * Math.sin(ang - 0.45) };
        var p2 = { x: a.x2 - hl * Math.cos(ang + 0.45), y: a.y2 - hl * Math.sin(ang + 0.45) };
        var head = document.createElementNS(ns, "polygon");
        head.setAttribute("points", a.x2 + "," + a.y2 + " " + p1.x + "," + p1.y + " " + p2.x + "," + p2.y);
        head.setAttribute("fill", a.color);
        el.appendChild(head);
      } else if (a.type === "box") {
        el = document.createElementNS(ns, "rect");
        el.setAttribute("x", Math.min(a.x1, a.x2)); el.setAttribute("y", Math.min(a.y1, a.y2));
        el.setAttribute("width", Math.abs(a.x2 - a.x1)); el.setAttribute("height", Math.abs(a.y2 - a.y1));
        el.setAttribute("fill", "none"); el.setAttribute("stroke", a.color);
        el.setAttribute("stroke-width", 3); el.setAttribute("stroke-dasharray", "8 5");
      } else if (a.type === "text") {
        el = document.createElementNS(ns, "text");
        el.setAttribute("x", a.x); el.setAttribute("y", a.y);
        el.setAttribute("fill", a.color); el.setAttribute("font-size", 30);
        el.setAttribute("font-weight", "700");
        el.setAttribute("paint-order", "stroke");
        el.setAttribute("stroke", "rgba(0,0,0,0.55)");
        el.setAttribute("stroke-width", 4);
        el.textContent = a.text;
      }
      if (el) {
        el.setAttribute("data-ann-id", a.id);
        svg.appendChild(el);
      }
    },
    _annPointerDown: function (e, svg) {
      if (!_annTool || _annTool === "eraser") {
        if (_annTool === "eraser") this._annEraseAt(e, svg);
        return;
      }
      e.preventDefault();
      var p = this._annToCanvas(e, svg);
      var pageId = this._annPageId();
      if (!_annotations[pageId]) _annotations[pageId] = [];
      var a = { id: "ann_" + Date.now() + "_" + Math.floor(Math.random() * 1e4), type: _annTool, color: _annColor };
      if (_annTool === "pen" || _annTool === "highlight") {
        a.points = [p];
      } else if (_annTool === "arrow" || _annTool === "box") {
        a.x1 = p.x; a.y1 = p.y; a.x2 = p.x; a.y2 = p.y;
      } else if (_annTool === "text") {
        var txt = window.prompt("Annotation text:", "");
        if (txt == null || !txt.trim()) return;
        a.x = p.x; a.y = p.y; a.text = txt.trim();
        _annotations[pageId].push(a);
        this._renderAnnLayer();
        return;
      }
      _annotations[pageId].push(a);
      _annDrawing = a;
      try { svg.setPointerCapture(e.pointerId); } catch (err) {}
    },
    _annPointerMove: function (e, svg) {
      if (!_annDrawing) return;
      var p = this._annToCanvas(e, svg);
      if (_annDrawing.type === "pen" || _annDrawing.type === "highlight") {
        _annDrawing.points.push(p);
        var pl = svg.querySelector('[data-ann-id="' + _annDrawing.id + '"]');
        if (pl) pl.setAttribute("points", _annDrawing.points.map(function (q) { return q.x + "," + q.y; }).join(" "));
      } else {
        _annDrawing.x2 = p.x; _annDrawing.y2 = p.y;
        this._renderAnnLayer();
      }
    },
    _annPointerUp: function (e, svg) {
      if (!_annDrawing) return;
      if (e) {
        var p = this._annToCanvas(e, svg);
        if (_annDrawing.type === "arrow" || _annDrawing.type === "box") {
          _annDrawing.x2 = p.x; _annDrawing.y2 = p.y;
        }
      }
      _annDrawing = null;
      this._renderAnnLayer();
    },
    _annEraseAt: function (e, svg) {
      var p = this._annToCanvas(e, svg);
      var pageId = this._annPageId();
      var list = _annotations[pageId] || [];
      for (var i = list.length - 1; i >= 0; i--) {
        var a = list[i];
        var hit = false;
        if (a.type === "pen" || a.type === "highlight") {
          for (var j = 0; j < a.points.length; j++) {
            if (Math.abs(a.points[j].x - p.x) < 12 && Math.abs(a.points[j].y - p.y) < 12) { hit = true; break; }
          }
        } else if (a.type === "arrow" || a.type === "box") {
          var x1 = Math.min(a.x1, a.x2), x2 = Math.max(a.x1, a.x2);
          var y1 = Math.min(a.y1, a.y2), y2 = Math.max(a.y1, a.y2);
          if (p.x >= x1 - 12 && p.x <= x2 + 12 && p.y >= y1 - 12 && p.y <= y2 + 12) hit = true;
        } else if (a.type === "text") {
          if (Math.abs(a.x - p.x) < 40 && Math.abs(a.y - p.y) < 20) hit = true;
        }
        if (hit) { list.splice(i, 1); }
      }
      this._renderAnnLayer();
    },

    /* ── P3.12 — responsive present mode ──
       Narrow viewports (mobile/portrait) switch the present canvas from
       whole-board scaling to a stacked responsive flow: KPI cards wrap into a
       half-width grid, charts go full width, tooltip pages stay hidden. */
    _flowMode: function () {
      if (_dash.mode !== "present") return "none";
      var w = window.innerWidth || 0;
      if (w < 720) return "mobile";
      if (w < 1024) return "tablet";
      return "none";
    },
    _mobileFlowActive: function () {
      return this._flowMode() !== "none";
    },
    _syncMobileFlow: function () {
      var canvas = document.getElementById("bd-canvas");
      if (!canvas) return false;
      var mode = this._flowMode();
      canvas.classList.toggle("bd-mobile-flow", mode === "mobile");
      canvas.classList.toggle("bd-tablet-flow", mode === "tablet");
      var wrap = document.querySelector(".bd-canvas-wrap");
      if (mode !== "none" && wrap) { wrap.scrollTop = 0; wrap.scrollLeft = 0; }
      return mode !== "none";
    },

    /* ── P3.12 — automatic bookmark/page carousel (present-mode kiosk). When
       playing, applies each bookmark of the current page in turn (5 s dwell),
       then advances to the next page; pages without bookmarks just advance.
       Space/P play-pauses; any manual navigation key stops it. */
    _toggleCarousel: function () {
      this._carouselOn() ? this._carouselStop() : this._carouselStart();
    },
    _carouselOn: function () { return !!_carousel.on; },
    _carouselSec: function () {
      var s = parseFloat(_dash.carouselSec);
      return (s > 0 && s <= 60) ? s : 5;
    },
    _carouselStart: function () {
      if (_dash.mode !== "present") { App.info("Enter presentation mode to auto-play"); return; }
      _carousel.on = true;
      _carousel.sec = this._carouselSec();
      _carousel.bIdx = -1;
      this._renderPresentNavLive();
      this._carouselTick();
      this._carouselCaption("Auto-play started — bookmarks, then pages");
    },
    _carouselStop: function (silent) {
      if (!_carousel.on && !_carousel.timer) { if (!silent) this._renderPresentNavLive(); return; }
      _carousel.on = false;
      if (_carousel.timer) { clearTimeout(_carousel.timer); _carousel.timer = null; }
      var cap = document.getElementById("bd-carousel-caption");
      if (cap) cap.remove();
      this._renderPresentNavLive();
      if (!silent) App.info("Auto-play stopped");
    },
    _carouselTick: function () {
      if (!_carousel.on) return;
      var self = this;
      _carousel.sec = this._carouselSec();
      var pageId = _dash.pages[_dash.currentPage] ? _dash.pages[_dash.currentPage].id : null;
      var cur = this._listBookmarks().filter(function (b) { return b.pageId === pageId; });
      if (!cur.length) {
        /* No bookmarks on this page → just advance the page */
        var next = _dash.currentPage + 1;
        if (next >= _dash.pages.length) next = 0;
        _carousel.bIdx = -1;
        this._sendPageStep(next, true);
      } else {
        _carousel.bIdx++;
        if (_carousel.bIdx >= cur.length) {
          var nx = _dash.currentPage + 1;
          if (nx >= _dash.pages.length) nx = 0;
          _carousel.bIdx = -1;
          this._sendPageStep(nx, true);
        } else {
          var bm = cur[_carousel.bIdx];
          this._applyBookmark(bm.id, true);
          this._carouselCaption('Bookmark ' + (_carousel.bIdx + 1) + ' of ' + cur.length + ' — “' + bm.name + '”');
        }
      }
      var self2 = this;
      _carousel.timer = setTimeout(function () { self2._carouselTick(); }, _carousel.sec * 1000);
    },
    _sendPageStep: function (idx, silent) {
      var t = _dash.pages[idx];
      if (!t) return;
      if (silent) {
        this._carouselCaption('Page ' + (idx + 1) + ' of ' + _dash.pages.length + ' — “' + (t.name || ("Page " + (idx + 1))) + '”');
        var snap = _pageFilterSnapshot();
        _storePageFilters(_dash.currentPage, snap);
        _restorePageFilters(idx);
        _dash.currentPage = idx;
        this._syncPageHash();
        this.render();
      } else {
        this._switchPage(idx);
      }
    },
    _carouselCaption: function (text) {
      var cap = document.getElementById("bd-carousel-caption");
      if (!cap && text) {
        cap = document.createElement("div");
        cap.id = "bd-carousel-caption";
        cap.className = "bd-carousel-caption";
        document.body.appendChild(cap);
      }
      if (cap) cap.innerHTML = '<span class="pulse"></span> ' + _esc(text);
    },
    _renderPresentNavLive: function () {
      var nav = document.getElementById("bd-present-nav");
      if (!nav) return;
      var wrap = document.createElement("div");
      wrap.innerHTML = _renderPresentNav();
      var fresh = wrap.firstElementChild;
      if (fresh && nav.parentNode) nav.parentNode.replaceChild(fresh, nav);
    },

    _toggleGrid: function () {
      _showGrid = !_showGrid;
      var canvas = document.getElementById("bd-canvas");
      if (canvas) canvas.classList.toggle("fff-grid-off", !_showGrid);
      var btn = document.getElementById("bd-grid-toggle");
      if (btn) btn.classList.toggle("active", !_showGrid);
    },

    /* A2 — grid mode toggle: cycles dots → columns → off. The mode is persisted
       on the dashboard (gridMode) so presenters keep the same visual grid. */
    _toggleGridMode: function () {
      var order = ["dots", "cols", "off"];
      var cur = _dash.gridMode === "cols" ? "cols" : (_dash.gridMode === "off" ? "off" : "dots");
      var next = order[(order.indexOf(cur) + 1) % order.length];
      _dash.gridMode = next;
      var canvas = document.getElementById("bd-canvas");
      if (canvas) {
        canvas.classList.toggle("fff-grid-off", next === "off");
        canvas.classList.toggle("bd-grid-cols", next === "cols");
      }
      var btn = document.getElementById("bd-grid-mode-toggle");
      if (btn) {
        btn.title = "Grid mode: " + next + " — click to cycle (dots / columns / off)";
        btn.innerHTML = next === "cols"
          ? '<i class="fas fa-columns"></i>'
          : (next === "off" ? '<i class="fas fa-border-none"></i>' : '<i class="fas fa-border-all"></i>');
      }
      this._markDirty();
    },

    /* Rulers overlaid on the canvas wrap; ticks/end labels are drawn in view
       pixels so they stay fixed while the (scaled) canvas scrolls underneath. */
    _renderRulers: function () {
      var wrap = document.querySelector(".bd-canvas-wrap");
      var canvas = document.getElementById("bd-canvas");
      if (!wrap || !canvas) return;
      if (!document.getElementById("bd-ruler-y")) {
        wrap.insertAdjacentHTML("afterbegin",
          '<div class="bd-ruler bd-ruler-y" id="bd-ruler-y"></div>' +
          '<div class="bd-ruler-corner" id="bd-ruler-corner"></div>' +
          '<div class="bd-ruler bd-ruler-x" id="bd-ruler-x"></div>');
      }
      var rx = document.getElementById("bd-ruler-x");
      var ry = document.getElementById("bd-ruler-y");
      if (!rx || !ry) return;
      rx.innerHTML = ""; ry.innerHTML = "";
      var wr = wrap.getBoundingClientRect();
      var cr = canvas.getBoundingClientRect();
      var ox = cr.left - wr.left, oy = cr.top - wr.top;
      var sl = wrap.scrollLeft, st = wrap.scrollTop;
      var vw = wrap.clientWidth, vh = wrap.clientHeight;
      var cw = parseFloat(canvas.style.width) || 1600;
      var ch = parseFloat(canvas.style.height) || 1200;
      var fx = document.createDocumentFragment(), fy = document.createDocumentFragment();
      for (var x = 0; x <= cw; x += 50) {
        var px = ox + x * _zoom - sl;
        if (px < -40 || px > vw) continue;
        var tk = document.createElement("div");
        tk.className = "bd-ruler-tick x" + (x % 100 === 0 ? " major" : "");
        tk.style.left = px + "px";
        fx.appendChild(tk);
        if (x % 100 === 0) {
          var lbl = document.createElement("div");
          lbl.className = "bd-ruler-label x";
          lbl.textContent = x;
          lbl.style.left = (px + 3) + "px";
          fx.appendChild(lbl);
        }
      }
      for (var y = 0; y <= ch; y += 50) {
        var py = oy + y * _zoom - st;
        if (py < -40 || py > vh) continue;
        var tk2 = document.createElement("div");
        tk2.className = "bd-ruler-tick y" + (y % 100 === 0 ? " major" : "");
        tk2.style.top = py + "px";
        fy.appendChild(tk2);
        if (y % 100 === 0) {
          var lbl2 = document.createElement("div");
          lbl2.className = "bd-ruler-label y";
          lbl2.textContent = y;
          lbl2.style.top = (py + 3) + "px";
          fy.appendChild(lbl2);
        }
      }
      rx.appendChild(fx);
      ry.appendChild(fy);
    },

    _attachRulerScroll: function () {
      var wrap = document.querySelector(".bd-canvas-wrap");
      if (!wrap || wrap.__bdRulerBound) return;
      wrap.__bdRulerBound = true;
      var self = this;
      var pending = false;
      wrap.addEventListener("scroll", function () {
        if (pending) return;
        pending = true;
        requestAnimationFrame(function () { pending = false; self._renderRulers(); });
      });
    },

    /* Fit the whole canvas width into the visible viewport */
    _fitZoom: function () {
      var wrap = document.querySelector(".bd-canvas-wrap");
      var canvas = document.getElementById("bd-canvas");
      if (!wrap || !canvas) return;
      var cw = parseFloat(canvas.style.width) || _baseCanvasW();
      var ch = parseFloat(canvas.style.height) || _baseCanvasH();
      if (cw <= 0 || ch <= 0) return;
      var availW = wrap.clientWidth - 32;
      var availH = wrap.clientHeight - 32;
      var z = _clamp(Math.min(availW / cw, availH / ch), 0.4, 2.5);
      this._applyZoom(z);
      wrap.scrollLeft = 0; wrap.scrollTop = 0;
    },

    _undo: function () {
      if (_undoStack.length === 0) return;
      var cur = _dash.pages[_dash.currentPage];
      _redoStack.push({ pageId: cur.id, widgets: JSON.parse(JSON.stringify(_currentWidgets())), groups: JSON.parse(JSON.stringify(_dash.groups || {})) });
      var guard = 0;
      while (_undoStack.length > 0 && guard++ < 100) {
        var snap = _undoStack.pop();
        if (this._applySnap(snap)) return;
      }
    },

    _redo: function () {
      if (_redoStack.length === 0) return;
      var cur = _dash.pages[_dash.currentPage];
      _undoStack.push({ pageId: cur.id, widgets: JSON.parse(JSON.stringify(_currentWidgets())), groups: JSON.parse(JSON.stringify(_dash.groups || {})) });
      var guard = 0;
      while (_redoStack.length > 0 && guard++ < 100) {
        var snap = _redoStack.pop();
        if (this._applySnap(snap)) return;
      }
    },

    /* Restore a {pageId, widgets} snapshot into its own page. If the target
       page no longer exists the snapshot is dead and false is returned so the
       caller can skip it. Legacy bare-array snapshots apply to the current page. */
    _applySnap: function (snap) {
      if (snap == null) return false;
      if (Array.isArray(snap)) snap = { pageId: _dash.pages[_dash.currentPage].id, widgets: snap };
      if (snap == null || snap.widgets == null) return false;
      var idx = -1;
      for (var p = 0; p < _dash.pages.length; p++) {
        if (_dash.pages[p].id === snap.pageId) { idx = p; break; }
      }
      if (idx === -1) return false;
      if (_dash.currentPage !== idx) {
        _dash.currentPage = idx;
        this.render();
      }
      if (snap.groups) _dash.groups = JSON.parse(JSON.stringify(snap.groups));
      this._setWidgets(snap.widgets, snap.pageId);
      return true;
    },

    _setWidgets: function (list, pageId) {
      var page = null;
      if (pageId != null) {
        for (var p = 0; p < _dash.pages.length; p++) {
          if (_dash.pages[p].id === pageId) { page = _dash.pages[p]; break; }
        }
      }
      if (!page) page = _dash.pages[_dash.currentPage];
      page.widgets = list;
      _lastSnap = { pageId: page.id, widgets: JSON.parse(JSON.stringify(page.widgets)), groups: JSON.parse(JSON.stringify(_dash.groups || {})) };
      this._initGrid();
      this._onCanvasChanged();
      if (this._propsWidget) this._openProps();
    },

    _onCanvasChanged: function () {
      var zb = document.querySelector('[onclick="BlankDash._undo()"]');
      var rb = document.querySelector('[onclick="BlankDash._redo()"]');
      if (zb) zb.disabled = _undoStack.length === 0;
      if (rb) rb.disabled = _redoStack.length === 0;
    },

    _onEditKey: function (e) {
      if (_dash.mode === "present") return;
      if (!document.getElementById("bd-canvas")) return;
      var ae = document.activeElement;
      var typing = ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.tagName === "SELECT" || ae.isContentEditable);
      var mod = e.ctrlKey || e.metaKey;
      var k = e.key;
      var lk = k.toLowerCase();
      if (typing) {
        if (mod && lk === "z") {
          e.preventDefault();
          if (e.shiftKey) BlankDash._redo();
          else BlankDash._undo();
        } else if (mod && lk === "s") {
          e.preventDefault();
          BlankDash._save();
        }
        return;
      }
      if (mod) {
        if (lk === "z") {
          e.preventDefault();
          if (e.shiftKey) BlankDash._redo();
          else BlankDash._undo();
        } else if (lk === "y") {
          e.preventDefault();
          BlankDash._redo();
        } else if (lk === "c" || lk === "x") {
          if (e.shiftKey && lk === "c") { e.preventDefault(); BlankDash._copyStyle(); }
          else { if (lk === "x") e.preventDefault(); BlankDash._copySelection(); }
        } else if (lk === "v") {
          e.preventDefault();
          if (e.shiftKey) BlankDash._pasteStyle();
          else BlankDash._pasteClipboard();
        } else if (lk === "a") {
          e.preventDefault();
          BlankDash._selectAll();
        } else if (lk === "d") {
          e.preventDefault();
          BlankDash._duplicateSelection();
        } else if (lk === "g") {
          e.preventDefault();
          if (e.shiftKey) BlankDash._ungroupSelection();
          else BlankDash._groupSelection();
        } else if (lk === "s") {
          e.preventDefault();
          BlankDash._save();
        }
        return;
      }
      if (k === "ArrowLeft" || k === "ArrowRight" || k === "ArrowUp" || k === "ArrowDown") {
        if (_selectedIds.length === 0) return;
        e.preventDefault();
        var step = e.shiftKey ? 10 : 1;
        var ddx = k === "ArrowLeft" ? -step : k === "ArrowRight" ? step : 0;
        var ddy = k === "ArrowUp" ? -step : k === "ArrowDown" ? step : 0;
        var sel = BlankDash._selectedUnlocked();
        if (sel.length === 0) return;
        sel.forEach(function (w) {
          w.x = Math.max(0, w.x + ddx);
          w.y = Math.max(0, w.y + ddy);
        });
        sel.forEach(function (w) { BlankDash._positionWidget(w); });
        BlankDash._growCanvas();
        if (!_nudgeNeedsBaseline) {
          _nudgeNeedsBaseline = true;
          document.addEventListener("keyup", function endNudge(ev) {
            document.removeEventListener("keyup", endNudge);
            _nudgeNeedsBaseline = false;
            BlankDash._refreshHistory();
            BlankDash._refreshProps();
          });
        }
        return;
      }
      if (k === "Delete" || k === "Backspace") {
        if (_selectedIds.length === 0) return;
        e.preventDefault();
        BlankDash._deleteSelection();
        return;
      }
      if (lk === "m") {
        e.preventDefault();
        BlankDash._toggleMinimap();
        return;
      }
      if (k === "Escape") {
        if (document.fullscreenElement) {
          e.preventDefault();
          try { document.exitFullscreen(); } catch (err) {}
          return;
        }
        if (_selectedIds.length) {
          BlankDash._deselect();
          e.preventDefault();
        }
      }
    },


    _onPresentKey: function (e) {
      if (_dash.mode !== "present") return;
      if (e.key === " " || e.key === "Spacebar" || e.key === "p" || e.key === "P") {
        e.preventDefault();
        BlankDash._toggleCarousel();
        return;
      }
      if (e.key === "ArrowRight" || e.key === "PageDown") {
        e.preventDefault();
        BlankDash._carouselStop(true);
        if (_dash.currentPage < _dash.pages.length - 1) BlankDash._switchPage(_dash.currentPage + 1);
      } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault();
        BlankDash._carouselStop(true);
        if (_dash.currentPage > 0) BlankDash._switchPage(_dash.currentPage - 1);
      } else if (e.key === "Escape") {
        e.preventDefault();
        BlankDash._carouselStop(true);
        BlankDash._exitPresent();
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        BlankDash._toggleFullscreen();
      } else if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        BlankDash._togglePresenterView();
      } else if (e.key === "a" || e.key === "A") {
        e.preventDefault();
        BlankDash._toggleAnnotations();
      } else if (e.key === "Home") {
        e.preventDefault();
        BlankDash._carouselStop(true);
        if (_dash.currentPage !== 0) BlankDash._switchPage(0);
      } else if (e.key === "End") {
        e.preventDefault();
        BlankDash._carouselStop(true);
        BlankDash._switchPage(_dash.pages.length - 1);
      } else if (/^[1-9]$/.test(e.key)) {
        e.preventDefault();
        BlankDash._carouselStop(true);
        BlankDash._switchPage(Math.min(parseInt(e.key, 10) - 1, _dash.pages.length - 1));
      }
      BlankDash._revealPresentIdle();
    },

    _pageStep: function (dir) {
      var t = _dash.currentPage + dir;
      if (t >= 0 && t < _dash.pages.length) this._switchPage(t);
    },
    _bindPresentIdle: function () {
      var self = this;
      if (window.__bdPresentIdleBound) return;
      window.__bdPresentIdleBound = true;
      var idleT = null;
      window.__bdPresentIdleReveal = function () {
        if (!document.querySelector(".bd-page.bd-present")) return;
        document.querySelector(".bd-page.bd-present").classList.remove("bd-present-idle");
      };
      window.__bdPresentIdleHide = function () {
        if (!document.querySelector(".bd-page.bd-present")) return;
        document.querySelector(".bd-page.bd-present").classList.add("bd-present-idle");
      };
      var scheduleHide = function () {
        clearTimeout(idleT);
        idleT = setTimeout(window.__bdPresentIdleHide, 2600);
      };
      document.addEventListener("mousemove", function () { window.__bdPresentIdleReveal(); scheduleHide(); });
      document.addEventListener("mousedown", function () { window.__bdPresentIdleReveal(); scheduleHide(); });
      document.addEventListener("keydown", function () { window.__bdPresentIdleReveal(); });
      scheduleHide();
    },
    _revealPresentIdle: function () {
      if (window.__bdPresentIdleReveal) window.__bdPresentIdleReveal();
    },

    /* ── Settings & auto-refresh ── */
    _showShortcuts: function () {
      function row(label, keys) {
        return '<div class="bd-sc-row"><span class="bd-sc-label">' + label + '</span><span><kbd>' + keys + '</kbd></span></div>';
      }
      var html = '<div class="modal-overlay" onclick="if(event.target===this)this.remove()">' +
        '<div class="modal-content" style="max-width:520px;">' +
          '<h3 style="margin:0 0 14px;font-size:18px;font-weight:700;"><i class="fas fa-keyboard"></i> Keyboard shortcuts</h3>' +
          '<div style="font-size:11px;font-weight:700;color:var(--color-primary);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Editing</div>' +
          '<div class="bd-shortcuts-grid">' +
            row("Move selection (1px)", "← ↑ ↓ →") +
            row("Move selection (10px)", "Shift + arrows") +
            row("Select all on page", "Ctrl/⌘ + A") +
            row("Copy / Paste", "Ctrl/⌘ + C · V") +
            row("Duplicate selection", "Ctrl/⌘ + D") +
            row("Save dashboard", "Ctrl/⌘ + S") +
            row("Undo / Redo", "Ctrl/⌘ + Z · Ctrl + Shift + Z") +
            row("Delete selection", "Delete / Backspace") +
            row("Toggle navigator minimap", "M") +
            row("Deselect / close menu", "Esc") +
          '</div>' +
          '<div style="font-size:11px;font-weight:700;color:var(--color-primary);text-transform:uppercase;letter-spacing:0.06em;margin:14px 0 4px;">Presentation</div>' +
          '<div class="bd-shortcuts-grid">' +
            row("Previous / Next page", "← / →") +
            row("First / Last page", "Home / End") +
            row("Jump to page", "1 – 9") +
            row("Toggle fullscreen", "F") +
            row("Exit presentation", "Esc") +
          '</div>' +
          '<div style="margin-top:14px;"><button class="btn btn-sm btn-ghost" onclick="this.closest(\'.modal-overlay\').remove()">Close</button></div>' +
        '</div>' +
      '</div>';
      document.body.insertAdjacentHTML("beforeend", html);
    },

    _settingsTab: function (btn) {
      if (!btn) return;
      var tabs = btn.parentNode.querySelectorAll(".bd-settings-tab");
      for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove("active");
      btn.classList.add("active");
      var paneId = btn.getAttribute("data-pane");
      var root = btn.closest(".bd-settings-body");
      if (!root) return;
      var panes = root.querySelectorAll(".bd-settings-pane");
      for (var j = 0; j < panes.length; j++) panes[j].classList.toggle("active", panes[j].id === paneId);
    },
    _settingsCopyLink: function (btn) {
      var self = this;
      var done = function (url) {
        if (btn) { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy link"; }, 1500); }
        if (window.App && App.success) App.success("Share link copied");
      };
      var copy = function (url) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url).then(function () { done(url); }).catch(function () { prompt("Copy this share link:", url); done(url); });
        } else {
          prompt("Copy this share link:", url); done(url);
        }
      };
      // Ensure the dashboard is saved server-side first.
      var afterSave = function (id) {
        if (!id) { App.warning("Save the dashboard before sharing"); return; }
        API.shareDashboard(String(id).replace(/^blankdash_/, ""), { shared_by: localStorage.getItem("username") || "anonymous" })
          .then(function (res) {
            var token = res && res.token;
            if (!token) throw new Error("no token");
            _dash.sharePublic = true;
            var url = location.origin + location.pathname + "#blankdash/shared/" + encodeURIComponent(token);
            copy(url);
          })
          .catch(function (e) {
            console.warn("Share link failed:", e);
            App.warning("Could not create share link (is the server reachable?)");
          });
      };
      if (_dash.id) {
        // persist latest state, then share
        var res2 = API.updateDashboard(String(_dash.id).replace(/^blankdash_/, ""), this._buildServerPayload());
        res2.then(function () { afterSave(_dash.id); }).catch(function () { afterSave(_dash.id); });
      } else {
        API.createDashboard(this._buildServerPayload()).then(function (r) {
          var id = (r && r.dashboard && r.dashboard.id) || _dash.id;
          _dash.id = id;
          try { localStorage.setItem("blankdash_" + id, JSON.stringify(_dashConfigSnapshot())); } catch (e) {}
          afterSave(id);
        }).catch(function () { App.warning("Could not save dashboard before sharing"); });
      }
    },

    /* Re-render the "Shining gradient" treatment on every widget from saved _dash state */
    _applyShineToAll: function () {
      document.querySelectorAll(".ff-widget").forEach(function (el) {
        var id = el.getAttribute && el.getAttribute("data-id");
        var w = id ? _findWidget(id) : null;
        if (w) _applyCardStyle(w, el);
      });
    },
    /* Live preview of the Settings → Appearance shine controls (visual only, not saved) */
    _previewShine: function () {
      var on = !!(document.getElementById("bd-set-shine") || {}).checked;
      var c1El = document.getElementById("bd-set-shine-c1");
      var c2El = document.getElementById("bd-set-shine-c2");
      var c1 = c1El && _hexOk(c1El.value) ? c1El.value : "#2563eb";
      var c2 = c2El && _hexOk(c2El.value) ? c2El.value : "#7c3aed";
      document.querySelectorAll(".ff-widget").forEach(function (el) {
        var id = el.getAttribute && el.getAttribute("data-id");
        var w = id ? _findWidget(id) : null;
        if (on && !_explicitlyStyled(w)) {
          el.style.backgroundImage = "linear-gradient(135deg," + c1 + "," + c2 + ")";
          el.classList.add("bd-shine");
        } else {
          if ((el.style.backgroundImage || "").indexOf("linear-gradient") !== -1) el.style.backgroundImage = "";
          el.classList.remove("bd-shine");
        }
      });
    },

    _showSettings: function () {
      var self = this;
      var refreshOpts = [0, 10, 30, 60, 300].map(function (s) {
        var label = s === 0 ? "Off — manual" : s + " sec";
        var sel = (_dash.refreshSec || 0) === s ? " selected" : "";
        return '<option value="' + s + '"' + sel + '>' + label + '</option>';
      }).join("");
      var minW = _dash.minW != null ? _dash.minW : 120;
      var minH = _dash.minH != null ? _dash.minH : 80;
      var maxW = _dash.maxW != null ? _dash.maxW : 2000;
      var maxH = _dash.maxH != null ? _dash.maxH : 2000;
      var snapOn = _dash.snap !== false;
      var shareOn = !!_dash.sharePublic;
      var dl = _dash.table ? ("<span class='bd-lbl-strong'>" + _esc(_friendlyName(_dash.table)) + "</span><code class='bd-lbl-code'>" + _esc(_dash.table) + "</code>") : "<span style='color:var(--color-content-text-secondary)'>No data source selected — add one from the toolbar.</span>";
      var shineOn = _shineOn();
      var shineC = _shineGradientColors();
      var html = '<div class="modal-overlay" onclick="if(event.target===this){BlankDash._applyShineToAll();this.remove()}">' +
        '<div class="modal-content" style="max-width:600px;">' +
          '<h3 style="margin:0 0 14px;font-size:18px;font-weight:700;">Dashboard Settings</h3>' +
          '<div class="bd-settings-body">' +
            '<div class="bd-settings-tabs">' +
              '<button type="button" class="bd-settings-tab active" data-pane="bd-pane-general"><i class="fas fa-sliders"></i> General</button>' +
              '<button type="button" class="bd-settings-tab" data-pane="bd-pane-data"><i class="fas fa-database"></i> Data</button>' +
              '<button type="button" class="bd-settings-tab" data-pane="bd-pane-appearance"><i class="fas fa-palette"></i> Appearance</button>' +
              '<button type="button" class="bd-settings-tab" data-pane="bd-pane-sharing"><i class="fas fa-share-nodes"></i> Sharing &amp; Access</button>' +
              '<button type="button" class="bd-settings-tab" data-pane="bd-pane-export" data-feature="dashboard_export"><i class="fas fa-file-export"></i> Export</button>' +
            '</div>' +
            '<div class="bd-settings-panes">' +
              '<div class="bd-settings-pane active" id="bd-pane-general">' +
                '<label class="form-label" style="font-size:13px;font-weight:600;margin-bottom:6px;display:block;">Dashboard name</label>' +
                '<input type="text" class="form-input" id="bd-set-name" style="width:100%;" value="' + _esc(_dash.name || "") + '" placeholder="Untitled Dashboard">' +
                '<div style="margin:16px 0 12px;display:flex;align-items:center;gap:8px;">' +
                  '<input type="checkbox" id="bd-set-snap" ' + (snapOn ? "checked" : "") + '>' +
                  '<label for="bd-set-snap" class="form-label" style="font-size:13px;font-weight:600;margin:0;cursor:pointer;">Snap widgets to grid</label>' +
                '</div>' +
                '<div class="bd-settings-divider"></div>' +
                '<label class="form-label" style="font-size:12px;color:var(--color-content-text-secondary);margin-bottom:6px;display:block;">Minimum widget size (px)</label>' +
                '<div style="display:flex;gap:8px;">' +
                  '<input type="number" min="40" class="form-input" id="bd-set-minw" value="' + minW + '" placeholder="Width" style="width:50%;">' +
                  '<input type="number" min="40" class="form-input" id="bd-set-minh" value="' + minH + '" placeholder="Height" style="width:50%;">' +
                '</div>' +
                '<label class="form-label" style="font-size:12px;color:var(--color-content-text-secondary);margin:10px 0 6px;display:block;">Maximum widget size (px)</label>' +
                '<div style="display:flex;gap:8px;">' +
                  '<input type="number" min="120" class="form-input" id="bd-set-maxw" value="' + maxW + '" placeholder="Width" style="width:50%;">' +
                  '<input type="number" min="120" class="form-input" id="bd-set-maxh" value="' + maxH + '" placeholder="Height" style="width:50%;">' +
                '</div>' +
                '<div class="bd-settings-divider"></div>' +
                '<label class="form-label" style="font-size:12px;color:var(--color-content-text-secondary);margin-bottom:6px;display:block;">Canvas / presentation size (px)</label>' +
                '<div style="display:flex;gap:8px;">' +
                  '<input type="number" min="320" class="form-input" id="bd-set-cw" value="' + (_dash.canvasMinW || _CANVAS_MIN_W) + '" placeholder="Width" style="width:50%;">' +
                  '<input type="number" min="240" class="form-input" id="bd-set-ch" value="' + (_dash.canvasMinH || _CANVAS_MIN_H) + '" placeholder="Height" style="width:50%;">' +
                '</div>' +
                '<div style="margin:10px 0 0;display:flex;align-items:center;gap:8px;">' +
                  '<input type="checkbox" id="bd-set-fixed" ' + (_dash.fixedCanvas ? "checked" : "") + '>' +
                  '<label for="bd-set-fixed" class="form-label" style="font-size:13px;font-weight:600;margin:0;cursor:pointer;">Fixed canvas size</label>' +
                '</div>' +
                '<p style="font-size:12px;color:var(--color-content-text-secondary);margin:4px 0 0;">Fixed: the canvas stays at exactly this size — widgets placed past the edge are clipped. Turn it off for a canvas that auto-grows as you place widgets.</p>' +
                '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">' +
                  '<button type="button" class="btn btn-sm btn-ghost" onclick="var a=document.getElementById(\'bd-set-cw\'),b=document.getElementById(\'bd-set-ch\'),f=document.getElementById(\'bd-set-fixed\');a.value=1900;b.value=1080;if(f)f.checked=true;">Desktop 1900×1080</button>' +
                  '<button type="button" class="btn btn-sm btn-ghost" onclick="var a=document.getElementById(\'bd-set-cw\'),b=document.getElementById(\'bd-set-ch\'),f=document.getElementById(\'bd-set-fixed\');a.value=1366;b.value=768;if(f)f.checked=true;">Laptop 1366×768</button>' +
                  '<button type="button" class="btn btn-sm btn-ghost" onclick="var a=document.getElementById(\'bd-set-cw\'),b=document.getElementById(\'bd-set-ch\'),f=document.getElementById(\'bd-set-fixed\');a.value=1024;b.value=768;if(f)f.checked=true;">Tablet 1024×768</button>' +
                  '<button type="button" class="btn btn-sm btn-ghost" onclick="var a=document.getElementById(\'bd-set-cw\'),b=document.getElementById(\'bd-set-ch\'),f=document.getElementById(\'bd-set-fixed\');a.value=390;b.value=844;if(f)f.checked=true;">Mobile 390×844</button>' +
                '</div>' +
                '<label class="form-label" style="font-size:12px;color:var(--color-content-text-secondary);margin:10px 0 6px;display:block;">Snap grid size (px)</label>' +
                '<input type="number" min="4" max="96" class="form-input" id="bd-set-grid" value="' + (_dash.grid || 12) + '" placeholder="Grid" style="width:50%;">' +
                '<p style="font-size:12px;color:var(--color-content-text-secondary);margin:6px 0 0;">The canvas grows automatically as you place widgets; this sets its starting size and the snap step.</p>' +
                '<div class="bd-settings-divider"></div>' +
                '<div style="display:flex;align-items:center;gap:10px;">' +
                  '<span class="form-label" style="font-size:13px;font-weight:600;margin:0;">Revert to last save</span>' +
                  '<span style="font-size:12px;color:var(--color-content-text-secondary);">' + (_dirty ? "You have unsaved changes." : "You\u2019re up to date.") + '</span>' +
                  '<button class="btn btn-sm btn-ghost" style="margin-left:auto;" onclick="this.closest(\'.modal-overlay\').remove();BlankDash._revertToSaved()"><i class="fas fa-rotate-left"></i> Revert</button>' +
                '</div>' +
              '</div>' +
              '<div class="bd-settings-pane" id="bd-pane-data">' +
                '<label class="form-label" style="font-size:13px;font-weight:600;margin-bottom:6px;display:block;">Auto-refresh interval</label>' +
                '<select class="form-input" id="bd-set-refresh" style="width:100%;">' + refreshOpts + '</select>' +
                '<p style="font-size:12px;color:var(--color-content-text-secondary);margin:6px 0 14px;">Re-query all widgets automatically at the chosen interval.</p>' +
                '<div class="bd-settings-divider"></div>' +
                '<label class="form-label" style="font-size:13px;font-weight:600;margin-bottom:6px;display:block;">Data source</label>' +
                '<div style="display:flex;align-items:center;gap:8px;justify-content:space-between;">' +
                  '<div style="min-width:0;">' + dl + '</div>' +
                  '<button class="btn btn-sm btn-ghost" onclick="this.closest(\'.modal-overlay\').remove();var s=document.getElementById(\'bd-table-select\');if(s){s.focus();s.scrollIntoView({block:\'center\'})}"><i class="fas fa-table-columns"></i> Change in toolbar</button>' +
                '</div>' +
              '</div>' +
              '<div class="bd-settings-pane" id="bd-pane-appearance">' +
                '<div style="display:flex;align-items:center;gap:10px;">' +
                  '<label class="form-label" style="font-size:13px;font-weight:600;margin:0;">Dashboard theme</label>' +
                  '<span style="font-size:12px;color:var(--color-content-text-secondary);">' + _esc(_themeLabel(_dash.theme || "auto")) + '</span>' +
                  '<button class="btn btn-sm btn-secondary" style="margin-left:auto;" onclick="BlankDash._openThemePicker()"><i class="fas fa-palette"></i> Theme editor</button>' +
                '</div>' +
                '<p style="font-size:12px;color:var(--color-content-text-secondary);margin:12px 0 0;">The dashboard theme controls the colour scheme applied to the canvas and widgets. Chart data colours are independent of the UI accent.</p>' +
                '<div class="bd-settings-divider"></div>' +
                '<div style="display:flex;align-items:center;gap:10px;">' +
                  '<label class="form-label" style="font-size:13px;font-weight:600;margin:0;">Shining gradient</label>' +
                  '<span style="font-size:12px;color:var(--color-content-text-secondary);">apply a glossy gradient to every widget</span>' +
                  '<input type="checkbox" id="bd-set-shine" ' + (shineOn ? "checked" : "") + ' onchange="BlankDash._previewShine()" style="margin-left:auto;">' +
                '</div>' +
                '<div style="display:flex;gap:12px;align-items:center;margin-top:8px;flex-wrap:wrap;">' +
                  '<label style="font-size:11px;color:var(--color-content-text-secondary);display:flex;align-items:center;gap:6px;">Start <input type="color" class="form-input bdp-color" id="bd-set-shine-c1" value="' + shineC[0] + '" onchange="BlankDash._previewShine()"></label>' +
                  '<label style="font-size:11px;color:var(--color-content-text-secondary);display:flex;align-items:center;gap:6px;">End <input type="color" class="form-input bdp-color" id="bd-set-shine-c2" value="' + shineC[1] + '" onchange="BlankDash._previewShine()"></label>' +
                  '<span style="font-size:11px;color:var(--color-content-text-secondary);">Defaults to your theme accent. Widgets with their own background colour are left untouched.</span>' +
                '</div>' +
              '</div>' +
              '<div class="bd-settings-pane" id="bd-pane-sharing">' +
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">' +
                  '<input type="checkbox" id="bd-set-share" ' + (shareOn ? "checked" : "") + '>' +
                  '<label for="bd-set-share" class="form-label" style="font-size:13px;font-weight:600;margin:0;cursor:pointer;">Allow others to open this dashboard</label>' +
                '</div>' +
                '<label class="form-label" style="font-size:12px;color:var(--color-content-text-secondary);margin-bottom:6px;display:block;">Dashboard ID</label>' +
                '<div style="display:flex;gap:8px;align-items:center;">' +
                  '<input type="text" readonly class="form-input" style="flex:1;font-family:monospace;font-size:12px;" value="' + _esc(_dash.id || "blankdash_" + Date.now()) + '">' +
                  '<button class="btn btn-sm btn-secondary" onclick="BlankDash._settingsCopyLink(this)"><i class="fas fa-link"></i> Copy link</button>' +
                '</div>' +
                '<p style="font-size:12px;color:var(--color-content-text-secondary);margin:10px 0 0;">Dashboards are stored locally. Share the link to let others review the same view.</p>' +
              '</div>' +
              '<div class="bd-settings-pane" id="bd-pane-export">' +
                '<p style="font-size:12px;color:var(--color-content-text-secondary);margin:0 0 12px;">Export this dashboard for sharing or archiving.</p>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">' +
                  '<button class="btn btn-sm btn-secondary export-fmt-btn" data-feature="dashboard_export" onclick="BlankDash._exportDash(\'pptx\')"><i class="fas fa-file-powerpoint"></i> PowerPoint (.pptx)</button>' +
                  '<button class="btn btn-sm btn-secondary export-fmt-btn" data-feature="dashboard_export" onclick="BlankDash._exportDash(\'xlsx\')"><i class="fas fa-file-excel"></i> Excel (.xlsx)</button>' +
                  '<button class="btn btn-sm btn-secondary export-fmt-btn" data-feature="dashboard_export" onclick="BlankDash._exportDash(\'png\')"><i class="fas fa-image"></i> Screenshot (PNG)</button>' +
                  '<button class="btn btn-sm btn-secondary export-fmt-btn" data-feature="dashboard_export" onclick="BlankDash._exportDash(\'print\')"><i class="fas fa-print"></i> Print / PDF</button>' +
                  '<button class="btn btn-sm btn-secondary export-fmt-btn" data-feature="dashboard_export" onclick="BlankDash._exportDash(\'json\')"><i class="fas fa-file-code"></i> JSON file</button>' +
                  '<button class="btn btn-sm btn-secondary export-fmt-btn" data-feature="dashboard_export" onclick="BlankDash._exportDash(\'copy\')"><i class="fas fa-clipboard"></i> Copy config (JSON)</button>' +
                '</div>' +
              '</div>' +
            '</div>' +
          '</div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px;">' +
            '<button class="btn btn-secondary btn-sm modal-close" onclick="BlankDash._applyShineToAll();this.closest(\'.modal-overlay\').remove()">Cancel</button>' +
            '<button class="btn btn-primary btn-sm" id="bd-set-save">Save</button>' +
          '</div>' +
        '</div>' +
      '</div>';
      document.body.insertAdjacentHTML("beforeend", html);
      // Gate premium features immediately so inline onclick is replaced
      // before the tab-switch listeners are registered on the same elements.
      if (window.RBAC && window.RBAC.applyToNav) {
        try { window.RBAC.applyToNav(); } catch (e) { console.warn("Failed to apply RBAC gating:", e); }
      }
      document.querySelectorAll(".bd-settings-tab").forEach(function (t) {
        t.addEventListener("click", function () { self._settingsTab(t); });
      });
      document.getElementById("bd-set-save").addEventListener("click", function () {
        _dash.name = _titleCaseName((document.getElementById("bd-set-name").value || "").trim()) || "Untitled Dashboard";
        var nameEl = document.getElementById("bd-name");
        if (nameEl) nameEl.value = _dash.name;
        _dash.refreshSec = parseInt(document.getElementById("bd-set-refresh").value, 10) || 0;
        _dash.snap = document.getElementById("bd-set-snap").checked;
        _dash.minW = Math.max(40, parseInt(document.getElementById("bd-set-minw").value, 10) || 120);
        _dash.minH = Math.max(40, parseInt(document.getElementById("bd-set-minh").value, 10) || 80);
        _dash.maxW = Math.max(120, parseInt(document.getElementById("bd-set-maxw").value, 10) || 2000);
        _dash.maxH = Math.max(120, parseInt(document.getElementById("bd-set-maxh").value, 10) || 2000);
        _dash.canvasMinW = Math.max(320, parseInt(document.getElementById("bd-set-cw").value, 10) || _CANVAS_MIN_W);
        _dash.canvasMinH = Math.max(240, parseInt(document.getElementById("bd-set-ch").value, 10) || _CANVAS_MIN_H);
        var fixedEl = document.getElementById("bd-set-fixed");
        if (fixedEl) _dash.fixedCanvas = fixedEl.checked;
        _dash.grid = Math.max(4, Math.min(96, parseInt(document.getElementById("bd-set-grid").value, 10) || 12));
        var shEl = document.getElementById("bd-set-share");
        if (shEl) _dash.sharePublic = shEl.checked;
        var gradOn = document.getElementById("bd-set-shine");
        if (gradOn) _dash.shineGradient = gradOn.checked;
        var g1 = document.getElementById("bd-set-shine-c1");
        var g2 = document.getElementById("bd-set-shine-c2");
        if (g1 && g2 && _hexOk(g1.value) && _hexOk(g2.value)) _dash.shineColors = [g1.value, g2.value];
        document.querySelector(".modal-overlay").remove();
        self._restartAutoRefresh();
        self._syncCanvasSettings();
        self._markDirty();
        self.render();
        App.success("Settings saved");
      });
    },

    /* Apply near-global canvas settings (snap / grid / min-max) from _dash to module state */
    _syncCanvasSettings: function () {
      _snap = _dash.snap !== false ? (parseInt(_dash.grid, 10) || 12) : 0;
      _minW = _dash.minW != null ? _dash.minW : 120;
      _minH = _dash.minH != null ? _dash.minH : 80;
      _maxW = _dash.maxW != null ? _dash.maxW : 2000;
      _maxH = _dash.maxH != null ? _dash.maxH : 2000;
    },

    /* Apply a canvas size preset from the toolbar select (or open Settings for Custom). */
    _setCanvasSize: function (val) {
      if (val === "custom") { this._showSettings(); return; }
      var s = _PRESET_CANVAS[val];
      if (!s) return;
      var self = this;
      var oldW = Math.max(320, parseInt(_dash.canvasMinW, 10) || _CANVAS_MIN_W);
      var oldH = Math.max(240, parseInt(_dash.canvasMinH, 10) || _CANVAS_MIN_H);
      if (oldW !== s[0] || oldH !== s[1]) {
        /* Rescale the current layout to the newly chosen canvas size */
        var rx = s[0] / oldW, ry = s[1] / oldH;
        _dash.pages.forEach(function (pg) {
          (pg.widgets || []).forEach(function (w) {
            w.x = Math.round(w.x * rx);
            w.width = Math.max(40, Math.round(w.width * rx));
            w.y = Math.round(w.y * ry);
            w.height = Math.max(30, Math.round(w.height * ry));
          });
        });
      }
      _dash.canvasMinW = s[0]; _dash.canvasMinH = s[1];
      _dash.fixedCanvas = true;
      this._syncCanvasSettings();
      this._markDirty();
      this.render();
      App.success("Canvas size: " + s[0] + "×" + s[1]);
    },

    /* ── Dashboard themes ──
       Theme CSS vars are applied to the canvas area (.bd-canvas-wrap) so a
       theme restyles the dashboard canvas + widgets only — the app chrome
       (toolbar, palette, left rail, page tabs) keeps the global :root vars. */
    _syncDashboardTheme: function () {
      var pageEl = document.querySelector(".bd-canvas-wrap") || document.querySelector(".bd-page");
      if (!pageEl) return;
      var name = _dash.theme || "auto";
      var mode = _normThemeMode(_dash.themeMode);
      var t = _themeDef(name);
      var setChart = false;
      var patch = { bg: null, cardbg: null, border: null, text: null, text2: null, primary: null };
      if (t) {
        if (mode === "full" || mode === "widgets") setChart = true;
        if (mode === "full" || mode === "widgets") {
          patch.cardbg = t.cardBg; patch.border = t.gridLine;
          patch.text = t.text; patch.text2 = t.textSecondary; patch.primary = t.primary;
        }
        if (mode === "full" || mode === "background") {
          patch.bg = t.background;
        }
      }
      window.BIDashboardTheme = (t && setChart) ? t : null;
      var s = pageEl.style;
      ["--color-content-bg", "--color-card-bg", "--color-card-border", "--color-content-text", "--color-content-text-secondary", "--color-primary", "--color-surface-hover", "--bd-canvas-bg", "--color-text", "--color-text-secondary"].forEach(function (k) { s.removeProperty(k); });
      if (patch.bg != null) {
        s.setProperty("--color-content-bg", patch.bg);
        if (t && t.backgroundGradient) s.setProperty("--bd-canvas-bg", "linear-gradient(135deg," + t.backgroundGradient[0] + "," + t.backgroundGradient[1] + ")");
        else s.setProperty("--bd-canvas-bg", patch.bg);
      }
      if (patch.cardbg != null) {
        s.setProperty("--color-card-bg", patch.cardbg);
        s.setProperty("--color-card-border", patch.border || patch.cardbg);
        s.setProperty("--color-content-text", patch.text || "#ffffff");
        s.setProperty("--color-content-text-secondary", patch.text2 || "#999999");
        s.setProperty("--color-text", patch.text || "#ffffff");
        s.setProperty("--color-text-secondary", patch.text2 || "#999999");
        s.setProperty("--color-primary", patch.primary || "#2563eb");
        s.setProperty("--color-surface-hover", _mixHex(patch.cardbg, patch.text || "#ffffff", 0.08));
      }
    },

    _applyDashboardTheme: function (name) {
      if (name !== undefined) _dash.theme = name || "auto";
      _restampWidgets(_dash.theme);
      this._refreshSidebars();
      this._syncDashboardTheme();
      this._initGrid();
      this._applyZoom(_zoom);
    },

    _openThemePicker: function () {
      var self = this;
      var overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.style.cssText = "z-index:12000;";
      overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
      overlay.innerHTML =
        '<div class="modal-content" style="max-width:540px;">' +
          '<h3 style="margin:0 0 4px;font-size:18px;font-weight:700;">Dashboard Theme</h3>' +
          '<p style="font-size:12px;color:var(--color-content-text-secondary);margin:0 0 14px;">Choose how themes apply, then pick one. Upload your own JSON file to add custom themes.</p>' +
          '<div id="bd-theme-modes" class="bd-theme-modes">' + _renderApplyMode() + '</div>' +
          '<div id="bd-theme-grid" class="bd-theme-grid">' + _renderThemeSwatches() + '</div>' +
          '<div style="display:flex;gap:8px;margin-top:16px;">' +
            '<button class="btn btn-sm btn-secondary" id="bd-theme-upload"><i class="fas fa-upload"></i> Upload theme</button>' +
            '<button class="btn btn-sm btn-ghost" id="bd-theme-export"><i class="fas fa-download"></i> Export JSON</button>' +
            '<button class="btn btn-sm btn-primary" id="bd-theme-close" style="margin-left:auto;">Done</button>' +
          '</div>' +
          '<input type="file" id="bd-theme-file" accept=".json,application/json" style="display:none;" />' +
        '</div>';
      document.body.appendChild(overlay);

      overlay.querySelector("#bd-theme-close").addEventListener("click", function () { overlay.remove(); });
      overlay.querySelector("#bd-theme-export").addEventListener("click", function () {
        var t = _themeDef(_dash.theme);
        if (!t || !t.uploaded) { App.warning("Select an uploaded (custom) theme to export"); return; }
        var payload = {
          name: t.name,
          background: t.backgroundGradient ? t.backgroundGradient : t.background,
          cardBg: t.cardBg, text: t.text, textSecondary: t.textSecondary,
          gridLine: t.gridLine, axisColor: t.axisColor,
          primary: t.primary, secondary: t.secondary, accent: t.accent,
          palette: t.palette, gradient: t.gradient || [t.primary, t.secondary]
        };
        var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = (t.name || "theme").replace(/[^\w\-]+/g, "_") + ".theme.json";
        a.click();
        App.success("Theme exported");
      });

      var grid = overlay.querySelector("#bd-theme-grid");
      grid.addEventListener("click", function (e) {
        var del = e.target.closest(".bd-theme-del");
        if (del) {
          var rmId = del.getAttribute("data-remove");
          _saveUserThemes(_userThemes().filter(function (u) { return u.id !== rmId; }));
          if (_dash.theme === rmId) _dash.theme = "auto";
          self._applyDashboardTheme();
          grid.innerHTML = _renderThemeSwatches();
          return;
        }
        var sw = e.target.closest(".bd-theme-swatch");
        if (!sw) return;
        self._applyDashboardTheme(sw.getAttribute("data-theme"));
        grid.innerHTML = _renderThemeSwatches();
      });

      var modes = overlay.querySelector("#bd-theme-modes");
      modes.addEventListener("click", function (e) {
        var b = e.target.closest("button[data-mode]");
        if (!b) return;
        var m = b.getAttribute("data-mode");
        if ((_dash.themeMode || "full") === m) return;
        _dash.themeMode = m;
        self._syncDashboardTheme();
        self._initGrid();
        self._applyZoom(_zoom);
        modes.querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x.getAttribute("data-mode") === m); });
      });

      overlay.querySelector("#bd-theme-upload").addEventListener("click", function () { overlay.querySelector("#bd-theme-file").click(); });
      overlay.querySelector("#bd-theme-file").addEventListener("change", function (e) {
        var f = e.target.files && e.target.files[0];
        if (!f) return;
        var reader = new FileReader();
        reader.onload = function (ev) {
          try {
            var t = _normTheme(JSON.parse(ev.target.result || "{}"));
            var list = _userThemes();
            t.id = _slug(t.name) + "_" + Date.now();
            t.uploaded = true;
            list.unshift(t);
            _saveUserThemes(list);
            _dash.theme = t.id;
            _restampWidgets(t.id);
            self._refreshSidebars();
            self._syncDashboardTheme();
            self._initGrid();
            self._applyZoom(_zoom);
            grid.innerHTML = _renderThemeSwatches();
            App.success("Theme '" + t.name + "' uploaded and applied");
          } catch (err) {
            App.error("Invalid theme file: " + (err && err.message || err));
          }
          e.target.value = "";
        };
        reader.readAsText(f);
      });
    },

    _restartAutoRefresh: function () {
      this._stopAutoRefresh();
      var sec = _dash.refreshSec || 0;
      if (sec > 0) {
        var self = this;
        _autoRefreshTimer = setInterval(function () {
          if (_dash.mode === "present") return;
          // Re-query every widget's data source (not just re-render cached DOM).
          _queryCacheClear();
          var ws = _currentWidgets();
          for (var i = 0; i < ws.length; i++) {
            var w = ws[i];
            if (!w || w.type === "text" || w.type === "image" || w.type === "custom" || w.type === "page" || w.type === "button" || w.type === "shape" || w.type === "divider") continue;
            var cell = document.querySelector('.ff-widget-content[data-id="' + w.id + '"]');
            if (cell) self._renderWidgetContent(w, cell);
          }
        }, sec * 1000);
      }
    },
    _stopAutoRefresh: function () {
      if (_autoRefreshTimer) { clearInterval(_autoRefreshTimer); _autoRefreshTimer = null; }
    },

    /* ── Drill-down ── */
    _drillDown: function (field, value) {
      _drillStack.push(Object.assign({}, _filters));
      CrossFilterBus.dispatch(field, value, null);
    },
    _drillUp: function () {
      if (_drillStack.length === 0) return;
      var entry = _drillStack.pop();
      var isPageEntry = entry && typeof entry === "object" && "filters" in entry;
      var fl = isPageEntry ? (entry.filters || {}) : entry;
      var from = isPageEntry && typeof entry.from === "number" ? entry.from : _dash.currentPage;
      if (from >= 0 && from < _dash.pages.length && from !== _dash.currentPage) {
        _dash.currentPage = from;
        _renderPageTabs();
      }
      this._syncPageHash();
      this.render();
    },
  };

  /* ── Render helpers ── */

  function _friendlyName(raw) {
    if (!raw) return "";
    return _titleCaseName(String(raw).replace(/\.(csv|json|xlsx?|parquet|duckdb)$/i, ""));
  }

  function _titleCaseName(raw) {
    if (!raw) return "";
    return String(raw).replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim()
      .split(" ").map(function (w) {
        if (/[A-Z]/.test(w)) return w;
        return w.charAt(0).toUpperCase() + w.slice(1);
      }).join(" ");
  }

  /* Onboarding card shown on the empty canvas (fixes the old empty-state that
     was wiped out by _initGrid and never displayed). */
  function _bdEmptyStateHtml(table) {
    return '<div class="bd-empty" id="bd-empty-state" draggable="false">' +
      '<div class="bd-empty-icon"><i class="fas fa-layer-group"></i></div>' +
      '<h3>Start Building</h3>' +
      '<p>' + (table
        ? 'Connected to <b>' + _esc(table) + '</b> — drag widgets here or add one below.'
        : 'Pick a data source, then drag widgets here or add one below.') + '</p>' +
      '<div class="bd-empty-actions">' +
        '<button class="btn btn-primary" onclick="BlankDash._showAddModal()"><i class="fas fa-plus"></i> Add Widget</button>' +
        '<button class="btn btn-ghost" onclick="BlankDash._toggleCopilot()"><i class="fas fa-wand-magic-sparkles"></i> Ask AI</button>' +
        '<button class="btn btn-ghost" onclick="BlankDash._showTemplatesModal()"><i class="fas fa-copy"></i> Templates</button>' +
      '</div>' +
      '<p class="bd-empty-hint">Or click Dots / File on the dropdown above to load a saved dashboard.</p>' +
    '</div>';
  }

  function _renderToolbar() {
    var present = _dash.mode === "present";
    if (present) {
      return '<div class="bd-toolbar bd-toolbar-present">' +
        '<div class="bd-toolbar-left">' +
          '<span class="bd-present-title">' + _esc(_dash.name || "Dashboard") + '</span>' +
          '<span class="bd-present-page">Page ' + (_dash.currentPage + 1) + ' of ' + _dash.pages.length + '</span>' +
        '</div>' +
        '<div class="bd-toolbar-right">' +
          '<button class="btn btn-sm btn-ghost" onclick="BlankDash._toggleFullscreen()" title="Toggle fullscreen"><i class="fas fa-expand"></i> Fullscreen</button>' +
          '<button class="btn btn-sm btn-ghost" onclick="BlankDash._exitPresent()" title="Exit presentation"><i class="fas fa-arrow-left"></i> Exit Preview</button>' +
        '</div>' +
      '</div>';
    }
    /* Single-row ribbon, Power BI style:
       File (back + name + save status) | Data | Edit | View (zoom/grid/fullscreen) | Tools (AI/Arrange/Pages) | Actions (overflow/Save/Present) */
    return '<div class="bd-toolbar bd-ribbon">' +
      '<div class="bd-ribbon-file">' +
        '<button class="btn btn-ghost btn-sm bd-back-btn" onclick="BlankDash._goBack()" title="Back to Dashboard Studio"><i class="fas fa-arrow-left"></i></button>' +
        '<input type="text" class="bd-name-input" id="bd-name" value="' + _esc(_dash.name) + '" placeholder="Untitled Dashboard" aria-label="Dashboard name" onchange="BlankDash._rename(this.value)">' +
        '<span class="bd-save-badge' + (_dirty ? " bd-save-dirty" : "") + '" id="bd-save-badge" title="Save status">' + (_dirty ? "Changes" : "Saved") + '</span>' +
      '</div>' +
      '<div class="bd-ribbon-scroll">' +
        '<div class="bd-ribbon-seg" title="' + _esc(_dash.table || "No data source selected") + '">' +
          '<span class="bd-ribbon-seg-label"><i class="fas fa-table-columns"></i><span>Data</span></span>' +
          '<select class="bd-toolbar-select" id="bd-table-select" aria-label="Data source" onchange="BlankDash._changeTable(this.value)">' +
            '<option value="">Select table...</option>' +
          '</select>' +
          '<button class="bd-ribbon-btn" onclick="CrossFilterBus.clear()" title="Clear all filters"><i class="fas fa-filter-circle-xmark"></i></button>' +
        '</div>' +
        '<div class="bd-ribbon-seg">' +
          '<span class="bd-ribbon-seg-label"><i class="fas fa-chart-pie"></i><span>Visual</span></span>' +
          '<select class="bd-toolbar-select bd-chart-add" id="bd-chart-add" aria-label="Insert chart" title="Add a chart to the canvas" onchange="var s=this;if(s.value){BlankDash._addWidget(s.value);s.value=\'\'}">' +
            '<option value="">Insert visual...</option>' + _insertChartOptions() +
          '</select>' +
        '</div>' +
        '<div class="bd-ribbon-seg">' +
          '<button class="bd-ribbon-btn" onclick="BlankDash._undo()" ' + (_undoStack.length > 0 ? '' : 'disabled aria-disabled="true"') + ' title="Undo (Ctrl+Z)"><i class="fas fa-undo"></i></button>' +
          '<button class="bd-ribbon-btn" onclick="BlankDash._redo()" ' + (_redoStack.length > 0 ? '' : 'disabled aria-disabled="true"') + ' title="Redo (Ctrl+Shift+Z)"><i class="fas fa-redo"></i></button>' +
        '</div>' +
        '<div class="bd-ribbon-seg">' +
          '<button class="bd-ribbon-btn" onclick="BlankDash._fitZoom()" title="Fit to screen"><i class="fas fa-maximize"></i></button>' +
          '<button class="bd-ribbon-btn" onclick="BlankDash._zoomOut()" title="Zoom out"><i class="fas fa-minus"></i></button>' +
          '<button class="bd-ribbon-btn bd-zoom-label" id="bd-zoom-pct" onclick="BlankDash._toggleZoomMenu(event)" title="Zoom level — click for presets">' + Math.round(_zoom * 100) + '%</button>' +
          '<button class="bd-ribbon-btn" onclick="BlankDash._zoomIn()" title="Zoom in"><i class="fas fa-plus"></i></button>' +
          '<button class="bd-ribbon-btn" id="bd-grid-toggle" onclick="BlankDash._toggleGrid()" title="Toggle grid"><i class="fas fa-border-all"></i></button>' +
          '<button class="bd-ribbon-btn" id="bd-grid-mode-toggle" onclick="BlankDash._toggleGridMode()" title="Grid mode: ' + (_dash.gridMode || "dots") + ' — click to cycle (dots / columns / off)">' +
            (_dash.gridMode === "cols" ? '<i class="fas fa-columns"></i>' : (_dash.gridMode === "off" ? '<i class="fas fa-border-none"></i>' : '<i class="fas fa-border-all"></i>')) +
          '</button>' +
          '<button class="bd-ribbon-btn" id="bd-minimap-toggle" onclick="BlankDash._toggleMinimap()" title="Toggle navigator minimap (M)"><i class="fas fa-map"></i></button>' +
          '<button class="bd-ribbon-btn bd-panels-btn" id="bd-panels-toggle" onclick="BlankDash._toggleSidePanels()" title="Show / hide the Layers and Visualizations panels"><i class="fas fa-columns"></i></button>' +
          '<button class="bd-ribbon-btn" onclick="BlankDash._toggleFullscreen()" title="Toggle fullscreen"><i class="fas fa-expand"></i></button>' +
        '</div>' +
        '<div class="bd-ribbon-seg">' +
          '<button class="bd-ribbon-btn bd-ribbon-btn-lbl ai-gen-btn" onclick="BlankDash._toggleCopilot()" title="AI assistant: generate recommended charts or ask about this data"><i class="fas fa-wand-magic-sparkles"></i><span>AI Assist</span></button>' +
          '<button class="bd-ribbon-btn bd-ribbon-btn-lbl" onclick="BlankDash._showArrangeMenu(this)" title="Arrange: align, distribute, layer and organise selection"><i class="fas fa-object-group"></i><span>Arrange</span></button>' +
          '<button class="bd-ribbon-btn bd-ribbon-btn-lbl" onclick="BlankDash._showPagesModal()" title="Manage pages: re-order, duplicate, delete"><i class="fas fa-folder"></i><span>Pages</span></button>' +
        '</div>' +
      '</div>' +
      '<div class="bd-ribbon-actions">' +
        '<button class="btn btn-sm btn-ghost" onclick="BlankDash._showToolbarOverflowMenu(this)" title="More actions"><i class="fas fa-ellipsis-vertical"></i></button>' +
        '<button class="btn btn-sm btn-outline bd-save-btn" onclick="BlankDash._save()" title="Save dashboard (Ctrl+S)"><i class="fas fa-save"></i> Save</button>' +
        '<button class="btn btn-sm btn-primary" onclick="BlankDash._present()" title="Present dashboard"><i class="fas fa-chalkboard-user"></i> Present</button>' +
      '</div>' +
    '</div>';
  }

  var _PALETTE_CATS = [
    { key: "Basics",           label: "Basics",           icon: "fa-boxes-stacked" },
    { key: "Standard Charts",  label: "Charts & Visuals", icon: "fa-chart-simple" },
    { key: "Advanced Charts",  label: "Advanced",         icon: "fa-bezier-curve" },
    { key: "Data Viz",         label: "Data Viz",         icon: "fa-border-all" },
    { key: "Statistical",      label: "Statistical",      icon: "fa-calculator" },
    { key: "Specialized",      label: "Specialized",      icon: "fa-wand-magic-sparkles" },
    { key: "Maps",             label: "Maps",             icon: "fa-map-location-dot" },
    { key: "Filters",          label: "Filters & Slicers",icon: "fa-filter" },
    { key: "Media",            label: "Media",            icon: "fa-image" },
    { key: "Navigation",       label: "Navigation",       icon: "fa-arrow-right-to-bracket" },
  ];

  function _paletteCatLabel(cat) {
    for (var i = 0; i < _PALETTE_CATS.length; i++) if (_PALETTE_CATS[i].key === cat) return _PALETTE_CATS[i];
    return { key: cat, label: cat, icon: "fa-tag" };
  }

  function _renderPalette() {
    var catOpts = '<option value="">All categories</option>';
    _chartGroups().forEach(function (g) {
      catOpts += '<option value="' + g.key + '">' + _esc(g.label) + '</option>';
    });
    var html = '<div class="bd-palette" id="bd-palette">' +
      '<div class="bd-palette-title">' +
        '<span class="bd-palette-title-name"><i class="fas fa-shapes"></i> Visualizations</span>' +
        '<span class="bd-palette-count">' + _CATALOGUE.length + '</span>' +
        '<button class="bd-palette-toggle" onclick="BlankDash._togglePalette()" title="Hide panel"><i class="fas fa-angles-right"></i></button>' +
      '</div>' +
      '<div class="bd-palette-filters">' +
        '<input type="text" class="bd-palette-search" id="bd-palette-search" placeholder="Search visualizations..." oninput="BlankDash._filterPalette(this.value)">' +
        '<select class="bd-palette-cat" id="bd-palette-cat" title="Filter by category" onchange="BlankDash._filterPaletteCat(this.value)">' + catOpts + '</select>' +
      '</div>' +
      '<div class="bd-palette-items" id="bd-palette-items">' +
        _renderPaletteItems() +
      '</div>' +
    '</div>';
    return html;
  }

  function _paletteItemHtml(item) {
      return '<div class="bd-palette-item" draggable="true" data-type="' + item.type + '" data-label="' + item.label + '" title="' + item.label + ' · drag or click to add" aria-label="' + item.label + '">' +
        '<div class="bd-palette-icon" style="background:linear-gradient(135deg,' + item.color + ',' + _darken(item.color) + ')">' +
          '<i class="fas ' + item.icon + '"></i>' +
        '</div>' +
        '<span class="bd-palette-item-label">' + _esc(item.label) + '</span>' +
      '</div>';
    }

  /* Ordered palette groups: the merged "Charts" group (Basics + Standard Charts + Data Viz) comes
     first and starts open, then every category is a normal collapsible dropdown group. */
  function _chartGroups() {
    var primaryKeys = { "Basics": 1, "Standard Charts": 1, "Data Viz": 1 };
    var groups = [];
    var prim = _CATALOGUE.filter(function (c) { return primaryKeys[c.cat]; });
    if (prim.length) groups.push({ key: "Charts", label: "Charts", icon: "fa-chart-column", items: prim });
    _PALETTE_CATS.forEach(function (cat) {
      if (primaryKeys[cat.key]) return;
      var items = _CATALOGUE.filter(function (c) { return c.cat === cat.key; });
      if (cat.key === "Maps") items = items.concat(_MAP_CATALOGUE);
      if (!items.length) return;
      groups.push({ key: cat.key, label: cat.label, icon: cat.icon, items: items });
    });
    return groups;
  }

  function _insertChartOptions() {
    var html = '';
    _chartGroups().forEach(function (g) {
      html += '<optgroup label="' + _esc(g.label) + '">';
      g.items.forEach(function (it) { html += '<option value="' + _esc(it.type) + '">' + _esc(it.label) + '</option>'; });
      html += '</optgroup>';
    });
    return html;
  }

  function _renderPaletteItems() {
    var html = '';
    var groups = _chartGroups();
    groups.forEach(function (g, gi) {
      var expanded = (gi === 0 || g.key === "Filters") ? " expanded" : "";
      html += '<div class="bd-palette-group' + expanded + '" data-cat="' + g.key + '">' +
        '<button class="bd-palette-group-title" type="button" title="' + _esc(g.label) + '" onclick="this.parentElement.classList.toggle(\'expanded\')">' +
          '<i class="fas ' + g.icon + '"></i><span>' + _esc(g.label) + '</span>' +
          '<span class="bd-palette-group-count">' + g.items.length + '</span>' +
          '<i class="fas fa-angle-right bd-palette-group-caret"></i>' +
        '</button>' +
        '<div class="bd-palette-group-items">';
      g.items.forEach(function (item) { html += _paletteItemHtml(item); });
      html += '</div></div>';
    });
    html += '<div class="bd-palette-group-basic" id="bd-palette-group-basic" data-cat="none" style="display:none"></div>';
    return html;
  }

  function _renderPageTabs() {
    var html = '<div class="bd-page-tabs">';
    _dash.pages.forEach(function (page, idx) {
      var active = idx === _dash.currentPage ? " active" : "";
      html += '<div class="bd-page-tab' + active + '" onclick="BlankDash._switchPage(' + idx + ')" ondblclick="BlankDash._promptRenamePage(' + idx + ')">' +
        '<i class="fas fa-file-lines" style="font-size:11px;opacity:0.75;"></i>' +
        '<span class="bd-page-tab-name">' + _esc(page.name) + '</span>' +
        '<button class="bd-page-tab-menu" title="Page actions" onclick="event.stopPropagation();BlankDash._togglePageMenu(' + idx + ', this)"><i class="fas fa-ellipsis-vertical"></i></button>' +
      '</div>';
    });
    html += (_dash.mode === "present" ? '' : '<button class="bd-page-tab-add" onclick="BlankDash._addPage()" title="Add page"><i class="fas fa-plus"></i></button>');
    html += '</div>';
    return html;
  }

  var _GROUP_META = {
    "KPIs": { icon: "fa-gauge-high" },
    "Charts": { icon: "fa-chart-column" },
    "Tables & Grids": { icon: "fa-table" },
    "Filters": { icon: "fa-filter" },
    "Maps": { icon: "fa-map-location-dot" },
    "Text & Links": { icon: "fa-font" },
    "Media": { icon: "fa-image" }
  };
  var _GROUP_ORDER = ["KPIs", "Charts", "Tables & Grids", "Filters", "Maps", "Text & Links", "Media"];

  function _layerGroupOf(w) {
    var t = w.type || "";
    if (["kpi", "gauge", "progress", "radial", "status", "bullet", "comparison", "targetactual", "watertank", "trendkpi", "percentagekpi", "prevkpi", "iconkpi", "multikpi", "sparkkpi", "sparkbarkpi", "minilinekpi", "sparkdeltakpi", "donutkpi"].indexOf(t) !== -1) return "KPIs";
    if (t === "table") return "Tables & Grids";
    if (_FILTER_TYPES.indexOf(t) !== -1) return "Filters";
    if (_isMapType(t)) return "Maps";
    if (t === "text" || t === "page") return "Text & Links";
    if (t === "image" || t === "custom") return "Media";
    return "Charts";
  }

  /* Keep a widget's lock badge / resize handles consistent when locking changes */
  function _syncResizeChrome(el, w) {
    var present = _dash.mode === "present";
    var handles = el.querySelectorAll(".ff-resize-handle");
    var badge = el.querySelector(".ff-lock-badge");
    if (!w.locked && !present) {
      if (!handles.length) {
        ["nw", "n", "ne", "e", "se", "s", "sw", "w"].forEach(function (dir) {
          var h = document.createElement("div");
          h.className = "ff-resize-handle";
          h.dataset.resize = dir;
          h.dataset.id = w.id;
          el.appendChild(h);
        });
      }
      if (badge) badge.remove();
    } else if (w.locked) {
      if (handles.length) for (var i = 0; i < handles.length; i++) handles[i].remove();
      if (!badge && !present) {
        badge = document.createElement("div");
        badge.className = "ff-lock-badge";
        badge.innerHTML = '<i class="fas fa-lock"></i>';
        el.appendChild(badge);
      }
    }
  }

  /* ── Fields pane: table schema as draggable field chips ── */
  var _FIELD_ICONS = { dim: "fa-shapes", measure: "fa-calculator", date: "fa-calendar-days" };
  var _KPIV_FAMILY = ["kpi", "gauge", "progress", "radial", "status", "bullet", "comparison", "targetactual", "watertank", "trendkpi", "percentagekpi", "prevkpi", "iconkpi", "multikpi", "sparkkpi", "sparkbarkpi", "minilinekpi", "sparkdeltakpi", "donutkpi"];

  /* P3.11 — shared calendar date hierarchy (Year > Quarter > Month > Day),
     backed by the analytics.calendar dimension asset in the data catalog. */
  var _CAL_LEVELS = [
    { level: "Year", calCol: "year" },
    { level: "Quarter", calCol: "year_quarter" },
    { level: "Month", calCol: "year_month" },
    { level: "Day", calCol: "date" }
  ];
  /* Widget types that honour calendar-hierarchy grouping (group-by charts). */
  var _DRILL_EXEMPT = ["text", "image", "custom", "page", "button", "divider", "shape", "geomap", "countrymap", "table", "slicer", "daterange", "searchbox"];
  function _widgetDateCols(w) {
    var cfg = w.config || {};
    var table = cfg.table || _dash.table || "";
    var out = [];
    try {
      var t = (window.App && App.state && App.state.tables && App.state.tables[table]) || {};
      (t.columns || []).forEach(function (c) {
        var n = typeof c === "string" ? c : (c.column_name || c.name || "");
        var ty = typeof c === "object" ? (c.column_type || c.type || "") : "";
        if (n && /date|timestamp|time/i.test(ty)) out.push(n);
      });
    } catch (e) {}
    return out;
  }
  function _widgetCalendarAvail(w) {
    var cfg = w.config || {};
    if (cfg.hierarchyLevel) return true;
    var x = cfg.xCol || "";
    if (!x) return false;
    return _widgetDateCols(w).indexOf(x) !== -1;
  }
  function _calLevelInfo(lvl) {
    var l = String(lvl || "").toLowerCase();
    for (var i = 0; i < _CAL_LEVELS.length; i++) {
      if (_CAL_LEVELS[i].level.toLowerCase() === l) return _CAL_LEVELS[i];
    }
    return null;
  }

  function _qi(name) {
    return '"' + String(name == null ? "" : name).replace(/"/g, '""') + '"';
  }
  function _fieldKind(ctype) {
    var t = String(ctype || "").toLowerCase();
    if (/date|time|timestamp/i.test(t)) return "date";
    if (/int|float|double|decimal|numeric|real|bigint|smallint|tinyint|money|long|short|fixed|serial/i.test(t)) return "measure";
    return "dim";
  }
  function _fieldColumns(table) {
    var entry = (window.App && App.state && App.state.tables) ? App.state.tables[table] : null;
    if (!entry) return [];
    var cols = Array.isArray(entry.columns) ? entry.columns : [];
    var out = [];
    cols.forEach(function (c) {
      if (typeof c === "string") { if (c) out.push({ name: c, type: "" }); return; }
      var n = c.column_name || c.name || "";
      if (n) out.push({ name: n, type: c.column_type || c.type || "" });
    });
    return out;
  }
  function _isNumericColumn(c) {
    var t = String((c && (c.type || c.column_type)) || "").toLowerCase();
    if (!t) return false;
    if (t.indexOf("decimal") !== -1 || t.indexOf("numeric") !== -1 ||
        t.indexOf("number") !== -1 || t.indexOf("int") !== -1 ||
        t.indexOf("double") !== -1 || t.indexOf("float") !== -1 ||
        t.indexOf("real") !== -1 || t.indexOf("hugeint") !== -1 ||
        t.indexOf("bigint") !== -1 || t.indexOf("smallint") !== -1 ||
        t.indexOf("tinyint") !== -1 || t.indexOf("utinyint") !== -1 ||
        t.indexOf("usmallint") !== -1 || t.indexOf("uinteger") !== -1) {
      return true;
    }
    return false;
  }
  function _hasField(table, col) {
    return _fieldColumns(table).some(function (c) { return c.name === col; });
  }
  function _humanizeCol(s) {
    return String(s || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim().replace(/\b\w/g, function (m) { return m.toUpperCase(); });
  }
  /* Find a plausible JOIN key shared by two tables (prefers id-ish columns). */
  function _joinKey(a, b) {
    var ca = _fieldColumns(a), cb = _fieldColumns(b);
    var na = [], nb = {};
    ca.forEach(function (c) { na.push(c.name); });
    cb.forEach(function (c) { nb[c.name] = true; });
    var matches = [];
    na.forEach(function (n) { if (nb[n]) matches.push(n); });
    if (!matches.length) return null;
    for (var i = 0; i < matches.length; i++) if (/_id$/.test(matches[i])) return matches[i];
    for (var j = 0; j < matches.length; j++) if (/^(id|sid|uid|key|code)$/i.test(matches[j])) return matches[j];
    for (var k = 0; k < matches.length; k++) {
      if (!/(name|date|time|status|type|category|region|city|country|label|desc)/i.test(matches[k])) return matches[k];
    }
    return matches.length === 1 ? matches[0] : null;
  }
  /* Related tables of the base table that share a usable join key. */
  function _relatedTableKeys(base) {
    var out = {};
    if (!base || !App.state || !App.state.tables) return out;
    Object.keys(App.state.tables).forEach(function (t) {
      if (t === base) return;
      var k = _joinKey(base, t);
      if (k) out[t] = k;
    });
    return out;
  }
  /* True when the current canvas already shows an AI insight/narrative widget,
     so repeated AI Generates never stack a second narrative for the same table. */
  function _hasInsightWidget() {
    return _currentWidgets().some(function (w) {
      var cfg = w.config || {};
      var title = String(w.title || cfg.label || w.label || w.id || "");
      return /insight/i.test(title);
    });
  }
  /* Resolve a column reference against the base table + any joins (Phase 2b). */
  function _fieldRef(col, base, joins) {
    if (joins) {
      for (var i = 0; i < joins.length; i++) {
        if (_hasField(joins[i].table, col)) return "j" + (i + 1) + "." + _qi(col);
      }
    }
    return _qi(col);
  }
  /* Keys whose change should trigger a SQL rebuild in _onWidgetEdited.
     Visual-only props (curveType, lineStyle, colors, typography, ...) must
     NOT rebuild the query — that would clobber a hand-tuned SQL. */
  function _isSqlAffectingKey(key) {
    var bare = String(key || "");
    if (bare.indexOf("config.") === 0) bare = bare.slice("config.".length);
    else if (bare.indexOf("widget.") === 0) bare = bare.slice("widget.".length);
    return ["table", "sql", "xCol", "yCol", "measures", "agg", "limit", "hierarchyLevel", "joins", "dateCol", "timeIntel", "measureExpr"].indexOf(bare) !== -1;
  }
  /* Rebuild a widget's SQL from its current field config (xCol / measures / joins). */
  function _buildWidgetSql(w) {
    var cfg = w.config || {};
    var base = cfg.table || _dash.table || "";
    var joins = (cfg.joins && cfg.joins.length) ? cfg.joins : null;
    if (!base) return cfg.sql || "";
    var from = "FROM " + _qi(base);
    var joinSql = "";
    if (joins) {
      joins.forEach(function (j, i) {
        var alias = "j" + (i + 1);
        var kw = j.type === "inner" ? " INNER JOIN " : " LEFT JOIN ";
        joinSql += kw + _qi(j.table) + " " + alias + " ON " + _qi(base) + "." + _qi(j.key) + " = " + alias + "." + _qi(j.key);
      });
    }
    var agg = cfg.agg && ["SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_DISTINCT"].indexOf(String(cfg.agg).toUpperCase()) !== -1 ? String(cfg.agg).toUpperCase() : "SUM";
    var ref = function (col) { return _fieldRef(col, base, joins); };
    if (agg === "COUNT_DISTINCT") agg = "count(DISTINCT ";
    /* Resolve a measure entry to a SQL fragment. Measures are either plain
       column names (strings) or registry objects {id,label,expr}: an object
       with its own expression (or a resolvable registry id) is used verbatim,
       otherwise it falls back to its label/column as a quoted column ref. */
    var measureSql = function (m) {
      if (m == null) return { expr: false, sql: "" };
      if (typeof m === "string") return { expr: false, sql: ref(m) };
      var expr = m.expr;
      if (!expr && m.id) {
        var reg = _measureById(m.id);
        if (reg) expr = reg.expression;
      }
      if (expr) return { expr: true, sql: expr };
      var col = m.column || m.label || m.name;
      return { expr: false, sql: ref(col || String(m)) };
    };
    var aggWrap = function (col) {
      var mr = measureSql(col);
      if (mr.expr) return mr.sql;
      return agg === "count(DISTINCT " ? "count(DISTINCT " + mr.sql + ")" : agg + "(" + mr.sql + ")";
    };

    if (_KPIV_FAMILY.indexOf(w.type) !== -1) {
      var m = (cfg.measures && cfg.measures.length) ? cfg.measures[0] : (cfg.yCol && cfg.yCol !== "value" ? cfg.yCol : "");
      if (!m) return cfg.sql || "";
      return "SELECT " + (agg === "COUNT" ? "COUNT(*)" : aggWrap(m)) + " AS value " + from + joinSql;
    }
    if (w.type === "table") return cfg.sql || "";

    /* Generic chart: label + one/more aggregated measures → multi-series renderer. */
    var x = cfg.xCol || "label";
    var parts = [ref(x) + " AS label"];
    var measures = cfg.measures || [];
    var measure = measures.length ? measures[0] : (cfg.yCol && cfg.yCol !== "value" ? cfg.yCol : null);
    parts.push(measure ? aggWrap(measure) + " AS value" : "COUNT(*) AS value");
    for (var s = 1; s < measures.length; s++) {
      var ms = measures[s];
      var alias = typeof ms === "string" ? ms : (ms.label || ms.column || ms.name || ("m" + (s + 1)));
      parts.push(aggWrap(ms) + " AS " + _qi(alias));
    }
    var groupExpr = ref(x);
    var orderExpr = "value DESC";
    var calLevel = _calLevelInfo(cfg.hierarchyLevel);
    if (calLevel && !joins && _widgetDateCols(w).indexOf(x) !== -1) {
      /* P3.11 — group by the analytics.calendar date hierarchy level. */
      var calExpr = 'c.' + _qi(calLevel.calCol);
      parts[0] = calExpr + " AS label";
      groupExpr = "1";
      orderExpr = "1 ASC";
      from += " JOIN analytics.calendar c ON c.\"date\" = CAST(" + _qi(cfg.table || _dash.table || "sales_data") + "." + ref(x) + " AS DATE)";
    }
    return "SELECT " + parts.join(", ") + " " + from + joinSql + " GROUP BY " + groupExpr + " ORDER BY " + orderExpr + " LIMIT " + (cfg.limit || 12);
  }

  /* Format a /measures/{id}/preview payload as compact HTML (modal preview) */
  function _measurePreviewHtml(p) {
    if (!p) return "";
    var mode = (p.mode && p.mode !== "none") ? p.mode.toUpperCase() : "TOTAL";
    var out = '<div style="font-size:12px;line-height:1.6">' +
      '<div><b>' + _esc(mode) + '</b> <span style="color:var(--color-text-muted)">(' + _esc(p.cur_label || "Value") + ')</span>: ' +
      '<b>' + _esc(p.value == null ? "—" : _fmtNumShort(p.value)) + '</b></div>';
    if (p.mode && p.mode !== "none") {
      out += '<div id="bd-prevline">Prior: ' + _esc(p.previous == null ? "—" : _fmtNumShort(p.previous)) +
        ' · Full prev: ' + _esc(p.comparison == null ? "—" : _fmtNumShort(p.comparison)) +
        ' · Δ ' + _esc(p.delta == null ? "—" : _fmtNumShort(p.delta)) +
        ' <span style="color:' + ((p.delta_pct || 0) < 0 ? "var(--color-danger)" : "var(--color-success)") + '">(' + _fmtPct(p.delta_pct) + ')</span></div>';
      if (p.anchor) out += '<div style="color:var(--color-text-muted)">anchored to ' + _esc(p.anchor) + '</div>';
    }
    if (p.trend && p.trend.labels && p.trend.labels.length) {
      var vals = p.trend.values.map(function (v) { return Number(v) || 0; });
      var w = 260, h = 36;
      var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
      var span = (max - min) || 1;
      var pts = vals.map(function (v, i) {
        var x = (i / (vals.length - 1)) * w;
        var y = h - 3 - ((v - min) / span) * (h - 6);
        return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      }).join(" ");
      out += '<svg width="' + w + '" height="' + h + '" style="margin-top:6px;max-width:100%"><polyline points="' + pts + '" fill="none" stroke="var(--color-primary)" stroke-width="1.5"/></svg>';
    }
    return out + "</div>";
  }
  function _fmtNumShort(v) {
    var n = Number(v);
    if (!isFinite(n)) return String(v == null ? "—" : v);
    var abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return n.toFixed(2);
  }
  function _fmtPct(v) {
    return (v == null || v === "") ? "—" : ((v > 0 ? "+" : "") + Number(v).toFixed(2) + "%");
  }

  /* —— Fields pane rendering —— */
  function _renderFieldChips(table, prefix) {
    var cols = _fieldColumns(table);
    var groups = { dim: [], measure: [], date: [] };
    cols.forEach(function (c) { (groups[_fieldKind(c.type)] || groups.dim).push(c); });
    var html = "";
    [["measure", "Measures", "fa-calculator"], ["dim", "Dimensions", "fa-shapes"], ["date", "Dates", "fa-calendar-days"]].forEach(function (g) {
      var kind = g[0], label = g[1];
      if (!groups[kind].length) return;
      var chips = groups[kind].map(function (c) {
        return '<span class="bd-field bd-field-' + kind + '" draggable="true" title="Click to add · drag onto a chart or KPI to bind"' +
          ' data-table="' + _esc(table) + '" data-field="' + _esc(c.name) + '" data-kind="' + kind + '"' +
          ' data-search="' + _esc((c.name + " " + label).toLowerCase()) + '"' +
          ' onclick="BlankDash._onFieldClick(this)">' +
          '<i class="bd-field-ico fas ' + g[2] + '"></i>' + _esc(c.name) + '</span>';
      }).join("");
      html += '<div class="bd-fields-group">' +
        '<div class="bd-fields-group-label"><span>' + label + '</span><span class="bd-fields-count">' + groups[kind].length + '</span></div>' +
        '<div class="bd-fields-row">' + chips + '</div>' +
      '</div>';
    });
    return '<div class="bd-fields-table' + (prefix ? " bd-fields-join" : "") + '">' +
      '<div class="bd-fields-table-name"><i class="fas fa-table-columns"></i>' + _esc(prefix ? (prefix + " › ") + table : table) + '</div>' +
      (html || '<div class="bd-lr-empty">No columns available</div>') +
    '</div>';
  }
  function _renderRelatedFieldSection() {
    var base = _dash.table;
    if (!base) return "";
    var tables = (window.App && App.state && App.state.tables) ? Object.keys(App.state.tables) : [];
    var rel = tables.filter(function (t) { return t !== base && _joinKey(base, t) !== null; });
    if (!rel.length) return "";
    var chips = rel.map(function (t) {
      var active = _relatedTables.indexOf(t) !== -1;
      return '<button type="button" class="bd-fields-relchip' + (active ? " active" : "") + '" onclick="BlankDash._toggleRelatedTable(\'' + _esc(t) + '\')" title="Show ' + _esc(t) + ' fields · join key ' + _esc(_joinKey(base, t)) + '">' +
        '<i class="fas fa-link"></i>' + _esc(t) + '</button>';
    }).join("");
    var joinsHtml = "";
    _relatedTables.forEach(function (t) {
      if (!t) return;
      var key = _joinKey(base, t);
      if (!key) return;
      joinsHtml += '<div class="bd-fields-join-note"><i class="fas fa-arrow-right"></i>join <b>' + _esc(t) + '</b> on ' + _esc(key) + '</div>' + _renderFieldChips(t, "");
    });
    return '<div class="bd-fields-related">' +
      '<div class="bd-fields-related-label"><i class="fas fa-link"></i> Related tables</div>' +
      '<div class="bd-fields-related-chips">' + chips + '</div>' +
      joinsHtml +
    '</div>';
  }
  function _renderFieldsSection() {
    var table = _dash.table;
    var body = table
      ? (_renderFieldChips(table, "") + _renderRelatedFieldSection())
      : '<div class="bd-lr-empty"><i class="fas fa-table-columns"></i><span>Select a data source in the toolbar to browse its fields. Drag any field onto the canvas to build a chart or KPI — drop it on an existing chart to bind it.</span></div>';
    return '<div class="bd-lr-section bd-fields-section' + (_lrSectionCollapsed.fields ? " collapsed" : "") + '">' +
      '<div class="bd-lr-section-head"><span class="bd-lr-title">' +
        '<button type="button" class="bd-lr-caret" onclick="BlankDash._toggleLrSection(\'fields\', event)" title="Collapse / expand Fields" aria-label="Toggle Fields"><i class="fas fa-caret-right"></i></button>' +
        '<span class="bd-lr-ico"><i class="fas fa-table-columns"></i></span> Fields</span>' +
        '<button class="bd-lr-add" onclick="BlankDash._focusTableSelect()" title="Change data source"><i class="fas fa-caret-down"></i> ' + _esc(table || "Data") + '</button></div>' +
      '<div class="bd-lr-body">' +
        (table ? '<input type="text" class="bd-lr-search" placeholder="Find fields..." value="' + _esc(_fieldSearch) + '" oninput="BlankDash._filterFields(this.value)">' : "") +
        '<div class="bd-lr-fields">' + body + '</div></div>' +
    '</div>';
  }

  /* —— Measures pane rendering (registered, reusable calculations) —— */
  function _renderMeasuresSection() {
    var rows = _measures.map(function (m) {
      var tip = "Drag to canvas · click to add as a time-intelligence KPI" +
        (m.description ? " · " + m.description : "");
      return '<div class="bd-lr-measure" draggable="true" data-measure-id="' + _esc(m.id) + '"' +
        ' data-search="' + _esc(((m.name || "") + " " + (m.table_name || "") + " " + (m.description || "")).toLowerCase()) + '"' +
        ' title="' + _esc(tip) + '">' +
        '<div class="bd-lr-measure-name"><i class="fas fa-calculator"></i>' + _esc(m.name) + '</div>' +
        '<div class="bd-lr-measure-meta">' + _esc(m.table_name) + (_esc(m.date_column) ? " · " + _esc(m.date_column) : "") + ' · ' + _esc(m.format) + '</div>' +
        (m.description ? '<div class="bd-lr-measure-desc">' + _esc(m.description) + '</div>' : "") +
        '<div class="bd-lr-measure-expr">' + _esc(m.expression) + '</div>' +
        '<div class="bd-lr-measure-actions">' +
          '<button type="button" class="bd-lr-act" title="Add KPI (YTD)" onclick="event.stopPropagation();BlankDash._measureAddKpi(\'' + _esc(m.id) + '\')"><i class="fas fa-chart-line"></i></button>' +
          '<button type="button" class="bd-lr-act" title="Edit measure" onclick="event.stopPropagation();BlankDash._openMeasureModal(\'' + _esc(m.id) + '\')"><i class="fas fa-pen"></i></button>' +
          '<button type="button" class="bd-lr-act" title="Delete measure" onclick="event.stopPropagation();BlankDash._deleteMeasure(\'' + _esc(m.id) + '\')"><i class="fas fa-trash"></i></button>' +
        '</div>' +
      '</div>';
    }).join("");
    return '<div class="bd-lr-section bd-measures-section' + (_lrSectionCollapsed.measures ? " collapsed" : "") + '">' +
      '<div class="bd-lr-section-head"><span class="bd-lr-title">' +
        '<button type="button" class="bd-lr-caret" onclick="BlankDash._toggleLrSection(\'measures\', event)" title="Collapse / expand Measures" aria-label="Toggle Measures"><i class="fas fa-caret-right"></i></button>' +
        '<span class="bd-lr-ico"><i class="fas fa-calculator"></i></span> Measures</span>' +
        '<button class="bd-lr-add" onclick="BlankDash._openMeasureModal(null)" title="Register a new measure"><i class="fas fa-plus"></i> New</button></div>' +
      '<div class="bd-lr-body">' +
        '<input type="text" class="bd-lr-search" placeholder="Find measures..." value="' + _esc(_measureSearch) + '" oninput="BlankDash._filterMeasures(this.value)">' +
        '<div class="bd-lr-measures">' +
          (rows || '<div class="bd-lr-empty"><i class="fas fa-calculator"></i><span>No measures yet. A measure is a named, reusable calculation that KPIs, templates and the AI builder can share — e.g. <b>SUM(revenue)</b>.</span></div>') +
        '</div></div>' +
      '</div>';
  }

  function _renderLeftRail() {
    var ws = _currentWidgets().slice().sort(function (a, b) { return (a.zIndex || 0) - (b.zIndex || 0); });
    var byGroup = {};
    ws.forEach(function (w) {
      var g = _layerGroupOf(w);
      (byGroup[g] = byGroup[g] || []).push(w);
    });
    var groupsHtml = "";
    _GROUP_ORDER.forEach(function (gname) {
      var list = byGroup[gname];
      if (!list || !list.length) return;
      var meta = _GROUP_META[gname] || { icon: "fa-rectangle-list" };
      var collapsed = !!_layerCollapsed[gname];
      var rows = list.map(function (w) {
        var sel = BlankDash._isSelected(w.id) ? " active" : "";
        var ic = _TITLES[w.type] || w.type || "widget";
        var info = _lookup(w.type) || {};
        var iconCls = info.icon || "fa-rectangle-list";
        iconCls = iconCls.indexOf("fa-") === 0 ? iconCls : "fa-rectangle-list";
        if (iconCls.indexOf(" ") !== -1) iconCls = iconCls.split(" ")[0];
        var searchable = ((w.title || "") + " " + ic).toLowerCase();
        return '<div class="bd-lr-layer' + sel + (w.hidden ? " bd-lr-hidden" : "") + '" data-search="' + _esc(searchable) + '" onclick="BlankDash._selectWidget(\'' + w.id + '\');BlankDash._scrollToWidget(\'' + w.id + '\')">' +
          '<i class="bd-lr-layer-icon fas ' + iconCls + '"></i>' +
          '<div class="bd-lr-layer-info"><span class="bd-lr-layer-name">' + _esc(w.title || ic) + '</span>' +
          '<span class="bd-lr-layer-type">' + _esc(ic) + '</span></div>' +
          '<div class="bd-lr-layer-actions">' +
            '<button type="button" class="bd-lr-act" title="' + (w.hidden ? "Show" : "Hide") + '" onclick="event.stopPropagation();BlankDash._toggleLayerHidden(\'' + w.id + '\')"><i class="fas ' + (w.hidden ? "fa-eye-slash" : "fa-eye") + '"></i></button>' +
            '<button type="button" class="bd-lr-act" title="' + (w.locked ? "Unlock" : "Lock") + '" onclick="event.stopPropagation();BlankDash._toggleLayerLocked(\'' + w.id + '\')"><i class="fas ' + (w.locked ? "fa-lock" : "fa-lock-open") + '"></i></button>' +
          '</div>' +
        '</div>';
      }).join("");
      groupsHtml += '<div class="bd-lr-group' + (collapsed ? " collapsed" : "") + '" data-group="' + _esc(gname) + '">' +
        '<button type="button" class="bd-lr-group-head" onclick="BlankDash._toggleLayerGroup(this)">' +
          '<i class="bd-lr-group-caret fas fa-caret-right"></i>' +
          '<i class="bd-lr-group-icon fas ' + meta.icon + '"></i>' +
          '<span class="bd-lr-group-name">' + _esc(gname) + '</span>' +
          '<span class="bd-lr-group-count">' + list.length + '</span>' +
        '</button>' +
        '<div class="bd-lr-group-items">' + rows + '</div>' +
      '</div>';
    });
    return '<div class="bd-leftrail' + (_leftRailCollapsed ? " bd-collapsed" : "") + '">' +
      _renderFieldsSection() +
      _renderMeasuresSection() +
      '<div class="bd-lr-section bd-layers-section' + (_lrSectionCollapsed.layers ? " collapsed" : "") + '">' +
      '<div class="bd-lr-section-head"><span class="bd-lr-title">' +
        '<button type="button" class="bd-lr-caret" onclick="BlankDash._toggleLrSection(\'layers\', event)" title="Collapse / expand Layers" aria-label="Toggle Layers"><i class="fas fa-caret-right"></i></button>' +
        '<span class="bd-lr-ico"><i class="fas fa-layer-group"></i></span> Layers</span>' +
          '<span class="bd-lr-head-actions">' +
            '<button class="bd-lr-add" onclick="BlankDash._showAddModal()" title="Add widget"><i class="fas fa-plus"></i> Add</button>' +
            '<button type="button" class="bd-lr-hide" onclick="BlankDash._toggleLeftRail()" title="Hide layers"><i class="fas fa-angles-left"></i></button>' +
          '</span></div>' +
        '<div class="bd-lr-body">' +
        '<input type="text" class="bd-lr-search" placeholder="Find widgets..." oninput="BlankDash._filterLayers(this.value)">' +
        '<div class="bd-lr-layers">' +
          (groupsHtml || '<div class="bd-lr-empty"><i class="fas fa-layer-group"></i><span>No widgets yet. Add a chart, KPI or image from the toolbar, then manage its visibility and lock state here.</span></div>') +
        '</div>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  function _renderPresentNav() {
    var dots = _dash.pages.map(function (p, i) {
      return '<button class="bd-present-dot' + (i === _dash.currentPage ? " active" : "") + '" onclick="BlankDash._switchPage(' + i + ')" title="' + _esc(p.name || ("Page " + (i + 1))) + '" aria-label="Go to page ' + (i + 1) + '"></button>';
    }).join("");
    var carous = _carousel.on
      ? '<span class="bd-carousel-status" title="Auto-play active — Space/P to stop"><span class="pulse"></span>Auto</span>'
      : '';
    return '<div class="bd-present-nav" id="bd-present-nav">' +
      carous +
      '<span class="bd-present-timer" id="bd-present-timer" title="Elapsed time in presentation">00:00</span>' +
      '<button class="bd-present-nav-btn" onclick="BlankDash._toggleCarousel()" title="Play/pause auto carousel (Space)" aria-label="Play/pause carousel"><i class="fas fa-' + (_carousel.on ? "pause" : "play") + '"></i></button>' +
      '<button class="bd-present-nav-btn" onclick="BlankDash._presentRestart()" title="Restart from page 1" aria-label="Restart presentation"><i class="fas fa-rotate-left"></i></button>' +
      '<button class="bd-present-nav-btn" onclick="BlankDash._pageStep(-1)" title="Previous page" aria-label="Previous page"><i class="fas fa-chevron-left"></i></button>' +
      '<div class="bd-present-dots">' + dots + '</div>' +
      '<button class="bd-present-nav-btn" onclick="BlankDash._pageStep(1)" title="Next page" aria-label="Next page"><i class="fas fa-chevron-right"></i></button>' +
      '<span class="bd-present-count" id="bd-present-count">' + (_dash.currentPage + 1) + ' / ' + _dash.pages.length + '</span>' +
      '<button class="bd-present-nav-btn" onclick="BlankDash._togglePresenterView()" title="Presenter view (N)" aria-label="Presenter view"><i class="fas fa-clipboard-list"></i></button>' +
      '<button class="bd-present-nav-btn" onclick="BlankDash._toggleAnnotations()" title="Annotate (A)" aria-label="Annotations"><i class="fas fa-pen"></i></button>' +
    '</div>';
  }

  function _renderStatusBar() {
    return '<div class="bd-statusbar" id="bd-statusbar">' +
      '<span class="bd-status-item bd-status-pos" id="bd-status-pos" title="Pointer position on the canvas grid">Line 0, Col 0</span>' +
      '<span class="bd-status-sep"></span>' +
      '<span class="bd-status-item" id="bd-status-sel" title="Widget selection">No selection</span>' +
      '<span class="bd-status-sep"></span>' +
      '<span class="bd-status-item" id="bd-status-size" title="Canvas size in pixels"></span>' +
      '<span class="bd-status-sep"></span>' +
      '<span class="bd-status-item" id="bd-status-page" title="Current page"></span>' +
      '<span class="bd-status-sep"></span>' +
      '<span class="bd-status-item" id="bd-status-zoom" title="Zoom level"></span>' +
      '<span class="bd-status-right" id="bd-status-save" title="Save status"></span>' +
    '</div>';
  }

  /* ── Minimap / navigator (edit mode) ──
     Power BI-style overview pinned bottom-right of the canvas wrap: every widget
     drawn as a colour-coded box plus a viewport rectangle tracking scroll.
     Click or drag to pan. Hidden by default; toggle with View → Navigator or M. */
  function _renderMinimap() {
    return '<div class="bd-minimap" id="bd-minimap">' +
      '<canvas id="bd-minimap-canvas" title="Navigator — click or drag to pan"></canvas>' +
    '</div>';
  }
  function _minimapScale() {
    var canvas = document.getElementById("bd-canvas");
    var w = canvas ? (parseFloat(canvas.style.width) || _baseCanvasW()) : _baseCanvasW();
    var h = canvas ? (parseFloat(canvas.style.height) || _baseCanvasH()) : _baseCanvasH();
    if (!w || !h) return 0.1;
    return Math.max(0.02, Math.min(176 / w, 110 / h));
  }
  function _minimapRefresh() {
    var mm = document.getElementById("bd-minimap");
    var draw = document.getElementById("bd-minimap-canvas");
    if (!mm || !draw || !_bdMinimapOn) return;
    var canvas = document.getElementById("bd-canvas");
    var wrap = document.querySelector(".bd-canvas-wrap");
    if (!canvas || !wrap) return;
    var cw = parseFloat(canvas.style.width) || _baseCanvasW();
    var ch = parseFloat(canvas.style.height) || _baseCanvasH();
    var scale = _minimapScale();
    var mw = Math.max(70, Math.round(cw * scale));
    var mh = Math.max(44, Math.round(ch * scale));
    var dpr = window.devicePixelRatio || 1;
    draw.width = Math.round(mw * dpr); draw.height = Math.round(mh * dpr);
    draw.style.width = mw + "px"; draw.style.height = mh + "px";
    mm.style.width = (mw + 10) + "px"; mm.style.height = (mh + 10) + "px";
    var ctx = draw.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var dark = document.documentElement.classList.contains("dark");
    ctx.clearRect(0, 0, mw, mh);
    ctx.fillStyle = dark ? "rgba(15,23,42,0.96)" : "rgba(255,255,255,0.96)";
    ctx.fillRect(0, 0, mw, mh);
    if (dark) {
      ctx.strokeStyle = "rgba(148,163,184,0.12)"; ctx.lineWidth = 1;
      for (var gx = 0; gx <= mw; gx += 20) { ctx.beginPath(); ctx.moveTo(gx + 0.5, 0); ctx.lineTo(gx + 0.5, mh); ctx.stroke(); }
      for (var gy = 0; gy <= mh; gy += 20) { ctx.beginPath(); ctx.moveTo(0, gy + 0.5); ctx.lineTo(mw, gy + 0.5); ctx.stroke(); }
    }
    _currentWidgets().forEach(function (w) {
      var c = _lookup(w.type) || {};
      var col = w.borderColor || c.color || "#2563eb";
      var x = Math.max(0, Math.round(w.x * scale));
      var y = Math.max(0, Math.round(w.y * scale));
      var wd = Math.max(2, Math.round(w.width * scale));
      var ht = Math.max(2, Math.round(w.height * scale));
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = col;
      ctx.fillRect(x, y, wd, ht);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = dark ? "rgba(203,213,225,0.6)" : "rgba(15,23,42,0.5)";
      ctx.lineWidth = 0.5;
      ctx.strokeRect(x + 0.5, y + 0.5, wd, ht);
    });
    var sMaxX = Math.max(1, wrap.scrollWidth - wrap.clientWidth);
    var sMaxY = Math.max(1, wrap.scrollHeight - wrap.clientHeight);
    var fracX = sMaxX > 0 ? (wrap.scrollLeft / sMaxX) : 0;
    var fracY = sMaxY > 0 ? (wrap.scrollTop / sMaxY) : 0;
    var vw = Math.max(36, mw * (wrap.clientWidth / Math.max(1, wrap.scrollWidth)));
    var vh = Math.max(22, mh * (wrap.clientHeight / Math.max(1, wrap.scrollHeight)));
    var vx = (mw - vw) * fracX, vy = (mh - vh) * fracY;
    ctx.fillStyle = "rgba(123,97,255,0.10)";
    ctx.fillRect(vx, vy, vw, vh);
    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(vx, vy, vw, vh);
  }
  var _bdMinimapRaf = false;
  function _minimapRefreshThrottled() {
    if (_bdMinimapRaf) return;
    _bdMinimapRaf = true;
    requestAnimationFrame(function () { _bdMinimapRaf = false; _minimapRefresh(); });
  }
  function _minimapPin() {
    var mm = document.getElementById("bd-minimap");
    var wrap = document.querySelector(".bd-canvas-wrap");
    var tbtn = document.getElementById("bd-minimap-toggle");
    if (tbtn) tbtn.classList.toggle("active", !!_bdMinimapOn);
    if (!mm || !wrap) return;
    mm.style.display = _dash.mode === "edit" && _bdMinimapOn ? "" : "none";
    if (mm.style.display === "none") return;
    mm.style.left = (wrap.scrollLeft + wrap.clientWidth - mm.offsetWidth - 14) + "px";
    mm.style.top = (wrap.scrollTop + wrap.clientHeight - mm.offsetHeight - 46) + "px";
  }

  function _renderFilterBar() {
    var active = Object.keys(_filters);
    if (active.length === 0) return '';
    var html = '<div class="bd-filter-bar">';
    active.forEach(function (field) {
      var val = _filters[field];
      var d = _filterChipLabel(field, val);
      var label = field === "__global__" ? "All columns" : String(field);
      html += '<span class="bd-filter-chip"' + (d.type ? ' data-type="' + d.type + '"' : "") + '>' +
        '<span class="bd-filter-chip-field">' + _esc(label) + "</span> <span class=\"bd-filter-chip-val\">" + _esc(d.display) + "</span>" +
        ' <button class="bd-filter-chip-x" data-field="' + _escAttr(field) + '" title="Remove filter">&times;</button></span>';
    });
    html += '<button class="btn btn-ghost btn-xs bd-filter-bar-clear"><i class="fas fa-times"></i> Clear all</button>';
    html += '</div>';
    return html;
  }

  /* Build a friendly chip label for a stored filter value (all filter types). */
  function _filterChipLabel(field, val) {
    if (Array.isArray(val)) {
      var shown = val.slice(0, 2);
      var more = val.length > 2 ? " +" + (val.length - 2) + " more" : "";
      return { type: val.length > 1 ? "multi" : "single", display: shown.join(", ") + more };
    }
    if (val && typeof val === "object") {
      if (val.search !== undefined && Array.isArray(val.cols)) return { type: "search", display: '"' + String(val.search) + '"' };
      if (val.start !== undefined || val.end !== undefined) return { type: "date", display: (val.start || "…") + " → " + (val.end || "…") };
      if (val.min !== undefined || val.max !== undefined) {
        var loS = val.min !== undefined ? _fmtNumShort(val.min) : "…";
        var hiS = val.max !== undefined ? _fmtNumShort(val.max) : "…";
        return { type: "range", display: loS + " – " + hiS };
      }
      return { type: "obj", display: JSON.stringify(val) };
    }
    return { type: "scalar", display: String(val) };
  }

  function _escAttr(str) {
    return String(str).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* Delegated clicks for the active-filter bar chips (safer than inline onclick
     when field names contain quotes). */
  document.addEventListener("click", function (e) {
    var el = e.target;
    if (!el || !el.closest) return;
    if (el.closest(".bd-filter-bar-clear")) { CrossFilterBus.clear(); return; }
    var x = el.closest(".bd-filter-chip-x");
    if (x && x.dataset && x.dataset.field != null) CrossFilterBus.dispatch(x.dataset.field, null, null);
  });

  function _darken(hex) {
    try {
      var r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
      r = Math.max(0, Math.round(r * 0.65));
      g = Math.max(0, Math.round(g * 0.65));
      b = Math.max(0, Math.round(b * 0.65));
      return '#' + ((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1);
    } catch (e) { return hex; }
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  function _interpColor(t) {
    var stops = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];
    t = Math.max(0, Math.min(1, t));
    var seg = t * (stops.length - 1);
    var i = Math.min(Math.floor(seg), stops.length - 2);
    var f = seg - i;
    function hx(c) { return parseInt(c.slice(1), 16); }
    function chan(a, b) { return Math.round((hx(b) >> 16 & 255) * f + (hx(a) >> 16 & 255) * (1 - f)); }
    var a = stops[i], b = stops[i + 1];
    var r = chan(a, b);
    var g = Math.round(((hx(b) >> 8 & 255) * f + (hx(a) >> 8 & 255) * (1 - f)));
    var bl = Math.round(((hx(b) & 255) * f + (hx(a) & 255) * (1 - f)));
    return 'rgb(' + r + ',' + g + ',' + bl + ')';
  }

  /* Interpolate between two hex colours (0..1) → rgb() string */
  function _mixHex(aHex, bHex, f) {
    var pa = _hexRgb(aHex), pb = _hexRgb(bHex);
    var f2 = Math.max(0, Math.min(1, f));
    function ch(i) { return Math.round(pb[i] * f2 + pa[i] * (1 - f2)); }
    return 'rgb(' + ch(0) + ',' + ch(1) + ',' + ch(2) + ')';
  }

  /* Parse a hex (#rgb/#rrggbb) or rgb() colour into [r,g,b]; fallback grey */
  function _hexRgb(c) {
    if (!c || typeof c !== "string") return [128, 128, 128];
    var m = String(c).trim();
    if (m.charAt(0) === "#") {
      var raw = m.slice(1);
      if (raw.length === 3) raw = raw.split("").map(function (x) { return x + x; }).join("");
      if (raw.length !== 6 || !/^[0-9a-fA-F]{6}$/.test(raw)) return [128, 128, 128];
      return [parseInt(raw.slice(0, 2), 16), parseInt(raw.slice(2, 4), 16), parseInt(raw.slice(4, 6), 16)];
    }
    var rm = m.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i);
    if (rm) return [Math.round(parseFloat(rm[1])), Math.round(parseFloat(rm[2])), Math.round(parseFloat(rm[3]))];
    return [128, 128, 128];
  }

  /* Perceived-luminance check — used to pick dark/light map styling */
  function _isDarkHex(c) {
    var rgb = _hexRgb(c);
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) < 120;
  }

  function fmtNum(v) {
    if (v == null || isNaN(parseFloat(v))) return String(v != null ? v : "");
    var n = parseFloat(v);
    var abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (abs >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (abs >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return n % 1 === 0 ? String(n) : n.toFixed(2);
  }

  function _adjustHex(hex, amount) {
    hex = String(hex).replace('#', '');
    if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    var r = Math.max(0, Math.min(255, parseInt(hex.substring(0, 2), 16) + amount));
    var g = Math.max(0, Math.min(255, parseInt(hex.substring(2, 4), 16) + amount));
    var b = Math.max(0, Math.min(255, parseInt(hex.substring(4, 6), 16) + amount));
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  }

  function _capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; }

  /* Per-widget colour list (Format tab) or the shared d3/palette default. */
  function _colorsOf(c) {
    if (window.StyleEngine && StyleEngine.colors && StyleEngine.colors(c)) return StyleEngine.colors(c);
    if (c && c.style && Array.isArray(c.style.colors) && c.style.colors.length) return c.style.colors;
    return (window.D3Charts && D3Charts._palette) || ["#2563eb", "#0d9488", "#b45309", "#7c3aed", "#be123c", "#0369a1", "#4d7c0f", "#64748b"];
  }

  /* Canvas-backed text measurer so d3 figure labels fit without overlapping or wrapping. */
  var _measureCtx = null;
  function _textWidth(s, fs) {
    if (!_measureCtx) _measureCtx = document.createElement("canvas").getContext("2d");
    _measureCtx.font = (typeof fs === "number" ? fs + "px" : (fs || "11px")) + " Segoe UI, Arial, sans-serif";
    return _measureCtx.measureText(String(s)).width;
  }
  function _clipText(t, maxW, fs) {
    if (t == null) return "";
    var s = String(t);
    if (!(maxW > 0)) return s;
    if (_textWidth(s, fs) <= maxW) return s;
    if (maxW < 10) return "";
    var ell = "…";
    var lo = 0, hi = s.length, best = 0;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (_textWidth(s.slice(0, mid) + ell, fs) <= maxW) { best = mid; lo = mid + 1; }
      else hi = mid - 1;
    }
    return best > 0 ? s.slice(0, best) + ell : ell;
  }

  /* "Shining gradient" dashboard setting: is it enabled and what are the two colours? */
  function _shineOn() {
    return !!(_dash && _dash.shineGradient);
  }
  function _shineGradientColors() {
    if (_dash && Array.isArray(_dash.shineColors) && _dash.shineColors.length === 2 && _hexOk(_dash.shineColors[0]) && _hexOk(_dash.shineColors[1])) return [_dash.shineColors[0], _dash.shineColors[1]];
    var t = _themeDef(_dash && _dash.theme);
    if (t) {
      var g = t.gradient || (t.primary && t.secondary ? [t.primary, t.secondary] : null);
      if (Array.isArray(g) && g.length === 2 && _hexOk(g[0]) && _hexOk(g[1])) return [g[0], g[1]];
    }
    return ["#2563eb", "#7c3aed"];
  }

  /* White-ish values are treated as "no custom background" so the shine
     gradient still applies unless the user actually picks a colour. */
  function _isDefaultBg(v) {
    if (v == null || v === "") return true;
    var s = String(v).trim().toLowerCase();
    return ["transparent", "rgba(0,0,0,0)", "rgb(255,255,255)", "rgba(255,255,255,1)", "#fff", "#ffffff", "#f8fafc", "#f9fafb", "var(--color-card-bg)"].indexOf(s) !== -1;
  }
  /* Has the user explicitly customised this widget's box (background/border/shadow)?
     Such widgets are by-passed by the shine layer so their properties stay visible. */
  function _explicitlyStyled(w) {
    if (!w) return false;
    if (w.bgColor || w.borderColor) return true;
    var cfg = w.config || {};
    if (cfg.background && !_isDefaultBg(cfg.background)) return true;
    var st = cfg.style || null;
    if (!st) return false;
    if (st.background && !_isDefaultBg(st.background)) return true;
    if (st.borderColor) return true;
    return !!st.shadow;
  }

  /* ---------- onClick helpers (tooltip / modal popups) ---------- */
  function showWidgetTooltip(ev, html) {
    var _tip = document.getElementById("ff-widget-tooltip");
    if (!_tip) {
      _tip = document.createElement("div");
      _tip.id = "ff-widget-tooltip";
      _tip.style.cssText = "position:fixed;z-index:100000;max-width:360px;padding:10px 14px;background:#1e293b;color:#e2e8f0;border-radius:8px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.35);pointer-events:none;white-space:pre-wrap;";
      document.body.appendChild(_tip);
    }
    _tip.innerHTML = html;
    _tip.style.display = "block";
    var _x = Math.min(ev.clientX + 12, window.innerWidth - 380);
    var _y = ev.clientY - 60;
    if (_y < 10) _y = ev.clientY + 16;
    _tip.style.left = _x + "px";
    _tip.style.top = _y + "px";
    function _hide() { _tip.style.display = "none"; document.removeEventListener("click", _hide, true); }
    setTimeout(function () { document.addEventListener("click", _hide, true); }, 50);
  }
  function showWidgetModal(html) {
    var _ov = document.createElement("div");
    _ov.style.cssText = "position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;";
    var _box = document.createElement("div");
    _box.style.cssText = "background:#fff;border-radius:12px;max-width:600px;width:90%;max-height:80vh;overflow:auto;padding:24px;position:relative;box-shadow:0 12px 40px rgba(0,0,0,.25);";
    _box.innerHTML = '<button onclick="this.closest(\'div[style*=fixed]\').remove()" style="position:absolute;top:10px;right:14px;background:none;border:none;font-size:20px;cursor:pointer;color:var(--color-text-muted);">&times;</button>' + html;
    _ov.appendChild(_box);
    _ov.addEventListener("click", function (e) { if (e.target === _ov) _ov.remove(); });
    document.body.appendChild(_ov);
  }

  /* Card-level style from the Format tab applied to every widget's outer box. */
  function _applyCardStyle(w, el) {
    if (!el || !w) return;
    if (_shineOn() && !_explicitlyStyled(w)) {
      var g = _shineGradientColors();
      el.style.backgroundImage = "linear-gradient(135deg," + g[0] + "," + g[1] + ")";
      el.classList.add("bd-shine");
    } else {
      if ((el.style.backgroundImage || "").indexOf("linear-gradient") !== -1) el.style.backgroundImage = "";
      el.classList.remove("bd-shine");
    }
    var _c = w.config || {};
    var pct = _c.borderShapePct;
    var legacyShape = pct == null ? (_c.borderShape || "auto") : null;
    if (legacyShape && legacyShape !== "auto") {
      var _w = el.offsetWidth || w.width || 300;
      var _h = el.offsetHeight || w.height || 200;
      var _min = Math.min(_w, _h);
      var _br;
      if (legacyShape === "square") _br = 0;
      else if (legacyShape === "rounded") _br = 12;
      else if (legacyShape === "round") _br = 24;
      else if (legacyShape === "oval") _br = 24;
      if (_br != null) el.style.borderRadius = typeof _br === "number" ? _br + "px" : _br;
      el.style.overflow = "hidden";
    } else if (pct != null && pct > 0) {
      var _r = 10 + (pct / 100) * 14;
      el.style.borderRadius = _r + "px";
      el.style.overflow = "hidden";
    } else if (typeof _c.borderRadius === "number") {
      el.style.borderRadius = _c.borderRadius + "px";
    } else if (el.style.borderRadius) {
      el.style.borderRadius = "";
    }
    /* expose shape intensity so inner content (text/value) can adapt: */
    var _shapePct = 0;
    if (_c.borderShapePct != null && _c.borderShapePct > 0) {
      _shapePct = Math.min(100, Math.max(0, _c.borderShapePct | 0));
    } else if (_c.borderShape) {
      _shapePct = { "rounded": 25, "round": 50, "oval": 100 }[_c.borderShape] || 0;
    }
    el.style.setProperty("--bd-scale", (1 - (_shapePct / 100) * 0.1).toFixed(3));
    el.style.setProperty("--bd-inset", "0px");
    el.style.setProperty("--bd-shape", _shapePct);
    /* keep the card title inside the rounded corners */
    var _tEl = el.querySelector(".ff-widget-title-inner");
    if (_tEl) {
      /* Apply titleFontSize from config */
      if (_c.titleFontSize) _tEl.style.fontSize = _c.titleFontSize + "px";
      else _tEl.style.fontSize = "";
      var _rad = 0;
      if (legacyShape && legacyShape !== "auto") {
        if (typeof _br === "number") _rad = _br;
        else { var _pb = parseFloat(String(_br)); if (!isNaN(_pb)) _rad = _pb; }
      } else if (pct != null && pct > 0) {
        _rad = 10 + (pct / 100) * 14;
      } else if (typeof _c.borderRadius === "number") {
        _rad = _c.borderRadius;
      }
      if (_rad > 0) {
        var _dy = 12;
        var _rem = _rad - _dy;
        var _ins = _rad - Math.sqrt(Math.max(0, _rad * _rad - Math.max(0, _rem) * Math.max(0, _rem)));
        _tEl.style.paddingLeft = _tEl.style.paddingRight = Math.max(6, Math.round(_ins + 6)) + "px";
        var _baseTop = parseFloat(getComputedStyle(_tEl).paddingTop) || 8;
        _tEl.style.paddingTop = Math.round(_baseTop + (_rad > 40 ? (_rad - 40) * 0.12 : 0)) + "px";
      } else {
        _tEl.style.paddingLeft = _tEl.style.paddingRight = _tEl.style.paddingTop = "";
      }
    }
    /* border style + width (general and per-side) from the config-based props panel */
    var _brdStyle = _c.borderStyle, _brdWidth = _c.borderWidth;
    if (_brdStyle) el.style.borderStyle = _brdStyle;
    if (typeof _brdWidth === "number") el.style.borderWidth = _brdWidth + "px";
    ["top", "right", "bottom", "left"].forEach(function (side) {
      var _up = side.charAt(0).toUpperCase() + side.slice(1);
      if (_c["border" + _up + "Style"]) el.style["border" + _up + "Style"] = _c["border" + _up + "Style"];
      if (typeof _c["border" + _up + "Width"] === "number") el.style["border" + _up + "Width"] = _c["border" + _up + "Width"] + "px";
      if (_c["border" + _up + "Color"]) {
        el.style["border" + _up + "Color"] = _c["border" + _up + "Color"];
        if (!_brdWidth && !_c["border" + _up + "Width"]) el.style["border" + _up + "Width"] = "1px";
      }
    });
    /* Per-side border radius */
    if (typeof _c.borderRadiusTopLeft === "number" || typeof _c.borderRadiusTopRight === "number" ||
        typeof _c.borderRadiusBottomLeft === "number" || typeof _c.borderRadiusBottomRight === "number") {
      el.style.borderRadius =
        (_c.borderRadiusTopLeft != null ? _c.borderRadiusTopLeft : 0) + "px " +
        (_c.borderRadiusTopRight != null ? _c.borderRadiusTopRight : 0) + "px " +
        (_c.borderRadiusBottomRight != null ? _c.borderRadiusBottomRight : 0) + "px " +
        (_c.borderRadiusBottomLeft != null ? _c.borderRadiusBottomLeft : 0) + "px";
    }
    /* Shadow from flat config (shadowColor/shadowOpacity/shadowBlur/shadowSpread/shadowX/shadowY) */
    if (_c.shadowColor || _c.shadowBlur || _c.shadowOpacity != null || _c.shadowX || _c.shadowY) {
      var _sCol = _c.shadowColor || "#000000";
      var _sOp = _c.shadowOpacity != null ? _c.shadowOpacity : 0.1;
      var _sBlur = _c.shadowBlur || 0;
      var _sSpread = _c.shadowSpread || 0;
      var _sX = _c.shadowX || 0;
      var _sY = _c.shadowY || 4;
      /* Convert hex to rgba if needed */
      if (_sCol.charAt(0) === "#" && (_sCol.length === 4 || _sCol.length === 7)) {
        var _r = parseInt(_sCol.slice(1, 2), 16) * 17, _g = parseInt(_sCol.slice(2, 3), 16) * 17, _b = parseInt(_sCol.slice(3, 4), 16) * 17;
        if (_sCol.length === 7) { _r = parseInt(_sCol.slice(1, 3), 16); _g = parseInt(_sCol.slice(3, 5), 16); _b = parseInt(_sCol.slice(5, 7), 16); }
        _sCol = "rgba(" + _r + "," + _g + "," + _b + "," + _sOp + ")";
      }
      el.style.boxShadow = _sX + "px " + _sY + "px " + _sBlur + "px " + _sSpread + "px " + _sCol;
    }
    /* Background from flat config (bgType/bgColor/bgColor2/bgAngle/bgOpacity) */
    if (_c.bgType && _c.bgType !== "solid" && _c.bgType !== "transparent") {
      if (_c.bgType === "linear-gradient") {
        var _a = _c.bgAngle || 135;
        var _c2 = _c.bgColor2 || "var(--color-primary)";
        el.style.background = "linear-gradient(" + _a + "deg, " + (_c.bgColor || "#ffffff") + ", " + _c2 + ")";
      } else if (_c.bgType === "radial-gradient") {
        el.style.background = "radial-gradient(circle, " + (_c.bgColor || "#ffffff") + ", " + (_c.bgColor2 || "var(--color-primary)") + ")";
      } else if (_c.bgType === "image" && _c.bgImageUrl) {
        el.style.backgroundImage = "url('" + _c.bgImageUrl + "')";
        el.style.backgroundSize = _c.bgSize || "cover";
        el.style.backgroundPosition = _c.bgPosition || "center";
        el.style.backgroundRepeat = _c.bgRepeat || "no-repeat";
      }
    } else if (_c.bgType === "transparent") {
      el.style.background = "transparent";
    } else if (_c.bgColor) {
      el.style.backgroundColor = _c.bgColor;
    }
    if (_c.bgOpacity != null && _c.bgType && _c.bgType !== "transparent" && _c.bgType !== "solid") {
      el.style.opacity = _c.bgOpacity;
    }
    /* Spacing (padding/margin) */
    if (typeof _c.paddingTop === "number") el.style.paddingTop = _c.paddingTop + "px";
    if (typeof _c.paddingRight === "number") el.style.paddingRight = _c.paddingRight + "px";
    if (typeof _c.paddingBottom === "number") el.style.paddingBottom = _c.paddingBottom + "px";
    if (typeof _c.paddingLeft === "number") el.style.paddingLeft = _c.paddingLeft + "px";
    if (typeof _c.marginTop === "number" && _c.marginTop > 0) el.style.marginTop = _c.marginTop + "px";
    if (typeof _c.marginRight === "number" && _c.marginRight > 0) el.style.marginRight = _c.marginRight + "px";
    if (typeof _c.marginBottom === "number" && _c.marginBottom > 0) el.style.marginBottom = _c.marginBottom + "px";
    if (typeof _c.marginLeft === "number" && _c.marginLeft > 0) el.style.marginLeft = _c.marginLeft + "px";
    /* Advanced effects: glassmorphism, glow, backdrop blur */
    if (_c.glassmorphism) {
      var _gOp = _c.glassOpacity != null ? _c.glassOpacity : 0.6;
      el.style.backdropFilter = "blur(" + (_c.backdropBlur || 12) + "px)";
      el.style.webkitBackdropFilter = el.style.backdropFilter;
      el.style.background = "rgba(255,255,255," + _gOp + ")";
      if (_c.glassBorder) { el.style.border = "1px solid rgba(255,255,255,0.3)"; }
    }
    if (_c.backdropBlur && !_c.glassmorphism) {
      el.style.backdropFilter = "blur(" + _c.backdropBlur + "px)";
      el.style.webkitBackdropFilter = el.style.backdropFilter;
    }
    if (_c.glow) {
      var _glCol = _c.glowColor || "var(--color-primary)";
      var _glSize = _c.glowSize || 8;
      el.style.boxShadow = (el.style.boxShadow ? el.style.boxShadow + ", " : "") + "0 0 " + _glSize + "px " + _glCol;
    }
    /* Hover effects: hoverShadow, hoverTransform, hoverOpacity — read from
       w.config at hover time so they stay live across _applyCardStyle calls */
    if (!el.__bdHoverBound) {
      el.__bdHoverBound = true;
      el.addEventListener("mouseenter", function () {
        var _oc = (w.config || {});
        var _baseShadow = el.getAttribute("data-base-shadow") || el.style.boxShadow || "";
        if (_oc.hoverShadow) {
          var _hsCol = _oc.hoverShadowColor || "var(--color-primary)";
          var _hsBlur = _oc.hoverShadowBlur || 20;
          el.style.boxShadow = (_baseShadow ? _baseShadow + ", " : "") + "0 4px " + _hsBlur + "px " + _hsCol;
        }
        if (_oc.hoverTransform && _oc.hoverTransform !== "none") {
          el.style.transform = _oc.hoverTransform;
          el.style.transition = "transform 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease";
        }
        if (_oc.hoverOpacity != null && _oc.hoverOpacity < 1) {
          el.style.opacity = String(_oc.hoverOpacity);
        }
      });
      el.addEventListener("mouseleave", function () {
        var _baseShadow = el.getAttribute("data-base-shadow") || "";
        var _baseOp = el.getAttribute("data-base-opacity") || "";
        el.style.boxShadow = _baseShadow;
        var _oc = (w.config || {});
        if (_oc.hoverTransform && _oc.hoverTransform !== "none") el.style.transform = "";
        if (_oc.hoverOpacity != null && _oc.hoverOpacity < 1) el.style.opacity = _baseOp;
      });
    }
    /* Snapshot the current shadow + opacity for the hover-leave restore */
    el.setAttribute("data-base-shadow", el.style.boxShadow || "");
    el.setAttribute("data-base-opacity", el.style.opacity || "");
    /* Title typography from flat config */
    var _ti = el.querySelector && el.querySelector(".ff-widget-title-inner");
    if (_ti) {
      if (_c.titleFontFamily) _ti.style.fontFamily = _c.titleFontFamily;
      if (_c.titleFontWeight) _ti.style.fontWeight = _c.titleFontWeight;
      if (_c.titleFontStyle) _ti.style.fontStyle = _c.titleFontStyle;
      if (_c.titleColor) _ti.style.color = _c.titleColor;
      if (_c.titleLetterSpacing != null) _ti.style.letterSpacing = _c.titleLetterSpacing + "px";
      if (_c.titleLineHeight != null) _ti.style.lineHeight = _c.titleLineHeight;
      if (_c.titleAlign) _ti.style.textAlign = _c.titleAlign;
      if (_c.titleTransform && _c.titleTransform !== "none") _ti.style.textTransform = _c.titleTransform;
      if (_c.titleDecoration && _c.titleDecoration !== "none") _ti.style.textDecoration = _c.titleDecoration;
    }
    /* onClick: store action config on element so the click handler always reads live data */
    el.setAttribute("data-click-action", _c.onClick || "none");
    el.setAttribute("data-click-target", _c.onClickTarget || "");
    el.setAttribute("data-click-newtab", _c.onClickNewTab ? "1" : "0");
    el.setAttribute("data-click-tooltip", _c.tooltipContent || "");
    el.setAttribute("data-click-modal", _c.modalContent || "");
    el.setAttribute("data-click-drillcols", _c.drillColumns || "");
    el.setAttribute("data-click-drilltgt", _c.drillTarget || "");
    el.setAttribute("data-click-widgetid", w.id || "");
    if (!el.__bdClickBound) {
      el.__bdClickBound = true;
      el.addEventListener("click", function (ev) {
        var _act = el.getAttribute("data-click-action") || "none";
        if (_act === "none") return;
        ev.stopPropagation();
        ev.preventDefault();
        var _tgt = el.getAttribute("data-click-target") || "";
        var _newTab = el.getAttribute("data-click-newtab") === "1";
        var _wId = el.getAttribute("data-click-widgetid") || "";
        switch (_act) {
          case "navigate":
            if (_tgt) { location.hash = _tgt; }
            break;
          case "url":
            if (_tgt) { window.open(_tgt, _newTab ? "_blank" : "_self"); }
            break;
          case "filter":
            var _dims = (w.config.columns || w.config.dimensions || []);
            if (_dims.length) {
              var _dimKey = Array.isArray(_dims[0]) ? _dims[0][0] : (typeof _dims[0] === "string" ? _dims[0] : "");
              if (_dimKey) {
                var _ev = new CustomEvent("blankdash:filter", { detail: { widgetId: _wId, dimension: _dimKey }, bubbles: true });
                el.dispatchEvent(_ev);
              }
            }
            break;
          case "details":
            var _detEv = new CustomEvent("blankdash:details", { detail: { widgetId: _wId, data: w.config }, bubbles: true });
            el.dispatchEvent(_detEv);
            break;
          case "drilldown":
            var _drillCols = (el.getAttribute("data-click-drillcols") || "").split(",").map(function(s){return s.trim();}).filter(Boolean);
            var _drillTgt = el.getAttribute("data-click-drilltgt") || "";
            if (_drillTgt) {
              var _drillEv = new CustomEvent("blankdash:drilldown", { detail: { widgetId: _wId, columns: _drillCols, targetDashboard: _drillTgt }, bubbles: true });
              el.dispatchEvent(_drillEv);
            }
            break;
          case "tooltip":
            var _tipHtml = el.getAttribute("data-click-tooltip");
            if (_tipHtml) {
              showWidgetTooltip(ev, _tipHtml);
            }
            break;
          case "modal":
            var _modalHtml = el.getAttribute("data-click-modal");
            if (_modalHtml) {
              showWidgetModal(_modalHtml);
            }
            break;
          case "refresh":
            var _refEv = new CustomEvent("blankdash:refresh", { detail: { widgetId: _wId }, bubbles: true });
            el.dispatchEvent(_refEv);
            if (typeof _paintWidget === "function") _paintWidget(w);
            break;
        }
      });
    }
    /* Snapshot cursor for onClick hint */
    if (_c.onClick && _c.onClick !== "none") {
      el.style.cursor = "pointer";
    } else if (el.style.cursor === "pointer") {
      el.style.cursor = "";
    }
    if (!w.config || !w.config.style) return;
    var st = w.config.style;
    /* Widget-scoped theme (stamped by the Dashboard Studio wizard via
       config.style.themeId + resolved colors): applies the style to this card
       and its content only. The page/app chrome is deliberately left alone. */
    var _tm = (st.themeId && _themeDef(st.themeId)) || null;
    if (_tm) {
      el.classList.add("bd-themed");
      var _wv = function (k) { return st[k] || _tm[k]; };
      el.style.setProperty("--w-theme-card-bg", _wv("cardBg"));
      el.style.setProperty("--w-theme-card-border", _wv("gridLine") || _wv("cardBg"));
      el.style.setProperty("--w-theme-text", _wv("text"));
      el.style.setProperty("--w-theme-text-2", _wv("textSecondary"));
      el.style.setProperty("--w-theme-grid", _wv("gridLine"));
      el.style.setProperty("--w-theme-accent", _wv("accent") || _wv("primary"));
      el.style.setProperty("--w-theme-accent-2", _wv("accent2") || (_tm.gradient && _tm.gradient[1]) || _wv("secondary") || _wv("accent"));
    } else {
      el.classList.remove("bd-themed");
    }
    if (st.background) el.style.backgroundColor = st.background;
    if (st.borderColor) { el.style.borderColor = st.borderColor; el.style.borderStyle = "solid"; el.style.borderWidth = "1px"; }
    if (typeof st.borderRadius === "number") el.style.borderRadius = st.borderRadius + "px";
    if (st.shadow) el.style.boxShadow = st.shadow;
    if (st.title) {
      var ti = el.querySelector && el.querySelector(".ff-widget-title-inner");
      if (ti) {
        if (st.title.color) ti.style.color = st.title.color;
        if (st.title.fontSize) ti.style.fontSize = st.title.fontSize + "px";
        if (st.title.align) ti.style.textAlign = st.title.align;
      }
    }
  }

  /* Chart.js options for a widget honouring its Format-tab config.style. */
  function _styleOpts(c) {
    var o = window.StyleEngine ? StyleEngine.opts(c) : { maintainAspectRatio: false, plugins: { legend: { display: c.showLegend !== false } } };
    /* D: draw instantly. Chart.js defaults to a 600ms entrance animation that
       replays on every re-render (filter change, watcher sweep, zoom) — the
       canvas is destroyed and re-created each time. Kill it so interactive
       editing stays at 60fps; Power BI updates visuals instantly too. */
    o.animation = { duration: 0 };
    return o;
  }
  /* Colour list for Chart.js option injection (returns undefined when unset). */
  function _styleColors(c) {
    return window.StyleEngine && StyleEngine.colors ? (StyleEngine.colors(c) || undefined) : undefined;
  }

  /* ── Theme helpers (built-in + user-uploaded) ── */
  function _themeKey() { return "bd_user_themes"; }
  function _userThemes() {
    try { return JSON.parse(localStorage.getItem(_themeKey()) || "[]"); } catch (e) { return []; }
  }
  function _saveUserThemes(list) {
    try { localStorage.setItem(_themeKey(), JSON.stringify(list)); } catch (e) {}
  }
  function _hexOk(s) {
    return typeof s === "string" && /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(s);
  }
  function _slug(s) {
    return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "theme";
  }
  /* Built-in theme definitions for when web/js/dist/bidashboard.bundle.js (the
     TypeScript rebuild, loaded on the standalone #bidashboard page) is not
     present — keeps blankdash's theme chrome working on the main SPA pages.
     Values mirror web/ts/charts/theme.ts. */
  var _BUILTIN_THEMES = {
    light: { name: "Light", primary: "#2563eb", secondary: "#00D4AA", accent: "#FF6B6B", palette: ["#2563eb", "#00D4AA", "#FF6B6B", "#FFB347", "#4FC3F7", "#AB47BC", "#26A69A", "#EF5350", "#FFA726", "#42A5F5"], gradient: ["#2563eb", "#00D4AA"], background: "#F5F5F5", cardBg: "#FFFFFF", text: "#333333", textSecondary: "#666666", gridLine: "#E8E8E8", axisColor: "#CCCCCC" },
    dark: { name: "Dark", primary: "#9B8AFF", secondary: "#33E0BB", accent: "#FF8A8A", palette: ["#9B8AFF", "#33E0BB", "#FF8A8A", "#FFC56B", "#6DD4FF", "#C06AE0", "#3DB8A9", "#FF6B6B", "#FFB84D", "#5EB8FF"], gradient: ["#9B8AFF", "#33E0BB"], background: "#1A1A2E", cardBg: "#252540", text: "#E0E0E0", textSecondary: "#999999", gridLine: "#3A3A5C", axisColor: "#555577" },
    midnight: { name: "Midnight", primary: "#8B5CF6", secondary: "#22D3EE", accent: "#F472B6", palette: ["#8B5CF6", "#22D3EE", "#F472B6", "#60A5FA", "#34D399", "#FBBF24", "#A78BFA", "#F87171", "#2DD4BF", "#C084FC"], gradient: ["#8B5CF6", "#22D3EE"], background: "#0B0F19", cardBg: "#141B2D", text: "#E2E8F0", textSecondary: "#7C89A3", gridLine: "#223049", axisColor: "#2E3D59", backgroundGradient: ["#0B0F19", "#1E1B4B"] },
    ocean: { name: "Ocean", primary: "#38BDF8", secondary: "#2DD4BF", accent: "#F59E0B", palette: ["#38BDF8", "#2DD4BF", "#60A5FA", "#22D3EE", "#F59E0B", "#F87171", "#34D399", "#A78BFA", "#38BDF8", "#E0F2FE"], gradient: ["#38BDF8", "#2DD4BF"], background: "#042C40", cardBg: "#0B3B54", text: "#E6F7FF", textSecondary: "#8FC3D9", gridLine: "#155E82", axisColor: "#21708F", backgroundGradient: ["#042C40", "#0E5A83"] },
    slate: { name: "Slate", primary: "#60A5FA", secondary: "#34D399", accent: "#F97316", palette: ["#60A5FA", "#34D399", "#F97316", "#818CF8", "#2DD4BF", "#FBBF24", "#F87171", "#94A3B8", "#38BDF8", "#4ADE80"], gradient: ["#60A5FA", "#34D399"], background: "#0F172A", cardBg: "#1E293B", text: "#E2E8F0", textSecondary: "#94A3B8", gridLine: "#334155", axisColor: "#475569" },
    sunset: { name: "Sunset", primary: "#FB7185", secondary: "#FFB347", accent: "#FDE047", palette: ["#FB7185", "#FFB347", "#FDE047", "#FB923C", "#F87171", "#F472B6", "#FDBA74", "#E879F9", "#FCA5A5", "#FDBA74"], gradient: ["#FB7185", "#FFB347"], background: "#2C0D22", cardBg: "#421531", text: "#FFF0F7", textSecondary: "#D9A3B8", gridLine: "#6C2746", axisColor: "#87325A", backgroundGradient: ["#2C0D22", "#7C2D12"] },
    forest: { name: "Forest", primary: "#34D399", secondary: "#A3E635", accent: "#FBBF24", palette: ["#34D399", "#A3E635", "#FBBF24", "#10B981", "#4ADE80", "#F59E0B", "#2DD4BF", "#86EFAC", "#D9F99D", "#34D399"], gradient: ["#34D399", "#A3E635"], background: "#0A1F17", cardBg: "#14352A", text: "#E9F7F0", textSecondary: "#86B5A2", gridLine: "#23463A", axisColor: "#2F5A4B", backgroundGradient: ["#0A1F17", "#14532D"] },
    grape: { name: "Grape", primary: "#A78BFA", secondary: "#F0ABFC", accent: "#FDE047", palette: ["#A78BFA", "#F0ABFC", "#818CF8", "#E879F9", "#C084FC", "#FBCFE8", "#7C3AED", "#F472B6", "#A5B4FC", "#E9D5FF"], gradient: ["#A78BFA", "#F0ABFC"], background: "#1C0F35", cardBg: "#2A1750", text: "#F1E9FF", textSecondary: "#B49BE0", gridLine: "#4A2E85", axisColor: "#5D3BA6", backgroundGradient: ["#1C0F35", "#4C1D95"] },
    candy: { name: "Candy", primary: "#F472B6", secondary: "#22D3EE", accent: "#FBBF24", palette: ["#F472B6", "#22D3EE", "#FBBF24", "#FB7185", "#A78BFA", "#34D399", "#FCA5A5", "#7DD3FC", "#F9A8D4", "#FDE68A"], gradient: ["#F472B6", "#22D3EE"], background: "#FFF7F9", cardBg: "#FFFFFF", text: "#4A3A46", textSecondary: "#A78B9E", gridLine: "#F0D5E2", axisColor: "#E5C4D2" },
    terminal: { name: "Terminal", primary: "#00FF66", secondary: "#00D4AA", accent: "#FF5555", palette: ["#00FF66", "#00D4AA", "#FF5555", "#FFB86C", "#8BE9FD", "#FF79C6", "#50FA7B", "#F1FA8C", "#BD93F9", "#6272A4"], gradient: ["#00FF66", "#00D4AA"], background: "#0C0C0C", cardBg: "#151515", text: "#33FF33", textSecondary: "#2E8B57", gridLine: "#262626", axisColor: "#333333" },
    solarized: { name: "Solarized", primary: "#268BD2", secondary: "#2AA198", accent: "#DC322F", palette: ["#268BD2", "#2AA198", "#DC322F", "#B58900", "#859900", "#6C71C4", "#CB4B16", "#D33682", "#2AA198", "#93A1A1"], gradient: ["#268BD2", "#2AA198"], background: "#002B36", cardBg: "#073642", text: "#93A1A1", textSecondary: "#586E75", gridLine: "#20424B", axisColor: "#40555D" },
    aurora: { name: "Aurora", primary: "#6366F1", secondary: "#10B981", accent: "#F59E0B", palette: ["#6366F1", "#10B981", "#F59E0B", "#22D3EE", "#A78BFA", "#34D399", "#FB7185", "#FDE047", "#60A5FA", "#EC4899"], gradient: ["#6366F1", "#10B981"], background: "#0D1030", cardBg: "#1B2054", text: "#E0E7FF", textSecondary: "#8FA3BF", gridLine: "#30387A", axisColor: "#3E4A99", backgroundGradient: ["#0D1030", "#312E81"] },
    coffee: { name: "Coffee", primary: "#D9A05B", secondary: "#B87333", accent: "#F97316", palette: ["#D9A05B", "#B87333", "#F97316", "#E0A96D", "#A0522D", "#F59E0B", "#C0823F", "#E7B477", "#8B5A2B", "#F97316"], gradient: ["#D9A05B", "#B87333"], background: "#221410", cardBg: "#3A2217", text: "#F3E5D8", textSecondary: "#C19A82", gridLine: "#5A3323", axisColor: "#6E3F2C" },
    rose: { name: "Rose", primary: "#F43F5E", secondary: "#FBBF24", accent: "#A855F7", palette: ["#F43F5E", "#FBBF24", "#A855F7", "#FB7185", "#F472B6", "#E879F9", "#FDA4AF", "#F9A8D4", "#F87171", "#C084FC"], gradient: ["#F43F5E", "#A855F7"], background: "#2A0B1B", cardBg: "#3F1530", text: "#FFE4EF", textSecondary: "#D18BAA", gridLine: "#6A2547", axisColor: "#85325B", backgroundGradient: ["#2A0B1B", "#9F1239"] },
    neon: { name: "Neon", primary: "#00FFFF", secondary: "#FF00FF", accent: "#39FF14", palette: ["#00FFFF", "#FF00FF", "#39FF14", "#FFFF00", "#FF6B00", "#00FF9D", "#FF3366", "#7DF9FF", "#B026FF", "#FFE600"], gradient: ["#00FFFF", "#FF00FF"], background: "#050510", cardBg: "#0E0E2A", text: "#E0FFFF", textSecondary: "#7DF9FF", gridLine: "#1E1E4A", axisColor: "#2F2F70", backgroundGradient: ["#050510", "#1E1B4B"] },
    emerald: { name: "Emerald", primary: "#10B981", secondary: "#34D399", accent: "#FBBF24", palette: ["#10B981", "#34D399", "#FBBF24", "#059669", "#6EE7B7", "#F59E0B", "#2DD4BF", "#A7F3D0", "#D1FAE5", "#10B981"], gradient: ["#10B981", "#34D399"], background: "#06281E", cardBg: "#0B3B2E", text: "#E7F6F0", textSecondary: "#7FB8A3", gridLine: "#1B4A3A", axisColor: "#25604C", backgroundGradient: ["#06281E", "#064E3B"] },
    crimson: { name: "Crimson", primary: "#F43F5E", secondary: "#FB7185", accent: "#FBBF24", palette: ["#F43F5E", "#FB7185", "#FBBF24", "#E11D48", "#FDA4AF", "#F97316", "#F87171", "#FECDD3", "#FCA5A5", "#F43F5E"], gradient: ["#F43F5E", "#FB7185"], background: "#2B0A12", cardBg: "#3F1420", text: "#FFE9EE", textSecondary: "#D18A9B", gridLine: "#5C2433", axisColor: "#70303F", backgroundGradient: ["#2B0A12", "#881337"] },
    gold: { name: "Gold", primary: "#F59E0B", secondary: "#FBBF24", accent: "#F43F5E", palette: ["#F59E0B", "#FBBF24", "#F43F5E", "#D97706", "#FDE68A", "#FB923C", "#FCD34D", "#FEF3C7", "#FCA5A5", "#F59E0B"], gradient: ["#F59E0B", "#FBBF24"], background: "#2A1A05", cardBg: "#3E2A0E", text: "#FFF7E6", textSecondary: "#D4B483", gridLine: "#5C4520", axisColor: "#70562A", backgroundGradient: ["#2A1A05", "#78350F"] },
    ice: { name: "Ice", primary: "#0EA5E9", secondary: "#22D3EE", accent: "#6366F1", palette: ["#0EA5E9", "#22D3EE", "#6366F1", "#38BDF8", "#818CF8", "#06B6D4", "#60A5FA", "#A5F3FC", "#C7D2FE", "#0EA5E9"], gradient: ["#0EA5E9", "#22D3EE"], background: "#F0F9FF", cardBg: "#FFFFFF", text: "#0C4A6E", textSecondary: "#64748B", gridLine: "#E0F2FE", axisColor: "#BAE6FD" },
    graphite: { name: "Graphite", primary: "#94A3B8", secondary: "#64748B", accent: "#38BDF8", palette: ["#94A3B8", "#64748B", "#38BDF8", "#CBD5E1", "#475569", "#22D3EE", "#E2E8F0", "#818CF8", "#64748B", "#94A3B8"], gradient: ["#94A3B8", "#64748B"], background: "#0F1115", cardBg: "#1A1D23", text: "#E5E7EB", textSecondary: "#8B919C", gridLine: "#2A2E36", axisColor: "#3A3F49", backgroundGradient: ["#0F1115", "#1F2937"] },
    lavender: { name: "Lavender", primary: "#8B5CF6", secondary: "#A78BFA", accent: "#EC4899", palette: ["#8B5CF6", "#A78BFA", "#EC4899", "#6D28D9", "#C4B5FD", "#F472B6", "#818CF8", "#EDE9FE", "#FBCFE8", "#8B5CF6"], gradient: ["#8B5CF6", "#A78BFA"], background: "#FAF7FF", cardBg: "#FFFFFF", text: "#3B2F63", textSecondary: "#7C6FA8", gridLine: "#EDE9FE", axisColor: "#DDD6FE" },
    peach: { name: "Peach", primary: "#F97316", secondary: "#FB923C", accent: "#F43F5E", palette: ["#F97316", "#FB923C", "#F43F5E", "#EA580C", "#FDBA74", "#F87171", "#FBBF24", "#FFEDD5", "#FECACA", "#F97316"], gradient: ["#F97316", "#FB923C"], background: "#FFF7ED", cardBg: "#FFFFFF", text: "#431407", textSecondary: "#9A6B4F", gridLine: "#FFEDD5", axisColor: "#FED7AA" },
    cyber: { name: "Cyber", primary: "#22D3EE", secondary: "#A855F7", accent: "#F0ABFC", palette: ["#22D3EE", "#A855F7", "#F0ABFC", "#06B6D4", "#C084FC", "#34D399", "#F472B6", "#67E8F9", "#E9D5FF", "#22D3EE"], gradient: ["#22D3EE", "#A855F7"], background: "#0A0A1F", cardBg: "#141433", text: "#E0F2FE", textSecondary: "#8B8BC9", gridLine: "#26264F", axisColor: "#34346B", backgroundGradient: ["#0A0A1F", "#2E1065"] },
    paper: { name: "Paper", primary: "#334155", secondary: "#64748B", accent: "#0EA5E9", palette: ["#334155", "#64748B", "#0EA5E9", "#94A3B8", "#475569", "#38BDF8", "#CBD5E1", "#E2E8F0", "#64748B", "#334155"], gradient: ["#334155", "#64748B"], background: "#FAFAFA", cardBg: "#FFFFFF", text: "#1E293B", textSecondary: "#64748B", gridLine: "#E5E7EB", axisColor: "#D1D5DB" },
    mint: { name: "Mint", primary: "#10B981", secondary: "#34D399", accent: "#0EA5E9", palette: ["#10B981", "#34D399", "#0EA5E9", "#059669", "#6EE7B7", "#38BDF8", "#F59E0B", "#D1FAE5", "#A7F3D0", "#10B981"], gradient: ["#10B981", "#34D399"], background: "#F0FDF9", cardBg: "#FFFFFF", text: "#134E4A", textSecondary: "#5B8A82", gridLine: "#CCFBF1", axisColor: "#99F6E4" }
  };
  function _themeDef(id) {
    if (!id || id === "auto") return null;
    var users = _userThemes();
    for (var i = 0; i < users.length; i++) if (users[i].id === id) return users[i];
    var built = window.BIDashboardThemes;
    if (built && built[id]) return built[id];
    return _BUILTIN_THEMES[id] || null;
  }
  function _themeLabel(id) {
    var t = _themeDef(id);
    return t ? (t.name || id) : "Auto";
  }
  /* Resolve the current dashboard theme into an AIBuild-friendly option bag so
     AI-generated header/insight/chart widgets inherit the chosen theme. */
  function _aiThemeOpts() {
    var t = _themeDef(_dash && _dash.theme);
    if (!t) return { accent: "#2563eb", accent2: "#1e3a8a", palette: null, theme: null };
    var cfg = {
      accent: t.accent || t.primary || "#2563eb",
      accent2: (t.gradient && t.gradient[1]) || t.secondary || t.accent2 || "#1e293b",
      text: t.text, textSecondary: t.textSecondary, cardBg: t.cardBg,
      gridLine: t.gridLine, palette: Array.isArray(t.palette) ? t.palette.slice() : null
    };
    return { accent: cfg.accent, accent2: cfg.accent2, palette: cfg.palette, theme: cfg };
  }
  /* Resolve the theme for an AI-built widget from its own stamped config.style
     (themeId + resolved colors), falling back to the dashboard theme. This keeps
     the page/app chrome on the global look while the widget keeps its wizard
     theme — and lets the theme picker re-stamp widgets by updating config.style. */
  function _aiNavTheme(w) {
    var st = (w && w.config && w.config.style && typeof w.config.style === "object") ? w.config.style : {};
    var t = (st.themeId && _themeDef(st.themeId)) || _themeDef(_dash && _dash.theme);
    if (!t) return _aiThemeOpts();
    var acc = st.accent || t.accent || t.primary || "#2563eb";
    var acc2 = st.accent2 || (t.gradient && t.gradient[1]) || t.secondary || t.accent2 || "#1e293b";
    var cfg = {
      accent: acc, accent2: acc2,
      text: st.text || t.text, textSecondary: st.textSecondary || t.textSecondary,
      cardBg: st.cardBg || t.cardBg, gridLine: st.gridLine || t.gridLine,
      palette: Array.isArray(t.palette) ? t.palette.slice() : null
    };
    return { accent: acc, accent2: acc2, palette: cfg.palette, theme: cfg };
  }
  /* Re-stamp every wizard-stamped widget (config.style.themeId set) with the
     newly chosen dashboard theme so the theme picker actually restyles widgets
     that were baked with a fixed theme at AI-build time. */
  function _restampWidgets(name) {
    var t = _themeDef(name);
    if (!t) return;
    var pages = _dash && _dash.pages;
    if (!pages) return;
    pages.forEach(function (pg) {
      (pg.widgets || []).forEach(function (w) {
        var st = w && w.config && w.config.style;
        if (!st || !st.themeId) return;
        st.themeId = name;
        st.cardBg = t.cardBg;
        st.text = t.text;
        st.textSecondary = t.textSecondary;
        st.gridLine = t.gridLine;
        st.accent = t.accent || t.primary;
        st.accent2 = (t.gradient && t.gradient[1]) || t.secondary || t.accent2;
      });
    });
  }
  function _normTheme(raw) {
    if (!raw || typeof raw !== "object") throw new Error("must be a JSON object");
    var colorKeys = ["cardBg", "text", "textSecondary", "gridLine", "axisColor", "primary", "secondary", "accent"];
    var name = String(raw.name || "").trim();
    if (!name) throw new Error("missing required field \"name\"");
    var t = { name: name };
    for (var i = 0; i < colorKeys.length; i++) {
      var k = colorKeys[i];
      if (!_hexOk(raw[k])) throw new Error("invalid hex color for \"" + k + "\"");
      t[k] = raw[k];
    }
    var bg = raw.background;
    if (Array.isArray(bg)) {
      if (bg.length !== 2 || !_hexOk(bg[0]) || !_hexOk(bg[1])) throw new Error("\"background\" gradient must be [c1, c2] hex colors");
      t.background = bg[0];
      t.backgroundGradient = [bg[0], bg[1]];
    } else if (_hexOk(bg)) {
      t.background = bg;
    } else {
      throw new Error("\"background\" must be a hex color or [c1, c2] gradient");
    }
    if (!Array.isArray(raw.palette) || !raw.palette.length) throw new Error("\"palette\" must be a non-empty array of hex colors");
    for (var j = 0; j < raw.palette.length; j++) if (!_hexOk(raw.palette[j])) throw new Error("palette[" + j + "] is not a valid hex color");
    t.palette = raw.palette;
    if (raw.gradient !== undefined) {
      if (!Array.isArray(raw.gradient) || raw.gradient.length !== 2 || !_hexOk(raw.gradient[0]) || !_hexOk(raw.gradient[1])) throw new Error("\"gradient\" must be [c1, c2] hex colors");
      t.gradient = [raw.gradient[0], raw.gradient[1]];
    } else {
      t.gradient = [raw.primary, raw.secondary];
    }
    return t;
  }
  function _hexRgb(hex) {
    var c = String(hex).replace("#", "");
    if (c.length === 3) c = c.split("").map(function (ch) { return ch + ch; }).join("");
    return [
      parseInt(c.substring(0, 2), 16) || 0,
      parseInt(c.substring(2, 4), 16) || 0,
      parseInt(c.substring(4, 6), 16) || 0
    ];
  }
  function _mixHex(a, b, r) {
    var pa = _hexRgb(a), pb = _hexRgb(b);
    return "rgb(" + Math.round(pa[0] + (pb[0] - pa[0]) * r) + "," + Math.round(pa[1] + (pb[1] - pa[1]) * r) + "," + Math.round(pa[2] + (pb[2] - pa[2]) * r) + ")";
  }
  function _normThemeMode(m) {
    if (m === "chrome") return "full";
    if (m === "palette") return "widgets";
    return m || "full";
  }
  function _renderApplyMode() {
    var mode = _normThemeMode(_dash.themeMode);
    var opts = [["full", "Full"], ["widgets", "Widgets only"], ["background", "Background only"]];
    var html = '<div class="bd-theme-modes">';
    for (var i = 0; i < opts.length; i++) {
      html += '<button data-mode="' + opts[i][0] + '" class="' + (mode === opts[i][0] ? "active" : "") + '">' + opts[i][1] + '</button>';
    }
    return html + '</div>';
  }
  function _renderThemeSwatches() {
    var html = '';
    var current = _dash.theme || "auto";
    var order = ["auto", "light", "dark", "midnight", "ocean", "sunset", "forest", "grape", "slate", "candy", "terminal", "solarized", "aurora", "coffee", "rose", "neon", "emerald", "crimson", "gold", "ice", "graphite", "lavender", "peach", "cyber", "paper", "mint"];
    for (var i = 0; i < order.length; i++) {
      html += _swatchRow(order[i], _themeLabel(order[i]), _themeDef(order[i]), current === order[i], false);
    }
    var users = _userThemes();
    if (users.length) {
      html += '<div class="bd-theme-sect">Custom</div>';
      for (var j = 0; j < users.length; j++) {
        html += _swatchRow(users[j].id, users[j].name || "Custom", users[j], current === users[j].id, true);
      }
    }
    return html;
  }
  function _swatchRow(id, label, t, active, custom) {
    var bg = "transparent", border = "var(--color-card-border)";
    if (t) {
      bg = t.backgroundGradient && t.backgroundGradient.length === 2
        ? "linear-gradient(135deg," + t.backgroundGradient[0] + "," + t.backgroundGradient[1] + ")"
        : (t.background || "transparent");
      border = t.gridLine || "transparent";
    }
    var card = t
      ? '<span class="bd-theme-cardbg" style="background:' + t.cardBg + ';"></span>'
      : '<span class="bd-theme-cardbg bd-theme-cardbg-auto"></span>';
    var dot = t
      ? '<span class="bd-theme-dot" style="background:' + t.primary + ';"></span>'
      : '<span class="bd-theme-dot bd-theme-dot-auto"></span>';
    return '<div class="bd-theme-swatch' + (active ? " active" : "") + '" data-theme="' + id + '" title="' + _esc(label) + '">' +
      '<span class="bd-theme-chip" style="background:' + bg + ';border-color:' + border + ';">' + card + dot + '</span>' +
      '<span class="bd-theme-name">' + _esc(label) + '</span>' +
      (custom ? '<button class="bd-theme-del" data-remove="' + _esc(id) + '" title="Delete theme"><i class="fas fa-times"></i></button>' : '') +
      '</div>';
  }

  BlankDash._promptRenamePage = function (idx) {
    var name = prompt("Page name:", _dash.pages[idx].name);
    if (name !== null) { BlankDash._renamePage(idx, name); BlankDash.render(); }
  };

  /* ── Register custom D3 renderers ── */
  function _registerCustomRenderers() {
    if (!window.d3) return;

    /* Polar Area */
    DashboardRenderer.register("polar", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!data.length) return;
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var cx = W / 2, cy = H / 2, R = Math.min(cx, cy) - 40;
      var maxVal = Math.max.apply(null, data) || 1;
      var colors = (window.D3Charts && D3Charts._palette) || ["#2563eb","#0d9488","#b45309","#7c3aed","#be123c","#0369a1","#4d7c0f","#64748b"];
      var arc = d3.arc().innerRadius(0);
      var pie = d3.pie().value(function(d) { return d; }).sort(null);
      var g = svg.append("g").attr("transform", "translate(" + cx + "," + cy + ")");
      var arcs = g.selectAll(".arc").data(pie(data)).enter().append("g").attr("class", "arc");
      arcs.append("path").attr("d", function(d) {
        var r = Math.sqrt(d.data / maxVal) * R;
        return arc.outerRadius(r)(d);
      }).attr("fill", function(d, i) { return colors[i % colors.length]; }).attr("opacity", 0.85)
        .on("mouseenter", function(ev, d) {
          d3.select(this).attr("opacity", 1);
          if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, labels[d.index], d.data, ev);
          Tooltip.show(Tooltip.multi([[labels[d.index] || "", d.data.toLocaleString()]]), ev);
        })
        .on("mousemove", function(ev) { Tooltip.move(ev); })
        .on("mouseleave", function() { d3.select(this).attr("opacity", 0.85); Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); })
        .on("click", function(ev, d) {
          if (window.BlankDash && BlankDash._chartClick) BlankDash._chartClick(widget, labels[d.index], ev);
        });
      g.selectAll(".plabel").data(pie(data)).enter().append("text").attr("text-anchor", "middle")
        .attr("transform", function(d) {
          var r = Math.sqrt(d.data / maxVal) * R * 0.65;
          var a = (d.startAngle + d.endAngle) / 2 - Math.PI / 2;
          return "translate(" + (r * Math.cos(a)) + "," + (r * Math.sin(a)) + ")";
        })
        .each(function(d) {
          var v = labels[d.index] || "";
          var rr = Math.sqrt(d.data / maxVal) * R * 0.65;
          var chord = 2 * rr * Math.sin((d.endAngle - d.startAngle) / 2);
          var txt = chord < 14 ? "" : _clipText(v, Math.min(chord, 110), 10);
          var st = d3.select(this);
          st.text(txt);
          if (v && v !== txt) st.append("title").text(v);
        })
        .attr("fill", "#fff").attr("font-size", "10px").attr("font-weight", "600");
    });

    /* Box Plot */
    DashboardRenderer.register("boxplot", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!data.length) return;
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)", axisColor: "rgba(255,255,255,0.2)" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var margin = { top: 20, right: 20, bottom: 48, left: 52 };
      /* Guard degenerate geometry: a tiny container (collapsed props edits,
         half-rendered cells) must not produce negative SVG attributes. */
      var w = Math.max(40, W - margin.left - margin.right);
      var h = Math.max(40, H - margin.top - margin.bottom);
      if (W < margin.left + margin.right + 40 || H < margin.top + margin.bottom + 40) {
        W = Math.max(W, margin.left + margin.right + 60);
        H = Math.max(H, margin.top + margin.bottom + 60);
      }
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + margin.left + "," + margin.top + ")");
      var sorted = data.slice().map(Number).filter(function(v){ return isFinite(v); }).sort(function(a, b) { return a - b; });
      if (!sorted.length) return;
      var q1 = sorted[Math.floor(sorted.length * 0.25)] || 0;
      var med = sorted[Math.floor(sorted.length * 0.5)] || 0;
      var q3 = sorted[Math.floor(sorted.length * 0.75)] || 0;
      var iqr = q3 - q1;
      var lo = Math.max(sorted[0] || 0, q1 - 1.5 * iqr);
      var hi = Math.min(sorted[sorted.length - 1] || 0, q3 + 1.5 * iqr);
      var y = d3.scaleLinear().domain([lo * 0.9, hi * 1.1 || 10]).range([h, 0]).nice();
      g.append("g").call(d3.axisLeft(y).ticks(5).tickFormat(d3.format("~s")))
        .call(function(g2) { g2.select(".domain").attr("stroke", theme.axisColor); g2.selectAll(".tick text").attr("fill", theme.textSecondary).attr("font-size", "11px"); });
      var cx = w / 2, bw = Math.max(8, Math.min(w * 0.4, 60));
      g.append("line").attr("x1", cx - bw * 0.3).attr("x2", cx + bw * 0.3).attr("y1", y(lo)).attr("y2", y(lo)).attr("stroke", theme.textSecondary).attr("stroke-width", 2);
      g.append("line").attr("x1", cx).attr("x2", cx).attr("y1", y(lo)).attr("y2", y(q1)).attr("stroke", theme.textSecondary).attr("stroke-width", 1.5).attr("stroke-dasharray", "3,3");
      g.append("rect").attr("x", cx - bw / 2).attr("y", y(q3)).attr("width", bw).attr("height", y(q1) - y(q3)).attr("fill", "var(--chart-1)").attr("opacity", 0.8).attr("rx", 4);
      g.append("line").attr("x1", cx - bw / 2).attr("x2", cx + bw / 2).attr("y1", y(med)).attr("y2", y(med)).attr("stroke", "#fff").attr("stroke-width", 2.5);
      g.append("line").attr("x1", cx).attr("x2", cx).attr("y1", y(q3)).attr("y2", y(hi)).attr("stroke", theme.textSecondary).attr("stroke-width", 1.5).attr("stroke-dasharray", "3,3");
      g.append("line").attr("x1", cx - bw * 0.3).attr("x2", cx + bw * 0.3).attr("y1", y(hi)).attr("y2", y(hi)).attr("stroke", theme.textSecondary).attr("stroke-width", 2);
      var outliers = sorted.filter(function(v) { return v < lo || v > hi; });
      g.selectAll(".outlier").data(outliers).enter().append("circle").attr("cx", cx).attr("cy", function(d) { return y(d); }).attr("r", 3).attr("fill", "var(--color-danger)").attr("opacity", 0.7);
      g.append("text").attr("x", cx).attr("y", h + 36).attr("text-anchor", "middle").attr("fill", theme.textSecondary).attr("font-size", "11px").text("Q1=" + q1.toFixed(1) + " Med=" + med.toFixed(1) + " Q3=" + q3.toFixed(1));
    });
    /* Histogram */
    DashboardRenderer.register("histogram", function (widget, container) {
      var c = widget.config || {};
      var data = c.values || c.data || [];
      if (!data.length) return;
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)", axisColor: "rgba(255,255,255,0.2)" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var margin = { top: 20, right: 20, bottom: 40, left: 52 };
      var w = W - margin.left - margin.right, h = H - margin.top - margin.bottom;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + margin.left + "," + margin.top + ")");
      var bins = d3.bin().thresholds(12)(data);
      var x = d3.scaleLinear().domain([bins[0].x0, bins[bins.length - 1].x1]).range([0, w]);
      var y = d3.scaleLinear().domain([0, d3.max(bins, function(b) { return b.length; }) || 1]).range([h, 0]).nice();
      g.append("g").attr("transform", "translate(0," + h + ")").call(d3.axisBottom(x).ticks(8)).call(function(g2) { g2.select(".domain").attr("stroke", theme.axisColor); g2.selectAll(".tick text").attr("fill", theme.textSecondary).attr("font-size", "10px"); });
      g.append("g").call(d3.axisLeft(y).ticks(5)).call(function(g2) { g2.select(".domain").attr("stroke", theme.axisColor); g2.selectAll(".tick text").attr("fill", theme.textSecondary).attr("font-size", "10px"); });
      g.selectAll(".bar").data(bins).enter().append("rect").attr("x", function(b) { return x(b.x0) + 1; }).attr("y", function(b) { return y(b.length); })
        .attr("width", function(b) { return Math.max(0, x(b.x1) - x(b.x0) - 2); }).attr("height", function(b) { return h - y(b.length); })
        .attr("fill", "var(--chart-1)").attr("opacity", 0.8).attr("rx", 2)
        .on("mouseenter", function(ev, b) { d3.select(this).attr("opacity", 1); Tooltip.show(Tooltip.multi([["Range", b.x0.toFixed(1) + " - " + b.x1.toFixed(1)], ["Count", String(b.length)]]), ev); })
        .on("mousemove", function(ev) { Tooltip.move(ev); })
        .on("mouseleave", function() { d3.select(this).attr("opacity", 0.8); Tooltip.hide(); });
    });

    /* Bullet Chart */
    DashboardRenderer.register("bullet", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [], target = c.target || [];
      if (!data.length) return;
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var margin = { top: 20, right: 30, bottom: 24, left: 100 };
      var w = W - margin.left - margin.right, h = H - margin.top - margin.bottom;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + margin.left + "," + margin.top + ")");
      var maxVal = Math.max(d3.max(data) || 1, d3.max(target) || 1) * 1.2;
      var y = d3.scaleBand().domain(labels).range([0, h]).padding(0.35);
      var x = d3.scaleLinear().domain([0, maxVal]).range([0, w]);
      g.append("g").call(d3.axisLeft(y).tickFormat(function(d) { return _clipText(d, 84, 11); })).call(function(g2) { g2.select(".domain").remove(); g2.selectAll(".tick text").attr("fill", theme.textSecondary).attr("font-size", "11px"); });
      g.selectAll(".bar-bg").data(data).enter().append("rect").attr("x", 0).attr("y", function(_, i) { return y(labels[i]); }).attr("width", w).attr("height", y.bandwidth()).attr("fill", "rgba(255,255,255,0.05)").attr("rx", 4);
      g.selectAll(".bar-val").data(data).enter().append("rect").attr("x", 0).attr("y", function(_, i) { return y(labels[i]); }).attr("width", function(d) { return x(d); }).attr("height", y.bandwidth()).attr("fill", "var(--chart-1)").attr("opacity", 0.85).attr("rx", 4)
        .on("mouseenter", function(ev, d) { d3.select(this).attr("opacity", 1); var i = data.indexOf(d); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, labels[i], d, ev); Tooltip.show(Tooltip.multi([[labels[i], d.toLocaleString()], ["Target", (target[i] || 0).toLocaleString()]]), ev); })
        .on("mousemove", function(ev) { Tooltip.move(ev); })
        .on("mouseleave", function() { d3.select(this).attr("opacity", 0.85); Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); })
        .on("click", function(ev, d) { var i = data.indexOf(d); if (window.BlankDash && BlankDash._chartClick) BlankDash._chartClick(widget, labels[i], ev); });
      g.selectAll(".target-mark").data(target).enter().append("line").attr("x1", function(d) { return x(d); }).attr("x2", function(d) { return x(d); })
        .attr("y", function(_, i) { return y(labels[i]) - 2; }).attr("height", y.bandwidth() + 4)
        .attr("stroke", "var(--color-danger)").attr("stroke-width", 2.5).attr("stroke-linecap", "round");
    });

    /* Pyramid */
    DashboardRenderer.register("pyramid", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!data.length) return;
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var maxVal = Math.max.apply(null, data) || 1;
      var barH = Math.min((H - 40) / data.length - 4, 36);
      var colors = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)", "var(--chart-7)", "var(--chart-8)"];
      var g = svg.append("g").attr("transform", "translate(" + W / 2 + ",20)");
      data.forEach(function (val, i) {
        var bw = (val / maxVal) * (W * 0.8);
        var y = i * (barH + 4);
        g.append("rect").attr("x", -bw / 2).attr("y", y).attr("width", bw).attr("height", barH).attr("fill", colors[i % colors.length]).attr("rx", 4).attr("opacity", 0.85)
          .on("mouseenter", function(ev) { d3.select(this).attr("opacity", 1); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, labels[i], val, ev); Tooltip.show(Tooltip.multi([[labels[i] || "", val.toLocaleString()]]), ev); })
          .on("mousemove", function(ev) { Tooltip.move(ev); })
          .on("mouseleave", function() { d3.select(this).attr("opacity", 0.85); Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); })
          .on("click", function(ev) { if (window.BlankDash && BlankDash._chartClick) BlankDash._chartClick(widget, labels[i], ev); });
        g.append("text").attr("x", 0).attr("y", y + barH / 2 + 4).attr("text-anchor", "middle").attr("fill", "#fff").attr("font-size", "11px").attr("font-weight", "600").text(labels[i] || "");
      });
    });

    /* Sunburst */
    DashboardRenderer.register("sunburst", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!data.length) return;
      container.innerHTML = "";
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var colors = ["var(--chart-1)","var(--chart-2)","var(--chart-3)","var(--chart-4)","var(--chart-5)","var(--chart-6)","var(--chart-7)","var(--chart-8)"];
      var sSum = 0;
      for (var di = 0; di < data.length; di++) sSum += Math.abs(+data[di] || 0);
      var total = sSum || 1;
      function fmtVal(v) {
        if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
        if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
        if (v >= 1e3) return (v / 1e3).toFixed(2) + "K";
        return String(Math.round(v));
      }
      var wrap = document.createElement("div");
      wrap.style.cssText = "display:flex;flex-direction:row;align-items:center;height:100%;width:100%;";
      var svgBox = document.createElement("div");
      svgBox.style.cssText = "flex:1;min-width:0;height:100%;position:relative;";
      var legBox = document.createElement("div");
      legBox.style.cssText = "width:168px;flex:none;padding:6px 10px;font-size:11px;line-height:1.35;color:var(--color-content-text);overflow:hidden;";
      wrap.appendChild(svgBox); wrap.appendChild(legBox); container.appendChild(wrap);
      var SW = svgBox.clientWidth || Math.round(W * 0.7), SH = svgBox.clientHeight || H;
      var svg = d3.select(svgBox).append("svg").attr("width", SW).attr("height", SH);
      var cx = SW / 2, cy = SH / 2, R = Math.min(cx, cy) - 18;
      var root = d3.hierarchy({ name: "root", children: labels.map(function(l, i) { return { name: l, value: data[i] }; }) }).sum(function(d) { return d.value; });
      d3.partition().size([2 * Math.PI, R])(root);
      var arc = d3.arc().startAngle(function(d) { return d.x0; }).endAngle(function(d) { return d.x1; }).innerRadius(function(d) { return Math.max(d.y0 * 0.55, R * 0.34); }).outerRadius(function(d) { return d.y1 * 0.72 - 1; });
      var g = svg.append("g").attr("transform", "translate(" + cx + "," + cy + ")");
      var leaves = root.descendants().filter(function(d) { return d.depth > 0; });
      g.selectAll("path").data(leaves).enter().append("path").attr("d", arc).attr("fill", function(d, i) { return colors[i % colors.length]; }).attr("opacity", 0.88)
        .on("mouseenter", function(ev, d) { d3.select(this).attr("opacity", 1); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, d.data.name, d.value, ev); Tooltip.show(Tooltip.multi([[d.data.name, d.value.toLocaleString()]]), ev); })
        .on("mousemove", function(ev) { Tooltip.move(ev); })
        .on("mouseleave", function() { d3.select(this).attr("opacity", 0.88); Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); })
        .on("click", function(ev, d) { if (window.BlankDash && BlankDash._chartClick) BlankDash._chartClick(widget, d.data.name, ev); });
      g.selectAll("text.arclabel").data(leaves.filter(function(d) { return (d.x1 - d.x0) > 0.35 && arc.centroid(d).length; })).enter().append("text")
        .attr("class", "arclabel")
        .attr("transform", function(d) { var ct = arc.centroid(d); return "translate(" + ct[0] + "," + ct[1] + ")"; })
        .attr("text-anchor", "middle").attr("dy", ".35em")
        .attr("fill", "#ffffff").style("font-size", "12px").style("font-weight", "700")
        .style("pointer-events", "none").style("text-shadow", "0 1px 2px rgba(0,0,0,0.4)")
        .text(function(d) { return Math.round(d.value / total * 100) + "%"; });
      g.append("text").attr("text-anchor", "middle").attr("dy", ".25em")
        .style("font-size", Math.max(10, Math.min(18, R * 0.28)) + "px").style("font-weight", "700")
        .style("fill", "var(--color-content-text)").text(fmtVal(total));
      g.append("text").attr("text-anchor", "middle").attr("dy", "1.7em")
        .style("font-size", "9px").style("fill", "var(--color-content-text-secondary)").text("TOTAL");
      leaves.forEach(function(d, i) {
        var pct = Math.round(d.value / total * 100);
        var item = document.createElement("div");
        item.style.cssText = "display:flex;align-items:center;gap:6px;margin-bottom:5px;";
        var sw = document.createElement("span");
        sw.style.cssText = "width:9px;height:9px;border-radius:2px;flex:none;background:" + colors[i % colors.length] + ";";
        var nm = document.createElement("span");
        nm.style.cssText = "flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
        nm.textContent = d.data.name;
        var vl = document.createElement("span");
        vl.style.cssText = "flex:none;font-weight:700;";
        vl.textContent = fmtVal(d.value);
        var pc = document.createElement("span");
        pc.style.cssText = "flex:none;width:40px;text-align:right;color:var(--color-content-text-secondary);";
        pc.textContent = pct + "%";
        item.appendChild(sw); item.appendChild(nm); item.appendChild(vl); item.appendChild(pc);
        legBox.appendChild(item);
      });
    });

    /* Word Cloud */
    DashboardRenderer.register("wordcloud", function (widget, container) {
      var c = widget.config || {};
      var words = c.words || [];
      if (!words.length) return;
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var colors = ["var(--chart-1)","var(--chart-2)","var(--chart-3)","var(--chart-4)","var(--chart-5)","var(--chart-6)","var(--chart-7)","var(--chart-8)"];
      var list = words.slice(0, 40).sort(function (a, b) { return Math.abs(b.value || 0) - Math.abs(a.value || 0); });
      var values = list.map(function (w) { return Math.abs(w.value || 0); });
      var vMax = Math.max.apply(null, values) || 1, vMin = Math.min.apply(null, values);
      var fsAbs = Math.max(11, Math.min(46, Math.min(W, H) / 7));
      var fontScale = d3.scaleLinear().domain([vMin, vMax]).range([Math.max(10, fsAbs * 0.4), fsAbs]);
      var placed = [];
      function collides(x, y, wd, hg) {
        for (var i = 0; i < placed.length; i++) {
          var p = placed[i];
          if (x < p.x + p.w && x + wd > p.x && y < p.y + p.h && y + hg > p.y) return true;
        }
        return false;
      }
      function tryPlace(wd, hg) {
        var maxR = Math.sqrt(W * W + H * H);
        for (var r = 0; r < maxR; r += 2) {
          for (var a = 0; a < Math.PI * 2; a += Math.PI / 16) {
            var x = W / 2 + r * Math.cos(a) - wd / 2;
            var y = H / 2 + r * Math.sin(a) - hg / 2;
            if (x < 2 || y < 2 || x + wd > W - 2 || y + hg > H - 2) continue;
            if (collides(x, y, wd, hg)) continue;
            return [x, y];
          }
        }
        return null;
      }
      list.forEach(function (word, i) {
        var fs = fontScale(Math.abs(word.value || 0));
        var wd = _textWidth(word.text, fs) + 12;
        var hg = fs * 1.35;
        var pt = tryPlace(wd, hg);
        if (!pt) return;
        placed.push({ x: pt[0], y: pt[1], w: wd, h: hg });
        var g = svg.append("g").attr("transform", "translate(" + (pt[0] + wd / 2) + "," + (pt[1] + hg / 2) + ")");
        g.append("text").attr("text-anchor", "middle").attr("dominant-baseline", "central")
          .attr("font-family", "Segoe UI, Arial, sans-serif")
          .attr("fill", colors[i % colors.length]).attr("font-size", fs + "px").attr("font-weight", fs > 24 ? "700" : "500").attr("opacity", 0.9)
          .text(word.text)
          .on("mouseenter", function(ev) { d3.select(this).attr("opacity", 1); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, word.text, word.value, ev); Tooltip.show(Tooltip.multi([[word.text, (word.value || 0).toLocaleString()]]), ev); })
          .on("mousemove", function(ev) { Tooltip.move(ev); })
          .on("mouseleave", function() { d3.select(this).attr("opacity", 0.9); Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); })
          .on("click", function(ev) { if (window.BlankDash && BlankDash._chartClick) BlankDash._chartClick(widget, word.text, ev); });
      });
    });

    /* Progress Ring */
    DashboardRenderer.register("progress", function (widget, container) {
      var c = widget.config || {};
      var value;
      if (c.value != null && c.value !== "") value = parseFloat(c.value) || 0;
      else value = c.progressValue != null ? parseFloat(c.progressValue) : 0;
      var total = c.progressMax != null ? (parseFloat(c.progressMax) || 100) : (parseFloat(c.total) || 100);
      var pct = Math.min(100, (value / (total || 1)) * 100);
      var W = container.clientWidth || 200, H = container.clientHeight || 200;
      var R = Math.min(W, H) / 2 - 16;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + W / 2 + "," + H / 2 + ")");
      var barColor = c.color || c.progressColor || "#10B981";
      var trackColor = c.progressTrackColor || "rgba(255,255,255,0.08)";
      var radius = c.progressBorderRadius != null ? c.progressBorderRadius : 4;
      var arc = d3.arc().innerRadius(R * 0.7).outerRadius(R).startAngle(0).cornerRadius(radius);
      g.append("path").datum({ endAngle: 2 * Math.PI }).attr("d", arc).attr("fill", trackColor);
      g.append("path").datum({ endAngle: (pct / 100) * 2 * Math.PI }).attr("d", arc).attr("fill", barColor);
      var pctFs = Math.min(Math.max(14, R * 0.38), R * 0.48);
      var lf = c.progressLabelFormat || "percent";
      var suf = c.progressSuffix || "";
      var centerText;
      if (c.progressShowValue === false) centerText = "";
      else if (lf === "value") centerText = value.toLocaleString() + suf;
      else if (lf === "value/max") centerText = value.toLocaleString() + " / " + total.toLocaleString();
      else centerText = pct.toFixed(0) + "%" + suf;
      if (centerText) {
        g.append("text").attr("text-anchor", "middle").attr("dominant-baseline", "central").attr("fill", "#fff").attr("font-size", pctFs + "px").attr("font-weight", "700").text(centerText);
      }
      g.append("text").attr("text-anchor", "middle").attr("y", R * 0.45).attr("fill", "rgba(255,255,255,0.6)").attr("font-size", "11px").text(_clipText(value.toLocaleString() + " / " + total.toLocaleString(), R * 0.9, 11));
    });

    /* Stacked Area */
    DashboardRenderer.register("stackedarea", function (widget, container) {
      var c = widget.config || {};
      var datasets = c.datasets || [{ label: c.label || "", data: c.data || [] }];
      var labels = c.labels || [];
      if (!labels.length || !datasets.length) return;
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var margin = { top: 20, right: 20, bottom: labels.length > 5 ? 64 : 48, left: 52 };
      var w = W - margin.left - margin.right, h = H - margin.top - margin.bottom;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + margin.left + "," + margin.top + ")");
      var keys = datasets.map(function(_, i) { return String(i); });
      var stackData = labels.map(function(_, i) { var o = {}; datasets.forEach(function(ds, j) { o[String(j)] = ds.data[i] || 0; }); return o; });
      var stacked = d3.stack().keys(keys)(stackData);
      var x = d3.scalePoint().domain(labels).range([0, w]).padding(0.1);
      var y = d3.scaleLinear().domain([0, d3.max(stacked[stacked.length - 1], function(d) { return d[1]; }) * 1.1 || 10]).range([h, 0]).nice();
      var colors = ["var(--chart-1)","var(--chart-2)","var(--chart-3)","var(--chart-4)","var(--chart-5)","var(--chart-6)","var(--chart-7)","var(--chart-8)"];
      g.append("g").attr("transform", "translate(0," + h + ")").call(d3.axisBottom(x)).call(function(g2) { g2.selectAll(".tick text").attr("fill", "#aaa").attr("font-size", "10px").attr("transform", "rotate(-35)").attr("text-anchor", "end"); });
      g.append("g").call(d3.axisLeft(y).ticks(5).tickFormat(d3.format("~s"))).call(function(g2) { g2.select(".domain").remove(); g2.selectAll(".tick text").attr("fill", "#aaa").attr("font-size", "10px"); g2.selectAll(".tick line").attr("stroke", "rgba(255,255,255,0.1)"); });
      var area = d3.area().x(function(_, i) { return x(labels[i]); }).y0(function(d) { return y(d[0]); }).y1(function(d) { return y(d[1]); }).curve(d3.curveMonotoneX);
      stacked.forEach(function(layer, i) {
        g.append("path").datum(layer).attr("d", area).attr("fill", colors[i % colors.length]).attr("opacity", 0.7);
      });
    });

    /* 100% Stacked Bar */
    DashboardRenderer.register("percentbar", function (widget, container) {
      var c = widget.config || {};
      var datasets = c.datasets || [{ label: c.label || "", data: c.data || [] }];
      var labels = c.labels || [];
      if (!labels.length || !datasets.length) return;
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var margin = { top: 20, right: 20, bottom: 48, left: 100 };
      var w = W - margin.left - margin.right, h = H - margin.top - margin.bottom;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + margin.left + "," + margin.top + ")");
      var y = d3.scaleBand().domain(labels).range([0, h]).padding(0.25);
      var x = d3.scaleLinear().domain([0, 100]).range([0, w]);
      var colors = ["var(--chart-1)","var(--chart-2)","var(--chart-3)","var(--chart-4)","var(--chart-5)","var(--chart-6)","var(--chart-7)","var(--chart-8)"];
      g.append("g").call(d3.axisLeft(y).tickFormat(function(d) { return _clipText(d, 84, 11); })).call(function(g2) { g2.select(".domain").remove(); g2.selectAll(".tick text").attr("fill", "#aaa").attr("font-size", "11px"); });
      g.append("g").attr("transform", "translate(0," + h + ")").call(d3.axisBottom(x).ticks(5).tickFormat(function(d) { return d + "%"; })).call(function(g2) { g2.selectAll(".tick text").attr("fill", "#aaa").attr("font-size", "10px"); });
      labels.forEach(function(label, li) {
        var total = datasets.reduce(function(s, ds) { return s + (ds.data[li] || 0); }, 0) || 1;
        var cumX = 0;
        datasets.forEach(function(ds, di) {
          var val = ds.data[li] || 0;
          var pct = (val / total) * 100;
          g.append("rect").attr("x", x(cumX)).attr("y", y(label)).attr("width", x(pct) - x(0)).attr("height", y.bandwidth()).attr("fill", colors[di % colors.length]).attr("rx", 2).attr("opacity", 0.85)
            .on("mouseenter", function(ev) { d3.select(this).attr("opacity", 1); Tooltip.show(Tooltip.multi([[ds.label, val.toLocaleString()], ["Share", pct.toFixed(1) + "%"], ["Category", label]]), ev); })
            .on("mousemove", function(ev) { Tooltip.move(ev); })
            .on("mouseleave", function() { d3.select(this).attr("opacity", 0.85); Tooltip.hide(); });
          cumX += pct;
        });
      });
    });

    /* ─── Slope Chart (P5) — start/end dot-per-category with deltas ─── */
    DashboardRenderer.register("slope", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], datasets = c.datasets || [];
      var starts = datasets[0] ? datasets[0].data : [];
      var ends = datasets[1] ? datasets[1].data : (datasets[0] ? starts : c.data || []);
      if (!labels.length || !starts.length) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-chart-line"></i><div>Two columns needed (group, start, end)</div></div>'; return; }
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)", axisColor: "rgba(255,255,255,0.2)" };
      var all = starts.concat(ends).map(function (v) { return Number(v) || 0; });
      var minV = Math.min.apply(null, all), maxV = Math.max.apply(null, all);
      if (minV === maxV) { minV -= 1; maxV += 1; }
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var m = { top: 18, right: 76, bottom: 18, left: 76 };
      var w = W - m.left - m.right, h = H - m.top - m.bottom;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + m.left + "," + m.top + ")");
      var y = d3.scaleLinear().domain([minV, maxV]).range([h, 0]).nice();
      var colors = _colorsOf(c);
      g.append("line").attr("x1", 0).attr("x2", 0).attr("y1", 0).attr("y2", h).attr("stroke", theme.axisColor).attr("stroke-width", 1.5);
      g.append("line").attr("x1", w).attr("x2", w).attr("y1", 0).attr("y2", h).attr("stroke", theme.axisColor).attr("stroke-width", 1.5);
      var lines = [];
      labels.forEach(function (lbl, i) {
        var s = Number(starts[i]) || 0, e = i < ends.length ? (Number(ends[i]) || 0) : s;
        var x0 = 0, x1 = w, y0 = y(s), y1 = y(e);
        var col = colors[i % colors.length];
        var up = e >= s;
        g.append("line").attr("x1", x0).attr("x2", x1).attr("y1", y0).attr("y2", y1)
          .attr("stroke", col).attr("stroke-width", 2).attr("opacity", 0.9)
          .on("mouseenter", function (ev) { d3.select(this).attr("stroke-width", 3.5); Tooltip.show(Tooltip.multi([[String(lbl),  _fmtNumShort(e)]]), ev); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, lbl, e, ev); })
          .on("mousemove", function (ev) { Tooltip.move(ev); })
          .on("mouseleave", function () { d3.select(this).attr("stroke-width", 2); Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); });
        g.append("circle").attr("cx", x0).attr("cy", y0).attr("r", 4).attr("fill", col);
        g.append("circle").attr("cx", x1).attr("cy", y1).attr("r", 4).attr("fill", col);
        var delta = e - s;
        g.append("text").attr("x", x1 + 8).attr("y", y1 + 3).attr("fill", up ? "var(--color-success)" : "var(--color-danger)").attr("font-size", "10px").attr("font-weight", "600")
          .text((up ? "+" : "") +  _fmtNumShort(delta))
          .each(function (d) { var t = d3.select(this); var n = t.node(); if (n && n.getComputedTextLength && n.getComputedTextLength() > m.right - 14) t.text(_clipText(t.text(), m.right - 14, 10)); });
        lines.push({ lbl: lbl, s: s, e: e, sEnd: y0, eEnd: y1 });
      });
      lines.forEach(function (ln, i) {
        var col = colors[i % colors.length];
        g.append("text").attr("x", 0).attr("y", ln.sEnd - 5).attr("fill", col).attr("font-size", "10px").attr("text-anchor", "start").text(_clipText(String(ln.lbl), m.left - 8, 10));
      });
      g.selectAll(".endlabel").data(lines).enter().append("text").attr("class", "endlabel")
        .attr("x", w).attr("y", function (d) { return d.eEnd - 5; }).attr("fill", theme.text).attr("font-size", "10px").attr("text-anchor", "end").text(function (d) { return _clipText(String(d.lbl), m.right - 28, 10); });
    });

    /* ─── Sankey Diagram (P5) — lightweight source→target flows ─── */
    DashboardRenderer.register("sankey", function (widget, container) {
      var c = widget.config || {};
      var flow = c.flow || (c.source && c.target ? c.source.map(function (s, i) { return { source: String(s), target: String(c.target[i]), value: Number(c.value[i]) || 0 }; }) : []);
      if (!flow.length) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-code-branch"></i><div>Needs source, target, value columns</div></div>'; return; }
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)", axisColor: "rgba(255,255,255,0.2)" };
      var srcs = [], tgts = [];
      flow.forEach(function (f) { if (srcs.indexOf(f.source) === -1) srcs.push(f.source); if (tgts.indexOf(f.target) === -1) tgts.push(f.target); });
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var pad = 6, m = { top: 12, right: 12, bottom: 12, left: 12 };
      var w = W - m.left - m.right, h = H - m.top - m.bottom;
      var x0 = 0, x1 = w, cw = Math.min(90, w * 0.18);
      var total = 0; flow.forEach(function (f) { total += f.value; }); if (!total) total = 1;
      var sAgg = {}, tAgg = {};
      flow.forEach(function (f) { sAgg[f.source] = (sAgg[f.source] || 0) + f.value; tAgg[f.target] = (tAgg[f.target] || 0) + f.value; });
      var ySpix = d3.scaleLinear().domain([0, d3.max(srcs.map(function (s) { return sAgg[s]; })) || 1]).range([0, h - Math.max(0, srcs.length - 1) * pad]);
      var yTpix = d3.scaleLinear().domain([0, d3.max(tgts.map(function (t) { return tAgg[t]; })) || 1]).range([0, h - Math.max(0, tgts.length - 1) * pad]);
      var sPos = {}, tPos = {};
      var cur = 0; srcs.forEach(function (s, i) { var v = sAgg[s] || 0; sPos[s] = { y: cur, h: ySpix(v) }; cur += ySpix(v) + pad; });
      var cur2 = 0; tgts.forEach(function (t, i) { var v = tAgg[t] || 0; tPos[t] = { y: cur2, h: yTpix(v) }; cur2 += yTpix(v) + pad; });
      var colors = _colorsOf(c);
      var scMap = {}; srcs.forEach(function (s, i) { scMap[s] = colors[i % colors.length]; });
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + m.left + "," + m.top + ")");
      flow.forEach(function (f, i) {
        var sy = sPos[f.source], ty = tPos[f.target];
        var dy = Math.abs(sy.y + sy.h / 2 - (ty.y + ty.h / 2));
        var curve = Math.min(dy * 0.55, w * 0.42);
        var d = "M " + x0 + " " + (sy.y + sy.h / 2) +
          " C " + (x0 + curve) + " " + (sy.y + sy.h / 2) + ", " + (x1 - curve) + " " + (ty.y + ty.h / 2) + ", " + x1 + " " + (ty.y + ty.h / 2) +
          " L " + x1 + " " + (ty.y + ty.h / 2) +
          " C " + (x1 - curve) + " " + (ty.y + ty.h / 2) + ", " + (x0 + curve) + " " + (sy.y + sy.h / 2) + ", " + x0 + " " + (sy.y + sy.h / 2) + " Z";
        var path = g.append("path").attr("d", d).attr("fill", scMap[f.source]).attr("opacity", 0.55)
          .on("mouseenter", function (ev) { d3.select(this).attr("opacity", 0.9); Tooltip.show(Tooltip.multi([[String(f.source) + " → " + String(f.target),  _fmtNumShort(f.value)]]), ev); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, f.source + " → " + f.target, f.value, ev); })
          .on("mousemove", function (ev) { Tooltip.move(ev); })
          .on("mouseleave", function () { d3.select(this).attr("opacity", 0.55); Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); });
        if (f.value / total > 0.02) {
          var tt = document.createElementNS("http://www.w3.org/2000/svg", "title");
          tt.textContent = f.source + " → " + f.target + ": " + _fmtNumShort(f.value);
          path.node().appendChild(tt);
        }
      });
      srcs.forEach(function (s) {
        g.append("rect").attr("x", x0 - cw).attr("y", sPos[s].y).attr("width", cw).attr("height", Math.max(4, sPos[s].h))
          .attr("rx", 3).attr("fill", scMap[s]).attr("opacity", 0.9);
        g.append("text").attr("x", x0 - cw + 6).attr("y", sPos[s].y + 10).attr("fill", "#fff").attr("font-size", "9px").attr("font-weight", "600").text(_clipText(String(s), cw - 10, 9));
      });
      tgts.forEach(function (t) {
        g.append("rect").attr("x", x1).attr("y", tPos[t].y).attr("width", cw).attr("height", Math.max(4, tPos[t].h))
          .attr("rx", 3).attr("fill", scMap[srcs[0]]).attr("opacity", 0.6);
        g.append("text").attr("x", x1 + 6).attr("y", tPos[t].y + 10).attr("fill", theme.text).attr("font-size", "9px").attr("font-weight", "600").text(_clipText(String(t), cw - 8, 9));
      });
    });

    /* ─── Stream Graph (P5) — overlapping "river" around a central axis ─── */
    DashboardRenderer.register("stream", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], datasets = c.datasets || [];
      if (!labels.length || !datasets.length) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-wind"></i><div>Needs several measure columns</div></div>'; return; }
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)", axisColor: "rgba(255,255,255,0.2)" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var m = { top: 16, right: 16, bottom: 30, left: 42 };
      var w = W - m.left - m.right, h = H - m.top - m.bottom;
      var x = d3.scalePoint().domain(labels).range([0, w]).padding(0.4);
      var n = labels.length;
      var cols = datasets.map(function (ds) { return (ds.data || []).map(function (v) { return Math.max(0, Number(v) || 0); }); });
      var layers = cols.map(function (col) { return col.concat([0]); }); /* ensure length */
      var nK = layers.length, nT = n + 1;
      layers.forEach(function (ly) { while (ly.length < nT) ly.push(0); });
      /* stream layout: ks = cumulative sums, normalised so the river floats about 0 */
      var ks = [];
      for (var i = 0; i < nK; i++) { var row = []; for (var t2 = 0; t2 < nT; t2++) row.push(0); ks.push(row); }
      for (var t = 0; t < nT; t++) { for (var k = 0; k < nK; k++) { ks[k][t] = (k ? ks[k - 1][t] : 0) + layers[k][t]; } }
      var totals = []; for (var t3 = 0; t3 < nT; t3++) { totals.push(ks[nK - 1][t3]); }
      var maxT = Math.max.apply(null, totals) || 1;
      var y = d3.scaleLinear().domain([-maxT / 2, maxT / 2]).range([h, 0]).nice();
      var stackGen = d3.area().x(function (d, i) { return x(labels[i]) || 0; }).y0(function (d) { return y(d[0]); }).y1(function (d) { return y(d[1]); });
      var colors = _colorsOf(c);
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + m.left + "," + m.top + ")");
      g.append("line").attr("x1", 0).attr("x2", w).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", theme.axisColor).attr("stroke-dasharray", "3,3");
      layers.forEach(function (ly, k) {
        var pts = [];
        for (var t = 0; t < nT; t++) {
          var cLo = (ks[nK - 1][t] / 2) - ks[k][t];
          var cHi = (ks[nK - 1][t] / 2) - (ks[k][t] - ly[t]);
          pts.push([cLo, cHi]);
        }
        pts.splice(pts.length - 1, 1);
        var path = g.append("path").datum(pts).attr("d", stackGen).attr("fill", colors[k % colors.length]).attr("opacity", 0.82).attr("stroke", "none")
          .on("mouseenter", function (ev) { d3.select(this).attr("opacity", 1); Tooltip.show(Tooltip.multi([[datasets[k].label || ("Series " + (k + 1)),  _fmtNumShort(ly[nT - 2])]]), ev); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, datasets[k].label || "", ly[nT - 2], ev); })
          .on("mousemove", function (ev) { Tooltip.move(ev); })
          .on("mouseleave", function () { d3.select(this).attr("opacity", 0.82); Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); });
      });
      g.append("g").attr("transform", "translate(0," + h + ")").call(d3.axisBottom(x).ticks(Math.min(n, 8)).tickFormat(function (d) { return _clipText(String(d), 44, 9); }))
        .call(function (g2) { g2.select(".domain").attr("stroke", theme.axisColor); g2.selectAll(".tick text").attr("fill", theme.textSecondary).attr("font-size", "9px"); g2.selectAll(".tick line").attr("stroke", theme.axisColor); });
    });

    /* ─── Population Pyramid (P5) — mirrored horizontal bars ─── */
    DashboardRenderer.register("population", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], datasets = c.datasets || [];
      var male = datasets[0] ? datasets[0].data : [], female = datasets[1] ? datasets[1].data : [];
      if (!labels.length || (!male.length && !female.length)) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-people-roof"></i><div>Needs two measure columns (male, female)</div></div>'; return; }
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)", axisColor: "rgba(255,255,255,0.2)" };
      var all = male.concat(female).map(function (v) { return Math.abs(Number(v) || 0); });
      var maxV = Math.max.apply(null, all) || 1;
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var m = { top: 14, right: 14, bottom: 26, left: 78 };
      var w = W - m.left - m.right, h = H - m.top - m.bottom;
      var y = d3.scaleBand().domain(labels).range([0, h]).padding(0.22);
      var xc = d3.scaleLinear().domain([-maxV, maxV]).range([0, w]);
      var mid = xc(0);
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(" + m.left + "," + m.top + ")");
      g.append("line").attr("x1", mid).attr("x2", mid).attr("y1", 0).attr("y2", h).attr("stroke", theme.axisColor).attr("stroke-dasharray", "3,3");
      labels.forEach(function (lbl, i) {
        var mv = Math.abs(Number(male[i]) || 0), fv = Math.abs(Number(female[i]) || 0);
        g.append("rect").attr("x", xc(-mv)).attr("y", y(lbl)).attr("width", Math.max(0, xc(0) - xc(-mv))).attr("height", y.bandwidth()).attr("rx", 3).attr("fill", "var(--chart-1)").attr("opacity", 0.85)
          .on("mouseenter", function (ev) { Tooltip.show(Tooltip.multi([[String(lbl) + " · Male",  _fmtNumShort(mv)]]), ev); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, lbl + " (M)", mv, ev); })
          .on("mousemove", function (ev) { Tooltip.move(ev); })
          .on("mouseleave", function () { Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); });
        g.append("rect").attr("x", mid).attr("y", y(lbl)).attr("width", Math.max(0, xc(fv) - xc(0))).attr("height", y.bandwidth()).attr("rx", 3).attr("fill", "#F472B6").attr("opacity", 0.85)
          .on("mouseenter", function (ev) { Tooltip.show(Tooltip.multi([[String(lbl) + " · Female",  _fmtNumShort(fv)]]), ev); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, lbl + " (F)", fv, ev); })
          .on("mousemove", function (ev) { Tooltip.move(ev); })
          .on("mouseleave", function () { Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); });
        g.append("text").attr("x", -6).attr("y", y(lbl) + y.bandwidth() / 2 + 3).attr("fill", theme.textSecondary).attr("font-size", "10px").attr("text-anchor", "end").text(_clipText(String(lbl), m.left - 12, 10));
      });
      g.append("text").attr("x", w / 4).attr("y", -6).attr("fill", "var(--chart-1)").attr("font-size", "10px").attr("font-weight", "700").attr("text-anchor", "middle").text((datasets[0] && datasets[0].label) || "Male");
      g.append("text").attr("x", (3 * w) / 4).attr("y", -6).attr("fill", "#F472B6").attr("font-size", "10px").attr("font-weight", "700").attr("text-anchor", "middle").text((datasets[1] && datasets[1].label) || "Female");
    });

    /* ─── Venn Diagram (P5) — three-region two-set venn ─── */
    DashboardRenderer.register("venn", function (widget, container) {
      var c = widget.config || {};
      var sizes = c.vennSizes || [c.data[0] || 0, c.data[1] || 0, c.data[2] || 0];
      var aOnly = Number(sizes[0]) || 0, bOnly = Number(sizes[1]) || 0, both = Number(sizes[2]) || 0;
      var aName = String(c.vennA != null ? c.vennA : (c.labels ? c.labels[0] : "A") || "A");
      var bName = String(c.vennB != null ? c.vennB : (c.labels ? c.labels[1] : "B") || "B");
      if (!(aOnly + bOnly + both)) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-circle-nodes"></i><div>No data</div></div>'; return; }
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa" };
      var aTot = aOnly + both, bTot = bOnly + both;
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var R = Math.min(W, H) * 0.32;
      var cx = W / 2, cy = H / 2 + 6;
      var d = Math.min((aTot + bTot ? (both / Math.min(aTot, bTot)) * (R * 1.28) : 0), R * 1.18);
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var g = svg.append("g").attr("transform", "translate(0,0)");
      g.append("circle").attr("cx", cx - d / 2).attr("cy", cy).attr("r", R).attr("fill", "var(--chart-1)").attr("opacity", 0.4).attr("stroke", "var(--chart-1)").attr("stroke-width", 2)
        .on("mouseenter", function (ev) { Tooltip.show(Tooltip.multi([[aName,  _fmtNumShort(aTot)], ["only " + aName,  _fmtNumShort(aOnly)]]), ev); })
        .on("mousemove", function (ev) { Tooltip.move(ev); })
        .on("mouseleave", function () { Tooltip.hide(); });
      g.append("circle").attr("cx", cx + d / 2).attr("cy", cy).attr("r", R).attr("fill", "#F472B6").attr("opacity", 0.4).attr("stroke", "#F472B6").attr("stroke-width", 2)
        .on("mouseenter", function (ev) { Tooltip.show(Tooltip.multi([[bName,  _fmtNumShort(bTot)], ["only " + bName,  _fmtNumShort(bOnly)]]), ev); })
        .on("mousemove", function (ev) { Tooltip.move(ev); })
        .on("mouseleave", function () { Tooltip.hide(); });
      g.append("circle").attr("cx", cx - d / 2).attr("cy", cy).attr("r", R).attr("fill", "none").attr("stroke", "var(--chart-1)").attr("stroke-width", 2);
      g.append("circle").attr("cx", cx + d / 2).attr("cy", cy).attr("r", R).attr("fill", "none").attr("stroke", "#F472B6").attr("stroke-width", 2);
      g.append("text").attr("x", cx - d / 2 - R * 0.7).attr("y", cy).attr("text-anchor", "middle").attr("fill", theme.text).attr("font-size", "12px").attr("font-weight", "700").text(_clipText(aName, R - 12, 12));
      g.append("text").attr("x", cx + d / 2 + R * 0.7).attr("y", cy).attr("text-anchor", "middle").attr("fill", theme.text).attr("font-size", "12px").attr("font-weight", "700").text(_clipText(bName, R - 12, 12));
      g.append("text").attr("x", cx).attr("y", cy + 4).attr("text-anchor", "middle").attr("fill", "#fff").attr("font-size", "11px").attr("font-weight", "800").text( _fmtNumShort(both));
      g.append("text").attr("x", cx).attr("y", H - 6).attr("text-anchor", "middle").attr("fill", theme.textSecondary).attr("font-size", "11px").text(_clipText("Only " + aName + ": " +  _fmtNumShort(aOnly) + " · Only " + bName + ": " +  _fmtNumShort(bOnly), Math.min(W - 20, 220), 11));
    });

    /* Chord Diagram — relationships between categories via source→target flows */
    DashboardRenderer.register("chord", function (widget, container) {
      var c = widget.config || {};
      var flow = c.flow || [];
      if (!flow.length) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-circle-nodes"></i><div>No source → target pairs</div></div>'; return; }
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var cx = W / 2, cy = H / 2, R = Math.min(cx, cy) - 34;
      /* Collect unique node names (cap at 12 to stay readable) */
      var names = [], seen = {};
      flow.forEach(function (f) {
        if (f.source != null && !(f.source in seen)) { seen[f.source] = 1; names.push(String(f.source)); }
        if (f.target != null && !(f.target in seen)) { seen[f.target] = 1; names.push(String(f.target)); }
      });
      names = names.slice(0, 12);
      if (names.length < 2) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-circle-nodes"></i><div>Need 2+ distinct categories</div></div>'; return; }
      var nIdx = {};
      names.forEach(function (n, i) { nIdx[n] = i; });
      /* Build the NxN matrix */
      var N = names.length;
      var matrix = [];
      for (var r0 = 0; r0 < N; r0++) { matrix.push(new Array(N).fill(0)); }
      flow.forEach(function (f) {
        var si = nIdx[String(f.source)], ti = nIdx[String(f.target)];
        if (si == null || ti == null) return;
        matrix[si][ti] += Math.abs(+f.value || 0) || 0;
      });
      var colors = ["var(--chart-1)","var(--chart-2)","var(--chart-3)","var(--chart-4)","var(--chart-5)","var(--chart-6)","var(--chart-7)","var(--chart-8)"];
      var chord = d3.chord().padAngle(0.03).sortSubgroups(d3.descending);
      var arcsGen = d3.arc().innerRadius(R * 0.72).outerRadius(R).cornerRadius(3);
      var ribbonGen = d3.ribbon().source(function (d) { return d.source; }).target(function (d) { return d.target; }).radius(R * 0.71);
      var g = svg.append("g").attr("transform", "translate(" + cx + "," + cy + ")");
      var chords = chord(matrix);
      /* Ribbons first (under the arcs) */
      g.selectAll(".ribbon").data(chords).enter().append("path")
        .attr("class", "ribbon")
        .attr("d", ribbonGen)
        .attr("fill", function (d) { return colors[d.source.index % colors.length]; })
        .attr("opacity", 0.28)
        .on("mouseenter", function (ev, d) {
          d3.select(this).attr("opacity", 0.65);
          Tooltip.show(Tooltip.multi([[names[d.source.index] + " → " + names[d.target.index], _fmtNumShort(d.source.value)]], ev));
        })
        .on("mousemove", function (ev) { Tooltip.move(ev); })
        .on("mouseleave", function () { d3.select(this).attr("opacity", 0.28); Tooltip.hide(); })
        .on("click", function (ev, d) { if (window.BlankDash && BlankDash._chartClick) BlankDash._chartClick(widget, names[d.source.index], ev); });
      /* Segment arcs */
      g.selectAll(".seg").data(chords.groups ? chords.groups : []).enter().append("path")
        .attr("class", "seg")
        .attr("d", arcsGen)
        .attr("fill", function (d) { return colors[d.index % colors.length]; })
        .attr("opacity", 0.95)
        .on("mouseenter", function (ev, d) {
          d3.select(this).attr("opacity", 1);
          var tot = 0; matrix[d.index].forEach(function (v) { tot += v; });
          Tooltip.show(Tooltip.multi([[names[d.index], _fmtNumShort(tot) + " out"]], ev));
        })
        .on("mousemove", function (ev) { Tooltip.move(ev); })
        .on("mouseleave", function () { d3.select(this).attr("opacity", 0.95); Tooltip.hide(); })
        .on("click", function (ev, d) { if (window.BlankDash && BlankDash._chartClick) BlankDash._chartClick(widget, names[d.index], ev); });
      /* Node labels around the ring */
      g.selectAll(".clabel").data(chords.groups ? chords.groups : []).enter().append("text")
        .attr("class", "clabel")
        .attr("transform", function (d) {
          var a = (d.startAngle + d.endAngle) / 2 - Math.PI / 2;
          var lr = Math.cos(a) >= 0;
          return "translate(" + ((R + 8) * Math.cos(a)) + "," + ((R + 8) * Math.sin(a)) + ") rotate(" + (lr ? 0 : 180) + ")";
        })
        .attr("text-anchor", function (d) {
          var a = (d.startAngle + d.endAngle) / 2 - Math.PI / 2;
          return Math.cos(a) >= 0 ? "start" : "end";
        })
        .attr("fill", theme.text).attr("font-size", "10px").attr("font-weight", "600")
        .text(function (d) { return _clipText(names[d.index], 64, 10); });
    });

    /* Ridgeline Plot — overlapping density curves per category */
    DashboardRenderer.register("ridgeline", function (widget, container) {
      var c = widget.config || {};
      var groups = c.groups || [];
      if (!groups.length) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-water-ladder"></i><div>No data</div></div>'; return; }
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var margin = { top: 14, right: 16, bottom: 34, left: 76 };
      var w = W - margin.left - margin.right, h = H - margin.top - margin.bottom;
      var nRows = Math.min(groups.length, 10);
      var rowH = h / nRows;
      var overlap = rowH * 0.55;
      var allVals = [];
      groups.slice(0, nRows).forEach(function (g) { allVals = allVals.concat(g.values); });
      if (!allVals.length) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-water-ladder"></i><div>No numeric values</div></div>'; return; }
      var vMin = d3.min(allVals), vMax = d3.max(allVals);
      if (vMin === vMax) { vMin -= 1; vMax += 1; }
      var x = d3.scaleLinear().domain([vMin, vMax]).range([0, w]);
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var gAll = svg.append("g").attr("transform", "translate(" + margin.left + "," + margin.top + ")");
      var colors = ["var(--chart-1)","var(--chart-2)","var(--chart-3)","var(--chart-4)","var(--chart-5)","var(--chart-6)","var(--chart-7)","var(--chart-8)"];
      /* X axis */
      gAll.append("g").attr("transform", "translate(0," + h + ")").call(d3.axisBottom(x).ticks(6).tickFormat(d3.format("~s")))
        .call(function (g2) { g2.select(".domain").attr("stroke", theme.gridLine); g2.selectAll(".tick text").attr("fill", theme.textSecondary).attr("font-size", "10px"); });
      /* One density curve per group (Scott's-rule-ish bins + gaussian smooth) */
      groups.slice(0, nRows).forEach(function (grp, i) {
        var vals = grp.values;
        if (!vals.length) return;
        var bins = d3.bin().thresholds(Math.min(24, Math.max(6, Math.ceil(Math.sqrt(vals.length))))).domain([vMin, vMax])(vals);
        var maxD = d3.max(bins, function (b) { return b.length; }) || 1;
        var yBase = h - (i + 0.5) * rowH;
        var curve = d3.line()
          .x(function (b) { return x(b.x0) + (x(b.x1) - x(b.x0)) / 2; })
          .y(function (b) { return yBase - (b.length / maxD) * overlap; })
          .curve(d3.curveBasis);
        var area = d3.area()
          .x(function (b) { return x(b.x0) + (x(b.x1) - x(b.x0)) / 2; })
          .y0(yBase)
          .y1(function (b) { return yBase - (b.length / maxD) * overlap; })
          .curve(d3.curveBasis);
        var col = colors[i % colors.length];
        gAll.append("path").attr("d", area(bins)).attr("fill", col).attr("opacity", 0.35);
        gAll.append("path").attr("d", curve(bins)).attr("fill", "none").attr("stroke", col).attr("stroke-width", 1.6);
        gAll.append("text").attr("x", -8).attr("y", yBase + 3).attr("text-anchor", "end")
          .attr("fill", theme.textSecondary).attr("font-size", "10px")
          .text(_clipText(grp.label, 70, 10));
      });
    });

    /* Beeswarm Plot — one dot per record, dodged to avoid overlap */
    DashboardRenderer.register("beeswarm", function (widget, container) {
      var c = widget.config || {};
      var groups = c.groups || [];
      if (!groups.length) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-braille"></i><div>No data</div></div>'; return; }
      var theme = typeof getTheme === "function" ? getTheme() : { text: "#fff", textSecondary: "#aaa", gridLine: "rgba(255,255,255,0.1)" };
      var W = container.clientWidth || 400, H = container.clientHeight || 280;
      var margin = { top: 14, right: 16, bottom: 34, left: 76 };
      var w = W - margin.left - margin.right, h = H - margin.top - margin.bottom;
      var nRows = Math.min(groups.length, 12);
      var rowH = h / nRows;
      var allVals = [];
      groups.slice(0, nRows).forEach(function (g) { allVals = allVals.concat(g.values); });
      if (!allVals.length) { container.innerHTML = '<div class="ff-placeholder"><i class="fas fa-braille"></i><div>No numeric values</div></div>'; return; }
      var vMin = d3.min(allVals), vMax = d3.max(allVals);
      if (vMin === vMax) { vMin -= 1; vMax += 1; }
      var x = d3.scaleLinear().domain([vMin, vMax]).range([0, w]);
      var r = Math.max(2.2, Math.min(5, rowH / 6));
      container.innerHTML = "";
      var svg = d3.select(container).append("svg").attr("width", W).attr("height", H);
      var gAll = svg.append("g").attr("transform", "translate(" + margin.left + "," + margin.top + ")");
      var colors = ["var(--chart-1)","var(--chart-2)","var(--chart-3)","var(--chart-4)","var(--chart-5)","var(--chart-6)","var(--chart-7)","var(--chart-8)"];
      gAll.append("g").attr("transform", "translate(0," + h + ")").call(d3.axisBottom(x).ticks(6).tickFormat(d3.format("~s")))
        .call(function (g2) { g2.select(".domain").attr("stroke", theme.gridLine); g2.selectAll(".tick text").attr("fill", theme.textSecondary).attr("font-size", "10px"); });
      groups.slice(0, nRows).forEach(function (grp, i) {
        var yBase = h - (i + 0.5) * rowH;
        var col = colors[i % colors.length];
        /* Simple 1-D dodge: place each dot at the lowest free y slot */
        var slots = [];
        grp.values.forEach(function (v) {
          var px = x(v);
          var py = yBase;
          for (var s = 0; s < slots.length; s++) {
            if (Math.abs(slots[s].x - px) < r * 2.2) {
              var cand = slots[s].y - r * 2.2;
              if (cand > yBase - rowH + r) { py = cand; break; }
            }
          }
          slots.push({ x: px, y: py });
          gAll.append("circle").attr("cx", px).attr("cy", py).attr("r", r).attr("fill", col).attr("opacity", 0.75)
            .on("mouseenter", function (ev) { d3.select(this).attr("opacity", 1); Tooltip.show(Tooltip.multi([[grp.label, _fmtNumShort(v)]], ev)); })
            .on("mousemove", function (ev) { Tooltip.move(ev); })
            .on("mouseleave", function () { d3.select(this).attr("opacity", 0.75); Tooltip.hide(); });
        });
        gAll.append("text").attr("x", -8).attr("y", yBase + 3).attr("text-anchor", "end")
          .attr("fill", theme.textSecondary).attr("font-size", "10px")
          .text(_clipText(grp.label, 70, 10));
      });
    });
  }

  /* Destroy any live Chart.js instance already bound to a canvas before re-render */
  function _safeChartCanvas(id) {
    var el = id ? document.getElementById(id) : null;
    if (!el) return false;
    if (window.Chart && Chart.getChart && Chart.getChart(el)) {
      try { Chart.getChart(el).destroy(); } catch (e) { /* continue */ }
    }
    return true;
  }

  /* ── Register extra renderers (Chart.js / HTML / d3 / KPIStyles) ── */
  function _registerExtraRenderers() {
    if (!window.DashboardRenderer) return;

    /* Step Line — Step chart */
    DashboardRenderer.register("stepline", function (widget, container) {
      var c = widget.config || {};
      var chartId = "chart_" + widget.id;
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function () {
        if (!window.Charts || !Charts.line) return;
        _safeChartCanvas(chartId);
        var so = _styleOpts(c); so.colors = _styleColors(c);
        Charts.line(chartId, c.labels || [], [{ data: c.data || [], stepped: "middle", fill: false, tension: 0 }], so);
      }, 50);
    });

    /* Smooth Line */
    DashboardRenderer.register("smoothline", function (widget, container) {
      var c = widget.config || {};
      var chartId = "chart_" + widget.id;
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function () {
        if (!window.Charts || !Charts.line) return;
        _safeChartCanvas(chartId);
        var so = _styleOpts(c); so.colors = _styleColors(c);
        Charts.line(chartId, c.labels || [], [{ data: c.data || [], tension: 0.45, fill: false }], so);
      }, 50);
    });

    /* Smooth Area */
    DashboardRenderer.register("splinearea", function (widget, container) {
      var c = widget.config || {};
      var chartId = "chart_" + widget.id;
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function () {
        if (!window.Charts || !Charts.line) return;
        _safeChartCanvas(chartId);
        var so = _styleOpts(c); so.colors = _styleColors(c);
        Charts.line(chartId, c.labels || [], [{ data: c.data || [], tension: 0.45, fill: true, borderWidth: 2 }], so);
      }, 50);
    });

    /* Lollipop — horizontal sticks with dots */
    DashboardRenderer.register("lollipop", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!labels.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      var max = 1; data.forEach(function (v) { if (Math.abs(v) > max) max = Math.abs(v); });
      var rows = labels.map(function (lbl, i) {
        var v = Number(data[i]) || 0;
        var neg = v < 0;
        var pct = Math.min(100, Math.round((Math.abs(v) / max) * 100));
        var st = c.style || {};
        var colour = neg ? (st.negativeColor || c.negativeColor || "#F87171") : (st.positiveColor || c.positiveColor || "#2563eb");
        return '<div style="display:flex;align-items:center;gap:8px;margin:7px 0;font-size:11px;">' +
          '<div style="width:86px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right;color:var(--color-content-text-secondary);">' + _esc(String(lbl)) + '</div>' +
          '<div style="flex:1;position:relative;height:3px;background:var(--bd-chrome-border,rgba(15,23,42,0.10));border-radius:3px;">' +
            '<div style="position:absolute;top:0;left:0;width:' + pct + '%;height:3px;border-radius:3px;background:' + colour + ';opacity:0.85;"></div>' +
            '<div style="position:absolute;top:-7px;left:calc(' + pct + '% - 8px);width:12px;height:12px;border-radius:50%;background:' + colour + ';box-shadow:0 0 8px ' + colour + ';border:2px solid var(--bd-workspace,#ffffff);"></div>' +
          '</div>' +
          '<div style="width:64px;flex:none;color:var(--color-content-text);font-weight:600;text-align:right;">' + v.toLocaleString() + '</div>' +
        '</div>';
      }).join("");
      container.innerHTML = '<div style="padding:10px 14px;height:100%;overflow:auto;display:flex;flex-direction:column;justify-content:center;">' + rows + '</div>';
    });

    /* Pareto — bars + cumulative % line */
    DashboardRenderer.register("pareto", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      var chartId = "chart_" + widget.id;
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function () {
        if (!window.Charts || !Charts.mixed) return;
        _safeChartCanvas(chartId);
        var so = _styleOpts(c); so.colors = _styleColors(c);
        var total = 0; data.forEach(function (v) { total += Math.abs(v) || 0; });
        var cum = 0;
        var pcts = data.map(function (v) { cum += Math.abs(v) || 0; return total ? Math.round((cum / total) * 10000) / 100 : 0; });
        Charts.mixed(chartId, labels, [
          { type: "bar", label: c.label || "Count", data: data },
          { type: "line", label: "Cumulative %", data: pcts, yAxisID: "y1", borderColor: ((so.colors && so.colors[1]) || "#b45309"), pointRadius: 2 }
        ], so);
      }, 50);
    });

    /* Rose (Nightingale) Chart — d3 */
    DashboardRenderer.register("rose", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!data.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      if (!window.d3) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Rose chart unavailable</div>'; return; }
      var nid = "rose_" + widget.id;
      container.innerHTML = '<div style="width:100%;height:100%;"><svg id="' + nid + '" style="width:100%;height:100%;min-height:140px;"></svg></div>';
      setTimeout(function () {
        var el = document.getElementById(nid);
        if (!el) return;
        var W = el.clientWidth || 320, H = el.clientHeight || 240;
        var R = Math.min(W, H) / 2 - 18;
        var max = 1; data.forEach(function (v) { if (Math.abs(v) > max) max = Math.abs(v); });
        var theme = typeof getTheme === "function" ? getTheme() : { textSecondary: "#94a3b8" };
        var palette = _colorsOf(c);
        var pie = d3.pie().value(function (d) { return d; }).sort(null)(data.map(function (v) { return Math.abs(v); }));
        var arc = d3.arc().innerRadius(0).outerRadius(function (d) { return (d.data / max) * R; });
        var svg = d3.select(el).html("").attr("viewBox", "0 0 " + W + " " + H);
        var g = svg.append("g").attr("transform", "translate(" + W / 2 + "," + H / 2 + ")");
        g.selectAll("path").data(pie).enter().append("path").attr("d", arc)
          .attr("fill", function (d, i) { return palette[i % palette.length]; })
          .attr("stroke", "var(--bd-workspace,#fff)").attr("stroke-width", 1).attr("opacity", 0.9)
          .on("mouseenter", function (ev, d) { d3.select(this).attr("opacity", 1); if (window.Tooltip) Tooltip.show(Tooltip.multi([[labels[d.index] || "", d.data.toLocaleString()]]), ev); if (window.BlankDash && BlankDash._chartHover) BlankDash._chartHover(widget, labels[d.index], d.data, ev); })
          .on("mousemove", function (ev) { if (window.Tooltip) Tooltip.move(ev); })
          .on("mouseleave", function () { d3.select(this).attr("opacity", 0.9); if (window.Tooltip) Tooltip.hide(); if (window.BlankDash && BlankDash._hideReportTooltip) BlankDash._hideReportTooltip(); });
        g.selectAll("text").data(pie).enter().append("text")
          .attr("text-anchor", "middle")
          .attr("transform", function (d) { var rr = ((d.data / max) * R) * 0.55; var a = (d.startAngle + d.endAngle) / 2 - Math.PI / 2; return "translate(" + (rr * Math.cos(a)) + "," + (rr * Math.sin(a)) + ")"; })
          .each(function (d) {
            var v = labels[d.index] || "";
            var rr = ((d.data / max) * R) * 0.55;
            var chord = 2 * rr * Math.sin((d.endAngle - d.startAngle) / 2);
            var txt = chord < 12 ? "" : _clipText(v, Math.min(chord, 110), 10);
            var st = d3.select(this);
            st.text(txt);
            if (v && v !== txt) st.append("title").text(v);
          })
          .attr("fill", "#fff").attr("font-size", "10px").attr("font-weight", "600");
      }, 50);
    });

    /* Radial Bar — concentric ring bars (d3) */
    DashboardRenderer.register("radialbar", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!data.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      if (!window.d3) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Radial bar unavailable</div>'; return; }
      var nid = "rb_" + widget.id;
      container.innerHTML = '<div style="width:100%;height:100%;"><svg id="' + nid + '" style="width:100%;height:100%;min-height:140px;"></svg></div>';
      setTimeout(function () {
        var el = document.getElementById(nid);
        if (!el) return;
        var W = el.clientWidth || 320, H = el.clientHeight || 240;
        var maxR = Math.min(W, H) / 2 - 18;
        var max = 1; data.forEach(function (v) { if (Math.abs(v) > max) max = Math.abs(v); });
        var palette = _colorsOf(c);
        var theme = typeof getTheme === "function" ? getTheme() : { textSecondary: "#94a3b8" };
        var n = data.length, band = maxR / n;
        var a0 = -Math.PI / 2, aMax = Math.PI * 1.5;
        var items = data.map(function (v, i) { return { v: Math.abs(v), i: i }; });
        var svg = d3.select(el).html("").attr("viewBox", "0 0 " + W + " " + H);
        var g = svg.append("g").attr("transform", "translate(" + W / 2 + "," + H / 2 + ")");
        var track = function (d) { return d3.arc().innerRadius(band * d.i + 1.5).outerRadius(band * (d.i + 1) - 2).startAngle(a0).endAngle(a0 + aMax)(d); };
        var valArc = function (d) { return d3.arc().innerRadius(band * d.i + 1.5).outerRadius(band * (d.i + 1) - 2).startAngle(a0).endAngle(a0 + (d.v / max) * aMax)(d); };
        g.selectAll("path.tk").data(items).enter().append("path").attr("d", track).attr("fill", "var(--bd-chrome-border,rgba(15,23,42,0.10))");
        g.selectAll("path.vl").data(items).enter().append("path").attr("d", valArc).attr("fill", function (d) { return palette[d.i % palette.length]; }).attr("opacity", 0.92);
        g.selectAll("text.lb").data(items).enter().append("text")
          .attr("text-anchor", "middle").attr("dy", "0.35em").attr("fill", theme.textSecondary).attr("font-size", "9px")
          .attr("transform", function (d, i) {
            var ang = a0 + (i * (Math.PI * 2) / items.length);
            var rr = maxR + 10;
            return "translate(" + (rr * Math.cos(ang)) + "," + (rr * Math.sin(ang)) + ")";
          })
          .each(function (d, i) {
            var v = labels[d.i] || "";
            var rr = maxR + 10;
            var cap = Math.max(30, Math.min(96, (2 * Math.PI * rr / items.length) - 6));
            var txt = _clipText(v, cap, 9);
            var st = d3.select(this);
            st.text(txt);
            if (v && v !== txt) st.append("title").text(v);
          });
      }, 50);
    });

    /* Waffle — part-to-whole 10x10 grid (HTML) */
    DashboardRenderer.register("waffle", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!data.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      var palette = _colorsOf(c);
      var shares = data.map(function (v) { return Math.abs(v) || 0; });
      var sTotal = 0; shares.forEach(function (s) { sTotal += s; });
      if (!sTotal) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      var st6 = c.style || {};
      var gCount = (st6.waffleGrid === 64 || st6.waffleGrid === 40) ? st6.waffleGrid : 100;
      var gCols = Math.round(Math.sqrt(gCount));
      var cum = []; var acc = 0;
      shares.forEach(function (s, i) { acc += s; cum.push(acc); });
      var cw = container.clientWidth || 300, chh = container.clientHeight || 200;
      var gap = 2;
      var rows = Math.ceil(gCount / gCols);
      var cellWmax = Math.floor((cw - 28 - (gCols - 1) * gap) / gCols);
      var cellHmax = Math.floor((chh - 56 - (rows - 1) * gap) / rows);
      var cell = Math.max(8, Math.min(cellWmax, cellHmax, 26));
      var cells = "";
      for (var i = 0; i < gCount; i++) {
        var target = (sTotal * (i + 1)) / gCount;
        var b = shares.length - 1;
        for (var j = 0; j < shares.length; j++) { if (target <= cum[j]) { b = j; break; } }
        cells += '<div style="width:' + cell + 'px;height:' + cell + 'px;border-radius:' + Math.max(2, cell / 4) + 'px;background:' + palette[b % palette.length] + ';"></div>';
      }
      var legend = labels.map(function (lbl, i) {
        return '<div style="display:flex;align-items:center;gap:5px;font-size:10px;color:var(--color-content-text-secondary);"><span style="width:9px;height:9px;border-radius:2px;background:' + palette[i % palette.length] + ';display:inline-block;"></span>' + _esc(String(lbl)) + '</div>';
      }).join("");
      container.innerHTML = '<div style="padding:10px 14px;height:100%;overflow:auto;display:flex;flex-direction:column;justify-content:center;gap:10px;">' +
        '<div style="display:grid;grid-template-columns:repeat(' + gCols + ',' + cell + 'px);gap:' + gap + 'px;">' + cells + '</div>' +
        '<div style="display:flex;flex-wrap:wrap;gap:8px;">' + legend + '</div></div>';
    });

    /* Calendar Heatmap — weekday-ish grid colored by intensity */
    DashboardRenderer.register("calendar", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!labels.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      var palette = _colorsOf(c);
      var max = 1; data.forEach(function (v) { if (Math.abs(v) > max) max = Math.abs(v); });
      var n = Math.min(labels.length, 90);
      var colsC = Math.ceil(n / 7) || 1;
      var dayLetter = ["S", "M", "T", "W", "T", "F", "S"];
      var ava = container.clientWidth || 400, avh = container.clientHeight || 240;
      var gap = 3;
      var cellWm = Math.floor((ava - 22 - 14 - (colsC - 1) * gap) / colsC);
      var cellHm = Math.floor((avh - 36 - (7 - 1) * gap) / 7);
      var cell = Math.max(8, Math.min(cellWm, cellHm, 28));
      var cellStr = [];
      for (var i = 0; i < n; i++) {
        var v = Math.abs(Number(data[i])) || 0;
        var idx = v === 0 ? 0 : Math.max(1, Math.round((v / max) * (palette.length - 1)));
        cellStr.push('<div title="' + _esc(String(labels[i])) + ': ' + v.toLocaleString() + '" style="width:' + cell + 'px;height:' + cell + 'px;border-radius:' + Math.max(2, cell / 4) + 'px;background:' + (v === 0 ? 'transparent' : palette[idx]) + ';outline:1px solid ' + palette[idx] + ';opacity:' + (v === 0 ? 0.15 : 0.9) + ';"></div>');
      }
      var grid2 = "";
      for (var r2 = 0; r2 < 7; r2++) {
        var row2 = '<div style="width:16px;font-size:8px;line-height:' + cell + 'px;color:var(--color-content-text-secondary);text-align:center;flex:none;">' + dayLetter[r2] + '</div>';
        for (var g2 = 0; g2 < colsC; g2++) {
          var ci2 = g2 * 7 + r2;
          row2 += '<div style="height:' + cell + 'px;display:flex;align-items:center;justify-content:center;">' + (ci2 < n ? cellStr[ci2] : '<div style="width:' + cell + 'px;height:' + cell + 'px;"></div>') + '</div>';
        }
        grid2 += '<div style="display:flex;flex-direction:row;gap:' + gap + 'px;align-items:center;">' + row2 + '</div>';
      }
      container.innerHTML = '<div style="padding:10px 14px;height:100%;overflow:auto;display:flex;flex-direction:column;justify-content:center;gap:' + gap + 'px;">' + grid2 + '</div>';
    });

    /* Leaderboard — ranked rows with medals */
    DashboardRenderer.register("ranking", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!labels.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      var max = 1; data.forEach(function (v) { if (Math.abs(v) > max) max = Math.abs(v); });
      var st5 = c.style || {};
      var showMedals = st5.medals !== false && st5.medals !== "0";
      var medals = ["#EAB308", "#94A3B8", "#B45309"];
      var rows = labels.map(function (lbl, i) {
        var v = Number(data[i]) || 0;
        var rank = i + 1;
        var medal = (showMedals && rank <= 3) ? '<span style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:' + medals[rank - 1] + ';color:#fff;font-size:10px;font-weight:700;flex:none;">' + rank + '</span>' : '<span style="width:18px;font-size:10px;color:var(--color-content-text-secondary);text-align:center;flex:none;">' + rank + '</span>';
        return '<div style="display:flex;align-items:center;gap:8px;margin:6px 0;">' +
          medal +
          '<div style="flex:1;position:relative;height:14px;background:var(--bd-chrome-border,rgba(15,23,42,0.10));border-radius:5px;overflow:hidden;">' +
            '<div style="position:absolute;top:0;left:0;height:100%;width:' + Math.min(100, Math.round((Math.abs(v) / max) * 100)) + '%;border-radius:5px;background:linear-gradient(90deg,' + (medals[(rank - 1) % 3] || "#2563eb") + ',' + _adjustHex(medals[(rank - 1) % 3] || "#2563eb", -20) + ');"></div>' +
          '</div>' +
          '<div style="width:70px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;color:var(--color-content-text);font-weight:600;font-size:11px;">' + _esc(String(lbl)) + '</div>' +
          '<div style="width:56px;flex:none;color:var(--color-content-text);font-weight:700;font-size:11px;text-align:right;">' + v.toLocaleString() + '</div>' +
        '</div>';
      }).join("");
      container.innerHTML = '<div style="padding:12px 16px;height:100%;overflow:auto;display:flex;flex-direction:column;justify-content:center;">' + rows + '</div>';
    });

    /* Diverging Bar — positive/negative against a zero axis */
    DashboardRenderer.register("diverging", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!labels.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      var max = 1; data.forEach(function (v) { if (Math.abs(v) > max) max = Math.abs(v); });
      var st4 = c.style || {};
      var posC = st4.positiveColor || c.positiveColor || "#10B981";
      var negC = st4.negativeColor || c.negativeColor || "#b91c1c";
      var rows = labels.map(function (lbl, i) {
        var v = Number(data[i]) || 0;
        var pos = v >= 0;
        var w = Math.min(50, Math.round((Math.abs(v) / max) * 50));
        return '<div style="margin:7px 0;">' +
          '<div style="display:flex;justify-content:space-between;font-size:10px;color:var(--color-content-text-secondary);margin-bottom:3px;"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%;">' + _esc(String(lbl)) + '</span><span style="color:var(--color-content-text);font-weight:700;">' + v.toLocaleString() + '</span></div>' +
          '<div style="display:flex;align-items:center;height:10px;position:relative;background:repeating-linear-gradient(90deg,var(--bd-chrome-border,rgba(15,23,42,0.10)) 0 1px,transparent 1px 25%);">' +
            '<div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--bd-chrome-border,rgba(15,23,42,0.35));"></div>' +
            (pos
              ? '<div style="position:absolute;left:50%;top:0;height:100%;width:' + w + '%;background:linear-gradient(90deg,' + posC + ',' + _adjustHex(posC, -25) + ');border-radius:0 4px 4px 0;"></div>'
              : '<div style="position:absolute;right:50%;top:0;height:100%;width:' + w + '%;background:linear-gradient(270deg,' + negC + ',' + _adjustHex(negC, -25) + ');border-radius:4px 0 0 4px;"></div>') +
          '</div>' +
        '</div>';
      }).join("");
      container.innerHTML = '<div style="padding:12px 16px;height:100%;overflow:auto;display:flex;flex-direction:column;justify-content:center;">' + rows + '</div>';
    });

    /* Gantt Timeline — proportional horizontal segments */
    DashboardRenderer.register("gantt", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!labels.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      var max = 1; data.forEach(function (v) { if (Math.abs(v) > max) max = Math.abs(v); });
      var palette = _colorsOf(c);
      var ticks = [0, 0.25, 0.5, 0.75, 1].map(function (f) { return '<div style="flex:1;border-left:1px solid var(--bd-chrome-border,rgba(15,23,42,0.10));font-size:8px;color:var(--color-content-text-secondary);padding-left:2px;">' + (f * 100) + '%</div>'; }).join("");
      var rows = labels.map(function (lbl, i) {
        var v = Number(data[i]) || 0;
        var w = Math.max(3, Math.round((Math.abs(v) / max) * 100));
        return '<div style="margin:7px 0;">' +
          '<div style="display:flex;align-items:center;gap:8px;">' +
            '<div style="width:72px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;color:var(--color-content-text-secondary);text-align:right;">' + _esc(String(lbl)) + '</div>' +
            '<div style="flex:1;display:flex;height:14px;background:var(--bd-chrome-border,rgba(15,23,42,0.10));border-radius:7px;overflow:hidden;">' +
              '<div style="width:' + w + '%;background:linear-gradient(90deg,' + palette[i % palette.length] + ',' + _adjustHex(palette[i % palette.length], -25) + ');border-radius:7px;"></div>' +
            '</div>' +
            '<div style="width:52px;flex:none;text-align:right;font-size:10px;color:var(--color-content-text);font-weight:600;">' + v.toLocaleString() + '</div>' +
          '</div>' +
          '<div style="display:flex;margin:3px 0 0 80px;">' + ticks + '</div>' +
        '</div>';
      }).join("");
      container.innerHTML = '<div style="padding:12px 16px;height:100%;overflow:auto;display:flex;flex-direction:column;justify-content:center;">' + rows + '</div>';
    });

    /* Bubble Grid — grid of value-scaled circles */
    DashboardRenderer.register("bubblegrid", function (widget, container) {
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      if (!labels.length) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">No data</div>'; return; }
      var max = 1; data.forEach(function (v) { if (Math.abs(v) > max) max = Math.abs(v); });
      var palette = _colorsOf(c);
      var cols = Math.min(4, labels.length) || 1;
      var cells = "";
      for (var i = 0; i < labels.length && i < 12; i++) {
        var v = Number(data[i]) || 0;
        var pct = Math.max(0.15, Math.min(1, v / max));
        var diam = Math.round(34 * pct);
        cells += '<div style="display:flex;flex-direction:column;align-items:center;gap:4px;padding:6px;min-width:0;">' +
          '<div title="' + _esc(String(labels[i])) + ': ' + v.toLocaleString() + '" style="width:' + diam + 'px;height:' + diam + 'px;border-radius:50%;background:radial-gradient(circle at 30% 30%,rgba(255,255,255,0.35),' + palette[i % palette.length] + ');box-shadow:0 2px 8px rgba(0,0,0,0.18);display:flex;align-items:center;justify-content:center;color:#fff;font-size:9px;font-weight:700;flex:none;"></div>' +
          '<div style="font-size:9px;color:var(--color-content-text-secondary);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _esc(String(labels[i])) + '</div>' +
        '</div>';
      }
      container.innerHTML = '<div style="padding:12px 14px;height:100%;overflow:auto;display:grid;grid-template-columns:repeat(' + cols + ',1fr);align-items:center;justify-items:center;gap:4px;">' + cells + '</div>';
    });

    /* Water Tank — percentage fill gauge */
    DashboardRenderer.register("watertank", function (widget, container) {
      var c = widget.config || {};
      var actual = Number(c.actual != null ? c.actual : c.value);
      var target = Number(c.target || 100);
      if (isNaN(actual)) actual = 0;
      if (isNaN(target) || target === 0) target = 100;
      var pct = Math.min(100, Math.max(0, Math.round((actual / target) * 100)));
      var color = pct >= 100 ? "#15803d" : pct >= 50 ? "#1d4ed8" : "#2563eb";
      var label = c.label || "";
      container.innerHTML = '<div style="height:100%;display:flex;align-items:center;justify-content:center;gap:14px;padding:12px;">' +
        '<div style="position:relative;width:64px;height:92px;border:2px solid ' + color + ';border-radius:12px 12px 16px 16px;overflow:hidden;background:var(--bd-workspace,var(--color-surface-2));">' +
          '<div style="position:absolute;left:0;right:0;bottom:0;height:' + pct + '%;background:linear-gradient(180deg,rgba(255,255,255,0.3),' + color + ' 40%);transition:height 0.5s ease;"></div>' +
          '<div style="position:absolute;left:0;right:0;top:' + Math.max(0, 100 - pct - 8) + '%;height:6px;background:rgba(255,255,255,0.55);border-radius:3px;"></div>' +
          '<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--color-text);font-weight:800;font-size:15px;">' + pct + '%</div>' +
        '</div>' +
        '<div style="text-align:left;">' +
          '<div style="color:var(--color-content-text);font-weight:700;font-size:22px;line-height:1.2;">' + _esc(fmtNum(actual)) + '</div>' +
          '<div style="font-size:10px;color:var(--color-content-text-secondary);margin-top:3px;">' + _esc(label || "of " + fmtNum(target)) + '</div>' +
        '</div></div>';
    });

    /* KPI-family renderers (delegate to KPIStyles) */
    DashboardRenderer.register("radial", function (widget, container) {
      if (window.KPIStyles) {
        var cfg = Object.assign({}, widget.config || {});
        cfg.kpiStyle = "radial";
        KPIStyles.render(cfg, null, container);
      } else { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Radial Progress</div>'; }
    });

    DashboardRenderer.register("status", function (widget, container) {
      if (window.KPIStyles) {
        var c = widget.config || {};
        var cfg = Object.assign({}, c);
        cfg.kpiStyle = "status";
        cfg.status = c.status || "info";
        KPIStyles.render(cfg, null, container);
      } else { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Status Indicator</div>'; }
    });

    DashboardRenderer.register("comparison", function (widget, container) {
      if (window.KPIStyles) {
        var cfg = Object.assign({}, widget.config || {});
        cfg.kpiStyle = "comparison";
        if (!cfg.comparisonLabel) cfg.comparisonLabel = "Comparison";
        KPIStyles.render(cfg, null, container);
      } else { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Comparison KPI</div>'; }
    });

    DashboardRenderer.register("targetactual", function (widget, container) {
      if (window.KPIStyles) {
        var cfg = Object.assign({}, widget.config || {});
        cfg.kpiStyle = "targetActual";
        KPIStyles.render(cfg, null, container);
      } else { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Target vs Actual</div>'; }
    });

    /* ── New KPI-family styles (Basics) ── */
    function _kpiDelegate(style, widget, container, fallbackText, tune) {
      if (!window.KPIStyles) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">' + fallbackText + '</div>'; return; }
      var cfg = Object.assign({}, widget.config || {});
      if (cfg.kpiStyle !== 'plan') cfg.kpiStyle = style;
      cfg.id = widget.id;
      if (style === "previousPeriod") { if (cfg.previousValue == null) cfg.previousValue = cfg.comparisonValue || 0; }
      if (style === "icon" && cfg.icon == null) cfg.icon = "&#9733;";
      if (typeof tune === "function") tune(cfg);
      KPIStyles.render(cfg, null, container);
    }

    DashboardRenderer.register("trendkpi", function (widget, container) { _kpiDelegate("trend", widget, container, "Trend KPI", function (c) { if (c.comparisonValue == null) c.comparisonValue = 0; }); });
    DashboardRenderer.register("percentagekpi", function (widget, container) { _kpiDelegate("percentage", widget, container, "% Change KPI", function (c) { if (c.comparisonValue == null) c.comparisonValue = 0; }); });
    DashboardRenderer.register("prevkpi", function (widget, container) { _kpiDelegate("previousPeriod", widget, container, "Prev Period KPI"); });
    DashboardRenderer.register("iconkpi", function (widget, container) { _kpiDelegate("icon", widget, container, "Icon KPI"); });
    DashboardRenderer.register("multikpi", function (widget, container) {
      if (!window.KPIStyles) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Multi-Value KPI</div>'; return; }
      var c = widget.config || {};
      var labels = c.labels || [], data = c.data || [];
      var values = labels.map(function (l, i) { return { label: l, value: data[i] }; });
      if (!values.length) values = [{ label: c.label || "Value", value: c.value || 0 }];
      var cfg = Object.assign({}, c);
      cfg.kpiStyle = "multiValue";
      cfg.values = values;
      cfg.labels = labels;
      KPIStyles.render(cfg, null, container);
    });
    DashboardRenderer.register("sparkkpi", function (widget, container) {
      if (!window.KPIStyles) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Sparkline KPI</div>'; return; }
      var c = widget.config || {};
      var data = c.data || [];
      var total = 0; data.forEach(function (v) { total += Number(v) || 0; });
      var cfg = Object.assign({}, c);
      cfg.kpiStyle = "sparkline";
      cfg.id = widget.id;
      cfg.value = total || c.value || 0;
      cfg.sparklineData = data;
      KPIStyles.render(cfg, null, container);
    });
    DashboardRenderer.register("sparkbarkpi", function (widget, container) {
      if (!window.KPIStyles) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Mini Bar KPI</div>'; return; }
      var c = widget.config || {};
      var data = c.data || [];
      var total = 0; data.forEach(function (v) { total += Number(v) || 0; });
      var cfg = Object.assign({}, c);
      cfg.kpiStyle = "miniBar";
      cfg.value = total || c.value || 0;
      cfg.chartData = data;
      KPIStyles.render(cfg, null, container);
    });
    DashboardRenderer.register("minilinekpi", function (widget, container) {
      if (!window.KPIStyles) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Mini Line KPI</div>'; return; }
      var c = widget.config || {};
      var data = c.data || [];
      var total = 0; data.forEach(function (v) { total += Number(v) || 0; });
      var cfg = Object.assign({}, c);
      cfg.kpiStyle = "miniLine";
      cfg.id = widget.id;
      cfg.value = total || c.value || 0;
      cfg.sparklineData = data;
      KPIStyles.render(cfg, null, container);
    });
    DashboardRenderer.register("sparkdeltakpi", function (widget, container) {
      if (!window.KPIStyles) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Sparkline Delta KPI</div>'; return; }
      var c = widget.config || {};
      var data = c.data || [];
      var total = 0; data.forEach(function (v) { total += Number(v) || 0; });
      var cfg = Object.assign({}, c);
      cfg.kpiStyle = "sparklineDelta";
      cfg.id = widget.id;
      cfg.value = total || c.value || 0;
      cfg.sparklineData = data;
      if (cfg.previousValue == null && data.length > 1) cfg.previousValue = data[0];
      KPIStyles.render(cfg, null, container);
    });
    DashboardRenderer.register("donutkpi", function (widget, container) {
      if (!window.KPIStyles) { container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Donut KPI</div>'; return; }
      var cfg = Object.assign({}, widget.config || {});
      cfg.kpiStyle = "donut";
      cfg.id = widget.id;
      KPIStyles.render(cfg, null, container);
    });
  }

  /* ─── Pivot matrix (P2.5) ───
     Client-side pivot built from already-fetched rows of a "table" widget:
     config.pivot = { rowCol, colCol, valueCol, agg }. Returns
     { rowCol, colCol, valueCol, agg, rows, cols, grid[][] } or an error
     string for unusable configs (set on c.pivotError). */
  function _buildPivotMatrix(rows, pivot, cols) {
    var agg = (pivot.agg || "SUM").toUpperCase();
    var rc = pivot.rowCol, cc = pivot.colCol, vc = pivot.valueCol;
    if (!cols) cols = [];
    var colSet = {};
    cols.forEach(function (c) { colSet[String(c)] = true; });
    if (!colSet[rc] || !colSet[vc]) return { error: "Pivot fields must exist in the result columns" };
    var fmtV = function (v) { var n = Number(v); return isNaN(n) ? v : n; };
    var rowVals = [], rowSeen = {}, colVals = [], colSeen = {};
    rows.forEach(function (r) {
      var rv = String(r[rc] == null ? "" : r[rc]);
      var cv = String(r[cc] == null ? "" : r[cc]);
      if (!rowSeen[rv]) { rowSeen[rv] = true; rowVals.push(rv); }
      if (!colSeen[cv]) { colSeen[cv] = true; colVals.push(cv); }
    });
    if (rowVals.length > 200) return { error: rowVals.length + " distinct \u201C" + rc + "\u201D values \u2014 too many for a readable pivot" };
    rowVals.sort(); colVals.sort();
    var grid = {};
    rows.forEach(function (r) {
      var key = String(r[rc] == null ? "" : r[rc]) + "\u0001" + String(r[cc] == null ? "" : r[cc]);
      var v = Number(r[vc]);
      if (isNaN(v)) return;
      grid[key] = grid[key] || [];
      grid[key].push(v);
    });
    var solve = function (list) {
      if (!list || !list.length) return 0;
      if (agg === "SUM") return list.reduce(function (a, b) { return a + b; }, 0);
      if (agg === "AVG") return list.reduce(function (a, b) { return a + b; }, 0) / list.length;
      if (agg === "MAX") return Math.max.apply(null, list);
      if (agg === "MIN") return Math.min.apply(null, list);
      if (agg === "COUNT") return list.length;
      return list.reduce(function (a, b) { return a + b; }, 0);
    };
    var colsT = colVals.map(function (cv) {
      return rowVals.map(function (rv) { return solve(grid[rv + "\u0001" + cv]); });
    });
    return { rowCol: rc, colCol: cc, valueCol: vc, agg: agg, rows: rowVals, cols: colVals, grid: grid, solve: solve, colsT: colsT, fmt: fmtV };
  }

  window.BlankDash = BlankDash;
  (window._PAGES = window._PAGES || {})[PAGE] = BlankDash;
})();
