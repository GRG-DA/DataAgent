/**
 * API Client Module
 * Wraps all FastAPI endpoints with a unified global API object.
 * Uses fetch() with proper method headers, body handling, and error management.
 */
(function () {
  "use strict";

  // The FastAPI backend runs on port 8002. The SPA can be served from a static
  // server on another port (e.g. serve_web.py on 3000), so API calls must
  // target the API origin, not the page origin. Override with window.API_BASE_URL
  // or window.API_PORT if your backend runs elsewhere.
  const _API_PORT = window.API_PORT || "8002";
  const BASE_URL = window.API_BASE_URL ||
    (String(window.location.port) === _API_PORT
      ? window.location.origin
      : window.location.protocol + "//" + window.location.hostname + ":" + _API_PORT);
  const API_PREFIX = "";

  // Expose the resolved API origin so other scripts (page modules) can build
  // absolute URLs for raw fetch() calls.
  window.__API_BASE__ = BASE_URL;

  // ── Loading indicator helpers ──────────────────────────────────────────
  let _activeRequests = 0;
  let _loadEpoch = 0;      // bumped on route change; orphans from the previous epoch must not hide the overlay
  let _quiet = false;   // background batch mode: suppresses the global loading overlay
  const _overlay = () => document.getElementById("loading-overlay");
  const _spinner = () => document.getElementById("loading-spinner");

  function _showLoading() {
    _activeRequests++;
    const overlay = _overlay();
    if (overlay) overlay.classList.remove("hidden");
    // Failsafe: a hung request (or a tab-switch that orphans epochs) must
    // never leave the global overlay stuck. Force-clear after 15s.
    const epochAtShow = _loadEpoch;
    setTimeout(function () {
      if (_loadEpoch === epochAtShow && _activeRequests > 0) {
        _resetLoading();
      }
    }, 15000);
  }

  function _hideLoading(epoch) {
    // Ignore completions from a previous route's in-flight requests.
    if (epoch !== undefined && epoch !== _loadEpoch) return;
    _activeRequests = Math.max(0, _activeRequests - 1);
    if (_activeRequests === 0) {
      const overlay = _overlay();
      if (overlay) overlay.classList.add("hidden");
    }
  }

  // Route change: orphan any in-flight page loads so a slow previous page
  // cannot leave the global overlay stuck on the next route.
  function _resetLoading() {
    _loadEpoch++;
    _activeRequests = 0;
    const overlay = _overlay();
    if (overlay) overlay.classList.add("hidden");
    const spinner = _spinner();
    if (spinner) spinner.classList.add("hidden");
  }

  // CSRF double-submit token: the server sets a JS-readable `csrf_token`
  // cookie and expects the same value in the X-CSRF-Token header.
  function _csrfToken() {
    if (typeof document === "undefined" || !document.cookie) return null;
    const hit = document.cookie
      .split(";")
      .map((c) => c.trim())
      .find((c) => c.startsWith("csrf_token="));
    return hit ? decodeURIComponent(hit.slice("csrf_token=".length)) : null;
  }

  // ── Core fetch wrapper ─────────────────────────────────────────────────
  // `signal` (an AbortSignal) is optional: when supplied it is handed to
  // fetch() so the caller can cancel an in-flight request. Without it the
  // behaviour is exactly as before (no signal on the fetch config).
  async function _request(method, path, { body, params, headers: extraHeaders, silent, signal } = {}) {
    const quiet = _quiet || !!silent;
    const epoch = _loadEpoch;
    if (!quiet) _showLoading();

    let url = BASE_URL + API_PREFIX + path;

    // Append query parameters
    if (params) {
      const qs = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== "") {
          qs.append(key, String(value));
        }
      }
      const qsStr = qs.toString();
      if (qsStr) url += "?" + qsStr;
    }

    const config = { method, headers: {} };

    // Cancellation: hand the AbortSignal to fetch() so an aborted request
    // is really dropped by the browser instead of running to completion
    // on the server (a stopped LLM turn would otherwise burn 30-90s of
    // server work for a reply nobody is waiting for).
    if (signal) {
      config.signal = signal;
    }

    // Set content type unless body is FormData (browser sets multipart boundary)
    if (body && !(body instanceof FormData)) {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
      config.body = body;
    }

    // Merge extra headers
    if (extraHeaders) {
      Object.assign(config.headers, extraHeaders);
    }

    // Attach the app JWT when present (Google SSO / local sessions).
    // Public endpoints ignore it; protected endpoints require it.
    const token = (typeof window !== "undefined" && window.RBAC && window.RBAC.getToken)
      ? window.RBAC.getToken()
      : null;
    if (token) {
      config.headers["Authorization"] = "Bearer " + token;
    }

    // CSRF: echo the double-submit cookie in the header. Only needed when
    // the request authenticates with the session cookie (a form cannot set
    // the Authorization header, so a bearer-token request is exempt), but
    // sending it always is harmless and avoids a subtle failure mode.
    const csrf = _csrfToken();
    if (csrf) {
      config.headers["X-CSRF-Token"] = csrf;
    }
    // Send the session cookie with every same-origin call.
    if (typeof document !== "undefined" && document.cookie) {
      config.credentials = "same-origin";
    }

    try {
      const response = await fetch(url, config);

      // Handle non-JSON responses (e.g. file downloads)
      const ct = response.headers.get("content-type") || "";
      if (!response.ok) {
        let errPayload;
        if (ct.includes("application/json")) {
          errPayload = await response.json();
        } else {
          errPayload = { detail: await response.text() };
        }
        const error = new Error(errPayload.detail || `HTTP ${response.status}`);
        error.status = response.status;
        error.data = errPayload;
        // Premium gate: show the upgrade overlay once instead of stacking
        // an error toast for every doomed free-plan API call.
        if (
          response.status === 403 &&
          (response.headers.get("X-Upgrade-Required") === "true" ||
            (typeof errPayload.detail === "string" && errPayload.detail.indexOf("Upgrade required") === 0))
        ) {
          error.upgradeRequired = true;
          error.feature =
            response.headers.get("X-Required-Feature") ||
            (function (m) { return m ? m[1] : null; })(/Upgrade required: '([^']+)'/.exec(String(errPayload.detail || "")));
          if (window.RBAC && typeof window.RBAC.showUpgradeOverlay === "function" && error.feature) {
            // Dedupe: only one overlay toast path; refresh entitlements so the
            // client stops believing the feature is already unlocked.
            window.RBAC.showUpgradeOverlay(error.feature);
            if (window.RBAC.setEntitlements && window.RBAC.getEntitlements) {
              const ent = Object.assign({}, window.RBAC.getEntitlements() || {});
              if (error.feature in ent) ent[error.feature] = false;
              window.RBAC.setEntitlements(ent);
              if (window.RBAC.applyToNav) window.RBAC.applyToNav();
            }
          }
        }
        throw error;
      }

      // If the response has no body (e.g. 204 No Content)
      if (response.status === 204) return null;
      // For blob responses (downloads)
      if (ct.includes("application/octet-stream") || ct.includes("zip") || ct.includes("spreadsheet") || 
          ct.includes("csv") || ct.includes("parquet") || ct.includes("pdf") ||
          ct.includes("presentationml") || ct.includes("officeDocument") ||
          ct.includes("image/")) {
        return await response.blob();
      }

      if (ct.includes("application/json")) {
        return await response.json();
      }

      // Fallback: try JSON, fall back to text
      const text = await response.text();
      try {
        return JSON.parse(text);
      } catch (_) {
        return text;
      }
    } catch (err) {
      // Re-throw network errors or API errors. Silent (background/polling)
      // requests must not spam error toasts. Upgrade-required 403s already
      // opened the premium overlay above — never toast those either.
      // A cancelled request (AbortError) is a deliberate stop, not a
      // failure: no error toast, no console noise — the caller decides.
      const isAbort = !!(err && (err.name === "AbortError" || err.code === 20));
      const isUpgrade = err && err.upgradeRequired;
      if (!quiet && !isUpgrade && !isAbort && typeof window !== "undefined" && window.App && typeof window.App.toast === "function") {
        try {
          window.App.toast(err.message || "Network error", "error");
        } catch (_) { /* toast system not ready */ }
      }
      throw err;
    } finally {
      if (!quiet) _hideLoading(epoch);
    }
  }

  // Shorthand helpers
  const GET = (path, opts) => _request("GET", path, opts);
  const POST = (path, body, opts) => _request("POST", path, { ...opts, body });
  const PUT = (path, body, opts) => _request("PUT", path, { ...opts, body });
  const PATCH = (path, body, opts) => _request("PATCH", path, { ...opts, body });
  const DELETE = (path, opts) => _request("DELETE", path, opts);

  function _saveBlob(data, filename) {
    const blob = data instanceof Blob
      ? data
      : new Blob([data == null ? "" : String(data)], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "download";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  // Download through the authenticated client. window.open()/plain <a href>
  // cannot attach the Bearer token, so protected export endpoints answer 401
  // in the new tab. GET() already returns a Blob for file content types.
  function download(path, filename) {
    return GET(path).then(function (data) {
      if (data == null) throw new Error("Empty download");
      _saveBlob(data, filename);
      return true;
    });
  }

  // ── Global API Object ──────────────────────────────────────────────────
  window.API = {
    // Toggle quiet/batch mode: suppresses the global loading overlay while set.
    quiet: (v) => { _quiet = !!v; },
    // Introspection for tests/debugging: current loading-counter state.
    debugLoadingState: () => ({ active: _activeRequests, epoch: _loadEpoch, quiet: _quiet }),
    // Drop orphaned loading counts when the SPA navigates away mid-request.
    resetLoading: () => { _resetLoading(); },

    // Health & basic
    health: () => GET("/health", { silent: true }),
    upload: (file) => {
      const fd = new FormData();
      fd.append("file", file);
      return POST("/upload", fd);
    },

    // Data operations
    clean: (filename, sheet) => POST("/clean", { filename, sheet }),
    cleanPreview: (tableName, opts = {}) => POST("/clean/preview", { table_name: tableName, ...opts }),
    load: (filename, sheet, table) => POST("/load", { filename, sheet, table_name: table }),
    // `opts.signal` (an AbortSignal) cancels the underlying HTTP request:
    // the browser drops the connection and the server stops working on a
    // turn the user already stopped. Omit it and nothing changes.
    ask: (question, opts = {}) => POST("/ask", {
      question,
      table: opts.table || undefined,
      tables: (opts.tables && opts.tables.length) ? opts.tables : undefined,
      context: {
        ...(opts.sessionId ? { session_id: opts.sessionId } : {}),
        ...(opts.context || {}),
      },
    }, opts.signal ? { signal: opts.signal } : undefined),

    // Tables
    tables: () => GET("/tables"),
    tableSample: (name, n = 5) => POST(`/tables/${encodeURIComponent(name)}/sample?n=${n}`),
    dropTable: (name) => DELETE(`/tables/${encodeURIComponent(name)}`),
    // Server requires {confirm:true} — an unconfirmed call is rejected with 400.
    dropAllTables: () => DELETE("/tables", { body: { confirm: true } }),

    // Merge
    merge: (left, right, leftKey, rightKey, joinType = "inner") =>
      POST("/merge", { left_table: left, right_table: right, left_key: leftKey, right_key: rightKey, join_type: joinType }),

    // SQL
    sql: (query, limit = 1000) => POST("/sql", { query, limit }),

    // Schema
    schema: () => GET("/schema"),

    // Dashboard
    dashboardKPI: () => GET("/dashboard/kpi"),
    revenueTrend: () => GET("/dashboard/revenue-trend"),
    revenueByDim: (dim) => GET("/dashboard/revenue-by-dimension", { params: { dimension: dim } }),
    topProducts: (n = 10) => GET("/dashboard/top-products", { params: { n } }),
    growthMetrics: () => GET("/dashboard/growth"),

    // Profile / quality
    profile: (table) => GET(`/profile/${encodeURIComponent(table)}`),
    columnDetail: (table, col) => GET(`/profile/${encodeURIComponent(table)}/column/${encodeURIComponent(col)}`),
    outliers: (table) => GET(`/profile/${encodeURIComponent(table)}/outliers`),
    outlierSuggestions: (table) => GET(`/profile/${encodeURIComponent(table)}/outliers/suggestions`),
    qualityScore: (table) => GET(`/quality/${encodeURIComponent(table)}`),
    piiScan: (table) => GET(`/quality/pii/${encodeURIComponent(table)}`),

    // Analysis - trend forecast & time series
    forecast: (table, dateCol, valCol, periods = 12) =>
      GET(`/analysis/forecast/${encodeURIComponent(table)}`, { params: { date_col: dateCol, value_col: valCol, periods } }),
    decomposition: (table, dateCol, valCol, period, model = "additive") =>
      GET(`/analysis/decomposition/${encodeURIComponent(table)}`, { params: { date_col: dateCol, value_col: valCol, period, model } }),
    survival: (table, timeCol, eventCol, groupCol) =>
      GET(`/analysis/survival/${encodeURIComponent(table)}`, { params: { time_col: timeCol, event_col: eventCol, group_col: groupCol } }),

    // Analysis - Anomalies & patterns
    anomalies: (table) => GET(`/analysis/anomalies/${encodeURIComponent(table)}`),
    insights: (table) => GET(`/analysis/insights/${encodeURIComponent(table)}`),
    // `opts` lets a caller opt out of the global loading overlay (the LLM
    // call can run for 30-90s; the page renders its own loading state).
    llmInsights: (table, question = "", opts = {}) =>
      POST("/analysis/insights/llm", { table_name: table, user_question: question }, opts),
    storytelling: (table, question = "", force = false, opts = undefined) =>
      POST("/analysis/storytelling", { table_name: table, user_question: question, force: !!force }, opts),
    storytellingStatus: (jobId) => GET(`/analysis/storytelling/status/${encodeURIComponent(jobId)}`),

    // Analysis - segment breakdowns & correlations
    // The backend endpoint declares `segment_col`; `seg_col` was silently
    // ignored and the server fell back to auto-detecting a segment column.
    segmentation: (table, segCol, metricCol) =>
      GET(`/analysis/segmentation/${encodeURIComponent(table)}`, { params: { segment_col: segCol, metric_col: metricCol } }),
    correlation: (table) => GET(`/analysis/correlation/${encodeURIComponent(table)}`),

    // Analysis - Normality testing
    normality: (table, col, method = "shapiro") =>
      GET(`/analysis/normality/${encodeURIComponent(table)}/${encodeURIComponent(col)}`, { params: { method } }),
    normalityAll: (table) => GET(`/analysis/normality/${encodeURIComponent(table)}`),

    // Analysis - Hypothesis testing
    // The backend only accepts: ttest, paired_ttest, mannwhitney, chi2,
    // anova, pearsonr. "auto" (and "t-test") are rejected as unknown test
    // types, so the default here is a valid one.
    hypothesis: (table, colA, colB, testType = "ttest", groupCol, alpha = 0.05) =>
      POST("/analysis/hypothesis", { table_name: table, column_a: colA, column_b: colB, test_type: testType, group_col: groupCol, alpha }),

    // Analysis - Regression & modeling
    regression: (table, target, features, type = "linear") =>
      POST("/analysis/regression", { table_name: table, target_col: target, feature_cols: features, regression_type: type }),
    featureImportance: (table, target, method = "permutation") =>
      POST("/analysis/feature-importance", { table_name: table, target_col: target, method }),
    autoSelectFeatures: (table, target) =>
      POST("/analysis/auto-select-features", { table_name: table, target_col: target, max_features: 10 }),
    compareModels: (table, target, features) =>
      POST("/analysis/compare-models", { table_name: table, target_col: target, feature_cols: features }),
    autoAnalyze: (table, target) =>
      POST("/analysis/auto-analyze", { table_name: table, target_col: target }),

    // Analysis - Confidence intervals & stats
    confidenceInterval: (table, col, confidence = 0.95, method = "t") =>
      GET(`/analysis/confidence-interval/${encodeURIComponent(table)}/${encodeURIComponent(col)}`, { params: { confidence, method } }),

    // Analysis - A/B testing & power
    abTest: (table, groupCol, metricCol, groupA, groupB, alpha = 0.05) =>
      POST("/analysis/ab-test", { table_name: table, group_col: groupCol, metric_col: metricCol, group_a: groupA, group_b: groupB, alpha }),
    powerAnalysis: (effectSize, alpha = 0.05, power = 0.8) =>
      POST("/analysis/power-analysis", { effect_size: effectSize, alpha, power }),
    conversionTest: (table, groupCol, conversionCol, groupA, groupB, alpha = 0.05) =>
      POST("/analysis/conversion-test", { table_name: table, group_col: groupCol, conversion_col: conversionCol, group_a: groupA, group_b: groupB, alpha }),
    sequentialTest: (table, groupCol, metricCol, groupA, groupB, alpha = 0.05, spending = "obrien_fleming") =>
      POST("/analysis/sequential-test", { table_name: table, group_col: groupCol, metric_col: metricCol, group_a: groupA, group_b: groupB, alpha, spending_function: spending }),
    sampleSize: (baselineRate, mdePct = 10, alpha = 0.05, power = 0.80) =>
      POST("/analysis/sample-size", { baseline_rate: baselineRate, mde_pct: mdePct, alpha, power }),

    // Analysis - Bayesian
    bayesianTest: (table, colA, colB, groupCol, prior = "default") =>
      POST("/analysis/bayesian", { table_name: table, column_a: colA, column_b: colB, group_col: groupCol, prior_mean: 0, prior_std: 10 }),
    bayesianCorr: (table, colX, colY) =>
      POST("/analysis/bayesian/correlation", { table_name: table, column_x: colX, column_y: colY }),

    // Analysis - Advanced trend forecast
    advancedForecast: (table, dateCol, valCol, periods = 12, method = "auto", confidence = 0.95) =>
      POST("/analysis/forecast/advanced", { table_name: table, date_col: dateCol, value_col: valCol, periods, method, confidence }),

    // Analysis - Cohort & Churn
    cohort: (table, userIdCol, dateCol, period = "month", revenueCol = "", maxPeriods = 12) =>
      POST("/analysis/cohort", { table_name: table, user_id_col: userIdCol, date_col: dateCol, cohort_period: period, revenue_col: revenueCol, max_periods: maxPeriods }),
    churn: (table, userIdCol, dateCol, inactivityDays = 30) =>
      POST("/analysis/churn", { table_name: table, user_id_col: userIdCol, date_col: dateCol, inactivity_days: inactivityDays }),

    // Analysis - Comparison
    comparePeriods: (table, dateCol, metricCol, groupCol = "", periodType = "month", alpha = 0.05) =>
      POST("/analysis/compare/periods", { table_name: table, date_col: dateCol, metric_col: metricCol, group_col: groupCol, period_type: periodType, alpha }),
    compareDiff: (tableBefore, tableAfter, keyCol = "") =>
      POST("/analysis/compare/diff", { table_before: tableBefore, table_after: tableAfter, key_column: keyCol }),

    // Chart recommendations
    chartRecommend: (table, topN = 5) =>
      GET(`/analysis/chart-recommend/${encodeURIComponent(table)}`, { params: { top_n: topN } }),
    dataDictionary: (table) => GET(`/dictionary/${encodeURIComponent(table)}`),
    discoverRelationships: () => GET("/analysis/relationships"),
    detectAnomalies: (tableName, method = "iqr", threshold = 1.5) =>
      POST("/analysis/anomalies", { table_name: tableName, method, threshold }),
    llmChartRecommend: (tableName, prompt = "", relatedTables = []) =>
      POST("/llm/chart-recommend", { table_name: tableName, prompt, related_tables: relatedTables }),
    autoBuildPlan: (table, options = {}) =>
      POST(`/dashboard/auto-build-plan/${encodeURIComponent(table)}`, {
        purpose: options.purpose || "executive",
        audience: options.audience || "executive",
        style: options.style || "light",
        kpi_focus: options.kpiFocus || [],
        prompt: options.prompt || "",
        sections: options.sections || []
      }),

    // Recreate a dashboard from a screenshot
    analyzeImage: (file) => {
      const fd = new FormData();
      fd.append("image", file);
      return POST("/dashstudio/analyze-image", fd);
    },
    buildFromImage: (file, options = {}) => {
      const fd = new FormData();
      fd.append("image", file);
      fd.append("table_name", options.table || "");
      fd.append("title", options.title || "");
      fd.append("spec_sha", options.specSha || "");
      fd.append("spec_json", options.specJson || "");
      return POST("/dashstudio/build-from-image", fd);
    },
    // Import a real Power BI .pbix/.pbit — deterministic layout+theme parse.
    importPbix: (file) => {
      const fd = new FormData();
      fd.append("file", file);
      return POST("/dashstudio/import-pbix", fd);
    },
    // Build validated blankdash plans (one per PBIX page) from the parsed spec.
    buildFromPbix: (options = {}) => {
      const fd = new FormData();
      fd.append("table_name", options.table || "");
      fd.append("title", options.title || "");
      fd.append("spec_json", options.specJson || "");
      fd.append("ai", options.ai === false ? "false" : "true");
      return POST("/dashstudio/build-from-pbix", fd);
    },

    // Reporting
    report: (table) => GET(`/report/${encodeURIComponent(table)}`),
    exportModel: (result) => POST("/analysis/export-model", result),

    // Metadata
    metadata: (table) => GET(`/metadata/${encodeURIComponent(table)}`),
    saveMetadata: (table, col, desc, tags) =>
      PUT(`/metadata/${encodeURIComponent(table)}/${encodeURIComponent(col)}`, { description: desc, tags }),

    // Queries
    saveQuery: (name, query) => POST("/queries/save", { name, query }),
    logQuery: (query) => POST("/queries/log", { query }),
    queryHistory: () => GET("/queries/history"),
    deleteQuery: (name) => DELETE(`/queries/${encodeURIComponent(name)}`),

    // Database connections
    connectDatabase: (request) => POST("/connect/database", request),
    importFromDatabase: (request, table) => POST("/connect/database/import", request, { params: { table_name: table } }),
    importFromAPI: (request) => POST("/connect/api", request),

    // File watcher
    startWatcher: (dirs) => POST("/watcher/start", { directories: dirs }),
    stopWatcher: () => POST("/watcher/stop"),
    watcherFiles: () => GET("/watcher/files"),
    scanWatcher: () => POST("/watcher/scan"),

    // Authentication
    register: (username, password, email) =>
      POST("/auth/register", { username, password, ...(email ? { email } : {}) }),
    login: (username, password) => POST("/auth/login", { username, password }),
    generateApiKey: (username, password) => POST("/auth/api-key", { username, password }),
    createApiKey: (name, scopes) => POST("/auth/api-keys", { name, scopes }),
    listApiKeys: () => GET("/auth/api-keys", { silent: true }),
    deleteApiKey: (id) => DELETE(`/auth/api-keys/${id}`),
    users: () => GET("/auth/users"),
    // ── Account lifecycle (ADR-009)
    // The reset / verification / invitation token travels in the body of the
    // one endpoint that redeems it — never in a query string.
    forgotPassword: (email) => POST("/auth/forgot-password", { email }),
    resetPassword: (token, password) => POST("/auth/reset-password", { token, password }),
    verifyEmail: (token) => POST("/auth/verify-email", { token }),
    resendVerification: (email) => POST("/auth/resend-verification", { email }),
    inviteUser: (data) => POST("/auth/invite", data),
    acceptInvite: (token, password) => POST("/auth/accept-invite", { token, password }),
    deleteAccount: (password) => POST("/auth/delete-account", { password, confirm: true }),
    me: () => GET("/auth/me", { silent: true }),
    listUsers: () => GET("/auth/users", { silent: true }),
    updateUser: (username, data) => PATCH(`/auth/users/${encodeURIComponent(username)}`, data),
    deleteUser: (username) => DELETE(`/auth/users/${encodeURIComponent(username)}`),
    resetUserPassword: (username, password) =>
        POST(`/auth/users/${encodeURIComponent(username)}/password`, { password }),
    exportMyData: () => GET("/auth/me/export", { silent: true }),
    // Google login
    googleLoginStatus: () => GET("/auth/google/login/status"),
    googleLoginUrl: () => GET("/auth/google/login"),
    googleLoginResult: (state) => GET(`/auth/google/login/result?state=${encodeURIComponent(state)}`),

    // Central accounts (billing server) — email/password when configured
    accountsStatus: () => GET("/auth/accounts/status"),
    accountsLogin: (email, password) => POST("/auth/accounts/login", { email, password }),
    accountsRegister: (email, password) => POST("/auth/accounts/register", { email, password }),

    // Subscriptions / plans (admin/self-serve offline grants; no in-app checkout)
    subscriptionPlans: () => GET("/subscriptions/plans"),
    mySubscription: () => GET("/subscriptions/me"),
    syncSubscription: () => POST("/subscriptions/sync", {}),
    setMyPlan: (plan, durationDays) => POST("/subscriptions/me", { plan, duration_days: durationDays }),
    adminSetPlan: (userId, plan, durationDays) =>
      POST(`/subscriptions/${encodeURIComponent(userId)}`, { plan, duration_days: durationDays }),

    // Product key activation (Ed25519 offline verification) — API kept for
    // legacy clients; the subscription page no longer surfaces key entry.
    activateProductKey: (key) => POST("/premium/activate-key", { key }),

    // One-time Pro trial (server-enforced single use, default 14 days)
    startTrial: () => POST("/subscriptions/trial/start", {}),
    trialStatus: () => GET("/subscriptions/trial/status"),

    // Per-account user data (server-side scoped by user_id)
    getPreferences: () => GET("/user/preferences"),
    setPreferences: (prefs) => PUT("/user/preferences", prefs),
    listConversations: () => GET("/user/conversations"),
    createConversation: (title) => POST("/user/conversations", { title }),
    getConversation: (convId) => GET(`/user/conversations/${encodeURIComponent(convId)}`),
    renameConversation: (convId, title) => PUT(`/user/conversations/${encodeURIComponent(convId)}`, { title }),
    deleteConversation: (convId) => DELETE(`/user/conversations/${encodeURIComponent(convId)}`),
    addMessage: (convId, role, content) =>
      POST(`/user/conversations/${encodeURIComponent(convId)}/messages`, { role, content }),
    listQueryHistory: (limit) => GET("/user/query-history", { params: { limit } }),
    addQueryHistory: (query, sql, summary) =>
      POST("/user/query-history", { query, sql, result_summary: summary }),
    clearQueryHistory: () => DELETE("/user/query-history"),

    // Alerts
    addAlert: (request) => POST("/alerts/add", request),
    listAlerts: () => GET("/alerts/list", { silent: true }),
    runAlerts: () => POST("/alerts/run"),
    deleteAlert: (name) => DELETE(`/alerts/${encodeURIComponent(name)}`),
    alertHistory: () => GET("/alerts/history"),

    // Schedules
    addSchedule: (request) => POST("/schedule/add", request),
    listSchedules: () => GET("/schedule/list", { silent: true }),
    deleteSchedule: (name) => DELETE(`/schedule/${encodeURIComponent(name)}`),
    startScheduler: () => POST("/schedule/start"),

    // Cache
    clearCache: (prefix) => POST("/cache/clear", { prefix }),
    cacheStats: () => GET("/cache/stats"),

    // Pipeline
    pipelineRun: (pipeline) => POST("/pipeline/run", pipeline),
    pipelineDiagnose: (payload) => POST("/pipeline/diagnose", payload),
    pipelineValidate: (pipeline) => POST("/pipeline/validate", pipeline),
    pipelineSave: (data) => POST("/pipeline/save", data),
    pipelineList: () => GET("/pipeline/list"),
    pipelineLoad: (name) => GET(`/pipeline/load/${encodeURIComponent(name)}`),
    pipelineDelete: (name) => DELETE(`/pipeline/${encodeURIComponent(name)}`),
    // Pipeline scheduling
    pipelineSchedule: (data) => POST("/pipeline/schedule", data),
    pipelineSchedules: () => GET("/pipeline/schedules"),
    pipelineScheduleDelete: (name) => DELETE(`/pipeline/schedule/${encodeURIComponent(name)}`),
    pipelineScheduleToggle: (name) => POST(`/pipeline/schedule/${encodeURIComponent(name)}/toggle`, {}),

    // LLM Code Generation
    generatePythonCode: (prompt) => POST("/llm/generate-code", { prompt: prompt, language: "python" }),
    generateSqlCode: (prompt, tables) => POST("/llm/generate-code", { prompt: prompt, language: "sql", context: tables }),

    // Lineage
    lineage: (table) => GET(`/lineage/${encodeURIComponent(table)}`),
    lineageDag: (table) => GET(`/lineage/${encodeURIComponent(table)}/dag`),
    lineageDagFull: () => GET("/lineage/dag/full"),
    columnLineage: (table) => GET(`/lineage/${encodeURIComponent(table)}/columns`),
    recordLineage: (request) => POST("/lineage/record", request),

    // Export
    download: (path, filename) => download(path, filename),
    exportBI: (table, format = "powerbi") =>
      GET(`/export/bi/${encodeURIComponent(table)}`, { params: { format } }),
    exportCSV: (table) => GET(`/export/csv/${encodeURIComponent(table)}`),
    exportParquet: (table) => GET(`/export/parquet/${encodeURIComponent(table)}`),
    exportXLSX: (table) => GET(`/export/xlsx/${encodeURIComponent(table)}`),
    exportZIP: () => GET("/export/zip"),
    exportMarkdown: (table) => GET(`/export/markdown/${encodeURIComponent(table)}`),
    exportHTMLReport: (table) => GET(`/export/html/${encodeURIComponent(table)}`),
    exportPPTX: (table) => GET(`/export/pptx/${encodeURIComponent(table)}`),

    // Dashboard exports (rich, designed output matching the in-app dashboard)
    listDashboards: () => GET("/dashboards"),
    exportDashboardPbit: (id) => GET(`/dashboards/${encodeURIComponent(id)}/export/pbit`),
    exportDashboardJob: (id, fmt) => POST(`/dashboards/${encodeURIComponent(id)}/export/${fmt}`, {}),
    exportJobStatus: (jobId) => GET(`/dashboards/export/${encodeURIComponent(jobId)}`),
    exportJobDownload: (jobId) => GET(`/dashboards/export/download/${encodeURIComponent(jobId)}`),

    // ── Code Execution (for Notebook) ──
    execute: (code, language, context) => POST("/execute", { code, language, context }),

    // ── Experiments ──
    listExperiments: (status) => GET("/experiments", { params: { status } }),
    createExperiment: (data) => POST("/experiments", data),
    startExperiment: (name) => POST(`/experiments/${encodeURIComponent(name)}/start`),
    completeExperiment: (name, results = {}) => POST(`/experiments/${encodeURIComponent(name)}/complete`, results),
    deleteExperiment: (name) => DELETE(`/experiments/${encodeURIComponent(name)}`),
    experimentHistory: (limit = 50) => GET("/experiments/history", { params: { limit } }),

    // ── Quality Trends ──
    qualityTrend: (table, days = 30) => GET(`/quality/${encodeURIComponent(table)}/trend`, { params: { days } }),
    recordQualitySnapshot: (table) => POST(`/quality/${encodeURIComponent(table)}/snapshot`),
    checkAlerts: (tableName, thresholds = {}) => POST("/alert/check", { table_name: tableName, thresholds }),
    recentAlerts: () => GET("/alerts/recent"),

    // ── External Connections ──
    connectExternal: (request) => POST("/connect/database", request),
    listExternalTables: (id) => GET(`/connect/database/${encodeURIComponent(id)}/tables`),
    importExternalTable: (conn, table, target) => POST(`/connect/database/import?table_name=${encodeURIComponent(table)}&limit=5000`, conn),
    listConnections: () => GET("/connect/database/connections"),
    deleteConnection: (id) => DELETE(`/connect/database/${encodeURIComponent(id)}`),

    // ── Background Tasks ──
    listTasks: () => GET("/tasks"),
    getTask: (taskId) => GET(`/tasks/${encodeURIComponent(taskId)}`),
    cancelTask: (taskId) => POST(`/tasks/${encodeURIComponent(taskId)}/cancel`),
    runBackgroundQuery: (query, limit = 1000) => POST("/tasks/run", { query, limit }),

    // ── LLM Provider Management ──
    llmProviders: () => GET("/llm/providers", { silent: true }),
    updateLLMProvider: (provider, apiKey, model, setDefault, baseUrl, routingRules = undefined, proxies = undefined) =>
      POST("/llm/providers/update", {
        provider,
        api_key: apiKey, model, set_default: setDefault, base_url: baseUrl,
        routing_rules: routingRules, proxies,
      }),
    clearLLMProvider: (provider) => POST("/llm/providers/clear", { provider }),
    testLLMProvider: (provider, model = "") =>
      POST("/llm/providers/test", { provider, model: model || undefined }),
    llmModels: () => GET("/llm/models", { silent: true }),
    opencodeProxiesStatus: () => GET("/llm/providers/opencode/proxies/status"),
    opencodeProxiesFetch: (opts = {}) => POST("/llm/providers/opencode/proxies/fetch", opts),
    geminiAuthUrl: () => GET("/llm/providers/gemini/auth-url"),
    geminiAuthStatus: () => GET("/llm/providers/gemini/status"),

    // ── Local AI Dependencies (Ollama / LM Studio) ──
    llmDependencies: () => GET("/llm/dependencies", { silent: true }),
    ollamaInstall: () => POST("/llm/ollama/install"),
    ollamaInstallStatus: (id) => GET(`/llm/ollama/install/${encodeURIComponent(id)}`),
    ollamaStart: () => POST("/llm/ollama/start"),
    ollamaPull: (model) => POST("/llm/ollama/pull", { model }),
    ollamaPullStatus: (id) => GET(`/llm/ollama/pull/${encodeURIComponent(id)}`),
    ollamaDelete: (model) => POST("/llm/ollama/delete", { model }),
    lmStudioInstall: () => POST("/llm/lmstudio/install"),
    lmStudioInstallStatus: (id) => GET(`/llm/lmstudio/install/${encodeURIComponent(id)}`),
    lmStudioLaunch: () => POST("/llm/lmstudio/launch"),

    // ── Cloudflare WARP (egress IP rotation for rate-limit recovery) ──
    warpStatus: () => GET("/llm/warp/status"),
    warpConnect: () => POST("/llm/warp/connect"),
    warpDisconnect: () => POST("/llm/warp/disconnect"),
    warpReset: () => POST("/llm/warp/reset"),
    warpInstall: () => POST("/llm/warp/install"),
    warpInstallStatus: (id) => GET(`/llm/warp/install/${encodeURIComponent(id)}`),

    // ── AI text rewrite (Dashboard Studio wizard chips) ──
    rewrite: (text, kind) => POST("/llm/rewrite", { text, kind }, { silent: true }),

    // ── Streaming Chat (SSE) ──
    askStream: async function* (question, history = [], sessionId = "", tables = [], signal = undefined, streamEvents = false) {
      // Mirror _request(): AuthMiddleware 401s the SSE endpoint without the
      // JWT, and a 401 here makes the chat silently fall back to non-streaming
      // /ask (no streaming and, before this, no chart render either).
      const headers = { "Content-Type": "application/json" };
      const streamToken = (typeof window !== "undefined" && window.RBAC && window.RBAC.getToken)
        ? window.RBAC.getToken()
        : null;
      if (streamToken) {
        headers["Authorization"] = "Bearer " + streamToken;
      }
      const streamCsrf = _csrfToken();
      if (streamCsrf) {
        headers["X-CSRF-Token"] = streamCsrf;
      }
      const response = await fetch(BASE_URL + "/llm/chat/stream", {
        method: "POST",
        headers,
        signal,
        credentials: "same-origin",
        body: JSON.stringify({
          question,
          tables: (tables && tables.length) ? tables : undefined,
          context: { history: history.slice(-20), session_id: sessionId || undefined },
          // Opt-in live pipeline progress: step/viz events stream before the
          // answer text instead of one final visualizations batch.
          stream_steps: streamEvents || undefined,
        }),
      });
      // Non-OK responses (503 / 500 JSON, not SSE) must throw so the caller can
      // fall back to /ask instead of receiving a silent empty stream.
      if (!response.ok) {
        let detail = "HTTP " + response.status;
        try { const j = await response.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
        throw new Error(detail);
      }
      if (!response.body) throw new Error("No response body from stream");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let gotToken = false;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const payload = line.slice(6).trim();
            if (payload === "[DONE]") return;
            try {
              const data = JSON.parse(payload);
              if (data.error) throw new Error(data.error);
              if (data.token) { gotToken = true; yield data.token; }
              if (data.step) { gotToken = true; yield { __step: data.step }; }
              if (data.viz) { gotToken = true; yield { __viz: data.viz }; }
              if (data.visualizations) { gotToken = true; yield { __visualizations: data.visualizations }; }
            } catch (e) {
              if (e.message && !e.message.includes("JSON")) throw e;
            }
          }
        }
      }
      // Stream closed with no tokens at all → signal a fallback.
      if (!gotToken) throw new Error("Empty stream response");
    },

    // ── Utility (internal) ──
    _request,
    _BASE_URL: BASE_URL,

    // ── Org Structure ──
    getOrgTree: () => GET("/org/tree"),
    listDepartments: () => GET("/org/departments"),
    createDepartment: (name, description, parentId, headUserId) =>
      POST("/org/departments", { name, description, parent_id: parentId, head_user_id: headUserId }),
    getDepartment: (id) => GET(`/org/departments/${id}`),
    updateDepartment: (id, data) => PUT(`/org/departments/${id}`, data),
    deleteDepartment: (id) => DELETE(`/org/departments/${id}`),
    listTeams: (deptId) => GET("/org/teams", { params: { department_id: deptId } }),
    createTeam: (name, deptId, description, leadUserId) =>
      POST("/org/teams", { name, department_id: deptId, description, lead_user_id: leadUserId }),
    deleteTeam: (id) => DELETE(`/org/teams/${id}`),
    listGroups: () => GET("/org/groups"),
    createGroup: (name, description, groupType) =>
      POST("/org/groups", { name, description, group_type: groupType }),
    deleteGroup: (id) => DELETE(`/org/groups/${id}`),
    addGroupMember: (groupId, userId, role) =>
      POST(`/org/groups/${groupId}/members`, { user_id: userId, role: role || "member" }),
    removeGroupMember: (groupId, userId) => DELETE(`/org/groups/${groupId}/members/${userId}`),
    getGroupMembers: (groupId) => GET(`/org/groups/${groupId}/members`),
    getUserGroups: (userId) => GET(`/org/users/${userId}/groups`),

    // ── Data Catalog ──
    catalogSearch: (q) => GET("/catalog/search", { params: { q } }),
    catalogAssets: (domain, cert) => GET("/catalog/assets", { params: { domain, certification: cert } }),
    catalogAsset: (id) => GET(`/catalog/assets/${id}`),
    updateAssetMetadata: (id, data) => PUT(`/catalog/assets/${id}/metadata`, data),
    certifyAsset: (id, status) => POST(`/catalog/certify/${id}`, { status }),
    syncCatalog: () => POST("/catalog/sync", {}),
    listTerms: (domain) => GET("/catalog/terms", { params: { domain } }),
    createTerm: (data) => POST("/catalog/terms", data),
    updateTerm: (id, data) => PUT(`/catalog/terms/${id}`, data),
    deleteTerm: (id) => DELETE(`/catalog/terms/${id}`),
    listMetrics: (domain) => GET("/catalog/metrics", { params: { domain } }),
    createMetric: (data) => POST("/catalog/metrics", data),
    updateMetric: (id, data) => PUT(`/catalog/metrics/${id}`, data),
    catalogDomains: () => GET("/catalog/domains"),

    // ── Notifications ──
    getNotifications: (limit, offset, unreadOnly, type) =>
      GET("/notifications", { params: { limit, offset, unread_only: unreadOnly, notif_type: type } }),
    getUnreadCount: () => GET("/notifications/unread/count", { silent: true }),
    markNotificationRead: (id) => POST(`/notifications/${id}/read`, {}),
    markAllNotificationsRead: () => POST("/notifications/read-all", {}),
    getNotificationPreferences: () => GET("/notifications/preferences", { silent: true }),
    updateNotificationPreferences: (prefs) => PUT("/notifications/preferences", prefs),

    // ── Visual Query Builder ──
    buildVisualQuery: (spec) => POST("/query/visual/build", spec),
    validateVisualQuery: (spec) => POST("/query/visual/validate", spec),
    executeVisualQuery: (spec) => POST("/query/visual/execute", spec),
    autoDetectJoins: (table1, table2) => POST("/query/visual/auto-join", { table1, table2 }),
    suggestMetrics: (table) => POST("/query/visual/suggest", { table }),

    // ── Advanced Dashboards ──
    dashboards: () => GET("/dashboards"),
    createDashboard: (data) => POST("/dashboards", data),
    getDashboard: (id) => GET(`/dashboards/${id}`),
    updateDashboard: (id, data) => PUT(`/dashboards/${id}`, data),
    deleteDashboard: (id) => DELETE(`/dashboards/${id}`),
    duplicateDashboard: (id) => POST(`/dashboards/${id}/duplicate`, {}),
    updateDashboardLayout: (id, data) => PUT(`/dashboards/${id}/layout`, data),
    shareDashboard: (id, data) => POST(`/dashboards/${id}/share`, data),
    getSharedDashboard: (token) => GET(`/dashboards/shared/${token}`),
    dashboardTemplates: () => GET("/dashboards/templates"),
    dashboardVersions: (id) => GET(`/dashboards/${id}/versions`),
    restoreDashboardVersion: (dashId, versionId) => POST(`/dashboards/${dashId}/restore/${versionId}`, {}),

    // ── Power BI Studio (#bidashboard) ──
    biQuery: (query) => POST("/bi/query", { query }),
    exportPBIT: (id) => GET(`/dashboards/${id}/export/pbit`),

    // ── Measures registry + calendar (persistent calculation layer) ──
    measures: () => GET("/measures"),
    measuresForTable: (table) => GET("/measures", { params: { table } }),
    measuresHealth: () => GET("/measures/health"),
    measureSql: (name, mode) => GET("/measures/sql", { params: { name, mode } }),
    createMeasure: (data) => POST("/measures", data),
    updateMeasure: (id, data) => PUT(`/measures/${id}`, data),
    deleteMeasure: (id) => DELETE(`/measures/${id}`),
    previewMeasure: (id, mode) => POST(`/measures/${id}/preview`, { mode }),

    // ── Collaboration (Comments, Annotations, Share) ──
    listComments: (type, id) => GET(`/comments/${type}/${id}`),
    addComment: (type, id, data) => POST(`/comments/${type}/${id}`, data),
    updateComment: (commentId, data) => PUT(`/comments/${commentId}`, data),
    deleteComment: (commentId, authorId) => DELETE(`/comments/${commentId}`, { params: { author_id: authorId } }),
    resolveComment: (commentId) => POST(`/comments/${commentId}/resolve`, {}),
    listAnnotations: (type, id) => GET(`/annotations/${type}/${id}`),
    addAnnotation: (type, id, data) => POST(`/annotations/${type}/${id}`, data),
    getActivity: (type, id) => GET(`/activity/${type}/${id}`),
    createShareLink: (data) => POST("/share", data),
    accessSharedResource: (token) => GET(`/share/${token}`),
    revokeShareLink: (linkId, sharedBy) => DELETE(`/share/${linkId}`, { params: { shared_by: sharedBy } }),

    // ── Approval Workflows ──
    workflowDefinitions: (type) => GET("/workflows/definitions", { params: { workflow_type: type } }),
    getWorkflowDefinition: (id) => GET(`/workflows/definitions/${id}`),
    startWorkflow: (data) => POST("/workflows/start", data),
    workflowInstances: (initiatorId, status, limit, offset) =>
      GET("/workflows/instances", { params: { initiator_id: initiatorId, status, limit, offset } }),
    getWorkflowInstance: (id) => GET(`/workflows/instances/${id}`),
    approveWorkflowStep: (id, data) => POST(`/workflows/instances/${id}/approve`, data),
    rejectWorkflowStep: (id, data) => POST(`/workflows/instances/${id}/reject`, data),
    cancelWorkflow: (id, initiatorId) => POST(`/workflows/instances/${id}/cancel`, { initiator_id: initiatorId }),
    pendingApprovals: (userId, role) => GET("/workflows/pending-approvals", { params: { user_id: userId, role } }),

    // ── Data Masking ──
    maskingRules: (table) => GET("/masking/rules", { params: { table_name: table } }),
    createMaskingRule: (data) => POST("/masking/rules", data),
    updateMaskingRule: (id, data) => PUT(`/masking/rules/${id}`, data),
    deleteMaskingRule: (id) => DELETE(`/masking/rules/${id}`),
    scanPiiColumns: (table) => GET(`/masking/scan/${table}`),

    // ── Data Marketplace ──
    marketplaceProducts: (category) => GET("/marketplace/products", { params: { category } }),
    getMarketplaceProduct: (id) => GET(`/marketplace/products/${id}`),
    publishMarketplaceProduct: (data) => POST("/marketplace/products", data),
    requestProductAccess: (id, data) => POST(`/marketplace/products/${id}/request`, data),
    getMyRequests: (requesterId) => GET("/marketplace/my-requests", { params: { requester_id: requesterId } }),
    rateProduct: (id, data) => POST(`/marketplace/products/${id}/rate`, data),
    marketplaceCategories: () => GET("/marketplace/categories"),

    // ── SLA Monitoring ──
    slaDefinitions: () => GET("/sla/definitions"),
    createSlaDefinition: (data) => POST("/sla/definitions", data),
    updateSlaDefinition: (id, data) => PUT(`/sla/definitions/${id}`, data),
    deleteSlaDefinition: (id) => DELETE(`/sla/definitions/${id}`),
    slaStatus: () => GET("/sla/status"),
    slaBreaches: () => GET("/sla/breaches"),
    checkSla: (id) => POST(`/sla/check/${id}`, {}),
    startSlaMonitor: (interval) => POST("/sla/monitor/start", { interval_minutes: interval || 60 }),

    // ── System Health ──
    systemHealth: () => GET("/health/system", { silent: true }),

    // ── Pipeline Health ──
    pipelineHealth: () => GET("/pipeline/health"),
    pipelineRuns: (name) => GET("/pipeline/runs", { params: { pipeline_name: name } }),
    pipelineHeartbeat: (data) => POST("/pipeline/run/heartbeat", data),

    // ── Cost & Usage ──
    recordQueryCost: (data) => POST("/cost/record", data),
    queryCosts: (userId, limit, offset) =>
      GET("/cost/queries", { params: { user_id: userId, limit, offset } }),
    costSummary: () => GET("/cost/summary"),
    createBudget: (data) => POST("/cost/budgets", data),
    listBudgets: () => GET("/cost/budgets"),
    budgetStatus: () => GET("/cost/budgets/status"),
    updateBudget: (id, data) => PUT(`/cost/budgets/${id}`, data),
    deleteBudget: (id) => DELETE(`/cost/budgets/${id}`),

    // ── Compliance ──
    submitPrivacyRequest: (data) => POST("/compliance/privacy-request", data),
    listPrivacyRequests: (status) => GET("/compliance/privacy-requests", { params: { status } }),
    getPrivacyRequest: (id) => GET(`/compliance/privacy-requests/${id}`),
    executePrivacyRequest: (id) => POST(`/compliance/privacy-requests/${id}/execute`, {}),
    createRetentionPolicy: (data) => POST("/compliance/retention-policies", data),
    listRetentionPolicies: () => GET("/compliance/retention-policies"),
    updateRetentionPolicy: (id, data) => PUT(`/compliance/retention-policies/${id}`, data),
    deleteRetentionPolicy: (id) => DELETE(`/compliance/retention-policies/${id}`),
    executeRetention: () => POST("/compliance/retention/run", {}),
    recordConsent: (data) => POST("/compliance/consent", data),
    listConsent: (subjectId) => GET("/compliance/consent", { params: { subject_id: subjectId } }),
    revokeConsent: (data) => POST("/compliance/consent/revoke", data),
    piiInventory: () => GET("/compliance/pii-inventory"),
    scanPiiInventory: () => POST("/compliance/pii-scan", {}),
    complianceReport: () => GET("/compliance/report"),

    // ── Generic HTTP methods for arbitrary endpoints ──
    // `opts` is forwarded to the request helper, so `{ signal }` cancels
    // the call (see _request above).
    get: (path, opts) => GET(path, opts),
    post: (path, body, opts) => POST(path, body, opts),
    put: (path, body, opts) => PUT(path, body, opts),
    patch: (path, body, opts) => PATCH(path, body, opts),
    delete: (path, opts) => DELETE(path, opts),
  };
})();
