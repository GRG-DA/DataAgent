import { Widget, WidgetConfig } from './types';
import { esc } from './utils';

function _aggOptions(sel: string): string {
  const aggs = ['SUM', 'AVG', 'COUNT', 'MIN', 'MAX'];
  return aggs.map(a =>
    '<option value="' + a + '" ' + (a === sel ? 'selected' : '') + '>' + a + '</option>'
  ).join('');
}

function _sortOptions(sel: string): string {
  const sorts: [string, string][] = [
    ['value_desc', 'Value (high → low)'],
    ['value_asc', 'Value (low → high)'],
    ['label_asc', 'Label (A → Z)'],
    ['label_desc', 'Label (Z → A)'],
  ];
  return sorts.map(s =>
    '<option value="' + s[0] + '" ' + (s[0] === sel ? 'selected' : '') + '>' + s[1] + '</option>'
  ).join('');
}

function _formatOptions(sel: string): string {
  const fmts: [string, string][] = [
    ['0', '0'], ['0.0', '0.0'], ['0.00', '0.00'],
    ['#,##0', '#,##0'], ['#,##0.00', '#,##0.00'],
    ['$#,##0', '$#,##0'], ['$#,##0.00', '$#,##0.00'],
    ['0%', '0%'], ['0.0%', '0.0%'], ['#,##0.0"M"', '#,##0.0 M'],
  ];
  return fmts.map(f =>
    '<option value="' + esc(f[0]) + '" ' + (f[0] === sel ? 'selected' : '') + '>' + esc(f[1]) + '</option>'
  ).join('');
}

function _buildDataTab(config: Record<string, any>, type: string, widget: Widget): string {
  const tables: Record<string, any> = (window as any).App?.state?.tables || {};
  const tableNames = Object.keys(tables);
  const currentTable = config.table || '';
  const sql = config.sql || '';

  let tableOpts = '<option value="">Select table...</option>';
  tableNames.forEach(t => {
    tableOpts += '<option value="' + esc(t) + '" ' + (currentTable === t ? 'selected' : '') + '>' + esc(t) + '</option>';
  });

  let colOpts = '<option value="">Auto-detect</option>';
  const numericCols: string[] = [];
  if (currentTable && tables[currentTable] && tables[currentTable].columns) {
    tables[currentTable].columns.forEach((c: any) => {
      const cn = typeof c === 'string' ? c : (c.column_name || c.name || '');
      if (!cn) return;
      const ct = typeof c === 'object' ? (c.column_type || c.type || '') : '';
      if (/int|float|double|decimal|numeric|bigint|number|real/i.test(ct)) numericCols.push(cn);
      colOpts += '<option value="' + esc(cn) + '" ' + ((config.xCol === cn || config.yCol === cn) ? 'style="font-weight:600"' : '') + '>' + esc(cn) + '</option>';
    });
  }

  const isChart = type !== 'kpi' && type !== 'gauge' && type !== 'text' && type !== 'table';
  let html = '<div class="space-y-3 text-sm">';

  html += '<div>';
  html += '<label class="form-label">Data Source</label>';
  html += '<select class="form-input w-full text-xs" id="wed-table">' + tableOpts + '</select>';
  html += '</div>';

  if (isChart) {
    html += '<div class="grid grid-cols-2 gap-2">';
    html += '<div><label class="form-label">X axis / Label</label>';
    html += '<select class="form-input w-full text-xs" id="wed-xcol">' + colOpts + '</select></div>';
    html += '<div><label class="form-label">Y axis / Value</label>';
    html += '<select class="form-input w-full text-xs" id="wed-ycol">' + colOpts + '</select></div>';
    html += '</div>';
  }

  if (isChart || type === 'kpi') {
    html += '<div class="grid grid-cols-3 gap-2">';
    html += '<div><label class="form-label">Aggregation</label>';
    html += '<select class="form-input w-full text-xs" id="wed-agg">' + _aggOptions(config.agg || 'SUM') + '</select></div>';
    html += '<div><label class="form-label">Sort</label>';
    html += '<select class="form-input w-full text-xs" id="wed-sort">' + _sortOptions(config.sort || 'value_desc') + '</select></div>';
    html += '<div><label class="form-label">Limit</label>';
    html += '<input class="form-input w-full text-xs" id="wed-limit" type="number" min="0" value="' + esc(config.limit != null ? config.limit : 12) + '"></div>';
    html += '</div>';
  }

  if (type === 'bar' || type === 'column' || type === 'line' || type === 'area' || type === 'radar' || type === 'combo') {
    const series: string[] = config.series || [];
    const seriesBoxes = numericCols.map(cn => {
      const on = series.indexOf(cn) !== -1 || (series.length === 0 && cn === config.yCol);
      return '<label class="inline-flex items-center gap-1 mr-2 text-xs"><input type="checkbox" class="wed-series" value="' + esc(cn) + '" ' + (on ? 'checked' : '') + '> ' + esc(cn) + '</label>';
    }).join('');
    html += '<div>';
    html += '<label class="form-label">Series (additional measures)</label>';
    html += '<div>' + (seriesBoxes || '<span class="text-[10px] text-gray-400">no numeric columns in this table</span>') + '</div>';
    html += '</div>';
  }

  if (type === 'bar' || type === 'column' || type === 'area') {
    html += '<label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-stacked" ' + (config.stacked ? 'checked' : '') + '> <span>Stacked</span></label>';
  }

  if (type === 'heatmap') {
    html += '<div><label class="form-label">Heatmap category (rows)</label>';
    html += '<select class="form-input w-full text-xs" id="wed-categorycol">' + colOpts + '</select></div>';
  }
  if (type === 'treemap') {
    html += '<div><label class="form-label">Treemap details (second dimension)</label>';
    html += '<select class="form-input w-full text-xs" id="wed-detailscol">' + colOpts + '</select></div>';
  }
  if (type === 'bubble') {
    html += '<div><label class="form-label">Bubble size (measure)</label>';
    html += '<select class="form-input w-full text-xs" id="wed-sizecol">' + colOpts + '</select></div>';
  }

  if (type === 'kpi') {
    html += '<div>';
    html += '<label class="form-label">KPI Label</label>';
    html += '<input class="form-input w-full" id="wed-label" type="text" value="' + esc(config.label || '') + '">';
    html += '</div>';
    html += '<div>';
    html += '<label class="form-label">Trend / sparkline SQL <span class="text-gray-400 font-normal">(optional)</span></label>';
    html += '<textarea class="form-input w-full font-mono text-xs" id="wed-sparksql" rows="2" placeholder="SELECT strftime(...) AS label, SUM(...) AS value FROM ...">' + esc(config.sparkSql || '') + '</textarea>';
    html += '</div>';
  }

  html += '<div>';
  html += '<div class="flex items-center justify-between mb-1">';
  html += '<label class="form-label mb-0">SQL Query <span class="text-gray-400 font-normal">(DuckDB syntax)</span></label>';
  html += '<button type="button" id="wed-gen-sql-btn" class="text-[11px] px-2 py-0.5 rounded bg-purple-50 text-purple-600 border border-purple-200 hover:bg-purple-100 font-medium transition-colors">';
  html += '<i class="fas fa-wand-magic-sparkles mr-1"></i>Generate SQL</button>';
  html += '</div>';
  html += '<div id="wed-gen-sql-prompt" class="hidden mb-2">';
  html += '<div class="flex gap-1.5">';
  html += '<input id="wed-gen-sql-input" class="form-input flex-1 text-xs" type="text" placeholder="e.g. total revenue by region, top 10 products, monthly trend">';
  html += '<button type="button" id="wed-gen-sql-go" class="text-xs px-2.5 py-1 rounded bg-purple-500 text-white hover:bg-purple-600 font-medium shrink-0">Go</button>';
  html += '<button type="button" id="wed-gen-sql-cancel" class="text-xs px-2 py-1 rounded text-gray-400 hover:text-gray-600">&#10005;</button>';
  html += '</div>';
  html += '<div id="wed-gen-sql-loading" class="hidden text-xs text-gray-400 mt-1"><i class="fas fa-spinner fa-spin mr-1"></i>Generating...</div>';
  html += '</div>';
  html += '<textarea class="form-input w-full font-mono text-xs" id="wed-sql" rows="4" placeholder="SELECT ... FROM table_name">' + esc(sql) + '</textarea>';
  html += '</div>';

  html += '</div>';
  return html;
}

