// ── Data Table Renderer ─────────────────────────────────────────────────
import { getTheme, themeIsLight } from './theme';

export interface TableConfig {
  columns: string[];
  rows: any[][];
  maxRows?: number;
  pageSize?: number;
}

export function render(container: HTMLElement, config: TableConfig): void {
  const theme = getTheme();
  container.innerHTML = '';
  if (!config?.columns?.length || !config?.rows?.length) {
    container.innerHTML = '<div style="padding:20px;text-align:center;color:#999">No data available</div>';
    return;
  }

  const maxRows = config.maxRows || 50;
  const pageSize = config.pageSize || 20;
  const visibleRows = config.rows.slice(0, maxRows);
  let page = 0;
  const totalPages = Math.ceil(visibleRows.length / pageSize);

  function draw() {
    container.innerHTML = '';
    const start = page * pageSize;
    const pageRows = visibleRows.slice(start, start + pageSize);

    const table = document.createElement('table');
    table.style.cssText = `
      width: 100%; border-collapse: collapse; font-size: 12px;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    `;

    // Header
    const thead = document.createElement('thead');
    const htr = document.createElement('tr');
    config.columns.forEach(col => {
      const th = document.createElement('th');
      th.textContent = col;
      th.style.cssText = `
        background: ${theme.primary}; color: #fff; padding: 10px 12px;
        text-align: left; font-weight: 600; position: sticky; top: 0;
        font-size: 11px; text-transform: uppercase; letter-spacing: 0.3px;
      `;
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    pageRows.forEach((row, ri) => {
      const tr = document.createElement('tr');
      tr.style.background = ri % 2 === 0 ? theme.cardBg : (themeIsLight(theme) ? '#F8F8FC' : '#2A2A45');
      row.forEach(cell => {
        const td = document.createElement('td');
        td.textContent = cell != null ? String(cell) : '';
        td.style.cssText = `padding: 8px 12px; border-bottom: 1px solid ${theme.gridLine}; color: ${theme.text};`;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);

    // Pagination
    if (totalPages > 1) {
      const pag = document.createElement('div');
      pag.style.cssText = `
        display: flex; justify-content: center; align-items: center; gap: 8px;
        padding: 8px; font-size: 12px; color: ${theme.textSecondary};
      `;
      pag.innerHTML = `
        <button class="tp" data-dir="-1" ${page === 0 ? 'disabled' : ''} style="padding:4px 10px;border:1px solid ${theme.gridLine};border-radius:4px;background:${theme.cardBg};color:${theme.text};cursor:pointer">Prev</button>
        <span>Page ${page + 1} of ${totalPages}</span>
        <button class="tp" data-dir="1" ${page >= totalPages - 1 ? 'disabled' : ''} style="padding:4px 10px;border:1px solid ${theme.gridLine};border-radius:4px;background:${theme.cardBg};color:${theme.text};cursor:pointer">Next</button>
      `;
      pag.querySelectorAll('.tp').forEach(btn => {
        btn.addEventListener('click', () => {
          const dir = parseInt((btn as HTMLElement).dataset.dir || '0');
          page = Math.max(0, Math.min(totalPages - 1, page + dir));
          draw();
        });
      });
      container.appendChild(pag);
    }
  }

  draw();
}
