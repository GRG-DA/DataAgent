/* ═══════════════════════════════════════════
   Data Marketplace — Browse, request, and publish data products
   ═══════════════════════════════════════════ */

const MarketplacePage = {
    _products: [],
    _requests: [],

    async init() { this._products = []; this._requests = []; },
    async destroy() {},

    async render() {
        const pc = document.getElementById('page-content');
        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
            <div class="page-header">
                <div>
                    <h2><i class="fas fa-store mr-2 text-emerald-500"></i>Data Marketplace</h2>
                    <p class="text-secondary mt-1">Discover and request access to curated data products</p>
                </div>
                <div class="page-actions">
                    <button onclick="MarketplacePage._showPublish()" class="btn btn-primary btn-sm"><i class="fas fa-plus"></i> Publish</button>
                </div>
            </div>

            <div class="flex gap-2 border-b dark:border-gray-700">
                <button class="mp-tab active px-4 py-2 text-sm font-medium border-b-2 border-primary text-primary" onclick="MarketplacePage._switchTab('browse')">Browse</button>
                <button class="mp-tab px-4 py-2 text-sm font-medium text-secondary" onclick="MarketplacePage._switchTab('requests')">My Requests</button>
            </div>

            <div id="mp-content" class="space-y-4">
                <div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>
            </div>
        </div>`;
        await this._loadProducts();
    },

    _switchTab(tab) {
        document.querySelectorAll('.mp-tab').forEach(t => {
            t.classList.remove('active', 'border-primary', 'text-primary');
            t.classList.add('text-secondary');
        });
        const active = document.querySelector(`.mp-tab[onclick*="${tab}"]`);
        if (active) {
            active.classList.add('active', 'border-primary', 'text-primary');
            active.classList.remove('text-secondary');
        }
        if (tab === 'browse') this._loadProducts();
        else this._loadRequests();
    },

    async _loadProducts() {
        const container = document.getElementById('mp-content');
        if (!container) return;
        container.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>';
        try {
            const res = await API.marketplaceProducts();
            this._products = res.products || [];
            this._renderProducts(container);
        } catch (e) {
            container.innerHTML = '<div class="empty-state"><p>Failed to load</p></div>';
        }
    },

    _renderProducts(container) {
        const products = this._products;
        if (products.length === 0) {
            container.innerHTML = `<div class="empty-state py-12">
                <i class="fas fa-store text-4xl mb-3 opacity-30"></i>
                <h3>No data products yet</h3>
                <p>Publish curated datasets for your team to discover.</p>
                <button onclick="MarketplacePage._showPublish()" class="btn btn-primary mt-4"><i class="fas fa-plus"></i> Publish Product</button>
            </div>`;
            return;
        }
        let html = '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">';
        for (const p of products) {
            const certColor = p.certification_status === 'certified' ? 'green' : p.certification_status === 'deprecated' ? 'red' : 'gray';
            html += `<div class="card hover:shadow-md transition-shadow cursor-pointer" onclick="MarketplacePage._showDetail('${p.id}')">
                <div class="flex items-start justify-between mb-2">
                    <i class="fas fa-cube text-emerald-500 text-lg"></i>
                    <span class="tag tag-${certColor} text-[10px]">${p.certification_status}</span>
                </div>
                <strong class="text-sm">${App.escapeHtml(p.name)}</strong>
                <div class="text-xs text-secondary mt-1 line-clamp-2">${App.escapeHtml(p.description || 'No description')}</div>
                <div class="flex items-center gap-3 mt-3 text-xs text-secondary">
                    <span><i class="fas fa-layer-group mr-1"></i>${p.domain || 'general'}</span>
                    <span><i class="fas fa-star mr-1 ${p.avg_rating ? 'text-amber-400' : ''}"></i>${p.avg_rating || '—'}</span>
                    <span class="ml-auto"><i class="fas fa-table mr-1"></i>${(p.tables || []).length} tables</span>
                </div>
            </div>`;
        }
        html += '</div>';
        container.innerHTML = html;
    },

    async _showDetail(productId) {
        try {
            const res = await API.getMarketplaceProduct(productId);
            if (!res.success) { App.toast('Product not found', 'error'); return; }
            const p = res.product;
            const tables = (p.tables || []).map(t => `<span class="tag tag-blue">${App.escapeHtml(t)}</span>`).join(' ');
            App.showModal(p.name, `
                <div class="space-y-3 text-sm">
                    ${p.description ? `<p>${App.escapeHtml(p.description)}</p>` : ''}
                    <div class="grid grid-cols-2 gap-2 text-xs">
                        <div><strong>Domain:</strong> ${App.escapeHtml(p.domain)}</div>
                        <div><strong>Owner:</strong> ${App.escapeHtml(p.owner || '—')}</div>
                        <div><strong>Quality:</strong> ${p.quality_score || '—'}/100</div>
                        <div><strong>Rating:</strong> ${p.avg_rating || '—'} (${p.rating_count || 0})</div>
                        <div><strong>Usage:</strong> ${p.usage_count || 0}</div>
                        <div><strong>Status:</strong> <span class="tag tag-${p.certification_status === 'certified' ? 'green' : 'gray'}">${p.certification_status}</span></div>
                    </div>
                    <div><strong>Tables:</strong> ${tables || 'None'}</div>
                    <div class="border-t dark:border-gray-700 pt-3">
                        <button onclick="MarketplacePage._requestAccess('${p.id}')" class="btn btn-primary btn-sm w-full"><i class="fas fa-hand"></i> Request Access</button>
                    </div>
                </div>
            `, null, 'Close');
        } catch (e) { App.toast('Failed to load detail', 'error'); }
    },

    async _requestAccess(productId) {
        App.closeModal();
        App.showModal('Request Access', `
            <div class="space-y-3">
                <div><label class="form-label">Your Name</label><input id="req-name" class="form-input" value="User"></div>
                <div><label class="form-label">Reason for Access</label><textarea id="req-reason" class="form-textarea" placeholder="Why do you need this data?"></textarea></div>
            </div>
        `, async () => {
            const name = document.getElementById('req-name')?.value || 'User';
            const reason = document.getElementById('req-reason')?.value || '';
            const res = await API.requestProductAccess(productId, { requester_id: 'current', requester_name: name, reason });
            if (res && res.success === false) { App.toast(res.error || 'Request failed', 'error'); return; }
            App.toast('Access request submitted', 'success');
        });
    },

    async _loadRequests() {
        const container = document.getElementById('mp-content');
        if (!container) return;
        container.innerHTML = '<div class="text-center py-8"><i class="fas fa-spinner fa-spin text-xl"></i></div>';
        try {
            const res = await API.getMyRequests('current');
            const requests = res.requests || [];
            if (requests.length === 0) {
                container.innerHTML = '<div class="empty-state py-8"><i class="fas fa-inbox text-3xl mb-2 opacity-30"></i><p>No requests yet</p></div>';
                return;
            }
            container.innerHTML = '<div class="card divide-y dark:divide-gray-700">' +
                requests.map(r => `
                <div class="py-3 flex items-center justify-between">
                    <div>
                        <strong class="text-sm">${App.escapeHtml(r.product_name || r.product_id)}</strong>
                        <div class="text-xs text-secondary">${r.reason ? App.escapeHtml(r.reason) : ''}</div>
                    </div>
                    <span class="tag tag-${r.status === 'approved' ? 'green' : r.status === 'rejected' ? 'red' : 'amber'}">${r.status}</span>
                </div>`).join('') + '</div>';
        } catch (e) {
            container.innerHTML = '<div class="empty-state"><p>Failed to load</p></div>';
        }
    },

    _showPublish() {
        App.showModal('Publish Data Product', `
            <div class="space-y-3">
                <div><label class="form-label">Product Name</label><input id="pub-name" class="form-input" placeholder="e.g., Customer 360"></div>
                <div><label class="form-label">Description</label><textarea id="pub-desc" class="form-textarea" placeholder="Describe the data product"></textarea></div>
                <div><label class="form-label">Tables (comma-separated)</label><input id="pub-tables" class="form-input" placeholder="customers, orders, sales"></div>
                <div><label class="form-label">Domain</label><input id="pub-domain" class="form-input" value="general"></div>
                <div><label class="form-label">Tags (comma-separated)</label><input id="pub-tags" class="form-input" placeholder="customer, revenue"></div>
            </div>
        `, async () => {
            const name = document.getElementById('pub-name')?.value;
            if (!name) { App.toast('Name is required', 'error'); return; }
            const tables = (document.getElementById('pub-tables')?.value || '').split(',').map(t => t.trim()).filter(Boolean);
            const tags = (document.getElementById('pub-tags')?.value || '').split(',').map(t => t.trim()).filter(Boolean);
            const res = await API.publishMarketplaceProduct({
                name, description: document.getElementById('pub-desc')?.value || '',
                tables, domain: document.getElementById('pub-domain')?.value || 'general', tags,
            });
            if (res && res.success === false) { App.toast(res.error || 'Publish failed', 'error'); return; }
            App.toast('Product published!', 'success');
            this._loadProducts();
        });
    },
};

window.MarketplacePage = MarketplacePage;
(window._PAGES = window._PAGES || {})["#marketplace"] = MarketplacePage;