function _buildDisplayTab(config: Record<string, any>, _type: string): string {
  const title = config.title || '';
  const fmt = config.format || '';
  return '<div class="space-y-3 text-sm">' +
    '<label class="form-label">Widget Title</label><input class="form-input" id="wed-title" type="text" value="' + esc(title) + '">' +
    '<div><label class="form-label">Number format <span class="text-gray-400 font-normal">(used on exported KPI cards)</span></label>' +
      '<select class="form-input w-full text-xs" id="wed-format">' + _formatOptions(fmt) + '</select></div>' +
    '<p class="text-[10px] text-gray-400">Colour, legend, data labels, axes and title style live on the <strong>Format</strong> tab.</p>' +
    '</div>';
}

function _buildFormatTab(config: Record<string, any>, type: string): string {
  const style = config.style || {};
  const colors: string[] = style.colors || (config.color ? [config.color] : []);
  const legend = style.legend || {};
  const labels = style.dataLabels || {};
  const axis = style.axis || {};
  const bg = style.background || '';
  const title = style.title || {};
  const palette: string[] = (window as any).Charts?.PALETTE?.categorical || [
    '#118DFF', '#12239E', '#E66C37', '#6B007B', '#E044A7', '#744EC2',
    '#D9B300', '#D64550', '#197278', '#1AAB40', '#15C6F4', '#FFA058',
    '#B5A1FF', '#F472D0', '#00DBBC', '#FF8080',
  ];
  const swatches = palette.map(c => {
    const on = colors.indexOf(c) !== -1;
    return '<button type="button" class="wed-fmt-swatch" data-fmt-color="' + c + '" data-on="' + (on ? '1' : '0') + '" style="width:22px;height:22px;border-radius:5px;background:' + c + ';border:2px solid ' + (on ? '#0f172a' : 'transparent') + ';cursor:pointer;outline:none;" title="' + c + '"></button>';
  }).join('');
  const hexCells = [0, 1, 2].map(i => {
    return '<div><label class="form-label">Colour ' + (i + 1) + '</label>' +
      '<input class="form-input wed-fmt-hex" data-i="' + i + '" type="text" value="' + esc(colors[i] || '') + '" placeholder="#118DFF"></div>';
  }).join('');
  const posOpts = (sel: string, cur: string, list: string[]) => {
    return list.map(p =>
      '<option value="' + p + '"' + (cur === p ? ' selected' : '') + '>' + p + '</option>'
    ).join('');
  };
  return '<div class="space-y-3 text-sm" id="wed-format-pane">' +
    '<div><label class="form-label">Series colours <span class="text-gray-400 font-normal">(click swatches or type hex — exported to Power BI)</span></label>' +
      '<div class="flex flex-wrap gap-1.5">' + swatches + '</div></div>' +
    '<div class="grid grid-cols-3 gap-2">' + hexCells + '</div>' +
    '<div class="grid grid-cols-2 gap-2 items-end">' +
      '<label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-fmt-legend" ' + (legend.show !== false ? 'checked' : '') + '> <span>Show legend</span></label>' +
      '<div><label class="form-label">Legend position</label><select class="form-input w-full" id="wed-fmt-legend-pos">' + posOpts('pos', legend.position, ['top', 'bottom', 'left', 'right']) + '</select></div>' +
    '</div>' +
    '<div class="grid grid-cols-2 gap-2 items-end">' +
      '<label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-fmt-labels" ' + (labels.show ? 'checked' : '') + '> <span>Show data labels</span></label>' +
      '<div><label class="form-label">Label position</label><select class="form-input w-full" id="wed-fmt-labels-pos">' + posOpts('pos', labels.position, ['outsideEnd', 'insideEnd', 'center', 'insideBase']) + '</select></div>' +
    '</div>' +
    '<div class="grid grid-cols-2 gap-2">' +
      '<div><label class="form-label">X-axis title</label><input class="form-input" id="wed-fmt-axis-x" type="text" value="' + esc(axis.categoryTitle || '') + '"></div>' +
      '<div><label class="form-label">Y-axis title</label><input class="form-input" id="wed-fmt-axis-y" type="text" value="' + esc(axis.valueTitle || '') + '"></div>' +
    '</div>' +
    '<label class="checkbox-label flex items-center gap-2"><input type="checkbox" id="wed-fmt-grid" ' + (axis.showGrid !== false ? 'checked' : '') + '> <span>Show value-axis gridlines</span></label>' +
    '<div class="grid grid-cols-3 gap-2">' +
      '<div><label class="form-label">Background</label><input class="form-input" id="wed-fmt-bg" type="text" value="' + esc(bg) + '" placeholder="auto"></div>' +
      '<div><label class="form-label">Title colour</label><input class="form-input" id="wed-fmt-title-color" type="text" value="' + esc(title.color || '') + '" placeholder="auto"></div>' +
      '<div><label class="form-label">Title size</label><input class="form-input" id="wed-fmt-title-size" type="number" min="8" max="40" value="' + esc(title.fontSize || '12') + '"></div>' +
    '</div>' +
    (type === 'kpi'
      ? '<p class="text-[10px] text-gray-400">The first colour is used as the KPI card background in the export.</p>'
      : '') +
    '</div>';
}

