// ---------------------------------------------------------------------------
// Sdlicit — Personas Panel Provider (Persona Cards with Socratic Probes)
// ---------------------------------------------------------------------------
// WebviewPanel that renders generated user personas as editable cards.
// Per-persona card: editable name, role, goals, frustrations.
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
import { getNonce, wrapHtml } from '../webview/webviewHelper';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PersonaCard {
    name: string;
    role: string;
    goals: string[];
    frustrations: string[];
    status: 'pending' | 'generating' | 'complete' | 'accepted' | 'editing';
}

export interface PersonasSocraticState {
    probe?: SocraticProbe;
    probeAnswered: boolean;
    probeCollapsed: boolean;
    needsSocratic: boolean;
    socraticReason: string;
}

export class PersonasPanelProvider {
    private panel: vscode.WebviewPanel | undefined;
    private personas: PersonaCard[] = [];
    private socratic: PersonasSocraticState = { probeAnswered: false, probeCollapsed: false, needsSocratic: false, socraticReason: '' };
    private isGenerating = false;
    private readOnly = false;
    private clarifications: Clarification[] = [];
    private srsContent = '';
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

    /** Start Personas generation from SRS content. */
    async startGeneration(srsContent: string): Promise<'accepted' | 'declined'> {
        this.srsContent = srsContent;
        const wip = this.loadWip();
        if (wip) {
            this.personas = wip.personas;
            this.clarifications = wip.clarifications;
            this.socratic = wip.socratic;
        } else {
            this.personas = [];
        }
        this.isGenerating = !wip;

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.personasPanel',
            'Sdlicit — User Personas',
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
                await this.generatePersonas(srsContent);
            } catch (err: any) {
                this.isGenerating = false;
                this.updatePanel();
                vscode.window.showErrorMessage(`Sdlicit: Personas generation failed — ${err.message}`);
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

    /** Load an existing Personas markdown into the panel (read-only). */
    async openExisting(markdown: string, filePath?: string): Promise<void> {
        this.readOnly = true;
        this.isGenerating = false;
        this.artifactPath = filePath;
        this.parseMarkdownIntoState(markdown);

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.personasPanel',
            'Sdlicit — User Personas',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );
        this.panel.onDidDispose(() => { this.panel = undefined; this.fileWatcher?.dispose(); });

        if (this.artifactPath) { this.setupFileWatcher(this.artifactPath); }

        this.renderHtml();
        this.setupMessageHandler();
        this.updatePanel();
    }

    /** Call the backend personas endpoint. */
    private async generatePersonas(srsContent: string): Promise<void> {
        this.updatePanel();

        const result: GenerationResponse = await this.client.generatePersonas(
            this.projectDir, srsContent, this.clarifications,
        );

        // Handle socratic probe
        if (result.socratic_probe) {
            this.socratic.probe = result.socratic_probe;
            this.socratic.probeAnswered = false;
            this.socratic.probeCollapsed = false;
            this.socratic.needsSocratic = true;
            this.socratic.socraticReason = result.socratic_probe.question;
        }

        // Populate personas from structured response
        const personas = (result as any).personas as any[] | undefined;
        if (personas && Array.isArray(personas)) {
            this.personas = personas.map((p: any) => ({
                name: p.name || '',
                role: p.role || '',
                goals: Array.isArray(p.goals) ? p.goals : [],
                frustrations: Array.isArray(p.frustrations) ? p.frustrations : [],
                status: 'complete' as const,
            }));
        }

        // Fallback: parse from raw_suggestion
        if (this.personas.length === 0 && (result as any).raw_suggestion) {
            this.parseMarkdownIntoState((result as any).raw_suggestion);
        }
    }

