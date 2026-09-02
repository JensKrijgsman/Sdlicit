// ---------------------------------------------------------------------------
// Sdlicit — SRS Panel Provider (Requirement Cards with FR/NFR groups)
// ---------------------------------------------------------------------------
// WebviewPanel that renders SRS sections progressively after generation.
// Layout: Introduction / Scope (text sections),
//         Functional Requirements / Non-Functional Requirements (card groups).
// Per-requirement card: editable fields (ID, domain, statement, rationale,
//   acceptance criteria), KB verification badge.
// Socratic probes with Respond/Chat, KB facts accordion, transparency badges.
// WIP persistence via VS Code globalStorageUri.
// Chat integration: sends section context to chat panel via commands.
// Mirrors SOW panel UX for consistency.
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

export interface SRSRequirement {
    req_id: string;
    category: 'functional' | 'non_functional';
    domain: string;
    statement: string;
    rationale: string;
    acceptance_criteria: string;
    status: 'pending' | 'generating' | 'complete' | 'accepted' | 'editing';
    kb_grounded?: boolean;
    kb_ungrounded_claims?: string[];
}

export interface SRSTextSection {
    key: string;
    heading: string;
    content: string;
    originalContent: string;
    status: 'pending' | 'generating' | 'complete' | 'accepted' | 'editing';
}

export interface SRSSocraticState {
    probe?: SocraticProbe;
    probeAnswered: boolean;
    probeCollapsed: boolean;
    needsSocratic: boolean;
    socraticReason: string;
    /** Per-requirement-group probes (mirrors SOW per-section approach). */
    groupProbes?: Record<string, {
        probe?: SocraticProbe;
        probeAnswered: boolean;
        probeCollapsed: boolean;
        clarifications: Clarification[];
    }>;
}

// Backward compat for external access
export interface SRSSection {
    key: string;
    heading: string;
    content: string;
    originalContent: string;
    status: 'pending' | 'generating' | 'complete' | 'accepted' | 'editing';
    needsSocratic: boolean;
    socraticReason: string;
    socraticProbe?: SocraticProbe;
    probeAnswered: boolean;
    probeCollapsed: boolean;
    proposedContent?: string;
}

const TEXT_SECTION_KEYS = ['introduction', 'scope'] as const;
const TEXT_SECTION_HEADINGS: Record<string, string> = {
    introduction: 'Introduction',
    scope: 'Scope',
};

export class SRSPanelProvider {
    private panel: vscode.WebviewPanel | undefined;
    private textSections: SRSTextSection[] = [];
    private requirements: SRSRequirement[] = [];
    private socratic: SRSSocraticState = { probeAnswered: false, probeCollapsed: false, needsSocratic: false, socraticReason: '' };
    private isGenerating = false;
    private readOnly = false;
    private clarifications: Clarification[] = [];
    private sowContent = '';
    private artifactPath: string | undefined;
    private fileWatcher: vscode.Disposable | undefined;
    private resolvePromise?: (value: 'accepted' | 'declined') => void;
    private projectDir: string;

    constructor(
        private readonly client: SdlicitClient,
        private readonly store: ArtifactStore,
        private readonly kbSync?: KBSyncService,
        private readonly globalStoragePath?: string,
        private readonly dataService?: import('../services/dataService').DataService,
    ) {
        const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';
        this.projectDir = ws;
    }

    /** Get sections for external access (backward compat). */
    getSections(): SRSSection[] {
        const sections: SRSSection[] = [];
        for (const ts of this.textSections) {
            sections.push({
                key: ts.key,
                heading: ts.heading,
                content: ts.content,
                originalContent: ts.originalContent,
                status: ts.status,
                needsSocratic: false,
                socraticReason: '',
                probeAnswered: false,
                probeCollapsed: false,
            });
        }
        for (const cat of ['functional', 'non_functional'] as const) {
            const reqs = this.requirements.filter(r => r.category === cat);
            const content = reqs.map(r => `- [${r.req_id}] ${r.statement}`).join('\n');
            sections.push({
                key: cat === 'functional' ? 'functional_requirements' : 'non_functional_requirements',
                heading: cat === 'functional' ? 'Functional Requirements' : 'Non-Functional Requirements',
                content,
                originalContent: content,
                status: reqs.every(r => r.status === 'accepted') ? 'accepted' : 'complete',
                needsSocratic: false,
                socraticReason: '',
                probeAnswered: false,
                probeCollapsed: false,
            });
        }
        return sections;
    }

    /** Update a section's content from external source (e.g., chat "Insert"). */
    updateSectionFromExternal(sectionKey: string, newContent: string): void {
        const ts = this.textSections.find(s => s.key === sectionKey);
        if (ts) {
            ts.content = newContent;
            if (ts.status === 'accepted') { ts.status = 'editing'; }
            this.updatePanel();
            this.saveWip();
        }
    }

    /** Whether the panel is still open. */
    get isAlive(): boolean { return this.panel !== undefined; }

    /** Reveal (focus) the existing panel without creating a new one. */
    reveal(): void { this.panel?.reveal(vscode.ViewColumn.One); }

    /** Load an existing SRS markdown into the panel (read-only). */
    async openExisting(markdown: string, filePath?: string): Promise<void> {
        this.readOnly = true;
        this.isGenerating = false;
        this.artifactPath = filePath;
        this.parseResponseIntoState(markdown);

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.srsPanel',
            'Sdlicit — Software Requirements Specification',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );
        this.panel.onDidDispose(() => {
            this.panel = undefined;
            this.fileWatcher?.dispose();
            vscode.commands.executeCommand('sdlicit.unregisterPanel', 'srs');
        });

        if (this.artifactPath) {
            this.setupFileWatcher(this.artifactPath);
        }

