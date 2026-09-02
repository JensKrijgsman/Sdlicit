// ---------------------------------------------------------------------------
// Sdlicit — BDD Review Provider (Editor Panel)
// ---------------------------------------------------------------------------
// Wizard-style BDD scenario review with:
// - Natural language situation/action display
// - Expandable Gherkin blocks
// - Accept/Reject verdict buttons
// - Importance selector
// - Review notes
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';
import { BddFeature, BddScenario } from '../types';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

export class BddReviewProvider {
    private panel?: vscode.WebviewPanel;
    private feature?: BddFeature;
    private activeStep = 0;

    constructor(private readonly data: DataService) {}

    async openReview(requirementId: string): Promise<void> {
        this.feature = await this.data.generateScenarios(requirementId);

        if (this.panel) {
            this.panel.reveal(vscode.ViewColumn.One);
        } else {
            this.panel = vscode.window.createWebviewPanel(
                'sdlicit.bddReview',
                `BDD Review: ${this.feature.title}`,
                vscode.ViewColumn.One,
                { enableScripts: true, retainContextWhenHidden: true },
            );
            this.panel.onDidDispose(() => { this.panel = undefined; });
        }

        this.panel.webview.onDidReceiveMessage(async (msg) => {
            await this.handleMessage(msg);
        });

        this.render();
    }

    private async handleMessage(msg: any): Promise<void> {
        if (!this.feature) { return; }

        switch (msg.command) {
            case 'goToStep':
                this.activeStep = msg.step;
                this.render();
                break;
            case 'nextStep':
                if (this.activeStep < this.feature.scenarios.length - 1) { this.activeStep++; this.render(); }
                break;
            case 'prevStep':
                if (this.activeStep > 0) { this.activeStep--; this.render(); }
                break;
            case 'verdict': {
                const scn = this.feature.scenarios.find(s => s.id === msg.scenarioId);
                if (scn) {
                    scn.status = msg.verdict;
                    await this.data.reviewScenario(msg.scenarioId, msg.verdict);
                    this.render();
                }
                break;
            }
            case 'importance': {
                const scn = this.feature.scenarios.find(s => s.id === msg.scenarioId);
                if (scn) {
                    scn.importance = msg.importance;
                    await this.data.reviewScenario(msg.scenarioId, scn.status, msg.importance);
                    this.render();
                }
                break;
            }
            case 'addNote': {
                const note = await vscode.window.showInputBox({
                    title: `Review Note — ${msg.scenarioId}`,
                    placeHolder: 'Add a review note…',
                });
                if (note !== undefined) {
                    const scn = this.feature.scenarios.find(s => s.id === msg.scenarioId);
                    if (scn) {
                        scn.reviewNote = note;
                        await this.data.reviewScenario(msg.scenarioId, scn.status, scn.importance, note);
                        this.render();
                    }
                }
                break;
            }
        }
    }

    private render(): void {
        if (!this.panel || !this.feature) { return; }
        const nonce = getNonce();
        const f = this.feature;
        const total = f.scenarios.length;
        const accepted = f.scenarios.filter(s => s.status === 'accepted').length;

        if (this.activeStep >= total) { this.activeStep = total - 1; }
        if (this.activeStep < 0) { this.activeStep = 0; }

        const stepsHtml = f.scenarios.map((s, i) => {
            const isActive = i === this.activeStep;
            const statusBadge = s.status === 'accepted'
                ? '<span class="wizard-step-badge wizard-badge-done">Accepted</span>'
                : s.status === 'rejected'
                    ? '<span class="wizard-step-badge wizard-badge-rejected">Rejected</span>'
                    : '<span class="wizard-step-badge wizard-badge-pending">Pending</span>';
            const bodyHtml = isActive ? this.renderStepContent(s, i) : '';

            return `
                <div class="wizard-step ${s.status === 'accepted' ? 'wizard-step-completed' : ''} ${isActive ? 'wizard-step-active' : ''}" data-step="${i}">
                    <div class="wizard-step-header" onclick="goToStep(${i})">
                        <span class="wizard-step-number">${i + 1}</span>
                        <span class="wizard-step-title">${escapeHtml(s.title)}</span>
                        ${statusBadge}
                    </div>
                    ${bodyHtml ? `<div class="wizard-step-body">${bodyHtml}</div>` : ''}
                </div>`;
        }).join('');

        const body = `
            <div class="flex items-center justify-between mb-md">
                <div>
                    <h1>BDD Review: ${escapeHtml(f.title)}</h1>
                    <p class="text-sm text-muted">Requirement: ${escapeHtml(f.requirementId)} · ${accepted}/${total} accepted</p>
                </div>
            </div>

            <div class="flex items-center gap-sm mb-md">
                <div class="progress-bar" style="flex:1"><div class="progress-fill" style="width:${total > 0 ? Math.round((accepted / total) * 100) : 0}%"></div></div>
                <span class="text-sm">${total > 0 ? Math.round((accepted / total) * 100) : 0}%</span>
            </div>

            <div class="scroll-area">
                ${stepsHtml}
            </div>

            <div class="flex justify-between mt-md">
                <button class="btn btn-secondary btn-sm" onclick="prevStep()" ${this.activeStep === 0 ? 'disabled' : ''}>← Previous</button>
                <button class="btn btn-primary btn-sm" onclick="nextStep()" ${this.activeStep >= total - 1 ? 'disabled' : ''}>Next →</button>
            </div>
        `;

        const scripts = `
            const vscode = acquireVsCodeApi();
            function goToStep(step) { vscode.postMessage({ command: 'goToStep', step }); }
            function nextStep() { vscode.postMessage({ command: 'nextStep' }); }
            function prevStep() { vscode.postMessage({ command: 'prevStep' }); }
            function verdict(scenarioId, v) { vscode.postMessage({ command: 'verdict', scenarioId, verdict: v }); }
            function setImportance(scenarioId, imp) { vscode.postMessage({ command: 'importance', scenarioId, importance: imp }); }
            function addNote(scenarioId) { vscode.postMessage({ command: 'addNote', scenarioId }); }
        `;

        this.panel.webview.html = wrapHtml(body, nonce, scripts);
    }

