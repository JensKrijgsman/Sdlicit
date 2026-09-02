// ---------------------------------------------------------------------------
// Sdlicit VSCode Extension — Main Entry Point
// ---------------------------------------------------------------------------
// Wires: SdlicitClient → ServerLifecycle → DataService → Providers/Workflows
// Layout:
//   Primary sidebar (left): Artifacts tree + Dashboard + Knowledge Browser + Sessions tree
//   Panel (bottom): Chat + Status panel (tokens, model, quick actions)
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import { SdlicitClient } from './services/sdlicitClient';
import { ServerLifecycle } from './services/serverLifecycle';
import { SessionManager } from './services/sessionManager';
import { StatusBarManager } from './services/statusBar';
import { DataService } from './services/dataService';
import { KBSyncService } from './services/kbSyncService';
import { ArtifactTreeProvider } from './providers/artifactTreeProvider';
import { CanvasProvider } from './providers/canvasProvider';
import { DashboardProvider } from './providers/dashboardProvider';
import { ChatPanelProvider } from './providers/chatPanelProvider';
import { SessionTreeProvider } from './providers/sessionTreeProvider';
import { StatusPanelProvider } from './providers/statusPanelProvider';
import { TraceGraphProvider } from './providers/traceGraphProvider';
import { KnowledgeBrowserProvider } from './providers/knowledgeBrowserProvider';
import { KBDecorationProvider } from './providers/kbDecorationProvider';
import { PendingArtifactLensProvider } from './providers/pendingArtifactLens';
import { TraceLensProvider } from './providers/traceLensProvider';
import { TraceHoverProvider } from './providers/traceHoverProvider';
import { TraceCoverageDecorationProvider } from './providers/traceCoverageProvider';
import { runCreateSOW, setLensProvider } from './workflows/intake';
import { runCreateADR, runSuggestDirections } from './workflows/composing';
import { runGenerateSRS, runGeneratePersonas, runGenerateStories, runGenerateGherkin } from './workflows/generation';
import { runQueryKB, runIngestKB, runExpandADR } from './workflows/expansion';
import { runGuidedFlow } from './workflows/guided';
import { ArtifactStore } from './services/artifactStore';
import { SessionEvent } from './types';