function _collectFormat(config: Record<string, any>, type: string): void {
  const style: Record<string, any> = {};
  const colors: string[] = [];
  document.querySelectorAll<HTMLInputElement>('.wed-fmt-hex').forEach(inp => {
    const v = inp.value.trim();
    if (v) colors.push(v);
  });
  document.querySelectorAll<HTMLButtonElement>('.wed-fmt-swatch').forEach(sw => {
    if (sw.dataset.on === '1') {
      const c = sw.dataset.fmtColor;
      if (c && colors.indexOf(c) === -1) colors.push(c);
    }
  });
  if (colors.length) style.colors = colors;

  const lg = document.getElementById('wed-fmt-legend') as HTMLInputElement | null;
  if (lg) {
    style.legend = { show: lg.checked, position: (document.getElementById('wed-fmt-legend-pos') as HTMLSelectElement | null)?.value || 'top' };
  }
  const dl = document.getElementById('wed-fmt-labels') as HTMLInputElement | null;
  if (dl) {
    style.dataLabels = { show: dl.checked, position: (document.getElementById('wed-fmt-labels-pos') as HTMLSelectElement | null)?.value || 'outsideEnd' };
  }
  const ax = document.getElementById('wed-fmt-axis-x') as HTMLInputElement | null;
  const ay = document.getElementById('wed-fmt-axis-y') as HTMLInputElement | null;
  const grid = document.getElementById('wed-fmt-grid') as HTMLInputElement | null;
  if (ax || ay || grid) {
    const a: Record<string, any> = {};
    if (ax && ax.value.trim()) a.categoryTitle = ax.value.trim();
    if (ay && ay.value.trim()) a.valueTitle = ay.value.trim();
    if (grid) a.showGrid = grid.checked;
    style.axis = a;
  }
  const bg = document.getElementById('wed-fmt-bg') as HTMLInputElement | null;
  if (bg && bg.value.trim()) style.background = bg.value.trim();
  const tc = document.getElementById('wed-fmt-title-color') as HTMLInputElement | null;
  const ts = document.getElementById('wed-fmt-title-size') as HTMLInputElement | null;
  if (tc || ts) {
    const t: Record<string, any> = {};
    if (tc && tc.value.trim()) t.color = tc.value.trim();
    if (ts) t.fontSize = parseInt(ts.value, 10) || 12;
    style.title = t;
  }

  if (Object.keys(style).length) config.style = style;
  else delete config.style;

  if (type === 'kpi') {
    if (style.colors && style.colors[0]) config.color = style.colors[0];
    else delete config.color;
  }
}

