/**
 * The three token-carrying account views.
 *
 * Each one redeems exactly one token, in the body of exactly one endpoint.
 * The token is handed in by the caller (read from the URL fragment) and is
 * never written to the console, never put in a query string and never sent
 * anywhere else.
 */
(function () {
  "use strict";

  var SPECIAL = "!@#$%^&*()_+-=[]{}|;:,.<>?";

  function esc(value) {
    if (window.App && window.App.escapeHtml) return window.App.escapeHtml(String(value == null ? "" : value));
    return String(value == null ? "" : value)
      .replace(/[&<>"']/g, function (ch) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
      });
  }

  /**
   * Turn a failed request into a sentence the reader can act on.
   * A 404 means the server has no such endpoint (an older build), which is
   * not the same thing as "your link expired" — say so instead of showing
   * the raw status.
   */
  function failure(e, fallback) {
    var status = e && e.status;
    if (status === 404) {
      return "This server does not offer the account action yet. Ask your administrator to enable it.";
    }
    if (status === 429) {
      return "Too many attempts just now. Wait a moment and try again.";
    }
    if (e && e.data && (e.data.detail || e.data.error)) {
      return String(e.data.detail || e.data.error);
    }
    if (e && e.message && e.message !== "HTTP " + status) {
      return String(e.message);
    }
    return fallback || "The link is invalid or has expired.";
  }

  // Mirrors the server policy (config/settings.yaml :: password_policy).
  function passwordProblems(password) {
    var value = String(password || "");
    var problems = [];
    if (value.length < 12) problems.push("at least 12 characters");
    if (!/[A-Z]/.test(value)) problems.push("an uppercase letter");
    if (!/[a-z]/.test(value)) problems.push("a lowercase letter");
    if (!/[0-9]/.test(value)) problems.push("a number");
    if (!new RegExp("[" + SPECIAL.replace(/[\\^\]]/g, "\\$&") + "]").test(value)) {
      problems.push("a special character");
    }
    return problems;
  }

  function shell(title, lead, body) {
    return (
      '<div class="account-view">' +
        '<div class="account-card">' +
          '<div class="account-mark"><i class="fas fa-network-wired"></i></div>' +
          '<h1 class="account-title">' + esc(title) + "</h1>" +
          '<p class="account-lead">' + esc(lead) + "</p>" +
          '<div id="account-body">' + body + "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function status(id, message, kind) {
    var box = document.getElementById(id);
    if (!box) return;
    box.className = "account-status account-status-" + (kind || "info");
    box.textContent = message;
    box.style.display = message ? "block" : "none";
  }

  function mount(html) {
    var host = document.getElementById("page-content");
    if (!host) return null;
    host.innerHTML = html;
    document.body.classList.add("account-mode");
    return document.getElementById("account-body");
  }

  function done(title, lead) {
    mount(
      shell(
        title,
        lead,
        '<div class="account-done"><i class="fas fa-circle-check"></i>' +
          '<p>You can close this tab, or sign in again below.</p>' +
          '<button class="btn btn-primary" id="account-continue">Continue</button></div>'
      )
    );
    var btn = document.getElementById("account-continue");
    if (btn) {
      btn.addEventListener("click", function () {
        window.location.href = window.AccountFragments.appPath(window.location.pathname);
      });
    }
  }

  function missingToken(what) {
    mount(
      shell(
        "Link incomplete",
        "This link is missing its one-time token.",
        '<div class="account-status account-status-error" style="display:block">' +
          esc("Open the link exactly as it was in the e-mail — the token lives in the part after the #.") +
        "</div>" +
        '<p class="account-hint">Ask for a fresh link and try again. ' + esc(what) + "</p>"
      )
    );
  }

  // ── Reset the password ────────────────────────────────────────────────
  function resetPassword(token) {
    if (!token) return missingToken("A password reset link was e-mailed to you.");
    mount(
      shell(
        "Choose a new password",
        "This link works once. Setting a new password signs out every device.",
        '<form id="account-form" autocomplete="off">' +
          '<label class="account-label" for="account-password">New password</label>' +
          '<input id="account-password" type="password" class="account-input" autocomplete="new-password" placeholder="12+ characters">' +
          '<label class="account-label" for="account-password2">Repeat the password</label>' +
          '<input id="account-password2" type="password" class="account-input" autocomplete="new-password" placeholder="Repeat it">' +
          '<div id="account-status" class="account-status" style="display:none"></div>' +
          '<button class="btn btn-primary account-submit" type="submit">Set the new password</button>' +
        "</form>"
      )
    );
    var form = document.getElementById("account-form");
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var password = document.getElementById("account-password").value;
      var repeat = document.getElementById("account-password2").value;
      var problems = passwordProblems(password);
      if (problems.length) {
        status("account-status", "The password needs " + problems.join(", ") + ".", "error");
        return;
      }
      if (password !== repeat) {
        status("account-status", "The two passwords do not match.", "error");
        return;
      }
      status("account-status", "Working…", "info");
      window.API
        .resetPassword(token, password)
        .then(function () {
          done("Password updated", "Your password has been reset and your old sessions were signed out.");
        })
        .catch(function (e) {
          status("account-status", failure(e), "error");
        });
    });
  }

  // ── Confirm the e-mail address ────────────────────────────────────────
  function verifyEmail(token) {
    if (!token) return missingToken("A confirmation link was e-mailed to you.");
    mount(
      shell(
        "Confirming your e-mail",
        "One moment — we are checking your confirmation link.",
        '<div id="account-status" class="account-status account-status-info" style="display:block">Checking the link…</div>'
      )
    );
    window.API
      .verifyEmail(token)
      .then(function () {
        done("E-mail confirmed", "Your address is confirmed. Notifications can reach you now.");
      })
      .catch(function (e) {
        var message = failure(e);
        status("account-status", message, "error");
        var retry = document.createElement("button");
        retry.className = "btn btn-secondary";
        retry.textContent = "Try again";
        retry.addEventListener("click", function () { verifyEmail(token); });
        var body = document.getElementById("account-body");
        if (body) body.appendChild(retry);
      });
  }

  // ── Accept an invitation ──────────────────────────────────────────────
  function acceptInvite(token) {
    if (!token) return missingToken("An invitation link was e-mailed to you.");
    mount(
      shell(
        "Accept your invitation",
        "Pick a password and your workspace is ready.",
        '<form id="account-form" autocomplete="off">' +
          '<label class="account-label" for="account-password">Choose a password</label>' +
          '<input id="account-password" type="password" class="account-input" autocomplete="new-password" placeholder="12+ characters">' +
          '<label class="account-label" for="account-password2">Repeat the password</label>' +
          '<input id="account-password2" type="password" class="account-input" autocomplete="new-password" placeholder="Repeat it">' +
          '<div id="account-status" class="account-status" style="display:none"></div>' +
          '<button class="btn btn-primary account-submit" type="submit">Accept the invitation</button>' +
        "</form>"
      )
    );
    var form = document.getElementById("account-form");
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var password = document.getElementById("account-password").value;
      var repeat = document.getElementById("account-password2").value;
      var problems = passwordProblems(password);
      if (problems.length) {
        status("account-status", "The password needs " + problems.join(", ") + ".", "error");
        return;
      }
      if (password !== repeat) {
        status("account-status", "The two passwords do not match.", "error");
        return;
      }
      status("account-status", "Working…", "info");
      window.API
        .acceptInvite(token, password)
        .then(function () {
          done("You are in", "Your account is ready — sign in with the password you just chose.");
        })
        .catch(function (e) {
          status("account-status", failure(e), "error");
        });
    });
  }

  window.AccountViews = {
    resetPassword: resetPassword,
    verifyEmail: verifyEmail,
    acceptInvite: acceptInvite,
    passwordProblems: passwordProblems,
    failure: failure,
  };
})();
