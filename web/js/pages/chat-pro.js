/**
 * ChatProPage — rebuilt conversational analyst UI (route #chat-pro)
 * ═══════════════════════════════════════════════════════════════════════
 * A from-scratch chat experience that lives beside the legacy #chat page
 * (which stays untouched). Registered as window._PAGES['#chat-pro'] and
 * lazily loaded by the SPA router, so it reaches the browser through the
 * same init() → render() → destroy() contract as every other page.
 *
 * What it does
 *  • Streams answers from /llm/chat/stream (SSE) with a hard fallback to
 *    /ask so a question is never left unanswered by a dead stream.
 *  • Plotly visualizations from {__visualizations} stream events and the
 *    non-streaming /ask response, with a render-retry until Plotly loads.
 *  • Conversation sidebar: date-grouped, searchable, rename/delete inline,
 *    collapsed state and scope selection persisted per login.
 *  • Table scope picker (multi-select popover) mirrored into request tables.
 *  • Markdown-ish renderer: fenced code (with copy), tables, lists,
 *    quotes, headings, links — all HTML-escaped before injection.
 *  • Stop, regenerate, edit-and-resend, copy message / whole conversation.
 *  • Best-effort server sync via /user/conversations so chats follow the
 *    account; silently stays local-only when the API is unavailable.
 *
 * Design notes
 *  • Own stylesheet (css/chat-pro.css, .cpx-* namespace) injected on boot;
 *    no edits to styles.css / chat.js / index.html.
 *  • Kills its document-level listeners and stream in destroy().
 */

