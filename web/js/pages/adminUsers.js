/* ═══════════════════════════════════════════════════════════════════
   Admin — Users & Roles

   Role management is an API (ADR-009 D4), so the console is a thin
   client over it.  Available to any administrator, whatever the plan:
   managing accounts is not a premium feature.
   ═══════════════════════════════════════════════════════════════════ */

const AdminUsers = {
    ROLES: ['admin', 'data_engineer', 'data_analyst', 'user', 'viewer', 'auditor'],

    _esc(value) {
        if (window.App && App.escapeHtml) return App.escapeHtml(String(value == null ? '' : value));
        return String(value == null ? '' : value);
    },

    async render(containerId) {
        const host = document.getElementById(containerId);
        if (!host) return;
        host.innerHTML = `
            <div class="card" id="admin-users-card">
                <div class="card-header">
                    <h3><i class="fas fa-users mr-2 text-indigo-500"></i>Users &amp; Roles</h3>
                    <div class="flex items-center gap-2">
                        <button onclick="AdminUsers.load()" class="btn btn-ghost btn-sm" title="Reload"><i class="fas fa-sync"></i></button>
                    </div>
                </div>
                <div class="border-b border-gray-100 dark:border-gray-700 px-4 py-3">
                    <p class="text-xs text-secondary mb-2"><strong>Invite a user</strong> — they receive a link that lets them choose a password.</p>
                    <div class="grid grid-cols-1 md:grid-cols-5 gap-2 items-end">
                        <div>
                            <div class="form-label">Username</div>
                            <input id="invite-username" class="form-input" placeholder="jane.doe" autocomplete="off">
                        </div>
                        <div>
                            <div class="form-label">E-mail</div>
                            <input id="invite-email" type="email" class="form-input" placeholder="jane@example.com" autocomplete="off">
                        </div>
                        <div>
                            <div class="form-label">Role</div>
                            <select id="invite-role" class="form-select">${this.ROLES.map(r => `<option value="${r}">${r}</option>`).join('')}</select>
                        </div>
                        <div>
                            <div class="form-label">Workspace (tenant)</div>
                            <input id="invite-tenant" class="form-input" placeholder="leave empty for a new one" list="admin-tenant-list" autocomplete="off">
                            <datalist id="admin-tenant-list"></datalist>
                        </div>
                        <div>
                            <button onclick="AdminUsers.invite()" class="btn btn-primary btn-sm w-full"><i class="fas fa-paper-plane mr-1"></i>Invite</button>
                        </div>
                    </div>
                    <div id="invite-status" class="text-xs mt-2" style="display:none"></div>
                </div>
                <div id="admin-users-list"><div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div></div>
            </div>`;
        await this.load();
    },

    async load() {
        const host = document.getElementById('admin-users-list');
        if (!host) return;
        try {
            const res = await API.listUsers();
            const users = (res && res.users) || [];
            this._render(host, users);
        } catch (e) {
            host.innerHTML = '<div class="text-center py-4 text-secondary text-sm">The account list is not available.</div>';
        }
    },

    _render(host, users) {
        if (!users.length) {
            host.innerHTML = '<div class="text-center py-4 text-secondary text-sm">No accounts yet.</div>';
            return;
        }
        const tenants = Array.from(new Set(users.map(u => u.tenant_id).filter(Boolean))).sort();
        const list = document.getElementById('admin-tenant-list');
        if (list) {
            list.innerHTML = tenants.map(t => `<option value="${this._esc(t)}"></option>`).join('');
        }
        host.innerHTML = `<div class="overflow-x-auto"><table class="data-table">
            <tr><th>User</th><th>Role</th><th>Workspace</th><th>E-mail</th><th>Verified</th><th>Last login</th><th>Status</th><th></th></tr>
            ${users.map(u => this._row(u)).join('')}
        </table></div>`;
    },

    _row(u) {
        const username = this._esc(u.username);
        const active = u.is_active !== false;
        const verified = !!u.email_verified;
        const lastLogin = u.last_login ? new Date(u.last_login).toLocaleString() : '—';
        const options = this.ROLES.map(r => `<option value="${r}"${r === u.role ? ' selected' : ''}>${r}</option>`).join('');
        return `<tr>
            <td class="font-medium">${username}</td>
            <td><select class="form-select" style="min-width:130px" onchange="AdminUsers.changeRole('${username}', this.value)">${options}</select></td>
            <td>${this._esc(u.tenant_id || '—')}</td>
            <td>${this._esc(u.email || '—')}</td>
            <td>${verified ? '<span class="tag tag-green">yes</span>' : '<span class="tag tag-amber">no</span>'}</td>
            <td class="text-secondary">${this._esc(lastLogin)}</td>
            <td>${active ? '<span class="tag tag-green">active</span>' : '<span class="tag tag-gray">disabled</span>'}</td>
            <td class="whitespace-nowrap">
                <button onclick="AdminUsers.toggleActive('${username}', ${active})" class="btn btn-ghost btn-sm" title="${active ? 'Deactivate' : 'Activate'}"><i class="fas fa-${active ? 'ban' : 'check'}"></i></button>
                <button onclick="AdminUsers.resetPassword('${username}')" class="btn btn-ghost btn-sm" title="Reset the password"><i class="fas fa-key"></i></button>
                <button onclick="AdminUsers.remove('${username}')" class="btn btn-ghost btn-sm" title="Delete the account and its data" style="color:var(--color-accent-red)"><i class="fas fa-trash"></i></button>
            </td>
        </tr>`;
    },

    async changeRole(username, role) {
        try {
            await API.updateUser(username, { role });
            App.success(`${username} is now ${role}`);
        } catch (e) {
            App.error(this._error(e, 'The role could not be changed.'));
            await this.load();
        }
    },

    async toggleActive(username, isActive) {
        try {
            await API.updateUser(username, { is_active: !isActive });
            App.success(isActive ? `${username} deactivated` : `${username} activated`);
            await this.load();
        } catch (e) {
            App.error(this._error(e, 'The account could not be updated.'));
        }
    },

    async remove(username) {
        const confirmed = await Modal.confirm(
            'Delete account',
            `Delete "${username}" and everything it owns — dashboards, conversations, uploads and its workspace data? This cannot be undone.`,
            { variant: 'danger', confirmLabel: 'Delete account' }
        );
        if (!confirmed) return;
        try {
            await API.deleteUser(username);
            App.success(`${username} deleted`);
        } catch (e) {
            App.error(this._error(e, 'The account could not be deleted.'));
        }
        await this.load();
    },

    async resetPassword(username) {
        const password = window.prompt(
            `New password for "${username}"?\n\nThe user must change it at their next sign-in.\n` +
            'Minimum 12 characters, with upper case, lower case, a number and a symbol.'
        );
        if (!password) return;
        try {
            await API.resetUserPassword(username, password);
            App.success(`Password reset for ${username}. They must change it at next sign-in.`);
        } catch (e) {
            App.error(this._error(e, 'The password could not be reset.'));
        }
    },

    async invite() {
        const status = document.getElementById('invite-status');
        const username = (document.getElementById('invite-username').value || '').trim();
        const email = (document.getElementById('invite-email').value || '').trim();
        const role = document.getElementById('invite-role').value;
        const tenant = (document.getElementById('invite-tenant').value || '').trim();
        const show = (message, ok) => {
            if (!status) return;
            status.textContent = message;
            status.style.color = ok ? 'var(--color-accent-green)' : 'var(--color-accent-red)';
            status.style.display = 'block';
        };
        if (!username || !email) {
            show('A username and an e-mail address are required.', false);
            return;
        }
        try {
            const res = await API.inviteUser({ username, email, role, tenant_id: tenant });
            show((res && res.message) || 'Invitation sent.', true);
            document.getElementById('invite-username').value = '';
            document.getElementById('invite-email').value = '';
            await this.load();
        } catch (e) {
            show(this._error(e, 'The invitation could not be sent.'), false);
        }
    },

    _error(e, fallback) {
        if (e && e.data && (e.data.detail || e.data.error)) return String(e.data.detail || e.data.error);
        if (e && e.message) return String(e.message);
        return fallback;
    },
};

window.AdminUsers = AdminUsers;