function _buildLayoutTab(widget: Widget): string {
  return '<div class="space-y-3 text-sm">' +
    '<label class="form-label">Width (columns 1-12)</label><input class="form-input" id="wed-cols" type="number" min="1" max="12" value="' + (widget.cols || 4) + '">' +
    '<label class="form-label">Height (rows 1-6)</label><input class="form-input" id="wed-rows" type="number" min="1" max="6" value="' + (widget.rows || 2) + '">' +
    '</div>';
}

function _refreshColOptions(config: Record<string, any>): void {
  const tables: Record<string, any> = (window as any).App?.state?.tables || {};
  const table = config.table || '';
  let colOpts = '<option value="">Auto-detect</option>';
  if (table && tables[table] && tables[table].columns) {
    tables[table].columns.forEach((c: any) => {
      const cn = typeof c === 'string' ? c : (c.column_name || c.name || '');
      if (cn) colOpts += '<option value="' + esc(cn) + '">' + esc(cn) + '</option>';
    });
  }
  const xEl = document.getElementById('wed-xcol') as HTMLSelectElement | null;
  const yEl = document.getElementById('wed-ycol') as HTMLSelectElement | null;
  if (xEl) { xEl.innerHTML = colOpts; if (config.xCol) xEl.value = config.xCol; }
  if (yEl) { yEl.innerHTML = colOpts; if (config.yCol) yEl.value = config.yCol; }
  const map: Record<string, string> = { 'wed-categorycol': 'categoryCol', 'wed-detailscol': 'detailsCol', 'wed-sizecol': 'sizeCol' };
  Object.keys(map).forEach(id => {
    const el = document.getElementById(id) as HTMLSelectElement | null;
    if (el) { el.innerHTML = colOpts; if (config[map[id]]) el.value = config[map[id]]; }
  });
}

function _collectData(config: Record<string, any>, type: string): void {
  const sql = document.getElementById('wed-sql') as HTMLTextAreaElement | null;
  if (sql) config.sql = sql.value.trim();

  const tbl = document.getElementById('wed-table') as HTMLSelectElement | null;
  if (tbl) config.table = tbl.value;

  const xcol = document.getElementById('wed-xcol') as HTMLSelectElement | null;
  if (xcol) config.xCol = xcol.value;

  const ycol = document.getElementById('wed-ycol') as HTMLSelectElement | null;
  if (ycol) config.yCol = ycol.value;

  const agg = document.getElementById('wed-agg') as HTMLSelectElement | null;
  if (agg) config.agg = agg.value;
  const sort = document.getElementById('wed-sort') as HTMLSelectElement | null;
  if (sort) config.sort = sort.value;
  const limit = document.getElementById('wed-limit') as HTMLInputElement | null;
  if (limit) config.limit = parseInt(limit.value) || 0;

  const seriesChecks = document.querySelectorAll<HTMLInputElement>('.wed-series');
  if (seriesChecks.length) {
    const series: string[] = [];
    seriesChecks.forEach(c => { if (c.checked) series.push(c.value); });
    if (series.length) config.series = series;
    else delete config.series;
  }

  const stacked = document.getElementById('wed-stacked') as HTMLInputElement | null;
  if (stacked) config.stacked = stacked.checked;

  const catcol = document.getElementById('wed-categorycol') as HTMLSelectElement | null;
  if (catcol) config.categoryCol = catcol.value;
  const detcol = document.getElementById('wed-detailscol') as HTMLSelectElement | null;
  if (detcol) config.detailsCol = detcol.value;
  const sizecol = document.getElementById('wed-sizecol') as HTMLSelectElement | null;
  if (sizecol) config.sizeCol = sizecol.value;

  if (type === 'kpi') {
    const l = document.getElementById('wed-label') as HTMLInputElement | null;
    if (l) config.label = l.value;
    const ss = document.getElementById('wed-sparksql') as HTMLTextAreaElement | null;
    if (ss) config.sparkSql = ss.value.trim();
    config.value = null;
  }
}

