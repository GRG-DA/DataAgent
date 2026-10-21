/* ═══════════════════════════════════════════
   Statistical Tests — t-test, ANOVA, Chi-Square
   ═══════════════════════════════════════════ */

const StatsPage = {
    async init() {},
    async destroy() {},

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
                    <div class="page-title-icon"><i class="fas fa-calculator"></i></div>
                    <div>
                        <h2>Statistical Tests</h2>
                        <p class="page-title-subtitle">Run hypothesis tests, compare groups, and measure significance</p>
                    </div>
                </div>
            </div>

            <!-- Test Selection -->
            <div class="card">
                <div class="grid grid-cols-1 md:grid-cols-5 gap-3">
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Test Type</label>
                        <select id="st-test-type" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" onchange="StatsPage._updateUI()">
                            <option value="ttest">T-Test (two groups)</option>
                            <option value="anova">ANOVA (3+ groups)</option>
                            <option value="chisquare">Chi-Square (categorical)</option>
                            <option value="normality">Normality Test</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Table</label>
                        <select id="st-table" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" onchange="StatsPage._updateUI()">
                            <option value="">Select...</option>
                            ${tableNames.map(t => `<option value="${t}" ${t === App.state.selectedTable ? 'selected' : ''}>${t}</option>`).join('')}
                        </select>
                    </div>
                    <div>
                        <label id="st-numeric-label" class="block text-xs font-medium text-gray-500 mb-1">Numeric Column</label>
                        <select id="st-numeric" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600"></select>
                    </div>
                    <div>
                        <label id="st-group-label" class="block text-xs font-medium text-gray-500 mb-1">Group/Category Column</label>
                        <select id="st-group" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600"></select>
                    </div>
                    <div class="flex items-end">
                        <button onclick="StatsPage._runTest()" id="st-run-btn" class="w-full bg-purple-500 hover:bg-purple-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                            <i class="fas fa-play mr-1"></i>Run Test
                        </button>
                    </div>
                </div>
                <div id="st-description" class="mt-3 text-xs text-gray-400">Compare means between two groups using the independent t-test.</div>
            </div>

            <!-- Results -->
            <div id="st-results" class="space-y-4">
                <div class="card text-center py-12 text-gray-400">
                    <i class="fas fa-flask text-4xl mb-3 opacity-30"></i>
                    <p>Select a test and configure parameters above</p>
                </div>
            </div>
        </div>`;
        this._updateUI();
    },

    _updateUI() {
        const testType = document.getElementById('st-test-type')?.value;
        const desc = document.getElementById('st-description');
        const descriptions = {
            ttest: 'Compare means between two groups (e.g., treatment vs control). Requires one numeric column and one categorical column with exactly 2 groups.',
            anova: 'Compare means across three or more groups. Requires one numeric column and one categorical column.',
            chisquare: 'Test independence between two categorical variables. Requires two categorical columns. H0: variables are independent.',
            normality: 'Test if a numeric column follows a normal distribution using Shapiro-Wilk test.',
        };
        if (desc) desc.textContent = descriptions[testType] || '';
        const numLabel = document.getElementById('st-numeric-label');
        const grpLabel = document.getElementById('st-group-label');
        if (numLabel) numLabel.textContent = testType === 'chisquare' ? 'Category Column' : 'Numeric Column';
        if (grpLabel) grpLabel.textContent = testType === 'normality' ? 'Column to Test' : 'Group/Category Column';
        this._loadColumns();
    },

    async _loadColumns() {
        const table = document.getElementById('st-table')?.value;
        if (!table) return;

        try {
            const profile = await API.profile(table);
            const prof = profile?.profile || profile || {};
            const colsArr = prof.columns || profile?.columns || [];
            const columns = Array.isArray(colsArr)
                ? colsArr.reduce(function (m, c) { var n = c.name || c.column_name; if (n) m[n] = c; return m; }, {})
                : colsArr;
            const numeric = Object.keys(columns).filter(c => columns[c]?.type === 'numeric');
            const text = Object.keys(columns).filter(c => columns[c]?.type === 'text' || columns[c]?.type === 'categorical' || columns[c]?.type === 'string');

            const numSelect = document.getElementById('st-numeric');
            const catSelect = document.getElementById('st-group');
            const testType = document.getElementById('st-test-type')?.value;

            if (numSelect) {
                const numericCols = testType === 'chisquare' ? text : numeric;
                numSelect.innerHTML = '<option value="">Select ' + (testType === 'chisquare' ? 'category' : 'numeric') + ' column...</option>' +
                    numericCols.map(c => `<option value="${c}">${c}</option>`).join('');
            }
            if (catSelect) {
                catSelect.innerHTML = '<option value="">Select group column...</option>' +
                    text.map(c => `<option value="${c}">${c}</option>`).join('');
                // For normality test, also add numeric columns as options for group
                if (testType === 'normality') {
                    catSelect.innerHTML = '<option value="">Select column to test...</option>' +
                        Object.keys(columns).map(c => `<option value="${c}">${c}</option>`).join('');
                }
            }
        } catch (_) {}
    },

    async _runTest() {
        const testType = document.getElementById('st-test-type')?.value;
        const table = document.getElementById('st-table')?.value;
        const numeric = document.getElementById('st-numeric')?.value;
        const group = document.getElementById('st-group')?.value;

        if (!table) return App.toast('Select a table', 'error');
        if (testType !== 'normality' && !numeric) return App.toast(testType === 'chisquare' ? 'Select a category column' : 'Select a numeric column', 'error');
        if ((testType === 'ttest' || testType === 'anova' || testType === 'chisquare') && !group) return App.toast('Select a group column', 'error');

        const el = document.getElementById('st-results');
        const btn = document.getElementById('st-run-btn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Running...'; }
        el.innerHTML = '<div class="card flex items-center justify-center py-12"><i class="fas fa-spinner fa-spin text-2xl text-purple-500"></i></div>';

        try {
            let html = '<div class="card">';

            if (testType === 'normality') {
                const col = numeric || group;
                const res = await API.normality(table, col, 'shapiro');
                const n = res?.normality || res || {};
                const pVal = typeof n.p_value === 'number' ? n.p_value : 1;
                const isNormal = typeof n.is_normal === 'boolean' ? n.is_normal : pVal > 0.05;

                html += `<h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Results — Normality (${App.escapeHtml(n.test_name || 'Shapiro-Wilk')})</h3>`;
                html += `<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center"><div class="text-lg font-bold">${n.sample_size ?? '-'}</div><div class="text-[10px] text-gray-500">Samples</div></div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center"><div class="text-lg font-bold">${typeof n.statistic === 'number' ? n.statistic.toFixed(4) : '-'}</div><div class="text-[10px] text-gray-500">W Statistic</div></div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center"><div class="text-lg font-bold">${pVal.toFixed(4)}</div><div class="text-[10px] text-gray-500">p-value</div></div>
                    <div class="p-3 rounded-lg ${isNormal ? 'bg-green-50 dark:bg-green-900/20' : 'bg-amber-50 dark:bg-amber-900/20'} text-center">
                        <div class="text-lg font-bold ${isNormal ? 'text-green-600' : 'text-amber-600'}">${isNormal ? '✓ Normal' : '⚠ Non-normal'}</div>
                        <div class="text-[10px] text-gray-500">α=0.05</div>
                    </div>
                </div>`;
                if (n.interpretation) html += `<p class="text-xs text-gray-500 mb-2">${App.escapeHtml(n.interpretation)}</p>`;
            } else {
                const backendType = testType === 'chisquare' ? 'chi2' : testType;
                const res = await API.hypothesis(table, numeric, '', backendType, group, 0.05);
                const h = res?.hypothesis_test || res || {};
                const pVal = typeof h.p_value === 'number' ? h.p_value : 1;
                const significant = typeof h.significant === 'boolean' ? h.significant : pVal < 0.05;
                const statLabel = testType === 'chisquare' ? 'χ² Statistic' : (testType === 'anova' ? 'F Statistic' : 't Statistic');

                html += `<h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Results — ${testType}</h3>`;
                html += `<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center"><div class="text-lg font-bold">${typeof h.statistic === 'number' ? h.statistic.toFixed(4) : '-'}</div><div class="text-[10px] text-gray-500">${statLabel}</div></div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center"><div class="text-lg font-bold">${typeof h.effect_size === 'number' ? h.effect_size.toFixed(4) : '-'}</div><div class="text-[10px] text-gray-500">${App.escapeHtml(h.effect_size_name || 'Effect Size')}</div></div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center"><div class="text-lg font-bold">${pVal.toFixed(4)}</div><div class="text-[10px] text-gray-500">p-value</div></div>
                    <div class="p-3 rounded-lg ${significant ? 'bg-green-50 dark:bg-green-900/20' : 'bg-gray-50 dark:bg-gray-700/30'} text-center">
                        <div class="text-lg font-bold ${significant ? 'text-green-600' : 'text-gray-500'}">${significant ? 'Significant' : 'Not sig.'}</div>
                        <div class="text-[10px] text-gray-500">α=0.05</div>
                    </div>
                </div>`;
                if (h.interpretation) html += `<p class="text-xs text-gray-500 mb-2">${App.escapeHtml(h.interpretation)}</p>`;

                const gs = h.group_stats || {};
                if (testType === 'chisquare') {
                    const ct = gs.contingency_table;
                    if (ct && typeof ct === 'object') {
                        const colKeys = Object.keys(ct);
                        const rowKeys = colKeys.length ? Object.keys(ct[colKeys[0]]) : [];
                        html += `<div class="overflow-x-auto"><table class="w-full text-xs border-collapse">
                            <thead><tr class="bg-gray-100 dark:bg-gray-700/50"><th class="px-3 py-2 text-left font-semibold">${App.escapeHtml(group)} \\ ${App.escapeHtml(numeric)}</th>`;
                        colKeys.forEach(v => { html += `<th class="px-3 py-2 text-left font-semibold">${App.escapeHtml(v)}</th>`; });
                        html += '</tr></thead><tbody>';
                        rowKeys.forEach(r => {
                            html += `<tr class="border-t border-gray-100 dark:border-gray-700/50"><td class="px-3 py-1.5 font-medium">${App.escapeHtml(r)}</td>`;
                            colKeys.forEach(v => { html += `<td class="px-3 py-1.5">${ct[v][r] ?? 0}</td>`; });
                            html += '</tr>';
                        });
                        html += '</tbody></table></div>';
                    }
                } else {
                    const gNames = (h.groups && h.groups.length) ? h.groups : Object.keys(gs);
                    if (gNames.length) {
                        html += `<div class="overflow-x-auto"><table class="w-full text-xs border-collapse">
                            <thead><tr class="bg-gray-100 dark:bg-gray-700/50">
                                <th class="px-3 py-2 text-left font-semibold">Group</th>
                                <th class="px-3 py-2 text-right font-semibold">N</th>
                                <th class="px-3 py-2 text-right font-semibold">Mean</th>
                                <th class="px-3 py-2 text-right font-semibold">Std</th>
                            </tr></thead><tbody>`;
                        gNames.forEach(name => {
                            const g = gs[name] || {};
                            html += `<tr class="border-t border-gray-100 dark:border-gray-700/50">
                                <td class="px-3 py-1.5 font-medium">${App.escapeHtml(String(name))}</td>
                                <td class="px-3 py-1.5 text-right">${g.n ?? '-'}</td>
                                <td class="px-3 py-1.5 text-right font-mono">${typeof g.mean === 'number' ? g.mean.toFixed(3) : '-'}</td>
                                <td class="px-3 py-1.5 text-right font-mono">${typeof g.std === 'number' ? g.std.toFixed(3) : '-'}</td>
                            </tr>`;
                        });
                        html += '</tbody></table></div>';
                    }
                }
            }

            html += '</div>';
            el.innerHTML = html;
        } catch (e) {
            el.innerHTML = `<div class="card"><div class="text-red-500 text-sm"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(e.message || 'Test failed')}</div></div>`;
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-play mr-1"></i>Run Test'; }
        }
    },
};

window.StatsPage = StatsPage;
(window._PAGES = window._PAGES || {})["#stats"] = StatsPage;
