/**
 * DashboardRenderer — Renders individual widget types (KPI, chart, table, text)
 */
(function () {
  "use strict";

  var _renderers = {};

  function _register(type, fn) {
    _renderers[type] = fn;
  }

  function _render(widget, container) {
    var fn = _renderers[widget.type];
    if (fn) fn(widget, container);
    else container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Unknown widget type: ' + widget.type + '</div>';
  }

  function _attr(s) { return String(s == null ? "" : s).replace(/"/g, "&quot;").replace(/</g, "&lt;"); }

  /* Drill-through binding: clicking a marked data point navigates to the
     widget's drill page carrying the drill field as a filter. Bound once per
     container (flag survives re-renders). */
  function _bindDrillThrough(container, getPoint) {
    if (!container || container.__bdDrillBound) return;
    container.__bdDrillBound = true;
    container.addEventListener("click", function (e) {
      var el = e.target.closest(".bd-drillable[data-drill-page]");
      if (!el) return;
      e.stopPropagation();
      var page = el.getAttribute("data-drill-page");
      var field = el.getAttribute("data-drill-field") || "";
      var value = el.getAttribute("data-drill-value");
      if (!window.BlankDash || !window.BlankDash._drillThrough) return;
      window.BlankDash._drillThrough(page, field ? { field: field, value: value } : null);
    });
  }

  /** KPI Widget — solid card fills taken from the shared chart palette, so a
      KPI card and a chart series of the same slot carry the same colour.
      Flat fills, not gradients: the colour is data, the decoration is not. */
  var _KPI_FILLS = [
    "var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)",
    "var(--chart-5)", "var(--chart-6)", "var(--chart-7)", "var(--chart-8)",
  ];
  function _kpiGradient(idx) { return _KPI_FILLS[idx % _KPI_FILLS.length]; }

  /** Enhanced KPI renderer — delegates to KPIStyles when available, falls back to legacy */
  _register("kpi", function(widget, container) {
    var c = widget.config || {};

    /* Use the new KPIStyles system if loaded */
    if (window.KPIStyles && KPIStyles.render) {
      KPIStyles.render(c, null, container);
      return;
    }

    /* Legacy fallback — original KPI rendering (flat light card + small accent) */
    var val = c.value !== undefined ? c.value : "--";
    var label = c.label || "KPI";
    var trend = c.trend;
    var trendIcon = trend > 0 ? "fa-arrow-up" : trend < 0 ? "fa-arrow-down" : "fa-minus";
    var trendColor = trend > 0 ? "var(--bd-kpi-good,var(--color-success))" : trend < 0 ? "var(--bd-kpi-bad,var(--color-danger))" : "var(--bd-kpi-muted,var(--color-text-muted))";
    var acc = c.color || "var(--color-primary)";
    var bgCss = c.background
      ? ("background:" + c.background)
      : ("background:var(--bd-kpi-bg,#ffffff);border:1px solid var(--bd-kpi-line,rgba(15,23,42,0.08));" +
         "background-image:linear-gradient(var(--bd-kpi-accent," + acc + ") 0 3px,transparent 3px)");

    var displayVal;
    if (typeof val === "number" && !isNaN(val)) {
      displayVal = (typeof c.format === "string" && c.format) ? _fmt(val, c.format) : (val >= 1000000 ? (val / 1000000).toFixed(1) + "M" : val >= 1000 ? (val / 1000).toFixed(1) + "K" : val.toLocaleString());
    } else {
      displayVal = val;
    }

    container.innerHTML =
      '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding:10px 18px;text-align:center;' + bgCss + ';border-radius:12px;box-shadow:0 1px 2px rgba(15,23,42,0.05);">' +
      '<div style="font-size:calc(32px * var(--bd-scale, 1));font-weight:800;color:var(--bd-kpi-txt,var(--color-text));line-height:1.2;letter-spacing:-0.02em;">' + _esc(String(displayVal)) + '</div>' +
      '<div style="font-size:11px;color:var(--bd-kpi-txt-2,var(--color-text-secondary));margin-top:5px;font-weight:600;letter-spacing:0.02em;">' + _esc(label) + '</div>' +
      (trend !== undefined ? '<div style="font-size:10px;color:' + trendColor + ';margin-top:7px;background:var(--bd-kpi-track,rgba(15,23,42,0.08));border-radius:12px;padding:3px 10px;display:inline-flex;align-items:center;gap:3px;"><i class="fas ' + trendIcon + '"></i>' + (Math.abs(trend)).toFixed(1) + '%</div>' : '') +
      '</div>';
    if (c.sparklineData && window.Charts && window.Charts.sparkline) {
      var sid = "spark_" + widget.id;
      setTimeout(function() {
        var spEl = container.querySelector("#" + sid);
        if (!spEl) {
          var sp = document.createElement("canvas");
          sp.id = sid;
          sp.style.cssText = "width:100%;height:32px;margin-top:6px;opacity:0.85;border-radius:4px;";
          container.appendChild(sp);
          window.Charts.sparkline(sid, c.sparklineData, { color: "rgba(255,255,255,0.92)" });
        }
      }, 50);
    }
  });

  /* Reference lines / bands — injected as extra datasets so they render
     without the (unloaded) chartjs-plugin-annotation. A "line" becomes a
     dashed horizontal line; a "band" becomes a lower line + an upper line
     that fills back to the previous dataset (fill: { target: '-1' }). */
  function _refLineDatasets(c, labels) {
    var refs = c.referenceLines;
    if (!Array.isArray(refs) || !refs.length) return [];
    var n = (labels && labels.length) || 1;
    var out = [];
    refs.forEach(function (r) {
      if (!r || typeof r.value !== "number") return;
      // Chart.js dataset colour — a hex, because it is painted on a canvas.
      var col = r.color || "#b91c1c";
      if (r.type === "band") {
        var lo = Math.min(r.value, r.bandTo != null ? r.bandTo : r.value);
        var hi = Math.max(r.value, r.bandTo != null ? r.bandTo : r.value);
        out.push({
          type: "line",
          label: r.label || "Band",
          data: new Array(n).fill(lo),
          borderColor: col,
          borderWidth: 1,
          borderDash: [2, 2],
          pointRadius: 0,
          pointHoverRadius: 0,
          fill: false,
          borderRadius: 0,
          datalabels: { display: false }
        });
        out.push({
          type: "line",
          label: undefined,
          data: new Array(n).fill(hi),
          borderColor: col,
          borderWidth: 1,
          borderDash: [2, 2],
          pointRadius: 0,
          pointHoverRadius: 0,
          fill: { target: "-1" },
          backgroundColor: _hexToRgba(col, 0.15),
          borderRadius: 0,
          datalabels: { display: false }
        });
      } else {
        out.push({
          type: "line",
          label: r.label || "Reference",
          data: new Array(n).fill(r.value),
          borderColor: col,
          borderWidth: 1.5,
          borderDash: [6, 3],
          pointRadius: 0,
          pointHoverRadius: 0,
          fill: false,
          borderRadius: 0,
          datalabels: { display: false }
        });
      }
    });
    return out;
  }

  function _hexToRgba(hex, alpha) {
    var m = String(hex).replace("#", "").match(/^([0-9a-f]{6})$/i);
    if (!m) return "rgba(239,68,68," + alpha + ")";
    var v = parseInt(m[1], 16);
    return "rgba(" + ((v >> 16) & 255) + "," + ((v >> 8) & 255) + "," + (v & 255) + "," + alpha + ")";
  }

  /** Chart Widget — dispatches on config.chartType to feed the right data shape */
  _register("chart", function(widget, container) {
    var c = widget.config || {};
    var chartType = c.chartType || "bar";
    var labels = c.labels || [];
    var data = c.data || [];
    var datasets = c.datasets || [{ data: data }];
    /* Apply lineThickness, curveType, lineStyle, fillOpacity to datasets for line/area charts */
    if (chartType === "line" || chartType === "area" || chartType === "stepline" || chartType === "smoothline" || chartType === "smootharea" || chartType === "combo") {
      var thickness = parseInt(c.lineThickness, 10) || 2;
      var tensionMap = { smooth: 0.4, linear: 0, step: 0 };
      var curveKey = c.curveType || "smooth";
      var tension = tensionMap.hasOwnProperty(curveKey) ? tensionMap[curveKey] : 0.4;
      var stepped = curveKey === "step" ? "middle" : undefined;
      var lineStyleMap = { solid: undefined, dashed: [6, 3], dotted: [2, 2] };
      var borderDash = lineStyleMap[c.lineStyle] || undefined;
      var fillOp = (typeof c.fillOpacity === "number") ? c.fillOpacity / 100 : 0.3;
      var showPts = c.showDataPoints === true;
      var ptSize = c.dataPointSize || 4;
      datasets = datasets.map(function(ds) {
        var nds = Object.assign({}, ds);
        nds.borderWidth = thickness;
        nds.tension = tension;
        if (stepped !== undefined) nds.stepped = stepped;
        if (borderDash) nds.borderDash = borderDash;
        if (chartType === "area" || chartType === "smootharea" || c.showArea) {
          nds.fill = true;
          nds.backgroundColor = nds.backgroundColor || "rgba(59,130,246," + fillOp + ")";
        }
        nds.pointRadius = showPts ? ptSize : 0;
        nds.pointHoverRadius = showPts ? ptSize + 2 : 4;
        return nds;
      });
    }
    /* Reference lines / bands — appended as overlay datasets for cartesian types */
    var refDs = _refLineDatasets(c, labels);
    if (refDs.length) datasets = datasets.concat(refDs);
    var chartId = "chart_" + widget.id;
    /* Data-visualization accent (categorical blue) — the first slot of the
       shared chart palette. A hex, not var(): it is handed to Chart.js, which
       paints it on a canvas. */
    var accent = (window.StyleEngine && (StyleEngine.colors(c) || [])[0]) || c.color || ((window.Charts && Charts.PALETTE && Charts.PALETTE.categorical) ? Charts.PALETTE.categorical[0] : "#2563eb");
    var opts = (window.StyleEngine) ? StyleEngine.opts(c) : { maintainAspectRatio: false, plugins: { legend: { display: c.showLegend !== false } } };
    if (window.StyleEngine && StyleEngine.colors(c)) opts.colors = StyleEngine.colors(c);

    // ── Cross-filtering (Power BI): click a data point to filter the page ──
    // Applies to every Chart.js subtype. The handler resolves the clicked
    // label and delegates to BlankDash._chartClick, which dispatches through
    // CrossFilterBus (same path the slicer widgets use).
    if (c.crossFilter !== false && window.BlankDash && BlankDash._chartClick) {
      opts.onClick = function (evt, elements) {
        if (!elements || !elements.length) return;
        var el = elements[0];
        var lbl = (labels && labels[el.index] != null) ? labels[el.index] : null;
        if (lbl == null) return;
        BlankDash._chartClick(widget, lbl, evt);
      };
      opts.onHover = function (evt, elements, chart) {
        if (chart && chart.canvas) chart.canvas.style.cursor = (elements && elements.length) ? "pointer" : "default";
      };
    }

    // ── Heatmap (custom Canvas2D) ──
    if (chartType === "heatmap") {
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts) window.Charts.heatmap(chartId, c.matrix || [], { xLabels: c.xLabels || [], yLabels: c.yLabels || [], title: c.label });
      }, 50);
      return;
    }

    // ── Scatter / Bubble ──
    if (chartType === "bubble" || chartType === "scatter") {
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts) window.Charts[chartType](chartId, c.dataPoints || [], { color: accent, label: c.label || chartType });
      }, 50);
      return;
    }

    // ── Radar ──
    if (chartType === "radar") {
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts && window.Charts.radar) {
          window.Charts.radar(chartId, labels, datasets, opts);
        }
      }, 50);
      return;
    }

    // ── Sparkline (tiny inline chart) ──
    if (chartType === "sparkline") {
      container.innerHTML = '<div style="padding:16px 8px;height:100%;display:flex;align-items:center;"><canvas id="' + chartId + '" style="width:100%;height:60px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts && window.Charts.sparkline) {
          window.Charts.sparkline(chartId, data, { color: accent, fill: true });
        }
      }, 50);
      return;
    }

    // ── Gauge (SVG HTML) ──
    if (chartType === "gauge") {
      container.innerHTML = _gaugeHtml(c);
      return;
    }

    // ── Funnel (HTML trapezoids) ──
    if (chartType === "funnel") {
      container.innerHTML = _funnelHtml(c);
      return;
    }

    // ── Treemap (horizontal bar preview) ──
    if (chartType === "treemap") {
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts) window.Charts.horizontalBar(chartId, labels, data, opts);
      }, 50);
      return;
    }

    // ── Waterfall (floating bars) ──
    if (chartType === "waterfall") {
      var cum = 0;
      var bars = data.map(function(v) {
        var b = [cum, cum + v];
        cum += v;
        return b;
      });
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts) window.Charts.bar(chartId, labels, [{
          data: bars,
          backgroundColor: bars.map(function(b) { return b[1] >= b[0] ? "#00D4AA" : "#FF6B6B"; }),
        }], opts);
      }, 50);
      return;
    }

    // ── Combo (bar + line mixed) ──
    if (chartType === "combo") {
      /* datasets already includes refDs (appended above); slice them off so
         they are not duplicated, then re-append as full configs so mixed()
         preserves their styling (borderDash, fill, pointRadius, ...). */
      var base = refDs.length ? datasets.slice(0, datasets.length - refDs.length) : datasets;
      var configs = base.map(function(ds, i) {
        return { type: i === 0 ? "bar" : "line", label: ds.label || "", data: ds.data };
      });
      if (refDs.length) configs = configs.concat(refDs);
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts) window.Charts.mixed(chartId, labels, configs, opts);
      }, 50);
      return;
    }

    // ── Horizontal Bar ──
    if (chartType === "horizontalbar") {
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts) window.Charts.horizontalBar(chartId, labels, refDs.length ? datasets : data, opts);
      }, 50);
      return;
    }

    // ── Stacked Bar ──
    if (chartType === "stackedbar") {
      opts.scales = opts.scales || {};
      opts.scales.x = opts.scales.x || {}; opts.scales.x.stacked = true;
      opts.scales.y = opts.scales.y || {}; opts.scales.y.stacked = true;
      container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
      setTimeout(function() {
        if (window.Charts) window.Charts.bar(chartId, labels, datasets, opts);
      }, 50);
      return;
    }

    // ── Standard Chart.js types (line, bar, column, area, pie, doughnut) ──
    container.innerHTML = '<div style="padding:8px;height:100%;"><canvas id="' + chartId + '" style="width:100%;height:100%;min-height:150px;"></canvas></div>';
    setTimeout(function() {
      if (!window.Charts) return;
      if (chartType === "donut") chartType = "doughnut";
      /* Column charts are vertical bars (Charts has no dedicated "column" method) */
      if (chartType === "column") chartType = "bar";
      var fn = window.Charts[chartType];
      if (!fn) return;
      if (c.stacked) {
        opts.scales = opts.scales || {};
        opts.scales.x = opts.scales.x || {}; opts.scales.x.stacked = true;
        opts.scales.y = opts.scales.y || {}; opts.scales.y.stacked = true;
      }
      if (chartType === "pie" || chartType === "doughnut") {
        var dlPie = (window.StyleEngine) ? StyleEngine.dataLabels(c, true) : { show: true, position: "center" };
        opts.plugins = opts.plugins || {};
        opts.plugins.datalabels = {
          display: dlPie.show, anchor: "center", align: "center",
          color: "#fff", font: { size: 11, weight: "bold" },
          formatter: (window.StyleEngine) ? function (v) { return StyleEngine.fmt(c, v); } : function (v) { return v >= 1000000 ? (v / 1000000).toFixed(1) + "M" : v >= 1000 ? (v / 1000).toFixed(1) + "K" : v; },
        };
        fn(chartId, labels, data, opts);
      } else if (chartType === "bar" || chartType === "column") {
        var dlBar = (window.StyleEngine) ? StyleEngine.dataLabels(c, true) : { show: true, position: "end" };
        opts.plugins = opts.plugins || {};
        opts.plugins.datalabels = {
          display: dlBar.show, anchor: (dlBar.position === "center" ? "center" : "end"), align: (dlBar.position === "center" ? "center" : "top"),
          color: ((c.style && c.style.text) ? c.style.text : "#4B5563"), font: { size: 10, weight: "600" },
          formatter: (window.StyleEngine) ? function (v) { return StyleEngine.fmt(c, v); } : function (v) { return v >= 1000000 ? (v / 1000000).toFixed(1) + "M" : v >= 1000 ? (v / 1000).toFixed(1) + "K" : v; },
        };
        window.Charts.bar(chartId, labels, datasets, opts);
      } else {
        fn(chartId, labels, datasets, opts);
      }
    }, 50);
  });

  /** SVG semicircular gauge (preview approximation of the PBI gauge visual). */
  function _gaugeHtml(c) {
    var v = c.value;
    var val = (typeof v === "number" && !isNaN(v)) ? v : 0;
    var disp = typeof c.format === "string" ? _fmt(val, c.format) : (val >= 1e6 ? (val / 1e6).toFixed(1) + "M" : val >= 1e3 ? (val / 1e3).toFixed(1) + "K" : val.toLocaleString());
    var max = Math.max(100, Math.abs(val) * 1.25);
    var frac = Math.max(0, Math.min(1, val / max));
    var L = Math.PI * 50; // semicircle arc length (radius 50)
    var colour = c.color || ((window.Charts && Charts.PALETTE && Charts.PALETTE.categorical) ? Charts.PALETTE.categorical[0] : "var(--chart-1)");
    return '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding:6px 10px;overflow:hidden;">' +
      '<svg viewBox="0 0 120 70" style="height:56%;width:auto;aspect-ratio:120/70;max-width:100%;flex:none;">' +
        '<path d="M10 60 A 50 50 0 0 1 110 60" fill="none" stroke="#EEF0F2" stroke-width="12" stroke-linecap="round"/>' +
        '<path d="M10 60 A 50 50 0 0 1 110 60" fill="none" stroke="' + _esc(colour) + '" stroke-width="12" stroke-linecap="round" stroke-dasharray="' + (L * frac).toFixed(1) + ' ' + L.toFixed(1) + '"/>' +
      '</svg>' +
      '<div style="font-size:15px;font-weight:700;color:var(--color-content-text);line-height:1.15;">' + _esc(String(disp)) + '</div>' +
      '<div style="font-size:10.5px;color:var(--color-content-text-secondary);margin-top:2px;text-align:center;">' + _esc(c.label || "Gauge") + '</div>' +
    '</div>';
  }

  /** Centered-trapezoid funnel (preview approximation of the PBI funnel). */
  function _funnelHtml(c) {
    var labels = c.labels || [];
    var data = c.data || [];
    if (!labels.length || !data.length) return '<div class="text-center text-gray-400 text-xs py-4">No data</div>';
    var max = 1;
    for (var i = 0; i < data.length; i++) if (Math.abs(data[i]) > max) max = Math.abs(data[i]);
    var rows = labels.map(function(lbl, i) {
      var v = data[i];
      var w = Math.round((Math.abs(v) / max) * 92) + 2; // 2%..94%
      var disp = (typeof v === "number") ? (Math.round(v * 10) / 10).toLocaleString() : v;
      return '<div style="display:flex;justify-content:center;margin:2px 0;">' +
        '<div style="width:' + w + '%;background:linear-gradient(90deg,var(--chart-1),var(--chart-6));border-radius:6px;padding:5px 10px;display:flex;justify-content:space-between;align-items:center;color:#fff;font-size:11px;white-space:nowrap;overflow:hidden;box-shadow:0 2px 6px rgba(var(--color-primary-rgb),0.2);">' +
        '<span style="overflow:hidden;text-overflow:ellipsis;">' + _esc(String(lbl)) + '</span>' +
        '<span style="font-weight:700;margin-left:6px;">' + _esc(String(disp)) + '</span>' +
        '</div></div>';
    }).join("");
    return '<div style="padding:10px;height:100%;overflow:auto;">' + rows + '</div>';
  }

  function _fmt(v, fmt) {
    try {
      if (fmt.indexOf("%") !== -1) return (v * 100).toFixed(fmt.indexOf("0.") !== -1 ? 1 : 0) + "%";
      if (fmt.indexOf("$") !== -1) return "$" + v.toLocaleString(undefined, { maximumFractionDigits: 2 });
      if (fmt.indexOf(".") !== -1) {
        var dec = fmt.split(".")[1].length;
        return v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
      }
      return v.toLocaleString();
    } catch (e) { return String(v); }
  }

  /* ═══════════════════════════════════════════════════════════════════
     StyleEngine — shared per-widget styling helpers.
     Reads the widget's config.style (Format tab) + number formatting keys
     (prefix/suffix/decimals/abbrev/format) and turns them into Chart.js
     options and formatted value strings. Used by every renderer so style
     edits made in the editor Apply live to all widgets.
  ═══════════════════════════════════════════════════════════════════ */
  function _seColors(c) {
    if (c && c.style && Array.isArray(c.style.colors) && c.style.colors.length) return c.style.colors;
    if (c && Array.isArray(c.colors) && c.colors.length) return c.colors;
    /* Flat config paths from PropsPanel (e.g. themePrimary, positiveColor) */
    if (c && c.themePrimary) return [c.themePrimary];
    return null;
  }

  function _seFormat(c) {
    c = c || {};
    return {
      prefix: c.prefix || "",
      suffix: c.suffix || "",
      decimals: (typeof c.decimals === "number") ? c.decimals : null,
      abbrev: !(c.abbrev === false || c.abbrev === "0"),
      format: c.format || "",
    };
  }

  /** Format a numeric value per the widget's config (prefix/suffix/decimals/KMB/format). */
  function _seFmt(c, v) {
    if (v == null) return "";
    if (typeof v !== "number" || isNaN(v)) return String(v);
    var f = _seFormat(c);
    var n = v, out;
    if (f.format) {
      try {
        if (f.format.indexOf("%") !== -1) out = (n * 100).toFixed(f.format.indexOf("0.") !== -1 ? 1 : 0) + "%";
        else if (f.format.indexOf("$") !== -1) out = "$" + n.toLocaleString(undefined, { maximumFractionDigits: f.decimals != null ? f.decimals : 2 });
        else if (f.format.indexOf(".") !== -1) { var d = f.format.split(".")[1].length; out = n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }); }
        else out = n.toLocaleString();
        return f.prefix + out + f.suffix;
      } catch (e) { out = String(n); }
    }
    if (f.abbrev) {
      if (Math.abs(n) >= 1e9) out = (n / 1e9).toFixed(f.decimals != null ? f.decimals : 1) + "B";
      else if (Math.abs(n) >= 1e6) out = (n / 1e6).toFixed(f.decimals != null ? f.decimals : 1) + "M";
      else if (Math.abs(n) >= 1e3) out = (n / 1e3).toFixed(f.decimals != null ? f.decimals : 1) + "K";
      else out = (f.decimals != null)
        ? n.toLocaleString(undefined, { minimumFractionDigits: f.decimals, maximumFractionDigits: f.decimals })
        : n.toLocaleString();
    } else {
      out = (f.decimals != null)
        ? n.toLocaleString(undefined, { minimumFractionDigits: f.decimals, maximumFractionDigits: f.decimals })
        : n.toLocaleString();
    }
    return f.prefix + out + f.suffix;
  }

  /** Build Chart.js options from config.style: legend, data labels, axes, gridlines. */
  function _seOpts(c) {
    c = c || {};
    var st = c.style || {};
    var legend = st.legend || {};
    var labels = st.dataLabels || {};
    var axis = st.axis || {};
    var o = {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: legend.show !== false && c.showLegend !== false && c.legendShow !== false,
                  position: legend.position || c.legendPosition || "top" },
      },
    };
    if (typeof labels.show === "boolean") {
      o.plugins.datalabels = {
        display: labels.show,
        position: labels.position || "end",
        anchor: (labels.position === "center" || labels.position === "insideEnd" || labels.position === "insideBase") ? "center" : "end",
        align: (labels.position === "center") ? "center" : "top",
        font: { size: 10, weight: "600" },
        color: (st.colors && st.colors.length) ? st.colors[0] : undefined,
        formatter: function (c2) { return function (v) { return _seFmt(c2, v); }; }(c),
      };
    }
    o.scales = {};
    var _showGrid = (typeof axis.showGrid === "boolean") ? axis.showGrid
      : (typeof c.showGrid === "boolean") ? c.showGrid : undefined;
    if (typeof _showGrid === "boolean") {
      o.scales.y = { grid: { display: _showGrid } };
      o.scales.x = o.scales.x || {};
      o.scales.x.grid = o.scales.x.grid || {};
      o.scales.x.grid.display = _showGrid;
    }
    if (axis.categoryTitle || c.xAxisTitle) {
      o.scales.x = o.scales.x || {};
      o.scales.x.title = { display: true, text: axis.categoryTitle || c.xAxisTitle };
    }
    if (axis.valueTitle || c.yAxisTitle) {
      o.scales.y = o.scales.y || {};
      o.scales.y.title = { display: true, text: axis.valueTitle || c.yAxisTitle };
    }
    /* Widget-scoped theme (from the Studio wizard via config.style.themeId):
       make axis ticks, gridlines and the legend readable on the tinted card
       while the rest of the app stays on its default light look. */
    if (st.themeId) {
      var _txt = st.text || "#0f172a";
      var _txt2 = st.textSecondary || "#64748b";
      var _grid = st.gridLine || "rgba(100,116,139,0.12)";
      ["x", "y"].forEach(function (k) {
        o.scales[k] = o.scales[k] || {};
        o.scales[k].ticks = o.scales[k].ticks || {};
        o.scales[k].ticks.color = _txt2;
        o.scales[k].grid = o.scales[k].grid || {};
        o.scales[k].grid.color = _grid;
      });
      o.plugins.legend = o.plugins.legend || {};
      o.plugins.legend.labels = o.plugins.legend.labels || {};
      o.plugins.legend.labels.color = _txt;
    }
    /* ── Flat config overrides from PropsPanel (chart-level settings) ── */
    /* Grid line color, style, width */
    var _glColor = c.gridLineColor;
    var _glStyle = c.gridLineStyle;
    var _glWidth = c.gridLineWidth;
    if (_glColor || _glStyle || _glWidth) {
      ["x", "y"].forEach(function (k) {
        o.scales[k] = o.scales[k] || {};
        o.scales[k].grid = o.scales[k].grid || {};
        if (_glColor) o.scales[k].grid.color = _glColor;
        if (_glWidth) o.scales[k].grid.lineWidth = _glWidth;
        if (_glStyle === "dashed") o.scales[k].grid.borderDash = [6, 3];
        else if (_glStyle === "dotted") o.scales[k].grid.borderDash = [2, 2];
      });
    }
    /* Axis label colors and sizes */
    if (c.xAxisLabelColor || c.xAxisLabelSize) {
      o.scales.x = o.scales.x || {};
      o.scales.x.ticks = o.scales.x.ticks || {};
      if (c.xAxisLabelColor) o.scales.x.ticks.color = c.xAxisLabelColor;
      if (c.xAxisLabelSize) o.scales.x.ticks.font = { size: c.xAxisLabelSize };
    }
    if (c.yAxisLabelColor || c.yAxisLabelSize) {
      o.scales.y = o.scales.y || {};
      o.scales.y.ticks = o.scales.y.ticks || {};
      if (c.yAxisLabelColor) o.scales.y.ticks.color = c.yAxisLabelColor;
      if (c.yAxisLabelSize) o.scales.y.ticks.font = { size: c.yAxisLabelSize };
    }
    /* Data labels (flat config path) */
    if (typeof c.showDataLabels === "boolean") {
      var dlPos = c.dataLabelPosition || "end";
      o.plugins.datalabels = {
        display: c.showDataLabels,
        position: dlPos,
        anchor: (dlPos === "center") ? "center" : "end",
        align: (dlPos === "center") ? "center" : "top",
        font: { size: c.dataLabelFontSize || 10, weight: "600", family: c.dataLabelFontFamily || undefined },
        color: c.dataLabelColor || undefined,
        formatter: function (c2) { return function (v) { return _seFmt(c2, v); }; }(c),
      };
    }
    /* Tooltip settings */
    if (c.tooltipBgColor || c.tooltipTextColor || c.tooltipFontSize) {
      o.plugins.tooltip = o.plugins.tooltip || {};
      o.plugins.tooltip.backgroundColor = c.tooltipBgColor || undefined;
      o.plugins.tooltip.titleColor = c.tooltipTextColor || undefined;
      o.plugins.tooltip.bodyColor = c.tooltipTextColor || undefined;
      if (c.tooltipFontSize) o.plugins.tooltip.titleFont = { size: c.tooltipFontSize };
      o.plugins.tooltip.cornerRadius = c.tooltipBorderRadius != null ? c.tooltipBorderRadius : undefined;
    }
    /* Chart background */
    if (c.chartBgColor) {
      o.chartArea = o.chartArea || {};
      o.backgroundColor = c.chartBgColor;
    }
    /* Show/hide axes */
    if (typeof c.showAxes === "boolean" && !c.showAxes) {
      o.scales.x = o.scales.x || {};
      o.scales.x.display = false;
      o.scales.y = o.scales.y || {};
      o.scales.y.display = false;
    }
    /* Chart padding */
    if (typeof c.chartPadding === "number") {
      o.layout = o.layout || {};
      o.layout.padding = c.chartPadding;
    }
    /* Animation */
    if (typeof c.animationEnabled === "boolean") {
      o.animation = { duration: c.animationEnabled ? (c.animationDuration || 600) : 0 };
    }
    /* Legend font size */
    if (c.legendFontSize || c.legendFontFamily) {
      o.plugins.legend = o.plugins.legend || {};
      o.plugins.legend.labels = o.plugins.legend.labels || {};
      if (c.legendFontSize) o.plugins.legend.labels.font = { size: c.legendFontSize };
      if (c.legendFontFamily) o.plugins.legend.labels.font = Object.assign({}, o.plugins.legend.labels.font || {}, { family: c.legendFontFamily });
    }
    return o;
  }

  /** Data-label visibility for a chart type: explicit style wins, else per-type default. */
  function _seDataLabels(c, defShow) {
    var st = (c && c.style) || {};
    var dl = st.dataLabels || {};
    return {
      show: (typeof dl.show === "boolean") ? dl.show : defShow !== false,
      position: dl.position || "end",
    };
  }

  window.StyleEngine = {
    colors: _seColors,
    fmt: _seFmt,
    valueFormat: _seFormat,
    opts: _seOpts,
    dataLabels: _seDataLabels,
  };

  /** Table Widget */
  _register("table", function(widget, container) {
    var c = widget.config || {};
    var columns = c.columns || [];
    var rows = c.rows || [];
    var pivotMode = !!(c.pivot && c.pivot.rowCol && c.pivot.valueCol);
    if (pivotMode) {
      var pm = c.pivotMatrix || null;
      if (pm && pm.error) {
        container.innerHTML = '<div style="padding:12px;font-size:12px;color:var(--color-danger);">' + _esc(pm.error) + '</div>';
        return;
      }
      if (!pm || !pm.rows) {
        container.innerHTML = '<div style="padding:12px;font-size:12px;color:var(--color-text-muted);">Pivot: no data</div>';
        return;
      }
      var nf = function (v) {
        var n = Number(v);
        if (isNaN(n)) return "—";
        return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
      };
      var html = '<table class="bd-pivot-table"><thead><tr>' +
        '<th class="bd-pivot-corner">' + _esc(pm.agg) + ' · ' + _esc(pm.rowCol) + ' → ' + _esc(pm.colCol) + '</th>';
      pm.cols.forEach(function (cv) { html += '<th>' + _esc(cv) + '</th>'; });
      html += '<th class="bd-pivot-total">Total</th></tr></thead><tbody>';
      pm.rows.forEach(function (rv, ri) {
        var drillAttr = '';
        if (c.drillPage) {
          drillAttr = ' data-drill-page="' + _attr(c.drillPage) + '"' +
            ' data-drill-field="' + _attr(c.drillField || pm.rowCol) + '"' +
            ' data-drill-value="' + _attr(rv) + '"';
        }
        html += '<tr><th class="bd-pivot-rowh' + (c.drillPage ? ' bd-drillable' : '') + '"' + drillAttr + '>' + _esc(rv) + '</th>';
        pm.cols.forEach(function (cv, ci) {
          html += '<td>' + nf(pm.colsT[ci][ri]) + '</td>';
        });
        var rowTotal = pm.solve(pm.cols.map(function (cv) { return pm.grid[rv + "\u0001" + cv]; }).filter(function (v) { return v !== null && v !== undefined; }));
        html += '<td class="bd-pivot-total">' + nf(rowTotal) + '</td></tr>';
      });
      html += '<tr class="bd-pivot-grand"><th>Total</th>';
      pm.cols.forEach(function (cv, ci) {
        html += '<td>' + nf(pm.colsT[ci].reduce(function (a, b) { return a + b; }, 0)) + '</td>';
      });
      var grand = pm.rows.reduce(function (a, rv) {
        var tot = pm.solve(pm.cols.map(function (cv) { return pm.grid[rv + "\u0001" + cv]; }).filter(function (v) { return v !== null && v !== undefined; }));
        return a + (isNaN(Number(tot)) ? 0 : Number(tot));
      }, 0);
      html += '<td class="bd-pivot-total">' + nf(grand) + '</td></tr>';
      html += '</tbody></table>';
      html += '<div style="padding:6px 8px 0;font-size:10.5px;color:var(--color-text-muted);">' + _esc(pm.valueCol) + ' · ' + _esc(pm.agg) + ' of ' + rows.length + ' rows</div>';
      container.innerHTML = '<div style="height:100%;overflow:auto;">' + html + '</div>';
      _bindDrillThrough(container);
      return;
    }
    if (c.tableColumns && String(c.tableColumns).trim()) {
      var wantSet = {};
      String(c.tableColumns).split(",").forEach(function (s) { wantSet[s.trim()] = true; });
      columns = columns.filter(function (col) { return wantSet[col]; });
    }
    var tableId = "tbl_" + widget.id;
    container.innerHTML = '<div id="' + tableId + '" style="padding:4px;height:100%;"></div>';
    if (c.drillPage) {
      var tblEl = document.getElementById(tableId);
      if (tblEl && !tblEl.__bdDrillBound) {
        tblEl.__bdDrillBound = true;
        tblEl.addEventListener("click", function (e) {
          var tr = e.target.closest("tbody tr");
          if (!tr) return;
          var firstTd = tr.querySelector("td");
          if (!firstTd) return;
          e.stopPropagation();
          var val = firstTd.textContent.trim();
          if (window.BlankDash && window.BlankDash._drillThrough) {
            window.BlankDash._drillThrough(c.drillPage, { field: c.drillField || (columns && columns.length ? columns[0] : ""), value: val });
          }
        });
      }
    }
    setTimeout(function() {
      if (window.DataTable) {
        var opt = {
          columns: columns,
          maxRows: c.tablePagination === false ? 1000 : 200,
          pageSize: c.tablePageSize || 20,
          searchable: c.tableSearchable,
          sortable: c.tableSortable !== false,
          paginate: c.tablePagination !== false,
          striped: c.tableStriped,
          bordered: c.tableBordered,
          condensed: c.tableCondensed,
          headerBg: c.tableHeaderBg,
          headerColor: c.tableHeaderColor,
          rowHoverBg: c.tableRowHoverBg,
          alternatingBg: c.tableAlternatingBg,
          radius: c.tableBorderRadius,
        };
        window.DataTable.render(tableId, rows, opt);
      }
    }, 50);
  });

  /** Text Widget */
  _register("text", function(widget, container) {
    var c = widget.config || {};
    var content = c.content || "";
    container.innerHTML = '<div style="padding:12px;font-size:13px;color:var(--color-content-text);line-height:1.6;">' + content + '</div>';
  });

  /* ── Shape toolkit (PBI-style shape visuals: rectangle / rounded / circle /
         triangle / diamond / pentagon / hexagon / arrow / chevron) ── */

  var _SHAPE_CLIPS = {
    triangle: "polygon(50% 0%, 0% 100%, 100% 100%)",
    diamond: "polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)",
    pentagon: "polygon(50% 0%, 100% 38%, 82% 100%, 18% 100%, 0% 38%)",
    hexagon: "polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%)",
    arrow: "polygon(0% 35%, 60% 35%, 60% 10%, 100% 50%, 60% 90%, 60% 65%, 0% 65%)",
    chevron: "polygon(20% 0%, 80% 50%, 20% 100%, 0% 100%, 60% 50%, 0% 0%)"
  };

  function _hexA(hex, alpha) {
    var m = /^#([0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return null;
    var n = parseInt(m[1], 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
  }

  _register("shape", function(widget, container) {
    var c = widget.config || {};
    var shape = c.shapeShape || "rect";
    var st = ["position:relative;box-sizing:border-box;width:100%;height:100%"];
    if (shape === "rounded") st.push("border-radius:" + (c.shapeRadius != null ? c.shapeRadius : 16) + "px");
    else if (shape === "circle") st.push("border-radius:50%");
    // Hex, not var(): _hexA() parses the fill to apply the opacity.
    var fill = c.shapeFill || "#2563eb";
    var op = c.shapeFillOpacity != null ? Math.min(100, Math.max(0, Number(c.shapeFillOpacity))) / 100 : 1;
    if (op < 1) {
      var a = _hexA(fill, op);
      if (a) fill = a;
      else st.push("opacity:" + op);
    }
    st.push("background:" + fill);
    if (c.shapeBorderColor) st.push("border:" + Math.max(1, Number(c.shapeBorderWidth) || 1) + "px solid " + c.shapeBorderColor);
    var clip = _SHAPE_CLIPS[shape];
    if (clip) st.push("clip-path:" + clip);
    if (c.shapeRotate) st.push("transform:rotate(" + Number(c.shapeRotate) + "deg)");
    container.innerHTML = '<div class="bd-shape" style="' + st.join(";") + '"></div>';
  });

  _register("divider", function(widget, container) {
    var c = widget.config || {};
    var color = c.dividerColor || "var(--color-border-strong)";
    var th = Math.max(1, Number(c.dividerThickness) || 2);
    var style = ["solid", "dashed", "dotted"].indexOf(c.dividerStyle) !== -1 ? c.dividerStyle : "solid";
    var vertical = c.dividerOrientation === "vertical";
    var inset = c.dividerInset != null ? Math.max(0, Number(c.dividerInset)) : (vertical ? 4 : 12);
    var st = vertical
      ? "display:flex;justify-content:center;align-items:stretch;height:100%;padding:" + inset + "px 0;"
      : "display:flex;align-items:center;width:100%;padding:" + inset + "px 0;";
    var line = vertical
      ? 'border-left:' + th + "px " + style + " " + color
      : 'border-top:' + th + "px " + style + " " + color;
    var inner = vertical
      ? '<div style="height:100%;width:0;' + line + ';"></div>'
      : '<div style="width:100%;height:0;' + line + ';"></div>';
    container.innerHTML = '<div class="bd-divider" style="box-sizing:border-box;' + st + '">' + inner + '</div>';
  });

  /* ── Metric Card (PBI cardVisual): N measures, each with a label, formatted
         value, prior-period delta and a 12-month sparkline strip ── */

  function _mcardFmt(val, f) {
    if (val == null || val === "" || isNaN(Number(val))) return "—";
    var num = Number(val);
    f = String(f || "auto");
    var prefix = "", suffix = "";
    if (f === "currency" || f.toString().indexOf("$") !== -1) prefix = "$";
    var dec = prefix ? 2 : 1;
    var abs = Math.abs(num);
    if (abs >= 1e9) { num = num / 1e9; suffix = "B"; }
    else if (abs >= 1e6) { num = num / 1e6; suffix = "M"; }
    else if (abs >= 1e4 && !prefix) { num = num / 1e3; suffix = "K"; }
    return prefix + num.toLocaleString(undefined, { maximumFractionDigits: dec }) + suffix;
  }

  function _mcardSpark(points, color) {
    var nums = (points || []).map(function (p) { return Number(p); }).filter(function (n) { return !isNaN(n); });
    if (nums.length < 2) return "";
    var w = 110, h = 22;
    var max = Math.max.apply(null, nums), min = Math.min.apply(null, nums);
    var span = (max - min) || 1;
    var step = (w - 4) / (nums.length - 1);
    var pts = nums.map(function (n, i) {
      return (i * step + 2).toFixed(1) + "," + (h - 3 - ((n - min) / span) * (h - 6)).toFixed(1);
    }).join(" ");
    var last = nums[nums.length - 1];
    var ly = (h - 3 - ((last - min) / span) * (h - 6)).toFixed(1);
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" preserveAspectRatio="none" style="display:block">' +
      '<polyline fill="none" stroke="' + color + '" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" points="' + pts + '"/>' +
      '<circle cx="' + (w - 2).toFixed(1) + '" cy="' + ly + '" r="1.8" fill="' + color + '"/></svg>';
  }

  /* Categorical series palette — the shared chart palette, read straight from
     the CSS tokens so a metric card and a chart series of the same slot carry
     the same colour. */
  var _MCARD_PALETTE = [
    "var(--chart-1)", "var(--chart-2)", "var(--chart-3)",
    "var(--chart-4)", "var(--chart-5)", "var(--chart-6)",
  ];

  _register("metriccard", function (widget, container) {
    var c = widget.config || {};
    var measures = c.measures || [];
    var rows = c._metricRows || [];
    var sparks = c._sparkRows || [];
    var byMetric = {};
    sparks.forEach(function (r) {
      if (r && r.metric != null) {
        var k = r.metric;
        if (!byMetric[k]) byMetric[k] = [];
        var ks = Object.keys(r);
        var v = r.value != null ? r.value : r[ks[ks.length - 1]];
        byMetric[k].push(Number(v) || 0);
      }
    });
    if (!measures.length) {
      measures = rows.length ? rows.map(function (r) { return { label: r.label || "Value" }; })
                             : [{ label: c.label || "Value" }];
    }
    var items = [];
    measures.forEach(function (m, i) {
      var row = null;
      for (var ri = 0; ri < rows.length; ri++) {
        if (String(rows[ri].label) === String(m.label)) { row = rows[ri]; break; }
      }
      var color = m.color || _MCARD_PALETTE[i % _MCARD_PALETTE.length];
      var value = row != null ? row.value : null;
      var prev = row && row.previous != null ? Number(row.previous) : null;
      var delta = null;
      if (value != null && prev != null && prev !== 0) delta = (Number(value) - prev) / prev * 100;
      var sp = byMetric[m.label];
      items.push(
        '<div class="bd-metric" style="flex:1 1 140px;min-width:150px;border-left:3px solid ' + color + ';padding:8px 12px;box-sizing:border-box;display:flex;flex-direction:column;justify-content:center;gap:2px;overflow:hidden;">' +
          '<div style="font-size:11px;color:var(--color-content-muted,var(--color-text-muted));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + _esc(m.label != null ? m.label : "") + '</div>' +
          '<div style="font-size:20px;font-weight:700;line-height:1.15;color:var(--color-content-text,var(--color-text));white-space:nowrap;">' + _mcardFmt(value, m.format) + '</div>' +
          (delta != null
            ? '<div style="font-size:11px;color:' + (delta < 0 ? "var(--color-danger)" : "var(--color-success)") + ';">' + (delta < 0 ? "▼" : "▲") + ' ' + Math.abs(delta).toFixed(1) + '% <span style="color:var(--color-content-muted,var(--color-text-muted));">vs prior</span></div>'
            : '<div style="font-size:11px;color:var(--color-content-muted,var(--color-text-muted));">&nbsp;</div>') +
          (sp && sp.length ? '<div style="margin-top:4px;">' + _mcardSpark(sp, color) + '</div>' : '') +
        '</div>'
      );
    });
    container.innerHTML = '<div style="display:flex;flex-wrap:wrap;align-items:stretch;height:100%;box-sizing:border-box;gap:2px;">' + items.join("") + '</div>';
  });

  window.DashboardRenderer = {
    render: _render,
    register: _register,
  };

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }
})();
