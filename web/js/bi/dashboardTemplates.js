/**
 * BIDashTemplates — curated dashboard templates for the Power BI Studio.
 *
 * Each template is a layout descriptor that is resolved against the selected
 * table's real columns (measures / dimensions / dates are matched by name
 * heuristics) and expanded into a full widget grid + slicers + insight text.
 *
 * The template descriptor schema supports:
 *   { id, name, icon, blurb,
 *     category: "Executive"|"Finance"|"Sales & Retail"|"Marketing"|"People & HR"|"Operations"|"CRM"|"Industry"|"Quick Start",
 *     nav: "sidebar"|"tabs",   (Power BI-style page navigation hint)
 *     accent: "#hex"   (gallery accent + first-line colour seed),
 *     tags: [str]      (gallery chips),
 *     kpis: n,         (1-6 KPI cards, resolved against the best measure),
 *     charts: [{
 *       type, title,
 *       by: "dim" | "dim2" | "date" | "m",
 *       series: n,      (multi-series line/bar/area/combo),
 *       bucket: "month"|"week"|"day"|"year",
 *       cols, rows      (12-col grid footprint)
 *     }],
 *     table: bool,      (append an "All Data" grid),
 *     slicers: ["dim","dim2","date"],
 *     autoSlicers: n,   (extra auto-added category slicers),
 *     insight: "..."    ({dim}/{dim2}/{date} placeholders are filled in),
 *     pages: [{         (optional additional pages — page 1 is "Overview")
 *       name, kpis, charts, table, slicers
 *     }] }
 *
 * Multi-page templates emit a Power BI-style "Page Navigator" pill strip on
 * page 1 and real slicer widgets (grid layout) so the built dashboard is
 * interactive out of the box.
 *
 * Every emitted widget carries a `config.sql` that aliases `label`/`value`
 * (plus series/category/size/details names) so the built dashboard renders in
 * dashboardstudio and exports as real Power BI visuals.
 *
 *   window.BIDashTemplates = {
 *     list() -> [{id, name, icon, blurb, accent, tags, kpis, charts, category, nav, pages}]
 *     build(templateId, table, ctx) -> {widgets, pages, slicers, insight} | null
 *     quickId
 *   }
 *
 * ctx (supplied by the page) keeps the column heuristics in one place:
 *   { columns(table), isNumeric(table,col), isDate(table,col),
 *     isCategorical(table,col), tableNames() }
 */
