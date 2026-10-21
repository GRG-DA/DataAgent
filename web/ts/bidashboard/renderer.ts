import { Widget } from './types';
import { esc, formatNumber } from './utils';

const _renderers: Record<string, any> = {};

export function register(type: string, fn: any): void {
  _renderers[type] = fn;
}

export function render(widget: Widget, container: HTMLElement): void {
  const fn = _renderers[widget.type];
  if (fn) {
    fn(widget, container);
  } else {
    container.innerHTML = '<div class="text-center text-gray-400 text-xs py-4">Unknown widget type: ' + esc(widget.type) + '</div>';
  }
}

const _KPI_GRADIENTS: string[] = [
  "linear-gradient(135deg, #7B61FF 0%, #5B3FD4 100%)",
  "linear-gradient(135deg, #00D4AA 0%, #00A882 100%)",
  "linear-gradient(135deg, #FF6B6B 0%, #D44444 100%)",
  "linear-gradient(135deg, #F59E0B 0%, #D97706 100%)",
  "linear-gradient(135deg, #3B82F6 0%, #1D4ED8 100%)",
  "linear-gradient(135deg, #14B8A6 0%, #0D9488 100%)",
  "linear-gradient(135deg, #EC4899 0%, #BE185D 100%)",
  "linear-gradient(135deg, #8B5CF6 0%, #6D28D9 100%)",
];

function _kpiGradient(idx: number): string {
  return _KPI_GRADIENTS[idx % _KPI_GRADIENTS.length];
}

function _kpiIdx(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 9973;
  return h;
}

function _fmt(v: number, fmt: string): string {
  try {
    if (fmt.indexOf('%') !== -1) return (v * 100).toFixed(fmt.indexOf('0.') !== -1 ? 1 : 0) + '%';
    if (fmt.indexOf('$') !== -1) return '$' + v.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (fmt.indexOf('.') !== -1) {
      const dec = fmt.split('.')[1].length;
      return v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
    }
    return v.toLocaleString();
  } catch (e) {
    return String(v);
  }
}

function _kpiDisplayVal(v: number, c: any): string {
  const customFmt = (typeof c.format === 'string' && c.format === '%' || c.format === '$' || c.format === 'currency');
  const dec = (typeof c.decimals === 'number' && !isNaN(c.decimals))
    ? c.decimals
    : (c.format && c.format.indexOf('.') !== -1 ? c.format.split('.')[1].length : undefined);
  const opts: Intl.NumberFormatOptions = dec !== undefined ? { minimumFractionDigits: dec, maximumFractionDigits: dec } : { maximumFractionDigits: 2 };
  let base: string;
  if (customFmt) {
    base = _fmt(v, c.format);
  } else {
    const abs = Math.abs(v);
    if (c.abbrev !== false) {
      if (abs >= 1e9) base = (v / 1e9).toFixed(dec !== undefined ? dec : 1) + 'B';
      else if (abs >= 1e6) base = (v / 1e6).toFixed(dec !== undefined ? dec : 1) + 'M';
      else if (abs >= 1e3) base = (v / 1e3).toFixed(dec !== undefined ? dec : 1) + 'K';
      else base = v.toLocaleString(undefined, opts);
    } else {
      base = v.toLocaleString(undefined, opts);
    }
  }
  return (c.prefix || '') + base + (c.suffix || '');
}

