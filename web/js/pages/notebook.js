/* ═══════════════════════════════════════════
   Notebook Workbench — Cell-Based Analysis
   SQL, Python, Markdown, and Viz cells
   ═══════════════════════════════════════════ */

const Notebook = {
    _cells: [],
    _notebookName: 'Untitled',
    _dirty: false,

    CELL_TYPES: [
        { type: 'sql', icon: 'fa-database', label: 'SQL', color: 'blue' },
        { type: 'markdown', icon: 'fa-paragraph', label: 'Markdown', color: 'green' },
        { type: 'code', icon: 'fa-code', label: 'Python', color: 'purple' },
    ],

    async init() {
        this._cells = [];
        this._notebookName = sessionStorage.getItem('nb_current_name') || 'Untitled';
        const saved = sessionStorage.getItem('nb_state_' + this._notebookName);
        if (saved) {
            try { this._cells = JSON.parse(saved); } catch (_) { this._cells = []; }
        }
        this._dirty = false;
    },

    async destroy() {
        // Clean up keyboard listener (destroyPage() delegates here — never call it back,
        // or destroy ↔ destroyPage recurse infinitely).
        document.removeEventListener('keydown', this._globalKeyHandler);
        if (this._dirty) {
            try { sessionStorage.setItem('nb_state_' + this._notebookName, JSON.stringify(this._cells)); } catch (_) {}
            try { sessionStorage.setItem('nb_current_name', this._notebookName); } catch (_) {}
        }
    },

    async render() {
        const pc = document.getElementById('page-content');
        pc.innerHTML = `
        <div class="max-w-5xl mx-auto animate-fade-in">
            <!-- Header -->
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-3">
                    <i class="fas fa-book-open text-primary-500 text-xl"></i>
                    <input id="nb-title" class="text-xl font-bold bg-transparent border-0 border-b-2 border-transparent hover:border-gray-300 focus:border-primary-500 focus:outline-none px-1"
                        value="${App.escapeHtml(this._notebookName)}"
                        onchange="Notebook._rename(this.value)"
                        placeholder="Notebook name...">
                    <span id="nb-status" class="text-xs text-gray-400">${this._dirty ? 'Unsaved' : 'Saved'}</span>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="Notebook._addCell('sql')" class="text-sm bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-800 px-3 py-1.5 rounded-lg hover:bg-blue-100"><i class="fas fa-database mr-1"></i>SQL</button>
                    <button onclick="Notebook._addCell('markdown')" class="text-sm bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 border border-green-200 dark:border-green-800 px-3 py-1.5 rounded-lg hover:bg-green-100"><i class="fas fa-paragraph mr-1"></i>Markdown</button>
                    <button onclick="Notebook._addCell('code')" class="text-sm bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400 border border-purple-200 dark:border-purple-800 px-3 py-1.5 rounded-lg hover:bg-purple-100"><i class="fas fa-code mr-1"></i>Python</button>
                    <div class="w-px h-6 bg-gray-300 dark:bg-gray-600 mx-1"></div>
                    <button onclick="Notebook._runAll()" class="text-sm bg-primary-500 hover:bg-primary-600 text-white px-3 py-1.5 rounded-lg"><i class="fas fa-play mr-1"></i>Run All</button>
                    <button onclick="Notebook._clearOutputs()" class="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-2 py-1.5"><i class="fas fa-eraser"></i></button>
                    <button onclick="Notebook._exportHTML()" class="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-2 py-1.5" title="Export as HTML"><i class="fas fa-file-export"></i></button>
                </div>
            </div>

            <!-- Cells Container -->
            <div id="nb-cells" class="space-y-3">
                ${this._cells.length === 0 ? `
                <div class="text-center py-16 text-gray-400">
                    <i class="fas fa-plus-circle text-5xl mb-4 opacity-30"></i>
                    <p class="text-lg font-medium">Start your analysis notebook</p>
                    <p class="text-sm mt-1">Click "SQL", "Markdown", or "Python" above to add a cell</p>
                </div>` :
                this._cells.map((c, i) => this._renderCell(c, i)).join('')}
            </div>

            <!-- Mini Add Bar -->
            <div class="mt-4 flex justify-center gap-2 opacity-60 hover:opacity-100 transition-opacity">
                <button onclick="Notebook._addCell('sql')" class="text-xs text-blue-500 border border-dashed border-blue-300 dark:border-blue-700 px-3 py-1.5 rounded-full hover:bg-blue-50 dark:hover:bg-blue-900/20"><i class="fas fa-plus mr-1"></i>SQL</button>
                <button onclick="Notebook._addCell('markdown')" class="text-xs text-green-500 border border-dashed border-green-300 dark:border-green-700 px-3 py-1.5 rounded-full hover:bg-green-50 dark:hover:bg-green-900/20"><i class="fas fa-plus mr-1"></i>Markdown</button>
                <button onclick="Notebook._addCell('code')" class="text-xs text-purple-500 border border-dashed border-purple-300 dark:border-purple-700 px-3 py-1.5 rounded-full hover:bg-purple-50 dark:hover:bg-purple-900/20"><i class="fas fa-plus mr-1"></i>Python</button>
            </div>
        </div>`;

        // Render cell content (notably Monaco editors for SQL cells)
        this._cells.forEach((c, i) => this._postRenderCell(c, i));

        // Listen for Ctrl+Enter to run current cell
        document.addEventListener('keydown', this._globalKeyHandler);
    },

    async destroyPage() {
        document.removeEventListener('keydown', this._globalKeyHandler);
        await this.destroy();
    },

    _globalKeyHandler(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            // Find which cell has focus and run it
            const active = document.activeElement;
            const cellEl = active?.closest('[data-cell-id]');
            if (cellEl) {
                e.preventDefault();
                Notebook._runCell(cellEl.dataset.cellId);
            }
        }
    },

    /* ── Cell Rendering ──────────────────────────────────────────── */
    _renderCell(cell, index) {
        const id = cell.id || 'cell-' + Date.now() + '-' + index;
        cell.id = id;

        const typeIcon = { sql: 'fa-database blue', markdown: 'fa-paragraph green', code: 'fa-code purple' };
        const [icon, color] = (typeIcon[cell.type] || 'fa-circle gray').split(' ');
        const noteExpanded = cell._noteOpen || false;

        return `
        <div class="nb-cell border rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow bg-white dark:bg-gray-800 dark:border-gray-700"
            data-cell-id="${id}" data-type="${cell.type}">
            <!-- Cell Header -->
            <div class="flex items-center justify-between px-4 py-2 bg-gray-50 dark:bg-gray-700/50 border-b dark:border-gray-700">
                <div class="flex items-center gap-2">
                    <i class="fas ${icon} text-${color}-500 text-xs"></i>
                    <span class="text-xs font-medium text-gray-500 uppercase tracking-wider">${cell.type}</span>
                    <span class="text-xs text-gray-400">#${index + 1}</span>
                    ${cell.note ? '<span class="text-xs text-indigo-400" title="Has annotation"><i class="fas fa-comment-dots"></i></span>' : ''}
                </div>
                <div class="flex items-center gap-1">
                    <button onclick="Notebook._toggleNote('${id}')" class="text-xs ${cell.note ? 'text-indigo-500' : 'text-gray-400'} hover:text-indigo-600 p-1" title="Add note/annotation"><i class="fas fa-comment-dots"></i></button>
                    <button onclick="Notebook._moveCell('${id}', -1)" class="text-xs text-gray-400 hover:text-gray-600 p-1" title="Move up"><i class="fas fa-chevron-up"></i></button>
                    <button onclick="Notebook._moveCell('${id}', 1)" class="text-xs text-gray-400 hover:text-gray-600 p-1" title="Move down"><i class="fas fa-chevron-down"></i></button>
                    <button onclick="Notebook._runCell('${id}')" class="text-xs text-green-500 hover:text-green-700 p-1 ml-2" title="Run cell"><i class="fas fa-play"></i></button>
                    <button onclick="Notebook._deleteCell('${id}')" class="text-xs text-gray-400 hover:text-red-500 p-1 ml-2" title="Delete"><i class="fas fa-times"></i></button>
                </div>
            </div>
            <!-- Cell Note (toggleable) -->
            <div id="nb-note-${id}" class="px-4 py-2 bg-indigo-50 dark:bg-indigo-900/10 border-b dark:border-gray-700 ${noteExpanded || cell.note ? '' : 'hidden'}">
                <textarea class="w-full text-xs italic text-indigo-700 dark:text-indigo-300 bg-transparent border-0 resize-y min-h-[28px] focus:outline-none placeholder-indigo-300 dark:placeholder-indigo-700"
                    placeholder="Why did you do this? (Note for your future self)"
                    oninput="Notebook._updateNote('${id}', this.value)">${App.escapeHtml(cell.note || '')}</textarea>
            </div>
            <!-- Cell Input -->
            <div class="cell-input">
                ${cell.type === 'markdown'
                    ? `<textarea class="w-full p-4 font-mono text-sm bg-white dark:bg-gray-800 dark:text-gray-200 border-0 resize-y min-h-[80px] focus:outline-none" placeholder="Write markdown... (Heading, **bold**, lists, etc.)">${App.escapeHtml(cell.source || '')}</textarea>`
                    : `<textarea class="w-full p-4 font-mono text-xs bg-gray-50 dark:bg-gray-900 dark:text-gray-200 border-0 resize-y min-h-[80px] focus:outline-none" placeholder="${cell.type === 'sql' ? 'SELECT * FROM table_name LIMIT 100' : '# Python code'}" spellcheck="false">${App.escapeHtml(cell.source || '')}</textarea>`
                }
            </div>
            <!-- Cell Output -->
            <div id="nb-output-${id}" class="cell-output border-t dark:border-gray-700 ${cell.output ? '' : 'hidden'}">
                ${cell.output ? this._renderOutput(cell.output) : ''}
            </div>
            <!-- Cell Status -->
            <div class="flex items-center gap-2 px-4 py-1.5 bg-gray-50/50 dark:bg-gray-700/30 border-t dark:border-gray-700 text-xs text-gray-400">
                <span id="nb-status-${id}">${cell.lastRun ? 'Last run: ' + cell.lastRun : 'Ready'}</span>
                ${cell.elapsed ? `<span>| ${cell.elapsed}s</span>` : ''}
                ${cell.output?.type === 'table' && cell.output.data ? `<span>| ${cell.output.data.length} rows</span>` : ''}
            </div>
        </div>`;
    },

    _postRenderCell(cell, index) {
        // After DOM render — attach Monaco-like editing or just focus-ready
        const el = document.querySelector(`[data-cell-id="${cell.id}"] .cell-input textarea`);
        if (el) {
            el.addEventListener('keydown', (e) => {
                if (e.key === 'Tab') {
                    e.preventDefault();
                    const start = el.selectionStart;
                    const end = el.selectionEnd;
                    el.value = el.value.substring(0, start) + '    ' + el.value.substring(end);
                    el.selectionStart = el.selectionEnd = start + 4;
                }
            });
            // Auto-resize
            el.addEventListener('input', () => {
                el.style.height = 'auto';
                el.style.height = Math.max(80, el.scrollHeight) + 'px';
                // Save source
                const cellObj = this._cells.find(c => c.id === cell.id);
                if (cellObj) { cellObj.source = el.value; this._markDirty(); }
            });
        }
    },

    _renderOutput(output) {
        if (!output) return '';
        if (output.type === 'table' && output.data?.length) {
            const d = output.data;
            const cols = Object.keys(d[0]);
            return `<div class="p-3 overflow-x-auto"><table class="w-full text-xs border-collapse">
                <thead><tr>${cols.map(c => `<th class="px-2 py-1.5 text-left font-semibold text-gray-600 dark:text-gray-300 border-b whitespace-nowrap">${App.escapeHtml(c)}</th>`).join('')}</tr></thead>
                <tbody>${d.slice(0, 50).map((r, i) => `<tr class="${i%2===0?'bg-white dark:bg-gray-800':'bg-gray-50 dark:bg-gray-800/60'}">${cols.map(c => `<td class="px-2 py-1.5 border-b truncate max-w-[200px]">${r[c]===null?'<span class="text-gray-300 italic">NULL</span>':App.escapeHtml(String(r[c]))}</td>`).join('')}</tr>`).join('')}</tbody>
            </table>${d.length > 50 ? `<p class="text-xs text-gray-400 text-center py-1">Showing 50 of ${d.length} rows</p>` : ''}</div>`;
        }
        if (output.type === 'text') {
            return `<div class="p-4 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-mono">${App.escapeHtml(output.content || '')}</div>`;
        }
        if (output.type === 'chart' && output.html) {
            return `<div class="p-2">${output.html}</div>`;
        }
        if (output.type === 'markdown') {
            return `<div class="p-4 text-sm prose prose-sm dark:prose-invert max-w-none">${output.html || App.escapeHtml(output.content || '')}</div>`;
        }
        if (output.type === 'error') {
            return `<div class="p-4 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(output.content || '')}</div>`;
        }
        return '<div class="p-4 text-sm text-gray-400">No output</div>';
    },

    /* ── Markdown Renderer ────────────────────────────────────────── */
    _renderMarkdown(md) {
        if (!md) return '';
        let html = App.escapeHtml(md);

        // Code blocks (```lang ... ```)
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
            return `<pre class="bg-gray-800 text-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-xs font-mono"><code>${code.trim()}</code></pre>`;
        });

        // Tables: detect markdown tables (| col | col | ...)
        html = html.replace(/^(\|.+\|)\n(\|[-| :]+\|)\n((?:\|.+\|\n?)+)/gm, (header, sep, body) => {
            const headers = header.split('|').filter(c => c.trim()).map(c => `<th class="px-3 py-2 text-left font-semibold border-b border-gray-200 dark:border-gray-600">${c.trim()}</th>`);
            const rows = body.trim().split('\n').map(row => {
                const cells = row.split('|').filter(c => c.trim() || c === '|').map(c => `<td class="px-3 py-2 border-b border-gray-100 dark:border-gray-700">${c.trim()}</td>`);
                return `<tr class="hover:bg-gray-50 dark:hover:bg-gray-700/20">${cells.join('')}</tr>`;
            });
            return `<div class="overflow-x-auto my-2"><table class="w-full text-sm border-collapse">${headers.join('')}</thead><tbody>${rows.join('')}</tbody></table></div>`;
        });

        // Headings
        html = html.replace(/^#### (.+)$/gm, '<h4 class="text-base font-semibold mt-2 mb-1 text-gray-700 dark:text-gray-300">$1</h4>');
        html = html.replace(/^### (.+)$/gm, '<h3 class="text-lg font-semibold mt-3 mb-1">$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2 class="text-xl font-semibold mt-4 mb-2">$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1 class="text-2xl font-bold mt-4 mb-2">$1</h1>');

        // Bold, italic, inline code
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        html = html.replace(/`([^`]+)`/g, '<code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-xs font-mono">$1</code>');

        // Links
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" class="text-primary-500 hover:underline" target="_blank">$1</a>');

        // Unordered lists
        html = html.replace(/^- (.+)$/gm, '<li class="ml-4 mb-1">• $1</li>');

        // Blockquotes
        html = html.replace(/^&gt; (.+)$/gm, '<blockquote class="border-l-4 border-primary-300 pl-3 italic text-gray-600 dark:text-gray-400 my-2">$1</blockquote>');

        // Horizontal rules
        html = html.replace(/^---$/gm, '<hr class="my-4 border-gray-200 dark:border-gray-700">');

        // Line breaks (preserve in code blocks)
        const preBlocks = [];
        html = html.replace(/(<pre[\s\S]*?<\/pre>)/gi, (m) => { preBlocks.push(m); return `%%PRE${preBlocks.length - 1}%%`; });
        html = html.replace(/\n/g, '<br>');
        preBlocks.forEach((b, i) => { html = html.replace(`%%PRE${i}%%`, b); });

        // Wrap loose text in paragraphs
        html = html.replace(/^(?!<[hluopbtd]|<\/|<hr|<a |<code|<strong|<em|<table|<div|<pre|<blockquote|%%)(.+)$/gm, '<p class="mb-1">$1</p>');

        return html;
    },

    /* ── Cell Management ─────────────────────────────────────────── */
    _addCell(type) {
        const cell = {
            id: 'cell-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6),
            type,
            source: '',
            output: null,
            lastRun: null,
            elapsed: null,
            note: '',
            _noteOpen: false,
        };
        this._cells.push(cell);
        this._markDirty();
        // Re-render entire notebook (simple approach)
        this.render();
        // Focus on new cell
        setTimeout(() => {
const el = document.querySelector(`[data-cell-id="${cell.id}"] .cell-input textarea`);
            if (el) el.focus();
        }, 50);
    },

    _deleteCell(id) {
        this._cells = this._cells.filter(c => c.id !== id);
        this._markDirty();
        this.render();
    },

    _moveCell(id, direction) {
        const idx = this._cells.findIndex(c => c.id === id);
        if (idx < 0) return;
        const newIdx = idx + direction;
        if (newIdx < 0 || newIdx >= this._cells.length) return;
        [this._cells[idx], this._cells[newIdx]] = [this._cells[newIdx], this._cells[idx]];
        this._markDirty();
        this.render();
    },

    _rename(name) {
        if (name.trim()) { this._notebookName = name.trim(); this._markDirty(); }
    },

    _toggleNote(id) {
        const cell = this._cells.find(c => c.id === id);
        if (!cell) return;
        cell._noteOpen = !cell._noteOpen;
        const noteEl = document.getElementById('nb-note-' + id);
        if (noteEl) noteEl.classList.toggle('hidden', !cell._noteOpen);
    },

    _updateNote(id, value) {
        const cell = this._cells.find(c => c.id === id);
        if (!cell) return;
        cell.note = value;
        this._markDirty();
    },

    _markDirty() {
        this._dirty = true;
        const el = document.getElementById('nb-status');
        if (el) el.textContent = 'Unsaved';
        // Auto-save to session storage on change
        try { sessionStorage.setItem('nb_state_' + this._notebookName, JSON.stringify(this._cells)); } catch (_) {}
    },

    /* ── Cell Execution ──────────────────────────────────────────── */
    async _runCell(id) {
        const idx = this._cells.findIndex(c => c.id === id);
        if (idx < 0) return;
        const cell = this._cells[idx];

        // Update source from textarea
        const textarea = document.querySelector(`[data-cell-id="${id}"] .cell-input textarea`);
        if (textarea) cell.source = textarea.value;

        // Show running state
        const statusEl = document.getElementById('nb-status-' + id);
        if (statusEl) statusEl.textContent = 'Running...';

        const startTime = performance.now();

        try {
            if (cell.type === 'sql') {
                if (!cell.source.trim()) {
                    cell.output = { type: 'error', content: 'Empty SQL query' };
                } else {
                    const result = await API.sql(cell.source);
                    const data = result.data || result.rows || [];
                    cell.output = {
                        type: 'table',
                        data: Array.isArray(data) ? data : [],
                        row_count: result.row_count || (Array.isArray(data) ? data.length : 0),
                    };
                }
            } else if (cell.type === 'code') {
                try {
                    const result = await API.execute(cell.source, 'python', {});
                    if (result.success) {
                        cell.output = { type: 'text', content: result.output || 'Code executed (no output)' };
                        if (result.data) {
                            cell.output.type = 'table';
                            cell.output.data = result.data;
                        }
                    } else {
                        cell.output = { type: 'error', content: result.error || 'Execution failed' };
                    }
                } catch (e) {
                    cell.output = { type: 'error', content: e.message || 'Execution failed' };
                }
            } else if (cell.type === 'markdown') {
                // Render markdown to HTML
                const md = cell.source || '';
                let html = this._renderMarkdown(md);
                cell.output = { type: 'markdown', html: `<div class="prose prose-sm dark:prose-invert max-w-none">${html}</div>`, content: md };
            }
            cell.lastRun = new Date().toLocaleTimeString();
            cell.elapsed = ((performance.now() - startTime) / 1000).toFixed(2);
        } catch (e) {
            cell.output = { type: 'error', content: e.message || 'Execution failed' };
            cell.lastRun = new Date().toLocaleTimeString();
            cell.elapsed = ((performance.now() - startTime) / 1000).toFixed(2);
        }

        // Update output display
        const outputEl = document.getElementById('nb-output-' + id);
        if (outputEl) {
            if (cell.output) {
                outputEl.innerHTML = this._renderOutput(cell.output);
                outputEl.classList.remove('hidden');
            } else {
                outputEl.classList.add('hidden');
            }
        }
        if (statusEl) statusEl.textContent = cell.lastRun ? 'Last run: ' + cell.lastRun : 'Ready';
        this._markDirty();
    },

    async _runAll() {
        for (const cell of this._cells) {
            await this._runCell(cell.id);
        }
        App.success('All cells executed');
    },

    _clearOutputs() {
        this._cells.forEach(c => { c.output = null; c.lastRun = null; c.elapsed = null; });
        this._cells.forEach(c => {
            const el = document.getElementById('nb-output-' + c.id);
            if (el) { el.innerHTML = ''; el.classList.add('hidden'); }
        });
        this._markDirty();
    },

    /* ── Export ──────────────────────────────────────────────────── */
    _exportHTML() {
        const title = this._notebookName || 'Notebook';
        let html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${App.escapeHtml(title)}</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
            <style>body{font-family:'Inter',sans-serif;max-width:800px;margin:0 auto;padding:2rem;line-height:1.6}
                pre{background:#1e1e2e;color:#cdd6f4;padding:1rem;border-radius:8px;overflow-x:auto;font-size:13px}
                table{border-collapse:collapse;width:100%;font-size:13px}
                th,td{border:1px solid #e2e8f0;padding:6px 10px;text-align:left}
                th{background:#f8fafc;font-weight:600}
                .cell{border:1px solid #e2e8f0;border-radius:8px;margin:12px 0;overflow:hidden}
                .cell-header{background:#f8fafc;padding:6px 12px;font-size:11px;font-weight:600;text-transform:uppercase;color:#64748b;border-bottom:1px solid #e2e8f0}
                .cell-source{padding:12px;background:#f1f5f9;font-family:monospace;font-size:12px;white-space:pre-wrap}
                .cell-output{padding:12px}
            </style></head><body>
            <h1 class="text-2xl font-bold mb-6">${App.escapeHtml(title)}</h1>
            <p class="text-sm text-gray-500 mb-6">Generated on ${new Date().toLocaleString()}</p>`;

        this._cells.forEach((cell, i) => {
            html += `<div class="cell"><div class="cell-header">${cell.type.toUpperCase()} Cell #${i + 1}`;
            if (cell.note) html += ` <span style="color:#818cf8;font-style:italic;font-weight:normal;margin-left:8px">"${App.escapeHtml(cell.note)}"</span>`;
            html += `</div>`;
            if (cell.source) {
                html += `<div class="cell-source">${App.escapeHtml(cell.source)}</div>`;
            }
            if (cell.output) {
                html += `<div class="cell-output">`;
                if (cell.output.type === 'table' && cell.output.data?.length) {
                    const d = cell.output.data;
                    const cols = Object.keys(d[0]);
                    html += '<table><thead><tr>' + cols.map(c => `<th>${App.escapeHtml(c)}</th>`).join('') + '</tr></thead><tbody>';
                    d.slice(0, 100).forEach(r => {
                        html += '<tr>' + cols.map(c => `<td>${App.escapeHtml(String(r[c] ?? ''))}</td>`).join('') + '</tr>';
                    });
                    html += '</tbody></table>';
                    if (d.length > 100) html += `<p class="text-xs text-gray-400 mt-1">Showing 100 of ${d.length} rows</p>`;
                } else if (cell.output.type === 'markdown') {
                    html += cell.output.html || App.escapeHtml(cell.output.content || '');
                } else {
                    html += `<pre>${App.escapeHtml(cell.output.content || '')}</pre>`;
                }
                html += '</div>';
            }
            html += '</div>';
        });

        html += '</body></html>';

        // Download as HTML file
        const blob = new Blob([html], { type: 'text/html' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = (this._notebookName || 'notebook').replace(/\s+/g, '_') + '.html';
        a.click();
        URL.revokeObjectURL(url);
        App.success('Notebook exported as HTML');
    },
};

window.Notebook = Notebook;
(window._PAGES = window._PAGES || {})["#notebook"] = Notebook;
