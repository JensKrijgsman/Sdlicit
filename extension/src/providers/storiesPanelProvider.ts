// ---------------------------------------------------------------------------
// Sdlicit — Stories Panel Provider (User Story Cards with Socratic Probes)
// ---------------------------------------------------------------------------
// WebviewPanel that renders generated user stories as editable cards.
// Per-story card: editable story_id, persona_id, statement, requirement links.
// Socratic probes with Respond/Chat, KB facts accordion.
// WIP persistence via VS Code globalStorageUri.
// Mirrors SOW/SRS panel UX for consistency.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { SdlicitClient, Clarification, SocraticProbe, GenerationResponse } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { KBSyncService } from '../services/kbSyncService';
import { WipManager } from '../services/wipManager';
import { getNonce, wrapHtml } from '../webview/webviewHelper';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StoryCard {
    story_id: string;
    persona_id: string;
    requirement_ids: string[];
    statement: string;
    status: 'pending' | 'generating' | 'complete' | 'accepted' | 'editing';
}

export interface StoriesSocraticState {
    probe?: SocraticProbe;
    probeAnswered: boolean;
    probeCollapsed: boolean;
    needsSocratic: boolean;
    socraticReason: string;
}

export class StoriesPanelProvider {
    private panel: vscode.WebviewPanel | undefined;
    private stories: StoryCard[] = [];
    private socratic: StoriesSocraticState = { probeAnswered: false, probeCollapsed: false, needsSocratic: false, socraticReason: '' };
    private isGenerating = false;
    private readOnly = false;
    private clarifications: Clarification[] = [];
    private srsContent = '';
    private personas: string[] = [];
    private artifactPath: string | undefined;
    private fileWatcher: vscode.Disposable | undefined;
    private resolvePromise?: (value: 'accepted' | 'declined') => void;
    private projectDir: string;

    constructor(
        private readonly client: SdlicitClient,
        private readonly store: ArtifactStore,
        private readonly kbSync?: KBSyncService,
        private readonly globalStoragePath?: string,
    ) {
        const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';
        this.projectDir = ws;
    }

    /** Start Stories generation. */
    async startGeneration(srsContent: string, personas: string[]): Promise<'accepted' | 'declined'> {
        this.srsContent = srsContent;
        this.personas = personas;

        // Prompt user if WIP data exists
        let wip: { stories: StoryCard[]; clarifications: Clarification[]; socratic: StoriesSocraticState } | null = null;
        if (this.globalStoragePath) {
            const wipMgr = new WipManager(this.globalStoragePath);
            const decision = await wipMgr.promptIfWipExists('stories');
            if (decision === 'resume') {
                wip = this.loadWip();
            } else {
                this.deleteWip();
            }
        } else {
            wip = this.loadWip();
        }
        if (wip) {
            this.stories = wip.stories;
            this.clarifications = wip.clarifications;
            this.socratic = wip.socratic;
        } else {
            this.stories = [];
        }
        this.isGenerating = !wip;

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.storiesPanel',
            'Sdlicit — User Stories',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        this.panel.onDidDispose(() => {
            this.fileWatcher?.dispose();
            if (this.resolvePromise) {
                this.resolvePromise('declined');
                this.resolvePromise = undefined;
            }
        });

        this.renderHtml();
        this.setupMessageHandler();

        if (!wip) {
            this.updatePanel();
            try {
                await this.generate();
            } catch (err: any) {
                this.isGenerating = false;
                this.updatePanel();
                vscode.window.showErrorMessage(`Sdlicit: Stories generation failed — ${err.message}`);
                return 'declined';
            }
            this.isGenerating = false;
            this.updatePanel();
            this.saveWip();
        } else {
            this.updatePanel();
        }

        return new Promise<'accepted' | 'declined'>((resolve) => {
            this.resolvePromise = resolve;
        });
    }

    /** Whether the panel is still open. */
    get isAlive(): boolean { return this.panel !== undefined; }

    /** Reveal (focus) the existing panel without creating a new one. */
    reveal(): void { this.panel?.reveal(vscode.ViewColumn.One); }

    /** Load existing stories markdown into the panel (read-only). */
    async openExisting(markdown: string, filePath?: string): Promise<void> {
        this.readOnly = true;
        this.isGenerating = false;
        this.artifactPath = filePath;
        this.parseMarkdownIntoState(markdown);

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.storiesPanel',
            'Sdlicit — User Stories',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );
        this.panel.onDidDispose(() => { this.panel = undefined; this.fileWatcher?.dispose(); });

