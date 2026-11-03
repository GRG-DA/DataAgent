(function() {
  "use strict";

  var _clipboard = null;
  var _PRESETS_KEY = "bd_widget_presets";
  var _MAX_PRESETS = 20;
  var _THEME_KEY = "bd_dashboard_theme";
  var _CUSTOM_THEMES_KEY = "bd_dashboard_custom_themes";

  var _DEFAULT_THEME = {
    name: "Default",
    mode: "light",
    primary: "#2563eb",
    secondary: "#0d9488",
    fontFamily: "Inter",
    borderRadius: 12,
    cardRadius: 12,
    shadow: "0 4px 15px rgba(0,0,0,0.08)",
    background: "#f8fafc",
    surface: "#ffffff",
    text: "#1e293b",
    textMuted: "#64748b",
    positive: "#10B981",
    negative: "#b91c1c",
    warning: "#b45309"
  };

  var _BUILTIN_THEMES = [
    {
      name: "Light",
      mode: "light",
      primary: "#2563eb",
      secondary: "#0d9488",
      fontFamily: "Inter",
      borderRadius: 12,
      cardRadius: 12,
      shadow: "0 4px 15px rgba(0,0,0,0.08)",
      background: "#f8fafc",
      surface: "#ffffff",
      text: "#1e293b",
      textMuted: "#64748b",
      positive: "#10B981",
      negative: "#b91c1c",
      warning: "#b45309"
    },
    {
      name: "Dark",
      mode: "dark",
      primary: "#60a5fa",
      secondary: "#2dd4bf",
      fontFamily: "Inter",
      borderRadius: 12,
      cardRadius: 12,
      shadow: "0 4px 15px rgba(0,0,0,0.3)",
      background: "#0b1120",
      surface: "#1e293b",
      text: "#e2e8f0",
      textMuted: "#94a3b8",
      positive: "#10B981",
      negative: "#b91c1c",
      warning: "#b45309"
    },
    {
      name: "Midnight Blue",
      mode: "dark",
      primary: "#60A5FA",
      secondary: "#818CF8",
      fontFamily: "Inter",
      borderRadius: 12,
      cardRadius: 12,
      shadow: "0 4px 15px rgba(0,0,0,0.3)",
      background: "#0f172a",
      surface: "#1e293b",
      text: "#e2e8f0",
      textMuted: "#94a3b8",
      positive: "#10B981",
      negative: "#b91c1c",
      warning: "#b45309"
    },
    {
      name: "Clean White",
      mode: "light",
      primary: "#4F46E5",
      secondary: "#7C3AED",
      fontFamily: "Inter",
      borderRadius: 12,
      cardRadius: 12,
      shadow: "0 2px 8px rgba(0,0,0,0.06)",
      background: "#ffffff",
      surface: "#f8fafc",
      text: "#0f172a",
      textMuted: "#64748b",
      positive: "#10B981",
      negative: "#b91c1c",
      warning: "#b45309"
    },
    {
      name: "Glass Dark",
      mode: "dark",
      primary: "#A78BFA",
      secondary: "#60A5FA",
      fontFamily: "Inter",
      borderRadius: 12,
      cardRadius: 12,
      shadow: "0 4px 15px rgba(0,0,0,0.2)",
      background: "rgba(15,23,42,0.95)",
      surface: "rgba(30,41,59,0.8)",
      text: "#e2e8f0",
      textMuted: "#94a3b8",
      positive: "#10B981",
      negative: "#b91c1c",
      warning: "#b45309"
    }
  ];

  function _toast(msg) {
    var el = document.createElement("div");
    el.className = "bd-toast";
    el.textContent = msg;
    el.style.cssText = "position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1e293b;color:#fff;padding:8px 20px;border-radius:8px;font-size:12px;z-index:99999;box-shadow:0 4px 12px rgba(0,0,0,0.2);animation:toast-in 0.2s ease;";
    document.body.appendChild(el);
    setTimeout(function() {
      el.style.opacity = "0";
      el.style.transition = "opacity 0.3s";
      setTimeout(function() { el.remove(); }, 300);
    }, 2000);
  }

  /* ── Scoped theme application ───────────────────────────────────────────
     A preset theme is a WIDGET theme, not an app theme. It used to be
     written straight onto <html> as inline custom properties. Inline
     styles on <html> beat every stylesheet rule, so the light/dark blocks in
     web/css/styles.css went dead the moment a preset was applied —
     --color-content-bg stayed pinned to the light value even with
     data-theme="dark" set.

     The tokens are now written onto the widget grid the preset belongs to
     (the canvas that holds the .ff-widget cards), using the SAME token
     names, so the scoped CSS picks them up. This is exactly the pattern
     .ff-widget.bd-themed uses in web/css/styles.css: that rule re-declares
     --color-content-bg / --color-content-text / --color-content-text-secondary
     / --color-primary / --color-card-bg locally on the card, so a card can
     be tinted without touching the app chrome. The app shell (topnav,
     sidebar, page background) keeps the stylesheet tokens and the app-wide
     light/dark toggle keeps working.

     The scope is deliberately NOT .bd-canvas-wrap / .bd-page: the Dashboard
     Studio theme picker (BlankDash._syncDashboardTheme) owns those elements.
     A token the studio has claimed there wins over the preset. */
  function _themeScope() {
    /* The widget grid — canvas + the cards inside it. */
    return document.getElementById("bd-canvas") || document.querySelector(".bd-canvas");
  }

  /* Every token this module may write, so a re-apply clears stale values.
     These are WIDGET-scoped names only. Writing an app-wide token here
     (e.g. --radius-md, which 80 rules read, or --color-warning, which is a
     RESERVED status colour) would restyle the whole canvas — and would
     repurpose a status colour as decoration. The widget's own surface/text
     tokens are the ones a dashboard theme is allowed to own. */
  var _THEME_TOKENS = [
    "--color-content-bg", "--color-card-bg", "--color-content-text",
    "--color-content-text-secondary", "--color-primary", "--color-secondary",
    "--bd-radius", "--radius-card", "--shadow-card", "--font-family",
    "--color-positive", "--color-negative", "--bd-kpi-warning"
  ];

  var _pendingTheme = null;    /* theme waiting for a scope element to exist */
  var _appliedScope = null;    /* scope element the theme is currently on    */
  var _scopeObserver = null;   /* watches for the scope element appearing    */
  var _studioWrap = null;      /* the canvas wrapper the studio themes      */
  var _studioObserver = null;  /* watches the studio theme for changes       */

  function _applyThemeTo(el, theme) {
    if (!el || !el.style || !theme) return;
    var appDark = localStorage.getItem("darkMode") === "true";
    var themeDark = theme.mode === "dark";
    var applySurfaces = themeDark === appDark;
    var style = el.style;
    /* Clear whatever a previous theme wrote so switching themes cannot
       leave stale values behind. */
    for (var i = 0; i < _THEME_TOKENS.length; i++) style.removeProperty(_THEME_TOKENS[i]);
    /* The Dashboard Studio theme picker (BlankDash._syncDashboardTheme)
       writes the same token names on the canvas wrapper. A token the studio
       has claimed there wins, so the two systems do not fight over it. */
    var wrap = el.closest ? el.closest(".bd-canvas-wrap") : null;
    function set(name, value) {
      if (wrap && wrap.style && wrap.style.getPropertyValue(name)) return;
      style.setProperty(name, value);
    }
    if (theme.primary) set("--color-primary", theme.primary);
    if (theme.secondary) set("--color-secondary", theme.secondary);
    /* Surfaces follow the app's own light/dark mode: when the preset mode and
       the app mode disagree, the app theme keeps ownership of the surfaces. */
    if (applySurfaces) {
      if (theme.background) set("--color-content-bg", theme.background);
      if (theme.surface) set("--color-card-bg", theme.surface);
      if (theme.text) set("--color-content-text", theme.text);
      if (theme.textMuted) set("--color-content-text-secondary", theme.textMuted);
    }
    if (theme.borderRadius != null) set("--bd-radius", theme.borderRadius + "px");
    if (theme.cardRadius != null) set("--radius-card", theme.cardRadius + "px");
    if (theme.shadow) set("--shadow-card", theme.shadow);
    if (theme.fontFamily) set("--font-family", theme.fontFamily);
    /* positive/negative/warning keep their meaning: the KPI good/bad states
       read these token names on the widget scope. The warning uses the
       widget-scoped name so the RESERVED app status colour is untouched. */
    if (theme.positive) set("--color-positive", theme.positive);
    if (theme.negative) set("--color-negative", theme.negative);
    if (theme.warning) set("--bd-kpi-warning", theme.warning);
  }

  function _watchStudioTheme() {
    var wrap = document.querySelector(".bd-canvas-wrap");
    if (!wrap || wrap === _studioWrap) return;
    if (_studioObserver) { _studioObserver.disconnect(); _studioObserver = null; }
    _studioWrap = wrap;
    if (typeof MutationObserver === "undefined") return;
    /* The studio theme picker re-writes the wrapper's inline style when the
       user picks a theme; re-apply the preset so the two stay in sync. */
    _studioObserver = new MutationObserver(function () {
      if (!_pendingTheme) return;
      var scope = _themeScope();
      if (scope) { _applyThemeTo(scope, _pendingTheme); _appliedScope = scope; }
    });
    _studioObserver.observe(wrap, { attributes: true, attributeFilter: ["style"] });
  }

  function _watchScope() {
    if (_scopeObserver || typeof MutationObserver === "undefined") return;
    /* The canvas is re-created whenever the dashboard page re-renders, so
       re-apply the theme when the page content is swapped. Observing the
       page container (not the whole document) keeps this cheap. */
    var target = document.getElementById("page-content") || document.body;
    if (!target) return;
    _scopeObserver = new MutationObserver(function () {
      if (!_pendingTheme) {
        _scopeObserver.disconnect();
        _scopeObserver = null;
        return;
      }
      var scope = _themeScope();
      if (scope !== _appliedScope) {
        if (scope) { _applyThemeTo(scope, _pendingTheme); _appliedScope = scope; }
        else _appliedScope = null;
      }
      _watchStudioTheme();
    });
    _scopeObserver.observe(target, { childList: true });
  }

  function _applyTheme(theme) {
    if (!theme) return;
    /* Remember it: if the widget grid does not exist yet the theme is
       applied as soon as it appears. It is NEVER written to the document
       root — that would kill the app-wide light/dark theme. */
    _pendingTheme = theme;
    _watchScope();
    var scope = _themeScope();
    if (scope) {
      _applyThemeTo(scope, theme);
      _appliedScope = scope;
    }
    _watchStudioTheme();
  }

  function _deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function _generateId() {
    return "preset_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 6);
  }

  function _loadPresets() {
    try {
      var raw = localStorage.getItem(_PRESETS_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function _savePresetsList(presets) {
    try {
      localStorage.setItem(_PRESETS_KEY, JSON.stringify(presets));
    } catch (e) {
      console.warn("[WidgetPresets] Failed to save presets:", e);
    }
  }

  function _loadTheme() {
    try {
      var raw = localStorage.getItem(_THEME_KEY);
      if (raw) {
        var saved = JSON.parse(raw);
        var merged = {};
        Object.keys(_DEFAULT_THEME).forEach(function(k) {
          merged[k] = saved[k] !== undefined ? saved[k] : _DEFAULT_THEME[k];
        });
        return merged;
      }
    } catch (e) {}
    return _deepClone(_DEFAULT_THEME);
  }

  /* True only when the user has actually picked/saved a theme. With no saved
     theme the app must fall back to the stylesheet tokens in
     web/css/styles.css (:root / [data-theme="dark"]) — the built-in default
     is a template for the theme picker, not a theme that should be applied. */
  function _hasSavedTheme() {
    try {
      return !!localStorage.getItem(_THEME_KEY);
    } catch (e) {
      return false;
    }
  }

  function _saveTheme(theme) {
    try {
      localStorage.setItem(_THEME_KEY, JSON.stringify(theme));
    } catch (e) {
      console.warn("[WidgetPresets] Failed to save theme:", e);
    }
  }

  function _loadCustomThemes() {
    try {
      var raw = localStorage.getItem(_CUSTOM_THEMES_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function _saveCustomThemesList(themes) {
    try {
      localStorage.setItem(_CUSTOM_THEMES_KEY, JSON.stringify(themes));
    } catch (e) {
      console.warn("[WidgetPresets] Failed to save custom themes:", e);
    }
  }

  window.WidgetPresets = {

    _stylePayload: function(widget) {
      var c = widget.config || {};
      return {
        configStyle: _deepClone(c.style || null),
        color: c.color != null ? c.color : null,
        kpiStyle: c.kpiStyle != null ? c.kpiStyle : null,
        prefix: c.prefix != null ? c.prefix : null,
        suffix: c.suffix != null ? c.suffix : null,
        decimals: c.decimals != null ? c.decimals : null,
        abbrev: c.abbrev != null ? c.abbrev : null,
        format: c.format != null ? c.format : null,
        target: c.target != null ? c.target : null,
        bgColor: widget.bgColor,
        borderColor: widget.borderColor
      };
    },

    _applyStylePayload: function(widget, payload) {
      if (!widget || !payload) return;
      widget.config = widget.config || {};
      var s = payload.style || payload;  // accept both wrap forms
      if (s.configStyle) widget.config.style = _deepClone(s.configStyle);
      ['color', 'kpiStyle', 'prefix', 'suffix', 'decimals', 'abbrev', 'format', 'target'].forEach(function(k) {
        if (s[k] != null) widget.config[k] = s[k]; else delete widget.config[k];
      });
      if (s.bgColor != null) widget.bgColor = s.bgColor;
      if (s.borderColor != null) widget.borderColor = s.borderColor;
    },

    copyStyle: function(widget) {
      if (!widget) { _toast("No widget selected"); return; }
      _clipboard = {
        type: widget.type,
        style: this._stylePayload(widget)
      };
      _toast("Style copied");
    },

    pasteStyle: function(widget) {
      if (!_clipboard) { _toast("No style to paste"); return; }
      if (!widget) { _toast("No widget selected"); return; }
      this._applyStylePayload(widget, _clipboard);
      _toast("Style pasted");
      if (window.BlankDash && BlankDash._onWidgetEdited) BlankDash._onWidgetEdited(widget.id);
    },

    hasStyle: function() {
      return _clipboard !== null;
    },

    savePreset: function(widget, name) {
      if (!widget) { _toast("No widget selected"); return null; }
      if (!name || !name.trim()) { _toast("Enter a preset name"); return null; }
      var presets = _loadPresets();
      if (presets.length >= _MAX_PRESETS) {
        _toast("Max presets reached (" + _MAX_PRESETS + ")");
        return null;
      }
      var preset = {
        id: _generateId(),
        name: name.trim(),
        type: widget.type,
        createdAt: Date.now(),
        style: this._stylePayload(widget)
      };
      presets.push(preset);
      _savePresetsList(presets);
      _toast("Preset saved: " + preset.name);
      return preset;
    },

    getPresets: function(widgetType) {
      var presets = _loadPresets();
      if (widgetType) {
        return presets.filter(function(p) { return p.type === widgetType; });
      }
      return presets;
    },

    applyPreset: function(widget, presetId) {
      if (!widget) { _toast("No widget selected"); return; }
      var presets = _loadPresets();
      var preset = null;
      for (var i = 0; i < presets.length; i++) {
        if (presets[i].id === presetId) { preset = presets[i]; break; }
      }
      if (!preset) { _toast("Preset not found"); return; }
      this._applyStylePayload(widget, preset);
      _toast("Applied: " + preset.name);
      if (window.BlankDash && BlankDash._onWidgetEdited) BlankDash._onWidgetEdited(widget.id);
    },

    deletePreset: function(presetId) {
      var presets = _loadPresets();
      var found = false;
      for (var i = 0; i < presets.length; i++) {
        if (presets[i].id === presetId) {
          presets.splice(i, 1);
          found = true;
          break;
        }
      }
      if (found) {
        _savePresetsList(presets);
        _toast("Preset deleted");
      } else {
        _toast("Preset not found");
      }
    },

    renamePreset: function(presetId, newName) {
      if (!newName || !newName.trim()) { _toast("Enter a name"); return; }
      var presets = _loadPresets();
      for (var i = 0; i < presets.length; i++) {
        if (presets[i].id === presetId) {
          presets[i].name = newName.trim();
          _savePresetsList(presets);
          _toast("Renamed to " + newName.trim());
          return;
        }
      }
      _toast("Preset not found");
    },

    getTheme: function() {
      return _loadTheme();
    },

    setTheme: function(theme) {
      if (!theme) return;
      var current = _loadTheme();
      var merged = {};
      Object.keys(_DEFAULT_THEME).forEach(function(k) {
        merged[k] = theme[k] !== undefined ? theme[k] : current[k];
      });
      _saveTheme(merged);
      _applyTheme(merged);
      _toast("Theme applied: " + (merged.name || "Custom"));
    },

    getBuiltinThemes: function() {
      return _BUILTIN_THEMES;
    },

    saveCustomTheme: function(theme) {
      if (!theme) return null;
      var customThemes = _loadCustomThemes();
      var entry = _deepClone(theme);
      entry.id = _generateId();
      entry.custom = true;
      entry.createdAt = Date.now();
      if (!entry.name) entry.name = "Custom Theme";
      customThemes.push(entry);
      _saveCustomThemesList(customThemes);
      _toast("Theme saved: " + entry.name);
      return entry;
    },

    getCustomThemes: function() {
      return _loadCustomThemes();
    }
  };

  /* Boot: only apply a preset theme when the user actually saved one.
     With no saved theme the app must start from the stylesheet tokens in
     web/css/styles.css (:root / [data-theme="dark"]) — the built-in default
     is only a template for the theme picker, and stamping it globally used
     to bake --color-primary: #2563eb etc. onto <html> at page load, which
     killed the app-wide light/dark theme. */
  if (_hasSavedTheme()) _applyTheme(_loadTheme());

  /* Re-apply on an app-wide light/dark switch: the preset's surface tokens
     only apply while the preset mode matches the app mode. */
  new MutationObserver(function() {
    if (_hasSavedTheme()) _applyTheme(_loadTheme());
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

})();
