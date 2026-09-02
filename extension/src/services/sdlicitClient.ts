// ---------------------------------------------------------------------------
// Sdlicit — Backend HTTP Client
// ---------------------------------------------------------------------------
// Mirrors cli/api_client.py — typed wrapper for all backend REST endpoints.
// All methods parse X-Sdlicit-Tokens-* headers and emit token events.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';

export interface AgentTokenUsage {
    prompt: number;
    completion: number;
    total: number;
    calls: number;
}

export interface TokenUsage {
    prompt: number;
    completion: number;
    total: number;
    calls: number;
    byAgent: Record<string, AgentTokenUsage>;
}

export interface CallLogEntry {
    timestamp: number;
    endpoint: string;
    agents: Record<string, AgentTokenUsage>;
    totalTokens: number;
    durationMs: number;
}

export interface Clarification {
    question: string;
    answer: string;
}

// --- Response types (match backend Pydantic models) ---

export interface BackendConfig {
    provider: string;
    model: string;
    model_overrides: Record<string, string>;
    enable_rag: boolean;
    enable_tom: boolean;
    enable_socratic: boolean;
    socratic_judge_mode: string;
    socratic_max_turns: number;
    model_context_window: number;
    compact_threshold_pct: number;
    tom_focus: string;
    log_prompts: boolean;
}

export interface SOWResponse {
    sow_markdown: string;
    socratic_probe?: SocraticProbe;
}

export interface StepEventResponse {
    suggestion?: { field: string; message: string; severity: string; should_show?: boolean } | null;
    suggestions?: Array<{ field: string; message: string; severity: string; should_show?: boolean }>;
    socratic_probe?: SocraticProbe;
    compliance?: string;
    supersedes_hint?: { adr_id: string; adr_title: string; reason: string } | null;
}

export interface SuggestDirectionsResponse {
    summary: string;
    directions: Array<{ title: string; rationale: string; priority: string; gap_filled: string }>;
}

export interface ExpandResponse {
    reviews: Array<{ agent: string; summary: string; suggestions: string[]; compliance?: string }>;
    tom_verdict: string;
}

export interface RAGQueryResponse {
    results: Array<{ text: string; source: string; relevance: number; mode: string; store: string }>;
    rag_enabled: boolean;
    probed: boolean;
    store: string;
}

export interface LocateChunkResponse {
    found: boolean;
    page: number;
    file_path: string;
    file_type: string;
    anchor: string;
    match_score: number;
}

// --- Traceability types ---

export interface TraceGraphNode {
    id: string;
    type: string;
    title: string;
    status: string;
    filePath: string;
}

export interface TraceGraphEdge {
    source: string;
    target: string;
    type: string;
}

export interface TraceGraphData {
    nodes: TraceGraphNode[];
    edges: TraceGraphEdge[];
}

export interface TraceIssue {
    severity: string;
    message: string;
    source_id: string;
    target_id: string;
}

export interface TraceCheckData {
    issues: TraceIssue[];
    impacted_nodes: string[];
    suggested_implements: string[];
}

// --- Trace Coverage types ---

export interface ArtifactCoverage {
    artifact_id: string;
    artifact_type: string;
    outgoing_links: number;
    valid_links: number;
    broken_links: number;
    semantic_score: number | null;
    covered_by: string[];
}

export interface TraceCoverageData {
    mode: string;
    total_links: number;
    valid_links: number;
    broken_links_count: number;
    structural_coverage_pct: number;
    semantic_coverage_pct: number | null;
    mean_tfidf: number | null;
    mean_jaccard: number | null;
    mean_topic: number | null;
    mean_combined: number | null;
    per_requirement_scores: Record<string, number>;
    artifacts: ArtifactCoverage[];
    graph_issues: TraceIssue[];
    has_conflicts: boolean;
    conflict_assessment: string;
    artifact_counts: Record<string, number>;
}

