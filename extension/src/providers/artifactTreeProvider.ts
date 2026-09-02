// ---------------------------------------------------------------------------
// Sdlicit — Artifact Tree Provider
// ---------------------------------------------------------------------------
// Sidebar tree with: type icons, quality badges, KB ingestion status,
// collapsible categories.
// Reads artifacts directly from .sdlicit/artifacts/ (local file I/O).
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';
import { SdlicitClient } from '../services/sdlicitClient';
import { Artifact, ArtifactType } from '../types';

export type ArtifactKBState = 'none' | 'ingesting' | 'ingested' | 'error';

class CategoryNode extends vscode.TreeItem {
    constructor(
        public readonly category: ArtifactType,
        label: string,
        public readonly artifacts: Artifact[],
    ) {
        super(label, vscode.TreeItemCollapsibleState.Expanded);
        this.contextValue = 'sdlcCategory';
        const accepted = artifacts.filter(a => a.status === 'accepted').length;
        this.description = `${artifacts.length}${accepted > 0 ? ` (${accepted} locked)` : ''}`;
        // BDD category click → overview panel
        if (category === 'scenario') {
            this.command = {
                command: 'sdlicit.viewBDDOverview',
                title: 'View BDD Overview',
                arguments: [],
            };
        }
    }
}

class ArtifactNode extends vscode.TreeItem {
    constructor(public readonly artifact: Artifact, kbState: ArtifactKBState, showKBStatus: boolean) {
        super(artifact.title, vscode.TreeItemCollapsibleState.None);
        this.description = this.buildDescription(kbState, showKBStatus);
        this.tooltip = `${artifact.id}: ${artifact.title}\nType: ${artifact.type}\nStatus: ${artifact.status}\nQuality: ${artifact.quality.current ?? 'Not assessed'}${showKBStatus ? `\nKB: ${kbState}` : ''}`;
        this.contextValue = `sdlcArtifact.${artifact.type}`;
        // Click routes to the dedicated panel (SOW/ADR/SRS) or canvas
        this.command = {
            command: 'sdlicit.openCanvas',
            title: 'Open Artifact',
            arguments: [artifact.id],
        };
        this.iconPath = this.getIcon(kbState, showKBStatus);
    }

    private buildDescription(kbState: ArtifactKBState, showKBStatus: boolean): string {
        const id = this.artifact.id;
        if (!showKBStatus) { return id; }
        switch (kbState) {
            case 'ingested': return `${id} ✓ KB`;
            case 'ingesting': return `${id} ⟳ ingesting…`;
            case 'error': return `${id} ⚠ KB error`;
            case 'none': return `${id} ○ not in KB`;
            default: return id;
        }
    }

    private getIcon(kbState: ArtifactKBState, showKBStatus: boolean): vscode.ThemeIcon {
        // When KB status is shown, override icons to reflect ingestion state
        if (showKBStatus) {
            switch (kbState) {
                case 'ingesting':
                    return new vscode.ThemeIcon('sync~spin', new vscode.ThemeColor('charts.blue'));
                case 'ingested':
                    return new vscode.ThemeIcon('pass-filled', new vscode.ThemeColor('testing.iconPassed'));
                case 'error':
                    return new vscode.ThemeIcon('error', new vscode.ThemeColor('testing.iconFailed'));
                case 'none':
                    return new vscode.ThemeIcon('circle-outline', new vscode.ThemeColor('charts.yellow'));
            }
        }

        if (this.artifact.status === 'accepted') {
            return new vscode.ThemeIcon('lock', new vscode.ThemeColor('charts.green'));
        }
        if (this.artifact.status === 'deprecated' || this.artifact.status === 'superseded') {
            return new vscode.ThemeIcon('archive', new vscode.ThemeColor('charts.red'));
        }
        const q = this.artifact.quality.current;
        if (q === 'gold') { return new vscode.ThemeIcon('star-full', new vscode.ThemeColor('charts.yellow')); }
        if (q === 'silver') { return new vscode.ThemeIcon('circle-filled', new vscode.ThemeColor('charts.foreground')); }
        if (q === 'bronze') { return new vscode.ThemeIcon('circle-outline', new vscode.ThemeColor('charts.orange')); }
        switch (this.artifact.type) {
            case 'sow': return new vscode.ThemeIcon('file-text', new vscode.ThemeColor('charts.blue'));
            case 'decision': return new vscode.ThemeIcon('symbol-structure', new vscode.ThemeColor('charts.yellow'));
            case 'requirement': return new vscode.ThemeIcon('list-unordered', new vscode.ThemeColor('charts.green'));
            case 'personas': return new vscode.ThemeIcon('person', new vscode.ThemeColor('charts.purple'));
            case 'stories': return new vscode.ThemeIcon('list-tree', new vscode.ThemeColor('charts.orange'));
            case 'scenario': return new vscode.ThemeIcon('beaker', new vscode.ThemeColor('charts.red'));
            default: return new vscode.ThemeIcon('file');
        }
    }
}

