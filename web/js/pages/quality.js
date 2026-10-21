/* ═══════════════════════════════════════════
   Quality Trends — Score History & Monitoring
   ═══════════════════════════════════════════ */

const QualityPage = {
    _trendData: null,

    async init() {},
    async destroy() { this._trendData = null; },

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
                        <h2>Data Quality Trends</h2>
                        <p class="page-title-subtitle">Track data quality scores over time and detect regressions</p>
                    </div>
                </div>
            </div>

            <!-- Controls -->
            <div class="card">
                <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Table</label>
                        <select id="qt-table" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                            <option value="">Select table...</option>
                            ${tableNames.map(t => `<option value="${t}" ${t === App.state.selectedTable ? 'selected' : ''}>${t}</option>`).join('')}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Time Range</label>
                        <select id="qt-days" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                            <option value="7">Last 7 days</option>
                            <option value="30" selected>Last 30 days</option>
                            <option value="90">Last 90 days</option>
                            <option value="365">Last year</option>
                        </select>
                    </div>
                    <div class="flex items-end gap-2">
                        <button onclick="QualityPage._loadTrend()" id="qt-load-btn" class="flex-1 bg-green-500 hover:bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                            <i class="fas fa-chart-line mr-1"></i>Load Trend
                        </button>
                    </div>
                    <div class="flex items-end">
                        <button onclick="QualityPage._recordSnapshot()" id="qt-snapshot-btn" class="w-full bg-amber-500 hover:bg-amber-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                            <i class="fas fa-camera mr-1"></i>Record Snapshot
                        </button>
                    </div>
                </div>
            </div>

            <!-- Trend Chart + Stats -->
            <div id="qt-results" class="space-y-4">
                <div class="card text-center py-12 text-gray-400">
                    <i class="fas fa-chart-line text-4xl mb-3 opacity-30"></i>
                    <p>Select a table and load its quality trend</p>
                </div>
            </div>
        </div>`;
    },

    async _loadTrend() {
        const table = document.getElementById('qt-table')?.value;
        if (!table) return App.toast('Select a table first', 'error');

        const days = parseInt(document.getElementById('qt-days')?.value) || 30;
        const el = document.getElementById('qt-results');
        const btn = document.getElementById('qt-load-btn');

        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Loading...'; }
        el.innerHTML = '<div class="card flex items-center justify-center py-12"><i class="fas fa-spinner fa-spin text-2xl text-green-500"></i></div>';

        try {
            const [trendResult, qualityResult] = await Promise.all([
                API.qualityTrend(table, days).catch(() => ({ trend: [] })),
                API.qualityScore(table).catch(() => null),
            ]);

            const trend = trendResult?.trend || [];
            const currentQuality = qualityResult?.quality?.overall_score ?? qualityResult?.score ?? qualityResult?.quality_score ?? null;

            let html = '';

            // Current quality score
            if (currentQuality != null) {
                const scoreColor = currentQuality >= 80 ? 'green' : currentQuality >= 50 ? 'amber' : 'red';
                html += `<div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div class="card text-center">
                        <div class="text-3xl font-bold text-${scoreColor}-600 dark:text-${scoreColor}-400">${Math.round(currentQuality)}</div>
                        <div class="text-xs text-gray-500">Current Score</div>
                    </div>
                    <div class="card text-center">
                        <div class="text-3xl font-bold text-gray-700 dark:text-gray-300">${trend.length}</div>
                        <div class="text-xs text-gray-500">Snapshots</div>
                    </div>
                    <div class="card text-center">
                        <div class="text-3xl font-bold ${trend.length >= 2 ? (trend[trend.length - 1]?.overall_score >= trend[0]?.overall_score ? 'text-green-600' : 'text-red-600') : 'text-gray-400'}">
                            ${trend.length >= 2 ? (trend[trend.length - 1]?.overall_score >= trend[0]?.overall_score ? '↑' : '↓') : '—'}
                        </div>
                        <div class="text-xs text-gray-500">Trend Direction</div>
                    </div>
                    <div class="card text-center">
                        <div class="text-3xl font-bold text-gray-700 dark:text-gray-300">${days}d</div>
                        <div class="text-xs text-gray-500">Time Window</div>
                    </div>
                </div>`;
            }

            // Trend chart
            if (trend.length > 0) {
                const labels = trend.map(d => {
                    const date = new Date(d.timestamp);
                    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                });
                const scores = trend.map(d => d.overall_score || 0);

                html += `<div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-chart-line mr-1"></i>Quality Score Over Time</h3>
                    <div class="flex justify-center">
                        <canvas id="qt-chart" width="500" height="250" style="max-width:800px;width:100%"></canvas>
                    </div>
                </div>`;

                // Trend table
                html += `<div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-table mr-1"></i>Snapshot History</h3>
                    <div class="overflow-x-auto max-h-[300px]">
                        <table class="w-full text-xs border-collapse">
                        <thead><tr class="bg-gray-100 dark:bg-gray-700/50">
                            <th class="px-3 py-2 text-left font-semibold">Timestamp</th>
                            <th class="px-3 py-2 text-left font-semibold">Score</th>
                            <th class="px-3 py-2 text-left font-semibold">Completeness</th>
                            <th class="px-3 py-2 text-left font-semibold">Uniqueness</th>
                            <th class="px-3 py-2 text-left font-semibold">Consistency</th>
                        </tr></thead><tbody>`;

                trend.slice().reverse().forEach(d => {
                    const score = d.overall_score || 0;
                    const scoreColor = score >= 80 ? 'text-green-600' : score >= 50 ? 'text-amber-600' : 'text-red-600';
                    const dims = d.dimension_scores || {};
                    html += `<tr class="border-t border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/20">
                        <td class="px-3 py-2 text-gray-500">${d.timestamp ? new Date(d.timestamp).toLocaleString() : '—'}</td>
                        <td class="px-3 py-2 font-bold ${scoreColor}">${Math.round(score)}/100</td>
                        <td class="px-3 py-2 text-gray-500">${dims.completeness != null ? Math.round(dims.completeness) : '—'}</td>
                        <td class="px-3 py-2 text-gray-500">${dims.uniqueness != null ? Math.round(dims.uniqueness) : '—'}</td>
                        <td class="px-3 py-2 text-gray-500">${dims.consistency != null ? Math.round(dims.consistency) : '—'}</td>
                    </tr>`;
                });

                html += '</tbody></table></div></div>';

                // Render chart after DOM is ready
                setTimeout(() => {
                    const canvas = document.getElementById('qt-chart');
                    if (canvas && window.Charts) {
                        Charts.line('qt-chart', labels, [{
                            label: 'Quality Score',
                            data: scores,
                            borderColor: '#10b981',
                            backgroundColor: 'rgba(16, 185, 129, 0.1)',
                            fill: true,
                            tension: 0.3,
                        }], {
                            scales: { y: { beginAtZero: false, min: 0, max: 100, title: { display: true, text: 'Score' } } },
                            plugins: { title: { display: true, text: `Quality Score Trend — ${table}` } },
                        });
                    }
                }, 100);
            } else {
                html += `<div class="card text-center py-8">
                    <i class="fas fa-camera text-gray-300 text-3xl mb-2"></i>
                    <p class="text-sm text-gray-500">No quality snapshots recorded yet</p>
                    <p class="text-xs text-gray-400 mt-1">Click "Record Snapshot" to save the current quality state</p>
                </div>`;
            }

            el.innerHTML = html;
        } catch (e) {
            el.innerHTML = `<div class="card"><div class="text-red-500 text-sm">${App.escapeHtml(e.message || 'Error')}</div></div>`;
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-chart-line mr-1"></i>Load Trend'; }
        }
    },

    async _recordSnapshot() {
        const table = document.getElementById('qt-table')?.value;
        if (!table) return App.toast('Select a table first', 'error');

        const btn = document.getElementById('qt-snapshot-btn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Recording...'; }

        try {
            const result = await API.recordQualitySnapshot(table);
            if (result?.success) {
                App.success('Quality snapshot recorded');
                // Auto-reload trend
                await this._loadTrend();
            } else {
                App.error('Failed: ' + (result?.error || 'Unknown'));
            }
        } catch (e) {
            App.error('Snapshot failed: ' + (e.message || 'Error'));
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-camera mr-1"></i>Record Snapshot'; }
        }
    },
};

window.QualityPage = QualityPage;
(window._PAGES = window._PAGES || {})["#quality"] = QualityPage;
