// ── Chart Theme — Power BI Color Palette ─────────────────────────────────
// Matches the official Power BI color scheme: purple (#7B61FF),
// teal (#00D4AA), coral accent (#FF6B6B), light gray background.

const _isDark = () => document.documentElement.classList.contains('dark') ||
  document.documentElement.getAttribute('data-theme') === 'dark';

export interface ChartTheme {
  name?: string;
  primary: string;
  secondary: string;
  accent: string;
  palette: string[];
  gradient: [string, string];
  background: string;
  cardBg: string;
  text: string;
  textSecondary: string;
  gridLine: string;
  axisColor: string;
  backgroundGradient?: [string, string];
}

// The default light/dark themes are driven by the CSS custom properties in
// `web/css/styles.css` (--chart-1..8, --color-primary, ...). Keeping the
// series ORDER identical in both themes means a chart series keeps its colour
// identity when the user flips the theme, instead of silently re-sorting.
//
// The named presets below (Midnight, Ocean, Sunset, ...) are opt-in chart
// palettes and intentionally keep their own colours.

const _cssVar = (name: string, fallback: string): string => {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return v && v.trim() ? v.trim() : fallback;
  } catch (e) {
    return fallback;
  }
};

const LIGHT_THEME: ChartTheme = {
  name: 'Light',
  primary: '#2563eb',
  secondary: '#0d9488',
  accent: '#b45309',
  palette: [
    '#2563eb', '#0d9488', '#b45309', '#7c3aed',
    '#be123c', '#0369a1', '#4d7c0f', '#64748b',
  ],
  gradient: ['#2563eb', '#0d9488'],
  background: '#f1f5f9',
  cardBg: '#ffffff',
  text: '#0f172a',
  textSecondary: '#475569',
  gridLine: '#e2e8f0',
  axisColor: '#cbd5e1',
};

const DARK_THEME: ChartTheme = {
  name: 'Dark',
  primary: '#60a5fa',
  secondary: '#2dd4bf',
  accent: '#fbbf24',
  // SAME series order as LIGHT_THEME — only the lightness changes.
  palette: [
    '#60a5fa', '#2dd4bf', '#fbbf24', '#a78bfa',
    '#fb7185', '#38bdf8', '#a3e635', '#94a3b8',
  ],
  gradient: ['#60a5fa', '#2dd4bf'],
  background: '#0b1220',
  cardBg: '#131c2e',
  text: '#e8eef7',
  textSecondary: '#a3b1c6',
  gridLine: '#24304a',
  axisColor: '#334155',
};

const MIDNIGHT_THEME: ChartTheme = {
  name: 'Midnight',
  primary: '#8B5CF6',
  secondary: '#22D3EE',
  accent: '#F472B6',
  palette: [
    '#8B5CF6', '#22D3EE', '#F472B6', '#60A5FA', '#34D399',
    '#FBBF24', '#A78BFA', '#F87171', '#2DD4BF', '#C084FC',
  ],
  gradient: ['#8B5CF6', '#22D3EE'],
  background: '#0B0F19',
  cardBg: '#141B2D',
  text: '#E2E8F0',
  textSecondary: '#7C89A3',
  gridLine: '#223049',
  axisColor: '#2E3D59',
  backgroundGradient: ['#0B0F19', '#1E1B4B'],
};

const OCEAN_THEME: ChartTheme = {
  name: 'Ocean',
  primary: '#38BDF8',
  secondary: '#2DD4BF',
  accent: '#F59E0B',
  palette: [
    '#38BDF8', '#2DD4BF', '#60A5FA', '#22D3EE', '#F59E0B',
    '#F87171', '#34D399', '#A78BFA', '#38BDF8', '#E0F2FE',
  ],
  gradient: ['#38BDF8', '#2DD4BF'],
  background: '#042C40',
  cardBg: '#0B3B54',
  text: '#E6F7FF',
  textSecondary: '#8FC3D9',
  gridLine: '#155E82',
  axisColor: '#21708F',
  backgroundGradient: ['#042C40', '#0E5A83'],
};

const SUNSET_THEME: ChartTheme = {
  name: 'Sunset',
  primary: '#FB7185',
  secondary: '#FFB347',
  accent: '#FDE047',
  palette: [
    '#FB7185', '#FFB347', '#FDE047', '#FB923C', '#F87171',
    '#F472B6', '#FDBA74', '#E879F9', '#FCA5A5', '#FDBA74',
  ],
  gradient: ['#FB7185', '#FFB347'],
  background: '#2C0D22',
  cardBg: '#421531',
  text: '#FFF0F7',
  textSecondary: '#D9A3B8',
  gridLine: '#6C2746',
  axisColor: '#87325A',
  backgroundGradient: ['#2C0D22', '#7C2D12'],
};

