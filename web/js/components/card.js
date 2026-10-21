/**
 * Card — Reusable card container with header, body, and states
 *
 * Usage:
 *   Card.render('container-id', {
 *     title: 'Sales Overview',
 *     subtitle: 'Monthly data',
 *     icon: 'fa-chart-bar',
 *     actions: '<button>Action</button>',
 *     content: '<p>Card body content</p>'
 *   });
 *
 * States: loading(), empty(), error(), content(html)
 */
(function () {
  "use strict";

  function _escape(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  function _render(containerId, opts) {
    var container =
      typeof containerId === "string"
        ? document.getElementById(containerId)
        : containerId;
    if (!container) return null;

    opts = opts || {};
    var iconHtml = opts.icon
      ? '<i class="fas ' + opts.icon + " text-primary-500 mr-2\"></i> "
      : "";
    var titleHtml = opts.title
      ? '<h3 class="text-sm font-semibold">' + iconHtml + _escape(opts.title) + "</h3>"
      : "";
    var subtitleHtml = opts.subtitle
      ? '<p class="text-xs text-gray-400 mt-0.5">' + _escape(opts.subtitle) + "</p>"
      : "";
    var actionsHtml = opts.actions || "";

    container.innerHTML =
      '<div class="card">' +
      (titleHtml
        ? '<div class="card-header">' +
          '<div>' +
          titleHtml +
          subtitleHtml +
          "</div>" +
          (actionsHtml
            ? '<div class="flex items-center gap-2">' + actionsHtml + "</div>"
            : "") +
          "</div>"
        : "") +
      '<div class="card-body">' +
      (opts.content || "") +
      "</div>" +
      "</div>";

    return {
      container: container,
      loading: function () {
        this._setState(
          '<div class="skeleton" style="height:60px;border-radius:8px;"></div>'
        );
      },
      empty: function (msg) {
        this._setState(
          '<div class="empty-state" style="padding:20px"><i class="fas fa-database text-2xl"></i><p class="text-sm mt-1">' +
            _escape(msg || "No data") +
            "</p></div>"
        );
      },
      error: function (msg) {
        this._setState(
          '<div class="error-state" style="padding:20px"><i class="fas fa-exclamation-circle"></i><p class="text-sm mt-1">' +
            _escape(msg || "An error occurred") +
            "</p></div>"
        );
      },
      content: function (html) {
        this._setState(html);
      },
      _setState: function (html) {
        var body = container.querySelector(".card-body");
        if (body) body.innerHTML = html;
      },
    };
  }

  window.Card = {
    render: function (containerId, opts) {
      return _render(containerId, opts);
    },
  };
})();
