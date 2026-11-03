/* ═══════════════════════════════════════════════════════════════════
   Settings Page — Modern Card Grid
   Cleaned up + fixed:
   - Enterprise tab now loads its sections (notif prefs, API keys,
     SLA, budgets) — previously stuck on "Loading…"
   - Scheduled Reports list is now populated & deletable
   - Real API connectivity check (dropped the fake base-URL field)
   - Masked JWT token with reveal toggle
   - Delete / enable toggles wired for alerts, schedules, API keys,
     SLA, budgets, masking & retention rules
   - Smart monitoring "Check All" runs concurrently with progress
   - Removed redundant ETL JSON runner (see #automation) & stale About card
   ═══════════════════════════════════════════════════════════════════ */

const SettingsPage = {
    _current: 'general',
    // Cached CLI-version / server-state payload for the opencode provider
    // card. Null until the first fetch resolves (see _loadOpencodeInfo).
    _opencodeInfo: null,
    _opencodeInfoTs: 0,

    async init() {},
    async destroy() {
        this._clearPollers();
    },

    async render() {
        await App.refreshTables();
        // Navigated away while tables refreshed — leave DOM to the next route.
        if (App.currentPage !== SettingsPage) return;
        this._current = 'general';
        const pc = document.getElementById('page-content');
        if (!pc) return;
        const tables = App.state.tables || {};
        const tableNames = Array.isArray(tables) ? tables : Object.keys(tables || {});
        pc.innerHTML = `
        <div class="max-w-6xl mx-auto space-y-5 animate-fade-in">
            <div class="card settings-hero">
                <div class="flex items-start justify-between gap-4 flex-wrap">
                    <div class="flex items-center gap-3">
                        <div class="settings-icon settings-icon-primary"><i class="fas fa-gear"></i></div>
                        <div>
                            <h2 class="text-xl font-bold">Settings</h2>
                            <p class="text-sm text-secondary">System configuration, LLM providers, users, alerts & enterprise governance</p>
                        </div>
                    </div>
                    <button onclick="SettingsPage._refreshAll()" class="btn btn-secondary btn-sm"><i class="fas fa-sync mr-1"></i>Refresh</button>
                </div>
                <div id="settings-health-strip" class="grid grid-cols-4 gap-2 mt-4"></div>
            </div>

            ${this._tabBar()}

            <div id="settings-content">${this._renderGeneral(tableNames)}</div>
        </div>`;

        this._loadHealthStrip();
        this._loadGeneralData();
        this._loadDeps();
        setTimeout(() => this.refreshProviders(), 60);
        if (window.RBAC && RBAC.applyToNav) RBAC.applyToNav();
        this._fixA11y();
    },

    _tabBar() {
        const plan = (window.RBAC && RBAC.getPlan) ? RBAC.getPlan() : 'free';
        const isFree = plan === 'free';
        const tabs = [
            { k: 'general', icon: 'fa-sliders', label: 'General' },
            { k: 'enterprise', icon: 'fa-building-shield', label: 'Enterprise', feature: 'enterprise' },
            { k: 'governance', icon: 'fa-scale-balanced', label: 'Governance', feature: 'governance' },
        ];
        return `<div class="settings-tabs">` + tabs.map(t => {
            const lock = (isFree && t.feature) ? `<i class="fas fa-lock" style="font-size:10px;margin-left:4px;opacity:.55"></i>` : '';
            return `
            <button class="settings-tab${this._current === t.k ? ' active' : ''}" data-settings-tab="${t.k}" onclick="SettingsPage._switchSection('${t.k}')">
                <i class="fas ${t.icon}"></i><span>${t.label}${lock}</span>
            </button>`;
        }).join('') + `</div>`;
    },

    _switchSection(section) {
        // In-page tab switch: drop any orphaned loading counts from the
        // previous tab's parallel fetches so #loading-overlay can't stick.
        if (window.API && typeof window.API.resetLoading === "function") {
            try { window.API.resetLoading(); } catch (_) {}
        }
        const plan = (window.RBAC && RBAC.getPlan) ? RBAC.getPlan() : 'free';
        const gatedTabs = { enterprise: 'enterprise', governance: 'governance' };
        if (plan === 'free' && gatedTabs[section]) {
            if (window.RBAC && RBAC.showUpgradeOverlay) RBAC.showUpgradeOverlay(gatedTabs[section]);
            return;
        }
        this._current = section;
        const container = document.getElementById('settings-content');
        if (!container) return;
        const tables = App.state.tables || {};
        const tableNames = Array.isArray(tables) ? tables : Object.keys(tables || {});
        document.querySelectorAll('.settings-tab').forEach(t => {
            t.classList.toggle('active', t.dataset && t.dataset.settingsTab === section);
        });
        if (section === 'enterprise') {
            container.innerHTML = this._renderEnterprise(tableNames);
            this._loadEnterpriseData();
        } else if (section === 'governance') {
            container.innerHTML = this._renderGovernance(tableNames);
            this._loadGovernanceData();
        } else {
            container.innerHTML = this._renderGeneral(tableNames);
            this._loadGeneralData();
            this._loadDeps();
            this.refreshProviders();
        }
        if (window.RBAC && RBAC.applyToNav) RBAC.applyToNav();
        this._fixA11y(container);
    },

    // ── Small building blocks ────────────────────────────────────────
    _section(id, icon, accent, title, subtitle, body, action, feature) {
        return `
        <div class="card" data-section="${id}"${feature ? ` data-feature="${feature}"` : ''}>
            <div class="settings-card-head">
                <div class="flex items-center gap-2.5 min-w-0">
                    <div class="settings-icon settings-icon-${accent}"><i class="fas ${icon}"></i></div>
                    <div class="min-w-0">
                        <h3 class="settings-card-title">${title}</h3>
                        ${subtitle ? `<p class="settings-card-sub">${subtitle}</p>` : ''}
                    </div>
                </div>
                ${action ? `<div class="flex items-center gap-2 shrink-0">${action}</div>` : ''}
            </div>
            ${body}
        </div>`;
    },

    _skeletonRows(n) {
        let s = '';
        for (let i = 0; i < n; i++) s += '<div class="settings-skeleton" style="height:52px"></div>';
        return s;
    },

    _empty(msg) {
        return `<div class="settings-empty"><i class="fas fa-inbox"></i><span>${msg}</span></div>`;
    },

    _error(msg) {
        return `<div class="settings-error"><i class="fas fa-circle-exclamation"></i><span>${msg}</span></div>`;
    },

    _iconAction(onclick, icon, title, danger) {
        return `<button onclick="${onclick}" class="btn btn-sm ${danger ? 'btn-ghost' : 'btn-ghost'}" title="${title}" style="color:${danger ? 'var(--color-accent-red)' : 'var(--color-content-text-secondary)'}"><i class="fas ${icon}"></i></button>`;
    },

    /**
     * The e-mail confirmation strip.
     *
     * An account with an address but no confirmation cannot receive
     * notifications, so the address is offered for confirmation right
     * where the user signed in. The request itself always answers the
     * same way — it never says whether the address exists.
     */
    _renderVerification(email, user) {
        if (!email) return '';
        const verified = !!(user && user.email_verified);
        if (verified) {
            return `<div class="flex items-center gap-2 mt-3 text-xs" style="color:var(--color-accent-green)">
                <i class="fas fa-circle-check"></i><span>${this._esc(email)} is confirmed.</span>
            </div>`;
        }
        return `<div class="flex items-center justify-between flex-wrap gap-2 mt-3 pt-3" style="border-top:1px solid var(--color-card-border)">
            <div class="text-xs text-secondary">
                <i class="fas fa-envelope mr-1" style="color:var(--color-accent-amber)"></i>
                <strong>${this._esc(email)}</strong> is not confirmed yet — notifications cannot reach it.
            </div>
            <button onclick="SettingsPage._resendVerification()" class="btn btn-secondary btn-sm">
                <i class="fas fa-paper-plane mr-1"></i>Resend the confirmation mail
            </button>
            <div id="resend-verification-status" class="text-xs w-full" style="display:none"></div>
        </div>`;
    },

    async _resendVerification() {
        const user = (window.RBAC && window.RBAC.getUser()) || {};
        const email = user.email || '';
        const box = document.getElementById('resend-verification-status');
        const show = (message, ok) => {
            if (!box) return;
            box.textContent = message;
            box.style.color = ok ? 'var(--color-accent-green)' : 'var(--color-accent-red)';
            box.style.display = 'block';
        };
        if (!email) {
            show('This account has no e-mail address on file.', false);
            return;
        }
        if (!window.AccountForms || !window.AccountForms.resendVerification) {
            show('Resending is not available on this server.', false);
            return;
        }
        try {
            const message = await window.AccountForms.resendVerification(email);
            show(message, true);
        } catch (e) {
            show((e && e.message) || 'The request could not be sent.', false);
        }
    },

    // ═════════════════════════════════════════════════════════════════
    // GENERAL TAB
    // ═════════════════════════════════════════════════════════════════
    _renderGeneral(tableNames) {
        const tableOptions = tableNames.map(t => `<option value="${this._esc(t)}">${this._esc(t)}</option>`).join('');
        const user = (window.RBAC && window.RBAC.getUser()) || {};
        const name = user.full_name || user.name || user.username || 'Local Admin';
        const email = user.email || '';
        const role = user.role || 'admin';
        const plan = (window.RBAC && window.RBAC.getPlan) ? window.RBAC.getPlan() : 'free';
        const tenant = user.tenant_id || '';
        const workspace = (window.TenantOnboarding && window.TenantOnboarding.getWorkspaceName()) || '';
        return `
        <div class="grid grid-cols-1 gap-4">
            ${this._section('account', 'fa-user', 'indigo', 'Account',
                'Your sign-in identity, role, and subscription plan.', `
                <div class="flex items-center justify-between flex-wrap gap-4">
                    <div class="flex items-center gap-3">
                        <div class="settings-icon settings-icon-indigo" style="width:44px;height:44px;border-radius:12px;font-size:18px;">${this._esc((name || 'U').charAt(0).toUpperCase())}</div>
                        <div>
                            <div class="font-semibold text-sm">${this._esc(name)}</div>
                            <div class="text-xs text-secondary">${this._esc(email || role)}</div>
                            <div class="text-xs text-secondary mt-0.5">Role: <span class="tag tag-gray" style="font-size:10px;text-transform:uppercase">${this._esc(role)}</span></div>
                        </div>
                    </div>
                    <div class="flex items-center gap-2">
                        ${(window.RBAC && window.RBAC.planBadge) ? window.RBAC.planBadge() : ''}
                        <button onclick="App.navigate('#subscription')" class="btn btn-secondary btn-sm"><i class="fas fa-crown mr-1"></i>Manage Plan</button>
                        <button onclick="window.Auth && Auth.signOut()" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt mr-1"></i>Sign Out</button>
                    </div>
                </div>
                ${this._renderVerification(email, user)}`)}

            ${this._section('workspace', 'fa-building', 'blue', 'Workspace',
                'The private workspace your account belongs to — only its members can see its data.', `
                <div class="flex items-center justify-between flex-wrap gap-4">
                    <div class="min-w-0">
                        <div class="font-semibold text-sm">${this._esc(workspace || tenant || 'Your workspace')}</div>
                        <div class="text-xs text-secondary mt-0.5">Workspace ID: <code>${this._esc(tenant || '—')}</code> &middot; Plan: <span class="tag tag-gray" style="font-size:10px;text-transform:uppercase">${this._esc(plan)}</span></div>
                    </div>
                    <div class="flex items-center gap-2">
                        <input id="workspace-rename" class="form-input" style="max-width:220px" maxlength="80" placeholder="Workspace name" value="${this._esc(workspace)}">
                        <button onclick="SettingsPage._saveWorkspaceName()" class="btn btn-secondary btn-sm"><i class="fas fa-check mr-1"></i>Save</button>
                    </div>
                </div>`)}

            ${this._section('danger', 'fa-triangle-exclamation', 'red', 'Danger zone',
                'Deleting your account is permanent and removes everything it owns.', `
                <div class="flex items-end gap-2 flex-wrap">
                    <div class="flex-1" style="min-width:220px">
                        <div class="form-label">Your password</div>
                        <input id="delete-account-password" type="password" class="form-input" autocomplete="current-password" placeholder="Confirm with your password">
                    </div>
                    <button onclick="SettingsPage._deleteAccount()" class="btn btn-danger btn-sm"><i class="fas fa-trash mr-1"></i>Delete my account</button>
                </div>
                <p class="text-xs text-secondary mt-2">Your dashboards, conversations, uploads and workspace data go with it. You will be asked to confirm.</p>`)}
            ${this._section('deps', 'fa-download', 'teal', 'Local AI Dependencies & Models',
                'Install Ollama, pull & manage models, detect LM Studio — everything the local LLMs need.', `
                <div id="llm-deps-body">
                    <div class="settings-skeleton" style="height:140px"></div>
                </div>`,
                `<button onclick="SettingsPage._refreshDeps()" class="btn btn-sm btn-secondary" title="Refresh dependencies"><i class="fas fa-sync"></i></button>`)}

            ${this._section('llm', 'fa-brain', 'purple', 'LLM Providers',
                'Configure which AI model powers the analysis agent.', `
                <div id="llm-providers-list" class="space-y-3">
                    <div class="text-center py-6"><div class="settings-skeleton" style="height:64px"></div><div class="settings-skeleton" style="height:64px"></div></div>
                </div>`,
                `<button onclick="SettingsPage.refreshProviders()" class="btn btn-sm btn-secondary"><i class="fas fa-sync"></i></button>`)}

                ${this._section('alerts', 'fa-bell', 'red', 'Alerts & Thresholds <span class="tag tag-gray" style="font-size:10px"><i class="fas fa-lock mr-1"></i>Pro / Lifetime</span>',
                    'SQL-level rules that flag when a condition trips.', `
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <input id="alert-name" placeholder="Alert name" class="form-input mb-2">
                            <input id="alert-table" placeholder="Table name" class="form-input mb-2">
                            <textarea id="alert-sql" placeholder="Condition SQL (e.g., COUNT(*) > 100)" class="form-textarea mb-2" style="height:64px"></textarea>
                            <button onclick="SettingsPage.addAlert()" class="btn btn-danger btn-sm w-full"><i class="fas fa-plus mr-1"></i>Add Alert Rule</button>
                        </div>
                        <div>
                            <div class="flex gap-2 mb-2">
                                <button onclick="SettingsPage._listAlerts()" class="btn btn-secondary btn-sm"><i class="fas fa-sync mr-1"></i>Refresh</button>
                                <button onclick="SettingsPage.runAlerts()" class="btn btn-ghost btn-sm">Run All Checks</button>
                            </div>
                            <div id="alerts-list" class="settings-list-stack"></div>
                        </div>
                    </div>`, null, 'alerts_thresholds')}

                ${this._section('schedules', 'fa-calendar', 'green', 'Scheduled Reports <span class="tag tag-gray" style="font-size:10px"><i class="fas fa-lock mr-1"></i>Pro / Lifetime</span>',
                    'Automate report generation on a recurring cadence.', `
                    <div class="flex gap-2 items-end mb-3">
                        <div class="flex-1">
                            <div class="form-label">Table</div>
                            <select id="sched-table" class="form-select w-full"><option value="">Select...</option>${tableOptions}</select>
                        </div>
                        <div style="width:130px">
                            <div class="form-label">Frequency</div>
                            <select id="sched-freq" class="form-select w-full"><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="hourly">Hourly</option></select>
                        </div>
                        <button onclick="SettingsPage.addSchedule()" class="btn btn-primary btn-sm">Add</button>
                        <button onclick="SettingsPage.startScheduler()" class="btn btn-success btn-sm">Start</button>
                    </div>
                    <div id="schedules-list"></div>`, null, 'scheduled_reports')}

                ${this._section('monitor', 'fa-shield-halved', 'amber', 'Smart Data Monitoring <span class="tag tag-gray" style="font-size:10px"><i class="fas fa-lock mr-1"></i>Pro / Lifetime</span>',
                    'Detect row count shifts, schema changes, and null increases.', `
                    <div class="grid grid-cols-4 gap-3 mb-3">
                        <div><div class="form-label">Table</div>
                            <select id="smart-table" class="form-select w-full"><option value="">Select...</option>${tableOptions}</select></div>
                        <div><div class="form-label">Row change (%)</div><input id="smart-row-threshold" type="number" value="5" class="form-input w-full"></div>
                        <div><div class="form-label">Null increase (%)</div><input id="smart-null-threshold" type="number" value="5" class="form-input w-full"></div>
                        <div class="flex items-end gap-2">
                            <button onclick="SettingsPage.checkSmartAlerts()" id="smart-check-btn" class="btn btn-primary btn-sm flex-1"><i class="fas fa-shield mr-1"></i>Check</button>
                            <button onclick="SettingsPage.checkAllSmartAlerts()" class="btn btn-ghost btn-sm" title="Check all tables"><i class="fas fa-check-double"></i></button>
                        </div>
                    </div>
                    <div id="smart-alerts-result"></div>`, null, 'smart_monitoring')}

                ${this._section('watcher', 'fa-eye', 'teal', 'Live Data Monitor <span class="tag tag-gray" style="font-size:10px"><i class="fas fa-lock mr-1"></i>Pro / Lifetime</span>',
                    'Watch folders for new files and auto-import them.', `
                    <div class="flex gap-2">
                        <button onclick="SettingsPage.startWatcher()" class="btn btn-primary btn-sm"><i class="fas fa-play mr-1"></i>Start</button>
                        <button onclick="SettingsPage.stopWatcher()" class="btn btn-danger btn-sm"><i class="fas fa-stop mr-1"></i>Stop</button>
                        <button onclick="SettingsPage.scanWatcher()" class="btn btn-secondary btn-sm"><i class="fas fa-search mr-1"></i>Scan</button>
                    </div>
                    <div id="watcher-status" class="text-sm mt-2"></div>`, null, 'live_monitor')}

            </div>
        </div>`;
    },

    // ── Workspace name ────────────────────────────────────────────────
    async _saveWorkspaceName() {
        const field = document.getElementById('workspace-rename');
        if (!field) return;
        const name = (field.value || '').trim();
        if (!name) {
            App.error('Give the workspace a name.');
            return;
        }
        try {
            await window.TenantOnboarding.save(name);
            App.success('Workspace renamed');
            if (window.App && App.applyTenant) App.applyTenant();
        } catch (e) {
            App.error((e && e.message) || 'The workspace name could not be saved.');
        }
    },

    // ── Self-serve account deletion ───────────────────────────────────
    async _deleteAccount() {
        const field = document.getElementById('delete-account-password');
        const password = field ? field.value : '';
        if (!password) {
            App.error('Enter your password to confirm the deletion.');
            return;
        }
        try {
            await window.AccountDelete.run(password);
        } catch (e) {
            App.error(window.AccountDelete.message(e));
        }
    },

    // ═════════════════════════════════════════════════════════════════
    // ENTERPRISE TAB
    // ═════════════════════════════════════════════════════════════════
    _renderEnterprise(tableNames) {
        const tableOptions = tableNames.map(t => `<option value="${this._esc(t)}">${this._esc(t)}</option>`).join('');
        return `
        <div class="grid grid-cols-1 gap-4">
            ${this._section('notif', 'fa-bell', 'blue', 'Notification Preferences',
                'Configure how you receive alerts, approvals and system events.', `
                <div id="notif-prefs-content"><div class="settings-skeleton" style="height:56px"></div></div>`)}
            <div class="grid grid-cols-2 gap-4">
                ${this._section('apikeys', 'fa-key', 'purple', 'API Key Management',
                    'Issue scoped keys for programmatic access.', `
                    <div class="flex gap-2 mb-3">
                        <input id="api-key-name" class="form-input flex-1" placeholder="Key name (e.g., ci-cd-pipeline)">
                        <select id="api-key-scope" class="form-select" style="width:110px"><option value="read">Read</option><option value="write">Write</option><option value="admin">Admin</option></select>
                        <button onclick="SettingsPage._createApiKey()" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i></button>
                        <button onclick="SettingsPage._listApiKeys()" class="btn btn-secondary btn-sm" title="Refresh keys"><i class="fas fa-sync"></i></button>
                    </div>
                    <div id="api-keys-list"></div>`,
                    `<button onclick="SettingsPage._listApiKeys()" class="btn btn-sm btn-secondary"><i class="fas fa-rotate"></i></button>`)}

                ${this._section('sla', 'fa-clock', 'amber', 'SLA Definitions',
                    'Data freshness SLAs for warehouse tables.', `
                    <div class="grid grid-cols-4 gap-2 mb-3">
                        <input id="sla-name" class="form-input" placeholder="SLA name">
                        <select id="sla-table" class="form-select"><option value="">Table...</option>${tableOptions}</select>
                        <input id="sla-hours" type="number" value="24" class="form-input" placeholder="Hours">
                        <button onclick="SettingsPage._addSla()" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i> Add</button>
                    </div>
                    <div id="sla-list"></div>`,
                    `<button onclick="SettingsPage._loadSlaList()" class="btn btn-sm btn-secondary"><i class="fas fa-sync"></i></button>`)}

                ${this._section('budgets', 'fa-coins', 'green', 'Cost Budgets',
                    'Set spending limits for query costs.', `
                    <div class="grid grid-cols-4 gap-2 mb-3">
                        <input id="budget-name" class="form-input" placeholder="Budget name">
                        <input id="budget-limit" type="number" value="100" class="form-input" placeholder="Limit ($)">
                        <select id="budget-period" class="form-select"><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="yearly">Yearly</option></select>
                        <button onclick="SettingsPage._addBudget()" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i> Add</button>
                    </div>
                    <div id="budget-list"></div>`,
                    `<button onclick="SettingsPage._loadBudgetList()" class="btn btn-sm btn-secondary"><i class="fas fa-sync"></i></button>`)}

                ${this._section('slawatch', 'fa-play', 'green', 'SLA Monitor',
                    'Continuously check SLAs on an interval.', `
                    <div class="flex gap-2 items-center">
                        <input id="sla-interval" type="number" value="60" class="form-input" style="width:90px">
                        <span class="text-xs text-secondary">minute interval</span>
                        <button onclick="SettingsPage._startSlaMonitor()" class="btn btn-primary btn-sm"><i class="fas fa-play mr-1"></i>Start Monitor</button>
                    </div>`)}

                ${this._section('catalog', 'fa-book', 'emerald', 'Data Catalog',
                    'Synchronize warehouse tables into the data catalog.', `
                    <div class="flex items-center gap-3">
                        <button onclick="SettingsPage._syncCatalog()" class="btn btn-primary btn-sm"><i class="fas fa-sync mr-1"></i>Sync Now</button>
                        <span id="catalog-sync-status" class="text-xs text-secondary"></span>
                    </div>`)}
            </div>
        </div>`;
    },

    // ═════════════════════════════════════════════════════════════════
    // GOVERNANCE TAB
    // ═════════════════════════════════════════════════════════════════
    _renderGovernance(tableNames) {
        const tableOptions = tableNames.map(t => `<option value="${this._esc(t)}">${this._esc(t)}</option>`).join('');
        return `
        <div class="grid grid-cols-1 gap-4">
            <div class="grid grid-cols-2 gap-4">
                ${this._section('masking', 'fa-mask', 'indigo', 'Data Masking Rules',
                    'Column-level masking for PII and sensitive data.', `
                    <div class="grid grid-cols-4 gap-2 mb-3">
                        <select id="mask-table" class="form-select"><option value="">Table...</option>${tableOptions}</select>
                        <input id="mask-column" class="form-input" placeholder="Column name">
                        <select id="mask-strategy" class="form-select">
                            <option value="null">NULL</option><option value="partial">Partial (last 4)</option>
                            <option value="hash">SHA-256</option><option value="truncate">Truncate</option>
                            <option value="email">Email</option><option value="ssn">SSN</option>
                        </select>
                        <button onclick="SettingsPage._addMaskingRule()" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i></button>
                    </div>
                    <div id="masking-rules-list"></div>
                    <div class="mt-2"><button onclick="SettingsPage._scanPii()" class="settings-link"><i class="fas fa-search mr-1"></i>Scan tables for PII</button></div>`,
                    `<button onclick="SettingsPage._loadMaskingRules()" class="btn btn-sm btn-secondary"><i class="fas fa-sync"></i></button>`)}

                ${this._section('retention', 'fa-trash', 'red', 'Data Retention Policies',
                    'Automatically delete or anonymize old data.', `
                    <div class="grid grid-cols-4 gap-2 mb-3">
                        <select id="ret-table" class="form-select"><option value="">Table...</option>${tableOptions}</select>
                        <input id="ret-days" type="number" value="365" class="form-input" placeholder="Days">
                        <select id="ret-action" class="form-select"><option value="delete">Delete</option><option value="anonymize">Anonymize</option></select>
                        <button onclick="SettingsPage._addRetentionPolicy()" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i></button>
                    </div>
                    <div id="retention-list"></div>
                    <div class="mt-2"><button onclick="SettingsPage._runRetention()" class="btn btn-danger btn-sm"><i class="fas fa-trash mr-1"></i>Execute Retention Now</button></div>`,
                    `<button onclick="SettingsPage._loadRetentionPolicies()" class="btn btn-sm btn-secondary"><i class="fas fa-sync"></i></button>`)}

                ${this._section('pii', 'fa-shield', 'purple', 'PII Inventory',
                    'Detected personally identifiable information across all tables.', `
                    <div id="pii-inventory-list"><div class="settings-skeleton" style="height:64px"></div></div>`,
                    `<button onclick="SettingsPage._loadPiiInventory()" class="btn btn-sm btn-secondary"><i class="fas fa-sync"></i></button>`)}

                ${this._section('privacy', 'fa-gavel', 'amber', 'Privacy Requests (GDPR/CCPA)',
                    'Data subject access and deletion requests.', `
                    <div id="privacy-requests-list"><div class="settings-skeleton" style="height:64px"></div></div>`,
                    `<button onclick="SettingsPage._loadPrivacyRequests()" class="btn btn-sm btn-secondary"><i class="fas fa-sync"></i></button>`)}
            </div>

            ${this._section('compliance', 'fa-chart-bar', 'green', 'Compliance Report',
                'High-level governance metrics.', `
                <div id="compliance-report"><div class="settings-skeleton" style="height:64px"></div></div>`,
                `<button onclick="SettingsPage._loadComplianceReport()" class="btn btn-sm btn-secondary"><i class="fas fa-sync"></i></button>`)}
        </div>`;
    },

    // ── Tab loading ──────────────────────────────────────────────────
    _loadGeneralData() {
        // Alerts/schedules are premium-gated server-side; skip the fetches on
        // the free plan so the console stays clean and cards show upsells.
        if (window.RBAC && !RBAC.hasFeature('alerts_thresholds')) {
            const el = document.getElementById('alerts-list');
            if (el) el.innerHTML = this._locked('Alert rules are a Pro feature');
        } else {
            this._listAlerts();
        }
        if (window.RBAC && !RBAC.hasFeature('scheduled_reports')) {
            const el2 = document.getElementById('schedules-list');
            if (el2) el2.innerHTML = this._locked('Scheduled reports are a Pro feature');
        } else {
            this._listSchedules();
        }
    },
    _locked(msg) {
        return '<div class="settings-empty"><i class="fas fa-lock"></i>' + this._esc(msg) +
            ' <button class="btn btn-ghost btn-xs ml-2" onclick="RBAC.showUpgradeOverlay(\'enterprise\')">Upgrade</button></div>';
    },
    _loadEnterpriseData() {
        this._loadNotifPrefs();
        this._listApiKeys();
        this._loadSlaList();
        this._loadBudgetList();
    },
    async _loadGovernanceData() {
        await Promise.all([
            this._loadMaskingRules(),
            this._loadRetentionPolicies(),
            this._loadPiiInventory(),
            this._loadPrivacyRequests(),
            this._loadComplianceReport(),
        ]);
    },

    async _refreshAll() {
        this._switchSection(this._current);
    },

    // Accessibility: every input/select/textarea gets an accessible name.
    // Chrome reports "No label associated" for placeholder-only fields, so
    // derive aria-label from placeholder / id when no <label> exists.
    _fixA11y(root) {
        try {
            const scope = root || document.getElementById('settings-content') || document;
            scope.querySelectorAll('input,select,textarea').forEach(el => {
                if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return;
                const label = el.getAttribute('placeholder')
                    || (el.id ? el.id.replace(/[-_]/g, ' ') : '')
                    || 'input';
                el.setAttribute('aria-label', label);
                if (!el.id) el.id = 'settings-' + Math.random().toString(36).slice(2, 8);
            });
        } catch (_) {}
    },

    // ── System Health strip ──────────────────────────────────────────
    async _loadHealthStrip() {
        const el = document.getElementById('settings-health-strip');
        if (!el) return;
        try {
            const data = await API.systemHealth();
            const comps = data.components || {};
            const items = [
                { k: 'api', label: 'API Server', icon: 'fa-server', detail: (comps.api && comps.api.version) || '' },
                { k: 'database', label: 'Database', icon: 'fa-database', detail: ((comps.database && comps.database.path) || '').split('/').pop() },
                { k: 'llm', label: 'LLM Engine', icon: 'fa-brain', detail: (comps.llm && comps.llm.model) || '' },
                { k: 'auth', label: 'Auth', icon: 'fa-lock', detail: (comps.auth && comps.auth.jwt_enabled) ? 'JWT enabled' : '' },
            ];
            const status = k => {
                const c = comps[k] || { status: 'unknown' };
                const ok = c.status === 'healthy' || c.status === 'active' || c.status === 'configured';
                return ok ? 'ok' : 'warn';
            };
            el.innerHTML = items.map(it => `
                <div class="settings-health-item">
                    <div class="flex-1 min-w-0">
                        <div class="settings-health-label"><i class="fas ${it.icon}" style="margin-right:6px"></i>${it.label}</div>
                        ${it.detail ? `<div class="settings-health-detail truncate">${this._esc(it.detail)}</div>` : ''}
                    </div>
                    <span class="settings-health-dot ${status(it.k)}"></span>
                </div>`).join('');
        } catch (e) {
            el.innerHTML = `<div class="settings-error"><i class="fas fa-circle-exclamation"></i><span>Health check unavailable</span></div>`;
        }
    },

    // ── JWT Token (masked) ───────────────────────────────────────────
    _toggleToken() {
        const el = document.getElementById('jwt-token-display');
        const eye = document.getElementById('token-eye');
        if (!el || !eye) return;
        const real = localStorage.getItem('auth_token') || '';
        if (el.type === 'text') {
            el.type = 'password';
            el.value = real ? '••••••••' : '';
            eye.innerHTML = '<i class="fas fa-eye"></i>';
        } else {
            el.type = 'text';
            el.value = real;
            eye.innerHTML = '<i class="fas fa-eye-slash"></i>';
        }
    },
    _copyToken() {
        const real = localStorage.getItem('auth_token') || '';
        if (!real) return App.toast('No token stored', 'info');
        navigator.clipboard.writeText(real);
        App.toast('Token copied', 'success');
    },
    async _verifyToken() {
        const el = document.getElementById('token-status');
        const token = localStorage.getItem('auth_token');
        if (!token) { if (el) el.innerHTML = '<span class="text-amber-500">No token stored</span>'; return; }
        try {
            const resp = await fetch((window.__API_BASE__ || '') + '/auth/verify', { headers: { 'Authorization': 'Bearer ' + token } });
            const data = await resp.json();
            if (el) el.innerHTML = data.authenticated
                ? `<span class="text-green-500">Valid — ${this._esc((data.user && (data.user.username || data.user.role)) || 'authenticated')}</span>`
                : '<span class="text-red-500">Invalid / expired</span>';
        } catch (e) {
            if (el) el.innerHTML = `<span class="text-red-500">Verify failed: ${this._esc(e.message)}</span>`;
        }
    },
    _clearToken() {
        localStorage.removeItem('auth_token');
        localStorage.removeItem('session');
        const el = document.getElementById('jwt-token-display');
        if (el) { el.value = ''; el.type = 'password'; }
        const st = document.getElementById('token-status');
        if (st) st.innerHTML = '<span class="text-gray-400">Token cleared</span>';
        App.toast('Token cleared', 'info');
    },

    // ── LLM Providers ────────────────────────────────────────────────
    async refreshProviders() {
        const el = document.getElementById('llm-providers-list');
        if (!el) return;
        try {
            // The opencode card also shows the CLI version and the loopback
            // server state. Those live on their own endpoints, so they are
            // fetched in parallel with the provider list — once per card
            // render, never per repaint (see _loadOpencodeInfo).
            const [data, opencodeInfo] = await Promise.all([
                API.llmProviders(),
                this._loadOpencodeInfo(),
            ]);
            const providers = data.providers || [];
            let modelsByProvider = {};
            try {
                const m = await API.llmModels();
                if (m && m.models && typeof m.models === 'object') modelsByProvider = m.models || {};
            } catch (_) {}

            const statusPill = (s, reason) => {
                const colors = { configured: 'tag-green', running: 'tag-green', missing_key: 'tag-amber', no_model: 'tag-amber', unreachable: 'tag-red', offline: 'tag-gray' };
                return `<span class="tag ${colors[s] || 'tag-gray'}" title="${this._esc(reason || '')}">${s === 'configured' ? 'Ready' : this._esc((s || '').replace(/_/g, ' '))}</span>`;
            };

            const cards = providers.map(p => {
                const cfg = p.config || {};
                const isDefault = p.is_default;
                const needsKey = p.needs_api_key;
                const isOpencode = p.name === 'opencode';
                const showBaseUrl = ['ollama', 'lmstudio', 'openrouter'].includes(p.name);
                const showProxies = false;
                const proxies = (cfg.proxies || []).join(', ');
                // GET /llm/models returns one entry per model, and each entry is
                // an OBJECT ({id} for opencode, {name}/{model} for ollama).
                // Stringifying them produced "[object Object]" datalist options,
                // so the real model id is read out of the object instead.
                const rawModels = modelsByProvider[p.name];
                const models = (Array.isArray(rawModels) ? rawModels : []).map(mm => this._modelId(mm)).filter(Boolean);
                const modelOptions = models.slice(0, 60).map(id => `<option value="${this._esc(id)}"></option>`).join('');
                const keyPlaceholder = cfg.api_key_masked ? `● ${this._esc(cfg.api_key_masked)}` : 'API key';
                let geminiBlock = '';
                if (p.name === 'gemini') {
                    const g = p.google || {};
                    if (g.active) {
                        geminiBlock = `<span class="tag tag-blue" style="font-size:10px"><i class="fas fa-check-circle"></i> Google: ${this._esc(g.email || 'signed in')}</span>
                            <button onclick="SettingsPage._geminiSignOut()" class="settings-link">Sign out</button>`;
                    } else if (g.configured) {
                        geminiBlock = `<button onclick="SettingsPage._geminiSignIn()" class="btn btn-sm" style="background:#4285F4;border-color:#4285F4;color:#fff"><i class="fab fa-google mr-1"></i>Sign in with Google</button>`;
                    } else {
                        geminiBlock = `<span class="text-xs text-secondary">Google sign-in needs an OAuth client (<code>google.client_id</code> in settings.yaml) or a Gemini API key above</span>`;
                    }
                }
                // opencode is a real, configurable provider: it has a model
                // override, a CLI version and a loopback server — show them.
                const opencodeBlock = isOpencode ? this._renderOpencodeDetail(cfg, opencodeInfo) : '';
                return `<div class="settings-provider ${isDefault ? 'default' : ''}">
                    <div class="settings-provider-icon"><i class="fas ${p.icon || 'fa-cube'}"></i></div>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="font-medium text-sm">${this._esc(p.label || p.name)}</span>
                            ${statusPill(p.status, p.status_reason)}
                            ${isDefault ? '<span class="tag tag-primary" style="font-size:10px">DEFAULT</span>' : ''}
                            <span class="text-xs text-secondary">Model: ${cfg.model ? this._esc(cfg.model) : 'Not set'}</span>
                            ${cfg.has_api_key ? '<i class="fas fa-key" style="color:var(--color-accent-green);font-size:10px" title="API key set"></i>' : (needsKey ? '<i class="fas fa-exclamation-triangle" style="color:var(--color-accent-amber);font-size:10px" title="No API key"></i>' : '')}
                            ${geminiBlock}
                        </div>
                        <form class="settings-provider-controls" autocomplete="off" onsubmit="return false;">
                            ${needsKey ? `<input id="llm-key-${p.name}" type="password" autocomplete="new-password" aria-label="${p.label || p.name} API key" placeholder="${keyPlaceholder}" class="form-input text-xs" style="width:170px">` : ''}
                            <input id="llm-model-${p.name}" type="text" placeholder="${isOpencode ? 'Model (auto = fastest free)' : 'Model'}" aria-label="${p.label || p.name} model" list="llm-models-${p.name}" class="form-input text-xs" style="width:190px" value="${this._esc(cfg.model || '')}">
                            ${modelOptions ? `<datalist id="llm-models-${p.name}">${modelOptions}</datalist>` : ''}
                            ${showBaseUrl ? `<input id="llm-url-${p.name}" type="text" placeholder="Base URL" aria-label="${p.label || p.name} base URL" class="form-input text-xs" style="width:210px" value="${this._esc(cfg.base_url || '')}">` : ''}
                            ${showProxies ? `<input id="llm-proxies-${p.name}" type="text" placeholder="Proxies (comma-separated, for IP rotation)" class="form-input text-xs" style="width:230px" value="${this._esc(proxies)}">` : ''}
                            <button type="button" onclick="SettingsPage._saveProvider('${p.name}')" class="btn btn-primary btn-sm">Save</button>
                            ${!isDefault ? `<button type="button" onclick="SettingsPage._setDefault('${p.name}')" class="settings-link">Set Default</button>` : ''}
                            ${p.name === 'opencode' ? '' : `<button type="button" onclick="SettingsPage._testProvider('${p.name}')" class="settings-link">Test</button>`}
                            ${(needsKey || p.name === 'opencode') ? `<button type="button" onclick="SettingsPage._clearProvider('${p.name}')" class="settings-link settings-link-danger">Clear overrides</button>` : ''}
                            <span id="llm-test-${p.name}" class="text-xs"></span>
                        </form>
                        ${p.name === 'lmstudio' ? '<p class="text-xs text-secondary mt-1"><i class="fas fa-circle-info mr-1" style="color:var(--color-accent-blue)"></i>Independent local server on <code>localhost:1234</code> — separate from OpenAI. No API key; never contacts the OpenAI cloud.</p>' : ''}
                        ${p.name === 'ollama' ? '<p class="text-xs text-secondary mt-1"><i class="fas fa-circle-info mr-1" style="color:var(--color-accent-blue)"></i>Separate local runtime on <code>localhost:11434</code> — independent of the OpenAI provider above.</p>' : ''}
                        ${opencodeBlock}
                    </div>
                </div>`;
            }).join('');

            const patterns = (data.patterns || []).map(pt => `<span class="tag tag-gray" style="font-size:10px">${this._esc(pt)}</span>`).join(' ');
            const routingCard = `<div class="settings-provider">
                <div class="settings-provider-icon"><i class="fas fa-route" style="color:var(--color-primary)"></i></div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="font-medium text-sm">Smart Routing</span>
                        <span class="text-xs text-secondary">route query types to a provider, e.g. complex_sql:openai, narrative:anthropic</span>
                    </div>
                    <div class="mt-2 flex flex-wrap items-center gap-2">${patterns}</div>
                    <div class="mt-2 flex items-center gap-2 flex-wrap">
                        <input id="llm-routing-rules" type="text" class="form-input flex-1 text-xs" placeholder="pattern:provider, pattern:provider" value="${data.routing_rules ? this._esc(data.routing_rules) : ''}">
                        <button onclick="SettingsPage._saveRouting()" class="btn btn-primary btn-sm">Save Routing</button>
                    </div>
                </div>
            </div>`;
            el.innerHTML = routingCard + cards;
            this._fixA11y(el);
        } catch (e) {
            if (el) el.innerHTML = `<div class="settings-error"><i class="fas fa-circle-exclamation"></i><span>Failed: ${this._esc(e.message)}</span></div>`;
        }
    },

    // ── opencode helpers ─────────────────────────────────────────────
    // The model list returned by GET /llm/models holds OBJECTS, one per
    // model: {id, …} for opencode, {name, model, …} for ollama. Read the
    // identifier out of the object instead of stringifying it (which
    // produced "[object Object]" datalist options).
    _modelId(entry) {
        if (entry == null) return '';
        if (typeof entry === 'string') return entry;
        if (typeof entry === 'object') return entry.id || entry.name || entry.model || '';
        return '';
    },

    // GET /llm/providers/opencode/{version,server}. These two endpoints are
    // not wrapped by the shared API client, so the request is made here.
    _opencodeGet(path) {
        const headers = {};
        const token = (window.RBAC && window.RBAC.getToken) ? window.RBAC.getToken() : null;
        if (token) headers['Authorization'] = 'Bearer ' + token;
        return fetch((window.__API_BASE__ || '') + path, { headers: headers })
            .then(r => {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            });
    },

    // Fetched once when the provider card renders and cached briefly, so a
    // repaint (tab switch, refresh, post-save) does not re-hit the server.
    // A failure is tolerated: the card still renders, just without the
    // version / server lines.
    async _loadOpencodeInfo() {
        const now = Date.now();
        if (this._opencodeInfo && (now - this._opencodeInfoTs) < 15000) return this._opencodeInfo;
        const [version, server] = await Promise.all([
            this._opencodeGet('/llm/providers/opencode/version').catch(() => null),
            this._opencodeGet('/llm/providers/opencode/server').catch(() => null),
        ]);
        this._opencodeInfo = { version: version, server: server };
        this._opencodeInfoTs = now;
        return this._opencodeInfo;
    },

    // The opencode card body: what "auto" means, the CLI version, and the
    // loopback AI server (URL / port / running state).
    _renderOpencodeDetail(cfg, info) {
        const v = (info && info.version) || null;
        const s = (info && info.server) || null;
        const model = (cfg && cfg.model) || '';
        const rows = [];

        let modelHint;
        if (!model) modelHint = 'no model set — the opencode server picks its own default';
        else if (/^auto$/i.test(model)) modelHint = 'the opencode server picks the fastest free model it offers, on every request';
        else modelHint = 'pinned to this model — Clear overrides puts it back on auto';
        rows.push(`<p class="text-xs text-secondary mt-1">
            <i class="fas fa-circle-info mr-1" style="color:var(--color-accent-blue)"></i>
            Model <code class="settings-code">${this._esc(model || 'not set')}</code> — ${modelHint}.
        </p>`);

        if (v && (v.current || v.pinned)) {
            const current = v.current || v.pinned;
            const bits = [`CLI <code class="settings-code">${this._esc(current)}</code>`];
            if (v.pinned && v.pinned !== current) bits.push(`pinned release ${this._esc(v.pinned)}`);
            if (v.update_available && v.latest) {
                bits.push(`<span class="tag tag-amber" style="font-size:10px"><i class="fas fa-arrow-up mr-1"></i>update available: ${this._esc(v.latest)}</span>`);
            } else {
                bits.push('up to date');
            }
            rows.push(`<p class="text-xs text-secondary mt-1"><i class="fas fa-terminal mr-1"></i>${bits.join(' &middot; ')}</p>`);
        }

        if (s) {
            const bits = [];
            if (s.server_url) bits.push(`<code class="settings-code">${this._esc(s.server_url)}</code>`);
            if (s.port) bits.push(`port ${this._esc(String(s.port))}`);
            if (s.installed === false) bits.push('<span class="tag tag-red" style="font-size:10px">CLI not installed</span>');
            if (s.running) {
                bits.push('<span class="tag tag-green" style="font-size:10px">running</span>');
                if (s.pid) bits.push(`pid ${this._esc(String(s.pid))}`);
            } else {
                bits.push('<span class="tag tag-gray" style="font-size:10px">not running</span>');
            }
            if (s.port_busy) bits.push('<span class="tag tag-amber" style="font-size:10px">port busy</span>');
            rows.push(`<p class="text-xs text-secondary mt-1"><i class="fas fa-server mr-1"></i>Server ${bits.join(' &middot; ')}</p>`);
            if (s.last_error) {
                rows.push(`<p class="text-xs mt-1" style="color:var(--color-accent-red)"><i class="fas fa-triangle-exclamation mr-1"></i>${this._esc(s.last_error)}</p>`);
            }
        }

        if (!v && !s) {
            rows.push('<p class="text-xs text-secondary mt-1"><i class="fas fa-circle-info mr-1" style="color:var(--color-accent-amber)"></i>CLI version and server details are unavailable right now.</p>');
        }

        return rows.join('');
    },

    _esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    _ipRotationEnabled() {
        const billing = (window.App && App.state && App.state.billing) || null;
        const ent = (billing && billing.entitlements) || null;
        if (ent && typeof ent.ip_rotation === 'boolean') return ent.ip_rotation;
        return (window.RBAC && window.RBAC.getPlan ? window.RBAC.getPlan() : 'free') !== 'free';
    },

    async _testProvider(provider) {
        const out = document.getElementById('llm-test-' + provider);
        const model = document.getElementById('llm-model-' + provider) ? document.getElementById('llm-model-' + provider).value.trim() : '';
        if (out) out.textContent = 'testing…';
        try {
            const r = await API.testLLMProvider(provider, model);
            if (out) out.textContent = r.success ? `✓ ${r.latency_ms} ms` : `✗ ${r.error || 'failed'}`;
            App.toast(r.success ? `${provider} replied in ${r.latency_ms} ms` : `${provider}: ${r.error || 'failed'}`, r.success ? 'success' : 'error');
        } catch (e) {
            if (out) out.textContent = '✗ failed';
            App.error(e.message);
        }
    },

    async _saveProvider(provider) {
        const keyInput = document.getElementById('llm-key-' + provider);
        const modelInput = document.getElementById('llm-model-' + provider);
        const urlInput = document.getElementById('llm-url-' + provider);
        const proxiesInput = document.getElementById('llm-proxies-' + provider);
        const apiKey = keyInput ? (keyInput.value.trim() || undefined) : undefined;
        const model = modelInput ? modelInput.value.trim() : undefined;
        const baseUrl = urlInput ? (urlInput.value.trim() || undefined) : undefined;
        const proxies = proxiesInput
            ? proxiesInput.value.split(',').map(s => s.trim()).filter(Boolean)
            : undefined;
        if (apiKey === undefined && model === undefined && baseUrl === undefined && proxies === undefined && provider !== 'ollama') return App.toast('Enter a model, key, or base URL', 'error');
        try {
            const r = await API.updateLLMProvider(provider, apiKey, model, false, baseUrl, undefined, proxies);
            App.toast((r.success ? 'Provider updated' : (r.error || 'Failed')) + (r.registered === false ? ' (saved but inactive — needs key + model)' : ''), r.success ? 'success' : 'error');
            if (r.success) this.refreshProviders();
        } catch (e) { App.error(e.message); }
    },

    async _clearProvider(provider) {
        try {
            const r = await API.clearLLMProvider(provider);
            App.toast(r.success ? 'Overrides cleared (falls back to config)' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this.refreshProviders();
        } catch (e) { App.error(e.message); }
    },

    async _setDefault(provider) {
        try {
            const r = await API.updateLLMProvider(provider, undefined, undefined, true);
            App.toast(r.success ? `Default set to ${provider}` : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this.refreshProviders();
        } catch (e) { App.error(e.message); }
    },

    async _saveRouting() {
        const input = document.getElementById('llm-routing-rules');
        const rules = input ? input.value.trim() : '';
        try {
            const r = await API.updateLLMProvider('ollama', undefined, undefined, false, undefined, rules);
            App.toast(r.success ? 'Routing rules saved' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this.refreshProviders();
        } catch (e) { App.error(e.message); }
    },

    async _geminiSignIn() {
        try {
            const r = await API.geminiAuthUrl();
            if (!r.success) return App.toast(r.error || 'Sign-in unavailable', 'error');
            window.open(r.auth_url, '_blank');
            App.toast('Complete the Google consent in the new tab, then hit Refresh', 'info');
        } catch (e) { App.error(e.message); }
    },

    async _geminiSignOut() {
        try {
            const resp = await fetch((window.__API_BASE__ || '') + '/llm/providers/gemini/signout', { method: 'POST' });
            const status = await resp.json();
            App.toast(status.success ? 'Signed out' : 'Sign-out failed', status.success ? 'success' : 'error');
            this.refreshProviders();
        } catch (e) { App.error(e.message); }
    },

    // ── Local AI Dependencies & Models ───────────────────────────────
    async _refreshDeps() {
        const el = document.getElementById('llm-deps-body');
        if (el) el.innerHTML = '<div class="settings-skeleton" style="height:140px"></div>';
        await this._loadDeps();
        this.refreshProviders();
    },

    async _loadDeps() {
        const el = document.getElementById('llm-deps-body');
        if (!el) return;
        try {
            const d = await API.llmDependencies();
            el.innerHTML = this._renderDeps(d || {});
        } catch (e) {
            el.innerHTML = `<div class="settings-error"><i class="fas fa-circle-exclamation"></i><span>Failed to load dependencies: ${this._esc(e.message)}</span></div>`;
        }
    },

    _renderDeps(d) {
        const o = d.ollama || {};
        const l = d.lmstudio || {};
        const py = d.python || {};
        const w = d.warp || {};

        const ollamaPill = o.server_reachable
            ? `<span class="tag tag-green">Ready${o.server_version ? ' v' + this._esc(o.server_version) : ''}</span>`
            : (o.installed
                ? `<span class="tag tag-amber">Installed — server not running</span>`
                : `<span class="tag tag-red">Not installed</span>`);

        const ollamaModels = (o.models || []).map(m => `
            <div class="settings-provider-row">
                <span class="font-mono text-xs truncate" title="${this._esc(m.name)}">${this._esc(m.name)}</span>
                <span class="text-xs text-secondary">${this._esc(m.size)}</span>
                <span class="text-xs text-secondary">${this._esc(m.quantization_level || m.parameter_size || '')}</span>
                <span class="text-xs text-secondary">ctx ${this._esc(m.context_length || '')}</span>
                <button onclick="SettingsPage._ollamaApply('${this._esc(m.name)}')" class="btn btn-sm btn-ghost" title="Set as active model">Use</button>
                <button onclick="SettingsPage._ollamaDelete('${this._esc(m.name)}')" class="btn btn-sm btn-ghost" title="Delete model" style="color:var(--color-accent-red)"><i class="fas fa-trash"></i></button>
            </div>`).join('') || `<div class="settings-empty"><i class="fas fa-inbox"></i><span>No models pulled yet.</span></div>`;

        const recommended = (o.recommended_models || []).map(r => `<option value="${this._esc(r)}"></option>`).join('');

        const ollamaActions = o.server_reachable
            ? `<button onclick="SettingsPage._ollamaPull()" class="btn btn-primary btn-sm"><i class="fas fa-download mr-1"></i>Pull</button>`
            : o.installed
                ? `<button onclick="SettingsPage._ollamaStart()" class="btn btn-success btn-sm"><i class="fas fa-play mr-1"></i>Start server</button>`
                : `<a href="${this._esc(o.install_url || 'https://ollama.com/download')}" target="_blank" rel="noopener" class="btn btn-primary btn-sm"><i class="fas fa-download mr-1"></i>Download</a>
                   <button onclick="SettingsPage._ollamaInstall()" class="btn btn-secondary btn-sm"><i class="fas fa-magic mr-1"></i>Auto-install</button>`;

        const lmPill = l.installed_server
            ? `<span class="tag tag-green">Reachable</span>`
            : l.app_installed
                ? `<span class="tag tag-amber">Installed — not running</span>`
                : `<span class="tag tag-gray">Not detected</span>`;
        const lmModels = (l.models || []).map(m => `
            <span class="tag tag-blue settings-deps-chip">${this._esc(m.id)}
                <button onclick="SettingsPage._lmStudioApply('${this._esc(m.id)}')" class="settings-link" style="margin-left:6px">Use</button>
            </span>`).join('') || (l.installed_server
                ? '<span class="text-xs text-secondary">Server reachable — no model loaded in LM Studio yet.</span>'
                : `<span class="text-xs text-secondary">${this._esc(l.guide || '')}</span>`);

        const pyPills = Object.entries(py).map(([name, info]) => `
            <span class="tag ${info && info.installed ? 'tag-green' : 'tag-red'}" title="${this._esc((info && info.version) || 'missing')}">
                <i class="fas ${info && info.installed ? 'fa-check' : 'fa-xmark'} mr-1"></i>${this._esc(name)}
            </span>`).join(' ') || '<span class="text-xs text-secondary">No python package info.</span>';

        const warpPill = w.connected
            ? `<span class="tag tag-green">Connected</span>`
            : (w.installed
                ? `<span class="tag tag-amber">Installed — not connected</span>`
                : `<span class="tag tag-red">Not installed</span>`);

        const warpActions = w.installed
            ? `<button onclick="SettingsPage._warpConnect()" class="btn btn-success btn-sm"><i class="fas fa-play mr-1"></i>Connect</button>
               <button onclick="SettingsPage._warpDisconnect()" class="btn btn-ghost btn-sm"><i class="fas fa-stop mr-1"></i>Disconnect</button>
               <button onclick="SettingsPage._warpReset()" class="btn btn-secondary btn-sm"><i class="fas fa-rotate mr-1"></i>Quick Reset</button>`
            : `<button onclick="SettingsPage._warpInstall()" class="btn btn-secondary btn-sm"><i class="fas fa-magic mr-1"></i>Auto-install</button>`;

        const ipRotation = this._ipRotationEnabled();
        const warpSection = ipRotation
            ? `<div class="settings-provider">
                <div class="settings-provider-icon"><i class="fas fa-robot" style="color:var(--color-accent-blue)"></i></div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="font-medium text-sm">Cloudflare WARP</span> ${warpPill}
                    </div>
                    <p class="text-xs text-secondary mt-1"><i class="fas fa-circle-info mr-1" style="color:var(--color-accent-blue)"></i>Routes provider traffic through a Cloudflare WARP tunnel and rotates the egress IP — used to recover from provider rate limits.</p>
                    <div class="mt-2 flex items-center gap-2 flex-wrap">${warpActions}</div>
                    <div id="warp-install-progress"></div>
                </div>
            </div>`
            : `<div class="settings-provider">
                <div class="settings-provider-icon"><i class="fas fa-lock" style="color:var(--color-accent-red)"></i></div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="font-medium text-sm">Cloudflare WARP</span>
                        <span class="tag tag-gray" style="font-size:10px"><i class="fas fa-lock mr-1"></i>Pro / Lifetime</span>
                    </div>
                    <p class="text-xs text-secondary mt-1">Cloudflare WARP egress rotation is available on the Pro or Lifetime plan.</p>
                    <div class="mt-2 flex items-center gap-2 flex-wrap">
                        <a href="#subscription" class="btn btn-primary btn-sm"><i class="fas fa-crown mr-1"></i>Upgrade</a>
                    </div>
                </div>
            </div>`;

        return `
        <div class="space-y-4">
            <div class="settings-provider">
                <div class="settings-provider-icon"><i class="fas fa-server" style="color:var(--color-primary)"></i></div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="font-medium text-sm">Ollama</span> ${ollamaPill}
                        ${o.installed ? `<span class="text-xs text-secondary">v${this._esc(o.version)}</span>` : ''}
                    </div>
                    <p class="text-xs text-secondary mt-1">Server: <code class="settings-code">${this._esc(o.server_url || 'http://localhost:11434')}</code></p>
                    ${o.server_reachable ? `
                    <div class="mt-2 space-y-1">${ollamaModels}</div>
                    <div class="mt-3 flex items-center gap-2 flex-wrap">
                        <input id="llm-pull-model" list="llm-recommended-models" placeholder="Model (e.g. qwen2.5:7b)" class="form-input text-xs" style="width:220px">
                        <datalist id="llm-recommended-models">${recommended}</datalist>
                        ${ollamaActions}
                        <span class="text-xs text-secondary">active: <code class="settings-code">${this._esc(o.active_model || 'not set')}</code></span>
                    </div>` : `
                    <div class="mt-2 flex items-center gap-2 flex-wrap">${ollamaActions}</div>`}
                    <div id="llm-pull-progress"></div>
                </div>
            </div>

            <div class="settings-provider">
                <div class="settings-provider-icon"><i class="fas fa-desktop" style="color:var(--color-accent-purple)"></i></div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="font-medium text-sm">LM Studio</span> ${lmPill}
                        <span class="tag tag-gray" style="font-size:10px">local · no API key</span>
                        ${l.installed_server ? `<span class="text-xs text-secondary"><i class="fas fa-server mr-1" style="color:var(--color-accent-green)"></i><code class="settings-code">${this._esc(l.server_url || '')}</code></span>` : ''}
                    </div>
                    <p class="text-xs text-secondary mt-1"><i class="fas fa-circle-info mr-1" style="color:var(--color-accent-blue)"></i>Separate local provider on <code class="settings-code">${this._esc(l.server_url || 'http://localhost:1234/v1')}</code> — fully independent of the OpenAI provider, never contacts the cloud.</p>
                    <div class="mt-2 flex flex-wrap items-center gap-1">${lmModels}</div>
                    ${!l.installed_server ? `
                    <div class="mt-3 flex items-center gap-2 flex-wrap">
                        ${l.app_installed
                            ? `<button onclick="SettingsPage._lmStudioLaunch()" class="btn btn-success btn-sm"><i class="fas fa-play mr-1"></i>Start server</button>
                               <span class="text-xs text-secondary">CLI: <code class="settings-code" title="${this._esc(l.binary || '')}">${this._esc((l.binary || 'lms').split('/').slice(-2).join('/'))}</code></span>`
                            : `<a href="${this._esc(l.download_url || 'https://lmstudio.ai')}" target="_blank" rel="noopener" class="btn btn-primary btn-sm"><i class="fas fa-download mr-1"></i>Download</a>
                               <button onclick="SettingsPage._lmStudioInstall()" class="btn btn-secondary btn-sm"><i class="fas fa-magic mr-1"></i>Auto-install</button>`}
                    </div>` : ''}
                    <div id="lmstudio-install-progress"></div>
                </div>
            </div>

            ${warpSection}

            <div class="settings-provider">
                <div class="settings-provider-icon"><i class="fas fa-cube" style="color:var(--color-accent-green)"></i></div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="font-medium text-sm">Python Backend Dependencies</span>
                    </div>
                    <div class="mt-2 flex flex-wrap items-center gap-1">${pyPills}</div>
                </div>
            </div>
        </div>`;
    },

    async _ollamaInstall() {
        try {
            const r = await API.ollamaInstall();
            if (!r.success) return App.toast(r.error || 'Install failed', 'error');
            if (r.already_installed) return App.toast('Ollama is already installed', 'info');
            App.toast('Downloading Ollama installer…', 'info');
            this._pollInstallJob(r.job_id);
        } catch (e) { App.error(e.message); }
    },

    _pollInstallJob(jobId) {
        const el = document.getElementById('llm-pull-progress');
        if (!el) return;
        this._clearPollers();
        const iv = setInterval(async () => {
            try {
                const r = await API.ollamaInstallStatus(jobId);
                const job = (r.success && r.job) || {};
                el.innerHTML = `<div class="flex justify-between text-xs mt-1"><span>${this._esc(job.stage || job.status || 'installing')}</span><span>${job.pct || 0}%</span></div>
                    <div class="progress-bar mt-1"><div class="progress-bar-fill" style="width:${(job.pct || 0) * 1.0}%"></div></div>
                    ${job.log ? `<pre class="settings-code mt-1" style="white-space:pre-wrap">${this._esc(job.log)}</pre>` : ''}`;
                if (job.status === 'done') {
                    clearInterval(iv); el.innerHTML = '';
                    this._loadDeps(); this.refreshProviders();
                    App.toast('Ollama install finished', 'success');
                } else if (job.status === 'error') {
                    clearInterval(iv);
                    this._loadDeps();
                    App.toast(job.error || 'Install failed', 'error');
                }
            } catch (e) { clearInterval(iv); }
        }, 1200);
        this._pullTimer = iv;
    },

    async _ollamaStart() {
        try {
            const r = await API.ollamaStart();
            if (!r.success) return App.toast(r.error || 'Could not start server', 'error');
            App.toast(r.already_running ? 'Server already running' : 'Server starting…', 'success');
            setTimeout(() => { this._loadDeps(); this.refreshProviders(); }, 2500);
        } catch (e) { App.error(e.message); }
    },

    async _ollamaPull() {
        const input = document.getElementById('llm-pull-model');
        const model = input ? input.value.trim() : '';
        if (!model) return App.toast('Enter a model name', 'error');
        try {
            const r = await API.ollamaPull(model);
            if (!r.success) return App.toast(r.error || 'Pull failed', 'error');
            if (r.already_running) return App.toast(`Already pulling ${model}`, 'info');
            const el = document.getElementById('llm-pull-progress');
            if (el) el.innerHTML = '<div class="mt-2"><div class="progress-bar"><div class="progress-bar-fill" style="width:2%"></div></div></div>';
            this._pollPullJob(r.job_id);
        } catch (e) { App.error(e.message); }
    },

    _pollPullJob(jobId) {
        const el = document.getElementById('llm-pull-progress');
        if (!el) return;
        this._clearPollers();
        const iv = setInterval(async () => {
            try {
                const r = await API.ollamaPullStatus(jobId);
                const job = (r.success && r.job) || {};
                el.innerHTML = `<div class="flex justify-between text-xs mt-1"><span>${this._esc(job.stage || '')} — ${this._esc(job.model || '')}</span><span>${job.pct || 0}%</span></div>
                    <div class="progress-bar mt-1"><div class="progress-bar-fill" style="width:${(job.pct || 0) * 1.0}%"></div></div>
                    ${job.error ? `<span class="text-xs" style="color:var(--color-accent-red)">${this._esc(job.error)}</span>` : ''}`;
                if (job.status === 'done') {
                    clearInterval(iv); el.innerHTML = '';
                    this._loadDeps(); this.refreshProviders();
                    App.toast(`Model ${job.model || ''} ready`, 'success');
                } else if (job.status === 'error') {
                    clearInterval(iv);
                    App.toast(job.error || 'Pull failed', 'error');
                    this._loadDeps();
                }
            } catch (e) { clearInterval(iv); }
        }, 1500);
        this._pullTimer = iv;
    },

    _clearPollers() {
        if (this._pullTimer) { clearInterval(this._pullTimer); this._pullTimer = null; }
        if (this._lmStudioTimer) { clearInterval(this._lmStudioTimer); this._lmStudioTimer = null; }
    },

    async _lmStudioInstall() {
        try {
            const r = await API.lmStudioInstall();
            if (!r.success) return App.toast(r.error || 'Install failed', 'error');
            if (r.already_installed) return App.toast('LM Studio is already installed', 'info');
            App.toast('Downloading LM Studio installer…', 'info');
            this._pollLmStudioInstall(r.job_id);
        } catch (e) { App.error(e.message); }
    },

    _pollLmStudioInstall(jobId) {
        const el = document.getElementById('lmstudio-install-progress');
        if (!el) return;
        this._clearPollers();
        const iv = setInterval(async () => {
            try {
                const r = await API.lmStudioInstallStatus(jobId);
                const job = (r.success && r.job) || {};
                el.innerHTML = `<div class="flex justify-between text-xs mt-1"><span>${this._esc(job.stage || job.status || 'installing')}</span><span>${job.pct || 0}%</span></div>
                    <div class="progress-bar mt-1"><div class="progress-bar-fill" style="width:${(job.pct || 0) * 1.0}%"></div></div>
                    ${job.log ? `<pre class="settings-code mt-1" style="white-space:pre-wrap">${this._esc(job.log)}</pre>` : ''}`;
                if (job.status === 'done') {
                    clearInterval(iv); el.innerHTML = '';
                    this._loadDeps(); this.refreshProviders();
                    App.toast('LM Studio install finished', 'success');
                } else if (job.status === 'error') {
                    clearInterval(iv);
                    this._loadDeps();
                    App.toast(job.error || 'Install failed', 'error');
                }
            } catch (e) { clearInterval(iv); }
        }, 1200);
        this._lmStudioTimer = iv;
    },

    async _lmStudioLaunch() {
        try {
            const r = await API.lmStudioLaunch();
            if (!r.success) return App.toast(r.error || 'Could not start server', 'error');
            App.toast(r.already_running ? 'Server already running' : 'Server starting…', 'success');
            setTimeout(() => { this._loadDeps(); this.refreshProviders(); }, 2500);
        } catch (e) { App.error(e.message); }
    },

    async _warpInstall() {
        try {
            const r = await API.warpInstall();
            if (!r.success) return App.toast(r.error || 'Install failed', 'error');
            if (r.already_installed) return App.toast('WARP is already installed', 'info');
            App.toast('Downloading Cloudflare WARP installer…', 'info');
            this._pollWarpInstallJob(r.job_id);
        } catch (e) { App.error(e.message); }
    },

    _pollWarpInstallJob(jobId) {
        const el = document.getElementById('warp-install-progress');
        if (!el) return;
        this._clearPollers();
        const iv = setInterval(async () => {
            try {
                const r = await API.warpInstallStatus(jobId);
                const job = (r.success && r.job) || {};
                el.innerHTML = `<div class="flex justify-between text-xs mt-1"><span>${this._esc(job.stage || job.status || 'installing')}</span><span>${job.pct || 0}%</span></div>
                    <div class="progress-bar mt-1"><div class="progress-bar-fill" style="width:${(job.pct || 0) * 1.0}%"></div></div>
                    ${job.log ? `<pre class="settings-code mt-1" style="white-space:pre-wrap">${this._esc(job.log)}</pre>` : ''}`;
                if (job.status === 'done') {
                    clearInterval(iv); el.innerHTML = '';
                    this._loadDeps();
                    App.toast('Cloudflare WARP installed', 'success');
                } else if (job.status === 'error') {
                    clearInterval(iv);
                    this._loadDeps();
                    App.toast(job.error || 'Install failed', 'error');
                }
            } catch (e) { clearInterval(iv); }
        }, 1200);
        this._lmStudioTimer = iv;
    },

    async _warpConnect() {
        try {
            const r = await API.warpConnect();
            if (!r.success) return App.toast(r.error || 'Could not connect WARP', 'error');
            App.toast(r.connected ? `WARP connected (${r.ip || 'IP'})` : 'WARP connect requested', 'success');
            setTimeout(() => this._loadDeps(), 2000);
        } catch (e) { App.error(e.message); }
    },

    async _warpDisconnect() {
        try {
            const r = await API.warpDisconnect();
            if (!r.success) return App.toast(r.error || 'Could not disconnect WARP', 'error');
            App.toast('WARP disconnected', 'info');
            this._loadDeps();
        } catch (e) { App.error(e.message); }
    },

    async _warpReset() {
        if (!confirm('Quick-reset WARP? This assigns a new egress IP (disconnect → re-register → connect).')) return;
        try {
            const r = await API.warpReset();
            if (!r.success) return App.toast(r.error || 'WARP reset failed', 'error');
            App.toast(r.note || 'WARP quick-reset complete', 'success');
            setTimeout(() => this._loadDeps(), 2000);
        } catch (e) { App.error(e.message); }
    },

    async _ollamaDelete(model) {
        if (!confirm(`Delete Ollama model "${model}"?`)) return;
        try {
            const r = await API.ollamaDelete(model);
            if (!r.success) return App.toast(r.error || 'Delete failed', 'error');
            App.toast(`Deleted ${model}`, 'success');
            this._loadDeps();
        } catch (e) { App.error(e.message); }
    },

    async _ollamaApply(model) {
        try {
            const urlInput = document.getElementById('llm-url-ollama');
            const baseUrl = urlInput ? urlInput.value.trim() : undefined;
            const r = await API.updateLLMProvider('ollama', undefined, model, false, baseUrl);
            App.toast(r.success ? `Ollama model set to ${model}` : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this.refreshProviders();
        } catch (e) { App.error(e.message); }
    },

    async _lmStudioApply(model) {
        try {
            const r = await API.updateLLMProvider('lmstudio', undefined, model, false);
            App.toast(r.success ? `LM Studio model set to ${model}` : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this.refreshProviders();
        } catch (e) { App.error(e.message); }
    },

    // ── Auth ─────────────────────────────────────────────────────────
    async register() {
        const u = document.getElementById('reg-user') ? document.getElementById('reg-user').value.trim() : '';
        const p = document.getElementById('reg-pass') ? document.getElementById('reg-pass').value : '';
        if (!u || !p) return App.toast('Fill in all fields', 'error');
        try {
            const r = await API.register(u, p);
            App.toast(r.success ? 'User registered!' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) { document.getElementById('reg-user').value = ''; document.getElementById('reg-pass').value = ''; }
        } catch (e) { App.error(e.message); }
    },
    async login() {
        const u = document.getElementById('login-user') ? document.getElementById('login-user').value.trim() : '';
        const p = document.getElementById('login-pass') ? document.getElementById('login-pass').value : '';
        if (!u || !p) return App.toast('Fill in all fields', 'error');
        try {
            const r = await API.login(u, p);
            App.toast(r.success ? `Logged in as ${u}` : (r.error || 'Failed'), r.success ? 'success' : 'error');
        } catch (e) { App.error(e.message); }
    },
    async listUsers() {
        const el = document.getElementById('users-list');
        if (!el) return;
        try {
            const r = await API.users();
            if (!r.success) { el.innerHTML = this._error(r.error || 'Failed'); return; }
            const users = r.users || [];
            if (!users.length) { el.innerHTML = this._empty('No users found'); return; }
            el.innerHTML = `<div class="settings-list-stack">` + users.map(u => `
                <div class="settings-list-item">
                    <div class="flex-1 min-w-0">
                        <div class="font-medium text-sm truncate">${this._esc(u.username || u.user_id || u.name || '?')}</div>
                        <div class="text-xs text-secondary">${this._esc(u.role || 'viewer')}</div>
                    </div>
                    <span class="tag tag-gray" style="font-size:10px;text-transform:uppercase">${this._esc(u.status || 'active')}</span>
                </div>`).join('') + `</div>`;
        } catch (e) { el.innerHTML = this._error('Failed to list users'); }
    },

    // ── Alerts ───────────────────────────────────────────────────────
    async addAlert() {
        const name = document.getElementById('alert-name') ? document.getElementById('alert-name').value.trim() : '';
        const table = document.getElementById('alert-table') ? document.getElementById('alert-table').value.trim() : '';
        const sql = document.getElementById('alert-sql') ? document.getElementById('alert-sql').value.trim() : '';
        if (!name || !table || !sql) return App.toast('Fill in all fields', 'error');
        try {
            const r = await API.addAlert({ name, table, condition_sql: sql, threshold: 0 });
            App.toast(r.message || 'Alert added', r.success ? 'success' : 'error');
            if (r.success) { document.getElementById('alert-name').value = ''; document.getElementById('alert-table').value = ''; document.getElementById('alert-sql').value = ''; this._listAlerts(); }
        } catch (e) { App.toast('Failed to add alert', 'error'); }
    },
    async _listAlerts() {
        const el = document.getElementById('alerts-list');
        if (!el) return;
        el.innerHTML = this._skeletonRows(3);
        try {
            const r = await API.listAlerts();
            const rules = (r.success && r.rules) ? r.rules : [];
            if (!rules.length) { el.innerHTML = this._empty('No alert rules yet'); return; }
            el.innerHTML = rules.map(rule => `
                <div class="settings-list-item">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-medium text-sm truncate">${this._esc(rule.name)}</span>
                            <span class="tag ${rule.enabled ? 'tag-green' : 'tag-gray'}" style="font-size:10px">${rule.enabled ? 'ON' : 'OFF'}</span>
                        </div>
                        <div class="text-xs text-secondary truncate">${this._esc(rule.table || '')}</div>
                    </div>
                    ${this._iconAction(`SettingsPage._deleteAlert('${encodeURIComponent(String(rule.name))}')`, 'fa-trash', 'Delete alert', true)}
                </div>`).join('');
        } catch (e) { el.innerHTML = this._error('Failed to load alerts'); }
    },
    async _deleteAlert(name) {
        name = decodeURIComponent(name);
        if (!confirm(`Delete alert "${name}"?`)) return;
        try {
            const r = await API.deleteAlert(name);
            App.toast(r.success ? 'Alert deleted' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._listAlerts();
        } catch (e) { App.error(e.message); }
    },
    async runAlerts() {
        try {
            const r = await API.runAlerts();
            const triggered = (r.results || []).filter(x => x.triggered);
            App.toast(`${r.count || 0} checked, ${triggered.length} triggered`, triggered.length > 0 ? 'warning' : 'success');
        } catch (e) {}
    },

    // ── Schedules ────────────────────────────────────────────────────
    async addSchedule() {
        const table = document.getElementById('sched-table') ? document.getElementById('sched-table').value : '';
        const freq = document.getElementById('sched-freq') ? document.getElementById('sched-freq').value : 'daily';
        if (!table) return App.toast('Select a table', 'error');
        try {
            const r = await API.addSchedule({ name: table + '_sched', table_name: table, schedule: freq });
            App.toast(r.success ? r.message : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._listSchedules();
        } catch (e) { App.error(e.message); }
    },
    async _listSchedules() {
        const el = document.getElementById('schedules-list');
        if (!el) return;
        el.innerHTML = this._skeletonRows(2);
        try {
            const r = await API.listSchedules();
            const schedules = (r.success && r.schedules) ? r.schedules : [];
            if (!schedules.length) { el.innerHTML = this._empty('No scheduled reports'); return; }
            el.innerHTML = schedules.map(s => `
                <div class="settings-list-item">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-medium text-sm truncate">${this._esc(s.table_name || s.name)}</span>
                            <span class="tag tag-blue" style="font-size:10px">${this._esc(s.schedule || '')}</span>
                            <span class="tag ${s.enabled === false ? 'tag-gray' : 'tag-green'}" style="font-size:10px">${s.enabled === false ? 'Off' : 'On'}</span>
                        </div>
                        <div class="text-xs text-secondary truncate">${s.last_run ? 'Last run ' + this._esc(String(s.last_run).slice(0, 16)) : ''}</div>
                    </div>
                    ${this._iconAction(`SettingsPage._deleteSchedule('${encodeURIComponent(String(s.name))}')`, 'fa-trash', 'Delete schedule', true)}
                </div>`).join('');
        } catch (e) { el.innerHTML = this._error('Failed to load schedules'); }
    },
    async _deleteSchedule(name) {
        name = decodeURIComponent(name);
        if (!confirm(`Delete schedule "${name}"?`)) return;
        try {
            const r = await API.deleteSchedule(name);
            App.toast(r.success ? 'Schedule deleted' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._listSchedules();
        } catch (e) { App.error(e.message); }
    },
    async startScheduler() {
        try {
            const r = await API.startScheduler();
            App.toast(r.success ? r.message : r.error, r.success ? 'success' : 'error');
        } catch (e) { App.error(e.message); }
    },

    // ── Watcher ──────────────────────────────────────────────────────
    async startWatcher() {
        try {
            const r = await API.startWatcher(['data/raw']);
            const el = document.getElementById('watcher-status');
            if (el) el.innerHTML = `<span class="text-green-500"><i class="fas fa-check-circle"></i> ${this._esc(r.message || 'Started')}</span>`;
        } catch (e) { App.error(e.message); }
    },
    async stopWatcher() {
        try {
            const r = await API.stopWatcher();
            const el = document.getElementById('watcher-status');
            if (el) el.innerHTML = `<span class="text-secondary">${this._esc(r.message || 'Stopped')}</span>`;
        } catch (e) { App.error(e.message); }
    },
    async scanWatcher() {
        try {
            const r = await API.scanWatcher();
            const el = document.getElementById('watcher-status');
            if (el) el.innerHTML = `<span><i class="fas fa-search"></i> Changes: ${(r.changes || []).length}</span>`;
            App.toast(`Watcher scan found ${((r.changes) || []).length} change(s)`, (r.changes || []).length ? 'info' : 'success');
        } catch (e) { App.error(e.message); }
    },

    // ── Smart Monitoring ─────────────────────────────────────────────
    async checkSmartAlerts() {
        const table = document.getElementById('smart-table') ? document.getElementById('smart-table').value : '';
        if (!table) return App.toast('Select a table', 'error');
        const rowThreshold = parseInt(document.getElementById('smart-row-threshold') ? document.getElementById('smart-row-threshold').value : '5') || 5;
        const nullThreshold = parseInt(document.getElementById('smart-null-threshold') ? document.getElementById('smart-null-threshold').value : '5') || 5;
        const el = document.getElementById('smart-alerts-result');
        const btn = document.getElementById('smart-check-btn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }
        if (el) el.innerHTML = '<div class="text-xs text-secondary py-2"><i class="fas fa-spinner fa-spin"></i> Scanning...</div>';
        try {
            const result = await API.checkAlerts(table, { row_change_pct: rowThreshold, null_increase_pct: nullThreshold });
            const alerts = result.alerts || [];
            if (el) el.innerHTML = `<div class="settings-result ${alerts.length > 0 ? 'warn' : 'ok'}">
                <div class="text-sm font-medium mb-1">${alerts.length === 0 ? '<i class="fas fa-check-circle mr-1"></i>All clear' : '<i class="fas fa-exclamation-circle mr-1"></i>' + alerts.length + ' alert(s)'}</div>
                ${alerts.map(a => `<div class="text-xs py-0.5"><span style="color:${a.level === 'critical' ? 'var(--color-accent-red)' : 'var(--color-accent-amber)'}">•</span> ${this._esc(a.message)}</div>`).join('')}
            </div>`;
        } catch (e) {
            if (el) el.innerHTML = `<div class="text-xs text-red-400">${this._esc(e.message)}</div>`;
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-shield mr-1"></i>Check'; }
        }
    },
    async checkAllSmartAlerts() {
        const tables = App.state.tables || {};
        const names = Array.isArray(tables) ? tables : Object.keys(tables || {});
        if (!names.length) return App.toast('No tables', 'error');
        const el = document.getElementById('smart-alerts-result');
        let done = 0, total = 0, results = [];
        const render = () => {
            if (!el) return;
            if (done < names.length) {
                el.innerHTML = `<div class="text-xs text-secondary py-2"><i class="fas fa-spinner fa-spin"></i> Checking ${done}/${names.length} tables...</div>
                    <div class="settings-bar"><div class="settings-bar-fill" style="width:${Math.round(done / names.length * 100)}%"></div></div>`;
                return;
            }
            el.innerHTML = `<div class="settings-result ${total > 0 ? 'warn' : 'ok'}">
                <div class="text-sm font-medium mb-1">${total === 0 ? '<i class="fas fa-check-circle mr-1"></i>All ' + names.length + ' tables healthy' : '<i class="fas fa-exclamation-circle mr-1"></i>' + total + ' alert(s) across tables'}</div>
                ${results.map(r => `<div class="text-xs mb-1"><span class="font-semibold">${this._esc(r.table)}:</span> ${this._esc(r.alerts.map(a => a.message).join(', '))}</div>`).join('')}
            </div>`;
        };
        render();
        const batch = async (t) => {
            try {
                const r = await API.checkAlerts(t, { row_change_pct: 5, null_increase_pct: 5 });
                if (r.success && r.alerts && r.alerts.length) { total += r.alerts.length; results.push({ table: t, alerts: r.alerts }); }
            } catch (_) {}
            done++;
            render();
        };
        const CONCURRENCY = 5;
        for (let i = 0; i < names.length; i += CONCURRENCY) {
            await Promise.all(names.slice(i, i + CONCURRENCY).map(batch));
        }
    },

    // ── Enterprise: Notification Preferences ─────────────────────────
    async _loadNotifPrefs() {
        const el = document.getElementById('notif-prefs-content');
        if (!el) return;
        try {
            const res = await API.getNotificationPreferences();
            const prefs = res.preferences || {};
            el.innerHTML = `
            <div class="grid grid-cols-3 gap-3">
                <div class="settings-pref-item">
                    <i class="fas fa-bell" style="color:var(--color-accent-blue)"></i>
                    <div class="flex-1"><div class="text-sm font-medium">In-App</div><div class="text-xs text-secondary">Browser notifications</div></div>
                    <span class="tag ${prefs.enabled !== false ? 'tag-green' : 'tag-gray'}" style="font-size:10px">${prefs.enabled !== false ? 'Active' : 'Off'}</span>
                </div>
                <div class="settings-pref-item">
                    <i class="fas fa-envelope" style="color:var(--color-accent-amber)"></i>
                    <div class="flex-1">
                        <div class="text-sm font-medium">Email Digest</div>
                        <label class="text-xs text-secondary" style="display:flex;align-items:center;gap:6px;cursor:pointer">
                            <input type="checkbox" id="notif-email-toggle" ${prefs.digest ? 'checked' : ''} onchange="SettingsPage._toggleEmailDigest()">
                            Daily summary
                        </label>
                    </div>
                </div>
                <div class="settings-pref-item">
                    <i class="fas fa-globe" style="color:var(--color-accent-purple)"></i>
                    <div class="flex-1"><div class="text-sm font-medium">Webhooks</div><div class="text-xs text-secondary">HTTP callbacks</div></div>
                    <span class="tag tag-gray" style="font-size:10px">Configure</span>
                </div>
            </div>`;
        } catch (e) {
            el.innerHTML = `<div class="settings-error"><i class="fas fa-circle-exclamation"></i><span>Failed to load preferences</span></div>`;
        }
    },
    async _toggleEmailDigest() {
        const enabled = document.getElementById('notif-email-toggle') && document.getElementById('notif-email-toggle').checked;
        try {
            const r = await API.updateNotificationPreferences({ channel: enabled ? 'email' : 'in_app', digest: enabled });
            App.toast(r.success !== false ? (enabled ? 'Email digest enabled' : 'Email digest disabled') : (r.error || 'Failed'), r.success !== false ? 'success' : 'error');
        } catch (e) { App.error(e.message); }
    },

    // ── Enterprise: API Keys ─────────────────────────────────────────
    async _createApiKey() {
        const name = document.getElementById('api-key-name') ? document.getElementById('api-key-name').value.trim() : '';
        const scope = document.getElementById('api-key-scope') ? document.getElementById('api-key-scope').value : 'read';
        if (!name) return App.toast('Enter a key name', 'error');
        try {
            const r = await API.createApiKey(name, [scope]);
            if (r.success) {
                App.toast(`Key created: ${r.api_key}`, 'success');
                document.getElementById('api-key-name').value = '';
                this._listApiKeys();
            } else App.toast(r.error || 'Failed', 'error');
        } catch (e) { App.error(e.message); }
    },
    async _listApiKeys() {
        const el = document.getElementById('api-keys-list');
        if (!el) return;
        el.innerHTML = this._skeletonRows(2);
        try {
            const r = await API.listApiKeys();
            const keys = r.keys || [];
            if (!keys.length) { el.innerHTML = this._empty('No API keys'); return; }
            el.innerHTML = keys.map(k => `
                <div class="settings-list-item">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-medium text-sm">${this._esc(k.name || k.key_id)}</span>
                            <span class="tag tag-gray" style="font-size:10px">${this._esc((k.scopes || []).join(', '))}</span>
                            <span class="tag ${k.revoked ? 'tag-gray' : 'tag-green'}" style="font-size:10px">${k.revoked ? 'Revoked' : 'Active'}</span>
                        </div>
                        <div class="text-xs text-secondary">${k.created_at ? 'Created ' + this._esc(String(k.created_at).slice(0, 10)) : ''}</div>
                    </div>
                    ${k.revoked ? '' : this._iconAction(`SettingsPage._deleteApiKey('${String(k.id)}')`, 'fa-trash', 'Revoke key', true)}
                </div>`).join('');
        } catch (e) { el.innerHTML = this._error('Failed to load API keys'); }
    },
    async _deleteApiKey(id) {
        if (!confirm('Revoke this API key?')) return;
        try {
            const r = await API.deleteApiKey(id);
            App.toast(r.success ? 'Key revoked' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._listApiKeys();
        } catch (e) { App.error(e.message); }
    },

    // ── Enterprise: Catalog Sync ─────────────────────────────────────
    async _syncCatalog() {
        const status = document.getElementById('catalog-sync-status');
        if (status) status.textContent = 'Syncing...';
        try {
            const r = await API.syncCatalog();
            if (status) status.textContent = r.success ? `✓ ${r.result ? this._esc(String((r.result.added || 0) + ' added, ' + (r.result.updated || 0) + ' updated')) : 'Synced'}` : 'Sync failed';
            if (r.success) App.toast('Catalog synced', 'success');
        } catch (e) {
            if (status) status.textContent = 'Error';
            App.toast('Sync failed', 'error');
        }
    },

    // ── Enterprise: SLA ──────────────────────────────────────────────
    async _addSla() {
        const name = document.getElementById('sla-name') ? document.getElementById('sla-name').value.trim() : '';
        const table = document.getElementById('sla-table') ? document.getElementById('sla-table').value : '';
        const hours = parseInt(document.getElementById('sla-hours') ? document.getElementById('sla-hours').value : '24') || 24;
        if (!name || !table) return App.toast('Fill in name and table', 'error');
        try {
            const r = await API.createSlaDefinition({ name, table_name: table, max_age_hours: hours });
            App.toast(r.success ? 'SLA added' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) { document.getElementById('sla-name').value = ''; this._loadSlaList(); }
        } catch (e) { App.error(e.message); }
    },
    async _loadSlaList() {
        const el = document.getElementById('sla-list');
        if (!el) return;
        el.innerHTML = this._skeletonRows(2);
        try {
            const r = await API.slaDefinitions();
            const defs = r.definitions || [];
            if (!defs.length) { el.innerHTML = this._empty('No SLA definitions'); return; }
            el.innerHTML = defs.map(d => `
                <div class="settings-list-item">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-medium text-sm">${this._esc(d.name)}</span>
                            <span class="text-xs text-secondary">${this._esc(d.table_name || '')} · ${d.max_age_hours}h</span>
                        </div>
                    </div>
                    <button onclick="SettingsPage._toggleSla('${String(d.id)}', ${d.enabled !== false})" class="btn btn-sm btn-ghost" title="Toggle"><i class="fas ${d.enabled !== false ? 'fa-toggle-on' : 'fa-toggle-off'}" style="color:var(--color-accent-green)"></i></button>
                    ${this._iconAction(`SettingsPage._deleteSla('${String(d.id)}')`, 'fa-trash', 'Delete SLA', true)}
                </div>`).join('');
        } catch (e) { el.innerHTML = this._error('Failed to load SLA definitions'); }
    },
    async _toggleSla(id, enabled) {
        try {
            const r = await API.updateSlaDefinition(id, { enabled: !enabled });
            App.toast(r.success ? 'SLA updated' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._loadSlaList();
        } catch (e) { App.error(e.message); }
    },
    async _deleteSla(id) {
        if (!confirm('Delete this SLA definition?')) return;
        try {
            const r = await API.deleteSlaDefinition(id);
            App.toast(r.success ? 'SLA deleted' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._loadSlaList();
        } catch (e) { App.error(e.message); }
    },
    async _startSlaMonitor() {
        const interval = parseInt(document.getElementById('sla-interval') ? document.getElementById('sla-interval').value : '60') || 60;
        try {
            const r = await API.startSlaMonitor(interval);
            App.toast(r.message || 'Monitor started', 'success');
        } catch (e) { App.error(e.message); }
    },

    // ── Enterprise: Budgets ──────────────────────────────────────────
    async _addBudget() {
        const name = document.getElementById('budget-name') ? document.getElementById('budget-name').value.trim() : '';
        const limit = parseFloat(document.getElementById('budget-limit') ? document.getElementById('budget-limit').value : '100') || 100;
        const period = document.getElementById('budget-period') ? document.getElementById('budget-period').value : 'monthly';
        if (!name) return App.toast('Enter a budget name', 'error');
        try {
            const r = await API.createBudget({ name, limit_amount: limit, period });
            App.toast(r.success ? 'Budget created' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) { document.getElementById('budget-name').value = ''; this._loadBudgetList(); }
        } catch (e) { App.error(e.message); }
    },
    async _loadBudgetList() {
        const el = document.getElementById('budget-list');
        if (!el) return;
        el.innerHTML = this._skeletonRows(2);
        try {
            const r = await API.budgetStatus();
            const budgets = r.status || [];
            if (!budgets.length) { el.innerHTML = this._empty('No budgets'); return; }
            el.innerHTML = budgets.map(b => {
                const pct = b.spent_pct || 0;
                const color = pct >= 100 ? 'var(--color-accent-red)' : pct >= (b.alert_threshold_pct || 80) ? 'var(--color-accent-amber)' : 'var(--color-accent-green)';
                return `<div class="settings-budget">
                    <div class="flex justify-between text-xs mb-1">
                        <span class="font-medium">${this._esc(b.name)}</span>
                        <span>$${this._esc(Number(b.spent_amount || 0).toFixed(0))} / $${this._esc(Number(b.limit_amount || 0).toFixed(0))}</span>
                    </div>
                    <div class="settings-bar"><div class="settings-bar-fill" style="width:${Math.min(pct, 100)}%;background:${color}"></div></div>
                    <div class="flex items-center justify-between mt-1">
                        <span class="text-[10px]" style="color:${color};font-size:10px">${pct.toFixed(1)}% used</span>
                        ${this._iconAction(`SettingsPage._deleteBudget('${String(b.budget_id || b.id)}')`, 'fa-trash', 'Delete budget', true)}
                    </div>
                </div>`;
            }).join('');
        } catch (e) { el.innerHTML = this._error('Failed to load budgets'); }
    },
    async _deleteBudget(id) {
        if (!confirm('Delete this budget?')) return;
        try {
            const r = await API.deleteBudget(id);
            App.toast(r.success ? 'Budget deleted' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._loadBudgetList();
        } catch (e) { App.error(e.message); }
    },

    // ── Governance: Masking ──────────────────────────────────────────
    async _addMaskingRule() {
        const table = document.getElementById('mask-table') ? document.getElementById('mask-table').value : '';
        const column = document.getElementById('mask-column') ? document.getElementById('mask-column').value.trim() : '';
        const strategy = document.getElementById('mask-strategy') ? document.getElementById('mask-strategy').value : 'null';
        if (!table || !column) return App.toast('Fill in table and column', 'error');
        try {
            const r = await API.createMaskingRule({ table_name: table, column_name: column, strategy });
            App.toast(r.success ? 'Masking rule added' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) { document.getElementById('mask-column').value = ''; this._loadMaskingRules(); }
        } catch (e) { App.error(e.message); }
    },
    async _loadMaskingRules() {
        const el = document.getElementById('masking-rules-list');
        if (!el) return;
        el.innerHTML = this._skeletonRows(2);
        try {
            const r = await API.maskingRules();
            const rules = r.rules || [];
            if (!rules.length) { el.innerHTML = this._empty('No masking rules'); return; }
            el.innerHTML = rules.map(rl => `
                <div class="settings-list-item">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-medium text-sm truncate">${this._esc(rl.table_name)}.${this._esc(rl.column_name)}</span>
                            <span class="tag tag-blue" style="font-size:10px">${this._esc(rl.strategy)}</span>
                        </div>
                    </div>
                    <button onclick="SettingsPage._toggleMasking('${String(rl.id)}', ${rl.enabled !== false})" class="btn btn-sm btn-ghost" title="Toggle"><i class="fas ${rl.enabled !== false ? 'fa-toggle-on' : 'fa-toggle-off'}" style="color:var(--color-accent-green)"></i></button>
                    ${this._iconAction(`SettingsPage._deleteMasking('${String(rl.id)}')`, 'fa-trash', 'Delete rule', true)}
                </div>`).join('');
        } catch (e) { el.innerHTML = this._error('Failed to load masking rules'); }
    },
    async _toggleMasking(id, enabled) {
        try {
            const r = await API.updateMaskingRule(id, { enabled: !enabled });
            App.toast(r.success ? 'Rule updated' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._loadMaskingRules();
        } catch (e) { App.error(e.message); }
    },
    async _deleteMasking(id) {
        if (!confirm('Delete this masking rule?')) return;
        try {
            const r = await API.deleteMaskingRule(id);
            App.toast(r.success ? 'Rule deleted' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._loadMaskingRules();
        } catch (e) { App.error(e.message); }
    },
    async _scanPii() {
        App.toast('Scanning tables for PII...', 'info');
        try {
            const r = await API.scanPiiInventory();
            App.toast(`Found ${r.count || 0} PII columns`, 'success');
            this._loadPiiInventory();
        } catch (e) { App.error(e.message); }
    },

    // ── Governance: Retention ────────────────────────────────────────
    async _addRetentionPolicy() {
        const table = document.getElementById('ret-table') ? document.getElementById('ret-table').value : '';
        const days = parseInt(document.getElementById('ret-days') ? document.getElementById('ret-days').value : '365') || 365;
        const action = document.getElementById('ret-action') ? document.getElementById('ret-action').value : 'delete';
        if (!table) return App.toast('Select a table', 'error');
        try {
            const r = await API.createRetentionPolicy({ table_name: table, retention_days: days, action });
            App.toast(r.success ? 'Retention policy added' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._loadRetentionPolicies();
        } catch (e) { App.error(e.message); }
    },
    async _loadRetentionPolicies() {
        const el = document.getElementById('retention-list');
        if (!el) return;
        el.innerHTML = this._skeletonRows(2);
        try {
            const r = await API.listRetentionPolicies();
            const policies = r.policies || [];
            if (!policies.length) { el.innerHTML = this._empty('No retention policies'); return; }
            el.innerHTML = policies.map(p => `
                <div class="settings-list-item">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-medium text-sm truncate">${this._esc(p.table_name)}</span>
                            <span class="text-xs text-secondary">${p.retention_days}d · ${this._esc(p.action)}</span>
                        </div>
                    </div>
                    <button onclick="SettingsPage._toggleRetention('${String(p.id)}', ${p.enabled !== false})" class="btn btn-sm btn-ghost" title="Toggle"><i class="fas ${p.enabled !== false ? 'fa-toggle-on' : 'fa-toggle-off'}" style="color:var(--color-accent-green)"></i></button>
                    ${this._iconAction(`SettingsPage._deleteRetention('${String(p.id)}')`, 'fa-trash', 'Delete policy', true)}
                </div>`).join('');
        } catch (e) { el.innerHTML = this._error('Failed to load retention policies'); }
    },
    async _toggleRetention(id, enabled) {
        try {
            const r = await API.updateRetentionPolicy(id, { enabled: !enabled });
            App.toast(r.success ? 'Policy updated' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._loadRetentionPolicies();
        } catch (e) { App.error(e.message); }
    },
    async _deleteRetention(id) {
        if (!confirm('Delete this retention policy?')) return;
        try {
            const r = await API.deleteRetentionPolicy(id);
            App.toast(r.success ? 'Policy deleted' : (r.error || 'Failed'), r.success ? 'success' : 'error');
            if (r.success) this._loadRetentionPolicies();
        } catch (e) { App.error(e.message); }
    },
    async _runRetention() {
        if (!confirm('Execute all retention policies? This will delete/anonymize data!')) return;
        App.toast('Running retention...', 'info');
        try {
            const r = await API.executeRetention();
            App.toast(`Retention complete: ${(r.result && r.result.policies_executed) || 0} policies`, 'success');
        } catch (e) { App.error(e.message); }
    },

    // ── Governance: PII / Privacy / Compliance ───────────────────────
    async _loadPiiInventory() {
        const el = document.getElementById('pii-inventory-list');
        if (!el) return;
        try {
            const r = await API.piiInventory();
            const items = r.inventory || [];
            if (!items.length) { el.innerHTML = this._empty('No PII columns detected. Run a scan.'); return; }
            el.innerHTML = `<div class="overflow-x-auto"><table class="data-table">
                <thead><tr><th>Table</th><th>Column</th><th>PII Type</th><th>Confidence</th></tr></thead><tbody>
                ${items.map(i => `<tr>
                    <td>${this._esc(i.table_name)}</td><td>${this._esc(i.column_name)}</td>
                    <td><span class="tag ${i.pii_type === 'email' || i.pii_type === 'ssn' ? 'tag-red' : 'tag-amber'}" style="font-size:10px">${this._esc(i.pii_type)}</span></td>
                    <td>${this._esc(i.confidence)}</td>
                </tr>`).join('')}
                </tbody></table></div>`;
        } catch (e) { el.innerHTML = this._error('Failed to load PII inventory'); }
    },
    async _loadPrivacyRequests() {
        const el = document.getElementById('privacy-requests-list');
        if (!el) return;
        try {
            const r = await API.listPrivacyRequests();
            const reqs = r.requests || [];
            if (!reqs.length) { el.innerHTML = this._empty('No privacy requests'); return; }
            el.innerHTML = reqs.slice(0, 10).map(rq => `
                <div class="settings-list-item">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-medium text-sm">${this._esc(rq.request_type)}</span>
                            <span class="text-xs text-secondary truncate">${this._esc(rq.subject_email || '')}</span>
                        </div>
                    </div>
                    <span class="tag ${rq.status === 'completed' ? 'tag-green' : rq.status === 'rejected' ? 'tag-red' : 'tag-amber'}" style="font-size:10px">${this._esc(rq.status)}</span>
                </div>`).join('');
        } catch (e) { el.innerHTML = this._error('Failed to load privacy requests'); }
    },
    async _loadComplianceReport() {
        const el = document.getElementById('compliance-report');
        if (!el) return;
        try {
            const r = await API.complianceReport();
            const rep = r.report || {};
            const cells = [
                { v: (rep.privacy_requests && rep.privacy_requests.total) || 0, l: 'Privacy Requests', c: 'var(--color-accent-blue)' },
                { v: (rep.retention_policies && rep.retention_policies.active) || 0, l: 'Active Retention', c: 'var(--color-accent-green)' },
                { v: (rep.consent_records && rep.consent_records.active) || 0, l: 'Active Consents', c: 'var(--color-accent-purple)' },
                { v: rep.pii_inventory_count || 0, l: 'PII Columns', c: 'var(--color-accent-amber)' },
            ];
            el.innerHTML = `<div class="grid grid-cols-4 gap-3 text-sm">` + cells.map(c => `
                <div class="card text-center" style="padding:14px">
                    <div class="text-2xl font-bold" style="color:${c.c}">${c.v}</div>
                    <div class="text-xs text-secondary mt-1">${c.l}</div>
                </div>`).join('') + `</div>`;
        } catch (e) { el.innerHTML = this._error('Failed to load compliance report'); }
    },
};

// Register with SPA router
window.SettingsPage = SettingsPage;
(window._PAGES = window._PAGES || {})["#settings"] = SettingsPage;
