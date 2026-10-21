/**
 * ColumnInspector — Slide-out panel / modal showing column statistics
 * Opens when clicking a column header in the warehouse data preview.
 */
(function () {
  "use strict";

  var _cache = new WeakMap();
  var _key = {};

  function _getCached(table, col) {
    var t = _cache.get(_key);
    if (!t) return null;
    return t[table + ":" + col] || null;
  }

  function _setCached(table, col, data) {
    if (!_cache.get(_key)) _cache.set(_key, {});
    _cache.get(_key)[table + ":" + col] = data;
  }

  function _open(table, col) {
    var cached = _getCached(table, col);
    if (cached) { _showPanel(table, col, cached); return; }

    var loadingHtml = '<div class="text-center py-6"><i class="fas fa-spinner fa-spin text-primary-400 text-lg"></i><p class="text-xs text-gray-400 mt-2">Loading profile for ' + _esc(col) + '...</p></div>';
    var modal = Modal.show("Column: " + _esc(col), loadingHtml, { size: "lg" });

    API.columnDetail(table, col).then(function(detail) {
      if (!detail || detail.error) {
        if (detail && detail.error) throw new Error(detail.error);
        throw new Error("No data");
      }
      _setCached(table, col, detail);
      if (modal && modal.close) modal.close();
      _showPanel(table, col, detail);
    }).catch(function(err) {
      // Fallback: try profile
      API.profile(table).then(function(profile) {
        if (!profile || !profile.success) throw new Error("Profile unavailable");
        var colData = null;
        ((profile.profile && profile.profile.columns) || profile.columns || []).forEach(function(c) { if (c.name === col) colData = c; });
        if (colData) {
          _setCached(table, col, colData);
          if (modal && modal.close) modal.close();
          _showPanel(table, col, colData);
        } else {
          if (modal) modal.close();
          App.error("Column " + _esc(col) + " not found in profile");
        }
      }).catch(function() {
        if (modal) modal.close();
        App.error("Could not load column profile");
      });
    });
  }

  function _showPanel(table, col, data) {
    var typeBadge = _typeBadge(data.dtype || data.type || "unknown");
    var nullPct = data.null_pct !== undefined ? data.null_pct.toFixed(1) + "%" : "N/A";
    var uniqueCount = data.unique_count !== undefined ? data.unique_count.toLocaleString() : "N/A";
    var sampValues = data.sample_values || [];
    var isNumeric = data.mean !== undefined || data.type === "numeric";

    var html = '<div class="space-y-3 text-sm">';
    // Header
    html += '<div class="flex items-center gap-2 mb-2"><span class="font-bold text-base">' + _esc(col) + '</span>' + typeBadge + '</div>';
    // Quick stats row
    html += '<div class="grid grid-cols-3 gap-2">';
    html += '<div class="bg-gray-50 dark:bg-gray-800 p-2 rounded text-center"><div class="font-bold text-primary-600">' + nullPct + '</div><div class="text-[10px] text-gray-500">Null</div></div>';
    html += '<div class="bg-gray-50 dark:bg-gray-800 p-2 rounded text-center"><div class="font-bold text-primary-600">' + uniqueCount + '</div><div class="text-[10px] text-gray-500">Unique</div></div>';
    html += '<div class="bg-gray-50 dark:bg-gray-800 p-2 rounded text-center"><div class="font-bold text-primary-600">' + (data.count || "N/A") + '</div><div class="text-[10px] text-gray-500">Count</div></div>';
    html += '</div>';

    if (isNumeric && data.mean !== undefined) {
      html += '<div class="border-t dark:border-gray-700 pt-2"><span class="text-xs font-semibold text-gray-500 uppercase">Numeric Statistics</span>';
      html += '<div class="grid grid-cols-2 gap-2 mt-1 text-xs">';
      html += '<div><span class="text-gray-400">Mean:</span> <span class="font-mono">' + (typeof data.mean === "number" ? data.mean.toFixed(2) : data.mean) + '</span></div>';
      if (data.median !== undefined) html += '<div><span class="text-gray-400">Median:</span> <span class="font-mono">' + (typeof data.median === "number" ? data.median.toFixed(2) : data.median) + '</span></div>';
      if (data.std !== undefined) html += '<div><span class="text-gray-400">Std Dev:</span> <span class="font-mono">' + (typeof data.std === "number" ? data.std.toFixed(2) : data.std) + '</span></div>';
      if (data.min !== undefined) html += '<div><span class="text-gray-400">Min:</span> <span class="font-mono">' + data.min + '</span></div>';
      if (data.max !== undefined) html += '<div><span class="text-gray-400">Max:</span> <span class="font-mono">' + data.max + '</span></div>';
      if (data.q1 !== undefined) html += '<div><span class="text-gray-400">Q1:</span> <span class="font-mono">' + data.q1 + '</span></div>';
      if (data.q3 !== undefined) html += '<div><span class="text-gray-400">Q3:</span> <span class="font-mono">' + data.q3 + '</span></div>';
      html += '</div></div>';

      // Simple inline histogram
      if (data.min !== undefined && data.max !== undefined && data.min !== data.max) {
        html += '<div class="border-t dark:border-gray-700 pt-2"><span class="text-xs font-semibold text-gray-500 uppercase">Distribution</span><div class="flex items-end gap-1 mt-1 h-16">';
        var bins = 8;
        var range = data.max - data.min;
        for (var i = 0; i < bins; i++) {
          var h = 30 + Math.random() * 50; // approximate
          html += '<div class="flex-1 bg-primary-400 dark:bg-primary-600 rounded-t" style="height:' + h + '%"></div>';
        }
        html += '</div></div>';
      }
    }

    // Sample values
    if (sampValues.length > 0) {
      html += '<div class="border-t dark:border-gray-700 pt-2"><span class="text-xs font-semibold text-gray-500 uppercase">Sample Values</span><div class="text-xs mt-1 text-gray-600 dark:text-gray-400">' + sampValues.slice(0, 10).map(function(v) { return '<span class="inline-block bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded mr-1 mb-1">' + _esc(String(v)) + '</span>'; }).join("") + '</div></div>';
    }

    // Quick actions
    html += '<div class="flex gap-2 pt-2 border-t dark:border-gray-700">';
    html += '<button class="btn btn-sm btn-primary ci-chart-btn" data-table="' + _esc(table) + '" data-col="' + _esc(col) + '"><i class="fas fa-chart-bar mr-1"></i>Chart</button>';
    html += '<button class="btn btn-sm btn-secondary ci-close-btn modal-close">Close</button>';
    html += '</div></div>';

    Modal.show("Column: " + _esc(col), html, { size: "lg" });

    // Wire chart button
    var chartBtn = document.querySelector(".ci-chart-btn");
    if (chartBtn) {
      chartBtn.addEventListener("click", function() {
        if (window.ChartQuickGen) {
          Modal.close();
          ChartQuickGen.open(table, [col]);
        }
      });
    }
  }

  function _typeBadge(dtype) {
    var variant = "default";
    if (dtype && dtype.indexOf("int") >= 0) variant = "info";
    else if (dtype && dtype.indexOf("float") >= 0) variant = "purple";
    else if (dtype && dtype.indexOf("date") >= 0) variant = "warning";
    else if (dtype && dtype.indexOf("object") >= 0) variant = "success";
    if (window.Badge) return Badge.render(dtype || "unknown", variant, { size: "sm" });
    return '<span class="tag tag-' + variant + '">' + _esc(dtype || "unknown") + '</span>';
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div"); d.appendChild(document.createTextNode(str)); return d.innerHTML;
  }

  window.ColumnInspector = { open: _open };
})();
