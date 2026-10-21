/**
 * WidgetPropertySchemas — Declarative property schema system for the dashboard builder.
 *
 * Each widget type defines its supported properties; the properties panel
 * auto-generates UI from these schemas. Properties use dot-paths
 * (e.g. "config.sql") to target fields in the widget object tree.
 *
 * Exposes: window.WidgetPropertySchemas
 */
(function () {
  "use strict";

  /* ═══════════════════════════════════════════════════════════════════════
     SHARED PROPERTY FRAGMENTS
     ═══════════════════════════════════════════════════════════════════════ */

  var LAYOUT_PROPS = [
    { key: "widget.title", type: "text", label: "Title", default: "", group: "Content", placeholder: "Widget title in the card header", helpText: "Header title shown at the top of the widget" },
    { key: "widget.x", type: "number", label: "X Position", default: 0, min: 0, max: 2000, step: 1, group: "Layout", helpText: "Horizontal position in pixels" },
    { key: "widget.y", type: "number", label: "Y Position", default: 0, min: 0, max: 2000, step: 1, group: "Layout", helpText: "Vertical position in pixels" },
    { key: "widget.width", type: "number", label: "Width", default: 300, min: 40, max: 2000, step: 1, group: "Layout", helpText: "Widget width in pixels" },
    { key: "widget.height", type: "number", label: "Height", default: 200, min: 30, max: 2000, step: 1, group: "Layout", helpText: "Widget height in pixels" },
    { key: "widget.zIndex", type: "number", label: "Z-Index", default: 1, min: 0, max: 9999, step: 1, group: "Layout", helpText: "Stacking order" },
    { key: "widget.locked", type: "toggle", label: "Locked", default: false, group: "Layout", helpText: "Prevent drag and resize" },
  ];

  var STYLE_PROPS = [
    { key: "config.bgColor", type: "color", label: "Background Color", default: "#ffffff", group: "Style" },
    { key: "config.borderColor", type: "color", label: "Border Color", default: "#e2e8f0", group: "Style" },
    { key: "config.showTitle", type: "toggle", label: "Show Title", default: true, group: "Style" },
    { key: "config.borderStyle", type: "select", label: "Border Style", default: "solid", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"], ["none", "None"]], group: "Style" },
    { key: "config.borderWidth", type: "number", label: "Border Width", default: 1, min: 0, max: 20, step: 1, group: "Style", suffix: "px" },
    { key: "config.borderRadius", type: "number", label: "Border Radius", default: 8, min: 0, max: 50, step: 1, group: "Style", suffix: "px" },
    { key: "config.borderShapePct", type: "range", label: "Corner Shape", default: 0, min: 0, max: 100, step: 1, group: "Style", helpText: "Drag to round corners: 0 = Theme default · 100 = Rounded", suffix: "%" },
  ];

  /* P3.9 — report tooltip page: a widget tagged "tooltip" acts as the report's
     hover tooltip (Power BI tooltip-page semantics). Hovering a data point in any
     chart re-renders it filtered by that point. */
  var TOOLTIP_PAGE_PROPS = [
    { key: "config.isTooltip", type: "toggle", label: "Use as Report Tooltip Page", default: false, group: "Report Tooltip", helpText: "This widget renders as a floating tooltip when hovering chart data points (filtered by the hovered point), like a Power BI tooltip page" },
  ];

  var SQL_PROPS = [
    { key: "config.table", type: "tableSelect", label: "Table", default: "", group: "Data", helpText: "Source table (dynamic options from connected database)" },
    { key: "config.sql", type: "textarea", label: "SQL Query", default: "", rows: 6, group: "Data", placeholder: "SELECT column AS label, value FROM table", helpText: "DuckDB-compatible SQL query" },
  ];

  var CHART_STYLE_PROPS = [
    { key: "config.chartColors", type: "colorArray", label: "Series Colors", default: ["#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#06b6d4"], group: "Chart Style", helpText: "Colors for each data series" },
    { key: "config.legendShow", type: "toggle", label: "Show Legend", default: true, group: "Chart Style" },
    { key: "config.legendPosition", type: "select", label: "Legend Position", default: "top", options: [["top", "Top"], ["bottom", "Bottom"], ["left", "Left"], ["right", "Right"]], group: "Chart Style" },
    { key: "config.legendFontFamily", type: "fontFamily", label: "Legend Font", default: "Inter, system-ui, sans-serif", group: "Chart Style" },
    { key: "config.legendFontSize", type: "number", label: "Legend Font Size", default: 12, min: 8, max: 24, step: 1, group: "Chart Style", suffix: "px" },
    { key: "config.showDataLabels", type: "toggle", label: "Show Data Labels", default: false, group: "Chart Style" },
    { key: "config.dataLabelPosition", type: "select", label: "Label Position", default: "auto", options: [["auto", "Auto"], ["top", "Top"], ["bottom", "Bottom"], ["center", "Center"], ["start", "Start"], ["end", "End"]], group: "Chart Style", condition: { key: "config.showDataLabels", equals: true } },
    { key: "config.dataLabelFontFamily", type: "fontFamily", label: "Label Font", default: "Inter, system-ui, sans-serif", group: "Chart Style", condition: { key: "config.showDataLabels", equals: true } },
    { key: "config.dataLabelFontSize", type: "number", label: "Label Font Size", default: 11, min: 8, max: 20, step: 1, group: "Chart Style", suffix: "px", condition: { key: "config.showDataLabels", equals: true } },
    { key: "config.dataLabelColor", type: "color", label: "Label Color", default: "#374151", group: "Chart Style", condition: { key: "config.showDataLabels", equals: true } },
    { key: "config.xAxisTitle", type: "text", label: "X-Axis Title", default: "", group: "Chart Style", placeholder: "X Axis" },
    { key: "config.yAxisTitle", type: "text", label: "Y-Axis Title", default: "", group: "Chart Style", placeholder: "Y Axis" },
    { key: "config.xAxisLabelColor", type: "color", label: "X-Axis Label Color", default: "#6b7280", group: "Chart Style" },
    { key: "config.yAxisLabelColor", type: "color", label: "Y-Axis Label Color", default: "#6b7280", group: "Chart Style" },
    { key: "config.xAxisLabelSize", type: "number", label: "X-Axis Label Size", default: 11, min: 8, max: 20, step: 1, group: "Chart Style", suffix: "px" },
    { key: "config.yAxisLabelSize", type: "number", label: "Y-Axis Label Size", default: 11, min: 8, max: 20, step: 1, group: "Chart Style", suffix: "px" },
    { key: "config.showGridLines", type: "toggle", label: "Show Grid Lines", default: true, group: "Chart Style" },
    { key: "config.gridLineColor", type: "color", label: "Grid Line Color", default: "#e5e7eb", group: "Chart Style", condition: { key: "config.showGridLines", equals: true } },
    { key: "config.gridLineStyle", type: "select", label: "Grid Line Style", default: "solid", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"]], group: "Chart Style", condition: { key: "config.showGridLines", equals: true } },
    { key: "config.gridLineWidth", type: "number", label: "Grid Line Width", default: 1, min: 0, max: 4, step: 0.5, group: "Chart Style", suffix: "px", condition: { key: "config.showGridLines", equals: true } },
    { key: "config.chartBgColor", type: "color", label: "Chart Background", default: "transparent", group: "Chart Style" },
    { key: "config.titleColor", type: "color", label: "Title Color", default: "#1f2937", group: "Chart Style" },
    { key: "config.titleFontSize", type: "number", label: "Title Font Size", default: 14, min: 10, max: 32, step: 1, group: "Chart Style", suffix: "px" },
    { key: "config.titleFontWeight", type: "select", label: "Title Font Weight", default: "600", options: [["300", "Light"], ["400", "Regular"], ["500", "Medium"], ["600", "Semibold"], ["700", "Bold"], ["800", "Extra Bold"]], group: "Chart Style" },
    { key: "config.tooltipBgColor", type: "color", label: "Tooltip Background", default: "#1f2937", group: "Chart Style" },
    { key: "config.tooltipTextColor", type: "color", label: "Tooltip Text Color", default: "#f9fafb", group: "Chart Style" },
    { key: "config.tooltipFontSize", type: "number", label: "Tooltip Font Size", default: 12, min: 8, max: 20, step: 1, group: "Chart Style", suffix: "px" },
    { key: "config.tooltipBorderRadius", type: "number", label: "Tooltip Border Radius", default: 6, min: 0, max: 20, step: 1, group: "Chart Style", suffix: "px" },
  ];

  /**
   * Factory: returns a complete typography sub-section.
   * @param {string} prefix  - e.g. "title", "value", "comparison", "subtitle"
   * @param {string} label   - display label prefix, e.g. "Title", "Value"
   * @returns {Array} property definitions
   */
  function _typographySection(prefix, label) {
    return [
      { key: "config." + prefix + "FontFamily", type: "fontFamily", label: label + " Font", default: "Inter, system-ui, sans-serif", group: label + " Typography" },
      { key: "config." + prefix + "FontSize", type: "number", label: label + " Font Size", default: prefix === "title" ? 14 : prefix === "value" ? 32 : 12, min: 8, max: 96, step: 1, group: label + " Typography", suffix: "px" },
      { key: "config." + prefix + "FontWeight", type: "select", label: label + " Font Weight", default: prefix === "value" ? "700" : "600", options: [["300", "Light"], ["400", "Regular"], ["500", "Medium"], ["600", "Semibold"], ["700", "Bold"], ["800", "Extra Bold"], ["900", "Black"]], group: label + " Typography" },
      { key: "config." + prefix + "FontStyle", type: "select", label: label + " Font Style", default: "normal", options: [["normal", "Normal"], ["italic", "Italic"], ["oblique", "Oblique"]], group: label + " Typography" },
      { key: "config." + prefix + "Color", type: "color", label: label + " Color", default: prefix === "title" ? "#1f2937" : prefix === "value" ? "#111827" : "#6b7280", group: label + " Typography" },
      { key: "config." + prefix + "LetterSpacing", type: "number", label: label + " Letter Spacing", default: prefix === "title" ? 0 : 0, min: -2, max: 10, step: 0.1, group: label + " Typography", suffix: "px" },
      { key: "config." + prefix + "LineHeight", type: "number", label: label + " Line Height", default: prefix === "title" ? 1.3 : prefix === "value" ? 1.1 : 1.5, min: 0.8, max: 3, step: 0.1, group: label + " Typography" },
      { key: "config." + prefix + "Align", type: "select", label: label + " Alignment", default: prefix === "title" ? "left" : "center", options: [["left", "Left"], ["center", "Center"], ["right", "Right"], ["justify", "Justify"]], group: label + " Typography" },
      { key: "config." + prefix + "Transform", type: "select", label: label + " Transform", default: "none", options: [["none", "None"], ["uppercase", "Uppercase"], ["lowercase", "Lowercase"], ["capitalize", "Capitalize"]], group: label + " Typography" },
      { key: "config." + prefix + "Decoration", type: "select", label: label + " Decoration", default: "none", options: [["none", "None"], ["underline", "Underline"], ["overline", "Overline"], ["line-through", "Line Through"]], group: label + " Typography" },
    ];
  }

  var BORDER_PROPS = [
    { key: "config.borderTopWidth", type: "number", label: "Top Width", default: 0, min: 0, max: 20, step: 1, group: "Border", suffix: "px" },
    { key: "config.borderTopStyle", type: "select", label: "Top Style", default: "solid", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"], ["none", "None"]], group: "Border" },
    { key: "config.borderTopColor", type: "color", label: "Top Color", default: "#e2e8f0", group: "Border" },
    { key: "config.borderRightWidth", type: "number", label: "Right Width", default: 0, min: 0, max: 20, step: 1, group: "Border", suffix: "px" },
    { key: "config.borderRightStyle", type: "select", label: "Right Style", default: "solid", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"], ["none", "None"]], group: "Border" },
    { key: "config.borderRightColor", type: "color", label: "Right Color", default: "#e2e8f0", group: "Border" },
    { key: "config.borderBottomWidth", type: "number", label: "Bottom Width", default: 0, min: 0, max: 20, step: 1, group: "Border", suffix: "px" },
    { key: "config.borderBottomStyle", type: "select", label: "Bottom Style", default: "solid", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"], ["none", "None"]], group: "Border" },
    { key: "config.borderBottomColor", type: "color", label: "Bottom Color", default: "#e2e8f0", group: "Border" },
    { key: "config.borderLeftWidth", type: "number", label: "Left Width", default: 0, min: 0, max: 20, step: 1, group: "Border", suffix: "px" },
    { key: "config.borderLeftStyle", type: "select", label: "Left Style", default: "solid", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"], ["none", "None"]], group: "Border" },
    { key: "config.borderLeftColor", type: "color", label: "Left Color", default: "#e2e8f0", group: "Border" },
    { key: "config.borderRadiusTopLeft", type: "number", label: "Top-Left Radius", default: 8, min: 0, max: 50, step: 1, group: "Border", suffix: "px" },
    { key: "config.borderRadiusTopRight", type: "number", label: "Top-Right Radius", default: 8, min: 0, max: 50, step: 1, group: "Border", suffix: "px" },
    { key: "config.borderRadiusBottomLeft", type: "number", label: "Bottom-Left Radius", default: 8, min: 0, max: 50, step: 1, group: "Border", suffix: "px" },
    { key: "config.borderRadiusBottomRight", type: "number", label: "Bottom-Right Radius", default: 8, min: 0, max: 50, step: 1, group: "Border", suffix: "px" },
    { key: "config.borderShapePct", type: "range", label: "Corner Shape", default: 0, min: 0, max: 100, step: 1, group: "Border", helpText: "Drag to round corners: 0 = Theme default · 100 = Rounded", suffix: "%" },
  ];

  var BACKGROUND_PROPS = [
    { key: "config.bgType", type: "select", label: "Background Type", default: "solid", options: [["solid", "Solid Color"], ["transparent", "Transparent"], ["linear-gradient", "Linear Gradient"], ["radial-gradient", "Radial Gradient"], ["image", "Image"]], group: "Background" },
    { key: "config.bgColor", type: "color", label: "Background Color", default: "#ffffff", group: "Background", condition: { key: "config.bgType", equals: "solid" } },
    { key: "config.bgColor2", type: "color", label: "Gradient End Color", default: "#3b82f6", group: "Background", condition: { key: "config.bgType", in: ["linear-gradient", "radial-gradient"] } },
    { key: "config.bgAngle", type: "number", label: "Gradient Angle", default: 135, min: 0, max: 360, step: 1, group: "Background", suffix: "deg", condition: { key: "config.bgType", equals: "linear-gradient" } },
    { key: "config.bgImageUrl", type: "text", label: "Image URL", default: "", group: "Background", placeholder: "https://...", condition: { key: "config.bgType", equals: "image" } },
    { key: "config.bgOpacity", type: "range", label: "Background Opacity", default: 1, min: 0, max: 1, step: 0.05, group: "Background" },
    { key: "config.bgPosition", type: "select", label: "Image Position", default: "center", options: [["center", "Center"], ["top", "Top"], ["bottom", "Bottom"], ["left", "Left"], ["right", "Right"], ["cover", "Cover"], ["contain", "Contain"]], group: "Background", condition: { key: "config.bgType", equals: "image" } },
    { key: "config.bgSize", type: "select", label: "Image Size", default: "cover", options: [["auto", "Auto"], ["cover", "Cover"], ["contain", "Contain"], ["100%", "Stretch"]], group: "Background", condition: { key: "config.bgType", equals: "image" } },
    { key: "config.bgRepeat", type: "select", label: "Image Repeat", default: "no-repeat", options: [["no-repeat", "No Repeat"], ["repeat", "Repeat"], ["repeat-x", "Repeat X"], ["repeat-y", "Repeat Y"]], group: "Background", condition: { key: "config.bgType", equals: "image" } },
  ];

  var SPACING_PROPS = [
    { key: "config.paddingTop", type: "number", label: "Padding Top", default: 12, min: 0, max: 100, step: 1, group: "Spacing", suffix: "px" },
    { key: "config.paddingRight", type: "number", label: "Padding Right", default: 12, min: 0, max: 100, step: 1, group: "Spacing", suffix: "px" },
    { key: "config.paddingBottom", type: "number", label: "Padding Bottom", default: 12, min: 0, max: 100, step: 1, group: "Spacing", suffix: "px" },
    { key: "config.paddingLeft", type: "number", label: "Padding Left", default: 12, min: 0, max: 100, step: 1, group: "Spacing", suffix: "px" },
    { key: "config.marginTop", type: "number", label: "Margin Top", default: 0, min: 0, max: 100, step: 1, group: "Spacing", suffix: "px" },
    { key: "config.marginRight", type: "number", label: "Margin Right", default: 0, min: 0, max: 100, step: 1, group: "Spacing", suffix: "px" },
    { key: "config.marginBottom", type: "number", label: "Margin Bottom", default: 0, min: 0, max: 100, step: 1, group: "Spacing", suffix: "px" },
    { key: "config.marginLeft", type: "number", label: "Margin Left", default: 0, min: 0, max: 100, step: 1, group: "Spacing", suffix: "px" },
  ];

  var EFFECTS_PROPS = [
    { key: "config.shadowColor", type: "color", label: "Shadow Color", default: "#000000", group: "Effects" },
    { key: "config.shadowOpacity", type: "range", label: "Shadow Opacity", default: 0.1, min: 0, max: 1, step: 0.05, group: "Effects" },
    { key: "config.shadowBlur", type: "number", label: "Shadow Blur", default: 12, min: 0, max: 100, step: 1, group: "Effects", suffix: "px" },
    { key: "config.shadowSpread", type: "number", label: "Shadow Spread", default: 0, min: -20, max: 40, step: 1, group: "Effects", suffix: "px" },
    { key: "config.shadowX", type: "number", label: "Shadow Offset X", default: 0, min: -40, max: 40, step: 1, group: "Effects", suffix: "px" },
    { key: "config.shadowY", type: "number", label: "Shadow Offset Y", default: 4, min: -40, max: 40, step: 1, group: "Effects", suffix: "px" },
    { key: "config.hoverShadow", type: "toggle", label: "Hover Shadow", default: false, group: "Effects" },
    { key: "config.hoverShadowColor", type: "color", label: "Hover Shadow Color", default: "#3b82f6", group: "Effects", condition: { key: "config.hoverShadow", equals: true } },
    { key: "config.hoverShadowBlur", type: "number", label: "Hover Shadow Blur", default: 20, min: 0, max: 100, step: 1, group: "Effects", suffix: "px", condition: { key: "config.hoverShadow", equals: true } },
    { key: "config.hoverTransform", type: "select", label: "Hover Transform", default: "none", options: [["none", "None"], ["scale(1.02)", "Scale Up 2%"], ["scale(1.05)", "Scale Up 5%"], ["translateY(-2px)", "Lift 2px"], ["translateY(-4px)", "Lift 4px"]], group: "Effects" },
    { key: "config.hoverOpacity", type: "range", label: "Hover Opacity", default: 1, min: 0.5, max: 1, step: 0.05, group: "Effects" },
    { key: "config.backdropBlur", type: "number", label: "Backdrop Blur", default: 0, min: 0, max: 40, step: 1, group: "Effects", suffix: "px" },
    { key: "config.glassmorphism", type: "toggle", label: "Glassmorphism", default: false, group: "Effects" },
    { key: "config.glassOpacity", type: "range", label: "Glass Opacity", default: 0.6, min: 0.1, max: 1, step: 0.05, group: "Effects", condition: { key: "config.glassmorphism", equals: true } },
    { key: "config.glassBorder", type: "toggle", label: "Glass Border", default: true, group: "Effects", condition: { key: "config.glassmorphism", equals: true } },
    { key: "config.glow", type: "toggle", label: "Glow Effect", default: false, group: "Effects" },
    { key: "config.glowColor", type: "color", label: "Glow Color", default: "#3b82f6", group: "Effects", condition: { key: "config.glow", equals: true } },
    { key: "config.glowSize", type: "number", label: "Glow Size", default: 8, min: 1, max: 30, step: 1, group: "Effects", suffix: "px", condition: { key: "config.glow", equals: true } },
  ];

  var INTERACTION_PROPS = [
    { key: "config.crossFilter", type: "toggle", label: "Cross-Filter on Click", default: true, group: "Interaction", helpText: "Click a data point to filter every other widget on the page by that value (click again to clear)" },
    { key: "config.onClick", type: "select", label: "On Click Action", default: "none", options: [["none", "None"], ["details", "Show Details"], ["navigate", "Navigate"], ["filter", "Apply Filter"], ["url", "Open URL"], ["drilldown", "Drill Down"], ["tooltip", "Show Tooltip"], ["modal", "Open Modal"], ["refresh", "Refresh Data"]], group: "Interaction", helpText: "Action triggered when the widget is clicked" },
    { key: "config.onClickTarget", type: "text", label: "Target", default: "", group: "Interaction", placeholder: "Dashboard ID or URL", condition: { key: "config.onClick", in: ["navigate", "url"] } },
    { key: "config.onClickNewTab", type: "toggle", label: "Open in New Tab", default: false, group: "Interaction", condition: { key: "config.onClick", in: ["navigate", "url"] } },
    { key: "config.tooltipContent", type: "textarea", label: "Tooltip Content", default: "", rows: 3, group: "Interaction", placeholder: "HTML or markdown content", condition: { key: "config.onClick", equals: "tooltip" } },
    { key: "config.modalContent", type: "textarea", label: "Modal Content", default: "", rows: 4, group: "Interaction", placeholder: "HTML or markdown content", condition: { key: "config.onClick", equals: "modal" } },
    { key: "config.drillColumns", type: "text", label: "Drill-Down Columns", default: "", group: "Interaction", placeholder: "col1, col2, col3", helpText: "Comma-separated column names to pass as drill context", condition: { key: "config.onClick", equals: "drilldown" } },
    { key: "config.drillTarget", type: "text", label: "Drill Target Dashboard", default: "", group: "Interaction", placeholder: "Dashboard ID", condition: { key: "config.onClick", equals: "drilldown" } },
  ];

  var COLOR_SYSTEM_PROPS = [
    { key: "config.themePrimary", type: "color", label: "Primary", default: "#3b82f6", group: "Color System" },
    { key: "config.themeSecondary", type: "color", label: "Secondary", default: "#8b5cf6", group: "Color System" },
    { key: "config.themeText", type: "color", label: "Text", default: "#1f2937", group: "Color System" },
    { key: "config.themeMutedText", type: "color", label: "Muted Text", default: "#6b7280", group: "Color System" },
    { key: "config.themeBackground", type: "color", label: "Background", default: "#ffffff", group: "Color System" },
    { key: "config.themeBorder", type: "color", label: "Border", default: "#e5e7eb", group: "Color System" },
    { key: "config.themePositive", type: "color", label: "Positive", default: "#10b981", group: "Color System", helpText: "Used for positive trends and success states" },
    { key: "config.themeNegative", type: "color", label: "Negative", default: "#ef4444", group: "Color System", helpText: "Used for negative trends and error states" },
    { key: "config.themeWarning", type: "color", label: "Warning", default: "#f59e0b", group: "Color System" },
    { key: "config.themeHover", type: "color", label: "Hover", default: "#f3f4f6", group: "Color System" },
    { key: "config.themeSelected", type: "color", label: "Selected", default: "#dbeafe", group: "Color System" },
  ];

  var RESPONSIVE_PROPS = [
    { key: "config.hideOnMobile", type: "toggle", label: "Hide on Mobile", default: false, group: "Responsive" },
    { key: "config.hideOnTablet", type: "toggle", label: "Hide on Tablet", default: false, group: "Responsive" },
    { key: "config.hideOnDesktop", type: "toggle", label: "Hide on Desktop", default: false, group: "Responsive" },
    { key: "config.minWidth", type: "number", label: "Min Width (px)", default: 0, min: 0, max: 2000, step: 1, group: "Responsive" },
    { key: "config.maxWidth", type: "number", label: "Max Width (px)", default: 0, min: 0, max: 4000, step: 1, group: "Responsive", helpText: "0 = no limit" },
  ];

  /* ═══════════════════════════════════════════════════════════════════════
     CHART DATA SECTION (shared across all chart types)
     ═══════════════════════════════════════════════════════════════════════ */

  var CHART_DATA_PROPS = [
    { key: "config.table", type: "tableSelect", label: "Table", default: "", group: "Data", helpText: "Source table" },
    { key: "config.sql", type: "textarea", label: "SQL Query", default: "", rows: 5, group: "Data", placeholder: "SELECT label, value FROM table", helpText: "DuckDB-compatible SQL. Columns: label, value" },
    { key: "config.labelColumn", type: "text", label: "Label Column", default: "label", group: "Data", helpText: "Column used for chart labels" },
    { key: "config.valueColumn", type: "text", label: "Value Column", default: "value", group: "Data", helpText: "Column used for chart values" },
    { key: "config.seriesColumn", type: "text", label: "Series Column", default: "", group: "Data", helpText: "Optional column for multi-series grouping" },
    { key: "config.aggregation", type: "select", label: "Aggregation", default: "sum", options: [["sum", "Sum"], ["avg", "Average"], ["count", "Count"], ["min", "Minimum"], ["max", "Maximum"], ["countDistinct", "Count Distinct"], ["none", "None"]], group: "Data" },
    { key: "config.sortBy", type: "select", label: "Sort By", default: "none", options: [["none", "None"], ["label", "Label (A-Z)"], ["labelDesc", "Label (Z-A)"], ["value", "Value (High-Low)"], ["valueDesc", "Value (Low-High)"]], group: "Data" },
    { key: "config.limit", type: "number", label: "Row Limit", default: 0, min: 0, max: 10000, step: 1, group: "Data", helpText: "0 = no limit" },
  ];

  /* ═══════════════════════════════════════════════════════════════════════
     CHART VISUAL OPTIONS (shared across chart types)
     ═══════════════════════════════════════════════════════════════════════ */

  var CHART_VISUAL_PROPS = [
    { key: "config.chartHeight", type: "number", label: "Chart Area Height", default: 300, min: 80, max: 1000, step: 10, group: "Chart Visual", suffix: "px" },
    { key: "config.lineThickness", type: "select", label: "Line Thickness", default: "2", options: [["1", "Thin (1px)"], ["2", "Regular (2px)"], ["3", "Medium (3px)"], ["4", "Thick (4px)"], ["5", "Heavy (5px)"]], group: "Chart Visual" },
    { key: "config.lineStyle", type: "select", label: "Line Style", default: "solid", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"]], group: "Chart Visual" },
    { key: "config.curveType", type: "select", label: "Curve Type", default: "smooth", options: [["smooth", "Smooth"], ["linear", "Linear"], ["step", "Step"]], group: "Chart Visual" },
    { key: "config.fillOpacity", type: "range", label: "Fill Opacity", default: 30, min: 0, max: 100, step: 5, group: "Chart Visual", suffix: "%" },
    { key: "config.showGrid", type: "toggle", label: "Show Grid", default: true, group: "Chart Visual" },
    { key: "config.showAxes", type: "toggle", label: "Show Axes", default: true, group: "Chart Visual" },
    { key: "config.showLabels", type: "toggle", label: "Show Labels", default: false, group: "Chart Visual" },
    { key: "config.showLegend", type: "toggle", label: "Show Legend", default: true, group: "Chart Visual" },
    { key: "config.showTooltip", type: "toggle", label: "Show Tooltip", default: true, group: "Chart Visual" },
    { key: "config.showDataPoints", type: "toggle", label: "Show Data Points", default: false, group: "Chart Visual" },
    { key: "config.dataPointSize", type: "number", label: "Data Point Size", default: 4, min: 2, max: 10, step: 1, group: "Chart Visual", suffix: "px", condition: { key: "config.showDataPoints", equals: true } },
    { key: "config.chartGradientColors", type: "colorArray", label: "Gradient Colors", default: [], group: "Chart Visual", helpText: "Optional gradient fill stops" },
    { key: "config.animationEnabled", type: "toggle", label: "Animation", default: true, group: "Chart Visual" },
    { key: "config.animationDuration", type: "number", label: "Animation Duration", default: 600, min: 100, max: 2000, step: 50, group: "Chart Visual", suffix: "ms", condition: { key: "config.animationEnabled", equals: true } },
    { key: "config.chartPadding", type: "number", label: "Chart Padding", default: 8, min: 0, max: 40, step: 2, group: "Chart Visual", suffix: "px" },
    { key: "config.referenceLines", type: "referenceLines", label: "Reference Lines / Bands", default: [], group: "Chart Visual", helpText: "Horizontal reference lines (targets, thresholds) or shaded bands. Each entry: value, optional label, color, and type (line or band with an upper value)." },
  ];

  /* ═══════════════════════════════════════════════════════════════════════
     CHART-SPECIFIC OPTIONS (built per chart type)
     ═══════════════════════════════════════════════════════════════════════ */

  var CHART_LINE_PROPS = [
    { key: "config.lineTension", type: "range", label: "Line Tension", default: 0.4, min: 0, max: 1, step: 0.05, group: "Line Options" },
    { key: "config.showArea", type: "toggle", label: "Show Area Fill", default: false, group: "Line Options" },
    { key: "config.stepped", type: "toggle", label: "Stepped Line", default: false, group: "Line Options" },
  ];

  var CHART_BAR_PROPS = [
    { key: "config.barThickness", type: "number", label: "Bar Thickness", default: 0, min: 0, max: 100, step: 1, group: "Bar Options", helpText: "0 = auto" },
    { key: "config.barMaxThickness", type: "number", label: "Max Bar Thickness", default: 50, min: 10, max: 200, step: 5, group: "Bar Options", suffix: "px" },
    { key: "config.barGap", type: "number", label: "Bar Gap", default: 2, min: 0, max: 20, step: 1, group: "Bar Options", suffix: "px" },
    { key: "config.stacked", type: "toggle", label: "Stacked", default: false, group: "Bar Options" },
    { key: "config.horizontal", type: "toggle", label: "Horizontal", default: false, group: "Bar Options" },
    { key: "config.barBorderRadius", type: "number", label: "Bar Border Radius", default: 0, min: 0, max: 20, step: 1, group: "Bar Options", suffix: "px" },
  ];

  var CHART_PIE_PROPS = [
    { key: "config.cutout", type: "range", label: "Cutout (Doughnut)", default: 50, min: 0, max: 90, step: 5, group: "Pie Options", suffix: "%", condition: { key: "widget.type", in: ["doughnut", "polar"] } },
    { key: "config.startAngle", type: "number", label: "Start Angle", default: -90, min: -360, max: 360, step: 5, group: "Pie Options", suffix: "deg" },
    { key: "config.endAngle", type: "number", label: "End Angle", default: 90, min: -360, max: 360, step: 5, group: "Pie Options", suffix: "deg" },
    { key: "config.pieOffset", type: "number", label: "Offset", default: 0, min: 0, max: 30, step: 1, group: "Pie Options", suffix: "px" },
    { key: "config.pieHoverOffset", type: "number", label: "Hover Offset", default: 8, min: 0, max: 30, step: 1, group: "Pie Options", suffix: "px" },
  ];

  var CHART_SCATTER_PROPS = [
    { key: "config.pointStyle", type: "select", label: "Point Style", default: "circle", options: [["circle", "Circle"], ["cross", "Cross"], ["crossRot", "Cross Rotated"], ["dash", "Dash"], ["line", "Line"], ["rect", "Rect"], ["rectRounded", "Rect Rounded"], ["rectRot", "Rect Rotated"], ["star", "Star"], ["triangle", "Triangle"]], group: "Scatter Options" },
    { key: "config.showTrendline", type: "toggle", label: "Show Trend Line", default: false, group: "Scatter Options" },
    { key: "config.trendlineColor", type: "color", label: "Trend Line Color", default: "#9ca3af", group: "Scatter Options", condition: { key: "config.showTrendline", equals: true } },
    { key: "config.trendlineWidth", type: "number", label: "Trend Line Width", default: 2, min: 1, max: 6, step: 1, group: "Scatter Options", suffix: "px", condition: { key: "config.showTrendline", equals: true } },
    { key: "config.trendlineStyle", type: "select", label: "Trend Line Style", default: "dashed", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"]], group: "Scatter Options", condition: { key: "config.showTrendline", equals: true } },
  ];

  var CHART_GAUGE_PROPS = [
    { key: "config.gaugeMin", type: "number", label: "Min Value", default: 0, min: -100000, max: 100000, step: 1, group: "Gauge Options" },
    { key: "config.gaugeMax", type: "number", label: "Max Value", default: 100, min: -100000, max: 100000, step: 1, group: "Gauge Options" },
    { key: "config.gaugeTarget", type: "number", label: "Target Value", default: 75, min: -100000, max: 100000, step: 1, group: "Gauge Options" },
    { key: "config.gaugeStartColor", type: "color", label: "Start Color", default: "#ef4444", group: "Gauge Options" },
    { key: "config.gaugeMidColor", type: "color", label: "Mid Color", default: "#f59e0b", group: "Gauge Options" },
    { key: "config.gaugeEndColor", type: "color", label: "End Color", default: "#10b981", group: "Gauge Options" },
    { key: "config.gaugeThickness", type: "range", label: "Arc Thickness", default: 20, min: 5, max: 60, step: 1, group: "Gauge Options", suffix: "px" },
  ];

  var CHART_HEATMAP_PROPS = [
    { key: "config.heatmapColorLow", type: "color", label: "Low Value Color", default: "#dbeafe", group: "Heatmap Options" },
    { key: "config.heatmapColorHigh", type: "color", label: "High Value Color", default: "#1d4ed8", group: "Heatmap Options" },
    { key: "config.heatmapCellRadius", type: "number", label: "Cell Border Radius", default: 2, min: 0, max: 12, step: 1, group: "Heatmap Options", suffix: "px" },
    { key: "config.heatmapCellGap", type: "number", label: "Cell Gap", default: 2, min: 0, max: 10, step: 1, group: "Heatmap Options", suffix: "px" },
  ];

  var CHART_TREEMAP_PROPS = [
    { key: "config.treemapPadding", type: "number", label: "Cell Padding", default: 2, min: 0, max: 10, step: 1, group: "Treemap Options", suffix: "px" },
    { key: "config.treemapShowLabels", type: "toggle", label: "Show Labels", default: true, group: "Treemap Options" },
    { key: "config.treemapLabelSize", type: "number", label: "Label Size", default: 11, min: 8, max: 20, step: 1, group: "Treemap Options", suffix: "px" },
  ];

  var CHART_FUNNEL_PROPS = [
    { key: "config.funnelOrientation", type: "select", label: "Orientation", default: "vertical", options: [["vertical", "Vertical"], ["horizontal", "Horizontal"]], group: "Funnel Options" },
    { key: "config.funnelLabelPosition", type: "select", label: "Label Position", default: "inside", options: [["inside", "Inside"], ["outside", "Outside"]], group: "Funnel Options" },
    { key: "config.funnelNeckWidth", type: "range", label: "Neck Width", default: 30, min: 0, max: 80, step: 5, group: "Funnel Options", suffix: "%" },
  ];

  var CHART_WATERFALL_PROPS = [
    { key: "config.waterfallPositiveColor", type: "color", label: "Positive Color", default: "#10b981", group: "Waterfall Options" },
    { key: "config.waterfallNegativeColor", type: "color", label: "Negative Color", default: "#ef4444", group: "Waterfall Options" },
    { key: "config.waterfallConnectorLine", type: "toggle", label: "Connector Line", default: true, group: "Waterfall Options" },
  ];

  var CHART_SPARKLINE_PROPS = [
    { key: "config.sparklineData", type: "text", label: "Sparkline Values", default: "", group: "Sparkline Options", placeholder: "1,2,3,4,5", helpText: "Comma-separated values (used when no SQL)" },
    { key: "config.sparklineColor", type: "color", label: "Line Color", default: "#3b82f6", group: "Sparkline Options" },
    { key: "config.sparklineFill", type: "toggle", label: "Fill Area", default: true, group: "Sparkline Options" },
    { key: "config.sparklineLineWidth", type: "number", label: "Line Width", default: 2, min: 1, max: 5, step: 1, group: "Sparkline Options", suffix: "px" },
  ];

  var CHART_BUBBLE_PROPS = [
    { key: "config.bubbleSizeColumn", type: "text", label: "Size Column", default: "size", group: "Bubble Options", helpText: "Column name for bubble radius" },
    { key: "config.bubbleMinRadius", type: "number", label: "Min Radius", default: 3, min: 1, max: 20, step: 1, group: "Bubble Options", suffix: "px" },
    { key: "config.bubbleMaxRadius", type: "number", label: "Max Radius", default: 30, min: 5, max: 100, step: 1, group: "Bubble Options", suffix: "px" },
  ];

  var CHART_POLAR_PROPS = [
    { key: "config.polarStyle", type: "select", label: "Polar Style", default: "radar", options: [["radar", "Radar"], ["polarArea", "Polar Area"]], group: "Polar Options" },
    { key: "config.polarScaleType", type: "select", label: "Scale Type", default: "linear", options: [["linear", "Linear"], ["logarithmic", "Logarithmic"]], group: "Polar Options" },
  ];

  var CHART_BOX_PLOT_PROPS = [
    { key: "config.boxPlotShowOutliers", type: "toggle", label: "Show Outliers", default: true, group: "Box Plot Options" },
    { key: "config.boxPlotMedianColor", type: "color", label: "Median Color", default: "#ef4444", group: "Box Plot Options" },
    { key: "config.boxPlotBoxColor", type: "color", label: "Box Color", default: "#3b82f680", group: "Box Plot Options" },
  ];

  var CHART_HISTOGRAM_PROPS = [
    { key: "config.histogramBins", type: "number", label: "Number of Bins", default: 10, min: 2, max: 100, step: 1, group: "Histogram Options" },
    { key: "config.histogramColor", type: "color", label: "Bar Color", default: "#3b82f6", group: "Histogram Options" },
    { key: "config.histogramBorderColor", type: "color", label: "Bar Border Color", default: "#1d4ed8", group: "Histogram Options" },
  ];

  var CHART_BULLET_PROPS = [
    { key: "config.bulletRanges", type: "text", label: "Range Values", default: "30,60,100", group: "Bullet Options", placeholder: "e.g. 30,60,100" },
    { key: "config.bulletColors", type: "colorArray", label: "Range Colors", default: ["#e5e7eb", "#93c5fd", "#3b82f6"], group: "Bullet Options" },
    { key: "config.bulletMarkerColor", type: "color", label: "Marker Color", default: "#111827", group: "Bullet Options" },
  ];

  var CHART_PYRAMID_PROPS = [
    { key: "config.pyramidOrientation", type: "select", label: "Orientation", default: "vertical", options: [["vertical", "Vertical"], ["horizontal", "Horizontal"]], group: "Pyramid Options" },
    { key: "config.pyramidColorByLevel", type: "toggle", label: "Color by Level", default: true, group: "Pyramid Options" },
  ];

  var CHART_SUNBURST_PROPS = [
    { key: "config.sunburstInnerRadius", type: "number", label: "Inner Radius", default: 30, min: 10, max: 80, step: 5, group: "Sunburst Options", suffix: "px" },
    { key: "config.sunburstPadAngle", type: "range", label: "Pad Angle", default: 0.02, min: 0, max: 0.2, step: 0.01, group: "Sunburst Options" },
  ];

  var CHART_WORDCLOUD_PROPS = [
    { key: "config.wordcloudColorMode", type: "select", label: "Color Mode", default: "palette", options: [["palette", "From Palette"], ["random", "Random"], ["single", "Single Color"]], group: "Word Cloud Options" },
    { key: "config.wordcloudRotation", type: "range", label: "Rotation Range", default: 30, min: 0, max: 90, step: 5, group: "Word Cloud Options", suffix: "deg" },
    { key: "config.wordcloudMinFont", type: "number", label: "Min Font Size", default: 10, min: 6, max: 30, step: 1, group: "Word Cloud Options", suffix: "px" },
    { key: "config.wordcloudMaxFont", type: "number", label: "Max Font Size", default: 60, min: 20, max: 120, step: 5, group: "Word Cloud Options", suffix: "px" },
  ];

  var CHART_PERCENT_BAR_PROPS = [
    { key: "config.percentBarStacked", type: "toggle", label: "100% Stacked", default: true, group: "Percent Bar Options" },
    { key: "config.percentBarShowTotal", type: "toggle", label: "Show Total Label", default: true, group: "Percent Bar Options" },
  ];

  var CHART_STACKED_AREA_PROPS = [
    { key: "config.stackedAreaFillOpacity", type: "range", label: "Area Fill Opacity", default: 50, min: 0, max: 100, step: 5, group: "Stacked Area Options", suffix: "%" },
    { key: "config.stackedAreaBorderColor", type: "color", label: "Border Color", default: "rgba(255,255,255,0.3)", group: "Stacked Area Options" },
  ];

  /* ═══════════════════════════════════════════════════════════════════════
     COMBO CHART SPECIFIC
     ═══════════════════════════════════════════════════════════════════════ */

  var CHART_COMBO_PROPS = [
    { key: "config.comboTypes", type: "text", label: "Series Types", default: "bar,line", group: "Combo Options", placeholder: "bar,line,bar", helpText: "Comma-separated: bar, line, scatter, area" },
    { key: "config.comboYAxisIDs", type: "text", label: "Y-Axis IDs", default: "", group: "Combo Options", placeholder: "y,y2", helpText: "Comma-separated axis IDs for each series" },
    { key: "config.comboY2Title", type: "text", label: "Y2 Axis Title", default: "", group: "Combo Options" },
    { key: "config.comboY2Position", type: "select", label: "Y2 Axis Position", default: "right", options: [["left", "Left"], ["right", "Right"]], group: "Combo Options" },
  ];

  /* ═══════════════════════════════════════════════════════════════════════
     KPI-SPECIFIC PROPERTIES
     ═══════════════════════════════════════════════════════════════════════ */

  var KPI_STYLES = [
    ["simple", "Simple"],
    ["trend", "Trend"],
    ["percentage", "Percentage"],
    ["sparkline", "Sparkline"],
    ["sparklineDelta", "Sparkline + Delta"],
    ["donut", "Donut KPI"],
    ["miniLine", "Mini Line Chart"],
    ["miniBar", "Mini Bar Chart"],
    ["progress", "Progress Bar"],
    ["radial", "Radial Gauge"],
    ["comparison", "Comparison"],
    ["targetActual", "Target vs Actual"],
    ["icon", "Icon Card"],
    ["status", "Status Indicator"],
    ["multiValue", "Multi-Value"],
    ["previousPeriod", "Previous Period"],
  ];

  var KPI_CONTENT_PROPS = [
    { key: "config.kpiStyle", type: "select", label: "KPI Style", default: "simple", options: KPI_STYLES, group: "Content", helpText: "Visual style of the KPI card" },
    { key: "config.label", type: "text", label: "Label", default: "KPI", group: "Content", placeholder: "Total Revenue" },
    { key: "config.value", type: "text", label: "Value", default: "", group: "Content", placeholder: "Dynamic from SQL", helpText: "Leave blank to use SQL result" },
    { key: "config.prefix", type: "text", label: "Prefix", default: "", group: "Content", placeholder: "$" },
    { key: "config.suffix", type: "text", label: "Suffix", default: "", group: "Content", placeholder: "%" },
    { key: "config.currency", type: "select", label: "Currency", default: "none", options: [["none", "None"], ["$", "USD ($)"], ["€", "EUR (€)"], ["£", "GBP (£)"], ["¥", "JPY (¥)"], ["₹", "INR (₹)"]], group: "Content" },
    { key: "config.decimals", type: "number", label: "Decimals", default: 0, min: 0, max: 10, step: 1, group: "Content" },
    { key: "config.numberFormat", type: "select", label: "Number Format", default: "auto", options: [["auto", "Auto (K/M/B)"], ["currency", "Currency"], ["percentage", "Percentage"], ["scientific", "Scientific"], ["compact", "Compact"]], group: "Content" },
    { key: "config.abbreviate", type: "toggle", label: "Abbreviate Large Numbers", default: true, group: "Content" },
    { key: "config.description", type: "text", label: "Description", default: "", group: "Content", placeholder: "Optional description text" },
    { key: "config.subtitle", type: "text", label: "Subtitle", default: "", group: "Content", placeholder: "Last 30 days" },
    { key: "config.table", type: "tableSelect", label: "Data Table", default: "", group: "Data" },
    { key: "config.sql", type: "textarea", label: "SQL Query", default: "", rows: 5, group: "Data", placeholder: "SELECT value FROM table" },
    { key: "config.dataSource", type: "select", label: "Data Source", default: "query", options: [["query", "SQL Query"], ["static", "Static Value"], ["expression", "Expression"]], group: "Data" },
    { key: "config.showDateRange", type: "toggle", label: "Show Date Range", default: false, group: "Content" },
    { key: "config.dateRangeLabel", type: "text", label: "Date Range Text", default: "Last 30 days", group: "Content", condition: { key: "config.showDateRange", equals: true } },

    // Trend
    { key: "config.showTrend", type: "toggle", label: "Show Trend", default: false, group: "Trend" },
    { key: "config.trendValue", type: "number", label: "Trend Value (%)", default: 0, min: -1000, max: 1000, step: 0.1, group: "Trend", condition: { key: "config.showTrend", equals: true } },
    { key: "config.trendDirection", type: "select", label: "Trend Direction", default: "auto", options: [["auto", "Auto"], ["up", "Up"], ["down", "Down"], ["flat", "Flat"]], group: "Trend", condition: { key: "config.showTrend", equals: true } },
    { key: "config.comparisonValue", type: "text", label: "Comparison Value", default: "", group: "Trend", placeholder: "e.g. 15000", condition: { key: "config.showTrend", equals: true } },
    { key: "config.comparisonLabel", type: "text", label: "Comparison Label", default: "Previous Period", group: "Trend", condition: { key: "config.showTrend", equals: true } },
    { key: "config.positiveColor", type: "color", label: "Positive Color", default: "#10b981", group: "Trend", condition: { key: "config.showTrend", equals: true } },
    { key: "config.negativeColor", type: "color", label: "Negative Color", default: "#ef4444", group: "Trend", condition: { key: "config.showTrend", equals: true } },

    // Sparkline
    { key: "config.showSparkline", type: "toggle", label: "Show Sparkline", default: false, group: "Sparkline" },
    { key: "config.sparkSql", type: "textarea", label: "Sparkline SQL", default: "", rows: 3, group: "Sparkline", placeholder: "SELECT date, value FROM table ORDER BY date", condition: { key: "config.showSparkline", equals: true } },
    { key: "config.sparklineColor", type: "color", label: "Sparkline Color", default: "rgba(255,255,255,0.9)", group: "Sparkline", condition: { key: "config.showSparkline", equals: true } },
    { key: "config.sparklineData", type: "text", label: "Static Sparkline Data", default: "", group: "Sparkline", placeholder: "1,2,3,4,5,6,7", condition: { key: "config.showSparkline", equals: true } },

    // Icon
    { key: "config.icon", type: "iconPicker", label: "Icon", default: "", group: "Icon", placeholder: "fa-chart-line" },
    { key: "config.iconPosition", type: "select", label: "Icon Position", default: "left", options: [["left", "Left"], ["right", "Right"], ["top", "Top"]], group: "Icon" },
    { key: "config.iconSize", type: "number", label: "Icon Size", default: 32, min: 16, max: 64, step: 2, group: "Icon", suffix: "px" },
    { key: "config.iconColor", type: "color", label: "Icon Color", default: "#ffffff", group: "Icon" },

    // Progress
    { key: "config.progressValue", type: "range", label: "Progress Value", default: 50, min: 0, max: 100, step: 1, group: "Progress", condition: { key: "config.kpiStyle", equals: "progress" } },
    { key: "config.progressMax", type: "number", label: "Progress Max", default: 100, min: 1, max: 10000, step: 1, group: "Progress", condition: { key: "config.kpiStyle", equals: "progress" } },
    { key: "config.progressColor", type: "color", label: "Progress Bar Color", default: "#3b82f6", group: "Progress", condition: { key: "config.kpiStyle", equals: "progress" } },
    { key: "config.progressTrackColor", type: "color", label: "Track Color", default: "#e5e7eb", group: "Progress", condition: { key: "config.kpiStyle", equals: "progress" } },

    // Radial gauge
    { key: "config.radialMin", type: "number", label: "Radial Min", default: 0, min: -100000, max: 100000, step: 1, group: "Radial", condition: { key: "config.kpiStyle", equals: "radial" } },
    { key: "config.radialMax", type: "number", label: "Radial Max", default: 100, min: -100000, max: 100000, step: 1, group: "Radial", condition: { key: "config.kpiStyle", equals: "radial" } },
    { key: "config.radialColor", type: "color", label: "Radial Color", default: "#3b82f6", group: "Radial", condition: { key: "config.kpiStyle", equals: "radial" } },
    { key: "config.radialTrackColor", type: "color", label: "Radial Track", default: "#e5e7eb", group: "Radial", condition: { key: "config.kpiStyle", equals: "radial" } },

    // Donut KPI
    { key: "config.target", type: "number", label: "Target", default: 100, min: 0, max: 100000000, step: 1, group: "Donut", condition: { key: "config.kpiStyle", equals: "donut" }, helpText: "Ring fills to this target; centre shows % of target" },
    { key: "config.donutColor", type: "color", label: "Donut Color", default: "#10b981", group: "Donut", condition: { key: "config.kpiStyle", equals: "donut" } },
    { key: "config.donutTrackColor", type: "color", label: "Donut Track", default: "rgba(15,23,42,0.08)", group: "Donut", condition: { key: "config.kpiStyle", equals: "donut" } },

    // Status
    { key: "config.statusValue", type: "text", label: "Status Text", default: "Active", group: "Status", condition: { key: "config.kpiStyle", equals: "status" } },
    { key: "config.statusColor", type: "color", label: "Status Color", default: "#10b981", group: "Status", condition: { key: "config.kpiStyle", equals: "status" } },
    { key: "config.statusBgColor", type: "color", label: "Status Background", default: "#d1fae5", group: "Status", condition: { key: "config.kpiStyle", equals: "status" } },

    // Multi-value
    { key: "config.multiValues", type: "text", label: "Additional Values", default: "", group: "Multi-Value", placeholder: "Revenue:12000|Users:5400|Orders:320", helpText: "Pipe-delimited, colon-separated key:value pairs", condition: { key: "config.kpiStyle", equals: "multiValue" } },

    // Color / gradient
    { key: "config.color", type: "color", label: "Accent Color", default: "#3b82f6", group: "Appearance" },
    { key: "config.colorEnd", type: "color", label: "Gradient End", default: "#8b5cf6", group: "Appearance" },
    { key: "config.kpiIndex", type: "number", label: "Gradient Preset Index", default: 0, min: 0, max: 7, step: 1, group: "Appearance", helpText: "Auto-cycle through preset gradient palettes" },
    { key: "config.cf", type: "select", label: "Conditional Format", default: "auto", options: [["auto", "Auto (tint by delta)"], ["none", "Off"], ["icon", "Delta icon + tint"], ["bg", "Background tint only"]], group: "Appearance", helpText: "Measure-backed KPIs tint the card by up/down delta; 'icon' adds a round arrow badge" },
  ];

  /* ═══════════════════════════════════════════════════════════════════════
     WIDGET-TYPE SCHEMAS
     ═══════════════════════════════════════════════════════════════════════ */

  var _schemas = {};

  // Helper: merge arrays and copy group labels
  function _merge() {
    var result = [];
    for (var i = 0; i < arguments.length; i++) {
      var arr = arguments[i];
      if (!arr) continue;
      for (var j = 0; j < arr.length; j++) {
        result.push(arr[j]);
      }
    }
    return result;
  }

  // Helper: build a chart schema from shared + chart-specific props
  function _chartSchema(chartSpecificProps, extraProps) {
    return _merge(
      SQL_PROPS,
      CHART_DATA_PROPS,
      CHART_VISUAL_PROPS,
      chartSpecificProps || [],
      extraProps || [],
      CHART_STYLE_PROPS,
      TOOLTIP_PAGE_PROPS,
      _typographySection("title", "Title"),
      BORDER_PROPS,
      BACKGROUND_PROPS,
      SPACING_PROPS,
      EFFECTS_PROPS,
      INTERACTION_PROPS,
      COLOR_SYSTEM_PROPS,
      RESPONSIVE_PROPS,
      LAYOUT_PROPS
    );
  }

  // ── KPI ──────────────────────────────────────────────────────────────
  _schemas.kpi = _merge(
    KPI_CONTENT_PROPS,
    _typographySection("title", "Title"),
    _typographySection("value", "Value"),
    _typographySection("comparison", "Comparison"),
    _typographySection("subtitle", "Subtitle"),
    _typographySection("label", "Label"),
    _typographySection("chartLabels", "Chart Labels"),
    [
      { key: "config.titleColor", type: "color", label: "Title Color", default: "#ffffff", group: "Appearance" },
      { key: "config.valueColor", type: "color", label: "Value Color", default: "#ffffff", group: "Appearance" },
      { key: "config.trendBgColor", type: "color", label: "Trend Badge BG", default: "rgba(0,0,0,0.2)", group: "Appearance" },
      { key: "config.trendBadgeRadius", type: "number", label: "Trend Badge Radius", default: 12, min: 0, max: 30, step: 1, group: "Appearance", suffix: "px" },
      { key: "config.drillPage", type: "text", label: "Drill Through Page", default: "", group: "Drill Through", helpText: "Page name or # — clicking the KPI navigates there with the drill field as a filter" },
      { key: "config.drillField", type: "text", label: "Drill Field", default: "", group: "Drill Through", helpText: "Column passed as the drill-through filter (defaults to the KPI's filter column)" },
    ],
    TOOLTIP_PAGE_PROPS,
    BORDER_PROPS,
    BACKGROUND_PROPS,
    SPACING_PROPS,
    EFFECTS_PROPS,
    INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS,
    RESPONSIVE_PROPS,
    LAYOUT_PROPS
  );

  // ── Line ─────────────────────────────────────────────────────────────
  _schemas.line = _chartSchema(CHART_LINE_PROPS);

  // ── Bar ──────────────────────────────────────────────────────────────
  _schemas.bar = _chartSchema(CHART_BAR_PROPS);

  // ── Column ───────────────────────────────────────────────────────────
  _schemas.column = _chartSchema(CHART_BAR_PROPS);

  // ── Area ─────────────────────────────────────────────────────────────
  _schemas.area = _chartSchema(CHART_LINE_PROPS, [
    { key: "config.showArea", type: "toggle", label: "Show Area Fill", default: true, group: "Area Options" },
  ]);

  // ── Pie ──────────────────────────────────────────────────────────────
  _schemas.pie = _chartSchema(CHART_PIE_PROPS, []);

  // ── Doughnut ─────────────────────────────────────────────────────────
  _schemas.doughnut = _chartSchema(CHART_PIE_PROPS);

  // ── Scatter ──────────────────────────────────────────────────────────
  _schemas.scatter = _chartSchema(CHART_SCATTER_PROPS);

  // ── Bubble ───────────────────────────────────────────────────────────
  _schemas.bubble = _chartSchema(CHART_SCATTER_PROPS, CHART_BUBBLE_PROPS);

  // ── Radar ────────────────────────────────────────────────────────────
  _schemas.radar = _chartSchema([], [
    { key: "config.radarScaleType", type: "select", label: "Scale Type", default: "linear", options: [["linear", "Linear"], ["logarithmic", "Logarithmic"]], group: "Radar Options" },
    { key: "config.radarPointStyle", type: "select", label: "Point Style", default: "circle", options: [["circle", "Circle"], ["cross", "Cross"], ["rect", "Rect"], ["triangle", "Triangle"], ["star", "Star"]], group: "Radar Options" },
    { key: "config.radarFill", type: "toggle", label: "Fill Area", default: true, group: "Radar Options" },
  ]);

  // ── Combo ────────────────────────────────────────────────────────────
  _schemas.combo = _chartSchema(CHART_COMBO_PROPS, CHART_BAR_PROPS, CHART_LINE_PROPS);

  // ── Horizontal Bar ───────────────────────────────────────────────────
  _schemas.horizontalbar = _chartSchema(CHART_BAR_PROPS, [
    { key: "config.horizontal", type: "toggle", label: "Horizontal", default: true, group: "Bar Options" },
  ]);

  // ── Stacked Bar ──────────────────────────────────────────────────────
  _schemas.stackedbar = _chartSchema(CHART_BAR_PROPS, [
    { key: "config.stacked", type: "toggle", label: "Stacked", default: true, group: "Bar Options" },
  ]);

  // ── Stacked Area ─────────────────────────────────────────────────────
  _schemas.stackedarea = _chartSchema(CHART_LINE_PROPS, CHART_STACKED_AREA_PROPS, [
    { key: "config.showArea", type: "toggle", label: "Show Area Fill", default: true, group: "Area Options" },
    { key: "config.stacked", type: "toggle", label: "Stacked", default: true, group: "Area Options" },
  ]);

  // ── Percent Bar ──────────────────────────────────────────────────────
  _schemas.percentbar = _chartSchema(CHART_BAR_PROPS, CHART_PERCENT_BAR_PROPS);

  // ── Sparkline ────────────────────────────────────────────────────────
  _schemas.sparkline = _chartSchema(CHART_SPARKLINE_PROPS);

  // ── Heatmap ──────────────────────────────────────────────────────────
  _schemas.heatmap = _chartSchema(CHART_HEATMAP_PROPS);

  // ── Gauge ────────────────────────────────────────────────────────────
  _schemas.gauge = _chartSchema(CHART_GAUGE_PROPS);

  // ── Funnel ───────────────────────────────────────────────────────────
  _schemas.funnel = _chartSchema(CHART_FUNNEL_PROPS);

  // ── Treemap ──────────────────────────────────────────────────────────
  _schemas.treemap = _chartSchema(CHART_TREEMAP_PROPS);

  // ── Waterfall ────────────────────────────────────────────────────────
  _schemas.waterfall = _chartSchema(CHART_WATERFALL_PROPS);

  // ── Polar ────────────────────────────────────────────────────────────
  _schemas.polar = _chartSchema(CHART_PIE_PROPS, CHART_POLAR_PROPS);

  // ── Box Plot ─────────────────────────────────────────────────────────
  _schemas.boxplot = _chartSchema(CHART_BOX_PLOT_PROPS);

  // ── Histogram ────────────────────────────────────────────────────────
  _schemas.histogram = _chartSchema(CHART_HISTOGRAM_PROPS);

  // ── Bullet ───────────────────────────────────────────────────────────
  _schemas.bullet = _chartSchema(CHART_BULLET_PROPS);

  // ── Pyramid ──────────────────────────────────────────────────────────
  _schemas.pyramid = _chartSchema(CHART_PYRAMID_PROPS);

  // ── Sunburst ─────────────────────────────────────────────────────────
  _schemas.sunburst = _chartSchema(CHART_SUNBURST_PROPS);

  // ── Word Cloud ───────────────────────────────────────────────────────
  _schemas.wordcloud = _chartSchema(CHART_WORDCLOUD_PROPS, [
    { key: "config.wordcloudTextColumn", type: "text", label: "Text Column", default: "label", group: "Word Cloud Options" },
    { key: "config.wordcloudValueColumn", type: "text", label: "Value Column", default: "value", group: "Word Cloud Options" },
  ]);

  // ── Progress ─────────────────────────────────────────────────────────
  _schemas.progress = _merge(
    SQL_PROPS,
    [
      { key: "config.progressValue", type: "range", label: "Value", default: 50, min: 0, max: 100, step: 1, group: "Progress" },
      { key: "config.progressMax", type: "number", label: "Max", default: 100, min: 1, max: 100000, step: 1, group: "Progress" },
      { key: "config.progressColor", type: "color", label: "Bar Color", default: "#3b82f6", group: "Progress" },
      { key: "config.progressTrackColor", type: "color", label: "Track Color", default: "#e5e7eb", group: "Progress" },
      { key: "config.progressBorderRadius", type: "number", label: "Bar Radius", default: 6, min: 0, max: 20, step: 1, group: "Progress", suffix: "px" },
      { key: "config.progressHeight", type: "number", label: "Bar Height", default: 8, min: 2, max: 40, step: 1, group: "Progress", suffix: "px" },
      { key: "config.progressShowLabel", type: "toggle", label: "Show Label", default: true, group: "Progress" },
      { key: "config.progressLabelFormat", type: "select", label: "Label Format", default: "percent", options: [["percent", "Percentage"], ["value", "Value"], ["valueMax", "Value / Max"]], group: "Progress" },
    ],
    _typographySection("title", "Title"),
    _typographySection("value", "Value"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Slicer ───────────────────────────────────────────────────────────
  _schemas.slicer = _merge(
    SQL_PROPS,
    [
      { key: "config.slicerColumn", type: "text", label: "Filter Column", default: "", group: "Slicer", helpText: "Column to filter on" },
      { key: "config.slicerStyle", type: "select", label: "Style", default: "list", options: [["list", "List"], ["dropdown", "Dropdown"], ["checkbox", "Checkbox"], ["buttons", "Button Group"]], group: "Slicer" },
      { key: "config.slicerMultiSelect", type: "toggle", label: "Multi-Select", default: false, group: "Slicer" },
      { key: "config.slicerSearch", type: "toggle", label: "Show Search", default: false, group: "Slicer" },
      { key: "config.slicerSelectAll", type: "toggle", label: "Show Select All", default: false, group: "Slicer" },
      { key: "config.slicerCounts", type: "toggle", label: "Show Item Counts", default: false, group: "Slicer", helpText: "Query per-item row counts (needs a table)" },
      { key: "config.slicerDefault", type: "text", label: "Default Selection", default: "", group: "Slicer", helpText: "Comma-separated default values" },
      { key: "config.slicerCascade", type: "toggle", label: "Dependent on Other Filters", default: false, group: "Slicer", helpText: "Restrict this slicer's options to values that match the current dashboard filters (cascading slicers)" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Action Button ────────────────────────────────────────────────────
  _schemas.button = _merge(
    [
      { key: "config.action", type: "select", label: "Action", default: "reset", options: [["reset", "Clear all filters"], ["bookmark", "Apply bookmark"], ["page", "Go to page"], ["drill", "Drill through"], ["refresh", "Refresh data"]], group: "Behavior" },
      { key: "config.value", type: "text", label: "Value (bookmark id/name, page # or name)", default: "", group: "Behavior", helpText: "Required for bookmark / page / drill-through actions" },
    ],
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS,
    _typographySection("title", "Title"),
    INTERACTION_PROPS, COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Date Range ───────────────────────────────────────────────────────
  _schemas.daterange = _merge(
    [
      { key: "config.dateRangePresets", type: "text", label: "Quick Presets", default: "Today,Last 7 days,Last 30 days,This month,Last month,This quarter,This year", group: "Date Range", helpText: "Comma-separated preset labels" },
      { key: "config.dateRangeStart", type: "date", label: "Default Start", default: "", group: "Date Range" },
      { key: "config.dateRangeEnd", type: "date", label: "Default End", default: "", group: "Date Range" },
      { key: "config.dateRangeStyle", type: "select", label: "Style", default: "dropdown", options: [["dropdown", "Dropdown"], ["pills", "Pill Buttons"], ["calendar", "Calendar"]], group: "Date Range" },
      { key: "config.dateRangeLabel", type: "text", label: "Label", default: "Date Range", group: "Date Range" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Search Box ───────────────────────────────────────────────────────
  _schemas.searchbox = _merge(
    [
      { key: "config.searchPlaceholder", type: "text", label: "Placeholder", default: "Search...", group: "Search" },
      { key: "config.searchColumns", type: "text", label: "Search Columns", default: "", group: "Search", helpText: "Comma-separated (blank = all)" },
      { key: "config.searchDebounce", type: "number", label: "Debounce (ms)", default: 300, min: 50, max: 2000, step: 50, group: "Search" },
      { key: "config.searchMinLength", type: "number", label: "Min Characters", default: 1, min: 0, max: 10, step: 1, group: "Search" },
      { key: "config.searchIcon", type: "toggle", label: "Show Icon", default: true, group: "Search" },
      { key: "config.searchClearable", type: "toggle", label: "Clearable", default: true, group: "Search" },
    ],
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS,
    _typographySection("value", "Input"),
    INTERACTION_PROPS, COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Number Range ─────────────────────────────────────────────────────
  _schemas.numrange = _merge(
    SQL_PROPS,
    [
      { key: "config.numCol", type: "text", label: "Number Column", default: "", group: "Number Range", helpText: "Numeric column to bound (blank = auto-detect)" },
      { key: "config.numStep", type: "number", label: "Step", default: 1, min: 0.01, max: 1000000, step: 0.01, group: "Number Range" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Hierarchy (drill-down slicer) ────────────────────────────────────
  _schemas.hierarchy = _merge(
    SQL_PROPS,
    [
      { key: "config.hCols", type: "text", label: "Hierarchy Columns", default: "", group: "Hierarchy", placeholder: "Region,Country,City", helpText: "Comma-separated drill-down columns, deepest last" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Relative Date ────────────────────────────────────────────────────
  _schemas.relativedate = _merge(
    [
      { key: "config.dateColumn", type: "text", label: "Date Column", default: "", group: "Relative Date", helpText: "Date column to filter (blank = auto-detect)" },
      { key: "config.datePresets", type: "text", label: "Presets", default: "today,yesterday,last 7 days,last 14 days,last 30 days,last 90 days,this month,last month,ytd,last 12 months", group: "Relative Date", helpText: "Comma-separated preset keys (today, yesterday, last n days, this/last week/month/quarter/year, ytd, mtd, qtd, last 12 months)" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Top N ────────────────────────────────────────────────────────────
  _schemas.topn = _merge(
    SQL_PROPS,
    [
      { key: "config.topnColumn", type: "text", label: "Label Column", default: "", group: "Top N", helpText: "Column whose top/bottom values appear (blank = auto-detect)" },
      { key: "config.topnMeasure", type: "text", label: "Measure", default: "", group: "Top N", helpText: "Aggregate to rank by (e.g. SUM(\"Amount\")). Prefer SQL grouped by the label column." },
      { key: "config.topnN", type: "number", label: "N", default: 10, min: 1, max: 200, step: 1, group: "Top N" },
      { key: "config.topnMode", type: "select", label: "Which", default: "top", options: [["top", "Top N"], ["bottom", "Bottom N"]], group: "Top N" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Filter Pane (multi-field filter container) ───────────────────────
  _schemas.filterpane = _merge(
    SQL_PROPS,
    [
      { key: "config.paneFields", type: "text", label: "Fields", default: "", group: "Filter Pane", placeholder: "Region,Category,Status", helpText: "Comma-separated columns to show as small filter blocks" },
      { key: "config.paneTitle", type: "text", label: "Pane Title", default: "Filters", group: "Filter Pane" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Global Search ────────────────────────────────────────────────────
  _schemas.globalsearch = _merge(
    SQL_PROPS,
    [
      { key: "config.searchPlaceholder", type: "text", label: "Placeholder", default: "Search across all columns...", group: "Global Search" },
    ],
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS,
    _typographySection("value", "Input"),
    INTERACTION_PROPS, COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Image ────────────────────────────────────────────────────────────
  _schemas.image = _merge(
    [
      { key: "config.imageUrl", type: "text", label: "Image URL", default: "", group: "Image", placeholder: "https://..." },
      { key: "config.imageAlt", type: "text", label: "Alt Text", default: "", group: "Image" },
      { key: "config.imageFit", type: "select", label: "Fit", default: "cover", options: [["cover", "Cover"], ["contain", "Contain"], ["fill", "Fill"], ["none", "None"], ["scale-down", "Scale Down"]], group: "Image" },
      { key: "config.imagePosition", type: "select", label: "Position", default: "center", options: [["center", "Center"], ["top", "Top"], ["bottom", "Bottom"], ["left", "Left"], ["right", "Right"]], group: "Image" },
      { key: "config.imageBorderRadius", type: "number", label: "Border Radius", default: 0, min: 0, max: 50, step: 1, group: "Image", suffix: "px" },
      { key: "config.imageLink", type: "text", label: "Click URL", default: "", group: "Image", placeholder: "https://..." },
      { key: "config.imageOpenNewTab", type: "toggle", label: "Open in New Tab", default: true, group: "Image" },
    ],
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Custom HTML ──────────────────────────────────────────────────────
  _schemas.custom = _merge(
    [
      { key: "config.html", type: "textarea", label: "HTML Content", default: '<div style="padding:16px;color:#374151;">Custom content</div>', rows: 10, group: "Custom", helpText: "Raw HTML rendered in the widget" },
      { key: "config.sandbox", type: "toggle", label: "Sandboxed", default: false, group: "Custom", helpText: "Run HTML in a shadow DOM" },
    ],
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Page (section container) ─────────────────────────────────────────
  _schemas.page = _merge(
    [
      { key: "config.pageTitle", type: "text", label: "Page Title", default: "Page", group: "Page" },
      { key: "config.pageColumns", type: "number", label: "Columns", default: 12, min: 1, max: 24, step: 1, group: "Page" },
      { key: "config.pageGap", type: "number", label: "Gap", default: 12, min: 0, max: 40, step: 1, group: "Page", suffix: "px" },
      { key: "config.pagePadding", type: "number", label: "Padding", default: 16, min: 0, max: 80, step: 2, group: "Page", suffix: "px" },
    ],
    BACKGROUND_PROPS, BORDER_PROPS, SPACING_PROPS, EFFECTS_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Text ─────────────────────────────────────────────────────────────
  _schemas.text = _merge(
    [
      { key: "config.content", type: "textarea", label: "Text Content", default: "", rows: 6, group: "Text" },
      { key: "config.textFormat", type: "select", label: "Format", default: "plain", options: [["plain", "Plain Text"], ["markdown", "Markdown"], ["html", "HTML"]], group: "Text" },
    ],
    _typographySection("value", "Text"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Shape (PBI-style shape visual: rectangle / rounded / circle / polygon / arrow) ──
  _schemas.shape = _merge(
    [
      { key: "config.shapeShape", type: "select", label: "Shape", default: "rect", options: [
        ["rect", "Rectangle"], ["rounded", "Rounded Rectangle"], ["circle", "Circle"],
        ["triangle", "Triangle"], ["diamond", "Diamond"], ["pentagon", "Pentagon"],
        ["hexagon", "Hexagon"], ["arrow", "Arrow"], ["chevron", "Chevron"]
      ], group: "Shape" },
      { key: "config.shapeFill", type: "color", label: "Fill Color", default: "#6366f1", group: "Shape" },
      { key: "config.shapeFillOpacity", type: "number", label: "Fill Opacity", default: 100, min: 0, max: 100, step: 5, group: "Shape", suffix: "%" },
      { key: "config.shapeRadius", type: "number", label: "Corner Radius", default: 16, min: 0, max: 100, step: 1, group: "Shape", suffix: "px", helpText: "Rounded rectangle corner radius" },
      { key: "config.shapeBorderColor", type: "color", label: "Border Color", default: "", group: "Shape" },
      { key: "config.shapeBorderWidth", type: "number", label: "Border Width", default: 1, min: 0, max: 20, step: 1, group: "Shape", suffix: "px" },
      { key: "config.shapeRotate", type: "number", label: "Rotation", default: 0, min: -180, max: 360, step: 5, group: "Shape", suffix: "deg" },
    ],
    SPACING_PROPS, EFFECTS_PROPS, LAYOUT_PROPS
  );

  // ── Divider (thin horizontal/vertical line for report sectioning) ──
  _schemas.divider = _merge(
    [
      { key: "config.dividerOrientation", type: "select", label: "Orientation", default: "horizontal", options: [["horizontal", "Horizontal"], ["vertical", "Vertical"]], group: "Divider" },
      { key: "config.dividerStyle", type: "select", label: "Line Style", default: "solid", options: [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"]], group: "Divider" },
      { key: "config.dividerThickness", type: "number", label: "Thickness", default: 2, min: 1, max: 12, step: 1, group: "Divider", suffix: "px" },
      { key: "config.dividerColor", type: "color", label: "Color", default: "#cbd5e1", group: "Divider" },
      { key: "config.dividerInset", type: "number", label: "Inset", default: 12, min: 0, max: 60, step: 2, group: "Divider", suffix: "px" },
    ],
    SPACING_PROPS, EFFECTS_PROPS, LAYOUT_PROPS
  );

  // ── Metric Card (PBI cardVisual: N measures, each label + value + sparkline strip)
  _schemas.metriccard = _merge(
    (function () {
      var f = [];
      var os = [["none", "None (all-time)"], ["ytd", "YTD"], ["mtd", "MTD"], ["qtd", "QTD"], ["mom", "MoM"]];
      for (var i = 0; i < 4; i++) {
        var n = " " + (i + 1);
        f.push({ key: "config.measures[" + i + "].id", type: "measureSelect", label: "Measure" + n, default: "", group: "Metrics" });
        f.push({ key: "config.measures[" + i + "].timeIntel", type: "select", label: "Period" + n, default: "ytd", options: os, group: "Metrics" });
        f.push({ key: "config.measures[" + i + "].color", type: "color", label: "Color" + n, default: "", group: "Metrics" });
      }
      return f;
    })(),
    _typographySection("value", "Value"),
    TOOLTIP_PAGE_PROPS,
    SPACING_PROPS, EFFECTS_PROPS, LAYOUT_PROPS
  );

  // ── Table ────────────────────────────────────────────────────────────
  _schemas.table = _merge(
    SQL_PROPS,
    [
      { key: "config.labelColumn", type: "text", label: "Label Column", default: "", group: "Data" },
      { key: "config.valueColumn", type: "text", label: "Value Column", default: "", group: "Data" },
      { key: "config.pivot.rowCol", type: "text", label: "Pivot Row Field", default: "", group: "Pivot", helpText: "Group rows by this column (e.g. Region)" },
      { key: "config.pivot.colCol", type: "text", label: "Pivot Column Field", default: "", group: "Pivot", helpText: "Spread columns across this field (e.g. Product)" },
      { key: "config.pivot.valueCol", type: "text", label: "Pivot Value Field", default: "", group: "Pivot", helpText: "Numeric column to aggregate" },
      { key: "config.pivot.agg", type: "select", label: "Pivot Aggregate", default: "SUM", options: [["SUM", "Sum"], ["AVG", "Average"], ["COUNT", "Count"], ["MAX", "Max"], ["MIN", "Min"]], group: "Pivot" },
      { key: "config.drillPage", type: "text", label: "Drill Through Page", default: "", group: "Drill Through", helpText: "Page name or # — clicking a data point navigates there with the drill field as a filter" },
      { key: "config.drillField", type: "text", label: "Drill Field", default: "", group: "Drill Through", helpText: "Column passed as the drill-through filter (defaults to the pivot row field)" },
      { key: "config.tableColumns", type: "text", label: "Visible Columns", default: "", group: "Table", helpText: "Comma-separated (blank = all)" },
      { key: "config.tableSortable", type: "toggle", label: "Sortable", default: true, group: "Table" },
      { key: "config.tableSearchable", type: "toggle", label: "Searchable", default: false, group: "Table" },
      { key: "config.tablePagination", type: "toggle", label: "Pagination", default: true, group: "Table" },
      { key: "config.tablePageSize", type: "number", label: "Page Size", default: 10, min: 5, max: 100, step: 5, group: "Table" },
      { key: "config.tableStriped", type: "toggle", label: "Striped Rows", default: false, group: "Table" },
      { key: "config.tableBordered", type: "toggle", label: "Bordered", default: false, group: "Table" },
      { key: "config.tableCondensed", type: "toggle", label: "Condensed", default: false, group: "Table" },
      { key: "config.tableHeaderBg", type: "color", label: "Header Background", default: "#f8fafc", group: "Table" },
      { key: "config.tableHeaderColor", type: "color", label: "Header Text Color", default: "#374151", group: "Table" },
      { key: "config.tableRowHoverBg", type: "color", label: "Row Hover Color", default: "#f1f5f9", group: "Table" },
      { key: "config.tableAlternatingBg", type: "color", label: "Alternating Row BG", default: "#f9fafb", group: "Table" },
      { key: "config.tableBorderRadius", type: "number", label: "Table Border Radius", default: 8, min: 0, max: 20, step: 1, group: "Table", suffix: "px" },
    ],
    _typographySection("title", "Title"),
    _typographySection("value", "Cell"),
    TOOLTIP_PAGE_PROPS,
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Shape Map (choropleth) ──────────────────────────────────────────
  _schemas.shapemap = _merge(
    SQL_PROPS,
    [
      { key: "config.geoField", type: "text", label: "Region Field", default: "", group: "Data", helpText: "Column holding region / country / state names (blank = auto-detect)" },
      { key: "config.measureField", type: "text", label: "Measure Field", default: "", group: "Data", helpText: "Numeric column to aggregate (blank = auto-detect)" },
      { key: "config.shapeSet", type: "select", label: "Shape Set", default: "auto", options: [["auto", "Auto-detect"], ["world", "World countries"], ["us-states", "US states"], ["us-counties", "US counties"], ["uk-counties", "UK counties"], ["eu-nuts2", "EU NUTS-2 regions"], ["india-states", "India states"], ["brazil-states", "Brazil states"], ["china-provinces", "China provinces"], ["japan-prefectures", "Japan prefectures"]], group: "Map", helpText: "Boundary set used to match region names (bundled, keyless & offline)" },
      { key: "config.shapeMode", type: "select", label: "Color Scale", default: "linear", options: [["linear", "Linear (max)"], ["quantile", "Quantile buckets"], ["center", "Center-out (median)"]], group: "Map", helpText: "How values map to the fill color ramp" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Dot Distribution Map (one dot per record, jittered around centroid) ──
  _schemas.dotmap = _merge(
    SQL_PROPS,
    [
      { key: "config.geoField", type: "text", label: "Region Field", default: "", group: "Data", helpText: "Column holding region / country / state / city names (blank = auto-detect)" },
      { key: "config.pinColor", type: "color", label: "Dot Color", default: "", group: "Style", helpText: "Fill color for the dot clusters (blank = theme accent)" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Heatmap Map (intensity blend) ────────────────────────────────────
  _schemas.heatmapmap = _merge(
    SQL_PROPS,
    [
      { key: "config.geoField", type: "text", label: "Region Field", default: "", group: "Data", helpText: "Column holding region / country / state / city names (blank = auto-detect)" },
      { key: "config.measureField", type: "text", label: "Measure Field", default: "", group: "Data", helpText: "Numeric column to aggregate (blank = auto-detect)" },
      { key: "config.bubbleSize", type: "range", label: "Bubble Size", default: 0.5, min: 0.2, max: 1.5, step: 0.05, group: "Map", helpText: "Scale the radius of the heat blobs (0.2 = smallest, 1.5 = largest)" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── Flow Map (great-circle connections) ──────────────────────────────
  _schemas.flowmap = _merge(
    SQL_PROPS,
    [
      { key: "config.originField", type: "text", label: "Origin Field", default: "", group: "Data", helpText: "Column holding the origin (from) location (blank = auto-detect)" },
      { key: "config.destField", type: "text", label: "Destination Field", default: "", group: "Data", helpText: "Column holding the destination (to) location (blank = auto-detect)" },
      { key: "config.measureField", type: "text", label: "Measure Field", default: "", group: "Data", helpText: "Numeric column to weight the arcs (blank = auto-detect)" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  // ── GL Map (mapcn.dev-style MapLibre GL map) ─────────────────────────
  _schemas.glmap = _merge(
    SQL_PROPS,
    [
      { key: "config.geoField", type: "text", label: "Region Field", default: "", group: "Data", helpText: "Column holding region / country / state / city names (blank = auto-detect)" },
      { key: "config.measureField", type: "text", label: "Measure Field", default: "", group: "Data", helpText: "Numeric column to aggregate (blank = auto-detect)" },
      { key: "config.glBasemap", type: "select", label: "Basemap Style", default: "default", options: [["default", "Default (CARTO)"], ["positron", "CARTO Light (Positron)"], ["darkmatter", "CARTO Dark (Dark Matter)"], ["osm", "OpenFreeMap Bright"], ["blank", "Blank canvas (no tiles)"]], group: "Map", helpText: "Basemap used by the MapLibre GL map (all keyless)" },
      { key: "config.glPitch", type: "range", label: "3D Pitch", default: 0, min: 0, max: 60, step: 5, group: "Map", helpText: "Tilt the map for a 3D perspective (0 = top-down)" },
      { key: "config.glShowPopups", type: "toggle", label: "Show Popups", default: true, group: "Map", helpText: "Show a popup card when clicking a marker" },
      { key: "config.bubbleSize", type: "range", label: "Marker Size", default: 0.5, min: 0.2, max: 1.5, step: 0.05, group: "Map", helpText: "Scale the radius of the value markers (0.2 = smallest, 1.5 = largest)" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  /* ═══════════════════════════════════════════════════════════════════════
     ADDITIONAL CATALOGUE TYPES
     These widget types exist on the canvas (dashboard catalogue) but used to
     fall back to the minimal layout schema. Map them onto the closest visual
     schema so the properties panel exposes their Data/Chart/Style tabs.
     ═══════════════════════════════════════════════════════════════════════ */

  /* Step / smooth line + smooth area — line-based */
  _schemas.stepline   = _chartSchema(CHART_LINE_PROPS);
  _schemas.smoothline = _chartSchema(CHART_LINE_PROPS);
  _schemas.splinearea = _chartSchema(CHART_LINE_PROPS, [
    { key: "config.showArea", type: "toggle", label: "Show Area Fill", default: true, group: "Area Options" },
  ]);

  /* Lollipop / Pareto / Diverging / Radial Bar — bar-based */
  _schemas.lollipop   = _chartSchema(CHART_BAR_PROPS);
  _schemas.pareto     = _chartSchema(CHART_BAR_PROPS);
  _schemas.diverging  = _chartSchema(CHART_BAR_PROPS);
  _schemas.radialbar  = _chartSchema(CHART_BAR_PROPS);

  /* Rose — pie-based */
  _schemas.rose = _chartSchema(CHART_PIE_PROPS);

  /* Water tank — gauge/needle family */
  _schemas.watertank = _chartSchema(CHART_GAUGE_PROPS);

  /* Waffle / Calendar / Bubble grid / Leaderboard / Gantt — full chart schema */
  _schemas.waffle     = _chartSchema([], []);
  _schemas.calendar   = _chartSchema([], []);
  _schemas.bubblegrid = _chartSchema([], []);
  _schemas.ranking    = _chartSchema([], []);
  _schemas.gantt      = _chartSchema([], []);

  /* Flow / relationship visuals — source→target schema (sankey, chord) */
  _schemas.sankey = _merge(
    SQL_PROPS,
    [
      { key: "config.sourceField", type: "text", label: "Source Field", default: "", group: "Data", helpText: "Column holding the source / from category (blank = auto-detect)" },
      { key: "config.targetField", type: "text", label: "Target Field", default: "", group: "Data", helpText: "Column holding the target / to category (blank = auto-detect)" },
      { key: "config.measureField", type: "text", label: "Measure Field", default: "", group: "Data", helpText: "Numeric column to weight the flows (blank = auto-detect)" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );
  _schemas.chord = _schemas.sankey;

  /* Distribution visuals — raw value rows per category (ridgeline, beeswarm) */
  _schemas.ridgeline = _merge(
    SQL_PROPS,
    [
      { key: "config.categoryField", type: "text", label: "Category Field", default: "", group: "Data", helpText: "Column holding the category grouping (blank = auto-detect)" },
      { key: "config.measureField", type: "text", label: "Measure Field", default: "", group: "Data", helpText: "Numeric column holding the distribution values (blank = auto-detect)" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );
  _schemas.beeswarm = _schemas.ridgeline;

  /* Slope / Stream — trend-family */
  _schemas.slope  = _chartSchema(CHART_LINE_PROPS);
  _schemas.stream = _chartSchema(CHART_LINE_PROPS, [
    { key: "config.showArea", type: "toggle", label: "Show Area Fill", default: true, group: "Area Options" },
  ]);

  /* Venn — set relationships */
  _schemas.venn = _merge(
    SQL_PROPS,
    [
      { key: "config.vennA", type: "text", label: "Set A Name", default: "A", group: "Data", helpText: "Label for the first set" },
      { key: "config.vennB", type: "text", label: "Set B Name", default: "B", group: "Data", helpText: "Label for the second set" },
    ],
    _typographySection("title", "Title"),
    BORDER_PROPS, BACKGROUND_PROPS, SPACING_PROPS, EFFECTS_PROPS, INTERACTION_PROPS,
    COLOR_SYSTEM_PROPS, RESPONSIVE_PROPS, LAYOUT_PROPS
  );

  /* Map visuals — share the shape-map schema */
  _schemas.geomap      = _schemas.shapemap;
  _schemas.countrymap  = _schemas.shapemap;

  /* KPI-family cards (rendered by the KPI engine) — full KPI schema */
  ["radial", "status", "comparison", "targetactual", "trendkpi", "percentagekpi",
   "prevkpi", "iconkpi", "multikpi", "sparkkpi", "sparkbarkpi", "minilinekpi",
   "sparkdeltakpi", "donutkpi", "bullet"].forEach(function (t) {
    if (!_schemas[t]) _schemas[t] = _schemas.kpi;
  });

  /* Population pyramid — bar-based layout */
  _schemas.population = _chartSchema(CHART_BAR_PROPS);

  /* ═══════════════════════════════════════════════════════════════════════
     HELPERS
     ═══════════════════════════════════════════════════════════════════════ */

  var _minimalLayoutSchema = _merge(LAYOUT_PROPS, STYLE_PROPS, INTERACTION_PROPS, RESPONSIVE_PROPS, TOOLTIP_PAGE_PROPS);

  /**
   * Returns the schema array for a given widget type.
   * Falls back to a minimal layout schema for unknown types.
   */
  function getSchema(type) {
    return _schemas[type] || _minimalLayoutSchema;
  }

  /* Expand "config.measures[0].id" into ["config", "measures", 0, "id"] */
  function _pathSegs(dotPath) {
    var parts = String(dotPath).split(".");
    var segs = [];
    parts.forEach(function (p) {
      var m = p.match(/^([^[\]]+)\[(\d+)\]$/);
      if (m) { segs.push(m[1]); segs.push(Number(m[2])); }
      else segs.push(p);
    });
    return segs;
  }

  /**
   * Read a value from an object tree using a dot-separated path.
   * e.g. resolve("config.sql", widget) → widget.config.sql
   */
  function resolve(dotPath, obj) {
    if (!obj || !dotPath) return undefined;
    /* a leading `widget.` segment addresses the widget object which is the
       root already passed in (the props panel works against the raw widget),
       so strip it — e.g. "widget.x" -> "x" */
    dotPath = String(dotPath).replace(/^widget\./, "");
    var cur = obj;
    var segs = _pathSegs(dotPath);
    for (var i = 0; i < segs.length; i++) {
      if (cur == null || typeof cur !== "object") return undefined;
      cur = cur[segs[i]];
    }
    return cur;
  }

  /**
   * Write a value into an object tree using a dot-separated path.
   * Creates intermediate objects/arrays as needed (bracket indices supported).
   * e.g. setValue("config.measures[0].id", widget, "m1")
   */
  function setValue(dotPath, obj, value) {
    if (!obj || !dotPath) return;
    dotPath = String(dotPath).replace(/^widget\./, "");
    var segs = _pathSegs(dotPath);
    var cur = obj;
    for (var i = 0; i < segs.length - 1; i++) {
      var key = segs[i];
      if (cur[key] == null || typeof cur[key] !== "object") {
        cur[key] = typeof segs[i + 1] === "number" ? [] : {};
      }
      cur = cur[key];
    }
    cur[segs[segs.length - 1]] = value;
  }

  /**
   * Returns all properties for a type as a flat array.
   */
  function allProps(type) {
    return getSchema(type).slice();
  }

  /**
   * Returns default values object for a given type.
   */
  function getDefaults(type) {
    var schema = getSchema(type);
    var defaults = {};
    for (var i = 0; i < schema.length; i++) {
      var p = schema[i];
      if (p.default !== undefined) {
        defaults[p.key] = p.default;
      }
    }
    return defaults;
  }

  /**
   * Internal typography factory exposed for external use.
   */
  function typographySection(prefix, label) {
    return _typographySection(prefix, label);
  }

  /**
   * Filter properties by group name.
   */
  function byGroup(type, group) {
    var schema = getSchema(type);
    var result = [];
    for (var i = 0; i < schema.length; i++) {
      if (schema[i].group === group) result.push(schema[i]);
    }
    return result;
  }

  /**
   * Filter properties by those visible for a given widget config
   * (resolves `condition` fields against the widget config).
   */
  function visibleProps(type, config) {
    var schema = getSchema(type);
    var result = [];
    for (var i = 0; i < schema.length; i++) {
      var p = schema[i];
      if (!p.condition) { result.push(p); continue; }
      var cond = p.condition;
      var val = resolve(cond.key, config);
      if ("equals" in cond && val === cond.equals) { result.push(p); continue; }
      if ("in" in cond && cond.in.indexOf(val) !== -1) { result.push(p); continue; }
    }
    return result;
  }

  /**
   * Get unique group labels for a type, in schema order.
   */
  function groups(type) {
    var schema = getSchema(type);
    var seen = {};
    var result = [];
    for (var i = 0; i < schema.length; i++) {
      var g = schema[i].group;
      if (g && !seen[g]) { seen[g] = true; result.push(g); }
    }
    return result;
  }

  /**
   * Look up a single property definition by key.
   */
  function findProp(type, key) {
    var schema = getSchema(type);
    for (var i = 0; i < schema.length; i++) {
      if (schema[i].key === key) return schema[i];
    }
    return null;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     PUBLIC API
     ═══════════════════════════════════════════════════════════════════════ */

  window.WidgetPropertySchemas = {
    // Core helpers
    getSchema: getSchema,
    resolve: resolve,
    setValue: setValue,
    allProps: allProps,
    getDefaults: getDefaults,
    byGroup: byGroup,
    visibleProps: visibleProps,
    groups: groups,
    findProp: findProp,
    typographySection: typographySection,

    // Shared fragments (for building custom schemas)
    fragments: {
      LAYOUT: LAYOUT_PROPS,
      STYLE: STYLE_PROPS,
      SQL: SQL_PROPS,
      CHART_STYLE: CHART_STYLE_PROPS,
      BORDER: BORDER_PROPS,
      BACKGROUND: BACKGROUND_PROPS,
      SPACING: SPACING_PROPS,
      EFFECTS: EFFECTS_PROPS,
      INTERACTION: INTERACTION_PROPS,
      COLOR_SYSTEM: COLOR_SYSTEM_PROPS,
      RESPONSIVE: RESPONSIVE_PROPS,
      CHART_DATA: CHART_DATA_PROPS,
      CHART_VISUAL: CHART_VISUAL_PROPS,
    },

    // Raw schemas (read-only reference)
    schemas: _schemas,

    // Available widget types
    types: Object.keys(_schemas),
  };

})();
