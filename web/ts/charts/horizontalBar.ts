// ── D3.js Horizontal Bar Chart ──────────────────────────────────────────
import { getTheme, palette } from './theme';
import * as Tooltip from './tooltip';

export interface HBarData {
  labels: string[];
  datasets: { label: string; data: number[] }[];
}

export function render(
  container: HTMLElement,
  data: HBarData,
  opts?: { legend?: boolean; dataLabels?: boolean; colors?: string[]; sorted?: boolean }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  const margin = { top: 24, right: 40, bottom: 24, left: 100 };
  const w = cW - margin.left - margin.right;
  const h = cH - margin.top - margin.bottom;
  container.innerHTML = '';
  if (!data || !data.labels.length || !data.datasets.length) return;
  const colors = opts?.colors || palette(data.datasets.length);

  let labels = [...data.labels];
  let datasets = data.datasets.map(ds => ({ ...ds, data: [...ds.data] }));
  if (opts?.sorted !== false && datasets.length === 1) {
    const pairs = labels.map((l, i) => ({ l, v: datasets[0].data[i] }));
    pairs.sort((a, b) => b.v - a.v);
    labels = pairs.map(p => p.l);
    datasets[0].data = pairs.map(p => p.v);
  }

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const y = (window as any).d3.scaleBand().domain(labels).range([0, h]).padding(0.25);
  const allVals = datasets.flatMap(ds => ds.data);
  const x = (window as any).d3.scaleLinear().domain([0, (window as any).d3.max(allVals) * 1.15 || 10]).range([0, w]).nice();

  // Grid
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x).tickSize(-h).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', theme.gridLine).attr('stroke-dasharray', '2,2'));

  // Y axis
  g.append('g').call((window as any).d3.axisLeft(y))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  // X axis
  g.append('g').attr('transform', `translate(0,${h})`).call((window as any).d3.axisBottom(x).ticks(5).tickFormat((window as any).d3.format('~s')))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'));

  // Bars
  datasets.forEach((ds, di) => {
    g.selectAll(`.hbar-${di}`).data(ds.data).enter().append('rect')
      .attr('x', 0).attr('y', (_, i) => y(labels[i])!)
      .attr('width', d => x(d)).attr('height', y.bandwidth())
      .attr('fill', colors[di]).attr('rx', 3)
      .on('mouseenter', function(event: MouseEvent, d: number) {
        (window as any).d3.select(this).attr('opacity', 0.8);
        const i = ds.data.indexOf(d);
        Tooltip.show(Tooltip.multi([[ds.label, d.toLocaleString()], ['Category', labels[i]]]), event);
      })
      .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
      .on('mouseleave', function() { (window as any).d3.select(this).attr('opacity', 1); Tooltip.hide(); });

    if (opts?.dataLabels) {
      g.selectAll(`.hlbl-${di}`).data(ds.data).enter().append('text')
        .attr('x', d => x(d) + 5).attr('y', (_, i) => y(labels[i])! + y.bandwidth() / 2 + 4)
        .attr('fill', theme.text).attr('font-size', '10px').attr('font-weight', '500')
        .text(d => d >= 1000 ? (d / 1000).toFixed(1) + 'K' : d.toLocaleString());
    }
  });
}
