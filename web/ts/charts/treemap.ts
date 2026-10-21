// ── D3.js Treemap ───────────────────────────────────────────────────────
import { getTheme, palette } from './theme';
import * as Tooltip from './tooltip';

export interface TreemapItem { label: string; value: number; details?: string; }

export function render(
  container: HTMLElement,
  data: TreemapItem[],
  opts?: { colors?: string[] }
): void {
  const theme = getTheme();
  const cW = container.clientWidth || 400;
  const cH = container.clientHeight || 280;
  container.innerHTML = '';
  if (!data?.length) return;
  const colors = opts?.colors || palette(data.length);

  const svg = (window as any).d3.select(container).append('svg').attr('width', cW).attr('height', cH);

  const root = (window as any).d3.hierarchy({ children: data }).sum(d => d.value).sort((a: any, b: any) => b.value - a.value);
  (window as any).d3.treemap().size([cW, cH]).padding(3).round(true)(root);

  const cells = svg.selectAll('g').data(root.leaves()).enter().append('g')
    .attr('transform', (d: any) => `translate(${d.x0},${d.y0})`);

  cells.append('rect')
    .attr('width', (d: any) => Math.max(0, d.x1 - d.x0))
    .attr('height', (d: any) => Math.max(0, d.y1 - d.y0))
    .attr('fill', (_, i: number) => colors[i % colors.length])
    .attr('rx', 4).attr('opacity', 0.85)
    .on('mouseenter', function(event: MouseEvent, d: any) {
      (window as any).d3.select(this).attr('opacity', 1);
      Tooltip.show(Tooltip.multi([['Label', d.data.label], ['Value', d.data.value.toLocaleString()], ['Details', d.data.details || '']]), event);
    })
    .on('mousemove', (event: MouseEvent) => Tooltip.move(event))
    .on('mouseleave', function() { (window as any).d3.select(this).attr('opacity', 0.85); Tooltip.hide(); });

  cells.append('text')
    .attr('x', 6).attr('y', 18)
    .attr('fill', '#fff').attr('font-size', '11px').attr('font-weight', '600')
    .text((d: any) => {
      const w = d.x1 - d.x0;
      return w > 50 ? d.data.label.slice(0, Math.floor(w / 7)) : '';
    });

  cells.append('text')
    .attr('x', 6).attr('y', 32)
    .attr('fill', 'rgba(255,255,255,0.8)').attr('font-size', '10px')
    .text((d: any) => {
      const w = d.x1 - d.x0;
      return w > 40 ? d.data.value.toLocaleString() : '';
    });
}
