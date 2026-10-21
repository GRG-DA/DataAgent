/* ═══════════════════════════════════════════
   Compare Page — Period-over-Period & Table Diff
   ═══════════════════════════════════════════ */

const ComparePage = {
    _result: null,

    async init() {},
    async destroy() { this._result = null; },

    async render() {
        await App.refreshTables();
        const pc = document.getElementById('page-content');
        const tables = App.state.tables || {};
        const tableNames = Array.isArray(tables) ? tables :
            (typeof tables === 'object' && tables !== null) ? Object.keys(tables) : [];

        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
            <div>
                <div class="page-title">
                    <div class="page-title-icon"><i class="fas fa-code-compare"></i></div>
                    <div>
                        <h2>Data Comparison</h2>
                        <p class="page-title-subtitle">Compare periods, segments, or tables side-by-side with statistical significance</p>
                    </div>
                </div>
            </div>

            <!-- Tab Bar -->
            <div class="flex gap-1 border-b dark:border-gray-700">
                <button onclick="ComparePage._showTab('periods')" id="tab-periods" class="px-4 py-2.5 text-sm font-medium border-b-2 border-primary-500 text-primary-600 dark:text-primary-400">
                    <i class="fas fa-calendar-alt mr-1"></i>Period Comparison
                </button>
                <button onclick="ComparePage._showTab('diff')" id="tab-diff" class="px-4 py-2.5 text-sm font-medium border-b-2 border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                    <i class="fas fa-code-branch mr-1"></i>Table Diff
                </button>
                <button onclick="ComparePage._showTab('universal')" id="tab-universal" class="px-4 py-2.5 text-sm font-medium border-b-2 border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                    <i class="fas fa-arrows-left-right mr-1"></i>Quick Compare
                </button>
            </div>

            <!-- Period Comparison Tab -->
            <div id="period-tab" class="space-y-4">
                <div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Compare Periods</h3>
                    <div class="grid grid-cols-1 md:grid-cols-5 gap-3">
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">Table</label>
                            <select id="cmp-table" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                                ${tableNames.map(t => `<option value="${t}" ${t === App.state.selectedTable ? 'selected' : ''}>${t}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">Date Column</label>
                            <input id="cmp-date" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="Auto-detect">
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">Metric Column</label>
                            <input id="cmp-metric" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="Auto-detect">
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">Group By (optional)</label>
                            <input id="cmp-group" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="e.g., Region">
                        </div>
                        <div class="flex items-end">
                            <button onclick="ComparePage._runPeriodComparison()" class="w-full bg-primary-500 hover:bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                                <i class="fas fa-play mr-1"></i>Compare
                            </button>
                        </div>
                    </div>
                </div>
                <div id="cmp-period-results"></div>
            </div>

            <!-- Table Diff Tab -->
            <div id="diff-tab" class="space-y-4 hidden">
                <div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Diff Two Tables</h3>
                    <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">Before Table</label>
                            <select id="diff-before" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                                ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">After Table</label>
                            <select id="diff-after" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                                ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">Key Column</label>
                            <input id="diff-key" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="Primary key">
                        </div>
                        <div class="flex items-end">
                            <button onclick="ComparePage._runDiff()" class="w-full bg-primary-500 hover:bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                                <i class="fas fa-play mr-1"></i>Diff
                            </button>
                        </div>
                    </div>
                </div>
                <div id="cmp-diff-results"></div>
            </div>

            <!-- Universal Compare Tab -->
            <div id="universal-tab" class="space-y-4 hidden">
                <div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Quick Compare Any Two Tables</h3>
                    <p class="text-xs text-gray-400 mb-3">Select two tables to see a side-by-side comparison with statistical significance</p>
                    <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">Before Table</label>
                            <select id="univ-before" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                                <option value="">Select table...</option>
                                ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-500 mb-1">After Table</label>
                            <select id="univ-after" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                                <option value="">Select table...</option>
                                ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                            </select>
                        </div>
                        <div class="flex items-end">
                            <button onclick="ComparePage._runQuickCompare()" id="univ-compare-btn" class="w-full bg-primary-500 hover:bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                                <i class="fas fa-play mr-1"></i>Auto-Compare
                            </button>
                        </div>
                        <div class="flex items-end">
                            <button onclick="ComparePage._swapTables()" class="w-full bg-gray-100 dark:bg-gray-700 border border-gray-300 dark:border-gray-600 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors">
                                <i class="fas fa-right-left mr-1"></i>Swap
                            </button>
                        </div>
                    </div>
                </div>
                <div id="univ-results"></div>
            </div>
        </div>`;
    },

    _showTab(tab) {
        document.getElementById('period-tab').classList.toggle('hidden', tab !== 'periods');
        document.getElementById('diff-tab').classList.toggle('hidden', tab !== 'diff');
        document.getElementById('universal-tab').classList.toggle('hidden', tab !== 'universal');
        ['periods', 'diff', 'universal'].forEach(t => {
            const el = document.getElementById('tab-' + t);
            if (!el) return;
            if (tab === t) { el.className = el.className.replace('border-transparent', 'border-primary-500').replace('text-gray-500', 'text-primary-600'); }
            else { el.className = el.className.replace('border-primary-500', 'border-transparent').replace('text-primary-600', 'text-gray-500'); }
        });
    },

    _swapTables() {
        const before = document.getElementById('univ-before');
        const after = document.getElementById('univ-after');
        if (before && after) {
            const tmp = before.value;
            before.value = after.value;
            after.value = tmp;
        }
    },

    async _runQuickCompare() {
        const tableBefore = document.getElementById('univ-before').value;
        const tableAfter = document.getElementById('univ-after').value;
        if (!tableBefore || !tableAfter) return App.toast('Select both tables to compare', 'error');
        if (tableBefore === tableAfter) return App.toast('Select two different tables', 'error');

        const el = document.getElementById('univ-results');
        const btn = document.getElementById('univ-compare-btn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Comparing...'; }
        el.innerHTML = '<div class="card flex items-center justify-center py-12"><i class="fas fa-spinner fa-spin text-2xl text-primary-500"></i><span class="ml-3 text-sm text-gray-400">Analyzing both tables...</span></div>';

        try {
            // Load profiles for both tables in parallel
            const [profileBefore, profileAfter, diffResult] = await Promise.all([
                API.profile(tableBefore).catch(() => null),
                API.profile(tableAfter).catch(() => null),
                API.compareDiff(tableBefore, tableAfter).catch(() => null),
            ]);

            const profileBeforeData = profileBefore?.profile || profileBefore || {};
            const profileAfterData = profileAfter?.profile || profileAfter || {};
            const rowsBefore = profileBeforeData?.row_count || 0;
            const rowsAfter = profileAfterData?.row_count || 0;
            const rowDelta = rowsAfter - rowsBefore;
            const rowPct = rowsBefore > 0 ? ((rowDelta / rowsBefore) * 100).toFixed(1) : '0';

            // Profile columns come back as an array of {name, dtype, type, null_pct, mean, ...}
            const colBefore = {};
            const colAfter = {};
            (profileBeforeData?.columns || []).forEach(c => { if (c && c.name) colBefore[c.name] = c; });
            (profileAfterData?.columns || []).forEach(c => { if (c && c.name) colAfter[c.name] = c; });

            // Find common columns and compute deltas
            const allCols = new Set([...Object.keys(colBefore), ...Object.keys(colAfter)]);
            const colDiffs = [];

            allCols.forEach(col => {
                const a = colBefore[col];
                const b = colAfter[col];
                if (!a && !b) return;

                const typeBefore = a?.type || '?';
                const typeAfter = b?.type || '?';
                const nullBefore = a?.null_pct ?? a?.null_percentage ?? 0;
                const nullAfter = b?.null_pct ?? b?.null_percentage ?? 0;
                const meanBefore = a?.mean;
                const meanAfter = b?.mean;
                const uniqueBefore = a?.nunique ?? a?.unique_count;
                const uniqueAfter = b?.nunique ?? b?.unique_count;

                const changes = [];
                if (typeBefore !== typeAfter) changes.push(`type: ${typeBefore} → ${typeAfter}`);
                if (Math.abs(nullAfter - nullBefore) > 0.1) changes.push(`null: ${nullBefore.toFixed(1)}% → ${nullAfter.toFixed(1)}%`);
                if (meanBefore != null && meanAfter != null) {
                    const meanDelta = ((meanAfter - meanBefore) / Math.abs(meanBefore) * 100);
                    if (Math.abs(meanDelta) > 0.5) changes.push(`mean: ${meanBefore.toFixed(2)} → ${meanAfter.toFixed(2)} (${meanDelta > 0 ? '+' : ''}${meanDelta.toFixed(1)}%)`);
                }
                if (uniqueBefore != null && uniqueAfter != null && uniqueBefore !== uniqueAfter) {
                    changes.push(`unique: ${uniqueBefore} → ${uniqueAfter}`);
                }

                if (changes.length > 0 || (!a && b) || (a && !b)) {
                    colDiffs.push({ col, typeBefore, typeAfter, changes, inBefore: !!a, inAfter: !!b });
                }
            });

            // Build HTML
            let html = '';

            // Summary cards
            const rowColor = rowDelta > 0 ? 'green' : rowDelta < 0 ? 'red' : 'gray';
            html += `<div class="card">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-chart-line mr-1"></i>Summary</h3>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold">${rowsBefore.toLocaleString()}</div>
                        <div class="text-[10px] text-gray-500">${App.escapeHtml(tableBefore)} rows</div>
                    </div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold">${rowsAfter.toLocaleString()}</div>
                        <div class="text-[10px] text-gray-500">${App.escapeHtml(tableAfter)} rows</div>
                    </div>
                    <div class="p-3 rounded-lg bg-${rowColor}-50 dark:bg-${rowColor}-900/20 text-center">
                        <div class="text-lg font-bold text-${rowColor}-600 dark:text-${rowColor}-400">${rowDelta > 0 ? '+' : ''}${rowDelta.toLocaleString()}</div>
                        <div class="text-[10px] text-gray-500">Row change (${rowDelta > 0 ? '+' : ''}${rowPct}%)</div>
                    </div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold">${colDiffs.length}</div>
                        <div class="text-[10px] text-gray-500">Columns changed</div>
                    </div>
                </div>`;

            // Added/removed tables summary
            const addedCols = colDiffs.filter(c => !c.inBefore && c.inAfter);
            const removedCols = colDiffs.filter(c => c.inBefore && !c.inAfter);
            if (addedCols.length > 0 || removedCols.length > 0) {
                html += '<div class="mt-3 flex flex-wrap gap-2">';
                addedCols.forEach(c => { html += `<span class="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"><i class="fas fa-plus-circle"></i>${App.escapeHtml(c.col)} added</span>`; });
                removedCols.forEach(c => { html += `<span class="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-full bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"><i class="fas fa-minus-circle"></i>${App.escapeHtml(c.col)} removed</span>`; });
                html += '</div>';
            }
            html += '</div>';

            // Column-level diffs
            const changedCols = colDiffs.filter(c => c.changes.length > 0);
            if (changedCols.length > 0) {
                html += `<div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-columns mr-1"></i>Column Changes</h3>
                    <div class="overflow-x-auto">
                        <table class="w-full text-xs border-collapse">
                        <thead><tr class="bg-gray-100 dark:bg-gray-700/50">
                            <th class="px-3 py-2 text-left font-semibold">Column</th>
                            <th class="px-3 py-2 text-left font-semibold">Before</th>
                            <th class="px-3 py-2 text-left font-semibold">After</th>
                            <th class="px-3 py-2 text-left font-semibold">Changes</th>
                        </tr></thead><tbody>`;
                changedCols.forEach(c => {
                    html += `<tr class="border-t border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/20">
                        <td class="px-3 py-2 font-medium">${App.escapeHtml(c.col)}</td>
                        <td class="px-3 py-2 text-gray-500">${App.escapeHtml(c.typeBefore)}</td>
                        <td class="px-3 py-2 text-gray-500">${App.escapeHtml(c.typeAfter)}</td>
                        <td class="px-3 py-2">${c.changes.map(ch => `<span class="block text-gray-600 dark:text-gray-400">${App.escapeHtml(ch)}</span>`).join('')}</td>
                    </tr>`;
                });
                html += '</tbody></table></div></div>';
            }

            // Diff details (if available)
            if (diffResult && diffResult.success && diffResult.diff) {
                const diff = diffResult.diff;
                html += `<div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-code-compare mr-1"></i>Row-Level Diff</h3>
                    <div class="grid grid-cols-3 gap-3 mb-3">
                        <div class="p-2 rounded-lg bg-green-50 dark:bg-green-900/20 text-center">
                            <div class="text-lg font-bold text-green-600">${diff.rows_added || 0}</div>
                            <div class="text-[10px] text-gray-500">Added</div>
                        </div>
                        <div class="p-2 rounded-lg bg-red-50 dark:bg-red-900/20 text-center">
                            <div class="text-lg font-bold text-red-600">${diff.rows_removed || 0}</div>
                            <div class="text-[10px] text-gray-500">Removed</div>
                        </div>
                        <div class="p-2 rounded-lg bg-yellow-50 dark:bg-yellow-900/20 text-center">
                            <div class="text-lg font-bold text-yellow-600">${diff.rows_modified || 0}</div>
                            <div class="text-[10px] text-gray-500">Modified</div>
                        </div>
                    </div>`;

                // Show first few modified rows
                if (diff.modified_rows && diff.modified_rows.length > 0) {
                    html += `<div class="overflow-x-auto max-h-[200px]">
                        <table class="w-full text-[10px] border-collapse">
                        <thead><tr class="bg-gray-100 dark:bg-gray-700/50"><th class="px-2 py-1.5 text-left font-semibold">Column</th><th class="px-2 py-1.5 text-left font-semibold">Before</th><th class="px-2 py-1.5 text-left font-semibold">After</th></tr></thead><tbody>`;
                    const sampleMods = diff.modified_rows.slice(0, 10);
                    sampleMods.forEach(row => {
                        const diffs = row.differences || row.changes || {};
                        Object.entries(diffs).forEach(([col, vals]) => {
                            const before = Array.isArray(vals) ? vals[0] : vals.before;
                            const after = Array.isArray(vals) ? vals[1] : vals.after;
                            html += `<tr class="border-t border-gray-100 dark:border-gray-700/50">
                                <td class="px-2 py-1 font-medium">${App.escapeHtml(col)}</td>
                                <td class="px-2 py-1 text-red-500">${App.escapeHtml(String(before ?? 'null'))}</td>
                                <td class="px-2 py-1 text-green-500">${App.escapeHtml(String(after ?? 'null'))}</td>
                            </tr>`;
                        });
                    });
                    html += '</tbody></table></div>';
                }
                html += '</div>';
            }

            // No changes message
            if (colDiffs.length === 0 && rowDelta === 0) {
                html += `<div class="card text-center py-8">
                    <i class="fas fa-check-circle text-green-500 text-3xl mb-2"></i>
                    <p class="text-sm font-medium text-green-600">No differences detected</p>
                    <p class="text-xs text-gray-400 mt-1">Both tables have the same structure and row count</p>
                </div>`;
            }

            el.innerHTML = html;
        } catch (e) {
            el.innerHTML = `<div class="card"><div class="text-red-500 text-sm"><i class="fas fa-exclamation-circle mr-1"></i>Compare failed: ${App.escapeHtml(e.message || 'Unknown error')}</div></div>`;
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-play mr-1"></i>Auto-Compare'; }
        }
    },

    async _runPeriodComparison() {
        const table = document.getElementById('cmp-table').value;
        if (!table) return App.toast('Select a table', 'error');
        const el = document.getElementById('cmp-period-results');
        el.innerHTML = '<div class="card flex items-center justify-center py-12"><i class="fas fa-spinner fa-spin text-2xl text-primary-500"></i></div>';

        try {
            const result = await API.comparePeriods(
                table,
                document.getElementById('cmp-date').value,
                document.getElementById('cmp-metric').value,
                document.getElementById('cmp-group').value,
            );

            if (!result.success) {
                el.innerHTML = `<div class="card"><div class="text-red-500 text-sm">${App.escapeHtml(result.error || 'Failed')}</div></div>`;
                return;
            }

            const cmp = result.comparison;
            el.innerHTML = `
            <div class="card">
                <div class="flex items-center gap-3 mb-4">
                    <span class="text-sm font-semibold">${cmp.comparison_type}</span>
                    <span class="text-xs px-2 py-0.5 rounded-full ${cmp.overall_significance ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-500'}">
                        ${cmp.highlight_count} / ${cmp.total_compared} significant
                    </span>
                </div>
                <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">${App.escapeHtml(cmp.narrative)}</p>
                <div class="overflow-x-auto">
                    <table class="w-full text-xs border-collapse">
                        <thead>
                            <tr class="bg-gray-100 dark:bg-gray-700/50">
                                <th class="px-3 py-2 text-left font-semibold">Period</th>
                                <th class="px-3 py-2 text-left font-semibold">Group</th>
                                <th class="px-3 py-2 text-right font-semibold">Previous</th>
                                <th class="px-3 py-2 text-right font-semibold">Current</th>
                                <th class="px-3 py-2 text-right font-semibold">Change</th>
                                <th class="px-3 py-2 text-center font-semibold">Sig.</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${cmp.metrics.map(m => {
                                const changeColor = m.pct_change > 0 ? 'text-green-600' : m.pct_change < 0 ? 'text-red-600' : 'text-gray-400';
                                const icon = m.pct_change > 0 ? 'fa-arrow-up' : m.pct_change < 0 ? 'fa-arrow-down' : 'fa-minus';
                                return `<tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30 ${m.significant ? 'bg-green-50/50 dark:bg-green-900/10' : ''}">
                                    <td class="px-3 py-2 font-medium">${App.escapeHtml(m.period_prev)} → ${App.escapeHtml(m.period_curr)}</td>
                                    <td class="px-3 py-2">${m.group ? App.escapeHtml(m.group) : '—'}</td>
                                    <td class="px-3 py-2 text-right font-mono">${m.mean_prev.toLocaleString(undefined, {maximumFractionDigits:2})}</td>
                                    <td class="px-3 py-2 text-right font-mono">${m.mean_curr.toLocaleString(undefined, {maximumFractionDigits:2})}</td>
                                    <td class="px-3 py-2 text-right ${changeColor} font-mono">
                                        <i class="fas ${icon} mr-1 text-[10px]"></i>${m.pct_change > 0 ? '+' : ''}${m.pct_change.toFixed(1)}%
                                    </td>
                                    <td class="px-3 py-2 text-center">
                                        ${m.significant
                                            ? '<span class="inline-flex items-center justify-center w-5 h-5 bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400 rounded-full text-[10px]"><i class="fas fa-check"></i></span>'
                                            : '<span class="text-gray-300">—</span>'}
                                    </td>
                                </tr>`;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            </div>`;
        } catch (e) {
            el.innerHTML = `<div class="card"><div class="text-red-500 text-sm">${App.escapeHtml(e.message)}</div></div>`;
        }
    },

    async _runDiff() {
        const tableBefore = document.getElementById('diff-before').value;
        const tableAfter = document.getElementById('diff-after').value;
        if (!tableBefore || !tableAfter) return App.toast('Select both tables', 'error');
        const el = document.getElementById('cmp-diff-results');
        el.innerHTML = '<div class="card flex items-center justify-center py-12"><i class="fas fa-spinner fa-spin text-2xl text-primary-500"></i></div>';

        try {
            const result = await API.compareDiff(tableBefore, tableAfter, document.getElementById('diff-key').value);
            if (!result.success) {
                el.innerHTML = `<div class="card"><div class="text-red-500 text-sm">${App.escapeHtml(result.error || 'Failed')}</div></div>`;
                return;
            }
            const diff = result.diff;
            el.innerHTML = `
            <div class="card space-y-4">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div class="p-3 bg-green-50 dark:bg-green-900/20 rounded-lg text-center">
                        <div class="text-2xl font-bold text-green-600">${diff.rows_added}</div>
                        <div class="text-xs text-gray-500">Added</div>
                    </div>
                    <div class="p-3 bg-red-50 dark:bg-red-900/20 rounded-lg text-center">
                        <div class="text-2xl font-bold text-red-600">${diff.rows_removed}</div>
                        <div class="text-xs text-gray-500">Removed</div>
                    </div>
                    <div class="p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg text-center">
                        <div class="text-2xl font-bold text-yellow-600">${diff.rows_modified}</div>
                        <div class="text-xs text-gray-500">Modified cells</div>
                    </div>
                    <div class="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-center">
                        <div class="text-2xl font-bold text-blue-600">${diff.rows_unchanged}</div>
                        <div class="text-xs text-gray-500">Unchanged</div>
                    </div>
                </div>
                <p class="text-sm text-gray-600 dark:text-gray-300">${App.escapeHtml(diff.summary)}</p>
                ${Object.keys(diff.modified_cells || {}).length > 0 ? `
                <div>
                    <h4 class="text-sm font-semibold mb-2">Modified Cells</h4>
                    <div class="overflow-x-auto">
                        <table class="w-full text-xs border-collapse">
                            <thead>
                                <tr class="bg-gray-100 dark:bg-gray-700/50">
                                    <th class="px-3 py-2 text-left font-semibold">Column</th>
                                    <th class="px-3 py-2 text-left font-semibold">Key</th>
                                    <th class="px-3 py-2 text-left font-semibold">Before</th>
                                    <th class="px-3 py-2 text-left font-semibold">After</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${Object.entries(diff.modified_cells).flatMap(([col, changes]) =>
                                    changes.slice(0, 50).map(c => `
                                        <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30">
                                            <td class="px-3 py-2 font-medium">${App.escapeHtml(col)}</td>
                                            <td class="px-3 py-2">${App.escapeHtml(String(c.key))}</td>
                                            <td class="px-3 py-2 bg-red-50/50 dark:bg-red-900/10">${App.escapeHtml(String(c.before ?? 'NULL'))}</td>
                                            <td class="px-3 py-2 bg-green-50/50 dark:bg-green-900/10">${App.escapeHtml(String(c.after ?? 'NULL'))}</td>
                                        </tr>
                                    `).join('')
                                ).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>` : ''}
            </div>`;
        } catch (e) {
            el.innerHTML = `<div class="card"><div class="text-red-500 text-sm">${App.escapeHtml(e.message)}</div></div>`;
        }
    },
};

window.ComparePage = ComparePage;
(window._PAGES = window._PAGES || {})["#compare"] = ComparePage;
