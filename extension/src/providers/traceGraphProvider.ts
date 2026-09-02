// ---------------------------------------------------------------------------
// Sdlicit — Trace Graph Provider (Editor Panel)
// ---------------------------------------------------------------------------
// Interactive hierarchical traceability graph:
//   - Backend-driven: fetches the DAG from GET /expansion/traceability-graph
//   - Falls back to local DataService when backend is unavailable
//   - Bottom-up tree layout: SOW (root) at bottom, derived artifacts above
//   - Hierarchical layers computed via topological distance from roots
//   - Smooth bezier edge connections with arrowheads
//   - Color-coded nodes by artifact type, edges by relationship type
//   - Interactive: pan (drag), zoom (scroll), click to open, hover for details
//   - Stale/superseded nodes visually distinguished
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';
import { SdlicitClient, TraceGraphData, TraceCoverageData, ArtifactCoverage } from '../services/sdlicitClient';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

const TYPE_COLORS: Record<string, string> = {
    sow: '#4191e0',
    srs: '#4caf50',
    requirement: '#4caf50',
    adr: '#e8a838',
    decision: '#e8a838',
    bdd: '#e07041',
    scenario: '#e07041',
    personas: '#9c6ade',
    stories: '#26a69a',
    unknown: '#888888',
};
const TYPE_LABELS: Record<string, string> = {
    sow: 'SOW', srs: 'SRS', requirement: 'REQ', adr: 'ADR', decision: 'ADR',
    bdd: 'BDD', scenario: 'SCN', personas: 'PER', stories: 'UST', unknown: '?',
};

// Priority order for layer assignment (lower = root)
const TYPE_PRIORITY: Record<string, number> = {
    sow: 0, srs: 1, requirement: 1, personas: 2, stories: 2, adr: 3, decision: 3, bdd: 4, scenario: 4,
};

interface GraphNode {
    id: string;
    type: string;
    title: string;
    status: string;
    isStale: boolean;
    coverage?: ArtifactCoverage;
}

interface GraphEdge {
    source: string;
    target: string;
    type: string;
    refs: string[]; // The requirement/reference IDs that create this link (e.g. REQ-FUNC-03)
}

export class TraceGraphProvider {
    private panel?: vscode.WebviewPanel;

    constructor(
        private readonly data: DataService,
        private readonly client?: SdlicitClient,
    ) {}