const FOREST_THEME: ChartTheme = {
  name: 'Forest',
  primary: '#34D399',
  secondary: '#A3E635',
  accent: '#FBBF24',
  palette: [
    '#34D399', '#A3E635', '#FBBF24', '#10B981', '#4ADE80',
    '#F59E0B', '#2DD4BF', '#86EFAC', '#D9F99D', '#34D399',
  ],
  gradient: ['#34D399', '#A3E635'],
  background: '#0A1F17',
  cardBg: '#14352A',
  text: '#E9F7F0',
  textSecondary: '#86B5A2',
  gridLine: '#23463A',
  axisColor: '#2F5A4B',
  backgroundGradient: ['#0A1F17', '#14532D'],
};

const GRAPE_THEME: ChartTheme = {
  name: 'Grape',
  primary: '#A78BFA',
  secondary: '#F0ABFC',
  accent: '#FDE047',
  palette: [
    '#A78BFA', '#F0ABFC', '#818CF8', '#E879F9', '#C084FC',
    '#FBCFE8', '#7C3AED', '#F472B6', '#A5B4FC', '#E9D5FF',
  ],
  gradient: ['#A78BFA', '#F0ABFC'],
  background: '#1C0F35',
  cardBg: '#2A1750',
  text: '#F1E9FF',
  textSecondary: '#B49BE0',
  gridLine: '#4A2E85',
  axisColor: '#5D3BA6',
  backgroundGradient: ['#1C0F35', '#4C1D95'],
};

const SLATE_THEME: ChartTheme = {
  name: 'Slate',
  primary: '#60A5FA',
  secondary: '#34D399',
  accent: '#F97316',
  palette: [
    '#60A5FA', '#34D399', '#F97316', '#818CF8', '#2DD4BF',
    '#FBBF24', '#F87171', '#94A3B8', '#38BDF8', '#4ADE80',
  ],
  gradient: ['#60A5FA', '#34D399'],
  background: '#0F172A',
  cardBg: '#1E293B',
  text: '#E2E8F0',
  textSecondary: '#94A3B8',
  gridLine: '#334155',
  axisColor: '#475569',
};

const CANDY_THEME: ChartTheme = {
  name: 'Candy',
  primary: '#F472B6',
  secondary: '#22D3EE',
  accent: '#FBBF24',
  palette: [
    '#F472B6', '#22D3EE', '#FBBF24', '#FB7185', '#A78BFA',
    '#34D399', '#FCA5A5', '#7DD3FC', '#F9A8D4', '#FDE68A',
  ],
  gradient: ['#F472B6', '#22D3EE'],
  background: '#FFF7F9',
  cardBg: '#FFFFFF',
  text: '#4A3A46',
  textSecondary: '#A78B9E',
  gridLine: '#F0D5E2',
  axisColor: '#E5C4D2',
};

const TERMINAL_THEME: ChartTheme = {
  name: 'Terminal',
  primary: '#00FF66',
  secondary: '#00D4AA',
  accent: '#FF5555',
  palette: [
    '#00FF66', '#00D4AA', '#FF5555', '#FFB86C', '#8BE9FD',
    '#FF79C6', '#50FA7B', '#F1FA8C', '#BD93F9', '#6272A4',
  ],
  gradient: ['#00FF66', '#00D4AA'],
  background: '#0C0C0C',
  cardBg: '#151515',
  text: '#33FF33',
  textSecondary: '#2E8B57',
  gridLine: '#262626',
  axisColor: '#333333',
};

const SOLARIZED_THEME: ChartTheme = {
  name: 'Solarized',
  primary: '#268BD2',
  secondary: '#2AA198',
  accent: '#DC322F',
  palette: [
    '#268BD2', '#2AA198', '#DC322F', '#B58900', '#859900',
    '#6C71C4', '#CB4B16', '#D33682', '#2AA198', '#93A1A1',
  ],
  gradient: ['#268BD2', '#2AA198'],
  background: '#002B36',
  cardBg: '#073642',
  text: '#93A1A1',
  textSecondary: '#586E75',
  gridLine: '#20424B',
  axisColor: '#40555D',
};

const AURORA_THEME: ChartTheme = {
  name: 'Aurora',
  primary: '#6366F1',
  secondary: '#10B981',
  accent: '#F59E0B',
  palette: [
    '#6366F1', '#10B981', '#F59E0B', '#22D3EE', '#A78BFA',
    '#34D399', '#FB7185', '#FDE047', '#60A5FA', '#EC4899',
  ],
  gradient: ['#6366F1', '#10B981'],
  background: '#0D1030',
  cardBg: '#1B2054',
  text: '#E0E7FF',
  textSecondary: '#8FA3BF',
  gridLine: '#30387A',
  axisColor: '#3E4A99',
  backgroundGradient: ['#0D1030', '#312E81'],
};

