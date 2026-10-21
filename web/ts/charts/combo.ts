// ── D3.js Combo Chart (Bar + Line) ──────────────────────────────────────
import { getTheme, palette } from './theme';
import * as Tooltip from './tooltip';

export interface ComboData {
  labels: string[];
  datasets: { label: string; data: number[]; type?: string }[];
}

export function render(
  container: HTMLElement,
  data: ComboData,
  opts?: { colors?: string[]; legend?: boolean }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  const margin = { top: 24, right: 50, bottom: 48, left: 52 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!data?.labels?.length || !data?.datasets?.length) return;
  const colors = opts?.colors || palette(data.datasets.length);

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = (window as any).d3.scaleBand().domain(data.labels).range([0, w]).padding(0.2);

  // Left Y axis (bars)
  const barVals = data.datasets.filter(ds => ds.type !== 'line').flatMap(ds => ds.data);
  const yLeft = (window as any).d3.scaleLinear().domain([0, (window as any).d3.max(barVals) * 1.15 || 10]).range([h, 0]).nice();

  // Right Y axis (lines)
  const lineVals = data.datasets.filter(ds => ds.type === 'line').flatMap(ds => ds.data);
  const yRight = (window as any).d3.scaleLinear().domain([0, (window as any).d3.max(lineVals) * 1.15 || 10]).range([h, 0]).nice();

  // Grid
  g.append('g').call((window as any).d3.axisLeft(yLeft).ticks(5).tickSize(-w).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', theme.gridLine).attr('stroke-dasharray', '2,2'));

  // Axes
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '10px')
      .attr('transform', 'rotate(-35)').attr('text-anchor', 'end'));
  g.append('g').call((window as any).d3.axisLeft(yLeft).ticks(5).tickFormat((window as any).d3.format('~s')))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  if (lineVals.length > 0) {
    g.append('g').attr('transform', `translate(${w},0)`).call((window as any).d3.axisRight(yRight).ticks(5).tickFormat((window as any).d3.format('~s')))
      .call(g => g.select('.domain').attr('stroke', theme.axisColor))
      .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));
  }

  let colorIdx = 0;
  data.datasets.forEach((ds) => {
    if (ds.type === 'line') {
      const line = (window as any).d3.line()
        .x((_: any, i: number) => x(data.labels[i])! + x.bandwidth() / 2)
        .y((d: number) => yRight(d))
        .curve((window as any).d3.curveMonotoneX);
      g.append('path').datum(ds.data).attr('d', line)
        .attr('fill', 'none').attr('stroke', colors[colorIdx]).attr('stroke-width', 2.5);
      g.selectAll(`.ldot-${colorIdx}`).data(ds.data).enter().append('circle')
        .attr('cx', (_, i) => x(data.labels[i])! + x.bandwidth() / 2)
        .attr('cy', d => yRight(d)).attr('r', 3.5)
        .attr('fill', '#fff').attr('stroke', colors[colorIdx]).attr('stroke-width', 2);
    } else {
      g.selectAll(`.cbar-${colorIdx}`).data(ds.data).enter().append('rect')
        .attr('x', (_, i) => x(data.labels[i])!)
        .attr('y', d => yLeft(d)).attr('height', d => Math.max(0, h - yLeft(d)))
        .attr('width', x.bandwidth()).attr('fill', colors[colorIdx]).attr('rx', 2)
        .on('mouseenter', function(event: MouseEvent, d: number) {
          (window as any).d3.select(this).attr('opacity', 0.8);
          Tooltip.show(Tooltip.multi([[ds.label, d.toLocaleString()], ['Category', data.labels[ds.data.indexOf(d)]]]), event);
        })
        .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
        .on('mouseleave', function() { (window as any).d3.select(this).attr('opacity', 1); Tooltip.hide(); });
    }
    colorIdx++;
  });

  if (opts?.legend !== false) {
    const lg = svg.append('g').attr('transform', `translate(${margin.left},8)`);
    data.datasets.forEach((ds, i) => {
      const r = lg.append('g').attr('transform', `translate(${i * 100},0)`);
      if (ds.type === 'line') {
        r.append('line').attr('x1', 0).attr('x2', 14).attr('y1', 5).attr('y2', 5).attr('stroke', colors[i]).attr('stroke-width', 2.5);
      } else {
        r.append('rect').attr('width', 10).attr('height', 10).attr('rx', 2).attr('fill', colors[i]);
      }
      r.append('text').attr('x', 18).attr('y', 9).text(ds.label).attr('fill', theme.textSecondary).attr('font-size', '11px');
    });
  }
}