(function () {
    'use strict';

    if (window._PAGES && window._PAGES['#chat-pro']) return; // already registered

    var CSS_ID = 'cpx-stylesheet';
    var CSS_HREF = 'css/chat-pro.css?v=6';
    var VIZ_SEQ = 0;
    var MAX_TITLE = 60;

    var SUGGESTIONS = [
        { icon: 'fa-chart-line', label: 'Spot the trends',
          desc: 'Surface the key trends and movements hiding in this dataset.',
          prompt: 'Show me the key trends in this dataset', tone: 'indigo' },
        { icon: 'fa-triangle-exclamation', label: 'Hunt anomalies',
          desc: 'Find outliers, spikes and unusual patterns worth a look.',
          prompt: 'Find any anomalies or outliers in the data', tone: 'amber' },
        { icon: 'fa-scale-balanced', label: 'Compare groups',
          desc: 'Break the data into segments and compare them head to head.',
          prompt: 'Compare groups in the data and highlight significant differences', tone: 'emerald' },
        { icon: 'fa-clipboard-list', label: 'Summarize everything',
          desc: 'Get a comprehensive, plain-English summary of the dataset.',
          prompt: 'Give me a comprehensive summary of this dataset', tone: 'sky' },
        { icon: 'fa-diagram-project', label: 'Map correlations',
          desc: 'Reveal the strongest relationships between columns.',
          prompt: 'What are the strongest correlations between columns?', tone: 'violet' },
        { icon: 'fa-terminal', label: 'Draft a query',
          desc: 'Generate a useful SQL query to explore the data further.',
          prompt: 'Generate a useful SQL query for exploring this table', tone: 'rose' }
    ];

    var TONE_TINTS = {
        indigo:  { tint: 'rgba(79, 70, 229, 0.12)',  accent: '#4f46e5', edge: 'rgba(79, 70, 229, 0.45)' },
        amber:   { tint: 'rgba(245, 158, 11, 0.14)', accent: '#d97706', edge: 'rgba(245, 158, 11, 0.5)' },
        emerald: { tint: 'rgba(16, 185, 129, 0.14)', accent: '#059669', edge: 'rgba(16, 185, 129, 0.5)' },
        sky:     { tint: 'rgba(59, 130, 246, 0.13)', accent: '#2563eb', edge: 'rgba(59, 130, 246, 0.5)' },
        violet:  { tint: 'rgba(139, 92, 246, 0.14)', accent: '#7c3aed', edge: 'rgba(139, 92, 246, 0.5)' },
        rose:    { tint: 'rgba(244, 63, 94, 0.12)',  accent: '#e11d48', edge: 'rgba(244, 63, 94, 0.45)' }
    };

    // ── tiny utilities ───────────────────────────────────────────────────

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function scopedKey(k) {
        return (window.RBAC && typeof window.RBAC.storeKey === 'function')
            ? window.RBAC.storeKey(k) : k;
    }

    function uid(prefix) {
        return prefix + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 7);
    }

    function fmtTime(ts) {
        try {
            return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (_) { return ''; }
    }

    function relTime(ts) {
        var diff = Date.now() - (ts || Date.now());
        var m = Math.floor(diff / 60000);
        if (m < 1) return 'just now';
        if (m < 60) return m + 'm ago';
        var h = Math.floor(m / 60);
        if (h < 24) return h + 'h ago';
        var d = Math.floor(h / 24);
        if (d < 7) return d + 'd ago';
        try { return new Date(ts).toLocaleDateString([], { month: 'short', day: 'numeric' }); }
        catch (_) { return ''; }
    }

    function dayBucket(ts) {
        var d = new Date(ts || Date.now());
        var today = new Date();
        var start = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
        if (ts >= start) return 'Today';
        if (ts >= start - 86400000) return 'Yesterday';
        if (ts >= start - 7 * 86400000) return 'Previous 7 days';
        return 'Older';
    }

    function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function (resolve, reject) {
            try {
                var ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                ta.remove();
                resolve();
            } catch (e) { reject(e); }
        });
    }

    // ── markdown-ish renderer (input MUST be escaped before use) ─────────

    function inlineMd(s) {
        // s is already escaped; operate on safe text only.
        s = s.replace(/`([^`]+)`/g, function (_, c) { return '<code class="cpx-inline">' + c + '</code>'; });
        s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?]|$)/g, '$1<em>$2</em>');
        s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|#[^\s)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
        return s;
    }

    function mdToHtml(src) {
        var lines = esc(src == null ? '' : String(src)).split('\n');
        var out = [];
        var i = 0, n = lines.length;

        function flushParagraph(buf) {
            if (buf.length) out.push('<p>' + inlineMd(buf.join('<br>')) + '</p>');
            buf.length = 0;
        }

        var para = [];
        while (i < n) {
            var line = lines[i];

            // fenced code
            var fence = line.match(/^```([\w+-]*)\s*$/);
            if (fence) {
                flushParagraph(para);
                var lang = fence[1] || 'code';
                var code = [];
                i++;
                while (i < n && !/^```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
                i++; // consume closing fence
                out.push(
                    '<div class="cpx-code">' +
                        '<div class="cpx-code-head"><span class="cpx-code-lang">' + esc(lang) + '</span>' +
                        '<button type="button" class="cpx-code-copy" data-cpx-action="copy-code">' +
                        '<i class="fas fa-copy"></i>Copy</button></div>' +
                        '<pre><code>' + code.join('\n') + '</code></pre>' +
                    '</div>');
                continue;
            }

            // heading
            var h = line.match(/^(#{1,4})\s+(.*)$/);
            if (h) {
                flushParagraph(para);
                out.push('<h' + h[1].length + '>' + inlineMd(h[2]) + '</h' + h[1].length + '>');
                i++; continue;
            }

            // hr
            if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
                flushParagraph(para);
                out.push('<hr>');
                i++; continue;
            }

            // blockquote
            if (/^&gt;\s?/.test(line)) {
                flushParagraph(para);
                var quote = [];
                while (i < n && /^&gt;\s?/.test(lines[i])) {
                    quote.push(lines[i].replace(/^&gt;\s?/, ''));
                    i++;
                }
                out.push('<blockquote>' + inlineMd(quote.join('<br>')) + '</blockquote>');
                continue;
            }

            // table: | a | b |  +  |---|---|
            if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < n && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
                flushParagraph(para);
                var head = line.split('|').slice(1, -1).map(function (c) { return c.trim(); });
                var rows = [];
                i += 2;
                while (i < n && /^\s*\|.*\|\s*$/.test(lines[i])) {
                    rows.push(lines[i].split('|').slice(1, -1).map(function (c) { return c.trim(); }));
                    i++;
                }
                var html = '<div class="cpx-table-scroll"><table><thead><tr>';
                head.forEach(function (c) { html += '<th>' + inlineMd(c) + '</th>'; });
                html += '</tr></thead><tbody>';
                rows.forEach(function (r) {
                    html += '<tr>';
                    for (var k = 0; k < head.length; k++) html += '<td>' + inlineMd(r[k] == null ? '' : r[k]) + '</td>';
                    html += '</tr>';
                });
                out.push(html + '</tbody></table></div>');
                continue;
            }

            // unordered list
            if (/^\s*[-*]\s+/.test(line)) {
                flushParagraph(para);
                var ul = [];
                while (i < n && /^\s*[-*]\s+/.test(lines[i])) {
                    ul.push(lines[i].replace(/^\s*[-*]\s+/, ''));
                    i++;
                }
                out.push('<ul>' + ul.map(function (li) { return '<li>' + inlineMd(li) + '</li>'; }).join('') + '</ul>');
                continue;
            }

            // ordered list
            if (/^\s*\d+[.)]\s+/.test(line)) {
                flushParagraph(para);
                var ol = [];
                while (i < n && /^\s*\d+[.)]\s+/.test(lines[i])) {
                    ol.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''));
                    i++;
                }
                out.push('<ol>' + ol.map(function (li) { return '<li>' + inlineMd(li) + '</li>'; }).join('') + '</ol>');
                continue;
            }

            // blank line → paragraph break
            if (/^\s*$/.test(line)) { flushParagraph(para); i++; continue; }

            para.push(line);
            i++;
        }
        flushParagraph(para);
        return out.join('');
    }

    // ── page module ──────────────────────────────────────────────────────

    var ChatProPage = {

        // ── state ──
        _root: null,
        _disabled: false,
        _messages: [],              // live reference into the active conversation
        _conversations: [],
        _activeId: null,
        _isWaiting: false,
        _abortCtrl: null,
        _stopped: false,
        _sessionId: 'cpx_' + Date.now(),
        _selectedTables: [],
        _tableList: [],             // [{name, rows}]
        _searchQuery: '',
        _scopeOpen: false,
        _scopeFilter: '',
        _collapsed: false,
        _renamingId: null,
        _armedDeleteId: null,
        _armTimer: null,
        _editIndex: -1,
        _mobile: false,
        _boundDocClick: null,
        _boundKey: null,
        _boundResize: null,

        _K_CONVS: 'chatpro_conversations_v1',
        _K_ACTIVE: 'chatpro_active_v1',
        _K_SIDEBAR: 'chatpro_sidebar_v1',
        _K_SCOPE: 'chatpro_scope_v1',

        // ════════════════════════════════════════════════════════════════
        //  Lifecycle
        // ════════════════════════════════════════════════════════════════

        async init() {
            this._K_CONVS = scopedKey('chatpro_conversations_v1');
            this._K_ACTIVE = scopedKey('chatpro_active_v1');
            this._K_SIDEBAR = scopedKey('chatpro_sidebar_v1');
            this._K_SCOPE = scopedKey('chatpro_scope_v1');

            this._isWaiting = false;
            this._abortCtrl = null;
            this._stopped = false;
            this._editIndex = -1;
            this._scopeOpen = false;
            this._renamingId = null;
            this._armedDeleteId = null;
            this._searchQuery = '';
            this._tableList = [];

            var llm = !window.App || !App.state ||
                (App.state.llm_enabled !== false &&
                 (!App.state.features || App.state.features.enable_llm_agent !== false));
            this._disabled = !llm;

            this._mobile = window.innerWidth <= 900;
            this._collapsed = localStorage.getItem(this._K_SIDEBAR) === 'true';
            if (this._mobile) this._collapsed = true;

            this._loadScope();
            this._loadConversations();

            var lastId = null;
            try { lastId = localStorage.getItem(this._K_ACTIVE); } catch (_) {}
            var conv = this._conversations.filter(function (c) { return c.id === lastId; })[0];
            if (conv) {
                this._activeId = conv.id;
                this._messages = conv.messages;
            } else if (this._conversations.length) {
                this._activeId = this._conversations[0].id;
                this._messages = this._conversations[0].messages;
            } else {
                this._messages = [];
                this._activeId = null;
            }
        },

        async destroy() {
            if (this._abortCtrl) { try { this._abortCtrl.abort(); } catch (_) {} this._abortCtrl = null; }
            this._saveConversations();
            try { localStorage.setItem(this._K_ACTIVE, this._activeId || ''); } catch (_) {}
            try { localStorage.setItem(this._K_SIDEBAR, this._collapsed ? 'true' : 'false'); } catch (_) {}
            this._persistScope();
            if (this._armTimer) { clearTimeout(this._armTimer); this._armTimer = null; }
            if (this._boundDocClick) document.removeEventListener('click', this._boundDocClick);
            if (this._boundKey) document.removeEventListener('keydown', this._boundKey);
            if (this._boundResize) window.removeEventListener('resize', this._boundResize);
            this._boundDocClick = this._boundKey = this._boundResize = null;
            var pc = document.getElementById('page-content');
            if (pc) pc.classList.remove('cpx-host');
            this._root = null;
            this._isWaiting = false;
            this._scopeOpen = false;
        },

        // ════════════════════════════════════════════════════════════════
        //  Persistence
        // ════════════════════════════════════════════════════════════════

        _loadConversations() {
            var list = [];
            try {
                var raw = localStorage.getItem(this._K_CONVS);
                list = raw ? JSON.parse(raw) : [];
                if (!Array.isArray(list)) list = [];
            } catch (_) { list = []; }
            // Drop empty untitled drafts so the sidebar never fills with
            // duplicate "New chat" rows.
            this._conversations = list.filter(function (c) {
                if (!c || !c.id) return false;
                var has = c.messages && c.messages.length;
                var t = (c.title || '').trim();
                return has || (t && t !== 'New chat');
            });
            this._conversations.sort(function (a, b) { return (b.updatedAt || 0) - (a.updatedAt || 0); });
        },

        _stripViz(convs) {
            return convs.map(function (c) {
                return {
                    id: c.id, serverId: c.serverId, title: c.title,
                    createdAt: c.createdAt, updatedAt: c.updatedAt,
                    _syncedCount: c._syncedCount,
                    messages: (c.messages || []).map(function (m) {
                        if (!m.vizHtml && !m.vizSpecs) return m;
                        var slim = {};
                        for (var k in m) if (k !== 'vizHtml' && k !== 'vizSpecs' && k !== 'streaming') slim[k] = m[k];
                        return slim;
                    })
                };
            });
        },

        _saveConversations() {
            var recent = this._conversations.slice(0, 50);
            try {
                localStorage.setItem(this._K_CONVS, JSON.stringify(this._stripViz(recent)));
                return;
            } catch (_) {}
            try {
                localStorage.setItem(this._K_CONVS, JSON.stringify(this._stripViz(recent.map(function (c) {
                    var slim = { id: c.id, serverId: c.serverId, title: c.title, createdAt: c.createdAt, updatedAt: c.updatedAt,
                        messages: (c.messages || []).map(function (m) { return { role: m.role, content: m.content, time: m.time }; }) };
                    return slim;
                }))));
            } catch (_) {}
        },

        async _syncFromServer() {
            try {
                var res = await window.API.listConversations();
                var serverConvs = (res && res.conversations) || [];
                var known = {};
                this._conversations.forEach(function (c) { if (c.serverId) known[c.serverId] = true; });

                for (var si = 0; si < serverConvs.length; si++) {
                    var sc = serverConvs[si];
                    if (known[sc.id]) continue;
                    try {
                        var full = await window.API.getConversation(sc.id);
                        var msgs = (full && full.messages) || [];
                        this._conversations.push({
                            id: 'srv_' + sc.id,
                            serverId: sc.id,
                            title: sc.title || 'New chat',
                            messages: msgs.map(function (m) {
                                return {
                                    role: m.role, content: m.content,
                                    time: m.created_at ? fmtTime(new Date(m.created_at).getTime()) : ''
                                };
                            }),
                            createdAt: sc.created_at ? new Date(sc.created_at).getTime() : Date.now(),
                            updatedAt: sc.updated_at ? new Date(sc.updated_at).getTime() : Date.now(),
                            _syncedCount: msgs.length
                        });
                    } catch (_) {}
                }

                // Upload local-only conversations (bounded to keep it snappy).
                var uploads = this._conversations.filter(function (c) {
                    return !c.serverId && c.messages && c.messages.length;
                }).slice(0, 20);
                for (var ui = 0; ui < uploads.length; ui++) {
                    var conv = uploads[ui];
                    try {
                        var created = await window.API.createConversation(conv.title || 'New chat');
                        if (created && created.id) {
                            conv.serverId = created.id;
                            conv._syncedCount = 0;
                            for (var mi = 0; mi < conv.messages.length; mi++) {
                                var m = conv.messages[mi];
                                if (m.role === 'user' || m.role === 'assistant') {
                                    await window.API.addMessage(created.id, m.role, m.content);
                                    conv._syncedCount++;
                                }
                            }
                        }
                    } catch (_) {}
                }

                this._conversations.sort(function (a, b) { return (b.updatedAt || 0) - (a.updatedAt || 0); });
                this._saveConversations();
                if (this._root) this._renderSidebar();
            } catch (_) { /* offline → local-only mode */ }
        },

        _syncMessages(conv) {
            if (!conv || !conv.serverId || conv._syncing) return;
            var start = conv._syncedCount || 0;
            var pending = (conv.messages || []).slice(start);
            if (!pending.length) return;
            conv._syncing = true;
            var self = this;
            (async function () {
                try {
                    for (var i = 0; i < pending.length; i++) {
                        var m = pending[i];
                        if (m.role === 'user' || m.role === 'assistant') {
                            await window.API.addMessage(conv.serverId, m.role, m.content);
                            conv._syncedCount = (conv._syncedCount || 0) + 1;
                        }
                    }
                } catch (err) {
                    if (err && err.status === 404) { conv.serverId = null; conv._syncedCount = 0; }
                } finally {
                    conv._syncing = false;
                    try { self._saveConversations(); } catch (_) {}
                }
            })();
        },

        // ── scope persistence ──

        _loadScope() {
            try {
                var raw = localStorage.getItem(this._K_SCOPE);
                this._selectedTables = raw ? (JSON.parse(raw) || []) : [];
                if (!Array.isArray(this._selectedTables)) this._selectedTables = [];
                this._selectedTables = this._selectedTables.filter(function (t) { return typeof t === 'string'; });
            } catch (_) { this._selectedTables = []; }
        },

        _persistScope() {
            try { localStorage.setItem(this._K_SCOPE, JSON.stringify(this._selectedTables)); } catch (_) {}
        },

        _activeConv() {
            var id = this._activeId;
            if (id == null) return null;
            return this._conversations.filter(function (c) { return c.id === id; })[0] || null;
        },

        // ════════════════════════════════════════════════════════════════
        //  Render — shell
        // ════════════════════════════════════════════════════════════════

        _ensureCss() {
            if (document.getElementById(CSS_ID)) return;
            var link = document.createElement('link');
            link.id = CSS_ID;
            link.rel = 'stylesheet';
            link.href = CSS_HREF;
            document.head.appendChild(link);
        },

        async render() {
            this._ensureCss();
            var pc = document.getElementById('page-content');
            if (!pc) return;
            pc.classList.add('cpx-host');

            if (this._disabled) {
                pc.innerHTML = this._disabledHtml();
                this._root = pc.querySelector('.cpx-app');
                this._wireRoot();
                return;
            }

            pc.innerHTML = this._shellHtml();
            this._root = pc.querySelector('.cpx-app');
            this._wireRoot();
            this._wireDoc();

            try { await App.refreshTables(); } catch (_) {}
            this._loadTables();

            // Drop scope entries whose tables no longer exist.
            var names = {};
            this._tableList.forEach(function (t) { names[t.name.toLowerCase()] = true; });
            var before = this._selectedTables.length;
            this._selectedTables = this._selectedTables.filter(function (t) { return names[t.toLowerCase()]; });
            if (this._selectedTables.length !== before) this._persistScope();

            if (!this._activeId && this._conversations.length) {
                this._activeId = this._conversations[0].id;
                this._messages = this._conversations[0].messages;
            }
            this._renderSidebar();
            this._renderThread();
            this._updateHeader();
            this._updateComposer();
            var ta = this._root.querySelector('.cpx-input');
            if (ta && !this._mobile) ta.focus();

            this._syncFromServer();
        },

        _shellHtml() {
            return '' +
            '<div class="cpx-app' + (this._collapsed ? ' cpx-collapsed' : '') + (this._mobile ? ' cpx-mobile cpx-drawer' : '') + '">' +
              '<div class="cpx-scrim" data-cpx-action="close-drawer"></div>' +
              this._sidebarHtml() +
              '<main class="cpx-main">' +
                '<header class="cpx-top">' +
                  '<button type="button" class="cpx-icon-btn" data-cpx-action="toggle-sidebar" title="Toggle sidebar" aria-label="Toggle sidebar">' +
                    '<i class="fas fa-bars-staggered"></i></button>' +
                  '<div class="cpx-title-wrap">' +
                    '<div class="cpx-title" data-cpx-title>New chat</div>' +
                    '<div class="cpx-subtitle"><span data-cpx-subtitle>Your data analyst</span></div>' +
                  '</div>' +
                  '<div class="cpx-status" data-cpx-status><span class="cpx-status-dot"></span>' +
                    '<span class="cpx-status-txt" data-cpx-status-txt>Ready</span></div>' +
                  '<div class="cpx-top-actions">' +
                    '<button type="button" class="cpx-icon-btn" data-cpx-action="copy-conv" title="Copy conversation" aria-label="Copy conversation">' +
                      '<i class="fas fa-copy"></i></button>' +
                    '<button type="button" class="cpx-icon-btn cpx-danger" data-cpx-action="clear-conv" title="Clear this conversation" aria-label="Clear this conversation">' +
                      '<i class="fas fa-broom"></i></button>' +
                  '</div>' +
                '</header>' +
                '<div class="cpx-thread" data-cpx-thread>' +
                  '<div class="cpx-thread-inner" data-cpx-thread-inner></div>' +
                '</div>' +
                '<button type="button" class="cpx-jump" data-cpx-jump data-cpx-action="jump-bottom" title="Scroll to bottom" aria-label="Scroll to bottom">' +
                  '<i class="fas fa-arrow-down"></i></button>' +
                this._composerHtml() +
              '</main>' +
            '</div>';
        },

        _sidebarHtml() {
            return '' +
            '<aside class="cpx-sidebar">' +
              '<div class="cpx-side-top">' +
                '<div class="cpx-brand">' +
                  '<div class="cpx-brand-orb"><i class="fas fa-wand-magic-sparkles"></i></div>' +
                  '<div><div class="cpx-brand-name">Agent Chat</div>' +
                  '<div class="cpx-brand-sub">DataAgent · Neural analyst</div></div>' +
                '</div>' +
                '<button type="button" class="cpx-new" data-cpx-action="new-conv">' +
                  '<i class="fas fa-plus"></i>New chat<kbd>Ctrl K</kbd></button>' +
                '<div class="cpx-search">' +
                  '<i class="fas fa-magnifying-glass"></i>' +
                  '<input type="text" class="cpx-search-input" data-cpx-search placeholder="Search conversations…" aria-label="Search conversations">' +
                '</div>' +
              '</div>' +
              '<div class="cpx-convs" data-cpx-convs></div>' +
              '<div class="cpx-side-foot">' +
                '<button type="button" class="cpx-scope-btn" data-cpx-action="scope-open" title="Choose which tables the agent may use">' +
                  '<i class="fas fa-database cpx-scope-ico"></i>' +
                  '<span class="cpx-scope-txt" data-cpx-scope-label>All tables</span>' +
                  '<span class="cpx-scope-count" data-cpx-scope-count hidden></span>' +
                '</button>' +
                '<div class="cpx-foot-note"><i class="fas fa-circle"></i>Local-first · one writer on the warehouse</div>' +
              '</div>' +
            '</aside>';
        },

        _composerHtml() {
            return '' +
            '<div class="cpx-composer-wrap">' +
              '<div class="cpx-composer-inner">' +
                '<div class="cpx-scopebar" data-cpx-scopebar></div>' +
                '<div class="cpx-composer">' +
                  '<button type="button" class="cpx-attach" data-cpx-action="scope-open" title="Pick tables for context" aria-label="Pick tables for context">' +
                    '<i class="fas fa-database"></i>' +
                    '<span class="cpx-attach-badge" data-cpx-attach-badge hidden></span>' +
                  '</button>' +
                  '<textarea class="cpx-input" data-cpx-input rows="1" placeholder="Ask anything about your data…" aria-label="Message"></textarea>' +
                  '<button type="button" class="cpx-send" data-cpx-action="send" title="Send" aria-label="Send">' +
                    '<i class="fas fa-arrow-up"></i></button>' +
                '</div>' +
                '<div class="cpx-hint">' +
                  '<span><kbd>Enter</kbd> send</span>' +
                  '<span><kbd>Shift</kbd>+<kbd>Enter</kbd> new line</span>' +
                  '<span data-cpx-hint-scope></span>' +
                '</div>' +
              '</div>' +
            '</div>';
        },

        _disabledHtml() {
            var fallback = window.App && App.state && App.state.features && App.state.features.fallback_to_sql;
            return '' +
            '<div class="cpx-app">' +
              '<main class="cpx-main">' +
                '<div class="cpx-disabled">' +
                  '<div class="cpx-orb"><i class="fas fa-robot"></i></div>' +
                  '<h2>AI chat is turned off</h2>' +
                  '<p>The LLM agent is disabled in this workspace.' +
                    (fallback ? ' You can still explore the data with SQL.' : '') + '</p>' +
                  '<div class="cpx-disabled-actions">' +
                    '<button type="button" class="cpx-btn-primary" data-cpx-action="go-sql">' +
                      '<i class="fas fa-terminal"></i>&nbsp; Open SQL Explorer</button>' +
                    '<button type="button" class="cpx-btn-ghost" data-cpx-action="go-analysis">' +
                      '<i class="fas fa-chart-line"></i>&nbsp; Analysis Suite</button>' +
                  '</div>' +
                '</div>' +
              '</main>' +
            '</div>';
        },

        // ── sidebar ──

        _renderSidebar() {
            if (!this._root) return;
            var wrap = this._root.querySelector('[data-cpx-convs]');
            if (!wrap) return;
            var q = this._searchQuery.trim().toLowerCase();
            var list = this._conversations.filter(function (c) {
                return !q || (c.title || '').toLowerCase().indexOf(q) !== -1;
            });

            if (!list.length) {
                wrap.innerHTML = '<div class="cpx-list-empty"><i class="fas fa-comments"></i>' +
                    (q ? 'No conversations match “' + esc(this._searchQuery.trim()) + '”.' : 'No conversations yet.<br>Start one above — it will appear here.') +
                    '</div>';
            } else {
                var html = '';
                var lastBucket = '';
                list.forEach(function (c) {
                    var bucket = dayBucket(c.updatedAt || c.createdAt);
                    if (bucket !== lastBucket) {
                        html += '<div class="cpx-group-label">' + bucket + '</div>';
                        lastBucket = bucket;
                    }
                    html += this._convRowHtml(c);
                }, this);
                wrap.innerHTML = html;
            }

            var label = this._root.querySelector('[data-cpx-scope-label]');
            var count = this._root.querySelector('[data-cpx-scope-count]');
            if (label) {
                label.textContent = this._selectedTables.length
                    ? (this._selectedTables.length === 1 ? this._selectedTables[0] : this._selectedTables.length + ' tables selected')
                    : 'All tables';
            }
            if (count) {
                count.hidden = !this._selectedTables.length;
                count.textContent = String(this._selectedTables.length);
            }
            this._refreshRenameInput();
        },

        _convRowHtml(c) {
            var active = c.id === this._activeId ? ' cpx-active' : '';
            var title = esc(c.title || 'New chat');
            var count = (c.messages || []).length;
            var renaming = this._renamingId === c.id;
            return '' +
            '<button type="button" class="cpx-conv' + active + '" data-cpx-conv="' + esc(c.id) + '" title="' + title + '">' +
              '<span class="cpx-conv-ico"><i class="fas fa-message"></i></span>' +
              '<span class="cpx-conv-main">' +
                (renaming
                  ? '<input class="cpx-rename-input" data-cpx-rename-input value="' + title + '" aria-label="Rename conversation">'
                  : '<span class="cpx-conv-title">' + title + '</span>') +
                '<span class="cpx-conv-meta">' + relTime(c.updatedAt || c.createdAt) +
                  (count ? ' · ' + count + ' msg' + (count === 1 ? '' : 's') : '') + '</span>' +
              '</span>' +
              '<span class="cpx-conv-actions">' +
                '<span role="button" tabindex="0" class="cpx-mini" data-cpx-action="rename-conv" data-cpx-id="' + esc(c.id) + '" title="Rename"><i class="fas fa-pen"></i></span>' +
                '<span role="button" tabindex="0" class="cpx-mini cpx-danger' + (this._armedDeleteId === c.id ? ' cpx-arm' : '') + '" data-cpx-action="delete-conv" data-cpx-id="' + esc(c.id) + '" title="' + (this._armedDeleteId === c.id ? 'Click again to confirm' : 'Delete') + '"><i class="fas fa-trash-can"></i></span>' +
              '</span>' +
            '</button>';
        },

        _refreshRenameInput() {
            if (!this._root || !this._renamingId) return;
            var input = this._root.querySelector('[data-cpx-rename-input]');
            if (input) { input.focus(); input.select(); }
        },

        _commitRename(value) {
            var conv = this._activeConvById(this._renamingId);
            this._renamingId = null;
            if (conv) {
                var clean = (value || '').trim();
                if (clean) {
                    conv.title = clean.slice(0, 80);
                    conv.updatedAt = Date.now();
                    this._saveConversations();
                    if (conv.serverId) {
                        window.API.renameConversation(conv.serverId, conv.title).catch(function () {});
                    }
                }
            }
            this._renderSidebar();
            this._updateHeader();
        },

        _activeConvById(id) {
            return this._conversations.filter(function (c) { return c.id === id; })[0] || null;
        },

        _armDelete(id) {
            var self = this;
            if (this._armTimer) clearTimeout(this._armTimer);
            if (this._armedDeleteId === id) {
                this._armedDeleteId = null;
                this._deleteConversation(id);
                return;
            }
            this._armedDeleteId = id;
            this._renderSidebar();
            this._armTimer = setTimeout(function () {
                self._armedDeleteId = null;
                if (self._root) self._renderSidebar();
            }, 2600);
        },

        // ── conversations ──

        _newConversation() {
            var draft = this._conversations.filter(function (c) {
                return (!c.messages || !c.messages.length) && (!c.title || c.title === 'New chat');
            })[0];
            if (draft) {
                this._switchConversation(draft.id);
                return draft.id;
            }
            var conv = {
                id: uid('cpx_'), title: 'New chat', messages: [],
                createdAt: Date.now(), updatedAt: Date.now()
            };
            this._conversations.unshift(conv);
            this._activeId = conv.id;
            this._messages = conv.messages;
            this._saveConversations();
            this._renderSidebar();
            this._renderThread();
            this._updateHeader();
            this._updateComposer();
            var self = this;
            window.API.createConversation('New chat').then(function (created) {
                if (created && created.id) { conv.serverId = created.id; self._saveConversations(); }
            }).catch(function () {});
            return conv.id;
        },

        _switchConversation(id) {
            var conv = this._activeConvById(id);
            if (!conv) return;
            this._activeId = id;
            this._messages = conv.messages;
            this._editIndex = -1;
            this._saveConversations();
            try { localStorage.setItem(this._K_ACTIVE, id); } catch (_) {}
            this._renderSidebar();
            this._renderThread();
            this._updateHeader();
            this._updateComposer();
            this._scrollToBottom(true);
            if (this._mobile) this._setCollapsed(true);
        },

        _deleteConversation(id) {
            var conv = this._activeConvById(id);
            if (!conv) return;
            this._conversations = this._conversations.filter(function (c) { return c.id !== id; });
            if (conv.serverId) window.API.deleteConversation(conv.serverId).catch(function () {});
            this._saveConversations();
            if (this._activeId === id) {
                if (this._conversations.length) {
                    this._switchConversation(this._conversations[0].id);
                } else {
                    this._activeId = null;
                    this._messages = [];
                    this._renderSidebar();
                    this._renderThread();
                    this._updateHeader();
                    this._updateComposer();
                }
            } else {
                this._renderSidebar();
            }
            if (window.App) App.toast('Conversation deleted', 'info');
        },

        _clearConversation() {
            var conv = this._activeConv();
            if (!conv) return;
            conv.messages = [];
            conv.title = 'New chat';
            conv.updatedAt = Date.now();
            this._messages = conv.messages;
            this._editIndex = -1;
            this._saveConversations();
            this._renderSidebar();
            this._renderThread();
            this._updateHeader();
            this._updateComposer();
            if (window.App) App.toast('Conversation cleared', 'info');
        },

        _generateTitle(text) {
            var clean = (text || '').trim().replace(/\s+/g, ' ');
            if (!clean) return 'New chat';
            return clean.length > MAX_TITLE ? clean.slice(0, MAX_TITLE) + '…' : clean;
        },

        _maybeRetitle() {
            var conv = this._activeConv();
            if (!conv) return;
            var firstUser = (conv.messages || []).filter(function (m) { return m.role === 'user'; })[0];
            if (firstUser && (!conv.title || conv.title === 'New chat')) {
                conv.title = this._generateTitle(firstUser.content);
                this._saveConversations();
                if (conv.serverId) {
                    window.API.renameConversation(conv.serverId, conv.title).catch(function () {});
                }
                this._renderSidebar();
                this._updateHeader();
            }
        },

        // ── header ──

        _updateHeader() {
            if (!this._root) return;
            var conv = this._activeConv();
            var titleEl = this._root.querySelector('[data-cpx-title]');
            var subEl = this._root.querySelector('[data-cpx-subtitle]');
            if (titleEl) titleEl.textContent = conv ? (conv.title || 'New chat') : 'New chat';
            if (subEl) {
                var count = conv ? (conv.messages || []).length : 0;
                var scopeTxt = this._selectedTables.length
                    ? (this._selectedTables.length === 1 ? this._selectedTables[0] : this._selectedTables.length + ' tables')
                    : 'All tables';
                var warehouse = this._tableList.length ? this._tableList.length + ' table' + (this._tableList.length === 1 ? '' : 's') + ' in warehouse' : 'warehouse ready';
                subEl.textContent = (count ? count + ' messages · ' : '') + scopeTxt + ' · ' + warehouse;
            }
            this._updateStatus(this._isWaiting);
        },

        _updateStatus(busy) {
            if (!this._root) return;
            var el = this._root.querySelector('[data-cpx-status]');
            var txt = this._root.querySelector('[data-cpx-status-txt]');
            if (!el || !txt) return;
            el.classList.toggle('cpx-busy', !!busy);
            txt.textContent = busy ? 'Thinking…' : 'Ready';
        },

        // ════════════════════════════════════════════════════════════════
        //  Render — thread
        // ════════════════════════════════════════════════════════════════

        _renderThread() {
            if (!this._root) return;
            var inner = this._root.querySelector('[data-cpx-thread-inner]');
            if (!inner) return;
            if (!this._messages.length) {
                inner.innerHTML = this._heroHtml();
                return;
            }
            var html = '';
            var lastAssistant = -1;
            for (var i = this._messages.length - 1; i >= 0; i--) {
                if (this._messages[i].role === 'assistant') { lastAssistant = i; break; }
            }
            for (var j = 0; j < this._messages.length; j++) {
                html += this._bubbleHtml(this._messages[j], j, j === lastAssistant);
            }
            inner.innerHTML = html;
            this._renderAllViz();
            this._scrollToBottom(true);
        },

        _heroHtml() {
            var cards = SUGGESTIONS.map(function (s) {
                var tone = TONE_TINTS[s.tone] || TONE_TINTS.indigo;
                return '' +
                '<button type="button" class="cpx-suggest" data-cpx-action="suggest" data-cpx-prompt="' + esc(s.prompt) + '" ' +
                  'style="--cpx-tint:' + tone.tint + ';--cpx-accent:' + tone.accent + ';--cpx-edge:' + tone.edge + '">' +
                  '<span class="cpx-suggest-ico"><i class="fas ' + s.icon + '"></i></span>' +
                  '<b>' + esc(s.label) + '</b>' +
                  '<small>' + esc(s.desc) + '</small>' +
                  '<i class="fas fa-arrow-right cpx-suggest-go"></i>' +
                '</button>';
            }).join('');

            var tables = this._tableList.length;
            var scope = this._selectedTables.length
                ? (this._selectedTables.length === 1 ? esc(this._selectedTables[0]) : this._selectedTables.length + ' tables scoped')
                : 'All tables in scope';

            return '' +
            '<div class="cpx-hero">' +
              '<div class="cpx-orb-wrap">' +
                '<div class="cpx-orb-ring"></div>' +
                '<div class="cpx-orb-ring cpx-r2"></div>' +
                '<div class="cpx-orb"><i class="fas fa-wand-magic-sparkles"></i></div>' +
              '</div>' +
              '<h1>Ask anything, <span>get answers</span></h1>' +
              '<p>The agent writes SQL, runs it on the warehouse, charts the result — ' +
                 'and explains what it found in plain language.</p>' +
              '<div class="cpx-hero-meta">' +
                '<span class="cpx-pill"><i class="fas fa-database"></i>' +
                  (tables ? tables + ' table' + (tables === 1 ? '' : 's') + ' loaded' : 'warehouse connected') + '</span>' +
                '<span class="cpx-pill"><i class="fas fa-bullseye"></i>' + scope + '</span>' +
                '<span class="cpx-pill"><i class="fas fa-lock"></i>local-first</span>' +
              '</div>' +
              '<div class="cpx-suggests">' + cards + '</div>' +
            '</div>';
        },

        _bubbleHtml(m, idx, isLastAssistant) {
            var isUser = m.role === 'user';
            var time = m.time || fmtTime(m.createdAt || Date.now());
            var body = '';
            var actions = '';

            if (isUser) {
                body = '<div class="cpx-bubble cpx-bubble-user">' +
                       '<div class="cpx-md">' + mdToHtml(m.content) + '</div></div>';
                actions =
                    '<button type="button" class="cpx-mini" data-cpx-action="edit-msg" data-cpx-idx="' + idx + '" title="Edit & resend"><i class="fas fa-pen"></i></button>' +
                    '<button type="button" class="cpx-mini" data-cpx-action="copy-msg" data-cpx-idx="' + idx + '" title="Copy"><i class="fas fa-copy"></i></button>';
            } else {
                var stepsHtml = (m.steps && m.steps.length)
                    ? this._renderStepsHtml(m.steps, m.streaming ? false : (m.stepsCollapsed !== false))
                    : '';
                var viz = '';
                if (m.vizHtml || (m.vizSpecs && m.vizSpecs.length)) {
                    viz = '<div class="cpx-viz">' + (m.vizHtml || '') + '</div>';
                }
                var inner = m.streaming
                    ? '<div class="cpx-md" data-cpx-md></div><div class="cpx-typing" data-cpx-typing><span></span><span></span><span></span></div>'
                    : (m.error
                        ? '<div class="cpx-error-head"><i class="fas fa-triangle-exclamation"></i>Could not fully answer</div>'
                        : '') + '<div class="cpx-md" data-cpx-md>' + mdToHtml(m.content || '') + '</div>';
                if (m.sqlCta) {
                    inner += '<button type="button" class="cpx-sql-cta" data-cpx-action="open-sql">' +
                        '<i class="fas fa-terminal"></i> Open SQL Explorer</button>';
                }
                body = '<div class="cpx-bubble cpx-bubble-ai' + (m.error ? ' cpx-error' : '') + '">' + stepsHtml + viz + inner + '</div>';
                if (!m.streaming) {
                    actions =
                        '<button type="button" class="cpx-mini" data-cpx-action="copy-msg" data-cpx-idx="' + idx + '" title="Copy"><i class="fas fa-copy"></i></button>' +
                        (isLastAssistant && !this._isWaiting
                            ? '<button type="button" class="cpx-mini" data-cpx-action="regenerate" title="Regenerate"><i class="fas fa-rotate-right"></i></button>'
                            : '');
                }
            }

            return '' +
            '<div class="cpx-row ' + (isUser ? 'cpx-row-user' : 'cpx-row-ai') + '" data-cpx-row="' + idx + '">' +
              '<div class="cpx-avatar ' + (isUser ? 'cpx-avatar-user' : 'cpx-avatar-ai') + '">' +
                (isUser ? '<i class="fas fa-user"></i>' : '<i class="fas fa-wand-magic-sparkles"></i>') + '</div>' +
              '<div class="cpx-col">' +
                body +
                '<div class="cpx-time">' + esc(time) +
                  (m.stopped ? ' <span class="cpx-limits"><i class="fas fa-hand"></i>stopped</span>' : '') + '</div>' +
                '<div class="cpx-msg-actions">' + actions + '</div>' +
              '</div>' +
            '</div>';
        },

        // ── live pipeline steps ──

        // Merge one {type: plan|start|done|answering} stream payload into the
        // message's steps model (kept on the message so re-renders show it).
        _applyStreamStep(payload, m) {
            if (!payload || !payload.type) return;
            if (payload.type === 'plan') {
                m.steps = (payload.steps || []).map(function (s) {
                    return { id: s.id, tool: s.tool, description: s.description,
                             status: 'pending', ms: null, rows: null };
                });
                if (m.stepsCollapsed === undefined) m.stepsCollapsed = false;
            } else if (payload.type === 'start') {
                (m.steps || []).forEach(function (s) {
                    if (s.id === payload.id) s.status = 'running';
                });
            } else if (payload.type === 'done') {
                (m.steps || []).forEach(function (s) {
                    if (s.id === payload.id) {
                        s.status = payload.ok === false ? 'failed' : 'done';
                        if (payload.ms != null) s.ms = payload.ms;
                        if (payload.rows != null) s.rows = payload.rows;
                    }
                });
            }
            // 'answering' needs no state change — the panel collapses on finish.
        },

        _renderStepsHtml(steps, collapsed) {
            if (!steps || !steps.length) return '';
            var doneCount = 0, failedCount = 0, chartCount = 0, totalMs = 0, live = false;
            steps.forEach(function (s) {
                if (s.status === 'done') { doneCount++; if (s.tool === 'viz') chartCount++; }
                else if (s.status === 'failed') failedCount++;
                else live = true;
                totalMs += (s.ms || 0);
            });
            var summary;
            if (live) {
                summary = 'Working… ' + (doneCount + failedCount) + ' of ' + steps.length + ' steps';
                if (chartCount) summary += ' · ' + chartCount + ' chart' + (chartCount === 1 ? '' : 's');
            } else if (failedCount) {
                summary = '⚠ ' + failedCount + ' of ' + steps.length + ' steps failed';
            } else {
                summary = '✓ ' + steps.length + ' step' + (steps.length === 1 ? '' : 's');
                if (chartCount) summary += ' · ' + chartCount + ' chart' + (chartCount === 1 ? '' : 's');
                if (totalMs >= 100) summary += ' · ' + (totalMs / 1000).toFixed(1) + 's';
            }
            var rows = steps.map(function (s) {
                var icon;
                if (s.status === 'done') icon = '<i class="fas fa-check"></i>';
                else if (s.status === 'failed') icon = '<i class="fas fa-xmark"></i>';
                else if (s.status === 'running') icon = '<span class="cpx-spin"></span>';
                else icon = '<i class="fas fa-circle cpx-step-dot"></i>';
                var meta = '';
                if (s.status === 'done' && s.ms != null) {
                    meta = Math.round(s.ms) + ' ms' + (s.rows != null ? ' · ' + s.rows + ' rows' : '');
                }
                return '<li class="cpx-step cpx-step-' + s.status + '" data-cpx-step-id="' + s.id + '">' +
                    '<span class="cpx-step-ico">' + icon + '</span>' +
                    '<span class="cpx-step-body"><b>' + esc(s.description || s.tool || '') + '</b>' +
                    (meta ? '<span class="cpx-step-meta">' + esc(meta) + '</span>' : '') +
                    '</span></li>';
            }).join('');
            return '' +
                '<div class="cpx-steps' + (collapsed ? ' cpx-steps-collapsed' : '') + '" data-cpx-steps>' +
                  '<button type="button" class="cpx-steps-head" data-cpx-action="steps-toggle">' +
                    '<i class="fas fa-diagram-project"></i>' +
                    '<span class="cpx-steps-summary">' + esc(summary) + '</span>' +
                    '<i class="fas fa-chevron-down cpx-steps-caret"></i>' +
                  '</button>' +
                  '<ul class="cpx-steps-list">' + rows + '</ul>' +
                '</div>';
        },

        // Live-update the panel in the DOM without touching the answer text
        // (re-rendering the bubble wholesale would wipe the streaming caret).
        _syncStepsPanel(rowEl, m) {
            if (!rowEl || !m.steps || !m.steps.length) return;
            var zone = rowEl.querySelector('.cpx-steps-zone');
            if (!zone) {
                var bubble = rowEl.querySelector('.cpx-bubble');
                if (!bubble) return;
                zone = document.createElement('div');
                zone.className = 'cpx-steps-zone';
                var anchor = bubble.querySelector('.cpx-viz') || bubble.querySelector('[data-cpx-md]');
                if (anchor) bubble.insertBefore(zone, anchor);
                else bubble.appendChild(zone);
            }
            zone.innerHTML = this._renderStepsHtml(m.steps, !!m.stepsCollapsed);
        },

        // Lazily create the chart wrapper. It must sit AFTER the steps panel
        // but BEFORE the markdown body.
        _ensureVizWrap(bubbleEl) {
            var vizWrap = bubbleEl.querySelector('.cpx-viz');
            if (!vizWrap) {
                vizWrap = document.createElement('div');
                vizWrap.className = 'cpx-viz';
                var zone = bubbleEl.querySelector('.cpx-steps-zone');
                bubbleEl.insertBefore(vizWrap, zone ? zone.nextSibling : bubbleEl.firstChild);
            }
            return vizWrap;
        },

        // ── visualizations ──

        _buildViz(vizList) {
            var base = 'cpxviz_' + Date.now() + '_' + (++VIZ_SEQ);
            var html = '';
            var specs = [];
            (vizList || []).forEach(function (v, i) {
                if (v && v.spec) {
                    var id = base + '_' + i;
                    html += '<div id="' + id + '" class="cpx-viz-plot"></div>';
                    specs.push({ id: id, spec: v.spec });
                } else if (v && v.html) {
                    html += '<div class="cpx-viz-raw">' + v.html + '</div>';
                }
            });
            return { html: html, specs: specs };
        },

        _renderVizSpecs(specs, attempt) {
            if (!specs || !specs.length) return;
            var self = this;
            attempt = attempt || 0;
            if (typeof window.Plotly === 'undefined') {
                if (attempt < 24) {
                    setTimeout(function () { self._renderVizSpecs(specs, attempt + 1); }, 250);
                }
                return;
            }
            specs.forEach(function (pair) {
                var el = document.getElementById(pair.id);
                if (!el || el.getAttribute('data-cpx-plotted')) return;
                try {
                    window.Plotly.newPlot(el, pair.spec.data || [], pair.spec.layout || {}, { responsive: true });
                    el.setAttribute('data-cpx-plotted', '1');
                } catch (e) {
                    console.error('ChatPro: Plotly render failed', e);
                }
            });
        },

        _renderAllViz() {
            if (!this._root || typeof window.Plotly === 'undefined') {
                // Retry once Plotly shows up (it can be lazily injected).
                var self = this;
                if (this._messages.some(function (m) { return m.vizSpecs && m.vizSpecs.length; })) {
                    setTimeout(function () { self._renderAllViz(); }, 400);
                }
                return;
            }
            this._messages.forEach(function (m) { this._renderVizSpecs(m.vizSpecs); }, this);
        },

        // ════════════════════════════════════════════════════════════════
        //  Composer
        // ════════════════════════════════════════════════════════════════

        _updateComposer() {
            if (!this._root) return;
            var send = this._root.querySelector('[data-cpx-action="send"]');
            if (send) {
                send.classList.toggle('cpx-stop', this._isWaiting);
                send.innerHTML = this._isWaiting
                    ? '<i class="fas fa-stop"></i>' : '<i class="fas fa-arrow-up"></i>';
                send.title = this._isWaiting ? 'Stop generating' : 'Send';
                send.setAttribute('aria-label', this._isWaiting ? 'Stop generating' : 'Send');
            }
            this._renderScopeChips();
            var hint = this._root.querySelector('[data-cpx-hint-scope]');
            if (hint) {
                hint.textContent = this._selectedTables.length
                    ? 'scope: ' + (this._selectedTables.length === 1 ? this._selectedTables[0] : this._selectedTables.length + ' tables')
                    : 'scope: all tables';
            }
            this._renderScopePopover();
        },

        _renderScopeChips() {
            if (!this._root) return;
            var bar = this._root.querySelector('[data-cpx-scopebar]');
            var attach = this._root.querySelector('.cpx-attach');
            var badge = this._root.querySelector('[data-cpx-attach-badge]');
            if (!bar) return;
            if (!this._selectedTables.length) {
                bar.innerHTML = '';
            } else {
                var chips = this._selectedTables.map(function (t) {
                    return '<span class="cpx-table-chip"><i class="fas fa-database"></i>' + esc(t) +
                        '<button type="button" data-cpx-action="scope-remove" data-cpx-table="' + esc(t) + '" title="Remove from scope" aria-label="Remove ' + esc(t) + ' from scope"><i class="fas fa-xmark"></i></button></span>';
                }).join('');
                bar.innerHTML = chips +
                    '<button type="button" class="cpx-clear-scope" data-cpx-action="scope-clear">clear all</button>';
            }
            if (attach) attach.classList.toggle('cpx-has-scope', !!this._selectedTables.length);
            if (badge) {
                badge.hidden = !this._selectedTables.length;
                badge.textContent = String(this._selectedTables.length);
            }
        },

        _renderScopePopover() {
            if (!this._root) return;
            var old = this._root.querySelector('.cpx-scopepop');
            if (old) old.remove();
            if (!this._scopeOpen) return;
            var composer = this._root.querySelector('.cpx-composer');
            if (!composer) return;

            var q = this._scopeFilter.trim().toLowerCase();
            var self = this;
            var rows = this._tableList.filter(function (t) {
                return !q || t.name.toLowerCase().indexOf(q) !== -1;
            }).map(function (t) {
                var on = self._selectedTables.some(function (s) { return s.toLowerCase() === t.name.toLowerCase(); });
                return '' +
                '<button type="button" class="cpx-scope-opt' + (on ? ' cpx-on' : '') + '" data-cpx-action="scope-toggle" data-cpx-table="' + esc(t.name) + '">' +
                  '<span class="cpx-check"><i class="fas fa-check"></i></span>' +
                  '<span class="cpx-opt-name">' + esc(t.name) + '</span>' +
                  (t.rows != null ? '<span class="cpx-opt-rows">' + Number(t.rows).toLocaleString() + ' rows</span>' : '') +
                '</button>';
            }).join('');

            var pop = document.createElement('div');
            pop.className = 'cpx-scopepop';
            pop.innerHTML = '' +
              '<div class="cpx-scopepop-head">' +
                '<div class="cpx-scopepop-title"><i class="fas fa-bullseye"></i>Query scope</div>' +
                '<div class="cpx-scopepop-search"><i class="fas fa-magnifying-glass"></i>' +
                  '<input type="text" data-cpx-scope-filter placeholder="Filter tables…" value="' + esc(this._scopeFilter) + '"></div>' +
              '</div>' +
              '<div class="cpx-scopepop-list">' +
                '<button type="button" class="cpx-scope-opt cpx-all' + (this._selectedTables.length ? '' : ' cpx-on') + '" data-cpx-action="scope-all">' +
                  '<span class="cpx-check"><i class="fas fa-check"></i></span>' +
                  '<span class="cpx-opt-name">All tables</span>' +
                  '<span class="cpx-opt-rows">' + this._tableList.length + ' available</span>' +
                '</button>' +
                (rows || '<div class="cpx-list-empty"><i class="fas fa-magnifying-glass"></i>No tables match.</div>') +
              '</div>' +
              '<div class="cpx-scopepop-foot">' +
                '<span>' + (this._selectedTables.length ? this._selectedTables.length + ' selected' : 'No scope — every table reachable') + '</span>' +
                '<button type="button" data-cpx-action="scope-close">Done</button>' +
              '</div>';
            composer.insertBefore(pop, composer.firstChild);
            var filter = pop.querySelector('[data-cpx-scope-filter]');
            if (filter && this._scopeFilter) { filter.focus(); filter.setSelectionRange(filter.value.length, filter.value.length); }
            var self2 = this;
            if (filter) {
                filter.addEventListener('input', function () {
                    self2._scopeFilter = filter.value;
                    self2._renderScopePopover();
                });
            }
        },

        _loadTables() {
            var raw = window.App && App.state ? App.state.tables : [];
            var out = [];
            if (Array.isArray(raw)) {
                raw.forEach(function (t) {
                    if (typeof t === 'string') out.push({ name: t, rows: null });
                    else if (t && t.name) out.push({ name: t.name, rows: (t.rows != null ? t.rows : (t.row_count != null ? t.row_count : null)) });
                });
            } else if (raw && typeof raw === 'object') {
                Object.keys(raw).forEach(function (k) {
                    var v = raw[k] || {};
                    out.push({ name: k, rows: (v.rows != null ? v.rows : (v.row_count != null ? v.row_count : null)) });
                });
            }
            out.sort(function (a, b) { return a.name.localeCompare(b.name); });
            this._tableList = out;
        },

        _toggleScopeTable(name) {
            var lower = name.toLowerCase();
            var exists = this._selectedTables.some(function (t) { return t.toLowerCase() === lower; });
            if (exists) {
                this._selectedTables = this._selectedTables.filter(function (t) { return t.toLowerCase() !== lower; });
            } else {
                this._selectedTables.push(name);
            }
            this._persistScope();
            this._renderScopeChips();
            this._renderScopePopover();
            this._renderSidebar();
            this._updateHeader();
            if (!this._root) return;
            var hint = this._root.querySelector('[data-cpx-hint-scope]');
            if (hint) {
                hint.textContent = this._selectedTables.length
                    ? 'scope: ' + (this._selectedTables.length === 1 ? this._selectedTables[0] : this._selectedTables.length + ' tables')
                    : 'scope: all tables';
            }
        },

        // ════════════════════════════════════════════════════════════════
        //  Answering / streaming
        // ════════════════════════════════════════════════════════════════

        _inputMessage() {
            if (!this._root) return '';
            var ta = this._root.querySelector('.cpx-input');
            return ta ? (ta.value || '').trim() : '';
        },

        _send() {
            if (this._isWaiting) { this._stop(); return; }
            var text = this._inputMessage();
            if (!text) return;
            var ta = this._root.querySelector('.cpx-input');
            if (ta) { ta.value = ''; this._autoGrow(ta); }
            var editIndex = this._editIndex;
            this._editIndex = -1;
            this._answer(text, editIndex >= 0 ? { editIndex: editIndex } : undefined);
        },

        _stop() {
            this._stopped = true;
            if (this._abortCtrl) { try { this._abortCtrl.abort(); } catch (_) {} }
        },

        /** Detect questions that ask to *generate* SQL (not questions about data). */
        _isSqlGenerationRequest(text) {
            var q = (text || '').trim().toLowerCase();
            if (!q) return false;
            if (/\b(generate|create|write|build|make|produce|construct|draft)\b/.test(q) &&
                /\b(sql|query|statement|script)\b/.test(q)) return true;
            if (/\bsql\b/.test(q) && /\b(code|query|statement|syntax)\b/.test(q)) return true;
            if (/\b(show|give|get|tell)\b[^.]{0,40}\bthe sql\b/.test(q)) return true;
            return false;
        },

        /** Point the user at the SQL Explorer's AI generator instead of calling the LLM. */
        _handleSqlGenerationRequest(text, conv) {
            try { sessionStorage.setItem('pending_ai_sql_prompt', text); } catch (_) {}
            conv.messages.push({
                role: 'assistant',
                content: 'SQL query generation lives in the **SQL Explorer** 🗄️\n\n' +
                    'It has an **AI generator** that writes the SQL for you from a plain-English ' +
                    'description (look for the **Visual Query Builder → AI Generate** button).\n\n' +
                    'Click **Open SQL Explorer** below to get started.',
                time: fmtTime(Date.now()), createdAt: Date.now(), sqlCta: true
            });
            conv.updatedAt = Date.now();
            this._saveConversations();
            this._renderSidebar();
            this._renderThread();
            this._updateHeader();
            this._updateComposer();
            this._syncMessages(conv);
        },

        async _answer(text, opts) {
            opts = opts || {};
            if (this._isWaiting) return;

            var conv = this._activeConv();
            if (!conv) {
                var id = this._newConversation();
                conv = this._activeConv();
                if (!conv) return;
                void id;
            }

            // Editing history: drop the edited user message and everything after.
            if (opts.editIndex != null && opts.editIndex >= 0) {
                conv.messages.splice(opts.editIndex);
                this._messages = conv.messages;
            }

            if (!opts.skipUser) {
                conv.messages.push({ role: 'user', content: text, time: fmtTime(Date.now()), createdAt: Date.now() });
            }
            conv.updatedAt = Date.now();
            this._messages = conv.messages;
            this._maybeRetitle();
            this._saveConversations();
            this._renderSidebar();
            this._renderThread();
            this._updateHeader();

            // "Write/generate SQL" requests route to the SQL Explorer's AI
            // generator instead of the LLM (same behavior as the old chat).
            if (this._isSqlGenerationRequest(text)) {
                this._handleSqlGenerationRequest(text, conv);
                return;
            }

            this._isWaiting = true;
            this._stopped = false;
            this._abortCtrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            this._updateComposer();
            this._updateStatus(true);

            // History = everything before the pending answer; the current
            // question travels separately as the question argument.
            var history = conv.messages
                .filter(function (m) { return m.role === 'user' || m.role === 'assistant'; })
                .map(function (m) { return { role: m.role, content: m.content }; })
                .slice(-20);

            var tables = this._selectedTables.slice();
            var question = tables.length === 1 ? 'Using the "' + tables[0] + '" table: ' + text : text;

            // Pending assistant placeholder (streamed into directly).
            var placeholder = {
                role: 'assistant', content: '', time: fmtTime(Date.now()),
                createdAt: Date.now(), streaming: true, vizHtml: '', vizSpecs: []
            };
            conv.messages.push(placeholder);
            this._renderThread();
            this._updateHeader();

            var rowSel = '[data-cpx-row="' + (conv.messages.length - 1) + '"]';
            var rowEl = this._root ? this._root.querySelector(rowSel) : null;
            var mdEl = rowEl ? rowEl.querySelector('[data-cpx-md]') : null;
            var typingEl = rowEl ? rowEl.querySelector('[data-cpx-typing]') : null;
            var bubbleEl = rowEl ? rowEl.querySelector('.cpx-bubble') : null;

            var streamed = '';
            var vizHtml = '';
            var vizSpecs = [];
            var usedStream = false;

            try {
                try {
                    var gen = window.API.askStream(question, history, this._sessionId, tables,
                        this._abortCtrl ? this._abortCtrl.signal : undefined, true);
                    for await (var token of gen) {
                        if (token && typeof token === 'object' && token.__step) {
                            this._applyStreamStep(token.__step, placeholder);
                            this._syncStepsPanel(rowEl, placeholder);
                            this._scrollToBottom();
                            continue;
                        }
                        if (token && typeof token === 'object' && token.__viz) {
                            var one = this._buildViz([token.__viz]);
                            vizHtml += one.html;
                            vizSpecs = vizSpecs.concat(one.specs);
                            if (bubbleEl) {
                                this._ensureVizWrap(bubbleEl).insertAdjacentHTML('beforeend', one.html);
                            }
                            this._renderVizSpecs(one.specs);
                            this._scrollToBottom();
                            continue;
                        }
                        if (token && typeof token === 'object' && token.__visualizations) {
                            var built = this._buildViz(token.__visualizations);
                            vizHtml = built.html;
                            vizSpecs = built.specs;
                            if (bubbleEl) {
                                this._ensureVizWrap(bubbleEl).innerHTML = vizHtml;
                            }
                            this._renderVizSpecs(vizSpecs);
                            this._scrollToBottom();
                            continue;
                        }
                        if (typeof token !== 'string') continue;
                        streamed += token;
                        if (typingEl && typingEl.parentNode) { typingEl.remove(); typingEl = null; }
                        if (!mdEl && rowEl) mdEl = rowEl.querySelector('[data-cpx-md]');
                        if (mdEl) {
                            mdEl.innerHTML = mdToHtml(streamed) + '<span class="cpx-caret"></span>';
                        }
                        this._scrollToBottom();
                    }
                    if (!streamed.trim()) throw new Error('Empty stream response');
                    usedStream = true;
                } catch (streamErr) {
                    if (this._stopped && (streamed.trim() || vizSpecs.length)) {
                        usedStream = true; // keep the partial answer, never re-run
                    } else if (this._stopped) {
                        usedStream = true; // user stopped before any token: discard placeholder
                    } else {
                        usedStream = false;
                    }
                }

                if (!usedStream) {
                    var result = await window.API.ask(question, { tables: tables, sessionId: this._sessionId });
                    if (result && result.session_id) this._sessionId = result.session_id;
                    var answer = (result && (result.answer || '')).trim();
                    var built2 = this._buildViz((result && result.visualizations) || []);
                    placeholder.content = answer || (result && result.error) || 'No results returned.';
                    placeholder.vizHtml = built2.html;
                    placeholder.vizSpecs = built2.specs;
                    placeholder.streaming = false;
                    placeholder.error = !answer && !!(result && result.error);
                } else if (this._stopped) {
                    if (streamed.trim() || vizSpecs.length) {
                        placeholder.content = streamed;
                        placeholder.vizHtml = vizHtml;
                        placeholder.vizSpecs = vizSpecs;
                        placeholder.streaming = false;
                        placeholder.stopped = true;
                    } else {
                        // Nothing arrived — drop the placeholder entirely.
                        conv.messages.pop();
                        this._messages = conv.messages;
                    }
                } else {
                    placeholder.content = streamed;
                    placeholder.vizHtml = vizHtml;
                    placeholder.vizSpecs = vizSpecs;
                    placeholder.streaming = false;
                }
            } catch (e) {
                placeholder.content = 'Something went wrong: ' + ((e && e.message) || 'unknown error') +
                    '\n\nTry rephrasing the question, or check that the LLM backend is reachable.';
                placeholder.streaming = false;
                placeholder.error = true;
            } finally {
                this._isWaiting = false;
                this._abortCtrl = null;
                var wasStopped = this._stopped;
                this._stopped = false;
                // Settle the live steps panel: collapse it and stop any
                // lingering spinner so re-renders show a finished summary.
                if (placeholder.steps && placeholder.steps.length) {
                    var settle = wasStopped ? 'failed' : 'done';
                    placeholder.steps.forEach(function (s) {
                        if (s.status === 'running' || s.status === 'pending') s.status = settle;
                    });
                    placeholder.stepsCollapsed = true;
                }
                conv.updatedAt = Date.now();
                this._saveConversations();
                this._renderSidebar();
                this._renderThread();
                this._updateComposer();
                this._updateStatus(false);
                this._updateHeader();
                this._syncMessages(conv);
                if (wasStopped && window.App) App.toast('Generation stopped', 'info');
            }
        },

        _regenerate() {
            var conv = this._activeConv();
            if (!conv || this._isWaiting) return;
            var msgs = conv.messages;
            var lastUser = -1;
            for (var i = msgs.length - 1; i >= 0; i--) {
                if (msgs[i].role === 'user') { lastUser = i; break; }
            }
            if (lastUser === -1) return;
            var text = msgs[lastUser].content;
            // Remove the previous assistant answer (if any) after the user turn.
            conv.messages = msgs.slice(0, lastUser + 1);
            this._messages = conv.messages;
            this._saveConversations();
            this._renderThread();
            this._answer(text, { skipUser: true });
        },

        _editMessage(idx) {
            var m = this._messages[idx];
            if (!m || m.role !== 'user') return;
            var ta = this._root.querySelector('.cpx-input');
            if (!ta) return;
            ta.value = m.content;
            this._autoGrow(ta);
            ta.focus();
            this._editIndex = idx;
            if (window.App) App.toast('Editing — press Enter to resend', 'info');
        },

        _autoGrow(ta) {
            if (!ta) return;
            ta.style.height = 'auto';
            ta.style.height = Math.min(ta.scrollHeight, 190) + 'px';
        },

        // ════════════════════════════════════════════════════════════════
        //  Events
        // ════════════════════════════════════════════════════════════════

        _wireRoot() {
            if (!this._root) return;
            var self = this;
            this._root.addEventListener('click', function (e) { self._onClick(e); });
            var thread = this._root.querySelector('[data-cpx-thread]');
            if (thread) {
                thread.addEventListener('scroll', function () { self._updateJumpBtn(); });
            }
            this._root.addEventListener('keydown', function (e) { self._onRootKey(e); });
            this._root.addEventListener('input', function (e) { self._onInput(e); });
            this._root.addEventListener('keydown', function (e) {
                // Enter-to-send lives on the textarea only.
                if (!e.target || !e.target.classList || !e.target.classList.contains('cpx-input')) return;
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    self._send();
                }
            });
        },

        _wireDoc() {
            if (!this._boundDocClick) {
                var self = this;
                this._boundDocClick = function (e) {
                    if (!self._root || !self._scopeOpen) return;
                    if (!self._root.contains(e.target)) return;
                    var pop = self._root.querySelector('.cpx-scopepop');
                    var attach = e.target.closest ? e.target.closest('[data-cpx-action="scope-open"]') : null;
                    if (pop && !pop.contains(e.target) && !attach) {
                        self._scopeOpen = false;
                        self._renderScopePopover();
                    }
                };
                document.addEventListener('click', this._boundDocClick);
            }
            if (!this._boundKey) {
                var self2 = this;
                this._boundKey = function (e) {
                    if (!self2._root) return;
                    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
                        e.preventDefault();
                        self2._newConversation();
                        var ta = self2._root.querySelector('.cpx-input');
                        if (ta) ta.focus();
                    } else if (e.key === 'Escape' && self2._scopeOpen) {
                        self2._scopeOpen = false;
                        self2._renderScopePopover();
                    }
                };
                document.addEventListener('keydown', this._boundKey);
            }
            if (!this._boundResize) {
                var self3 = this;
                this._boundResize = function () {
                    var wasMobile = self3._mobile;
                    self3._mobile = window.innerWidth <= 900;
                    if (self3._root) {
                        self3._root.classList.toggle('cpx-mobile', self3._mobile);
                        self3._root.classList.toggle('cpx-drawer', self3._mobile && !self3._collapsed);
                        if (self3._mobile && !wasMobile) self3._setCollapsed(true);
                    }
                };
                window.addEventListener('resize', this._boundResize);
            }
        },

        _onInput(e) {
            var t = e.target;
            if (!t) return;
            if (t.classList.contains('cpx-input')) { this._autoGrow(t); }
            if (t.classList.contains('cpx-search-input')) {
                this._searchQuery = t.value || '';
                this._renderSidebar();
                var again = this._root.querySelector('.cpx-search-input');
                if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
            }
        },

        _onRootKey(e) {
            var t = e.target;
            if (!t || !t.classList) return;
            if (t.classList.contains('cpx-rename-input')) {
                if (e.key === 'Enter') { e.preventDefault(); this._commitRename(t.value); }
                else if (e.key === 'Escape') { e.preventDefault(); this._renamingId = null; this._renderSidebar(); }
            }
        },

        _setCollapsed(collapsed) {
            this._collapsed = collapsed;
            if (this._root) {
                this._root.classList.toggle('cpx-collapsed', collapsed);
                this._root.classList.toggle('cpx-drawer', this._mobile && !collapsed);
            }
        },

        _onClick(e) {
            var t = e.target;
            if (!t || !t.closest) return;

            // conversation row (but not its action buttons)
            var row = t.closest('[data-cpx-conv]');
            var action = t.closest('[data-cpx-action]');
            var act = action ? action.getAttribute('data-cpx-action') : null;

            if (action && (act === 'rename-conv' || act === 'delete-conv')) {
                e.stopPropagation();
                var id = action.getAttribute('data-cpx-id');
                if (act === 'rename-conv') { this._renamingId = id; this._renderSidebar(); }
                else { this._armDelete(id); }
                return;
            }

            if (act) {
                var handled = this._handleAction(act, action, e);
                if (handled) return;
            }

            if (row && this._root.contains(row)) {
                if (this._renamingId) return;
                var cid = row.getAttribute('data-cpx-conv');
                if (cid && cid !== this._activeId) this._switchConversation(cid);
                return;
            }
        },

        _handleAction(act, el, e) {
            var self = this;
            switch (act) {
                case 'toggle-sidebar':
                    if (this._mobile) this._setCollapsed(!this._collapsed);
                    else this._setCollapsed(!this._collapsed);
                    return true;

                case 'close-drawer':
                    this._setCollapsed(true);
                    return true;

                case 'new-conv':
                    this._newConversation();
                    var ta = this._root.querySelector('.cpx-input');
                    if (ta) ta.focus();
                    return true;

                case 'send':
                    this._send();
                    return true;

                case 'suggest': {
                    var prompt = el.getAttribute('data-cpx-prompt') || '';
                    if (prompt) this._answer(prompt);
                    return true;
                }

                case 'open-sql':
                    if (window.App) App.navigate('#sql');
                    return true;

                case 'scope-open':
                    e.stopPropagation();
                    this._scopeOpen = !this._scopeOpen;
                    this._scopeFilter = '';
                    this._renderScopePopover();
                    return true;

                case 'scope-close':
                    this._scopeOpen = false;
                    this._renderScopePopover();
                    return true;

                case 'scope-all':
                    this._selectedTables = [];
                    this._persistScope();
                    this._renderScopeChips();
                    this._renderScopePopover();
                    this._renderSidebar();
                    this._updateHeader();
                    return true;

                case 'scope-toggle':
                    this._toggleScopeTable(el.getAttribute('data-cpx-table') || '');
                    return true;

                case 'scope-remove':
                    this._toggleScopeTable(el.getAttribute('data-cpx-table') || '');
                    return true;

                case 'scope-clear':
                    this._selectedTables = [];
                    this._persistScope();
                    this._renderScopeChips();
                    this._renderScopePopover();
                    this._renderSidebar();
                    this._updateHeader();
                    return true;

                case 'steps-toggle': {
                    var stepRow = el.closest('[data-cpx-row]');
                    var stepIdx = stepRow ? parseInt(stepRow.getAttribute('data-cpx-row'), 10) : -1;
                    var stepMsg = this._messages[stepIdx];
                    if (stepMsg && stepMsg.steps) {
                        // undefined/true = collapsed → expand; false = expand → collapse.
                        stepMsg.stepsCollapsed = stepMsg.stepsCollapsed === false;
                        this._renderThread();
                    }
                    return true;
                }

                case 'copy-msg': {
                    var idx = parseInt(el.getAttribute('data-cpx-idx'), 10);
                    var m = this._messages[idx];
                    if (m) {
                        copyText(m.content || '').then(function () {
                            if (window.App) App.toast('Copied to clipboard', 'success');
                        }).catch(function () {
                            if (window.App) App.toast('Copy failed', 'error');
                        });
                    }
                    return true;
                }

                case 'copy-code': {
                    var codeEl = el.closest('.cpx-code');
                    var pre = codeEl ? codeEl.querySelector('pre') : null;
                    if (pre) {
                        copyText(pre.textContent || '').then(function () {
                            if (window.App) App.toast('Code copied', 'success');
                        }).catch(function () {
                            if (window.App) App.toast('Copy failed', 'error');
                        });
                    }
                    return true;
                }

                case 'edit-msg':
                    this._editMessage(parseInt(el.getAttribute('data-cpx-idx'), 10));
                    return true;

                case 'regenerate':
                    this._regenerate();
                    return true;

                case 'copy-conv': {
                    var conv = this._activeConv();
                    if (conv) {
                        var text = (conv.messages || []).map(function (m) {
                            return (m.role === 'user' ? 'You: ' : 'Agent: ') + (m.content || '');
                        }).join('\n\n---\n\n');
                        copyText(text).then(function () {
                            if (window.App) App.toast('Conversation copied', 'success');
                        }).catch(function () {
                            if (window.App) App.toast('Copy failed', 'error');
                        });
                    }
                    return true;
                }

                case 'clear-conv':
                    if (el.classList.contains('cpx-arm')) {
                        el.classList.remove('cpx-arm');
                        this._clearConversation();
                    } else {
                        el.classList.add('cpx-arm');
                        setTimeout(function () { el.classList.remove('cpx-arm'); }, 2600);
                    }
                    return true;

                case 'jump-bottom':
                    this._scrollToBottom(true);
                    return true;

                case 'go-sql':
                    if (window.App) App.navigate('#sql');
                    return true;

                case 'go-analysis':
                    if (window.App) App.navigate('#analysis');
                    return true;
            }
            return false;
        },

        // ── scroll ──

        _scrollToBottom(force) {
            var thread = this._root ? this._root.querySelector('[data-cpx-thread]') : null;
            if (!thread) return;
            var nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 140;
            if (force || nearBottom) {
                thread.scrollTop = thread.scrollHeight;
            }
            this._updateJumpBtn();
        },

        _updateJumpBtn() {
            var thread = this._root ? this._root.querySelector('[data-cpx-thread]') : null;
            var btn = this._root ? this._root.querySelector('[data-cpx-jump]') : null;
            if (!thread || !btn) return;
            var far = thread.scrollHeight - thread.scrollTop - thread.clientHeight > 320;
            btn.classList.toggle('cpx-show', far);
        }
    };

    (window._PAGES = window._PAGES || {})['#chat-pro'] = ChatProPage;
})();
