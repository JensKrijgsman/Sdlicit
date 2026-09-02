// ---------------------------------------------------------------------------
// Sdlicit — ADR Panel Provider (Section-by-Section)
// ---------------------------------------------------------------------------
// WebviewPanel for creating/viewing ADRs with per-field editing,
// inline feedback via stepEvent, Socratic probes, focus mode,
// and WIP persistence. Mirrors SOW panel UX.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { SdlicitClient, Clarification, SocraticProbe } from '../services/sdlicitClient';
import { ArtifactStore } from '../services/artifactStore';
import { KBSyncService } from '../services/kbSyncService';
import { WipManager } from '../services/wipManager';
import { getNonce, wrapHtml, escapeHtml } from '../webview/webviewHelper';

export interface ADRField {
    key: string;
    heading: string;
    prompt: string;
    content: string;
    status: 'empty' | 'editing' | 'validated' | 'accepted';
    suggestions: Array<{ field: string; message: string }>;
    socraticProbe?: SocraticProbe;
    probeAnswered: boolean;
    probeCollapsed: boolean;
    validating: boolean;
}

const ADR_FIELD_ORDER = [
    'title',
    'context',
    'decision',
    'alternatives',
    'rationale',
    'consequences',
];

const ADR_FIELD_META: Record<string, { heading: string; prompt: string }> = {
    title: { heading: 'Title', prompt: 'A short, descriptive title for this decision' },
    context: { heading: 'Context', prompt: 'What is the issue that motivates this decision?' },
    decision: { heading: 'Decision', prompt: 'What is the change being proposed or decided?' },
    alternatives: { heading: 'Alternatives Considered', prompt: 'What other options were evaluated?' },
    rationale: { heading: 'Rationale', prompt: 'Why was this decision made over the alternatives?' },
    consequences: { heading: 'Consequences', prompt: 'What are the positive and negative consequences?' },
};

export class ADRPanelProvider {
    private panel: vscode.WebviewPanel | undefined;
    private fields: ADRField[] = [];
    private readOnly = false;
    private clarifications: Clarification[] = [];
    private projectDir: string;
    private artifactPath: string | undefined;
    private supersedesHint: { adr_id: string; adr_title: string; reason: string } | undefined;
    private traceLinks: { implements: string[]; supersedes: string; testedBy: string[] } = { implements: [], supersedes: '', testedBy: [] };
    private fileWatcher: vscode.Disposable | undefined;
    private resolvePromise?: (value: 'accepted' | 'declined') => void;

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

    /** Create a new ADR with empty fields. */
    async startCreation(): Promise<'accepted' | 'declined'> {
        this.readOnly = false;

        // Prompt user if WIP data exists
        let wip: { fields: ADRField[]; clarifications: Clarification[] } | null = null;
        if (this.globalStoragePath) {
            const wipMgr = new WipManager(this.globalStoragePath);
            const decision = await wipMgr.promptIfWipExists('adr');
            if (decision === 'resume') {
                wip = this.loadWip();
            } else {
                this.deleteWip();
            }
        } else {
            wip = this.loadWip();
        }
        if (wip) {
            this.fields = wip.fields;
            this.clarifications = wip.clarifications || [];
        } else {
            this.fields = ADR_FIELD_ORDER.map(key => ({
                key,
                heading: ADR_FIELD_META[key].heading,
                prompt: ADR_FIELD_META[key].prompt,
                content: '',
                status: 'empty' as const,
                suggestions: [],
                probeAnswered: false,
                probeCollapsed: false,
                validating: false,
            }));
        }

        this.createPanel();
        this.updatePanel();

        if (wip) {
            this.client.log('ADR: Restored from WIP');
        }

        return new Promise<'accepted' | 'declined'>((resolve) => {
            this.resolvePromise = resolve;
        });
    }

    /** Get fields for external access (bidirectional linking). */
    getFields(): ADRField[] { return this.fields; }

    /** Update a field's content from external source (e.g., chat "Insert"). */
    updateFieldFromExternal(fieldKey: string, newContent: string): void {
        const field = this.fields.find(f => f.key === fieldKey);
        if (!field) { return; }
        field.content = newContent;
        if (field.status === 'accepted') { field.status = 'editing'; }
        else if (field.status === 'empty') { field.status = 'editing'; }
        this.updatePanel();
        this.saveWip();
    }

    /** Open an existing ADR markdown in read-only mode. */
    async openExisting(markdown: string, filePath?: string): Promise<void> {
        this.readOnly = true;
        this.artifactPath = filePath;
        this.fields = this.parseADRMarkdown(markdown);
        this.createPanel();
        this.updatePanel();
    }

    private createPanel(): void {
        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.adrPanel',
            'Sdlicit — ADR',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        this.panel.onDidDispose(() => {
            this.saveWip();
            this.panel = undefined;
            this.fileWatcher?.dispose();
            vscode.commands.executeCommand('sdlicit.unregisterPanel', 'adr');
            if (this.resolvePromise) {
                this.resolvePromise('declined');
                this.resolvePromise = undefined;
            }
        });

        // Watch the artifact file for external changes
        if (this.artifactPath) {
            this.setupFileWatcher(this.artifactPath);
        }

        this.renderHtml();
        this.setupMessageHandler();
    }

    // -- WIP persistence -------------------------------------------------------

    private get wipPath(): string | undefined {
        if (!this.globalStoragePath) { return undefined; }
        return path.join(this.globalStoragePath, 'wip', 'wip_adr.json');
    }

    private saveWip(): void {
        if (this.readOnly) { return; }
        const wp = this.wipPath;
        if (!wp) { return; }
        const dir = path.dirname(wp);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        const data = {
            fields: this.fields,
            clarifications: this.clarifications,
            savedAt: new Date().toISOString(),
        };
        fs.writeFileSync(wp, JSON.stringify(data, null, 2), 'utf-8');
    }

    private loadWip(): { fields: ADRField[]; clarifications: Clarification[] } | null {
        const wp = this.wipPath;
        if (!wp || !fs.existsSync(wp)) { return null; }
        try {
            const raw = JSON.parse(fs.readFileSync(wp, 'utf-8'));
            if (raw.fields?.length > 0) { return raw; }
        } catch { /* ignore */ }
        return null;
    }

    private deleteWip(): void {
        const wp = this.wipPath;
        if (wp && fs.existsSync(wp)) { fs.unlinkSync(wp); }
    }

