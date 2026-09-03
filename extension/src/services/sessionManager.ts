// ---------------------------------------------------------------------------
// Sdlicit — Session Manager
// ---------------------------------------------------------------------------
// Manages ToM session lifecycle and persists the three-tier memory:
//
//   .sdlicit/sessions/chat/<sid>.json     — Tier 1: raw interactions
//                                           (agent, input, output — no tool calls)
//   .sdlicit/sessions/sdlicit/<sid>.json  — Tier 2: per-session analysis
//                                           (intents, patterns, scaffolding)
//   .sdlicit/sessions/user/user_model.json — Tier 3: aggregated user profile
//   .sdlicit/sessions/user/state_of_mind.json — current inferred mental state
//   .sdlicit/sessions/user/state_of_mind_log.json — append-only history
//
//   .sdlicit/sessions/index.json          — session index (drives tree view)
//   .sdlicit/logs/<sid>.jsonl             — detailed activity log
//
// The backend never writes files; it returns Tier 1/2/3 artifacts via the
// /session/end response, and this manager persists them.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { SdlicitClient } from './sdlicitClient';

export interface ToMInteraction {
    timestamp: string;
    event_type: string;
    agent?: string;
    data?: Record<string, unknown>;
}

/** Minimal index entry shown in the session tree view. */
interface SessionIndexEntry {
    session_id: string;
    started_at: string;
    last_event_at: string;
    status: 'active' | 'closed' | 'crashed';
}

interface SessionIndex {
    recent: SessionIndexEntry[];
    last_session_id: string | null;
    active_session_id: string | null;
}

/** State of mind snapshot (cached, updated every N calls). */
interface StateOfMind {
    updated_at: string;
    frustration_level: 'none' | 'low' | 'moderate' | 'high';
    engagement: 'low' | 'moderate' | 'high';
    confidence: 'low' | 'moderate' | 'high';
    inferred_goals: string[];
    notes: string;
}

const DEFAULT_TOKEN_WARNING_THRESHOLD = 20_000;
const MAX_INDEX_ENTRIES = 50;
const STATE_OF_MIND_UPDATE_INTERVAL = 5; // update every N interactions

export class SessionManager {
    private sessionId: string | null = null;
    private projectDir: string | undefined;
    private startedAt: string | null = null;

    /** Tier 1: raw interactions for ToM (only user-facing events) */
    private tomInteractions: ToMInteraction[] = [];

    /** Detailed log (everything — API calls, file access, timing) */
    private logEntries: string[] = [];

    /** Interaction count since last state-of-mind update */
    private interactionsSinceStateUpdate = 0;

    /** Whether the token warning has been shown this session */
    private tokenWarningShown = false;

    /**
     * Compaction trigger threshold in tokens. Same concept the CLI already
     * uses (model_context_window * compact_threshold_pct from /api/v1/config)
     * so both clients respond to one shared, user configurable budget
     * instead of the extension using its own hardcoded constant.
     */
    private compactionThreshold = DEFAULT_TOKEN_WARNING_THRESHOLD;

    /** Count of interactions since last compaction (for incremental compact) */
    private interactionsSinceCompact = 0;

    /** Whether logging is enabled (always true — backend owns persistence since v0.9). */
    private loggingEnabled = true;

    constructor(private readonly client: SdlicitClient) {
        // Listen to token updates for the compaction warning
        this.client.onTokenUpdate((usage) => {
            if (!this.tokenWarningShown && usage.total >= this.compactionThreshold) {
                this.tokenWarningShown = true;
                this.handleTokenThresholdExceeded();
            }
        });
    }

    get currentSessionId(): string | null { return this.sessionId; }

    /**
     * Enable/disable local logging.
     * @deprecated Since v0.9 the backend always persists sessions.
     * Kept for future use (e.g. offline/privacy mode).
     */
    setLoggingEnabled(enabled: boolean): void {
        this.loggingEnabled = enabled;
    }

    // --- Paths ---------------------------------------------------------------

    private get sessionsDir(): string | undefined {
        if (!this.projectDir) { return undefined; }
        return path.join(this.projectDir, '.sdlicit', 'sessions');
    }

    private get chatDir(): string | undefined {
        if (!this.projectDir) { return undefined; }
        return path.join(this.projectDir, '.sdlicit', 'sessions', 'chat');
    }

    private get sdlicitDir(): string | undefined {
        if (!this.projectDir) { return undefined; }
        return path.join(this.projectDir, '.sdlicit', 'sessions', 'sdlicit');
    }

    private get userDir(): string | undefined {
        if (!this.projectDir) { return undefined; }
        return path.join(this.projectDir, '.sdlicit', 'sessions', 'user');
    }

