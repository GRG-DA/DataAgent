(function () {
  var _idSeq = 0;
  function _uid(prefix) {
    return "up-" + (prefix || "") + (++_idSeq);
  }

  function _esc(s) {
    if (s == null) return "";
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(String(s)));
    return d.innerHTML;
  }

  function _toHex(c) {
    if (!c) return "#000000";
    c = String(c).trim();
    if (/^#[0-9a-f]{6}$/i.test(c)) return c;
    if (/^#[0-9a-f]{3}$/i.test(c)) {
      return "#" + c[1] + c[1] + c[2] + c[2] + c[3] + c[3];
    }
    var m = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (m) {
      var h = function (n) { return ("0" + parseInt(n, 10).toString(16)).slice(-2); };
      return "#" + h(m[1]) + h(m[2]) + h(m[3]);
    }
    return c;
  }

  /* native <input type=color> only accepts #rrggbb — never CSS keywords like
     "transparent", or the browser emits console warnings and ignores the value */
  function _pickColor(c) {
    var h = _toHex(c);
    return /^#[0-9a-f]{6}$/i.test(h) ? h : "#ffffff";
  }

  function _curVal(prop, widget) {
    if (window.WidgetPropertySchemas && typeof window.WidgetPropertySchemas.resolve === "function") {
      var v = window.WidgetPropertySchemas.resolve(prop.key, widget);
      if (v !== undefined && v !== null) return v;
    }
    if (widget && widget.props && widget.props[prop.key] !== undefined) {
      return widget.props[prop.key];
    }
    if (prop.current !== undefined) return prop.current;
    return prop.default;
  }

  var _registry = {};

  function _reg(type, renderFn, getValFn) {
    _registry[type] = { render: renderFn, getValue: getValFn };
  }

  // 1. text
  _reg("text", function (prop, widget) {
    var val = _esc(_curVal(prop, widget) != null ? _curVal(prop, widget) : "");
    var ph = _esc(prop.placeholder || "");
    return '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="text">' +
      '<input type="text" id="' + _uid("txt") + '" value="' + val + '" placeholder="' + ph + '" class="bd-props-input" />' +
      '</div>';
  }, function (root) {
    var inp = root.querySelector("input");
    return inp ? inp.value : "";
  });

  // 1.1 textarea
  _reg("textarea", function (prop, widget) {
    var val = _esc(_curVal(prop, widget) != null ? _curVal(prop, widget) : "");
    var ph = _esc(prop.placeholder || "");
    var rows = prop.rows != null ? prop.rows : 4;
    return '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="textarea">' +
      '<textarea id="' + _uid("txa") + '" rows="' + rows + '" placeholder="' + ph + '" class="bd-props-input" style="resize:vertical;min-height:64px;font-family:var(--font-mono,ui-monospace,monospace);font-size:12px;">' + val + '</textarea>' +
      '</div>';
  }, function (root) {
    var ta = root.querySelector("textarea");
    return ta ? ta.value : "";
  });

  // 2. number
  _reg("number", function (prop, widget) {
    var raw = _curVal(prop, widget);
    var val = raw != null ? _esc(raw) : "";
    var min = prop.min != null ? ' min="' + prop.min + '"' : "";
    var max = prop.max != null ? ' max="' + prop.max + '"' : "";
    var step = prop.step != null ? ' step="' + prop.step + '"' : "";
    return '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="number">' +
      '<input type="number" id="' + _uid("num") + '" value="' + val + '"' + min + max + step + ' class="bd-props-input" />' +
      '</div>';
  }, function (root) {
    var inp = root.querySelector("input");
    if (!inp || inp.value === "") return null;
    return parseFloat(inp.value);
  });

  // 3. color
  _reg("color", function (prop, widget) {
    var raw = _curVal(prop, widget);
    var hex = _toHex(raw || prop.default || "#000000");
    /* native <input type=color> only accepts #rrggbb, never keywords like
       "transparent" — keep the real value in the text box but give the
       picker a valid hex to render */
    var pick = /^#[0-9a-f]{6}$/i.test(hex) ? hex : "#ffffff";
    var tid = _uid("clr");
    var hid = _uid("clh");
    return '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="color">' +
      '<div class="bd-color-row" style="display:flex;gap:6px;align-items:center">' +
      '<input type="text" id="' + tid + '" value="' + _esc(hex) + '" class="bd-props-input" style="flex:1" maxlength="7" />' +
      '<input type="color" id="' + hid + '" value="' + _esc(pick) + '" style="width:36px;height:32px;border:none;padding:0;cursor:pointer;border-radius:4px" />' +
      '</div></div>';
  }, function (root) {
    var inp = root.querySelector('input[type="text"]');
    return inp ? _toHex(inp.value) : "#000000";
  });

  // 4. toggle
  _reg("toggle", function (prop, widget) {
    var raw = _curVal(prop, widget);
    var on = raw === true || raw === "true" || raw === 1;
    var tid = _uid("tgl");
    return '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="toggle">' +
      '<label class="bd-props-switch">' +
      '<input type="checkbox" id="' + tid + '"' + (on ? " checked" : "") + ' />' +
      '<span class="bd-props-slider"></span>' +
      '</label></div>';
  }, function (root) {
    var cb = root.querySelector("input[type='checkbox']");
    return cb ? cb.checked : false;
  });

  // 5. select
  _reg("select", function (prop, widget) {
    var cur = _curVal(prop, widget);
    var opts = prop.options || [];
    var tid = _uid("sel");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="select">' +
      '<select id="' + tid + '" class="bd-props-input">';
    for (var i = 0; i < opts.length; i++) {
      var o = opts[i];
      if (o == null) continue;
      var v, l;
      if (Array.isArray(o)) { v = o[0]; l = o[1]; }
      else if (typeof o === "object") { v = o.value; l = o.label || o.value; }
      else { v = o; l = o; }
      var sel = String(v) === String(cur) ? " selected" : "";
      html += '<option value="' + _esc(v) + '"' + sel + '>' + _esc(l) + '</option>';
    }
    html += '</select></div>';
    return html;
  }, function (root) {
    var sel = root.querySelector("select");
    return sel ? sel.value : "";
  });

  // 6. slider
  _reg("slider", function (prop, widget) {
    var cur = _curVal(prop, widget);
    if (cur == null) cur = prop.default != null ? prop.default : 0;
    var min = prop.min != null ? prop.min : 0;
    var max = prop.max != null ? prop.max : 100;
    var step = prop.step != null ? prop.step : 1;
    var tid = _uid("sld");
    var bid = _uid("slb");
    return '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="slider">' +
      '<div class="bd-slider-row" style="display:flex;align-items:center;gap:8px">' +
      '<input type="range" id="' + tid + '" min="' + min + '" max="' + max + '" step="' + step + '" value="' + _esc(cur) + '" style="flex:1" />' +
      '<span id="' + bid + '" class="bd-slider-badge" style="min-width:36px;text-align:center;font-size:12px;background:#2a2a3e;padding:2px 6px;border-radius:4px">' + _esc(cur) + '</span>' +
      '</div></div>';
  }, function (root) {
    var inp = root.querySelector('input[type="range"]');
    return inp ? parseFloat(inp.value) : 0;
  });

  // 6b. "range" (used by many schemas: opacity, corner shape, etc.) renders the same slider control
  var _sliderReg = _registry.slider;
  if (_sliderReg) {
    _registry.range = { render: _sliderReg.render, getValue: _sliderReg.getValue };
  }

  // 7. font
  _reg("font", function (prop, widget) {
    var cur = _curVal(prop, widget) || "Inter";
    var fonts = ["Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "Poppins", "Source Sans Pro", "Nunito", "IBM Plex Sans", "system-ui"];
    var tid = _uid("fnt");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="font">' +
      '<select id="' + tid + '" class="bd-props-input">';
    for (var i = 0; i < fonts.length; i++) {
      var sel = fonts[i] === cur ? " selected" : "";
      html += '<option value="' + _esc(fonts[i]) + '"' + sel + '>' + _esc(fonts[i]) + '</option>';
    }
    html += '</select></div>';
    return html;
  }, function (root) {
    var sel = root.querySelector("select");
    return sel ? sel.value : "Inter";
  });

  // 8. alignment
  _reg("alignment", function (prop, widget) {
    var cur = _curVal(prop, widget) || "left";
    var items = [
      { val: "left", icon: "fa-align-left" },
      { val: "center", icon: "fa-align-center" },
      { val: "right", icon: "fa-align-right" },
      { val: "justify", icon: "fa-align-justify" }
    ];
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="alignment">' +
      '<div class="bd-align-group" style="display:flex;gap:0">';
    for (var i = 0; i < items.length; i++) {
      var active = items[i].val === cur ? " active" : "";
      html += '<button type="button" class="bd-align-btn bd-props-btn' + active + '" data-val="' + items[i].val + '" style="padding:6px 10px;border:1px solid #3a3a5c;background:transparent;color:#ccc;cursor:pointer;display:flex;align-items:center;justify-content:center">' +
        '<i class="fas ' + items[i].icon + '"></i></button>';
    }
    html += '</div></div>';
    return html;
  }, function (root) {
    var btn = root.querySelector(".bd-align-btn.active");
    return btn ? btn.getAttribute("data-val") : "left";
  });

  // 9. spacing
  _reg("spacing", function (prop, widget) {
    var cur = _curVal(prop, widget) || {};
    var sides = ["top", "right", "bottom", "left"];
    var icons = { top: "\u25B2", right: "\u25B6", bottom: "\u25BC", left: "\u25C0" };
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="spacing">' +
      '<div class="bd-spacing-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:4px">';
    for (var i = 0; i < sides.length; i++) {
      var s = sides[i];
      var v = cur[s] != null ? cur[s] : 0;
      var tid = _uid("spc");
      html += '<div style="display:flex;align-items:center;gap:4px">' +
        '<span style="font-size:10px;color:#888;width:14px">' + icons[s] + '</span>' +
        '<input type="number" data-side="' + s + '" id="' + tid + '" value="' + _esc(v) + '" min="0" class="bd-props-input" style="width:100%" />' +
        '</div>';
    }
    html += '</div></div>';
    return html;
  }, function (root) {
    var result = { top: 0, right: 0, bottom: 0, left: 0 };
    var inputs = root.querySelectorAll("input[data-side]");
    for (var i = 0; i < inputs.length; i++) {
      var side = inputs[i].getAttribute("data-side");
      result[side] = inputs[i].value === "" ? 0 : parseFloat(inputs[i].value);
    }
    return result;
  });

  // 10. border
  _reg("border", function (prop, widget) {
    var cur = _curVal(prop, widget) || {};
    var w = cur.width != null ? cur.width : 0;
    var s = cur.style || "solid";
    var c = _toHex(cur.color || "#000000");
    var styles = ["solid", "dashed", "dotted", "double", "none"];
    var wid = _uid("bdw");
    var sid = _uid("bds");
    var cid = _uid("bdc");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="border">' +
      '<div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">' +
      '<input type="number" id="' + wid + '" value="' + _esc(w) + '" min="0" max="20" class="bd-props-input" style="width:48px" title="Width" />' +
      '<select id="' + sid + '" class="bd-props-input" style="flex:1">';
    for (var i = 0; i < styles.length; i++) {
      var sel = styles[i] === s ? " selected" : "";
      html += '<option value="' + styles[i] + '"' + sel + '>' + styles[i] + '</option>';
    }
    html += '</select>' +
      '<input type="color" id="' + cid + '" value="' + _esc(_pickColor(c)) + '" style="width:32px;height:28px;border:none;padding:0;cursor:pointer;border-radius:4px" />' +
      '</div></div>';
    return html;
  }, function (root) {
    var wInp = root.querySelector("input[type='number']");
    var sSel = root.querySelector("select");
    var cInp = root.querySelector("input[type='color']");
    return {
      width: wInp && wInp.value !== "" ? parseFloat(wInp.value) : 0,
      style: sSel ? sSel.value : "solid",
      color: cInp ? cInp.value : "#000000"
    };
  });

  // 11. gradient
  _reg("gradient", function (prop, widget) {
    var cur = _curVal(prop, widget) || "";
    var c1 = "#7B61FF", c2 = "#3B82F6", angle = 135;
    var gm = cur.match(/linear-gradient\((\d+)deg,\s*(#[0-9a-f]{3,6})\s*(?:0%|),\s*(#[0-9a-f]{3,6})\s*(?:100%|)\)/i);
    if (gm) { angle = parseInt(gm[1]); c1 = gm[2]; c2 = gm[3]; }
    var gid1 = _uid("gr1");
    var gid2 = _uid("gr2");
    var aid = _uid("gra");
    var pid = _uid("grp");
    return '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="gradient">' +
      '<div style="display:flex;gap:4px;align-items:center;margin-bottom:4px">' +
      '<input type="color" id="' + gid1 + '" value="' + _esc(_pickColor(c1)) + '" style="width:32px;height:28px;border:none;padding:0;cursor:pointer;border-radius:4px" />' +
      '<input type="color" id="' + gid2 + '" value="' + _esc(_pickColor(c2)) + '" style="width:32px;height:28px;border:none;padding:0;cursor:pointer;border-radius:4px" />' +
      '<input type="number" id="' + aid + '" value="' + angle + '" min="0" max="360" class="bd-props-input" style="width:52px" title="Angle" />' +
      '<span style="font-size:11px;color:#888">deg</span>' +
      '</div>' +
      '<div id="' + pid + '" style="height:24px;border-radius:6px;background:linear-gradient(' + angle + 'deg,' + c1 + ',' + c2 + ')"></div>' +
      '</div>';
  }, function (root) {
    var inputs = root.querySelectorAll("input[type='color']");
    var angleInp = root.querySelector("input[type='number']");
    var c1 = inputs[0] ? inputs[0].value : "#7B61FF";
    var c2 = inputs[1] ? inputs[1].value : "#3B82F6";
    var angle = angleInp && angleInp.value !== "" ? parseInt(angleInp.value) : 135;
    return "linear-gradient(" + angle + "deg," + c1 + "," + c2 + ")";
  });

  // 12. multicolor
  _reg("multicolor", function (prop, widget) {
    var cur = _curVal(prop, widget);
    if (!Array.isArray(cur)) cur = [];
    var palette = window.UIControlKit ? window.UIControlKit.themePalette() : ["#7B61FF", "#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#EC4899"];
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="multicolor">' +
      '<div class="bd-mc-palette" style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px">';
    for (var i = 0; i < palette.length; i++) {
      var on = cur.indexOf(palette[i]) !== -1;
      html += '<button type="button" class="bd-mc-swatch' + (on ? " active" : "") + '" data-color="' + palette[i] + '" style="width:22px;height:22px;border-radius:4px;border:2px solid ' + (on ? "#fff" : "transparent") + ";background:" + palette[i] + ";cursor:pointer;outline:none\"></button>";
    }
    html += '</div><div class="bd-mc-custom" style="display:flex;gap:4px;flex-wrap:wrap">';
    for (var j = 0; j < 3; j++) {
      var cid = _uid("mcc");
      var v = cur[j] || "#000000";
      html += '<input type="color" id="' + cid + '" data-idx="' + j + '" value="' + _esc(_pickColor(v)) + '" style="width:28px;height:24px;border:none;padding:0;cursor:pointer;border-radius:4px" />';
    }
    html += '</div></div>';
    return html;
  }, function (root) {
    var colors = [];
    var swatches = root.querySelectorAll(".bd-mc-swatch.active");
    for (var i = 0; i < swatches.length; i++) {
      colors.push(swatches[i].getAttribute("data-color"));
    }
    var customs = root.querySelectorAll("input[type='color']");
    for (var j = 0; j < customs.length; j++) {
      if (colors.indexOf(customs[j].value) === -1) colors.push(customs[j].value);
    }
    return colors;
  });

  // 13. icon
  _reg("icon", function (prop, widget) {
    var cur = _curVal(prop, widget) || "";
    var icons = [
      "fa-chart-line", "fa-chart-bar", "fa-chart-pie", "fa-users", "fa-dollar-sign",
      "fa-percent", "fa-bolt", "fa-star", "fa-heart", "fa-arrow-up",
      "fa-arrow-down", "fa-check", "fa-times", "fa-info", "fa-warning",
      "fa-calendar", "fa-clock", "fa-globe", "fa-cog", "fa-home",
      "fa-search", "fa-plus", "fa-minus", "fa-edit", "fa-trash",
      "fa-download", "fa-upload", "fa-share", "fa-lock", "fa-unlock",
      "fa-eye", "fa-bookmark", "fa-flag", "fa-bell", "fa-envelope",
      "fa-phone", "fa-map", "fa-tag", "fa-folder", "fa-file",
      "fa-image", "fa-video", "fa-music", "fa-code", "fa-database",
      "fa-server", "fa-cloud", "fa-shield"
    ];
    var tid = _uid("icn");
    var gid = _uid("icg");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="icon">' +
      '<input type="text" id="' + tid + '" placeholder="Search icons..." value="' + _esc(cur) + '" class="bd-props-input" style="margin-bottom:4px" />' +
      '<div id="' + gid + '" class="bd-icon-grid" style="display:grid;grid-template-columns:repeat(6,1fr);gap:2px;max-height:140px;overflow-y:auto">';
    for (var i = 0; i < icons.length; i++) {
      var sel = icons[i] === cur ? " active" : "";
      html += '<button type="button" class="bd-icon-btn' + sel + '" data-icon="' + icons[i] + '" title="' + icons[i] + '" style="padding:5px;border:1px solid ' + (sel ? "#7B61FF" : "#3a3a5c") + ";background:" + (sel ? "rgba(123,97,255,0.2)" : "transparent") + ";color:#ccc;cursor:pointer;border-radius:4px;text-align:center;font-size:12px\"><i class=\"fas " + icons[i] + "\"></i></button>";
    }
    html += '</div></div>';
    return html;
  }, function (root) {
    var btn = root.querySelector(".bd-icon-btn.active");
    return btn ? btn.getAttribute("data-icon") : "";
  });

  // 14. columnpicker
  _reg("columnpicker", function (prop, widget) {
    var cur = _curVal(prop, widget) || "";
    var columns = [];
    if (widget && widget.table) {
      var tbl = null;
      if (window.App && window.App.state && window.App.state.tables) {
        tbl = window.App.state.tables[widget.table];
      }
      if (tbl && tbl.columns) {
        columns = tbl.columns;
      }
    }
    if (columns.length === 0 && prop.columns) {
      columns = prop.columns;
    }
    var tid = _uid("col");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="columnpicker">' +
      '<select id="' + tid + '" class="bd-props-input">';
    html += '<option value="">-- Select Column --</option>';
    for (var i = 0; i < columns.length; i++) {
      var cn = typeof columns[i] === "string" ? columns[i] : columns[i].name;
      var sel = cn === cur ? " selected" : "";
      html += '<option value="' + _esc(cn) + '"' + sel + '>' + _esc(cn) + '</option>';
    }
    html += '</select></div>';
    return html;
  }, function (root) {
    var sel = root.querySelector("select");
    return sel ? sel.value : "";
  });

  // 15. pagepicker
  _reg("pagepicker", function (prop, widget) {
    var cur = _curVal(prop, widget);
    if (cur == null) cur = 0;
    var pages = [];
    if (window.BlankDash && typeof window.BlankDash.getPages === "function") {
      pages = window.BlankDash.getPages();
    } else if (prop.pages) {
      pages = prop.pages;
    }
    var tid = _uid("pgp");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="pagepicker">' +
      '<select id="' + tid + '" class="bd-props-input">';
    for (var i = 0; i < pages.length; i++) {
      var p = pages[i];
      var label = typeof p === "object" ? (p.name || p.title || "Page " + i) : p;
      var val = typeof p === "object" ? i : p;
      var sel = Number(val) === Number(cur) ? " selected" : "";
      html += '<option value="' + val + '"' + sel + '>' + _esc(label) + '</option>';
    }
    html += '</select></div>';
    return html;
  }, function (root) {
    var sel = root.querySelector("select");
    return sel ? (sel.value === "" ? 0 : parseInt(sel.value)) : 0;
  });

  // 16. radiogroup
  _reg("radiogroup", function (prop, widget) {
    var cur = _curVal(prop, widget);
    var opts = prop.options || [];
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="radiogroup">' +
      '<div class="bd-radio-group" style="display:flex;gap:0">';
    for (var i = 0; i < opts.length; i++) {
      var o = opts[i];
      var v = typeof o === "object" ? o.value : o;
      var l = typeof o === "object" ? o.label : o;
      var active = String(v) === String(cur) ? " active" : "";
      html += '<button type="button" class="bd-radio-btn bd-props-btn' + active + '" data-val="' + _esc(v) + '" style="padding:5px 10px;border:1px solid #3a3a5c;background:' + (active ? "rgba(123,97,255,0.25)" : "transparent") + ";color:#ccc;cursor:pointer;font-size:12px\">" +
        _esc(l) + '</button>';
    }
    html += '</div></div>';
    return html;
  }, function (root) {
    var btn = root.querySelector(".bd-radio-btn.active");
    return btn ? btn.getAttribute("data-val") : "";
  });

  // 17. tableSelect — dropdown of tables from the app's warehouse state
  _reg("tableSelect", function (prop, widget) {
    var cur = _curVal(prop, widget) || "";
    var tables = {};
    try { tables = (window.App && window.App.state && window.App.state.tables) || {}; } catch (e) {}
    var names = Object.keys(tables);
    if (!names.length && prop.options) {
      (Array.isArray(prop.options) ? prop.options : []).forEach(function (o) {
        if (o == null) return;
        names.push(Array.isArray(o) ? o[0] : (typeof o === "object" ? o.value : o));
      });
    }
    var tid = _uid("tbl");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="tableSelect">' +
      '<select id="' + tid + '" class="bd-select bd-props-input">';
    html += '<option value="">' + _esc(prop.placeholder || "Select table...") + '</option>';
    for (var i = 0; i < names.length; i++) {
      var sel = names[i] === cur ? " selected" : "";
      html += '<option value="' + _esc(names[i]) + '"' + sel + '>' + _esc(names[i]) + '</option>';
    }
    html += '</select></div>';
    return html;
  }, function (root) {
    var sel = root.querySelector("select");
    return sel ? sel.value : "";
  });

  // 18. measureSelect — dropdown of registered measures (measure registry)
  _reg("measureSelect", function (prop, widget) {
    var cur = _curVal(prop, widget) || "";
    var ms = [];
    try { ms = (window.App && App.measures) || []; } catch (e) {}
    if (!ms.length && prop.options) {
      (Array.isArray(prop.options) ? prop.options : []).forEach(function (o) {
        ms.push(o);
      });
    }
    var tid = _uid("ms");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="measureSelect">' +
      '<select id="' + tid + '" class="bd-select bd-props-input">' +
      '<option value="">' + _esc(prop.placeholder || "Select measure...") + '</option>';
    for (var i = 0; i < ms.length; i++) {
      var m = ms[i];
      if (!m || !m.id) continue;
      var sel = String(m.id) === String(cur) ? " selected" : "";
      html += '<option value="' + _esc(m.id) + '"' + sel + '>' + _esc(m.name || m.id) + '</option>';
    }
    html += '</select></div>';
    return html;
  }, function (root) {
    var sel = root.querySelector("select");
    return sel ? sel.value : "";
  });

  // 19. colorArray — palette swatches + custom picks (like multicolor)
  var _multiCore = (function () {
    var create = function (prop, widget) {
      var cur = _curVal(prop, widget);
      if (!Array.isArray(cur)) cur = [];
      var palette = window.UIControlKit ? window.UIControlKit.themePalette() : ["#7B61FF", "#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#EC4899"];
      var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="' + _esc(prop.type) + '">' +
        '<div class="bd-mc-palette" style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px">';
      for (var i = 0; i < palette.length; i++) {
        var on = cur.indexOf(palette[i]) !== -1;
        html += '<button type="button" class="bd-mc-swatch' + (on ? " active" : "") + '" data-color="' + palette[i] + '" style="width:22px;height:22px;border-radius:4px;border:2px solid ' + (on ? "#fff" : "transparent") + ";background:" + palette[i] + ";cursor:pointer;outline:none\"></button>";
      }
      html += '</div><div class="bd-mc-custom" style="display:flex;gap:4px;flex-wrap:wrap">';
      var customCount = Math.max(cur.length, prop.maxColors || 4);
      for (var j = 0; j < customCount && j < 8; j++) {
        var cid = _uid("mcc");
        var v = cur[j] || "#000000";
        html += '<input type="color" id="' + cid + '" data-idx="' + j + '" value="' + _esc(_pickColor(v)) + '" style="width:28px;height:24px;border:none;padding:0;cursor:pointer;border-radius:4px" />';
      }
      html += '</div></div>';
      return html;
    };
    var get = function (root) {
      var colors = [];
      var swatches = root.querySelectorAll(".bd-mc-swatch.active");
      for (var i = 0; i < swatches.length; i++) colors.push(swatches[i].getAttribute("data-color"));
      var customs = root.querySelectorAll("input[type='color']");
      for (var j = 0; j < customs.length; j++) {
        if (colors.indexOf(customs[j].value) === -1) colors.push(customs[j].value);
      }
      return colors;
    };
    return { create: create, get: get };
  })();
  _reg("colorArray", function (prop, widget) {
    return _multiCore.create(prop, widget);
  }, function (root) {
    return _multiCore.get(root);
  });

  // 19.1 referenceLines — list editor for reference lines/bands
  // Each row: value (number), label (text), color (color), type (line|band), bandTo (number)
  var _refLinesCore = (function () {
    var create = function (prop, widget) {
      var cur = _curVal(prop, widget);
      if (!Array.isArray(cur)) cur = [];
      var rows = cur.length ? cur : [{ value: 0, label: "", color: "#EF4444", type: "line", bandTo: 0 }];
      var html = '<div class="bd-ctrl bd-reflines" data-prop-key="' + _esc(prop.key) + '" data-prop-type="referenceLines">';
      html += '<div class="bd-reflines-list" style="display:flex;flex-direction:column;gap:6px;">';
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i] || {};
        var rid = _uid("rl");
        var isBand = r.type === "band";
        html += '<div class="bd-refline-row" data-idx="' + i + '" style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;border:1px solid #e5e7eb;border-radius:6px;padding:4px;">' +
          '<input type="number" class="bd-rl-value" value="' + _esc(r.value != null ? r.value : 0) + '" step="any" style="width:64px;" title="Value" />' +
          '<input type="text" class="bd-rl-label" value="' + _esc(r.label != null ? r.label : "") + '" placeholder="Label" style="width:70px;" />' +
          '<input type="color" class="bd-rl-color" value="' + _esc(_pickColor(r.color || "#EF4444")) + '" style="width:26px;height:22px;padding:0;border:none;cursor:pointer;" />' +
          '<select class="bd-rl-type" style="width:58px;">' +
            '<option value="line"' + (isBand ? "" : " selected") + '>Line</option>' +
            '<option value="band"' + (isBand ? " selected" : "") + '>Band</option>' +
          '</select>' +
          '<input type="number" class="bd-rl-bandto" value="' + _esc(r.bandTo != null ? r.bandTo : 0) + '" step="any" placeholder="To" title="Band upper value" style="width:56px;' + (isBand ? "" : "display:none;") + '" />' +
          '<button type="button" class="bd-rl-del" title="Remove" style="border:none;background:transparent;color:#ef4444;cursor:pointer;font-size:12px;">\u2715</button>' +
        '</div>';
      }
      html += '</div>';
      html += '<button type="button" class="bd-rl-add" style="margin-top:6px;border:1px dashed #cbd5e1;background:transparent;color:#6b7280;border-radius:6px;padding:3px 8px;cursor:pointer;font-size:12px;">+ Add reference line</button>';
      html += '</div>';
      return html;
    };
    var get = function (root) {
      var out = [];
      var rows = root.querySelectorAll(".bd-refline-row");
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        var type = row.querySelector(".bd-rl-type").value;
        var bandTo = row.querySelector(".bd-rl-bandto");
        out.push({
          value: parseFloat(row.querySelector(".bd-rl-value").value) || 0,
          label: row.querySelector(".bd-rl-label").value || "",
          color: row.querySelector(".bd-rl-color").value || "#EF4444",
          type: type,
          bandTo: type === "band" ? (parseFloat(bandTo.value) || 0) : undefined
        });
      }
      return out;
    };
    return { create: create, get: get };
  })();
  _reg("referenceLines", function (prop, widget) {
    return _refLinesCore.create(prop, widget);
  }, function (root) {
    return _refLinesCore.get(root);
  });

  // 19. fontFamily — font stack picker (matches the leading font family)
  _reg("fontFamily", function (prop, widget) {
    var cur = _curVal(prop, widget) || "Inter, system-ui, sans-serif";
    var stacks = [
      ["Inter, system-ui, sans-serif", "Inter"],
      ["Roboto, system-ui, sans-serif", "Roboto"],
      ["'Open Sans', system-ui, sans-serif", "Open Sans"],
      ["Lato, sans-serif", "Lato"],
      ["Montserrat, sans-serif", "Montserrat"],
      ["Poppins, sans-serif", "Poppins"],
      ["'Source Sans Pro', sans-serif", "Source Sans Pro"],
      ["Nunito, sans-serif", "Nunito"],
      ["'IBM Plex Sans', sans-serif", "IBM Plex Sans"],
      ["Merriweather, serif", "Merriweather"],
      ["Georgia, serif", "Georgia"],
      ["system-ui, sans-serif", "System UI"],
      ["ui-monospace, monospace", "Mono"]
    ];
    var curLead = String(cur).split(",")[0].replace(/['"]/g, "").trim().toLowerCase();
    var tid = _uid("fml");
    var html = '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="fontFamily">' +
      '<select id="' + tid + '" class="bd-props-input">';
    var matched = false;
    for (var i = 0; i < stacks.length; i++) {
      var lead = stacks[i][1].toLowerCase();
      var sel = lead === curLead || stacks[i][0] === cur ? " selected" : "";
      if (sel) matched = true;
      html += '<option value="' + _esc(stacks[i][0]) + '"' + sel + '>' + _esc(stacks[i][1]) + '</option>';
    }
    if (!matched && String(cur).trim()) {
      html += '<option value="' + _esc(cur) + '" selected>' + _esc(String(cur).split(",")[0]) + '</option>';
    }
    html += '</select></div>';
    return html;
  }, function (root) {
    var sel = root.querySelector("select");
    return sel ? sel.value : "Inter, system-ui, sans-serif";
  });

  // 20. iconPicker — same control as "icon"
  var _iconReg = _registry.icon;
  if (_iconReg) {
    _registry.iconPicker = { render: _iconReg.render, getValue: _iconReg.getValue };
  }

  // 21. date — native date picker
  _reg("date", function (prop, widget) {
    var cur = _curVal(prop, widget);
    var val = cur != null ? String(cur) : "";
    var tid = _uid("dat");
    return '<div class="bd-ctrl" data-prop-key="' + _esc(prop.key) + '" data-prop-type="date">' +
      '<input type="date" id="' + tid + '" value="' + _esc(val) + '" class="bd-props-input" />' +
      '</div>';
  }, function (root) {
    var inp = root.querySelector("input");
    return inp ? inp.value : "";
  });

  // Event delegation wiring (called after inserting controls into DOM)
  function _wireEvents(root) {
    if (!root) return;

    // Color sync
    root.addEventListener("input", function (e) {
      var ctrl = e.target.closest("[data-prop-type='color']");
      if (ctrl) {
        var textInp = ctrl.querySelector('input[type="text"]');
        var colorInp = ctrl.querySelector('input[type="color"]');
        if (e.target === textInp && colorInp) {
          if (/^#[0-9a-f]{6}$/i.test(textInp.value)) colorInp.value = textInp.value;
        } else if (e.target === colorInp && textInp) {
          textInp.value = colorInp.value;
        }
      }

      // Gradient live preview
      var gCtrl = e.target.closest("[data-prop-type='gradient']");
      if (gCtrl) {
        var gc = gCtrl.querySelectorAll("input[type='color']");
        var ga = gCtrl.querySelector("input[type='number']");
        var gp = gCtrl.querySelector("[id]");
        if (gc.length >= 2 && ga) {
          var angle = ga.value || "135";
          var previewEl = gCtrl.querySelector("div:last-child");
          if (previewEl) {
            previewEl.style.background = "linear-gradient(" + angle + "deg," + gc[0].value + "," + gc[1].value + ")";
          }
        }
      }

      // Slider badge
      var sCtrl = e.target.closest("[data-prop-type='slider']");
      if (sCtrl && e.target.type === "range") {
        var badge = sCtrl.querySelector(".bd-slider-badge");
        if (badge) badge.textContent = e.target.value;
      }
    });

    root.addEventListener("change", function (e) {
      // Color sync on change too
      var ctrl = e.target.closest("[data-prop-type='color']");
      if (ctrl) {
        var textInp = ctrl.querySelector('input[type="text"]');
        var colorInp = ctrl.querySelector('input[type="color"]');
        if (e.target === colorInp && textInp) textInp.value = colorInp.value;
      }

      // Gradient preview
      var gCtrl = e.target.closest("[data-prop-type='gradient']");
      if (gCtrl) {
        var gc = gCtrl.querySelectorAll("input[type='color']");
        var ga = gCtrl.querySelector("input[type='number']");
        if (gc.length >= 2 && ga) {
          var previewEl = gCtrl.querySelector("div:last-child");
          if (previewEl) {
            previewEl.style.background = "linear-gradient(" + (ga.value || "135") + "deg," + gc[0].value + "," + gc[1].value + ")";
          }
        }
      }
    });

    // Click delegation: alignment, icon swatches, multicolor swatches, radiogroup
    root.addEventListener("click", function (e) {
      var btn;
      btn = e.target.closest(".bd-align-btn");
      if (btn) {
        var group = btn.closest(".bd-align-group");
        if (group) {
          var siblings = group.querySelectorAll(".bd-align-btn");
          for (var i = 0; i < siblings.length; i++) siblings[i].classList.remove("active");
          btn.classList.add("active");
        }
        return;
      }

      btn = e.target.closest(".bd-icon-btn");
      if (btn) {
        var grid = btn.closest(".bd-icon-grid");
        if (grid) {
          var siblings = grid.querySelectorAll(".bd-icon-btn");
          for (var i = 0; i < siblings.length; i++) {
            siblings[i].classList.remove("active");
            siblings[i].style.borderColor = "#3a3a5c";
            siblings[i].style.background = "transparent";
          }
          btn.classList.add("active");
          btn.style.borderColor = "#7B61FF";
          btn.style.background = "rgba(123,97,255,0.2)";
        }
        return;
      }

      btn = e.target.closest(".bd-mc-swatch");
      if (btn) {
        btn.classList.toggle("active");
        var on = btn.classList.contains("active");
        btn.style.borderColor = on ? "#fff" : "transparent";
        return;
      }

      btn = e.target.closest(".bd-radio-btn");
      if (btn) {
        var group = btn.closest(".bd-radio-group");
        if (group) {
          var siblings = group.querySelectorAll(".bd-radio-btn");
          for (var i = 0; i < siblings.length; i++) {
            siblings[i].classList.remove("active");
            siblings[i].style.background = "transparent";
          }
          btn.classList.add("active");
          btn.style.background = "rgba(123,97,255,0.25)";
        }
        return;
      }

      // Reference-lines list editor: add / remove rows
      btn = e.target.closest(".bd-rl-add");
      if (btn) {
        var rlCtrl = btn.closest("[data-prop-type='referenceLines']");
        if (rlCtrl) {
          var list = rlCtrl.querySelector(".bd-reflines-list");
          var row = document.createElement("div");
          row.className = "bd-refline-row";
          row.style.cssText = "display:flex;gap:4px;align-items:center;flex-wrap:wrap;border:1px solid #e5e7eb;border-radius:6px;padding:4px;";
          row.innerHTML =
            '<input type="number" class="bd-rl-value" value="0" step="any" style="width:64px;" title="Value" />' +
            '<input type="text" class="bd-rl-label" value="" placeholder="Label" style="width:70px;" />' +
            '<input type="color" class="bd-rl-color" value="#EF4444" style="width:26px;height:22px;padding:0;border:none;cursor:pointer;" />' +
            '<select class="bd-rl-type" style="width:58px;">' +
              '<option value="line" selected>Line</option>' +
              '<option value="band">Band</option>' +
            '</select>' +
            '<input type="number" class="bd-rl-bandto" value="0" step="any" placeholder="To" title="Band upper value" style="width:56px;display:none;" />' +
            '<button type="button" class="bd-rl-del" title="Remove" style="border:none;background:transparent;color:#ef4444;cursor:pointer;font-size:12px;">\u2715</button>';
          list.appendChild(row);
        }
        return;
      }

      btn = e.target.closest(".bd-rl-del");
      if (btn) {
        var rowEl = btn.closest(".bd-refline-row");
        if (rowEl) rowEl.remove();
        return;
      }
    });

    // Reference-lines: toggle bandTo visibility when type changes
    root.addEventListener("change", function (e) {
      var typeSel = e.target.closest(".bd-rl-type");
      if (typeSel) {
        var rowEl = typeSel.closest(".bd-refline-row");
        if (rowEl) {
          var bandTo = rowEl.querySelector(".bd-rl-bandto");
          if (bandTo) bandTo.style.display = typeSel.value === "band" ? "" : "none";
        }
      }
    });

    // Icon search filter
    root.addEventListener("input", function (e) {
      var ctrl = e.target.closest("[data-prop-type='icon']");
      if (ctrl && e.target.tagName === "INPUT" && e.target.type === "text") {
        var query = e.target.value.toLowerCase();
        var grid = ctrl.querySelector(".bd-icon-grid");
        if (grid) {
          var btns = grid.querySelectorAll(".bd-icon-btn");
          for (var i = 0; i < btns.length; i++) {
            var icon = btns[i].getAttribute("data-icon") || "";
            btns[i].style.display = icon.toLowerCase().indexOf(query) !== -1 ? "" : "none";
          }
        }
      }
    });
  }

  var _presetColors = [
    "#7B61FF", "#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#EC4899",
    "#8B5CF6", "#14B8A6", "#06B6D4", "#F97316", "#6366F1", "#0EA5E9"
  ];

  window.UIControlKit = {
    register: function (type, handlers) {
      if (handlers.render && handlers.getValue) {
        _registry[type] = handlers;
      }
    },
    render: function (propDef, widget) {
      var type = propDef.type || "text";
      var handler = _registry[type];
      if (!handler) {
        return '<div class="bd-ctrl" data-prop-key="' + _esc(propDef.key) + '" data-prop-type="' + _esc(type) + '">' +
          '<span style="color:#888">Unsupported control: ' + _esc(type) + '</span></div>';
      }
      return handler.render(propDef, widget);
    },
    getValue: function (rootEl, widget) {
      if (!rootEl) return null;
      var type = rootEl.getAttribute("data-prop-type");
      var key = rootEl.getAttribute("data-prop-key");
      var handler = _registry[type];
      if (!handler) return null;
      var val = handler.getValue(rootEl, widget);
      if (val === undefined || val === null) {
        var propDef = { key: key, "default": null };
        var def = _curVal(propDef, widget);
        if (def !== undefined) return def;
      }
      return val;
    },
    themePalette: function () {
      return _presetColors.slice();
    },
    _wireEvents: _wireEvents,
    _toHex: _toHex,
    _esc: _esc,
    _uid: _uid
  };
})();
