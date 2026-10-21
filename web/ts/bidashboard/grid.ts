import { Widget, WidgetType } from './types';
import { genWidgetId, esc } from './utils';

export interface GridOptions {
  columns?: number;
  rowHeight?: number;
  gap?: number;
  padding?: number;
  fixedWidth?: number;
  editMode?: boolean;
  onChange?: (layout: any[]) => void;
}

export interface GridWidget extends Widget {
  _index?: number;
  col?: number;
  row?: number;
}

export interface GridInstance {
  addWidget: (type: WidgetType, config: any, extra?: any) => string;
  removeWidget: (id: string) => void;
  updateWidget: (id: string, config: Partial<GridWidget>) => void;
  getLayout: () => any[];
  setEditMode: (mode: boolean) => void;
  getWidgets: () => GridWidget[];
  getContainer: () => HTMLElement;
}

const _grids: Record<string, any> = {};

/* ── geometry helpers ────────────────────────────────────────────────────
 * Every widget gets a collision-free position. Widgets WITHOUT col/row are
 * band-packed left-to-right; widgets WITH explicit positions keep their
 * column intent but are pushed down until they no longer overlap anything
 * already placed (heals legacy rows saved by the old row+1 packer).
 * Also clamps every widget inside the 1..numCols column band.
 * ───────────────────────────────────────────────────────────────────── */
function _rectOf(wi: GridWidget): { c1: number; c2: number; r1: number; r2: number } {
  const cols = Math.max(1, wi.cols || 4);
  const rows = Math.max(1, wi.rows || 2);
  const col = Math.max(1, wi.col || 1);
  const row = Math.max(1, wi.row || 1);
  return { c1: col, c2: col + cols - 1, r1: row, r2: row + rows - 1 };
}

function _overlaps(a: { c1: number; c2: number; r1: number; r2: number },
                   b: { c1: number; c2: number; r1: number; r2: number }): boolean {
  return a.c1 <= b.c2 && b.c1 <= a.c2 && a.r1 <= b.r2 && b.r1 <= a.r2;
}

export function resolveLayout(widgets: GridWidget[], numCols: number): void {
  const placed: Array<{ c1: number; c2: number; r1: number; r2: number }> = [];
  let bandRow = 1, bandCol = 1, bandH = 0; // auto-pack cursor for position-less widgets
  for (let i = 0; i < widgets.length; i++) {
    const wi = widgets[i];
    wi.cols = Math.max(1, wi.cols || 4);
    wi.rows = Math.max(1, wi.rows || 2);
    const hasPos = wi.row != null && wi.col != null;
    if (hasPos) {
      // Clamp inside the grid, then push down until the slot is free.
      wi.col = Math.max(1, Math.min(wi.col!, numCols - wi.cols + 1));
      if (wi.cols > numCols) wi.cols = numCols;
      let rect = _rectOf(wi);
      let guard = 0;
      while (guard++ < 200) {
        const hit = placed.some(function (p) { return _overlaps(rect, p); });
        if (!hit) break;
        wi.row = wi.row! + 1;
        rect = _rectOf(wi);
      }
      placed.push(rect);
    } else {
      if (bandCol + wi.cols - 1 > numCols && bandCol > 1) {
        bandRow += bandH;
        bandCol = 1;
        bandH = 0;
      }
      wi.row = bandRow;
      wi.col = bandCol;
      const rect = _rectOf(wi);
      let guard = 0;
      while (guard++ < 200 && placed.some(function (p) { return _overlaps(rect, p); })) {
        wi.row = wi.row! + 1;
        rect.r1++; rect.r2++;
      }
      placed.push(rect);
      bandCol += wi.cols;
      if (wi.rows > bandH) bandH = wi.rows;
    }
  }
}

