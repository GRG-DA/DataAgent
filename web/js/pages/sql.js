/* ═══════════════════════════════════════════
   SQL Explorer Page — Monaco Query Editor & Runner
   ═══════════════════════════════════════════ */

const SqlPage = {
    _history: [],
    _savedQueries: [],
    _lastResult: null,
    _monacoEditor: null,
    _compModel: null,
    _selectedTables: [],          // empty = All tables (AI generation scope)
    _tableSearch: '',

    async init() {
        this._history = [];
        this._savedQueries = [];
        this._lastResult = null;
        this._monacoEditor = null;
        this._compModel = null;
        const sel = sessionStorage.getItem('sql_selected_tables');
        if (sel) { try { this._selectedTables = JSON.parse(sel) || []; } catch (_) { this._selectedTables = []; } }
    },

    async destroy() {
        this._lastResult = null;
        try { sessionStorage.setItem('sql_selected_tables', JSON.stringify(this._selectedTables)); } catch (_) {}
        if (this._boundClose) document.removeEventListener('click', this._boundClose);
        if (this._monacoEditor) {
            this._monacoEditor.dispose();
            this._monacoEditor = null;
        }
        if (this._compModel) {
            this._compModel.dispose();
            this._compModel = null;
        }
    },

    /* ── Render shell ──────────────────────────────────────────────── */
    async render() {
        await App.refreshTables();
        const pc = document.getElementById('page-content');
        const tables = App.state.tables || {};
        const tableNames = Array.isArray(tables) ? tables :
            (typeof tables === 'object' && tables !== null) ? Object.keys(tables) : [];

        // Prune any selected datasets that no longer exist (e.g. were dropped).
        const known = new Set(tableNames.map(x => x.toLowerCase()));
        const before = this._selectedTables.length;
        this._selectedTables = this._selectedTables.filter(t => known.has(t.toLowerCase()));
        if (this._selectedTables.length !== before) {
            try { sessionStorage.setItem('sql_selected_tables', JSON.stringify(this._selectedTables)); } catch (_) {}
        }

        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in">
            <div class="flex items-center justify-between mb-4">
                <div class="page-title"><div class="page-title-icon"><i class="fas fa-database"></i></div><div><h2>SQL Explorer</h2></div></div>
                <div class="flex items-center gap-2">
                    <select id="sql-ref-table" class="text-sm border rounded-lg p-1.5 dark:bg-gray-700 dark:border-gray-600"
                        onchange="SqlPage._showTableSchema(this.value)" title="Quick table reference">
                        <option value="">Table schema...</option>
                        ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                    </select>
                    <button onclick="SqlPage._toggleHistory()" class="text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 px-3 py-1.5 rounded-lg" title="Query History">
                        <i class="fas fa-history mr-1"></i>History
                    </button>
                </div>
            </div>

            <!-- Visual Query Builder -->
            <div class="card mb-4">
                <div class="flex items-center justify-between cursor-pointer" onclick="SqlPage._toggleQueryBuilder()">
                    <h3 class="text-sm font-semibold text-gray-600 dark:text-gray-300"><i class="fas fa-wand-magic-sparkles text-indigo-500 mr-1"></i>Visual Query Builder</h3>
                    <i class="fas fa-chevron-down text-gray-400 text-xs" id="qb-chevron"></i>
                </div>
                <div id="query-builder-panel" class="hidden mt-3 space-y-3">
                    <div class="flex flex-wrap items-center gap-2">
                        <label class="block text-gray-500 font-medium text-xs">Datasets</label>
                        <div class="relative" id="sql-ds-picker">
                            <button id="sql-ds-btn" onclick="SqlPage._toggleTablePicker()"
                                class="text-xs border rounded-lg px-2.5 py-1.5 min-w-[140px] max-w-[200px] flex items-center justify-between gap-2 dark:bg-gray-700 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-600 transition-colors"
                                title="Select datasets to generate SQL against (multi-select)">
                                <span id="sql-ds-btn-label" class="truncate">All tables</span>
                                <i class="fas fa-chevron-down text-[9px] text-gray-400 shrink-0"></i>
                            </button>
                            <div id="sql-ds-panel" class="hidden absolute z-10 left-0 mt-1 w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-lg overflow-hidden">
                                <div class="p-2 border-b border-gray-100 dark:border-gray-700">
                                    <input id="sql-ds-search" type="text" placeholder="Search tables..."
                                        class="w-full text-sm border rounded-lg px-2.5 py-1.5 dark:bg-gray-700 dark:border-gray-600"
                                        oninput="SqlPage._renderTableList()" onclick="event.stopPropagation()">
                                </div>
                                <div class="px-2 pt-2 border-b border-gray-100 dark:border-gray-700">
                                    <label class="flex items-center gap-2 px-1 py-1.5 cursor-pointer text-sm hover:bg-gray-50 dark:hover:bg-gray-700/60 rounded-lg">
                                        <input type="checkbox" id="sql-ds-all" class="rounded"
                                            ${this._selectedTables.length === 0 ? 'checked' : ''}
                                            onchange="SqlPage._onAllTablesToggle(this.checked)">
                                        <span class="font-medium">All tables</span>
                                        <span class="text-xs text-gray-400 ml-auto">no scope</span>
                                    </label>
                                </div>
                                <div id="sql-ds-list" class="max-h-56 overflow-y-auto p-1"></div>
                                <div class="flex items-center justify-between p-2 border-t border-gray-100 dark:border-gray-700">
                                    <button onclick="SqlPage._clearTables()" class="text-xs text-gray-400 hover:text-red-500 px-2 py-1 rounded" title="Clear dataset selection">
                                        <i class="fas fa-times mr-1"></i>Clear
                                    </button>
                                    <button onclick="SqlPage._toggleTablePicker()" class="text-xs bg-purple-500 hover:bg-purple-600 text-white rounded-lg px-3 py-1.5 transition-colors">Done</button>
                                </div>
                            </div>
                        </div>
                        <span class="text-[10px] text-gray-400">AI Generate scopes queries to these tables (joins allowed)</span>
                    </div>
                    <div id="sql-ds-chips" class="${this._selectedTables.length ? 'flex flex-wrap items-center gap-1.5' : 'hidden'}">
                        ${this._selectedTables.map((t, i) => `
                        <span class="inline-flex items-center gap-1.5 bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-800 rounded-full px-2.5 py-0.5 text-xs">
                            ${App.escapeHtml(t)}
                            <button onclick="SqlPage._removeTableByIndex(${i})" class="text-purple-400 hover:text-purple-600 dark:hover:text-purple-200" title="Remove ${App.escapeHtml(t)}"><i class="fas fa-times text-[9px]"></i></button>
                        </span>`).join('')}
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-5 gap-3 text-xs">
                        <div>
                            <label class="block text-gray-500 mb-1 font-medium">Table</label>
                            <select id="qb-table" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600" onchange="SqlPage._loadTableColumns(this.value)">
                                <option value="">Select...</option>
                                ${tableNames.map(t => `<option value="${t}" ${t === App.state.selectedTable ? 'selected' : ''}>${t}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <label class="block text-gray-500 mb-1 font-medium">Columns</label>
                            <select id="qb-columns" multiple class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 h-20">
                                <option value="*">* (all)</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-gray-500 mb-1 font-medium">Where</label>
                            <input id="qb-where" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600" placeholder="e.g. amount > 100">
                        </div>
                        <div>
                            <label class="block text-gray-500 mb-1 font-medium">Group By</label>
                            <input id="qb-group" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600" placeholder="e.g. region">
                        </div>
                        <div>
                            <label class="block text-gray-500 mb-1 font-medium">Order By / Limit</label>
                            <div class="flex gap-1">
                                <input id="qb-order" class="flex-1 border rounded p-2 dark:bg-gray-700 dark:border-gray-600" placeholder="e.g. count DESC">
                                <input id="qb-limit" type="number" class="w-16 border rounded p-2 dark:bg-gray-700 dark:border-gray-600" value="100" placeholder="Limit">
                            </div>
                        </div>
                    </div>
                    <div class="flex items-center gap-2">
                        <button onclick="SqlPage._buildQuery()" class="bg-indigo-500 hover:bg-indigo-600 text-white px-4 py-1.5 rounded-lg text-xs font-medium">
                            <i class="fas fa-code mr-1"></i>Generate SQL
                        </button>
                        <button onclick="SqlPage._generateSqlWithAI()" class="bg-purple-500 hover:bg-purple-600 text-white px-4 py-1.5 rounded-lg text-xs font-medium">
                            <i class="fas fa-wand-magic-sparkles mr-1"></i>AI Generate
                        </button>
                        <div id="qb-ai-prompt" class="flex-1 hidden">
                            <input id="qb-ai-input" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 text-xs" placeholder="Describe what you want to query..."
                                onkeydown="if(event.key==='Enter'){SqlPage._generateSqlWithAI();event.preventDefault();}">
                        </div>
                        <span id="qb-generated" class="text-xs text-gray-400"></span>
                    </div>
                </div>
            </div>

            <div class="flex gap-4">
                <!-- Main Editor Area -->
                <div class="flex-1 space-y-4 min-w-0">
                    <!-- Editor -->
                    <div class="card p-0 overflow-hidden">
                        <div class="flex items-center justify-between bg-gray-50 dark:bg-gray-800 px-4 py-2 border-b dark:border-gray-700">
                            <span class="text-xs font-medium text-gray-500 uppercase tracking-wide">
                                <i class="fas fa-code mr-1"></i>SQL Editor
                                <span class="ml-2 font-normal normal-case text-gray-400">[Ctrl+Enter to run]</span>
                            </span>
                            <div class="flex items-center gap-2">
                                <button onclick="SqlPage._formatSQL()" class="text-xs text-gray-400 hover:text-primary-500 px-2 py-1 rounded"
                                    title="Format query">
                                    <i class="fas fa-magic mr-1"></i>Format
                                </button>
                                <button onclick="SqlPage._clearEditor()" class="text-xs text-gray-400 hover:text-red-500 px-2 py-1 rounded"
                                    title="Clear editor">
                                    <i class="fas fa-eraser mr-1"></i>Clear
                                </button>
                            </div>
                        </div>
                        <div id="monaco-editor-container" style="width:100%;height:180px;min-height:120px"></div>
                        <div class="flex items-center justify-between bg-gray-50 dark:bg-gray-800 px-4 py-2 border-t dark:border-gray-700">
                            <div class="flex items-center gap-2">
                                <button onclick="SqlPage._execute()" id="sql-execute-btn"
                                    class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-1.5 rounded-lg text-sm font-medium transition-colors">
                                    <i class="fas fa-play mr-1"></i>Execute
                                </button>
                                <button onclick="SqlPage._saveQuery()" class="text-sm text-gray-500 hover:text-primary-500 px-3 py-1.5 rounded-lg border dark:border-gray-600"
                                    title="Save query">
                                    <i class="fas fa-save mr-1"></i>Save
                                </button>
                            </div>
                            <div id="sql-meta" class="text-xs text-gray-400"></div>
                        </div>
                    </div>

                    <!-- Results -->
                    <div class="card">
                        <div class="flex items-center justify-between mb-3">
                            <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide">Results</h3>
                            <div id="sql-result-info" class="text-xs text-gray-400"></div>
                        </div>
                        <div id="sql-results" class="overflow-x-auto">
                            <p class="text-gray-400 text-center py-8 text-sm">Execute a query to see results.</p>
                        </div>
                        <div id="sql-results-actions" class="hidden mt-3 flex items-center gap-2">
                            <button onclick="SqlPage._exportResults('csv')" class="text-xs text-gray-500 hover:text-primary-500 border dark:border-gray-600 px-3 py-1 rounded">
                                <i class="fas fa-file-csv mr-1"></i>Export CSV
                            </button>
                        </div>
                    </div>
                </div>

                <!-- History Sidebar -->
                <div id="sql-history-panel" class="hidden w-72 flex-shrink-0 space-y-4">
                    <div class="card">
                        <div class="flex items-center justify-between mb-3">
                            <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide">Queries</h3>
                            <button onclick="SqlPage._toggleHistory()" class="text-gray-400 hover:text-gray-600 text-sm">
                                <i class="fas fa-times"></i>
                            </button>
                        </div>
                        <input id="sql-query-search" type="text" placeholder="Search queries..." aria-label="Search queries" class="w-full text-xs border rounded-lg px-3 py-1.5 mb-3 dark:bg-gray-700 dark:border-gray-600" oninput="SqlPage._filterQueries(this.value)">
                        <div class="flex gap-1 mb-3">
                            <button onclick="SqlPage._showTab('history')" id="sql-tab-history" class="flex-1 text-xs py-1.5 rounded bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400 font-medium">History</button>
                            <button onclick="SqlPage._showTab('saved')" id="sql-tab-saved" class="flex-1 text-xs py-1.5 rounded text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700">Saved</button>
                        </div>
                        <div id="sql-history-list" class="space-y-2 max-h-[300px] overflow-y-auto">
                            <p class="text-gray-400 text-sm text-center py-4">No history yet.</p>
                        </div>
                        <div id="sql-saved-list" class="hidden space-y-2 max-h-[300px] overflow-y-auto">
                            <p class="text-gray-400 text-sm text-center py-4">No saved queries.</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>`;

        this._initMonaco('monaco-editor-container');
        this._loadHistory();
        this._loadSavedQueries();
        this._loadPendingAiPrompt();
        this._updateTableUI();
    },

    /* ── Pre-filled AI prompt (from the chat's SQL-generation redirect) ── */
    _loadPendingAiPrompt() {
        let prompt = null;
        try { prompt = sessionStorage.getItem('pending_ai_sql_prompt'); } catch (_) {}
        if (!prompt) return;
        try { sessionStorage.removeItem('pending_ai_sql_prompt'); } catch (_) {}

        // Open the Visual Query Builder + its AI prompt, pre-filled.
        const panel = document.getElementById('query-builder-panel');
        if (panel?.classList.contains('hidden')) this._toggleQueryBuilder();
        const promptArea = document.getElementById('qb-ai-prompt');
        const promptInput = document.getElementById('qb-ai-input');
        if (promptArea?.classList.contains('hidden')) promptArea.classList.remove('hidden');
        if (promptInput) { promptInput.value = prompt; promptInput.focus(); }
        App.toast('AI prompt loaded from chat', 'info');
    },

    /* ── Monaco Editor initialization ──────────────────────────────── */
    async _initMonaco(containerId) {
        if (this._monacoEditor) { this._monacoEditor.focus(); return; }

        const doCreate = () => {
            const container = document.getElementById(containerId);
            if (!container) return;
            this._createEditor(container);
        };

        if (window.monaco) { doCreate(); return; }

        // Load Monaco from CDN — at most once per browser session. The
        // vscode-loader owns the global AMD `define`; injecting loader.min.js
        // twice (e.g. navigating away mid-load and returning) throws "Can only
        // have one anonymous define call per script file". Memoize on `window`
        // because SPA re-entry re-executes this whole page module.
        if (!window.__monacoLoaderPromise) {
            window.__monacoLoaderPromise = new Promise((resolve) => {
                const existing = document.querySelector('script[data-monaco-loader]');
                if (existing) {
                    existing.addEventListener('load', () => resolve(), { once: true });
                    setTimeout(resolve, 8000);   // never hang on a stalled CDN tag
                    return;
                }
                const script = document.createElement('script');
                script.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/loader.min.js';
                script.dataset.monacoLoader = '1';
                script.onload = () => {
                    require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' } });
                    require(['vs/editor/editor.main'], () => resolve(), () => resolve());
                };
                script.onerror = () => resolve();
                document.head.appendChild(script);
            });
        }
        await window.__monacoLoaderPromise;
        doCreate();
    },

    _createEditor(container) {
        const tables = App.state.tables || {};
        const tableNames = Array.isArray(tables) ? tables :
            (typeof tables === 'object' && tables !== null) ? Object.keys(tables) : [];

        // Build SQL completion items
        monaco.languages.registerCompletionItemProvider('sql', {
            provideCompletionItems: async (model, position) => {
                const word = model.getWordUntilPosition(position);
                const range = {
                    startLineNumber: position.lineNumber,
                    endLineNumber: position.lineNumber,
                    startColumn: word.startColumn,
                    endColumn: word.endColumn,
                };
                const suggestions = [];

                // SQL keywords
                const kws = ['SELECT','FROM','WHERE','AND','OR','NOT','IN','AS','ON',
                    'GROUP BY','ORDER BY','HAVING','LIMIT','OFFSET','JOIN',
                    'LEFT JOIN','RIGHT JOIN','INNER JOIN','OUTER JOIN','CROSS JOIN',
                    'UNION','ALL','DISTINCT','CASE','WHEN','THEN','ELSE','END',
                    'COUNT','SUM','AVG','MIN','MAX','COALESCE','NULLIF','CAST',
                    'EXISTS','BETWEEN','LIKE','IS NULL','IS NOT NULL',
                    'ASC','DESC','WITH','RECURSIVE','OVER','PARTITION BY',
                    'ROW_NUMBER','RANK','DENSE_RANK','LAG','LEAD'];
                kws.forEach(kw => suggestions.push({
                    label: kw, kind: monaco.languages.CompletionItemKind.Keyword,
                    insertText: kw, range,
                }));

                // Table names
                tableNames.forEach(name => suggestions.push({
                    label: name, kind: monaco.languages.CompletionItemKind.Struct,
                    insertText: name, range, detail: 'Table',
                }));

                // Column names from tables
                for (const table of tableNames) {
                    try {
                        const prof = await API.profile(table);
                        const cols = prof?.profile?.columns || prof?.columns || [];
                        if (Array.isArray(cols)) {
                            cols.forEach(col => {
                                const cn = typeof col === 'string' ? col : (col.name || col.column_name || '');
                                if (cn) suggestions.push({
                                    label: cn, kind: monaco.languages.CompletionItemKind.Field,
                                    insertText: cn, range, detail: `${table}.${cn}`,
                                });
                            });
                        }
                    } catch (_) {}
                }
                return { suggestions };
            },
        });

        // Dark SQL theme
        monaco.editor.defineTheme('sql-theme', {
            base: 'vs-dark', inherit: true,
            rules: [
                { token: 'keyword', foreground: '569cd6', fontStyle: 'bold' },
                { token: 'string.sql', foreground: 'ce9178' },
                { token: 'number', foreground: 'b5cea8' },
                { token: 'type', foreground: '4ec9b0' },
                { token: 'function', foreground: 'dcdcaa' },
                { token: 'comment', foreground: '6a9955', fontStyle: 'italic' },
            ],
            colors: {
                'editor.background': '#1e1e2e',
                'editorLineNumber.foreground': '#6e6e8a',
                'editorCursor.foreground': '#a6e22e',
            },
        });

        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        const firstTable = (tableNames && tableNames.length) ? tableNames[0] : 'table_name';
        this._monacoEditor = monaco.editor.create(container, {
            value: `SELECT * FROM ${firstTable} LIMIT 100`,
            language: 'sql',
            theme: isDark ? 'sql-theme' : 'vs',
            fontSize: 13,
            fontFamily: '"JetBrains Mono", "Fira Code", monospace',
            minimap: { enabled: false },
            lineNumbers: 'on',
            folding: true,
            lineNumbersMinChars: 3,
            padding: { top: 12, bottom: 12 },
            scrollBeyondLastLine: false,
            automaticLayout: true,
            tabSize: 4,
            wordWrap: 'on',
            bracketPairColorization: { enabled: true },
            suggest: { showKeywords: true },
        });

        // Ctrl+Enter to execute
        this._monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => this._execute());
        this._monacoEditor.onDidChangeModelContent(() => this._autoResize());
        this._autoResize();

        // Load pending SQL from external source (chat, analysis, etc.)
        this._loadPendingQuery();

        this._monacoEditor.focus();
    },

    _autoResize() {
        if (!this._monacoEditor) return;
        const lines = this._monacoEditor.getModel()?.getLineCount() || 10;
        const h = Math.max(120, Math.min(lines * 20 + 20, 500));
        this._monacoEditor.getContainerDomNode().style.height = h + 'px';
        this._monacoEditor.layout();
    },

    _getQuery() {
        return this._monacoEditor ? this._monacoEditor.getValue().trim() : '';
    },

    _setQuery(sql) {
        if (this._monacoEditor) {
            this._monacoEditor.setValue(sql);
            this._monacoEditor.focus();
        }
    },

    _loadPendingQuery() {
        const sql = window.__pendingSqlQuery || sessionStorage.getItem('pending_sql_query');
        if (sql) {
            this._setQuery(sql);
            window.__pendingSqlQuery = null;
            sessionStorage.removeItem('pending_sql_query');
            App.toast('Query loaded from chat', 'info');
        }
    },

    _clearEditor() { this._setQuery(''); },

    /* ── Tab Switching & Search ─────────────────────────────────── */
    _showTab(tab) {
        const historyEl = document.getElementById('sql-history-list');
        const savedEl = document.getElementById('sql-saved-list');
        const histTab = document.getElementById('sql-tab-history');
        const savedTab = document.getElementById('sql-tab-saved');

        if (tab === 'history') {
            if (historyEl) historyEl.classList.remove('hidden');
            if (savedEl) savedEl.classList.add('hidden');
            if (histTab) { histTab.className = 'flex-1 text-xs py-1.5 rounded bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400 font-medium'; }
            if (savedTab) { savedTab.className = 'flex-1 text-xs py-1.5 rounded text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'; }
        } else {
            if (historyEl) historyEl.classList.add('hidden');
            if (savedEl) savedEl.classList.remove('hidden');
            if (savedTab) { savedTab.className = 'flex-1 text-xs py-1.5 rounded bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400 font-medium'; }
            if (histTab) { histTab.className = 'flex-1 text-xs py-1.5 rounded text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'; }
        }
        this._filterQueries(document.getElementById('sql-query-search')?.value || '');
    },

    _filterQueries(query) {
        const q = (query || '').toLowerCase();
        document.querySelectorAll('#sql-history-list .query-item, #sql-saved-list .query-item').forEach(item => {
            const text = item.textContent.toLowerCase();
            item.style.display = (!q || text.includes(q)) ? '' : 'none';
        });
    },

    /* ── Visual Query Builder ─────────────────────────────────── */
    _toggleQueryBuilder() {
        const panel = document.getElementById('query-builder-panel');
        const chevron = document.getElementById('qb-chevron');
        if (panel) {
            const isHidden = panel.classList.contains('hidden');
            panel.classList.toggle('hidden', !isHidden);
            if (chevron) chevron.style.transform = isHidden ? 'rotate(180deg)' : '';
        }
    },

    async _loadTableColumns(tableName) {
        const select = document.getElementById('qb-columns');
        if (!select || !tableName) return;
        select.innerHTML = '<option value="*">* (all)</option>';
        try {
            const resp = await API.profile(tableName);
            // API wraps the payload: {success, profile: {columns: [...]}}
            const profile = resp?.profile || resp;
            const raw = profile?.columns;
            // columns may be an array of {name} objects or a plain dict
            const names = Array.isArray(raw)
                ? raw.map(c => (typeof c === 'string' ? c : c.name)).filter(Boolean)
                : Object.keys(raw || {});
            names.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c;
                opt.textContent = c;
                select.appendChild(opt);
            });
        } catch (_) {}
    },

    _buildQuery() {
        let table = document.getElementById('qb-table')?.value;
        if (!table && this._selectedTables.length === 1) table = this._selectedTables[0];
        if (!table) return App.toast(this._selectedTables.length > 1
            ? 'Visual builder is single-table — pick one below, or use AI Generate for multiple datasets'
            : 'Select a table first', 'error');

        const colSelect = document.getElementById('qb-columns');
        const selectedCols = Array.from(colSelect?.selectedOptions || []).map(o => o.value);
        const cols = selectedCols.length === 0 || selectedCols.includes('*') ? '*' : selectedCols.join(', ');

        const where = document.getElementById('qb-where')?.value?.trim();
        const group = document.getElementById('qb-group')?.value?.trim();
        const order = document.getElementById('qb-order')?.value?.trim();
        const limit = document.getElementById('qb-limit')?.value?.trim();

        let sql = `SELECT ${cols}\nFROM ${table}`;
        if (where) sql += `\nWHERE ${where}`;
        if (group) sql += `\nGROUP BY ${group}`;
        if (order) sql += `\nORDER BY ${order}`;
        if (limit) sql += `\nLIMIT ${limit}`;

        this._setQuery(sql);
        const gen = document.getElementById('qb-generated');
        if (gen) gen.textContent = 'Generated!';
    },

    async _generateSqlWithAI() {
        const promptArea = document.getElementById('qb-ai-prompt');
        const promptInput = document.getElementById('qb-ai-input');

        // Toggle AI prompt visibility
        if (promptArea?.classList.contains('hidden')) {
            promptArea.classList.remove('hidden');
            promptInput?.focus();
            return;
        }

        const prompt = promptInput?.value?.trim();
        if (!prompt) return App.toast('Enter a description of what you want to query', 'error');

        // Multi-select datasets: prefer explicit selection, else all tables,
        // else the single visual-builder table (back-compat with the chat redirect).
        let datasets = this._selectedTables.length
            ? this._selectedTables.slice()
            : this._allTableNames();
        if (!datasets.length) {
            const single = document.getElementById('qb-table')?.value || App.state.selectedTable || '';
            if (single) datasets = [single];
        }
        const gen = document.getElementById('qb-generated');

        try {
            if (gen) gen.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            const result = await API.generateSqlCode(prompt, datasets);
            if (result?.success && result.code) {
                this._setQuery(result.code);
                if (result.llm_used === false) {
                    // Rule-based fallback — be honest about what produced the SQL.
                    if (gen) gen.textContent = 'Rule-based fallback';
                    App.warning('LLM unavailable — SQL came from the rule-based fallback engine, not AI');
                } else if (result.valid === false) {
                    // LLM output that still fails EXPLAIN after a repair retry.
                    if (gen) gen.textContent = 'AI generated (unvalidated)';
                    App.warning('SQL was AI-generated but failed database validation — review before executing');
                } else {
                    if (gen) gen.textContent = 'AI generated!';
                    App.success('SQL generated by AI');
                }
            } else {
                if (gen) gen.textContent = 'Failed';
                App.error('AI generation failed: ' + (result?.error || 'Unknown'));
            }
        } catch (e) {
            if (gen) gen.textContent = 'Error';
            App.error('AI error: ' + (e.message || 'Check LLM'));
        }
    },

    /* ── Multi-select dataset picker ────────────────────────────────── */

    _allTableNames() {
        const tables = App.state.tables || {};
        if (Array.isArray(tables)) return tables.slice();
        return typeof tables === 'object' && tables !== null ? Object.keys(tables) : [];
    },

    _toggleTablePicker() {
        const panel = document.getElementById('sql-ds-panel');
        if (!panel) return;
        const willOpen = panel.classList.contains('hidden');
        panel.classList.toggle('hidden', !willOpen);
        if (willOpen) {
            this._renderTableList();
            const search = document.getElementById('sql-ds-search');
            if (search) search.value = '';
            const allEl = document.getElementById('sql-ds-all');
            if (allEl) allEl.checked = this._selectedTables.length === 0;
            if (!this._boundClose) this._boundClose = (e) => this._closePickerHandler(e);
            setTimeout(() => document.addEventListener('click', this._boundClose), 0);
        } else {
            if (this._boundClose) document.removeEventListener('click', this._boundClose);
        }
    },

    _closePickerHandler(e) {
        const picker = document.getElementById('sql-ds-picker');
        const panel = document.getElementById('sql-ds-panel');
        if (picker && panel && !picker.contains(e.target)) {
            panel.classList.add('hidden');
            if (this._boundClose) document.removeEventListener('click', this._boundClose);
        }
    },

    _renderTableList() {
        const list = document.getElementById('sql-ds-list');
        if (!list) return;
        const searchEl = document.getElementById('sql-ds-search');
        const q = (searchEl?.value || '').trim().toLowerCase();
        const tables = this._allTableNames().filter(t => !q || t.toLowerCase().includes(q));
        const sel = new Set(this._selectedTables.map(x => x.toLowerCase()));
        const allEl = document.getElementById('sql-ds-all');
        if (allEl) allEl.checked = this._selectedTables.length === 0;

        if (!tables.length) {
            list.innerHTML = '<div class="text-center text-xs text-gray-400 py-4">No matching tables</div>';
            return;
        }
        list.innerHTML = tables.map(t => {
            const checked = this._selectedTables.length === 0 || sel.has(t.toLowerCase());
            return `<label class="flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer text-sm hover:bg-gray-50 dark:hover:bg-gray-700/60">
                <input type="checkbox" class="rounded" data-table="${App.escapeHtml(t)}" ${checked ? 'checked' : ''} onchange="SqlPage._onTableCheck(this)">
                <span class="truncate">${App.escapeHtml(t)}</span>
            </label>`;
        }).join('');
    },

    _onTableCheck(el) {
        const name = el && el.dataset && el.dataset.table;
        if (!name) return;
        const lower = name.toLowerCase();
        const allMode = this._selectedTables.length === 0;

        if (el.checked) {
            if (!allMode && !this._selectedTables.some(x => x.toLowerCase() === lower)) {
                this._selectedTables.push(name);
            }
        } else {
            if (allMode) {
                this._selectedTables = this._allTableNames().filter(t => t.toLowerCase() !== lower);
            } else {
                this._selectedTables = this._selectedTables.filter(t => t.toLowerCase() !== lower);
            }
        }
        this._persistTables();
        this._updateTableUI();
        this._renderTableList();
    },

    _onAllTablesToggle(checked) {
        if (checked) this._selectedTables = [];
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
        try { sessionStorage.setItem('sql_selected_tables', JSON.stringify(this._selectedTables)); } catch (_) {}
    },

    _updateTableUI() {
        const label = document.getElementById('sql-ds-btn-label');
        if (label) {
            label.textContent = this._selectedTables.length === 0
                ? 'All tables'
                : this._selectedTables.length === 1
                    ? this._selectedTables[0]
                    : `${this._selectedTables.length} tables`;
        }
        const chips = document.getElementById('sql-ds-chips');
        if (chips) {
            if (this._selectedTables.length) {
                chips.className = 'flex flex-wrap items-center gap-1.5';
                chips.innerHTML = this._selectedTables.map((t, i) => `
                    <span class="inline-flex items-center gap-1.5 bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-800 rounded-full px-2.5 py-0.5 text-xs">
                        ${App.escapeHtml(t)}
                        <button onclick="SqlPage._removeTableByIndex(${i})" class="text-purple-400 hover:text-purple-600 dark:hover:text-purple-200" title="Remove ${App.escapeHtml(t)}"><i class="fas fa-times text-[9px]"></i></button>
                    </span>`).join('');
            } else {
                chips.className = 'hidden';
                chips.innerHTML = '';
            }
        }
    },

    _formatSQL() {
        const sql = this._getQuery();
        if (!sql) return;
        const keywords = ['SELECT','FROM','WHERE','GROUP BY','ORDER BY','HAVING',
            'LIMIT','OFFSET','JOIN','LEFT JOIN','RIGHT JOIN','INNER JOIN','OUTER JOIN',
            'ON','AND','OR','NOT','IN','AS','UNION','ALL','DISTINCT','CASE',
            'WHEN','THEN','ELSE','END'];
        let formatted = sql;
        keywords.forEach(kw => {
            formatted = formatted.replace(new RegExp('\\b' + kw.replace(' ', '\\s+') + '\\b', 'gi'), kw);
        });
        const breaks = ['SELECT','FROM','WHERE','GROUP BY','ORDER BY','HAVING','LIMIT',
            'JOIN','LEFT JOIN','RIGHT JOIN','INNER JOIN','UNION'];
        breaks.forEach(kw => {
            formatted = formatted.replace(new RegExp('\\s+(' + kw.replace(' ', '\\s+') + ')\\b', 'gi'), '\n' + kw);
        });
        this._setQuery(formatted.replace(/^\s+|\s+$/g, '').replace(/\n{3,}/g, '\n\n'));
        App.toast('SQL formatted', 'info');
    },

    /* ── Execute query ─────────────────────────────────────────────── */
    async _execute() {
        const query = this._getQuery();
        if (!query) return App.toast('Enter a SQL query', 'error');

        const resultsEl = document.getElementById('sql-results');
        const metaEl = document.getElementById('sql-meta');
        const infoEl = document.getElementById('sql-result-info');
        const actionsEl = document.getElementById('sql-results-actions');

        resultsEl.innerHTML = '<div class="flex items-center justify-center py-8"><i class="fas fa-spinner fa-spin text-2xl text-primary-500"></i></div>';
        metaEl.textContent = 'Executing...';
        actionsEl?.classList.add('hidden');

        const startTime = performance.now();
        try {
            API.logQuery(query); // Fire & forget
            const result = await API.sql(query);
            const elapsed = ((performance.now() - startTime) / 1000).toFixed(2);

            // Backend reports failures with HTTP 200 + success:false — surface them
            if (result && result.success === false) {
                metaEl.textContent = `Error (${elapsed}s)`;
                resultsEl.innerHTML = `<div class="bg-red-50 dark:bg-red-900/20 p-4 rounded-lg text-sm">
                    <i class="fas fa-exclamation-circle text-red-500 mr-2"></i>
                    <strong>Error:</strong> ${App.escapeHtml(result.error || 'Query failed')}
                    ${result.hint ? `<div class="mt-1 text-xs text-gray-500">${App.escapeHtml(result.hint)}</div>` : ''}
                </div>`;
                return;
            }

            const data = result.data || result.rows || result;
            const rowCount = result.row_count ?? (Array.isArray(data) ? data.length : 0);

            this._lastResult = { data, query };
            metaEl.textContent = `${elapsed}s`;
            infoEl.textContent = `${rowCount} row${rowCount !== 1 ? 's' : ''} | ${elapsed}s`;

            if (Array.isArray(data) && data.length > 0) {
                DataTable.render('sql-results', data, { maxRows: 500 });
                actionsEl?.classList.remove('hidden');
            } else if (typeof data === 'object' && data !== null) {
                resultsEl.innerHTML = `<div class="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg text-sm">
                    <i class="fas fa-check-circle text-green-500 mr-2"></i>Query executed successfully.
                    ${result.row_count !== undefined ? `Rows affected: ${result.row_count}` : ''}</div>`;
            } else {
                resultsEl.innerHTML = '<p class="text-gray-400 text-center py-4 text-sm">Query returned no results.</p>';
            }
            this._addToHistory(query, rowCount, elapsed);
        } catch (e) {
            const elapsed = ((performance.now() - startTime) / 1000).toFixed(2);
            metaEl.textContent = `Error (${elapsed}s)`;
            resultsEl.innerHTML = `<div class="bg-red-50 dark:bg-red-900/20 p-4 rounded-lg text-sm">
                <i class="fas fa-exclamation-circle text-red-500 mr-2"></i>
                <strong>Error:</strong> ${App.escapeHtml(e.message || 'Unknown error')}
            </div>`;
        }
    },

    /* ── Query History ─────────────────────────────────────────────── */
    _addToHistory(query, rowCount, elapsed) {
        this._history.unshift({ query: query.substring(0, 200), fullQuery: query, rows: rowCount, time: elapsed, timestamp: new Date().toLocaleTimeString() });
        if (this._history.length > 50) this._history.pop();
        this._renderHistory();
        // Mirror into the legacy localStorage key the dashboard KPI reads.
        // Key is namespaced per login so users don't see each other's queries.
        var _qhKey = (window.RBAC && window.RBAC.storeKey) ? window.RBAC.storeKey("query_history") : "query_history";
        try {
            var stored = [];
            try { stored = JSON.parse(localStorage.getItem(_qhKey) || "[]"); } catch (_) { stored = []; }
            if (!Array.isArray(stored)) stored = [];
            stored.unshift({ question: query.substring(0, 200), query: query.substring(0, 200), timestamp: new Date().toISOString() });
            localStorage.setItem(_qhKey, JSON.stringify(stored.slice(0, 50)));
        } catch (_) {}
    },

    async _loadHistory() {
        try {
            const result = await API.queryHistory();
            const items = result.history || result.queries || result || [];
            if (Array.isArray(items) && items.length) {
                this._history = items.map(q => ({
                    query: (q.query || '').substring(0, 200), fullQuery: q.query || '',
                    rows: q.row_count ?? '-', time: q.execution_time ?? '-', timestamp: q.timestamp || '',
                }));
                this._renderHistory();
            }
        } catch (_) {}
    },

    _renderHistory() {
        const el = document.getElementById('sql-history-list');
        if (!el) return;
        if (!this._history.length) { el.innerHTML = '<p class="text-gray-400 text-sm text-center py-4">No history yet.</p>'; return; }
        el.innerHTML = this._history.slice(0, 30).map((h, i) =>
            `<div class="query-item p-2 rounded bg-gray-50 dark:bg-gray-800 text-xs cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700" onclick="SqlPage._loadFromHistory(${i})" title="${App.escapeHtml(h.fullQuery || h.query)}">
                <div class="font-mono text-gray-600 dark:text-gray-300 truncate">${App.escapeHtml(h.query)}</div>
                <div class="flex justify-between text-gray-400 mt-1"><span>${h.timestamp}</span><span>${h.rows} rows | ${h.time}s</span></div>
            </div>`
        ).join('');
    },

    _loadFromHistory(index) {
        const entry = this._history[index];
        if (entry) this._setQuery(entry.fullQuery || entry.query);
    },

    /* ── Saved Queries ─────────────────────────────────────────────── */
    async _loadSavedQueries() {
        try {
            const result = await API.queryHistory();
            this._savedQueries = Array.isArray(result.saved) ? result.saved : [];
            this._renderSavedQueries();
        } catch (_) {}
    },

    _renderSavedQueries() {
        const el = document.getElementById('sql-saved-list');
        if (!el) return;
        if (!this._savedQueries.length) { el.innerHTML = '<p class="text-gray-400 text-sm text-center py-4">No saved queries.</p>'; return; }
        el.innerHTML = this._savedQueries.map((q, i) =>
            `<div class="query-item p-2 rounded bg-gray-50 dark:bg-gray-800 text-xs flex items-center justify-between group">
                <div class="flex-1 min-w-0 cursor-pointer hover:text-primary-500" onclick="SqlPage._loadSavedQuery(${i})">
                    <div class="font-medium truncate">${App.escapeHtml(q.name || 'Query ' + (i+1))}</div>
                    <div class="font-mono text-gray-400 truncate">${App.escapeHtml((q.query||'').substring(0,60))}</div>
                </div>
                <button onclick="SqlPage._deleteSavedQuery(${i})" class="text-gray-400 hover:text-red-500 ml-2 opacity-0 group-hover:opacity-100"><i class="fas fa-trash-alt text-xs"></i></button>
            </div>`
        ).join('');
    },

    _loadSavedQuery(index) {
        const entry = this._savedQueries[index];
        if (entry) this._setQuery(entry.query || entry.sql || '');
    },

    async _saveQuery() {
        const query = this._getQuery();
        if (!query) return App.toast('Nothing to save', 'error');
        const name = prompt('Query name:');
        if (!name) return;
        try {
            await API.saveQuery(name, query);
            App.toast('Query saved', 'success');
            this._loadSavedQueries();
        } catch (e) { App.toast('Save failed', 'error'); }
    },

    async _deleteSavedQuery(index) {
        const entry = this._savedQueries[index];
        if (!entry) return;
        if (typeof Modal === "undefined" || !Modal.confirm) return;
        const confirmed = await Modal.confirm('Delete Query', `Delete saved query "${entry.name}"?`);
        if (!confirmed) return;
        try {
            await API.deleteQuery(entry.name);
            this._savedQueries.splice(index, 1);
            this._renderSavedQueries();
            if (typeof App !== "undefined") App.toast('Query deleted', 'success');
        } catch (e) {
            if (typeof App !== "undefined") App.toast('Delete failed', 'error');
        }
    },

    _toggleHistory() {
        document.getElementById('sql-history-panel')?.classList.toggle('hidden');
    },

    /* ── Table schema reference ────────────────────────────────────── */
    async _showTableSchema(tableName) {
        if (!tableName) return;
        try {
            const profile = await API.profile(tableName);
            const cols = profile.profile?.columns || profile.columns || [];
            const colList = Array.isArray(cols) ? cols.map(c =>
                `<tr><td class="font-medium">${App.escapeHtml(c.name || c.column_name || '')}</td><td>${App.escapeHtml(c.dtype || c.type || '')}</td></tr>`
            ).join('') : '<tr><td colspan="2">No columns found</td></tr>';
            Modal.show(`Schema: ${tableName}`, `
                <div class="overflow-x-auto max-h-64"><table class="data-table text-sm w-full">
                    <thead><tr><th>Column</th><th>Type</th></tr></thead>
                    <tbody>${colList}</tbody>
                </table></div>
                <button onclick="SqlPage._insertTableName('${App.escapeHtml(tableName)}')" class="mt-3 bg-primary-500 hover:bg-primary-600 text-white px-4 py-1.5 rounded text-sm"><i class="fas fa-plus mr-1"></i>Insert Table Name</button>
            `);
        } catch (e) { App.toast('Could not load schema', 'error'); }
        document.getElementById('sql-ref-table').value = '';
    },

    _insertTableName(name) {
        if (this._monacoEditor) {
            const pos = this._monacoEditor.getPosition();
            this._monacoEditor.executeEdits('insert', [{
                range: { startLineNumber: pos.lineNumber, startColumn: pos.column, endLineNumber: pos.lineNumber, endColumn: pos.column },
                text: name,
            }]);
            this._monacoEditor.focus();
        }
        Modal.close();
    },

    /* ── Export results ────────────────────────────────────────────── */
    _exportResults(format) {
        if (!this._lastResult?.data) return;
        const data = this._lastResult.data;
        if (format === 'csv' && Array.isArray(data) && data.length > 0) {
            const cols = Object.keys(data[0]);
            const csv = [cols.join(',')].concat(data.map(r =>
                cols.map(c => { const v = r[c]; return v === null || v === undefined ? '' : String(v).includes(',') ? `"${String(v).replace(/"/g, '""')}"` : String(v); }).join(',')
            )).join('\n');
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a'); a.href = url; a.download = 'query_results.csv';
            a.click(); URL.revokeObjectURL(url);
        }
    },
};

window.SqlPage = SqlPage;
(window._PAGES = window._PAGES || {})["#sql"] = SqlPage;