function _gaugeHtml(c: any): string {
  const v = c.value;
  const val: number = (typeof v === 'number' && !isNaN(v)) ? v : 0;
  const disp: string = typeof c.format === 'string' ? _fmt(val, c.format) : (val >= 1e6 ? (val / 1e6).toFixed(1) + 'M' : val >= 1e3 ? (val / 1e3).toFixed(1) + 'K' : val.toLocaleString());
  const max: number = Math.max(100, Math.abs(val) * 1.25);
  const frac: number = Math.max(0, Math.min(1, val / max));
  const L: number = Math.PI * 50;
  const colour: string = c.color || '#7B61FF';
  return '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding:10px;overflow:hidden;">' +
    '<svg viewBox="0 0 120 70" width="110" height="64">' +
      '<path d="M10 60 A 50 50 0 0 1 110 60" fill="none" stroke="#EEF0F2" stroke-width="12" stroke-linecap="round"/>' +
      '<path d="M10 60 A 50 50 0 0 1 110 60" fill="none" stroke="' + esc(colour) + '" stroke-width="12" stroke-linecap="round" stroke-dasharray="' + (L * frac).toFixed(1) + ' ' + L.toFixed(1) + '"/>' +
    '</svg>' +
    '<div style="font-size:18px;font-weight:700;color:var(--color-content-text);line-height:1.1;">' + esc(String(disp)) + '</div>' +
    '<div style="font-size:11px;color:var(--color-content-text-secondary);margin-top:3px;text-align:center;">' + esc(c.label || 'Gauge') + '</div>' +
  '</div>';
}

function _funnelHtml(c: any): string {
  const labels: any[] = c.labels || [];
  const data: any[] = c.data || [];
  if (!labels.length || !data.length) return '<div class="text-center text-gray-400 text-xs py-4">No data</div>';
  let max = 1;
  for (let i = 0; i < data.length; i++) if (Math.abs(data[i]) > max) max = Math.abs(data[i]);
  const rows = labels.map((lbl: any, i: number) => {
    const v = data[i];
    const w = Math.round((Math.abs(v) / max) * 92) + 2;
    const disp = (typeof v === 'number') ? (Math.round(v * 10) / 10).toLocaleString() : v;
    return '<div style="display:flex;justify-content:center;margin:2px 0;">' +
      '<div style="width:' + w + '%;background:linear-gradient(90deg,#7B61FF,#9B7FFF);border-radius:6px;padding:5px 10px;display:flex;justify-content:space-between;align-items:center;color:#fff;font-size:11px;white-space:nowrap;overflow:hidden;box-shadow:0 2px 6px rgba(123,97,255,0.2);">' +
      '<span style="overflow:hidden;text-overflow:ellipsis;">' + esc(String(lbl)) + '</span>' +
      '<span style="font-weight:700;margin-left:6px;">' + esc(String(disp)) + '</span>' +
      '</div></div>';
  }).join('');
  return '<div style="padding:10px;height:100%;overflow:auto;">' + rows + '</div>';
}

register('kpi', function (widget: Widget, container: HTMLElement) {
  const c: any = widget.config || {};
  const val = c.value !== undefined ? c.value : '--';
  const label: string = c.label || 'KPI';
  const trend = c.trend;
  const trendIcon: string = trend > 0 ? 'fa-arrow-up' : trend < 0 ? 'fa-arrow-down' : 'fa-minus';
  const trendColor: string = trend > 0 ? '#86efac' : trend < 0 ? '#fca5a5' : '#d1d5db';
  const idx: number = (typeof c.kpiIndex === 'number') ? c.kpiIndex : _kpiIdx(widget.id);
  const kpiColor: string = (c.style && c.style.colors && c.style.colors[0]) || c.color;
  const grad: string = c.color ? ('linear-gradient(135deg, ' + c.color + ' 0%, ' + c.color + '88 100%)') : _kpiGradient(idx);

  let displayVal: string;
  if (typeof val === 'number' && !isNaN(val)) {
    displayVal = _kpiDisplayVal(val, c);
  } else {
    displayVal = String(val);
  }

  const D3 = (window as any).D3Charts;

  if (D3 && D3.KPI) {
    D3.KPI.render(container, { value: val, label, trend, format: c.format, prefix: c.prefix, suffix: c.suffix, decimals: c.decimals, abbrev: c.abbrev, showTrend: c.showTrend, color: kpiColor, gradientIdx: idx });
  } else {
    container.innerHTML =
      '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding:10px 18px;text-align:center;background:' + grad + ';border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,0.12);">' +
      '<div style="font-size:32px;font-weight:800;color:#fff;line-height:1.2;text-shadow:0 2px 4px rgba(0,0,0,0.2);letter-spacing:-0.02em;">' + esc(String(displayVal)) + '</div>' +
      '<div style="font-size:11px;color:rgba(255,255,255,0.88);margin-top:5px;font-weight:600;letter-spacing:0.02em;">' + esc(label) + '</div>' +
      (trend !== undefined && c.showTrend !== false ? '<div style="font-size:10px;color:' + trendColor + ';margin-top:7px;background:rgba(0,0,0,0.2);border-radius:12px;padding:3px 10px;display:inline-flex;align-items:center;gap:3px;backdrop-filter:blur(4px);"><i class="fas ' + trendIcon + '"></i>' + (Math.abs(trend)).toFixed(1) + '%</div>' : '') +
      '</div>';
  }

  if (c.sparklineData && D3 && D3.Sparkline) {
    const sid = 'spark_' + widget.id;
    setTimeout(function () {
      let spEl = container.querySelector('#' + sid);
      if (!spEl) {
        const sp = document.createElement('div');
        sp.id = sid;
        sp.style.cssText = 'width:100%;height:32px;margin-top:6px;opacity:0.85;border-radius:4px;';
        container.appendChild(sp);
        D3.Sparkline.render(sp, c.sparklineData, 'rgba(255,255,255,0.92)');
      }
    }, 50);
  }
});

