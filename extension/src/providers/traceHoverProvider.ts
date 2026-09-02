// ---------------------------------------------------------------------------
// Sdlicit — Trace Hover Provider
// ---------------------------------------------------------------------------
// Hovering over artifact IDs (ADR-0001, REQ-01, STORY-01, BDD-01) in markdown
// files shows a tooltip with the artifact's status, title, and traceability.
// If the artifact is superseded, shows a warning with the superseding artifact.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';

// Regex to match artifact IDs in text
const ARTIFACT_ID_PATTERN = /\b(ADR-[\w-]+-\d{1,4}|ADR-\d{1,4}|REQ-[\w-]+\d+|STORY-\d+|BDD-\d+|PERSONA-\d+)\b/g;

export class TraceHoverProvider implements vscode.HoverProvider {
    constructor(private readonly data: DataService) {}

    provideHover(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): vscode.Hover | undefined {
        const range = document.getWordRangeAtPosition(position, ARTIFACT_ID_PATTERN);
        if (!range) { return undefined; }

        const word = document.getText(range);
        const artifacts = this.data.getArtifacts();

        // Find matching artifact by ID (case-insensitive)
        const artifact = artifacts.find(a =>
            a.id.toLowerCase() === word.toLowerCase()
        );

        if (!artifact) {
            return new vscode.Hover(
                new vscode.MarkdownString(`$(warning) **${word}** — not found in artifacts`),
                range,
            );
        }

        // Build tooltip
        const md = new vscode.MarkdownString();
        md.isTrusted = true;
        md.supportThemeIcons = true;

        // Status icon
        const statusIcon = artifact.status === 'superseded' ? '⚠️'
            : artifact.status === 'accepted' ? '✅'
            : artifact.status === 'rejected' ? '❌'
            : artifact.status === 'deprecated' ? '⛔'
            : '📝';

        md.appendMarkdown(`### ${statusIcon} ${artifact.id}\n\n`);
        md.appendMarkdown(`**${artifact.title}**\n\n`);
        md.appendMarkdown(`Status: \`${artifact.status}\` · Type: \`${artifact.type}\`\n\n`);

        // Supersession warning
        if (artifact.status === 'superseded') {
            md.appendMarkdown(`---\n\n`);
            md.appendMarkdown(`⚠️ **SUPERSEDED**`);
            // Try to find what superseded it
            const superseder = artifacts.find(a =>
                a.traces.supersedes === artifact.id
            );
            if (superseder) {
                md.appendMarkdown(` by [${superseder.id}](command:sdlicit.openCanvas?${encodeURIComponent(JSON.stringify(superseder.id))})`);
            }
            md.appendMarkdown(`\n\n`);
        }

        // Trace links
        const traces: string[] = [];
        if (artifact.traces.implements.length > 0) {
            traces.push(`Implements: ${artifact.traces.implements.join(', ')}`);
        }
        if (artifact.traces.supersedes) {
            traces.push(`Supersedes: ${artifact.traces.supersedes}`);
        }
        if (artifact.traces.testedBy.length > 0) {
            traces.push(`Tested by: ${artifact.traces.testedBy.join(', ')}`);
        }
        if (artifact.traces.upstream.length > 0) {
            traces.push(`↑ Upstream: ${artifact.traces.upstream.join(', ')}`);
        }
        if (artifact.traces.downstream.length > 0) {
            traces.push(`↓ Downstream: ${artifact.traces.downstream.join(', ')}`);
        }

        if (traces.length > 0) {
            md.appendMarkdown(`---\n\n`);
            md.appendMarkdown(traces.join('  \n') + '\n');
        }

        return new vscode.Hover(md, range);
    }
}
