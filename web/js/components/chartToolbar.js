/**
 * ChartToolbar — Floating toolbar on chart hover with export, fullscreen, annotations
 *
 * Auto-attaches to any canvas created via Charts.*() methods.
 */
(function () {
  "use strict";

  function _attachToCanvas(canvas) {
    if (!canvas || canvas.dataset.chartToolbar) return;
    canvas.dataset.chartToolbar = "1";

    // Wrap in relative container if not already
    var container = canvas.closest(".chart-container");
    if (!container) {
      container = document.createElement("div");
      container.className = "chart-container";
      // Fluid dashboard charts keep height:100% on the canvas and need a
      // block container with a real height so Chart.js can measure it.
      // Fixed-height canvases (e.g. KPI sparklines) keep inline-block so the
      // auto-height wrap does not stretch them.
      var fluid = /height\s*:\s*100%/.test(canvas.style.cssText || "");
      container.style.cssText = "position:relative;display:" + (fluid ? "block" : "inline-block") + ";width:100%;" + (fluid ? "height:100%;" : "");
      canvas.parentNode.insertBefore(container, canvas);
      container.appendChild(canvas);
    }

    // Build toolbar
    var toolbar = document.createElement("div");
    toolbar.className = "chart-toolbar";
    toolbar.style.cssText = "position:absolute;top:4px;right:4px;display:none;gap:2px;z-index:10;background:rgba(255,255,255,0.9);border:1px solid #e2e8f0;border-radius:6px;padding:2px;box-shadow:0 2px 8px rgba(0,0,0,0.1);";
    toolbar.innerHTML =
      '<button class="ctb-btn" data-action="fullscreen" title="Full screen"><i class="fas fa-expand" style="font-size:11px"></i></button>' +
      '<button class="ctb-btn" data-action="export-png" title="Download PNG"><i class="fas fa-download" style="font-size:11px"></i></button>' +
      '<button class="ctb-btn" data-action="export-jpeg" title="Download JPEG"><i class="fas fa-image" style="font-size:11px"></i></button>';
    container.appendChild(toolbar);

    // Style buttons
    toolbar.querySelectorAll(".ctb-btn").forEach(function(btn) {
      btn.style.cssText = "width:26px;height:26px;border:none;border-radius:4px;background:transparent;color:#64748b;cursor:pointer;display:flex;align-items:center;justify-content:center;";
      btn.addEventListener("mouseenter", function() { this.style.background = "#f1f5f9"; });
      btn.addEventListener("mouseleave", function() { this.style.background = "transparent"; });
    });

    // Dark mode support
    function updateDark() {
      var dark = document.documentElement.classList.contains("dark");
      toolbar.style.background = dark ? "rgba(30,41,59,0.9)" : "rgba(255,255,255,0.9)";
      toolbar.style.borderColor = dark ? "#475569" : "#e2e8f0";
    }
    updateDark();

    // Show/hide on hover
    container.addEventListener("mouseenter", function() { toolbar.style.display = "flex"; });
    container.addEventListener("mouseleave", function() { toolbar.style.display = "none"; });

    // Wire actions
    toolbar.querySelector('[data-action="fullscreen"]').addEventListener("click", function() {
      if (window.Charts && window.Charts.fullscreen) window.Charts.fullscreen(canvas.id);
    });
    toolbar.querySelector('[data-action="export-png"]').addEventListener("click", function() {
      if (window.Charts && window.Charts.exportPng) window.Charts.exportPng(canvas.id);
    });
    toolbar.querySelector('[data-action="export-jpeg"]').addEventListener("click", function() {
      if (window.Charts && window.Charts.exportJpeg) window.Charts.exportJpeg(canvas.id);
    });
  }

  // Auto-attach to all existing canvases and future ones via MutationObserver
  function init() {
    document.querySelectorAll("canvas").forEach(_attachToCanvas);
    // Watch for new canvases
    var observer = new MutationObserver(function(mutations) {
      mutations.forEach(function(m) {
        m.addedNodes.forEach(function(node) {
          if (node.tagName === "CANVAS") _attachToCanvas(node);
          if (node.querySelectorAll) node.querySelectorAll("canvas").forEach(_attachToCanvas);
        });
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
