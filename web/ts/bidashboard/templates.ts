// ── Dashboard Templates ──────────────────────────────────────────────────
// Curated layout descriptors resolved against a table's real columns.
// Exact port of window.BIDashTemplates from dashboardTemplates.js.

import {
  TemplateDescriptor, TemplateContext, TemplateBuild, TemplateMatch,
  ChartSpec, Widget, SlicerDef, WidgetConfig, WidgetType,
} from './types';
import { genWidgetId } from './utils';

// ── Template Definitions ────────────────────────────────────────────────

const TEMPLATES: TemplateDescriptor[] = [
  {
    id: 'executive', name: 'Executive Overview', icon: 'fa-briefcase',
    blurb: 'Revenue & margin KPIs, a monthly trend, and a regional share split.',
    kpis: 4,
    charts: [
      { type: 'line', title: '{date} Trend', by: 'date' },
      { type: 'donut', title: '{dim} Share', by: 'dim' },
      { type: 'bar', title: 'Top {dim2}', by: 'dim2' },
      { type: 'column', title: 'By {dim}', by: 'dim' },
    ],
    table: true, slicers: ['dim', 'dim2', 'date'], autoSlicers: 0,
    insight: 'Track overall performance across {dim} over {date}.',
  },
  {
    id: 'sales', name: 'Sales Performance', icon: 'fa-chart-line',
    blurb: 'Sales KPIs, a regional funnel, monthly trend and a P&L waterfall.',
    kpis: 4,
    charts: [
      { type: 'column', title: 'Revenue by {dim}', by: 'dim' },
      { type: 'line', title: '{date} Trend', by: 'date', series: 2 },
      { type: 'funnel', title: '{dim} Funnel', by: 'dim' },
      { type: 'waterfall', title: '{dim2} Bridge', by: 'dim2' },
    ],
    table: true, slicers: ['dim', 'dim2', 'date'], autoSlicers: 1,
    insight: 'Sales performance across {dim} and {dim2}, trending over {date}.',
  },
  {
    id: 'retail', name: 'Retail Sales', icon: 'fa-store',
    blurb: 'A retail-style pack: KPI band, store/category treemap, and a volume vs revenue combo.',
    kpis: 4,
    charts: [
      { type: 'column', title: 'Revenue by {dim}', by: 'dim' },
      { type: 'treemap', title: '{dim} x {dim2} Mix', by: 'dim' },
      { type: 'combo', title: 'Volume vs Revenue', by: 'dim' },
      { type: 'area', title: '{date} Trend', by: 'date' },
    ],
    table: true, slicers: ['dim', 'dim2'], autoSlicers: 1,
    insight: 'Retail mix across {dim} and {dim2} — spot the biggest contributors.',
  },
  {
    id: 'chocolate', name: 'Chocolate Sales', icon: 'fa-candy-cane',
    blurb: 'Product/team/geo pack inspired by the chocolate sales sample.',
    kpis: 4,
    charts: [
      { type: 'bar', title: 'Top {dim}', by: 'dim' },
      { type: 'line', title: '{date} Trend', by: 'date' },
      { type: 'donut', title: '{dim2} Split', by: 'dim2' },
      { type: 'treemap', title: '{dim} x {dim2}', by: 'dim' },
    ],
    table: true, slicers: ['dim', 'dim2'], autoSlicers: 1,
    insight: 'Chocolate sales by {dim} and {dim2} over time.',
  },
  {
    id: 'hr', name: 'HR Analytics', icon: 'fa-users',
    blurb: 'Headcount, salary and a gauge — a people dashboard.',
    kpis: 4,
    charts: [
      { type: 'gauge', title: 'Avg {m}', by: 'dim' },
      { type: 'bar', title: 'By {dim}', by: 'dim' },
      { type: 'donut', title: '{dim2} Share', by: 'dim2' },
      { type: 'line', title: '{date} Trend', by: 'date' },
    ],
    table: true, slicers: ['dim', 'dim2'], autoSlicers: 0,
    insight: 'People metrics across {dim} and {dim2}.',
  },
  {
    id: 'supplychain', name: 'Supply Chain', icon: 'fa-truck-fast',
    blurb: 'Order volumes, delivery risk and a category bridge.',
    kpis: 4,
    charts: [
      { type: 'line', title: '{date} Trend', by: 'date' },
      { type: 'bar', title: 'By {dim}', by: 'dim' },
      { type: 'waterfall', title: '{dim2} Bridge', by: 'dim2' },
      { type: 'treemap', title: '{dim} x {dim2}', by: 'dim' },
    ],
    table: true, slicers: ['dim', 'dim2'], autoSlicers: 1,
    insight: 'Supply chain volume and risk by {dim}, {dim2} and {date}.',
  },
  {
    id: 'marketing', name: 'Marketing Campaign', icon: 'fa-bullhorn',
    blurb: 'Spend/ROI KPIs, a channel funnel and a clicks-vs-conversion combo.',
    kpis: 4,
    charts: [
      { type: 'combo', title: 'Clicks vs Conversions', by: 'dim' },
      { type: 'funnel', title: '{dim} Funnel', by: 'dim' },
      { type: 'donut', title: '{dim2} Channel', by: 'dim2' },
      { type: 'line', title: '{date} Trend', by: 'date' },
    ],
    table: true, slicers: ['dim', 'dim2'], autoSlicers: 1,
    insight: 'Campaign performance and funnel shape across {dim} and {dim2}.',
  },
  {
    id: 'finance', name: 'Finance P&L', icon: 'fa-scale-balanced',
    blurb: 'Revenue/expense/margin KPIs with a P&L waterfall and a gauge.',
    kpis: 4,
    charts: [
      { type: 'waterfall', title: 'P&L Bridge', by: 'dim2' },
      { type: 'gauge', title: 'Total {m}', by: 'dim' },
      { type: 'bar', title: 'By {dim}', by: 'dim' },
      { type: 'line', title: '{date} Trend', by: 'date' },
    ],
    table: true, slicers: ['dim', 'dim2'], autoSlicers: 0,
    insight: 'Financial performance across {dim} over {date}.',
  },
  {
    id: 'crm', name: 'CRM Pipeline', icon: 'fa-handshake',
    blurb: 'Pipeline value, a stage funnel and a win-rate gauge.',
    kpis: 4,
    charts: [
      { type: 'funnel', title: '{dim} Funnel', by: 'dim' },
      { type: 'gauge', title: 'Total {m}', by: 'dim' },
      { type: 'column', title: 'By {dim2}', by: 'dim2' },
      { type: 'area', title: '{date} Trend', by: 'date' },
    ],
    table: true, slicers: ['dim', 'dim2'], autoSlicers: 1,
    insight: 'Pipeline value by stage ({dim}) and owner ({dim2}).',
  },
  {
    id: 'quick', name: 'Quick Auto-Build', icon: 'fa-wand-magic-sparkles',
    blurb: 'The classic one-click build: KPI band + trend + share + table.',
    kpis: 4,
    charts: [
      { type: 'bar', title: 'Top {dim}', by: 'dim' },
      { type: 'line', title: '{date} Trend', by: 'date' },
      { type: 'donut', title: '{dim} Share', by: 'dim' },
      { type: 'area', title: '{date} Volume', by: 'date' },
    ],
    table: true, slicers: ['dim', 'dim2'], autoSlicers: 1,
    insight: '',
  },
];