export interface KBIngestEvent {
    type: 'start' | 'progress' | 'done' | 'error';
    total_chunks?: number;
    total_files?: number;
    current?: number;
    source_name?: string;
    ok?: boolean;
    ingested?: number;
    errors?: string[];
    message?: string;
    file?: string;
    skipped?: number;
}

export interface ScannedDocument {
    relative_path: string;
    size_bytes: number;
    suffix: string;
    ingestion_status: 'complete' | 'partial' | 'error' | 'none';
}

export interface ScanDocumentsResponse {
    documents: ScannedDocument[];
    total_files: number;
}

export interface GenerationResponse {
    [key: string]: unknown;
    socratic_probe?: SocraticProbe;
}

export interface SocraticProbe {
    probe_id: string;
    question: string;
    style: string;
    originating_agent: string;
    what_was_asked: string;
    turn: number;
    max_turns: number;
    rag_grounding: string;
    /** Raw verbatim KB facts to show the user before the question. Empty string = none. */
    kb_facts: string;
    /** Backend actions taken while generating this probe (e.g. 'Consulting Knowledge Base',
     *  'Analyzing interaction history', 'Dean review applied: simplified').
     *  Render as status badges in the UI. */
    transparency_events: string[];
}

export interface SocraticResponse {
    status: 'probe' | 'skipped' | 'disabled';
    probe: SocraticProbe | null;
}

// --- SOW Streaming types ---

export interface SOWStreamEvent {
    event: 'section_start' | 'section_complete' | 'socratic_probe' | 'kb_verification' | 'complete';
    section?: string;
    heading?: string;
    content?: string;
    markdown?: string;
    needs_socratic?: boolean;
    socratic_reason?: string;
    probe?: SocraticProbe;
    grounded?: boolean;
    ungrounded_claims?: string[];
    full_markdown?: string;
}

export interface RegenerateSectionResponse {
    section_name: string;
    content: string;
    needs_socratic: boolean;
    socratic_reason: string;
    socratic_probe?: SocraticProbe;
}

// --- Events ---

export interface TokenEvent {
    usage: TokenUsage;
}

// ---------------------------------------------------------------------------

export class SdlicitClient {
    private baseUrl: string;
    private _totalUsage: TokenUsage = { prompt: 0, completion: 0, total: 0, calls: 0, byAgent: {} };
    private _callLog: CallLogEntry[] = [];
    private _onTokenUpdate = new vscode.EventEmitter<TokenUsage>();
    readonly onTokenUpdate = this._onTokenUpdate.event;
    private _onCallLogUpdate = new vscode.EventEmitter<CallLogEntry>();
    readonly onCallLogUpdate = this._onCallLogUpdate.event;

    /** Public output channel — shows backend activity step-by-step. */
    readonly outputChannel: vscode.OutputChannel;

    constructor(baseUrl?: string) {
        const config = vscode.workspace.getConfiguration('sdlicit');
        this.baseUrl = baseUrl ?? config.get<string>('serverUrl', 'http://localhost:8000');
        this.outputChannel = vscode.window.createOutputChannel('Sdlicit', { log: true });
    }

    get totalUsage(): TokenUsage { return this._totalUsage; }
    get callLog(): readonly CallLogEntry[] { return this._callLog; }

    /** Log a message to the output channel with timestamp. */
    log(message: string): void {
        this.outputChannel.appendLine(`[${new Date().toLocaleTimeString()}] ${message}`);
    }

    // --- Core HTTP helpers ---

