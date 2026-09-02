// ---------------------------------------------------------------------------
// Sdlicit — Status Panel Provider (Bottom Panel)
// ---------------------------------------------------------------------------
// Shows in the Panel area (like Terminal/Output):
//   Top half: Quick actions (compact tokens, change model)
//   Bottom half: Token usage breakdown, model info, session details
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SdlicitClient, TokenUsage, CallLogEntry } from '../services/sdlicitClient';
import { ServerLifecycle, ServerState } from '../services/serverLifecycle';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

export class StatusPanelProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private serverState: ServerState = 'disconnected';
    private model: string = '?';

    constructor(
        private readonly client: SdlicitClient,
        private readonly lifecycle: ServerLifecycle,
    ) {
        lifecycle.onStateChange(state => {
            this.serverState = state;
            this.render();
        });
        client.onTokenUpdate(() => {
            this.render();
        });
        client.onCallLogUpdate(() => {
            this.render();
        });
    }

    setModel(model: string): void {
        this.model = model;
        this.render();
    }

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
                case 'compact':
                    vscode.commands.executeCommand('sdlicit.compactSession');
                    break;
                case 'resetTokens':
                    this.client.resetTokenUsage();
                    this.render();
                    break;
                case 'changeModel': {
                    const config = vscode.workspace.getConfiguration('sdlicit');
                    const current = config.get<string>('models.default', '');
                    const input = await vscode.window.showInputBox({
                        title: 'Change Default Model',
                        prompt: 'Enter model identifier (e.g. openai/gpt-4o-mini)',
                        value: current,
                    });
                    if (input !== undefined) {
                        await config.update('models.default', input, vscode.ConfigurationTarget.Workspace);
                        this.model = input;
                        this.render();
                    }
                    break;
                }
                case 'startServer':
                    vscode.commands.executeCommand('sdlicit.startServer');
                    break;
                case 'openSettings':
                    vscode.commands.executeCommand('workbench.action.openSettings', 'sdlicit');
                    break;
            }
        });
    }

    private render(): void {
        if (!this.view) { return; }
        const nonce = getNonce();
        const usage = this.client.totalUsage;
        const callLog = this.client.callLog;

        const stateColor: Record<string, string> = {
            connected: 'var(--sdl-color-success)',
            starting: 'var(--sdl-color-warning)',
            error: 'var(--sdl-color-danger)',
            disconnected: 'var(--vscode-descriptionForeground)',
        };

        // Agent breakdown — full detail table
        const agentCount = Object.keys(usage.byAgent).length;
        let agentTable = '';
        if (agentCount > 0) {
            const agentRows = Object.entries(usage.byAgent)
                .sort(([, a], [, b]) => b.total - a.total)
                .map(([agent, u]) => `
                    <tr>
                        <td class="agent-name">${escapeHtml(agent)}</td>
                        <td class="num">${u.calls}</td>
                        <td class="num">${this.formatTokens(u.prompt)}</td>
                        <td class="num">${this.formatTokens(u.completion)}</td>
                        <td class="num agent-total">${this.formatTokens(u.total)}</td>
                    </tr>
                `).join('');

            agentTable = `
                <h3>Subagents (${agentCount})</h3>
                <div class="card-flat">
                    <table class="agent-table">
                        <thead><tr>
                            <th>Agent</th>
                            <th class="num">Calls</th>
                            <th class="num">Prompt</th>
                            <th class="num">Completion</th>
                            <th class="num">Total</th>
                        </tr></thead>
                        <tbody>${agentRows}</tbody>
                    </table>
                </div>
            `;
        }

        // Call timeline — last 20 entries, newest first
        let timelineHtml = '';
        if (callLog.length > 0) {
            const recent = callLog.slice(-20).reverse();
            const rows = recent.map(entry => {
                const time = new Date(entry.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                const agents = Object.keys(entry.agents);
                const agentBadges = agents.map(a =>
                    '<span class="timeline-agent">' + escapeHtml(a) + '</span>'
                ).join('');
                return `
                    <div class="timeline-entry">
                        <div class="timeline-header">
                            <span class="timeline-time">${time}</span>
                            <span class="timeline-endpoint">${escapeHtml(entry.endpoint)}</span>
                            <span class="timeline-tokens">${this.formatTokens(entry.totalTokens)}</span>
                            <span class="timeline-duration">${entry.durationMs}ms</span>
                        </div>
                        <div class="timeline-agents">${agentBadges || '<span class="text-muted">no agents</span>'}</div>
                    </div>
                `;
            }).join('');

            timelineHtml = `
                <h3>Call Timeline (last ${Math.min(callLog.length, 20)} of ${callLog.length})</h3>
                <div class="card-flat timeline-container">
                    ${rows}
                </div>
            `;
        }

        const body = `
            <!-- Quick Actions -->
            <h3>Quick Actions</h3>
            <div class="flex gap-sm flex-wrap mb-md">
                <button class="btn btn-secondary btn-sm" data-action="compact">Compact Tokens</button>
                <button class="btn btn-secondary btn-sm" data-action="changeModel">Change Model</button>
                <button class="btn btn-secondary btn-sm" data-action="resetTokens">Reset Counter</button>
                <button class="btn btn-secondary btn-sm" data-action="openSettings">Settings</button>
                ${this.serverState !== 'connected' ? `<button class="btn btn-primary btn-sm" data-action="startServer">Start Server</button>` : ''}
            </div>

            <!-- Status Grid -->
            <div class="status-grid mb-md">
                <div class="status-cell">
                    <div class="label">Connection</div>
                    <div class="value" style="color:${stateColor[this.serverState] ?? stateColor.disconnected}">${this.serverState}</div>
                </div>
                <div class="status-cell">
                    <div class="label">Model</div>
                    <div class="value" style="font-size:.85em">${escapeHtml(this.model)}</div>
                </div>
                <div class="status-cell">
                    <div class="label">Total Tokens</div>
                    <div class="value">${this.formatTokens(usage.total)}</div>
                </div>
                <div class="status-cell">
                    <div class="label">API Calls</div>
                    <div class="value">${usage.calls}</div>
                </div>
                <div class="status-cell">
                    <div class="label">Subagents</div>
                    <div class="value">${agentCount}</div>
                </div>
            </div>

            <!-- Token Breakdown -->
            <h3>Token Breakdown</h3>
            <div class="card-flat">
                <div class="metric-row">
                    <span class="metric-label">Prompt tokens</span>
                    <span class="metric-value">${this.formatTokens(usage.prompt)}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Completion tokens</span>
                    <span class="metric-value">${this.formatTokens(usage.completion)}</span>
                </div>
            </div>

            ${agentTable}
            ${timelineHtml}
        `;

        const scripts = `
            document.addEventListener('click', function(e) {
                var el;
                if ((el = e.target.closest('[data-action="compact"]'))) { vscode.postMessage({ command: 'compact' }); return; }
                if ((el = e.target.closest('[data-action="resetTokens"]'))) { vscode.postMessage({ command: 'resetTokens' }); return; }
                if ((el = e.target.closest('[data-action="changeModel"]'))) { vscode.postMessage({ command: 'changeModel' }); return; }
                if ((el = e.target.closest('[data-action="startServer"]'))) { vscode.postMessage({ command: 'startServer' }); return; }
                if ((el = e.target.closest('[data-action="openSettings"]'))) { vscode.postMessage({ command: 'openSettings' }); return; }
            });
        `;

        const extraStyles = `
            .agent-table { width: 100%; border-collapse: collapse; font-size: .85em; }
            .agent-table th, .agent-table td { padding: 3px 6px; text-align: left; border-bottom: 1px solid var(--sdl-border); }
            .agent-table th { font-weight: 600; color: var(--vscode-descriptionForeground); font-size: .8em; text-transform: uppercase; }
            .agent-table .num { text-align: right; font-variant-numeric: tabular-nums; }
            .agent-name { font-weight: 500; }
            .agent-total { font-weight: 600; }

            .timeline-container { max-height: 300px; overflow-y: auto; }
            .timeline-entry { padding: 4px 0; border-bottom: 1px solid var(--sdl-border); }
            .timeline-entry:last-child { border-bottom: none; }
            .timeline-header { display: flex; align-items: center; gap: 8px; font-size: .82em; }
            .timeline-time { color: var(--vscode-descriptionForeground); font-variant-numeric: tabular-nums; min-width: 65px; }
            .timeline-endpoint { flex: 1; font-family: var(--vscode-editor-font-family); font-size: .9em; color: var(--vscode-textLink-foreground); }
            .timeline-tokens { font-weight: 600; font-variant-numeric: tabular-nums; }
            .timeline-duration { color: var(--vscode-descriptionForeground); font-size: .9em; }
            .timeline-agents { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
            .timeline-agent { background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); padding: 1px 6px; border-radius: 3px; font-size: .78em; }
        `;

        this.view.webview.html = wrapHtml(body, nonce, scripts, extraStyles);
    }

    private formatTokens(n: number): string {
        if (n >= 1_000_000) { return `${(n / 1_000_000).toFixed(1)}M`; }
        if (n >= 1_000) { return `${(n / 1_000).toFixed(1)}k`; }
        return `${n}`;
    }
}
