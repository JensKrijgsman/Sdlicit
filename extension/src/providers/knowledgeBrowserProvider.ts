// ---------------------------------------------------------------------------
// Sdlicit — Knowledge Browser Tree Provider
// ---------------------------------------------------------------------------
// Sidebar tree showing .sdlicit/knowledge/ files with KB sync status icons.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { KBSyncService, KBFileEntry, KBFileStatus } from '../services/kbSyncService';

const STATUS_ICONS: Record<KBFileStatus, vscode.ThemeIcon> = {
    synced: new vscode.ThemeIcon('pass-filled', new vscode.ThemeColor('testing.iconPassed')),
    uploading: new vscode.ThemeIcon('sync~spin', new vscode.ThemeColor('charts.blue')),
    pending: new vscode.ThemeIcon('circle-outline', new vscode.ThemeColor('charts.yellow')),
    error: new vscode.ThemeIcon('error', new vscode.ThemeColor('testing.iconFailed')),
};

export class KnowledgeBrowserItem extends vscode.TreeItem {
    constructor(public readonly entry: KBFileEntry) {
        super(entry.fileName, vscode.TreeItemCollapsibleState.None);
        this.iconPath = STATUS_ICONS[entry.status];
        this.tooltip = this.buildTooltip();
        this.description = this.buildDescription();
        this.contextValue = `kbFile.${entry.status}`;
        this.command = {
            command: 'sdlicit.openKBFile',
            title: 'Open File',
            arguments: [entry.filePath],
        };
    }

    private buildTooltip(): string {
        const lines = [this.entry.fileName];
        lines.push(`Status: ${this.entry.status}`);
        if (this.entry.lastSynced) {
            lines.push(`Last synced: ${this.entry.lastSynced}`);
        }
        if (this.entry.error) {
            lines.push(`Error: ${this.entry.error}`);
        }
        return lines.join('\n');
    }

    private buildDescription(): string {
        switch (this.entry.status) {
            case 'synced': return '✓ in KB';
            case 'uploading': return 'uploading…';
            case 'pending': return 'not in KB';
            case 'error': return '⚠ error';
        }
    }
}

export class KnowledgeBrowserProvider implements vscode.TreeDataProvider<KnowledgeBrowserItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<KnowledgeBrowserItem | undefined | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    constructor(private readonly kbSync: KBSyncService) {
        kbSync.onFilesChange(() => this.refresh());
        kbSync.onStatusChange(() => this.refresh());
    }

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: KnowledgeBrowserItem): vscode.TreeItem {
        return element;
    }

    getChildren(): KnowledgeBrowserItem[] {
        const files = this.kbSync.getFiles();
        if (files.length === 0) {
            return [];
        }
        return files.map(f => new KnowledgeBrowserItem(f));
    }
}
