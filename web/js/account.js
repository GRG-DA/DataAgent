/**
 * The account-lifecycle entry point.
 *
 * When the address carries a one-time token the account view owns the
 * screen: the router, the login overlay and the top nav all step aside.
 * The token is read out of the URL fragment, handed to the view that
 * redeems it and then removed from the address bar.
 */
(function () {
  "use strict";

  function _renderUnknown(token) {
    var host = document.getElementById("page-content");
    if (!host) return;
    document.body.classList.add("account-mode");
    host.innerHTML =
      '<div class="account-view"><div class="account-card">' +
        '<div class="account-mark"><i class="fas fa-link-slash"></i></div>' +
        '<h1 class="account-title">Unrecognised link</h1>' +
        '<p class="account-lead">This link does not point at an account action DataAgent knows about.</p>' +
        '<div class="account-status account-status-error" style="display:block">' +
          "Ask for a fresh link, or sign in as usual." +
        "</div>" +
        '<button class="btn btn-primary account-submit" id="account-home">Go to DataAgent</button>' +
      "</div></div>";
    var btn = document.getElementById("account-home");
    if (btn) {
      btn.addEventListener("click", function () {
        window.location.href = window.AccountFragments.appPath(window.location.pathname);
      });
    }
    if (token) {
      // Nothing can redeem it — do not leave it in the address bar.
      window.AccountFragments.clear();
    }
  }

  /**
   * Take over the screen when the URL carries an account token.
   * @returns {boolean} true when the account view owns the screen
   */
  function init() {
    if (!window.AccountFragments || !window.AccountViews) return false;
    var parsed = window.AccountFragments.parse(
      window.location.hash,
      window.location.pathname
    );
    if (!parsed.kind && !parsed.token) return false;

    // The secret is read once; the fragment goes away immediately.
    window.AccountFragments.clear();

    // The account screen owns the whole window. A login overlay that is
    // already up (the page was open when the link arrived) has to step
    // aside, or it would cover the very form the user came to fill in.
    if (window.Auth && typeof window.Auth.hideOverlay === "function") {
      window.Auth.hideOverlay();
    }

    if (!parsed.kind) {
      _renderUnknown(parsed.token);
      return true;
    }

    if (parsed.kind === "reset-password") {
      window.AccountViews.resetPassword(parsed.token);
    } else if (parsed.kind === "verify-email") {
      window.AccountViews.verifyEmail(parsed.token);
    } else if (parsed.kind === "accept-invite") {
      window.AccountViews.acceptInvite(parsed.token);
    } else {
      _renderUnknown(parsed.token);
    }
    return true;
  }

  window.AccountView = { init: init };
})();
