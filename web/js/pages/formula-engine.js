/* ═══════════════════════════════════════════════════════════════════
   Formula Engine
   ───────────────────────────────────────────────────────────────────
   Comprehensive Excel-like formula engine wrapping HyperFormula.
   Provides 70+ built-in functions, cell reference resolution,
   dependency tracking, and recalculation.

   Usage:
     FormulaEngine.init()
     FormulaEngine.loadSheet("Sheet1", [[1,2,3],[4,5,6]])
     FormulaEngine.setCell("Sheet1", 0, 0, "=SUM(A2:A3)")
     FormulaEngine.getCellValue("Sheet1", 0, 0)  // → 9
   ═══════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  // ── Error value constants ─────────────────────────────────────────
  var ERR = {
    VALUE:  "#VALUE!",
    REF:    "#REF!",
    NAME:   "#NAME?",
    DIV0:   "#DIV/0!",
    NA:     "#N/A",
    NULL:   "#NULL!",
    NUM:    "#NUM!",
    SPILL:  "#SPILL!",
  };

  // ── Helper: column letter ↔ index ────────────────────────────────
  function colToLetter(col) {
    var s = "";
    var c = col;
    while (c >= 0) {
      s = String.fromCharCode(65 + (c % 26)) + s;
      c = Math.floor(c / 26) - 1;
    }
    return s;
  }

  function letterToCol(letters) {
    var s = letters.toUpperCase();
    var n = 0;
    for (var i = 0; i < s.length; i++) {
      n = n * 26 + (s.charCodeAt(i) - 64);
    }
    return n - 1;
  }

  // Parse "B5" → {row:4, col:1}  (0-based)
  function parseA1(ref) {
    if (!ref || typeof ref !== "string") return null;
    var m = ref.match(/^\$?([A-Z]+)\$?(\d+)$/i);
    if (!m) return null;
    return { col: letterToCol(m[1]), row: parseInt(m[2], 10) - 1 };
  }

  // {row:4, col:1} → "B5"
  function toA1(row, col) {
    return colToLetter(col) + (row + 1);
  }

  // Parse "B2:C10" → [{row:1,col:1}, ..., {row:9,col:2}]
  function parseRange(rangeStr) {
    if (!rangeStr || typeof rangeStr !== "string") return [];
    var parts = rangeStr.split(":");
    if (parts.length !== 2) {
      var single = parseA1(rangeStr);
      return single ? [single] : [];
    }
    var start = parseA1(parts[0]);
    var end = parseA1(parts[1]);
    if (!start || !end) return [];
    var cells = [];
    var r1 = Math.min(start.row, end.row);
    var r2 = Math.max(start.row, end.row);
    var c1 = Math.min(start.col, end.col);
    var c2 = Math.max(start.col, end.col);
    for (var r = r1; r <= r2; r++) {
      for (var c = c1; c <= c2; c++) {
        cells.push({ row: r, col: c });
      }
    }
    return cells;
  }

  // Extract all cell references from a formula string
  function extractRefs(formula) {
    if (!formula || typeof formula !== "string") return [];
    var seen = {};
    var refs = [];
    // Match ranges like A1:B5 and single refs like A1
    var re = /\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?/gi;
    var m;
    while ((m = re.exec(formula)) !== null) {
      var key = m[0].replace(/\$/g, "");
      if (!seen[key]) {
        seen[key] = true;
        if (m[3]) {
          refs.push({ type: "range", text: key, start: parseA1(m[1] + m[2]), end: parseA1(m[3] + m[4]) });
        } else {
          refs.push({ type: "cell", text: key, pos: parseA1(m[1] + m[2]) });
        }
      }
    }
    return refs;
  }

  // ── Highlight color palette ──────────────────────────────────────
  var REF_COLORS = [
    "#3b82f6", // blue
    "#ef4444", // red
    "#10b981", // green
    "#f59e0b", // amber
    "#8b5cf6", // purple
    "#ec4899", // pink
  ];

  // ══════════════════════════════════════════════════════════════════
  //  FORMULA ENGINE
  // ══════════════════════════════════════════════════════════════════
  window.FormulaEngine = {
    _hf: null,              // HyperFormula instance
    _sheetMap: {},          // sheetName → HF sheet index
    _sheetNames: [],        // ordered list of sheet names
    _updating: false,       // guard against recursive updates
    _cellFormulas: {},      // "Sheet1!A1" → "=SUM(B1:B5)"
    _customFunctions: {},   // name → {call, parameters}

    ERR: ERR,
    colToLetter: colToLetter,
    letterToCol: letterToCol,
    parseA1: parseA1,
    toA1: toA1,
    parseRange: parseRange,
    extractRefs: extractRefs,
    REF_COLORS: REF_COLORS,

    // ── Initialization ─────────────────────────────────────────────
    init: function () {
      if (this._hf) return;
      try {
        var HF = window.HyperFormula;
        if (!HF) {
          console.warn("[FormulaEngine] HyperFormula not loaded from CDN — formulas will use basic fallback.");
          return;
        }
        this._hf = HF.buildEmpty({
          licenseKey: "gpl-v3",
          chooseAddressMappingStrategy: "ZERO_BASED",
          useArrayArithmetic: true,
          smartParsing: true,
          locale: "en-US",
          parseDate: function (v) {
            if (v instanceof Date) return { year: v.getFullYear(), month: v.getMonth() + 1, day: v.getDate() };
            return null;
          },
          formatDate: function (v) {
            return v.year + "-" + String(v.month).padStart(2, "0") + "-" + String(v.day).padStart(2, "0");
          },
        });
        this._registerCustomFunctions();
        console.log("[FormulaEngine] HyperFormula initialized OK — " + Object.keys(this._customFunctions).length + " custom functions registered.");
      } catch (e) {
        console.error("[FormulaEngine] HyperFormula init failed:", e);
        this._hf = null;
      }
    },

    // ── Sheet management ───────────────────────────────────────────
    // NOTE: HyperFormula 2.x `addSheet()` returns the sheet NAME, so we
    // resolve the numeric sheet id ourselves and always store ids in
    // _sheetMap. Cell addresses are { sheet: <id>, row, col }.
    addSheet: function (name) {
      if (!this._hf) return -1;
      if (this._sheetMap[name] != null) return this._sheetMap[name];
      try {
        this._hf.addSheet(name);
        var idx = this._hf.getSheetId(name);
        this._sheetMap[name] = idx;
        this._sheetNames.push(name);
        return idx;
      } catch (e) {
        console.warn("[FormulaEngine] addSheet failed:", name, e);
        return -1;
      }
    },

    removeSheet: function (name) {
      if (!this._hf) return;
      var idx = this._sheetMap[name];
      if (idx == null) return;
      try { this._hf.removeSheet(idx); } catch (e) { /* noop */ }
      delete this._sheetMap[name];
      this._sheetNames = this._sheetNames.filter(function (n) { return n !== name; });
    },

    listSheets: function () {
      return this._sheetNames.slice();
    },

    loadSheet: function (name, data2d) {
      if (!this._hf) return false;

      // Rebuild the HyperFormula instance fresh — avoids all sheet ID conflicts
      try {
        var HF = window.HyperFormula;
        if (!HF) return false;
        var oldHf = this._hf;
        this._hf = HF.buildEmpty({
          licenseKey: "gpl-v3",
          chooseAddressMappingStrategy: "ZERO_BASED",
          useArrayArithmetic: true,
          smartParsing: true,
          locale: "en-US",
          parseDate: function (v) {
            if (v instanceof Date) return { year: v.getFullYear(), month: v.getMonth() + 1, day: v.getDate() };
            return null;
          },
          formatDate: function (v) {
            return v.year + "-" + String(v.month).padStart(2, "0") + "-" + String(v.day).padStart(2, "0");
          },
        });
        // Destroy old instance
        try { oldHf.destroy(); } catch (e) { /* noop */ }
        // Re-register custom functions
        this._registerCustomFunctions();
        // Reset sheet tracking
        this._sheetMap = {};
        this._sheetNames = [];
      } catch (e) {
        console.warn("[FormulaEngine] HyperFormula rebuild failed:", e);
        return false;
      }

      // Now add the sheet fresh (2.x: addSheet returns the name; getSheetId
      // resolves the numeric id used by setSheetContent / cell addresses).
      var idx;
      try {
        this._hf.addSheet(name);
        idx = this._hf.getSheetId(name);
      } catch (e) {
        try {
          this._hf.addSheet(name + "_1");
          idx = this._hf.getSheetId(name + "_1");
        } catch (e2) {
          console.warn("[FormulaEngine] Cannot create sheet:", name, e2);
          return false;
        }
      }
      idx = Number(idx);
      this._sheetMap[name] = idx;
      this._sheetNames.push(name);

      // Set the content
      if (data2d && data2d.length) {
        try {
          this._hf.setSheetContent(idx, data2d);
        } catch (e) {
          console.warn("[FormulaEngine] setSheetContent failed for", name, e);
        }
      }
      return true;
    },

    // ── Cell read/write ────────────────────────────────────────────
    setCell: function (sheetName, row, col, value) {
      if (!this._hf) return;
      var idx = this._sheetMap[sheetName];
      if (idx == null) idx = this.addSheet(sheetName);
      var addr = { sheet: idx, row: row, col: col };
      try {
        if (typeof value === "string" && value.charAt(0) === "=") {
          this._hf.setCellContents(addr, value);
          this._cellFormulas[sheetName + "!" + toA1(row, col)] = value;
        } else {
          var num = Number(value);
          this._hf.setCellContents(addr, isNaN(num) ? String(value) : num);
          delete this._cellFormulas[sheetName + "!" + toA1(row, col)];
        }
      } catch (e) {
        console.warn("[FormulaEngine] setCell failed:", sheetName, row, col, e);
      }
    },

    getCellValue: function (sheetName, row, col) {
      if (!this._hf) return "";
      var idx = this._sheetMap[sheetName];
      if (idx == null) return "";
      try {
        var val = this._hf.getCellValue({ sheet: idx, row: row, col: col });
        if (val === null || val === undefined) return "";
        if (typeof val === "object") {
          // HyperFormula 2.x error cells are DetailedCellError objects
          // ({ value: "#DIV/0!", type: "DIV_BY_ZERO", ... }).
          if (val.error) return val.error;
          if (val.value != null) return val.value;
          return "";
        }
        return val;
      } catch (e) {
        return "";
      }
    },

    getCellFormula: function (sheetName, row, col) {
      var key = sheetName + "!" + toA1(row, col);
      return this._cellFormulas[key] || "";
    },

    getCellRaw: function (sheetName, row, col) {
      if (!this._hf) return "";
      var idx = this._sheetMap[sheetName];
      if (idx == null) return "";
      try {
        // 2.x renamed getCellRaw → getCellSerialized (returns the raw
        // formula string for formula cells, the value otherwise).
        return this._hf.getCellSerialized({ sheet: idx, row: row, col: col });
      } catch (e) {
        return "";
      }
    },

    // Evaluate a standalone formula (not tied to a cell)
    evaluateFormula: function (formula, sheetName) {
      if (!this._hf || !formula) return formula;
      var sheetIdx = this._sheetMap[sheetName || "Sheet1"];
      if (sheetIdx == null) sheetIdx = 0;
      try {
        var result = this._hf.calculateFormula(formula, sheetIdx);
        if (result && typeof result === "object") {
          if (result.error) return result.error;
          if (result.value != null) return result.value;
          return ERR.NAME;
        }
        return result;
      } catch (e) {
        return ERR.NAME;
      }
    },

    // Get the entire sheet content as 2D array
    getSheetContent: function (sheetName) {
      if (!this._hf) return [];
      var idx = this._sheetMap[sheetName];
      if (idx == null) return [];
      try {
        return this._hf.getSheetValues(idx);
      } catch (e) {
        return [];
      }
    },

    // ── Recalculation ──────────────────────────────────────────────
    recalcSheet: function () {
      // HyperFormula 2.x recalculates automatically on every setCellContents /
      // setSheetContent — a manual recalc step no longer exists (and the old
      // recalculateFormulas() method was removed). Kept for API compatibility.
    },

    // ── Custom function registration ───────────────────────────────
    _registerCustomFunctions: function () {
      var hf = this._hf;
      if (!hf) return;

      var self = this;

      // Helper to register a custom function with HyperFormula
      function reg(name, params, fn) {
        try {
          hf.registerFunction(name, params, fn);
          self._customFunctions[name] = { parameters: params };
        } catch (e) {
          // Function may already be built-in — that's fine
        }
      }

      // ── Additional text functions ──────────────────────────────
      reg("CONCATENATE", [{ argumentType: "ARRAY", repeat: true }], function () {
        var args = Array.prototype.slice.call(arguments);
        var parts = [];
        for (var i = 0; i < args.length; i++) {
          var a = args[i];
          if (Array.isArray(a)) {
            for (var j = 0; j < a.length; j++) {
              parts.push(a[j] != null ? String(a[j]) : "");
            }
          } else {
            parts.push(a != null ? String(a) : "");
          }
        }
        return parts.join("");
      });

      reg("REPT", [{ argumentType: "STRING" }, { argumentType: "NUMBER" }], function (text, count) {
        text = text != null ? String(text) : "";
        count = Math.max(0, Math.floor(Number(count) || 0));
        var s = "";
        for (var i = 0; i < count; i++) s += text;
        return s;
      });

      reg("CLEAN", [{ argumentType: "STRING" }], function (text) {
        return String(text || "").replace(/[\x00-\x1f]/g, "");
      });

      reg("EXACT", [{ argumentType: "STRING" }, { argumentType: "STRING" }], function (a, b) {
        return String(a) === String(b);
      });

      // ── Financial functions ────────────────────────────────────
      reg("PMT", [
        { argumentType: "NUMBER" }, { argumentType: "NUMBER" },
        { argumentType: "NUMBER" }, { argumentType: "NUMBER" }
      ], function (rate, nper, pv, fv, type) {
        fv = fv || 0;
        type = type || 0;
        if (rate === 0) return -(pv + fv) / nper;
        var factor = Math.pow(1 + rate, nper);
        var pmt = rate * (fv + pv * factor) / ((type ? 1 + rate : 1) * (factor - 1));
        return -pmt;
      });

      reg("FV", [
        { argumentType: "NUMBER" }, { argumentType: "NUMBER" },
        { argumentType: "NUMBER" }, { argumentType: "NUMBER" }
      ], function (rate, nper, pmt, pv, type) {
        pv = pv || 0;
        type = type || 0;
        if (rate === 0) return -(pv + pmt * nper);
        var factor = Math.pow(1 + rate, nper);
        return -(pv * factor + pmt * (type ? 1 + rate : 1) * (factor - 1) / rate);
      });

      reg("PV", [
        { argumentType: "NUMBER" }, { argumentType: "NUMBER" },
        { argumentType: "NUMBER" }, { argumentType: "NUMBER" }
      ], function (rate, nper, pmt, fv, type) {
        fv = fv || 0;
        type = type || 0;
        if (rate === 0) return -(pmt * nper + fv);
        var factor = Math.pow(1 + rate, nper);
        return -(fv + pmt * (type ? 1 + rate : 1) * (factor - 1) / rate) / factor;
      });

      // ── Information functions ──────────────────────────────────
      reg("ISBLANK", [{ argumentType: "ANY" }], function (v) {
        return v === null || v === undefined || v === "";
      });

      reg("ISERROR", [{ argumentType: "ANY" }], function (v) {
        if (typeof v === "string" && v.charAt(0) === "#") return true;
        return false;
      });

      reg("ISERR", [{ argumentType: "ANY" }], function (v) {
        if (typeof v === "string" && v.charAt(0) === "#" && v !== "#N/A") return true;
        return false;
      });

      reg("ISNA", [{ argumentType: "ANY" }], function (v) {
        return v === "#N/A" || v === null || v === undefined;
      });

      // ── Statistical functions ──────────────────────────────────
      reg("TRIMMEAN", [
        { argumentType: "ARRAY" }, { argumentType: "NUMBER" }
      ], function (arr, percent) {
        var flat = [];
        if (Array.isArray(arr)) {
          for (var i = 0; i < arr.length; i++) {
            if (Array.isArray(arr[i])) {
              for (var j = 0; j < arr[i].length; j++) {
                var n = Number(arr[i][j]);
                if (!isNaN(n)) flat.push(n);
              }
            } else {
              var n2 = Number(arr[i]);
              if (!isNaN(n2)) flat.push(n2);
            }
          }
        }
        flat.sort(function (a, b) { return a - b; });
        var trim = Math.floor(flat.length * (percent || 0) / 2);
        var trimmed = flat.slice(trim, flat.length - trim);
        if (!trimmed.length) return 0;
        return trimmed.reduce(function (s, v) { return s + v; }, 0) / trimmed.length;
      });

      reg("DEVSQ", [{ argumentType: "ARRAY", repeat: true }], function () {
        var args = Array.prototype.slice.call(arguments);
        var vals = [];
        for (var i = 0; i < args.length; i++) {
          var a = args[i];
          if (Array.isArray(a)) {
            for (var j = 0; j < a.length; j++) {
              var n = Number(a[j]);
              if (!isNaN(n)) vals.push(n);
            }
          } else {
            var n2 = Number(a);
            if (!isNaN(n2)) vals.push(n2);
          }
        }
        if (vals.length < 2) return 0;
        var mean = vals.reduce(function (s, v) { return s + v; }, 0) / vals.length;
        return vals.reduce(function (s, v) { return s + Math.pow(v - mean, 2); }, 0);
      });

      reg("AVEDEV", [{ argumentType: "ARRAY", repeat: true }], function () {
        var args = Array.prototype.slice.call(arguments);
        var vals = [];
        for (var i = 0; i < args.length; i++) {
          var a = args[i];
          if (Array.isArray(a)) {
            for (var j = 0; j < a.length; j++) {
              var n = Number(a[j]);
              if (!isNaN(n)) vals.push(n);
            }
          } else {
            var n2 = Number(a);
            if (!isNaN(n2)) vals.push(n2);
          }
        }
        if (!vals.length) return 0;
        var mean = vals.reduce(function (s, v) { return s + v; }, 0) / vals.length;
        return vals.reduce(function (s, v) { return s + Math.abs(v - mean); }, 0) / vals.length;
      });

      // ── Date functions ─────────────────────────────────────────
      reg("DATEDIF", [
        { argumentType: "STRING" }, { argumentType: "STRING" },
        { argumentType: "STRING" }
      ], function (start, end, unit) {
        var s = new Date(start);
        var e = new Date(end);
        if (isNaN(s) || isNaN(e)) return ERR.VALUE;
        var diff = e.getTime() - s.getTime();
        unit = String(unit).toUpperCase();
        if (unit === "D") return Math.floor(diff / 86400000);
        if (unit === "M") return (e.getFullYear() - s.getFullYear()) * 12 + (e.getMonth() - s.getMonth());
        if (unit === "Y") return Math.floor(((e.getFullYear() - s.getFullYear()) * 12 + (e.getMonth() - s.getMonth())) / 12);
        if (unit === "MD") return Math.abs(e.getDate() - s.getDate());
        if (unit === "YM") return Math.abs((e.getMonth() - s.getMonth()) + 12 * (e.getFullYear() - s.getFullYear()));
        if (unit === "YD") {
          var s2 = new Date(s.getFullYear(), e.getMonth(), e.getDate());
          return Math.floor(Math.abs(e - s2) / 86400000);
        }
        return ERR.VALUE;
      });

      // ── LOG10 ──────────────────────────────────────────────────
      reg("LOG10", [{ argumentType: "NUMBER" }], function (v) {
        var n = Number(v);
        if (n <= 0) return ERR.NUM;
        return Math.log10(n);
      });

      // ── EOMONTH ────────────────────────────────────────────────
      reg("EOMONTH", [
        { argumentType: "STRING" }, { argumentType: "NUMBER" }
      ], function (start, months) {
        var d = new Date(start);
        if (isNaN(d)) return ERR.VALUE;
        d.setMonth(d.getMonth() + Math.floor(Number(months)));
        d.setDate(0); // last day of month
        return d.toISOString().slice(0, 10);
      });

      // ── YEARFRAC ───────────────────────────────────────────────
      reg("YEARFRAC", [
        { argumentType: "STRING" }, { argumentType: "STRING" },
        { argumentType: "NUMBER" }
      ], function (start, end, basis) {
        var s = new Date(start);
        var e = new Date(end);
        if (isNaN(s) || isNaN(e)) return ERR.VALUE;
        var diffDays = Math.abs(e - s) / 86400000;
        basis = Math.floor(Number(basis) || 0);
        if (basis === 0) {
          // US 30/360
          var d1 = s.getDate(), d2 = e.getDate();
          var m1 = s.getMonth(), m2 = e.getMonth();
          var y1 = s.getFullYear(), y2 = e.getFullYear();
          if (d1 === 31 && d2 === 31) d2 = 30;
          if (d1 === 31) d1 = 30;
          if (d2 === 31 && d1 === 30) d2 = 30;
          return ((y2 - y1) * 360 + (m2 - m1) * 30 + (d2 - d1)) / 360;
        }
        if (basis === 1) {
          // Actual/actual
          return diffDays / 365.25;
        }
        if (basis === 3) {
          return diffDays / 365;
        }
        return diffDays / 365.25;
      });

      // ── SUMPRODUCT (more flexible) ─────────────────────────────
      reg("SUMPRODUCT", [{ argumentType: "ARRAY", repeat: true }], function () {
        var args = Array.prototype.slice.call(arguments);
        if (!args.length || !args[0] || !args[0].length) return 0;
        var len = 0;
        for (var i = 0; i < args.length; i++) {
          if (Array.isArray(args[i]) && args[i].length > len) len = args[i].length;
        }
        var sum = 0;
        for (var j = 0; j < len; j++) {
          var prod = 1;
          for (var k = 0; k < args.length; k++) {
            var v = Array.isArray(args[k]) ? args[k][j] : args[k];
            prod *= Number(v) || 0;
          }
          sum += prod;
        }
        return sum;
      });
    },

    // ── Get function list for autocomplete ─────────────────────────
    getFunctions: function () {
      return [
        // Math & Trigonometry
        { name: "SUM",            category: "Math",       params: "number1, [number2...]",   desc: "Adds all the numbers in a range" },
        { name: "SUMIF",          category: "Math",       params: "range, criteria, [sum_range]", desc: "Adds cells that meet a condition" },
        { name: "SUMIFS",         category: "Math",       params: "sum_range, range1, criteria1, ...", desc: "Adds cells that meet multiple conditions" },
        { name: "SUMPRODUCT",     category: "Math",       params: "array1, [array2...]",      desc: "Multiplies and sums corresponding components" },
        { name: "AVERAGE",        category: "Math",       params: "number1, [number2...]",    desc: "Returns the average of the arguments" },
        { name: "AVERAGEIF",      category: "Math",       params: "range, criteria, [avg_range]", desc: "Average of cells meeting a condition" },
        { name: "AVERAGEIFS",     category: "Math",       params: "avg_range, range1, criteria1, ...", desc: "Average of cells meeting multiple conditions" },
        { name: "COUNT",          category: "Math",       params: "value1, [value2...]",      desc: "Counts cells containing numbers" },
        { name: "COUNTA",         category: "Math",       params: "value1, [value2...]",      desc: "Counts non-empty cells" },
        { name: "COUNTIF",        category: "Math",       params: "range, criteria",           desc: "Counts cells meeting a condition" },
        { name: "COUNTIFS",       category: "Math",       params: "range1, criteria1, ...",   desc: "Counts cells meeting multiple conditions" },
        { name: "MAX",            category: "Math",       params: "number1, [number2...]",    desc: "Returns the largest value" },
        { name: "MIN",            category: "Math",       params: "number1, [number2...]",    desc: "Returns the smallest value" },
        { name: "ABS",            category: "Math",       params: "number",                    desc: "Returns the absolute value" },
        { name: "ROUND",          category: "Math",       params: "number, num_digits",        desc: "Rounds a number to specified digits" },
        { name: "ROUNDUP",        category: "Math",       params: "number, num_digits",        desc: "Rounds up (away from zero)" },
        { name: "ROUNDDOWN",      category: "Math",       params: "number, num_digits",        desc: "Rounds down (toward zero)" },
        { name: "SQRT",           category: "Math",       params: "number",                    desc: "Returns the square root" },
        { name: "POWER",          category: "Math",       params: "number, power",             desc: "Returns a number raised to a power" },
        { name: "MOD",            category: "Math",       params: "number, divisor",           desc: "Returns the remainder after division" },
        { name: "INT",            category: "Math",       params: "number",                    desc: "Rounds down to the nearest integer" },
        { name: "CEILING",        category: "Math",       params: "number, significance",      desc: "Rounds up to nearest multiple" },
        { name: "FLOOR",          category: "Math",       params: "number, significance",      desc: "Rounds down to nearest multiple" },
        { name: "SIGN",           category: "Math",       params: "number",                    desc: "Returns sign of a number (+1, -1, 0)" },
        { name: "RAND",           category: "Math",       params: "",                          desc: "Returns a random number between 0 and 1" },
        { name: "RANDBETWEEN",    category: "Math",       params: "bottom, top",               desc: "Returns a random integer between two numbers" },
        { name: "PI",             category: "Math",       params: "",                          desc: "Returns the value of PI" },
        { name: "TRUNC",          category: "Math",       params: "number, [num_digits]",      desc: "Truncates a number to an integer" },
        { name: "EXP",            category: "Math",       params: "number",                    desc: "Returns e raised to a power" },
        { name: "LN",             category: "Math",       params: "number",                    desc: "Returns the natural logarithm" },
        { name: "LOG",            category: "Math",       params: "number, [base]",            desc: "Returns the logarithm" },
        { name: "LOG10",          category: "Math",       params: "number",                    desc: "Returns the base-10 logarithm" },
        { name: "FACT",           category: "Math",       params: "number",                    desc: "Returns the factorial" },
        { name: "SUMPRODUCT",     category: "Math",       params: "array1, [array2...]",       desc: "Multiplies arrays element-wise and sums" },

        // Logical
        { name: "IF",             category: "Logical",    params: "logical_test, value_if_true, value_if_false", desc: "Returns one value if TRUE, another if FALSE" },
        { name: "AND",            category: "Logical",    params: "logical1, [logical2...]",   desc: "Returns TRUE if all arguments are TRUE" },
        { name: "OR",             category: "Logical",    params: "logical1, [logical2...]",   desc: "Returns TRUE if any argument is TRUE" },
        { name: "NOT",            category: "Logical",    params: "logical",                   desc: "Reverses the logical value" },
        { name: "IFERROR",        category: "Logical",    params: "value, value_if_error",     desc: "Returns value_if_error if expression is an error" },
        { name: "IFNA",           category: "Logical",    params: "value, value_if_na",        desc: "Returns value_if_na if expression is #N/A" },
        { name: "TRUE",           category: "Logical",    params: "",                          desc: "Returns the logical value TRUE" },
        { name: "FALSE",          category: "Logical",    params: "",                          desc: "Returns the logical value FALSE" },
        { name: "IFS",            category: "Logical",    params: "condition1, value1, ...",   desc: "Checks whether conditions are met and returns a value" },
        { name: "SWITCH",         category: "Logical",    params: "expression, val1, result1, ...", desc: "Compares expression to values and returns matching result" },

        // Text
        { name: "LEN",            category: "Text",       params: "text",                      desc: "Returns the number of characters" },
        { name: "LEFT",           category: "Text",       params: "text, [num_chars]",         desc: "Returns characters from the left" },
        { name: "RIGHT",          category: "Text",       params: "text, [num_chars]",         desc: "Returns characters from the right" },
        { name: "MID",            category: "Text",       params: "text, start_num, num_chars", desc: "Returns characters from the middle" },
        { name: "FIND",           category: "Text",       params: "find_text, within_text, [start_num]", desc: "Finds one text within another (case-sensitive)" },
        { name: "SEARCH",         category: "Text",       params: "find_text, within_text, [start_num]", desc: "Finds one text within another (case-insensitive)" },
        { name: "REPLACE",        category: "Text",       params: "old_text, start_num, num_chars, new_text", desc: "Replaces part of a text string" },
        { name: "SUBSTITUTE",     category: "Text",       params: "text, old_text, new_text, [instance_num]", desc: "Substitutes new text for old" },
        { name: "CONCATENATE",    category: "Text",       params: "text1, [text2...]",         desc: "Joins text strings together" },
        { name: "TRIM",           category: "Text",       params: "text",                      desc: "Removes extra spaces from text" },
        { name: "UPPER",          category: "Text",       params: "text",                      desc: "Converts text to uppercase" },
        { name: "LOWER",          category: "Text",       params: "text",                      desc: "Converts text to lowercase" },
        { name: "PROPER",         category: "Text",       params: "text",                      desc: "Capitalizes first letter of each word" },
        { name: "TEXT",           category: "Text",       params: "value, format_text",        desc: "Formats a number as text" },
        { name: "VALUE",          category: "Text",       params: "text",                      desc: "Converts text to a number" },
        { name: "REPT",           category: "Text",       params: "text, number_times",        desc: "Repeats text a given number of times" },
        { name: "CLEAN",          category: "Text",       params: "text",                      desc: "Removes non-printable characters" },
        { name: "EXACT",          category: "Text",       params: "text1, text2",              desc: "Checks if two text strings are exactly the same" },
        { name: "T",              category: "Text",       params: "value",                     desc: "Returns text if value is text" },
        { name: "N",              category: "Text",       params: "value",                     desc: "Returns a number converted from a value" },

        // Date & Time
        { name: "TODAY",          category: "Date",       params: "",                          desc: "Returns today's date" },
        { name: "NOW",            category: "Date",       params: "",                          desc: "Returns current date and time" },
        { name: "YEAR",           category: "Date",       params: "serial_number",             desc: "Returns the year from a date" },
        { name: "MONTH",          category: "Date",       params: "serial_number",             desc: "Returns the month from a date" },
        { name: "DAY",            category: "Date",       params: "serial_number",             desc: "Returns the day of the month" },
        { name: "HOUR",           category: "Date",       params: "serial_number",             desc: "Returns the hour from a time" },
        { name: "MINUTE",         category: "Date",       params: "serial_number",             desc: "Returns the minute from a time" },
        { name: "SECOND",         category: "Date",       params: "serial_number",             desc: "Returns the second from a time" },
        { name: "DATE",           category: "Date",       params: "year, month, day",          desc: "Creates a date from individual values" },
        { name: "DATEVALUE",      category: "Date",       params: "date_text",                 desc: "Converts a date text to serial number" },
        { name: "TIME",           category: "Date",       params: "hour, minute, second",      desc: "Creates a time from individual values" },
        { name: "WEEKDAY",        category: "Date",       params: "serial_number, [return_type]", desc: "Returns the day of the week" },
        { name: "WEEKNUM",        category: "Date",       params: "serial_number, [return_type]", desc: "Returns the week number of the year" },
        { name: "EOMONTH",        category: "Date",       params: "start_date, months",        desc: "Returns the last day of a month" },
        { name: "DATEDIF",        category: "Date",       params: "start_date, end_date, unit", desc: "Returns the difference between dates" },
        { name: "YEARFRAC",       category: "Date",       params: "start_date, end_date, [basis]", desc: "Returns the fraction of the year between dates" },

        // Lookup & Reference
        { name: "VLOOKUP",        category: "Lookup",     params: "lookup_value, table_array, col_index, [range_lookup]", desc: "Vertical lookup — searches first column and returns value from another column" },
        { name: "HLOOKUP",        category: "Lookup",     params: "lookup_value, table_array, row_index, [range_lookup]", desc: "Horizontal lookup — searches first row and returns value from another row" },
        { name: "INDEX",          category: "Lookup",     params: "array, row_num, [col_num]", desc: "Returns value at a given position in a range" },
        { name: "MATCH",          category: "Lookup",     params: "lookup_value, lookup_array, [match_type]", desc: "Returns relative position of an item" },
        { name: "CHOOSE",         category: "Lookup",     params: "index_num, value1, [value2...]", desc: "Returns a value from a list based on index" },
        { name: "OFFSET",         category: "Lookup",     params: "reference, rows, cols, [height], [width]", desc: "Returns a reference offset from a starting point" },
        { name: "INDIRECT",       category: "Lookup",     params: "ref_text",                  desc: "Returns the reference specified by a text string" },
        { name: "ADDRESS",        category: "Lookup",     params: "row_num, column_num, [abs_num], [a1], [sheet_text]", desc: "Returns a cell address as text" },
        { name: "COLUMN",         category: "Lookup",     params: "[reference]",               desc: "Returns the column number" },
        { name: "COLUMNS",        category: "Lookup",     params: "reference",                 desc: "Returns the number of columns in a range" },
        { name: "ROW",            category: "Lookup",     params: "[reference]",               desc: "Returns the row number" },
        { name: "ROWS",           category: "Lookup",     params: "reference",                 desc: "Returns the number of rows in a range" },
        { name: "TYPE",           category: "Lookup",     params: "value",                     desc: "Returns a number indicating the type of data" },

        // Statistical
        { name: "MEDIAN",         category: "Statistical", params: "number1, [number2...]",    desc: "Returns the median (middle value)" },
        { name: "MODE",           category: "Statistical", params: "number1, [number2...]",    desc: "Returns the most frequently occurring value" },
        { name: "STDEV",          category: "Statistical", params: "number1, [number2...]",    desc: "Returns standard deviation based on a sample" },
        { name: "STDEVP",         category: "Statistical", params: "number1, [number2...]",    desc: "Returns standard deviation based on the population" },
        { name: "VAR",            category: "Statistical", params: "number1, [number2...]",    desc: "Returns variance based on a sample" },
        { name: "VARP",           category: "Statistical", params: "number1, [number2...]",    desc: "Returns variance based on the entire population" },
        { name: "PERCENTILE",     category: "Statistical", params: "array, k",                 desc: "Returns the k-th percentile of values" },
        { name: "QUARTILE",       category: "Statistical", params: "array, quart",             desc: "Returns the quartile of values" },
        { name: "RANK",           category: "Statistical", params: "number, ref, [order]",     desc: "Returns the rank of a number in a list" },
        { name: "LARGE",          category: "Statistical", params: "array, k",                 desc: "Returns the k-th largest value" },
        { name: "SMALL",          category: "Statistical", params: "array, k",                 desc: "Returns the k-th smallest value" },
        { name: "COUNTBLANK",     category: "Statistical", params: "range",                    desc: "Counts empty cells in a range" },
        { name: "TRIMMEAN",       category: "Statistical", params: "array, percent",           desc: "Returns mean excluding outliers" },
        { name: "DEVSQ",          category: "Statistical", params: "number1, [number2...]",    desc: "Returns sum of squared deviations" },
        { name: "AVEDEV",         category: "Statistical", params: "number1, [number2...]",    desc: "Returns average of absolute deviations" },

        // Financial
        { name: "PV",             category: "Financial",  params: "rate, nper, pmt, [fv], [type]", desc: "Returns the present value" },
        { name: "FV",             category: "Financial",  params: "rate, nper, pmt, [pv], [type]", desc: "Returns the future value" },
        { name: "PMT",            category: "Financial",  params: "rate, nper, pv, [fv], [type]", desc: "Returns the periodic payment" },

        // Information
        { name: "ISBLANK",        category: "Information", params: "value",                   desc: "Checks if cell is empty" },
        { name: "ISERROR",        category: "Information", params: "value",                   desc: "Checks if value is any error" },
        { name: "ISERR",          category: "Information", params: "value",                   desc: "Checks if value is an error (except #N/A)" },
        { name: "ISNA",           category: "Information", params: "value",                   desc: "Checks if value is #N/A" },
        { name: "ISTEXT",         category: "Information", params: "value",                   desc: "Checks if value is text" },
        { name: "ISNONTEXT",      category: "Information", params: "value",                   desc: "Checks if value is not text" },
        { name: "ISNUMBER",       category: "Information", params: "value",                   desc: "Checks if value is a number" },
        { name: "ISLOGICAL",      category: "Information", params: "value",                   desc: "Checks if value is a logical value" },
        { name: "ISEVEN",         category: "Information", params: "number",                  desc: "Checks if number is even" },
        { name: "ISODD",          category: "Information", params: "number",                  desc: "Checks if number is odd" },
      ];
    },

    // ── Utility: flatten arguments for aggregate functions ──────────
    flattenArgs: function (args) {
      var vals = [];
      for (var i = 0; i < args.length; i++) {
        var a = args[i];
        if (Array.isArray(a)) {
          for (var j = 0; j < a.length; j++) {
            if (Array.isArray(a[j])) {
              for (var k = 0; k < a[j].length; k++) {
                vals.push(a[j][k]);
              }
            } else {
              vals.push(a[j]);
            }
          }
        } else {
          vals.push(a);
        }
      }
      return vals;
    },

    // ── Status check ───────────────────────────────────────────────
    isAvailable: function () {
      return !!this._hf;
    },
  };
})();
