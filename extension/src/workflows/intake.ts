// ---------------------------------------------------------------------------
// Sdlicit — Intake Workflow (SOW Creation)
// ---------------------------------------------------------------------------
// User provides a raw brief → calls /intake/sow/stream (SSE) → shows
// sections incrementally in the SOW Panel webview.  Each section appears
// as it's generated, with inline Socratic probes and KB verification
// badges.  Accept / Decline / Regenerate in the panel footer.
// Falls back to the old synchronous flow + CodeLens if SSE fails.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { SdlicitClient, Clarification, SocraticProbe } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { KBSyncService } from '../services/kbSyncService';
import { ArtifactTreeProvider } from '../providers/artifactTreeProvider';
import { SOWPanelProvider } from '../providers/sowPanelProvider';
import { PendingArtifactLensProvider } from '../providers/pendingArtifactLens';
import { DataService } from '../services/dataService';
import { handleSocraticProbe } from './socraticLoop';

/** Shared lens provider instance — set from extension.ts */
let _lensProvider: PendingArtifactLensProvider | undefined;
export function setLensProvider(provider: PendingArtifactLensProvider): void {
    _lensProvider = provider;
}

export async function runCreateSOW(
    client: SdlicitClient,
    store: ArtifactStore,
    kbSync?: KBSyncService,
    globalStoragePath?: string,
    artifactTree?: ArtifactTreeProvider,
    options?: { guidedFlow?: boolean },
    dataService?: DataService,
): Promise<void> {
    if (!_lensProvider) {
        vscode.window.showErrorMessage('Sdlicit: Internal error — lens provider not initialized.');
        return;
    }

    // 1. Get the raw brief — offer existing briefs from knowledge/ or paste new
    let brief: string | undefined;
    let briefIsExisting = false;

    if (kbSync) {
        const existingBriefs = _findExistingBriefs(kbSync);
        if (existingBriefs.length > 0) {
            const choices: (vscode.QuickPickItem & { action: string; content?: string })[] = [
                { label: '$(edit) Paste new brief', description: 'Type or paste a new project brief', action: 'new' },
                ...existingBriefs.map(b => ({
                    label: `$(file-text) ${b.name}`,
                    description: b.preview,
                    action: 'existing' as string,
                    content: b.content,
                })),
            ];
            const picked = await vscode.window.showQuickPick(choices, {
                title: 'Sdlicit — Select Project Brief',
                placeHolder: 'Use an existing brief or paste a new one',
            });
            if (!picked) { return; }
            if (picked.action === 'existing' && picked.content) {
                brief = picked.content;
                briefIsExisting = true;
            }
        }
    }

    if (!brief) {
        brief = await vscode.window.showInputBox({
            title: 'Sdlicit — Create Statement of Work',
            prompt: 'Paste or type the raw project brief',
            placeHolder: 'Describe the project, its goals, constraints, and deliverables…',
            ignoreFocusOut: true,
        });
    }
    if (!brief || brief.trim() === '') { return; }

    // 1b. Save the raw brief to .sdlicit/knowledge/ (only for NEW briefs, not re-used ones)
    if (kbSync && !briefIsExisting) {
        const timestamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
        const briefFilename = `raw_brief_${timestamp}.md`;
        const briefContent = `# Raw Project Brief\n\n_Captured: ${new Date().toISOString()}_\n\n---\n\n${brief}`;
        kbSync.saveToKnowledge(briefFilename, briefContent);
    }

    client.log('SOW: Starting incremental generation from brief');

    // 2. Use the SOW panel with SSE streaming
    const sowPanel = new SOWPanelProvider(client, store, kbSync, globalStoragePath, dataService);
    vscode.commands.executeCommand('sdlicit.setActiveSowPanel', sowPanel);
    const result = await sowPanel.startGeneration(brief, [], { guidedFlow: options?.guidedFlow });

    if (result === 'accepted') {
        client.log('SOW: Accepted by user');
    } else {
        client.log('SOW: Declined by user');
    }
}

