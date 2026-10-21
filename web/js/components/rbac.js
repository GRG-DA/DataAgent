/**
 * RBAC — Role-Based Access Control for frontend
 *
 * Usage:
 *   RBAC.setUser(user)
 *   RBAC.hasPermission(p)    // read, write, delete, admin, export
 *   RBAC.requireRole(r)      // admin, analyst, viewer
 *   RBAC.isAuthenticated()
 *   RBAC.logout()
 *   RBAC.applyToNav()        // hides elements with data-permission / data-role
 */
(function () {
  "use strict";

  var _user = null;
  var _token = null;
  var _plan = null;
  var _entitlements = null;

  var PERMISSIONS = {
    admin: ["read", "write", "delete", "admin", "manage_users", "export", "share", "schedule"],
    analyst: ["read", "write", "export", "share", "schedule"],
    viewer: ["read", "export"],
  };

  var PLAN_LIMITS = {
    free: { max_dashboards: 3, max_chat_history_days: 7 },
    pro: { max_dashboards: 50, max_chat_history_days: 365 },
    lifetime: { max_dashboards: 1000, max_chat_history_days: 3650 },
  };

  function setUser(user) { _user = user; try { localStorage.setItem("session", JSON.stringify(user)); } catch (e) {} }
  function setToken(token) { _token = token; try { localStorage.setItem("auth_token", token); } catch (e) {} }
  function getToken() { if (_token) return _token; try { return localStorage.getItem("auth_token"); } catch (e) { return null; } }

  function setPlan(plan) { _plan = plan || null; try { localStorage.setItem("plan", _plan || ""); } catch (e) {} }
  function getPlan() {
    if (_plan) return _plan;
    try { return localStorage.getItem("plan") || "free"; } catch (e) { return "free"; }
  }
  function planLimit(key) {
    var limits = PLAN_LIMITS[getPlan()] || PLAN_LIMITS.free;
    return limits[key];
  }

  // Feature entitlements (set from /config billing.entitlements)
  function setEntitlements(ent) {
    _entitlements = ent || null;
    if (_entitlements) {
      _plan = _entitlements.plan || _plan || "free";
      try { localStorage.setItem("plan", _plan); } catch (e) {}
    }
  }
  function getEntitlements() { return _entitlements; }
  function hasFeature(feature) {
    if (!_entitlements) return false;
    return !!_entitlements[feature];
  }
  function planBadge() {
    var plan = getPlan() || "free";
    var allowed = { free: 1, pro: 1, lifetime: 1 };
    var safe = allowed[plan] ? plan : "free";
    var colors = { free: "#6b7280", pro: "#6366f1", lifetime: "#8b5cf6" };
    var color = colors[safe];
    return '<span class="plan-badge" style="display:inline-flex;align-items:center;gap:4px;' +
      'padding:2px 8px;border-radius:999px;font-size:10px;font-weight:700;letter-spacing:.4px;' +
      'text-transform:uppercase;color:#fff;background:' + color + ';">' +
      (safe.charAt(0).toUpperCase() + safe.slice(1)) + '</span>';
  }

  function getUser() {
    if (_user) return _user;
    try { var s = localStorage.getItem("session"); if (s) { _user = JSON.parse(s); return _user; } } catch (e) {}
    return null;
  }

  // Browser-cache namespace: storage keys must differ per login so two
  // users on one machine never see each other's chats/queries.
  function storeKey(base) {
    var who = "";
    try {
      var u = getUser();
      who = (u && (u.username || u.user_id)) || "";
    } catch (e) {}
    who = String(who).replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 64) || "anon";
    return who + "::" + base;
  }

  function isAuthenticated() { return !!getUser(); }

  function hasPermission(perm) {
    var user = getUser(); if (!user) return false;
    var perms = PERMISSIONS[user.role || "viewer"] || PERMISSIONS.viewer;
    return perms.indexOf(perm) >= 0;
  }

  function requireRole(roles) {
    var user = getUser(); if (!user) return false;
    if (typeof roles === "string") roles = [roles];
    return roles.indexOf(user.role) >= 0;
  }

  function logout() {
    _user = null; _token = null; _plan = null;
    localStorage.removeItem("session"); localStorage.removeItem("auth_token"); localStorage.removeItem("plan");
    window.location.hash = "#dashboard"; window.location.reload();
  }

  var _origOnclick = new WeakMap();

  function _lockFeatureElement(el, feature) {
    el.classList.add("premium-locked");
    el.setAttribute("title", "Premium feature — upgrade to access");
    // Add a lock icon next to the label (once) — skip if the element already
    // carries a lock (e.g. a "Pro / Lifetime" badge in its title).
    if (!el.querySelector(".premium-lock-icon") && !el.querySelector(".fa-lock")) {
      var lock = document.createElement("i");
      lock.className = "fas fa-lock premium-lock-icon";
      lock.setAttribute("aria-hidden", "true");
      lock.style.cssText = "font-size:10px;color:#9ca3af;margin-left:4px;";
      el.appendChild(lock);
    }
    if (!el.getAttribute("data-gated")) {
      el.setAttribute("data-gated", "1");
      if (typeof el.onclick === "function") _origOnclick.set(el, el.onclick);
      // Replace the element's own click handler (this also overrides any
      // inline onclick attribute) and stop the event from reaching delegated
      // handlers on ancestors — so the underlying action never runs.
      el.onclick = function(e) {
        e.preventDefault();
        e.stopImmediatePropagation();
        showUpgradeOverlay(feature);
        return false;
      };
    }
  }

  function _unlockFeatureElement(el) {
    el.classList.remove("premium-locked");
    var lock = el.querySelector(".premium-lock-icon");
    if (lock) lock.remove();
    if (el.getAttribute("data-gated")) {
      el.removeAttribute("data-gated");
      var orig = _origOnclick.get(el);
      if (orig) { el.onclick = orig; _origOnclick.delete(el); }
      else el.onclick = null;
    }
  }

  function applyToNav() {
    document.querySelectorAll("[data-permission]").forEach(function(el) {
      if (!hasPermission(el.dataset.permission)) el.style.display = "none";
    });
    document.querySelectorAll("[data-role]").forEach(function(el) {
      if (!requireRole(el.dataset.role.split(","))) el.style.display = "none";
    });
    document.querySelectorAll("[data-feature]").forEach(function(el) {
      var feature = el.dataset.feature;
      if (!hasFeature(feature)) _lockFeatureElement(el, feature);
      else _unlockFeatureElement(el);
    });
  }

  // Gate a single element rendered after boot (e.g. a button inside a page or
  // modal). Returns true when the element is locked, false when entitled.
  function gateElement(el, feature) {
    if (!el) return false;
    if (!hasFeature(feature)) { _lockFeatureElement(el, feature); return true; }
    _unlockFeatureElement(el);
    return false;
  }

  function showUpgradeOverlay(feature) {
    dismissUpgradeOverlay();
    var labels = {
      connect_page: "Connections", marketplace: "Marketplace", organization: "Organization",
      automation: "Automation", export_page: "Export", governance: "Data Governance",
      reports: "Reports", storytelling: "Storytelling", alerts_thresholds: "Alerts & Thresholds",
      scheduled_reports: "Scheduled Reports", smart_monitoring: "Smart Monitoring",
      live_monitor: "Live Monitoring", api_connection: "API Connections",
      dashboard_export: "Dashboard Export", recreate_from_image: "Recreate from Image",
      external_sources: "External Sources",
      enterprise: "Enterprise Features"
    };
    var label = labels[feature] || feature;
    var overlay = document.createElement("div");
    overlay.id = "premium-overlay";
    overlay.setAttribute("data-feature", feature || "");
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:9999;";
    overlay.innerHTML =
      '<div style="background:#fff;border-radius:12px;box-shadow:0 12px 32px rgba(0,0,0,.25);width:420px;max-width:90vw;padding:28px;text-align:center;position:relative;">' +
        '<button onclick="RBAC.dismissUpgradeOverlay()" style="position:absolute;top:12px;right:12px;border:none;background:transparent;cursor:pointer;font-size:18px;color:#666;">&times;</button>' +
        '<div style="font-size:32px;margin-bottom:8px;">&#9733;</div>' +
        '<div style="font-size:20px;font-weight:600;margin-bottom:8px;">Premium Feature</div>' +
        '<div style="display:inline-block;background:#ede9fe;color:#6d28d9;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;margin-bottom:12px;">' + label + '</div>' +
        '<p style="font-size:13px;color:#666;line-height:20px;margin-bottom:20px;">This feature is part of the Pro plan. Upgrade to unlock ' + label.toLowerCase() + ', a higher daily AI allowance, and all premium capabilities.</p>' +
        '<button onclick="window.location.hash=\'#subscription\';RBAC.dismissUpgradeOverlay();" style="width:100%;padding:10px;border:none;border-radius:8px;background:#6366f1;color:#fff;font-size:14px;font-weight:600;cursor:pointer;margin-bottom:8px;">Upgrade to Pro</button>' +
        '<button onclick="RBAC.dismissUpgradeOverlay()" style="width:100%;padding:8px;border:1px solid #d1d5db;border-radius:8px;background:#fff;color:#374151;font-size:13px;cursor:pointer;">Not now</button>' +
        '<div style="font-size:11px;color:#9ca3af;margin-top:10px;">One-time Lifetime plan also available.</div>' +
      '</div>';
    document.body.appendChild(overlay);
    return overlay;
  }

  // Route gate shows the overlay then redirects to #dashboard in the same
  // tick; the redirect's _handleRoute must not immediately tear it down.
  // data-keep survives exactly one route render (cleared by
  // clearUpgradeOverlayKeep); data-keep-until survives cold-boot races where
  // _handleRoute runs twice (initial + post-Auth/config settle) by keeping
  // the overlay for a short wall-clock window instead.
  var OVERLAY_KEEP_MS = 2500;
  function keepUpgradeOverlay() {
    var overlay = document.getElementById("premium-overlay");
    if (overlay) {
      overlay.setAttribute("data-keep", "1");
      overlay.setAttribute("data-keep-until", String(Date.now() + OVERLAY_KEEP_MS));
    }
  }
  function upgradeOverlayKeepValid() {
    var overlay = document.getElementById("premium-overlay");
    if (!overlay) return false;
    if (overlay.getAttribute("data-keep")) return true;
    var until = parseInt(overlay.getAttribute("data-keep-until") || "0", 10);
    return Date.now() < until;
  }
  function clearUpgradeOverlayKeep() {
    var overlay = document.getElementById("premium-overlay");
    if (overlay) overlay.removeAttribute("data-keep");
  }
  function dismissUpgradeOverlay() {
    var overlay = document.getElementById("premium-overlay");
    if (overlay) overlay.remove();
  }

  window.RBAC = { setUser, setToken, getToken, getUser, storeKey, isAuthenticated, hasPermission, requireRole, logout, applyToNav, gateElement, setPlan, getPlan, planLimit, planBadge, setEntitlements, getEntitlements, hasFeature, showUpgradeOverlay, dismissUpgradeOverlay, keepUpgradeOverlay, clearUpgradeOverlayKeep, upgradeOverlayKeepValid };
})();