function _buildGrid(state: any): void {
  const w: GridWidget[] = state.widgets;
  if (w.length === 0) {
    state.container.innerHTML =
      '<div class="empty-state" style="padding:40px"><i class="fas fa-plus-circle text-3xl text-primary-400"></i><p class="text-sm mt-2">Add widgets to build your dashboard</p></div>';
    return;
  }
  const numCols: number = state.columns || 12;
  resolveLayout(w, numCols);
  const fixedWidth: number = state.options.fixedWidth || 0;
  const gap: number = state.options.gap != null ? state.options.gap : 12;
  const pad: number = state.options.padding != null ? state.options.padding : 4;
  const rowH: number = state.options.rowHeight || 80;
  let gridStyle: string;
  if (fixedWidth > 0) {
    const unitW = (fixedWidth - 2 * pad - (numCols - 1) * gap) / numCols;
    gridStyle = 'display:grid;width:' + fixedWidth + 'px;box-sizing:border-box;' +
      'grid-template-columns:repeat(' + numCols + ',' + unitW + 'px);' +
      'grid-auto-rows:' + rowH + 'px;gap:' + gap + 'px;padding:' + pad + 'px;';
  } else {
    gridStyle = 'display:grid;grid-template-columns:repeat(' + numCols + ',1fr);' +
      'gap:' + gap + 'px;padding:' + pad + 'px;';
  }
  let html = '<div class="dashboard-grid" style="' + gridStyle + '">';
  for (let i = 0; i < w.length; i++) {
    const wi = w[i];
    wi._index = i;
    const row = wi.row || i + 1;
    const col = wi.col || 1;
    const rows = wi.rows || 2;
    const cols = wi.cols || 4;
    html += '<div class="dash-widget" data-id="' + wi.id + '" data-type="' + esc(wi.type) + '" draggable="' + state.editMode + '" style="grid-row:' + row + '/span ' + rows + ';grid-column:' + col + '/span ' + cols + ';background:var(--color-card-bg,#fff);border:1px solid var(--color-card-border,#e2e8f0);border-radius:12px;box-shadow:var(--shadow-sm);overflow:hidden;position:relative;min-height:60px;">';
    html += '<div class="dash-widget-header" style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--color-card-border,#e2e8f0);cursor:' + (state.editMode ? 'grab' : 'default') + ';background:var(--color-content-bg,#f8fafc);">';
    html += '<span class="text-xs font-medium truncate">' + esc(wi.title || wi.type) + '</span>';
    if (state.editMode) {
      html += '<div class="flex gap-1">';
      html += '<button class="dash-widget-config" data-id="' + wi.id + '" style="border:none;background:transparent;cursor:pointer;color:#64748b;padding:2px;font-size:11px;" title="Configure"><i class="fas fa-cog"></i></button>';
      html += '<button class="dash-widget-remove" data-id="' + wi.id + '" style="border:none;background:transparent;cursor:pointer;color:#ef4444;padding:2px;font-size:11px;" title="Remove"><i class="fas fa-times"></i></button>';
      html += '</div>';
    }
    html += '</div>';
    html += '<div class="dash-widget-body" data-id="' + wi.id + '" style="padding:0;overflow:auto;height:calc(100% - 36px);">';
    html += '<div class="dash-widget-content" data-id="' + wi.id + '"></div>';
    html += '</div>';
    if (state.editMode) {
      html += '<div class="dash-resize-handle" data-id="' + wi.id + '" style="position:absolute;bottom:0;right:0;width:16px;height:16px;cursor:se-resize;background:linear-gradient(135deg,transparent 50%,#94a3b8 50%);border-radius:0 0 12px 0;"></div>';
    }
    html += '</div>';
  }
  html += '</div>';
  state.container.innerHTML = html;
  if (state.editMode) {
    _bindDragDrop(state);
    _bindResize(state);
  }
  _bindRemove(state);
  _bindConfig(state);
}

