// ── D3.js Scatter Plot ──────────────────────────────────────────────────
import { getTheme } from './theme';
import * as Tooltip from './tooltip';

export interface ScatterPoint { x: number; y: number; label?: string; category?: string; }

export function render(
  container: HTMLElement,
  data: ScatterPoint[],
  opts?: { color?: string; xLabel?: string; yLabel?: string }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  const margin = { top: 20, right: 20, bottom: 40, left: 52 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!data?.length) return;
  const color = opts?.color || theme.primary;

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = (window as any).d3.scaleLinear()
    .domain([(window as any).d3.min(data, d => d.x), (window as any).d3.max(data, d => d.x)]).range([0, w]).nice();
  const y = (window as any).d3.scaleLinear()
    .domain([(window as any).d3.min(data, d => d.y), (window as any).d3.max(data, d => d.y)]).range([h, 0]).nice();

  // Grid
  g.append('g').call((window as any).d3.axisLeft(y).tickSize(-w).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', theme.gridLine).attr('stroke-dasharray', '2,2'));

  // Axes
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x).ticks(5))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));
  g.append('g').call((window as any).d3.axisLeft(y).ticks(5))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  // Points
  g.selectAll('circle').data(data).enter().append('circle')
    .attr('cx', d => x(d.x)).attr('cy', d => y(d.y)).attr('r', 5)
    .attr('fill', color).attr('opacity', 0.7).attr('stroke', '#fff').attr('stroke-width', 1.5)
    .on('mouseenter', function(event: MouseEvent, d: ScatterPoint) {
      (window as any).d3.select(this).transition().duration(150).attr('r', 8).attr('opacity', 1);
      Tooltip.show(Tooltip.multi([['X', d.x.toLocaleString()], ['Y', d.y.toLocaleString()], ['Label', d.label || '']]), event);
    })
    .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
    .on('mouseleave', function() { (window as any).d3.select(this).transition().duration(150).attr('r', 5).attr('opacity', 0.7); Tooltip.hide(); });
}
