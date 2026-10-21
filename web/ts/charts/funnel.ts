// ── D3.js Funnel Chart ──────────────────────────────────────────────────
import { getTheme, palette } from './theme';
import * as Tooltip from './tooltip';

export function render(
  container: HTMLElement,
  labels: string[],
  data: number[],
  opts?: { colors?: string[] }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  const margin = { top: 16, right: 20, bottom: 16, left: 20 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!labels?.length || !data?.length) return;
  const colors = opts?.colors || palette(labels.length);
  const maxVal = (window as any).d3.max(data) || 1;

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const n = labels.length;
  const barH = h / n;
  const centerX = w / 2;

  data.forEach((val, i) => {
    const pct = val / maxVal;
    const halfW = (w * 0.9 / 2) * pct;
    const nextPct = i < n - 1 ? data[i + 1] / maxVal : pct * 0.8;
    const nextHalfW = (w * 0.9 / 2) * nextPct;

    const y0 = i * barH;
    const y1 = (i + 1) * barH;

    const points = [
      [centerX - halfW, y0],
      [centerX + halfW, y0],
      [centerX + nextHalfW, y1],
      [centerX - nextHalfW, y1],
    ];

    g.append('polygon')
      .attr('points', points.map(p => p.join(',')).join(' '))
      .attr('fill', colors[i]).attr('opacity', 0.85)
      .attr('stroke', theme.cardBg).attr('stroke-width', 1.5)
      .style('cursor', 'pointer')
      .on('mouseenter', function(event: MouseEvent) {
        (window as any).d3.select(this).attr('opacity', 1);
        Tooltip.show(Tooltip.multi([['Stage', labels[i]], ['Value', val.toLocaleString()]]), event);
      })
      .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
      .on('mouseleave', function() { (window as any).d3.select(this).attr('opacity', 0.85); Tooltip.hide(); });

    // Label
    g.append('text')
      .attr('x', centerX).attr('y', y0 + barH / 2 + 4)
      .attr('text-anchor', 'middle').attr('fill', '#fff').attr('font-size', '12px').attr('font-weight', '600')
      .text(labels[i]);

    // Value
    g.append('text')
      .attr('x', centerX).attr('y', y0 + barH / 2 + 18)
      .attr('text-anchor', 'middle').attr('fill', 'rgba(255,255,255,0.8)').attr('font-size', '10px')
      .text(val >= 1000 ? (val / 1000).toFixed(1) + 'K' : val.toLocaleString());
  });
}
