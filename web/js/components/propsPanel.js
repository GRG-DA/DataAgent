/**
 * PropsPanel – Auto-generated properties panel for dashboard widgets.
 * Reads WidgetPropertySchemas and builds real-time editing UI via UIControlKit.
 */
(function () {
  'use strict';

  /* ── helpers ────────────────────────────────────────────────────────── */

  function _esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(String(s)));
    return d.innerHTML;
  }

  function _el(tag, cls, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  /* compact vs full-width property types */
  var COMPACT_TYPES = ['number', 'color', 'toggle', 'select', 'slider'];
  var FULL_TYPES    = ['text', 'textarea', 'spacing', 'border', 'gradient',
                       'multicolor', 'alignment', 'font', 'icon'];

  /* ── tab definitions ────────────────────────────────────────────────── */

  var TAB_DEFS = [
    { id: 'content',     icon: 'fa-edit',           label: 'Content' },
    { id: 'data',        icon: 'fa-database',        label: 'Data' },
    { id: 'chart',       icon: 'fa-chart-bar',       label: 'Chart' },
    { id: 'typography',  icon: 'fa-font',            label: 'Typography' },
    { id: 'colors',      icon: 'fa-palette',         label: 'Colors' },
    { id: 'border',      icon: 'fa-border-all',      label: 'Border' },
    { id: 'background',  icon: 'fa-fill-drip',       label: 'Background' },
    { id: 'spacing',     icon: 'fa-arrows-alt',      label: 'Spacing' },
    { id: 'shadow',      icon: 'fa-clone',           label: 'Shadow' },
    { id: 'interaction', icon: 'fa-mouse-pointer',   label: 'Interaction' },
    { id: 'responsive',  icon: 'fa-mobile-alt',      label: 'Responsive' },
    { id: 'advanced',    icon: 'fa-cog',             label: 'Advanced' }
  ];

  /* ══════════════════════════════════════════════════════════════════════ */
  /*  PropsPanel                                                          */
  /* ══════════════════════════════════════════════════════════════════════ */

  var _panelEl   = null;   // the root .bd-props dock element
  var _current   = null;   // currently edited widget
  var _listeners = [];     // change callbacks
  var _activeTab = 'content';

  /* Chart sub-types (bar/line/pie/…) are stored as widget.type = "chart" with
     the real visual type on widget.config.chartType. Resolve the effective
     schema type so the properties panel shows the right tabs for them. */
  function _widgetType(widget) {
    if (!widget) return null;
    if (widget.config && widget.config.chartType) return widget.config.chartType;
    return widget.type;
  }

  /* ── build the full panel HTML ──────────────────────────────────────── */

  /* map schema group names to panel tabs */
  var GROUP_TO_TAB = {
    'content': 'content',
    'kpi': 'content',
    'main': 'content',
    'layout': 'content',
    'style': 'content',
    'data': 'data',
    'chart': 'chart',
    'chart style': 'chart',
    'chart visual': 'chart',
    'axes': 'chart',
    'legend': 'chart',
    'tooltip': 'chart',
    'chart colors': 'chart',
    'line options': 'chart',
    'bar options': 'chart',
    'pie options': 'chart',
    'scatter options': 'chart',
    'gauge options': 'chart',
    'heatmap options': 'chart',
    'treemap options': 'chart',
    'funnel options': 'chart',
    'waterfall options': 'chart',
    'sparkline options': 'chart',
    'bubble options': 'chart',
    'polar options': 'chart',
    'box plot options': 'chart',
    'histogram options': 'chart',
    'bullet options': 'chart',
    'pyramid options': 'chart',
    'sunburst options': 'chart',
    'word cloud options': 'chart',
    'percent bar options': 'chart',
    'stacked area options': 'chart',
    'area options': 'chart',
    'combo options': 'chart',
    'typography': 'typography',
    'title typography': 'typography',
    'value typography': 'typography',
    'comparison typography': 'typography',
    'subtitle typography': 'typography',
    'label typography': 'typography',
    'labels typography': 'typography',
    'chart labels typography': 'typography',
    'colors': 'colors',
    'color system': 'colors',
    'border': 'border',
    'background': 'background',
    'spacing': 'spacing',
    'effects': 'shadow',
    'shadow': 'shadow',
    'interaction': 'interaction',
    'responsive': 'responsive',
    'advanced': 'advanced'
  };

  function _build(widget) {
    _destroy();
    var wtype = _widgetType(widget);
    var schema = window.WidgetPropertySchemas
      ? window.WidgetPropertySchemas.getSchema(wtype)
      : null;
    if (!schema) return;

    /* Schema is a flat array of { key, type, label, group, default, ... }.
       Normalise into the { properties, defaults } shape expected below. */
    var properties = {};
    var defaults   = {};
    if (Array.isArray(schema)) {
      schema.forEach(function (p) {
        if (!p || !p.key) return;
        properties[p.key] = p;
        if (p.default !== undefined) defaults[p.key] = p.default;
      });
    } else {
      properties = schema.properties || {};
      defaults   = schema.defaults   || {};
    }

    /* organise properties into { tabId: { sectionId: [prop, …] } } */
    var tabsMap = _organiseByTab(properties, widget.type);

    /* root dock */
    var panel = _el('div', 'bd-props');
    _panelEl = panel;

    /* ── header ── */
    var head = _el('div', 'bd-props-head');
    var badge = _el('span', 'bd-props-type-badge');
    badge.textContent = wtype || 'widget';
    head.appendChild(badge);

    var titleSpan = _el('span', 'bd-props-title');
    titleSpan.textContent = (widget.config && widget.config.label) || wtype || 'Widget';
    head.appendChild(titleSpan);

    var resetAllBtn = _el('button', 'bd-props-reset-all', { title: 'Reset All' });
    resetAllBtn.innerHTML = '<i class="fas fa-redo"></i>';
    resetAllBtn.addEventListener('click', function () { _resetAll(widget, schema); });
    head.appendChild(resetAllBtn);

    var closeBtn = _el('button', 'bd-props-close', { title: 'Close' });
    closeBtn.innerHTML = '<i class="fas fa-times"></i>';
    closeBtn.addEventListener('click', function () { window.PropsPanel.close(); });
    head.appendChild(closeBtn);

    panel.appendChild(head);

    /* ── body ── */
    var body = _el('div', 'bd-props-body');

    /* tab bar */
    var tabBar = _el('div', 'bd-props-tabs');
    TAB_DEFS.forEach(function (def) {
      if (!tabsMap[def.id] || tabsMap[def.id].__count === 0) return;
      var btn = _el('button', 'bd-props-tab' + (def.id === _activeTab ? ' active' : ''),
                    { 'data-props-tab': def.id });
      btn.innerHTML = '<i class="fas ' + def.icon + '"></i> ' + _esc(def.label);
      btn.addEventListener('click', function () { _switchTab(def.id); });
      tabBar.appendChild(btn);
    });
    body.appendChild(tabBar);

    /* ensure active tab still exists */
    if (!tabsMap[_activeTab]) {
      var firstTab = tabBar.querySelector('.bd-props-tab');
      _activeTab = firstTab ? firstTab.getAttribute('data-props-tab') : '';
    }

    /* tab panels */
    TAB_DEFS.forEach(function (def) {
      var sections = tabsMap[def.id];
      if (!sections || sections.__count === 0) return;

      var panel_ = _el('div', 'bd-props-tab-panel' +
                       (def.id === _activeTab ? ' active' : ''),
                       { 'data-props-panel': def.id });

      Object.keys(sections).forEach(function (sectionId) {
        if (sectionId === '__count') return;
        var props = sections[sectionId];
        if (!props || props.length === 0) return;
        _renderSection(panel_, sectionId, props, widget, schema, defaults);
      });

      body.appendChild(panel_);
    });

    /* ── footer / actions ── */
    var footer = _el('div', 'bd-props-actions');

    var actions = [
      { cls: 'bd-props-act-dup',   icon: 'fa-copy',         label: 'Duplicate',   fn: function () { window.BlankDash && window.BlankDash._duplicateWidget(widget.id); } },
      { cls: 'bd-props-act-del',   icon: 'fa-trash',        label: 'Delete',      fn: function () { window.BlankDash && window.BlankDash._deleteWidget(widget.id); } },
      { cls: 'bd-props-act-copy',  icon: 'fa-clone',        label: 'Copy Style',  fn: function () { _copyStyle(widget); } },
      { cls: 'bd-props-act-paste', icon: 'fa-paste',        label: 'Paste Style', fn: function () { _pasteStyle(widget); } },
      { cls: 'bd-props-act-preset',icon: 'fa-bookmark',     label: 'Save Preset', fn: function () { _savePreset(widget); } },
      { cls: 'bd-props-act-reset', icon: 'fa-undo',         label: 'Reset Tab',   fn: function () { _resetTab(widget, schema, defaults); } }
    ];

    actions.forEach(function (a) {
      var btn = _el('button', a.cls, { title: a.label });
      btn.innerHTML = '<i class="fas ' + a.icon + '"></i> ' + _esc(a.label);
      btn.addEventListener('click', a.fn);
      footer.appendChild(btn);
    });

    body.appendChild(footer);
    panel.appendChild(body);

    /* ── event delegation on body for property changes ── */
    if (window.UIControlKit && typeof window.UIControlKit._wireEvents === 'function') {
      /* colour picker uses two linked inputs (text + native <input type=color>);
         _wireEvents syncs them so the value read reflects whatever the user
         actually changed. Register first so it runs before _onBodyEvent reads. */
      window.UIControlKit._wireEvents(body);
    }
    body.addEventListener('input', _onBodyEvent);
    body.addEventListener('change', _onBodyEvent);

    /* ── wire conditional visibility ── */
    _evalConditions(widget, schema);

    document.body.appendChild(panel);
  }

  /* ── organise properties by tab → section ───────────────────────────── */

  function _organiseByTab(properties, widgetType) {
    var map = {};
    TAB_DEFS.forEach(function (d) { map[d.id] = {}; map[d.id].__count = 0; });

    Object.keys(properties).forEach(function (key) {
      var p   = properties[key];
      var tab = p.tab;
      if (!tab) {
        /* derive tab from group label */
        var g = (p.group || '').toLowerCase();
        tab = GROUP_TO_TAB[g] || GROUP_TO_TAB['content'];
      }
      var section = p.section || _sectionFromGroup(p.group || 'general', tab);
      if (!map[tab]) map[tab] = {};
      if (!map[tab][section]) { map[tab][section] = []; }
      p._key = key;
      map[tab][section].push(p);
      map[tab].__count++;
    });

    return map;
  }

  function _sectionFromGroup(group, tab) {
    /* map group -> section label within its tab */
    var g = (group || '').toLowerCase();
    if (g === 'layout' || g === 'style') return 'General';
    if (tab === 'typography') return group || 'Typography';
    if (g === 'data') return 'Data';
    if (tab === 'chart') return group || 'Chart';
    if (g === 'color system') return 'Colors';
    if (g === 'content' || g === 'kpi' || g === 'main') return 'Content';
    if (g === 'border') return 'Border';
    if (g === 'background') return 'Background';
    if (g === 'spacing') return 'Spacing';
    if (g === 'shadow' || g === 'effects') return 'Shadow & Effects';
    if (g === 'interaction') return 'Interaction';
    if (g === 'responsive') return 'Responsive';
    if (g === 'advanced') return 'Advanced';
    return group || 'General';
  }

  /* ── render one section ─────────────────────────────────────────────── */

  function _renderSection(container, sectionId, props, widget, schema, defaults) {
    var uid = 'props-' + sectionId;

    var header = _el('div', 'bd-props-section-header', { 'data-collapse': uid });
    header.innerHTML = '<span>' + _esc(_humanise(sectionId)) + '</span>' +
                       '<i class="fas fa-chevron-down"></i>';
    header.addEventListener('click', function () {
      var body = document.getElementById(uid);
      if (!body) return;
      var open = body.style.display === 'none';
      body.style.display = open ? '' : 'none';
      header.querySelector('i').className = 'fas fa-chevron-' + (open ? 'down' : 'right');
    });
    container.appendChild(header);

    var body = _el('div', 'bd-props-section-body');
    body.id = uid;

    /* separate compact vs full-width props */
    var compact = [];
    var full    = [];
    props.forEach(function (p) {
      if (COMPACT_TYPES.indexOf(p.type) !== -1) compact.push(p);
      else full.push(p);
    });

    /* compact grid */
    if (compact.length > 0) {
      var grid = _el('div', 'bd-props-grid');
      compact.forEach(function (p) { _renderField(grid, p, widget); });
      body.appendChild(grid);
    }

    /* full-width fields */
    full.forEach(function (p) { _renderField(body, p, widget); });

    container.appendChild(body);
  }

  /* ── render a single property field ─────────────────────────────────── */

  function _renderField(container, p, widget) {
    var field = _el('div', 'bd-props-field');
    if (p.condition) field.setAttribute('data-prop-condition', JSON.stringify(p.condition));

    var label = _el('label', 'bd-props-label');
    label.textContent = p.label || _humanise(p._key);
    field.appendChild(label);

    var ctrl = _el('div', 'bd-props-ctrl', {
      'data-prop-key':  p._key,
      'data-prop-type': p.type || 'text'
    });

    /* build the inline control via UIControlKit if available — render returns HTML */
    var html = '';
    if (window.UIControlKit && typeof window.UIControlKit.render === 'function') {
      try {
        html = window.UIControlKit.render(p, widget) || '';
        if (html.indexOf('Unsupported control') !== -1) html = '';
      } catch (e) { html = ''; }
    }
    if (html) {
      ctrl.innerHTML = html;
    } else {
      /* attach a { key, type } to fallback prop def for getValue compatibility */
      p._key = p._key || p.key;
      _fallbackControl(ctrl, p, widget);
    }

    field.appendChild(ctrl);

    if (p.help) {
      var help = _el('div', 'bd-props-help');
      help.textContent = p.help;
      field.appendChild(help);
    } else if (p.helpText) {
      var help2 = _el('div', 'bd-props-help');
      help2.textContent = p.helpText;
      field.appendChild(help2);
    }

    container.appendChild(field);
  }

  /* ── fallback when UIControlKit.render is absent ────────────────────── */

  function _fallbackControl(container, p, widget) {
    var current = _resolveValue(widget, p._key);
    var input;
    switch (p.type) {
      case 'number':
      case 'slider':
        input = _el('input', 'bd-input', {
          type: 'number', value: current != null ? current : '',
          min: p.min != null ? p.min : '', max: p.max != null ? p.max : '',
          step: p.step || '1'
        });
        container.appendChild(input);
        break;
      case 'color':
        input = _el('input', 'bd-input', { type: 'color', value: current || '#000000' });
        container.appendChild(input);
        break;
      case 'toggle':
        input = _el('input', 'bd-toggle', { type: 'checkbox' });
        input.checked = !!current;
        container.appendChild(input);
        break;
      case 'select':
        input = _el('select', 'bd-select');
        (p.options || []).forEach(function (o) {
          var opt = _el('option');
          if (o == null) return;
          var ov = Array.isArray(o) ? o[0] : (typeof o === 'object' ? o.value : o);
          var ol = Array.isArray(o) ? o[1] : (typeof o === 'object' ? (o.label || o.value) : o);
          opt.value = ov;
          opt.textContent = ol;
          if (opt.value === String(current)) opt.selected = true;
          input.appendChild(opt);
        });
        container.appendChild(input);
        break;
      case 'text':
      case 'textarea':
        input = _el(p.type === 'textarea' ? 'textarea' : 'input', 'bd-input', {
          type: 'text', value: current != null ? current : ''
        });
        if (p.placeholder) input.setAttribute('placeholder', p.placeholder);
        container.appendChild(input);
        break;
      default:
        input = _el('input', 'bd-input', {
          type: 'text', value: current != null ? String(current) : ''
        });
        container.appendChild(input);
    }
  }

  /* ── resolve a nested value from widget via dot path ────────────────── */

  function _resolveValue(widget, key) {
    if (!widget || !key) return undefined;
    /* conditions check the real visual type (chart sub-types store "chart" in
       widget.type and the actual type in widget.config.chartType) */
    if (key === 'type' || key === 'widget.type') return _widgetType(widget);
    var parts = key.split('.');
    var cur = widget;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null) return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }

  /* ── event delegation handler ───────────────────────────────────────── */

  function _onBodyEvent(e) {
    var target = e.target.closest && e.target.closest('[data-prop-key]');
    if (!target) return;

    var key  = target.getAttribute('data-prop-key');
    var type = target.getAttribute('data-prop-type');
    var w    = _current;
    if (!w || !key) return;

    var value;
    if (window.UIControlKit && typeof window.UIControlKit.getValue === 'function') {
      try { value = window.UIControlKit.getValue(target, w); } catch (err) { /* fallback */ }
    }
    if (value === undefined || value === null) {
      value = _readNative(target, type);
    }

    if (window.WidgetPropertySchemas && typeof window.WidgetPropertySchemas.setValue === 'function') {
      window.WidgetPropertySchemas.setValue(key, w, value);
    } else {
      _setValueByPath(w, key, value);
    }

    _applyPropertyChange(w, key, value);
    _evalConditions(w, _getSchema(w));
    _notifyListeners(w, key, value);
  }

  function _readNative(el, type) {
    switch (type) {
      case 'toggle':  return el.querySelector('input[type="checkbox"]') ? el.querySelector('input[type="checkbox"]').checked : el.checked;
      case 'select':  return el.querySelector('select') ? el.querySelector('select').value : el.value;
      default:
        var inp = el.querySelector('input, textarea, select');
        if (!inp) return el.textContent;
        if (inp.type === 'checkbox') return inp.checked;
        return inp.type === 'number' || inp.type === 'range' ? (inp.value === '' ? null : Number(inp.value)) : inp.value;
    }
  }

  function _setValueByPath(obj, path, val) {
    var parts = path.split('.');
    var cur = obj;
    for (var i = 0; i < parts.length - 1; i++) {
      if (cur[parts[i]] == null) cur[parts[i]] = {};
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = val;
  }

  /* ── apply property change to widget DOM ────────────────────────────── */

  function _applyPropertyChange(widget, key, value) {
    if (!window.BlankDash) return;

    /* Normalise the schema key ("widget.x", "config.bgColor", ...) onto a bare
       name so the chrome switch below can match it. "widget.*" keys address the
       widget root; "config.*" keys address the widget's config node. */
    var bare = String(key);
    if (bare.indexOf('widget.') === 0) bare = bare.slice('widget.'.length);
    else if (bare.indexOf('config.') === 0) bare = bare.slice('config.'.length);

    /* chrome (geometry / chrome flags) gets a targeted DOM update */
    var chrome = ['x', 'y', 'width', 'height', 'zIndex', 'locked',
                  'title', 'showTitle', 'plain', 'bgColor', 'borderColor',
                  'borderStyle', 'borderWidth', 'borderRadius', 'borderShapePct'];
    if (chrome.indexOf(bare) !== -1) {
      _applyChromeChange(widget, bare, value);
      return;
    }

    /* every other content property re-renders the widget's DOM cell.
       Route through _onWidgetEdited(id) which locates the .ff-widget-content
       cell and re-paints + re-renders it correctly. */
    if (typeof window.BlankDash._onWidgetEdited === 'function') {
      window.BlankDash._onWidgetEdited(widget.id, key);
      return;
    }

    /* legacy fallback: locate the cell ourselves */
    var cell = document.querySelector('.ff-widget-content[data-id="' + widget.id + '"]');
    if (cell && typeof window.BlankDash._renderWidgetContent === 'function') {
      window.BlankDash._renderWidgetContent(widget, cell);
    }
  }

  function _applyChromeChange(widget, key, value) {
    /* geometry never needs a cached element — blankdash re-queries the DOM
       inside _positionWidget, and widgets may not carry a live `_el` reference */
    if (key === 'x' || key === 'y' || key === 'width' || key === 'height') {
      if (typeof window.BlankDash._positionWidget === 'function') {
        window.BlankDash._positionWidget(widget);
      }
      return;
    }

    var el = widget._el || document.querySelector('.ff-widget[data-id="' + widget.id + '"]');
    if (!el) return;

    switch (key) {
      case 'zIndex':
        el.style.zIndex = value != null ? value : '';
        break;
      case 'locked':
        el.classList.toggle('bd-locked', !!value);
        break;
      case 'title':
        widget.title = value || '';
        if (typeof window.BlankDash._syncPropsWidget === 'function') {
          window.BlankDash._syncPropsWidget(widget, 'title');
        } else {
          var _t = el.querySelector('.ff-widget-title-inner');
          if (_t) _t.textContent = widget.title;
        }
        break;
      case 'showTitle':
        widget.showTitle = !!value;
        var _tn = el.querySelector('.ff-widget-title-inner');
        if (widget.showTitle && widget.title && !_tn) {
          _tn = document.createElement('div');
          _tn.className = 'ff-widget-title-inner';
          _tn.dataset.id = widget.id;
          _tn.textContent = widget.title;
          var _c = el.querySelector('.ff-widget-content');
          if (_c) _c.parentNode.insertBefore(_tn, _c);
          else el.insertBefore(_tn, el.firstChild);
        } else if (!widget.showTitle && _tn) {
          _tn.remove();
        }
        break;
      case 'bgColor':
      case 'borderColor':
        /* the blankdash runtime paints from top-level w.bgColor/w.borderColor,
           so mirror the config value there for the visual to update */
        if (key === 'bgColor') widget.bgColor = value;
        else if (key === 'borderColor') widget.borderColor = value;
        if (typeof window.BlankDash._paintWidget === 'function') {
          window.BlankDash._paintWidget(widget);
        } else if (typeof window.BlankDash._onWidgetEdited === 'function') {
          window.BlankDash._onWidgetEdited(widget.id);
        }
        break;
      case 'plain':
      case 'borderStyle':
      case 'borderWidth':
      case 'borderRadius':
      case 'borderShapePct':
        if (typeof window.BlankDash._paintWidget === 'function') {
          window.BlankDash._paintWidget(widget);
        } else if (typeof window.BlankDash._onWidgetEdited === 'function') {
          window.BlankDash._onWidgetEdited(widget.id);
        }
        break;
    }
  }

  /* ── conditional visibility ─────────────────────────────────────────── */

  function _evalConditions(widget, schema) {
    if (!_panelEl) return;
    var fields = _panelEl.querySelectorAll('.bd-props-field[data-prop-condition]');
    for (var i = 0; i < fields.length; i++) {
      var cond;
      try { cond = JSON.parse(fields[i].getAttribute('data-prop-condition')); } catch (e) { continue; }
      var visible = _checkCondition(cond, widget, schema);
      fields[i].style.display = visible ? '' : 'none';
    }
  }

  function _checkCondition(cond, widget, schema) {
    if (!cond) return true;
    /* Support schema format: { key, equals } or { key, in: [...] } */
    if (cond.key && (cond.equals !== undefined || cond.in !== undefined)) {
      var actual = _resolveValue(widget, cond.key);
      if (cond.equals !== undefined) return actual === cond.equals;
      if (cond.in !== undefined) return Array.isArray(cond.in) && cond.in.indexOf(actual) !== -1;
      return true;
    }
    /* Legacy format: { key, op, value } */
    var key    = cond.key;
    var op     = cond.op    || 'eq';
    var expect = cond.value;
    var val = _resolveValue(widget, key);
    switch (op) {
      case 'eq':      return val == expect;
      case 'neq':     return val != expect;
      case 'gt':      return val > expect;
      case 'lt':      return val < expect;
      case 'truthy':  return !!val;
      case 'falsy':   return !val;
      case 'in':      return Array.isArray(expect) && expect.indexOf(val) !== -1;
      case 'not_in':  return Array.isArray(expect) && expect.indexOf(val) === -1;
      default:        return true;
    }
  }

  /* ── tab switching ──────────────────────────────────────────────────── */

  function _switchTab(tabId) {
    _activeTab = tabId;
    if (!_panelEl) return;

    var tabs = _panelEl.querySelectorAll('.bd-props-tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle('active', tabs[i].getAttribute('data-props-tab') === tabId);
    }

    var panels = _panelEl.querySelectorAll('.bd-props-tab-panel');
    for (var j = 0; j < panels.length; j++) {
      panels[j].classList.toggle('active', panels[j].getAttribute('data-props-panel') === tabId);
    }
  }

  /* ── reset helpers ──────────────────────────────────────────────────── */

  /* getSchema returns a flat array; normalise into {properties, defaults} */
  function _schemaDefaults(schema) {
    var defaults = {};
    var props = schema && schema.properties ? schema.properties : (Array.isArray(schema) ? schema : []);
    if (schema && schema.defaults) return schema.defaults;
    for (var key in props) {
      var p = props[key];
      if (p && p.default !== undefined) defaults[p.key != null ? p.key : key] = p.default;
    }
    return defaults;
  }

  function _propTab(p) {
    if (!p) return 'content';
    if (p.tab) return p.tab;
    return GROUP_TO_TAB[(p.group || '').toLowerCase()] || GROUP_TO_TAB['content'];
  }

  function _resetAll(widget, schema) {
    var defaults = _schemaDefaults(schema);
    Object.keys(defaults).forEach(function (key) {
      _setValueByPath(widget, key, defaults[key]);
      if (window.WidgetPropertySchemas) {
        window.WidgetPropertySchemas.setValue(key, widget, defaults[key]);
      }
    });
    _applyPropertyChange(widget, '', '');
    _refresh();
  }

  function _resetTab(widget, schema, defaults) {
    if (!schema) return;
    var props = schema.properties ? Object.keys(schema.properties).map(function (k) { return schema.properties[k]; })
      : (Array.isArray(schema) ? schema : []);
    props.forEach(function (prop) {
      if (!prop) return;
      if (_propTab(prop) !== _activeTab) return;
      var val = defaults && defaults[prop.key] !== undefined ? defaults[prop.key] : prop.default;
      if (val !== undefined) {
        _setValueByPath(widget, prop.key, val);
        if (window.WidgetPropertySchemas) {
          window.WidgetPropertySchemas.setValue(prop.key, widget, val);
        }
      }
    });
    _applyPropertyChange(widget, '', '');
    _refresh();
  }

  /* ── copy / paste / preset ──────────────────────────────────────────── */

  function _copyStyle(widget) {
    if (window.WidgetPresets && typeof window.WidgetPresets.copyStyle === 'function') {
      window.WidgetPresets.copyStyle(widget);
    }
  }

  function _pasteStyle(widget) {
    if (window.WidgetPresets && typeof window.WidgetPresets.pasteStyle === 'function') {
      window.WidgetPresets.pasteStyle(widget);
      _refresh();
    }
  }

  function _savePreset(widget) {
    if (window.WidgetPresets && typeof window.WidgetPresets.savePreset === 'function') {
      window.WidgetPresets.savePreset(widget);
    }
  }

  /* ── listeners ──────────────────────────────────────────────────────── */

  function _notifyListeners(widget, key, value) {
    for (var i = 0; i < _listeners.length; i++) {
      try { _listeners[i](widget, key, value); } catch (e) { /* ignore */ }
    }
  }

  /* ── destroy / helpers ──────────────────────────────────────────────── */

  function _destroy() {
    if (_panelEl && _panelEl.parentNode) _panelEl.parentNode.removeChild(_panelEl);
    _panelEl = null;
  }

  function _getSchema(widget) {
    if (!widget || !window.WidgetPropertySchemas) return null;
    return window.WidgetPropertySchemas.getSchema(_widgetType(widget));
  }

  function _refresh() {
    if (_current) _build(_current);
  }

  function _humanise(str) {
    return (str || '')
      .replace(/([A-Z])/g, ' $1')
      .replace(/[-_]/g, ' ')
      .replace(/^\w/, function (c) { return c.toUpperCase(); })
      .trim();
  }

  /* ══════════════════════════════════════════════════════════════════════ */
  /*  Public API                                                          */
  /* ══════════════════════════════════════════════════════════════════════ */

  window.PropsPanel = {
    open: function (widget) {
      if (!widget) return;
      _current = widget;
      _activeTab = 'content';
      _build(widget);
    },

    close: function () {
      _destroy();
      _current = null;
    },

    refresh: function () {
      _refresh();
    },

    onChange: function (callback) {
      if (typeof callback === 'function') _listeners.push(callback);
    },

    getCurrentWidget: function () {
      return _current;
    }
  };

})();
