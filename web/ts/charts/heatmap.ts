// ── D3.js Heatmap ───────────────────────────────────────────────────────
import { getTheme } from './theme';
import * as Tooltip from './tooltip';

export function render(
  container: HTMLElement,
  matrix: number[][],
  xLabels: string[],
  yLabels: string[],
  opts?: { title?: string }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  const margin = { top: 30, right: 20, bottom: 50, left: 80 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!matrix?.length || !xLabels?.length || !yLabels?.length) return;

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = (window as any).d3.scaleBand().domain(xLabels).range([0, w]).padding(0.05);
  const y = (window as any).d3.scaleBand().domain(yLabels).range([0, h]).padding(0.05);
  const allVals = matrix.flat();
  const color = (window as any).d3.scaleSequential((window as any).d3.interpolateYlOrRd)
    .domain([(window as any).d3.min(allVals) || 0, (window as any).d3.max(allVals) || 10]);

  // Cells
  yLabels.forEach((yl, yi) => {
    xLabels.forEach((xl, xi) => {
      const val = matrix[yi]?.[xi] || 0;
      g.append('rect')
        .attr('x', x(xl)).attr('y', y(yl))
        .attr('width', x.bandwidth()).attr('height', y.bandwidth())
        .attr('fill', color(val)).attr('rx', 2)
        .on('mouseenter', function(event: MouseEvent) {
          (window as any).d3.select(this).attr('stroke', theme.text).attr('stroke-width', 2);
          Tooltip.show(Tooltip.multi([['X', xl], ['Y', yl], ['Value', val.toLocaleString()]]), event);
        })
        .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
        .on('mouseleave', function() { (window as any).d3.select(this).attr('stroke', 'none'); Tooltip.hide(); });

      if (x.bandwidth() > 25) {
        g.append('text')
          .attr('x', x(xl)! + x.bandwidth() / 2).attr('y', y(yl)! + y.bandwidth() / 2 + 4)
          .attr('text-anchor', 'middle').attr('fill', val > (window as any).d3.max(allVals) * 0.6 ? '#fff' : theme.text)
          .attr('font-size', '10px').attr('font-weight', '500').text(val >= 1000 ? (val / 1000).toFixed(1) + 'K' : val);
      }
    });
  });

  // Axes
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x).tickSize(0))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '10px')
      .attr('transform', 'rotate(-45)').attr('text-anchor', 'end'));
  g.append('g').call((window as any).d3.axisLeft(y).tickSize(0))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  if (opts?.title) {
    svg.append('text').attr('x', margin.left).attr('y', 16).attr('fill', theme.text)
      .attr('font-size', '13px').attr('font-weight', '600').text(opts.title);
  }
}
