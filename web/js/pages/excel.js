(function () {
  "use strict";

  var MAX_ROWS = 100000;

  var AI_CHIPS = [
    { label: "Clean", prompt: "Analyze data quality issues and clean this table -- check for duplicates, missing values, and wrong data types. Summarize what you find." },
    { label: "Top 10", prompt: "Show the top 10 rows sorted by the most important metric in this table and explain them." },
    { label: "Pivot", prompt: "Build a pivot-style summary of this table: group by its main category and show totals/averages for the key numeric column." },
    { label: "Bar chart", prompt: "Create a bar chart of the most interesting metric in this table and explain what it shows." },
    { label: "Why dropped?", prompt: "Do a root-cause analysis on this table: what drove the biggest change or drop in the data?" },
    { label: "Report", prompt: "Write a short executive report for this table: key metrics, trends, insights, and recommendations." },
  ];

  var ExcelPage = {
    _grid: null,
    _currentTable: "",
    _currentColumns: [],
    _pivotColumns: null,
    _pivotMatrix: null,
    _sessionId: "excel-" + Date.now().toString(36),
    _chatHistory: [],
    _chartIds: [],
    _aiWaiting: false,
    _dirty: false,
    _tableSheets: null,
    _showAI: false,
    _showTools: false,
    _showFindBar: false,
    _sharedClipboard: null,
    _showFilterBar: false,
    _debug: { lastAction: null, errors: [], version: "v3" },
    _askTimeoutMs: 90000,
    _condRules: [],
    _condManual: {},
    _cfCovered: {},
    _cfSeq: 0,
    _cfApplying: false,
    _cfAllowed: false,
    _lastCfRefresh: 0,
    _condRangeMode: false,

    // === Lifecycle ===

    async init() {
      try { await App.refreshTables(); } catch (e) { /* non-fatal */ }
      try {
        var base = (window.__API_BASE__ || "").replace(/\/+$/, "");
        var cfg = await fetch(base + "/config").then(function (r) { return r.json(); });
        if (cfg && cfg.ask_timeout_seconds > 0) this._askTimeoutMs = cfg.ask_timeout_seconds * 1000 + 5000;
      } catch (e) { /* keep default */ }
    },

    destroy() {
      this._chartIds.forEach(function (id) {
        try { if (window.Charts) window.Charts.destroy(id); } catch (e) { /* noop */ }
      });
      this._chartIds = [];
      if (this._selectionPollId) { clearInterval(this._selectionPollId); this._selectionPollId = null; }
      this._selectionBound = false;
      if (window.FormulaBar && typeof window.FormulaBar.destroy === "function") {
        try { window.FormulaBar.destroy(); } catch (e) { /* noop */ }
      }
      if (this._grid && typeof this._grid.destroy === "function") {
        try { this._grid.destroy(); } catch (e) { /* noop */ }
      }
      this._grid = null;
      if (this._keydownHandler) {
        document.removeEventListener("keydown", this._keydownHandler);
        this._keydownHandler = null;
      }
      if (this._copyHandler) {
        document.removeEventListener("copy", this._copyHandler);
        this._copyHandler = null;
      }
      if (this._pasteHandler) {
        document.removeEventListener("paste", this._pasteHandler);
        this._pasteHandler = null;
      }
      if (this._docClickHandler) {
        document.removeEventListener("click", this._docClickHandler);
        this._docClickHandler = null;
      }
      this._showAI = false;
      this._showTools = false;
      this._showFindBar = false;
      this._showFilterBar = false;
      this._filterBackup = null;
      this._findMatches = [];
      this._findPos = -1;
      this._findQuery = "";
    },

    // === Render ===

    async render() {
      var pc = document.getElementById("page-content");
      var tables = App.state.tables || {};
      var tableNames = Object.keys(tables);

      this._loadFormulaScripts();

      pc.innerHTML = '<div class="xl-page" id="xl-page">' +
        this._buildRibbon(tableNames) +
        this._buildFindBar() +
        this._buildFilterBar() +
        '<div class="xl-main">' +
          '<div class="xl-grid-area" id="xl-grid-area">' +
            '<div class="xl-grid-wrapper" id="xl-grid-wrapper">' +
              '<div id="xl-formula-bar-slot"></div>' +
              '<div class="xl-grid-container" id="xl-grid"></div>' +
              '<div class="xl-drop-overlay hidden" id="xl-drop-overlay"><div class="xl-drop-overlay-inner"><i class="fas fa-cloud-arrow-up"></i><p>Drop your file here (.xlsx, .csv, .json)</p></div></div>' +
            '</div>' +
            '<div class="xl-sheet-tabs" id="xl-sheet-tabs">' +
              '<div class="xl-sheet-tabs-list" id="xl-sheet-tabs-list"></div>' +
              '<button class="xl-sheet-tabs-add" id="xl-sheet-tabs-add" title="Add new sheet"><i class="fas fa-plus"></i></button>' +
            '</div>' +
            '<div class="xl-status-bar" id="xl-status-bar">' +
              '<div class="xl-status-left" id="xl-status-left"><span id="xl-status-info" class="xl-status-info"></span></div>' +
              '<div class="xl-status-right" id="xl-status-right">' +
                '<span class="xl-status-stat" id="xl-stat-min"></span>' +
                '<span class="xl-status-stat" id="xl-stat-max"></span>' +
                '<span class="xl-status-stat" id="xl-stat-avg"></span>' +
                '<span class="xl-status-stat" id="xl-stat-count"></span>' +
                '<span class="xl-status-stat" id="xl-stat-ncount"></span>' +
                '<span class="xl-status-stat" id="xl-stat-sum"></span>' +
              '</div>' +
            '</div>' +
            '<div class="xl-analysis-bar flex flex-wrap items-center gap-2">' +
              '<span class="xl-analysis-label"><i class="fas fa-layer-group mr-1"></i>Analysis sheet:</span>' +
              '<input type="text" id="xl-analysis-sheet" class="xl-analysis-input" placeholder="analysis" title="Clean / Pivot / Merge / AI results are appended here as labeled sections." />' +
              '<span class="text-xs text-secondary">All tasks append here with section labels.</span>' +
            '</div>' +
            '<div class="xl-tools hidden" id="xl-tools-section">' +
              this._cleanCard() + this._pivotCard() + this._mergeCard() +
              this._chartCard() + this._reportCard() + this._exportCard() +
              this._statsCard() + this._insightsCard() + this._forecastCard() +
            '</div>' +
          '</div>' +
          '<div class="xl-ai-sidebar hidden" id="xl-ai-sidebar">' +
            '<div class="xl-ai-card card">' +
              '<div class="xl-ai-header"><h3><i class="fas fa-wand-magic-sparkles mr-1 text-primary-500"></i>AI Assistant</h3>' +
              '<button id="xl-ai-clear" class="xl-ai-header-btn" title="Clear conversation"><i class="fas fa-trash"></i></button></div>' +
              '<div class="xl-ai-messages" id="xl-ai-messages"><div class="xl-ai-bubble ai" id="xl-ai-empty">Ask me to do Excel-style tasks on the selected table -- clean it, pivot it, chart it, or explain it.</div></div>' +
              '<div class="xl-ai-chips" id="xl-ai-chips"></div>' +
              '<div class="xl-ai-input-row"><input id="xl-ai-input" class="xl-ai-input" placeholder="e.g. Show monthly revenue trend..." autocomplete="off" />' +
              '<button id="xl-ai-send" class="xl-ai-send-btn"><i class="fas fa-paper-plane"></i></button></div>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<input type="file" id="xl-file-input" accept=".xlsx,.csv,.json,.xls" style="display:none;" />' +
        this._buildCondFmtModal() +
        this._buildSortModal() +
        this._buildFreezeDialog() +
        this._buildDupsDialog() +
        this._buildTextColsModal() +
        this._buildGoToDialog() +
        this._buildCommentModal() +
        this._buildValidationModal() +
        this._buildContextMenu() +
        this._buildCommentPopup() +
        this._buildExportModal() +
        this._buildPrintModal() +
        this._buildChartCopyModal() +
        this._buildFormatModal() +
        this._buildDefineNameModal() +
      '</div>';

this._bindShell();
      this._bindCondFmt();
      this._bindMerge();
      this._renderChips();
      this._initGrid(document.getElementById("xl-grid"));
      this._bindAIPanel();
      this._setupFileImport();

      var first = tableNames[0] || "";
      if (first) {
        this._loadTable(first);
        var sel = document.getElementById("xl-table");
        if (sel) sel.value = first;
        var self = this;
        setTimeout(function () {
          var rowSel = document.getElementById("xl-pivot-row");
          if (rowSel && rowSel.options.length <= 1) {
            var gridCols = self._currentColumns || [];
            if (!gridCols.length && self._grid) {
              var sheets = self._grid.getData() || [];
              var s = sheets[0];
              if (s && s.rows && s.rows[0] && s.rows[0].cells) {
                gridCols = Object.keys(s.rows[0].cells)
                  .map(Number).sort(function (a, b) { return a - b; })
                  .map(function (ci) { return String(s.rows[0].cells[ci].text || "Col" + (ci + 1)); });
              }
            }
            if (gridCols.length) self._fillColumnDropdowns(gridCols);
          }
        }, 1500);
      } else {
        this._setGridNote("No warehouse tables yet -- upload a file on the Upload page, import a file, or come back.");
      }
    },

    // === Ribbon ===

    _buildRibbon(tableNames) {
      var tableOpts = '<option value="">Choose a table...</option>' +
        (tableNames || []).map(function (t) {
          var tables = App.state.tables || {};
          var meta = tables[t] || {};
          var rows = meta.row_count != null ? " - " + Number(meta.row_count).toLocaleString() + " rows" : "";
          return '<option value="' + this._escape(t) + '">' + this._escape(t) + rows + '</option>';
        }.bind(this)).join("");

      function grp(label, inner) {
        return '<div class="xl-ribbon-group">' +
          '<span class="xl-ribbon-label">' + label + '</span>' +
          '<div class="xl-ribbon-btns">' + inner + '</div>' +
        '</div>';
      }
      function sep() { return '<div class="xl-ribbon-sep"></div>'; }
      function sepSm() { return '<div class="xl-ribbon-sep-sm"></div>'; }
      function btn(id, icon, text, title) {
        var inner = '<i class="fas ' + icon + '"></i>';
        if (text) inner += ' <span>' + text + '</span>';
        return '<button class="xl-ribbon-btn" id="' + id + '" title="' + (title || text || '') + '">' + inner + '</button>';
      }
      function btnAccent(id, icon, text, title) {
        var inner = '<i class="fas ' + icon + '"></i>';
        if (text) inner += ' <span>' + text + '</span>';
        return '<button class="xl-ribbon-btn xl-ribbon-btn-accent" id="' + id + '" title="' + (title || text || '') + '">' + inner + '</button>';
      }

      return '<div class="xl-ribbon" id="xl-ribbon">' +
        grp('File',
          btn('xl-btn-open', 'fa-folder-open', 'Open', 'Open a local file (.xlsx, .csv, .json)') +
          btn('xl-btn-save', 'fa-download', 'Save', 'Save grid as .xlsx (Ctrl+S)') +
          btn('xl-btn-csv', 'fa-file-csv', 'CSV', 'Export backend table as CSV')
        ) +
        sep() +
        grp('Export & Print',
          btn('xl-btn-export', 'fa-file-export', 'Export', 'Export grid as .xlsx (keeps formatting / selected range)') +
          btn('xl-btn-print', 'fa-print', 'Print', 'Print preview with page breaks (Ctrl+P)') +
          btn('xl-btn-chart-copy', 'fa-camera', 'Chart', 'Copy chart image to clipboard')
        ) +
        sep() +
        grp('Edit',
          btn('xl-btn-undo', 'fa-undo', '', 'Undo (Ctrl+Z)') +
          btn('xl-btn-redo', 'fa-redo', '', 'Redo (Ctrl+Y)') +
          sepSm() +
          btn('xl-btn-find-toggle', 'fa-magnifying-glass', 'Find', 'Find and Replace (Ctrl+F)')
        ) +
        sep() +
        grp('Cells',
          btn('xl-btn-freeze', 'fa-thumbtack', 'Freeze', 'Freeze / unfreeze header row') +
          sepSm() +
          btn('xl-btn-add-row', 'fa-plus', 'Add', 'Insert row after selection') +
          btn('xl-btn-del-row', 'fa-minus', 'Del', 'Delete selected row')
        ) +
        sep() +
        grp('Sort & Filter',
          btn('xl-btn-sort-asc', 'fa-sort-alpha-down', '', 'Sort column A-Z') +
          btn('xl-btn-sort-desc', 'fa-sort-alpha-up', '', 'Sort column Z-A') +
          btn('xl-btn-sort-multi', 'fa-arrow-up-1-9', '', 'Multi-column sort') +
          sepSm() +
          btn('xl-btn-filter-toggle', 'fa-filter', 'Filter', 'Toggle AutoFilter on headers')
        ) +
        sep() +
        grp('Format',
          btn('xl-btn-bold', 'fa-bold', '', 'Bold (Ctrl+B)') +
          btn('xl-btn-italic', 'fa-italic', '', 'Italic (Ctrl+I)') +
          btn('xl-btn-underline', 'fa-underline', '', 'Underline (Ctrl+U)') +
          sepSm() +
          '<div class="xl-color-btn-wrap" id="xl-font-color-wrap">' +
            '<button class="xl-ribbon-btn" id="xl-btn-font-color" title="Font color">' +
              '<i class="fas fa-font"></i>' +
              '<span class="xl-color-indicator" id="xl-font-color-indicator" style="background:#000000;"></span>' +
            '</button>' +
          '</div>' +
          '<div class="xl-color-btn-wrap" id="xl-bg-color-wrap">' +
            '<button class="xl-ribbon-btn" id="xl-btn-bg-color" title="Cell background color">' +
              '<i class="fas fa-fill-drip"></i>' +
              '<span class="xl-color-indicator" id="xl-bg-color-indicator" style="background:#ffff00;"></span>' +
            '</button>' +
          '</div>' +
          sepSm() +
          btn('xl-btn-align-left', 'fa-align-left', '', 'Align left') +
          btn('xl-btn-align-center', 'fa-align-center', '', 'Align center') +
          btn('xl-btn-align-right', 'fa-align-right', '', 'Align right') +
          sepSm() +
          '<select class="xl-ribbon-select" id="xl-num-format" title="Number format">' +
            '<option value="general">General</option>' +
            '<option value="number">Number</option>' +
            '<option value="currency">Currency</option>' +
            '<option value="percent">Percent</option>' +
            '<option value="date">Date</option>' +
            '<option value="comma">Comma</option>' +
          '</select>' +
          sepSm() +
          btn('xl-btn-borders-all', 'fa-border-all', '', 'Apply all borders') +
          btn('xl-btn-borders-none', 'fa-border-none', '', 'Remove all borders') +
          sepSm() +
          btn('xl-btn-autowidth', 'fa-arrows-left-right-to-line', '', 'Auto-fit column width') +
          sepSm() +
          btn('xl-btn-condfmt', 'fa-palette', '', 'Conditional Formatting (cell rules, color scales, data bars, icon sets)')
        ) +
        sep() +
        grp('Names',
          '<select class="xl-ribbon-select" id="xl-name-box" title="Named ranges — select to jump"><option value="">(None)</option></select>' +
          sepSm() +
          btn('xl-btn-def-name', 'fa-tag', 'Define', 'Define or manage named ranges')
        ) +
        sep() +
        grp('Formula',
          btnAccent('xl-btn-autosum', 'fa-sigma', 'AutoSum', 'Insert SUM formula (Alt+=)')
        ) +
        sep() +
        grp('Data',
          '<select class="xl-ribbon-select" id="xl-table" title="Choose a warehouse table to open in the grid">' + tableOpts + '</select>' +
          sepSm() +
          btn('xl-btn-new-sheet', 'fa-plus', 'Sheet', 'Add a blank sheet (Ctrl+N)')
        ) +
        sep() +
        grp('Data Tools',
          btn('xl-btn-remove-dups', 'fa-clone', '', 'Remove duplicate rows (by selected columns)') +
          btn('xl-btn-text-cols', 'fa-columns', '', 'Text to Columns (split by delimiter)') +
          sepSm() +
          btn('xl-btn-goto', 'fa-location-crosshairs', '', 'Go To / Go To Special (Ctrl+G)')
        ) +
        sep() +
        grp('Annotations',
          btn('xl-btn-comment', 'fa-comment', '', 'Add / edit comment on the selected cell') +
          btn('xl-btn-validation', 'fa-list-check', '', 'Data validation: restrict cell input to a list')
        ) +
        sep() +
        grp('View',
          btn('xl-btn-ai-toggle', 'fa-wand-magic-sparkles', 'AI', 'Toggle AI Assistant panel') +
          btn('xl-btn-tools-toggle', 'fa-toolbox', 'Tools', 'Toggle analysis tools panel')
        ) +
      '</div>';
    },

    _buildFindBar() {
      return '<div class="xl-find-bar hidden" id="xl-find-bar">' +
        '<div class="xl-find-bar-inner">' +
          '<input class="xl-find-input" id="xl-find-val" placeholder="Find..." />' +
          '<input class="xl-find-input" id="xl-replace-val" placeholder="Replace..." />' +
          '<button class="xl-find-btn" id="xl-btn-find-next"><i class="fas fa-arrow-down"></i> Find</button>' +
          '<button class="xl-find-btn" id="xl-btn-replace-all">Replace All</button>' +
          '<button class="xl-find-btn xl-find-close" id="xl-btn-find-close"><i class="fas fa-xmark"></i></button>' +
        '</div>' +
      '</div>';
    },

    _buildFilterBar() {
      return '<div class="xl-filter-bar hidden" id="xl-filter-bar">' +
        '<div class="xl-filter-bar-inner">' +
          '<select class="xl-filter-col-select" id="xl-filter-col"></select>' +
          '<input class="xl-find-input" id="xl-filter-val" placeholder="Value..." />' +
          '<button class="xl-find-btn" id="xl-btn-filter-apply"><i class="fas fa-check"></i> Apply</button>' +
          '<button class="xl-find-btn" id="xl-btn-filter-clear"><i class="fas fa-ban"></i> Clear</button>' +
          '<button class="xl-find-btn xl-find-close" id="xl-btn-filter-close"><i class="fas fa-xmark"></i></button>' +
        '</div>' +
      '</div>';
    },

    // === Shell Binding ===

    _bindShell() {
      var self = this;
      var bind = function (id, fn) {
        var el = document.getElementById(id);
        if (el) el.addEventListener("click", function () { fn(); });
      };

      // Table selector
      var tableSel = document.getElementById("xl-table");
      if (tableSel) tableSel.addEventListener("change", function () { self._loadTable(tableSel.value); });

      // File ribbon
      bind("xl-btn-open", function () { self._openFile(); });
      bind("xl-btn-save", function () { self.saveGridXlsx(); });
      bind("xl-btn-csv", function () { self.exportBackend("csv"); });

      // Edit ribbon
      bind("xl-btn-undo", function () { self.undoGrid(); });
      bind("xl-btn-redo", function () { self.redoGrid(); });
      bind("xl-btn-find-toggle", function () { self._toggleFindBar(); });

      // Cells ribbon
      bind("xl-btn-freeze", function () { self._openFreezeDialog(); });
      bind("xl-btn-add-row", function () { self.insertRow(); });
      bind("xl-btn-del-row", function () { self.deleteRow(); });

      // Sort & Filter ribbon
      bind("xl-btn-sort-asc", function () { self.sortColumn(true); });
      bind("xl-btn-sort-desc", function () { self.sortColumn(false); });
      bind("xl-btn-sort-multi", function () { self._openSortModal(); });
      bind("xl-btn-filter-toggle", function () { self._toggleAutoFilter(); });

      // Formula ribbon
      bind("xl-btn-autosum", function () { self.autoSum(); });

      // Data ribbon
      bind("xl-btn-new-sheet", function () { self._tryNewSheet(); });

      // Data Tools ribbon
      bind("xl-btn-remove-dups", function () { self._openDupsDialog(); });
      bind("xl-btn-text-cols", function () { self._openTextColsModal(); });
      bind("xl-btn-goto", function () { self._openGoToDialog(); });

      // Custom sheet tabs
      bind("xl-sheet-tabs-add", function () { self._tryNewSheet(); });

      // View ribbon
      bind("xl-btn-ai-toggle", function () { self._toggleAI(); });
      bind("xl-btn-tools-toggle", function () { self._toggleTools(); });

      this._bindFormatToolbar();

      // Find bar buttons
      bind("xl-btn-find-next", function () { self.findNext(); });
      bind("xl-btn-replace-all", function () { self.replaceAll(); });
      bind("xl-btn-find-close", function () { self._toggleFindBar(); });
      var fv = document.getElementById("xl-find-val");
      if (fv) fv.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); self.findNext(); } });

      // Sort modal bindings
      bind("xl-sort-close", function () { self._closeSortModal(); });
      bind("xl-sort-cancel", function () { self._closeSortModal(); });
      bind("xl-sort-apply", function () { self._applyMultiSort(); });
      bind("xl-sort-add-level", function () { self._addSortLevel(); });

      // Freeze dialog bindings
      bind("xl-freeze-close", function () { self._closeFreezeDialog(); });
      bind("xl-freeze-cancel", function () { self._closeFreezeDialog(); });
      bind("xl-freeze-apply", function () { self._applyFreeze(); });

      // Remove Duplicates dialog bindings
      bind("xl-dups-close", function () { self._closeDupsDialog(); });
      bind("xl-dups-cancel", function () { self._closeDupsDialog(); });
      bind("xl-dups-apply", function () { self._applyRemoveDups(); });

      // Text to Columns modal bindings
      bind("xl-tc-close", function () { self._closeTextColsModal(); });
      bind("xl-tc-cancel", function () { self._closeTextColsModal(); });
      bind("xl-tc-apply", function () { self._applyTextToColumns(); });
      bind("xl-tc-preview", function () { self._previewTextToColumns(); });
      var tcDelim = document.getElementById("xl-tc-delim");
      if (tcDelim) tcDelim.addEventListener("change", function () { self._previewTextToColumns(); });
      var tcCol = document.getElementById("xl-tc-col");
      if (tcCol) tcCol.addEventListener("change", function () { self._previewTextToColumns(); });

      // Go To dialog bindings
      bind("xl-goto-close", function () { self._closeGoToDialog(); });
      bind("xl-goto-cancel", function () { self._closeGoToDialog(); });
      bind("xl-goto-go", function () { self._gotoRun(); });
      var gotoRef = document.getElementById("xl-goto-ref");
      if (gotoRef) gotoRef.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); self._gotoRun(); } });
      bind("xl-goto-last", function () { self._gotoSpecial("last"); });
      bind("xl-goto-blanks", function () { self._gotoSpecial("blanks"); });
      bind("xl-goto-formulas", function () { self._gotoSpecial("formulas"); });
      bind("xl-goto-constants", function () { self._gotoSpecial("constants"); });

      // Annotations ribbon
      bind("xl-btn-comment", function () { self._addCommentForSelection(); });
      bind("xl-btn-validation", function () { self._openValidationForSelection(); });

      // Comment modal + context menu bindings
      bind("xl-note-close", function () { self._closeCommentModal(); });
      bind("xl-note-cancel", function () { self._closeCommentModal(); });
      bind("xl-note-save", function () { self._saveComment(); });
      bind("xl-note-delete", function () { self._deleteComment(); });
      bind("xl-ctx-comment", function () { self._ctxAddComment(); });
      bind("xl-ctx-delete-comment", function () { self._ctxDeleteComment(); });
      bind("xl-ctx-validation", function () { self._ctxValidation(); });
      bind("xl-ctx-clear-validation", function () { self._ctxClearValidation(); });
      bind("xl-ctx-cut", function () { self._ctxCut(); });
      bind("xl-ctx-copy", function () { self._ctxCopy(); });
      bind("xl-ctx-paste", function () { self._ctxPaste(); });
      bind("xl-ctx-insert-row", function () { self._ctxInsertRow(); });
      bind("xl-ctx-delete-row", function () { self._ctxDeleteRow(); });
      bind("xl-ctx-insert-col", function () { self._ctxInsertCol(); });
      bind("xl-ctx-delete-col", function () { self._ctxDeleteCol(); });
      bind("xl-ctx-format", function () { self._ctxFormat(); });
      bind("xl-ctx-name", function () { self._ctxDefineName(); });

      // Format Cells modal
      bind("xl-fmt-close", function () { self._closeFormatModal(); });
      bind("xl-fmt-cancel", function () { self._closeFormatModal(); });
      bind("xl-fmt-apply", function () { self._applyFormatModal(); });
      bind("xl-fmt-border-all", function () {
        var data = self._gridData();
        if (!data) return App.error("Load a table first");
        data.setSelectedCellAttr("border", { mode: "all", color: "#000000", style: "thin" });
        if (typeof self._grid.reRender === "function") self._grid.reRender();
      });
      bind("xl-fmt-border-none", function () {
        var data = self._gridData();
        if (!data) return App.error("Load a table first");
        data.setSelectedCellAttr("border", { mode: "none" });
        if (typeof self._grid.reRender === "function") self._grid.reRender();
      });
      self._bindFmtColorPicker("xl-fmt-btn-font-color-palette", "xl-fmt-btn-font-color", "color", "xl-fmt-font-color-indicator", "#000000");
      self._bindFmtColorPicker("xl-fmt-btn-bg-color-palette", "xl-fmt-btn-bg-color", "bgcolor", "xl-fmt-bg-color-indicator", "#ffff00");

      // Named ranges modal
      bind("xl-name-close", function () { self._closeDefineNameModal(); });
      bind("xl-name-close2", function () { self._closeDefineNameModal(); });
      bind("xl-name-save", function () { self._saveNamedRange(); });
      var nameBox = document.getElementById("xl-name-box");
      if (nameBox) nameBox.addEventListener("change", function () {
        if (nameBox.value) self._gotoNamed(nameBox.value);
      });
      bind("xl-btn-def-name", function () { self._openDefineNameModal(); });

      // Validation modal bindings
      bind("xl-val-close", function () { self._closeValidationModal(); });
      bind("xl-val-cancel", function () { self._closeValidationModal(); });
      bind("xl-val-save", function () { self._saveValidation(); });
      bind("xl-val-remove", function () { self._removeValidation(); });

      // Right-click context menu on the grid
      var gridHost = document.getElementById("xl-grid");
      if (gridHost && this._ctxMenuHandler) gridHost.removeEventListener("contextmenu", this._ctxMenuHandler);
      this._ctxMenuHandler = function (e) { self._onGridContextMenu(e); };
      if (gridHost) gridHost.addEventListener("contextmenu", this._ctxMenuHandler);
      if (this._ctxDocHideHandler) document.removeEventListener("mousedown", this._ctxDocHideHandler);
      this._ctxDocHideHandler = function (e) {
        var menu = document.getElementById("xl-ctx-menu");
        if (menu && !menu.contains(e.target)) self._hideContextMenu();
      };
      document.addEventListener("mousedown", this._ctxDocHideHandler);
      if (this._ctxScrollHideHandler) gridHost && gridHost.removeEventListener("scroll", this._ctxScrollHideHandler);
      if (gridHost) {
        this._ctxScrollHideHandler = function () { self._hideContextMenu(); };
        gridHost.addEventListener("scroll", this._ctxScrollHideHandler, true);
      }

      // Export & Print ribbon
      bind("xl-btn-export", function () { self._openExportModal(); });
      bind("xl-btn-print", function () { self._openPrintModal(); });
      bind("xl-btn-chart-copy", function () { self._openChartCopyModal(); });

      // Export modal bindings
      bind("xl-export-close", function () { self._closeExportModal(); });
      bind("xl-export-cancel", function () { self._closeExportModal(); });
      bind("xl-export-run", function () { self._runExport(); });
      var expSel = document.getElementById("xl-export-sheet");
      if (expSel) expSel.addEventListener("change", function () { self._syncExportRangeField(); });

      // Print modal bindings
      bind("xl-print-close", function () { self._closePrintModal(); });
      bind("xl-print-cancel", function () { self._closePrintModal(); });
      bind("xl-print-do", function () { self._doPrint(); });

      // Chart copy modal bindings
      bind("xl-chartcopy-close", function () { self._closeChartCopyModal(); });
      bind("xl-chartcopy-cancel", function () { self._closeChartCopyModal(); });

      // Event delegation for "Run with AI" buttons
      if (this._docClickHandler) document.removeEventListener("click", this._docClickHandler);
      this._docClickHandler = function (e) {
        var btn = e.target.closest(".xl-run-ai");
        if (!btn) return;
        e.preventDefault();
        var prompt = btn.getAttribute("data-prompt");
        if (prompt) self.aiTask(prompt);
      };
      document.addEventListener("click", this._docClickHandler);

      // Global keyboard shortcuts
      if (this._keydownHandler) document.removeEventListener("keydown", this._keydownHandler);
      this._keydownHandler = function (e) { self._handleShortcut(e); };
      document.addEventListener("keydown", this._keydownHandler);

      if (this._copyHandler) document.removeEventListener("copy", this._copyHandler);
      this._copyHandler = function (e) { self._onCopy(e); };
      document.addEventListener("copy", this._copyHandler);

      if (this._pasteHandler) document.removeEventListener("paste", this._pasteHandler);
      this._pasteHandler = function (e) { self._onPaste(e); };
      document.addEventListener("paste", this._pasteHandler);
    },

    _handleShortcut(e) {
      var ctrl = e.ctrlKey || e.metaKey;
      if (!this._grid) return;
      if (ctrl && (e.key === "ArrowUp" || e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "Home" || e.key === "End")) {
        var modalOpen = document.querySelector(".xl-modal-mask:not(.hidden)") != null;
        if (modalOpen) return;
        e.preventDefault();
        this._navDataEdge(e.key);
        return;
      }
      if (!ctrl) return;
      var key = e.key.toLowerCase();
      if (key === "s") { e.preventDefault(); this.saveGridXlsx(); }
      else if (key === "n") { e.preventDefault(); this._tryNewSheet(); }
      else if (key === "e") { e.preventDefault(); this.exportGridXlsx(); }
      else if (key === "f") {
        var ae = document.activeElement;
        if (ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA")) return;
        e.preventDefault();
        this._toggleFindBar();
      }
      else if (key === "z" || key === "y") {
        var ae2 = document.activeElement;
        if (ae2 && (ae2.tagName === "INPUT" || ae2.tagName === "TEXTAREA")) return;
        e.preventDefault();
        if (key === "z") this.undoGrid();
        else this.redoGrid();
      }
      else if (key === "b") {
        var ae3 = document.activeElement;
        if (ae3 && (ae3.tagName === "INPUT" || ae3.tagName === "TEXTAREA")) return;
        e.preventDefault();
        this._toggleFormat("bold");
      }
      else if (key === "i") {
        var ae4 = document.activeElement;
        if (ae4 && (ae4.tagName === "INPUT" || ae4.tagName === "TEXTAREA")) return;
        e.preventDefault();
        this._toggleFormat("italic");
      }
      else if (key === "u") {
        var ae5 = document.activeElement;
        if (ae5 && (ae5.tagName === "INPUT" || ae5.tagName === "TEXTAREA")) return;
        e.preventDefault();
        this._toggleFormat("underline");
      }
      else if (key === "1" && e.shiftKey) {
        var ae6 = document.activeElement;
        if (ae6 && (ae6.tagName === "INPUT" || ae6.tagName === "TEXTAREA")) return;
        e.preventDefault();
        this._applyFormat("format", "0.00");
        var nf = document.getElementById("xl-num-format");
        if (nf) nf.value = "number";
      }
    },
    _onCopy(e) {
      if (!this._grid) return;
      var ae = document.activeElement;
      if (ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.isContentEditable)) return;
      try {
        var sel = this._getSelection();
        if (!sel) return;
        var sheet = this._grid.sheet;
        var data = (sheet && sheet.data) || null;
        if (!data) return;
        var range = sel.range || null;
        var sri = sel.row, sci = sel.col, eri = sel.row, eci = sel.col;
        if (range) {
          if (range.sri != null && range.eri != null) { sri = range.sri; eri = range.eri; sci = range.sci; eci = range.eci; }
          else if (range.ri != null && range.rh != null) { sri = range.ri; eri = range.ri + range.rh - 1; sci = range.ci; eci = range.ci + range.rw - 1; }
        }
        if (sri === eri && sci === eci) { eri = Math.min(sri + 10, 199); eci = Math.min(sci + 4, 25); }
        var tsvLines = [];
        var rows = [];
        for (var r = sri; r <= eri; r++) {
          var row = [];
          var tsvRow = [];
          for (var c = sci; c <= eci; c++) {
            var cell = data.getCell(r, c);
            var val = cell && cell.text != null ? String(cell.text) : "";
            row.push(val);
            tsvRow.push(val.replace(/\t/g, " ").replace(/\n/g, " "));
          }
          tsvLines.push(tsvRow.join("\t"));
          rows.push(row);
        }
        this._sharedClipboard = { data: rows };
        var tsvText = tsvLines.join("\n");
        if (e.clipboardData) {
          e.clipboardData.setData("text/plain", tsvText);
          e.preventDefault();
        }
      } catch (err) { console.warn("Copy failed:", err); }
    },
    _onPaste(e) {
      if (!this._grid) return;
      var ae = document.activeElement;
      if (ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.isContentEditable)) return;
      if (!this._sharedClipboard) return;
      e.preventDefault();
      try {
        var sheet = this._grid.sheet;
        var data = (sheet && sheet.data) || null;
        if (!data) return;
        var sel = this._getSelection();
        var ri = sel ? sel.row : 0;
        var ci = sel ? sel.col : 0;
        var clipData = this._sharedClipboard.data;
        for (var r = 0; r < clipData.length; r++) {
          for (var c = 0; c < clipData[r].length; c++) {
            data.setCellText(ri + r, ci + c, clipData[r][c]);
          }
        }
        sheet.render();
        this._dirty = true;
      } catch (err) { console.warn("Paste failed:", err); }
    },

    saveGridXlsx() { this.exportGridXlsx(); },

    // === Cell Formatting ===

    _gridData() {
      try {
        if (this._grid && this._grid.sheet && this._grid.sheet.data) return this._grid.sheet.data;
        return this._grid ? this._grid.data : null;
      } catch (e) { return this._grid ? this._grid.data : null; }
    },

    _applyFormat(attr, value) {
      var data = this._gridData();
      if (!data) return App.error("Load a table first");
      try {
        data.setSelectedCellAttr(attr, value);
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        this._dirty = true;
        this._updateFormatToolbar();
        if (this._getCondRules().length) this._snapshotManualStyles();
      } catch (e) {
        App.error("Format failed: " + (e.message || e));
      }
    },

    _toggleFormat(type) {
      var attrMap = {
        bold: "font-bold",
        italic: "font-italic",
        underline: "underline",
        strike: "strike",
        textwrap: "textwrap"
      };
      var attr = attrMap[type];
      if (!attr) return;
      var style = this._getSelectedCellStyle();
      var current = false;
      if (style) {
        if (type === "bold" || type === "italic") {
          current = style.font ? !!style.font[type] : false;
        } else {
          current = !!style[attr];
        }
      }
      this._applyFormat(attr, !current);
    },

    _getSelectedCellStyle() {
      var data = this._gridData();
      if (!data) return null;
      try {
        return data.getSelectedCellStyle();
      } catch (e) { return null; }
    },

    _updateFormatToolbar() {
      var style = this._getSelectedCellStyle();
      if (!style) return;

      var boldBtn = document.getElementById("xl-btn-bold");
      var italicBtn = document.getElementById("xl-btn-italic");
      var underlineBtn = document.getElementById("xl-btn-underline");

      var fontBold = style.font ? !!style.font.bold : false;
      var fontItalic = style.font ? !!style.font.italic : false;
      var underline = !!style.underline;

      if (boldBtn) boldBtn.classList.toggle("active", fontBold);
      if (italicBtn) italicBtn.classList.toggle("active", fontItalic);
      if (underlineBtn) underlineBtn.classList.toggle("active", underline);

      var alignLeft = document.getElementById("xl-btn-align-left");
      var alignCenter = document.getElementById("xl-btn-align-center");
      var alignRight = document.getElementById("xl-btn-align-right");
      var align = style.align || "";
      if (alignLeft) alignLeft.classList.toggle("active", align === "left" || align === "");
      if (alignCenter) alignCenter.classList.toggle("active", align === "center");
      if (alignRight) alignRight.classList.toggle("active", align === "right");

      var fontColorIndicator = document.getElementById("xl-font-color-indicator");
      if (fontColorIndicator && style.color) {
        fontColorIndicator.style.background = style.color;
      }
      var bgColorIndicator = document.getElementById("xl-bg-color-indicator");
      if (bgColorIndicator && style.bgcolor) {
        bgColorIndicator.style.background = style.bgcolor;
      }
    },

    _buildColorPalette(paletteId) {
      var colors = [
        "#000000", "#434343", "#666666", "#999999", "#b7b7b7", "#cccccc", "#d9d9d9", "#efefef", "#f3f3f3", "#ffffff",
        "#980000", "#ff0000", "#ff9900", "#ffff00", "#00ff00", "#00ffff", "#4a86e8", "#0000ff", "#9900ff", "#ff00ff",
        "#e6b8af", "#f4cccc", "#fce5cd", "#fff2cc", "#d9ead3", "#d0e0e3", "#c9daf8", "#cfe2f3", "#d9d2e9", "#ead1dc",
        "#dd7e6b", "#ea9999", "#f9cb9c", "#ffe599", "#b6d7a8", "#a2c4c9", "#a4c2f4", "#9fc5e8", "#b4a7d6", "#d5a6bd",
        "#cc4125", "#e06666", "#f6b26b", "#ffd966", "#93c47d", "#76a5af", "#6d9eeb", "#6fa8dc", "#8e7cc3", "#c27ba0",
        "#a61c00", "#cc0000", "#e69138", "#f1c232", "#6aa84f", "#45818e", "#3c78d8", "#3d85c6", "#674ea7", "#a64d79",
        "#85200c", "#990000", "#b45f06", "#bf9000", "#38761d", "#134f5c", "#1155cc", "#0b5394", "#351c75", "#741b47",
        "#5b0f00", "#660000", "#783f04", "#7f6000", "#274e13", "#0c343d", "#1c4587", "#073763", "#20124d", "#4c1130"
      ];

      var el = document.getElementById(paletteId);
      if (!el) return;
      var html = '<div class="xl-color-swatch xl-color-none" data-color="" title="No color"></div>';
      for (var i = 0; i < colors.length; i++) {
        html += '<div class="xl-color-swatch" data-color="' + colors[i] + '" style="background:' + colors[i] + ';" title="' + colors[i] + '"></div>';
      }
      el.innerHTML = html;
      return el;
    },

    _bindFormatToolbar() {
      var self = this;

      var bind = function (id, fn) {
        var el = document.getElementById(id);
        if (el) el.addEventListener("click", function () { fn(); });
      };

      bind("xl-btn-bold", function () { self._toggleFormat("bold"); });
      bind("xl-btn-italic", function () { self._toggleFormat("italic"); });
      bind("xl-btn-underline", function () { self._toggleFormat("underline"); });

      bind("xl-btn-align-left", function () { self._applyFormat("align", "left"); });
      bind("xl-btn-align-center", function () { self._applyFormat("align", "center"); });
      bind("xl-btn-align-right", function () { self._applyFormat("align", "right"); });

      var numFormat = document.getElementById("xl-num-format");
      if (numFormat) {
        numFormat.addEventListener("change", function () {
          var val = numFormat.value;
          var formats = {
            general: "",
            number: "0.00",
            currency: "$#,##0.00",
            percent: "0.00%",
            date: "yyyy-mm-dd",
            comma: "#,##0"
          };
          self._applyFormat("format", formats[val] || "");
        });
      }

      bind("xl-btn-borders-all", function () {
        var data = self._gridData();
        if (!data) return App.error("Load a table first");
        try {
          data.setSelectedCellAttr("border", { mode: "all", color: "#000000", style: "thin" });
          if (typeof self._grid.reRender === "function") self._grid.reRender();
          App.success("All borders applied");
        } catch (e) { App.error("Borders failed: " + (e.message || e)); }
      });

      bind("xl-btn-borders-none", function () {
        var data = self._gridData();
        if (!data) return App.error("Load a table first");
        try {
          data.setSelectedCellAttr("border", { mode: "none" });
          if (typeof self._grid.reRender === "function") self._grid.reRender();
          App.success("Borders removed");
        } catch (e) { App.error("Borders failed: " + (e.message || e)); }
      });

      bind("xl-btn-autowidth", function () {
        self._autoFitColumnWidth();
      });

      self._bindColorPicker("xl-font-color-wrap", "xl-btn-font-color", "color", "xl-font-color-indicator", "#000000");
      self._bindColorPicker("xl-bg-color-wrap", "xl-btn-bg-color", "bgcolor", "xl-bg-color-indicator", "#ffff00");
    },

    _bindColorPicker(wrapId, btnId, formatAttr, indicatorId, defaultColor) {
      var self = this;
      var wrap = document.getElementById(wrapId);
      var btn = document.getElementById(btnId);
      var indicator = document.getElementById(indicatorId);
      if (!wrap || !btn) return;

      var paletteId = btnId + "-palette";
      var palette = document.getElementById(paletteId);
      if (!palette) {
        palette = document.createElement("div");
        palette.id = paletteId;
        palette.className = "xl-color-palette hidden";
        wrap.appendChild(palette);
      }
      this._buildColorPalette(paletteId);

      var currentColor = defaultColor;

      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        palette.classList.toggle("hidden");
      });

      palette.addEventListener("click", function (e) {
        var swatch = e.target.closest(".xl-color-swatch");
        if (!swatch) return;
        var color = swatch.getAttribute("data-color");
        currentColor = color || defaultColor;
        if (indicator) indicator.style.background = currentColor;
        palette.classList.add("hidden");
        self._applyFormat(formatAttr, currentColor);
      });

      document.addEventListener("click", function (e) {
        if (!e.target.closest("#" + wrapId)) {
          palette.classList.add("hidden");
        }
      });
    },

    _bindFmtColorPicker(paletteId, btnId, formatAttr, indicatorId, defaultColor) {
      var self = this;
      var btn = document.getElementById(btnId);
      var indicator = document.getElementById(indicatorId);
      var palette = document.getElementById(paletteId);
      if (!palette) return;
      this._buildColorPalette(paletteId);
      var currentColor = defaultColor;
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        palette.classList.toggle("hidden");
      });
      palette.addEventListener("click", function (e) {
        var swatch = e.target.closest(".xl-color-swatch");
        if (!swatch) return;
        var color = swatch.getAttribute("data-color");
        currentColor = color || defaultColor;
        if (indicator) indicator.style.background = currentColor;
        palette.classList.add("hidden");
        var data = self._gridData();
        if (!data) return;
        data.setSelectedCellAttr(formatAttr, currentColor);
        if (typeof self._grid.reRender === "function") self._grid.reRender();
        self._dirty = true;
      });
      document.addEventListener("click", function (e) {
        if (palette && !e.target.closest("#" + paletteId) && !e.target.closest("#" + btnId)) {
          palette.classList.add("hidden");
        }
      });
    },

    // === Conditional Formatting ===

    _buildCondFmtModal() {
      return '<div class="xl-modal-mask hidden" id="xl-cf-mask">' +
        '<div class="xl-modal" id="xl-cf-modal">' +
          '<div class="xl-modal-head">' +
            '<h3><i class="fas fa-palette mr-1 text-primary-500"></i>Conditional Formatting</h3>' +
            '<button class="xl-modal-close" id="xl-cf-close" title="Close"><i class="fas fa-xmark"></i></button>' +
          '</div>' +
          '<div class="xl-modal-body">' +
            '<p class="xl-cf-hint">Highlight cells in the active sheet based on rules. Rules apply from top to bottom; the first matching rule wins.</p>' +
            '<div class="xl-cf-rules" id="xl-cf-list"></div>' +
            '<div class="xl-cf-form card">' +
              '<div class="xl-cf-form-grid">' +
                '<label>Rule type' +
                  '<select id="xl-cf-type">' +
                    '<option value="cellRule">Cell value</option>' +
                    '<option value="colorScale">Color scale</option>' +
                    '<option value="dataBar">Data bar</option>' +
                    '<option value="iconSet">Icon set</option>' +
                  '</select>' +
                '</label>' +
                '<label>Range <span class="xl-cf-input-range-hint" id="xl-cf-range-hint">(drag on grid or type)</span>' +
                  '<input id="xl-cf-range" placeholder="A1:B10" />' +
                '</label>' +
              '</div>' +
              '<div class="xl-cf-form-row" id="xl-cf-cell-op-row">' +
                '<label>Condition' +
                  '<select id="xl-cf-op">' +
                    '<option value="greaterThan">greater than</option>' +
                    '<option value="lessThan">less than</option>' +
                    '<option value="equal">equal to</option>' +
                    '<option value="notEqual">not equal to</option>' +
                    '<option value="between">between</option>' +
                    '<option value="notBetween">not between</option>' +
                    '<option value="textContains">text contains</option>' +
                    '<option value="textNotContains">text not contains</option>' +
                    '<option value="beginsWith">begins with</option>' +
                    '<option value="endsWith">ends with</option>' +
                  '</select>' +
                '</label>' +
                '<label id="xl-cf-val1-wrap">Value 1<input id="xl-cf-val1" placeholder="10" /></label>' +
                '<label id="xl-cf-val2-wrap" class="hidden">Value 2<input id="xl-cf-val2" placeholder="20" /></label>' +
              '</div>' +
              '<div class="xl-cf-form-row hidden" id="xl-cf-style-row">' +
                '<label>Fill<select id="xl-cf-fill">' +
                  '<option value="">No fill</option>' +
                  '<option value="#ff0000">Red</option><option value="#ff9900">Orange</option>' +
                  '<option value="#ffff00">Yellow</option><option value="#00b050">Green</option>' +
                  '<option value="#0070c0">Blue</option><option value="#7030a0">Purple</option>' +
                '</select></label>' +
                '<label>Font color<select id="xl-cf-fontcolor">' +
                  '<option value="">Default</option>' +
                  '<option value="#000000">Black</option><option value="#ff0000">Red</option>' +
                  '<option value="#00b050">Green</option><option value="#0070c0">Blue</option>' +
                  '<option value="#ffffff">White</option>' +
                '</select></label>' +
                '<label class="xl-cf-check"><input type="checkbox" id="xl-cf-bold" /> Bold</label>' +
              '</div>' +
              '<div class="xl-cf-form-row hidden" id="xl-cf-bar-row">' +
                '<label>Bar color<select id="xl-cf-bar-color">' +
                  '<option value="#638ec6">Blue</option><option value="#00b050">Green</option>' +
                  '<option value="#ff0000">Red</option><option value="#ff9900">Orange</option>' +
                '</select></label>' +
              '</div>' +
              '<div class="xl-cf-form-row hidden" id="xl-cf-icon-row">' +
                '<label>Icon style<select id="xl-cf-icon-style">' +
                  '<option value="arrows">Arrows</option><option value="trafficLight">Traffic lights</option>' +
                  '<option value="flags">Flags</option>' +
                '</select></label>' +
              '</div>' +
              '<div class="xl-cf-actions">' +
                '<button class="btn btn-sm btn-primary" id="xl-cf-add"><i class="fas fa-plus mr-1"></i>Add Rule</button>' +
                '<button class="btn btn-sm" id="xl-cf-clear"><i class="fas fa-eraser mr-1"></i>Clear All</button>' +
              '</div>' +
            '</div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="btn btn-sm btn-primary" id="xl-cf-done"><i class="fas fa-check mr-1"></i>Apply</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _condSheetKey() {
      try {
        var g = this._grid;
        if (!g) return "_";
        return (g.sheet && g.sheet.data && g.sheet.data.name) || (g.data && g.data.name) || "_";
      } catch (e) { return "_"; }
    },

    _getCondRules() {
      var key = this._condSheetKey();
      return (this._condRules[key] || []).slice();
    },

    _parseRange(s) {
      if (!s) return null;
      var m = /^\s*\$?([A-Za-z]{1,3})\$?(\d+)\s*(?::\s*\$?([A-Za-z]{1,3})\$?(\d+))?\s*$/.exec(s.toUpperCase());
      if (!m) return null;
      var c1 = this._colIndex(m[1]);
      var r1 = parseInt(m[2], 10) - 1;
      var c2, r2;
      if (m[3]) { c2 = this._colIndex(m[3]); r2 = parseInt(m[4], 10) - 1; }
      else { c2 = c1; r2 = r1; }
      var sr = Math.min(r1, r2), er = Math.max(r1, r2), sc = Math.min(c1, c2), ec = Math.max(c1, c2);
      return { sri: sr, eri: er, sci: sc, eci: ec, r1: sr, c1: sc, r2: er, c2: ec, sc: sc, ec: ec, sr: sr, er: er };
    },

    _colIndex(name) {
      var n = 0;
      for (var i = 0; i < name.length; i++) n = n * 26 + (name.charCodeAt(i) - 64);
      return n - 1;
    },

    _colName(idx) {
      var s = "";
      var n = idx + 1;
      while (n > 0) { var r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); }
      return s;
    },

    _openCondFmt() {
      if (!this._grid) return App.error("Load a table first");
      var range = this._currentSelectionRangeA1() || ("A1:" + this._colName(Math.min(25, this._maxColInGrid())) + "1");
      var rangeInput = document.getElementById("xl-cf-range");
      if (rangeInput && !rangeInput.value) rangeInput.value = this._sanitizeRange(range);
      this._renderCondRuleList();
      var mask = document.getElementById("xl-cf-mask");
      if (mask) mask.classList.remove("hidden");
      this._syncCondFormVisibility();
    },

    _closeCondFmt() {
      var mask = document.getElementById("xl-cf-mask");
      if (mask) mask.classList.add("hidden");
    },

    _currentSelectionRangeA1() {
      try {
        var g = this._grid;
        if (!g || !g.sheet) return null;
        var d = g.sheet.data;
        var sel = d.selector;
        if (!sel || !sel.range) return null;
        var r = sel.range;
        var sri = (r.sri != null) ? r.sri : 0;
        var eri = (r.eri != null) ? r.eri : sri;
        var sci = (r.sci != null) ? r.sci : 0;
        var eci = (r.eci != null) ? r.eci : sci;
        return this._colName(sci) + (sri + 1) + ":" + this._colName(eci) + (eri + 1);
      } catch (e) { return null; }
    },

    _maxColInGrid() {
      try {
        var s = (this._grid.getData() || [])[0];
        if (!s || !s.rows) return 1;
        var max = 0;
        Object.keys(s.rows).forEach(function (rk) {
          var cells = (s.rows[rk] || {}).cells || {};
          Object.keys(cells).forEach(function (ck) { var n = parseInt(ck, 10); if (n > max) max = n; });
        });
        return max;
      } catch (e) { return 1; }
    },

    _sanitizeRange(range) {
      var p = this._parseRange(range);
      if (!p) return range;
      return this._colName(p.sc) + (p.sr + 1) + ":" + this._colName(p.ec) + (p.er + 1);
    },

    _syncCondFormVisibility() {
      var type = "cellRule";
      var t = document.getElementById("xl-cf-type");
      if (t) type = t.value;
      var cellRow = document.getElementById("xl-cf-cell-op-row");
      var styleRow = document.getElementById("xl-cf-style-row");
      var barRow = document.getElementById("xl-cf-bar-row");
      var iconRow = document.getElementById("xl-cf-icon-row");
      var opSel = document.getElementById("xl-cf-op");
      var v2 = document.getElementById("xl-cf-val2-wrap");
      if (type === "cellRule") {
        if (cellRow) cellRow.classList.remove("hidden");
        if (styleRow) styleRow.classList.remove("hidden");
        if (barRow) barRow.classList.add("hidden");
        if (iconRow) iconRow.classList.add("hidden");
        var op = opSel ? opSel.value : "greaterThan";
        if (v2) v2.classList.toggle("hidden", !(op === "between" || op === "notBetween"));
      } else if (type === "colorScale") {
        if (cellRow) cellRow.classList.add("hidden");
        if (styleRow) styleRow.classList.add("hidden");
        if (barRow) barRow.classList.add("hidden");
        if (iconRow) iconRow.classList.add("hidden");
      } else if (type === "dataBar") {
        if (cellRow) cellRow.classList.add("hidden");
        if (styleRow) styleRow.classList.add("hidden");
        if (barRow) barRow.classList.remove("hidden");
        if (iconRow) iconRow.classList.add("hidden");
      } else if (type === "iconSet") {
        if (cellRow) cellRow.classList.add("hidden");
        if (styleRow) styleRow.classList.add("hidden");
        if (barRow) barRow.classList.add("hidden");
        if (iconRow) iconRow.classList.remove("hidden");
      }
    },

    _clickedCondRange: null,

    _enterCondRangeMode() {
      var self = this;
      var hint = document.getElementById("xl-cf-range-hint");
      if (hint) hint.textContent = " (select a range on the grid... click a cell, or drag)";
      this._condRangeMode = true;
      this._condRangeFrom = null;
      App.success("Conditional Formatting: click or drag on the grid to choose a range");
      var poll = function () {
        if (!self._condRangeMode) return;
        var data = self._gridData();
        if (!data || !data.selector) { setTimeout(poll, 200); return; }
        var sel = data.selector;
        var r = sel.range;
        if (!r) { setTimeout(poll, 200); return; }
        var sri = (r.sri != null) ? r.sri : (sel.ri || 0);
        var eri = (r.eri != null) ? r.eri : sri;
        var sci = (r.sci != null) ? r.sci : (sel.ci || 0);
        var eci = (r.eci != null) ? r.eci : sci;
        var inp = document.getElementById("xl-cf-range");
        if (inp) inp.value = self._colName(sci) + (sri + 1) + ":" + self._colName(eci) + (eri + 1);
        setTimeout(poll, 200);
      };
      poll();
    },

    _exitCondRangeMode() {
      this._condRangeMode = false;
      var hint = document.getElementById("xl-cf-range-hint");
      if (hint) hint.textContent = "(drag on grid or type)";
    },

    _readCondForm() {
      var type = (document.getElementById("xl-cf-type") || {}).value || "cellRule";
      var range = this._sanitizeRange((document.getElementById("xl-cf-range") || {}).value || "");
      var par = this._parseRange(range);
      if (!par) return { error: "Invalid range. Use A1:B10 format." };
      var rule = { id: "cf-" + (++this._cfSeq), sheet: this._condSheetKey(), type: type, range: range };
      if (type === "cellRule") {
        var op = (document.getElementById("xl-cf-op") || {}).value || "greaterThan";
        var v1 = ((document.getElementById("xl-cf-val1") || {}).value || "").trim();
        var v2 = ((document.getElementById("xl-cf-val2") || {}).value || "").trim();
        rule.op = op;
        rule.val1 = v1;
        rule.val2 = v2;
        var style = {};
        var fill = ((document.getElementById("xl-cf-fill") || {}).value || "");
        var fc = ((document.getElementById("xl-cf-fontcolor") || {}).value || "");
        var bold = !!((document.getElementById("xl-cf-bold") || {}).checked);
        if (fill) style.bgcolor = fill;
        if (fc) style.color = fc;
        if (bold) { style.font = style.font || {}; style.font.bold = true; }
        rule.style = style;
      } else if (type === "colorScale") {
        rule.minColor = "#f8696b";
        rule.maxColor = "#63be7b";
        rule.midColor = null;
      } else if (type === "dataBar") {
        rule.barColor = ((document.getElementById("xl-cf-bar-color") || {}).value) || "#638ec6";
      } else if (type === "iconSet") {
        rule.iconStyle = ((document.getElementById("xl-cf-icon-style") || {}).value) || "arrows";
      }
      return rule;
    },

    _addCondRule() {
      var rule = this._readCondForm();
      if (rule.error) return App.error(rule.error);
      var key = this._condSheetKey();
      if (!this._condRules[key]) this._condRules[key] = [];
      this._condRules[key].push(rule);
      this._renderCondRuleList();
      this._applyConditionalFormatting(true);
      App.success("Conditional format rule added");
    },

    _deleteCondRule(id) {
      var key = this._condSheetKey();
      if (!this._condRules[key]) return;
      this._condRules[key] = this._condRules[key].filter(function (r) { return r.id !== id; });
      this._renderCondRuleList();
      this._applyConditionalFormatting(true);
    },

    _clearCondRules() {
      var key = this._condSheetKey();
      this._condRules[key] = [];
      this._renderCondRuleList();
      this._applyConditionalFormatting(true);
    },

    _renderCondRuleList() {
      var el = document.getElementById("xl-cf-list");
      if (!el) return;
      var rules = this._getCondRules();
      if (!rules.length) {
        el.innerHTML = '<div class="xl-cf-empty">No conditional formatting rules for this sheet yet.</div>';
        return;
      }
      var html = "";
      rules.forEach(function (r) {
        var desc = this._describeCondRule(r);
        html += '<div class="xl-cf-rule card">' +
          '<div class="xl-cf-rule-info">' +
            '<div class="xl-cf-rule-title"><span class="xl-cf-rule-type">' + this._cfTypeLabel(r.type) + '</span> ' + desc + '</div>' +
            '<div class="xl-cf-rule-range">' + this._escape(r.range) + '</div>' +
          '</div>' +
          '<div class="xl-cf-rule-actions">' +
            '<button class="xl-cf-rule-del" data-cf-del="' + r.id + '" title="Delete rule"><i class="fas fa-trash"></i></button>' +
          '</div>' +
        '</div>';
      }, this);
      el.innerHTML = html;
      var self = this;
      el.querySelectorAll("[data-cf-del]").forEach(function (b) {
        b.addEventListener("click", function () { self._deleteCondRule(b.getAttribute("data-cf-del")); });
      });
    },

    _cfTypeLabel(type) {
      var map = { cellRule: "Cell value", colorScale: "Color scale", dataBar: "Data bar", iconSet: "Icon set" };
      return map[type] || type;
    },

    _describeCondRule(r) {
      if (r.type === "cellRule") {
        var opMap = { greaterThan: ">", lessThan: "<", equal: "=", notEqual: "<>", between: "between", notBetween: "not between", textContains: "contains", textNotContains: "does not contain", beginsWith: "begins with", endsWith: "ends with" };
        var op = opMap[r.op] || r.op;
        if (r.op === "between" || r.op === "notBetween") return op + " <b>" + this._escape(r.val1) + "</b> and <b>" + this._escape(r.val2) + "</b>";
        return op + " <b>" + this._escape(r.val1) + "</b>";
      }
      if (r.type === "colorScale") return "min <span style='background:" + r.minColor + "'>&nbsp;</span> to max <span style='background:" + r.maxColor + "'>&nbsp;</span>";
      if (r.type === "dataBar") return "bar, color <span style='background:" + r.barColor + "'>&nbsp;</span>";
      if (r.type === "iconSet") return "icons: " + r.iconStyle;
      return "";
    },

    _getManualStyle(r, c) {
      var key = this._condSheetKey();
      var m = this._condManual[key] || {};
      return m[(r || 0) + ":" + (c || 0)] || null;
    },

    _recordManualStyleForCell(r, c, styleObj) {
      var key = this._condSheetKey();
      if (!this._condManual[key]) this._condManual[key] = {};
      if (styleObj && Object.keys(styleObj).length) {
        this._condManual[key][(r || 0) + ":" + (c || 0)] = this._shallowCloneStyle(styleObj);
      } else {
        delete this._condManual[key][(r || 0) + ":" + (c || 0)];
      }
    },

    _shallowCloneStyle(s) {
      if (!s) return null;
      var o = {};
      for (var k in s) {
        if (k === "font" && s[k]) { o[k] = Object.assign({}, s[k]); }
        else o[k] = s[k];
      }
      return o;
    },

    _mergeStyles(base, overlay) {
      if (!overlay) return base ? this._shallowCloneStyle(base) : null;
      if (!base) return this._shallowCloneStyle(overlay);
      var out = this._shallowCloneStyle(base);
      for (var k in overlay) {
        if (k === "font" && overlay[k]) {
          out.font = Object.assign({}, out.font || {}, overlay[k]);
        } else {
          out[k] = overlay[k];
        }
      }
      return out;
    },

    _rulesForRange(par) {
      var rules = this._getCondRules();
      return rules.filter(function (r) {
        var rp = this._parseRange(r.range);
        if (!rp) return false;
        return this._rangesOverlap(par, rp);
      }, this);
    },

    _rangesOverlap(a, b) {
      return !(a.eri < b.sri || b.eri < a.sri || a.eci < b.sci || b.eci < a.sci);
    },

    _evalCellRule(rule, text) {
      var num = Number(String(text).replace(/[^\d.\-]/g, ""));
      var isNum = text !== "" && String(text).trim() !== "" && !isNaN(num);
      var t = String(text == null ? "" : text).trim().toLowerCase();
      var op = rule.op;
      var v1s = String(rule.val1 == null ? "" : rule.val1).trim();
      var v2s = String(rule.val2 == null ? "" : rule.val2).trim();
      var n1 = Number(v1s.replace(/[^\d.\-]/g, ""));
      var n2 = Number(v2s.replace(/[^\d.\-]/g, ""));
      var t1 = v1s.toLowerCase();
      var t2 = v2s.toLowerCase();
      var sgn = 0;
      if (isNum && v1s !== "" && !isNaN(n1)) sgn = num - n1;
      switch (op) {
        case "greaterThan": return isNum && !isNaN(n1) && num > n1;
        case "lessThan": return isNum && !isNaN(n1) && num < n1;
        case "equal":
          if (isNum && v1s !== "" && !isNaN(n1)) return num === n1;
          return t === t1;
        case "notEqual":
          if (isNum && v1s !== "" && !isNaN(n1)) return num !== n1;
          return t !== t1;
        case "between": return isNum && !isNaN(n1) && !isNaN(n2) && num >= Math.min(n1, n2) && num <= Math.max(n1, n2);
        case "notBetween": return isNum && !isNaN(n1) && !isNaN(n2) && (num < Math.min(n1, n2) || num > Math.max(n1, n2));
        case "textContains": return t.indexOf(t1) !== -1;
        case "textNotContains": return t !== "" && t.indexOf(t1) === -1;
        case "beginsWith": return t.indexOf(t1) === 0;
        case "endsWith": return t.length >= t1.length && t.slice(t.length - t1.length) === t1;
        default: return false;
      }
    },

    _ruleStyleForCell(rule, text) {
      if (rule.type === "cellRule") {
        return this._evalCellRule(rule, text) ? (rule.style || {}) : null;
      }
      return null;
    },

    _hexToRgb(hex) {
      var h = String(hex || "#000000").replace("#", "");
      if (h.length === 3) h = h.split("").map(function (x) { return x + x; }).join("");
      var n = parseInt(h, 16);
      if (isNaN(n)) return { r: 0, g: 0, b: 0 };
      return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
    },

    _rgbToHex(r, g, b) {
      var c = function (x) { var s = Math.round(x).toString(16); return s.length === 1 ? "0" + s : s; };
      return "#" + c(Math.max(0, Math.min(255, r))) + c(Math.max(0, Math.min(255, g))) + c(Math.max(0, Math.min(255, b)));
    },

    _interpolate(a, b, t) {
      var ca = this._hexToRgb(a), cb = this._hexToRgb(b);
      return this._rgbToHex(ca.r + (cb.r - ca.r) * t, ca.g + (cb.g - ca.g) * t, ca.b + (cb.b - ca.b) * t);
    },

    _colorScaleStyle(rule, text, min, max) {
      var num = Number(String(text).replace(/[^\d.\-]/g, ""));
      if (text === "" || isNaN(num)) return null;
      var t;
      if (max === min) t = 1;
      else t = (num - min) / (max - min);
      t = Math.max(0, Math.min(1, t));
      var color = rule.midColor ? (t < 0.5 ? this._interpolate(rule.minColor, rule.midColor, t / 0.5) : this._interpolate(rule.midColor, rule.maxColor, (t - 0.5) / 0.5)) : this._interpolate(rule.minColor, rule.maxColor, t);
      return { bgcolor: color };
    },

    _numericRangeOfRule(rule) {
      var par = this._parseRange(rule.range);
      var min = Infinity, max = -Infinity;
      var data = this._gridData();
      if (!data || !par) return { min: 0, max: 1 };
      for (var r = par.sri; r <= par.eri && r <= 5000; r++) {
        for (var c = par.sci; c <= par.eci && c <= 100; c++) {
          var cell = data.getCell(r, c);
          if (!cell) continue;
          var t = cell.text != null ? String(cell.text) : "";
          var n = Number(t.replace(/[^\d.\-]/g, ""));
          if (t !== "" && !isNaN(n)) { if (n < min) min = n; if (n > max) max = n; }
        }
      }
      if (!isFinite(min)) { min = 0; }
      if (!isFinite(max)) { max = 1; }
      return { min: min, max: max };
    },

    _applyConditionalFormatting(force) {
      if (!this._cfAllowed) return;
      var data = this._gridData();
      if (!data) return;
      if (this._cfApplying && !force) return;
      this._cfApplying = true;
      var rules = this._getCondRules();
      var self = this;
      var touched = {};
      var barOverlays = [];
      var iconOverlays = [];
      var rangeMinMax = {};

      rules.forEach(function (rule) {
        var par = self._parseRange(rule.range);
        if (!par) return;
        var mm = null;
        if (rule.type === "colorScale" || rule.type === "dataBar" || rule.type === "iconSet") {
          mm = self._numericRangeOfRule(rule);
          rangeMinMax[rule.id] = mm;
        }
        for (var r = par.sri; r <= par.eri; r++) {
          for (var c = par.sci; c <= par.eci; c++) {
            var k = r + ":" + c;
            var cell = data.getCell(r, c);
            if (!cell) continue;
            var text = cell.text != null ? String(cell.text) : "";
            if (rule.type === "cellRule") {
              var st = self._ruleStyleForCell(rule, text);
              if (st) {
                if (!touched[k]) touched[k] = { cell: cell, row: r, col: c, cellRule: null, colorScale: null, dataBar: null, icon: null };
                touched[k].cellRule = st;
              }
            } else if (rule.type === "colorScale") {
              var cs = self._colorScaleStyle(rule, text, mm.min, mm.max);
              if (cs) {
                if (!touched[k]) touched[k] = { cell: cell, row: r, col: c, cellRule: null, colorScale: null, dataBar: null, icon: null };
                touched[k].colorScale = cs;
              }
            } else if (rule.type === "dataBar") {
              var n = Number(text.replace(/[^\d.\-]/g, ""));
              if (text !== "" && !isNaN(n)) {
                barOverlays.push({ r: r, c: c, n: n, min: mm.min, max: mm.max, color: rule.barColor });
                if (!touched[k]) touched[k] = { cell: cell, row: r, col: c, cellRule: null, colorScale: null, dataBar: null, icon: null };
                touched[k].dataBar = true;
              }
            } else if (rule.type === "iconSet") {
              var ni = Number(text.replace(/[^\d.\-]/g, ""));
              if (text !== "" && !isNaN(ni)) {
                iconOverlays.push({ r: r, c: c, n: ni, min: mm.min, max: mm.max, style: rule.iconStyle });
                if (!touched[k]) touched[k] = { cell: cell, row: r, col: c, cellRule: null, colorScale: null, dataBar: null, icon: null };
                touched[k].icon = true;
              }
            }
          }
        }
      });

      var key = this._condSheetKey();
      var manual = this._condManual[key] = this._condManual[key] || {};
      var covered = this._cfCovered[key] = this._cfCovered[key] || {};
      var stylesChanged = false;

      var applyMerged = function (r, c, cfStyle, isCovered) {
        var k = r + ":" + c;
        if (!(k in manual)) {
          var cell0 = data.getCell(r, c);
          var cur0 = self._currentStyleObjForCell(cell0, data);
          manual[k] = self._existingCleanedStyle(cur0);
        }
        var base = manual[k];
        if (isCovered) covered[k] = 1;
        var merged = cfStyle ? self._mergeStyles(base || {}, cfStyle) : (base ? self._shallowCloneStyle(base) : null);
        var cell = data.getCell(r, c);
        if (!cell) return;
        if (merged) {
          var idx = data.addStyle(merged);
          if (cell.style !== idx) { cell.style = idx; stylesChanged = true; }
        } else if (cell.style != null) {
          delete cell.style; stylesChanged = true;
        }
      };

      try {
        Object.keys(covered).forEach(function (k) {
          if (touched[k]) return;
          var parts = k.split(":");
          applyMerged(Number(parts[0]), Number(parts[1]), null, false);
        });
      } catch (e) { /* noop */ }

      try {
        Object.keys(touched).forEach(function (k) {
          var t = touched[k];
          var cfStyle = t.cellRule || t.colorScale;
          applyMerged(t.row, t.col, cfStyle, true);
        });
      } catch (e) { /* noop */ }

      this._cfBarOverlays = barOverlays;
      this._cfIconOverlays = iconOverlays;
      if (stylesChanged) {
        try { if (typeof data.change === "function") data.change("cond-format"); } catch (e) { /* noop */ }
      }
      this._cfApplying = false;
      if (typeof this._grid.reRender === "function") this._grid.reRender();
      this._renderCfOverlay();
    },

    _currentStyleObjForCell(cell, data) {
      try { return cell.style != null && data.styles[cell.style] ? data.styles[cell.style] : null; }
      catch (e) { return null; }
    },

    _existingCleanedStyle(s) {
      if (!s) return null;
      var out = {};
      for (var k in s) {
        if (k === "border") continue;
        if (k === "font" && s[k]) out[k] = Object.assign({}, s[k]);
        else out[k] = s[k];
      }
      return out;
    },

    _snapshotManualStyles() {
      var data = this._gridData();
      if (!data) return;
      var key = this._condSheetKey();
      var manual = this._condManual[key] = this._condManual[key] || {};
      var covered = this._cfCovered[key] = this._cfCovered[key] || {};
      var sel = this._getSelection();
      if (!sel || !sel.range) return;
      var rg = sel.range;
      var sri = rg.sri != null ? rg.sri : rg.start.row;
      var eri = rg.eri != null ? rg.eri : rg.end.row;
      var sci = rg.sci != null ? rg.sci : rg.start.col;
      var eci = rg.eci != null ? rg.eci : rg.end.col;
      var self = this;
      try {
        for (var r = sri; r <= eri; r++) {
          for (var c = sci; c <= eci; c++) {
            var cell = data.getCell(r, c);
            if (!cell) continue;
            var cur = self._currentStyleObjForCell(cell, data);
            var base = self._existingCleanedStyle(cur);
            if (base) { manual[r + ":" + c] = base; covered[r + ":" + c] = true; }
          }
        }
      } catch (e) { /* noop */ }
    },

    _renderCfOverlay() {
      var container = document.getElementById("xl-cf-overlay");
      var gridArea = document.getElementById("xl-grid");
      if (!container) {
        container = document.createElement("div");
        container.id = "xl-cf-overlay";
        container.className = "xl-cf-overlay";
        if (gridArea) gridArea.appendChild(container);
      }
      var data = this._gridData();
      if (!data || (!this._cfBarOverlays || !this._cfBarOverlays.length) && (!this._cfIconOverlays || !this._cfIconOverlays.length)) {
        container.innerHTML = "";
        container.style.display = "none";
        return;
      }
      container.innerHTML = "";
      container.style.display = "";
      var self = this;
      var indexW = (data.cols && data.cols.indexWidth) ? data.cols.indexWidth : 60;
      var sx = 0, sy = 0;
      try {
        if (data.scroll) { sx = data.scroll.x || 0; sy = data.scroll.y || 0; }
      } catch (e) { /* noop */ }
      function cellRect(r, c) {
        try {
          var rr = data.cellRect ? data.cellRect(r, c) : null;
          if (!rr) return null;
          return { left: (rr.left != null ? rr.left : 0) + indexW - sx, top: (rr.top != null ? rr.top : 0) - sy, width: rr.width || 0, height: rr.height || 0 };
        } catch (e) { return null; }
      }
      (this._cfBarOverlays || []).forEach(function (o) {
        var rect = cellRect(o.r, o.c);
        if (!rect) return;
        var frac = (o.max === o.min) ? 1 : (o.n - o.min) / (o.max - o.min);
        frac = Math.max(0, Math.min(1, frac));
        var w = Math.max(2, (rect.width - 4) * frac);
        var bar = document.createElement("div");
        bar.className = "xl-cf-databar";
        bar.style.width = w + "px";
        bar.style.background = o.color;
        bar.style.left = rect.left + "px";
        bar.style.top = (rect.top + rect.height / 2) + "px";
        container.appendChild(bar);
      });
      (this._cfIconOverlays || []).forEach(function (o) {
        var rect = cellRect(o.r, o.c);
        if (!rect) return;
        var frac = (o.max === o.min) ? 1 : (o.n - o.min) / (o.max - o.min);
        frac = Math.max(0, Math.min(1, frac));
        var icon = document.createElement("div");
        icon.className = "xl-cf-icon";
        icon.style.left = rect.left + "px";
        icon.style.top = rect.top + "px";
        var sym = "▲", color = "#00b050";
        if (o.style === "trafficLight") { sym = frac >= 0.667 ? "●" : (frac >= 0.333 ? "●" : "●"); color = frac >= 0.667 ? "#00b050" : (frac >= 0.333 ? "#ffc000" : "#ff0000"); icon.style.color = color; icon.textContent = sym; }
        else if (o.style === "flags") { sym = "🚩"; color = frac >= 0.667 ? "#00b050" : (frac >= 0.333 ? "#ffc000" : "#ff0000"); icon.style.color = color; icon.textContent = sym; }
        else { sym = frac >= 0.667 ? "▲" : (frac >= 0.333 ? "◆" : "▼"); color = frac >= 0.667 ? "#00b050" : (frac >= 0.333 ? "#ffc000" : "#ff0000"); icon.style.color = color; icon.textContent = sym; }
        container.appendChild(icon);
      });
    },

    _bindCondFmt() {
      var self = this;
      function bind(id, fn) { var el = document.getElementById(id); if (el) el.addEventListener("click", function () { fn(); }); }
      bind("xl-btn-condfmt", function () { self._openCondFmt(); });
      bind("xl-cf-close", function () { self._closeCondFmt(); });
      bind("xl-cf-done", function () { self._closeCondFmt(); });
      bind("xl-cf-clear", function () { self._clearCondRules(); });
      bind("xl-cf-add", function () { self._addCondRule(); });
      var typeSel = document.getElementById("xl-cf-type");
      if (typeSel) typeSel.addEventListener("change", function () { self._syncCondFormVisibility(); });
      var opSel = document.getElementById("xl-cf-op");
      if (opSel) opSel.addEventListener("change", function () { self._syncCondFormVisibility(); });
      var rangeInput = document.getElementById("xl-cf-range");
      if (rangeInput) {
        rangeInput.addEventListener("focus", function () { self._enterCondRangeMode(); });
        rangeInput.addEventListener("blur", function () { self._exitCondRangeMode(); });
      }
      var mask = document.getElementById("xl-cf-mask");
      if (mask) mask.addEventListener("click", function (e) { if (e.target === mask) self._closeCondFmt(); });
    },

    // === Toggle Panels ===

    _toggleAI() {
      this._showAI = !this._showAI;
      var el = document.getElementById("xl-ai-sidebar");
      var btn = document.getElementById("xl-btn-ai-toggle");
      if (el) el.classList.toggle("hidden", !this._showAI);
      if (btn) btn.classList.toggle("active", this._showAI);
    },

    _toggleTools() {
      this._showTools = !this._showTools;
      var el = document.getElementById("xl-tools-section");
      var btn = document.getElementById("xl-btn-tools-toggle");
      if (el) el.classList.toggle("hidden", !this._showTools);
      if (btn) btn.classList.toggle("active", this._showTools);
    },

    _toggleFindBar() {
      this._showFindBar = !this._showFindBar;
      var el = document.getElementById("xl-find-bar");
      var btn = document.getElementById("xl-btn-find-toggle");
      if (el) el.classList.toggle("hidden", !this._showFindBar);
      if (btn) btn.classList.toggle("active", this._showFindBar);
      if (this._showFindBar) {
        var inp = document.getElementById("xl-find-val");
        if (inp) inp.focus();
      }
    },

    _toggleAutoFilter() {
      if (!this._grid) return App.error("Load a table first");
      try {
        var d = this._gridData();
        if (!d) return App.error("No sheet data");
        if (typeof d.autofilter !== "function") return App.error("AutoFilter not supported");
        var wasActive = d.autoFilter && d.autoFilter.active && d.autoFilter.active();
        if (wasActive) {
          d.autofilter();
          var btn = document.getElementById("xl-btn-filter-toggle");
          if (btn) btn.classList.remove("active");
          App.success("AutoFilter removed");
        } else {
          var sel = d.selector;
          if (sel && sel.range && sel.range.sri != null) {
            var r = sel.range;
            var cols = this._currentColumns ? this._currentColumns.length : 10;
            var headerRow = r.sri;
            var rangeRef = this._colName(0) + (headerRow + 1) + ":" + this._colName(Math.max(0, cols - 1)) + (d.rows && d.rows.len ? d.rows.len : 100);
            try { d.selector.range = this._parseRange(rangeRef); } catch (e) { /* noop */ }
          }
          d.autofilter();
          var btn2 = document.getElementById("xl-btn-filter-toggle");
          if (btn2) btn2.classList.add("active");
          App.success("AutoFilter enabled — click dropdown arrows on headers to filter/sort");
        }
        if (typeof this._grid.reRender === "function") this._grid.reRender();
      } catch (e) { App.error("AutoFilter failed: " + (e.message || e)); }
    },

    _buildSortModal() {
      return '<div class="xl-modal-mask hidden" id="xl-sort-mask">' +
        '<div class="xl-modal xl-sort-modal" id="xl-sort-modal">' +
          '<div class="xl-modal-head"><span>Multi-Column Sort</span><button class="xl-modal-close" id="xl-sort-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-sort-levels" id="xl-sort-levels"></div>' +
            '<div class="xl-cf-row-actions"><button class="xl-cf-btn" id="xl-sort-add-level"><i class="fas fa-plus"></i> Add Level</button></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-sort-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-sort-apply">Sort</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openSortModal() {
      var mask = document.getElementById("xl-sort-mask");
      if (mask) mask.classList.remove("hidden");
      var container = document.getElementById("xl-sort-levels");
      if (container && !container.children.length) this._addSortLevel();
    },

    _closeSortModal() {
      var mask = document.getElementById("xl-sort-mask");
      if (mask) mask.classList.add("hidden");
    },

    _addSortLevel() {
      var container = document.getElementById("xl-sort-levels");
      if (!container) return;
      var idx = container.children.length;
      var colOptions = (this._currentColumns || []).map(function (c, i) {
        return '<option value="' + i + '">' + this._escape(c) + '</option>';
      }.bind(this)).join("");
      var level = document.createElement("div");
      level.className = "xl-sort-level";
      level.innerHTML =
        '<span class="xl-sort-level-num">' + (idx + 1) + '</span>' +
        '<select class="xl-sort-col xl-cf-field" id="xl-sort-col-' + idx + '">' + colOptions + '</select>' +
        '<select class="xl-sort-dir xl-cf-field" id="xl-sort-dir-' + idx + '">' +
          '<option value="asc">A → Z (ascending)</option>' +
          '<option value="desc">Z → A (descending)</option>' +
        '</select>' +
        '<button class="xl-cf-rule-del xl-sort-remove" data-level="' + idx + '"><i class="fas fa-trash"></i></button>';
      container.appendChild(level);
      var self = this;
      level.querySelector(".xl-sort-remove").addEventListener("click", function () {
        self._removeSortLevel(level);
      });
    },

    _removeSortLevel(levelEl) {
      var container = document.getElementById("xl-sort-levels");
      if (container && levelEl && levelEl.parentNode === container) {
        container.removeChild(levelEl);
        var levels = container.querySelectorAll(".xl-sort-level");
        for (var i = 0; i < levels.length; i++) {
          levels[i].querySelector(".xl-sort-level-num").textContent = i + 1;
          var colSel = levels[i].querySelector(".xl-sort-col");
          var dirSel = levels[i].querySelector(".xl-sort-dir");
          if (colSel) colSel.id = "xl-sort-col-" + i;
          if (dirSel) dirSel.id = "xl-sort-dir-" + i;
        }
      }
    },

    _applyMultiSort() {
      var container = document.getElementById("xl-sort-levels");
      if (!container || !container.children.length) return App.warning("Add at least one sort level");
      var levels = [];
      for (var i = 0; i < container.children.length; i++) {
        var colSel = container.children[i].querySelector(".xl-sort-col");
        var dirSel = container.children[i].querySelector(".xl-sort-dir");
        var colIdx = parseInt(colSel.value, 10);
        var dir = dirSel.value;
        if (!isNaN(colIdx)) levels.push({ col: colIdx, dir: dir });
      }
      if (!levels.length) return App.warning("Select a column for each sort level");
      try {
        var d = this._gridData();
        if (!d) return App.error("No sheet data");
        var rows = d.rows;
        var map = rows._ || {};
        var keys = Object.keys(map).map(Number).filter(function (k) { return k > 0; }).sort(function (a, b) { return a - b; });
        if (keys.length < 2) return App.warning("Not enough data rows to sort");
        var cellText = function (rk, ci) {
          var c = map[rk] && map[rk].cells ? map[rk].cells[ci] : null;
          if (!c) return "";
          var v = c.value != null ? c.value : (c.text != null ? c.text : "");
          return String(v);
        };
        var cellNum = function (rk, ci) {
          var t = cellText(rk, ci);
          if (t === "") return { empty: true, num: 0 };
          var n = Number(t);
          if (!isNaN(n)) return { empty: false, num: n };
          return { empty: false, num: 0 };
        };
        keys.sort(function (a, b) {
          for (var i = 0; i < levels.length; i++) {
            var lv = levels[i];
            var an = cellNum(a, lv.col), bn = cellNum(b, lv.col);
            var cmp = 0;
            if (an.empty && bn.empty) cmp = 0;
            else if (an.empty) cmp = 1;
            else if (bn.empty) cmp = -1;
            else cmp = an.num - bn.num;
            if (cmp === 0) cmp = cellText(a, lv.col).localeCompare(cellText(b, lv.col));
            if (lv.dir === "desc") cmp = -cmp;
            if (cmp !== 0) return cmp;
          }
          return 0;
        });
        var newMap = {};
        if (map[0]) newMap[0] = map[0];
        keys.forEach(function (k, i) { newMap[i + 1] = map[k]; });
        rows._ = newMap;
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("sort");
        this._closeSortModal();
        App.success("Multi-column sort applied (" + levels.length + " level" + (levels.length > 1 ? "s" : "") + ")");
      } catch (e) { App.error("Sort failed: " + (e.message || e)); }
    },

    _buildFreezeDialog() {
      return '<div class="xl-modal-mask hidden" id="xl-freeze-mask">' +
        '<div class="xl-modal xl-freeze-dialog" id="xl-freeze-modal">' +
          '<div class="xl-modal-head"><span>Freeze Panes</span><button class="xl-modal-close" id="xl-freeze-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-cf-field"><label>Rows to freeze (from top)</label><input type="number" id="xl-freeze-rows" min="0" max="100" value="1" class="xl-cf-field" /></div>' +
            '<div class="xl-cf-field"><label>Columns to freeze (from left)</label><input type="number" id="xl-freeze-cols" min="0" max="100" value="0" class="xl-cf-field" /></div>' +
            '<div class="xl-freeze-presets" id="xl-freeze-presets">' +
              '<button class="xl-cf-btn xl-freeze-preset" data-rows="1" data-cols="0">Header row only</button>' +
              '<button class="xl-cf-btn xl-freeze-preset" data-rows="0" data-cols="1">Header column only</button>' +
              '<button class="xl-cf-btn xl-freeze-preset" data-rows="1" data-cols="1">Row + column</button>' +
              '<button class="xl-cf-btn xl-freeze-preset" data-rows="0" data-cols="0">Unfreeze all</button>' +
            '</div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-freeze-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-freeze-apply">Apply</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openFreezeDialog() {
      if (!this._grid) return App.error("Load a table first");
      var d = this._gridData();
      var f = d ? (d.freeze || [0, 0]) : [0, 0];
      var rowsEl = document.getElementById("xl-freeze-rows");
      var colsEl = document.getElementById("xl-freeze-cols");
      if (rowsEl) rowsEl.value = Number(f[0]) || 0;
      if (colsEl) colsEl.value = Number(f[1]) || 0;
      var mask = document.getElementById("xl-freeze-mask");
      if (mask) mask.classList.remove("hidden");
      var self = this;
      var presets = document.querySelectorAll(".xl-freeze-preset");
      presets.forEach(function (p) {
        p.onclick = function () {
          if (rowsEl) rowsEl.value = p.getAttribute("data-rows");
          if (colsEl) colsEl.value = p.getAttribute("data-cols");
        };
      });
    },

    _closeFreezeDialog() {
      var mask = document.getElementById("xl-freeze-mask");
      if (mask) mask.classList.add("hidden");
    },

    _applyFreeze() {
      if (!this._grid) return App.error("Load a table first");
      try {
        var d = this._gridData();
        if (!d || typeof d.setFreeze !== "function") return App.error("Freeze not supported");
        var rows = parseInt(document.getElementById("xl-freeze-rows").value, 10) || 0;
        var cols = parseInt(document.getElementById("xl-freeze-cols").value, 10) || 0;
        d.setFreeze(rows, cols);
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        var btn = document.getElementById("xl-btn-freeze");
        if (btn) btn.classList.toggle("active", rows > 0 || cols > 0);
        this._closeFreezeDialog();
        if (rows === 0 && cols === 0) App.success("Freeze cleared");
        else App.success("Frozen " + (rows > 0 ? rows + " row" + (rows > 1 ? "s" : "") : "") + (rows > 0 && cols > 0 ? " + " : "") + (cols > 0 ? cols + " col" + (cols > 1 ? "s" : "") : ""));
      } catch (e) { App.error("Freeze failed: " + (e.message || e)); }
    },

    // === Remove Duplicates ===

    _buildDupsDialog() {
      var colOpts = (this._currentColumns || []).map(function (c, i) {
        return '<label class="xl-dup-col"><input type="checkbox" class="xl-dup-col-check" value="' + i + '" checked /> ' + this._escape(c) + '</label>';
      }.bind(this)).join("");
      return '<div class="xl-modal-mask hidden" id="xl-dups-mask">' +
        '<div class="xl-modal xl-dups-dialog" id="xl-dups-modal">' +
          '<div class="xl-modal-head"><span>Remove Duplicates</span><button class="xl-modal-close" id="xl-dups-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<p class="xl-cf-hint">Select the columns to use for identifying duplicate rows. Rows with the same values in all chosen columns are removed, keeping the first occurrence.</p>' +
            '<div class="xl-cf-row-actions"><label class="flex items-center gap-2 text-sm"><input type="checkbox" id="xl-dups-all" checked /> My data has a header row</label></div>' +
            '<div class="xl-dup-cols" id="xl-dups-cols">' + (colOpts || '<p class="xl-cf-empty">No columns detected — load a table first.</p>') + '</div>' +
            '<div class="xl-tool-result hidden" id="xl-dups-result"></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-dups-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-dups-apply">Remove Duplicates</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openDupsDialog() {
      if (!this._grid) return App.error("Load a table first");
      var colOpts = (this._currentColumns || []).map(function (c, i) {
        return '<label class="xl-dup-col"><input type="checkbox" class="xl-dup-col-check" value="' + i + '" checked /> ' + this._escape(c) + '</label>';
      }.bind(this)).join("");
      var colsEl = document.getElementById("xl-dups-cols");
      if (colsEl) colsEl.innerHTML = colOpts || '<p class="xl-cf-empty">No columns detected — load a table first.</p>';
      var resultEl = document.getElementById("xl-dups-result");
      if (resultEl) { resultEl.classList.add("hidden"); resultEl.innerHTML = ""; }
      var mask = document.getElementById("xl-dups-mask");
      if (mask) mask.classList.remove("hidden");
    },

    _closeDupsDialog() {
      var mask = document.getElementById("xl-dups-mask");
      if (mask) mask.classList.add("hidden");
    },

    _applyRemoveDups() {
      if (!this._grid) return App.error("Load a table first");
      var d = this._gridData();
      if (!d) return App.error("No sheet data");
      var checks = document.querySelectorAll(".xl-dup-col-check:checked");
      var cols = [];
      checks.forEach(function (c) { cols.push(parseInt(c.value, 10)); });
      if (!cols.length) return App.warning("Select at least one column for duplicate detection");
      var hasHeader = document.getElementById("xl-dups-all").checked;
      try {
        var map = d.rows._ || {};
        var keys = Object.keys(map).map(Number).sort(function (a, b) { return a - b; });
        var seen = new Set();
        var removed = 0;
        var keep = {};
        keys.forEach(function (k) {
          if (hasHeader && k === 0) { keep[k] = map[k]; return; }
          var rowObj = map[k] || {};
          var cells = rowObj.cells || {};
          var keyParts = cols.map(function (ci) {
            var cell = cells[ci] || {};
            return cell.text != null ? String(cell.text) : (cell.value != null ? String(cell.value) : "");
          });
          var key = keyParts.join("\x01");
          if (seen.has(key)) removed++;
          else { seen.add(key); keep[k] = map[k]; }
        });
        var newMap = {};
        var newIdx = 0;
        Object.keys(keep).map(Number).sort(function (a, b) { return a - b; }).forEach(function (k) {
          newMap[newIdx++] = keep[k];
        });
        d.rows._ = newMap;
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("remove-duplicates");
        var resultEl = document.getElementById("xl-dups-result");
        if (resultEl) {
          resultEl.classList.remove("hidden");
          resultEl.innerHTML = "<h4>Remove Duplicates</h4><p class=\"text-sm\"><b>" + removed + "</b> duplicate row" + (removed === 1 ? "" : "s") + " removed (" + Object.keys(keep).length + " row" + (Object.keys(keep).length === 1 ? "" : "s") + " kept).</p>";
        }
        App.success(removed ? removed + " duplicate row" + (removed === 1 ? "" : "s") + " removed" : "No duplicates found");
        var self = this;
        setTimeout(function () { self._closeDupsDialog(); }, 900);
      } catch (e) { App.error("Remove duplicates failed: " + (e.message || e)); }
    },

    // === Text to Columns ===

    _buildTextColsModal() {
      var colOpts = (this._currentColumns || []).map(function (c, i) {
        return '<option value="' + i + '">' + this._escape(c) + '</option>';
      }.bind(this)).join("");
      return '<div class="xl-modal-mask hidden" id="xl-tc-mask">' +
        '<div class="xl-modal xl-tc-modal" id="xl-tc-modal">' +
          '<div class="xl-modal-head"><span>Text to Columns</span><button class="xl-modal-close" id="xl-tc-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-cf-fields">' +
              '<div class="xl-cf-field"><label>Source column</label><select class="xl-cf-field" id="xl-tc-col">' + colOpts + '</select></div>' +
              '<div class="xl-cf-field"><label>Delimiter</label><select class="xl-cf-field" id="xl-tc-delim">' +
                '<option value=",">Comma  ( , )</option>' +
                '<option value="\\t">Tab</option>' +
                '<option value=";">Semicolon  ( ; )</option>' +
                '<option value=" ">Space</option>' +
                '<option value="custom">Custom...</option>' +
              '</select></div>' +
              '<div class="xl-cf-field hidden" id="xl-tc-custom-wrap"><label>Custom delimiter</label><input class="xl-cf-field" id="xl-tc-custom" placeholder="|" /></div>' +
            '</div>' +
            '<div class="xl-tool-result hidden" id="xl-tc-result"></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-tc-preview"><i class="fas fa-eye"></i> Preview</button>' +
            '<button class="xl-cf-btn" id="xl-tc-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-tc-apply">Split into Columns</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openTextColsModal() {
      if (!this._grid) return App.error("Load a table first");
      var colOpts = (this._currentColumns || []).map(function (c, i) {
        return '<option value="' + i + '">' + this._escape(c) + '</option>';
      }.bind(this)).join("");
      var colEl = document.getElementById("xl-tc-col");
      if (colEl) colEl.innerHTML = colOpts;
      var sel = this._getSelection();
      if (sel && colEl && sel.col != null && this._currentColumns && sel.col < this._currentColumns.length) colEl.value = String(sel.col);
      this._setTextColsDelimState();
      var resultEl = document.getElementById("xl-tc-result");
      if (resultEl) { resultEl.classList.add("hidden"); resultEl.innerHTML = ""; }
      var mask = document.getElementById("xl-tc-mask");
      if (mask) mask.classList.remove("hidden");
      this._previewTextToColumns();
    },

    _closeTextColsModal() {
      var mask = document.getElementById("xl-tc-mask");
      if (mask) mask.classList.add("hidden");
    },

    _textColsDelimiter() {
      var delim = document.getElementById("xl-tc-delim").value;
      if (delim === "custom") return document.getElementById("xl-tc-custom").value || ",";
      if (delim === "\\t") return "\t";
      return delim;
    },

    _setTextColsDelimState() {
      var delim = document.getElementById("xl-tc-delim");
      var wrapper = document.getElementById("xl-tc-custom-wrap");
      if (delim && wrapper) wrapper.classList.toggle("hidden", delim.value !== "custom");
    },

    _previewTextToColumns() {
      this._setTextColsDelimState();
      if (!this._grid) return;
      var resultEl = document.getElementById("xl-tc-result");
      if (!resultEl) return;
      var d = this._gridData();
      if (!d) return;
      var colSel = document.getElementById("xl-tc-col");
      var colIdx = colSel ? parseInt(colSel.value, 10) : 0;
      if (isNaN(colIdx)) colIdx = 0;
      var delim = this._textColsDelimiter();
      var map = d.rows._ || {};
      var samples = [];
      var keyCount = 0;
      var uniqueKeys = new Set();
      Object.keys(map).map(Number).sort(function (a, b) { return a - b; }).forEach(function (k) {
        if (k === 0) return;
        var cell = map[k] && map[k].cells ? map[k].cells[colIdx] : null;
        var t = cell ? String(cell.text != null ? cell.text : (cell.value != null ? cell.value : "")) : "";
        if (t === "") return;
        var parts = t.split(delim);
        keyCount = Math.max(keyCount, parts.length);
        uniqueKeys.add(parts.join("\x01"));
        if (samples.length < 5) samples.push({ k: k, parts: parts });
      });
      if (!samples.length) {
        resultEl.classList.remove("hidden");
        resultEl.innerHTML = '<p class="text-sm text-secondary">No data found in the selected column.</p>';
        return;
      }
      var maxCols = Math.min(6, keyCount);
      var rowsHtml = samples.map(function (s) {
        var cells = [];
        for (var i = 0; i < maxCols; i++) cells.push('<td>' + this._escape(s.parts[i] != null ? s.parts[i] : "") + '</td>');
        return '<tr>' + cells.join("") + '</tr>';
      }.bind(this)).join("");
      resultEl.classList.remove("hidden");
      resultEl.innerHTML =
        "<h4>Preview</h4>" +
        '<p class="text-sm text-secondary">Splitting the selected column into up to <b>' + keyCount + '</b> column' + (keyCount === 1 ? "" : "s") + '.</p>' +
        '<table class="xl-tc-table"><tbody>' + rowsHtml + '</tbody></table>';
    },

    _applyTextToColumns() {
      if (!this._grid) return App.error("Load a table first");
      var d = this._gridData();
      if (!d) return App.error("No sheet data");
      var colSel = document.getElementById("xl-tc-col");
      var colIdx = colSel ? parseInt(colSel.value, 10) : 0;
      if (isNaN(colIdx)) colIdx = 0;
      var delim = this._textColsDelimiter();
      var map = d.rows._ || {};
      var rowKeys = Object.keys(map).map(Number).sort(function (a, b) { return a - b; });
      var maxCol = 0;
      rowKeys.forEach(function (k) {
        var cells = (map[k] || {}).cells || {};
        Object.keys(cells).map(Number).forEach(function (ci) { if (ci > maxCol) maxCol = ci; });
      });
      // Determine how many columns the split will need to shift right
      var splitCount = 0;
      rowKeys.forEach(function (k) {
        if (k === 0) return;
        var cell = map[k] && map[k].cells ? map[k].cells[colIdx] : null;
        var t = cell ? String(cell.text != null ? cell.text : (cell.value != null ? cell.value : "")) : "";
        if (t !== "") splitCount = Math.max(splitCount, t.split(delim).length);
      });
      if (splitCount === 0) return App.warning("No data to split in the selected column");
      var shift = splitCount - 1;
      try {
        // Shift existing columns at/after colIdx (except the split column itself) right by `shift`
        rowKeys.forEach(function (k) {
          var cells = (map[k] || {}).cells || {};
          var newCells = {};
          Object.keys(cells).map(Number).sort(function (a, b) { return a - b; }).forEach(function (ci) {
            newCells[ci + shift] = cells[ci];
          });
          if (map[k]) map[k].cells = newCells;
        });
        // Fill the split column + the shifted-in columns with split parts
        rowKeys.forEach(function (k) {
          var cell = map[k] && map[k].cells ? map[k].cells[colIdx + shift] : null;
          var t = cell ? String(cell.text != null ? cell.text : (cell.value != null ? cell.value : "")) : "";
          var parts = t.split(delim);
          for (var i = 0; i < splitCount; i++) {
            var targetCol = colIdx + i;
            var raw = parts[i] != null ? parts[i] : "";
            var targetCell = map[k].cells[targetCol] || {};
            targetCell.text = raw;
            targetCell.value = raw;
            map[k].cells[targetCol] = targetCell;
          }
        });
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("text-to-columns");
        this._closeTextColsModal();
        App.success("Column split into " + splitCount + " column" + (splitCount === 1 ? "" : "s"));
      } catch (e) { App.error("Text to columns failed: " + (e.message || e)); }
    },

    // === Go To / Go To Special ===

    _buildGoToDialog() {
      return '<div class="xl-modal-mask hidden" id="xl-goto-mask">' +
        '<div class="xl-modal xl-goto-dialog" id="xl-goto-modal">' +
          '<div class="xl-modal-head"><span>Go To / Go To Special</span><button class="xl-modal-close" id="xl-goto-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-cf-field"><label>Reference</label><input class="xl-cf-field" id="xl-goto-ref" placeholder="e.g. B5, C10:F20" /></div>' +
            '<div class="xl-goto-special"><div class="xl-goto-special-label">Go To Special</div>' +
              '<button class="xl-cf-btn" id="xl-goto-last">Last cell (used range)</button>' +
              '<button class="xl-cf-btn" id="xl-goto-blanks">First blank cell</button>' +
              '<button class="xl-cf-btn" id="xl-goto-formulas">First formula cell</button>' +
              '<button class="xl-cf-btn" id="xl-goto-constants">First constant cell</button>' +
            '</div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-goto-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-goto-go">OK</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    // === Cell Comments ===

    _buildCommentModal() {
      return '<div class="xl-modal-mask hidden" id="xl-note-mask">' +
        '<div class="xl-modal xl-note-dialog" id="xl-note-modal">' +
          '<div class="xl-modal-head"><span>Cell Comment</span><button class="xl-modal-close" id="xl-note-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-cf-field"><label>Cell</label><input class="xl-cf-field" id="xl-note-cell" readonly /></div>' +
            '<div class="xl-cf-field"><label>Comment</label><textarea class="xl-cf-field xl-note-textarea" id="xl-note-text" rows="5" placeholder="Type a note for this cell..."></textarea></div>' +
            '<div class="xl-tool-result hidden" id="xl-note-info"></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-note-delete" style="margin-right:auto;">Delete</button>' +
            '<button class="xl-cf-btn" id="xl-note-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-note-save">Save</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _buildCommentPopup() {
      return '<div class="xl-comment-popup hidden" id="xl-comment-popup"></div>';
    },

    _commentNode() {
      return this._gridData() ? this._gridData()._comments : null;
    },

    _getCellNote(r, c) {
      var d = this._gridData();
      if (!d || !d._comments) return null;
      return d._comments[r + ":" + c] || null;
    },

    _setCellNote(r, c, text) {
      var d = this._gridData();
      if (!d) return;
      if (!d._comments) d._comments = {};
      if (text && String(text).trim()) d._comments[r + ":" + c] = String(text).trim();
      else delete d._comments[r + ":" + c];
    },

    _addCommentForSelection() {
      if (!this._grid) return App.error("Load a table first");
      var sel = this._getSelection();
      if (!sel) return App.error("Select a cell first");
      this._openCommentModal(sel.row, sel.col);
    },

    _openCommentModal(r, c) {
      if (!this._grid) return App.error("Load a table first");
      var cellEl = document.getElementById("xl-note-cell");
      if (cellEl) cellEl.value = this._a1(r, c);
      var textEl = document.getElementById("xl-note-text");
      var note = this._getCellNote(r, c);
      if (textEl) textEl.value = note || "";
      var info = document.getElementById("xl-note-info");
      if (info) { info.classList.add("hidden"); info.innerHTML = ""; }
      this._commentTarget = { r: r, c: c };
      var mask = document.getElementById("xl-note-mask");
      if (mask) mask.classList.remove("hidden");
      if (textEl) textEl.focus();
    },

    _closeCommentModal() {
      var mask = document.getElementById("xl-note-mask");
      if (mask) mask.classList.add("hidden");
    },

    _saveComment() {
      var t = this._commentTarget;
      if (!t) return this._closeCommentModal();
      var text = ((document.getElementById("xl-note-text") || {}).value || "").trim();
      this._setCellNote(t.r, t.c, text);
      if (typeof this._grid.reRender === "function") this._grid.reRender();
      this._renderCommentsOverlay();
      this._closeCommentModal();
      App.success(text ? "Comment added to " + this._a1(t.r, t.c) : "Comment removed from " + this._a1(t.r, t.c));
    },

    _deleteComment() {
      var t = this._commentTarget;
      if (!t) return this._closeCommentModal();
      this._setCellNote(t.r, t.c, "");
      if (typeof this._grid.reRender === "function") this._grid.reRender();
      this._renderCommentsOverlay();
      this._closeCommentModal();
      App.success("Comment deleted from " + this._a1(t.r, t.c));
    },

    _renderCommentsOverlay() {
      var container = document.getElementById("xl-comment-overlay");
      var gridArea = document.getElementById("xl-grid");
      var d = this._gridData();
      if (!d || !d._comments) {
        if (container) { container.innerHTML = ""; }
        return;
      }
      if (!container) {
        container = document.createElement("div");
        container.id = "xl-comment-overlay";
        container.className = "xl-comment-overlay";
        if (gridArea) gridArea.appendChild(container);
      }
      container.innerHTML = "";
      var self = this;
      var indexW = (d.cols && d.cols.indexWidth) ? d.cols.indexWidth : 60;
      var sx = 0, sy = 0;
      try { if (d.scroll) { sx = d.scroll.x || 0; sy = d.scroll.y || 0; } } catch (e) { /* noop */ }
      Object.keys(d._comments).forEach(function (key) {
        var parts = key.split(":");
        var r = parseInt(parts[0], 10);
        var c = parseInt(parts[1], 10);
        var rect = null;
        try { if (d.cellRect) { var rr = d.cellRect(r, c); if (rr) rect = { left: (rr.left != null ? rr.left : 0) + indexW - sx, top: (rr.top != null ? rr.top : 0) - sy, width: rr.width || 0, height: rr.height || 0 }; } } catch (e) { rect = null; }
        if (!rect) {
          var rr2 = null;
          try { rr2 = d.cellRect ? d.cellRect(r, c) : null; } catch (e) { rr2 = null; }
          if (rr2) rect = { left: (rr2.left != null ? rr2.left : 0) + indexW, top: (rr2.top != null ? rr2.top : 0), width: rr2.width || 0, height: rr2.height || 0 };
        }
        if (!rect) return;
        var tri = document.createElement("div");
        tri.className = "xl-comment-tri";
        tri.style.left = (rect.left + rect.width - 10) + "px";
        tri.style.top = (rect.top + rect.height - 8) + "px";
        tri.setAttribute("data-r", r);
        tri.setAttribute("data-c", c);
        tri.title = self._getCellNote(r, c) || "";
        tri.addEventListener("mouseenter", function (ev) { self._showCommentPopup(r, c, ev); });
        tri.addEventListener("mouseleave", function () { self._hideCommentPopup(); });
        container.appendChild(tri);
      });
    },

    _showCommentPopup(r, c, ev) {
      var val = this._getCellNote(r, c);
      if (!val) return;
      var pop = document.getElementById("xl-comment-popup");
      if (!pop) return;
      var gridEl = document.getElementById("xl-grid");
      var ref = gridEl.getBoundingClientRect ? gridEl.getBoundingClientRect() : null;
      var label = this._a1(r, c);
      pop.innerHTML = '<div class="xl-comment-popup-title">' + this._escape(label) + '</div><div class="xl-comment-popup-body">' + this._escape(val) + '</div>';
      pop.classList.remove("hidden");
      var x = ev ? ev.clientX + 14 : (ref ? ref.left + 40 : 40);
      var y = ev ? ev.clientY + 14 : (ref ? ref.top + 40 : 40);
      if (ref) { x = x - ref.left; y = y - ref.top; }
      pop.style.left = x + "px";
      pop.style.top = y + "px";
    },

    _hideCommentPopup() {
      var pop = document.getElementById("xl-comment-popup");
      if (pop) pop.classList.add("hidden");
    },

    // === Export (with formatting / selected range) ===

    _buildExportModal() {
      var sheets = (this._grid && this._grid.getData()) || [];
      var sheetOpts = sheets.map(function (s, i) {
        return '<option value="' + i + '">' + this._escape(s.name || ("Sheet " + (i + 1))) + '</option>';
      }.bind(this)).join("");
      return '<div class="xl-modal-mask hidden" id="xl-export-mask">' +
        '<div class="xl-modal xl-export-dialog" id="xl-export-modal">' +
          '<div class="xl-modal-head"><span>Export Grid as .xlsx</span><button class="xl-modal-close" id="xl-export-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-cf-hint">Formatting (bold, italics, fill/ font colors, alignment, borders, number format) and column widths are preserved in the .xlsx file.</div>' +
            '<div class="xl-cf-field"><label>Sheet</label><select class="xl-cf-field" id="xl-export-sheet2">' + sheetOpts + '</select></div>' +
            '<div class="xl-export-scope">' +
              '<label class="xl-export-radio"><input type="radio" name="xl-export-scope" id="xl-export-full" value="full" checked /> Entire sheet</label>' +
              '<label class="xl-export-radio"><input type="radio" name="xl-export-scope" id="xl-export-range" value="range" /> Selected range</label>' +
              '<input class="xl-cf-field xl-export-range-input hidden" id="xl-export-range-field" readonly />' +
            '</div>' +
            '<div class="xl-tool-result hidden" id="xl-export-result"></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-export-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-export-run">Export .xlsx</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openExportModal() {
      if (!window.XLSX) return App.error("SheetJS not loaded");
      if (!this._grid) return App.error("Load a table first");
      var sheets = this._grid.getData() || [];
      if (!sheets.length) return App.error("Grid is empty");
      var selEl = document.getElementById("xl-export-sheet2");
      if (selEl) {
        var sheetEl = document.getElementById("xl-export-sheet");
        var idx = sheetEl && sheetEl.value !== "all" ? parseInt(sheetEl.value, 10) : 0;
        selEl.value = String(sheets[Math.min(Math.max(idx, 0), sheets.length - 1)] ? Math.min(Math.max(idx, 0), sheets.length - 1) : 0);
      }
      this._syncExportRangeField();
      var resultEl = document.getElementById("xl-export-result");
      if (resultEl) { resultEl.classList.add("hidden"); resultEl.innerHTML = ""; }
      var fullEl = document.getElementById("xl-export-full");
      if (fullEl) fullEl.checked = true;
      var mask = document.getElementById("xl-export-mask");
      if (mask) mask.classList.remove("hidden");
    },

    _closeExportModal() {
      var mask = document.getElementById("xl-export-mask");
      if (mask) mask.classList.add("hidden");
    },

    _syncExportRangeField() {
      var rangeField = document.getElementById("xl-export-range-field");
      if (!rangeField) return;
      var sel = this._getSelection();
      var rangeA1 = sel && sel.range ? this._currentSelectionRangeA1() : "";
      rangeField.value = rangeA1;
      var isRange = document.querySelector('input[name="xl-export-scope"]:checked');
      if (isRange && isRange.value === "range") rangeField.classList.remove("hidden");
    },

    _selectedExportRange() {
      var isRange = document.querySelector('input[name="xl-export-scope"]:checked');
      if (!isRange || isRange.value !== "range") return null;
      var sel = this._getSelection();
      if (!sel || !sel.range) return null;
      var rg = sel.range;
      var sri = rg.sri != null ? rg.sri : (rg.start ? rg.start.row : 0);
      var eri = rg.eri != null ? rg.eri : (rg.end ? rg.end.row : sri);
      var sci = rg.sci != null ? rg.sci : (rg.start ? rg.start.col : 0);
      var eci = rg.eci != null ? rg.eci : (rg.end ? rg.end.col : sci);
      return { sri: sri, eri: eri, sci: sci, eci: eci };
    },

    _runExport() {
      if (!window.XLSX) return App.error("SheetJS not loaded");
      if (!this._grid) return App.error("Load a table first");
      var self = this;
      var range = this._selectedExportRange();
      var wb = window.XLSX.utils.book_new();
      var sheets = this._grid.getData() || [];
      var selEl = document.getElementById("xl-export-sheet2");
      var idx = selEl ? parseInt(selEl.value, 10) : 0;
      var sheet = sheets[Math.min(Math.max(idx, 0), sheets.length - 1)] || sheets[0];
      if (!sheet) return App.error("No sheet to export");
      var ws = range ? this._sheetToWsRange(sheet, range) : this._sheetToWs(sheet);
      if (!ws) return App.error("Nothing to export");
      window.XLSX.utils.book_append_sheet(wb, ws, (sheet.name || "Sheet").slice(0, 31));
      var fname = "excel-workbench" + (range ? "-range-" + this._currentSelectionRangeA1().replace(":", "-") : "") + ".xlsx";
      window.XLSX.writeFile(wb, fname);
      var resultEl = document.getElementById("xl-export-result");
      if (resultEl) {
        resultEl.classList.remove("hidden");
        resultEl.innerHTML = "<p class=\"text-sm\"><b>Downloaded:</b> " + this._escape(fname) + (range ? " (selected range only)" : " (full sheet, formatting preserved)") + ".</p>";
      }
      App.success("Exported " + (range ? "selected range " + this._currentSelectionRangeA1() : fname));
    },

    _sheetToWsRange(sheet, range) {
      if (!sheet || !sheet.rows) return null;
      var rows = sheet.rows;
      var self = this;
      var sri = range.sri, eri = range.eri, sci = range.sci, eci = range.eci;
      var ws = {};
      var outputRows = 0, outputCols = 0;
      for (var r = sri; r <= eri; r++) {
        var cells = (rows[r] || {}).cells || {};
        for (var c = sci; c <= eci; c++) {
          var cell = cells[c] || {};
          var text = cell.text != null ? String(cell.text) : "";
          var formula = cell.f != null ? String(cell.f) : "";
          var wsCell;
          if (formula.charAt(0) === "=") {
            wsCell = { f: formula.substring(1) };
          } else {
            var num = Number(text);
            if (text !== "" && !isNaN(num)) wsCell = { v: num, t: "n" };
            else wsCell = { v: text, t: "s" };
          }
          var styleData = this._gridData();
          var stylesMap = styleData ? styleData.styles : null;
          var cellStyle = null;
          if (stylesMap && cell && cell.style != null) cellStyle = stylesMap[cell.style];
          if (cellStyle) {
            var s = {};
            if (cellStyle.font) { if (cellStyle.font.bold) s.bold = true; if (cellStyle.font.italic) s.italic = true; }
            if (cellStyle.underline) s.underline = true;
            if (cellStyle.color) s.font = { color: { rgb: cellStyle.color.replace("#", "") } };
            if (cellStyle.bgcolor) s.fill = { fgColor: { rgb: cellStyle.bgcolor.replace("#", "") } };
            if (cellStyle.align === "center") s.alignment = { horizontal: "center" };
            else if (cellStyle.align === "right") s.alignment = { horizontal: "right" };
            else if (cellStyle.align === "left") s.alignment = { horizontal: "left" };
            if (cellStyle.format) s.numFmt = cellStyle.format;
            if (cellStyle.border) {
              var bSide = cellStyle.border.top || cellStyle.border.bottom || cellStyle.border.left || cellStyle.border.right;
              if (bSide) {
                var bStyleName = (bSide[0] || "thin");
                var bRgb = String(bSide[1] != null ? bSide[1] : "#000000").replace("#", "");
                s.border = { top: { style: bStyleName, color: { rgb: bRgb } }, bottom: { style: bStyleName, color: { rgb: bRgb } }, left: { style: bStyleName, color: { rgb: bRgb } }, right: { style: bStyleName, color: { rgb: bRgb } } };
              }
            }
            wsCell.s = s;
          }
          ws[window.XLSX.utils.encode_cell({ r: r - sri, c: c - sci })] = wsCell;
          if (r - sri > outputRows) outputRows = r - sri;
          if (c - sci > outputCols) outputCols = c - sci;
        }
      }
      ws["!ref"] = window.XLSX.utils.encode_range({ s: { c: 0, r: 0 }, e: { c: outputCols, r: outputRows } });
      var colWidths = [];
      for (var wc = sci; wc <= eci; wc++) {
        var w = null;
        try { w = this._gridData().cols ? this._gridData().cols.getWidth(wc) : null; } catch (e) { w = null; }
        colWidths.push({ wch: Math.max(5, Math.round((w || 80) / 7)) });
      }
      ws["!cols"] = colWidths;
      return ws;
    },

    // === Print / Print Preview ===

    _buildPrintModal() {
      return '<div class="xl-modal-mask hidden" id="xl-print-mask">' +
        '<div class="xl-modal xl-print-dialog" id="xl-print-modal">' +
          '<div class="xl-modal-head"><span>Print Preview</span><button class="xl-modal-close" id="xl-print-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-print-options">' +
              '<label class="xl-export-radio"><input type="radio" name="xl-print-orient" id="xl-print-portrait" value="portrait" checked /> Portrait</label>' +
              '<label class="xl-export-radio"><input type="radio" name="xl-print-orient" id="xl-print-landscape" value="landscape" /> Landscape</label>' +
            '</div>' +
            '<p class="xl-cf-hint">Preview below shows how pages will break. The "Print" button opens your browser print dialog with page breaks applied.</p>' +
            '<div class="xl-print-preview" id="xl-print-preview"><div class="xl-print-page"><table class="xl-print-table"></table></div></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-print-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-print-do"><i class="fas fa-print"></i> Print</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openPrintModal() {
      if (!this._grid) return App.error("Load a table first");
      this._renderPrintPreview();
      var mask = document.getElementById("xl-print-mask");
      if (mask) mask.classList.remove("hidden");
    },

    _closePrintModal() {
      var mask = document.getElementById("xl-print-mask");
      if (mask) mask.classList.add("hidden");
    },

    _printActiveSheet() {
      var sheets = this._grid ? this._grid.getData() || [] : [];
      if (!sheets.length) return null;
      var sheetEl = document.getElementById("xl-export-sheet");
      var idx = sheetEl && sheetEl.value !== "all" ? parseInt(sheetEl.value, 10) : 0;
      return sheets[Math.min(Math.max(idx, 0), sheets.length - 1)] || sheets[0];
    },

    _printMaxCols() {
      var sheet = this._printActiveSheet();
      if (!sheet || !sheet.rows) return 0;
      var max = 0;
      Object.keys(sheet.rows).forEach(function (k) {
        if (!/^\d+$/.test(k)) return;
        var cells = (sheet.rows[k] || {}).cells || {};
        Object.keys(cells).forEach(function (ck) { var n = parseInt(ck, 10); if (n > max) max = n; });
      });
      return max;
    },

    _renderPrintPreview() {
      var area = document.getElementById("xl-print-preview");
      if (!area) return;
      var sheet = this._printActiveSheet();
      if (!sheet || !sheet.rows) { area.innerHTML = "<p class=\"xl-cf-empty\">Nothing to print.</p>"; return; }
      var rows = sheet.rows;
      var rKeys = Object.keys(rows).filter(function (k) { return /^\d+$/.test(k); }).map(Number).sort(function (a, b) { return a - b; });
      var maxCol = this._printMaxCols();
      var styleData = this._gridData();
      var stylesMap = styleData ? styleData.styles : null;
      var html = "<table class=\"xl-print-table\">";
      rKeys.forEach(function (rk) {
        var cells = (rows[rk] || {}).cells || {};
        html += "<tr>";
        for (var c = 0; c <= maxCol; c++) {
          var cell = cells[c] || {};
          var text = cell.text != null ? String(cell.text) : "";
          var st = "";
          if (stylesMap && cell.style != null && stylesMap[cell.style]) {
            var cs = stylesMap[cell.style];
            var inline = [];
            if (cs.font && cs.font.bold) inline.push("font-weight:bold");
            if (cs.font && cs.font.italic) inline.push("font-style:italic");
            if (cs.color) inline.push("color:" + cs.color);
            if (cs.bgcolor) inline.push("background:" + cs.bgcolor);
            if (cs.align === "center") inline.push("text-align:center");
            else if (cs.align === "right") inline.push("text-align:right");
            if (inline.length) st = " style=\"" + inline.join(";") + "\"";
          }
          html += "<td" + st + ">" + this._escape(text) + "</td>";
        }
        html += "</tr>";
      }.bind(this));
      html += "</table>";
      area.innerHTML = this._wrapPrintPages(html);
      var landscapes = document.querySelector('input[name="xl-print-orient"]:checked');
      var pg = area.querySelector(".xl-print-preview");
      if (pg) pg.classList.toggle("xl-print-landscape", landscapes && landscapes.value === "landscape");
    },

    _wrapPrintPages(html) {
      var rows = String(html).match(/<tr>[\s\S]*?<\/tr>/g) || [];
      var rowsPerPage = 25;
      var pages = [];
      for (var i = 0; i < rows.length; i += rowsPerPage) pages.push(rows.slice(i, i + rowsPerPage).join(""));
      if (!pages.length) pages.push("");
      return pages.map(function (p, n) {
        return '<div class="xl-print-page">' +
          '<div class="xl-print-page-num">Page ' + (n + 1) + ' of ' + pages.length + '</div>' +
          '<table class="xl-print-table">' + p + '</table>' +
        '</div>';
      }).join("");
    },

    _doPrint() {
      var html = document.getElementById("xl-print-preview").innerHTML;
      if (!html) return App.error("Nothing to print");
      var sheetName = (this._printActiveSheet() || {}).name || "Sheet";
      var title = this._escape(sheetName);
      var landscape = false;
      var orientEl = document.querySelector('input[name="xl-print-orient"]:checked');
      if (orientEl) landscape = orientEl.value === "landscape";
      var printWin = window.open("", "_blank", "width=1000,height=900");
      if (!printWin) return App.error("Pop-up blocked - allow pop-ups to print");
      var pages = String(html).replace(/\sclass="xl-print-preview"/g, "").replace(/<div class="xl-cf-empty"[\s\S]*?<\/div>/g, "");
      document.__xlPrintCss = "";
      var style = "html,body{margin:0;padding:0}.xl-print-page{page-break-after:always;padding:24px 28px;box-sizing:border-box;}" +
        ".xl-print-page:last-child{page-break-after:auto}.xl-print-page-num{font-size:11px;color:#666;margin-bottom:6px;}" +
        ".xl-print-table{border-collapse:collapse;width:100%;font-size:12px;font-family:Arial,Helvetica,sans-serif;}" +
        ".xl-print-table td{border:1px solid #999;padding:4px 6px;}" +
        "@page{size:" + (landscape ? "landscape" : "A4") + ";}";
      printWin.document.write("<!doctype html><html><head><title>" + title + "</title><style>" + style + "</style></head><body>" +
        "<div class=\"xl-print-head\">" + title + "</div>" + pages + "</body></html>");
      printWin.document.close();
      setTimeout(function () {
        printWin.focus();
        printWin.print();
      }, 350);
      this._closePrintModal();
      App.success("Printing " + sheetName);
    },

    // === Copy chart image to clipboard ===

    _buildChartCopyModal() {
      return '<div class="xl-modal-mask hidden" id="xl-chartcopy-mask">' +
        '<div class="xl-modal xl-chartcopy-dialog" id="xl-chartcopy-modal">' +
          '<div class="xl-modal-head"><span>Copy Chart Image</span><button class="xl-modal-close" id="xl-chartcopy-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-cf-hint">Select a chart to copy its image as PNG to the clipboard (or download it).</div>' +
            '<div class="xl-chartcopy-list" id="xl-chartcopy-list"></div>' +
            '<div class="xl-tool-result hidden" id="xl-chartcopy-result"></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-chartcopy-cancel">Close</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _chartCanvasList() {
      var list = [];
      var fixed = document.getElementById("xl-chart-canvas");
      if (fixed) list.push({ id: "xl-chart-canvas", label: "Chart Builder canvas" });
      (this._chartIds || []).forEach(function (id) {
        if (id && id !== "xl-chart-canvas" && document.getElementById(id)) {
          list.push({ id: id, label: "Chart " + (list.length + 1) });
        }
      });
      return list;
    },

    _openChartCopyModal() {
      if (!window.Charts || typeof window.Charts.copyImage !== "function" && typeof window.Charts.exportPng !== "function") {
        return App.error("Chart export lib not available");
      }
      var list = this._chartCanvasList();
      var listEl = document.getElementById("xl-chartcopy-list");
      if (!listEl) return;
      var resultEl = document.getElementById("xl-chartcopy-result");
      if (resultEl) { resultEl.classList.add("hidden"); resultEl.innerHTML = ""; }
      if (!list.length) {
        listEl.innerHTML = "<p class=\"xl-cf-empty\">No charts rendered yet. Build a chart using the Chart Builder tool first.</p>";
      } else {
        var self = this;
        listEl.innerHTML = list.map(function (c, i) {
          return '<div class="xl-chartcopy-row" data-id="' + self._escape(c.id) + '">' +
            '<div class="xl-chartcopy-info"><canvas id="thumb-' + self._escape(c.id) + '" class="xl-chartcopy-thumb"></canvas><span>' + self._escape(c.label) + '</span></div>' +
            '<div class="xl-chartcopy-actions">' +
              '<button class="xl-cf-btn" data-copy="' + self._escape(c.id) + '"><i class="fas fa-copy"></i> Copy</button>' +
              '<button class="xl-cf-btn" data-dl="' + self._escape(c.id) + '"><i class="fas fa-download"></i> PNG</button>' +
            '</div>' +
          '</div>';
        }).join("");
        listEl.querySelectorAll("button[data-copy]").forEach(function (b) {
          b.addEventListener("click", function () { self._copyChartToClipboard(b.getAttribute("data-copy")); });
        });
        listEl.querySelectorAll("button[data-dl]").forEach(function (b) {
          b.addEventListener("click", function () { self._downloadChartPng(b.getAttribute("data-dl")); });
        });
        var seen = new Set();
        list.forEach(function (c) {
          if (seen.has(c.id)) return; seen.add(c.id);
          var thumb = document.getElementById("thumb-" + c.id);
          if (thumb) {
            var src = document.getElementById(c.id);
            if (src) {
              var ctx = thumb.getContext && thumb.getContext("2d");
              if (ctx) {
                try { thumb.width = src.width; thumb.height = src.height; ctx.drawImage(src, 0, 0); } catch (e) { /* noop */ }
              }
            }
          }
        });
      }
      var mask = document.getElementById("xl-chartcopy-mask");
      if (mask) mask.classList.remove("hidden");
    },

    _closeChartCopyModal() {
      var mask = document.getElementById("xl-chartcopy-mask");
      if (mask) mask.classList.add("hidden");
    },

    _copyChartToClipboard(id) {
      if (!window.Charts) return App.error("Chart lib not available");
      return Promise.resolve()
        .then(function () {
          if (typeof window.Charts.copyImage === "function") {
            return window.Charts.copyImage(id).then(function () {
              App.success("Chart image copied to clipboard");
            });
          }
          var canvas = document.getElementById(id);
          if (canvas && typeof canvas.toBlob === "function") {
            canvas.toBlob(function (blob) {
              try {
                navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]).then(function () {
                  App.success("Chart image copied to clipboard");
                }, function () { App.error("Copy to clipboard failed"); });
              } catch (e) { App.error("Copy to clipboard failed: " + (e.message || e)); }
            });
            return Promise.resolve();
          }
          return Promise.reject(new Error("No chart to copy"));
        })
        .catch(function (e) { App.error("Copy chart failed: " + (e.message || e)); });
    },

    _downloadChartPng(id) {
      if (!window.Charts || typeof window.Charts.exportPng !== "function") return App.error("Chart PNG export not available");
      try {
        window.Charts.exportPng(id, "chart.png");
        App.success("Chart image downloaded as chart.png");
      } catch (e) { App.error("Download failed: " + (e.message || e)); }
    },

    // === Right-click Context Menu ===

    _buildContextMenu() {
      return '<div class="xl-ctx-menu hidden" id="xl-ctx-menu">' +
        '<div class="xl-ctx-item" id="xl-ctx-cut"><i class="fas fa-scissors"></i> Cut <kbd>Ctrl+X</kbd></div>' +
        '<div class="xl-ctx-item" id="xl-ctx-copy"><i class="fas fa-copy"></i> Copy <kbd>Ctrl+C</kbd></div>' +
        '<div class="xl-ctx-item" id="xl-ctx-paste"><i class="fas fa-paste"></i> Paste <kbd>Ctrl+V</kbd></div>' +
        '<div class="xl-ctx-sep"></div>' +
        '<div class="xl-ctx-item" id="xl-ctx-insert-row"><i class="fas fa-arrow-down-to-bracket"></i> Insert Row</div>' +
        '<div class="xl-ctx-item" id="xl-ctx-delete-row"><i class="fas fa-arrow-up-from-bracket"></i> Delete Row</div>' +
        '<div class="xl-ctx-item" id="xl-ctx-insert-col"><i class="fas fa-arrow-right-to-bracket"></i> Insert Column</div>' +
        '<div class="xl-ctx-item" id="xl-ctx-delete-col"><i class="fas fa-arrow-right-from-bracket"></i> Delete Column</div>' +
        '<div class="xl-ctx-sep"></div>' +
        '<div class="xl-ctx-item" id="xl-ctx-format"><i class="fas fa-paint-roller"></i> Format Cells</div>' +
        '<div class="xl-ctx-item" id="xl-ctx-name"><i class="fas fa-tag"></i> Define Name</div>' +
        '<div class="xl-ctx-sep"></div>' +
        '<div class="xl-ctx-item" id="xl-ctx-comment"><i class="fas fa-comment"></i> Add/Edit Comment</div>' +
        '<div class="xl-ctx-item hidden" id="xl-ctx-delete-comment"><i class="fas fa-comment-slash"></i> Delete Comment</div>' +
        '<div class="xl-ctx-item" id="xl-ctx-validation"><i class="fas fa-list-check"></i> Data Validation</div>' +
        '<div class="xl-ctx-item hidden" id="xl-ctx-clear-validation"><i class="fas fa-ban"></i> Clear Validation</div>' +
      '</div>';
    },

    _contextCell: null,

    _onGridContextMenu(e) {
      if (!this._grid) return;
      var sel = this._getSelection();
      var r = -1, c = -1;
      if (sel && sel.row != null) { r = sel.row; c = sel.col; }
      else {
        try { r = this._gridData().selector.ri; c = this._gridData().selector.ci; } catch (x) { r = -1; c = -1; }
      }
      if (r < 0 || c < 0) return;
      e.preventDefault();
      this._contextCell = { r: r, c: c };
      var hasNote = !!this._getCellNote(r, c);
      var hasVal = this._cellHasValidation(r, c);
      var delNote = document.getElementById("xl-ctx-delete-comment");
      if (delNote) delNote.classList.toggle("hidden", !hasNote);
      var clrVal = document.getElementById("xl-ctx-clear-validation");
      if (clrVal) clrVal.classList.toggle("hidden", !hasVal);
      var menu = document.getElementById("xl-ctx-menu");
      if (!menu) return;
      menu.classList.remove("hidden");
      var gridEl = document.getElementById("xl-grid");
      var rect = gridEl ? gridEl.getBoundingClientRect() : null;
      var x = e.clientX, y = e.clientY;
      if (rect) { x = e.clientX - rect.left; y = e.clientY - rect.top; }
      var mw = 240;
      if (rect && x + mw > rect.width) x = rect.width - mw - 8;
      menu.style.left = Math.max(0, x) + "px";
      menu.style.top = Math.max(0, y) + "px";
      return false;
    },

    _hideContextMenu() {
      var menu = document.getElementById("xl-ctx-menu");
      if (menu) menu.classList.add("hidden");
    },

    _ctxAddComment() {
      var t = this._contextCell;
      this._hideContextMenu();
      if (!t) return;
      this._openCommentModal(t.r, t.c);
    },

    _ctxDeleteComment() {
      var t = this._contextCell;
      this._hideContextMenu();
      if (!t) return;
      this._setCellNote(t.r, t.c, "");
      if (typeof this._grid.reRender === "function") this._grid.reRender();
      this._renderCommentsOverlay();
      App.success("Comment deleted from " + this._a1(t.r, t.c));
    },

    _ctxValidation() {
      var t = this._contextCell;
      this._hideContextMenu();
      if (!t) return;
      this._openValidationModal(t.r, t.c);
    },

    _ctxClearValidation() {
      var t = this._contextCell;
      this._hideContextMenu();
      if (!t) return;
      this._clearValidationAt(t.r, t.c);
    },

    // Standard Excel clipboard: an in-memory tab-separated representation
    // shared by Cut/Copy/Paste (also mirrored to navigator.clipboard when possible).
    _selectionBox() {
      var sel = this._getSelection();
      if (!sel) return null;
      var range = sel.range;
      var sri = sel.row, sci = sel.col, eri = sel.row, eci = sel.col;
      if (range) {
        if (range.sri != null && range.eri != null && range.sci != null && range.eci != null) {
          sri = range.sri; eri = range.eri; sci = range.sci; eci = range.eci;
        } else if (range.ri != null && range.rh != null) {
          sri = range.ri; eri = range.ri + range.rh - 1; sci = range.ci; eci = range.ci + range.rw - 1;
        }
      }
      return { sri: sri, eri: eri, sci: sci, eci: eci };
    },

    _clipboardFromSelection(clearAfter) {
      var d = this._gridData();
      var box = this._selectionBox();
      if (!d || !box) return null;
      var r0 = box.sri, r1 = box.eri, c0 = box.sci, c1 = box.eci;
      var rows = [];
      for (var r = r0; r <= r1; r++) {
        var line = [];
        for (var c = c0; c <= c1; c++) {
          var cell = (d.rows._[r] || {}).cells ? d.rows._[r].cells[c] : null;
          var t = cell ? (cell.text != null ? cell.text : (cell.value != null ? String(cell.value) : "")) : "";
          line.push(t);
        }
        rows.push(line.join("\t"));
      }
      var payload = rows.join("\n");
      if (payload !== "") {
        try { if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(payload).catch(function () {}); } catch (e) {}
      }
      this._xlClipboard = {
        rows: rows.length,
        cols: c1 - c0 + 1,
        data: rows,
        mode: clearAfter ? "cut" : "copy"
      };
      if (clearAfter) {
        for (var rr = r0; rr <= r1; rr++) {
          for (var cc = c0; cc <= c1; cc++) {
            var cr = d.rows._[rr] || {};
            if (cr.cells) delete cr.cells[cc];
          }
        }
        if (typeof this._grid.reRender === "function") this._grid.reRender();
      }
      return this._xlClipboard;
    },

    _ctxCut() {
      this._hideContextMenu();
      if (!this._grid) return App.error("Load a table first");
      var res = this._clipboardFromSelection(true);
      if (res) App.success("Cut " + res.rows + "×" + res.cols + " cell" + (res.rows * res.cols === 1 ? "" : "s"));
      else App.warning("No cells selected to cut");
    },

    _ctxCopy() {
      this._hideContextMenu();
      if (!this._grid) return App.error("Load a table first");
      var res = this._clipboardFromSelection(false);
      if (res) App.success("Copied " + res.rows + "×" + res.cols + " cell" + (res.rows * res.cols === 1 ? "" : "s"));
      else App.warning("No cells selected to copy");
    },

    _ctxPaste() {
      this._hideContextMenu();
      if (!this._grid) return App.error("Load a table first");
      var d = this._gridData();
      var box = this._selectionBox();
      if (!d || !box || !this._xlClipboard) return App.warning("Clipboard is empty - copy or cut cells first");
      var r0 = box.sri;
      var c0 = box.sci;
      for (var r = 0; r < this._xlClipboard.rows; r++) {
        var tr = r0 + r;
        if (!d.rows._[tr]) d.rows._[tr] = {};
        if (!d.rows._[tr].cells) d.rows._[tr].cells = {};
        var src = this._xlClipboard.data[r] || "";
        var parts = src.split("\t");
        for (var c = 0; c < parts.length && c < this._xlClipboard.cols; c++) {
          d.rows._[tr].cells[c0 + c] = { text: parts[c], value: parts[c] };
        }
      }
      if (typeof this._grid.reRender === "function") this._grid.reRender();
      if (typeof d.change === "function") d.change("paste");
      App.success("Pasted " + this._xlClipboard.rows + "×" + this._xlClipboard.cols + " cell" + (this._xlClipboard.rows * this._xlClipboard.cols === 1 ? "" : "s"));
    },

    _ctxInsertRow() {
      this._hideContextMenu();
      this.insertRow();
    },

    _ctxDeleteRow() {
      this._hideContextMenu();
      this.deleteRow();
    },

    _shiftColumn(d, at, dir) {
      var map = d.rows._ || {};
      var keys = Object.keys(map).map(Number).sort(function (a, b) { return a - b; });
      keys.forEach(function (rk) {
        var cells = (map[rk] || {}).cells || {};
        var colKeys = Object.keys(cells).map(Number).sort(function (a, b) { return dir > 0 ? b - a : a - b; });
        colKeys.forEach(function (ck) {
          if (dir > 0) { if (ck >= at) cells[ck + 1] = cells[ck]; }
          else { if (ck > at) cells[ck - 1] = cells[ck]; }
        });
        if (dir < 0) delete cells[at];
      });
      if (dir > 0) {
        keys.forEach(function (rk) {
          if (!map[rk]) map[rk] = {};
          if (!map[rk].cells) map[rk].cells = {};
          map[rk].cells[at] = {};
        });
      }
      if (d.cols.len != null) d.cols.len = Math.max(0, Number(d.cols.len) + dir);
      if (d.cols && d.cols.len == null && d.colLen != null) d.colLen = Math.max(0, Number(d.colLen) + dir);
    },

    _ctxInsertCol() {
      this._hideContextMenu();
      if (!this._grid) return App.error("Load a table first");
      var sel = this._getSelection();
      var at = sel && sel.col != null ? sel.col : 0;
      try {
        this._shiftColumn(this._gridData(), at, 1);
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        var d = this._gridData();
        if (typeof d.change === "function") d.change("insert-col");
        App.success("Column inserted at position " + (at + 1));
      } catch (e) { App.error("Insert column failed: " + (e.message || e)); }
    },

    _ctxDeleteCol() {
      this._hideContextMenu();
      if (!this._grid) return App.error("Load a table first");
      var sel = this._getSelection();
      var at = sel && sel.col != null ? sel.col : 0;
      try {
        this._shiftColumn(this._gridData(), at, -1);
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        var d = this._gridData();
        if (typeof d.change === "function") d.change("delete-col");
        App.success("Column " + this._colName(at) + " deleted");
      } catch (e) { App.error("Delete column failed: " + (e.message || e)); }
    },

    _ctxFormat() {
      this._hideContextMenu();
      if (!this._grid) return App.error("Load a table first");
      this._openFormatModal();
    },

    _ctxDefineName() {
      this._hideContextMenu();
      this._openDefineNameModal();
    },

    _getSelectionRef() {
      var box = this._selectionBox();
      if (!box) return "";
      return this._a1(box.sri, box.sci) + ":" + this._a1(box.eri, box.eci);
    },

    _buildFormatModal() {
      return '<div class="xl-modal-mask hidden" id="xl-fmt-mask">' +
        '<div class="xl-modal" id="xl-fmt-modal">' +
          '<div class="xl-modal-head"><span><i class="fas fa-paint-roller mr-1"></i>Format Cells</span><button class="xl-modal-close" id="xl-fmt-close" title="Close"><i class="fas fa-xmark"></i></button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-fmt-grid">' +
              '<label class="xl-fmt-check"><input type="checkbox" id="xl-fmt-bold" /> <b>Bold</b></label>' +
              '<label class="xl-fmt-check"><input type="checkbox" id="xl-fmt-italic" /> <i>Italic</i></label>' +
              '<label class="xl-fmt-check"><input type="checkbox" id="xl-fmt-underline" /> <u>Underline</u></label>' +
              '<label>Alignment' +
                '<select id="xl-fmt-align">' +
                  '<option value="">Default / Left</option>' +
                  '<option value="center">Center</option>' +
                  '<option value="right">Right</option>' +
                '</select>' +
              '</label>' +
              '<label>Number format' +
                '<select id="xl-fmt-num">' +
                  '<option value="">General</option>' +
                  '<option value="0.00">Number</option>' +
                  '<option value="$#,##0.00">Currency</option>' +
                  '<option value="0.00%">Percent</option>' +
                  '<option value="yyyy-mm-dd">Date</option>' +
                  '<option value="#,##0">Comma</option>' +
                '</select>' +
              '</label>' +
              '<div class="xl-fmt-color-row">' +
                '<span>Font color</span><div class="xl-color-wrap xl-fmt-color-wrap" id="xl-fmt-font-color-wrap"><button class="xl-color-btn" id="xl-fmt-btn-font-color" title="Font color"><i class="fas fa-font"></i></button><span class="xl-color-indicator" id="xl-fmt-font-color-indicator" style="background:#000000;"></span></div>' +
                '<span>Fill color</span><div class="xl-color-wrap xl-fmt-color-wrap" id="xl-fmt-bg-color-wrap"><button class="xl-color-btn" id="xl-fmt-btn-bg-color" title="Fill color"><i class="fas fa-fill-drip"></i></button><span class="xl-color-indicator" id="xl-fmt-bg-color-indicator" style="background:#ffff00;"></span></div>' +
              '</div>' +
              '<div class="xl-fmt-actions">' +
                '<button class="btn btn-sm btn-outline" id="xl-fmt-border-all" type="button">All borders</button>' +
                '<button class="btn btn-sm btn-outline" id="xl-fmt-border-none" type="button">No borders</button>' +
              '</div>' +
            '</div>' +
            '<p class="xl-cf-hint">Applies to the currently selected cell or range.</p>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="btn btn-outline" id="xl-fmt-cancel" type="button">Cancel</button>' +
            '<button class="btn btn-primary" id="xl-fmt-apply" type="button">Apply</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openFormatModal() {
      var sel = this._getSelection();
      var hdr = document.querySelector("#xl-fmt-modal .xl-modal-head span");
      if (hdr) hdr.textContent = " Format Cells" + (sel && sel.sri != null ? " — " + this._a1(sel.sri, sel.sci) + ":" + this._a1(sel.eri, sel.eci) : "");
      var style = this._getSelectedCellStyle() || {};
      var f = style.font || {};
      var boldEl = document.getElementById("xl-fmt-bold");
      var italEl = document.getElementById("xl-fmt-italic");
      var ulEl = document.getElementById("xl-fmt-underline");
      if (boldEl) boldEl.checked = !!f.bold;
      if (italEl) italEl.checked = !!f.italic;
      if (ulEl) ulEl.checked = !!style.underline;
      var alignEl = document.getElementById("xl-fmt-align");
      if (alignEl) alignEl.value = style.align || "";
      if (style.color) { var fi = document.getElementById("xl-fmt-font-color-indicator"); if (fi) fi.style.background = style.color; }
      if (style.bgcolor) { var bi = document.getElementById("xl-fmt-bg-color-indicator"); if (bi) bi.style.background = style.bgcolor; }
      var mask = document.getElementById("xl-fmt-mask");
      if (mask) mask.classList.remove("hidden");
      this._openFmtPalettes();
    },

    _openFmtPalettes() {
      this._buildColorPalette("xl-fmt-btn-font-color-palette");
      this._buildColorPalette("xl-fmt-btn-bg-color-palette");
      var fw = document.getElementById("xl-fmt-font-color-wrap");
      var bw = document.getElementById("xl-fmt-bg-color-wrap");
      if (fw && !document.getElementById("xl-fmt-btn-font-color-palette")) {
        var p = document.createElement("div");
        p.id = "xl-fmt-btn-font-color-palette";
        p.className = "xl-color-palette hidden";
        fw.appendChild(p);
      }
      if (bw && !document.getElementById("xl-fmt-btn-bg-color-palette")) {
        var p2 = document.createElement("div");
        p2.id = "xl-fmt-btn-bg-color-palette";
        p2.className = "xl-color-palette hidden";
        bw.appendChild(p2);
      }
      this._buildColorPalette("xl-fmt-btn-font-color-palette");
      this._buildColorPalette("xl-fmt-btn-bg-color-palette");
    },

    _closeFormatModal() {
      var mask = document.getElementById("xl-fmt-mask");
      if (mask) mask.classList.add("hidden");
    },

    _applyFormatModal() {
      if (!this._grid) return App.error("Load a table first");
      var boldEl = document.getElementById("xl-fmt-bold");
      var italEl = document.getElementById("xl-fmt-italic");
      var ulEl = document.getElementById("xl-fmt-underline");
      var alignEl = document.getElementById("xl-fmt-align");
      var numEl = document.getElementById("xl-fmt-num");
      var data = this._gridData();
      if (!data) return App.error("No sheet data");
      if (boldEl) data.setSelectedCellAttr("font-bold", !!boldEl.checked);
      if (italEl) data.setSelectedCellAttr("font-italic", !!italEl.checked);
      if (ulEl) data.setSelectedCellAttr("underline", !!ulEl.checked);
      if (alignEl && alignEl.value) data.setSelectedCellAttr("align", alignEl.value);
      if (numEl && numEl.value) data.setSelectedCellAttr("format", numEl.value);
      if (typeof this._grid.reRender === "function") this._grid.reRender();
      this._dirty = true;
      this._updateFormatToolbar();
      this._closeFormatModal();
      App.success("Format applied");
    },

    // === Named ranges (Go To) ===

    _buildDefineNameModal() {
      var opts = (this._namedRangeNames ? Object.keys(this._namedRangeNames) : []).map(function (n) {
        return '<option value="' + this._escape(n) + '">' + this._escape(n) + ' (' + this._escape(this._namedRangeNames[n]) + ')</option>';
      }.bind(this)).join("");
      return '<div class="xl-modal-mask hidden" id="xl-name-mask">' +
        '<div class="xl-modal" id="xl-name-modal">' +
          '<div class="xl-modal-head"><span><i class="fas fa-tag mr-1"></i>Named Ranges</span><button class="xl-modal-close" id="xl-name-close" title="Close"><i class="fas fa-xmark"></i></button></div>' +
          '<div class="xl-modal-body">' +
            '<p class="xl-cf-hint">Name a cell or range (e.g. <code>Sales</code> or <code>Region1</code>) to jump to it quickly from the Name Box.</p>' +
            '<label>Range reference<input class="xl-cf-field" id="xl-name-ref" placeholder="e.g. A1:C50" value="' + this._escape(this._getSelectionRef()) + '" /></label>' +
            '<label>Name<input class="xl-cf-field" id="xl-name-name" placeholder="e.g. Sales2024" /></label>' +
            '<div class="xl-name-list" id="xl-name-list"></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="btn btn-outline" id="xl-name-close2" type="button">Close</button>' +
            '<button class="btn btn-primary" id="xl-name-save" type="button">Define Name</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openDefineNameModal() {
      var refEl = document.getElementById("xl-name-ref");
      if (refEl) refEl.value = this._getSelectionRef();
      this._renderNamedRangeList();
      var mask = document.getElementById("xl-name-mask");
      if (mask) mask.classList.remove("hidden");
    },

    _closeDefineNameModal() {
      var mask = document.getElementById("xl-name-mask");
      if (mask) mask.classList.add("hidden");
    },

    _namedRangeNames: {},
    _namedRangeRefs: {},

    _renderNamedRangeList() {
      var list = document.getElementById("xl-name-list");
      if (!list) return;
      var names = this._namedRangeNames || {};
      var self = this;
      var keys = Object.keys(names);
      if (!keys.length) {
        list.innerHTML = '<div class="xl-cf-hint">No named ranges yet.</div>';
        return;
      }
      var html = "";
      keys.forEach(function (n) {
        html += '<div class="xl-name-row"><span class="xl-name-item" data-name="' + self._escape(n) + '" title="Go to ' + self._escape(names[n]) + '">' +
          '<i class="fas fa-tag mr-1"></i>' + self._escape(n) + ' — <code>' + self._escape(names[n]) + '</code>' +
          '</span><button class="xl-name-goto" data-name="' + self._escape(n) + '" title="Go to"><i class="fas fa-arrow-right"></i></button>' +
          '<button class="xl-name-del" data-name="' + self._escape(n) + '" title="Delete"><i class="fas fa-trash"></i></button></div>';
      });
      list.innerHTML = html;
      list.querySelectorAll(".xl-name-goto").forEach(function (b) {
        b.addEventListener("click", function () { self._gotoNamed(b.getAttribute("data-name")); });
      });
      list.querySelectorAll(".xl-name-del").forEach(function (b) {
        b.addEventListener("click", function () { self._deleteNamed(b.getAttribute("data-name")); });
      });
      list.querySelectorAll(".xl-name-item").forEach(function (s) {
        s.addEventListener("click", function () { self._gotoNamed(s.getAttribute("data-name")); });
      });
    },

    _saveNamedRange() {
      var refEl = document.getElementById("xl-name-ref");
      var nameEl = document.getElementById("xl-name-name");
      if (!refEl || !nameEl) return;
      var ref = (refEl.value || "").trim();
      var name = (nameEl.value || "").trim();
      if (!ref) return App.warning("Enter a range reference (e.g. A1:C50)");
      if (!name) return App.warning("Enter a name for the range");
      if (!/^[A-Za-z_][A-Za-z0-9_.]*$/.test(name)) return App.warning("Name must start with a letter or underscore and contain only letters, numbers, _ or .");
      var parsed = this._parseRange(ref);
      if (!parsed) return App.warning("Invalid range reference: " + ref);
      this._namedRangeNames = this._namedRangeNames || {};
      this._namedRangeRefs = this._namedRangeRefs || {};
      this._namedRangeNames[name] = ref;
      this._namedRangeRefs[name] = parsed;
      this._renderNamedRangeList();
      nameEl.value = "";
      App.success("Named range '" + name + "' defined for " + ref);
      this._refreshNameBox();
    },

    _deleteNamed(name) {
      delete this._namedRangeNames[name];
      delete this._namedRangeRefs[name];
      this._renderNamedRangeList();
      this._refreshNameBox();
    },

    _gotoNamed(name) {
      var range = this._namedRangeNames && this._namedRangeNames[name];
      if (!range) return App.warning("Unknown named range: " + name);
      var parsed = this._parseRange(range);
      if (!parsed) return App.warning("Invalid named range: " + range);
      this._closeDefineNameModal();
      this._selectAndScrollCell(parsed.sri, parsed.sci);
      App.success("Jumped to " + name + " (" + range + ")");
    },

    _refreshNameBox() {
      var box = document.getElementById("xl-name-box");
      if (!box) return;
      var names = this._namedRangeNames || {};
      var keys = Object.keys(names);
      var html = '<option value="">(None)</option>';
      keys.forEach(function (n) {
        html += '<option value="' + this._escape(n) + '">' + this._escape(n) + '</option>';
      }.bind(this));
      box.innerHTML = html;
      box.value = "";
    },

    _cellHasValidation(r, c) {
      try {
        var d = this._gridData();
        if (!d || !d.validations) return false;
        return !!d.validations.get(r, c);
      } catch (e) { return false; }
    },

    // === Data Validation (list dropdown) ===

    _buildValidationModal() {
      return '<div class="xl-modal-mask hidden" id="xl-val-mask">' +
        '<div class="xl-modal xl-val-dialog" id="xl-val-modal">' +
          '<div class="xl-modal-head"><span>Data Validation</span><button class="xl-modal-close" id="xl-val-close">&times;</button></div>' +
          '<div class="xl-modal-body">' +
            '<div class="xl-cf-hint">Restrict the cell to a list of allowed values. Cells with this rule show a dropdown arrow.</div>' +
            '<div class="xl-cf-field"><label>Apply to cell</label><input class="xl-cf-field" id="xl-val-cell" readonly /></div>' +
            '<div class="xl-cf-field"><label>Allow</label><select class="xl-cf-field" id="xl-val-type"><option value="list" selected>List</option></select></div>' +
            '<div class="xl-cf-field"><label>Source (comma-separated values)</label><input class="xl-cf-field" id="xl-val-source" placeholder="e.g. Low, Medium, High" /></div>' +
            '<div class="xl-cf-field"><label>Default value (optional, applied to the cell)</label><input class="xl-cf-field" id="xl-val-default" placeholder="e.g. Low" /></div>' +
          '</div>' +
          '<div class="xl-modal-foot">' +
            '<button class="xl-cf-btn" id="xl-val-remove" style="margin-right:auto;">Clear</button>' +
            '<button class="xl-cf-btn" id="xl-val-cancel">Cancel</button>' +
            '<button class="xl-cf-btn primary" id="xl-val-save">Apply</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    },

    _openValidationModal(r, c) {
      if (!this._grid) return App.error("Load a table first");
      var cellEl = document.getElementById("xl-val-cell");
      if (cellEl) cellEl.value = this._a1(r, c);
      var src = document.getElementById("xl-val-source");
      var def = document.getElementById("xl-val-default");
      if (src) src.value = "";
      if (def) def.value = "";
      var existing = null;
      try { existing = this._gridData().validations ? this._gridData().validations.get(r, c) : null; } catch (e) { existing = null; }
      if (existing && existing.validator && existing.validator.type === "list") {
        var v = existing.validator.value;
        if (Array.isArray(v)) { if (src) src.value = v.join(", "); if (def && v.length) def.value = v[0]; }
        else if (typeof v === "string") {
          var raw = v.split(",").map(function (s) { return s.trim(); }).filter(function (s) { return s !== ""; });
          if (src) src.value = raw.join(", ");
          var cur = null;
          try { cur = this._gridData().getCell(r, c); } catch (e) { cur = null; }
          var curTxt = cur && cur.text != null ? String(cur.text) : "";
          if (def) def.value = raw.includes(curTxt) ? curTxt : (raw[0] || "");
        }
      } else {
        var cur2 = null;
        try { cur2 = this._gridData().getCell(r, c); } catch (e) { cur2 = null; }
        if (def) def.value = (cur2 && typeof cur2.text !== "undefined" && cur2.text !== null) ? String(cur2.text) : "";
      }
      this._validationTarget = { r: r, c: c };
      var mask = document.getElementById("xl-val-mask");
      if (mask) mask.classList.remove("hidden");
      if (src) src.focus();
    },

    _closeValidationModal() {
      var mask = document.getElementById("xl-val-mask");
      if (mask) mask.classList.add("hidden");
    },

    _openValidationForSelection() {
      if (!this._grid) return App.error("Load a table first");
      var sel = this._getSelection();
      if (!sel) return App.error("Select a cell first");
      this._openValidationModal(sel.row, sel.col);
    },

    _saveValidation() {
      var t = this._validationTarget;
      if (!t) return this._closeValidationModal();
      var d = this._gridData();
      if (!d) return App.error("No sheet data");
      var raw = ((document.getElementById("xl-val-source") || {}).value || "").split(",").map(function (s) { return s.trim(); }).filter(function (s) { return s !== ""; });
      if (!raw.length) return App.warning("Enter at least one allowed value");
      var def = ((document.getElementById("xl-val-default") || {}).value || "").trim();
      var ref = this._a1(t.r, t.c);
      try {
        if (typeof d.addValidation === "function") {
          d.addValidation("cell", ref, { type: "list", operator: "eq", required: false, value: raw.join(",") });
        } else {
          var cell = d.getCell(t.r, t.c) || {};
          var st = cell.style;
          if (!d.rows._[t.r]) d.rows._[t.r] = {};
          if (!d.rows._[t.r].cells) d.rows._[t.r].cells = {};
          var nc = d.rows._[t.r].cells[t.c] || {};
          nc._validation = raw.slice();
          if (st) nc.style = st;
          d.rows._[t.r].cells[t.c] = nc;
        }
        if (def) {
          this._writeCellText(d, t.r, t.c, def);
        }
        if (typeof d.change === "function") d.change("validation");
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        this._closeValidationModal();
        App.success("Validation applied to " + ref);
      } catch (e) { App.error("Validation failed: " + (e.message || e)); }
    },

    _removeValidation() {
      var t = this._validationTarget;
      if (!t) return this._closeValidationModal();
      this._clearValidationAt(t.r, t.c);
      this._closeValidationModal();
      App.success("Validation cleared");
    },

    _clearValidationAt(r, c) {
      var d = this._gridData();
      if (!d) return;
      try {
        var ref = this._a1(r, c);
        if (typeof d.removeValidation === "function") {
          try {
            d.selector = d.selector || {};
            d.selector.range = { sri: r, sci: c, eri: r, eci: c };
            d.removeValidation();
          } catch (e) { /* noop */ }
        } else {
          var cell = d.getCell(r, c) || {};
          if (cell._validation) { delete cell._validation; if (cell.text != null) this._writeCellText(d, r, c, cell.text); }
          var v = d.validations; if (v && v.remove) { try { v.remove({ sri: r, sci: c, eri: r, eci: c }); } catch (e2) { /* noop */ } }
        }
        this._renderCommentsOverlay();
        if (typeof d.change === "function") d.change("validation");
        if (typeof this._grid.reRender === "function") this._grid.reRender();
      } catch (e) { App.error("Clear validation failed: " + (e.message || e)); }
    },

    _writeCellText(d, r, c, text) {
      if (!d || !d.rows || !d.rows._) return;
      if (!d.rows._[r]) d.rows._[r] = {};
      if (!d.rows._[r].cells) d.rows._[r].cells = {};
      var cell = d.rows._[r].cells[c] || {};
      var style = cell.style;
      cell.style = style;
      cell.text = String(text);
      cell.value = String(text);
      if (cell.cellFormat || cell.dataFormat) { cell.value = cell.text; }
      d.rows._[r].cells[c] = cell;
      if (typeof d.rows.set === "function") { try { d.rows.set(r, c, cell); } catch (e) { /* noop */ } }
    },

    _openGoToDialog() {
      if (!this._grid) return App.error("Load a table first");
      var refEl = document.getElementById("xl-goto-ref");
      if (refEl) {
        var sel = this._getSelection();
        if (sel) refEl.value = this._a1(sel.row, sel.col);
      }
      var mask = document.getElementById("xl-goto-mask");
      if (mask) mask.classList.remove("hidden");
      if (refEl) refEl.focus();
    },

    _closeGoToDialog() {
      var mask = document.getElementById("xl-goto-mask");
      if (mask) mask.classList.add("hidden");
    },

    _selectAndScrollCell(row, col) {
      if (!this._grid) return;
      try {
        if (this._grid.sheet && this._grid.sheet.selector && typeof this._grid.sheet.selector.set === "function") {
          this._grid.sheet.selector.set(row, col);
        } else if (this._gridData && this._gridData().selector) {
          try { this._gridData().selector.range = { sri: row, sci: col, eri: row, eci: col }; } catch (e) { /* noop */ }
        }
        if (this._grid.sheet && this._grid.sheet.table && typeof this._grid.sheet.table.scrollTo === "function") {
          try { this._grid.sheet.table.scrollTo({ row: row, col: col }); } catch (e) { /* noop */ }
        }
        if (typeof this._grid.reRender === "function") this._grid.reRender();
      } catch (e) { /* noop */ }
    },

    _dataEdges() {
      var d = this._gridData();
      var e = { firstRow: 0, lastRow: 0, firstCol: 0, lastCol: 0, hasData: false };
      if (!d || !d.rows || !d.rows._) return e;
      var map = d.rows._;
      var rKeys = Object.keys(map).map(Number).sort(function (a, b) { return a - b; });
      if (!rKeys.length) return e;
      var maxC = 0;
      rKeys.forEach(function (rk) {
        var cells = (map[rk] || {}).cells || {};
        Object.keys(cells).forEach(function (ck) { var n = parseInt(ck, 10); if (n > maxC) maxC = n; });
      });
      var lastRow = rKeys[rKeys.length - 1];
      e.firstRow = 0;
      e.lastRow = lastRow;
      e.firstCol = 0;
      e.lastCol = maxC;
      e.hasData = true;
      return e;
    },

    _navDataEdge(key) {
      var sel = this._getSelection();
      var curRow = sel && sel.row != null ? sel.row : 0;
      var curCol = sel && sel.col != null ? sel.col : 0;
      var edges = this._dataEdges();
      var row = curRow, col = curCol;
      if (key === "ArrowDown") row = edges.lastRow;
      else if (key === "ArrowUp") row = edges.firstRow;
      else if (key === "ArrowRight") col = edges.lastCol;
      else if (key === "ArrowLeft") col = edges.firstCol;
      else if (key === "Home") { row = edges.firstRow; col = edges.firstCol; }
      else if (key === "End") { row = edges.lastRow; col = edges.lastCol; }
      this._selectAndScrollCell(row, col);
    },

    _gotoRun() {
      if (!this._grid) return App.error("Load a table first");
      var ref = ((document.getElementById("xl-goto-ref") || {}).value || "").trim();
      if (!ref) return App.warning("Enter a cell reference (e.g. B5) or range (e.g. C10:F20)");
      var par = this._parseRange(ref);
      if (!par) return App.error("Invalid reference: " + ref);
      this._selectAndScrollCell(par.sri, par.sci);
      this._closeGoToDialog();
      App.success("Went to " + this._a1(par.sri, par.sci));
    },

    _gotoSpecial(kind) {
      if (!this._grid) return App.error("Load a table first");
      var d = this._gridData();
      if (!d) return App.error("No sheet data");
      var map = d.rows._ || {};
      var rowKeys = Object.keys(map).map(Number).sort(function (a, b) { return a - b; });
      var target = null;
      if (kind === "last") {
        var lastRow = -1, lastCol = -1;
        rowKeys.forEach(function (k) {
          var cells = (map[k] || {}).cells || {};
          Object.keys(cells).map(Number).forEach(function (ci) {
            var cell = cells[ci] || {};
            var t = cell.text != null ? String(cell.text) : (cell.value != null ? String(cell.value) : "");
            if (t !== "" || (cell.f != null && cell.f !== "")) { if (k > lastRow) lastRow = k; if (ci > lastCol) lastCol = ci; }
          });
        });
        if (lastRow >= 0) target = { row: lastRow, col: lastCol < 0 ? 0 : lastCol };
      } else if (kind === "blanks" || kind === "constants") {
        var checkConstant = (kind === "constants");
        outer:
        for (var r = 1; r < rowKeys.length; r++) {
          var cells = (map[rowKeys[r]] || {}).cells || {};
          var colsInRow = Object.keys(cells).map(Number).sort(function (a, b) { return a - b; });
          var maxC = Math.max((this._currentColumns ? this._currentColumns.length : 10) - 1, colsInRow.length ? colsInRow[colsInRow.length - 1] : 0);
          for (var c = 0; c <= maxC; c++) {
            var cell = cells[c] || {};
            var t = cell.text != null ? String(cell.text) : (cell.value != null ? String(cell.value) : "");
            if (kind === "blanks") {
              if (t === "") { target = { row: rowKeys[r], col: c }; break outer; }
            } else {
              if (t !== "" && t.trim() !== "") { target = { row: rowKeys[r], col: c }; break outer; }
            }
          }
        }
      } else if (kind === "formulas") {
        outer2:
        for (var r2 = 1; r2 < rowKeys.length; r2++) {
          var cells2 = (map[rowKeys[r2]] || {}).cells || {};
          Object.keys(cells2).map(Number).sort(function (a, b) { return a - b; }).forEach(function (ci) {
            var cell = cells2[ci] || {};
            if (cell.f != null && String(cell.f).charAt(0) === "=") { target = { row: rowKeys[r2], col: ci }; return; }
          });
          if (target) break outer2;
        }
      }
      if (target) {
        this._selectAndScrollCell(target.row, target.col);
        this._closeGoToDialog();
        App.success("Went to " + this._a1(target.row, target.col));
      } else {
        App.warning("No " + kind + " found in the grid");
      }
    },

    // === File Import ===

    _setupFileImport() {
      var self = this;
      var wrapper = document.getElementById("xl-grid-wrapper");
      var overlay = document.getElementById("xl-drop-overlay");
      var fileInput = document.getElementById("xl-file-input");
      if (!wrapper || !overlay || !fileInput) return;

      // Drag events
      var dragCounter = 0;
      wrapper.addEventListener("dragenter", function (e) {
        e.preventDefault();
        e.stopPropagation();
        dragCounter++;
        overlay.classList.remove("hidden");
      });
      wrapper.addEventListener("dragleave", function (e) {
        e.preventDefault();
        e.stopPropagation();
        dragCounter--;
        if (dragCounter <= 0) { dragCounter = 0; overlay.classList.add("hidden"); }
      });
      wrapper.addEventListener("dragover", function (e) {
        e.preventDefault();
        e.stopPropagation();
      });
      wrapper.addEventListener("drop", function (e) {
        e.preventDefault();
        e.stopPropagation();
        dragCounter = 0;
        overlay.classList.add("hidden");
        var files = e.dataTransfer && e.dataTransfer.files;
        if (files && files.length) {
          self._handleDroppedFile(files[0]);
        }
      });

      // File input change
      fileInput.addEventListener("change", function () {
        if (fileInput.files && fileInput.files.length) {
          self._handleDroppedFile(fileInput.files[0]);
          fileInput.value = "";
        }
      });
    },

    _openFile() {
      var fileInput = document.getElementById("xl-file-input");
      if (fileInput) fileInput.click();
    },

    _handleDroppedFile(file) {
      var self = this;
      var name = file.name || "Imported";
      var ext = (name.split(".").pop() || "").toLowerCase();

      if (ext === "csv") {
        var reader = new FileReader();
        reader.onload = function (ev) {
          var text = ev.target.result || "";
          var result = self._parseCSV(text);
          if (result && result.columns && result.columns.length) {
            self._loadFileIntoGrid(result.columns, result.rows, name.replace(/\.[^.]+$/, ""));
            App.success("Imported " + name + " -- " + result.rows.length + " rows, " + result.columns.length + " columns.");
          } else {
            App.error("Could not parse CSV file.");
          }
        };
        reader.readAsText(file);
      } else if (ext === "json") {
        var reader2 = new FileReader();
        reader2.onload = function (ev) {
          var text = ev.target.result || "";
          var result = self._parseJSON(text);
          if (result && result.columns && result.columns.length) {
            self._loadFileIntoGrid(result.columns, result.rows, name.replace(/\.[^.]+$/, ""));
            App.success("Imported " + name + " -- " + result.rows.length + " rows, " + result.columns.length + " columns.");
          } else {
            App.error("Could not parse JSON file.");
          }
        };
        reader2.readAsText(file);
      } else if (ext === "xlsx" || ext === "xls") {
        var reader3 = new FileReader();
        reader3.onload = function (ev) {
          var data = new Uint8Array(ev.target.result);
          var result = self._parseXLSX(data);
          if (result && result.columns && result.columns.length) {
            self._loadFileIntoGrid(result.columns, result.rows, name.replace(/\.[^.]+$/, ""));
            App.success("Imported " + name + " -- " + result.rows.length + " rows, " + result.columns.length + " columns.");
          } else {
            App.error("Could not parse Excel file.");
          }
        };
        reader3.readAsArrayBuffer(file);
      } else {
        App.error("Unsupported file type: ." + ext + ". Use .xlsx, .csv, or .json");
      }
    },

    _parseCSV(text) {
      var lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
      if (!lines.length) return { columns: [], rows: [] };
      // Remove trailing empty lines
      while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
      if (!lines.length) return { columns: [], rows: [] };

      function parseRow(line) {
        var result = [];
        var i = 0;
        while (i < line.length) {
          if (line.charAt(i) === '"') {
            var j = i + 1;
            var val = "";
            while (j < line.length) {
              if (line.charAt(j) === '"') {
                if (j + 1 < line.length && line.charAt(j + 1) === '"') {
                  val += '"';
                  j += 2;
                } else {
                  j++;
                  break;
                }
              } else {
                val += line.charAt(j);
                j++;
              }
            }
            result.push(val);
            i = j + 1;
            if (i < line.length && line.charAt(i) === ",") i++;
          } else {
            var k = line.indexOf(",", i);
            if (k < 0) { result.push(line.substring(i)); break; }
            result.push(line.substring(i, k));
            i = k + 1;
          }
        }
        return result;
      }

      var columns = parseRow(lines[0]).map(function (c, i) {
        return (c && c.trim()) || ("Col" + (i + 1));
      });
      var rows = [];
      for (var i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        var parsed = parseRow(lines[i]);
        var row = {};
        columns.forEach(function (c, ci) {
          row[c] = ci < parsed.length ? parsed[ci] : "";
        });
        rows.push(row);
      }
      return { columns: columns, rows: rows };
    },

    _parseJSON(text) {
      try {
        var data = JSON.parse(text);
        if (!Array.isArray(data)) {
          if (typeof data === "object" && data !== null) {
            var keys = Object.keys(data);
            for (var k = 0; k < keys.length; k++) {
              if (Array.isArray(data[keys[k]])) { data = data[keys[k]]; break; }
            }
          }
          if (!Array.isArray(data)) return { columns: [], rows: [] };
        }
        if (!data.length) return { columns: [], rows: [] };
        // If first element is an array, treat as matrix
        if (Array.isArray(data[0])) {
          var cols = data[0].map(function (c, i) { return String(c != null ? c : "Col" + (i + 1)); });
          var rows = [];
          for (var i = 1; i < data.length; i++) {
            var row = {};
            cols.forEach(function (c, ci) {
              row[c] = ci < data[i].length ? (data[i][ci] != null ? String(data[i][ci]) : "") : "";
            });
            rows.push(row);
          }
          return { columns: cols, rows: rows };
        }
        // Array of objects
        var colSet = [];
        var colSeen = {};
        data.forEach(function (obj) {
          Object.keys(obj).forEach(function (k) {
            if (!colSeen[k]) { colSeen[k] = true; colSet.push(k); }
          });
        });
        var rows2 = data.map(function (obj) {
          var row = {};
          colSet.forEach(function (c) {
            row[c] = obj[c] != null ? String(obj[c]) : "";
          });
          return row;
        });
        return { columns: colSet, rows: rows2 };
      } catch (e) {
        App.error("JSON parse error: " + e.message);
        return { columns: [], rows: [] };
      }
    },

    _parseXLSX(data) {
      if (!window.XLSX) { App.error("SheetJS not loaded -- cannot parse .xlsx"); return { columns: [], rows: [] }; }
      try {
        var wb = window.XLSX.read(data, { type: "array" });
        var sheetName = wb.SheetNames[0];
        if (!sheetName) return { columns: [], rows: [] };
        var sheet = wb.Sheets[sheetName];
        var aoa = window.XLSX.utils.sheet_to_json(sheet, { header: 1, defval: "" });
        if (!aoa.length) return { columns: [], rows: [] };
        var columns = (aoa[0] || []).map(function (c, i) { return (c != null && String(c).trim()) || ("Col" + (i + 1)); });
        var rows = [];
        for (var i = 1; i < aoa.length; i++) {
          var row = {};
          columns.forEach(function (c, ci) {
            var v = ci < aoa[i].length ? aoa[i][ci] : "";
            row[c] = v != null ? String(v) : "";
          });
          rows.push(row);
        }
        return { columns: columns, rows: rows };
      } catch (e) {
        App.error("XLSX parse error: " + e.message);
        return { columns: [], rows: [] };
      }
    },

    _loadFileIntoGrid(columns, rows, name) {
      this._currentTable = "";
      this._currentColumns = columns;
      this._pivotColumns = null;
      this._pivotMatrix = null;
      var sheets = this._rowsToSheets(columns, rows, name || "Sheet1");
      this._tableSheets = sheets;
      if (this._grid) this._grid.loadData(sheets);
      this._loadTableFormulas(columns, rows, name || "Sheet1");
      var count = rows.length;
      var msg = "\"" + name + "\" -- " + count.toLocaleString() + " rows \u00D7 " + columns.length + " columns imported. Edit cells, type formulas (=SUM(A1:A5)), add sheets.";
      this._setGridNote(msg);
      this._fillColumnDropdowns(columns);
      this._populateMerge();
      this._populateForecastCols();
      this._refreshExportSheets();
      this._renderSheetTabs(0);
      var self = this;
      setTimeout(function () {
        self._updateStatusBar(0, 0);
        self._readSelectionAndUpdate();
      }, 200);
      this._startSelectionPoll();
    },

    // === Grid Init ===

    _initGrid(el) {
      var XS = window.x_spreadsheet || window.xSpreadsheet;
      if (!XS || !el) {
        el.innerHTML = '<div class="p-6 text-center text-secondary text-sm">Spreadsheet engine (x-spreadsheet) failed to load from CDN -- check your internet connection.</div>';
        return;
      }
      try {
        this._grid = new XS("#" + el.id);
        var self = this;
        if (typeof this._grid.change === "function") {
          this._grid.change(function () {
            self._dirty = true;
            self._onGridChanged();
          });
        }
        this._hideXSBuiltIn();
        var gridEl = document.getElementById(el.id);
        this._bindSelectionListeners(gridEl);
        this._renderSheetTabs(0);
        this._cfAllowed = true;
      } catch (e) {
        App.error("Spreadsheet engine failed to start: " + (e.message || e));
      }
    },

    _hideXSBuiltIn() {
      if (!this._grid) return;
      try {
        var gridEl = document.getElementById("xl-grid");
        var container = (gridEl && gridEl.querySelector(".x-spreadsheet")) || gridEl;
        if (!container) return;
        var tb = container.querySelector(".x-spreadsheet-toolbar");
        if (tb) tb.style.display = "none";
        var bb = container.querySelector(".x-spreadsheet-bottombar");
        if (bb) bb.style.display = "none";
        container.classList.add("xl-hide-xs-ui");
      } catch (e) { /* noop */ }
    },

    // === Formula System ===

    _formulaReady: false,

    _loadFormulaScripts() {
      if (this._formulaReady) return;
      var self = this;
      var engineSrc = "js/pages/formula-engine.js?v=" + Date.now();
      var barSrc = "js/pages/formula-bar.js?v=" + Date.now();

      function loadScript(src) {
        return new Promise(function (resolve, reject) {
          var existing = document.querySelector('script[src^="' + src.split("?")[0] + '"]');
          if (existing) { resolve(); return; }
          var s = document.createElement("script");
          s.src = src;
          s.onload = resolve;
          s.onerror = reject;
          document.head.appendChild(s);
        });
      }

      loadScript(engineSrc)
        .then(function () { return loadScript(barSrc); })
        .then(function () {
          self._formulaReady = true;
          if (window.FormulaEngine) {
            window.FormulaEngine.init();
          }
          self._initFormulaBar();
          console.log("[ExcelPage] Formula system loaded OK");
        })
        .catch(function (e) {
          console.warn("[ExcelPage] Formula scripts failed to load:", e);
        });
    },

    _initFormulaBar() {
      if (!this._formulaReady || !window.FormulaBar || !this._grid) return;
      var gridEl = document.getElementById("xl-grid");
      if (!gridEl) return;
      try {
        window.FormulaBar.init(gridEl, this._grid);
        var sheetName = this._currentTable || "Sheet1";
        if (window.FormulaEngine) {
          window.FormulaEngine.addSheet(sheetName);
        }
      } catch (e) {
        console.warn("[ExcelPage] FormulaBar init failed:", e);
      }
    },

    _onGridChanged() {
      if (!this._formulaReady || !window.FormulaEngine || !window.FormulaEngine.isAvailable()) return;
      if (this._updating) return;
      this._updating = true;
      var self = this;
      try {
        var sheets = this._grid.getData() || [];
        var FE = window.FormulaEngine;

        if (typeof FE.listSheets === "function") {
          var gridNames = {};
          for (var gs = 0; gs < sheets.length; gs++) gridNames[sheets[gs].name || ("Sheet" + (gs + 1))] = true;
          FE.listSheets().forEach(function (oldName) {
            if (!gridNames[oldName]) FE.removeSheet(oldName);
          });
        }

        for (var si = 0; si < sheets.length; si++) {
          var s = sheets[si];
          var sName = s.name || ("Sheet" + (si + 1));
          FE.addSheet(sName);
          if (!s.rows) continue;
          var rKeys = Object.keys(s.rows).filter(function (k) { return /^\d+$/.test(k); }).map(Number).sort(function (a, b) { return a - b; });
          for (var ri = 0; ri < rKeys.length; ri++) {
            var rowIdx = rKeys[ri];
            var cells = (s.rows[rowIdx] || {}).cells || {};
            var cKeys = Object.keys(cells).map(Number).sort(function (a, b) { return a - b; });
            for (var ci = 0; ci < cKeys.length; ci++) {
              var colIdx = cKeys[ci];
              var cell = cells[colIdx] || {};
              var text = cell.text != null ? String(cell.text) : "";
              var f = cell.f != null ? String(cell.f) : "";
              if (text.charAt(0) === "=") {
                FE.setCell(sName, rowIdx, colIdx, text);
              } else if (f.charAt(0) === "=") {
                if (cell.v != null && String(cell.v) === text) {
                  FE.setCell(sName, rowIdx, colIdx, f);
                } else {
                  delete cell.f;
                  delete cell.v;
                  var numOver = Number(text);
                  FE.setCell(sName, rowIdx, colIdx, isNaN(numOver) ? text : numOver);
                }
              } else {
                var num = Number(text);
                FE.setCell(sName, rowIdx, colIdx, isNaN(num) ? text : num);
              }
            }
          }
        }

        for (var si2 = 0; si2 < sheets.length; si2++) {
          FE.recalcSheet(sheets[si2].name || ("Sheet" + (si2 + 1)));
        }

        for (var si3 = 0; si3 < sheets.length; si3++) {
          var s3 = sheets[si3];
          var sName3 = s3.name || ("Sheet" + (si3 + 1));
          if (!s3.rows) continue;
          var rKeys3 = Object.keys(s3.rows).filter(function (k) { return /^\d+$/.test(k); }).map(Number).sort(function (a, b) { return a - b; });
          for (var ri3 = 0; ri3 < rKeys3.length; ri3++) {
            var rowIdx3 = rKeys3[ri3];
            var cells3 = (s3.rows[rowIdx3] || {}).cells || {};
            var cKeys3 = Object.keys(cells3).map(Number).sort(function (a, b) { return a - b; });
            for (var ci3 = 0; ci3 < cKeys3.length; ci3++) {
              var colIdx3 = cKeys3[ci3];
              var cell3 = cells3[colIdx3] || {};
              var formula3 = cell3.f != null ? String(cell3.f) : "";
              var text3 = formula3 || (cell3.text != null ? String(cell3.text) : "");
              if (text3.charAt(0) === "=") {
                var result = FE.getCellValue(sName3, rowIdx3, colIdx3);
                if (result !== "" && result != null) {
                  var displayVal = String(result);
                  if (result && typeof result === "object" && result.type) {
                    displayVal = self._mapHFError(result);
                  }
                  cell3.f = text3;
                  cell3.v = displayVal;
                  if (cell3.text !== displayVal) cell3.text = displayVal;
                }
              }
            }
          }
        }
      } catch (e) {
        console.warn("[ExcelPage] Formula evaluation error:", e);
      } finally {
        this._updating = false;
      }
    },

    _mapHFError(err) {
      if (!err || !err.type) return "#VALUE!";
      var map = {
        "ERROR": "#VALUE!", "NAME": "#NAME?", "VALUE": "#VALUE!",
        "REF": "#REF!", "DIV_BY_ZERO": "#DIV/0!", "NULL": "#NULL!",
        "NA": "#N/A", "NUM": "#NUM!", "SPILL": "#SPILL!",
      };
      return map[err.type] || "#VALUE!";
    },

    _loadTableFormulas(columns, rows, sheetName) {
      if (!this._formulaReady || !window.FormulaEngine || !window.FormulaEngine.isAvailable()) return;
      var FE = window.FormulaEngine;
      var data2d = [];
      data2d.push(columns.slice());
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        var arr = [];
        for (var j = 0; j < columns.length; j++) {
          var v = Array.isArray(row) ? row[j] : row[columns[j]];
          arr.push(v != null ? v : "");
        }
        data2d.push(arr);
      }
      FE.loadSheet(sheetName, data2d);
    },

    // === Selection / Status Bar ===

    _lastGridNote: "",
    _selectionPollId: null,
    _lastPollRow: -1,
    _lastPollCol: -1,
    _lastPollSheet: "",
    _selectionBound: false,

    _getSelection() {
      if (!this._grid) return null;
      try {
        var g = this._grid;
        var data = (g.sheet && g.sheet.data) || g.data || (g.datas && g.datas[0]) || null;
        var sel = null;
        if (data && data.selector) sel = data.selector;
        else if (data && data.select) sel = data.select;
        if (!sel && typeof g.getSelected === "function") {
          try {
            var s = g.getSelected();
            sel = s && (s.range ? { ri: s.sri, sci: undefined, ci: s.sci, range: s.range } : s);
          } catch (e) { /* noop */ }
        }
        var row = -1, col = -1, range = null;
        if (sel) {
          if (sel.ri != null) { row = sel.ri; col = sel.ci; }
          else if (sel.row != null) { row = sel.row; col = sel.col; }
          else if (sel.range && sel.range.sri != null) { row = sel.range.sri; col = sel.range.sci; }
          else if (sel.range && sel.range.start && sel.range.start.row != null) { row = sel.range.start.row; col = sel.range.start.col; }
          range = sel.range || null;
        }
        if (row < 0 || col < 0) return null;
        return { row: row, col: col, range: range };
      } catch (e) { return null; }
    },

    _activeSheetName() {
      try {
        var g = this._grid;
        var data = (g.sheet && g.sheet.data) || (g.datas && g.datas[0]);
        if (data && data.name) return String(data.name);
      } catch (e) { /* noop */ }
      return this._currentTable || "Sheet1";
    },

    _activeSheetIndex() {
      try {
        var g = this._grid;
        var d = (g.sheet && g.sheet.data) || g.data;
        var i = (g.datas || []).indexOf(d);
        return i >= 0 ? i : 0;
      } catch (e) { return 0; }
    },

    _startSelectionPoll() {
      var self = this;
      if (this._selectionPollId) clearInterval(this._selectionPollId);
      this._selectionPollId = setInterval(function () {
        if (!self._grid) { clearInterval(self._selectionPollId); self._selectionPollId = null; return; }
        try {
          var sel = self._getSelection();
          if (!sel) return;
          if (sel.row === self._lastPollRow && sel.col === self._lastPollCol && self._activeSheetName() === self._lastPollSheet) return;
          self._lastPollRow = sel.row;
          self._lastPollCol = sel.col;
          self._lastPollSheet = self._activeSheetName();
self._syncFormulaBar(sel.row, sel.col);
          self._updateStatusBar(sel.row, sel.col, sel.range);
          self._updateFormatToolbar();
          self._refreshCfIfEnabled();
          self._refreshAnnotationOverlays();
        } catch (e) { /* noop */ }
      }, 250);
    },

    _refreshCfIfEnabled() {
      var key = this._condSheetKey();
      if (!this._condRules[key] || !this._condRules[key].length) return;
      var now = Date.now();
      if (this._lastCfRefresh && now - this._lastCfRefresh < 1500) return;
      this._lastCfRefresh = now;
      try { this._applyConditionalFormatting(); } catch (e) { /* noop */ }
    },

    _refreshAnnotationOverlays() {
      var now = Date.now();
      if (this._lastAnnRefresh && now - this._lastAnnRefresh < 1200) return;
      this._lastAnnRefresh = now;
      try { this._renderCommentsOverlay(); } catch (e) { /* noop */ }
    },


    _readSelectionAndUpdate() {
      if (!this._grid) return;
      try {
        var sel = this._getSelection();
        if (!sel) return;
        this._lastPollRow = sel.row;
        this._lastPollCol = sel.col;
        this._lastPollSheet = this._activeSheetName();
        this._syncFormulaBar(sel.row, sel.col);
        this._updateStatusBar(sel.row, sel.col, sel.range);
        this._updateFormatToolbar();
      } catch (e) { /* noop */ }
    },

    _bindSelectionListeners(gridEl) {
      if (!gridEl || this._selectionBound) return;
      this._selectionBound = true;
      var self = this;
      var h = function () { setTimeout(function () { self._readSelectionAndUpdate(); }, 30); };
      gridEl.addEventListener("mousedown", h);
      gridEl.addEventListener("click", h);
    },

    _detectColumnFromMouse(e) {
      if (!this._grid || !e) return;
      try {
        var g = this._grid;
        var gridEl = document.getElementById("xl-grid");
        if (!gridEl) return;
        var rect = gridEl.getBoundingClientRect();
        var mouseX = e.clientX - rect.left;
        var colWidths = [];
        if (g._ && g._.col) {
          var colLen = g._.col.len || 0;
          for (var i = 0; i < colLen; i++) {
            var w = 80;
            try { w = g._.col.getWidth(i); } catch (ex) { try { w = g._.col.width; } catch (ex2) {} }
            colWidths.push(w);
          }
        }
        if (colWidths.length === 0 && g._ && g._.table && g._.table.col) {
          try {
            for (var j = 0; j < 50; j++) {
              try { colWidths.push(g._.table.col.getWidth(j)); } catch (ex) { break; }
            }
          } catch (ex2) { /* noop */ }
        }
        if (colWidths.length === 0) {
          var numCols = this._currentColumns ? this._currentColumns.length : 10;
          var availW = rect.width - 40;
          var avgW = availW / Math.max(numCols, 1);
          for (var k = 0; k < numCols; k++) colWidths.push(avgW);
        }
        var x = mouseX - 40;
        if (x < 0) x = 0;
        for (var c = 0; c < colWidths.length; c++) {
          if (x < colWidths[c]) {
            this._syncFormulaBar(0, c);
            this._updateStatusBar(0, c);
            return;
          }
          x -= colWidths[c];
        }
      } catch (e) { /* noop */ }
    },

    _syncFormulaBar(row, col) {
      if (!window.FormulaBar) return;
      window.FormulaBar.sync(row, col, this._activeSheetName());
    },

    _updateStatusBar(row, col, range) {
      if (!this._grid) return;
      var sheets = this._grid.getData() || [];
      var activeName = this._activeSheetName();
      var s = null;
      for (var si = 0; si < sheets.length; si++) {
        if (sheets[si] && sheets[si].name === activeName) { s = sheets[si]; break; }
      }
      if (!s) s = sheets[0];
      if (!s || !s.rows) return;

      var avgEl = document.getElementById("xl-stat-avg");
      var countEl = document.getElementById("xl-stat-count");
      var sumEl = document.getElementById("xl-stat-sum");
      var minEl = document.getElementById("xl-stat-min");
      var maxEl = document.getElementById("xl-stat-max");
      var ncountEl = document.getElementById("xl-stat-ncount");
      if (!avgEl || !countEl || !sumEl) return;

      var rKeys = Object.keys(s.rows).filter(function (k) { return /^\d+$/.test(k); }).map(Number).sort(function (a, b) { return a - b; });
      if (!rKeys.length) return;
      var lastRow = rKeys[rKeys.length - 1];

      var maxCol = 0;
      rKeys.forEach(function (rk) {
        var cells = (s.rows[rk] || {}).cells || {};
        Object.keys(cells).forEach(function (ck) { var n = parseInt(ck, 10); if (n > maxCol) maxCol = n; });
      });

      var r0 = 1, r1 = lastRow, c0 = 0, c1 = maxCol;
      var single = !range || range.sri === range.eri && range.sci === range.eci;
      if (range && range.sri != null && !single) {
        r0 = Math.max(1, range.sri);
        r1 = Math.min(lastRow, range.eri != null ? range.eri : range.sri);
        c0 = range.sci != null ? range.sci : 0;
        c1 = range.eci != null ? range.eci : (range.sci != null ? range.sci : maxCol);
      } else if (col >= 0) {
        c0 = col; c1 = col;
      }

      var nums = [];
      var nonEmpty = 0;
      for (var rk2 = r0; rk2 <= r1; rk2++) {
        var cells2 = (s.rows[rk2] || {}).cells || {};
        for (var ck2 = c0; ck2 <= c1; ck2++) {
          var cell = cells2[ck2] || {};
          var raw = cell.value != null ? cell.value : (cell.text != null ? cell.text : "");
          if (raw === "" || raw == null) continue;
          nonEmpty++;
          var n = Number(raw);
          if (!isNaN(n)) nums.push(n);
        }
      }

      if (nums.length === 0 && nonEmpty === 0) {
        avgEl.textContent = "";
        countEl.textContent = "";
        sumEl.textContent = "";
        if (minEl) minEl.textContent = "";
        if (maxEl) maxEl.textContent = "";
        if (ncountEl) ncountEl.textContent = "";
        var infoEl = document.getElementById("xl-status-info");
        if (infoEl && this._lastGridNote) infoEl.textContent = this._lastGridNote;
        return;
      }

      var sum = 0;
      var mn = Infinity, mx = -Infinity;
      for (var j = 0; j < nums.length; j++) {
        sum += nums[j];
        if (nums[j] < mn) mn = nums[j];
        if (nums[j] > mx) mx = nums[j];
      }
      var avg = nums.length ? sum / nums.length : 0;

      var fmtNum = function (v) {
        if (Number.isInteger(v)) return v.toLocaleString();
        return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      };

      avgEl.textContent = "Average: " + fmtNum(avg);
      countEl.textContent = "Count: " + nonEmpty.toLocaleString();
      sumEl.textContent = "Sum: " + fmtNum(sum);
      if (minEl) minEl.textContent = "Min: " + (nums.length ? fmtNum(mn) : "-");
      if (maxEl) maxEl.textContent = "Max: " + (nums.length ? fmtNum(mx) : "-");
      if (ncountEl) ncountEl.textContent = "Numeric: " + nums.length.toLocaleString();
    },

    _setGridNote(msg) {
      var info = document.getElementById("xl-status-info");
      if (info) info.textContent = msg;
      this._lastGridNote = msg;
    },

    // === Tool Methods ===

    _toolsColumnIndex() {
      var sel = document.getElementById("xl-filter-col");
      var cols = this._currentColumns || [];
      if (sel && cols.length) {
        var i = cols.indexOf(sel.value);
        if (i >= 0) return i;
      }
      try {
        var sel2 = this._getSelection();
        if (sel2 && sel2.col != null) return sel2.col;
      } catch (e) { /* noop */ }
      return -1;
    },

    _a1(row, col) {
      if (window.FormulaEngine && typeof window.FormulaEngine.toA1 === "function") {
        try { return window.FormulaEngine.toA1(row, col); } catch (e) { /* fall through */ }
      }
      var s = "", c = col;
      while (c >= 0) { s = String.fromCharCode(65 + (c % 26)) + s; c = Math.floor(c / 26) - 1; }
      return s + (row + 1);
    },

    undoGrid() {
      if (!this._grid) return App.error("Load a table first");
      try {
        var d = this._grid.sheet && this._grid.sheet.data;
        if (!d || typeof d.undo !== "function" || !d.history || !d.history.undoItems.length) { App.warning("Nothing to undo"); return; }
        d.undo();
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("undo");
        App.success("Undone");
      } catch (e) { App.error("Undo failed: " + (e.message || e)); }
    },

    redoGrid() {
      if (!this._grid) return App.error("Load a table first");
      try {
        var d = this._grid.sheet && this._grid.sheet.data;
        if (!d || typeof d.redo !== "function" || !d.history || !d.history.redoItems.length) { App.warning("Nothing to redo"); return; }
        d.redo();
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("redo");
        App.success("Redone");
      } catch (e) { App.error("Redo failed: " + (e.message || e)); }
    },

    toggleFreeze() {
      if (!this._grid) return App.error("Load a table first");
      try {
        var d = this._grid.sheet.data;
        if (!d || typeof d.setFreeze !== "function") { App.error("Freeze not supported by the grid engine"); return; }
        var f = d.freeze || [0, 0];
        var isOn = (Number(f[0]) || 0) > 0 || (Number(f[1]) || 0) > 0;
        d.setFreeze(isOn ? 0 : 1, 0);
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        var btn = document.getElementById("xl-btn-freeze");
        if (btn) btn.classList.toggle("active", !isOn);
        App.success(isOn ? "Header row unfrozen" : "Header row frozen");
      } catch (e) { App.error("Freeze failed: " + (e.message || e)); }
    },

    sortColumn(ascending) {
      if (!this._grid) return App.error("Load a table first");
      var col = this._toolsColumnIndex();
      if (col < 0) return App.error("Pick a column to sort (open the Filter bar to select a column)");
      try {
        var d = this._grid.sheet.data;
        var rows = d.rows;
        var map = rows._ || {};
        var keys = Object.keys(map).map(Number).filter(function (k) { return k > 0; }).sort(function (a, b) { return a - b; });
        if (keys.length < 2) return App.warning("Not enough data rows to sort");
        var valOf = function (rk) {
          var c = map[rk] && map[rk].cells ? map[rk].cells[col] : null;
          if (!c) return "";
          return c.value != null ? c.value : (c.text != null ? c.text : "");
        };
        keys.sort(function (a, b) {
          var av = valOf(a), bv = valOf(b);
          var an = Number(av), bn = Number(bv);
          if (av !== "" && bv !== "" && !isNaN(an) && !isNaN(bn)) return an - bn;
          return String(av).localeCompare(String(bv));
        });
        if (!ascending) keys.reverse();
        var newMap = {};
        if (map[0]) newMap[0] = map[0];
        keys.forEach(function (k, i) { newMap[i + 1] = map[k]; });
        var used = { 0: true };
        keys.forEach(function (k) { used[k] = true; });
        Object.keys(map).map(Number).filter(function (k) { return !used[k]; }).sort(function (a, b) { return a - b; })
          .forEach(function (k, i) { newMap[keys.length + 1 + i] = map[k]; });
        rows._ = newMap;
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("sort");
        App.success("Column sorted " + (ascending ? "A\u2192Z" : "Z\u2192A"));
      } catch (e) { App.error("Sort failed: " + (e.message || e)); }
    },

    _filterBackup: null,

    applyFilter() {
      if (!this._grid) return App.error("Load a table first");
      var col = this._toolsColumnIndex();
      if (col < 0) return App.error("Pick a column to filter (select from the Filter bar)");
      var valEl = document.getElementById("xl-filter-val");
      var query = ((valEl && valEl.value) || "").trim().toLowerCase();
      if (!query) return App.warning("Enter a value to filter on");
      try {
        var d = this._grid.sheet.data;
        var map = d.rows._ || {};
        if (!this._filterBackup) this._filterBackup = { sheet: d.name, map: JSON.parse(JSON.stringify(map)) };
        var newMap = {};
        Object.keys(map).map(Number).sort(function (a, b) { return a - b; }).forEach(function (k) {
          if (k === 0) { newMap[0] = map[0]; return; }
          var c = map[k] && map[k].cells ? map[k].cells[col] : null;
          var v = c ? String(c.value != null ? c.value : (c.text != null ? c.text : "")) : "";
          if (v.toLowerCase().indexOf(query) >= 0) newMap[k] = map[k];
        });
        d.rows._ = newMap;
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("filter");
        App.success("Filter applied -- " + (Object.keys(newMap).length - 1) + " row(s) match");
      } catch (e) { App.error("Filter failed: " + (e.message || e)); }
    },

    clearFilter() {
      if (!this._filterBackup) return App.warning("No active filter");
      try {
        var d = this._grid.sheet.data;
        if (d.name === this._filterBackup.sheet) {
          d.rows._ = this._filterBackup.map;
          if (typeof this._grid.reRender === "function") this._grid.reRender();
          if (typeof d.change === "function") d.change("filter-clear");
          App.success("Filter cleared -- all rows restored");
        } else {
          App.warning("Filter dropped (the active sheet changed)");
        }
        this._filterBackup = null;
      } catch (e) { App.error("Clear filter failed: " + (e.message || e)); }
    },

    autoSum() {
      if (!this._grid) return App.error("Load a table first");
      var sel = this._getSelection();
      if (!sel) return App.error("Select the cell where the total should go");
      var row = sel.row, col = sel.col;
      if (row <= 1) return App.warning("Select a cell below the numbers to total");
      try {
        var formula = "=SUM(" + this._a1(1, col) + ":" + this._a1(row - 1, col) + ")";
        this._grid.cellText(row, col, formula, this._activeSheetIndex());
        if (this._grid.sheet && typeof this._grid.sheet.trigger === "function") {
          this._grid.sheet.trigger("change", "finished", formula);
        }
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        App.success("Inserted " + formula);
      } catch (e) { App.error("AutoSum failed: " + (e.message || e)); }
    },

    _findMatches: [],
    _findPos: -1,
    _findQuery: "",

    findNext() {
      if (!this._grid) return App.error("Load a table first");
      var valEl = document.getElementById("xl-find-val");
      var query = ((valEl && valEl.value) || "").trim();
      if (!query) return App.warning("Enter text to find");
      try {
        var d = this._grid.sheet.data;
        var map = d.rows._ || {};
        var q = query.toLowerCase();
        var matches = [];
        Object.keys(map).forEach(function (rk) {
          var cells = map[rk] && map[rk].cells ? map[rk].cells : {};
          Object.keys(cells).forEach(function (ck) {
            var cell = cells[ck];
            var t = cell.text != null ? String(cell.text) : "";
            if (t.toLowerCase().indexOf(q) >= 0) matches.push({ row: Number(rk), col: Number(ck) });
          });
        });
        if (!matches.length) { App.warning("No matches for \"" + query + "\""); return; }
        if (this._findQuery !== query) {
          this._findMatches = matches;
          this._findPos = 0;
          this._findQuery = query;
        } else {
          this._findPos = (this._findPos + 1) % matches.length;
        }
        var m = matches[this._findPos];
        try { this._grid.sheet.selector.set(m.row, m.col); } catch (e) { /* noop */ }
        if (this._grid.sheet.table && typeof this._grid.sheet.table.scrollTo === "function") {
          try { this._grid.sheet.table.scrollTo({ row: m.row, col: m.col }); } catch (e) { /* noop */ }
        }
        App.success("Match " + (this._findPos + 1) + " of " + matches.length + " -- " + this._a1(m.row, m.col));
      } catch (e) { App.error("Find failed: " + (e.message || e)); }
    },

    replaceAll() {
      if (!this._grid) return App.error("Load a table first");
      var findEl = document.getElementById("xl-find-val");
      var repEl = document.getElementById("xl-replace-val");
      var find = ((findEl && findEl.value) || "");
      var rep = ((repEl && repEl.value) || "");
      if (!find) return App.warning("Enter text to find");
      try {
        var d = this._grid.sheet.data;
        var map = d.rows._ || {};
        var count = 0;
        Object.keys(map).forEach(function (rk) {
          var cells = map[rk] && map[rk].cells ? map[rk].cells : {};
          Object.keys(cells).forEach(function (ck) {
            var cell = cells[ck];
            if (!cell || cell.text == null) return;
            var t = String(cell.text);
            if (t.indexOf(find) >= 0) {
              var isFormula = cell.f != null && String(cell.f).charAt(0) === "=";
              if (!isFormula) {
                cell.text = t.split(find).join(rep);
                count++;
              }
            }
          });
        });
        if (!count) { App.warning("No matches found"); return; }
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("replace");
        App.success("Replaced " + count + " occurrence" + (count === 1 ? "" : "s"));
      } catch (e) { App.error("Replace failed: " + (e.message || e)); }
    },

    insertRow() {
      if (!this._grid) return App.error("Load a table first");
      try {
        var d = this._grid.sheet.data;
        var sel = this._getSelection();
        var insertAt = sel ? sel.row + 1 : 1;
        var map = d.rows._ || {};
        var keys = Object.keys(map).map(Number).filter(function (k) { return k > 0; }).sort(function (a, b) { return b - a; });
        for (var i = 0; i < keys.length; i++) {
          if (keys[i] >= insertAt) {
            map[keys[i] + 1] = map[keys[i]];
          }
        }
        map[insertAt] = { cells: {} };
        if (d.rows.len != null) d.rows.len = Number(d.rows.len) + 1;
        d.rows._ = map;
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("insert-row");
        App.success("Row inserted at position " + insertAt);
      } catch (e) { App.error("Insert row failed: " + (e.message || e)); }
    },

    deleteRow() {
      if (!this._grid) return App.error("Load a table first");
      var sel = this._getSelection();
      if (!sel || sel.row <= 0) return App.warning("Select a data row to delete");
      try {
        var d = this._grid.sheet.data;
        var map = d.rows._ || {};
        if (!map[sel.row]) return App.warning("No row at position " + sel.row);
        var keys = Object.keys(map).map(Number).filter(function (k) { return k > 0; }).sort(function (a, b) { return a - b; });
        if (keys.length <= 1) return App.warning("Cannot delete the last data row");
        delete map[sel.row];
        var newMap = {};
        newMap[0] = map[0] || { cells: {} };
        var idx = 1;
        keys.forEach(function (k) {
          if (k !== sel.row) {
            newMap[idx++] = map[k];
          }
        });
        if (d.rows.len != null) d.rows.len = Math.max(0, Number(d.rows.len) - 1);
        d.rows._ = newMap;
        if (typeof this._grid.reRender === "function") this._grid.reRender();
        if (typeof d.change === "function") d.change("delete-row");
        App.success("Row " + sel.row + " deleted");
      } catch (e) { App.error("Delete row failed: " + (e.message || e)); }
    },

    _tryNewSheet() {
      try {
        if (!this._grid) { App.error("Spreadsheet grid not initialized"); return; }
        var g = this._grid;
        var sheets = g.getData() || [];
        var newName = "Sheet" + (sheets.length + 1);
        if (typeof g.addSheet === "function") {
          g.addSheet(newName);
        } else {
          sheets.push({ name: newName, rows: { len: 0 } });
          g.loadData(sheets);
        }
        var allDatas = g.datas || [];
        var newIdx = allDatas.length - 1;
        if (allDatas[newIdx] && g.sheet && typeof g.sheet.resetData === "function") {
          g.sheet.resetData(allDatas[newIdx]);
          if (g.bottombar) {
            if (g.bottombar.activeEl && typeof g.bottombar.activeEl.toggle === "function") {
              try { g.bottombar.activeEl.toggle(); } catch (e) { /* noop */ }
            }
            g.bottombar.activeEl = g.bottombar.items ? g.bottombar.items[newIdx] : null;
            if (g.bottombar.activeEl && typeof g.bottombar.activeEl.toggle === "function") {
              try { g.bottombar.activeEl.toggle(); } catch (e) { /* noop */ }
            }
          }
          g.data = allDatas[newIdx];
        }
        this._renderSheetTabs(newIdx);
        App.success("Added \"" + newName + "\" -- now " + (g.getData() || []).length + " sheet(s)");
      } catch (e) {
        App.error("New Sheet failed: " + (e.message || e));
      }
    },

    _renderSheetTabs(activeIdx) {
      var list = document.getElementById("xl-sheet-tabs-list");
      if (!list) return;
      var sheets = [];
      try { sheets = this._grid ? (this._grid.getData() || []) : []; } catch (e) { /* noop */ }
      if (activeIdx == null) {
        try {
          if (this._grid && this._grid.datas && this._grid.sheet) {
            var curData = this._grid.sheet.data;
            for (var i = 0; i < this._grid.datas.length; i++) {
              if (this._grid.datas[i] === curData) { activeIdx = i; break; }
            }
          }
          if (activeIdx == null) activeIdx = 0;
        } catch (e) { activeIdx = 0; }
      }
      if (!sheets.length) {
        sheets = [{ name: "Sheet1", rows: { len: 0 } }];
        activeIdx = 0;
      }
      var self = this;
      var html = sheets.map(function (s, i) {
        var name = s.name || ("Sheet" + (i + 1));
        var cls = i === activeIdx ? "xl-sheet-tab active" : "xl-sheet-tab";
        return '<div class="' + cls + '" data-idx="' + i + '" title="' + self._escape(name) + '">' +
          '<span class="xl-sheet-tab-name">' + self._escape(name) + '</span>' +
          (sheets.length > 1 ? '<button class="xl-sheet-tab-close" data-idx="' + i + '" title="Delete sheet">&times;</button>' : '') +
        '</div>';
      }).join("");
      list.innerHTML = html;
      var tabs = list.querySelectorAll(".xl-sheet-tab");
      for (var t = 0; t < tabs.length; t++) {
        (function (tab) {
          tab.addEventListener("click", function (e) {
            if (e.target.classList.contains("xl-sheet-tab-close")) return;
            var idx = parseInt(tab.getAttribute("data-idx"), 10);
            self._switchSheet(idx);
          });
          tab.addEventListener("dblclick", function (e) {
            var idx = parseInt(tab.getAttribute("data-idx"), 10);
            self._renameSheet(idx, tab);
          });
        })(tabs[t]);
      }
      var closeBtns = list.querySelectorAll(".xl-sheet-tab-close");
      for (var c = 0; c < closeBtns.length; c++) {
        (function (btn) {
          btn.addEventListener("click", function (e) {
            e.stopPropagation();
            var idx = parseInt(btn.getAttribute("data-idx"), 10);
            self._deleteSheet(idx);
          });
        })(closeBtns[c]);
      }
    },

    _switchSheet(idx) {
      if (!this._grid) return;
      try {
        var g = this._grid;
        var allDatas = g.datas || [];
        if (idx < 0 || idx >= allDatas.length) return;
        var targetData = allDatas[idx];
        if (!targetData) return;
        if (g.sheet && typeof g.sheet.resetData === "function") {
          g.sheet.resetData(targetData);
        }
        g.data = targetData;
        if (g.bottombar) {
          if (g.bottombar.activeEl && typeof g.bottombar.activeEl.toggle === "function") {
            try { g.bottombar.activeEl.toggle(); } catch (e) { /* noop */ }
          }
          g.bottombar.activeEl = g.bottombar.items ? g.bottombar.items[idx] : null;
          if (g.bottombar.activeEl && typeof g.bottombar.activeEl.toggle === "function") {
            try { g.bottombar.activeEl.toggle(); } catch (e) { /* noop */ }
          }
        }
        this._renderSheetTabs(idx);
        var self = this;
        setTimeout(function () { self._readSelectionAndUpdate(); }, 100);
      } catch (e) {
        console.warn("[ExcelPage] Switch sheet failed:", e);
        this._renderSheetTabs(idx);
      }
    },

    _deleteSheet(idx) {
      if (!this._grid) return;
      try {
        var g = this._grid;
        var allDatas = g.datas || [];
        if (allDatas.length <= 1) { App.warning("Cannot delete the only sheet"); return; }
        var sheets = g.getData() || [];
        var name = (sheets[idx] || {}).name || ("Sheet" + (idx + 1));
        if (g.bottombar && g.bottombar.items && g.bottombar.items[idx]) {
          g.bottombar.deleteEl = g.bottombar.items[idx];
        }
        if (typeof g.deleteSheet === "function") {
          g.deleteSheet();
        } else {
          var curSheetData = g.sheet ? g.sheet.data : null;
          allDatas.splice(idx, 1);
          if (g.bottombar && g.bottombar.items) {
            g.bottombar.items.splice(idx, 1);
            if (g.bottombar.dataNames) g.bottombar.dataNames.splice(idx, 1);
          }
          if (curSheetData && allDatas.indexOf(curSheetData) === -1) {
            var switchIdx = Math.min(idx, allDatas.length - 1);
            if (allDatas[switchIdx] && g.sheet && typeof g.sheet.resetData === "function") {
              g.sheet.resetData(allDatas[switchIdx]);
              g.data = allDatas[switchIdx];
            }
          }
        }
        var remaining = g.getData() || [];
        var switchTo = Math.min(idx, remaining.length - 1);
        if (switchTo >= 0 && g.datas && g.datas[switchTo] && g.sheet && typeof g.sheet.resetData === "function") {
          g.sheet.resetData(g.datas[switchTo]);
          g.data = g.datas[switchTo];
          if (g.bottombar) {
            if (g.bottombar.activeEl && typeof g.bottombar.activeEl.toggle === "function") {
              try { g.bottombar.activeEl.toggle(); } catch (e) { /* noop */ }
            }
            g.bottombar.activeEl = g.bottombar.items ? g.bottombar.items[switchTo] : null;
            if (g.bottombar.activeEl && typeof g.bottombar.activeEl.toggle === "function") {
              try { g.bottombar.activeEl.toggle(); } catch (e) { /* noop */ }
            }
          }
        }
        this._renderSheetTabs(Math.min(idx, (g.getData() || []).length - 1));
        App.success("Deleted \"" + name + "\"");
      } catch (e) {
        console.warn("[ExcelPage] Delete sheet failed:", e);
        this._renderSheetTabs(idx);
      }
    },

    _renameSheet(idx, tabEl) {
      if (!this._grid) return;
      try {
        var g = this._grid;
        var sheets = g.getData() || [];
        var oldName = (sheets[idx] || {}).name || ("Sheet" + (idx + 1));
        var newName = prompt("Rename sheet:", oldName);
        if (!newName || newName === oldName) return;
        newName = newName.trim();
        if (!newName) return;
        if (g.datas && g.datas[idx]) {
          g.datas[idx].name = newName;
        }
        if (g.bottombar && g.bottombar.dataNames) {
          g.bottombar.dataNames[idx] = newName;
        }
        this._renderSheetTabs(idx);
        App.success("Renamed to \"" + newName + "\"");
      } catch (e) {
        console.warn("[ExcelPage] Rename sheet failed:", e);
        this._renderSheetTabs(idx);
      }
    },

    // === Data Loading ===

    async _loadTable(table) {
      if (!table) return;
      var gen = (this._loadGen = (this._loadGen || 0) + 1);
      this._currentTable = table;
      this._pivotColumns = null;
      this._pivotMatrix = null;
      var ael = document.getElementById("xl-analysis-sheet");
      if (ael) ael.value = table + "_analysis";
      try {
        var res = await API.tableSample(table, MAX_ROWS);
        if (gen !== this._loadGen) return;
        var tsel = document.getElementById("xl-table");
        if (tsel && tsel.value !== table) tsel.value = table;
        var columns = res.columns || [];
        var rows = res.rows || [];
        this._currentColumns = columns;
        var sheets = this._rowsToSheets(columns, rows, table);
        this._tableSheets = sheets;
        if (this._grid) this._grid.loadData(sheets);
        this._loadTableFormulas(columns, rows, table);
        var count = rows.length;
        var msg = count >= MAX_ROWS
          ? "Showing first " + MAX_ROWS.toLocaleString() + " rows of \"" + table + "\" (full export available below)."
          : "\"" + table + "\" -- " + count.toLocaleString() + " rows \u00D7 " + columns.length + " columns loaded. Edit cells, type formulas (=SUM(A1:A5)), add sheets.";
        this._setGridNote(msg);
        var cols = columns;
        if (!cols.length && this._grid) {
          cols = this._extractColumnsFromGrid();
        }
        if (cols.length) this._currentColumns = cols;
        this._fillColumnDropdowns(cols);
        this._populateMerge();
        this._populateForecastCols();
        this._refreshExportSheets();
        this._renderSheetTabs(0);
        App.success("Loaded \"" + table + "\" into the grid");
        var self = this;
        setTimeout(function () {
          self._updateStatusBar(0, 0);
          self._readSelectionAndUpdate();
        }, 200);
        this._startSelectionPoll();
      } catch (e) {
        if (gen !== this._loadGen) return;
        this._setGridNote("Failed to load \"" + table + "\": " + (e.message || e));
        var fallbackCols = this._extractColumnsFromGrid();
        if (fallbackCols.length) {
          this._currentColumns = fallbackCols;
          this._fillColumnDropdowns(fallbackCols);
        }
      }
    },

    _rowsToSheets(columns, rows, name) {
      var extraRows = 5;
      var totalRows = rows.length + 1 + extraRows;
      var sheetRows = { len: totalRows };
      columns.forEach(function (c, ci) {
        sheetRows[0] = sheetRows[0] || { cells: {} };
        sheetRows[0].cells[ci] = { text: String(c) };
      });
      rows.forEach(function (row, ri) {
        var cells = {};
        columns.forEach(function (c, ci) {
          var v = Array.isArray(row) ? row[ci] : row[c];
          cells[ci] = { text: v == null ? "" : String(v) };
        });
        sheetRows[ri + 1] = { cells: cells };
      });
      return [{ name: name || "Sheet1", rows: sheetRows }];
    },

    _extractColumnsFromGrid() {
      if (!this._grid) return [];
      try {
        var sheets = this._grid.getData() || [];
        var s = sheets[0];
        if (!s || !s.rows || !s.rows[0] || !s.rows[0].cells) return [];
        var cells = s.rows[0].cells;
        return Object.keys(cells)
          .map(Number)
          .sort(function (a, b) { return a - b; })
          .map(function (ci) { return String(cells[ci].text || "Col" + (ci + 1)); });
      } catch (e) { return []; }
    },

    _gridToMatrix() {
      if (!this._grid) return { columns: [], matrix: [] };
      var sheets = this._grid.getData() || [];
      var s = sheets[0];
      if (!s || !s.rows) return { columns: [], matrix: [] };
      var rKeys = Object.keys(s.rows)
        .filter(function (k) { return /^\d+$/.test(k); })
        .map(Number).sort(function (a, b) { return a - b; });
      if (!rKeys.length) return { columns: [], matrix: [] };
      var headerCells = (s.rows[rKeys[0]] || {}).cells || {};
      var colIdx = Object.keys(headerCells).map(Number).sort(function (a, b) { return a - b; });
      var columns = colIdx.map(function (ci) { return String(headerCells[ci].text != null ? headerCells[ci].text : ""); });
      var matrix = [];
      for (var i = 1; i < rKeys.length; i++) {
        var cells = (s.rows[rKeys[i]] || {}).cells || {};
        matrix.push(colIdx.map(function (ci) {
          var cell = cells[ci] || {};
          return cell.text != null ? cell.text : (cell.value != null ? cell.value : "");
        }));
      }
      while (matrix.length && matrix[matrix.length - 1].every(function (v) { return v === "" || v == null; })) matrix.pop();
      return { columns: columns, matrix: matrix };
    },

    _analysisSheetName() {
      var el = document.getElementById("xl-analysis-sheet");
      var name = (el && el.value.trim()) || (this._currentTable ? this._currentTable + "_analysis" : "analysis");
      if (el) el.value = name;
      return name;
    },

    _rebuildGrid(allSheets) {
      try { this._grid.destroy(); } catch (e) { /* noop */ }
      this._grid = null;
      var el = document.getElementById("xl-grid");
      if (!el) return false;
      el.innerHTML = "";
      var XS = window.x_spreadsheet || window.xSpreadsheet;
      if (!XS) return false;
      var self = this;
      try {
        this._grid = new XS("#xl-grid");
        this._grid.loadData(allSheets);
        if (typeof this._grid.change === "function") {
          this._grid.change(function () {
            self._dirty = true;
            self._onGridChanged();
          });
        }
      this._bindSelectionListeners(document.getElementById("xl-grid"));
        if (typeof this._grid.on === "function") {
          try { this._grid.on("cellSelected", function (d) {
            if (d && d.row != null && d.col != null) {
              self._syncFormulaBar(d.row, d.col);
              self._updateStatusBar(d.row, d.col);
              self._updateFormatToolbar();
            }
          }); } catch (e) { /* noop */ }
          try { this._grid.on("selected", function (d) {
            if (d && d.row != null && d.col != null) {
              self._syncFormulaBar(d.row, d.col);
              self._updateStatusBar(d.row, d.col);
              self._updateFormatToolbar();
            }
          }); } catch (e) { /* noop */ }
          try { this._grid.on("cellEdit", function (d) {
            if (d && d.row != null && d.col != null) {
              self._syncFormulaBar(d.row, d.col);
              self._updateStatusBar(d.row, d.col);
              self._updateFormatToolbar();
            }
          }); } catch (e) { /* noop */ }
        }
      } catch (e) {
        App.error("Grid reload failed: " + (e.message || e));
        return false;
      }
this._hideXSBuiltIn();
      this._initFormulaBar();
      this._refreshExportSheets();
      this._renderSheetTabs(0);
      var self = this;
      setTimeout(function () { try { self._applyConditionalFormatting(); } catch (e) { /* noop */ } }, 150);
      return true;
    },

    _appendAnalysisResult(label, cols, rows, quiet) {
      if (!this._grid) return { name: "", created: false, rows: (rows || []).length, cols: (cols || []).length };
      var name = this._analysisSheetName();
      var allSheets = (typeof this._grid.getData === "function") ? (this._grid.getData() || []) : [];
      var sheet = null;
      for (var i = 0; i < allSheets.length; i++) {
        if (allSheets[i] && allSheets[i].name === name) { sheet = allSheets[i]; break; }
      }
      var created = !sheet;
      if (!sheet) {
        sheet = { name: name, rows: { len: 0 } };
        allSheets.push(sheet);
      }
      var r = sheet.rows;
      var rKeys = Object.keys(r).filter(function (k) { return /^\d+$/.test(k); }).map(Number).sort(function (a, b) { return a - b; });
      var last = -1;
      rKeys.forEach(function (k) {
        var cells = (r[k] || {}).cells || {};
        var has = Object.keys(cells).some(function (ci) {
          var cell = cells[ci] || {};
          var v = cell.text != null ? cell.text : (cell.value != null ? cell.value : "");
          return v !== "" && v != null;
        });
        if (has) last = Math.max(last, k);
      });
      var next = last + 1;
      if (r[next] && !Object.keys(r[next].cells || {}).length) next += 1;
      var labelCells = {};
      labelCells[0] = { text: "\u2500\u2500\u2500 " + label + " \u2500\u2500\u2500" };
      r[next] = { cells: labelCells }; next++;
      var hdrCells = {};
      (cols || []).forEach(function (c, ci) { hdrCells[ci] = { text: String(c) }; });
      r[next] = { cells: hdrCells }; next++;
      (rows || []).forEach(function (row) {
        var cells = {};
        (cols || []).forEach(function (c, ci) {
          var v = Array.isArray(row) ? row[ci] : row[c];
          cells[ci] = { text: v == null ? "" : String(v) };
        });
        r[next] = { cells: cells }; next++;
      });
      r[next] = { cells: {} }; next++;
      if ((r.len || 0) < next + 20) r.len = next + 20;
      if (this._rebuildGrid(allSheets) === false) {
        return { name: name, created: created, rows: (rows || []).length, cols: (cols || []).length };
      }
      if (!quiet) {
        App.success(created
          ? "Created analysis sheet \"" + name + "\" \u2014 added \"" + label + "\" (" + (rows || []).length.toLocaleString() + " rows)."
          : "Added \"" + label + "\" to \"" + name + "\" \u2014 " + (rows || []).length.toLocaleString() + " rows.");
      }
      return { name: name, created: created, rows: (rows || []).length, cols: (cols || []).length };
    },

    // === Dropdown Population ===

    _fillColumnDropdowns(columns) {
      if (!columns || !columns.length) return;
      var self = this;
      var opts = function (cols) { return cols.map(function (c) { return '<option value="' + self._escape(c) + '">' + self._escape(c) + '</option>'; }).join(""); };
      var row = document.getElementById("xl-pivot-row");
      var col = document.getElementById("xl-pivot-col");
      var val = document.getElementById("xl-pivot-val");
      if (row) { row.innerHTML = opts(columns); row.selectedIndex = 0; }
      if (col) { col.innerHTML = '<option value="">\u2014 (none) \u2014</option>' + opts(columns); col.selectedIndex = 0; }
      if (val) { val.innerHTML = opts(columns); val.selectedIndex = 0; }
      var chartVal = document.getElementById("xl-chart-val");
      if (chartVal) { chartVal.innerHTML = opts(columns); chartVal.selectedIndex = 0; }
      var filterCol = document.getElementById("xl-filter-col");
      if (filterCol) { filterCol.innerHTML = opts(columns); filterCol.selectedIndex = 0; }
    },

    _bindMerge() {
      var self = this;
      var left = document.getElementById("xl-merge-left");
      var right = document.getElementById("xl-merge-right");
      if (left) left.addEventListener("change", function () { self._populateMergeKeys("left"); });
      if (right) right.addEventListener("change", function () { self._populateMergeKeys("right"); });
    },

    _populateMerge() {
      var self = this;
      var tables = App.state.tables || {};
      var names = Object.keys(tables);
      var left = document.getElementById("xl-merge-left");
      var right = document.getElementById("xl-merge-right");
      if (left) left.innerHTML = names.map(function (t) { return '<option value="' + self._escape(t) + '">' + self._escape(t) + '</option>'; }).join("");
      if (right) right.innerHTML = names.map(function (t) { return '<option value="' + self._escape(t) + '">' + self._escape(t) + '</option>'; }).join("");
      if (left && this._currentTable) left.value = this._currentTable;
      if (right && this._currentTable) {
        var alt = names.find(function (t) { return t !== self._currentTable; }) || this._currentTable;
        right.value = alt;
      }
      this._populateMergeKeys("left");
      this._populateMergeKeys("right");
    },

    async _populateMergeKeys(which) {
      var self = this;
      var sel = document.getElementById(which === "left" ? "xl-merge-left" : "xl-merge-right");
      var keySel = document.getElementById(which === "left" ? "xl-merge-leftkey" : "xl-merge-rightkey");
      if (!sel || !keySel) return;
      var table = sel.value;
      keySel.innerHTML = table ? '<option value="">loading\u2026</option>' : "";
      if (!table) return;
      var cols = await this._columnsFor(table);
      keySel.innerHTML = cols.map(function (c) { return '<option value="' + self._escape(c) + '">' + self._escape(c) + '</option>'; }).join("");
    },

    async _columnsFor(table) {
      try {
        var res = await API.tableSample(table, 1);
        return (res && res.columns) || [];
      } catch (e) { return []; }
    },

    async _populateForecastCols() {
      var cols = this._currentColumns;
      if (!cols || !cols.length) return;
      var self = this;
      var datePatterns = /date|time|timestamp|month|year|day/i;
      var dateCandidates = cols.filter(function (c) { return datePatterns.test(c); });
      var allCols = cols;
      var dateSel = document.getElementById("xl-fc-date");
      var valSel = document.getElementById("xl-fc-val");
      if (dateSel) {
        dateSel.innerHTML = (dateCandidates.length ? dateCandidates : allCols).map(function (c) { return '<option value="' + self._escape(c) + '">' + self._escape(c) + '</option>'; }).join("");
      }
      if (valSel) valSel.innerHTML = allCols.map(function (c) { return '<option value="' + self._escape(c) + '">' + self._escape(c) + '</option>'; }).join("");
    },

    // === Tool Cards (HTML) ===

    _cleanCard() {
      return '<div class="card"><div class="card-header"><h3>Clean &amp; Validate</h3>' +
        '<button class="btn btn-sm btn-ghost xl-run-ai" data-prompt="Clean this table and report the data-quality issues you find."><i class="fas fa-wand-magic-sparkles mr-1"></i>Run with AI</button></div>' +
        '<div class="space-y-3"><div class="flex flex-wrap items-end gap-3">' +
        '<label class="flex items-center gap-2 text-sm"><input type="checkbox" id="xl-clean-dedup" checked /> Remove duplicates</label>' +
        '<label class="text-sm flex items-center gap-2">Nulls: <select id="xl-clean-nulls" class="form-select" style="width:150px;">' +
        '<option value="fill_mean">Fill with mean</option><option value="fill_median">Fill with median</option>' +
        '<option value="fill_mode">Fill with mode</option><option value="drop">Drop rows</option></select></label></div>' +
        '<div class="flex flex-wrap gap-2">' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.runCleanPreview()"><i class="fas fa-eye mr-1"></i>Preview</button>' +
        '<button class="btn btn-sm btn-primary" onclick="ExcelPage.applyClean()"><i class="fas fa-broom mr-1"></i>Apply Clean</button>' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.runValidation()"><i class="fas fa-shield-halved mr-1"></i>Quality + PII</button></div>' +
        '<div class="xl-tool-result hidden" id="xl-clean-result"></div></div></div>';
    },

    _pivotCard() {
      return '<div class="card"><div class="card-header"><h3>Pivot &amp; Summarize</h3>' +
        '<button class="btn btn-sm btn-ghost xl-run-ai" data-prompt="Build a pivot-style summary of this table \u2014 group by the main category and show totals for the key numeric column."><i class="fas fa-wand-magic-sparkles mr-1"></i>Run with AI</button></div>' +
        '<div class="space-y-3"><div class="grid grid-cols-2 gap-2">' +
        '<div><label class="form-label">Row field</label><select id="xl-pivot-row" class="form-select"></select></div>' +
        '<div><label class="form-label">Column field</label><select id="xl-pivot-col" class="form-select"><option value="">\u2014 (none) \u2014</option></select></div>' +
        '<div><label class="form-label">Value field</label><select id="xl-pivot-val" class="form-select"></select></div>' +
        '<div><label class="form-label">Aggregate</label><select id="xl-pivot-agg" class="form-select">' +
        '<option value="SUM">SUM</option><option value="AVG">AVG</option><option value="COUNT">COUNT</option>' +
        '<option value="MIN">MIN</option><option value="MAX">MAX</option></select></div></div>' +
        '<div class="flex flex-wrap gap-2">' +
        '<button class="btn btn-sm btn-primary" onclick="ExcelPage.buildPivot()"><i class="fas fa-table-pivot mr-1"></i>Build Pivot</button>' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.buildPivot(true)"><i class="fas fa-chart-bar mr-1"></i>Pivot + Chart</button></div>' +
        '<div class="xl-tool-result hidden" id="xl-pivot-result"></div></div></div>';
    },

    _mergeCard() {
      return '<div class="card"><div class="card-header"><h3>VLOOKUP / Merge</h3>' +
        '<button class="btn btn-sm btn-ghost xl-run-ai" data-prompt="Explain how to join this table with another table on a key column (like a VLOOKUP), and what the best join type would be."><i class="fas fa-wand-magic-sparkles mr-1"></i>Run with AI</button></div>' +
        '<div class="space-y-3"><div class="grid grid-cols-2 gap-2">' +
        '<div><label class="form-label">Left table</label><select id="xl-merge-left" class="form-select"></select></div>' +
        '<div><label class="form-label">Right table</label><select id="xl-merge-right" class="form-select"></select></div>' +
        '<div><label class="form-label">Left key</label><select id="xl-merge-leftkey" class="form-select"></select></div>' +
        '<div><label class="form-label">Right key</label><select id="xl-merge-rightkey" class="form-select"></select></div></div>' +
        '<label class="text-sm flex items-center gap-2">Join type: <select id="xl-merge-join" class="form-select" style="width:150px;">' +
        '<option value="inner">INNER</option><option value="left">LEFT</option>' +
        '<option value="right">RIGHT</option><option value="outer">OUTER</option></select></label>' +
        '<div class="flex flex-wrap gap-2">' +
        '<button class="btn btn-sm btn-primary" onclick="ExcelPage.buildMerge()"><i class="fas fa-code-branch mr-1"></i>Run Merge</button></div>' +
        '<div class="xl-tool-result hidden" id="xl-merge-result"></div></div></div>';
    },

    _chartCard() {
      return '<div class="card"><div class="card-header"><h3>Chart Builder</h3>' +
        '<button class="btn btn-sm btn-ghost xl-run-ai" data-prompt="Create a chart for the most interesting metric in this table and explain what it shows."><i class="fas fa-wand-magic-sparkles mr-1"></i>Run with AI</button></div>' +
        '<div class="space-y-3"><div class="grid grid-cols-2 gap-2">' +
        '<div><label class="form-label">Data source</label><select id="xl-chart-source" class="form-select">' +
        '<option value="grid">Current sheet</option><option value="pivot">Pivot result</option></select></div>' +
        '<div><label class="form-label">Chart type</label><select id="xl-chart-type" class="form-select">' +
        '<option value="bar">Bar</option><option value="line">Line</option>' +
        '<option value="pie">Pie</option><option value="heatmap">Heatmap</option></select></div>' +
        '<div class="col-span-2"><label class="form-label">Value column (numbers)</label><select id="xl-chart-val" class="form-select"></select></div></div>' +
        '<button class="btn btn-sm btn-primary" onclick="ExcelPage.renderChart()"><i class="fas fa-chart-line mr-1"></i>Render Chart</button>' +
        '<div class="xl-tool-result hidden" id="xl-chart-wrap"><canvas id="xl-chart-canvas" class="xl-chart-canvas"></canvas></div></div></div>';
    },

    _reportCard() {
      return '<div class="card"><div class="card-header"><h3>Report &amp; Narrative</h3>' +
        '<button class="btn btn-sm btn-ghost xl-run-ai" data-prompt="Write a short executive report for this table: key metrics, trends, insights, and recommendations."><i class="fas fa-wand-magic-sparkles mr-1"></i>Run with AI</button></div>' +
        '<div class="space-y-3"><div class="flex flex-wrap gap-2">' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.generateReport()"><i class="fas fa-file-lines mr-1"></i>Executive Report</button>' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.generateStory()"><i class="fas fa-book-open mr-1"></i>AI Story</button></div>' +
        '<div class="xl-tool-result hidden" id="xl-report-result"></div></div></div>';
    },

    _exportCard() {
      return '<div class="card"><div class="card-header"><h3>Export</h3></div>' +
        '<div class="space-y-3"><div><label class="form-label">Sheet to download</label>' +
        '<select id="xl-export-sheet" class="form-select" style="width:100%;"><option value="all">All sheets</option></select></div>' +
        '<div class="flex flex-wrap gap-2">' +
        '<button class="btn btn-sm btn-toolbar-green" onclick="ExcelPage.exportBackend(\'xlsx\')"><i class="fas fa-file-excel mr-1"></i>.xlsx (full table)</button>' +
        '<button class="btn btn-sm btn-toolbar" onclick="ExcelPage.exportBackend(\'csv\')"><i class="fas fa-file-csv mr-1"></i>CSV</button>' +
        '<button class="btn btn-sm btn-toolbar" onclick="ExcelPage.exportBackend(\'zip\')"><i class="fas fa-file-archive mr-1"></i>ZIP all</button>' +
        '<button class="btn btn-sm btn-primary" onclick="ExcelPage.exportGridXlsx()"><i class="fas fa-download mr-1"></i>Grid \u2192 .xlsx</button></div>' +
        '<p class="text-xs text-secondary">Pick a sheet above, then click <b>Grid \u2192 .xlsx</b> to download it. <b>.xlsx / CSV / ZIP</b> use the full table from the backend.</p></div></div>';
    },

    _statsCard() {
      return '<div class="card"><div class="card-header"><h3>Statistics &amp; Correlations</h3>' +
        '<button class="btn btn-sm btn-ghost xl-run-ai" data-prompt="Give me summary statistics and correlation analysis for this table."><i class="fas fa-wand-magic-sparkles mr-1"></i>Run with AI</button></div>' +
        '<div class="flex flex-wrap gap-2">' +
        '<button class="btn btn-sm btn-primary" onclick="ExcelPage.runStats()"><i class="fas fa-chart-column mr-1"></i>Run Analysis</button>' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.runCorrelation()"><i class="fas fa-link mr-1"></i>Correlation Matrix</button></div>' +
        '<div class="xl-tool-result hidden" id="xl-stats-result"></div></div>';
    },

    _insightsCard() {
      return '<div class="card"><div class="card-header"><h3>Insights</h3></div>' +
        '<div class="space-y-2"><div class="flex flex-wrap gap-2">' +
        '<button class="btn btn-sm btn-primary" onclick="ExcelPage.runAutoInsights()"><i class="fas fa-lightbulb mr-1"></i>Auto Insights</button>' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.runLLMInsights()"><i class="fas fa-wand-magic-sparkles mr-1"></i>LLM Insights</button>' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.runAnomalies()"><i class="fas fa-bug mr-1"></i>Detect Anomalies</button></div>' +
        '<div class="xl-tool-result hidden" id="xl-insights-result"></div></div></div>';
    },

    _forecastCard() {
      return '<div class="card"><div class="card-header"><h3>Forecast &amp; Trend</h3></div>' +
        '<div class="space-y-3"><div class="grid grid-cols-2 gap-2">' +
        '<div><label class="form-label">Date column</label><select id="xl-fc-date" class="form-select"></select></div>' +
        '<div><label class="form-label">Value column</label><select id="xl-fc-val" class="form-select"></select></div></div>' +
        '<div class="flex flex-wrap gap-2">' +
        '<button class="btn btn-sm btn-primary" onclick="ExcelPage.runForecast()"><i class="fas fa-chart-line mr-1"></i>Forecast</button>' +
        '<button class="btn btn-sm btn-secondary" onclick="ExcelPage.runDecomposition()"><i class="fas fa-layer-group mr-1"></i>Decompose</button></div>' +
        '<div class="xl-tool-result hidden" id="xl-fc-result"></div></div></div>';
    },

    // === Clean & Validate ===

    _cleanData(columns, matrix, opts) {
      opts = opts || {};
      var dedup = !!opts.dedup;
      var nullStrategy = opts.null_strategy || "fill_mean";
      var rows = matrix.map(function (r) { return r.slice(); });
      var filledCount = 0;
      var droppedRows = 0;

      if (nullStrategy === "drop") {
        var before = rows.length;
        rows = rows.filter(function (r) { return r.every(function (v) { return v !== "" && v != null; }); });
        droppedRows = before - rows.length;
      } else {
        for (var ci = 0; ci < columns.length; ci++) {
          var vals = matrix.map(function (r) { return r[ci]; }).filter(function (v) { return v !== "" && v != null && !isNaN(Number(v)); }).map(Number);
          if (!vals.length) continue;
          var fill;
          if (nullStrategy === "fill_median") {
            var s = vals.slice().sort(function (a, b) { return a - b; });
            fill = s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
          } else if (nullStrategy === "fill_mode") {
            var freq = {};
            vals.forEach(function (v) { freq[v] = (freq[v] || 0) + 1; });
            fill = Number(Object.keys(freq).sort(function (a, b) { return freq[b] - freq[a]; })[0]);
          } else {
            fill = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
          }
          rows.forEach(function (r) {
            if (r[ci] === "" || r[ci] == null) { r[ci] = String(fill); filledCount++; }
          });
        }
      }

      var seen = new Set();
      var dupRemoved = 0;
      rows = rows.filter(function (r) {
        var key = r.join("\x01");
        if (dedup && seen.has(key)) { dupRemoved++; return false; }
        seen.add(key);
        return true;
      });

      return { rows: rows, filledCount: filledCount, droppedRows: droppedRows, dupRemoved: dupRemoved, beforeRows: matrix.length, afterRows: rows.length };
    },

    runCleanPreview() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var dedup = document.getElementById("xl-clean-dedup").checked;
      var nullStrategy = document.getElementById("xl-clean-nulls").value;
      var box = document.getElementById("xl-clean-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      try {
        var gm = this._gridToMatrix();
        var columns = gm.columns, matrix = gm.matrix;
        if (!columns.length) { box.innerHTML = '<p class="text-danger text-sm">Grid is empty \u2014 load a table first</p>'; return; }
        var r = this._cleanData(columns, matrix, { dedup: dedup, null_strategy: nullStrategy });
        var changes = [];
        if (dedup && r.dupRemoved) changes.push(r.dupRemoved + " duplicate rows removed");
        if (nullStrategy === "drop") {
          if (r.droppedRows) changes.push(r.droppedRows + " rows with nulls dropped");
        } else if (r.filledCount) {
          changes.push(r.filledCount + " null cells filled (" + nullStrategy.replace("fill_", "") + ")");
        }
        var pct = r.beforeRows ? Math.round((1 - r.afterRows / r.beforeRows) * 100) : 0;
        box.innerHTML =
          "<h4>Before \u2192 After</h4>" +
          '<p class="text-sm">' + Number(r.beforeRows).toLocaleString() + " rows \u2192 <b>" + Number(r.afterRows).toLocaleString() + "</b> rows (" + pct + "% removed)</p>" +
          (changes.length ? "<ul>" + changes.map(function (c) { return "<li>" + this._escape(c) + "</li>"; }.bind(this)).join("") + "</ul>" : '<p class="text-sm text-secondary">No changes detected.</p>') +
          '<p class="text-xs text-secondary mt-2">Click <b>Apply Clean</b> to run this cleaning and add it to the analysis sheet (currently "' + this._analysisSheetName() + '") as a labeled section. Your source data stays untouched.</p>';
      } catch (e) { box.innerHTML = '<p class="text-danger text-sm">' + this._escape(e.message || e) + '</p>'; }
    },

    applyClean() {
      if (!this._grid) return App.error("Spreadsheet not available");
      var dedup = document.getElementById("xl-clean-dedup").checked;
      var nullStrategy = document.getElementById("xl-clean-nulls").value;
      var gm = this._gridToMatrix();
      var columns = gm.columns, matrix = gm.matrix;
      if (!columns.length) return App.error("Grid is empty \u2014 load a table first");
      var r = this._cleanData(columns, matrix, { dedup: dedup, null_strategy: nullStrategy });
      this._appendAnalysisResult("CLEAN RESULT", columns, r.rows, true);
      var changes = [
        dedup ? r.dupRemoved + " duplicate rows removed" : null,
        nullStrategy === "drop" ? r.droppedRows + " rows with nulls dropped" : r.filledCount + " null cells filled (" + nullStrategy.replace("fill_", "") + ")",
      ].filter(Boolean);
      App.success("Analysis sheet updated \u2014 Clean: " + (changes.join(", ") || "no changes") + '. See the "' + this._analysisSheetName() + '" tab.');
    },

    _scoreBand(score) {
      var s = Number(score);
      if (!isFinite(s)) return { color: "#6b7280", label: "n/a" };
      if (s >= 80) return { color: "#10b981", label: "Good" };
      if (s >= 60) return { color: "#f59e0b", label: "Needs attention" };
      return { color: "#ef4444", label: "Poor" };
    },

    _fmtNum(v, maxDec) {
      var n = Number(v);
      if (v == null || v === "" || isNaN(n)) return this._escape(v == null ? "\u2014" : v);
      return n.toLocaleString(undefined, { maximumFractionDigits: maxDec == null ? 2 : maxDec });
    },

    runValidation() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var box = document.getElementById("xl-clean-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      Promise.allSettled([API.qualityScore(table), API.piiScan(table)])
        .then(function (results) {
          var q = results[0], p = results[1];
          var qv = q.status === "fulfilled" ? q.value : null;
          var pv = p.status === "fulfilled" ? p.value : null;
          var html = "";
          if (qv && qv.quality) {
            var qobj = qv.quality;
            var score = qobj.overall_score != null ? qobj.overall_score
              : (qobj.score != null ? qobj.score : (qobj.overall != null ? qobj.overall : qobj.quality_score));
            html += "<h4>Data Quality Report</h4>";
            if (score != null) {
              var band = self._scoreBand(score);
              html += '<div class="flex items-center gap-3 my-2">' +
                '<span class="text-3xl font-bold" style="color:' + band.color + ';">' + Number(score).toFixed(0) + '</span>' +
                '<span class="text-sm"><span class="text-secondary">/ 100</span> \u2014 <b style="color:' + band.color + ';">' + band.label + '</b></span></div>';
            }
            var chips = [];
            if (qobj.row_count != null) chips.push(self._fmtNum(qobj.row_count, 0) + " rows");
            if (qobj.column_count != null) chips.push(self._fmtNum(qobj.column_count, 0) + " columns");
            if (qobj.duplicate_count != null) chips.push(self._fmtNum(qobj.duplicate_count, 0) + " duplicates");
            if (qobj.total_missing_cells != null) chips.push(self._fmtNum(qobj.total_missing_cells, 0) + " missing cells");
            if (chips.length) html += '<div class="flex flex-wrap gap-2 text-xs mb-2">' +
              chips.map(function (c) { return '<span class="px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-secondary">' + c + "</span>"; }).join("") + "</div>";
            var dims = qobj.dimension_scores;
            if (dims && typeof dims === "object") {
              html += '<div class="space-y-1 my-2">' + Object.keys(dims).map(function (d) {
                var val = Number(dims[d]);
                var band = self._scoreBand(val);
                var w = Math.max(0, Math.min(100, isFinite(val) ? val : 0));
                return '<div class="flex items-center gap-2 text-xs">' +
                  '<span style="width:90px;" class="text-right text-secondary">' + self._escape(d) + '</span>' +
                  '<div class="flex-1 h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden"><div style="width:' + w + '%;background:' + band.color + ';height:100%;"></div></div>' +
                  '<span class="font-mono" style="width:42px;">' + (isFinite(val) ? val.toFixed(0) : "\u2014") + "</span></div>";
              }).join("") + "</div>";
            }
            var issues = qobj.issues || [];
            if (issues.length) html += "<h4>Issues</h4><ul>" + issues.map(function (i) {
              return '<li class="text-sm">' + self._escape(typeof i === "string" ? i : JSON.stringify(i)) + "</li>";
            }).join("") + "</ul>";
            var recs = qobj.recommendations || [];
            if (recs.length) html += "<h4>Recommendations</h4><ol class=\"list-decimal ml-5 space-y-0.5\">" + recs.map(function (r) {
              return '<li class="text-sm">' + self._renderMarkdown(String(r)) + "</li>";
            }).join("") + "</ol>";
          }
          if (pv && pv.pii_scan) {
            var pii = pv.pii_scan;
            var risk = String(pii.risk_level || "unknown").toLowerCase();
            var riskColor = risk === "high" ? "#ef4444" : (risk === "medium" ? "#f59e0b" : "#10b981");
            html += "<h4>PII Scan</h4>" +
              '<p class="text-sm my-1">Risk level: <b style="color:' + riskColor + ';">' + self._escape(pii.risk_level || "unknown").toUpperCase() + "</b> \u2014 " +
              self._escape(pii.total_detected != null ? pii.total_detected : "?") + " of " + self._escape(pii.total_columns != null ? pii.total_columns : "?") + " columns flagged</p>";
            if (Array.isArray(pii.columns_detected) && pii.columns_detected.length)
              html += '<div class="flex flex-wrap gap-1 my-1">' + pii.columns_detected.map(function (c) {
                var name = typeof c === "object" ? (c.column || c.name || JSON.stringify(c)) : c;
                return '<span class="px-2 py-0.5 rounded-full text-xs" style="background:' + riskColor + '22;color:' + riskColor + ';">' + self._escape(name) + "</span>";
              }).join("") + "</div>";
            var precs = pii.recommendations || [];
            if (precs.length) html += '<ul class="list-disc ml-5">' + precs.map(function (r) { return '<li class="text-sm">' + self._renderMarkdown(String(r)) + "</li>"; }).join("") + "</ul>";
          }
          if (!html) html = '<p class="text-sm text-secondary">No quality / PII data returned.</p>';
          box.innerHTML = html;
        });
    },

    // === Pivot ===

    async buildPivot(withChart) {
      var table = this._currentTable;
      var rowF = document.getElementById("xl-pivot-row").value;
      var colF = document.getElementById("xl-pivot-col").value;
      var valF = document.getElementById("xl-pivot-val").value;
      var agg = document.getElementById("xl-pivot-agg").value;
      if (!table) return App.error("Load a table first");
      if (!rowF || !valF) return App.error("Pick a row field and a value field");

      var q = function (s) { return '"' + String(s).replace(/"/g, '""') + '"'; };
      var self = this;
      var box = document.getElementById("xl-pivot-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:50px;"></div>';

      try {
        var countSql = "SELECT COUNT(DISTINCT " + q(rowF) + ") AS _cnt FROM " + q(table) + " LIMIT 1";
        var countRes = await API.sql(countSql);
        var distinctCount = (countRes.data && countRes.data[0] && countRes.data[0]._cnt) || 0;
        if (distinctCount > 200) {
          box.innerHTML = '<p class="text-danger text-sm">The field <b>"' + self._escape(rowF) + '"</b> has <b>' + Number(distinctCount).toLocaleString() + '</b> distinct values \u2014 too many for a readable pivot table. Pick a field with fewer categories.</p>';
          return;
        }

        var sql;
        if (colF) {
          sql = "SELECT " + q(rowF) + " AS __row, " + q(colF) + " AS __col, " + agg + "(" + q(valF) + ") AS __val FROM " + q(table) + " GROUP BY " + q(rowF) + ", " + q(colF) + " ORDER BY " + q(rowF) + ", " + q(colF) + " LIMIT " + MAX_ROWS;
        } else {
          sql = "SELECT " + q(rowF) + " AS __row, " + agg + "(" + q(valF) + ") AS __val FROM " + q(table) + " GROUP BY " + q(rowF) + " ORDER BY " + q(rowF) + " LIMIT " + MAX_ROWS;
        }

        var res = await API.sql(sql, MAX_ROWS);
        if (!res.success && res.error && /VARCHAR/i.test(res.error) && agg !== "COUNT") {
          agg = "COUNT";
          document.getElementById("xl-pivot-agg").value = "COUNT";
          if (colF) {
            sql = "SELECT " + q(rowF) + " AS __row, " + q(colF) + " AS __col, " + agg + "(" + q(valF) + ") AS __val FROM " + q(table) + " GROUP BY " + q(rowF) + ", " + q(colF) + " ORDER BY " + q(rowF) + ", " + q(colF) + " LIMIT " + MAX_ROWS;
          } else {
            sql = "SELECT " + q(rowF) + " AS __row, " + agg + "(" + q(valF) + ") AS __val FROM " + q(table) + " GROUP BY " + q(rowF) + " ORDER BY " + q(rowF) + " LIMIT " + MAX_ROWS;
          }
          res = await API.sql(sql, MAX_ROWS);
        }
        if (!res.success) { box.innerHTML = '<p class="text-danger text-sm">' + self._escape(res.error || "Pivot failed") + "</p>"; return; }
        var data = res.data || [];
        var columns, matrix;
        if (colF) {
          var rowSet = [], rowSeen = new Set(), colSet = [], colSeen = new Set();
          data.forEach(function (d) {
            if (!rowSeen.has(d.__row)) { rowSeen.add(d.__row); rowSet.push(d.__row); }
            if (!colSeen.has(d.__col)) { colSeen.add(d.__col); colSet.push(d.__col); }
          });
          var grid = {};
          data.forEach(function (d) { grid[String(d.__row) + "|||" + String(d.__col)] = d.__val; });
          columns = ["Row Labels"].concat(colSet.map(String), ["Grand Total"]);
          matrix = rowSet.map(function (rv) {
            var rvs = colSet.map(function (cv) {
              var v = grid[String(rv) + "|||" + String(cv)];
              return v != null ? Number(v) || 0 : 0;
            });
            var rt = rvs.reduce(function (a, b) { return a + b; }, 0);
            return [rv].concat(rvs, [rt]);
          });
          var gt = colSet.map(function (_, ci) {
            return matrix.reduce(function (s, row) { return s + (Number(row[ci + 1]) || 0); }, 0);
          });
          var gta = gt.reduce(function (a, b) { return a + b; }, 0);
          matrix.push(["Grand Total"].concat(gt, [gta]));
        } else {
          columns = ["Row Labels", agg + "(" + valF + ")"];
          matrix = data.map(function (d) { return [d.__row, d.__val]; });
          var total = matrix.reduce(function (s, r) { return s + (Number(r[1]) || 0); }, 0);
          matrix.push(["Grand Total", total]);
        }
        self._pivotColumns = columns;
        self._pivotMatrix = matrix;
        var label = agg + " of " + valF + (colF ? " by " + rowF + " x " + colF : " by " + rowF);
        var st = self._appendAnalysisResult(label, columns, matrix);
        var pv = Math.min(matrix.length, 12);
        var h = "<h4>" + self._escape(label) + "</h4>";
        h += '<p class="text-xs text-secondary mb-2">' + (matrix.length - 1) + ' groups &mdash; "<b>' + self._escape(st.name) + '</b>"</p>';
        h += '<div class="overflow-x-auto"><table class="text-xs border-collapse w-full xl-pivot-table"><thead><tr>';
        columns.forEach(function (c) { h += '<th class="px-2 py-1 border text-left font-semibold">' + self._escape(c) + "</th>"; });
        h += "</tr></thead><tbody>";
        for (var ri = 0; ri < pv; ri++) {
          var _r = matrix[ri]; var _t = _r[0] === "Grand Total";
          h += "<tr" + (_t ? ' class="xl-pivot-total"' : "") + ">";
          _r.forEach(function (v, ci) {
            var cls = ci === 0 ? "font-semibold" : "text-right font-mono";
            if (_t) cls += " xl-pivot-total-cell";
            h += '<td class="px-2 py-1 border ' + cls + '">' + (typeof v === "number" ? v.toLocaleString() : self._escape(String(v != null ? v : ""))) + "</td>";
          });
          h += "</tr>";
        }
        if (matrix.length > pv) h += '<tr><td class="px-2 py-1 border text-secondary" colspan="' + columns.length + '">... ' + (matrix.length - pv) + " more</td></tr>";
        h += "</tbody></table></div>";
        box.innerHTML = h;
        if (withChart) {
          document.getElementById("xl-chart-source").value = "pivot";
          document.getElementById("xl-chart-val").value = columns[1] || "";
          self.renderChart();
        }
      } catch (e) {
        box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>";
      }
    },

    // === Merge ===

    async buildMerge() {
      var left = document.getElementById("xl-merge-left").value;
      var leftKey = document.getElementById("xl-merge-leftkey").value;
      var right = document.getElementById("xl-merge-right").value;
      var rightKey = document.getElementById("xl-merge-rightkey").value;
      var joinType = document.getElementById("xl-merge-join").value;
      if (!left || !right || !leftKey || !rightKey) return App.error("Select both tables and their key columns");
      var self = this;
      var box = document.getElementById("xl-merge-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:50px;"></div>';
      try {
        var res = await API.merge(left, right, leftKey, rightKey, joinType);
        if (!res.success) { box.innerHTML = '<p class="text-danger text-sm">' + self._escape(res.error || "Merge failed") + "</p>"; return; }
        var columns = res.columns || [];
        var data = res.data || [];
        var label = "MERGE RESULT (" + joinType.toUpperCase() + " " + left + " \u22C8 " + right + " on " + leftKey + ")";
        var st = self._appendAnalysisResult(label, columns, data.map(function (r) { return columns.map(function (c) { return r[c]; }); }));
        box.innerHTML =
          '<p class="text-sm">Merged <b>' + Number(res.total_rows).toLocaleString() + "</b> rows via " + self._escape(joinType.toUpperCase()) + " JOIN on " + self._escape(left) + "." + self._escape(leftKey) + " = " + self._escape(right) + "." + self._escape(rightKey) + "." +
          '<span class="text-secondary"> First ' + data.length + ' rows added to the "' + self._escape(st.name) + '" analysis sheet under ' + self._escape(label) + ".</span></p>";
      } catch (e) {
        box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>";
      }
    },

    // === Charts ===

    async renderChart() {
      var type = document.getElementById("xl-chart-type").value;
      var source = document.getElementById("xl-chart-source").value;
      var columns, matrix;
      if (source === "pivot" && this._pivotColumns && this._pivotMatrix) {
        columns = this._pivotColumns; matrix = this._pivotMatrix;
      } else {
        var g = this._gridToMatrix();
        columns = g.columns; matrix = g.matrix;
      }
      if (!columns.length || !matrix.length) return App.error("No data to chart \u2014 load a table or build a pivot first.");

      var self = this;
      var wrap = document.getElementById("xl-chart-wrap");
      var canvasId = "xl-chart-canvas";
      wrap.classList.remove("hidden");
      try { window.Charts.destroy(canvasId); } catch (e) { /* noop */ }
      var canvas = document.getElementById(canvasId);
      if (!canvas) return App.error("Chart canvas not found");

      var labels = matrix.map(function (r) { return String(r[0]); });
      var valCol = document.getElementById("xl-chart-val").value || columns[1] || "";
      var valIdx = columns.indexOf(valCol);
      var values = matrix.map(function (r) {
        var v = valIdx >= 0 ? r[valIdx] : r[1];
        var n = Number(v);
        return isNaN(n) ? 0 : n;
      });

      var title = (valCol || "value") + " by " + (columns[0] || "row");
      if (type === "heatmap") {
        var numCols = columns.slice(1);
        var capped = matrix.slice(0, 60);
        var matrix2d = capped.map(function (r) { return numCols.map(function (c) {
          var v = Number(r[columns.indexOf(c)]);
          return isNaN(v) ? 0 : v;
        }); });
        window.Charts.heatmap(canvasId, matrix2d, {
          title: title,
          xLabels: numCols,
          yLabels: capped.map(function (r) { return String(r[0]); }),
        });
      } else if (type === "line") {
        window.Charts.line(canvasId, labels, [{ label: valCol, data: values, borderColor: "#10b981", backgroundColor: "rgba(16,185,129,0.1)" }], { title: title });
      } else if (type === "pie") {
        window.Charts.pie(canvasId, labels, values, { title: title });
      } else {
        window.Charts.bar(canvasId, labels, values, { title: title });
      }
      if (!this._chartIds.includes(canvasId)) this._chartIds.push(canvasId);
      App.success("Chart rendered");
    },

    // === Report ===

    async generateReport() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var box = document.getElementById("xl-report-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:70px;"></div>';
      try {
        var res = await API.report(table);
        if (!res || res.success === false) throw new Error((res && res.error) || "Report failed");
        box.innerHTML = self._renderReport(res);
      } catch (e) {
        box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>";
      }
    },

    async generateStory() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var box = document.getElementById("xl-report-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:70px;"></div>';
      try {
        var res = await API.storytelling(table, "Summarize the key story this data tells.");
        if (!res || res.success === false) throw new Error((res && res.error) || "Story failed");
        var stories = (res.stories || res.data || []).map(function (s) { return "<li>" + self._escape(typeof s === "string" ? s : (s.narrative || s.title || JSON.stringify(s))) + "</li>"; }).join("");
        box.innerHTML = "<h4>AI Story</h4><ul>" + (stories || "<li>No story returned.</li>") + "</ul>";
      } catch (e) {
        box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>";
      }
    },

    // === Stats ===

    async runStats() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var box = document.getElementById("xl-stats-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      try {
        var res = await API.llmInsights(table, "Give me per-column statistics, distribution summaries, and key findings for every column in this table.");
        if (!res || res.success === false) throw new Error((res && res.error) || "Insights failed");
        var insights = res.insights;
        var html = "<h4>Column-Level Insights</h4>";
        if (typeof insights === "string") html += '<p class="text-sm">' + self._escape(insights) + "</p>";
        else html += self._renderInsights(insights);
        box.innerHTML = html;
      } catch (e) {
        try {
          var cols = self._currentColumns || [];
          var rowsOut = [];
          var CHUNK = 16;
          for (var ci = 0; ci < cols.length; ci += CHUNK) {
            var chunk = cols.slice(ci, ci + CHUNK);
            if (!chunk.length) break;
            var parts = chunk.map(function (c, j) {
              return 'COUNT("' + c + '") AS _c' + (ci + j) + '_count, MIN(TRY_CAST("' + c + '" AS DOUBLE)) AS _c' + (ci + j) + '_min, MAX(TRY_CAST("' + c + '" AS DOUBLE)) AS _c' + (ci + j) + '_max, AVG(TRY_CAST("' + c + '" AS DOUBLE)) AS _c' + (ci + j) + '_avg';
            });
            var sql = "SELECT COUNT(*) AS _n, " + parts.join(", ") + ' FROM "' + table + '" LIMIT 1';
            var sr = await API.sql(sql);
            if (!sr.success) continue;
            var d = sr.data && sr.data[0] ? sr.data[0] : {};
            chunk.forEach(function (c, j) {
              var k = ci + j;
              rowsOut.push([c, d["_c" + k + "_count"], d["_c" + k + "_min"], d["_c" + k + "_max"], d["_c" + k + "_avg"]]);
            });
          }
          if (rowsOut.length) {
            var fmt = function (v) { return (v == null || isNaN(v) ? "\u2014" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })); };
            var fmtCount = function (v) { return (v == null ? "\u2014" : Number(v).toLocaleString()); };
            box.innerHTML = "<h4>Column Statistics</h4>" +
              '<div class="overflow-x-auto"><table class="text-xs border-collapse w-full"><thead><tr><th class="px-2 py-1 border text-left">Column</th><th class="px-2 py-1 border text-right">Count</th><th class="px-2 py-1 border text-right">Min</th><th class="px-2 py-1 border text-right">Max</th><th class="px-2 py-1 border text-right">Avg</th></tr></thead><tbody>' +
              rowsOut.map(function (r) { return "<tr><td class=\"px-2 py-1 border\">" + self._escape(r[0]) + "</td><td class=\"px-2 py-1 border text-right\">" + fmtCount(r[1]) + "</td><td class=\"px-2 py-1 border text-right\">" + fmt(r[2]) + "</td><td class=\"px-2 py-1 border text-right\">" + fmt(r[3]) + "</td><td class=\"px-2 py-1 border text-right\">" + fmt(r[4]) + "</td></tr>"; }).join("") +
              "</tbody></table></div>";
          } else box.innerHTML = '<p class="text-sm text-secondary">No numeric statistics available.</p>';
        } catch (e2) { box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e2.message || e2) + "</p>"; }
      }
    },

    async runCorrelation() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var box = document.getElementById("xl-stats-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      try {
        var res = await API.correlation(table);
        if (!res || res.success === false) throw new Error((res && res.error) || "Correlation failed");
        var pairs = (res.correlation && res.correlation.pairs) || res.pairs || [];
        if (!pairs.length) { box.innerHTML = '<p class="text-sm text-secondary">No numeric pairs to correlate.</p>'; return; }
        var sorted = pairs.slice().sort(function (a, b) { return Math.abs(b.correlation) - Math.abs(a.correlation); });
        box.innerHTML =
          "<h4>Correlation Pairs</h4>" +
          '<div class="overflow-x-auto"><table class="text-xs border-collapse w-full"><thead><tr><th class="px-2 py-1 border text-left">Col A</th><th class="px-2 py-1 border text-left">Col B</th><th class="px-2 py-1 border text-right">r</th><th class="px-2 py-1 border text-left">Strength</th><th class="px-2 py-1 border text-left">Direction</th></tr></thead><tbody>' +
          sorted.map(function (p) { return "<tr><td class=\"px-2 py-1 border\">" + self._escape(p.col1) + "</td><td class=\"px-2 py-1 border\">" + self._escape(p.col2) + '</td><td class="px-2 py-1 border text-right font-mono">' + Number(p.correlation).toFixed(3) + "</td><td class=\"px-2 py-1 border\">" + self._escape(p.strength || "") + "</td><td class=\"px-2 py-1 border\">" + self._escape(p.direction || "") + "</td></tr>"; }).join("") +
          "</tbody></table></div>" +
          '<button class="btn btn-sm btn-secondary mt-2" onclick="ExcelPage.renderCorrelationMatrix()"><i class="fas fa-th mr-1"></i>Show Matrix</button>';
      } catch (e) { box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>"; }
    },

    async renderCorrelationMatrix() {
      var table = this._currentTable;
      if (!table) return;
      var self = this;
      try {
        var res = await API.sql('SELECT * FROM "' + table + '"');
        if (!res.success) return;
        var data = res.data || [];
        var numCols = (res.columns || []).filter(function (c) { return data.some(function (r) { return r[c] != null && !isNaN(Number(r[c])); }); }).slice(0, 10);
        if (numCols.length < 2) return App.error("Need \u22652 numeric columns for a correlation matrix.");
        var vals = data.map(function (r) { return numCols.map(function (c) { return Number(r[c]) || 0; }); });
        var n = vals.length;
        var means = numCols.map(function (_, ci) { return vals.reduce(function (s, r) { return s + r[ci]; }, 0) / n; });
        var stds = numCols.map(function (_, ci) {
          var m = means[ci];
          return Math.sqrt(vals.reduce(function (s, r) { return s + Math.pow(r[ci] - m, 2); }, 0) / n) || 1;
        });
        var matrix = numCols.map(function (_, ci) {
          return numCols.map(function (_, cj) {
            var m1 = means[ci], m2 = means[cj];
            return (vals.reduce(function (s, r) { return s + (r[ci] - m1) * (r[cj] - m2); }, 0) / n) / (stds[ci] * stds[cj]);
          });
        });
        var wrap = document.getElementById("xl-chart-wrap");
        var canvasId = "xl-chart-canvas";
        wrap.classList.remove("hidden");
        try { window.Charts.destroy(canvasId); } catch (e) { /* noop */ }
        window.Charts.heatmap(canvasId, matrix, { title: "Correlation Matrix", xLabels: numCols, yLabels: numCols });
        if (!self._chartIds.includes(canvasId)) self._chartIds.push(canvasId);
        App.success("Correlation matrix rendered");
      } catch (e) { App.error("Matrix failed: " + (e.message || e)); }
    },

    // === Insights ===

    async runAutoInsights() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var box = document.getElementById("xl-insights-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      try {
        var res = await API.insights(table);
        if (!res || res.success === false) throw new Error((res && res.error) || "Insights failed");
        var ins = res.insights;
        var html = "<h4>Auto Insights</h4>";
        html += self._renderInsights(ins);
        box.innerHTML = html;
      } catch (e) { box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>"; }
    },

    async runLLMInsights() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var box = document.getElementById("xl-insights-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      try {
        var res = await API.llmInsights(table, "Analyze this table and produce actionable data insights: trends, patterns, anomalies, and recommendations.");
        if (!res || res.success === false) throw new Error((res && res.error) || "LLM Insights failed");
        var ins = res.insights;
        var html = "<h4>LLM-Driven Insights</h4>";
        if (typeof ins === "string") html += self._renderMarkdown(ins);
        else html += self._renderInsights(ins);
        box.innerHTML = html;
      } catch (e) {
        box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>" +
          '<p class="text-xs text-secondary mt-1">Ensure Ollama is running for LLM-powered insights.</p>';
      }
    },

    async runAnomalies() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var box = document.getElementById("xl-insights-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      try {
        var res = await API.anomalies(table);
        if (!res || res.success === false) throw new Error((res && res.error) || "Anomaly detection failed");
        var anomalies = res.anomalies || [];
        if (!anomalies.length) { box.innerHTML = '<p class="text-sm text-secondary">No anomalies detected.</p>'; return; }
        var cols = Object.keys(anomalies[0] || {});
        box.innerHTML =
          "<h4>Anomalies Detected (" + anomalies.length + ")</h4>" +
          '<div class="overflow-x-auto"><table class="text-xs border-collapse w-full"><thead><tr>' +
          cols.map(function (c) { return '<th class="px-2 py-1 border text-left font-semibold">' + self._escape(c) + "</th>"; }).join("") +
          "</tr></thead><tbody>" +
          anomalies.slice(0, 50).map(function (a) { return "<tr>" + cols.map(function (c) { return "<td class=\"px-2 py-1 border\">" + self._escape(a[c]) + "</td>"; }).join("") + "</tr>"; }).join("") +
          "</tbody></table></div>";
      } catch (e) { box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>"; }
    },

    // === Forecast ===

    async runForecast() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var dateCol = document.getElementById("xl-fc-date").value;
      var valCol = document.getElementById("xl-fc-val").value;
      if (!dateCol || !valCol) return App.error("Select a date and value column");
      var box = document.getElementById("xl-fc-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      try {
        var res = await API.forecast(table, dateCol, valCol, 12);
        if (!res || res.success === false) throw new Error((res && res.error) || "Forecast failed");

        var histDates = res.historical_dates || [], histVals = res.historical_values || [];
        var fcDates = res.forecast_dates || [], fcVals = res.forecast_values || [];
        var upVals = res.upper_bound || [], loVals = res.lower_bound || [];
        if (!fcDates.length || !fcVals.length) throw new Error("No forecast data returned");

        var allLabels = histDates.concat(fcDates).map(String);
        var pad = function (arr) {
          return arr.length ? arr : Array(allLabels.length).fill(null);
        };
        var histSeries = pad(histVals).slice(0, allLabels.length);
        while (histSeries.length < allLabels.length) histSeries.push(null);
        var fcSeries = Array(histDates.length).fill(null).concat(fcVals.map(Number));
        var upSeries = Array(histDates.length).fill(null).concat(upVals.map(Number));
        var loSeries = Array(histDates.length).fill(null).concat(loVals.map(Number));

        var wrap = document.getElementById("xl-chart-wrap");
        wrap.classList.remove("hidden");
        try { window.Charts.destroy("xl-chart-canvas"); } catch (e) { /* noop */ }
        window.Charts.line("xl-chart-canvas", allLabels, [
          { label: valCol + " (actual)", data: histSeries, borderColor: "#3b82f6", backgroundColor: "rgba(59,130,246,0.08)" },
          { label: "Upper bound", data: upSeries, borderColor: "rgba(245,158,11,0.35)", borderDash: [2, 3] },
          { label: "Lower bound", data: loSeries, borderColor: "rgba(245,158,11,0.35)", borderDash: [2, 3] },
          { label: "Forecast", data: fcSeries, borderColor: "#f59e0b", borderDash: [5, 5] }
        ], { title: "Forecast: " + valCol });
        if (!self._chartIds.includes("xl-chart-canvas")) self._chartIds.push("xl-chart-canvas");

        var html = "<h4>Forecast (" + fcVals.length + " periods)</h4>";
        var dir = String(res.trend_direction || "").toLowerCase();
        var slope = res.trend_slope != null ? Number(res.trend_slope) : null;
        var lastActuals = histVals.slice(-Math.min(5, histVals.length)).map(Number);
        var avgActual = lastActuals.reduce(function (s, v) { return s + v; }, 0) / (lastActuals.length || 1);
        var avgFc = fcVals.map(Number).reduce(function (s, v) { return s + v; }, 0) / fcVals.length;
        var pct = avgActual ? ((avgFc - avgActual) / Math.abs(avgActual)) * 100 : null;
        var dirColor = dir === "increasing" ? "#10b981" : (dir === "decreasing" ? "#ef4444" : "#6b7280");
        var arrow = dir === "increasing" ? "\u2197" : (dir === "decreasing" ? "\u2198" : "\u2192");
        html += '<div class="text-sm space-y-0.5 my-2">' +
          '<p>Trend: <b style="color:' + dirColor + ';">' + arrow + " " + self._escape(dir || "flat") + "</b>" +
          (slope != null && isFinite(slope) ? ' <span class="text-secondary">(slope \u2248 ' + self._fmtNum(slope) + " per period)</span>" : "") + "</p>" +
          "<p>Avg forecast <b>" + self._fmtNum(avgFc) + "</b> vs recent actual avg <b>" + self._fmtNum(avgActual) + "</b>" +
          (pct != null ? ' (<span style="color:' + (pct >= 0 ? "#10b981" : "#ef4444") + ';">' + (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%</span>)" : "") + "</p>" +
          '<p class="text-xs text-secondary">Total next ' + fcVals.length + " periods: <b>" + self._fmtNum(fcVals.reduce(function (s, v) { return s + Number(v); }, 0)) + "</b>" +
          (loVals.length && upVals.length ? " \u00b7 95% band on final period: " + self._fmtNum(loVals[loVals.length - 1]) + " \u2013 " + self._fmtNum(upVals[upVals.length - 1]) : "") + "</p></div>";

        html += '<div class="overflow-x-auto"><table class="text-xs border-collapse w-full"><thead><tr>' +
          '<th class="px-2 py-1 border text-left">' + self._escape(dateCol) + '</th><th class="px-2 py-1 border text-right">' + self._escape(valCol) + '</th><th class="px-2 py-1 border text-right">Low</th><th class="px-2 py-1 border text-right">High</th>' +
          "</tr></thead><tbody>" +
          fcDates.map(function (d, i) {
            return "<tr><td class=\"px-2 py-1 border\">" + self._escape(d) + '</td><td class="px-2 py-1 border text-right font-mono font-semibold">' + self._fmtNum(fcVals[i]) + '</td><td class="px-2 py-1 border text-right font-mono text-secondary">' + self._fmtNum(loVals[i]) + '</td><td class="px-2 py-1 border text-right font-mono text-secondary">' + self._fmtNum(upVals[i]) + "</td></tr>";
          }).join("") + "</tbody></table></div>";
        box.innerHTML = html;
      } catch (e) { box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>"; }
    },

    async runDecomposition() {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var self = this;
      var dateCol = document.getElementById("xl-fc-date").value;
      var valCol = document.getElementById("xl-fc-val").value;
      if (!dateCol || !valCol) return App.error("Select a date and value column");
      var box = document.getElementById("xl-fc-result");
      box.classList.remove("hidden");
      box.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
      try {
        var res = await API.decomposition(table, dateCol, valCol, 12);
        if (!res || res.success === false) throw new Error((res && res.error) || "Decomposition failed");
        var dates = res.dates || [];
        var html = "<h4>Time Series Decomposition <span class='text-xs font-normal text-secondary'>(" + self._escape(res.model || "additive") + " model, period " + self._escape(res.period) + ")</span></h4>";
        [["Trend", "strength_trend"], ["Seasonality", "strength_seasonality"]].forEach(function (pair) {
          var name = pair[0], key = pair[1];
          var s = res[key] != null ? Number(res[key]) : null;
          if (s == null || !isFinite(s)) return;
          var band = self._scoreBand(s * 100);
          var w = Math.max(2, Math.min(100, s * 100));
          html += '<div class="flex items-center gap-2 text-xs my-1">' +
            '<span style="width:80px;" class="text-right text-secondary">' + name + '</span>' +
            '<div class="flex-1 h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden"><div style="width:' + w + '%;background:' + band.color + ';height:100%;"></div></div>' +
            '<span class="font-mono" style="width:52px;">' + (s * 100).toFixed(0) + "%</span></div>";
        });
        html += '<p class="text-xs text-secondary mt-1">Strength \u2248 100% = the component explains nearly all variation (excluding trend/seasonal noise). Values &lt; 30% suggest weak pattern.</p>';
        var wrap = document.getElementById("xl-chart-wrap");
        if (dates.length && Array.isArray(res.trend)) {
          var MAX_PTS = 400;
          var step = Math.max(1, Math.ceil(dates.length / MAX_PTS));
          var labels = [], trendV = [], seasV = [];
          for (var i = 0; i < dates.length; i += step) {
            labels.push(String(dates[i]));
            trendV.push(res.trend[i] == null ? null : Number(res.trend[i]));
            seasV.push(res.seasonal && res.seasonal[i] != null ? Number(res.seasonal[i]) : null);
          }
          wrap.classList.remove("hidden");
          try { window.Charts.destroy("xl-chart-canvas"); } catch (e) { /* noop */ }
          window.Charts.line("xl-chart-canvas", labels, [
            { label: valCol + " (trend)", data: trendV, borderColor: "#3b82f6", backgroundColor: "rgba(59,130,246,0.08)" },
            { label: "Seasonal", data: seasV, borderColor: "#10b981", backgroundColor: "rgba(16,185,129,0.08)" }
          ], { title: "Decomposition: " + valCol + " by " + dateCol });
          if (!self._chartIds.includes("xl-chart-canvas")) self._chartIds.push("xl-chart-canvas");
          html += '<p class="text-xs text-secondary mt-2">Chart shows trend vs seasonal component (downsampled to ' + labels.length + ' points).</p>';
        }
        box.innerHTML = html;
      } catch (e) { box.innerHTML = '<p class="text-danger text-sm">' + self._escape(e.message || e) + "</p>"; }
    },

    // === Export ===

    exportBackend(format) {
      var table = this._currentTable;
      if (!table) return App.error("Load a table first");
      var base = window.__API_BASE__ || window.location.origin;
      var url = format === "zip"
        ? base + "/export/zip"
        : base + "/export/" + format + "/" + encodeURIComponent(table);
      window.open(url, "_blank");
      App.success("Export started: " + format.toUpperCase());
    },

    _refreshExportSheets() {
      if (!this._grid) return;
      var sel = document.getElementById("xl-export-sheet");
      if (!sel) return;
      var sheets = this._grid.getData() || [];
      var current = sel.value;
      sel.innerHTML = '<option value="all">All sheets</option>';
      sheets.forEach(function (s, i) {
        var opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = s.name || ("Sheet" + (i + 1));
        sel.appendChild(opt);
      });
      if (current && sel.querySelector('option[value="' + current + '"]')) sel.value = current;
    },

    exportGridXlsx() {
      if (!window.XLSX) return App.error("SheetJS failed to load from CDN.");
      if (!this._grid) return App.error("Grid not initialized");
      var sel = document.getElementById("xl-export-sheet");
      var sheets = this._grid.getData() || [];
      if (!sheets.length) return App.error("Grid is empty \u2014 load a table first");
      var wb = window.XLSX.utils.book_new();
      var target = sel ? sel.value : "all";
      var self = this;
      function addSheet(s) {
        var ws = self._sheetToWs(s);
        if (ws) window.XLSX.utils.book_append_sheet(wb, ws, (s.name || "Sheet").slice(0, 31));
      }
      if (target === "all") {
        sheets.forEach(addSheet);
      } else {
        addSheet(sheets[parseInt(target)] || sheets[0]);
      }
      window.XLSX.writeFile(wb, "excel-workbench.xlsx");
      App.success("Downloaded as excel-workbench.xlsx" + (target === "all" ? " (" + sheets.length + " sheets)" : ""));
    },

    _sheetToWs(sheet) {
      if (!sheet || !sheet.rows) return null;
      var rows = sheet.rows;
      var rKeys = Object.keys(rows).filter(function (k) { return /^\d+$/.test(k); }).map(Number).sort(function (a, b) { return a - b; });
      if (!rKeys.length) return null;
      var maxCol = 0;
      var lastNonEmpty = -1;
      rKeys.forEach(function (rk) {
        var cells = (rows[rk] || {}).cells || {};
        var keys = Object.keys(cells);
        var hasContent = keys.some(function (ck) {
          var cell = cells[ck] || {};
          var t = cell.text != null ? String(cell.text) : "";
          var f = cell.f != null ? String(cell.f) : "";
          return t !== "" || f !== "";
        });
        if (hasContent) lastNonEmpty = rk;
        keys.forEach(function (ck) { var n = parseInt(ck, 10); if (n > maxCol) maxCol = n; });
      });
      var lastRow = rKeys.length - 1;
      var ws = {};
      var range = { s: { c: 0, r: 0 }, e: { c: maxCol, r: lastRow } };
      var styleData = this._gridData();
      var stylesMap = styleData ? styleData.styles : null;
      rKeys.forEach(function (rk, ri) {
        var cells = (rows[rk] || {}).cells || {};
        for (var ci = 0; ci <= maxCol; ci++) {
          var cell = cells[ci] || {};
          var addr = { c: ci, r: ri };
          var formula = cell.f != null ? String(cell.f) : "";
          var wsCell;
          if (formula.charAt(0) === "=") {
            wsCell = { f: formula.substring(1) };
            if (cell.v != null) {
              var vNum = Number(cell.v);
              if (!isNaN(vNum) && String(cell.v).trim() !== "") {
                wsCell.v = vNum; wsCell.t = "n";
              } else {
                wsCell.v = cell.v; wsCell.t = "s";
              }
            }
          } else {
            var text = cell.text != null ? String(cell.text) : "";
            var num = Number(text);
            if (text !== "" && !isNaN(num)) {
              wsCell = { v: num, t: "n" };
            } else {
              wsCell = { v: text, t: "s" };
            }
          }
          var cellStyle = null;
          if (stylesMap && cell && cell.style != null) {
            cellStyle = stylesMap[cell.style];
          }
          if (cellStyle) {
            var s = {};
            if (cellStyle.font) {
              if (cellStyle.font.bold) s.bold = true;
              if (cellStyle.font.italic) s.italic = true;
            }
            if (cellStyle.underline) s.underline = true;
            if (cellStyle.color) s.font = { color: { rgb: cellStyle.color.replace("#", "") } };
            if (cellStyle.bgcolor) s.fill = { fgColor: { rgb: cellStyle.bgcolor.replace("#", "") } };
            if (cellStyle.align === "center") s.alignment = { horizontal: "center" };
            else if (cellStyle.align === "right") s.alignment = { horizontal: "right" };
            else if (cellStyle.align === "left") s.alignment = { horizontal: "left" };
            if (cellStyle.format) s.numFmt = cellStyle.format;
            if (cellStyle.border) {
              var bSide = cellStyle.border.top || cellStyle.border.bottom || cellStyle.border.left || cellStyle.border.right;
              if (bSide) {
                var bStyleName = (bSide[0] || "thin");
                var bRgb = String(bSide[1] != null ? bSide[1] : "#000000").replace("#", "");
                s.border = { top: { style: bStyleName, color: { rgb: bRgb } }, bottom: { style: bStyleName, color: { rgb: bRgb } }, left: { style: bStyleName, color: { rgb: bRgb } }, right: { style: bStyleName, color: { rgb: bRgb } } };
              }
            }
            wsCell.s = s;
          }
          ws[window.XLSX.utils.encode_cell(addr)] = wsCell;
        }
      });
      ws["!ref"] = window.XLSX.utils.encode_range(range);
      ws["!cols"] = this._getColWidths();
      return ws;
    },

    _getColWidths() {
      var widths = [];
      try {
        var data = this._gridData();
        if (!data || !data.cols) return widths;
        var cols = data.cols;
        var len = cols.len || 0;
        for (var i = 0; i < len && i < 30; i++) {
          var w = cols.getWidth(i) || 80;
          widths.push({ wch: Math.max(5, Math.round(w / 7)) });
        }
      } catch (e) { /* noop */ }
      return widths;
    },

    _autoFitColumnWidth() {
      if (!this._grid) return App.error("Load a table first");
      try {
        var g = this._grid;
        var data = this._gridData();
        if (!data || !data.cols) return App.error("Column width API not available");
        var colStore = data.cols;
        var sheets = g.getData() || [];
        var s = sheets[this._activeSheetIndex()] || sheets[0];
        if (!s || !s.rows) return App.warning("No data in this sheet");
        var rKeys = Object.keys(s.rows).filter(function (k) { return /^\d+$/.test(k); }).map(Number).sort(function (a, b) { return a - b; });
        if (!rKeys.length) return App.warning("No data to fit");
        var maxCol = 0;
        rKeys.slice(0, Math.min(rKeys.length, 500)).forEach(function (rk) {
          var cells = (s.rows[rk] || {}).cells || {};
          Object.keys(cells).forEach(function (ck) {
            var n = parseInt(ck, 10);
            if (n > maxCol) maxCol = n;
          });
        });
        if (!g.data || !g.data.cols) return App.error("Column width API not available");
        var colStore = g.data.cols;
        for (var ci = 0; ci <= maxCol; ci++) {
          var maxLen = 0;
          rKeys.slice(0, Math.min(rKeys.length, 500)).forEach(function (rk) {
            var cell = (s.rows[rk] || {}).cells ? (s.rows[rk].cells[ci] || {}) : {};
            var t = cell.text != null ? String(cell.text) : "";
            if (t.length > maxLen) maxLen = t.length;
          });
          var width = Math.max(50, Math.min(300, maxLen * 8 + 12));
          try { colStore.setWidth(ci, width); } catch (e) { /* noop */ }
        }
        if (typeof g.reRender === "function") g.reRender();
        App.success("Auto-fit column widths for the active sheet");
      } catch (e) { App.error("Auto-fit failed: " + (e.message || e)); }
    },

    _sheetToAoa(sheet) {
      if (!sheet || !sheet.rows) return [];
      var rows = sheet.rows;
      var rKeys = Object.keys(rows).filter(function (k) { return /^\d+$/.test(k); }).map(Number).sort(function (a, b) { return a - b; });
      if (!rKeys.length) return [];
      var maxCol = 0;
      rKeys.forEach(function (rk) {
        var cells = (rows[rk] || {}).cells || {};
        Object.keys(cells).forEach(function (ck) { var n = parseInt(ck); if (n > maxCol) maxCol = n; });
      });
      var aoa = [];
      rKeys.forEach(function (rk) {
        var cells = (rows[rk] || {}).cells || {};
        var row = [];
        for (var ci = 0; ci <= maxCol; ci++) {
          var cell = cells[ci] || {};
          row.push(cell.text != null ? cell.text : (cell.value != null ? cell.value : ""));
        }
        aoa.push(row);
      });
      return aoa;
    },

    // === AI Assistant ===

    _renderChips() {
      var self = this;
      var wrap = document.getElementById("xl-ai-chips");
      if (!wrap) return;
      wrap.innerHTML = AI_CHIPS.map(function (c) { return '<button class="xl-chip" data-prompt="' + self._escape(c.prompt) + '">' + self._escape(c.label) + "</button>"; }).join("");
      wrap.querySelectorAll(".xl-chip").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var prompt = btn.getAttribute("data-prompt");
          if (!self._currentTable) return App.warning("Load a table first so the AI has data to work on.");
          self._aiSend(prompt);
        });
      });
    },

    _bindAIPanel() {
      var self = this;
      var input = document.getElementById("xl-ai-input");
      var send = document.getElementById("xl-ai-send");
      var clear = document.getElementById("xl-ai-clear");
      var doSend = function () { self._aiSend(); };
      if (send) send.addEventListener("click", doSend);
      if (input) input.addEventListener("keydown", function (e) { if (e.key === "Enter") doSend(); });
      if (clear) clear.addEventListener("click", function () {
        self._chatHistory = [];
        var area = document.getElementById("xl-ai-messages");
        if (area) {
          area.innerHTML = '<div class="xl-ai-bubble ai" id="xl-ai-empty">Ask me to do Excel-style tasks on the selected table \u2014 clean it, pivot it, chart it, or explain it.</div>';
        }
      });
    },

    _addAIMessage(role, content) {
      var area = document.getElementById("xl-ai-messages");
      if (!area) return null;
      var empty = area.querySelector("#xl-ai-empty");
      if (empty) empty.remove();
      var bubble = document.createElement("div");
      bubble.className = "xl-ai-bubble " + (role === "user" ? "user" : "ai");
      bubble.innerHTML = role === "user" ? this._escape(content) : this._renderMarkdown(content);
      area.appendChild(bubble);
      area.scrollTop = area.scrollHeight;
      return bubble;
    },

    _askWithTimeout(question, opts, ms) {
      return new Promise(function (resolve, reject) {
        var timer = setTimeout(function () { reject(new Error("Request timed out after " + (ms / 1000) + "s \u2014 Ollama may be slow or down.")); }, ms);
        API.ask(question, opts).then(function (r) { clearTimeout(timer); resolve(r); }).catch(function (e) { clearTimeout(timer); reject(e); });
      });
    },

    async _aiSend(prePrompt) {
      var self = this;
      var input = document.getElementById("xl-ai-input");
      var text = (prePrompt || (input ? input.value : "") || "").trim();
      if (!text) { this._debug.lastAction = "aiSend:empty"; return; }
      if (this._aiWaiting) { this._debug.lastAction = "aiSend:busy"; App.warning("AI is still processing the previous request\u2026"); return; }
      if (input) input.value = "";
      this._addAIMessage("user", text);
      this._aiWaiting = true;
      var sendBtn = document.getElementById("xl-ai-send");
      if (sendBtn) sendBtn.disabled = true;

      var table = this._currentTable || "";
      var question = table ? 'Using the "' + table + '" table: ' + text : text;

      try {
        this._debug.lastAction = "aiSend:calling /ask";
        this._chartIds.forEach(function (id) { try { if (window.Charts) window.Charts.destroy(id); } catch (e) { /* noop */ } });
        this._chartIds = [];
        var startedAt = Date.now();
        var result = await this._askWithTimeout(question, { tables: table ? [table] : [], sessionId: this._sessionId }, this._askTimeoutMs);
        var elapsedS = ((Date.now() - startedAt) / 1000).toFixed(1);
        if (result.session_id) self._sessionId = result.session_id;
        var area = document.getElementById("xl-ai-messages");

        var answer = (result.answer || "").trim();
        if (answer) self._addAIMessage("assistant", answer + "\n\n_" + elapsedS + "s_");

        var viz = result.visualizations || [];
        viz.forEach(function (v, i) {
          if (!area) return;
          var chartId = "xl-ai-chart-" + Date.now() + "-" + i;
          var chartDiv = document.createElement("div");
          chartDiv.className = "xl-tool-result";
          chartDiv.innerHTML = '<canvas id="' + chartId + '" style="width:100%;height:240px;"></canvas>';
          area.appendChild(chartDiv);
          var cfg = v.config || {};
          var labels, values;
          if (typeof cfg.x === "string" && result.data_preview && result.data_preview.head) {
            var head = result.data_preview.head;
            labels = head.map(function (r) { return String(r[cfg.x] != null ? r[cfg.x] : ""); });
            values = head.map(function (r) { return Number(r[cfg.y]) || 0; });
          } else {
            labels = Array.isArray(cfg.x) ? cfg.x.map(String) : (cfg.x ? [String(cfg.x)] : []);
            values = Array.isArray(cfg.y) ? cfg.y.map(function (v) { return Number(v) || 0; }) : (cfg.y ? [Number(cfg.y) || 0] : []);
          }
          if (labels.length && values.length) {
            try {
              var ct = ((v.chart_config && v.chart_config.type) || v.chart_type || "bar").toLowerCase();
              var titleLines = answer ? answer.split("\n").filter(function (l) { return l.trim() && !l.trim().startsWith("#"); }) : [];
              var title = titleLines.length ? titleLines[0].replace(/[#*>|]/g, "").trim().slice(0, 60) : "Analysis Result";
              if (ct === "pie") window.Charts.pie(chartId, labels, values, { title: title });
              else if (ct === "line" || ct === "area")
                window.Charts.line(chartId, labels, [{ label: "value", data: values, borderColor: "#10b981", backgroundColor: "rgba(16,185,129,0.15)" }], { title: title });
              else window.Charts.bar(chartId, labels, values, { title: title });
            } catch (chartErr) {
              console.warn("Chart render failed:", chartErr);
            }
          }
          if (!self._chartIds.includes(chartId)) self._chartIds.push(chartId);
        });

        var preview = result.data_preview;
        if (preview && preview.head && preview.head.length > 0 && area) {
          var cols = preview.columns || Object.keys(preview.head[0] || {});
          var total = preview.shape ? preview.shape[0] : preview.head.length;
          var rowsHtml = preview.head.slice(0, 20).map(function (r) {
            return "<tr>" + cols.map(function (c) { return '<td class="px-2 py-1 border whitespace-nowrap">' + self._escape(r[c]) + "</td>"; }).join("") + "</tr>";
          }).join("");
          var headHtml = cols.map(function (c) { return '<th class="px-2 py-1 border text-left font-semibold">' + self._escape(c) + "</th>"; }).join("");
          var loadable = preview.head.map(function (r) { return cols.map(function (c) { return (r[c] != null ? String(r[c]) : ""); }); });
          var tableDiv = document.createElement("div");
          tableDiv.className = "xl-tool-result";
          tableDiv.innerHTML =
            "<h4>Result Data</h4>" +
            '<div class="overflow-x-auto"><table class="text-xs border-collapse w-full"><thead><tr>' + headHtml + "</tr></thead><tbody>" + rowsHtml + "</tbody></table></div>" +
            '<div class="mt-2 flex flex-wrap items-center gap-2"><button class="btn btn-sm btn-primary xl-load-grid-btn"><i class="fas fa-table mr-1"></i>Load into Grid</button>' +
            '<span class="text-xs text-secondary">' + preview.head.length + " rows shown (" + total + " total)</span></div>";
          area.appendChild(tableDiv);
          var loadBtn = tableDiv.querySelector(".xl-load-grid-btn");
          if (loadBtn)
            loadBtn.addEventListener("click", function () {
              self._appendAnalysisResult("AI RESULT", cols, loadable);
              loadBtn.textContent = "\u2713 Added to analysis sheet";
              loadBtn.disabled = true;
            });
        }

        if (!answer && viz.length === 0 && !preview) {
          self._addAIMessage("assistant", "The agent returned a response but no content could be extracted. Try rephrasing your question.");
        }

        self._chatHistory.push({ role: "assistant", content: answer });
        if (area) area.scrollTop = area.scrollHeight;
      } catch (e) {
        self._debug.errors.push({ time: Date.now(), msg: (e && e.message) || String(e) });
        var msg = (e && e.message) || String(e);
        self._addAIMessage(
          "assistant",
          "**AI assistant unavailable:** " + msg +
          "\n\nThe manual tools \u2014 Clean, Pivot, VLOOKUP, Charts, Report, Export, Stats, Insights, Forecast \u2014 still work without the LLM. " +
          (msg.indexOf("503") >= 0 || msg.indexOf("not initialized") >= 0
            ? "Start Ollama (`ollama serve`) and ensure a model is pulled (`ollama list`)."
            : "Check that Ollama is running and the LLM agent is enabled in Settings.")
        );
      } finally {
        self._aiWaiting = false;
        var btn = document.getElementById("xl-ai-send");
        if (btn) btn.disabled = false;
      }
    },

    aiTask(prompt) {
      try {
        var input = document.getElementById("xl-ai-input");
        if (!input) { App.error("AI panel not found"); return; }
        input.value = prompt;
        if (!this._showAI) this._toggleAI();
        this._aiSend(prompt);
        var sidebar = document.getElementById("xl-ai-sidebar");
        if (sidebar) sidebar.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (e) {
        App.error("AI task failed: " + (e.message || e));
      }
    },

    // === Utilities ===

    _escape(v) {
      return String(v == null ? "" : v)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },

    _dedupe(text) {
      if (!text || text.length < 50) return text;
      var lines = text.split("\n");
      var out = [];
      lines.forEach(function (l, i) {
        if (i > 0 && l.trim() === lines[i - 1].trim() && l.trim() !== "") return;
        out.push(l);
      });
      return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
    },

    _renderKV(obj, opts) {
      var self = this;
      opts = opts || {};
      if (!obj || typeof obj !== "object") return '<p class="text-sm">' + self._escape(obj) + "</p>";
      var skip = new Set(opts.skip || []);
      var parts = [];
      Object.keys(obj).forEach(function (k) {
        if (skip.has(k)) return;
        var v = obj[k];
        if (v == null || v === "") return;
        var display;
        if (Array.isArray(v)) {
          display = "<ul>" + v.slice(0, 20).map(function (x) { return "<li>" + self._escape(typeof x === "object" ? JSON.stringify(x) : x) + "</li>"; }).join("") + "</ul>";
        } else if (typeof v === "object") {
          display = self._renderKV(v);
        } else {
          display = self._escape(v);
        }
        parts.push('<p class="text-sm"><span class="font-medium">' + self._escape(k) + ":</span> " + display + "</p>");
      });
      return parts.join("");
    },

    _renderInsights(ins) {
      var self = this;
      if (!ins || typeof ins !== "object" || Array.isArray(ins)) {
        if (typeof ins === "string") return '<p class="text-sm">' + self._escape(ins) + "</p>";
        if (Array.isArray(ins)) return "<ul>" + ins.map(function (i) { return '<li class="text-sm mb-1">' + self._escape(typeof i === "string" ? i : JSON.stringify(i)) + "</li>"; }).join("") + "</ul>";
        return self._renderKV(ins);
      }
      var parts = [];
      if (ins.confidence) {
        var conf = String(ins.confidence).toLowerCase();
        var cc = conf === "high" ? "#10b981" : (conf === "low" ? "#ef4444" : "#f59e0b");
        parts.push('<p class="text-xs mb-1">Confidence: <b style="color:' + cc + ';">' + self._escape(String(ins.confidence).toUpperCase()) + "</b></p>");
      }
      if (ins.summary) parts.push('<div class="text-sm mb-2">' + self._renderMarkdown(String(ins.summary)) + "</div>");
      var findings = ins.key_findings || ins.findings || [];
      if (findings.length) parts.push('<h4>Key Findings</h4><ul class="list-disc ml-5 space-y-0.5">' +
        findings.map(function (f) { return '<li class="text-sm mb-0.5">' + self._renderMarkdown(typeof f === "string" ? f : (f.text || f.insight || JSON.stringify(f))) + "</li>"; }).join("") + "</ul>");
      var ms = ins.metrics_summary;
      if (Array.isArray(ms) && ms.length) {
        parts.push("<h4>Column Statistics</h4>" +
          '<div class="overflow-x-auto"><table class="text-xs border-collapse w-full"><thead><tr>' +
          "<th class=\"px-2 py-1 border text-left\">Column</th><th class=\"px-2 py-1 border text-right\">Mean</th><th class=\"px-2 py-1 border text-right\">Median</th><th class=\"px-2 py-1 border text-right\">Std Dev</th><th class=\"px-2 py-1 border text-right\">Min</th><th class=\"px-2 py-1 border text-right\">Max</th>" +
          "</tr></thead><tbody>" +
          ms.map(function (m) {
            return "<tr><td class=\"px-2 py-1 border font-medium\">" + self._escape(m.column) + "</td>" +
              ["mean", "median", "std", "min", "max"].map(function (k) {
                return '<td class="px-2 py-1 border text-right font-mono">' + self._fmtNum(m[k]) + "</td>";
              }).join("") + "</tr>";
          }).join("") + "</tbody></table></div>");
      }
      var corr = ins.correlation_highlights || [];
      if (corr.length) parts.push('<h4>Correlations</h4><ul class="list-disc ml-5 space-y-0.5">' +
        corr.map(function (c) { return '<li class="text-sm mb-0.5">' + self._renderMarkdown(String(c)) + "</li>"; }).join("") + "</ul>");
      var outliers = ins.outlier_highlights || [];
      if (outliers.length) parts.push('<h4>Outliers</h4><ul class="list-disc ml-5 space-y-0.5">' +
        outliers.map(function (o) { return '<li class="text-sm mb-0.5">' + self._renderMarkdown(String(o)) + "</li>"; }).join("") + "</ul>");
      var recs = ins.recommendations || [];
      if (recs.length) parts.push('<h4>Next Steps</h4><ol class="list-decimal ml-5 space-y-0.5">' +
        recs.map(function (r) { return '<li class="text-sm mb-0.5">' + self._renderMarkdown(typeof r === "string" ? r : (r.text || JSON.stringify(r))) + "</li>"; }).join("") + "</ol>");
      if (!parts.length) return self._renderKV(ins);
      return parts.join("");
    },

    _renderReport(res) {
      var self = this;
      var parts = [];
      if (res.title) parts.push("<h4>" + self._escape(res.title) + "</h4>");
      if (res.summary) parts.push(typeof res.summary === "string"
        ? '<p class="text-sm">' + self._escape(res.summary) + "</p>" : self._renderKV(res.summary));
      if (res.metrics) parts.push("<h4>Key Metrics</h4>" + self._renderKV(res.metrics));
      var insights = res.insights || res.findings || res.key_findings;
      if (Array.isArray(insights) && insights.length) {
        parts.push("<h4>Insights</h4><ul>" + insights.map(function (i) { return "<li>" + self._escape(typeof i === "string" ? i : (i.text || i.title || JSON.stringify(i))) + "</li>"; }).join("") + "</ul>");
      }
      var recs = res.recommendations || res.suggestions;
      if (Array.isArray(recs) && recs.length) {
        parts.push("<h4>Recommendations</h4><ul>" + recs.map(function (r) { return "<li>" + self._escape(typeof r === "string" ? r : JSON.stringify(r)) + "</li>"; }).join("") + "</ul>");
      }
      if (!parts.length) parts.push('<pre class="text-xs overflow-x-auto">' + self._escape(JSON.stringify(res, null, 2)) + "</pre>");
      return parts.join("");
    },

    _renderMarkdown(text) {
      var self = this;
      if (!text) return "";
      var html = self._escape(text);
      html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
        return '<pre class="bg-gray-800 text-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-xs font-mono"><code>' + self._escape(code) + "</code></pre>";
      });
      html = html.replace(/```(\w*)\n([\s\S]*?)$/, function (_, lang, code) {
        return '<pre class="bg-gray-800 text-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-xs font-mono"><code>' + self._escape(code) + "</code></pre>";
      });
      html = html.replace(/`([^`]+)`/g, '<code class="bg-gray-200 px-1.5 py-0.5 rounded text-xs font-mono">$1</code>');
      html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      html = html.replace(/\|(.+)\|[\r\n]+\|[\s:|-]+\|[\r\n]+((?:\|.*\|[\r\n]*)+)/g, function (m, header, body) {
        var cells = function (row) { return row.trim().replace(/^\|+|\|+$/g, "").split("|").map(function (c) { return c.trim(); }).filter(Boolean); };
        var h = cells(header);
        var rows = body.trim().split(/\r?\n/).filter(function (r) { return r.trim().startsWith("|"); });
        var headHtml = h.map(function (c) { return '<th class="px-2 py-1 border text-left font-semibold">' + self._escape(c) + "</th>"; }).join("");
        var rowsHtml = rows.map(function (r) {
          var cs = cells(r).map(function (c) { return '<td class="px-2 py-1 border whitespace-nowrap">' + self._escape(c) + "</td>"; }).join("");
          return "<tr>" + cs + "</tr>";
        }).join("");
        return '<div class="overflow-x-auto my-2"><table class="text-xs border-collapse w-full">' + (headHtml ? "<thead>" + headHtml + "</thead>" : "") + "<tbody>" + rowsHtml + "</tbody></table></div>";
      });
      var pres = [];
      html = html.replace(/(<pre[\s\S]*?<\/pre>)/gi, function (m) { pres.push(m); return "%%PRE" + (pres.length - 1) + "%%"; });
      html = html.replace(/\n/g, "<br>");
      pres.forEach(function (b, i) { html = html.replace("%%PRE" + i + "%%", b); });
      return html;
    },
  };

  window.ExcelPage = ExcelPage;
  window._xlDebug = ExcelPage._debug;
  (window._PAGES = window._PAGES || {})["#excel"] = ExcelPage;
})();

