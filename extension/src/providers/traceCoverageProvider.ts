// ---------------------------------------------------------------------------
// Sdlicit — Trace Coverage Decoration Provider
// ---------------------------------------------------------------------------
// Shows inline gutter decorations and hover info on artifact files
// indicating their trace coverage status (structural + semantic).
//
// - Green gutter dot = all trace links valid
// - Yellow gutter dot = some links valid, some broken
// - Red gutter dot = broken links present
// - Hover tooltip shows coverage %, linked artifacts, semantic score
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SdlicitClient, ArtifactCoverage, TraceCoverageData } from '../services/sdlicitClient';
import { DataService } from '../services/dataService';

// Decoration types for different coverage states
const DECO_FULL = vscode.window.createTextEditorDecorationType({
    gutterIconPath: undefined, // Will use colored dot via CSS
    overviewRulerColor: 'rgba(76, 175, 80, 0.7)',
    overviewRulerLane: vscode.OverviewRulerLane.Left,
    before: {
        contentText: '●',
        color: new vscode.ThemeColor('testing.iconPassed'),
        margin: '0 4px 0 0',
    },
});

const DECO_PARTIAL = vscode.window.createTextEditorDecorationType({
    overviewRulerColor: 'rgba(255, 193, 7, 0.7)',
    overviewRulerLane: vscode.OverviewRulerLane.Left,
    before: {
        contentText: '◐',
        color: new vscode.ThemeColor('charts.yellow'),
        margin: '0 4px 0 0',
    },
});

const DECO_BROKEN = vscode.window.createTextEditorDecorationType({
    overviewRulerColor: 'rgba(244, 67, 54, 0.7)',
    overviewRulerLane: vscode.OverviewRulerLane.Left,
    before: {
        contentText: '○',
        color: new vscode.ThemeColor('testing.iconFailed'),
        margin: '0 4px 0 0',
    },
});

interface TraceDecorationEntry {
    line: number;
    artifactId: string;
    coverage: ArtifactCoverage;
}

export class TraceCoverageDecorationProvider implements vscode.Disposable {
    private disposables: vscode.Disposable[] = [];
    private coverageCache: TraceCoverageData | null = null;
    private lastFetchTime = 0;
    private readonly CACHE_TTL_MS = 30_000; // 30 seconds

    constructor(
        private readonly client: SdlicitClient,
        private readonly data: DataService,
    ) {
        // Update decorations when active editor changes
        this.disposables.push(
            vscode.window.onDidChangeActiveTextEditor((editor) => {
                if (editor) { this.updateDecorations(editor); }
            }),
        );
        // Update on document save
        this.disposables.push(
            vscode.workspace.onDidSaveTextDocument((doc) => {
                const editor = vscode.window.activeTextEditor;
                if (editor && editor.document === doc) {
                    this.invalidateCache();
                    this.updateDecorations(editor);
                }
            }),
        );
        // Initial decoration
        if (vscode.window.activeTextEditor) {
            this.updateDecorations(vscode.window.activeTextEditor);
        }
    }

    invalidateCache(): void {
        this.coverageCache = null;
        this.lastFetchTime = 0;
    }

    async refresh(): Promise<void> {
        this.invalidateCache();
        const editor = vscode.window.activeTextEditor;
        if (editor) {
            await this.updateDecorations(editor);
        }
    }

