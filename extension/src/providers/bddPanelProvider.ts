// ---------------------------------------------------------------------------
// Sdlicit — BDD Panel Provider (Gherkin Scenario Cards with Socratic Probes)
// ---------------------------------------------------------------------------
// WebviewPanel that renders generated BDD scenarios progressively.
// Per-scenario card: expandable Gherkin, accept/reject verdict, importance.
// Socratic probes with Respond/Chat, KB facts accordion.
// WIP persistence via VS Code globalStorageUri.
// Saves per-requirement .feature files.
// Mirrors Stories panel UX for consistency.
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

export interface BddScenarioCard {
    id: string;
    title: string;
    gherkin: string;
    requirement_id: string;
    status: 'pending' | 'generating' | 'accepted' | 'rejected';
    importance?: 'critical' | 'important' | 'nice-to-have';
    reviewNote?: string;
}

export interface BddSocraticState {
    probe?: SocraticProbe;
    probeAnswered: boolean;
    probeCollapsed: boolean;
    needsSocratic: boolean;
    socraticReason: string;
}

export class BddPanelProvider {
    private panel: vscode.WebviewPanel | undefined;
    private scenarios: BddScenarioCard[] = [];
    private socratic: BddSocraticState = { probeAnswered: false, probeCollapsed: false, needsSocratic: false, socraticReason: '' };
    private isGenerating = false;
    private readOnly = false;
    private clarifications: Clarification[] = [];
    private srsContent = '';
    private personas: string[] = [];
    private artifactPath: string | undefined;
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

