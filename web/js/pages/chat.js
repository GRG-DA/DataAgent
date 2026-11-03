/**
 * ChatPage — Conversational Data Analyst
 *
 * Rewritten so the chat page:
 *  • Streams from /llm/chat/stream (SSE) with a hard fallback to /ask
 *  • Sends the selected table + session id so the backend scopes queries
 *  • Never shows an empty bubble: a zero-token stream falls back to /ask
 *  • Dedupes SQL queries (never the same query twice) and keeps a clean
 *    markdown-ish renderer for code blocks, bold, inline code and SQL
 *  • Persists multiple conversations in localStorage with a sidebar UI
 *  • Sidebar + full-width layout (like ChatGPT/Claude)
 */

let _vizSeq = 0;
const ChatPage = {
    // ── state ──
    _messages: [],
    _isWaiting: false,
    _scrollArea: null,
    _sessionId: 'chat_' + Date.now(),
    _selectedTables: [],          // EXPLICIT scope: the tables the user picked (empty = All tables, no scope)
    _tableSearch: '',
    _conversations: [],           // [{ id, title, messages, createdAt, updatedAt }]
    _activeConversationId: null,
    _sidebarCollapsed: false,
    _searchQuery: '',
    _abortCtrl: null,
    _stopped: false,
    _STORAGE_KEY: 'chat_conversations',
    _SIDEBAR_KEY: 'chat_sidebar_collapsed',
    // v2: the selection is now the INCLUSIVE scope (tables the user picked).
    // The v1 key stored an EXCLUSION set, so it must not be resurrected.
    _TABLES_KEY: 'chat_selected_tables_v2',

    // ── quick-task prompts ──
    PREBUILT_TASKS: [
        { label: 'Show me trends',    prompt: 'Show me the key trends in this dataset' },
        { label: 'Find anomalies',    prompt: 'Find any anomalies or outliers in the data' },
        { label: 'Compare groups',    prompt: 'Compare groups in the data and highlight significant differences' },
        { label: 'Data summary',      prompt: 'Give me a comprehensive summary of this dataset' },
        { label: 'Correlations',      prompt: 'What are the strongest correlations between columns?' },
        { label: 'Missing values',    prompt: 'Analyze missing values and suggest how to handle them' },
        { label: 'Top segments',      prompt: 'Identify the top segments in this data' },
        { label: 'Generate SQL',      prompt: 'Generate a useful SQL query for exploring this table' },
    ],

    // ══════════════════════════════════════════════════════════════════
    //  Lifecycle
    // ══════════════════════════════════════════════════════════════════

    async init() {
        if (!App.state.llm_enabled || !App.state.features.enable_llm_agent) {
            this._renderDisabled();
            return;
        }
        this._isWaiting = false;
        this._lastPreview = null;
        this._storedPreviewData = null;

        // Per-user browser keys: switching logins on one machine must not
        // surface the previous user's chats.
        const scoped = (k) => (window.RBAC && window.RBAC.storeKey)
            ? window.RBAC.storeKey(k) : k;
        this._STORAGE_KEY = scoped('chat_conversations');
        this._ACTIVE_KEY = scoped('chat_active_conversation');

        this._loadConversations();

        // Restore the query scope BEFORE the first await. destroy() persists
        // the scope when the page is torn down and the router can call it
        // while init() is still waiting on the server sync — an empty scope
        // at that moment would overwrite the stored selection.
        this._selectedTables = this._readStoredTables();

        await this._syncFromServer();
        this._sidebarCollapsed = localStorage.getItem(this._SIDEBAR_KEY) === 'true';

        // Restore last active conversation, or create a fresh one.
        const lastId = localStorage.getItem(this._ACTIVE_KEY);
        const conv = this._conversations.find(c => c.id === lastId);
        if (conv) {
            this._activeConversationId = conv.id;
            this._messages = conv.messages;
        } else {
            this._newConversation();
        }
    },

    async destroy() {
        this._saveConversations();
        try { localStorage.setItem(this._ACTIVE_KEY, this._activeConversationId || ''); } catch (_) {}
        try { localStorage.setItem(this._SIDEBAR_KEY, this._sidebarCollapsed ? 'true' : 'false'); } catch (_) {}
        this._persistScopeOnExit();
        if (this._boundClose) document.removeEventListener('click', this._boundClose);
        this._scrollArea = null;
    },

    // ══════════════════════════════════════════════════════════════════
    //  Conversation persistence (localStorage)
    // ══════════════════════════════════════════════════════════════════

    _loadConversations() {
        try {
            const raw = localStorage.getItem(this._STORAGE_KEY);
            this._conversations = raw ? JSON.parse(raw) : [];
            if (!Array.isArray(this._conversations)) this._conversations = [];
        } catch (_) {
            this._conversations = [];
        }
        // Prune empty untitled conversations (created by opening the page but
        // never sending a message) and collapse exact-duplicate titles so the
        // sidebar doesn't fill with "New chat / Sep 23" rows.
        const seen = new Set();
        this._conversations = this._conversations.filter(c => {
            const hasMessages = c && c.messages && c.messages.length > 0;
            const title = (c && c.title || '').trim();
            if (!hasMessages && (!title || title === 'New chat')) return false;
            const key = title + '|' + (c.messages || []).length;
            if (seen.has(key) && title === 'New chat') return false;
            seen.add(key);
            return true;
        });
    },

    /** Copy of the conversations without chart payloads (vizHtml/vizSpecs),
     *  used only for persistence — the in-memory session keeps its charts. */
    _stripVizPayload(convs) {
        return convs.map(c => ({
            ...c,
            messages: (c.messages || []).map(m => {
                if (!m.vizHtml && !m.vizSpecs) return m;
                const slim = { ...m };
                delete slim.vizHtml;
                delete slim.vizSpecs;
                return slim;
            }),
        }));
    },

    _saveConversations() {
        const recent = this._conversations.slice(-50);
        try {
            localStorage.setItem(this._STORAGE_KEY, JSON.stringify(recent));
            return;
        } catch (_) {}
        // Quota exceeded: chart payloads dominate the store — persist a
        // slimmed copy (message text kept, charts dropped) rather than
        // silently losing the whole history.
        try {
            localStorage.setItem(this._STORAGE_KEY, JSON.stringify(this._stripVizPayload(recent)));
            return;
        } catch (_) {}
        // Still too big: fall back to the most recent conversations only.
        try {
            localStorage.setItem(this._STORAGE_KEY, JSON.stringify(this._stripVizPayload(recent.slice(-10))));
        } catch (_) {}
    },

    /**
     * Best-effort server sync for conversations (per-account persistence).
     * Merges server conversations into the local list and uploads any
     * local-only conversations. Falls back silently to local-only mode
     * when the API is unreachable.
     */
    async _syncFromServer() {
        try {
            const res = await API.listConversations();
            const serverConvs = (res && res.conversations) || [];
            const known = new Set(this._conversations.filter(c => c.serverId).map(c => c.serverId));

            for (const sc of serverConvs) {
                if (known.has(sc.id)) continue;
                try {
                    const full = await API.getConversation(sc.id);
                    const messages = (full && full.messages) || [];
                    this._conversations.push({
                        id: 'srv_' + sc.id,
                        serverId: sc.id,
                        title: sc.title || 'New chat',
                        messages: messages.map(m => ({
                            role: m.role,
                            content: m.content,
                            time: m.created_at
                                ? new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                                : '',
                        })),
                        createdAt: sc.created_at ? new Date(sc.created_at).getTime() : Date.now(),
                        updatedAt: sc.updated_at ? new Date(sc.updated_at).getTime() : Date.now(),
                        _syncedCount: messages.length,
                    });
                } catch (_) {}
            }

            for (const conv of this._conversations) {
                if (conv.serverId) continue;
                if (!conv.messages || conv.messages.length === 0) continue;
                try {
                    const created = await API.createConversation(conv.title || 'New chat');
                    if (created && created.id) {
                        conv.serverId = created.id;
                        conv._syncedCount = 0;
                        for (const m of conv.messages) {
                            if (m.role === 'user' || m.role === 'assistant') {
                                await API.addMessage(created.id, m.role, m.content);
                                conv._syncedCount++;
                            }
                        }
                    }
                } catch (_) {}
            }

            this._saveConversations();
        } catch (_) {
            // Server unavailable — keep local-only mode.
        }
    },

    /** Upload any unsent messages of a conversation to the server (fire-and-forget). */
    _syncMessages(conv) {
        if (!conv || !conv.serverId || conv._syncing) return;
        const start = conv._syncedCount || 0;
        const pending = conv.messages.slice(start);
        if (!pending.length) return;
        conv._syncing = true;
        (async () => {
            try {
                for (const m of pending) {
                    if (m.role === 'user' || m.role === 'assistant') {
                        await API.addMessage(conv.serverId, m.role, m.content);
                        conv._syncedCount = (conv._syncedCount || 0) + 1;
                    }
                }
            } catch (err) {
                // 404 → the server conversation is gone (stale serverId).
                // Drop the dead id and re-sync so it gets re-created on the
                // server and the local messages are re-uploaded from scratch.
                if (err && err.status === 404) {
                    conv.serverId = null;
                    conv._syncedCount = 0;
                    this._syncFromServer();
                }
            } finally {
                conv._syncing = false;
            }
        })();
    },

    async _newConversation() {
        // Reuse an existing empty draft instead of stacking another
        // "New chat" row every time the page boots.
        const draft = this._conversations.find(c =>
            (!c.messages || c.messages.length === 0) &&
            (!c.title || c.title === 'New chat'));
        if (draft) {
            this._activeConversationId = draft.id;
            this._messages = draft.messages;
            this._persistActiveConversation();
            this._renderSidebar();
            this._renderMessages();
            this._updateHeaderTitle();
            return draft.id;
        }
        const id = 'conv_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7);
        const conv = {
            id,
            title: 'New chat',
            messages: [],
            createdAt: Date.now(),
            updatedAt: Date.now(),
        };
        this._conversations.unshift(conv);
        this._activeConversationId = id;
        this._messages = conv.messages;
        this._saveConversations();
        this._persistActiveConversation();
        this._renderSidebar();
        this._renderMessages();
        this._updateHeaderTitle();
        try {
            const created = await API.createConversation('New chat');
            if (created && created.id) conv.serverId = created.id;
        } catch (_) {}
        return id;
    },

    _persistActiveConversation() {
        try { localStorage.setItem(this._ACTIVE_KEY, this._activeConversationId || ''); } catch (_) {}
    },

    _switchConversation(id) {
        const conv = this._conversations.find(c => c.id === id);
        if (!conv) return;
        this._activeConversationId = id;
        this._messages = conv.messages;
        this._saveConversations();
        this._persistActiveConversation();
        this._renderSidebar();
        this._renderMessages();
        this._updateHeaderTitle();
        this._scrollToBottom();
    },

    _deleteConversation(id) {
        const idx = this._conversations.findIndex(c => c.id === id);
        if (idx === -1) return;
        const conv = this._conversations[idx];
        this._conversations.splice(idx, 1);
        this._saveConversations();
        if (conv.serverId) {
            API.deleteConversation(conv.serverId).catch(() => {});
        }
        if (this._activeConversationId === id) {
            if (this._conversations.length) {
                this._switchConversation(this._conversations[0].id);
            } else {
                this._newConversation();
            }
        } else {
            this._renderSidebar();
        }
        App.toast('Conversation deleted', 'info');
    },

    /** 404-safe server-side rename: a dead serverId (conversation removed
     *  server-side) is cleared so the next sync re-creates the conversation
     *  with the local title instead of 404ing forever. */
    _renameServer(conv) {
        if (!conv || !conv.serverId) return;
        API.renameConversation(conv.serverId, conv.title).catch((err) => {
            if (err && err.status === 404) conv.serverId = null;
        });
    },

    _renameConversation(id, title) {
        const conv = this._conversations.find(c => c.id === id);
        if (!conv) return;
        const clean = (title || '').trim();
        if (clean) conv.title = clean.slice(0, 80);
        conv.updatedAt = Date.now();
        this._saveConversations();
        this._renameServer(conv);
        this._renderSidebar();
        if (id === this._activeConversationId) this._updateHeaderTitle();
    },

    _generateTitle(text) {
        const clean = (text || '').trim().replace(/\s+/g, ' ');
        if (!clean) return 'New chat';
        return clean.length > 40 ? clean.slice(0, 40) + '…' : clean;
    },

    _updateConversationTitle() {
        if (!this._activeConversationId) return;
        const conv = this._conversations.find(c => c.id === this._activeConversationId);
        if (!conv) return;
        const firstUser = this._messages.find(m => m.role === 'user');
        if (firstUser && (conv.title === 'New chat' || !conv.title)) {
            conv.title = this._generateTitle(firstUser.content);
            this._saveConversations();
            this._renameServer(conv);
            this._renderSidebar();
            this._updateHeaderTitle();
        }
    },

    _updateHeaderTitle() {
        const el = document.getElementById('chat-header-title');
        if (el) {
            const conv = this._conversations.find(c => c.id === this._activeConversationId);
            el.textContent = conv ? conv.title : 'New chat';
        }
    },

    // ══════════════════════════════════════════════════════════════════
    //  Render
    // ══════════════════════════════════════════════════════════════════

    async render() {
        await App.refreshTables();
        const pc = document.getElementById('page-content');

        // Drop selected tables that no longer exist — but only against a
        // table list we actually have (see _pruneSelectedTables).
        this._pruneSelectedTables();

        pc.innerHTML = `
        <div class="chat-layout animate-fade-in">
            <!-- Sidebar -->
            <aside class="chat-sidebar ${this._sidebarCollapsed ? 'collapsed' : ''}" id="chat-sidebar">
                <div class="chat-sidebar-header">
                    <button class="btn-new-chat" onclick="ChatPage._newConversation()">
                        <i class="fas fa-plus"></i> New chat
                    </button>
                    <input type="text" class="chat-sidebar-search" id="chat-sidebar-search" aria-label="Search conversations"
                        placeholder="Search conversations..."
                        value="${App.escapeHtml(this._searchQuery)}"
                        oninput="ChatPage._onSearch(this.value)">
                </div>
                <div class="chat-conv-list" id="chat-conv-list"></div>
            </aside>

            <!-- Sidebar toggle -->
            <button class="chat-sidebar-toggle ${this._sidebarCollapsed ? '' : 'shifted'}" id="chat-sidebar-toggle"
                onclick="ChatPage._toggleSidebar()" title="Toggle sidebar">
                <i class="fas ${this._sidebarCollapsed ? 'fa-chevron-right' : 'fa-chevron-left'}"></i>
            </button>

            <!-- Main chat area -->
            <main class="chat-main">
                <div class="chat-main-inner">
                    <!-- Header -->
                    <div class="chat-header">
                        <div class="chat-header-title" id="chat-header-title" title="Click to rename"
                            ondblclick="ChatPage._startRenameHeader()">New chat</div>
                        <div class="chat-header-controls">
                            <div class="relative" id="chat-table-picker">
                                <button id="chat-table-btn" onclick="ChatPage._toggleTablePicker()"
                                    class="text-sm border rounded-lg px-2.5 py-1.5 min-w-[150px] max-w-[190px] flex items-center justify-between gap-2 dark:bg-gray-700 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-600 transition-colors"
                                    title="Choose which tables the agent may use (multi-select)">
                                    <i class="fas fa-bullseye text-[10px] text-primary-500 shrink-0"></i>
                                    <span id="chat-table-btn-label" class="truncate">All tables</span>
                                    <i class="fas fa-chevron-down text-[9px] text-gray-400 shrink-0"></i>
                                </button>
                                <div id="chat-table-panel" class="hidden absolute z-10 right-0 mt-1 w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-lg overflow-hidden">
                                    <div class="px-3 py-2 border-b border-gray-100 dark:border-gray-700 flex items-center gap-2">
                                        <i class="fas fa-bullseye text-primary-500 text-[11px]"></i>
                                        <span class="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">Query scope</span>
                                    </div>
                                    <div class="p-2 border-b border-gray-100 dark:border-gray-700">
                                        <input id="chat-table-search" type="text" placeholder="Search tables..."
                                            class="w-full text-sm border rounded-lg px-2.5 py-1.5 dark:bg-gray-700 dark:border-gray-600"
                                            oninput="ChatPage._renderTableList()" onclick="event.stopPropagation()">
                                    </div>
                                    <div class="px-2 pt-2 border-b border-gray-100 dark:border-gray-700">
                                        <label class="flex items-center gap-2 px-1 py-1.5 cursor-pointer text-sm hover:bg-gray-50 dark:hover:bg-gray-700/60 rounded-lg">
                                            <input type="checkbox" id="chat-table-all" class="rounded"
                                                ${this._selectedTables.length === 0 ? 'checked' : ''}
                                                onchange="ChatPage._onAllTablesToggle(this.checked)">
                                            <span class="font-medium">All tables</span>
                                            <span class="text-xs text-gray-400 ml-auto">${this._allTableNames().length} available</span>
                                        </label>
                                    </div>
                                    <div id="chat-table-list" class="max-h-56 overflow-y-auto p-1"></div>
                                    <div class="flex items-center justify-between gap-2 p-2 border-t border-gray-100 dark:border-gray-700">
                                        <span id="chat-table-scope-hint" class="text-[11px] text-gray-400 truncate">No scope — every table reachable</span>
                                        <div class="flex items-center gap-1 shrink-0">
                                            <button onclick="ChatPage._clearTables()" class="text-xs text-gray-400 hover:text-red-500 px-2 py-1 rounded" title="Clear table selection">
                                                <i class="fas fa-times mr-1"></i>Clear
                                            </button>
                                            <button onclick="ChatPage._toggleTablePicker()" class="text-xs bg-primary-500 hover:bg-primary-600 text-white rounded-lg px-3 py-1.5 transition-colors">Done</button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <button onclick="ChatPage._toggleTablePreview()" class="text-sm border rounded-lg px-2.5 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-600 dark:border-gray-600 transition-colors" title="Preview selected tables">
                                <i class="fas fa-table"></i>
                            </button>
                            <select id="chat-tasks" class="text-sm border rounded-lg p-1.5 dark:bg-gray-700 dark:border-gray-600"
                                onchange="ChatPage._onTaskSelect(this.value)">
                                <option value="">Quick tasks...</option>
                                ${this.PREBUILT_TASKS.map(t => `<option value="${App.escapeHtml(t.prompt)}">${App.escapeHtml(t.label)}</option>`).join('')}
                            </select>
                            <button onclick="ChatPage._newConversation()" class="text-sm border rounded-lg px-2.5 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-600 dark:border-gray-600 transition-colors" title="New chat">
                                <i class="fas fa-plus"></i>
                            </button>
                        </div>
                    </div>

                    <!-- Selected-table chips -->
                    ${this._selectedTables.length ? `
                    <div id="chat-chips" class="chat-chips">
                        ${this._selectedTables.map((t, i) => `
                        <span class="inline-flex items-center gap-1.5 bg-primary-50 dark:bg-primary-900/20 text-primary-700 dark:text-primary-300 border border-primary-200 dark:border-primary-800 rounded-full px-2.5 py-0.5 text-xs">
                            ${App.escapeHtml(t)}
                            <button onclick="ChatPage._removeTableByIndex(${i})" class="text-primary-400 hover:text-primary-600 dark:hover:text-primary-200" title="Remove ${App.escapeHtml(t)}"><i class="fas fa-times text-[9px]"></i></button>
                        </span>`).join('')}
                    </div>` : '<div id="chat-chips" class="hidden"></div>'}

                    <!-- Messages -->
                    <div id="chat-messages" class="flex-1 overflow-y-auto py-4 space-y-4 min-h-0"></div>

                    <!-- Loading -->
                    <div id="chat-loading" class="hidden chat-loading">
                        <div class="chat-loading-inner">
                            <div class="chat-loading-dots"><span></span><span></span><span></span></div>
                            <span>Analyzing your question...</span>
                        </div>
                    </div>

                    <!-- Input -->
                    <div class="chat-input-bar">
                        <div class="chat-input-row">
                            <textarea id="chat-input" rows="1"
                                placeholder="Ask about your data..."
                                onkeydown="ChatPage._onKeyDown(event)"
                                oninput="ChatPage._autoResize(this)"></textarea>
                            <button onclick="ChatPage._sendOrStop()" id="chat-send-btn" class="chat-send-btn" title="Send">
                                <i class="fas fa-paper-plane"></i>
                            </button>
                        </div>
                        <div class="chat-input-hint">Enter to send · Shift+Enter for a new line</div>
                    </div>
                </div>
            </main>
        </div>`;

        this._scrollArea = document.getElementById('chat-messages');
        this._renderSidebar();
        this._renderMessages();
        this._updateTableUI();
        this._updateHeaderTitle();
        this._scrollToBottom();
    },

    _renderSidebar() {
        const list = document.getElementById('chat-conv-list');
        if (!list) return;
        const q = (this._searchQuery || '').trim().toLowerCase();
        const filtered = this._conversations.filter(c => {
            if (!q) return true;
            return (c.title || '').toLowerCase().includes(q) ||
                (c.messages || []).some(m => (m.content || '').toLowerCase().includes(q));
        });

        if (!filtered.length) {
            list.innerHTML = `
                <div class="chat-conv-empty">
                    <i class="fas fa-comments"></i>
                    <span>${q ? 'No matching conversations' : 'No conversations yet'}</span>
                </div>`;
            return;
        }

        list.innerHTML = filtered.map(c => {
            const active = c.id === this._activeConversationId;
            const subtitle = this._conversationSubtitle(c);
            const time = this._formatTime(c.updatedAt);
            return `
            <div class="chat-conv-item ${active ? 'active' : ''}" onclick="ChatPage._switchConversation('${c.id}')">
                <div class="conv-title">${App.escapeHtml(c.title || 'New chat')}</div>
                ${subtitle ? `<div class="conv-preview">${App.escapeHtml(subtitle)}</div>` : ''}
                <div class="conv-time">${time}</div>
                <div class="conv-actions" onclick="event.stopPropagation()">
                    <button onclick="ChatPage._startRename('${c.id}')" title="Rename"><i class="fas fa-pen"></i></button>
                    <button onclick="ChatPage._deleteConversation('${c.id}')" title="Delete"><i class="fas fa-trash"></i></button>
                </div>
            </div>`;
        }).join('');
    },

    /**
     * One-line subtitle for a conversation row.
     *
     * It is the user's last question — the assistant's answer is long and
     * noisy (a whole result table), which made the list unreadable. A
     * conversation without a question yet falls back to a short placeholder.
     */
    _conversationSubtitle(conv) {
        const messages = (conv && conv.messages) || [];
        const lastUser = messages.slice().reverse()
            .find(m => m && m.role === 'user' && m.content);
        if (!lastUser) return 'No question yet';
        // Read as plain text: strip code blocks, links, table pipes and
        // markdown emphasis markers.
        const text = String(lastUser.content)
            .replace(/```[\s\S]*?```/g, '[code]')
            .replace(/\[([^\]]+)\]\((?:https?:\/\/)[^\s)]+\)/g, '$1')
            .replace(/\|/g, ' ')
            .replace(/[*#>`~]+/g, '')
            .replace(/\s+/g, ' ')
            .trim();
        if (!text) return 'No question yet';
        return text.length > 60 ? text.slice(0, 60).trimEnd() + '…' : text;
    },

    _startRename(id) {
        const conv = this._conversations.find(c => c.id === id);
        if (!conv) return;
        const items = document.querySelectorAll('.chat-conv-item');
        items.forEach(item => {
            if (item.querySelector('.conv-title')?.textContent === conv.title && item.onclick) {
                // find the matching item by data
            }
        });
        // Simpler: replace the title element with an input
        const list = document.getElementById('chat-conv-list');
        const target = Array.from(list.querySelectorAll('.chat-conv-item')).find(item => {
            const t = item.querySelector('.conv-title');
            return t && t.textContent === (conv.title || 'New chat');
        });
        if (!target) return;
        const titleEl = target.querySelector('.conv-title');
        if (!titleEl) return;
        const input = document.createElement('input');
        input.className = 'conv-rename-input';
        input.value = conv.title || '';
        input.onclick = e => e.stopPropagation();
        input.onkeydown = e => {
            if (e.key === 'Enter') { this._renameConversation(id, input.value); }
            if (e.key === 'Escape') { this._renderSidebar(); }
        };
        input.onblur = () => this._renameConversation(id, input.value);
        titleEl.replaceWith(input);
        input.focus();
        input.select();
    },

    _startRenameHeader() {
        const el = document.getElementById('chat-header-title');
        if (!el || !this._activeConversationId) return;
        el.contentEditable = 'true';
        el.focus();
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        const finish = () => {
            el.contentEditable = 'false';
            this._renameConversation(this._activeConversationId, el.textContent);
            el.removeEventListener('blur', finish);
            el.removeEventListener('keydown', keyHandler);
        };
        const keyHandler = e => {
            if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
            if (e.key === 'Escape') { el.textContent = this._conversations.find(c => c.id === this._activeConversationId)?.title || ''; el.blur(); }
        };
        el.addEventListener('blur', finish);
        el.addEventListener('keydown', keyHandler);
    },

    _formatTime(ts) {
        if (!ts) return '';
        const d = new Date(ts);
        const now = new Date();
        const sameDay = d.toDateString() === now.toDateString();
        if (sameDay) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
        if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
        return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    },

    _toggleSidebar() {
        this._sidebarCollapsed = !this._sidebarCollapsed;
        const sidebar = document.getElementById('chat-sidebar');
        const toggle = document.getElementById('chat-sidebar-toggle');
        if (sidebar) sidebar.classList.toggle('collapsed', this._sidebarCollapsed);
        if (toggle) {
            toggle.classList.toggle('shifted', !this._sidebarCollapsed);
            toggle.querySelector('i').className = 'fas ' + (this._sidebarCollapsed ? 'fa-chevron-right' : 'fa-chevron-left');
        }
        try { localStorage.setItem(this._SIDEBAR_KEY, this._sidebarCollapsed ? 'true' : 'false'); } catch (_) {}
    },

    _onSearch(q) {
        this._searchQuery = q || '';
        this._renderSidebar();
    },

    _renderMessages() {
        const area = document.getElementById('chat-messages');
        if (!area) return;
        if (!this._messages.length) {
            area.innerHTML = `
                <div class="chat-empty-state">
                    <i class="fas fa-comments"></i>
                    <p>Ask me anything about your data</p>
                    <span>I can analyze tables, run queries, find patterns, and more.</span>
                    <div class="chat-suggestions">
                        ${this.PREBUILT_TASKS.slice(0, 6).map(t => `
                            <button class="chat-suggestion" onclick="ChatPage._onSuggestion(this)" data-prompt="${App.escapeHtml(t.prompt)}">
                                <span>${App.escapeHtml(t.label)}</span>
                                <small>${App.escapeHtml(t.prompt)}</small>
                            </button>`).join('')}
                    </div>
                </div>`;
            return;
        }
        area.innerHTML = this._messages.map((m, i) => this._renderBubble(m, i)).join('');
        this._renderAllVizSpecs();
    },

    _onSuggestion(el) {
        const prompt = el && el.dataset ? el.dataset.prompt : '';
        if (prompt) this._answer(prompt);
    },

    _buildViz(vizList) {
        const base = Date.now() + '-' + (++_vizSeq);
        const html = (vizList || []).map((v, i) => {
            if (v.spec) {
                return `<div id="chat-viz-${base}-${i}" class="chat-viz" style="height:420px;width:100%;"></div>`;
            }
            return v.html || '';
        }).join('');
        const specs = (vizList || [])
            .map((v, i) => (v.spec ? { id: `chat-viz-${base}-${i}`, spec: v.spec } : null))
            .filter(Boolean);
        return { html, specs };
    },

    _renderVizSpecs(specs) {
        if (!specs || !specs.length || typeof window.Plotly === 'undefined') return;
        specs.forEach(({ id, spec }) => {
            const el = document.getElementById(id);
            if (!el) return;
            try {
                // The backend spec can be taller (500px) than the 420px card.
                // Fit it to the card so the x-axis is never clipped; fall back
                // to the spec height while the card is still hidden.
                const baseLayout = spec.layout || {};
                const height = el.clientHeight || baseLayout.height || 420;
                Plotly.newPlot(el, spec.data || [],
                    { ...baseLayout, height, autosize: true }, { responsive: true });
            } catch (e) {
                console.error('Plotly render failed:', e);
            }
        });
    },

    _renderAllVizSpecs() {
        if (typeof window.Plotly === 'undefined') return;
        this._messages.forEach((m) => this._renderVizSpecs(m.vizSpecs));
    },

    _renderDisabled() {
        const pc = document.getElementById('page-content');
        const fallback = App.state.features.fallback_to_sql;
        pc.innerHTML = `
        <div class="max-w-2xl mx-auto text-center py-16 animate-fade-in">
            <div class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl p-8">
                <i class="fas fa-robot text-5xl text-amber-500 mb-4"></i>
                <h2 class="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-2">AI Chat Temporarily Disabled</h2>
                <p class="text-gray-600 dark:text-gray-300 mb-6">The LLM-powered chat agent is currently disabled.</p>
                ${fallback ? `<p class="text-gray-600 dark:text-gray-300 mb-6">You can use <strong>SQL-only analysis</strong> via <a href="#sql" onclick="App.navigate('#sql')" class="text-primary-600 hover:underline">SQL Explorer</a>.</p>` : ''}
                <div class="flex justify-center gap-3">
                    <button onclick="App.navigate('#sql')" class="px-6 py-2.5 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition"><i class="fas fa-terminal mr-2"></i>Open SQL Explorer</button>
                    <button onclick="App.navigate('#analysis')" class="px-6 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition"><i class="fas fa-chart-line mr-2"></i>Analysis Suite</button>
                </div>
            </div>
        </div>`;
    },

    // ══════════════════════════════════════════════════════════════════
    //  Message bubble rendering
    // ══════════════════════════════════════════════════════════════════

    _renderBubble(msg, index) {
        const isUser = msg.role === 'user';
        const isLast = index === undefined || index === this._messages.length - 1;
        return `
        <div class="flex ${isUser ? 'justify-end' : 'justify-start'} group message ${isUser ? 'user' : 'assistant'}${msg.vizHtml ? ' has-viz' : ''}">
            ${isUser ? '' : '<div class="msg-avatar"><i class="fas fa-robot"></i></div>'}
            <div class="bubble">
                ${msg.vizHtml ? `<div class="mb-2">${msg.vizHtml}</div>` : ''}
                <div class="msg-body">${this._renderMarkdown(msg.content)}</div>
                <div class="msg-time">
                    <span>${msg.time || ''}</span>
                    <span class="msg-actions">
                        ${isUser && !this._isWaiting ? `<button onclick="ChatPage._editMessage(${index})" title="Edit and resend"><i class="fas fa-pen"></i></button>` : ''}
                        <button onclick="ChatPage._copyMessage(${index})" title="Copy message"><i class="fas fa-copy"></i></button>
                        ${!isUser && isLast ? `<button onclick="ChatPage._regenerate()" title="Regenerate answer"><i class="fas fa-redo"></i></button>` : ''}
                    </span>
                </div>
            </div>
        </div>`;
    },

    // ══════════════════════════════════════════════════════════════════
    //  Markdown → HTML  (code blocks, inline code, bold, SQL highlight)
    // ══════════════════════════════════════════════════════════════════

    _renderMarkdown(text) {
        if (!text) return '';
        let html = App.escapeHtml(text);

        // ── Fenced code blocks ── (placeheld so later transforms can't
        //    touch their content; SQL keywords highlighted at restore)
        const preBlocks = [];
        const codeBlock = (lang, code) => {
            const label = (lang || 'code').toLowerCase() || 'code';
            preBlocks.push(
                `<div class="code-block"><div class="code-head"><span class="code-lang">${label}</span>` +
                `<button class="code-copy" type="button" onclick="ChatPage._copyCode(this)"><i class="fas fa-copy"></i> Copy</button></div>` +
                `<pre><code>${code}</code></pre></div>`);
            return `%%PRE${preBlocks.length - 1}%%`;
        };
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => codeBlock(lang, App.escapeHtml(code)));
        html = html.replace(/```(\w*)\n([\s\S]*?)$/, (_, lang, code) => codeBlock(lang, App.escapeHtml(code)));

        // ── Inline code ──
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // ── Query result header (e.g. "Query returned 5 row(s) × 2 column(s):") ──
        html = html.replace(/\*\*Query returned (\d+) row\(s\) × (\d+) column\(s\):\*\*/g,
            '<div class="chat-query-meta"><i class="fas fa-table"></i>Query returned <strong>$1</strong> row(s) × <strong>$2</strong> column(s)</div>');

        // ── Highlights block → summary card ──
        html = html.replace(/\*\*Highlights:\*\*[\r\n]+((?:- .*[\r\n]*)+)/g, (m, items) => {
            const rows = items.trim().split(/\r?\n/).map(line => {
                const li = line.replace(/^- /, '');
                const mm = li.match(/^\*\*([^*]+):\*\*\s*(.+)$/);
                if (mm) {
                    return `<div class="hl-row"><span class="hl-label">${mm[1]}</span><span class="hl-value">${mm[2]}</span></div>`;
                }
                return `<div class="hl-row"><span class="hl-label">${li}</span></div>`;
            }).join('');
            return `<div class="chat-highlights">${rows}</div>`;
        });

        // ── Bold ──
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

        // ── Markdown tables (pipe-delimited, e.g. data answers) ──
        html = html.replace(/\|(.+)\|[\r\n]+\|[\s:|-]+\|[\r\n]+((?:\|.*\|[\r\n]*)+)/g, (m, header, body) => {
            const cells = row => row.trim().replace(/^\|+|\|+$/g, '').split('|').map(c => c.trim()).filter((c, i, a) => !(i === 0 && c === '' && a.length > 1));
            const h = cells(header);
            const rows = body.trim().split(/\r?\n/).filter(r => r.trim().startsWith('|'));
            const rowsHtml = rows.map(r => {
                const cellsHtml = cells(r).map(c => `<td class="px-3 py-1.5 whitespace-nowrap">${c || ''}</td>`).join('');
                return `<tr>${cellsHtml}</tr>`;
            }).join('');
            const headHtml = h.map(c => `<th class="px-3 py-1.5 text-left font-semibold">${c}</th>`).join('');
            return `<div class="overflow-x-auto my-2"><table class="chat-table">` +
                   `<thead>${headHtml}</thead><tbody>${rowsHtml}</tbody></table></div>`;
        });

        // ── Headings (#, ##, ###) ──
        html = html.replace(/(?:^|\n)(#{1,3}) +([^\n]+)/g, (m, hashes, title) =>
            `\n<div class="chat-h chat-h${hashes.length}">${title}</div>`);

        // ── Bullet lists ──
        html = html.replace(/(?:^|\n)(?:-|\*) (.+)(?:\n(?:-|\*) .+)*/g, (m) => {
            const items = m.trim().split('\n').map(l => l.replace(/^(?:-|\*) /, '')).map(i => `<li>${i}</li>`).join('');
            return `\n<ul class="chat-list">${items}</ul>`;
        });

        // ── Ordered lists ──
        html = html.replace(/(?:^|\n)(\d+)\. (.+)(?:\n\d+\. .+)*/g, (m) => {
            const items = m.trim().split('\n').map(l => l.replace(/^\d+\. /, '')).map(i => `<li>${i}</li>`).join('');
            return `\n<ol class="chat-list chat-ol">${items}</ol>`;
        });

        // ── Blockquotes ──
        html = html.replace(/(?:^|\n)&gt; ([^\n]+(?:\n&gt; [^\n]+)*)/g, (m) => {
            const inner = m.trim().split('\n').map(l => l.replace(/^&gt; ?/, '')).join('<br>');
            return `\n<blockquote class="chat-quote">${inner}</blockquote>`;
        });

        // ── Horizontal rule ──
        html = html.replace(/(?:^|\n)-{3,}(?=\n|$)/g, '\n<hr class="chat-hr">');

        // ── Source line → muted footer ──
        html = html.replace(/\*Source: ([^*]+)\*/g,
            '<div class="chat-source"><i class="fas fa-database"></i><span>Source: <strong>$1</strong></span></div>');

        // ── Links (http/https only) ──
        html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer" class="chat-link">$1</a>');

        // ── Italic ──
        html = html.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');

        // ── Strikethrough ──
        html = html.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');

        // ── Line breaks (code blocks still placeheld → kept verbatim) ──
        html = html.replace(/\n/g, '<br>');

        // ── Restore code blocks, highlighting SQL keywords inside ──
        const kws = ['SELECT','FROM','WHERE','GROUP BY','ORDER BY','HAVING','JOIN','LEFT JOIN','RIGHT JOIN',
            'INNER JOIN','OUTER JOIN','ON','AND','OR','NOT','INSERT','UPDATE','DELETE','CREATE','DROP',
            'ALTER','TABLE','AS','COUNT','SUM','AVG','MIN','MAX','DISTINCT','LIMIT','UNION','CASE','WHEN','THEN','ELSE','END'];
        preBlocks.forEach((blockHtml, i) => {
            const withKw = blockHtml.replace(/(<pre[^>]*>\s*<code>)([\s\S]*?)(<\/code>\s*<\/pre>)/g, (match, open, inner, close) => {
                let h = inner;
                kws.forEach(kw => {
                    h = h.replace(new RegExp('\\b(' + kw.replace(' ', '\\s+') + ')\\b', 'gi'),
                        '<span class="text-blue-400 font-semibold">$1</span>');
                });
                return open + h + close;
            });
            html = html.replace(`%%PRE${i}%%`, () => withKw);
        });

        return html;
    },

    // ══════════════════════════════════════════════════════════════════
    //  SQL generation → SQL Explorer routing
    // ══════════════════════════════════════════════════════════════════

    /** Detect questions that ask to *generate* SQL (not questions about data). */
    _isSqlGenerationRequest(text) {
        const q = (text || '').trim().toLowerCase();
        if (!q) return false;
        if (/\b(generate|create|write|build|make|produce|construct|draft)\b/.test(q) &&
            /\b(sql|query|statement|script)\b/.test(q)) return true;
        if (/\bsql\b/.test(q) && /\b(code|query|statement|syntax)\b/.test(q)) return true;
        if (/\b(show|give|get|tell)\b[^.]{0,40}\bthe sql\b/.test(q)) return true;
        return false;
    },

    /** Show a bubble directing the user to the SQL Explorer's AI generator. */
    _handleSqlGenerationRequest(text) {
        try { sessionStorage.setItem('pending_ai_sql_prompt', text); } catch (_) {}

        this._isWaiting = false;
        this._updateSendBtn();
        this._showLoading(false);

        const content =
            "SQL query generation lives in the **SQL Explorer** 🗄️\n\n" +
            "It has an **AI generator** that writes the SQL for you from a plain-English " +
            "description (look for the **Visual Query Builder → AI Generate** button).\n\n" +
            "Click **Open SQL Explorer** below to get started.";
        this._addMessage('assistant', content);

        const area = document.getElementById('chat-messages');
        const bubbles = area?.querySelectorAll('.message.assistant .bubble');
        const last = bubbles?.[bubbles.length - 1];
        if (last) {
            const btn = document.createElement('div');
            btn.className = 'mt-2 flex justify-end';
            btn.innerHTML = `<button onclick="ChatPage._openSqlExplorer()"
                class="text-xs text-white px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 transition-colors
                       flex items-center gap-1.5 font-semibold shadow-sm">
                <i class="fas fa-terminal"></i> Open SQL Explorer
            </button>`;
            last.appendChild(btn);
        }
        this._scrollToBottom();
    },

    _openSqlExplorer() {
        App.navigate('#sql');
    },

    // ══════════════════════════════════════════════════════════════════
    //  Send message
    // ══════════════════════════════════════════════════════════════════

    _sendOrStop() {
        if (this._isWaiting) this._stop();
        else this._send();
    },

    /** Abort the in-flight stream; the partial answer is kept, not discarded. */
    _stop() {
        this._stopped = true;
        this._showLoading(false);
        if (this._abortCtrl) { try { this._abortCtrl.abort(); } catch (_) {} }
    },

    async _send() {
        if (this._isWaiting) return;
        const input = document.getElementById('chat-input');
        const text = (input?.value || '').trim();
        if (!text) return;
        input.value = '';
        input.style.height = 'auto';
        await this._answer(text);
    },

    /** Re-run the last user question: drop its answer(s) and ask again. */
    async _regenerate() {
        if (this._isWaiting) return;
        const lastUser = this._messages.slice().reverse().find(m => m.role === 'user');
        if (!lastUser) return;
        const idx = this._messages.lastIndexOf(lastUser);
        this._messages.splice(idx);
        this._saveHistory();
        this._renderMessages();
        await this._answer(lastUser.content);
    },

    async _answer(text) {
        this._addMessage('user', text);
        this._isWaiting = true;
        this._stopped = false;
        this._abortCtrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        this._updateSendBtn();
        this._showLoading(true);

        if (this._isSqlGenerationRequest(text)) {
            this._abortCtrl = null;
            this._handleSqlGenerationRequest(text);
            return;
        }

        const tables = this._selectedTables.slice();
        const question = tables.length === 1 ? `Using the "${tables[0]}" table: ${text}` : text;
        const history = this._messages
            .filter(m => m.role === 'user' || m.role === 'assistant')
            .map(m => ({ role: m.role, content: m.content }));

        try {
            let streamed = '';
            let usedStream = false;

            // ── Try SSE streaming first ──
            let bubbleEl = null;
            try {
                const streamGen = API.askStream(question, history, this._sessionId, tables,
                    this._abortCtrl ? this._abortCtrl.signal : undefined);
                const area = document.getElementById('chat-messages');

                const emptyState = area?.querySelector('.chat-empty-state');
                if (emptyState) emptyState.remove();

                bubbleEl = document.createElement('div');
                bubbleEl.className = 'flex justify-start message assistant';
                bubbleEl.innerHTML = `
                    <div class="msg-avatar"><i class="fas fa-robot"></i></div>
                    <div class="bubble">
                        <div id="stream-viz"></div>
                        <div class="msg-body" id="stream-content"></div>
                        <div class="msg-time" id="stream-time"></div>
                    </div>`;
                area.appendChild(bubbleEl);
                this._scrollToBottom(true);

                const streamEl = document.getElementById('stream-content');
                const vizEl = document.getElementById('stream-viz');
                let vizHtml = '';
                let vizSpecs = [];
                for await (const token of streamGen) {
                    if (token && typeof token === 'object' && token.__visualizations) {
                        const built = this._buildViz(token.__visualizations);
                        vizHtml = built.html;
                        vizSpecs = built.specs;
                        if (vizEl) vizEl.innerHTML = vizHtml;
                        this._renderVizSpecs(vizSpecs);
                        bubbleEl.classList.add('has-viz');
                        this._showLoading(false);
                        this._scrollToBottom();
                        continue;
                    }
                    streamed += token;
                    this._showLoading(false);
                    if (streamEl) streamEl.innerHTML = this._renderMarkdown(streamed) + '<span class="stream-caret"></span>';
                    this._scrollToBottom();
                }

                if (!streamed.trim()) {
                    throw new Error('Empty stream response');
                }

                usedStream = true;
                this._finalizeStreamBubble(bubbleEl, streamed, vizHtml, vizSpecs);

            } catch (streamErr) {
                // User pressed Stop: keep whatever streamed and NEVER fall
                // back to /ask, which would re-run the whole question.
                if (this._stopped) {
                    usedStream = true;
                    if (bubbleEl && streamed.trim()) {
                        this._finalizeStreamBubble(bubbleEl, streamed + '\n\n*(stopped)*', '', []);
                    } else if (bubbleEl) {
                        bubbleEl.remove();
                    }
                } else {
                    const partial = document.getElementById('stream-content');
                    if (partial) partial.closest('.flex')?.remove();
                    usedStream = false;
                }
            }

            // ── Fallback: non-streaming /ask ──
            if (!usedStream) {
                // Stop must cancel the fallback request too, not only the SSE
                // stream: the signal goes all the way into fetch(), so the
                // server stops working on a turn nobody is waiting for.
                const signal = this._abortCtrl ? this._abortCtrl.signal : undefined;
                try {
                    const result = await API.ask(question, { tables, sessionId: this._sessionId, signal });
                    const answer = (result.answer || '').trim();

                    if (result.session_id) this._sessionId = result.session_id;

                    // Render the charts the pipeline returned — dropping them here
                    // left "see chart above" with no chart whenever streaming was
                    // unavailable (it always was while askStream was unauthenticated).
                    const built = this._buildViz(result.visualizations || []);
                    // _addMessage stores the specs and renders them once (a second
                    // _renderVizSpecs here would Plotly-purge then re-plot).
                    this._addMessage('assistant', answer || result.error || 'No results returned.', built);
                } catch (askErr) {
                    const aborted = this._stopped
                        || (askErr && (askErr.name === 'AbortError' || askErr.code === 20));
                    if (!aborted) throw askErr;
                    this._addMessage('assistant', 'Generation stopped.');
                }
            }

        } catch (e) {
            this._addMessage('assistant', 'Sorry, I encountered an error: ' + (e.message || 'Unknown error') + '\n\nPlease try rephrasing your question or check your connection.');
        } finally {
            this._isWaiting = false;
            this._abortCtrl = null;
            this._stopped = false;
            this._updateSendBtn();
            this._showLoading(false);
            this._refreshLastBubbles();
        }
    },

    /** Re-render the trailing user + assistant bubbles now that the request
     *  finished, so their hover actions reflect the idle state (edit button
     *  on the user message, regenerate on the answer). */
    _refreshLastBubbles() {
        if (!this._messages.length) return;
        const area = document.getElementById('chat-messages');
        if (!area) return;
        const els = area.querySelectorAll('.message');
        const targets = new Set([this._messages.length - 1]);
        const lastUserIdx = this._messages.map((m, i) => m.role === 'user' ? i : -1).filter(i => i >= 0).pop();
        if (lastUserIdx !== undefined) targets.add(lastUserIdx);
        targets.forEach(i => {
            const el = els[i];
            if (!el) return;
            const temp = document.createElement('div');
            temp.innerHTML = this._renderBubble(this._messages[i], i);
            el.replaceWith(temp.firstElementChild);
            const msg = this._messages[i];
            if (msg.vizSpecs && msg.vizSpecs.length) this._renderVizSpecs(msg.vizSpecs);
        });
    },

    // ══════════════════════════════════════════════════════════════════
    //  Deduplication — clean, single-purpose functions
    // ══════════════════════════════════════════════════════════════════

    _dedupeCodeBlocks(text) {
        if (!text || text.length < 50) return text;
        const norm = s => (s || '').trim().replace(/\s+/g, ' ').toLowerCase();

        const lines = text.split('\n');
        const unique = [];
        for (let i = 0; i < lines.length; i++) {
            if (i > 0 && lines[i].trim() === lines[i - 1].trim() && lines[i].trim() !== '') continue;
            unique.push(lines[i]);
        }
        text = unique.join('\n');

        const blocks = [];
        text = text.replace(/```[\s\S]*?```/g, m => { blocks.push(m); return `\x00B${blocks.length - 1}\x00`; });
        const seen = new Set();
        for (let i = 0; i < blocks.length; i++) {
            const n = norm(blocks[i]);
            if (seen.has(n)) {
                text = text.replace('\x00B' + i + '\x00', '');
            } else {
                seen.add(n);
                text = text.replace('\x00B' + i + '\x00', blocks[i]);
            }
        }

        const paras = text.split(/\n{2,}/);
        const seenP = new Set();
        const dedupP = [];
        for (const p of paras) {
            const n = norm(p);
            if (n.length < 25 || !seenP.has(n)) { seenP.add(n); dedupP.push(p); }
        }
        text = dedupP.join('\n\n');

        text = text.replace(/\n{3,}/g, '\n\n').trim();

        const mid = Math.floor(text.length / 2);
        if (mid > 80) {
            const a = text.substring(0, mid).trim();
            const b = text.substring(mid).trim();
            if (a === b) { text = a; }
            else if (a.length > 50 && b.length > 50) {
                const n = Math.floor(Math.min(a.length, b.length) * 0.85);
                if (n > 40 && a.substring(0, n) === b.substring(0, n)) text = a;
            }
        }
        return text;
    },

    // ══════════════════════════════════════════════════════════════════
    //  Message management
    // ══════════════════════════════════════════════════════════════════

    _addMessage(role, content, viz) {
        const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const msg = { role, content, time };
        if (viz && viz.html) {
            msg.vizHtml = viz.html;
            msg.vizSpecs = viz.specs || [];
        }
        this._messages.push(msg);
        this._saveHistory();
        this._updateConversationTitle();

        const area = document.getElementById('chat-messages');
        if (!area) return;

        const empty = area.querySelector('.chat-empty-state');
        if (empty) empty.remove();

        const wrapper = document.createElement('div');
        wrapper.innerHTML = this._renderBubble(msg, this._messages.length - 1);
        area.appendChild(wrapper.firstElementChild);
        if (msg.vizSpecs && msg.vizSpecs.length) this._renderVizSpecs(msg.vizSpecs);
        this._scrollToBottom();
    },

    /** Promote a streamed bubble to a finalized message: dedupe, persist,
     *  and re-render it through the standard bubble template (adds avatar
     *  actions + markdown polish that streaming skips for speed). */
    _finalizeStreamBubble(bubbleEl, streamed, vizHtml, vizSpecs) {
        const finalContent = this._dedupeCodeBlocks(streamed);
        const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const msg = { role: 'assistant', content: finalContent, time };
        if (vizHtml) { msg.vizHtml = vizHtml; msg.vizSpecs = vizSpecs || []; }
        this._messages.push(msg);
        this._saveHistory();

        const temp = document.createElement('div');
        temp.innerHTML = this._renderBubble(msg, this._messages.length - 1);
        if (bubbleEl && bubbleEl.parentNode) bubbleEl.replaceWith(temp.firstElementChild);
        if (msg.vizSpecs && msg.vizSpecs.length) this._renderVizSpecs(msg.vizSpecs);
        this._scrollToBottom();
    },

    _copyMessage(index) {
        const msg = this._messages[index];
        if (msg) this._copyText(msg.content || '');
    },

    _copyCode(btn) {
        const block = btn && btn.closest ? btn.closest('.code-block') : null;
        const code = block ? block.querySelector('pre code') : null;
        this._copyText(code ? code.textContent : '');
    },

    _copyText(text) {
        if (!text) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text)
                .then(() => App.toast('Copied to clipboard', 'success'))
                .catch(() => this._copyFallback(text));
        } else {
            this._copyFallback(text);
        }
    },

    _copyFallback(text) {
        try {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
            App.toast('Copied to clipboard', 'success');
        } catch (_) {
            App.toast('Copy failed', 'error');
        }
    },

    _saveHistory() {
        // Persist the active conversation's messages into the conversations array.
        if (!this._activeConversationId) return;
        const conv = this._conversations.find(c => c.id === this._activeConversationId);
        if (conv) {
            conv.messages = this._messages.slice(-100);
            conv.updatedAt = Date.now();
            this._saveConversations();
            this._renderSidebar();
            this._syncMessages(conv);
        }
    },

    _clearChat() {
        this._messages = [];
        this._lastPreview = null;
        this._storedPreviewData = null;
        this._saveHistory();
        this._renderMessages();
        App.toast('Chat cleared', 'info');
    },

    _editMessage(index) {
        if (this._isWaiting || index < 0 || index >= this._messages.length) return;
        const msg = this._messages[index];
        if (!msg || msg.role !== 'user') return;

        // Capture the content BEFORE splicing — after splice(index) the
        // message at `index` is gone, so reading it afterwards cleared the
        // input box instead of restoring the text being edited.
        const content = msg.content || '';
        this._messages.splice(index);
        this._saveHistory();

        const input = document.getElementById('chat-input');
        if (input) { input.value = content; input.focus(); this._autoResize(input); }

        this._renderMessages();
        this._scrollToBottom(true);
        App.toast('Editing previous message — press Enter to resend', 'info');
    },

    // ══════════════════════════════════════════════════════════════════
    //  Table preview toggle
    // ══════════════════════════════════════════════════════════════════

    _showTablePreview: false,
    async _toggleTablePreview() {
        const tableNames = this._selectedTables.slice();
        if (!tableNames.length) return App.toast('Select one or more tables to preview', 'info');

        this._showTablePreview = !this._showTablePreview;
        const area = document.getElementById('chat-messages');
        if (!area) return;

        if (this._showTablePreview) {
            const existing = document.getElementById('chat-table-preview');
            if (existing) existing.remove();

            const previewEl = document.createElement('div');
            previewEl.id = 'chat-table-preview';
            previewEl.className = 'mb-2';
            previewEl.innerHTML = '<div class="text-center py-4 text-gray-400 text-sm"><i class="fas fa-spinner fa-spin"></i> Loading preview...</div>';
            area.insertBefore(previewEl, area.firstChild);

            try {
                let html = '';
                for (const tableName of tableNames) {
                    const [profile, sample] = await Promise.all([
                        API.profile(tableName).catch(() => null),
                        API.tableSample(tableName, 5).catch(() => null),
                    ]);
                    html += this._renderTablePreviewCard(tableName, profile, sample);
                }
                if (!previewEl.parentNode) return;
                previewEl.innerHTML = html;
            } catch (e) {
                previewEl.innerHTML = `<div class="text-center py-3 text-sm text-red-400">Failed to load preview: ${App.escapeHtml(e.message || 'Error')}</div>`;
            }
        } else {
            document.getElementById('chat-table-preview')?.remove();
        }
    },

    _renderTablePreviewCard(tableName, profile, sample) {
        // /profile returns { success, profile: {...} } — unwrap the nested
        // payload, but tolerate a flat one.
        const prof = (profile && profile.profile) || profile || {};
        const rawCols = prof.columns || {};
        // columns arrive as an array of {name, dtype, type} or a name→info map
        const cols = Array.isArray(rawCols)
            ? rawCols.reduce((acc, c) => { if (c && c.name) acc[c.name] = c; return acc; }, {})
            : rawCols;
        const rows = sample?.data || sample?.rows || [];
        const rowCount = prof.row_count || 0;

        let html = `<div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden mb-2">
            <div class="flex items-center justify-between px-4 py-2.5 bg-gray-50 dark:bg-gray-700/30 border-b border-gray-200 dark:border-gray-700">
                <span class="text-xs font-semibold text-gray-500 dark:text-gray-400">
                    <i class="fas fa-table mr-1.5"></i>${App.escapeHtml(tableName)} — ${rowCount.toLocaleString()} rows, ${Object.keys(cols).length} columns
                </span>
                <button onclick="ChatPage._toggleTablePreview()" class="text-gray-400 hover:text-red-500 text-xs"><i class="fas fa-times"></i></button>
            </div>
            <div class="overflow-x-auto max-h-[200px] overflow-y-auto">
                <table class="w-full text-xs border-collapse">`;

        if (rows.length > 0) {
            const colNames = Object.keys(rows[0]);
            html += '<thead><tr class="bg-gray-100 dark:bg-gray-700/50">';
            colNames.forEach(c => {
                const info = cols[c];
                const badge = info ? ` <span class="text-[9px] text-gray-400 ml-1">${info.type || info.dtype || ''}</span>` : '';
                html += `<th class="px-2.5 py-2 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">${App.escapeHtml(c)}${badge}</th>`;
            });
            html += '</tr></thead><tbody>';
            rows.forEach(row => {
                html += '<tr class="border-t border-gray-100 dark:border-gray-700/50">';
                colNames.forEach(c => {
                    let val = row[c];
                    if (val === null || val === undefined) val = '<span class="text-gray-300 italic">null</span>';
                    else if (typeof val === 'number') val = Number.isInteger(val) ? val.toLocaleString() : val.toFixed(2);
                    else val = App.escapeHtml(String(val).slice(0, 50));
                    html += `<td class="px-2.5 py-1.5 text-gray-600 dark:text-gray-400 whitespace-nowrap">${val}</td>`;
                });
                html += '</tr>';
            });
            html += '</tbody>';
        }
        html += '</table></div></div>';
        return html;
    },

    // ══════════════════════════════════════════════════════════════════
    //  Multi-select table picker
    // ══════════════════════════════════════════════════════════════════

    _allTableNames() {
        const tables = App.state.tables || {};
        if (Array.isArray(tables)) return tables.slice();
        return typeof tables === 'object' && tables !== null ? Object.keys(tables) : [];
    },

    _toggleTablePicker() {
        const panel = document.getElementById('chat-table-panel');
        if (!panel) return;
        const willOpen = panel.classList.contains('hidden');
        panel.classList.toggle('hidden', !willOpen);
        if (willOpen) {
            this._renderTableList();
            const search = document.getElementById('chat-table-search');
            if (search) search.value = '';
            const allEl = document.getElementById('chat-table-all');
            if (allEl) allEl.checked = this._selectedTables.length === 0;
            if (!this._boundClose) this._boundClose = (e) => this._closePickerHandler(e);
            setTimeout(() => document.addEventListener('click', this._boundClose), 0);
        } else {
            if (this._boundClose) document.removeEventListener('click', this._boundClose);
        }
    },

    _closePickerHandler(e) {
        const picker = document.getElementById('chat-table-picker');
        const panel = document.getElementById('chat-table-panel');
        if (picker && panel && !picker.contains(e.target)) {
            panel.classList.add('hidden');
            if (this._boundClose) document.removeEventListener('click', this._boundClose);
        }
    },

    _renderTableList() {
        const list = document.getElementById('chat-table-list');
        if (!list) return;
        const searchEl = document.getElementById('chat-table-search');
        const q = (searchEl?.value || '').trim().toLowerCase();
        const tables = this._allTableNames().filter(t => !q || t.toLowerCase().includes(q));
        const sel = new Set(this._selectedTables.map(x => x.toLowerCase()));
        const allEl = document.getElementById('chat-table-all');
        if (allEl) allEl.checked = this._selectedTables.length === 0;

        if (!tables.length) {
            list.innerHTML = '<div class="text-center text-xs text-gray-400 py-4">No matching tables</div>';
            return;
        }
        list.innerHTML = tables.map(t => {
            // Checked ⇔ explicitly scoped to (inclusive selection).
            const checked = sel.has(t.toLowerCase());
            const rows = this._tableRowCount(t);
            return `<label class="flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer text-sm hover:bg-gray-50 dark:hover:bg-gray-700/60">
                <input type="checkbox" class="rounded" data-table="${App.escapeHtml(t)}" ${checked ? 'checked' : ''} onchange="ChatPage._onTableCheck(this)">
                <span class="truncate">${App.escapeHtml(t)}</span>
                ${rows != null ? `<span class="text-xs text-gray-400 ml-auto whitespace-nowrap">${rows.toLocaleString()} rows</span>` : ''}
            </label>`;
        }).join('');
    },

    /** Row count for a table when the warehouse metadata knows it. */
    _tableRowCount(name) {
        const tables = App.state.tables || {};
        if (!tables || Array.isArray(tables) || typeof tables !== 'object') return null;
        let info = tables[name];
        if (!info) {
            const lower = String(name || '').toLowerCase();
            const key = Object.keys(tables).find(k => k.toLowerCase() === lower);
            if (key) info = tables[key];
        }
        if (!info || typeof info !== 'object') return null;
        const n = info.row_count != null ? info.row_count : info.rows;
        return (typeof n === 'number' && n >= 0) ? n : null;
    },

    _onTableCheck(el) {
        const name = el && el.dataset && el.dataset.table;
        if (!name) return;
        const lower = name.toLowerCase();
        const idx = this._selectedTables.findIndex(t => t.toLowerCase() === lower);

        // The selection is INCLUSIVE (the tables the user wants the agent to
        // use). It used to be an EXCLUSION set — "all tables EXCEPT the ones
        // I unchecked" — which made a single-table warehouse unscopable:
        // unchecking the only table produced an empty exclusion set, i.e.
        // "All tables" again.
        if (el.checked) {
            if (idx === -1) this._selectedTables.push(name);
        } else if (idx !== -1) {
            this._selectedTables.splice(idx, 1);
        }
        this._persistTables();
        this._updateTableUI();
        this._renderTableList();
    },

    _onAllTablesToggle(checked) {
        // "All tables" is the no-scope option: checking it clears the explicit
        // scope; unchecking it starts an explicit scope over every table
        // (the per-table checkboxes then narrow it down).
        this._selectedTables = checked ? [] : this._allTableNames();
        this._persistTables();
        this._updateTableUI();
        this._renderTableList();
    },

    _removeTableByIndex(i) {
        if (i >= 0 && i < this._selectedTables.length) {
            this._selectedTables.splice(i, 1);
            this._persistTables();
            this._updateTableUI();
            this._renderTableList();
        }
    },

    _clearTables() {
        this._selectedTables = [];
        this._persistTables();
        this._updateTableUI();
        this._renderTableList();
    },

    _persistTables() {
        try { sessionStorage.setItem(this._TABLES_KEY, JSON.stringify(this._selectedTables)); } catch (_) {}
    },

    /** The stored scope, read back safely (sessionStorage may be blocked). */
    _readStoredTables() {
        try {
            const raw = sessionStorage.getItem(this._TABLES_KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed.filter(t => typeof t === 'string') : [];
        } catch (_) {
            return [];
        }
    },

    /**
     * Persist the scope when the page is torn down.
     *
     * An empty in-memory scope only means "All tables" when the user asked
     * for it — the picker persists that immediately. If the scope is empty
     * while a scope is still stored, the table list was simply not ready (or
     * the restore had not run yet) and the stored scope has to survive.
     */
    _persistScopeOnExit() {
        const stored = this._readStoredTables();
        if (!this._selectedTables.length && stored.length) return;
        this._persistTables();
    },

    /**
     * Is the table list known to be loaded?
     *
     * App.refreshTables() swallows its own errors and App.state.tables starts
     * out as {}, so an empty list is ambiguous: the fetch may not have
     * finished, may have failed, or the workspace may really have no tables.
     * Only a list that actually carries tables can be trusted for pruning.
     */
    _tableListLoaded() {
        return this._allTableNames().length > 0;
    },

    /**
     * Drop selected tables that no longer exist in the warehouse.
     *
     * The scope is the user's explicit choice, so it is only pruned against a
     * table list we know arrived. Pruning against a list that never arrived
     * would clear the scope and silently lose the selection.
     */
    _pruneSelectedTables() {
        if (!this._tableListLoaded()) return false;   // no usable list → keep the scope
        const known = new Set(this._allTableNames().map(x => String(x).toLowerCase()));
        const before = this._selectedTables.length;
        this._selectedTables = this._selectedTables.filter(t => known.has(String(t).toLowerCase()));
        if (this._selectedTables.length === before) return false;
        this._persistTables();
        return true;
    },

    _updateTableUI() {
        const label = document.getElementById('chat-table-btn-label');
        if (label) {
            label.textContent = this._selectedTables.length === 0
                ? 'All tables'
                : this._selectedTables.length === 1
                    ? this._selectedTables[0]
                    : `${this._selectedTables.length} tables selected`;
        }
        const hint = document.getElementById('chat-table-scope-hint');
        if (hint) {
            hint.textContent = this._selectedTables.length === 0
                ? 'No scope — every table reachable'
                : `${this._selectedTables.length} selected`;
        }
        const allEl = document.getElementById('chat-table-all');
        if (allEl) allEl.checked = this._selectedTables.length === 0;
        const chips = document.getElementById('chat-chips');
        if (chips) {
            if (this._selectedTables.length) {
                chips.className = 'chat-chips';
                chips.innerHTML = this._selectedTables.map((t, i) => `
                    <span class="inline-flex items-center gap-1.5 bg-primary-50 dark:bg-primary-900/20 text-primary-700 dark:text-primary-300 border border-primary-200 dark:border-primary-800 rounded-full px-2.5 py-0.5 text-xs">
                        ${App.escapeHtml(t)}
                        <button onclick="ChatPage._removeTableByIndex(${i})" class="text-primary-400 hover:text-primary-600 dark:hover:text-primary-200" title="Remove ${App.escapeHtml(t)}"><i class="fas fa-times text-[9px]"></i></button>
                    </span>`).join('');
            } else {
                chips.className = 'hidden';
                chips.innerHTML = '';
            }
        }
    },

    // ══════════════════════════════════════════════════════════════════
    //  UI helpers
    // ══════════════════════════════════════════════════════════════════

    _showLoading(show) { document.getElementById('chat-loading')?.classList.toggle('hidden', !show); },
    _updateSendBtn() {
        const b = document.getElementById('chat-send-btn');
        if (!b) return;
        const stop = this._isWaiting;
        b.classList.toggle('stop', stop);
        b.title = stop ? 'Stop generating' : 'Send';
        const icon = b.querySelector('i');
        if (icon) icon.className = 'fas ' + (stop ? 'fa-stop' : 'fa-paper-plane');
    },
    _scrollToBottom(force) {
        if (!this._scrollArea) return;
        const el = this._scrollArea;
        // Auto-follow only while the user is near the bottom; if they have
        // scrolled up to read, don't yank the viewport back down.
        if (!force && (el.scrollHeight - el.scrollTop - el.clientHeight) > 140) return;
        requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    },
    _autoResize(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'; },
    _onKeyDown(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!this._isWaiting) this._send(); } },
    _onTaskSelect(prompt) { if (prompt) { const i = document.getElementById('chat-input'); if (i) { i.value = prompt; this._send(); } } },
};

// Register with SPA router + expose globally for onclick handlers
window.ChatPage = ChatPage;
(window._PAGES = window._PAGES || {})["#chat"] = ChatPage;