        this.renderHtml();
        this.setupMessageHandler();
        this.updatePanel();
    }

    /** Start SRS generation from SOW content, showing the panel immediately. */
    async startGeneration(sowContent: string): Promise<'accepted' | 'declined'> {
        this.sowContent = sowContent;

        // Prompt user if WIP data exists
        let wip: { textSections: SRSTextSection[]; requirements: SRSRequirement[]; clarifications?: Clarification[]; socratic?: SRSSocraticState } | null = null;
        if (this.globalStoragePath) {
            const wipMgr = new WipManager(this.globalStoragePath);
            const decision = await wipMgr.promptIfWipExists('srs');
            if (decision === 'resume') {
                wip = this.loadWip();
            } else {
                this.deleteWip();
            }
        } else {
            wip = this.loadWip();
        }
        if (wip) {
            this.textSections = wip.textSections;
            this.requirements = wip.requirements;
            this.clarifications = wip.clarifications || [];
            this.socratic = wip.socratic || { probeAnswered: false, probeCollapsed: false, needsSocratic: false, socraticReason: '' };
        } else {
            this.textSections = TEXT_SECTION_KEYS.map(key => ({
                key,
                heading: TEXT_SECTION_HEADINGS[key],
                content: '',
                originalContent: '',
                status: 'pending' as const,
            }));
            this.requirements = [];
        }
        this.isGenerating = !wip;

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.srsPanel',
            'Sdlicit — Software Requirements Specification',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        this.panel.onDidDispose(() => {
            this.saveWip();
            this.panel = undefined;
            this.fileWatcher?.dispose();
            vscode.commands.executeCommand('sdlicit.unregisterPanel', 'srs');
            if (this.resolvePromise) {
                this.resolvePromise('declined');
                this.resolvePromise = undefined;
            }
        });

        this.renderHtml();
        this.setupMessageHandler();

        if (!wip) {
            this.client.log('SRS: Starting generation');
            this.updatePanel();
            try {
                await this.generateFromSOW(sowContent);
            } catch (err: any) {
                this.client.log(`SRS: Generation error — ${err.message}`);
                this.isGenerating = false;
                this.updatePanel();
                vscode.window.showErrorMessage(`Sdlicit: SRS generation failed — ${err.message}`);
                return 'declined';
            }
            this.isGenerating = false;
            this.updatePanel();
            this.saveWip();
            this.client.log('SRS: All sections generated — waiting for user review');
        } else {
            this.updatePanel();
            this.client.log('SRS: Restored from WIP');
        }

        return new Promise<'accepted' | 'declined'>((resolve) => {
            this.resolvePromise = resolve;
        });
    }

    /** Call the backend SRS endpoint and populate state from the response. */
    private async generateFromSOW(sowContent: string): Promise<void> {
        for (const s of this.textSections) { s.status = 'generating'; }
        this.updatePanel();

        const result: GenerationResponse = await this.client.generateSRS(
            sowContent, this.projectDir, this.clarifications,
        );

        // Handle socratic probe
        if (result.socratic_probe) {
            this.socratic.probe = result.socratic_probe;
            this.socratic.probeAnswered = false;
            this.socratic.probeCollapsed = false;
            this.socratic.needsSocratic = true;
            this.socratic.socraticReason = result.socratic_probe.question;
        }

        // Populate text sections
        const intro = (result as any).introduction as string | undefined;
        const scope = (result as any).scope as string | undefined;

        for (const ts of this.textSections) {
            if (ts.key === 'introduction' && intro) { ts.content = intro; ts.originalContent = intro; ts.status = 'complete'; }
            else if (ts.key === 'scope' && scope) { ts.content = scope; ts.originalContent = scope; ts.status = 'complete'; }
            else if (ts.status === 'generating') { ts.status = ts.content ? 'complete' : 'pending'; }
        }

        // Populate requirements
        const reqs = (result as any).requirements as any[] | undefined;
        if (reqs && Array.isArray(reqs)) {
            this.requirements = reqs.map((r: any) => ({
                req_id: r.req_id || '',
                category: r.category === 'non_functional' ? 'non_functional' as const : 'functional' as const,
                domain: r.domain || '',
                statement: r.statement || '',
                rationale: r.rationale || '',
                acceptance_criteria: r.acceptance_criteria || '',
                status: 'complete' as const,
                kb_grounded: r.kb_grounded ?? undefined,
                kb_ungrounded_claims: r.kb_ungrounded_claims || [],
            }));
        }

        // Fallback: parse from srs_markdown if no structured requirements
        if (this.requirements.length === 0 && result.srs_markdown) {
            this.parseSRSMarkdownFallback(result.srs_markdown as string);
        }
    }

    /** Fallback parser for SRS markdown into state. */
    private parseSRSMarkdownFallback(md: string): void {
        const lines = md.split('\n');
        let currentSection = '';
        let currentCategory: 'functional' | 'non_functional' = 'functional';
        let contentLines: string[] = [];
        let pendingReq: SRSRequirement | null = null;

        const flushText = () => {
            const content = contentLines.join('\n').trim();
            if (currentSection && content) {
                const ts = this.textSections.find(s => s.key === currentSection);
                if (ts) {
                    ts.content = content;
                    ts.originalContent = content;
                    ts.status = 'complete';
                }
            }
            contentLines = [];
        };

        const flushReq = () => {
            if (pendingReq) { this.requirements.push(pendingReq); }
            pendingReq = null;
        };

        for (const line of lines) {
            const h2 = line.match(/^##\s+(.+)/);
            if (h2) {
                flushText();
                flushReq();
                const heading = h2[1].trim().toLowerCase();
                if (heading === 'introduction') { currentSection = 'introduction'; }
                else if (heading === 'scope') { currentSection = 'scope'; }
                else if (heading.includes('functional') && !heading.includes('non')) { currentSection = 'fr'; currentCategory = 'functional'; }
                else if (heading.includes('non') && heading.includes('functional')) { currentSection = 'nfr'; currentCategory = 'non_functional'; }
                else { currentSection = ''; }
                continue;
            }

            if (currentSection === 'fr' || currentSection === 'nfr') {
                // Format: - [REQ-ID] statement
                const bracketMatch = line.match(/^-\s+\[([^\]]+)\]\s*(.+)/);
                // Format: - **REQ-ID** [domain] — statement
                const boldMatch = line.match(/^-\s+\*\*([^*]+)\*\*\s*(?:\[([^\]]*)\])?\s*[—–-]\s*(.+)/);
                // Sub-items: rationale or acceptance
                const rationaleMatch = line.match(/^\s+-\s+_Rationale:_\s*(.+)/);
                const acceptanceMatch = line.match(/^\s+-\s+_Acceptance:_\s*(.+)/);

                if (bracketMatch) {
                    flushReq();
                    pendingReq = {
                        req_id: bracketMatch[1].trim(),
                        category: currentCategory,
                        domain: '',
                        statement: bracketMatch[2].trim(),
                        rationale: '',
                        acceptance_criteria: '',
                        status: 'complete',
                    };
                } else if (boldMatch) {
                    flushReq();
                    pendingReq = {
                        req_id: boldMatch[1].trim(),
                        category: currentCategory,
                        domain: boldMatch[2]?.trim() || '',
                        statement: boldMatch[3].trim(),
                        rationale: '',
                        acceptance_criteria: '',
                        status: 'complete',
                    };
                } else if (rationaleMatch && pendingReq) {
                    pendingReq.rationale = rationaleMatch[1].trim();
                } else if (acceptanceMatch && pendingReq) {
                    pendingReq.acceptance_criteria = acceptanceMatch[1].trim();
                }
            } else if (currentSection) {
                contentLines.push(line);
            }
        }
        flushText();
        flushReq();
    }

    /** Parse markdown response into state (for openExisting). */
    private parseResponseIntoState(md: string): void {
        this.textSections = TEXT_SECTION_KEYS.map(key => ({
            key,
            heading: TEXT_SECTION_HEADINGS[key],
            content: '',
            originalContent: '',
            status: 'accepted' as const,
        }));
        this.requirements = [];
        this.parseSRSMarkdownFallback(md);
    }

    // -- WIP persistence -------------------------------------------------------

    private get wipPath(): string | undefined {
        if (!this.globalStoragePath) { return undefined; }
        return path.join(this.globalStoragePath, 'wip', 'wip_srs.json');
    }

    private saveWip(): void {
        if (this.readOnly) { return; }
        const wp = this.wipPath;
        if (!wp) { return; }
        const dir = path.dirname(wp);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        const data = {
            textSections: this.textSections,
            requirements: this.requirements,
            clarifications: this.clarifications,
            socratic: this.socratic,
            savedAt: new Date().toISOString(),
        };
        fs.writeFileSync(wp, JSON.stringify(data, null, 2), 'utf-8');
    }

    private loadWip(): { textSections: SRSTextSection[]; requirements: SRSRequirement[]; clarifications: Clarification[]; socratic: SRSSocraticState } | null {
        const wp = this.wipPath;
        if (!wp || !fs.existsSync(wp)) { return null; }
        try {
            const raw = JSON.parse(fs.readFileSync(wp, 'utf-8'));
            // Only restore if there's actual content (not just empty shells)
            const hasTextContent = raw.textSections?.some((s: any) => s.content?.trim());
            const hasReqs = raw.requirements?.length > 0;
            if (hasTextContent || hasReqs) { return raw; }
        } catch { /* ignore */ }
        return null;
    }

    private clearWip(): void {
        const wp = this.wipPath;
        if (wp && fs.existsSync(wp)) { fs.unlinkSync(wp); }
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
            this.parseResponseIntoState(md);
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

    // -- Panel messaging -------------------------------------------------------

    private updatePanel(): void {
        if (!this.panel) { return; }

        // Compute per-requirement trace links if DataService is available
        let reqTraces: Record<string, string[]> | undefined;
        if (this.dataService && this.requirements.length > 0) {
            reqTraces = {};
            for (const req of this.requirements) {
                const implementors = this.dataService.getImplementors(req.req_id);
                if (implementors.length > 0) {
                    reqTraces[req.req_id] = implementors.map(a => a.id);
                }
            }
        }

        this.panel.webview.postMessage({
            command: 'updateState',
            textSections: this.textSections,
            requirements: this.requirements,
            socratic: this.socratic,
            isGenerating: this.isGenerating,
            readOnly: this.readOnly,
            reqTraces: reqTraces,
        });
    }

    private setupMessageHandler(): void {
        if (!this.panel) { return; }

        this.panel.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.command) {
                case 'acceptTextSection': {
                    const ts = this.textSections.find(s => s.key === msg.sectionKey);
                    if (ts) { ts.status = 'accepted'; this.updatePanel(); this.saveWip(); }
                    break;
                }
                case 'editTextSection': {
                    const ts = this.textSections.find(s => s.key === msg.sectionKey);
                    if (ts) {
                        ts.content = msg.content;
                        if (ts.status === 'accepted') { ts.status = 'editing'; }
                        this.saveWip();
                        this.writeArtifactFile();
                    }
                    break;
                }
                case 'clearTextSection': {
                    const ts = this.textSections.find(s => s.key === msg.sectionKey);
                    if (ts) { ts.content = ''; ts.status = 'editing'; this.updatePanel(); this.saveWip(); }
                    break;
                }
                case 'acceptReq': {
                    const idx = msg.reqIndex as number;
                    if (idx >= 0 && idx < this.requirements.length) {
                        this.requirements[idx].status = 'accepted';
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'editReq': {
                    const idx = msg.reqIndex as number;
                    if (idx >= 0 && idx < this.requirements.length) {
                        const r = this.requirements[idx];
                        if (msg.field === 'statement') { r.statement = msg.value; }
                        else if (msg.field === 'rationale') { r.rationale = msg.value; }
                        else if (msg.field === 'acceptance_criteria') { r.acceptance_criteria = msg.value; }
                        else if (msg.field === 'req_id') { r.req_id = msg.value; }
                        else if (msg.field === 'domain') { r.domain = msg.value; }
                        if (r.status === 'accepted') { r.status = 'editing'; }
                        this.saveWip();
                    }
                    break;
                }
                case 'deleteReq': {
                    const idx = msg.reqIndex as number;
                    if (idx >= 0 && idx < this.requirements.length) {
                        this.requirements.splice(idx, 1);
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'addReq': {
                    const cat = msg.category === 'non_functional' ? 'non_functional' : 'functional';
                    this.requirements.push({
                        req_id: `REQ-NEW-${this.requirements.length + 1}`,
                        category: cat as any,
                        domain: '',
                        statement: '',
                        rationale: '',
                        acceptance_criteria: '',
                        status: 'editing',
                    });
                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'acceptAllCategory': {
                    const cat = msg.category;
                    for (const r of this.requirements) {
                        if (r.category === cat) { r.status = 'accepted'; }
                    }
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
                        const sowContent = this.store.getLatestSOW();
                        if (sowContent) {
                            const result = await this.client.generateSRS(
                                sowContent, this.projectDir, this.clarifications,
                            );

                            const intro = (result as any).introduction as string | undefined;
                            const scope = (result as any).scope as string | undefined;
                            if (intro) { const ts = this.textSections.find(s => s.key === 'introduction'); if (ts && ts.status !== 'accepted') { ts.content = intro; ts.originalContent = intro; ts.status = 'complete'; } }
                            if (scope) { const ts = this.textSections.find(s => s.key === 'scope'); if (ts && ts.status !== 'accepted') { ts.content = scope; ts.originalContent = scope; ts.status = 'complete'; } }

                            const reqs = (result as any).requirements as any[] | undefined;
                            if (reqs && Array.isArray(reqs) && reqs.length > 0) {
                                const accepted = this.requirements.filter(r => r.status === 'accepted');
                                const newReqs: SRSRequirement[] = reqs.map((r: any) => ({
                                    req_id: r.req_id || '',
                                    category: r.category === 'non_functional' ? 'non_functional' as const : 'functional' as const,
                                    domain: r.domain || '',
                                    statement: r.statement || '',
                                    rationale: r.rationale || '',
                                    acceptance_criteria: r.acceptance_criteria || '',
                                    status: 'complete' as const,
                                    kb_grounded: r.kb_grounded ?? undefined,
                                    kb_ungrounded_claims: r.kb_ungrounded_claims || [],
                                }));
                                const acceptedIds = new Set(accepted.map(r => r.req_id));
                                this.requirements = [
                                    ...accepted,
                                    ...newReqs.filter(r => !acceptedIds.has(r.req_id)),
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
                        vscode.window.showErrorMessage(`SRS regeneration failed: ${err.message}`);
                    }

                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'chatAboutSection': {
                    const sectionKey = msg.sectionKey as string;
                    let context = '';
                    if (sectionKey === 'functional_requirements' || sectionKey === 'non_functional_requirements') {
                        const cat = sectionKey === 'functional_requirements' ? 'functional' : 'non_functional';
                        const reqs = this.requirements.filter(r => r.category === cat);
                        context = `**SRS: ${sectionKey === 'functional_requirements' ? 'Functional' : 'Non-Functional'} Requirements**\n\n` +
                            reqs.map(r => `- ${r.req_id}: ${r.statement}`).join('\n');
                    } else {
                        const ts = this.textSections.find(s => s.key === sectionKey);
                        context = ts ? `**SRS Section: ${ts.heading}**\n\n${ts.content || '(empty)'}` : '';
                    }

                    const probeQ = this.socratic.probe?.question ?? this.socratic.socraticReason ?? '';
                    if (probeQ) { context += `\n\nSocratic probe: ${probeQ}`; }

                    vscode.commands.executeCommand('sdlicit.chatWithContext', {
                        panelId: 'srs',
                        panelType: 'srs',
                        sectionKey,
                        sectionHeading: sectionKey,
                        context,
                    });
                    break;
                }
                case 'toggleProbe': {
                    this.socratic.probeCollapsed = !this.socratic.probeCollapsed;
                    this.updatePanel();
                    break;
                }
                case 'toggleGroupProbe': {
                    const group = msg.group as string;
                    if (!this.socratic.groupProbes?.[group]) { break; }
                    this.socratic.groupProbes[group].probeCollapsed = !this.socratic.groupProbes[group].probeCollapsed;
                    this.updatePanel();
                    break;
                }
                case 'requestGroupProbe': {
                    const group = msg.group as string;
                    const catLabel = group === 'functional' ? 'Functional Requirements' : 'Non-Functional Requirements';
                    const catReqs = this.requirements.filter(r => r.category === group);
                    if (catReqs.length === 0) { break; }

                    const reqSummary = catReqs.map(r => `${r.req_id}: ${r.statement}`).join('\n');

                    this.panel?.webview.postMessage({ command: 'groupProbeLoading', group });

                    try {
                        const result = await this.client.consultSocratic(
                            'RequirementAgent',
                            `Review the ${catLabel} for completeness, consistency, and testability`,
                            `SOW context: ${this.sowContent?.slice(0, 500) || '(not available)'}\n\nCurrent ${catLabel}:\n${reqSummary}`,
                            reqSummary,
                            'quality_review',
                            this.socratic.groupProbes?.[group]?.clarifications || [],
                        );

                        if (result.status === 'probe' && result.probe) {
                            if (!this.socratic.groupProbes) { this.socratic.groupProbes = {}; }
                            this.socratic.groupProbes[group] = {
                                probe: result.probe,
                                probeAnswered: false,
                                probeCollapsed: false,
                                clarifications: this.socratic.groupProbes[group]?.clarifications || [],
                            };
                        }
                    } catch (err: any) {
                        vscode.window.showErrorMessage(`Socratic probe failed: ${err.message}`);
                    }

                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'respondToGroupProbe': {
                    const group = msg.group as string;
                    const answer = msg.answer as string;
                    if (!answer || !this.socratic.groupProbes?.[group]?.probe) { break; }

                    const gp = this.socratic.groupProbes[group];
                    gp.probeAnswered = true;
                    gp.clarifications.push({
                        question: gp.probe!.question,
                        answer,
                    });

                    // Also add to global clarifications for next regeneration
                    this.clarifications.push({
                        question: gp.probe!.question,
                        answer,
                    });

                    this.panel?.webview.postMessage({ command: 'groupProbeLoading', group });

                    try {
                        // Regenerate SRS with updated clarifications
                        const sowContent = this.sowContent || this.store.getLatestSOW() || '';
                        if (sowContent) {
                            const result = await this.client.generateSRS(
                                sowContent, this.projectDir, this.clarifications,
                            );

                            const intro = (result as any).introduction as string | undefined;
                            const scope = (result as any).scope as string | undefined;
                            if (intro) { const ts = this.textSections.find(s => s.key === 'introduction'); if (ts && ts.status !== 'accepted') { ts.content = intro; ts.originalContent = intro; ts.status = 'complete'; } }
                            if (scope) { const ts = this.textSections.find(s => s.key === 'scope'); if (ts && ts.status !== 'accepted') { ts.content = scope; ts.originalContent = scope; ts.status = 'complete'; } }

                            const reqs = (result as any).requirements as any[] | undefined;
                            if (reqs && Array.isArray(reqs) && reqs.length > 0) {
                                const accepted = this.requirements.filter(r => r.status === 'accepted');
                                const newReqs: SRSRequirement[] = reqs.map((r: any) => ({
                                    req_id: r.req_id || '',
                                    category: r.category === 'non_functional' ? 'non_functional' as const : 'functional' as const,
                                    domain: r.domain || '',
                                    statement: r.statement || '',
                                    rationale: r.rationale || '',
                                    acceptance_criteria: r.acceptance_criteria || '',
                                    status: 'complete' as const,
                                    kb_grounded: r.kb_grounded ?? undefined,
                                    kb_ungrounded_claims: r.kb_ungrounded_claims || [],
                                }));
                                const acceptedIds = new Set(accepted.map(r => r.req_id));
                                this.requirements = [
                                    ...accepted,
                                    ...newReqs.filter(r => !acceptedIds.has(r.req_id)),
                                ];
                            }

                            // Check for new global probe
                            if (result.socratic_probe) {
                                this.socratic.probe = result.socratic_probe;
                                this.socratic.probeAnswered = false;
                                this.socratic.probeCollapsed = false;
                            }

                            // Request follow-up group probe
                            const catLabel = group === 'functional' ? 'Functional Requirements' : 'Non-Functional Requirements';
                            const catReqs = this.requirements.filter(r => r.category === group);
                            const reqSummary = catReqs.map(r => `${r.req_id}: ${r.statement}`).join('\n');

                            const followUp = await this.client.consultSocratic(
                                'RequirementAgent',
                                `Review the ${catLabel} for completeness, consistency, and testability`,
                                `SOW context: ${this.sowContent?.slice(0, 500) || ''}\n\nCurrent ${catLabel}:\n${reqSummary}`,
                                reqSummary,
                                'quality_review',
                                gp.clarifications,
                            );

                            if (followUp.status === 'probe' && followUp.probe) {
                                gp.probe = followUp.probe;
                                gp.probeAnswered = false;
                                gp.probeCollapsed = false;
                            } else {
                                gp.probe = undefined;
                                gp.probeAnswered = false;
                            }
                        }
                    } catch (err: any) {
                        vscode.window.showErrorMessage(`SRS regeneration failed: ${err.message}`);
                    }

                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'saveAndIngest': {
                    const allTextDone = this.textSections.every(s => s.status === 'accepted' || s.status === 'complete');
                    const allReqsDone = this.requirements.every(r => r.status === 'accepted' || r.status === 'complete');
                    if (!allTextDone || !allReqsDone) {
                        vscode.window.showWarningMessage('Sdlicit: Accept or fill all sections and requirements first.');
                        break;
                    }

                    // Build structured data for the backend
                    const introSection = this.textSections.find(s => s.key === 'introduction');
                    const scopeSection = this.textSections.find(s => s.key === 'scope');
                    const srsData: Record<string, unknown> = {
                        artifact_type: 'srs',
                        introduction: introSection?.content || '',
                        scope: scopeSection?.content || '',
                        requirements: this.requirements.map(r => ({
                            req_id: r.req_id,
                            category: r.category === 'non_functional' ? 'non-functional' : 'functional',
                            statement: r.statement,
                            rationale: r.rationale,
                            acceptance: r.acceptance_criteria,
                        })),
                    };

                    let artifactFilePath: string;
                    let fullMd: string;
                    try {
                        const saveResult = await this.client.saveArtifact('srs', srsData, this.projectDir);
                        artifactFilePath = saveResult.markdown_path || saveResult.json_path;
                        const mdResult = await this.client.renderArtifactMarkdown('srs', this.projectDir);
                        fullMd = mdResult.markdown;
                    } catch {
                        // Fallback to local save
                        fullMd = this.buildFullMarkdown();
                        artifactFilePath = this.store.saveByMeta(
                            { tag: 'SRS', filename: 'srs.md', relative_path: 'srs.md', artifact_type: 'srs' },
                            fullMd,
                        );
                    }

                    this.artifactPath = artifactFilePath;
                    const artifactId = 'srs';

                    vscode.commands.executeCommand('sdlicit.markArtifactIngesting', artifactId);
                    this.deleteWip();
                    this.panel?.dispose();
                    if (this.resolvePromise) {
                        this.resolvePromise('accepted');
                        this.resolvePromise = undefined;
                    }

                    (async () => {
                        try {
                            await this.client.ingestArtifact(fullMd, 'srs', 'srs');
                            vscode.commands.executeCommand('sdlicit.markArtifactIngested', artifactId);
                            vscode.window.showInformationMessage('Sdlicit: SRS saved and ingested into KB → srs.json');
                        } catch {
                            vscode.commands.executeCommand('sdlicit.markArtifactIngestError', artifactId);
                            vscode.window.showWarningMessage('Sdlicit: SRS saved but KB ingestion failed.');
                        }
                    })();
                    break;
                }
                case 'toggleEdit': {
                    this.readOnly = !this.readOnly;
                    if (!this.readOnly) {
                        for (const s of this.textSections) {
                            if (s.status === 'accepted') { s.status = 'complete'; }
                        }
                        for (const r of this.requirements) {
                            if (r.status === 'accepted') { r.status = 'complete'; }
                        }
                    }
                    this.updatePanel();
                    break;
                }
                case 'regenerate': {
                    this.clearWip();
                    this.textSections = TEXT_SECTION_KEYS.map(key => ({
                        key,
                        heading: TEXT_SECTION_HEADINGS[key],
                        content: '',
                        originalContent: '',
                        status: 'pending' as const,
                    }));
                    this.requirements = [];
                    this.clarifications = [];
                    this.socratic = { probeAnswered: false, probeCollapsed: false, needsSocratic: false, socraticReason: '' };
                    this.isGenerating = true;
                    this.updatePanel();
                    const sow = this.sowContent || this.store.getLatestSOW() || '';
                    if (!sow) {
                        vscode.window.showWarningMessage('Sdlicit: No SOW content available for regeneration');
                        this.isGenerating = false;
                        this.updatePanel();
                        break;
                    }
                    this.generateFromSOW(sow).then(() => {
                        this.isGenerating = false;
                        this.updatePanel();
                        this.saveWip();
                    }).catch((err: any) => {
                        this.isGenerating = false;
                        this.updatePanel();
                        vscode.window.showErrorMessage(`Sdlicit: SRS regeneration failed — ${err.message}`);
                    });
                    break;
                }
                case 'openMarkdown': {
                    const fullMd = this.buildFullMarkdown();
                    if (!fullMd) { break; }
                    if (!this.artifactPath) {
                        this.artifactPath = this.store.saveByMeta(
                            { tag: 'SRS', filename: 'srs.md', relative_path: 'srs.md', artifact_type: 'srs' },
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
                case 'openArtifact': {
                    if (msg.artifactId) {
                        vscode.commands.executeCommand('sdlicit.openCanvas', msg.artifactId);
                    }
                    break;
                }
            }
        });
    }

    private buildFullMarkdown(): string {
        const lines: string[] = ['# Software Requirements Specification', ''];

        // Include text sections (Introduction, Scope, etc.)
        for (const ts of this.textSections) {
            if (ts.content.trim()) {
                lines.push(`## ${ts.heading}`, '', ts.content.trim(), '');
            }
        }

        const fr = this.requirements.filter(r => r.category === 'functional');
        const nfr = this.requirements.filter(r => r.category !== 'functional');

        if (fr.length > 0) {
            lines.push('## Functional Requirements', '');
            for (const r of fr) {
                lines.push(`- [${r.req_id}] ${r.statement}`);
                if (r.rationale) { lines.push(`  - _Rationale:_ ${r.rationale}`); }
                if (r.acceptance_criteria) { lines.push(`  - _Acceptance:_ ${r.acceptance_criteria}`); }
            }
            lines.push('');
        }

        if (nfr.length > 0) {
            lines.push('## Non-Functional Requirements', '');
            for (const r of nfr) {
                lines.push(`- [${r.req_id}] ${r.statement}`);
                if (r.rationale) { lines.push(`  - _Rationale:_ ${r.rationale}`); }
                if (r.acceptance_criteria) { lines.push(`  - _Acceptance:_ ${r.acceptance_criteria}`); }
            }
            lines.push('');
        }

        return lines.join('\n');
    }

    // -- HTML rendering --------------------------------------------------------

    private renderHtml(): void {
        if (!this.panel) { return; }
        const nonce = getNonce();

        const body = `
            <div id="srs-root">
                <div class="flex items-center justify-between mb-md">
                    <h1 style="margin-bottom:0">Software Requirements Specification</h1>
                    <div class="flex gap-xs">
                        <button class="btn btn-secondary btn-sm" data-action="regenerate" id="regenerate-btn" title="Clear cache and regenerate from SOW">Regenerate</button>
                        <button class="btn btn-primary btn-sm hidden" data-action="toggleEdit" id="edit-toggle">Edit</button>
                        <button class="btn btn-secondary btn-sm" data-action="openMarkdown">Markdown</button>
                    </div>
                </div>
                <div id="text-sections-container"></div>
                <div id="socratic-container"></div>
                <div id="fr-container"></div>
                <div id="nfr-container"></div>
                <div id="save-bar" class="mt-lg hidden">
                    <button class="btn btn-primary" data-action="saveAndIngest">Save &amp; Ingest to KB</button>
                </div>
                <div id="generating-indicator" class="mt-md hidden">
                    <span class="spinner"></span>
                    <span class="text-sm">Generating SRS…</span>
                </div>
            </div>
        `;

        const scripts = this.buildScripts();
        this.panel.webview.html = wrapHtml(body, nonce, scripts);
    }

    private buildScripts(): string {
        return `
            var _textSections = [];
            var _requirements = [];
            var _socratic = {};
            var _isGenerating = false;
            var _readOnly = false;
            var _probeLoading = false;
            var _groupProbeLoading = {};
            var _reqTraces = {};

            window.addEventListener('message', function(event) {
                var msg = event.data;
                if (msg.command === 'updateState') {
                    _textSections = msg.textSections;
                    _requirements = msg.requirements;
                    _socratic = msg.socratic;
                    _isGenerating = msg.isGenerating;
                    _readOnly = msg.readOnly || false;
                    _reqTraces = msg.reqTraces || {};
                    _probeLoading = false;
                    _groupProbeLoading = {};
                    render();
                } else if (msg.command === 'probeLoading') {
                    _probeLoading = true;
                    render();
                } else if (msg.command === 'groupProbeLoading') {
                    _groupProbeLoading[msg.group] = true;
                    render();
                }
            });

            function esc(s) {
                var d = document.createElement('div');
                d.textContent = s || '';
                return d.innerHTML;
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
                    } else if (/^\\d+[.)]\s/.test(t)) {
                        if (inUl) { html += '</ul>'; inUl = false; }
                        if (!inOl) { html += '<ol>'; inOl = true; }
                        html += '<li>' + fmtInline(t.replace(/^\\d+[.)]\s/, '')) + '</li>';
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

            function renderTextSections() {
                var container = document.getElementById('text-sections-container');
                var html = '';
                for (var i = 0; i < _textSections.length; i++) {
                    var s = _textSections[i];
                    var isAccepted = s.status === 'accepted';
                    var statusIcon = isAccepted
                        ? '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>'
                        : s.status === 'generating'
                        ? '<span class="spinner" style="width:12px;height:12px"></span>'
                        : s.status === 'pending'
                        ? '<span style="opacity:.4">&#x25CB;</span>'
                        : '<span style="color:var(--vscode-charts-blue)">&#x25CF;</span>';

                    html += '<div class="section-panel' + (isAccepted ? '' : ' section-active') + '" data-key="' + s.key + '">';
                    html += '<div class="section-header"><div class="flex items-center gap-sm">' + statusIcon + '<strong>' + esc(s.heading) + '</strong></div></div>';
                    html += '<div class="section-body">';

                    if (s.status === 'generating' || (s.status === 'pending' && _isGenerating)) {
                        html += '<div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Generating…</span></div>';
                    } else if (_readOnly || isAccepted) {
                        html += '<div class="section-content">' + fmtMd(s.content) + '</div>';
                    } else {
                        html += '<textarea class="section-textarea" data-section-key="' + s.key + '" rows="' + Math.max(3, Math.min(8, (s.content||'').split('\\n').length + 1)) + '">' + esc(s.content) + '</textarea>';
                    }

                    if (!_readOnly && s.content && !isAccepted) {
                        html += '<div class="flex gap-sm mt-sm">';
                        html += '<button class="btn btn-primary btn-sm" data-action="acceptTextSection" data-key="' + s.key + '">Accept</button>';
                        html += '<button class="btn btn-secondary btn-sm" data-action="clearTextSection" data-key="' + s.key + '">Clear</button>';
                        html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-key="' + s.key + '">Chat</button>';
                        html += '</div>';
                    } else if (!_readOnly && isAccepted) {
                        html += '<div class="flex gap-sm mt-sm">';
                        html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-key="' + s.key + '">Chat</button>';
                        html += '</div>';
                    }

                    html += '</div></div>';
                }
                container.innerHTML = html;
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
                    html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-key="functional_requirements">Chat</button>';
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
                    html += '<div class="flex gap-sm mt-xs"><button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-key="functional_requirements">Chat about this</button></div>';
                    html += '</div>';
                }

                container.innerHTML = html;
            }

            function renderReqGroup(containerId, title, category) {
                var container = document.getElementById(containerId);
                var reqs = _requirements.filter(function(r) { return r.category === category; });
                var html = '';

                var allAccepted = reqs.length > 0 && reqs.every(function(r) { return r.status === 'accepted'; });
                var groupStatusIcon = allAccepted
                    ? '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>'
                    : reqs.length === 0
                    ? '<span style="opacity:.4">&#x25CB;</span>'
                    : '<span style="color:var(--vscode-charts-blue)">&#x25CF;</span>';

                html += '<div class="section-panel' + (allAccepted ? '' : ' section-active') + '">';
                html += '<div class="section-header"><div class="flex items-center justify-between" style="width:100%">';
                html += '<div class="flex items-center gap-sm">' + groupStatusIcon + '<strong>' + esc(title) + '</strong> <span class="text-xs text-muted">(' + reqs.length + ')</span></div>';
                if (!_readOnly && reqs.length > 0 && !allAccepted) {
                    html += '<button class="btn btn-sm btn-primary" data-action="acceptAllCategory" data-category="' + category + '">Accept All</button>';
                }
                html += '</div></div>';
                html += '<div class="section-body">';

                if (_isGenerating && reqs.length === 0) {
                    html += '<div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Generating…</span></div>';
                }

                for (var i = 0; i < _requirements.length; i++) {
                    var r = _requirements[i];
                    if (r.category !== category) continue;

                    var isAccepted = r.status === 'accepted';

                    var kbBadge = '';
                    if (r.kb_grounded === true) {
                        kbBadge = '<span class="text-xs" style="color:var(--vscode-testing-iconPassed)" title="Grounded in KB">KB &#x2714;</span>';
                    } else if (r.kb_grounded === false) {
                        kbBadge = '<span class="text-xs" style="color:var(--vscode-errorForeground)" title="' + esc((r.kb_ungrounded_claims||[]).join(', ')) + '">KB &#x26A0;</span>';
                    }

                    html += '<div class="req-card' + (isAccepted ? ' req-accepted' : '') + '" style="border:1px solid var(--vscode-panel-border);border-left:4px solid ' + (category === 'functional' ? 'var(--vscode-charts-blue)' : 'var(--vscode-charts-purple, #b180d7)') + ';border-radius:6px;padding:14px 18px;margin-bottom:20px;background:var(--vscode-editor-background);box-shadow:0 1px 3px rgba(0,0,0,.12)">';

                    html += '<div class="flex items-center justify-between" style="margin-bottom:8px">';
                    html += '<div class="flex items-center gap-sm" style="flex-wrap:wrap;gap:8px">';
                    if (isAccepted) {
                        html += '<span style="color:var(--vscode-testing-iconPassed);font-size:1.1em">&#x2714;</span>';
                    }
                    if (_readOnly || isAccepted) {
                        html += '<strong style="font-size:1em">' + esc(r.req_id) + '</strong>';
                    } else {
                        html += '<input type="text" value="' + esc(r.req_id) + '" data-req-idx="' + i + '" data-field="req_id" class="req-field-input" style="width:120px;font-weight:bold" />';
                    }
                    if (r.domain) {
                        html += '<span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:.82em;font-weight:500;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground);line-height:1.4">';
                        if (_readOnly || isAccepted) {
                            html += esc(r.domain);
                        } else {
                            html += '<input type="text" value="' + esc(r.domain) + '" data-req-idx="' + i + '" data-field="domain" class="req-field-input" style="width:80px;font-size:.9em;background:transparent;border:none;color:inherit;padding:0;text-align:center" />';
                        }
                        html += '</span>';
                    } else if (!_readOnly && !isAccepted) {
                        html += '<input type="text" value="" data-req-idx="' + i + '" data-field="domain" class="req-field-input" placeholder="domain" style="width:80px;font-size:.82em" />';
                    }
                    html += kbBadge;
                    html += '</div>';
                    if (!_readOnly && !isAccepted) {
                        html += '<div class="flex gap-xs">';
                        html += '<button class="btn btn-primary btn-sm" data-action="acceptReq" data-req-idx="' + i + '" style="padding:2px 10px;font-size:.8em">Accept</button>';
                        html += '<button class="btn btn-secondary btn-sm" data-action="deleteReq" data-req-idx="' + i + '" style="padding:2px 8px;font-size:.8em;color:var(--vscode-errorForeground)">&#x2715;</button>';
                        html += '</div>';
                    }
                    html += '</div>';

                    if (_readOnly || isAccepted) {
                        html += '<div style="margin-bottom:8px;line-height:1.5">' + esc(r.statement) + '</div>';
                    } else {
                        html += '<div style="margin-bottom:10px"><label class="text-xs text-muted">Statement</label>';
                        html += '<textarea class="req-field-textarea auto-resize" data-req-idx="' + i + '" data-field="statement" rows="2">' + esc(r.statement) + '</textarea></div>';
                    }

                    if (r.rationale || (!_readOnly && !isAccepted)) {
                        if (_readOnly || isAccepted) {
                            if (r.rationale) html += '<div class="text-xs text-muted" style="margin-bottom:6px"><em>Rationale:</em> ' + esc(r.rationale) + '</div>';
                        } else {
                            html += '<div style="margin-bottom:10px"><label class="text-xs text-muted">Rationale</label>';
                            html += '<textarea class="req-field-textarea auto-resize" data-req-idx="' + i + '" data-field="rationale" rows="2">' + esc(r.rationale) + '</textarea></div>';
                        }
                    }

                    if (r.acceptance_criteria || (!_readOnly && !isAccepted)) {
                        if (_readOnly || isAccepted) {
                            if (r.acceptance_criteria) html += '<div class="text-xs text-muted" style="margin-bottom:6px"><em>Acceptance:</em> ' + esc(r.acceptance_criteria) + '</div>';
                        } else {
                            html += '<div style="margin-bottom:10px"><label class="text-xs text-muted">Acceptance Criteria</label>';
                            html += '<textarea class="req-field-textarea auto-resize" data-req-idx="' + i + '" data-field="acceptance_criteria" rows="2">' + esc(r.acceptance_criteria) + '</textarea></div>';
                        }
                    }

                    // Show trace links (which artifacts implement this requirement)
                    if (_reqTraces && _reqTraces[r.req_id] && _reqTraces[r.req_id].length > 0) {
                        html += '<div class="trace-row" style="margin-top:6px"><span class="trace-label">Implemented by:</span> ';
                        for (var ti = 0; ti < _reqTraces[r.req_id].length; ti++) {
                            html += '<span class="trace-node clickable" data-artifact-id="' + esc(_reqTraces[r.req_id][ti]) + '" title="Click to navigate">' + esc(_reqTraces[r.req_id][ti]) + '</span> ';
                        }
                        html += '</div>';
                    }

                    html += '</div>';
                }

                if (!_readOnly) {
                    html += '<div class="mt-sm">';
                    html += '<button class="btn btn-secondary btn-sm" data-action="addReq" data-category="' + category + '">+ Add Requirement</button>';
                    if (reqs.length > 0) {
                        html += ' <button class="btn btn-secondary btn-sm" data-action="requestGroupProbe" data-group="' + category + '" title="Ask Socratic probe about this group">Probe</button>';
                    }
                    html += '</div>';
                }

                // Per-group Socratic probe (same component as SOW per-section probes)
                var gp = (_socratic.groupProbes || {})[category];
                var gpLoading = _groupProbeLoading[category];

                if (gpLoading) {
                    html += '<div class="companion-panel mt-sm"><div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Processing…</span></div></div>';
                } else if (gp && gp.probe && !gp.probeCollapsed && !gp.probeAnswered) {
                    html += '<div class="companion-panel mt-sm">';
                    html += '<div class="companion-header flex items-center justify-between">';
                    html += '<span class="text-sm"><strong>Socratic Probe</strong> <span class="text-xs text-muted">(' + esc(gp.probe.style) + ' &middot; turn ' + gp.probe.turn + '/' + gp.probe.max_turns + ')</span></span>';
                    html += '<button class="btn btn-sm btn-secondary" data-action="toggleGroupProbe" data-group="' + category + '" style="padding:0 6px;font-size:.75em">Collapse</button>';
                    html += '</div>';
                    if (gp.probe.transparency_events && gp.probe.transparency_events.length > 0) {
                        html += '<div class="flex gap-xs mt-xs flex-wrap">';
                        for (var te = 0; te < gp.probe.transparency_events.length; te++) {
                            html += '<span style="font-size:.7em;padding:1px 6px;border-radius:3px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(gp.probe.transparency_events[te]) + '</span>';
                        }
                        html += '</div>';
                    }
                    if (gp.probe.kb_facts && gp.probe.kb_facts.trim() !== '') {
                        html += '<details class="mt-xs" style="font-size:.82em">';
                        html += '<summary style="cursor:pointer;color:var(--vscode-textLink-foreground);user-select:none">From the Knowledge Base</summary>';
                        html += '<div style="margin-top:4px;padding:6px 8px;border-left:2px solid var(--vscode-textLink-foreground);background:var(--vscode-editor-background);white-space:pre-wrap;word-break:break-word">' + esc(gp.probe.kb_facts.trim()) + '</div>';
                        html += '</details>';
                    }
                    html += '<div class="companion-body mt-xs">' + esc(gp.probe.question) + '</div>';
                    html += '<div class="flex gap-sm mt-sm">';
                    html += '<input class="probe-input" id="probe-input-group-' + category + '" placeholder="Your response…" style="flex:1" />';
                    html += '<button class="btn btn-primary btn-sm" data-action="respondToGroupProbe" data-group="' + category + '">Respond</button>';
                    html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutSection" data-key="' + category + '_requirements">Chat</button>';
                    html += '</div>';
                    html += '</div>';
                } else if (gp && gp.probe && gp.probeAnswered) {
                    html += '<div class="companion-panel mt-sm" style="opacity:.6">';
                    html += '<div class="companion-header flex items-center gap-sm">';
                    html += '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>';
                    html += '<span class="text-sm"><strong>Probe Answered</strong></span>';
                    html += '</div>';
                    html += '<div class="companion-body text-sm text-muted">' + esc(gp.probe.question) + '</div>';
                    html += '</div>';
                } else if (gp && gp.probe && gp.probeCollapsed) {
                    html += '<div class="mt-xs"><button class="btn btn-sm btn-secondary text-xs" data-action="toggleGroupProbe" data-group="' + category + '">Show Probe</button></div>';
                }

                html += '</div></div>';
                container.innerHTML = html;
            }

            function render() {
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

                renderTextSections();
                renderSocratic();
                renderReqGroup('fr-container', 'Functional Requirements', 'functional');
                renderReqGroup('nfr-container', 'Non-Functional Requirements', 'non_functional');

                // Restore focus and input value
                if (_focusId) {
                    var restoreEl = document.getElementById(_focusId) || document.querySelector('[data-key="' + _focusId + '"]');
                    if (restoreEl) {
                        restoreEl.value = _focusVal;
                        restoreEl.focus();
                        if (restoreEl.setSelectionRange) {
                            restoreEl.setSelectionRange(_focusSel[0], _focusSel[1]);
                        }
                    }
                }

                var genIndicator = document.getElementById('generating-indicator');
                if (genIndicator) {
                    if (_isGenerating) genIndicator.classList.remove('hidden');
                    else genIndicator.classList.add('hidden');
                }

                var editToggle = document.getElementById('edit-toggle');
                var regenBtn = document.getElementById('regenerate-btn');
                var allTextDone = _textSections.every(function(s) { return s.status === 'accepted'; });
                var allReqsDone = _requirements.length > 0 && _requirements.every(function(r) { return r.status === 'accepted'; });
                if (editToggle) {
                    if (_readOnly || (!_isGenerating && allTextDone && allReqsDone)) editToggle.classList.remove('hidden');
                    else editToggle.classList.add('hidden');
                    editToggle.textContent = _readOnly ? 'Edit' : 'Done Editing';
                }
                if (regenBtn) {
                    if (_readOnly || _isGenerating) regenBtn.classList.add('hidden');
                    else regenBtn.classList.remove('hidden');
                }

                var saveBar = document.getElementById('save-bar');
                var hasContent = _requirements.length > 0 || _textSections.some(function(s) { return s.content; });
                if (saveBar) {
                    if (!_isGenerating && hasContent && !_readOnly) saveBar.classList.remove('hidden');
                    else saveBar.classList.add('hidden');
                }
            }

            document.addEventListener('click', function(e) {
                var el;
                if ((el = e.target.closest('[data-action="acceptTextSection"]'))) {
                    var ta = document.querySelector('textarea[data-section-key="' + el.dataset.key + '"]');
                    if (ta) vscode.postMessage({ command: 'editTextSection', sectionKey: el.dataset.key, content: ta.value });
                    vscode.postMessage({ command: 'acceptTextSection', sectionKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="clearTextSection"]'))) {
                    vscode.postMessage({ command: 'clearTextSection', sectionKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="acceptReq"]'))) {
                    vscode.postMessage({ command: 'acceptReq', reqIndex: parseInt(el.dataset.reqIdx) });
                    return;
                }
                if ((el = e.target.closest('[data-action="deleteReq"]'))) {
                    vscode.postMessage({ command: 'deleteReq', reqIndex: parseInt(el.dataset.reqIdx) });
                    return;
                }
                if ((el = e.target.closest('[data-action="addReq"]'))) {
                    vscode.postMessage({ command: 'addReq', category: el.dataset.category });
                    return;
                }
                if ((el = e.target.closest('[data-action="acceptAllCategory"]'))) {
                    vscode.postMessage({ command: 'acceptAllCategory', category: el.dataset.category });
                    return;
                }
                if ((el = e.target.closest('[data-action="respondToProbe"]'))) {
                    var input = document.getElementById('probe-input-main');
                    var answer = input ? input.value.trim() : '';
                    if (answer) vscode.postMessage({ command: 'respondToProbe', answer: answer });
                    return;
                }
                if ((el = e.target.closest('[data-action="chatAboutSection"]'))) {
                    vscode.postMessage({ command: 'chatAboutSection', sectionKey: el.dataset.key || 'functional_requirements' });
                    return;
                }
                if ((el = e.target.closest('[data-action="toggleProbe"]'))) {
                    vscode.postMessage({ command: 'toggleProbe' });
                    return;
                }
                if ((el = e.target.closest('[data-action="toggleGroupProbe"]'))) {
                    vscode.postMessage({ command: 'toggleGroupProbe', group: el.dataset.group });
                    return;
                }
                if ((el = e.target.closest('[data-action="requestGroupProbe"]'))) {
                    vscode.postMessage({ command: 'requestGroupProbe', group: el.dataset.group });
                    return;
                }
                if ((el = e.target.closest('[data-action="respondToGroupProbe"]'))) {
                    var input = document.getElementById('probe-input-group-' + el.dataset.group);
                    var answer = input ? input.value.trim() : '';
                    if (answer) vscode.postMessage({ command: 'respondToGroupProbe', group: el.dataset.group, answer: answer });
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
                if ((el = e.target.closest('[data-artifact-id]'))) {
                    vscode.postMessage({ command: 'openArtifact', artifactId: el.dataset.artifactId });
                    return;
                }
                if ((el = e.target.closest('[data-action="toggleEdit"]'))) {
                    vscode.postMessage({ command: 'toggleEdit' });
                    return;
                }
            });

            document.addEventListener('focusout', function(e) {
                var t = e.target;
                if (t && t.classList && t.classList.contains('section-textarea') && t.dataset.sectionKey) {
                    vscode.postMessage({ command: 'editTextSection', sectionKey: t.dataset.sectionKey, content: t.value });
                }
                if (t && t.classList && (t.classList.contains('req-field-textarea') || t.classList.contains('req-field-input'))) {
                    var idx = parseInt(t.dataset.reqIdx);
                    var field = t.dataset.field;
                    if (!isNaN(idx) && field) {
                        vscode.postMessage({ command: 'editReq', reqIndex: idx, field: field, value: t.value });
                    }
                }
            });

            document.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && e.target && e.target.id === 'probe-input-main') {
                    var answer = e.target.value.trim();
                    if (answer) vscode.postMessage({ command: 'respondToProbe', answer: answer });
                }
                if (e.key === 'Enter' && e.target && e.target.classList && e.target.classList.contains('probe-input') && e.target.id && e.target.id.startsWith('probe-input-group-')) {
                    var group = e.target.id.replace('probe-input-group-', '');
                    var answer = e.target.value.trim();
                    if (answer) vscode.postMessage({ command: 'respondToGroupProbe', group: group, answer: answer });
                }
            });

            // Auto-resize textareas to fit content
            function autoResize(el) {
                el.style.height = 'auto';
                el.style.height = el.scrollHeight + 'px';
            }
            document.addEventListener('input', function(e) {
                if (e.target && (e.target.classList.contains('section-textarea') || e.target.classList.contains('req-field-textarea') || e.target.classList.contains('auto-resize'))) {
                    autoResize(e.target);
                }
            });
            // Initial auto-resize after render
            var _origRender = render;
            render = function() {
                _origRender();
                var tas = document.querySelectorAll('.section-textarea, .req-field-textarea');
                for (var t = 0; t < tas.length; t++) { autoResize(tas[t]); }
            };
        `;
    }
}
