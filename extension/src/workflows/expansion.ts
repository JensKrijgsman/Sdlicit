// ---------------------------------------------------------------------------
// Sdlicit — Expansion Workflows (KB Ingest, Query, Review)
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SdlicitClient, ScannedDocument } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { KBSyncService } from '../services/kbSyncService';

// --- Knowledge Base Query ---

export async function runQueryKB(client: SdlicitClient): Promise<void> {
    const query = await vscode.window.showInputBox({
        title: 'Sdlicit — Query Knowledge Base',
        prompt: 'What do you want to know?',
        placeHolder: 'e.g., "What does IEEE 830 say about verifiability?"',
        ignoreFocusOut: true,
    });
    if (!query) { return; }

    const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'Sdlicit: Querying KB…' },
        () => client.queryRAG(query, 'all', 'hybrid', false, 5),
    );

    if (!result.rag_enabled) {
        vscode.window.showWarningMessage('Sdlicit: Knowledge base is not enabled. Run KB Ingest first.');
        return;
    }

    if (result.results.length === 0) {
        vscode.window.showInformationMessage('Sdlicit: No results found for your query.');
        return;
    }

    // Show results in an output channel
    const channel = vscode.window.createOutputChannel('Sdlicit KB Results', 'markdown');
    channel.clear();
    channel.appendLine(`# Knowledge Base Query: "${query}"\n`);

    // Separate synthesized answer from raw source chunks
    const graphResult = result.results.find(r => r.mode !== 'naive');
    const sourceChunks = result.results.filter(r => r.mode === 'naive');

    if (graphResult) {
        channel.appendLine('## Answer\n');
        channel.appendLine(graphResult.text);
        channel.appendLine('');
    }

    if (sourceChunks.length > 0) {
        channel.appendLine(`\n## Sources (${sourceChunks.length} chunks)\n`);
        for (const [i, chunk] of sourceChunks.entries()) {
            channel.appendLine(`### ${i + 1}. ${chunk.source}`);
            channel.appendLine(`Relevance: ${(chunk.relevance * 100).toFixed(0)}%\n`);
            // Show only first 300 chars of raw chunk to avoid noise
            const preview = chunk.text.length > 300 ? chunk.text.slice(0, 300) + '…' : chunk.text;
            channel.appendLine(preview);
            channel.appendLine('\n---\n');
        }
    }

    channel.show();
}

// --- Knowledge Base Ingest ---

export async function runIngestKB(client: SdlicitClient, projectDir: string, kbSync?: KBSyncService): Promise<void> {
    // Scan available documents
    let documents: ScannedDocument[];
    try {
        const scan = await client.scanDocuments(projectDir);
        documents = scan.documents;
    } catch (err: any) {
        vscode.window.showErrorMessage(`Sdlicit: Failed to scan documents — ${err.message}`);
        return;
    }

    if (documents.length === 0) {
        vscode.window.showInformationMessage('Sdlicit: No documents found to ingest. Place files in the "knowledge/" folder.');
        return;
    }

    // Let user select which files to ingest
    const items = documents.map(d => ({
        label: d.relative_path,
        description: `${d.ingestion_status === 'complete' ? '✓ ingested' : d.ingestion_status} | ${(d.size_bytes / 1024).toFixed(1)} KB`,
        picked: d.ingestion_status !== 'complete',
    }));

    const selected = await vscode.window.showQuickPick(items, {
        title: 'Sdlicit — Select Documents to Ingest',
        placeHolder: 'Select files to add to the knowledge base',
        canPickMany: true,
    });
    if (!selected || selected.length === 0) { return; }

    const selectedFiles = selected.map(s => s.label);

    // Run ingestion with progress
    await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'Sdlicit: Ingesting documents…', cancellable: false },
        async (progress) => {
            let totalChunks = 0;
            await client.ingestKB(projectDir, selectedFiles, (event) => {
                switch (event.type) {
                    case 'start':
                        totalChunks = event.total_chunks ?? 0;
                        progress.report({ message: `0/${totalChunks} chunks` });
                        break;
                    case 'progress':
                        progress.report({
                            message: `${event.current}/${totalChunks}: ${event.source_name}`,
                            increment: totalChunks > 0 ? (100 / totalChunks) : 0,
                        });
                        break;
                    case 'done':
                        vscode.window.showInformationMessage(
                            `Sdlicit: Ingested ${event.ingested}/${totalChunks} chunks successfully.`
                        );
                        break;
                    case 'error':
                        vscode.window.showWarningMessage(
                            `Sdlicit: ${event.file ?? 'file'} — ${event.message}`
                        );
                        break;
                }
            });
        },
    );

    // Sync the KB tree view with backend state after ingestion
    if (kbSync) {
        await kbSync.syncFromBackend();
    }
}

// --- Expand/Review ADR ---

export async function runExpandADR(client: SdlicitClient, store: ArtifactStore, projectDir: string): Promise<void> {
    // Pick an ADR to review
    const adrs = store.listArtifacts().filter(a => a.type === 'adr');
    if (adrs.length === 0) {
        vscode.window.showWarningMessage('Sdlicit: No ADRs found. Create one first.');
        return;
    }

    const items = adrs.map(a => ({ label: a.id, description: a.title, filePath: a.filePath }));
    const picked = await vscode.window.showQuickPick(items, {
        title: 'Sdlicit — Select ADR to Review',
        placeHolder: 'Which ADR should the multi-agent pipeline review?',
    });
    if (!picked) { return; }

    const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Sdlicit: Reviewing ${picked.label}…` },
        () => client.expandADR(picked.label, projectDir),
    );

    // Display results in output channel
    const channel = vscode.window.createOutputChannel('Sdlicit Review', 'markdown');
    channel.clear();
    channel.appendLine(`# Multi-Agent Review: ${picked.label}\n`);

    for (const review of result.reviews) {
        channel.appendLine(`## ${review.agent}`);
        channel.appendLine(`*${review.summary}*\n`);
        for (const suggestion of review.suggestions) {
            channel.appendLine(`- ${suggestion}`);
        }
        if (review.compliance) {
            channel.appendLine(`\n**Compliance:** ${review.compliance}`);
        }
        channel.appendLine('');
    }

    if (result.tom_verdict) {
        channel.appendLine('## Theory of Mind — Verdict');
        channel.appendLine(result.tom_verdict);
    }

    channel.show();
}
