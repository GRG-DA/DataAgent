/**
 * StoryFeed — Grid of story cards with filtering, sorting, and regeneration
 */
(function () {
  "use strict";

  function _render(containerId, opts) {
    var container = typeof containerId === "string" ? document.getElementById(containerId) : containerId;
    if (!container) return;
    opts = opts || {};
    var stories = opts.stories || [];
    var tableName = opts.tableName || "";
    var onRegenerate = opts.onRegenerate || null;

    // Loading state
    if (opts.loading) {
      container.innerHTML = '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">';
      for (var i = 0; i < 6; i++) {
        container.innerHTML += '<div class="card p-4"><div class="skeleton skeleton-text w-1/3"></div><div class="skeleton skeleton-text mt-2"></div><div class="skeleton skeleton-text-sm mt-1"></div><div class="skeleton skeleton-text mt-1 w-2/3"></div></div>';
      }
      container.innerHTML += '</div>';
      return;
    }

    if (!stories || stories.length === 0) {
      container.innerHTML =
        '<div class="empty-state py-8"><i class="fas fa-book-open text-3xl text-gray-300"></i><h3 class="text-sm mt-2">No stories yet</h3><p class="text-xs text-gray-500 mt-1">Select a table and generate insights to see data stories</p></div>';
      return;
    }

    // Control bar
    var html = '<div class="flex items-center gap-2 mb-3 flex-wrap">';
    html += '<select id="sf-type-filter" class="form-select text-xs" style="max-width:130px;padding:4px 8px;">';
    html += '<option value="all">All Types</option>';
    html += '<option value="trend">Trends</option><option value="anomaly">Anomalies</option><option value="correlation">Correlations</option><option value="distribution">Distributions</option><option value="forecast">Forecasts</option>';
    html += '</select>';
    html += '<select id="sf-conf-filter" class="form-select text-xs" style="max-width:120px;padding:4px 8px;">';
    html += '<option value="all">All Confidence</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>';
    html += '</select>';
    html += '<button id="sf-regenerate" class="btn btn-sm btn-secondary text-xs"><i class="fas fa-sync mr-1"></i>Regenerate</button>';
    if (tableName) html += '<span class="text-[10px] text-gray-400 ml-auto">Table: ' + _esc(tableName) + '</span>';
    html += '</div>';

    // Story grid
    html += '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3" id="sf-grid">';
    html += '</div>';

    container.innerHTML = html;

    // Render stories in grid
    var grid = document.getElementById("sf-grid");
    if (grid) _renderStories(grid, stories);

    // Wire filters
    document.getElementById("sf-type-filter")?.addEventListener("change", function() {
      _applyFilters(stories, grid);
    });
    document.getElementById("sf-conf-filter")?.addEventListener("change", function() {
      _applyFilters(stories, grid);
    });

    // Wire regenerate
    document.getElementById("sf-regenerate")?.addEventListener("click", function() {
      if (onRegenerate) onRegenerate();
      else if (tableName && API.llmInsights) {
        grid.innerHTML = '<div class="col-span-3 text-center py-8"><i class="fas fa-spinner fa-spin"></i><p class="text-xs mt-2">Regenerating...</p></div>';
        API.llmInsights(tableName, "Generate data stories for this dataset")
          .then(function(data) {
            var newStories = _buildStoriesFromInsights(data, tableName);
            _renderStories(grid, newStories);
          })
          .catch(function() { grid.innerHTML = '<div class="col-span-3 text-center text-red-400 text-xs py-4">Failed to regenerate. Try again.</div>'; });
      }
    });
  }

  function _renderStories(grid, stories) {
    grid.innerHTML = "";
    stories.forEach(function(s) {
      var card = document.createElement("div");
      StoryCard.render(card, s);
      grid.appendChild(card);
    });
  }

  function _applyFilters(stories, grid) {
    var typeFilter = document.getElementById("sf-type-filter")?.value || "all";
    var confFilter = document.getElementById("sf-conf-filter")?.value || "all";
    var filtered = stories.filter(function(s) {
      if (typeFilter !== "all" && s.type !== typeFilter) return false;
      if (confFilter !== "all" && s.confidence !== confFilter) return false;
      return true;
    });
    _renderStories(grid, filtered);
    if (filtered.length === 0) {
      grid.innerHTML = '<div class="col-span-3 text-center text-gray-400 text-xs py-8">No stories match the selected filters</div>';
    }
  }

  function _buildStoriesFromInsights(data, tableName) {
    var ins = (data && data.insights) || data || {};
    var narrative = ins.narrative || "";
    var findings = ins.findings || [];
    var recommendations = ins.recommendations || [];
    var stories = [];

    if (narrative) {
      stories.push({ title: "Executive Summary", narrative: narrative, type: "trend", confidence: "high", source: "llm" });
    }
    findings.slice(0, 5).forEach(function(f, i) {
      stories.push({ title: "Finding " + (i + 1), narrative: f, type: i === 0 ? "trend" : "correlation", confidence: "medium", source: "llm" });
    });
    recommendations.slice(0, 3).forEach(function(r, i) {
      stories.push({ title: "Recommendation", narrative: r, type: "recommendation", confidence: "high", source: "auto" });
    });
    return stories;
  }

  function _esc(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div"); d.appendChild(document.createTextNode(str)); return d.innerHTML;
  }

  window.StoryFeed = { render: _render, buildStoriesFromInsights: _buildStoriesFromInsights };
})();