register('chart', function (widget: Widget, container: HTMLElement) {
  const c: any = widget.config || {};
  let chartType: string = c.chartType || 'bar';
  const labels: string[] = c.labels || [];
  const data: number[] = c.data || [];
  const datasets = c.datasets || [{ label: '', data }];
  const accent: string = c.color || '#7B61FF';
  const stacked: boolean = !!c.stacked;

  const D3 = (window as any).D3Charts;

  if (chartType === 'heatmap') {
    if (D3 && D3.Heatmap) {
      container.innerHTML = '';
      D3.Heatmap.render(container, c.matrix || [], c.xLabels || [], c.yLabels || [], { title: c.label });
    } else {
      container.innerHTML = '<div style="padding:8px;height:100%;"><div style="width:100%;height:100%;min-height:150px;"></div></div>';
    }
    return;
  }

  if (chartType === 'scatter') {
    if (D3 && D3.Scatter) {
      container.innerHTML = '';
      D3.Scatter.render(container, c.dataPoints || [], { color: accent });
    } else {
      container.innerHTML = '<div style="padding:8px;height:100%;"><div style="width:100%;height:100%;min-height:150px;"></div></div>';
    }
    return;
  }

  if (chartType === 'bubble') {
    if (D3 && D3.Bubble) {
      container.innerHTML = '';
      D3.Bubble.render(container, c.dataPoints || []);
    } else {
      container.innerHTML = '<div style="padding:8px;height:100%;"><div style="width:100%;height:100%;min-height:150px;"></div></div>';
    }
    return;
  }

  if (chartType === 'gauge') {
    if (D3 && D3.Gauge) {
      container.innerHTML = '';
      D3.Gauge.render(container, c.value || 0, Math.max(100, Math.abs(c.value || 0) * 1.25), { label: c.label || 'Gauge', color: c.color || '#7B61FF' });
    } else {
      container.innerHTML = _gaugeHtml(c);
    }
    return;
  }

  if (chartType === 'funnel') {
    if (D3 && D3.Funnel) {
      container.innerHTML = '';
      D3.Funnel.render(container, c.labels || [], c.data || []);
    } else {
      container.innerHTML = _funnelHtml(c);
    }
    return;
  }

  if (chartType === 'treemap') {
    if (D3 && D3.Treemap) {
      container.innerHTML = '';
      const items = labels.map((l: string, i: number) => ({ label: l, value: data[i] || 0 }));
      D3.Treemap.render(container, items);
    } else {
      container.innerHTML = '<div style="padding:8px;height:100%;"><div style="width:100%;height:100%;min-height:150px;"></div></div>';
    }
    return;
  }

  if (chartType === 'waterfall') {
    if (D3 && D3.Waterfall) {
      container.innerHTML = '';
      const items = labels.map((l: string, i: number) => ({ label: l, value: data[i] || 0 }));
      D3.Waterfall.render(container, items);
    } else {
      container.innerHTML = '<div style="padding:8px;height:100%;"><div style="width:100%;height:100%;min-height:150px;"></div></div>';
    }
    return;
  }

  if (chartType === 'combo') {
    if (D3 && D3.Combo) {
      container.innerHTML = '';
      D3.Combo.render(container, { labels, datasets });
    } else {
      container.innerHTML = '<div style="padding:8px;height:100%;"><div style="width:100%;height:100%;min-height:150px;"></div></div>';
    }
    return;
  }

  if (chartType === 'radar') {
    if (D3 && D3.Radar) {
      container.innerHTML = '';
      D3.Radar.render(container, { labels, datasets });
    } else {
      container.innerHTML = '<div style="padding:8px;height:100%;"><div style="width:100%;height:100%;min-height:150px;"></div></div>';
    }
    return;
  }

  if (chartType === 'donut') chartType = 'doughnut';

  container.innerHTML = '';

  if (D3) {
    if ((chartType === 'bar' || chartType === 'column') && D3.Bar) {
      D3.Bar.render(container, { labels, datasets }, { stacked, dataLabels: true });
    } else if (chartType === 'line' && D3.Line) {
      D3.Line.render(container, { labels, datasets });
    } else if (chartType === 'area' && D3.Area) {
      D3.Area.render(container, { labels, datasets }, { stacked });
    } else if (chartType === 'pie' && D3.Pie) {
      D3.Pie.render(container, labels, data);
    } else if (chartType === 'doughnut' && D3.Doughnut) {
      D3.Doughnut.render(container, labels, data);
    }
  }
});