    private async updateDecorations(editor: vscode.TextEditor): Promise<void> {
        const doc = editor.document;

        // Only decorate markdown and feature files in artifact directories
        if (!this.isArtifactFile(doc.uri)) {
            this.clearDecorations(editor);
            return;
        }

        const coverage = await this.getCoverage();
        if (!coverage) {
            this.clearDecorations(editor);
            return;
        }

        // Find which artifact this file represents
        const entries = this.matchFileToArtifacts(doc, coverage);
        if (entries.length === 0) {
            this.clearDecorations(editor);
            return;
        }

        // Apply decorations
        const fullRanges: vscode.DecorationOptions[] = [];
        const partialRanges: vscode.DecorationOptions[] = [];
        const brokenRanges: vscode.DecorationOptions[] = [];

        for (const entry of entries) {
            const range = new vscode.Range(entry.line, 0, entry.line, 0);
            const cov = entry.coverage;

            const hoverParts: string[] = [];
            hoverParts.push(`**Trace: ${entry.artifactId}**`);
            if (cov.outgoing_links > 0) {
                hoverParts.push(`Links: ${cov.valid_links}/${cov.outgoing_links} valid`);
            }
            if (cov.broken_links > 0) {
                hoverParts.push(`⚠ ${cov.broken_links} broken link(s)`);
            }
            if (cov.semantic_score !== null) {
                const pct = Math.round(cov.semantic_score * 100);
                hoverParts.push(`Semantic match: ${pct}%`);
            }
            if (cov.covered_by.length > 0) {
                hoverParts.push(`Covered by: ${cov.covered_by.join(', ')}`);
            }

            const hover = new vscode.MarkdownString(hoverParts.join('  \n'));

            const decoOption: vscode.DecorationOptions = { range, hoverMessage: hover };

            if (cov.broken_links > 0) {
                brokenRanges.push(decoOption);
            } else if (cov.outgoing_links > 0 && cov.valid_links === cov.outgoing_links) {
                fullRanges.push(decoOption);
            } else if (cov.outgoing_links > 0) {
                partialRanges.push(decoOption);
            }
        }

        editor.setDecorations(DECO_FULL, fullRanges);
        editor.setDecorations(DECO_PARTIAL, partialRanges);
        editor.setDecorations(DECO_BROKEN, brokenRanges);
    }

    private clearDecorations(editor: vscode.TextEditor): void {
        editor.setDecorations(DECO_FULL, []);
        editor.setDecorations(DECO_PARTIAL, []);
        editor.setDecorations(DECO_BROKEN, []);
    }

    private async getCoverage(): Promise<TraceCoverageData | null> {
        const now = Date.now();
        if (this.coverageCache && (now - this.lastFetchTime) < this.CACHE_TTL_MS) {
            return this.coverageCache;
        }
        try {
            this.coverageCache = await this.client.getTraceCoverage();
            this.lastFetchTime = now;
            return this.coverageCache;
        } catch {
            return null;
        }
    }

    private isArtifactFile(uri: vscode.Uri): boolean {
        const path = uri.fsPath;
        return path.includes('/.sdlicit/artifacts/');
    }

    private matchFileToArtifacts(
        doc: vscode.TextDocument,
        coverage: TraceCoverageData,
    ): TraceDecorationEntry[] {
        const entries: TraceDecorationEntry[] = [];
        const text = doc.getText();

        // Try to find artifact IDs referenced in this file
        const idPatterns = [
            /REQ-[\w-]+\d+/g,
            /ADR-[\w-]+-\d{1,4}/g,
            /ADR-\d{1,4}/g,
            /STORY-\d+/g,
            /BDD-\d+/g,
            /PERSONA-\d+/g,
        ];

        // Build coverage lookup
        const coverageMap = new Map<string, ArtifactCoverage>();
        for (const art of coverage.artifacts) {
            coverageMap.set(art.artifact_id, art);
        }

        // Find artifact IDs in the document and their line positions
        const seen = new Set<string>();
        for (const pattern of idPatterns) {
            let match: RegExpExecArray | null;
            while ((match = pattern.exec(text)) !== null) {
                const id = match[0];
                if (seen.has(id)) { continue; }
                seen.add(id);

                const cov = coverageMap.get(id);
                if (!cov) { continue; }

                const pos = doc.positionAt(match.index);
                entries.push({
                    line: pos.line,
                    artifactId: id,
                    coverage: cov,
                });
            }
        }

        // Also check if this file IS the artifact (header-based ID)
        const fileName = doc.uri.fsPath.split('/').pop() ?? '';
        for (const art of coverage.artifacts) {
            if (seen.has(art.artifact_id)) { continue; }
            // Match by filename slug
            const slug = art.artifact_id.toLowerCase().replace(/[^a-z0-9]/g, '');
            if (fileName.toLowerCase().replace(/[^a-z0-9]/g, '').includes(slug)) {
                entries.push({
                    line: 0,
                    artifactId: art.artifact_id,
                    coverage: art,
                });
                break;
            }
        }

        return entries;
    }

    dispose(): void {
        this.disposables.forEach(d => d.dispose());
    }
}