type TreeNode = CategoryNode | ArtifactNode;

export class ArtifactTreeProvider implements vscode.TreeDataProvider<TreeNode> {
    private _onDidChangeTreeData = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private hideAccepted = false;
    private showKBStatus = true;
    private artifacts: Artifact[] = [];
    private treeView?: vscode.TreeView<TreeNode>;
    private artifactNodeMap: Map<string, ArtifactNode> = new Map();
    private categoryNodeMap: Map<ArtifactType, CategoryNode> = new Map();

    /** Tracks KB ingestion state per artifact ID */
    private kbStateMap: Map<string, ArtifactKBState> = new Map();

    constructor(private readonly data: DataService, private readonly client?: SdlicitClient) {
        this.loadArtifacts();
        // Sync KB status on startup (non-blocking, no auto-ingest — that waits for confirmed connection)
        this.syncKBStatus(false);
    }

    setTreeView(view: vscode.TreeView<TreeNode>): void {
        this.treeView = view;
    }

    async revealArtifact(artifactId: string): Promise<void> {
        const node = this.artifactNodeMap.get(artifactId);
        if (node && this.treeView) {
            try { await this.treeView.reveal(node, { select: true, focus: false, expand: true }); }
            catch { /* node may not be visible */ }
        }
    }

    getParent(element: TreeNode): TreeNode | undefined {
        if (element instanceof ArtifactNode) {
            return this.categoryNodeMap.get(element.artifact.type);
        }
        return undefined;
    }

    toggleHideAccepted(): void {
        this.hideAccepted = !this.hideAccepted;
        this._onDidChangeTreeData.fire();
    }

    /** Toggle KB status visibility in tree items. */
    toggleShowKBStatus(): void {
        this.showKBStatus = !this.showKBStatus;
        this._onDidChangeTreeData.fire();
    }

    get isShowingKBStatus(): boolean {
        return this.showKBStatus;
    }

    /** Mark an artifact as currently being ingested into KB. */
    markIngesting(artifactId: string): void {
        this.kbStateMap.set(artifactId, 'ingesting');
        this._onDidChangeTreeData.fire();
    }

    /** Mark an artifact as successfully ingested into KB. */
    markIngested(artifactId: string): void {
        this.kbStateMap.set(artifactId, 'ingested');
        this._onDidChangeTreeData.fire();
    }

    /** Mark an artifact as having failed KB ingestion. */
    markIngestError(artifactId: string): void {
        this.kbStateMap.set(artifactId, 'error');
        this._onDidChangeTreeData.fire();
    }

    /** Get KB state for an artifact. */
    getKBState(artifactId: string): ArtifactKBState {
        return this.kbStateMap.get(artifactId) ?? 'none';
    }

    refresh(): void {
        this.loadArtifacts();
        this.syncKBStatus(false);
    }

