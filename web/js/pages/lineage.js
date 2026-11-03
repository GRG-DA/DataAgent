/* ═══════════════════════════════════════════
   Lineage Explorer — DAG Visualization & Column Lineage
   ═══════════════════════════════════════════ */

const LineagePage = {
    _dagData: null,

    async init() {},
    async destroy() { this._dagData = null; },

    async render() {
        await App.refreshTables();
        const pc = document.getElementById('page-content');
        const tables = App.state.tables || {};
        const tableNames = Array.isArray(tables) ? tables :
            (typeof tables === 'object' && tables !== null) ? Object.keys(tables) : [];

        pc.innerHTML = `
        <div class="max-w-7xl mx-auto animate-fade-in space-y-6">
            <div>
                <div class="page-title">
                    <div class="page-title-icon"><i class="fas fa-project-diagram"></i></div>
                    <div>
                        <h2>Data Lineage Explorer</h2>
                        <p class="page-title-subtitle">Visualize data transformations, provenance, and column-level lineage</p>
                    </div>
                </div>
            </div>

            <!-- Controls -->
            <div class="card">
                <div class="flex items-center gap-3 flex-wrap">
                    <div class="flex-1 min-w-[200px]">
                        <label class="text-xs font-medium text-gray-500">Select Table</label>
                        <select id="lineage-table" class="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600 mt-1">
                            <option value="">All tables (full DAG)</option>
                            ${tableNames.map(t => `<option value="${t}">${t}</option>`).join('')}
                        </select>
                    </div>
                    <button onclick="LineagePage._loadDag()" class="bg-primary-500 hover:bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium mt-4">
                        <i class="fas fa-project-diagram mr-1"></i>Load DAG
                    </button>
                    <button onclick="LineagePage._loadColumnLineage()" class="bg-gray-100 dark:bg-gray-700 border px-4 py-2 rounded-lg text-sm mt-4">
                        <i class="fas fa-columns mr-1"></i>Column Lineage
                    </button>
                </div>
            </div>

            <!-- DAG Visualization -->
            <div class="card">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Transformation Graph</h3>
                <div id="lineage-dag-container" class="overflow-auto" style="min-height:400px;">
                    <div class="text-center py-16 text-gray-400">
                        <i class="fas fa-project-diagram text-5xl mb-4 opacity-30"></i>
                        <p class="text-lg font-medium">Select a table or load the full DAG</p>
                        <p class="text-sm mt-1">Click "Load DAG" to visualize data lineage</p>
                    </div>
                </div>
            </div>

            <!-- Column Lineage -->
            <div id="column-lineage-section" class="card hidden">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Column-Level Lineage</h3>
                <div id="column-lineage-content" class="text-center py-8 text-gray-400">Select a table and click "Column Lineage"</div>
            </div>

            <!-- Lineage History -->
            <div class="card">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Transformation History</h3>
                <div id="lineage-history" class="text-center py-8 text-gray-400">Select a table to view its transformation history</div>
            </div>
        </div>`;
    },

    async _loadDag() {
        const tableName = document.getElementById('lineage-table').value;
        const container = document.getElementById('lineage-dag-container');
        container.innerHTML = '<div class="flex items-center justify-center py-16"><i class="fas fa-spinner fa-spin text-2xl text-primary-500"></i></div>';

        try {
            let data;
            if (tableName) {
                data = await API.lineageDag(tableName);
            } else {
                data = await API.lineageDagFull();
            }

            if (!data.success) {
                container.innerHTML = `<div class="text-center py-8 text-red-400"><i class="fas fa-exclamation-circle mr-2"></i>${App.escapeHtml(data.error || 'Failed to load')}</div>`;
                return;
            }

            const dag = data.dag;
            this._dagData = dag;

            if (!dag.nodes || dag.nodes.length === 0) {
                container.innerHTML = '<div class="text-center py-16 text-gray-400"><i class="fas fa-inbox text-4xl mb-4 opacity-30"></i><p>No lineage data recorded yet</p><p class="text-sm mt-1">Lineage records appear here once transformations are recorded</p></div>';
                if (tableName) this._loadHistory(tableName);
                return;
            }

            // Render DAG as SVG
            this._renderDag(container, dag);

            // Also load transformation history
            if (tableName) {
                this._loadHistory(tableName);
            }
        } catch (e) {
            container.innerHTML = `<div class="text-center py-8 text-red-400"><i class="fas fa-exclamation-circle mr-2"></i>${App.escapeHtml(e.message)}</div>`;
        }
    },

    _renderDag(container, dag) {
        const nodes = dag.nodes || [];
        const edges = dag.edges || [];

        // Simple layered DAG layout
        // 1. Compute depth for each node (longest path from root)
        const depths = {};
        const adj = {}; // adjacency: node -> children
        const inDeg = {};

        nodes.forEach(n => { depths[n.id] = 0; adj[n.id] = []; inDeg[n.id] = 0; });
        edges.forEach(e => {
            if (adj[e.from]) adj[e.from].push(e.to);
            if (inDeg[e.to] !== undefined) inDeg[e.to]++;
        });

        // BFS to assign depths
        const queue = nodes.filter(n => inDeg[n.id] === 0).map(n => n.id);
        const visited = new Set();
        while (queue.length > 0) {
            const curr = queue.shift();
            if (visited.has(curr)) continue;
            visited.add(curr);
            (adj[curr] || []).forEach(child => {
                if (depths[child] !== undefined) {
                    depths[child] = Math.max(depths[child], depths[curr] + 1);
                }
                if (inDeg[child] !== undefined) {
                    inDeg[child]--;
                    if (inDeg[child] <= 0 && !visited.has(child)) {
                        queue.push(child);
                    }
                }
            });
        }

        // Add any unvisited nodes
        nodes.forEach(n => {
            if (!visited.has(n.id)) depths[n.id] = 0;
        });

        // Group by depth
        const layers = {};
        nodes.forEach(n => {
            const d = depths[n.id] || 0;
            if (!layers[d]) layers[d] = [];
            layers[d].push(n);
        });

        const maxDepth = Math.max(...Object.keys(layers).map(Number), 0);
        const nodeW = 160, nodeH = 50, gapX = 80, gapY = 30;
        const positions = {};

        // Layout
        Object.entries(layers).forEach(([depth, layerNodes]) => {
            const d = parseInt(depth);
            const totalH = layerNodes.length * (nodeH + gapY) - gapY;
            layerNodes.forEach((n, i) => {
                positions[n.id] = {
                    x: 40 + d * (nodeW + gapX),
                    y: 20 + i * (nodeH + gapY) - totalH / 2 + (maxDepth > 0 ? totalH / 2 : 0),
                };
            });
        });

        // SVG dimensions
        let maxX = 0, maxY = 0;
        Object.values(positions).forEach(p => {
            maxX = Math.max(maxX, p.x + nodeW);
            maxY = Math.max(maxY, p.y + nodeH);
        });
        const svgW = maxX + 60;
        const svgH = maxY + 60;

        // Build SVG
        let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${svgH}" style="font-family:Inter,sans-serif;">`;

        // Defs
        svg += `<defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-primary)"/>
            </marker>
        </defs>`;

        // Draw edges
        edges.forEach(e => {
            const from = positions[e.from];
            const to = positions[e.to];
            if (!from || !to) return;
            const x1 = from.x + nodeW, y1 = from.y + nodeH / 2;
            const x2 = to.x, y2 = to.y + nodeH / 2;
            const mx = (x1 + x2) / 2;
            svg += `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" stroke="var(--color-primary)" stroke-width="2" fill="none" marker-end="url(#arrow)" opacity="0.6"/>`;
            // Edge label
            if (e.operation) {
                svg += `<text x="${mx}" y="${(y1 + y2) / 2 - 6}" font-size="9" fill="var(--color-text-muted)" text-anchor="middle">${App.escapeHtml(e.operation)}</text>`;
            }
        });

        // Draw nodes
        nodes.forEach(n => {
            const pos = positions[n.id];
            if (!pos) return;
            const isTransform = n.type === "transform";
            const fill = isTransform ? "var(--color-primary)" : "#1e293b";
            const textColor = isTransform ? "var(--color-on-primary)" : "#ffffff";
            const rx = 8;

            svg += `<rect x="${pos.x}" y="${pos.y}" width="${nodeW}" height="${nodeH}" rx="${rx}" fill="${fill}" opacity="0.9"/>`;
            // Label (truncate if long). Escape after truncation so a
            // filename like `<img onerror=...>` cannot become SVG markup.
            const rawLabel = String(n.label || n.id || "");
            const label = rawLabel.length > 20 ? rawLabel.substring(0, 18) + "…" : rawLabel;
            svg += `<text x="${pos.x + nodeW / 2}" y="${pos.y + nodeH / 2 - 4}" font-size="11" fill="${textColor}" text-anchor="middle" font-weight="600">${App.escapeHtml(label)}</text>`;
            // Type label
            const typeLabel = isTransform ? (n.operation || "transform") : "table";
            svg += `<text x="${pos.x + nodeW / 2}" y="${pos.y + nodeH / 2 + 10}" font-size="8" fill="var(--color-text-muted)" text-anchor="middle">${App.escapeHtml(typeLabel)}</text>`;
        });

        svg += '</svg>';

        container.innerHTML = `
            <div class="p-4">
                <div class="flex items-center gap-4 mb-3 text-xs text-gray-400">
                    <span><span class="inline-block w-3 h-3 rounded bg-gray-800 mr-1"></span>Table</span>
                    <span><span class="inline-block w-3 h-3 rounded bg-indigo-500 mr-1"></span>Transformation</span>
                    <span><i class="fas fa-long-arrow-alt-right text-primary-500 mr-1"></i>Data flow</span>
                    <span class="ml-auto">${nodes.length} nodes, ${edges.length} edges</span>
                </div>
                <div class="overflow-auto" style="max-height:500px;border:1px solid var(--color-border);border-radius:8px;padding:10px;">
                    ${svg}
                </div>
            </div>`;
    },

    async _loadColumnLineage() {
        const tableName = document.getElementById('lineage-table').value;
        if (!tableName) return App.toast('Select a table first', 'error');

        const section = document.getElementById('column-lineage-section');
        const content = document.getElementById('column-lineage-content');
        section.classList.remove('hidden');
        content.innerHTML = '<div class="flex items-center justify-center py-8"><i class="fas fa-spinner fa-spin text-xl text-primary-500"></i></div>';

        try {
            const data = await API.columnLineage(tableName);
            if (!data.success) {
                content.innerHTML = `<div class="text-red-400 text-sm">${App.escapeHtml(data.error || 'Failed')}</div>`;
                return;
            }

            const colLineage = data.column_lineage || {};
            const cols = Object.keys(colLineage);

            if (cols.length === 0) {
                content.innerHTML = '<div class="text-center py-8 text-gray-400"><i class="fas fa-columns mr-2"></i>No column lineage data available for this table</div>';
                return;
            }

            // Build column lineage graph
            const svg = this._renderColumnLineageGraph(colLineage, tableName);

            let html = `
                <div class="mb-3 overflow-auto" style="max-height:400px;border:1px solid var(--color-border);border-radius:8px;padding:10px;">
                    ${svg}
                </div>
                <div class="overflow-x-auto"><table class="w-full text-xs border-collapse">`;
            html += '<thead><tr class="bg-gray-100 dark:bg-gray-700/50">';
            html += '<th class="px-3 py-2 text-left font-semibold">Column</th>';
            html += '<th class="px-3 py-2 text-left font-semibold">Source Table</th>';
            html += '<th class="px-3 py-2 text-left font-semibold">Source Column</th>';
            html += '<th class="px-3 py-2 text-left font-semibold">Operation</th>';
            html += '<th class="px-3 py-2 text-left font-semibold">Timestamp</th>';
            html += '</tr></thead><tbody>';

            cols.forEach(col => {
                const sources = colLineage[col];
                sources.forEach((src, i) => {
                    html += `<tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30">`;
                    html += `<td class="px-3 py-2 font-medium ${i === 0 ? '' : 'border-t dark:border-gray-700'}">${i === 0 ? `<span class="text-primary-600">${App.escapeHtml(col)}</span>` : ''}</td>`;
                    html += `<td class="px-3 py-2">${App.escapeHtml(src.source_table || '—')}</td>`;
                    html += `<td class="px-3 py-2">${App.escapeHtml(src.source_column || '—')}</td>`;
                    html += `<td class="px-3 py-2"><span class="px-2 py-0.5 rounded text-[10px] bg-gray-100 dark:bg-gray-700">${App.escapeHtml(src.operation || '')}</span></td>`;
                    html += `<td class="px-3 py-2 text-gray-400">${src.timestamp ? new Date(src.timestamp).toLocaleDateString() : '—'}</td>`;
                    html += `</tr>`;
                });
            });

            html += '</tbody></table></div>';
            content.innerHTML = html;
        } catch (e) {
            content.innerHTML = `<div class="text-red-400 text-sm">${App.escapeHtml(e.message)}</div>`;
        }
    },

    _renderColumnLineageGraph(colLineage, targetTable) {
        const cols = Object.keys(colLineage);
        if (cols.length === 0) return '';

        // Build nodes: source columns (left) → target columns (right)
        const sourceNodes = new Map(); // "table.col" → position
        const targetNodes = new Map(); // "col" → position
        const edges = [];

        // Collect unique source tables
        const sourceTables = new Set();
        cols.forEach(col => {
            (colLineage[col] || []).forEach(src => {
                const key = `${src.source_table || '?'}.${src.source_column || '?'}`;
                sourceTables.add(src.source_table || '?');
                if (!sourceNodes.has(key)) {
                    sourceNodes.set(key, { table: src.source_table || '?', col: src.source_column || '?', key });
                }
            });
        });

        // Assign positions
        const srcArr = [...sourceNodes.values()];
        const tgtArr = cols.map(c => ({ col: c, key: c }));
        const maxRows = Math.max(srcArr.length, tgtArr.length, 1);
        const colW = 180, nodeH = 28, gapY = 12, padX = 40, padY = 30;

        // Source column layout
        const srcX = padX;
        srcArr.forEach((n, i) => {
            n.x = srcX;
            n.y = padY + i * (nodeH + gapY);
        });

        // Target column layout
        const tgtX = padX + colW * 2 + 60;
        tgtArr.forEach((n, i) => {
            n.x = tgtX;
            n.y = padY + i * (nodeH + gapY);
        });

        // Build edge list
        cols.forEach(col => {
            const tgtNode = tgtArr.find(n => n.col === col);
            (colLineage[col] || []).forEach(src => {
                const srcKey = `${src.source_table || '?'}.${src.source_column || '?'}`;
                const srcNode = srcArr.find(n => n.key === srcKey);
                if (srcNode && tgtNode) {
                    edges.push({ from: srcNode, to: tgtNode, op: src.operation || '' });
                }
            });
        });

        const svgW = tgtX + colW + padX;
        const svgH = maxRows * (nodeH + gapY) + padY * 2;

        let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${svgH}" style="font-family:Inter,sans-serif;">`;

        // Defs
        svg += `<defs>
            <marker id="col-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-text-muted)"/>
            </marker>
        </defs>`;

        // Draw edges
        edges.forEach(e => {
            const x1 = e.from.x + colW, y1 = e.from.y + nodeH / 2;
            const x2 = e.to.x, y2 = e.to.y + nodeH / 2;
            const mx = (x1 + x2) / 2;
            svg += `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" stroke="var(--color-text-muted)" stroke-width="1.5" fill="none" marker-end="url(#col-arrow)" opacity="0.6"/>`;
            // Operation label on edge
            if (e.op) {
                svg += `<text x="${mx}" y="${(y1 + y2) / 2 - 4}" font-size="8" fill="var(--color-text-muted)" text-anchor="middle">${App.escapeHtml(e.op)}</text>`;
            }
        });

        // Draw source column nodes
        srcArr.forEach(n => {
            svg += `<rect x="${n.x}" y="${n.y}" width="${colW}" height="${nodeH}" rx="6" fill="var(--color-primary-light)" stroke="var(--color-primary)" stroke-width="1"/>`;
            const label = `${n.table}.${n.col}`;
            svg += `<text x="${n.x + 8}" y="${n.y + nodeH / 2 + 3}" font-size="10" fill="var(--color-primary-active)">${App.escapeHtml(label.length > 22 ? label.slice(0, 20) + '…' : label)}</text>`;
        });

        // Draw target column nodes
        tgtArr.forEach(n => {
            svg += `<rect x="${n.x}" y="${n.y}" width="${colW}" height="${nodeH}" rx="6" fill="var(--color-warning-bg)" stroke="var(--color-warning)" stroke-width="1"/>`;
            svg += `<text x="${n.x + 8}" y="${n.y + nodeH / 2 + 3}" font-size="10" fill="var(--color-warning)">${App.escapeHtml(n.col)}</text>`;
        });

        // Labels
        svg += `<text x="${srcX + colW / 2}" y="${padY - 10}" font-size="10" fill="var(--color-text-muted)" text-anchor="middle" font-weight="600">Source Columns</text>`;
        svg += `<text x="${tgtX + colW / 2}" y="${padY - 10}" font-size="10" fill="var(--color-text-muted)" text-anchor="middle" font-weight="600">Target Columns</text>`;

        svg += '</svg>';
        return svg;
    },

    async _loadHistory(tableName) {
        try {
            const data = await API.lineage(tableName);
            const el = document.getElementById('lineage-history');
            if (!el) return;

            const records = data.lineage || [];
            if (records.length === 0) {
                el.innerHTML = `<div class="text-center py-4 text-gray-400">No lineage records for "${App.escapeHtml(tableName)}"</div>`;
                return;
            }

            el.innerHTML = records.map(r => `
                <div class="flex items-start gap-3 p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg mb-2">
                    <div class="flex-shrink-0 w-8 h-8 rounded-full bg-primary-100 dark:bg-primary-900/30 flex items-center justify-center">
                        <i class="fas fa-cog text-primary-500 text-xs"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-medium text-sm">${App.escapeHtml(r.operation)}</span>
                            <span class="text-xs text-gray-400">${r.timestamp ? new Date(r.timestamp).toLocaleString() : ''}</span>
                        </div>
                        ${r.description ? `<div class="text-xs text-gray-500 mt-0.5">${App.escapeHtml(r.description)}</div>` : ''}
                        <div class="text-[10px] text-gray-400 mt-1">
                            ${r.inputs?.length ? `Inputs: ${App.escapeHtml(r.inputs.join(', '))}` : ''}
                            ${r.outputs?.length ? ` → Outputs: ${App.escapeHtml(r.outputs.join(', '))}` : ''}
                        </div>
                    </div>
                </div>
            `).join('');
        } catch (_) {}
    },
};

window.LineagePage = LineagePage;
(window._PAGES = window._PAGES || {})["#lineage"] = LineagePage;
