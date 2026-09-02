// ---------------------------------------------------------------------------
// Sdlicit — Trace Link CodeLens Provider
// ---------------------------------------------------------------------------
// Shows link counts above artifact titles in .sdlicit/artifacts/ files:
//   "↑ 2 upstream · ↓ 3 downstream · implements: REQ-01, REQ-02"
// Also shows a "Check Traceability" action lens.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';
import { SdlicitClient } from '../services/sdlicitClient';

export class TraceLensProvider implements vscode.CodeLensProvider {
    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChange.event;

    constructor(
        private readonly data: DataService,
        private readonly client: SdlicitClient,
    ) {}

    refresh(): void {
        this._onDidChange.fire();
    }

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        // Only act on .sdlicit/artifacts/ markdown files
        if (!document.uri.fsPath.includes('.sdlicit') || !document.uri.fsPath.includes('artifacts')) {
            return [];
        }

        const lenses: vscode.CodeLens[] = [];
        const content = document.getText();

        // Find the artifact for this file
        const artifacts = this.data.getArtifacts();
        const artifact = artifacts.find(a => a.filePath === document.uri.fsPath);
        if (!artifact) { return []; }

        // Find the title line (first H1)
        const titleLine = this.findTitleLine(document);
        if (titleLine < 0) { return []; }

        const range = new vscode.Range(titleLine, 0, titleLine, 0);

        // Build link summary
        const parts: string[] = [];
        const upCount = artifact.traces.upstream.length;
        const downCount = artifact.traces.downstream.length;
        const implCount = artifact.traces.implements.length;
        const testCount = artifact.traces.testedBy.length;

        if (upCount > 0) { parts.push(`↑ ${upCount} upstream`); }
        if (downCount > 0) { parts.push(`↓ ${downCount} downstream`); }
        if (implCount > 0) { parts.push(`implements: ${artifact.traces.implements.join(', ')}`); }
        if (artifact.traces.supersedes) { parts.push(`supersedes: ${artifact.traces.supersedes}`); }
        if (testCount > 0) { parts.push(`tested by: ${artifact.traces.testedBy.join(', ')}`); }

        if (parts.length > 0) {
            lenses.push(new vscode.CodeLens(range, {
                title: `$(link) ${parts.join(' · ')}`,
                command: 'sdlicit.openTraceGraph',
                tooltip: 'Open Trace Graph to see all connections',
            }));
        } else {
            lenses.push(new vscode.CodeLens(range, {
                title: '$(link) No trace links',
                command: 'sdlicit.openTraceGraph',
                tooltip: 'No upstream or downstream links found',
            }));
        }

        // Check traceability action
        lenses.push(new vscode.CodeLens(range, {
            title: '$(shield) Check Traceability',
            command: 'sdlicit.checkTraceability',
            arguments: [artifact.id],
            tooltip: 'Run traceability validation for this artifact',
        }));

        return lenses;
    }

    private findTitleLine(document: vscode.TextDocument): number {
        for (let i = 0; i < Math.min(document.lineCount, 20); i++) {
            const line = document.lineAt(i).text;
            if (line.startsWith('# ') || line.startsWith('Feature:')) {
                return i;
            }
        }
        return 0;
    }
}
