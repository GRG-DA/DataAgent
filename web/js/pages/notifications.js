/* ═══════════════════════════════════════════
   Notifications Page — Full notification history and preferences
   ═══════════════════════════════════════════ */

const NotificationsPage = {
    _page: 0,
    _pageSize: 30,
    _filter: '',

    async init() { this._page = 0; this._filter = ''; },
    async destroy() {},

    async render() {
        const pc = document.getElementById('page-content');
        pc.innerHTML = `
        <div class="max-w-4xl mx-auto animate-fade-in space-y-6">
            <div class="page-header">
                <div>
                    <h2><i class="fas fa-bell mr-2 text-indigo-500"></i>Notifications</h2>
                    <p class="text-secondary mt-1">View and manage all your notifications</p>
                </div>
                <div class="page-actions">
                    <button onclick="NotificationsPage._markAllRead()" class="btn btn-secondary btn-sm"><i class="fas fa-check-double"></i> Mark All Read</button>
                </div>
            </div>

            <!-- Filters -->
            <div class="flex flex-wrap gap-2">
                <button class="notif-filter active px-3 py-1.5 text-xs font-medium rounded-full bg-primary text-white" data-filter="" onclick="NotificationsPage._setFilter('')">All</button>
                <button class="notif-filter px-3 py-1.5 text-xs font-medium rounded-full bg-gray-100 dark:bg-gray-800 text-secondary hover:bg-gray-200" data-filter="alert" onclick="NotificationsPage._setFilter('alert')">Alerts</button>
                <button class="notif-filter px-3 py-1.5 text-xs font-medium rounded-full bg-gray-100 dark:bg-gray-800 text-secondary hover:bg-gray-200" data-filter="approval" onclick="NotificationsPage._setFilter('approval')">Approvals</button>
                <button class="notif-filter px-3 py-1.5 text-xs font-medium rounded-full bg-gray-100 dark:bg-gray-800 text-secondary hover:bg-gray-200" data-filter="comment" onclick="NotificationsPage._setFilter('comment')">Comments</button>
                <button class="notif-filter px-3 py-1.5 text-xs font-medium rounded-full bg-gray-100 dark:bg-gray-800 text-secondary hover:bg-gray-200" data-filter="sla" onclick="NotificationsPage._setFilter('sla')">SLA</button>
                <button class="notif-filter px-3 py-1.5 text-xs font-medium rounded-full bg-gray-100 dark:bg-gray-800 text-secondary hover:bg-gray-200" data-filter="system" onclick="NotificationsPage._setFilter('system')">System</button>
            </div>

            <!-- Preferences Toggle -->
            <div class="flex items-center justify-end gap-4 text-xs text-secondary">
                <label class="flex items-center gap-1.5 cursor-pointer">
                    <input type="checkbox" id="notif-pref-email" onchange="NotificationsPage._toggleEmailPref()">
                    Email Digest
                </label>
            </div>

            <!-- Notification List -->
            <div id="notif-page-list" class="space-y-2">
                <div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>
            </div>

            <!-- Load More -->
            <div id="notif-load-more" class="text-center hidden">
                <button onclick="NotificationsPage._loadMore()" class="btn btn-secondary btn-sm"><i class="fas fa-chevron-down"></i> Load More</button>
            </div>
        </div>`;
        await this._load();
        await this._loadPrefs();
    },

    _setFilter(filter) {
        this._filter = filter;
        this._page = 0;
        document.querySelectorAll('.notif-filter').forEach(b => {
            b.classList.remove('bg-primary', 'text-white');
            b.classList.add('bg-gray-100', 'dark:bg-gray-800', 'text-secondary');
        });
        const active = document.querySelector(`.notif-filter[data-filter="${filter}"]`);
        if (active) {
            active.classList.add('bg-primary', 'text-white');
            active.classList.remove('bg-gray-100', 'dark:bg-gray-800', 'text-secondary');
        }
        this._load();
    },

    async _load() {
        const container = document.getElementById('notif-page-list');
        if (!container) return;
        container.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>';

        try {
            const res = await API.getNotifications(this._pageSize, this._page * this._pageSize, false, this._filter);
            if (res.success === false) {
                container.innerHTML = '<div class="empty-state"><p>Failed to load notifications</p></div>';
                return;
            }
            const notifs = res.notifications || [];
            const total = res.total || 0;

            if (notifs.length === 0) {
                container.innerHTML = '<div class="empty-state py-12"><i class="fas fa-bell text-4xl mb-3 opacity-30"></i><h3>No notifications</h3><p>You\'re all caught up!</p></div>';
                return;
            }

            container.innerHTML = notifs.map(n => {
                const iconMap = { alert: 'fa-exclamation-triangle text-red-500', approval: 'fa-check-circle text-amber-500', comment: 'fa-comment text-blue-500', sla: 'fa-clock text-orange-500', system: 'fa-gear text-gray-500', data_quality: 'fa-chart-line text-green-500', workflow: 'fa-diagram-project text-purple-500' };
                const icon = iconMap[n.type] || 'fa-bell text-gray-400';
                const isUnread = !n.is_read ? 'border-l-4 border-l-primary bg-primary-50/30 dark:bg-primary-900/10' : 'opacity-70';
                return `<div class="card ${isUnread} cursor-pointer hover:shadow-md transition-shadow flex items-start gap-3" onclick="NotificationsPage._markRead('${n.id}', this)">
                    <i class="fas ${icon.split(' ')[0]} ${icon.split(' ')[1]} mt-1 text-lg"></i>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center justify-between">
                            <strong class="text-sm">${App.escapeHtml(n.title)}</strong>
                            <span class="text-xs text-secondary shrink-0 ml-2">${n.created_at ? new Date(n.created_at).toLocaleString() : ''}</span>
                        </div>
                        ${n.body ? `<div class="text-xs text-secondary mt-1">${App.escapeHtml(n.body)}</div>` : ''}
                        ${n.target_type ? `<div class="text-xs text-primary mt-1">${n.target_type}${n.target_id ? ': ' + App.escapeHtml(n.target_id) : ''}</div>` : ''}
                    </div>
                    ${!n.is_read ? '<span class="w-2 h-2 rounded-full bg-primary shrink-0 mt-2"></span>' : ''}
                </div>`;
            }).join('');

            const loadMore = document.getElementById('notif-load-more');
            if (loadMore) {
                if (this._page * this._pageSize + this._pageSize < total) {
                    loadMore.classList.remove('hidden');
                } else {
                    loadMore.classList.add('hidden');
                }
            }
        } catch (e) {
            container.innerHTML = '<div class="empty-state"><p>Failed to load notifications</p></div>';
        }
    },

    async _loadMore() {
        this._page++;
        await this._load();
    },

    async _markRead(id, element) {
        try {
            const res = await API.markNotificationRead(id);
            if (res.success === false) {
                App.toast(res.error || 'Failed to mark as read', 'error');
                return;
            }
            element.classList.remove('border-l-primary', 'bg-primary-50/30', 'dark:bg-primary-900/10');
            element.classList.add('opacity-70');
            const dot = element.querySelector('.w-2.h-2.rounded-full');
            if (dot) dot.remove();
            // Update badge
            if (window.NotifComponent) window.NotifComponent.fetchUnreadCount();
        } catch (e) {}
    },

    async _markAllRead() {
        try {
            const res = await API.markAllNotificationsRead();
            if (res.success === false) {
                App.toast(res.error || 'Failed to mark all as read', 'error');
                return;
            }
            App.toast(`Marked ${res.count ?? 'all'} as read`, 'success');
            document.querySelectorAll('#notif-page-list .card').forEach(el => {
                el.classList.remove('border-l-primary', 'bg-primary-50/30', 'dark:bg-primary-900/10');
                el.classList.add('opacity-70');
                const dot = el.querySelector('.w-2.h-2.rounded-full');
                if (dot) dot.remove();
            });
            if (window.NotifComponent) window.NotifComponent.fetchUnreadCount();
        } catch (e) { App.toast('Failed to mark all as read', 'error'); }
    },

    async _loadPrefs() {
        try {
            const res = await API.getNotificationPreferences();
            if (res.success === false) return;
            const prefs = res.preferences || {};
            const emailCheck = document.getElementById('notif-pref-email');
            if (emailCheck) emailCheck.checked = prefs.channel === 'email' || prefs.digest;
        } catch (e) {}
    },

    async _toggleEmailPref() {
        const enabled = document.getElementById('notif-pref-email')?.checked || false;
        try {
            const res = await API.updateNotificationPreferences({
                channel: enabled ? 'email' : 'in_app',
                digest: enabled,
            });
            if (res.success === false) {
                App.toast(res.error || 'Failed to update preferences', 'error');
                return;
            }
            App.toast(enabled ? 'Email digest enabled' : 'Email digest disabled', 'success');
        } catch (e) { App.toast('Failed to update preferences', 'error'); }
    },
};

window.NotificationsPage = NotificationsPage;
(window._PAGES = window._PAGES || {})["#notifications"] = NotificationsPage;
