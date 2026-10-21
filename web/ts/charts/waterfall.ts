// ── D3.js Waterfall Chart ───────────────────────────────────────────────
import { getTheme } from './theme';
import * as Tooltip from './tooltip';

export interface WaterfallItem { label: string; value: number; }

export function render(
  container: HTMLElement,
  items: WaterfallItem[],
  opts?: { positiveColor?: string; negativeColor?: string }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  const margin = { top: 24, right: 20, bottom: 48, left: 52 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!items?.length) return;
  const posColor = opts?.positiveColor || '#00D4AA';
  const negColor = opts?.negativeColor || '#FF6B6B';

  // Compute cumulative positions
  let cum = 0;
  const bars = items.map(item => {
    const start = cum;
    cum += item.value;
    return { label: item.label, value: item.value, start, end: cum, isPositive: item.value >= 0 };
  });
  // Add total bar
  bars.push({ label: 'Total', value: cum, start: 0, end: cum, isPositive: cum >= 0 });

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = (window as any).d3.scaleBand().domain(bars.map(b => b.label)).range([0, w]).padding(0.25);
  const allY = bars.flatMap(b => [b.start, b.end]);
  const y = (window as any).d3.scaleLinear()
    .domain([(window as any).d3.min(allY) * 1.1, (window as any).d3.max(allY) * 1.1]).range([h, 0]).nice();

  // Grid
  g.append('g').call((window as any).d3.axisLeft(y).ticks(5).tickSize(-w).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', theme.gridLine).attr('stroke-dasharray', '2,2'));

  // Axes
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '10px')
      .attr('transform', 'rotate(-35)').attr('text-anchor', 'end'));
  g.append('g').call((window as any).d3.axisLeft(y).ticks(5).tickFormat((window as any).d3.format('~s')))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  // Connector lines + bars
  bars.forEach((bar, i) => {
    const bx = x(bar.label)!;
    const bw = x.bandwidth();
    const top = Math.min(y(bar.start), y(bar.end));
    const bottom = Math.max(y(bar.start), y(bar.end));

    g.append('rect')
      .attr('x', bx).attr('y', top).attr('width', bw).attr('height', Math.max(1, bottom - top))
      .attr('fill', i === bars.length - 1 ? theme.primary : bar.isPositive ? posColor : negColor)
      .attr('rx', 2)
      .on('mouseenter', function(event: MouseEvent) {
        (window as any).d3.select(this).attr('opacity', 0.8);
        Tooltip.show(Tooltip.multi([['Label', bar.label], ['Value', bar.value.toLocaleString()]]), event);
      })
      .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
      .on('mouseleave', function() { (window as any).d3.select(this).attr('opacity', 1); Tooltip.hide(); });

    // Connector line to next bar
    if (i < bars.length - 1) {
      g.append('line')
        .attr('x1', bx + bw).attr('y1', y(bar.end))
        .attr('x2', x(bars[i + 1].label)!).attr('y2', y(bar.end))
        .attr('stroke', theme.gridLine).attr('stroke-width', 1).attr('stroke-dasharray', '3,2');
    }

    // Data label
    g.append('text').attr('x', bx + bw / 2).attr('y', top - 4)
      .attr('text-anchor', 'middle').attr('fill', theme.text).attr('font-size', '10px').attr('font-weight', '500')
      .text(bar.value >= 1000 ? (bar.value / 1000).toFixed(1) + 'K' : bar.value.toLocaleString());
  });
}