register('table', function (widget: Widget, container: HTMLElement) {
  const c: any = widget.config || {};
  const columns: string[] = c.columns || [];
  const rawRows: any[] = c.rows || [];

  const D3 = (window as any).D3Charts;

  if (D3 && D3.DataTable) {
    container.innerHTML = '';
    const arrRows = rawRows.map((r: any) => Array.isArray(r) ? r : columns.map((col: string) => r[col]));
    D3.DataTable.render(container, { columns, rows: arrRows });
  } else {
    const tableId = 'tbl_' + widget.id;
    container.innerHTML = '<div id="' + tableId + '" style="padding:4px;height:100%;"></div>';
  }
});

register('text', function (widget: Widget, container: HTMLElement) {
  const c: any = widget.config || {};
  const content: string = c.content || '';

  const D3 = (window as any).D3Charts;
  if (D3 && D3.Text) {
    container.innerHTML = '';
    D3.Text.render(container, content);
  } else {
    container.innerHTML = '<div style="padding:12px;font-size:13px;color:var(--color-content-text);line-height:1.6;">' + content + '</div>';
  }
});

// Chart sub-type aliases (widgets built by templates may carry a raw type
// like 'bar'/'line'/'donut'). Route them through the 'chart' renderer which
// keys off config.chartType, so template-built dashboards render everywhere.
const CHART_ALIAS_TYPES = [
  'line', 'bar', 'column', 'area', 'pie', 'doughnut', 'donut',
  'scatter', 'bubble', 'radar', 'heatmap', 'funnel', 'treemap',
  'waterfall', 'combo', 'horizontalbar', 'stackedbar', 'sparkline', 'gauge',
];
CHART_ALIAS_TYPES.forEach((t) => {
  register(t, function (widget: Widget, container: HTMLElement) {
    const c: any = widget.config || {};
    if (!c.chartType) c.chartType = t;
    const chartFn = _renderers['chart'];
    if (chartFn) chartFn(widget, container);
  });
});

(window as any).DashboardRenderer = { render: render, register: register };
