// ---------------------------------------------------------------------------
// Sdlicit — Generation Workflows (SRS, Personas, Stories, Gherkin)
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SdlicitClient, Clarification, SocraticProbe } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { ArtifactTreeProvider } from '../providers/artifactTreeProvider';
import { SRSPanelProvider } from '../providers/srsPanelProvider';
import { PersonasPanelProvider } from '../providers/personasPanelProvider';
import { StoriesPanelProvider } from '../providers/storiesPanelProvider';
import { BddPanelProvider } from '../providers/bddPanelProvider';
import { KBSyncService } from '../services/kbSyncService';

// --- SRS Generation (Panel-based) ---

export async function runGenerateSRS(
    client: SdlicitClient,
    store: ArtifactStore,
    projectDir: string,
    kbSync?: KBSyncService,
    globalStoragePath?: string,
    artifactTree?: ArtifactTreeProvider,
): Promise<void> {
    const sowContent = store.getLatestSOW();
    if (!sowContent) {
        vscode.window.showWarningMessage('Sdlicit: No SOW found. Create one first.');
        return;
    }

    const srsPanel = new SRSPanelProvider(client, store, kbSync, globalStoragePath);
    const result = await srsPanel.startGeneration(sowContent);

    if (result === 'accepted') {
        artifactTree?.refresh();
    }
}

// --- Personas Generation (Panel-based) ---

export async function runGeneratePersonas(
    client: SdlicitClient,
    store: ArtifactStore,
    projectDir: string,
    kbSync?: KBSyncService,
    globalStoragePath?: string,
    artifactTree?: ArtifactTreeProvider,
): Promise<void> {
    const srsContent = store.getLatestSRS();
    if (!srsContent) {
        vscode.window.showWarningMessage('Sdlicit: No SRS found. Generate one first before creating personas.');
        return;
    }

    const personasPanel = new PersonasPanelProvider(client, store, kbSync, globalStoragePath);
    const result = await personasPanel.startGeneration(srsContent);

    if (result === 'accepted') {
        artifactTree?.refresh();
    }
}

// --- User Stories Generation (Panel-based) ---

export async function runGenerateStories(
    client: SdlicitClient,
    store: ArtifactStore,
    projectDir: string,
    kbSync?: KBSyncService,
    globalStoragePath?: string,
    artifactTree?: ArtifactTreeProvider,
): Promise<void> {
    const srsContent = store.getLatestSRS();
    if (!srsContent) {
        vscode.window.showWarningMessage('Sdlicit: No SRS found. Generate one first.');
        return;
    }

    // Read personas file — prefer JSON export over markdown duplicate
    const personaArtifacts = store.listArtifacts().filter(a => a.type === 'personas');
    const jsonArtifact = personaArtifacts.find(a => a.filePath.endsWith('.json'));
    const mdArtifact = personaArtifacts.find(a => a.filePath.endsWith('.md'));
    const personas: string[] = [];
    if (jsonArtifact) {
        const content = store.readArtifact(jsonArtifact.filePath);
        if (content) { personas.push(content); }
    } else if (mdArtifact) {
        const content = store.readArtifact(mdArtifact.filePath);
        if (content) { personas.push(content); }
    } else {
        personaArtifacts.forEach(a => {
            const content = store.readArtifact(a.filePath);
            if (content) { personas.push(content); }
        });
    }

    const storiesPanel = new StoriesPanelProvider(client, store, kbSync, globalStoragePath);
    const result = await storiesPanel.startGeneration(srsContent, personas);

    if (result === 'accepted') {
        artifactTree?.refresh();
    }
}

// --- Gherkin/BDD Generation (Panel-based) ---

export async function runGenerateGherkin(
    client: SdlicitClient,
    store: ArtifactStore,
    projectDir: string,
    kbSync?: KBSyncService,
    globalStoragePath?: string,
    artifactTree?: ArtifactTreeProvider,
): Promise<void> {
    const srsContent = store.getLatestSRS();
    if (!srsContent) {
        vscode.window.showWarningMessage('Sdlicit: No SRS found. Generate one first before creating BDD scenarios.');
        return;
    }

    const personaArtifacts = store.listArtifacts().filter(a => a.type === 'personas');
    const jsonArtifact = personaArtifacts.find(a => a.filePath.endsWith('.json'));
    const mdArtifact = personaArtifacts.find(a => a.filePath.endsWith('.md'));
    const personas: string[] = [];
    if (jsonArtifact) {
        const content = store.readArtifact(jsonArtifact.filePath);
        if (content) { personas.push(content); }
    } else if (mdArtifact) {
        const content = store.readArtifact(mdArtifact.filePath);
        if (content) { personas.push(content); }
    } else {
        personaArtifacts.forEach(a => {
            const content = store.readArtifact(a.filePath);
            if (content) { personas.push(content); }
        });
    }

    const bddPanel = new BddPanelProvider(client, store, kbSync, globalStoragePath);
    const result = await bddPanel.startGeneration(srsContent, personas);

    if (result === 'accepted') {
        artifactTree?.refresh();
    }
}


