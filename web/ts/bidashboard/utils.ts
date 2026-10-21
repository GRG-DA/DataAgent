// ── Utility Functions ────────────────────────────────────────────────────
// Column heuristics, SQL helpers, formatting — mirrors the IIFE helpers
// in the old bidashboard.js.

import { TemplateContext, WidgetConfig } from './types';

// ── Table / Column Heuristics ───────────────────────────────────────────

const SYSTEM_TABLES = new Set([
  'sqlite_sequence', 'slicers', 'settings', 'system_tables',
  'duckdb_tables', 'duckdb_columns', 'information_schema',
]);

export function tableNames(): string[] {
  const tables = (window as any).App?.state?.tables;
  if (!tables) return [];
  return Object.keys(tables);
}

export function isSystemTable(table: string): boolean {
  return SYSTEM_TABLES.has(table.toLowerCase());
}

export function columnNames(table: string): string[] {
  const tables = (window as any).App?.state?.tables;
  if (!tables || !tables[table]) return [];
  return tables[table].columns || tables[table].map((c: any) => c.name) || [];
}

export function columnType(table: string, col: string): string {
  const tables = (window as any).App?.state?.tables;
  if (!tables || !tables[table]) return '';
  const cols = tables[table].columns || tables[table];
  if (Array.isArray(cols)) {
    const found = cols.find((c: any) => c.name === col || c === col);
    return found?.type || found?.data_type || '';
  }
  return '';
}

export function isNumericCol(table: string, col: string): boolean {
  const t = columnType(table, col).toLowerCase();
  if (/int|float|double|decimal|numeric|real|number|bigint|smallint/i.test(t)) return true;
  const name = col.toLowerCase();
  return /revenue|sales|amount|profit|income|value|cost|price|margin|gmv|qty|quantity|units|count|score|rating|salary|hours|expense|balance|rate|total|sum|roi|ctr|conversion|volume|target|budget|gross|net|ltv/i.test(name);
}

export function isDateCol(table: string, col: string): boolean {
  const t = columnType(table, col).toLowerCase();
  if (/date|time|timestamp|datetime/i.test(t)) return true;
  const name = col.toLowerCase();
  return /date|time|year|month|day|week|quarter|created|updated|modified/i.test(name);
}

export function isCategoricalCol(table: string, col: string): boolean {
  return !isNumericCol(table, col) && !isDateCol(table, col);
}

export function bestAvailableTable(preferred: string): string {
  const names = tableNames().filter(n => !isSystemTable(n));
  if (!names.length) return '';
  if (preferred && names.includes(preferred)) return preferred;
  // Fuzzy match
  const lower = preferred.toLowerCase();
  const match = names.find(n => n.toLowerCase().includes(lower) || lower.includes(n.toLowerCase()));
  return match || names[0];
}

// ── HTML Escaping ───────────────────────────────────────────────────────

export function esc(s: any): string {
  const div = document.createElement('div');
  div.textContent = String(s == null ? '' : s);
  return div.innerHTML;
}

// ── SQL Helpers ─────────────────────────────────────────────────────────

export function tableInSql(sql: string): string {
  const m = sql.match(/\bFROM\s+(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))/i);
  return m ? (m[1] || m[2]) : '';
}

export function rewriteSqlTables(sql: string, preferred: string): string {
  if (!preferred) return sql;
  return sql.replace(
    /\b(FROM|JOIN)\s+(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))/gi,
    (match, keyword, quoted, unquoted) => {
      const current = quoted || unquoted;
      // Only rewrite if the current table doesn't exist
      const names = tableNames();
      if (names.includes(current)) return match;
      return `${keyword} "${preferred}"`;
    }
  );
}

// ── Number Formatting ───────────────────────────────────────────────────

export function formatNumber(v: any, format?: string): string {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (isNaN(n)) return String(v);

  if (format === '%') {
    return (n * 100).toFixed(1) + '%';
  }
  if (format === '$') {
    return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }
  if (format === 'currency') {
    return n.toLocaleString(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  // Auto-format large numbers
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// ── ID Generation ───────────────────────────────────────────────────────

let _widgetSeq = 0;
export function genWidgetId(): string {
  _widgetSeq++;
  return 'w' + Date.now().toString(36) + '_' + _widgetSeq;
}

// ── Build TemplateContext from App.state ─────────────────────────────────

export function buildTemplateContext(): TemplateContext {
  return {
    tableNames: () => tableNames().filter(n => !isSystemTable(n)),
    columns: (table: string) => columnNames(table),
    isNumeric: (table: string, col: string) => isNumericCol(table, col),
    isDate: (table: string, col: string) => isDateCol(table, col),
    isCategorical: (table: string, col: string) => isCategoricalCol(table, col),
  };
}

// ── Aggregate Types ─────────────────────────────────────────────────────

export type AggregateType = 'SUM' | 'AVG' | 'MAX' | 'MIN' | 'COUNT';

export const AGGREGATE_OPTIONS: { value: AggregateType; label: string }[] = [
  { value: 'SUM', label: 'Sum' },
  { value: 'AVG', label: 'Average' },
  { value: 'MAX', label: 'Maximum' },
  { value: 'MIN', label: 'Minimum' },
  { value: 'COUNT', label: 'Count' },
];

// ── KPI Variants ────────────────────────────────────────────────────────

export interface KpiVariant {
  agg: AggregateType;
  prefix: string;
}

export const KPI_VARIANTS: KpiVariant[] = [
  { agg: 'SUM', prefix: 'Total ' },
  { agg: 'AVG', prefix: 'Avg ' },
  { agg: 'MAX', prefix: 'Max ' },
  { agg: 'COUNT', prefix: 'Records' },
];
