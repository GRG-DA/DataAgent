/* 
   Automation Page " Visual Blueprint Workflow Builder
   Drag-and-drop node canvas with configurable pipeline execution
    */

const AutomationPage = {
  _blueprint: null,
  _savedPipelines: [],
  _currentName: 'Untitled Pipeline',
  _dirty: false,
  _isRunning: false,
  _configSnapshotSaved: false,
  _edgeTimer: null,

  /* "" Lifecycle """"""""""""""""""""""""""" */
  async init() {
    this._savedPipelines = [];
    this._dirty = false;
    this._isRunning = false;
    this._boundKeyHandler = (e) => this._onKeyDown(e);
    const saved = sessionStorage.getItem('bp_state');
    if (saved) {
      try { this._savedPipelines = JSON.parse(saved); } catch (_) { this._savedPipelines = []; }
    }
    document.addEventListener('keydown', this._boundKeyHandler);
  },

  async destroy() {
    document.removeEventListener('keydown', this._boundKeyHandler);
    if (this._blueprint) {
      this._blueprint.destroy();
      this._blueprint = null;
    }
    this._saveSessionState();
  },

  /* "" Render """"""""""""""""""""""""""""""" */
  async render() {
    const pc = document.getElementById('page-content');
    // Automation entitlement pre-check: show an upsell instead of a builder
    // whose server saves would all 403 on the free plan.
    if (window.RBAC && !RBAC.hasFeature('automation')) {
      pc.innerHTML = `
      <div class="max-w-3xl mx-auto animate-fade-in py-10">
        <div class="card text-center enterprise-upsell">
          <i class="fas fa-diagram-project text-2xl enterprise-upsell-icon"></i>
          <h3 class="mt-2 font-semibold">Automation is a Pro feature</h3>
          <p class="text-sm text-secondary mt-1">Design drag-and-drop data pipelines with LLM nodes, scheduled runs, and blueprint sharing.</p>
          <button onclick="RBAC.showUpgradeOverlay('automation')" class="btn btn-primary btn-sm mt-3"><i class="fas fa-crown mr-1"></i>Upgrade to Pro</button>
        </div>
      </div>`;
      return;
    }
    await App.refreshTables();

    pc.innerHTML = `
    <div class="max-w-full bp-page flex flex-col animate-fade-in">
      <!-- Header Toolbar -->
      <div class="bp-header flex items-center justify-between px-4 py-2.5 shrink-0">
        <div class="flex items-center gap-3">
          <div class="bp-brand-chip"><i class="fas fa-diagram-project"></i></div>
          <div class="mr-1">
            <div class="bp-title">Automation</div>
            <div class="bp-subtitle">Build &amp; run data pipelines</div>
          </div>
          <div class="w-px h-6 bg-gray-300 dark:bg-gray-600 mx-1"></div>
          <input id="bp-name" class="bp-name-input"
            value="${App.escapeHtml(this._currentName)}" placeholder="Pipeline name..."
            onchange="AutomationPage._rename(this.value)">
          <span id="bp-status" class="bp-status-pill ${this._dirty ? 'dirty' : ''}">
            <i class="fas ${this._dirty ? 'fa-circle' : 'fa-check-circle'}"></i>${this._dirty ? 'Unsaved' : 'Saved'}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <div class="bp-toolbar-group">
            <button onclick="AutomationPage._newPipeline()" class="btn-toolbar" title="New pipeline">
              <i class="fas fa-file mr-1"></i>New
            </button>
            <button onclick="AutomationPage._savePipeline()" class="btn-toolbar" title="Save pipeline">
              <i class="fas fa-save mr-1"></i>Save
            </button>
            <button onclick="AutomationPage._loadPipeline()" class="btn-toolbar" title="Load pipeline">
              <i class="fas fa-folder-open mr-1"></i>Load
            </button>
            <button onclick="AutomationPage._showTemplates()" class="btn-toolbar" title="Pipeline templates">
              <i class="fas fa-shapes mr-1"></i>Templates
            </button>
            <button onclick="AutomationPage._deleteCurrentPipeline()" class="btn-toolbar btn-toolbar-danger" title="Delete current pipeline">
              <i class="fas fa-trash mr-1"></i>Delete
            </button>
          </div>
          <div class="bp-toolbar-group">
            <button onclick="AutomationPage._undo()" id="bp-undo-btn" class="btn-toolbar" title="Undo (Ctrl+Z)">
              <i class="fas fa-undo mr-1"></i>Undo
            </button>
            <button onclick="AutomationPage._redo()" id="bp-redo-btn" class="btn-toolbar" title="Redo (Ctrl+Y)">
              <i class="fas fa-redo mr-1"></i>Redo
            </button>
          </div>
          <div class="bp-toolbar-group">
            <button onclick="AutomationPage._validatePipeline()" class="btn-toolbar" title="Validate pipeline">
              <i class="fas fa-check-circle mr-1"></i>Validate
            </button>
            <button onclick="AutomationPage._runPipeline()" id="bp-run-btn" class="btn-toolbar bp-run-btn" title="Run pipeline">
              <i class="fas fa-play mr-1"></i>Run
            </button>
            <button onclick="AutomationPage._stopPipeline()" id="bp-stop-btn" class="btn-toolbar-red hidden" title="Stop">
              <i class="fas fa-stop mr-1"></i>Stop
            </button>
          </div>
        </div>
      </div>

      <!-- Inline Error Panel (always in DOM, no Modal needed) -->
      <div id="bp-error-panel" class="bp-error-panel hidden mx-3 mb-3 border border-red-200 dark:border-red-800 rounded-xl shadow-sm overflow-hidden">
        <div class="flex items-center justify-between px-4 py-2.5 bg-red-50 dark:bg-red-900/20 border-b border-red-200 dark:border-red-800">
          <div class="flex items-center gap-2">
            <i class="fas fa-exclamation-circle text-red-500"></i>
            <span id="bp-error-title" class="font-semibold text-sm text-red-700 dark:text-red-400">Pipeline Error</span>
          </div>
          <button onclick="AutomationPage._closeErrorPanel()" class="text-red-400 hover:text-red-600">&times;</button>
        </div>
        <div class="p-4">
          <div id="bp-error-message" class="text-xs text-red-600 dark:text-red-300 mb-3 whitespace-pre-wrap"></div>
          <div id="bp-error-hints" class="mb-3"></div>
          <div class="flex gap-2 mb-3">
            <button id="bp-ai-fix-btn" onclick="AutomationPage._aiDiagnose()"
              class="flex-1 px-4 py-2 bg-gradient-to-r from-purple-500 to-blue-500 text-white rounded-lg font-medium text-sm hover:opacity-90 transition-opacity flex items-center justify-center gap-2">
              <i class="fas fa-wand-magic-sparkles"></i>AI Diagnose &amp; Fix
            </button>
          </div>
          <div id="bp-ai-fix-result" class="hidden"></div>
        </div>
      </div>

      <!-- Three-panel Blueprint Area -->
      <div class="flex flex-1 overflow-hidden" id="bp-workspace">
        <!-- Left: Node Palette (grouped + searchable) -->
        <div class="bp-palette w-56 shrink-0 border-r overflow-y-auto p-3" id="bp-palette">
          <div class="bp-palette-search">
            <i class="fas fa-search"></i>
            <input id="bp-palette-search" type="text" placeholder="Search nodes..."
              oninput="AutomationPage._filterPalette(this.value)">
          </div>
          <div id="bp-palette-items"></div>
        </div>

        <!-- Center: Canvas -->
        <div class="bp-canvas-wrap flex-1 relative">
          <div id="bp-canvas-container" class="absolute inset-0"></div>
          <div class="bp-canvas-toolbar">
            <button onclick="AutomationPage._zoomCanvas(1.2)" title="Zoom in"><i class="fas fa-plus"></i></button>
            <button onclick="AutomationPage._zoomCanvas(0.8)" title="Zoom out"><i class="fas fa-minus"></i></button>
            <button onclick="AutomationPage._fitCanvas()" title="Fit to view"><i class="fas fa-expand"></i></button>
          </div>
          <!-- Empty state shown when canvas is empty -->
          <div id="bp-empty-state" class="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
            <div class="bp-empty-card">
              <div class="bp-empty-icon"><i class="fas fa-diagram-project"></i></div>
              <div class="bp-empty-title">Design your pipeline</div>
              <div class="bp-empty-desc">Drag nodes from the palette onto the canvas, connect them port-to-port, then run to generate tables and dashboards.</div>
              <div class="bp-quick-start">
                <button onclick="AutomationPage._quickStart('data_source')"><i class="fas fa-database"></i>Data Source</button>
                <button onclick="AutomationPage._quickStart('analysis')"><i class="fas fa-microscope"></i>Analysis</button>
                <button onclick="AutomationPage._quickStart('dashboard')"><i class="fas fa-gauge-high"></i>Dashboard</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Right: Node Config Panel (hidden until a node is selected) -->
        <div class="bp-config-panel w-80 shrink-0 border-l overflow-y-auto p-4 hidden" id="bp-config-panel">
          <div id="bp-config-head" class="bp-config-panel-head">
            <div class="bp-config-node-icon"><i id="bp-config-head-icon" class="fas fa-cog"></i></div>
            <div class="bp-config-node-meta">
              <div id="bp-config-head-label" class="bp-config-node-label">Node</div>
              <div id="bp-config-head-type" class="bp-config-node-type">Configure</div>
            </div>
            <button type="button" class="bp-config-deselect" title="Deselect node"
              onclick="AutomationPage._blueprint && AutomationPage._blueprint.deselect()">&times;</button>
          </div>
          <div id="bp-config-form"></div>
        </div>
      </div>
    </div>`;

    // Build palette items
    this._buildPalette();

    // Initialize Blueprint canvas
    this._blueprint = new Blueprint('bp-canvas-container', {
      onNodeSelect: (node) => this._showNodeConfig(node),
      onNodeDeselect: () => this._hideNodeConfig(),
      onChange: (state) => this._onBlueprintChange(state),
    });

    // Restore previous state from session
    this._restoreSessionState();

    // Show empty state if no nodes
    this._updateEmptyState();
    this._updateUndoRedoButtons();
  },

  /* "" Palette Builder (grouped + searchable) """" */
  _buildPalette() {
    const container = document.getElementById('bp-palette-items');
    if (!container) return;

    const groups = [
      {
        name: 'Data',
        icon: 'fa-database',
        items: [
          ['data_source', 'Data Source', '#3b82f6', 'fa-database'],
          ['cleaning', 'Cleaning', '#10b981', 'fa-broom'],
          ['sql', 'SQL Query', '#8b5cf6', 'fa-terminal'],
          ['python', 'Python', '#8b5cf6', 'fa-code'],
          ['filter', 'Filter', '#06b6d4', 'fa-filter'],
          ['join', 'Join', '#f43f5e', 'fa-code-branch'],
          ['aggregate', 'Aggregate', '#f97316', 'fa-calculator'],
          ['quality', 'Quality Check', '#10b981', 'fa-shield-heart'],
        ],
      },
      {
        name: 'Analyze',
        icon: 'fa-microscope',
        items: [
          ['analysis', 'Analysis', '#6366f1', 'fa-microscope'],
          ['chart', 'Chart', '#f59e0b', 'fa-chart-bar'],
          ['insights', 'Insights', '#ec4899', 'fa-lightbulb'],
          ['anomaly', 'Anomaly Detection', '#ef4444', 'fa-exclamation-triangle'],
          ['forecast', 'Forecast', '#14b8a6', 'fa-chart-line'],
          ['stat_test', 'Statistical Test', '#8b5cf6', 'fa-vial'],
          ['regression', 'Regression', '#0ea5e9', 'fa-chart-scatter'],
          ['comparison', 'Comparison', '#f59e0b', 'fa-scale-balanced'],
          ['cohort', 'Cohort / Retention', '#ec4899', 'fa-users'],
          ['ml_model', 'AutoML / Model', '#6366f1', 'fa-robot'],
        ],
      },
      {
        name: 'Deliver',
        icon: 'fa-paper-plane',
        items: [
          ['dashboard', 'Generate Dashboard', '#a855f7', 'fa-gauge-high'],
          ['export', 'Export', '#8b5cf6', 'fa-file-export'],
        ],
      },
    ];

    container.innerHTML = groups.map((group) => `
      <div class="bp-palette-group" data-group="${group.name}">
        <div class="bp-palette-group-title">
          <i class="fas ${group.icon}"></i>${group.name}
          <span class="bp-palette-count">${group.items.length}</span>
        </div>
        ${group.items.map(([type, label, color, icon]) => `
          <div class="bp-palette-item" data-search="${label.toLowerCase()}" onclick="AutomationPage._addNodeAtCanvasCenter('${type}')">
            <span class="bp-palette-chip" style="background:${color}"><i class="fas ${icon}"></i></span>
            <span class="text-sm font-medium">${label}</span>
          </div>
        `).join('')}
      </div>
    `).join('');
  },

  _filterPalette(query) {
    const q = (query || '').trim().toLowerCase();
    const container = document.getElementById('bp-palette-items');
    if (!container) return;
    let anyVisible = false;
    container.querySelectorAll('.bp-palette-group').forEach((group) => {
      let visible = 0;
      group.querySelectorAll('.bp-palette-item').forEach((item) => {
        const match = !q || item.dataset.search.includes(q);
        item.style.display = match ? '' : 'none';
        if (match) visible++;
      });
      group.style.display = visible ? '' : 'none';
      if (visible) anyVisible = true;
    });
    let empty = container.querySelector('.bp-palette-empty');
    if (!anyVisible && !empty) {
      empty = document.createElement('div');
      empty.className = 'bp-palette-empty';
      empty.textContent = 'No nodes match your search';
      container.appendChild(empty);
    } else if (empty) {
      empty.style.display = anyVisible ? 'none' : '';
    }
  },

  _zoomCanvas(factor) {
    if (this._blueprint) this._blueprint.zoomBy(factor);
  },

  _fitCanvas() {
    if (this._blueprint) this._blueprint.fitToContent();
  },

  _quickStart(type) {
    this._addNodeAtCanvasCenter(type);
  },

  _addNodeAtCanvasCenter(type) {
    // Self-heal: if blueprint not initialized yet, create it now
    if (!this._blueprint) {
      const container = document.getElementById('bp-canvas-container');
      if (container) {
        this._blueprint = new Blueprint('bp-canvas-container', {
          onNodeSelect: (node) => this._showNodeConfig(node),
          onNodeDeselect: () => this._hideNodeConfig(),
          onChange: (state) => this._onBlueprintChange(state),
        });
      }
    }

    if (!this._blueprint) {
      App.error('Could not create blueprint canvas " container not found');
      return;
    }

    // Place node at a staggered position
    const existing = this._blueprint.getNodes().length;
    const x = 40 + (existing % 4) * 260;
    const y = 40 + Math.floor(existing / 4) * 120;

    try {
      this._blueprint.addNode(type, x, y);
      this._dirty = true;
      this._updateStatus();
      this._updateEmptyState();
      App.success(`Added ${type.replace(/_/g, ' ')} node`);
    } catch (err) {
      console.error('Failed to add node:', err);
      App.error('Failed to add node: ' + err.message);
    }
  },

  /* "" Node Config Panel """""""""""""""""""" */
  _showNodeConfig(node) {
    const panel = document.getElementById('bp-config-panel');
    const form = document.getElementById('bp-config-form');
    if (!panel || !form) return;
    panel.classList.remove('hidden');
    this._setConfigHead(node);

    form.innerHTML = this._buildConfigForm(node);
    // Reset config snapshot flag so the first actual change saves a history entry
    this._configSnapshotSaved = false;
    this._attachConfigHandlers(node);
  },

  _hideNodeConfig() {
    const panel = document.getElementById('bp-config-panel');
    const form = document.getElementById('bp-config-form');
    if (!panel || !form) return;
    panel.classList.add('hidden');
    form.innerHTML = '';
    this._setConfigHead(null);
  },

  _setConfigHead(node) {
    const head = document.getElementById('bp-config-head');
    if (!head) return;
    if (!node) {
      head.classList.remove('is-visible');
      return;
    }
    const meta = this._nodeTypeMeta(node.type) || { label: node.type, icon: 'fa-cog', group: '' };
    const labelEl = document.getElementById('bp-config-head-label');
    const typeEl = document.getElementById('bp-config-head-type');
    const iconEl = document.getElementById('bp-config-head-icon');
    if (labelEl) labelEl.textContent = node.label || meta.label;
    if (typeEl) typeEl.textContent = meta.group || meta.label;
    if (iconEl) iconEl.className = 'fas ' + meta.icon;
    head.classList.add('is-visible');
  },

  _nodeTypeMeta(type) {
    const map = {
      data_source: { label: 'Data Source', icon: 'fa-database', group: 'Data' },
      cleaning: { label: 'Cleaning', icon: 'fa-broom', group: 'Data' },
      sql: { label: 'SQL Query', icon: 'fa-terminal', group: 'Data' },
      python: { label: 'Python', icon: 'fa-code', group: 'Data' },
      filter: { label: 'Filter', icon: 'fa-filter', group: 'Data' },
      join: { label: 'Join', icon: 'fa-code-branch', group: 'Data' },
      aggregate: { label: 'Aggregate', icon: 'fa-calculator', group: 'Data' },
      quality: { label: 'Quality Check', icon: 'fa-shield-halved', group: 'Data' },
      analysis: { label: 'Analysis', icon: 'fa-microscope', group: 'Analyze' },
      chart: { label: 'Chart', icon: 'fa-chart-simple', group: 'Analyze' },
      insights: { label: 'Insights', icon: 'fa-lightbulb', group: 'Analyze' },
      anomaly: { label: 'Anomaly Detection', icon: 'fa-triangle-exclamation', group: 'Analyze' },
      forecast: { label: 'Forecast', icon: 'fa-chart-line', group: 'Analyze' },
      stat_test: { label: 'Stat Test', icon: 'fa-flask', group: 'Analyze' },
      regression: { label: 'Regression', icon: 'fa-chart-area', group: 'Analyze' },
      comparison: { label: 'Comparison', icon: 'fa-scale-balanced', group: 'Analyze' },
      cohort: { label: 'Cohort', icon: 'fa-users', group: 'Analyze' },
      ml_model: { label: 'ML Model', icon: 'fa-brain', group: 'Analyze' },
      dashboard: { label: 'Dashboard', icon: 'fa-gauge-high', group: 'Output' },
      export: { label: 'Export', icon: 'fa-file-export', group: 'Output' },
    };
    return map[type] || { label: String(type || '').replace(/_/g, ' '), icon: 'fa-cog', group: '' };
  },

  _buildConfigForm(node) {
    const c = node.config;
    switch (node.type) {
      case 'data_source':
        return this._buildDataSourceConfig(node);
      case 'cleaning':
        return this._buildCleaningConfig(node);
      case 'sql':
        return this._buildSqlConfig(node);
      case 'python':
        return this._buildPythonConfig(node);
      case 'analysis':
        return this._buildAnalysisConfig(node);
      case 'chart':
        return this._buildChartConfig(node);
      case 'filter':
        return this._buildFilterConfig(node);
      case 'join':
        return this._buildJoinConfig(node);
      case 'aggregate':
        return this._buildAggregateConfig(node);
      case 'anomaly':
        return this._buildAnomalyConfig(node);
      case 'forecast':
        return this._buildForecastConfig(node);
      case 'stat_test':
        return this._buildStatTestConfig(node);
      case 'regression':
        return this._buildRegressionConfig(node);
      case 'quality':
        return this._buildQualityConfig(node);
      case 'comparison':
        return this._buildComparisonConfig(node);
      case 'cohort':
        return this._buildCohortConfig(node);
      case 'ml_model':
        return this._buildMlModelConfig(node);
      case 'dashboard':
        return this._buildDashboardConfig(node);
      case 'export':
        return this._buildExportConfig(node);
      case 'insights':
        return this._buildInsightsConfig(node);
      default:
        return '<p class="text-xs text-gray-400">No configuration available</p>';
    }
  },

  _buildDataSourceConfig(node) {
    const tables = App.state.tables || {};
    const tableNames = Object.keys(tables);
    return `
      <div class="bp-config-section">
        <h4>Table</h4>
        <select class="bp-config-select" data-key="table_name">
          <option value="">Select a table...</option>
          ${tableNames.map((t) => `<option value="${App.escapeHtml(t)}" ${node.config.table_name === t ? 'selected' : ''}>${App.escapeHtml(t)}</option>`).join('')}
        </select>
      </div>
      <div class="bp-config-section">
        <h4>Filter (SQL WHERE)</h4>
        <input class="bp-config-input" type="text" data-key="filter" value="${App.escapeHtml(node.config.filter || '')}" placeholder="e.g., year > 2023">
      </div>
      <div class="bp-config-section">
        <h4>Row Limit</h4>
        <input class="bp-config-input" type="number" data-key="limit" value="${node.config.limit || 1000}" min="0" step="100">
      </div>`;
  },

  _buildCleaningConfig(node) {
    const c = node.config;
    const nullStrategies = ['drop', 'fill_mean', 'fill_median', 'fill_mode', 'fill_constant', 'fill_forward', 'fill_backward', 'interpolate'];
    return `
      <div class="bp-config-section">
        <h4>Options</h4>
        <label class="bp-config-checkbox-label">
          <input type="checkbox" class="bp-config-checkbox" data-key="remove_duplicates" ${c.remove_duplicates ? 'checked' : ''}>
          Remove duplicates
        </label>
      </div>
      <div class="bp-config-section">
        <h4>Null Handling Strategy</h4>
        <select class="bp-config-select" data-key="null_strategy">
          ${nullStrategies.map((s) => `<option value="${s}" ${c.null_strategy === s ? 'selected' : ''}>${s}</option>`).join('')}
        </select>
      </div>
      <div class="bp-config-section">
        <h4>Drop Columns</h4>
        <input class="bp-config-input" type="text" data-key="drop_columns" value="${App.escapeHtml((c.drop_columns || []).join(', '))}" placeholder="col1, col2">
        <p class="bp-config-help">Comma-separated column names</p>
      </div>`;
  },

  _buildSqlConfig(node) {
    const c = node.config;
    const SQL_TEMPLATES = [
      { label: 'All columns (SELECT *)', sql: 'SELECT * FROM input' },
      { label: 'Top N by first column', sql: 'SELECT * FROM input ORDER BY 1 DESC LIMIT 10' },
      { label: 'Group and aggregate', sql: 'SELECT col_a, COUNT(*) as count, SUM(col_b) as total FROM input GROUP BY col_a ORDER BY total DESC' },
      { label: 'Filter by date range', sql: 'SELECT * FROM input WHERE date_col BETWEEN \'2024-01-01\' AND \'2024-12-31\'' },
      { label: 'Find duplicates', sql: 'SELECT col_a, col_b, COUNT(*) as dup_count FROM input GROUP BY col_a, col_b HAVING COUNT(*) > 1' },
      { label: 'Join two tables', sql: 'SELECT a.*, b.* FROM input a JOIN other_table b ON a.key_col = b.key_col' },
    ];
    return `
      <div class="bp-config-section">
        <h4>Quick Templates</h4>
        <select class="bp-config-select" onchange="AutomationPage._applySqlTemplate('${node.id}', this.value)">
          <option value="">Select a template...</option>
          ${SQL_TEMPLATES.map((t) => `<option value="${t.sql}">${t.label}</option>`).join('')}
        </select>
      </div>
      <div class="bp-config-section">
        <h4>Describe what query you need (LLM generates SQL)</h4>
        <textarea class="bp-config-textarea" data-key="prompt" rows="3"
          placeholder="e.g., Top 10 products by revenue">${App.escapeHtml(c.prompt || '')}</textarea>
      </div>
      <div class="bp-config-section">
        <button onclick="AutomationPage._generateSqlCode('${node.id}')" id="bp-gen-sql-${node.id}"
          class="bp-config-action">
          <i class="fas fa-wand-magic-sparkles"></i>Generate with LLM
        </button>
      </div>
      <div class="bp-config-section">
        <h4>SQL Query</h4>
        <textarea class="bp-config-textarea" data-key="query" rows="5" spellcheck="false"
          placeholder="SELECT * FROM input">${App.escapeHtml(c.query || 'SELECT * FROM input')}</textarea>
        <p class="bp-config-help">Use <code>input</code> as table name</p>
      </div>
      <div class="bp-config-section">
        <h4>Row Limit</h4>
        <input class="bp-config-input" type="number" data-key="limit" value="${c.limit || 1000}" min="0">
      </div>`;
  },

  _buildPythonConfig(node) {
    const c = node.config;
    const PY_TEMPLATES = [
      { label: 'Preview data', code: 'print(df.head(10))\\nprint(df.shape)\\nprint(df.dtypes)' },
      { label: 'Summary statistics', code: 'print(df.describe())\\nprint("\\nMissing values:")\\nprint(df.isnull().sum())' },
      { label: 'Group by and aggregate', code: 'result = df.groupby("group_col").agg({"metric_col": ["count", "mean", "sum"]}).round(2)\\nprint(result)' },
      { label: 'Correlation matrix', code: 'import pandas as pd\\ncorr = df.select_dtypes(include="number").corr().round(3)\\nprint(corr)' },
      { label: 'Distribution plot', code: 'import matplotlib.pyplot as plt\\nnumeric_cols = df.select_dtypes(include="number").columns\\nfor col in numeric_cols[:4]:\\n    df[col].hist(bins=30, title=col)\\n    plt.tight_layout()\\n    plt.savefig(f"distribution_{col}.png")\\n    plt.close()\\nprint("Saved distribution plots")' },
      { label: 'Clean data', code: 'df = df.dropna()\\ndf = df.drop_duplicates()\\nfor col in df.select_dtypes(include="object").columns:\\n    df[col] = df[col].str.strip().str.lower()\\nprint(f"Cleaned: {df.shape}")' },
    ];
    return `
      <div class="bp-config-section">
        <h4>Quick Templates</h4>
        <select class="bp-config-select" onchange="AutomationPage._applyPyTemplate('${node.id}', this.value)">
          <option value="">Select a template...</option>
          ${PY_TEMPLATES.map((t) => `<option value="${t.label}">${t.label}</option>`).join('')}
        </select>
      </div>
      <div class="bp-config-section">
        <h4>Describe what you want (LLM generates code)</h4>
        <textarea class="bp-config-textarea" data-key="prompt" rows="3"
          placeholder="e.g., Average sales by region, find top 3">${App.escapeHtml(c.prompt || '')}</textarea>
      </div>
      <div class="bp-config-section">
        <button onclick="AutomationPage._generatePythonCode('${node.id}')" id="bp-gen-code-${node.id}"
          class="bp-config-action">
          <i class="fas fa-wand-magic-sparkles"></i>Generate with LLM
        </button>
      </div>
      <div class="bp-config-section">
        <h4>Python Code</h4>
        <textarea class="bp-config-textarea font-mono text-xs" data-key="code" rows="6" spellcheck="false"
          placeholder="Write code or use templates/LLM above">${App.escapeHtml(c.code || '')}</textarea>
        <p class="bp-config-help"><code>df</code> is the input DataFrame</p>
      </div>
      <div class="bp-config-section">
        <h4>Result Variable</h4>
        <input class="bp-config-input font-mono text-xs" type="text" data-key="var_name"
          value="${App.escapeHtml(c.var_name || 'df')}" placeholder="df">
        <p class="bp-config-help">Variable holding the output DataFrame (falls back to <code>result</code>, then the input)</p>
      </div>`;
  },

  _buildAnalysisConfig(node) {
    const c = node.config;
    const analysisTypes = ['statistics', 'correlation'];
    return `
      <div class="bp-config-section">
        <h4>Analysis Type</h4>
        <select class="bp-config-select" data-key="analysis_type">
          ${analysisTypes.map((t) => `<option value="${t}" ${c.analysis_type === t ? 'selected' : ''}>${t.replace(/_/g, ' ')}</option>`).join('')}
        </select>
      </div>
      <div class="bp-config-section">
        <h4>Target Column</h4>
        <input class="bp-config-input" type="text" data-key="target_col" value="${App.escapeHtml(c.target_col || '')}" placeholder="Column name">
      </div>
      <div class="bp-config-section">
        <h4>Feature Columns</h4>
        <input class="bp-config-input" type="text" data-key="feature_cols" value="${App.escapeHtml((c.feature_cols || []).join(', '))}" placeholder="col1, col2">
      </div>`;
  },

  _buildChartConfig(node) {
    const c = node.config;
    const chartTypes = ['bar', 'line', 'pie', 'scatter', 'heatmap', 'area', 'histogram'];
    const exportTargets = ['none', 'powerbi', 'tableau'];
    const recs = c.recommended_charts || [];
    const recCards = recs.length > 0 ? recs.map((r, i) => `
      <div class="p-2 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-900/20 mb-2 text-xs">
        <div class="flex items-start justify-between gap-2">
          <div class="flex-1 min-w-0">
            <span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium bg-indigo-500 text-white">${App.escapeHtml(r.chart_type || '')}</span>
            ${r.title ? `<span class="ml-1 font-medium">${App.escapeHtml(r.title)}</span>` : ''}
            <div class="text-gray-500 mt-1">
              ${r.x_axis ? `<span class="mr-2"><strong>X:</strong> ${App.escapeHtml(r.x_axis)}</span>` : ''}
              ${r.y_axis ? `<span class="mr-2"><strong>Y:</strong> ${App.escapeHtml(r.y_axis)}</span>` : ''}
              ${r.color ? `<span><strong>Color:</strong> ${App.escapeHtml(r.color)}</span>` : ''}
            </div>
            ${r.reasoning ? `<div class="text-gray-400 mt-0.5 italic">${App.escapeHtml(r.reasoning)}</div>` : ''}
          </div>
          <button onclick="AutomationPage._useChartRecommendation('${node.id}', ${i})"
            class="shrink-0 bg-indigo-500 hover:bg-indigo-600 text-white px-2 py-1 rounded text-[10px] font-medium">Use</button>
        </div>
      </div>
    `).join('') : '';

    return `
      <!-- AI Chart Recommendations -->
      <div class="bp-config-group bp-config-section">
        <h4><i class="fas fa-wand-magic-sparkles text-indigo-500 mr-1"></i>AI Generate Charts</h4>
        ${recCards ? `<div class="mb-3">${recCards}</div>` : '<div class="bp-config-help">Let AI analyze your data and suggest the best charts</div>'}
        <textarea id="chart-llm-prompt-${node.id}" class="bp-config-textarea" rows="2"
          placeholder="Optional focus e.g., Focus on revenue trends">${App.escapeHtml(c._llmPrompt || '')}</textarea>
        <button onclick="AutomationPage._generateLLMChartRecommendations('${node.id}')"
          id="chart-llm-btn-${node.id}"
          class="bp-config-action">
          <i class="fas fa-wand-magic-sparkles"></i>Analyze & Recommend Charts
        </button>
        <div id="chart-llm-status-${node.id}" class="mt-1 hidden"></div>
      </div>

      <!-- Manual Config -->
      <div class="bp-config-section">
        <h4>Chart Type</h4>
        <select class="bp-config-select" data-key="chart_type">
          ${chartTypes.map((t) => `<option value="${t}" ${c.chart_type === t ? 'selected' : ''}>${t}</option>`).join('')}
        </select>
      </div>
      <div class="bp-config-section">
        <h4>X-axis Column</h4>
        <input class="bp-config-input" type="text" data-key="x_col" value="${App.escapeHtml(c.x_col || '')}" placeholder="Column name">
      </div>
      <div class="bp-config-section">
        <h4>Y-axis Column</h4>
        <input class="bp-config-input" type="text" data-key="y_col" value="${App.escapeHtml(c.y_col || '')}" placeholder="Column name">
      </div>
      <div class="bp-config-section">
        <h4>Group/Color Column</h4>
        <input class="bp-config-input" type="text" data-key="group_col" value="${App.escapeHtml(c.group_col || '')}" placeholder="Optional">
      </div>
      <div class="bp-config-section">
        <h4>Chart Title</h4>
        <input class="bp-config-input" type="text" data-key="title" value="${App.escapeHtml(c.title || '')}" placeholder="My Chart">
      </div>
      <div class="bp-config-section">
        <h4>Export for Presentation</h4>
        <select class="bp-config-select" data-key="export_target">
          ${exportTargets.map((t) => `<option value="${t}" ${(c.export_target || 'none') === t ? 'selected' : ''}>${t === 'none' ? 'None' : t === 'powerbi' ? 'Power BI' : 'Tableau'}</option>`).join('')}
        </select>
      </div>`;
  },

  _buildInsightsConfig(node) {
    return `
      <div class="bp-config-section">
        <h4>Insights Prompt</h4>
        <textarea class="bp-config-textarea" data-key="prompt" rows="6" placeholder="What insights to generate?">${App.escapeHtml(node.config.prompt || 'Summarize key trends and patterns in this data.')}</textarea>
      </div>
      <div class="bp-config-section">
        <p class="bp-config-help">Insights will be generated using the LLM agent after pipeline execution.</p>
      </div>`;
  },

  _buildFilterConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Column</h4><input class="bp-config-input" data-key="column" value="' + App.escapeHtml(c.column||'') + '" placeholder="column_name"></div><div class="bp-config-section"><h4>Operator</h4><select class="bp-config-select" data-key="operator"><option value="equals"' + (c.operator==='equals'?' selected':'') + '>Equals</option><option value="greater_than"' + (c.operator==='greater_than'?' selected':'') + '>Greater than</option><option value="less_than"' + (c.operator==='less_than'?' selected':'') + '>Less than</option><option value="contains"' + (c.operator==='contains'?' selected':'') + '>Contains</option></select></div><div class="bp-config-section"><h4>Value</h4><input class="bp-config-input" data-key="value" value="' + App.escapeHtml(c.value||'') + '"></div>';
  },

  _buildJoinConfig(node) {
    var c = node.config;
    // Candidate right-hand inputs: every other node that could feed this join
    var nodes = (this._blueprint && this._blueprint.getNodes()) || [];
    var candidates = nodes.filter(function(n) {
      return n.type !== 'join' && n.id !== node.id;
    }).map(function(n) { return { id: n.id, label: n.label + ' (' + n.id + ')' }; });
    var opts = '<option value="">(second connected edge)</option>' + candidates.map(function(n) {
      return '<option value="' + n.id + '"' + (c.second_input === n.id ? ' selected' : '') + '>' + App.escapeHtml(n.label) + '</option>';
    }).join('');
    return '<div class="bp-config-section"><h4>Join Type</h4><select class="bp-config-select" data-key="join_type"><option value="inner"' + (c.join_type==='inner'?' selected':'') + '>Inner</option><option value="left"' + (c.join_type==='left'?' selected':'') + '>Left</option><option value="right"' + (c.join_type==='right'?' selected':'') + '>Right</option><option value="outer"' + (c.join_type==='outer'?' selected':'') + '>Outer</option></select></div>'
      + '<div class="bp-config-section"><h4>Left Key</h4><input class="bp-config-input" data-key="left_key" value="' + App.escapeHtml(c.left_key||'') + '"></div>'
      + '<div class="bp-config-section"><h4>Right Key</h4><input class="bp-config-input" data-key="right_key" value="' + App.escapeHtml(c.right_key||'') + '"></div>'
      + '<div class="bp-config-section"><h4>Right Input Source</h4><select class="bp-config-select" data-key="second_input">' + opts + '</select><p class="bp-config-help">Pick which upstream node is the second dataset, or leave on auto (second connection)</p></div>';
  },

  _buildAggregateConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Group By</h4><input class="bp-config-input" data-key="group_by" value="' + App.escapeHtml(c.group_by||'') + '"></div><div class="bp-config-section"><h4>Aggregate Column</h4><input class="bp-config-input" data-key="aggregate_col" value="' + App.escapeHtml(c.aggregate_col||'') + '"></div><div class="bp-config-section"><h4>Function</h4><select class="bp-config-select" data-key="aggregate_fn"><option value="sum"' + (c.aggregate_fn==='sum'?' selected':'') + '>SUM</option><option value="avg"' + (c.aggregate_fn==='avg'?' selected':'') + '>AVG</option><option value="count"' + (c.aggregate_fn==='count'?' selected':'') + '>COUNT</option><option value="min"' + (c.aggregate_fn==='min'?' selected':'') + '>MIN</option><option value="max"' + (c.aggregate_fn==='max'?' selected':'') + '>MAX</option></select></div>';
  },

  _buildAnomalyConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Column</h4><input class="bp-config-input" data-key="column" value="' + App.escapeHtml(c.column||'') + '" placeholder="numeric column"></div>'
      + '<div class="bp-config-section"><h4>Method</h4><select class="bp-config-select" data-key="method"><option value="iqr"' + (c.method==='iqr'?' selected':'') + '>IQR (interquartile range)</option><option value="zscore"' + (c.method==='zscore'?' selected':'') + '>Z-score</option><option value="change_point"' + (c.method==='change_point'?' selected':'') + '>Change point</option></select></div>'
      + '<div class="bp-config-section"><h4>Threshold</h4><input class="bp-config-input" type="number" step="0.1" data-key="threshold" value="' + App.escapeHtml(c.threshold||1.5) + '"></div>';
  },

  _buildForecastConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Date Column</h4><input class="bp-config-input" data-key="date_col" value="' + App.escapeHtml(c.date_col||'') + '" placeholder="date column"></div>'
      + '<div class="bp-config-section"><h4>Value Column</h4><input class="bp-config-input" data-key="value_col" value="' + App.escapeHtml(c.value_col||'') + '" placeholder="numeric column"></div>'
      + '<div class="bp-config-section"><h4>Horizon (periods)</h4><input class="bp-config-input" type="number" min="1" data-key="horizon" value="' + App.escapeHtml(c.horizon||12) + '"></div>'
      + '<div class="bp-config-section"><h4>Method</h4><select class="bp-config-select" data-key="method"><option value="auto"' + (c.method==='auto'?' selected':'') + '>Auto (best available)</option><option value="linear"' + (c.method==='linear'?' selected':'') + '>Linear trend + seasonality</option><option value="naive"' + (c.method==='naive'?' selected':'') + '>Naive</option><option value="seasonal_naive"' + (c.method==='seasonal_naive'?' selected':'') + '>Seasonal naive</option></select></div>';
  },

  _buildStatTestConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Test Type</h4><select class="bp-config-select" data-key="test_type"><option value="ttest"' + (c.test_type==='ttest'?' selected':'') + '>T-test (two groups)</option><option value="paired_ttest"' + (c.test_type==='paired_ttest'?' selected':'') + '>Paired t-test</option><option value="mannwhitney"' + (c.test_type==='mannwhitney'?' selected':'') + '>Mann-Whitney U</option><option value="chi2"' + (c.test_type==='chi2'?' selected':'') + '>Chi-square</option><option value="anova"' + (c.test_type==='anova'?' selected':'') + '>ANOVA</option><option value="pearsonr"' + (c.test_type==='pearsonr'?' selected':'') + '>Pearson correlation</option></select></div>'
      + '<div class="bp-config-section"><h4>Column A</h4><input class="bp-config-input" data-key="column_a" value="' + App.escapeHtml(c.column_a||'') + '" placeholder="metric / category column"></div>'
      + '<div class="bp-config-section"><h4>Column B</h4><input class="bp-config-input" data-key="column_b" value="' + App.escapeHtml(c.column_b||'') + '" placeholder="optional second column"></div>'
      + '<div class="bp-config-section"><h4>Group Column</h4><input class="bp-config-input" data-key="group_col" value="' + App.escapeHtml(c.group_col||'') + '" placeholder="optional grouping column"></div>'
      + '<div class="bp-config-section"><h4>Alpha</h4><input class="bp-config-input" type="number" step="0.01" data-key="alpha" value="' + App.escapeHtml(c.alpha||0.05) + '"></div>';
  },

  _buildRegressionConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Target Column</h4><input class="bp-config-input" data-key="target_col" value="' + App.escapeHtml(c.target_col||'') + '" placeholder="target column"></div>'
      + '<div class="bp-config-section"><h4>Feature Columns</h4><input class="bp-config-input" data-key="feature_cols" value="' + App.escapeHtml(c.feature_cols||'') + '" placeholder="col1, col2 (blank = auto)"></div>'
      + '<div class="bp-config-section"><h4>Regression Type</h4><select class="bp-config-select" data-key="regression_type"><option value="linear"' + (c.regression_type==='linear'?' selected':'') + '>Linear</option><option value="polynomial"' + (c.regression_type==='polynomial'?' selected':'') + '>Polynomial</option><option value="logistic"' + (c.regression_type==='logistic'?' selected':'') + '>Logistic</option></select></div>';
  },

  _buildQualityConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Table Name</h4><input class="bp-config-input" data-key="table_name" value="' + App.escapeHtml(c.table_name||'') + '" placeholder="optional label"></div>'
      + '<div class="bp-config-section"><p class="bp-config-help">Computes a quality score: completeness, uniqueness, validity, consistency, and overall health.</p></div>';
  },

  _buildComparisonConfig(node) {
    var c = node.config;
    var nodes = (this._blueprint && this._blueprint.getNodes()) || [];
    var candidates = nodes.filter(function(n) {
      return n.type !== 'comparison' && n.id !== node.id;
    }).map(function(n) { return { id: n.id, label: n.label + ' (' + n.id + ')' }; });
    var opts = '<option value="">(second connected edge)</option>' + candidates.map(function(n) {
      return '<option value="' + n.id + '"' + (c.second_input === n.id ? ' selected' : '') + '>' + App.escapeHtml(n.label) + '</option>';
    }).join('');
    return '<div class="bp-config-section"><h4>Comparison Type</h4><select class="bp-config-select" data-key="kind"><option value="periods"' + (c.kind==='periods'?' selected':'') + '>Period over period</option><option value="diff"' + (c.kind==='diff'?' selected':'') + '>Table diff (two datasets)</option></select></div>'
      + '<div class="bp-config-section"><h4>Date Column</h4><input class="bp-config-input" data-key="date_col" value="' + App.escapeHtml(c.date_col||'') + '" placeholder="for period comparison"></div>'
      + '<div class="bp-config-section"><h4>Metric Column</h4><input class="bp-config-input" data-key="metric_col" value="' + App.escapeHtml(c.metric_col||'') + '" placeholder="numeric column"></div>'
      + '<div class="bp-config-section"><h4>Group Column</h4><input class="bp-config-input" data-key="group_col" value="' + App.escapeHtml(c.group_col||'') + '" placeholder="optional"></div>'
      + '<div class="bp-config-section"><h4>Period Type</h4><select class="bp-config-select" data-key="period_type"><option value="month"' + (c.period_type==='month'?' selected':'') + '>Month</option><option value="week"' + (c.period_type==='week'?' selected':'') + '>Week</option><option value="day"' + (c.period_type==='day'?' selected':'') + '>Day</option><option value="quarter"' + (c.period_type==='quarter'?' selected':'') + '>Quarter</option><option value="year"' + (c.period_type==='year'?' selected':'') + '>Year</option></select></div>'
      + '<div class="bp-config-section"><h4>Key Column (diff)</h4><input class="bp-config-input" data-key="key_column" value="' + App.escapeHtml(c.key_column||'') + '" placeholder="optional join key"></div>'
      + '<div class="bp-config-section"><h4>Second Dataset (diff)</h4><select class="bp-config-select" data-key="second_input">' + opts + '</select><p class="bp-config-help">Pick which upstream node is the second dataset, or leave on auto (second connection)</p></div>';
  },

  _buildCohortConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Analysis Type</h4><select class="bp-config-select" data-key="kind"><option value="retention"' + (c.kind==='retention'?' selected':'') + '>Cohort retention</option><option value="churn"' + (c.kind==='churn'?' selected':'') + '>Churn analysis</option></select></div>'
      + '<div class="bp-config-section"><h4>User ID Column</h4><input class="bp-config-input" data-key="user_id_col" value="' + App.escapeHtml(c.user_id_col||'') + '" placeholder="user / customer id"></div>'
      + '<div class="bp-config-section"><h4>Date Column</h4><input class="bp-config-input" data-key="date_col" value="' + App.escapeHtml(c.date_col||'') + '" placeholder="event date"></div>'
      + '<div class="bp-config-section"><h4>Cohort Period</h4><select class="bp-config-select" data-key="cohort_period"><option value="month"' + (c.cohort_period==='month'?' selected':'') + '>Month</option><option value="week"' + (c.cohort_period==='week'?' selected':'') + '>Week</option><option value="day"' + (c.cohort_period==='day'?' selected':'') + '>Day</option></select></div>'
      + '<div class="bp-config-section"><h4>Revenue Column</h4><input class="bp-config-input" data-key="revenue_col" value="' + App.escapeHtml(c.revenue_col||'') + '" placeholder="optional"></div>'
      + '<div class="bp-config-section"><h4>Max Periods</h4><input class="bp-config-input" type="number" min="1" data-key="max_periods" value="' + App.escapeHtml(c.max_periods||12) + '"></div>'
      + '<div class="bp-config-section"><h4>Inactivity Days (churn)</h4><input class="bp-config-input" type="number" min="1" data-key="inactivity_days" value="' + App.escapeHtml(c.inactivity_days||30) + '"></div>';
  },

  _buildMlModelConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Target Column</h4><input class="bp-config-input" data-key="target_col" value="' + App.escapeHtml(c.target_col||'') + '" placeholder="target column"></div>'
      + '<div class="bp-config-section"><h4>Task</h4><select class="bp-config-select" data-key="task"><option value="auto"' + (c.task==='auto'?' selected':'') + '>Auto analyze (full pipeline)</option><option value="compare"' + (c.task==='compare'?' selected':'') + '>Compare linear vs polynomial</option></select></div>';
  },

  _buildDashboardConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Dashboard Name</h4><input class="bp-config-input" data-key="dashboard_name" value="' + App.escapeHtml(c.dashboard_name||'') + '" placeholder="My Dashboard"></div>'
      + '<div class="bp-config-section"><h4>KPI Focus</h4><input class="bp-config-input" data-key="kpi_focus" value="' + App.escapeHtml(c.kpi_focus||'') + '" placeholder="e.g. revenue, growth"></div>'
      + '<div class="bp-config-section"><h4>Sections</h4><input class="bp-config-input" data-key="sections" value="' + App.escapeHtml(c.sections||'overview') + '" placeholder="overview, trends, breakdown"></div>'
      + '<div class="bp-config-section"><p class="bp-config-help">After the pipeline runs, a dashboard is auto-built from the output table and saved to Dashboard Studio.</p></div>';
  },

  _buildExportConfig(node) {
    var c = node.config;
    return '<div class="bp-config-section"><h4>Format</h4><select class="bp-config-select" data-key="format"><option value="csv"' + (c.format==='csv'?' selected':'') + '>CSV</option><option value="xlsx"' + (c.format==='xlsx'?' selected':'') + '>Excel</option><option value="parquet"' + (c.format==='parquet'?' selected':'') + '>Parquet</option></select></div><div class="bp-config-section"><h4>Filename</h4><input class="bp-config-input" data-key="filename" value="' + App.escapeHtml(c.filename||'') + '"></div>';
  },

  _attachConfigHandlers(node) {
    const form = document.getElementById('bp-config-form');
    if (!form) return;

    // Handle all input types
    form.querySelectorAll('[data-key]').forEach((el) => {
      const key = el.dataset.key;
      if (!el.name) el.name = key;
      if (!el.id) el.id = `bp-cfg-${key}`;

      const update = () => {
        // Save a snapshot on the FIRST change to capture pre-edit state
        if (!AutomationPage._configSnapshotSaved) {
          if (AutomationPage._blueprint) {
            AutomationPage._blueprint.saveSnapshot();
          }
          AutomationPage._configSnapshotSaved = true;
        }

        let value;
        if (el.type === 'checkbox') {
          value = el.checked;
        } else if (el.type === 'number') {
          value = parseInt(el.value) || 0;
        } else if (el.tagName === 'SELECT') {
          value = el.value;
        } else if (el.tagName === 'TEXTAREA' && key === 'feature_cols') {
          value = el.value.split(',').map((s) => s.trim()).filter(Boolean);
        } else if (el.tagName === 'TEXTAREA' && key === 'drop_columns') {
          value = el.value.split(',').map((s) => s.trim()).filter(Boolean);
        } else {
          value = el.value;
        }

        this._blueprint.updateNodeConfig(node.id, { [key]: value });
        this._blueprint._rerenderNodeBody(node.id);
        this._dirty = true;
        this._updateStatus();
      };

      el.addEventListener('change', update);
      el.addEventListener('input', el.tagName === 'TEXTAREA' ? update : null);
    });

    // A11y: give remaining form controls a name/id (quick-template selects, etc.)
    let orphanIdx = 0;
    form.querySelectorAll('input, select, textarea').forEach((el) => {
      if (el.type === 'hidden' || el.type === 'button' || el.type === 'submit') return;
      if (el.name) return;
      orphanIdx += 1;
      el.name = `bp_cfg_field_${orphanIdx}`;
      if (!el.id) el.id = `bp-cfg-field-${orphanIdx}`;
    });
  },

  /* "" Python LLM Code Generation """"""" */
  async _generatePythonCode(nodeId) {
    const node = this._blueprint.getNode(nodeId);
    if (!node || !node.config.prompt) {
      App.error('Enter a prompt describing what you want');
      return;
    }

    const btn = document.getElementById('bp-gen-code-' + nodeId);
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Generating...';
    }

    try {
      const result = await API.generatePythonCode(node.config.prompt);
      if (result && result.success && result.code) {
        this._blueprint.updateNodeConfig(nodeId, { code: result.code });
        // Update the code textarea in the config panel
        const codeTextarea = document.querySelector('#bp-config-form textarea[data-key="code"]');
        if (codeTextarea) codeTextarea.value = result.code;
        // Update node summary
        this._blueprint._rerenderNodeBody(nodeId);
        App.success('Python code generated!');
      } else {
        App.error('Code generation failed: ' + (result?.error || 'Unknown error'));
      }
    } catch (err) {
      App.error('Code generation error: ' + (err.message || 'Check if LLM is running'));
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-wand-magic-sparkles mr-1"></i>Generate Code with LLM';
      }
    }
  },

  /* "" SQL Template Helpers """""""""""""" */
  _applySqlTemplate(nodeId, sql) {
    if (!sql || !this._blueprint) return;
    this._blueprint.updateNodeConfig(nodeId, { query: sql });
    const queryTextarea = document.querySelector('#bp-config-form textarea[data-key="query"]');
    if (queryTextarea) queryTextarea.value = sql;
    this._blueprint._rerenderNodeBody(nodeId);
  },

  _applyPyTemplate(nodeId, label) {
    if (!label || !this._blueprint) return;
    const templates = {
      'Preview data': 'print(df.head(10))\nprint(df.shape)\nprint(df.dtypes)',
      'Summary statistics': 'print(df.describe())\nprint("\\nMissing values:")\nprint(df.isnull().sum())',
      'Group by and aggregate': 'result = df.groupby("group_col").agg({"metric_col": ["count", "mean", "sum"]}).round(2)\nprint(result)',
      'Correlation matrix': 'import pandas as pd\ncorr = df.select_dtypes(include="number").corr().round(3)\nprint(corr)',
      'Distribution plot': 'import matplotlib.pyplot as plt\nnumeric_cols = df.select_dtypes(include="number").columns\nfor col in numeric_cols[:4]:\n    df[col].hist(bins=30, title=col)\n    plt.tight_layout()\n    plt.savefig(f"distribution_{col}.png")\n    plt.close()\nprint("Saved distribution plots")',
      'Clean data': 'df = df.dropna()\ndf = df.drop_duplicates()\nfor col in df.select_dtypes(include="object").columns:\n    df[col] = df[col].str.strip().str.lower()\nprint(f"Cleaned: {df.shape}")',
    };
    const code = templates[label] || label;
    this._blueprint.updateNodeConfig(nodeId, { code: code });
    const codeTextarea = document.querySelector('#bp-config-form textarea[data-key="code"]');
    if (codeTextarea) codeTextarea.value = code;
    this._blueprint._rerenderNodeBody(nodeId);
  },

  /* "" SQL LLM Code Generation """"""""""" */
  async _generateSqlCode(nodeId) {
    const node = this._blueprint.getNode(nodeId);
    if (!node || !node.config.prompt) {
      App.error('Enter a prompt describing the query you need');
      return;
    }

    const btn = document.getElementById('bp-gen-sql-' + nodeId);
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Generating...';
    }

    try {
      const result = await API.generateSqlCode(node.config.prompt);
      if (result && result.success && result.code) {
        this._blueprint.updateNodeConfig(nodeId, { query: result.code });
        const queryTextarea = document.querySelector('#bp-config-form textarea[data-key="query"]');
        if (queryTextarea) queryTextarea.value = result.code;
        this._blueprint._rerenderNodeBody(nodeId);
        App.success('SQL query generated!');
      } else {
        App.error('SQL generation failed: ' + (result?.error || 'Unknown error'));
      }
    } catch (err) {
      App.error('SQL generation error: ' + (err.message || 'Check if LLM is running'));
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-wand-magic-sparkles mr-1"></i>Generate with LLM';
      }
    }
  },

  /* "" LLM Chart Recommendation """""""""" */
  _findUpstreamTableName(nodeId) {
    // Walk edges backward to find the data_source table name
    const edges = this._blueprint.getEdges();
    const visited = new Set();
    let current = nodeId;
    while (current) {
      if (visited.has(current)) return '';
      visited.add(current);
      const edge = edges.find((e) => e.to === current);
      if (!edge) return '';
      const upstream = this._blueprint.getNode(edge.from);
      if (!upstream) return '';
      if (upstream.type === 'data_source' && upstream.config.table_name) {
        return upstream.config.table_name;
      }
      current = upstream.id;
    }
    return '';
  },

  async _generateLLMChartRecommendations(nodeId) {
    const node = this._blueprint.getNode(nodeId);
    if (!node) return;

    const btn = document.getElementById('chart-llm-btn-' + nodeId);
    const status = document.getElementById('chart-llm-status-' + nodeId);
    const prompt = document.getElementById('chart-llm-prompt-' + nodeId);

    // Find table from connected data_source
    const tableName = this._findUpstreamTableName(nodeId);
    if (!tableName) {
      App.error('Connect a Data Source node first');
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Analyzing data...';
    }
    if (status) {
      status.classList.remove('hidden');
      status.innerHTML = '<div class="text-xs text-indigo-400"><i class="fas fa-spinner fa-spin mr-1"></i>LLM analyzing column profile...</div>';
    }

    try {
      const result = await API.llmChartRecommend(tableName, prompt ? prompt.value : '');
      if (result && result.success && result.charts && result.charts.length > 0) {
        // Store recommendations on the node config
        this._blueprint.updateNodeConfig(nodeId, { recommended_charts: result.charts, _llmPrompt: prompt ? prompt.value : '' });
        if (status) {
          status.innerHTML = `<div class="text-xs text-green-500"><i class="fas fa-check-circle mr-1"></i>${result.charts.length} chart recommendation${result.charts.length > 1 ? 's' : ''} generated</div>`;
          setTimeout(() => { if (status) status.classList.add('hidden'); }, 3000);
        }
        // Re-render config form
        this._showNodeConfig(node);
        App.success('Chart recommendations ready!');
      } else {
        const errMsg = (result && result.error) || 'No chart suggestions returned';
        if (status) {
          status.innerHTML = `<div class="text-xs text-red-400"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(errMsg)}</div>`;
        }
        App.error('Chart recommendation failed: ' + errMsg);
      }
    } catch (err) {
      if (status) {
        status.innerHTML = `<div class="text-xs text-red-400"><i class="fas fa-exclamation-circle mr-1"></i>${App.escapeHtml(err.message || 'LLM request failed')}</div>`;
      }
      App.error('LLM chart error: ' + (err.message || 'Check if LLM is running'));
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-wand-magic-sparkles mr-1"></i>Analyze & Recommend Charts';
      }
    }
  },

  _useChartRecommendation(nodeId, index) {
    const node = this._blueprint.getNode(nodeId);
    if (!node) return;
    const recs = node.config.recommended_charts || [];
    const rec = recs[index];
    if (!rec) return;

    const config = {
      chart_type: rec.chart_type || 'bar',
      x_col: rec.x_axis || '',
      y_col: rec.y_axis || '',
      group_col: rec.color || '',
      title: rec.title || '',
    };
    this._blueprint.updateNodeConfig(nodeId, config);
    this._blueprint._rerenderNodeBody(nodeId);
    // Re-render config panel to reflect updated values
    this._showNodeConfig(node);
    App.success(`Applied "${rec.title || rec.chart_type}" chart`);
  },

  /* "" Pipeline Actions """"""""""""""""""""" */
  _newPipeline() {
    if (this._dirty) {
      if (!confirm('Discard unsaved changes?')) return;
    }
    this._currentName = 'Untitled Pipeline';
    this._dirty = false;
    this._blueprint.clear();
    this._updateStatus();
    this._updateEmptyState();
    document.getElementById('bp-name').value = this._currentName;
  },

  async _deleteCurrentPipeline() {
    const name = this._currentName;
    if (name === 'Untitled Pipeline' || !name) {
      App.error('No saved pipeline to delete');
      return;
    }

    const key = 'bp_saved_' + name;
    const exists = localStorage.getItem(key);
    if (!exists) {
      App.error(`Pipeline "${name}" is not saved`);
      return;
    }

    if (!confirm(`Delete pipeline "${name}"? This cannot be undone.`)) return;

    localStorage.removeItem(key);
    try { await API.delete('/pipeline/' + encodeURIComponent(name)); } catch (e) { /* local copy removed anyway */ }
    App.success(`Deleted "${name}"`);

    // Reset to a new empty pipeline
    this._currentName = 'Untitled Pipeline';
    this._dirty = false;
    this._blueprint.clear();
    this._updateStatus();
    this._updateEmptyState();
    document.getElementById('bp-name').value = 'Untitled Pipeline';
  },

  async _savePipeline() {
    const data = this._blueprint.serialize();
    data.name = this._currentName;

    // Save to browser storage (offline mirror)
    const key = 'bp_saved_' + this._currentName;
    let localOk = true;
    try {
      localStorage.setItem(key, JSON.stringify(data));
    } catch (e) {
      localOk = false;
    }

    // Save to the server so pipelines survive across browsers/devices
    let serverOk = false;
    try {
      const resp = await API.post('/pipeline/save', { name: this._currentName, blueprint: data });
      serverOk = !!(resp && resp.success);
      if (!serverOk && resp && resp.error) console.warn('Server pipeline save failed:', resp.error);
    } catch (e) {
      console.warn('Server pipeline save failed:', e.message || e);
    }

    if (localOk || serverOk) {
      this._dirty = false;
      this._updateStatus();
      App.success(`Pipeline "${this._currentName}" saved${serverOk ? '' : ' (local only)'}`);
    } else {
      App.error('Failed to save pipeline');
    }
  },

  
  _builtInTemplates() {
    return [
      { name: 'Data Cleaning Pipeline', description: 'Load data, clean, and preview', nodes: [
        { type:'data_source', label:'Data Source', x:40, y:40, config:{table_name:'',filter:'',limit:1000} },
        { type:'cleaning', label:'Cleaning', x:300, y:40, config:{remove_duplicates:true,null_strategy:'drop',column_rules:{}} },
        { type:'insights', label:'Insights', x:560, y:40, config:{prompt:'Summarize the cleaned dataset'} },
      ], edges: [{from:0,to:1},{from:1,to:2}] },
      { name: 'Sales Analysis', description: 'Query, analyze, and chart sales data', nodes: [
        { type:'data_source', label:'Sales Data', x:40, y:40, config:{table_name:'',filter:'',limit:1000} },
        { type:'sql', label:'SQL Query', x:300, y:40, config:{query:'SELECT * FROM input',limit:1000} },
        { type:'chart', label:'Chart', x:560, y:40, config:{chart_type:'bar',x_col:'',y_col:'',title:'Sales Chart'} },
      ], edges: [{from:0,to:1},{from:1,to:2}] },
      { name: 'Chart Export', description: 'Generate and export charts', nodes: [
        { type:'data_source', label:'Data Source', x:40, y:40, config:{table_name:'',filter:'',limit:1000} },
        { type:'chart', label:'Chart', x:300, y:40, config:{chart_type:'bar',x_col:'',y_col:'',title:'Chart'} },
        { type:'export', label:'Export', x:560, y:40, config:{format:'csv',filename:'export',destination:''} },
      ], edges: [{from:0,to:1},{from:1,to:2}] },
    ];
  },

  _saveTemplate() {
    const name = prompt('Template name:');
    if (!name) return;
    const data = this._blueprint.serialize();
    data._templateName = name;
    data._templateDesc = 'Saved from pipeline: ' + this._currentName;
    try {
      const existing = JSON.parse(localStorage.getItem('bp_templates') || '[]');
      existing.push(data);
      localStorage.setItem('bp_templates', JSON.stringify(existing));
      App.success('Template "' + name + '" saved');
    } catch(e) { App.error('Failed to save template'); }
  },

  _loadTemplate(templateData) {
    if (this._dirty && !confirm('Discard unsaved changes?')) return;
    this._blueprint.clear();
    // Add nodes FIRST so addEdge's connection validation can see them, and map
    // each template index to the REAL id returned by addNode (it generates ids itself).
    const nodeMap = {};
    (templateData.nodes || []).forEach(function(nd, idx) {
      const realId = this._blueprint.addNode(nd.type, nd.x, nd.y, nd.config || {});
      nodeMap[nd._idx !== undefined ? nd._idx : idx] = realId;
      // Also map by real id so edges stored as {from:realId, to:realId}
      // (saved templates from serialize()) resolve correctly.
      if (nd.id) nodeMap[nd.id] = realId;
    }, this);
    (templateData.edges || []).forEach(function(ed) {
      const fromId = nodeMap[ed.from];
      const toId = nodeMap[ed.to];
      if (fromId && toId) this._blueprint.addEdge(fromId, toId);
    }, this);
    this._currentName = templateData._templateName || 'From Template';
    this._dirty = true;
    this._updateStatus();
    this._updateEmptyState();
    document.getElementById('bp-name').value = this._currentName;
  },

  _showTemplates() {
    const builtIn = this._builtInTemplates();
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem('bp_templates') || '[]'); } catch(e) {}
    const all = builtIn.concat(saved);
    if (all.length === 0) { App.info('No templates available'); return; }
    const html = '<div class="space-y-2">' + all.map(function(t, i) {
      const isBuiltIn = i < builtIn.length ? '(built-in)' : '(saved)';
      return '<div class="flex items-center justify-between p-3 rounded-lg border dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer" onclick="AutomationPage._loadTemplate(window.allTemplates[' + i + '])">' +
        '<span><i class="fas fa-diagram-project text-primary-500 mr-2"></i>' + App.escapeHtml(t.name || 'Template') + ' <span class="text-[10px] text-gray-400">' + isBuiltIn + '</span></span>' +
        '<span class="text-[10px] text-gray-500">' + ((t.nodes||[]).length + ' nodes') + '</span></div>';
    }).join('') + '</div>';
    // Store templates for onclick reference
    window.allTemplates = all;
    if (typeof Modal !== 'undefined') Modal.show('Pipeline Templates', html, { wide: false });
  },

  async _loadPipeline() {
    // Merge server-saved pipelines with this browser's local copies
    const pipelines = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith('bp_saved_')) {
        const name = key.replace('bp_saved_', '');
        pipelines.push({ name, source: 'local' });
      }
    }
    try {
      const resp = await API.get('/pipeline/list');
      ((resp && resp.pipelines) || []).forEach(p => {
        if (p && p.name && !pipelines.some(x => x.name === p.name)) {
          pipelines.push({ name: p.name, source: 'server' });
        }
      });
    } catch (e) { /* server unavailable — local list still works */ }

    if (pipelines.length === 0) {
      App.info('No saved pipelines found');
      return;
    }

    const html = `
      <div class="space-y-2">
        ${pipelines.map((p, idx) => `
          <div class="flex items-center justify-between p-3 rounded-lg border dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
               onclick="AutomationPage._loadSpecificPipeline(window.bpPipelineList[${idx}].name)">
            <span><i class="fas fa-diagram-project text-primary-500 mr-2"></i>${App.escapeHtml(p.name)}
              ${p.source === 'server' ? '<span class="text-[10px] text-gray-400 ml-1">(server)</span>' : ''}
            </span>
            <button onclick="event.stopPropagation();AutomationPage._deletePipeline(window.bpPipelineList[${idx}].name)" class="text-red-400 hover:text-red-600 text-sm">
              <i class="fas fa-trash"></i>
            </button>
          </div>
        `).join('')}
      </div>`;
    // Index-based references survive names containing quotes that would break inline onclick
    window.bpPipelineList = pipelines;

    if (typeof Modal !== 'undefined' && Modal.show) {
      Modal.show('Load Pipeline', html, { wide: false });
    }
  },

  async _loadSpecificPipeline(name) {
    // Prefer the server copy, fall back to the browser mirror
    let data = null;
    try {
      const resp = await API.get('/pipeline/load/' + encodeURIComponent(name));
      if (resp && resp.success && resp.pipeline) data = resp.pipeline.blueprint || resp.pipeline;
    } catch (e) { /* fall through to localStorage */ }
    if (!data) {
      try { data = JSON.parse(localStorage.getItem('bp_saved_' + name)); } catch (e) { data = null; }
    }
    if (!data) {
      App.error('Pipeline not found');
      return;
    }
    try {
      this._currentName = name;
      this._blueprint.deserialize(data);
      this._dirty = false;
      this._updateStatus();
      document.getElementById('bp-name').value = name;
      this._updateEmptyState();
      if (typeof Modal !== 'undefined') Modal.close();
      App.success(`Loaded "${name}"`);
    } catch (e) {
      App.error('Failed to load pipeline: ' + e.message);
    }
  },

  async _deletePipeline(name) {
    if (!confirm(`Delete pipeline "${name}"?`)) return;
    localStorage.removeItem('bp_saved_' + name);
    try { await API.delete('/pipeline/' + encodeURIComponent(name)); } catch (e) { /* local copy removed anyway */ }
    App.success(`Deleted "${name}"`);
    // Refresh the list
    this._loadPipeline();
  },

  _validatePipeline() {
    const pipeline = this._serializeToPipeline();
    if (!pipeline) {
      App.error('Pipeline is empty " add some nodes first');
      return;
    }

    // Client-side checks
    const errors = [];
    const nodes = this._blueprint.getNodes();
    const edges = this._blueprint.getEdges();

    // Check for orphan nodes (no connections) " warning
    const connectedNodes = new Set();
    edges.forEach((e) => { connectedNodes.add(e.from); connectedNodes.add(e.to); });

    nodes.forEach((n) => {
      if (n.type !== 'data_source' && !connectedNodes.has(n.id)) {
        errors.push({ node: n.id, message: `"${n.label}" is not connected` });
      }
      // Check config completeness
      if (n.type === 'data_source' && !n.config.table_name) {
        errors.push({ node: n.id, message: 'Data Source: no table selected' });
      }
    });

    if (errors.length > 0) {
      const html = `
        <div class="space-y-2">
          <p class="text-sm font-medium text-red-500">${errors.length} issue(s) found:</p>
          ${errors.map((e) => `<div class="text-xs p-2 bg-red-50 dark:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400">${App.escapeHtml(e.message)}</div>`).join('')}
        </div>`;
      if (typeof Modal !== 'undefined') Modal.show('Validation Results', html, { wide: false });
    } else {
      // Call backend validate
      API.pipelineValidate(pipeline).then((result) => {
        const success = result && result.success;
        const html = success
          ? '<div class="text-center py-6"><i class="fas fa-check-circle text-green-500 text-4xl mb-2"></i><p class="font-medium text-green-600">Pipeline is valid!</p></div>'
          : `<div class="space-y-2"><p class="text-sm font-medium text-red-500">Validation issues:</p>${(result.errors || []).map((e) => `<div class="text-xs p-2 bg-red-50 rounded-lg text-red-600">${App.escapeHtml(e)}</div>`).join('')}</div>`;
        if (typeof Modal !== 'undefined') Modal.show('Validation Results', html, { wide: false });
      }).catch((e) => {
        App.error('Pipeline validation request failed: ' + (e.message || 'Network error'));
      });
    }
  },

  _runPipeline() {
    if (this._isRunning) return;
    const pipeline = this._serializeToPipeline();
    if (!pipeline) {
      App.error('Pipeline is empty " add some nodes first');
      return;
    }

    this._isRunning = true;
    this._clearNodeBadges();
    this._clearEdgeStatus();
    document.getElementById('bp-run-btn').classList.add('hidden');
    document.getElementById('bp-stop-btn').classList.remove('hidden');

    // Add running state to all nodes and animate edges
    document.querySelectorAll('.bp-node').forEach((el) => {
      const overlay = document.createElement('div');
      overlay.className = 'bp-node-status bp-node-status-running';
      overlay.innerHTML = '<i class="fas fa-spinner fa-spin text-white text-lg"></i>';
      overlay.style.cssText = 'position:absolute;inset:0;background:rgba(0,0,0,0.3);border-radius:12px;display:flex;align-items:center;justify-content:center;z-index:20;';
      el.style.position = 'relative';
      el.appendChild(overlay);
    });

    // Animate edges (green flow toward downstream)
    document.querySelectorAll('.bp-edge-path').forEach((el) => {
      el.classList.add('animating');
      el.setAttribute('marker-end', 'url(#bp-arrowhead-anim)');
    });

    // Client-generated run id lets us cancel this run via /pipeline/run/{id}/cancel
    this._runId = 'run_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    pipeline.run_id = this._runId;

    API.pipelineRun(pipeline)
      .then((result) => {
        const success = result && result.success;
        this._isRunning = false;
        document.getElementById('bp-run-btn').classList.remove('hidden');
        document.getElementById('bp-stop-btn').classList.add('hidden');

        // Update node statuses
        document.querySelectorAll('.bp-node-status').forEach((s) => s.remove());

        if (success) {
          const okData = result.result || {};
          // Paint per-node run status badges on the canvas
          this._applyNodeBadges(okData.step_results || {});
          this._clearEdgeStatus();
        } else {
          const failedData = (result && result.result) || {};
          if (failedData.step_results) {
            this._applyNodeBadges(failedData.step_results);
            this._paintEdgeStatus(failedData.step_results);
          } else {
            this._clearEdgeStatus(true);
          }
        }

        if (success) {
          App.success('Pipeline executed successfully!');
          const resultData = result.result || {};
          const tables = resultData.output_tables || [];
          const execTime = resultData.execution_time_ms || 0;
          const stepNames = resultData.step_results ? Object.keys(resultData.step_results) : [];

          // Charts produced by Chart nodes
          const chartEntries = Object.entries(resultData.step_results || {})
            .filter(([, v]) => v && v.chart_png_base64);
          const chartsSection = chartEntries.length > 0 ? `
              <div>
                <h4 class="font-semibold text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-1">
                  <i class="fas fa-chart-bar text-amber-500 text-xs"></i> Generated Charts
                </h4>
                ${chartEntries.map(([name, v]) => `
                  <div class="mb-2 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden bg-white">
                    <img src="data:image/png;base64,${v.chart_png_base64}" alt="${App.escapeHtml(name)}" class="w-full" style="max-height:320px;object-fit:contain;">
                  </div>`).join('')}
              </div>` : '';

          // Textual summaries from Insights/Analysis/Python/Export nodes
          const summaryEntries = Object.entries(resultData.step_results || {})
            .filter(([, v]) => v && (v.summary || v.print_output || v.file));
          const summariesSection = summaryEntries.length > 0 ? `
              <details class="text-xs">
                <summary class="cursor-pointer text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 font-medium">
                  <i class="fas fa-align-left mr-1"></i> Step Summaries (${summaryEntries.length})
                </summary>
                <div class="mt-2 space-y-2 max-h-56 overflow-y-auto">
                  ${summaryEntries.map(([name, v]) => `
                    <div class="p-2 bg-gray-50 dark:bg-gray-800 rounded-lg">
                      <div class="font-mono font-medium mb-1">${App.escapeHtml(name)}</div>
                      <pre class="text-[10px] whitespace-pre-wrap font-mono text-gray-600 dark:text-gray-300">${App.escapeHtml(v.summary || v.print_output || '')}</pre>
                      ${v.file ? `<div class="text-[10px] text-green-500 mt-1"><i class="fas fa-file mr-1"></i>${App.escapeHtml(v.file)}</div>` : ''}
                    </div>`).join('')}
                </div>
              </details>` : '';

          // Build rich table rows with preview + stats + export
          const tableExportRows = tables.map((t, idx) => {
            const safeTable = App.escapeHtml(t);
            const previewId = `bp-preview-${idx}`;
            const statsId = `bp-stats-${idx}`;
            return `
            <div class="border border-gray-200 dark:border-gray-700 rounded-lg mb-2 overflow-hidden">
              <!-- Table header row -->
              <div class="flex items-center justify-between p-2 bg-gray-50 dark:bg-gray-800">
                <div class="flex items-center gap-2 min-w-0">
                  <i class="fas fa-table text-primary-400 text-xs shrink-0"></i>
                  <span class="font-mono text-xs font-medium truncate">${safeTable}</span>
                  <span class="text-[10px] text-gray-400 shrink-0" id="${previewId}-meta"></span>
                </div>
                <div class="flex gap-1 shrink-0 ml-2">
                  <!-- Preview toggle -->
                  <button onclick="AutomationPage._togglePreview('${safeTable}','${previewId}','${statsId}')"
                    class="px-2 py-1 text-[10px] bg-indigo-500 text-white rounded hover:bg-indigo-600" title="Preview first 3 rows">
                    <i class="fas fa-eye mr-0.5"></i>Preview
                  </button>
                  <!-- Stats toggle -->
                  <button onclick="AutomationPage._toggleStats('${safeTable}','${statsId}')"
                    class="px-2 py-1 text-[10px] bg-teal-500 text-white rounded hover:bg-teal-600" title="Column statistics">
                    <i class="fas fa-chart-bar mr-0.5"></i>Stats
                  </button>
                  <!-- Export dropdown wrapper -->
                  <div class="relative inline-block">
                    <button onclick="AutomationPage._showExportMenu(event,'${safeTable}')"
                      class="px-2 py-1 text-[10px] bg-primary-500 text-white rounded hover:bg-primary-600" title="Export options">
                      <i class="fas fa-download mr-0.5"></i>Export 
                    </button>
                  </div>
                </div>
              </div>
              <!-- Preview area (hidden by default) -->
              <div id="${previewId}" class="hidden border-t border-gray-200 dark:border-gray-700">
                <div class="p-2 text-center text-[10px] text-gray-400">
                  <i class="fas fa-spinner fa-spin mr-1"></i>Loading preview...
                </div>
              </div>
              <!-- Stats area (hidden by default) -->
              <div id="${statsId}" class="hidden border-t border-gray-200 dark:border-gray-700"></div>
            </div>`;
          }).join('');

          const firstTable = tables.length > 0 ? App.escapeHtml(tables[0]) : '';

          const html = `
            <div class="space-y-4 text-sm">

              <!-- Execution Summary -->
              <div class="grid grid-cols-3 gap-3">
                <div class="bg-green-50 dark:bg-green-900/20 p-3 rounded-lg text-center">
                  <div class="text-lg font-bold text-green-600 dark:text-green-400">${resultData.steps_completed || 0}/${resultData.steps_total || 0}</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">Steps</div>
                </div>
                <div class="bg-blue-50 dark:bg-blue-900/20 p-3 rounded-lg text-center">
                  <div class="text-lg font-bold text-blue-600 dark:text-blue-400">${tables.length}</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">Tables</div>
                </div>
                <div class="bg-gray-50 dark:bg-gray-800 p-3 rounded-lg text-center">
                  <div class="text-lg font-bold text-gray-600 dark:text-gray-400">${(execTime / 1000).toFixed(1)}s</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">Duration</div>
                </div>
              </div>

              <!-- Data Export with Preview -->
              ${tables.length > 0 ? `
              <div>
                <h4 class="font-semibold text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-1">
                  <i class="fas fa-download text-primary-500 text-xs"></i> Export Data
                  <span class="text-[10px] font-normal text-gray-400 ml-1">Click <strong>Preview</strong> to see first 3 rows, then export</span>
                </h4>
                ${tableExportRows}
                ${tables.length > 1 ? `
                  <button onclick="AutomationPage._exportAllTables('${tables.map(t => App.escapeHtml(t)).join(',')}')"
                    class="mt-2 w-full text-xs bg-purple-500 hover:bg-purple-600 text-white px-3 py-2 rounded-lg transition-colors">
                    <i class="fas fa-file-archive mr-1"></i> Download All Tables as ZIP
                  </button>
                ` : ''}
              </div>
              ` : ''}

              ${tables.length > 0 ? `
              <!-- LLM Auto-Generated Report -->
              <div>
                <h4 class="font-semibold text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-1">
                  <i class="fas fa-robot text-purple-500 text-xs"></i> AI Report (LLM Generated)
                  <span class="text-[9px] font-normal text-gray-400 ml-1">&mdash; auto-generating...</span>
                </h4>
                <div id="bp-llm-report" class="border border-purple-200 dark:border-purple-800 rounded-lg p-3 bg-purple-50/50 dark:bg-purple-900/10">
                  <div class="text-center py-4">
                    <i class="fas fa-spinner fa-spin text-purple-400 text-lg mb-2"></i>
                    <p class="text-xs text-gray-500">Connecting to local LLM to analyze data...</p>
                    <p class="text-[10px] text-gray-400 mt-1">Fetching statistics &amp; generating insights</p>
                  </div>
                </div>
                <button id="bp-3pass-btn" onclick="AutomationPage._threePassReview('${firstTable}')"
                  class="mt-2 w-full text-xs bg-purple-600 hover:bg-purple-700 text-white px-3 py-2 rounded-lg transition-colors">
                  <i class="fas fa-magic mr-1"></i> Regenerate with 3-Pass Quality Check
                </button>
              </div>

              <!-- Export & Presentation -->
              <div>
                <h4 class="font-semibold text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-1">
                  <i class="fas fa-download text-primary-500 text-xs"></i> Export Options
                </h4>
                <div class="grid grid-cols-3 gap-2">
                  <button onclick="AutomationPage._exportTable('${firstTable}','html')"
                    class="flex flex-col items-center gap-1 p-2 border border-orange-200 dark:border-orange-800 rounded-lg hover:bg-orange-50 dark:hover:bg-orange-900/20 transition-colors">
                    <i class="fas fa-file-code text-orange-500"></i>
                    <span class="text-[10px] font-medium">HTML</span>
                  </button>
                  <button onclick="AutomationPage._exportTable('${firstTable}','pptx')"
                    class="flex flex-col items-center gap-1 p-2 border border-red-200 dark:border-red-800 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors">
                    <i class="fas fa-file-powerpoint text-red-500"></i>
                    <span class="text-[10px] font-medium">PPTX</span>
                  </button>
                  <button onclick="AutomationPage._exportBI('${firstTable}','powerbi')"
                    class="flex flex-col items-center gap-1 p-2 border border-yellow-200 dark:border-yellow-800 rounded-lg hover:bg-yellow-50 dark:hover:bg-yellow-900/20 transition-colors">
                    <i class="fab fa-microsoft text-yellow-500"></i>
                    <span class="text-[10px] font-medium">Power BI</span>
                  </button>
                </div>
              </div>
              ` : `
              <!-- No data hint -->
              <div class="text-center py-4">
                <i class="fas fa-database text-gray-300 dark:text-gray-600 text-2xl mb-2"></i>
                <p class="text-xs text-gray-400">Pipeline ran successfully but produced no output tables.</p>
                <p class="text-xs text-gray-400 mt-0.5">Add a <strong>Data Source</strong> node with a table selected and connect it to run data through the pipeline.</p>
              </div>
              `}

              ${chartsSection}

              ${summariesSection}

              <!-- Step Details (collapsible) -->
              ${stepNames.length > 0 ? `
              <details class="text-xs">
                <summary class="cursor-pointer text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 font-medium">
                  <i class="fas fa-list mr-1"></i> Step Details (${stepNames.length})
                </summary>
                <div class="mt-2 space-y-1 max-h-32 overflow-y-auto">
                  ${stepNames.map(s => `<div class="flex items-center gap-2 px-2 py-1 bg-gray-50 dark:bg-gray-800 rounded">
                    <i class="fas fa-check-circle text-green-400 text-[10px]"></i>
                    <span class="font-mono">${App.escapeHtml(s)}</span>
                  </div>`).join('')}
                </div>
              </details>
              ` : ''}
            </div>`;
          if (typeof Modal !== 'undefined') Modal.show('Pipeline Results', html, { wide: true });

          // Generate any Dashboard nodes as saved dashboards after a successful run
          this._generateDashboards(pipeline);

          // Auto-generate LLM report once modal is visible
          if (tables.length > 0) {
            setTimeout(() => this._generateAutoReport(firstTable), 600);
          }
        } else {
          const rawErr = ((result && result.result && result.result.error) || (result && result.error) || 'Unknown error');
          const friendly = this._friendlyError(rawErr, pipeline);
          this._showErrorModal(friendly, rawErr, pipeline);
        }
      })
      .catch((err) => {
        this._isRunning = false;
        document.getElementById('bp-run-btn').classList.remove('hidden');
        document.getElementById('bp-stop-btn').classList.add('hidden');
        document.querySelectorAll('.bp-node-status').forEach((s) => s.remove());
        // No per-step info on network failure — mark everything failed
        this._clearEdgeStatus(true);
        const friendly = this._friendlyError(err.message || err, pipeline);
        this._showErrorModal(friendly, err.message || err, pipeline);
      });
   },

  /* "" Generate Dashboard nodes "" */
  _generateDashboards(pipeline) {
    const nodes = (this._blueprint ? this._blueprint.getNodes() : []) || [];
    const dashNodes = nodes.filter((n) => n.type === 'dashboard');
    if (!dashNodes.length) return;
    dashNodes.forEach((node) => this._generateDashboardForNode(node));
  },

  _generateDashboardForNode(node) {
    const safeName = this._currentName.replace(/[^a-zA-Z0-9_]/g, '_').replace(/^_+|_+$/g, '') || 'pipeline';
    const safeId = node.id.replace('n_', '');
    const tableName = `pipeline_${safeName}_${safeId}`;
    const cfg = node.config || {};
    const dashName = cfg.dashboard_name || (this._currentName + ' Dashboard');
    const kpiFocus = String(cfg.kpi_focus || '').split(',').map((s) => s.trim()).filter(Boolean);
    const sections = String(cfg.sections || '').split(',').map((s) => s.trim()).filter(Boolean);

    App.info('Generating dashboard from ' + tableName + '...');
    API.autoBuildPlan(tableName, {
      kpiFocus: kpiFocus,
      prompt: cfg.prompt || '',
      sections: sections,
    })
      .then((plan) => {
        if (!plan || !plan.success) {
          App.error('Dashboard generation failed: ' + ((plan && plan.error) || 'unknown error'));
          return null;
        }
        if (typeof AIBuild === 'undefined' || !AIBuild.buildDashboardFromPlan) {
          App.error('Dashboard generation failed: AI visual builder is unavailable.');
          return null;
        }
        const built = AIBuild.buildDashboardFromPlan(plan, {
          canvasW: 1920,
          canvasH: 1080,
          title: dashName,
          subtitle: 'Data source: ' + tableName + ' · AI data-driven layout',
        });
        const widgets = (built && built.widgets) || [];
        const dashCfg = {
          name: dashName,
          table: tableName,
          pages: [{ id: 'page_1', name: 'Page 1', widgets: widgets }],
        };
        return API.createDashboard({
          name: dashName,
          owner_id: (typeof localStorage !== 'undefined' && localStorage.getItem('username')) || 'anonymous',
          widgetCount: widgets.length,
          is_shared: false,
          config: dashCfg,
        });
      })
      .then((res) => {
        if (!res) return;
        if (res.success) {
          const dash = res.dashboard || {};
          const dashId = dash.id || '';
          App.success('Dashboard "' + dashName + '" created!');
          const link = dashId ? `#blankdash/${dashId}` : '#dashboardstudio';
          if (typeof Modal !== 'undefined') {
            Modal.show('Dashboard Generated', `
              <div class="text-center py-4">
                <i class="fas fa-gauge-high text-purple-500 text-4xl mb-3"></i>
                <p class="font-medium text-gray-700 dark:text-gray-300 mb-1">"${App.escapeHtml(dashName)}" was created from <span class="font-mono text-xs">${App.escapeHtml(tableName)}</span></p>
                <p class="text-xs text-gray-400 mb-4">Open it in Dashboard Studio to refine the layout.</p>
                <a href="${link}" class="inline-block bg-purple-600 hover:bg-purple-700 text-white text-sm px-4 py-2 rounded-lg transition-colors">
                  <i class="fas fa-external-link-alt mr-1"></i> Open Dashboard
                </a>
              </div>`, { wide: false });
          }
        } else {
          App.error('Failed to save dashboard: ' + (res.error || 'unknown error'));
        }
      })
      .catch((err) => {
        App.error('Dashboard generation failed: ' + (err && err.message ? err.message : ''));
      });
  },

  /* "" Edge run-status painting "" */
  _clearEdgeStatus(allFailed) {
    if (this._edgeTimer) { clearTimeout(this._edgeTimer); this._edgeTimer = null; }
    document.querySelectorAll('.bp-edge-path').forEach((el) => {
      el.classList.remove('animating', 'error', 'skipped');
      el.style.stroke = '';
      el.style.strokeWidth = '';
      el.setAttribute('marker-end', allFailed ? 'url(#bp-arrowhead-error)' : 'url(#bp-arrowhead)');
    });
  },

  // Color only edges actually affected by failures:
  //   red   — edge INTO a failed node, or OUT of a failed/skipped node
  //   amber — edge into a skipped (not executed) node
  //   default gray everywhere else
  _paintEdgeStatus(stepResults) {
    const failed = new Set();
    const skipped = new Set();
    Object.entries(stepResults || {}).forEach(([stepName, res]) => {
      if (!res || stepName.startsWith('load_')) return;
      const idx = stepName.indexOf('_');
      const nodeId = idx > -1 ? stepName.slice(idx + 1) : null;
      if (!nodeId) return;
      if (res.status === 'error') failed.add(nodeId);
      else if (res.status === 'skipped') skipped.add(nodeId);
    });

    document.querySelectorAll('.bp-edge-path').forEach((el) => {
      el.classList.remove('animating');
      el.style.stroke = '';
      el.style.strokeWidth = '';
      const edgeId = el.dataset.edgeId;
      const edge = this._blueprint.getEdges().find((e) => e.id === edgeId);
      if (!edge) return;
      let marker = 'url(#bp-arrowhead)';
      if (failed.has(edge.to) || failed.has(edge.from) || skipped.has(edge.from)) {
        el.classList.add('error');
        el.style.stroke = '#ef4444';
        el.style.strokeWidth = '2.5px';
        marker = 'url(#bp-arrowhead-error)';
      } else if (skipped.has(edge.to)) {
        el.classList.add('skipped');
        el.style.stroke = '#f59e0b';
        el.style.strokeWidth = '2.5px';
      }
      el.setAttribute('marker-end', marker);
    });
    // Error colors persist until the next run (which calls _clearEdgeStatus)
  },


  /* "" Per-node run status badges "" */
  // Step names look like "<type>_<nodeId>" (nodeId itself contains underscores),
  // so strip everything up to the FIRST underscore to recover the node id.
  _clearNodeBadges() {
    if (this._blueprint && this._blueprint.clearNodeStatus) {
      this._blueprint.clearNodeStatus();
    }
    document.querySelectorAll('.bp-node').forEach((el) => {
      el.style.boxShadow = '';
      if (el.title) el.title = el.title.split('\n').filter((l) => !l.startsWith('Run')).join('\n');
    });
  },

  _applyNodeBadges(stepResults) {
    Object.entries(stepResults || {}).forEach(([stepName, res]) => {
      // Auto-generated warehouse-load steps share the terminal node's id;
      // their result is already reflected by that node's own step.
      if (stepName.startsWith('load_')) return;
      const idx = stepName.indexOf('_');
      const nodeId = idx > -1 ? stepName.slice(idx + 1) : null;
      if (!nodeId) return;
      const el = this._blueprint._canvas
        ? this._blueprint._canvas.querySelector(`[data-node-id="${nodeId}"]`)
        : document.querySelector(`.bp-node[data-node-id="${nodeId}"]`);
      if (!el || !res) return;
      if (res.status === 'completed') {
        const meta = res.duration_ms !== undefined ? ` (${(res.duration_ms / 1000).toFixed(2)}s, ${res.rows ?? '?'} rows)` : '';
        if (this._blueprint && this._blueprint.setNodeStatus) this._blueprint.setNodeStatus(nodeId, 'ok', meta);
        el.title = `${el.title || ''}\nRun: OK${meta}`;
      } else if (res.status === 'error') {
        if (this._blueprint && this._blueprint.setNodeStatus) this._blueprint.setNodeStatus(nodeId, 'err', '');
        el.title = `${el.title || ''}\nRun error: ${(res.error || '').slice(0, 200)}`;
      } else if (res.status === 'skipped') {
        if (this._blueprint && this._blueprint.setNodeStatus) this._blueprint.setNodeStatus(nodeId, 'skip', '');
        el.title = `${el.title || ''}\nRun: skipped`;
      }
    });
  },

  async _stopPipeline() {
    if (!this._isRunning) return;
    // Ask the backend to abort the run at the next step boundary
    if (this._runId) {
      try {
        await API.post('/pipeline/run/' + encodeURIComponent(this._runId) + '/cancel', {});
      } catch (e) { /* run may already be finished */ }
    }
    this._isRunning = false;
    this._runId = null;
    document.getElementById('bp-run-btn').classList.remove('hidden');
    document.getElementById('bp-stop-btn').classList.add('hidden');
    document.querySelectorAll('.bp-node-status').forEach((s) => s.remove());
    // Stop leaves steps unfinished — clear animations/badges so lines don't stay stuck
    this._clearNodeBadges();
    this._clearEdgeStatus();
    App.info('Pipeline stop requested — aborting at the next step');
  },

  /* "" Friendly error messages with recommendations "" */
  _friendlyError(rawErr, pipeline) {
    // Build step name -> human label mapping
    const nodes = this._blueprint ? this._blueprint.getNodes() : [];
    const nodeLabels = {};
    const typeIcons = { data_source:'Data Source', cleaning:'Clean', sql:'SQL Query', python:'Python', analysis:'Analysis', chart:'Chart', insights:'Insights' };
    for (const n of nodes) {
      const prefix = typeIcons[n.type] || n.type;
      nodeLabels[n.id] = '"' + (n.label || prefix) + '"';
    }
    // Also map step_name (type_nodeId) -> label
    for (const n of nodes) {
      const stepName = this._stepNameForNode(n);
      if (stepName) nodeLabels[stepName] = nodeLabels[n.id] || stepName;
    }

    // Replace raw step IDs with friendly labels in the error message
    let friendlyMsg = rawErr;
    for (const [id, label] of Object.entries(nodeLabels)) {
      friendlyMsg = friendlyMsg.split(id).join(label);
    }

    // Determine error category and generate recommendations
    const errLower = rawErr.toLowerCase();
    const hints = [];
    let title = 'Pipeline Error';
    let detail = false;

    if (errLower.includes('binder error') || errLower.includes('referenced column') || errLower.includes('no such column') || errLower.includes('keyerror')) {
      title = 'Column Not Found';
      const stepMatch = rawErr.match(/['"]([^'"]+)['"]/);
      const colName = stepMatch ? stepMatch[1] : 'specified';
      hints.push('The column "' + colName + '" does not exist in the input data.');
      hints.push('Check the upstream step output or use a different column name.');
    } else if (errLower.includes('not found') || errLower.includes('does not exist')) {
      title = 'Missing Input Data';
      hints.push('Check that all upstream steps are connected with edges.');
      hints.push('Ensure Data Source nodes have a table selected.');
      hints.push('Try running the pipeline again after connecting all steps.');
    } else if (errLower.includes('depends on') || errLower.includes('dependency')) {
      title = 'Invalid Step Ordering';
      hints.push('Rearrange the pipeline so data flows from source through each step in sequence.');
      hints.push('Remove and re-add the edge connections between steps.');
    } else if (errLower.includes('syntax') || errLower.includes('parse')) {
      title = 'SQL or Code Syntax Error';
      hints.push('Review the SQL query or Python code in the failing step for syntax issues.');
      hints.push('Use the SQL Explorer page to test your query before adding it to the pipeline.');
    } else if (errLower.includes('timeout') || errLower.includes('timed out')) {
      title = 'Step Timed Out';
      hints.push('The step took too long to execute. Try reducing the data size or limit.');
      hints.push('Check if the warehouse query can be optimized with filters.');
    } else if (errLower.includes('permission') || errLower.includes('access')) {
      title = 'Permission Denied';
      hints.push('You may not have access to the table or resource. Contact your admin.');
    } else if (errLower.includes('column') || errLower.includes('field')) {
      title = 'Column Not Found';
      const stepMatch2 = rawErr.match(/['"]([^'"]+)['"]/);
      const colName2 = stepMatch2 ? stepMatch2[1] : 'specified';
      hints.push('The column "' + colName2 + '" does not exist in the input data.');
      hints.push('Check the upstream step output or use a different column name.');
    } else {
      title = 'Step Execution Failed';
      hints.push('Review the configuration of the failing step.');
      hints.push('Try running the step individually to isolate the issue.');
    }

    return { title, message: friendlyMsg, hints, detail: true };
  },

  _stepNameForNode(node) {
    const typePrefix = { data_source:'extract', cleaning:'clean', sql:'sql', python:'transform', analysis:'analysis', chart:'chart', insights:'insights', filter:'filter', join:'join', aggregate:'aggregate', anomaly:'anomaly', forecast:'forecast', stat_test:'stat_test', regression:'regression', quality:'quality', comparison:'comparison', cohort:'cohort', ml_model:'ml_model', dashboard:'dashboard', export:'export' };
    const prefix = typePrefix[node.type] || node.type;
    return prefix + '_' + node.id;
  },

  /*  AI Diagnose & Fix  */

  _showErrorModal(friendly, rawErr, pipeline) {
    this._lastPipelineError = rawErr;
    this._lastPipeline = pipeline;

    var panel = document.getElementById('bp-error-panel');
    var titleEl = document.getElementById('bp-error-title');
    var msgEl = document.getElementById('bp-error-message');
    var hintsEl = document.getElementById('bp-error-hints');
    var resultEl = document.getElementById('bp-ai-fix-result');
    if (!panel || !titleEl || !msgEl) return;

    titleEl.textContent = friendly.title || 'Pipeline Error';
    msgEl.textContent = friendly.message || rawErr || 'Unknown error';

    var hints = friendly.hints || [];
    hintsEl.innerHTML = hints.length > 0
      ? '<ul class="text-xs text-gray-600 dark:text-gray-400 space-y-1 ml-3 list-disc">'
        + hints.map(function(h) { return '<li>' + App.escapeHtml(h) + '</li>'; }).join('') + '</ul>'
      : '';

    // Reset the AI fix result area
    if (resultEl) { resultEl.innerHTML = ''; resultEl.classList.add('hidden'); }

    // Show the inline panel
    panel.classList.remove('hidden');
  },

  _closeErrorPanel() {
    var panel = document.getElementById('bp-error-panel');
    if (panel) panel.classList.add('hidden');
  },

  async _aiDiagnose() {
    var btn = document.getElementById('bp-ai-fix-btn');
    var resultEl = document.getElementById('bp-ai-fix-result');
    if (!btn || !resultEl) return;

    var rawErr = this._lastPipelineError || '';
    var pipeline = this._lastPipeline || this._serializeToPipeline();
    if (!pipeline) { App.error('No pipeline to diagnose'); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Analyzing with AI...';
    resultEl.classList.remove('hidden');
    resultEl.innerHTML = '<div class="flex items-center gap-2 p-3 text-xs text-purple-500"><i class="fas fa-spinner fa-spin"></i> Sending error to LLM for analysis...</div>';

    try {
      var tables = Object.keys((typeof App !== 'undefined' && App.state && App.state.tables) || {});
      var result = await API.pipelineDiagnose({ error: rawErr, pipeline: pipeline, available_tables: tables });
      if (!result || !result.success) {
        resultEl.innerHTML = '<div class="p-3 text-xs text-red-400">' + App.escapeHtml((result && result.error) || 'No suggestions available') + '</div>';
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-wand-magic-sparkles mr-1"></i>AI Diagnose & Fix';
        return;
      }
      this._lastFixes = result.fixes || [];
      this._renderFixes(result);
    } catch (e) {
      resultEl.innerHTML = '<div class="p-3 text-xs text-red-400">AI unavailable  ' + App.escapeHtml(e.message || String(e)) + '</div>';
    }
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-wand-magic-sparkles mr-1"></i>AI Diagnose & Fix';
  },

  _renderFixes(result) {
    var resultEl = document.getElementById('bp-ai-fix-result');
    if (!resultEl) return;
    var fixes = result.fixes || [];
    var analysis = result.analysis || '';

    var html = '';
    if (analysis) {
      html += '<div class="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg mb-3">'
        + '<div class="text-xs font-semibold text-blue-700 dark:text-blue-400 mb-1"><i class="fas fa-lightbulb mr-1"></i>Analysis</div>'
        + '<div class="text-xs text-blue-600 dark:text-blue-300">' + AutomationPage._mdToHtml(App.escapeHtml(analysis)) + '</div></div>';
    }

    if (fixes.length === 0) {
      html += '<div class="p-3 text-xs text-gray-400 text-center">No specific fixes found. Try adjusting the pipeline manually.</div>';
      resultEl.innerHTML = html;
      return;
    }

    html += '<div class="space-y-2">';
    fixes.forEach(function(fix, i) {
      html += '<div class="p-2.5 border rounded-lg bg-gray-50 dark:bg-gray-800 dark:border-gray-700" id="bp-fix-' + i + '">'
        + '<div class="flex items-center justify-between mb-1">'
        + '<div class="text-xs font-semibold">' + App.escapeHtml(fix.nodeLabel || fix.nodeId || 'Node') + '</div>'
        + '<button onclick="AutomationPage._applyFix(' + i + ')" class="text-[10px] px-2 py-0.5 bg-green-500 text-white rounded hover:bg-green-600">Apply</button>'
        + '</div>'
        + '<div class="text-[10px] text-gray-500 mb-1">Field: <code class="bg-gray-200 dark:bg-gray-700 px-1 rounded">' + App.escapeHtml(fix.field || '') + '</code></div>'
        + '<div class="text-[10px] font-mono text-gray-400 mb-1 break-all">Current: ' + App.escapeHtml(String(fix.currentValue || '').slice(0, 120)) + '</div>'
        + '<div class="text-[10px] font-mono text-green-600 dark:text-green-400 mb-1 break-all">Suggested: ' + App.escapeHtml(String(fix.suggestedValue || '').slice(0, 120)) + '</div>'
        + '<div class="text-[10px] text-amber-600 dark:text-amber-400 italic">' + App.escapeHtml(fix.reason || '') + '</div>'
        + '</div>';
    });
    html += '</div>';

    html += '<div class="flex gap-2 mt-3">'
      + '<button onclick="AutomationPage._applyAllFixes()" class="flex-1 px-3 py-2 bg-green-500 hover:bg-green-600 text-white rounded-lg text-xs font-medium">Apply All Fixes</button>'
      + '<button onclick="AutomationPage._runAgain()" class="flex-1 px-3 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg text-xs font-medium">Run Again</button>'
      + '</div>';

    resultEl.innerHTML = html;
  },

  _applyFix(index) {
    var fix = (this._lastFixes || [])[index];
    if (!fix || !fix.nodeId) return;

    var nodes = this._blueprint.getNodes();
    // The LLM may return a step name (e.g. "sql_n_rrq4noa") rather than
    // the raw node ID ("n_rrq4noa"). Try exact match first, then strip
    // the type prefix, then match by label.
    var node = nodes.find(function(n) { return n.id === fix.nodeId; });
    if (!node) {
      // Try stripping prefix: "sql_n_xxx"  "n_xxx"
      var stripped = fix.nodeId.replace(/^[a-z]+_/, '');
      node = nodes.find(function(n) { return n.id === stripped; });
    }
    if (!node && fix.nodeLabel) {
      node = nodes.find(function(n) { return (n.label || '').toLowerCase().indexOf(fix.nodeLabel.toLowerCase()) !== -1; });
    }
    if (!node) { App.error('Node not found: ' + fix.nodeId); return; }

    var field = fix.field || '';
    if (field.startsWith('config.')) {
      var key = field.slice(7);
      // Update through the blueprint so the node body re-renders with the new
      // value (direct node.config mutation leaves the canvas stale).
      this._blueprint.updateNodeConfig(node.id, { [key]: fix.suggestedValue });
      this._blueprint._rerenderNodeBody(node.id);
      this._showNodeConfig(node);
    } else if (field === 'action' && fix.suggestedValue === 'load_table') {
      App.info('Please load the required table via the Upload page first');
      return;
    }

    this._dirty = true;
    App.success('Fixed: ' + (fix.nodeLabel || fix.nodeId));

    var fixEl = document.getElementById('bp-fix-' + index);
    if (fixEl) {
      fixEl.classList.add('border-green-400', 'bg-green-50', 'dark:bg-green-900/10');
      var btn = fixEl.querySelector('button');
      if (btn) { btn.textContent = 'Applied'; btn.disabled = true; btn.classList.remove('bg-green-500'); btn.classList.add('bg-green-300'); }
    }
  },

  _applyAllFixes() {
    var fixes = this._lastFixes || [];
    fixes.forEach(function(_, i) { AutomationPage._applyFix(i); });
  },

  _runAgain() {
    if (typeof Modal !== 'undefined') Modal.close();
    setTimeout(function() { AutomationPage._runPipeline(); }, 300);
  },

  /* "" Serialization to Backend Pipeline "" */
  // Node types that are canvas metadata only — they never become backend steps,
  // so they must not receive auto-generated load steps either.
  _NO_STEP_TYPES: [],

  _serializeToPipeline() {
    const nodes = this._blueprint.getNodes();
    if (nodes.length === 0) return null;

    const edges = this._blueprint.getEdges();
    const steps = [];

    // Build a map from node ID to step name (type_nodeId)
    const stepNameForNode = {};
    const typePrefix = { data_source:'extract', cleaning:'clean', sql:'sql', python:'transform', analysis:'analysis', chart:'chart', insights:'insights', filter:'filter', join:'join', aggregate:'aggregate', anomaly:'anomaly', forecast:'forecast', stat_test:'stat_test', regression:'regression', quality:'quality', comparison:'comparison', cohort:'cohort', ml_model:'ml_model', dashboard:'dashboard', export:'export' };
    for (const n of nodes) {
      const prefix = typePrefix[n.type] || n.type;
      stepNameForNode[n.id] = prefix + '_' + n.id;
    }

    // Node types that DO produce a backend step (deps pointing to others are dropped)
    const hasStepType = (t) => !this._NO_STEP_TYPES.includes(t);

    // Incoming connections per node (order preserved — used by Join's second input)
    const inputsByNode = {};
    edges.forEach((e) => {
      if (!inputsByNode[e.to]) inputsByNode[e.to] = [];
      inputsByNode[e.to].push(e.from);
    });

    // Build dependency map using step names, not raw node IDs
    const stepDeps = {};
    edges.forEach((e) => {
      const toName = stepNameForNode[e.to];
      const fromName = stepNameForNode[e.from];
      const fromNode = nodes.find((n) => n.id === e.from);
      if (toName && fromName && fromNode && hasStepType(fromNode.type)) {
        if (!stepDeps[toName]) stepDeps[toName] = [];
        if (!stepDeps[toName].includes(fromName)) stepDeps[toName].push(fromName);
      }
    });

    // Sort nodes topologically based on edges
    const sorted = this._topologicalSort(nodes, edges);

    for (const node of sorted) {
      if (!hasStepType(node.type)) continue;
      const stepName = stepNameForNode[node.id];
      const deps = (stepDeps[stepName] || []).filter((d) => steps.some((s) => s.name === d));
      const step = this._nodeToStep(node, deps, inputsByNode[node.id] || []);
      if (step) steps.push(step);
    }

    // Auto-append load steps for data-producing terminal nodes so output is saved
    // to the warehouse. Sinks like Export/Dashboard handle themselves and must
    // NOT get a load step.
    const nodeIds = new Set(nodes.map(n => n.id));
    const hasOutgoing = new Set();
    edges.forEach(e => hasOutgoing.add(e.from));
    const terminalNodes = sorted.filter(n =>
      !hasOutgoing.has(n.id) &&
      hasStepType(n.type) &&
      n.type !== 'export' &&
      n.type !== 'dashboard' &&
      n.type !== 'chart' &&
      n.type !== 'insights' &&
      steps.some((s) => s.name === stepNameForNode[n.id])
    );

    for (const node of terminalNodes) {
      const stepName = stepNameForNode[node.id];
      // Sanitize table name " ensure at least one valid character
      const safeName = this._currentName.replace(/[^a-zA-Z0-9_]/g, '_').replace(/^_+|_+$/g, '') || 'pipeline';
      const safeId = node.id.replace('n_', '');
      const tableName = `pipeline_${safeName}_${safeId}`;
      steps.push({
        name: 'load_' + node.id,
        type: 'load',
        params: {
          // Pipeline stores data with node.id as key (output_name param)
          input_data: node.id,
          table_name: tableName,
          if_exists: 'replace',
        },
        depends_on: [stepName],
      });
    }

    return {
      name: this._currentName,
      description: `Blueprint pipeline: ${this._currentName}`,
      steps,
    };
  },

  _nodeToStep(node, deps, upstreamInputs) {
    const c = node.config;
    const firstInput = (upstreamInputs && upstreamInputs[0]) || this._findInputFor(node.id);
    // Join needs a second input: prefer the explicitly chosen one, else the
    // second connected edge, else nothing.
    const secondInput = (upstreamInputs || []).find((id) => id !== firstInput)
      || ((c.second_input && c.second_input !== firstInput) ? c.second_input : '');
    switch (node.type) {
      case 'data_source':
        return {
          name: 'extract_' + node.id,
          type: 'extract',
          params: {
            source_type: 'warehouse',
            table: c.table_name || '',
            filter: c.filter || '',
            limit: c.limit || 1000,
            output_name: node.id,
          },
          depends_on: [],
        };
      case 'cleaning':
        return {
          name: 'clean_' + node.id,
          type: 'clean',
          params: {
            input_data: firstInput,
            plan: {
              deduplication: c.remove_duplicates ? 'first' : 'false',
              null_strategy: c.null_strategy || 'drop',
              drop_columns: c.drop_columns || [],
            },
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'sql':
        return {
          name: 'sql_' + node.id,
          type: 'sql',
          params: {
            input_data: firstInput,
            query: c.query || 'SELECT * FROM input',
            limit: c.limit || 1000,
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'python':
        return {
          name: 'transform_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'custom',
            code: c.code || '',
            var_name: c.var_name || 'df',
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'analysis':
        return {
          name: 'analysis_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'analysis',
            analysis_type: c.analysis_type || 'statistics',
            target_col: c.target_col || '',
            feature_cols: c.feature_cols || [],
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'chart':
        return {
          name: node.type + '_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: node.type,
            config: c,
            output_name: node.id,
            export_target: c.export_target || 'none',
          },
          depends_on: deps,
        };
      case 'insights':
        // Terminal nodes " don't produce pipeline output
        return {
          name: node.type + '_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: node.type,
            config: c,
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'filter':
        return {
          name: 'filter_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'filter',
            column: c.column || '',
            operator: c.operator || 'equals',
            value: c.value === undefined || c.value === null ? '' : String(c.value),
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'join':
        return {
          name: 'join_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            input_data2: secondInput,
            operation: 'join',
            join_type: c.join_type || 'inner',
            left_key: c.left_key || '',
            right_key: c.right_key || '',
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'aggregate':
        return {
          name: 'aggregate_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'aggregate',
            group_by: c.group_by || '',
            aggregate_col: c.aggregate_col || '',
            aggregate_fn: c.aggregate_fn || 'sum',
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'anomaly':
        return {
          name: 'anomaly_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'anomaly',
            column: c.column || '',
            method: c.method || 'iqr',
            threshold: c.threshold === undefined || c.threshold === '' ? 1.5 : Number(c.threshold),
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'forecast':
        return {
          name: 'forecast_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'forecast',
            date_col: c.date_col || '',
            value_col: c.value_col || '',
            horizon: c.horizon === undefined || c.horizon === '' ? 12 : Number(c.horizon),
            method: c.method || 'auto',
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'stat_test':
        return {
          name: 'stat_test_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'stat_test',
            test_type: c.test_type || 'ttest',
            column_a: c.column_a || '',
            column_b: c.column_b || '',
            group_col: c.group_col || '',
            alpha: c.alpha === undefined || c.alpha === '' ? 0.05 : Number(c.alpha),
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'regression':
        return {
          name: 'regression_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'regression',
            target_col: c.target_col || '',
            feature_cols: c.feature_cols ? String(c.feature_cols).split(',').map((s) => s.trim()).filter(Boolean) : [],
            regression_type: c.regression_type || 'linear',
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'quality':
        return {
          name: 'quality_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'quality',
            table_name: c.table_name || '',
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'comparison':
        return {
          name: 'comparison_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            input_data2: secondInput,
            operation: 'comparison',
            kind: c.kind || 'periods',
            date_col: c.date_col || '',
            metric_col: c.metric_col || '',
            group_col: c.group_col || '',
            period_type: c.period_type || 'month',
            key_column: c.key_column || '',
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'cohort':
        return {
          name: 'cohort_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'cohort',
            kind: c.kind || 'retention',
            user_id_col: c.user_id_col || '',
            date_col: c.date_col || '',
            cohort_period: c.cohort_period || 'month',
            revenue_col: c.revenue_col || '',
            max_periods: c.max_periods === undefined || c.max_periods === '' ? 12 : Number(c.max_periods),
            inactivity_days: c.inactivity_days === undefined || c.inactivity_days === '' ? 30 : Number(c.inactivity_days),
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'ml_model':
        return {
          name: 'ml_model_' + node.id,
          type: 'transform',
          params: {
            input_data: firstInput,
            operation: 'ml_model',
            target_col: c.target_col || '',
            task: c.task || 'auto',
            output_name: node.id,
          },
          depends_on: deps,
        };
      case 'dashboard':
        // Sink: persist upstream data to the warehouse so the client can
        // auto-build a dashboard from it after a successful run.
        {
          const safeName = this._currentName.replace(/[^a-zA-Z0-9_]/g, '_').replace(/^_+|_+$/g, '') || 'pipeline';
          const safeId = node.id.replace('n_', '');
          return {
            name: 'dashboard_' + node.id,
            type: 'load',
            params: {
              input_data: firstInput,
              table_name: `pipeline_${safeName}_${safeId}`,
              if_exists: 'replace',
              output_name: node.id,
            },
            depends_on: deps,
          };
        }
      case 'export':
        return {
          name: 'export_' + node.id,
          type: 'load',
          params: {
            input_data: firstInput,
            format: c.format || 'csv',
            filename: c.filename || ('pipeline_' + node.id),
            output_name: node.id,
          },
          depends_on: deps,
        };
      default:
        return null;
    }
  },

  _findInputFor(nodeId) {
    // Find the edge that connects to this node's input
    const edges = this._blueprint.getEdges();
    const edge = edges.find((e) => e.to === nodeId);
    return edge ? edge.from : '';
  },

  _topologicalSort(nodes, edges) {
    // Build adjacency
    const adj = {};
    const inDeg = {};
    nodes.forEach((n) => { adj[n.id] = []; inDeg[n.id] = 0; });
    edges.forEach((e) => {
      if (adj[e.from]) adj[e.from].push(e.to);
      if (inDeg[e.to] !== undefined) inDeg[e.to]++;
    });

    const queue = [];
    for (const [id, deg] of Object.entries(inDeg)) {
      if (deg === 0) queue.push(id);
    }

    const sorted = [];
    while (queue.length) {
      const id = queue.shift();
      const node = nodes.find((n) => n.id === id);
      if (node) sorted.push(node);
      for (const neighbor of adj[id] || []) {
        inDeg[neighbor]--;
        if (inDeg[neighbor] === 0) queue.push(neighbor);
      }
    }

    return sorted;
  },

  /* "" Utility """""""""""""""""""""""""""""" */
  _rename(name) {
    this._currentName = name || 'Untitled Pipeline';
    this._dirty = true;
    this._updateStatus();
  },

  _onBlueprintChange(state) {
    this._dirty = true;
    this._updateStatus();
    this._updateEmptyState();
    this._updateUndoRedoButtons();
    this._saveSessionState();
  },

  _updateStatus() {
    const el = document.getElementById('bp-status');
    if (el) {
      el.classList.toggle('dirty', this._dirty);
      el.innerHTML = `<i class="fas ${this._dirty ? 'fa-circle' : 'fa-check-circle'}"></i>${this._dirty ? 'Unsaved' : 'Saved'}`;
    }
  },

  _updateEmptyState() {
    const empty = document.getElementById('bp-empty-state');
    if (!empty) return;
    const nodes = this._blueprint ? this._blueprint.getNodes() : [];
    empty.style.display = nodes.length === 0 ? 'flex' : 'none';
  },

  /* "" Undo / Redo """""""""""""""""""""""" */
  _undo() {
    if (!this._blueprint) return;
    const undone = this._blueprint.undo();
    if (undone) {
      this._dirty = true;
      this._updateStatus();
      this._updateEmptyState();
      this._updateUndoRedoButtons();
      // Deselect node to avoid stale references
      this._hideNodeConfig();
    }
  },

  _redo() {
    if (!this._blueprint) return;
    const redone = this._blueprint.redo();
    if (redone) {
      this._dirty = true;
      this._updateStatus();
      this._updateEmptyState();
      this._updateUndoRedoButtons();
      this._hideNodeConfig();
    }
  },

  _updateUndoRedoButtons() {
    const undoBtn = document.getElementById('bp-undo-btn');
    const redoBtn = document.getElementById('bp-redo-btn');
    if (undoBtn) {
      undoBtn.disabled = !this._blueprint || !this._blueprint.canUndo();
    }
    if (redoBtn) {
      redoBtn.disabled = !this._blueprint || !this._blueprint.canRedo();
    }
  },

  /* "" Data Preview & Stats """"""""""""""" */
  _togglePreview(table, previewId, statsId) {
    const previewEl = document.getElementById(previewId);
    const statsEl = document.getElementById(statsId);
    if (!previewEl) return;

    // If preview is already visible, hide it
    if (!previewEl.classList.contains('hidden')) {
      previewEl.classList.add('hidden');
      return;
    }

    // Hide stats panel if open
    if (statsEl) statsEl.classList.add('hidden');

    // Show loading
    previewEl.classList.remove('hidden');
    previewEl.innerHTML = '<div class="p-3 text-center text-[10px] text-gray-400"><i class="fas fa-spinner fa-spin mr-1"></i>Loading preview...</div>';

    // Fetch first 3 rows
    const metaEl = document.getElementById(`${previewId}-meta`);
    if (typeof API !== 'undefined' && API.tableSample) {
      API.tableSample(table, 4)
        .then((data) => {
          if (!data || !data.rows || !data.columns) {
            previewEl.innerHTML = '<div class="p-3 text-center text-[10px] text-red-400">No preview available</div>';
            return;
          }
          const rows = data.rows || [];
          const cols = data.columns || [];
          if (metaEl) metaEl.textContent = `${rows.length} rows preview`;
          // Build inline table (max 4 rows)
          let html = '<div class="overflow-x-auto p-2">';
          html += '<table class="w-full text-[10px] border-collapse">';
          html += '<thead><tr class="bg-gray-100 dark:bg-gray-700">';
          html += cols.map(c => `<th class="px-2 py-1 text-left font-semibold border-b dark:border-gray-600 whitespace-nowrap">${App.escapeHtml(c)}</th>`).join('');
          html += '</tr></thead><tbody>';
          const showRows = rows.slice(0, 3);
          showRows.forEach((row) => {
            html += '<tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50">';
            html += cols.map(c => {
              const val = row[c];
              const display = val === null || val === undefined ? '<span class="text-gray-400 italic">NULL</span>' : App.escapeHtml(String(val));
              return `<td class="px-2 py-1 border-b dark:border-gray-700 truncate max-w-[120px]">${display}</td>`;
            }).join('');
            html += '</tr>';
          });
          if (rows.length > 3) {
            html += `<tr><td colspan="${cols.length}" class="px-2 py-1 text-center text-gray-400 italic">... and ${rows.length - 3} more rows</td></tr>`;
          }
          html += '</tbody></table>';
          // Quick stats row
          html += `<div class="flex gap-3 px-2 py-1 text-[9px] text-gray-500 border-t dark:border-gray-700 mt-1">
            <span><strong>Rows:</strong> ${rows.length}</span>
            <span><strong>Columns:</strong> ${cols.length}</span>
            <button onclick="AutomationPage._exportTable('${table}','csv')" class="text-primary-500 hover:underline ml-auto">Download CSV</button>
          </div>`;
          html += '</div>';
          previewEl.innerHTML = html;
        })
        .catch((err) => {
          previewEl.innerHTML = `<div class="p-3 text-center text-[10px] text-red-400">Preview failed: ${App.escapeHtml(err.message || 'Error')}</div>`;
        });
    } else {
      previewEl.innerHTML = '<div class="p-3 text-center text-[10px] text-red-400">API not available</div>';
    }
  },

  _toggleStats(table, statsId) {
    const statsEl = document.getElementById(statsId);
    if (!statsEl) return;

    // If stats already visible, toggle off
    if (!statsEl.classList.contains('hidden')) {
      statsEl.classList.add('hidden');
      return;
    }

    statsEl.classList.remove('hidden');
    statsEl.innerHTML = '<div class="p-3 text-center text-[10px] text-gray-400"><i class="fas fa-spinner fa-spin mr-1"></i>Loading statistics...</div>';

    if (typeof API !== 'undefined' && API.profile) {
      API.profile(table)
        .then((data) => {
          if (!data || !data.success) {
            statsEl.innerHTML = `<div class="p-3 text-center text-[10px] text-red-400">${App.escapeHtml(data?.error || 'Stats not available')}</div>`;
            return;
          }
          // API returns {success, profile:{row_count, columns, ...}} for non-empty
          // tables but a flat payload for empty ones — normalize to one shape.
          const p = data.profile || data;
          const cols = p.columns || [];
          const totalRows = p.row_count || 0;
          let html = '<div class="p-2 space-y-1">';
          html += `<div class="flex gap-3 text-[10px] text-gray-500 mb-2 px-1">
            <span><strong>Rows:</strong> ${totalRows.toLocaleString()}</span>
            <span><strong>Cols:</strong> ${p.column_count || cols.length}</span>
            <span><strong>Missing:</strong> ${p.missing_cells_pct || 0}%</span>
            <span><strong>Dup:</strong> ${p.duplicate_pct || 0}%</span>
          </div>`;
          // Column stats table
          html += '<table class="w-full text-[9px] border-collapse">';
          html += '<thead><tr class="bg-gray-100 dark:bg-gray-700">';
          html += '<th class="px-1.5 py-0.5 text-left font-semibold border-b dark:border-gray-600">Column</th>';
          html += '<th class="px-1.5 py-0.5 text-left font-semibold border-b dark:border-gray-600">Type</th>';
          html += '<th class="px-1.5 py-0.5 text-right font-semibold border-b dark:border-gray-600">Count</th>';
          html += '<th class="px-1.5 py-0.5 text-right font-semibold border-b dark:border-gray-600">Null%</th>';
          html += '<th class="px-1.5 py-0.5 text-right font-semibold border-b dark:border-gray-600">Mean</th>';
          html += '<th class="px-1.5 py-0.5 text-right font-semibold border-b dark:border-gray-600">Min</th>';
          html += '<th class="px-1.5 py-0.5 text-right font-semibold border-b dark:border-gray-600">Max</th>';
          html += '</tr></thead><tbody>';
          cols.forEach((col) => {
            const name = col.name || '';
            const dtype = col.dtype || '';
            const count = col.count ?? '';
            const nullPct = col.null_pct !== undefined ? col.null_pct : (col.null_count !== undefined && totalRows > 0 ? (col.null_count / totalRows * 100).toFixed(1) : '');
            const mean = col.mean !== undefined ? (typeof col.mean === 'number' ? col.mean.toFixed(2) : col.mean) : '';
            const min = col.min !== undefined ? col.min : '';
            const max = col.max !== undefined ? col.max : '';
            html += `<tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50">
              <td class="px-1.5 py-0.5 border-b dark:border-gray-700 font-medium">${App.escapeHtml(name)}</td>
              <td class="px-1.5 py-0.5 border-b dark:border-gray-700 text-gray-500">${App.escapeHtml(dtype)}</td>
              <td class="px-1.5 py-0.5 border-b dark:border-gray-700 text-right">${count}</td>
              <td class="px-1.5 py-0.5 border-b dark:border-gray-700 text-right">${nullPct}${nullPct !== '' ? '%' : ''}</td>
              <td class="px-1.5 py-0.5 border-b dark:border-gray-700 text-right font-mono">${App.escapeHtml(String(mean))}</td>
              <td class="px-1.5 py-0.5 border-b dark:border-gray-700 text-right font-mono">${App.escapeHtml(String(min))}</td>
              <td class="px-1.5 py-0.5 border-b dark:border-gray-700 text-right font-mono">${App.escapeHtml(String(max))}</td>
            </tr>`;
          });
          html += '</tbody></table>';
          html += '</div>';
          statsEl.innerHTML = html;
        })
        .catch((err) => {
          statsEl.innerHTML = `<div class="p-3 text-center text-[10px] text-red-400">Stats failed: ${App.escapeHtml(err.message || 'Error')}</div>`;
        });
    } else {
      statsEl.innerHTML = '<div class="p-3 text-center text-[10px] text-red-400">API not available</div>';
    }
  },

  _showExportMenu(event, table) {
    // Close any existing export menu
    const existing = document.getElementById('bp-export-menu');
    if (existing) existing.remove();

    const btn = event.currentTarget;
    const rect = btn.getBoundingClientRect();

    const menu = document.createElement('div');
    menu.id = 'bp-export-menu';
    menu.className = 'fixed z-[100] bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-xl py-1 text-xs';
    menu.style.left = Math.min(rect.left, window.innerWidth - 180) + 'px';
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.minWidth = '160px';
    menu.innerHTML = `
      <div class="px-3 py-1.5 text-[9px] text-gray-400 uppercase tracking-wider font-semibold">Download as</div>
      <button class="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2" onclick="AutomationPage._exportTable('${table}','csv')"><i class="fas fa-file-csv text-green-500 w-4"></i>CSV</button>
      <button class="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2" onclick="AutomationPage._exportTable('${table}','xlsx')"><i class="fas fa-file-excel text-emerald-500 w-4"></i>Excel (XLSX)</button>
      <button class="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2" onclick="AutomationPage._exportTable('${table}','parquet')"><i class="fas fa-file-code text-blue-500 w-4"></i>Parquet</button>
      <button class="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2" onclick="AutomationPage._exportTable('${table}','md')"><i class="fas fa-copy text-gray-500 w-4"></i>Markdown (clipboard)</button>
      <div class="border-t dark:border-gray-700 my-1"></div>
      <button class="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2" onclick="AutomationPage._exportTable('${table}','html')"><i class="fas fa-file-code text-orange-500 w-4"></i>HTML Report</button>
      <button class="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2" onclick="AutomationPage._exportTable('${table}','pptx')"><i class="fas fa-file-powerpoint text-red-500 w-4"></i>PowerPoint</button>
      <div class="border-t dark:border-gray-700 my-1"></div>
      <button class="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2" onclick="AutomationPage._exportBI('${table}','powerbi')"><i class="fab fa-microsoft text-yellow-500 w-4"></i>Power BI</button>
      <button class="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2" onclick="AutomationPage._exportBI('${table}','tableau')"><i class="fas fa-chart-pie text-blue-500 w-4"></i>Tableau</button>
    `;
    document.body.appendChild(menu);

    // Close on click outside
    const closeMenu = (e) => {
      if (!menu.contains(e.target) && e.target !== btn) {
        menu.remove();
        document.removeEventListener('click', closeMenu);
      }
    };
    setTimeout(() => document.addEventListener('click', closeMenu), 10);
  },

  /* "" Pipeline Export Actions """""""""""" */
  /*  LLM Auto-Report  */
  /* "" Minimal markdown rendering for LLM output ""
     Input MUST already be HTML-escaped (App.escapeHtml) — this only adds tags,
     so it is XSS-safe. Handles **bold**, *italic*, `code`, # headings, - bullets. */
  _mdToHtml(escaped) {
    if (!escaped) return '';
    var h = escaped;
    h = h.replace(/`([^`]+)`/g, '<code class="bg-gray-100 dark:bg-gray-700 px-1 rounded font-mono">$1</code>');
    h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, '$1<em class="text-gray-500">$2</em>');
    return h.split('\n').map(function(line) {
      var t = line.trim();
      if (/^#{1,6}\s+/.test(t)) return '<div class="font-semibold text-[11px] mt-1">' + t.replace(/^#{1,6}\s+/, '') + '</div>';
      if (/^-\s+/.test(t)) return '<div class="ml-3">' + t + '</div>';
      return line;
    }).join('<br>');
  },

  _generateAutoReport(table) {
    if (!table || typeof API === 'undefined') return;
    var el = document.getElementById('bp-llm-report');
    if (!el) return;
    API.profile(table).then(function(pdRaw) {
      if (!pdRaw || !pdRaw.success) { el.innerHTML = '<div class="text-center py-3 text-xs text-red-400">Stats unavailable</div>'; return; }
      var pd = pdRaw.profile || pdRaw;
      API.llmInsights(table, 'Generate detailed statistical analysis with key findings, trends, recommendations').then(function(id) {
        var ins = id?.insights || {}, narr = ins.narrative || '', finds = ins.findings || [], recs = ins.recommendations || [];
        var cols = pd.columns || [], rc = pd.row_count || 0, nc = cols.filter(function(c){return c.mean!==undefined}).length, mp = pd.missing_cells_pct||0;
        var html = '<div class="grid grid-cols-4 gap-1.5 mb-2">'
          + '<div class="bg-blue-50 p-1.5 rounded text-center"><div class="text-xs font-bold text-blue-600">'+rc.toLocaleString()+'</div><div class="text-[8px] text-gray-500">Rows</div></div>'
          + '<div class="bg-green-50 p-1.5 rounded text-center"><div class="text-xs font-bold text-green-600">'+cols.length+'</div><div class="text-[8px] text-gray-500">Cols</div></div>'
          + '<div class="bg-purple-50 p-1.5 rounded text-center"><div class="text-xs font-bold text-purple-600">'+nc+'</div><div class="text-[8px] text-gray-500">Numeric</div></div>'
          + '<div class="bg-amber-50 p-1.5 rounded text-center"><div class="text-xs font-bold text-amber-600">'+mp+'%</div><div class="text-[8px] text-gray-500">Missing</div></div></div>';
        html += '<table class="w-full text-[9px] border-collapse mb-2"><thead><tr class="bg-gray-100"><th class="px-1.5 py-0.5 text-left">Col</th><th class="px-1.5 py-0.5 text-left">Type</th><th class="px-1.5 py-0.5 text-right">Null%</th><th class="px-1.5 py-0.5 text-right">Mean</th><th class="px-1.5 py-0.5 text-right">Min</th><th class="px-1.5 py-0.5 text-right">Max</th></tr></thead><tbody>';
        for (var i = 0; i < Math.min(cols.length, 8); i++) {
          var c = cols[i];
          html += '<tr class="hover:bg-gray-50"><td class="px-1.5 py-0.5 border-b font-medium">'+App.escapeHtml(c.name||'')+'</td><td class="px-1.5 py-0.5 border-b text-gray-500">'+App.escapeHtml(c.dtype||'')+'</td><td class="px-1.5 py-0.5 border-b text-right">'+(c.null_pct?.toFixed(1)||0)+'%</td><td class="px-1.5 py-0.5 border-b text-right font-mono">'+(c.mean!==undefined?(typeof c.mean==='number'?c.mean.toFixed(1):c.mean):'-')+'</td><td class="px-1.5 py-0.5 border-b text-right font-mono">'+(c.min!==undefined?c.min:'-')+'</td><td class="px-1.5 py-0.5 border-b text-right font-mono">'+(c.max!==undefined?c.max:'-')+'</td></tr>';
        }
        html += '</tbody></table>';
        if (narr) html += '<div class="text-[10px] leading-relaxed text-gray-700 p-2 bg-white rounded border mb-2">'+AutomationPage._mdToHtml(App.escapeHtml(narr))+'</div>';
        if (finds.length) html += '<div class="mb-2"><span class="text-[10px] font-semibold text-purple-600">Key Findings</span><ul class="text-[9px] text-gray-600 mt-0.5 space-y-0.5 ml-3 list-disc">'+finds.slice(0,5).map(function(f){return '<li>'+AutomationPage._mdToHtml(App.escapeHtml(f))+'</li>';}).join('')+'</ul></div>';
        if (recs.length) html += '<div class="mb-2"><span class="text-[10px] font-semibold text-green-600">Recommendations</span><ul class="text-[9px] text-gray-600 mt-0.5 space-y-0.5 ml-3 list-disc">'+recs.slice(0,3).map(function(r){return '<li>'+AutomationPage._mdToHtml(App.escapeHtml(r))+'</li>';}).join('')+'</ul></div>';
        html += '<div class="flex gap-1.5 mt-2 pt-2 border-t"><button onclick="AutomationPage._exportTable(\''+table+'\',\'csv\')" class="flex-1 text-[9px] bg-green-500 text-white py-1 rounded">CSV</button><button onclick="AutomationPage._exportTable(\''+table+'\',\'xlsx\')" class="flex-1 text-[9px] bg-emerald-500 text-white py-1 rounded">Excel</button><button onclick="AutomationPage._exportTable(\''+table+'\',\'html\')" class="flex-1 text-[9px] bg-orange-500 text-white py-1 rounded">HTML</button><button onclick="AutomationPage._exportTable(\''+table+'\',\'pptx\')" class="flex-1 text-[9px] bg-red-500 text-white py-1 rounded">PPTX</button></div>';
        el.innerHTML = html;
        var hs = document.querySelector('#bp-llm-report').closest('div').querySelector('h4 span');
        if (hs) hs.textContent = '- ready';
      }).catch(function() { el.innerHTML = '<div class="text-center py-3 text-xs text-amber-500">LLM unavailable - stats shown separately via Preview/Stats buttons</div>'; });
    }).catch(function() { el.innerHTML = '<div class="text-center py-3 text-xs text-red-400">Failed to load stats</div>'; });
  },

  _threePassReview(table) {
    if (!table) return;
    var btn = document.getElementById('bp-3pass-btn'), el = document.getElementById('bp-llm-report');
    if (!el || !btn) return;
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>3-Pass...';
    el.innerHTML = '<div class="text-center py-3 text-[10px] text-purple-400"><i class="fas fa-spinner fa-spin mr-1"></i>Pass 1/3...</div>';
    API.profile(table).then(function(pdRaw) {
      if (!pdRaw?.success) throw new Error('Fail');
      var pd = pdRaw.profile || pdRaw;
      var nc = pd.column_count ?? (pd.columns || []).length;
      var summary = pd.row_count+' rows, '+nc+' cols';
      var colInfo = (pd.columns||[]).slice(0,8).map(function(c){return c.name+'('+c.dtype+')';}).join(', ');
      API.llmInsights(table, 'Generate complete business report: '+summary+'. Columns: '+colInfo).then(function(r1) {
        var r1t = r1?.insights?.narrative||'';
        el.innerHTML = '<div class="text-center py-3 text-[10px] text-purple-400">Pass 2/3: Reviewing...</div>';
        API.llmInsights(table, 'Critique and improve this report: '+r1t).then(function(r2) {
          var r2t = r2?.insights?.narrative||r1t;
          el.innerHTML = '<div class="text-center py-3 text-[10px] text-purple-400">Pass 3/3: Final quality...</div>';
          API.llmInsights(table, 'Final quality review. Correct errors, improve clarity, add numbers: '+r2t).then(function(r3) {
            var finalReport = r3?.insights?.narrative||r2t;
            el.innerHTML = '<div class="flex items-center gap-1 mb-2"><span class="text-[9px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded font-medium">PASSED 3-PASS</span></div><div class="text-[10px] leading-relaxed text-gray-700 p-2 bg-white rounded border max-h-48 overflow-y-auto">'+AutomationPage._mdToHtml(App.escapeHtml(finalReport))+'</div><div class="flex gap-1.5 mt-2 pt-2 border-t"><button onclick="AutomationPage._exportTable(\''+table+'\',\'csv\')" class="flex-1 text-[9px] bg-green-500 text-white py-1 rounded">CSV</button><button onclick="AutomationPage._exportTable(\''+table+'\',\'xlsx\')" class="flex-1 text-[9px] bg-emerald-500 text-white py-1 rounded">Excel</button><button onclick="AutomationPage._exportTable(\''+table+'\',\'html\')" class="flex-1 text-[9px] bg-orange-500 text-white py-1 rounded">HTML</button><button onclick="AutomationPage._exportTable(\''+table+'\',\'pptx\')" class="flex-1 text-[9px] bg-red-500 text-white py-1 rounded">PPTX</button></div>';
            btn.disabled = false; btn.innerHTML = '<i class="fas fa-magic mr-1"></i> 3-Pass Review';
            App.success('Report after 3-pass!');
          }).catch(function(){btn.disabled=false;btn.innerHTML='<i class="fas fa-magic mr-1"></i> 3-Pass';});
        }).catch(function(){btn.disabled=false;btn.innerHTML='<i class="fas fa-magic mr-1"></i> 3-Pass';});
      }).catch(function(){btn.disabled=false;btn.innerHTML='<i class="fas fa-magic mr-1"></i> 3-Pass';});
    }).catch(function(){el.innerHTML='<div class="text-center py-3 text-xs text-red-400">Failed to load stats</div>';btn.disabled=false;btn.innerHTML='<i class="fas fa-magic mr-1"></i> 3-Pass';});
  },

  _exportTable(table, format) {
    if (!table) {
      App.error('No table to export');
      return;
    }
    const encoded = encodeURIComponent(table);
    const base = window.__API_BASE__ || '';

    // Formats that trigger a direct download via window.open (simple file download)
    if (format === 'csv' || format === 'xlsx' || format === 'parquet') {
      window.open(`${base}/export/${format}/${encoded}`, '_blank');
      return;
    }

    // Markdown: copy to clipboard
    if (format === 'md') {
      fetch(base + `/export/markdown/${encoded}`)
        .then(r => r.text())
        .then(text => navigator.clipboard.writeText(text))
        .then(() => App.success('Markdown copied to clipboard!'))
        .catch(() => App.error('Failed to copy markdown'));
      return;
    }

    // Presentation formats: fetch as blob + download (catches errors vs window.open)
    const urlMap = { html: `${base}/export/html/${encoded}`, pptx: `${base}/export/pptx/${encoded}` };
    const url = urlMap[format];
    if (!url) return;

    App.info(`Preparing ${format.toUpperCase()} export...`);
    fetch(url)
      .then(async (resp) => {
        const contentType = resp.headers.get('content-type') || '';
        if (!resp.ok) {
          // Try to parse error message
          if (contentType.includes('application/json')) {
            const err = await resp.json();
            throw new Error(err.detail || err.error || `HTTP ${resp.status}`);
          }
          const text = await resp.text();
          throw new Error(text.substring(0, 200) || `HTTP ${resp.status}`);
        }
        // If it's JSON, it might be an error object (python-pptx missing case)
        if (contentType.includes('application/json')) {
          const data = await resp.json();
          if (data && !data.success) {
            throw new Error(data.error || 'Export failed');
          }
          // JSON success " show in modal
          const title = format === 'html' ? 'HTML Report' : 'PowerPoint';
          const html = `<div class="text-sm space-y-3">
            <div class="flex items-center gap-2 text-green-600">
              <i class="fas fa-check-circle"></i><span>Export data ready</span>
            </div>
            ${data.data ? `<pre class="text-[10px] bg-gray-50 dark:bg-gray-800 p-3 rounded-lg overflow-x-auto">${App.escapeHtml(data.data.substring(0, 1000))}</pre>` : ''}
          </div>`;
          if (typeof Modal !== 'undefined') Modal.show(title, html, { wide: false });
          return;
        }
        // Blob download
        const blob = await resp.blob();
        const ext = format === 'html' ? 'html' : 'pptx';
        const filename = `${table.replace(/[^a-zA-Z0-9_]/g, '_')}_report.${ext}`;
        const dlUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = dlUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(dlUrl);
        App.success(`${format.toUpperCase()} report downloaded!`);
      })
      .catch((err) => {
        App.error(`Export failed: ${err.message}`);
      });
  },

  _exportAllTables(tablesCsv) {
    const tables = tablesCsv ? tablesCsv.split(',').filter(Boolean) : [];
    if (tables.length === 0) {
      App.error('No tables to export');
      return;
    }
    if (tables.length === 1) {
      this._exportTable(tables[0], 'csv');
      return;
    }
    // ZIP containing only the pipeline output tables
    const base = window.__API_BASE__ || '';
    window.open(`${base}/export/zip?tables=${encodeURIComponent(tables.join(','))}`, '_blank');
    App.success('Downloading tables ZIP...');
  },

  _exportBI(table, format) {
    if (!table) {
      App.error('Select a table first for BI export');
      return;
    }
    if (typeof API !== 'undefined' && API.exportBI) {
      API.exportBI(table, format)
        .then((result) => {
          if (!result || result.error) {
            App.error(result?.error || 'BI export failed');
            return;
          }
          const title = format === 'powerbi' ? 'Power BI Export' : 'Tableau Export';
          const html = `
            <div class="space-y-3 text-sm">
              <div class="flex items-center gap-2 text-green-600">
                <i class="fas fa-check-circle"></i>
                <span class="font-medium">${result.row_count || 0} rows exported</span>
              </div>
              ${result.instructions ? `
                <div class="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-xs">
                  <strong>Instructions:</strong><br>${result.instructions}
                </div>
              ` : ''}
              ${result.config ? `
                <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
                  <pre class="text-[10px] overflow-x-auto">${JSON.stringify(result.config, null, 2).substring(0, 1500)}</pre>
                </div>
              ` : ''}
              ${result.data ? `
                <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
                  <pre class="text-[10px] overflow-x-auto">${App.escapeHtml(result.data.substring(0, 500))}</pre>
                </div>
              ` : ''}
              <button onclick="AutomationPage._exportTable('${table}','csv')"
                class="w-full text-xs bg-primary-500 hover:bg-primary-600 text-white px-3 py-2 rounded-lg">
                <i class="fas fa-download mr-1"></i> Download CSV
              </button>
            </div>`;
          if (typeof Modal !== 'undefined') Modal.show(title, html, { wide: false });
        })
        .catch((err) => App.error('BI export error: ' + (err.message || 'Unknown')));
    } else {
      App.error('Export API not available');
    }
  },

  _saveSessionState() {
    try {
      const data = this._blueprint ? this._blueprint.serialize() : null;
      if (data) {
        data._name = this._currentName;
        sessionStorage.setItem('bp_state', JSON.stringify(data));
      }
    } catch (_) {}
  },

  _restoreSessionState() {
    try {
      const saved = sessionStorage.getItem('bp_state');
      if (saved) {
        const data = JSON.parse(saved);
        this._currentName = data._name || 'Untitled Pipeline';
        delete data._name;
        this._blueprint.deserialize(data);
        document.getElementById('bp-name').value = this._currentName;
        this._dirty = false;
        this._updateStatus();
        this._updateEmptyState();
      }
    } catch (_) {}
  },

  /* "" Keyboard Shortcuts """"""""""""""""" */
  _onKeyDown(e) {
    // Only handle when automation page is active (App.currentPage is the page module)
    if (App.currentPage !== window.AutomationPage) return;

    // Delete selected node
    if ((e.key === 'Delete' || e.key === 'Backspace') && this._blueprint) {
      const activeEl = document.activeElement;
      const isInput = activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA' || activeEl.tagName === 'SELECT' || activeEl.isContentEditable);
      if (!isInput && this._blueprint._selectedNodeId) {
        e.preventDefault();
        this._blueprint.removeNode(this._blueprint._selectedNodeId);
        this._hideNodeConfig();
        this._dirty = true;
        this._updateStatus();
        this._updateEmptyState();
      }
    }

    // Ctrl+S to save
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      this._savePipeline();
    }

    // Ctrl+Enter to run
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      this._runPipeline();
    }

    // Ctrl+Z to undo
    if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
      e.preventDefault();
      this._undo();
    }

    // Ctrl+Y or Ctrl+Shift+Z to redo
    if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
      e.preventDefault();
      this._redo();
    }
  },
};

// "" Page Registration """"""""""""""""""
window.AutomationPage = AutomationPage;
(window._PAGES = window._PAGES || {})['#automation'] = AutomationPage;
