/**
 * Account-link fragments — the secret never leaves the browser.
 *
 * A password-reset / e-mail-verification / invitation link carries its
 * secret in the URL **fragment** (`#token=…`). A fragment is never sent to
 * the server, so the token cannot land in an access log. The backend mints
 * the links the same way (see `api/routes/account.py::_link`), and
 * `/auth/verify` refuses a token in a query string for the same reason.
 *
 * Four shapes are understood:
 *
 *   /reset-password#token=ABC     the canonical form — the kind comes from
 *                                  the path, the token from the fragment
 *   #reset-password#token=ABC     the hash-router form — the kind is the
 *                                  first hash segment, the token the second
 *   #token=ABC&type=reset         the self-describing form — both live in
 *                                  the fragment
 *   #token=ABC                    the bare form — a token on its own opens
 *                                  the password-reset view, the one action a
 *                                  link can ask for without a kind
 *
 * In every case the token is read out of the fragment and the fragment is
 * cleared with `history.replaceState`, so a later copy of the address bar
 * does not carry the secret.
 */
(function () {
  "use strict";

  var KINDS = {
    "reset-password": "reset-password",
    "verify-email": "verify-email",
    "accept-invite": "accept-invite",
  };

  // The short names a mail may use in `type=`.
  var TYPE_ALIASES = {
    "reset": "reset-password",
    "reset-password": "reset-password",
    "password": "reset-password",
    "verify": "verify-email",
    "verify-email": "verify-email",
    "confirm": "verify-email",
    "invite": "accept-invite",
    "accept-invite": "accept-invite",
    "accept": "accept-invite",
  };

  function _decode(value) {
    try {
      return decodeURIComponent(value || "");
    } catch (e) {
      return "";
    }
  }

  // "#reset-password#token=ABC" -> ["reset-password", "token=ABC"]
  function _segments(hash) {
    var raw = String(hash || "").replace(/^#/, "");
    if (!raw) return [];
    return raw.split("#");
  }

  // "/reset-password" -> "reset-password" (anything else -> "")
  function kindFromPath(pathname) {
    var match = /^\/(reset-password|verify-email|accept-invite)\/?$/.exec(
      String(pathname || "")
    );
    return match ? match[1] : "";
  }

  // The address the app itself lives at. An account link is served from a
  // path of its own ("/reset-password"), so "Continue" has to go back to
  // the application root rather than to the link's path.
  function appPath(pathname) {
    return kindFromPath(pathname) ? "/" : String(pathname || "/");
  }

  function _params(segment) {
    var out = {};
    var parts = String(segment || "").split("&");
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      var pair = parts[i].split("=");
      var key = _decode(pair[0]);
      if (!key) continue;
      out[key] = _decode(pair.slice(1).join("="));
    }
    return out;
  }

  /**
   * Read the kind and the token out of the current location.
   *
   * A bare `#token=…` — a token with no kind at all — opens the password
   * reset view: it is the one action a link can ask for on its own, and it
   * is the form the user is most likely to paste from an e-mail.
   *
   * @returns {{kind: string, token: string}} — empty strings when absent.
   */
  function parse(hash, pathname) {
    var segments = _segments(hash);
    var kind = "";
    var token = "";
    for (var i = 0; i < segments.length; i++) {
      var segment = segments[i];
      if (!segment) continue;
      var params = _params(segment);
      if (!token && params.token) token = params.token;
      if (!kind && params.type && TYPE_ALIASES[params.type]) {
        kind = TYPE_ALIASES[params.type];
      }
      if (!kind && KINDS[segment]) kind = segment;
    }
    if (!kind) kind = kindFromPath(pathname);
    if (!kind && token) kind = KINDS["reset-password"];
    return { kind: kind, token: token };
  }

  /**
   * Drop the fragment from the address bar without adding a history entry.
   * The secret must not stay visible in the URL after it has been used.
   */
  function clear() {
    try {
      var url = window.location.pathname + window.location.search;
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, "", url);
      }
    } catch (e) {
      /* ignore — a stale fragment is not fatal */
    }
  }

  window.AccountFragments = {
    KINDS: KINDS,
    TYPE_ALIASES: TYPE_ALIASES,
    parse: parse,
    kindFromPath: kindFromPath,
    appPath: appPath,
    clear: clear,
  };
})();
