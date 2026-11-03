/* ═══════════════════════════════════════════
   Trend Forecast — linear trend projection
   ═══════════════════════════════════════════ */

const ForecastPage = {
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
                    <div class="page-title-icon"><i class="fas fa-chart-line"></i></div>
                    <div>
                        <h2>Trend Forecast</h2>
                        <p class="page-title-subtitle">Least-squares linear trend projection with classical seasonal decomposition and changepoint detection — a trend projection, not an ARIMA/ETS/Prophet model.</p>
                    </div>
                </div>
            </div>

            <!-- Controls -->
            <div class="card">
                <div class="grid grid-cols-1 md:grid-cols-5 gap-3">
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Table</label>
                        <select id="fc-table" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                            <option value="">Select...</option>
                            ${tableNames.map(t => `<option value="${t}" ${t === App.state.selectedTable ? 'selected' : ''}>${t}</option>`).join('')}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Date Column</label>
                        <select id="fc-date" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600"></select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Value Column</label>
                        <select id="fc-value" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600"></select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Periods to Forecast</label>
                        <input id="fc-periods" type="number" value="12" min="1" max="120" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                    </div>
                    <div class="flex items-end">
                        <button onclick="ForecastPage._runForecast()" id="fc-run-btn" class="w-full bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                            <i class="fas fa-magic mr-1"></i>Generate Forecast
                        </button>
                    </div>
                </div>
            </div>

            <!-- Results -->
            <div id="fc-results" class="space-y-4">
                <div class="card text-center py-12 text-gray-400">
                    <i class="fas fa-chart-line text-4xl mb-3 opacity-30"></i>
                    <p>Select a table with a date column and numeric values to project a trend</p>
                </div>
            </div>
        </div>`;

        // Auto-load columns when table selected
        document.getElementById('fc-table')?.addEventListener('change', () => ForecastPage._loadColumns());
        if (App.state.selectedTable) {
            document.getElementById('fc-table').value = App.state.selectedTable;
            ForecastPage._loadColumns();
        }
    },

    async _loadColumns() {
        const table = document.getElementById('fc-table')?.value;
        if (!table) return;

        try {
            const profile = await API.profile(table);
            const prof = profile?.profile || profile || {};
            const colsArr = prof.columns || profile?.columns || [];
            const columns = Array.isArray(colsArr)
                ? colsArr.reduce((m, c) => { const n = c.name || c.column_name; if (n) m[n] = c; return m; }, {})
                : colsArr;

            const dateCols = Object.entries(columns).filter(([k, v]) => {
                if (v.type === 'datetime' || v.dtype?.includes('date') || v.dtype?.includes('time')) return true;
                if (v.type === 'numeric' || v.type === 'categorical' && v.dtype?.includes('bool')) return false;
                const name = k.toLowerCase();
                if (/(date|time|timestamp|year|month|day|week|quarter|created|updated|modified)/.test(name)) return true;
                const samples = (v.sample_values || []).filter(s => s != null && s !== '');
                return samples.length > 0 && samples.every(s => /^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(String(s)));
            }).map(([k]) => k);
            const numCols = Object.entries(columns).filter(([, v]) => v.type === 'numeric').map(([k]) => k);

            const dateSelect = document.getElementById('fc-date');
            const valSelect = document.getElementById('fc-value');

            if (dateSelect) dateSelect.innerHTML = dateCols.map(c => `<option value="${c}">${c}</option>`).join('') || '<option value="">No date columns</option>';
            if (valSelect) valSelect.innerHTML = numCols.map(c => `<option value="${c}">${c}</option>`).join('') || '<option value="">No numeric columns</option>';
        } catch (_) {}
    },

    async _runForecast() {
        const table = document.getElementById('fc-table')?.value;
        const dateCol = document.getElementById('fc-date')?.value;
        const valCol = document.getElementById('fc-value')?.value;
        const periods = parseInt(document.getElementById('fc-periods')?.value) || 12;

        if (!table) return App.toast('Select a table', 'error');
        if (!dateCol || !valCol) return App.toast('Select date and value columns', 'error');

        const el = document.getElementById('fc-results');
        const btn = document.getElementById('fc-run-btn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Projecting trend…'; }
        el.innerHTML = '<div class="card flex items-center justify-center py-12"><i class="fas fa-spinner fa-spin text-2xl text-blue-500"></i><span class="ml-3 text-sm text-gray-400">Computing forecast...</span></div>';

        try {
            const result = await API.forecast(table, dateCol, valCol, periods);
            if (!result.success) {
                el.innerHTML = `<div class="card"><div class="text-red-500 text-sm">${App.escapeHtml(result.error || 'Failed')}</div></div>`;
                return;
            }

            const forecast = result.forecast || result;
            const histDates = forecast.historical_dates || forecast.dates || [];
            const histVals = forecast.historical_values || forecast.actual || [];
            const fcDates = forecast.forecast_dates || [];
            const fcVals = forecast.forecast_values || [];
            const lower = forecast.lower_bound || [];
            const upper = forecast.upper_bound || [];
            const historical = histDates.map((d, i) => ({ date: d, value: histVals[i] }));
            const predicted = fcDates.map((d, i) => ({ date: d, value: fcVals[i], lower: lower[i], upper: upper[i] }));

            let html = '';

            // Summary cards
            let trendDir = forecast.trend_direction || 'unknown';
            if (trendDir === 'unknown' && predicted.length > 0 && historical.length > 0) {
                const lastPred = predicted[predicted.length - 1]?.yhat || predicted[predicted.length - 1]?.value || 0;
                const lastHist = historical[historical.length - 1]?.value || 0;
                trendDir = lastPred > lastHist ? 'upward' : 'downward';
            }
            const trendColor = trendDir === 'upward' ? 'text-green-600' : trendDir === 'downward' ? 'text-red-600' : 'text-gray-500';

            html += `<div class="card">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-chart-line mr-1"></i>Forecast Summary</h3>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold">${historical.length.toLocaleString()}</div>
                        <div class="text-[10px] text-gray-500">Historical Points</div>
                    </div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold">${periods}</div>
                        <div class="text-[10px] text-gray-500">Forecast Periods</div>
                    </div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold ${trendColor}">${trendDir}</div>
                        <div class="text-[10px] text-gray-500">Trend Direction</div>
                    </div>
                    <div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 text-center">
                        <div class="text-lg font-bold font-mono">${forecast.mase != null ? forecast.mase.toFixed(3) : '—'}</div>
                        <div class="text-[10px] text-gray-500">MASE (0=perfect)</div>
                    </div>
                </div>
            </div>`;

            // Chart
            const allLabels = [
                ...historical.map(d => d.date || d.ds || d.timestamp || ''),
                ...predicted.map(d => d.date || d.ds || d.timestamp || ''),
            ];

            const histValues = historical.map(d => d.value || d.y || d.yhat || 0);
            const predValues = predicted.map(d => d.yhat || d.value || d.predicted || 0);

            // Fill with nulls so the chart shows a gap
            const paddedPred = [
                ...new Array(historical.length).fill(null),
                ...predValues,
            ];

            html += `<div class="card">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-chart-area mr-1"></i>Forecast Chart</h3>
                <div class="flex justify-center">
                    <canvas id="fc-chart" width="600" height="300" style="width:100%"></canvas>
                </div>
            </div>`;

            // Forecast table
            if (predicted.length > 0) {
                html += `<div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-table mr-1"></i>Predicted Values</h3>
                    <div class="overflow-x-auto max-h-[250px]">
                        <table class="w-full text-xs border-collapse">
                        <thead><tr class="bg-gray-100 dark:bg-gray-700/50">
                            <th class="px-3 py-2 text-left font-semibold">Period</th>
                            <th class="px-3 py-2 text-right font-semibold">Forecast</th>
                            <th class="px-3 py-2 text-right font-semibold">Lower Bound</th>
                            <th class="px-3 py-2 text-right font-semibold">Upper Bound</th>
                        </tr></thead><tbody>`;
                predicted.forEach((p, i) => {
                    html += `<tr class="border-t border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/20">
                        <td class="px-3 py-1.5 text-gray-500">${i + 1}</td>
                        <td class="px-3 py-1.5 text-right font-mono font-medium">${(p.yhat || p.value || 0).toFixed(2)}</td>
                        <td class="px-3 py-1.5 text-right font-mono text-gray-400">${(p.yhat_lower || p.lower || 0).toFixed(2)}</td>
                        <td class="px-3 py-1.5 text-right font-mono text-gray-400">${(p.yhat_upper || p.upper || 0).toFixed(2)}</td>
                    </tr>`;
                });
                html += '</tbody></table></div></div>';
            }

            el.innerHTML = html;

            // Render chart after DOM
            setTimeout(() => {
                const canvas = document.getElementById('fc-chart');
                if (canvas && window.Charts) {
                    Charts.line('fc-chart', allLabels, [
                        { label: 'Historical', data: [...histValues, ...new Array(predValues.length).fill(null)], borderColor: '#2563eb', fill: false },
                        { label: 'Forecast', data: paddedPred, borderColor: '#b91c1c', borderDash: [5, 5], fill: false },
                    ], {
                        plugins: { title: { display: true, text: `${valCol} Forecast — ${table}` } },
                        scales: { y: { beginAtZero: false } },
                    });
                }
            }, 200);
        } catch (e) {
            el.innerHTML = `<div class="card"><div class="text-red-500 text-sm"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(e.message || 'Error')}</div></div>`;
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-magic mr-1"></i>Generate Forecast'; }
        }
    },
};

window.ForecastPage = ForecastPage;
(window._PAGES = window._PAGES || {})["#forecast"] = ForecastPage;
