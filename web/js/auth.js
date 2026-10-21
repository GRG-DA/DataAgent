/**
 * Auth — login gate for the SPA.
 *
 * Flow:
 *   1. Auth.init() checks localStorage for a token + user, then verifies
 *      it via GET /auth/verify. Stale tokens are cleared, never trusted.
 *   2. Without a valid session it shows a login overlay with up to two paths:
 *      - "Continue with Google" — native OAuth popup (GET /auth/google/login,
 *        postMessage + GET /auth/google/login/result). No Firebase involved.
 *      - Email/password when the billing server (central accounts) is
 *        configured — POST /auth/accounts/login, /auth/accounts/register.
 *        Otherwise local username/password — POST /auth/login, /auth/register.
 *   3. token / user / plan persist via RBAC, then the page reloads booted.
 *   4. Auth.signOut() clears the local session.
 */
(function () {
  "use strict";

  var _enabled = false;      // native Google login available
  var _configured = false;
  var _central = false;      // central (server-side) accounts available
  var _overlay = null;
  var _initPromise = null;

  async function _fetchStatus() {
    try {
      var r = await window.API.get("/auth/google/login/status", { silent: true });
      _enabled = !!(r && r.enabled);
      _configured = !!(r && r.configured);
    } catch (e) {
      _enabled = false;
      _configured = false;
    }
    try {
      var c = await window.API.get("/auth/accounts/status", { silent: true });
      _central = !!(c && c.enabled && c.configured);
    } catch (e) {
      _central = false;
    }
  }

  function _currentUser() {
    return window.RBAC ? window.RBAC.getUser() : null;
  }

  function _currentToken() {
    return window.RBAC ? window.RBAC.getToken() : null;
  }

  function _clearStoredSession() {
    try {
      localStorage.removeItem("auth_token");
      localStorage.removeItem("session");
      localStorage.removeItem("plan");
    } catch (e) { /* ignore */ }
  }

  async function _hasValidLocalSession() {
    if (!_currentUser() || !_currentToken()) return false;
    try {
      var r = await window.API.get("/auth/verify", { silent: true });
      if (r && r.authenticated) return true;
    } catch (e) { /* treat as invalid below */ }
    _clearStoredSession();
    return false;
  }

  function _applyUserToUI() {
    var user = _currentUser();
    if (!user) return;
    var name = user.full_name || user.name || user.username || "User";
    var role = user.role || "viewer";
    var email = user.email || "";
    var initial = (name || "U").trim().charAt(0).toUpperCase();
    var set = function (id, val) {
      var el = document.getElementById(id);
      if (el) el.textContent = val;
    };
    set("user-name", name);
    set("user-role", role);
    set("ud-name", name);
    set("ud-email", email);
    var avatars = document.querySelectorAll("#user-avatar, #ud-avatar");
    avatars.forEach(function (el) { el.textContent = initial; });
    var plan = window.RBAC.getPlan ? window.RBAC.getPlan() : "";
    var chip = document.getElementById("ud-plan");
    if (chip) {
      if (plan) {
        chip.textContent = plan.charAt(0).toUpperCase() + plan.slice(1);
        chip.style.display = "inline-flex";
      } else {
        chip.style.display = "none";
      }
    }
    // The role badge sits next to the plan, so the workspace chip below it
    // reads as "this account, in this plan, in this workspace".
    var roleChip = document.getElementById("ud-role");
    if (roleChip) {
      if (role) {
        roleChip.textContent = role;
        roleChip.style.display = "inline-flex";
      } else {
        roleChip.style.display = "none";
      }
    }
    // The workspace (tenant) the account belongs to, next to the plan badge.
    if (window.App && window.App.applyTenant) window.App.applyTenant();
  }

  function _friendlyError(e, fallback) {
    if (e && e.data && (e.data.error || e.data.detail)) {
      var m = e.data.error || e.data.detail;
      return String(m).slice(0, 200);
    }
    if (e && e.message) return String(e.message).slice(0, 200);
    return fallback;
  }

  function _showError(overlay, message) {
    var err = overlay.querySelector("#auth-error");
    if (!err) return;
    err.textContent = message || "Sign-in failed. Please try again.";
    err.style.display = "block";
  }

  function _hideError(overlay) {
    var err = overlay.querySelector("#auth-error");
    if (err) err.style.display = "none";
  }

  function _setBusy(overlay, busy) {
    ["#auth-google-btn", "#auth-login-btn", "#auth-register-btn"].forEach(function (sel) {
      var b = overlay.querySelector(sel);
      if (b) { b.disabled = !!busy; b.style.opacity = busy ? "0.6" : "1"; }
    });
  }

  function _googleSection() {
    if (!_enabled || !_configured) return "";
    return '<button id="auth-google-btn" style="width:100%;display:flex;align-items:center;justify-content:center;gap:12px;' +
      'padding:12px 16px;border:1px solid #e5e7eb;border-radius:12px;background:#fff;color:#111827;' +
      'font-size:15px;font-weight:600;cursor:pointer;">' +
      '<svg width="20" height="20" viewBox="0 0 48 48" aria-hidden="true">' +
      '<path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3l5.7-5.7C34.1 6.1 29.3 4 24 4 13 4 4 13 4 24s9 20 20 20 20-9 20-20c0-1.3-.1-2.6-.4-3.9z"/>' +
      '<path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3l5.7-5.7C34.1 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>' +
      '<path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/>' +
      '<path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C36.9 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.9z"/>' +
      '</svg>Continue with Google</button>';
  }

  function _localSection() {
    var label = _central ? "OR WITH EMAIL" : "OR WITH USERNAME";
    var idPlaceholder = _central ? "Email" : "Username";
    var idType = _central ? "email" : "text";
    var hint = _central
      ? '<p style="font-size:11px;color:#9ca3af;margin:12px 0 0;">Your DataAgent account stores only your email, password and purchases — your data stays on this machine.</p>'
      : '<p style="font-size:11px;color:#9ca3af;margin:12px 0 0;">Register creates a standard account in its own workspace (12+ chars, upper/lower/number/symbol).</p>';
    return '<div style="display:flex;align-items:center;gap:12px;margin:20px 0 16px;">' +
      '<div style="flex:1;height:1px;background:#e5e7eb;"></div>' +
      '<span style="font-size:12px;color:#9ca3af;font-weight:600;">' + label + '</span>' +
      '<div style="flex:1;height:1px;background:#e5e7eb;"></div></div>' +
      '<input id="auth-username" type="' + idType + '" maxlength="64" autocomplete="' + (_central ? "email" : "username") + '" aria-label="' + idPlaceholder + '" placeholder="' + idPlaceholder + '" style="width:100%;padding:11px 14px;margin-bottom:10px;' +
      'border:1px solid #e5e7eb;border-radius:10px;font-size:14px;box-sizing:border-box;" />' +
      '<input id="auth-email" type="email" maxlength="254" autocomplete="email" aria-label="E-mail address (optional)" placeholder="E-mail address (optional)" style="width:100%;padding:11px 14px;margin-bottom:10px;' +
      'border:1px solid #e5e7eb;border-radius:10px;font-size:14px;box-sizing:border-box;' + (_central ? 'display:none;' : '') + '" />' +
      '<input id="auth-password" type="password" maxlength="256" autocomplete="current-password" aria-label="Password" placeholder="Password" style="width:100%;padding:11px 14px;' +
      'margin-bottom:12px;border:1px solid #e5e7eb;border-radius:10px;font-size:14px;box-sizing:border-box;" />' +
      '<div style="display:flex;gap:10px;">' +
      '<button type="button" id="auth-login-btn" style="flex:1;padding:11px;border:none;border-radius:10px;background:#4f46e5;color:#fff;' +
      'font-size:14px;font-weight:700;cursor:pointer;">Log in</button>' +
      '<button type="button" id="auth-register-btn" style="flex:1;padding:11px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;' +
      'color:#111827;font-size:14px;font-weight:700;cursor:pointer;">Register</button></div>' +
      '<div style="display:flex;justify-content:center;margin-top:12px;">' +
      '<button type="button" id="auth-forgot-btn" style="border:none;background:none;padding:0;font-size:12px;color:#4f46e5;' +
      'cursor:pointer;text-decoration:underline;">Forgot your password?</button></div>' +
      hint;
  }

  function _buildOverlay() {
    var overlay = document.createElement("div");
    overlay.id = "auth-overlay";
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;" +
      "background:linear-gradient(135deg,#1e1b4b 0%,#312e81 50%,#4c1d95 100%);" +
      "font-family:Inter,system-ui,-apple-system,sans-serif;";
    overlay.innerHTML =
      '<div style="background:#fff;border-radius:20px;padding:36px 40px;max-width:400px;width:92%;' +
      'box-shadow:0 25px 60px rgba(0,0,0,.35);text-align:center;max-height:92vh;overflow-y:auto;">' +
      '<div style="width:60px;height:60px;margin:0 auto 14px;border-radius:16px;' +
      'background:linear-gradient(135deg,#6366f1,#8b5cf6);display:flex;align-items:center;justify-content:center;">' +
      '<i class="fas fa-chart-network" style="color:#fff;font-size:24px;"></i></div>' +
      '<h1 style="font-size:22px;font-weight:800;color:#111827;margin:0 0 6px;">DataAgent</h1>' +
      '<p style="font-size:14px;color:#6b7280;margin:0 0 20px;">Sign in to continue to your analytics workspace</p>' +
      _googleSection() + _localSection() +
      '<div id="auth-error" style="display:none;margin-top:14px;padding:10px 12px;border-radius:8px;' +
      'background:#fef2f2;color:#dc2626;font-size:13px;text-align:left;"></div>' +
      '<p style="font-size:12px;color:#9ca3af;margin:18px 0 0;">Your data stays in your local workspace.</p>' +
      '</div>';
    _wireOverlay(overlay);
    return overlay;
  }

  function _wireOverlay(overlay) {
    _wireGoogle(overlay);
    _wireLocal(overlay);
  }

  function _wireGoogle(overlay) {
    var btn = overlay.querySelector("#auth-google-btn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      _hideError(overlay);
      _setBusy(overlay, true);
      _signInWithGoogle().then(function () {
        _hideOverlay();
        window.location.reload();
      }).catch(function (e) {
        _setBusy(overlay, false);
        _showError(overlay, _friendlyError(e, "Google sign-in failed."));
      });
    });
  }

  function _readLocalCreds(overlay) {
    var u = overlay.querySelector("#auth-username");
    var p = overlay.querySelector("#auth-password");
    var e = overlay.querySelector("#auth-email");
    return {
      username: (u && u.value || "").trim(),
      password: (p && p.value || ""),
      email: (e && e.value || "").trim(),
    };
  }

  function _wireLocal(overlay) {
    var login = overlay.querySelector("#auth-login-btn");
    var reg = overlay.querySelector("#auth-register-btn");
    var forgot = overlay.querySelector("#auth-forgot-btn");
    var user = overlay.querySelector("#auth-username");
    var pass = overlay.querySelector("#auth-password");
    if (login) login.addEventListener("click", function () { _submitLocal(overlay, "login"); });
    if (reg) reg.addEventListener("click", function () { _submitLocal(overlay, "register"); });
    if (forgot) forgot.addEventListener("click", function () {
      if (window.AccountForms) {
        window.AccountForms.showForgotPassword(overlay, function () { _showOverlay(true); });
      }
    });
    if (pass) pass.addEventListener("keydown", function (e) {
      if (e.key === "Enter") _submitLocal(overlay, "login");
    });
    if (user) user.addEventListener("keydown", function (e) {
      if (e.key === "Enter") _submitLocal(overlay, "login");
    });
  }

  function _submitLocal(overlay, mode) {
    _hideError(overlay);
    var creds = _readLocalCreds(overlay);
    if (!creds.username) return _showError(overlay, _central ? "Enter your email." : "Enter a username.");
    if (!creds.password) return _showError(overlay, "Enter a password.");
    _setBusy(overlay, true);
    var op = mode === "register" ? _registerLocal(creds) : _signInLocal(creds);
    op.then(function (result) {
      if (mode === "register") {
        _setBusy(overlay, false);
        // A central (hosted) account lives on the billing server and has no
        // local tenant to prepare — only a local signup gets the workspace
        // screen.
        if (_central) {
          _hideOverlay();
          window.location.reload();
          return;
        }
        _showTenantPrepared(overlay, result);
        return;
      }
      _hideOverlay();
      window.location.reload();
    }).catch(function (e) {
      _setBusy(overlay, false);
      _showError(overlay, _friendlyError(e, "Sign-in failed. Please try again."));
    });
  }

  /**
   * The state a fresh signup lands in: the account exists and its private
   * workspace is being prepared.
   */
  function _showTenantPrepared(overlay, result) {
    var user = (result && result.user) || {};
    var email = (user.email || "") || "";
    if (window.TenantOnboarding) window.TenantOnboarding.markPending();
    var card = overlay.firstElementChild;
    if (!card) return;
    card.innerHTML =
      '<div style="width:60px;height:60px;margin:0 auto 14px;border-radius:16px;' +
        'background:linear-gradient(135deg,#6366f1,#8b5cf6);display:flex;align-items:center;justify-content:center;">' +
        '<i class="fas fa-building" style="color:#fff;font-size:24px;"></i></div>' +
      '<h1 style="font-size:22px;font-weight:800;color:#111827;margin:0 0 6px;">Your workspace is being prepared</h1>' +
      '<p style="font-size:14px;color:#6b7280;margin:0 0 18px;">The account <strong>' +
        _escapeText(user.username || "") + '</strong> is ready. We are setting up a private workspace for it.</p>' +
      '<div style="text-align:left;padding:12px 14px;border-radius:10px;background:#f9fafb;border:1px solid #e5e7eb;' +
        'font-size:13px;color:#374151;line-height:1.7;">' +
        '<div><strong>Workspace</strong> &middot; created for this account</div>' +
        '<div><strong>Data</strong> &middot; only you can see it</div>' +
        (email ? '<div><strong>E-mail</strong> &middot; ' + _escapeText(email) + '</div>' : '') +
        '<div><strong>Next</strong> &middot; sign in and name the workspace</div>' +
      '</div>' +
      '<button type="button" id="auth-prepared-continue" style="width:100%;margin-top:18px;padding:11px;border:none;' +
        'border-radius:10px;background:#4f46e5;color:#fff;font-size:14px;font-weight:700;cursor:pointer;">Enter the workspace</button>';
    var btn = card.querySelector("#auth-prepared-continue");
    if (btn) {
      btn.addEventListener("click", function () {
        _hideOverlay();
        window.location.reload();
      });
    }
  }

  function _escapeText(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function _persistSession(r) {
    if (!r || !r.token) throw new Error("Login failed — no token returned");
    window.RBAC.setToken(r.token);
    window.RBAC.setUser(r.user);
    if (r.plan) window.RBAC.setPlan(r.plan);
    return r;
  }

  // ── Native Google OAuth (popup) — no Firebase ──────────────────────

  function _stateFromUrl(url) {
    try {
      return new URL(url, window.location.origin).searchParams.get("state") || "";
    } catch (e) {
      return "";
    }
  }

  async function _claimGoogleResult(state) {
    var r = await window.API.get("/auth/google/login/result?state=" + encodeURIComponent(state));
    if (!r || r.success === false) {
      throw new Error((r && r.error) || "Google sign-in failed.");
    }
    return _persistSession(r);
  }

  function _waitForGoogleResult(state, popup) {
    return new Promise(function (resolve, reject) {
      var done = false;
      var claiming = false;
      var timer = null;

      function cleanup() {
        window.removeEventListener("message", onMessage);
        if (timer) clearInterval(timer);
        try { if (popup && !popup.closed) popup.close(); } catch (e) { /* ignore */ }
      }
      function claim() {
        if (done || claiming) return;
        claiming = true;
        _claimGoogleResult(state).then(function (r) {
          if (done) return;
          done = true; cleanup(); resolve(r);
        }).catch(function (e) {
          claiming = false;
          if (done) return;
          done = true; cleanup(); reject(e);
        });
      }
      function fail(message) {
        if (done) return;
        done = true; cleanup(); reject(new Error(message));
      }
      function onMessage(ev) {
        if (ev.origin !== window.location.origin) return;
        var d = ev.data || {};
        if (d.type !== "google_login") return;
        if (state && d.state && d.state !== state) return;
        if (d.success) claim();
        else fail(d.message || "Google sign-in failed.");
      }

      window.addEventListener("message", onMessage);
      var deadline = Date.now() + 120000;
      timer = setInterval(function () {
        if (popup && popup.closed) {
          // Give a late postMessage a beat to land, then claim by state.
          setTimeout(function () { claim(); }, 600);
          return;
        }
        if (Date.now() > deadline) fail("Google sign-in timed out — try again.");
      }, 1000);
    });
  }

  async function _signInWithGoogle() {
    var st = await window.API.get("/auth/google/login");
    if (!st || st.success === false) {
      throw new Error((st && st.error) || "Google sign-in is not configured");
    }
    var state = _stateFromUrl(st.auth_url);
    if (!state) throw new Error("Could not start sign-in — missing OAuth state.");
    var popup = window.open(st.auth_url, "da-google-login", "width=480,height=640");
    if (!popup) throw new Error("Browser blocked the Google popup — allow popups and try again.");
    return _waitForGoogleResult(state, popup);
  }

  async function _signInLocal(creds) {
    var path = _central ? "/auth/accounts/login" : "/auth/login";
    var body = _central
      ? { email: creds.username, password: creds.password }
      : { username: creds.username, password: creds.password };
    var r = await window.API.post(path, body);
    if (r && r.success === false) throw new Error(r.error || "Invalid credentials");
    return _persistSession(r);
  }

  async function _registerLocal(creds) {
    var path = _central ? "/auth/accounts/register" : "/auth/register";
    var body = _central
      ? { email: creds.username, password: creds.password }
      : { username: creds.username, password: creds.password, email: creds.email || undefined };
    var r = await window.API.post(path, body);
    if (r && r.success === false) throw new Error(r.error || "Registration failed");
    if (r && r.token) return _persistSession(r);
    try {
      return await _signInLocal(creds);
    } catch (e) {
      throw new Error("Registered — please log in.");
    }
  }

  function _showOverlay(force) {
    if (_overlay && _overlay.parentNode) {
      if (!force) return;
      _overlay.parentNode.removeChild(_overlay);
      _overlay = null;
    }
    _overlay = _buildOverlay();
    document.body.appendChild(_overlay);
  }

  function _showOverlayWithError(message) {
    _showOverlay();
    if (_overlay) _showError(_overlay, message);
  }

  function _hideOverlay() {
    if (_overlay && _overlay.parentNode) _overlay.parentNode.removeChild(_overlay);
    _overlay = null;
  }

  window.Auth = {
    init: function () {
      if (_initPromise) {
        // The result is cached, but the overlay is not: an account link may
        // have taken it off the screen in the meantime. Without a session it
        // has to come back.
        return _initPromise.then(function (ok) {
          if (!ok) _showOverlay();
          return ok;
        });
      }
      _initPromise = (async function () {
        await _fetchStatus();
        if (await _hasValidLocalSession()) {
          _applyUserToUI();
          return true;
        }
        var params = new URLSearchParams(window.location.search || "");
        if (params.get("error")) {
          _showOverlayWithError(String(params.get("error")).slice(0, 200));
          return false;
        }
        _showOverlay();
        return false;
      })();
      return _initPromise;
    },

    isEnabled: function () { return _enabled; },
    isConfigured: function () { return _configured; },
    isCentral: function () { return _central; },

    signInWithGoogle: _signInWithGoogle,
    signInLocal: _signInLocal,
    registerLocal: _registerLocal,

    signOut: async function () {
      try {
        var t = window.RBAC && window.RBAC.getToken ? window.RBAC.getToken() : null;
        await fetch((window.__API_BASE__ || "") + "/auth/logout", {
          method: "POST", headers: t ? { "Authorization": "Bearer " + t } : {},
        });
      } catch (e) { /* revoke is best-effort */ }
      if (window.RBAC) window.RBAC.logout();
    },

    applyUserToUI: _applyUserToUI,

    // Take the login overlay off the screen. An account-lifecycle link
    // (password reset, e-mail confirmation, invitation) owns the whole
    // screen, and it may arrive while the overlay is up.
    hideOverlay: _hideOverlay,
  };
})();
