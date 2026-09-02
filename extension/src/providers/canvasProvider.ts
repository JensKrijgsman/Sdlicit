// ---------------------------------------------------------------------------
// Sdlicit — Canvas Provider
// ---------------------------------------------------------------------------
// Rich structured editing surface with OLD-style visuals:
// - View modes: overview (all sections) / focus (one section with dot nav)
// - Section-by-section editing with AI Assist button + inline spinner
// - Companion observations panel (purple left border, dismiss)
// - Socratic elicitation inline (multi-choice options, free text)
// - Quality progress, traces, lock banner
// - Open questions (inline, warning-styled)
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';
import { Artifact, ArtifactSection, CompanionObservation } from '../types';
import { getNonce, wrapHtml, escapeHtml, statusDot, typeLabel } from '../webview/webviewHelper';

type ViewMode = 'overview' | 'focus';

export class CanvasProvider {
    private panels: Map<string, vscode.WebviewPanel> = new Map();
    private viewModes: Map<string, ViewMode> = new Map();
    private focusIndex: Map<string, number> = new Map();

    constructor(private readonly data: DataService) {}

    toggleActiveViewMode(): void {
        for (const [id, panel] of this.panels) {
            if (panel.active) {
                const mode = this.viewModes.get(id) === 'focus' ? 'overview' : 'focus';
                this.viewModes.set(id, mode);
                this.rerenderPanel(id);
                break;
            }
        }
    }

    openActiveInMarkdown(): void {
        for (const [id, panel] of this.panels) {
            if (panel.active) {
                this.openInMarkdown(id);
                break;
            }
        }
    }

