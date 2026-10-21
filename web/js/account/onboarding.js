/**
 * The tenant onboarding step.
 *
 * A signup gets its own tenant (and therefore its own schema) the moment
 * the account exists. This is the single screen that tells the new account
 * what just happened and lets it name the workspace it will work in.
 *
 * The workspace name is a label, stored in the caller's own preferences
 * (`/user/preferences`) and mirrored into localStorage so the header can
 * show it without a round-trip.
 */
(function () {
  "use strict";

  var PENDING_KEY = "da_tenant_onboarding";
  var NAME_KEY = "da_workspace_name";

  function esc(value) {
    if (window.App && window.App.escapeHtml) return window.App.escapeHtml(String(value == null ? "" : value));
    return String(value == null ? "" : value);
  }

  function _currentUser() {
    return (window.RBAC && window.RBAC.getUser()) || {};
  }

  function _tenantId() {
    var user = _currentUser();
    return user.tenant_id || "";
  }

  function isPending() {
    try {
      return localStorage.getItem(PENDING_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function markPending() {
    try {
      localStorage.setItem(PENDING_KEY, "1");
    } catch (e) {
      /* ignore */
    }
  }

  function clearPending() {
    try {
      localStorage.removeItem(PENDING_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  function getWorkspaceName() {
    try {
      return localStorage.getItem(NAME_KEY) || "";
    } catch (e) {
      return "";
    }
  }

  function _setWorkspaceName(name) {
    try {
      localStorage.setItem(NAME_KEY, name || "");
    } catch (e) {
      /* ignore */
    }
  }

  function _planLabel() {
    var plan = (window.RBAC && window.RBAC.getPlan) ? window.RBAC.getPlan() : "free";
    return String(plan || "free").charAt(0).toUpperCase() + String(plan || "free").slice(1);
  }

  /** Store the name server-side, keeping the other preferences intact. */
  function save(name) {
    var clean = String(name || "").trim().slice(0, 80);
    _setWorkspaceName(clean);
    if (!window.API || !window.API.getPreferences) return Promise.resolve(clean);
    return window.API
      .getPreferences()
      .catch(function () {
        return { prefs: {} };
      })
      .then(function (res) {
        var prefs = (res && res.prefs) || {};
        var merged = {};
        Object.keys(prefs).forEach(function (key) {
          merged[key] = prefs[key];
        });
        merged.workspace_name = clean;
        return window.API.setPreferences(merged);
      })
      .then(function () {
        return clean;
      });
  }

  function _overlayHtml(user, tenant, plan) {
    var suggested = getWorkspaceName() || tenant || (user.username || "workspace");
    return (
      '<div class="tenant-onboarding" id="tenant-onboarding">' +
        '<div class="tenant-onboarding-card">' +
          '<div class="account-mark"><i class="fas fa-building"></i></div>' +
          '<h1 class="account-title">Your workspace is ready</h1>' +
          '<p class="account-lead">A private workspace was created for <strong>' + esc(user.username || "you") + "</strong>. " +
            "Name it so your team recognises it.</p>" +
          '<label class="account-label" for="workspace-name">Workspace name</label>' +
          '<input id="workspace-name" class="account-input" maxlength="80" value="' + esc(suggested) + '" placeholder="Acme Analytics">' +
          '<div class="tenant-onboarding-facts">' +
            '<div><span>Workspace</span><strong>' + esc(tenant || "assigned at first sign-in") + "</strong></div>" +
            '<div><span>Plan</span><strong>' + esc(plan) + "</strong></div>" +
          "</div>" +
          '<div class="account-status" id="tenant-onboarding-status" style="display:none"></div>' +
          '<button class="btn btn-primary account-submit" id="tenant-onboarding-save">Enter the workspace</button>' +
          '<p class="account-hint"><strong>What happens next:</strong> your data lives only in this workspace. ' +
            "Upload a file or ask a question to get started — an administrator can invite colleagues to join you.</p>" +
        "</div>" +
      "</div>"
    );
  }

  /**
   * Show the onboarding step once, when a signup is waiting for it.
   * @returns {boolean} true when the screen was shown
   */
  function showIfPending() {
    if (!isPending()) return false;
    var user = _currentUser();
    var tenant = _tenantId();
    var plan = _planLabel();
    var host = document.createElement("div");
    host.innerHTML = _overlayHtml(user, tenant, plan);
    var overlay = host.firstElementChild;
    document.body.appendChild(overlay);

    var status = document.getElementById("tenant-onboarding-status");
    // NB: the button must not be called "save" — that name belongs to the
    // function that persists the name, and shadowing it here made
    // `save(name)` call the DOM node instead of the function.
    var saveBtn = document.getElementById("tenant-onboarding-save");
    var input = document.getElementById("workspace-name");

    saveBtn.addEventListener("click", function () {
      var name = (input.value || "").trim();
      if (!name) {
        status.className = "account-status account-status-error";
        status.textContent = "Give the workspace a name.";
        status.style.display = "block";
        return;
      }
      saveBtn.disabled = true;
      saveBtn.style.opacity = "0.6";
      save(name)
        .then(function () {
          clearPending();
          // The local (single-machine) wizard is not for a hosted tenant.
          try {
            localStorage.setItem("onboarding_completed", "true");
          } catch (e) {
            /* ignore */
          }
          overlay.remove();
          if (window.App && window.App.applyTenant) window.App.applyTenant();
        })
        .catch(function (e) {
          status.className = "account-status account-status-error";
          status.textContent = (e && e.message) || "The workspace name could not be saved.";
          status.style.display = "block";
          saveBtn.disabled = false;
          saveBtn.style.opacity = "1";
        });
    });

    if (input) input.focus();
    return true;
  }

  window.TenantOnboarding = {
    isPending: isPending,
    markPending: markPending,
    clearPending: clearPending,
    getWorkspaceName: getWorkspaceName,
    save: save,
    showIfPending: showIfPending,
    planLabel: _planLabel,
  };
})();
