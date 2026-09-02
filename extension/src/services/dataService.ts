// ---------------------------------------------------------------------------
// Sdlicit — Data Service
// ---------------------------------------------------------------------------
// Central bridge between providers and data sources.
// - Local file I/O for artifacts and sessions (reads .sdlicit/ directly)
// - Backend calls for AI operations (Socratic, companion, BDD, KB, chat)
// - Computes dashboard stats (backend-driven for quality, local for counts)
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { SdlicitClient, SOWResponse, StepEventResponse, SuggestDirectionsResponse, ExpandResponse, RAGQueryResponse, SocraticResponse, SocraticProbe, GenerationResponse, Clarification, KBIngestEvent } from './sdlicitClient';
import {
    Artifact, ArtifactType, ArtifactStatus, ArtifactSection, QualityLevel,
    BddFeature, BddScenario, DashboardSummary, CoverageStats,
    OpenQuestion, ActivityEntry, KBSource, ExplorerResponse,
    ChatMode, ChatEntry, ElicitationResponse, CompanionObservation,
    SessionSummary, SessionMeta, SessionEvent, TokenUsage,
} from '../types';

const SDLICIT_DIR = '.sdlicit';
const ARTIFACTS_DIR = path.join(SDLICIT_DIR, 'artifacts');
const SESSIONS_DIR = path.join(SDLICIT_DIR, 'sessions');
const INDEX_FILE = path.join(SESSIONS_DIR, 'index.json');
const CHAT_DIR = path.join(SESSIONS_DIR, 'chat');
const META_DIR = path.join(SESSIONS_DIR, 'sdlicit');

export class DataService {
    private workspaceRoot: string | undefined;

    constructor(
        private readonly client: SdlicitClient,
    ) {
        this.workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    }

