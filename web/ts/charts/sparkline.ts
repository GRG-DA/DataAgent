// ── D3.js Sparkline ─────────────────────────────────────────────────────
// Minimal line chart for KPI cards: no axes, no legend, fill=true.

import { getTheme, addAreaGradient } from './theme';

export function render(
  container: HTMLElement,
  data: number[],
  color?: string,
  width?: number,
  height?: number
): void {
  const w = width || container.clientWidth || 120;
  const h = height || container.clientHeight || 36;
  const theme = getTheme();
  const stroke = color || theme.primary;

  // Clear previous
  container.innerHTML = '';

  if (!data || data.length < 2) return;

  const svg = (window as any).d3.select(container)
    .append('svg')
    .attr('width', w)
    .attr('height', h);

  const pad = 2;
  const x = (window as any).d3.scaleLinear()
    .domain([0, data.length - 1])
    .range([pad, w - pad]);
  const y = (window as any).d3.scaleLinear()
    .domain([(window as any).d3.min(data), (window as any).d3.max(data)])
    .range([h - pad, pad]);

  const line = (window as any).d3.line()
    .x((_, i) => x(i))
    .y(d => y(d))
    .curve((window as any).d3.curveMonotoneX);

  const area = (window as any).d3.area()
    .x((_, i) => x(i))
    .y0(h)
    .y1(d => y(d))
    .curve((window as any).d3.curveMonotoneX);

  const gradId = 'spark-grad-' + Math.random().toString(36).slice(2, 8);
  addAreaGradient(svg, gradId, stroke);

  svg.append('path')
    .datum(data)
    .attr('d', area)
    .attr('fill', `url(#${gradId})`);

  svg.append('path')
    .datum(data)
    .attr('d', line)
    .attr('fill', 'none')
    .attr('stroke', stroke)
    .attr('stroke-width', 1.5);

  // End-point circle
  svg.append('circle')
    .attr('cx', x(data.length - 1))
    .attr('cy', y(data[data.length - 1]))
    .attr('r', 2.5)
    .attr('fill', stroke);
}