// ── Column Classification Patterns ──────────────────────────────────────

const MEASURE_PAT = /revenue|sales|amount|profit|income|value|cost|price|margin|gmv|qty|quantity|units|count|score|rating|salary|hours|expense|balance|rate|ltv|sum|total|roi|ctr|conversion|pipeline|volume|target|budget|gross|net/i;
const PROFIT_PAT = /revenue|sales|profit|margin|income|amount|value/i;
const DIM_PAT = /region|category|segment|type|status|country|product|customer|channel|department|group|state|city|team|division|store|outlet|supplier|platform|source|market|territory|name|brand|item|stage|owner|campaign|payment|sub.?category/i;

// ── Helpers ─────────────────────────────────────────────────────────────

function _uid(): string {
  return genWidgetId();
}

function _widget(type: WidgetType, title: string, cols: number, rows: number, cfg: WidgetConfig): Widget {
  return { id: _uid(), type, title, cols, rows, config: cfg };
}

function _match(arr: string[], re: RegExp, exclude?: string): string {
  for (const c of arr) {
    if (exclude && c === exclude) continue;
    if (re.test(c)) return c;
  }
  return '';
}

function _firstOther(arr: string[], exclude: string): string {
  for (const c of arr) if (c !== exclude) return c;
  return '';
}

