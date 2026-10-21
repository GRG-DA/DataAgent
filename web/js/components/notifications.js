/* ═══════════════════════════════════════════
   Notification Component — Dropdown panel, badge count, polling
   ═══════════════════════════════════════════ */

(function () {
    'use strict';

    // ── State ──
    let _pollInterval = null;
    let _unreadCount = 0;

    // ── Initialize ──
    function init() {
        // Add event listeners
        const bell = document.getElementById('notif-bell');
        const dropdown = document.getElementById('notif-dropdown');
        const userBtn = document.getElementById('user-menu-btn');
        const userDropdown = document.getElementById('user-dropdown');

        if (bell && dropdown) {
            bell.addEventListener('click', function (e) {
                e.stopPropagation();
                const wasOpen = dropdown.classList.contains('open');
                dropdown.classList.toggle('open');
                if (userDropdown) userDropdown.classList.remove('open');
                // Load the notification list fresh every time the panel opens
                if (!wasOpen) loadNotifications();
            });
        }

        if (userBtn && userDropdown) {
            userBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                userDropdown.classList.toggle('open');
                if (dropdown) dropdown.classList.remove('open');
            });
        }

        // Close dropdowns on outside click
        document.addEventListener('click', function () {
            if (dropdown) dropdown.classList.remove('open');
            if (userDropdown) userDropdown.classList.remove('open');
        });

        // Start polling
        startPolling();
    }

    // ── Polling ──
    function startPolling() {
        fetchUnreadCount();
        _pollInterval = setInterval(fetchUnreadCount, 30000); // Every 30s
        // Auth attaches the JWT after DOMContentLoaded; re-check once it lands
        // so the badge fills without waiting a full 30s tick.
        if (window.RBAC && window.RBAC.getToken && !window.RBAC.getToken()) {
            setTimeout(fetchUnreadCount, 1500);
        }
    }

    function stopPolling() {
        if (_pollInterval) {
            clearInterval(_pollInterval);
            _pollInterval = null;
        }
    }

    async function fetchUnreadCount() {
        try {
            if (typeof API === 'undefined' || !API.getUnreadCount) return;
            // Skip until a session token exists — otherwise the pre-auth boot
            // poll fires /notifications/unread/count as 401 on every load.
            if (window.RBAC && window.RBAC.getToken && !window.RBAC.getToken()) return;
            const res = await API.getUnreadCount();
            if (!res.success) return;
            _unreadCount = res.count || 0;
            updateBadge(_unreadCount);
        } catch (e) {
            // Silently fail — API may not be ready
        }
    }

    function updateBadge(count) {
        const badge = document.getElementById('notif-badge');
        if (!badge) return;
        if (count > 0) {
            badge.style.display = 'inline';
            badge.textContent = count > 99 ? '99+' : count;
        } else {
            badge.style.display = 'none';
        }
    }

    // ── Load notifications into dropdown ──
    async function loadNotifications() {
        const list = document.getElementById('notif-list');
        if (!list) return;

        try {
            const res = await API.getNotifications(20, 0, true);
            const notifs = res.notifications || [];

            if (notifs.length === 0) {
                list.innerHTML = '<div class="text-center py-8 text-secondary text-sm">No new notifications</div>';
                return;
            }

            list.innerHTML = notifs.map(n => {
                const iconClass = n.type === 'alert' ? 'alert' : n.type === 'approval' ? 'approval' : n.type === 'system' ? 'system' : 'comment';
                const icon = n.type === 'alert' ? 'fa-exclamation' : n.type === 'approval' ? 'fa-check' : n.type === 'system' ? 'fa-gear' : 'fa-comment';
                return `
                <div class="notification-item unread" onclick="NotifComponent.markRead('${n.id}', this)">
                    <div class="notif-icon ${iconClass}"><i class="fas ${icon}"></i></div>
                    <div class="notif-body">
                        <div class="notif-title">${escapeHtml(n.title || '')}</div>
                        <div class="notif-body-text">${escapeHtml(n.body || '')}</div>
                        <div class="notif-time">${timeAgo(n.created_at)}</div>
                    </div>
                </div>`;
            }).join('');
            // Badge reflects the true unread count from the API, not the
            // number of rows rendered (the dropdown caps the list).
            fetchUnreadCount();
        } catch (e) {
            list.innerHTML = '<div class="text-center py-4 text-secondary text-sm">Unable to load notifications</div>';
        }
    }

    // ── Mark as read ──
    async function markRead(id, element) {
        try {
            const res = await API.markNotificationRead(id);
            if (res.success === false) return;
            if (element) {
                element.classList.remove('unread');
                element.innerHTML = element.innerHTML.replace('fa-exclamation', 'fa-check');
            }
            fetchUnreadCount();
        } catch (e) {}
    }

    // ── Mark all as read ──
    async function markAllRead() {
        try {
            const res = await API.markAllNotificationsRead();
            if (res.success === false) return;
            const items = document.querySelectorAll('#notif-list .notification-item');
            items.forEach(el => el.classList.remove('unread'));
            fetchUnreadCount();
            App.toast('All notifications marked as read', 'success');
        } catch (e) {}
    }

    // ── Helpers ──
    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function timeAgo(dateStr) {
        if (!dateStr) return '';
        const now = new Date();
        const date = new Date(dateStr);
        const diffMs = now - date;
        const diffMin = Math.floor(diffMs / 60000);
        const diffHour = Math.floor(diffMs / 3600000);
        const diffDay = Math.floor(diffMs / 86400000);

        if (diffMin < 1) return 'just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        if (diffHour < 24) return `${diffHour}h ago`;
        if (diffDay < 7) return `${diffDay}d ago`;
        return date.toLocaleDateString();
    }

    // ── Expose global API ──
    window.NotifComponent = {
        init,
        startPolling,
        stopPolling,
        loadNotifications,
        markRead,
        markAllRead,
        fetchUnreadCount,
        getUnreadCount: () => _unreadCount,
    };

    // Auto-initialize after page loads
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
