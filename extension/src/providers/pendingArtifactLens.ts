// ---------------------------------------------------------------------------
// Sdlicit — Pending Artifact CodeLens Provider
// ---------------------------------------------------------------------------
// Shows Accept / Decline / Regenerate CodeLens buttons at the top of generated
// artifacts that are pending user review (like GitHub Copilot inline suggestions).
// Also applies a gutter decoration to make it clear the document is "proposed".
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';

export interface PendingArtifact {
    filePath: string;
    uri: vscode.Uri;
    type: 'sow' | 'adr' | 'srs' | 'personas' | 'stories' | 'gherkin';
    brief?: string;
    clarifications?: { question: string; answer: string }[];
    onAccept?: () => Promise<void>;
    onDecline?: () => Promise<void>;
    onRegenerate?: (notes: string) => Promise<string | undefined>;
}

const pendingBannerDecoration = vscode.window.createTextEditorDecorationType({
    isWholeLine: true,
    overviewRulerColor: '#4fc3f7',
    overviewRulerLane: vscode.OverviewRulerLane.Full,
    light: { backgroundColor: '#e1f5fe40' },
    dark: { backgroundColor: '#01579b30' },
});

export class PendingArtifactLensProvider implements vscode.CodeLensProvider {
    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChange.event;

    private pending: Map<string, PendingArtifact> = new Map();
    private _disposables: vscode.Disposable[] = [];

    constructor() {
        // Re-apply decorations when editors change visibility
        this._disposables.push(
            vscode.window.onDidChangeVisibleTextEditors(() => {
                for (const artifact of this.pending.values()) {
                    this.applyDecoration(artifact.uri);
                }
            }),
        );
    }

    /**
     * Register a file as pending review. Returns a dispose function.
     */
    addPending(artifact: PendingArtifact): vscode.Disposable {
        this.pending.set(artifact.filePath, artifact);
        this._onDidChange.fire();
        this.applyDecoration(artifact.uri);

        // Fire again after a short delay to ensure CodeLens appears
        // after the editor is fully initialized
        setTimeout(() => {
            if (this.pending.has(artifact.filePath)) {
                this._onDidChange.fire();
                this.applyDecoration(artifact.uri);
            }
        }, 300);

        return new vscode.Disposable(() => {
            this.pending.delete(artifact.filePath);
            this._onDidChange.fire();
            this.clearDecoration(artifact.uri);
        });
    }

    getPending(filePath: string): PendingArtifact | undefined {
        return this.pending.get(filePath);
    }

    removePending(filePath: string): void {
        this.pending.delete(filePath);
        this._onDidChange.fire();
    }

    hasPending(): boolean {
        return this.pending.size > 0;
    }

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] | undefined {
        const artifact = this.pending.get(document.uri.fsPath);
        if (!artifact) { return undefined; }

        const topRange = new vscode.Range(0, 0, 0, 0);

        return [
            new vscode.CodeLens(topRange, {
                title: '$(check) Accept',
                command: 'sdlicit.acceptPendingArtifact',
                arguments: [document.uri.fsPath],
                tooltip: 'Accept this artifact and ingest into Knowledge Base',
            }),
            new vscode.CodeLens(topRange, {
                title: '$(close) Decline',
                command: 'sdlicit.declinePendingArtifact',
                arguments: [document.uri.fsPath],
                tooltip: 'Discard this artifact',
            }),
            new vscode.CodeLens(topRange, {
                title: '$(sync) Regenerate',
                command: 'sdlicit.regeneratePendingArtifact',
                arguments: [document.uri.fsPath],
                tooltip: 'Regenerate with additional notes',
            }),
        ];
    }

    private applyDecoration(uri: vscode.Uri): void {
        const editor = vscode.window.visibleTextEditors.find(
            e => e.document.uri.fsPath === uri.fsPath,
        );
        if (editor) {
            const fullRange = new vscode.Range(0, 0, editor.document.lineCount - 1, 0);
            editor.setDecorations(pendingBannerDecoration, [fullRange]);
        }
    }

    private clearDecoration(uri: vscode.Uri): void {
        const editor = vscode.window.visibleTextEditors.find(
            e => e.document.uri.fsPath === uri.fsPath,
        );
        if (editor) {
            editor.setDecorations(pendingBannerDecoration, []);
        }
    }

    dispose(): void {
        for (const d of this._disposables) { d.dispose(); }
        this._disposables = [];
    }
}
