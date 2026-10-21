/* ═══════════════════════════════════════════
   External Database Connections
   Connect, browse schemas, and import tables
   ═══════════════════════════════════════════ */

const ConnectPage = {
    _connections: [],
    _currentConn: null,
    _currentTables: [],
    _savedConnections: [],

    async init() {
        this._connections = [];
        this._savedConnections = [];
        try {
            const r = await API.listConnections();
            if (r.success) this._savedConnections = r.connections || [];
        } catch (_) { this._savedConnections = []; }
    },

    async destroy() { this._currentConn = null; },

    async render() {
        const pc = document.getElementById('page-content');
        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
            <div>
                <div class="page-title">
                    <div class="page-title-icon"><i class="fas fa-database"></i></div>
                    <div>
                        <h2>Database Connections</h2>
                        <p class="page-title-subtitle">Connect to external databases, browse schemas, and import data</p>
                    </div>
                </div>
            </div>

            <!-- Connect Form -->
            <div class="card">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-plug mr-1"></i>New Connection</h3>
                <form autocomplete="off" onsubmit="return false;">
                <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Type</label>
                        <select id="conn-type" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600">
                            <option value="postgresql">PostgreSQL</option>
                            <option value="mysql">MySQL</option>
                            <option value="sqlite">SQLite (file)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Host</label>
                        <input id="conn-host" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="localhost">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Port</label>
                        <input id="conn-port" type="number" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="5432">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1">Database</label>
                        <input id="conn-db" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="mydb">
                    </div>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1" for="conn-user">Username</label>
                        <input id="conn-user" autocomplete="username" aria-label="Database username" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="user">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-500 mb-1" for="conn-pass">Password</label>
                        <input id="conn-pass" type="password" autocomplete="current-password" aria-label="Database password" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600" placeholder="••••••••">
                    </div>
                    <div class="flex items-end gap-2">
                        <button type="button" onclick="ConnectPage._testConnection()" id="conn-test-btn" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm font-medium flex-1">
                            <i class="fas fa-plug mr-1"></i>Connect
                        </button>
                        <button type="button" onclick="ConnectPage._clearForm()" class="text-gray-400 hover:text-red-500 px-2 py-2"><i class="fas fa-eraser"></i></button>
                    </div>
                </div>
                </form>
                <div id="conn-status" class="mt-2 text-sm"></div>
            </div>

            <!-- Connection Details + Schema Browser -->
            <div id="conn-browser" class="hidden space-y-4">
                <div class="card">
                    <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3"><i class="fas fa-table-list mr-1"></i>Available Tables</h3>
                    <div id="conn-tables" class="text-center py-4 text-gray-400 text-sm"><i class="fas fa-spinner fa-spin"></i> Loading...</div>
                </div>
                <div id="conn-import-result" class="hidden"></div>
            </div>
        </div>`;
    },

    async _testConnection() {
        const btn = document.getElementById('conn-test-btn');
        const status = document.getElementById('conn-status');
        const browser = document.getElementById('conn-browser');

        const request = {
            db_type: document.getElementById('conn-type').value,
            host: document.getElementById('conn-host').value || 'localhost',
            port: parseInt(document.getElementById('conn-port').value) || null,
            database: document.getElementById('conn-db').value,
            username: document.getElementById('conn-user').value || '',
            password: document.getElementById('conn-pass').value || '',
        };

        if (!request.database) return App.toast('Enter a database name', 'error');

        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Connecting...'; }
        if (status) status.innerHTML = '<span class="text-gray-400"><i class="fas fa-spinner fa-spin"></i> Testing connection...</span>';

        try {
            const result = await API.connectExternal(request);
            if (result.success) {
                if (status) status.innerHTML = `<span class="text-green-600 dark:text-green-400"><i class="fas fa-check-circle mr-1"></i>Connected! Found ${result.tables?.length || 0} tables</span>`;
                this._currentConn = request;
                this._currentTables = result.tables || [];
                browser.classList.remove('hidden');
                this._renderTables(result.tables || []);
                App.success(`Connected to ${request.db_type}:${request.database}`);
            } else {
                if (status) status.innerHTML = `<span class="text-red-500"><i class="fas fa-times-circle mr-1"></i>${App.escapeHtml(result.error || 'Connection failed')}</span>`;
                browser.classList.add('hidden');
            }
        } catch (e) {
            if (status) status.innerHTML = `<span class="text-red-500"><i class="fas fa-times-circle mr-1"></i>${App.escapeHtml(e.message || 'Connection error')}</span>`;
            browser.classList.add('hidden');
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-plug mr-1"></i>Connect'; }
        }
    },

    _renderTables(tables) {
        const el = document.getElementById('conn-tables');
        if (!tables.length) {
            el.innerHTML = '<div class="text-center py-4 text-gray-400 text-sm">No tables found in this database</div>';
            return;
        }
        el.innerHTML = `<div class="grid grid-cols-1 md:grid-cols-3 gap-2">${tables.map(t => this._renderTableCard(t)).join('')}</div>`;
    },

    _renderTableCard(tableName) {
        const safeName = App.escapeHtml(tableName);
        return `<div class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30 border border-gray-100 dark:border-gray-700 hover:shadow-sm transition-shadow">
            <div class="flex items-center justify-between">
                <span class="text-sm font-medium text-gray-700 dark:text-gray-300 truncate"><i class="fas fa-table text-emerald-500 mr-1.5"></i>${safeName}</span>
                <button onclick="ConnectPage._importTable('${safeName}')" class="text-[10px] bg-emerald-500 hover:bg-emerald-600 text-white px-2 py-1 rounded font-medium"><i class="fas fa-download mr-1"></i>Import</button>
            </div>
        </div>`;
    },

    async _importTable(tableName) {
        if (!this._currentConn) return;
        const resultEl = document.getElementById('conn-import-result');
        resultEl.classList.remove('hidden');
        resultEl.innerHTML = `<div class="card"><div class="text-sm text-gray-400"><i class="fas fa-spinner fa-spin mr-1"></i> Importing ${App.escapeHtml(tableName)}...</div></div>`;

        try {
            const result = await API.importExternalTable(
                this._currentConn,
                tableName,
                `ext_${this._currentConn.db_type}_${tableName}`
            );
            if (result.success) {
                resultEl.innerHTML = `<div class="card">
                    <div class="text-sm text-green-600 dark:text-green-400"><i class="fas fa-check-circle mr-1"></i>Imported successfully!</div>
                    <div class="text-xs text-gray-500 mt-1">${result.rows?.toLocaleString() || '?'} rows into <code class="bg-gray-100 dark:bg-gray-700 px-1 py-0.5 rounded">${App.escapeHtml(result.table || '')}</code></div>
                    <button onclick="App.state.selectedTable='${App.escapeHtml(result.table || '')}';App.navigate('#warehouse')" class="text-xs text-primary-500 hover:underline mt-2 inline-block">View in Warehouse →</button>
                </div>`;
                App.success(`Imported ${result.rows || ''} rows from ${tableName}`);
            } else {
                resultEl.innerHTML = `<div class="card"><div class="text-sm text-red-500"><i class="fas fa-times-circle mr-1"></i>${App.escapeHtml(result.error || 'Import failed')}</div></div>`;
            }
        } catch (e) {
            resultEl.innerHTML = `<div class="card"><div class="text-sm text-red-500"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(e.message || 'Error')}</div></div>`;
        }
    },

    _clearForm() {
        ['conn-host', 'conn-port', 'conn-db', 'conn-user', 'conn-pass'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        document.getElementById('conn-status').innerHTML = '';
        document.getElementById('conn-browser').classList.add('hidden');
        this._currentConn = null;
    },
};

window.ConnectPage = ConnectPage;
(window._PAGES = window._PAGES || {})["#connect"] = ConnectPage;
