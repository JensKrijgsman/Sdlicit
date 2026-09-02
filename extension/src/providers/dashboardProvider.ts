// ---------------------------------------------------------------------------
// Sdlicit — Dashboard Provider (Primary Sidebar)
// ---------------------------------------------------------------------------
// Tabbed view: Overview | Questions | BDD Tests
// - Mini trace graph (SOW → REQ → ADR → SCN chips)
// - Coverage progress bars
// - Quality scores per artifact
// - Open questions list
// - BDD test summary with filter-by-requirement
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';
import { Artifact, OpenQuestion } from '../types';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

export class DashboardProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;

    constructor(private readonly data: DataService) {}

    resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken,
    ): void {
        this.view = webviewView;
        webviewView.webview.options = { enableScripts: true };
        this.render();

        webviewView.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.command) {
                case 'openTraceGraph':
                    vscode.commands.executeCommand('sdlicit.openTraceGraph');
                    break;
                case 'openQuestionInArtifact':
                    vscode.commands.executeCommand('sdlicit.openCanvas', msg.artifactId);
                    break;
            }
        });
    }

    refresh(): void {
        this.render();
    }

    private async render(): Promise<void> {
        if (!this.view) { return; }
        const nonce = getNonce();

        const dashboard = await this.data.getDashboard();
        const artifacts = this.data.getArtifacts();
        const { coverage, openQuestions } = dashboard;

        // ── Helpers ──
        const progressBar = (pct: number, label: string, color?: string) => `
            <div class="metric-row">
                <span class="metric-label">${label}</span>
                <span class="metric-value">${pct}%</span>
            </div>
            <div class="progress-bar mb-sm"><div class="progress-fill" style="width:${pct}%;${color ? `background:${color}` : ''}"></div></div>`;

        // ── Mini trace graph ──
        const sowCount = artifacts.filter(a => a.type === 'sow').length;
        const reqCount = artifacts.filter(a => a.type === 'requirement').length;
        const adrCount = artifacts.filter(a => a.type === 'decision').length;
        const scnCount = artifacts.filter(a => a.type === 'scenario').length;

        const traceGraphHtml = `
            <div class="card-flat mb-md" style="cursor:pointer" data-action="openTraceGraph">
                <div class="flex items-center justify-between mb-xs">
                    <span class="text-xs text-muted">Artifact Pipeline</span>
                    <span class="text-xs" style="color:var(--vscode-textLink-foreground)">Open Graph →</span>
                </div>
                <div class="trace-graph">
                    <span class="trace-node"><span class="pill pill-sow">SOW</span> ${sowCount}</span>
                    <span class="trace-arrow">&rarr;</span>
                    <span class="trace-node"><span class="pill pill-req">REQ</span> ${reqCount}</span>
                    <span class="trace-arrow">&rarr;</span>
                    <span class="trace-node"><span class="pill pill-adr">ADR</span> ${adrCount}</span>
                    <span class="trace-arrow">&rarr;</span>
                    <span class="trace-node"><span class="pill pill-scn">SCN</span> ${scnCount}</span>
                </div>
            </div>`;

        // ── Requirements listing (non-clickable) ──
        const requirements = artifacts.filter(a => a.type === 'requirement');
        const functionalReqs = requirements.filter(r => r.id.startsWith('FR-') || r.id.startsWith('REQ-'));
        const nonFunctionalReqs = requirements.filter(r => r.id.startsWith('NFR-'));
        // If the prefix-based split doesn't capture all, put remainder in functional
        const classifiedIds = new Set([...functionalReqs.map(r => r.id), ...nonFunctionalReqs.map(r => r.id)]);
        const unclassified = requirements.filter(r => !classifiedIds.has(r.id));
        const allFR = [...functionalReqs, ...unclassified];

        const renderReqList = (reqs: typeof requirements, heading: string) => {
            if (reqs.length === 0) { return ''; }
            return `
                <details class="mb-sm" open>
                    <summary style="cursor:pointer;user-select:none;font-size:.85em;font-weight:600;color:var(--vscode-foreground);padding:4px 0">
                        ${heading} (${reqs.length})
                    </summary>
                    <div style="padding-left:8px">
                    ${reqs.map(r => {
                        const scenarioCount = r.traces.testedBy?.length ?? 0;
                        const implCount = r.traces.implements?.length ?? 0;
                        const linkedCount = (r.traces.downstream?.length ?? 0) + (r.traces.upstream?.length ?? 0) + implCount;
                        return `
                            <div class="flex items-center justify-between" style="padding:4px 0;border-bottom:1px solid var(--vscode-panel-border)">
                                <div class="flex items-center gap-sm" style="flex:1;min-width:0">
                                    <span class="text-xs" style="font-weight:600;min-width:56px;flex-shrink:0">${escapeHtml(r.id)}</span>
                                    <span class="text-xs" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(r.title)}</span>
                                </div>
                                <div class="flex items-center gap-xs" style="flex-shrink:0">
                                    ${scenarioCount > 0
                                        ? `<span class="text-xs" style="padding:1px 6px;border-radius:8px;background:var(--vscode-testing-iconPassed);color:#fff" title="${scenarioCount} scenario(s)">${scenarioCount} scn</span>`
                                        : '<span class="text-xs text-muted" title="No BDD scenarios">○</span>'}
                                    ${linkedCount > 0
                                        ? `<span class="text-xs" style="padding:1px 6px;border-radius:8px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)" title="${linkedCount} linked artifact(s)">${linkedCount} links</span>`
                                        : ''}
                                </div>
                            </div>`;
                    }).join('')}
                    </div>
                </details>`;
        };

        const requirementsHtml = requirements.length > 0
            ? `<h3>Requirements (${requirements.length})</h3>
               <div class="card-flat">
                   ${renderReqList(allFR, 'Functional Requirements')}
                   ${renderReqList(nonFunctionalReqs, 'Non-Functional Requirements')}
               </div>`
            : '';

        // ── Coverage ──
        const reqWithScenarios = coverage.requirementsWithScenarios;
        const reqCov = coverage.requirementsCount > 0 ? Math.round((reqWithScenarios / coverage.requirementsCount) * 100) : 0;
        const traceCov = coverage.traceCoverage;

        // Link stats
        const totalLinks = coverage.totalLinks ?? 0;
        const validLinks = coverage.validLinks ?? 0;
        const brokenLinks = coverage.brokenLinks ?? 0;
        const linkCov = totalLinks > 0 ? Math.round((validLinks / totalLinks) * 100) : 0;

        const overviewHtml = `
            ${traceGraphHtml}
            <h3>Coverage</h3>
            <div class="card-flat">
                ${progressBar(traceCov, 'Artifact connectivity', traceCov >= 80 ? 'var(--vscode-testing-iconPassed)' : traceCov >= 50 ? 'var(--vscode-charts-orange)' : 'var(--vscode-errorForeground)')}
                ${progressBar(reqCov, `Requirements → Scenarios (${reqWithScenarios}/${coverage.requirementsCount})`, reqCov >= 80 ? 'var(--vscode-testing-iconPassed)' : reqCov >= 50 ? 'var(--vscode-charts-orange)' : 'var(--vscode-errorForeground)')}
                ${progressBar(linkCov, `Trace links (${validLinks}/${totalLinks}${brokenLinks > 0 ? `, ${brokenLinks} broken` : ''})`, linkCov >= 80 ? 'var(--vscode-testing-iconPassed)' : linkCov >= 50 ? 'var(--vscode-charts-orange)' : 'var(--vscode-errorForeground)')}
            </div>`;

        // ── Questions tab ──
        // Socratic probes are tracked per-panel while a panel is open, not
        // persisted across sessions yet, so this list only ever reflects
        // probes surfaced during panels still open right now, not a full
        // history. See DashboardSummary.openQuestions.
        const unresolved = openQuestions.filter(q => !('resolved' in q));
        const questionsHtml = `
            <h3>Open Questions (${unresolved.length})</h3>
            ${unresolved.length === 0 ? '<div class="text-muted text-sm">No open questions from panels currently open. Socratic questions are not yet tracked across sessions.</div>' : ''}
            ${unresolved.map(q => `
                <div class="card-flat" style="cursor:pointer" data-action="openQuestion" data-artifact-id="${escapeHtml(q.artifactId)}">
                    <div class="flex items-center gap-sm">
                        <span class="text-sm"><strong>${escapeHtml(q.artifactId)}</strong></span>
                        ${q.sectionId ? `<span class="pill pill-req" style="font-size:.65em">${escapeHtml(q.sectionId)}</span>` : ''}
                    </div>
                    <div class="text-sm mt-xs">${escapeHtml(q.text)}</div>
                    <div class="text-xs text-muted mt-xs">Source: ${escapeHtml(q.source)}</div>
                </div>
            `).join('')}`;

        const body = `
            <div class="tab-bar">
                <button class="tab-btn active" data-tab="overview">Overview</button>
                <button class="tab-btn" data-tab="requirements">Requirements <span class="text-xs">(${requirements.length})</span></button>
                <button class="tab-btn" data-tab="questions">Questions <span class="text-xs">(${unresolved.length})</span></button>
            </div>
            <div class="tab-content active" id="tab-overview">${overviewHtml}</div>
            <div class="tab-content" id="tab-requirements">${requirementsHtml || '<div class="text-muted text-sm">No requirements found. Generate an SRS first.</div>'}</div>
            <div class="tab-content" id="tab-questions">${questionsHtml}</div>`;

        const scripts = `
            // Tab switching
            document.querySelectorAll('.tab-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
                    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
                    btn.classList.add('active');
                    var t = document.getElementById('tab-' + btn.dataset.tab);
                    if (t) t.classList.add('active');
                });
            });

            // Click delegation
            document.addEventListener('click', function(e) {
                var el;
                if ((el = e.target.closest('[data-action="openQuestion"]'))) {
                    vscode.postMessage({ command: 'openQuestionInArtifact', artifactId: el.dataset.artifactId });
                    return;
                }
                if ((el = e.target.closest('[data-action="openTraceGraph"]'))) {
                    vscode.postMessage({ command: 'openTraceGraph' });
                    return;
                }
            });
        `;

        this.view.webview.html = wrapHtml(body, nonce, scripts);
    }
}
