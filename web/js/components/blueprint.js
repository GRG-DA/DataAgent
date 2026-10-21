/**
 * Blueprint Canvas Engine " Visual Workflow Builder
 *
 * A self-contained drag-and-drop node canvas with port connections,
 * SVG bezier edges, zoom/pan, and serialization. No external deps.
 *
 * Usage:
 *   const bp = new Blueprint('container-id', { onNodeSelect, onNodeDeselect, onChange });
 *   bp.addNode('data_source', 100, 200, { label: 'Sales Data' });
 *   const json = bp.serialize();
 *   bp.deserialize(json);
 */
(function () {
  'use strict';

  // "" Helpers """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""
  const _id = () => 'n_' + Math.random().toString(36).slice(2, 9);
  const _eid = () => 'e_' + Math.random().toString(36).slice(2, 9);
  const NODE_WIDTH = 220;

  const _shadeColor = (hex, percent) => {
    const h = hex.replace('#', '');
    const num = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    const amt = Math.round(2.55 * percent);
    const r = Math.min(255, Math.max(0, (num >> 16) + amt));
    const g = Math.min(255, Math.max(0, ((num >> 8) & 0xff) + amt));
    const b = Math.min(255, Math.max(0, (num & 0xff) + amt));
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  };

  // "" Node Definitions """"""""""""""""""""""""""""""""""""""""""""""""""
  const NODE_TYPES = {
    data_source: {
      label: 'Data Source',
      icon: 'fa-database',
      color: '#3b82f6', // blue
      inputs: 0,
      outputs: 1,
      defaultConfig: { table_name: '', filter: '', limit: 1000 },
    },
    cleaning: {
      label: 'Cleaning',
      icon: 'fa-broom',
      color: '#10b981', // green
      inputs: 1,
      outputs: 1,
      defaultConfig: {
        remove_duplicates: true,
        null_strategy: 'drop',
        column_rules: {},
      },
    },
    sql: {
      label: 'SQL Query',
      icon: 'fa-terminal',
      color: '#8b5cf6', // purple
      inputs: 1,
      outputs: 1,
      defaultConfig: { prompt: '', query: 'SELECT * FROM input', limit: 1000 },
    },
    python: {
      label: 'Python',
      icon: 'fa-code',
      color: '#8b5cf6', // purple
      inputs: 1,
      outputs: 1,
      defaultConfig: { prompt: '', code: '', var_name: 'df' },
    },
    analysis: {
      label: 'Analysis',
      icon: 'fa-microscope',
      color: '#6366f1', // indigo
      inputs: 1,
      outputs: 1,
      defaultConfig: {
        analysis_type: 'statistics',
        target_col: '',
        feature_cols: [],
      },
    },
    chart: {
      label: 'Chart',
      icon: 'fa-chart-bar',
      color: '#f59e0b', // orange
      inputs: 1,
      outputs: 0,
      defaultConfig: {
        chart_type: 'bar',
        x_col: '',
        y_col: '',
        group_col: '',
        title: '',
        export_target: 'none',
        recommended_charts: [],
      },
    },
    insights: {
      label: 'Insights',
      icon: 'fa-lightbulb',
      color: '#ec4899', // pink
      inputs: 1,
      outputs: 0,
      defaultConfig: { prompt: 'Summarize key trends and patterns in this data.' },
    },
    filter: {
      label: 'Filter',
      icon: 'fa-filter',
      color: '#06b6d4', // cyan
      inputs: 1,
      outputs: 1,
      defaultConfig: { column: '', operator: 'equals', value: '', condition: '' },
    },
    join: {
      label: 'Join',
      icon: 'fa-code-branch',
      color: '#f43f5e', // rose
      inputs: 2,
      outputs: 1,
      defaultConfig: { join_type: 'inner', left_key: '', right_key: '', second_input: '' },
    },
    aggregate: {
      label: 'Aggregate',
      icon: 'fa-calculator',
      color: '#f97316', // orange
      inputs: 1,
      outputs: 1,
      defaultConfig: { group_by: '', aggregate_col: '', aggregate_fn: 'sum', output_name: '' },
    },
    anomaly: {
      label: 'Anomaly Detection',
      icon: 'fa-exclamation-triangle',
      color: '#ef4444', // red
      inputs: 1,
      outputs: 1,
      defaultConfig: { column: '', method: 'iqr', threshold: 1.5 },
    },
    forecast: {
      label: 'Forecast',
      icon: 'fa-chart-line',
      color: '#14b8a6', // teal
      inputs: 1,
      outputs: 1,
      defaultConfig: { date_col: '', value_col: '', horizon: 12, method: 'auto' },
    },
    stat_test: {
      label: 'Statistical Test',
      icon: 'fa-vial',
      color: '#8b5cf6', // purple
      inputs: 1,
      outputs: 1,
      defaultConfig: { test_type: 'ttest', column_a: '', column_b: '', group_col: '', alpha: 0.05 },
    },
    regression: {
      label: 'Regression',
      icon: 'fa-chart-scatter',
      color: '#0ea5e9', // sky
      inputs: 1,
      outputs: 1,
      defaultConfig: { target_col: '', feature_cols: '', regression_type: 'linear' },
    },
    quality: {
      label: 'Quality Check',
      icon: 'fa-shield-heart',
      color: '#10b981', // green
      inputs: 1,
      outputs: 1,
      defaultConfig: { table_name: '' },
    },
    comparison: {
      label: 'Comparison',
      icon: 'fa-scale-balanced',
      color: '#f59e0b', // amber
      inputs: 2,
      outputs: 1,
      defaultConfig: { kind: 'periods', date_col: '', metric_col: '', group_col: '', period_type: 'month', key_column: '', second_input: '' },
    },
    cohort: {
      label: 'Cohort / Retention',
      icon: 'fa-users',
      color: '#ec4899', // pink
      inputs: 1,
      outputs: 1,
      defaultConfig: { kind: 'retention', user_id_col: '', date_col: '', cohort_period: 'month', revenue_col: '', max_periods: 12, inactivity_days: 30 },
    },
    ml_model: {
      label: 'AutoML / Model',
      icon: 'fa-robot',
      color: '#6366f1', // indigo
      inputs: 1,
      outputs: 1,
      defaultConfig: { target_col: '', task: 'auto' },
    },
    dashboard: {
      label: 'Generate Dashboard',
      icon: 'fa-gauge-high',
      color: '#a855f7', // purple
      inputs: 1,
      outputs: 0,
      defaultConfig: { dashboard_name: '', kpi_focus: '', sections: 'overview' },
    },
    export: {
      label: 'Export',
      icon: 'fa-file-export',
      color: '#8b5cf6', // purple
      inputs: 1,
      outputs: 0,
      defaultConfig: { format: 'csv', filename: '', destination: '' },
    },
  };

  // "" Connection Validation Rules (auto-built from NODE_TYPES) """""""""
  // Any node type with outputs >= 1 can connect to any node type with inputs >= 1
  const CONNECTION_RULES = (() => {
    const rules = {};
    const sourceTypes = Object.entries(NODE_TYPES).filter(([, def]) => def.outputs > 0).map(([type]) => type);
    Object.entries(NODE_TYPES).forEach(([type, def]) => {
      if (def.inputs > 0) rules[type] = [...sourceTypes];
    });
    return rules;
  })();

  // "" Blueprint Class """"""""""""""""""""""""""""""""""""""""""""""""""""
  class Blueprint {
    constructor(containerId, opts = {}) {
      this._container = document.getElementById(containerId);
      if (!this._container) throw new Error(`Container #${containerId} not found`);

      this._opts = Object.assign(    {
      snapToGrid: 20,
    },
        {
          onNodeSelect: null,
          onNodeDeselect: null,
          onChange: null,
        },
        opts
      );

      // State
      this._nodes = [];
      this._edges = [];
      this._selectedNodeId = null;
      this._nextNodeId = 0;

      // Viewport
      this._zoom = 1;
      this._panX = 0;
      this._panY = 0;

      // Drag state
      this._drag = {
        active: false,
        nodeId: null,
        startX: 0,
        startY: 0,
        nodeStartX: 0,
        nodeStartY: 0,
      };

      // Undo/Redo
      this._undoStack = [];
      this._redoStack = [];
      this._maxUndo = 50;
      this._savingHistory = false;
      this._dragJustStarted = false;    // track first drag move for snapshot

      // Connection drag state
      this._conn = {
        active: false,
        fromNodeId: null,
        fromPortIdx: 0,
        tempPath: null,
        snapTarget: null,
        snapRadius: 40,
      };

      // Build DOM
      this._buildDOM();
      this._attachEvents();

      // Auto-size canvas
      this._resizeCanvas();
    }

    // "" DOM Construction """""""""""""""""""""""""""""""""""""""""""""
    _buildDOM() {
      const c = this._container;
      c.classList.add('bp-container');
      c.innerHTML = `
        <div class="bp-canvas-wrapper" id="${c.id}-wrapper">
          <svg class="bp-svg-overlay" id="${c.id}-svg" width="5000" height="5000"></svg>
          <div class="bp-canvas" id="${c.id}-canvas"></div>
        </div>
      `;
      this._wrapper = c.querySelector('.bp-canvas-wrapper');
      this._svg = c.querySelector('.bp-svg-overlay');
      this._canvas = c.querySelector('.bp-canvas');

      // SVG namespace
      this._svgNS = 'http://www.w3.org/2000/svg';

      // Create defs for arrow markers
      const defs = document.createElementNS(this._svgNS, 'defs');
      const mkAttrs = { viewBox: '0 0 10 7', markerWidth: '10', markerHeight: '7', refX: '9', refY: '3.5', orient: 'auto', markerUnits: 'userSpaceOnUse' };
      const mkPoly = '<polygon points="0 0, 10 3.5, 0 7" />';
      const marker = document.createElementNS(this._svgNS, 'marker');
      marker.setAttribute('id', 'bp-arrowhead');
      Object.entries(mkAttrs).forEach(([k, v]) => marker.setAttribute(k, v));
      marker.innerHTML = mkPoly.replace('/>', 'fill="#94a3b8" />');
      defs.appendChild(marker);

      // Hover state arrowhead (indigo accent)
      const markerHover = document.createElementNS(this._svgNS, 'marker');
      markerHover.setAttribute('id', 'bp-arrowhead-hover');
      Object.entries(mkAttrs).forEach(([k, v]) => markerHover.setAttribute(k, v));
      markerHover.innerHTML = mkPoly.replace('/>', 'fill="#6366f1" />');
      defs.appendChild(markerHover);

      // Error state arrowhead (red accent)
      const markerError = document.createElementNS(this._svgNS, 'marker');
      markerError.setAttribute('id', 'bp-arrowhead-error');
      Object.entries(mkAttrs).forEach(([k, v]) => markerError.setAttribute(k, v));
      markerError.innerHTML = mkPoly.replace('/>', 'fill="#ef4444" />');
      defs.appendChild(markerError);

      // Running state arrowhead (green accent)
      const markerAnim = document.createElementNS(this._svgNS, 'marker');
      markerAnim.setAttribute('id', 'bp-arrowhead-anim');
      Object.entries(mkAttrs).forEach(([k, v]) => markerAnim.setAttribute(k, v));
      markerAnim.innerHTML = mkPoly.replace('/>', 'fill="#10b981" />');
      defs.appendChild(markerAnim);
      this._svg.appendChild(defs);

      // Create grid pattern
      const pattern = document.createElementNS(this._svgNS, 'pattern');
      pattern.setAttribute('id', 'bp-grid');
      pattern.setAttribute('width', '20');
      pattern.setAttribute('height', '20');
      pattern.setAttribute('patternUnits', 'userSpaceOnUse');
      pattern.innerHTML =
        '<path d="M 20 0 L 0 0 0 20" fill="none" stroke="rgba(0,0,0,0.06)" stroke-width="0.5"/>';
      defs.appendChild(pattern);

      // Grid rect fills the SVG
      const gridRect = document.createElementNS(this._svgNS, 'rect');
      gridRect.setAttribute('width', '100%');
      gridRect.setAttribute('height', '100%');
      gridRect.setAttribute('fill', 'url(#bp-grid)');
      this._svg.appendChild(gridRect);

      // Connection container (for temp lines and edges " drawn above grid, below nodes)
      this._edgesGroup = document.createElementNS(this._svgNS, 'g');
      this._edgesGroup.setAttribute('id', `${c.id}-edges`);
      this._svg.appendChild(this._edgesGroup);

      // Tooltip element (positioned via fixed coords from edge hover)
      this._tooltip = document.createElement('div');
      this._tooltip.className = 'bp-edge-tooltip';
      this._tooltip.style.display = 'none';      // Mini-map container
      this._miniMap = document.createElement('div');
      this._miniMap.className = 'bp-minimap';
      this._miniMap.style.cssText = 'position:absolute;bottom:12px;right:12px;width:160px;height:100px;background:rgba(30,41,59,0.85);border:1px solid rgba(255,255,255,0.1);border-radius:8px;z-index:30;overflow:hidden;cursor:pointer;display:none;';
      this._miniMapCanvas = document.createElement('canvas');
      this._miniMapCanvas.style.cssText = 'width:100%;height:100%;';
      this._miniMap.appendChild(this._miniMapCanvas);
      this._container.appendChild(this._miniMap);
      this._miniMapCtx = this._miniMapCanvas.getContext('2d');
      this._container.appendChild(this._tooltip);
    }

    _attachEvents() {
      // Save bound references so we can remove them later
      this._boundMouseMove = (e) => this._onMouseMove(e);
      this._boundMouseUp = (e) => this._onMouseUp(e);
      this._boundWheel = (e) => this._onWheel(e);
      this._boundClick = (e) => {
        if (e.target === this._wrapper || e.target === this._canvas || e.target.classList.contains('bp-canvas-wrapper') || e.target.tagName === 'svg') {
          this._deselectNode();
        }
      };

      // Canvas mouse events for node drag
      document.addEventListener('mousemove', this._boundMouseMove);
      document.addEventListener('mouseup', this._boundMouseUp);

      // Canvas wheel for zoom
      this._wrapper.addEventListener('wheel', this._boundWheel, { passive: false });

      // Canvas click to deselect
      this._wrapper.addEventListener('click', this._boundClick);

      // Canvas mousedown for panning (drag on empty space)
      this._boundMouseDown = (e) => {
        // Only pan if clicking on wrapper/canvas/svg background (not on a node or port)
        const t = e.target;
        if (t === this._wrapper || t === this._canvas || t.tagName === 'svg' || t.classList.contains('bp-svg-overlay')) {
          this._panStart = { x: e.clientX, y: e.clientY, px: this._panX, py: this._panY };
          this._wrapper.style.cursor = 'grabbing';
          e.preventDefault();
        }
      };
      this._wrapper.addEventListener('mousedown', this._boundMouseDown);

      // Global mousemove for panning
      this._boundPanMouseMove = (e) => {
        if (!this._panStart) return;
        this._panX = this._panStart.px + (e.clientX - this._panStart.x);
        this._panY = this._panStart.py + (e.clientY - this._panStart.y);
        this._updateTransform();
        this._renderAllEdges(); this._updateMiniMap();
      };

      // Global mouseup for panning
      this._boundPanMouseUp = (e) => {
        if (this._panStart) {
          this._panStart = null;
          this._wrapper.style.cursor = 'grab';
        }
      };
      document.addEventListener('mousemove', this._boundPanMouseMove);
      document.addEventListener('mouseup', this._boundPanMouseUp);
    }

    // "" Resize """""""""""""""""""""""""""""""""""""""""""""""""""""""
    _resizeCanvas() {
      // Use a RAF to ensure the layout is calculated after DOM insertion
      requestAnimationFrame(() => {
        let rect = this._wrapper.getBoundingClientRect();
        // Fallback: if wrapper hasn't laid out yet, try the container
        if (rect.width === 0 || rect.height === 0) {
          const container = this._container;
          rect = container.getBoundingClientRect();
        }
        const w = Math.max(rect.width * 2, 3000);
        const h = Math.max(rect.height * 2, 3000);
        this._canvas.style.width = w + 'px';
        this._canvas.style.height = h + 'px';
        this._svg.setAttribute('width', rect.width);
        this._svg.setAttribute('height', rect.height);
        this._updateTransform();
      });
    }

    _updateTransform() {
      this._canvas.style.transform = `translate(${this._panX}px, ${this._panY}px) scale(${this._zoom})`;
    }

    // "" Zoom / Pan """"""""""""""""""""""""""""""""""""""""""""""""""
    _onWheel(e) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      const newZoom = Math.min(2, Math.max(0.25, this._zoom * delta));
      // Zoom centered on cursor
      const rect = this._wrapper.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      this._panX = mx - (mx - this._panX) * (newZoom / this._zoom);
      this._panY = my - (my - this._panY) * (newZoom / this._zoom);
      this._zoom = newZoom;
      this._updateTransform();
      this._updateSvgSize();
      this._renderAllEdges(); this._updateMiniMap();
    }

    _updateSvgSize() {
      const rect = this._wrapper.getBoundingClientRect();
      this._svg.setAttribute('width', rect.width);
      this._svg.setAttribute('height', rect.height);
    }

    // "" Node Management """""""""""""""""""""""""""""""""""""""""""""
    addNode(type, x, y, config = {}) {
      const def = NODE_TYPES[type];
      if (!def) throw new Error(`Unknown node type: ${type}`);
      this._saveSnapshot();

      const id = _id();
      const node = {
        id,
        type,
        label: config.label || def.label,
        x,
        y,
        config: Object.assign({}, def.defaultConfig, config),
      };
      this._nodes.push(node);
      this._renderNode(node);
      this._emitChange();
      return id;
    }

    removeNode(nodeId) {
      const idx = this._nodes.findIndex((n) => n.id === nodeId);
      if (idx === -1) return;
      this._saveSnapshot();

      // Remove connected edges
      this._edges = this._edges.filter((e) => e.from !== nodeId && e.to !== nodeId);

      // Remove DOM element
      const el = this._getNodeEl(nodeId);
      if (el) el.remove();

      this._nodes.splice(idx, 1);
      if (this._selectedNodeId === nodeId) this._deselectNode();
      this._renderAllEdges(); this._updateMiniMap();
      this._emitChange();
    }

    updateNodeConfig(nodeId, config) {
      const node = this._getNode(nodeId);
      if (!node) return;
      Object.assign(node.config, config);
      this._emitChange();
    }

    getNode(nodeId) {
      return this._getNode(nodeId);
    }

    getNodes() {
      return this._nodes;
    }

    getEdges() {
      return this._edges;
    }

    // "" Viewport Controls """""""""""""""""""""""""""""""""""""""""""
    zoomBy(factor) {
      const newZoom = Math.min(2, Math.max(0.25, this._zoom * factor));
      this._zoom = newZoom;
      this._updateTransform();
      this._updateSvgSize();
    }

    fitToContent() {
      const cw = this._container.clientWidth;
      const ch = this._container.clientHeight;
      if (!this._nodes.length) {
        this._zoom = 1; this._panX = 0; this._panY = 0;
        this._updateTransform(); this._updateSvgSize();
        return;
      }
      const minX = Math.min(...this._nodes.map((n) => n.x));
      const minY = Math.min(...this._nodes.map((n) => n.y));
      const maxX = Math.max(...this._nodes.map((n) => n.x + NODE_WIDTH));
      const maxY = Math.max(...this._nodes.map((n) => n.y + 190));
      const zoom = Math.min(1, Math.min(cw / (maxX - minX + 100), ch / (maxY - minY + 100)));
      this._zoom = Math.max(0.25, zoom);
      this._panX = (cw - (maxX - minX) * this._zoom) / 2 - minX * this._zoom;
      this._panY = (ch - (maxY - minY) * this._zoom) / 2 - minY * this._zoom;
      this._updateTransform();
      this._updateSvgSize();
    }

    setNodeStatus(nodeId, status, meta) {
      const el = this._canvas ? this._canvas.querySelector(`[data-node-id="${nodeId}"]`) : null;
      if (!el) return;
      el.querySelectorAll('.bp-node-status-badge').forEach((b) => b.remove());
      if (!status) { el.style.boxShadow = ''; return; }
      const badge = document.createElement('div');
      badge.className = 'bp-node-status-badge ' + status;
      const icon = status === 'ok' ? 'fa-check' : status === 'err' ? 'fa-xmark' : 'fa-clock';
      badge.innerHTML = `<i class="fas ${icon}"></i>${meta || ''}`;
      el.appendChild(badge);
      el.style.boxShadow = status === 'ok' ? '0 0 0 2px #10b981' : status === 'err' ? '0 0 0 2px #ef4444' : '0 0 0 2px #f59e0b';
    }

    clearNodeStatus() {
      this._canvas.querySelectorAll('.bp-node-status-badge').forEach((b) => b.remove());
      this._canvas.querySelectorAll('.bp-node').forEach((el) => { el.style.boxShadow = ''; });
    }

    // "" Edge Management """""""""""""""""""""""""""""""""""""""""""""
    addEdge(fromNodeId, toNodeId, label = '') {
      // Validate connection
      if (!this._canConnect(fromNodeId, toNodeId)) return null;
      // Prevent duplicates
      if (this._edges.some((e) => e.from === fromNodeId && e.to === toNodeId)) return null;
      // Prevent circular workflows (B already reaches A)
      if (this._wouldCreateCycle({ from: fromNodeId, to: toNodeId })) return null;
      this._saveSnapshot();

      const edge = { id: _eid(), from: fromNodeId, to: toNodeId, label };
      this._edges.push(edge);
      this._renderAllEdges(); this._updateMiniMap();
      this._emitChange();
      return edge;
    }

    removeEdge(edgeId) {
      const idx = this._edges.findIndex((e) => e.id === edgeId);
      if (idx === -1) return;
      this._saveSnapshot();
      this._edges.splice(idx, 1);
      this._renderAllEdges(); this._updateMiniMap();
      this._emitChange();
    }

    setEdgeLabel(edgeId, label) {
      const edge = this._edges.find((e) => e.id === edgeId);
      if (!edge) return;
      this._saveSnapshot();
      edge.label = label;
      this._renderAllEdges(); this._updateMiniMap();
      this._emitChange();
    }

    // "" Rendering """""""""""""""""""""""""""""""""""""""""""""""""""
    _renderNode(node) {
      const def = NODE_TYPES[node.type];
      const el = document.createElement('div');
      el.className = 'bp-node';
      el.dataset.nodeId = node.id;
      el.style.left = node.x + 'px';
      el.style.top = node.y + 'px';
      el.style.width = NODE_WIDTH + 'px';

      // Header
      const header = document.createElement('div');
      header.className = 'bp-node-header';
      header.style.background = `linear-gradient(135deg, ${def.color} 0%, ${_shadeColor(def.color, -22)} 100%)`;
      header.innerHTML = `
        <i class="fas ${def.icon} text-xs"></i>
        <span class="bp-node-title">${node.label}</span>
        <button class="bp-node-delete" title="Delete node">&times;</button>
      `;
      header.addEventListener('mousedown', (e) => this._onNodeHeaderMouseDown(e, node.id));
      header.querySelector('.bp-node-delete').addEventListener('click', (e) => {
        e.stopPropagation();
        this.removeNode(node.id);
      });

      // Body
      const body = document.createElement('div');
      body.className = 'bp-node-body';
      body.innerHTML = this._getNodeSummary(node);
      body.addEventListener('click', () => this._selectNode(node.id));

      // Ports
      if (def.inputs > 0) {
        const port = document.createElement('div');
        port.className = 'bp-port bp-port-input';
        port.dataset.nodeId = node.id;
        port.dataset.portType = 'input';
        port.dataset.portIdx = '0';
        port.addEventListener('mouseup', (e) => this._onPortMouseUp(e, node.id, 'input', 0));
        body.appendChild(port);
      }
      if (def.outputs > 0) {
        const port = document.createElement('div');
        port.className = 'bp-port bp-port-output';
        port.dataset.nodeId = node.id;
        port.dataset.portType = 'output';
        port.dataset.portIdx = '0';
        port.addEventListener('mousedown', (e) => this._onPortMouseDown(e, node.id, 0));
        body.appendChild(port);
      }

      el.appendChild(header);
      el.appendChild(body);
      this._canvas.appendChild(el);
    }

    _getNodeSummary(node) {
      const def = NODE_TYPES[node.type];
      const c = node.config;
      switch (node.type) {
        case 'data_source':
          return `<span class="text-xs text-gray-500">${c.table_name || 'No table selected'}${c.limit ? ` (limit ${c.limit})` : ''}</span>`;
        case 'cleaning':
          const parts = [];
          if (c.remove_duplicates) parts.push('Dedup');
          parts.push(c.null_strategy === 'drop' ? 'Drop nulls' : `Null: ${c.null_strategy}`);
          return `<span class="text-xs text-gray-500">${parts.join(', ') || 'Default clean'}</span>`;
        case 'sql':
          return `<span class="text-xs text-gray-500 font-mono">${(c.query || '').slice(0, 40)}${(c.query || '').length > 40 ? '...' : ''}</span>`;
        case 'python':
          return `<span class="text-xs text-gray-500">Python code (${(c.code || '').split('\n').length} lines)</span>`;
        case 'analysis':
          return `<span class="text-xs text-gray-500">${c.analysis_type}${c.target_col ? ` on ${c.target_col}` : ''}</span>`;
        case 'chart':
          const recCount = (c.recommended_charts || []).length;
          const exportLabel = c.export_target && c.export_target !== 'none' ? `  ${c.export_target}` : '';
          const recLabel = recCount > 0 ? ` (${recCount} AI recs)` : '';
          return `<span class="text-xs text-gray-500">${c.chart_type}${c.x_col ? `: ${c.x_col}  ${c.y_col}` : ''}${exportLabel}${recLabel}</span>`;
        case 'insights':
          return `<span class="text-xs text-gray-500">${(c.prompt || '').slice(0, 50)}${(c.prompt || '').length > 50 ? '...' : ''}</span>`;
        case 'anomaly':
          return `<span class="text-xs text-gray-500">${c.method}${c.column ? ` on ${c.column}` : ''}${c.threshold ? ` (${c.threshold})` : ''}</span>`;
        case 'forecast':
          return `<span class="text-xs text-gray-500">${c.method}${c.value_col ? ` on ${c.value_col}` : ''}${c.horizon ? `  +${c.horizon}` : ''}</span>`;
        case 'stat_test':
          return `<span class="text-xs text-gray-500">${c.test_type}${c.column_a ? `: ${c.column_a}` : ''}${c.column_b ? ` vs ${c.column_b}` : ''}</span>`;
        case 'regression':
          return `<span class="text-xs text-gray-500">${c.regression_type}${c.target_col ? ` on ${c.target_col}` : ''}</span>`;
        case 'quality':
          return `<span class="text-xs text-gray-500">Quality score${c.table_name ? ` for ${c.table_name}` : ''}</span>`;
        case 'comparison':
          return `<span class="text-xs text-gray-500">${c.kind}${c.kind === 'periods' && c.metric_col ? ` on ${c.metric_col}` : ''}</span>`;
        case 'cohort':
          return `<span class="text-xs text-gray-500">${c.kind}${c.user_id_col ? ` by ${c.user_id_col}` : ''}</span>`;
        case 'ml_model':
          return `<span class="text-xs text-gray-500">${c.task}${c.target_col ? ` on ${c.target_col}` : ''}</span>`;
        case 'dashboard':
          return `<span class="text-xs text-gray-500">${c.dashboard_name || 'New dashboard'}</span>`;
        default:
          return '';
      }
    }

    _rerenderNodeBody(nodeId) {
      const node = this._getNode(nodeId);
      const el = this._getNodeEl(nodeId);
      if (!node || !el) return;
      const body = el.querySelector('.bp-node-body');
      if (!body) return;

      // Update summary text
      body.innerHTML = this._getNodeSummary(node);

      // Recreate ports (they were destroyed when innerHTML was replaced)
      const def = NODE_TYPES[node.type];
      if (def.inputs > 0) {
        const port = document.createElement('div');
        port.className = 'bp-port bp-port-input';
        port.dataset.nodeId = node.id;
        port.dataset.portType = 'input';
        port.dataset.portIdx = '0';
        port.addEventListener('mouseup', (e) => this._onPortMouseUp(e, node.id, 'input', 0));
        body.appendChild(port);
      }
      if (def.outputs > 0) {
        const port = document.createElement('div');
        port.className = 'bp-port bp-port-output';
        port.dataset.nodeId = node.id;
        port.dataset.portType = 'output';
        port.dataset.portIdx = '0';
        port.addEventListener('mousedown', (e) => this._onPortMouseDown(e, node.id, 0));
        body.appendChild(port);
      }
    }

    _renderAllEdges() {
      // Remove old edges, labels, AND stale temp paths
      const oldPaths = this._edgesGroup.querySelectorAll(
        '.bp-edge-path, .bp-edge-label, .bp-edge-label-bg, .bp-temp-edge'
      );
      oldPaths.forEach((p) => p.remove());

      for (const edge of this._edges) {
        this._renderEdge(edge);
      }
    }

    _renderEdge(edge) {
      const fromNode = this._getNode(edge.from);
      const toNode = this._getNode(edge.to);
      if (!fromNode || !toNode) return;

      const fromPos = this._getPortPos(edge.from, 'output');
      const toPos = this._getPortPos(edge.to, 'input');
      if (!fromPos || !toPos) return;

      const path = this._createBezierPath(fromPos, toPos);
      const pathEl = document.createElementNS(this._svgNS, 'path');
      pathEl.setAttribute('d', path);
      pathEl.setAttribute('class', 'bp-edge-path');
      pathEl.setAttribute('data-edge-id', edge.id);
      pathEl.setAttribute('fill', 'none');
      pathEl.setAttribute('stroke', '#64748b');
      pathEl.setAttribute('stroke-width', '2.5');
      pathEl.setAttribute('marker-end', 'url(#bp-arrowhead)');
      this._edgesGroup.appendChild(pathEl);

      // Click to delete edge
      pathEl.style.pointerEvents = 'stroke';
      pathEl.style.cursor = 'pointer';
      pathEl.addEventListener('dblclick', () => {
        this.removeEdge(edge.id);
      });

      // Hover tooltip
      pathEl.addEventListener('mouseenter', () => {
        const fNode = this._getNode(edge.from);
        const tNode = this._getNode(edge.to);
        const fLabel = fNode ? fNode.label : '?';
        const tLabel = tNode ? tNode.label : '?';
        const labelText = edge.label ? `<br><span class="bp-tooltip-label">${this._escapeHtml(edge.label)}</span>` : '';
        this._tooltip.innerHTML = `<strong>${this._escapeHtml(fLabel)}</strong> &rarr; <strong>${this._escapeHtml(tLabel)}</strong>${labelText}`;
        this._tooltip.style.display = 'block';
        pathEl.classList.add('hovered');
        pathEl.setAttribute('marker-end', 'url(#bp-arrowhead-hover)');
      });

      pathEl.addEventListener('mousemove', (e) => {
        this._tooltip.style.left = (e.clientX + 12) + 'px';
        this._tooltip.style.top = (e.clientY - 10) + 'px';
      });

      pathEl.addEventListener('mouseleave', () => {
        this._tooltip.style.display = 'none';
        pathEl.classList.remove('hovered');
        // Restore the marker matching the edge's current run state
        const stateMarker = pathEl.classList.contains('error') ? 'url(#bp-arrowhead-error)'
          : pathEl.classList.contains('animating') ? 'url(#bp-arrowhead-anim)'
          : 'url(#bp-arrowhead)';
        pathEl.setAttribute('marker-end', stateMarker);
      });

      // Edge label
      if (edge.label) {
        const mid = this._bezierMidpoint(fromPos, toPos);

        const bg = document.createElementNS(this._svgNS, 'rect');
        bg.setAttribute('x', mid.x - 30);
        bg.setAttribute('y', mid.y - 10);
        bg.setAttribute('width', '60');
        bg.setAttribute('height', '16');
        bg.setAttribute('rx', '4');
        bg.setAttribute('fill', '#fff');
        bg.setAttribute('stroke', '#e2e8f0');
        bg.setAttribute('stroke-width', '1');
        bg.setAttribute('class', 'bp-edge-label-bg');
        this._edgesGroup.appendChild(bg);

        const text = document.createElementNS(this._svgNS, 'text');
        text.setAttribute('x', mid.x);
        text.setAttribute('y', mid.y + 3);
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('class', 'bp-edge-label');
        text.textContent = edge.label;
        this._edgesGroup.appendChild(text);
      }
    }

    _createBezierPath(from, to) {
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const absDx = Math.abs(dx);
      const absDy = Math.abs(dy);

      if (dx > 20) {
        // Normal left-to-right flow
        const baseHandle = Math.max(absDx * 0.4, 40);
        const adjHandle = baseHandle + absDy * 0.15;
        const cp1x = from.x + adjHandle;
        const cp2x = to.x - adjHandle;
        return `M ${from.x},${from.y} C ${cp1x},${from.y} ${cp2x},${to.y} ${to.x},${to.y}`;
      }

      if (dx < -20) {
        // Right-to-left: push control points LEFT
        const baseHandle = Math.max(absDx * 0.4, 40);
        const adjHandle = baseHandle + absDy * 0.15;
        const cp1x = from.x - adjHandle;
        const cp2x = to.x + adjHandle;
        return `M ${from.x},${from.y} C ${cp1x},${from.y} ${cp2x},${to.y} ${to.x},${to.y}`;
      }

      // Nearly vertical (dx between -20 and +20): use vertical offset
      const vHandle = Math.max(absDy * 0.4, 50);
      const cp1x = from.x;
      const cp1y = from.y + (dy >= 0 ? vHandle : -vHandle);
      const cp2x = to.x;
      const cp2y = to.y + (dy >= 0 ? -vHandle : vHandle);
      return `M ${from.x},${from.y} C ${cp1x},${cp1y} ${cp2x},${cp2y} ${to.x},${to.y}`;
    }

    _bezierMidpoint(from, to) {
      // Compute control points same as _createBezierPath
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const absDx = Math.abs(dx);
      const absDy = Math.abs(dy);
      let cp1x, cp1y, cp2x, cp2y;

      if (dx > 20) {
        const baseHandle = Math.max(absDx * 0.4, 40);
        const adjHandle = baseHandle + absDy * 0.15;
        cp1x = from.x + adjHandle; cp1y = from.y;
        cp2x = to.x - adjHandle; cp2y = to.y;
      } else if (dx < -20) {
        const baseHandle = Math.max(absDx * 0.4, 40);
        const adjHandle = baseHandle + absDy * 0.15;
        cp1x = from.x - adjHandle; cp1y = from.y;
        cp2x = to.x + adjHandle; cp2y = to.y;
      } else {
        const vHandle = Math.max(absDy * 0.4, 50);
        cp1x = from.x; cp1y = from.y + (dy >= 0 ? vHandle : -vHandle);
        cp2x = to.x; cp2y = to.y + (dy >= 0 ? -vHandle : vHandle);
      }

      // Cubic bezier at t=0.5
      const t = 0.5;
      const mt = 1 - t;
      return {
        x: mt*mt*mt*from.x + 3*mt*mt*t*cp1x + 3*mt*t*t*cp2x + t*t*t*to.x,
        y: mt*mt*mt*from.y + 3*mt*mt*t*cp1y + 3*mt*t*t*cp2y + t*t*t*to.y,
      };
    }

    _getPortPos(nodeId, type) {
      const node = this._getNode(nodeId);
      const el = this._getNodeEl(nodeId);
      if (!node || !el) return null;

      // Calculate port y from actual DOM: header height + half of body height
      const header = el.querySelector('.bp-node-header');
      const body = el.querySelector('.bp-node-body');
      const headerH = header ? header.offsetHeight : 36;
      const bodyH = body ? body.offsetHeight : 40;
      const portY = headerH + bodyH / 2;

      const nx = node.x;
      const ny = node.y;

      // Extend into the node so lines overlap the 14px port circles (radius = 7px)
      const PORT_BIAS = 7;

      if (type === 'output') {
        return { x: (nx + NODE_WIDTH - PORT_BIAS) * this._zoom + this._panX, y: (ny + portY) * this._zoom + this._panY };
      } else {
        return { x: (nx + PORT_BIAS) * this._zoom + this._panX, y: (ny + portY) * this._zoom + this._panY };
      }
    }

    // "" Selection """""""""""""""""""""""""""""""""""""""""""""""""""
    _selectNode(nodeId) {
      this._deselectNode();
      this._selectedNodeId = nodeId;
      const el = this._getNodeEl(nodeId);
      if (el) el.classList.add('selected');
      if (this._opts.onNodeSelect) {
        const node = this._getNode(nodeId);
        this._opts.onNodeSelect(node);
      }
    }

    _deselectNode() {
      if (this._selectedNodeId) {
        const el = this._getNodeEl(this._selectedNodeId);
        if (el) el.classList.remove('selected');
      }
      this._selectedNodeId = null;
      if (this._opts.onNodeDeselect) this._opts.onNodeDeselect();
    }

    deselect() {
      this._deselectNode();
    }

    getSelectedNodeId() {
      return this._selectedNodeId;
    }

    // "" Drag (Move Node) """"""""""""""""""""""""""""""""""""""""""""
    _onNodeHeaderMouseDown(e, nodeId) {
      if (e.button !== 0) return;
      const el = this._getNodeEl(nodeId);
      if (!el) return;

      this._saveSnapshot();
      this._drag.active = true;
      this._drag.nodeId = nodeId;
      this._drag.startX = e.clientX;
      this._drag.startY = e.clientY;
            // Snap to grid
      if (this._opts.snapToGrid) {
        const grid = this._opts.snapToGrid;
        this._drag.nodeStartX = Math.round(parseInt(el.style.left) / grid) * grid;
        this._drag.nodeStartY = Math.round(parseInt(el.style.top) / grid) * grid;
      } else {
        this._drag.nodeStartX = parseInt(el.style.left);
        this._drag.nodeStartY = parseInt(el.style.top);
      }
      this._drag.nodeStartY = parseInt(el.style.top);

      e.preventDefault();
    }

    _onMouseMove(e) {
      // Node drag
      if (this._drag.active && this._drag.nodeId) {
        const dx = (e.clientX - this._drag.startX) / this._zoom;
        const dy = (e.clientY - this._drag.startY) / this._zoom;
        const el = this._getNodeEl(this._drag.nodeId);
        if (el) {
          el.style.left = this._drag.nodeStartX + dx + 'px';
          el.style.top = this._drag.nodeStartY + dy + 'px';
          // Update node data
          const node = this._getNode(this._drag.nodeId);
          if (node) {
            node.x = this._drag.nodeStartX + dx;
            node.y = this._drag.nodeStartY + dy;
          }
          this._renderAllEdges(); this._updateMiniMap();
        }
      }

      // Connection drag with snap-to-port
      if (this._conn.active && this._conn.tempPath) {
        const rect = this._wrapper.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        const fromPos = this._getPortPos(this._conn.fromNodeId, 'output');

        if (fromPos) {
          // Find snap target
          const prevSnap = this._conn.snapTarget;
          this._conn.snapTarget = this._findSnapTarget(e.clientX, e.clientY);

          // Update port highlight classes
          if (prevSnap && (!this._conn.snapTarget || prevSnap.nodeId !== this._conn.snapTarget.nodeId)) {
            const prevPort = this._canvas.querySelector(
              `[data-node-id="${prevSnap.nodeId}"][data-port-type="input"]`
            );
            if (prevPort) {
              prevPort.classList.remove('snap-target');
              prevPort.classList.add('valid-target');
            }
          }
          if (this._conn.snapTarget) {
            const snapPort = this._canvas.querySelector(
              `[data-node-id="${this._conn.snapTarget.nodeId}"][data-port-type="input"]`
            );
            if (snapPort) {
              snapPort.classList.remove('valid-target');
              snapPort.classList.add('snap-target');
            }
          }

          // Update port proximity highlights
          this._updatePortProximity(e.clientX, e.clientY);

          // Draw to snap point or mouse position
          const target = this._conn.snapTarget
            ? this._getPortPos(this._conn.snapTarget.nodeId, 'input')
            : { x, y };

          if (target) {
            const d = this._createBezierPath(fromPos, target);
            this._conn.tempPath.setAttribute('d', d);
          }
        }
      }
    }

    _onMouseUp(e) {
      if (this._drag.active) {
        this._drag.active = false;
        this._drag.nodeId = null;
        this._emitChange();
        return;
      }

      // Finish connection drag
      if (this._conn.active) {
        // Check snap target first, then elementFromPoint
        let toNodeId = null;
        if (this._conn.snapTarget) {
          toNodeId = this._conn.snapTarget.nodeId;
        } else {
          const target = document.elementFromPoint(e.clientX, e.clientY);
          if (target && target.classList.contains('bp-port-input')) {
            toNodeId = target.dataset.nodeId;
          }
        }
        if (toNodeId && toNodeId !== this._conn.fromNodeId) {
          this.addEdge(this._conn.fromNodeId, toNodeId);
        }
        // Clean up temp path
        if (this._conn.tempPath) {
          this._conn.tempPath.remove();
        }
        this._conn.active = false;
        this._conn.fromNodeId = null;
        this._conn.tempPath = null;
        this._conn.snapTarget = null;

        // Remove valid-target highlights
        document.querySelectorAll('.bp-port-input').forEach((p) => {
          p.classList.remove('valid-target');
          p.classList.remove('dragging');
          p.classList.remove('snap-target');
          p.classList.remove('near-target');
          p.classList.remove('very-near-target');
        });
      }
    }

    // "" Connection Validation """"""""""""""""""""""""""""""""""""""""
    _canConnect(fromNodeId, toNodeId) {
      if (fromNodeId === toNodeId) return false;
      const fromNode = this._getNode(fromNodeId);
      const toNode = this._getNode(toNodeId);
      if (!fromNode || !toNode) return false;

      const allowed = CONNECTION_RULES[toNode.type];
      if (!allowed) return false;

      // Reject if the reverse edge already exists (prevent cycles)
      if (this._edges.some((e) => e.from === toNodeId && e.to === fromNodeId)) return false;

      return allowed.includes(fromNode.type);
    }

    // Check if adding edge AB would create a cycle.
    // A cycle exists if B can already reach A via existing edges.
    _wouldCreateCycle(newEdge) {
      if (newEdge.from === newEdge.to) return true;
      // Build forward adjacency: from  [to, to, ...]
      const adj = {};
      for (const e of this._edges) {
        if (!adj[e.from]) adj[e.from] = [];
        adj[e.from].push(e.to);
      }
      // BFS from newEdge.to  if we reach newEdge.from, a cycle would form
      const visited = new Set();
      const queue = [newEdge.to];
      while (queue.length > 0) {
        const node = queue.shift();
        if (node === newEdge.from) return true;
        if (visited.has(node)) continue;
        visited.add(node);
        for (const neighbor of (adj[node] || [])) queue.push(neighbor);
      }
      return false;
    }

    _findSnapTarget(clientX, clientY) {
      const rect = this._wrapper.getBoundingClientRect();
      const mouseX = clientX - rect.left;
      const mouseY = clientY - rect.top;

      let closest = null;
      let closestDist = this._conn.snapRadius;

      document.querySelectorAll('.bp-port-input').forEach((p) => {
        if (p.dataset.nodeId === this._conn.fromNodeId) return;
        if (!this._canConnect(this._conn.fromNodeId, p.dataset.nodeId)) return;

        const portRect = p.getBoundingClientRect();
        const portCx = portRect.left + portRect.width / 2 - rect.left;
        const portCy = portRect.top + portRect.height / 2 - rect.top;

        const dist = Math.hypot(mouseX - portCx, mouseY - portCy);
        if (dist < closestDist) {
          closestDist = dist;
          closest = { nodeId: p.dataset.nodeId, portIdx: parseInt(p.dataset.portIdx) };
        }
      });

      return closest;
    }

    _updatePortProximity(clientX, clientY) {
      const rect = this._wrapper.getBoundingClientRect();
      const mouseX = clientX - rect.left;
      const mouseY = clientY - rect.top;

      document.querySelectorAll('.bp-port-input.valid-target, .bp-port-input.near-target, .bp-port-input.very-near-target').forEach((p) => {
        if (p.dataset.nodeId === this._conn.fromNodeId) return;

        const portRect = p.getBoundingClientRect();
        const portCx = portRect.left + portRect.width / 2 - rect.left;
        const portCy = portRect.top + portRect.height / 2 - rect.top;
        const dist = Math.hypot(mouseX - portCx, mouseY - portCy);

        p.classList.remove('near-target', 'very-near-target');

        if (dist < 20) {
          // Already handled by snap-target class in the snap logic
        } else if (dist < 50) {
          p.classList.add('very-near-target');
        } else if (dist < 100) {
          p.classList.add('near-target');
        }
      });
    }

    // "" Port Connection """""""""""""""""""""""""""""""""""""""""""""
    _onPortMouseDown(e, nodeId, portIdx) {
      if (e.button !== 0) return;
      e.stopPropagation();

      this._conn.active = true;
      this._conn.fromNodeId = nodeId;
      this._conn.portIdx = portIdx;

      // Create temp SVG path
      const path = document.createElementNS(this._svgNS, 'path');
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', '#6366f1');
      path.setAttribute('stroke-width', '2');
      path.setAttribute('stroke-dasharray', '5,5');
      path.setAttribute('class', 'bp-temp-edge');
      this._edgesGroup.appendChild(path);
      this._conn.tempPath = path;

      // Highlight valid targets (input ports on other nodes that can connect)
      document.querySelectorAll('.bp-port-input').forEach((p) => {
        const targetId = p.dataset.nodeId;
        if (targetId !== nodeId && this._canConnect(nodeId, targetId)) {
          p.classList.add('valid-target');
        }
      });

      // Style the source port
      e.target.classList.add('dragging');
    }

    _onPortMouseUp(e, nodeId, portType, portIdx) {
      // Handled in _onMouseUp
    }

    // "" Coordinate Helpers """"""""""""""""""""""""""""""""""""""""""
    _toSvgCoords(pos) {
      const rect = this._wrapper.getBoundingClientRect();
      return { x: pos.x - rect.left, y: pos.y - rect.top };
    }

    _screenToCanvas(clientX, clientY) {
      const rect = this._wrapper.getBoundingClientRect();
      const x = (clientX - rect.left - this._panX) / this._zoom;
      const y = (clientY - rect.top - this._panY) / this._zoom;
      return { x, y };
    }

    // "" Serialization """""""""""""""""""""""""""""""""""""""""""""""
    serialize() {
      return {
        nodes: this._nodes.map((n) => ({
          id: n.id,
          type: n.type,
          label: n.label,
          x: Math.round(n.x),
          y: Math.round(n.y),
          config: n.config,
        })),
        edges: this._edges.map((e) => ({
          id: e.id,
          from: e.from,
          to: e.to,
          label: e.label || '',
        })),
        viewport: { x: this._panX, y: this._panY, zoom: this._zoom },
      };
    }

    deserialize(data) {
      this._saveSnapshot();
      // Clear existing
      this._nodes = [];
      this._edges = [];
      this._canvas.innerHTML = '';
      this._edgesGroup.querySelectorAll('.bp-edge-path, .bp-temp-edge').forEach((p) => p.remove());

      if (data.viewport) {
        this._panX = data.viewport.x || 0;
        this._panY = data.viewport.y || 0;
        this._zoom = data.viewport.zoom || 1;
        this._updateTransform();
      }

      // Restore nodes
      if (data.nodes) {
        for (const nd of data.nodes) {
          const def = NODE_TYPES[nd.type];
          if (!def) continue;
          const node = {
            id: nd.id,
            type: nd.type,
            label: nd.label || def.label,
            x: nd.x,
            y: nd.y,
            config: Object.assign({}, def.defaultConfig, nd.config || {}),
          };
          this._nodes.push(node);
          this._renderNode(node);
        }
      }

      // Restore edges (filter out cycles to prevent messy backward arrows)
      if (data.edges) {
        for (const ed of data.edges) {
          const edge = { id: ed.id, from: ed.from, to: ed.to, label: ed.label || '' };
          if (this._wouldCreateCycle(edge)) continue;
          this._edges.push(edge);
        }
        this._renderAllEdges(); this._updateMiniMap();
      }

      this._emitChange();
    }

    clear() {
      this._saveSnapshot();
      this._nodes = [];
      this._edges = [];
      this._canvas.innerHTML = '';
      this._edgesGroup.querySelectorAll('.bp-edge-path, .bp-temp-edge').forEach((p) => p.remove());
      this._selectedNodeId = null;
      this._panX = 0;
      this._panY = 0;
      this._zoom = 1;
      this._updateTransform();
      this._emitChange();
    }

    // "" Getters """""""""""""""""""""""""""""""""""""""""""""""""""""
    _getNode(id) {
      return this._nodes.find((n) => n.id === id);
    }

    _getNodeEl(id) {
      return this._canvas.querySelector(`[data-node-id="${id}"]`);
    }

    _emitChange() {
      if (this._opts.onChange) this._opts.onChange(this.serialize());
    }

    _escapeHtml(str) {
      const div = document.createElement('div');
      div.appendChild(document.createTextNode(str));
      return div.innerHTML;
    }

    // "" Undo / Redo """""""""""""""""""""""""""""""""""""""""""""""""
    _captureState() {
      return JSON.parse(JSON.stringify({
        nodes: this._nodes,
        edges: this._edges,
        viewport: { x: this._panX, y: this._panY, zoom: this._zoom },
      }));
    }

    _restoreState(state) {
      this._nodes = state.nodes;
      this._edges = state.edges;
      this._panX = state.viewport.x;
      this._panY = state.viewport.y;
      this._zoom = state.viewport.zoom;

      // Re-render everything
      this._canvas.innerHTML = '';
      this._edgesGroup.querySelectorAll('.bp-edge-path, .bp-temp-edge').forEach((p) => p.remove());
      this._updateTransform();
      this._svg.appendChild(this._edgesGroup);

      for (const nd of this._nodes) {
        this._renderNode(nd);
      }
      this._renderAllEdges(); this._updateMiniMap();
      // Clear selection
      this._deselectNode();
      this._emitChange();
    }

    _saveSnapshot() {
      if (this._savingHistory) return;
      this._undoStack.push(this._captureState());
      if (this._undoStack.length > this._maxUndo) {
        this._undoStack.shift();
      }
      this._redoStack = [];
    }

    saveSnapshot() {
      this._saveSnapshot();
    }

    undo() {
      if (this._undoStack.length === 0) return false;
      this._redoStack.push(this._captureState());
      const state = this._undoStack.pop();
      this._savingHistory = true;
      this._restoreState(state);
      this._savingHistory = false;
      return true;
    }

    redo() {
      if (this._redoStack.length === 0) return false;
      this._undoStack.push(this._captureState());
      const state = this._redoStack.pop();
      this._savingHistory = true;
      this._restoreState(state);
      this._savingHistory = false;
      return true;
    }

    canUndo() {
      return this._undoStack.length > 0;
    }

    canRedo() {
      return this._redoStack.length > 0;
    }

      _updateMiniMap() {
    if (!this._miniMap || !this._miniMapCtx) return;
    const canvas = this._miniMapCanvas;
    const ctx = this._miniMapCtx;
    if (this._nodes.length === 0) { this._miniMap.style.display = 'none'; return; }
    this._miniMap.style.display = 'block';

    // Set canvas resolution
    const rect = this._miniMap.getBoundingClientRect();
    if (canvas.width !== Math.round(rect.width) || canvas.height !== Math.round(rect.height)) {
      canvas.width = Math.round(rect.width);
      canvas.height = Math.round(rect.height);
    }
    const mw = canvas.width, mh = canvas.height;
    ctx.clearRect(0, 0, mw, mh);

    // Calculate bounds of all nodes
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of this._nodes) {
      if (n.x < minX) minX = n.x; if (n.y < minY) minY = n.y;
      if (n.x + 220 > maxX) maxX = n.x + 220; if (n.y + 80 > maxY) maxY = n.y + 80;
    }
    const pad = 20;
    minX -= pad; minY -= pad; maxX += pad; maxY += pad;
    const scale = Math.min(mw / (maxX - minX), mh / (maxY - minY), 0.5);

    // Draw nodes
    for (const n of this._nodes) {
      const def = NODE_TYPES[n.type];
      const x = (n.x - minX) * scale, y = (n.y - minY) * scale;
      const w = 220 * scale, h = 80 * scale;
      ctx.fillStyle = def && def.color ? def.color : '#6366f1';
      ctx.fillRect(x, y, Math.max(w, 4), Math.max(h, 4));
    }

    // Draw viewport rect
    const vx = (-this._panX / this._zoom - minX) * scale;
    const vy = (-this._panY / this._zoom - minY) * scale;
    const vw = (this._wrapper.clientWidth / this._zoom) * scale;
    const vh = (this._wrapper.clientHeight / this._zoom) * scale;
    ctx.strokeStyle = 'rgba(255,255,255,0.5)';
    ctx.lineWidth = 1;
    ctx.strokeRect(vx, vy, vw, vh);

    // Click to navigate
    this._miniMap.onclick = (e) => {
      const r = this._miniMap.getBoundingClientRect();
      const cx = (e.clientX - r.left) / scale + minX - this._wrapper.clientWidth / 2 / this._zoom;
      const cy = (e.clientY - r.top) / scale + minY - this._wrapper.clientHeight / 2 / this._zoom;
      this._panX = -cx * this._zoom;
      this._panY = -cy * this._zoom;
      this._updateTransform();
      this._renderAllEdges();
    };
  }

  destroy() {
      // Remove event listeners to prevent memory leaks
      if (this._boundMouseMove) document.removeEventListener('mousemove', this._boundMouseMove);
      if (this._boundMouseUp) document.removeEventListener('mouseup', this._boundMouseUp);
      if (this._boundWheel && this._wrapper) this._wrapper.removeEventListener('wheel', this._boundWheel);
      if (this._boundClick && this._wrapper) this._wrapper.removeEventListener('click', this._boundClick);
      this._container.innerHTML = '';
    }
  }

  // "" Node Type Definitions (for palette) """""""""""""""""""""""""""""""
  const getNodeTypes = () => Object.entries(NODE_TYPES).map(([type, def]) => ({
    type,
    ...def,
  }));

  // "" Exports """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""
  window.Blueprint = Blueprint;
  window.BLUEPRINT_NODE_TYPES = NODE_TYPES;
  window.getBlueprintNodeTypes = getNodeTypes;
})();