    /** Insert a key: value into YAML frontmatter. */
    private addFrontmatterField(md: string, key: string, value: string): string {
        const frontmatterEnd = md.indexOf('\n---', 4);
        if (frontmatterEnd === -1) { return md; }
        // Insert before the closing ---
        return md.slice(0, frontmatterEnd) + `\n${key}: ${value}` + md.slice(frontmatterEnd);
    }

    /** Update the old ADR's YAML frontmatter to status: superseded. */
    private async markOldAdrSuperseded(oldAdrId: string, newAdrId: string): Promise<void> {
        const artifacts = this.store.listArtifacts();
        const oldAdr = artifacts.find(a => a.id === oldAdrId || a.id.includes(oldAdrId));
        if (!oldAdr) { return; }
        try {
            let content = fs.readFileSync(oldAdr.filePath, 'utf-8');
            // Replace status in frontmatter
            content = content.replace(
                /^(status:\s*).+$/m,
                `$1superseded`,
            );
            // Add superseded_by if not present
            if (!content.includes('superseded_by:')) {
                const fmEnd = content.indexOf('\n---', 4);
                if (fmEnd !== -1) {
                    content = content.slice(0, fmEnd) + `\nsuperseded_by: ${newAdrId}` + content.slice(fmEnd);
                }
            }
            fs.writeFileSync(oldAdr.filePath, content, 'utf-8');
        } catch { /* best-effort */ }
    }

