// ---------------------------------------------------------------------------
// Sdlicit — Session Tree Provider
// ---------------------------------------------------------------------------
// Sidebar tree showing session history from .sdlicit/sessions/.
// Expandable sessions reveal event replay (ToM + log data).
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';
import { SessionSummary, SessionMeta, SessionEvent } from '../types';

class SessionNode extends vscode.TreeItem {
    constructor(
        public readonly session: SessionSummary,
        public readonly meta?: SessionMeta,
    ) {
        super(
            `${session.session_id.slice(0, 8)}…`,
            vscode.TreeItemCollapsibleState.Collapsed,
        );
        const startDate = new Date(session.started_at);
        this.description = `${startDate.toLocaleDateString()} ${startDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
        this.tooltip = [
            `Session: ${session.session_id}`,
            `Status: ${session.status}`,
            `Started: ${session.started_at}`,
            `Last event: ${session.last_event_at}`,
            meta ? `Events: ${meta.event_count} | Tokens: ${meta.tokens.total_tokens}` : '',
        ].filter(Boolean).join('\n');
        this.contextValue = 'sdlicitSession';

        if (session.status === 'active') {
            this.iconPath = new vscode.ThemeIcon('debug-start', new vscode.ThemeColor('charts.green'));
        } else if (session.status === 'crashed') {
            this.iconPath = new vscode.ThemeIcon('warning', new vscode.ThemeColor('charts.red'));
        } else {
            this.iconPath = new vscode.ThemeIcon('history', new vscode.ThemeColor('charts.foreground'));
        }
    }
}

class SectionNode extends vscode.TreeItem {
    constructor(label: string, public readonly children: TreeNode[]) {
        super(label, vscode.TreeItemCollapsibleState.Collapsed);
        this.contextValue = 'sdlicitSessionSection';
        this.iconPath = label === 'ToM Interactions'
            ? new vscode.ThemeIcon('person', new vscode.ThemeColor('charts.blue'))
            : new vscode.ThemeIcon('output', new vscode.ThemeColor('charts.purple'));
    }
}

class EventNode extends vscode.TreeItem {
    constructor(public readonly event: SessionEvent) {
        super(`${String(event.seq).padStart(3, '0')} — ${event.kind}`, vscode.TreeItemCollapsibleState.None);
        const time = new Date(event.ts);
        this.description = time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        this.tooltip = JSON.stringify(event, null, 2);
        this.contextValue = 'sdlicitEvent';
        this.command = {
            command: 'sdlicit.showEventDetail',
            title: 'Show Event Detail',
            arguments: [event],
        };

        // Icon based on event kind
        const kind = event.kind;
        if (kind.includes('error')) {
            this.iconPath = new vscode.ThemeIcon('error', new vscode.ThemeColor('charts.red'));
        } else if (kind.includes('start') || kind.includes('cli_start')) {
            this.iconPath = new vscode.ThemeIcon('play', new vscode.ThemeColor('charts.green'));
        } else if (kind.includes('end')) {
            this.iconPath = new vscode.ThemeIcon('debug-stop', new vscode.ThemeColor('charts.red'));
        } else if (kind.includes('menu')) {
            this.iconPath = new vscode.ThemeIcon('list-selection');
        } else if (kind.includes('expansion') || kind.includes('kb')) {
            this.iconPath = new vscode.ThemeIcon('database', new vscode.ThemeColor('charts.blue'));
        } else if (kind.includes('intake') || kind.includes('sow')) {
            this.iconPath = new vscode.ThemeIcon('file-text', new vscode.ThemeColor('charts.blue'));
        } else {
            this.iconPath = new vscode.ThemeIcon('circle-outline');
        }
    }
}

class TokenSummaryNode extends vscode.TreeItem {
    constructor(meta: SessionMeta) {
        super('Token Usage', vscode.TreeItemCollapsibleState.None);
        this.description = `${meta.tokens.total_tokens} tokens (${meta.tokens.calls} calls)`;
        this.tooltip = Object.entries(meta.tokens.by_agent)
            .map(([agent, u]) => `${agent}: ${u.total_tokens} tokens`)
            .join('\n') || 'No per-agent breakdown';
        this.iconPath = new vscode.ThemeIcon('dashboard', new vscode.ThemeColor('charts.yellow'));
        this.contextValue = 'sdlicitTokens';
    }
}

type TreeNode = SessionNode | SectionNode | EventNode | TokenSummaryNode;

export class SessionTreeProvider implements vscode.TreeDataProvider<TreeNode> {
    private _onDidChangeTreeData = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    constructor(private readonly data: DataService) {}

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: TreeNode): vscode.TreeItem { return element; }

    getParent(element: TreeNode): TreeNode | undefined {
        // Flat enough that we don't need reverse-lookup
        return undefined;
    }

    getChildren(element?: TreeNode): TreeNode[] {
        if (!element) {
            // Root: list sessions from index
            const index = this.data.getSessionIndex();
            return index.recent.map(s => {
                const meta = this.data.getSessionMeta(s.session_id);
                return new SessionNode(s, meta);
            });
        }

        if (element instanceof SessionNode) {
            const children: TreeNode[] = [];
            // Token summary first
            if (element.meta) {
                children.push(new TokenSummaryNode(element.meta));
            }
            // ToM interactions section
            const tomEvents = this.data.getSessionEvents(element.session.session_id);
            const tomNodes = tomEvents.map(ev => new EventNode(ev));
            // Log entries section
            const logEvents = this.data.getSessionLog(element.session.session_id);
            const logNodes = logEvents.map(ev => new EventNode(ev));

            if (tomNodes.length > 0 && logNodes.length > 0) {
                // Show both as sub-sections
                children.push(new SectionNode('ToM Interactions', tomNodes));
                children.push(new SectionNode('Activity Log', logNodes));
            } else if (tomNodes.length > 0) {
                // Only ToM data — show flat
                children.push(...tomNodes);
            } else if (logNodes.length > 0) {
                // Only log data — show flat
                children.push(...logNodes);
            }
            return children;
        }

        if (element instanceof SectionNode) {
            return element.children;
        }

        return [];
    }
}