const COFFEE_THEME: ChartTheme = {
  name: 'Coffee',
  primary: '#D9A05B',
  secondary: '#B87333',
  accent: '#F97316',
  palette: [
    '#D9A05B', '#B87333', '#F97316', '#E0A96D', '#A0522D',
    '#F59E0B', '#C0823F', '#E7B477', '#8B5A2B', '#F97316',
  ],
  gradient: ['#D9A05B', '#B87333'],
  background: '#221410',
  cardBg: '#3A2217',
  text: '#F3E5D8',
  textSecondary: '#C19A82',
  gridLine: '#5A3323',
  axisColor: '#6E3F2C',
};

const ROSE_THEME: ChartTheme = {
  name: 'Rose',
  primary: '#F43F5E',
  secondary: '#FBBF24',
  accent: '#A855F7',
  palette: [
    '#F43F5E', '#FBBF24', '#A855F7', '#FB7185', '#F472B6',
    '#E879F9', '#FDA4AF', '#F9A8D4', '#F87171', '#C084FC',
  ],
  gradient: ['#F43F5E', '#A855F7'],
  background: '#2A0B1B',
  cardBg: '#3F1530',
  text: '#FFE4EF',
  textSecondary: '#D18BAA',
  gridLine: '#6A2547',
  axisColor: '#85325B',
  backgroundGradient: ['#2A0B1B', '#9F1239'],
};

const NEON_THEME: ChartTheme = {
  name: 'Neon',
  primary: '#00FFFF',
  secondary: '#FF00FF',
  accent: '#39FF14',
  palette: [
    '#00FFFF', '#FF00FF', '#39FF14', '#FFFF00', '#FF6B00',
    '#00FF9D', '#FF3366', '#7DF9FF', '#B026FF', '#FFE600',
  ],
  gradient: ['#00FFFF', '#FF00FF'],
  background: '#050510',
  cardBg: '#0E0E2A',
  text: '#E0FFFF',
  textSecondary: '#7DF9FF',
  gridLine: '#1E1E4A',
  axisColor: '#2F2F70',
  backgroundGradient: ['#050510', '#1E1B4B'],
};

const EMERALD_THEME: ChartTheme = {
  name: 'Emerald',
  primary: '#10B981',
  secondary: '#34D399',
  accent: '#FBBF24',
  palette: [
    '#10B981', '#34D399', '#FBBF24', '#059669', '#6EE7B7',
    '#F59E0B', '#2DD4BF', '#A7F3D0', '#D1FAE5', '#10B981',
  ],
  gradient: ['#10B981', '#34D399'],
  background: '#06281E',
  cardBg: '#0B3B2E',
  text: '#E7F6F0',
  textSecondary: '#7FB8A3',
  gridLine: '#1B4A3A',
  axisColor: '#25604C',
  backgroundGradient: ['#06281E', '#064E3B'],
};

const CRIMSON_THEME: ChartTheme = {
  name: 'Crimson',
  primary: '#F43F5E',
  secondary: '#FB7185',
  accent: '#FBBF24',
  palette: [
    '#F43F5E', '#FB7185', '#FBBF24', '#E11D48', '#FDA4AF',
    '#F97316', '#F87171', '#FECDD3', '#FCA5A5', '#F43F5E',
  ],
  gradient: ['#F43F5E', '#FB7185'],
  background: '#2B0A12',
  cardBg: '#3F1420',
  text: '#FFE9EE',
  textSecondary: '#D18A9B',
  gridLine: '#5C2433',
  axisColor: '#70303F',
  backgroundGradient: ['#2B0A12', '#881337'],
};

const GOLD_THEME: ChartTheme = {
  name: 'Gold',
  primary: '#F59E0B',
  secondary: '#FBBF24',
  accent: '#F43F5E',
  palette: [
    '#F59E0B', '#FBBF24', '#F43F5E', '#D97706', '#FDE68A',
    '#FB923C', '#FCD34D', '#FEF3C7', '#FCA5A5', '#F59E0B',
  ],
  gradient: ['#F59E0B', '#FBBF24'],
  background: '#2A1A05',
  cardBg: '#3E2A0E',
  text: '#FFF7E6',
  textSecondary: '#D4B483',
  gridLine: '#5C4520',
  axisColor: '#70562A',
  backgroundGradient: ['#2A1A05', '#78350F'],
};