        if (this.artifactPath) { this.setupFileWatcher(this.artifactPath); }

        this.renderHtml();
        this.setupMessageHandler();
        this.updatePanel();
    }

    /** Call the backend stories endpoint. */
    private async generate(): Promise<void> {
        this.updatePanel();

        const result: GenerationResponse = await this.client.generateStories(
            this.personas, this.srsContent, this.projectDir, this.clarifications,
        );

        // Handle socratic probe
        if (result.socratic_probe) {
            this.socratic.probe = result.socratic_probe;
            this.socratic.probeAnswered = false;
            this.socratic.probeCollapsed = false;
            this.socratic.needsSocratic = true;
            this.socratic.socraticReason = result.socratic_probe.question;
        }

        // Populate stories from structured response
        const stories = (result as any).stories as any[] | undefined;
        if (stories && Array.isArray(stories)) {
            this.stories = stories.map((s: any) => ({
                story_id: s.story_id || '',
                persona_id: s.persona_id || '',
                requirement_ids: Array.isArray(s.requirement_ids) ? s.requirement_ids : [],
                statement: s.statement || '',
                status: 'complete' as const,
            }));
        }

        // Fallback: parse from raw_suggestion
        if (this.stories.length === 0 && (result as any).raw_suggestion) {
            this.parseMarkdownIntoState((result as any).raw_suggestion);
        }
    }

    /** Parse stories markdown into state. */
    private parseMarkdownIntoState(md: string): void {
        // Try JSON first (primary format for stories)
        try {
            const data = JSON.parse(md);
            if (data.stories && Array.isArray(data.stories)) {
                this.stories = data.stories.map((s: any) => ({
                    story_id: s.story_id || '',
                    persona_id: s.persona_id || '',
                    requirement_ids: Array.isArray(s.requirement_ids) ? s.requirement_ids : [],
                    statement: s.statement || '',
                    status: 'accepted' as const,
                }));
                return;
            }
        } catch { /* not JSON, parse as markdown */ }

        const lines = md.split('\n');
        this.stories = [];
        let current: StoryCard | null = null;

        const flush = () => {
            if (current && current.statement) { this.stories.push(current); }
        };

        for (const line of lines) {
            const trimmed = line.trim();
            // Match story headings like "### US-001" or "## Story 1"
            if (/^#{2,3}\s+/.test(trimmed)) {
                flush();
                const id = trimmed.replace(/^#{2,3}\s+/, '');
                current = { story_id: id, persona_id: '', requirement_ids: [], statement: '', status: 'accepted' as const };
            } else if (current) {
                // Match **Persona:** Value or **Persona**: Value (colon inside or outside bold)
                if (/^\*\*Persona:?\*\*[:\s]*/i.test(trimmed)) {
                    current.persona_id = trimmed.replace(/^\*\*Persona:?\*\*[:\s]*/i, '').trim();
                } else if (/^\*\*Requirements?:?\*\*[:\s]*/i.test(trimmed)) {
                    const reqs = trimmed.replace(/^\*\*Requirements?:?\*\*[:\s]*/i, '').trim();
                    current.requirement_ids = reqs.split(/[,;]\s*/).filter(r => r.trim());
                } else if (/^As a?\s/i.test(trimmed) || /^I want\s/i.test(trimmed) || /^So that\s/i.test(trimmed)) {
                    current.statement += (current.statement ? '\n' : '') + trimmed;
                } else if (trimmed && !trimmed.startsWith('#') && !trimmed.startsWith('---')) {
                    // General text that might be the story statement
                    if (!current.statement) {
                        current.statement = trimmed;
                    } else if (current.statement.split('\n').length < 4) {
                        current.statement += '\n' + trimmed;
                    }
                }
            }
        }
        flush();
    }

    // -- WIP persistence -------------------------------------------------------

    private get wipPath(): string | undefined {
        if (!this.globalStoragePath) { return undefined; }
        return path.join(this.globalStoragePath, 'wip', 'wip_stories.json');
    }

    private saveWip(): void {
        if (this.readOnly) { return; }
        const wp = this.wipPath;
        if (!wp) { return; }
        const dir = path.dirname(wp);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        const data = {
            stories: this.stories,
            clarifications: this.clarifications,
            socratic: this.socratic,
            savedAt: new Date().toISOString(),
        };
        fs.writeFileSync(wp, JSON.stringify(data, null, 2), 'utf-8');
    }

    private loadWip(): { stories: StoryCard[]; clarifications: Clarification[]; socratic: StoriesSocraticState } | null {
        const wp = this.wipPath;
        if (!wp || !fs.existsSync(wp)) { return null; }
        try {
            const raw = JSON.parse(fs.readFileSync(wp, 'utf-8'));
            if (raw.stories?.length > 0) { return raw; }
        } catch { /* ignore */ }
        return null;
    }

    private deleteWip(): void {
        const wp = this.wipPath;
        if (wp && fs.existsSync(wp)) { fs.unlinkSync(wp); }
    }

    // -- File watcher ----------------------------------------------------------

    private _suppressFileWatch = false;

    private setupFileWatcher(filePath: string): void {
        this.fileWatcher?.dispose();
        const watcher = vscode.workspace.createFileSystemWatcher(
            new vscode.RelativePattern(vscode.Uri.file(path.dirname(filePath)), path.basename(filePath)),
        );
        this.fileWatcher = vscode.Disposable.from(
            watcher,
            watcher.onDidChange(() => this.onArtifactFileChanged()),
        );
    }

    private onArtifactFileChanged(): void {
        if (this._suppressFileWatch || !this.artifactPath) { return; }
        try {
            const md = fs.readFileSync(this.artifactPath, 'utf-8');
            this.parseMarkdownIntoState(md);
            this.updatePanel();
        } catch { /* ignore */ }
    }

    private writeArtifactFile(): void {
        if (!this.artifactPath) { return; }
        this._suppressFileWatch = true;
        const dir = path.dirname(this.artifactPath);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        fs.writeFileSync(this.artifactPath, this.buildFullMarkdown(), 'utf-8');
        setTimeout(() => { this._suppressFileWatch = false; }, 500);
    }

    // -- Panel messaging -------------------------------------------------------

    private updatePanel(): void {
        if (!this.panel) { return; }
        this.panel.webview.postMessage({
            command: 'updateState',
            stories: this.stories,
            socratic: this.socratic,
            isGenerating: this.isGenerating,
            readOnly: this.readOnly,
        });
    }

    private setupMessageHandler(): void {
        if (!this.panel) { return; }

        this.panel.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.command) {
                case 'acceptStory': {
                    const idx = msg.index as number;
                    if (idx >= 0 && idx < this.stories.length) {
                        this.stories[idx].status = 'accepted';
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'editStory': {
                    const idx = msg.index as number;
                    const field = msg.field as string;
                    if (idx >= 0 && idx < this.stories.length) {
                        const s = this.stories[idx];
                        if (field === 'story_id') { s.story_id = msg.value; }
                        else if (field === 'persona_id') { s.persona_id = msg.value; }
                        else if (field === 'statement') { s.statement = msg.value; }
                        else if (field === 'requirement_ids') { s.requirement_ids = (msg.value as string).split(',').map((r: string) => r.trim()).filter(Boolean); }
                        if (s.status === 'accepted') { s.status = 'editing'; }
                        this.saveWip();
                    }
                    break;
                }
                case 'deleteStory': {
                    const idx = msg.index as number;
                    if (idx >= 0 && idx < this.stories.length) {
                        this.stories.splice(idx, 1);
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'addStory': {
                    this.stories.push({
                        story_id: `US-${String(this.stories.length + 1).padStart(3, '0')}`,
                        persona_id: '',
                        requirement_ids: [],
                        statement: '',
                        status: 'editing',
                    });
                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'acceptAll': {
                    for (const s of this.stories) { s.status = 'accepted'; }
                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'respondToProbe': {
                    const answer = msg.answer as string;
                    if (!answer || !this.socratic.probe) { break; }

                    this.socratic.probeAnswered = true;
                    this.clarifications.push({
                        question: this.socratic.probe.question,
                        answer,
                    });

                    this.panel?.webview.postMessage({ command: 'probeLoading' });

                    try {
                        const result = await this.client.generateStories(
                            this.personas, this.srsContent, this.projectDir, this.clarifications,
                        );

                        const stories = (result as any).stories as any[] | undefined;
                        if (stories && Array.isArray(stories) && stories.length > 0) {
                            const accepted = this.stories.filter(s => s.status === 'accepted');
                            const newStories: StoryCard[] = stories.map((s: any) => ({
                                story_id: s.story_id || '',
                                persona_id: s.persona_id || '',
                                requirement_ids: Array.isArray(s.requirement_ids) ? s.requirement_ids : [],
                                statement: s.statement || '',
                                status: 'complete' as const,
                            }));
                            const acceptedIds = new Set(accepted.map(s => s.story_id));
                            this.stories = [
                                ...accepted,
                                ...newStories.filter(s => !acceptedIds.has(s.story_id)),
                            ];
                        }

                        if (result.socratic_probe) {
                            this.socratic.probe = result.socratic_probe;
                            this.socratic.probeAnswered = false;
                            this.socratic.probeCollapsed = false;
                        } else {
                            this.socratic.probe = undefined;
                            this.socratic.probeAnswered = false;
                            this.socratic.needsSocratic = false;
                            this.socratic.socraticReason = '';
                        }
                    } catch (err: any) {
                        vscode.window.showErrorMessage(`Stories regeneration failed: ${err.message}`);
                    }

                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'chatAboutSection': {
                    const context = `**User Stories**\n\n` +
                        this.stories.map(s => `- ${s.story_id}: ${s.statement}`).join('\n');
                    const probeQ = this.socratic.probe?.question ?? '';
                    const fullContext = probeQ ? `${context}\n\nSocratic probe: ${probeQ}` : context;

                    vscode.commands.executeCommand('sdlicit.chatWithContext', {
                        panelId: 'stories',
                        panelType: 'stories',
                        sectionKey: 'stories',
                        sectionHeading: 'User Stories',
                        context: fullContext,
                    });
                    break;
                }
                case 'toggleProbe': {
                    this.socratic.probeCollapsed = !this.socratic.probeCollapsed;
                    this.updatePanel();
                    break;
                }
                case 'saveAndIngest': {
                    const allDone = this.stories.every(s => s.status === 'accepted' || s.status === 'complete');
                    if (!allDone || this.stories.length === 0) {
                        vscode.window.showWarningMessage('Sdlicit: Accept or fill all stories first.');
                        break;
                    }

                    // Build structured data for the backend
                    const storiesData: Record<string, unknown> = {
                        artifact_type: 'stories',
                        stories: this.stories.map(s => ({
                            story_id: s.story_id,
                            persona_id: s.persona_id,
                            requirement_ids: s.requirement_ids,
                            statement: s.statement,
                        })),
                    };

                    let artifactFilePath: string;
                    let fullMd: string;
                    try {
                        const saveResult = await this.client.saveArtifact('stories', storiesData, this.projectDir);
                        artifactFilePath = saveResult.markdown_path || saveResult.json_path;
                        const mdResult = await this.client.renderArtifactMarkdown('stories', this.projectDir);
                        fullMd = mdResult.markdown;
                    } catch {
                        // Fallback to local save
                        fullMd = this.buildFullMarkdown();
                        artifactFilePath = this.store.saveByMeta(
                            { tag: 'STORIES', filename: 'stories.md', relative_path: 'stories.md', artifact_type: 'stories' },
                            fullMd,
                        );
                    }

                    this.artifactPath = artifactFilePath;
                    const artifactId = 'stories';

                    vscode.commands.executeCommand('sdlicit.markArtifactIngesting', artifactId);
                    this.deleteWip();
                    this.panel?.dispose();
                    if (this.resolvePromise) {
                        this.resolvePromise('accepted');
                        this.resolvePromise = undefined;
                    }

                    (async () => {
                        try {
                            await this.client.ingestArtifact(fullMd, 'stories', 'stories');
                            vscode.commands.executeCommand('sdlicit.markArtifactIngested', artifactId);
                            vscode.window.showInformationMessage('Sdlicit: Stories saved and ingested into KB → stories.json');
                        } catch {
                            vscode.commands.executeCommand('sdlicit.markArtifactIngestError', artifactId);
                            vscode.window.showWarningMessage('Sdlicit: Stories saved but KB ingestion failed.');
                        }
                    })();
                    break;
                }
                case 'toggleEdit': {
                    this.readOnly = !this.readOnly;
                    if (!this.readOnly) {
                        for (const s of this.stories) {
                            if (s.status === 'accepted') { s.status = 'complete'; }
                        }
                    }
                    this.updatePanel();
                    break;
                }
                case 'regenerate': {
                    this.deleteWip();
                    this.stories = [];
                    this.clarifications = [];
                    this.socratic = { probeAnswered: false, probeCollapsed: false, needsSocratic: false, socraticReason: '' };
                    this.isGenerating = true;
                    this.updatePanel();
                    const srs = this.srsContent || this.store.getLatestSRS() || '';
                    if (!srs) {
                        vscode.window.showWarningMessage('Sdlicit: No SRS content available for regeneration');
                        this.isGenerating = false;
                        this.updatePanel();
                        break;
                    }
                    this.generate().then(() => {
                        this.isGenerating = false;
                        this.updatePanel();
                        this.saveWip();
                    }).catch((err: any) => {
                        this.isGenerating = false;
                        this.updatePanel();
                        vscode.window.showErrorMessage(`Sdlicit: Stories regeneration failed — ${err.message}`);
                    });
                    break;
                }
                case 'openMarkdown': {
                    const fullMd = this.buildFullMarkdown();
                    if (!fullMd) { break; }
                    if (!this.artifactPath) {
                        this.artifactPath = this.store.saveByMeta(
                            { tag: 'STORIES', filename: 'stories.md', relative_path: 'stories.md', artifact_type: 'stories' },
                            fullMd,
                        );
                        this.setupFileWatcher(this.artifactPath);
                    } else {
                        this.writeArtifactFile();
                    }
                    const uri = vscode.Uri.file(this.artifactPath);
                    const doc = await vscode.workspace.openTextDocument(uri);
                    await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.Beside });
                    break;
                }
            }
        });
    }

    private buildFullMarkdown(): string {
        const lines: string[] = ['# User Stories', ''];
        for (const s of this.stories) {
            lines.push(`## ${s.story_id}`);
            lines.push('');
            if (s.persona_id) { lines.push(`**Persona:** ${s.persona_id}`); }
            if (s.requirement_ids.length > 0) { lines.push(`**Requirements:** ${s.requirement_ids.join(', ')}`); }
            lines.push('');
            lines.push(s.statement);
            lines.push('');
        }
        return lines.join('\n');
    }

    // -- HTML rendering --------------------------------------------------------

    private renderHtml(): void {
        if (!this.panel) { return; }
        const nonce = getNonce();

        const body = `
            <div id="stories-root">
                <div class="flex items-center justify-between mb-md">
                    <h1 style="margin-bottom:0">User Stories</h1>
                    <div class="flex gap-xs">
                        <button class="btn btn-secondary btn-sm" data-action="regenerate" id="regenerate-btn" title="Regenerate stories">Regenerate</button>
                        <button class="btn btn-primary btn-sm hidden" data-action="toggleEdit" id="edit-toggle">Edit</button>
                        <button class="btn btn-secondary btn-sm" data-action="openMarkdown">Markdown</button>
                    </div>
                </div>
                <div id="socratic-container"></div>
                <div id="stories-container"></div>
                <div id="save-bar" class="mt-lg hidden">
                    <button class="btn btn-primary" data-action="saveAndIngest">Save &amp; Ingest to KB</button>
                </div>
                <div id="generating-indicator" class="mt-md hidden">
                    <span class="spinner"></span>
                    <span class="text-sm">Generating user stories…</span>
                </div>
            </div>
        `;

        const scripts = this.buildScripts();
        this.panel.webview.html = wrapHtml(body, nonce, scripts);
    }

    private buildScripts(): string {
        return `
            var _stories = [];
            var _socratic = {};
            var _isGenerating = false;
            var _readOnly = false;
            var _probeLoading = false;

            window.addEventListener('message', function(event) {
                var msg = event.data;
                if (msg.command === 'updateState') {
                    _stories = msg.stories;
                    _socratic = msg.socratic;
                    _isGenerating = msg.isGenerating;
                    _readOnly = msg.readOnly || false;
                    _probeLoading = false;
                    render();
                } else if (msg.command === 'probeLoading') {
                    _probeLoading = true;
                    render();
                }
            });

            function esc(s) {
                var d = document.createElement('div');
                d.textContent = s || '';
                return d.innerHTML;
            }

            function renderSocratic() {
                var container = document.getElementById('socratic-container');
                var html = '';
                var s = _socratic;

                if (_probeLoading) {
                    html += '<div class="companion-panel mt-sm"><div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Processing response…</span></div></div>';
                } else if (s.probe && !s.probeCollapsed && !s.probeAnswered) {
                    html += '<div class="companion-panel mt-sm">';
                    html += '<div class="companion-header flex items-center justify-between">';
                    html += '<span class="text-sm"><strong>Socratic Probe</strong> <span class="text-xs text-muted">(' + esc(s.probe.style) + ' &middot; turn ' + s.probe.turn + '/' + s.probe.max_turns + ')</span></span>';
                    html += '<button class="btn btn-sm btn-secondary" data-action="toggleProbe" style="padding:0 6px;font-size:.75em">Collapse</button>';
                    html += '</div>';
                    if (s.probe.transparency_events && s.probe.transparency_events.length > 0) {
                        html += '<div class="flex gap-xs mt-xs flex-wrap">';
                        for (var te = 0; te < s.probe.transparency_events.length; te++) {
                            html += '<span style="font-size:.7em;padding:1px 6px;border-radius:3px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(s.probe.transparency_events[te]) + '</span>';
                        }
                        html += '</div>';
                    }
                    if (s.probe.kb_facts && s.probe.kb_facts.trim() !== '') {
                        html += '<details class="mt-xs" style="font-size:.82em">';
                        html += '<summary style="cursor:pointer;color:var(--vscode-textLink-foreground);user-select:none">From the Knowledge Base</summary>';
                        html += '<div style="margin-top:4px;padding:6px 8px;border-left:2px solid var(--vscode-textLink-foreground);background:var(--vscode-editor-background);white-space:pre-wrap;word-break:break-word">' + esc(s.probe.kb_facts.trim()) + '</div>';
                        html += '</details>';
                    }
                    html += '<div class="companion-body mt-xs">' + esc(s.probe.question) + '</div>';
                    html += '<div class="flex gap-sm mt-sm">';
                    html += '<input class="probe-input" id="probe-input-main" placeholder="Your response…" style="flex:1" />';
                    html += '<button class="btn btn-primary btn-sm" data-action="respondToProbe">Respond</button>';
                    html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutSection">Chat</button>';
                    html += '</div>';
                    html += '</div>';
                } else if (s.probe && s.probeAnswered) {
                    html += '<div class="companion-panel mt-sm" style="opacity:.6">';
                    html += '<div class="companion-header flex items-center gap-sm">';
                    html += '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>';
                    html += '<span class="text-sm"><strong>Probe Answered</strong></span>';
                    html += '</div>';
                    html += '<div class="companion-body text-sm text-muted">' + esc(s.probe.question) + '</div>';
                    html += '</div>';
                } else if (s.probe && s.probeCollapsed) {
                    html += '<div class="mt-xs"><button class="btn btn-sm btn-secondary text-xs" data-action="toggleProbe">Show Probe</button></div>';
                } else if (s.needsSocratic && s.socraticReason && !s.probe) {
                    html += '<div class="companion-panel mt-sm">';
                    html += '<div class="companion-body text-sm text-muted"><em>Flagged:</em> ' + esc(s.socraticReason) + '</div>';
                    html += '<div class="flex gap-sm mt-xs"><button class="btn btn-secondary btn-sm" data-action="chatAboutSection">Chat about this</button></div>';
                    html += '</div>';
                }

                container.innerHTML = html;
            }

            function renderStories() {
                var container = document.getElementById('stories-container');
                var html = '';

                var allAccepted = _stories.length > 0 && _stories.every(function(s) { return s.status === 'accepted'; });
                var groupStatusIcon = allAccepted
                    ? '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>'
                    : _stories.length === 0
                    ? '<span style="opacity:.4">&#x25CB;</span>'
                    : '<span style="color:var(--vscode-charts-blue)">&#x25CF;</span>';

                html += '<div class="section-panel' + (allAccepted ? '' : ' section-active') + '">';
                html += '<div class="section-header"><div class="flex items-center justify-between" style="width:100%">';
                html += '<div class="flex items-center gap-sm">' + groupStatusIcon + '<strong>User Stories</strong> <span class="text-xs text-muted">(' + _stories.length + ')</span></div>';
                if (!_readOnly && _stories.length > 0 && !allAccepted) {
                    html += '<button class="btn btn-sm btn-primary" data-action="acceptAll">Accept All</button>';
                }
                html += '</div></div>';
                html += '<div class="section-body">';

                if (_isGenerating && _stories.length === 0) {
                    html += '<div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Generating…</span></div>';
                }

                for (var i = 0; i < _stories.length; i++) {
                    var s = _stories[i];
                    var isAccepted = s.status === 'accepted';

                    html += '<div class="req-card' + (isAccepted ? ' req-accepted' : '') + '" style="border:1px solid var(--vscode-panel-border);border-left:4px solid var(--vscode-charts-orange, #d18616);border-radius:6px;padding:14px 18px;margin-bottom:20px;background:var(--vscode-editor-background);box-shadow:0 1px 3px rgba(0,0,0,.12)">';

                    // Header row
                    html += '<div class="flex items-center justify-between" style="margin-bottom:8px">';
                    html += '<div class="flex items-center gap-sm" style="flex-wrap:wrap;gap:8px">';
                    if (isAccepted) {
                        html += '<span style="color:var(--vscode-testing-iconPassed);font-size:1.1em">&#x2714;</span>';
                    }
                    if (_readOnly || isAccepted) {
                        html += '<strong style="font-size:1em">' + esc(s.story_id) + '</strong>';
                    } else {
                        html += '<input type="text" value="' + esc(s.story_id) + '" data-story-idx="' + i + '" data-field="story_id" class="story-field-input" style="font-weight:bold;width:100px" />';
                    }
                    if (s.persona_id) {
                        html += '<span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:.82em;font-weight:500;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">';
                        if (_readOnly || isAccepted) {
                            html += esc(s.persona_id);
                        } else {
                            html += '<input type="text" value="' + esc(s.persona_id) + '" data-story-idx="' + i + '" data-field="persona_id" class="story-field-input" style="width:100px;font-size:.9em;background:transparent;border:none;color:inherit;padding:0" />';
                        }
                        html += '</span>';
                    } else if (!_readOnly && !isAccepted) {
                        html += '<input type="text" value="" data-story-idx="' + i + '" data-field="persona_id" class="story-field-input" placeholder="persona" style="width:100px;font-size:.82em" />';
                    }
                    if (s.requirement_ids && s.requirement_ids.length > 0) {
                        for (var ri = 0; ri < s.requirement_ids.length; ri++) {
                            html += '<span class="text-xs" style="padding:2px 6px;border-radius:4px;background:var(--vscode-textCodeBlock-background);color:var(--vscode-foreground)">' + esc(s.requirement_ids[ri]) + '</span>';
                        }
                    }
                    html += '</div>';
                    if (!_readOnly && !isAccepted) {
                        html += '<div class="flex gap-xs">';
                        html += '<button class="btn btn-primary btn-sm" data-action="acceptStory" data-idx="' + i + '" style="padding:2px 10px;font-size:.8em">Accept</button>';
                        html += '<button class="btn btn-secondary btn-sm" data-action="deleteStory" data-idx="' + i + '" style="padding:2px 8px;font-size:.8em;color:var(--vscode-errorForeground)">&#x2715;</button>';
                        html += '</div>';
                    }
                    html += '</div>';

                    // Statement
                    if (_readOnly || isAccepted) {
                        html += '<div style="margin-bottom:8px;line-height:1.5;white-space:pre-wrap">' + esc(s.statement) + '</div>';
                    } else {
                        html += '<div style="margin-bottom:10px"><label class="text-xs text-muted">Statement</label>';
                        html += '<textarea class="story-field-textarea auto-resize" data-story-idx="' + i + '" data-field="statement" rows="3">' + esc(s.statement) + '</textarea></div>';
                    }

                    // Requirement IDs (editable)
                    if (!_readOnly && !isAccepted) {
                        html += '<div style="margin-bottom:6px"><label class="text-xs text-muted">Requirement IDs (comma-separated)</label>';
                        html += '<input type="text" value="' + esc((s.requirement_ids || []).join(', ')) + '" data-story-idx="' + i + '" data-field="requirement_ids" class="story-field-input" style="width:100%" />';
                        html += '</div>';
                    }

                    html += '</div>';
                }

                if (!_readOnly) {
                    html += '<div class="mt-sm">';
                    html += '<button class="btn btn-secondary btn-sm" data-action="addStory">+ Add Story</button>';
                    html += '</div>';
                }

                html += '</div></div>';
                container.innerHTML = html;
            }

            function render() {
                renderSocratic();
                renderStories();

                var genIndicator = document.getElementById('generating-indicator');
                if (genIndicator) {
                    if (_isGenerating) genIndicator.classList.remove('hidden');
                    else genIndicator.classList.add('hidden');
                }

                var editToggle = document.getElementById('edit-toggle');
                var regenBtn = document.getElementById('regenerate-btn');
                var allDone = _stories.length > 0 && _stories.every(function(s) { return s.status === 'accepted'; });
                if (editToggle) {
                    if (_readOnly || (!_isGenerating && allDone)) editToggle.classList.remove('hidden');
                    else editToggle.classList.add('hidden');
                    editToggle.textContent = _readOnly ? 'Edit' : 'Done Editing';
                }
                if (regenBtn) {
                    if (_readOnly || _isGenerating) regenBtn.classList.add('hidden');
                    else regenBtn.classList.remove('hidden');
                }

                var saveBar = document.getElementById('save-bar');
                if (saveBar) {
                    if (!_isGenerating && _stories.length > 0 && !_readOnly) saveBar.classList.remove('hidden');
                    else saveBar.classList.add('hidden');
                }
            }

            document.addEventListener('click', function(e) {
                var el;
                if ((el = e.target.closest('[data-action="acceptStory"]'))) {
                    vscode.postMessage({ command: 'acceptStory', index: parseInt(el.dataset.idx) });
                    return;
                }
                if ((el = e.target.closest('[data-action="deleteStory"]'))) {
                    vscode.postMessage({ command: 'deleteStory', index: parseInt(el.dataset.idx) });
                    return;
                }
                if ((el = e.target.closest('[data-action="addStory"]'))) {
                    vscode.postMessage({ command: 'addStory' });
                    return;
                }
                if ((el = e.target.closest('[data-action="acceptAll"]'))) {
                    vscode.postMessage({ command: 'acceptAll' });
                    return;
                }
                if ((el = e.target.closest('[data-action="respondToProbe"]'))) {
                    var input = document.getElementById('probe-input-main');
                    var answer = input ? input.value.trim() : '';
                    if (answer) vscode.postMessage({ command: 'respondToProbe', answer: answer });
                    return;
                }
                if ((el = e.target.closest('[data-action="chatAboutSection"]'))) {
                    vscode.postMessage({ command: 'chatAboutSection' });
                    return;
                }
                if ((el = e.target.closest('[data-action="toggleProbe"]'))) {
                    vscode.postMessage({ command: 'toggleProbe' });
                    return;
                }
                if ((el = e.target.closest('[data-action="saveAndIngest"]'))) {
                    vscode.postMessage({ command: 'saveAndIngest' });
                    return;
                }
                if ((el = e.target.closest('[data-action="regenerate"]'))) {
                    vscode.postMessage({ command: 'regenerate' });
                    return;
                }
                if ((el = e.target.closest('[data-action="openMarkdown"]'))) {
                    vscode.postMessage({ command: 'openMarkdown' });
                    return;
                }
                if ((el = e.target.closest('[data-action="toggleEdit"]'))) {
                    vscode.postMessage({ command: 'toggleEdit' });
                    return;
                }
            });

            document.addEventListener('focusout', function(e) {
                var t = e.target;
                if (t && t.classList && (t.classList.contains('story-field-textarea') || t.classList.contains('story-field-input'))) {
                    var idx = parseInt(t.dataset.storyIdx);
                    var field = t.dataset.field;
                    if (!isNaN(idx) && field) {
                        vscode.postMessage({ command: 'editStory', index: idx, field: field, value: t.value });
                    }
                }
            });

            document.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && e.target && e.target.id === 'probe-input-main') {
                    var answer = e.target.value.trim();
                    if (answer) vscode.postMessage({ command: 'respondToProbe', answer: answer });
                }
            });

            // Auto-resize textareas
            function autoResize(el) {
                el.style.height = 'auto';
                el.style.height = el.scrollHeight + 'px';
            }
            document.addEventListener('input', function(e) {
                if (e.target && (e.target.classList.contains('story-field-textarea') || e.target.classList.contains('auto-resize'))) {
                    autoResize(e.target);
                }
            });
            var _origRender = render;
            render = function() {
                _origRender();
                var tas = document.querySelectorAll('.story-field-textarea');
                for (var t = 0; t < tas.length; t++) { autoResize(tas[t]); }
            };
        `;
    }
}
