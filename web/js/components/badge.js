/**
 * Badge — Small badge/tag component
 *
 * Usage:
 *   Badge.render(text, variant, options)
 *
 * Variants: default (gray), success (green), warning (amber), danger (red), info (blue), purple
 * Options: { size: 'sm'|'md', removable: true, dot: true }
 */
(function () {
  "use strict";

  function _escape(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  var VARIANTS = {
    default: "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300",
    success: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
    warning: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
    danger: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
    info: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
    purple: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300",
  };

  window.Badge = {
    render: function (text, variant, options) {
      variant = variant || "default";
      options = options || {};
      var cls = VARIANTS[variant] || VARIANTS.default;
      var sizeClass = options.size === "sm" ? "text-[9px] px-1 py-0.5" : "text-[10px] px-1.5 py-0.5";
      var dotHtml = options.dot
        ? '<span class="w-1.5 h-1.5 rounded-full inline-block mr-1" style="background:currentColor"></span>'
        : "";
      var closeHtml = options.removable
        ? '<button class="badge-remove ml-1 hover:opacity-70">&times;</button>'
        : "";
      return (
        '<span class="inline-flex items-center ' +
        sizeClass +
        " " +
        cls +
        " rounded-full font-medium" +
        '">' +
        dotHtml +
        _escape(text) +
        closeHtml +
        "</span>"
      );
    },
  };
})();
