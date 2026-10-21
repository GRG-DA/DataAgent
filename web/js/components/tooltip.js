/**
 * Tooltip – lightweight global tooltip used by dashboard chart renders.
 *
 * The chart renderers (blankdash.js, the BI dashboard bundle) reference a
 * global `Tooltip` object with this API:
 *
 *   Tooltip.multi(rows)  -> HTML string for a table of [label, value] rows
 *   Tooltip.show(html, event)  -> position + display near the cursor
 *   Tooltip.move(event)        -> reposition on mousemove
 *   Tooltip.hide()             -> hide the tooltip
 *
 * This module provides that global. It keeps a single shared, absolutely
 * positioned element so many charts can reuse a lightweight tooltip.
 */
(function () {
  'use strict';

  var _el = null;
  var _offsetX = 14;   // horizontal gap from cursor
  var _offsetY = 14;   // vertical gap from cursor
  var _margin = 8;     // keep inside the viewport edge

  function _esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(String(s)));
    return d.innerHTML;
  }

  function _ensure() {
    if (_el) return _el;
    _el = document.createElement('div');
    _el.className = 'bd-global-tooltip';
    _el.style.position = 'fixed';
    _el.style.zIndex = '99999';
    _el.style.pointerEvents = 'none';
    _el.style.display = 'none';
    _el.style.maxWidth = '360px';
    _el.style.maxHeight = '80vh';
    _el.style.overflowY = 'auto';
    document.body.appendChild(_el);
    return _el;
  }

  function _pos(event) {
    var x = (event && event.clientX != null) ? event.clientX
          : (event && event.pageX != null) ? event.pageX : 0;
    var y = (event && event.clientY != null) ? event.clientY
          : (event && event.pageY != null) ? event.pageY : 0;
    return { x: x, y: y };
  }

  function _hide() {
    if (_el) _el.style.display = 'none';
  }

  window.Tooltip = {
    /* Build a small HTML table from [[label, value], ...] rows.
       Labels are escaped; values may carry simple inline HTML (e.g. <b>). */
    multi: function (rows) {
      if (!rows || !rows.length) return '';
      var html = '<table class="bd-global-tooltip-table" style="border-collapse:collapse;border-spacing:0">';
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i] || [];
        html += '<tr><td style="padding:2px 10px 2px 0;color:inherit;white-space:nowrap">' + _esc(r[0]) + '</td>' +
                '<td style="padding:2px 0;white-space:nowrap;font-weight:600">' + (r[1] == null ? '' : r[1]) + '</td></tr>';
      }
      return html + '</table>';
    },

    show: function (html, event) {
      var el = _ensure();
      el.innerHTML = html || '';
      el.style.display = 'block';
      var p = _pos(event);
      var w = el.offsetWidth || 0;
      var h = el.offsetHeight || 0;
      var vw = window.innerWidth || document.documentElement.clientWidth;
      var vh = window.innerHeight || document.documentElement.clientHeight;
      var left = p.x + _offsetX;
      var top = p.y + _offsetY;
      if (left + w > vw - _margin) left = Math.max(_margin, p.x - w - _offsetX);
      if (top + h > vh - _margin) top = Math.max(_margin, p.y - h - _offsetY);
      el.style.left = left + 'px';
      el.style.top = top + 'px';
    },

    move: function (event) {
      if (!_el || _el.style.display === 'none') return;
      var p = _pos(event);
      var w = _el.offsetWidth || 0;
      var h = _el.offsetHeight || 0;
      var vw = window.innerWidth || document.documentElement.clientWidth;
      var vh = window.innerHeight || document.documentElement.clientHeight;
      var left = p.x + _offsetX;
      var top = p.y + _offsetY;
      if (left + w > vw - _margin) left = Math.max(_margin, p.x - w - _offsetX);
      if (top + h > vh - _margin) top = Math.max(_margin, p.y - h - _offsetY);
      _el.style.left = left + 'px';
      _el.style.top = top + 'px';
    },

    hide: _hide
  };
})();
