// ---------------------------------------------------------------------------
// Sdlicit — Socratic Loop Helper
// ---------------------------------------------------------------------------
// Shared utility for handling inline Socratic probes across all workflows.
// When a backend response contains a `socratic_probe`, this shows transparency
// events, KB facts (if any), then collects the user's answer.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SocraticProbe, Clarification } from '../services/sdlicitClient';

const _STYLE_LABEL: Record<string, string> = {
    assumption: 'Hidden assumption',
    contradiction: 'Contradiction',
    depth: 'Deeper reflection',
    perspective: 'Unconsidered angle',
};

/**
 * Handle Socratic probe loop. Returns accumulated clarifications or null if cancelled.
 *
 * Shows:
 *  1. Transparency events as an info notification (if any).
 *  2. A KB Facts quick-pick info step (collapsible accordion equivalent) if kb_facts is set.
 *  3. An InputBox with the Socratic question.
 */
export async function handleSocraticProbe(
    probe: SocraticProbe,
    existingClarifications: Clarification[],
): Promise<{ answer: string; clarifications: Clarification[] } | null> {

    const styleLabel = _STYLE_LABEL[probe.style] ?? probe.style;
    const turnLabel = `Turn ${probe.turn}/${probe.max_turns}`;

    // 1. Transparency events — show as a brief info notification
    if (probe.transparency_events && probe.transparency_events.length > 0) {
        const message = probe.transparency_events.join('  ·  ');
        vscode.window.showInformationMessage(`Sdlicit: ${message}`, { modal: false });
    }

    // 2. KB Facts — show as a multi-step QuickPick so the user can read them before answering
    if (probe.kb_facts && probe.kb_facts.trim() !== '') {
        const items: vscode.QuickPickItem[] = [
            {
                label: '$(book) From the Knowledge Base',
                description: turnLabel,
                detail: probe.kb_facts.trim(),
                kind: vscode.QuickPickItemKind.Default,
                alwaysShow: true,
            },
            {
                label: '$(arrow-right) Continue to question',
                description: 'Proceed to the follow-up question',
                kind: vscode.QuickPickItemKind.Default,
                alwaysShow: true,
            },
        ];

        const kbChoice = await vscode.window.showQuickPick(items, {
            title: `Sdlicit — Knowledge Base Context (${styleLabel})`,
            placeHolder: 'Review the relevant knowledge base excerpt, then continue.',
            ignoreFocusOut: true,
        });

        if (kbChoice === undefined) {
            // Cancelled
            return null;
        }
    }

    // 3. Ask the Socratic question
    const answer = await vscode.window.showInputBox({
        title: `Sdlicit — ${styleLabel} (${turnLabel})`,
        prompt: probe.question,
        placeHolder: 'Your answer (leave empty or press Escape to skip)…',
        ignoreFocusOut: true,
    });

    if (answer === undefined) {
        // User cancelled
        return null;
    }

    if (answer.trim() === '') {
        // User skipped — still return so resolution judge can stop the loop
        const clarification: Clarification = { question: probe.question, answer: 'skip' };
        return {
            answer: 'skip',
            clarifications: [...existingClarifications, clarification],
        };
    }

    const clarification: Clarification = { question: probe.question, answer: answer.trim() };
    return {
        answer: answer.trim(),
        clarifications: [...existingClarifications, clarification],
    };
}

/**
 * Show a review prompt after artifact generation.
 * Returns the user's choice.
 */
export async function showReviewPrompt(artifactLabel: string): Promise<'accept' | 'regenerate' | 'edit' | 'skip' | undefined> {
    const items: (vscode.QuickPickItem & { action: 'accept' | 'regenerate' | 'edit' | 'skip' })[] = [
        { label: '$(check) Accept', description: 'Save the artifact as-is', action: 'accept' },
        { label: '$(sync) Regenerate', description: 'Regenerate with additional notes', action: 'regenerate' },
        { label: '$(edit) Edit', description: 'Open in editor for manual changes', action: 'edit' },
        { label: '$(close) Skip', description: 'Discard this artifact', action: 'skip' },
    ];

    const choice = await vscode.window.showQuickPick(items, {
        title: `Sdlicit — Review: ${artifactLabel}`,
        placeHolder: 'What would you like to do with this artifact?',
    });

    return choice?.action;
}
