// ---------------------------------------------------------------------------
// Sdlicit — Shared Types
// ---------------------------------------------------------------------------
// Data shapes used by providers and DataService. These represent the contract
// between the local file system, backend API, and UI components.
// ---------------------------------------------------------------------------

// ── Artifact Types ──────────────────────────────────────────────────────────

export type ArtifactType = 'sow' | 'requirement' | 'decision' | 'scenario' | 'personas' | 'stories';
export type ArtifactStatus = 'draft' | 'active' | 'accepted' | 'rejected' | 'deprecated' | 'superseded';
export type QualityLevel = 'gold' | 'silver' | 'bronze' | 'unassessed';

export interface ArtifactSection {
    id: string;
    title: string;
    content: string;
    status: 'empty' | 'partial' | 'complete';
    prompt?: string;           // Guided prompt for this section
    companionHints?: string[]; // AI-generated hints for improvement
}

export interface ArtifactTraces {
    upstream: string[];   // IDs of artifacts this traces FROM
    downstream: string[]; // IDs of artifacts this traces TO
    implements: string[]; // Requirement IDs this artifact implements
    supersedes: string;   // ID of artifact this supersedes (empty if none)
    testedBy: string[];   // BDD scenario IDs that test this artifact
}

export interface BddCoverage {
    totalScenarios: number;
    accepted: number;
    pending: number;
    rejected: number;
}

export interface Artifact {
    id: string;
    type: ArtifactType;
    title: string;
    status: ArtifactStatus;
    quality: { target?: QualityLevel; current?: QualityLevel };
    sections: ArtifactSection[];
    traces: ArtifactTraces;
    bddCoverage?: BddCoverage;
    filePath: string;
    createdAt: string;
    updatedAt: string;
}

// ── BDD / Gherkin Types ─────────────────────────────────────────────────────

export type ScenarioStatus = 'pending' | 'accepted' | 'rejected' | 'revised';
export type ScenarioOrigin = 'generated' | 'edge_case' | 'user';
export type ImportanceLevel = 'critical' | 'important' | 'nice-to-have';

export interface BddScenario {
    id: string;
    title: string;
    situation: string;
    gherkin: string;
    status: ScenarioStatus;
    origin: ScenarioOrigin;
    importance?: ImportanceLevel;
    reviewNote?: string;
    edgeCaseOptions?: { label: string; value: string }[];
}

export interface BddFeature {
    requirementId: string;
    title: string;
    scenarios: BddScenario[];
}

// ── Dashboard Types ─────────────────────────────────────────────────────────

export interface CoverageStats {
    requirementsCount: number;
    decisionsCount: number;
    scenariosCount: number;
    requirementsWithScenarios: number;
    traceCoverage: number; // 0-100
    totalLinks?: number;
    validLinks?: number;
    brokenLinks?: number;
}

export interface DashboardSummary {
    coverage: CoverageStats;
    qualityOverview?: { gold: number; silver: number; bronze: number; unassessed: number };
    openQuestions: OpenQuestion[];
    recentActivity: ActivityEntry[];
}

export interface OpenQuestion {
    id: string;
    text: string;
    artifactId: string;
    sectionId?: string;
    source: 'socratic' | 'companion' | 'manual';
}

export interface ActivityEntry {
    timestamp: string;
    action: string;
    artifactId?: string;
    detail?: string;
}

// ── Knowledge Base Types ────────────────────────────────────────────────────

export interface KBSource {
    ref: string;
    title?: string;
    relevance?: number;
    snippet?: string;
}

export interface ExplorerResponse {
    answer: string;
    sources: KBSource[];
}

// ── Chat Types ──────────────────────────────────────────────────────────────

export type ChatMode = 'chat' | 'explore' | 'agent';

export interface ChatEntry {
    role: 'user' | 'assistant' | 'system';
    content: string;
    sources?: KBSource[];
    toolsUsed?: string[];
    agentsUsed?: string[];
    tokensUsed?: number;
    tokensByAgent?: Record<string, { prompt: number; completion: number; total: number; calls: number }>;
    timestamp: number;
    mode?: ChatMode;
}

// ── Session Types ───────────────────────────────────────────────────────────

export type SessionStatus = 'active' | 'closed' | 'crashed';

export interface SessionSummary {
    session_id: string;
    started_at: string;
    last_event_at: string;
    status: SessionStatus;
}

export interface SessionMeta {
    session_id: string;
    started_at: string;
    last_event_at: string;
    status: SessionStatus;
    stage: string;
    event_count: number;
    tokens: TokenUsage;
}

export interface TokenUsage {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    calls: number;
    by_agent: Record<string, { prompt_tokens: number; completion_tokens: number; total_tokens: number; calls: number }>;
}

export interface SessionEvent {
    kind: string;
    seq: number;
    ts: string;
    [key: string]: unknown;
}

// ── Elicitation Types ───────────────────────────────────────────────────────

export interface ElicitationResponse {
    sessionId: string;
    question: string;
    options?: { label: string; value: string }[];
    done: boolean;
    draft?: string;
}

export interface CompanionObservation {
    id: string;
    text: string;
    severity: 'info' | 'warning' | 'suggestion';
    actionable: boolean;
}
