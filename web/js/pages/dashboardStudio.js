/**
 * Dashboard Studio — Central hub for creating & managing dashboards.
 * Provides data selection, template gallery, history, and quick-start actions.
 */
(function () {
  "use strict";

  const PAGE = "#dashboardstudio";
  var _tables = [];
  var _history = [];

  // ── Option sets for the AI Auto-Build design wizard (dashboard_interactive.md Q1–Q7) ──
  // These power the clarifying questions (purpose / audience / KPIs / style /
  // sections / access) and the generated Design Brief. ids are stable so they
  // can be persisted in localStorage and reused across sessions.
  const _DESIGN_META = {
    purposes: [
      { id: "executive", label: "Executive overview", desc: "Top-level snapshot of revenue, profit & health", icon: "fa-briefcase" },
      { id: "ops", label: "Operational monitoring", desc: "Daily KPIs, exceptions & thresholds", icon: "fa-gauge-high" },
      { id: "analytical", label: "Analytical deep-dive", desc: "Explore, slice and compare across dimensions", icon: "fa-magnifying-glass-chart" },
      { id: "demo", label: "Demo / showcase", desc: "Polished showcase of the dataset", icon: "fa-chess-board" }
    ],
    audiences: [
      { id: "executives", label: "Executives", desc: "High-level, big trends, few filters", icon: "fa-user-tie" },
      { id: "analysts", label: "Analysts", desc: "Filters, drill-down & detail tables", icon: "fa-chart-simple" },
      { id: "ops", label: "Ops / engineers", desc: "Alerts, exceptions, real-time-ish views", icon: "fa-screwdriver-wrench" },
      { id: "mixed", label: "Mixed", desc: "Summary cards with filtering & detail", icon: "fa-people-group" }
    ],
    kpis: [
      { id: "revenue", label: "Revenue / sales trend", desc: "Monthly trend & growth vs prior period", icon: "fa-chart-line" },
      { id: "profit", label: "Profit & margin by region/category", desc: "Where money is made / lost", icon: "fa-coins" },
      { id: "customers", label: "Customer / segment breakdown", desc: "Counts, segments, churn, AOV", icon: "fa-users" },
      { id: "exceptions", label: "Operational exceptions", desc: "Low stock, late shipments, ticket backlog", icon: "fa-triangle-exclamation" }
    ],
    styles: [
      { id: "light", label: "Light", desc: "Corporate · Power BI look", icon: "fa-sun" },
      { id: "dark", label: "Dark", desc: "High-contrast dark", icon: "fa-moon" },
      { id: "midnight", label: "Midnight", desc: "Deep navy", icon: "fa-star" },
      { id: "ocean", label: "Ocean", desc: "Cool teal-blue", icon: "fa-water" },
      { id: "slate", label: "Slate", desc: "Minimal neutral", icon: "fa-layer-group" },
      { id: "sunset", label: "Sunset", desc: "Warm pink-orange", icon: "fa-cloud-sun" },
      { id: "forest", label: "Forest", desc: "Deep greens", icon: "fa-tree" },
      { id: "grape", label: "Grape", desc: "Rich purple", icon: "fa-wine-bottle" },
      { id: "candy", label: "Candy", desc: "Soft pastel pink", icon: "fa-candy-cane" },
      { id: "terminal", label: "Terminal", desc: "Green on black", icon: "fa-terminal" },
      { id: "aurora", label: "Aurora", desc: "Indigo & emerald", icon: "fa-wand-sparkles" },
      { id: "coffee", label: "Coffee", desc: "Warm browns", icon: "fa-mug-hot" },
      { id: "rose", label: "Rose", desc: "Crimson & gold", icon: "fa-rose" },
      { id: "neon", label: "Neon", desc: "Electric glow", icon: "fa-bolt" },
      { id: "emerald", label: "Emerald", desc: "Jewel green", icon: "fa-gem" },
      { id: "gold", label: "Gold", desc: "Amber luxury", icon: "fa-crown" },
      { id: "ice", label: "Ice", desc: "Light frost blue", icon: "fa-snowflake" },
      { id: "graphite", label: "Graphite", desc: "Cool grey", icon: "fa-pen-nib" },
      { id: "lavender", label: "Lavender", desc: "Soft purple light", icon: "fa-spa" },
      { id: "peach", label: "Peach", desc: "Warm light orange", icon: "fa-apple-whole" },
      { id: "cyber", label: "Cyber", desc: "Cyan & violet", icon: "fa-microchip" },
      { id: "paper", label: "Paper", desc: "Clean minimal light", icon: "fa-file-lines" },
      { id: "mint", label: "Mint", desc: "Fresh light green", icon: "fa-leaf" }
    ],
    sections: [
      { id: "kpi", label: "KPI cards", icon: "fa-chart-simple" },
      { id: "filters", label: "Filters / slicers", icon: "fa-sliders" },
      { id: "trends", label: "Trend charts", icon: "fa-chart-line" },
      { id: "breakdown", label: "Breakdown charts", icon: "fa-chart-pie" },
      { id: "table", label: "Data table", icon: "fa-table" },
      { id: "insights", label: "Executive insights", icon: "fa-robot" },
      { id: "brief", label: "Design brief", icon: "fa-file-lines" }
    ],
    pageFocuses: [
      { id: "breakdown", label: "Breakdowns & shares", desc: "Category splits, donuts, treemaps", icon: "fa-chart-pie" },
      { id: "comparison", label: "Comparisons", desc: "Bars, funnels, top-N rankings", icon: "fa-chart-column" },
      { id: "distribution", label: "Distributions", desc: "Histograms, box-plots", icon: "fa-chart-bar" },
      { id: "correlation", label: "Correlations", desc: "Scatter plots & heatmaps", icon: "fa-arrow-right-arrow-left" },
      { id: "special", label: "Analysis", desc: "Waterfalls & special charts", icon: "fa-wand-magic-sparkles" },
      { id: "geo", label: "Geography", desc: "Interactive map", icon: "fa-map-location-dot" },
      { id: "data", label: "Data table", desc: "Full raw data preview", icon: "fa-table" }
    ],
    // Full widget catalogue (mirrors the blank-dashboard palette). Used by the
    // final wizard step so users can multi-select exactly which widget types the
    // AI should place on the dashboard. When the blank-dashboard page has already
    // loaded its richer catalogue (window.DashVisCatalogue) that one wins.
    widgets: [
      { type: "kpi",          label: "KPI Card",         icon: "fa-chart-simple",           color: "#7B61FF", cat: "Basics" },
      { type: "table",        label: "Data Table",       icon: "fa-table",                  color: "#64748B", cat: "Basics" },
      { type: "text",         label: "Text Box",         icon: "fa-font",                   color: "#4F46E5", cat: "Basics" },
      { type: "trendkpi",     label: "Trend KPI",        icon: "fa-arrow-trend-up",         color: "#22C55E", cat: "Basics" },
      { type: "percentagekpi",label: "% Change KPI",     icon: "fa-percent",                color: "#06B6D4", cat: "Basics" },
      { type: "prevkpi",      label: "Prev Period KPI",  icon: "fa-clock-rotate-left",      color: "#F59E0B", cat: "Basics" },
      { type: "iconkpi",      label: "Icon KPI",         icon: "fa-star",                   color: "#EAB308", cat: "Basics" },
      { type: "multikpi",     label: "Multi-Value KPI",  icon: "fa-cubes",                  color: "#8B5CF6", cat: "Basics" },
      { type: "metriccard",   label: "Metric Card",      icon: "fa-th-large",               color: "#7C3AED", cat: "Basics" },
      { type: "sparkkpi",     label: "Sparkline KPI",    icon: "fa-wave-square",            color: "#0D9488", cat: "Basics" },
      { type: "sparkbarkpi",  label: "Mini Bar KPI",     icon: "fa-signal",                 color: "#10B981", cat: "Basics" },
      { type: "minilinekpi",  label: "Mini Line KPI",    icon: "fa-chart-line",             color: "#3B82F6", cat: "Basics" },
      { type: "sparkdeltakpi", label: "Sparkline Delta KPI", icon: "fa-wave-square",       color: "#0D9488", cat: "Basics" },
      { type: "donutkpi",      label: "Donut KPI",        icon: "fa-circle-notch",           color: "#8B5CF6", cat: "Basics" },
      { type: "radial",       label: "Radial Progress",  icon: "fa-circle-dot",             color: "#10B981", cat: "Basics" },
      { type: "status",       label: "Status Indicator", icon: "fa-circle-check",           color: "#2563EB", cat: "Basics" },
      { type: "comparison",   label: "Comparison KPI",   icon: "fa-scale-balanced",         color: "#7C3AED", cat: "Basics" },
      { type: "targetactual", label: "Target vs Actual", icon: "fa-crosshairs",             color: "#F59E0B", cat: "Basics" },
      { type: "line",         label: "Line Chart",       icon: "fa-chart-line",             color: "#3B82F6", cat: "Standard Charts" },
      { type: "bar",          label: "Bar Chart",        icon: "fa-chart-bar",              color: "#10B981", cat: "Standard Charts" },
      { type: "column",       label: "Column Chart",     icon: "fa-chart-column",           color: "#F59E0B", cat: "Standard Charts" },
      { type: "area",         label: "Area Chart",       icon: "fa-chart-area",             color: "#14B8A6", cat: "Standard Charts" },
      { type: "pie",          label: "Pie Chart",        icon: "fa-chart-pie",              color: "#EC4899", cat: "Standard Charts" },
      { type: "doughnut",     label: "Doughnut",         icon: "fa-circle-notch",           color: "#8B5CF6", cat: "Standard Charts" },
      { type: "stepline",     label: "Step Line Chart",  icon: "fa-step-forward",           color: "#3B82F6", cat: "Standard Charts" },
      { type: "smoothline",   label: "Smooth Line",      icon: "fa-wave-sine",              color: "#0EA5E9", cat: "Standard Charts" },
      { type: "splinearea",   label: "Smooth Area",      icon: "fa-mountain-sun",           color: "#14B8A6", cat: "Standard Charts" },
      { type: "scatter",      label: "Scatter Plot",     icon: "fa-braille",                color: "#06B6D4", cat: "Advanced Charts" },
      { type: "bubble",       label: "Bubble Chart",     icon: "fa-circle",                 color: "#0EA5E9", cat: "Advanced Charts" },
      { type: "radar",        label: "Radar Chart",      icon: "fa-spider",                 color: "#6366F1", cat: "Advanced Charts" },
      { type: "combo",        label: "Combo Chart",      icon: "fa-chart-line fa-chart-bar",color: "#7C3AED", cat: "Advanced Charts" },
      { type: "horizontalbar",label: "Horizontal Bar",   icon: "fa-bars",                   color: "#059669", cat: "Advanced Charts" },
      { type: "stackedbar",   label: "Stacked Bar",      icon: "fa-layer-group",            color: "#DC2626", cat: "Advanced Charts" },
      { type: "stackedarea",  label: "Stacked Area",     icon: "fa-layer-group",            color: "#0891B2", cat: "Advanced Charts" },
      { type: "percentbar",   label: "100% Stacked Bar", icon: "fa-percent",                color: "#9333EA", cat: "Advanced Charts" },
      { type: "sparkline",    label: "Sparkline",        icon: "fa-wave-square",            color: "#0D9488", cat: "Advanced Charts" },
      { type: "lollipop",     label: "Lollipop Chart",   icon: "fa-candy-cane",             color: "#DB2777", cat: "Advanced Charts" },
      { type: "stream",       label: "Stream Graph",     icon: "fa-wind",                   color: "#06B6D4", cat: "Advanced Charts" },
      { type: "heatmap",      label: "Heatmap",          icon: "fa-border-all",             color: "#D946EF", cat: "Data Viz" },
      { type: "gauge",        label: "Gauge",            icon: "fa-gauge-high",             color: "#F97316", cat: "Data Viz" },
      { type: "funnel",       label: "Funnel",           icon: "fa-filter",                 color: "#EF4444", cat: "Data Viz" },
      { type: "treemap",      label: "Treemap",          icon: "fa-table-cells-large",      color: "#0891B2", cat: "Data Viz" },
      { type: "waterfall",    label: "Waterfall",        icon: "fa-water",                  color: "#2563EB", cat: "Data Viz" },
      { type: "polar",        label: "Polar Area",       icon: "fa-compass",                color: "#EA580C", cat: "Data Viz" },
      { type: "waffle",       label: "Waffle Chart",     icon: "fa-table-cells",            color: "#0D9488", cat: "Data Viz" },
      { type: "calendar",     label: "Calendar Heatmap", icon: "fa-calendar",               color: "#14B8A6", cat: "Data Viz" },
      { type: "bubblegrid",   label: "Bubble Grid",      icon: "fa-atom",                   color: "#16A34A", cat: "Data Viz" },
      { type: "watertank",    label: "Water Tank",       icon: "fa-glass-water",            color: "#0EA5E9", cat: "Data Viz" },
      { type: "venn",         label: "Venn Diagram",     icon: "fa-circle-nodes",           color: "#8B5CF6", cat: "Data Viz" },
      { type: "boxplot",      label: "Box Plot",         icon: "fa-square",                 color: "#475569", cat: "Statistical" },
      { type: "histogram",    label: "Histogram",        icon: "fa-chart-bar",              color: "#6366F1", cat: "Statistical" },
      { type: "bullet",       label: "Bullet Chart",     icon: "fa-bullseye",               color: "#B91C1C", cat: "Statistical" },
      { type: "pareto",       label: "Pareto Chart",     icon: "fa-bars-progress",          color: "#F97316", cat: "Statistical" },
      { type: "diverging",    label: "Diverging Bar",    icon: "fa-bars-staggered",         color: "#EF4444", cat: "Statistical" },
      { type: "population",   label: "Population Pyramid",icon: "fa-people-roof",           color: "#F59E0B", cat: "Statistical" },
      { type: "pyramid",      label: "Pyramid",          icon: "fa-caret-down",             color: "#D97706", cat: "Specialized" },
      { type: "sunburst",     label: "Sunburst",         icon: "fa-sun",                    color: "#E11D48", cat: "Specialized" },
      { type: "wordcloud",    label: "Word Cloud",       icon: "fa-cloud",                  color: "#7C3AED", cat: "Specialized" },
      { type: "progress",     label: "Progress Ring",    icon: "fa-circle-half-stroke",     color: "#10B981", cat: "Specialized" },
      { type: "rose",         label: "Rose Chart",       icon: "fa-asterisk",               color: "#EC4899", cat: "Specialized" },
      { type: "radialbar",    label: "Radial Bar Chart", icon: "fa-record-vinyl",           color: "#8B5CF6", cat: "Specialized" },
      { type: "ranking",      label: "Leaderboard",      icon: "fa-ranking-star",           color: "#EAB308", cat: "Specialized" },
      { type: "gantt",        label: "Gantt Timeline",   icon: "fa-timeline",               color: "#6366F1", cat: "Specialized" },
      { type: "slope",        label: "Slope Chart",      icon: "fa-arrow-trend-up",         color: "#228BE6", cat: "Specialized" },
      { type: "sankey",       label: "Sankey Diagram",   icon: "fa-code-branch",            color: "#14B8A6", cat: "Specialized" },
      { type: "slicer",       label: "Dropdown Slicer",  icon: "fa-list-check",             color: "#2563EB", cat: "Filters" },
      { type: "daterange",    label: "Date Range",       icon: "fa-calendar-days",          color: "#7C3AED", cat: "Filters" },
      { type: "searchbox",    label: "Search Filter",    icon: "fa-magnifying-glass",       color: "#0D9488", cat: "Filters" },
      { type: "numrange",     label: "Number Range",     icon: "fa-sliders",                color: "#06B6D4", cat: "Filters" },
      { type: "hierarchy",    label: "Hierarchy Slicer", icon: "fa-sitemap",                color: "#F59E0B", cat: "Filters" },
      { type: "relativedate", label: "Relative Date",    icon: "fa-clock",                  color: "#10B981", cat: "Filters" },
      { type: "topn",         label: "Top N Filter",     icon: "fa-ranking-star",           color: "#DC2626", cat: "Filters" },
      { type: "filterpane",   label: "Filter Pane",      icon: "fa-filter",                 color: "#2563EB", cat: "Filters" },
      { type: "globalsearch", label: "Global Search",    icon: "fa-magnifying-glass",       color: "#0D9488", cat: "Filters" },
      { type: "image",        label: "Image",            icon: "fa-image",                  color: "#16A34A", cat: "Media" },
      { type: "shape",        label: "Shape",            icon: "fa-shapes",                 color: "#6366F1", cat: "Media" },
      { type: "divider",      label: "Divider",          icon: "fa-minus",                  color: "#94A3B8", cat: "Media" },
      { type: "custom",       label: "Custom HTML",      icon: "fa-code",                   color: "#0F766E", cat: "Media" },
      { type: "page",         label: "Page Link",        icon: "fa-arrow-right-to-bracket", color: "#0EA5E9", cat: "Navigation" },
      { type: "button",       label: "Action Button",    icon: "fa-circle-play",            color: "#7C3AED", cat: "Navigation" },
      { type: "geomap",       label: "Marker Map",       icon: "fa-location-dot",           color: "#2563EB", cat: "Maps" },
      { type: "countrymap",   label: "Bubble Sizing Map",icon: "fa-earth-americas",         color: "#7C3AED", cat: "Maps" },
      { type: "shapemap",     label: "Shape Map",        icon: "fa-draw-polygon",           color: "#0EA5A4", cat: "Maps" },
      { type: "dotmap",       label: "Dot Distribution Map",icon: "fa-circle-dot",          color: "#F59E0B", cat: "Maps" },
      { type: "heatmapmap",   label: "Heatmap Map",      icon: "fa-temperature-high",       color: "#EF4444", cat: "Maps" },
      { type: "flowmap",      label: "Flow Map",         icon: "fa-right-left",             color: "#10B981", cat: "Maps" }
    ]
  };

  const StudioPage = {
    async init() {
      _tables = [];
      _history = _loadHistory();
      window._studioDashCount = undefined;
    },

    async destroy() { /* noop */ },

    async render() {
      var pc = document.getElementById("page-content");
      if (!pc) return;

      pc.innerHTML =
        '<div class="animate-fade-in studio-page">' +

        // ── Hero Banner ──
        '<div class="stud-hero">' +
          '<div class="stud-hero-glow"></div>' +
          '<div class="stud-hero-left">' +
            '<div class="stud-hero-kicker"><i class="fas fa-object-group"></i> Dashboard Studio</div>' +
            '<h1 class="stud-hero-title">Build beautiful,<br><span>Power BI&nbsp;-&nbsp;style</span> dashboards</h1>' +
            '<p class="stud-hero-sub">Describe your data and let AI instantiate a professional executive dashboard — KPI cards, slicers, charts and a narrative executive panel.</p>' +
            '<div class="stud-hero-actions">' +
              '<button class="btn btn-primary stud-hero-cta" onclick="StudioPage._aiBuild()"><i class="fas fa-robot"></i> AI Auto-Build</button>' +
              '<button class="btn stud-hero-cta-ghost" data-feature="recreate_from_image" onclick="StudioPage._recreateFromImage()"><i class="fas fa-image"></i> Recreate from Image</button>' +
              '<button class="btn stud-hero-cta-ghost" onclick="StudioPage._createBlank()"><i class="fas fa-plus"></i> Start Blank</button>' +
            '</div>' +
            '<div class="stud-hero-stats">' +
              '<div><strong id="st-kpi-tables">—</strong><span>Data Sources</span></div>' +
              '<div><strong id="st-kpi-templates">—</strong><span>Templates</span></div>' +
              '<div><strong id="st-kpi-history">—</strong><span>Recent</span></div>' +
            '</div>' +
          '</div>' +
          '<div class="stud-hero-right" aria-hidden="true">' +
            '<div class="stud-hero-shards">' +
              '<div class="shard shard-1"><i class="fas fa-chart-line"></i><b>Revenue</b><span>▲ 12.4%</span></div>' +
              '<div class="shard shard-2"><i class="fas fa-chart-pie"></i><b>Share</b><span>38%</span></div>' +
              '<div class="shard shard-3"><i class="fas fa-robot"></i><b>AI Insight</b><span>Auto</span></div>' +
            '</div>' +
          '</div>' +
        '</div>' +

        // ── Quick Start ──
        '<div class="stud-quick">' +
          '<div class="stud-quick-head">' +
            '<h2 class="section-title"><i class="fas fa-bolt mr-2"></i>Quick Start</h2>' +
            '<span class="stud-quick-note">Pick a start point</span>' +
          '</div>' +
          '<div class="stud-quick-grid">' +
            '<div class="stud-quick-card stud-quick-featured card-accent-blue" onclick="StudioPage._aiBuild()" style="cursor:pointer">' +
              '<div class="stud-quick-ico"><i class="fas fa-robot"></i></div>' +
              '<div class="stud-quick-body"><div class="stud-quick-title">AI Auto-Build</div><div class="stud-quick-desc">Describe what you want — AI lays out KPIs, slicers, charts and an executive insight panel.</div></div>' +
              '<div class="stud-quick-arrow"><i class="fas fa-arrow-right"></i></div>' +
            '</div>' +
            '<div class="stud-quick-card" data-feature="recreate_from_image" onclick="StudioPage._recreateFromImage()" style="cursor:pointer">' +
              '<div class="stud-quick-ico" style="background:#ede9fe;color:#7B61FF"><i class="fas fa-image"></i></div>' +
              '<div class="stud-quick-body"><div class="stud-quick-title">Recreate from Image</div><div class="stud-quick-desc">Upload a dashboard screenshot and rebuild it on your data.</div></div>' +
              '<div class="stud-quick-arrow"><i class="fas fa-arrow-right"></i></div>' +
            '</div>' +
            '<div class="stud-quick-card" onclick="StudioPage._aiInsights()" style="cursor:pointer">' +
              '<div class="stud-quick-ico" style="background:#fef3c7;color:#d97706"><i class="fas fa-lightbulb"></i></div>' +
              '<div class="stud-quick-body"><div class="stud-quick-title">AI Insights</div><div class="stud-quick-desc">Let AI narrate your dataset — metrics, key findings, correlations and smart recommendations.</div></div>' +
              '<div class="stud-quick-arrow"><i class="fas fa-arrow-right"></i></div>' +
            '</div>' +
            '<div class="stud-quick-card" onclick="StudioPage._createBlank()" style="cursor:pointer">' +
              '<div class="stud-quick-ico" style="background:#eef2ff;color:#6366f1"><i class="fas fa-wand-magic-sparkles"></i></div>' +
              '<div class="stud-quick-body"><div class="stud-quick-title">Start from Scratch</div><div class="stud-quick-desc">Blank canvas with full control over every widget.</div></div>' +
              '<div class="stud-quick-arrow"><i class="fas fa-arrow-right"></i></div>' +
            '</div>' +
            '<div class="stud-quick-card" onclick="StudioPage._openSQL()" style="cursor:pointer">' +
              '<div class="stud-quick-ico" style="background:#d1fae5;color:#22c55e"><i class="fas fa-terminal"></i></div>' +
              '<div class="stud-quick-body"><div class="stud-quick-title">SQL to Dashboard</div><div class="stud-quick-desc">Write a query and visualize the results.</div></div>' +
              '<div class="stud-quick-arrow"><i class="fas fa-arrow-right"></i></div>' +
            '</div>' +
            '<div class="stud-quick-card" onclick="StudioPage._importDash()" style="cursor:pointer">' +
              '<div class="stud-quick-ico" style="background:#ffedd5;color:#f97316"><i class="fas fa-file-import"></i></div>' +
              '<div class="stud-quick-body"><div class="stud-quick-title">Import Dashboard</div><div class="stud-quick-desc">Upload a real .pbix Power BI report and rebuild it on your data — exact layout & theme.</div></div>' +
              '<div class="stud-quick-arrow"><i class="fas fa-arrow-right"></i></div>' +
            '</div>' +
          '</div>' +
        '</div>' +

        // ── Two Column Layout: Select Data + Templates ──
        '<div class="studio-two-col">' +

          // ── Select Data Source ──
          '<div class="studio-section">' +
            '<div class="stud-section-head"><h2 class="section-title"><i class="fas fa-database mr-2"></i>Data Source</h2><span class="stud-quick-note">Pick a table to analyze</span></div>' +
            '<div class="card" id="studio-data-card">' +
              '<div class="studio-data-search">' +
                '<input type="text" class="form-input" placeholder="Search tables..." id="studio-data-search" oninput="StudioPage._filterTables()">' +
              '</div>' +
              '<div class="studio-data-list" id="studio-data-list">' +
                '<div class="text-center py-8 text-gray-400"><i class="fas fa-spinner fa-spin text-lg"></i><p class="mt-2 text-sm">Loading data sources...</p></div>' +
              '</div>' +
            '</div>' +
          '</div>' +

          // ── Template Gallery ──
          '<div class="studio-section">' +
            '<div class="stud-section-head"><h2 class="section-title"><i class="fas fa-layer-group mr-2"></i>Template Gallery</h2><span class="stud-quick-note">Start from a proven layout</span></div>' +
            '<div class="studio-tpl-layout">' +
              '<div class="studio-tpl-rail" id="studio-tpl-rail"></div>' +
              '<div class="studio-tpl-main">' +
                '<div class="studio-tpl-toolbar">' +
                  '<input type="text" class="form-input" placeholder="Search templates..." id="studio-tpl-search" oninput="StudioPage._filterTemplates()">' +
                  '<span class="studio-tpl-count" id="studio-tpl-count"></span>' +
                '</div>' +
                '<div class="studio-templates-grid" id="studio-templates">' +
                  '<div class="text-center py-8 text-gray-400"><i class="fas fa-spinner fa-spin text-lg"></i></div>' +
                '</div>' +
              '</div>' +
            '</div>' +
          '</div>' +

        '</div>' +

        // ── My Dashboards ──
        '<div class="stud-section-head"><h2 class="section-title"><i class="fas fa-folder-open mr-2"></i>My Dashboards</h2><span class="stud-quick-note">Open, present, duplicate or manage what you saved</span></div>' +
        '<div class="card" id="studio-mydash-card">' +
          '<div id="studio-mydash-list">' +
            '<div class="text-center py-8 text-gray-400"><i class="fas fa-spinner fa-spin text-lg"></i><p class="mt-2 text-sm">Loading dashboards...</p></div>' +
          '</div>' +
        '</div>' +

        // ── Recent activity (collapsible) ──
        '<div class="stud-section-head" style="margin-top:10px">' +
          '<button class="btn btn-sm btn-ghost" onclick="StudioPage._toggleHistory()" title="Show / hide creation log"><i class="fas fa-clock-rotate-left mr-1"></i> Recent activity <span id="studio-history-count" class="text-gray-400"></span></button>' +
        '</div>' +
        '<div class="card" id="studio-history-card" style="display:none">' +
          '<div id="studio-history-list"></div>' +
        '</div>' +

        '</div>';

      // Load data
      await this._loadTables();
      this._renderTemplates();
      this._renderHistory();
      this._renderDashboards();
      setTimeout(() => this._updateKpis(), 0);

      // Apply premium feature gating to controls rendered in this page
      if (window.RBAC && window.RBAC.applyToNav) {
        try { window.RBAC.applyToNav(); } catch (e) { console.warn("Failed to apply RBAC gating:", e); }
      }
    },

    // ── Data Sources ──
    async _loadTables() {
      var container = document.getElementById("studio-data-list");
      if (!container) return;
      try {
        var result = await API.tables();
        var tbls = result && result.tables ? result.tables : result;
        if (Array.isArray(tbls)) {
          _tables = tbls;
        } else if (tbls && typeof tbls === "object") {
          _tables = Object.keys(tbls).map(function (k) {
            var t = tbls[k] || {};
            return { table_name: t.table_name || t.name || k, row_count: t.row_count || 0, column_count: t.column_count || (t.columns ? t.columns.length : 0), columns: t.columns || [] };
          });
        }
        this._renderTables(_tables);
      } catch (e) {
        container.innerHTML = '<div class="text-center py-6 text-gray-400"><i class="fas fa-exclamation-triangle text-lg mb-2"></i><p class="text-sm">Could not load data sources</p></div>';
      }
    },

    _renderTables(tables) {
      var container = document.getElementById("studio-data-list");
      if (!container) return;
      if ((!tables || tables.length === 0) && this._filterActive) {
        container.innerHTML = '<div class="text-center py-8 text-gray-400"><i class="fas fa-magnifying-glass text-lg mb-2"></i><p class="text-sm">No tables match your search</p></div>';
        return;
      }
      if (!tables || tables.length === 0) {
        container.innerHTML = '<div class="flex flex-col items-center py-8"><i class="fas fa-database text-3xl text-gray-300 mb-3"></i><p class="text-sm text-gray-400">No tables found. <a href="#upload" class="text-primary-500 hover:underline">Upload data</a></p></div>';
        return;
      }
      var savedName = null;
      try {
        var raw = localStorage.getItem("studio_selected_table");
        if (raw) savedName = (JSON.parse(raw) || {}).name || null;
      } catch (e) { savedName = null; }
      var html = '<div class="space-y-1">';
      tables.forEach(function (t) {
        var name = t.table_name || t.name || "Table";
        var idx = _tables.indexOf(t);
        if (idx === -1) idx = 0;
        var rows = t.row_count != null ? t.row_count.toLocaleString() : "—";
        var cols = t.column_count != null ? t.column_count : (t.columns ? t.columns.length : "—");
        html += '<div class="studio-data-item' + (savedName === name ? " selected" : "") + '" onclick="StudioPage._selectTable(' + idx + ')" data-idx="' + idx + '">' +
          '<div class="studio-data-icon"><i class="fas fa-table"></i></div>' +
          '<div class="studio-data-info">' +
            '<div class="studio-data-name">' + _esc(name) + '</div>' +
            '<div class="studio-data-meta">' + rows + ' rows · ' + cols + ' columns</div>' +
          '</div>' +
          '<div class="studio-data-ai" title="AI insights for this table" onclick="event.stopPropagation();StudioPage._aiInsightsFrom(' + idx + ')"><i class="fas fa-robot"></i><span>Insights</span></div>' +
          '<div class="studio-data-action"><i class="fas fa-chevron-right"></i></div>' +
        '</div>';
      });
      html += '</div>';
      var countLine = this._filterActive
        ? '<div class="studio-filter-count">' + tables.length + (tables.length === 1 ? " match" : " matches") + '</div>'
        : "";
      container.innerHTML = countLine + html;
      // restore persisted selection
      var saved = _tables.filter(function (t) { return (t.table_name || t.name) === savedName; })[0] || null;
      this._selectedTable = saved;
    },

    _filterTables() {
      var query = (document.getElementById("studio-data-search").value || "").trim().toLowerCase();
      this._filterActive = !!query;
      var filtered;
      if (!query) {
        filtered = _tables;
      } else {
        filtered = _tables.filter(function (t) {
          var name = (t.table_name || t.name || "").toLowerCase();
          if (name.indexOf(query) !== -1) return true;
          var cols = (t.columns || []).map(function (c) {
            return String((c && (c.column_name || c.name)) || (typeof c === "string" ? c : c && c.ColumnName) || "").toLowerCase();
          });
          return cols.some(function (c) { return c.indexOf(query) !== -1; });
        });
      }
      this._renderTables(filtered);
    },

    _selectedTable: null,
    _filterActive: false,

    _selectTable(idx) {
      var t = _tables[idx];
      if (!t) return;
      this._selectedTable = t;
      try { localStorage.setItem("studio_selected_table", JSON.stringify({ name: t.table_name || t.name })); } catch (e) {}
      this._markTableSelected(idx);
      App.info("Selected: " + (t.table_name || t.name));
    },

    _markTableSelected(idx) {
      document.querySelectorAll(".studio-data-item").forEach(function (el) { el.classList.remove("selected"); });
      var items = document.querySelectorAll(".studio-data-item");
      items.forEach(function (el) {
        if (parseInt(el.getAttribute("data-idx")) === idx) el.classList.add("selected");
      });
    },

    // ── AI Insights (narrative panel for the selected table) ──
    _aiInsights() {
      if (!this._selectedTable) {
        App.warning("Please select a data source first");
        return;
      }
      this._openInsightsModal(this._selectedTable);
    },

    _aiInsightsFrom(idx) {
      var t = _tables[idx];
      if (!t) return;
      this._selectedTable = t;
      try { localStorage.setItem("studio_selected_table", JSON.stringify({ name: t.table_name || t.name })); } catch (e) {}
      this._markTableSelected(idx);
      this._openInsightsModal(t);
    },

    async _openInsightsModal(t) {
      var tableName = t.table_name || t.name || "table";
      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal ds-ai-modal" id="ds-ai-modal" style="max-width:680px">' +
          '<div class="ds-ai-head">' +
            '<div class="ds-ai-head-ico"><i class="fas fa-lightbulb"></i></div>' +
            '<div class="ds-ai-head-txt"><h3>AI Insights</h3><span class="ds-ai-head-sub">' + _esc(tableName) + '</span></div>' +
            '<span class="ds-ai-src" id="ds-ai-src">Auto</span>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button>' +
          '</div>' +
          '<div id="ds-ai-status" class="ds-ai-loading">' +
            '<i class="fas fa-spinner fa-spin fa-lg"></i><div>Asking the AI to read your data&hellip;</div>' +
          '</div>' +
        '</div>';
      document.body.appendChild(ov);

      var self = this;
      var ins = null, source = "Auto";
      try {
        var auto = await API.insights(tableName);
        if (auto && auto.success && auto.insights) ins = auto.insights;
      } catch (e) { ins = null; }
      if (!ins) {
        source = "LLM";
        try {
          var llm = await API.llmInsights(tableName, "Explain this dataset: give an executive summary, key findings, correlations, distributions and concrete recommendations, each as a concise bullet with exact figures.");
          ins = (llm && llm.insights) || null;
        } catch (e2) { ins = null; }
      }

      var status = document.getElementById("ds-ai-status");
      var modal = document.getElementById("ds-ai-modal");
      if (!modal || !status) { ov.remove(); return; }

      if (!ins) {
        status.innerHTML = '<div class="ds-ai-error"><i class="fas fa-triangle-exclamation"></i>The AI insight service is unavailable right now (auto and LLM both failed). Close and try again shortly.</div>';
        return;
      }

      var rendered = this._renderInsightsBody(tableName, ins, source);
      modal.setAttribute("data-plain", rendered.plain);
      var body = rendered.html;
      var srcEl = document.getElementById("ds-ai-src");
      if (srcEl) {
        srcEl.textContent = source;
        srcEl.className = "ds-ai-src " + (source === "LLM" ? "ds-ai-src-llm" : "ds-ai-src-auto");
      }
      status.outerHTML = body;
      ov.querySelectorAll("[data-close]").forEach(function (b) {
        b.addEventListener("click", function () { ov.remove(); });
      });
      if (ov.querySelector("[data-refresh]")) {
        ov.querySelector("[data-refresh]").addEventListener("click", function () {
          ov.remove();
          self._openInsightsModal(t);
        });
      }
      if (ov.querySelector("[data-build-insight]")) {
        ov.querySelectorAll("[data-build-insight]").forEach(function (b) {
          b.addEventListener("click", function () {
            var text = (this.getAttribute("data-build-insight") || "").trim();
            ov.remove();
            self._promptBuildFrom(text);
          });
        });
      }
      if (ov.querySelector("[data-copy]")) {
        ov.querySelector("[data-copy]").addEventListener("click", function () {
          try {
            var txt = modal.getAttribute("data-plain") || "";
            var tmp = document.createElement("textarea");
            tmp.value = txt; document.body.appendChild(tmp); tmp.select();
            document.execCommand("copy"); tmp.remove();
            App.success("Insights copied to clipboard");
          } catch (e) { App.warning("Copy failed"); }
        });
      }
    },

    _renderInsightsBody(tableName, ins, source) {
      var escq = function (s) { var d = document.createElement("div"); if (s == null) s = ""; d.appendChild(document.createTextNode(String(s))); return d.innerHTML; };
      var md = function (s) {
        s = String(s || "");
        return s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\*([^*]+)\*/g, "<em>$1</em>").replace(/\n+/g, "<br>");
      };
      var h = "";
      var plain = ["AI Insights — " + tableName + " (source: " + source + ")"];
      if (ins.summary) {
        plain.push("Summary: " + ins.summary);
        h += '<div class="ds-ai-summary-card"><div class="ds-ai-summary-ico"><i class="fas fa-file-lines"></i></div><div class="ds-ai-summary-txt"><div class="ds-ai-summary-label">Executive summary</div><p class="ds-ai-summary">' + md(ins.summary) + "</p></div></div>";
      }
      if (Array.isArray(ins.metrics_summary) && ins.metrics_summary.length) {
        h += '<div class="ds-ai-sec-title"><span class="ds-ai-sec-chip" style="--chip:#3b82f6"><i class="fas fa-table-list"></i></span> Metric profile</div>';
        h += '<div class="overflow-x-auto"><table class="data-table w-full ds-ai-metrics"><thead><tr><th>Metric</th><th>Mean</th><th>Median</th><th>Std</th><th>Min</th><th>Max</th></tr></thead><tbody>';
        ins.metrics_summary.forEach(function (m) {
          h += "<tr><td>" + escq(m.column || m.name || "—") + "</td><td>" + (m.mean != null ? (+m.mean).toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—") + "</td><td>" + (m.median != null ? (+m.median).toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—") + "</td><td>" + (m.std != null ? (+m.std).toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—") + "</td><td>" + (m.min != null ? (+m.min).toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—") + "</td><td>" + (m.max != null ? (+m.max).toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—") + "</td></tr>";
          plain.push("  " + (m.column || m.name) + ": mean " + m.mean + ", median " + m.median + ", min " + m.min + ", max " + m.max);
        });
        h += "</tbody></table></div>";
      }
      var sections = [
        ["Key findings", ins.key_findings || ins.findings, "fa-bullseye", "#f59e0b"],
        ["Correlations", ins.correlation_highlights || ins.correlations, "fa-link", "#8b5cf6"],
        ["Distributions", ins.distributions, "fa-chart-column", "#0ea5e9"],
        ["Outliers", ins.outlier_highlights, "fa-arrow-trend-up", "#ef4444"],
        ["Recommendations", ins.recommendations, "fa-list-check", "#10b981"]
      ];
      sections.forEach(function (sec) {
        var arr = sec[1];
        if (!Array.isArray(arr) || !arr.length) return;
        h += '<div class="ds-ai-sec-title"><span class="ds-ai-sec-chip" style="--chip:' + sec[3] + '"><i class="fas ' + sec[2] + '"></i></span> ' + sec[0] + "</div><ul class=\"ds-ai-list\">";
        arr.forEach(function (item) {
          var t = typeof item === "string" ? item : (item.text || item.finding || item.narrative || JSON.stringify(item));
          h += "<li>" + md(t);
          if (sec[0] === "Recommendations" || sec[0] === "Key findings") {
            h += '<button type="button" class="btn btn-sm ds-ai-build" data-build-insight="' + escq(String(t).replace(/"/g, "&quot;").slice(0, 160)) + '"><i class="fas fa-wand-magic-sparkles"></i> Build</button>';
            plain.push(sec[0] + ": " + t);
          } else {
            plain.push(sec[0] + ": " + t);
          }
          h += "</li>";
        });
        h += "</ul>";
      });
      if (!h) h = '<div class="ds-ai-empty">No insight content returned for this table.</div>';
      var footer =
        '<div class="ds-ai-foot">' +
          '<button type="button" class="btn btn-sm btn-secondary" data-refresh="1"><i class="fas fa-rotate"></i> Refresh</button>' +
          '<button type="button" class="btn btn-sm btn-secondary" data-copy="1"><i class="fas fa-copy"></i> Copy</button>' +
          '<button type="button" class="btn btn-sm btn-primary" data-close="1"><i class="fas fa-check"></i> Done</button>' +
        '</div>';
      return {
        html: '<div id="ds-ai-body">' + h +
          (ins.confidence ? '<div class="ds-ai-conf"><i class="fas fa-shield-halved"></i> AI confidence: ' + escq(ins.confidence) + '</div>' : '') +
          "</div>" + footer,
        plain: plain.join("\n")
      };
    },

    _promptBuildFrom(text) {
      var self = this;
      this._askCanvasSize("AI dashboard from insight", function (size) {
        self._buildAIDashboard(size, text || "");
      });
    },

    _filterTemplates() {
      this._renderTemplates();
    },

    _setTemplateCategory(cat) {
      this._tplCategory = cat;
      this._renderTemplates();
    },

    _renderTemplateRail(templates) {
      var rail = document.getElementById("studio-tpl-rail");
      if (!rail) return;
      var CATS = [
        ["All", "fa-th-large"],
        ["Executive", "fa-briefcase"],
        ["Finance", "fa-scale-balanced"],
        ["Sales & Retail", "fa-store"],
        ["Marketing", "fa-bullhorn"],
        ["People & HR", "fa-users"],
        ["Operations", "fa-truck-fast"],
        ["CRM", "fa-handshake"],
        ["Industry", "fa-building"],
        ["Quick Start", "fa-wand-magic-sparkles"],
      ];
      var counts = {};
      templates.forEach(function (t) { var c = t.category || "Other"; counts[c] = (counts[c] || 0) + 1; });
      var active = this._tplCategory || "All";
      var html = CATS.map(function (c) {
        var key = c[0];
        var n = key === "All" ? templates.length : (counts[key] || 0);
        var cls = key === active ? "active" : "";
        return '<button type="button" class="studio-tpl-rail-item ' + cls + '" data-cat="' + _esc(key) + '" onclick="StudioPage._setTemplateCategory(\'' + _esc(key) + '\')">' +
          '<i class="fas ' + c[1] + '"></i><span class="stpl-label">' + _esc(key) + '</span><span class="stpl-count">' + n + '</span>' +
        '</button>';
      }).join("");
      rail.innerHTML = html;
    },

    // ── Templates ──
    _renderTemplates() {
      var container = document.getElementById("studio-templates");
      if (!container) return;
      var templates = [];
      if (typeof BIDashTemplates !== "undefined") {
        templates = BIDashTemplates.list();
      }
      if (!templates.length) {
        container.innerHTML = '<div class="text-center py-8 text-gray-400"><p class="text-sm">No templates available</p></div>';
        return;
      }
      this._renderTemplateRail(templates);
      var active = this._tplCategory || "All";
      var query = ((document.getElementById("studio-tpl-search") || {}).value || "").toLowerCase();
      var filtered = templates.filter(function (t) {
        if (active !== "All" && (t.category || "Other") !== active) return false;
        if (!query) return true;
        var hay = (t.name + " " + (t.tags || []).join(" ") + " " + (t.blurb || "")).toLowerCase();
        return hay.indexOf(query) !== -1;
      });
      var countEl = document.getElementById("studio-tpl-count");
      if (countEl) countEl.textContent = filtered.length + " of " + templates.length;
      if (!filtered.length) {
        container.innerHTML = '<div class="text-center py-8 text-gray-400"><p class="text-sm">No templates match</p></div>';
        return;
      }
      var html = '';
      filtered.forEach(function (t) {
        var accent = t.accent || '#3B82F6';
        var kpis = t.kpis || 0;
        var charts = (t.charts || []).length;
        var pages = (t.pages || []).length + 1;
        var meta = [];
        if (kpis) meta.push('<span><i class="fas fa-chart-simple"></i> ' + kpis + ' KPIs</span>');
        if (charts) meta.push('<span><i class="fas fa-chart-column"></i> ' + charts + ' charts</span>');
        if ((t.slicers || []).length) meta.push('<span><i class="fas fa-sliders"></i> ' + t.slicers.length + ' slicers</span>');
        if (pages > 1) meta.push('<span><i class="fas fa-file"></i> ' + pages + ' pages</span>');
        var tags = (t.tags || []).slice(0, 3).map(function (tag) {
          return '<span class="studio-tpl-tag">' + _esc(tag) + '</span>';
        }).join('');
        var navChip = pages > 1
          ? '<span class="studio-tpl-navchip" style="background:' + accent + '1a;color:' + accent + '"><i class="fas fa-arrows-up-down-left-right"></i> ' + _esc(t.nav === "sidebar" ? "Sidebar" : "Tabs") + '</span>'
          : '';
        var searchText = (t.name + " " + (t.tags || []).join(" ") + " " + (t.blurb || "") + " " + (t.category || "")).toLowerCase();
        var preview = '<div class="studio-tpl-preview">' +
          '<div class="stp-kpis">' + Array(Math.min(kpis, 6)).fill('<i style="background:' + accent + '"></i>').join('') + '</div>' +
          '<div class="stp-grid">' +
            '<i class="stp-lg" style="background:' + accent + '"></i>' +
            '<i class="stp-sm" style="background:' + accent + '"></i>' +
            '<i class="stp-sm" style="background:' + accent + '"></i>' +
            '<i class="stp-lg" style="background:' + accent + '"></i>' +
          '</div>' +
        '</div>';
        html += '<div class="studio-template-card" style="border-top:3px solid ' + accent + '" data-search="' + _esc(searchText) + '" onclick="StudioPage._useTemplate(\'' + t.id + '\')">' +
          '<div class="studio-template-top">' +
            '<div class="studio-template-icon" style="background:' + accent + '1a;color:' + accent + '"><i class="fas ' + (t.icon || 'fa-chart-pie') + '"></i></div>' +
            '<span class="studio-tpl-cat" style="color:' + accent + '">' + _esc(t.category || "Template") + '</span>' +
          '</div>' +
          '<div class="studio-template-name">' + _esc(t.name) + '</div>' +
          '<div class="studio-template-desc">' + _esc(t.blurb || '') + '</div>' +
          '<div class="studio-template-meta">' + meta.join('') + '</div>' +
          (tags ? '<div class="studio-template-tags">' + tags + '</div>' : '') +
          (navChip ? '<div class="studio-template-nav">' + navChip + '</div>' : '') +
          preview +
        '</div>';
      });
      container.innerHTML = html;
    },

    _useTemplate(templateId) {
      if (!this._selectedTable) {
        App.warning("Please select a data source first");
        return;
      }
      var self = this;
      this._askCanvasSize("Build dashboard from template", function (size) {
        self._buildDashboard(templateId, "template", size);
      });
    },

    // ── Canvas size picker (shown before any auto-build) ──
    _askCanvasSize(label, onDone) {
      // Power BI-style 16:9 canvases; the custom inputs snap to 16:9 on build.
      var PRESETS = [["Desktop", "1900", "1080"], ["Laptop", "1600", "900"], ["Tablet", "1280", "720"], ["Custom", "1900", "1080"]];
      var chips = PRESETS.map(function (p) {
        return '<button type="button" class="btn btn-sm btn-ghost ds-canvas-chip" data-w="' + p[1] + '" data-h="' + p[2] + '">' + p[0] + '</button>';
      }).join("");
      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal" style="max-width:430px">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-expand"></i> ' + label + '</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button></div>' +
          '<div style="padding:14px 18px 6px;font-size:12px;color:var(--color-content-text-secondary,#64748b)">Choose a canvas size</div>' +
          '<div style="display:flex;flex-wrap:wrap;gap:8px;padding:6px 18px">' + chips + '</div>' +
          '<div style="display:flex;align-items:center;gap:8px;padding:12px 18px">' +
            '<input type="number" min="320" class="form-input" id="ds-canvas-w" value="1900" style="width:90px" title="Canvas width">' +
            '<span style="color:#94a3b8">×</span>' +
            '<input type="number" min="240" class="form-input" id="ds-canvas-h" value="1080" style="width:90px" title="Canvas height">' +
            '<span style="font-size:12px;color:#94a3b8">px</span>' +
          '</div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid var(--color-card-border,#e2e8f0)">' +
            '<button type="button" class="btn btn-sm btn-secondary" data-close="1">Cancel</button>' +
            '<button type="button" class="btn btn-sm btn-primary" data-build="1"><i class="fas fa-layer-group"></i> Build Dashboard</button>' +
          '</div>' +
        '</div>';
      var wInput = ov.querySelector("#ds-canvas-w");
      var hInput = ov.querySelector("#ds-canvas-h");
      ov.querySelectorAll(".ds-canvas-chip").forEach(function (ch) {
        ch.addEventListener("click", function () {
          wInput.value = ch.getAttribute("data-w");
          hInput.value = ch.getAttribute("data-h");
          ov.querySelectorAll(".ds-canvas-chip").forEach(function (x) {
            x.classList.remove("btn-primary");
            x.classList.add("btn-ghost");
          });
          ch.classList.remove("btn-ghost");
          ch.classList.add("btn-primary");
        });
      });
      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); return; }
        if (e.target.closest("[data-build]")) {
          var w = Math.max(320, parseInt(wInput.value, 10) || 1900);
          var h = Math.max(240, parseInt(hInput.value, 10) || 1080);
          // Snap landscape custom sizes to the nearest 16:9 canvas.
          if (w >= h && Math.abs(w / h - 16 / 9) > 0.03) h = Math.round(w * 9 / 16);
          ov.remove();
          if (onDone) onDone({ w: w, h: h });
        } else if (e.target === ov) {
          ov.remove();
        }
      });
      document.body.appendChild(ov);
    },

    _buildDashboard(templateId, kind, size) {
      var tableName = this._selectedTable.table_name || this._selectedTable.name;
      _saveHistory({ type: kind, template: kind === "template" ? templateId : null, table: tableName, time: Date.now() });
      // Build widgets from the template and pass to blank dash
      var widgets = [];
      if (kind === "template" && typeof BIDashTemplates !== "undefined" && BIDashTemplates.build) {
        var ctx = {
          columns: function (t) { return []; },
          isNumeric: function () { return false; },
          isDate: function () { return false; },
          isCategorical: function () { return true; },
        };
        // Try to get real columns
        try {
          var tblData = App.state.tables[tableName] || {};
          var cols = tblData.columns || [];
          if (cols.length) {
            var _colName = function (c) { return c.column_name || c.name || c; };
            var _colInfo = function (c) { return cols.find(function (x) { return _colName(x) === c; }); };
            var _colType = function (col) { return String(col && (col.column_type || col.type) || "").toLowerCase(); };
            ctx.columns = function () { return cols.map(_colName); };
            ctx.isNumeric = function (t, c) {
              var col = _colInfo(c);
              if (!col) return false;
              if (col.isNumeric) return true;
              var ct = _colType(col);
              return /^(number|integer|int|float|double|decimal|real|numeric|bigint|smallint|tinyint|money|fixed|long|short)$/.test(ct);
            };
            ctx.isDate = function (t, c) {
              var col = _colInfo(c);
              if (!col) return false;
              if (col.isDate) return true;
              var ct = _colType(col);
              return /date|time|timestamp/.test(ct);
            };
          }
        } catch (e) { /* ignore */ }
        var built = BIDashTemplates.build(templateId, tableName, ctx);
        if (built && built.widgets) widgets = built.widgets;
        var context = {
          table: tableName,
          name: kind === "template" ? templateId + " Dashboard" : "AI Dashboard",
          widgets: widgets,
          canvasW: size.w,
          canvasH: size.h,
          grid: 12,
          fixedCanvas: true
        };
        if (built && built.pages && built.pages.length > 1) context.pages = built.pages;
        sessionStorage.setItem("blankdash_context", JSON.stringify(context));
      } else {
        sessionStorage.setItem("blankdash_context", JSON.stringify({
          table: tableName,
          name: kind === "template" ? templateId + " Dashboard" : "AI Dashboard",
          widgets: widgets,
          canvasW: size.w,
          canvasH: size.h,
          grid: 12,
          fixedCanvas: true
        }));
      }
      App.navigate("#blankdash");
    },

    // ── Quick Actions ──
    _createBlank() {
      var tableName = this._selectedTable ? (this._selectedTable.table_name || this._selectedTable.name) : "";
      _saveHistory({ type: "blank", table: tableName || null, time: Date.now() });
      sessionStorage.setItem("blankdash_context", JSON.stringify({ table: tableName, name: "Untitled Dashboard", widgets: [], fixedCanvas: true }));
      App.navigate("#blankdash");
    },

    _aiBuild() {
      if (!this._selectedTable) {
        App.warning("Please select a data source first");
        return;
      }
      var self = this;
      // Step 1: interactive design questionnaire (dashboard_interactive.md).
      // Asks the clarifying questions (purpose, audience, KPIs, style, sections,
      // share) before lock in to generate a professional, purpose-fit dashboard.
      this._askDesignQuestions(function (answers) {
        // Step 2: canvas size, then the AI build carries the answers through.
        self._askCanvasSize("AI auto-build dashboard", function (size) {
          self._buildAIDashboard(size, answers.prompt || "", answers);
        });
      });
    },

    // ── Natural-language prompt for AI auto-build ──
    _askAIPrompt(label, onDone) {
      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal" style="max-width:460px">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-robot"></i> ' + label + '</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button></div>' +
          '<div style="padding:14px 18px 6px;font-size:12px;color:var(--color-content-text-secondary,#64748b)">' +
            'Describe the dashboard you want. The AI will pick the best charts (leave blank for automatic suggestions).</div>' +
          '<div style="padding:10px 18px">' +
            '<textarea class="form-input" id="ds-ai-prompt" rows="4" placeholder="e.g. Revenue by region, top products by sales, monthly trend, and a correlation analysis..."></textarea>' +
            '<div class="ds-ai-hints" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">' +
              '<button type="button" class="ds-ai-hint" data-hint="monthly sales trend and top products">Monthly trend + top products</button>' +
              '<button type="button" class="ds-ai-hint" data-hint="revenue by region/segment and a pie breakdown">Regional breakdown</button>' +
              '<button type="button" class="ds-ai-hint" data-hint="correlations between metrics as a scatter/heatmap">Correlation view</button>' +
            '</div>' +
          '</div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid var(--color-card-border,#e2e8f0)">' +
            '<button type="button" class="btn btn-sm btn-secondary" data-close="1">Cancel</button>' +
            '<button type="button" class="btn btn-sm btn-primary" data-build="1"><i class="fas fa-wand-magic-sparkles"></i> Generate with AI</button>' +
          '</div>' +
        '</div>';
      var ta = ov.querySelector("#ds-ai-prompt");
      ov.querySelectorAll(".ds-ai-hint").forEach(function (b) {
        b.addEventListener("click", function () { ta.value = this.getAttribute("data-hint"); });
      });
      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); return; }
        if (e.target.closest("[data-build]")) {
          var p = (ta.value || "").trim();
          ov.remove();
          if (onDone) onDone(p);
        } else if (e.target === ov) {
          ov.remove();
        }
      });
      document.body.appendChild(ov);
      setTimeout(function () { if (ta) ta.focus(); }, 50);
    },

    // ── Interactive design questionnaire for AI Auto-Build ──
    // dashboard_interactive.md: ask 3-6 clarifying questions before generating.
    // This 4-step wizard collects purpose, audience, KPIs/focus, visual style,
    // sections and access. Answers feed the AI prompt, the theme, and the
    // on-canvas Design Brief. Last answers are kept in localStorage to prefill
    // the next build.
    _askDesignQuestions(onDone) {
      var self = this;
      var a = this._defaultAnswers();
      var step = 0;
      var STEPS = [
        { title: "Purpose & Audience", note: "Who is this dashboard for and what do they need to decide?" },
        { title: "KPIs & Focus", note: "What are the top questions the dashboard must answer?" },
        { title: "Style & Access", note: "Pick a visual theme and how the dashboard is shared." },
        { title: "Pages & Layout", note: "One curated executive canvas, or several focused pages — pick what fits your story." }
      ];

      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      var modal = document.createElement("div");
      modal.className = "bd-gal-modal";
      modal.style.maxWidth = "680px";
      ov.appendChild(modal);

      // Single-select option chip (purpose/audience/style groups) or multi
      // (kpi group). data-opt encodes "<group>_<id>". When rewrite is true the
      // chip gets a small "rewrite with AI" wand icon (purpose/audience only).
      function chipOption(id, label, desc, icon, rewrite) {
        return '<button type="button" class="ds-ai-hint ds-des-opt" data-opt="' + id + '" style="text-align:left;border:1px solid var(--color-card-border,#e2e8f0);background:#fff;border-radius:10px;padding:9px 12px;cursor:pointer;font-size:12px;width:100%;position:relative">' +
          (icon ? '<i class="fas ' + icon + '" style="color:#7B61FF;margin-right:6px"></i>' : "") +
          '<b>' + label + "</b>" +
          (desc ? '<span class="ds-opt-desc" data-desc-for="' + id + '" style="display:block;font-weight:400;font-size:11px;color:var(--color-content-text-secondary,#64748b);margin-top:2px' + (rewrite ? ";padding-right:18px" : "") + '">' + desc + "</span>" : "") +
          (rewrite ? '<span class="ds-ai-rewrite" data-rewrite="' + id + '" title="Rewrite with AI" style="position:absolute;top:6px;right:8px;color:#7B61FF;cursor:pointer;font-size:11px;padding:2px"><i class="fas fa-wand-magic-sparkles"></i></span>' : "") +
          "</button>";
      }

      function render() {
        var M = _DESIGN_META;
        var head =
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-robot" style="color:#7B61FF;margin-right:6px"></i> AI Auto-Build</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1" title="Cancel">&times;</button></div>' +
          '<div style="padding:2px 18px 0;font-size:11px;color:var(--color-content-text-secondary,#64748b);display:flex;gap:14px;align-items:center;flex-wrap:wrap">' +
            STEPS.map(function (s, i) {
              var active = i === step, done = i < step;
              return '<div style="display:flex;align-items:center;gap:5px' + (active ? ";color:#7B61FF;font-weight:700" : "") + '">' +
                '<span style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:' + (done ? "#10b981" : active ? "#7B61FF" : "#e2e8f0") + ";color:#fff;font-size:10px\">" +
                (done ? '<i class="fas fa-check"></i>' : i + 1) + "</span>" + s.title + "</div>";
            }).join("") +
          "</div>" +
          '<div style="padding:12px 18px 2px;font-size:12.5px;font-weight:700">' + STEPS[step].title + "</div>" +
          '<div style="padding:0 18px;font-size:11.5px;color:var(--color-content-text-secondary,#64748b)">' + STEPS[step].note + "</div>";

        var body = "";
        if (step === 0) {
          body += '<div class="ds-ai-list-title" style="padding:10px 18px 4px;display:flex;align-items:center;justify-content:space-between"><span><i class="fas fa-bullseye"></i> Main purpose</span>' +
            '<button type="button" class="ds-ai-custom" data-custom="purpose" title="Give a custom prompt" style="border:none;background:none;color:' + (a.purposeCustom ? "#7B61FF" : "var(--color-content-text-secondary,#64748b)") + ';cursor:pointer;font-size:12px;padding:2px 4px"><i class="fas fa-robot"></i></button></div>' +
            (a.purposeCustom ? '<div class="ds-ai-custom-note" data-custom-note="purpose" style="padding:2px 18px 2px;font-size:11px;color:#7B61FF;display:flex;align-items:center;gap:6px"><i class="fas fa-circle-check"></i><span style="flex:1">' + _esc(a.purposeCustom) + '</span><button type="button" class="ds-ai-custom-clear" data-custom-clear="purpose" title="Clear custom prompt" style="border:none;background:none;color:#ef4444;cursor:pointer;font-size:11px;padding:2px"><i class="fas fa-times"></i></button></div>' : "") +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:4px 18px">' +
            M.purposes.map(function (p) { return chipOption("purpose_" + p.id, p.label, p.desc, p.icon, true); }).join("") + "</div>" +
            '<div class="ds-ai-list-title" style="padding:10px 18px 4px;display:flex;align-items:center;justify-content:space-between"><span><i class="fas fa-users"></i> Audience & decisions</span>' +
            '<button type="button" class="ds-ai-custom" data-custom="audience" title="Give a custom prompt" style="border:none;background:none;color:' + (a.audienceCustom ? "#7B61FF" : "var(--color-content-text-secondary,#64748b)") + ';cursor:pointer;font-size:12px;padding:2px 4px"><i class="fas fa-robot"></i></button></div>' +
            (a.audienceCustom ? '<div class="ds-ai-custom-note" data-custom-note="audience" style="padding:2px 18px 2px;font-size:11px;color:#7B61FF;display:flex;align-items:center;gap:6px"><i class="fas fa-circle-check"></i><span style="flex:1">' + _esc(a.audienceCustom) + '</span><button type="button" class="ds-ai-custom-clear" data-custom-clear="audience" title="Clear custom prompt" style="border:none;background:none;color:#ef4444;cursor:pointer;font-size:11px;padding:2px"><i class="fas fa-times"></i></button></div>' : "") +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:4px 18px">' +
            M.audiences.map(function (p) { return chipOption("audience_" + p.id, p.label, p.desc, p.icon, true); }).join("") + "</div>";
        } else if (step === 1) {
          body += '<div class="ds-ai-list-title" style="padding:10px 18px 4px"><i class="fas fa-chart-simple"></i> Top KPIs / questions (select all that apply)</div>' +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:4px 18px">' +
            M.kpis.map(function (p) { return chipOption("kpi_" + p.id, p.label, p.desc, p.icon); }).join("") + "</div>" +
            '<div class="ds-ai-list-title" style="padding:10px 18px 2px;display:flex;align-items:center;justify-content:space-between"><span><i class="fas fa-pen"></i> Anything else the dashboard should show (optional)</span>' +
            '<button type="button" class="ds-ai-custom" data-custom="extra" title="Give a custom prompt" style="border:none;background:none;color:' + (a.extraCustom ? "#7B61FF" : "var(--color-content-text-secondary,#64748b)") + ';cursor:pointer;font-size:12px;padding:2px 4px"><i class="fas fa-robot"></i></button></div>' +
            '<div style="padding:2px 18px 10px"><textarea class="form-input" id="ds-des-prompt" rows="2" placeholder="e.g. Show YTD vs target by region and a weekly running average..."></textarea></div>';
        } else if (step === 2) {
          body += '<div class="ds-ai-list-title" style="padding:10px 18px 4px"><i class="fas fa-palette"></i> Visual style / theme</div>' +
            '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;padding:4px 18px">' +
            M.styles.map(function (p) { return chipOption("style_" + p.id, p.label, p.desc, p.icon); }).join("") + "</div>" +
            '<div class="ds-ai-list-title" style="padding:10px 18px 4px"><i class="fas fa-shield-halved"></i> Access</div>' +
            '<div style="padding:2px 18px 10px;display:flex;align-items:center;gap:8px">' +
              '<input type="checkbox" id="ds-des-share" style="accent-color:#7B61FF"' + (a.sharePublic ? " checked" : "") + ">" +
              '<label for="ds-des-share" style="font-size:12.5px">Make this dashboard shareable as a public present link</label>' +
            "</div>";
        } else if (step === 3) {
          // Pages & Layout — dynamic page list with + to add more.
          var pgCount = Math.max(1, parseInt(a.pages, 10) || 1);
          if (!Array.isArray(a.pageFocus)) a.pageFocus = ["overview"];
          if (!Array.isArray(a.pagePrompt)) a.pagePrompt = [];
          for (var pi = 0; pi < pgCount; pi++) {
            if (!a.pageFocus[pi]) a.pageFocus[pi] = pi === 0 ? "overview" : (pi === 1 ? "breakdown" : "correlation");
          }
          body += '<div class="ds-ai-list-title" style="padding:10px 18px 4px"><i class="fas fa-layer-group"></i> Pages</div>' +
            '<div style="padding:4px 18px 10px;display:flex;flex-direction:column;gap:8px">';
          for (var pg = 1; pg <= pgCount; pg++) {
            if (pg === 1) {
              body += '<div style="border:1px solid var(--color-card-border,#e2e8f0);border-radius:10px;padding:9px 12px;font-size:12px;display:flex;align-items:center;justify-content:space-between">' +
                '<span><b>Page 1 · Overview</b> <span style="color:var(--color-content-text-secondary,#64748b)">(auto — KPIs, trend, top breakdown & insights)</span></span>' +
                '<span style="color:var(--color-content-text-secondary,#94a3b8);font-size:11px"><i class="fas fa-lock"></i></span>' +
                '</div>';
              continue;
            }
            body += '<div class="ds-dynamic-page" style="border:1px solid var(--color-card-border,#e2e8f0);border-radius:10px;padding:10px 12px">' +
              '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">' +
                '<div style="font-size:12px;font-weight:700">Page ' + pg + ' — main focus</div>' +
                '<button type="button" class="btn btn-sm btn-ghost ds-pg-remove" data-pg="' + pg + '" title="Remove page" style="color:#ef4444;padding:2px 6px;font-size:11px"><i class="fas fa-times"></i></button>' +
              '</div>' +
              '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">' +
              M.pageFocuses.map(function (p) {
                var on = a.pageFocus[pg - 1] === p.id;
                return '<button type="button" class="btn btn-sm ds-pg-focus' + (on ? " btn-primary" : " btn-ghost") + '" data-page="' + pg + '" data-fid="' + p.id + '" title="' + (p.desc || "") + '"><i class="fas ' + p.icon + '"></i> ' + p.label + "</button>";
              }).join("") + "</div>" +
              '<input type="text" class="form-input ds-pg-txt" data-page="' + pg + '" value="' + _esc(a.pagePrompt[pg - 1] || "") + '" placeholder="Optional — page title / extra focus (e.g. profit by region and top categories)" style="width:100%;font-size:12px">' +
            "</div>";
          }
          body += '<button type="button" class="btn btn-sm btn-ghost ds-pg-add" style="display:flex;align-items:center;gap:6px;padding:8px 14px;border:1px dashed var(--color-card-border,#cbd5e1);border-radius:10px;color:var(--color-accent,#7B61FF);font-size:12px"><i class="fas fa-plus"></i> Add page</button>';
          body += "</div>";
        }

        var foot =
          '<div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid var(--color-card-border,#e2e8f0)">' +
            (step > 0 ? '<button type="button" class="btn btn-sm btn-secondary" data-back="1"><i class="fas fa-arrow-left"></i> Back</button>' : "") +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">Cancel</button>' +
            (step < STEPS.length - 1
              ? '<button type="button" class="btn btn-sm btn-primary" data-next="1">Next <i class="fas fa-arrow-right"></i></button>'
              : '<button type="button" class="btn btn-sm btn-primary" data-build="1"><i class="fas fa-wand-magic-sparkles"></i> Generate Dashboard</button>') +
          "</div>";

        modal.innerHTML = head + '<div id="ds-des-body" style="flex:1;min-height:0;overflow-y:auto">' + body + "</div>" + foot;

        // Wire up chips: single-select per group, multi-select for kpi.
        // applyState also renders the persisted (prefilled) selection.
        modal.querySelectorAll(".ds-des-opt[data-opt]").forEach(function (el) {
          var parts = (el.getAttribute("data-opt") || "").split("_", 2);
          var group = parts[0], id = parts[1];
          var applyState = function (on) {
            el.style.background = on ? "#7B61FF1a" : "#fff";
            el.style.borderColor = on ? "#7B61FF" : "";
          };
          applyState(group === "kpi" ? (a.kpis.indexOf(id) !== -1) : (a[group] === id));
          el.addEventListener("click", function () {
            if (group === "kpi") {
              var idx = a.kpis.indexOf(id);
              if (idx === -1) a.kpis.push(id); else a.kpis.splice(idx, 1);
              applyState(a.kpis.indexOf(id) !== -1);
            } else {
              a[group] = id;
              modal.querySelectorAll(".ds-des-opt[data-opt^='" + group + "_']").forEach(function (x) {
                var on = x === el;
                x.style.background = on ? "#7B61FF1a" : "#fff";
                x.style.borderColor = on ? "#7B61FF" : "";
              });
            }
          });
        });
        // Pages & Layout controls (dynamic add/remove + per-page focus chips + text).
        var addPgBtn = modal.querySelector(".ds-pg-add");
        if (addPgBtn) {
          addPgBtn.addEventListener("click", function () {
            var n = parseInt(a.pages, 10) || 1;
            a.pages = n + 1;
            if (!a.pageFocus[n]) a.pageFocus[n] = "breakdown";
            if (!a.pagePrompt[n]) a.pagePrompt[n] = "";
            render();
          });
        }
        modal.querySelectorAll(".ds-pg-remove").forEach(function (el) {
          el.addEventListener("click", function () {
            var pg = parseInt(el.getAttribute("data-pg"), 10);
            var n = parseInt(a.pages, 10) || 1;
            if (pg < 2 || n < 2) return;
            a.pageFocus.splice(pg - 1, 1);
            a.pagePrompt.splice(pg - 1, 1);
            a.pages = n - 1;
            render();
          });
        });
        modal.querySelectorAll(".ds-pg-focus").forEach(function (el) {
          el.addEventListener("click", function () {
            var pg = parseInt(el.getAttribute("data-page"), 10) - 1;
            if (pg < 0) return;
            a.pageFocus[pg] = el.getAttribute("data-fid");
            modal.querySelectorAll(".ds-pg-focus[data-page='" + (pg + 1) + "']").forEach(function (x) {
              var on = x === el;
              x.classList.toggle("btn-primary", on);
              x.classList.toggle("btn-ghost", !on);
            });
          });
        });
        modal.querySelectorAll(".ds-pg-txt").forEach(function (el) {
          el.addEventListener("input", function () {
            var pg = parseInt(el.getAttribute("data-page"), 10) - 1;
            if (pg < 0) return;
            a.pagePrompt[pg] = el.value;
          });
        });
        var promptEl = modal.querySelector("#ds-des-prompt");
        if (promptEl) { promptEl.value = a.prompt || ""; promptEl.addEventListener("input", function () { a.prompt = promptEl.value; }); }
        var shareEl = modal.querySelector("#ds-des-share");
        if (shareEl) shareEl.addEventListener("change", function () { a.sharePublic = shareEl.checked; });
        // "Rewrite with AI" wand on purpose/audience chips — rewrites the
        // option's description in place and stores the override for the prompt
        // and the on-canvas Design Brief.
        modal.querySelectorAll(".ds-ai-rewrite").forEach(function (el) {
          el.addEventListener("click", function (e) {
            e.stopPropagation();
            var optId = el.getAttribute("data-rewrite");
            var descEl = modal.querySelector('[data-desc-for="' + optId + '"]');
            var origDesc = descEl ? descEl.textContent : "";
            var icon = el.querySelector("i");
            var oldCls = icon ? icon.className : "";
            if (icon) icon.className = "fas fa-spinner fa-spin";
            el.style.pointerEvents = "none";
            API.rewrite(origDesc, optId.indexOf("purpose_") === 0 ? "purpose" : "audience")
              .then(function (res) {
                if (res && res.success && res.rewritten) {
                  if (!a.rewrites) a.rewrites = {};
                  a.rewrites[optId] = res.rewritten;
                  if (descEl) descEl.textContent = res.rewritten;
                  if (App.success) App.success("Rewritten with AI");
                } else if (App.warning) {
                  App.warning((res && res.error) || "AI rewrite failed");
                }
              })
              .catch(function (err) {
                if (App.warning) App.warning("AI rewrite failed — " + ((err && err.message) || "try again"));
              })
              .finally(function () {
                if (icon) icon.className = oldCls;
                el.style.pointerEvents = "";
              });
          });
        });
        // "Give a custom prompt" icon on section headings — opens the prompt
        // modal; the typed text is used as-is for that section in the build
        // prompt. Blank input clears it.
        modal.querySelectorAll(".ds-ai-custom[data-custom]").forEach(function (el) {
          el.addEventListener("click", function () {
            var key = el.getAttribute("data-custom");
            var label = key === "purpose" ? "Main purpose" : (key === "audience" ? "Audience & decisions" : "Anything else to show");
            self._askAIPrompt(label + " — custom prompt", function (text) {
              var v = (text || "").trim();
              if (key === "purpose") a.purposeCustom = v;
              else if (key === "audience") a.audienceCustom = v;
              else a.extraCustom = v;
              render();
            });
          });
        });
        modal.querySelectorAll(".ds-ai-custom-clear[data-custom-clear]").forEach(function (el) {
          el.addEventListener("click", function () {
            var key = el.getAttribute("data-custom-clear");
            if (key === "purpose") a.purposeCustom = "";
            else if (key === "audience") a.audienceCustom = "";
            else a.extraCustom = "";
            render();
          });
        });
      }

      modal.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); return; }
        if (e.target.closest("[data-back]")) { step = Math.max(0, step - 1); render(); return; }
        if (e.target.closest("[data-next]")) { step = Math.min(STEPS.length - 1, step + 1); render(); return; }
        if (e.target.closest("[data-build]")) {
          try { localStorage.setItem("studio_design_defaults", JSON.stringify(a)); } catch (e2) { /* ignore */ }
          ov.remove();
          if (onDone) onDone(a);
        }
      });
      ov.addEventListener("click", function (e) { if (e.target === ov) ov.remove(); });
      document.body.appendChild(ov);
      render();
    },

    // Default design answers — used to prefill the wizard and for the
    // "Build from this insight" path that skips the questionnaire.
    _defaultAnswers() {
      var a = {
        purpose: "executive", audience: "executives",
        kpis: ["revenue"], prompt: "", style: "light", sharePublic: false,
        sections: ["kpi", "filters", "trends", "breakdown", "table", "insights"],
        pages: 4, pageFocus: ["overview", "breakdown", "geo", "data"], pagePrompt: [], widgets: [], rewrites: {},
        purposeCustom: "", audienceCustom: "", extraCustom: ""
      };
      try {
        var saved = JSON.parse(localStorage.getItem("studio_design_defaults") || "null");
        if (saved && typeof saved === "object") {
          for (var k in a) {
            if (saved[k] !== undefined && saved[k] !== null) a[k] = saved[k];
          }
        }
      } catch (e) { /* ignore */ }
      if (!Array.isArray(a.kpis) || !a.kpis.length) a.kpis = ["revenue"];
      // The Widgets picker step was removed — the AI always places every widget
      // type automatically. Drop any stale saved selection so it can't filter.
      a.widgets = [];
      if (!a.rewrites || typeof a.rewrites !== "object") a.rewrites = {};
      if (!Array.isArray(a.sections) || !a.sections.length) a.sections = ["kpi", "filters", "trends", "breakdown", "table", "insights"];
      return a;
    },

    // Turn the design answers into a concise, business-focused AI prompt line
    // (e.g. "Executives — Revenue / sales trend, Profit & margin by region.")
    _aiPromptFromAnswers(a) {
      a = a || {};
      function lbl(list, id) {
        for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i].label;
        return "";
      }
      var parts = [];
      var purpose = lbl(_DESIGN_META.purposes, a.purpose);
      var audience = lbl(_DESIGN_META.audiences, a.audience);
      var purposeCustom = (a.purposeCustom || "").trim();
      var audienceCustom = (a.audienceCustom || "").trim();
      var extraCustom = (a.extraCustom || "").trim();
      if (purposeCustom) {
        parts.push("Purpose: " + purposeCustom);
      } else if (purpose || audience) {
        parts.push((purpose ? purpose : "") + (audience ? " for " + audience : ""));
      }
      if (audienceCustom) parts.push("Audience: " + audienceCustom);
      var kpiNames = (a.kpis || []).map(function (k) { return lbl(_DESIGN_META.kpis, k) || k; });
      if (kpiNames.length) parts.push("focus on: " + kpiNames.join(", "));
      var rewrites = (a.rewrites && typeof a.rewrites === "object") ? a.rewrites : {};
      var purposeDesc = rewrites["purpose_" + a.purpose];
      var audienceDesc = rewrites["audience_" + a.audience];
      if (!purposeCustom && purposeDesc) parts.push("Purpose detail: " + purposeDesc);
      if (!audienceCustom && audienceDesc) parts.push("Audience detail: " + audienceDesc);
      if (extraCustom) parts.push("Also show: " + extraCustom);
      return parts.filter(Boolean).join(". ");
    },

    // Map a wizard style id to a blankdash theme + Chart.js palette. Kept
    // self-contained here (no dependency on blankdash internals); the themeId
    // is stamped onto each widget card (config.style) so only the widgets get
    // themed — the page/app chrome is left on its default look.
    _studioThemeOpts(style) {
      var T = {
        light:    { themeId: "light",    accent: "#2563eb", accent2: "#1e3a8a", palette: ["#2563eb", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"], dark: false, cardBg: "#ffffff", text: "#0f172a", text2: "#64748b", grid: "#e2e8f0" },
        dark:     { themeId: "dark",     accent: "#818cf8", accent2: "#0f172a", palette: ["#818cf8", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#22d3ee"], dark: true, cardBg: "#252540", text: "#e0e0e0", text2: "#99a1b9", grid: "#3a3a5c" },
        midnight: { themeId: "midnight", accent: "#38bdf8", accent2: "#020617", palette: ["#38bdf8", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#fb7185"], dark: true, cardBg: "#141b2d", text: "#e2e8f0", text2: "#7c89a3", grid: "#223049" },
        ocean:    { themeId: "ocean",    accent: "#06b6d4", accent2: "#0f172a", palette: ["#06b6d4", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#60a5fa"], dark: true, cardBg: "#0b3b54", text: "#e6f7ff", text2: "#8fc3d9", grid: "#155e82" },
        slate:    { themeId: "slate",    accent: "#475569", accent2: "#0f172a", palette: ["#475569", "#0f766e", "#b45309", "#be185d", "#6d28d9", "#0369a1"], dark: true, cardBg: "#1e293b", text: "#e2e8f0", text2: "#94a3b8", grid: "#334155" },
        sunset:   { themeId: "sunset",   accent: "#fb7185", accent2: "#ffb347", palette: ["#fb7185", "#ffb347", "#fde047", "#fb923c", "#f87171", "#f472b6"], dark: true, cardBg: "#421531", text: "#fff0f7", text2: "#d9a3b8", grid: "#6c2746" },
        forest:   { themeId: "forest",   accent: "#34d399", accent2: "#a3e635", palette: ["#34d399", "#a3e635", "#fbbf24", "#10b981", "#4ade80", "#f59e0b"], dark: true, cardBg: "#14352a", text: "#e9f7f0", text2: "#86b5a2", grid: "#23463a" },
        grape:    { themeId: "grape",    accent: "#a78bfa", accent2: "#f0abfc", palette: ["#a78bfa", "#f0abfc", "#818cf8", "#e879f9", "#c084fc", "#f472b6"], dark: true, cardBg: "#2a1750", text: "#f1e9ff", text2: "#b49be0", grid: "#4a2e85" },
        candy:    { themeId: "candy",    accent: "#f472b6", accent2: "#22d3ee", palette: ["#f472b6", "#22d3ee", "#fbbf24", "#fb7185", "#a78bfa", "#34d399"], dark: false, cardBg: "#ffffff", text: "#4a3a46", text2: "#a78b9e", grid: "#f0d5e2" },
        terminal: { themeId: "terminal", accent: "#00ff66", accent2: "#00d4aa", palette: ["#00ff66", "#00d4aa", "#ff5555", "#ffb86c", "#8be9fd", "#ff79c6"], dark: true, cardBg: "#151515", text: "#33ff33", text2: "#2e8b57", grid: "#262626" },
        aurora:   { themeId: "aurora",   accent: "#6366f1", accent2: "#10b981", palette: ["#6366f1", "#10b981", "#f59e0b", "#22d3ee", "#a78bfa", "#34d399"], dark: true, cardBg: "#1b2054", text: "#e0e7ff", text2: "#8fa3bf", grid: "#30387a" },
        coffee:   { themeId: "coffee",   accent: "#d9a05b", accent2: "#b87333", palette: ["#d9a05b", "#b87333", "#f97316", "#e0a96d", "#a0522d", "#f59e0b"], dark: true, cardBg: "#3a2217", text: "#f3e5d8", text2: "#c19a82", grid: "#5a3323" },
        rose:     { themeId: "rose",     accent: "#f43f5e", accent2: "#fbbf24", palette: ["#f43f5e", "#fbbf24", "#a855f7", "#fb7185", "#f472b6", "#e879f9"], dark: true, cardBg: "#3f1530", text: "#ffe4ef", text2: "#d18baa", grid: "#6a2547" },
        neon:     { themeId: "neon",     accent: "#00ffff", accent2: "#ff00ff", palette: ["#00ffff", "#ff00ff", "#39ff14", "#ffff00", "#ff6b00", "#00ff9d"], dark: true, cardBg: "#0e0e2a", text: "#e0ffff", text2: "#7df9ff", grid: "#1e1e4a" },
        emerald:  { themeId: "emerald",  accent: "#10b981", accent2: "#34d399", palette: ["#10b981", "#34d399", "#fbbf24", "#059669", "#6ee7b7", "#f59e0b"], dark: true, cardBg: "#0b3b2e", text: "#e7f6f0", text2: "#7fb8a3", grid: "#1b4a3a" },
        gold:     { themeId: "gold",     accent: "#f59e0b", accent2: "#fbbf24", palette: ["#f59e0b", "#fbbf24", "#f43f5e", "#d97706", "#fde68a", "#fb923c"], dark: true, cardBg: "#3e2a0e", text: "#fff7e6", text2: "#d4b483", grid: "#5c4520" },
        ice:      { themeId: "ice",      accent: "#0ea5e9", accent2: "#22d3ee", palette: ["#0ea5e9", "#22d3ee", "#6366f1", "#38bdf8", "#818cf8", "#06b6d4"], dark: false, cardBg: "#ffffff", text: "#0c4a6e", text2: "#64748b", grid: "#e0f2fe" },
        graphite: { themeId: "graphite", accent: "#94a3b8", accent2: "#64748b", palette: ["#94a3b8", "#64748b", "#38bdf8", "#cbd5e1", "#475569", "#22d3ee"], dark: true, cardBg: "#1a1d23", text: "#e5e7eb", text2: "#8b919c", grid: "#2a2e36" },
        lavender: { themeId: "lavender", accent: "#8b5cf6", accent2: "#a78bfa", palette: ["#8b5cf6", "#a78bfa", "#ec4899", "#6d28d9", "#c4b5fd", "#f472b6"], dark: false, cardBg: "#ffffff", text: "#3b2f63", text2: "#7c6fa8", grid: "#ede9fe" },
        peach:    { themeId: "peach",    accent: "#f97316", accent2: "#fb923c", palette: ["#f97316", "#fb923c", "#f43f5e", "#ea580c", "#fdba74", "#f87171"], dark: false, cardBg: "#ffffff", text: "#431407", text2: "#9a6b4f", grid: "#ffedd5" },
        cyber:    { themeId: "cyber",    accent: "#22d3ee", accent2: "#a855f7", palette: ["#22d3ee", "#a855f7", "#f0abfc", "#06b6d4", "#c084fc", "#34d399"], dark: true, cardBg: "#141433", text: "#e0f2fe", text2: "#8b8bc9", grid: "#26264f" },
        paper:    { themeId: "paper",    accent: "#334155", accent2: "#64748b", palette: ["#334155", "#64748b", "#0ea5e9", "#94a3b8", "#475569", "#38bdf8"], dark: false, cardBg: "#ffffff", text: "#1e293b", text2: "#64748b", grid: "#e5e7eb" },
        mint:     { themeId: "mint",     accent: "#10b981", accent2: "#34d399", palette: ["#10b981", "#34d399", "#0ea5e9", "#059669", "#6ee7b7", "#38bdf8"], dark: false, cardBg: "#ffffff", text: "#134e4a", text2: "#5b8a82", grid: "#ccfbf1" }
      };
      var t0 = T[String(style || "light")] || T.light;
      return {
        themeId: t0.themeId, themeMode: "full",
        accent: t0.accent, accent2: t0.accent2,
        palette: t0.palette.slice(),
        theme: {
          name: t0.themeId,
          accent: t0.accent, accent2: t0.accent2,
          primary: t0.accent, secondary: t0.accent2,
          cardBg: t0.cardBg,
          text: t0.text,
          textSecondary: t0.text2,
          gridLine: t0.grid,
          palette: t0.palette.slice(),
          gradient: [t0.accent, t0.accent2],
          background: t0.dark ? "#0f172a" : "#f8fafc"
        }
      };
    },

    // Build the on-canvas Design Brief text widget (PART 1 of the spec).
    _designBriefWidget(a, table) {
      function lbl(list, id) {
        for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
        return null;
      }
      var purpose = lbl(_DESIGN_META.purposes, a.purpose);
      var audience = lbl(_DESIGN_META.audiences, a.audience);
      var style = lbl(_DESIGN_META.styles, a.style);
      var kpiNames = (a.kpis || []).map(function (k) { var o = lbl(_DESIGN_META.kpis, k); return (o && o.label) || k; });
      if (a.prompt && kpiNames.indexOf(a.prompt) === -1) kpiNames.push(a.prompt);
      var rewrites = (a.rewrites && typeof a.rewrites === "object") ? a.rewrites : {};
      var purposeDesc = rewrites["purpose_" + a.purpose] || (purpose ? purpose.desc : "");
      var audienceDesc = rewrites["audience_" + a.audience] || (audience ? audience.desc : "");
      var content =
        "DESIGN BRIEF\n" +
        "Purpose · " + (purpose ? purpose.label + " — " + purposeDesc : "Dashboard") + "\n" +
        "Audience · " + (audience ? audience.label + " — " + audienceDesc : "Mixed") + "\n" +
        "Key questions · " + (kpiNames.length ? kpiNames.join(", ") : "High-level overview") + "\n" +
        "Layout · Header → KPI cards → Filters → Charts → Executive insights → Data details\n" +
        "Color strategy · " + (style ? style.label : "Light") + " — consistent, high-contrast, business palette\n" +
        "Interactions · Cross-filter slicers, hover tooltips, click-to-filter, drill-down, present mode";
      return {
        id: "w" + Date.now().toString(36) + "_brief",
        type: "text",
        title: "Design Brief",
        x: 24, y: 24, width: 1872, height: 150,
        zIndex: 2, locked: false, plain: true,
        config: {
          table: table,
          content: content,
          label: "Design Brief",
          plain: true,
          textFormat: "plain"
        }
      };
    },

    // Insert a widget straight below the header banner, pushing every widget
    // that currently sits under the header down so the canvas flows top-down.
    _insertAfterHeader(widgets, newWidget) {
      if (!widgets.length) { newWidget.y = 24; widgets.push(newWidget); return widgets; }
      var header = widgets[0];
      var gap = 16;
      var hBottom = (header.y || 24) + (header.height || 96);
      newWidget.x = header.x || 24;
      newWidget.width = newWidget.width || (header.width || 1872);
      newWidget.y = hBottom + gap;
      var delta = (newWidget.height || 150) + gap;
      for (var i = 1; i < widgets.length; i++) {
        var w = widgets[i];
        if (w.y != null && w.y >= hBottom) w.y = Math.round(w.y + delta);
      }
      widgets.splice(1, 0, newWidget);
      return widgets;
    },

    // Heuristic: a chart widget is a "trend" when its chartType is a line/area
    // family or its x/label column is a date/time column.
    _isTrendChart(w, table) {
      var ct = String((w.config || {}).chartType || w.type || "");
      if (["line", "area", "smoothline", "splinearea", "stepline", "stackedarea"].indexOf(ct) !== -1) return true;
      var label = String((w.config || {}).label || (w.config || {}).xCol || "");
      if (!label) return false;
      try {
        var cols = ((App.state.tables[table] || {}).columns) || [];
        for (var i = 0; i < cols.length; i++) {
          var c = cols[i];
          var cn = c.column_name || c.name || c;
          if (String(cn) === label) {
            var t = String(c.column_type || c.type || "").toLowerCase();
            return /date|time|timestamp/.test(t);
          }
        }
      } catch (e) { /* ignore */ }
      return false;
    },

    // Apply the Q5 section checklist to the AI-built widget list (mutates a
    // copy) and drop/append widgets accordingly. "brief" inserts the Design
    // Brief widget; positions are normalised by _insertAfterHeader.
    _applySections(widgets, answers, table, themeOpts) {
      var self = this;
      var sec = answers.sections || ["kpi", "filters", "trends", "breakdown", "table", "insights", "brief"];
      var has = function (id) { return sec.indexOf(id) !== -1; };
      if (!Array.isArray(widgets)) widgets = [];

      // KPI band
      if (!has("kpi")) widgets = widgets.filter(function (w) { return w.type !== "kpi"; });

      // Slicer / filter row
      if (!has("filters")) widgets = widgets.filter(function (w) {
        return ["slicer", "daterange", "numrange", "searchbox"].indexOf(w.type) === -1;
      });

      // Chart grid: keep trends and/or breakdowns based on the toggle.
      var skipCharts = !(has("trends") || has("breakdown"));
      var onlyTrend = has("trends") && !has("breakdown");
      var onlyBreakdown = has("breakdown") && !has("trends");
      if (skipCharts || onlyTrend || onlyBreakdown) {
        widgets = widgets.filter(function (w) {
          if (w.type !== "chart") return true;
          if (skipCharts) return false;
          return self._isTrendChart(w, table) === onlyTrend;
        });
      }

      // Executive insight panel
      if (!has("insights")) widgets = widgets.filter(function (w) { return w.title !== "AI Executive Insights"; });

      // Data table
      if (has("table") && !widgets.some(function (w) { return w.type === "table"; })) {
        widgets.push({
          id: "w" + Date.now().toString(36) + "_tbl",
          type: "table",
          title: "Data Details",
          x: 24, y: 24, width: 1872, height: 260, zIndex: 999, locked: false,
          config: { table: table, sql: 'SELECT * FROM "' + table + '" LIMIT 50', xCol: "", yCol: "" }
        });
      }

      // Design Brief (default on, toggleable)
      if (has("brief")) {
        var brief = this._designBriefWidget(answers, table);
        widgets = this._insertAfterHeader(widgets, brief);
      }

      // Widget-scoped theme: stamp the chosen style onto every widget card so
      // the canvas applies it to the widgets only — the page/app chrome stays
      // on its default look (the wizard no longer repaints the whole canvas).
      if (themeOpts && themeOpts.themeId) {
        var tt = themeOpts.theme || {};
        var tid = themeOpts.themeId;
        widgets.forEach(function (w) {
          if (!w || !w.config) return;
          var st = (w.config.style && typeof w.config.style === "object") ? w.config.style : {};
          w.config.style = Object.assign({}, st, {
            themeId: tid,
            cardBg: tt.cardBg, text: tt.text, textSecondary: tt.textSecondary,
            gridLine: tt.gridLine, accent: tt.accent
          });
        });
      }

      // Deterministic z-order
      widgets.forEach(function (w, i) { w.zIndex = (w.zIndex != null ? w.zIndex : i + 1); });
      return widgets;
    },

    // ── Give the AI-built dashboard a polished, generated title ──
    _aiTitle(prompt, tableName) {
      var _capWord = function (w) {
        if (/[A-Z]/.test(w)) return w;
        if (/^(ai|kpi|sql|crm|sku|roi|etl|hr|api|url|csv|hr|usa|uk)$/i.test(w)) return w.toUpperCase();
        return w.charAt(0).toUpperCase() + w.slice(1);
      };
      var p = (prompt || "").trim().replace(/\s+/g, " ");
      if (!p) {
        var base = String(tableName || "AI Dashboard").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
        base = base.split(" ").map(_capWord).join(" ");
        if (/overview|dashboard|report|summary|analysis$/i.test(base)) return base;
        return base + " Overview";
      }
      var s = p.length > 64 ? p.slice(0, 64).replace(/\s+\S*$/, "") + "…" : p;
      s = s.split(" ").map(_capWord).join(" ");
      return s.replace(/[.\s]+$/, "");
    },

    // ── Build an AI dashboard from a data-driven plan ──
    // The backend analyzes the full dataset, selects valid widgets, and
    // positions them in the professional Power BI band layout. The answers
    // from the design questionnaire drive the KPI focus, sections and theme.
    async _buildAIDashboard(size, prompt, answers) {
      var tableName = this._selectedTable.table_name || this._selectedTable.name;
      answers = answers || this._defaultAnswers();

      var focused = (prompt || "").trim();
      var extra = this._aiPromptFromAnswers(answers);
      var briefPrompt = focused && extra ? focused + ". " + extra : (focused || extra || "");

      var aiTitle = this._aiTitle(answers.prompt || focused, tableName);
      _saveHistory({ type: "ai", table: tableName, time: Date.now() });

      if (App.info) App.info("Analyzing data and building your dashboard...");

      var themeOpts = this._studioThemeOpts(answers.style);
      var canvasW = (size && size.w) || 1900;
      var canvasH = (size && size.h) || 1080;

      // 1. Ask the backend to analyze the data and produce a validated plan.
      var plan = null;
      var used = "auto";
      try {
        var res = await API.autoBuildPlan(tableName, {
          purpose: answers.purpose, audience: "executive",
          style: answers.style,
          kpiFocus: Array.isArray(answers.kpis) ? answers.kpis.map(function (k) { return typeof k === "string" ? k : (k.id || k); }) : [],
          prompt: briefPrompt,
          sections: Array.isArray(answers.sections) ? answers.sections : []
        });
        if (res && res.success) {
          plan = res;
          used = res.used || "auto";
        } else if (res && res.error) {
          if (App.error) App.error("AI could not build this dashboard: " + res.error);
          return;
        }
      } catch (e) {
        if (App.error) App.error("AI dashboard build failed. Is the server running? " + (e && e.message ? e.message : ""));
        return;
      }

      if (!plan || !Array.isArray(plan.zones) || !plan.zones.length) {
        if (App.info) App.info("No valid widgets could be generated for this table. Opening a blank canvas.");
        var ctx0 = {
          table: tableName,
          name: aiTitle,
          widgets: [],
          canvasW: canvasW, canvasH: canvasH, grid: 12,
          fixedCanvas: true,
          sharePublic: !!answers.sharePublic
        };
        sessionStorage.setItem("blankdash_context", JSON.stringify(ctx0));
        App.navigate("#blankdash");
        return;
      }

      // 2. Compose the professional Power BI band layout from the validated plan.
      //    Single page → curated big panels fitted to the canvas size.
      //    Multi page → page 1 overview + focused follow-up pages (each page keeps
      //    its own header, KPI band + slicers and renders fewer, larger widgets).
      var opts = {
        canvasW: canvasW,
        canvasH: canvasH,
        title: aiTitle,
        subtitle: "Data source: " + tableName + " · AI data-driven layout",
        chartH: 300,
        insightH: 260,
        theme: themeOpts.theme,
        colors: themeOpts.palette,
        accent: themeOpts.accent,
        accent2: themeOpts.accent2
      };
      var pageCount = Math.max(1, parseInt(answers.pages, 10) || 1);

      var pageItems = null;   // multi-page list [{id,name,widgets}]
      var widgets = null;     // single-page widget list
      if (pageCount > 1) {
        var pagesRes = AIBuild.buildDashboardFromPlanPages(plan, opts, {
          pages: pageCount,
          pageFocus: answers.pageFocus || ["overview"],
          pagePrompt: answers.pagePrompt || []
        });
        pageItems = (Array.isArray(pagesRes.pages) ? pagesRes.pages : []).map(function (pg) {
          return { id: pg.id, name: pg.name, widgets: pg.widgets || [] };
        });
        if (!pageItems.length) { App.error("Could not generate the multi-page dashboard"); return; }
      } else {
        widgets = AIBuild.buildDashboardFromPlan(plan, opts).widgets.concat([]);
      }

      // 3. Apply section toggles on top of the plan (filters / insights), the
      //    widget-type picker (only selected widget types stay), and
      //    widget-scoped theme stamping (keeps the page chrome untouched).
      function applySections(widgets) {
        var sec = answers.sections || [];
        if (Array.isArray(sec) && sec.indexOf("filters") === -1) {
          widgets = widgets.filter(function (w) { return ["slicer", "daterange", "numrange", "searchbox"].indexOf(w.type) === -1; });
        }
        if (Array.isArray(sec) && sec.indexOf("insights") === -1) {
          widgets = widgets.filter(function (w) { return w.title !== "AI Executive Insights"; });
        }
        return widgets;
      }
      // Keep a widget only when its type matches the user's multi-select picker.
      // Empty selection = auto (keep everything). The page header, dividers and
      // the page-navigator pill strip are layout chrome and always stay.
      function keepWidget(w) {
        if (!w || !w.type) return false;
        if (w.type === "divider") return true;
        if (w.type === "custom" && (w.locked === true || w.title === "Page Navigator")) return true;
        var sel = answers.widgets;
        if (!Array.isArray(sel) || !sel.length) return true;
        var key = w.type === "chart" ? ((w.config && w.config.chartType) || "bar") : w.type;
        return sel.indexOf(key) !== -1;
      }
      function filterWidgets(widgets) { return (widgets || []).filter(keepWidget); }
      function stampTheme(widgets) {
        if (!themeOpts || !themeOpts.themeId) return widgets;
        var tt = themeOpts.theme || {};
        var tid = themeOpts.themeId;
        widgets.forEach(function (w) {
          if (!w || !w.config) return;
          var st = (w.config.style && typeof w.config.style === "object") ? w.config.style : {};
          w.config.style = Object.assign({}, st, {
            themeId: tid, cardBg: tt.cardBg, text: tt.text,
            textSecondary: tt.textSecondary, gridLine: tt.gridLine, accent: tt.accent
          });
        });
        return widgets;
      }
      // Insert the on-canvas Design Brief (rewritten purpose/audience
      // descriptions included) right below the header on the first page.
      if (pageItems) {
        pageItems.forEach(function (pg) { pg.widgets = filterWidgets(stampTheme(applySections(pg.widgets))); });
      } else {
        widgets = filterWidgets(stampTheme(applySections(widgets)));
      }

      var totalWidgets = pageItems ? pageItems.reduce(function (n, pg) { return n + (pg.widgets || []).length; }, 0) : widgets.length;
      var context = {
        table: tableName,
        name: aiTitle,
        canvasW: canvasW, canvasH: canvasH, grid: 12,
        fixedCanvas: true,
        sharePublic: !!answers.sharePublic
      };
      if (pageItems) {
        context.pages = pageItems;
        context.currentPage = 0;
      } else {
        context.widgets = widgets;
      }
      sessionStorage.setItem("blankdash_context", JSON.stringify(context));
      var lastHist = _history[_history.length - 1];
      if (lastHist && lastHist.type === "ai") {
        lastHist.detail = (answers.prompt || briefPrompt || "Auto layout") + " · " + totalWidgets + (pageItems ? " widget(s) across " + pageItems.length + " pages" : " widget(s)") + " · " + (used === "llm" ? "LLM" : "Auto");
        _persistHistory();
      }
      App.navigate("#blankdash");
      if (used === "llm") {
        if (App.success) App.success("AI built " + totalWidgets + " widget(s)" + (pageItems ? " across " + pageItems.length + " pages" : ""));
      } else if (App.info) {
        App.info("AI narrative unavailable — showing automatic widget selection based on your data");
      }
    },

    // ── Recreate a dashboard from a screenshot ──
    _recreateFromImage() {
      if (!this._selectedTable) {
        App.warning("Please select a data source first");
        return;
      }
      var inputFile = document.createElement("input");
      inputFile.type = "file";
      inputFile.accept = "image/*";
      inputFile.style.display = "none";
      document.body.appendChild(inputFile);
      var self = this;
      inputFile.addEventListener("change", function () {
        var f = inputFile.files && inputFile.files[0];
        inputFile.remove();
        if (!f) return;
        self._analyzeImageFile(f);
      });
      inputFile.click();
    },

    _analyzeImageFile(file) {
      var self = this;
      if (file.size > 8 * 1024 * 1024) {
        App.warning("Image is larger than 8MB — upload a smaller screenshot.");
        return;
      }
      this._downscaleImage(file, function (blob) {
        self._showAnalyzingModal();
        API.analyzeImage(blob).then(function (res) {
          self._hideAnalyzingModal();
          if (!res || !res.success) {
            self._showImageError((res && res.message) || "Could not analyze the image.", file);
            return;
          }
          self._lastImageSha = res.sha || "";
          self._showConfirmModal(res, file);
        }).catch(function (e) {
          self._hideAnalyzingModal();
          self._showImageError((e && e.message) || "Vision analysis failed.", file);
        });
      });
    },

    // Downscale large screenshots client-side so the vision model gets a
    // manageable payload (and the upload stays under the size limit). 2200px
    // keeps chart titles legible for dense dashboards.
    _downscaleImage(file, cb) {
      var MAX = 2200;
      var img = new Image();
      var url = URL.createObjectURL(file);
      img.onload = function () {
        URL.revokeObjectURL(url);
        var w = img.naturalWidth, h = img.naturalHeight;
        var scale = Math.min(1, MAX / Math.max(w, h));
        if (scale >= 1) { cb(file); return; }
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(w * scale);
        canvas.height = Math.round(h * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) { cb(blob || file); }, "image/jpeg", 0.92);
      };
      img.onerror = function () { URL.revokeObjectURL(url); cb(file); };
      img.src = url;
    },

    _showAnalyzingModal() {
      this._hideAnalyzingModal();
      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.id = "ds-analyzing-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal" style="max-width:420px;text-align:center">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-image"></i> Recreate from Image</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button></div>' +
          '<div style="padding:26px 24px">' +
            '<div style="width:46px;height:46px;margin:0 auto 14px;border:3px solid #e2e8f0;border-top-color:#7B61FF;border-radius:50%;animation:spin 0.9s linear infinite"></div>' +
            '<div style="font-size:13px;font-weight:700">Reading your dashboard image…</div>' +
            '<div id="ds-analyze-step" style="font-size:11.5px;color:var(--color-content-text-secondary,#64748b);margin-top:6px">Detecting layout, colors and widgets</div>' +
          '</div>' +
        '</div>';
      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); }
      });
      document.body.appendChild(ov);
      var steps = ["Detecting layout, colors and widgets", "Extracting chart titles and families", "Writing the design brief"];
      var i = 0;
      var el = ov.querySelector("#ds-analyze-step");
      ov._stepTimer = setInterval(function () {
        i = (i + 1) % steps.length;
        if (el) el.textContent = steps[i];
      }, 2500);
    },

    _hideAnalyzingModal() {
      var ov = document.getElementById("ds-analyzing-overlay");
      if (ov) {
        if (ov._stepTimer) clearInterval(ov._stepTimer);
        ov.remove();
      }
    },

    _showImageError(msg, file) {
      var self = this;
      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal" style="max-width:440px">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-triangle-exclamation" style="color:#ef4444"></i> Could not read the image</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button></div>' +
          '<div style="padding:14px 18px;font-size:12.5px;color:var(--color-content-text-secondary,#64748b)">' + _esc(msg) + '</div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid var(--color-card-border,#e2e8f0)">' +
            '<button type="button" class="btn btn-sm btn-secondary" data-close="1">Cancel</button>' +
            '<button type="button" class="btn btn-sm btn-secondary" data-retry="1"><i class="fas fa-rotate-right"></i> Retry</button>' +
            '<button type="button" class="btn btn-sm btn-primary" data-fallback="1"><i class="fas fa-robot"></i> Use AI Auto-Build instead</button>' +
          '</div>' +
        '</div>';
      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); return; }
        if (e.target.closest("[data-retry]")) { ov.remove(); self._analyzeImageFile(file); return; }
        if (e.target.closest("[data-fallback]")) { ov.remove(); self._aiBuild(); return; }
      });
      document.body.appendChild(ov);
    },

    // Editable confirm modal — everything pre-filled from the vision spec.
    _showConfirmModal(res, file) {
      var self = this;
      var spec = res.spec || {};
      var pages = Array.isArray(spec.pages) ? spec.pages : [];
      var widgets = pages.length ? (pages[0].widgets || []) : [];
      var tableName = this._selectedTable.table_name || this._selectedTable.name;
      var hdr = (spec.header && spec.header.present) ? spec.header : null;
      var defaultTitle = (hdr && hdr.title) || ("Recreated from Image — " + tableName);

      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal" style="max-width:720px;max-height:86vh;display:flex;flex-direction:column">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-image" style="color:#7B61FF;margin-right:6px"></i> Recreate from Image</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button></div>' +
          '<div style="padding:12px 18px;font-size:11.5px;color:var(--color-content-text-secondary,#64748b)">' +
            'We read <b>' + widgets.length + ' widget(s)</b> from your screenshot. Everything is pre-filled — just hit <b>Build it</b>.' +
            (res.looksLikeDashboard === false ? ' <span style="color:#d97706">This image does not look like a dashboard — results may be rough.</span>' : '') +
          '</div>' +
          '<div style="padding:0 18px;overflow:auto;flex:1">' +
            '<div style="margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Dashboard title</label>' +
              '<input type="text" class="form-input" id="ds-img-title" value="' + _esc(defaultTitle) + '"></div>' +
            '<div style="display:flex;gap:12px">' +
              '<div style="flex:1;margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Theme</label>' +
                '<select class="form-input" id="ds-img-theme">' +
                  '<option value="light"' + (String(spec.theme) !== "dark" ? " selected" : "") + '>Light</option>' +
                  '<option value="dark"' + (String(spec.theme) === "dark" ? " selected" : "") + '>Dark</option>' +
                '</select></div>' +
              '<div style="flex:1;margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Accent color</label>' +
                '<input type="color" class="form-input" id="ds-img-accent" value="' + _esc(spec.accent || "#2563eb") + '" style="height:38px;padding:2px"></div>' +
              '<div style="flex:1;margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Background</label>' +
                '<input type="color" class="form-input" id="ds-img-bg" value="' + _esc(spec.background || "#f8fafc") + '" style="height:38px;padding:2px"></div>' +
            '</div>' +
            '<div style="margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Widgets <span style="font-weight:400">(uncheck to skip)</span></label>' +
              '<div id="ds-img-widgets"></div>' +
            '</div>' +
            (res.prompt ? '<div style="margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Design brief</label>' +
              '<div style="font-size:12px;color:var(--color-content-text-secondary,#64748b);background:#f8fafc;border:1px solid var(--color-card-border,#e2e8f0);border-radius:8px;padding:8px 10px;margin-top:4px">' + _esc(res.prompt) + '</div></div>' : '') +
          '</div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid var(--color-card-border,#e2e8f0)">' +
            '<button type="button" class="btn btn-sm btn-secondary" data-close="1">Cancel</button>' +
            '<button type="button" class="btn btn-sm btn-primary" data-build="1" id="ds-img-build"><i class="fas fa-wand-magic-sparkles"></i> Build it</button>' +
          '</div>' +
        '</div>';

      var wl = ov.querySelector("#ds-img-widgets");
      var SWAP = { bar: 1, line: 1, area: 1, donut: 1, pie: 1, column: 1, horizontalbar: 1, stackedbar: 1 };
      var FAM_LABEL = { kpi: "KPI card", bar: "Bar", line: "Line", area: "Area", pie: "Pie", donut: "Donut", table: "Table", gauge: "Gauge", treemap: "Treemap", funnel: "Funnel", scatter: "Scatter", heatmap: "Heatmap", map: "Map", slicer: "Slicer", text: "Text" };
      var CHART_OPTS = ["bar", "column", "line", "area", "donut", "pie"];
      widgets.forEach(function (w, i) {
        var fam = String(w.family || "chart").toLowerCase();
        var label = FAM_LABEL[fam] || fam;
        var swapSel = "";
        if (SWAP[fam]) {
          swapSel = '<select class="form-input" data-idx="' + i + '" style="width:110px;height:30px;font-size:11px;padding:2px 6px">' +
            CHART_OPTS.map(function (c) { return '<option value="' + c + '"' + (c === fam ? " selected" : "") + '>' + c + "</option>"; }).join("") +
            "</select>";
        }
        var row = document.createElement("div");
        row.style.cssText = "display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--color-card-border,#e2e8f0)";
        row.innerHTML =
          '<label style="display:flex;align-items:center;gap:8px;flex:1;min-width:0;cursor:pointer">' +
            '<input type="checkbox" data-idx="' + i + '" checked>' +
            '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">' + _esc(w.title || ("Widget " + (i + 1))) + "</span>" +
          "</label>" +
          '<span style="font-size:10px;color:var(--color-content-text-secondary,#64748b);background:#f1f5f9;border-radius:6px;padding:2px 6px;white-space:nowrap">' + label + "</span>" +
          swapSel;
        wl.appendChild(row);
      });
      if (!widgets.length) {
        wl.innerHTML = '<div style="font-size:12px;color:var(--color-content-text-secondary,#64748b);padding:8px 0">No widgets were detected in this image.</div>';
      }

      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); return; }
        if (e.target.closest("[data-build]")) {
          var btn = ov.querySelector("#ds-img-build");
          if (btn.disabled) return;
          btn.disabled = true;
          btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Building…';
          var edited = self._specFromModal(ov, spec);
          ov.remove();
          self._buildFromImage(file, edited);
        }
      });
      document.body.appendChild(ov);
    },

    _specFromModal(ov, spec) {
      var title = (ov.querySelector("#ds-img-title") || {}).value || "";
      var theme = (ov.querySelector("#ds-img-theme") || {}).value || "light";
      var accent = (ov.querySelector("#ds-img-accent") || {}).value || "";
      var background = (ov.querySelector("#ds-img-bg") || {}).value || "";
      var keepIdx = [];
      var swaps = {};
      ov.querySelectorAll("#ds-img-widgets input[type=checkbox]").forEach(function (cb) {
        if (cb.checked) keepIdx.push(parseInt(cb.getAttribute("data-idx"), 10));
      });
      ov.querySelectorAll("#ds-img-widgets select").forEach(function (sel) {
        var idx = parseInt(sel.getAttribute("data-idx"), 10);
        if (sel.value) swaps[idx] = sel.value;
      });
      var pages = Array.isArray(spec.pages) ? spec.pages : [];
      var widgets = pages.length ? (pages[0].widgets || []) : [];
      var kept = widgets.filter(function (_, i) { return keepIdx.indexOf(i) !== -1; });
      var edited = Object.assign({}, spec, {
        theme: theme,
        accent: accent,
        background: background,
        pages: [{ name: (pages[0] && pages[0].name) || "Page 1", widgets: kept }]
      });
      if (Object.keys(swaps).length) edited.chartSwaps = swaps;
      return { title: title, spec: edited };
    },

    async _buildFromImage(file, edited) {
      var self = this;
      var tableName = this._selectedTable.table_name || this._selectedTable.name;
      var title = edited.title || ("Recreated from Image — " + tableName);
      if (App.info) App.info("Building your dashboard from the image…");
      try {
        var res = await API.buildFromImage(file, {
          table: tableName,
          title: title,
          specSha: this._lastImageSha || "",
          specJson: JSON.stringify(edited.spec)
        });
        if (!res || !res.success) {
          if (res && res.error_code === "image_changed") {
            App.warning("The image changed since it was analyzed — please re-upload it.");
            return;
          }
          if (App.error) App.error((res && res.message) || (res && res.error) || "Could not build the dashboard.");
          return;
        }
        var plan = res.plan;
        var hasContent = plan && (
          (Array.isArray(plan.zones) && plan.zones.length) ||
          (Array.isArray(plan.kpis) && plan.kpis.length) ||
          (Array.isArray(plan.tables) && plan.tables.length) ||
          (Array.isArray(plan.texts) && plan.texts.length) ||
          (Array.isArray(plan.placeholders) && plan.placeholders.length) ||
          (Array.isArray(plan.filters) && plan.filters.length)
        );
        if (!hasContent) {
          if (App.info) App.info("No valid widgets could be generated for this table. Opening a blank canvas.");
          var ctx0 = { table: tableName, name: title, widgets: [], canvasW: 1900, canvasH: 1080, grid: 12, fixedCanvas: true };
          sessionStorage.setItem("blankdash_context", JSON.stringify(ctx0));
          App.navigate("#blankdash");
          return;
        }
        self._renderImagePlan(plan, title, tableName);
      } catch (e) {
        if (App.error) App.error("Build failed: " + (e && e.message ? e.message : ""));
      }
    },

    // Build a themeOpts-like object from the colors the vision model extracted.
    _themeOptsFromSpec(spec) {
      var dark = String(spec.theme || "light") === "dark";
      var accent = spec.accent || (dark ? "#818cf8" : "#2563eb");
      var background = spec.background || (dark ? "#0f172a" : "#f8fafc");
      var cardBg = spec.cardBg || (dark ? "#252540" : "#ffffff");
      var text = spec.textColor || (dark ? "#e0e0e0" : "#0f172a");
      var palette = (Array.isArray(spec.palette) && spec.palette.length)
        ? spec.palette.slice(0, 6)
        : (dark ? ["#818cf8", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#22d3ee"]
                : ["#2563eb", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"]);
      return {
        themeId: dark ? "dark" : "light",
        themeMode: "full",
        accent: accent,
        accent2: accent,
        palette: palette,
        theme: {
          name: dark ? "dark" : "light",
          accent: accent, accent2: accent,
          primary: accent, secondary: accent,
          cardBg: cardBg,
          text: text,
          textSecondary: dark ? "#99a1b9" : "#64748b",
          gridLine: dark ? "#3a3a5c" : "#e2e8f0",
          palette: palette,
          gradient: [accent, accent],
          background: background
        }
      };
    },

    // Register the image-extracted colors as a real user theme (localStorage
    // bd_user_themes) so the whole canvas chrome — background, cards, text —
    // matches the screenshot. Replaces any previous "From Image" theme.
    _registerImageTheme(themeOpts) {
      var t = themeOpts.theme || {};
      var id = "from_image";
      var theme = {
        id: id,
        name: "From Image",
        primary: t.primary || themeOpts.accent,
        secondary: t.secondary || themeOpts.accent,
        accent: themeOpts.accent,
        palette: (themeOpts.palette || []).slice(),
        gradient: [themeOpts.accent, themeOpts.accent2 || themeOpts.accent],
        background: t.background || "#f8fafc",
        cardBg: t.cardBg || "#ffffff",
        text: t.text || "#0f172a",
        textSecondary: t.textSecondary || "#64748b",
        gridLine: t.gridLine || "#e2e8f0",
        axisColor: t.gridLine || "#e2e8f0",
        uploaded: true
      };
      try {
        var list = JSON.parse(localStorage.getItem("bd_user_themes") || "[]");
        list = list.filter(function (u) { return u && u.id !== "from_image"; });
        list.unshift(theme);
        localStorage.setItem("bd_user_themes", JSON.stringify(list));
      } catch (e) {}
      return id;
    },

    // Render the validated plan from build-from-image into the blankdash canvas.
    // Widgets are placed at the EXACT bounding boxes the vision model extracted
    // from the screenshot (plan.canvas mirrors the image's aspect ratio), so the
    // rebuilt dashboard reproduces the original layout instead of a generic grid.
    _renderImagePlan(plan, title, tableName) {
      var spec = plan.spec || {};
      var themeOpts = this._themeOptsFromSpec(spec);
      var themeId = this._registerImageTheme(themeOpts);
      var canvas = plan.canvas || { w: 1900, h: 1080 };
      var cw = Math.max(800, Math.round(canvas.w) || 1900);
      var ch = Math.max(450, Math.round(canvas.h) || 1080);
      var palette = themeOpts.palette;
      var widgets = [];
      var z = 2;

      // Header band — only when the image actually has one, at its exact box
      // and styled with the colors extracted from the image.
      var hdr = (spec.header && spec.header.present) ? spec.header : null;
      if (hdr) {
        var hb = { x: hdr.x || 0, y: hdr.y || 0, w: hdr.w || 100, h: hdr.h || 8 };
        widgets.push(AIBuild.headerWidget(tableName, title,
          "Data source: " + tableName + " · Recreated from image",
          {
            x: Math.round((hb.x / 100) * cw), y: Math.round((hb.y / 100) * ch),
            width: Math.round((hb.w / 100) * cw), height: Math.max(40, Math.round((hb.h / 100) * ch)),
            index: z++, accent: themeOpts.accent, accent2: themeOpts.accent2,
            branding: false,
            bgStart: hdr.background || "",
            bgEnd: hdr.background || ""
          }));
      }

      // Filters / slicers at their exact boxes.
      (plan.filters || []).forEach(function (f, i) {
        var box = f.box || {};
        var x = box.x != null ? box.x : 24;
        var y = box.y != null ? box.y : 60;
        var w = box.w || 300;
        var h = box.h || 60;
        var isDate = String(f.type || "") === "daterange";
        widgets.push({
          id: "flt" + i + "_" + Date.now(),
          type: isDate ? "daterange" : "slicer",
          title: f.label || f.field,
          x: x, y: y, width: w, height: h, zIndex: z++, locked: false,
          config: { table: tableName, sql: f.sql, filterField: f.field,
            label: f.label || f.field, slicerStyle: "dropdown", compact: true }
        });
      });

      // Text widgets at their exact boxes with the extracted content.
      (plan.texts || []).forEach(function (tx, i) {
        var box = tx.box || {};
        widgets.push({
          id: "txt" + i + "_" + Date.now(),
          type: "text",
          title: tx.title || "Text",
          x: box.x != null ? box.x : 24,
          y: box.y != null ? box.y : 24,
          width: box.w || 300,
          height: box.h || 60,
          zIndex: z++, locked: false,
          config: { table: tableName, sql: "", label: tx.title || "Text",
            content: tx.content || tx.title || "" }
        });
      });

      // Data tables at their exact boxes.
      (plan.tables || []).forEach(function (tb, i) {
        var box = tb.box || {};
        widgets.push({
          id: "tbl" + i + "_" + Date.now(),
          type: "table",
          title: tb.title || "Data Table",
          x: box.x != null ? box.x : 24,
          y: box.y != null ? box.y : 24,
          width: box.w || 600,
          height: box.h || 300,
          zIndex: z++, locked: false,
          config: { table: tableName, sql: tb.sql, label: tb.title || "Data Table" }
        });
      });

      // KPI cards at their exact boxes.
      (plan.kpis || []).forEach(function (k, i) {
        var box = k.box || {};
        widgets.push(AIBuild.kpiWidgetFromPlan(k, tableName, {
          x: box.x != null ? box.x : 24,
          y: box.y != null ? box.y : 120,
          width: box.w || 300,
          height: box.h || 120,
          index: z++, colors: palette
        }));
      });

      // Charts at their exact boxes.
      (plan.zones || []).forEach(function (zn, i) {
        var box = zn.box || {};
        widgets.push(AIBuild.chartWidgetFromPlan(zn, tableName, {
          x: box.x != null ? box.x : 24,
          y: box.y != null ? box.y : 240,
          width: box.w || 600,
          height: box.h || 300,
          index: z++, colors: palette
        }));
      });

      // Sized placeholders for widgets that could not be matched to data —
      // they keep the image's layout intact and explain what is missing.
      (plan.placeholders || []).forEach(function (ph, i) {
        var box = ph.box || {};
        var famLabel = String(ph.family || "chart");
        var note = ph.note ? " — " + ph.note : "";
        widgets.push({
          id: "ph" + i + "_" + Date.now(),
          type: "text",
          title: ph.title || "Widget",
          x: box.x != null ? box.x : 24,
          y: box.y != null ? box.y : 24,
          width: box.w || 300,
          height: box.h || 120,
          zIndex: z++, locked: false,
          config: { table: tableName, sql: "", label: ph.title || "Widget",
            content: (ph.title || "Widget") + "\n(" + famLabel + " visual" + note + " — no matching data found)" }
        });
      });

      widgets.forEach(function (w) {
        if (!w || !w.config) return;
        var st = (w.config.style && typeof w.config.style === "object") ? w.config.style : {};
        w.config.style = Object.assign({}, st, {
          themeId: themeId, cardBg: themeOpts.theme.cardBg,
          text: themeOpts.theme.text, textSecondary: themeOpts.theme.textSecondary,
          gridLine: themeOpts.theme.gridLine, accent: themeOpts.accent
        });
      });
      var context = {
        table: tableName,
        name: title,
        canvasW: cw, canvasH: ch, grid: 12,
        fixedCanvas: true,
        theme: themeId, themeMode: "full",
        widgets: widgets
      };
      sessionStorage.setItem("blankdash_context", JSON.stringify(context));
      App.navigate("#blankdash");
      if (App.success) App.success("Recreated " + widgets.length + " widget(s) from your image");
    },

    _openSQL() {
      App.navigate("#sql");
    },

    _importDash() {
      var self = this;
      var inputFile = document.createElement("input");
      inputFile.type = "file";
      inputFile.accept = ".json,.pbix,.pbit,application/json";
      inputFile.style.display = "none";
      document.body.appendChild(inputFile);
      inputFile.addEventListener("change", function () {
        var f = inputFile.files && inputFile.files[0];
        inputFile.remove();
        if (!f) return;
        var lname = String(f.name || "").toLowerCase();
        if (/\.(pbix|pbit)$/.test(lname)) {
          self._analyzePbixFile(f);
          return;
        }
        var reader = new FileReader();
        reader.onload = function () {
          try {
            var data = JSON.parse(String(reader.result || ""));
            self._importFromData(data);
          } catch (e) {
            App.warning("Could not parse that file: " + (e && e.message ? e.message : "invalid JSON"));
          }
        };
        reader.readAsText(f);
      });
      inputFile.click();
    },

    // ── PBIX import: parse → confirm → build replica on selected table ──
    _analyzePbixFile(file) {
      var self = this;
      if (file.size > 30 * 1024 * 1024) {
        App.warning("Power BI files are limited to 30MB — upload a smaller file.");
        return;
      }
      this._showPbixAnalyzingModal();
      API.importPbix(file).then(function (res) {
        self._hidePbixAnalyzingModal();
        if (!res || !res.success) {
          self._showPbixError((res && res.message) || "Could not read this Power BI file.", file);
          return;
        }
        if (!self._selectedTable) {
          App.warning("Select a data source first — the replica will rebuild on your data.");
          return;
        }
        self._showPbixConfirmModal(res);
      }).catch(function (e) {
        self._hidePbixAnalyzingModal();
        self._showPbixError((e && e.message) || "Upload failed.", file);
      });
    },

    _showPbixAnalyzingModal() {
      this._hidePbixAnalyzingModal();
      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.id = "ds-pbix-analyzing-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal" style="max-width:420px;text-align:center">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-file-import"></i> Import Power BI</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button></div>' +
          '<div style="padding:26px 24px">' +
            '<div style="width:46px;height:46px;margin:0 auto 14px;border:3px solid #e2e8f0;border-top-color:#f97316;border-radius:50%;animation:spin 0.9s linear infinite"></div>' +
            '<div style="font-size:13px;font-weight:700">Reading your Power BI report…</div>' +
            '<div id="ds-pbix-step" style="font-size:11.5px;color:var(--color-content-text-secondary,#64748b);margin-top:6px">Extracting pages, visuals and theme</div>' +
          '</div>' +
        '</div>';
      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); }
      });
      document.body.appendChild(ov);
      var steps = ["Extracting pages, visuals and theme", "Mapping visual types to widgets", "Preparing the replica build"];
      var i = 0;
      var el = ov.querySelector("#ds-pbix-step");
      ov._stepTimer = setInterval(function () {
        i = (i + 1) % steps.length;
        if (el) el.textContent = steps[i];
      }, 2000);
    },

    _hidePbixAnalyzingModal() {
      var ov = document.getElementById("ds-pbix-analyzing-overlay");
      if (ov) {
        if (ov._stepTimer) clearInterval(ov._stepTimer);
        ov.remove();
      }
    },

    _showPbixError(msg, file) {
      var self = this;
      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal" style="max-width:440px">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-triangle-exclamation" style="color:#ef4444"></i> Could not read the Power BI file</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button></div>' +
          '<div style="padding:14px 18px;font-size:12.5px;color:var(--color-content-text-secondary,#64748b)">' + _esc(msg) + '</div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid var(--color-card-border,#e2e8f0)">' +
            '<button type="button" class="btn btn-sm btn-secondary" data-close="1">Cancel</button>' +
            '<button type="button" class="btn btn-sm btn-secondary" data-retry="1"><i class="fas fa-rotate-right"></i> Retry</button>' +
            '<button type="button" class="btn btn-sm btn-primary" data-fallback="1"><i class="fas fa-robot"></i> Use AI Auto-Build instead</button>' +
          '</div>' +
        '</div>';
      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); return; }
        if (e.target.closest("[data-retry]")) { ov.remove(); self._analyzePbixFile(file); return; }
        if (e.target.closest("[data-fallback]")) { ov.remove(); self._aiBuild(); return; }
      });
      document.body.appendChild(ov);
    },

    _pbixFamLabel(fam) {
      var L = { kpi: "KPI card", column: "Column chart", bar: "Bar chart", line: "Line chart", area: "Area chart",
        pie: "Pie", donut: "Donut", gauge: "Gauge", table: "Table", scatter: "Scatter", funnel: "Funnel",
        slicer: "Slicer", text: "Text", map: "Map", placeholder: "Visual" };
      return L[String(fam || "").toLowerCase()] || String(fam || "Visual");
    },

    // Editable confirm modal — pre-filled from the deterministic PBIX parse.
    _showPbixConfirmModal(res) {
      var self = this;
      var spec = res.spec || {};
      var pages = Array.isArray(spec.pages) ? spec.pages : [];
      var meta = spec._meta || {};
      var defaultTitle = ((spec.header && spec.header.title) ||
        String(res.fileName || "Imported Power BI").replace(/\.(pbix|pbit)$/i, "") || "Imported Power BI");

      var ov = document.createElement("div");
      ov.className = "bd-gal-overlay bd-tpl-overlay";
      ov.innerHTML =
        '<div class="bd-gal-modal" style="max-width:680px;max-height:86vh;display:flex;flex-direction:column">' +
          '<div class="bd-gal-head"><h3 style="margin:0;font-size:16px;font-weight:700;"><i class="fas fa-file-import" style="color:#f97316;margin-right:6px"></i> Import Power BI Dashboard</h3>' +
            '<button type="button" class="btn btn-sm btn-ghost" data-close="1">&times;</button></div>' +
          '<div style="padding:12px 18px;font-size:11.5px;color:var(--color-content-text-secondary,#64748b)">' +
            'Read <b>' + (meta.pageCount || pages.length) + ' page(s)</b> and <b>' + (meta.widgetCount || 0) + ' visual(s)</b> from <b>' + _esc(res.fileName || "report.pbix") + '</b>.' +
            ' The layout is replicated exactly and rebuilt on <b>' + _esc(((this._selectedTable || {}).table_name || (this._selectedTable || {}).name) || "") + '</b>.' +
          '</div>' +
          '<div style="padding:0 18px;overflow:auto;flex:1">' +
            '<div style="margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Dashboard title</label>' +
              '<input type="text" class="form-input" id="ds-pbix-title" value="' + _esc(defaultTitle) + '"></div>' +
            '<div style="display:flex;gap:12px">' +
              '<div style="flex:1;margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Theme</label>' +
                '<select class="form-input" id="ds-pbix-theme">' +
                  '<option value="light"' + (String(spec.theme) !== "dark" ? " selected" : "") + '>Light</option>' +
                  '<option value="dark"' + (String(spec.theme) === "dark" ? " selected" : "") + '>Dark</option>' +
                '</select></div>' +
              '<div style="flex:1;margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Accent color</label>' +
                '<input type="color" class="form-input" id="ds-pbix-accent" value="' + _esc(spec.accent || "#2563eb") + '" style="height:38px;padding:2px"></div>' +
              '<div style="flex:1;margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Background</label>' +
                '<input type="color" class="form-input" id="ds-pbix-bg" value="' + _esc(spec.background || "#f8fafc") + '" style="height:38px;padding:2px"></div>' +
            '</div>' +
            '<label style="display:flex;align-items:flex-start;gap:8px;margin:12px 0 8px;padding:9px 10px;border:1px solid var(--color-card-border,#e2e8f0);border-radius:8px;cursor:pointer;background:var(--color-card-bg,#fff)">' +
              '<input type="checkbox" id="ds-pbix-ai" checked style="margin-top:2px">' +
              '<span style="font-size:11.5px;line-height:1.45"><b>AI smart-mapping</b><span style="display:block;color:var(--color-content-text-secondary,#64748b);font-size:10.5px">The AI binds each visual to the best-matching columns (uncheck for fast title-only matching).</span></span>' +
            '</label>' +
            '<div style="margin:8px 0"><label style="font-size:11px;font-weight:700;color:var(--color-content-text-secondary,#64748b)">Pages <span style="font-weight:400">(uncheck to skip)</span></label>' +
              '<div id="ds-pbix-pages">' +
                pages.map(function (p, i) {
                  return '<label style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--color-card-border,#e2e8f0);cursor:pointer">' +
                    '<input type="checkbox" data-idx="' + i + '" checked>' +
                    '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">' + _esc(p.name || ("Page " + (i + 1))) + '</span>' +
                    '<span style="font-size:10px;color:var(--color-content-text-secondary,#64748b);background:#f1f5f9;border-radius:6px;padding:2px 6px;white-space:nowrap">' +
                      (p.widgets || []).length + ' visual' + ((p.widgets || []).length === 1 ? "" : "s") + '</span>' +
                  '</label>';
                }).join("") +
              '</div></div>' +
          '</div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid var(--color-card-border,#e2e8f0)">' +
            '<button type="button" class="btn btn-sm btn-secondary" data-close="1">Cancel</button>' +
            '<button type="button" class="btn btn-sm btn-primary" data-build="1" id="ds-pbix-build"><i class="fas fa-wand-magic-sparkles"></i> Build Replica</button>' +
          '</div>' +
        '</div>';

      ov.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { ov.remove(); return; }
        if (e.target.closest("[data-build]")) {
          var btn = ov.querySelector("#ds-pbix-build");
          if (btn.disabled) return;
          btn.disabled = true;
          btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Building…';
          var title = (ov.querySelector("#ds-pbix-title") || {}).value || defaultTitle;
          var theme = (ov.querySelector("#ds-pbix-theme") || {}).value || "light";
          var accent = (ov.querySelector("#ds-pbix-accent") || {}).value || "";
          var background = (ov.querySelector("#ds-pbix-bg") || {}).value || "";
          var kept = [];
          ov.querySelectorAll("#ds-pbix-pages input[type=checkbox]").forEach(function (cb) {
            if (!cb.checked) return;
            var p = pages[parseInt(cb.getAttribute("data-idx"), 10)];
            if (p && (p.widgets || []).length) kept.push(p);
          });
          if (!kept.length) { App.warning("Keep at least one page."); btn.disabled = false; btn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> Build Replica'; return; }
          var useAi = !!(ov.querySelector("#ds-pbix-ai") || {}).checked;
          var edited = Object.assign({}, spec, { theme: theme, accent: accent, background: background, pages: kept });
          ov.remove();
          self._buildFromPbix(edited, title.trim() || defaultTitle, useAi);
        }
      });
      document.body.appendChild(ov);
    },

    async _buildFromPbix(spec, title, useAi) {
      var tableName = this._selectedTable.table_name || this._selectedTable.name;
      if (App.info) App.info(useAi === false ? "Building the replica from your Power BI report…" : "AI is mapping the visuals to your data and building the replica…");
      try {
        var res = await API.buildFromPbix({ table: tableName, title: title, specJson: JSON.stringify(spec), ai: useAi !== false });
        if (!res || !res.success) {
          if (App.error) App.error((res && res.message) || (res && res.error) || (res && res.detail) || "Could not build the replica.");
          return;
        }
        this._renderPbixPlans(res, title, tableName);
      } catch (e) {
        if (App.error) App.error("Build failed: " + (e && e.message ? e.message : ""));
      }
    },

    // Register the PBIX-extracted colors as a real user theme (localStorage
    // bd_user_themes) so the whole canvas chrome matches the original report.
    _registerPbixTheme(themeOpts) {
      var t = themeOpts.theme || {};
      var id = "from_pbix";
      var theme = {
        id: id,
        name: "From Power BI",
        primary: t.primary || themeOpts.accent,
        secondary: t.secondary || themeOpts.accent,
        accent: themeOpts.accent,
        palette: (themeOpts.palette || []).slice(),
        gradient: [themeOpts.accent, themeOpts.accent2 || themeOpts.accent],
        background: t.background || "#f8fafc",
        cardBg: t.cardBg || "#ffffff",
        text: t.text || "#0f172a",
        textSecondary: t.textSecondary || "#64748b",
        gridLine: t.gridLine || "#e2e8f0",
        axisColor: t.gridLine || "#e2e8f0",
        uploaded: true
      };
      try {
        var list = JSON.parse(localStorage.getItem("bd_user_themes") || "[]");
        list = list.filter(function (u) { return u && u.id !== id; });
        list.unshift(theme);
        localStorage.setItem("bd_user_themes", JSON.stringify(list));
      } catch (e) {}
      return id;
    },

    // Render the validated per-page plans into blankdash pages. Every widget
    // lands at the EXACT bounding box from the PBIX layout (each page plans on
    // its own canvas), so the replica mirrors the original report geometry.
    _renderPbixPlans(res, title, tableName) {
      var self = this;
      var theme = res.theme || {};
      var themeOpts = this._themeOptsFromSpec(theme);
      var themeId = this._registerPbixTheme(themeOpts);
      var plans = res.plans || [];
      if (!plans.length) {
        App.warning("No widgets could be matched to this table's data.");
        return;
      }
      var pages = plans.map(function (plan, pi) { return self._pbixPageFromPlan(plan, pi, themeOpts, tableName); });
      var canvas = plans[0].canvas || theme.canvas || { w: 1900, h: 1080 };
      var totalWidgets = pages.reduce(function (a, p) { return a + (p.widgets || []).length; }, 0);
      var context = {
        table: tableName,
        name: title,
        canvasW: Math.max(800, Math.round(canvas.w) || 1900),
        canvasH: Math.max(450, Math.round(canvas.h) || 1080),
        grid: 12,
        fixedCanvas: true,
        theme: themeId, themeMode: "full",
        pages: pages,
        currentPage: 0
      };
      sessionStorage.setItem("blankdash_context", JSON.stringify(context));
      App.navigate("#blankdash");
      if (App.success) App.success("Imported " + pages.length + " page(s), " + totalWidgets + " widget(s) from your Power BI file");
    },

    // Convert one validated PBIX page plan into a blankdash page of widgets.
    _pbixPageFromPlan(plan, pi, themeOpts, tableName) {
      var canvas = plan.canvas || { w: 1900, h: 1080 };
      var cw = Math.max(800, Math.round(canvas.w) || 1900);
      var ch = Math.max(450, Math.round(canvas.h) || 1080);
      var palette = themeOpts.palette;
      var spec = plan.spec || {};
      var hdr = (spec.header && spec.header.present) ? spec.header : null;
      var widgets = [];
      var z = 2;
      var ts = Date.now() + "_p" + pi;

      if (hdr) {
        widgets.push(AIBuild.headerWidget(tableName, plan.name || ("Page " + (pi + 1)),
          "Data source: " + tableName + " · Imported from Power BI",
          { x: 0, y: 0, width: cw, height: Math.max(40, Math.round(ch * 0.06)),
            index: z++, accent: themeOpts.accent, accent2: themeOpts.accent2, branding: false }));
      }

      (plan.filters || []).forEach(function (f, i) {
        var box = f.box || {};
        var isDate = String(f.type || "") === "daterange";
        widgets.push({
          id: "flt" + i + "_" + ts,
          type: isDate ? "daterange" : "slicer",
          title: f.label || f.field,
          x: box.x != null ? box.x : 24, y: box.y != null ? box.y : 60,
          width: box.w || 220, height: box.h || 60, zIndex: z++, locked: false,
          config: { table: tableName, sql: f.sql, filterField: f.field,
            label: f.label || f.field, slicerStyle: "dropdown", compact: true }
        });
      });

      (plan.texts || []).forEach(function (tx, i) {
        var box = tx.box || {};
        widgets.push({
          id: "txt" + i + "_" + ts,
          type: "text",
          title: tx.title || "Text",
          x: box.x != null ? box.x : 24, y: box.y != null ? box.y : 24,
          width: box.w || 300, height: box.h || 60, zIndex: z++, locked: false,
          config: { table: tableName, sql: "", label: tx.title || "Text",
            content: tx.content || tx.title || "" }
        });
      });

      (plan.tables || []).forEach(function (tb, i) {
        var box = tb.box || {};
        widgets.push({
          id: "tbl" + i + "_" + ts,
          type: "table",
          title: tb.title || "Data Table",
          x: box.x != null ? box.x : 24, y: box.y != null ? box.y : 24,
          width: box.w || 600, height: box.h || 300, zIndex: z++, locked: false,
          config: { table: tableName, sql: tb.sql, label: tb.title || "Data Table" }
        });
      });

      (plan.kpis || []).forEach(function (k, i) {
        var box = k.box || {};
        widgets.push(AIBuild.kpiWidgetFromPlan(k, tableName, {
          x: box.x != null ? box.x : 24, y: box.y != null ? box.y : 120,
          width: box.w || 300, height: box.h || 120,
          index: z++, colors: palette
        }));
      });

      (plan.zones || []).forEach(function (zn, i) {
        var box = zn.box || {};
        widgets.push(AIBuild.chartWidgetFromPlan(zn, tableName, {
          x: box.x != null ? box.x : 24, y: box.y != null ? box.y : 240,
          width: box.w || 600, height: box.h || 300,
          index: z++, colors: palette
        }));
      });

      (plan.placeholders || []).forEach(function (ph, i) {
        var box = ph.box || {};
        var famLabel = this._pbixFamLabel(ph.family);
        var note = ph.note ? " — " + ph.note : "";
        widgets.push({
          id: "ph" + i + "_" + ts,
          type: "text",
          title: ph.title || "Visual",
          x: box.x != null ? box.x : 24, y: box.y != null ? box.y : 24,
          width: box.w || 300, height: box.h || 120, zIndex: z++, locked: false,
          config: { table: tableName, sql: "", label: ph.title || "Visual",
            content: (ph.title || "Visual") + "\n(" + famLabel + " visual" + note + " — no matching data found)" }
        });
      }, this);

      widgets.forEach(function (w) {
        if (!w || !w.config) return;
        var st = (w.config.style && typeof w.config.style === "object") ? w.config.style : {};
        w.config.style = Object.assign({}, st, {
          themeId: themeId, cardBg: themeOpts.theme.cardBg,
          text: themeOpts.theme.text, textSecondary: themeOpts.theme.textSecondary,
          gridLine: themeOpts.theme.gridLine, accent: themeOpts.accent
        });
      });
      return {
        id: "pbix_page_" + (pi + 1),
        name: plan.name || ("Page " + (pi + 1)),
        widgets: widgets
      };
    },

    _importFromData(data) {
      if (!data || typeof data !== "object") { App.warning("This file does not look like a dashboard export"); return; }
      var cfg = null;
      var name = data.name || data.title || "Imported Dashboard";
      if (Array.isArray(data.pages)) cfg = data;                                   // blankdash full snapshot
      else if (Array.isArray(data.widgets))                                        // flat widget list
        cfg = { name: name, table: data.table || "", pages: [{ id: "page_1", name: "Page 1", widgets: data.widgets }] };
      else if (data.layout && typeof data.layout === "object") cfg = data.layout._blankdash || data.layout;
      else if (data.config && typeof data.config === "object") cfg = data.config._blankdash || data.config;
      if (!cfg || typeof cfg !== "object" || !((cfg.pages && cfg.pages.length) || (cfg.widgets && cfg.widgets.length))) {
        App.warning("This file does not look like a dashboard export");
        return;
      }
      if (!cfg.id) cfg.id = "imported_" + Date.now();
      if (!cfg.time) cfg.time = Date.now();
      if (!cfg.name) cfg.name = name;
      var self = this;
      API.createDashboard(this._dashPayload(cfg, cfg.name)).then(function (r) {
        var dash = (r && r.dashboard) ? r.dashboard : r;
        var nid = dash && dash.id;
        App.success("Imported \u201C" + cfg.name + "\u201D");
        if (nid) App.navigate("#blankdash/" + String(nid).replace(/^blankdash_/, ""));
        else self._renderDashboards();
      }).catch(function (e) {
        App.warning("Import failed: " + (e && e.message ? e.message : "server error"));
      });
    },

    // ── My Dashboards (live server-backed list) ──
    _renderDashboards() {
      var container = document.getElementById("studio-mydash-list");
      if (!container) return;
      var self = this;
      API.dashboards().then(function (res) {
        var list = (res && res.dashboards) || [];
        var items = list.map(function (d) {
          var cfg = null;
          try { cfg = (d.layout && d.layout._blankdash) || (d.layout && typeof d.layout === "object" ? d.layout : null); } catch (e) {}
          var widgets = (d.widgets || []).length;
          if (cfg && Array.isArray(cfg.pages)) widgets = cfg.pages.reduce(function (a, p) { return a + (p.widgets ? p.widgets.length : 0); }, 0);
          var time = (cfg && cfg.time) || (d.updated_at ? new Date(d.updated_at).getTime() : 0);
          return {
            id: d.id,
            name: cfg && cfg.name ? cfg.name : (d.name || "Untitled Dashboard"),
            table: cfg && cfg.table ? cfg.table : "",
            pageCount: (cfg && cfg.pages) ? cfg.pages.length : 1,
            widgetCount: widgets,
            timeStr: time ? new Date(time).toLocaleDateString() : ""
          };
        });
        window._studioDashCount = items.length;
        self._updateKpis();
        if (!items.length) {
          container.innerHTML = '<div class="flex flex-col items-center py-10"><i class="fas fa-folder-open text-3xl text-gray-300 mb-3"></i><p class="text-sm text-gray-400">No saved dashboards yet — create one with AI Auto-Build, a template, or Start Blank above.</p></div>';
          return;
        }
        var html = '<div class="studio-dash-grid">' + items.map(function (d) {
          var id = _esc(String(d.id));
          var meta = [];
          if (d.pageCount > 1) meta.push(d.pageCount + " pages");
          meta.push((d.widgetCount || 0) + " widget" + ((d.widgetCount || 0) === 1 ? "" : "s"));
          if (d.table) meta.push(_esc(d.table));
          if (d.timeStr) meta.push(d.timeStr);
          return '<div class="studio-dash-card">' +
            '<div class="studio-dash-top">' +
              '<div class="studio-dash-icon"><i class="fas fa-chart-pie"></i></div>' +
              '<div class="studio-dash-info">' +
                '<div class="studio-dash-name">' + _esc(d.name || "Untitled Dashboard") + '</div>' +
                '<div class="studio-dash-meta">' + meta.join(" · ") + '</div>' +
              '</div>' +
            '</div>' +
            '<div class="studio-dash-actions">' +
              '<button class="btn btn-sm btn-primary" onclick="StudioPage._openDashboard(\'' + id + '\', false)" title="Open in editor"><i class="fas fa-pen"></i> Open</button>' +
              '<button class="btn btn-sm btn-outline" title="Present" onclick="StudioPage._openDashboard(\'' + id + '\', true)"><i class="fas fa-chalkboard-user"></i></button>' +
              '<button class="btn btn-sm btn-outline" title="Export as Power BI report (.pbix)" onclick="StudioPage._exportDashboardPbit(\'' + id + '\', this)"><i class="fas fa-chart-column" style="color:#e8a80c"></i></button>' +
              '<button class="btn btn-sm btn-outline" title="Duplicate" onclick="StudioPage._duplicateDashboard(\'' + id + '\')"><i class="fas fa-copy"></i></button>' +
              '<button class="btn btn-sm btn-outline" title="Rename" onclick="StudioPage._renameDashboard(\'' + id + '\')"><i class="fas fa-pen-to-square"></i></button>' +
              '<button class="btn btn-sm btn-ghost" title="Delete" onclick="StudioPage._deleteDashboard(\'' + id + '\')"><i class="fas fa-trash"></i></button>' +
            '</div>' +
          '</div>';
        }).join("") + '</div>';
        container.innerHTML = html;
      }).catch(function () {
        container.innerHTML = '<div class="flex flex-col items-center py-10"><i class="fas fa-plug-circle-xmark text-3xl text-gray-300 mb-3"></i><p class="text-sm text-gray-400">Could not load dashboards from the server.</p></div>';
      });
    },

    _dashPayload(cfg, name) {
      var count = 0;
      if (cfg && Array.isArray(cfg.pages)) count = cfg.pages.reduce(function (a, p) { return a + (p.widgets ? p.widgets.length : 0); }, 0);
      else if (cfg && Array.isArray(cfg.widgets)) count = cfg.widgets.length;
      return {
        name: name || (cfg && cfg.name) || "Untitled Dashboard",
        owner_id: localStorage.getItem("username") || "anonymous",
        widgetCount: count,
        is_shared: cfg ? !!cfg.sharePublic : false,
        config: cfg
      };
    },

    _fetchDashboardCfg(id) {
      return API.getDashboard(String(id).replace(/^blankdash_/, "")).then(function (res) {
        var dash = (res && res.dashboard) ? res.dashboard : res;
        var cfg = null;
        try {
          var l = dash.layout;
          if (l && typeof l === "object" && l._blankdash) cfg = l._blankdash;
          else if (l && typeof l === "object") cfg = l;
        } catch (e) {}
        if (!cfg && Array.isArray(dash.widgets)) cfg = { name: dash.name || "Untitled Dashboard", pages: [{ id: "page_1", name: "Page 1", widgets: dash.widgets }] };
        if (!cfg || typeof cfg !== "object") cfg = { name: dash.name || "Untitled Dashboard", pages: [] };
        return { cfg: cfg };
      });
    },

    _openDashboard(id, present) {
      var target = "#blankdash/" + String(id).replace(/^blankdash_/, "");
      if (present) { try { sessionStorage.setItem("blankdash_present", "1"); } catch (e) {} }
      App.navigate(target);
    },

    _exportDashboardPbit(id, btn) {
      /* Export a saved dashboard as a real Power BI report (.pbix) that opens
         in Power BI Desktop with the data model populated. Server-gated via
         FEATURE_DASHBOARD_EXPORT — a 403 opens the upgrade overlay. */
      var serverId = String(id).replace(/^blankdash_/, "").split("/")[0];
      if (!serverId) { App.warning("Save the dashboard first, then export."); return; }
      // Prefer the card's display name for the download filename.
      var name = "";
      try {
        var card = btn && btn.closest ? btn.closest(".studio-dash-card") : null;
        name = (card && card.querySelector(".studio-dash-name"))?.textContent || "";
      } catch (e) {}
      if (!name) name = String(id).replace(/^blankdash_/, "");
      App.info("Building Power BI export\u2026");
      API.exportDashboardPbit(serverId).then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name.replace(/[^\w\-]+/g, "_") + ".pbix";
        document.body.appendChild(a);
        a.click();
        a.remove();
        App.success("Power BI export downloaded \u2014 open it in Power BI Desktop");
      }).catch(function (e) {
        console.warn("pbit export failed:", e);
        App.warning("Power BI export failed: " + (e && e.message ? e.message : "server error"));
      });
    },

    _duplicateDashboard(id) {
      var self = this;
      this._fetchDashboardCfg(id).then(function (res) {
        var cfg = JSON.parse(JSON.stringify(res.cfg));
        cfg.name = "Copy of " + (res.cfg.name || "Untitled Dashboard");
        if (cfg.id) cfg.id = null;
        API.createDashboard(self._dashPayload(cfg, cfg.name)).then(function (r) {
          var dash = (r && r.dashboard) ? r.dashboard : r;
          var nid = dash && dash.id;
          App.success("Duplicated as \u201C" + cfg.name + "\u201D");
          if (nid) App.navigate("#blankdash/" + String(nid).replace(/^blankdash_/, ""));
          else self._renderDashboards();
        }).catch(function (e) {
          App.warning("Duplicate failed: " + (e && e.message ? e.message : "server error"));
        });
      }).catch(function (e) {
        App.warning("Could not load dashboard: " + (e && e.message ? e.message : "server error"));
      });
    },

    _renameDashboard(id) {
      var self = this;
      this._fetchDashboardCfg(id).then(function (res) {
        var cur = res.cfg.name || "Untitled Dashboard";
        var nn = window.prompt("New dashboard name:", cur);
        if (nn == null) return;
        nn = nn.trim();
        if (!nn || nn === cur) return;
        res.cfg.name = nn;
        API.updateDashboard(String(id).replace(/^blankdash_/, ""), self._dashPayload(res.cfg, nn)).then(function () {
          App.success("Dashboard renamed");
          self._renderDashboards();
        }).catch(function (e) {
          App.warning("Rename failed: " + (e && e.message ? e.message : "server error"));
        });
      }).catch(function (e) {
        App.warning("Could not load dashboard: " + (e && e.message ? e.message : "server error"));
      });
    },

    _deleteDashboard(id) {
      var self = this;
      var serverId = String(id).replace(/^blankdash_/, "").split("/")[0];
      if (!window.confirm("Delete this dashboard permanently?")) return;
      API.deleteDashboard(serverId).then(function () {
        try { localStorage.removeItem("blankdash_" + serverId); } catch (e) {}
        App.success("Dashboard deleted");
        self._renderDashboards();
      }).catch(function (e) {
        App.warning("Delete failed: " + (e && e.message ? e.message : "server error"));
      });
    },

    _toggleHistory() {
      var card = document.getElementById("studio-history-card");
      if (!card) return;
      card.style.display = card.style.display === "none" ? "" : "none";
    },

    // ── History ──
    _renderHistory() {
      var container = document.getElementById("studio-history-list");
      if (!container) return;
      if (!_history.length) {
        container.innerHTML = '<div class="flex flex-col items-center py-8"><i class="fas fa-clock text-3xl text-gray-300 mb-3"></i><p class="text-sm text-gray-400">No dashboards created yet. Start by selecting a data source above.</p></div>';
        return;
      }
      var html = '<div class="overflow-x-auto"><table class="data-table w-full"><thead><tr>' +
        '<th>Type</th><th>Table</th><th>Details</th><th>Created</th><th>Action</th>' +
        '</tr></thead><tbody>';
      _history.slice().reverse().forEach(function (h, idx) {
        var typeLabel = h.type === "template" ? "Template" : h.type === "ai" ? "AI Build" : h.type === "sql" ? "SQL" : "Blank";
        var typeIcon = h.type === "template" ? "fa-layer-group" : h.type === "ai" ? "fa-robot" : h.type === "sql" ? "fa-terminal" : "fa-file";
        var templateLabel = h.detail || h.template || "—";
        var timeAgo = _timeAgo(h.time);
        html += '<tr>' +
          '<td><span class="studio-badge"><i class="fas ' + typeIcon + '"></i> ' + typeLabel + '</span></td>' +
          '<td class="font-medium">' + _esc(h.table || "—") + '</td>' +
          '<td>' + _esc(templateLabel) + '</td>' +
          '<td class="text-gray-500">' + timeAgo + '</td>' +
          '<td><button class="btn btn-sm btn-ghost" onclick="StudioPage._clearHistoryItem(' + idx + ')"><i class="fas fa-trash"></i></button></td>' +
        '</tr>';
      });
      html += '</tbody></table></div>';
      html += '<div class="text-center mt-3"><button class="btn btn-sm btn-ghost text-gray-400" onclick="StudioPage._clearHistory()"><i class="fas fa-trash-can"></i> Clear history</button></div>';
      container.innerHTML = html;
    },

    _clearHistoryItem(idx) {
      _history.splice(_history.length - 1 - idx, 1);
      _persistHistory();
      this._renderHistory();
      this._updateKpis();
    },

    _clearHistory() {
      _history = [];
      _persistHistory();
      this._renderHistory();
      this._updateKpis();
    },

    _updateKpis() {
      var tEl = document.getElementById("st-kpi-tables");
      if (tEl) tEl.textContent = String(_tables.length);
      var tmplEl = document.getElementById("st-kpi-templates");
      if (tmplEl) {
        var tCount = 0;
        if (typeof BIDashTemplates !== "undefined") tCount = BIDashTemplates.list().length;
        tmplEl.textContent = String(tCount);
      }
      var hEl = document.getElementById("st-kpi-history");
      if (hEl) {
        var n = window._studioDashCount;
        hEl.textContent = String(typeof n === "number" ? n : _history.length);
      }
      var cntEl = document.getElementById("studio-history-count");
      if (cntEl) cntEl.textContent = "(" + _history.length + ")";
    },
  };

  // ── Helpers ──
  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  function _timeAgo(ts) {
    if (!ts) return "—";
    var diff = Date.now() - ts;
    if (diff < 60000) return "Just now";
    if (diff < 3600000) return Math.floor(diff / 60000) + "m ago";
    if (diff < 86400000) return Math.floor(diff / 3600000) + "h ago";
    return Math.floor(diff / 86400000) + "d ago";
  }

  // ── History Persistence (key namespaced per login) ──
  function _historyKey() {
    return (window.RBAC && window.RBAC.storeKey) ? window.RBAC.storeKey("studio_history") : "studio_history";
  }
  function _loadHistory() {
    try {
      var raw = localStorage.getItem(_historyKey());
      return raw ? JSON.parse(raw) : [];
    } catch (e) { return []; }
  }

  function _persistHistory() {
    try { localStorage.setItem(_historyKey(), JSON.stringify(_history.slice(-50))); } catch (e) { /* noop */ }
  }

  function _saveHistory(entry) {
    _history.push(entry);
    if (_history.length > 50) _history = _history.slice(-50);
    _persistHistory();
  }

  // ── Global bridge for BI page auto-build ──
  window._studioAutoBuild = null;

  window.StudioPage = StudioPage;
  (window._PAGES = window._PAGES || {})[PAGE] = StudioPage;
})();