    /** Parse personas markdown into state. */
    private parseMarkdownIntoState(md: string): void {
        // Try JSON first (primary format for personas)
        try {
            const data = JSON.parse(md);
            if (data.personas && Array.isArray(data.personas)) {
                this.personas = data.personas.map((p: any) => ({
                    name: p.name || '',
                    role: p.role || '',
                    goals: Array.isArray(p.goals) ? p.goals : [],
                    frustrations: Array.isArray(p.frustrations) ? p.frustrations : [],
                    status: 'accepted' as const,
                }));
                return;
            }
        } catch { /* not JSON, parse as markdown */ }

        const lines = md.split('\n');
        this.personas = [];
        let current: PersonaCard | null = null;
        let inGoals = false;
        let inFrustrations = false;

        const flush = () => {
            if (current && current.name) {
                this.personas.push(current);
            }
        };

        for (const line of lines) {
            const trimmed = line.trim();
            if (/^##\s+/.test(trimmed)) {
                flush();
                current = { name: trimmed.replace(/^##\s+/, ''), role: '', goals: [], frustrations: [], status: 'accepted' as const };
                inGoals = false;
                inFrustrations = false;
            } else if (current) {
                if (/^\*\*Role:?\*\*[:\s]*/i.test(trimmed)) {
                    current.role = trimmed.replace(/^\*\*Role:?\*\*[:\s]*/i, '').trim();
                    inGoals = false; inFrustrations = false;
                } else if (/^\*\*Goals?:?\*\*/i.test(trimmed)) {
                    inGoals = true; inFrustrations = false;
                } else if (/^\*\*Frustrations?:?\*\*/i.test(trimmed) || /^\*\*Pain Points?:?\*\*/i.test(trimmed)) {
                    inGoals = false; inFrustrations = true;
                } else if (/^[-*]\s+/.test(trimmed)) {
                    const item = trimmed.replace(/^[-*]\s+/, '');
                    if (inGoals) { current.goals.push(item); }
                    else if (inFrustrations) { current.frustrations.push(item); }
                }
            }
        }
        flush();
    }

    // -- WIP persistence -------------------------------------------------------

    private get wipPath(): string | undefined {
        if (!this.globalStoragePath) { return undefined; }
        return path.join(this.globalStoragePath, 'wip', 'wip_personas.json');
    }

    private saveWip(): void {
        if (this.readOnly) { return; }
        const wp = this.wipPath;
        if (!wp) { return; }
        const dir = path.dirname(wp);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        const data = {
            personas: this.personas,
            clarifications: this.clarifications,
            socratic: this.socratic,
            savedAt: new Date().toISOString(),
        };
        fs.writeFileSync(wp, JSON.stringify(data, null, 2), 'utf-8');
    }

    private loadWip(): { personas: PersonaCard[]; clarifications: Clarification[]; socratic: PersonasSocraticState } | null {
        const wp = this.wipPath;
        if (!wp || !fs.existsSync(wp)) { return null; }
        try {
            const raw = JSON.parse(fs.readFileSync(wp, 'utf-8'));
            if (raw.personas?.length > 0) { return raw; }
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
            personas: this.personas,
            socratic: this.socratic,
            isGenerating: this.isGenerating,
            readOnly: this.readOnly,
        });
    }

    private setupMessageHandler(): void {
        if (!this.panel) { return; }

        this.panel.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.command) {
                case 'acceptPersona': {
                    const idx = msg.index as number;
                    if (idx >= 0 && idx < this.personas.length) {
                        this.personas[idx].status = 'accepted';
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'editPersona': {
                    const idx = msg.index as number;
                    const field = msg.field as string;
                    if (idx >= 0 && idx < this.personas.length) {
                        const p = this.personas[idx];
                        if (field === 'name') { p.name = msg.value; }
                        else if (field === 'role') { p.role = msg.value; }
                        else if (field === 'goals') { p.goals = (msg.value as string).split('\n').filter((l: string) => l.trim()); }
                        else if (field === 'frustrations') { p.frustrations = (msg.value as string).split('\n').filter((l: string) => l.trim()); }
                        if (p.status === 'accepted') { p.status = 'editing'; }
                        this.saveWip();
                    }
                    break;
                }
                case 'deletePersona': {
                    const idx = msg.index as number;
                    if (idx >= 0 && idx < this.personas.length) {
                        this.personas.splice(idx, 1);
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'addPersona': {
                    this.personas.push({
                        name: '',
                        role: '',
                        goals: [],
                        frustrations: [],
                        status: 'editing',
                    });
                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'acceptAll': {
                    for (const p of this.personas) { p.status = 'accepted'; }
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
                        const srsContent = this.srsContent || this.store.getLatestSRS() || '';
                        if (srsContent) {
                            const result = await this.client.generatePersonas(
                                this.projectDir, srsContent, this.clarifications,
                            );

                            const personas = (result as any).personas as any[] | undefined;
                            if (personas && Array.isArray(personas) && personas.length > 0) {
                                const accepted = this.personas.filter(p => p.status === 'accepted');
                                const newPersonas: PersonaCard[] = personas.map((p: any) => ({
                                    name: p.name || '',
                                    role: p.role || '',
                                    goals: Array.isArray(p.goals) ? p.goals : [],
                                    frustrations: Array.isArray(p.frustrations) ? p.frustrations : [],
                                    status: 'complete' as const,
                                }));
                                const acceptedNames = new Set(accepted.map(p => p.name));
                                this.personas = [
                                    ...accepted,
                                    ...newPersonas.filter(p => !acceptedNames.has(p.name)),
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
                        }
                    } catch (err: any) {
                        vscode.window.showErrorMessage(`Personas regeneration failed: ${err.message}`);
                    }

                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'chatAboutSection': {
                    const context = `**User Personas**\n\n` +
                        this.personas.map(p => `- ${p.name} (${p.role})`).join('\n');
                    const probeQ = this.socratic.probe?.question ?? '';
                    const fullContext = probeQ ? `${context}\n\nSocratic probe: ${probeQ}` : context;

                    vscode.commands.executeCommand('sdlicit.chatWithContext', {
                        panelId: 'personas',
                        panelType: 'personas',
                        sectionKey: 'personas',
                        sectionHeading: 'User Personas',
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
                    const allDone = this.personas.every(p => p.status === 'accepted' || p.status === 'complete');
                    if (!allDone || this.personas.length === 0) {
                        vscode.window.showWarningMessage('Sdlicit: Accept or fill all personas first.');
                        break;
                    }

                    // Build structured data for the backend
                    const personasData: Record<string, unknown> = {
                        artifact_type: 'personas',
                        personas: this.personas.map(p => ({
                            persona_id: '',
                            name: p.name,
                            role: p.role || '',
                            goals: p.goals || [],
                            frustrations: p.frustrations || [],
                            description: '',
                        })),
                    };

                    let artifactFilePath: string;
                    let fullMd: string;
                    try {
                        const saveResult = await this.client.saveArtifact('personas', personasData, this.projectDir);
                        artifactFilePath = saveResult.markdown_path || saveResult.json_path;
                        const mdResult = await this.client.renderArtifactMarkdown('personas', this.projectDir);
                        fullMd = mdResult.markdown;
                    } catch {
                        // Fallback to local save
                        fullMd = this.buildFullMarkdown();
                        artifactFilePath = this.store.saveByMeta(
                            { tag: 'PERSONAS', filename: 'personas.md', relative_path: 'personas.md', artifact_type: 'personas' },
                            fullMd,
                        );
                    }

                    this.artifactPath = artifactFilePath;
                    const artifactId = 'personas';

                    vscode.commands.executeCommand('sdlicit.markArtifactIngesting', artifactId);
                    this.deleteWip();
                    this.panel?.dispose();
                    if (this.resolvePromise) {
                        this.resolvePromise('accepted');
                        this.resolvePromise = undefined;
                    }

                    (async () => {
                        try {
                            await this.client.ingestArtifact(fullMd, 'personas', 'personas');
                            vscode.commands.executeCommand('sdlicit.markArtifactIngested', artifactId);
                            vscode.window.showInformationMessage('Sdlicit: Personas saved and ingested into KB → personas.json');
                        } catch {
                            vscode.commands.executeCommand('sdlicit.markArtifactIngestError', artifactId);
                            vscode.window.showWarningMessage('Sdlicit: Personas saved but KB ingestion failed.');
                        }
                    })();
                    break;
                }
                case 'toggleEdit': {
                    this.readOnly = !this.readOnly;
                    if (!this.readOnly) {
                        for (const p of this.personas) {
                            if (p.status === 'accepted') { p.status = 'complete'; }
                        }
                    }
                    this.updatePanel();
                    break;
                }
                case 'regenerate': {
                    this.deleteWip();
                    this.personas = [];
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
                    this.generatePersonas(srs).then(() => {
                        this.isGenerating = false;
                        this.updatePanel();
                        this.saveWip();
                    }).catch((err: any) => {
                        this.isGenerating = false;
                        this.updatePanel();
                        vscode.window.showErrorMessage(`Sdlicit: Personas regeneration failed — ${err.message}`);
                    });
                    break;
                }
                case 'openMarkdown': {
                    const fullMd = this.buildFullMarkdown();
                    if (!fullMd) { break; }
                    if (!this.artifactPath) {
                        this.artifactPath = this.store.saveByMeta(
                            { tag: 'PERSONAS', filename: 'personas.md', relative_path: 'personas.md', artifact_type: 'personas' },
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
        const lines: string[] = ['# User Personas', ''];
        for (const p of this.personas) {
            lines.push(`## ${p.name}`);
            lines.push('');
            if (p.role) { lines.push(`**Role:** ${p.role}`); lines.push(''); }
            if (p.goals.length > 0) {
                lines.push('**Goals:**');
                for (const g of p.goals) { lines.push(`- ${g}`); }
                lines.push('');
            }
            if (p.frustrations.length > 0) {
                lines.push('**Frustrations:**');
                for (const f of p.frustrations) { lines.push(`- ${f}`); }
                lines.push('');
            }
        }
        return lines.join('\n');
    }

    // -- HTML rendering --------------------------------------------------------

    private renderHtml(): void {
        if (!this.panel) { return; }
        const nonce = getNonce();

        const body = `
            <div id="personas-root">
                <div class="flex items-center justify-between mb-md">
                    <h1 style="margin-bottom:0">User Personas</h1>
                    <div class="flex gap-xs">
                        <button class="btn btn-secondary btn-sm" data-action="regenerate" id="regenerate-btn" title="Regenerate personas from SRS">Regenerate</button>
                        <button class="btn btn-primary btn-sm hidden" data-action="toggleEdit" id="edit-toggle">Edit</button>
                        <button class="btn btn-secondary btn-sm" data-action="openMarkdown">Markdown</button>
                    </div>
                </div>
                <div id="socratic-container"></div>
                <div id="personas-container"></div>
                <div id="save-bar" class="mt-lg hidden">
                    <button class="btn btn-primary" data-action="saveAndIngest">Save &amp; Ingest to KB</button>
                </div>
                <div id="generating-indicator" class="mt-md hidden">
                    <span class="spinner"></span>
                    <span class="text-sm">Generating personas…</span>
                </div>
            </div>
        `;

        const scripts = this.buildScripts();
        this.panel.webview.html = wrapHtml(body, nonce, scripts);
    }

    private buildScripts(): string {
        return `
            var _personas = [];
            var _socratic = {};
            var _isGenerating = false;
            var _readOnly = false;
            var _probeLoading = false;

            window.addEventListener('message', function(event) {
                var msg = event.data;
                if (msg.command === 'updateState') {
                    _personas = msg.personas;
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

            function renderPersonas() {
                var container = document.getElementById('personas-container');
                var html = '';

                var allAccepted = _personas.length > 0 && _personas.every(function(p) { return p.status === 'accepted'; });
                var groupStatusIcon = allAccepted
                    ? '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>'
                    : _personas.length === 0
                    ? '<span style="opacity:.4">&#x25CB;</span>'
                    : '<span style="color:var(--vscode-charts-blue)">&#x25CF;</span>';

                html += '<div class="section-panel' + (allAccepted ? '' : ' section-active') + '">';
                html += '<div class="section-header"><div class="flex items-center justify-between" style="width:100%">';
                html += '<div class="flex items-center gap-sm">' + groupStatusIcon + '<strong>Personas</strong> <span class="text-xs text-muted">(' + _personas.length + ')</span></div>';
                if (!_readOnly && _personas.length > 0 && !allAccepted) {
                    html += '<button class="btn btn-sm btn-primary" data-action="acceptAll">Accept All</button>';
                }
                html += '</div></div>';
                html += '<div class="section-body">';

                if (_isGenerating && _personas.length === 0) {
                    html += '<div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Generating…</span></div>';
                }

                for (var i = 0; i < _personas.length; i++) {
                    var p = _personas[i];
                    var isAccepted = p.status === 'accepted';

                    html += '<div class="req-card' + (isAccepted ? ' req-accepted' : '') + '" style="border:1px solid var(--vscode-panel-border);border-left:4px solid var(--vscode-charts-green, #89d185);border-radius:6px;padding:14px 18px;margin-bottom:20px;background:var(--vscode-editor-background);box-shadow:0 1px 3px rgba(0,0,0,.12)">';

                    // Header row
                    html += '<div class="flex items-center justify-between" style="margin-bottom:8px">';
                    html += '<div class="flex items-center gap-sm" style="flex-wrap:wrap;gap:8px">';
                    if (isAccepted) {
                        html += '<span style="color:var(--vscode-testing-iconPassed);font-size:1.1em">&#x2714;</span>';
                    }
                    if (_readOnly || isAccepted) {
                        html += '<strong style="font-size:1.1em">' + esc(p.name) + '</strong>';
                    } else {
                        html += '<input type="text" value="' + esc(p.name) + '" data-persona-idx="' + i + '" data-field="name" class="persona-field-input" style="font-weight:bold;font-size:1.1em;width:200px" placeholder="Persona name" />';
                    }
                    if (p.role) {
                        html += '<span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:.82em;font-weight:500;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">';
                        if (_readOnly || isAccepted) {
                            html += esc(p.role);
                        } else {
                            html += '<input type="text" value="' + esc(p.role) + '" data-persona-idx="' + i + '" data-field="role" class="persona-field-input" style="width:120px;font-size:.9em;background:transparent;border:none;color:inherit;padding:0" placeholder="role" />';
                        }
                        html += '</span>';
                    } else if (!_readOnly && !isAccepted) {
                        html += '<input type="text" value="" data-persona-idx="' + i + '" data-field="role" class="persona-field-input" placeholder="role" style="width:120px;font-size:.82em" />';
                    }
                    html += '</div>';
                    if (!_readOnly && !isAccepted) {
                        html += '<div class="flex gap-xs">';
                        html += '<button class="btn btn-primary btn-sm" data-action="acceptPersona" data-idx="' + i + '" style="padding:2px 10px;font-size:.8em">Accept</button>';
                        html += '<button class="btn btn-secondary btn-sm" data-action="deletePersona" data-idx="' + i + '" style="padding:2px 8px;font-size:.8em;color:var(--vscode-errorForeground)">&#x2715;</button>';
                        html += '</div>';
                    }
                    html += '</div>';

                    // Goals
                    if (p.goals.length > 0 || (!_readOnly && !isAccepted)) {
                        if (_readOnly || isAccepted) {
                            html += '<div style="margin-bottom:8px"><span class="text-xs text-muted"><strong>Goals:</strong></span><ul style="margin:4px 0 0 16px;padding:0">';
                            for (var g = 0; g < p.goals.length; g++) {
                                html += '<li style="margin-bottom:2px">' + esc(p.goals[g]) + '</li>';
                            }
                            html += '</ul></div>';
                        } else {
                            html += '<div style="margin-bottom:10px"><label class="text-xs text-muted">Goals (one per line)</label>';
                            html += '<textarea class="persona-field-textarea auto-resize" data-persona-idx="' + i + '" data-field="goals" rows="' + Math.max(2, p.goals.length + 1) + '">' + esc(p.goals.join('\\n')) + '</textarea></div>';
                        }
                    }

                    // Frustrations
                    if (p.frustrations.length > 0 || (!_readOnly && !isAccepted)) {
                        if (_readOnly || isAccepted) {
                            html += '<div style="margin-bottom:8px"><span class="text-xs text-muted"><strong>Frustrations:</strong></span><ul style="margin:4px 0 0 16px;padding:0">';
                            for (var f = 0; f < p.frustrations.length; f++) {
                                html += '<li style="margin-bottom:2px">' + esc(p.frustrations[f]) + '</li>';
                            }
                            html += '</ul></div>';
                        } else {
                            html += '<div style="margin-bottom:10px"><label class="text-xs text-muted">Frustrations (one per line)</label>';
                            html += '<textarea class="persona-field-textarea auto-resize" data-persona-idx="' + i + '" data-field="frustrations" rows="' + Math.max(2, p.frustrations.length + 1) + '">' + esc(p.frustrations.join('\\n')) + '</textarea></div>';
                        }
                    }

                    html += '</div>';
                }

                if (!_readOnly) {
                    html += '<div class="mt-sm">';
                    html += '<button class="btn btn-secondary btn-sm" data-action="addPersona">+ Add Persona</button>';
                    html += '</div>';
                }

                html += '</div></div>';
                container.innerHTML = html;
            }

            function render() {
                renderSocratic();
                renderPersonas();

                var genIndicator = document.getElementById('generating-indicator');
                if (genIndicator) {
                    if (_isGenerating) genIndicator.classList.remove('hidden');
                    else genIndicator.classList.add('hidden');
                }

                var editToggle = document.getElementById('edit-toggle');
                var regenBtn = document.getElementById('regenerate-btn');
                var allDone = _personas.length > 0 && _personas.every(function(p) { return p.status === 'accepted'; });
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
                    if (!_isGenerating && _personas.length > 0 && !_readOnly) saveBar.classList.remove('hidden');
                    else saveBar.classList.add('hidden');
                }
            }

            document.addEventListener('click', function(e) {
                var el;
                if ((el = e.target.closest('[data-action="acceptPersona"]'))) {
                    vscode.postMessage({ command: 'acceptPersona', index: parseInt(el.dataset.idx) });
                    return;
                }
                if ((el = e.target.closest('[data-action="deletePersona"]'))) {
                    vscode.postMessage({ command: 'deletePersona', index: parseInt(el.dataset.idx) });
                    return;
                }
                if ((el = e.target.closest('[data-action="addPersona"]'))) {
                    vscode.postMessage({ command: 'addPersona' });
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
                if (t && t.classList && (t.classList.contains('persona-field-textarea') || t.classList.contains('persona-field-input'))) {
                    var idx = parseInt(t.dataset.personaIdx);
                    var field = t.dataset.field;
                    if (!isNaN(idx) && field) {
                        vscode.postMessage({ command: 'editPersona', index: idx, field: field, value: t.value });
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
                if (e.target && (e.target.classList.contains('persona-field-textarea') || e.target.classList.contains('auto-resize'))) {
                    autoResize(e.target);
                }
            });
            var _origRender = render;
            render = function() {
                _origRender();
                var tas = document.querySelectorAll('.persona-field-textarea');
                for (var t = 0; t < tas.length; t++) { autoResize(tas[t]); }
            };
        `;
    }
}
