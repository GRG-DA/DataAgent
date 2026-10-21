// ── Chart Registry ──────────────────────────────────────────────────────
// Central registry mapping widget types to D3.js render functions.
// Each chart module registers itself on window.D3Charts at bundle time.
// This file reads from that global registry and builds the type mapping.

// Type-to-renderer mapping
const RENDERERS: Record<string, string> = {
  kpi: 'KPI',
  chart: 'chart',
  bar: 'Bar',
  column: 'Bar',
  line: 'Line',
  area: 'Area',
  pie: 'Pie',
  doughnut: 'Doughnut',
  scatter: 'Scatter',
  bubble: 'Bubble',
  radar: 'Radar',
  heatmap: 'Heatmap',
  gauge: 'Gauge',
  funnel: 'Funnel',
  treemap: 'Treemap',
  waterfall: 'Waterfall',
  combo: 'Combo',
  table: 'DataTable',
  text: 'Text',
};

function getRenderer(type: string): any {
  const name = RENDERERS[type];
  return name ? (window as any).D3Charts[name] : null;
}
