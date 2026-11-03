/* ═══════════════════════════════════════════
   Warehouse Page — Table list, preview, manage
   ═══════════════════════════════════════════ */

const WarehousePage = {
    _tables: [],
    _selected: new Set(),
    _previewOpen: new Map(),
    _searchQuery: "",

    async init() {},

    async destroy() {
        this._selected.clear();
        this._previewOpen.clear();
    },

    async render() {
        const pc = document.getElementById("page-content");

        pc.innerHTML = `
        <div class="max-w-6xl mx-auto space-y-6 animate-fade-in">
            <!-- Header -->
            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                    <div class="page-title">
                        <div class="page-title-icon"><i class="fas fa-database"></i></div>
                        <div>
                            <h2>Warehouse</h2>
                            <p class="page-title-subtitle">Manage your data tables</p>
                        </div>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="WarehousePage.clearAll()" class="btn btn-sm btn-danger">
                        <i class="fas fa-trash-alt"></i>Clear All
                    </button>
                    <button onclick="WarehousePage.refresh()" class="icon-btn" title="Refresh tables">
                        <i class="fas fa-sync-alt"></i>
                    </button>
                </div>
            </div>

            <!-- Search + Bulk Actions -->
            <div class="flex flex-col sm:flex-row gap-3">
                <div class="relative flex-1">
                    <i class="fas fa-search pointer-events-none" aria-hidden="true" style="position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--color-text-muted);"></i>
                    <input type="text" id="wh-search" placeholder="Search tables..." aria-label="Search tables" class="w-full pl-9 pr-4 py-2 border rounded-lg dark:bg-gray-700 dark:border-gray-600 text-sm" oninput="WarehousePage.onSearch(this.value)">
                </div>
                <div class="flex items-center gap-2">
                    <label class="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" id="wh-select-all" onchange="WarehousePage.toggleSelectAll(this.checked)" class="rounded">
                        Select All
                    </label>
                    <span id="wh-selected-count" class="text-xs text-gray-400"></span>
                </div>
            </div>

            <!-- Table List -->
            <div id="wh-table-list" class="space-y-3">
                <div class="text-center py-12 text-gray-400"><i class="fas fa-spinner fa-spin text-2xl"></i><p class="mt-2">Loading tables...</p></div>
            </div>

            <!-- Quick Actions (shown when tables selected) -->
            <div id="wh-quick-actions" class="card hidden">
                <h3 class="text-sm font-semibold mb-3">Quick Actions (<span id="wh-action-count">0</span> selected)</h3>
                <div class="flex flex-wrap gap-2">
                    <button onclick="WarehousePage.sendToAnalysis()" class="btn btn-sm btn-primary">
                        <i class="fas fa-microscope"></i>Send to Analysis
                    </button>
                    <button onclick="WarehousePage.sendToSQL()" class="btn btn-sm btn-secondary">
                        <i class="fas fa-code"></i>Send to SQL
                    </button>
                    <button onclick="WarehousePage.deleteSelected()" class="btn btn-sm btn-danger">
                        <i class="fas fa-trash"></i>Delete Selected
                    </button>
                </div>
            </div>

            <!-- Upload Additional File -->
            <div class="card">
                <h3 class="text-sm font-semibold mb-3"><i class="fas fa-plus-circle mr-1"></i>Upload Additional File</h3>
                <div class="flex items-center gap-3">
                    <input type="file" id="wh-file-input" class="form-control flex-1" accept=".csv,.xlsx,.xls,.parquet,.json,.tsv">
                    <input type="text" id="wh-table-name" placeholder="Table name (optional)" class="form-control w-48">
                    <button onclick="WarehousePage.uploadQuick()" class="btn btn-sm btn-primary whitespace-nowrap">
                        <i class="fas fa-upload"></i>Upload &amp; Load
                    </button>
                </div>
                <div id="wh-upload-status" class="mt-2 text-sm"></div>
            </div>
        </div>`;

        await this._loadTables();
    },

    // ── Load tables ──────────────────────────────────────────────
    async _loadTables() {
        try {
            const result = await API.tables();
            const raw = result.tables || result || {};
            // Backend returns {tables: {name: {table_name, columns, row_count, sample}}} — convert to array
            if (Array.isArray(raw)) {
                this._tables = raw;
            } else if (typeof raw === 'object' && raw !== null) {
                this._tables = Object.entries(raw).map(([name, info]) => ({
                    name: name,
                    table_name: name,
                    row_count: info.row_count || 0,
                    columns: Array.isArray(info.columns) ? info.columns.length : 0,
                    column_count: Array.isArray(info.columns) ? info.columns.length : 0,
                    schema: info.schema || '',
                    sample: info.sample || [],
                }));
            } else {
                this._tables = [];
            }
            this._renderList();
        } catch (e) {
            console.warn("Failed to load tables:", e);
            this._tables = [];
            this._renderList();
        }
    },

    // ── Render table list ─────────────────────────────────────────
    _renderList() {
        const container = document.getElementById("wh-table-list");
        if (!container) return;

        const query = this._searchQuery.toLowerCase();
        const filtered = this._tables.filter((t) => {
            const name = (typeof t === "string" ? t : t.name || t.table || "").toLowerCase();
            return !query || name.includes(query);
        });

        if (filtered.length === 0) {
            container.innerHTML = `
                <div class="text-center py-12 text-gray-400">
                    <i class="fas fa-inbox text-3xl mb-3"></i>
                    <p>${this._tables.length === 0 ? "No tables in warehouse" : "No tables match your search"}</p>
                    ${this._tables.length === 0 ? '<p class="text-sm mt-1">Upload a file or connect to a database to get started</p>' : ""}
                </div>`;
            return;
        }

        let html = "";
        filtered.forEach((t, idx) => {
            const name = typeof t === "string" ? t : (t.name || t.table_name || t.table || "");
            const rows = typeof t === "object" ? (t.row_count || t.rows || 0) : 0;
            const cols = typeof t === "object" ? (t.column_count || (Array.isArray(t.columns) ? t.columns.length : 0)) : 0;
            const checked = this._selected.has(name) ? "checked" : "";
            const previewId = "preview-" + name.replace(/[^a-zA-Z0-9]/g, "_");
            const profileId = "profile-" + name.replace(/[^a-zA-Z0-9]/g, "_");
            const isOpen = this._previewOpen.get(name) || false;

            html += `
            <div class="card p-0 overflow-hidden" id="row-${idx}">
                <div class="flex items-center gap-3 p-4">
                    <input type="checkbox" class="rounded wh-row-check" data-table="${App.escapeHtml(name)}" ${checked} onchange="WarehousePage.toggleSelect('${App.escapeHtml(name)}', this.checked)">
                    <div class="w-10 h-10 rounded-lg bg-primary-50 dark:bg-primary-900/30 flex items-center justify-center flex-shrink-0">
                        <i class="fas fa-table text-primary-500"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="font-medium truncate">${App.escapeHtml(name)}</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400">${rows.toLocaleString()} rows &middot; ${cols} columns</div>
                    </div>
                    <div class="flex items-center gap-1 flex-shrink-0">
                        <button onclick="WarehousePage.togglePreview('${App.escapeHtml(name)}')" class="icon-btn icon-btn-label" title="Preview data">
                            <i class="fas fa-eye"></i>Preview
                        </button>
                        <button onclick="WarehousePage._showCleanPanel('${App.escapeHtml(name)}')" class="icon-btn" title="Clean data">
                            <i class="fas fa-broom"></i>
                        </button>
                        <button onclick="WarehousePage._showDictionary('${App.escapeHtml(name)}')" class="icon-btn" title="Data dictionary">
                            <i class="fas fa-book"></i>
                        </button>
                        <button onclick="WarehousePage.sendSingleToAnalysis('${App.escapeHtml(name)}')" class="icon-btn" title="Send to Analysis">
                            <i class="fas fa-microscope"></i>
                        </button>
                        <button onclick="WarehousePage.sendSingleToSQL('${App.escapeHtml(name)}')" class="icon-btn" title="Send to SQL">
                            <i class="fas fa-code"></i>
                        </button>
                        <button onclick="WarehousePage.deleteTable('${App.escapeHtml(name)}')" class="icon-btn icon-btn-danger" title="Delete table">
                            <i class="fas fa-trash-alt"></i>
                        </button>
                    </div>
                </div>
                <div id="${previewId}" class="border-t border-gray-100 dark:border-gray-700 ${isOpen ? "" : "hidden"}">
                    <div id="${profileId}" class="border-b border-gray-100 dark:border-gray-700">
                        <div class="p-4 text-sm text-gray-400 text-center"><i class="fas fa-spinner fa-spin"></i> Analyzing data profile...</div>
                    </div>
                    <div id="clean-${name.replace(/[^a-zA-Z0-9]/g, "_")}" class="border-b border-gray-100 dark:border-gray-700 hidden"></div>
                    <div class="p-4 text-sm text-gray-400 text-center"><i class="fas fa-spinner fa-spin"></i> Loading preview...</div>
                </div>
            </div>`;
        });

        container.innerHTML = html;

        // Restore open previews
        this._previewOpen.forEach((isOpen, tableName) => {
            if (isOpen) this._loadPreviewData(tableName);
        });
    },

    // ── Search ───────────────────────────────────────────────────
    onSearch(query) {
        this._searchQuery = query;
        this._renderList();
    },

    // ── Select / Deselect ─────────────────────────────────────────
    toggleSelect(name, checked) {
        if (checked) {
            this._selected.add(name);
        } else {
            this._selected.delete(name);
        }
        this._updateSelectionUI();
    },

    toggleSelectAll(checked) {
        const query = this._searchQuery.toLowerCase();
        const filtered = this._tables.filter((t) => {
            const name = (typeof t === "string" ? t : t.name || t.table || "").toLowerCase();
            return !query || name.includes(query);
        });
        filtered.forEach((t) => {
            const name = typeof t === "string" ? t : (t.name || t.table || "");
            if (checked) {
                this._selected.add(name);
            } else {
                this._selected.delete(name);
            }
        });

        // Update individual checkboxes
        document.querySelectorAll(".wh-row-check").forEach((cb) => {
            cb.checked = checked;
        });

        this._updateSelectionUI();
    },

    _updateSelectionUI() {
        const count = this._selected.size;
        const countEl = document.getElementById("wh-selected-count");
        const actionsEl = document.getElementById("wh-quick-actions");
        const actionCount = document.getElementById("wh-action-count");
        const selectAll = document.getElementById("wh-select-all");

        if (countEl) countEl.textContent = count > 0 ? `${count} selected` : "";
        if (actionsEl) actionsEl.classList.toggle("hidden", count === 0);
        if (actionCount) actionCount.textContent = count;
        if (selectAll) selectAll.checked = count > 0 && count === this._tables.length;
    },

    // ── Preview ──────────────────────────────────────────────────
    async togglePreview(name) {
        const previewId = "preview-" + name.replace(/[^a-zA-Z0-9]/g, "_");
        const el = document.getElementById(previewId);
        if (!el) return;

        const isOpen = this._previewOpen.get(name) || false;
        if (isOpen) {
            el.classList.add("hidden");
            this._previewOpen.set(name, false);
            return;
        }

        el.classList.remove("hidden");
        this._previewOpen.set(name, true);

        // Load profile + data in parallel
        await Promise.all([
            this._loadProfile(name),
            this._loadPreviewData(name),
        ]);
    },

    async _loadPreviewData(name) {
        const previewId = "preview-" + name.replace(/[^a-zA-Z0-9]/g, "_");
        const el = document.getElementById(previewId);
        if (!el) return;

        el.innerHTML = '<div class="p-4 text-sm text-gray-400 text-center"><i class="fas fa-spinner fa-spin"></i> Loading preview...</div>';

        try {
            const result = await API.tableSample(name, 5);
            const rows = result.data || result.rows || result || [];
            if (!Array.isArray(rows) || rows.length === 0) {
                el.innerHTML = '<div class="p-4 text-sm text-gray-400 text-center">No data available</div>';
                return;
            }

            const cols = Object.keys(rows[0]);
            let html = `<div class="flex items-center justify-between px-3 py-2 bg-gray-50 dark:bg-gray-700/30 border-b border-gray-200 dark:border-gray-700">
                <span class="text-xs font-semibold text-gray-500"><i class="fas fa-table mr-1"></i>Data Preview (${rows.length} rows)</span>
                <div class="flex gap-1.5">
                    <button onclick="WarehousePage._exportPreviewCsv('${App.escapeHtml(name)}')" class="text-[10px] bg-white dark:bg-gray-600 border border-gray-200 dark:border-gray-600 px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-500"><i class="fas fa-file-csv mr-1"></i>CSV</button>
                    <button onclick="WarehousePage._exportPreviewPptx('${App.escapeHtml(name)}')" class="text-[10px] bg-white dark:bg-gray-600 border border-gray-200 dark:border-gray-600 px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-500"><i class="fas fa-file-powerpoint mr-1"></i>PPTX</button>
                </div>
            </div><div class="overflow-x-auto"><table class="data-table"><thead><tr>`;
            cols.forEach((c) => { html += `<th>${App.escapeHtml(c)}</th>`; });
            html += "</tr></thead><tbody>";
            rows.forEach((row) => {
                html += "<tr>";
                cols.forEach((c) => {
                    let val = row[c];
                    if (val === null || val === undefined) val = '<span class="text-gray-300">null</span>';
                    else if (typeof val === "number") val = Number.isInteger(val) ? val.toLocaleString() : val.toFixed(2);
                    else val = App.escapeHtml(String(val));
                    html += `<td class="text-xs">${val}</td>`;
                });
                html += "</tr>";
            });
            html += "</tbody></table></div>";
            el.innerHTML = html;
        } catch (e) {
            el.innerHTML = '<div class="p-4 text-sm text-red-400 text-center"><i class="fas fa-exclamation-circle mr-1"></i>Failed to load preview</div>';
        }
    },

    // ── Smart Data Profile ─────────────────────────────────────
    async _loadProfile(name) {
        const profileId = "profile-" + name.replace(/[^a-zA-Z0-9]/g, "_");
        const el = document.getElementById(profileId);
        if (!el) return;

        el.innerHTML = '<div class="p-4 text-sm text-gray-400 text-center"><i class="fas fa-spinner fa-spin"></i> Analyzing data profile...</div>';

        try {
            // Also load relationships (cache globally)
            if (!this._relationships) {
                try {
                    const relResult = await API.discoverRelationships();
                    if (relResult.success) this._relationships = relResult.relationships || [];
                } catch (_) {}
            }

            const [profile, quality, chartRecs] = await Promise.all([
                API.profile(name).catch(() => null),
                API.qualityScore(name).catch(() => null),
                API.chartRecommend(name).catch(() => null),
            ]);

            if (!profile || !profile.success) {
                el.innerHTML = '<div class="p-4 text-sm text-gray-400 text-center">Profile not available</div>';
                return;
            }

            // Quality score badge
            const score = quality?.score ?? quality?.quality_score ?? null;
            const scoreLabel = score != null
                ? `<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold ${score >= 80 ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : score >= 50 ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}"><i class="fas ${score >= 80 ? 'fa-check-circle' : score >= 50 ? 'fa-exclamation-triangle' : 'fa-times-circle'}"></i> ${Math.round(score)}/100</span>`
                : '';

            // Row/col summary
            const base = profile.profile || profile || {};
            const rowCount = base.row_count || 0;
            const colCount = base.column_count || base.columns?.length || 0;

            // Column stats table
            const colsArr = base.columns || profile.columns || profile.column_profiles || {};
            const columns = Array.isArray(colsArr)
                ? colsArr.reduce((m, c) => { const n = c.name || c.column_name; if (n) m[n] = c; return m; }, {})
                : colsArr;
            const colEntries = Object.entries(columns);

            // Build suggested actions
            const suggestions = [];
            colEntries.forEach(([col, info]) => {
                const nullPct = info.null_pct ?? info.null_percentage ?? 0;
                const outlierPct = info.outlier_pct ?? 0;
                if (nullPct > 10) {
                    suggestions.push({ icon: 'fa-broom', text: `Drop or fill nulls in "${col}" (${nullPct.toFixed(1)}% null)`, color: 'yellow' });
                }
                if (outlierPct > 2) {
                    suggestions.push({ icon: 'fa-filter', text: `Review ${info.outlier_count || '?'} outliers in "${col}" (${outlierPct.toFixed(1)}%)`, color: 'orange' });
                }
                if (info.type === 'text' && info.nunique && info.nunique <= 6) {
                    suggestions.push({ icon: 'fa-chart-pie', text: `Create pie chart for "${col}" (${info.nunique} categories)`, color: 'indigo' });
                }
                const sampleVal = info.sample_values?.[0] || info.top_values ? Object.keys(info.top_values || {})[0] : '';
                if (info.type === 'text' && sampleVal && /^\d{4}[-/]\d{2}/.test(String(sampleVal))) {
                    suggestions.push({ icon: 'fa-calendar', text: `Convert "${col}" to datetime (looks like dates)`, color: 'blue' });
                }
            });

            // PII warnings
            const pii = quality?.pii || quality?.columns_detected || [];
            const piiCount = Array.isArray(pii) ? pii.length : Object.keys(pii).length;

            // Relationships
            let relationshipsHtml = '';
            if (this._relationships) {
                const rels = this._relationships.filter(r => r.from_table === name || r.to_table === name);
                if (rels.length > 0) {
                    relationshipsHtml = '<div><h4 class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2"><i class="fas fa-link text-emerald-500 mr-1"></i>Related Tables</h4><div class="flex flex-wrap gap-2">';
                    rels.slice(0, 6).forEach(r => {
                        const otherTable = r.from_table === name ? r.to_table : r.from_table;
                        const otherCol = r.from_table === name ? r.to_column : r.from_column;
                        const thisCol = r.from_table === name ? r.from_column : r.to_column;
                        relationshipsHtml += `<span class="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 cursor-default hover:bg-gray-50 dark:hover:bg-gray-700">
                            <i class="fas fa-arrow-right text-emerald-500 text-[10px]"></i>
                            <strong>${App.escapeHtml(thisCol)}</strong> → <strong>${App.escapeHtml(otherTable)}.${App.escapeHtml(otherCol)}</strong>
                            <span class="text-gray-400">(${r.overlap}% match)</span>
                        </span>`;
                    });
                    relationshipsHtml += '</div></div>';
                }
            }

            // Chart recommendations
            const recs = chartRecs?.recommendations || [];

            // Build HTML
            let html = `<div class="p-4 space-y-4">`;

            // Header summary
            html += `<div class="flex flex-wrap items-center gap-3 text-sm">
                ${scoreLabel}
                <span class="text-gray-500 dark:text-gray-400">${rowCount.toLocaleString()} rows × ${colCount} columns</span>
                ${piiCount > 0 ? `<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"><i class="fas fa-user-shield"></i> ${piiCount} PII column${piiCount > 1 ? 's' : ''}</span>` : ''}
            </div>`;

            // Column table
            if (colEntries.length > 0) {
                html += `<div class="overflow-x-auto">
                    <table class="w-full text-xs border-collapse">
                    <thead><tr class="bg-gray-100 dark:bg-gray-700/50 text-left">
                        <th class="px-3 py-2 font-semibold">Column</th>
                        <th class="px-3 py-2 font-semibold">Type</th>
                        <th class="px-3 py-2 font-semibold">Null %</th>
                        <th class="px-3 py-2 font-semibold">Unique</th>
                        <th class="px-3 py-2 font-semibold">Top Value</th>
                        <th class="px-3 py-2 font-semibold">Outliers</th>
                    </tr></thead><tbody>`;

                colEntries.forEach(([col, info]) => {
                    const nullPct = (info.null_pct ?? info.null_percentage ?? 0).toFixed(1);
                    const nullClass = parseFloat(nullPct) > 10 ? 'text-red-500 font-semibold' : parseFloat(nullPct) > 5 ? 'text-yellow-500' : 'text-gray-500';
                    const type = info.type || info.dtype || '?';
                    const unique = info.nunique ?? info.unique_count ?? '—';
                    const topVal = info.top_values ? Object.keys(info.top_values)[0] : (info.mean != null ? `μ=${Number(info.mean).toFixed(2)}` : '—');
                    const outlierInfo = info.outlier_count != null && info.outlier_count > 0
                        ? `<span class="text-orange-500">${info.outlier_count} (${(info.outlier_pct ?? 0).toFixed(1)}%)</span>`
                        : '<span class="text-gray-300">—</span>';

                    html += `<tr class="border-t border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/20">
                        <td class="px-3 py-1.5 font-medium text-gray-700 dark:text-gray-300">${App.escapeHtml(col)}</td>
                        <td class="px-3 py-1.5"><span class="px-1.5 py-0.5 rounded text-[10px] font-medium ${type === 'numeric' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' : type === 'text' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'}">${App.escapeHtml(String(type))}</span></td>
                        <td class="px-3 py-1.5 ${nullClass}">${nullPct}%</td>
                        <td class="px-3 py-1.5 text-gray-500">${typeof unique === 'number' ? unique.toLocaleString() : unique}</td>
                        <td class="px-3 py-1.5 text-gray-500 max-w-[120px] truncate">${App.escapeHtml(String(topVal).slice(0, 30))}</td>
                        <td class="px-3 py-1.5">${outlierInfo}</td>
                    </tr>`;
                });

                html += `</tbody></table></div>`;
            }

            // Suggested Actions
            if (suggestions.length > 0) {
                html += `<div>
                    <h4 class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2"><i class="fas fa-wand-magic-sparkles text-indigo-500 mr-1"></i>Suggested Actions</h4>
                    <div class="flex flex-wrap gap-2">`;
                suggestions.forEach(s => {
                    const colors = {
                        yellow: 'bg-yellow-50 text-yellow-700 border-yellow-200 hover:bg-yellow-100 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800',
                        orange: 'bg-orange-50 text-orange-700 border-orange-200 hover:bg-orange-100 dark:bg-orange-900/20 dark:text-orange-400 dark:border-orange-800',
                        blue: 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100 dark:bg-blue-900/20 dark:text-blue-400 dark:border-blue-800',
                        indigo: 'bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100 dark:bg-indigo-900/20 dark:text-indigo-400 dark:border-indigo-800',
                    };
                    html += `<span class="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border cursor-default ${colors[s.color] || colors.blue}"><i class="fas ${s.icon} text-[10px]"></i>${App.escapeHtml(s.text)}</span>`;
                });
                html += `</div></div>`;
            }

            // Chart Recommendations
            if (recs.length > 0) {
                html += `<div>
                    <h4 class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2"><i class="fas fa-chart-bar text-primary-500 mr-1"></i>Chart Recommendations</h4>
                    <div class="flex flex-wrap gap-2">`;
                recs.forEach(r => {
                    const typeColors = { bar: 'blue', line: 'green', pie: 'orange', scatter: 'purple', histogram: 'teal', area: 'indigo', box: 'pink' };
                    const c = typeColors[r.chart_type] || 'gray';
                    html += `<span class="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 cursor-default hover:bg-gray-50 dark:hover:bg-gray-700">
                        <span class="font-semibold text-${c}-600 dark:text-${c}-400">${App.escapeHtml(r.chart_type)}</span>
                        <span class="text-gray-400">·</span>
                        <span class="text-gray-600 dark:text-gray-400">${App.escapeHtml(r.x_axis || '')}${r.y_axis ? ` × ${App.escapeHtml(r.y_axis)}` : ''}</span>
                    </span>`;
                });
                html += `</div></div>`;
            }

            html += `</div>`;
            el.innerHTML = html;
        } catch (e) {
            el.innerHTML = `<div class="p-4 text-sm text-red-400 text-center"><i class="fas fa-exclamation-circle mr-1"></i>Failed to load profile: ${App.escapeHtml(e.message || 'Unknown error')}</div>`;
        }
    },

    // ── Cleaning Panel ───────────────────────────────────────────
    _showCleanPanel(name) {
        const cleanId = "clean-" + name.replace(/[^a-zA-Z0-9]/g, "_");
        const el = document.getElementById(cleanId);
        if (!el) return;

        // Toggle visibility
        if (!el.classList.contains('hidden')) {
            el.classList.add('hidden');
            return;
        }

        el.classList.remove('hidden');
        el.innerHTML = `
            <div class="p-4 space-y-3">
                <div class="flex items-center justify-between">
                    <h4 class="text-sm font-semibold text-gray-700 dark:text-gray-300"><i class="fas fa-broom text-green-500 mr-1"></i>Clean "${App.escapeHtml(name)}"</h4>
                    <button onclick="document.getElementById('${cleanId}').classList.add('hidden')" class="text-xs text-gray-400 hover:text-red-500"><i class="fas fa-times"></i></button>
                </div>
                <div class="flex flex-wrap gap-3 text-xs">
                    <label class="flex items-center gap-1.5">
                        <input type="checkbox" id="clean-dedup-${cleanId}" checked class="rounded"> Remove duplicates
                    </label>
                    <label class="flex items-center gap-1.5">
                        Null strategy:
                        <select id="clean-null-${cleanId}" class="border rounded px-2 py-1 dark:bg-gray-700 dark:border-gray-600 text-xs">
                            <option value="drop">Drop rows</option>
                            <option value="fill_mean">Fill mean</option>
                            <option value="fill_median">Fill median</option>
                            <option value="fill_mode">Fill mode</option>
                        </select>
                    </label>
                </div>
                <button onclick="WarehousePage._previewClean('${App.escapeHtml(name)}')" id="clean-preview-btn-${cleanId}"
                    class="btn btn-sm btn-secondary">
                    <i class="fas fa-eye mr-1"></i>Preview Changes
                </button>
                <div id="clean-result-${cleanId}"></div>
            </div>`;
    },

    async _previewClean(name) {
        const cleanId = "clean-" + name.replace(/[^a-zA-Z0-9]/g, "_");
        const resultEl = document.getElementById("clean-result-" + cleanId);
        const btn = document.getElementById("clean-preview-btn-" + cleanId);
        if (!resultEl) return;

        const dedup = document.getElementById("clean-dedup-" + cleanId)?.checked ?? true;
        const nullStrategy = document.getElementById("clean-null-" + cleanId)?.value ?? "drop";

        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Analyzing...'; }
        resultEl.innerHTML = '<div class="text-xs text-gray-400"><i class="fas fa-spinner fa-spin"></i> Computing cleaning diff...</div>';

        try {
            const result = await API.cleanPreview(name, { dedup, null_strategy: nullStrategy });
            if (!result.success) {
                resultEl.innerHTML = `<div class="text-xs text-red-400">${App.escapeHtml(result.error || 'Failed')}</div>`;
                return;
            }

            const before = result.before;
            const after = result.after;
            const changes = result.changes || [];
            const rowsRemoved = result.rows_removed || 0;
            const rowsPct = result.rows_pct || 0;

            let html = '<div class="space-y-3">';

            // Before/After summary
            html += `<div class="grid grid-cols-3 gap-3">
                <div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                    <div class="text-lg font-bold">${before.rows.toLocaleString()}</div>
                    <div class="text-[10px] text-gray-500">Before rows</div>
                </div>
                <div class="p-2 rounded-lg ${rowsRemoved > 0 ? 'bg-yellow-50 dark:bg-yellow-900/20' : 'bg-gray-50 dark:bg-gray-700/30'} text-center">
                    <div class="text-lg font-bold ${rowsRemoved > 0 ? 'text-yellow-600' : ''}">${rowsRemoved > 0 ? '-' : ''}${rowsRemoved.toLocaleString()}</div>
                    <div class="text-[10px] text-gray-500">Rows ${rowsRemoved > 0 ? 'removed' : 'changed'} (${rowsPct}%)</div>
                </div>
                <div class="p-2 rounded-lg bg-green-50 dark:bg-green-900/20 text-center">
                    <div class="text-lg font-bold text-green-600">${after.rows.toLocaleString()}</div>
                    <div class="text-[10px] text-gray-500">After rows</div>
                </div>
            </div>`;

            // Changes detail
            if (changes.length > 0) {
                html += '<div class="space-y-1.5">';
                changes.forEach(c => {
                    html += `<div class="flex items-start gap-2 text-xs p-2 rounded-lg bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700">
                        <i class="fas fa-check-circle text-green-500 mt-0.5"></i>
                        <div><span class="font-medium">${App.escapeHtml(c.step)}</span><span class="text-gray-400 ml-1">${App.escapeHtml(c.detail)}</span></div>
                    </div>`;
                });
                html += '</div>';
            } else {
                html += '<div class="text-xs text-gray-400 text-center py-2">No changes needed — data is already clean</div>';
            }

            // Null summary comparison
            const beforeNulls = Object.entries(before.nulls || {});
            if (beforeNulls.length > 0) {
                html += '<div class="mt-2">';
                html += '<div class="text-[10px] font-semibold text-gray-500 uppercase mb-1">Null columns handled:</div>';
                html += '<div class="flex flex-wrap gap-1.5">';
                beforeNulls.forEach(([col, info]) => {
                    const afterNull = after.nulls?.[col];
                    const removed = afterNull ? info.count - afterNull.count : info.count;
                    html += `<span class="px-2 py-1 rounded text-[10px] ${removed > 0 ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-500'}">${App.escapeHtml(col)}: ${removed > 0 ? `${removed} handled` : `${info.count} nulls`}</span>`;
                });
                html += '</div></div>';
            }

            html += '</div>';
            resultEl.innerHTML = html;
        } catch (e) {
            resultEl.innerHTML = `<div class="text-xs text-red-400">${App.escapeHtml(e.message || 'Error')}</div>`;
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-eye mr-1"></i>Preview Changes'; }
        }
    },

    // ── Data Dictionary ──────────────────────────────────────────
    async _showDictionary(name) {
        const profileId = "profile-" + name.replace(/[^a-zA-Z0-9]/g, "_");
        const el = document.getElementById(profileId);
        if (!el) return;

        el.classList.remove('hidden');
        el.innerHTML = '<div class="p-4 text-sm text-gray-400 text-center"><i class="fas fa-spinner fa-spin"></i> Generating data dictionary...</div>';

        try {
            const result = await API.dataDictionary(name);
            if (!result.success) {
                el.innerHTML = `<div class="p-4 text-sm text-red-400"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(result.error || 'Failed')}</div>`;
                return;
            }

            const columns = result.columns || [];

            let html = `<div class="p-4 space-y-4">
                <div class="flex items-center justify-between">
                    <h4 class="text-sm font-semibold text-gray-700 dark:text-gray-300"><i class="fas fa-book text-purple-500 mr-1"></i>Data Dictionary: ${App.escapeHtml(name)}</h4>
                    <div class="flex gap-2">
                        <button onclick="WarehousePage._exportDictionary('${App.escapeHtml(name)}')" class="text-[10px] bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 px-2 py-1 rounded hover:bg-purple-100 dark:hover:bg-purple-800/30 text-purple-600 dark:text-purple-400"><i class="fas fa-download mr-1"></i>Export MD</button>
                        <button onclick="document.getElementById('${profileId}').classList.add('hidden')" class="text-xs text-gray-400 hover:text-red-500"><i class="fas fa-times"></i></button>
                    </div>
                </div>
                <p class="text-xs text-gray-400">${result.row_count.toLocaleString()} rows × ${result.column_count} columns</p>

                <div class="space-y-2">`;

            columns.forEach((col, i) => {
                const typeColors = {
                    numeric: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
                    text: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
                    datetime: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
                    other: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
                };
                const typeClass = typeColors[col.type] || typeColors.other;

                html += `<div class="border border-gray-100 dark:border-gray-700 rounded-lg p-3 hover:bg-gray-50 dark:hover:bg-gray-700/20 transition-colors">
                    <div class="flex items-start justify-between gap-3">
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-2 mb-1">
                                <span class="font-medium text-sm text-gray-800 dark:text-gray-200">${App.escapeHtml(col.name)}</span>
                                <span class="px-1.5 py-0.5 rounded text-[10px] font-medium ${typeClass}">${col.type}</span>
                                <span class="text-[10px] text-gray-400">${col.dtype}</span>
                            </div>
                            <div class="text-xs text-indigo-600 dark:text-indigo-400 italic mb-1.5">${App.escapeHtml(col.suggested_description || '')}</div>
                            <div class="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-gray-500">
                                <span>Non-null: ${col.non_null.toLocaleString()} (${(100 - col.null_pct).toFixed(1)}%)</span>
                                <span>Unique: ${col.unique_count.toLocaleString()}</span>
                                ${col.mean != null ? `<span>Mean: ${col.mean}</span>` : ''}
                                ${col.min != null ? `<span>Min: ${col.min}</span>` : ''}
                                ${col.max != null ? `<span>Max: ${col.max}</span>` : ''}
                                ${col.avg_length != null ? `<span>Avg len: ${col.avg_length}</span>` : ''}
                            </div>
                            ${col.top_values ? `<div class="text-[10px] text-gray-400 mt-1">Top: ${Object.entries(col.top_values).slice(0, 3).map(([k, v]) => `"${App.escapeHtml(String(k).slice(0, 20))}" (${v})`).join(', ')}</div>` : ''}
                        </div>
                        ${col.null_pct > 0 ? `<div class="text-[10px] ${col.null_pct > 10 ? 'text-red-500 font-semibold' : 'text-yellow-500'}">${col.null_pct}% null</div>` : ''}
                    </div>
                </div>`;
            });

            html += '</div></div>';
            el.innerHTML = html;

            // Store for export
            this._lastDictResult = result;
        } catch (e) {
            el.innerHTML = `<div class="p-4 text-sm text-red-400"><i class="fas fa-exclamation-circle mr-1"></i>Failed: ${App.escapeHtml(e.message)}</div>`;
        }
    },

    _exportDictionary(name) {
        const result = this._lastDictResult;
        if (!result) return App.toast('No dictionary data to export', 'error');

        let md = `# Data Dictionary: ${name}\n\n`;
        md += `**${result.row_count.toLocaleString()} rows** × **${result.column_count} columns**\n\n`;
        md += `## Columns\n\n`;
        md += `| Column | Type | Null % | Unique | Description |\n`;
        md += `|--------|------|--------|--------|-------------|\n`;

        (result.columns || []).forEach(col => {
            md += `| ${col.name} | ${col.type} | ${col.null_pct}% | ${col.unique_count} | ${col.suggested_description || ''} |\n`;
        });

        md += `\n## Detailed Statistics\n\n`;
        (result.columns || []).forEach(col => {
            md += `### ${col.name}\n`;
            md += `- **Type:** ${col.dtype} (${col.type})\n`;
            md += `- **Non-null:** ${col.non_null.toLocaleString()} / ${result.row_count.toLocaleString()}\n`;
            md += `- **Unique values:** ${col.unique_count.toLocaleString()}\n`;
            if (col.mean != null) md += `- **Mean:** ${col.mean}, **Median:** ${col.median}, **Std:** ${col.std}\n`;
            if (col.min != null) md += `- **Range:** ${col.min} to ${col.max}\n`;
            if (col.top_values) md += `- **Top values:** ${Object.entries(col.top_values).map(([k, v]) => `"${k}" (${v})`).join(', ')}\n`;
            md += `\n`;
        });

        const blob = new Blob([md], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = `${name}_dictionary.md`;
        a.href = url;
        a.click();
        URL.revokeObjectURL(url);
        App.success('Dictionary exported as Markdown');
    },

    // ── Preview Export ────────────────────────────────────────────
    _exportPreviewCsv(tableName) {
        App.info('Fetching table data for CSV export...');
        API.tableSample(tableName, 10000).then(result => {
            const data = result.data || result.rows || [];
            if (!Array.isArray(data) || data.length === 0) return App.toast('No data to export', 'error');
            const cols = Object.keys(data[0]);
            let csv = cols.map(c => `"${c.replace(/"/g, '""')}"`).join(',') + '\n';
            data.forEach(row => {
                csv += cols.map(c => `"${String(row[c] ?? '').replace(/"/g, '""')}"`).join(',') + '\n';
            });
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.download = `${tableName}.csv`;
            link.href = url;
            link.click();
            URL.revokeObjectURL(url);
            App.success(`${tableName} exported as CSV`);
        }).catch(e => App.error('Export failed: ' + e.message));
    },

    async _exportPreviewPptx(tableName) {
        try {
            App.showLoading();
            const resp = await fetch((window.__API_BASE__ || '') + `/export/pptx/${encodeURIComponent(tableName)}`);
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.download = `${tableName}_report.pptx`;
            link.href = url;
            link.click();
            URL.revokeObjectURL(url);
            App.hideLoading();
            App.success(`${tableName} exported as PPTX`);
        } catch (e) {
            App.hideLoading();
            App.error('PPTX export failed: ' + (e.message || 'Error'));
        }
    },

    // ── Delete single table ───────────────────────────────────────
    async deleteTable(name) {
        if (typeof Modal === "undefined" || !Modal.confirm) return;
        const confirmed = await Modal.confirm(
            "Delete Table",
            `Are you sure you want to delete <strong>${App.escapeHtml(name)}</strong>? This cannot be undone.`,
            { variant: "danger" }
        );
        if (!confirmed) return;
        try {
            await API.dropTable(name);
            if (typeof App !== "undefined") App.success("Table deleted: " + name);
            this._selected.delete(name);
            this._previewOpen.delete(name);
            await this._loadTables();
            this._updateSelectionUI();
        } catch (e) {
            if (typeof App !== "undefined") App.error("Failed to delete table");
        }
    },

    // ── Delete selected ───────────────────────────────────────────
    async deleteSelected() {
        const names = [...this._selected];
        if (names.length === 0) return;
        if (typeof Modal === "undefined" || !Modal.confirm) return;
        const confirmed = await Modal.confirm(
            "Delete Tables",
            `Are you sure you want to delete <strong>${names.length}</strong> table(s)? This cannot be undone.`,
            { variant: "danger" }
        );
        if (!confirmed) return;
        let deleted = 0;
        for (const name of names) {
            try {
                await API.dropTable(name);
                deleted++;
            } catch (_) {
                // Continue with other deletes
            }
        }
        if (typeof App !== "undefined") App.success(`Deleted ${deleted} of ${names.length} tables`);
        this._selected.clear();
        this._previewOpen.clear();
        await this._loadTables();
        this._updateSelectionUI();
    },

    // ── Clear all ─────────────────────────────────────────────────
    async clearAll() {
        if (this._tables.length === 0) {
            if (typeof App !== "undefined") App.toast("No tables to clear", "info");
            return;
        }
        if (typeof Modal === "undefined" || !Modal.confirm) return;
        const confirmed = await Modal.confirm(
            "Clear All Tables",
            `Are you sure you want to delete ALL <strong>${this._tables.length}</strong> tables? This cannot be undone. Platform-owned tables (billing, dashboards, notifications, chat, org, catalog) are preserved.`,
            { variant: "danger", confirmLabel: "Delete All" }
        );
        if (!confirmed) return;
        try {
            await API.dropAllTables();
            if (typeof App !== "undefined") App.success("All tables cleared");
            this._selected.clear();
            this._previewOpen.clear();
            await this._loadTables();
            this._updateSelectionUI();
        } catch (e) {
            if (typeof App !== "undefined") App.error("Failed to clear tables");
        }
    },

    // ── Quick actions ─────────────────────────────────────────────
    sendToAnalysis() {
        const names = [...this._selected];
        if (names.length === 0) return App.toast("Select tables first", "error");
        App.state.selectedTable = names[0];
        App.navigate("#analysis");
    },

    sendToSQL() {
        const names = [...this._selected];
        if (names.length === 0) return App.toast("Select tables first", "error");
        App.state.selectedTable = names[0];
        App.navigate("#sql");
    },

    sendSingleToAnalysis(name) {
        App.state.selectedTable = name;
        App.navigate("#analysis");
    },

    sendSingleToSQL(name) {
        App.state.selectedTable = name;
        App.navigate("#sql");
    },

    // ── Refresh ───────────────────────────────────────────────────
    async refresh() {
        await this._loadTables();
        await App.refreshTables();
        App.success("Tables refreshed");
    },

    // ── Quick upload & load ───────────────────────────────────────
    async uploadQuick() {
        const input = document.getElementById("wh-file-input");
        const nameInput = document.getElementById("wh-table-name");
        const statusEl = document.getElementById("wh-upload-status");

        const file = input?.files?.[0];
        if (!file) return App.toast("Select a file first", "error");

        const tableName = nameInput?.value?.trim() || file.name.replace(/\.[^.]+$/, "").replace(/[^a-zA-Z0-9_]/g, "_");

        if (statusEl) statusEl.innerHTML = '<span class="text-gray-500"><i class="fas fa-spinner fa-spin mr-1"></i>Uploading and loading...</span>';

        try {
            const uploadResult = await API.upload(file);
            const filename = uploadResult.filename || file.name;
            const sheet = uploadResult.sheet || "";
            await API.load(filename, sheet, tableName);

            App.success("Loaded: " + tableName);
            if (statusEl) statusEl.innerHTML = `<span class="text-green-600 dark:text-green-400"><i class="fas fa-check-circle mr-1"></i>Loaded as <strong>${App.escapeHtml(tableName)}</strong></span>`;

            // Reset inputs
            if (input) input.value = "";
            if (nameInput) nameInput.value = "";

            await this._loadTables();
            await App.refreshTables();
        } catch (e) {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600 dark:text-red-400"><i class="fas fa-times-circle mr-1"></i>${App.escapeHtml(e.message || "Upload failed")}</span>`;
        }
    },
};

window.WarehousePage = WarehousePage;
(window._PAGES = window._PAGES || {})["#warehouse"] = WarehousePage;