    private async post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
        const url = `${this.baseUrl}/api/v1${path}`;
        this.log(`→ POST ${path}`);
        const start = Date.now();
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const elapsed = Date.now() - start;
        if (!resp.ok) {
            const text = await resp.text();
            this.log(`✗ POST ${path} failed (${resp.status}) [${elapsed}ms]: ${text}`);
            throw new Error(`POST ${path} failed (${resp.status}): ${text}`);
        }
        this.log(`✓ POST ${path} (${resp.status}) [${elapsed}ms]`);
        this.parseTokenHeaders(resp.headers, path, elapsed);
        return await resp.json() as T;
    }

    private async get<T>(path: string): Promise<T> {
        const url = `${this.baseUrl}/api/v1${path}`;
        this.log(`→ GET ${path}`);
        const start = Date.now();
        const resp = await fetch(url, { method: 'GET' });
        const elapsed = Date.now() - start;
        if (!resp.ok) {
            const text = await resp.text();
            this.log(`✗ GET ${path} failed (${resp.status}) [${elapsed}ms]: ${text}`);
            throw new Error(`GET ${path} failed (${resp.status}): ${text}`);
        }
        this.log(`✓ GET ${path} (${resp.status}) [${elapsed}ms]`);
        this.parseTokenHeaders(resp.headers, path, elapsed);
        return await resp.json() as T;
    }

    private parseTokenHeaders(headers: Headers, endpoint: string = '', durationMs: number = 0): void {
        const prompt = parseInt(headers.get('x-sdlicit-tokens-prompt') ?? '0', 10);
        const completion = parseInt(headers.get('x-sdlicit-tokens-completion') ?? '0', 10);
        const total = parseInt(headers.get('x-sdlicit-tokens-total') ?? '0', 10);
        const calls = parseInt(headers.get('x-sdlicit-tokens-calls') ?? '0', 10);
        if (total > 0) {
            this._totalUsage.prompt += prompt;
            this._totalUsage.completion += completion;
            this._totalUsage.total += total;
            this._totalUsage.calls += calls;
            const callAgents: Record<string, AgentTokenUsage> = {};
            const byAgentRaw = headers.get('x-sdlicit-tokens-by-agent');
            if (byAgentRaw) {
                try {
                    const parsed = JSON.parse(byAgentRaw);
                    for (const [agent, usage] of Object.entries(parsed)) {
                        // Backend sends prompt_tokens/completion_tokens/total_tokens/calls
                        const u = usage as { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; calls?: number; prompt?: number; completion?: number; total?: number };
                        const p = u.prompt_tokens ?? u.prompt ?? 0;
                        const c = u.completion_tokens ?? u.completion ?? 0;
                        const t = u.total_tokens ?? u.total ?? (p + c);
                        const agentCalls = u.calls ?? 1;
                        if (!this._totalUsage.byAgent[agent]) {
                            this._totalUsage.byAgent[agent] = { prompt: 0, completion: 0, total: 0, calls: 0 };
                        }
                        this._totalUsage.byAgent[agent].prompt += p;
                        this._totalUsage.byAgent[agent].completion += c;
                        this._totalUsage.byAgent[agent].total += t;
                        this._totalUsage.byAgent[agent].calls += agentCalls;
                        callAgents[agent] = { prompt: p, completion: c, total: t, calls: agentCalls };
                    }
                } catch { /* ignore parse errors */ }
            }
            // Record to call log
            const logEntry: CallLogEntry = {
                timestamp: Date.now(),
                endpoint,
                agents: callAgents,
                totalTokens: total,
                durationMs,
            };
            this._callLog.push(logEntry);
            // Cap log at 200 entries
            if (this._callLog.length > 200) { this._callLog.shift(); }
            this._onCallLogUpdate.fire(logEntry);
            this._onTokenUpdate.fire(this._totalUsage);
        }
    }

    // --- Health & Init ---

    async health(): Promise<boolean> {
        try {
            const url = `${this.baseUrl}/health`;
            const resp = await fetch(url, { method: 'GET', signal: AbortSignal.timeout(8000) });
            return resp.ok;
        } catch {
            return false;
        }
    }

    async init(projectDir: string): Promise<{ status: string; project_dir: string }> {
        return this.post('/init', { project_dir: projectDir });
    }

    async getConfig(): Promise<BackendConfig> {
        return this.get('/config');
    }

    // --- Session ---

    async sessionStart(stage: string = 'extension'): Promise<{ session_id: string | null }> {
        return this.post('/session/start', { stage });
    }

    async sessionEnd(): Promise<Record<string, unknown>> {
        return this.post('/session/end');
    }

    async sessionCompact(): Promise<Record<string, unknown>> {
        return this.post('/session/compact');
    }

    async savePreference(key: string, value: string, note: string = ''): Promise<Record<string, unknown>> {
        return this.post('/preference', { key, value, note });
    }

    // --- ToM Chat Observation & Suggestions ---

    async observeChat(
        userMessage: string,
        assistantResponse: string,
        chatHistory: Array<{ role: string; content: string }>,
    ): Promise<{
        status: string;
        observation?: string;
        recommendation?: string;
        scaffolding_level?: string;
        confidence?: number;
        inferred_goals?: string[];
    }> {
        return this.post('/tom/observe-chat', {
            user_message: userMessage,
            assistant_response: assistantResponse,
            chat_history: chatHistory,
        });
    }

    async giveSuggestions(
        chatHistory: Array<{ role: string; content: string }>,
    ): Promise<{
        status: string;
        should_probe: boolean;
        probe_question?: string;
        probe_style?: string;
        satisfaction_score?: number;
        reasoning?: string;
    }> {
        return this.post('/tom/give-suggestions', { chat_history: chatHistory });
    }

    // --- Intake Stage ---

    async createSOW(rawBrief: string, clarifications: Clarification[] = []): Promise<SOWResponse> {
        return this.post('/intake/sow', { raw_brief: rawBrief, clarifications });
    }

    async createSOWStream(
        rawBrief: string,
        clarifications: Clarification[] = [],
        onEvent?: (event: SOWStreamEvent) => void,
    ): Promise<string> {
        const url = `${this.baseUrl}/api/v1/intake/sow/stream`;
        this.log('→ POST /intake/sow/stream (SSE)');
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ raw_brief: rawBrief, clarifications }),
        });
        if (!resp.ok) {
            const text = await resp.text();
            this.log(`✗ POST /intake/sow/stream failed (${resp.status}): ${text}`);
            throw new Error(`POST /intake/sow/stream failed (${resp.status}): ${text}`);
        }
        if (!resp.body) { return ''; }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let fullMarkdown = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) { break; }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() ?? '';
            for (const line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith('data: ')) {
                    try {
                        const event: SOWStreamEvent = JSON.parse(trimmed.slice(6));
                        if (event.event === 'complete' && event.full_markdown) {
                            fullMarkdown = event.full_markdown;
                        }
                        onEvent?.(event);
                    } catch { /* skip malformed lines */ }
                }
            }
        }
        this.log('✓ POST /intake/sow/stream — complete');
        return fullMarkdown;
    }

    async regenerateSection(
        rawBrief: string,
        sectionName: string,
        priorSections: string = '',
        userFeedback: string = '',
        currentContent: string = '',
        clarifications: Clarification[] = [],
    ): Promise<RegenerateSectionResponse> {
        return this.post('/intake/sow/regenerate-section', {
            raw_brief: rawBrief,
            section_name: sectionName,
            prior_sections: priorSections,
            user_feedback: userFeedback,
            current_content: currentContent,
            clarifications,
        });
    }

    // --- Composing Stage ---

    async stepEvent(
        stepName: string,
        stepValue: string | string[],
        partialFields: Record<string, string>,
        projectDir: string,
        clarifications: Clarification[] = [],
    ): Promise<StepEventResponse> {
        return this.post('/composing/step', {
            step_name: stepName,
            step_value: stepValue,
            partial_fields: partialFields,
            project_dir: projectDir,
            clarifications,
        });
    }

    async analyseInput(rawInput: string, projectDir: string): Promise<Record<string, unknown>> {
        return this.post('/composing/analyse', { raw_input: rawInput, project_dir: projectDir });
    }

    async suggestDirections(brief: string, projectDir: string, downstreamArtifacts: string = ''): Promise<SuggestDirectionsResponse> {
        return this.post('/composing/adr/suggest-directions', {
            brief, project_dir: projectDir, downstream_artifacts: downstreamArtifacts,
        });
    }

    // --- Expansion Stage ---

    async expandADR(adrFilename: string, projectDir: string): Promise<ExpandResponse> {
        return this.post('/expansion/expand', { adr_filename: adrFilename, project_dir: projectDir });
    }

    async queryKB(query: string, mode: string = 'hybrid'): Promise<RAGQueryResponse> {
        return this.post('/expansion/query-kb', { query, mode });
    }

    async queryRAG(query: string, store: string = 'all', mode: string = 'hybrid', probFirst: boolean = false, topK: number = 5): Promise<RAGQueryResponse> {
        return this.post('/expansion/query-rag', { query, store, mode, probe_first: probFirst, top_k: topK });
    }

    async locateChunk(sourceRef: string, snippet: string, projectDir: string = ''): Promise<LocateChunkResponse> {
        return this.post('/expansion/locate-chunk', { source_ref: sourceRef, snippet, project_dir: projectDir });
    }

    async scanDocuments(projectDir: string): Promise<ScanDocumentsResponse> {
        const url = `${this.baseUrl}/api/v1/expansion/scan-documents?project_dir=${encodeURIComponent(projectDir)}`;
        const resp = await fetch(url, { method: 'GET' });
        if (!resp.ok) { throw new Error(`scan-documents failed: ${resp.status}`); }
        return await resp.json() as ScanDocumentsResponse;
    }

    async kbStatus(): Promise<{ enabled: boolean }> {
        return this.get('/expansion/kb-status');
    }

    async ingestKB(
        projectDir: string,
        selectedFiles?: string[],
        onProgress?: (event: KBIngestEvent) => void,
    ): Promise<void> {
        const url = `${this.baseUrl}/api/v1/expansion/ingest-kb`;
        const body: Record<string, unknown> = { project_dir: projectDir };
        if (selectedFiles) { body.selected_files = selectedFiles; }

        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) { throw new Error(`ingest-kb failed: ${resp.status}`); }
        if (!resp.body) { return; }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) { break; }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() ?? '';
            for (const line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith('data: ')) {
                    try {
                        const event: KBIngestEvent = JSON.parse(trimmed.slice(6));
                        onProgress?.(event);
                    } catch { /* skip malformed lines */ }
                }
            }
        }
    }

    async ingestArtifact(text: string, artifactType: string, name: string, replace: boolean = true): Promise<void> {
        await this.post('/expansion/ingest-artifact', { text, artifact_type: artifactType, name, replace });
    }

    // --- Artifact Store (canonical backend save/load) ---

    async saveArtifact(artifactType: string, data: Record<string, unknown>, projectDir?: string, renderMarkdown: boolean = true): Promise<{ json_path: string; markdown_path?: string; artifact_meta: Record<string, string>; suggested_implements?: string[] }> {
        return this.post('/artifacts/save', { artifact_type: artifactType, data, project_dir: projectDir ?? '', render_markdown: renderMarkdown });
    }

    async validateGherkin(gherkinText: string): Promise<{ valid: boolean; feature_name: string; scenario_count: number; issues: string[] }> {
        return this.post('/generation/validate-gherkin', { gherkin_text: gherkinText });
    }

    async loadArtifact(artifactType: string, projectDir?: string, filename?: string): Promise<{ artifact_type: string; data: Record<string, unknown> }> {
        const params = new URLSearchParams();
        if (projectDir) { params.set('project_dir', projectDir); }
        if (filename) { params.set('filename', filename); }
        const qs = params.toString() ? `?${params.toString()}` : '';
        return this.get(`/artifacts/${artifactType}${qs}`);
    }

    async loadAllArtifacts(artifactType: string, projectDir?: string): Promise<Array<Record<string, unknown>>> {
        const params = new URLSearchParams();
        if (projectDir) { params.set('project_dir', projectDir); }
        const qs = params.toString() ? `?${params.toString()}` : '';
        return this.get(`/artifacts/${artifactType}/all${qs}`);
    }

    async renderArtifactMarkdown(artifactType: string, projectDir?: string, filename?: string): Promise<{ markdown: string }> {
        const params = new URLSearchParams();
        if (projectDir) { params.set('project_dir', projectDir); }
        if (filename) { params.set('filename', filename); }
        const qs = params.toString() ? `?${params.toString()}` : '';
        return this.get(`/artifacts/${artifactType}/markdown${qs}`);
    }

    async deleteFromKB(artifactType: string, name: string): Promise<{ removed: number }> {
        return this.post('/expansion/delete-artifact', { artifact_type: artifactType, name });
    }

    async getArtifactKBStatus(): Promise<{ artifacts: Array<{ artifact_type: string; name: string; status: string; chunks: number }> }> {
        return this.get('/expansion/artifact-kb-status');
    }

    async supersedeADR(oldAdrId: string, newText: string, newAdrId: string): Promise<{ removed: number; added: number }> {
        return this.post('/expansion/supersede-adr', { old_adr_id: oldAdrId, new_text: newText, new_adr_id: newAdrId });
    }

    // --- Traceability ---

    async getTraceabilityGraph(): Promise<TraceGraphData> {
        return this.get('/expansion/traceability-graph');
    }

    async checkTraceability(artifactId: string, projectDir?: string, artifactContent?: string): Promise<TraceCheckData> {
        return this.post('/expansion/check-traceability', { artifact_id: artifactId, artifact_content: artifactContent ?? '', project_dir: projectDir ?? '' });
    }

    async getTraceCoverage(mode?: string): Promise<TraceCoverageData> {
        return this.post('/expansion/trace-coverage', { mode: mode ?? '' });
    }

    // --- Generation Stage ---

    async generateSRS(sowContent: string, projectDir: string, clarifications: Clarification[] = []): Promise<GenerationResponse> {
        return this.post('/generation/srs', { sow_content: sowContent, project_dir: projectDir, clarifications });
    }

    async generatePersonas(projectDir: string, srsContent?: string, clarifications: Clarification[] = []): Promise<GenerationResponse> {
        return this.post('/generation/personas', { project_dir: projectDir, srs_content: srsContent, clarifications });
    }

    async generateStories(personas: string[], requirements: string, projectDir: string, clarifications: Clarification[] = []): Promise<GenerationResponse> {
        return this.post('/generation/stories', { personas: personas.join('\n\n'), requirements, project_dir: projectDir, clarifications });
    }

    async generateGherkin(projectDir: string, personas?: string[], requirements?: string, clarifications: Clarification[] = []): Promise<GenerationResponse> {
        const parsedPersonas = personas ? this.normalizePersonasInput(personas) : [];
        return this.post('/generation/gherkin', { project_dir: projectDir, personas: parsedPersonas, requirements, clarifications });
    }

    /** Parse personas from JSON exports or markdown into structured objects. */
    private normalizePersonasInput(personas: string[]): Array<{ name: string; role: string; goals: string[]; frustrations: string[] }> {
        const deduped = new Map<string, { name: string; role: string; goals: string[]; frustrations: string[] }>();

        const addPersona = (persona: { name: string; role: string; goals: string[]; frustrations: string[] }) => {
            const key = persona.name.trim().toLowerCase();
            if (!key) { return; }
            const existing = deduped.get(key);
            const score = persona.goals.length + persona.frustrations.length;
            const existingScore = existing ? existing.goals.length + existing.frustrations.length : -1;
            if (!existing || score > existingScore) {
                deduped.set(key, persona);
            }
        };

        for (const chunk of personas) {
            const trimmed = chunk.trim();
            if (!trimmed) { continue; }

            if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                try {
                    const parsed = JSON.parse(trimmed);
                    const items = Array.isArray(parsed) ? parsed : (parsed.personas || [parsed]);
                    for (const item of items) {
                        if (item?.name) {
                            addPersona({
                                name: String(item.name),
                                role: String(item.role || ''),
                                goals: Array.isArray(item.goals) ? item.goals.map(String) : [],
                                frustrations: Array.isArray(item.frustrations) ? item.frustrations.map(String) : [],
                            });
                        }
                    }
                    continue;
                } catch { /* fall through to markdown parsing */ }
            }

            for (const persona of this.parsePersonasMarkdown(trimmed)) {
                addPersona(persona);
            }
        }

        return Array.from(deduped.values());
    }

    /** Parse personas markdown into structured objects for the backend. */
    private parsePersonasMarkdown(md: string): Array<{ name: string; role: string; goals: string[]; frustrations: string[] }> {
        const lines = md.split('\n');
        const personas: Array<{ name: string; role: string; goals: string[]; frustrations: string[] }> = [];
        let current: { name: string; role: string; goals: string[]; frustrations: string[] } | null = null;
        let inGoals = false;
        let inFrustrations = false;

        const flush = () => { if (current && current.name) { personas.push(current); } };

        for (const line of lines) {
            const trimmed = line.trim();
            if (/^##\s+/.test(trimmed)) {
                flush();
                const heading = trimmed.replace(/^##\s+/, '');
                const idMatch = heading.match(/^(?:PERSONA-\d+|P-\d+)\s*:\s*(.+)$/i);
                current = { name: idMatch?.[1]?.trim() || heading, role: '', goals: [], frustrations: [] };
                inGoals = false; inFrustrations = false;
            } else if (current) {
                if (/^\*\*Role\*\*[:\s]*/i.test(trimmed) || /^Role[:\s]*/i.test(trimmed)) {
                    current.role = trimmed.replace(/^\*\*Role\*\*[:\s]*/i, '').replace(/^Role[:\s]*/i, '');
                    inGoals = false; inFrustrations = false;
                } else if (/^\*\*Goals?\*\*[:\s]*/i.test(trimmed)) {
                    const inline = trimmed.replace(/^\*\*Goals?\*\*[:\s]*/i, '').trim();
                    if (inline) {
                        current.goals.push(...inline.split(/[,;]\s*/).map(s => s.trim()).filter(Boolean));
                    }
                    inGoals = true; inFrustrations = false;
                } else if (/^Goals?[:\s]*$/i.test(trimmed)) {
                    inGoals = true; inFrustrations = false;
                } else if (/^\*\*Frustrations?\*\*[:\s]*/i.test(trimmed) || /^\*\*Pain Points?\*\*[:\s]*/i.test(trimmed)) {
                    const inline = trimmed.replace(/^\*\*(?:Frustrations?|Pain Points?)\*\*[:\s]*/i, '').trim();
                    if (inline) {
                        current.frustrations.push(...inline.split(/[,;]\s*/).map(s => s.trim()).filter(Boolean));
                    }
                    inGoals = false; inFrustrations = true;
                } else if (/^Frustrations?[:\s]*$/i.test(trimmed)) {
                    inGoals = false; inFrustrations = true;
                } else if (/^[-*]\s+/.test(trimmed)) {
                    const item = trimmed.replace(/^[-*]\s+/, '');
                    if (inGoals) { current.goals.push(item); }
                    else if (inFrustrations) { current.frustrations.push(item); }
                }
            }
        }
        flush();
        return personas;
    }

    // --- Socratic (cross-cutting) ---

    async consultSocratic(
        originatingAgent: string,
        whatWasAsked: string,
        whatIsKnown: string,
        suspectOutput: string = '',
        issue: string = 'ambiguous_input',
        clarifications: Clarification[] = [],
    ): Promise<SocraticResponse> {
        return this.post('/socratic/consult', {
            originating_agent: originatingAgent,
            what_was_asked: whatWasAsked,
            what_is_known: whatIsKnown,
            suspect_output: suspectOutput,
            issue,
            clarifications,
        });
    }

    // --- Utility ---

    resetTokenUsage(): void {
        this._totalUsage = { prompt: 0, completion: 0, total: 0, calls: 0, byAgent: {} };
        this._callLog.length = 0;
        this._onTokenUpdate.fire(this._totalUsage);
    }
}