const ICE_THEME: ChartTheme = {
  name: 'Ice',
  primary: '#0EA5E9',
  secondary: '#22D3EE',
  accent: '#6366F1',
  palette: [
    '#0EA5E9', '#22D3EE', '#6366F1', '#38BDF8', '#818CF8',
    '#06B6D4', '#60A5FA', '#A5F3FC', '#C7D2FE', '#0EA5E9',
  ],
  gradient: ['#0EA5E9', '#22D3EE'],
  background: '#F0F9FF',
  cardBg: '#FFFFFF',
  text: '#0C4A6E',
  textSecondary: '#64748B',
  gridLine: '#E0F2FE',
  axisColor: '#BAE6FD',
};

const GRAPHITE_THEME: ChartTheme = {
  name: 'Graphite',
  primary: '#94A3B8',
  secondary: '#64748B',
  accent: '#38BDF8',
  palette: [
    '#94A3B8', '#64748B', '#38BDF8', '#CBD5E1', '#475569',
    '#22D3EE', '#E2E8F0', '#818CF8', '#64748B', '#94A3B8',
  ],
  gradient: ['#94A3B8', '#64748B'],
  background: '#0F1115',
  cardBg: '#1A1D23',
  text: '#E5E7EB',
  textSecondary: '#8B919C',
  gridLine: '#2A2E36',
  axisColor: '#3A3F49',
  backgroundGradient: ['#0F1115', '#1F2937'],
};

const LAVENDER_THEME: ChartTheme = {
  name: 'Lavender',
  primary: '#8B5CF6',
  secondary: '#A78BFA',
  accent: '#EC4899',
  palette: [
    '#8B5CF6', '#A78BFA', '#EC4899', '#6D28D9', '#C4B5FD',
    '#F472B6', '#818CF8', '#EDE9FE', '#FBCFE8', '#8B5CF6',
  ],
  gradient: ['#8B5CF6', '#A78BFA'],
  background: '#FAF7FF',
  cardBg: '#FFFFFF',
  text: '#3B2F63',
  textSecondary: '#7C6FA8',
  gridLine: '#EDE9FE',
  axisColor: '#DDD6FE',
};

const PEACH_THEME: ChartTheme = {
  name: 'Peach',
  primary: '#F97316',
  secondary: '#FB923C',
  accent: '#F43F5E',
  palette: [
    '#F97316', '#FB923C', '#F43F5E', '#EA580C', '#FDBA74',
    '#F87171', '#FBBF24', '#FFEDD5', '#FECACA', '#F97316',
  ],
  gradient: ['#F97316', '#FB923C'],
  background: '#FFF7ED',
  cardBg: '#FFFFFF',
  text: '#431407',
  textSecondary: '#9A6B4F',
  gridLine: '#FFEDD5',
  axisColor: '#FED7AA',
};

const CYBER_THEME: ChartTheme = {
  name: 'Cyber',
  primary: '#22D3EE',
  secondary: '#A855F7',
  accent: '#F0ABFC',
  palette: [
    '#22D3EE', '#A855F7', '#F0ABFC', '#06B6D4', '#C084FC',
    '#34D399', '#F472B6', '#67E8F9', '#E9D5FF', '#22D3EE',
  ],
  gradient: ['#22D3EE', '#A855F7'],
  background: '#0A0A1F',
  cardBg: '#141433',
  text: '#E0F2FE',
  textSecondary: '#8B8BC9',
  gridLine: '#26264F',
  axisColor: '#34346B',
  backgroundGradient: ['#0A0A1F', '#2E1065'],
};

const PAPER_THEME: ChartTheme = {
  name: 'Paper',
  primary: '#334155',
  secondary: '#64748B',
  accent: '#0EA5E9',
  palette: [
    '#334155', '#64748B', '#0EA5E9', '#94A3B8', '#475569',
    '#38BDF8', '#CBD5E1', '#E2E8F0', '#64748B', '#334155',
  ],
  gradient: ['#334155', '#64748B'],
  background: '#FAFAFA',
  cardBg: '#FFFFFF',
  text: '#1E293B',
  textSecondary: '#64748B',
  gridLine: '#E5E7EB',
  axisColor: '#D1D5DB',
};

const MINT_THEME: ChartTheme = {
  name: 'Mint',
  primary: '#10B981',
  secondary: '#34D399',
  accent: '#0EA5E9',
  palette: [
    '#10B981', '#34D399', '#0EA5E9', '#059669', '#6EE7B7',
    '#38BDF8', '#F59E0B', '#D1FAE5', '#A7F3D0', '#10B981',
  ],
  gradient: ['#10B981', '#34D399'],
  background: '#F0FDF9',
  cardBg: '#FFFFFF',
  text: '#134E4A',
  textSecondary: '#5B8A82',
  gridLine: '#CCFBF1',
  axisColor: '#99F6E4',
};

