// ── KPI Card Renderer ───────────────────────────────────────────────────
import { kpiGradient } from './theme';
import { formatNumber } from '../bidashboard/utils';

export interface KpiConfig {
  value: number;
  label: string;
  trend?: number;
  format?: string;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  abbrev?: boolean;
  showTrend?: boolean;
  sparkData?: number[];
  gradientIdx?: number;
  color?: string;
}

function _kpiDisplay(v: number, cfg: KpiConfig): string {
  const customFmt = (typeof cfg.format === 'string' && cfg.format === '%' || cfg.format === '$' || cfg.format === 'currency');
  const dec = (typeof cfg.decimals === 'number' && !isNaN(cfg.decimals))
    ? cfg.decimals
    : (cfg.format && cfg.format.indexOf('.') !== -1 ? cfg.format.split('.')[1].length : undefined);
  const opts = dec !== undefined ? { minimumFractionDigits: dec, maximumFractionDigits: dec } : { maximumFractionDigits: 2 } as Intl.NumberFormatOptions;
  let base: string;
  if (customFmt) {
    base = formatNumber(v, cfg.format);
  } else {
    const abs = Math.abs(v);
    if (cfg.abbrev !== false) {
      if (abs >= 1e9) base = (v / 1e9).toFixed(dec !== undefined ? dec : 1) + 'B';
      else if (abs >= 1e6) base = (v / 1e6).toFixed(dec !== undefined ? dec : 1) + 'M';
      else if (abs >= 1e3) base = (v / 1e3).toFixed(dec !== undefined ? dec : 1) + 'K';
      else base = v.toLocaleString(undefined, opts);
    } else {
      base = v.toLocaleString(undefined, opts);
    }
  }
  return (cfg.prefix || '') + base + (cfg.suffix || '');
}

export function render(container: HTMLElement, config: KpiConfig): void {
  container.innerHTML = '';
  const idx = config.gradientIdx || 0;
  const bg = config.color
    ? `linear-gradient(135deg, ${config.color}, ${config.color}88)`
    : kpiGradient(idx);

  const card = document.createElement('div');
  card.style.cssText = `
    background: ${bg}; border-radius: 12px; padding: 20px;
    color: #fff; display: flex; flex-direction: column; justify-content: space-between;
    height: 100%; box-shadow: 0 4px 16px rgba(0,0,0,0.15);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  `;

  // Label
  const labelEl = document.createElement('div');
  labelEl.style.cssText = 'font-size: 12px; font-weight: 500; opacity: 0.85; text-transform: uppercase; letter-spacing: 0.5px;';
  labelEl.textContent = config.label || '';

  // Value
  const valueEl = document.createElement('div');
  valueEl.style.cssText = 'font-size: 32px; font-weight: 700; line-height: 1.1; margin: 8px 0;';
  valueEl.textContent = _kpiDisplay(config.value, config);

  // Trend
  const trendEl = document.createElement('div');
  trendEl.style.cssText = 'font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 4px;';
  if (config.showTrend !== false && config.trend !== undefined && config.trend !== 0) {
    const isUp = config.trend > 0;
    trendEl.innerHTML = `
      <span style="color:${isUp ? '#90EE90' : '#FFB6C1'}">${isUp ? '\u25B2' : '\u25BC'}</span>
      <span>${isUp ? '+' : ''}${config.trend.toFixed(1)}%</span>
    `;
  }

  card.appendChild(labelEl);
  card.appendChild(valueEl);

  // Sparkline area
  if (config.sparkData && config.sparkData.length > 1) {
    const sparkContainer = document.createElement('div');
    sparkContainer.style.cssText = 'height: 36px; margin-top: 8px;';
    card.appendChild(sparkContainer);

    // Render sparkline after DOM attachment
    setTimeout(() => {
      import('../charts/sparkline').then(mod => {
        mod.render(sparkContainer, config.sparkData!, '#fff');
      }).catch(() => {
        // Fallback: render inline sparkline
        renderInlineSparkline(sparkContainer, config.sparkData!);
      });
    }, 0);
  }

  card.appendChild(trendEl);
  container.appendChild(card);
}

function renderInlineSparkline(container: HTMLElement, data: number[]): void {
  const w = container.clientWidth || 120;
  const h = container.clientHeight || 36;
  if (!data || data.length < 2 || w === 0) return;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * (h - 4) - 2;
    return `${x},${y}`;
  });

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', String(w));
  svg.setAttribute('height', String(h));

  const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  polyline.setAttribute('points', points.join(' '));
  polyline.setAttribute('fill', 'none');
  polyline.setAttribute('stroke', 'rgba(255,255,255,0.7)');
  polyline.setAttribute('stroke-width', '1.5');
  svg.appendChild(polyline);

  container.appendChild(svg);
}
