// ── Cross-Filter Event System ───────────────────────────────────────────
// Dispatches custom events when chart elements are clicked.
// The bidashboard listens and updates slicer filters.

export interface CrossFilterDetail {
  field: string;
  value: string;
}

const EVENT_NAME = 'crossfilter';

export function dispatch(field: string, value: string): void {
  window.dispatchEvent(new CustomEvent(EVENT_NAME, {
    detail: { field, value } as CrossFilterDetail,
  }));
}

export function listen(handler: (detail: CrossFilterDetail) => void): () => void {
  const fn = (e: Event) => handler((e as CustomEvent).detail);
  window.addEventListener(EVENT_NAME, fn);
  return () => window.removeEventListener(EVENT_NAME, fn);
}