    open(): void {
        if (this.panel) {
            this.panel.reveal(vscode.ViewColumn.One);
            this.render();
            return;
        }

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.traceGraph',
            'Sdlicit — Trace Graph',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        this.panel.onDidDispose(() => { this.panel = undefined; });
        this.panel.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.command) {
                case 'openArtifact':
                    vscode.commands.executeCommand('sdlicit.openCanvas', msg.artifactId);
                    break;
                case 'refresh':
                    this.render();
                    break;
            }
        });

        this.render();
    }

    refresh(): void {
        if (this.panel) { this.render(); }
    }

    private async render(): Promise<void> {
        if (!this.panel) { return; }
        const nonce = getNonce();

        let graphData: TraceGraphData | null = null;
        let coverageData: TraceCoverageData | null = null;
        if (this.client) {
            try {
                graphData = await this.client.getTraceabilityGraph();
            } catch { /* Backend unavailable */ }
            try {
                coverageData = await this.client.getTraceCoverage();
            } catch { /* Coverage not available */ }
        }

        let nodes: GraphNode[];
        let edges: GraphEdge[];
        if (graphData && graphData.nodes.length > 0) {
            ({ nodes, edges } = this.buildFromBackend(graphData, coverageData));
        } else {
            ({ nodes, edges } = this.buildFromLocal());
        }

        this.renderGraph(nodes, edges, nonce, coverageData);
    }

    private buildFromBackend(data: TraceGraphData, coverageData?: TraceCoverageData | null): { nodes: GraphNode[]; edges: GraphEdge[] } {
        const coverageMap = new Map<string, ArtifactCoverage>();
        if (coverageData) {
            for (const art of coverageData.artifacts) {
                coverageMap.set(art.artifact_id, art);
            }
        }

        const supersededIds = new Set(data.nodes.filter(n => n.status === 'superseded').map(n => n.id));
        const staleIds = new Set<string>();
        for (const edge of data.edges) {
            if (supersededIds.has(edge.target) && !supersededIds.has(edge.source)) {
                staleIds.add(edge.source);
            }
        }

        const nodes: GraphNode[] = data.nodes.map(n => ({
            id: n.id,
            type: n.type,
            title: n.title,
            status: n.status,
            isStale: staleIds.has(n.id),
            coverage: coverageMap.get(n.id),
        }));

        const edges: GraphEdge[] = data.edges.map(e => ({
            source: e.source,
            target: e.target,
            type: e.type,
            refs: [],
        }));

        return { nodes, edges };
    }

    private buildFromLocal(): { nodes: GraphNode[]; edges: GraphEdge[] } {
        const artifacts = this.data.getArtifacts();
        const nodes: GraphNode[] = artifacts.map(a => ({
            id: a.id,
            type: a.type,
            title: a.title,
            status: a.status,
            isStale: a.status === 'superseded',
        }));

        const edges: GraphEdge[] = [];
        const idSet = new Set(artifacts.map(a => a.id));
        const edgeSet = new Set<string>(); // Deduplicate edges

        // Build a map from requirement/reference IDs (e.g. REQ-FUNC-01) to the artifact that defines them.
        // Requirements are typically defined inside an SRS or requirement-type artifact.
        const refToArtifact = new Map<string, string>();
        for (const artifact of artifacts) {
            if (artifact.type === 'requirement') {
                // Scan content for [REQ-xxx] definitions
                const content = this.data.getArtifactContent(artifact.filePath);
                const reqMatches = content.matchAll(/\[(REQ-[A-Z0-9]+-\d+)\]/g);
                for (const m of reqMatches) {
                    refToArtifact.set(m[1], artifact.id);
                }
                // Also match simpler "- [REQ-xxx]" or just REQ-xxx at start of line items
                const lineMatches = content.matchAll(/^\s*-\s*\[(REQ-[\w-]+\d+)\]/gm);
                for (const m of lineMatches) {
                    refToArtifact.set(m[1], artifact.id);
                }
            }
        }

        // Also map PERSONA-xx to the personas artifact, STORY-xx to stories artifact
        for (const artifact of artifacts) {
            if (artifact.type === 'personas') {
                const content = this.data.getArtifactContent(artifact.filePath);
                const personaMatches = content.matchAll(/\*\*ID:\*\*\s*(PERSONA-\d+)/g);
                for (const m of personaMatches) {
                    refToArtifact.set(m[1], artifact.id);
                }
            }
            if (artifact.type === 'stories') {
                const content = this.data.getArtifactContent(artifact.filePath);
                const storyMatches = content.matchAll(/##\s+(STORY-\d+)/g);
                for (const m of storyMatches) {
                    refToArtifact.set(m[1], artifact.id);
                }
            }
        }

        const addEdge = (source: string, target: string, type: string, ref?: string) => {
            if (source === target) { return; }
            const key = `${source}|${target}|${type}`;
            if (!edgeSet.has(key)) {
                edgeSet.add(key);
                edges.push({ source, target, type, refs: ref ? [ref] : [] });
            } else if (ref) {
                // Add ref to existing edge
                const existing = edges.find(e => e.source === source && e.target === target && e.type === type);
                if (existing && !existing.refs.includes(ref)) {
                    existing.refs.push(ref);
                }
            }
        };

        // Resolve a reference ID to an actual artifact ID
        const resolveRef = (ref: string): string | undefined => {
            if (idSet.has(ref)) { return ref; }
            return refToArtifact.get(ref);
        };

        for (const artifact of artifacts) {
            // Downstream traces
            for (const downId of artifact.traces.downstream) {
                const target = resolveRef(downId);
                if (target) { addEdge(artifact.id, target, 'TRACES_TO', downId); }
            }

            // Upstream traces (this artifact traces FROM something)
            for (const upId of artifact.traces.upstream) {
                const target = resolveRef(upId);
                if (target) { addEdge(target, artifact.id, 'TRACES_TO', upId); }
            }

            // Implements (this artifact implements a requirement)
            for (const reqId of artifact.traces.implements) {
                const target = resolveRef(reqId);
                if (target) { addEdge(target, artifact.id, 'IMPLEMENTS', reqId); }
            }

            // TestedBy
            for (const bddId of artifact.traces.testedBy) {
                const target = resolveRef(bddId);
                if (target) { addEdge(artifact.id, target, 'TESTED_BY', bddId); }
            }

            // Supersedes
            if (artifact.traces.supersedes) {
                const target = resolveRef(artifact.traces.supersedes);
                if (target) { addEdge(artifact.id, target, 'SUPERSEDES', artifact.traces.supersedes); }
            }
        }

        // Implicit hierarchy: SOW → SRS/requirement artifacts (if no explicit link exists)
        const sowArtifacts = artifacts.filter(a => a.type === 'sow');
        const reqArtifacts = artifacts.filter(a => a.type === 'requirement');
        for (const sow of sowArtifacts) {
            for (const req of reqArtifacts) {
                addEdge(sow.id, req.id, 'TRACES_TO');
            }
            // SOW → personas (personas derive from stakeholder analysis)
            for (const p of artifacts.filter(a => a.type === 'personas')) {
                addEdge(sow.id, p.id, 'TRACES_TO');
            }
        }

        // Personas → stories (stories reference personas)
        const personasArtifact = artifacts.find(a => a.type === 'personas');
        const storiesArtifact = artifacts.find(a => a.type === 'stories');
        if (personasArtifact && storiesArtifact) {
            addEdge(personasArtifact.id, storiesArtifact.id, 'TRACES_TO');
        }
        // SRS → stories (stories implement requirements from SRS)
        if (storiesArtifact) {
            for (const req of reqArtifacts) {
                addEdge(req.id, storiesArtifact.id, 'TRACES_TO');
            }
        }

        return { nodes, edges };
    }

    private renderGraph(nodes: GraphNode[], edges: GraphEdge[], nonce: string, coverageData?: TraceCoverageData | null): void {
        if (!this.panel) { return; }

        // ── Build adjacency ──
        const nodeMap = new Map<string, GraphNode>();
        nodes.forEach(n => nodeMap.set(n.id, n));

        const childrenOf = new Map<string, string[]>();
        const parentsOf = new Map<string, string[]>();
        nodes.forEach(n => { childrenOf.set(n.id, []); parentsOf.set(n.id, []); });
        edges.forEach(e => {
            if (nodeMap.has(e.source) && nodeMap.has(e.target)) {
                childrenOf.get(e.source)!.push(e.target);
                parentsOf.get(e.target)!.push(e.source);
            }
        });

        // ── BFS layer assignment (roots = layer 0) ──
        let roots = nodes.filter(n => parentsOf.get(n.id)!.length === 0).map(n => n.id);
        if (roots.length === 0) { roots = nodes.filter(n => n.type === 'sow').map(n => n.id); }
        if (roots.length === 0) { roots = nodes.map(n => n.id); }

        const layers = new Map<string, number>();
        nodes.forEach(n => layers.set(n.id, -1));
        const bfsQueue: string[] = [];
        roots.forEach(id => { layers.set(id, 0); bfsQueue.push(id); });
        while (bfsQueue.length > 0) {
            const curr = bfsQueue.shift()!;
            const currLayer = layers.get(curr)!;
            for (const childId of (childrenOf.get(curr) || [])) {
                if (layers.get(childId)! < currLayer + 1) {
                    layers.set(childId, currLayer + 1);
                    bfsQueue.push(childId);
                }
            }
        }
        nodes.forEach(n => {
            if (layers.get(n.id) === -1) { layers.set(n.id, TYPE_PRIORITY[n.type] ?? 2); }
        });

        // ── Determine display order (layer by layer, reversed so root at bottom) ──
        const maxLayer = Math.max(0, ...[...layers.values()]);
        const orderedNodes: GraphNode[] = [];
        const colAssignment = new Map<string, number>();
        let nextCol = 0;
        const visited = new Set<string>();

        for (let layer = 0; layer <= maxLayer; layer++) {
            const layerNodes = nodes
                .filter(n => layers.get(n.id) === layer)
                .sort((a, b) => {
                    const pa = TYPE_PRIORITY[a.type] ?? 5;
                    const pb = TYPE_PRIORITY[b.type] ?? 5;
                    if (pa !== pb) { return pa - pb; }
                    return a.title.localeCompare(b.title);
                });

            for (const node of layerNodes) {
                if (visited.has(node.id)) { continue; }
                visited.add(node.id);

                // Column assignment: first child inherits parent column, others branch
                if (layer === 0) {
                    colAssignment.set(node.id, nextCol++);
                } else {
                    const nodeParents = parentsOf.get(node.id) || [];
                    let inherited = false;
                    for (const pId of nodeParents) {
                        if (colAssignment.has(pId)) {
                            const parentChildren = childrenOf.get(pId) || [];
                            const sameLayerChildren = parentChildren.filter(c => layers.get(c) === layer);
                            if (sameLayerChildren[0] === node.id) {
                                colAssignment.set(node.id, colAssignment.get(pId)!);
                                inherited = true;
                                break;
                            }
                        }
                    }
                    if (!inherited) {
                        colAssignment.set(node.id, nextCol++);
                    }
                }

                orderedNodes.push(node);
            }
        }

        // Reverse so root (SOW) is at the bottom like git log
        orderedNodes.reverse();

        // Row index for each node (after reversal)
        const rowIndex = new Map<string, number>();
        orderedNodes.forEach((n, i) => rowIndex.set(n.id, i));

        // ── Graph geometry (git-graph style) ──
        const ROW_HEIGHT = 34;
        const COL_WIDTH = 16;
        const OFFSET_X = 12;
        const OFFSET_Y = ROW_HEIGHT / 2;
        const totalCols = nextCol || 1;
        const graphWidth = OFFSET_X + totalCols * COL_WIDTH + 8;
        const totalHeight = orderedNodes.length * ROW_HEIGHT;

        const getX = (col: number) => OFFSET_X + col * COL_WIDTH;
        const getY = (row: number) => row * ROW_HEIGHT + OFFSET_Y;

        // ── Build edges as SVG paths (git-graph style: curved transitions) ──
        const edgeColors: Record<string, string> = {
            'TRACES_TO': '#4191e0', 'TRACES_FROM': '#4191e0',
            'IMPLEMENTS': '#4caf50', 'TESTED_BY': '#e07041', 'SUPERSEDES': '#e8a838',
        };
        const edgeLabels: Record<string, string> = {
            'TRACES_TO': 'traces to', 'TRACES_FROM': 'traces from',
            'IMPLEMENTS': 'implements', 'TESTED_BY': 'tested by', 'SUPERSEDES': 'supersedes',
        };

        let svgPaths = '';
        for (const edge of edges) {
            const srcRow = rowIndex.get(edge.source);
            const tgtRow = rowIndex.get(edge.target);
            if (srcRow === undefined || tgtRow === undefined) { continue; }
            const srcCol = colAssignment.get(edge.source) ?? 0;
            const tgtCol = colAssignment.get(edge.target) ?? 0;

            const x1 = getX(srcCol);
            const y1 = getY(srcRow);
            const x2 = getX(tgtCol);
            const y2 = getY(tgtRow);
            const color = edgeColors[edge.type] ?? '#888';

            if (x1 === x2) {
                // Same column: vertical line
                svgPaths += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="2" opacity="0.7"/>`;
            } else {
                // Different columns: cubic bezier (git-graph style angular/curved)
                const d = ROW_HEIGHT * 0.8;
                // From source row downward: extend vertically, then curve to target column
                if (y2 > y1) {
                    // target is below source in the display
                    svgPaths += `<path d="M${x1},${y1} C${x1},${y1 + d} ${x2},${y2 - d} ${x2},${y2}" fill="none" stroke="${color}" stroke-width="2" opacity="0.7"/>`;
                } else {
                    svgPaths += `<path d="M${x1},${y1} C${x1},${y1 - d} ${x2},${y2 + d} ${x2},${y2}" fill="none" stroke="${color}" stroke-width="2" opacity="0.7"/>`;
                }
            }
        }

        // ── Draw circles (vertices) on top of paths ──
        let svgCircles = '';
        for (let i = 0; i < orderedNodes.length; i++) {
            const node = orderedNodes[i];
            const col = colAssignment.get(node.id) ?? 0;
            const x = getX(col);
            const y = getY(i);
            const color = TYPE_COLORS[node.type] ?? '#888';
            svgCircles += `<circle cx="${x}" cy="${y}" r="4" fill="${color}" data-id="${escapeHtml(node.id)}"/>`;
        }

        // ── Build the HTML table (git-graph approach: table with graph in first column) ──
        let tableRows = '';
        for (let i = 0; i < orderedNodes.length; i++) {
            const node = orderedNodes[i];
            const color = TYPE_COLORS[node.type] ?? '#888';
            const label = TYPE_LABELS[node.type] ?? '?';
            const staleClass = node.isStale ? ' tg-stale-row' : '';
            const supersededClass = node.status === 'superseded' ? ' tg-superseded-row' : '';

            // Gather incoming edge refs for this node (show what it implements/traces)
            const inRefs = edges.filter(e => e.target === node.id && e.refs.length > 0);
            const refsHtml = inRefs.length > 0
                ? `<span class="tg-refs">${inRefs.map(e => `<span class="tg-ref" style="color:${edgeColors[e.type] ?? '#888'}">${edgeLabels[e.type] ?? e.type} ${escapeHtml(e.refs.join(', '))}</span>`).join(' ')}</span>`
                : '';

            tableRows += `<tr class="tg-commit${staleClass}${supersededClass}" data-id="${escapeHtml(node.id)}" data-color="${color}">
                <td class="tg-graph-td"></td>
                <td class="tg-desc-td">
                    <span class="tg-type-badge" style="background:${color}">${label}</span>
                    <span class="tg-title">${escapeHtml(node.title)}</span>
                    <span class="tg-id">${escapeHtml(node.id)}</span>
                    ${refsHtml}
                    ${node.isStale ? '<span class="tg-stale-badge">⚠ stale</span>' : ''}
                </td>
            </tr>`;
        }

        // ── Coverage summary ──
        let coverageSummaryHtml = '';
        if (coverageData) {
            const structPct = coverageData.structural_coverage_pct.toFixed(1);
            const semPct = coverageData.semantic_coverage_pct !== null ? coverageData.semantic_coverage_pct.toFixed(1) : null;
            coverageSummaryHtml = `
                <div class="tg-coverage">
                    <span class="tg-cov-badge">Structural: <strong>${structPct}%</strong></span>
                    ${semPct !== null ? `<span class="tg-cov-badge">Semantic: <strong>${semPct}%</strong></span>` : ''}
                    <span class="tg-cov-badge">Links: ${coverageData.valid_links}/${coverageData.total_links}</span>
                    ${coverageData.broken_links_count > 0 ? `<span class="tg-cov-badge tg-badge-warn">${coverageData.broken_links_count} broken</span>` : ''}
                </div>`;
        }

        // ── Legend ──
        const usedTypes = [...new Set(nodes.map(n => n.type))].sort((a, b) => (TYPE_PRIORITY[a] ?? 5) - (TYPE_PRIORITY[b] ?? 5));
        const legendHtml = usedTypes.map(type =>
            `<span class="tg-legend-item"><span class="tg-legend-dot" style="background:${TYPE_COLORS[type] ?? '#888'}"></span>${TYPE_LABELS[type] ?? type}</span>`
        ).join('');

        const edgeLegendHtml = [
            ['TRACES_TO', 'Trace'],
            ['IMPLEMENTS', 'Implements'],
            ['TESTED_BY', 'Tests'],
            ['SUPERSEDES', 'Supersedes'],
        ].map(([key, lbl]) =>
            `<span class="tg-legend-item"><span class="tg-legend-line" style="background:${edgeColors[key]}"></span>${lbl}</span>`
        ).join('');

        const body = `
            <div class="tg-header">
                <h2 style="margin:0">Trace Graph</h2>
                <div class="tg-legend">${legendHtml}<span class="tg-legend-sep">|</span>${edgeLegendHtml}</div>
                <button class="btn btn-sm btn-secondary" id="refreshBtn">↻ Refresh</button>
            </div>
            ${coverageSummaryHtml}
            <div class="tg-subtitle">${nodes.length} artifacts · ${edges.length} links · Click artifact to open</div>
            <div id="view" class="tg-view">
                <div id="commitGraph" class="tg-graph-wrap" style="width:${graphWidth}px">
                    <svg xmlns="http://www.w3.org/2000/svg" width="${graphWidth}" height="${totalHeight}">
                        <g class="tg-edges">${svgPaths}</g>
                        <g class="tg-vertices">${svgCircles}</g>
                    </svg>
                </div>
                <div id="commitTable" class="tg-table-wrap">
                    <table><tbody>${tableRows}</tbody></table>
                </div>
            </div>
        `;

        const extraStyles = `
            .tg-header {
                display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
                padding-bottom: 8px; border-bottom: 1px solid var(--sdl-border); margin-bottom: 8px;
            }
            .tg-header h2 { flex-shrink: 0; }
            .tg-legend { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; flex: 1; }
            .tg-legend-item { display: inline-flex; align-items: center; gap: 4px; font-size: .78em; }
            .tg-legend-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
            .tg-legend-line { width: 14px; height: 3px; border-radius: 2px; flex-shrink: 0; }
            .tg-legend-sep { color: var(--vscode-descriptionForeground); opacity: .4; }
            .tg-coverage {
                display: flex; gap: 10px; align-items: center; padding: 6px 10px;
                background: var(--vscode-editor-inactiveSelectionBackground); border-radius: 4px;
                font-size: .82em; margin-bottom: 6px;
            }
            .tg-cov-badge { padding: 2px 7px; border-radius: 3px; background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
            .tg-badge-warn { background: var(--vscode-editorWarning-foreground); color: var(--vscode-editor-background); }
            .tg-subtitle { font-size: .82em; color: var(--vscode-descriptionForeground); margin-bottom: 8px; }

            /* ── Git-graph inspired table layout ── */
            .tg-view {
                display: flex;
                overflow: auto;
                max-height: calc(100vh - 140px);
                border: 1px solid var(--sdl-border);
                border-radius: 4px;
                position: relative;
            }
            .tg-graph-wrap {
                flex-shrink: 0;
                position: sticky;
                left: 0;
                z-index: 2;
                background: var(--vscode-editor-background);
            }
            .tg-graph-wrap svg {
                display: block;
            }
            .tg-table-wrap {
                flex: 1;
                min-width: 0;
            }
            .tg-table-wrap table {
                border-collapse: collapse;
                width: 100%;
                table-layout: fixed;
            }
            .tg-commit {
                height: ${ROW_HEIGHT}px;
                cursor: pointer;
                transition: background .1s;
            }
            .tg-commit:hover {
                background: var(--vscode-list-hoverBackground);
            }
            .tg-commit.tg-highlight {
                background: color-mix(in srgb, var(--sdl-color-accent) 15%, transparent);
            }
            .tg-stale-row {
                border-left: 3px solid var(--vscode-editorWarning-foreground);
            }
            .tg-superseded-row {
                opacity: .5;
            }
            .tg-graph-td {
                width: 0;
                padding: 0;
                /* This cell has no content - graph SVG is in sibling div */
            }
            .tg-desc-td {
                padding: 0 12px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                vertical-align: middle;
                border-bottom: 1px solid color-mix(in srgb, var(--sdl-border) 20%, transparent);
            }
            .tg-type-badge {
                display: inline-block; padding: 1px 6px; border-radius: 3px;
                font-size: .7em; font-weight: 700; color: #fff;
                vertical-align: middle; margin-right: 6px;
            }
            .tg-title {
                font-size: .88em; font-weight: 500;
                vertical-align: middle;
            }
            .tg-id {
                font-size: .72em; color: var(--vscode-descriptionForeground);
                margin-left: 8px; vertical-align: middle;
            }
            .tg-refs {
                margin-left: 8px; vertical-align: middle;
            }
            .tg-ref {
                font-size: .7em; font-style: italic; margin-right: 6px;
                font-family: var(--vscode-editor-font-family, monospace);
            }
            .tg-stale-badge {
                font-size: .68em; padding: 1px 4px; border-radius: 3px;
                background: var(--vscode-editorWarning-foreground); color: var(--vscode-editor-background);
                font-weight: 600; margin-left: 8px; vertical-align: middle;
            }
            .tg-empty { padding: 48px; text-align: center; color: var(--vscode-descriptionForeground); }
        `;

        const scripts = `
            document.addEventListener('click', function(e) {
                var row = e.target.closest('.tg-commit');
                if (row && row.dataset.id) {
                    vscode.postMessage({ command: 'openArtifact', artifactId: row.dataset.id });
                }
                if (e.target.closest('#refreshBtn')) {
                    vscode.postMessage({ command: 'refresh' });
                }
            });

            var _edges = ${JSON.stringify(edges.map(e => ({ source: e.source, target: e.target })))};
            document.addEventListener('mouseover', function(e) {
                var row = e.target.closest('.tg-commit');
                if (row && row.dataset.id) {
                    var id = row.dataset.id;
                    var connected = new Set([id]);
                    _edges.forEach(function(ed) {
                        if (ed.source === id) connected.add(ed.target);
                        if (ed.target === id) connected.add(ed.source);
                    });
                    document.querySelectorAll('.tg-commit').forEach(function(r) {
                        if (connected.has(r.dataset.id)) {
                            r.classList.add('tg-highlight');
                        }
                    });
                }
            });
            document.addEventListener('mouseout', function(e) {
                var row = e.target.closest('.tg-commit');
                if (row) {
                    document.querySelectorAll('.tg-highlight').forEach(function(r) {
                        r.classList.remove('tg-highlight');
                    });
                }
            });
        `;

        this.panel.webview.html = wrapHtml(body, nonce, scripts, extraStyles);
    }
}