function _bindResize(state: any): void {
  const container = state.container;
  const grid = container.querySelector('.dashboard-grid');
  const numCols: number = state.columns || 12;
  const gap: number = state.options.gap != null ? state.options.gap : 12;
  const rowH: number = state.options.rowHeight || 80;
  container.querySelectorAll('.dash-resize-handle').forEach(function (handle: HTMLElement) {
    handle.addEventListener('mousedown', function (e: MouseEvent) {
      e.preventDefault();
      e.stopPropagation();
      const id = (handle as HTMLElement).dataset.id!;
      let w: GridWidget | null = null;
      for (let i = 0; i < state.widgets.length; i++) {
        if (state.widgets[i].id === id) { w = state.widgets[i]; break; }
      }
      if (!w) return;
      const rect = grid.getBoundingClientRect();
      const cellW = rect.width / numCols;
      const cellH = rowH + gap;
      const sx = e.clientX, sy = e.clientY;
      const sc = w.cols, sr = w.rows;
      function onMove(ev: MouseEvent) {
        const dc = Math.round((ev.clientX - sx) / cellW);
        const dr = Math.round((ev.clientY - sy) / cellH);
        w!.cols = Math.max(1, Math.min(numCols - ((w!.col || 1) - 1), sc + dc));
        w!.rows = Math.max(1, Math.min(12, sr + dr));
        (handle as HTMLElement).style.opacity = '0.6';
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        (handle as HTMLElement).style.opacity = '';
        _buildGrid(state);
        if (state.onChange) state.onChange(_getLayout(state));
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });
}
function _bindDragDrop(state: any): void {
  const container = state.container;
  container.querySelectorAll('.dash-widget').forEach(function (el: HTMLElement) {
    el.addEventListener('dragstart', function (this: HTMLElement, e: DragEvent) {
      state.dragSource = this.dataset.id;
      this.style.opacity = '0.5';
      e.dataTransfer!.effectAllowed = 'move';
    });
    el.addEventListener('dragend', function (this: HTMLElement) {
      this.style.opacity = '1';
      container.querySelectorAll('.dash-widget').forEach(function (wi: HTMLElement) { wi.style.outline = 'none'; });
    });
    el.addEventListener('dragover', function (this: HTMLElement, e: DragEvent) {
      e.preventDefault();
      e.dataTransfer!.dropEffect = 'move';
      container.querySelectorAll('.dash-widget').forEach(function (wi: HTMLElement) { wi.style.outline = 'none'; });
      this.style.outline = '2px dashed #6366f1';
    });
    el.addEventListener('dragleave', function (this: HTMLElement) { this.style.outline = 'none'; });
    el.addEventListener('drop', function (this: HTMLElement, e: DragEvent) {
      e.preventDefault();
      this.style.outline = 'none';
      const fromId = state.dragSource;
      const toId = this.dataset.id;
      if (fromId && toId && fromId !== toId) {
        const widgets: GridWidget[] = state.widgets;
        let fromIdx = -1, toIdx = -1;
        for (let i = 0; i < widgets.length; i++) {
          if (widgets[i].id === fromId) fromIdx = i;
          if (widgets[i].id === toId) toIdx = i;
        }
        if (fromIdx >= 0 && toIdx >= 0) {
          const fromW = widgets[fromIdx], toW = widgets[toIdx];
          const fp = { col: fromW.col, row: fromW.row };
          const tp = { col: toW.col, row: toW.row };
          const moved = widgets.splice(fromIdx, 1)[0];
          widgets.splice(toIdx, 0, moved);
          moved.col = tp.col; moved.row = tp.row;
          toW.col = fp.col; toW.row = fp.row;
          _reindex(state);
          _buildGrid(state);
          if (state.onChange) state.onChange(_getLayout(state));
        }
      }
    });
  });
}

function _bindRemove(state: any): void {
  state.container.querySelectorAll('.dash-widget-remove').forEach(function (btn: HTMLElement) {
    btn.addEventListener('click', function (e: Event) {
      e.stopPropagation();
      _removeWidget(state, (btn as HTMLElement).dataset.id!);
    });
  });
}

function _bindConfig(state: any): void {
  state.container.querySelectorAll('.dash-widget-config').forEach(function (btn: HTMLElement) {
    btn.addEventListener('click', function (e: Event) {
      e.stopPropagation();
      const id = (btn as HTMLElement).dataset.id!;
      let widget: GridWidget | null = null;
      for (let i = 0; i < state.widgets.length; i++) {
        if (state.widgets[i].id === id) { widget = state.widgets[i]; break; }
      }
      if (widget && (window as any).WidgetEditor) {
        (window as any).WidgetEditor.open(widget, function (updated: any) {
          _updateWidget(state, id, updated);
        });
      }
    });
  });
}

function _addWidget(state: any, type: WidgetType, config: any, extra?: any): string {
  extra = extra || {};
  const isKpi = (type === 'kpi' || type === 'cardVisual' || type === 'gauge');
  state.widgets.push({
    id: genWidgetId(),
    type: type,
    title: extra.title || config.title || type,
    cols: extra.cols || (isKpi ? 3 : (type === 'table' || type === 'text' ? 12 : 6)),
    rows: extra.rows || (type === 'heatmap' ? 4 : (isKpi ? 2 : 3)),
    config: config || {},
  });
  _buildGrid(state);
  if (state.onChange) state.onChange(_getLayout(state));
  return state.widgets[state.widgets.length - 1].id;
}

function _removeWidget(state: any, id: string): void {
  for (let i = 0; i < state.widgets.length; i++) {
    if (state.widgets[i].id === id) {
      state.widgets.splice(i, 1);
      break;
    }
  }
  _buildGrid(state);
  if (state.onChange) state.onChange(_getLayout(state));
}

function _updateWidget(state: any, id: string, newConfig: any): void {
  for (let i = 0; i < state.widgets.length; i++) {
    if (state.widgets[i].id === id) {
      Object.assign(state.widgets[i], newConfig);
      break;
    }
  }
  _buildGrid(state);
  if (state.onChange) state.onChange(_getLayout(state));
}

function _getLayout(state: any): any[] {
  return state.widgets.map(function (w: GridWidget) {
    return { id: w.id, type: w.type, title: w.title, col: w.col, row: w.row, cols: w.cols, rows: w.rows, config: w.config };
  });
}

function _reindex(state: any): void {
  for (let i = 0; i < state.widgets.length; i++) {
    state.widgets[i]._index = i;
  }
}

export function createGrid(containerId: string, widgets: GridWidget[], options: GridOptions): GridInstance | null {
  const container = typeof containerId === 'string' ? document.getElementById(containerId) : containerId as unknown as HTMLElement;
  if (!container) return null;
  options = options || {};
  const state: any = {
    container: container,
    widgets: widgets || [],
    options: options,
    onChange: options.onChange || null,
    columns: options.columns || 12,
    rowHeight: options.rowHeight || 80,
    editMode: options.editMode !== false,
    dragSource: null,
  };
  _grids[containerId] = state;
  _buildGrid(state);
  return {
    addWidget: function (type: WidgetType, config: any, extra?: any) { return _addWidget(state, type, config, extra); },
    removeWidget: function (id: string) { _removeWidget(state, id); },
    updateWidget: function (id: string, config: any) { _updateWidget(state, id, config); },
    getLayout: function () { return _getLayout(state); },
    setEditMode: function (mode: boolean) { state.editMode = mode; _buildGrid(state); },
    getWidgets: function () { return state.widgets; },
    getContainer: function () { return state.container; },
  };
}

(window as any).DashboardGrid = { render: createGrid };
