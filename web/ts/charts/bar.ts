// ── D3.js Bar / Column Chart ────────────────────────────────────────────
import { getTheme, palette } from './theme';
import * as Tooltip from './tooltip';

export interface BarData {
  labels: string[];
  datasets: { label: string; data: number[] }[];
}

export function render(
  container: HTMLElement,
  data: BarData,
  opts?: { legend?: boolean; dataLabels?: boolean; stacked?: boolean; colors?: string[]; sorted?: boolean }
): void {
  const theme = getTheme();
  const cW = opts && (opts as any).width || container.clientWidth || 400;
  const cH = opts && (opts as any).height || container.clientHeight || 280;
  const margin = { top: 24, right: 20, bottom: 48, left: 52 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!data || !data.labels.length || !data.datasets.length) return;
  const colors = opts?.colors || palette(data.datasets.length);

  let labels = [...data.labels];
  let datasets = data.datasets.map(ds => ({ ...ds, data: [...ds.data] }));
  if (opts?.sorted && !opts?.stacked && datasets.length === 1) {
    const pairs = labels.map((l, i) => ({ l, v: datasets[0].data[i] }));
    pairs.sort((a, b) => b.v - a.v);
    labels = pairs.map(p => p.l);
    datasets[0].data = pairs.map(p => p.v);
  }

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = (window as any).d3.scaleBand().domain(labels).range([0, w]).padding(0.25);
  const allVals = datasets.flatMap(ds => ds.data);
  const y = (window as any).d3.scaleLinear().domain([0, (window as any).d3.max(allVals) * 1.15 || 10]).range([h, 0]).nice();

  // Grid
  g.append('g').call((window as any).d3.axisLeft(y).tickSize(-w).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', theme.gridLine).attr('stroke-dasharray', '2,2'));

  // X axis
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px')
      .attr('transform', 'rotate(-35)').attr('text-anchor', 'end').attr('dx', '-0.5em').attr('dy', '0.5em'));

  // Y axis
  g.append('g').call((window as any).d3.axisLeft(y).ticks(5).tickFormat((window as any).d3.format('~s')))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  // Bars
  if (opts?.stacked && datasets.length > 1) {
    const stacked = (window as any).d3.stack().keys(datasets.map((_, i) => String(i)))(
      labels.map((_, i) => Object.fromEntries(datasets.map((ds, j) => [String(j), ds.data[i]])))
    );
    const yStack = (window as any).d3.scaleLinear()
      .domain([0, (window as any).d3.max(stacked.flat(2)) * 1.1 || 10]).range([h, 0]).nice();
    g.append('g').call((window as any).d3.axisLeft(yStack).ticks(5).tickFormat((window as any).d3.format('~s')))
      .call(g => g.select('.domain').attr('stroke', theme.axisColor))
      .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));
    stacked.forEach((layer: any, li: number) => {
      g.selectAll(`.bar-s-${li}`).data(layer).enter().append('rect')
        .attr('x', (d: any) => x(labels[+d[0]]))
        .attr('y', (d: any) => yStack(d[1]))
        .attr('height', (d: any) => Math.max(0, yStack(d[0]) - yStack(d[1])))
        .attr('width', x.bandwidth()).attr('fill', colors[li]).attr('rx', 2);
    });
  } else {
    const barW = datasets.length > 1 ? x.bandwidth() / datasets.length : x.bandwidth();
    datasets.forEach((ds, di) => {
      g.selectAll(`.bar-${di}`).data(ds.data).enter().append('rect')
        .attr('x', (_, i) => x(labels[i])! + di * barW)
        .attr('y', d => y(d))
        .attr('height', d => Math.max(0, h - y(d)))
        .attr('width', barW - (datasets.length > 1 ? 2 : 0))
        .attr('fill', colors[di]).attr('rx', 2)
        .on('mouseenter', function(event: MouseEvent, d: number) {
          (window as any).d3.select(this).attr('opacity', 0.8);
          const i = ds.data.indexOf(d);
          Tooltip.show(Tooltip.multi([[ds.label, d.toLocaleString()], ['Category', labels[i]]]), event);
        })
        .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
        .on('mouseleave', function() { (window as any).d3.select(this).attr('opacity', 1); Tooltip.hide(); });

      if (opts?.dataLabels) {
        g.selectAll(`.lbl-${di}`).data(ds.data).enter().append('text')
          .attr('x', (_, i) => x(labels[i])! + di * barW + barW / 2)
          .attr('y', d => y(d) - 5).attr('text-anchor', 'middle')
          .attr('fill', theme.text).attr('font-size', '10px').attr('font-weight', '500')
          .text(d => d >= 1000 ? (d / 1000).toFixed(1) + 'K' : d.toLocaleString());
      }
    });
  }

  // Legend
  if (opts?.legend !== false && datasets.length > 1) {
    const lg = svg.append('g').attr('transform', `translate(${margin.left},8)`);
    datasets.forEach((ds, i) => {
      const r = lg.append('g').attr('transform', `translate(${i * 100},0)`);
      r.append('rect').attr('width', 10).attr('height', 10).attr('rx', 2).attr('fill', colors[i]);
      r.append('text').attr('x', 14).attr('y', 9).text(ds.label).attr('fill', theme.textSecondary).attr('font-size', '11px');
    });
  }
}
