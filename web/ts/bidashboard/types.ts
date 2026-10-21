// ── Dashboard Types ──────────────────────────────────────────────────────
// Matches the exact shapes used by the existing vanilla JS bidashboard.
// Every interface mirrors what flows through API.biQuery(), DashboardGrid,
// DashboardRenderer, and the template engine.

export interface DashboardState {
  dashId: string | null;
  name: string;
  description: string;
  dataSource: string;
  layout: LayoutConfig;
  widgets: Widget[];
  filters: FilterMap;
  mode: 'edit' | 'interact';
  view: 'hub' | 'builder';
  lastSaved: Date | null;
  lastValidation: ValidationReport | null;
}

export interface LayoutConfig {
  columns: number;
  rowHeight: number;
  slicers: SlicerDef[];
  insight: string;
}

export interface Widget {
  id: string;
  type: WidgetType;
  title: string;
  cols: number;
  rows: number;
  config: WidgetConfig;
}

export type WidgetType =
  | 'kpi' | 'chart' | 'table' | 'text'
  | 'gauge' | 'funnel' | 'treemap' | 'waterfall'
  | 'heatmap' | 'combo' | 'scatter' | 'bubble'
  | 'radar' | 'pie' | 'doughnut'
  | 'bar' | 'column' | 'line' | 'area';

export interface WidgetConfig {
  table: string;
  sql: string;
  xCol?: string;
  yCol?: string;
  label?: string;
  chartType?: string;
  series?: string[];
  stacked?: boolean;
  sizeCol?: string;
  categoryCol?: string;
  detailsCol?: string;
  content?: string;
  format?: string;
  legend?: boolean;
  dataLabels?: boolean;
  colors?: string[];
  bgColor?: string;
  titleColor?: string;
  titleSize?: number;
  agg?: string;
  sort?: string;
  limit?: number;
  sparkSql?: string;
  value?: number;
}

export interface SlicerDef {
  field: string;
  table: string;
  kind: 'select' | 'date' | 'range';
}

export type FilterValue = string[] | { min: any; max: any };
export type FilterMap = Record<string, FilterValue>;

// ── Query / API Types ───────────────────────────────────────────────────

export interface QueryResult {
  success: boolean;
  columns: string[];
  rows: any[][];
  error?: string;
}

export interface DashboardMeta {
  id: string;
  name: string;
  description: string;
  dataSource: string;
  layout: LayoutConfig;
  widgets: Widget[];
  createdAt?: string;
  updatedAt?: string;
}

export interface ExportJob {
  jobId: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed';
  downloadUrl?: string;
  error?: string;
}

// ── Validation ──────────────────────────────────────────────────────────

export interface ValidationReport {
  valid: boolean;
  issues: ValidationIssue[];
}

export interface ValidationIssue {
  widgetId: string;
  severity: 'error' | 'warning';
  message: string;
}

// ── Chart Rendering ─────────────────────────────────────────────────────

export interface ChartTheme {
  primary: string;
  secondary: string;
  accent: string;
  palette: string[];
  gradient: [string, string];
  background: string;
  cardBg: string;
  text: string;
  textSecondary: string;
  gridLine: string;
  axisColor: string;
}

export interface ChartOptions {
  width: number;
  height: number;
  theme: ChartTheme;
  legend?: boolean;
  dataLabels?: boolean;
  stacked?: boolean;
  colors?: string[];
  bgColor?: string;
  titleColor?: string;
  titleSize?: number;
}

export interface RenderConfig {
  labels?: string[];
  datasets?: Dataset[];
  dataPoints?: DataPoint[];
  matrix?: number[][];
  value?: number;
  total?: number;
  content?: string;
  columns?: string[];
  rows?: any[][];
}

export interface Dataset {
  label: string;
  data: number[];
  color?: string;
  type?: string;
  yAxisID?: string;
  fill?: boolean;
}

