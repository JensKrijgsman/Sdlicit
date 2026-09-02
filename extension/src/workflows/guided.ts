// ---------------------------------------------------------------------------
// Sdlicit — Guided Flow (Full Pipeline)
// ---------------------------------------------------------------------------
// Orchestrates: SOW → SRS → Personas → Stories → ADRs → Gherkin
// After each stage completes (accept/decline), asks user to continue.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SdlicitClient } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { KBSyncService } from '../services/kbSyncService';
import { DataService } from '../services/dataService';
import { runCreateSOW } from './intake';
import { runCreateADR, runSuggestDirections } from './composing';
import { runGenerateSRS, runGeneratePersonas, runGenerateStories, runGenerateGherkin } from './generation';

interface GuidedStage {
    label: string;
    description: string;
    artifactType: string;
    run: () => Promise<void>;
}

export async function runGuidedFlow(
    client: SdlicitClient,
    store: ArtifactStore,
    projectDir: string,
    kbSync?: KBSyncService,
): Promise<void> {
    const dataService = new DataService(client);
    const stages: GuidedStage[] = [
        {
            label: '1. Create SOW',
            description: 'Transform raw brief into structured Statement of Work',
            artifactType: 'sow',
            run: () => runCreateSOW(client, store, kbSync, undefined, undefined, { guidedFlow: true }, dataService),
        },
        {
            label: '2. Generate SRS',
            description: 'Derive requirements from the SOW',
            artifactType: 'srs',
            run: () => runGenerateSRS(client, store, projectDir, kbSync),
        },
        {
            label: '3. Generate Personas',
            description: 'Create user personas from requirements',
            artifactType: 'personas',
            run: () => runGeneratePersonas(client, store, projectDir, kbSync),
        },
        {
            label: '4. Generate User Stories',
            description: 'Derive user stories from personas and requirements',
            artifactType: 'stories',
            run: () => runGenerateStories(client, store, projectDir, kbSync),
        },
        {
            label: '5. Create ADRs',
            description: 'Document architectural decisions',
            artifactType: 'adr',
            run: () => runCreateADR(client, store, projectDir, kbSync, undefined, dataService),
        },
        {
            label: '6. Generate BDD Scenarios',
            description: 'Create Gherkin scenarios from stories and requirements',
            artifactType: 'gherkin',
            run: () => runGenerateGherkin(client, store, projectDir),
        },
    ];

    // Detect which artifacts already exist
    const existing = store.listArtifacts();
    const existingTypes = new Set<string>(existing.map(a => a.type));

    // Build picker items with completion status
    interface StagePickItem extends vscode.QuickPickItem {
        stageIndex: number;
    }
    const items: StagePickItem[] = stages.map((stage, idx) => {
        const done = existingTypes.has(stage.artifactType);
        return {
            label: `${done ? '$(check)' : '$(circle-outline)'} ${stage.label}`,
            description: done ? 'Done' : stage.description,
            stageIndex: idx,
            picked: false,
        };
    });

    // Pre-select first unfinished stage
    const firstUnfinished = stages.findIndex(s => !existingTypes.has(s.artifactType));
    const activeIndex = firstUnfinished >= 0 ? firstUnfinished : 0;

    client.log('Guided Flow: Starting pipeline');

    const picked = await vscode.window.showQuickPick(items, {
        title: 'Sdlicit — Guided Flow',
        placeHolder: `Select a stage to start from (suggested: ${stages[activeIndex].label})`,
    });

    if (!picked) {
        client.log('Guided Flow: Cancelled by user');
        return;
    }

    // Run from selected stage onwards
    for (let i = picked.stageIndex; i < stages.length; i++) {
        const stage = stages[i];
        client.log(`Guided Flow: Running stage "${stage.label}"`);

        // Run the stage (waits for user accept/decline via CodeLens)
        await stage.run();

        // If there are more stages, ask whether to continue
        if (i < stages.length - 1) {
            const next = stages[i + 1];
            const action = await vscode.window.showQuickPick(
                [
                    { label: `$(play) Continue: ${next.label}`, description: next.description, action: 'continue' as const },
                    { label: '$(debug-step-over) Skip next stage', description: `Skip "${next.label}" and move on`, action: 'skip' as const },
                    { label: '$(code) Start implementing', description: 'Exit Sdlicit and start coding', action: 'implement' as const },
                    { label: '$(stop) Stop guided flow', description: 'End the pipeline here', action: 'stop' as const },
                ],
                {
                    title: `Sdlicit — Stage "${stage.label}" complete`,
                    placeHolder: `Continue to ${next.label}?`,
                },
            );

            if (!action || action.action === 'stop' || action.action === 'implement') {
                client.log('Guided Flow: Stopped by user');
                vscode.window.showInformationMessage(
                    action?.action === 'implement'
                        ? 'Sdlicit: Exiting guided flow — happy coding!'
                        : 'Sdlicit: Guided flow stopped.'
                );
                return;
            }

            if (action.action === 'skip') {
                client.log(`Guided Flow: Skipping "${next.label}"`);
                // Skip the next stage by incrementing i (the for loop will also increment)
                i++;
                // But if we just skipped the last one, we're done
                if (i >= stages.length - 1) { break; }
                continue;
            }
            // 'continue' falls through to next iteration
        }
    }

    client.log('Guided Flow: Pipeline complete');
    vscode.window.showInformationMessage('Sdlicit: Guided flow complete! All artifacts generated.');
}
