/* ═══════════════════════════════════════════
   Analysis Page — 16-tab data analysis suite
   ═══════════════════════════════════════════ */

const AnalysisPage = {
    _selectedTable: null,
    _activeTab: 'profiles',
    _tabLoaded: {},
    _profileData: null,

    TABS: [
        { id: 'profiles',    icon: 'fa-columns',      label: 'Column Profiles' },
        { id: 'correlations', icon: 'fa-th',          label: 'Correlations' },
        { id: 'distributions', icon: 'fa-chart-bar',  label: 'Distributions' },
        { id: 'outliers',     icon: 'fa-exclamation-triangle', label: 'Outliers' },
        { id: 'forecast',     icon: 'fa-chart-line',  label: 'Forecast' },
        { id: 'segmentation', icon: 'fa-users',       label: 'Segmentation' },
        { id: 'kpi',          icon: 'fa-calculator',  label: 'Custom KPI' },
        { id: 'merge',        icon: 'fa-code-branch', label: 'Data Merge' },
        { id: 'chart',        icon: 'fa-palette',     label: 'Chart Builder' },
        { id: 'stats',        icon: 'fa-flask',       label: 'Statistical Tests' },
        { id: 'timeseries',   icon: 'fa-clock',       label: 'Time Series' },
        { id: 'abtest',       icon: 'fa-balance-scale', label: 'A/B Testing' },
        { id: 'quality',      icon: 'fa-shield-alt',  label: 'Quality' },
        { id: 'pii',          icon: 'fa-user-secret', label: 'PII' },
        { id: 'insights',     icon: 'fa-lightbulb',   label: 'Insights' },
        { id: 'report',       icon: 'fa-file-alt',    label: 'Report' },
        { id: 'storytelling', icon: 'fa-book-open',  label: 'Storytelling' },
    ],

    async init() {
        this._selectedTable = null;
        this._activeTab = 'profiles';
        this._tabLoaded = {};
        this._profileData = null;
    },

    async destroy() {
        this._tabLoaded = {};
        this._profileData = null;
    },

    /* ── Render shell ──────────────────────────────────────────────── */
    async render() {
        await App.refreshTables();
        const pc = document.getElementById('page-content');
        const tables = App.state.tables || {};
        const tableNames = Array.isArray(tables) ? tables :
            (typeof tables === 'object' && tables !== null) ? Object.keys(tables) : [];

        pc.innerHTML = `
        <div class="max-w-6xl mx-auto space-y-5 animate-fade-in">
            <div class="flex items-center justify-between flex-wrap gap-3">
                <div class="page-title">
                    <div class="page-title-icon"><i class="fas fa-microscope"></i></div>
                    <div><h2>Analysis Tools</h2></div>
                </div>
                <div class="flex items-center gap-3">
                    <select id="ana-table" class="border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600 min-w-[200px]"
                        onchange="AnalysisPage._onTableChange(this.value)">
                        <option value="">Select a table...</option>
                        ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                    </select>
                </div>
            </div>

            <!-- Tab Bar -->
            <div class="flex gap-1 overflow-x-auto pb-1 border-b dark:border-gray-700" id="ana-tab-bar">
                ${this.TABS.map(tab => `
                    <button class="ana-tab-btn px-3 py-2 text-sm font-medium rounded-t-lg whitespace-nowrap transition-colors
                        ${tab.id === this._activeTab ? 'bg-primary-500 text-white' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'}"
                        data-tab="${tab.id}" onclick="AnalysisPage._switchTab('${tab.id}')">
                        <i class="fas ${tab.icon} mr-1"></i>${tab.label}
                    </button>
                `).join('')}
            </div>

            <!-- Tab Content Area -->
            <div id="ana-tab-content" class="card min-h-[300px]">
                <p class="text-gray-400 text-center py-12" id="ana-placeholder">Select a table to begin analysis.</p>
            </div>
        </div>`;
    },

    /* ── Table selection ───────────────────────────────────────────── */
    async _onTableChange(table) {
        this._selectedTable = table || null;
        this._tabLoaded = {};
        this._profileData = null;
        if (table) {
            this._loadTab(this._activeTab);
        } else {
            document.getElementById('ana-tab-content').innerHTML =
                '<p class="text-gray-400 text-center py-12">Select a table to begin analysis.</p>';
        }
    },

    /* ── Tab switching ─────────────────────────────────────────────── */
    async _switchTab(tabId) {
        if (!this._selectedTable) return App.toast('Select a table first', 'error');
        this._activeTab = tabId;

        document.querySelectorAll('.ana-tab-btn').forEach(btn => {
            btn.classList.toggle('bg-primary-500', btn.dataset.tab === tabId);
            btn.classList.toggle('text-white', btn.dataset.tab === tabId);
            btn.classList.toggle('text-gray-600', btn.dataset.tab !== tabId);
        });

        this._loadTab(tabId);
    },

    async _loadTab(tabId) {
        if (!this._selectedTable) return;
        const content = document.getElementById('ana-tab-content');
        if (!content) return;
        content.innerHTML = '<div class="flex items-center justify-center py-12"><i class="fas fa-spinner fa-spin text-2xl text-primary-500"></i></div>';

        try {
            switch (tabId) {
                case 'profiles':    await this._renderProfiles(content); break;
                case 'correlations': await this._renderCorrelations(content); break;
                case 'distributions': await this._renderDistributions(content); break;
                case 'outliers':    await this._renderOutliers(content); break;
                case 'forecast':    await this._renderForecast(content); break;
                case 'segmentation': await this._renderSegmentation(content); break;
                case 'kpi':         this._renderKPI(content); break;
                case 'merge':       this._renderMerge(content); break;
                case 'chart':       this._renderChartBuilder(content); break;
                case 'stats':       this._renderStats(content); break;
                case 'timeseries':  this._renderTimeSeries(content); break;
                case 'abtest':      this._renderABTest(content); break;
                case 'quality':     await this._renderQuality(content); break;
                case 'pii':         this._renderPII(content); break;
                case 'insights':    this._renderInsights(content); break;
                case 'report':      this._renderReport(content); break;
                case 'storytelling': this._renderStorytelling(content); break;
            }
            this._tabLoaded[tabId] = true;
        } catch (e) {
            content.innerHTML = `<div class="text-center py-12">
                <i class="fas fa-exclamation-circle text-red-400 text-3xl mb-3"></i>
                <p class="text-red-500">Failed to load: ${App.escapeHtml(e.message || 'Unknown error')}</p></div>`;
        }
    },

    /* ── Helper: normalize profile data to {colName: info} object ── */
    _normalizeProfile(data) {
        // Backend returns {success, profile: {columns: [{name, dtype, ...}], row_count, ...}}
        // or sometimes {success, columns: {colName: {...}}}
        const profile = data.profile || data;
        const colsArr = profile.columns || data.columns || [];
        if (Array.isArray(colsArr)) {
            // Convert [{name, dtype, count, ...}] -> {name: {dtype, count, ...}}
            const map = {};
            colsArr.forEach(c => {
                const name = c.name || c.column_name || '';
                if (name) map[name] = c;
            });
            return map;
        }
        return colsArr; // already an object
    },

    _colOptions(profileData) {
        if (!profileData) return '';
        const cols = this._normalizeProfile(profileData);
        return Object.keys(cols).map(c => `<option value="${c}">${c}</option>`).join('');
    },

    _numericCols(profileData) {
        if (!profileData) return [];
        const cols = this._normalizeProfile(profileData);
        return Object.keys(cols).filter(c => {
            const info = cols[c];
            const dt = (info.dtype || info.type || '').toLowerCase();
            return dt.includes('int') || dt.includes('float') || dt.includes('num');
        });
    },

    _dateCols(profileData) {
        if (!profileData) return [];
        const cols = this._normalizeProfile(profileData);
        const nameRe = /date|time|timestamp|year|month|day|week|quarter|created|updated|modified/i;
        const isoRe = /^\d{4}[-/]\d{1,2}[-/]\d{1,2}/;
        return Object.keys(cols).filter(c => {
            const info = cols[c];
            const dt = (info.dtype || info.type || '').toLowerCase();
            if (info.type === 'datetime' || dt.includes('date') || dt.includes('time')) return true;
            if (dt.includes('int') || dt.includes('float') || dt.includes('num') || dt.includes('bool')) return false;
            if (nameRe.test(c)) return true;
            const samples = info.sample_values || [];
            if (samples.length && samples.every(v => v != null && isoRe.test(String(v)))) return true;
            return false;
        });
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 1: Column Profiles
       ════════════════════════════════════════════════════════════════ */
    async _renderProfiles(el) {
        const data = await API.profile(this._selectedTable);
        this._profileData = data;
        const cols = this._normalizeProfile(data);
        const colNames = Object.keys(cols);
        const profile = data.profile || data;

        // Summary row
        const summary = `<div class="flex flex-wrap gap-4 mb-4 text-sm">
            <span class="bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 px-3 py-1 rounded-full"><i class="fas fa-table mr-1"></i>${profile.row_count || 0} rows</span>
            <span class="bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 px-3 py-1 rounded-full"><i class="fas fa-columns mr-1"></i>${colNames.length} columns</span>
            <span class="bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 px-3 py-1 rounded-full"><i class="fas fa-check-circle mr-1"></i>${profile.duplicate_count || 0} duplicates</span>
            <span class="bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 px-3 py-1 rounded-full"><i class="fas fa-exclamation-circle mr-1"></i>${profile.missing_cells || 0} missing</span>
        </div>`;

        let html = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Column Profiles — ${App.escapeHtml(this._selectedTable)}</h3>
            ${summary}
            <div class="overflow-x-auto">
                <table class="data-table w-full text-sm">
                    <thead><tr>
                        <th>Column</th><th>Type</th><th>Count</th><th>Nulls</th><th>Unique</th>
                        <th>Mean</th><th>Std</th><th>Min</th><th>Median</th><th>Max</th>
                        <th>Histogram</th>
                    </tr></thead><tbody>`;

        colNames.forEach((col, idx) => {
            const c = cols[col];
            html += `<tr>
                <td class="font-medium">${App.escapeHtml(col)}</td>
                <td><span class="px-2 py-0.5 rounded text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300">${App.escapeHtml(c.dtype || c.type || '-')}</span></td>
                <td>${c.count != null ? c.count : '-'}</td>
                <td>${c.null_count != null ? c.null_count : (c.missing != null ? c.missing : 0)}</td>
                <td>${c.unique_count != null ? c.unique_count : '-'}</td>
                <td>${c.mean != null ? Number(c.mean).toFixed(2) : '-'}</td>
                <td>${c.std != null ? Number(c.std).toFixed(2) : '-'}</td>
                <td>${c.min != null ? c.min : '-'}</td>
                <td>${c.median != null ? c.median : '-'}</td>
                <td>${c.max != null ? c.max : '-'}</td>
                <td><canvas id="prof-hist-${idx}" width="100" height="40"></canvas></td>
            </tr>`;
        });

        html += '</tbody></table></div>';

        // Data dictionary editor
        html += `
        <div class="mt-6 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
            <h4 class="font-semibold mb-3"><i class="fas fa-book mr-1"></i>Data Dictionary</h4>
            <div class="space-y-2" id="dict-entries">
                ${colNames.map(col => `
                <div class="flex items-center gap-3">
                    <span class="font-medium text-sm min-w-[120px]">${App.escapeHtml(col)}</span>
                    <input type="text" placeholder="Description..." class="flex-1 border rounded p-1 text-sm dark:bg-gray-700 dark:border-gray-600"
                        value="${App.escapeHtml(cols[col].description || '')}"
                        data-col="${col}" />
                    <button class="text-xs text-primary-500 hover:underline" onclick="AnalysisPage._saveDictEntry('${App.escapeHtml(col)}', this)">Save</button>
                </div>`).join('')}
            </div>
        </div>
        </div>`;

        el.innerHTML = html;

        // Render mini histograms
        colNames.forEach((col, idx) => {
            const c = cols[col];
            const hist = c.histogram || c.hist;
            if (hist && hist.bins && hist.counts) {
                Charts.bar('prof-hist-' + idx, hist.bins.map(b => String(b)), hist.counts, {
                    plugins: { legend: { display: false } },
                    scales: { x: { display: false }, y: { display: false } },
                });
            }
        });
    },

    async _saveDictEntry(col, btn) {
        const input = btn.previousElementSibling;
        try {
            await API.saveMetadata(this._selectedTable, col, input.value, []);
            App.toast('Description saved', 'success');
        } catch (e) { App.toast('Save failed', 'error'); }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 2: Correlations
       ════════════════════════════════════════════════════════════════ */
    async _renderCorrelations(el) {
        const data = await API.correlation(this._selectedTable);
        const matrix = data.matrix || data.correlation_matrix || [];
        const labels = data.columns || data.labels || Object.keys(matrix);
        const pairs = data.pairs || [];

        if (!matrix.length || !labels.length) {
            el.innerHTML = '<div class="text-center py-12 text-gray-400">Not enough numeric columns for correlation analysis.</div>';
            return;
        }

        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Correlation Matrix</h3>
            <div class="flex justify-center">
                <canvas id="corr-heatmap" width="600" height="500"></canvas>
            </div>
            <div id="corr-pairs" class="mt-4"></div>
        </div>`;

        Charts.heatmap('corr-heatmap', matrix, {
            xLabels: labels.map(l => String(l).substring(0, 12)),
            yLabels: labels.map(l => String(l).substring(0, 12)),
            title: 'Correlation Matrix',
        });

        // Use pairs from backend if available, otherwise compute
        let displayPairs = pairs.length > 0 ? pairs : [];
        if (displayPairs.length === 0) {
            for (let i = 0; i < labels.length; i++) {
                for (let j = i + 1; j < labels.length; j++) {
                    if (matrix[i] && matrix[i][j] != null) {
                        displayPairs.push({ col1: labels[i], col2: labels[j], correlation: matrix[i][j] });
                    }
                }
            }
            displayPairs.sort((a, b) => Math.abs(b.correlation) - Math.abs(a.correlation));
        }

        const pairsHtml = displayPairs.slice(0, 10).map(p => {
            const val = p.correlation != null ? p.correlation : p.value || 0;
            const a = p.col1 || p.a || '';
            const b = p.col2 || p.b || '';
            const color = Math.abs(val) > 0.7 ? 'text-red-500' : Math.abs(val) > 0.4 ? 'text-yellow-500' : 'text-green-500';
            const strength = p.strength || (Math.abs(val) > 0.7 ? 'strong' : Math.abs(val) > 0.4 ? 'moderate' : 'weak');
            return `<div class="flex justify-between items-center py-1 border-b dark:border-gray-700">
                <span class="text-sm">${App.escapeHtml(a)} &harr; ${App.escapeHtml(b)}</span>
                <span class="font-mono font-bold ${color}">${Number(val).toFixed(3)} <span class="text-xs font-normal text-gray-400">${strength}</span></span>
            </div>`;
        }).join('');

        document.getElementById('corr-pairs').innerHTML = `
            <h4 class="font-semibold mb-2">Strongest Correlations</h4>
            <div class="bg-gray-50 dark:bg-gray-800 rounded-lg p-3">${pairsHtml || '<p class="text-gray-400 text-center">No significant correlations</p>'}</div>`;
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 3: Distributions
       ════════════════════════════════════════════════════════════════ */
    async _renderDistributions(el) {
        const profile = this._profileData || await API.profile(this._selectedTable);
        if (!this._profileData) this._profileData = profile;
        const numCols = this._numericCols(profile);

        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Distributions</h3>
            <div class="flex items-center gap-3">
                <label class="text-sm font-medium">Column:</label>
                <select id="dist-col" class="border rounded p-1 dark:bg-gray-700 dark:border-gray-600">
                    ${numCols.map(c => `<option value="${c}">${c}</option>`).join('')}
                </select>
                <button onclick="AnalysisPage._loadDistribution()" class="bg-primary-500 hover:bg-primary-600 text-white px-4 py-1 rounded text-sm">View</button>
            </div>
            <canvas id="dist-chart" width="600" height="300"></canvas>
            <div id="dist-normality" class="text-sm text-gray-500 mt-2"></div>
        </div>`;

        if (numCols.length > 0) this._loadDistribution();
    },

    async _loadDistribution() {
        const col = document.getElementById('dist-col').value;
        if (!col) return;
        const data = await API.normality(this._selectedTable, col, 'shapiro');
        const profile = this._profileData || await API.profile(this._selectedTable);
        const colData = this._normalizeProfile(profile)[col];
        const hist = colData?.histogram || colData?.hist;

        if (hist && hist.bins && hist.counts) {
            Charts.bar('dist-chart', hist.bins.map(b => String(b)), hist.counts, {
                plugins: { title: { display: true, text: `Distribution of ${col}` }, legend: { display: false } },
            });
        }

        if (data) {
            document.getElementById('dist-normality').innerHTML = `
                <span class="font-medium">Normality Test (${data.method || 'Shapiro-Wilk'}):</span>
                Statistic: ${data.statistic?.toFixed(4) || '-'}, p-value: ${data.p_value?.toFixed(4) || data.pvalue?.toFixed(4) || '-'}
                — <span class="${(data.p_value || data.pvalue) > 0.05 ? 'text-green-500' : 'text-red-500'} font-medium">
                    ${(data.p_value || data.pvalue) > 0.05 ? 'Likely normal' : 'Not normal'}
                </span>`;
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 4: Outliers
       ════════════════════════════════════════════════════════════════ */
    async _renderOutliers(el) {
        const outlierData = await API.outliers(this._selectedTable);

        let html = `<div class="space-y-4">
            <h3 class="text-lg font-semibold">Outlier Detection (IQR Method)</h3>
            <div class="overflow-x-auto"><table class="data-table w-full text-sm">
                <thead><tr><th>Column</th><th>Lower Bound</th><th>Upper Bound</th><th>Outlier Count</th><th>% of Data</th></tr></thead>
                <tbody>`;

        const entries = outlierData.outliers || outlierData.columns || outlierData;
        const table = Array.isArray(entries) ? entries :
            Object.entries(entries).map(([col, info]) => ({ column: col, ...info }));

        table.forEach(row => {
            html += `<tr>
                <td class="font-medium">${App.escapeHtml(row.column || row.col || '')}</td>
                <td>${row.lower_bound != null ? Number(row.lower_bound).toFixed(2) : '-'}</td>
                <td>${row.upper_bound != null ? Number(row.upper_bound).toFixed(2) : '-'}</td>
                <td class="font-bold text-red-500">${row.outlier_count != null ? row.outlier_count : (row.count || '-')}</td>
                <td>${row.percentage != null ? row.percentage.toFixed(1) + '%' : '-'}</td>
            </tr>`;
        });

        html += '</tbody></table></div>';

        // Suggestions
        try {
            const suggestions = await API.outlierSuggestions(this._selectedTable);
            const sug = suggestions.suggestions || suggestions;
            if (sug && (Array.isArray(sug) ? sug.length > 0 : Object.keys(sug).length > 0)) {
                html += `<div class="mt-4 p-4 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
                    <h4 class="font-semibold mb-2"><i class="fas fa-magic mr-1 text-yellow-500"></i>Handling Suggestions</h4>`;
                const sugList = Array.isArray(sug) ? sug : Object.values(sug);
                const badgeColor = {
                    transform: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
                    cap_winsorize: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
                    keep: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
                    remove: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
                    investigate: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                };
                sugList.forEach(col => {
                    if (typeof col === 'string') {
                        html += `<div class="text-sm mt-2">${App.escapeHtml(col)}</div>`;
                        return;
                    }
                    const colName = col.column || col.col || '';
                    const colSugs = Array.isArray(col.suggestions) ? col.suggestions : [];
                    html += `<div class="mt-2">
                        <div class="text-sm font-semibold">${App.escapeHtml(colName)} <span class="text-xs font-normal text-gray-500 dark:text-gray-400">${col.outlier_count != null ? col.outlier_count + ' outlier(s), ' + col.outlier_pct + '%' : ''}</span></div>
                        <ul class="space-y-1 mt-1">`;
                    colSugs.forEach(s => {
                        if (typeof s === 'string') {
                            html += `<li class="text-sm">${App.escapeHtml(s)}</li>`;
                            return;
                        }
                        const action = s.action || 'note';
                        const reason = s.reason || '';
                        const impact = s.impact || '';
                        html += `<li class="flex items-start gap-2 py-0.5">
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase whitespace-nowrap ${badgeColor[action] || 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300'}">${App.escapeHtml(action)}</span>
                            <span class="text-sm">${App.escapeHtml(reason)}</span>
                            <span class="text-xs text-gray-500 dark:text-gray-400">${App.escapeHtml(impact)}</span>
                        </li>`;
                    });
                    html += '</ul></div>';
                });
                html += '</div>';
            }
        } catch (e) { /* suggestions optional */ }

        html += '</div>';
        el.innerHTML = html;
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 5: Forecast
       ════════════════════════════════════════════════════════════════ */
    async _renderForecast(el) {
        const profile = this._profileData || await API.profile(this._selectedTable);
        if (!this._profileData) this._profileData = profile;
        const cols = Object.keys(this._normalizeProfile(profile));
        const dateCols = this._dateCols(profile);
        const dateOptions = (dateCols.length ? dateCols : cols).map(c => `<option value="${c}">${c}</option>`).join('');

        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Forecast</h3>
            <div class="flex items-center gap-3 flex-wrap">
                <div>
                    <label class="text-sm font-medium">Date Column:</label>
                    <select id="fc-date" class="border rounded p-1 dark:bg-gray-700 dark:border-gray-600 ml-1">
                        ${dateOptions}
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Value Column:</label>
                    <select id="fc-val" class="border rounded p-1 dark:bg-gray-700 dark:border-gray-600 ml-1">
                        ${this._numericCols(profile).map(c => `<option value="${c}">${c}</option>`).join('')}
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Periods:</label>
                    <input id="fc-periods" type="number" value="12" min="1" max="120"
                        class="w-20 border rounded p-1 dark:bg-gray-700 dark:border-gray-600 ml-1">
                </div>
                <button onclick="AnalysisPage._runForecast()" class="bg-primary-500 hover:bg-primary-600 text-white px-4 py-1 rounded text-sm mt-4">Run Forecast</button>
            </div>
            <canvas id="fc-chart" width="600" height="300"></canvas>
            <div id="fc-info" class="text-sm text-gray-500"></div>
        </div>`;
    },

    async _runForecast() {
        const dateCol = document.getElementById('fc-date').value;
        const valCol = document.getElementById('fc-val').value;
        const periods = parseInt(document.getElementById('fc-periods').value) || 12;
        if (!dateCol || !valCol) return App.toast('Select both columns', 'error');

        try {
            const data = await API.forecast(this._selectedTable, dateCol, valCol, periods);
            if (!data.success) throw new Error(data.error || 'Forecast failed');

            // Backend returns: historical_dates, historical_values, forecast_dates, forecast_values, upper_bound, lower_bound
            const histDates = data.historical_dates || data.dates || [];
            const histValues = data.historical_values || data.actual || [];
            const fcDates = data.forecast_dates || [];
            const fcValues = data.forecast_values || [];
            const lower = data.lower_bound || [];
            const upper = data.upper_bound || [];

            const allLabels = [...histDates, ...fcDates].map(d => String(d).substring(0, 10));
            const histData = [...histValues, ...new Array(fcDates.length).fill(null)];
            const fcData = [...new Array(histDates.length - 1).fill(null), histValues[histValues.length - 1], ...fcValues];

            const datasets = [{ label: 'Actual', data: histData, borderColor: '#3b82f6', backgroundColor: 'transparent' }];
            datasets.push({ label: 'Forecast', data: fcData, borderColor: '#f59e0b', backgroundColor: 'transparent', borderDash: [5, 5] });
            if (lower.length || upper.length) {
                const lowerData = [...new Array(histDates.length).fill(null), ...lower];
                const upperData = [...new Array(histDates.length).fill(null), ...upper];
                datasets.push({ label: 'Lower CI', data: lowerData, borderColor: 'transparent', backgroundColor: 'rgba(245,158,11,0.1)', fill: '+1', pointRadius: 0 });
                datasets.push({ label: 'Upper CI', data: upperData, borderColor: 'transparent', backgroundColor: 'rgba(245,158,11,0.1)', fill: false, pointRadius: 0 });
            }

            Charts.line('fc-chart', allLabels, datasets, { scales: { y: { beginAtZero: false } } });
            const dir = data.trend_direction || '';
            const slope = data.trend_slope != null ? ` (slope: ${data.trend_slope})` : '';
            document.getElementById('fc-info').innerHTML = `<span class="font-medium">${fcValues.length} periods ahead</span> — Trend: ${dir}${slope}`;
        } catch (e) {
            App.toast('Forecast failed: ' + (e.message || ''), 'error');
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 6: Segmentation
       ════════════════════════════════════════════════════════════════ */
    async _renderSegmentation(el) {
        const profile = this._profileData || await API.profile(this._selectedTable);
        if (!this._profileData) this._profileData = profile;
        const cols = Object.keys(this._normalizeProfile(profile));
        const numCols = this._numericCols(profile);

        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Segmentation</h3>
            <div class="flex items-center gap-3 flex-wrap">
                <div>
                    <label class="text-sm font-medium">Segment By:</label>
                    <select id="seg-col" class="border rounded p-1 dark:bg-gray-700 dark:border-gray-600 ml-1">
                        ${cols.map(c => `<option value="${c}">${c}</option>`).join('')}
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Metric:</label>
                    <select id="seg-metric" class="border rounded p-1 dark:bg-gray-700 dark:border-gray-600 ml-1">
                        <option value="">Count</option>
                        ${numCols.map(c => `<option value="${c}">${c}</option>`).join('')}
                    </select>
                </div>
                <button onclick="AnalysisPage._runSegmentation()" class="bg-primary-500 hover:bg-primary-600 text-white px-4 py-1 rounded text-sm mt-4">Segment</button>
            </div>
            <canvas id="seg-chart" width="700" height="350"></canvas>
        </div>`;
    },

    async _runSegmentation() {
        const segCol = document.getElementById('seg-col').value;
        const metricCol = document.getElementById('seg-metric').value || undefined;
        try {
            const data = await API.segmentation(this._selectedTable, segCol, metricCol);
            if (!data.success) throw new Error(data.error || 'Segmentation failed');

            // Backend returns: segments: [{segment, count, total, mean, ...}]
            const segments = data.segments || [];
            if (segments.length === 0) {
                App.toast('No segments found', 'info');
                return;
            }
            const labels = segments.map(s => s.segment || s.group || '');
            const values = segments.map(s => s.total || s.mean || s.count || 0);

            if (labels.length <= 8) {
                Charts.pie('seg-chart', labels, values, {
                    plugins: { title: { display: true, text: `Segmentation by ${segCol} (${metricCol || 'count'})` } },
                });
            } else {
                Charts.bar('seg-chart', labels, values, {
                    plugins: { title: { display: true, text: `Segmentation by ${segCol}` } },
                });
            }
        } catch (e) {
            App.toast('Segmentation failed', 'error');
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 7: Custom KPI
       ════════════════════════════════════════════════════════════════ */
    _renderKPI(el) {
        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Custom KPI — SQL Query</h3>
            <div>
                <label class="text-sm font-medium">Enter SQL:</label>
                <textarea id="kpi-sql" rows="5" class="w-full border rounded-lg p-3 font-mono text-sm dark:bg-gray-800 dark:border-gray-600 mt-1"
                    placeholder="SELECT column, COUNT(*) as cnt FROM ${App.escapeHtml(this._selectedTable || 'table')} GROUP BY column ORDER BY cnt DESC"></textarea>
            </div>
            <button onclick="AnalysisPage._runKPI()" class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-2 rounded-lg text-sm">
                <i class="fas fa-play mr-1"></i>Execute
            </button>
            <div id="kpi-results"></div>
        </div>`;
    },

    async _runKPI() {
        const sql = document.getElementById('kpi-sql').value.trim();
        if (!sql) return App.toast('Enter a SQL query', 'error');
        try {
            const result = await API.sql(sql);
            const data = result.data || result.rows || result;
            const el = document.getElementById('kpi-results');
            if (Array.isArray(data) && data.length > 0) {
                DataTable.render('kpi-results', data, { maxRows: 200 });
            } else {
                el.innerHTML = `<div class="bg-green-50 dark:bg-green-900/20 p-3 rounded-lg text-sm">
                    <i class="fas fa-check-circle text-green-500 mr-1"></i>Query executed successfully.
                    ${result.row_count !== undefined ? `Rows affected: ${result.row_count}` : ''}
                </div>`;
            }
        } catch (e) {
            App.toast('Query failed: ' + (e.message || ''), 'error');
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 8: Data Merge
       ════════════════════════════════════════════════════════════════ */
    _renderMerge(el) {
        const tables = App.state.tables || {};
        const tableNames = Array.isArray(tables) ? tables :
            (typeof tables === 'object' && tables !== null) ? Object.keys(tables) : [];

        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Data Merge</h3>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <label class="text-sm font-medium">Left Table:</label>
                    <select id="mg-left" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1"
                        onchange="AnalysisPage._updateMergeKeys('left')">
                        ${tableNames.map(t => `<option value="${t}" ${t === this._selectedTable ? 'selected' : ''}>${t}</option>`).join('')}
                    </select>
                    <label class="text-sm font-medium mt-2 block">Join Key (Left):</label>
                    <select id="mg-left-key" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                        <option value="">Select...</option>
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Right Table:</label>
                    <select id="mg-right" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1"
                        onchange="AnalysisPage._updateMergeKeys('right')">
                        ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                    </select>
                    <label class="text-sm font-medium mt-2 block">Join Key (Right):</label>
                    <select id="mg-right-key" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                        <option value="">Select...</option>
                    </select>
                </div>
            </div>
            <div>
                <label class="text-sm font-medium">Join Type:</label>
                <select id="mg-type" class="border rounded p-2 dark:bg-gray-700 dark:border-gray-600 ml-2">
                    <option value="inner">Inner Join</option>
                    <option value="left">Left Join</option>
                    <option value="right">Right Join</option>
                    <option value="outer">Outer Join</option>
                </select>
            </div>
            <button onclick="AnalysisPage._runMerge()" class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-2 rounded-lg text-sm">
                <i class="fas fa-code-branch mr-1"></i>Merge & Preview
            </button>
            <div id="mg-preview"></div>
        </div>`;

        // Auto-load keys for left table
        if (this._selectedTable) this._updateMergeKeys('left');
    },

    async _updateMergeKeys(side) {
        const table = document.getElementById(side === 'left' ? 'mg-left' : 'mg-right').value;
        const keySel = document.getElementById(side === 'left' ? 'mg-left-key' : 'mg-right-key');
        if (!table) { keySel.innerHTML = '<option value="">Select...</option>'; return; }
        try {
            const profile = await API.profile(table);
            const cols = Object.keys(this._normalizeProfile(profile));
            keySel.innerHTML = '<option value="">Select...</option>' + cols.map(c => `<option value="${c}">${c}</option>`).join('');
        } catch (e) {
            keySel.innerHTML = '<option value="">Error loading</option>';
        }
    },

    async _runMerge() {
        const left = document.getElementById('mg-left').value;
        const right = document.getElementById('mg-right').value;
        const leftKey = document.getElementById('mg-left-key').value;
        const rightKey = document.getElementById('mg-right-key').value;
        const joinType = document.getElementById('mg-type').value;

        if (!left || !right || !leftKey || !rightKey) return App.toast('Select both tables and keys', 'error');

        try {
            const result = await API.merge(left, right, leftKey, rightKey, joinType);
            const data = result.data || result.rows || result;
            const el = document.getElementById('mg-preview');

            if (Array.isArray(data) && data.length > 0) {
                el.innerHTML = `<div class="text-sm text-gray-500 mb-2">Preview: ${data.length} rows</div>`;
                const wrapper = document.createElement('div');
                wrapper.id = 'mg-preview-table';
                el.appendChild(wrapper);
                DataTable.render('mg-preview-table', data, { maxRows: 50 });
            } else {
                el.innerHTML = '<p class="text-yellow-500">Merge returned no results.</p>';
            }
        } catch (e) {
            App.toast('Merge failed: ' + (e.message || ''), 'error');
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 9: Chart Builder
       ════════════════════════════════════════════════════════════════ */
    _renderChartBuilder(el) {
        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Chart Builder</h3>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                    <label class="text-sm font-medium">Chart Type:</label>
                    <select id="cb-type" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                        <option value="bar">Bar</option>
                        <option value="line">Line</option>
                        <option value="pie">Pie</option>
                        <option value="doughnut">Doughnut</option>
                        <option value="scatter">Scatter</option>
                        <option value="radar">Radar</option>
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">X Column:</label>
                    <select id="cb-x" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                        <option value="">Select...</option>
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Y Column:</label>
                    <select id="cb-y" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                        <option value="">Select...</option>
                    </select>
                </div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                    <label class="text-sm font-medium">Color By (optional):</label>
                    <select id="cb-color" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                        <option value="">None</option>
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Title:</label>
                    <input id="cb-title" type="text" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1" placeholder="Chart Title">
                </div>
                <div>
                    <label class="text-sm font-medium">Height (px):</label>
                    <input id="cb-height" type="number" value="350" min="150" max="800"
                        class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                </div>
            </div>
            <button onclick="AnalysisPage._buildChart()" class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-2 rounded-lg text-sm">
                <i class="fas fa-palette mr-1"></i>Build Chart
            </button>
            <div class="flex justify-center">
                <canvas id="cb-canvas" width="700" height="350" class="max-w-full"></canvas>
            </div>
            <div id="cb-export-bar" class="flex items-center justify-center gap-2 mt-2">
                <button onclick="AnalysisPage._exportChartAsPng()" class="text-xs bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 px-2.5 py-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"><i class="fas fa-image mr-1"></i>Save PNG</button>
                <button onclick="AnalysisPage._exportChartData()" class="text-xs bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 px-2.5 py-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"><i class="fas fa-file-csv mr-1"></i>Download CSV</button>
                <button onclick="AnalysisPage._exportChartPptx()" class="text-xs bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 px-2.5 py-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"><i class="fas fa-file-powerpoint mr-1"></i>Export PPTX</button>
            </div>
        </div>`;

        // Populate columns from profile
        this._populateChartCols();
    },

    async _populateChartCols() {
        try {
            const profile = this._profileData || await API.profile(this._selectedTable);
            if (!this._profileData) this._profileData = profile;
            const cols = Object.keys(this._normalizeProfile(profile));
            ['cb-x', 'cb-y', 'cb-color'].forEach(id => {
                const sel = document.getElementById(id);
                if (sel) {
                    sel.innerHTML = '<option value="">Select...</option>' +
                        cols.map(c => `<option value="${c}">${c}</option>`).join('');
                }
            });
        } catch (e) { /* ignore */ }
    },

    async _buildChart() {
        const type = document.getElementById('cb-type').value;
        const xCol = document.getElementById('cb-x').value;
        const yCol = document.getElementById('cb-y').value;
        const colorCol = document.getElementById('cb-color').value;
        const title = document.getElementById('cb-title').value;
        const height = parseInt(document.getElementById('cb-height').value) || 350;

        if (!xCol || !yCol) return App.toast('Select X and Y columns', 'error');

        try {
            const result = await API.tableSample(this._selectedTable, 2000);
            const data = result.data || result.rows || result;
            if (!Array.isArray(data) || data.length === 0) return App.toast('No data available', 'error');

            const canvas = document.getElementById('cb-canvas');
            canvas.height = height;

            // Aggregate by X column
            const groups = {};
            data.forEach(row => {
                const key = String(row[xCol] || 'null');
                if (!groups[key]) groups[key] = [];
                groups[key].push(Number(row[yCol]) || 0);
            });

            const labels = Object.keys(groups);

            // Store chart data for CSV export
            this._chartData = labels.map(l => ({ [xCol]: l, [yCol]: groups[l].reduce((a, b) => a + b, 0) / groups[l].length, count: groups[l].length }));

            if (colorCol) {
                // Grouped by color
                const colorGroups = {};
                data.forEach(row => {
                    const g = String(row[colorCol] || 'default');
                    if (!colorGroups[g]) colorGroups[g] = {};
                    const key = String(row[xCol] || 'null');
                    if (!colorGroups[g][key]) colorGroups[g][key] = [];
                    colorGroups[g][key].push(Number(row[yCol]) || 0);
                });
                const series = Object.entries(colorGroups).map(([name, vals]) => ({
                    label: name,
                    data: labels.map(l => {
                        const arr = vals[l] || [];
                        return arr.reduce((s, v) => s + v, 0) / (arr.length || 1);
                    }),
                }));
                Charts.bar('cb-canvas', labels, series, { plugins: { title: { display: !!title, text: title } } });
            } else {
                const values = labels.map(l => {
                    const arr = groups[l];
                    return arr.reduce((s, v) => s + v, 0) / (arr.length || 1);
                });

                if (type === 'pie' || type === 'doughnut') {
                    Charts[type]('cb-canvas', labels, values, {
                        plugins: { title: { display: !!title, text: title } },
                    });
                } else if (type === 'scatter') {
                    const points = data.map(r => ({ x: Number(r[xCol]) || 0, y: Number(r[yCol]) || 0 }));
                    Charts.scatter('cb-canvas', points, {
                        plugins: { title: { display: !!title, text: title } },
                    });
                } else {
                    Charts[type]('cb-canvas', labels, values, {
                        plugins: { title: { display: !!title, text: title } },
                    });
                }
            }
        } catch (e) {
            App.toast('Chart build failed: ' + (e.message || ''), 'error');
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 10: Statistical Tests
       ════════════════════════════════════════════════════════════════ */
    _renderStats(el) {
        el.innerHTML = `
        <div class="space-y-6">
            <h3 class="text-lg font-semibold">Statistical Tests</h3>

            <!-- Normality Test -->
            <div class="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
                <h4 class="font-semibold mb-3">Normality Test</h4>
                <div class="flex items-center gap-3 flex-wrap">
                    <select id="st-norm-col" class="border rounded p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="">Select column...</option>
                    </select>
                    <select id="st-norm-method" class="border rounded p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="shapiro">Shapiro-Wilk</option>
                        <option value="ks">Kolmogorov-Smirnov</option>
                        <option value="dagostino">D'Agostino-Pearson</option>
                    </select>
                    <button onclick="AnalysisPage._runNormality()" class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded text-sm">Test</button>
                </div>
                <div id="st-norm-result" class="mt-3"></div>
            </div>

            <!-- Hypothesis Test -->
            <div class="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
                <h4 class="font-semibold mb-3">Hypothesis Test</h4>
                <div class="flex items-center gap-3 flex-wrap">
                    <select id="st-hyp-a" class="border rounded p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="">Column A...</option>
                    </select>
                    <select id="st-hyp-b" class="border rounded p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="">Column B...</option>
                    </select>
                    <select id="st-hyp-type" class="border rounded p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="auto">Auto</option>
                        <option value="t-test">T-Test</option>
                        <option value="mannwhitney">Mann-Whitney U</option>
                        <option value="anova">ANOVA</option>
                        <option value="chi2">Chi-Squared</option>
                    </select>
                    <button onclick="AnalysisPage._runHypothesis()" class="bg-purple-500 hover:bg-purple-600 text-white px-4 py-2 rounded text-sm">Test</button>
                </div>
                <div id="st-hyp-result" class="mt-3"></div>
            </div>

            <!-- Confidence Interval -->
            <div class="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
                <h4 class="font-semibold mb-3">Confidence Interval</h4>
                <div class="flex items-center gap-3 flex-wrap">
                    <select id="st-ci-col" class="border rounded p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="">Select column...</option>
                    </select>
                    <select id="st-ci-level" class="border rounded p-2 dark:bg-gray-700 dark:border-gray-600">
                        <option value="0.90">90%</option>
                        <option value="0.95" selected>95%</option>
                        <option value="0.99">99%</option>
                    </select>
                    <button onclick="AnalysisPage._runCI()" class="bg-green-500 hover:bg-green-600 text-white px-4 py-2 rounded text-sm">Compute</button>
                </div>
                <div id="st-ci-result" class="mt-3"></div>
            </div>
        </div>`;

        this._populateStatCols();
    },

    async _populateStatCols() {
        try {
            const profile = this._profileData || await API.profile(this._selectedTable);
            if (!this._profileData) this._profileData = profile;
            const cols = Object.keys(this._normalizeProfile(profile));
            const numCols = this._numericCols(profile);

            ['st-norm-col', 'st-ci-col'].forEach(id => {
                const sel = document.getElementById(id);
                if (sel) sel.innerHTML = '<option value="">Select column...</option>' + numCols.map(c => `<option value="${c}">${c}</option>`).join('');
            });
            ['st-hyp-a', 'st-hyp-b'].forEach(id => {
                const sel = document.getElementById(id);
                if (sel) sel.innerHTML = '<option value="">Select column...</option>' + numCols.map(c => `<option value="${c}">${c}</option>`).join('');
            });
        } catch (e) { /* ignore */ }
    },

    async _runNormality() {
        const col = document.getElementById('st-norm-col').value;
        const method = document.getElementById('st-norm-method').value;
        if (!col) return App.toast('Select a column', 'error');
        try {
            const data = await API.normality(this._selectedTable, col, method);
            const pVal = data.p_value || data.pvalue;
            document.getElementById('st-norm-result').innerHTML = `
                <div class="bg-white dark:bg-gray-700 p-3 rounded border dark:border-gray-600 text-sm">
                    <p><strong>Test:</strong> ${data.method || method}</p>
                    <p><strong>Statistic:</strong> ${data.statistic?.toFixed(4) || '-'}</p>
                    <p><strong>p-value:</strong> ${pVal?.toFixed(4) || '-'}</p>
                    <p class="mt-1 font-medium ${pVal > 0.05 ? 'text-green-500' : 'text-red-500'}">
                        ${pVal > 0.05 ? 'Data appears normally distributed (p > 0.05)' : 'Data is NOT normally distributed (p <= 0.05)'}
                    </p>
                </div>`;
        } catch (e) { App.toast('Normality test failed', 'error'); }
    },

    async _runHypothesis() {
        const colA = document.getElementById('st-hyp-a').value;
        const colB = document.getElementById('st-hyp-b').value;
        const testType = document.getElementById('st-hyp-type').value;
        if (!colA || !colB) return App.toast('Select both columns', 'error');
        try {
            const data = await API.hypothesis(this._selectedTable, colA, colB, testType);
            const pVal = data.p_value || data.pvalue;
            document.getElementById('st-hyp-result').innerHTML = `
                <div class="bg-white dark:bg-gray-700 p-3 rounded border dark:border-gray-600 text-sm">
                    <p><strong>Test:</strong> ${data.test_name || data.test || testType}</p>
                    <p><strong>Statistic:</strong> ${data.statistic?.toFixed(4) || '-'}</p>
                    <p><strong>p-value:</strong> ${pVal?.toFixed(4) || '-'}</p>
                    ${data.interpretation ? `<p class="mt-1"><strong>Interpretation:</strong> ${App.escapeHtml(data.interpretation)}</p>` : ''}
                    <p class="mt-1 font-medium ${pVal > 0.05 ? 'text-green-500' : 'text-red-500'}">
                        ${pVal > 0.05 ? 'No significant difference (p > 0.05)' : 'Significant difference found (p <= 0.05)'}
                    </p>
                </div>`;
        } catch (e) { App.toast('Hypothesis test failed', 'error'); }
    },

    async _runCI() {
        const col = document.getElementById('st-ci-col').value;
        const confidence = parseFloat(document.getElementById('st-ci-level').value);
        if (!col) return App.toast('Select a column', 'error');
        try {
            const data = await API.confidenceInterval(this._selectedTable, col, confidence);
            document.getElementById('st-ci-result').innerHTML = `
                <div class="bg-white dark:bg-gray-700 p-3 rounded border dark:border-gray-600 text-sm">
                    <p><strong>Confidence Level:</strong> ${(confidence * 100).toFixed(0)}%</p>
                    <p><strong>Mean:</strong> ${data.mean?.toFixed(4) || '-'}</p>
                    <p><strong>Lower Bound:</strong> ${data.lower?.toFixed(4) || data.ci_lower?.toFixed(4) || '-'}</p>
                    <p><strong>Upper Bound:</strong> ${data.upper?.toFixed(4) || data.ci_upper?.toFixed(4) || '-'}</p>
                    <p><strong>Margin of Error:</strong> ${data.margin_of_error?.toFixed(4) || data.moe?.toFixed(4) || '-'}</p>
                </div>`;
        } catch (e) { App.toast('CI computation failed', 'error'); }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 11: Time Series Decomposition
       ════════════════════════════════════════════════════════════════ */
    _renderTimeSeries(el) {
        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Time Series Decomposition</h3>
            <div class="flex items-center gap-3 flex-wrap">
                <div>
                    <label class="text-sm font-medium">Date Column:</label>
                    <select id="ts-date" class="border rounded p-1 dark:bg-gray-700 dark:border-gray-600 ml-1">
                        <option value="">Select...</option>
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Value Column:</label>
                    <select id="ts-val" class="border rounded p-1 dark:bg-gray-700 dark:border-gray-600 ml-1">
                        <option value="">Select...</option>
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Period:</label>
                    <input id="ts-period" type="number" value="12" min="2"
                        class="w-20 border rounded p-1 dark:bg-gray-700 dark:border-gray-600 ml-1">
                </div>
                <button onclick="AnalysisPage._runDecomposition()" class="bg-primary-500 hover:bg-primary-600 text-white px-4 py-1 rounded text-sm mt-4">
                    <i class="fas fa-wave-square mr-1"></i>Decompose
                </button>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div><h4 class="text-sm font-medium mb-1">Observed</h4><canvas id="ts-observed" width="300" height="150"></canvas></div>
                <div><h4 class="text-sm font-medium mb-1">Trend</h4><canvas id="ts-trend" width="300" height="150"></canvas></div>
                <div><h4 class="text-sm font-medium mb-1">Seasonal</h4><canvas id="ts-seasonal" width="300" height="150"></canvas></div>
                <div><h4 class="text-sm font-medium mb-1">Residual</h4><canvas id="ts-residual" width="300" height="150"></canvas></div>
            </div>
        </div>`;

        this._populateTimeSeriesCols();
    },

    async _populateTimeSeriesCols() {
        try {
            const profile = this._profileData || await API.profile(this._selectedTable);
            if (!this._profileData) this._profileData = profile;
            const cols = Object.keys(this._normalizeProfile(profile));
            const numCols = this._numericCols(profile);

            const dateSel = document.getElementById('ts-date');
            if (dateSel) dateSel.innerHTML = '<option value="">Select...</option>' + cols.map(c => `<option value="${c}">${c}</option>`).join('');

            const valSel = document.getElementById('ts-val');
            if (valSel) valSel.innerHTML = '<option value="">Select...</option>' + numCols.map(c => `<option value="${c}">${c}</option>`).join('');
        } catch (e) { /* ignore */ }
    },

    async _runDecomposition() {
        const dateCol = document.getElementById('ts-date').value;
        const valCol = document.getElementById('ts-val').value;
        const period = parseInt(document.getElementById('ts-period').value) || 12;
        if (!dateCol || !valCol) return App.toast('Select both columns', 'error');

        try {
            const data = await API.decomposition(this._selectedTable, dateCol, valCol, period);
            const labels = (data.observed || data.trend || []).map((_, i) => String(i));

            const panels = ['observed', 'trend', 'seasonal', 'residual'];
            panels.forEach(p => {
                if (data[p] && data[p].length) {
                    Charts.line('ts-' + p, labels, [{ label: p.charAt(0).toUpperCase() + p.slice(1), data: data[p] }], {
                        plugins: { legend: { display: false } },
                    });
                }
            });
        } catch (e) {
            App.toast('Decomposition failed: ' + (e.message || ''), 'error');
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 12: A/B Testing (Enhanced with Conversion, Sequential, Sample Size)
       ════════════════════════════════════════════════════════════════ */
    _renderABTest(el) {
        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">A/B Testing Suite</h3>

            <!-- Test Type Tabs -->
            <div class="flex gap-1 border-b dark:border-gray-700">
                <button onclick="AnalysisPage._showABTab('ttest')" id="ab-tab-ttest" class="px-3 py-2 text-xs font-medium border-b-2 border-primary-500 text-primary-600">Welch's t-test</button>
                <button onclick="AnalysisPage._showABTab('conversion')" id="ab-tab-conversion" class="px-3 py-2 text-xs font-medium border-b-2 border-transparent text-gray-500">Chi-Square Conversion</button>
                <button onclick="AnalysisPage._showABTab('sequential')" id="ab-tab-sequential" class="px-3 py-2 text-xs font-medium border-b-2 border-transparent text-gray-500">Sequential Test</button>
                <button onclick="AnalysisPage._showABTab('sample-size')" id="ab-tab-sample-size" class="px-3 py-2 text-xs font-medium border-b-2 border-transparent text-gray-500">Sample Size Calculator</button>
            </div>

            <!-- Common Fields -->
            <div id="ab-common-fields" class="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                    <label class="text-sm font-medium">Group Column:</label>
                    <select id="ab-group" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                        <option value="">Select...</option>
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Metric Column:</label>
                    <select id="ab-metric" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                        <option value="">Select...</option>
                    </select>
                </div>
                <div>
                    <label class="text-sm font-medium">Group A Value:</label>
                    <input id="ab-a" type="text" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1" placeholder="e.g. control">
                </div>
                <div>
                    <label class="text-sm font-medium">Group B Value:</label>
                    <input id="ab-b" type="text" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1" placeholder="e.g. treatment">
                </div>
            </div>

            <!-- T-test specific -->
            <div id="ab-ttest-section">
                <button onclick="AnalysisPage._runABTest()" class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-2 rounded-lg text-sm">
                    <i class="fas fa-balance-scale mr-1"></i>Run t-test
                </button>
            </div>

            <!-- Conversion test specific -->
            <div id="ab-conversion-section" class="hidden">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
                    <div>
                        <label class="text-sm font-medium">Conversion Column (binary 0/1):</label>
                        <select id="ab-conversion-col" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                            <option value="">Select...</option>
                        </select>
                    </div>
                </div>
                <button onclick="AnalysisPage._runConversionTest()" class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-2 rounded-lg text-sm">
                    <i class="fas fa-percentage mr-1"></i>Run Conversion Test
                </button>
            </div>

            <!-- Sequential test specific -->
            <div id="ab-sequential-section" class="hidden">
                <div class="mb-3">
                    <label class="text-sm font-medium">Alpha Spending Function:</label>
                    <select id="ab-spending" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1 max-w-xs">
                        <option value="obrien_fleming">O'Brien-Fleming</option>
                        <option value="pocock">Pocock</option>
                        <option value="simple">Simple (proportional)</option>
                    </select>
                </div>
                <button onclick="AnalysisPage._runSequentialTest()" class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-2 rounded-lg text-sm">
                    <i class="fas fa-chart-line mr-1"></i>Run Sequential Test
                </button>
            </div>

            <!-- Sample Size Calculator (no table needed) -->
            <div id="ab-sample-size-section" class="hidden">
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
                    <div>
                        <label class="text-sm font-medium">Baseline Conversion Rate:</label>
                        <input id="ss-baseline" type="number" step="0.01" min="0.01" max="0.99" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1" placeholder="0.10 (10%)" value="0.10">
                    </div>
                    <div>
                        <label class="text-sm font-medium">Minimum Detectable Effect (%):</label>
                        <input id="ss-mde" type="number" step="1" min="1" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1" placeholder="10" value="10">
                    </div>
                    <div>
                        <label class="text-sm font-medium">Power:</label>
                        <select id="ss-power" class="w-full border rounded p-2 dark:bg-gray-700 dark:border-gray-600 mt-1">
                            <option value="0.80">80%</option>
                            <option value="0.90">90%</option>
                            <option value="0.95">95%</option>
                        </select>
                    </div>
                </div>
                <button onclick="AnalysisPage._runSampleSize()" class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-2 rounded-lg text-sm">
                    <i class="fas fa-calculator mr-1"></i>Calculate Sample Size
                </button>
            </div>

            <div id="ab-result"></div>
        </div>`;

        this._populateABCols();
    },

    async _populateABCols() {
        try {
            const profile = this._profileData || await API.profile(this._selectedTable);
            if (!this._profileData) this._profileData = profile;
            const cols = Object.keys(this._normalizeProfile(profile));
            const numCols = this._numericCols(profile);

            const groupSel = document.getElementById('ab-group');
            if (groupSel) groupSel.innerHTML = '<option value="">Select...</option>' + cols.map(c => `<option value="${c}">${c}</option>`).join('');

            const metricSel = document.getElementById('ab-metric');
            if (metricSel) metricSel.innerHTML = '<option value="">Select...</option>' + numCols.map(c => `<option value="${c}">${c}</option>`).join('');

            // Also populate conversion column selector (binary columns)
            const convSel = document.getElementById('ab-conversion-col');
            if (convSel) convSel.innerHTML = '<option value="">Select...</option>' + numCols.map(c => `<option value="${c}">${c}</option>`).join('');
        } catch (e) { /* ignore */ }
    },

    _showABTab(tab) {
        // Hide all specific sections
        ['ab-ttest-section', 'ab-conversion-section', 'ab-sequential-section', 'ab-sample-size-section', 'ab-common-fields'].forEach(id => {
            document.getElementById(id)?.classList.add('hidden');
        });
        // Show relevant sections
        if (tab === 'sample-size') {
            document.getElementById('ab-sample-size-section')?.classList.remove('hidden');
        } else {
            document.getElementById('ab-common-fields')?.classList.remove('hidden');
            if (tab === 'ttest') document.getElementById('ab-ttest-section')?.classList.remove('hidden');
            else if (tab === 'conversion') document.getElementById('ab-conversion-section')?.classList.remove('hidden');
            else if (tab === 'sequential') document.getElementById('ab-sequential-section')?.classList.remove('hidden');
        }
        // Update tab styling
        ['ttest', 'conversion', 'sequential', 'sample-size'].forEach(t => {
            const el = document.getElementById('ab-tab-' + t);
            if (!el) return;
            if (tab === t) {
                el.className = el.className.replace('border-transparent', 'border-primary-500').replace('text-gray-500', 'text-primary-600');
            } else {
                el.className = el.className.replace('border-primary-500', 'border-transparent').replace('text-primary-600', 'text-gray-500');
            }
        });
    },

    async _runABTest() {
        const groupCol = document.getElementById('ab-group').value;
        const metricCol = document.getElementById('ab-metric').value;
        const groupA = document.getElementById('ab-a').value;
        const groupB = document.getElementById('ab-b').value;
        if (!groupCol || !metricCol || !groupA || !groupB) return App.toast('Fill in all fields', 'error');

        try {
            const data = await API.abTest(this._selectedTable, groupCol, metricCol, groupA, groupB);
            const el = document.getElementById('ab-result');
            el.innerHTML = `
                <div class="bg-white dark:bg-gray-700 p-4 rounded-lg border dark:border-gray-600">
                    <h4 class="font-semibold mb-2">A/B Test Results</h4>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                        <div><span class="text-gray-500">Test:</span><br><strong>${data.test_name || data.test || '-'}</strong></div>
                        <div><span class="text-gray-500">Statistic:</span><br><strong>${data.statistic?.toFixed(4) || '-'}</strong></div>
                        <div><span class="text-gray-500">p-value:</span><br><strong>${(data.p_value || data.pvalue || 0).toFixed(4)}</strong></div>
                        <div><span class="text-gray-500">Significant:</span><br>
                            <strong class="${(data.p_value || data.pvalue || 1) < 0.05 ? 'text-green-500' : 'text-gray-500'}">
                                ${(data.p_value || data.pvalue || 1) < 0.05 ? 'Yes' : 'No'}
                            </strong>
                        </div>
                    </div>
                    ${data.group_a_stats ? `<div class="mt-3 grid grid-cols-2 gap-4 text-sm">
                        <div class="p-2 bg-blue-50 dark:bg-blue-900/20 rounded">
                            <strong>Group A (${App.escapeHtml(groupA)}):</strong> n=${data.group_a_stats.count || '-'}, mean=${data.group_a_stats.mean?.toFixed(2) || '-'}
                        </div>
                        <div class="p-2 bg-purple-50 dark:bg-purple-900/20 rounded">
                            <strong>Group B (${App.escapeHtml(groupB)}):</strong> n=${data.group_b_stats.count || '-'}, mean=${data.group_b_stats.mean?.toFixed(2) || '-'}
                        </div>
                    </div>` : ''}
                    ${data.interpretation ? `<p class="mt-3 text-sm">${App.escapeHtml(data.interpretation)}</p>` : ''}
                </div>`;
        } catch (e) { App.toast('A/B test failed', 'error'); }
    },

    async _runConversionTest() {
        const groupCol = document.getElementById('ab-group')?.value;
        const convCol = document.getElementById('ab-conversion-col')?.value;
        const groupA = document.getElementById('ab-a')?.value;
        const groupB = document.getElementById('ab-b')?.value;
        if (!groupCol || !convCol || !groupA || !groupB) return App.toast('Fill in all fields', 'error');

        try {
            const data = await API.conversionTest(this._selectedTable, groupCol, convCol, groupA, groupB);
            const el = document.getElementById('ab-result');
            if (!data.success) { el.innerHTML = `<div class="text-red-500 text-sm">${App.escapeHtml(data.error)}</div>`; return; }
            el.innerHTML = `
            <div class="bg-white dark:bg-gray-700 p-4 rounded-lg border dark:border-gray-600 space-y-3">
                <h4 class="font-semibold">Chi-Square Conversion Test</h4>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                    <div><span class="text-gray-500">Chi²:</span><br><strong>${data.chi2_statistic?.toFixed(4)}</strong></div>
                    <div><span class="text-gray-500">p-value:</span><br><strong class="${data.significant ? 'text-green-500' : 'text-gray-500'}">${data.p_value?.toFixed(4)}</strong></div>
                    <div><span class="text-gray-500">Odds Ratio:</span><br><strong>${data.odds_ratio?.toFixed(3)}</strong></div>
                    <div><span class="text-gray-500">Lift:</span><br><strong class="${data.lift_pct > 0 ? 'text-green-600' : 'text-red-600'}">${data.lift_pct > 0 ? '+' : ''}${data.lift_pct?.toFixed(1)}%</strong></div>
                </div>
                <div class="grid grid-cols-2 gap-3 text-sm">
                    <div class="p-2 bg-blue-50 dark:bg-blue-900/20 rounded">
                        <strong>${App.escapeHtml(groupA)}:</strong> ${data.group_a?.conversions}/${data.group_a?.total} = ${(data.group_a?.rate * 100)?.toFixed(1)}%
                    </div>
                    <div class="p-2 bg-purple-50 dark:bg-purple-900/20 rounded">
                        <strong>${App.escapeHtml(groupB)}:</strong> ${data.group_b?.conversions}/${data.group_b?.total} = ${(data.group_b?.rate * 100)?.toFixed(1)}%
                    </div>
                </div>
                <div class="text-sm text-gray-500">95% CI for diff: [${(data.ci_lower * 100)?.toFixed(2)}%, ${(data.ci_upper * 100)?.toFixed(2)}%]</div>
                <p class="text-sm">${App.escapeHtml(data.interpretation || '')}</p>
            </div>`;
        } catch (e) { App.toast('Conversion test failed', 'error'); }
    },

    async _runSequentialTest() {
        const groupCol = document.getElementById('ab-group')?.value;
        const metricCol = document.getElementById('ab-metric')?.value;
        const groupA = document.getElementById('ab-a')?.value;
        const groupB = document.getElementById('ab-b')?.value;
        const spending = document.getElementById('ab-spending')?.value || 'obrien_fleming';
        if (!groupCol || !metricCol || !groupA || !groupB) return App.toast('Fill in all fields', 'error');

        try {
            const data = await API.sequentialTest(this._selectedTable, groupCol, metricCol, groupA, groupB, 0.05, spending);
            const el = document.getElementById('ab-result');
            if (!data.success) { el.innerHTML = `<div class="text-red-500 text-sm">${App.escapeHtml(data.error)}</div>`; return; }

            const points = data.analysis_points || [];
            el.innerHTML = `
            <div class="bg-white dark:bg-gray-700 p-4 rounded-lg border dark:border-gray-600 space-y-3">
                <h4 class="font-semibold">Sequential Analysis — ${data.spending_function}</h4>
                <div class="text-sm text-gray-500">Total n: ${data.total_n} | Any significant: ${data.any_significant ? '<span class="text-green-500 font-semibold">Yes</span>' : '<span class="text-gray-400">No</span>'}</div>
                ${points.length > 0 ? `
                <div class="overflow-x-auto">
                    <table class="w-full text-xs border-collapse">
                        <thead><tr class="bg-gray-100 dark:bg-gray-700/50">
                            <th class="px-2 py-1.5 text-left">N</th>
                            <th class="px-2 py-1.5 text-right">Mean A</th>
                            <th class="px-2 py-1.5 text-right">Mean B</th>
                            <th class="px-2 py-1.5 text-right">t-stat</th>
                            <th class="px-2 py-1.5 text-right">Raw p</th>
                            <th class="px-2 py-1.5 text-right">Adj. Threshold</th>
                            <th class="px-2 py-1.5 text-center">Sig?</th>
                        </tr></thead>
                        <tbody>${points.map(p => `
                            <tr class="${p.significant ? 'bg-green-50/50 dark:bg-green-900/10' : ''}">
                                <td class="px-2 py-1">${p.n_total}</td>
                                <td class="px-2 py-1 text-right font-mono">${p.mean_a}</td>
                                <td class="px-2 py-1 text-right font-mono">${p.mean_b}</td>
                                <td class="px-2 py-1 text-right font-mono">${p.t_statistic?.toFixed(4)}</td>
                                <td class="px-2 py-1 text-right font-mono">${p.p_value_raw?.toFixed(4)}</td>
                                <td class="px-2 py-1 text-right font-mono">${p.z_threshold_adjusted?.toFixed(4)}</td>
                                <td class="px-2 py-1 text-center">${p.significant ? '<span class="text-green-500 font-bold">✓</span>' : '<span class="text-gray-300">—</span>'}</td>
                            </tr>
                        `).join('')}</tbody>
                    </table>
                </div>` : ''}
                <p class="text-sm">${App.escapeHtml(data.interpretation || '')}</p>
            </div>`;
        } catch (e) { App.toast('Sequential test failed', 'error'); }
    },

    async _runSampleSize() {
        const baseline = parseFloat(document.getElementById('ss-baseline')?.value);
        const mde = parseFloat(document.getElementById('ss-mde')?.value);
        const power = parseFloat(document.getElementById('ss-power')?.value);
        if (isNaN(baseline) || isNaN(mde)) return App.toast('Enter valid numbers', 'error');

        try {
            const data = await API.sampleSize(baseline, mde, 0.05, power);
            const el = document.getElementById('ab-result');
            if (!data.success) { el.innerHTML = `<div class="text-red-500 text-sm">${App.escapeHtml(data.error)}</div>`; return; }
            el.innerHTML = `
            <div class="bg-white dark:bg-gray-700 p-4 rounded-lg border dark:border-gray-600 space-y-3">
                <h4 class="font-semibold">Sample Size Calculator</h4>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
                    <div class="p-3 bg-blue-50 dark:bg-blue-900/20 rounded">
                        <div class="text-2xl font-bold text-blue-600">${data.n_per_group?.toLocaleString()}</div>
                        <div class="text-xs text-gray-500">Per Group</div>
                    </div>
                    <div class="p-3 bg-primary-50 dark:bg-primary-900/20 rounded">
                        <div class="text-2xl font-bold text-primary-600">${data.total_sample_size?.toLocaleString()}</div>
                        <div class="text-xs text-gray-500">Total</div>
                    </div>
                    <div class="p-3 bg-green-50 dark:bg-green-900/20 rounded">
                        <div class="text-2xl font-bold text-green-600">${data.relative_lift_pct}%</div>
                        <div class="text-xs text-gray-500">MDE Lift</div>
                    </div>
                    <div class="p-3 bg-gray-100 dark:bg-gray-600 rounded">
                        <div class="text-2xl font-bold">${(data.power * 100)}%</div>
                        <div class="text-xs text-gray-500">Power</div>
                    </div>
                </div>
                <p class="text-sm">${App.escapeHtml(data.interpretation || '')}</p>
            </div>`;
        } catch (e) { App.toast('Sample size calculation failed', 'error'); }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 13: Data Quality
       ════════════════════════════════════════════════════════════════ */
    async _renderQuality(el) {
        try {
            const data = await API.qualityScore(this._selectedTable);
            const quality = data.quality || data;
            const score = quality.overall_score || quality.score || 0;
            const dimensions = quality.dimension_scores || quality.dimensions || {};
            const dimEntries = Array.isArray(dimensions) ? dimensions :
                Object.entries(dimensions).map(([name, val]) => ({
                    name: name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
                    score: typeof val === 'number' ? val : (val.score || val.value || 0),
                }));

            el.innerHTML = `
            <div class="space-y-4">
                <h3 class="text-lg font-semibold">Data Quality — ${App.escapeHtml(this._selectedTable)}</h3>

                <!-- Score Gauge -->
                <div class="flex justify-center">
                    <div class="relative w-40 h-40">
                        <canvas id="q-gauge" width="160" height="160"></canvas>
                        <div class="absolute inset-0 flex items-center justify-center">
                            <span class="text-3xl font-bold">${Math.round(score)}%</span>
                        </div>
                    </div>
                </div>

                <!-- Dimension Bars -->
                <div class="space-y-3">
                    ${dimEntries.map(d => {
                        const val = typeof d === 'object' ? (d.score || d.value || 0) : 0;
                        const name = typeof d === 'object' ? (d.name || d.dimension || '') : d;
                        const color = val >= 80 ? 'bg-green-500' : val >= 50 ? 'bg-yellow-500' : 'bg-red-500';
                        return `<div>
                            <div class="flex justify-between text-sm mb-1">
                                <span class="font-medium">${App.escapeHtml(name)}</span>
                                <span>${Math.round(val)}%</span>
                            </div>
                            <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-3">
                                <div class="${color} h-3 rounded-full transition-all" style="width: ${val}%"></div>
                            </div>
                        </div>`;
                    }).join('')}
                </div>
            </div>`;

            // Draw gauge
            Charts.doughnut('q-gauge', ['Score', 'Remaining'], [score, 100 - score], {
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                cutout: '75%',
            });
        } catch (e) {
            el.innerHTML = `<p class="text-center text-red-500 py-8">Failed to load quality data.</p>`;
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 14: PII Detection
       ════════════════════════════════════════════════════════════════ */
    _renderPII(el) {
        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">PII Detection — ${App.escapeHtml(this._selectedTable)}</h3>
            <p class="text-sm text-gray-500">Scan columns for personally identifiable information.</p>
            <button onclick="AnalysisPage._runPIIScan()" class="bg-red-500 hover:bg-red-600 text-white px-5 py-2 rounded-lg text-sm">
                <i class="fas fa-user-secret mr-1"></i>Scan for PII
            </button>
            <div id="pii-results"></div>
        </div>`;
    },

    async _runPIIScan() {
        const el = document.getElementById('pii-results');
        el.innerHTML = '<div class="flex items-center justify-center py-6"><i class="fas fa-spinner fa-spin text-2xl text-red-500"></i></div>';

        try {
            const data = await API.piiScan(this._selectedTable);
            const pii = data.pii_scan || data;
            const columnsDetected = pii.columns_detected || pii.findings || pii.columns || [];
            const totalDetected = pii.total_detected || columnsDetected.length;
            const riskLevel = pii.risk_level || 'low';
            const recs = pii.recommendations || [];

            if (totalDetected === 0) {
                el.innerHTML = `<div class="text-center py-6">
                    <div class="w-16 h-16 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center mx-auto mb-3">
                        <i class="fas fa-shield-alt text-green-500 text-2xl"></i>
                    </div>
                    <p class="text-green-600 dark:text-green-400 font-medium">No PII detected</p>
                    <p class="text-sm text-gray-500 mt-1">Scanned ${pii.total_columns || 0} columns</p>
                    ${recs.length ? `<div class="mt-3 text-sm text-gray-500">${recs.map(r => `<p>${App.escapeHtml(r)}</p>`).join('')}</div>` : ''}
                </div>`;
                return;
            }

            const riskColors = {
                high: 'bg-red-100 dark:bg-red-900/30 border-red-300',
                medium: 'bg-yellow-100 dark:bg-yellow-900/30 border-yellow-300',
                low: 'bg-green-100 dark:bg-green-900/30 border-green-300',
            };

            const items = Array.isArray(columnsDetected) ? columnsDetected : [];
            el.innerHTML = `
            <div class="space-y-3">
                <div class="flex items-center gap-3 p-3 bg-${riskLevel === 'high' ? 'red' : riskLevel === 'medium' ? 'yellow' : 'green'}-50 dark:bg-${riskLevel === 'high' ? 'red' : riskLevel === 'medium' ? 'yellow' : 'green'}-900/20 rounded-lg">
                    <span class="text-lg font-bold">${totalDetected}</span>
                    <span class="text-sm">PII columns detected (Risk: <strong>${riskLevel.toUpperCase()}</strong>)</span>
                </div>
                ${items.map(item => `
                    <div class="p-3 rounded-lg border ${riskColors.low}">
                        <div class="font-medium">${App.escapeHtml(item.column || item.col || '')}</div>
                        <p class="text-sm text-gray-600 dark:text-gray-400">${App.escapeHtml(item.type || item.pii_type || item.pattern || 'PII')}</p>
                    </div>
                `).join('')}
                ${recs.length ? `<div class="text-sm text-gray-500 mt-2">${recs.map(r => `<p>• ${App.escapeHtml(r)}</p>`).join('')}</div>` : ''}
            </div>`;
        } catch (e) {
            el.innerHTML = '<p class="text-center text-red-500 py-4">PII scan failed.</p>';
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 15: Insights
       ════════════════════════════════════════════════════════════════ */
    _renderInsights(el) {
        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Data Insights — ${App.escapeHtml(this._selectedTable)}</h3>
            <p class="text-sm text-gray-500">Generate AI-powered narrative insights about your data.</p>
            <button onclick="AnalysisPage._generateInsights()" class="bg-amber-500 hover:bg-amber-600 text-white px-5 py-2 rounded-lg text-sm">
                <i class="fas fa-lightbulb mr-1"></i>Generate Insights
            </button>
            <div id="insights-output"></div>
        </div>`;
    },

    async _generateInsights() {
        const el = document.getElementById('insights-output');
        el.innerHTML = '<div class="flex items-center justify-center py-8"><i class="fas fa-spinner fa-spin text-2xl text-amber-500"></i><span class="ml-3 text-gray-500">Generating insights...</span></div>';

        try {
            const data = await API.insights(this._selectedTable);
            if (!data.success) throw new Error(data.error || 'Failed');
            const insights = data.insights || data;

            // Backend returns: {summary, key_findings: [], metrics_summary: [], ...}
            let html = '<div class="space-y-4">';

            if (insights.summary) {
                html += `<div class="bg-amber-50 dark:bg-amber-900/20 p-4 rounded-lg">
                    <h4 class="font-semibold mb-2"><i class="fas fa-lightbulb text-amber-500 mr-1"></i>Summary</h4>
                    <p class="text-sm leading-relaxed">${App.mdInline(insights.summary)}</p>
                </div>`;
            }

            const findings = insights.key_findings || [];
            if (findings.length > 0) {
                html += `<div class="bg-white dark:bg-gray-700 p-4 rounded-lg border dark:border-gray-600">
                    <h4 class="font-semibold mb-2">Key Findings</h4>
                    <ul class="space-y-1 text-sm">${findings.map(f => `<li class="flex items-start gap-2"><i class="fas fa-check text-green-500 mt-0.5"></i><span>${App.mdInline(f)}</span></li>`).join('')}</ul>
                </div>`;
            }

            const corrHighlights = insights.correlation_highlights || [];
            if (corrHighlights.length > 0) {
                html += `<div class="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg">
                    <h4 class="font-semibold mb-2"><i class="fas fa-link text-blue-500 mr-1"></i>Correlations</h4>
                    <ul class="space-y-1 text-sm">${corrHighlights.map(c => `<li>${App.mdInline(c)}</li>`).join('')}</ul>
                </div>`;
            }

            const recs = insights.recommendations || [];
            if (recs.length > 0) {
                html += `<div class="bg-purple-50 dark:bg-purple-900/20 p-4 rounded-lg">
                    <h4 class="font-semibold mb-2"><i class="fas fa-arrow-right text-purple-500 mr-1"></i>Recommendations</h4>
                    <ul class="space-y-1 text-sm">${recs.map(r => `<li>• ${App.mdInline(r)}</li>`).join('')}</ul>
                </div>`;
            }

            html += '</div>';
            el.innerHTML = html;
        } catch (e) {
            el.innerHTML = '<p class="text-center text-red-500 py-4">Failed to generate insights.</p>';
        }
    },

    /* ════════════════════════════════════════════════════════════════
       TAB 16: Report
       ════════════════════════════════════════════════════════════════ */
    _renderReport(el) {
        el.innerHTML = `
        <div class="space-y-4">
            <h3 class="text-lg font-semibold">Report Generation — ${App.escapeHtml(this._selectedTable)}</h3>
            <p class="text-sm text-gray-500">Generate a comprehensive HTML report for this table.</p>
            <button onclick="AnalysisPage._generateReport()" class="bg-indigo-500 hover:bg-indigo-600 text-white px-5 py-2 rounded-lg text-sm">
                <i class="fas fa-file-alt mr-1"></i>Generate Report
            </button>
            <div id="report-output"></div>
        </div>`;
    },

    async _generateReport() {
        const el = document.getElementById('report-output');
        el.innerHTML = '<div class="flex items-center justify-center py-8"><i class="fas fa-spinner fa-spin text-2xl text-indigo-500"></i><span class="ml-3 text-gray-500">Generating report...</span></div>';

        try {
            const data = await API.report(this._selectedTable);
            if (data.success === false) throw new Error(data.error || 'Report generation failed');

            const reportHtml = data.report_html || data.html || data.content || '';

            el.innerHTML = `
            <div class="space-y-3">
                <div class="bg-green-50 dark:bg-green-900/20 p-3 rounded-lg text-sm">
                    <i class="fas fa-check-circle text-green-500 mr-1"></i>
                    Report generated successfully.
                    ${data.sections ? `Sections: ${data.sections.join(', ')}` : ''}
                </div>
                <div class="border rounded-lg overflow-hidden">
                    <iframe id="report-frame" class="w-full h-[500px] border-0" sandbox="allow-same-origin"></iframe>
                </div>
                <div class="flex gap-3">
                    <a href="/report/${encodeURIComponent(this._selectedTable)}" target="_blank"
                        class="text-primary-500 hover:underline text-sm">
                        <i class="fas fa-external-link-alt mr-1"></i>Open full report
                    </a>
                    <button onclick="AnalysisPage._downloadReport()" class="text-sm text-gray-500 hover:text-gray-700">
                        <i class="fas fa-download mr-1"></i>Download HTML
                    </button>
                </div>
            </div>`;

            // Set iframe content
            const frame = document.getElementById('report-frame');
            if (frame && reportHtml) {
                frame.srcdoc = reportHtml;
            }

            this._lastReportHtml = reportHtml;
        } catch (e) {
            el.innerHTML = '<p class="text-center text-red-500 py-4">Report generation failed.</p>';
        }
    },

    _downloadReport() {
        if (!this._lastReportHtml) return App.toast('No report to download', 'error');
        const blob = new Blob([this._lastReportHtml], { type: 'text/html' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${this._selectedTable}_report.html`;
        a.click();
        URL.revokeObjectURL(url);
    },

    _renderStorytelling(el) {
        el.innerHTML = `
        <div class="space-y-4">
            <div class="flex items-center justify-between">
                <h3 class="text-lg font-semibold">Storytelling — ${App.escapeHtml(this._selectedTable)}</h3>
                <button onclick="AnalysisPage._loadStorytelling()" class="btn btn-sm btn-primary">
                    <i class="fas fa-sync mr-1"></i>Generate Stories
                </button>
            </div>
            <div id="storytelling-container"></div>
        </div>`;
        this._loadStorytelling();
    },

    async _loadStorytelling() {
        const el = document.getElementById('storytelling-container');
        if (!el) return;
        el.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-primary-400 text-lg"></i><p class="text-xs text-gray-400 mt-2">Generating AI narratives (can take ~60–90s)...</p></div>';
        try {
            const [autoData, jobData] = await Promise.all([
                API.insights(this._selectedTable).catch(() => null),
                API.storytelling(this._selectedTable).catch(() => null),
            ]);
            if (!jobData || !jobData.job_id) {
                if (jobData && jobData.error) {
                    el.innerHTML = '<div class="text-center py-4 text-red-400 text-xs">' + App.escapeHtml(jobData.error) + '</div>';
                    return;
                }
                // Served from the server-side cache (stories present, no job).
                if (jobData && jobData.stories) {
                    this._renderStories(el, autoData, jobData);
                    return;
                }
                // Fallback: show rule-based insights only.
                var fallback = (autoData && autoData.stories) || [];
                if (autoData && autoData.summary) fallback.unshift({ title: "Data Overview", narrative: autoData.summary, type: "trend", confidence: "high", source: "auto" });
                if (window.StoryFeed) StoryFeed.render(el, { stories: fallback, tableName: this._selectedTable, onRegenerate: function() { AnalysisPage._loadStorytelling(); } });
                else el.innerHTML = '<div class="text-center py-4 text-gray-400 text-xs">StoryFeed component not loaded</div>';
                return;
            }
            const storyData = await this._pollStorytellingJob(el, jobData.job_id);
            if (storyData === null) return;
            this._renderStories(el, autoData, storyData);
        } catch (e) {
            el.innerHTML = '<div class="text-center py-4 text-red-400 text-xs">Failed to load stories: ' + App.escapeHtml(e.message) + '</div>';
        }
    },

    _renderStories(el, autoData, storyData) {
        var stories = (storyData && storyData.stories) || [];
        // Build additional stories from auto insights
        if (autoData && autoData.summary) {
            stories.unshift({ title: "Data Overview", narrative: autoData.summary, type: "trend", confidence: "high", source: "auto" });
        }
        if (autoData && autoData.key_findings) {
            autoData.key_findings.slice(0, 3).forEach(function(f) {
                stories.push({ title: "Finding", narrative: f, type: "trend", confidence: "medium", source: "auto" });
            });
        }
        if (window.StoryFeed) {
            StoryFeed.render(el, { stories: stories, tableName: this._selectedTable, onRegenerate: function() { AnalysisPage._loadStorytelling(); } });
        } else {
            el.innerHTML = '<div class="text-center py-4 text-gray-400 text-xs">StoryFeed component not loaded</div>';
        }
    },

    async _pollStorytellingJob(el, jobId) {
        // Poll the background storytelling job until done/error (~90s max).
        const started = Date.now();
        const maxWait = 6 * 60 * 1000;
        while (Date.now() - started < maxWait) {
            var st = null;
            try { st = await API.storytellingStatus(jobId); } catch (_) { /* transient */ }
            if (st && st.success && st.status === 'done') return st;
            if (st && st.status === 'error') {
                el.innerHTML = '<div class="text-center py-4 text-red-400 text-xs">Storytelling failed: ' + App.escapeHtml(st.error || 'unknown error') + '</div>';
                return null;
            }
            await new Promise(r => setTimeout(r, 2000));
        }
        el.innerHTML = '<div class="text-center py-4 text-orange-400 text-xs">Storytelling is still generating. Click "Generate Stories" to poll again.</div>';
        return null;
    },

    /* ── Chart Export Methods ──────────────────────────────────────── */
    _exportChartAsPng() {
        const canvas = document.getElementById('cb-canvas');
        if (!canvas || canvas.toDataURL('image/png').length < 100) {
            return App.toast('No chart to export', 'error');
        }
        const link = document.createElement('a');
        link.download = `${this._selectedTable || 'chart'}_${Date.now()}.png`;
        link.href = canvas.toDataURL('image/png');
        link.click();
        App.success('Chart saved as PNG');
    },

    _exportChartData() {
        if (!this._chartData || !this._chartData.length) {
            return App.toast('Build a chart first to export data', 'error');
        }
        const data = this._chartData;
        const cols = Object.keys(data[0]);
        let csv = cols.map(c => `"${c.replace(/"/g, '""')}"`).join(',') + '\n';
        data.forEach(row => {
            csv += cols.map(c => {
                const val = row[c];
                return `"${String(val ?? '').replace(/"/g, '""')}"`;
            }).join(',') + '\n';
        });
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.download = `${this._selectedTable || 'data'}_chart_data.csv`;
        link.href = url;
        link.click();
        URL.revokeObjectURL(url);
        App.success('Chart data exported as CSV');
    },

    async _exportChartPptx() {
        if (!this._selectedTable) return App.toast('No table selected', 'error');
        try {
            App.showLoading();
            const resp = await fetch((window.__API_BASE__ || '') + `/export/pptx/${encodeURIComponent(this._selectedTable)}`);
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.download = `${this._selectedTable}_report.pptx`;
            link.href = url;
            link.click();
            URL.revokeObjectURL(url);
            App.hideLoading();
            App.success('Presentation exported as PPTX');
        } catch (e) {
            App.hideLoading();
            App.error('PPTX export failed: ' + (e.message || 'Unknown error'));
        }
    },
};

// Register with SPA router
window.AnalysisPage = AnalysisPage;
(window._PAGES = window._PAGES || {})["#analysis"] = AnalysisPage;
