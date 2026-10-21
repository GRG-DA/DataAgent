/**
 * Tabs — Reusable tab container
 *
 * Usage:
 *   Tabs.render('container-id', [
 *     { id: 'overview', label: 'Overview', icon: 'fa-house', badge: 5, content: '<p>Tab content</p>' },
 *     { id: 'details', label: 'Details', content: '...' }
 *   ], function(tabId) { console.log('Switched to', tabId); });
 *
 * Returns controller: { switchTab(id), setBadge(id, count), getActive() }
 */
(function () {
  "use strict";

  function _escape(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  function _render(containerId, tabs, onChange) {
    var container =
      typeof containerId === "string"
        ? document.getElementById(containerId)
        : containerId;
    if (!container || !tabs || tabs.length === 0) return null;

    var activeId = tabs[0].id;
    var state = { tabs: tabs, activeId: activeId, onChange: onChange };

    function _build() {
      var html =
        '<div class="tabs-container">';
      // Tab bar
      html += '<div class="flex border-b border-gray-200 dark:border-gray-700 overflow-x-auto">';
      for (var i = 0; i < tabs.length; i++) {
        var t = tabs[i];
        var isActive = t.id === activeId;
        html +=
          '<button class="tab-btn px-3 py-2 text-xs font-medium whitespace-nowrap border-b-2 transition-colors ' +
          (isActive
            ? "border-primary-500 text-primary-600 dark:text-primary-400"
            : "border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300") +
          '" data-tab-id="' +
          _escape(t.id) +
          '">';
        if (t.icon)
          html += '<i class="fas ' + t.icon + " mr-1\"></i>";
        html += _escape(t.label);
        if (t.badge !== undefined && t.badge !== null) {
          html +=
            '<span class="ml-1 px-1.5 py-0.5 text-[9px] rounded-full bg-primary-100 dark:bg-primary-900 text-primary-600 dark:text-primary-300">' +
            t.badge +
            "</span>";
        }
        html += "</button>";
      }
      html += "</div>";
      // Content panel
      html +=
        '<div class="tab-content pt-3">';
      for (var j = 0; j < tabs.length; j++) {
        var t2 = tabs[j];
        html +=
          '<div class="tab-panel' +
          (t2.id === activeId ? "" : " hidden") +
          '" data-tab-panel="' +
          _escape(t2.id) +
          '">' +
          (t2.content || "") +
          "</div>";
      }
      html += "</div></div>";
      container.innerHTML = html;

      // Bind clicks
      container.querySelectorAll(".tab-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var newId = this.dataset.tabId;
          if (newId && newId !== activeId) {
            activeId = newId;
            _build();
            if (state.onChange) state.onChange(activeId);
          }
        });
      });
    }

    _build();

    return {
      switchTab: function (id) {
        if (id && id !== activeId) {
          activeId = id;
          _build();
          if (state.onChange) state.onChange(activeId);
        }
      },
      setBadge: function (id, count) {
        for (var i = 0; i < tabs.length; i++) {
          if (tabs[i].id === id) {
            tabs[i].badge = count;
            _build();
            break;
          }
        }
      },
      getActive: function () {
        return activeId;
      },
    };
  }

  window.Tabs = {
    render: function (containerId, tabs, onChange) {
      return _render(containerId, tabs, onChange);
    },
  };
})();
