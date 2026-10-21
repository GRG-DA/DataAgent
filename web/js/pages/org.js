/* ═══════════════════════════════════════════
   Org Structure Page — Departments, Teams, Groups
   ═══════════════════════════════════════════ */

const OrgPage = {
    _data: null,

    async init() {
        this._data = null;
    },

    async destroy() { this._data = null; },

    async render() {
        const pc = document.getElementById('page-content');
        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
            <div class="page-header">
                <div>
                    <h2><i class="fas fa-sitemap mr-2 text-indigo-500"></i>Org Structure</h2>
                    <p class="text-secondary mt-1">Manage departments, teams, and user groups</p>
                </div>
                <div class="page-actions">
                    <button onclick="OrgPage._showAddDept()" class="btn btn-primary btn-sm">
                        <i class="fas fa-plus"></i> Department
                    </button>
                    <button onclick="OrgPage._showAddTeam()" class="btn btn-secondary btn-sm">
                        <i class="fas fa-plus"></i> Team
                    </button>
                    <button onclick="OrgPage._showAddGroup()" class="btn btn-secondary btn-sm">
                        <i class="fas fa-plus"></i> Group
                    </button>
                    <button onclick="OrgPage._refresh()" class="btn btn-ghost btn-sm">
                        <i class="fas fa-sync"></i>
                    </button>
                </div>
            </div>

            <!-- Org Tree -->
            <div id="org-content">
                <div class="text-center py-12 text-secondary">
                    <i class="fas fa-sitemap text-4xl mb-3 opacity-30"></i>
                    <p>Loading org structure...</p>
                </div>
            </div>
        </div>`;
        await this._refresh();
    },

    async _refresh() {
        const container = document.getElementById('org-content');
        if (!container) return;
        container.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>';

        try {
            const res = await API.getOrgTree();
            if (!res.success) { container.innerHTML = `<div class="empty-state"><p>${App.escapeHtml(res.error || 'Failed to load')}</p></div>`; return; }
            const org = res.org || {};
            this._data = org;
            this._renderTree(container, org);
        } catch (e) {
            container.innerHTML = `<div class="empty-state"><p>Error: ${App.escapeHtml(e.message)}</p></div>`;
        }
    },

    _renderTree(container, org) {
        const depts = org.departments || [];
        const groups = org.groups || [];

        if (depts.length === 0 && groups.length === 0) {
            container.innerHTML = `<div class="empty-state py-12">
                <i class="fas fa-sitemap text-4xl mb-3 opacity-30"></i>
                <h3>No org structure yet</h3>
                <p>Create departments, teams, and groups to organize your users.</p>
                <button onclick="OrgPage._showAddDept()" class="btn btn-primary mt-4"><i class="fas fa-plus"></i> Create Department</button>
            </div>`;
            return;
        }

        let html = '<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">';

        // Departments
        html += '<div class="lg:col-span-2 card">';
        html += '<div class="card-header"><h3><i class="fas fa-building mr-2 text-indigo-500"></i>Departments & Teams</h3></div>';
        for (const dept of depts) {
            html += `<div class="mb-4 p-3 rounded-lg bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700">`;
            html += `<div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2">
                    <i class="fas fa-building text-indigo-500"></i>
                    <strong class="text-sm">${App.escapeHtml(dept.name)}</strong>
                    ${dept.head_user_id ? `<span class="text-xs text-secondary ml-2">Head: ${App.escapeHtml(dept.head_user_id)}</span>` : ''}
                </div>
                <div class="flex gap-1">
                    <button onclick="OrgPage._showAddTeam('${dept.id}')" class="text-xs text-primary hover:underline">+ Team</button>
                    <button onclick="OrgPage._deleteDept('${dept.id}')" class="text-xs text-danger hover:underline ml-2">Delete</button>
                </div>
            </div>`;
            // Teams
            const teams = dept.teams || [];
            if (teams.length > 0) {
                html += '<div class="ml-4 space-y-1">';
                for (const team of teams) {
                    html += `<div class="flex items-center justify-between py-1.5 px-2 rounded hover:bg-gray-100 dark:hover:bg-gray-700/50">
                        <div class="flex items-center gap-2">
                            <i class="fas fa-users text-xs text-gray-400"></i>
                            <span class="text-sm">${App.escapeHtml(team.name)}</span>
                            ${team.lead_user_id ? `<span class="text-xs text-secondary">Lead: ${App.escapeHtml(team.lead_user_id)}</span>` : ''}
                        </div>
                        <button onclick="OrgPage._deleteTeam('${team.id}')" class="text-xs text-danger hover:underline">Delete</button>
                    </div>`;
                }
                html += '</div>';
            } else {
                html += '<div class="ml-4 text-xs text-secondary py-1">No teams yet</div>';
            }
            html += '</div>';
        }
        html += '</div>'; // card

        // Groups
        html += '<div class="card">';
        html += '<div class="card-header"><h3><i class="fas fa-tag mr-2 text-purple-500"></i>Groups</h3></div>';
        if (groups.length === 0) {
            html += '<div class="text-center py-8 text-secondary text-sm">No groups created yet.</div>';
        } else {
            html += '<div class="space-y-2">';
            for (const g of groups) {
                html += `<div class="flex items-center justify-between p-2 rounded hover:bg-gray-50 dark:hover:bg-gray-800/50">
                    <div>
                        <div class="text-sm font-medium">${App.escapeHtml(g.name)}</div>
                        <div class="text-xs text-secondary">${App.escapeHtml(g.group_type || 'standard')}</div>
                    </div>
                    <div class="flex gap-1">
                        <button onclick="OrgPage._showGroupMembers('${g.id}')" class="text-xs text-primary hover:underline">Members</button>
                        <button onclick="OrgPage._deleteGroup('${g.id}')" class="text-xs text-danger hover:underline ml-1">Delete</button>
                    </div>
                </div>`;
            }
            html += '</div>';
        }
        html += '</div>'; // card

        html += '</div>'; // grid
        container.innerHTML = html;
    },

    _showAddDept() {
        App.showModal('Create Department', `
            <div class="space-y-3">
                <div><label class="form-label">Department Name</label><input id="add-dept-name" class="form-input" placeholder="e.g., Engineering"></div>
                <div><label class="form-label">Description</label><textarea id="add-dept-desc" class="form-textarea" placeholder="Optional description"></textarea></div>
                <div><label class="form-label">Head User ID</label><input id="add-dept-head" class="form-input" placeholder="Optional user ID"></div>
            </div>
        `, async () => {
            const name = document.getElementById('add-dept-name')?.value;
            if (!name) { App.toast('Name is required', 'error'); return; }
            const res = await API.createDepartment(name, document.getElementById('add-dept-desc')?.value || '', '', document.getElementById('add-dept-head')?.value || '');
            if (res.success === false) { App.toast(res.error || 'Failed to create department', 'error'); return; }
            App.toast('Department created', 'success');
            this._refresh();
        });
    },

    _showAddTeam(deptId) {
        App.showModal('Create Team', `
            <div class="space-y-3">
                <div><label class="form-label">Team Name</label><input id="add-team-name" class="form-input" placeholder="e.g., Data Engineering"></div>
                <div><label class="form-label">Lead User ID</label><input id="add-team-lead" class="form-input" placeholder="Optional lead"></div>
            </div>
        `, async () => {
            const name = document.getElementById('add-team-name')?.value;
            if (!name) { App.toast('Team name is required', 'error'); return; }
            const res = await API.createTeam(name, deptId, '', document.getElementById('add-team-lead')?.value || '');
            if (res.success === false) { App.toast(res.error || 'Failed to create team', 'error'); return; }
            App.toast('Team created', 'success');
            this._refresh();
        });
    },

    _showAddGroup() {
        App.showModal('Create Group', `
            <div class="space-y-3">
                <div><label class="form-label">Group Name</label><input id="add-group-name" class="form-input" placeholder="e.g., Data Owners"></div>
                <div><label class="form-label">Description</label><textarea id="add-group-desc" class="form-textarea" placeholder="Optional"></textarea></div>
                <div><label class="form-label">Type</label>
                    <select id="add-group-type" class="form-select">
                        <option value="standard">Standard</option>
                        <option value="data_owners">Data Owners</option>
                        <option value="data_stewards">Data Stewards</option>
                        <option value="admin">Admin</option>
                    </select>
                </div>
            </div>
        `, async () => {
            const name = document.getElementById('add-group-name')?.value;
            if (!name) { App.toast('Group name is required', 'error'); return; }
            const res = await API.createGroup(name, document.getElementById('add-group-desc')?.value || '', document.getElementById('add-group-type')?.value || 'standard');
            if (res.success === false) { App.toast(res.error || 'Failed to create group', 'error'); return; }
            App.toast('Group created', 'success');
            this._refresh();
        });
    },

    async _showGroupMembers(groupId) {
        App.closeModal();
        let members;
        try {
            const res = await API.getGroupMembers(groupId);
            if (res.success === false) { App.toast(res.error || 'Failed to load members', 'error'); return; }
            members = res.members || [];
        } catch (e) { members = []; }

        App.showModal('Group Members', `
            <div class="space-y-3">
                <div class="flex gap-2">
                    <input id="gm-user-id" class="form-input flex-1" placeholder="User ID to add">
                    <button onclick="OrgPage._addMember('${groupId}')" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i> Add</button>
                </div>
                <div class="divide-y">
                    ${members.length === 0 ? '<p class="text-secondary text-sm py-4 text-center">No members yet</p>' :
                        members.map(m => `<div class="flex items-center justify-between py-2">
                            <span class="text-sm">${App.escapeHtml(m.user_id)} <span class="text-xs text-secondary">(${m.role})</span></span>
                            <button onclick="OrgPage._removeMember('${groupId}','${m.user_id}')" class="text-xs text-danger hover:underline">Remove</button>
                        </div>`).join('')}
                </div>
            </div>
        `, null, 'Close');
    },

    async _addMember(groupId) {
        const userId = document.getElementById('gm-user-id')?.value;
        if (!userId) { App.toast('User ID required', 'error'); return; }
        const res = await API.addGroupMember(groupId, userId, 'member');
        if (res.success === false) { App.toast(res.error || 'Failed to add member', 'error'); return; }
        App.toast('Member added', 'success');
        this._showGroupMembers(groupId);
    },

    async _removeMember(groupId, userId) {
        if (!confirm(`Remove ${userId} from group?`)) return;
        const res = await API.removeGroupMember(groupId, userId);
        if (res.success === false) { App.toast(res.error || 'Failed to remove member', 'error'); return; }
        App.toast('Member removed', 'success');
        this._showGroupMembers(groupId);
    },

    async _deleteDept(id) {
        if (!confirm('Delete this department and all its teams?')) return;
        const res = await API.deleteDepartment(id);
        if (res.success === false) { App.toast(res.error || 'Failed to delete department', 'error'); return; }
        App.toast('Department deleted', 'success');
        this._refresh();
    },

    async _deleteTeam(id) {
        if (!confirm('Delete this team?')) return;
        const res = await API.deleteTeam(id);
        if (res.success === false) { App.toast(res.error || 'Failed to delete team', 'error'); return; }
        App.toast('Team deleted', 'success');
        this._refresh();
    },

    async _deleteGroup(id) {
        if (!confirm('Delete this group?')) return;
        const res = await API.deleteGroup(id);
        if (res.success === false) { App.toast(res.error || 'Failed to delete group', 'error'); return; }
        App.toast('Group deleted', 'success');
        this._refresh();
    },
};

window.OrgPage = OrgPage;
(window._PAGES = window._PAGES || {})["#org"] = OrgPage;
