// ── SQL Filter Engine ────────────────────────────────────────────────────
// Preview-only WHERE-clause builder + SQL injection for the Power BI Studio.
// Exact port of window.BIFilters from sqlFilters.js.

import { FilterMap, FilterValue, SlicerDef } from './types';

/**
 * Quote a value as a SQL string literal.
 */
export function literal(v: any): string {
  const s = String(v == null ? '' : v).replace(/'/g, "''");
  return "'" + s + "'";
}

/**
 * Best-effort guess of a widget's dimension column: the column aliased
 * `label`, or the GROUP BY column. Mirrors the exporter's dimension lookup.
 */
export function dimFromSql(sql: string): string {
  if (!sql) return '';
  let m = sql.match(/"([^"]+)"\s+AS\s+label\b/i) ||
          sql.match(/\b([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+label\b/i);
  if (m) return m[1];
  m = sql.match(/\bGROUP\s+BY\s+"([^"]+)"/i);
  if (m) return m[1];
  m = sql.match(/\bGROUP\s+BY\s+([A-Za-z_][A-Za-z0-9_]*)/i);
  if (m) return m[1];
  return '';
}

/**
 * Build the combined WHERE text for a widget whose SQL reads from `table`.
 *
 * @param filters  {slicerField: [values] | [{min, max}]}
 * @param slicers  the dashboard's layout.slicers array
 * @param table    the resolved widget source table ("" = unknown)
 */
export function clausesFor(
  filters: FilterMap,
  slicers: SlicerDef[],
  table: string
): string {
  if (!filters || !slicers || !slicers.length) return '';
  const wTable = String(table || '').toLowerCase();
  const parts: string[] = [];

  for (const sl of slicers) {
    if (!sl || !sl.field) continue;
    // Only filter widgets that read from the slicer's own table
    const slTable = String(sl.table || '').toLowerCase();
    if (slTable && wTable && slTable !== wTable) continue;

    const sel = filters[sl.field];
    if (!sel || (Array.isArray(sel) && sel.length === 0)) continue;

    const fq = '"' + sl.field + '"';
    const kind = sl.kind || 'select';

    if (kind === 'date' || kind === 'range') {
      const rng: any = Array.isArray(sel) ? sel[0] : sel;
      const obj = (rng && typeof rng === 'object') ? rng : {};
      const mn = obj.min;
      const mx = obj.max;
      if (mn !== undefined && mn !== null && mn !== '') {
        parts.push(fq + ' >= ' + literal(mn));
      }
      if (mx !== undefined && mx !== null && mx !== '') {
        parts.push(fq + ' <= ' + literal(mx));
      }
    } else {
      const vals = (sel as string[]).map(literal).join(',');
      parts.push(fq + ' IN (' + vals + ')');
    }
  }

  return parts.join(' AND ');
}

/**
 * Splice `WHERE (<where>)` into a generated widget SQL right after the first
 * top-level `FROM <table>`. Handles a pre-existing WHERE (merges as AND),
 * GROUP BY / ORDER BY / LIMIT, and bare SELECT statements. If the SQL has no
 * FROM (e.g. a scalar) it is returned untouched.
 */
export function injectWhere(sql: string, where: string): string {
  if (!where) return sql;
  if (!sql) return sql;

  const from = sql.match(/\bFROM\s+(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))/i);
  if (!from) return sql;
  const afterFrom = from.index! + from[0].length;
  const rest = sql.slice(afterFrom);

  // Existing WHERE -> merge as AND
  const ew = rest.match(/^\s*WHERE\b/i);
  if (ew) {
    const insEnd = afterFrom + ew.index! + ew[0].length;
    return sql.slice(0, insEnd) + ' (' + where + ') AND' + rest.slice(ew[0].length);
  }

  // Insert before the next clause keyword
  const nxt = rest.match(/\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|OFFSET)\b/i);
  if (nxt) {
    const pos = afterFrom + nxt.index!;
    return sql.slice(0, pos) + 'WHERE (' + where + ') ' + sql.slice(pos);
  }

  return sql.slice(0, afterFrom) + ' WHERE (' + where + ') ' + rest;
}
