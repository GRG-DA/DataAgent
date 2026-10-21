/* eslint-disable no-unused-vars */
(function () {
  'use strict';

  /* Accent palette for the flat "light card" KPI look. Used only as a thin
     accent bar / line / glyph so chart data stays the star. */
  var _ACCENTS = ['#3B82F6', '#14B8A6', '#F59E0B'];

  /* Fallback dark gradients, kept for the signal-heavy styles (radial,
     progress, target/actual, status) that encode their meaning with colour. */
  var _gradients = [
    'linear-gradient(135deg,#3b82f6 0%,#1e40af 100%)',
    'linear-gradient(135deg,#14b8a6 0%,#0f766e 100%)',
    'linear-gradient(135deg,#f59e0b 0%,#b45309 100%)',
    'linear-gradient(135deg,#8b5cf6 0%,#6d28d9 100%)',
    'linear-gradient(135deg,#0ea5e9 0%,#0369a1 100%)',
    'linear-gradient(135deg,#10b981 0%,#047857 100%)',
    'linear-gradient(135deg,#ef4444 0%,#b91c1c 100%)',
    'linear-gradient(135deg,#6366f1 0%,#4338ca 100%)'
  ];

  var _light = false; /* tone for the current card being rendered */

  function _kpiGradient(idx) {
    return _gradients[Math.abs(idx || 0) % _gradients.length];
  }

  function _accent(idx) {
    return _ACCENTS[Math.abs(idx || 0) % _ACCENTS.length];
  }

  function _isLightDefault(style) {
    /* Flat, neutral cards: simple, trend, % change, comparison, prior period, icon.
       Signal styles keep their rich coloured treatment. */
    return ['simple', 'trend', 'percentage', 'comparison', 'previousPeriod', 'icon', 'plan', 'sparklineDelta', 'donut'].indexOf(style) !== -1;
  }

  function _getStatusColor(status) {
    switch (status) {
      case 'success': return '#10B981';
      case 'warning': return '#F59E0B';
      case 'danger':  return '#EF4444';
      case 'info':    return '#3B82F6';
      default:        return '#64748B';
    }
  }

  function _esc(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _fmtValue(val, config) {
    if (val == null || val === '') return '—';
    config = config || {};

    var num = Number(val);
    var isNum = !isNaN(num) && isFinite(num);

    var decimals = config.decimals != null ? config.decimals : (isNum ? 0 : null);
    var cur = config.currency;
    if (cur === 'none' || cur === 'None') cur = '';
    var prefix = config.prefix != null && config.prefix !== '' ? config.prefix : (cur || '');
    var suffix = config.suffix || '';
    var fmt = config.format || '';

    if (config.numberFormat === 'percentage') fmt = fmt || 'percentage';
    else if (config.numberFormat === 'currency') fmt = fmt || 'currency';
    else if (config.numberFormat === 'scientific') fmt = fmt || 'scientific';
    else if (config.numberFormat === 'compact') fmt = fmt || 'compact';
    if (cur && !fmt) fmt = 'currency';
    else if (!fmt && prefix === '$') fmt = 'currency';

    if (fmt === 'currency') {
      if (decimals == null) decimals = 2;
    }

    if (fmt === 'percentage' || suffix === '%') {
      suffix = '%';
      if (decimals == null) decimals = 1;
    }

    if (isNum) {
      var abbr = config.abbrev != null
        ? ((config.abbrev === false || config.abbrev === '0' || config.abbrev === 'off') ? 'off' : String(config.abbrev))
        : ((config.abbreviate === false || config.abbreviate === '0' || config.abbreviate === 'off') ? 'off' : '');
      if (fmt === 'scientific') {
        if (decimals == null) decimals = 2;
        return prefix + num.toExponential(decimals) + suffix;
      }
      if (fmt === 'compact') {
        return prefix + num.toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: decimals == null ? 1 : 2 }) + suffix;
      }
      if (abbr === 'auto' || abbr === '') {
        var absNum = Math.abs(num);
        if (abbr === '' && absNum >= 1e9) {
          num = num / 1e9;
          suffix = 'B' + suffix;
          if (decimals == null) decimals = 1;
        } else if (abbr === '' && absNum >= 1e6) {
          num = num / 1e6;
          suffix = 'M' + suffix;
          if (decimals == null) decimals = 1;
        } else if (abbr === '' && absNum >= 1e4) {
          num = num / 1e3;
          suffix = 'K' + suffix;
          if (decimals == null) decimals = 1;
        } else if (abbr === 'K') {
          num = num / 1e3;
          suffix = 'K' + suffix;
          if (decimals == null) decimals = 1;
        } else if (abbr === 'M') {
          num = num / 1e6;
          suffix = 'M' + suffix;
          if (decimals == null) decimals = 1;
        } else if (abbr === 'B') {
          num = num / 1e9;
          suffix = 'B' + suffix;
          if (decimals == null) decimals = 1;
        }
      }
      if (decimals == null) decimals = 0;
      var formatted = num.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
      });
      return prefix + formatted + suffix;
    }

    return prefix + String(val) + suffix;
  }

  function _typography(style, align) {
    var t = style || {};
    var parts = [];
    if (t.fontFamily) parts.push('font-family:' + t.fontFamily);
    if (t.fontWeight) parts.push('font-weight:' + t.fontWeight);
    if (t.letterSpacing) parts.push('letter-spacing:' + t.letterSpacing);
    if (align) parts.push('text-align:' + align);
    return parts.length ? parts.join(';') + ';' : '';
  }

  function _adjustColor(hex, amount) {
    hex = hex.replace('#', '');
    if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    var r = Math.max(0, Math.min(255, parseInt(hex.substring(0, 2), 16) + amount));
    var g = Math.max(0, Math.min(255, parseInt(hex.substring(2, 4), 16) + amount));
    var b = Math.max(0, Math.min(255, parseInt(hex.substring(4, 6), 16) + amount));
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  }

  /* Background for a card. Blindly honours an explicit `config.background`;
     defaults to the flat light card for neutral styles, colour otherwise. */
  function _bgStyle(config, fallbackIdx) {
    var parts = ['border-radius:12px'];
    if (config.background) {
      parts.push('background:' + config.background);
      if (config.border) parts.push('border:' + config.border);
      if (config.shadow) parts.push('box-shadow:' + config.shadow);
      return parts.join(';');
    }
    if (_light) {
      var accent = config.color || _accent(fallbackIdx);
      parts.push('background:var(--bd-kpi-bg,#ffffff)');
      parts.push('border:1px solid var(--bd-kpi-line,rgba(15,23,42,0.08))');
      if (config.shadow) parts.push('box-shadow:' + config.shadow);
      else parts.push('box-shadow:0 1px 2px rgba(15,23,42,0.05)');
      parts.push('background-image:linear-gradient(var(--bd-kpi-accent,' + accent + ') 0 3px,transparent 3px)');
      parts.push('background-size:cover');
      return parts.join(';');
    }
    var bg;
    if (config.color) bg = 'linear-gradient(135deg,' + config.color + ' 0%,' + _adjustColor(config.color, -20) + ' 100%)';
    else bg = _kpiGradient(fallbackIdx || 0);
    parts.push('background:' + bg);
    if (config.border) parts.push('border:' + config.border);
    if (config.shadow) parts.push('box-shadow:' + config.shadow);
    return parts.join(';');
  }

  /* Text colour helpers: light cards use theme tokens; dark cards use white */
  function _tv(color) { /* strong value text */
    return 'var(--bd-kpi-txt,' + color + ')';
  }
  function _tm(color) { /* secondary / label text */
    return 'var(--bd-kpi-txt-2,' + color + ')';
  }
  function _td(color) { /* muted / description text */
    return 'var(--bd-kpi-muted,' + color + ')';
  }

  var _styles = {};

  /* ─── 1. simple ─── */
  _styles.simple = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    return '<div class="kpi-simple kpi-light" style="' + _bgStyle(config, 0) + '; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:16px; text-align:center;">' +
      '<div class="kpi-value" style="font-size:calc(32px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; font-weight:500; color:' + _tm('#475569') + '; margin-top:6px;">' + _esc(label) + '</div>' +
      (config.description ? '<div style="font-size:10px; color:' + _td('#94a3b8') + '; margin-top:4px;">' + _esc(config.description) + '</div>' : '') +
      '</div>';
  };

  /* ─── 2. plan ───
     AI Dashboard Studio plan KPI: ONE aligned layout for every card — big
     value centred, label beneath it, then a compact delta pill and an optional
     muted "vs Previous" line. A fixed-height footer is always reserved so the
     big values sit on the same baseline across all cards in the band (cards
     without a delta keep the footer empty instead of shifting vertically). */
  _styles.plan = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var prevVal = config.previousValue != null && config.previousValue !== '' ?
      _fmtValue(config.previousValue, config) : null;
    var cur = Number(config.value != null ? config.value : (data && data.value));
    var base = config.previousValue != null && config.previousValue !== '' && !isNaN(Number(config.previousValue))
      ? Number(config.previousValue)
      : (config.comparisonValue != null && !isNaN(Number(config.comparisonValue)) ? Number(config.comparisonValue) : null);
    var deltaStr = '', prevStr = '';
    if (!isNaN(cur) && base != null && !isNaN(base)) {
      var delta = cur - base;
      var arrow = delta >= 0 ? '&#9650;' : '&#9660;';
      var pct = base !== 0 ? ((delta / Math.abs(base)) * 100).toFixed(1) : null;
      var col = delta >= 0 ? 'var(--bd-kpi-good,#10b981)' : 'var(--bd-kpi-bad,#ef4444)';
      deltaStr = '<span style="color:' + col + '; font-weight:700;">' + arrow + '&nbsp;' +
        (delta >= 0 ? '+' : '') + (pct != null ? _esc(pct) + '%' : _esc(_fmtValue(delta, config))) + '</span>';
      if (prevVal != null) {
        prevStr = '<span style="color:var(--bd-kpi-muted,#94a3b8);">vs Previous ' + _esc(prevVal) + '</span>';
      }
    }
    /* Single reserved line so every card's big value lands on the same Y and
       the whole block fits inside the card (nothing is pushed up to the
       top accent stripe). */
    var footer = '<div style="height:16px; display:flex; align-items:center; justify-content:center; gap:6px; margin-top:5px; font-size:11px; font-weight:700; letter-spacing:.2px; white-space:nowrap;">' + deltaStr + prevStr + '</div>';
    return '<div class="kpi-plan kpi-light" style="' + _bgStyle(config, 0) + '; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:8px 16px; text-align:center;">' +
      '<div class="kpi-value" style="font-size:calc(32px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.15; letter-spacing:-0.02em; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:11px; font-weight:600; letter-spacing:.3px; color:' + _tm('#475569') + '; margin-top:5px;">' + _esc(label) + '</div>' +
      footer +
      '</div>';
  };

  /* ─── 3. trend ─── */
  _styles.trend = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var trend = config.trend;
    var pct = config.comparisonValue;
    var up = true;
    if (trend === 'up' || (pct != null && Number(pct) >= 0)) up = true;
    else if (trend === 'down' || (pct != null && Number(pct) < 0)) up = false;
    var arrow = up ? '&#9650;' : '&#9660;';
    var pctAbs = pct != null ? Math.abs(Number(pct)).toFixed(1) : '';
    var badgeBg = up ? 'var(--bd-kpi-good,#10b981)' : 'var(--bd-kpi-bad,#ef4444)';
    var badge = pctAbs ? '<span style="display:inline-block; background:' + badgeBg + '; border-radius:12px; padding:3px 10px; font-size:11px; color:#fff; margin-top:8px;">' + arrow + ' ' + _esc(pctAbs) + '%</span>' : '';

    return '<div class="kpi-trend kpi-light" style="' + _bgStyle(config, 1) + '; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:16px; text-align:center;">' +
      '<div class="kpi-value" style="font-size:calc(32px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; font-weight:500; color:' + _tm('#475569') + '; margin-top:6px;">' + _esc(label) + '</div>' +
      badge +
      '</div>';
  };

  /* ─── 3. percentage ─── */
  _styles.percentage = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var pct = config.comparisonValue != null ? Number(config.comparisonValue) : null;
    var pctStr = pct != null ? (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%' : '';
    var pctColor = pct != null ? (pct >= 0 ? 'var(--bd-kpi-good,#10b981)' : 'var(--bd-kpi-bad,#ef4444)') : _td('#94a3b8');

    return '<div class="kpi-percentage kpi-light" style="' + _bgStyle(config, 2) + '; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:16px; text-align:center;">' +
      '<div class="kpi-value" style="font-size:calc(32px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; font-weight:500; color:' + _tm('#475569') + '; margin-top:6px;">' + _esc(label) + '</div>' +
      (pctStr ? '<div style="font-size:24px; font-weight:700; color:' + pctColor + '; margin-top:8px;">' + _esc(pctStr) + '</div>' : '') +
      '</div>';
  };

  /* ─── 4. sparkline (keep coloured: encodes a mini data plot) ─── */
  _styles.sparkline = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var widgetId = config.id || ('kpi-sp-' + Math.random().toString(36).slice(2, 8));
    var canvasId = 'spark-canvas-' + widgetId;

    return '<div class="kpi-sparkline" style="' + _bgStyle(config, 3) + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px;">' +
      '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:#fff; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; color:rgba(255,255,255,0.85); margin-top:4px;">' + _esc(label) + '</div>' +
      '<div style="flex:1; min-height:40px; margin-top:8px;">' +
      '<canvas id="' + _esc(canvasId) + '" style="width:100%; height:100%;" data-widget-id="' + _esc(widgetId) + '"></canvas>' +
      '</div>' +
      '</div>';
  };

  /* ─── 5. miniLine (keep coloured) ─── */
  _styles.miniLine = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var widgetId = config.id || ('kpi-ml-' + Math.random().toString(36).slice(2, 8));
    var canvasId = 'miniline-canvas-' + widgetId;

    return '<div class="kpi-miniLine" style="' + _bgStyle(config, 4) + '; height:100%; display:flex; flex-direction:column; padding:16px;">' +
      '<div style="display:flex; justify-content:space-between; align-items:baseline;">' +
      '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:#fff; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; color:rgba(255,255,255,0.85);">' + _esc(label) + '</div>' +
      '</div>' +
      '<div style="flex:1; min-height:60px; margin-top:8px;">' +
      '<canvas id="' + _esc(canvasId) + '" style="width:100%; height:100%;" data-widget-id="' + _esc(widgetId) + '"></canvas>' +
      '</div>' +
      '</div>';
  };

  /* ─── 5b. sparklineDelta (light) — big value + delta pill + sparkline ─── */
  _styles.sparklineDelta = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var widgetId = config.id || ('kpi-sd-' + Math.random().toString(36).slice(2, 8));
    var canvasId = 'sparkdelta-canvas-' + widgetId;
    var cur = Number(config.value != null ? config.value : (data && data.value));
    var base = config.previousValue != null && config.previousValue !== '' && !isNaN(Number(config.previousValue))
      ? Number(config.previousValue)
      : (config.comparisonValue != null && !isNaN(Number(config.comparisonValue)) ? Number(config.comparisonValue) : null);
    var deltaStr = '';
    if (!isNaN(cur) && base != null && !isNaN(base)) {
      var delta = cur - base;
      var arrow = delta >= 0 ? '&#9650;' : '&#9660;';
      var pct = base !== 0 ? ((delta / Math.abs(base)) * 100).toFixed(1) : null;
      var col = delta >= 0 ? 'var(--bd-kpi-good,#10b981)' : 'var(--bd-kpi-bad,#ef4444)';
      deltaStr = '<span style="color:' + col + '; font-weight:700;">' + arrow + '&nbsp;' +
        (delta >= 0 ? '+' : '') + (pct != null ? _esc(pct) + '%' : _esc(_fmtValue(delta, config))) + '</span>';
    }

    return '<div class="kpi-sparklineDelta kpi-light" style="' + _bgStyle(config, 0) + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px;">' +
      '<div style="display:flex; justify-content:space-between; align-items:baseline;">' +
      '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div style="font-size:11px; font-weight:700; letter-spacing:.2px; white-space:nowrap;">' + deltaStr + '</div>' +
      '</div>' +
      '<div class="kpi-label" style="font-size:11px; font-weight:600; color:' + _tm('#475569') + '; margin-top:4px;">' + _esc(label) + '</div>' +
      '<div style="flex:1; min-height:40px; margin-top:8px;">' +
      '<canvas id="' + _esc(canvasId) + '" style="width:100%; height:100%;" data-widget-id="' + _esc(widgetId) + '"></canvas>' +
      '</div>' +
      '</div>';
  };

  /* ─── 6. miniBar (single accent colour, no rainbow) ─── */
  _styles.miniBar = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var chartData = config.chartData || (data && data.chartData) || [];
    var bars = '';
    var maxVal = 1;
    for (var i = 0; i < chartData.length; i++) {
      var n = Number(chartData[i]);
      if (!isNaN(n) && n > maxVal) maxVal = n;
    }
    var barColor = config.color || '#3B82F6';
    for (var j = 0; j < chartData.length; j++) {
      var v = Number(chartData[j]) || 0;
      var h = Math.round((v / maxVal) * 100);
      bars += '<div style="display:flex; flex-direction:column; align-items:center; flex:1;">' +
        '<div style="width:100%; display:flex; align-items:flex-end; height:50px;">' +
        '<div style="width:100%; height:' + h + '%; background:linear-gradient(180deg,' + barColor + ',' + _adjustColor(barColor, -25) + '); border-radius:4px 4px 0 0; min-height:2px;"></div>' +
        '</div>' +
        '</div>';
    }

    return '<div class="kpi-miniBar" style="' + _bgStyle(config, 5) + '; height:100%; display:flex; flex-direction:column; padding:16px;">' +
      '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:#fff; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; color:rgba(255,255,255,0.85); margin-top:4px;">' + _esc(label) + '</div>' +
      '<div style="flex:1; display:flex; align-items:flex-end; gap:3px; margin-top:8px;">' + bars + '</div>' +
      '</div>';
  };

  /* ─── 7. progress (keep coloured: encodes completion) ─── */
  _styles.progress = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var actual = Number(config.actual != null ? config.actual : (data && data.actual));
    var target = Number(config.target != null ? config.target : (data && data.target));
    if (isNaN(actual)) actual = 0;
    if (isNaN(target) || target === 0) target = 100;
    var pct = Math.min(100, Math.max(0, Math.round((actual / target) * 100)));

    return '<div class="kpi-progress" style="' + _bgStyle(config, 6) + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px;">' +
      '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:#fff; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; color:rgba(255,255,255,0.85); margin-top:4px;">' + _esc(label) + '</div>' +
      '<div style="margin-top:12px;">' +
      '<div style="background:rgba(255,255,255,0.2); border-radius:8px; height:8px; width:100%;">' +
      '<div style="background:#fff; height:100%; border-radius:8px; width:' + pct + '%; transition:width 0.4s ease;"></div>' +
      '</div>' +
      '<div style="font-size:11px; color:rgba(255,255,255,0.7); margin-top:4px; text-align:right;">' + pct + '%</div>' +
      '</div>' +
      '</div>';
  };

  /* ─── 8. radial (keep coloured) ─── */
  _styles.radial = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var actual = Number(config.actual != null ? config.actual : (data && data.actual));
    var target = Number(config.target != null ? config.target : (data && data.target));
    if (isNaN(actual)) actual = 0;
    if (isNaN(target) || target === 0) target = 100;
    var pct = Math.min(1, Math.max(0, actual / target));
    var radius = 42;
    var circumference = 2 * Math.PI * radius;
    var offset = circumference * (1 - pct);
    var ringColor = pct >= 0.8 ? '#6EE7B7' : pct >= 0.5 ? '#FBBF24' : '#F87171';

    var svg = '<svg viewBox="0 0 100 100" style="height:58%;max-height:150px;max-width:100%;aspect-ratio:1/1;flex:none;">' +
      '<circle cx="50" cy="50" r="' + radius + '" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="8"/>' +
      '<circle cx="50" cy="50" r="' + radius + '" fill="none" stroke="' + ringColor + '" stroke-width="8" ' +
      'stroke-linecap="round" stroke-dasharray="' + circumference + '" stroke-dashoffset="' + offset + '" ' +
      'transform="rotate(-90 50 50)" style="transition:stroke-dashoffset 0.6s ease;"/>' +
      '<text x="50" y="50" text-anchor="middle" dominant-baseline="central" fill="#fff" font-size="16" font-weight="800">' + _esc(Math.round(pct * 100) + '%') + '</text>' +
      '</svg>';

    return '<div class="kpi-radial" style="' + _bgStyle(config, 7) + '; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:16px; text-align:center;">' +
      svg +
      '<div class="kpi-value" style="font-size:22px; font-weight:800; color:#fff; margin-top:8px; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; color:rgba(255,255,255,0.85); margin-top:4px;">' + _esc(label) + '</div>' +
      '</div>';
  };

  /* ─── 8b. donut (light) — donut ring vs target with value in the centre ─── */
  _styles.donut = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var actual = Number(config.value != null ? config.value : (data && data.value));
    var target = Number(config.target != null ? config.target : (data && data.target));
    if (isNaN(actual)) actual = 0;
    if (isNaN(target) || target === 0) target = 100;
    var pct = Math.min(1, Math.max(0, actual / target));
    var radius = 40;
    var circumference = 2 * Math.PI * radius;
    var offset = circumference * (1 - pct);
    var ringColor = config.donutColor || (pct >= 0.8 ? '#10B981' : pct >= 0.5 ? '#F59E0B' : '#EF4444');
    var trackColor = config.donutTrackColor || 'rgba(15,23,42,0.08)';

    var svg = '<svg viewBox="0 0 100 100" style="height:64px;width:64px;flex:none;">' +
      '<circle cx="50" cy="50" r="' + radius + '" fill="none" stroke="' + trackColor + '" stroke-width="10"/>' +
      '<circle cx="50" cy="50" r="' + radius + '" fill="none" stroke="' + ringColor + '" stroke-width="10" ' +
      'stroke-linecap="round" stroke-dasharray="' + circumference + '" stroke-dashoffset="' + offset + '" ' +
      'transform="rotate(-90 50 50)" style="transition:stroke-dashoffset 0.6s ease;"/>' +
      '<text x="50" y="50" text-anchor="middle" dominant-baseline="central" fill="' + _tv('#0f172a') + '" font-size="15" font-weight="800">' + _esc(Math.round(pct * 100) + '%') + '</text>' +
      '</svg>';

    return '<div class="kpi-donut kpi-light" style="' + _bgStyle(config, 0) + '; height:100%; display:flex; align-items:center; justify-content:center; gap:16px; padding:16px;">' +
      svg +
      '<div style="text-align:left;">' +
      '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:11px; font-weight:600; color:' + _tm('#475569') + '; margin-top:4px;">' + _esc(label) + '</div>' +
      (config.target != null ? '<div style="font-size:11px; color:' + _td('#94a3b8') + '; margin-top:2px;">Target ' + _esc(_fmtValue(config.target, config)) + '</div>' : '') +
      '</div>' +
      '</div>';
  };

  /* ─── 9. comparison ─── */
  _styles.comparison = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var compVal = _fmtValue(config.comparisonValue, config);
    var label = config.label || '';
    var compLabel = config.comparisonLabel || 'Comparison';

    return '<div class="kpi-comparison kpi-light" style="' + _bgStyle(config, 0) + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px; text-align:center;">' +
      '<div style="display:flex; align-items:center; justify-content:center; gap:24px;">' +
      '<div style="flex:1;">' +
      '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:11px; font-weight:500; color:' + _tm('#475569') + '; margin-top:4px;">' + _esc(label) + '</div>' +
      '</div>' +
      '<div style="width:1px; height:40px; background:var(--bd-kpi-line,rgba(15,23,42,0.08));"></div>' +
      '<div style="flex:1;">' +
      '<div style="font-size:24px; font-weight:700; color:' + _tv('#0f172a') + '; line-height:1.2;">' + _esc(compVal) + '</div>' +
      '<div style="font-size:11px; color:' + _tm('#64748b') + '; margin-top:4px;">' + _esc(compLabel) + '</div>' +
      '</div>' +
      '</div>' +
      '</div>';
  };

  /* ─── 10. targetActual (keep coloured: encodes target gap) ─── */
  _styles.targetActual = function (config, data) {
    var actualVal = _fmtValue(config.actual != null ? config.actual : (data && data.actual), config);
    var targetVal = _fmtValue(config.target != null ? config.target : (data && data.target), config);
    var label = config.label || '';
    var a = Number(config.actual != null ? config.actual : (data && data.actual));
    var t = Number(config.target != null ? config.target : (data && data.target));
    var pct = (!isNaN(a) && !isNaN(t) && t !== 0) ? Math.round((a / t) * 100) : 0;
    var gap = pct >= 100 ? 0 : Math.max(0, 100 - pct);
    var pctColor = pct >= 90 ? '#6EE7B7' : pct >= 60 ? '#FBBF24' : '#F87171';
    var statusText = pct >= 100 ? 'Target Met' : gap + '% gap';

    return '<div class="kpi-targetActual" style="' + _bgStyle(config, 1) + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px;">' +
      '<div class="kpi-label" style="font-size:12px; color:rgba(255,255,255,0.85); margin-bottom:8px;">' + _esc(label) + '</div>' +
      '<div style="display:flex; justify-content:space-between; align-items:baseline;">' +
      '<div>' +
      '<div style="font-size:10px; color:rgba(255,255,255,0.6); text-transform:uppercase; letter-spacing:0.5px;">Actual</div>' +
      '<div class="kpi-value" style="font-size:26px; font-weight:800; color:#fff; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(actualVal) + '</div>' +
      '</div>' +
      '<div style="text-align:right;">' +
      '<div style="font-size:10px; color:rgba(255,255,255,0.6); text-transform:uppercase; letter-spacing:0.5px;">Target</div>' +
      '<div style="font-size:22px; font-weight:700; color:rgba(255,255,255,0.8); line-height:1.2;">' + _esc(targetVal) + '</div>' +
      '</div>' +
      '</div>' +
      '<div style="margin-top:10px;">' +
      '<div style="background:rgba(255,255,255,0.2); border-radius:6px; height:6px; width:100%;">' +
      '<div style="background:' + pctColor + '; height:100%; border-radius:6px; width:' + Math.min(100, pct) + '%; transition:width 0.4s ease;"></div>' +
      '</div>' +
      '<div style="display:flex; justify-content:space-between; margin-top:4px;">' +
      '<span style="font-size:10px; color:' + pctColor + '; font-weight:600;">' + pct + '%</span>' +
      '<span style="font-size:10px; color:rgba(255,255,255,0.6);">' + _esc(statusText) + '</span>' +
      '</div>' +
      '</div>' +
      '</div>';
  };

  /* ─── 11. icon ─── */
  _styles.icon = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var icon = config.icon || '&#9733;';
    var iconPos = config.iconPosition || 'left';
    var iconSize = config.iconSize || '28px';
    var iconColor = config.iconColor || (config.color || '#3B82F6');
    var iconHtml = '<span style="font-size:' + iconSize + '; color:' + iconColor + '; line-height:1;">' + icon + '</span>';

    var layout;
    if (iconPos === 'top') {
      layout = '<div style="text-align:center;">' +
        '<div style="margin-bottom:8px;">' + iconHtml + '</div>' +
        '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
        '<div class="kpi-label" style="font-size:12px; font-weight:500; color:' + _tm('#475569') + '; margin-top:4px;">' + _esc(label) + '</div>' +
        '</div>';
    } else if (iconPos === 'right') {
      layout = '<div style="display:flex; align-items:center; justify-content:space-between; width:100%;">' +
        '<div>' +
        '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
        '<div class="kpi-label" style="font-size:12px; font-weight:500; color:' + _tm('#475569') + '; margin-top:4px;">' + _esc(label) + '</div>' +
        '</div>' +
        iconHtml +
        '</div>';
    } else {
      layout = '<div style="display:flex; align-items:center; justify-content:space-between; width:100%;">' +
        '<div style="display:flex; align-items:center; gap:12px;">' +
        iconHtml +
        '<div>' +
        '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
        '<div class="kpi-label" style="font-size:12px; font-weight:500; color:' + _tm('#475569') + '; margin-top:2px;">' + _esc(label) + '</div>' +
        '</div>' +
        '</div>' +
        '</div>';
    }

    return '<div class="kpi-icon kpi-light" style="' + _bgStyle(config, 2) + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px;">' +
      layout +
      '</div>';
  };

  /* ─── 12. status (keep coloured: encodes health) ─── */
  _styles.status = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var label = config.label || '';
    var status = config.status || 'info';
    var dotColor = _getStatusColor(status);
    var statusLabel = status.charAt(0).toUpperCase() + status.slice(1);

    return '<div class="kpi-status" style="' + _bgStyle(config, 3) + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px;">' +
      '<div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">' +
      '<span style="width:10px; height:10px; border-radius:50%; background:' + dotColor + '; display:inline-block; box-shadow:0 0 6px ' + dotColor + ';"></span>' +
      '<span style="font-size:11px; color:rgba(255,255,255,0.75); text-transform:uppercase; letter-spacing:0.5px; font-weight:600;">' + _esc(statusLabel) + '</span>' +
      '</div>' +
      '<div class="kpi-value" style="font-size:calc(28px * var(--bd-scale, 1)); font-weight:800; color:#fff; line-height:1.2; ' + _typography(config.typography) + '">' + _esc(val) + '</div>' +
      '<div class="kpi-label" style="font-size:12px; color:rgba(255,255,255,0.85); margin-top:4px;">' + _esc(label) + '</div>' +
      '</div>';
  };

  /* ─── 13. multiValue (keep coloured) ─── */
  _styles.multiValue = function (config, data) {
    var values = config.values || [];
    if (values.length === 0 && data && Array.isArray(data.values)) {
      values = data.values;
    }
    var cells = '';
    for (var i = 0; i < values.length && i < 4; i++) {
      var item = values[i] || {};
      var itemVal = _fmtValue(item.value, config);
      var itemLabel = item.label || item.name || ('Value ' + (i + 1));
      cells += '<div style="background:rgba(255,255,255,0.1); border-radius:8px; padding:10px; text-align:center;">' +
        '<div style="font-size:20px; font-weight:700; color:#fff; line-height:1.2;">' + _esc(itemVal) + '</div>' +
        '<div style="font-size:10px; color:rgba(255,255,255,0.7); margin-top:4px;">' + _esc(itemLabel) + '</div>' +
        '</div>';
    }

    return '<div class="kpi-multiValue" style="' + _bgStyle(config, 4) + '; height:100%; display:flex; flex-direction:column; padding:16px;">' +
      '<div class="kpi-label" style="font-size:12px; color:rgba(255,255,255,0.85); margin-bottom:10px; ' + _typography(config.typography) + '">' + _esc(config.label || '') + '</div>' +
      '<div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; flex:1;">' + cells + '</div>' +
      '</div>';
  };

  /* ─── 14. previousPeriod ─── */
  _styles.previousPeriod = function (config, data) {
    var val = _fmtValue(config.value != null ? config.value : (data && data.value), config);
    var prevVal = _fmtValue(config.previousValue != null ? config.previousValue : (data && data.previousValue), config);
    var label = config.label || '';
    var periodLabel = config.periodLabel || 'vs Previous';

    var cur = Number(config.value != null ? config.value : (data && data.value));
    var prev = Number(config.previousValue != null ? config.previousValue : (data && data.previousValue));
    var delta = null;
    var deltaColor = _td('#94a3b8');
    var tip = config.measureExpr ? _reportTip(config) : '';
    var switcher = _measureSwitcher(config);
    var headRow = '<div style="display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:6px;">' +
      '<div class="kpi-label" style="font-size:12px; font-weight:500; color:' + _tm('#475569') + '; ' + _typography(config.typography) + '">' + _esc(label) + '</div>' +
      switcher + '</div>';
    if (!isNaN(cur) && !isNaN(prev)) {
      delta = cur - prev;
      var deltaPct = prev !== 0 ? ((delta / Math.abs(prev)) * 100).toFixed(1) : null;
      var arrow = delta >= 0 ? '&#9650;' : '&#9660;';
      deltaColor = delta >= 0 ? 'var(--bd-kpi-good,#10b981)' : 'var(--bd-kpi-bad,#ef4444)';
      var deltaStr = delta >= 0 ? '+' : '';
      deltaStr += deltaPct != null ? deltaPct + '%' : _fmtValue(delta, config);
      var cfMode = (config.cf || 'auto');
      var cfBg = _bgStyle(config, 5);
      var cfBadge = '';
      if (config.measureExpr && cfMode !== 'none') {
        var tint = delta >= 0 ? 'rgba(16,185,129,0.12)' : 'rgba(239,68,68,0.12)';
        var tintLayer = 'linear-gradient(' + tint + ',' + tint + ')';
        if (cfBg.indexOf('background-image:') !== -1) {
          cfBg = cfBg.replace(/background-image:/, 'background-image: ' + tintLayer + ' , ');
        } else {
          cfBg += ';background-image:' + tintLayer;
        }
        if (cfMode === 'icon') {
          cfBadge = '<span style="width:24px; height:24px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; font-size:12px; font-weight:800; color:#fff; background:' + deltaColor + '; margin-right:2px;">' + (delta >= 0 ? '▲' : '▼') + '</span>';
        }
      }

return '<div class="kpi-previousPeriod kpi-light" style="' + cfBg + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px; position:relative;">' +
        tip +
        headRow +
        '<div class="kpi-value" style="font-size:calc(32px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2;">' + _esc(val) + '</div>' +
        '<div style="display:flex; align-items:center; gap:8px; margin-top:8px;">' +
        cfBadge +
        '<span style="color:' + deltaColor + '; font-weight:700; font-size:13px;">' + arrow + ' ' + _esc(deltaStr) + '</span>' +
        '</div>' +
        '<div style="font-size:11px; color:' + _td('#94a3b8') + '; margin-top:4px;">' + _esc(periodLabel) + ': ' + _esc(prevVal) + '</div>' +
        '</div>';
    }

    return '<div class="kpi-previousPeriod kpi-light" style="' + _bgStyle(config, 5) + '; height:100%; display:flex; flex-direction:column; justify-content:center; padding:16px; position:relative;">' +
      tip +
      headRow +
      '<div class="kpi-value" style="font-size:calc(32px * var(--bd-scale, 1)); font-weight:800; color:' + _tv('#0f172a') + '; line-height:1.2;">' + _esc(val) + '</div>' +
      '<div style="font-size:12px; color:' + _td('#94a3b8') + '; margin-top:6px;">' + _esc(periodLabel) + ': ' + _esc(prevVal) + '</div>' +
      '</div>';
  };

  /* ─── Metric switcher (P1.2) ───
     A compact measure dropdown on measure-backed KPIs (visible when the
     registry has more than one measure): switching re-targets the KPI to
     another measure without recreating the widget. */
  function _measureSwitcher(c) {
    if (!c.measureExpr || !c.id) return '';
    var lst = [];
    try { lst = (window.App && App.measures) || []; } catch (e) {}
    if (!lst.length) return '';
    var opts = lst.map(function (m) {
      return '<option value="' + _esc(m.id) + '"' + (String(m.id) === String(c.measureId) ? ' selected' : '') + '>' + _esc(m.name) + '</option>';
    }).join('');
    return '<select class="bd-kpi-measure-switch" title="Switch measure" onchange="window.BlankDash && BlankDash.switchMeasure(\'' + _esc(c.id) + '\', this.value)" style="font-size:10.5px; background:rgba(127,97,255,0.08); color:' + _tm('#475569') + '; border:1px solid rgba(127,97,255,0.25); border-radius:6px; padding:2px 4px; max-width:132px; cursor:pointer; font-weight:600; outline:none;">' + opts + '</select>';
  }

  /* ─── Report tooltip (P1.1) ───
     Hovering the ℹ chip on a measure-backed KPI shows the calculation
     report: period, formula, source table, prior/full-prior anchor maths. */
  function _reportTip(c) {
    var rows = '';
    function rw(key, valHtml) {
      return '<div style="display:flex; justify-content:space-between; gap:14px; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.08);">' +
        '<span style="color:#a3a3c2; font-size:11px; white-space:nowrap;">' + _esc(key) + '</span>' +
        '<span style="color:#eceaf8; font-size:11px; text-align:right; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">' + valHtml + '</span>' +
        '</div>';
    }
    var cur = Number(c.value != null ? c.value : 0);
    var prev = Number(c.previousValue != null ? c.previousValue : 0);
    var cmp = Number(c.comparisonValue != null ? c.comparisonValue : 0);
    var delta = cur - prev;
    var deltaPct = prev !== 0 ? ((delta / Math.abs(prev)) * 100) : null;
    var arrow = delta >= 0 ? '▲' : '▼';
    var pctStr = deltaPct == null ? '—' : (deltaPct > 0 ? '+' : '') + deltaPct.toFixed(1) + '%';
    var curLabel = (c.timeIntel ? String(c.timeIntel).toUpperCase() : 'CURRENT');
    rows += rw('Period', _esc(curLabel) + ' <span style="color:#827cac">(' + _esc(c.periodLabel || 'prior') + ')</span>');
    rows += rw('Value', '<span style="color:#10b981; font-weight:700;">' + _esc(_fmtValue(cur, c)) + '</span>');
    rows += rw('Prior', _esc(_fmtValue(prev, c)));
    rows += rw('Δ ' + pctStr, '<span style="color:' + (delta >= 0 ? '#10b981' : '#ef4444') + ';">' + arrow + ' ' + _esc(_fmtValue(delta, c)) + '</span>');
    if (cmp != null && !isNaN(cmp) && String(cmp) !== '' && Number(cmp) !== 0) rows += rw('Full prior', _esc(_fmtValue(cmp, c)));
    var formula = String(c.measureExpr || '');
    if (formula.length > 34) formula = formula.slice(0, 31) + '…';
    rows += rw('Formula', _esc(formula));
    rows += rw('Source', 'analytics.' + _esc(c.table || ''));
    if (c.anchor) rows += rw('As of', _esc(c.anchor));
    return '<span class="bd-kpi-tip-ic" style="position:absolute; top:8px; right:8px; z-index:30; width:20px; height:20px; border-radius:50%; background:rgba(127,97,255,0.14); color:#7B61FF; font-size:11px; font-weight:800; display:flex; align-items:center; justify-content:center; cursor:help;" onmouseover="var cc=this.firstElementChild; if(cc){cc.style.display=\'block\';}" onmouseout="var cc=this.firstElementChild; if(cc){cc.style.display=\'none\';}">ℹ' +
      '<span class="bd-kpi-tip-card" style="display:none; position:absolute; top:24px; right:0; width:250px; background:#1e1b3a; border:1px solid #3b355c; border-radius:10px; padding:10px 12px; box-shadow:0 12px 32px rgba(0,0,0,0.45); z-index:40;">' +
      '<div style="color:#c9c4f0; font-size:12px; font-weight:700; margin-bottom:6px;">Report</div>' + rows +
      '</span></span>';
  }

  /* ─── Public API ─── */
  window.KPIStyles = {
    render: function (config, data, container) {
      if (!container) return;
      var style = config.kpiStyle || 'simple';
      /* Neutral styles default to the flat light card unless the user supplies
         an explicit background/gradient (opt-in to the rich look). */
      _light = _isLightDefault(style) && !config.background;
      var fn = _styles[style] || _styles.simple;
      container.innerHTML = fn(config, data);

      /* Drill-through target: config.drillPage makes the whole card clickable. */
      if (config.drillPage) {
        var root = container.firstElementChild;
        if (root) {
          root.classList.add('bd-drillable');
          root.setAttribute('data-drill-page', String(config.drillPage).replace(/"/g, '&quot;'));
          if (config.drillField) {
            root.setAttribute('data-drill-field', String(config.drillField).replace(/"/g, '&quot;'));
            var fl = window.CrossFilterBus ? window.CrossFilterBus.getFilters() : null;
            if (fl && config.drillField in fl && fl[config.drillField] != null) {
              root.setAttribute('data-drill-value', JSON.stringify(fl[config.drillField]).replace(/"/g, '&quot;'));
            }
          }
        }
        if (!container.__bdDrillBound) {
          container.__bdDrillBound = true;
          container.addEventListener('click', function (e) {
            var el = e.target.closest('.bd-drillable[data-drill-page]');
            if (!el) return;
            e.stopPropagation();
            if (!window.BlankDash || !window.BlankDash._drillThrough) return;
            var page = el.getAttribute('data-drill-page');
            var field = el.getAttribute('data-drill-field') || '';
            var value = el.getAttribute('data-drill-value');
            if (value != null) {
              value = value.replace(/&quot;/g, '"').replace(/&amp;/g, '&');
              try { value = JSON.parse(value); } catch (e2) { /* scalar string stays as-is */ }
            }
            window.BlankDash._drillThrough(page, field ? { field: field, value: value } : null);
          });
        }
      }

      if ((style === 'sparkline' || style === 'miniLine' || style === 'sparklineDelta') && config.sparklineData && window.Charts && window.Charts.sparkline) {
        setTimeout(function () {
          var widgetId = config.id || '';
          var prefix = style === 'sparkline' ? 'spark-canvas-' : style === 'miniLine' ? 'miniline-canvas-' : 'sparkdelta-canvas-';
          var canvas = container.querySelector('#' + prefix + widgetId) || container.querySelector('canvas');
          if (canvas) {
            window.Charts.sparkline(canvas.id || canvas, config.sparklineData, {
              color: config.color || _accent(0),
              lineWidth: 2,
              height: style === 'miniLine' ? 60 : 40,
              interactive: false
            });
          }
        }, 50);
      }
    },
    list: function () {
      return [
        { value: 'simple',           label: 'Simple KPI' },
        { value: 'trend',            label: 'KPI with Trend' },
        { value: 'percentage',       label: 'Percentage Change' },
        { value: 'sparkline',        label: 'Sparkline KPI' },
        { value: 'sparklineDelta',   label: 'Sparkline + Delta' },
        { value: 'donut',            label: 'Donut KPI' },
        { value: 'miniLine',         label: 'Mini Line Chart' },
        { value: 'miniBar',          label: 'Mini Bar Chart' },
        { value: 'progress',         label: 'Progress Bar' },
        { value: 'radial',           label: 'Radial Progress' },
        { value: 'comparison',       label: 'Comparison' },
        { value: 'targetActual',     label: 'Target vs Actual' },
        { value: 'icon',             label: 'Icon KPI' },
        { value: 'status',           label: 'Status Indicator' },
        { value: 'multiValue',       label: 'Multi Value Grid' },
        { value: 'previousPeriod',   label: 'Previous Period' }
      ];
    }
  };
})();