window.BIDashTemplates = (function () {
  "use strict";

  /* ── descriptors ──────────────────────────────────────────────────────── */
  var _TEMPLATES = [
    {
      id: "executive", name: "Executive Overview", icon: "fa-briefcase",
      category: "Executive", nav: "sidebar",
      accent: "#1E3A8A", tags: ["Executive", "Sales", "KPI"],
      blurb: "Multi-market executive pack: revenue & margin KPIs, trend, regional share and a forecast view.",
      kpis: 5,
      charts: [
        { type: "line", title: "{date} Trend", by: "date", cols: 6, rows: 3 },
        { type: "pie", title: "{dim} Share", by: "dim" },
        { type: "bar", title: "Top {dim2}", by: "dim2" },
        { type: "column", title: "By {dim}", by: "dim" },
        { type: "gauge", title: "{m}", by: "m" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 0,
      insight: "Track overall performance across {dim} over {date}.",
      pages: [
        {
          name: "Regional", kpis: 4,
          charts: [
            { type: "column", title: "By {dim}", by: "dim" },
            { type: "treemap", title: "{dim} x {dim2} Mix", by: "dim" },
            { type: "funnel", title: "{dim} Funnel", by: "dim" },
            { type: "ranking", title: "{dim2} Leaderboard", by: "dim2" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
        {
          name: "Trend & Forecast", kpis: 4,
          charts: [
            { type: "line", title: "{date} Trend", by: "date", series: 2 },
            { type: "area", title: "{date} Volume", by: "date" },
            { type: "splinearea", title: "{date} Smooth", by: "date" },
            { type: "calendar", title: "Daily {m}", by: "date" },
          ],
          table: false, slicers: ["date"],
        },
      ],
    },
    {
      id: "sales", name: "Sales Performance", icon: "fa-chart-line",
      category: "Sales & Retail", nav: "tabs",
      accent: "#047857", tags: ["Sales", "Trend", "Funnel"],
      blurb: "Sales KPIs, a regional funnel, monthly trend and a P&L waterfall.",
      kpis: 5,
      charts: [
        { type: "column", title: "Revenue by {dim}", by: "dim" },
        { type: "line", title: "{date} Trend", by: "date", series: 2 },
        { type: "funnel", title: "{dim} Funnel", by: "dim" },
        { type: "waterfall", title: "{dim2} Bridge", by: "dim2" },
        { type: "radialbar", title: "{dim} Share", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Sales performance across {dim} and {dim2}, trending over {date}.",
      pages: [
        {
          name: "Breakdown", kpis: 4,
          charts: [
            { type: "radialbar", title: "{dim} Share", by: "dim" },
            { type: "treemap", title: "{dim} x {dim2} Mix", by: "dim" },
            { type: "pareto", title: "By {dim} Pareto", by: "dim" },
            { type: "ranking", title: "{dim2} Leaderboard", by: "dim2" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
    {
      id: "retail", name: "Retail Sales", icon: "fa-store",
      category: "Sales & Retail", nav: "tabs",
      accent: "#B45309", tags: ["Retail", "Mix", "Combo"],
      blurb: "A retail-style pack: KPI band, store/category treemap, and a volume vs revenue combo.",
      kpis: 5,
      charts: [
        { type: "column", title: "Revenue by {dim}", by: "dim" },
        { type: "treemap", title: "{dim} x {dim2} Mix", by: "dim" },
        { type: "combo", title: "Volume vs Revenue", by: "dim", series: 2 },
        { type: "area", title: "{date} Trend", by: "date" },
        { type: "splinearea", title: "{date} Smooth Trend", by: "date" },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 1,
      insight: "Retail mix across {dim} and {dim2} — spot the biggest contributors.",
      pages: [
        {
          name: "Store Mix", kpis: 4,
          charts: [
            { type: "heatmap", title: "{dim} x {dim2} Heat", by: "dim" },
            { type: "pie", title: "{dim2} Share", by: "dim2" },
            { type: "ranking", title: "{dim} Leaderboard", by: "dim" },
            { type: "calendar", title: "Daily {m}", by: "date" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
    {
      id: "chocolate", name: "Chocolate Sales", icon: "fa-candy-cane",
      category: "Sales & Retail", nav: "sidebar",
      accent: "#78611E", tags: ["Sales", "Product", "Team"],
      blurb: "Product/team/geo pack inspired by the chocolate sales sample — overview, geography and sales team.",
      kpis: 5,
      charts: [
        { type: "bar", title: "Top {dim}", by: "dim" },
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "pie", title: "{dim2} Split", by: "dim2" },
        { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
        { type: "wordcloud", title: "{dim} Mentions", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 1,
      insight: "Chocolate sales by {dim} and {dim2} over time.",
      pages: [
        {
          name: "Geography", kpis: 4,
          charts: [
            { type: "column", title: "By {dim}", by: "dim" },
            { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
            { type: "pie", title: "{dim2} Share", by: "dim2" },
            { type: "ranking", title: "{dim} Leaderboard", by: "dim" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
        {
          name: "Sales Team", kpis: 4,
          charts: [
            { type: "bar", title: "By {dim2}", by: "dim2" },
            { type: "combo", title: "{dim2} vs {m}", by: "dim2", series: 2 },
            { type: "gauge", title: "Avg {m}", by: "m" },
            { type: "wordcloud", title: "Top {dim}", by: "dim" },
          ],
          table: true, slicers: ["dim2"],
        },
      ],
    },
    {
      id: "hr", name: "HR Analytics", icon: "fa-users",
      category: "People & HR", nav: "tabs",
      accent: "#7C3AED", tags: ["HR", "People", "Gauge"],
      blurb: "Headcount, salary and a gauge — a people dashboard with a compensation deep-dive.",
      kpis: 5,
      charts: [
        { type: "gauge", title: "Avg {m}", by: "m" },
        { type: "bar", title: "By {dim}", by: "dim" },
        { type: "pie", title: "{dim2} Share", by: "dim2" },
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "bullet", title: "{dim} vs Target", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 0,
      insight: "People metrics across {dim} and {dim2}.",
      pages: [
        {
          name: "Compensation", kpis: 4,
          charts: [
            { type: "bullet", title: "{dim} vs Target", by: "dim" },
            { type: "percentbar", title: "{dim} Mix", by: "dim" },
            { type: "ranking", title: "{dim2} Leaderboard", by: "dim2" },
            { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
    {
      id: "supplychain", name: "Supply Chain", icon: "fa-truck-fast",
      category: "Operations", nav: "sidebar",
      accent: "#0F766E", tags: ["Ops", "Logistics", "Risk"],
      blurb: "Order volumes, delivery risk and a category bridge — with a risk & inventory page.",
      kpis: 5,
      charts: [
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "bar", title: "By {dim}", by: "dim" },
        { type: "waterfall", title: "{dim2} Bridge", by: "dim2" },
        { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
        { type: "pareto", title: "By {dim} Pareto", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 1,
      insight: "Supply chain volume and risk by {dim}, {dim2} and {date}.",
      pages: [
        {
          name: "Risk & Inventory", kpis: 4,
          charts: [
            { type: "gauge", title: "On-time {m}", by: "m" },
            { type: "funnel", title: "{dim} Status", by: "dim" },
            { type: "treemap", title: "{dim} x {dim2} Stock", by: "dim" },
            { type: "calendar", title: "Daily Volume", by: "date" },
          ],
          table: true, slicers: ["dim", "dim2", "date"],
        },
      ],
    },
    {
      id: "marketing", name: "Marketing Campaign", icon: "fa-bullhorn",
      category: "Marketing", nav: "tabs",
      accent: "#b91c1c", tags: ["Marketing", "Funnel", "ROI"],
      blurb: "Spend/ROI KPIs, a channel funnel and a clicks-vs-conversion combo.",
      kpis: 5,
      charts: [
        { type: "combo", title: "Clicks vs Conversions", by: "dim", series: 2 },
        { type: "funnel", title: "{dim} Funnel", by: "dim" },
        { type: "pie", title: "{dim2} Channel", by: "dim2" },
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "rose", title: "{dim} Rose", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 1,
      insight: "Campaign performance and funnel shape across {dim} and {dim2}.",
      pages: [
        {
          name: "Channel Efficiency", kpis: 4,
          charts: [
            { type: "rose", title: "{dim} Rose", by: "dim" },
            { type: "radialbar", title: "{dim2} Share", by: "dim2" },
            { type: "gauge", title: "CTR %", by: "m" },
            { type: "wordcloud", title: "Top {dim}", by: "dim" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
    {
      id: "finance", name: "Finance P&L", icon: "fa-scale-balanced",
      category: "Finance", nav: "sidebar",
      accent: "#065F46", tags: ["Finance", "P&L", "Gauge"],
      blurb: "Revenue/expense/margin KPIs with a P&L waterfall, budget bullets and a mix page.",
      kpis: 5,
      charts: [
        { type: "waterfall", title: "P&L Bridge", by: "dim2" },
        { type: "gauge", title: "{m}", by: "m" },
        { type: "bar", title: "By {dim}", by: "dim" },
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "percentbar", title: "{dim} Mix", by: "dim", cols: 4, rows: 3 },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 0,
      insight: "Financial performance across {dim} over {date}.",
      pages: [
        {
          name: "Mix & Budget", kpis: 4,
          charts: [
            { type: "percentbar", title: "{dim} Mix", by: "dim" },
            { type: "bullet", title: "{dim} vs Target", by: "dim" },
            { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
            { type: "pareto", title: "By {dim} Pareto", by: "dim" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
    {
      id: "crm", name: "CRM Pipeline", icon: "fa-handshake",
      category: "CRM", nav: "tabs",
      accent: "#1D4ED8", tags: ["CRM", "Pipeline", "Funnel"],
      blurb: "Pipeline value, a stage funnel and a win-rate gauge — with an owner performance page.",
      kpis: 5,
      charts: [
        { type: "funnel", title: "{dim} Funnel", by: "dim" },
        { type: "gauge", title: "{m}", by: "m" },
        { type: "column", title: "By {dim2}", by: "dim2" },
        { type: "area", title: "{date} Trend", by: "date" },
        { type: "progress", title: "Pipeline Progress", by: "m" },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 1,
      insight: "Pipeline value by stage ({dim}) and owner ({dim2}).",
      pages: [
        {
          name: "Owner Performance", kpis: 4,
          charts: [
            { type: "ranking", title: "{dim2} Leaderboard", by: "dim2" },
            { type: "bullet", title: "{dim2} vs Target", by: "dim2" },
            { type: "pie", title: "{dim} Share", by: "dim" },
            { type: "progress", title: "Pipeline Progress", by: "m" },
          ],
          table: true, slicers: ["dim2", "dim"],
        },
      ],
    },
    {
      id: "quick", name: "Quick Auto-Build", icon: "fa-wand-magic-sparkles",
      category: "Quick Start", nav: "tabs",
      accent: "#9333EA", tags: ["Auto", "Classic"],
      blurb: "The classic one-click build: KPI band + trend + share + table.",
      kpis: 4,
      charts: [
        { type: "bar", title: "Top {dim}", by: "dim" },
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "pie", title: "{dim} Share", by: "dim" },
        { type: "area", title: "{date} Volume", by: "date" },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 1,
      insight: "",
    },
    {
      id: "customer_profitability", name: "Customer Profitability", icon: "fa-user-tie",
      category: "Finance", nav: "tabs",
      accent: "#991B1B", tags: ["Profit", "Power BI"],
      blurb: "Power BI sample: profit by customer & product with a margin vs volume view.",
      kpis: 5,
      charts: [
        { type: "bar", title: "Top {dim} by {m}", by: "dim" },
        { type: "combo", title: "{m} vs {m2}", by: "dim", series: 2 },
        { type: "treemap", title: "{dim} x {dim2} Profit", by: "dim" },
        { type: "column", title: "By {dim2}", by: "dim2" },
        { type: "bullet", title: "{dim} vs Target", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Profitability concentrates in a few {dim} — the {dim} x {dim2} mix and {m} vs {m2} reveal which segments drive margin.",
    },
    {
      id: "store_sales", name: "Store Sales", icon: "fa-store",
      category: "Sales & Retail", nav: "tabs",
      accent: "#B45309", tags: ["Retail", "Heat", "Power BI"],
      blurb: "Power BI sample: store & district performance with a modernity/density feel.",
      kpis: 5,
      charts: [
        { type: "column", title: "Sales per {dim}", by: "dim" },
        { type: "pie", title: "{dim2} Share", by: "dim2" },
        { type: "combo", title: "Sales vs Units", by: "dim", series: 2 },
        { type: "heatmap", title: "{dim} x {dim2} Heat", by: "dim" },
        { type: "ranking", title: "{dim} Leaderboard", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2"],
      autoSlicers: 1,
      insight: "Store performance by {dim} and {dim2}: heat and unit mix flag the highest-traffic segments.",
    },
    {
      id: "sales_returns", name: "Sales & Returns", icon: "fa-rotate-left",
      category: "Sales & Retail", nav: "tabs",
      accent: "#CA8A04", tags: ["Returns", "Power BI"],
      blurb: "Power BI sample: returns rate, refunds vs quantity and a returns trend.",
      kpis: 5,
      charts: [
        { type: "funnel", title: "{dim} Return Volume", by: "dim" },
        { type: "column", title: "Returns by {dim2}", by: "dim2" },
        { type: "combo", title: "Refunds vs Qty", by: "dim", series: 2 },
        { type: "line", title: "{date} Returns Trend", by: "date" },
        { type: "calendar", title: "{date} Heat", by: "date" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Return volume by {dim} and {dim2} over {date} — the funnel identifies the most-returned products.",
    },
    {
      id: "operations", name: "Order Fulfillment", icon: "fa-boxes-stacked",
      category: "Operations", nav: "tabs",
      accent: "#B91C1C", tags: ["Ops", "Fulfillment", "Power BI"],
      blurb: "Power BI sample: order status funnel, on-time gauge and delivery trend.",
      kpis: 5,
      charts: [
        { type: "funnel", title: "{dim} Order Status", by: "dim" },
        { type: "gauge", title: "On-time {m}", by: "m" },
        { type: "area", title: "{date} Order Volume", by: "date" },
        { type: "bar", title: "By {dim2}", by: "dim2" },
        { type: "lollipop", title: "{dim} Volume", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Fulfillment health by {dim} and {dim2}: the status funnel and on-time gauge show where orders stall.",
    },
    {
      id: "ecommerce", name: "E-Commerce", icon: "fa-cart-shopping",
      category: "Sales & Retail", nav: "tabs",
      accent: "#0E7490", tags: ["E-Commerce", "Orders", "AOV"],
      blurb: "Orders, revenue and AOV KPIs with a channel mix, heat and a top-seller word cloud.",
      kpis: 6,
      charts: [
        { type: "combo", title: "Orders vs Revenue", by: "date", series: 2 },
        { type: "pie", title: "Channel ({dim2}) Share", by: "dim2" },
        { type: "treemap", title: "{dim} x {dim2} Mix", by: "dim" },
        { type: "heatmap", title: "{dim} x {dim2} Heat", by: "dim" },
        { type: "wordcloud", title: "Top {dim}", by: "dim" },
        { type: "gauge", title: "Avg {m}", by: "m" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "E-commerce momentum by {dim} and {dim2} over {date} — AOV and the {dim} x {dim2} heat reveal the spendiest segments.",
      pages: [
        {
          name: "Catalog & Heat", kpis: 4,
          charts: [
            { type: "heatmap", title: "{dim} x {dim2} Heat", by: "dim" },
            { type: "wordcloud", title: "Top {dim}", by: "dim" },
            { type: "ranking", title: "{dim} Leaderboard", by: "dim" },
            { type: "calendar", title: "Daily Orders", by: "date" },
          ],
          table: true, slicers: ["dim", "dim2", "date"],
        },
      ],
    },
    {
      id: "healthcare", name: "Healthcare", icon: "fa-heart-pulse",
      category: "Industry", nav: "tabs",
      accent: "#BE123C", tags: ["Healthcare", "Quality", "Gauge"],
      blurb: "Patients, revenue and quality scores with capacity, outcomes and a demand calendar.",
      kpis: 5,
      charts: [
        { type: "gauge", title: "Capacity ({m}%)", by: "m" },
        { type: "bar", title: "By {dim}", by: "dim" },
        { type: "pie", title: "{dim2} Share", by: "dim2" },
        { type: "line", title: "{date} Demand Trend", by: "date" },
        { type: "calendar", title: "Daily {m}", by: "date" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Care activity by {dim} and {dim2} over {date}: capacity, share and daily demand tell the throughput story.",
    },
    {
      id: "manufacturing", name: "Manufacturing", icon: "fa-industry",
      category: "Operations", nav: "tabs",
      accent: "#A16207", tags: ["Manufacturing", "Yield", "Pareto"],
      blurb: "Production, yield and downtime with a utilization gauge and a defect Pareto.",
      kpis: 5,
      charts: [
        { type: "gauge", title: "Utilization", by: "m" },
        { type: "column", title: "Output by {dim}", by: "dim" },
        { type: "pareto", title: "Defect Pareto ({dim})", by: "dim" },
        { type: "stackedarea", title: "Output vs Target", by: "date", series: 2 },
        { type: "percentbar", title: "Line Share", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Plant output by {dim} and {dim2}: utilization, Pareto defects and output-vs-target reveal the bottlenecks.",
    },
    {
      id: "socialmedia", name: "Social Media", icon: "fa-hashtag",
      category: "Marketing", nav: "tabs",
      accent: "#DB2777", tags: ["Social", "Engagement", "Reach"],
      blurb: "Reach, engagement and CTR KPIs with platform share and a hashtag word cloud.",
      kpis: 6,
      charts: [
        { type: "combo", title: "Reach vs Engagement", by: "date", series: 2 },
        { type: "radialbar", title: "Platform ({dim}) Share", by: "dim" },
        { type: "rose", title: "By {dim2} Rose", by: "dim2" },
        { type: "wordcloud", title: "Top {dim}", by: "dim" },
        { type: "area", title: "{date} Volume", by: "date" },
        { type: "gauge", title: "CTR %", by: "m" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Social reach by {dim} and {dim2}: mix, rose and hashtag cloud show what resonates over {date}.",
    },
    {
      id: "logistics", name: "Logistics", icon: "fa-truck-ramp-box",
      category: "Operations", nav: "tabs",
      accent: "#155E75", tags: ["Logistics", "Fulfillment", "On-time"],
      blurb: "Shipments, on-time rate and cost-per-mile with a status funnel and daily volume.",
      kpis: 5,
      charts: [
        { type: "funnel", title: "{dim} Status", by: "dim" },
        { type: "gauge", title: "On-time Rate", by: "m" },
        { type: "splinearea", title: "{date} Shipment Trend", by: "date" },
        { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
        { type: "calendar", title: "Daily Volume", by: "date" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Shipment health by {dim} and {dim2}: the status funnel and on-time gauge flag lanes that stall.",
    },
    {
      id: "education", name: "Education", icon: "fa-graduation-cap",
      category: "Industry", nav: "tabs",
      accent: "#4D7C0F", tags: ["Education", "Scores", "Enrollment"],
      blurb: "Enrollment, attendance and scores with a completion radial and a results leaderboard.",
      kpis: 5,
      charts: [
        { type: "radial", title: "Completion", by: "m" },
        { type: "bar", title: "Scores by {dim}", by: "dim" },
        { type: "ranking", title: "{dim2} Leaderboard", by: "dim2" },
        { type: "pie", title: "{dim} Share", by: "dim" },
        { type: "line", title: "{date} Trend", by: "date" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Learning outcomes by {dim} and {dim2}: completion, scores and the leaderboard highlight who is falling behind.",
    },

    /* ── New Power-BI-pattern templates ─────────────────────────────────── */
    {
      id: "financial_performance", name: "Financial Performance", icon: "fa-chart-pie",
      category: "Finance", nav: "sidebar",
      accent: "#065F46", tags: ["Finance", "P&L", "Budget", "Power BI"],
      blurb: "Executive finance pack: revenue vs expense combo, P&L waterfall, margin gauge and budget bullets.",
      kpis: 6,
      charts: [
        { type: "combo", title: "Revenue vs Expense", by: "date", series: 2 },
        { type: "waterfall", title: "P&L Bridge", by: "dim2" },
        { type: "gauge", title: "Margin %", by: "m" },
        { type: "bullet", title: "{dim} vs Budget", by: "dim" },
        { type: "column", title: "By {dim}", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 0,
      insight: "Financial health across {dim} over {date}: the revenue/expense combo and P&L bridge show where margin is won or lost.",
      pages: [
        {
          name: "Budget vs Actual", kpis: 4,
          charts: [
            { type: "bullet", title: "{dim} vs Budget", by: "dim" },
            { type: "percentbar", title: "{dim} Mix", by: "dim" },
            { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
            { type: "pareto", title: "By {dim} Pareto", by: "dim" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
    {
      id: "digital_marketing", name: "Digital Marketing", icon: "fa-bullseye",
      category: "Marketing", nav: "sidebar",
      accent: "#b91c1c", tags: ["Marketing", "PPC", "ROI", "Power BI"],
      blurb: "PPC-style pack: spend, impressions, clicks, CTR, CPM & CPC KPIs with channel funnel and cost gauges.",
      kpis: 6,
      charts: [
        { type: "combo", title: "Spend vs Clicks", by: "date", series: 2 },
        { type: "funnel", title: "{dim} Funnel", by: "dim" },
        { type: "pie", title: "{dim2} Channel", by: "dim2" },
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "gauge", title: "CTR %", by: "m" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Campaign efficiency by {dim} and {dim2} over {date}: spend vs clicks and the funnel reveal which channels convert.",
      pages: [
        {
          name: "Cost Efficiency", kpis: 4,
          charts: [
            { type: "gauge", title: "CTR %", by: "m" },
            { type: "gauge", title: "CPM", by: "m" },
            { type: "rose", title: "{dim} Rose", by: "dim" },
            { type: "radialbar", title: "{dim2} Share", by: "dim2" },
            { type: "wordcloud", title: "Top {dim}", by: "dim" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
    {
      id: "supply_chain_ops", name: "Supply Chain Ops", icon: "fa-warehouse",
      category: "Operations", nav: "sidebar",
      accent: "#0F766E", tags: ["Supply Chain", "Inventory", "On-time", "Power BI"],
      blurb: "Supply chain management pack: on-time gauge, status funnel, inventory pareto and cost bridge.",
      kpis: 5,
      charts: [
        { type: "gauge", title: "On-time Rate", by: "m" },
        { type: "funnel", title: "{dim} Status", by: "dim" },
        { type: "bar", title: "By {dim2}", by: "dim2" },
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "pareto", title: "By {dim} Pareto", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Supply chain health by {dim} and {dim2}: the on-time gauge and status funnel flag lanes that stall.",
      pages: [
        {
          name: "Inventory & Cost", kpis: 4,
          charts: [
            { type: "pareto", title: "Inventory Pareto", by: "dim" },
            { type: "treemap", title: "{dim} x {dim2} Stock", by: "dim" },
            { type: "waterfall", title: "{dim2} Cost Bridge", by: "dim2" },
            { type: "calendar", title: "Daily Volume", by: "date" },
          ],
          table: true, slicers: ["dim", "dim2", "date"],
        },
      ],
    },
    {
      id: "car_sales", name: "Car Sales", icon: "fa-car",
      category: "Sales & Retail", nav: "tabs",
      accent: "#1D4ED8", tags: ["Automotive", "Sales", "Power BI"],
      blurb: "Car sales dashboard: units & revenue KPIs, model mix, price trend and a model heat view.",
      kpis: 5,
      charts: [
        { type: "column", title: "Units by {dim}", by: "dim" },
        { type: "pie", title: "{dim2} Share", by: "dim2" },
        { type: "line", title: "{date} Trend", by: "date" },
        { type: "gauge", title: "Avg {m}", by: "m" },
        { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Car sales by {dim} and {dim2} over {date}: model mix and price trend show what sells and at what margin.",
      pages: [
        {
          name: "Model Mix", kpis: 4,
          charts: [
            { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
            { type: "ranking", title: "{dim} Leaderboard", by: "dim" },
            { type: "heatmap", title: "{dim} x {dim2} Heat", by: "dim" },
            { type: "percentbar", title: "{dim} Mix", by: "dim" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
    {
      id: "coffee_shop", name: "Coffee Shop Sales", icon: "fa-mug-hot",
      category: "Sales & Retail", nav: "tabs",
      accent: "#92400E", tags: ["Retail", "Beverage", "Power BI"],
      blurb: "Coffee shop sales: units vs revenue combo, category share, product ranking and a daily heat calendar.",
      kpis: 5,
      charts: [
        { type: "combo", title: "Units vs Revenue", by: "date", series: 2 },
        { type: "pie", title: "{dim} Category", by: "dim" },
        { type: "column", title: "By {dim2}", by: "dim2" },
        { type: "area", title: "{date} Trend", by: "date" },
        { type: "ranking", title: "{dim} Leaderboard", by: "dim" },
      ],
      table: true,
      slicers: ["dim", "dim2", "date"],
      autoSlicers: 1,
      insight: "Coffee shop performance by {dim} and {dim2} over {date}: units vs revenue and the leaderboard show the best sellers.",
      pages: [
        {
          name: "Product Mix", kpis: 4,
          charts: [
            { type: "treemap", title: "{dim} x {dim2}", by: "dim" },
            { type: "ranking", title: "{dim} Leaderboard", by: "dim" },
            { type: "calendar", title: "Daily Sales", by: "date" },
            { type: "wordcloud", title: "Top {dim}", by: "dim" },
          ],
          table: true, slicers: ["dim", "dim2"],
        },
      ],
    },
  ];

  /* ── matching patterns (ordered: higher priority first) ──────────────── */
  var _MEASURE_PRIORITY = [
    /revenue|sales|profit|gmv|income|amount|gross|net/i,
    /value|margin|price|cost|expense|budget|target|rate|score|salary|hours|balance|ltv|roi|ctr|conversion|pipeline|volume|premium/i,
    /qty|quantity|units|count|sum|total|rating|members|enroll|visits|orders|clicks|reach|engagement/i,
  ];
  var _MEASURE_PAT = /revenue|sales|amount|profit|income|value|cost|price|margin|gmv|qty|quantity|units|count|score|rating|salary|hours|expense|balance|rate|ltv|sum|total|roi|ctr|conversion|pipeline|volume|target|budget|gross|net/i;
  var _PROFIT_PAT = /revenue|sales|profit|margin|income|amount|value/i;
  var _DIM_PAT = /region|category|segment|type|status|country|product|customer|channel|department|group|state|city|team|division|store|outlet|supplier|platform|source|market|territory|name|brand|item|stage|owner|campaign|payment|sub.?category|platform|grade|course/i;
  var _ID_PAT = /(_id$|^id$| id$| id |uuid|guid|hash|^key$|_key$|token|secret|password)/i;
  var _DATE_NAME_PAT = /(^|_|[\s])date(\b|_|$)|_day$|_month$|_year$|^day\b|^month\b|^year\b|^dt$/i;

  var _CHART_SUBTYPES = ["line","bar","column","area","pie","doughnut","scatter","bubble","radar","heatmap","funnel","treemap","waterfall","combo","horizontalbar","stackedbar","sparkline","gauge"];
  var _CUSTOM_TYPES = ["radial","watertank","status","comparison","targetactual","trendkpi","percentagekpi","prevkpi","iconkpi","progress","bullet","wordcloud","waffle","calendar","polar","rose","radialbar","smoothline","splinearea","stackedarea","percentbar","lollipop","pareto","ranking","diverging"];
  var _GROUP_TYPES = ["polar","rose","radialbar","lollipop","pareto","ranking","diverging","waterfall","waffle"];

  /* ── helpers ──────────────────────────────────────────────────────────── */

  var _seq = 0;
  function _uid() { _seq++; return "w" + Date.now().toString(36) + "_" + _seq; }

  function _widget(type, title, cols, rows, cfg) {
    return { id: _uid(), type: type, title: title, cols: cols, rows: rows, config: cfg };
  }

  function _match(arr, re, exclude) {
    for (var i = 0; i < arr.length; i++) {
      var c = arr[i];
      if (exclude && c === exclude) continue;
      if (re.test(c)) return c;
    }
    return "";
  }

  function _matchPriority(arr, patterns, exclude) {
    for (var p = 0; p < patterns.length; p++) {
      var hit = _match(arr, patterns[p], exclude);
      if (hit) return hit;
    }
    return "";
  }

  function _firstOther(arr, exclude) {
    for (var i = 0; i < arr.length; i++) if (arr[i] !== exclude) return arr[i];
    return "";
  }

  function _quoted(col) { return '"' + String(col).replace(/"/g, '""') + '"'; }

  function _trendSql(table, dateCol, m, bucket) {
    var fmt = "'%Y-%m'";
    if (bucket === "week") fmt = "'%Y-W%W'";
    else if (bucket === "year") fmt = "'%Y'";
    else if (bucket === "day") fmt = "'%Y-%m-%d'";
    return 'SELECT strftime(CAST(' + _quoted(dateCol) + ' AS TIMESTAMP), ' + fmt + ') AS label, SUM(' + _quoted(m) + ') AS value FROM ' + _quoted(table) + ' GROUP BY label ORDER BY label LIMIT 12';
  }

  function _daySql(table, dateCol, m) {
    return 'SELECT strftime(CAST(' + _quoted(dateCol) + ' AS TIMESTAMP), \'%Y-%m-%d\') AS label, SUM(' + _quoted(m) + ') AS value FROM ' + _quoted(table) + ' GROUP BY label ORDER BY label LIMIT 90';
  }

  function _groupSql(table, col, m, opt) {
    var order = (opt && opt.order) || "DESC";
    var limit = (opt && opt.limit) || 12;
    return 'SELECT ' + _quoted(col) + ' AS label, SUM(' + _quoted(m) + ') AS value FROM ' + _quoted(table) + ' GROUP BY label ORDER BY value ' + order + ' LIMIT ' + limit;
  }

  function _multiSql(table, col, m1, m2, isDate, bucket) {
    var groupExpr = isDate
      ? 'strftime(CAST(' + _quoted(col) + ' AS TIMESTAMP), \'%Y-%m\')'
      : _quoted(col);
    var fmt = bucket === "month" ? "'%Y-%m'" : bucket === "day" ? "'%Y-%m-%d'" : "'%Y-%m'";
    if (isDate && bucket && bucket !== "month") groupExpr = 'strftime(CAST(' + _quoted(col) + ' AS TIMESTAMP), ' + fmt + ')';
    return 'SELECT ' + groupExpr + ' AS label, SUM(' + _quoted(m1) + ') AS ' + _quoted(m1) + ', SUM(' + _quoted(m2) + ') AS ' + _quoted(m2) + ' FROM ' + _quoted(table) + ' GROUP BY label ORDER BY label LIMIT 12';
  }

  var _KPI_VARIANTS = [
    { agg: "SUM", prefix: "Total " },
    { agg: "AVG", prefix: "Avg " },
    { agg: "MAX", prefix: "Peak " },
    { agg: "COUNT", prefix: "Records" },
    { agg: "MIN", prefix: "Lowest " },
  ];

  function _kpiWidget(H, i) {
    /* Registered measures for this table take priority over guessed columns */
    var regs = [];
    try { regs = (window.App && App.measures) || []; } catch (e) {}
    var mine = regs.filter(function (m) { return m.table_name === H.table; });
    if (mine.length) {
      var mm = mine[i % mine.length];
      var cfg = {
        table: H.table,
        sql: 'SELECT ' + mm.expression + ' AS value FROM ' + _quoted(H.table),
        xCol: "", yCol: "value", label: mm.name,
        measureExpr: mm.expression, measureId: mm.id, measureName: mm.name,
        measureFormat: mm.format || "auto", format: mm.format || "auto",
        timeIntel: mm.date_column ? "ytd" : "none", dateCol: mm.date_column || "", periodLabel: ""
      };
      if (mm.date_column) {
        cfg.sparkSql = 'SELECT strftime(CAST(' + _quoted(mm.date_column) + " AS TIMESTAMP), '%Y-%m') AS label, " + mm.expression + " AS value FROM " + _quoted(H.table) + " GROUP BY label ORDER BY label LIMIT 12";
      }
      return _widget("kpi", mm.name, 3, 2, cfg);
    }
    var v = _KPI_VARIANTS[i % _KPI_VARIANTS.length];
    var title = v.prefix === "Records" ? "Records"
      : (H.m.toLowerCase().indexOf(v.prefix.trim().toLowerCase()) === 0 ? H.m : v.prefix + H.m);
    var expr = v.agg === "COUNT"
      ? "COUNT(*) AS value"
      : v.agg + '(' + _quoted(H.m) + ') AS value';
    var cfg = {
      table: H.table,
      sql: 'SELECT ' + expr + ' FROM ' + _quoted(H.table),
      xCol: "", yCol: "value", label: title,
    };
    if (H.date && (v.agg === "SUM" || v.agg === "AVG" || v.agg === "MAX")) cfg.sparkSql = _trendSql(H.table, H.date, H.m, "month");
    return _widget("kpi", title, 3, 2, cfg);
  }

  function _chartWidget(spec, H) {
    var type = spec.type;
    var title = String(spec.title || "").replace(/\{dim\}/g, H.dim || "?")
      .replace(/\{dim2\}/g, H.dim2 || "?").replace(/\{date\}/g, H.date || "?")
      .replace(/\{m\}/g, H.m || "?").replace(/\{m2\}/g, H.m2 || "?");
    var cols = spec.cols || 6, rows = spec.rows || 3;
    var cfg = { table: H.table, sql: "", xCol: "label", yCol: "value", label: title };

    if (_CHART_SUBTYPES.indexOf(type) !== -1) {
      cfg.chartType = type === "horizontalbar" ? "bar" : type === "stackedbar" ? "bar" : type;
      if (type === "horizontalbar") cfg.indexAxis = "y";
      if (type === "stackedbar") cfg.stacked = true;
    }
    if (type === "stackedarea") cfg.stacked = true;
    if (type === "percentbar") { cfg.stacked = true; cfg.percent = true; }

    if (!H.numCount) return null; // a chart needs at least one measure

    /* single-measure dials / progress / KPI-likes */
    if (["gauge", "radial", "watertank", "status"].indexOf(type) !== -1) {
      cfg.sql = 'SELECT SUM(' + _quoted(H.m) + ') AS value FROM ' + _quoted(H.table);
      return _widget(_CHART_SUBTYPES.indexOf(type) !== -1 ? "chart" : type, title, type === "gauge" ? 3 : 4, rows, cfg);
    }

    if (type === "progress") {
      cfg.sql = 'SELECT SUM(' + _quoted(H.m) + ') AS value, (SELECT SUM(' + _quoted(H.m) + ') FROM ' + _quoted(H.table) + ') AS total FROM ' + _quoted(H.table) + ' LIMIT 1';
      return _widget("progress", title, 4, rows, cfg);
    }

    if (["comparison", "trendkpi", "percentagekpi", "iconkpi"].indexOf(type) !== -1) {
      cfg.sql = 'SELECT SUM(' + _quoted(H.m) + ') AS value, ROUND(AVG(' + _quoted(H.m) + '), 2) AS comparison FROM ' + _quoted(H.table);
      return _widget(type, title, 4, rows, cfg);
    }

    if (type === "targetactual") {
      var tgt = H.m2 || H.m;
      cfg.sql = 'SELECT SUM(' + _quoted(H.m) + ') AS actual, ROUND(AVG(' + _quoted(tgt) + '), 2) AS target FROM ' + _quoted(H.table);
      return _widget("targetactual", title, 4, rows, cfg);
    }

    if (type === "bullet") {
      var bt = H.m2 || H.m;
      cfg.sql = 'SELECT ' + _quoted(H.dim) + ' AS label, SUM(' + _quoted(H.m) + ') AS value, AVG(' + _quoted(bt) + ') AS target FROM ' + _quoted(H.table) + ' GROUP BY label ORDER BY value DESC LIMIT 8';
      return _widget("bullet", title, cols, rows, cfg);
    }

    if (type === "wordcloud") {
      cfg.sql = 'SELECT ' + _quoted(H.dim) + ' AS label, COUNT(*) AS value FROM ' + _quoted(H.table) + ' GROUP BY label ORDER BY value DESC LIMIT 40';
      return _widget("wordcloud", title, cols, 4, cfg);
    }

    /* date-driven heat calendar */
    if (type === "calendar") {
      if (H.date) cfg.sql = _daySql(H.table, H.date, H.m);
      else cfg.sql = _groupSql(H.table, H.dim, H.m, { order: "DESC", limit: 12 });
      return _widget("calendar", title, cols, 4, cfg);
    }

    /* combo / multi-series pairs */
    if (type === "combo" || type === "stackedarea" || type === "percentbar" || (spec.series > 1 && H.m2 && H.m2 !== H.m)) {
      var a = H.m, b = (H.m2 && H.m2 !== H.m) ? H.m2 : H.m;
      if (spec.by === "date" && H.date) cfg.sql = _multiSql(H.table, H.date, a, b, true, spec.bucket || "month");
      else cfg.sql = 'SELECT ' + _quoted(H.dim) + ' AS label, SUM(' + _quoted(a) + ') AS ' + _quoted(a) + ', SUM(' + _quoted(b) + ') AS ' + _quoted(b) + ' FROM ' + _quoted(H.table) + ' GROUP BY label ORDER BY label LIMIT 12';
      cfg.series = [a, b];
      cfg.yCol = a;
      return _widget(type === "combo" || _CHART_SUBTYPES.indexOf(type) !== -1 ? "chart" : type, title, cols, rows, cfg);
    }

    /* treemap + heatmap (second categorical dimension) */
    if (type === "treemap") {
      var det = H.dim2 || H.dim;
      cfg.sql = 'SELECT ' + _quoted(H.dim) + ' AS label, SUM(' + _quoted(H.m) + ') AS value, ' + _quoted(det) + ' AS details FROM ' + _quoted(H.table) + ' GROUP BY label, details ORDER BY value DESC LIMIT 30';
      cfg.detailsCol = "details";
      return _widget("chart", title, 6, 4, cfg);
    }

    if (type === "heatmap") {
      var d2 = H.dim2 || H.dim;
      cfg.sql = 'SELECT ' + _quoted(H.dim) + ' AS label, ' + _quoted(d2) + ' AS category, SUM(' + _quoted(H.m) + ') AS value FROM ' + _quoted(H.table) + ' GROUP BY ' + _quoted(H.dim) + ', ' + _quoted(d2) + ' ORDER BY label LIMIT 40';
      cfg.categoryCol = "category";
      return _widget("chart", title, 6, 4, cfg);
    }

    /* funnel + waterfall */
    if (type === "funnel") {
      cfg.sql = 'SELECT ' + _quoted(H.dim) + ' AS label, SUM(' + _quoted(H.m) + ') AS value FROM ' + _quoted(H.table) + ' GROUP BY label ORDER BY value DESC LIMIT 10';
      return _widget("chart", title, cols, rows, cfg);
    }

    if (type === "waterfall" && _CHART_SUBTYPES.indexOf(type) !== -1) {
      cfg.sql = 'SELECT ' + _quoted(H.dim) + ' AS label, SUM(' + _quoted(H.m) + ') AS value FROM ' + _quoted(H.table) + ' GROUP BY label ORDER BY label LIMIT 12';
      return _widget("chart", title, 6, 4, cfg);
    }

    /* custom grouped types (polar/rose/radialbar/lollipop/pareto/ranking/diverging/waffle) */
    if (_CUSTOM_TYPES.indexOf(type) !== -1 && _GROUP_TYPES.indexOf(type) !== -1) {
      var lim = (type === "polar" || type === "rose" || type === "radialbar") ? 8 : 12;
      cfg.sql = _groupSql(H.table, H.dim, H.m, { order: "DESC", limit: lim });
      return _widget(type, title, cols, rows, cfg);
    }

    /* date bucket (line/area/smoothline/splinearea) */
    if (spec.by === "date" && H.date) {
      cfg.sql = _trendSql(H.table, H.date, H.m, spec.bucket || "month");
      return _widget(_CHART_SUBTYPES.indexOf(type) !== -1 ? "chart" : type, title, cols, rows, cfg);
    }

    var order = (type === "pie" || type === "bar" || type === "column")
      ? " ORDER BY value DESC LIMIT 12" : " ORDER BY label LIMIT 12";
    cfg.sql = 'SELECT ' + _quoted(H.dim) + ' AS label, SUM(' + _quoted(H.m) + ') AS value FROM ' + _quoted(H.table) + ' GROUP BY label' + order;
    return _widget(_CHART_SUBTYPES.indexOf(type) !== -1 ? "chart" : type, title, cols, rows, cfg);
  }

  function _tableWidget(table) {
    var cfg = { table: table, sql: 'SELECT * FROM "' + table + '" LIMIT 50', xCol: "", yCol: "" };
    return _widget("table", "All Data", 12, 3, cfg);
  }

  /* Power BI-style slicer widgets (grid layout) resolved from a spec list of
     "dim" | "dim2" | "date" | <column name>. */
  function _slicerWidgets(table, H, specs) {
    var out = [];
    (specs || []).forEach(function (s) {
      var field, kind = "slicer";
      if (s === "date") { field = H.date; kind = "daterange"; }
      else if (s === "dim") field = H.dim;
      else if (s === "dim2") field = H.dim2;
      else field = s;
      if (!field) return;
      var sql = kind === "daterange"
        ? 'SELECT MIN(' + _quoted(field) + ') AS min_val, MAX(' + _quoted(field) + ') AS max_val FROM ' + _quoted(table)
        : 'SELECT DISTINCT ' + _quoted(field) + ' AS label FROM ' + _quoted(table) + ' ORDER BY label';
      out.push(_widget(kind, field, 3, 2, { table: table, sql: sql, filterField: field, label: field }));
    });
    return out;
  }

  /* Power BI-style page navigator pill strip (grid layout, full-width row). */
  function _navWidget(pageNames, accent) {
    var acc = accent || "var(--color-primary)";
    var esc = function (s) {
      var d = document.createElement("div");
      d.appendChild(document.createTextNode(String(s == null ? "" : s)));
      return d.innerHTML;
    };
    var pills = pageNames.map(function (nm, i) {
      var active = i === 0;
      return '<span onclick="event.stopPropagation();window.BlankDash&&BlankDash._switchPage(' + i + ')" style="display:inline-block;padding:6px 14px;margin:2px 4px;border-radius:999px;cursor:pointer;font-size:12px;font-weight:700;letter-spacing:.3px;white-space:nowrap;' +
        (active
          ? 'background:' + acc + ';color:#fff;'
          : 'background:color-mix(in srgb,' + acc + ' 8%,transparent);color:var(--color-text);border:1px solid color-mix(in srgb,' + acc + ' 18%,transparent);') +
        '">' + (i + 1) + '&nbsp;&middot;&nbsp;' + esc(nm) + '</span>';
    }).join("");
    var html =
      '<div style="height:100%;display:flex;align-items:center;gap:10px;padding:4px 10px;overflow-x:auto;font-family:inherit">' +
        '<i class="fas fa-layer-group" style="color:' + acc + ';font-size:13px;flex:none"></i>' +
        '<div style="display:flex;align-items:center;flex-wrap:nowrap">' + pills + '</div>' +
        '<div style="flex:1"></div>' +
      '</div>';
    return _widget("custom", "Page Navigator", 12, 1, { table: "", html: html, label: "Page Navigator", plain: true, autoFit: true });
  }

  /* ── build ────────────────────────────────────────────────────────────── */

  function _find(id) {
    for (var i = 0; i < _TEMPLATES.length; i++) if (_TEMPLATES[i].id === id) return _TEMPLATES[i];
    return null;
  }

  /**
   * Expand a template against a table's real columns.
   * @returns {{widgets:Array, pages:Array, slicers:Array, insight:string}} or null
   */
  function build(id, table, ctx) {
    var t = _find(id);
    if (!t || !table || !ctx) return null;
    var cols = (ctx.columns && ctx.columns(table)) || [];
    if (!cols.length) return null;

    var numeric = [], dates = [], cats = [];
    for (var i = 0; i < cols.length; i++) {
      var c = cols[i];
      if (ctx.isDate(table, c)) dates.push(c);
      else if (ctx.isNumeric(table, c)) numeric.push(c);
      else cats.push(c);
    }
    /* date-looking text columns (e.g. a DATE stored as VARCHAR) count as dates */
    if (!dates.length) {
      var dateNamed = cats.filter(function (c) { return _DATE_NAME_PAT.test(c); });
      if (dateNamed.length) {
        dates = dateNamed.slice(0, 1);
        cats = cats.filter(function (c) { return dates.indexOf(c) === -1; });
      }
    }
    /* exclude id-ish columns from categorical roles — nobody slices by a PK */
    cats = cats.filter(function (c) { return !_ID_PAT.test(c); });

    var m = _matchPriority(numeric, _MEASURE_PRIORITY);
    if (!m) m = numeric[0] || "";
    var m2 = _match(numeric, _PROFIT_PAT, m) || _firstOther(numeric, m) || m;
    var dim = _match(cats, _DIM_PAT) || cats[0] || "";
    var dim2 = _firstOther(cats, dim) || dim;
    var date = dates[0] || "";

    var H = { table: table, m: m, m2: m2, dim: dim, dim2: dim2, date: date, numCount: numeric.length };

    /* Build one page's widgets from a page descriptor (kpis/charts/table). */
    function buildPage(spec) {
      var widgets = [];
      var kpiCount = spec.kpis || 4;
      if (H.m) {
        for (var k = 0; k < kpiCount; k++) widgets.push(_kpiWidget(H, k));
      }
      (spec.charts || []).forEach(function (cs) {
        var w = _chartWidget(cs, H);
        if (w) widgets.push(w);
      });
      if (spec.table) widgets.push(_tableWidget(table));
      return widgets;
    }

    var pages = [];
    pages.push({ id: "page_1", name: "Overview", widgets: buildPage(t) });
    (t.pages || []).forEach(function (pg, pi) {
      pages.push({ id: "page_" + (pi + 2), name: pg.name || ("Page " + (pi + 2)), widgets: buildPage(pg) });
    });

    /* Power BI-style page navigator on page 1 for multi-page templates. */
    if (pages.length > 1) {
      pages[0].widgets.unshift(_navWidget(pages.map(function (p) { return p.name; }), t.accent));
    }

    /* Slicer widgets (grid) on page 1 — Power BI slicer band. */
    var slicerWidgets = _slicerWidgets(table, H, t.slicers);
    if (t.autoSlicers) {
      cats.slice(0, t.autoSlicers).forEach(function (f) {
        if (!slicerWidgets.some(function (s) { return s.config.filterField === f; })) {
          slicerWidgets.push(_widget("slicer", f, 3, 2, {
            table: table,
            sql: 'SELECT DISTINCT ' + _quoted(f) + ' AS label FROM ' + _quoted(table) + ' ORDER BY label',
            filterField: f, label: f
          }));
        }
      });
    }
    pages[0].widgets = slicerWidgets.concat(pages[0].widgets);

    var insight = String(t.insight || "").replace(/\{dim\}/g, dim || "?")
      .replace(/\{dim2\}/g, dim2 || "?").replace(/\{date\}/g, date || "?")
      .replace(/\{m\}/g, m || "?").replace(/\{m2\}/g, m2 || "?");
    return { widgets: pages[0].widgets, pages: pages, slicers: slicerWidgets, insight: insight };
  }

  return {
    list: function () { return _TEMPLATES.slice(); },
    build: build,
    quickId: "quick",
  };
})();
