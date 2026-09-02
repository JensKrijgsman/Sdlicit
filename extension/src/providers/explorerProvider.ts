// ---------------------------------------------------------------------------
// Sdlicit — Knowledge Explorer Provider
// ---------------------------------------------------------------------------
// Sidebar webview for querying the curated knowledge base (RAG).
// Uses DataService which routes through the backend.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { DataService } from '../services/dataService';
import { KBSource } from '../types';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

interface ChatEntry {
    role: 'user' | 'assistant';
    content: string;
    sources?: KBSource[];
}

export class ExplorerProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private history: ChatEntry[] = [];

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
            if (msg.command === 'query') {
                await this.handleQuery(msg.text);
            } else if (msg.command === 'clear') {
                this.history = [];
                this.render();
            }
        });
    }

    private async handleQuery(text: string): Promise<void> {
        if (!text.trim()) { return; }

        this.history.push({ role: 'user', content: text });
        this.render();

        try {
            const response = await this.data.queryKnowledgeBase(text);
            this.history.push({
                role: 'assistant',
                content: response.answer,
                sources: response.sources,
            });
        } catch {
            this.history.push({
                role: 'assistant',
                content: 'Error processing your query. Is the backend running?',
            });
        }
        this.render();
    }

    private render(): void {
        if (!this.view) { return; }
        const nonce = getNonce();

        const messagesHtml = this.history.map(entry => {
            const cssClass = entry.role === 'user' ? 'chat-user' : 'chat-assistant';
            const sourcesHtml = entry.sources && entry.sources.length > 0
                ? `<div class="chat-sources mt-xs">${entry.sources.map(s =>
                    `<span class="chat-source-badge" title="${escapeHtml(s.snippet ?? '')}">${escapeHtml(s.ref)}${s.relevance ? ` (${Math.round(s.relevance * 100)}%)` : ''}</span>`
                ).join('')}</div>`
                : '';
            return `<div class="chat-message ${cssClass}"><div class="text-sm">${escapeHtml(entry.content)}</div>${sourcesHtml}</div>`;
        }).join('');

        const body = `
            <div class="flex flex-col" style="height:100%;overflow:hidden">
                <div class="flex items-center justify-between mb-sm">
                    <h3>Knowledge Explorer</h3>
                    <button class="btn btn-icon" onclick="clearChat()" title="Clear">🗑</button>
                </div>
                <p class="text-xs text-muted mb-sm">Query IEEE standards, design patterns, SE best practices via RAG.</p>

                <div class="scroll-area" id="messages" style="flex:1;overflow-y:auto">
                    ${messagesHtml || '<p class="text-sm text-muted text-center" style="padding:16px">Ask a question about the knowledge base…</p>'}
                </div>

                <div class="flex gap-sm mt-sm" style="flex-shrink:0">
                    <input type="text" id="queryInput" placeholder="e.g., What does ISO 25010 say about…" style="flex:1" />
                    <button class="btn btn-primary btn-sm" onclick="send()">Ask</button>
                </div>
            </div>
        `;

        const scripts = `
            const vscode = acquireVsCodeApi();
            const input = document.getElementById('queryInput');
            const messages = document.getElementById('messages');

            function send() {
                const text = input.value.trim();
                if (!text) return;
                vscode.postMessage({ command: 'query', text });
                input.value = '';
            }
            function clearChat() { vscode.postMessage({ command: 'clear' }); }

            input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
            if (messages) messages.scrollTop = messages.scrollHeight;
        `;

        this.view.webview.html = wrapHtml({ nonce, title: 'Knowledge Explorer', body, scripts });
    }
}
