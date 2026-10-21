// ── Dashboard State Management ───────────────────────────────────────────
// Centralized, typed state for the Power BI Studio page.
// Replaces the IIFE-scoped _state variable in the old bidashboard.js.

import {
  DashboardState, Widget, WidgetType, WidgetConfig,
  FilterMap, FilterValue, SlicerDef, LayoutConfig,
} from './types';

let _seq = 0;

function genId(): string {
  _seq++;
  return 'w' + Date.now().toString(36) + '_' + _seq;
}

// ── Default state factory ───────────────────────────────────────────────

function createDefaultState(): DashboardState {
  return {
    dashId: null,
    name: 'Untitled Dashboard',
    description: '',
    dataSource: '',
    layout: {
      columns: 12,
      rowHeight: 80,
      slicers: [],
      insight: '',
    },
    widgets: [],
    filters: {},
    mode: 'edit',
    view: 'hub',
    lastSaved: null,
    lastValidation: null,
  };
}

// ── Singleton state ─────────────────────────────────────────────────────

let _state: DashboardState = createDefaultState();

export function getState(): DashboardState {
  return _state;
}

export function resetState(): void {
  _state = createDefaultState();
  _seq = 0;
}

// ── State mutators ──────────────────────────────────────────────────────

export function setDashId(id: string | null): void {
  _state.dashId = id;
}

export function setName(name: string): void {
  _state.name = name;
}

export function setDescription(desc: string): void {
  _state.description = desc;
}

export function setDataSource(table: string): void {
  _state.dataSource = table;
}

export function setMode(mode: 'edit' | 'interact'): void {
  _state.mode = mode;
}

export function setView(view: 'hub' | 'builder'): void {
  _state.view = view;
}

export function setLastSaved(date: Date): void {
  _state.lastSaved = date;
}

// ── Widget operations ───────────────────────────────────────────────────

export function addWidget(type: WidgetType, config: WidgetConfig, extra?: { cols?: number; rows?: number }): string {
  const defaults = defaultWidgetSize(type);
  const widget: Widget = {
    id: genId(),
    type,
    title: config.label || type.charAt(0).toUpperCase() + type.slice(1),
    cols: extra?.cols ?? defaults.cols,
    rows: extra?.rows ?? defaults.rows,
    config,
  };
  _state.widgets.push(widget);
  return widget.id;
}

export function removeWidget(id: string): void {
  _state.widgets = _state.widgets.filter(w => w.id !== id);
}

export function updateWidget(id: string, updates: Partial<Widget>): void {
  const w = _state.widgets.find(w => w.id === id);
  if (w) {
    if (updates.title !== undefined) w.title = updates.title;
    if (updates.cols !== undefined) w.cols = updates.cols;
    if (updates.rows !== undefined) w.rows = updates.rows;
    if (updates.config) w.config = { ...w.config, ...updates.config };
  }
}

export function getWidget(id: string): Widget | undefined {
  return _state.widgets.find(w => w.id === id);
}

export function setWidgets(widgets: Widget[]): void {
  _state.widgets = widgets;
}

// ── Filter operations ───────────────────────────────────────────────────

export function setFilter(field: string, value: FilterValue): void {
  _state.filters[field] = value;
}

export function clearFilter(field: string): void {
  delete _state.filters[field];
}

export function clearAllFilters(): void {
  _state.filters = {};
}

export function toggleFilterValue(field: string, value: string): void {
  const current = _state.filters[field];
  if (Array.isArray(current)) {
    const idx = current.indexOf(value);
    if (idx >= 0) {
      current.splice(idx, 1);
      if (current.length === 0) delete _state.filters[field];
    } else {
      current.push(value);
    }
  } else {
    _state.filters[field] = [value];
  }
}

// ── Slicer operations ───────────────────────────────────────────────────

export function addSlicer(slicer: SlicerDef): void {
  _state.layout.slicers.push(slicer);
}

export function removeSlicer(field: string): void {
  _state.layout.slicers = _state.layout.slicers.filter(s => s.field !== field);
  delete _state.filters[field];
}