function _collectDisplay(config: Record<string, any>): void {
  const t = document.getElementById('wed-title') as HTMLInputElement | null;
  if (t) config.title = t.value;
  const fmt = document.getElementById('wed-format') as HTMLSelectElement | null;
  if (fmt) { if (fmt.value) config.format = fmt.value; else delete config.format; }
}

function _collectLayout(widget: Widget): void {
  const c = document.getElementById('wed-cols') as HTMLInputElement | null;
  if (c) widget.cols = parseInt(c.value) || 4;
  const r = document.getElementById('wed-rows') as HTMLInputElement | null;
  if (r) widget.rows = parseInt(r.value) || 2;
}

function _orderBy(s: string): string {
  if (s === 'label_asc') return 'label ASC';
  if (s === 'label_desc') return 'label DESC';
  if (s === 'value_asc') return 'value ASC';
  return 'value DESC';
}

function _domOpts(): Record<string, any> {
  const o: Record<string, any> = { series: [] };
  const a = document.getElementById('wed-agg'); if (a) o.agg = (a as any).value;
  const s = document.getElementById('wed-sort'); if (s) o.sort = (s as any).value;
  const l = document.getElementById('wed-limit'); if (l) o.limit = parseInt((l as any).value) || 0;
  document.querySelectorAll('.wed-series').forEach((c: any) => { if (c.checked) o.series.push(c.value); });
  const st = document.getElementById('wed-stacked'); if (st) o.stacked = (st as any).checked;
  return o;
}

