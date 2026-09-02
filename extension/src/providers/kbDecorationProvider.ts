// ---------------------------------------------------------------------------
// Sdlicit — KB File Decoration Provider
// ---------------------------------------------------------------------------
// Shows badges/icons on files in the explorer and knowledge browser
// indicating their KB sync status.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { KBSyncService, KBFileStatus } from '../services/kbSyncService';

export class KBDecorationProvider implements vscode.FileDecorationProvider {
    private _onDidChangeFileDecorations = new vscode.EventEmitter<vscode.Uri | vscode.Uri[] | undefined>();
    readonly onDidChangeFileDecorations = this._onDidChangeFileDecorations.event;

    constructor(private readonly kbSync: KBSyncService) {
        kbSync.onStatusChange((entry) => {
            const uri = vscode.Uri.file(entry.filePath);
            this._onDidChangeFileDecorations.fire(uri);
        });
        kbSync.onFilesChange(() => {
            this._onDidChangeFileDecorations.fire(undefined);
        });
    }

    provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
        const knowledgeDir = this.kbSync.knowledgeDir;
        if (!knowledgeDir) { return undefined; }

        // Only decorate files inside .sdlicit/knowledge/
        if (!uri.fsPath.startsWith(knowledgeDir)) { return undefined; }

        const fileName = uri.fsPath.split('/').pop() ?? '';
        const status = this.kbSync.getStatus(fileName);

        return this.decorationForStatus(status);
    }

    private decorationForStatus(status: KBFileStatus): vscode.FileDecoration | undefined {
        switch (status) {
            case 'synced':
                return new vscode.FileDecoration(
                    '✓',
                    'Synced to Knowledge Base',
                    new vscode.ThemeColor('testing.iconPassed'),
                );
            case 'uploading':
                return new vscode.FileDecoration(
                    '↑',
                    'Uploading to Knowledge Base…',
                    new vscode.ThemeColor('charts.blue'),
                );
            case 'pending':
                return new vscode.FileDecoration(
                    '○',
                    'Not in Knowledge Base',
                    new vscode.ThemeColor('charts.yellow'),
                );
            case 'error':
                return new vscode.FileDecoration(
                    '!',
                    'KB ingestion failed',
                    new vscode.ThemeColor('testing.iconFailed'),
                );
            default:
                return undefined;
        }
    }

    dispose(): void {
        this._onDidChangeFileDecorations.dispose();
    }
}
