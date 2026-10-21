/**
 * DataTable v2 — Sortable, filterable, paginated table with loading/empty/export
 *
 * Backward-compatible: DataTable.render(containerId, data, options) still works.
 * New: DataTable.load(containerId, fetchFn, options) for async loading with skeleton.
 *
 * Options:
 *   columns       - Array of column keys (auto-detected if omitted)
 *   headers       - { colKey: "Display Name" }
 *   maxRows       - Max rows to display (default: 100)
 *   pageSize      - Rows per page (default: 50)
 *   searchable    - Show search input (default: false)
 *   selectable    - Show checkboxes (default: false)
 *   exportable    - Show CSV export button (default: false)
 *   loading       - Show skeleton loading (default: false)
 *   emptyMessage  - Text when no data (default: "No data")
 *   format        - { colKey: function(value) => string }
 *   onRowClick    - function(row) callback
 */
(function () {
  "use strict";

  var _states = {};

  function _getStateId(containerId) {
    return "dt_" + containerId;
  }

  var DataTable = {
    /** Render data into a container */
    render: function (containerId, data, options) {
      var container =
        typeof containerId === "string"
          ? document.getElementById(containerId)
          : containerId;
      if (!container) return;
      if (!data || data.length === 0) {
        container.innerHTML =
          '<div class="empty-state" style="padding:20px"><i class="fas fa-database text-2xl"></i><p class="text-sm mt-1">' +
          _escape((options && options.emptyMessage) || "No data") +
          "</p></div>";
        return;
      }

      options = options || {};
      var cols = options.columns || Object.keys(data[0]);
      var pageSize = options.pageSize || 50;
      var maxRows = options.maxRows || 100;
      var stateId = _getStateId(containerId);

      _states[stateId] = {
        data: data,
        cols: cols,
        options: options,
        page: 0,
        pageSize: pageSize,
        sortCol: null,
        sortDir: 1,
        searchQuery: "",
        selection: {},
        container: container,
      };

      this._renderPage(containerId, stateId);
    },

    /** Async load: show skeleton, fetch data, then render */
    load: function (containerId, fetchFn, options) {
      var container =
        typeof containerId === "string"
          ? document.getElementById(containerId)
          : containerId;
      if (!container) return Promise.resolve(null);
      var h = container.offsetHeight || 120;
      container.innerHTML =
        '<div class="skeleton" style="height:' + h + 'px;border-radius:8px;"></div>';

      var self = this;
      return Promise.resolve()
        .then(function () {
          return fetchFn();
        })
        .then(function (data) {
          self.render(containerId, data, options);
          return data;
        })
        .catch(function (err) {
          container.innerHTML =
            '<div class="error-state"><i class="fas fa-exclamation-circle"></i><p>' +
            _escape(err.message || "Error loading data") +
            "</p></div>";
          return null;
        });
    },

    _renderPage: function (containerId, stateId) {
      var state = _states[stateId];
      if (!state) return;
      var data = state.data,
        cols = state.cols,
        options = state.options;
      var page = state.page,
        pageSize = state.pageSize;
      var query = (state.searchQuery || "").toLowerCase();

      // Filter by search
      var filtered = data;
      if (query) {
        filtered = data.filter(function (row) {
          for (var i = 0; i < cols.length; i++) {
            var val = String(row[cols[i]] || "").toLowerCase();
            if (val.indexOf(query) !== -1) return true;
          }
          return false;
        });
      }

      // Sort
      if (state.sortCol) {
        var col = state.sortCol,
          dir = state.sortDir;
        filtered = filtered.slice().sort(function (a, b) {
          var va = a[col],
            vb = b[col];
          if (va == null) return 1;
          if (vb == null) return -1;
          if (typeof va === "number" && typeof vb === "number")
            return (va - vb) * dir;
          return String(va).localeCompare(String(vb)) * dir;
        });
      }

      var totalFiltered = filtered.length;
      var start = page * pageSize;
      var end = Math.min(start + pageSize, totalFiltered);
      var pageData = filtered.slice(start, end);
      var totalPages = Math.ceil(totalFiltered / pageSize);

      // Build HTML
      var html = "";

      // Toolbar: search + export
      if (options.searchable || options.exportable) {
        html +=
          '<div class="flex items-center gap-2 mb-2">';
        if (options.searchable) {
          html +=
            '<input class="form-input dt-search" data-state="' +
            stateId +
            '" type="text" placeholder="Search..." value="' +
            _escape(state.searchQuery || "") +
            '" style="max-width:220px;padding:6px 10px;font-size:12px;">';
        }
        if (options.exportable) {
          html +=
            '<button class="btn btn-sm btn-primary dt-export" data-state="' +
            stateId +
            '" style="margin-left:auto"><i class="fas fa-download mr-1"></i>CSV</button>';
        }
        html += "</div>";
      }

      // Table wrapper
      var tableCls =
        "data-table w-full text-xs" +
        (options.striped || options.alternatingBg ? " data-table-striped" : "") +
        (options.bordered ? " data-table-bordered" : "") +
        (options.condensed ? " data-table-condensed" : "");
      var wrapStyle = "overflow:auto;";
      if (options.radius != null) wrapStyle += "border-radius:" + options.radius + "px;";
      if (options.alternatingBg) wrapStyle += "--dt-alt:" + options.alternatingBg + ";";
      if (options.rowHoverBg) wrapStyle += "--dt-hover:" + options.rowHoverBg + ";";
      html += '<div class="overflow-x-auto" style="' + wrapStyle + '">';
      html += '<table class="' + tableCls + '"><thead><tr>';

      // Selection checkbox
      if (options.selectable) {
        html += '<th style="width:32px"><input type="checkbox" class="dt-select-all" data-state="' + stateId + '"></th>';
      }

      var thStyle = [];
      if (options.headerBg) thStyle.push("background:" + options.headerBg);
      if (options.headerColor) thStyle.push("color:" + options.headerColor);
      var thStyleAttr = thStyle.length ? ' style="' + thStyle.join(";") + '"' : "";
      var sortable = options.sortable !== false;
      cols.forEach(function (col) {
        var label = (options.headers && options.headers[col]) || col;
        var sortIcon = "";
        if (sortable) {
          sortIcon = "fa-sort";
          if (state.sortCol === col) {
            sortIcon = state.sortDir > 0 ? "fa-sort-up" : "fa-sort-down";
          }
          sortIcon = ' <i class="fas ' + sortIcon + ' text-xs opacity-50"></i>';
        }
        html +=
          '<th data-col="' +
          _escape(col) +
          '" class="' + (sortable ? "cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 dt-sort " : "") +
          'select-none whitespace-nowrap" data-state="' +
          stateId +
          '"' +
          thStyleAttr +
          ">" +
          _escape(label) +
          sortIcon +
          "</th>";
      });
      html += "</tr></thead><tbody>";

      if (pageData.length === 0) {
        html +=
          '<tr><td colspan="' +
          (cols.length + (options.selectable ? 1 : 0)) +
          '" class="text-center text-gray-400 py-8">No matching records</td></tr>';
      } else {
        pageData.forEach(function (row) {
          html += "<tr" + (options.onRowClick ? ' class="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50"' : "") + ">";
          if (options.selectable) {
            var rowId = row.id || JSON.stringify(row);
            var checked = state.selection[rowId] ? "checked" : "";
            html +=
              '<td><input type="checkbox" class="dt-select-row" data-row-id="' +
              _escape(rowId) +
              '" ' +
              checked +
              "></td>";
          }
          cols.forEach(function (col) {
            var val = row[col];
            var display;
            if (val === null || val === undefined)
              display = '<span class="text-gray-300 italic">NULL</span>';
            else if (typeof val === "number")
              display = options.format && options.format[col]
                ? options.format[col](val)
                : Number.isInteger(val)
                ? val.toLocaleString()
                : val.toFixed(2);
            else display = _escape(String(val));
            html +=
              '<td class="px-2 py-1.5 border-b border-gray-100 dark:border-gray-700/50 max-w-[200px] truncate">' +
              display +
              "</td>";
          });
          html += "</tr>";
        });
      }

      html += "</tbody></table></div>";

      // Pagination
      if (options.paginate !== false && totalPages > 1) {
        html +=
          '<div class="flex items-center justify-between mt-2 px-1 text-xs text-gray-500">' +
          '<span>' +
          (start + 1) +
          "-" +
          end +
          " of " +
          totalFiltered.toLocaleString() +
          " rows" +
          (totalFiltered < data.length
            ? " (filtered from " + data.length.toLocaleString() + ")"
            : "") +
          '</span>' +
          '<div class="flex items-center gap-1">' +
          '<button class="dt-page btn-sm" data-state="' +
          stateId +
          '" data-page="0" ' +
          (page === 0 ? 'disabled' : "") +
          '><i class="fas fa-angle-double-left"></i></button>' +
          '<button class="dt-page btn-sm" data-state="' +
          stateId +
          '" data-page="' +
          (page - 1) +
          '" ' +
          (page === 0 ? 'disabled' : "") +
          '><i class="fas fa-angle-left"></i></button>' +
          '<span class="px-2 font-medium">' +
          (page + 1) +
          " / " +
          totalPages +
          "</span>" +
          '<button class="dt-page btn-sm" data-state="' +
          stateId +
          '" data-page="' +
          (page + 1) +
          '" ' +
          (page >= totalPages - 1 ? 'disabled' : "") +
          '><i class="fas fa-angle-right"></i></button>' +
          '<button class="dt-page btn-sm" data-state="' +
          stateId +
          '" data-page="' +
          (totalPages - 1) +
          '" ' +
          (page >= totalPages - 1 ? 'disabled' : "") +
          '><i class="fas fa-angle-double-right"></i></button>' +
          "</div></div>";
      } else if (totalFiltered > 0) {
        html +=
          '<p class="text-xs text-gray-400 mt-1">' +
          totalFiltered.toLocaleString() +
          " row" +
          (totalFiltered !== 1 ? "s" : "") +
          "</p>";
      }

      state.container.innerHTML = html;

      // Bind events
      this._bindEvents(containerId, stateId);
    },

    _bindEvents: function (containerId, stateId) {
      var state = _states[stateId];
      if (!state) return;
      var container = state.container;

      // Sorting
      if (state.options.sortable !== false && !(state.options && state.options.sortable === false)) {
        container.querySelectorAll(".dt-sort").forEach(function (th) {
          th.addEventListener("click", function () {
            var col = th.dataset.col;
            if (state.sortCol === col) {
              state.sortDir *= -1;
            } else {
              state.sortCol = col;
              state.sortDir = 1;
            }
            state.page = 0;
            DataTable._renderPage(containerId, stateId);
          });
        });
      }

      // Search
      var searchInput = container.querySelector(".dt-search");
      if (searchInput) {
        searchInput.addEventListener("input", function () {
          state.searchQuery = this.value;
          state.page = 0;
          DataTable._renderPage(containerId, stateId);
        });
      }

      // Pagination
      container.querySelectorAll(".dt-page").forEach(function (btn) {
        btn.addEventListener("click", function () {
          if (this.disabled) return;
          state.page = parseInt(this.dataset.page);
          DataTable._renderPage(containerId, stateId);
        });
      });

      // Export CSV
      var exportBtn = container.querySelector(".dt-export");
      if (exportBtn) {
        exportBtn.addEventListener("click", function () {
          DataTable._exportCSV(stateId);
        });
      }

      // Select all
      var selectAll = container.querySelector(".dt-select-all");
      if (selectAll) {
        selectAll.addEventListener("change", function () {
          var checked = this.checked;
          var filtered = state.searchQuery
            ? state.data.filter(function (row) {
                var q = state.searchQuery.toLowerCase();
                return state.cols.some(function (c) {
                  return String(row[c] || "").toLowerCase().indexOf(q) !== -1;
                });
              })
            : state.data;
          filtered.forEach(function (row) {
            var rowId = row.id || JSON.stringify(row);
            state.selection[rowId] = checked;
          });
          DataTable._renderPage(containerId, stateId);
        });
      }

      // Row select
      container.querySelectorAll(".dt-select-row").forEach(function (cb) {
        cb.addEventListener("change", function () {
          state.selection[this.dataset.rowId] = this.checked;
        });
      });

      // Row click
      if (state.options.onRowClick) {
        container.querySelectorAll("tbody tr").forEach(function (tr, idx) {
          tr.addEventListener("click", function (e) {
            if (e.target.closest("input, button, a")) return;
            var pageData = DataTable._getPageData(state);
            if (pageData[idx]) state.options.onRowClick(pageData[idx]);
          });
        });
      }
    },

    _getPageData: function (state) {
      var data = state.data;
      if (state.searchQuery) {
        var q = state.searchQuery.toLowerCase();
        data = data.filter(function (row) {
          return state.cols.some(function (c) {
            return String(row[c] || "").toLowerCase().indexOf(q) !== -1;
          });
        });
      }
      if (state.sortCol) {
        var col = state.sortCol,
          dir = state.sortDir;
        data = data.slice().sort(function (a, b) {
          var va = a[col],
            vb = b[col];
          if (va == null) return 1;
          if (vb == null) return -1;
          if (typeof va === "number" && typeof vb === "number")
            return (va - vb) * dir;
          return String(va).localeCompare(String(vb)) * dir;
        });
      }
      return data.slice(
        state.page * state.pageSize,
        (state.page + 1) * state.pageSize
      );
    },

    _exportCSV: function (stateId) {
      var state = _states[stateId];
      if (!state) return;
      var cols = state.cols;
      var headers = state.options.headers || {};
      var rows = state.data;

      // Build CSV
      var csv =
        cols
          .map(function (c) {
            return '"' + (headers[c] || c) + '"';
          })
          .join(",") + "\n";
      rows.forEach(function (row) {
        csv +=
          cols
            .map(function (c) {
              var val = row[c];
              if (val === null || val === undefined) return "";
              return '"' + String(val).replace(/"/g, '""') + '"';
            })
            .join(",") + "\n";
      });

      // Download
      var blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "export.csv";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },

    /** Get selected rows */
    getSelected: function (containerId) {
      var state = _states[_getStateId(containerId)];
      if (!state) return [];
      return state.data.filter(function (row) {
        var rowId = row.id || JSON.stringify(row);
        return state.selection[rowId];
      });
    },
  };

  // Backward compatibility — _goPage and _sortColumn
  DataTable._goPage = function (containerId, stateId, page) {
    var state = _states[stateId];
    if (!state) return;
    var totalPages = Math.ceil(state.data.length / state.pageSize);
    state.page = Math.max(0, Math.min(page, totalPages - 1));
    this._renderPage(containerId, stateId);
  };

  DataTable._sortColumn = function (containerId, col) {
    for (var id in _states) {
      if (id.indexOf(containerId) !== -1) {
        var state = _states[id];
        state.sortCol = col;
        state.sortDir = state.sortDir > 0 ? -1 : 1;
        state.page = 0;
        this._renderPage(containerId, id);
        break;
      }
    }
  };

  window.DataTable = DataTable;

  function _escape(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }
})();