    private renderStepContent(s: BddScenario, i: number): string {
        const importanceOptions = ['critical', 'important', 'nice-to-have'].map(imp =>
            `<button class="btn btn-sm ${s.importance === imp ? 'btn-primary' : 'btn-secondary'}" onclick="setImportance('${escapeHtml(s.id)}', '${imp}')">${imp}</button>`
        ).join('');

        return `
            <div class="mb-sm">
                <p class="text-sm"><strong>Situation:</strong> ${escapeHtml(s.situation)}</p>
            </div>

            <details class="mb-sm" open>
                <summary class="text-sm" style="cursor:pointer;color:var(--vscode-textLink-foreground)">View Gherkin</summary>
                <div class="gherkin-formatted mt-xs">${this.formatGherkin(s.gherkin)}</div>
            </details>

            ${s.reviewNote ? `<p class="text-xs text-muted mb-sm">Note: ${escapeHtml(s.reviewNote)}</p>` : ''}

            <div class="flex items-center gap-sm mb-sm">
                <span class="text-xs text-muted">Importance:</span>
                ${importanceOptions}
            </div>

            <div class="flex items-center gap-sm">
                <span class="text-xs text-muted">Verdict:</span>
                <button class="btn btn-sm ${s.status === 'accepted' ? 'btn-primary' : 'btn-secondary'}" onclick="verdict('${escapeHtml(s.id)}', 'accepted')">✅ Accept</button>
                <button class="btn btn-sm ${s.status === 'rejected' ? 'btn-primary' : 'btn-secondary'}" onclick="verdict('${escapeHtml(s.id)}', 'rejected')">❌ Reject</button>
                <button class="btn btn-sm btn-secondary" onclick="addNote('${escapeHtml(s.id)}')" title="Add note">note</button>
            </div>
        `;
    }

    private formatGherkin(gherkin: string): string {
        return escapeHtml(gherkin)
            .split('\n')
            .map(line => {
                const trimmed = line.trim();
                if (trimmed.startsWith('Feature:')) {
                    return `<div class="gherkin-line gherkin-feature"><span class="gherkin-keyword">Feature:</span>${line.replace(/Feature:/, '')}</div>`;
                }
                if (trimmed.startsWith('Scenario:') || trimmed.startsWith('Scenario Outline:')) {
                    const keyword = trimmed.startsWith('Scenario Outline:') ? 'Scenario Outline:' : 'Scenario:';
                    return `<div class="gherkin-line gherkin-scenario"><span class="gherkin-keyword">${keyword}</span>${line.replace(keyword, '')}</div>`;
                }
                if (trimmed.startsWith('Given ')) {
                    return `<div class="gherkin-line gherkin-step"><span class="gherkin-keyword">Given</span>${line.replace(/Given/, '')}</div>`;
                }
                if (trimmed.startsWith('When ')) {
                    return `<div class="gherkin-line gherkin-step"><span class="gherkin-keyword">When</span>${line.replace(/When/, '')}</div>`;
                }
                if (trimmed.startsWith('Then ')) {
                    return `<div class="gherkin-line gherkin-step"><span class="gherkin-keyword">Then</span>${line.replace(/Then/, '')}</div>`;
                }
                if (trimmed.startsWith('And ')) {
                    return `<div class="gherkin-line gherkin-step"><span class="gherkin-keyword">And</span>${line.replace(/And/, '')}</div>`;
                }
                if (trimmed.startsWith('But ')) {
                    return `<div class="gherkin-line gherkin-step"><span class="gherkin-keyword">But</span>${line.replace(/But/, '')}</div>`;
                }
                if (trimmed.startsWith('Background:')) {
                    return `<div class="gherkin-line gherkin-scenario"><span class="gherkin-keyword">Background:</span>${line.replace(/Background:/, '')}</div>`;
                }
                if (trimmed.startsWith('Examples:')) {
                    return `<div class="gherkin-line gherkin-scenario"><span class="gherkin-keyword">Examples:</span>${line.replace(/Examples:/, '')}</div>`;
                }
                if (trimmed.startsWith('|')) {
                    return `<div class="gherkin-line gherkin-table">${line}</div>`;
                }
                if (trimmed.startsWith('#')) {
                    return `<div class="gherkin-line gherkin-comment">${line}</div>`;
                }
                if (trimmed.startsWith('@')) {
                    return `<div class="gherkin-line gherkin-tag">${line}</div>`;
                }
                return `<div class="gherkin-line">${line}</div>`;
            })
            .join('');
    }
}
