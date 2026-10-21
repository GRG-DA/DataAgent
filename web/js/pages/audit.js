/**
 * Audit Page — Audit Log Viewer with search, filter, export
 */
(function () {
  "use strict";

  var PAGE = "#audit";

  var AuditPage = {
    async init() {},
    async destroy() {},

    async render() {
      var pc = document.getElementById("page-content");
      pc.innerHTML =
        '<div class="max-w-6xl mx-auto animate-fade-in space-y-4">' +
        '<div class="page-title"><div class="page-title-icon"><i class="fas fa-clipboard-list"></i></div><div><h2>Audit Log</h2></div></div>' +
        '<div class="flex gap-2 flex-wrap items-center" id="audit-filters">' +
        '<input id="audit-search" class="form-input" style="max-width:200px;padding:6px 10px;font-size:12px;" placeholder="Search...">' +
        '<select id="audit-type" class="form-select" style="max-width:140px;padding:6px 10px;font-size:12px;"><option value="">All types</option><option value="login">Login</option><option value="query">Query</option><option value="export">Export</option><option value="admin">Admin</option></select>' +
        '<button id="audit-refresh" class="btn btn-sm btn-secondary text-xs"><i class="fas fa-sync mr-1"></i>Refresh</button>' +
        '<button id="audit-export" class="btn btn-sm btn-primary text-xs"><i class="fas fa-download mr-1"></i>Export CSV</button>' +
        "</div>" +
        '<div id="audit-table-container"></div></div>';

      document.getElementById("audit-refresh")?.addEventListener("click", () => this._loadLogs());
      document.getElementById("audit-export")?.addEventListener("click", () => this._exportLogs());
      document.getElementById("audit-search")?.addEventListener("input", () => this._loadLogs());
      document.getElementById("audit-type")?.addEventListener("change", () => this._loadLogs());
      this._loadLogs();
    },

    async _loadLogs() {
      var container = document.getElementById("audit-table-container");
      if (!container) return;
      container.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-gray-400"></i></div>';

      try {
        var search = document.getElementById("audit-search")?.value || "";
        var type = document.getElementById("audit-type")?.value || "";
        var url = "/audit/logs?limit=50" + (search ? "&search=" + encodeURIComponent(search) : "") + (type ? "&action=" + encodeURIComponent(type) : "");
        // Auth middleware requires a Bearer token; bare fetch() always 401s.
        var token = (window.RBAC && window.RBAC.getToken) ? window.RBAC.getToken() : null;
        var headers = {};
        if (token) headers["Authorization"] = "Bearer " + token;
        var resp = await fetch((window.__API_BASE__ || "") + url, { headers: headers });
        if (!resp.ok) {
          var detail = "";
          try { detail = (await resp.json()).detail || ""; } catch (_) {}
          throw new Error(detail || ("HTTP " + resp.status));
        }
        var data = await resp.json();
        var logs = (data && data.logs) || [];

        if (logs.length === 0) {
          container.innerHTML = '<div class="empty-state py-8"><i class="fas fa-clipboard-list text-3xl text-gray-300"></i><p class="text-sm mt-1 text-gray-400">No audit log entries found</p></div>';
          return;
        }

        var cols = ["timestamp", "user", "action", "resource", "detail", "ip"];
        if (window.DataTable) DataTable.render(container, logs, { columns: cols, pageSize: 25, headers: { timestamp: "Time", user: "User", action: "Action", resource: "Resource", detail: "Detail", ip: "IP" } });
      } catch (e) {
        container.innerHTML = '<div class="error-state py-8"><p class="text-sm text-red-400">' + App.escapeHtml(e.message || "Failed to load audit logs") + '</p></div>';
      }
    },

    async _exportLogs() {
      try {
        var token = (window.RBAC && window.RBAC.getToken) ? window.RBAC.getToken() : null;
        var headers = {};
        if (token) headers["Authorization"] = "Bearer " + token;
        var resp = await fetch((window.__API_BASE__ || "") + "/audit/logs/export", { headers: headers });
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        var blob = await resp.blob();
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "audit_logs.csv";
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        App.error("Export failed");
      }
    },
  };

  window.AuditPage = AuditPage;
  (window._PAGES = window._PAGES || {})[PAGE] = AuditPage;
})();
