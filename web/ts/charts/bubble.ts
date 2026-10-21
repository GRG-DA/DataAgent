// ── D3.js Bubble Chart ──────────────────────────────────────────────────
import { getTheme, palette } from './theme';
import * as Tooltip from './tooltip';

export interface BubblePoint { x: number; y: number; r: number; label?: string; category?: string; }

export function render(
  container: HTMLElement,
  data: BubblePoint[],
  opts?: { colors?: string[] }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  const margin = { top: 20, right: 20, bottom: 40, left: 52 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!data?.length) return;
  const colors = opts?.colors || palette(8);

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = (window as any).d3.scaleLinear()
    .domain([(window as any).d3.min(data, d => d.x) * 0.9, (window as any).d3.max(data, d => d.x) * 1.1]).range([0, w]).nice();
  const y = (window as any).d3.scaleLinear()
    .domain([(window as any).d3.min(data, d => d.y) * 0.9, (window as any).d3.max(data, d => d.y) * 1.1]).range([h, 0]).nice();
  const r = (window as any).d3.scaleSqrt()
    .domain([0, (window as any).d3.max(data, d => d.r) || 1]).range([4, 30]);

  // Grid
  g.append('g').call((window as any).d3.axisLeft(y).tickSize(-w).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', theme.gridLine).attr('stroke-dasharray', '2,2'));
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x).ticks(5))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));
  g.append('g').call((window as any).d3.axisLeft(y).ticks(5))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  // Bubbles
  g.selectAll('circle').data(data).enter().append('circle')
    .attr('cx', d => x(d.x)).attr('cy', d => y(d.y)).attr('r', d => r(d.r))
    .attr('fill', (_, i) => colors[i % colors.length]).attr('opacity', 0.65)
    .attr('stroke', (_, i) => colors[i % colors.length]).attr('stroke-width', 1.5)
    .on('mouseenter', function(event: MouseEvent, d: BubblePoint) {
      (window as any).d3.select(this).transition().duration(150).attr('opacity', 0.9);
      Tooltip.show(Tooltip.multi([['Label', d.label || ''], ['X', d.x.toLocaleString()], ['Y', d.y.toLocaleString()], ['Size', d.r.toLocaleString()]]), event);
    })
    .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
    .on('mouseleave', function() { (window as any).d3.select(this).transition().duration(150).attr('opacity', 0.65); Tooltip.hide(); });
}
