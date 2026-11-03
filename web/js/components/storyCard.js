/**
 * StoryCard — Single data story card with narrative, badges, and actions
 */
(function () {
  "use strict";

  function _render(containerId, story) {
    var container = typeof containerId === "string" ? document.getElementById(containerId) : containerId;
    if (!container) return;
    story = story || {};

    var title = story.title || "Insight";
    var narrative = story.narrative || "";
    var type = story.type || "trend";
    var confidence = story.confidence || "medium";
    var source = story.source || "auto";

    // Type icon
    var typeIcons = { trend: "fa-chart-line", anomaly: "fa-exclamation-triangle", correlation: "fa-arrows-alt-h", distribution: "fa-chart-pie", forecast: "fa-chart-line", recommendation: "fa-lightbulb" };
    var typeIcon = typeIcons[type] || "fa-chart-line";
    var typeLabels = { trend: "Trend", anomaly: "Anomaly", correlation: "Correlation", distribution: "Distribution", forecast: "Forecast", recommendation: "Recommendation" };
    var typeLabel = typeLabels[type] || type;

    // Confidence badge
    var confColors = { high: "success", medium: "warning", low: "danger" };
    var confVariant = confColors[confidence] || "default";

    // Build mini chart if chart_data exists
    var chartHtml = "";
    if (story.chartData && story.chartData.values && story.chartData.values.length > 0 && window.Charts) {
      var cid = "story_chart_" + Math.random().toString(36).slice(2, 6);
      chartHtml = '<canvas id="' + cid + '" style="width:100%;height:60px;margin-top:6px;"></canvas>';
      setTimeout(function() {
        // Canvas sparkline: a custom property cannot be a canvas stroke colour, so
  // the brand hue stays a literal here.
  Charts.sparkline(cid, story.chartData.values, { color: "#2563eb" });
      }, 100);
    }

    container.innerHTML =
      '<div class="card" style="padding:14px;">' +
      // Header row: type badge + confidence + source
      '<div class="flex items-center gap-1.5 mb-2">' +
      '<span class="inline-flex items-center text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300"><i class="fas ' + typeIcon + ' mr-0.5"></i>' + typeLabel + '</span>' +
      (window.Badge ? Badge.render(confidence, confVariant, { size: "sm", dot: true }) : '<span class="tag tag-' + confVariant + '">' + confidence + '</span>') +
      (source === "llm" ? '<span class="text-[9px] text-gray-400 ml-auto"><i class="fas fa-robot mr-0.5"></i>AI</span>' : '<span class="text-[9px] text-gray-400 ml-auto">Auto</span>') +
      '</div>' +
      // Title
      '<div class="text-sm font-semibold mb-1">' + _esc(title) + '</div>' +
      // Narrative
      (narrative ? '<div class="text-[12px] text-gray-600 dark:text-gray-400 leading-relaxed mb-1">' + (window.App && App.mdInline ? App.mdInline(narrative) : _esc(narrative).replace(/\n/g, "<br>")) + '</div>' : "") +
      // Chart
      chartHtml +
      // Footer actions
      '<div class="flex items-center gap-2 mt-2 pt-2 border-t dark:border-gray-700">' +
      '<button class="story-add-dash text-[10px] text-primary-500 hover:text-primary-700 font-medium" data-story=\'' + JSON.stringify(story).replace(/'/g, "&apos;") + '\'><i class="fas fa-plus mr-0.5"></i>Add to Dashboard</button>' +
      '</div>' +
      '</div>';

    // Wire add-to-dashboard
    container.querySelector(".story-add-dash")?.addEventListener("click", function() {
      try {
        var s = JSON.parse(this.dataset.story);
        // Store for dashboard to pick up
        var existing = JSON.parse(localStorage.getItem("dash_pending_stories") || "[]");
        var md = (window.App && App.mdInline) ? App.mdInline(s.narrative || "") : "";
        var safeTitle = (window.App && App.escapeHtml) ? App.escapeHtml(s.title || "") : s.title;
        existing.push({ title: s.title, type: "text", config: { content: "<strong>" + safeTitle + "</strong><p>" + md + "</p>" } });
        localStorage.setItem("dash_pending_stories", JSON.stringify(existing));
        App.success("Story added to dashboard!");
      } catch(e) { App.error("Could not add to dashboard"); }
    });
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div"); d.appendChild(document.createTextNode(str)); return d.innerHTML;
  }

  window.StoryCard = { render: _render };
})();
