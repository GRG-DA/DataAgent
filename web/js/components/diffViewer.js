/**
 * DiffViewer — Side-by-side diff visualization component
 */
(function () {
  "use strict";

  function _render(containerId, beforeData, afterData, options) {
    var container = typeof containerId === "string" ? document.getElementById(containerId) : containerId;
    if (!container) return;
    options = options || {};
    var columns = options.columns || (beforeData && beforeData.length > 0 ? Object.keys(beforeData[0]) : []);
    var keyColumn = options.keyColumn || columns[0];
    var mode = options.mode || "row";
    var maxRows = options.maxRows || 1000;

    if (!beforeData || !afterData || beforeData.length === 0 || afterData.length === 0) {
      container.innerHTML = '<div class="empty-state" style="padding:20px"><p>Need two datasets to compare</p></div>';
      return;
    }

    // Build row index by key
    var beforeMap = {}, afterMap = {};
    beforeData.forEach(function(r, i) { beforeMap[r[keyColumn]] = { row: r, idx: i }; });
    afterData.forEach(function(r, i) { afterMap[r[keyColumn]] = { row: r, idx: i }; });

    var added = [], removed = [], modified = [], unchanged = [];
    Object.keys(afterMap).forEach(function(key) {
      if (beforeMap[key]) {
        var bRow = beforeMap[key].row, aRow = afterMap[key].row;
        var changed = false;
        for (var i = 0; i < columns.length; i++) {
          if (String(bRow[columns[i]] || "") !== String(aRow[columns[i]] || "")) { changed = true; break; }
        }
        if (changed) modified.push({ key: key, before: bRow, after: aRow });
        else unchanged.push({ key: key, before: bRow, after: aRow });
      } else {
        added.push({ key: key, before: null, after: afterMap[key].row });
      }
    });
    Object.keys(beforeMap).forEach(function(key) {
      if (!afterMap[key]) removed.push({ key: key, before: beforeMap[key].row, after: null });
    });

    // Truncate if too many
    var totalChanges = added.length + removed.length + modified.length;
    var truncated = totalChanges > maxRows;
    if (truncated) {
      added = added.slice(0, maxRows);
      modified = modified.slice(0, maxRows);
    }

    // Summary bar
    var total = added.length + removed.length + modified.length + unchanged.length;
    var addedPct = total > 0 ? (added.length / total * 100).toFixed(1) : 0;
    var removedPct = total > 0 ? (removed.length / total * 100).toFixed(1) : 0;
    var modifiedPct = total > 0 ? (modified.length / total * 100).toFixed(1) : 0;

    var html = '<div class="space-y-3 text-sm">';

    // Summary
    if (truncated) html += '<div class="bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300 text-xs p-2 rounded-lg">Showing first ' + maxRows + ' of ' + totalChanges + ' changes</div>';

    // Summary bar
    html += '<div class="flex h-4 rounded-full overflow-hidden text-[9px] font-medium">';
    if (added.length > 0) html += '<div class="bg-green-400 flex items-center justify-center text-white" style="width:' + addedPct + '%">+' + added.length + '</div>';
    if (removed.length > 0) html += '<div class="bg-red-400 flex items-center justify-center text-white" style="width:' + removedPct + '%">-' + removed.length + '</div>';
    if (modified.length > 0) html += '<div class="bg-amber-400 flex items-center justify-center text-white" style="width:' + modifiedPct + '%">~' + modified.length + '</div>';
    html += '</div>';

    // Summary stats
    html += '<div class="flex gap-3 text-xs">';
    html += '<span class="text-green-600"><i class="fas fa-plus-circle"></i> ' + added.length + ' added</span>';
    html += '<span class="text-red-600"><i class="fas fa-minus-circle"></i> ' + removed.length + ' removed</span>';
    html += '<span class="text-amber-600"><i class="fas fa-pencil"></i> ' + modified.length + ' modified</span>';
    html += '<span class="text-gray-400"><i class="fas fa-equals"></i> ' + unchanged.length + ' unchanged</span>';
    html += '</div>';

    if (total === 0) {
      html += '<div class="text-center py-4 text-gray-400">No data to compare</div>';
    } else if (added.length === 0 && removed.length === 0 && modified.length === 0) {
      html += '<div class="text-center py-4 text-green-500"><i class="fas fa-check-circle text-lg"></i><p class="mt-1">Datasets are identical</p></div>';
    } else {
      // Side-by-side diff table
      html += '<div class="overflow-x-auto border dark:border-gray-700 rounded-lg">';
      html += '<table class="data-table w-full text-xs">';
      html += '<thead><tr><th style="width:40px"></th>';
      columns.forEach(function(c) { html += '<th>' + _esc(c) + '</th>'; });
      html += '<th style="width:40px"></th>';
      columns.forEach(function(c) { html += '<th>' + _esc(c) + '</th>'; });
      html += '</tr></thead><tbody>';

      // Added rows
      added.forEach(function(item) {
        html += '<tr class="bg-green-50 dark:bg-green-900/10">';
        html += '<td class="text-green-600 font-bold text-center">+</td>';
        columns.forEach(function(c) { html += '<td></td>'; });
        html += '<td></td>';
        columns.forEach(function(c) { html += '<td class="font-medium">' + _esc(String(item.after[c] || "")) + '</td>'; });
        html += '</tr>';
      });

      // Removed rows
      removed.forEach(function(item) {
        html += '<tr class="bg-red-50 dark:bg-red-900/10">';
        html += '<td class="text-red-600 font-bold text-center">-</td>';
        columns.forEach(function(c) { html += '<td class="font-medium">' + _esc(String(item.before[c] || "")) + '</td>'; });
        html += '<td></td>';
        columns.forEach(function(c) { html += '<td></td>'; });
        html += '</tr>';
      });

      // Modified rows
      modified.forEach(function(item) {
        html += '<tr class="bg-amber-50 dark:bg-amber-900/10">';
        html += '<td class="text-amber-600 font-bold text-center">~</td>';
        columns.forEach(function(c) {
          var bVal = String(item.before[c] || ""), aVal = String(item.after[c] || "");
          var changed = bVal !== aVal;
          html += '<td class="' + (changed ? 'bg-red-100 dark:bg-red-900/20 line-through text-red-600' : '') + '">' + _esc(bVal) + '</td>';
        });
        html += '<td></td>';
        columns.forEach(function(c) {
          var bVal = String(item.before[c] || ""), aVal = String(item.after[c] || "");
          var changed = bVal !== aVal;
          html += '<td class="' + (changed ? 'bg-green-100 dark:bg-green-900/20 font-medium' : '') + '">' + _esc(aVal) + '</td>';
        });
        html += '</tr>';
      });

      html += '</tbody></table></div>';
    }

    html += '</div>';
    container.innerHTML = html;
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div"); d.appendChild(document.createTextNode(str)); return d.innerHTML;
  }

  window.DiffViewer = { render: _render };
})();
