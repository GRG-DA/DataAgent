// ── D3.js Line Chart ────────────────────────────────────────────────────
// Multi-series line chart with smooth curves, markers, rotated x-labels.

import { getTheme, palette, addAreaGradient } from './theme';
import * as Tooltip from './tooltip';

export interface LineData {
  labels: string[];
  datasets: { label: string; data: number[] }[];
}

export function render(
  container: HTMLElement,
  data: LineData,
  opts?: { legend?: boolean; dataLabels?: boolean; colors?: string[]; width?: number; height?: number }
): void {
  const theme = getTheme();
  const containerW = opts?.width || container.clientWidth || 400;
  const containerH = opts?.height || container.clientHeight || 280;
  const margin = { top: 24, right: 20, bottom: 48, left: 52 };
  const w = containerW - margin.left - margin.right;
  const h = containerH - margin.top - margin.bottom;

  container.innerHTML = '';
  if (!data || !data.labels?.length || !data.datasets?.length) return;

  const colors = opts?.colors || palette(data.datasets.length);

  const svg = (window as any).d3.select(container)
    .append('svg')
    .attr('width', containerW)
    .attr('height', containerH);

  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = (window as any).d3.scaleBand()
    .domain(data.labels)
    .range([0, w])
    .padding(0.1);

  const allVals = data.datasets.flatMap(ds => ds.data);
  const y = (window as any).d3.scaleLinear()
    .domain([0, (window as any).d3.max(allVals) * 1.1 || 10])
    .range([h, 0])
    .nice();

  // Grid lines
  g.append('g')
    .attr('class', 'grid')
    .call((window as any).d3.axisLeft(y).tickSize(-w).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').attr('stroke', theme.gridLine).attr('stroke-dasharray', '2,2'));

  // X axis
  g.append('g')
    .attr('transform', `translate(0,${h})`)
    .call((window as any).d3.axisBottom(x))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text')
      .attr('fill', theme.textSecondary)
      .attr('font-size', '11px')
      .attr('transform', 'rotate(-45)')
      .attr('text-anchor', 'end')
      .attr('dx', '-0.5em')
      .attr('dy', '0.5em'));

  // Y axis
  g.append('g')
    .call((window as any).d3.axisLeft(y).ticks(5).tickFormat((window as any).d3.format('~s')))
    .call(g => g.select('.domain').attr('stroke', theme.axisColor))
    .call(g => g.selectAll('.tick text').attr('fill', theme.textSecondary).attr('font-size', '11px'))
    .call(g => g.selectAll('.tick line').attr('stroke', theme.axisColor));

  // Lines
  const line = (window as any).d3.line()
    .x((_, i) => x(data.labels[i]) + x.bandwidth() / 2)
    .y(d => y(d))
    .curve((window as any).d3.curveMonotoneX);

  data.datasets.forEach((ds, di) => {
    const color = colors[di];

    // Area fill
    const area = (window as any).d3.area()
      .x((_, i) => x(data.labels[i]) + x.bandwidth() / 2)
      .y0(h)
      .y1(d => y(d))
      .curve((window as any).d3.curveMonotoneX);

    const gradId = `line-area-${di}-${Math.random().toString(36).slice(2, 8)}`;
    addAreaGradient(svg, gradId, color);

    g.append('path')
      .datum(ds.data)
      .attr('d', area)
      .attr('fill', `url(#${gradId})`)
      .attr('opacity', 0.3);

    // Line
    g.append('path')
      .datum(ds.data)
      .attr('d', line)
      .attr('fill', 'none')
      .attr('stroke', color)
      .attr('stroke-width', 2.5)
      .attr('stroke-linejoin', 'round');

    // Data point markers
    g.selectAll(`.dot-${di}`)
      .data(ds.data)
      .enter()
      .append('circle')
      .attr('cx', (_, i) => x(data.labels[i]) + x.bandwidth() / 2)
      .attr('cy', d => y(d))
      .attr('r', 3.5)
      .attr('fill', '#fff')
      .attr('stroke', color)
      .attr('stroke-width', 2)
      .on('mouseenter', function(event: MouseEvent, d: number) {
        (window as any).d3.select(this).transition().duration(150).attr('r', 5);
        const i = ds.data.indexOf(d);
        Tooltip.show(Tooltip.multi([
          [ds.label, d.toLocaleString()],
          ['Category', data.labels[i]],
        ]), event);
      })
      .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
      .on('mouseleave', function() {
        (window as any).d3.select(this).transition().duration(150).attr('r', 3.5);
        Tooltip.hide();
      });

    // Data labels
    if (opts?.dataLabels) {
      g.selectAll(`.label-${di}`)
        .data(ds.data)
        .enter()
        .append('text')
        .attr('x', (_, i) => x(data.labels[i]) + x.bandwidth() / 2)
        .attr('y', d => y(d) - 8)
        .attr('text-anchor', 'middle')
        .attr('fill', theme.text)
        .attr('font-size', '10px')
        .attr('font-weight', '500')
        .text(d => d >= 1000 ? (d / 1000).toFixed(1) + 'K' : d.toLocaleString());
    }
  });

  // Legend
  if (opts?.legend !== false && data.datasets.length > 1) {
    const legend = svg.append('g')
      .attr('transform', `translate(${margin.left}, 8)`);
    data.datasets.forEach((ds, i) => {
      const row = legend.append('g').attr('transform', `translate(${i * 100}, 0)`);
      row.append('rect').attr('width', 10).attr('height', 10).attr('rx', 2).attr('fill', colors[i]);
      row.append('text').attr('x', 14).attr('y', 9).text(ds.label)
        .attr('fill', theme.textSecondary).attr('font-size', '11px');
    });
  }
}
