// ---------------------------------------------------------------------------
// Sdlicit -- Chat Panel Provider
// ---------------------------------------------------------------------------
// VS Code native-look chat panel with:
//   - Chat history sidebar (previous conversations)
//   - Mode selector (Chat / Explore KB / Agent) via minimal dropdown
//   - Slash commands (/explore, /agent)
//   - Bidirectional panel linking (SOW, ADR, Canvas)
//   - Persistent chat sessions via VS Code globalStorageUri
//   - ToM observation on every chat message
//   - Auto-injected Socratic probes when ToM detects issues
//   - Clean minimalistic styling, no emoji, grey icons only
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { DataService } from '../services/dataService';
import { ChatMode, ChatEntry, KBSource, Artifact } from '../types';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

const MODE_LABELS: Record<ChatMode, string> = {
    chat: 'Chat',
    explore: 'Explore KB',
    agent: 'Agent',
};

const MAX_PERSISTED_SESSIONS = 50;
const CHAT_STORAGE_DIR = 'chat-sessions';

interface ChatSession {
    id: string;
    title: string;
    mode: ChatMode;
    history: ChatEntry[];
    createdAt: number;
    updatedAt: number;
    tokenCount: number;
}

export interface ActivePanel {
    panelType: 'sow' | 'adr' | 'canvas';
    panelLabel: string;
    sections: Array<{ key: string; heading: string }>;
}