    async openArtifact(artifactId: string): Promise<void> {
        if (this.panels.has(artifactId)) {
            this.panels.get(artifactId)!.reveal();
            return;
        }

        const artifact = this.data.getArtifact(artifactId);
        if (!artifact) {
            vscode.window.showErrorMessage(`Artifact ${artifactId} not found.`);
            return;
        }

        this.viewModes.set(artifactId, 'overview');
        this.focusIndex.set(artifactId, 0);

        const panel = vscode.window.createWebviewPanel(
            'sdlicit.canvas',
            `${artifact.id}: ${artifact.title}`,
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        this.panels.set(artifactId, panel);
        panel.onDidDispose(() => {
            this.panels.delete(artifactId);
            this.viewModes.delete(artifactId);
            this.focusIndex.delete(artifactId);
            if (this.panels.size === 0) {
                vscode.commands.executeCommand('setContext', 'sdlicit.canvasActive', false);
            }
        });
        panel.onDidChangeViewState((e) => {
            if (e.webviewPanel.active) {
                vscode.commands.executeCommand('setContext', 'sdlicit.canvasActive', true);
            }
        });

        panel.webview.html = this.buildHtml(artifact);
        this.setupHandler(panel, artifactId);
        vscode.commands.executeCommand('setContext', 'sdlicit.canvasActive', true);
    }

    openInMarkdown(artifactId: string): void {
        const artifact = this.data.getArtifact(artifactId);
        if (!artifact) { return; }
        vscode.commands.executeCommand('vscode.open', vscode.Uri.file(artifact.filePath));
    }

    private rerenderPanel(artifactId: string): void {
        const panel = this.panels.get(artifactId);
        if (!panel) { return; }
        const artifact = this.data.getArtifact(artifactId);
        if (!artifact) { return; }
        panel.webview.html = this.buildHtml(artifact);
    }

    /** Auto-review a section after navigation (if it has enough content). */
    private async triggerSectionAutoReview(panel: vscode.WebviewPanel, artifactId: string, sectionIdx: number): Promise<void> {
        const artifact = this.data.getArtifact(artifactId);
        if (!artifact) { return; }
        const section = artifact.sections[sectionIdx];
        if (!section || section.content.trim().length < 20) { return; }
        panel.webview.postMessage({ command: 'showAutoReview', sectionId: section.id, status: 'loading' });
        try {
            const observations = await this.data.getCompanionObservations(artifactId, section.id, section.content);
            panel.webview.postMessage({ command: 'showAutoReview', sectionId: section.id, status: 'done', observations });
        } catch {
            panel.webview.postMessage({ command: 'showAutoReview', sectionId: section.id, status: 'error' });
        }
    }

    // ── Message Handling ────────────────────────────────────────────────────

    private setupHandler(panel: vscode.WebviewPanel, artifactId: string): void {
        panel.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.command) {
                case 'saveSection': {
                    await this.data.updateSection(msg.artifactId, msg.sectionId, msg.content);
                    this.rerenderPanel(artifactId);
                    break;
                }
                case 'requestCompanion': {
                    panel.webview.postMessage({ command: 'showSpinner', sectionId: msg.sectionId, area: 'companion' });
                    try {
                        const observations = await this.data.getCompanionObservations(msg.artifactId, msg.sectionId, msg.content);
                        panel.webview.postMessage({ command: 'companionResult', sectionId: msg.sectionId, observations });
                    } catch (err: any) {
                        panel.webview.postMessage({ command: 'companionResult', sectionId: msg.sectionId, observations: [{ id: 'err', text: err.message, severity: 'warning', actionable: false }] });
                    }
                    break;
                }
                case 'autoReviewSection': {
                    // Lightweight auto-review triggered by debounce (period typed or section navigation)
                    if (!msg.content || msg.content.trim().length < 20) { break; } // Skip trivial content
                    panel.webview.postMessage({ command: 'showAutoReview', sectionId: msg.sectionId, status: 'loading' });
                    try {
                        const observations = await this.data.getCompanionObservations(msg.artifactId, msg.sectionId, msg.content);
                        panel.webview.postMessage({ command: 'showAutoReview', sectionId: msg.sectionId, status: 'done', observations });
                    } catch (err: any) {
                        panel.webview.postMessage({ command: 'showAutoReview', sectionId: msg.sectionId, status: 'error', error: err.message });
                    }
                    break;
                }
                case 'companionAction': {
                    if (msg.action === 'clarify') {
                        const clarification = await this.data.clarifyObservation(msg.observationId);
                        panel.webview.postMessage({ command: 'clarifyResult', observationId: msg.observationId, clarify: clarification });
                    }
                    break;
                }
                case 'dismissCompanion': {
                    panel.webview.postMessage({ command: 'companionDismissed', sectionId: msg.sectionId });
                    break;
                }
                case 'startElicitation': {
                    panel.webview.postMessage({ command: 'showSpinner', sectionId: msg.sectionId, area: 'elicitation' });
                    try {
                        const resp = await this.data.startElicitation(msg.artifactId, msg.sectionId);
                        panel.webview.postMessage({ command: 'elicitationQuestion', sectionId: msg.sectionId, response: resp });
                    } catch (err: any) {
                        panel.webview.postMessage({ command: 'hideSpinner', sectionId: msg.sectionId, area: 'elicitation' });
                    }
                    break;
                }
                case 'elicitationRespond': {
                    panel.webview.postMessage({ command: 'showSpinner', sectionId: msg.sectionId, area: 'elicitation' });
                    try {
                        const resp = await this.data.respondToElicitation(msg.sessionId, msg.response, msg.sectionId);
                        panel.webview.postMessage({ command: 'elicitationQuestion', sectionId: msg.sectionId, response: resp });
                    } catch (err: any) {
                        panel.webview.postMessage({ command: 'hideSpinner', sectionId: msg.sectionId, area: 'elicitation' });
                    }
                    break;
                }
                case 'skipElicitation': {
                    panel.webview.postMessage({ command: 'elicitationDismissed', sectionId: msg.sectionId });
                    break;
                }
                case 'acceptDraft': {
                    await this.data.updateSection(msg.artifactId, msg.sectionId, msg.draft);
                    this.rerenderPanel(artifactId);
                    break;
                }
                case 'openMarkdown': {
                    this.openInMarkdown(artifactId);
                    break;
                }
                case 'toggleViewMode': {
                    const mode = this.viewModes.get(artifactId) === 'focus' ? 'overview' : 'focus';
                    this.viewModes.set(artifactId, mode);
                    this.rerenderPanel(artifactId);
                    break;
                }
                case 'focusSection': {
                    this.focusIndex.set(artifactId, msg.index);
                    this.viewModes.set(artifactId, 'focus');
                    this.rerenderPanel(artifactId);
                    break;
                }
                case 'focusPrev': {
                    // Auto-fire elicitation for the section user is leaving, if it was newly modified
                    if (msg.sectionModified && msg.leavingSectionId) {
                        panel.webview.postMessage({ command: 'showSpinner', sectionId: msg.leavingSectionId, area: 'elicitation' });
                        this.data.startElicitation(artifactId, msg.leavingSectionId).then(resp => {
                            panel.webview.postMessage({ command: 'elicitationQuestion', sectionId: msg.leavingSectionId, response: resp });
                        }).catch(() => {
                            panel.webview.postMessage({ command: 'hideSpinner', sectionId: msg.leavingSectionId, area: 'elicitation' });
                        });
                    }
                    const idx = Math.max(0, (this.focusIndex.get(artifactId) ?? 0) - 1);
                    this.focusIndex.set(artifactId, idx);
                    this.rerenderPanel(artifactId);
                    break;
                }
                case 'focusNext': {
                    // Auto-fire elicitation for the section user is leaving, if it was newly modified
                    if (msg.sectionModified && msg.leavingSectionId) {
                        panel.webview.postMessage({ command: 'showSpinner', sectionId: msg.leavingSectionId, area: 'elicitation' });
                        this.data.startElicitation(artifactId, msg.leavingSectionId).then(resp => {
                            panel.webview.postMessage({ command: 'elicitationQuestion', sectionId: msg.leavingSectionId, response: resp });
                        }).catch(() => {
                            panel.webview.postMessage({ command: 'hideSpinner', sectionId: msg.leavingSectionId, area: 'elicitation' });
                        });
                    }
                    const artifact = this.data.getArtifact(artifactId);
                    const max = (artifact?.sections.length ?? 1) - 1;
                    const idx = Math.min(max, (this.focusIndex.get(artifactId) ?? 0) + 1);
                    this.focusIndex.set(artifactId, idx);
                    this.rerenderPanel(artifactId);
                    break;
                }
                case 'openTraceGraph': {
                    vscode.commands.executeCommand('sdlicit.openTraceGraph');
                    break;
                }
                case 'openArtifact': {
                    if (msg.artifactId) {
                        vscode.commands.executeCommand('sdlicit.openCanvas', msg.artifactId);
                    }
                    break;
                }
                case 'chatAboutSection': {
                    const artifact = this.data.getArtifact(artifactId);
                    if (!artifact) { break; }
                    const section = artifact.sections.find(s => s.id === msg.sectionId);
                    if (!section) { break; }
                    const context = [
                        `**${artifact.type.toUpperCase()} — ${artifact.title}**`,
                        `**Section: ${section.title}**`,
                        '',
                        section.content ? `Current content:\n${section.content}` : '(empty)',
                    ].filter(Boolean).join('\n');

                    vscode.commands.executeCommand('sdlicit.chatWithContext', {
                        panelId: `canvas-${artifactId}`,
                        panelType: 'canvas',
                        sectionKey: section.id,
                        sectionHeading: section.title,
                        context,
                    });
                    break;
                }
                case 'requestArtifactReview': {
                    // Full artifact-level review triggered on open/switch
                    const artifact = this.data.getArtifact(msg.artifactId);
                    if (!artifact) { break; }
                    panel.webview.postMessage({ command: 'artifactReviewStatus', status: 'loading' });
                    try {
                        const fullContent = artifact.sections.map(s => `## ${s.title}\n${s.content}`).join('\n\n');
                        const observations = await this.data.getCompanionObservations(msg.artifactId, '__full_artifact__', fullContent);
                        panel.webview.postMessage({ command: 'artifactReviewStatus', status: 'done', observations });
                    } catch (err: any) {
                        panel.webview.postMessage({ command: 'artifactReviewStatus', status: 'error', error: err.message });
                    }
                    break;
                }
            }
        });
    }

    // ── HTML Rendering ──────────────────────────────────────────────────────

    private buildHtml(artifact: Artifact): string {
        const nonce = getNonce();
        const viewMode = this.viewModes.get(artifact.id) ?? 'overview';
        const focusIdx = this.focusIndex.get(artifact.id) ?? 0;
        const isLocked = artifact.status === 'accepted' || artifact.status === 'deprecated';
        const completeSections = artifact.sections.filter(s => s.status === 'complete').length;
        const totalSections = artifact.sections.length;
        const progressPct = totalSections > 0 ? Math.round((completeSections / totalSections) * 100) : 0;

        // Header
        const headerHtml = `
            <div class="flex items-center justify-between mb-sm">
                <div>
                    <h1 style="margin-bottom:2px">${escapeHtml(artifact.title)}</h1>
                    <div class="flex items-center gap-sm text-sm">
                        ${statusDot(artifact.status)}<span class="text-muted">${artifact.status}</span>
                        ${typeLabel(artifact.type)}
                    </div>
                </div>
                <div class="flex gap-xs">
                    <button class="btn btn-secondary btn-sm" data-action="toggleViewMode">${viewMode === 'overview' ? 'Focus' : 'Overview'}</button>
                    <button class="btn btn-secondary btn-sm" data-action="openMarkdown">Markdown</button>
                </div>
            </div>`;

        // Lock banner
        const lockBanner = isLocked
            ? `<div class="lock-banner"><strong>Locked</strong> — This artifact is ${artifact.status}. Editing is disabled.</div>`
            : '';

        // Artifact-level review banner (populated via message)
        const artifactReviewBanner = `<div id="artifact-review-banner" class="artifact-review-banner" style="display:none"></div>`;

        // Progress bar
        const progressHtml = `
            <div class="flex items-center gap-sm mb-md">
                <span class="text-xs text-muted">Progress</span>
                <div class="progress-bar" style="flex:1"><div class="progress-fill" style="width:${progressPct}%"></div></div>
                <span class="text-xs">${progressPct}% (${completeSections}/${totalSections})</span>
            </div>`;

        // Traces
        const tracesHtml = this.buildTracesHtml(artifact);

        // Sections
        let sectionsHtml: string;
        if (viewMode === 'focus') {
            const section = artifact.sections[focusIdx];
            const dotsHtml = artifact.sections.map((_, i) =>
                `<span class="focus-dot${i === focusIdx ? ' active' : ''}" data-action="focusSection" data-index="${i}"></span>`
            ).join('');
            sectionsHtml = `
                <div class="focus-nav">
                    <button class="btn btn-secondary btn-sm" data-action="focusPrev" ${focusIdx === 0 ? 'disabled' : ''}>&larr;</button>
                    <div class="flex gap-xs items-center">${dotsHtml}</div>
                    <button class="btn btn-secondary btn-sm" data-action="focusNext" ${focusIdx === totalSections - 1 ? 'disabled' : ''}>&rarr;</button>
                    <span class="text-xs text-muted">${focusIdx + 1}/${totalSections}</span>
                </div>
                ${section ? this.buildSectionHtml(artifact, section, focusIdx, isLocked, true) : ''}`;
        } else {
            sectionsHtml = artifact.sections.map((s, i) =>
                this.buildSectionHtml(artifact, s, i, isLocked, false)
            ).join('');
        }

        const body = `${headerHtml}${lockBanner}${artifactReviewBanner}${progressHtml}${tracesHtml}${sectionsHtml}`;
        const scripts = this.buildScripts(artifact.id);
        return wrapHtml(body, nonce, scripts);
    }

    private buildSectionHtml(artifact: Artifact, section: ArtifactSection, index: number, isLocked: boolean, expanded: boolean): string {
        const statusIcon = section.status === 'complete' ? '&#x2714;' : section.status === 'partial' ? '&#x25CF;' : '&#x25CB;';
        const statusCls = `badge-${section.status}`;
        const isEmpty = section.content.trim().length === 0;

        return `
            <div class="section-panel${expanded ? ' section-active' : ''}" data-section-id="${escapeHtml(section.id)}">
                <div class="section-header" data-action="toggleSection" data-index="${index}">
                    <div class="flex items-center gap-sm">
                        <span style="font-size:.9em">${statusIcon}</span>
                        <strong>${escapeHtml(section.title)}</strong>
                    </div>
                    <span class="badge-status ${statusCls}">${section.status}</span>
                </div>
                <div class="section-body${expanded ? '' : ' collapsed'}" id="body-${index}">
                    ${section.prompt ? `<p class="text-sm text-muted mb-sm">${escapeHtml(section.prompt)}</p>` : ''}
                    <textarea id="content-${index}" rows="${Math.max(3, Math.min(12, section.content.split('\n').length + 1))}" ${isLocked ? 'disabled' : ''} data-section-id="${escapeHtml(section.id)}">${escapeHtml(section.content)}</textarea>
                    <div id="spinner-${index}" class="section-spinner" style="display:none"><span class="spinner"></span><span>Working...</span></div>
                    ${!isLocked ? `
                        <div class="flex gap-sm mt-sm flex-wrap">
                            <button class="btn btn-primary btn-sm" data-action="save" data-section="${escapeHtml(section.id)}" data-index="${index}">Save</button>
                            <button class="btn btn-secondary btn-sm" data-action="elicit" data-section="${escapeHtml(section.id)}" data-index="${index}">AI Assist</button>
                            <button class="btn btn-secondary btn-sm" data-action="companion" data-section="${escapeHtml(section.id)}" data-index="${index}">Review</button>
                            <button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-section="${escapeHtml(section.id)}" data-index="${index}">Chat</button>
                            ${isEmpty ? `<span class="text-xs text-muted items-center flex">Empty — use AI Assist to get started</span>` : ''}
                        </div>
                    ` : ''}
                    <div id="elicitation-${index}" class="mt-sm"></div>
                    <div id="companion-${index}" class="mt-sm"></div>
                    <div id="autoreview-${index}" class="mt-sm"></div>
                </div>
            </div>`;
    }

    private buildTracesHtml(artifact: Artifact): string {
        const { upstream, downstream, implements: impl, testedBy, supersedes } = artifact.traces;

        // Compute reverse traces
        const reverse = this.data.getReversTraces(artifact.id);

        const hasTraces = upstream.length > 0 || downstream.length > 0 || impl.length > 0 ||
            testedBy.length > 0 || !!supersedes ||
            reverse.implementedBy.length > 0 || reverse.testedBy.length > 0 || reverse.referencedBy.length > 0;
        if (!hasTraces) { return ''; }

        const makeNode = (id: string) => `<span class="trace-node clickable" data-artifact-id="${escapeHtml(id)}" title="Click to navigate">${escapeHtml(id)}</span>`;
        const traceRows: string[] = [];

        if (impl.length > 0) {
            traceRows.push(`<div class="trace-row"><span class="trace-label">Implements:</span> ${impl.map(makeNode).join(' ')}</div>`);
        }
        if (upstream.length > 0) {
            traceRows.push(`<div class="trace-row"><span class="trace-label">Traces from:</span> ${upstream.map(makeNode).join(' ')}</div>`);
        }
        if (downstream.length > 0) {
            traceRows.push(`<div class="trace-row"><span class="trace-label">Traces to:</span> ${downstream.map(makeNode).join(' ')}</div>`);
        }
        if (testedBy.length > 0) {
            traceRows.push(`<div class="trace-row"><span class="trace-label">Tested by:</span> ${testedBy.map(makeNode).join(' ')}</div>`);
        }
        if (supersedes) {
            traceRows.push(`<div class="trace-row"><span class="trace-label">Supersedes:</span> ${makeNode(supersedes)}</div>`);
        }
        // Reverse traces
        if (reverse.implementedBy.length > 0) {
            traceRows.push(`<div class="trace-row"><span class="trace-label">Implemented by:</span> ${reverse.implementedBy.map(makeNode).join(' ')}</div>`);
        }
        if (reverse.testedBy.length > 0) {
            traceRows.push(`<div class="trace-row"><span class="trace-label">Tests:</span> ${reverse.testedBy.map(makeNode).join(' ')}</div>`);
        }
        if (reverse.referencedBy.length > 0) {
            traceRows.push(`<div class="trace-row"><span class="trace-label">Referenced by:</span> ${reverse.referencedBy.map(makeNode).join(' ')}</div>`);
        }

        return `
            <div class="card-flat mb-md">
                <div class="text-xs text-muted mb-xs">Traceability</div>
                ${traceRows.join('\n')}
            </div>`;
    }

    private buildScripts(artifactId: string): string {
        return `
            // Section toggle
            function toggleSection(idx) {
                var body = document.getElementById('body-' + idx);
                if (body) body.classList.toggle('collapsed');
            }

            // ── Debounced auto-review ──────────────────────────────────────────
            var _reviewTimers = {};
            function scheduleAutoReview(sectionId, index) {
                if (_reviewTimers[sectionId]) clearTimeout(_reviewTimers[sectionId]);
                _reviewTimers[sectionId] = setTimeout(function() {
                    var ta = document.getElementById('content-' + index);
                    if (ta && ta.value.trim().length >= 20) {
                        vscode.postMessage({ command: 'autoReviewSection', artifactId: '${escapeHtml(artifactId)}', sectionId: sectionId, content: ta.value });
                    }
                }, 1500);
            }



            // Track which sections have been modified by the user
            var _modifiedSections = {};
            var _originalContent = {};

            // Store original content of each textarea on load
            document.querySelectorAll('textarea[data-section-id]').forEach(function(ta) {
                _originalContent[ta.dataset.sectionId] = ta.value;
            });

            // Mark sections as modified when user types
            document.addEventListener('input', function(e) {
                if (e.target && e.target.tagName === 'TEXTAREA' && e.target.dataset.sectionId) {
                    var sid = e.target.dataset.sectionId;
                    if (e.target.value !== _originalContent[sid]) {
                        _modifiedSections[sid] = true;
                    }
                }
            });

            // Auto-fire companion review on blur if section was modified
            document.addEventListener('focusout', function(e) {
                if (e.target && e.target.tagName === 'TEXTAREA' && e.target.dataset.sectionId) {
                    var sid = e.target.dataset.sectionId;
                    if (_modifiedSections[sid] && e.target.value.trim().length >= 20) {
                        vscode.postMessage({ command: 'requestCompanion', artifactId: '${escapeHtml(artifactId)}', sectionId: sid, content: e.target.value });
                        _modifiedSections[sid] = false;
                    }
                }
            });

            // Click delegation
            document.addEventListener('click', function(e) {
                var el;
                if ((el = e.target.closest('[data-action="toggleSection"]'))) {
                    toggleSection(el.dataset.index);
                    return;
                }
                if ((el = e.target.closest('[data-action="save"]'))) {
                    var ta = document.getElementById('content-' + el.dataset.index);
                    vscode.postMessage({ command: 'saveSection', artifactId: '${escapeHtml(artifactId)}', sectionId: el.dataset.section, content: ta.value });
                    return;
                }
                if ((el = e.target.closest('[data-action="elicit"]'))) {
                    vscode.postMessage({ command: 'startElicitation', artifactId: '${escapeHtml(artifactId)}', sectionId: el.dataset.section });
                    return;
                }
                if ((el = e.target.closest('[data-action="chatAboutSection"]'))) {
                    vscode.postMessage({ command: 'chatAboutSection', artifactId: '${escapeHtml(artifactId)}', sectionId: el.dataset.section });
                    return;
                }
                if ((el = e.target.closest('[data-action="companion"]'))) {
                    var ta = document.getElementById('content-' + el.dataset.index);
                    vscode.postMessage({ command: 'requestCompanion', artifactId: '${escapeHtml(artifactId)}', sectionId: el.dataset.section, content: ta.value });
                    return;
                }
                if ((el = e.target.closest('[data-action="toggleViewMode"]'))) {
                    vscode.postMessage({ command: 'toggleViewMode' });
                    return;
                }
                if ((el = e.target.closest('[data-action="openMarkdown"]'))) {
                    vscode.postMessage({ command: 'openMarkdown' });
                    return;
                }
                if ((el = e.target.closest('[data-action="focusPrev"]'))) {
                    var activeSection = document.querySelector('.section-panel.section-active');
                    var sid = activeSection ? activeSection.dataset.sectionId : null;
                    var modified = sid ? !!_modifiedSections[sid] : false;
                    vscode.postMessage({ command: 'focusPrev', sectionModified: modified, leavingSectionId: sid });
                    return;
                }
                if ((el = e.target.closest('[data-action="focusNext"]'))) {
                    var activeSection = document.querySelector('.section-panel.section-active');
                    var sid = activeSection ? activeSection.dataset.sectionId : null;
                    var modified = sid ? !!_modifiedSections[sid] : false;
                    vscode.postMessage({ command: 'focusNext', sectionModified: modified, leavingSectionId: sid });
                    return;
                }
                if ((el = e.target.closest('[data-action="focusSection"]'))) {
                    vscode.postMessage({ command: 'focusSection', index: parseInt(el.dataset.index) });
                    return;
                }
                if ((el = e.target.closest('[data-action="skipElicit"]'))) {
                    vscode.postMessage({ command: 'skipElicitation', sectionId: el.dataset.section });
                    return;
                }
                if ((el = e.target.closest('[data-action="sendElicit"]'))) {
                    var input = document.getElementById('elicit-input-' + el.dataset.index);
                    if (input && input.value.trim()) {
                        vscode.postMessage({ command: 'elicitationRespond', sessionId: el.dataset.session, response: input.value.trim(), sectionId: el.dataset.section });
                    }
                    return;
                }
                if ((el = e.target.closest('[data-action="elicitOption"]'))) {
                    vscode.postMessage({ command: 'elicitationRespond', sessionId: el.dataset.session, response: el.dataset.value, sectionId: el.dataset.section });
                    return;
                }
                if ((el = e.target.closest('[data-action="acceptDraft"]'))) {
                    vscode.postMessage({ command: 'acceptDraft', artifactId: '${escapeHtml(artifactId)}', sectionId: el.dataset.section, draft: el.dataset.draft });
                    return;
                }
                if ((el = e.target.closest('[data-action="dismissCompanion"]'))) {
                    vscode.postMessage({ command: 'dismissCompanion', sectionId: el.dataset.section });
                    return;
                }
                if ((el = e.target.closest('[data-action="dismissArtifactReview"]'))) {
                    var banner = document.getElementById('artifact-review-banner');
                    if (banner) banner.style.display = 'none';
                    return;
                }
                if ((el = e.target.closest('[data-artifact-id]'))) {
                    vscode.postMessage({ command: 'openArtifact', artifactId: el.dataset.artifactId });
                    return;
                }
            });

            // Message handler from extension
            window.addEventListener('message', function(event) {
                var msg = event.data;
                switch (msg.command) {
                    case 'showSpinner': {
                        var panels = document.querySelectorAll('.section-panel');
                        panels.forEach(function(p) {
                            if (p.dataset.sectionId === msg.sectionId) {
                                var idx = p.querySelector('.section-body').id.replace('body-', '');
                                var sp = document.getElementById('spinner-' + idx);
                                if (sp) sp.style.display = 'inline-flex';
                            }
                        });
                        break;
                    }
                    case 'hideSpinner': {
                        var panels = document.querySelectorAll('.section-panel');
                        panels.forEach(function(p) {
                            if (p.dataset.sectionId === msg.sectionId) {
                                var idx = p.querySelector('.section-body').id.replace('body-', '');
                                var sp = document.getElementById('spinner-' + idx);
                                if (sp) sp.style.display = 'none';
                            }
                        });
                        break;
                    }
                    case 'showAutoReview': {
                        var panels = document.querySelectorAll('.section-panel');
                        panels.forEach(function(p) {
                            if (p.dataset.sectionId === msg.sectionId) {
                                var idx = p.querySelector('.section-body').id.replace('body-', '');
                                var container = document.getElementById('autoreview-' + idx);
                                if (!container) return;
                                if (msg.status === 'loading') {
                                    container.innerHTML = '<div class="auto-review-hint"><span class="spinner"></span> Reviewing...</div>';
                                } else if (msg.status === 'done' && msg.observations) {
                                    container.innerHTML = '<div class="auto-review-hint auto-review-done">' +
                                        msg.observations.map(function(o) {
                                            return '<div class="auto-review-item"><span class="badge badge-' + o.severity + '">' + o.severity + '</span> ' + o.text + '</div>';
                                        }).join('') + '</div>';
                                } else if (msg.status === 'error') {
                                    container.innerHTML = '';
                                }
                            }
                        });
                        break;
                    }
                    case 'artifactReviewStatus': {
                        var banner = document.getElementById('artifact-review-banner');
                        if (!banner) break;
                        if (msg.status === 'loading') {
                            banner.innerHTML = '<span class="spinner"></span> <span class="text-sm">Analyzing artifact...</span>';
                            banner.style.display = 'flex';
                        } else if (msg.status === 'done' && msg.observations) {
                            banner.innerHTML = '<div style="flex:1">' +
                                msg.observations.slice(0, 3).map(function(o) {
                                    return '<div class="text-sm" style="margin-bottom:2px"><span class="badge badge-' + o.severity + '">' + o.severity + '</span> ' + o.text + '</div>';
                                }).join('') + '</div>' +
                                '<button class="btn btn-sm btn-secondary" data-action="dismissArtifactReview" style="flex-shrink:0">&times;</button>';
                            banner.style.display = 'flex';
                        } else {
                            banner.style.display = 'none';
                        }
                        break;
                    }
                    case 'elicitationQuestion': {
                        var panels = document.querySelectorAll('.section-panel');
                        panels.forEach(function(p) {
                            if (p.dataset.sectionId === msg.sectionId) {
                                var idx = p.querySelector('.section-body').id.replace('body-', '');
                                var sp = document.getElementById('spinner-' + idx);
                                if (sp) sp.style.display = 'none';
                                var el = document.getElementById('elicitation-' + idx);
                                if (!el) return;
                                var resp = msg.response;
                                if (resp.done && resp.draft) {
                                    el.innerHTML = '<div class="elicitation-panel">' +
                                        '<p class="text-sm"><strong>Draft ready:</strong></p>' +
                                        '<pre>' + resp.draft + '</pre>' +
                                        '<button class="btn btn-primary btn-sm" data-action="acceptDraft" data-section="' + msg.sectionId + '" data-draft="' + resp.draft.replace(/"/g, '&quot;') + '">Accept Draft</button>' +
                                        '</div>';
                                } else {
                                    var questionText = typeof resp.question === 'string' ? resp.question : (resp.question && resp.question.text ? resp.question.text : JSON.stringify(resp.question));
                                    var optionsHtml = '';
                                    if (resp.options && resp.options.length > 0) {
                                        optionsHtml = '<div class="elicitation-options">' +
                                            resp.options.map(function(o) {
                                                return '<button class="option-btn" data-action="elicitOption" data-session="' + resp.sessionId + '" data-value="' + o.value + '" data-section="' + msg.sectionId + '">' + o.label + '</button>';
                                            }).join('') + '</div>';
                                    }
                                    el.innerHTML = '<div class="elicitation-panel">' +
                                        '<p class=\"elicitation-question\">' + questionText + '</p>' +
                                        optionsHtml +
                                        '<div class="flex gap-sm mt-sm">' +
                                        '<input type="text" id="elicit-input-' + idx + '" placeholder="Type your answer..." style="flex:1">' +
                                        '<button class="btn btn-primary btn-sm" data-action="sendElicit" data-index="' + idx + '" data-session="' + resp.sessionId + '" data-section="' + msg.sectionId + '">Send</button>' +
                                        '<button class="btn btn-secondary btn-sm" data-action="skipElicit" data-section="' + msg.sectionId + '">Skip</button>' +
                                        '</div></div>';
                                    var inp = document.getElementById('elicit-input-' + idx);
                                    if (inp) {
                                        inp.addEventListener('keydown', function(e) {
                                            if (e.key === 'Enter') {
                                                e.preventDefault();
                                                vscode.postMessage({ command: 'elicitationRespond', sessionId: resp.sessionId, response: inp.value.trim(), sectionId: msg.sectionId });
                                            }
                                        });
                                    }
                                }
                            }
                        });
                        break;
                    }
                    case 'elicitationDismissed': {
                        var panels = document.querySelectorAll('.section-panel');
                        panels.forEach(function(p) {
                            if (p.dataset.sectionId === msg.sectionId) {
                                var idx = p.querySelector('.section-body').id.replace('body-', '');
                                var el = document.getElementById('elicitation-' + idx);
                                if (el) el.innerHTML = '';
                            }
                        });
                        break;
                    }
                    case 'companionResult': {
                        var panels = document.querySelectorAll('.section-panel');
                        panels.forEach(function(p) {
                            if (p.dataset.sectionId === msg.sectionId) {
                                var idx = p.querySelector('.section-body').id.replace('body-', '');
                                var sp = document.getElementById('spinner-' + idx);
                                if (sp) sp.style.display = 'none';
                                var el = document.getElementById('companion-' + idx);
                                if (!el) return;
                                var obs = msg.observations;
                                el.innerHTML = '<div class="companion-panel">' +
                                    '<button class="dismiss-btn" data-action="dismissCompanion" data-section="' + msg.sectionId + '">&times;</button>' +
                                    '<div class="text-xs text-muted mb-xs" style="font-weight:600">Companion Observations</div>' +
                                    obs.map(function(o) {
                                        return '<div class="companion-observation">' +
                                            '<span class="badge badge-' + o.severity + '">' + o.severity + '</span>' +
                                            '<span class="text-sm">' + o.text + '</span>' +
                                            '</div>';
                                    }).join('') + '</div>';
                            }
                        });
                        break;
                    }
                    case 'companionDismissed': {
                        var panels = document.querySelectorAll('.section-panel');
                        panels.forEach(function(p) {
                            if (p.dataset.sectionId === msg.sectionId) {
                                var idx = p.querySelector('.section-body').id.replace('body-', '');
                                var el = document.getElementById('companion-' + idx);
                                if (el) el.innerHTML = '';
                            }
                        });
                        break;
                    }
                }
            });
        `;
    }
}
