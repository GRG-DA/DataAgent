// ── Shared Tooltip ──────────────────────────────────────────────────────
// A single tooltip <div> shared across all D3 charts.

let _el: HTMLDivElement | null = null;

function ensureEl(): HTMLDivElement {
  if (!_el) {
    _el = document.createElement('div');
    _el.className = 'd3-tooltip';
    _el.style.cssText = `
      position: fixed; pointer-events: none; z-index: 10000;
      background: rgba(30,30,50,0.92); color: #fff; padding: 8px 12px;
      border-radius: 8px; font-size: 12px; line-height: 1.5;
      box-shadow: 0 4px 12px rgba(0,0,0,0.25); max-width: 280px;
      opacity: 0; transition: opacity 0.15s ease;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    `;
    document.body.appendChild(_el);
  }
  return _el;
}

export function show(html: string, event: MouseEvent): void {
  const el = ensureEl();
  el.innerHTML = html;
  el.style.opacity = '1';
  position(el, event);
}

export function move(event: MouseEvent): void {
  if (_el) position(_el, event);
}

export function hide(): void {
  if (_el) _el.style.opacity = '0';
}

function position(el: HTMLDivElement, event: MouseEvent): void {
  const pad = 12;
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + w > window.innerWidth - pad) x = event.clientX - w - pad;
  if (y + h > window.innerHeight - pad) y = event.clientY - h - pad;
  el.style.left = x + 'px';
  el.style.top = y + 'px';
}

// ── Tooltip HTML builders ───────────────────────────────────────────────

export function simple(label: string, value: string): string {
  return `<div style="font-weight:600">${label}</div><div>${value}</div>`;
}

export function multi(entries: [string, string][]): string {
  return entries.map(([k, v]) =>
    `<div><span style="opacity:0.7">${k}:</span> <span style="font-weight:600">${v}</span></div>`
  ).join('');
}
