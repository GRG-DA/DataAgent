/* ═══════════════════════════════════════════
   Approval Workflows Page
   ═══════════════════════════════════════════ */

const WorkflowsPage = {
    async init() {},
    async destroy() {},

    async render() {
        const pc = document.getElementById('page-content');
        // Automation entitlement pre-check: workflow APIs are premium-gated,
        // so on the free plan show an upsell instead of doomed 403 fetches.
        if (window.RBAC && !RBAC.hasFeature('automation')) {
            pc.innerHTML = `
            <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
                <div class="page-header">
                    <div>
                        <h2><i class="fas fa-diagram-project mr-2 text-indigo-500"></i>Approval Workflows</h2>
                        <p class="text-secondary mt-1">Manage data access requests, publishing approvals, and certification workflows</p>
                    </div>
                </div>
                <div class="card text-center enterprise-upsell">
                    <i class="fas fa-diagram-project text-2xl enterprise-upsell-icon"></i>
                    <h3 class="mt-2 font-semibold">Approval Workflows are a Pro feature</h3>
                    <p class="text-sm text-secondary mt-1">Automate data access requests, publishing approvals, and certification with multi-step review chains.</p>
                    <button onclick="RBAC.showUpgradeOverlay('automation')" class="btn btn-primary btn-sm mt-3"><i class="fas fa-crown mr-1"></i>Upgrade to Pro</button>
                </div>
            </div>`;
            return;
        }
        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
            <div class="page-header">
                <div>
                    <h2><i class="fas fa-diagram-project mr-2 text-indigo-500"></i>Approval Workflows</h2>
                    <p class="text-secondary mt-1">Manage data access requests, publishing approvals, and certification workflows</p>
                </div>
                <div class="page-actions">
                    <button onclick="WorkflowsPage._showStartWorkflow()" class="btn btn-primary btn-sm">
                        <i class="fas fa-play"></i> New Request
                    </button>
                </div>
            </div>

            <!-- Summary Cards -->
            <div class="grid grid-cols-3 gap-4">
                <div class="card text-center">
                    <div class="text-2xl font-bold text-primary" id="wf-active-count">0</div>
                    <div class="text-xs text-secondary mt-1">Active</div>
                </div>
                <div class="card text-center">
                    <div class="text-2xl font-bold text-amber-500" id="wf-pending-count">0</div>
                    <div class="text-xs text-secondary mt-1">Pending Approval</div>
                </div>
                <div class="card text-center">
                    <div class="text-2xl font-bold text-green-500" id="wf-approved-count">0</div>
                    <div class="text-xs text-secondary mt-1">Approved</div>
                </div>
            </div>

            <!-- Tabs -->
            <div class="flex gap-2 border-b dark:border-gray-700">
                <button class="wf-tab active px-4 py-2 text-sm font-medium border-b-2 border-primary text-primary" onclick="WorkflowsPage._switchTab('active')">Active</button>
                <button class="wf-tab px-4 py-2 text-sm font-medium text-secondary" onclick="WorkflowsPage._switchTab('approved')">Approved</button>
                <button class="wf-tab px-4 py-2 text-sm font-medium text-secondary" onclick="WorkflowsPage._switchTab('rejected')">Rejected</button>
            </div>

            <div id="wf-content" class="space-y-3">
                <div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>
            </div>
        </div>`;
        await this._loadActive();
    },

    _switchTab(tab) {
        document.querySelectorAll('.wf-tab').forEach(t => {
            t.classList.remove('active', 'border-primary', 'text-primary');
            t.classList.add('text-secondary');
        });
        const active = document.querySelector(`.wf-tab:nth-child(${tab === 'active' ? 1 : tab === 'approved' ? 2 : 3})`);
        if (active) {
            active.classList.add('active', 'border-primary', 'text-primary');
            active.classList.remove('text-secondary');
        }
        if (tab === 'active') this._loadActive();
        else if (tab === 'approved') this._loadByStatus('approved');
        else this._loadByStatus('rejected');
    },

    async _loadActive() {
        const container = document.getElementById('wf-content');
        if (!container) return;
        try {
            const res = await API.workflowInstances('', 'active');
            const countRes = await API.workflowInstances('', 'approved');
            const pendingRes = await API.pendingApprovals('current', '');
            this._updateCounts(res.total || 0, pendingRes.pending?.length || 0, countRes.total || 0);
            this._renderInstances(container, res.instances || [], 'active');
        } catch (e) {
            container.innerHTML = '<div class="empty-state"><p>Failed to load</p></div>';
        }
    },

    async _loadByStatus(status) {
        const container = document.getElementById('wf-content');
        if (!container) return;
        try {
            const res = await API.workflowInstances('', status);
            this._renderInstances(container, res.instances || [], status);
        } catch (e) {
            container.innerHTML = '<div class="empty-state"><p>Failed to load</p></div>';
        }
    },

    _updateCounts(active, pending, approved) {
        const a = document.getElementById('wf-active-count');
        const p = document.getElementById('wf-pending-count');
        const ap = document.getElementById('wf-approved-count');
        if (a) a.textContent = active;
        if (p) p.textContent = pending;
        if (ap) ap.textContent = approved;
    },

    _renderInstances(container, instances, status) {
        if (instances.length === 0) {
            container.innerHTML = `<div class="empty-state py-12"><i class="fas fa-inbox text-4xl mb-3 opacity-30"></i><h3>No ${status} workflows</h3><p>Start a new request to begin.</p></div>`;
            return;
        }

        container.innerHTML = instances.map(inst => `
        <div class="card hover:shadow-md cursor-pointer transition-shadow" onclick="WorkflowsPage._showDetail('${inst.id}')">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-3">
                    <i class="fas fa-${inst.status === 'approved' ? 'check-circle text-green-500' : inst.status === 'rejected' ? 'times-circle text-red-500' : 'clock text-amber-500'}"></i>
                    <div>
                        <strong class="text-sm">${App.escapeHtml(inst.workflow_name || 'Workflow')}</strong>
                        <div class="text-xs text-secondary">
                            Step ${(inst.current_step || 0) + 1}/${inst.total_steps || 1} · ${App.escapeHtml(inst.initiator_name || inst.initiator_id || '')}
                        </div>
                    </div>
                </div>
                <span class="tag tag-${inst.status === 'approved' ? 'green' : inst.status === 'rejected' ? 'red' : 'amber'}">${inst.status}</span>
            </div>
        </div>`).join('');
    },

    async _showDetail(instanceId) {
        try {
            const res = await API.getWorkflowInstance(instanceId);
            if (!res.success) { App.toast('Instance not found', 'error'); return; }
            const inst = res.instance || {};
            const actions = inst.actions || [];

            let actionsHtml = actions.length > 0 ? actions.map(a => `
                <div class="flex items-center gap-2 py-1 text-sm">
                    <i class="fas fa-${a.action === 'approve' ? 'check-circle text-green-500' : a.action === 'reject' ? 'times-circle text-red-500' : 'arrow-right text-blue-500'}"></i>
                    <strong>${App.escapeHtml(a.actor_name || a.actor_id)}</strong> ${a.action}
                    ${a.comment ? `: "${App.escapeHtml(a.comment)}"` : ''}
                </div>
            `).join('') : '<div class="text-secondary text-sm">No actions yet</div>';

            App.showModal(`Workflow: ${App.escapeHtml(inst.workflow_name || '')}`, `
                <div class="space-y-3 text-sm">
                    <div><strong>Status:</strong> <span class="tag tag-${inst.status === 'approved' ? 'green' : inst.status === 'rejected' ? 'red' : 'amber'}">${inst.status}</span></div>
                    <div><strong>Initiator:</strong> ${App.escapeHtml(inst.initiator_name || inst.initiator_id || '')}</div>
                    <div><strong>Progress:</strong> Step ${(inst.current_step || 0) + 1} of ${inst.total_steps || 1}</div>
                    <div class="border-t dark:border-gray-700 pt-2">
                        <strong>Actions:</strong>
                        <div class="mt-1">${actionsHtml}</div>
                    </div>
                    ${inst.status === 'active' ? `
                    <div class="border-t dark:border-gray-700 pt-3 flex gap-2">
                        <button onclick="WorkflowsPage._approve('${instanceId}')" class="btn btn-sm btn-primary"><i class="fas fa-check"></i> Approve</button>
                        <button onclick="WorkflowsPage._reject('${instanceId}')" class="btn btn-sm btn-danger"><i class="fas fa-times"></i> Reject</button>
                    </div>` : ''}
                </div>
            `, null, 'Close');
        } catch (e) { App.toast('Failed to load detail', 'error'); }
    },

    async _approve(instanceId) {
        const res = await API.approveWorkflowStep(instanceId, { actor_id: 'current', actor_name: 'User', comment: 'Approved' });
        if (!res || res.success === false) {
            App.toast((res && res.error) || 'Failed to approve', 'error');
            return;
        }
        App.toast('Workflow approved', 'success');
        App.closeModal();
        this._loadActive();
    },

    async _reject(instanceId) {
        const reason = prompt('Reason for rejection:') || 'No reason given';
        const res = await API.rejectWorkflowStep(instanceId, { actor_id: 'current', actor_name: 'User', comment: reason });
        if (!res || res.success === false) {
            App.toast((res && res.error) || 'Failed to reject', 'error');
            return;
        }
        App.toast('Workflow rejected', 'info');
        App.closeModal();
        this._loadActive();
    },

    async _showStartWorkflow() {
        let defs = [];
        try {
            const res = await API.workflowDefinitions();
            defs = res.definitions || [];
        } catch (e) {}

        App.showModal('Start New Request', `
            <div class="space-y-3">
                <div>
                    <label class="form-label">Workflow Type</label>
                    <select id="wf-type-select" class="form-select" onchange="WorkflowsPage._updateDefDesc()">
                        ${defs.map(d => `<option value="${d.id}" desc="${App.escapeHtml(d.description || '')}">${App.escapeHtml(d.name)}</option>`).join('')}
                    </select>
                    <p id="wf-def-desc" class="text-xs text-secondary mt-1">${defs[0]?.description || ''}</p>
                </div>
                <div>
                    <label class="form-label">Context (optional)</label>
                    <textarea id="wf-context" class="form-textarea" placeholder="Additional information..."></textarea>
                </div>
            </div>
        `, async () => {
            const defId = document.getElementById('wf-type-select')?.value;
            if (!defId) { App.toast('Select a workflow type', 'error'); return; }
            const res = await API.startWorkflow({ workflow_def_id: defId, initiator_id: 'current', initiator_name: 'User' });
            if (!res || res.success === false) {
                App.toast((res && res.error) || 'Failed to start workflow', 'error');
                return;
            }
            App.toast('Workflow started', 'success');
            this._loadActive();
        });
    },

    _updateDefDesc() {
        const sel = document.getElementById('wf-type-select');
        const desc = document.getElementById('wf-def-desc');
        if (sel && desc) {
            const opt = sel.options[sel.selectedIndex];
            desc.textContent = opt?.getAttribute('desc') || '';
        }
    },
};

window.WorkflowsPage = WorkflowsPage;
(window._PAGES = window._PAGES || {})["#workflows"] = WorkflowsPage;