    /** Sync KB ingestion state from the backend for all artifacts.
     *  Only updates artifacts not currently in 'ingesting' state (to avoid
     *  overwriting an in-flight spinner).
     *  Optionally auto-ingests any artifacts not yet in KB.
     */
    async syncKBStatus(autoIngest = true): Promise<void> {
        if (!this.client) { return; }
        try {
            const result = await this.client.getArtifactKBStatus();
            // Build a set of artifact names that are ingested in the backend
            const ingestedSet = new Set<string>();
            for (const entry of result.artifacts) {
                // Match: artifact id is the filename without extension
                // Backend name field is the same filename stem
                ingestedSet.add(entry.name);
            }
            let changed = false;
            const missingArtifacts: Artifact[] = [];
            for (const artifact of this.artifacts) {
                const currentState = this.kbStateMap.get(artifact.id);
                // Don't overwrite an in-flight ingesting state
                if (currentState === 'ingesting') { continue; }
                const isIngested = ingestedSet.has(artifact.id);
                const newState: ArtifactKBState = isIngested ? 'ingested' : 'none';
                if (currentState !== newState) {
                    this.kbStateMap.set(artifact.id, newState);
                    changed = true;
                }
                if (!isIngested && autoIngest) {
                    missingArtifacts.push(artifact);
                }
            }
            if (changed) {
                this._onDidChangeTreeData.fire();
            }
            // Auto-ingest missing artifacts in the background
            if (autoIngest && missingArtifacts.length > 0) {
                this.autoIngestMissing(missingArtifacts);
            }
        } catch {
            // Backend may not be running — ignore
        }
    }

    /** Auto-ingest artifacts that exist on disk but are not yet in KB. */
    private async autoIngestMissing(artifacts: Artifact[]): Promise<void> {
        if (!this.client) { return; }
        const typeMap: Record<string, string> = {
            sow: 'sow', decision: 'adr', requirement: 'srs',
            personas: 'personas', stories: 'stories', scenario: 'gherkin',
        };
        for (const artifact of artifacts) {
            const content = this.data.getArtifactContent(artifact.filePath);
            if (!content?.trim()) { continue; }
            const backendType = typeMap[artifact.type] ?? artifact.type;
            this.kbStateMap.set(artifact.id, 'ingesting');
            this._onDidChangeTreeData.fire();
            try {
                await this.client.ingestArtifact(content, backendType, artifact.id, true);
                this.kbStateMap.set(artifact.id, 'ingested');
            } catch {
                this.kbStateMap.set(artifact.id, 'error');
            }
            this._onDidChangeTreeData.fire();
        }
    }

    private loadArtifacts(): void {
        this.artifacts = this.data.getArtifacts();
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: TreeNode): vscode.TreeItem { return element; }

    getChildren(element?: TreeNode): TreeNode[] {
        if (!element) {
            this.artifactNodeMap.clear();
            this.categoryNodeMap.clear();

            if (this.artifacts.length === 0) { return []; }

            const categories: { type: ArtifactType; label: string }[] = [
                { type: 'sow', label: 'Statements of Work' },
                { type: 'requirement', label: 'Requirements' },
                { type: 'decision', label: 'Decisions (ADRs)' },
                { type: 'personas', label: 'Personas' },
                { type: 'stories', label: 'User Stories' },
                { type: 'scenario', label: 'BDD Scenarios' },
            ];

            return categories
                .map(c => {
                    let items = this.artifacts.filter(a => a.type === c.type);
                    if (this.hideAccepted) { items = items.filter(a => a.status !== 'accepted'); }
                    const node = new CategoryNode(c.type, c.label, items);
                    this.categoryNodeMap.set(c.type, node);
                    return node;
                })
                .filter(c => c.artifacts.length > 0);
        }

        if (element instanceof CategoryNode) {
            return element.artifacts.map(a => {
                const kbState = this.kbStateMap.get(a.id) ?? 'none';
                const node = new ArtifactNode(a, kbState, this.showKBStatus);
                this.artifactNodeMap.set(a.id, node);
                return node;
            });
        }

        return [];
    }
}