/** Fallback: synchronous SOW creation (used if SSE is unavailable). */
export async function runCreateSOWSync(
    client: SdlicitClient,
    store: ArtifactStore,
    kbSync?: KBSyncService,
    brief?: string,
    globalStoragePath?: string,
    artifactTree?: ArtifactTreeProvider,
): Promise<void> {
    if (!_lensProvider || !brief) { return; }

    let clarifications: Clarification[] = [];
    let sowMarkdown: string | undefined;

    await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'Sdlicit: Generating SOW…', cancellable: true },
        async (progress, token) => {
            let iteration = 0;
            const maxIterations = 10;

            while (iteration < maxIterations && !token.isCancellationRequested) {
                iteration++;
                progress.report({ message: `Processing${clarifications.length > 0 ? ` (${clarifications.length} clarifications)` : ''}…` });

                const result = await client.createSOW(brief, clarifications);
                sowMarkdown = result.sow_markdown;

                if (result.socratic_probe) {
                    client.log(`SOW: Socratic probe received (turn ${iteration})`);
                    const probe = result.socratic_probe as SocraticProbe;
                    const probeResult = await handleSocraticProbe(probe, clarifications);
                    if (probeResult === null) { break; }
                    clarifications = probeResult.clarifications;
                    continue;
                }
                break;
            }
        },
    );

    if (!sowMarkdown) { return; }

    const filename = 'sow.md';
    const filePath = store.saveByMeta(
        { tag: 'SOW', filename, relative_path: 'sow.md', artifact_type: 'sow' },
        sowMarkdown,
    );
    const uri = vscode.Uri.file(filePath);
    const doc = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(doc, { preview: false, viewColumn: vscode.ViewColumn.One });

    return new Promise<void>((resolve) => {
        const pendingDisposable = _lensProvider!.addPending({
            filePath,
            uri,
            type: 'sow',
            brief,
            clarifications,
            onAccept: async () => {
                const latestContent = fs.readFileSync(filePath, 'utf-8');
                const artifactId = 'sow';
                artifactTree?.refresh();
                artifactTree?.markIngesting(artifactId);
                if (kbSync) {
                    kbSync.saveToKnowledge(filename, latestContent);
                    kbSync.ingestFileAsync(filename);
                } else {
                    try { await client.ingestArtifact(latestContent, 'sow', 'sow'); } catch { /* best effort */ }
                }
                artifactTree?.markIngested(artifactId);
                pendingDisposable.dispose();
                vscode.window.showInformationMessage(`Sdlicit: SOW accepted and ingested into KB — ${filename}`);
                resolve();
            },
            onDecline: async () => {
                pendingDisposable.dispose();
                for (const group of vscode.window.tabGroups.all) {
                    for (const tab of group.tabs) {
                        if (tab.input instanceof vscode.TabInputText && tab.input.uri.fsPath === filePath) {
                            await vscode.window.tabGroups.close(tab);
                        }
                    }
                }
                if (fs.existsSync(filePath)) { fs.unlinkSync(filePath); }
                vscode.window.showInformationMessage('Sdlicit: SOW discarded.');
                resolve();
            },
            onRegenerate: async (notes: string) => {
                clarifications.push({ question: 'Regeneration notes', answer: notes });
                const result = await client.createSOW(brief, clarifications);
                if (result.sow_markdown) {
                    fs.writeFileSync(filePath, result.sow_markdown, 'utf-8');
                    const newDoc = await vscode.workspace.openTextDocument(uri);
                    await vscode.window.showTextDocument(newDoc, { preview: false });
                    return result.sow_markdown;
                }
                return undefined;
            },
        });
    });
}

/** Scan knowledge/ for existing raw_brief_*.md files */
function _findExistingBriefs(kbSync: KBSyncService): Array<{ name: string; content: string; preview: string }> {
    const dir = kbSync.knowledgeDir;
    if (!dir || !fs.existsSync(dir)) { return []; }

    const results: Array<{ name: string; content: string; preview: string }> = [];
    const entries = fs.readdirSync(dir, { withFileTypes: true });

    for (const entry of entries) {
        if (!entry.isFile()) { continue; }
        if (!entry.name.startsWith('raw_brief') || !entry.name.endsWith('.md')) { continue; }
        try {
            const content = fs.readFileSync(path.join(dir, entry.name), 'utf-8');
            // Extract the actual brief (skip frontmatter)
            const briefText = content.replace(/^#.*\n+_Captured:.*_\n+---\n+/s, '').trim();
            const preview = briefText.slice(0, 80).replace(/\n/g, ' ') + (briefText.length > 80 ? '…' : '');
            results.push({ name: entry.name, content: briefText, preview });
        } catch { /* skip unreadable */ }
    }

    return results;
}
