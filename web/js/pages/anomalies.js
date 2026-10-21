/* ═══════════════════════════════════════════
   Anomaly Detection — Statistical Outliers
   ═══════════════════════════════════════════ */

const AnomaliesPage = {
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
                    <div class="page-title-icon"><i class="fas fa-bug"></i></div>
                    <div>
                        <h2>Anomaly Detection</h2>
                        <p class="page-title-subtitle">Detect outliers and anomalies in your data using statistical methods</p>
                    </div>
                </div>
            </div>

            <!-- Auto-Detected Anomalies -->
            <div id="auto-anomalies" class="card">
                <h3 class="text-sm font-semibold mb-3"><i class="fas fa-robot text-purple-500 mr-1"></i>Auto-Detected Anomalies</h3>
                <div id="auto-anomalies-content"><p class="text-xs text-gray-400">Select a table and run detection to see auto-detected anomalies.</p></div>
            </div>

            <!-- Detection Config -->
            <div class="card">
                <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Table</label>
                        <select id="anom-table" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                            <option value="">Select table...</option>
                            ${tableNames.map(t => `<option value="${t}" ${t === App.state.selectedTable ? 'selected' : ''}>${t}</option>`).join('')}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Method</label>
                        <select id="anom-method" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                            <option value="iqr">IQR (Interquartile Range)</option>
                            <option value="zscore">Z-Score</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Threshold</label>
                        <input id="anom-threshold" type="number" value="1.5" step="0.1" min="0.5" max="5"
                            class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                    </div>
                    <div class="flex items-end">
                        <button onclick="AnomaliesPage._runDetection()" id="anom-run-btn"
                            class="w-full bg-red-500 hover:bg-red-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                            <i class="fas fa-search mr-1"></i>Detect Anomalies
                        </button>
                    </div>
                </div>
                <p class="text-[10px] text-gray-400 mt-2">IQR: values beyond Q1 - 1.5×IQR or Q3 + 1.5×IQR. Z-Score: values beyond 1.5 standard deviations.</p>
            </div>

            <!-- Results -->
            <div id="anom-results"></div>
        </div>`;

        // Load auto-detected anomalies (after DOM is rendered)
        this._loadAutoAnomalies();
    },

    async _runDetection() {
        const table = document.getElementById('anom-table').value;
        if (!table) return App.toast('Select a table first', 'error');

        const method = document.getElementById('anom-method').value;
        const threshold = parseFloat(document.getElementById('anom-threshold').value) || 1.5;
        const el = document.getElementById('anom-results');
        const btn = document.getElementById('anom-run-btn');

        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Analyzing...'; }
        el.innerHTML = '<div class="card flex items-center justify-center py-12"><i class="fas fa-spinner fa-spin text-2xl text-red-500"></i><span class="ml-3 text-sm text-gray-400">Running anomaly detection...</span></div>';

        try {
            const result = await API.detectAnomalies(table, method, threshold);
            if (!result.success) {
                el.innerHTML = `<div class="card"><div class="text-red-500 text-sm"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(result.error || 'Failed')}</div></div>`;
                return;
            }

            const summary = result.summary;
            const anomalies = result.anomalies || {};
            const anomalyCols = Object.keys(anomalies);

            let html = '';

            // Summary cards
            html += `<div class="card">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-chart-bar mr-1"></i>Detection Summary</h3>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold">${summary.total_rows.toLocaleString()}</div>
                        <div class="text-[10px] text-gray-500">Total Rows</div>
                    </div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold">${summary.columns_checked}</div>
                        <div class="text-[10px] text-gray-500">Numeric Columns</div>
                    </div>
                    <div class="p-3 rounded-lg ${anomalyCols.length > 0 ? 'bg-red-50 dark:bg-red-900/20' : 'bg-green-50 dark:bg-green-900/20'} text-center">
                        <div class="text-lg font-bold ${anomalyCols.length > 0 ? 'text-red-600' : 'text-green-600'}">${anomalyCols.length}</div>
                        <div class="text-[10px] text-gray-500">Anomaly Columns</div>
                    </div>
                    <div class="p-3 rounded-lg ${summary.total_outliers > 0 ? 'bg-amber-50 dark:bg-amber-900/20' : 'bg-green-50 dark:bg-green-900/20'} text-center">
                        <div class="text-lg font-bold ${summary.total_outliers > 0 ? 'text-amber-600' : 'text-green-600'}">${summary.total_outliers.toLocaleString()}</div>
                        <div class="text-[10px] text-gray-500">Total Outliers</div>
                    </div>
                </div>
                <div class="mt-3 flex gap-2 text-xs text-gray-500">
                    <span class="px-2 py-1 rounded bg-gray-100 dark:bg-gray-700">Method: ${summary.method.toUpperCase()}</span>
                    <span class="px-2 py-1 rounded bg-gray-100 dark:bg-gray-700">Threshold: ${summary.threshold}</span>
                </div>
            </div>`;

            // Anomaly details
            if (anomalyCols.length > 0) {
                anomalyCols.forEach(col => {
                    const a = anomalies[col];
                    html += `<div class="card">
                        <div class="flex items-center justify-between mb-3">
                            <div class="flex items-center gap-2">
                                <h4 class="text-sm font-semibold text-gray-800 dark:text-gray-200">${App.escapeHtml(col)}</h4>
                                <span class="px-2 py-0.5 rounded-full text-[10px] font-medium ${a.pct > 5 ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'}">${a.count} outliers (${a.pct}%)</span>
                            </div>
                            <div class="text-xs text-gray-400">μ=${a.mean}, σ=${a.std}</div>
                        </div>
                        <div class="overflow-x-auto">
                            <table class="w-full text-xs border-collapse">
                                <thead><tr class="bg-gray-100 dark:bg-gray-700/50">
                                    <th class="px-3 py-2 text-left font-semibold">Row #</th>
                                    <th class="px-3 py-2 text-left font-semibold">Value</th>
                                    <th class="px-3 py-2 text-left font-semibold">Deviation</th>
                                </tr></thead>
                                <tbody>`;
                    (a.examples || []).slice(0, 10).forEach(ex => {
                        const val = ex[col];
                        const dev = method === 'zscore'
                            ? `${((val - a.mean) / (a.std || 1)).toFixed(2)}σ`
                            : val > a.mean ? `+${((val - a.mean) / (a.std || 1)).toFixed(2)}σ` : `${((val - a.mean) / (a.std || 1)).toFixed(2)}σ`;
                        html += `<tr class="border-t border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/20">
                            <td class="px-3 py-1.5 text-gray-400">${ex._row_index != null ? ex._row_index : '—'}</td>
                            <td class="px-3 py-1.5 font-mono font-medium text-red-600 dark:text-red-400">${val != null ? Number(val).toLocaleString(undefined, { maximumFractionDigits: 4 }) : 'null'}</td>
                            <td class="px-3 py-1.5 text-gray-500">${dev}</td>
                        </tr>`;
                    });
                    html += '</tbody></table></div></div>';
                });
            } else {
                html += `<div class="card text-center py-8">
                    <i class="fas fa-check-circle text-green-500 text-3xl mb-2"></i>
                    <p class="text-sm font-medium text-green-600">No anomalies detected</p>
                    <p class="text-xs text-gray-400 mt-1">All numeric columns appear to be within normal ranges</p>
                </div>`;
            }

            el.innerHTML = html;
        } catch (e) {
            el.innerHTML = `<div class="card"><div class="text-red-500 text-sm"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(e.message || 'Error')}</div></div>`;
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-search mr-1"></i>Detect Anomalies'; }
        }
    },

    async _loadAutoAnomalies() {
        const el = document.getElementById('auto-anomalies-content');
        if (!el) return;
        // Try to load from auto pipeline data
        try {
            const tables = App.state.tables || {};
            const tableNames = Array.isArray(tables) ? tables : Object.keys(tables);
            if (tableNames.length === 0) return;
            const firstTable = tableNames[0];
            const profile = await API.profile(firstTable);
            if (!profile || !profile.success) return;
            const numericCols = ((profile.profile?.columns || profile.columns) || []).filter(c => c.mean !== undefined);
            if (numericCols.length === 0) return;
            var html = '<div class="text-xs space-y-1">';
            html += '<div class="text-[10px] text-gray-500 mb-1">Table: ' + App.escapeHtml(firstTable) + '</div>';
            var anomalyCount = 0;
            numericCols.slice(0, 8).forEach(function(c) {
                if (c.outlier_count > 0) {
                    anomalyCount++;
                    html += '<div class="flex items-center gap-2 py-1 px-2 bg-amber-50 dark:bg-amber-900/10 rounded"><span class="font-medium">' + App.escapeHtml(c.name) + '</span><span class="text-amber-600">' + c.outlier_count + ' outliers (' + c.outlier_pct + '%)</span><span class="text-[9px] text-gray-400">bounds: [' + (c.outlier_bounds?.lower?.toFixed(2)||'-') + ', ' + (c.outlier_bounds?.upper?.toFixed(2)||'-') + ']</span></div>';
                }
            });
            if (anomalyCount === 0) html += '<p class="text-gray-400 text-xs">No anomalies detected in ' + App.escapeHtml(firstTable) + '</p>';
            html += '</div>';
            el.innerHTML = html;
        } catch(e) {
            el.innerHTML = '<p class="text-xs text-gray-400">Auto-detection unavailable</p>';
        }
    },
};

window.AnomaliesPage = AnomaliesPage;
(window._PAGES = window._PAGES || {})["#anomalies"] = AnomaliesPage;
