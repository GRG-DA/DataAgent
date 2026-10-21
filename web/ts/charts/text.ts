// ── Text / Narrative Renderer ───────────────────────────────────────────
import { getTheme } from './theme';

export function render(container: HTMLElement, content: string): void {
  const theme = getTheme();
  container.innerHTML = '';
  const div = document.createElement('div');
  div.style.cssText = `
    padding: 16px; font-size: 14px; line-height: 1.6; color: ${theme.text};
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  `;
  div.textContent = content || 'No content';
  container.appendChild(div);
}
