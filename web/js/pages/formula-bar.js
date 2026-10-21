/* ═══════════════════════════════════════════════════════════════════
   Formula Bar Component
   ───────────────────────────────────────────────────────────────────
   Excel-like formula bar with:
   • Cell name box (editable — type "B5" to jump)
   • Formula input (shows/edits raw formula for selected cell)
   • fx button (toggles function list panel)
   • Autocomplete dropdown (suggests functions as you type "=")
   • Keyboard navigation in autocomplete
   • Cell reference highlighting in the grid

   Usage:
     FormulaBar.init(gridContainerEl, gridInstance)
     FormulaBar.sync(row, col)  // called on cell selection
     FormulaBar.destroy()
   ═══════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  var FE = null; // FormulaEngine reference (set on init)

  var FormulaBar = {
    _grid: null,
    _container: null,   // the #xl-grid container element
    _nameBox: null,     // cell name input (A1, B2, etc.)
    _input: null,       // formula text input
    _fxBtn: null,       // fx toggle button
    _acDropdown: null,  // autocomplete dropdown div
    _fxPanel: null,     // function list panel
    _activeRow: 0,
    _activeCol: 0,
    _activeSheet: "Sheet1",
    _acIndex: -1,       // selected autocomplete index
    _acItems: [],       // current autocomplete items
    _highlightEls: [],  // highlighted cell elements
    _open: false,
    _bound: false,      // guards _bindEvents against re-binding on rebuild

    // ── Initialize ─────────────────────────────────────────────────
    init: function (containerEl, grid) {
      FE = window.FormulaEngine;
      this._container = containerEl;
      this._grid = grid;
      this._buildHTML();
      this._bindEvents();
    },

    // ── Build the formula bar HTML ─────────────────────────────────
    _buildHTML: function () {
      if (document.getElementById("xl-formula-bar")) return; // already built

      var bar = document.createElement("div");
      bar.id = "xl-formula-bar";
      bar.className = "xl-formula-bar";
      bar.innerHTML =
        '<div class="xl-formula-bar-inner">' +
          '<input id="xl-formula-namebox" class="xl-formula-namebox" type="text" value="A1" title="Cell reference — type a cell address to jump to (e.g. B5)" />' +
          '<span class="xl-formula-separator"></span>' +
          '<button id="xl-formula-fx" class="xl-formula-fx" title="Insert function (opens function browser)">fx</button>' +
          '<input id="xl-formula-input" class="xl-formula-input" type="text" placeholder="" autocomplete="off" spellcheck="false" title="Enter a formula (start with =) or value" />' +
        '</div>' +
        '<div id="xl-formula-ac" class="xl-formula-autocomplete" style="display:none;"></div>' +
        '<div id="xl-formula-fx-panel" class="xl-formula-fx-panel" style="display:none;"></div>';

      // Insert before the grid container
      var parent = this._container.parentNode;
      if (parent) {
        parent.insertBefore(bar, this._container);
      }

      this._nameBox = document.getElementById("xl-formula-namebox");
      this._input = document.getElementById("xl-formula-input");
      this._fxBtn = document.getElementById("xl-formula-fx");
      this._acDropdown = document.getElementById("xl-formula-ac");
      this._fxPanel = document.getElementById("xl-formula-fx-panel");
    },

    // ── Event binding ──────────────────────────────────────────────
    _bindEvents: function () {
      var self = this;
      // The grid is destroyed + recreated on every analysis append, and init()
      // is called again each time. Without this guard the document-level
      // listeners would stack up and double-fire after a few operations.
      if (this._bound) return;
      this._bound = true;

      // Name box: jump to cell on Enter
      this._nameBox.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          self._jumpToCell(self._nameBox.value.trim());
          self._nameBox.blur();
        }
        if (e.key === "Escape") {
          self._nameBox.value = self._cellName(self._activeRow, self._activeCol);
          self._nameBox.blur();
        }
      });

      // Name box: select all on focus
      this._nameBox.addEventListener("focus", function () {
        self._nameBox.select();
      });

      // Formula input: typing events
      this._input.addEventListener("input", function () {
        self._onInput();
      });

      // Formula input: keyboard navigation
      this._input.addEventListener("keydown", function (e) {
        self._onInputKeydown(e);
      });

      // Formula input: focus/blur
      this._input.addEventListener("focus", function () {
        self._open = true;
      });

      // fx button toggle
      this._fxBtn.addEventListener("click", function () {
        self._toggleFxPanel();
      });

      // Close autocomplete/fx-panel on outside click
      document.addEventListener("click", function (e) {
        if (!e.target.closest("#xl-formula-ac") &&
            !e.target.closest("#xl-formula-input") &&
            !e.target.closest("#xl-formula-fx-panel") &&
            !e.target.closest("#xl-formula-fx")) {
          self._hideAutocomplete();
          self._hideFxPanel();
        }
      });
    },

    // ── Sync with selected cell ────────────────────────────────────
    sync: function (row, col, sheetName) {
      this._activeRow = row;
      this._activeCol = col;
      this._activeSheet = sheetName || this._activeSheet;

      // Update name box
      this._nameBox.value = this._cellName(row, col);

      // Get cell content (formula or value)
      var formula = "";
      var value = "";
      if (FE && FE.isAvailable()) {
        formula = FE.getCellFormula(this._activeSheet, row, col);
        value = FE.getCellValue(this._activeSheet, row, col);
      }

      // Show formula if it exists, otherwise the value
      if (formula) {
        this._input.value = formula;
        this._input.title = "Formula: " + formula + " → Result: " + value;
      } else {
        this._input.value = value != null ? String(value) : "";
        this._input.title = "Cell value (type = to enter a formula)";
      }

      // Highlight referenced cells if editing a formula
      if (formula && formula.charAt(0) === "=") {
        this._highlightRefs(formula);
      } else {
        this._clearHighlights();
      }
    },

    // ── Get current input value ────────────────────────────────────
    getValue: function () {
      return this._input ? this._input.value : "";
    },

    // ── Set input value programmatically ───────────────────────────
    setValue: function (val) {
      if (this._input) this._input.value = val;
    },

    // ── Input handler ──────────────────────────────────────────────
    _onInput: function () {
      var val = this._input.value;
      if (val.charAt(0) === "=") {
        this._showAutocomplete(val);
        this._highlightRefs(val);
      } else {
        this._hideAutocomplete();
        this._clearHighlights();
      }
    },

    // ── Keyboard handler for formula input ─────────────────────────
    _onInputKeydown: function (e) {
      var acVisible = this._acDropdown && this._acDropdown.style.display !== "none";

      if (acVisible) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          this._acIndex = Math.min(this._acIndex + 1, this._acItems.length - 1);
          this._renderAcSelection();
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          this._acIndex = Math.max(this._acIndex - 1, 0);
          this._renderAcSelection();
          return;
        }
        if (e.key === "Tab" || e.key === "Enter") {
          if (this._acIndex >= 0 && this._acIndex < this._acItems.length) {
            e.preventDefault();
            this._insertAutocomplete(this._acItems[this._acIndex]);
            return;
          }
        }
        if (e.key === "Escape") {
          this._hideAutocomplete();
          return;
        }
      }

      // Enter: commit the formula
      if (e.key === "Enter" && !acVisible) {
        e.preventDefault();
        this._commitFormula();
        return;
      }

      // Escape: cancel editing
      if (e.key === "Escape" && !acVisible) {
        e.preventDefault();
        this.sync(this._activeRow, this._activeCol);
        this._input.blur();
        return;
      }
    },

    // ── Commit formula to grid ─────────────────────────────────────
    _commitFormula: function () {
      var val = this._input.value;
      if (!this._grid) return;

      // Which sheet index are we writing to? x-spreadsheet's cellText takes
      // the sheet index (default 0) — using the active one keeps multi-sheet
      // editing working.
      var sheetIdx = 0;
      try {
        var d = this._grid.sheet && this._grid.sheet.data;
        var datas = this._grid.datas || [];
        sheetIdx = Math.max(0, datas.indexOf(d));
      } catch (e) { sheetIdx = 0; }

      // Write to x-spreadsheet cell
      try {
        if (typeof this._grid.cellText === "function") {
          this._grid.cellText(this._activeRow, this._activeCol, val, sheetIdx);
        } else {
          // Fallback: modify data directly
          var sheets = this._grid.getData ? this._grid.getData() : [];
          var si = sheetIdx;
          if (sheets[si] && sheets[si].rows) {
            var r = sheets[si].rows;
            if (!r[this._activeRow]) r[this._activeRow] = { cells: {} };
            if (!r[this._activeRow].cells) r[this._activeRow].cells = {};
            r[this._activeRow].cells[this._activeCol] = { text: val };
          }
        }
      } catch (err) {
        console.warn("[FormulaBar] cellText failed:", err);
      }

      // Evaluate via FormulaEngine
      if (FE && FE.isAvailable() && val.charAt(0) === "=") {
        FE.setCell(this._activeSheet, this._activeRow, this._activeCol, val);
      }

      // cellText() ("finished" path) does NOT fire x-spreadsheet's change
      // event in this version, so the grid never recomputes or repaints the
      // cell on its own. Fire the change listeners (which run the formula
      // engine sync) and re-render so the evaluated result actually shows.
      try {
        if (this._grid.sheet && typeof this._grid.sheet.trigger === "function") {
          this._grid.sheet.trigger("change", "finished", val);
        }
        if (typeof this._grid.reRender === "function") this._grid.reRender();
      } catch (e) { /* noop */ }

      this._hideAutocomplete();
      this._clearHighlights();

      // Re-sync to show the evaluated result
      var self = this;
      setTimeout(function () {
        self.sync(self._activeRow, self._activeCol);
      }, 50);
    },

    // ── Jump to cell ───────────────────────────────────────────────
    _jumpToCell: function (addr) {
      if (!addr) return;
      var FE = window.FormulaEngine;
      var pos = FE ? FE.parseA1(addr.toUpperCase()) : null;
      if (!pos) return;
      this._activeRow = pos.row;
      this._activeCol = pos.col;
      this.sync(pos.row, pos.col);
      // Try to scroll the cell into view
      if (this._grid && typeof this._grid.scroll === "function") {
        try { this._grid.scroll({ row: pos.row, col: pos.col }); } catch (e) { /* noop */ }
      }
    },

    // ── Cell name helper ───────────────────────────────────────────
    _cellName: function (row, col) {
      if (FE) return FE.toA1(row, col);
      // Fallback
      var s = "";
      var c = col;
      while (c >= 0) {
        s = String.fromCharCode(65 + (c % 26)) + s;
        c = Math.floor(c / 26) - 1;
      }
      return s + (row + 1);
    },

    // ══════════════════════════════════════════════════════════════════
    //  AUTOCOMPLETE
    // ══════════════════════════════════════════════════════════════════

    _showAutocomplete: function (input) {
      if (!FE || !this._acDropdown) return;

      // Extract partial function name after "="
      var afterEq = input.substring(1);
      // Find the current function being typed (last word after operators/parens)
      var match = afterEq.match(/([A-Z_][A-Z0-9_]*)$/i);
      if (!match) { this._hideAutocomplete(); return; }

      var prefix = match[1].toUpperCase();
      if (prefix.length < 1) { this._hideAutocomplete(); return; }

      var funcs = FE.getFunctions();
      var filtered = funcs.filter(function (f) {
        return f.name.indexOf(prefix) === 0;
      });

      if (!filtered.length) { this._hideAutocomplete(); return; }

      this._acItems = filtered;
      this._acIndex = 0;
      this._renderAutocomplete();
    },

    _renderAutocomplete: function () {
      var html = '<div class="xl-ac-list">';
      var currentCat = "";
      for (var i = 0; i < this._acItems.length; i++) {
        var f = this._acItems[i];
        if (f.category !== currentCat) {
          currentCat = f.category;
          html += '<div class="xl-ac-category">' + this._esc(currentCat) + '</div>';
        }
        html += '<div class="xl-ac-item" data-idx="' + i + '" title="' + this._esc(f.desc) + '">' +
                  '<span class="xl-ac-name">' + this._esc(f.name) + '</span>' +
                  '<span class="xl-ac-params">' + this._esc(f.params) + '</span>' +
                '</div>';
      }
      html += '</div>';
      this._acDropdown.innerHTML = html;
      this._acDropdown.style.display = "block";

      // Position below the formula input
      var inputRect = this._input.getBoundingClientRect();
      var barRect = this._acDropdown.parentNode.getBoundingClientRect();
      this._acDropdown.style.left = (inputRect.left - barRect.left) + "px";
      this._acDropdown.style.maxWidth = Math.max(300, inputRect.width) + "px";

      // Click handlers
      var self = this;
      this._acDropdown.querySelectorAll(".xl-ac-item").forEach(function (el) {
        el.addEventListener("click", function () {
          var idx = parseInt(el.getAttribute("data-idx"), 10);
          self._insertAutocomplete(self._acItems[idx]);
        });
        el.addEventListener("mouseenter", function () {
          self._acIndex = parseInt(el.getAttribute("data-idx"), 10);
          self._renderAcSelection();
        });
      });

      this._renderAcSelection();
    },

    _renderAcSelection: function () {
      if (!this._acDropdown) return;
      this._acDropdown.querySelectorAll(".xl-ac-item").forEach(function (el, i) {
        el.classList.toggle("selected", i === this._acIndex);
      }.bind(this));

      // Scroll selected into view
      var sel = this._acDropdown.querySelector(".xl-ac-item.selected");
      if (sel) sel.scrollIntoView({ block: "nearest" });
    },

    _insertAutocomplete: function (func) {
      if (!func) return;
      var val = this._input.value;
      var afterEq = val.substring(1);
      // Replace the partial function name with the full name + "("
      var replaced = afterEq.replace(/([A-Z_][A-Z0-9_]*)$/i, func.name + "(");
      this._input.value = "=" + replaced;
      this._hideAutocomplete();
      this._input.focus();
      // Place cursor after the opening paren
      var pos = this._input.value.length - 1;
      this._input.setSelectionRange(pos, pos);
    },

    _hideAutocomplete: function () {
      if (this._acDropdown) this._acDropdown.style.display = "none";
      this._acItems = [];
      this._acIndex = -1;
    },

    // ══════════════════════════════════════════════════════════════════
    //  FUNCTION LIST PANEL (fx button)
    // ══════════════════════════════════════════════════════════════════

    _toggleFxPanel: function () {
      if (!this._fxPanel) return;
      if (this._fxPanel.style.display !== "none") {
        this._hideFxPanel();
      } else {
        this._showFxPanel();
      }
    },

    _showFxPanel: function () {
      if (!FE || !this._fxPanel) return;
      var funcs = FE.getFunctions();
      var categories = {};
      funcs.forEach(function (f) {
        if (!categories[f.category]) categories[f.category] = [];
        categories[f.category].push(f);
      });

      var html = '<div class="xl-fx-header"><h4>Insert Function</h4>' +
        '<button class="xl-fx-close" id="xl-fx-close">&times;</button></div>' +
        '<div class="xl-fx-search"><input type="text" id="xl-fx-search" class="form-input" placeholder="Search functions…" /></div>' +
        '<div class="xl-fx-body" id="xl-fx-body">';

      var catNames = Object.keys(categories).sort();
      for (var ci = 0; ci < catNames.length; ci++) {
        var cat = catNames[ci];
        html += '<div class="xl-fx-category">' + this._esc(cat) + '</div>';
        var fnList = categories[cat];
        for (var fi = 0; fi < fnList.length; fi++) {
          var f = fnList[fi];
          html += '<div class="xl-fx-func" data-name="' + this._esc(f.name) + '" data-params="' + this._esc(f.params) + '" data-desc="' + this._esc(f.desc) + '">' +
            '<span class="xl-fx-func-name">' + this._esc(f.name) + '</span>' +
            '<span class="xl-fx-func-params">' + this._esc(f.params) + '</span>' +
            '<div class="xl-fx-func-desc">' + this._esc(f.desc) + '</div>' +
          '</div>';
        }
      }
      html += '</div>';
      this._fxPanel.innerHTML = html;
      this._fxPanel.style.display = "block";

      // Search filter
      var self = this;
      var searchInput = document.getElementById("xl-fx-search");
      if (searchInput) {
        searchInput.addEventListener("input", function () {
          var q = searchInput.value.toLowerCase();
          self._fxPanel.querySelectorAll(".xl-fx-func").forEach(function (el) {
            var name = (el.getAttribute("data-name") || "").toLowerCase();
            var desc = (el.getAttribute("data-desc") || "").toLowerCase();
            el.style.display = (!q || name.indexOf(q) >= 0 || desc.indexOf(q) >= 0) ? "" : "none";
          });
        });
        searchInput.focus();
      }

      // Close button
      var closeBtn = document.getElementById("xl-fx-close");
      if (closeBtn) closeBtn.addEventListener("click", function () { self._hideFxPanel(); });

      // Click on function → insert into formula
      this._fxPanel.querySelectorAll(".xl-fx-func").forEach(function (el) {
        el.addEventListener("click", function () {
          var name = el.getAttribute("data-name");
          var params = el.getAttribute("data-params");
          self._insertFunction(name, params);
          self._hideFxPanel();
        });
      });
    },

    _hideFxPanel: function () {
      if (this._fxPanel) this._fxPanel.style.display = "none";
    },

    _insertFunction: function (name, params) {
      if (!this._input) return;
      var val = this._input.value;
      if (val.charAt(0) !== "=") {
        this._input.value = "=" + name + "()";
      } else {
        this._input.value = val + name + "()";
      }
      this._input.focus();
      // Position cursor inside the parens
      var pos = this._input.value.length - 1;
      this._input.setSelectionRange(pos, pos);
    },

    // ══════════════════════════════════════════════════════════════════
    //  CELL REFERENCE HIGHLIGHTING
    // ══════════════════════════════════════════════════════════════════

    _highlightRefs: function (formula) {
      this._clearHighlights();
      if (!formula || !FE) return;

      var refs = FE.extractRefs(formula);
      if (!refs.length) return;

      // Try to find the x-spreadsheet canvas to overlay highlights
      var gridEl = this._container;
      if (!gridEl) return;
      var canvas = gridEl.querySelector(".x-spreadsheet canvas") ||
                   gridEl.querySelector("canvas");
      if (!canvas) return;

      // Create highlight overlay
      var overlay = document.createElement("div");
      overlay.className = "xl-ref-highlight-overlay";
      overlay.style.cssText = "position:absolute;top:0;left:0;right:0;bottom:0;pointer-events:none;z-index:10;";

      var colorIdx = 0;
      for (var i = 0; i < refs.length; i++) {
        var ref = refs[i];
        var color = FormulaEngine.REF_COLORS[colorIdx % FormulaEngine.REF_COLORS.length];
        colorIdx++;

        if (ref.type === "range" && ref.start && ref.end) {
          // Add range highlight
          var div = document.createElement("div");
          div.className = "xl-ref-highlight-range";
          div.setAttribute("data-range", ref.text);
          div.style.borderColor = color;
          div.style.backgroundColor = color + "15";
          overlay.appendChild(div);
        } else if (ref.type === "cell" && ref.pos) {
          // Add single cell highlight
          var div2 = document.createElement("div");
          div2.className = "xl-ref-highlight-cell";
          div2.setAttribute("data-cell", ref.text);
          div2.style.borderColor = color;
          overlay.appendChild(div2);
        }
      }

      // Store for cleanup
      this._highlightEls.push(overlay);
      gridEl.style.position = "relative";
      gridEl.appendChild(overlay);

      // Note: positioning individual highlights requires knowing the cell
      // pixel coordinates from x-spreadsheet. This is best-effort —
      // the overlay will be visible but may need manual positioning
      // based on the grid's internal scroll state.
    },

    _clearHighlights: function () {
      for (var i = 0; i < this._highlightEls.length; i++) {
        if (this._highlightEls[i] && this._highlightEls[i].parentNode) {
          this._highlightEls[i].parentNode.removeChild(this._highlightEls[i]);
        }
      }
      this._highlightEls = [];
    },

    // ── Utility ────────────────────────────────────────────────────
    _esc: function (v) {
      return String(v == null ? "" : v)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    },

    destroy: function () {
      this._clearHighlights();
      this._hideAutocomplete();
      this._hideFxPanel();
      var bar = document.getElementById("xl-formula-bar");
      if (bar && bar.parentNode) bar.parentNode.removeChild(bar);
      this._grid = null;
      this._container = null;
      this._bound = false;
    },
  };

  window.FormulaBar = FormulaBar;
})();
