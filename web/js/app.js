/**
 * Application Module - SPA Router & State Management
 * Hash-based routing, page lifecycle, notifications, dark mode, top nav.
 */
(function () {
  "use strict";

  // ============================================================================
  // Global App State
  // ============================================================================
  const _state = {
    currentPage: null,
    apiHealthy: false,
    tables: {},
    systemTables: [],
    selectedTable: null,
    darkMode: localStorage.getItem("darkMode") === "true",
    pageStates: {},
    _initialized: false,
    // Feature flags from backend
    features: {
      enable_llm_agent: true,
      fallback_to_sql: true,
    },
    llm_enabled: true,
    ads: null,
    billing: null,
  };

  // ============================================================================
  // Page Registry
  // ============================================================================
  const _pages = new Map();

  function registerPage(hash, module) {
    _pages.set(hash, module);
  }

  // Fetch feature flags from backend
  async function _loadConfig() {
    try {
      const response = await window.API.get("/config");
      if (response && response.features) {
        _state.features = response.features;
        _state.llm_enabled = response.llm_enabled;
      }
      if (response && response.ads) {
        _state.ads = response.ads;
      }
      if (response && response.billing) {
        _state.billing = response.billing;
        if (response.billing.entitlements && window.RBAC) {
          window.RBAC.setEntitlements(response.billing.entitlements);
        }
      }
    } catch (e) {
      console.warn("Failed to load feature config:", e);
    }
  }

  // Refresh billing entitlements when the window regains focus so pro
  // unlocks without a reload after returning from an external payment browser.
  function _refreshBillingOnFocus() {
    if (document.hidden) return;
    _loadConfig().then(function () {
      _applyFeatureFlags();
      if (window.RBAC && window.RBAC.applyToNav) {
        try { window.RBAC.applyToNav(); } catch (e) { /* ignore */ }
      }
    });
  }

  // ============================================================================
  // Hash-based Router
  // ============================================================================
  function _navigate(hash) {
    if (!hash.startsWith("#")) hash = "#" + hash;
    if (hash !== window.location.hash) {
      window.location.hash = hash;
    } else {
      _handleRoute();
    }
  }

  // Monotonic token: rapid hash changes must abandon a stale async route
  // so a previous page's render cannot overwrite the next page's content.
  let _routeToken = 0;

  // Ensure premium entitlements are loaded before gating. On cold boot
  // _handleRoute can run before /config resolves, leaving
  // RBAC.getEntitlements() null and hasFeature() false for every gated
  // route — which wrongly bounces Pro users back to #dashboard.
  async function _ensureEntitlements() {
    try {
      if (!window.RBAC || typeof window.RBAC.getEntitlements !== "function") return;
      if (window.RBAC.getEntitlements()) return;
      const cfg = await window.API.get("/config", { silent: true });
      if (cfg && cfg.billing && cfg.billing.entitlements && window.RBAC.setEntitlements) {
        window.RBAC.setEntitlements(cfg.billing.entitlements);
        if (window.RBAC.applyToNav) { try { window.RBAC.applyToNav(); } catch (_) {} }
      }
    } catch (_) { /* fail-open: page-level 403 handling covers it */ }
  }

  async function _handleRoute() {
    const routeToken = ++_routeToken;
    const _stale = () => routeToken !== _routeToken;
    // Orphan loading counts from the previous route (settings fires many
    // parallel loads; leaving them counted keeps #loading-overlay visible).
    if (window.API && typeof window.API.resetLoading === "function") {
      window.API.resetLoading();
    }
    let hash = window.location.hash || "#dashboard";
    // Legacy deep links: #bidashboard (and #bidashboard/<dashId>) point at the
    // retired Power BI Studio page whose module (web/js/pages/bidashboard.js)
    // no longer ships — lazy-loading it 404s and the route falls through to a
    // console warning + dashboard redirect. blankdash is the surviving builder
    // and already handles #blankdash/<dashId> server deep-links, so rewrite the
    // hash there instead of resolving a dead route.
    if (/^#bidashboard(\/|$)/.test(hash)) {
      const dashId = hash.split("/")[1] || "";
      window.location.hash = dashId ? "#blankdash/" + dashId : "#dashboardstudio";
      return;
    }
    const pageHash = /^#blankdash\//.test(hash) ? "#blankdash" : hash;

    // Drop a stale upgrade overlay when the user moves to another route.
    // The gate marks its overlay keep-valid so the redirect back to
    // #dashboard keeps it; the keep survives cold-boot double renders via a
    // wall-clock window (see RBAC.upgradeOverlayKeepValid).
    if (window.RBAC && window.RBAC.dismissUpgradeOverlay) {
      const ov = document.getElementById("premium-overlay");
      const keepValid = window.RBAC.upgradeOverlayKeepValid
        ? window.RBAC.upgradeOverlayKeepValid()
        : !!(ov && ov.getAttribute("data-keep"));
      if (ov && !keepValid) window.RBAC.dismissUpgradeOverlay();
      if (window.RBAC.clearUpgradeOverlayKeep) window.RBAC.clearUpgradeOverlayKeep();
    }

    // Premium feature gate: block direct navigation to gated pages.
    const ROUTE_FEATURES = {
      "#connect": "connect_page",
      "#marketplace": "marketplace",
      "#org": "organization",
      "#workflows": "automation",
      "#automation": "automation",
      "#export": "export_page",
      "#catalog": "governance",
    };
    const routeFeature = ROUTE_FEATURES[pageHash];
    if (routeFeature && window.RBAC) {
      await _ensureEntitlements();
      if (_stale()) return;
      // Still null (offline / unauthenticated): fail-open and let the page's
      // own API 403s drive the upgrade overlay instead of bouncing here.
      const ent = typeof window.RBAC.getEntitlements === "function"
        ? window.RBAC.getEntitlements() : null;
      if (ent && !window.RBAC.hasFeature(routeFeature)) {
        window.RBAC.showUpgradeOverlay(routeFeature);
        if (window.RBAC.keepUpgradeOverlay) window.RBAC.keepUpgradeOverlay();
        if (window.location.hash !== "#dashboard") {
          window.location.hash = "#dashboard";
        }
        return;
      }
    }

    const module = _pages.get(pageHash);

    // Lazy load if not registered yet (dynamic page loading)
    if (!module) {
      const pageName = pageHash.replace("#", "");
      try {
        const loadedModule = await App.loadPage(pageName);
        if (_stale()) return;
        if (loadedModule) {
          registerPage(pageHash, loadedModule);
          _handleRoute();
          return;
        }
      } catch (e) {
        if (_stale()) return;
        console.warn("Failed to lazy load page:", pageName, e);
      }
      if (_stale()) return;
      console.warn("Unknown route:", hash);
      window.location.hash = "#dashboard";
      return;
    }

    // Destroy previous page
    if (_state.currentPage && _state.currentPage.destroy) {
      try {
        await _state.currentPage.destroy();
      } catch (e) {
        console.error("Error destroying page:", _state.currentPage, e);
      }
    }
    if (_stale()) return;

    // Initialize and render new page
    _state.currentPage = module;
    if (module.init) {
      try {
        await module.init();
      } catch (e) {
        console.error("Error initializing page:", hash, e);
      }
    }
    if (_stale()) return;
    if (module.render) {
      try {
        await module.render();
      } catch (e) {
        console.error("Error rendering page:", hash, e);
      }
    }
    // A slower previous render can finish after this one and clobber
    // #page-content (e.g. settings awaiting refreshTables). If we lost the
    // race, re-run the current hash so the visible page matches the route.
    if (_stale()) {
      _handleRoute();
      return;
    }

    // Update nav active state
    document.querySelectorAll(".nav-item, .nav-dropdown-item, .mobile-nav-item").forEach(function (el) {
      var href = el.getAttribute("href");
      if (href === pageHash) el.classList.add("active");
      else el.classList.remove("active");
    });

    // Ad slots removed — fully ad-free for all plans.
  }

  // ============================================================================
  // Toast Notifications
  // ============================================================================
  function _toast(message, type = "info", duration = 4000) {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const icons = { success: "check-circle", error: "exclamation-circle", warning: "exclamation-triangle", info: "info-circle" };
    const colors = { success: "bg-green-600", error: "bg-red-600", warning: "bg-yellow-600", info: "bg-blue-600" };
    const toast = document.createElement("div");
    toast.className = "toast px-4 py-3 rounded-lg shadow-lg text-white " + (colors[type] || "bg-blue-600") + " flex items-center gap-2 animate-slide-in";
    toast.innerHTML =
      '<i class="fas fa-' + (icons[type] || "info-circle") + '"></i>' +
      '<span>' + _escapeHtml(message) + '</span>' +
      '<button class="toast-close" aria-label="Dismiss"><i class="fas fa-times"></i></button>';
    const dismiss = function () {
      toast.classList.add("toast-fadeout");
      setTimeout(function () { toast.remove(); }, 300);
    };
    toast.querySelector(".toast-close").addEventListener("click", dismiss);
    container.appendChild(toast);
    setTimeout(dismiss, duration);
  }

  // ============================================================================
  // Health Check
  // ============================================================================
  async function _checkHealth() {
    try {
      const health = await window.API.get("/health", { silent: true });
      const dot = document.getElementById("health-dot");
      const text = document.getElementById("health-text");
      const statusWrapper = document.getElementById("health-status-wrapper");
      _state.apiHealthy = health?.status === "healthy";
      if (dot && text) {
        if (_state.apiHealthy) {
          dot.className = "status-dot rounded-full bg-green-500";
          text.textContent = "Connected";
          text.className = "text-green-400 font-medium hidden md:inline";
          if (statusWrapper) statusWrapper.className = "flex items-center gap-1.5 text-xs shrink-0 connected";
        } else {
          dot.className = "status-dot rounded-full bg-red-500";
          text.textContent = "Disconnected";
          text.className = "text-red-400 font-medium hidden md:inline";
          if (statusWrapper) statusWrapper.className = "flex items-center gap-1.5 text-xs shrink-0 disconnected";
        }
      }
      _updateNavStats();
    } catch (e) {
      _state.apiHealthy = false;
      const dot = document.getElementById("health-dot");
      const text = document.getElementById("health-text");
      const statusWrapper = document.getElementById("health-status-wrapper");
      if (dot && text) {
        dot.className = "status-dot rounded-full bg-red-500";
        text.textContent = "Disconnected";
        text.className = "text-red-400 font-medium hidden md:inline";
        if (statusWrapper) statusWrapper.className = "flex items-center gap-1.5 text-xs shrink-0 disconnected";
      }
    }
  }

  function _startHealthCheck(intervalMs = 30000) {
    _checkHealth();
    if (_healthInterval) clearInterval(_healthInterval);
    _healthInterval = setInterval(_checkHealth, intervalMs);
  }

  let _healthInterval = null;

  // ============================================================================
  // Utility
  // ============================================================================
  function _escapeHtml(str) {
    if (typeof str !== "string") str = String(str);
    return str.replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

  // Inline markdown for LLM-produced text: escape first, then apply
  // **bold**, `code`, and newline -> <br> so narratives render consistently.
  function _mdInline(str) {
    return _escapeHtml(str == null ? "" : str)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\n/g, "<br>");
  }

  function _getElement(id) {
    const el = document.getElementById(id);
    if (!el) console.warn("Element #" + id + " not found");
    return el;
  }

  // ============================================================================
  // Public App API
  // ============================================================================
  window.App = {
    get state() { return _state; },
    navigate: _navigate,
    registerPage,
    get currentPage() { return _state.currentPage; },

    toast: _toast,
    success: (msg) => _toast(msg, "success"),
    error: (msg) => _toast(msg, "error"),
    info: (msg) => _toast(msg, "info"),
    warning: (msg) => _toast(msg, "warning"),
    escapeHtml: _escapeHtml,
    mdInline: _mdInline,

    // Show which workspace (tenant) the caller is in. An operator has to be
    // able to tell at a glance whose data they are looking at.
    applyTenant: function () {
      var user = (window.RBAC && window.RBAC.getUser()) || {};
      var box = document.getElementById("ud-tenant");
      if (!box) return;
      var tenant = user.tenant_id || "";
      var name = (window.TenantOnboarding && window.TenantOnboarding.getWorkspaceName()) || "";
      var label = name || tenant;
      if (!label) {
        box.style.display = "none";
        return;
      }
      var suffix = name && tenant
        ? ' <span class="ud-tenant-id">' + _escapeHtml(tenant) + "</span>"
        : "";
      box.innerHTML = '<i class="fas fa-building"></i>' + _escapeHtml(label) + suffix;
      box.title = "Workspace: " + label;
      box.style.display = "inline-flex";
    },

    // Generic modal dialog. Contract:
    //   App.showModal(title, bodyHtml, onConfirm, confirmLabel)
    //   - title: string heading
    //   - bodyHtml: HTML string for the modal body
    //   - onConfirm: optional async callback. When provided a Confirm button is
    //     shown (label = confirmLabel || 'Confirm'); when null only a Close
    //     button is shown (label = confirmLabel || 'Close').
    //   App.closeModal() removes the modal.
    showModal: function (title, bodyHtml, onConfirm, confirmLabel) {
      var overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      overlay.id = 'app-modal-overlay';

      var hasConfirm = typeof onConfirm === 'function';
      var confirmBtn = hasConfirm
        ? '<button class="btn btn-primary btn-sm" id="app-modal-confirm">' +
          _escapeHtml(confirmLabel || 'Confirm') + '</button>'
        : '';
      var closeLabel = hasConfirm ? 'Cancel' : (confirmLabel || 'Close');

      overlay.innerHTML =
        '<div class="modal-content" role="dialog" aria-modal="true">' +
        '<div class="flex items-start justify-between mb-3">' +
        '<h3 class="text-lg font-bold">' + _escapeHtml(title) + '</h3>' +
        '<button class="text-gray-400 hover:text-gray-600 text-xl leading-none" id="app-modal-close" aria-label="Close">&times;</button>' +
        '</div>' +
        '<div class="modal-body">' + bodyHtml + '</div>' +
        '<div class="flex justify-end gap-2 mt-4">' +
        '<button class="btn btn-sm btn-secondary" id="app-modal-cancel">' + _escapeHtml(closeLabel) + '</button>' +
        confirmBtn +
        '</div>' +
        '</div>';

      document.body.appendChild(overlay);

      function close() {
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        document.removeEventListener('keydown', onKey);
      }
      function onKey(e) {
        if (e.key === 'Escape') close();
      }
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) close();
      });
      overlay.querySelector('#app-modal-close').addEventListener('click', close);
      overlay.querySelector('#app-modal-cancel').addEventListener('click', close);
      if (hasConfirm) {
        overlay.querySelector('#app-modal-confirm').addEventListener('click', function () {
          var result = onConfirm();
          if (result && typeof result.then === 'function') {
            result.then(function () { close(); }).catch(function () {});
          } else {
            close();
          }
        });
      }
      document.addEventListener('keydown', onKey);
    },

    closeModal: function () {
      var overlay = document.getElementById('app-modal-overlay');
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    },

    logout() {
      localStorage.removeItem('session');
      window.location.hash = '#dashboard';
      window.location.reload();
    },

    markAllNotificationsRead() {
      if (window.NotifComponent) {
        window.NotifComponent.markAllRead();
      }
    },

    // Strip executable content from HTML strings before innerHTML sinks.
    // Page renderers build HTML from server/CSV-derived data — one missed
    // escape upstream becomes stored XSS without this last line of defense.
    _sanitizeHtml(html) {
      try {
        var tpl = document.createElement('template');
        tpl.innerHTML = String(html == null ? '' : html);
        var bad = tpl.content.querySelectorAll(
          'script,iframe,object,embed,link,meta,style,base,form');
        for (var i = 0; i < bad.length; i++) bad[i].remove();
        var els = tpl.content.querySelectorAll('*');
        for (var j = 0; j < els.length; j++) {
          var attrs = els[j].attributes;
          for (var k = attrs.length - 1; k >= 0; k--) {
            var name = attrs[k].name.toLowerCase();
            var val = String(attrs[k].value || '');
            if (name.indexOf('on') === 0 ||
                ((name === 'href' || name === 'src' || name === 'xlink:href') &&
                 /^\s*javascript:/i.test(val))) {
              els[j].removeAttribute(attrs[k].name);
            }
          }
        }
        return tpl.innerHTML;
      } catch (e) {
        return '';
      }
    },

    // Load region with loading/empty/error states
    async loadRegion(elementId, loaderFn) {
      var el = typeof elementId === 'string' ? document.getElementById(elementId) : elementId;
      if (!el) return null;
      var h = el.offsetHeight || 100;
      el.innerHTML = '<div class="skeleton" style="height:' + h + 'px;border-radius:8px;"></div>';
      try {
        var result = await loaderFn();
        if (typeof result === 'string') el.innerHTML = App._sanitizeHtml(result);
        return result;
      } catch (e) {
        el.innerHTML = '<div class="error-state"><i class="fas fa-exclamation-circle"></i><p>' + _escapeHtml(e.message || 'Error') + '</p></div>';
        return null;
      }
    },

    // Lazy-load a page module on first access (dynamic script injection)
    _pageCache: {},
    _pageLoading: {},
    // Known page routes whose source file uses different casing than the hash
    // route. On case-sensitive filesystems (Linux) the naive lowercase lookup
    // 404s, so we resolve the real filename explicitly.
    _pageFiles: {
      dashboardstudio: 'dashboardStudio'
    },
    _resolvePageFile: function (pageName) {
      return this._pageFiles[pageName] || pageName;
    },
    async loadPage(pageName) {
      if (this._pageCache[pageName]) return this._pageCache[pageName];
      // Dedupe concurrent loads: two rapid route events would otherwise inject
      // the same <script> twice and re-declare its top-level const (SyntaxError).
      if (this._pageLoading[pageName]) return this._pageLoading[pageName];
      var self = this;
      var filename = this._resolvePageFile(pageName);
      var promise = new Promise(function(resolve, reject) {
        var script = document.createElement('script');
        script.src = 'js/pages/' + filename + '.js?v=' + Date.now();
        script.onload = function() {
          var mod = window._PAGES ? window._PAGES['#' + pageName] : null;
          self._pageCache[pageName] = mod;
          resolve(mod);
        };
        script.onerror = function() { reject(new Error('Failed to load: ' + pageName)); };
        document.body.appendChild(script);
      });
      this._pageLoading[pageName] = promise;
      promise.catch(function() { delete self._pageLoading[pageName]; });
      return promise;
    },

    // Refresh table list from API
    async refreshTables() {
      try {
        var tables = await window.API.tables();
        _state.tables = tables.tables || tables;
        // Backend lists platform/system-owned tables so the UI (BI dashboard
        // table pickers, best-table fallbacks) can exclude them.
        _state.systemTables = tables.system_tables || [];
        _updateNavStats();
      } catch (e) {
        console.warn("Failed to refresh tables:", e);
      }
    },
  };

  // ============================================================================
  // Onboarding Wizard
  // ============================================================================
  function _checkOnboarding() {
    const completed = localStorage.getItem("onboarding_completed");
    if (completed === "true") return;
    // Skip onboarding when a dashboard deep-link is active (sharing / screenshots)
    if (window.location.search && window.location.search.indexOf("dash=") !== -1) return;

    const steps = [
      { title: "Welcome to DataAgent", content: "Your local AI-powered analytics platform. Let's get you set up in 4 steps.", action: null },
      { title: "Set up your local AI", content: "The AI chat runs a model on your machine — nothing leaves the computer. If Ollama is already running we'll use it; otherwise we can install it for you. SQL, Excel, dashboards and exports all work without a model.", action: null, llm: true },
      { title: "Upload Your Data", content: "Drag & drop CSV, Excel, or Parquet files to the Upload page. We'll auto-detect schemas.", action: "#upload" },
      { title: "Ask Questions", content: "Use the AI Chat to ask natural language questions. The agent writes SQL, runs analysis, and builds charts.", action: "#chat" },
    ];

    let currentStep = 0;

    function showStep() {
      const step = steps[currentStep];
      const html = '<div class="onboarding-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/50">' +
        '<div class="bg-white dark:bg-gray-800 rounded-xl p-8 max-w-md w-full mx-4 shadow-2xl">' +
        '<h3 class="text-xl font-bold mb-2">' + _escapeHtml(step.title) + '</h3>' +
        '<p class="text-gray-600 dark:text-gray-300 mb-6">' + _escapeHtml(step.content) + '</p>' +
        '<div id="onboarding-llm-status" class="hidden mb-5 text-sm"></div>' +
        '<div class="flex justify-between items-center">' +
        (currentStep > 0 ? '<button class="px-4 py-2 text-gray-600 hover:text-gray-900" onclick="App._onboardingPrev()">Back</button>' : '<div></div>') +
        '<div class="flex gap-2">' +
        (currentStep < steps.length - 1 ?
          '<button class="px-4 py-2 bg-indigo-600 text-white rounded-lg" onclick="App._onboardingNext()">Next</button>' :
          '<button class="px-4 py-2 bg-indigo-600 text-white rounded-lg" onclick="App._onboardingFinish()">Get Started</button>') +
        '</div>' +
        '</div>' +
        '</div>' +
        '</div>';
      document.body.insertAdjacentHTML('beforeend', html);
      if (step.llm) _runLlmSetupStep();
    }

    // The AI chat needs a local model. Rather than sending the user off to
    // install Ollama and pull a 4.7 GB model by hand, this step runs a live
    // check and offers a one-click install + pull. Everything else in the
    // app works with no model at all, so the step is skippable.
    async function _runLlmSetupStep() {
      const box = document.getElementById("onboarding-llm-status");
      if (!box) return;
      box.classList.remove("hidden");
      box.innerHTML = '<span class="text-gray-500">Checking for a local model…</span>';
      let deps = null;
      try {
        deps = await window.API.get("/llm/dependencies");
      } catch (e) {
        box.innerHTML = '<span class="text-gray-500">Could not check automatically — set up Ollama from Settings.</span>';
        return;
      }
      const ollama = (deps && deps.ollama) || {};
      const models = ollama.models || [];
      if (ollama.server_reachable && models.length) {
        box.innerHTML = '<span class="text-emerald-600 font-medium">✓ Local model ready (' +
          _escapeHtml(models[0].name || models[0]) + ')</span>';
        return;
      }
      if (!ollama.installed) {
        box.innerHTML = '<button class="px-4 py-2 bg-indigo-600 text-white rounded-lg" onclick="App._onboardingInstallOllama()">' +
          'Install Ollama for me</button>';
      } else {
        box.innerHTML = '<div class="text-gray-600 dark:text-gray-300 mb-2">Ollama is installed but not running or has no model yet.</div>' +
          '<button class="px-4 py-2 bg-indigo-600 text-white rounded-lg" onclick="App._onboardingStartOllama()">Start Ollama</button>';
      }
    }

    window.App._onboardingInstallOllama = async function () {
      const box = document.getElementById("onboarding-llm-status");
      if (!box) return;
      box.innerHTML = '<span class="text-gray-500">Installing Ollama… this takes a minute.</span>';
      try {
        const res = await window.API.post("/llm/ollama/install", {});
        if (!res || !res.success) {
          box.innerHTML = '<span class="text-amber-600">Install did not start: ' +
            _escapeHtml((res && res.error) || "unknown error") +
            '. You can install Ollama manually from Settings.</span>';
          return;
        }
        if (res.already_installed) { _runLlmSetupStep(); return; }
        const jobId = res.job_id;
        const poll = setInterval(async () => {
          try {
            const st = await window.API.get("/llm/ollama/install/" + jobId);
            const job = (st && st.job) || {};
            if (job.status === "done" || job.status === "completed" || job.status === "success") {
              clearInterval(poll);
              window.App._onboardingStartOllama();
            } else if (job.status === "error" || job.status === "failed") {
              clearInterval(poll);
              box.innerHTML = '<span class="text-amber-600">Install failed: ' +
                _escapeHtml(job.error || "unknown error") + '. Install Ollama manually from Settings.</span>';
            }
          } catch (e) { /* keep polling */ }
        }, 1500);
      } catch (e) {
        box.innerHTML = '<span class="text-amber-600">Install failed. You can install Ollama manually from Settings.</span>';
      }
    };

    window.App._onboardingStartOllama = async function () {
      const box = document.getElementById("onboarding-llm-status");
      if (!box) return;
      box.innerHTML = '<span class="text-gray-500">Starting Ollama…</span>';
      try {
        const res = await window.API.post("/llm/ollama/start", {});
        if (!res || !res.success) {
          box.innerHTML = '<span class="text-amber-600">Could not start Ollama: ' +
            _escapeHtml((res && res.error) || "unknown error") + '</span>';
          return;
        }
        box.innerHTML = '<span class="text-gray-500">Downloading the model (about 4.7 GB)…</span>';
        const model = (window.ONBOARDING_MODEL) || "qwen2.5:7b";
        const pull = await window.API.post("/llm/ollama/pull", { model: model });
        if (!pull || !pull.success) {
          box.innerHTML = '<span class="text-amber-600">' +
            _escapeHtml((pull && pull.error) || "Could not download the model") +
            ' — pull it from Settings.</span>';
          return;
        }
        box.innerHTML = '<span class="text-emerald-600 font-medium">✓ Downloading your model — this runs in the background. ' +
          'You can start asking questions as soon as it finishes.</span>';
      } catch (e) {
        box.innerHTML = '<span class="text-amber-600">Setup failed. You can do this manually from Settings.</span>';
      }
    };

    window.App._onboardingNext = function() {
      document.querySelector('.onboarding-overlay')?.remove();
      currentStep++;
      showStep();
    };

    window.App._onboardingPrev = function() {
      document.querySelector('.onboarding-overlay')?.remove();
      currentStep--;
      showStep();
    };

    window.App._onboardingFinish = function() {
      document.querySelector('.onboarding-overlay')?.remove();
      localStorage.setItem("onboarding_completed", "true");
    };

    showStep();
  }

  // ============================================================================
  // Navigation Stats Update
  // ============================================================================
  function _updateNavStats() {
    const tableCount = Object.keys(_state.tables || {}).length;
    const tableStat = document.getElementById("nav-table-count");
    if (tableStat) tableStat.textContent = tableCount;
  }

  // ============================================================================
  // Feature Flag Application
  // ============================================================================
  function _applyFeatureFlags() {
    const llmEnabled = _state.llm_enabled && _state.features.enable_llm_agent;
    const llmElements = document.querySelectorAll(".feature-llm");
    llmElements.forEach(function(el) {
      el.style.display = llmEnabled ? "" : "none";
    });

    // If LLM disabled and current page is chat, redirect to SQL
    if (!llmEnabled && (window.location.hash === "#chat" || window.location.hash === "#chat-pro")) {
        window.location.hash = "#sql";
    }
  }

  // ============================================================================
  // Account-lifecycle mode
  // ============================================================================

  // The account screen (password reset, e-mail confirmation, invitation)
  // takes the whole window: no top nav, no footer. Leaving it has to undo
  // exactly that, so the application can take the screen back.
  function _leaveAccountMode() {
    if (!document.body.classList.contains("account-mode")) return;
    document.body.classList.remove("account-mode");
    var host = document.getElementById("page-content");
    if (host && host.querySelector(".account-view")) {
      host.innerHTML =
        '<div class="empty-state py-20"><i class="fas fa-rocket text-primary-400"></i>' +
        '<p class="mt-3 font-medium">Loading DataAgent...</p></div>';
    }
  }

  // The parts of the boot that need a session. They are deferred when the
  // page opened on an account link and run as soon as the user navigates
  // away from it.
  let _bootDeferred = false;

  async function _bootApplication() {
    // Load feature config first
    await _loadConfig();

    // Apply feature flags - show/hide LLM-dependent UI elements
    _applyFeatureFlags();

    // Apply premium feature gating to nav items (data-feature attributes)
    if (window.RBAC && window.RBAC.applyToNav) {
      try {
        window.RBAC.applyToNav();
      } catch (e) {
        console.warn("Failed to apply RBAC nav gating:", e);
      }
    }

    // Auth gate: when no valid session exists, a full-screen login overlay
    // is shown and the app stays behind it until sign-in.
    if (window.Auth) {
      const authed = await window.Auth.init();
      if (!authed) return false;
    }

    // Start health checks and data loading
    _startHealthCheck();
    App.refreshTables();
    // A hosted signup gets the one-screen tenant onboarding; the local
    // single-machine wizard is not for it.
    const tenantPending = !!(window.TenantOnboarding && window.TenantOnboarding.isPending());
    if (!tenantPending) _checkOnboarding();
    return true;
  }

  // ============================================================================
  // Initialize
  // ============================================================================
  async function _init() {
    // The hash listener is registered before anything else: an account link
    // owns the screen on boot, and the user must still be able to navigate
    // away from it (and back) without a full reload.
    window.addEventListener("hashchange", function () {
      // An account link can arrive as a hash change on a page that is
      // already open. The token lives in the fragment, so it has to be
      // picked up before the router — and the router's fallback — sees it.
      if (window.AccountView && window.AccountView.init()) {
        // The account screen took the window: the session-bound part of
        // the boot is deferred until the user navigates away from it.
        _bootDeferred = true;
        return;
      }
      _leaveAccountMode();
      if (_bootDeferred) {
        _bootDeferred = false;
        _bootApplication().then(function (ok) {
          if (ok) _handleRoute();
        });
        return;
      }
      _handleRoute();
    });

    // An account-lifecycle link (password reset, e-mail confirmation,
    // invitation) owns the whole screen. Its token lives in the URL
    // fragment, so the router must never see it. The shell is still wired
    // up below: the user has to be able to navigate away from the screen.
    const accountScreen = !!(window.AccountView && window.AccountView.init());
    if (accountScreen) _bootDeferred = true;
    else if (!(await _bootApplication())) return;

    // Dark mode
    if (_state.darkMode) document.documentElement.setAttribute("data-theme", "dark");
    function _toggleDark() {
      _state.darkMode = !_state.darkMode;
      localStorage.setItem("darkMode", _state.darkMode);
      document.documentElement.setAttribute("data-theme", _state.darkMode ? "dark" : "light");
      var text = document.getElementById("dark-toggle-text");
      if (text) text.textContent = _state.darkMode ? "Light Mode" : "Dark Mode";
    }
    ["dark-toggle", "theme-toggle"].forEach(function(id) {
      var btn = document.getElementById(id);
      if (btn) btn.addEventListener("click", _toggleDark);
    });
    var dd2 = document.getElementById("dark-toggle-2");
    if (dd2) dd2.addEventListener("click", function() {
      _toggleDark();
      var dropdown = document.getElementById("user-dropdown");
      if (dropdown) dropdown.classList.remove("open");
    });

    // Sidebar toggle
    const sidebarToggle = document.getElementById("sidebar-toggle");
    const sidebar = document.getElementById("sidebar");
    const sidebarOverlay = document.getElementById("sidebar-overlay");
    if (sidebarToggle && sidebar) {
      sidebarToggle.addEventListener("click", function () {
        sidebar.classList.toggle("open");
        if (sidebarOverlay) sidebarOverlay.classList.toggle("hidden");
      });
    }
    if (sidebarOverlay) {
      sidebarOverlay.addEventListener("click", function () {
        sidebar?.classList.remove("open");
        sidebarOverlay.classList.add("hidden");
      });
    }

    // Mobile nav overlay close
    const mobileOverlay = document.getElementById("mobile-nav-overlay");
    if (mobileOverlay) {
      mobileOverlay.addEventListener("click", function () {
        document.getElementById("mobile-nav")?.classList.add("hidden");
        this.classList.add("hidden");
      });
    }

    // Note: notification bell, user menu, and outside-click dropdown close
    // are handled by web/js/components/notifications.js (uses .open class)
    // Do NOT duplicate them here to avoid toggle conflicts.

    // Direct navigation clicks on all nav links
    // Intercept all .nav-dropdown-item and .mobile-nav-item clicks
    // to ensure redirect works even if hashchange has issues
    document.querySelector("#topnav-nav")?.addEventListener("click", function (e) {
      var link = e.target.closest("a[href^='#']");
      if (link) {
        e.preventDefault();
        _navigate(link.getAttribute("href"));
      }
    });
    document.querySelector("#mobile-nav")?.addEventListener("click", function (e) {
      var link = e.target.closest("a[href^='#']");
      if (link) {
        e.preventDefault();
        _navigate(link.getAttribute("href"));
      }
    });

    // Handle browser back/forward and direct URL changes.
    // (The hashchange listener is registered at the top of _init so it is
    // already live when the boot was taken over by an account link.)
    window.addEventListener("focus", _refreshBillingOnFocus);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) _refreshBillingOnFocus();
    });

    // The session-bound part of the boot already ran (or is deferred until
    // the user leaves the account screen). Render the current route.
    if (accountScreen) return;
    _handleRoute();
    if (window.TenantOnboarding && window.TenantOnboarding.isPending()) {
      window.TenantOnboarding.showIfPending();
    }
  }


  // Run initialization when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _init);
  } else {
    _init();
  }
})();