function _generateSQL(prompt: string, table: string, columns: string[], colTypes: Record<string, string>, widgetType: string, opts: Record<string, any>): string {
  opts = opts || {};
  const p = prompt.toLowerCase();
  const q = (n: string) => '"' + String(n).replace(/"/g, '""') + '"';

  const measures: string[] = [];
  const dimensions: string[] = [];
  const dates: string[] = [];
  const numericRe = /int|float|double|decimal|numeric|bigint|number|real|amount|revenue|profit|sales|cost|price|qty|quantity|count|score|rate|ltv|salary|expense|balance|margin|income/i;
  const dateRe = /date|timestamp|time/i;
  const dateNameRe = /^(created|updated|date|timestamp|datetime|order_date|ship_date|sale_date|transaction_date)/i;

  columns.forEach(c => {
    const t = (colTypes[c] || '').toLowerCase();
    if (dateRe.test(t) || dateNameRe.test(c)) dates.push(c);
    else if (numericRe.test(t) || numericRe.test(c)) measures.push(c);
    else dimensions.push(c);
  });

  function bestMeasure(): string {
    const prefer = measures.filter(m => /revenue|profit|sales|amount|income|total|gross|ltv|salary/i.test(m));
    return prefer[0] || measures[0] || '';
  }
  function bestDim(): string {
    const prefer = dimensions.filter(d => /region|category|product|segment|department|channel|status|type|country|city|store|customer|employee/i.test(d));
    return prefer[0] || dimensions[0] || '';
  }
  function bestDate(): string {
    const prefer = dates.filter(d => /date|created|updated|order|transaction/i.test(d));
    return prefer[0] || dates[0] || '';
  }

  let measure = bestMeasure();
  let dim = bestDim();
  let dateCol = bestDate();

  let topN = opts.limit > 0 ? opts.limit : 10;
  const topMatch = p.match(/top\s+(\d+)/);
  if (topMatch) topN = parseInt(topMatch[1]);

  let agg = opts.agg || 'SUM';
  const sortBy = opts.sort || 'value_desc';
  if (/average|avg|mean/.test(p)) agg = 'AVG';
  else if (/count|number|how many|total rows/.test(p)) agg = 'COUNT';
  else if (/min|lowest|smallest|minimum/.test(p)) agg = 'MIN';
  else if (/max|highest|largest|maximum/.test(p)) agg = 'MAX';

  function findCol(words: string[]): string {
    for (let i = 0; i < columns.length; i++) {
      const cl = columns[i].toLowerCase();
      for (let j = 0; j < words.length; j++) {
        if (cl.indexOf(words[j]) !== -1) return columns[i];
      }
    }
    return '';
  }

  const promptMeasure = findCol(measures.map(m => m.toLowerCase()).filter(Boolean));
  const promptDim = findCol(dimensions.map(d => d.toLowerCase()).filter(Boolean));
  const promptDate = findCol(dates.map(d => d.toLowerCase()).filter(Boolean));

  if (promptMeasure) measure = promptMeasure;
  if (promptDim) dim = promptDim;
  if (promptDate) dateCol = promptDate;

  if (/trend|over time|time series|monthly|daily|weekly|yearly|by month|by date|by day|by year|chronolog/.test(p)) {
    let dateExpr = dateCol;
    const t = (colTypes[dateCol] || '').toLowerCase();
    if (/date|timestamp/.test(t)) {
      if (/month|monthly/.test(p)) dateExpr = "date_trunc('month', " + q(dateCol) + ')';
      else if (/week|weekly/.test(p)) dateExpr = "date_trunc('week', " + q(dateCol) + ')';
      else if (/year|yearly/.test(p)) dateExpr = "date_trunc('year', " + q(dateCol) + ')';
      else dateExpr = "date_trunc('month', " + q(dateCol) + ')';
    }
    if (measure) {
      return 'SELECT ' + dateExpr + ' AS label, ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table) + ' GROUP BY label ORDER BY label';
    }
    return 'SELECT ' + dateExpr + ' AS label, COUNT(*) AS value FROM ' + q(table) + ' GROUP BY label ORDER BY label';
  }

  if (/breakdown|by |per |each |group|split|distribut/.test(p) && dim) {
    if (measure) {
      return 'SELECT ' + q(dim) + ' AS label, ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table) + ' GROUP BY ' + q(dim) + ' ORDER BY value DESC LIMIT ' + topN;
    }
    return 'SELECT ' + q(dim) + ' AS label, COUNT(*) AS value FROM ' + q(table) + ' GROUP BY ' + q(dim) + ' ORDER BY value DESC LIMIT ' + topN;
  }

  if (/top|rank|best|worst|highest|lowest|most|least|leading|largest|smallest/.test(p)) {
    const desc = /worst|lowest|least|smallest|bottom/.test(p) ? 'ASC' : 'DESC';
    if (measure) {
      return 'SELECT ' + q(dim || columns[0]) + ' AS label, ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table) + ' GROUP BY ' + q(dim || columns[0]) + ' ORDER BY value ' + desc + ' LIMIT ' + topN;
    }
    return 'SELECT ' + q(dim || columns[0]) + ' AS label, COUNT(*) AS value FROM ' + q(table) + ' GROUP BY ' + q(dim || columns[0]) + ' ORDER BY value ' + desc + ' LIMIT ' + topN;
  }

  if (/sum|total|add up|aggregate/.test(p) && measure) {
    if (dim) {
      return 'SELECT ' + q(dim) + ' AS label, SUM(' + q(measure) + ') AS value FROM ' + q(table) + ' GROUP BY ' + q(dim) + ' ORDER BY value DESC LIMIT ' + topN;
    }
    return 'SELECT SUM(' + q(measure) + ') AS value FROM ' + q(table);
  }

  if (/average|avg|mean/.test(p) && measure) {
    if (dim) {
      return 'SELECT ' + q(dim) + ' AS label, AVG(' + q(measure) + ') AS value FROM ' + q(table) + ' GROUP BY ' + q(dim) + ' ORDER BY value DESC LIMIT ' + topN;
    }
    return 'SELECT AVG(' + q(measure) + ') AS value FROM ' + q(table);
  }

  if (/count|how many|number of|total rows|rows/.test(p)) {
    if (dim) {
      return 'SELECT ' + q(dim) + ' AS label, COUNT(*) AS value FROM ' + q(table) + ' GROUP BY ' + q(dim) + ' ORDER BY value DESC LIMIT ' + topN;
    }
    return 'SELECT COUNT(*) AS value FROM ' + q(table);
  }

  if (/correlat|relationship|scatter|vs|against|compare/.test(p) && measures.length >= 2) {
    const m1 = measure;
    const m2 = measures.filter(m => m !== m1)[0];
    if (dim) {
      return 'SELECT ' + q(dim) + ' AS label, SUM(' + q(m1) + ') AS "' + m1 + '", SUM(' + q(m2) + ') AS "' + m2 + '" FROM ' + q(table) + ' GROUP BY ' + q(dim) + ' ORDER BY ' + q(m1) + ' DESC LIMIT ' + topN;
    }
    return 'SELECT ' + q(m1) + ' AS "' + m1 + '", ' + q(m2) + ' AS "' + m2 + '" FROM ' + q(table) + ' LIMIT ' + topN;
  }

  if (/filter|where|only|exclude|include/.test(p)) {
    const filterWords = p.replace(/filter|where|only|exclude|include|rows?|from|the|that|which|are|is|has/g, '').trim().split(/\s+/).filter(w => w.length > 1);
    if (dim && filterWords.length > 0) {
      const filterVal = filterWords[0];
      if (measure) {
        return 'SELECT ' + q(dim) + ' AS label, SUM(' + q(measure) + ') AS value FROM ' + q(table) + ' WHERE LOWER(' + q(dim) + ") LIKE '%" + filterVal + "%' GROUP BY " + q(dim) + ' ORDER BY value DESC LIMIT ' + topN;
      }
      return 'SELECT * FROM ' + q(table) + " WHERE LOWER(" + q(dim) + ") LIKE '%" + filterVal + "%' LIMIT " + topN;
    }
  }

  if (widgetType === 'kpi' || widgetType === 'gauge') {
    if (measure) return 'SELECT ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table);
    return 'SELECT COUNT(*) AS value FROM ' + q(table);
  }
  if (widgetType === 'table') {
    return 'SELECT * FROM ' + q(table) + ' LIMIT 50';
  }
  if (opts.series && opts.series.length > 1 &&
      (widgetType === 'bar' || widgetType === 'column' || widgetType === 'line' ||
       widgetType === 'area' || widgetType === 'radar' || widgetType === 'combo')) {
    const selCols = opts.series.slice(0, 2).map((m: string) =>
      agg + '(' + q(m) + ') AS "' + m + '"'
    ).join(', ');
    return 'SELECT ' + q(dim || columns[0]) + ' AS label, ' + selCols + ' FROM ' + q(table) +
      ' GROUP BY ' + q(dim || columns[0]) + ' ORDER BY label LIMIT ' + topN;
  }
  if (widgetType === 'funnel' || widgetType === 'pie' || widgetType === 'donut' ||
      widgetType === 'bar' || widgetType === 'column') {
    if (dim && measure) {
      return 'SELECT ' + q(dim) + ' AS label, ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table) +
        ' GROUP BY ' + q(dim) + ' ORDER BY ' + _orderBy(sortBy) + ' LIMIT ' + topN;
    }
    if (measure) return 'SELECT ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table);
  }
  if (widgetType === 'waterfall' || widgetType === 'treemap' || widgetType === 'heatmap' ||
      widgetType === 'radar' || widgetType === 'combo' || widgetType === 'scatter' ||
      widgetType === 'line' || widgetType === 'area') {
    if (dim && measure) {
      return 'SELECT ' + q(dim) + ' AS label, ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table) +
        ' GROUP BY ' + q(dim) + ' ORDER BY label LIMIT ' + topN;
    }
    if (measure) return 'SELECT ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table) + ' LIMIT ' + topN;
  }
  if (dim && measure) {
    return 'SELECT ' + q(dim) + ' AS label, ' + agg + '(' + q(measure) + ') AS value FROM ' + q(table) +
      ' GROUP BY ' + q(dim) + ' ORDER BY ' + _orderBy(sortBy) + ' LIMIT ' + topN;
  }
  if (measure) {
    return 'SELECT ' + q(measure) + ' AS value FROM ' + q(table) + ' LIMIT ' + topN;
  }
  return 'SELECT * FROM ' + q(table) + ' LIMIT ' + topN;
}

export function open(widget: Widget, onSave: (updated: Widget) => void): void {
  if (!(window as any).Modal || !(window as any).Tabs) return;
  const config: Record<string, any> = JSON.parse(JSON.stringify(widget.config || {}));
  const title = widget.title || widget.type;
  const type = widget.type;

  const dataHtml = _buildDataTab(config, type, widget);
  const displayHtml = _buildDisplayTab(config, type);
  const formatHtml = _buildFormatTab(config, type);
  const layoutHtml = _buildLayoutTab(widget);

  const content =
    '<div id="widget-editor-tabs">' +
    '<div id="wed-data">' + dataHtml + '</div>' +
    '<div id="wed-display">' + displayHtml + '</div>' +
    '<div id="wed-format">' + formatHtml + '</div>' +
    '<div id="wed-layout">' + layoutHtml + '</div>' +
    '</div>';

  const modal = (window as any).Modal.show('Edit: ' + esc(title), content, {
    size: 'lg',
    footer: '<button class="btn btn-secondary btn-sm" id="wed-duplicate">Duplicate</button><button class="btn btn-primary btn-sm" id="wed-save">Save</button><button class="btn btn-secondary btn-sm modal-close">Cancel</button>',
  });

  const tabsContainer = document.getElementById('widget-editor-tabs');
  if (tabsContainer && (window as any).Tabs) {
    (window as any).Tabs.render(tabsContainer, [
      { id: 'wed-data', label: 'Data', icon: 'fa-database', content: dataHtml },
      { id: 'wed-display', label: 'Display', icon: 'fa-palette', content: displayHtml },
      { id: 'wed-format', label: 'Format', icon: 'fa-sliders', content: formatHtml },
      { id: 'wed-layout', label: 'Layout', icon: 'fa-arrows-alt', content: layoutHtml },
    ]);
  }

  const formatPane = document.getElementById('wed-format');
  if (formatPane) {
    formatPane.addEventListener('click', (e: Event) => {
      const target = (e.target as HTMLElement).closest('.wed-fmt-swatch') as HTMLElement | null;
      if (!target) return;
      const on = target.dataset.on === '1' ? '0' : '1';
      target.dataset.on = on;
      target.style.borderColor = on === '1' ? '#0f172a' : 'transparent';
    });
  }

  const tableSelect = document.getElementById('wed-table') as HTMLSelectElement | null;
  if (tableSelect) {
    tableSelect.addEventListener('change', function () {
      config.table = this.value;
      _refreshColOptions(config);
    });
  }

  const dupBtn = document.getElementById('wed-duplicate');
  if (dupBtn) {
    dupBtn.addEventListener('click', () => {
      _collectData(config, type);
      _collectDisplay(config);
      _collectLayout(widget);
      widget.config = config as WidgetConfig;
      if ((window as any).BIDash && (window as any).BIDash.duplicateWidget) (window as any).BIDash.duplicateWidget(widget);
      modal.close();
    });
  }

  const saveBtn = document.getElementById('wed-save');
  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      _collectData(config, type);
      _collectDisplay(config);
      _collectFormat(config, type);
      _collectLayout(widget);
      if (!config.sql && config.table && (window as any).App && (window as any).App.state && (window as any).App.state.tables) {
        const _ti = (window as any).App.state.tables[config.table] || {};
        const _cols = (_ti.columns || []).map((c: any) => {
          return typeof c === 'string' ? c : (c.column_name || c.name || '');
        }).filter(Boolean);
        const _ct: Record<string, string> = {};
        (_ti.columns || []).forEach((c: any) => {
          if (typeof c === 'object') { const n = c.column_name || c.name || ''; _ct[n] = c.column_type || c.type || ''; }
        });
        config.sql = _generateSQL('', config.table, _cols, _ct, type, {
          agg: config.agg, sort: config.sort, limit: config.limit,
          series: config.series, stacked: config.stacked,
        });
      }
      widget.config = config as WidgetConfig;
      if (onSave) onSave(widget);
      modal.close();
    });
  }

  const genBtn = document.getElementById('wed-gen-sql-btn');
  const genPrompt = document.getElementById('wed-gen-sql-prompt');
  const genInput = document.getElementById('wed-gen-sql-input') as HTMLInputElement | null;
  const genGo = document.getElementById('wed-gen-sql-go');
  const genCancel = document.getElementById('wed-gen-sql-cancel');
  const genLoading = document.getElementById('wed-gen-sql-loading');

  if (genBtn && genPrompt) {
    genBtn.addEventListener('click', () => {
      genPrompt.classList.toggle('hidden');
      if (!genPrompt.classList.contains('hidden') && genInput) genInput.focus();
    });
  }
  if (genCancel && genPrompt) {
    genCancel.addEventListener('click', () => {
      genPrompt.classList.add('hidden');
    });
  }
  if (genGo && genInput) {
    function runGen(): void {
      const prompt = genInput!.value.trim();
      if (!prompt) return;
      const tbl = config.table || '';
      if (!tbl) {
        if ((window as any).App) (window as any).App.warning('Select a table first.');
        return;
      }
      if (genLoading) genLoading.classList.remove('hidden');
      (genGo as HTMLButtonElement).disabled = true;

      const tables: Record<string, any> = ((window as any).App && (window as any).App.state && (window as any).App.state.tables) || {};
      const tableInfo = tables[tbl] || {};
      const columns = (tableInfo.columns || []).map((c: any) => {
        return typeof c === 'string' ? c : (c.column_name || c.name || '');
      }).filter(Boolean);
      const colTypes: Record<string, string> = {};
      (tableInfo.columns || []).forEach((c: any) => {
        if (typeof c === 'object') {
          const cn = c.column_name || c.name || '';
          colTypes[cn] = c.column_type || c.type || '';
        }
      });

      let schemaCtx = 'TABLE ' + tbl + '\nColumns:\n';
      columns.forEach((c: string) => {
        schemaCtx += '  - ' + c + ' (' + (colTypes[c] || 'unknown') + ')\n';
      });

      const base = (window as any).__API_BASE__ || '';
      const llmPayload = {
        prompt: 'Write a DuckDB SQL query for this task: ' + prompt +
          '\n\nWidget type: ' + type +
          '\nTable: ' + tbl +
          '\nColumns: ' + columns.join(', ') +
          '\n\nRules:\n' +
          '- Return ONLY the SQL query, no explanation\n' +
          '- Use DuckDB syntax\n' +
          '- Use double quotes for identifiers\n' +
          '- For aggregation, use appropriate GROUP BY\n' +
          '- For trends, use date_trunc on date columns\n' +
          '- LIMIT results to 20 rows for GROUP BY queries\n' +
          '- The result columns should be named \'label\' and \'value\' for charts, or appropriate names for KPIs/tables',
        language: 'sql',
        context: schemaCtx,
      };

      fetch(base + '/llm/generate-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(llmPayload),
      }).then(r => r.json()).then(result => {
        let sql = '';
        if (result && result.success && result.code) {
          sql = result.code.trim();
        } else {
          sql = _generateSQL(prompt, tbl, columns, colTypes, type, _domOpts());
        }
        const sqlEl = document.getElementById('wed-sql') as HTMLTextAreaElement | null;
        if (sqlEl) sqlEl.value = sql;
        config.sql = sql;
        genPrompt!.classList.add('hidden');
        if ((window as any).App) (window as any).App.success('SQL generated' + (result && result.success ? ' (via LLM)' : ' (local)'));
      }).catch(() => {
        const sql = _generateSQL(prompt, tbl, columns, colTypes, type, _domOpts());
        const sqlEl = document.getElementById('wed-sql') as HTMLTextAreaElement | null;
        if (sqlEl) sqlEl.value = sql;
        config.sql = sql;
        genPrompt!.classList.add('hidden');
        if ((window as any).App) (window as any).App.success('SQL generated (local fallback)');
      }).finally(() => {
        if (genLoading) genLoading.classList.add('hidden');
        (genGo as HTMLButtonElement).disabled = false;
        genInput!.value = '';
      });
    }
    genGo.addEventListener('click', runGen);
    genInput.addEventListener('keydown', (e: KeyboardEvent) => {
      if (e.key === 'Enter') { e.preventDefault(); runGen(); }
    });
  }
}

(window as any).WidgetEditor = { open: open };