    get projectDir(): string | undefined {
        return this.workspaceRoot;
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // ARTIFACTS — Local File I/O
    // ═══════════════════════════════════════════════════════════════════════════

    /** List all artifacts from .sdlicit/artifacts/. */
    getArtifacts(): Artifact[] {
        if (!this.workspaceRoot) { return []; }
        const dir = path.join(this.workspaceRoot, ARTIFACTS_DIR);
        if (!fs.existsSync(dir)) { return []; }

        const artifacts: Artifact[] = [];
        this.walkArtifacts(dir, dir, artifacts);
        return artifacts.sort((a, b) => a.id.localeCompare(b.id));
    }

    /** Get a single artifact by ID. */
    getArtifact(id: string): Artifact | undefined {
        return this.getArtifacts().find(a => a.id === id);
    }

    /** Compute reverse traces for an artifact: find all other artifacts that reference it. */
    getReversTraces(artifactId: string): { implementedBy: string[]; testedBy: string[]; referencedBy: string[] } {
        const all = this.getArtifacts();
        const implementedBy: string[] = [];
        const testedBy: string[] = [];
        const referencedBy: string[] = [];

        // For SRS artifacts, also collect artifacts that implement any REQ-xxx within this artifact
        const artifact = all.find(a => a.id === artifactId);
        const reqIds = new Set<string>();
        if (artifact?.type === 'requirement') {
            const content = this.getArtifactContent(artifact.filePath);
            const matches = content.matchAll(/\[(REQ-[A-Z0-9]+-\d+)\]/g);
            for (const m of matches) { reqIds.add(m[1]); }
        }

        for (const a of all) {
            if (a.id === artifactId) { continue; }
            // Direct implements match on artifact ID
            if (a.traces.implements.includes(artifactId)) {
                implementedBy.push(a.id);
            }
            // Also check if this artifact implements any requirement defined in the SRS
            if (reqIds.size > 0 && a.traces.implements.some(ref => reqIds.has(ref))) {
                if (!implementedBy.includes(a.id)) { implementedBy.push(a.id); }
            }
            if (a.traces.testedBy.includes(artifactId)) {
                testedBy.push(a.id);
            }
            if (a.traces.upstream.includes(artifactId)) {
                referencedBy.push(a.id);
            }
        }
        return { implementedBy, testedBy, referencedBy };
    }

    /** Find all artifacts that implement a specific requirement ID (e.g. REQ-DATA-01). */
    getImplementors(reqId: string): Artifact[] {
        return this.getArtifacts().filter(a => a.traces.implements.includes(reqId));
    }

    /** Read raw content of an artifact file. */
    getArtifactContent(filePath: string): string {
        try {
            return fs.readFileSync(filePath, 'utf-8');
        } catch { return ''; }
    }

    /** Save (overwrite) an artifact file. */
    saveArtifact(filePath: string, content: string): void {
        const dir = path.dirname(filePath);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        fs.writeFileSync(filePath, content, 'utf-8');
    }

    /** Update a single section in an artifact by rewriting the file. */
    async updateSection(artifactId: string, sectionId: string, content: string): Promise<void> {
        const artifact = this.getArtifact(artifactId);
        if (!artifact) { return; }
        // Re-read file, find section heading, replace content
        const fileContent = this.getArtifactContent(artifact.filePath);
        const lines = fileContent.split('\n');
        const sectionHeading = `## ${sectionId}`;
        let startIdx = lines.findIndex(l => l.trim().startsWith('## ') && l.trim().toLowerCase().includes(sectionId.toLowerCase()));
        if (startIdx === -1) { return; }
        // Find next section heading
        let endIdx = lines.findIndex((l, i) => i > startIdx && l.trim().startsWith('## '));
        if (endIdx === -1) { endIdx = lines.length; }
        // Replace
        const newLines = [
            ...lines.slice(0, startIdx + 1),
            '',
            content,
            '',
            ...lines.slice(endIdx),
        ];
        this.saveArtifact(artifact.filePath, newLines.join('\n'));
    }

    private walkArtifacts(baseDir: string, dir: string, results: Artifact[]): void {
        let entries: fs.Dirent[];
        try { entries = fs.readdirSync(dir, { withFileTypes: true }); }
        catch { return; }
        for (const entry of entries) {
            const fullPath = path.join(dir, entry.name);
            if (entry.isDirectory()) {
                this.walkArtifacts(baseDir, fullPath, results);
            } else if (entry.name.endsWith('.md') || entry.name.endsWith('.feature')) {
                results.push(this.parseArtifactFile(fullPath, baseDir));
            }
        }
    }

    private parseArtifactFile(filePath: string, baseDir: string): Artifact {
        const content = this.getArtifactContent(filePath);
        const filename = path.basename(filePath);
        const relativePath = path.relative(baseDir, filePath);

        // Parse YAML frontmatter
        const frontmatter = this.parseFrontmatter(content);
        const sections = this.parseSections(content);
        const stat = fs.statSync(filePath);
        const artifactType = (frontmatter.type as ArtifactType) ?? this.inferType(filename, relativePath);

        // Build traces from frontmatter + content-level references
        const traces = this.buildTraces(frontmatter, content, artifactType);

        return {
            id: frontmatter.id ?? filename.replace(/\.(md|feature)$/, ''),
            type: artifactType,
            title: frontmatter.title ?? this.inferTitle(content, filename),
            status: (frontmatter.status as ArtifactStatus) ?? 'draft',
            quality: {
                target: frontmatter.quality_target as QualityLevel | undefined,
                current: frontmatter.quality as QualityLevel | undefined,
            },
            sections,
            traces,
            filePath,
            createdAt: frontmatter.created ?? stat.birthtime.toISOString(),
            updatedAt: stat.mtime.toISOString(),
        };
    }

    /** Build trace links from frontmatter metadata AND inline content references. */
    private buildTraces(frontmatter: Record<string, string | undefined>, content: string, type: ArtifactType): import('../types').ArtifactTraces {
        const upstream: string[] = frontmatter.traces_from ? String(frontmatter.traces_from).split(',').map(s => s.trim()) : [];
        const downstream: string[] = frontmatter.traces_to ? String(frontmatter.traces_to).split(',').map(s => s.trim()) : [];
        const implements_: string[] = this.parseYamlList(frontmatter.implements);
        const supersedes = frontmatter.supersedes ?? '';
        const testedBy: string[] = this.parseYamlList(frontmatter.tested_by);

        // BDD/scenario: parse # story and # adr from hash-comment metadata
        if (type === 'scenario') {
            if (frontmatter.story) {
                for (const ref of String(frontmatter.story).split(',').map(s => s.trim())) {
                    if (ref && !upstream.includes(ref)) { upstream.push(ref); }
                }
            }
            if (frontmatter.adr) {
                for (const ref of String(frontmatter.adr).split(',').map(s => s.trim())) {
                    if (ref && !upstream.includes(ref)) { upstream.push(ref); }
                }
            }
            // Parse @tags for requirement/ADR references (e.g., @FR-01, @REQ-DATA-01, @ADR-0001)
            const tagMatches = content.matchAll(/@((?:FR|NFR|REQ)-[\w-]+\d+|ADR-\d{1,4})/g);
            for (const m of tagMatches) {
                if (!implements_.includes(m[1]) && !upstream.includes(m[1])) {
                    implements_.push(m[1]);
                }
            }
        }

        // Stories: parse inline (refs: REQ-xxx, REQ-yyy) and (persona: PERSONA-xxx)
        if (type === 'stories') {
            const refMatches = content.matchAll(/refs?:\s*([^)]+)\)/g);
            for (const m of refMatches) {
                for (const ref of m[1].split(',').map(s => s.trim())) {
                    if (ref && !implements_.includes(ref)) { implements_.push(ref); }
                }
            }
            const personaMatches = content.matchAll(/persona:\s*([^,)]+)/g);
            for (const m of personaMatches) {
                const ref = m[1].trim();
                if (ref && !upstream.includes(ref)) { upstream.push(ref); }
            }
        }

        // ADR: also extract REQ-xxx references from ## References or content
        if (type === 'decision' && implements_.length === 0) {
            const reqRefs = content.matchAll(/\b(REQ-[A-Z0-9]+-\d+)\b/g);
            for (const m of reqRefs) {
                if (!implements_.includes(m[1])) { implements_.push(m[1]); }
            }
        }

        return { upstream, downstream, implements: implements_, supersedes, testedBy };
    }

    private parseFrontmatter(content: string): Record<string, string | undefined> {
        // YAML frontmatter (markdown files)
        const yamlMatch = content.match(/^---\n([\s\S]*?)\n---/);
        if (yamlMatch) {
            const result: Record<string, string> = {};
            for (const line of yamlMatch[1].split('\n')) {
                const sep = line.indexOf(':');
                if (sep > 0) {
                    const key = line.slice(0, sep).trim();
                    const val = line.slice(sep + 1).trim();
                    result[key] = val;
                }
            }
            return result;
        }
        // Hash-comment metadata (feature files): lines starting with "# key: value"
        const result: Record<string, string> = {};
        for (const line of content.split('\n')) {
            const m = line.match(/^#\s+([\w_]+)\s*:\s*(.+)$/);
            if (m) {
                result[m[1]] = m[2].trim();
            } else if (!line.startsWith('#') && line.trim()) {
                break; // stop at first non-comment, non-empty line
            }
        }
        return result;
    }

    /** Parse a YAML inline list like `[REQ-01, REQ-02]` into string[]. */
    private parseYamlList(raw: string | undefined): string[] {
        if (!raw) { return []; }
        const trimmed = raw.trim();
        if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
            const inner = trimmed.slice(1, -1);
            if (!inner.trim()) { return []; }
            return inner.split(',').map(s => s.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
        }
        return trimmed ? [trimmed] : [];
    }

    private parseSections(content: string): ArtifactSection[] {
        // Strip frontmatter
        const body = content.replace(/^---\n[\s\S]*?\n---\n?/, '');
        const sections: ArtifactSection[] = [];
        const headingRegex = /^##\s+(.+)$/gm;
        let match: RegExpExecArray | null;
        const headings: { title: string; start: number }[] = [];

        while ((match = headingRegex.exec(body)) !== null) {
            headings.push({ title: match[1].trim(), start: match.index + match[0].length });
        }

        for (let i = 0; i < headings.length; i++) {
            const end = i < headings.length - 1 ? headings[i + 1].start - headings[i + 1].title.length - 4 : body.length;
            const sectionContent = body.slice(headings[i].start, end).trim();
            const status = sectionContent.length === 0 ? 'empty' : sectionContent.length < 50 ? 'partial' : 'complete';
            sections.push({
                id: headings[i].title.toLowerCase().replace(/\s+/g, '_'),
                title: headings[i].title,
                content: sectionContent,
                status,
            });
        }
        return sections;
    }

    private inferType(filename: string, relativePath: string): ArtifactType {
        const lower = filename.toLowerCase();
        if (lower.startsWith('sow') || lower.includes('statement')) { return 'sow'; }
        if (lower.startsWith('adr') || lower.includes('decision')) { return 'decision'; }
        if (lower.startsWith('srs') || lower.includes('requirement')) { return 'requirement'; }
        if (lower.includes('persona')) { return 'personas'; }
        if (lower.includes('stor')) { return 'stories'; }
        if (lower.endsWith('.feature') || lower.includes('gherkin') || lower.includes('bdd') || lower.includes('scenario')) { return 'scenario'; }
        const dir = relativePath.split(path.sep)[0]?.toLowerCase() ?? '';
        if (dir.includes('sow')) { return 'sow'; }
        if (dir.includes('adr') || dir.includes('decision')) { return 'decision'; }
        if (dir.includes('bdd')) { return 'scenario'; }
        if (dir.includes('srs') || dir.includes('requirement')) { return 'requirement'; }
        return 'sow'; // default
    }

    private inferTitle(content: string, filename: string): string {
        // Markdown H1
        const mdMatch = content.match(/^#\s+(.+)$/m);
        if (mdMatch) { return mdMatch[1]; }
        // Gherkin Feature line
        const featureMatch = content.match(/^Feature:\s*(.+)$/m);
        if (featureMatch) { return featureMatch[1]; }
        return filename.replace(/\.(md|feature)$/, '').replace(/[-_]/g, ' ');
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // SESSIONS — Local File I/O
    // ═══════════════════════════════════════════════════════════════════════════

    /** Get session index (list of recent sessions). */
    getSessionIndex(): { recent: SessionSummary[]; last_session_id: string | null; active_session_id: string | null } {
        if (!this.workspaceRoot) { return { recent: [], last_session_id: null, active_session_id: null }; }
        const indexPath = path.join(this.workspaceRoot, INDEX_FILE);
        try {
            const raw = fs.readFileSync(indexPath, 'utf-8');
            return JSON.parse(raw);
        } catch {
            return { recent: [], last_session_id: null, active_session_id: null };
        }
    }

    /** Get metadata for a specific session. */
    getSessionMeta(sessionId: string): SessionMeta | undefined {
        if (!this.workspaceRoot) { return undefined; }
        const metaPath = path.join(this.workspaceRoot, META_DIR, sessionId, 'meta.json');
        try {
            const raw = fs.readFileSync(metaPath, 'utf-8');
            return JSON.parse(raw);
        } catch { return undefined; }
    }

    /** Get all events for a session (for replay). */
    getSessionEvents(sessionId: string): SessionEvent[] {
        if (!this.workspaceRoot) { return []; }

        // Try single-file format first (extension writes chat/<sid>.json)
        const singleFile = path.join(this.workspaceRoot, CHAT_DIR, `${sessionId}.json`);
        if (fs.existsSync(singleFile)) {
            try {
                const raw = JSON.parse(fs.readFileSync(singleFile, 'utf-8'));
                const interactions: Array<Record<string, unknown>> = raw.interactions ?? [];
                return interactions.map((i, idx) => ({
                    kind: (i.event_type as string) ?? 'unknown',
                    seq: idx + 1,
                    ts: (i.timestamp as string) ?? '',
                    agent: i.agent,
                    data: i.data,
                }));
            } catch { /* fall through to directory format */ }
        }

        // Try directory format (CLI writes chat/<sid>/<nnn>.json)
        const chatDir = path.join(this.workspaceRoot, CHAT_DIR, sessionId);
        if (!fs.existsSync(chatDir)) { return []; }

        const files = fs.readdirSync(chatDir)
            .filter(f => f.endsWith('.json'))
            .sort();

        const events: SessionEvent[] = [];
        for (const file of files) {
            try {
                const raw = fs.readFileSync(path.join(chatDir, file), 'utf-8');
                events.push(JSON.parse(raw));
            } catch { /* skip malformed */ }
        }
        return events;
    }

    /** Get log entries for a session (.sdlicit/logs/<sid>.jsonl). */
    getSessionLog(sessionId: string): SessionEvent[] {
        if (!this.workspaceRoot) { return []; }
        const logPath = path.join(this.workspaceRoot, SDLICIT_DIR, 'logs', `${sessionId}.jsonl`);
        if (!fs.existsSync(logPath)) { return []; }
        try {
            const raw = fs.readFileSync(logPath, 'utf-8');
            return raw.split('\n').filter(Boolean).map((line, idx) => {
                const parsed = JSON.parse(line);
                return {
                    kind: parsed.type ?? 'log',
                    seq: idx + 1,
                    ts: parsed.ts ?? '',
                    ...parsed,
                };
            });
        } catch { return []; }
    }

    /** Delete a session and all its files from disk. */
    deleteSession(sessionId: string): void {
        if (!this.workspaceRoot) { return; }

        // Remove chat data (single file or directory)
        const chatFile = path.join(this.workspaceRoot, CHAT_DIR, `${sessionId}.json`);
        if (fs.existsSync(chatFile)) { fs.unlinkSync(chatFile); }
        const chatDir = path.join(this.workspaceRoot, CHAT_DIR, sessionId);
        if (fs.existsSync(chatDir)) { fs.rmSync(chatDir, { recursive: true }); }

        // Remove meta/analysis directory
        const metaDir = path.join(this.workspaceRoot, META_DIR, sessionId);
        if (fs.existsSync(metaDir)) { fs.rmSync(metaDir, { recursive: true }); }

        // Remove log file
        const logFile = path.join(this.workspaceRoot, SDLICIT_DIR, 'logs', `${sessionId}.jsonl`);
        if (fs.existsSync(logFile)) { fs.unlinkSync(logFile); }

        // Remove compact file
        const compactFile = path.join(this.workspaceRoot, META_DIR, `${sessionId}_compact.json`);
        if (fs.existsSync(compactFile)) { fs.unlinkSync(compactFile); }

        // Update index
        const indexPath = path.join(this.workspaceRoot, INDEX_FILE);
        if (fs.existsSync(indexPath)) {
            try {
                const index = JSON.parse(fs.readFileSync(indexPath, 'utf-8'));
                index.recent = (index.recent ?? []).filter((s: any) => s.session_id !== sessionId);
                if (index.last_session_id === sessionId) { index.last_session_id = null; }
                if (index.active_session_id === sessionId) { index.active_session_id = null; }
                fs.writeFileSync(indexPath, JSON.stringify(index, null, 2));
            } catch { /* best effort */ }
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // DASHBOARD — Backend-driven
    // ═══════════════════════════════════════════════════════════════════════════

    /** Get dashboard summary. Computes local stats + backend quality data. */
    async getDashboard(): Promise<DashboardSummary> {
        const artifacts = this.getArtifacts();
        const requirements = artifacts.filter(a => a.type === 'requirement');
        const decisions = artifacts.filter(a => a.type === 'decision');
        const scenarios = artifacts.filter(a => a.type === 'scenario');

        const quality = { gold: 0, silver: 0, bronze: 0, unassessed: 0 };
        for (const a of artifacts) {
            const q = a.quality.current;
            if (q === 'gold') { quality.gold++; }
            else if (q === 'silver') { quality.silver++; }
            else if (q === 'bronze') { quality.bronze++; }
            else { quality.unassessed++; }
        }

        // Count requirement IDs that have at least one BDD scenario implementing/testing them
        // Requirements may be inline in a single SRS file — extract REQ IDs from content
        const allReqIds = new Set<string>();
        for (const req of requirements) {
            const content = this.getArtifactContent(req.filePath);
            const idMatches = content.matchAll(/\[((?:FR|NFR|REQ)-[\w-]+\d+)\]/g);
            for (const m of idMatches) { allReqIds.add(m[1]); }
            // Also the artifact ID itself if it has a REQ-like ID
            if (/^(FR|NFR|REQ)-/i.test(req.id)) { allReqIds.add(req.id); }
        }

        // Check which REQ IDs are covered by scenarios
        const coveredReqIds = new Set<string>();
        for (const scn of scenarios) {
            for (const impl of scn.traces.implements) {
                if (allReqIds.has(impl)) { coveredReqIds.add(impl); }
            }
            // Also check upstream refs
            for (const up of scn.traces.upstream) {
                if (allReqIds.has(up)) { coveredReqIds.add(up); }
            }
        }

        const reqCount = allReqIds.size > 0 ? allReqIds.size : requirements.length;
        const requirementsWithScenarios = coveredReqIds.size;

        // Trace coverage: count artifacts that have at least one valid link (upstream, downstream, implements, testedBy)
        const linkedArtifacts = artifacts.filter(a =>
            a.traces.upstream.length > 0 ||
            a.traces.downstream.length > 0 ||
            a.traces.implements.length > 0 ||
            a.traces.testedBy.length > 0 ||
            a.traces.supersedes !== ''
        );

        // Also count total links and broken links
        let totalLinks = 0;
        let validLinks = 0;
        const allArtifactIds = new Set(artifacts.map(a => a.id));
        // Also add known REQ IDs as valid targets
        for (const rid of allReqIds) { allArtifactIds.add(rid); }

        for (const a of artifacts) {
            for (const ref of [...a.traces.upstream, ...a.traces.downstream, ...a.traces.implements, ...a.traces.testedBy]) {
                totalLinks++;
                if (allArtifactIds.has(ref)) { validLinks++; }
            }
            if (a.traces.supersedes) {
                totalLinks++;
                if (allArtifactIds.has(a.traces.supersedes)) { validLinks++; }
            }
        }

        const traceCoverage = artifacts.length > 0
            ? Math.round((linkedArtifacts.length / artifacts.length) * 100)
            : 0;

        const coverage: CoverageStats = {
            requirementsCount: reqCount,
            decisionsCount: decisions.length,
            scenariosCount: scenarios.length,
            requirementsWithScenarios,
            traceCoverage,
            totalLinks,
            validLinks,
            brokenLinks: totalLinks - validLinks,
        };

        // Recent activity from session events
        const idx = this.getSessionIndex();
        const recentActivity: ActivityEntry[] = [];
        if (idx.last_session_id) {
            const events = this.getSessionEvents(idx.last_session_id).slice(-10);
            for (const e of events) {
                recentActivity.push({
                    timestamp: e.ts,
                    action: e.kind,
                    detail: typeof e.endpoint === 'string' ? e.endpoint : undefined,
                });
            }
        }

        return {
            coverage,
            qualityOverview: quality,
            openQuestions: [], // TODO: extract from Socratic probes
            recentActivity,
        };
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // AI OPERATIONS — Backend calls (proxied through SdlicitClient)
    // ═══════════════════════════════════════════════════════════════════════════

    /** Socratic elicitation for a section. */
    async startElicitation(artifactId: string, sectionId: string): Promise<ElicitationResponse> {
        const artifact = this.getArtifact(artifactId);
        if (!artifact) { throw new Error(`Artifact ${artifactId} not found`); }
        const section = artifact.sections.find(s => s.id === sectionId);
        const resp = await this.client.consultSocratic(
            'canvas_elicitation',
            `Help the user complete the "${section?.title ?? sectionId}" section of ${artifact.type} "${artifact.title}"`,
            section?.content ?? '',
            '',
            'ambiguous_input',
        );
        return {
            sessionId: artifactId + ':' + sectionId,
            question: resp.probe?.question ?? (resp.status === 'disabled' ? 'Socratic module is disabled.' : 'No probe available.'),
            done: false,
        };
    }

    /** Respond to an elicitation question. */
    async respondToElicitation(sessionId: string, response: string, sectionId: string): Promise<ElicitationResponse> {
        const [artifactId] = sessionId.split(':');
        const artifact = this.getArtifact(artifactId);
        const section = artifact?.sections.find(s => s.id === sectionId);
        const resp = await this.client.consultSocratic(
            'canvas_elicitation',
            `Continue eliciting for section "${section?.title ?? sectionId}"`,
            `${section?.content ?? ''}\n\nUser response: ${response}`,
            '',
            'ambiguous_input',
            [{ question: 'Previous probe response', answer: response }],
        );
        return {
            sessionId,
            question: resp.probe?.question ?? 'No follow-up available.',
            done: false,
        };
    }

    /** Get companion observations for a section. */
    async getCompanionObservations(artifactId: string, sectionId: string, content: string): Promise<CompanionObservation[]> {
        const artifact = this.getArtifact(artifactId);
        if (!artifact) { return []; }
        const resp = await this.client.consultSocratic(
            'companion',
            `Review the "${sectionId}" section of ${artifact.type} "${artifact.title}" and provide improvement observations`,
            content,
            '',
            'quality_concern',
        );
        // Parse companion response into observations
        const probeText = resp.probe?.question ?? '';
        if (!probeText) {
            return [{ id: 'obs-0', text: resp.status === 'disabled' ? 'Companion module is disabled.' : 'No observations available.', severity: 'info', actionable: false }];
        }
        const observations: CompanionObservation[] = [];
        const lines = probeText.split('\n').filter(l => l.trim());
        for (let i = 0; i < lines.length; i++) {
            observations.push({
                id: `obs-${i}`,
                text: lines[i].replace(/^[-•*]\s*/, ''),
                severity: lines[i].toLowerCase().includes('warning') ? 'warning' : 'suggestion',
                actionable: true,
            });
        }
        return observations.length > 0 ? observations : [{
            id: 'obs-0',
            text: probeText,
            severity: 'info',
            actionable: false,
        }];
    }

    /** Clarify an observation. */
    async clarifyObservation(observationId: string): Promise<string> {
        const resp = await this.client.consultSocratic(
            'companion',
            'Clarify this observation in more detail',
            observationId,
            '',
            'ambiguous_input',
        );
        return resp.probe?.question ?? 'No clarification available.';
    }

    /** Generate BDD scenarios for a requirement. */
    async generateScenarios(requirementId: string): Promise<BddFeature> {
        const artifact = this.getArtifact(requirementId);
        if (!artifact || !this.workspaceRoot) {
            return { requirementId, title: requirementId, scenarios: [] };
        }
        const content = this.getArtifactContent(artifact.filePath);
        const resp = await this.client.generateGherkin(this.workspaceRoot, undefined, content);
        // Parse response into BddFeature
        const gherkinText = typeof resp.gherkin_markdown === 'string' ? resp.gherkin_markdown :
            typeof resp.content === 'string' ? resp.content : JSON.stringify(resp);
        return this.parseGherkinResponse(requirementId, artifact.title, gherkinText);
    }

    /** Review a BDD scenario (accept/reject). */
    async reviewScenario(scenarioId: string, verdict: string, importance?: string, note?: string): Promise<void> {
        // TODO: persist review decisions to file
    }

    private parseGherkinResponse(requirementId: string, title: string, gherkinText: string): BddFeature {
        const scenarios: BddScenario[] = [];
        const scenarioBlocks = gherkinText.split(/(?=Scenario:)/);
        for (let i = 0; i < scenarioBlocks.length; i++) {
            const block = scenarioBlocks[i].trim();
            if (!block.startsWith('Scenario:')) { continue; }
            const titleMatch = block.match(/^Scenario:\s*(.+)$/m);
            scenarios.push({
                id: `${requirementId}-scn-${i}`,
                title: titleMatch?.[1] ?? `Scenario ${i + 1}`,
                situation: block.match(/Given\s+(.+)/)?.[1] ?? '',
                gherkin: block,
                status: 'pending',
                origin: 'generated',
            });
        }
        return { requirementId, title, scenarios };
    }

    // ── Chat / KB Operations ──────────────────────────────────────────────────

    /** Send a chat message (routes based on mode). */
    async chat(message: string, mode: ChatMode, history: ChatEntry[] = []): Promise<{ content: string; sources?: KBSource[]; tokensUsed?: number; agentsInvolved?: string[]; tokensByAgent?: Record<string, { prompt: number; completion: number; total: number; calls: number }> }> {
        const beforeTotal = this.client.totalUsage.total;
        const beforeByAgent = { ...this.client.totalUsage.byAgent };

        let result: { content: string; sources?: KBSource[] };
        switch (mode) {
            case 'explore': {
                const resp = await this.client.queryRAG(message);
                // First result with mode != 'naive' is the graph-synthesized answer;
                // the remaining naive chunks are source evidence for the chips.
                const graphResult = resp.results.find(r => r.mode !== 'naive');
                const vectorChunks = resp.results.filter(r => r.mode === 'naive');
                const answer = graphResult?.text ?? vectorChunks[0]?.text ?? '';
                const sourceResults = graphResult ? vectorChunks : resp.results.slice(1);
                const sources: KBSource[] = [
                    // Include inline refs from graph result as sources too
                    ...(graphResult ? [{ ref: graphResult.source, relevance: graphResult.relevance, snippet: graphResult.text }] : []),
                    ...sourceResults.map(r => ({
                        ref: r.source,
                        relevance: r.relevance,
                        snippet: r.text,
                    })),
                ];
                result = { content: answer || 'No relevant results found.', sources };
                break;
            }
            case 'agent':
            case 'chat':
            default: {
                // Use Socratic consult for general chat (ToM-aware)
                const context = history.slice(-5).map(h => `${h.role}: ${h.content}`).join('\n');
                const resp = await this.client.consultSocratic(
                    mode === 'agent' ? 'agent_chat' : 'general_chat',
                    message,
                    context,
                    '',
                    'ambiguous_input',
                );
                const content = resp.probe?.question ?? (resp.status === 'disabled' ? 'Chat module is currently disabled.' : 'No response available.');
                result = { content, sources: [] };
                break;
            }
        }

        // Compute token delta and new agents from this call
        const tokensUsed = this.client.totalUsage.total - beforeTotal;
        const agentsInvolved: string[] = [];
        const tokensByAgent: Record<string, { prompt: number; completion: number; total: number; calls: number }> = {};
        for (const agent of Object.keys(this.client.totalUsage.byAgent)) {
            const cur = this.client.totalUsage.byAgent[agent];
            const prev = beforeByAgent[agent];
            if (!prev || cur.total > prev.total) {
                agentsInvolved.push(agent);
                tokensByAgent[agent] = {
                    prompt: cur.prompt - (prev?.prompt ?? 0),
                    completion: cur.completion - (prev?.completion ?? 0),
                    total: cur.total - (prev?.total ?? 0),
                    calls: cur.calls - (prev?.calls ?? 0),
                };
            }
        }

        return { ...result, tokensUsed, agentsInvolved, tokensByAgent };
    }

    // ── ToM Chat Observation ────────────────────────────────────────────────

    /** Observe a chat exchange for ToM (fire-and-forget). */
    async observeChat(
        userMessage: string,
        assistantResponse: string,
        chatHistory: Array<{ role: string; content: string }>,
    ): Promise<void> {
        await this.client.observeChat(userMessage, assistantResponse, chatHistory);
    }

    /** Ask ToM if it wants to inject a Socratic probe. */
    async giveSuggestions(
        chatHistory: Array<{ role: string; content: string }>,
    ): Promise<{
        should_probe: boolean;
        probe_question?: string;
        probe_style?: string;
        satisfaction_score?: number;
        reasoning?: string;
    }> {
        return this.client.giveSuggestions(chatHistory);
    }

    /** Query knowledge base (KB explorer). */
    async queryKnowledgeBase(query: string): Promise<ExplorerResponse> {
        const resp = await this.client.queryRAG(query);
        // Use graph-synthesized result as the answer; vector chunks become sources
        const graphResult = resp.results.find(r => r.mode !== 'naive');
        const vectorChunks = resp.results.filter(r => r.mode === 'naive');
        const answer = graphResult?.text ?? vectorChunks[0]?.text ?? 'No results found.';
        const sourceResults = graphResult ? vectorChunks : resp.results.slice(1);
        const sources = [
            ...(graphResult ? [{ ref: graphResult.source, relevance: graphResult.relevance, snippet: graphResult.text }] : []),
            ...sourceResults.map(r => ({ ref: r.source, relevance: r.relevance, snippet: r.text })),
        ];
        return { answer, sources };
    }

    /** Locate a chunk's source in its original document (PDF page, text file section). */
    async locateChunk(sourceRef: string, snippet: string): Promise<{ found: boolean; page: number; filePath: string; fileType: string; anchor: string; matchScore: number }> {
        const resp = await this.client.locateChunk(sourceRef, snippet, this.workspaceRoot ?? '');
        return {
            found: resp.found,
            page: resp.page,
            filePath: resp.file_path,
            fileType: resp.file_type,
            anchor: resp.anchor,
            matchScore: resp.match_score,
        };
    }

    /** Ingest documents into KB (SSE streaming). */
    async ingestKB(projectDir: string, selectedFiles?: string[], onProgress?: (event: KBIngestEvent) => void): Promise<void> {
        await this.client.ingestKB(projectDir, selectedFiles, onProgress);
    }

    // ── Passthrough to SdlicitClient ──────────────────────────────────────────

    get sdlicitClient(): SdlicitClient { return this.client; }
}
