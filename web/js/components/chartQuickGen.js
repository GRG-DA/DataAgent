/**
 * ChartQuickGen — Quick chart generation wizard as a modal
 */
(function () {
  "use strict";

  function _open(table, preSelectedCols) {
    var html = '<div class="text-center py-4"><i class="fas fa-spinner fa-spin"></i><p class="text-xs mt-2">Loading columns...</p></div>';
    var modal = Modal.show("Quick Chart: " + _esc(table), html, { size: "lg" });

    API.profile(table).then(function(profile) {
      if (!profile || !profile.success) throw new Error("Profile load failed");
      var cols = (profile.profile && profile.profile.columns) || profile.columns || [];
      var numCols = cols.filter(function(c) { return c.mean !== undefined; });
      var catCols = cols.filter(function(c) { return c.mean === undefined; });

      var html2 = '<div class="space-y-3 text-sm">';
      // Column selection
      html2 += '<div><label class="form-label">X-Axis (categories)</label><select id="cqg-x" class="form-select">';
      catCols.forEach(function(c) { html2 += '<option value="' + _esc(c.name) + '">' + _esc(c.name) + '</option>'; });
      if (catCols.length === 0) { cols.forEach(function(c) { html2 += '<option value="' + _esc(c.name) + '">' + _esc(c.name) + '</option>'; }); }
      html2 += '</select></div>';

      html2 += '<div><label class="form-label">Y-Axis (values)</label><select id="cqg-y" class="form-select">';
      numCols.forEach(function(c) { html2 += '<option value="' + _esc(c.name) + '">' + _esc(c.name) + '</option>'; });
      if (numCols.length === 0) { cols.forEach(function(c) { html2 += '<option value="' + _esc(c.name) + '">' + _esc(c.name) + '</option>'; }); }
      html2 += '</select></div>';

      // Chart type picker
      html2 += '<div><label class="form-label">Chart Type</label><div class="grid grid-cols-3 gap-2" id="cqg-types">';
      var types = [
        { id: "bar", icon: "fa-chart-bar", label: "Bar" },
        { id: "line", icon: "fa-chart-line", label: "Line" },
        { id: "pie", icon: "fa-chart-pie", label: "Pie" },
        { id: "doughnut", icon: "fa-circle", label: "Doughnut" },
        { id: "scatter", icon: "fa-dot-circle", label: "Scatter" },
        { id: "area", icon: "fa-chart-area", label: "Area" },
      ];
      types.forEach(function(t, i) {
        html2 += '<button class="cqg-type-btn p-2 border border-gray-200 dark:border-gray-700 rounded-lg text-center hover:bg-gray-50 dark:hover:bg-gray-800 ' + (i === 0 ? 'ring-2 ring-primary-500' : '') + '" data-type="' + t.id + '"><i class="fas ' + t.icon + ' text-primary-500 text-lg"></i><div class="text-[10px] mt-0.5">' + t.label + '</div></button>';
      });
      html2 += '</div></div>';

      // Preview canvas
      html2 += '<div class="border-t dark:border-gray-700 pt-2"><canvas id="cqg-preview" style="width:100%;height:200px;"></canvas></div>';

      // Actions
      html2 += '<div class="flex gap-2 pt-2 border-t dark:border-gray-700">';
      html2 += '<button id="cqg-export" class="btn btn-sm btn-secondary"><i class="fas fa-download mr-1"></i>Export PNG</button>';
      html2 += '<button id="cqg-dashboard" class="btn btn-sm btn-primary"><i class="fas fa-plus mr-1"></i>Add to Dashboard</button>';
      html2 += '</div></div>';

      modal.close();
      modal = Modal.show("Quick Chart: " + _esc(table), html2, { size: "lg" });

      // Type selection
      var selectedType = "bar";
      document.querySelectorAll(".cqg-type-btn").forEach(function(btn) {
        btn.addEventListener("click", function() {
          document.querySelectorAll(".cqg-type-btn").forEach(function(b) { b.classList.remove("ring-2", "ring-primary-500"); });
          this.classList.add("ring-2", "ring-primary-500");
          selectedType = this.dataset.type;
          _renderPreview(table, selectedType, document.getElementById("cqg-x"), document.getElementById("cqg-y"), modal);
        });
      });

      // Axis change = re-render
      document.getElementById("cqg-x").addEventListener("change", function() { _renderPreview(table, selectedType, this, document.getElementById("cqg-y"), modal); });
      document.getElementById("cqg-y").addEventListener("change", function() { _renderPreview(table, selectedType, document.getElementById("cqg-x"), this, modal); });

      // Export PNG
      document.getElementById("cqg-export").addEventListener("click", function() {
        if (window.Charts && window.Charts.exportPng) Charts.exportPng("cqg-preview", table + "_chart.png");
      });

      // Initial render
      _renderPreview(table, selectedType, document.getElementById("cqg-x"), document.getElementById("cqg-y"), modal);
    }).catch(function(err) {
      modal.close();
      App.error("Quick chart failed: " + (err.message || "Error"));
    });
  }

  function _renderPreview(table, type, xSel, ySel, modal) {
    var xCol = xSel ? xSel.value : "";
    var yCol = ySel ? ySel.value : "";
    if (!xCol || !yCol) return;

    API.tableSample(table, 20).then(function(data) {
      if (!data || !data.rows) return;
      var labels = data.rows.map(function(r) { return r[xCol] || ""; });
      var values = data.rows.map(function(r) { return parseFloat(r[yCol]) || 0; });

      var fn = window.Charts[type];
      if (fn && labels.length > 0) {
        if (type === "scatter") {
          var pts = labels.map(function(l, i) { return { x: i, y: values[i] }; });
          Charts.destroy("cqg-preview");
          fn("cqg-preview", pts, { maintainAspectRatio: false });
        } else if (type === "pie" || type === "doughnut") {
          Charts.destroy("cqg-preview");
          fn("cqg-preview", labels, values, { maintainAspectRatio: false });
        } else {
          Charts.destroy("cqg-preview");
          fn("cqg-preview", labels, [{ data: values, label: yCol }], { maintainAspectRatio: false });
        }
      }
    }).catch(function() {});
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div"); d.appendChild(document.createTextNode(str)); return d.innerHTML;
  }

  window.ChartQuickGen = { open: _open };
})();