    /** Apply auto-detected implements links to an ADR's YAML frontmatter. */
    private async applyImplementsLinks(filePath: string, reqIds: string[]): Promise<void> {
        if (!reqIds.length || !fs.existsSync(filePath)) { return; }
        try {
            let content = fs.readFileSync(filePath, 'utf-8');
            const implementsLine = `implements: [${reqIds.join(', ')}]`;

            // Check if frontmatter already has implements
            if (/^implements:\s*.+$/m.test(content)) {
                // Merge: parse existing and deduplicate
                const existing = content.match(/^implements:\s*\[?(.*?)\]?\s*$/m);
                if (existing) {
                    const current = existing[1].split(',').map(s => s.trim().replace(/['"]/g, '')).filter(Boolean);
                    const merged = [...new Set([...current, ...reqIds])];
                    content = content.replace(/^implements:\s*.+$/m, `implements: [${merged.join(', ')}]`);
                }
            } else {
                // Insert implements field into frontmatter
                content = this.addFrontmatterField(content, 'implements', `[${reqIds.join(', ')}]`);
            }

            fs.writeFileSync(filePath, content, 'utf-8');
        } catch { /* best-effort — don't block the user */ }
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
            this.fields = this.parseADRMarkdown(md);
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

        // Compute trace links from DataService if we have a saved artifact
        if (this.dataService && this.artifactPath) {
            const artifacts = this.dataService.getArtifacts();
            const thisArtifact = artifacts.find(a => a.filePath === this.artifactPath);
            if (thisArtifact) {
                this.traceLinks.implements = thisArtifact.traces.implements;
                this.traceLinks.supersedes = thisArtifact.traces.supersedes;
                this.traceLinks.testedBy = thisArtifact.traces.testedBy;
            }
        }

        this.panel.webview.postMessage({
            command: 'updateFields',
            fields: this.fields,
            readOnly: this.readOnly,
            traceLinks: this.traceLinks,
        });
    }

    private setupMessageHandler(): void {
        if (!this.panel) { return; }

        this.panel.webview.onDidReceiveMessage(async (msg) => {
            const fieldIdx = msg.fieldKey
                ? this.fields.findIndex(f => f.key === msg.fieldKey)
                : -1;

            switch (msg.command) {
                case 'editField': {
                    if (fieldIdx >= 0) {
                        this.fields[fieldIdx].content = msg.content;
                        this.fields[fieldIdx].status = msg.content.trim() ? 'editing' : 'empty';
                        this.saveWip();
                        this.writeArtifactFile();
                    }
                    break;
                }
                case 'validateField': {
                    // Triggered when user types "." or moves to next field
                    if (fieldIdx < 0) { break; }
                    const field = this.fields[fieldIdx];
                    if (!field.content.trim()) { break; }

                    field.validating = true;
                    // Don't do full re-render while user might be typing — just mark validating
                    this.panel?.webview.postMessage({
                        command: 'fieldValidating',
                        fieldKey: field.key,
                    });

                    try {
                        const partialFields: Record<string, string> = {};
                        for (const f of this.fields) {
                            if (f.content.trim()) { partialFields[f.key] = f.content; }
                        }

                        const result = await this.client.stepEvent(
                            field.key, field.content, partialFields,
                            this.projectDir, this.clarifications,
                        );

                        const allSuggestions = result.suggestions
                            ?? (result.suggestion ? [result.suggestion] : []);
                        field.suggestions = allSuggestions
                            .filter(s => s.should_show !== false)
                            .map(s => ({ field: s.field, message: s.message }));

                        if (result.socratic_probe) {
                            field.socraticProbe = {
                                probe_id: result.socratic_probe.probe_id || `adr-${field.key}-${Date.now()}`,
                                question: result.socratic_probe.question,
                                style: result.socratic_probe.style || 'depth',
                                originating_agent: result.socratic_probe.originating_agent || 'adr_step',
                                what_was_asked: result.socratic_probe.what_was_asked || field.content,
                                turn: result.socratic_probe.turn ?? 1,
                                max_turns: result.socratic_probe.max_turns ?? 7,
                                rag_grounding: result.socratic_probe.rag_grounding || '',
                                kb_facts: result.socratic_probe.kb_facts || '',
                                transparency_events: result.socratic_probe.transparency_events || [],
                            };
                            field.probeAnswered = false;
                            field.probeCollapsed = false;
                        }

                        // Supersession detection — notify user if this title overlaps an existing ADR
                        if (result.supersedes_hint && field.key === 'title') {
                            this.supersedesHint = result.supersedes_hint;
                            vscode.window.showInformationMessage(
                                `This ADR may supersede "${result.supersedes_hint.adr_title}" (${result.supersedes_hint.reason}). ` +
                                `It will be pre-selected when you save.`,
                            );
                        }

                        // Only change status to 'validated' if user hasn't started editing again
                        if (field.status !== 'editing') {
                            field.status = 'validated';
                        }
                    } catch (err: any) {
                        console.warn(`ADR step validation failed for ${field.key}:`, err.message);
                    }

                    field.validating = false;
                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'acceptField': {
                    if (fieldIdx >= 0) {
                        this.fields[fieldIdx].status = 'accepted';
                        this.fields[fieldIdx].probeCollapsed = true;
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'respondToProbe': {
                    if (fieldIdx < 0) { break; }
                    const field = this.fields[fieldIdx];
                    const answer = msg.answer as string;
                    if (!answer || !field.socraticProbe) { break; }

                    field.probeAnswered = true;
                    this.clarifications.push({
                        question: field.socraticProbe.question,
                        answer,
                    });

                    // Re-validate with the clarification
                    field.validating = true;
                    this.updatePanel();

                    try {
                        const partialFields: Record<string, string> = {};
                        for (const f of this.fields) {
                            if (f.content.trim()) { partialFields[f.key] = f.content; }
                        }

                        const result = await this.client.stepEvent(
                            field.key, field.content, partialFields,
                            this.projectDir, this.clarifications,
                        );

                        const allSuggestions = result.suggestions
                            ?? (result.suggestion ? [result.suggestion] : []);
                        field.suggestions = allSuggestions
                            .filter(s => s.should_show !== false)
                            .map(s => ({ field: s.field, message: s.message }));

                        if (result.socratic_probe) {
                            field.socraticProbe = {
                                probe_id: result.socratic_probe.probe_id || `adr-${field.key}-${Date.now()}`,
                                question: result.socratic_probe.question,
                                style: result.socratic_probe.style || 'depth',
                                originating_agent: result.socratic_probe.originating_agent || 'adr_step',
                                what_was_asked: result.socratic_probe.what_was_asked || answer,
                                turn: result.socratic_probe.turn ?? 1,
                                max_turns: result.socratic_probe.max_turns ?? 7,
                                rag_grounding: result.socratic_probe.rag_grounding || '',
                                kb_facts: result.socratic_probe.kb_facts || '',
                                transparency_events: result.socratic_probe.transparency_events || [],
                            };
                            field.probeAnswered = false;
                        } else {
                            // No more probes
                            field.socraticProbe = undefined;
                            field.probeAnswered = false;
                        }
                    } catch (err: any) {
                        console.warn(`ADR probe re-validation failed:`, err.message);
                    }

                    field.validating = false;
                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'toggleProbe': {
                    if (fieldIdx >= 0) {
                        this.fields[fieldIdx].probeCollapsed = !this.fields[fieldIdx].probeCollapsed;
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
                case 'saveADR': {
                    const hasTitleContent = this.fields.find(f => f.key === 'title')?.content?.trim();
                    if (!hasTitleContent) {
                        vscode.window.showWarningMessage('Sdlicit: ADR title is required.');
                        break;
                    }

                    const title = this.fields.find(f => f.key === 'title')!.content.trim();
                    const existingAdrs = this.store.listArtifacts().filter(a => a.type === 'adr');

                    // Ask if this supersedes an existing ADR
                    let supersededAdr: string | undefined;
                    if (this.supersedesHint) {
                        // Auto-detected supersession — confirm with user
                        const confirm = await vscode.window.showInformationMessage(
                            `This ADR appears to supersede "${this.supersedesHint.adr_title}". Mark it as superseding?`,
                            { modal: false },
                            'Yes, supersede', 'No, create new',
                        );
                        if (confirm === undefined) { break; } // cancelled
                        if (confirm === 'Yes, supersede') {
                            supersededAdr = this.supersedesHint.adr_id;
                        }
                    } else if (existingAdrs.length > 0) {
                        const items = [
                            { label: '$(add) New ADR (no supersession)', id: '' },
                            ...existingAdrs.map(a => ({
                                label: `$(replace) Supersedes: ${a.title || a.id}`,
                                id: a.id,
                            })),
                        ];
                        const picked = await vscode.window.showQuickPick(items, {
                            title: 'Does this ADR supersede an existing one?',
                            placeHolder: 'Select an ADR to supersede, or create as new',
                        });
                        if (!picked) { break; } // cancelled
                        supersededAdr = picked.id || undefined;
                    }

                    // Build structured ADR data for the backend
                    const adrData: Record<string, unknown> = {
                        artifact_type: 'adr',
                        title,
                        status: 'proposed',
                        context: this.fields.find(f => f.key === 'context')?.content?.trim() || '',
                        decision: this.fields.find(f => f.key === 'decision')?.content?.trim() || '',
                        alternatives: this.fields.find(f => f.key === 'alternatives')?.content?.trim() || '',
                        rationale: this.fields.find(f => f.key === 'rationale')?.content?.trim() || '',
                        consequences: this.fields.find(f => f.key === 'consequences')?.content?.trim() || '',
                        supersedes: supersededAdr || this.traceLinks?.supersedes || '',
                        implements: this.traceLinks?.implements || [],
                        references: [],
                    };

                    // Save via backend API (backend handles naming, format, paths + auto-detect implements)
                    let artifactFilePath: string;
                    let artifactId: string;
                    let finalMd: string;
                    try {
                        const saveResult = await this.client.saveArtifact('adr', adrData, this.projectDir);
                        artifactFilePath = saveResult.markdown_path || saveResult.json_path;
                        artifactId = saveResult.artifact_meta?.tag || `ADR-${String(existingAdrs.length + 1).padStart(4, '0')}`;

                        // Backend auto-detected implements links — inform user
                        if (saveResult.suggested_implements && saveResult.suggested_implements.length > 0) {
                            vscode.window.showInformationMessage(
                                `Sdlicit: Auto-linked ADR to: ${saveResult.suggested_implements.join(', ')}`
                            );
                        }

                        // Read the rendered markdown (now includes implements in frontmatter)
                        const mdResult = await this.client.renderArtifactMarkdown('adr', this.projectDir, saveResult.artifact_meta?.filename);
                        finalMd = mdResult.markdown;
                    } catch (err: any) {
                        // Fallback to local save if backend API not available
                        const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
                        const num = String(existingAdrs.length + 1).padStart(4, '0');
                        const adrFilename = `ADR-${num}-${slug}.md`;
                        artifactId = `ADR-${num}`;

                        // Ensure traceLinks has the adr_id for frontmatter
                        if (!this.traceLinks) {
                            this.traceLinks = { implements: [], supersedes: '', testedBy: [] };
                        }
                        if (supersededAdr) {
                            this.traceLinks.supersedes = supersededAdr;
                        }

                        finalMd = this.buildFullMarkdown();
                        // Prepend id field into frontmatter
                        finalMd = this.addFrontmatterField(finalMd, 'id', artifactId);

                        artifactFilePath = this.store.saveByMeta(
                            { tag: artifactId, filename: adrFilename, relative_path: `adr/${adrFilename}`, artifact_type: 'adr' },
                            finalMd,
                        );
                    }

                    this.artifactPath = artifactFilePath;

                    // Notify artifact tree and start async ingestion
                    vscode.commands.executeCommand('sdlicit.markArtifactIngesting', artifactId);
                    this.deleteWip();
                    this.panel?.dispose();
                    if (this.resolvePromise) {
                        this.resolvePromise('accepted');
                        this.resolvePromise = undefined;
                    }

                    // Async: detect trace links → update frontmatter → ingest (runs after panel closes)
                    (async () => {
                        try {
                            // Step 1: Detect implements links BEFORE ingestion
                            let updatedMd = finalMd;
                            try {
                                const traceResult = await this.client.checkTraceability(artifactId, this.projectDir, finalMd);

                                // Auto-link: write detected implements into frontmatter
                                if (traceResult.suggested_implements?.length > 0 && artifactFilePath) {
                                    await this.applyImplementsLinks(artifactFilePath, traceResult.suggested_implements);
                                    // Re-read the updated file for ingestion
                                    updatedMd = fs.readFileSync(artifactFilePath, 'utf-8');
                                }

                                if (traceResult.issues.length > 0) {
                                    const impactedMsg = traceResult.impacted_nodes.length > 0
                                        ? ` (${traceResult.impacted_nodes.length} impacted artifacts)`
                                        : '';
                                    vscode.window.showWarningMessage(
                                        `Sdlicit: ADR saved. ${traceResult.issues.length} traceability issue(s) detected${impactedMsg}.`,
                                        'Show Issues'
                                    ).then(action => {
                                        if (action === 'Show Issues') {
                                            vscode.commands.executeCommand('sdlicit.checkTraceability', artifactId);
                                        }
                                    });
                                } else if (traceResult.suggested_implements?.length > 0) {
                                    vscode.window.showInformationMessage(
                                        `Sdlicit: ADR saved → ${artifactId}. Auto-linked to: ${traceResult.suggested_implements.join(', ')}`
                                    );
                                }
                            } catch {
                                // Traceability check failed — proceed with ingestion of original content
                            }

                            // Step 2: Ingest (with updated frontmatter if trace detection succeeded)
                            if (supersededAdr) {
                                await this.markOldAdrSuperseded(supersededAdr, artifactId);
                                await this.client.supersedeADR(supersededAdr!, updatedMd, artifactId);
                            } else {
                                await this.client.ingestArtifact(updatedMd, 'adr', artifactId);
                            }
                            vscode.commands.executeCommand('sdlicit.markArtifactIngested', artifactId);

                            if (!updatedMd.includes('implements:')) {
                                vscode.window.showInformationMessage(`Sdlicit: ADR saved and ingested → ${artifactId}`);
                            }
                        } catch {
                            vscode.commands.executeCommand('sdlicit.markArtifactIngestError', artifactId);
                            vscode.window.showWarningMessage(`Sdlicit: ADR saved but KB ingestion failed → ${artifactId}`);
                        }
                    })();
                    break;
                }
                case 'suggestTopics': {
                    // Get SOW/SRS context for topic suggestion
                    const brief = this.store.getLatestSOW() || '';
                    if (!brief) {
                        vscode.window.showWarningMessage('Sdlicit: No SOW found. Create one first for topic suggestions.');
                        break;
                    }

                    this.panel?.webview.postMessage({ command: 'suggestingTopics', loading: true });

                    try {
                        const result = await this.client.suggestDirections(brief, this.projectDir);

                        this.panel?.webview.postMessage({ command: 'suggestingTopics', loading: false });

                        if (!result.directions || result.directions.length === 0) {
                            vscode.window.showInformationMessage('Sdlicit: No ADR topics suggested at this time.');
                            break;
                        }

                        // Use native VS Code quick pick (with manual entry option)
                        const items: vscode.QuickPickItem[] = result.directions.map((d: any) => ({
                            label: d.title,
                            description: d.priority || '',
                            detail: d.rationale + (d.gap_filled ? ` | Gap: ${d.gap_filled}` : ''),
                        }));

                        const picked = await vscode.window.showQuickPick(items, {
                            title: 'Select an ADR topic (or press Esc to enter manually)',
                            placeHolder: 'Pick a suggested topic…',
                            matchOnDetail: true,
                        });

                        if (!picked) { break; } // user cancelled

                        // Find the full direction object
                        const direction = result.directions.find((d: any) => d.title === picked.label);
                        if (!direction) { break; }

                        // Overwrite title and context fields
                        const titleField = this.fields.find(f => f.key === 'title');
                        const contextField = this.fields.find(f => f.key === 'context');
                        if (titleField) {
                            titleField.content = direction.title;
                            titleField.status = 'editing';
                        }
                        if (contextField) {
                            contextField.content = direction.rationale + (direction.gap_filled ? `\n\nGap addressed: ${direction.gap_filled}` : '');
                            contextField.status = 'editing';
                        }

                        this.updatePanel();
                        this.saveWip();

                        // Trigger socratic probe via stepEvent for the context field
                        if (contextField && contextField.content.trim()) {
                            contextField.validating = true;
                            this.updatePanel();

                            try {
                                const partialFields: Record<string, string> = {};
                                for (const f of this.fields) {
                                    if (f.content.trim()) { partialFields[f.key] = f.content; }
                                }

                                const stepResult = await this.client.stepEvent(
                                    contextField.key, contextField.content, partialFields,
                                    this.projectDir, this.clarifications,
                                );

                                const allCtxSuggestions = stepResult.suggestions
                                    ?? (stepResult.suggestion ? [stepResult.suggestion] : []);
                                contextField.suggestions = allCtxSuggestions
                                    .filter(s => s.should_show !== false)
                                    .map(s => ({ field: s.field, message: s.message }));

                                if (stepResult.socratic_probe) {
                                    contextField.socraticProbe = {
                                        probe_id: stepResult.socratic_probe.probe_id || `adr-${contextField.key}-${Date.now()}`,
                                        question: stepResult.socratic_probe.question,
                                        style: stepResult.socratic_probe.style || 'depth',
                                        originating_agent: stepResult.socratic_probe.originating_agent || 'adr_step',
                                        what_was_asked: stepResult.socratic_probe.what_was_asked || contextField.content,
                                        turn: stepResult.socratic_probe.turn ?? 1,
                                        max_turns: stepResult.socratic_probe.max_turns ?? 7,
                                        rag_grounding: stepResult.socratic_probe.rag_grounding || '',
                                        kb_facts: stepResult.socratic_probe.kb_facts || '',
                                        transparency_events: stepResult.socratic_probe.transparency_events || [],
                                    };
                                    contextField.probeAnswered = false;
                                    contextField.probeCollapsed = false;
                                }

                                contextField.status = 'validated';
                            } catch (err: any) {
                                // Non-fatal — fields are still pre-filled
                            }

                            contextField.validating = false;
                            this.updatePanel();
                            this.saveWip();
                        }
                    } catch (err: any) {
                        this.panel?.webview.postMessage({ command: 'suggestingTopics', loading: false });
                        vscode.window.showErrorMessage(`Sdlicit: Topic suggestion failed — ${err.message}`);
                    }
                    break;
                }
                case 'chatAboutField': {
                    if (fieldIdx < 0) { break; }
                    const cf = this.fields[fieldIdx];
                    const probeQ = cf.socraticProbe?.question ?? '';
                    const context = [
                        `**ADR Field: ${cf.heading}**`,
                        '',
                        cf.content ? `Current content:\n${cf.content}` : '(empty)',
                        '',
                        probeQ ? `Socratic probe: ${probeQ}` : '',
                    ].filter(Boolean).join('\n');

                    vscode.commands.executeCommand('sdlicit.chatWithContext', {
                        panelId: 'adr',
                        panelType: 'adr',
                        sectionKey: cf.key,
                        sectionHeading: cf.heading,
                        context,
                    });
                    break;
                }
                case 'toggleEdit': {
                    this.readOnly = !this.readOnly;
                    if (!this.readOnly) {
                        for (const f of this.fields) {
                            if (f.status === 'accepted') { f.status = 'validated'; }
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
                        const existingAdrs = this.store.listArtifacts().filter(a => a.type === 'adr');
                        const num = String(existingAdrs.length + 1).padStart(4, '0');
                        const previewFilename = `ADR-${num}-draft.md`;
                        this.artifactPath = this.store.saveByMeta(
                            { tag: `ADR-${num}`, filename: previewFilename, relative_path: `adr/${previewFilename}`, artifact_type: 'adr' },
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
        const f = (key: string) => this.fields.find(x => x.key === key)?.content?.trim() ?? '';
        const title = f('title') || 'Untitled Decision';
        const today = new Date().toISOString().split('T')[0];
        const lines: string[] = [];

        // YAML frontmatter — matches backend's _render_adr_md format
        lines.push('---');
        if (this.traceLinks?.implements?.length) {
            lines.push(`implements: [${this.traceLinks.implements.join(', ')}]`);
        }
        if (this.traceLinks?.supersedes) {
            lines.push(`supersedes: ${this.traceLinks.supersedes}`);
        }
        if (this.traceLinks?.testedBy?.length) {
            lines.push(`tested_by: [${this.traceLinks.testedBy.join(', ')}]`);
        }
        lines.push(`status: proposed`);
        lines.push(`date: ${today}`);
        lines.push('---');
        lines.push('');

        // Body — MADR 4.0.0 structure
        lines.push(`# ${title}`);
        lines.push('');
        lines.push(`**Status:** Proposed`);
        lines.push(`**Date:** ${today}`);
        lines.push('');

        const sectionMap: Record<string, string> = {
            context: 'Context',
            decision: 'Decision',
            alternatives: 'Alternatives Considered',
            rationale: 'Rationale',
            consequences: 'Consequences',
        };
        for (const key of ['context', 'decision', 'alternatives', 'rationale', 'consequences']) {
            const val = f(key);
            if (val) {
                lines.push(`## ${sectionMap[key]}`);
                lines.push('');
                lines.push(val);
                lines.push('');
            }
        }

        // References section (traceability links)
        const refLines: string[] = [];
        if (this.traceLinks?.implements?.length) {
            refLines.push(`- Implements: ${this.traceLinks.implements.join(', ')}`);
        }
        if (this.traceLinks?.supersedes) {
            refLines.push(`- Supersedes: ${this.traceLinks.supersedes}`);
        }
        if (refLines.length) {
            lines.push('## References');
            lines.push('');
            lines.push(...refLines);
            lines.push('');
        }

        return lines.join('\n');
    }

    /** Parse existing ADR markdown back into fields. */
    private parseADRMarkdown(md: string): ADRField[] {
        const lines = md.split('\n');
        const fields: ADRField[] = [];
        let currentKey = '';
        let contentLines: string[] = [];
        let titleFound = false;
        let inFrontmatter = false;
        let fmStarted = false;

        // Parse YAML frontmatter for trace links
        this.traceLinks = { implements: [], supersedes: '', testedBy: [] };
        for (const line of lines) {
            if (line.trim() === '---') {
                if (!fmStarted) { fmStarted = true; inFrontmatter = true; continue; }
                else { inFrontmatter = false; break; }
            }
            if (inFrontmatter) {
                const m = line.match(/^(\w[\w_-]*)\s*:\s*(.*)/);
                if (m) {
                    const key = m[1].toLowerCase();
                    const val = m[2].trim();
                    if (key === 'implements') {
                        // Parse YAML list: [REQ-01, REQ-02] or single value
                        const inner = val.replace(/^\[|\]$/g, '');
                        this.traceLinks.implements = inner.split(',').map(s => s.trim().replace(/['"]/g, '')).filter(Boolean);
                    } else if (key === 'supersedes') {
                        this.traceLinks.supersedes = val.replace(/['"]/g, '');
                    } else if (key === 'tested_by') {
                        const inner = val.replace(/^\[|\]$/g, '');
                        this.traceLinks.testedBy = inner.split(',').map(s => s.trim().replace(/['"]/g, '')).filter(Boolean);
                    }
                }
            }
        }

        const headingToKey: Record<string, string> = {};
        for (const [k, v] of Object.entries(ADR_FIELD_META)) {
            headingToKey[v.heading.toLowerCase()] = k;
        }

        const flush = () => {
            if (currentKey) {
                const content = contentLines.join('\n').trim();
                const meta = ADR_FIELD_META[currentKey] || { heading: currentKey, prompt: '' };
                fields.push({
                    key: currentKey,
                    heading: meta.heading,
                    prompt: meta.prompt,
                    content,
                    status: content ? 'accepted' : 'empty',
                    suggestions: [],
                    probeAnswered: false,
                    probeCollapsed: false,
                    validating: false,
                });
            }
            contentLines = [];
        };

        for (const line of lines) {
            // Skip YAML frontmatter
            if (line.startsWith('---') && fields.length === 0 && !currentKey) { continue; }
            // Skip status/date lines
            if (line.startsWith('**Status:**') || line.startsWith('**Date:**')) { continue; }

            const h1 = line.match(/^#\s+(.+)/);
            const h2 = line.match(/^##\s+(.+)/);
            if (h1 && !titleFound) {
                // Only the first H1 is treated as the title
                flush();
                titleFound = true;
                currentKey = 'title';
                contentLines.push(h1[1].trim());
            } else if (h2) {
                flush();
                const heading = h2[1].trim();
                currentKey = headingToKey[heading.toLowerCase()] || heading.toLowerCase().replace(/\s+/g, '_');
            } else {
                contentLines.push(line);
            }
        }
        flush();

        // Fill missing fields
        for (const key of ADR_FIELD_ORDER) {
            if (!fields.find(f => f.key === key)) {
                const meta = ADR_FIELD_META[key];
                fields.push({
                    key,
                    heading: meta.heading,
                    prompt: meta.prompt,
                    content: '',
                    status: 'empty',
                    suggestions: [],
                    probeAnswered: false,
                    probeCollapsed: false,
                    validating: false,
                });
            }
        }

        return fields.sort((a, b) => ADR_FIELD_ORDER.indexOf(a.key) - ADR_FIELD_ORDER.indexOf(b.key));
    }

    // -- HTML rendering --------------------------------------------------------

    private renderHtml(): void {
        if (!this.panel) { return; }
        const nonce = getNonce();

        const body = `
            <div id="adr-root">
                <div class="flex items-center justify-between mb-md">
                    <h1 style="margin-bottom:0">Architectural Decision Record</h1>
                    <div class="flex gap-xs">
                        <button class="btn btn-primary btn-sm" data-action="suggestTopics" id="suggest-topics-btn" title="Get AI-suggested ADR topics from your project context">Suggest Topics</button>
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
                <div id="fields-container"></div>
                <div id="save-bar" class="mt-lg hidden">
                    <button class="btn btn-primary" data-action="saveADR">Save &amp; Ingest to KB</button>
                </div>
            </div>
        `;

        const scripts = this.buildScripts();
        this.panel.webview.html = wrapHtml(body, nonce, scripts);
    }

    private buildScripts(): string {
        return `
            var _fields = [];
            var _readOnly = false;
            var _traceLinks = { implements: [], supersedes: '', testedBy: [] };
            var _focusMode = false;
            var _focusIndex = 0;
            var _debounceTimers = {};
            var _suggestingTopics = false;
            var _isRendering = false;

            window.addEventListener('message', function(event) {
                var msg = event.data;
                if (msg.command === 'updateFields') {
                    _fields = msg.fields;
                    _readOnly = msg.readOnly || false;
                    _traceLinks = msg.traceLinks || { implements: [], supersedes: '', testedBy: [] };
                    renderTraceLinks();
                    renderFields();
                } else if (msg.command === 'fieldValidating') {
                    // Show spinner on a specific field without full re-render
                    var panel = document.querySelector('.section-panel[data-key="' + msg.fieldKey + '"]');
                    if (panel) {
                        var header = panel.querySelector('.section-header .flex');
                        if (header && header.firstElementChild) {
                            header.firstElementChild.outerHTML = '<span class="spinner" style="width:12px;height:12px"></span>';
                        }
                    }
                } else if (msg.command === 'suggestingTopics') {
                    _suggestingTopics = msg.loading;
                    var btn = document.getElementById('suggest-topics-btn');
                    if (btn) {
                        btn.disabled = _suggestingTopics;
                        btn.textContent = _suggestingTopics ? 'Suggesting…' : 'Suggest Topics';
                    }
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
                var hasLinks = _traceLinks.implements.length > 0 || _traceLinks.supersedes || _traceLinks.testedBy.length > 0;
                if (!hasLinks) { container.innerHTML = ''; return; }

                var html = '<div class="card-flat mb-md" style="padding:10px 14px;border-left:3px solid var(--vscode-textLink-foreground)">';
                html += '<div class="text-xs text-muted mb-xs"><strong>Traceability Links</strong></div>';

                if (_traceLinks.implements.length > 0) {
                    html += '<div class="flex items-center gap-sm flex-wrap mb-xs">';
                    html += '<span class="text-xs text-muted">Implements:</span>';
                    for (var i = 0; i < _traceLinks.implements.length; i++) {
                        html += '<span class="trace-node clickable" data-artifact-id="' + esc(_traceLinks.implements[i]) + '" title="Click to open" style="cursor:pointer;padding:2px 8px;border-radius:4px;font-size:.8em;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(_traceLinks.implements[i]) + '</span>';
                    }
                    html += '</div>';
                }
                if (_traceLinks.supersedes) {
                    html += '<div class="flex items-center gap-sm mb-xs">';
                    html += '<span class="text-xs text-muted">Supersedes:</span>';
                    html += '<span class="trace-node clickable" data-artifact-id="' + esc(_traceLinks.supersedes) + '" title="Click to open" style="cursor:pointer;padding:2px 8px;border-radius:4px;font-size:.8em;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(_traceLinks.supersedes) + '</span>';
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
                var inList = false;
                for (var i = 0; i < lines.length; i++) {
                    var t = lines[i].trim();
                    if (/^[-*] /.test(t)) {
                        if (!inList) { html += '<ul>'; inList = true; }
                        html += '<li>' + fmtInline(t.slice(2)) + '</li>';
                    } else if (t === '') {
                        if (inList) { html += '</ul>'; inList = false; }
                    } else {
                        if (inList) { html += '</ul>'; inList = false; }
                        html += '<p>' + fmtInline(t) + '</p>';
                    }
                }
                if (inList) html += '</ul>';
                return html;
            }

            function renderFields() {
                var container = document.getElementById('fields-container');
                var saveBar = document.getElementById('save-bar');

                // Preserve focus state — save active textarea key + cursor position
                var activeKey = null;
                var selStart = 0;
                var selEnd = 0;
                var scrollTop = 0;
                var activeEl = document.activeElement;
                if (activeEl && activeEl.classList && activeEl.classList.contains('section-textarea')) {
                    activeKey = activeEl.dataset.key;
                    selStart = activeEl.selectionStart || 0;
                    selEnd = activeEl.selectionEnd || 0;
                    scrollTop = activeEl.scrollTop || 0;
                }

                var html = '';

                for (var i = 0; i < _fields.length; i++) {
                    var f = _fields[i];
                    var isAccepted = f.status === 'accepted';
                    var isValidating = f.validating;

                    // Focus mode: hide non-focused fields
                    var focusHidden = _focusMode && i !== _focusIndex;

                    // Status indicator
                    var statusIcon = '';
                    if (isValidating) {
                        statusIcon = '<span class="spinner" style="width:12px;height:12px"></span>';
                    } else if (isAccepted) {
                        statusIcon = '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>';
                    } else if (f.status === 'validated') {
                        statusIcon = '<span style="color:var(--vscode-charts-blue)">&#x25CF;</span>';
                    } else {
                        statusIcon = '<span style="opacity:.4">&#x25CB;</span>';
                    }

                    html += '<div class="section-panel' + (isAccepted ? '' : ' section-active') + (focusHidden ? ' hidden' : '') + '" data-key="' + f.key + '">';

                    // Header
                    html += '<div class="section-header">';
                    html += '<div class="flex items-center gap-sm">';
                    html += statusIcon;
                    html += '<strong>' + esc(f.heading) + '</strong>';
                    html += '</div>';
                    html += '</div>';

                    // Body
                    html += '<div class="section-body">';

                    if (_readOnly) {
                        html += '<div class="section-content">' + fmtMd(f.content) + '</div>';
                    } else {
                        // Prompt hint
                        if (!f.content) {
                            html += '<div class="text-xs text-muted mb-xs">' + esc(f.prompt) + '</div>';
                        }

                        // Editable textarea (title is single line)
                        var rows = f.key === 'title' ? 1 : Math.max(3, Math.min(10, (f.content || '').split('\\n').length + 1));
                        html += '<textarea class="section-textarea" data-key="' + f.key + '" rows="' + rows + '" placeholder="' + esc(f.prompt) + '">' + esc(f.content) + '</textarea>';

                        // Suggestions (inline feedback)
                        if (f.suggestions && f.suggestions.length > 0) {
                            html += '<div class="mt-xs">';
                            for (var si = 0; si < f.suggestions.length; si++) {
                                html += '<div class="text-sm" style="color:var(--vscode-charts-blue);padding:2px 0">&#x1F4A1; ' + esc(f.suggestions[si].message) + '</div>';
                            }
                            html += '</div>';
                        }

                        // Section actions
                        if (f.content) {
                            html += '<div class="flex gap-sm mt-sm flex-wrap">';
                            if (!isAccepted) {
                                html += '<button class="btn btn-primary btn-sm" data-action="acceptField" data-key="' + f.key + '">Accept</button>';
                            }
                            if (!f.socraticProbe && !isValidating) {
                                html += '<button class="btn btn-secondary btn-sm" data-action="validateField" data-key="' + f.key + '" title="Request Socratic probe for this field">Probe</button>';
                            }
                            html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutField" data-key="' + f.key + '">Chat</button>';
                            html += '</div>';
                        }
                    }

                    // Socratic probe
                    if (f.socraticProbe && !f.probeCollapsed && !f.probeAnswered) {
                        html += '<div class="companion-panel mt-sm">';
                        html += '<div class="companion-header flex items-center justify-between">';
                        html += '<span class="text-sm"><strong>Socratic Probe</strong> <span class="text-xs text-muted">(' + esc(f.socraticProbe.style || 'depth') + ' &middot; turn ' + (f.socraticProbe.turn || 1) + '/' + (f.socraticProbe.max_turns || 7) + ')</span></span>';
                        html += '<button class="btn btn-sm btn-secondary" data-action="toggleProbe" data-key="' + f.key + '" style="padding:0 6px;font-size:.75em">Collapse</button>';
                        html += '</div>';
                        if (f.socraticProbe.transparency_events && f.socraticProbe.transparency_events.length > 0) {
                            html += '<div class="flex gap-xs mt-xs flex-wrap">';
                            for (var te = 0; te < f.socraticProbe.transparency_events.length; te++) {
                                html += '<span style="font-size:.7em;padding:1px 6px;border-radius:3px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(f.socraticProbe.transparency_events[te]) + '</span>';
                            }
                            html += '</div>';
                        }
                        if (f.socraticProbe.kb_facts && f.socraticProbe.kb_facts.trim() !== '') {
                            html += '<details class="mt-xs" style="font-size:.82em">';
                            html += '<summary style="cursor:pointer;color:var(--vscode-textLink-foreground);user-select:none">From the Knowledge Base</summary>';
                            html += '<div style="margin-top:4px;padding:6px 8px;border-left:2px solid var(--vscode-textLink-foreground);background:var(--vscode-editor-background);white-space:pre-wrap;word-break:break-word">' + esc(f.socraticProbe.kb_facts.trim()) + '</div>';
                            html += '</details>';
                        }
                        html += '<div class="companion-body mt-xs">' + esc(f.socraticProbe.question) + '</div>';
                        html += '<div class="flex gap-sm mt-sm">';
                        html += '<input class="probe-input" id="probe-input-' + f.key + '" placeholder="Your response…" style="flex:1" />';
                        html += '<button class="btn btn-primary btn-sm" data-action="respondToProbe" data-key="' + f.key + '">Respond</button>';
                        html += '<button class="btn btn-secondary btn-sm" data-action="chatAboutField" data-key="' + f.key + '">Chat</button>';
                        html += '</div>';
                        html += '</div>';
                    } else if (f.socraticProbe && f.probeAnswered) {
                        html += '<div class="companion-panel mt-sm" style="opacity:.6">';
                        html += '<div class="companion-header flex items-center gap-sm">';
                        html += '<span style="color:var(--vscode-testing-iconPassed)">&#x2714;</span>';
                        html += '<span class="text-sm"><strong>Probe Addressed</strong></span>';
                        html += '</div>';
                        html += '<div class="companion-body text-sm text-muted">' + esc(f.socraticProbe.question) + '</div>';
                        html += '</div>';
                    } else if (f.socraticProbe && f.probeCollapsed) {
                        html += '<div class="mt-xs"><button class="btn btn-sm btn-secondary text-xs" data-action="toggleProbe" data-key="' + f.key + '">Show Probe</button></div>';
                    }

                    html += '</div>'; // section-body
                    html += '</div>'; // section-panel
                }

                _isRendering = true;
                container.innerHTML = html;

                // Restore focus to the textarea that was active
                if (activeKey) {
                    var ta = container.querySelector('textarea[data-key="' + activeKey + '"]');
                    if (ta) {
                        ta.focus();
                        ta.selectionStart = selStart;
                        ta.selectionEnd = selEnd;
                        ta.scrollTop = scrollTop;
                    }
                }
                _isRendering = false;

                // Edit button visibility
                var editToggle = document.getElementById('edit-toggle');
                if (editToggle) {
                    if (_readOnly) { editToggle.classList.remove('hidden'); }
                    else { editToggle.classList.add('hidden'); }
                    editToggle.textContent = _readOnly ? 'Edit' : 'Done Editing';
                }

                // Suggest Topics button: hide only in read-only mode (always available during creation)
                var suggestBtn = document.getElementById('suggest-topics-btn');
                if (suggestBtn) {
                    if (_readOnly) { suggestBtn.classList.add('hidden'); }
                    else { suggestBtn.classList.remove('hidden'); }
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
                if (focusLabel && _fields[_focusIndex]) {
                    focusLabel.textContent = (_focusIndex + 1) + ' / ' + _fields.length + ' — ' + (_fields[_focusIndex].heading || '');
                }
                var focusPrev = document.getElementById('focus-prev');
                var focusNext = document.getElementById('focus-next');
                if (focusPrev) focusPrev.disabled = _focusIndex <= 0;
                if (focusNext) focusNext.disabled = _focusIndex >= _fields.length - 1;

                // Save bar
                var hasContent = _fields.filter(function(f) { return f.content; }).length;
                if (saveBar) {
                    if (!_readOnly && hasContent > 0) { saveBar.classList.remove('hidden'); }
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
                if ((el = e.target.closest('[data-action="acceptField"]'))) {
                    var ta = document.querySelector('textarea[data-key="' + el.dataset.key + '"]');
                    if (ta) { vscode.postMessage({ command: 'editField', fieldKey: el.dataset.key, content: ta.value }); }
                    vscode.postMessage({ command: 'acceptField', fieldKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="validateField"]'))) {
                    var ta2 = document.querySelector('textarea[data-key="' + el.dataset.key + '"]');
                    if (ta2) { vscode.postMessage({ command: 'editField', fieldKey: el.dataset.key, content: ta2.value }); }
                    vscode.postMessage({ command: 'validateField', fieldKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="chatAboutField"]'))) {
                    vscode.postMessage({ command: 'chatAboutField', fieldKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="respondToProbe"]'))) {
                    var input = document.getElementById('probe-input-' + el.dataset.key);
                    var answer = input ? input.value.trim() : '';
                    if (answer) { vscode.postMessage({ command: 'respondToProbe', fieldKey: el.dataset.key, answer: answer }); }
                    return;
                }
                if ((el = e.target.closest('[data-action="toggleProbe"]'))) {
                    vscode.postMessage({ command: 'toggleProbe', fieldKey: el.dataset.key });
                    return;
                }
                if ((el = e.target.closest('[data-action="saveADR"]'))) {
                    vscode.postMessage({ command: 'saveADR' });
                    return;
                }
                if ((el = e.target.closest('[data-action="suggestTopics"]'))) {
                    vscode.postMessage({ command: 'suggestTopics' });
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
                    renderFields();
                    return;
                }
                if ((el = e.target.closest('[data-action="focusPrev"]'))) {
                    if (_focusIndex > 0) { _focusIndex--; renderFields(); }
                    return;
                }
                if ((el = e.target.closest('[data-action="focusNext"]'))) {
                    if (_focusIndex < _fields.length - 1) { _focusIndex++; renderFields(); }
                    return;
                }
            });

            // Save textarea edits on blur → validate
            document.addEventListener('focusout', function(e) {
                if (_isRendering) return; // Don't trigger validation during re-render
                if (e.target && e.target.classList && e.target.classList.contains('section-textarea')) {
                    var key = e.target.dataset.key;
                    var content = e.target.value;
                    vscode.postMessage({ command: 'editField', fieldKey: key, content: content });
                    // Trigger validation on blur (moving to next field)
                    if (content.trim() && content.trim().length > 10) {
                        vscode.postMessage({ command: 'validateField', fieldKey: key });
                    }
                }
            });

            // Validate on "." typed (sentence completion) OR after debounce on meaningful input
            document.addEventListener('input', function(e) {
                if (e.target && e.target.classList && e.target.classList.contains('section-textarea')) {
                    var key = e.target.dataset.key;
                    var content = e.target.value;
                    // Save content
                    vscode.postMessage({ command: 'editField', fieldKey: key, content: content });
                    // Debounced validation: fire on "." at end OR after 2s of inactivity with meaningful content
                    if (_debounceTimers[key]) clearTimeout(_debounceTimers[key]);
                    if (content.endsWith('.') || content.endsWith('?') || content.endsWith('!')) {
                        _debounceTimers[key] = setTimeout(function() {
                            if (content.trim().length > 10) {
                                vscode.postMessage({ command: 'validateField', fieldKey: key });
                            }
                        }, 600);
                    } else if (content.trim().length > 20) {
                        _debounceTimers[key] = setTimeout(function() {
                            vscode.postMessage({ command: 'validateField', fieldKey: key });
                        }, 2000);
                    }
                }
            });

            // Enter in probe input = respond
            document.addEventListener('keydown', function(e) {
                if (e.target && e.target.classList && e.target.classList.contains('probe-input') && e.key === 'Enter') {
                    var key = e.target.id.replace('probe-input-', '');
                    var answer = e.target.value.trim();
                    if (answer) { vscode.postMessage({ command: 'respondToProbe', fieldKey: key, answer: answer }); }
                }
            });

            // Auto-resize textareas to fit content
            function autoResize(el) {
                el.style.height = 'auto';
                el.style.height = el.scrollHeight + 'px';
            }
            document.addEventListener('input', function(e) {
                if (e.target && e.target.classList.contains('section-textarea')) {
                    autoResize(e.target);
                }
            });
            var _origRenderFields = renderFields;
            renderFields = function() {
                _origRenderFields();
                var tas = document.querySelectorAll('.section-textarea');
                for (var t = 0; t < tas.length; t++) { autoResize(tas[t]); }
            };

            renderFields();
        `;
    }
}
