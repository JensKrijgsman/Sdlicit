// ---------------------------------------------------------------------------
// Sdlicit — Token Tracker + Status Bar
// ---------------------------------------------------------------------------
// Tracks token usage from backend response headers and displays in status bar.
// Shows: connection state, model name, running token total, and a spinner
// when backend calls are in progress.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SdlicitClient, TokenUsage } from './sdlicitClient';
import { ServerLifecycle, ServerState } from './serverLifecycle';

export class StatusBarManager {
    private item: vscode.StatusBarItem;
    private serverState: ServerState = 'disconnected';
    private model: string = '?';
    private tokens: number = 0;
    private activeCalls: number = 0;

    constructor(
        private readonly client: SdlicitClient,
        private readonly lifecycle: ServerLifecycle,
    ) {
        this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
        this.item.command = 'sdlicit.showTokenDetails';
        this.item.show();

        // Listen for state changes
        lifecycle.onStateChange(state => {
            this.serverState = state;
            this.render();
        });

        // Listen for token updates
        client.onTokenUpdate(usage => {
            this.tokens = usage.total;
            this.render();
        });

        this.render();
    }

    setModel(model: string): void {
        this.model = model;
        this.render();
    }

    /** Call this before a backend request to show spinner. */
    startCall(): void {
        this.activeCalls++;
        this.render();
        const showNotification = vscode.workspace.getConfiguration('sdlicit').get<boolean>('showCallNotifications', false);
        if (showNotification) {
            vscode.window.setStatusBarMessage('$(loading~spin) Sdlicit: calling backend…', 10000);
        }
    }

    /** Call this after a backend request completes. */
    endCall(): void {
        this.activeCalls = Math.max(0, this.activeCalls - 1);
        this.render();
    }

    private render(): void {
        const stateIcon = this.stateIcon();
        const tokenStr = this.tokens > 0 ? ` | ${this.formatTokens(this.tokens)}` : '';
        const callingStr = this.activeCalls > 0 ? ' $(loading~spin)' : '';
        this.item.text = `${stateIcon} ${this.model}${tokenStr}${callingStr}`;
        this.item.tooltip = this.buildTooltip();
        this.item.backgroundColor = this.serverState === 'error'
            ? new vscode.ThemeColor('statusBarItem.errorBackground')
            : this.activeCalls > 0
                ? new vscode.ThemeColor('statusBarItem.warningBackground')
                : undefined;
    }

    private stateIcon(): string {
        switch (this.serverState) {
            case 'connected': return '$(circuit-board)';
            case 'starting': return '$(loading~spin)';
            case 'error': return '$(error)';
            default: return '$(debug-disconnect)';
        }
    }

    private formatTokens(n: number): string {
        if (n >= 1_000_000) { return `${(n / 1_000_000).toFixed(1)}M tok`; }
        if (n >= 1_000) { return `${(n / 1_000).toFixed(1)}k tok`; }
        return `${n} tok`;
    }

    private buildTooltip(): string {
        const lines: string[] = ['Sdlicit Backend'];
        lines.push(`State: ${this.serverState}`);
        lines.push(`Model: ${this.model}`);
        if (this.activeCalls > 0) {
            lines.push(`Active calls: ${this.activeCalls}`);
        }
        if (this.tokens > 0) {
            const usage = this.client.totalUsage;
            lines.push(`Tokens: ${usage.total} (prompt: ${usage.prompt}, completion: ${usage.completion})`);
            lines.push(`Calls: ${usage.calls}`);
            if (Object.keys(usage.byAgent).length > 0) {
                lines.push('By agent:');
                for (const [agent, u] of Object.entries(usage.byAgent)) {
                    lines.push(`  ${agent}: ${u.total} (${u.calls} call${u.calls !== 1 ? 's' : ''}, p:${u.prompt} c:${u.completion})`);
                }
            }
        }
        return lines.join('\n');
    }

    dispose(): void {
        this.item.dispose();
    }
}