export function activate(context: vscode.ExtensionContext) {
    // --- Core Services ---
    const client = new SdlicitClient();
    const lifecycle = new ServerLifecycle(client);
    const session = new SessionManager(client);
    const statusBar = new StatusBarManager(client, lifecycle);
    const data = new DataService(client);
    const kbSync = new KBSyncService(client);
    const statusPanel = new StatusPanelProvider(client, lifecycle);
    context.subscriptions.push({ dispose: () => statusBar.dispose() });
    context.subscriptions.push({ dispose: () => kbSync.dispose() });
    context.subscriptions.push(client.outputChannel);

    // Workspace root
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const store = workspaceRoot ? new ArtifactStore(workspaceRoot) : undefined;

    // Initialize KB sync with project dir
    if (workspaceRoot) {
        kbSync.setProjectDir(workspaceRoot);
    }

    // --- Connect to backend on activation ---
    if (workspaceRoot) {
        lifecycle.connect(workspaceRoot).then(async (connected) => {
            if (connected) {
                try {
                    const config = await client.getConfig();
                    statusBar.setModel(config.model);
                    statusPanel.setModel(config.model);
                    await session.start(workspaceRoot);
                    // Sync KB ingestion status once backend is confirmed up
                    artifactTree.syncKBStatus();
                } catch (err: any) {
                    console.warn('Sdlicit: post-connect setup failed:', err.message);
                }
            }
        });
    }

    // --- Providers ---
    const artifactTree = new ArtifactTreeProvider(data, client);
    const canvas = new CanvasProvider(data);

    // Sync KB status whenever server reconnects
    lifecycle.onStateChange(state => {
        if (state === 'connected') {
            artifactTree.syncKBStatus();
        }
    });
    const dashboard = new DashboardProvider(data);
    const chatPanel = new ChatPanelProvider(data, context);
    const sessionTree = new SessionTreeProvider(data);
    const traceGraph = new TraceGraphProvider(data, client);
    const knowledgeBrowser = new KnowledgeBrowserProvider(kbSync);
    const kbDecoration = new KBDecorationProvider(kbSync);
    const traceCoverage = new TraceCoverageDecorationProvider(client, data);
    context.subscriptions.push(traceCoverage);

    // --- Tree Views ---
    const artifactTreeView = vscode.window.createTreeView('sdlicit.artifacts', {
        treeDataProvider: artifactTree,
        showCollapseAll: true,
    });
    artifactTree.setTreeView(artifactTreeView);
    context.subscriptions.push(artifactTreeView);

    const sessionTreeView = vscode.window.createTreeView('sdlicit.sessions', {
        treeDataProvider: sessionTree,
        showCollapseAll: true,
    });
    context.subscriptions.push(sessionTreeView);

    const knowledgeTreeView = vscode.window.createTreeView('sdlicit.knowledgeBrowser', {
        treeDataProvider: knowledgeBrowser,
        showCollapseAll: false,
    });
    context.subscriptions.push(knowledgeTreeView);

    // --- Webview View Providers ---
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('sdlicit.dashboard', dashboard),
        vscode.window.registerWebviewViewProvider('sdlicit.chatPanel', chatPanel),
        vscode.window.registerWebviewViewProvider('sdlicit.statusPanel', statusPanel),
    );

    // --- File Decoration Provider ---
    context.subscriptions.push(
        vscode.window.registerFileDecorationProvider(kbDecoration),
    );

    // --- Read-only Source Preview Provider ---
    const sourcePreviewContents = new Map<string, string>();
    const sourcePreviewProvider = new class implements vscode.TextDocumentContentProvider {
        private _onDidChange = new vscode.EventEmitter<vscode.Uri>();
        readonly onDidChange = this._onDidChange.event;
        provideTextDocumentContent(uri: vscode.Uri): string {
            return sourcePreviewContents.get(uri.toString()) ?? '';
        }
        update(uri: vscode.Uri, content: string): void {
            sourcePreviewContents.set(uri.toString(), content);
            this._onDidChange.fire(uri);
        }
    };
    context.subscriptions.push(
        vscode.workspace.registerTextDocumentContentProvider('sdlicit-preview', sourcePreviewProvider),
    );
    // Expose to ChatPanelProvider
    chatPanel.setSourcePreviewProvider(sourcePreviewProvider, sourcePreviewContents);

    // --- Pending Artifact CodeLens Provider (inline Accept/Decline like Copilot) ---
    const pendingLens = new PendingArtifactLensProvider();
    setLensProvider(pendingLens);
    context.subscriptions.push(pendingLens);
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            { scheme: 'file', pattern: '**/.sdlicit/artifacts/**' },
            pendingLens,
        ),
    );

    // --- Trace Link CodeLens & Hover Providers ---
    const traceLens = new TraceLensProvider(data, client);
    const traceHover = new TraceHoverProvider(data);
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            [
                { scheme: 'file', pattern: '**/.sdlicit/artifacts/**/*.md' },
                { scheme: 'file', pattern: '**/.sdlicit/artifacts/**/*.feature' },
            ],
            traceLens,
        ),
        vscode.languages.registerHoverProvider(
            [
                { scheme: 'file', pattern: '**/.sdlicit/**/*.md' },
                { scheme: 'file', pattern: '**/.sdlicit/**/*.feature' },
            ],
            traceHover,
        ),
    );

    // --- File Watcher ---
    if (workspaceRoot) {
        const watcher = vscode.workspace.createFileSystemWatcher(
            new vscode.RelativePattern(workspaceRoot, '.sdlicit/**'),
        );
        watcher.onDidCreate(() => { artifactTree.refresh(); sessionTree.refresh(); knowledgeBrowser.refresh(); });
        watcher.onDidChange(() => { artifactTree.refresh(); sessionTree.refresh(); knowledgeBrowser.refresh(); });
        watcher.onDidDelete(() => { artifactTree.refresh(); sessionTree.refresh(); knowledgeBrowser.refresh(); });
        context.subscriptions.push(watcher);
    }

    // --- Helper: wrap backend calls with status bar spinner + show output ---
    async function withSpinner<T>(fn: () => Promise<T>): Promise<T> {
        statusBar.startCall();
        client.outputChannel.show(true); // reveal output channel (preserveFocus)
        try {
            return await fn();
        } finally {
            statusBar.endCall();
        }
    }

    // --- Commands ---

    // Refresh
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.refresh', () => {
            artifactTree.refresh();
            sessionTree.refresh();
            dashboard.refresh();
        }),
    );

    // Open Canvas (artifact by ID) — routes SOW/ADR to dedicated panels
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.openCanvas', async (artifactId: string) => {
            if (!artifactId) {
                const artifacts = data.getArtifacts();
                const items = artifacts.map(a => ({ label: a.title, description: a.id, id: a.id }));
                const picked = await vscode.window.showQuickPick(items, { title: 'Open Artifact' });
                if (!picked) { return; }
                artifactId = picked.id;
            }
            // Route SOW, ADR, and SRS to their dedicated panels
            let artifact = data.getArtifact(artifactId);

            // If not found by exact ID, try fuzzy matching for requirement IDs
            // (REQ-xxx may be embedded within the SRS file, not a standalone artifact)
            if (!artifact) {
                const allArtifacts = data.getArtifacts();
                // Try case-insensitive match
                artifact = allArtifacts.find(a => a.id.toLowerCase() === artifactId.toLowerCase());
                // Try matching requirement ID within SRS content
                if (!artifact && /^(REQ|FR|NFR)-/i.test(artifactId)) {
                    // Find the SRS artifact and open it, scrolling to the requirement
                    const srsArtifact = allArtifacts.find(a => a.type === 'requirement');
                    if (srsArtifact) {
                        const uri = vscode.Uri.file(srsArtifact.filePath);
                        const doc = await vscode.workspace.openTextDocument(uri);
                        const editor = await vscode.window.showTextDocument(doc, { preview: true });
                        // Search for the requirement ID in the document and highlight it
                        const text = doc.getText();
                        const reqIdx = text.indexOf(artifactId);
                        if (reqIdx >= 0) {
                            const pos = doc.positionAt(reqIdx);
                            const endPos = doc.positionAt(reqIdx + artifactId.length);
                            editor.selection = new vscode.Selection(pos, endPos);
                            editor.revealRange(new vscode.Range(pos, endPos), vscode.TextEditorRevealType.InCenter);
                        }
                        return;
                    }
                }
                if (!artifact) {
                    vscode.window.showWarningMessage(`Sdlicit: Artifact "${artifactId}" not found.`);
                    return;
                }
            }

            if (artifact?.type === 'sow') {
                return vscode.commands.executeCommand('sdlicit.viewSOW', artifact.filePath);
            }
            if (artifact?.type === 'decision') {
                return vscode.commands.executeCommand('sdlicit.viewADR', artifact.filePath);
            }
            if (artifact?.type === 'requirement') {
                return vscode.commands.executeCommand('sdlicit.viewSRS', artifact.filePath);
            }
            if (artifact?.type === 'personas') {
                return vscode.commands.executeCommand('sdlicit.viewPersonas', artifact.filePath);
            }
            if (artifact?.type === 'stories') {
                return vscode.commands.executeCommand('sdlicit.viewStories', artifact.filePath);
            }
            if (artifact?.type === 'scenario') {
                return vscode.commands.executeCommand('sdlicit.viewBDD', artifact.filePath);
            }
            await canvas.openArtifact(artifactId);
        }),
    );

    // Open artifact file (markdown) from tree
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.openArtifact', async (filePath: string) => {
            if (!filePath) { return; }
            const uri = vscode.Uri.file(filePath);
            const doc = await vscode.workspace.openTextDocument(uri);
            await vscode.window.showTextDocument(doc, { preview: true });
        }),
    );

    // Toggle canvas view mode
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.toggleViewMode', () => {
            canvas.toggleActiveViewMode();
        }),
    );

    // Open markdown source of active canvas artifact
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.openMarkdownSource', () => {
            canvas.openActiveInMarkdown();
        }),
    );

    // New Artifact
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.newArtifact', async () => {
            if (!store || !workspaceRoot) { return showNoWorkspace(); }
            const items: (vscode.QuickPickItem & { action: string })[] = [
                { label: '$(file-text) Statement of Work (SOW)', description: 'From a raw project brief', action: 'sow' },
                { label: '$(symbol-structure) Architecture Decision Record', description: 'Document a decision', action: 'adr' },
                { label: '$(list-unordered) Software Requirements Specification', description: 'From SOW', action: 'srs' },
                { label: '$(person) User Personas', description: 'From SRS context', action: 'personas' },
                { label: '$(list-tree) User Stories', description: 'From personas + requirements', action: 'stories' },
                { label: '$(beaker) BDD Scenarios (Gherkin)', description: 'From stories/requirements', action: 'gherkin' },
            ];
            const choice = await vscode.window.showQuickPick(items, {
                title: 'Sdlicit — New Artifact',
                placeHolder: 'What would you like to create?',
            });
            if (!choice) { return; }
            session.logEvent(`${choice.action}_create`, { source: 'new_artifact_menu' });
            await withSpinner(async () => {
                switch (choice.action) {
                    case 'sow': return runCreateSOW(client, store, kbSync, context.globalStorageUri.fsPath, artifactTree, undefined, data);
                    case 'adr': return runCreateADR(client, store, workspaceRoot, kbSync, context.globalStorageUri.fsPath, data);
                    case 'srs': return runGenerateSRS(client, store, workspaceRoot, kbSync, context.globalStorageUri.fsPath, artifactTree);
                    case 'personas': return runGeneratePersonas(client, store, workspaceRoot, kbSync, context.globalStorageUri.fsPath, artifactTree);
                    case 'stories': return runGenerateStories(client, store, workspaceRoot, kbSync, context.globalStorageUri.fsPath, artifactTree);
                    case 'gherkin': return runGenerateGherkin(client, store, workspaceRoot, artifactTree);
                }
            });
            session.logEvent(`${choice.action}_complete`);
            artifactTree.refresh();
        }),
    );

    // Guided flow
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.guidedFlow', async () => {
            if (!store || !workspaceRoot) { return showNoWorkspace(); }
            session.logEvent('guided_flow_start');
            await withSpinner(() => runGuidedFlow(client, store, workspaceRoot, kbSync));
            session.logEvent('guided_flow_end');
            artifactTree.refresh();
        }),
    );

    // Suggest ADR directions
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.suggestDirections', async () => {
            if (!store || !workspaceRoot) { return showNoWorkspace(); }
            session.logEvent('suggest_directions_start');
            await withSpinner(() => runSuggestDirections(client, store, workspaceRoot));
            session.logEvent('suggest_directions_complete');
        }),
    );

    // Expand/Review ADR
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.expandADR', async () => {
            if (!store || !workspaceRoot) { return showNoWorkspace(); }
            session.logEvent('expand_adr_start');
            await withSpinner(() => runExpandADR(client, store, workspaceRoot));
            session.logEvent('expand_adr_complete');
        }),
    );

    // Query Knowledge Base
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.queryKB', async () => {
            session.logEvent('kb_query_start');
            await withSpinner(() => runQueryKB(client));
            session.logEvent('kb_query_complete');
        }),
    );

    // Ingest Knowledge Base
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.ingestKB', async () => {
            if (!workspaceRoot) { return showNoWorkspace(); }
            session.logEvent('kb_ingest_start');
            await withSpinner(() => runIngestKB(client, workspaceRoot, kbSync));
            session.logEvent('kb_ingest_complete');
        }),
    );

    // BDD Generation (panel-based)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.generateScenarios', async () => {
            if (!store || !workspaceRoot) { return showNoWorkspace(); }
            session.logEvent('bdd_generate_start');
            await withSpinner(() => runGenerateGherkin(client, store, workspaceRoot, artifactTree));
            session.logEvent('bdd_generate_complete');
            artifactTree.refresh();
        }),
    );

    // Show event detail (from session tree) — read-only virtual document
    const eventScheme = 'sdlicit-event';
    const eventContentProvider = new class implements vscode.TextDocumentContentProvider {
        private contents = new Map<string, string>();
        setContent(uri: vscode.Uri, content: string) { this.contents.set(uri.toString(), content); }
        provideTextDocumentContent(uri: vscode.Uri): string { return this.contents.get(uri.toString()) ?? ''; }
    }();
    context.subscriptions.push(
        vscode.workspace.registerTextDocumentContentProvider(eventScheme, eventContentProvider),
    );
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.showEventDetail', async (event: SessionEvent) => {
            const content = JSON.stringify(event, null, 2);
            const uri = vscode.Uri.parse(`${eventScheme}:event-${event.seq}-${event.kind}.json`);
            eventContentProvider.setContent(uri, content);
            const doc = await vscode.workspace.openTextDocument(uri);
            await vscode.window.showTextDocument(doc, { preview: true });
        }),
    );

    // Delete session (removes all files from disk)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.deleteSession', async (node: any) => {
            const sessionId: string | undefined = node?.session?.session_id;
            if (!sessionId) { return; }
            const confirm = await vscode.window.showWarningMessage(
                `Delete session ${sessionId.slice(0, 8)}… and all its data?`,
                { modal: true },
                'Delete',
            );
            if (confirm !== 'Delete') { return; }
            data.deleteSession(sessionId);
            sessionTree.refresh();
            vscode.window.showInformationMessage(`Session ${sessionId.slice(0, 8)}… deleted.`);
        }),
    );

    // Session compact
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.compactSession', async () => {
            const result = await withSpinner(() => session.compact());
            if (result?.status === 'ok') {
                vscode.window.showInformationMessage('Sdlicit: Session compacted.');
            }
        }),
    );

    // Save preference
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.savePreference', async () => {
            const key = await vscode.window.showInputBox({ prompt: 'Preference key (e.g., style, verbosity)' });
            if (!key) { return; }
            const value = await vscode.window.showInputBox({ prompt: `Value for "${key}"` });
            if (!value) { return; }
            await session.savePreference(key, value);
            vscode.window.showInformationMessage(`Sdlicit: Preference saved — ${key}=${value}`);
        }),
    );

    // Start/restart server
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.startServer', async () => {
            if (!workspaceRoot) { return showNoWorkspace(); }
            await lifecycle.startServer(workspaceRoot);
        }),
    );

    // Show token details
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.showTokenDetails', () => {
            const usage = client.totalUsage;
            const lines = [
                `Total: ${usage.total} tokens (${usage.calls} calls)`,
                `Prompt: ${usage.prompt} | Completion: ${usage.completion}`,
                `Subagents: ${Object.keys(usage.byAgent).length}`,
            ];
            for (const [agent, u] of Object.entries(usage.byAgent)) {
                lines.push(`  ${agent}: ${u.total} (${u.calls} call${u.calls !== 1 ? 's' : ''}, p:${u.prompt} c:${u.completion})`);
            }
            vscode.window.showInformationMessage(lines.join('\n'));
        }),
    );

    // Toggle hide accepted in tree
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.toggleHideAccepted', () => {
            artifactTree.toggleHideAccepted();
        }),
    );

    // Toggle KB ingestion status display in artifact tree
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.toggleKBStatus', () => {
            artifactTree.toggleShowKBStatus();
        }),
    );

    // Artifact KB state commands (used by panel providers during ingestion)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.markArtifactIngesting', (artifactId: string) => {
            artifactTree.refresh();
            artifactTree.markIngesting(artifactId);
        }),
        vscode.commands.registerCommand('sdlicit.markArtifactIngested', (artifactId: string) => {
            artifactTree.markIngested(artifactId);
        }),
        vscode.commands.registerCommand('sdlicit.markArtifactIngestError', (artifactId: string) => {
            artifactTree.markIngestError(artifactId);
        }),
    );

    // Upload artifact to KB from tree item hover button
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.uploadArtifactToKB', async (node: any) => {
            if (!node?.artifact) { return; }
            const artifact = node.artifact as import('./types').Artifact;
            const content = data.getArtifactContent(artifact.filePath);
            if (!content) {
                vscode.window.showWarningMessage(`Cannot read artifact file: ${artifact.id}`);
                return;
            }
            // Map ArtifactType to backend artifact_type
            const typeMap: Record<string, string> = {
                sow: 'sow', decision: 'adr', requirement: 'srs',
                personas: 'personas', stories: 'stories', scenario: 'gherkin',
            };
            const backendType = typeMap[artifact.type] ?? artifact.type;
            // Use the filename stem as name (strip extension)
            const name = artifact.id;

            artifactTree.markIngesting(name);
            try {
                await client.ingestArtifact(content, backendType, name, true);
                artifactTree.markIngested(name);
                vscode.window.showInformationMessage(`Uploaded ${artifact.title} to Knowledge Base`);
            } catch (err: any) {
                artifactTree.markIngestError(name);
                vscode.window.showErrorMessage(`KB upload failed for ${artifact.title}: ${err.message ?? err}`);
            }
        }),
    );

    // Delete Artifact (disk + KB)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.deleteArtifact', async (node: any) => {
            if (!node?.artifact) { return; }
            const artifact = node.artifact as import('./types').Artifact;

            const confirm = await vscode.window.showWarningMessage(
                `Delete "${artifact.title}"? This will remove the file from disk and from the Knowledge Base.`,
                { modal: true },
                'Delete',
            );
            if (confirm !== 'Delete') { return; }

            // 1. Remove from disk
            try {
                if (fs.existsSync(artifact.filePath)) {
                    fs.unlinkSync(artifact.filePath);
                }
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to delete file: ${err.message ?? err}`);
                return;
            }

            // 2. Remove from KB
            const typeMap: Record<string, string> = {
                sow: 'sow', decision: 'adr', requirement: 'srs',
                personas: 'personas', stories: 'stories', scenario: 'gherkin',
            };
            const backendType = typeMap[artifact.type] ?? artifact.type;
            try {
                await client.deleteFromKB(backendType, artifact.id);
            } catch {
                // KB removal is best-effort — file is already gone from disk
            }

            // 3. Update tree
            artifactTree.refresh();
            vscode.window.showInformationMessage(`Deleted "${artifact.title}".`);
        }),
    );

    // Open Trace Graph
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.openTraceGraph', () => {
            traceGraph.open();
        }),
    );

    // Check Traceability for an artifact
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.checkTraceability', async (artifactId?: string) => {
            if (!artifactId) {
                const artifacts = data.getArtifacts();
                const picked = await vscode.window.showQuickPick(
                    artifacts.map(a => ({ label: a.id, description: a.title })),
                    { title: 'Select artifact to check' },
                );
                if (!picked) { return; }
                artifactId = picked.label;
            }
            await withSpinner(async () => {
                const result = await client.checkTraceability(artifactId!, workspaceRoot);
                if (result.issues.length === 0) {
                    vscode.window.showInformationMessage(`✓ No traceability issues for ${artifactId}`);
                } else {
                    const items = result.issues.map(i => `[${i.severity}] ${i.message}`);
                    const msg = `${result.issues.length} issue(s) found for ${artifactId}`;
                    const action = await vscode.window.showWarningMessage(msg, 'Show Details');
                    if (action === 'Show Details') {
                        const doc = await vscode.workspace.openTextDocument({
                            content: items.join('\n'),
                            language: 'markdown',
                        });
                        vscode.window.showTextDocument(doc, { preview: true });
                    }
                }
            });
        }),
    );

    // Show server logs (reveal the server terminal)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.showLogs', () => {
            lifecycle.revealTerminal();
        }),
    );

    // Show output channel (backend activity log)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.showOutput', () => {
            client.outputChannel.show();
        }),
    );

    // --- Bidirectional Chat <-> Panel Commands ---

    // Chat with section context (called from SOW, ADR, or Canvas panels)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.chatWithContext', (ctx: {
            panelId?: string; panelType?: string;
            sectionKey: string; sectionHeading: string; context: string;
        }) => {
            const panelId = ctx.panelId ?? 'sow';
            const panelType = (ctx.panelType ?? 'sow') as 'sow' | 'adr' | 'canvas';
            chatPanel.receiveContextFromPanel(panelId, panelType, ctx.sectionKey, ctx.sectionHeading, ctx.context);
        }),
    );

    // Track active creation panels for bidirectional insert
    let activeSowPanel: import('./providers/sowPanelProvider').SOWPanelProvider | undefined;
    let activeAdrPanel: import('./providers/adrPanelProvider').ADRPanelProvider | undefined;

    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.setActiveSowPanel', (panel: import('./providers/sowPanelProvider').SOWPanelProvider) => {
            activeSowPanel = panel;
            // Register SOW panel sections in chat
            chatPanel.registerActivePanel('sow', {
                panelType: 'sow',
                panelLabel: 'SOW',
                sections: panel.getSections().map(s => ({ key: s.key, heading: s.heading })),
            });
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.setActiveAdrPanel', (panel: import('./providers/adrPanelProvider').ADRPanelProvider) => {
            activeAdrPanel = panel;
            // Register ADR panel fields in chat
            chatPanel.registerActivePanel('adr', {
                panelType: 'adr',
                panelLabel: 'ADR',
                sections: panel.getFields().map(f => ({ key: f.key, heading: f.heading })),
            });
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.unregisterPanel', (panelId: string) => {
            chatPanel.unregisterActivePanel(panelId);
            if (panelId === 'sow') { activeSowPanel = undefined; }
            if (panelId === 'adr') { activeAdrPanel = undefined; }
        }),
    );

    // Insert content from chat into any active panel (called from chat panel)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.insertChatToPanel', (ctx: { panelId: string; sectionKey: string; content: string }) => {
            if (ctx.panelId === 'sow') {
                activeSowPanel?.updateSectionFromExternal(ctx.sectionKey, ctx.content);
            } else if (ctx.panelId === 'adr') {
                activeAdrPanel?.updateFieldFromExternal(ctx.sectionKey, ctx.content);
            } else if (ctx.panelId.startsWith('canvas-')) {
                // Canvas insert: update section via DataService
                const artifactId = ctx.panelId.replace('canvas-', '');
                data.updateSection(artifactId, ctx.sectionKey, ctx.content).then(() => {
                    vscode.window.showInformationMessage(`Sdlicit: Inserted into ${ctx.sectionKey}`);
                }).catch(() => {});
            }
        }),
    );

    // Legacy: Insert content from chat into SOW section
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.insertToSOWSection', (ctx: { sectionKey: string; content: string }) => {
            activeSowPanel?.updateSectionFromExternal(ctx.sectionKey, ctx.content);
        }),
    );

    // Open existing SOW in read-only panel
    let activeSrsPanel: import('./providers/srsPanelProvider').SRSPanelProvider | undefined;
    let activePersonasPanel: import('./providers/personasPanelProvider').PersonasPanelProvider | undefined;
    let activeStoriesPanel: import('./providers/storiesPanelProvider').StoriesPanelProvider | undefined;

    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.viewSOW', async (filePath?: string) => {
            if (!store) { return showNoWorkspace(); }
            // Reuse existing panel if alive
            if (activeSowPanel?.isAlive) { activeSowPanel.reveal(); return; }
            let sowMd: string | null = null;
            if (filePath) {
                const fsModule = await import('fs');
                if (fsModule.existsSync(filePath)) { sowMd = fsModule.readFileSync(filePath, 'utf-8'); }
            }
            if (!sowMd) {
                sowMd = store.getLatestSOW();
            }
            if (!sowMd) {
                vscode.window.showWarningMessage('Sdlicit: No SOW found.');
                return;
            }
            const { SOWPanelProvider } = await import('./providers/sowPanelProvider');
            const panel = new SOWPanelProvider(client, store, kbSync, context.globalStorageUri.fsPath, data);
            activeSowPanel = panel;
            vscode.commands.executeCommand('sdlicit.setActiveSowPanel', panel);
            await panel.openExisting(sowMd, filePath);
        }),
    );

    // Open existing ADR in read-only panel
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.viewADR', async (filePath?: string) => {
            if (!store) { return showNoWorkspace(); }
            // ADR: don't reuse since multiple ADRs can be open simultaneously
            let md: string | null = null;
            if (filePath) {
                const fs = await import('fs');
                if (fs.existsSync(filePath)) { md = fs.readFileSync(filePath, 'utf-8'); }
            }
            if (!md) {
                const adrs = store.getADRContents();
                if (adrs.length === 0) {
                    vscode.window.showWarningMessage('Sdlicit: No ADRs found.');
                    return;
                }
                md = adrs[adrs.length - 1];
            }
            const { ADRPanelProvider } = await import('./providers/adrPanelProvider');
            const panel = new ADRPanelProvider(client, store, kbSync, context.globalStorageUri.fsPath, data);
            vscode.commands.executeCommand('sdlicit.setActiveAdrPanel', panel);
            await panel.openExisting(md, filePath);
        }),
    );

    // Open existing SRS in read-only panel
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.viewSRS', async (filePath?: string) => {
            if (!store) { return showNoWorkspace(); }
            if (activeSrsPanel?.isAlive) { activeSrsPanel.reveal(); return; }
            let md: string | null = null;
            if (filePath) {
                const fsModule = await import('fs');
                if (fsModule.existsSync(filePath)) { md = fsModule.readFileSync(filePath, 'utf-8'); }
            }
            if (!md) {
                md = store.getLatestSRS();
            }
            if (!md) {
                vscode.window.showWarningMessage('Sdlicit: No SRS found.');
                return;
            }
            const { SRSPanelProvider } = await import('./providers/srsPanelProvider');
            const panel = new SRSPanelProvider(client, store, kbSync, context.globalStorageUri.fsPath, data);
            activeSrsPanel = panel;
            await panel.openExisting(md, filePath);
        }),
    );

    // Open existing Personas in read-only panel
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.viewPersonas', async (filePath?: string) => {
            if (!store) { return showNoWorkspace(); }
            if (activePersonasPanel?.isAlive) { activePersonasPanel.reveal(); return; }
            let md: string | null = null;
            if (filePath) {
                const fsModule = await import('fs');
                if (fsModule.existsSync(filePath)) { md = fsModule.readFileSync(filePath, 'utf-8'); }
            }
            if (!md) {
                const personasArtifacts = store.listArtifacts().filter(a => a.type === 'personas');
                // Prefer .json (primary format) over .md
                const jsonArtifact = personasArtifacts.find(a => a.filePath.endsWith('.json'));
                const target = jsonArtifact || personasArtifacts[personasArtifacts.length - 1];
                if (target) {
                    md = store.readArtifact(target.filePath);
                    filePath = target.filePath;
                }
            }
            if (!md) {
                vscode.window.showWarningMessage('Sdlicit: No Personas found.');
                return;
            }
            const { PersonasPanelProvider } = await import('./providers/personasPanelProvider');
            const panel = new PersonasPanelProvider(client, store, kbSync, context.globalStorageUri.fsPath);
            activePersonasPanel = panel;
            await panel.openExisting(md, filePath);
        }),
    );

    // View existing stories in panel
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.viewStories', async (filePath?: string) => {
            if (!store) { return showNoWorkspace(); }
            if (activeStoriesPanel?.isAlive) { activeStoriesPanel.reveal(); return; }
            let md: string | null = null;
            if (filePath) {
                const fsModule = await import('fs');
                if (fsModule.existsSync(filePath)) { md = fsModule.readFileSync(filePath, 'utf-8'); }
            }
            if (!md) {
                const storiesArtifacts = store.listArtifacts().filter(a => a.type === 'stories');
                // Prefer .json (primary format) over .md
                const jsonArtifact = storiesArtifacts.find(a => a.filePath.endsWith('.json'));
                const target = jsonArtifact || storiesArtifacts[storiesArtifacts.length - 1];
                if (target) {
                    md = store.readArtifact(target.filePath);
                    filePath = target.filePath;
                }
            }
            if (!md) {
                vscode.window.showWarningMessage('Sdlicit: No User Stories found.');
                return;
            }
            const { StoriesPanelProvider } = await import('./providers/storiesPanelProvider');
            const panel = new StoriesPanelProvider(client, store, kbSync, context.globalStorageUri.fsPath);
            activeStoriesPanel = panel;
            await panel.openExisting(md, filePath);
        }),
    );

    // View existing BDD/Gherkin in panel (single file)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.viewBDD', async (filePath?: string) => {
            if (!store) { return showNoWorkspace(); }
            let content: string | null = null;
            if (filePath) {
                const fsModule = await import('fs');
                if (fsModule.existsSync(filePath)) { content = fsModule.readFileSync(filePath, 'utf-8'); }
            }
            if (!content) {
                vscode.window.showWarningMessage('Sdlicit: Could not read BDD feature file.');
                return;
            }
            const { BddPanelProvider } = await import('./providers/bddPanelProvider');
            const bddPanel = new BddPanelProvider(client, store, kbSync, context.globalStorageUri.fsPath);
            await bddPanel.openExisting(content, filePath);
        }),
    );

    // BDD Overview — shows all feature files with syntax highlighting
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.viewBDDOverview', async () => {
            if (!store) { return showNoWorkspace(); }
            const bddArtifacts = store.listArtifacts().filter(a =>
                a.type === 'gherkin' || a.filePath.endsWith('.feature'),
            );
            if (bddArtifacts.length === 0) {
                vscode.window.showWarningMessage('Sdlicit: No BDD feature files found.');
                return;
            }
            const { BddOverviewProvider } = await import('./providers/bddOverviewProvider');
            const overview = new BddOverviewProvider(store, bddArtifacts);
            overview.show();
        }),
    );

    // Add a file to the knowledge folder
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.addToKnowledge', async () => {
            if (!workspaceRoot) { return showNoWorkspace(); }

            const uris = await vscode.window.showOpenDialog({
                canSelectFiles: true,
                canSelectMany: true,
                filters: { 'Documents': ['md', 'txt', 'feature', 'json', 'yaml', 'yml', 'pdf'] },
                title: 'Select files to add to Knowledge Base',
            });
            if (!uris || uris.length === 0) { return; }

            for (const uri of uris) {
                const destPath = await kbSync.addFile(uri);
                const fileName = uri.fsPath.split('/').pop() ?? '';

                const ingest = await vscode.window.showInformationMessage(
                    `Added "${fileName}" to knowledge folder. Ingest into RAG KB now?`,
                    'Yes, ingest',
                    'Later',
                );
                if (ingest === 'Yes, ingest') {
                    kbSync.ingestFileAsync(fileName);
                }
            }
            knowledgeBrowser.refresh();
        }),
    );

    // Ingest a specific KB file (from tree context menu)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.ingestKBFile', async (item: any) => {
            if (!item?.entry?.fileName) { return; }
            kbSync.ingestFileAsync(item.entry.fileName);
            vscode.window.showInformationMessage(`Sdlicit: Ingesting "${item.entry.fileName}" into KB…`);
        }),
    );

    // Ingest all pending files
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.ingestAllPending', () => {
            kbSync.ingestAllPending();
            vscode.window.showInformationMessage('Sdlicit: Ingesting all pending files into KB…');
        }),
    );

    // Delete an artifact from the KB
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.deleteFromKB', async (artifactType: string, name: string) => {
            const confirm = await vscode.window.showWarningMessage(
                `Delete "${name}" from the Knowledge Base?`,
                { modal: true },
                'Delete',
            );
            if (confirm === 'Delete') {
                await kbSync.deleteFromKB(name, artifactType);
                vscode.window.showInformationMessage(`Sdlicit: Removed "${name}" from KB.`);
            }
        }),
    );

    // Open a KB file (PDFs via vscode-pdf viewer, others as text)
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.openKBFile', async (filePath: string, page?: number) => {
            if (!filePath) { return; }
            const uri = vscode.Uri.file(filePath);
            if (filePath.toLowerCase().endsWith('.pdf')) {
                const pdfUri = page ? uri.with({ query: `page=${page}` }) : uri;
                try {
                    await vscode.commands.executeCommand('vscode.openWith', pdfUri, 'pdf.preview');
                } catch {
                    // Fallback: prompt user to install vscode-pdf
                    const action = await vscode.window.showWarningMessage(
                        'Sdlicit: PDF preview requires the "vscode-pdf" extension.',
                        'Install Extension', 'Open Raw',
                    );
                    if (action === 'Install Extension') {
                        vscode.commands.executeCommand('workbench.extensions.installExtension', 'tomoki1207.pdf');
                    } else if (action === 'Open Raw') {
                        await vscode.commands.executeCommand('vscode.open', pdfUri);
                    }
                }
            } else {
                const doc = await vscode.workspace.openTextDocument(uri);
                await vscode.window.showTextDocument(doc, { preview: true });
            }
        }),
    );

    // --- Pending Artifact Accept / Decline / Regenerate Commands ---
    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.acceptPendingArtifact', async (filePath: string) => {
            const artifact = pendingLens.getPending(filePath);
            if (!artifact) { return; }
            if (artifact.onAccept) { await artifact.onAccept(); }
            pendingLens.removePending(filePath);
            artifactTree.refresh();
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.declinePendingArtifact', async (filePath: string) => {
            const artifact = pendingLens.getPending(filePath);
            if (!artifact) { return; }
            if (artifact.onDecline) { await artifact.onDecline(); }
            pendingLens.removePending(filePath);
            artifactTree.refresh();
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('sdlicit.regeneratePendingArtifact', async (filePath: string) => {
            const artifact = pendingLens.getPending(filePath);
            if (!artifact || !artifact.onRegenerate) { return; }
            const notes = await vscode.window.showInputBox({
                title: 'Sdlicit — Regeneration Notes',
                prompt: 'What should be changed?',
                placeHolder: 'e.g., "Add more detail about constraints" or "Make scope narrower"',
            });
            if (!notes) { return; }
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'Sdlicit: Regenerating…' },
                () => artifact.onRegenerate!(notes),
            );
        }),
    );

    // --- Cleanup on deactivation ---
    context.subscriptions.push({
        dispose: async () => {
            await session.end();
            await lifecycle.dispose();
        },
    });
}

export function deactivate() {}

function showNoWorkspace(): void {
    vscode.window.showWarningMessage('Sdlicit: Open a workspace folder first.');
}
