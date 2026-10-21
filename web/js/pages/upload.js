/* ═══════════════════════════════════════════
   Upload Page — File upload, schema preview, external sources
   ═══════════════════════════════════════════ */

const UploadPage = {
    _file: null,
    _uploadResult: null,
    _activeTab: "upload",

    async init() {},

    async destroy() {
        this._file = null;
        this._uploadResult = null;
    },

    async render() {
        const pc = document.getElementById("page-content");

        pc.innerHTML = `
        <div class="max-w-4xl mx-auto space-y-6 animate-fade-in">
            <div class="page-title"><div class="page-title-icon"><i class="fas fa-upload"></i></div><div><h2>Data Upload</h2></div></div>

            <!-- Tabs -->
            <div class="flex border-b border-gray-200 dark:border-gray-700">
                <button class="tab-btn active" onclick="UploadPage.switchTab('upload')" id="tab-upload">
                    <i class="fas fa-file-upload mr-1"></i>Upload File
                </button>
                <button class="tab-btn" onclick="UploadPage.switchTab('external')" id="tab-external" data-feature="external_sources">
                    <i class="fas fa-plug mr-1"></i>External Sources
                </button>
            </div>

            <!-- Upload File Tab -->
            <div id="panel-upload" class="space-y-6">
                <!-- Drag-and-Drop Zone -->
                <div class="card">
                    <h3 class="text-lg font-semibold mb-3">Select File</h3>
                    <div class="drop-zone" id="drop-zone" onclick="document.getElementById('file-input').click()">
                        <input type="file" id="file-input" class="hidden" accept=".csv,.xlsx,.xls,.parquet,.json,.tsv" onchange="UploadPage.onFileSelect(this.files[0])">
                        <div id="drop-zone-content">
                            <i class="fas fa-cloud-upload-alt text-4xl text-gray-300 dark:text-gray-600 mb-3"></i>
                            <p class="text-gray-500 dark:text-gray-400">Drag &amp; drop a file here, or <span class="text-primary-500 font-medium">browse</span></p>
                            <p class="text-xs text-gray-400 mt-2">Supported: CSV, Excel, Parquet, JSON, TSV</p>
                        </div>
                        <div id="drop-zone-file" class="hidden">
                            <div class="flex items-center gap-3">
                                <i class="fas fa-file text-3xl text-primary-500"></i>
                                <div class="text-left">
                                    <p class="font-medium" id="file-name"></p>
                                    <p class="text-sm text-gray-400" id="file-meta"></p>
                                </div>
                                <span id="file-type-badge" class="px-2 py-1 rounded-full text-xs font-bold bg-primary-100 text-primary-700 dark:bg-primary-900 dark:text-primary-300"></span>
                            </div>
                        </div>
                    </div>
                    <div class="mt-4 flex gap-2">
                        <button onclick="UploadPage.uploadAndParse()" id="btn-upload" title="Select a file above to enable upload" class="bg-primary-500 hover:bg-primary-600 text-white px-5 py-2 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed" disabled>
                            <i class="fas fa-cog mr-1"></i>Upload &amp; Parse
                        </button>
                    </div>
                </div>

                <!-- Schema Preview -->
                <div class="card hidden" id="schema-section">
                    <h3 class="text-lg font-semibold mb-3"><i class="fas fa-table mr-1 text-primary-500"></i>Schema Preview</h3>
                    <div id="schema-container" class="overflow-x-auto"></div>
                </div>

                <!-- Quality Issues -->
                <div class="card hidden" id="quality-section">
                    <h3 class="text-lg font-semibold mb-3"><i class="fas fa-exclamation-triangle mr-1 text-yellow-500"></i>Quality Issues</h3>
                    <div id="quality-container"></div>
                </div>

                <!-- Action Buttons -->
                <div class="card hidden" id="actions-section">
                    <h3 class="text-lg font-semibold mb-3">Actions</h3>
                    <div class="flex flex-wrap gap-3">
                        <button onclick="UploadPage.cleanData()" class="bg-yellow-500 hover:bg-yellow-600 text-white px-5 py-2 rounded-lg font-medium transition-colors">
                            <i class="fas fa-broom mr-1"></i>Clean Data
                        </button>
                        <button onclick="UploadPage.loadToWarehouse()" class="bg-green-500 hover:bg-green-600 text-white px-5 py-2 rounded-lg font-medium transition-colors">
                            <i class="fas fa-database mr-1"></i>Load to Warehouse
                        </button>
                    </div>
                    <div id="load-status" class="mt-3 hidden"></div>
                </div>
            </div>

            <!-- External Sources Tab -->
            <div id="panel-external" class="hidden space-y-6" data-feature="external_sources">
                <!-- Database Connector -->
                <div class="card">
                    <h3 class="text-lg font-semibold mb-3"><i class="fas fa-database mr-1 text-blue-500"></i>Database Connector</h3>
                    <form autocomplete="off" onsubmit="return false;">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium mb-1">Database Type</label>
                            <select id="db-type" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                                <option value="postgresql">PostgreSQL</option>
                                <option value="mysql">MySQL</option>
                                <option value="sqlite">SQLite</option>
                                <option value="sqlserver">SQL Server</option>
                                <option value="clickhouse">ClickHouse</option>
                                <option value="oracle">Oracle</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1">Host</label>
                            <input type="text" id="db-host" placeholder="localhost" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1">Port</label>
                            <input type="number" id="db-port" placeholder="5432" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1">Database</label>
                            <input type="text" id="db-name" placeholder="my_database" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1" for="db-user">Username</label>
                            <input type="text" id="db-user" placeholder="user" autocomplete="username" aria-label="Database username" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1" for="db-pass">Password</label>
                            <input type="password" id="db-pass" placeholder="password" autocomplete="current-password" aria-label="Database password" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        </div>
                    </div>
                    <div id="db-table-select" class="mt-4 hidden">
                        <label class="block text-sm font-medium mb-1">Table to Import</label>
                        <select id="db-table" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                            <option value="">Select a table...</option>
                        </select>
                    </div>
                    <div class="mt-4 flex gap-2">
                        <button type="button" onclick="UploadPage.testDbConnection()" class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded-lg font-medium transition-colors">
                            <i class="fas fa-plug mr-1"></i>Test Connection
                        </button>
                        <button type="button" onclick="UploadPage.importFromDb()" id="btn-db-import" class="bg-green-500 hover:bg-green-600 text-white px-4 py-2 rounded-lg font-medium transition-colors" disabled>
                            <i class="fas fa-download mr-1"></i>Import
                        </button>
                    </div>
                    </form>
                    <div id="db-status" class="mt-3"></div>
                </div>

                <!-- API Import -->
                <div class="card">
                    <h3 class="text-lg font-semibold mb-3"><i class="fas fa-globe mr-1 text-purple-500"></i>API Import</h3>
                    <form autocomplete="off" onsubmit="return false;">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="md:col-span-2">
                            <label class="block text-sm font-medium mb-1">API URL</label>
                            <input type="url" id="api-url" placeholder="https://api.example.com/data" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1">Method</label>
                            <select id="api-method" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                                <option value="GET">GET</option>
                                <option value="POST">POST</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1" for="api-token">Auth Token (optional)</label>
                            <input type="password" id="api-token" placeholder="Bearer ..." autocomplete="off" aria-label="API auth token" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600">
                        </div>
                        <div class="md:col-span-2">
                            <label class="block text-sm font-medium mb-1" for="api-headers">Headers (JSON, optional)</label>
                            <textarea id="api-headers" rows="2" placeholder='{"X-Custom": "value"}' aria-label="Custom request headers as JSON" class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600 font-mono text-sm"></textarea>
                        </div>
                        <div class="md:col-span-2">
                            <label class="block text-sm font-medium mb-1">Body / Params (JSON, optional)</label>
                            <textarea id="api-body" rows="2" placeholder='{"key": "value"}' class="w-full border rounded-lg p-2 dark:bg-gray-700 dark:border-gray-600 font-mono text-sm"></textarea>
                        </div>
                    </div>
                    <div class="mt-4 flex gap-2">
                        <button type="button" onclick="UploadPage.importFromApi()" class="bg-purple-500 hover:bg-purple-600 text-white px-4 py-2 rounded-lg font-medium transition-colors">
                            <i class="fas fa-download mr-1"></i>Import from API
                        </button>
                    </div>
                    </form>
                    <div id="api-status" class="mt-3"></div>
                </div>
            </div>
        </div>`;

        // Set up drag-and-drop listeners
        this._setupDropZone();

        // Gate premium features (External Sources) for the current plan.
        if (window.RBAC && RBAC.applyToNav) RBAC.applyToNav();
    },

    // ── Tab switching ─────────────────────────────────────────────
    switchTab(tab) {
        this._activeTab = tab;
        document.getElementById("tab-upload").classList.toggle("active", tab === "upload");
        document.getElementById("tab-external").classList.toggle("active", tab === "external");
        document.getElementById("panel-upload").classList.toggle("hidden", tab !== "upload");
        document.getElementById("panel-external").classList.toggle("hidden", tab !== "external");
    },

    // ── Drag & Drop ──────────────────────────────────────────────
    _setupDropZone() {
        const zone = document.getElementById("drop-zone");
        if (!zone) return;

        zone.addEventListener("dragover", (e) => {
            e.preventDefault();
            zone.classList.add("dragover");
        });
        zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
        zone.addEventListener("drop", (e) => {
            e.preventDefault();
            zone.classList.remove("dragover");
            if (e.dataTransfer.files.length > 0) {
                this.onFileSelect(e.dataTransfer.files[0]);
            }
        });
    },

    // ── File selection ────────────────────────────────────────────
    onFileSelect(file) {
        if (!file) return;
        this._file = file;
        this._uploadResult = null;

        document.getElementById("drop-zone-content").classList.add("hidden");
        document.getElementById("drop-zone-file").classList.remove("hidden");
        document.getElementById("file-name").textContent = file.name;

        const size = file.size > 1e6
            ? (file.size / 1e6).toFixed(1) + " MB"
            : (file.size / 1e3).toFixed(1) + " KB";
        document.getElementById("file-meta").textContent = size;

        // Determine file type badge
        const ext = file.name.split(".").pop().toLowerCase();
        const badge = document.getElementById("file-type-badge");
        const typeColors = {
            csv: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
            xlsx: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300",
            xls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300",
            parquet: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
            json: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
            tsv: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
        };
        badge.textContent = ext.toUpperCase();
        badge.className = "px-2 py-1 rounded-full text-xs font-bold " + (typeColors[ext] || "bg-gray-100 text-gray-700");

        document.getElementById("btn-upload").disabled = false;
    },

    // ── Upload & Parse ───────────────────────────────────────────
    async uploadAndParse() {
        if (!this._file) return App.toast("Select a file first", "error");

        const btn = document.getElementById("btn-upload");
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Uploading...';

        try {
            const result = await API.upload(this._file);
            this._uploadResult = result;
            App.success("File uploaded and parsed successfully");

            // Show schema preview
            if (result.schema || result.columns || result.fields) {
                const schema = result.schema || result.columns || result.fields;
                this._renderSchema(schema);
                document.getElementById("schema-section").classList.remove("hidden");
            }

            // Show quality issues
            const issues = result.quality_issues || result.warnings || result.issues;
            if (issues && (Array.isArray(issues) ? issues.length > 0 : Object.keys(issues).length > 0)) {
                this._renderQuality(issues);
                document.getElementById("quality-section").classList.remove("hidden");
            }

            // Show action buttons
            document.getElementById("actions-section").classList.remove("hidden");
        } catch (e) {
            App.error("Upload failed: " + (e.message || "Unknown error"));
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-cog mr-1"></i>Upload &amp; Parse';
        }
    },

    // ── Schema rendering ──────────────────────────────────────────
    _renderSchema(schema) {
        const container = document.getElementById("schema-container");
        if (!container) return;

        // Backend returns {sheet_name: {columns: [...], dtypes: {...}, null_counts: {...}, ...}}
        // or {columns: [...]} directly
        let colData = [];
        if (schema.columns && Array.isArray(schema.columns)) {
            // Direct format: {columns: [col_names], dtypes: {col: type}, ...}
            const columns = schema.columns;
            const dtypes = schema.dtypes || {};
            const nulls = schema.null_counts || {};
            const uniques = schema.unique_counts || {};
            const samples = schema.sample_values || {};
            colData = columns.map(name => ({
                name: name,
                dtype: dtypes[name] || '',
                null_count: nulls[name] || 0,
                unique_count: uniques[name] || 0,
                sample_values: samples[name] || [],
            }));
        } else {
            // Sheet format: {sheet_name: {columns: [...], ...}}
            const sheetData = Object.values(schema);
            if (sheetData.length > 0 && sheetData[0].columns) {
                const s = sheetData[0];
                const dtypes = s.dtypes || {};
                const nulls = s.null_counts || {};
                const uniques = s.unique_counts || {};
                const samples = s.sample_values || {};
                colData = (s.columns || []).map(name => ({
                    name: name,
                    dtype: dtypes[name] || '',
                    null_count: nulls[name] || 0,
                    unique_count: uniques[name] || 0,
                    sample_values: Array.isArray(samples[name]) ? samples[name] : [],
                }));
            }
        }

        if (colData.length === 0) {
            container.innerHTML = '<p class="text-gray-400">No schema information available</p>';
            return;
        }

        let html = '<table class="data-table"><thead><tr>';
        html += '<th>Column</th><th>Type</th><th>Nulls</th><th>Unique</th><th>Sample</th>';
        html += "</tr></thead><tbody>";

        colData.forEach((col) => {
            const name = col.name || "";
            const type = col.dtype || "";
            const nullCount = col.null_count || 0;
            const uniqueCount = col.unique_count || 0;
            const sample = Array.isArray(col.sample_values) ? col.sample_values.slice(0, 3).join(', ') : '--';
            html += `<tr>
                <td class="font-medium">${App.escapeHtml(String(name))}</td>
                <td><span class="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700">${App.escapeHtml(String(type))}</span></td>
                <td>${nullCount}</td>
                <td>${uniqueCount}</td>
                <td class="text-xs text-gray-500 max-w-[200px] truncate" title="${App.escapeHtml(String(sample))}">${App.escapeHtml(String(sample))}</td>
            </tr>`;
        });

        html += "</tbody></table>";
        container.innerHTML = html;
    },

    // ── Quality issues rendering ──────────────────────────────────
    _renderQuality(issues) {
        const container = document.getElementById("quality-container");
        if (!container) return;

        const issueList = Array.isArray(issues)
            ? issues
            : Object.entries(issues).map(([col, details]) => ({
                column: col,
                ...(typeof details === "object" ? details : { issue: details }),
            }));

        if (issueList.length === 0) {
            container.innerHTML = '<p class="text-green-500"><i class="fas fa-check-circle mr-1"></i>No quality issues found</p>';
            return;
        }

        let html = '<div class="space-y-2">';
        issueList.forEach((issue) => {
            const col = issue.column || issue.col || "";
            const msg = issue.message || issue.issue || issue.description || issue.detail || JSON.stringify(issue);
            const severity = issue.severity || issue.level || "warning";
            const iconMap = { error: "fas fa-exclamation-circle text-red-500", warning: "fas fa-exclamation-triangle text-yellow-500", info: "fas fa-info-circle text-blue-500" };
            const icon = iconMap[severity] || iconMap.warning;

            html += `
                <div class="flex items-start gap-2 p-2 rounded-lg bg-gray-50 dark:bg-gray-700/50 text-sm">
                    <i class="${icon} mt-0.5"></i>
                    <div>
                        ${col ? `<span class="font-medium">${App.escapeHtml(String(col))}:</span> ` : ""}
                        <span class="text-gray-600 dark:text-gray-300">${App.escapeHtml(String(msg))}</span>
                    </div>
                </div>`;
        });
        html += "</div>";
        container.innerHTML = html;
    },

    // ── Clean data ────────────────────────────────────────────────
    async cleanData() {
        if (!this._uploadResult) return App.toast("Upload a file first", "error");
        const filename = this._uploadResult.filename || this._file?.name || "";
        const sheet = this._uploadResult.sheet || "";
        try {
            App.info("Cleaning data...");
            await API.clean(filename, sheet);
            App.success("Data cleaned successfully");
        } catch (e) {
            App.error("Clean failed: " + (e.message || "Unknown error"));
        }
    },

    // ── Load to warehouse ─────────────────────────────────────────
    async loadToWarehouse() {
        if (!this._uploadResult) return App.toast("Upload a file first", "error");
        const filename = this._uploadResult.filename || this._file?.name || "";
        const sheet = this._uploadResult.sheet || "";
        const tableName = prompt("Table name (leave blank for filename):", filename.replace(/\.[^.]+$/, "").replace(/[^a-zA-Z0-9_]/g, "_"));

        if (tableName === null) return; // user cancelled
        const table = tableName.trim() || filename.replace(/\.[^.]+$/, "");

        const statusEl = document.getElementById("load-status");
        if (statusEl) {
            statusEl.classList.remove("hidden");
            statusEl.innerHTML = '<div class="flex items-center gap-2 text-sm text-gray-500"><i class="fas fa-spinner fa-spin"></i>Loading to warehouse...</div>';
        }

        try {
            const result = await API.load(filename, sheet, table);
            App.success("Loaded to table: " + table);
            await App.refreshTables();
            if (statusEl) {
                statusEl.innerHTML = `<div class="flex items-center gap-2 text-sm text-green-600 dark:text-green-400"><i class="fas fa-check-circle"></i>Successfully loaded to <strong>${App.escapeHtml(table)}</strong></div>`;
            }
        } catch (e) {
            App.error("Load failed: " + (e.message || "Unknown error"));
            if (statusEl) {
                statusEl.innerHTML = `<div class="flex items-center gap-2 text-sm text-red-600 dark:text-red-400"><i class="fas fa-times-circle"></i>Load failed</div>`;
            }
        }
    },

    // ── Database connector ────────────────────────────────────────
    async testDbConnection() {
        const statusEl = document.getElementById("db-status");
        const request = this._getDbConfig();
        if (!request) return;

        statusEl.innerHTML = '<div class="flex items-center gap-2 text-sm text-gray-500"><i class="fas fa-spinner fa-spin"></i>Testing connection...</div>';

        try {
            const result = await API.connectDatabase(request);
            if (result.success || result.tables) {
                statusEl.innerHTML = '<div class="flex items-center gap-2 text-sm text-green-600 dark:text-green-400"><i class="fas fa-check-circle"></i>Connected successfully</div>';
                document.getElementById("btn-db-import").disabled = false;

                // Populate table selector
                const tables = result.tables || [];
                if (tables.length > 0) {
                    const selectEl = document.getElementById("db-table");
                    selectEl.innerHTML = '<option value="">Select a table...</option>' +
                        tables.map((t) => `<option value="${typeof t === "string" ? t : t.name}">${typeof t === "string" ? t : t.name}</option>`).join("");
                    document.getElementById("db-table-select").classList.remove("hidden");
                }
            } else {
                statusEl.innerHTML = '<div class="flex items-center gap-2 text-sm text-yellow-600"><i class="fas fa-exclamation-triangle"></i>Connection test completed</div>';
            }
        } catch (e) {
            statusEl.innerHTML = `<div class="flex items-center gap-2 text-sm text-red-600 dark:text-red-400"><i class="fas fa-times-circle"></i>${App.escapeHtml(e.message || "Connection failed")}</div>`;
        }
    },

    async importFromDb() {
        const request = this._getDbConfig();
        if (!request) return;
        const tableSelect = document.getElementById("db-table");
        const table = tableSelect ? tableSelect.value : "";
        if (!table) return App.toast("Select a table to import", "error");

        const statusEl = document.getElementById("db-status");
        statusEl.innerHTML = '<div class="flex items-center gap-2 text-sm text-gray-500"><i class="fas fa-spinner fa-spin"></i>Importing data...</div>';

        try {
            const result = await API.importFromDatabase(request, table);
            App.success("Imported table: " + (result.table_name || table));
            await App.refreshTables();
            statusEl.innerHTML = `<div class="flex items-center gap-2 text-sm text-green-600 dark:text-green-400"><i class="fas fa-check-circle"></i>Imported successfully as <strong>${App.escapeHtml(result.table_name || table)}</strong></div>`;
        } catch (e) {
            statusEl.innerHTML = `<div class="flex items-center gap-2 text-sm text-red-600 dark:text-red-400"><i class="fas fa-times-circle"></i>${App.escapeHtml(e.message || "Import failed")}</div>`;
        }
    },

    _getDbConfig() {
        const type = document.getElementById("db-type")?.value;
        const host = document.getElementById("db-host")?.value?.trim();
        const port = document.getElementById("db-port")?.value;
        const dbname = document.getElementById("db-name")?.value?.trim();
        const user = document.getElementById("db-user")?.value?.trim();
        const pass = document.getElementById("db-pass")?.value;

        if (!host || !dbname) {
            App.toast("Host and Database are required", "error");
            return null;
        }

        return { type, host, port: port ? Number(port) : undefined, database: dbname, user, password: pass };
    },

    // ── API import ────────────────────────────────────────────────
    async importFromApi() {
        const url = document.getElementById("api-url")?.value?.trim();
        if (!url) return App.toast("Enter an API URL", "error");

        const method = document.getElementById("api-method")?.value || "GET";
        const token = document.getElementById("api-token")?.value?.trim();
        const headersStr = document.getElementById("api-headers")?.value?.trim();
        const bodyStr = document.getElementById("api-body")?.value?.trim();

        let headers = {};
        if (token) headers["Authorization"] = token;
        if (headersStr) {
            try { headers = { ...headers, ...JSON.parse(headersStr) }; } catch (_) {
                return App.toast("Invalid headers JSON", "error");
            }
        }

        let body = undefined;
        if (bodyStr) {
            try { body = JSON.parse(bodyStr); } catch (_) {
                return App.toast("Invalid body JSON", "error");
            }
        }

        const statusEl = document.getElementById("api-status");
        statusEl.innerHTML = '<div class="flex items-center gap-2 text-sm text-gray-500"><i class="fas fa-spinner fa-spin"></i>Importing from API...</div>';

        try {
            const result = await API.importFromAPI({ url, method, headers, body });
            App.success("API import completed");
            await App.refreshTables();
            statusEl.innerHTML = `<div class="flex items-center gap-2 text-sm text-green-600 dark:text-green-400"><i class="fas fa-check-circle"></i>Imported as <strong>${App.escapeHtml(result.table_name || "table")}</strong></div>`;
        } catch (e) {
            statusEl.innerHTML = `<div class="flex items-center gap-2 text-sm text-red-600 dark:text-red-400"><i class="fas fa-times-circle"></i>${App.escapeHtml(e.message || "API import failed")}</div>`;
        }
    },
};

window.UploadPage = UploadPage;
(window._PAGES = window._PAGES || {})["#upload"] = UploadPage;
