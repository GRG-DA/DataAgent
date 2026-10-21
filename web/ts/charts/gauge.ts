// ── D3.js Radial Gauge ──────────────────────────────────────────────────
import { getTheme } from './theme';

export function render(
  container: HTMLElement,
  value: number,
  max?: number,
  opts?: { label?: string; color?: string }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 200;
  const cH = container.clientHeight || 160;
  container.innerHTML = '';

  const total = max || 100;
  const pct = Math.min(1, Math.max(0, value / total));
  const color = opts?.color || theme.primary;

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const cx = cW / 2;
  const cy = cH * 0.6;
  const radius = Math.min(cW, cH) / 2 - 16;

  const startAngle = -Math.PI * 0.75;
  const endAngle = Math.PI * 0.75;
  const valueAngle = startAngle + pct * (endAngle - startAngle);

  // Background arc
  const bgArc = (window as any).d3.arc().innerRadius(radius * 0.7).outerRadius(radius)
    .startAngle(startAngle).endAngle(endAngle);
  svg.append('path').attr('d', bgArc()).attr('transform', `translate(${cx},${cy})`)
    .attr('fill', theme.gridLine);

  // Value arc with gradient colors
  if (pct > 0) {
    const valArc = (window as any).d3.arc().innerRadius(radius * 0.7).outerRadius(radius)
      .startAngle(startAngle).endAngle(valueAngle);
    svg.append('path').attr('d', valArc()).attr('transform', `translate(${cx},${cy})`)
      .attr('fill', pct < 0.4 ? '#FF6B6B' : pct < 0.7 ? '#FFB347' : color);
  }

  // Needle
  const needleLen = radius * 0.6;
  const nx = cx + needleLen * Math.cos(valueAngle - Math.PI / 2);
  const ny = cy + needleLen * Math.sin(valueAngle - Math.PI / 2);
  svg.append('line').attr('x1', cx).attr('y1', cy).attr('x2', nx).attr('y2', ny)
    .attr('stroke', theme.text).attr('stroke-width', 2.5).attr('stroke-linecap', 'round');
  svg.append('circle').attr('cx', cx).attr('cy', cy).attr('r', 4).attr('fill', theme.text);

  // Value text
  const displayVal = value >= 1000 ? (value / 1000).toFixed(1) + 'K' : value.toLocaleString();
  svg.append('text').attr('x', cx).attr('y', cy + radius * 0.45)
    .attr('text-anchor', 'middle').attr('fill', theme.text)
    .attr('font-size', '22px').attr('font-weight', '700').text(displayVal);

  if (opts?.label) {
    svg.append('text').attr('x', cx).attr('y', cy + radius * 0.65)
      .attr('text-anchor', 'middle').attr('fill', theme.textSecondary)
      .attr('font-size', '11px').text(opts.label);
  }
}
