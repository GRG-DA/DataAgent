(function () {
  "use strict";

  var _BREAKPOINTS = {
    desktop: { label: "Desktop", width: 1920, icon: "fa-desktop" },
    laptop: { label: "Laptop", width: 1366, icon: "fa-laptop" },
    tablet: { label: "Tablet", width: 768, icon: "fa-tablet-screen-button" },
    mobile: { label: "Mobile", width: 375, icon: "fa-mobile-screen-button" }
  };

  var _currentBreakpoint = "desktop";

  function getCurrent() {
    return _currentBreakpoint;
  }

  function setBreakpoint(bp) {
    if (!_BREAKPOINTS[bp]) return;
    _currentBreakpoint = bp;
    var canvas = document.getElementById("bd-canvas");
    if (canvas && bp !== "desktop") {
      canvas.style.maxWidth = _BREAKPOINTS[bp].width + "px";
    } else if (canvas) {
      canvas.style.maxWidth = "";
    }
  }

  function applyOverrides(widget) {
    if (!widget || !widget.config || !widget.config.responsive) return widget;
    var bp = _currentBreakpoint;
    var overrides = widget.config.responsive[bp];
    if (!overrides) return widget;

    var effective = Object.assign({}, widget.config);

    if (overrides.width !== undefined) effective.width = overrides.width;
    if (overrides.height !== undefined) effective.height = overrides.height;
    if (overrides.visible !== undefined) effective.visible = overrides.visible;
    if (overrides.fontSize !== undefined) effective.fontSize = overrides.fontSize;
    if (overrides.padding !== undefined) {
      effective.padding = Object.assign(
        {},
        widget.config.padding || { top: 0, right: 0, bottom: 0, left: 0 },
        overrides.padding
      );
    }
    if (overrides.fontSize !== undefined) effective.fontSize = overrides.fontSize;
    if (overrides.minWidth !== undefined) effective.minWidth = overrides.minWidth;
    if (overrides.maxWidth !== undefined) effective.maxWidth = overrides.maxWidth;

    return effective;
  }

  function saveOverride(widget, breakpoint, overrides) {
    if (!widget || !breakpoint || !_BREAKPOINTS[breakpoint]) return;
    widget.config = widget.config || {};
    widget.config.responsive = widget.config.responsive || {};
    widget.config.responsive[breakpoint] = Object.assign(
      widget.config.responsive[breakpoint] || {},
      overrides
    );
  }

  function getResponsiveProps() {
    return Object.keys(_BREAKPOINTS).map(function (bp) {
      return {
        key: "config.responsive." + bp + ".visible",
        type: "toggle",
        label: _BREAKPOINTS[bp].label + " Visible",
        default: true
      };
    });
  }

  function previewBreakpoint(bp) {
    setBreakpoint(bp);
    if (window.BlankDash && BlankDash._initGrid) BlankDash._initGrid();
  }

  function resetPreview() {
    setBreakpoint("desktop");
    if (window.BlankDash && BlankDash._initGrid) BlankDash._initGrid();
  }

  function getCanvasWidth(bp) {
    return _BREAKPOINTS[bp] ? _BREAKPOINTS[bp].width : 1920;
  }

  window.ResponsiveConfig = {
    getCurrent: getCurrent,
    setBreakpoint: setBreakpoint,
    applyOverrides: applyOverrides,
    saveOverride: saveOverride,
    getResponsiveProps: getResponsiveProps,
    previewBreakpoint: previewBreakpoint,
    resetPreview: resetPreview,
    getCanvasWidth: getCanvasWidth,
    getBreakpoints: function () {
      return Object.assign({}, _BREAKPOINTS);
    }
  };
})();