export class ChatPanelProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private sessions: ChatSession[] = [];
    private activeSessionId: string | undefined;
    private contextArtifacts: Map<string, Artifact> = new Map();
    private mode: ChatMode = 'chat';
    private isProcessing = false;
    private showHistory = false;
    private tokenLimit = 128000; // default, can be updated from backend

    private activePanels: Map<string, ActivePanel> = new Map();
    private linkedPanelId?: string;
    private linkedSectionKey?: string;
    private linkedSectionHeading?: string;

    /** Provider for read-only source preview documents. */
    private sourcePreviewProvider?: { update(uri: vscode.Uri, content: string): void };
    private sourcePreviewContents?: Map<string, string>;
    private previewCounter = 0;

    /** Root directory for persisted chat sessions (inside globalStorageUri). */
    private readonly storageDir: string;

    constructor(
        private readonly data: DataService,
        private readonly extensionContext: vscode.ExtensionContext,
    ) {
        this.storageDir = path.join(extensionContext.globalStorageUri.fsPath, CHAT_STORAGE_DIR);
        this.loadAllSessions();
        if (this.sessions.length === 0) {
            this.newSession();
        } else {
            this.activeSessionId = this.sessions[0].id;
            this.mode = this.sessions[0].mode;
        }
    }

    /** Update the per-session token limit (call from extension.ts if backend provides it). */
    setTokenLimit(limit: number): void {
        this.tokenLimit = limit;
        this.render();
    }

    /** Inject the read-only source preview provider (from extension.ts). */
    setSourcePreviewProvider(
        provider: { update(uri: vscode.Uri, content: string): void },
        contents: Map<string, string>,
    ): void {
        this.sourcePreviewProvider = provider;
        this.sourcePreviewContents = contents;
    }

    private get activeSession(): ChatSession | undefined {
        return this.sessions.find(s => s.id === this.activeSessionId);
    }

    private get history(): ChatEntry[] {
        return this.activeSession?.history ?? [];
    }

    // ── Session management ──────────────────────────────────────────────────

    private newSession(): string {
        const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
        const session: ChatSession = {
            id,
            title: 'New chat',
            mode: this.mode,
            history: [],
            createdAt: Date.now(),
            updatedAt: Date.now(),
            tokenCount: 0,
        };
        this.sessions.unshift(session);
        this.activeSessionId = id;
        this.persistSession(session);
        return id;
    }

    private switchSession(id: string): void {
        const session = this.sessions.find(s => s.id === id);
        if (session) {
            this.activeSessionId = id;
            this.mode = session.mode;
            this.showHistory = false;
        }
    }

    private deleteSession(id: string): void {
        this.sessions = this.sessions.filter(s => s.id !== id);
        this.deletePersistedSession(id);
        if (this.activeSessionId === id) {
            this.activeSessionId = this.sessions[0]?.id;
            if (!this.activeSessionId) { this.newSession(); }
        }
    }

    private deriveTitle(text: string): string {
        const clean = text.replace(/^\/\w+\s*/, '').trim();
        if (clean.length <= 40) { return clean || 'New chat'; }
        return clean.slice(0, 37) + '...';
    }

    // ── Bidirectional API ───────────────────────────────────────────────────

    registerActivePanel(panelId: string, panel: ActivePanel): void {
        this.activePanels.set(panelId, panel);
        this.render();
    }

    unregisterActivePanel(panelId: string): void {
        this.activePanels.delete(panelId);
        if (this.linkedPanelId === panelId) {
            this.linkedPanelId = undefined;
            this.linkedSectionKey = undefined;
            this.linkedSectionHeading = undefined;
        }
        this.render();
    }

    receiveContextFromPanel(
        panelId: string,
        panelType: 'sow' | 'adr' | 'canvas',
        sectionKey: string,
        sectionHeading: string,
        context: string,
    ): void {
        this.linkedPanelId = panelId;
        this.linkedSectionKey = sectionKey;
        this.linkedSectionHeading = sectionHeading;

        const session = this.activeSession;
        if (session) {
            session.history.push({
                role: 'system',
                content: '[Context from ' + panelType.toUpperCase() + ' \u2014 ' + sectionHeading + ']\n' + context,
                timestamp: Date.now(),
            });
            session.updatedAt = Date.now();
            this.persistSession(session);
        }
        this.render();
        if (this.view) { this.view.show?.(true); }
    }

    receiveContextFromSOW(sectionKey: string, sectionHeading: string, context: string): void {
        this.receiveContextFromPanel('sow', 'sow', sectionKey, sectionHeading, context);
    }

    async addArtifactToContext(artifactId: string): Promise<void> {
        const artifact = this.data.getArtifact(artifactId);
        if (artifact) {
            this.contextArtifacts.set(artifactId, artifact);
            this.render();
        }
    }

    // ── WebviewViewProvider ─────────────────────────────────────────────────

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
                case 'send': await this.handleSend(msg.text); break;
                case 'setMode': {
                    this.mode = msg.mode as ChatMode;
                    const s = this.activeSession;
                    if (s) { s.mode = this.mode; }
                    this.render();
                    break;
                }
                case 'addContext': await this.handleAddContext(); break;
                case 'removeContext': this.contextArtifacts.delete(msg.artifactId); this.render(); break;
                case 'exportChat': await this.exportChat(); break;
                case 'openArtifact': vscode.commands.executeCommand('sdlicit.openCanvas', msg.artifactId); break;
                case 'openSource': {
                    const assistantEntries = this.history.filter(e => e.role === 'assistant');
                    const entry = assistantEntries[msg.assistantIdx ?? 0];
                    const source = entry?.sources?.[msg.sourceIdx ?? 0];
                    await this.handleOpenSource(msg.ref, source?.snippet ?? '');
                    break;
                }
                case 'insertToPanel': {
                    vscode.commands.executeCommand('sdlicit.insertChatToPanel', {
                        panelId: msg.panelId,
                        sectionKey: msg.sectionKey,
                        content: msg.content,
                    });
                    break;
                }
                case 'newChat': {
                    this.newSession();
                    this.linkedPanelId = undefined;
                    this.linkedSectionKey = undefined;
                    this.linkedSectionHeading = undefined;
                    this.contextArtifacts.clear();
                    this.showHistory = false;
                    this.render();
                    break;
                }
                case 'toggleHistory': {
                    this.showHistory = !this.showHistory;
                    this.render();
                    break;
                }
                case 'switchSession': {
                    this.switchSession(msg.sessionId);
                    this.render();
                    break;
                }
                case 'deleteSession': {
                    this.deleteSession(msg.sessionId);
                    this.render();
                    break;
                }
            }
        });
    }

    // ── Send ────────────────────────────────────────────────────────────────

    private async handleAddContext(): Promise<void> {
        const artifacts = this.data.getArtifacts();
        const items = artifacts.map(a => ({
            label: a.id + ': ' + a.title,
            description: a.type + ' \u00b7 ' + a.status,
            artifactId: a.id,
        }));
        const picked = await vscode.window.showQuickPick(items, {
            title: 'Add Artifact to Chat Context',
            placeHolder: 'Select artifacts to include',
            canPickMany: true,
        });
        if (picked) {
            for (const p of picked) {
                const artifact = artifacts.find(a => a.id === p.artifactId);
                if (artifact) { this.contextArtifacts.set(artifact.id, artifact); }
            }
            this.render();
        }
    }

    private async handleSend(rawText: string): Promise<void> {
        if (!rawText.trim() || this.isProcessing) { return; }
        const session = this.activeSession;
        if (!session) { return; }

        // Block input if token limit reached
        if (session.tokenCount >= this.tokenLimit) {
            vscode.window.showWarningMessage('Token limit reached for this chat session. Start a new chat.');
            return;
        }

        let text = rawText.trim();
        if (text.startsWith('/explore ')) {
            this.mode = 'explore'; session.mode = 'explore';
            text = text.slice(9).trim();
        } else if (text.startsWith('/agent ')) {
            this.mode = 'agent'; session.mode = 'agent';
            text = text.slice(7).trim();
        } else if (text === '/explore' || text === '/agent') {
            this.mode = text.slice(1) as ChatMode;
            session.mode = this.mode;
            this.render();
            return;
        }
        if (!text) { return; }

        // Auto-title from first user message
        if (session.history.filter(e => e.role === 'user').length === 0) {
            session.title = this.deriveTitle(rawText);
        }

        this.isProcessing = true;
        session.history.push({ role: 'user', content: text, timestamp: Date.now(), mode: this.mode });
        session.updatedAt = Date.now();
        this.render();

        try {
            const response = await this.data.chat(text, this.mode, session.history);
            const entry: ChatEntry = {
                role: 'assistant',
                content: response.content,
                sources: response.sources,
                timestamp: Date.now(),
                mode: this.mode,
                tokensUsed: response.tokensUsed,
                agentsUsed: response.agentsInvolved,
                tokensByAgent: response.tokensByAgent,
            };
            session.history.push(entry);
            session.tokenCount += response.tokensUsed ?? 0;
            session.updatedAt = Date.now();

            // ToM: observe the exchange and check for auto-Socratic (fire-and-forget)
            this.observeAndSuggest(text, response.content, session);
        } catch (err: any) {
            session.history.push({
                role: 'assistant',
                content: 'Error: ' + (err?.message ?? 'Unknown error'),
                timestamp: Date.now(),
            });
        }
        this.isProcessing = false;
        this.persistSession(session);
        this.render();
    }

    private async exportChat(): Promise<void> {
        const content = this.history
            .filter(e => e.role !== 'system')
            .map(e => '**' + (e.role === 'user' ? 'You' : 'Sdlicit') + ':** ' + e.content)
            .join('\n\n---\n\n');
        const doc = await vscode.workspace.openTextDocument({ content, language: 'markdown' });
        await vscode.window.showTextDocument(doc);
    }

    // ── Source navigation ───────────────────────────────────────────────────

    private async handleOpenSource(ref: string, snippet: string): Promise<void> {
        try {
            const location = await this.data.locateChunk(ref, snippet);

            if (!location.found || !location.filePath) {
                vscode.window.showWarningMessage('Could not locate source file: ' + ref);
                return;
            }

            const fileUri = vscode.Uri.file(location.filePath);

            if (location.fileType === 'pdf') {
                // Open PDF via vscode-pdf custom editor (supports page= query)
                const pdfUri = vscode.Uri.file(location.filePath);
                const pdfWithPage = location.page
                    ? pdfUri.with({ query: `page=${location.page}` })
                    : pdfUri;
                try {
                    await vscode.commands.executeCommand('vscode.openWith', pdfWithPage, 'pdf.preview');
                } catch {
                    // Fallback: prompt to install vscode-pdf
                    const action = await vscode.window.showWarningMessage(
                        'Sdlicit: PDF preview requires the "vscode-pdf" extension.',
                        'Install Extension', 'Open Raw',
                    );
                    if (action === 'Install Extension') {
                        vscode.commands.executeCommand('workbench.extensions.installExtension', 'tomoki1207.pdf');
                    } else if (action === 'Open Raw') {
                        await vscode.commands.executeCommand('vscode.open', pdfWithPage);
                    }
                }

                // Show chunk text in a side panel for explainability
                if (snippet) {
                    this.showChunkPreview(ref, snippet, location.page, location.matchScore);
                }
            } else {
                // For markdown/text files, open in editor
                const doc = await vscode.workspace.openTextDocument(fileUri);
                const editor = await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.Beside });

                // Try to navigate to the anchor section
                if (location.anchor) {
                    const text = doc.getText();
                    const anchorPos = this.findAnchorPosition(text, location.anchor);
                    if (anchorPos >= 0) {
                        const pos = doc.positionAt(anchorPos);
                        const range = new vscode.Range(pos, pos);
                        editor.selection = new vscode.Selection(pos, pos);
                        editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
                    }
                }

                // Show chunk preview for text files too
                if (snippet) {
                    this.showChunkPreview(ref, snippet, undefined, location.matchScore);
                }
            }
        } catch (err: any) {
            vscode.window.showErrorMessage('Error opening source: ' + (err?.message ?? 'Unknown'));
        }
    }

    private findAnchorPosition(text: string, anchor: string): number {
        // Anchor format: "N-slug" e.g. "2-considered-options"
        // Try to find a heading that matches the slug part
        const parts = anchor.match(/^\d+-(.+)$/);
        if (!parts) { return -1; }
        const slug = parts[1];

        // Convert slug back to approximate heading words
        const words = slug.split('-').filter(w => w.length > 0);
        if (words.length === 0) { return -1; }

        // Search for a markdown heading containing those words
        const lines = text.split('\n');
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            if (line.match(/^#{1,6}\s/)) {
                const headingLower = line.toLowerCase();
                const matchCount = words.filter(w => headingLower.includes(w)).length;
                if (matchCount >= Math.ceil(words.length * 0.7)) {
                    // Found a matching heading
                    let offset = 0;
                    for (let j = 0; j < i; j++) { offset += lines[j].length + 1; }
                    return offset;
                }
            }
        }
        return -1;
    }

    private showChunkPreview(ref: string, snippet: string, page?: number, matchScore?: number): void {
        // Format the snippet as a nicely readable markdown preview
        const pageInfo = page ? `**Page ${page}**` : '';
        const scoreInfo = matchScore !== undefined ? ` (match confidence: ${Math.round(matchScore * 100)}%)` : '';
        const header = `## Source: ${ref}\n\n${pageInfo}${scoreInfo}\n\n---\n\n`;

        // Clean up the snippet for display — remove structured prefixes
        const cleanSnippet = snippet.replace(/^\[.*?\]\n/, '');
        const content = header + cleanSnippet;

        // Use the registered read-only content provider (sdlicit-preview: scheme)
        const fileName = ref.split('/').pop()?.split('#')[0] ?? 'source';
        this.previewCounter++;
        const uri = vscode.Uri.parse(`sdlicit-preview:${fileName}-${this.previewCounter}.md`);

        if (this.sourcePreviewProvider) {
            this.sourcePreviewProvider.update(uri, content);
            vscode.workspace.openTextDocument(uri).then(doc => {
                vscode.languages.setTextDocumentLanguage(doc, 'markdown');
                vscode.window.showTextDocument(doc, {
                    viewColumn: vscode.ViewColumn.Beside,
                    preview: true,
                    preserveFocus: true,
                });
            });
        } else {
            // Fallback: use { content } — may prompt to save
            vscode.workspace.openTextDocument({ content, language: 'markdown' }).then(doc => {
                vscode.window.showTextDocument(doc, {
                    viewColumn: vscode.ViewColumn.Beside,
                    preview: true,
                    preserveFocus: true,
                });
            });
        }
    }

    // ── ToM observation & auto-Socratic ─────────────────────────────────────

    /** Counter for exchanges since last suggestion check. */
    private exchangesSinceSuggestion = 0;
    private static readonly SUGGEST_INTERVAL = 3;

    /**
     * Fire-and-forget: observe the chat exchange via ToM and periodically
     * check whether a Socratic probe should be injected.
     */
    private async observeAndSuggest(
        userMessage: string,
        assistantResponse: string,
        session: ChatSession,
    ): Promise<void> {
        const chatHistory = session.history
            .filter(e => e.role !== 'system')
            .slice(-10)
            .map(e => ({ role: e.role, content: e.content }));

        // Observe (best-effort, non-blocking)
        try {
            await this.data.observeChat(userMessage, assistantResponse, chatHistory);
        } catch { /* best effort — don't disrupt chat */ }

        // Periodic suggestion check
        this.exchangesSinceSuggestion++;
        if (this.exchangesSinceSuggestion >= ChatPanelProvider.SUGGEST_INTERVAL) {
            this.exchangesSinceSuggestion = 0;
            try {
                const suggestion = await this.data.giveSuggestions(chatHistory);
                if (suggestion.should_probe && suggestion.probe_question) {
                    // Auto-inject the Socratic probe as a system→assistant message
                    session.history.push({
                        role: 'assistant',
                        content: suggestion.probe_question,
                        timestamp: Date.now(),
                        mode: this.mode,
                        toolsUsed: ['tom_give_suggestions'],
                        agentsUsed: ['ToM', 'Socratic'],
                    });
                    session.updatedAt = Date.now();
                    this.persistSession(session);
                    this.render();
                }
            } catch { /* best effort */ }
        }
    }

    // ── Persistence (globalStorageUri) ──────────────────────────────────────

    private ensureStorageDir(): void {
        if (!fs.existsSync(this.storageDir)) {
            fs.mkdirSync(this.storageDir, { recursive: true });
        }
    }

    private sessionFilePath(id: string): string {
        return path.join(this.storageDir, `${id}.json`);
    }

    private persistSession(session: ChatSession): void {
        try {
            this.ensureStorageDir();
            fs.writeFileSync(
                this.sessionFilePath(session.id),
                JSON.stringify(session, null, 2),
                'utf-8',
            );
        } catch { /* best effort */ }
    }

    private deletePersistedSession(id: string): void {
        try {
            const fp = this.sessionFilePath(id);
            if (fs.existsSync(fp)) { fs.unlinkSync(fp); }
        } catch { /* best effort */ }
    }

    private loadAllSessions(): void {
        try {
            this.ensureStorageDir();
            const files = fs.readdirSync(this.storageDir)
                .filter(f => f.endsWith('.json'))
                .sort()
                .reverse(); // newest first (filenames are ID-based with timestamp prefix)

            const loaded: ChatSession[] = [];
            for (const file of files.slice(0, MAX_PERSISTED_SESSIONS)) {
                try {
                    const raw = JSON.parse(fs.readFileSync(path.join(this.storageDir, file), 'utf-8'));
                    if (raw.id && raw.history) { loaded.push(raw as ChatSession); }
                } catch { /* skip corrupt files */ }
            }
            // Sort by updatedAt descending
            loaded.sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
            this.sessions = loaded;

            // Prune old sessions on disk beyond limit
            if (files.length > MAX_PERSISTED_SESSIONS) {
                for (const old of files.slice(MAX_PERSISTED_SESSIONS)) {
                    try { fs.unlinkSync(path.join(this.storageDir, old)); } catch { /* ignore */ }
                }
            }
        } catch {
            this.sessions = [];
        }
    }

    // ── Render ──────────────────────────────────────────────────────────────

    private render(): void {
        if (!this.view) { return; }
        const nonce = getNonce();

        if (this.showHistory) {
            this.view.webview.html = wrapHtml(this.renderHistoryView(), nonce, this.buildHistoryScripts());
            return;
        }

        const session = this.activeSession;
        const history = session?.history ?? [];
        const visibleHistory = history.filter(e => e.role !== 'system');
        const assistantEntries = history.filter(e => e.role === 'assistant');

        // Header
        const headerHtml =
            '<div class="sc-header">' +
                '<button class="sc-hbtn" data-action="toggleHistory" title="Chat history">' +
                    '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M1.5 2h13l.5.5v10l-.5.5h-13l-.5-.5v-10l.5-.5zm0 1v9h13V3h-13zm1 1h3v1h-3V4zm0 2h7v1h-7V6zm0 2h5v1h-5V8z"/></svg>' +
                '</button>' +
                '<select id="modeSelect" class="sc-mode-sel">' +
                    (['chat', 'explore', 'agent'] as ChatMode[]).map(m =>
                        '<option value="' + m + '"' + (m === this.mode ? ' selected' : '') + '>' + MODE_LABELS[m] + '</option>'
                    ).join('') +
                '</select>' +
                '<span class="sc-spacer"></span>' +
                '<button class="sc-hbtn" data-action="exportChat" title="Export">' +
                    '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l4 4h-3v5H7V5H4l4-4zm-6 9v4h12v-4h1v4.5l-.5.5h-13l-.5-.5V10h1z"/></svg>' +
                '</button>' +
                '<button class="sc-hbtn" data-action="newChat" title="New chat">' +
                    '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1v6H2v1h6v6h1V8h6V7H9V1H8z"/></svg>' +
                '</button>' +
            '</div>';

        // Context bar
        const chips = Array.from(this.contextArtifacts.values());
        let contextHtml = '';
        if (chips.length > 0 || this.linkedPanelId) {
            contextHtml = '<div class="sc-ctx">';
            if (this.linkedPanelId) {
                contextHtml += '<span class="sc-link-chip">Linked: ' + escapeHtml(this.linkedSectionHeading ?? 'Section') + '</span>';
            }
            for (const a of chips) {
                contextHtml += '<span class="sc-chip">' + escapeHtml(a.id) +
                    '<span class="sc-chip-x" data-action="removeContext" data-artifact-id="' + a.id + '">\u00d7</span></span>';
            }
            contextHtml += '</div>';
        }

        // Messages
        let msgsHtml = '';
        if (visibleHistory.length === 0) {
            msgsHtml = this.renderWelcome();
        } else {
            for (const entry of visibleHistory) {
                if (entry.role === 'user') {
                    msgsHtml += this.renderUserMsg(entry);
                } else {
                    const aIdx = assistantEntries.indexOf(entry);
                    msgsHtml += this.renderAssistantMsg(entry, aIdx);
                }
            }
        }

        // Loading
        if (this.isProcessing) {
            msgsHtml +=
                '<div class="sc-msg">' +
                    '<div class="sc-msg-hd"><span class="sc-av sc-av-s">S</span><span class="sc-name">Sdlicit</span></div>' +
                    '<div class="sc-msg-bd"><span class="sc-dots"><span></span><span></span><span></span></span></div>' +
                '</div>';
        }

        // Token bar
        const tokenCount = session?.tokenCount ?? 0;
        const tokenPct = Math.min(100, Math.round((tokenCount / this.tokenLimit) * 100));
        const atLimit = tokenCount >= this.tokenLimit;
        const tokenBarHtml =
            '<div class="sc-token-bar">' +
                '<div class="sc-token-fill" style="width:' + tokenPct + '%"></div>' +
                '<span class="sc-token-label">' + this.formatTokens(tokenCount) + ' / ' + this.formatTokens(this.tokenLimit) + '</span>' +
            '</div>';

        // Recent chats strip (last 3 other sessions)
        const recentSessions = this.sessions.filter(s => s.id !== this.activeSessionId).slice(0, 3);
        let recentHtml = '';
        if (recentSessions.length > 0) {
            recentHtml = '<div class="sc-recent">' +
                recentSessions.map(s =>
                    '<div class="sc-recent-item" data-action="switchSession" data-session-id="' + s.id + '" title="' + escapeHtml(s.title) + '">' +
                        '<span class="sc-recent-title">' + escapeHtml(s.title) + '</span>' +
                        '<span class="sc-recent-meta">' + this.relativeTime(new Date(s.updatedAt)) + '</span>' +
                    '</div>'
                ).join('') +
            '</div>';
        }

        // Input
        const placeholder = atLimit ? 'Token limit reached — start a new chat' : 'Ask Sdlicit...';
        const inputDisabled = this.isProcessing || atLimit;
        const inputHtml =
            '<div class="sc-input">' +
                tokenBarHtml +
                '<div class="sc-input-row">' +
                    '<textarea id="chatInput" rows="1" placeholder="' + placeholder + '"' + (atLimit ? ' disabled' : '') + '></textarea>' +
                    '<button class="sc-send" data-action="send"' + (inputDisabled ? ' disabled' : '') + ' title="Send">' +
                        '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M1 8.5l7-7 7 7H10v6H6v-6H1z"/></svg>' +
                    '</button>' +
                '</div>' +
                '<div class="sc-input-ft">' +
                    '<button class="sc-hbtn" data-action="addContext" title="Attach context">' +
                        '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M10.5 1a3.5 3.5 0 0 1 2.5 6l-6 6a2.5 2.5 0 0 1-3.5-3.5l6-6a1.5 1.5 0 1 1 2 2l-5 5-.7-.7 5-5a.5.5 0 1 0-.7-.7l-6 6a1.5 1.5 0 0 0 2.2 2.2l6-6A2.5 2.5 0 0 0 10.5 1z"/></svg>' +
                    '</button>' +
                    '<span class="sc-ft-label">/ for commands</span>' +
                '</div>' +
                recentHtml +
            '</div>';

        const body =
            '<div class="sc-root">' +
                headerHtml +
                contextHtml +
                '<div class="sc-msgs" id="chatMessages">' + msgsHtml + '</div>' +
                inputHtml +
            '</div>';

        this.view.webview.html = wrapHtml(body, nonce, this.buildScripts());
    }

    private renderHistoryView(): string {
        let items = '';
        for (const s of this.sessions) {
            const isActive = s.id === this.activeSessionId;
            const date = new Date(s.updatedAt);
            const msgCount = s.history.filter(e => e.role !== 'system').length;
            items +=
                '<div class="sc-hist-item' + (isActive ? ' sc-hist-active' : '') + '" data-action="switchSession" data-session-id="' + s.id + '">' +
                    '<div class="sc-hist-title">' + escapeHtml(s.title) + '</div>' +
                    '<div class="sc-hist-meta">' +
                        '<span>' + MODE_LABELS[s.mode] + '</span>' +
                        '<span>\u00b7</span>' +
                        '<span>' + msgCount + ' msgs</span>' +
                        '<span>\u00b7</span>' +
                        '<span>' + this.relativeTime(date) + '</span>' +
                        '<span class="sc-spacer"></span>' +
                        (this.sessions.length > 1
                            ? '<button class="sc-hist-del" data-action="deleteSession" data-session-id="' + s.id + '" title="Delete">\u00d7</button>'
                            : '') +
                    '</div>' +
                '</div>';
        }
        return '<div class="sc-root">' +
            '<div class="sc-header">' +
                '<button class="sc-hbtn" data-action="toggleHistory" title="Back to chat">' +
                    '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M5.7 3L2 8l3.7 5 .8-.6L3.5 8l3-4.4-.8-.6z"/></svg>' +
                '</button>' +
                '<span class="sc-header-title">Chat History</span>' +
                '<span class="sc-spacer"></span>' +
                '<button class="sc-hbtn" data-action="newChat" title="New chat">' +
                    '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1v6H2v1h6v6h1V8h6V7H9V1H8z"/></svg>' +
                '</button>' +
            '</div>' +
            '<div class="sc-hist-list">' + items + '</div>' +
        '</div>';
    }

    private renderWelcome(): string {
        return '<div class="sc-welcome">' +
            '<div class="sc-welcome-title">Sdlicit</div>' +
            '<div class="sc-welcome-sub">Software elicitation assistant</div>' +
            '<div class="sc-welcome-hints">' +
                '<div class="sc-whint" data-prefill="/explore What does IEEE 830 say about requirement quality?">' +
                    '<span class="sc-whint-slash">/explore</span> Search knowledge base</div>' +
                '<div class="sc-whint" data-prefill="Review my SOW for completeness">' +
                    'Review an artifact</div>' +
                '<div class="sc-whint" data-prefill="/agent Analyze traceability gaps">' +
                    '<span class="sc-whint-slash">/agent</span> Analyze with agent</div>' +
            '</div>' +
        '</div>';
    }

    private renderUserMsg(entry: ChatEntry): string {
        const badge = entry.mode ? '<span class="sc-mode-tag">' + MODE_LABELS[entry.mode] + '</span>' : '';
        return '<div class="sc-msg sc-msg-u">' +
            '<div class="sc-msg-hd">' +
                '<span class="sc-av sc-av-u">U</span>' +
                '<span class="sc-name">You</span>' +
                badge +
                '<span class="sc-time">' + new Date(entry.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + '</span>' +
            '</div>' +
            '<div class="sc-msg-bd">' + this.formatMarkdown(entry.content) + '</div>' +
        '</div>';
    }

    private renderAssistantMsg(entry: ChatEntry, aIdx: number): string {
        const badge = entry.mode ? '<span class="sc-mode-tag">' + MODE_LABELS[entry.mode] + '</span>' : '';

        // Trace log (tools + agents used, like VS Code thinking)
        let traceHtml = '';
        const traceItems: string[] = [];
        if (entry.agentsUsed && entry.agentsUsed.length > 0) {
            if (entry.tokensByAgent && Object.keys(entry.tokensByAgent).length > 0) {
                // Detailed per-agent breakdown with tokens
                for (const a of entry.agentsUsed) {
                    const agentTokens = entry.tokensByAgent[a];
                    if (agentTokens) {
                        traceItems.push(
                            '<span class="sc-trace-agent" title="prompt: ' + agentTokens.prompt.toLocaleString() + ', completion: ' + agentTokens.completion.toLocaleString() + ', calls: ' + agentTokens.calls + '">' +
                            escapeHtml(a) + ' <span class="sc-trace-agent-tokens">' + agentTokens.total.toLocaleString() + ' tok</span></span>'
                        );
                    } else {
                        traceItems.push('<span class="sc-trace-agent">' + escapeHtml(a) + '</span>');
                    }
                }
            } else {
                for (const a of entry.agentsUsed) {
                    traceItems.push('<span class="sc-trace-agent">' + escapeHtml(a) + '</span>');
                }
            }
        }
        if (entry.toolsUsed && entry.toolsUsed.length > 0) {
            for (const t of entry.toolsUsed) {
                traceItems.push('<span class="sc-trace-tool">' + escapeHtml(t) + '</span>');
            }
        }
        if (entry.tokensUsed && entry.tokensUsed > 0) {
            traceItems.push('<span class="sc-trace-tokens">' + entry.tokensUsed.toLocaleString() + ' tokens</span>');
        }
        if (traceItems.length > 0) {
            traceHtml = '<details class="sc-trace"><summary class="sc-trace-sum">' +
                '<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 1a6 6 0 1 1 0 12A6 6 0 0 1 8 2zm-.5 3v4h1V5h-1zm0 5v1h1v-1h-1z"/></svg> ' +
                'Used ' + (entry.agentsUsed?.length ?? 0) + ' agent(s)' +
                (entry.tokensUsed ? ' · ' + entry.tokensUsed.toLocaleString() + ' tokens' : '') +
                '</summary><div class="sc-trace-body">' + traceItems.join('') + '</div></details>';
        }

        let sourcesHtml = '';
        if (entry.sources && entry.sources.length > 0) {
            sourcesHtml = '<div class="sc-sources">' +
                '<div class="sc-sources-label">Sources</div>' +
                entry.sources.map((s, i) =>
                    '<span class="sc-src" data-action="openSource" data-ref="' + escapeHtml(s.ref) + '" data-snippet-idx="' + i + '" data-aidx="' + aIdx + '" title="Click to open source — ' + escapeHtml((s.snippet ?? '').slice(0, 120)) + '...">' +
                    '<svg class="sc-src-icon" width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M13.85 4.44l-3.28-3.3-.71-.14H3.5l-.5.5v13l.5.5h9l.5-.5V4.8l-.15-.36zM10 1.94L12.06 4H10V1.94zM13 14H3V2h6v3h4v9z"/></svg>' +
                    escapeHtml(s.ref) +
                    (s.relevance !== undefined ? '<span class="sc-src-rel">' + Math.round(s.relevance * 100) + '%</span>' : '') +
                    '</span>'
                ).join('') +
            '</div>';
        }

        // Insert actions
        let insertHtml = '';
        if (this.linkedPanelId && this.linkedSectionKey) {
            const panel = this.activePanels.get(this.linkedPanelId);
            const panelLabel = panel?.panelLabel ?? this.linkedPanelId;
            const secLabel = this.linkedSectionHeading ?? this.linkedSectionKey;
            insertHtml += '<button class="sc-insert-btn" data-action="insertToPanel"' +
                ' data-panel-id="' + this.linkedPanelId + '" data-section-key="' + this.linkedSectionKey + '" data-aidx="' + aIdx + '">' +
                'Insert to ' + escapeHtml(panelLabel) + ' \u203a ' + escapeHtml(secLabel) + '</button>';
        }
        if (this.activePanels.size > 0) {
            for (const [pid, p] of this.activePanels.entries()) {
                if (pid === this.linkedPanelId) { continue; }
                const opts = p.sections.map(s =>
                    '<div class="sc-ins-opt" data-action="insertToPanel" data-panel-id="' + pid + '" data-section-key="' + s.key + '" data-aidx="' + aIdx + '">' +
                        escapeHtml(p.panelLabel) + ' \u203a ' + escapeHtml(s.heading) +
                    '</div>'
                ).join('');
                insertHtml += '<details class="sc-ins-grp"><summary class="sc-ins-sum">Insert to ' + escapeHtml(p.panelLabel) + '</summary>' +
                    '<div class="sc-ins-opts">' + opts + '</div></details>';
            }
        }
        const actionsHtml = insertHtml ? '<div class="sc-actions">' + insertHtml + '</div>' : '';

        return '<div class="sc-msg sc-msg-a">' +
            '<div class="sc-msg-hd">' +
                '<span class="sc-av sc-av-s">S</span>' +
                '<span class="sc-name">Sdlicit</span>' +
                badge +
                '<span class="sc-time">' + new Date(entry.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + '</span>' +
            '</div>' +
            traceHtml +
            '<div class="sc-msg-bd">' + this.formatMarkdown(entry.content) + '</div>' +
            sourcesHtml +
            actionsHtml +
        '</div>';
    }

    private buildScripts(): string {
        return [
            'var chatInput = document.getElementById("chatInput");',
            'var messagesDiv = document.getElementById("chatMessages");',
            'var modeSelect = document.getElementById("modeSelect");',
            'if (messagesDiv) messagesDiv.scrollTop = messagesDiv.scrollHeight;',
            // Auto-resize
            'if (chatInput) {',
            '  chatInput.addEventListener("input", function(){ this.style.height="auto"; this.style.height=Math.min(this.scrollHeight,140)+"px"; });',
            '  chatInput.addEventListener("keydown", function(e){',
            '    if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); if(chatInput.value.trim()){ vscode.postMessage({command:"send",text:chatInput.value.trim()}); chatInput.value=""; chatInput.style.height="auto"; } }',
            '  });',
            // Slash popup
            '  chatInput.addEventListener("input", function(){',
            '    var popup=document.getElementById("slashPopup");',
            '    if(chatInput.value==="/"){',
            '      if(!popup){ popup=document.createElement("div"); popup.id="slashPopup"; popup.className="sc-slash-pop";',
            '        popup.innerHTML=\'<div class="sc-slash-it" data-cmd="/explore "><span class="sc-whint-slash">/explore</span> Search knowledge base</div><div class="sc-slash-it" data-cmd="/agent "><span class="sc-whint-slash">/agent</span> Full agent with tools</div>\';',
            '        chatInput.parentElement.style.position="relative"; chatInput.parentElement.appendChild(popup);',
            '        popup.addEventListener("click",function(ev){ var it=ev.target.closest(".sc-slash-it"); if(it){chatInput.value=it.dataset.cmd;chatInput.focus();popup.remove();} });',
            '      }',
            '    } else if(popup){ popup.remove(); }',
            '  });',
            '  chatInput.focus();',
            '}',
            'if(modeSelect){ modeSelect.addEventListener("change",function(){ vscode.postMessage({command:"setMode",mode:modeSelect.value}); }); }',
            // Prefill hints
            'document.querySelectorAll("[data-prefill]").forEach(function(el){',
            '  el.addEventListener("click",function(){ if(chatInput){chatInput.value=el.dataset.prefill;chatInput.focus();chatInput.style.height="auto";chatInput.style.height=Math.min(chatInput.scrollHeight,140)+"px";} });',
            '});',
            // Click delegation
            'document.addEventListener("click",function(e){',
            '  var el;',
            '  if((el=e.target.closest(\'[data-action="send"]\'))&&chatInput&&chatInput.value.trim()){vscode.postMessage({command:"send",text:chatInput.value.trim()});chatInput.value="";chatInput.style.height="auto";return;}',
            '  if((el=e.target.closest(\'[data-action="newChat"]\'))){vscode.postMessage({command:"newChat"});return;}',
            '  if((el=e.target.closest(\'[data-action="toggleHistory"]\'))){vscode.postMessage({command:"toggleHistory"});return;}',
            '  if((el=e.target.closest(\'[data-action="addContext"]\'))){vscode.postMessage({command:"addContext"});return;}',
            '  if((el=e.target.closest(\'[data-action="removeContext"]\'))){vscode.postMessage({command:"removeContext",artifactId:el.dataset.artifactId});return;}',
            '  if((el=e.target.closest(\'[data-action="exportChat"]\'))){vscode.postMessage({command:"exportChat"});return;}',
            '  if((el=e.target.closest(\'[data-action="switchSession"]\'))){vscode.postMessage({command:"switchSession",sessionId:el.dataset.sessionId});return;}',
            '  if((el=e.target.closest(\'[data-action="openSource"]\'))){',
            '    var aidx=parseInt(el.dataset.aidx||"0",10);var sidx=parseInt(el.dataset.snippetIdx||"0",10);',
            '    vscode.postMessage({command:"openSource",ref:el.dataset.ref,assistantIdx:aidx,sourceIdx:sidx});return;',
            '  }',
            '  if((el=e.target.closest("[data-artifact-id]"))){vscode.postMessage({command:"openArtifact",artifactId:el.dataset.artifactId});return;}',
            '  if((el=e.target.closest(\'[data-action="insertToPanel"]\'))){',
            '    var idx=parseInt(el.dataset.aidx,10); var msgs=document.querySelectorAll(".sc-msg-a");',
            '    var bd=msgs[idx]?msgs[idx].querySelector(".sc-msg-bd"):null;',
            '    if(bd){vscode.postMessage({command:"insertToPanel",panelId:el.dataset.panelId,sectionKey:el.dataset.sectionKey,content:bd.textContent||""});}',
            '    return;',
            '  }',
            '});',
        ].join('\n');
    }

    private buildHistoryScripts(): string {
        return [
            'document.addEventListener("click",function(e){',
            '  var el;',
            '  if((el=e.target.closest(\'[data-action="toggleHistory"]\'))){vscode.postMessage({command:"toggleHistory"});return;}',
            '  if((el=e.target.closest(\'[data-action="newChat"]\'))){vscode.postMessage({command:"newChat"});return;}',
            '  if((el=e.target.closest(\'[data-action="deleteSession"]\'))){e.stopPropagation();vscode.postMessage({command:"deleteSession",sessionId:el.dataset.sessionId});return;}',
            '  if((el=e.target.closest(\'[data-action="switchSession"]\'))){vscode.postMessage({command:"switchSession",sessionId:el.dataset.sessionId});return;}',
            '});',
        ].join('\n');
    }

    private formatTokens(n: number): string {
        if (n >= 1000000) { return (n / 1000000).toFixed(1) + 'M'; }
        if (n >= 1000) { return (n / 1000).toFixed(1) + 'k'; }
        return n.toString();
    }

    private relativeTime(date: Date): string {
        const diff = Date.now() - date.getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) { return 'now'; }
        if (mins < 60) { return mins + 'm'; }
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) { return hrs + 'h'; }
        const days = Math.floor(hrs / 24);
        return days + 'd';
    }

    private formatMarkdown(text: string): string {
        let html = escapeHtml(text);
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="sc-pre"><code>$2</code></pre>');
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
        html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
        html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^- (.+)/gm, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
        html = html.replace(/\n\n/g, '</p><p>');
        html = html.replace(/\n/g, '<br>');
        return '<p>' + html + '</p>';
    }
}