export const THEMES: Record<string, ChartTheme> = {
  light: LIGHT_THEME,
  dark: DARK_THEME,
  midnight: MIDNIGHT_THEME,
  ocean: OCEAN_THEME,
  sunset: SUNSET_THEME,
  forest: FOREST_THEME,
  grape: GRAPE_THEME,
  slate: SLATE_THEME,
  candy: CANDY_THEME,
  terminal: TERMINAL_THEME,
  solarized: SOLARIZED_THEME,
  aurora: AURORA_THEME,
  coffee: COFFEE_THEME,
  rose: ROSE_THEME,
  neon: NEON_THEME,
  emerald: EMERALD_THEME,
  crimson: CRIMSON_THEME,
  gold: GOLD_THEME,
  ice: ICE_THEME,
  graphite: GRAPHITE_THEME,
  lavender: LAVENDER_THEME,
  peach: PEACH_THEME,
  cyber: CYBER_THEME,
  paper: PAPER_THEME,
  mint: MINT_THEME,
};

// Expose presets (and active override) to the plain-JS dashboard builder.
(window as any).BIDashboardThemes = THEMES;

export function getTheme(): ChartTheme {
  const o = (window as any).BIDashboardTheme as ChartTheme | undefined;
  if (o) return o;
  if (!_isDark()) return LIGHT_THEME;

  // Dark theme: take the surface/axis colours from the live CSS tokens so
  // the chart always matches the app chrome, but keep the series palette
  // order identical to the light theme (see DARK_THEME above).
  return {
    ...DARK_THEME,
    background: _cssVar('--color-bg', DARK_THEME.background),
    cardBg: _cssVar('--color-surface', DARK_THEME.cardBg),
    text: _cssVar('--color-text', DARK_THEME.text),
    textSecondary: _cssVar('--color-text-secondary', DARK_THEME.textSecondary),
    gridLine: _cssVar('--color-border', DARK_THEME.gridLine),
    axisColor: _cssVar('--color-border-strong', DARK_THEME.axisColor),
  };
}

export function themeIsLight(t: ChartTheme): boolean {
  const c = String(t.cardBg || '#ffffff').replace('#', '');
  const full = c.length === 3
    ? c.split('').map((ch) => ch + ch).join('')
    : c;
  const r = parseInt(full.substring(0, 2), 16) || 0;
  const g = parseInt(full.substring(2, 4), 16) || 0;
  const b = parseInt(full.substring(4, 6), 16) || 0;
  return (0.299 * r + 0.587 * g + 0.114 * b) > 140;
}

export function palette(n: number): string[] {
  const t = getTheme();
  const result: string[] = [];
  for (let i = 0; i < n; i++) {
    result.push(t.palette[i % t.palette.length]);
  }
  return result;
}

export function kpiGradient(idx: number): string {
  const t = getTheme();
  const pal: string[] = (t.palette && t.palette.length) ? t.palette : [t.primary, t.accent || t.secondary || t.primary];
  const i = Math.abs(idx) % pal.length;
  return `linear-gradient(135deg, ${pal[i]}, ${pal[(i + 3) % pal.length]})`;
}

// ── SVG Gradient Definitions ────────────────────────────────────────────

export function addGradientDef(svg: any, id: string, color1: string, color2: string, vertical?: boolean): void {
  const defs = svg.append('defs');
  const grad = defs.append('linearGradient')
    .attr('id', id)
    .attr('x1', '0%').attr('y1', vertical ? '100%' : '0%')
    .attr('x2', '0%').attr('y2', vertical ? '0%' : '0%');
  if (!vertical) {
    grad.attr('x1', '0%').attr('y1', '0%').attr('x2', '100%').attr('y2', '0%');
  }
  grad.append('stop').attr('offset', '0%').attr('stop-color', color1);
  grad.append('stop').attr('offset', '100%').attr('stop-color', color2);
}

export function addAreaGradient(svg: any, id: string, color: string): void {
  const defs = svg.append('defs');
  const grad = defs.append('linearGradient')
    .attr('id', id)
    .attr('x1', '0%').attr('y1', '0%')
    .attr('x2', '0%').attr('y2', '100%');
  grad.append('stop').attr('offset', '0%').attr('stop-color', color).attr('stop-opacity', 0.4);
  grad.append('stop').attr('offset', '100%').attr('stop-color', color).attr('stop-opacity', 0.02);
}