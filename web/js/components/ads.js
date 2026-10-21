/**
 * Ads — REMOVED.
 *
 * The product is fully ad-free for all plans. This stub remains only so any
 * stale reference to window.Ads degrades gracefully; it intentionally does
 * nothing. The ad slot containers were removed from index.html.
 */
(function () {
  "use strict";

  window.Ads = {
    refresh: function () { /* no-op — ad-free */ },
    destroy: function () { /* no-op — ad-free */ },
    isEnabled: function () { return false; },
  };
})();