    private get logsDir(): string | undefined {
        if (!this.projectDir) { return undefined; }
        return path.join(this.projectDir, '.sdlicit', 'logs');
    }

    private get indexPath(): string | undefined {
        if (!this.sessionsDir) { return undefined; }
        return path.join(this.sessionsDir, 'index.json');
    }

    // --- Session lifecycle ---------------------------------------------------

    async start(projectDir: string, stage: string = 'extension'): Promise<string | null> {
        this.projectDir = projectDir;
        this.tomInteractions = [];
        this.logEntries = [];
        this.interactionsSinceStateUpdate = 0;
        this.interactionsSinceCompact = 0;
        this.tokenWarningShown = false;
        this.startedAt = new Date().toISOString();

        // Ensure directories exist
        for (const dir of [this.chatDir, this.sdlicitDir, this.userDir, this.logsDir]) {
            if (dir && !fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        }

        try {
            const result = await this.client.sessionStart(stage);
            this.sessionId = result.session_id;
            this.client.log(`Session started: ${this.sessionId}`);
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.client.log(`Session start failed (using local ID): ${message}`);
            this.sessionId = `local_${Date.now().toString(36)}`;
        }

        try {
            const config = await this.client.getConfig();
            if (config.model_context_window && config.compact_threshold_pct) {
                this.compactionThreshold = Math.round(
                    config.model_context_window * config.compact_threshold_pct,
                );
            }
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.client.log(`Could not load compaction threshold, using default: ${message}`);
        }

        // Add to session index
        this.updateIndex('active');

        this.recordToM('session_start', 'extension', { stage });
        this.recordLog('session_start', { stage, session_id: this.sessionId });
        return this.sessionId;
    }

    async end(): Promise<void> {
        if (!this.sessionId || !this.projectDir) { return; }

        this.recordToM('session_end', 'extension');
        this.recordLog('session_end');

        try {
            const result = await this.client.sessionEnd();
            this.persistBackendResult(result);
            this.client.log(`Session ended: ${this.sessionId}`);
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.client.log(`Session end failed: ${message}`);
            // Still persist local data
            this.persistLocalChat();
        }

        // Update index status before clearing sessionId
        this.updateIndex('closed');
        this.persistSessionMeta();
        this.sessionId = null;
    }

    async compact(): Promise<Record<string, unknown> | null> {
        try {
            const result = await this.client.sessionCompact();
            if (result.status === 'ok' && this.sdlicitDir && this.sessionId) {
                fs.mkdirSync(this.sdlicitDir, { recursive: true });
                // Keep old compact for reference
                const compactPath = path.join(this.sdlicitDir, `${this.sessionId}_compact.json`);
                let existing: Record<string, unknown>[] = [];
                if (fs.existsSync(compactPath)) {
                    try {
                        const old = JSON.parse(fs.readFileSync(compactPath, 'utf-8'));
                        existing = Array.isArray(old) ? old : [old];
                    } catch { /* overwrite if corrupt */ }
                }
                existing.push({
                    ...result,
                    compacted_at: new Date().toISOString(),
                    interactions_compacted: this.interactionsSinceCompact,
                });
                fs.writeFileSync(compactPath, JSON.stringify(existing, null, 2));
                this.interactionsSinceCompact = 0;
            }
            return result;
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.client.log(`Session compact failed: ${message}`);
            return null;
        }
    }

    async savePreference(key: string, value: string, note: string = ''): Promise<void> {
        try {
            const result = await this.client.savePreference(key, value, note);
            this.recordToM('preference_saved', 'extension', { key, value });
            if (result.user_model && this.userDir) {
                fs.mkdirSync(this.userDir, { recursive: true });
                fs.writeFileSync(
                    path.join(this.userDir, 'user_model.json'),
                    JSON.stringify(result.user_model, null, 2),
                );
            }
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.client.log(`Save preference failed: ${message}`);
        }
    }

    // --- ToM recording (Tier 1: agent, input, output — no tool calls) --------

    /**
     * Record a ToM-relevant interaction (agent, input, output).
     * These go to sessions/chat/<sid>.json for the backend ToM to read.
     * Tool calls and internal routing are excluded — only user-facing I/O.
     */
    recordToM(eventType: string, agent: string = '', data?: Record<string, unknown>): void {
        if (!this.loggingEnabled) { return; }
        const interaction: ToMInteraction = {
            timestamp: new Date().toISOString(),
            event_type: eventType,
            agent,
            data,
        };
        this.tomInteractions.push(interaction);
        this.interactionsSinceCompact++;
        this.interactionsSinceStateUpdate++;
        this.persistLocalChat();
        this.updateIndex('active');

        // Periodically update state of mind
        if (this.interactionsSinceStateUpdate >= STATE_OF_MIND_UPDATE_INTERVAL) {
            this.updateStateOfMind();
            this.interactionsSinceStateUpdate = 0;
        }
    }

    // --- Detailed logging (everything — API calls, file access, timing) ------

    /**
     * Record a log entry. These go to .sdlicit/logs/<sid>.jsonl for debugging.
     */
    recordLog(type: string, data?: Record<string, unknown>): void {
        if (!this.loggingEnabled) { return; }
        const entry = JSON.stringify({
            ts: new Date().toISOString(),
            type,
            ...data,
        });
        this.logEntries.push(entry);
        this.persistLog();
    }

    /**
     * Convenience: log a workflow event (combined ToM + log)
     */
    logEvent(type: string, data?: Record<string, unknown>): void {
        this.recordToM(type, 'extension', data);
        this.recordLog(type, data);
    }

    // --- Token threshold -------------------------------------------------

    private async handleTokenThresholdExceeded(): Promise<void> {
        const choice = await vscode.window.showWarningMessage(
            `Sdlicit: Session tokens exceeded ${this.compactionThreshold.toLocaleString()} — ToM quality may degrade. Auto-compacting session…`,
            'OK',
            'View Details',
        );
        if (choice === 'View Details') {
            vscode.commands.executeCommand('sdlicit.showTokenDetails');
        }
        // Auto-compact in background
        this.compact().then((result) => {
            if (result?.status === 'ok') {
                this.client.log('Auto-compact completed after token threshold reached.');
            }
        }).catch(() => { /* best effort */ });
    }

    // --- State of mind -------------------------------------------------------

    private updateStateOfMind(): void {
        if (!this.userDir || !this.sessionId) { return; }

        const recent = this.tomInteractions.slice(-10);
        const state: StateOfMind = this.inferStateOfMind(recent);

        // Write current state (single file, overwritten)
        try {
            fs.mkdirSync(this.userDir, { recursive: true });
            fs.writeFileSync(
                path.join(this.userDir, 'state_of_mind.json'),
                JSON.stringify(state, null, 2),
            );
        } catch { /* best effort */ }

        // Append to per-session log (for pattern building)
        const logPath = path.join(this.userDir, 'state_of_mind_log.json');
        try {
            let log: Array<StateOfMind & { session_id: string }> = [];
            if (fs.existsSync(logPath)) {
                log = JSON.parse(fs.readFileSync(logPath, 'utf-8'));
            }
            log.push({ ...state, session_id: this.sessionId });
            // Cap log at 200 entries to avoid bloat
            if (log.length > 200) { log = log.slice(-200); }
            fs.writeFileSync(logPath, JSON.stringify(log, null, 2));
        } catch { /* best effort */ }
    }

    private inferStateOfMind(recent: ToMInteraction[]): StateOfMind {
        // Rule-based inference from recent interactions
        let frustration: StateOfMind['frustration_level'] = 'none';
        let engagement: StateOfMind['engagement'] = 'moderate';
        let confidence: StateOfMind['confidence'] = 'moderate';
        const goals: string[] = [];
        const notes: string[] = [];

        let skipCount = 0;
        let errorCount = 0;
        let detailedInputCount = 0;

        for (const i of recent) {
            if (i.event_type === 'user_skip') { skipCount++; }
            if (i.event_type.includes('error') || i.event_type.includes('retry')) { errorCount++; }
            if (i.data?.input && String(i.data.input).length > 200) { detailedInputCount++; }
            if (i.event_type === 'guided_flow_start') { goals.push('creating artifacts via guided flow'); }
            if (i.event_type === 'sow_create') { goals.push('creating SOW'); }
            if (i.event_type === 'adr_create') { goals.push('creating ADR'); }
        }

        if (errorCount >= 3) {
            frustration = 'high';
            notes.push(`${errorCount} errors in recent interactions`);
        } else if (errorCount >= 1) {
            frustration = 'low';
        }

        if (skipCount >= 3) {
            engagement = 'low';
            confidence = 'high'; // skipping = knows what they want
            notes.push(`${skipCount} skips — user likely experienced`);
        } else if (detailedInputCount >= 3) {
            engagement = 'high';
            notes.push('User providing detailed inputs');
        }

        return {
            updated_at: new Date().toISOString(),
            frustration_level: frustration,
            engagement,
            confidence,
            inferred_goals: [...new Set(goals)],
            notes: notes.join('; '),
        };
    }

    // --- Session index -------------------------------------------------------

    private updateIndex(status: 'active' | 'closed' | 'crashed'): void {
        if (!this.indexPath || !this.sessionId) { return; }
        try {
            let index: SessionIndex = { recent: [], last_session_id: null, active_session_id: null };
            if (fs.existsSync(this.indexPath)) {
                index = JSON.parse(fs.readFileSync(this.indexPath, 'utf-8'));
            }

            const now = new Date().toISOString();
            const existing = index.recent.findIndex(s => s.session_id === this.sessionId);

            if (existing >= 0) {
                // Update existing entry
                index.recent[existing].last_event_at = now;
                index.recent[existing].status = status;
            } else {
                // Add new entry at the front
                index.recent.unshift({
                    session_id: this.sessionId,
                    started_at: this.startedAt ?? now,
                    last_event_at: now,
                    status,
                });
            }

            // Cap index size
            if (index.recent.length > MAX_INDEX_ENTRIES) {
                index.recent = index.recent.slice(0, MAX_INDEX_ENTRIES);
            }

            index.last_session_id = this.sessionId;
            index.active_session_id = status === 'active' ? this.sessionId : null;

            fs.mkdirSync(path.dirname(this.indexPath), { recursive: true });
            fs.writeFileSync(this.indexPath, JSON.stringify(index, null, 2));
        } catch { /* best effort */ }
    }

    // --- Session meta (for tree view token summary) --------------------------

    private persistSessionMeta(): void {
        if (!this.sdlicitDir || !this.sessionId) { return; }
        try {
            const metaDir = path.join(this.sdlicitDir, this.sessionId);
            fs.mkdirSync(metaDir, { recursive: true });
            const usage = this.client.totalUsage;
            const meta = {
                session_id: this.sessionId,
                started_at: this.startedAt,
                last_event_at: new Date().toISOString(),
                status: 'closed',
                stage: 'extension',
                event_count: this.tomInteractions.length,
                tokens: {
                    prompt_tokens: usage.prompt,
                    completion_tokens: usage.completion,
                    total_tokens: usage.total,
                    calls: usage.calls,
                    by_agent: Object.fromEntries(
                        Object.entries(usage.byAgent).map(([k, v]) => [k, {
                            prompt_tokens: v.prompt,
                            completion_tokens: v.completion,
                            total_tokens: v.total,
                            calls: 0,
                        }]),
                    ),
                },
            };
            fs.writeFileSync(path.join(metaDir, 'meta.json'), JSON.stringify(meta, null, 2));
        } catch { /* best effort */ }
    }

    // --- Persistence ---------------------------------------------------------

    /** Persist Tier 1 raw interactions locally (sessions/chat/<sid>.json) */
    private persistLocalChat(): void {
        if (!this.chatDir || !this.sessionId) { return; }
        try {
            fs.mkdirSync(this.chatDir, { recursive: true });
            const rawSession = {
                session_id: this.sessionId,
                started_at: this.startedAt ?? this.tomInteractions[0]?.timestamp ?? new Date().toISOString(),
                ended_at: this.tomInteractions[this.tomInteractions.length - 1]?.timestamp,
                stage: 'extension',
                interactions: this.tomInteractions,
            };
            fs.writeFileSync(
                path.join(this.chatDir, `${this.sessionId}.json`),
                JSON.stringify(rawSession, null, 2),
            );
        } catch { /* best effort */ }
    }

    /** Persist all three tiers from the backend's /session/end response */
    private persistBackendResult(result: Record<string, unknown>): void {
        if (!this.projectDir || !this.sessionId) { return; }

        // Tier 1: raw session → sessions/chat/<sid>.json
        if (result.raw_session && this.chatDir) {
            fs.mkdirSync(this.chatDir, { recursive: true });
            fs.writeFileSync(
                path.join(this.chatDir, `${this.sessionId}.json`),
                JSON.stringify(result.raw_session, null, 2),
            );
        } else {
            // No backend raw session — persist our local one
            this.persistLocalChat();
        }

        // Tier 2: session model → sessions/sdlicit/<sid>.json
        if (result.session_model && this.sdlicitDir) {
            fs.mkdirSync(this.sdlicitDir, { recursive: true });
            fs.writeFileSync(
                path.join(this.sdlicitDir, `${this.sessionId}.json`),
                JSON.stringify(result.session_model, null, 2),
            );
        }

        // Tier 3: user model → sessions/user/user_model.json
        if (result.user_model && this.userDir) {
            fs.mkdirSync(this.userDir, { recursive: true });
            fs.writeFileSync(
                path.join(this.userDir, 'user_model.json'),
                JSON.stringify(result.user_model, null, 2),
            );
        }
    }

    /** Persist detailed log to .sdlicit/logs/<sid>.jsonl */
    private persistLog(): void {
        if (!this.logsDir || !this.sessionId) { return; }
        try {
            fs.mkdirSync(this.logsDir, { recursive: true });
            fs.writeFileSync(
                path.join(this.logsDir, `${this.sessionId}.jsonl`),
                this.logEntries.join('\n') + '\n',
            );
        } catch { /* best effort */ }
    }
}
