// ---------------------------------------------------------------------------
// Sdlicit — Composing Workflow (ADR Creation + Suggest Directions)
// ---------------------------------------------------------------------------
// ADR creation now uses the ADR panel provider (section-by-section webview).
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SdlicitClient } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { KBSyncService } from '../services/kbSyncService';
import { DataService } from '../services/dataService';
import { ADRPanelProvider } from '../providers/adrPanelProvider';

export async function runCreateADR(
    client: SdlicitClient,
    store: ArtifactStore,
    projectDir: string,
    kbSync?: KBSyncService,
    globalStoragePath?: string,
    dataService?: DataService,
): Promise<void> {
    const panel = new ADRPanelProvider(client, store, kbSync, globalStoragePath, dataService);
    const result = await panel.startCreation();

    if (result === 'accepted') {
        // After saving, ask if user wants to create another
        const next = await vscode.window.showQuickPick(
            [
                { label: '$(add) Create another ADR', action: 'another' as const },
                { label: '$(code) Start implementing', action: 'implement' as const },
                { label: '$(close) Done', action: 'done' as const },
            ],
            { title: 'Sdlicit — ADR Saved', placeHolder: 'What next?' },
        );

        if (next?.action === 'another') {
            return runCreateADR(client, store, projectDir, kbSync, globalStoragePath, dataService);
        }
    }
}

export async function runSuggestDirections(client: SdlicitClient, store: ArtifactStore, projectDir: string): Promise<void> {
    // Get the SOW/brief as context
    const brief = store.getLatestSOW();
    if (!brief) {
        vscode.window.showWarningMessage('Sdlicit: No SOW found. Create one first (Intake → Create SOW).');
        return;
    }

    const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'Sdlicit: Analyzing for ADR directions…' },
        () => client.suggestDirections(brief, projectDir),
    );

    if (!result.directions || result.directions.length === 0) {
        vscode.window.showInformationMessage('Sdlicit: No ADR directions suggested at this time.');
        return;
    }

    // Show as QuickPick
    const items = result.directions.map(d => ({
        label: `$(lightbulb) ${d.title}`,
        description: d.priority,
        detail: d.rationale,
        direction: d,
    }));

    const picked = await vscode.window.showQuickPick(items, {
        title: 'Sdlicit — Suggested ADR Directions',
        placeHolder: 'Select a direction to start an ADR, or Escape to dismiss',
        canPickMany: false,
    });

    if (picked) {
        vscode.window.showInformationMessage(`Sdlicit: Starting ADR for "${picked.direction.title}"…`);
        // Could auto-launch ADR wizard with pre-filled context
    }
}