export interface DataPoint {
  x: number;
  y: number;
  r?: number;
  label?: string;
  category?: string;
}

// ── Template System ─────────────────────────────────────────────────────

export interface TemplateDescriptor {
  id: string;
  name: string;
  icon: string;
  blurb: string;
  kpis: number;
  charts: ChartSpec[];
  table: boolean;
  slicers: string[];
  autoSlicers: number;
  insight: string;
}

export interface ChartSpec {
  type: string;
  title: string;
  by: 'dim' | 'dim2' | 'date';
  series?: number;
  bucket?: string;
  cols?: number;
  rows?: number;
}

export interface TemplateContext {
  tableNames: () => string[];
  columns: (table: string) => string[];
  isNumeric: (table: string, col: string) => boolean;
  isDate: (table: string, col: string) => boolean;
  isCategorical: (table: string, col: string) => boolean;
}

export interface TemplateBuild {
  widgets: Widget[];
  slicers: SlicerDef[];
  insight: string;
}

export interface TemplateMatch {
  table: string;
  m: string;
  m2: string;
  dim: string;
  dim2: string;
  date: string;
}

// ── Column Heuristics ───────────────────────────────────────────────────

export interface ColumnInfo {
  name: string;
  type: string;
  isNumeric: boolean;
  isDate: boolean;
  isCategorical: boolean;
}

// ── Widget Editor ───────────────────────────────────────────────────────

export interface WidgetEditorState {
  widget: Widget;
  onSave: (updated: Widget) => void;
}

// ── Hub ─────────────────────────────────────────────────────────────────

export interface DashboardCard {
  id: string;
  name: string;
  description: string;
  dataSource: string;
  widgetCount: number;
  updatedAt: string;
}

// ── Slicer ──────────────────────────────────────────────────────────────

export interface SlicerValue {
  value: string;
  selected: boolean;
}

// ── Export ──────────────────────────────────────────────────────────────

export const SAFE_EXPORT_TYPES: WidgetType[] = [
  'kpi', 'chart', 'table', 'text',
  'gauge', 'funnel', 'treemap', 'waterfall',
  'heatmap', 'combo', 'scatter', 'bubble',
  'radar', 'pie', 'doughnut',
  'bar', 'column', 'line', 'area',
];

export const EXPORT_VISUAL_HINT: Record<string, string> = {
  kpi: 'Card',
  chart: 'ClusteredBarChart',
  bar: 'ClusteredBarChart',
  column: 'ClusteredColumnChart',
  line: 'LineChart',
  area: 'AreaChart',
  pie: 'PieChart',
  doughnut: 'DoughnutChart',
  scatter: 'ScatterChart',
  bubble: 'ScatterChart',
  radar: 'RadarChart',
  gauge: 'Gauge',
  funnel: 'Funnel',
  treemap: 'Treemap',
  waterfall: 'WaterfallChart',
  combo: 'LineClusteredColumnComboChart',
  heatmap: 'ShapeMap',
  table: 'TableEx',
  text: 'Card',
};

export const WIDGET_LABEL: Record<string, string> = {
  kpi: 'KPI Card',
  chart: 'Chart',
  bar: 'Bar Chart',
  column: 'Column Chart',
  line: 'Line Chart',
  area: 'Area Chart',
  pie: 'Pie Chart',
  doughnut: 'Doughnut Chart',
  scatter: 'Scatter Plot',
  bubble: 'Bubble Chart',
  radar: 'Radar Chart',
  gauge: 'Gauge',
  funnel: 'Funnel',
  treemap: 'Treemap',
  waterfall: 'Waterfall',
  combo: 'Combo Chart',
  heatmap: 'Heatmap',
  table: 'Data Table',
  text: 'Text',
};

// Keys stripped from config at save time
export const PREVIEW_CONFIG_KEYS = ['agg', 'sort', 'limit', 'sparkSql', 'value'];
