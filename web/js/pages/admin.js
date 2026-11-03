/* ═══════════════════════════════════════════════════════════════════
   Admin Dashboard — Users & Roles, System Health, SLA, Budget,
   Compliance & Pipeline Health
   ═══════════════════════════════════════════════════════════════════ */

const AdminPage = {
    async init() {},
    async destroy() {},
    async render() {
        const pc = document.getElementById('page-content');
        // Enterprise entitlement pre-check: the SLA/cost/compliance APIs are
        // premium-gated, so on the free plan render upgrade placeholders
        // instead of firing doomed requests (avoids 403 console noise).
        const entitled = !window.RBAC || RBAC.hasFeature('enterprise');
        // Account management is not a premium feature: an administrator gets
        // it whatever the plan is.
        const isAdmin = !!(window.RBAC && RBAC.requireRole('admin'));
        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
            <div class="page-header">
                <div>
                    <h2><i class="fas fa-gauge-high mr-2 text-indigo-500"></i>Admin Dashboard</h2>
                    <p class="text-secondary mt-1">System health, users &amp; roles, SLA compliance, budgets, and compliance overview</p>
                </div>
                <div class="page-actions">
                    <button onclick="AdminPage._refreshAll()" class="btn btn-ghost btn-sm"><i class="fas fa-sync"></i> Refresh</button>
                </div>
            </div>

            <!-- System health (per component) -->
            <div class="card">
                <div class="card-header"><h3><i class="fas fa-heart-pulse mr-2 text-rose-500"></i>System health</h3></div>
                <div id="admin-health"><div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div></div>
            </div>

            ${isAdmin ? '<div id="admin-users-section"></div>' : ''}
            ${isAdmin ? '<div id="admin-tenants-section"></div>' : ''}

            ${entitled ? this._enterpriseGrid() : this._upsellGrid()}
        </div>`;

        await this._loadHealthStrip();
        if (isAdmin && window.AdminUsers) await window.AdminUsers.render('admin-users-section');
        if (isAdmin && window.AdminTenants) await window.AdminTenants.render('admin-tenants-section');
        if (entitled) await this._refreshEnterprise();
    },

    _upsellGrid() {
        const upsell = (title, icon) => `
            <div class="card text-center enterprise-upsell">
                <i class="fas ${icon} text-2xl enterprise-upsell-icon"></i>
                <h3 class="mt-2 font-semibold">${title}</h3>
                <p class="text-sm text-secondary mt-1">Available on the Pro plan — SLA tracking, budgets, and compliance reporting.</p>
                <button onclick="RBAC.showUpgradeOverlay('enterprise')" class="btn btn-primary btn-sm mt-3"><i class="fas fa-crown mr-1"></i>Upgrade to Pro</button>
            </div>`;
        return `<div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            ${upsell('SLA Compliance', 'fa-clock')}
            ${upsell('Budget & Cost Tracking', 'fa-coins')}
            ${upsell('Compliance Status', 'fa-shield')}
            ${upsell('Pipeline Health', 'fa-diagram-project')}
        </div>`;
    },

    _enterpriseGrid() {
        return `
            <!-- Summary Cards -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4" id="admin-summary">
                <div class="card text-center"><i class="fas fa-spinner fa-spin text-xl"></i></div>
            </div>

            <!-- Content Grid -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- SLA Status -->
                <div class="card">
                    <div class="card-header"><h3><i class="fas fa-clock mr-2 text-amber-500"></i>SLA Compliance</h3></div>
                    <div id="admin-sla"><div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div></div>
                </div>

                <!-- Budget Status -->
                <div class="card">
                    <div class="card-header"><h3><i class="fas fa-coins mr-2 text-emerald-500"></i>Budget Status</h3></div>
                    <div id="admin-budget"><div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div></div>
                </div>

                <!-- Cost Summary -->
                <div class="card">
                    <div class="card-header"><h3><i class="fas fa-chart-line mr-2 text-blue-500"></i>Usage Summary</h3></div>
                    <div id="admin-cost"><div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div></div>
                </div>

                <!-- Compliance -->
                <div class="card">
                    <div class="card-header"><h3><i class="fas fa-shield mr-2 text-purple-500"></i>Compliance Status</h3></div>
                    <div id="admin-compliance"><div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div></div>
                </div>

                <!-- Pipeline Health -->
                <div class="card lg:col-span-2">
                    <div class="card-header"><h3><i class="fas fa-diagram-project mr-2 text-cyan-500"></i>Pipeline Health</h3></div>
                    <div id="admin-pipeline"><div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div></div>
                </div>
            </div>`;
    },

    async _refreshAll() {
        await this._loadHealthStrip();
        if (window.AdminUsers && document.getElementById('admin-users-section')) {
            await window.AdminUsers.load();
        }
        await this._refreshEnterprise();
    },

    async _refreshEnterprise() {
        await Promise.all([
            this._loadSLA(),
            this._loadBudget(),
            this._loadCost(),
            this._loadCompliance(),
            this._loadPipeline(),
            this._loadSummary(),
        ]);
    },

    /**
     * The deep health check: one row per component, so an operator can see
     * *which* dependency is unhappy. Reachable for administrators only.
     */
    async _loadHealthStrip() {
        const host = document.getElementById('admin-health');
        if (!host) return;
        try {
            const res = await API.systemHealth();
            const components = (res && res.components) || {};
            const names = Object.keys(components);
            if (!names.length) {
                host.innerHTML = '<div class="text-center py-3 text-secondary text-sm">No component detail reported.</div>';
                return;
            }
            const status = (res && res.status) || 'unknown';
            const tone = AdminPage._healthTone(status);
            // The build reports the version per component; fall back to the
            // top-level one, and never print a bare "?" when it is absent.
            const api = components.api || {};
            const version = (res && res.version) || api.version || '';
            const checked = (res && res.checked_at) || '';
            const meta = [
                version ? `version ${App.escapeHtml(version)}` : '',
                checked ? `checked ${App.escapeHtml(checked)}` : ''
            ].filter(Boolean).join(' &middot; ');
            host.innerHTML = `
                <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
                    <span class="tag tag-${tone}">${App.escapeHtml(status)}</span>
                    ${meta ? `<span class="text-xs text-secondary">${meta}</span>` : ''}
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                    ${names.map(name => {
                        const c = components[name] || {};
                        const ok = c.ok !== false && AdminPage._healthTone(c.status || 'healthy') !== 'red';
                        const detail = c.detail || c.status || (ok ? 'ok' : 'unavailable');
                        return `<div class="flex items-start gap-2 p-2 rounded-lg" style="border:1px solid var(--color-card-border)">
                            <i class="fas fa-${ok ? 'circle-check text-green-500' : 'circle-xmark text-red-500'} mt-0.5"></i>
                            <div class="min-w-0">
                                <div class="text-xs font-semibold truncate">${App.escapeHtml(name)}</div>
                                <div class="text-[11px] text-secondary truncate" title="${App.escapeHtml(detail)}">${App.escapeHtml(detail)}</div>
                            </div>
                        </div>`;
                    }).join('')}
                </div>`;
        } catch (e) {
            host.innerHTML = '<div class="text-center py-3 text-secondary text-sm">The deep health check is not available for this account.</div>';
        }
    },

    /** Map a health word onto the tag colour. */
    _healthTone(status) {
        const s = String(status || '').toLowerCase();
        if (['healthy', 'ok', 'active', 'available', 'configured', 'running', 'ready', 'connected'].includes(s)) return 'green';
        if (['degraded', 'warning', 'partial', 'stale', 'starting'].includes(s)) return 'amber';
        return 'red';
    },

    async _loadSummary() {
        const container = document.getElementById('admin-summary');
        if (!container) return;
        try {
            const [slaRes, costRes, compRes] = await Promise.allSettled([
                API.slaStatus(), API.costSummary(), API.complianceReport()
            ]);
            const sla = slaRes.status === 'fulfilled' ? slaRes.value?.status : null;
            const cost = costRes.status === 'fulfilled' ? costRes.value?.summary : null;
            const comp = compRes.status === 'fulfilled' ? compRes.value?.report : null;

            const slaCompliant = sla?.compliant_pct || 0;
            const totalCost = cost?.total_cost || 0;
            const pendingReq = comp?.privacy_requests?.pending || 0;
            const piiCount = comp?.pii_inventory_count || 0;

            container.innerHTML = `
                <div class="card text-center"><div class="text-2xl font-bold text-${slaCompliant >= 80 ? 'green' : slaCompliant >= 50 ? 'amber' : 'red'}-500">${slaCompliant}%</div><div class="text-xs text-secondary">SLA Compliant</div></div>
                <div class="card text-center"><div class="text-2xl font-bold text-blue-500">$${totalCost.toFixed(4)}</div><div class="text-xs text-secondary">Total Cost</div></div>
                <div class="card text-center"><div class="text-2xl font-bold text-${pendingReq > 0 ? 'amber' : 'green'}-500">${pendingReq}</div><div class="text-xs text-secondary">Pending Requests</div></div>
                <div class="card text-center"><div class="text-2xl font-bold text-purple-500">${piiCount}</div><div class="text-xs text-secondary">PII Columns</div></div>
            `;
        } catch (e) {
            container.innerHTML = '<div class="col-span-4 text-center text-secondary text-sm">Failed to load summary</div>';
        }
    },

    async _loadSLA() {
        const container = document.getElementById('admin-sla');
        if (!container) return;
        try {
            const res = await API.slaStatus();
            const sla = res.status || {};
            const results = sla.results || [];
            if (results.length === 0) {
                container.innerHTML = '<div class="text-center py-4 text-secondary text-sm">No SLA definitions configured</div>';
                return;
            }
            container.innerHTML = results.map(r => `
                <div class="flex items-center justify-between py-1.5 text-sm">
                    <span>${App.escapeHtml(r.name || r.table)}</span>
                    <span class="tag tag-${r.status === 'compliant' ? 'green' : r.status === 'warning' ? 'amber' : 'red'} text-[10px]">${r.status}</span>
                </div>
            `).join('') + `<div class="text-right mt-2"><a href="#settings" class="text-xs text-primary hover:underline" onclick="App.navigate('#settings')">Manage SLAs →</a></div>`;
        } catch (e) { container.innerHTML = '<div class="text-center py-4 text-secondary text-sm">Failed to load</div>'; }
    },

    async _loadBudget() {
        const container = document.getElementById('admin-budget');
        if (!container) return;
        try {
            const res = await API.budgetStatus();
            const budgets = res.status || [];
            if (budgets.length === 0) {
                container.innerHTML = '<div class="text-center py-4 text-secondary text-sm">No budgets configured</div>';
                return;
            }
            container.innerHTML = budgets.slice(0, 5).map(b => {
                const pct = b.spent_pct || 0;
                const color = pct >= 100 ? 'bg-red-500' : pct >= (b.alert_threshold_pct || 80) ? 'bg-amber-500' : 'bg-green-500';
                return `<div class="mb-2">
                    <div class="flex justify-between text-xs"><span>${App.escapeHtml(b.name)}</span><span>$${b.spent_amount?.toFixed(0) || 0} / $${(b.limit_amount || 0).toFixed(0)}</span></div>
                    <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5"><div class="${color} h-1.5 rounded-full" style="width:${Math.min(pct, 100)}%"></div></div>
                </div>`;
            }).join('');
        } catch (e) { container.innerHTML = '<div class="text-center py-4 text-secondary text-sm">Failed to load</div>'; }
    },

    async _loadCost() {
        const container = document.getElementById('admin-cost');
        if (!container) return;
        try {
            const res = await API.costSummary();
            const s = res.summary || {};
            container.innerHTML = `
                <div class="grid grid-cols-2 gap-3 text-sm">
                    <div><span class="text-secondary">Total Queries:</span><br><strong>${(s.total_queries || 0).toLocaleString()}</strong></div>
                    <div><span class="text-secondary">Avg Duration:</span><br><strong>${(s.avg_duration_ms || 0).toFixed(1)}ms</strong></div>
                    <div><span class="text-secondary">Today's Cost:</span><br><strong>$${(s.today_cost || 0).toFixed(4)}</strong></div>
                    <div><span class="text-secondary">Rows Scanned:</span><br><strong>${(s.total_rows_scanned || 0).toLocaleString()}</strong></div>
                </div>
                ${(s.top_users || []).length > 0 ? `<div class="mt-3 text-xs"><strong class="text-secondary">Top Users:</strong>${s.top_users.slice(0, 3).map(u => `<div class="flex justify-between py-0.5"><span>${App.escapeHtml(u.user_id)}</span><span>$${u.total_cost?.toFixed(4)}</span></div>`).join('')}</div>` : ''}
            `;
        } catch (e) { container.innerHTML = '<div class="text-center py-4 text-secondary text-sm">Failed to load</div>'; }
    },

    async _loadCompliance() {
        const container = document.getElementById('admin-compliance');
        if (!container) return;
        try {
            const res = await API.complianceReport();
            const r = res.report || {};
            container.innerHTML = `
                <div class="grid grid-cols-2 gap-3 text-sm">
                    <div><span class="text-secondary">Privacy Requests:</span><br><strong>${r.privacy_requests?.total || 0}</strong> (${r.privacy_requests?.pending || 0} pending)</div>
                    <div><span class="text-secondary">Retention Policies:</span><br><strong>${r.retention_policies?.active || 0}</strong> active</div>
                    <div><span class="text-secondary">Consent Records:</span><br><strong>${r.consent_records?.active || 0}</strong> active</div>
                    <div><span class="text-secondary">PII Columns:</span><br><strong>${r.pii_inventory_count || 0}</strong></div>
                </div>
                <div class="mt-3 text-right"><a href="#compliance" class="text-xs text-primary hover:underline">Full report →</a></div>
            `;
        } catch (e) { container.innerHTML = '<div class="text-center py-4 text-secondary text-sm">Failed to load</div>'; }
    },

    async _loadPipeline() {
        const container = document.getElementById('admin-pipeline');
        if (!container) return;
        try {
            const res = await API.pipelineHealth();
            const health = res.health || [];
            if (health.length === 0) {
                container.innerHTML = '<div class="text-center py-4 text-secondary text-sm">No pipeline runs recorded yet</div>';
                return;
            }
            container.innerHTML = `<div class="overflow-x-auto"><table class="data-table">
                <tr><th>Pipeline</th><th>Runs</th><th>Success Rate</th><th>Last Run</th></tr>
                ${health.map(h => {
                    const rate = h.total_runs > 0 ? Math.round((h.success / h.total_runs) * 100) : 0;
                    const statusColor = rate >= 90 ? 'text-green-500' : rate >= 70 ? 'text-amber-500' : 'text-red-500';
                    return `<tr><td class="font-medium">${App.escapeHtml(h.pipeline_name)}</td>
                        <td>${h.total_runs}</td>
                        <td class="${statusColor} font-medium">${rate}%</td>
                        <td class="text-secondary">${h.last_run ? new Date(h.last_run).toLocaleDateString() : '—'}</td></tr>`;
                }).join('')}
            </table></div>`;
        } catch (e) { container.innerHTML = '<div class="text-center py-4 text-secondary text-sm">Failed to load</div>'; }
    },
};

window.AdminPage = AdminPage;
(window._PAGES = window._PAGES || {})["#admin"] = AdminPage;
