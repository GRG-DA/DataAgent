/**
 * KpiCompareCard — Side-by-side KPI comparison card with delta + sparkline
 */
(function () {
  "use strict";

  function _render(containerId, opts) {
    var container = typeof containerId === "string" ? document.getElementById(containerId) : containerId;
    if (!container) return;
    opts = opts || {};
    var label = opts.label || "KPI";
    var before = opts.before !== undefined ? opts.before : 0;
    var after = opts.after !== undefined ? opts.after : 0;
    var pValue = opts.pValue;
    var sparklineData = opts.sparklineData;

    var delta = after - before;
    var pctChange = before !== 0 ? ((delta / Math.abs(before)) * 100) : 0;
    var isUp = delta > 0;
    var isDown = delta < 0;
    var arrowClass = isUp ? "fa-arrow-up text-green-500" : isDown ? "fa-arrow-down text-red-500" : "fa-minus text-gray-400";
    var arrowColor = isUp ? "#22c55e" : isDown ? "#ef4444" : "#9ca3af";

    // Significance
    var sigHtml = "";
    if (pValue !== undefined) {
      var isSig = pValue < 0.05;
      sigHtml = '<span class="' + (isSig ? 'text-green-600' : 'text-gray-400') + ' text-[10px] ml-1">' + (isSig ? 'Significant' : 'Not significant') + (pValue ? ' (p=' + pValue.toFixed(3) + ')' : '') + '</span>';
    }

    // Format numbers
    function fmt(n) { return typeof n === "number" ? (n >= 1000000 ? (n/1000000).toFixed(1)+"M" : n >= 1000 ? (n/1000).toFixed(1)+"K" : n.toLocaleString()) : n; }

    container.innerHTML =
      '<div class="card" style="padding:12px;">' +
      '<div class="text-xs font-medium text-gray-500 mb-1">' + _esc(label) + '</div>' +
      '<div class="flex items-center gap-3">' +
      '<div class="flex-1 text-center"><div class="text-[10px] text-gray-400">Before</div><div class="text-lg font-bold">' + fmt(before) + '</div></div>' +
      '<div class="flex-1 text-center"><div class="text-[10px] text-gray-400">After</div><div class="text-lg font-bold">' + fmt(after) + '</div></div>' +
      '<div class="text-center px-2"><div class="text-[10px] text-gray-400">Change</div>' +
      '<div class="text-sm font-bold" style="color:' + arrowColor + '"><i class="fas ' + arrowClass + ' mr-0.5"></i>' + (delta >= 0 ? "+" : "") + delta.toLocaleString() + ' (' + (pctChange >= 0 ? "+" : "") + pctChange.toFixed(1) + '%)</div>' +
      sigHtml +
      '</div></div>';

    // Sparkline
    if (sparklineData && sparklineData.length > 0 && window.Charts) {
      var sid = "spark_kpi_" + Math.random().toString(36).slice(2, 6);
      var spEl = document.createElement("canvas");
      spEl.id = sid;
      spEl.style.cssText = "width:100%;height:30px;margin-top:6px;";
      container.appendChild(spEl);
      setTimeout(function() { Charts.sparkline(sid, sparklineData, { color: arrowColor }); }, 50);
    }
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div"); d.appendChild(document.createTextNode(str)); return d.innerHTML;
  }

  window.KpiCompareCard = { render: _render };
})();
