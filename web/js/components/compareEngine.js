/**
 * CompareEngine — Shared comparison logic for the Compare page
 */
(function () {
  "use strict";

  var _profileCache = {};

  async function loadComparison(tableBefore, tableAfter) {
    var p1 = _profileCache[tableBefore] || (await API.profile(tableBefore));
    var p2 = _profileCache[tableAfter] || (await API.profile(tableAfter));
    _profileCache[tableBefore] = p1;
    _profileCache[tableAfter] = p2;

    var deltas = computeDeltas(p1, p2);
    return { before: p1, after: p2, deltas: deltas };
  }

  function computeDeltas(profileBefore, profileAfter) {
    var cols1 = (profileBefore && profileBefore.columns) || [];
    var cols2 = (profileAfter && profileAfter.columns) || [];
    var colMap1 = {}, colMap2 = {};
    cols1.forEach(function(c) { colMap1[c.name] = c; });
    cols2.forEach(function(c) { colMap2[c.name] = c; });

    var allNames = new Set();
    cols1.forEach(function(c) { allNames.add(c.name); });
    cols2.forEach(function(c) { allNames.add(c.name); });

    var deltas = [];
    allNames.forEach(function(name) {
      var c1 = colMap1[name], c2 = colMap2[name];
      deltas.push({
        name: name,
        before: c1 || null,
        after: c2 || null,
        status: !c1 ? "added" : !c2 ? "removed" : "modified",
        typeChanged: c1 && c2 && c1.dtype !== c2.dtype,
        nullPctChange: c1 && c2 ? ((c2.null_pct || 0) - (c1.null_pct || 0)).toFixed(1) : null,
      });
    });
    return deltas;
  }

  function formatChange(value, decimals) {
    decimals = decimals !== undefined ? decimals : 2;
    if (typeof value !== "number") return String(value);
    return (value >= 0 ? "+" : "") + value.toFixed(decimals);
  }

  function getSignificanceBadge(pValue) {
    if (pValue === undefined || pValue === null) return "";
    var isSig = pValue < 0.05;
    return '<span class="inline-flex items-center text-[10px] px-1.5 py-0.5 rounded-full ' +
      (isSig ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400') +
      '"><i class="fas ' + (isSig ? 'fa-check-circle' : 'fa-minus-circle') + ' mr-0.5"></i>' +
      (isSig ? 'Significant' : 'Not sig') +
      (pValue ? ' (p=' + pValue.toFixed(3) + ')' : '') + '</span>';
  }

  function clearCache() { _profileCache = {}; }

  window.CompareEngine = { loadComparison: loadComparison, computeDeltas: computeDeltas, formatChange: formatChange, getSignificanceBadge: getSignificanceBadge, clearCache: clearCache };
})();
