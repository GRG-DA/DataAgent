/**
 * Charts Module - Chart.js Wrappers
 * Unified API for all chart types with responsive defaults, themed colors,
 * and chart instance management.
 */
(function () {
  "use strict";

  // Register the datalabels plugin so charts show values by default (matching
  // real Power BI visuals which always display data labels).
  if (window.Chart && window.ChartDataLabels) {
    window.Chart.register(window.ChartDataLabels);
  }

  // ── Color Palette ──────────────────────────────────────────────────────
  const PALETTE = {
    primary: "#3b82f6",
    secondary: "#8b5cf6",
    success: "#10b981",
    warning: "#f59e0b",
    danger: "#ef4444",
    info: "#06b6d4",
    pink: "#ec4899",
    indigo: "#6366f1",
    orange: "#f97316",
    teal: "#14b8a6",
    slate: "#64748b",
    rose: "#f43f5e",
    cyan: "#22d3ee",
    lime: "#84cc16",
    amber: "#fbbf24",

    // Full ordered palette for categorical data
    categorical: [
      "#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444",
      "#06b6d4", "#ec4899", "#6366f1", "#f97316", "#14b8a6",
      "#84cc16", "#f43f5e", "#22d3ee", "#fbbf24", "#64748b",
    ],

    // Sequential gradient stops
    sequential: ["#eff6ff", "#3b82f6", "#1e40af"],
    // Diverging gradient stops
    diverging: ["#ef4444", "#f5f5f5", "#3b82f6"],
  };

  // ── Chart instance registry ────────────────────────────────────────────
  const _instances = new Map();

  function _getInstance(canvasId) {
    return _instances.get(canvasId) || null;
  }

  function _setInstance(canvasId, chart) {
    // Destroy previous if exists
    _destroyInstance(canvasId);
    _instances.set(canvasId, chart);
  }

  function _destroyInstance(canvasId) {
    const existing = _instances.get(canvasId);
    if (existing && typeof existing.destroy === "function") {
      existing.destroy();
    }
    _instances.delete(canvasId);
  }

  // ── Canvas helper ──────────────────────────────────────────────────────
  function _getCanvas(canvasId) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
      // Expected race: dashboard re-rendered and removed the visual while its
      // data request was in flight. Callers handle null, so keep this quiet.
      console.debug("Canvas #" + canvasId + " not found (removed before render)");
      return null;
    }
    const attached = (window.Chart && typeof Chart.getChart === "function") ? Chart.getChart(canvas) : null;
    if (attached) {
      try { attached.destroy(); } catch (e) {}
    }
    return canvas;
  }

  // ── Themed defaults ────────────────────────────────────────────────────
  function _isDark(el) {
    el = el || document.documentElement;
    if (!el.classList) return false;
    return el.classList.contains("dark") || el.classList.contains("bd-themed");
  }

  function _getDefaultOptions() {
    const dark = _isDark();
    return {
      responsive: true,
      maintainAspectRatio: true,
      animation: {
        duration: 600,
        easing: "easeInOutQuart",
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: {
            color: dark ? "#e2e8f0" : "#1e293b",
            font: { size: 12, family: "Inter, system-ui, sans-serif" },
            padding: 12,
          },
        },
        tooltip: {
          enabled: true,
          backgroundColor: dark ? "#1e293b" : "#ffffff",
          titleColor: dark ? "#f1f5f9" : "#0f172a",
          bodyColor: dark ? "#cbd5e1" : "#334155",
          borderColor: dark ? "#334155" : "#e2e8f0",
          borderWidth: 1,
          padding: 10,
          cornerRadius: 6,
          titleFont: { size: 13, weight: "bold" },
          bodyFont: { size: 12 },
        },
        // Data labels: show values on chart elements (matches PBI default)
        datalabels: {
          display: false,           // off by default; individual chart types opt in
          anchor: "end",
          align: "top",
          color: dark ? "#e2e8f0" : "#334155",
          font: { size: 10, weight: "bold" },
          formatter: function (value) {
            if (value >= 1000000) return (value / 1000000).toFixed(1) + "M";
            if (value >= 1000) return (value / 1000).toFixed(1) + "K";
            return value;
          },
        },
      },
      scales: {
        x: {
          ticks: { color: dark ? "#94a3b8" : "#64748b", font: { size: 11 } },
          grid: { color: dark ? "rgba(148,163,184,0.1)" : "rgba(100,116,139,0.1)" },
        },
        y: {
          ticks: { color: dark ? "#94a3b8" : "#64748b", font: { size: 11 } },
          grid: { color: dark ? "rgba(148,163,184,0.1)" : "rgba(100,116,139,0.1)" },
        },
      },
    };
  }

  function _mergeOptions(custom) {
    const defaults = _getDefaultOptions();
    return _deepMerge(defaults, custom || {});
  }

  function _deepMerge(target, source) {
    const out = { ...target };
    for (const key of Object.keys(source)) {
      if (
        source[key] &&
        typeof source[key] === "object" &&
        !Array.isArray(source[key]) &&
        target[key] &&
        typeof target[key] === "object"
      ) {
        out[key] = _deepMerge(target[key], source[key]);
      } else {
        out[key] = source[key];
      }
    }
    return out;
  }

  // ── Auto-colour palette application ────────────────────────────────────
  function _autoColorPalette(dataLength) {
    const palette = PALETTE.categorical;
    return Array.from({ length: dataLength }, (_, i) => palette[i % palette.length]);
  }

  // Honour an explicit per-chart colour list (widget Format tab) when present,
  // otherwise fall back to the automatic categorical palette.
  function _resolveColours(dataLength, options) {
    if (options && Array.isArray(options.colors) && options.colors.length) {
      return Array.from({ length: dataLength }, (_, i) => options.colors[i % options.colors.length]);
    }
    return _autoColorPalette(dataLength);
  }

  // First explicit colour is used as the single-series accent when configured.
  function _accentColour(options, fallback) {
    if (options && Array.isArray(options.colors) && options.colors.length) return options.colors[0];
    return fallback;
  }

  function _makeTransparent(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  // ── Chart Factory Functions ────────────────────────────────────────────

  /**
   * Line chart.
   * @param {string} canvasId
   * @param {string[]} labels
   * @param {object[]} datasets - [{ label, data }]
   * @param {object} [options]
   */
  function line(canvasId, labels, datasets, options) {
    const canvas = _getCanvas(canvasId);
    if (!canvas) return null;

    const colours = _resolveColours(datasets.length, options);
    const processedDatasets = datasets.map((ds, i) => ({
      label: ds.label || "",
      data: ds.data,
      borderColor: colours[i],
      backgroundColor: _makeTransparent(colours[i], 0.1),
      borderWidth: 2,
      pointRadius: 3,
      pointHoverRadius: 5,
      tension: 0.3,
      fill: false,
      ...ds,
    }));

    const opts = _mergeOptions(options);
    const chart = new Chart(canvas, {
      type: "line",
      data: { labels, datasets: processedDatasets },
      options: opts,
    });
    _setInstance(canvasId, chart);
    return chart;
  }

  /**
   * Bar chart (vertical).
   * @param {string} canvasId
   * @param {string[]} labels
   * @param {number[]|object[]} data - flat array or [{ label, data }]
   * @param {object} [options]
   */
  function bar(canvasId, labels, data, options) {
    const canvas = _getCanvas(canvasId);
    if (!canvas) return null;

    let datasets;
    if (Array.isArray(data) && data.length > 0 && typeof data[0] === "object" && "data" in data[0]) {
      // Multiple datasets provided
      const colours = _resolveColours(data.length, options);
      datasets = data.map((ds, i) => ({
        label: ds.label || "",
        data: ds.data,
        backgroundColor: _makeTransparent(colours[i], 0.7),
        borderColor: colours[i],
        borderWidth: 1,
        borderRadius: 4,
        ...ds,
      }));
    } else {
      // Single dataset
      const colour = _accentColour(options, PALETTE.primary);
      datasets = [
        {
          data: data,
          backgroundColor: _makeTransparent(colour, 0.7),
          borderColor: colour,
          borderWidth: 1,
          borderRadius: 4,
        },
      ];
    }

    const opts = _mergeOptions(options);
    const chart = new Chart(canvas, {
      type: "bar",
      data: { labels, datasets },
      options: opts,
    });
    _setInstance(canvasId, chart);
    return chart;
  }

  /**
   * Horizontal bar chart.
   * @param {string} canvasId
   * @param {string[]} labels
   * @param {number[]|object[]} data
   * @param {object} [options]
   */
  function horizontalBar(canvasId, labels, data, options) {
    const merged = _mergeOptions(options);
    merged.indexAxis = "y";
    const result = bar(canvasId, labels, data, merged);
    return result;
  }

  /**
   * Scatter plot.
   * @param {string} canvasId
   * @param {object[]} dataPoints - [{ x, y }]
   * @param {object} [options]
   */
  function scatter(canvasId, dataPoints, options) {
    const canvas = _getCanvas(canvasId);
    if (!canvas) return null;

    const colour = _accentColour(options, PALETTE.secondary);
    const dataset = {
      label: "Data",
      data: dataPoints,
      backgroundColor: _makeTransparent(colour, 0.6),
      borderColor: colour,
      borderWidth: 1,
      pointRadius: 4,
      pointHoverRadius: 6,
    };

    const opts = _mergeOptions(options);
    const chart = new Chart(canvas, {
      type: "scatter",
      data: { datasets: [dataset] },
      options: opts,
    });
    _setInstance(canvasId, chart);
    return chart;
  }

  /**
   * Pie chart.
   * @param {string} canvasId
   * @param {string[]} labels
   * @param {number[]} data
   * @param {object} [options]
   */
  function pie(canvasId, labels, data, options) {
    const canvas = _getCanvas(canvasId);
    if (!canvas) return null;

    const colours = _resolveColours(data.length, options);
    const dataset = {
      data: data,
      backgroundColor: colours.map((c) => _makeTransparent(c, 0.75)),
      borderColor: "#ffffff",
      borderWidth: 2,
    };

    const opts = _deepMerge(
      _deepMerge(
        _getDefaultOptions(),
        {
          scales: undefined,
          plugins: {
            legend: { position: "right" },
          },
        }
      ),
      options || {}
    );

    const chart = new Chart(canvas, {
      type: "pie",
      data: { labels, datasets: [dataset] },
      options: opts,
    });
    _setInstance(canvasId, chart);
    return chart;
  }

  /**
   * Doughnut chart.
   * @param {string} canvasId
   * @param {string[]} labels
   * @param {number[]} data
   * @param {object} [options]
   */
  function doughnut(canvasId, labels, data, options) {
    const canvas = _getCanvas(canvasId);
    if (!canvas) return null;

    const colours = _resolveColours(data.length, options);
    const dataset = {
      data: data,
      backgroundColor: colours.map((c) => _makeTransparent(c, 0.75)),
      borderColor: "#ffffff",
      borderWidth: 2,
      cutout: "60%",
    };

    const opts = _deepMerge(
      _deepMerge(
        _getDefaultOptions(),
        {
          scales: undefined,
          plugins: {
            legend: { position: "right" },
          },
        }
      ),
      options || {}
    );

    const chart = new Chart(canvas, {
      type: "doughnut",
      data: { labels, datasets: [dataset] },
      options: opts,
    });
    _setInstance(canvasId, chart);
    return chart;
  }

  /**
   * Heatmap via custom canvas rendering (Chart.js matrix plugin fallback).
   * @param {string} canvasId
   * @param {number[][]} matrix - 2D array of values
   * @param {object} [options] - { xLabels, yLabels, colorScale, title }
   */
  function heatmap(canvasId, matrix, options) {
    const canvas = _getCanvas(canvasId);
    if (!canvas) return null;

    const opts = options || {};
    const xLabels = opts.xLabels || matrix[0]?.map((_, i) => "X" + i) || [];
    const yLabels = opts.yLabels || matrix.map((_, i) => "Y" + i) || [];
    const title = opts.title || "";

    // Flatten matrix into scatter-like data for Chart.js matrix plugin,
    // or fall back to pure canvas 2D rendering
    _destroyInstance(canvasId);

    const rows = matrix.length;
    const cols = matrix.length > 0 ? matrix[0].length : 0;
    if (rows === 0 || cols === 0) {
      const ctx = canvas.getContext("2d");
      const dark = _isDark();
      ctx.fillStyle = dark ? "#1e293b" : "#f8fafc";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = dark ? "#94a3b8" : "#64748b";
      ctx.font = "14px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No data", canvas.width / 2, canvas.height / 2);
      return null;
    }

    // Calculate min/max for normalisation
    let min = Infinity;
    let max = -Infinity;
    for (const row of matrix) {
      for (const val of row) {
        if (val < min) min = val;
        if (val > max) max = val;
      }
    }

    function draw() {
      const ctx = canvas.getContext("2d");
      const dark = _isDark();
      const padding = { top: 30, left: 60, right: 20, bottom: 60 };
      const cellW = (canvas.width - padding.left - padding.right) / cols;
      const cellH = (canvas.height - padding.top - padding.bottom) / rows;

      // Clear canvas
      ctx.fillStyle = dark ? "#0f172a" : "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // Draw cells
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const val = matrix[r][c];
          const norm = max === min ? 0.5 : (val - min) / (max - min);
          ctx.fillStyle = _heatmapColor(norm, dark);
          ctx.fillRect(padding.left + c * cellW, padding.top + r * cellH, cellW - 1, cellH - 1);

          // Cell value label
          if (cellW > 30 && cellH > 20) {
            ctx.fillStyle = norm > 0.6 ? "#ffffff" : dark ? "#e2e8f0" : "#1e293b";
            ctx.font = "11px Inter, sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(
              typeof val === "number" ? val.toFixed(2) : String(val),
              padding.left + c * cellW + cellW / 2 - 0.5,
              padding.top + r * cellH + cellH / 2
            );
          }
        }
      }

      // X axis labels
      ctx.fillStyle = dark ? "#94a3b8" : "#64748b";
      ctx.font = "11px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let c = 0; c < cols; c++) {
        ctx.save();
        const x = padding.left + c * cellW + cellW / 2;
        const y = canvas.height - padding.bottom + 6;
        ctx.translate(x, y);
        ctx.rotate(-Math.PI / 4);
        ctx.fillText(xLabels[c] || "", 0, 0);
        ctx.restore();
      }

      // Y axis labels
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      for (let r = 0; r < rows; r++) {
        ctx.fillText(
          yLabels[r] || "",
          padding.left - 6,
          padding.top + r * cellH + cellH / 2
        );
      }

      // Title
      if (title) {
        ctx.fillStyle = dark ? "#f1f5f9" : "#0f172a";
        ctx.font = "bold 13px Inter, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(title, canvas.width / 2, 8);
      }
    }

    draw();

    // Store a minimal "chart-like" object so fullscreen entry/exit can
    // resize and redraw this pure-canvas chart (no Chart.js instance).
    _instances.set(canvasId, {
      destroy: () => { const ctx = canvas.getContext("2d"); ctx.clearRect(0, 0, canvas.width, canvas.height); },
      resize: function () {
        if (document.fullscreenElement) {
          const w = window.innerWidth;
          const h = window.innerHeight;
          if (w > 0 && h > 0 && (w !== canvas.width || h !== canvas.height)) {
            canvas.width = w;
            canvas.height = h;
          }
        }
        draw();
      },
      render: draw
    });
    return { canvas, matrix };
  }

  function _heatmapColor(norm, dark) {
    // Blue-to-red sequential scale
    const r = Math.round(59 + norm * (239 - 59));
    const g = Math.round(130 - norm * 80);
    const b = Math.round(246 - norm * 200);
    const alpha = dark ? 0.85 : 0.75;
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  /**
   * Radar chart.
   * @param {string} canvasId
   * @param {string[]} labels
   * @param {object[]} datasets - [{ label, data }]
   * @param {object} [options]
   */
  function radar(canvasId, labels, datasets, options) {
    const canvas = _getCanvas(canvasId);
    if (!canvas) return null;

    const colours = _resolveColours(datasets.length, options);
    const processedDatasets = datasets.map((ds, i) => ({
      label: ds.label || "",
      data: ds.data,
      backgroundColor: _makeTransparent(colours[i], 0.15),
      borderColor: colours[i],
      borderWidth: 2,
      pointBackgroundColor: colours[i],
      pointRadius: 3,
      ...ds,
    }));

    const opts = _deepMerge(
      _getDefaultOptions(),
      {
        scales: {
          r: {
            ticks: {
              color: _isDark() ? "#94a3b8" : "#64748b",
              backdropColor: "transparent",
            },
            grid: {
              color: _isDark() ? "rgba(148,163,184,0.2)" : "rgba(100,116,139,0.15)",
            },
            pointLabels: {
              color: _isDark() ? "#cbd5e1" : "#334155",
              font: { size: 12 },
            },
          },
        },
      },
      options || {}
    );

    const chart = new Chart(canvas, {
      type: "radar",
      data: { labels, datasets: processedDatasets },
      options: opts,
    });
    _setInstance(canvasId, chart);
    return chart;
  }

  /**
   * Sparkline - tiny inline chart without axes or legend.
   * @param {string} canvasId
   * @param {number[]} data
   * @param {object} [options] - { color, fillColor, lineWidth }
   */
  function sparkline(canvasId, data, options) {
    const canvas = _getCanvas(canvasId);
    if (!canvas) return null;

    const opts = options || {};
    const color = opts.color || PALETTE.primary;
    const fillColor = opts.fillColor || _makeTransparent(color, 0.15);
    const lineWidth = opts.lineWidth || 1.5;
    const dark = _isDark();

    const labels = data.map((_, i) => "");
    const chartOpts = _deepMerge(
      _getDefaultOptions(),
      {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: false,
          },
        },
        scales: {
          x: { display: false },
          y: { display: false },
        },
        animation: { duration: 400 },
        elements: { point: { radius: 0, hitRadius: 4 } },
      },
      options || {}
    );

    const dataset = {
      data: data,
      borderColor: color,
      backgroundColor: fillColor,
      borderWidth: lineWidth,
      fill: true,
      tension: 0.4,
      pointRadius: 0,
    };

    const chart = new Chart(canvas, {
      type: "line",
      data: { labels, datasets: [dataset] },
      options: chartOpts,
    });
    _setInstance(canvasId, chart);
    return chart;
  }

  /**
   * Multi-panel: render multiple related charts in parallel.
   * @param {string[]} canvasIds
   * @param {object[]} configs - [{ type, labels, datasets/data, options }]
   */
  function multiPanel(canvasIds, configs) {
    const charts = [];
    for (let i = 0; i < canvasIds.length; i++) {
      const cfg = configs[i];
      if (!cfg) {
        console.warn("Missing config for canvas #" + canvasIds[i]);
        continue;
      }

      const { type, labels, data, datasets, options } = cfg;
      let chart;

      switch (type) {
        case "line":
          chart = line(canvasIds[i], labels, datasets || [{ data }], options);
          break;
        case "bar":
          chart = bar(canvasIds[i], labels, data || datasets, options);
          break;
        case "horizontalBar":
          chart = horizontalBar(canvasIds[i], labels, data || datasets, options);
          break;
        case "scatter":
          chart = scatter(canvasIds[i], data, options);
          break;
        case "pie":
          chart = pie(canvasIds[i], labels, data, options);
          break;
        case "doughnut":
          chart = doughnut(canvasIds[i], labels, data, options);
          break;
        case "radar":
          chart = radar(canvasIds[i], labels, datasets || [{ data }], options);
          break;
        case "sparkline":
          chart = sparkline(canvasIds[i], data, options);
          break;
        default:
          console.warn("Unknown chart type:", type, "for canvas #" + canvasIds[i]);
      }
      if (chart) charts.push(chart);
    }
    return charts;
  }

  /**
   * Destroy a chart instance by canvas ID.
   * @param {string} canvasId
   */
  function destroy(canvasId) {
    _destroyInstance(canvasId);
  }

  // ── Chart Export ──
  function exportPng(canvasId, filename) {
    filename = filename || "chart.png";
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var link = document.createElement("a"); link.download = filename;
    link.href = canvas.toDataURL("image/png", 1.0);
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
  }
  function exportJpeg(canvasId, filename, quality) {
    filename = filename || "chart.jpg"; quality = quality || 0.9;
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var link = document.createElement("a"); link.download = filename;
    link.href = canvas.toDataURL("image/jpeg", quality);
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
  }
  function copyImage(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    canvas.toBlob(function(blob) { try { navigator.clipboard.write([new ClipboardItem({"image/png":blob})]); } catch(e) {} });
  }

  // ── Full-Screen ──
  function fullscreen(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var c = canvas.closest(".chart-container") || canvas.parentElement || canvas;
    if (!c) return;
    // Remember the pre-fullscreen canvas size so we can restore it on exit.
    // Chart.js rewrites the width/height attributes during fullscreen, so keep
    // the original in dataset (survives repeated fullscreen toggles).
    if (!canvas.dataset.fsOrigW) canvas.dataset.fsOrigW = canvas.getAttribute("width") || "";
    if (!canvas.dataset.fsOrigH) canvas.dataset.fsOrigH = canvas.getAttribute("height") || "";
    var origW = canvas.dataset.fsOrigW;
    var origH = canvas.dataset.fsOrigH;
    if (c.requestFullscreen) c.requestFullscreen(); else if (c.webkitRequestFullscreen) c.webkitRequestFullscreen();
    if (!c.querySelector(".chart-fullscreen-close")) {
      var btn = document.createElement("button");
      btn.className = "chart-fullscreen-close";
      btn.innerHTML = "&times;";
      btn.style.cssText = "position:fixed;top:16px;right:16px;z-index:10000;width:36px;height:36px;border-radius:50%;border:none;background:rgba(0,0,0,0.5);color:#fff;font-size:20px;cursor:pointer;";
      btn.addEventListener("click", function() { if (document.exitFullscreen) document.exitFullscreen(); });
      c.appendChild(btn);
      function onFS() {
        if (!document.fullscreenElement) {
          var b = c.querySelector(".chart-fullscreen-close");
          if (b) b.remove();
          document.removeEventListener("fullscreenchange", onFS);
          // Chart.js's ResizeObserver does not fire reliably on fullscreen exit,
          // so the canvas keeps its fullscreen pixel size and stretches the
          // surrounding layout. Restore the original size, let the layout
          // reflow, then force a re-measure.
          if (origW) canvas.setAttribute("width", origW);
          if (origH) canvas.setAttribute("height", origH);
          canvas.style.width = "";
          canvas.style.height = "";
          setTimeout(function() {
            var ch = _getInstance(canvasId);
            if (ch && ch.resize) ch.resize();
            // resize() can early-return without redrawing when the restored
            // canvas attributes already match the target size (Chart.js's
            // updateCanvasSize sees no change), leaving the canvas blank.
            // Force a redraw so the chart is visible again after exit.
            if (ch && ch.render) ch.render();
          }, 100);
        }
      }
      document.addEventListener("fullscreenchange", onFS);
    }
    setTimeout(function() { var ch = _getInstance(canvasId); if (ch && ch.resize) ch.resize(); }, 100);
  }

  // ── Area Chart ──
  function area(canvasId, labels, datasets, options) {
    var canvas = _getCanvas(canvasId);
    if (!canvas) return null;
    var opts = options || {};
    var colours = _autoColorPalette(datasets.length);
    var ds = datasets.map(function(d, i) { var c = colours[i]; return { label:d.label||"", data:d.data, borderColor:c, backgroundColor:_makeTransparent(c,0.2), borderWidth:2, tension:0.3, fill:true, pointRadius:2 }; });
    var merged = _mergeOptions(opts);
    if (opts.stacked) { merged.scales = merged.scales || {}; merged.scales.y = merged.scales.y || {}; merged.scales.y.stacked = true; }
    var chart = new Chart(canvas, { type:"line", data:{ labels:labels, datasets:ds }, options:merged });
    _setInstance(canvasId, chart); return chart;
  }

  // ── Bubble Chart ──
  function bubble(canvasId, dataPoints, options) {
    var canvas = _getCanvas(canvasId);
    if (!canvas) return null;
    var opts = options || {};
    var colour = opts.color || PALETTE.secondary;
    var chart = new Chart(canvas, { type:"bubble", data:{ datasets:[{ label:opts.label||"Data", data:dataPoints, backgroundColor:_makeTransparent(colour,0.6), borderColor:colour }] }, options:_mergeOptions(opts) });
    _setInstance(canvasId, chart); return chart;
  }

  // ── Mixed Chart ──
  function mixed(canvasId, labels, configs, options) {
    var canvas = _getCanvas(canvasId);
    if (!canvas) return null;
    var colours = _resolveColours(configs.length, options);
    var datasets = configs.map(function(cfg, i) { var c = colours[i]; return Object.assign({ type:cfg.type||"bar", label:cfg.label||"", data:cfg.data, borderColor:c, backgroundColor:cfg.type==="line"?_makeTransparent(c,0.1):_makeTransparent(c,0.7), borderWidth:cfg.type==="line"?2:1, pointRadius:cfg.type==="line"?3:0, yAxisID:cfg.yAxisID||"y" }, cfg); });
    var merged = _mergeOptions(options);
    if (configs.some(function(c){return c.yAxisID==="y1"})) { merged.scales = merged.scales || {}; merged.scales.y1 = { position:"right", grid:{display:false}, ticks:{color:_isDark()?"#94a3b8":"#64748b",font:{size:11}} }; }
    var chart = new Chart(canvas, { type:"bar", data:{ labels:labels, datasets:datasets }, options:merged });
    _setInstance(canvasId, chart); return chart;
  }

  // ── Cross-Filtering ──
  var _filterGroups = {};
  function linkCharts(groupId, chartIds, onFilter) {
    var group = _filterGroups[groupId];
    if (!group) { group = { chartIds:chartIds||[], activeFilter:null, onFilter:onFilter }; _filterGroups[groupId] = group; }
    (chartIds||[]).forEach(function(id) {
      var chart = _getInstance(id);
      if (!chart||!chart.canvas) return;
      chart.canvas.style.cursor = "pointer";
      chart.options.onClick = function(e, elements) {
        if (!elements||elements.length===0) { group.activeFilter=null; _dispatchFilter(groupId,null); return; }
        var el = elements[0], label = chart.data.labels ? chart.data.labels[el.index] : null;
        if (group.activeFilter&&group.activeFilter.label===label) group.activeFilter=null;
        else group.activeFilter = { label:label, index:el.index };
        _dispatchFilter(groupId, group.activeFilter);
      };
    });
    document.addEventListener("chart:filter-"+groupId, function(e) {
      var filter = e.detail;
      group.activeFilter = filter;
      if (group.onFilter) group.onFilter(filter);
      (chartIds||[]).forEach(function(id) {
        var ch = _getInstance(id);
        if (!ch||!ch.data) return;
        ch.data.datasets.forEach(function(ds) {
          if (Array.isArray(ds.backgroundColor)) {
            if (!ds._origColors) ds._origColors = [].concat(ds.backgroundColor);
            ds.backgroundColor = (filter&&filter.index!=null) ? ds.backgroundColor.map(function(c,ci){return ci===filter.index?c:"rgba(200,200,200,0.3)";}) : [].concat(ds._origColors);
          }
        });
        ch.update();
      });
    });
  }
  function _dispatchFilter(groupId, filter) { document.dispatchEvent(new CustomEvent("chart:filter-"+groupId, {detail:filter})); }

  // ── Annotations ──
  function addAnnotation(canvasId, annotation) {
    var chart = _getInstance(canvasId);
    if (!chart||!chart.options) return;
    chart.options.plugins = chart.options.plugins || {};
    chart.options.plugins.annotation = chart.options.plugins.annotation || {};
    chart.options.plugins.annotation.annotations = chart.options.plugins.annotation.annotations || [];
    var ann = { type:annotation.type||"line", borderColor:annotation.color||PALETTE.primary, borderWidth:2 };
    if (annotation.label) ann.label = { content:annotation.label, enabled:true, position:"start" };
    if (annotation.type==="box") ann.backgroundColor = _makeTransparent(annotation.color||PALETTE.primary,0.1);
    if (annotation.xMin!=null) ann.xMin=annotation.xMin; if (annotation.xMax!=null) ann.xMax=annotation.xMax;
    if (annotation.yMin!=null) ann.yMin=annotation.yMin; if (annotation.yMax!=null) ann.yMax=annotation.yMax;
    chart.options.plugins.annotation.annotations.push(ann);
    chart.update();
  }

  // ── Registry ──
  function listInstances() { var results=[]; _instances.forEach(function(ch,id){results.push({canvasId:id,chart:ch});}); return results; }
  function refreshAllCharts() {
    _instances.forEach(function(chart) {
      if (chart.update) {
        var host = (chart.canvas && chart.canvas.closest) ? chart.canvas.closest(".ff-widget") : null;
        var dark = _isDark(host);
        if (chart.options&&chart.options.scales) {
          if (chart.options.scales.x&&chart.options.scales.x.ticks) chart.options.scales.x.ticks.color = dark?"#94a3b8":"#64748b";
          if (chart.options.scales.y&&chart.options.scales.y.ticks) chart.options.scales.y.ticks.color = dark?"#94a3b8":"#64748b";
        }
        if (chart.options&&chart.options.plugins&&chart.options.plugins.legend&&chart.options.plugins.legend.labels)
          chart.options.plugins.legend.labels.color = dark?"#e2e8f0":"#1e293b";
        chart.update();
      }
    });
  }

  // ── Public Charts API ──────────────────────────────────────────────────
  window.Charts = {
    line,
    bar,
    horizontalBar,
    scatter,
    pie,
    doughnut,
    heatmap,
    radar,
    sparkline,
    multiPanel,
    destroy,
    PALETTE,
    // Export
    exportPng,
    exportJpeg,
    copyImage,
    // Full-screen
    fullscreen,
    // New chart types
    area,
    bubble,
    mixed,
    // Interactivity
    linkCharts,
    addAnnotation,
    // Registry
    list: listInstances,
    refreshAll: refreshAllCharts,
  };
})();