export function getSlicers(): SlicerDef[] {
  return _state.layout.slicers;
}

// ── Layout operations ───────────────────────────────────────────────────

export function setInsight(text: string): void {
  _state.layout.insight = text;
}

// ── Hydration (from API response) ───────────────────────────────────────

export function hydrate(data: Partial<DashboardState> & { id?: string; _id?: string }): void {
  _state.dashId = data.dashId || data.id || data._id || null;
  _state.name = data.name || 'Untitled Dashboard';
  _state.description = data.description || '';
  _state.dataSource = data.dataSource || '';
  if (data.layout) {
    _state.layout.columns = data.layout.columns || 12;
    _state.layout.rowHeight = data.layout.rowHeight || 80;
    _state.layout.slicers = data.layout.slicers || [];
    _state.layout.insight = data.layout.insight || '';
  }
  _state.widgets = data.widgets || [];
  _state.filters = {};
  _state.mode = 'edit';
  _state.view = 'builder';
  _state.lastSaved = new Date();
}

// ── Serialization (for API save) ────────────────────────────────────────

export function serialize(): Record<string, any> {
  const { PREVIEW_CONFIG_KEYS } = require('./types');
  const sanitized = _state.widgets.map(w => {
    const cfg = { ...w.config };
    PREVIEW_CONFIG_KEYS.forEach((k: string) => delete (cfg as any)[k]);
    Object.keys(cfg).forEach(k => {
      if (k.startsWith('_')) delete (cfg as any)[k];
    });
    return { id: w.id, type: w.type, title: w.title, cols: w.cols, rows: w.rows, config: cfg };
  });
  return {
    name: _state.name,
    description: _state.description,
    dataSource: _state.dataSource,
    layout: { ..._state.layout },
    widgets: sanitized,
  };
}

// ── Default widget sizes ────────────────────────────────────────────────

function defaultWidgetSize(type: WidgetType): { cols: number; rows: number } {
  switch (type) {
    case 'kpi':       return { cols: 3, rows: 2 };
    case 'table':     return { cols: 12, rows: 3 };
    case 'text':      return { cols: 12, rows: 2 };
    case 'heatmap':   return { cols: 6, rows: 4 };
    case 'treemap':   return { cols: 6, rows: 4 };
    case 'funnel':    return { cols: 6, rows: 3 };
    case 'gauge':     return { cols: 3, rows: 3 };
    default:          return { cols: 6, rows: 3 };
  }
}

// ── Default widget config factory ───────────────────────────────────────

export function defaultWidgetConfig(type: WidgetType, table: string): WidgetConfig {
  const base: WidgetConfig = { table, sql: '', xCol: '', yCol: '', label: '' };

  switch (type) {
    case 'kpi':
      return { ...base, sql: `SELECT COUNT(*) AS value FROM "${table}"`, yCol: 'value' };
    case 'table':
      return { ...base, sql: `SELECT * FROM "${table}" LIMIT 50` };
    case 'text':
      return { ...base, content: 'Enter text here...' };
    case 'gauge':
      return { ...base, sql: `SELECT SUM("value") AS value FROM "${table}"`, yCol: 'value' };
    case 'funnel':
      return { ...base, sql: `SELECT "label", SUM("value") AS value FROM "${table}" GROUP BY label ORDER BY value DESC LIMIT 10`, xCol: 'label', yCol: 'value' };
    case 'heatmap':
      return { ...base, sql: `SELECT "label", "category", SUM("value") AS value FROM "${table}" GROUP BY label, category LIMIT 40`, xCol: 'label', categoryCol: 'category', yCol: 'value' };
    case 'treemap':
      return { ...base, sql: `SELECT "label", SUM("value") AS value FROM "${table}" GROUP BY label ORDER BY value DESC LIMIT 30`, xCol: 'label', yCol: 'value', detailsCol: 'details' };
    default:
      return { ...base, sql: `SELECT "label", SUM("value") AS value FROM "${table}" GROUP BY label ORDER BY value DESC LIMIT 12`, xCol: 'label', yCol: 'value' };
  }
}
