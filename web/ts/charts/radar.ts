// ── D3.js Radar Chart ───────────────────────────────────────────────────
import { getTheme, palette } from './theme';

export interface RadarData {
  labels: string[];
  datasets: { label: string; data: number[] }[];
}

export function render(
  container: HTMLElement,
  data: RadarData,
  opts?: { colors?: string[]; legend?: boolean }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 300;
  const cH = container.clientHeight || 300;
  container.innerHTML = '';
  if (!data?.labels?.length || !data?.datasets?.length) return;
  const colors = opts?.colors || palette(data.datasets.length);
  const n = data.labels.length;
  const radius = Math.min(cW, cH) / 2 - 40;
  const angleSlice = (Math.PI * 2) / n;

  const allVals = data.datasets.flatMap(ds => ds.data);
  const maxVal = (window as any).d3.max(allVals) || 10;
  const r = (window as any).d3.scaleLinear().domain([0, maxVal * 1.1]).range([0, radius]);

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const g = svg.append('g').attr('transform', `translate(${cW / 2},${cH / 2})`);

  // Grid circles
  const levels = 5;
  for (let i = 1; i <= levels; i++) {
    const pts: [number, number][] = [];
    for (let j = 0; j < n; j++) {
      const angle = angleSlice * j - Math.PI / 2;
      pts.push([r(maxVal * 1.1 * i / levels) * Math.cos(angle), r(maxVal * 1.1 * i / levels) * Math.sin(angle)]);
    }
    g.append('polygon').attr('points', pts.map(p => p.join(',')).join(' '))
      .attr('fill', 'none').attr('stroke', theme.gridLine).attr('stroke-width', 0.8);
  }

  // Axes
  for (let i = 0; i < n; i++) {
    const angle = angleSlice * i - Math.PI / 2;
    const x2 = r(maxVal * 1.1) * Math.cos(angle);
    const y2 = r(maxVal * 1.1) * Math.sin(angle);
    g.append('line').attr('x1', 0).attr('y1', 0).attr('x2', x2).attr('y2', y2)
      .attr('stroke', theme.gridLine).attr('stroke-width', 0.8);

    const lx = (radius + 20) * Math.cos(angle);
    const ly = (radius + 20) * Math.sin(angle);
    g.append('text').attr('x', lx).attr('y', ly)
      .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
      .attr('fill', theme.textSecondary).attr('font-size', '11px')
      .text(data.labels[i]);
  }

  // Data polygons
  data.datasets.forEach((ds, di) => {
    const pts: [number, number][] = [];
    for (let i = 0; i < n; i++) {
      const angle = angleSlice * i - Math.PI / 2;
      pts.push([r(ds.data[i] || 0) * Math.cos(angle), r(ds.data[i] || 0) * Math.sin(angle)]);
    }
    g.append('polygon')
      .attr('points', pts.map(p => p.join(',')).join(' '))
      .attr('fill', colors[di]).attr('fill-opacity', 0.2)
      .attr('stroke', colors[di]).attr('stroke-width', 2);

    pts.forEach(([px, py], i) => {
      g.append('circle').attr('cx', px).attr('cy', py).attr('r', 3.5)
        .attr('fill', colors[di]).attr('stroke', '#fff').attr('stroke-width', 1.5);
    });
  });

  if (opts?.legend !== false && data.datasets.length > 1) {
    const lg = svg.append('g').attr('transform', `translate(${cW / 2 - data.datasets.length * 50}, 8)`);
    data.datasets.forEach((ds, i) => {
      const r2 = lg.append('g').attr('transform', `translate(${i * 100},0)`);
      r2.append('rect').attr('width', 10).attr('height', 10).attr('rx', 2).attr('fill', colors[i]);
      r2.append('text').attr('x', 14).attr('y', 9).text(ds.label).attr('fill', theme.textSecondary).attr('font-size', '11px');
    });
  }
}