function _trendSql(table: string, dateCol: string, m: string, bucket?: string): string {
  let fmt = "'%Y-%m'";
  if (bucket === 'week') fmt = "'%Y-W%W'";
  else if (bucket === 'year') fmt = "'%Y'";
  else if (bucket === 'day') fmt = "'%Y-%m-%d'";
  return `SELECT strftime(CAST("${dateCol}" AS TIMESTAMP), ${fmt}) AS label, SUM("${m}") AS value FROM "${table}" GROUP BY label ORDER BY label LIMIT 12`;
}

// ── KPI Widget Builder ──────────────────────────────────────────────────

function _kpiWidget(H: TemplateMatch, i: number): Widget {
  const v = [
    { agg: 'SUM' as const, prefix: 'Total ' },
    { agg: 'AVG' as const, prefix: 'Avg ' },
    { agg: 'MAX' as const, prefix: 'Max ' },
    { agg: 'COUNT' as const, prefix: 'Records' },
  ][i % 4];

  const title = v.prefix === 'Records' ? 'Records' : v.prefix + H.m;
  const expr = v.agg === 'COUNT' ? 'COUNT(*) AS value' : `${v.agg}("${H.m}") AS value`;
  const cfg: WidgetConfig = {
    table: H.table,
    sql: `SELECT ${expr} FROM "${H.table}"`,
    xCol: '', yCol: 'value', label: title,
  };
  if (H.date && v.agg === 'SUM') {
    cfg.sparkSql = _trendSql(H.table, H.date, H.m, 'month');
  }
  return _widget('kpi', title, 3, 2, cfg);
}

// ── Chart Widget Builder ────────────────────────────────────────────────

function _chartWidget(spec: ChartSpec, H: TemplateMatch): Widget | null {
  const type = spec.type;
  let title = spec.title
    .replace(/\{dim\}/g, H.dim || '?')
    .replace(/\{dim2\}/g, H.dim2 || '?')
    .replace(/\{date\}/g, H.date || '?')
    .replace(/\{m\}/g, H.m || '?');
  const cfg: WidgetConfig = { table: H.table, sql: '', xCol: 'label', yCol: 'value', label: title };
  const cols = spec.cols || 6;
  const rows = spec.rows || 3;

  if (!H.m && type !== 'gauge') return null;

  if (type === 'gauge') {
    cfg.sql = `SELECT SUM("${H.m || H.m2 || 'value'}") AS value FROM "${H.table}"`;
    cfg.xCol = ''; cfg.yCol = 'value';
    return _widget('gauge', title, 3, 3, cfg);
  }

  if (type === 'combo') {
    const c1 = H.m, c2 = H.m2 || H.m;
    cfg.sql = `SELECT "${H.dim}" AS label, SUM("${c1}") AS "${c1}", SUM("${c2}") AS "${c2}" FROM "${H.table}" GROUP BY label ORDER BY label LIMIT 12`;
    cfg.series = [c1, c2];
    cfg.yCol = c1;
    return _widget('combo', title, cols, rows, cfg);
  }

  if (type === 'treemap') {
    const det = H.dim2 || H.dim;
    cfg.sql = `SELECT "${H.dim}" AS label, SUM("${H.m}") AS value, "${det}" AS details FROM "${H.table}" GROUP BY label, details ORDER BY value DESC LIMIT 30`;
    cfg.detailsCol = 'details';
    return _widget('treemap', title, 6, 4, cfg);
  }

  if (type === 'heatmap') {
    const d2 = H.dim2 || H.dim;
    cfg.sql = `SELECT "${H.dim}" AS label, "${d2}" AS category, SUM("${H.m}") AS value FROM "${H.table}" GROUP BY label, category ORDER BY label LIMIT 40`;
    cfg.categoryCol = 'category';
    return _widget('heatmap', title, 6, 4, cfg);
  }

  if (type === 'funnel') {
    cfg.sql = `SELECT "${H.dim}" AS label, SUM("${H.m}") AS value FROM "${H.table}" GROUP BY label ORDER BY value DESC LIMIT 10`;
    return _widget('funnel', title, cols, rows, cfg);
  }

  if (type === 'waterfall') {
    cfg.sql = `SELECT "${H.dim}" AS label, SUM("${H.m}") AS value FROM "${H.table}" GROUP BY label ORDER BY label LIMIT 12`;
    return _widget('waterfall', title, cols, rows, cfg);
  }

  if (spec.by === 'date' && H.date) {
    cfg.sql = _trendSql(H.table, H.date, H.m, spec.bucket);
    return _widget(type as WidgetType, title, cols, rows, cfg);
  }

  if (spec.series && spec.series > 1 && H.m2 && H.m2 !== H.m) {
    cfg.sql = `SELECT "${H.dim}" AS label, SUM("${H.m}") AS "${H.m}", SUM("${H.m2}") AS "${H.m2}" FROM "${H.table}" GROUP BY label ORDER BY label LIMIT 12`;
    cfg.series = [H.m, H.m2];
    cfg.yCol = H.m;
    return _widget(type as WidgetType, title, cols, rows, cfg);
  }

  const order = (type === 'pie' || type === 'donut')
    ? ' ORDER BY value DESC LIMIT 10'
    : (type === 'bar' || type === 'column') ? ' ORDER BY value DESC LIMIT 12' : ' ORDER BY label LIMIT 12';
  cfg.sql = `SELECT "${H.dim}" AS label, SUM("${H.m}") AS value FROM "${H.table}" GROUP BY label` + order;
  return _widget(type as WidgetType, title, cols, rows, cfg);
}

