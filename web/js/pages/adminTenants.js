/* ═══════════════════════════════════════════════════════════════════
   Admin — Tenants & Workspaces

   Every account belongs to a tenant (ADR-009 D1) and a tenant owns a
   DuckDB schema, so the tenant list is the operator's view of "who
   shares a data space".  This panel shows each workspace, its members,
   and the org structure (departments / teams / groups) inside it, and
   lets an administrator move an account between workspaces.

   The tenant model is an API, so this console is a thin client over it.
   ═══════════════════════════════════════════════════════════════════ */

const AdminTenants = {
    _esc(value) {
        if (window.App && App.escapeHtml) return App.escapeHtml(String(value == null ? '' : value));
        return String(value == null ? '' : value);
    },

    async render(containerId) {
        const host = document.getElementById(containerId);
        if (!host) return;
        host.innerHTML = `
            <div class="card" id="admin-tenants-card">
                <div class="card-header">
                    <h3><i class="fas fa-sitemap mr-2 text-emerald-500"></i>Tenants &amp; Workspaces</h3>
                    <div class="flex items-center gap-2">
                        <button onclick="AdminTenants.load()" class="btn btn-ghost btn-sm" title="Reload"><i class="fas fa-sync"></i></button>
                    </div>
                </div>
                <div class="border-b border-gray-100 dark:border-gray-700 px-4 py-3">
                    <p class="text-xs text-secondary">
                        Each workspace owns its own schema, so its members share data and nobody else can read it.
                    </p>
                </div>
                <div id="admin-tenants-list"><div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div></div>
            </div>`;
        await this.load();
    },

    async load() {
        const host = document.getElementById('admin-tenants-list');
        if (!host) return;
        try {
            const [usersRes, deptsRes, groupsRes, teamsRes] = await Promise.all([
                API.listUsers(),
                API.listDepartments().catch(() => ({ departments: [] })),
                API.listGroups().catch(() => ({ groups: [] })),
                API.listTeams().catch(() => ({ teams: [] })),
            ]);
            const users = (usersRes && usersRes.users) || [];
            const departments = (deptsRes && deptsRes.departments) || [];
            const groups = (groupsRes && groupsRes.groups) || [];
            const teams = (teamsRes && teamsRes.teams) || [];
            this._render(host, users, departments, groups, teams);
        } catch (e) {
            host.innerHTML = '<div class="text-center py-4 text-secondary text-sm">The workspace list is not available.</div>';
        }
    },

    _render(host, users, departments, groups, teams) {
        const byTenant = new Map();
        users.forEach(u => {
            const key = (u.tenant_id || 'default') || 'default';
            if (!byTenant.has(key)) byTenant.set(key, []);
            byTenant.get(key).push(u);
        });
        const tenants = Array.from(byTenant.keys()).sort();
        if (!tenants.length) {
            host.innerHTML = '<div class="text-center py-4 text-secondary text-sm">No workspaces yet.</div>';
            return;
        }
        host.innerHTML = `<div class="divide-y divide-gray-100 dark:divide-gray-700">${tenants.map(t => this._tenant(t, byTenant.get(t), departments, groups, teams)).join('')}</div>`;
    },

    _tenant(tenant, members, departments, groups, teams) {
        const names = members.map(m => m.username);
        const mine = (list) => (list || []).filter(x => !x.tenant_id || x.tenant_id === tenant);
        const deptNames = mine(departments).map(d => d.name);
        const teamNames = mine(teams).map(t => t.name);
        const groupNames = mine(groups).map(g => g.name);
        const chips = (list, empty) => list.length
            ? list.map(n => `<span class="tag tag-indigo mr-1">${this._esc(n)}</span>`).join('')
            : `<span class="text-secondary text-xs">${empty}</span>`;
        return `
            <div class="px-4 py-3">
                <div class="flex items-center justify-between flex-wrap gap-2">
                    <div>
                        <div class="font-semibold text-sm"><i class="fas fa-building mr-1 text-emerald-500"></i>${this._esc(tenant)}</div>
                        <div class="text-xs text-secondary">${members.length} member${members.length === 1 ? '' : 's'}</div>
                    </div>
                    <div class="text-xs text-secondary">
                        ${names.slice(0, 6).map(n => `<span class="tag tag-gray mr-1">${this._esc(n)}</span>`).join('')}
                        ${names.length > 6 ? `<span class="tag tag-gray">+${names.length - 6}</span>` : ''}
                    </div>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2 text-xs">
                    <div><span class="text-secondary">Departments:</span> ${chips(deptNames, 'none')}</div>
                    <div><span class="text-secondary">Teams:</span> ${chips(teamNames, 'none')}</div>
                    <div><span class="text-secondary">Groups:</span> ${chips(groupNames, 'none')}</div>
                </div>
                <div class="mt-2">
                    <button onclick="AdminTenants.moveUser('${this._esc(tenant)}')" class="btn btn-ghost btn-sm"><i class="fas fa-arrow-right-arrow-left mr-1"></i>Move an account to this workspace</button>
                </div>
            </div>`;
    },

    async moveUser(tenant) {
        let users = [];
        try {
            const res = await API.listUsers();
            users = (res && res.users) || [];
        } catch (e) {
            App.error('The account list is not available.');
            return;
        }
        const names = users.map(u => u.username).sort();
        if (!names.length) { App.warning('There are no accounts yet.'); return; }
        const pick = window.prompt(
            `Move which account into workspace "${tenant}"?\n\nAccounts: ${names.join(', ')}\n\nType the username:`,
            ''
        );
        if (!pick) return;
        const username = pick.trim();
        if (!names.includes(username)) {
            App.error(`"${username}" is not an account.`);
            return;
        }
        try {
            await API.updateUser(username, { tenant_id: tenant });
            App.success(`${username} now belongs to ${tenant}.`);
            await this.load();
        } catch (e) {
            App.error((e && e.message) || 'The account could not be moved.');
        }
    },
};

if (typeof window !== 'undefined') window.AdminTenants = AdminTenants;
