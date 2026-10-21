// ── D3.js Pie Chart ─────────────────────────────────────────────────────
import { getTheme, palette } from './theme';
import * as Tooltip from './tooltip';

export function render(
  container: HTMLElement,
  labels: string[],
  data: number[],
  opts?: { colors?: string[]; legend?: boolean; dataLabels?: boolean; width?: number; height?: number }
): void {
  const theme = getTheme();
  const cW = opts?.width || container.clientWidth || 300;
  const cH = opts?.height || container.clientHeight || 280;
  container.innerHTML = '';
  if (!labels?.length || !data?.length) return;
  const colors = opts?.colors || palette(labels.length);
  const total = data.reduce((s, v) => s + v, 0);

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);
  const radius = Math.min(cW, cH) / 2 - 20;
  const g = svg.append('g').attr('transform', `translate(${cW / 2},${cH / 2})`);

  const pie = (window as any).d3.pie().value((d: number) => d).sort(null);
  const arc = (window as any).d3.arc().innerRadius(0).outerRadius(radius);
  const hoverArc = (window as any).d3.arc().innerRadius(0).outerRadius(radius + 6);

  const arcs = g.selectAll('.arc').data(pie(data)).enter().append('g').attr('class', 'arc');

  arcs.append('path')
    .attr('d', arc)
    .attr('fill', (_, i: number) => colors[i])
    .attr('stroke', theme.cardBg).attr('stroke-width', 2)
    .style('cursor', 'pointer')
    .on('mouseenter', function(event: MouseEvent, d: any) {
      (window as any).d3.select(this).transition().duration(150).attr('d', hoverArc);
      const i = data.indexOf(d.data);
      const pct = total > 0 ? ((d.data / total) * 100).toFixed(1) : '0';
      Tooltip.show(Tooltip.multi([['Category', labels[i]], ['Value', d.data.toLocaleString()], ['Percent', pct + '%']]), event);
    })
    .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
    .on('mouseleave', function() {
      (window as any).d3.select(this).transition().duration(150).attr('d', arc);
      Tooltip.hide();
    });

  // Percentage labels on slices
  if (opts?.dataLabels !== false) {
    arcs.append('text')
      .attr('transform', (d: any) => `translate(${arc.centroid(d)})`)
      .attr('text-anchor', 'middle').attr('fill', '#fff').attr('font-size', '11px').attr('font-weight', '600')
      .text((d: any) => {
        const pct = total > 0 ? (d.data / total) * 100 : 0;
        return pct > 5 ? pct.toFixed(0) + '%' : '';
      });
  }

  // Legend
  if (opts?.legend !== false) {
    const lg = svg.append('g').attr('transform', `translate(10, ${cH - labels.length * 18 - 5})`);
    labels.forEach((l, i) => {
      const row = lg.append('g').attr('transform', `translate(0,${i * 18})`);
      row.append('rect').attr('width', 10).attr('height', 10).attr('rx', 2).attr('fill', colors[i]);
      row.append('text').attr('x', 14).attr('y', 9).text(l).attr('fill', theme.textSecondary).attr('font-size', '11px');
    });
  }
}
