/**
 * Home Page — Landing dashboard with quick actions, KPIs, and activity
 * Complete rebuild with hero section, stats cards, tool grid, and recent activity.
 */
(function () {
  "use strict";

  const PAGE = "#dashboard";
  var _data = { tables: [], tableCount: 0, rowCount: 0, apiHealthy: false, recentQueries: [] };

  const HomePage = {
    async init() {
      _data = { tables: [], tableCount: 0, rowCount: 0, apiHealthy: false, recentQueries: [] };
    },

    async destroy() { /* noop */ },

    async render() {
      var pc = document.getElementById("page-content");
      if (!pc) return;

      pc.innerHTML =
        '<div class="animate-fade-in max-w-6xl mx-auto">' +
        // ── Hero Section ──
        '<div class="home-hero">' +
          '<div class="hero-icon"><i class="fas fa-chart-network"></i></div>' +
          '<h1 class="hero-title">DataAgent</h1>' +
          '<p class="hero-subtitle">AI-powered data analytics — ask questions, explore data, build dashboards</p>' +
          '<div class="hero-badges">' +
            '<span class="hero-badge"><i class="fas fa-cloud-arrow-up"></i> Upload</span>' +
            '<span class="hero-badge"><i class="fas fa-wand-magic-sparkles"></i> AI Chat</span>' +
            '<span class="hero-badge"><i class="fas fa-terminal"></i> SQL</span>' +
            '<span class="hero-badge"><i class="fas fa-chart-line"></i> Analyze</span>' +
            '<span class="hero-badge"><i class="fas fa-file-export"></i> Export</span>' +
          '</div>' +
        '</div>' +
        // ── KPI Stats Row ──
        '<div class="kpi-row" id="home-kpi-row">' +
          '<div class="kpi-card"><div class="kpi-icon"><i class="fas fa-table"></i></div><div class="kpi-info"><span class="kpi-value" id="kpi-tables">—</span><span class="kpi-label">Tables</span></div></div>' +
          '<div class="kpi-card"><div class="kpi-icon"><i class="fas fa-rows"></i></div><div class="kpi-info"><span class="kpi-value" id="kpi-rows">—</span><span class="kpi-label">Total Rows</span></div></div>' +
          '<div class="kpi-card"><div class="kpi-icon"><i class="fas fa-server"></i></div><div class="kpi-info"><span class="kpi-value" id="kpi-status">—</span><span class="kpi-label">API Status</span></div></div>' +
          '<div class="kpi-card"><div class="kpi-icon"><i class="fas fa-clock"></i></div><div class="kpi-info"><span class="kpi-value" id="kpi-queries">—</span><span class="kpi-label">Queries</span></div></div>' +
        '</div>' +
        // ── Quick Action Cards ──
        '<h2 class="section-title"><i class="fas fa-bolt mr-2"></i>Quick Actions</h2>' +
        '<div class="home-cards" id="home-action-cards">' +
          '<a href="#upload" class="home-card card-accent-purple" onclick="App.navigate(\'#upload\');return false"><div class="home-card-icon" style="background:#f3e8ff"><i class="fas fa-cloud-arrow-up" style="color:#8b5cf6"></i></div><div class="home-card-title">Upload Data</div><div class="home-card-desc">Import CSV, Excel, Parquet files</div><div class="home-card-arrow"><i class="fas fa-arrow-right"></i></div></a>' +
          '<a href="#chat" class="home-card card-accent-blue" onclick="App.navigate(\'#chat\');return false"><div class="home-card-icon" style="background:#dbeafe"><i class="fas fa-wand-magic-sparkles" style="color:#3b82f6"></i></div><div class="home-card-title">AI Chat</div><div class="home-card-desc">Ask questions in natural language</div><div class="home-card-arrow"><i class="fas fa-arrow-right"></i></div></a>' +
          '<a href="#sql" class="home-card card-accent-green" onclick="App.navigate(\'#sql\');return false"><div class="home-card-icon" style="background:#d1fae5"><i class="fas fa-terminal" style="color:#22c55e"></i></div><div class="home-card-title">SQL Explorer</div><div class="home-card-desc">Run queries directly on your data</div><div class="home-card-arrow"><i class="fas fa-arrow-right"></i></div></a>' +
          '<a href="#analysis" class="home-card card-accent-orange" onclick="App.navigate(\'#analysis\');return false"><div class="home-card-icon" style="background:#ffedd5"><i class="fas fa-chart-line" style="color:#f97316"></i></div><div class="home-card-title">Analysis</div><div class="home-card-desc">Trend projection, anomalies, segment breakdowns</div><div class="home-card-arrow"><i class="fas fa-arrow-right"></i></div></a>' +
          '<a href="#export" class="home-card card-accent-teal" onclick="App.navigate(\'#export\');return false"><div class="home-card-icon" style="background:#ccfbf1"><i class="fas fa-file-export" style="color:#14b8a6"></i></div><div class="home-card-title">Export</div><div class="home-card-desc">CSV, Parquet, Excel, Power BI</div><div class="home-card-arrow"><i class="fas fa-arrow-right"></i></div></a>' +
          '<a href="#warehouse" class="home-card card-accent-pink" onclick="App.navigate(\'#warehouse\');return false"><div class="home-card-icon" style="background:#fce7f3"><i class="fas fa-warehouse" style="color:#ec4899"></i></div><div class="home-card-title">Warehouse</div><div class="home-card-desc">Browse tables and schema</div><div class="home-card-arrow"><i class="fas fa-arrow-right"></i></div></a>' +
          '<a href="#compare" class="home-card card-accent-red" onclick="App.navigate(\'#compare\');return false"><div class="home-card-icon" style="background:#fee2e2"><i class="fas fa-code-compare" style="color:#ef4444"></i></div><div class="home-card-title">Compare</div><div class="home-card-desc">Period-over-period & diff analysis</div><div class="home-card-arrow"><i class="fas fa-arrow-right"></i></div></a>' +
          '<a href="#forecast" class="home-card card-accent-indigo" onclick="App.navigate(\'#forecast\');return false"><div class="home-card-icon" style="background:#eef2ff"><i class="fas fa-chart-simple" style="color:#6366f1"></i></div><div class="home-card-title">Trend Forecast</div><div class="home-card-desc">Linear trend projection &amp; seasonality</div><div class="home-card-arrow"><i class="fas fa-arrow-right"></i></div></a>' +
        '</div>' +
        // ── Data Tables Section ──
        '<h2 class="section-title"><i class="fas fa-database mr-2"></i>Data Tables</h2>' +
        '<div class="card" id="home-tables-section">' +
          '<div class="text-center py-8 text-gray-400" id="home-tables-content"><i class="fas fa-spinner fa-spin text-lg"></i><p class="mt-2 text-sm">Loading tables...</p></div>' +
        '</div>' +
        // ── Recent Queries ──
        '<h2 class="section-title"><i class="fas fa-clock-rotate mr-2"></i>Recent Activity</h2>' +
        '<div class="card" id="home-queries-section">' +
          '<div class="text-center py-8 text-gray-400" id="home-queries-content"><p class="text-sm">No recent queries</p></div>' +
        '</div>' +
      '</div>';

      // Load data
      await this._loadStats();
      this._loadTables();
      this._loadRecentQueries();
    },

    // ── Load KPIs from health + tables API ──
    async _loadStats() {
      try {
        if (typeof API === "undefined") return;

        // Health check
        var health = null;
        try { health = await API.health(); } catch (e) { /* ignore */ }
        var isHealthy = health && (health.status === "healthy" || health.status === "ok" || health.healthy === true);
        _data.apiHealthy = isHealthy;
        var statusEl = document.getElementById("kpi-status");
        if (statusEl) {
          statusEl.textContent = isHealthy ? "Online" : "Offline";
          statusEl.style.color = isHealthy ? "#22c55e" : "#ef4444";
        }

        // Tables info
        try {
          var tblResult = await API.tables();
          var tbls = tblResult && tblResult.tables ? tblResult.tables : tblResult;
          if (tbls && typeof tbls === "object") {
            if (Array.isArray(tbls)) {
              _data.tables = tbls;
              _data.tableCount = tbls.length;
              _data.rowCount = tbls.reduce(function (sum, t) { return sum + (t.row_count || 0); }, 0);
            } else {
              _data.tables = tbls;
              _data.tableCount = Object.keys(tbls).length;
              _data.rowCount = 0;
              for (var k in tbls) {
                if (tbls.hasOwnProperty(k) && tbls[k]) _data.rowCount += tbls[k].row_count || 0;
              }
            }
          }
        } catch (e) { /* ignore */ }

        // Query count: prefer server-side history (SqlPage logs there),
        // fall back to the legacy localStorage key (namespaced per login).
        try {
          var _qhKey = (window.RBAC && window.RBAC.storeKey) ? window.RBAC.storeKey("query_history") : "query_history";
          var hist = localStorage.getItem(_qhKey);
          if (hist) {
            var parsed = JSON.parse(hist);
            _data.recentQueries = Array.isArray(parsed) ? parsed.slice(-5).reverse() : [];
          }
          if ((!_data.recentQueries || !_data.recentQueries.length) && API.listQueryHistory) {
            API.listQueryHistory(5).then(function (res) {
              var items = (res && (res.history || res.queries)) || [];
              if (Array.isArray(items) && items.length) {
                _data.recentQueries = items.slice(0, 5);
                var qEl2 = document.getElementById("kpi-queries");
                if (qEl2) qEl2.textContent = String(items.length >= 5 ? "5+" : items.length);
                this._loadRecentQueries();
              }
            }.bind(this)).catch(function () {});
          }
        } catch (e) { /* ignore */ }

        this._updateKpis();
      } catch (e) { /* silent fail */ }
    },

    _updateKpis() {
      var tblEl = document.getElementById("kpi-tables");
      if (tblEl) tblEl.textContent = String(_data.tableCount);

      var rowEl = document.getElementById("kpi-rows");
      if (rowEl) rowEl.textContent = _data.rowCount > 0 ? _data.rowCount.toLocaleString() : "0";

      var qEl = document.getElementById("kpi-queries");
      if (qEl) qEl.textContent = String(_data.recentQueries.length);
    },

    // ── Load tables list ──
    async _loadTables() {
      var container = document.getElementById("home-tables-content");
      if (!container) return;

      try {
        if (!_data.tables || _data.tableCount === 0) {
          container.innerHTML = '<div class="flex flex-col items-center py-6"><i class="fas fa-database text-3xl text-gray-300 mb-3"></i><p class="text-sm text-gray-400">No tables yet. <a href="#upload" class="text-primary-500 hover:underline">Upload your first dataset</a></p></div>';
          return;
        }

        var tbls = _data.tables;
        var html = '<div class="overflow-x-auto"><table class="data-table w-full">' +
          '<thead><tr><th>Table Name</th><th>Rows</th><th>Columns</th></tr></thead><tbody>';

        if (Array.isArray(tbls)) {
          tbls.slice(0, 10).forEach(function (t) {
            var name = t.table_name || t.name || "?";
            var rows = t.row_count != null ? t.row_count.toLocaleString() : "—";
            var cols = t.column_count != null ? t.column_count : (t.columns ? t.columns.length : "—");
            html += '<tr><td class="font-medium"><a href="#warehouse" class="text-primary-500 hover:underline">' + escapeHtml(name) + '</a></td><td>' + rows + '</td><td>' + cols + '</td></tr>';
          });
        } else {
          var count = 0;
          for (var name in tbls) {
            if (tbls.hasOwnProperty(name) && count < 10) {
              var t = tbls[name];
              var rows = t.row_count != null ? t.row_count.toLocaleString() : "—";
              var cols = t.columns ? t.columns.length : "—";
              html += '<tr><td class="font-medium"><a href="#warehouse" class="text-primary-500 hover:underline">' + escapeHtml(name) + '</a></td><td>' + rows + '</td><td>' + cols + '</td></tr>';
              count++;
            }
          }
        }

        html += '</tbody></table></div>';
        if (_data.tableCount > 10) {
          html += '<div class="text-center mt-3"><a href="#warehouse" class="text-sm text-primary-500 hover:underline">View all ' + _data.tableCount + ' tables →</a></div>';
        }
        container.innerHTML = html;
      } catch (e) {
        container.innerHTML = '<div class="error-state"><p>Failed to load tables</p></div>';
      }
    },

    // ── Load recent queries ──
    async _loadRecentQueries() {
      var container = document.getElementById("home-queries-content");
      if (!container) return;

      try {
        var queries = _data.recentQueries;
        if (!queries || queries.length === 0) {
          container.innerHTML = '<div class="flex flex-col items-center py-6"><i class="fas fa-clock text-3xl text-gray-300 mb-3"></i><p class="text-sm text-gray-400">No recent queries. Try asking a question in <a href="#chat" class="text-primary-500 hover:underline">AI Chat</a></p></div>';
          return;
        }

        var html = '<div class="space-y-1">';
        queries.forEach(function (q) {
          var text = typeof q === "string" ? q : (q.question || q.query || q.text || "");
          if (text.length > 80) text = text.substring(0, 80) + "...";
          html += '<div class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 text-sm"><i class="fas fa-message text-gray-300 text-xs"></i><span class="text-gray-600 dark:text-gray-400 flex-1 truncate">' + escapeHtml(text) + '</span></div>';
        });
        html += '</div>';
        container.innerHTML = html;
      } catch (e) {
        container.innerHTML = '<div class="error-state"><p>Could not load activity</p></div>';
      }
    },
  };

  function escapeHtml(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  window.HomePage = HomePage;
  (window._PAGES = window._PAGES || {})[PAGE] = HomePage;
})();
