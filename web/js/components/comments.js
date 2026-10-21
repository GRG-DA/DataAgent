/* ═══════════════════════════════════════════
   Comments Component — Threaded comment widget
   Reusable across dashboard, reports, catalog, tables
   ═══════════════════════════════════════════ */

const CommentsWidget = {
    _targetType: '',
    _targetId: '',
    _containerId: '',

    async mount(containerId, targetType, targetId) {
        this._containerId = containerId;
        this._targetType = targetType;
        this._targetId = targetId;
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
        <div class="comments-widget">
            <div class="flex items-center justify-between mb-3">
                <h4 class="text-sm font-semibold"><i class="fas fa-comments mr-1.5 text-primary"></i>Comments</h4>
                <button onclick="CommentsWidget._toggleForm()" class="btn btn-ghost btn-sm">
                    <i class="fas fa-plus"></i> Add
                </button>
            </div>
            <div id="comment-form-${containerId}" class="hidden mb-3">
                <textarea id="comment-input-${containerId}" class="form-textarea text-sm" placeholder="Write a comment... (use @name to mention)" rows="2"></textarea>
                <div class="flex justify-end gap-2 mt-2">
                    <button onclick="CommentsWidget._cancel()" class="btn btn-ghost btn-sm">Cancel</button>
                    <button onclick="CommentsWidget._submit()" class="btn btn-primary btn-sm"><i class="fas fa-paper-plane"></i> Post</button>
                </div>
            </div>
            <div id="comment-list-${containerId}" class="space-y-3">
                <div class="text-center py-4 text-secondary text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div>
            </div>
        </div>`;
        await this._load();
    },

    async _load() {
        const list = document.getElementById(`comment-list-${this._containerId}`);
        if (!list) return;
        try {
            const res = await API.listComments(this._targetType, this._targetId);
            const comments = res.comments || [];
            if (comments.length === 0) {
                list.innerHTML = '<div class="text-center py-6 text-secondary text-sm"><i class="fas fa-comment-dots text-2xl mb-2 opacity-30"></i><br>No comments yet</div>';
                return;
            }
            list.innerHTML = comments.map(c => this._renderComment(c)).join('');
        } catch (e) {
            list.innerHTML = '<div class="text-center py-4 text-secondary text-sm">Failed to load comments</div>';
        }
    },

    _renderComment(c) {
        const date = c.created_at ? new Date(c.created_at).toLocaleString() : '';
        const resolved = c.is_resolved ? 'opacity-50' : '';
        return `
        <div class="flex gap-3 ${resolved}">
            <div class="w-8 h-8 rounded-full bg-primary-100 dark:bg-primary-900/30 flex items-center justify-center text-xs font-bold text-primary shrink-0">
                ${(c.author_name || c.author_id || '?').charAt(0).toUpperCase()}
            </div>
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                    <span class="text-sm font-semibold">${this._escape(c.author_name || c.author_id)}</span>
                    <span class="text-xs text-secondary">${date}</span>
                    ${c.is_resolved ? '<span class="tag tag-green text-[10px]"><i class="fas fa-check"></i> Resolved</span>' : ''}
                </div>
                <div class="text-sm mt-1">${this._escape(c.content)}</div>
                ${c.replies && c.replies.length > 0 ? `
                    <div class="mt-2 ml-4 space-y-2 border-l-2 border-gray-100 dark:border-gray-700 pl-3">
                        ${c.replies.map(r => `
                            <div class="flex gap-2">
                                <div class="w-6 h-6 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center text-[9px] font-bold shrink-0">${(r.author_name || r.author_id || '?').charAt(0).toUpperCase()}</div>
                                <div><span class="text-xs font-semibold">${this._escape(r.author_name || r.author_id)}</span>
                                <span class="text-xs text-secondary ml-1">${r.created_at ? new Date(r.created_at).toLocaleString() : ''}</span>
                                <div class="text-sm">${this._escape(r.content)}</div></div>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
            </div>
        </div>`;
    },

    _toggleForm() {
        const form = document.getElementById(`comment-form-${this._containerId}`);
        if (form) form.classList.toggle('hidden');
    },

    _cancel() {
        const form = document.getElementById(`comment-form-${this._containerId}`);
        const input = document.getElementById(`comment-input-${this._containerId}`);
        if (form) form.classList.add('hidden');
        if (input) input.value = '';
    },

    async _submit() {
        const input = document.getElementById(`comment-input-${this._containerId}`);
        if (!input || !input.value.trim()) return;
        try {
            await API.addComment(this._targetType, this._targetId, {
                content: input.value.trim(),
                author_id: 'current',
                author_name: localStorage.getItem('username') || 'User',
            });
            input.value = '';
            const form = document.getElementById(`comment-form-${this._containerId}`);
            if (form) form.classList.add('hidden');
            await this._load();
        } catch (e) {
            alert('Failed to post comment');
        }
    },

    _escape(str) {
        if (!str) return '';
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    },
};
