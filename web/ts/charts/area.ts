// ── D3.js Area Chart ────────────────────────────────────────────────────
import { getTheme, palette, addAreaGradient } from './theme';
import * as Tooltip from './tooltip';

export interface AreaData {
  labels: string[];
  datasets: { label: string; data: number[] }[];
}

export function render(
  container: HTMLElement,
  data: AreaData,
  opts?: { legend?: boolean; colors?: string[]; stacked?: boolean }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  const margin = { top: 24, right: 20, bottom: 48, left: 52 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!data || !data.labels.length || !data.datasets.length) return;
  const colors = opts?.colors || palette(data.datasets.length);

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = (window as any).d3.scalePoint().domain(data.labels).range([0, w]).padding(0.3);
  const allVals = opts?.stacked
    ? data.labels.map((_, i) => data.datasets.reduce((s, ds) => s + ds.data[i], 0))
    : data.datasets.flatMap(ds => ds.data);
  const y = (window as any).d3.scaleLinear().domain([0, (window as any).d3.max(allVals) * 1.15 || 10]).range([h, 0]).nice();

  // Grid
  g.append('g').call((window as any).d3.axisLeft(y).tickSize(-w).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', theme.gridLine).attr('stroke-dasharray', '2,2'));

  // Axes
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '10px')
      .attr('transform', 'rotate(-40)').attr('text-anchor', 'end').attr('dx', '-0.5em'));
  g.append('g').call((window as any).d3.axisLeft(y).ticks(5).tickFormat((window as any).d3.format('~s')))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  const defs = svg.append('defs');

  if (opts?.stacked && data.datasets.length > 1) {
    const stackedData = data.labels.map((_, i) => {
      let cum = 0;
      return data.datasets.map(ds => { const v = cum; cum += ds.data[i]; return v; });
    });
    for (let di = data.datasets.length - 1; di >= 0; di--) {
      const top = data.labels.map((_, i) => stackedData[i][di] + data.datasets[di].data[i]);
      const bottom = stackedData.map(s => s[di]);
      const area = (window as any).d3.area()
        .x((_: any, i: number) => x(data.labels[i]))
        .y0((_: any, i: number) => y(bottom[i]))
        .y1((_: any, i: number) => y(top[i]))
        .curve((window as any).d3.curveMonotoneX);
      g.append('path').datum(data.labels).attr('d', area)
        .attr('fill', colors[di]).attr('opacity', 0.6);
    }
  } else {
    data.datasets.forEach((ds, di) => {
      const color = colors[di];
      const gradId = `area-${di}-${Math.random().toString(36).slice(2, 8)}`;
      addAreaGradient(defs, gradId, color);

      const area = (window as any).d3.area()
        .x((_: any, i: number) => x(data.labels[i]))
        .y0(h).y1(d => y(d))
        .curve((window as any).d3.curveMonotoneX);
      g.append('path').datum(ds.data).attr('d', area).attr('fill', `url(#${gradId})`);

      const line = (window as any).d3.line()
        .x((_: any, i: number) => x(data.labels[i]))
        .y(d => y(d))
        .curve((window as any).d3.curveMonotoneX);
      g.append('path').datum(ds.data).attr('d', line)
        .attr('fill', 'none').attr('stroke', color).attr('stroke-width', 2);

      g.selectAll(`.adot-${di}`).data(ds.data).enter().append('circle')
        .attr('cx', (_, i) => x(data.labels[i]) as number)
        .attr('cy', d => y(d)).attr('r', 3).attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2)
        .on('mouseenter', function(event: MouseEvent, d: number) {
          Tooltip.show(Tooltip.multi([[ds.label, d.toLocaleString()], ['Category', data.labels[ds.data.indexOf(d)]]]), event);
        })
        .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
        .on('mouseleave', () => Tooltip.hide());
    });
  }

  if (opts?.legend !== false && data.datasets.length > 1) {
    const lg = svg.append('g').attr('transform', `translate(${margin.left},8)`);
    data.datasets.forEach((ds, i) => {
      const r = lg.append('g').attr('transform', `translate(${i * 100},0)`);
      r.append('rect').attr('width', 10).attr('height', 10).attr('rx', 2).attr('fill', colors[i]);
      r.append('text').attr('x', 14).attr('y', 9).text(ds.label).attr('fill', theme.textSecondary).attr('font-size', '11px');
    });
  }
}
