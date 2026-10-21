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
    primary: "#7B61FF",
    secondary: "#3B82F6",
    fontFamily: "Inter",
    borderRadius: 12,
    cardRadius: 12,
    shadow: "0 4px 15px rgba(0,0,0,0.08)",
    background: "#f8fafc",
    surface: "#ffffff",
    text: "#1e293b",
    textMuted: "#64748b",
    positive: "#10B981",
    negative: "#EF4444",
    warning: "#F59E0B"
  };

  var _BUILTIN_THEMES = [
    {
      name: "Light",
      mode: "light",
      primary: "#7B61FF",
      secondary: "#3B82F6",
      fontFamily: "Inter",
      borderRadius: 12,
      cardRadius: 12,
      shadow: "0 4px 15px rgba(0,0,0,0.08)",
      background: "#f8fafc",
      surface: "#ffffff",
      text: "#1e293b",
      textMuted: "#64748b",
      positive: "#10B981",
      negative: "#EF4444",
      warning: "#F59E0B"
    },
    {
      name: "Dark",
      mode: "dark",
      primary: "#7B61FF",
      secondary: "#3B82F6",
      fontFamily: "Inter",
      borderRadius: 12,
      cardRadius: 12,
      shadow: "0 4px 15px rgba(0,0,0,0.3)",
      background: "#0b1120",
      surface: "#1e293b",
      text: "#e2e8f0",
      textMuted: "#94a3b8",
      positive: "#10B981",
      negative: "#EF4444",
      warning: "#F59E0B"
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
      negative: "#EF4444",
      warning: "#F59E0B"
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
      negative: "#EF4444",
      warning: "#F59E0B"
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
      negative: "#EF4444",
      warning: "#F59E0B"
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

  function _applyTheme(theme) {
    var root = document.documentElement;
    var appDark = localStorage.getItem("darkMode") === "true";
    var themeDark = theme.mode === "dark";
    var applySurfaces = themeDark === appDark;
    if (theme.primary) root.style.setProperty("--color-primary", theme.primary);
    if (theme.secondary) root.style.setProperty("--color-secondary", theme.secondary);
    if (applySurfaces && theme.background) root.style.setProperty("--color-content-bg", theme.background);
    else root.style.removeProperty("--color-content-bg");
    if (applySurfaces && theme.surface) root.style.setProperty("--color-card-bg", theme.surface);
    else root.style.removeProperty("--color-card-bg");
    if (applySurfaces && theme.text) root.style.setProperty("--color-content-text", theme.text);
    else root.style.removeProperty("--color-content-text");
    if (applySurfaces && theme.textMuted) root.style.setProperty("--color-content-text-secondary", theme.textMuted);
    else root.style.removeProperty("--color-content-text-secondary");
    if (theme.borderRadius != null) root.style.setProperty("--radius-md", theme.borderRadius + "px");
    if (theme.cardRadius != null) root.style.setProperty("--radius-card", theme.cardRadius + "px");
    if (theme.shadow) root.style.setProperty("--shadow-card", theme.shadow);
    if (theme.fontFamily) root.style.setProperty("--font-family", theme.fontFamily);
    if (theme.positive) root.style.setProperty("--color-positive", theme.positive);
    if (theme.negative) root.style.setProperty("--color-negative", theme.negative);
    if (theme.warning) root.style.setProperty("--color-warning", theme.warning);
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

  _applyTheme(_loadTheme());

  new MutationObserver(function() {
    _applyTheme(_loadTheme());
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

})();