    /** Start BDD generation from SRS + personas. */
    async startGeneration(srsContent: string, personas: string[]): Promise<'accepted' | 'declined'> {
        this.srsContent = srsContent;
        this.personas = personas;

        // Prompt user if WIP data exists
        let wip: { scenarios: BddScenarioCard[]; clarifications: Clarification[]; socratic: BddSocraticState } | null = null;
        if (this.globalStoragePath) {
            const wipMgr = new WipManager(this.globalStoragePath);
            const decision = await wipMgr.promptIfWipExists('bdd');
            if (decision === 'resume') {
                wip = this.loadWip();
            } else {
                this.deleteWip();
            }
        } else {
            wip = this.loadWip();
        }
        if (wip) {
            this.scenarios = wip.scenarios;
            this.clarifications = wip.clarifications;
            this.socratic = wip.socratic;
        } else {
            this.scenarios = [];
        }
        this.isGenerating = !wip;

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.bddPanel',
            'Sdlicit — BDD Scenarios',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        this.panel.onDidDispose(() => {
            this.saveWip();
            this.panel = undefined;
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
                vscode.window.showErrorMessage(`Sdlicit: BDD generation failed — ${err.message}`);
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

    /** Load existing .feature content into the panel (read-only). */
    async openExisting(gherkinContent: string, filePath?: string): Promise<void> {
        this.readOnly = true;
        this.isGenerating = false;
        this.artifactPath = filePath;
        this.parseGherkinIntoState(gherkinContent);

        this.panel = vscode.window.createWebviewPanel(
            'sdlicit.bddPanel',
            'Sdlicit — BDD Scenarios',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true },
        );
        this.panel.onDidDispose(() => { this.panel = undefined; });

        this.renderHtml();
        this.setupMessageHandler();
        this.updatePanel();
    }

    /** Call the backend gherkin endpoint. */
    private async generate(): Promise<void> {
        this.updatePanel();

        const result: GenerationResponse = await this.client.generateGherkin(
            this.projectDir, this.personas, this.srsContent, this.clarifications,
        );

        // Handle socratic probe
        if (result.socratic_probe) {
            this.socratic.probe = result.socratic_probe;
            this.socratic.probeAnswered = false;
            this.socratic.probeCollapsed = false;
            this.socratic.needsSocratic = true;
            this.socratic.socraticReason = result.socratic_probe.question;
            this.updatePanel();
            return;
        }

        // Parse gherkin response into scenarios
        const gherkinText = typeof (result as any).gherkin === 'string' ? (result as any).gherkin :
            typeof (result as any).gherkin_markdown === 'string' ? (result as any).gherkin_markdown :
            typeof (result as any).content === 'string' ? (result as any).content : '';

        if (gherkinText) {
            this.parseGherkinIntoScenarios(gherkinText);
            this.updatePanel();
        }
    }

    /** Parse a full Gherkin response into scenario cards, splitting by Feature blocks for multi-file support. */
    private parseGherkinIntoScenarios(gherkin: string): void {
        // Split into feature blocks if multiple Feature: exist
        const featureBlocks = gherkin.split(/(?=^@|\n@|\nFeature:)/m).filter(b => b.trim());

        // If only one block or no Feature: separator, parse as single feature
        if (featureBlocks.length <= 1 || !gherkin.includes('Feature:')) {
            const newScenarios = this.parseGherkinBlock(gherkin, '');
            this.scenarios.push(...newScenarios);
            return;
        }

        // Multiple feature blocks — extract feature name from each
        for (const block of featureBlocks) {
            const featureMatch = block.match(/Feature:\s*(.+)/);
            const featureName = featureMatch?.[1]?.trim() || '';
            const newScenarios = this.parseGherkinBlock(block, featureName);
            this.scenarios.push(...newScenarios);
        }
    }

    /** Extract a persona name from persona content (markdown or JSON). */
    private extractPersonaName(personaText: string): string {
        if (!personaText) { return ''; }
        // If it looks like JSON, parse and extract name
        const trimmed = personaText.trim();
        if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
            try {
                const parsed = JSON.parse(trimmed);
                // Handle { personas: [{name: ...}] } or [{name: ...}]
                const personas = Array.isArray(parsed) ? parsed : (parsed.personas || [parsed]);
                if (personas[0]?.name) { return personas[0].name; }
                if (personas[0]?.persona_id) { return personas[0].persona_id; }
            } catch { /* not valid JSON, try as text */ }
            return '';
        }
        // Try ## heading
        const headingMatch = personaText.match(/^##\s+(.+)$/m);
        if (headingMatch) { return headingMatch[1].trim(); }
        // Try "Name:" field
        const nameMatch = personaText.match(/\*\*Name\*\*[:\s]*(.+)|^Name[:\s]*(.+)/im);
        if (nameMatch) { return (nameMatch[1] || nameMatch[2]).trim(); }
        // First non-empty line (but NOT if it starts with { or [)
        const firstLine = personaText.split('\n').find(l => l.trim() && !l.trim().startsWith('{') && !l.trim().startsWith('['));
        return firstLine?.replace(/^#+\s*/, '').trim() || '';
    }

    /** Parse a gherkin block into scenario cards, tagging with persona. */
    private parseGherkinBlock(gherkin: string, groupLabel: string): BddScenarioCard[] {
        const scenarios: BddScenarioCard[] = [];
        const blocks = gherkin.split(/(?=\s*Scenario(?:\s+Outline)?:)/);

        // Extract all requirement IDs mentioned anywhere in this block
        const allReqIds = [...gherkin.matchAll(/@?((?:FR|NFR|REQ)-[\w-]+\d+)/gi)].map(m => m[1]);
        const defaultReqId = allReqIds[0] || '';

        for (const block of blocks) {
            const trimmed = block.trim();
            if (!trimmed.match(/^Scenario(?:\s+Outline)?:/)) { continue; }

            const titleMatch = trimmed.match(/^Scenario(?:\s+Outline)?:\s*(.+)$/m);
            const title = titleMatch?.[1]?.trim() ?? 'Untitled Scenario';

            // Try to extract requirement ID from tags above the scenario
            let reqId = '';
            const tagMatch = block.match(/@((?:FR|NFR|REQ)-[\w-]+\d+)/i);
            if (tagMatch) {
                reqId = tagMatch[1];
            }
            // Try to match requirement IDs from the scenario text itself
            if (!reqId) {
                const inlineMatch = trimmed.match(/\b((?:FR|NFR|REQ)-[\w-]+\d+)\b/i);
                if (inlineMatch) { reqId = inlineMatch[1]; }
            }

            // Try to find matching req from SRS content
            if (!reqId && this.srsContent) {
                reqId = this.findMatchingReqId(title) || defaultReqId;
            }

            scenarios.push({
                id: `scn-${this.scenarios.length + scenarios.length + 1}`,
                title,
                gherkin: trimmed,
                requirement_id: reqId || 'general',
                status: 'pending',
            });
        }

        return scenarios;
    }

    /** Try to find a matching requirement ID from SRS content based on scenario title keywords. */
    private findMatchingReqId(scenarioTitle: string): string {
        if (!this.srsContent) { return ''; }
        // Extract all req definitions from SRS: [REQ-xxx] description
        const reqDefs = [...this.srsContent.matchAll(/\[((?:FR|NFR|REQ)-[\w-]+\d+)\]\s*(.+)/gi)];
        if (reqDefs.length === 0) { return ''; }

        // Simple keyword matching: find the req whose description shares the most words with the scenario title
        const titleWords = new Set(scenarioTitle.toLowerCase().split(/\W+/).filter(w => w.length > 3));
        let bestMatch = '';
        let bestScore = 0;

        for (const [, reqId, desc] of reqDefs) {
            const descWords = new Set(desc.toLowerCase().split(/\W+/).filter(w => w.length > 3));
            let score = 0;
            for (const w of titleWords) {
                if (descWords.has(w)) { score++; }
            }
            if (score > bestScore) {
                bestScore = score;
                bestMatch = reqId;
            }
        }

        return bestScore >= 2 ? bestMatch : '';
    }

    /** Parse Gherkin feature text into scenario cards. */
    private parseGherkinIntoState(gherkin: string): void {
        const scenarios: BddScenarioCard[] = [];
        // Split by Scenario: or Scenario Outline:
        const blocks = gherkin.split(/(?=\s*Scenario(?:\s+Outline)?:)/);

        // Extract all requirement IDs from the text
        const allReqIds = [...gherkin.matchAll(/@?((?:FR|NFR|REQ)-[\w-]+\d+)/gi)].map(m => m[1]);
        const defaultReqId = allReqIds[0] || '';

        for (const block of blocks) {
            const trimmed = block.trim();
            if (!trimmed.match(/^Scenario(?:\s+Outline)?:/)) { continue; }

            const titleMatch = trimmed.match(/^Scenario(?:\s+Outline)?:\s*(.+)$/m);
            const title = titleMatch?.[1]?.trim() ?? 'Untitled Scenario';

            // Try to extract requirement ID from tags above the scenario
            let reqId = '';
            const tagMatch = block.match(/@((?:FR|NFR|REQ)-[\w-]+\d+)/i);
            if (tagMatch) {
                reqId = tagMatch[1];
            }
            if (!reqId) {
                const inlineMatch = trimmed.match(/\b((?:FR|NFR|REQ)-[\w-]+\d+)\b/i);
                if (inlineMatch) { reqId = inlineMatch[1]; }
            }
            if (!reqId && this.srsContent) {
                reqId = this.findMatchingReqId(title) || defaultReqId;
            }

            scenarios.push({
                id: `scn-${scenarios.length + 1}`,
                title,
                gherkin: trimmed,
                requirement_id: reqId || 'general',
                status: this.readOnly ? 'accepted' : 'pending',
            });
        }

        if (scenarios.length > 0) {
            this.scenarios = scenarios;
        }
    }

    // -- WIP persistence -------------------------------------------------------

    private get wipPath(): string | undefined {
        if (!this.globalStoragePath) { return undefined; }
        return path.join(this.globalStoragePath, 'wip', 'wip_bdd.json');
    }

    private saveWip(): void {
        if (this.readOnly) { return; }
        const wp = this.wipPath;
        if (!wp) { return; }
        const dir = path.dirname(wp);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        const data = {
            scenarios: this.scenarios,
            clarifications: this.clarifications,
            socratic: this.socratic,
            savedAt: new Date().toISOString(),
        };
        fs.writeFileSync(wp, JSON.stringify(data, null, 2), 'utf-8');
    }

    private loadWip(): { scenarios: BddScenarioCard[]; clarifications: Clarification[]; socratic: BddSocraticState } | null {
        const wp = this.wipPath;
        if (!wp || !fs.existsSync(wp)) { return null; }
        try {
            const raw = JSON.parse(fs.readFileSync(wp, 'utf-8'));
            if (raw.scenarios?.length > 0) { return raw; }
        } catch { /* ignore */ }
        return null;
    }

    private deleteWip(): void {
        const wp = this.wipPath;
        if (wp && fs.existsSync(wp)) { fs.unlinkSync(wp); }
    }

    // -- Panel messaging -------------------------------------------------------

    private updatePanel(): void {
        if (!this.panel) { return; }

        // Collect unique requirement IDs from scenarios for trace links
        const reqIds = [...new Set(
            this.scenarios
                .map(s => s.requirement_id)
                .filter(id => id && id !== 'general' && this.isReqId(id))
        )];

        this.panel.webview.postMessage({
            command: 'updateState',
            scenarios: this.scenarios,
            socratic: this.socratic,
            isGenerating: this.isGenerating,
            readOnly: this.readOnly,
            traceLinks: { implements: reqIds },
        });
    }

    private setupMessageHandler(): void {
        if (!this.panel) { return; }

        this.panel.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.command) {
                case 'openArtifact': {
                    if (msg.artifactId) {
                        vscode.commands.executeCommand('sdlicit.openCanvas', msg.artifactId);
                    }
                    break;
                }
                case 'acceptScenario': {
                    const idx = msg.index as number;
                    if (idx >= 0 && idx < this.scenarios.length) {
                        this.scenarios[idx].status = 'accepted';
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'rejectScenario': {
                    const idx = msg.index as number;
                    if (idx >= 0 && idx < this.scenarios.length) {
                        this.scenarios[idx].status = 'rejected';
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'setImportance': {
                    const idx = msg.index as number;
                    if (idx >= 0 && idx < this.scenarios.length) {
                        this.scenarios[idx].importance = msg.importance;
                        this.updatePanel();
                        this.saveWip();
                    }
                    break;
                }
                case 'editScenario': {
                    const idx = msg.index as number;
                    const field = msg.field as string;
                    if (idx >= 0 && idx < this.scenarios.length) {
                        const s = this.scenarios[idx];
                        if (field === 'title') { s.title = msg.value; }
                        else if (field === 'gherkin') { s.gherkin = msg.value; }
                        else if (field === 'requirement_id') { s.requirement_id = msg.value; }
                        this.saveWip();
                    }
                    break;
                }
                case 'acceptAll': {
                    for (const s of this.scenarios) {
                        if (s.status === 'pending') { s.status = 'accepted'; }
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
                        const result = await this.client.generateGherkin(
                            this.projectDir, this.personas, this.srsContent, this.clarifications,
                        );

                        const gherkinText = typeof (result as any).gherkin === 'string' ? (result as any).gherkin :
                            typeof (result as any).gherkin_markdown === 'string' ? (result as any).gherkin_markdown : '';

                        if (gherkinText) {
                            const accepted = this.scenarios.filter(s => s.status === 'accepted');
                            const acceptedTitles = new Set(accepted.map(s => s.title));
                            this.parseGherkinIntoState(gherkinText);
                            // Preserve already-accepted scenarios, merge new ones
                            const newScenarios = this.scenarios.filter(s => !acceptedTitles.has(s.title));
                            this.scenarios = [...accepted, ...newScenarios];
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
                        vscode.window.showErrorMessage(`BDD regeneration failed: ${err.message}`);
                    }

                    this.updatePanel();
                    this.saveWip();
                    break;
                }
                case 'chatAboutSection': {
                    const context = `**BDD Scenarios**\n\n` +
                        this.scenarios.map(s => `- ${s.title} (${s.requirement_id})`).join('\n');
                    const probeQ = this.socratic.probe?.question ?? '';
                    const fullContext = probeQ ? `${context}\n\nSocratic probe: ${probeQ}` : context;

                    vscode.commands.executeCommand('sdlicit.chatWithContext', {
                        panelId: 'bdd',
                        panelType: 'bdd',
                        sectionKey: 'scenarios',
                        sectionHeading: 'BDD Scenarios',
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
                    const accepted = this.scenarios.filter(s => s.status === 'accepted');
                    if (accepted.length === 0) {
                        vscode.window.showWarningMessage('Sdlicit: Accept at least one scenario to save.');
                        break;
                    }

                    // Validate each scenario's Gherkin before saving
                    const validationIssues: string[] = [];
                    for (let i = 0; i < accepted.length; i++) {
                        const featureContent = this.buildSingleFeatureFile(accepted[i], i);
                        try {
                            const result = await this.client.validateGherkin(featureContent);
                            if (!result.valid) {
                                validationIssues.push(`${accepted[i].title}: ${result.issues.join('; ')}`);
                            }
                        } catch {
                            // Validation endpoint unavailable — skip
                        }
                    }
                    if (validationIssues.length > 0) {
                        const proceed = await vscode.window.showWarningMessage(
                            `Gherkin validation issues:\n${validationIssues.join('\n')}\nSave anyway?`,
                            'Save anyway', 'Cancel',
                        );
                        if (proceed !== 'Save anyway') { break; }
                    }

                    // Save one .feature file per accepted scenario
                    const savedFiles: string[] = [];
                    for (let i = 0; i < accepted.length; i++) {
                        const featureContent = this.buildSingleFeatureFile(accepted[i], i);
                        const filename = this.buildFeatureFilename(accepted[i], i);

                        const filePath = this.store.saveByMeta(
                            { tag: `BDD-${i + 1}`, filename, relative_path: `bdd/${filename}`, artifact_type: 'bdd' },
                            featureContent,
                        );
                        savedFiles.push(filePath);
                    }

                    vscode.window.showInformationMessage(
                        `Sdlicit: Saved ${accepted.length} scenario(s) as ${savedFiles.length} individual .feature file(s).`
                    );

                    // Refresh artifact tree
                    vscode.commands.executeCommand('sdlicit.refresh');

                    this.deleteWip();
                    this.panel?.dispose();
                    if (this.resolvePromise) {
                        this.resolvePromise('accepted');
                        this.resolvePromise = undefined;
                    }

                    // Async ingestion — one per file
                    (async () => {
                        for (let i = 0; i < accepted.length; i++) {
                            const featureContent = this.buildSingleFeatureFile(accepted[i], i);
                            const filename = this.buildFeatureFilename(accepted[i], i);
                            const artifactId = filename.replace('.feature', '');
                            vscode.commands.executeCommand('sdlicit.markArtifactIngesting', artifactId);
                            try {
                                await this.client.ingestArtifact(featureContent, 'gherkin', artifactId);
                                vscode.commands.executeCommand('sdlicit.markArtifactIngested', artifactId);
                            } catch {
                                vscode.commands.executeCommand('sdlicit.markArtifactIngestError', artifactId);
                            }
                        }
                        vscode.window.showInformationMessage(
                            `Sdlicit: ${accepted.length} BDD scenario(s) ingested into KB.`
                        );
                    })();
                    break;
                }
                case 'regenerate': {
                    this.deleteWip();
                    this.scenarios = [];
                    this.clarifications = [];
                    this.socratic = { probeAnswered: false, probeCollapsed: false, needsSocratic: false, socraticReason: '' };
                    this.isGenerating = true;
                    this.updatePanel();
                    this.generate().then(() => {
                        this.isGenerating = false;
                        this.updatePanel();
                        this.saveWip();
                    }).catch((err: any) => {
                        this.isGenerating = false;
                        this.updatePanel();
                        vscode.window.showErrorMessage(`Sdlicit: BDD regeneration failed — ${err.message}`);
                    });
                    break;
                }
                case 'toggleEdit': {
                    this.readOnly = !this.readOnly;
                    if (!this.readOnly) {
                        for (const s of this.scenarios) {
                            if (s.status === 'accepted') { s.status = 'pending'; }
                        }
                    }
                    this.updatePanel();
                    break;
                }
            }
        });
    }

    /** Sanitize a string into a valid Gherkin tag (no spaces, alphanumeric + hyphens). */
    private sanitizeTag(raw: string): string {
        return raw.replace(/[^a-zA-Z0-9_-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
    }

    /** Check if a string looks like a proper requirement ID (e.g., FR-01, REQ-DATA-01, NFR-03). */
    private isReqId(s: string): boolean {
        return /^(FR|NFR|REQ)-[\w-]+\d+$/i.test(s);
    }

    /**
     * Build a single .feature file for ONE scenario with comment-based frontmatter.
     * Format:
     *   # --- sdlicit frontmatter ---
     *   # title: <scenario title>
     *   # traces_from: <requirement id>
     *   # linked_stories: <story ids>
     *   # generated_at: <ISO date>
     *   # generated_by: sdlicit
     *   # importance: <critical|important|nice-to-have>
     *   # artifact_type: bdd
     *   # ---
     *   @REQ-ID @importance
     *   Feature: <scenario title>
     *     <description>
     *
     *     Scenario: <title>
     *       Given ...
     *       When ...
     *       Then ...
     */
    private buildSingleFeatureFile(scenario: BddScenarioCard, index: number): string {
        const lines: string[] = [];
        const now = new Date().toISOString();
        const reqId = scenario.requirement_id || 'unlinked';

        // Comment-based frontmatter
        lines.push('# --- sdlicit frontmatter ---');
        lines.push(`# title: ${scenario.title}`);
        lines.push(`# traces_from: ${reqId}`);
        if (scenario.importance) {
            lines.push(`# importance: ${scenario.importance}`);
        }
        lines.push(`# generated_at: ${now}`);
        lines.push(`# generated_by: sdlicit`);
        lines.push(`# artifact_type: bdd`);
        lines.push(`# sequence: ${index + 1}`);
        lines.push('# ---');
        lines.push('');

        // Tags
        const tags: string[] = [];
        if (reqId && reqId !== 'general' && reqId !== 'unlinked') {
            const tag = this.isReqId(reqId) ? reqId : this.sanitizeTag(reqId);
            if (tag) { tags.push(`@${tag}`); }
        }
        if (scenario.importance) {
            tags.push(`@${scenario.importance}`);
        }
        if (tags.length > 0) {
            lines.push(tags.join(' '));
        }

        // Feature line — one feature per file with a descriptive name
        lines.push(`Feature: ${scenario.title}`);
        lines.push(`  Verifies: ${reqId}`);
        lines.push('');

        // Scenario body — use the raw gherkin if it includes the Scenario keyword
        if (scenario.gherkin.includes('Scenario')) {
            // Indent the scenario block properly
            const scenarioLines = scenario.gherkin.split('\n');
            for (const sl of scenarioLines) {
                lines.push(`  ${sl}`);
            }
        } else {
            lines.push(`  Scenario: ${scenario.title}`);
            // Indent the step lines
            const stepLines = scenario.gherkin.split('\n');
            for (const sl of stepLines) {
                lines.push(`    ${sl.trim() ? sl.trimStart() : sl}`);
            }
        }
        lines.push('');

        return lines.join('\n');
    }

    /** Generate the filename for a scenario: BDD-{number}-{slug}.feature */
    private buildFeatureFilename(scenario: BddScenarioCard, index: number): string {
        const num = String(index + 1).padStart(3, '0');
        const slug = scenario.title
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-|-$/g, '')
            .slice(0, 50);
        return `BDD-${num}-${slug}.feature`;
    }

    // -- HTML rendering --------------------------------------------------------

    private renderHtml(): void {
        if (!this.panel) { return; }
        const nonce = getNonce();

        const body = `
            <div id="bdd-root">
                <div class="flex items-center justify-between mb-md">
                    <h1 style="margin-bottom:0">BDD Scenarios</h1>
                    <div class="flex gap-xs">
                        <button class="btn btn-secondary btn-sm" data-action="regenerate" id="regenerate-btn" title="Regenerate scenarios">Regenerate</button>
                        <button class="btn btn-primary btn-sm hidden" data-action="toggleEdit" id="edit-toggle">Edit</button>
                    </div>
                </div>
                <div id="trace-links-container"></div>
                <div id="socratic-container"></div>
                <div id="scenarios-container"></div>
                <div id="save-bar" class="mt-lg hidden">
                    <button class="btn btn-primary" data-action="saveAndIngest">Save &amp; Ingest to KB</button>
                </div>
                <div id="generating-indicator" class="mt-md hidden">
                    <span class="spinner"></span>
                    <span class="text-sm">Generating BDD scenarios…</span>
                </div>
            </div>
        `;

        const scripts = this.buildScripts();
        this.panel.webview.html = wrapHtml(body, nonce, scripts);
    }

    private buildScripts(): string {
        return `
            var _scenarios = [];
            var _socratic = {};
            var _isGenerating = false;
            var _readOnly = false;
            var _probeLoading = false;
            var _traceLinks = { implements: [] };

            window.addEventListener('message', function(event) {
                var msg = event.data;
                if (msg.command === 'updateState') {
                    _scenarios = msg.scenarios;
                    _socratic = msg.socratic;
                    _isGenerating = msg.isGenerating;
                    _readOnly = msg.readOnly || false;
                    _traceLinks = msg.traceLinks || { implements: [] };
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

            function highlightContent(text) {
                // Highlight "quoted strings" and <parameters> within step text
                var result = esc(text);
                // Quoted strings
                result = result.replace(/&quot;([^&]*?)&quot;|"([^"]*?)"/g, function(m, a, b) {
                    var inner = a !== undefined ? a : b;
                    return '<span class="gherkin-string">"' + inner + '"</span>';
                });
                // <angle bracket params>
                result = result.replace(/&lt;([^&]+?)&gt;/g, function(m, inner) {
                    return '<span class="gherkin-param">&lt;' + inner + '&gt;</span>';
                });
                return result;
            }

            function formatGherkin(text) {
                var lines = text.split('\\n');
                var html = '';
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i];
                    var t = line.trim();
                    var cls = 'gherkin-line';
                    if (t.startsWith('Feature:')) cls += ' gherkin-feature';
                    else if (t.startsWith('Scenario:') || t.startsWith('Scenario Outline:')) cls += ' gherkin-scenario';
                    else if (t.startsWith('Given ') || t.startsWith('When ') || t.startsWith('Then ') || t.startsWith('And ') || t.startsWith('But ')) cls += ' gherkin-step';
                    else if (t.startsWith('Background:') || t.startsWith('Examples:')) cls += ' gherkin-scenario';
                    else if (t.startsWith('|')) cls += ' gherkin-table';
                    else if (t.startsWith('#')) cls += ' gherkin-comment';
                    else if (t.startsWith('@')) cls += ' gherkin-tag';

                    // Bold the keyword and highlight content
                    var kwMatch = t.match(/^(Feature:|Scenario Outline:|Scenario:|Given|When|Then|And|But|Background:|Examples:)/);
                    if (kwMatch) {
                        var afterKw = line.substring(line.indexOf(kwMatch[1]) + kwMatch[1].length);
                        html += '<div class="' + cls + '"><span class="gherkin-keyword">' + kwMatch[1] + '</span>' + highlightContent(afterKw) + '</div>';
                    } else if (t.startsWith('|')) {
                        // Highlight table separators
                        var tableHtml = esc(line).replace(/\\|/g, '<span class="gherkin-cell-sep">|</span>');
                        html += '<div class="' + cls + '">' + tableHtml + '</div>';
                    } else if (t.startsWith('@')) {
                        html += '<div class="' + cls + '">' + esc(line) + '</div>';
                    } else {
                        html += '<div class="' + cls + '">' + highlightContent(line) + '</div>';
                    }
                }
                return html;
            }

            function renderTraceLinks() {
                var container = document.getElementById('trace-links-container');
                if (!container) return;
                var impls = _traceLinks.implements || [];
                if (impls.length === 0) { container.innerHTML = ''; return; }

                var html = '<div class="card-flat mb-md" style="padding:10px 14px;border-left:3px solid var(--vscode-textLink-foreground)">';
                html += '<div class="text-xs text-muted mb-xs"><strong>Traceability Links</strong></div>';
                html += '<div class="flex items-center gap-sm flex-wrap">';
                html += '<span class="text-xs text-muted">Implements:</span>';
                for (var i = 0; i < impls.length; i++) {
                    html += '<span class="trace-node clickable" data-artifact-id="' + esc(impls[i]) + '" title="Click to navigate" style="cursor:pointer;padding:2px 8px;border-radius:4px;font-size:.8em;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(impls[i]) + '</span>';
                }
                html += '</div>';
                html += '</div>';
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
                }

                container.innerHTML = html;
            }

            function renderScenarios() {
                var container = document.getElementById('scenarios-container');
                var html = '';

                var acceptedCount = _scenarios.filter(function(s) { return s.status === 'accepted'; }).length;
                var rejectedCount = _scenarios.filter(function(s) { return s.status === 'rejected'; }).length;
                var pendingCount = _scenarios.filter(function(s) { return s.status === 'pending'; }).length;
                var total = _scenarios.length;

                // Progress bar
                if (total > 0) {
                    var pct = Math.round((acceptedCount / total) * 100);
                    html += '<div class="flex items-center gap-sm mb-md">';
                    html += '<div class="progress-bar" style="flex:1"><div class="progress-fill" style="width:' + pct + '%"></div></div>';
                    html += '<span class="text-sm">' + acceptedCount + '/' + total + ' accepted</span>';
                    if (rejectedCount > 0) html += '<span class="text-xs text-muted">(' + rejectedCount + ' rejected)</span>';
                    html += '</div>';
                }

                // Accept all button
                if (!_readOnly && pendingCount > 0) {
                    html += '<div class="mb-sm"><button class="btn btn-primary btn-sm" data-action="acceptAll">Accept All Pending (' + pendingCount + ')</button></div>';
                }

                if (_isGenerating && _scenarios.length === 0) {
                    html += '<div class="flex items-center gap-sm"><span class="spinner" style="width:14px;height:14px"></span><span class="text-sm text-muted">Generating BDD scenarios…</span></div>';
                }

                for (var i = 0; i < _scenarios.length; i++) {
                    var s = _scenarios[i];
                    var isAccepted = s.status === 'accepted';
                    var isRejected = s.status === 'rejected';

                    var borderColor = isAccepted ? 'var(--vscode-testing-iconPassed, #4caf50)' :
                        isRejected ? 'var(--vscode-errorForeground, #f44336)' :
                        'var(--vscode-charts-blue, #2196f3)';

                    html += '<div class="req-card" style="border:1px solid var(--vscode-panel-border);border-left:4px solid ' + borderColor + ';border-radius:6px;padding:14px 18px;margin-bottom:16px;background:var(--vscode-editor-background);box-shadow:0 1px 3px rgba(0,0,0,.12);' + (isRejected ? 'opacity:.6' : '') + '">';

                    // Header
                    html += '<div class="flex items-center justify-between" style="margin-bottom:8px">';
                    html += '<div class="flex items-center gap-sm" style="flex-wrap:wrap;gap:8px">';
                    if (isAccepted) html += '<span style="color:var(--vscode-testing-iconPassed);font-size:1.1em">&#x2714;</span>';
                    if (isRejected) html += '<span style="color:var(--vscode-errorForeground);font-size:1.1em">&#x2718;</span>';
                    html += '<strong style="font-size:1em">' + esc(s.title) + '</strong>';
                    if (s.requirement_id) {
                        html += '<span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:.82em;font-weight:500;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)">' + esc(s.requirement_id) + '</span>';
                    }
                    if (s.importance) {
                        var impColor = s.importance === 'critical' ? 'var(--vscode-errorForeground)' : s.importance === 'important' ? 'var(--vscode-charts-orange)' : 'var(--vscode-foreground)';
                        html += '<span class="text-xs" style="color:' + impColor + '">' + esc(s.importance) + '</span>';
                    }
                    html += '</div>';

                    // Action buttons
                    if (!_readOnly && !isAccepted && !isRejected) {
                        html += '<div class="flex gap-xs">';
                        html += '<button class="btn btn-primary btn-sm" data-action="acceptScenario" data-idx="' + i + '" style="padding:2px 10px;font-size:.8em">Accept</button>';
                        html += '<button class="btn btn-secondary btn-sm" data-action="rejectScenario" data-idx="' + i + '" style="padding:2px 10px;font-size:.8em;color:var(--vscode-errorForeground)">Reject</button>';
                        html += '</div>';
                    }
                    html += '</div>';

                    // Gherkin block (collapsible)
                    html += '<details' + (s.status === 'pending' ? ' open' : '') + ' style="margin-bottom:10px">';
                    html += '<summary style="cursor:pointer;color:var(--vscode-textLink-foreground);font-size:.9em;user-select:none">View Gherkin</summary>';
                    html += '<div class="gherkin-formatted mt-xs" style="padding:8px 12px;border-radius:4px;background:var(--vscode-textCodeBlock-background);font-family:var(--vscode-editor-font-family);font-size:.85em;line-height:1.6;overflow-x:auto">' + formatGherkin(s.gherkin) + '</div>';
                    html += '</details>';

                    // Importance selector (for pending scenarios)
                    if (!_readOnly && !isRejected) {
                        html += '<div class="flex items-center gap-xs">';
                        html += '<span class="text-xs text-muted">Importance:</span>';
                        var imps = ['critical', 'important', 'nice-to-have'];
                        for (var imp = 0; imp < imps.length; imp++) {
                            var active = s.importance === imps[imp] ? 'btn-primary' : 'btn-secondary';
                            html += '<button class="btn btn-sm ' + active + '" data-action="setImportance" data-idx="' + i + '" data-importance="' + imps[imp] + '" style="padding:1px 8px;font-size:.75em">' + imps[imp] + '</button>';
                        }
                        html += '</div>';
                    }

                    html += '</div>';
                }

                container.innerHTML = html;
            }

            function render() {
                renderTraceLinks();
                renderSocratic();
                renderScenarios();

                var genIndicator = document.getElementById('generating-indicator');
                if (genIndicator) {
                    if (_isGenerating) genIndicator.classList.remove('hidden');
                    else genIndicator.classList.add('hidden');
                }

                var editToggle = document.getElementById('edit-toggle');
                var regenBtn = document.getElementById('regenerate-btn');
                var allDone = _scenarios.length > 0 && _scenarios.every(function(s) { return s.status === 'accepted' || s.status === 'rejected'; });
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
                    var hasAccepted = _scenarios.some(function(s) { return s.status === 'accepted'; });
                    if (!_isGenerating && hasAccepted && !_readOnly) saveBar.classList.remove('hidden');
                    else saveBar.classList.add('hidden');
                }
            }

            document.addEventListener('click', function(e) {
                var el;
                // Trace link navigation
                if ((el = e.target.closest('[data-artifact-id]'))) {
                    vscode.postMessage({ command: 'openArtifact', artifactId: el.dataset.artifactId });
                    return;
                }
                if ((el = e.target.closest('[data-action="acceptScenario"]'))) {
                    vscode.postMessage({ command: 'acceptScenario', index: parseInt(el.dataset.idx) });
                    return;
                }
                if ((el = e.target.closest('[data-action="rejectScenario"]'))) {
                    vscode.postMessage({ command: 'rejectScenario', index: parseInt(el.dataset.idx) });
                    return;
                }
                if ((el = e.target.closest('[data-action="setImportance"]'))) {
                    vscode.postMessage({ command: 'setImportance', index: parseInt(el.dataset.idx), importance: el.dataset.importance });
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
                if ((el = e.target.closest('[data-action="toggleEdit"]'))) {
                    vscode.postMessage({ command: 'toggleEdit' });
                    return;
                }
            });

            document.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && e.target && e.target.id === 'probe-input-main') {
                    var answer = e.target.value.trim();
                    if (answer) vscode.postMessage({ command: 'respondToProbe', answer: answer });
                }
            });
        `;
    }
}
