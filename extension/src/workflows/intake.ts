// ---------------------------------------------------------------------------
// Sdlicit — Intake Workflow (SOW Creation)
// ---------------------------------------------------------------------------
// User provides a raw brief → calls /intake/sow/stream (SSE) → shows
// sections incrementally in the SOW Panel webview.  Each section appears
// as it's generated, with inline Socratic probes and KB verification
// badges.  Accept / Decline / Regenerate in the panel footer.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { SdlicitClient } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { KBSyncService } from '../services/kbSyncService';
import { ArtifactTreeProvider } from '../providers/artifactTreeProvider';
import { SOWPanelProvider } from '../providers/sowPanelProvider';
import { DataService } from '../services/dataService';

export async function runCreateSOW(
    client: SdlicitClient,
    store: ArtifactStore,
    kbSync?: KBSyncService,
    globalStoragePath?: string,
    artifactTree?: ArtifactTreeProvider,
    options?: { guidedFlow?: boolean },
    dataService?: DataService,
): Promise<void> {
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
