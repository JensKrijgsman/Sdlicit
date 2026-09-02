// ---------------------------------------------------------------------------
// Sdlicit — BDD Overview Provider
// ---------------------------------------------------------------------------
// WebviewPanel that shows all BDD .feature files in a scrollable overview
// with syntax highlighting. Click on any scenario to open it in detail.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { ArtifactStore, LocalArtifact } from '../services/artifactStore';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

export class BddOverviewProvider {
    private panel: vscode.WebviewPanel | undefined;

    constructor(
        private readonly store: ArtifactStore,
        private readonly bddArtifacts: LocalArtifact[],
    ) {}

    show(): void {
        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.bddOverview',
            'Sdlicit — BDD Overview',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );
        this.panel.onDidDispose(() => { this.panel = undefined; });
        this.renderHtml();
    }

    private renderHtml(): void {
        if (!this.panel) { return; }
        const nonce = getNonce();

        // Read all feature files
        const files: Array<{ filename: string; content: string; filePath: string; meta: Record<string, string> }> = [];
        for (const artifact of this.bddArtifacts) {
            const content = this.store.readArtifact(artifact.filePath);
            if (!content) { continue; }
            const meta = this.parseFrontmatter(content);
            files.push({
                filename: artifact.filePath.split('/').pop() || artifact.id,
                content,
                filePath: artifact.filePath,
                meta,
            });
        }

        // Sort by sequence number if available
        files.sort((a, b) => {
            const seqA = parseInt(a.meta.sequence || '999', 10);
            const seqB = parseInt(b.meta.sequence || '999', 10);
            return seqA - seqB;
        });

        let bodyHtml = `
            <div id="bdd-overview">
                <div class="flex items-center justify-between mb-md">
                    <h1 style="margin-bottom:0">BDD Scenarios Overview</h1>
                    <span class="text-sm text-muted">${files.length} feature file(s)</span>
                </div>
        `;

        for (let i = 0; i < files.length; i++) {
            const f = files[i];
            const tracesFrom = f.meta.traces_from || 'unlinked';
            const importance = f.meta.importance || '';
            const title = f.meta.title || f.filename;
            const generated = f.meta.generated_at ? new Date(f.meta.generated_at).toLocaleDateString() : '';

            const importanceColor = importance === 'critical' ? 'var(--vscode-errorForeground)' :
                importance === 'important' ? 'var(--vscode-charts-orange)' : 'var(--vscode-foreground)';

            // Make traces_from clickable if it looks like a requirement ID
            const isReqLink = /^(FR|NFR|REQ)-[\w-]+\d+$/i.test(tracesFrom);
            const traceBadge = isReqLink
                ? `<span class="bdd-badge" data-artifact-id="${escapeHtml(tracesFrom)}" style="cursor:pointer;text-decoration:underline" title="Click to navigate to ${escapeHtml(tracesFrom)}">${escapeHtml(tracesFrom)}</span>`
                : (tracesFrom !== 'unlinked' ? `<span class="bdd-badge">${escapeHtml(tracesFrom)}</span>` : '');

            bodyHtml += `
                <div class="bdd-file-card" data-filepath="${escapeHtml(f.filePath)}" data-index="${i}">
                    <div class="bdd-file-header">
                        <div class="flex items-center gap-sm" style="flex-wrap:wrap">
                            <span class="bdd-file-number">${i + 1}</span>
                            <strong class="bdd-file-title">${escapeHtml(title)}</strong>
                            ${traceBadge}
                            ${importance ? `<span class="bdd-badge" style="color:${importanceColor}">${escapeHtml(importance)}</span>` : ''}
                            ${generated ? `<span class="text-xs text-muted">${generated}</span>` : ''}
                        </div>
                        <button class="btn btn-secondary btn-sm bdd-open-btn" data-filepath="${escapeHtml(f.filePath)}">Open</button>
                    </div>
                    <details>
                        <summary style="cursor:pointer;color:var(--vscode-textLink-foreground);font-size:.85em;user-select:none;margin-top:6px">View Gherkin</summary>
                        <div class="gherkin-formatted mt-xs">${this.formatGherkin(f.content)}</div>
                    </details>
                </div>
            `;
        }

        bodyHtml += '</div>';

        const scripts = `
            document.addEventListener('click', function(e) {
                var btn = e.target.closest('.bdd-open-btn');
                if (btn) {
                    var fp = btn.dataset.filepath;
                    if (fp) {
                        vscode.postMessage({ command: 'openFile', filePath: fp });
                    }
                    return;
                }
                var traceNode = e.target.closest('[data-artifact-id]');
                if (traceNode) {
                    vscode.postMessage({ command: 'openArtifact', artifactId: traceNode.dataset.artifactId });
                    return;
                }
            });
        `;

        const extraStyles = `
            .bdd-file-card {
                border: 1px solid var(--vscode-panel-border);
                border-left: 4px solid var(--vscode-charts-blue);
                border-radius: 6px;
                padding: 14px 18px;
                margin-bottom: 14px;
                background: var(--vscode-editor-background);
                box-shadow: 0 1px 3px rgba(0,0,0,.08);
            }
            .bdd-file-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
            }
            .bdd-file-number {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 24px; height: 24px;
                border-radius: 50%;
                background: var(--vscode-badge-background);
                color: var(--vscode-badge-foreground);
                font-size: .75em;
                font-weight: 700;
            }
            .bdd-file-title { font-size: .95em; }
            .bdd-badge {
                display: inline-block;
                padding: 2px 8px;
                border-radius: 10px;
                font-size: .78em;
                font-weight: 500;
                background: var(--vscode-badge-background);
                color: var(--vscode-badge-foreground);
            }
        `;

        this.panel.webview.html = wrapHtml(bodyHtml, nonce, scripts, extraStyles);

        // Handle messages from webview
        this.panel.webview.onDidReceiveMessage((msg) => {
            if (msg.command === 'openFile' && msg.filePath) {
                vscode.commands.executeCommand('sdlicit.viewBDD', msg.filePath);
            }
            if (msg.command === 'openArtifact' && msg.artifactId) {
                vscode.commands.executeCommand('sdlicit.openCanvas', msg.artifactId);
            }
        });
    }

    /** Parse comment-based frontmatter from a feature file. */
    private parseFrontmatter(content: string): Record<string, string> {
        const meta: Record<string, string> = {};
        const lines = content.split('\n');
        let inFrontmatter = false;

        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed === '# --- sdlicit frontmatter ---') {
                inFrontmatter = true;
                continue;
            }
            if (trimmed === '# ---' && inFrontmatter) {
                break;
            }
            if (inFrontmatter && trimmed.startsWith('#')) {
                const matchKV = trimmed.match(/^#\s*(\w+):\s*(.+)$/);
                if (matchKV) {
                    meta[matchKV[1]] = matchKV[2].trim();
                }
            }
            // Legacy comment metadata (non-frontmatter style)
            if (!inFrontmatter && trimmed.startsWith('#')) {
                const matchKV = trimmed.match(/^#\s*(\w+):\s*(.+)$/);
                if (matchKV) {
                    meta[matchKV[1]] = matchKV[2].trim();
                }
            }
            // Stop at first non-comment line
            if (!trimmed.startsWith('#') && trimmed !== '') {
                if (!inFrontmatter) { break; }
            }
        }
        return meta;
    }

    /** Format Gherkin with syntax highlighting HTML. */
    private formatGherkin(text: string): string {
        const lines = text.split('\n');
        let html = '';
        for (const line of lines) {
            const t = line.trim();
            let cls = 'gherkin-line';
            if (t.startsWith('Feature:')) { cls += ' gherkin-feature'; }
            else if (t.startsWith('Scenario:') || t.startsWith('Scenario Outline:')) { cls += ' gherkin-scenario'; }
            else if (/^(Given|When|Then|And|But) /.test(t)) { cls += ' gherkin-step'; }
            else if (t.startsWith('Background:') || t.startsWith('Examples:')) { cls += ' gherkin-scenario'; }
            else if (t.startsWith('|')) { cls += ' gherkin-table'; }
            else if (t.startsWith('#')) { cls += ' gherkin-comment'; }
            else if (t.startsWith('@')) { cls += ' gherkin-tag'; }

            const kwMatch = t.match(/^(Feature:|Scenario Outline:|Scenario:|Given|When|Then|And|But|Background:|Examples:)/);
            if (kwMatch) {
                const afterKw = escapeHtml(line.substring(line.indexOf(kwMatch[1]) + kwMatch[1].length));
                const highlighted = this.highlightContent(afterKw);
                html += `<div class="${cls}"><span class="gherkin-keyword">${kwMatch[1]}</span>${highlighted}</div>`;
            } else if (t.startsWith('|')) {
                const tableHtml = escapeHtml(line).replace(/\|/g, '<span class="gherkin-cell-sep">|</span>');
                html += `<div class="${cls}">${tableHtml}</div>`;
            } else if (t.startsWith('@')) {
                html += `<div class="${cls}">${escapeHtml(line)}</div>`;
            } else {
                html += `<div class="${cls}">${this.highlightContent(escapeHtml(line))}</div>`;
            }
        }
        return html;
    }

    /** Highlight quoted strings and <parameters> in step text. */
    private highlightContent(text: string): string {
        let result = text;
        result = result.replace(/&quot;([^&]*?)&quot;|"([^"]*?)"/g, (_, a, b) => {
            const inner = a !== undefined ? a : b;
            return `<span class="gherkin-string">"${inner}"</span>`;
        });
        result = result.replace(/&lt;([^&]+?)&gt;/g, (_, inner) => {
            return `<span class="gherkin-param">&lt;${inner}&gt;</span>`;
        });
        return result;
    }
}
