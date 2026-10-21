/* ═══════════════════════════════════════════
   Export Page — Download data + reports
   ═══════════════════════════════════════════ */

const ExportPage = {
    dashboards: [],

    async init() {
        try {
            const res = await API.listDashboards();
            this.dashboards = (res && res.dashboards) || [];
        } catch (e) { this.dashboards = []; }
    },
    async destroy() {},

    async render() {
        const pc = document.getElementById('page-content');
        const tables = App.state.tables || {};
        const tableNames = Object.keys(tables);
        const dashboards = this.dashboards || [];

        pc.innerHTML = `
        <div class="max-w-4xl mx-auto space-y-6 animate-fade-in">
            <div class="page-title"><div class="page-title-icon"><i class="fas fa-file-export"></i></div><div><h2>Export Data</h2></div></div>

            <!-- Dashboard Report Export (rich, designed) -->
            <div class="card">
                <h3 class="text-lg font-semibold mb-4">📊 Dashboard Report Export</h3>
                <p class="text-sm text-gray-500 mb-4">Export a saved dashboard as a designed Power BI report or PowerPoint deck — matching your in-app dashboard (KPI cards, charts, tables).</p>
                <div class="mb-4">
                    <label class="block text-sm font-medium mb-1">Select Dashboard</label>
                    <select id="exp-dashboard" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="">Choose a dashboard...</option>
                        ${dashboards.map(d => `<option value="${d.id}">${d.name || d.id}</option>`).join('')}
                    </select>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <button onclick="ExportPage.downloadDashboard('pbit')" class="border-2 border-yellow-400 bg-yellow-50 dark:bg-yellow-900/20 hover:bg-yellow-100 dark:hover:bg-yellow-900/40 px-4 py-4 rounded-lg text-center transition-colors">
                        <i class="fab fa-microsoft text-yellow-500 text-xl mb-1"></i>
                        <div class="font-medium">Power BI Report (.pbix)</div>
                        <div class="text-xs text-gray-500">Designed report with KPI cards + charts</div>
                    </button>
                    <button onclick="ExportPage.downloadDashboard('pptx')" class="border-2 border-red-400 bg-red-50 dark:bg-red-900/20 hover:bg-red-100 dark:hover:bg-red-900/40 px-4 py-4 rounded-lg text-center transition-colors">
                        <i class="fas fa-file-powerpoint text-red-500 text-xl mb-1"></i>
                        <div class="font-medium">PowerPoint Deck (.pptx)</div>
                        <div class="text-xs text-gray-500">Designed slides with chart images</div>
                    </button>
                </div>
                <div id="dash-result" class="mt-4 hidden"></div>
            </div>

            <!-- Data Export -->
            <div class="card">
                <h3 class="text-lg font-semibold mb-4">📦 Data Export</h3>
                <div class="mb-4">
                    <label class="block text-sm font-medium mb-1">Select Table</label>
                    <select id="exp-table" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="">Choose a table...</option>
                        ${tableNames.map(t => `<option value="${t}">${t} (${tables[t]?.row_count || 0} rows)</option>`).join('')}
                    </select>
                </div>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <button onclick="ExportPage.download('csv')" class="bg-green-500 hover:bg-green-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-file-csv mr-1"></i>CSV
                    </button>
                    <button onclick="ExportPage.download('parquet')" class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-file-code mr-1"></i>Parquet
                    </button>
                    <button onclick="ExportPage.download('xlsx')" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-file-excel mr-1"></i>Excel
                    </button>
                    <button onclick="ExportPage.download('markdown')" class="bg-gray-500 hover:bg-gray-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-file-lines mr-1"></i>Markdown
                    </button>
                    <button onclick="ExportPage.download('html')" class="bg-orange-500 hover:bg-orange-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-file-code mr-1"></i>HTML Report
                    </button>
                    <button onclick="ExportPage.download('pptx')" class="bg-red-500 hover:bg-red-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-file-powerpoint mr-1"></i>PowerPoint
                    </button>
                    <button onclick="ExportPage.download('pdf')" class="bg-indigo-500 hover:bg-indigo-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-file-pdf mr-1"></i>PDF Report
                    </button>
                    <button onclick="ExportPage.download('zip')" class="bg-purple-500 hover:bg-purple-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-file-archive mr-1"></i>ZIP All
                    </button>
                    <button onclick="ExportPage.copyMarkdown()" class="bg-indigo-500 hover:bg-indigo-600 text-white px-4 py-3 rounded-lg font-medium transition-colors">
                        <i class="fas fa-copy mr-1"></i>Copy as MD
                    </button>
                </div>
            </div>

            <!-- BI Export -->
            <div class="card">
                <h3 class="text-lg font-semibold mb-4">📊 BI Tool Export</h3>
                <p class="text-sm text-gray-500 mb-4">Export data formatted for popular BI tools</p>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <button onclick="ExportPage.exportBI('powerbi')" class="border-2 border-yellow-400 bg-yellow-50 dark:bg-yellow-900/20 hover:bg-yellow-100 dark:hover:bg-yellow-900/40 px-4 py-4 rounded-lg text-center transition-colors">
                        <i class="fas fa-chart-bar text-yellow-500 text-xl mb-1"></i>
                        <div class="font-medium">Power BI</div>
                        <div class="text-xs text-gray-500">CSV import</div>
                    </button>
                    <button onclick="ExportPage.exportBI('tableau')" class="border-2 border-blue-400 bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/40 px-4 py-4 rounded-lg text-center transition-colors">
                        <i class="fas fa-chart-pie text-blue-500 text-xl mb-1"></i>
                        <div class="font-medium">Tableau</div>
                        <div class="text-xs text-gray-500">Data extract</div>
                    </button>
                    <button onclick="ExportPage.exportBI('looker')" class="border-2 border-indigo-400 bg-indigo-50 dark:bg-indigo-900/20 hover:bg-indigo-100 dark:hover:bg-indigo-900/40 px-4 py-4 rounded-lg text-center transition-colors">
                        <i class="fas fa-chart-area text-indigo-500 text-xl mb-1"></i>
                        <div class="font-medium">Looker</div>
                        <div class="text-xs text-gray-500">LookML config</div>
                    </button>
                </div>
                <div id="bi-result" class="mt-4 hidden"></div>
            </div>

            <!-- Report Generation -->
            <div class="card">
                <h3 class="text-lg font-semibold mb-4">📄 Report Generation</h3>
                <div class="mb-4">
                    <label class="block text-sm font-medium mb-1">Table for Report</label>
                    <select id="report-table" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="">Choose a table...</option>
                        ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                    </select>
                </div>
                <button onclick="ExportPage.generateReport()" class="bg-primary-500 hover:bg-primary-600 text-white px-6 py-2 rounded-lg font-medium transition-colors">
                    <i class="fas fa-file-alt mr-1"></i>Generate Executive Report
                </button>
                <div id="report-result" class="mt-4 hidden"></div>
            </div>

            <!-- Schema Documentation -->
            <div class="card">
                <h3 class="text-lg font-semibold mb-4">📋 Schema Documentation</h3>
                <button onclick="ExportPage.downloadSchemaDoc()" class="bg-gray-500 hover:bg-gray-600 text-white px-4 py-2 rounded-lg text-sm transition-colors">
                    <i class="fas fa-download mr-1"></i>Download Schema Doc (Markdown)
                </button>
            </div>
        </div>`;
    },

    _selectedDashboard() {
        const el = document.getElementById('exp-dashboard');
        if (!el || !el.value) return null;
        return (this.dashboards || []).find(d => d.id === el.value) || null;
    },

    _downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
    },

    _showDashResult(html) {
        const el = document.getElementById('dash-result');
        if (!el) return;
        el.classList.remove('hidden');
        el.innerHTML = html;
    },

    async _pollExportJob(jobId) {
        for (let i = 0; i < 90; i++) {
            await new Promise(r => setTimeout(r, 1000));
            const res = await API.exportJobStatus(jobId);
            const status = res && res.job && res.job.status;
            if (status === 'succeeded') return API.exportJobDownload(jobId);
            if (status === 'failed') throw new Error((res.job && res.job.error) || 'Export failed');
        }
        throw new Error('Export timed out');
    },

    async downloadDashboard(fmt) {
        const dash = this._selectedDashboard();
        if (!dash) return App.toast('Select a dashboard first', 'error');
        const name = (dash.name || dash.id || 'dashboard').replace(/[\\/:*?"<>|]+/g, '_');
        try {
            if (fmt === 'pbit') {
                const blob = await API.exportDashboardPbit(dash.id);
                this._downloadBlob(blob, `${name}.pbix`);
                this._showDashResult(`<div class="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg text-sm">✅ <b>${name}.pbix</b> downloaded — open it in Power BI Desktop.</div>`);
            } else if (fmt === 'pptx') {
                const { jobId } = await API.exportDashboardJob(dash.id, 'pptx');
                this._showDashResult(`<div class="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg text-sm">⏳ Building PowerPoint deck... this can take a few seconds.</div>`);
                const blob = await this._pollExportJob(jobId);
                this._downloadBlob(blob, `${name}.pptx`);
                this._showDashResult(`<div class="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg text-sm">✅ <b>${name}.pptx</b> downloaded — designed slides with KPI cards + chart images.</div>`);
            }
        } catch (e) {
            this._showDashResult(`<div class="bg-red-50 dark:bg-red-900/20 p-4 rounded-lg text-sm">❌ Export failed: ${App.escapeHtml(e.message || e)}</div>`);
        }
    },

    async download(format) {
        const table = document.getElementById('exp-table').value;
        if (!table) return App.toast('Select a table first', 'error');

        // Handle new formats
        if (format === 'html') {
            window.open(`/export/html/${encodeURIComponent(table)}`, '_blank');
            return;
        }
        if (format === 'pptx') {
            window.open(`/export/pptx/${encodeURIComponent(table)}`, '_blank');
            return;
        }
        if (format === 'pdf') {
            window.open(`/export/pdf/${encodeURIComponent(table)}`, '_blank');
            return;
        }

        const url = format === 'zip' ? `/export/zip` : `/export/${format}/${table}`;
        window.open(url, '_blank');
    },

    async copyMarkdown() {
        const table = document.getElementById('exp-table').value;
        if (!table) return App.toast('Select a table first', 'error');
        try {
            const resp = await fetch((window.__API_BASE__ || '') + `/export/markdown/${encodeURIComponent(table)}`);
            const text = await resp.text();
            await navigator.clipboard.writeText(text);
            App.success('Markdown copied to clipboard!');
        } catch (e) { App.toast('Copy failed', 'error'); }
    },

    async exportBI(format) {
        const table = document.getElementById('exp-table').value;
        if (!table) return App.toast('Select a table first', 'error');
        try {
            const result = await API.exportBI(table, format);
            const el = document.getElementById('bi-result');
            el.classList.remove('hidden');
            el.innerHTML = `<div class="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg"><pre class="text-sm overflow-x-auto">${JSON.stringify(result, null, 2).substring(0, 1000)}</pre></div>`;
        } catch (e) { App.toast('Export failed', 'error'); }
    },

    async generateReport() {
        const table = document.getElementById('report-table').value;
        if (!table) return App.toast('Select a table first', 'error');
        try {
            const result = await API.report(table);
            if (result.success) {
                const el = document.getElementById('report-result');
                el.classList.remove('hidden');
                el.innerHTML = `
                    <div class="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg mb-3">
                        <p class="font-medium text-green-700 dark:text-green-300">Report generated! Sections: ${result.sections?.join(', ')}</p>
                    </div>
                    <iframe srcdoc="${App.escapeHtml(result.report_html || '')}" class="w-full h-96 border rounded-lg"></iframe>
                    <a href="/report/${table}" target="_blank" class="inline-block mt-2 text-primary-500 hover:underline text-sm"><i class="fas fa-external-link-alt mr-1"></i>Open full report</a>`;
            }
        } catch (e) { App.toast('Report generation failed', 'error'); }
    },

    async downloadSchemaDoc() {
        try {
            const resp = await fetch((window.__API_BASE__ || '') + '/export/schema-doc');
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a'); a.href = url; a.download = 'schema.md'; a.click();
            URL.revokeObjectURL(url);
        } catch (e) { App.toast('Download failed', 'error'); }
    }
};

// Register with SPA router
window.ExportPage = ExportPage;
(window._PAGES = window._PAGES || {})["#export"] = ExportPage;