// ── Table Widget Builder ────────────────────────────────────────────────

function _tableWidget(table: string): Widget {
  const cfg: WidgetConfig = { table, sql: `SELECT * FROM "${table}" LIMIT 50`, xCol: '', yCol: '' };
  return _widget('table', 'All Data', 12, 3, cfg);
}

// ── Template Lookup ─────────────────────────────────────────────────────

function _find(id: string): TemplateDescriptor | null {
  return TEMPLATES.find(t => t.id === id) || null;
}

// ── Public API ──────────────────────────────────────────────────────────

export function list(): TemplateDescriptor[] {
  return TEMPLATES.slice();
}

export function quickId(): string {
  return 'quick';
}

/**
 * Expand a template against a table's real columns.
 * @returns {widgets, slicers, insight} or null
 */
export function build(id: string, table: string, ctx: TemplateContext): TemplateBuild | null {
  const t = _find(id);
  if (!t || !table || !ctx) return null;
  const cols = ctx.columns(table) || [];
  if (!cols.length) return null;

  const numeric: string[] = [];
  const dates: string[] = [];
  const cats: string[] = [];

  for (const c of cols) {
    if (ctx.isDate(table, c)) dates.push(c);
    else if (ctx.isNumeric(table, c)) numeric.push(c);
    else cats.push(c);
  }

  const m = _match(numeric, MEASURE_PAT) || numeric[0] || '';
  const m2 = _match(numeric, PROFIT_PAT, m) || _firstOther(numeric, m) || m;
  const dim = _match(cats, DIM_PAT) || cats[0] || '';
  const dim2 = _firstOther(cats, dim) || dim;
  const date = dates[0] || '';

  const H: TemplateMatch = { table, m, m2, dim, dim2, date };

  const widgets: Widget[] = [];
  const slicers: SlicerDef[] = [];

  // KPIs
  for (let k = 0; k < (t.kpis || 4); k++) {
    widgets.push(_kpiWidget(H, k));
  }

  // Charts
  for (const spec of t.charts) {
    const w = _chartWidget(spec, H);
    if (w) widgets.push(w);
  }

  // Table
  if (t.table) widgets.push(_tableWidget(table));

  // Slicers
  for (const s of t.slicers) {
    let field: string;
    let kind: 'select' | 'date' | 'range' = 'select';
    if (s === 'date') { field = H.date; kind = 'date'; }
    else if (s === 'dim') field = H.dim;
    else if (s === 'dim2') field = H.dim2;
    else field = s;
    if (field) slicers.push({ field, table, kind });
  }

  // Auto-slicers (extra categorical dimensions)
  if (t.autoSlicers) {
    for (const f of cats.slice(0, t.autoSlicers)) {
      if (!slicers.some(s => s.field === f)) {
        slicers.push({ field: f, table, kind: 'select' });
      }
    }
  }

  const insight = (t.insight || '')
    .replace(/\{dim\}/g, dim || '?')
    .replace(/\{dim2\}/g, dim2 || '?')
    .replace(/\{date\}/g, date || '?');

  return { widgets, slicers, insight };
}
