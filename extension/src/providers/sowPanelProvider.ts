// ---------------------------------------------------------------------------
// Sdlicit — SOW Panel Provider (Incremental Generation)
// ---------------------------------------------------------------------------
// WebviewPanel that renders SOW sections progressively as SSE events arrive.
// Per-section: editable content, Accept/Clear actions, inline diff view,
// Socratic probes with Respond/Chat buttons, KB verification badges.
// WIP persistence via VS Code globalStorageUri.
// Chat integration: sends section context to chat panel via commands.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { SdlicitClient, SOWStreamEvent, Clarification, SocraticProbe } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { KBSyncService } from '../services/kbSyncService';
import { WipManager } from '../services/wipManager';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

export interface SOWSection {
    key: string;
    heading: string;
    content: string;
    originalContent: string;
    markdown: string;
    status: 'pending' | 'generating' | 'complete' | 'accepted' | 'editing';
    needsSocratic: boolean;
    socraticReason: string;
    socraticProbe?: SocraticProbe;
    probeAnswered: boolean;
    probeCollapsed: boolean;
    kbGrounded?: boolean;
    kbUngroundedClaims?: string[];
    proposedContent?: string;
    clarifications: Clarification[];
}

const SECTION_ORDER = [
    'project_name',
    'problem_statement',
    'stakeholders',
    'constraints',
    'out_of_scope',
    'open_questions',
];

const SECTION_HEADINGS: Record<string, string> = {
    project_name: 'Project Name',
    problem_statement: 'Problem Statement',
    stakeholders: 'Stakeholders',
    constraints: 'Constraints',
    out_of_scope: 'Out of Scope',
    open_questions: 'Open Questions',
};

export class SOWPanelProvider {
    private panel: vscode.WebviewPanel | undefined;
    private sections: SOWSection[] = [];
    private brief = '';
    private isGenerating = false;
    private readOnly = false;

    /** Whether the panel is still open. */
    get isAlive(): boolean { return this.panel !== undefined; }

    /** Reveal (focus) the existing panel without creating a new one. */
    reveal(): void { this.panel?.reveal(vscode.ViewColumn.One); }
    private artifactPath: string | undefined;
    private fileWatcher: vscode.Disposable | undefined;
    private resolvePromise?: (value: 'accepted' | 'declined') => void;
    private isGuidedFlow = false;

    constructor(
        private readonly client: SdlicitClient,
        private readonly store: ArtifactStore,
        private readonly kbSync?: KBSyncService,
        private readonly globalStoragePath?: string,
        private readonly dataService?: import('../services/dataService').DataService,
    ) {}

    /** Get sections for external access (e.g., chat integration). */
    getSections(): SOWSection[] { return this.sections; }

    /** Update a section's content from external source (e.g., chat "Insert"). */
    updateSectionFromExternal(sectionKey: string, newContent: string): void {
        const section = this.sections.find(s => s.key === sectionKey);
        if (!section) { return; }
        section.proposedContent = newContent;
        if (section.status === 'accepted') { section.status = 'editing'; }
        this.updatePanel();
        this.saveWip();
    }

    /** Load an existing SOW markdown into the panel (read-only by default). */
    async openExisting(markdown: string, filePath?: string): Promise<void> {
        this.readOnly = true;
        this.isGenerating = false;
        this.artifactPath = filePath;
        this.sections = this.parseSOWMarkdown(markdown);

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.sowPanel',
            'Sdlicit — Statement of Work',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );
        this.panel.onDidDispose(() => {
            this.panel = undefined;
            this.fileWatcher?.dispose();
            vscode.commands.executeCommand('sdlicit.unregisterPanel', 'sow');
        });

        // Watch the artifact file for external changes (e.g. edited via Markdown view)
        if (this.artifactPath) {
            this.setupFileWatcher(this.artifactPath);
        }

