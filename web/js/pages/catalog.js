/* ═══════════════════════════════════════════
   Data Catalog — Browse, search, and manage data assets
   ═══════════════════════════════════════════ */

const CatalogPage = {
    _assets: [],
    _terms: [],
    _metrics: [],
    _searchTimeout: null,

    async init() {
        this._assets = [];
        this._terms = [];
        this._metrics = [];
    },

    async destroy() {
        if (this._searchTimeout) clearTimeout(this._searchTimeout);
    },

    async render() {
        const pc = document.getElementById('page-content');
        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
            <div class="page-header">
                <div>
                    <h2><i class="fas fa-book mr-2 text-indigo-500"></i>Data Catalog</h2>
                    <p class="text-secondary mt-1">Browse, search, and manage your data assets, business terms, and metrics</p>
                </div>
                <div class="page-actions">
                    <button onclick="CatalogPage._sync()" class="btn btn-secondary btn-sm">
                        <i class="fas fa-sync"></i> Sync
                    </button>
                </div>
            </div>

            <!-- Search -->
            <div class="card">
                <div class="relative">
                    <i class="fas fa-search absolute left-3 top-2.5 text-secondary"></i>
                    <input id="cat-search" class="form-input pl-9" placeholder="Search assets, business terms, metrics..." oninput="CatalogPage._onSearch(this.value)">
                </div>
            </div>

            <!-- Tabs -->
            <div class="flex gap-2 border-b dark:border-gray-700">
                <button class="cat-tab active px-4 py-2 text-sm font-medium border-b-2 border-primary text-primary" data-tab="assets" onclick="CatalogPage._switchTab('assets')">
                    <i class="fas fa-table mr-1"></i> Assets
                </button>
                <button class="cat-tab px-4 py-2 text-sm font-medium text-secondary hover:text-gray-700 dark:hover:text-gray-300" data-tab="terms" onclick="CatalogPage._switchTab('terms')">
                    <i class="fas fa-font mr-1"></i> Business Terms
                </button>
                <button class="cat-tab px-4 py-2 text-sm font-medium text-secondary hover:text-gray-700 dark:hover:text-gray-300" data-tab="metrics" onclick="CatalogPage._switchTab('metrics')">
                    <i class="fas fa-calculator mr-1"></i> Metrics
                </button>
            </div>

            <!-- Tab Content -->
            <div id="cat-content">
                <div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>
            </div>
        </div>`;

        await this._loadAssets();
    },

    _switchTab(tab) {
        document.querySelectorAll('.cat-tab').forEach(t => {
            t.classList.remove('active', 'text-primary', 'border-primary');
            t.classList.add('text-secondary');
        });
        const active = document.querySelector(`.cat-tab[data-tab="${tab}"]`);
        if (active) {
            active.classList.add('active', 'text-primary', 'border-primary');
            active.classList.remove('text-secondary');
        }

        if (tab === 'terms') this._loadTerms();
        else if (tab === 'metrics') this._loadMetrics();
        else this._loadAssets();
    },

    _onSearch(val) {
        if (this._searchTimeout) clearTimeout(this._searchTimeout);
        this._searchTimeout = setTimeout(async () => {
            if (!val) { this._loadAssets(); return; }
            try {
                const res = await API.catalogSearch(val);
                if (!res.success) return;
                const results = res.results || {};
                const container = document.getElementById('cat-content');
                if (!container) return;
                let html = '';
                // Assets
                if (results.assets?.length) {
                    html += '<div class="card mb-4"><div class="card-header"><h3><i class="fas fa-table mr-2 text-blue-500"></i>Assets</h3></div>';
                    html += results.assets.map(a => `<div class="flex items-center justify-between py-2 border-b dark:border-gray-700 last:border-0">
                        <div><strong>${App.escapeHtml(a.name)}</strong><div class="text-xs text-secondary">${App.escapeHtml(a.table_name)}</div></div>
                        <span class="tag tag-${a.certification_status === 'certified' ? 'green' : a.certification_status === 'deprecated' ? 'red' : 'gray'}">${a.certification_status}</span>
                    </div>`).join('');
                    html += '</div>';
                }
                // Terms
                if (results.terms?.length) {
                    html += '<div class="card mb-4"><div class="card-header"><h3><i class="fas fa-font mr-2 text-green-500"></i>Business Terms</h3></div>';
                    html += results.terms.map(t => `<div class="py-2 border-b dark:border-gray-700 last:border-0">
                        <strong>${App.escapeHtml(t.term)}</strong>
                        <div class="text-xs text-secondary">${App.escapeHtml(t.definition)}</div>
                    </div>`).join('');
                    html += '</div>';
                }
                // Metrics
                if (results.metrics?.length) {
                    html += '<div class="card mb-4"><div class="card-header"><h3><i class="fas fa-calculator mr-2 text-purple-500"></i>Metrics</h3></div>';
                    html += results.metrics.map(m => `<div class="py-2 border-b dark:border-gray-700 last:border-0">
                        <strong>${App.escapeHtml(m.name)}</strong>
                        <div class="text-xs text-secondary">${App.escapeHtml(m.formula)}</div>
                    </div>`).join('');
                    html += '</div>';
                }
                if (!html) html = '<div class="empty-state py-8"><p>No results found</p></div>';
                container.innerHTML = html;
            } catch (e) {}
        }, 300);
    },

    async _loadAssets() {
        const container = document.getElementById('cat-content');
        if (!container) return;
        container.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>';

        try {
            const res = await API.catalogAssets();
            if (!res.success) { container.innerHTML = `<div class="empty-state"><p>${App.escapeHtml(res.error)}</p></div>`; return; }
            this._assets = res.assets || [];
            this._renderAssets(container);
        } catch (e) {
            container.innerHTML = `<div class="empty-state"><p>Error loading assets</p></div>`;
        }
    },

    _renderAssets(container) {
        const assets = this._assets;
        if (assets.length === 0) {
            container.innerHTML = `<div class="empty-state py-12">
                <i class="fas fa-book text-4xl mb-3 opacity-30"></i>
                <h3>No assets cataloged yet</h3>
                <p>Click <strong>Sync</strong> to scan the warehouse and register tables.</p>
                <button onclick="CatalogPage._sync()" class="btn btn-primary mt-4"><i class="fas fa-sync"></i> Sync Now</button>
            </div>`;
            return;
        }

        let html = `<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">`;
        for (const a of assets) {
            const certColor = a.certification_status === 'certified' ? 'green' : a.certification_status === 'deprecated' ? 'red' : 'gray';
            html += `<div class="card cursor-pointer hover:shadow-md transition-shadow" onclick="CatalogPage._showAsset('${a.id}')">
                <div class="flex items-start justify-between mb-2">
                    <div class="flex items-center gap-2">
                        <i class="fas fa-table text-blue-500"></i>
                        <strong class="text-sm">${App.escapeHtml(a.name)}</strong>
                    </div>
                    <span class="tag tag-${certColor}">${a.certification_status}</span>
                </div>
                <div class="text-xs text-secondary mb-2">${App.escapeHtml(a.table_name)}</div>
                ${a.description ? `<div class="text-xs text-secondary mb-2">${App.escapeHtml(a.description)}</div>` : ''}
                <div class="flex gap-3 text-xs text-secondary mt-2">
                    <span><i class="fas fa-layer-group mr-1"></i>${a.domain || 'general'}</span>
                    <span><i class="fas fa-table-list mr-1"></i>${(a.row_count || 0).toLocaleString()} rows</span>
                    ${a.quality_score ? `<span><i class="fas fa-star mr-1"></i>${a.quality_score}/100</span>` : ''}
                </div>
            </div>`;
        }
        html += '</div>';
        container.innerHTML = html;
    },

    async _showAsset(id) {
        try {
            const res = await API.catalogAsset(id);
            if (!res.success) { App.toast('Asset not found', 'error'); return; }
            const a = res.asset;
            App.showModal(a.name, `
                <div class="space-y-3 text-sm">
                    <div><strong>Table:</strong> ${App.escapeHtml(a.table_name)}</div>
                    ${a.description ? `<div><strong>Description:</strong> ${App.escapeHtml(a.description)}</div>` : ''}
                    <div><strong>Domain:</strong> ${App.escapeHtml(a.domain)}</div>
                    <div><strong>Owner:</strong> ${App.escapeHtml(a.owner || '—')}</div>
                    <div><strong>Rows:</strong> ${(a.row_count || 0).toLocaleString()}</div>
                    <div><strong>Certification:</strong> <span class="tag tag-${a.certification_status === 'certified' ? 'green' : a.certification_status === 'deprecated' ? 'red' : 'gray'}">${a.certification_status}</span></div>
                    <div class="border-t dark:border-gray-700 pt-3 flex gap-2">
                        <button onclick="CatalogPage._certify('${a.id}','certified')" class="btn btn-sm btn-primary">Certify</button>
                        <button onclick="CatalogPage._certify('${a.id}','deprecated')" class="btn btn-sm btn-danger">Deprecate</button>
                    </div>
                </div>
            `, null, 'Close');
        } catch (e) { App.toast('Failed to load asset', 'error'); }
    },

    async _certify(id, status) {
        const res = await API.certifyAsset(id, status);
        if (res.success === false) { App.toast(res.error || 'Certify failed', 'error'); return; }
        App.toast(`Asset ${status}`, 'success');
        App.closeModal();
        this._loadAssets();
    },

    async _loadTerms() {
        const container = document.getElementById('cat-content');
        if (!container) return;
        container.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>';
        try {
            const res = await API.listTerms();
            if (res.success === false) { container.innerHTML = `<div class="empty-state"><p>${App.escapeHtml(res.error)}</p></div>`; return; }
            const terms = res.terms || [];
            let html = `<div class="flex justify-end mb-3"><button onclick="CatalogPage._showAddTerm()" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i> Add Term</button></div>`;
            if (terms.length === 0) {
                html += '<div class="empty-state py-8"><p>No business terms defined yet</p></div>';
            } else {
                html += '<div class="card divide-y dark:divide-gray-700">';
                for (const t of terms) {
                    html += `<div class="py-3">
                        <div class="flex items-center justify-between">
                            <strong>${App.escapeHtml(t.term)}</strong>
                            ${t.approved ? '<span class="tag tag-green">Approved</span>' : '<span class="tag tag-amber">Draft</span>'}
                        </div>
                        <div class="text-xs text-secondary mt-1">${App.escapeHtml(t.definition)}</div>
                        <div class="text-xs text-secondary mt-1">Domain: ${App.escapeHtml(t.domain)}</div>
                    </div>`;
                }
                html += '</div>';
            }
            container.innerHTML = html;
        } catch (e) { container.innerHTML = '<div class="empty-state"><p>Error loading terms</p></div>'; }
    },

    _showAddTerm() {
        App.showModal('Add Business Term', `
            <div class="space-y-3">
                <div><label class="form-label">Term</label><input id="add-term-name" class="form-input" placeholder="e.g., Net Revenue"></div>
                <div><label class="form-label">Definition</label><textarea id="add-term-def" class="form-textarea" placeholder="Define the term..."></textarea></div>
                <div><label class="form-label">Domain</label><input id="add-term-domain" class="form-input" value="general"></div>
            </div>
        `, async () => {
            const term = document.getElementById('add-term-name')?.value;
            if (!term) { App.toast('Term is required', 'error'); return; }
            const res = await API.createTerm({ term, definition: document.getElementById('add-term-def')?.value || '', domain: document.getElementById('add-term-domain')?.value || 'general' });
            if (res.success === false) { App.toast(res.error || 'Failed to create term', 'error'); return; }
            App.toast('Term created', 'success');
            this._loadTerms();
        });
    },

    async _loadMetrics() {
        const container = document.getElementById('cat-content');
        if (!container) return;
        container.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>';
        try {
            const res = await API.listMetrics();
            if (res.success === false) { container.innerHTML = `<div class="empty-state"><p>${App.escapeHtml(res.error)}</p></div>`; return; }
            const metrics = res.metrics || [];
            let html = `<div class="flex justify-end mb-3"><button onclick="CatalogPage._showAddMetric()" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i> Add Metric</button></div>`;
            if (metrics.length === 0) {
                html += '<div class="empty-state py-8"><p>No metric definitions yet</p></div>';
            } else {
                html += '<div class="grid grid-cols-1 md:grid-cols-2 gap-4">';
                for (const m of metrics) {
                    html += `<div class="card">
                        <div class="flex items-center gap-2 mb-1"><i class="fas fa-calculator text-purple-500"></i><strong>${App.escapeHtml(m.name)}</strong></div>
                        <div class="text-xs text-secondary mb-1">${App.escapeHtml(m.description)}</div>
                        <div class="text-xs font-mono bg-gray-50 dark:bg-gray-800 p-2 rounded">${App.escapeHtml(m.formula)}</div>
                        <div class="text-xs text-secondary mt-1">${App.escapeHtml(m.domain)} ${m.unit ? '· ' + App.escapeHtml(m.unit) : ''}</div>
                    </div>`;
                }
                html += '</div>';
            }
            container.innerHTML = html;
        } catch (e) { container.innerHTML = '<div class="empty-state"><p>Error loading metrics</p></div>'; }
    },

    _showAddMetric() {
        App.showModal('Add Metric Definition', `
            <div class="space-y-3">
                <div><label class="form-label">Name</label><input id="add-metric-name" class="form-input" placeholder="e.g., Monthly Recurring Revenue"></div>
                <div><label class="form-label">Formula</label><input id="add-metric-formula" class="form-input" placeholder="e.g., SUM(revenue)"></div>
                <div><label class="form-label">Description</label><textarea id="add-metric-desc" class="form-textarea"></textarea></div>
                <div class="grid grid-cols-2 gap-2">
                    <div><label class="form-label">Domain</label><input id="add-metric-domain" class="form-input" value="general"></div>
                    <div><label class="form-label">Unit</label><input id="add-metric-unit" class="form-input" placeholder="e.g., USD"></div>
                </div>
            </div>
        `, async () => {
            const name = document.getElementById('add-metric-name')?.value;
            if (!name) { App.toast('Name is required', 'error'); return; }
            const res = await API.createMetric({ name, formula: document.getElementById('add-metric-formula')?.value || '', description: document.getElementById('add-metric-desc')?.value || '', domain: document.getElementById('add-metric-domain')?.value || 'general', unit: document.getElementById('add-metric-unit')?.value || '' });
            if (res.success === false) { App.toast(res.error || 'Failed to create metric', 'error'); return; }
            App.toast('Metric created', 'success');
            this._loadMetrics();
        });
    },

    async _sync() {
        App.toast('Syncing catalog...', 'info');
        try {
            const res = await API.syncCatalog();
            if (res.success) {
                App.toast(`Sync complete: ${res.result?.added || 0} added, ${res.result?.updated || 0} updated`, 'success');
                this._loadAssets();
            } else {
                App.toast('Sync failed: ' + (res.error || 'unknown error'), 'error');
            }
        } catch (e) { App.toast('Sync error: ' + e.message, 'error'); }
    },
};

window.CatalogPage = CatalogPage;
(window._PAGES = window._PAGES || {})["#catalog"] = CatalogPage;