        this.renderHtml();
        this.setupMessageHandler(this.brief, []);
        this.updatePanel();
    }

    /** Parse SOW markdown back into sections. */
    private parseSOWMarkdown(md: string): SOWSection[] {
        const lines = md.split('\n');
        const sections: SOWSection[] = [];
        let currentKey = '';
        let currentHeading = '';
        let contentLines: string[] = [];

        const headingToKey: Record<string, string> = {};
        for (const [k, v] of Object.entries(SECTION_HEADINGS)) {
            headingToKey[v.toLowerCase()] = k;
        }

        const flush = () => {
            if (currentKey) {
                const content = contentLines.join('\n').trim();
                sections.push({
                    key: currentKey,
                    heading: currentHeading,
                    content,
                    originalContent: content,
                    markdown: content,
                    status: 'accepted',
                    needsSocratic: false,
                    socraticReason: '',
                    probeAnswered: false,
                    probeCollapsed: false,
                    clarifications: [],
                });
            }
            contentLines = [];
        };

        for (const line of lines) {
            const h1 = line.match(/^#\s+(.+)/);
            const h2 = line.match(/^##\s+(.+)/);
            if (h1) {
                flush();
                const title = h1[1].trim();
                currentKey = 'project_name';
                currentHeading = 'Project Name';
                contentLines.push(title);
            } else if (h2) {
                flush();
                const heading = h2[1].trim();
                currentKey = headingToKey[heading.toLowerCase()] || heading.toLowerCase().replace(/\s+/g, '_');
                currentHeading = heading;
            } else {
                contentLines.push(line);
            }
        }
        flush();

        // Fill missing sections
        for (const key of SECTION_ORDER) {
            if (!sections.find(s => s.key === key)) {
                sections.push({
                    key,
                    heading: SECTION_HEADINGS[key],
                    content: '',
                    originalContent: '',
                    markdown: '',
                    status: 'complete',
                    needsSocratic: false,
                    socraticReason: '',
                    probeAnswered: false,
                    probeCollapsed: false,
                    clarifications: [],
                });
            }
        }

        // Sort by SECTION_ORDER
        return sections.sort((a, b) => SECTION_ORDER.indexOf(a.key) - SECTION_ORDER.indexOf(b.key));
    }

    async startGeneration(
        brief: string,
        clarifications: Clarification[] = [],
        options?: { guidedFlow?: boolean },
    ): Promise<'accepted' | 'declined'> {
        this.brief = brief;
        this.isGuidedFlow = options?.guidedFlow ?? false;

        // Prompt user if WIP data exists
        let wip: { brief: string; sections: SOWSection[] } | null = null;
        if (this.globalStoragePath) {
            const wipMgr = new WipManager(this.globalStoragePath);
            const decision = await wipMgr.promptIfWipExists('sow');
            if (decision === 'resume') {
                wip = this.loadWip();
            } else {
                this.deleteWip();
            }
        } else {
            wip = this.loadWip();
        }

        if (wip) {
            this.sections = wip.sections;
        } else {
            this.sections = SECTION_ORDER.map(key => ({
                key,
                heading: SECTION_HEADINGS[key],
                content: '',
                originalContent: '',
                markdown: '',
                status: 'pending' as const,
                needsSocratic: false,
                socraticReason: '',
                probeAnswered: false,
                probeCollapsed: false,
                clarifications: [],
            }));
        }
        this.isGenerating = !wip;

        // Create webview panel
        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.sowPanel',
            'Sdlicit — Statement of Work',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        this.panel.onDidDispose(() => {
            this.saveWip();
            this.panel = undefined;
            this.fileWatcher?.dispose();
            vscode.commands.executeCommand('sdlicit.unregisterPanel', 'sow');
            if (this.resolvePromise) {
                this.resolvePromise('declined');
                this.resolvePromise = undefined;
            }
        });

        this.renderHtml();
        this.setupMessageHandler(brief, clarifications);

        if (!wip) {
            this.client.log('SOW: Starting incremental generation via SSE');
            try {
                await this.client.createSOWStream(
                    brief, clarifications,
                    (event) => this.handleStreamEvent(event),
                );
            } catch (err: any) {
                this.client.log(`SOW: Stream error — ${err.message}`);
                this.isGenerating = false;
                this.updatePanel();
                vscode.window.showErrorMessage(`Sdlicit: SOW generation failed — ${err.message}`);
                return 'declined';
            }
            this.isGenerating = false;
            this.updatePanel();
            this.saveWip();
            this.client.log('SOW: All sections generated — waiting for user review');
        } else {
            this.updatePanel();
            this.client.log('SOW: Restored from WIP');
        }

        return new Promise<'accepted' | 'declined'>((resolve) => {
            this.resolvePromise = resolve;
        });
    }

    // -- WIP persistence -------------------------------------------------------

    private get wipPath(): string | undefined {
        if (!this.globalStoragePath) { return undefined; }
        return path.join(this.globalStoragePath, 'wip', 'wip_sow.json');
    }

    private saveWip(): void {
        const wp = this.wipPath;
        if (!wp) { return; }
        const dir = path.dirname(wp);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        const data = { brief: this.brief, sections: this.sections, savedAt: new Date().toISOString() };
        fs.writeFileSync(wp, JSON.stringify(data, null, 2), 'utf-8');
    }

    private loadWip(): { brief: string; sections: SOWSection[] } | null {
        const wp = this.wipPath;
        if (!wp || !fs.existsSync(wp)) { return null; }
        try {
            const raw = JSON.parse(fs.readFileSync(wp, 'utf-8'));
            // Only restore if there's actual content (not just empty shells)
            const hasContent = raw.sections?.some((s: any) => s.content?.trim());
            if (hasContent) { return raw; }
        } catch { /* ignore */ }
        return null;
    }

    private deleteWip(): void {
        const wp = this.wipPath;
        if (wp && fs.existsSync(wp)) { fs.unlinkSync(wp); }
    }

    // -- File watcher for markdown sync ----------------------------------------

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
            this.sections = this.parseSOWMarkdown(md);
            this.updatePanel();
        } catch { /* file may have been deleted */ }
    }

    private writeArtifactFile(): void {
        if (!this.artifactPath) { return; }
        this._suppressFileWatch = true;
        const dir = path.dirname(this.artifactPath);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        fs.writeFileSync(this.artifactPath, this.buildFullMarkdown(), 'utf-8');
        setTimeout(() => { this._suppressFileWatch = false; }, 500);
    }

    // -- Stream events ---------------------------------------------------------

    private handleStreamEvent(event: SOWStreamEvent): void {
        const idx = this.sections.findIndex(s => s.key === event.section);

        switch (event.event) {
            case 'section_start':
                if (idx >= 0) { this.sections[idx].status = 'generating'; }
                this.updatePanel();
                break;
            case 'section_complete':
                if (idx >= 0) {
                    this.sections[idx].status = 'complete';
                    this.sections[idx].content = event.content ?? '';
                    this.sections[idx].originalContent = event.content ?? '';
                    this.sections[idx].markdown = event.markdown ?? '';
                    this.sections[idx].needsSocratic = event.needs_socratic ?? false;
                    this.sections[idx].socraticReason = event.socratic_reason ?? '';
                }
                this.updatePanel();
                break;
            case 'socratic_probe':
                if (idx >= 0 && event.probe) {
                    this.sections[idx].socraticProbe = event.probe;
                    this.sections[idx].probeCollapsed = false;
                }
                this.updatePanel();
                break;
            case 'kb_verification':
                if (idx >= 0) {
                    this.sections[idx].kbGrounded = event.grounded;
                    this.sections[idx].kbUngroundedClaims = event.ungrounded_claims;
                }
                this.updatePanel();
                break;
            case 'complete':
                break;
        }
    }

    private updatePanel(): void {
        if (!this.panel) { return; }

        // Compute downstream trace links (SOW → SRS/ADR)
        const traceLinks: { downstream: string[]; testedBy: string[] } = { downstream: [], testedBy: [] };
        if (this.dataService) {
            const artifacts = this.dataService.getArtifacts();
            const sowArtifact = artifacts.find(a => a.type === 'sow');
            if (sowArtifact) {
                traceLinks.downstream = sowArtifact.traces.downstream;
                traceLinks.testedBy = sowArtifact.traces.testedBy;
            }
            // Also find artifacts that trace FROM this SOW
            const referencedBy = artifacts.filter(a =>
                a.traces.upstream.some(u => u === 'SOW' || u.toLowerCase().includes('sow'))
            );
            for (const a of referencedBy) {
                if (!traceLinks.downstream.includes(a.id)) {
                    traceLinks.downstream.push(a.id);
                }
            }
        }

        this.panel.webview.postMessage({
            command: 'updateSections',
            sections: this.sections,
            isGenerating: this.isGenerating,
            readOnly: this.readOnly,
            traceLinks,
        });
    }

    // -- Message handling -------------------------------------------------------

    private setupMessageHandler(brief: string, _clarifications: Clarification[]): void {
        if (!this.panel) { return; }

        this.panel.webview.onDidReceiveMessage(async (msg) => {
            const sectionIdx = msg.sectionKey
                ? this.sections.findIndex(s => s.key === msg.sectionKey)
                : -1;

            switch (msg.command) {
                case 'acceptSection': {
                    if (sectionIdx >= 0) {
                        const s = this.sections[sectionIdx];
                        s.status = 'accepted';
                        s.probeCollapsed = true;
                        s.proposedContent = undefined;
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'clearSection': {
                    if (sectionIdx >= 0) {
                        this.sections[sectionIdx].content = '';
                        this.sections[sectionIdx].status = 'editing';
                        this.sections[sectionIdx].proposedContent = undefined;
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'editSection': {
                    if (sectionIdx >= 0) {
                        this.sections[sectionIdx].content = msg.content;
                        if (this.sections[sectionIdx].status === 'accepted') {
                            this.sections[sectionIdx].status = 'editing';
                        }
                        this.saveWip();
                        this.writeArtifactFile();
                    }
                    break;
                }
                case 'acceptDiff': {
                    if (sectionIdx >= 0 && this.sections[sectionIdx].proposedContent) {
                        this.sections[sectionIdx].content = this.sections[sectionIdx].proposedContent!;
                        this.sections[sectionIdx].originalContent = this.sections[sectionIdx].proposedContent!;
                        this.sections[sectionIdx].proposedContent = undefined;
                        this.sections[sectionIdx].status = 'complete';
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'rejectDiff': {
                    if (sectionIdx >= 0) {
                        this.sections[sectionIdx].proposedContent = undefined;
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'respondToProbe': {
                    if (sectionIdx < 0) { break; }
                    const section = this.sections[sectionIdx];
                    const answer = msg.answer as string;
                    if (!answer) { break; }

                    // Track this Q&A for turn counting
                    const question = section.socraticProbe?.question ?? '';
                    section.clarifications.push({ question, answer });

                    // Mark current probe as answered immediately
                    section.probeAnswered = true;

                    this.panel?.webview.postMessage({
                        command: 'sectionLoading', sectionKey: section.key,
                    });

                    try {
                        const priorMd = this.sections
                            .filter(s => s.key !== section.key && s.content)
                            .map(s => s.markdown || s.content)
                            .join('\n\n');

                        const result = await this.client.regenerateSection(
                            brief, section.key, priorMd, answer, section.content,
                            section.clarifications,
                        );
                        if (result.content) {
                            section.proposedContent = result.content;
                            section.needsSocratic = result.needs_socratic;
                            section.socraticReason = result.socratic_reason;
                        }
                        // Handle new probe from backend
                        if (result.socratic_probe) {
                            section.socraticProbe = result.socratic_probe;
                            section.probeAnswered = false;
                            section.probeCollapsed = false;
                        } else if (!result.needs_socratic) {
                            // No more probes needed — clear
                            section.socraticProbe = undefined;
                            section.probeAnswered = false;
                            section.needsSocratic = false;
                            section.socraticReason = '';
                        }
                    } catch (err: any) {
                        vscode.window.showErrorMessage(`Regeneration failed: ${err.message}`);
                    }

                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'chatAboutSection': {
                    if (sectionIdx < 0) { break; }
                    const s = this.sections[sectionIdx];
                    const probeQ = s.socraticProbe?.question ?? s.socraticReason ?? '';
                    const context = [
                        `**SOW Section: ${s.heading}**`,
                        '',
                        s.content ? `Current content:\n${s.content}` : '(empty)',
                        '',
                        probeQ ? `Socratic probe: ${probeQ}` : '',
                    ].filter(Boolean).join('\n');

                    vscode.commands.executeCommand('sdlicit.chatWithContext', {
                        sectionKey: s.key,
                        sectionHeading: s.heading,
                        context,
                    });
                    break;
                }
                case 'toggleProbe': {
                    if (sectionIdx >= 0) {
                        this.sections[sectionIdx].probeCollapsed = !this.sections[sectionIdx].probeCollapsed;
                        this.updatePanel();
                    }
                    break;
                }
                case 'openArtifact': {
                    if (msg.artifactId) {
                        vscode.commands.executeCommand('sdlicit.openCanvas', msg.artifactId);
                    }
                    break;
                }
                case 'saveAndIngest': {
                    const allDone = this.sections.every(
                        s => s.status === 'accepted' || s.status === 'complete',
                    );
                    if (!allDone) {
                        vscode.window.showWarningMessage('Sdlicit: Accept or fill all sections first.');
                        break;
                    }

                    const fullMd = this.buildFullMarkdown();
                    const artifactFilePath = this.store.saveByMeta(
                        { tag: 'SOW', filename: 'sow.md', relative_path: 'sow.md', artifact_type: 'sow' },
                        fullMd,
                    );
                    this.artifactPath = artifactFilePath;
                    const artifactId = 'sow';

                    // Notify artifact tree and start async ingestion
                    vscode.commands.executeCommand('sdlicit.markArtifactIngesting', artifactId);
                    this.deleteWip();

                    // Start async ingestion
                    (async () => {
                        try {
                            await this.client.ingestArtifact(fullMd, 'sow', 'sow');
                            vscode.commands.executeCommand('sdlicit.markArtifactIngested', artifactId);
                        } catch {
                            vscode.commands.executeCommand('sdlicit.markArtifactIngestError', artifactId);
                            vscode.window.showWarningMessage('Sdlicit: SOW saved but KB ingestion failed.');
                        }
                    })();

                    this.panel?.dispose();
                    if (this.resolvePromise) {
                        this.resolvePromise('accepted');
                        this.resolvePromise = undefined;
                    }

                    // Only show "what next?" when NOT in guided flow (guided flow has its own continuation)
                    if (!this.isGuidedFlow) {
                        const nextAction = await vscode.window.showInformationMessage(
                            'SOW saved and ingesting into KB. What would you like to do next?',
                            { modal: false },
                            'Generate SRS',
                            'Generate ADR',
                            'Done',
                        );
                        if (nextAction === 'Generate SRS') {
                            vscode.commands.executeCommand('sdlicit.newArtifact');
                        } else if (nextAction === 'Generate ADR') {
                            vscode.commands.executeCommand('sdlicit.suggestDirections');
                        }
                    }
                    break;
                }
                case 'toggleEdit': {
                    this.readOnly = !this.readOnly;
                    // When switching to edit mode, mark all accepted sections as 'complete' so textareas show
                    if (!this.readOnly) {
                        for (const s of this.sections) {
                            if (s.status === 'accepted') { s.status = 'complete'; }
                        }
                    }
                    this.updatePanel();
                    break;
                }
                case 'openMarkdown': {
                    const fullMd = this.buildFullMarkdown();
                    if (!fullMd) { break; }
                    // Save to existing artifact path or create one
                    if (!this.artifactPath) {
                        this.artifactPath = this.store.saveByMeta(
                            { tag: 'SOW', filename: 'sow.md', relative_path: 'sow.md', artifact_type: 'sow' },
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
                case 'regenerate': {
                    this.deleteWip();
                    this.sections = SECTION_ORDER.map(key => ({
                        key,
                        heading: SECTION_HEADINGS[key],
                        content: '',
                        originalContent: '',
                        markdown: '',
                        status: 'pending' as const,
                        needsSocratic: false,
                        socraticReason: '',
                        probeAnswered: false,
                        probeCollapsed: false,
                        clarifications: [],
                    }));
                    this.isGenerating = true;
                    this.updatePanel();
                    if (!this.brief) {
                        vscode.window.showWarningMessage('Sdlicit: No brief content available for regeneration');
                        this.isGenerating = false;
                        this.updatePanel();
                        break;
                    }
                    this.client.createSOWStream(
                        this.brief, [],
                        (event) => this.handleStreamEvent(event),
                    ).then(() => {
                        this.isGenerating = false;
                        this.updatePanel();
                        this.saveWip();
                    }).catch((err: any) => {
                        this.isGenerating = false;
                        this.updatePanel();
                        vscode.window.showErrorMessage(`Sdlicit: SOW regeneration failed — ${err.message}`);
                    });
                    break;
                }
            }
        });
    }

    private buildFullMarkdown(): string {
        return this.sections
            .filter(s => s.content)
            .map(s => {
                if (s.key === 'project_name') { return `# ${s.content}`; }
                return `## ${s.heading}\n\n${s.content}`;
            })
            .join('\n\n');
    }

    // -- HTML rendering --------------------------------------------------------

    private renderHtml(): void {
        if (!this.panel) { return; }
        const nonce = getNonce();

        const body = `
            <div id="sow-root">
                <div class="flex items-center justify-between mb-md">
                    <h1 style="margin-bottom:0">Statement of Work</h1>
                    <div class="flex gap-xs">
                        <button class="btn btn-secondary btn-sm" data-action="regenerate" id="regenerate-btn" title="Clear cache and regenerate from brief">Regenerate</button>
                        <button class="btn btn-primary btn-sm hidden" data-action="toggleEdit" id="edit-toggle">Edit</button>
                        <button class="btn btn-secondary btn-sm" data-action="toggleFocus" id="focus-toggle">Focus</button>
                        <button class="btn btn-secondary btn-sm" data-action="openMarkdown">Markdown</button>
                    </div>
                </div>
                <div id="focus-nav" class="flex items-center justify-between mb-sm hidden">
                    <button class="btn btn-secondary btn-sm" data-action="focusPrev" id="focus-prev">&larr; Previous</button>
                    <span id="focus-label" class="text-sm text-muted"></span>
                    <button class="btn btn-secondary btn-sm" data-action="focusNext" id="focus-next">Next &rarr;</button>
                </div>
                <div id="trace-links-container"></div>
                <div id="sections-container"></div>
                <div id="save-bar" class="mt-lg hidden">
                    <button class="btn btn-primary" data-action="saveAndIngest">Save &amp; Ingest to KB</button>
                </div>
                <div id="generating-indicator" class="mt-md hidden">
                    <span class="spinner"></span>
                    <span class="text-sm">Generating sections…</span>
                </div>
            </div>
        `;

        const scripts = this.buildScripts();
        this.panel.webview.html = wrapHtml(body, nonce, scripts);
    }

    private buildScripts(): string {
        return `
            var _sections = [];
            var _isGenerating = false;
            var _loadingSection = null;
            var _focusMode = false;
            var _focusIndex = 0;
            var _readOnly = false;
            var _traceLinks = { downstream: [], testedBy: [] };

            window.addEventListener('message', function(event) {
                var msg = event.data;
                if (msg.command === 'updateSections') {
                    _sections = msg.sections;
                    _isGenerating = msg.isGenerating;
                    _readOnly = msg.readOnly || false;
                    _traceLinks = msg.traceLinks || { downstream: [], testedBy: [] };
                    _loadingSection = null;
                    renderTraceLinks();
                    renderSections();
                } else if (msg.command === 'sectionLoading') {
                    _loadingSection = msg.sectionKey;
                    renderSections();
                }
            });

            function esc(s) {
                var d = document.createElement('div');
                d.textContent = s || '';
                return d.innerHTML;
            }

            function renderTraceLinks() {
                var container = document.getElementById('trace-links-container');
                if (!container) return;
                var hasLinks = _traceLinks.downstream.length > 0 || _traceLinks.testedBy.length > 0;
                if (!hasLinks) { container.innerHTML = ''; return; }

                var html = '<div class="card-flat mb-md" style="padding:10px 14px;border-left:3px solid var(--vscode-textLink-foreground)">';
                html += '<div class="text-xs text-muted mb-xs"><strong>Connected Artifacts</strong></div>';

                if (_traceLinks.downstream.length > 0) {
                    html += '<div class="flex items-center gap-sm flex-wrap mb-xs">';
                    html += '<span class="text-xs text-muted">Downstream:</span>';
                    for (var i = 0; i < _traceLinks.downstream.length; i++) {
                        html += '<span class="trace-node clickable" data-artifact-id="' + esc(_traceLinks.downstream[i]) + '" title="Click to open" style="cursor:pointer;padding:2px 8px;border-radius:4px;font-size:.8em;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(_traceLinks.downstream[i]) + '</span>';
                    }
                    html += '</div>';
                }
                if (_traceLinks.testedBy.length > 0) {
                    html += '<div class="flex items-center gap-sm flex-wrap">';
                    html += '<span class="text-xs text-muted">Tested by:</span>';
                    for (var j = 0; j < _traceLinks.testedBy.length; j++) {
                        html += '<span class="trace-node clickable" data-artifact-id="' + esc(_traceLinks.testedBy[j]) + '" title="Click to open" style="cursor:pointer;padding:2px 8px;border-radius:4px;font-size:.8em;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(_traceLinks.testedBy[j]) + '</span>';
                    }
                    html += '</div>';
                }
                html += '</div>';
                container.innerHTML = html;
            }

            function fmtInline(s) {
                return esc(s)
                    .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
                    .replace(/\\*(.+?)\\*/g, '<em>$1</em>')
                    .replace(/\\\`([^\\\`]+)\\\`/g, '<code>$1</code>');
            }

            function fmtMd(text) {
                if (!text) return '';
                var lines = text.split('\\n');
                var html = '';
                var inUl = false;
                var inOl = false;
                for (var i = 0; i < lines.length; i++) {
                    var t = lines[i].trim();
                    if (t.startsWith('### ')) {
                        if (inUl) { html += '</ul>'; inUl = false; }
                        if (inOl) { html += '</ol>'; inOl = false; }
                        html += '<h4>' + fmtInline(t.slice(4)) + '</h4>';
                    } else if (t.startsWith('## ')) {
                        if (inUl) { html += '</ul>'; inUl = false; }
                        if (inOl) { html += '</ol>'; inOl = false; }
                        html += '<h3>' + fmtInline(t.slice(3)) + '</h3>';
                    } else if (t.startsWith('# ')) {
                        if (inUl) { html += '</ul>'; inUl = false; }
                        if (inOl) { html += '</ol>'; inOl = false; }
                        html += '<h2>' + fmtInline(t.slice(2)) + '</h2>';
                    } else if (/^[-*] /.test(t)) {
                        if (inOl) { html += '</ol>'; inOl = false; }
                        if (!inUl) { html += '<ul>'; inUl = true; }
                        html += '<li>' + fmtInline(t.slice(2)) + '</li>';
                    } else if (/^\\d+[.)]\\s/.test(t)) {
                        if (inUl) { html += '</ul>'; inUl = false; }
                        if (!inOl) { html += '<ol>'; inOl = true; }
                        html += '<li>' + fmtInline(t.replace(/^\\d+[.)]\\s/, '')) + '</li>';
                    } else if (t === '') {
                        if (inUl) { html += '</ul>'; inUl = false; }
                        if (inOl) { html += '</ol>'; inOl = false; }
                    } else {
                        if (inUl) { html += '</ul>'; inUl = false; }
                        if (inOl) { html += '</ol>'; inOl = false; }
                        html += '<p>' + fmtInline(t) + '</p>';
                    }
                }
                if (inUl) html += '</ul>';
                if (inOl) html += '</ol>';
                return html;
            }

            // Word-level diff using longest common subsequence
            function splitSentences(text) {
                // Split on sentence boundaries (period/!/?/newline) but keep delimiters
                var parts = (text || '').split(/(?<=[.!?\\n])\\s+/);
                return parts.filter(function(p) { return p.length > 0; });
            }

            function lcs(a, b) {
                var m = a.length, n = b.length;
                var dp = [];
                for (var i = 0; i <= m; i++) {
                    dp[i] = [];
                    for (var j = 0; j <= n; j++) dp[i][j] = 0;
                }
                for (var i = 1; i <= m; i++) {
                    for (var j = 1; j <= n; j++) {
                        if (a[i-1] === b[j-1]) dp[i][j] = dp[i-1][j-1] + 1;
                        else dp[i][j] = Math.max(dp[i-1][j], dp[i][j-1]);
                    }
                }
                var ops = [];
                var i = m, j = n;
                while (i > 0 || j > 0) {
                    if (i > 0 && j > 0 && a[i-1] === b[j-1]) {
                        ops.push({ type: 'same', text: a[i-1] });
                        i--; j--;
                    } else if (j > 0 && (i === 0 || dp[i][j-1] >= dp[i-1][j])) {
                        ops.push({ type: 'add', text: b[j-1] });
                        j--;
                    } else {
                        ops.push({ type: 'del', text: a[i-1] });
                        i--;
                    }
                }
                ops.reverse();
                return ops;
            }

            function buildDiff(oldText, newText) {
                var oldSentences = splitSentences(oldText);
                var newSentences = splitSentences(newText);
                var ops = lcs(oldSentences, newSentences);
                var html = '<div class="diff-inline">';
                for (var k = 0; k < ops.length; k++) {
                    var o = ops[k];
                    if (o.type === 'same') {
                        html += '<span class="diff-kept">' + esc(o.text) + ' </span>';
                    } else if (o.type === 'del') {
                        html += '<span class="diff-removed">' + esc(o.text) + ' </span>';
                    } else {
                        html += '<span class="diff-added">' + esc(o.text) + ' </span>';
                    }
                }
                html += '</div>';
                return html;
            }

            function renderSections() {
                var container = document.getElementById('sections-container');
                var saveBar = document.getElementById('save-bar');
                var genIndicator = document.getElementById('generating-indicator');
                var html = '';
                var allDone = true;

                for (var i = 0; i < _sections.length; i++) {
                    var s = _sections[i];
                    var isLoading = _loadingSection === s.key;
                    var isAccepted = s.status === 'accepted';
                    var hasDiff = !!s.proposedContent;

                    if (!isAccepted) allDone = false;

                    // Status indicator
                    var statusIcon = '';
                    if (isLoading || s.status === 'generating') {
                        statusIcon = '<span class="spinner" style="width:12px;height:12px"></span>';
                    } else if (isAccepted) {
                        statusIcon = '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>';
                    } else if (s.status === 'pending') {
                        statusIcon = '<span style="opacity:.4">&#x25CB;</span>';
                    } else {
                        statusIcon = '<span style="color:var(--vscode-charts-blue)">&#x25CF;</span>';
                    }

                    // KB badge
                    var kbBadge = '';
                    if (s.kbGrounded === true) {
                        kbBadge = ' <span class="text-xs" style="color:var(--vscode-testing-iconPassed)" title="Grounded in KB">KB &#x2714;</span>';
                    } else if (s.kbGrounded === false) {
                        kbBadge = ' <span class="text-xs" style="color:var(--vscode-errorForeground)" title="' + esc((s.kbUngroundedClaims||[]).join(', ')) + '">KB &#x26A0;</span>';
                    }

                    var focusHidden = _focusMode && i !== _focusIndex;
                    html += '<div class="section-panel' + (isAccepted ? '' : ' section-active') + (focusHidden ? ' hidden' : '') + '" data-key="' + s.key + '">';

                    // Header
                    html += '<div class="section-header">';
                    html += '<div class="flex items-center gap-sm">';
                    html += statusIcon;
                    html += '<strong>' + esc(s.heading) + '</strong>';
                    html += kbBadge;
                    html += '</div>';
                    html += '</div>';

                    // Body
                    html += '<div class="section-body">';

                    if (s.status === 'generating' || (s.status === 'pending' && _isGenerating)) {
                        html += '<div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Generating…</span></div>';
                    } else if (isLoading) {
                        html += '<div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Regenerating…</span></div>';
                    } else if (s.status === 'pending' && !_isGenerating) {
                        html += '<div class="text-sm text-muted">Waiting…</div>';
                    } else {
                        if (_readOnly) {
                            // Read-only: always render as formatted content
                            html += '<div class="section-content">' + fmtMd(s.content) + '</div>';
                        } else if (hasDiff) {
                            // Diff view
                            html += '<div class="mb-sm">' + buildDiff(s.content, s.proposedContent) + '</div>';
                            html += '<div class="flex gap-sm mb-sm">';
                            html += '<button class="btn btn-primary btn-sm" data-action="acceptDiff" data-key="' + s.key + '">Accept Changes</button>';
                            html += '<button class="btn btn-secondary btn-sm" data-action="rejectDiff" data-key="' + s.key + '">Reject</button>';
                            html += '</div>';
                        } else if (isAccepted) {
                            html += '<div class="section-content">' + fmtMd(s.content) + '</div>';
                        } else {
                            // Editable textarea
                            html += '<textarea class="section-textarea" data-key="' + s.key + '" rows="' + Math.max(2, Math.min(10, (s.content||'').split('\\n').length + 1)) + '">' + esc(s.content) + '</textarea>';
                        }

                        // Section actions
                        if (!_readOnly && !hasDiff && s.content) {
                            html += '<div class="flex gap-sm mt-sm flex-wrap">';
                            if (!isAccepted) {
                                html += '<button class="btn btn-primary btn-sm" data-action="acceptSection" data-key="' + s.key + '">Accept</button>';
                                html += '<button class="btn btn-secondary btn-sm" data-action="clearSection" data-key="' + s.key + '">Clear</button>';
                            }
                            html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-key="' + s.key + '">Chat</button>';
                            html += '</div>';
                        }
                    }

                    // Socratic probe
                    if (s.socraticProbe && !s.probeCollapsed && !s.probeAnswered) {
                        html += '<div class="companion-panel mt-sm">';
                        html += '<div class="companion-header flex items-center justify-between">';
                        html += '<span class="text-sm"><strong>Socratic Probe</strong> <span class="text-xs text-muted">(' + esc(s.socraticProbe.style) + ' &middot; turn ' + s.socraticProbe.turn + '/' + s.socraticProbe.max_turns + ')</span></span>';
                        html += '<button class="btn btn-sm btn-secondary" data-action="toggleProbe" data-key="' + s.key + '" style="padding:0 6px;font-size:.75em">Collapse</button>';
                        html += '</div>';
                        // Transparency badges
                        if (s.socraticProbe.transparency_events && s.socraticProbe.transparency_events.length > 0) {
                            html += '<div class="flex gap-xs mt-xs flex-wrap">';
                            for (var te of s.socraticProbe.transparency_events) {
                                html += '<span style="font-size:.7em;padding:1px 6px;border-radius:3px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(te) + '</span>';
                            }
                            html += '</div>';
                        }
                        // KB facts accordion
                        if (s.socraticProbe.kb_facts && s.socraticProbe.kb_facts.trim() !== '') {
                            html += '<details class="mt-xs" style="font-size:.82em">';
                            html += '<summary style="cursor:pointer;color:var(--vscode-textLink-foreground);user-select:none">From the Knowledge Base</summary>';
                            html += '<div style="margin-top:4px;padding:6px 8px;border-left:2px solid var(--vscode-textLink-foreground);background:var(--vscode-editor-background);white-space:pre-wrap;word-break:break-word">' + esc(s.socraticProbe.kb_facts.trim()) + '</div>';
                            html += '</details>';
                        }
                        html += '<div class="companion-body mt-xs">' + esc(s.socraticProbe.question) + '</div>';
                        html += '<div class="flex gap-sm mt-sm">';
                        html += '<input class="probe-input" id="probe-input-' + s.key + '" placeholder="Your response…" style="flex:1" />';
                        html += '<button class="btn btn-primary btn-sm" data-action="respondToProbe" data-key="' + s.key + '">Respond</button>';
                        html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-key="' + s.key + '">Chat</button>';
                        html += '</div>';
                        html += '</div>';
                    } else if (s.socraticProbe && s.probeAnswered) {
                        html += '<div class="companion-panel mt-sm" style="opacity:.6">';
                        html += '<div class="companion-header flex items-center gap-sm">';
                        html += '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>';
                        html += '<span class="text-sm"><strong>Probe Answered</strong></span>';
                        html += '</div>';
                        html += '<div class="companion-body text-sm text-muted">' + esc(s.socraticProbe.question) + '</div>';
                        html += '</div>';
                    } else if (s.socraticProbe && s.probeCollapsed) {
                        html += '<div class="mt-xs">';
                        html += '<button class="btn btn-sm btn-secondary text-xs" data-action="toggleProbe" data-key="' + s.key + '">Show Probe</button>';
                        html += '</div>';
                    } else if (s.needsSocratic && s.socraticReason && !s.socraticProbe) {
                        html += '<div class="companion-panel mt-sm">';
                        html += '<div class="companion-body text-sm text-muted"><em>Flagged:</em> ' + esc(s.socraticReason) + '</div>';
                        html += '<div class="flex gap-sm mt-xs">';
                        html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-key="' + s.key + '">Chat about this</button>';
                        html += '</div>';
                        html += '</div>';
                    }

                    html += '</div>'; // section-body
                    html += '</div>'; // section-panel
                }

                // Preserve focus and input state before DOM replacement
                var _focusId = '';
                var _focusVal = '';
                var _focusSel = [0, 0];
                var activeEl = document.activeElement;
                if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) {
                    _focusId = activeEl.id || activeEl.getAttribute('data-key') || '';
                    _focusVal = activeEl.value || '';
                    _focusSel = [activeEl.selectionStart || 0, activeEl.selectionEnd || 0];
                }

                container.innerHTML = html;

                // Restore focus and input value
                if (_focusId) {
                    var restoreEl = document.getElementById(_focusId) || container.querySelector('[data-key="' + _focusId + '"]');
                    if (restoreEl) {
                        restoreEl.value = _focusVal;
                        restoreEl.focus();
                        if (restoreEl.setSelectionRange) {
                            restoreEl.setSelectionRange(_focusSel[0], _focusSel[1]);
                        }
                    }
                }
                if (genIndicator) {
                    if (_isGenerating) { genIndicator.classList.remove('hidden'); }
                    else { genIndicator.classList.add('hidden'); }
                }

                // Edit button visibility — show when in read-only mode or all done
                var editToggle = document.getElementById('edit-toggle');
                var regenBtn = document.getElementById('regenerate-btn');
                if (editToggle) {
                    if (_readOnly || (!_isGenerating && allDone)) { editToggle.classList.remove('hidden'); }
                    else { editToggle.classList.add('hidden'); }
                    editToggle.textContent = _readOnly ? 'Edit' : 'Done Editing';
                }
                if (regenBtn) {
                    if (_readOnly || _isGenerating) regenBtn.classList.add('hidden');
                    else regenBtn.classList.remove('hidden');
                }

                // Focus mode UI
                var focusNav = document.getElementById('focus-nav');
                var focusLabel = document.getElementById('focus-label');
                var focusToggle = document.getElementById('focus-toggle');
                if (focusNav) {
                    if (_focusMode) { focusNav.classList.remove('hidden'); }
                    else { focusNav.classList.add('hidden'); }
                }
                if (focusToggle) focusToggle.textContent = _focusMode ? 'Show All' : 'Focus';
                if (focusLabel && _sections[_focusIndex]) {
                    focusLabel.textContent = (_focusIndex + 1) + ' / ' + _sections.length + ' — ' + (_sections[_focusIndex].heading || '');
                }
                var focusPrev = document.getElementById('focus-prev');
                var focusNext = document.getElementById('focus-next');
                if (focusPrev) focusPrev.disabled = _focusIndex <= 0;
                if (focusNext) focusNext.disabled = _focusIndex >= _sections.length - 1;

                // Show save bar when all sections have content and we're not generating
                var hasContent = _sections.filter(function(s) { return s.content; }).length;
                if (saveBar) {
                    if (!_isGenerating && hasContent > 0 && !_readOnly) { saveBar.classList.remove('hidden'); }
                    else { saveBar.classList.add('hidden'); }
                }
            }

            // Click delegation
            document.addEventListener('click', function(e) {
                var el;
                // Trace link navigation
                if ((el = e.target.closest('[data-artifact-id]'))) {
                    vscode.postMessage({ command: 'openArtifact', artifactId: el.dataset.artifactId });
                    return;
                }
                if ((el = e.target.closest('[data-action="acceptSection"]'))) {
                    var ta = document.querySelector('textarea[data-key="' + el.dataset.key + '"]');
                    if (ta) { vscode.postMessage({ command: 'editSection', sectionKey: el.dataset.key, content: ta.value }); }
                    vscode.postMessage({ command: 'acceptSection', sectionKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="clearSection"]'))) {
                    vscode.postMessage({ command: 'clearSection', sectionKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="acceptDiff"]'))) {
                    vscode.postMessage({ command: 'acceptDiff', sectionKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="rejectDiff"]'))) {
                    vscode.postMessage({ command: 'rejectDiff', sectionKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="respondToProbe"]'))) {
                    var input = document.getElementById('probe-input-' + el.dataset.key);
                    var answer = input ? input.value.trim() : '';
                    if (answer) { vscode.postMessage({ command: 'respondToProbe', sectionKey: el.dataset.key, answer: answer }); }
                    return;
                }
                if ((el = e.target.closest('[data-action="chatAboutSection"]'))) {
                    vscode.postMessage({ command: 'chatAboutSection', sectionKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="toggleProbe"]'))) {
                    vscode.postMessage({ command: 'toggleProbe', sectionKey: el.dataset.key });
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
                if ((el = e.target.closest('[data-action="toggleFocus"]'))) {
                    _focusMode = !_focusMode;
                    renderSections();
                    return;
                }
                if ((el = e.target.closest('[data-action="focusPrev"]'))) {
                    if (_focusIndex > 0) { _focusIndex--; renderSections(); }
                    return;
                }
                if ((el = e.target.closest('[data-action="focusNext"]'))) {
                    if (_focusIndex < _sections.length - 1) { _focusIndex++; renderSections(); }
                    return;
                }
            });

            // Save textarea edits on blur
            document.addEventListener('focusout', function(e) {
                if (e.target && e.target.classList && e.target.classList.contains('section-textarea')) {
                    vscode.postMessage({ command: 'editSection', sectionKey: e.target.dataset.key, content: e.target.value });
                }
            });

            // Enter in probe input = respond
            document.addEventListener('keydown', function(e) {
                if (e.target && e.target.classList && e.target.classList.contains('probe-input') && e.key === 'Enter') {
                    var key = e.target.id.replace('probe-input-', '');
                    var answer = e.target.value.trim();
                    if (answer) { vscode.postMessage({ command: 'respondToProbe', sectionKey: key, answer: answer }); }
                }
            });

            // Auto-resize textareas to fit content
            function autoResize(el) {
                el.style.height = 'auto';
                el.style.height = el.scrollHeight + 'px';
            }
            document.addEventListener('input', function(e) {
                if (e.target && (e.target.classList.contains('section-textarea') || e.target.classList.contains('auto-resize'))) {
                    autoResize(e.target);
                }
            });
            // Initial auto-resize after render
            var _origRenderSections = renderSections;
            renderSections = function() {
                _origRenderSections();
                var tas = document.querySelectorAll('.section-textarea');
                for (var t = 0; t < tas.length; t++) { autoResize(tas[t]); }
            };

            renderSections();
        `;
    }
}
