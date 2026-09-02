// ---------------------------------------------------------------------------
// Sdlicit — Webview Helpers
// ---------------------------------------------------------------------------
// Shared utilities for all webview providers (Canvas, Dashboard, Chat, BDD,
// Status Panel). DRY CSS token system with VS Code theme integration.
// ---------------------------------------------------------------------------

import * as crypto from 'crypto';

/** Generate a random nonce for Content-Security-Policy. */
export function getNonce(): string {
    return crypto.randomBytes(16).toString('hex');
}

/** Escape HTML entities. */
export function escapeHtml(text: string): string {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/** Quality badge with color coding. */
export function qualityBadge(quality: string | undefined): string {
    if (!quality) { return ''; }
    const colors: Record<string, string> = {
        gold: 'var(--vscode-charts-yellow)',
        silver: 'var(--vscode-foreground)',
        bronze: 'var(--vscode-charts-orange)',
    };
    const color = colors[quality] ?? 'var(--vscode-descriptionForeground)';
    return `<span class="quality-badge" style="color:${color};font-weight:600;text-transform:uppercase;font-size:.7em;letter-spacing:.05em">${escapeHtml(quality)}</span>`;
}

/** Status dot indicator. */
export function statusDot(status: string): string {
    const colors: Record<string, string> = {
        draft: 'var(--vscode-charts-blue)',
        active: 'var(--vscode-charts-green)',
        accepted: 'var(--vscode-charts-green)',
        rejected: 'var(--vscode-errorForeground)',
        deprecated: 'var(--vscode-disabledForeground)',
        superseded: 'var(--vscode-disabledForeground)',
        pending: 'var(--vscode-charts-yellow)',
    };
    const color = colors[status] ?? 'var(--vscode-descriptionForeground)';
    return `<span class="status-dot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:4px" title="${escapeHtml(status)}"></span>`;
}

/** Type icon (codicon name) for artifact types. */
export function typeIcon(type: string): string {
    const icons: Record<string, string> = {
        sow: 'file-text',
        requirement: 'list-unordered',
        decision: 'symbol-structure',
        scenario: 'beaker',
        personas: 'person',
        stories: 'list-tree',
    };
    return icons[type] ?? 'file';
}

/** Type label (colored pill) for artifact types. */
export function typeLabel(type: string): string {
    const cls: Record<string, string> = {
        sow: 'pill-sow',
        requirement: 'pill-req',
        decision: 'pill-adr',
        scenario: 'pill-scn',
        personas: 'pill-sow',
        stories: 'pill-req',
    };
    const labels: Record<string, string> = {
        sow: 'SOW', requirement: 'REQ', decision: 'ADR', scenario: 'SCN', personas: 'PER', stories: 'UST',
    };
    return `<span class="pill ${cls[type] ?? ''}">${labels[type] ?? type}</span>`;
}

// ── Shared CSS ──────────────────────────────────────────────────────────────

const SHARED_CSS = `
    /* ── Design Tokens ── */
    :root {
        --sdl-r-sm: 3px;
        --sdl-r-md: 6px;
        --sdl-r-lg: 10px;
        --sdl-sp-xs: 4px;
        --sdl-sp-sm: 8px;
        --sdl-sp-md: 16px;
        --sdl-sp-lg: 24px;
        --sdl-sp-xl: 32px;
        --sdl-border: var(--vscode-widget-border, rgba(128,128,128,.35));
        --sdl-surface-input: var(--vscode-input-background);
        --sdl-surface-card: var(--vscode-sideBar-background);
        --sdl-surface-elevated: var(--vscode-editorWidget-background);
        --sdl-color-info: var(--vscode-charts-blue);
        --sdl-color-success: var(--vscode-testing-iconPassed);
        --sdl-color-warning: var(--vscode-charts-yellow);
        --sdl-color-danger: var(--vscode-errorForeground);
        --sdl-color-accent: var(--vscode-focusBorder);
    }

    /* ── Reset ── */
    * { box-sizing: border-box; }
    body {
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        color: var(--vscode-foreground);
        background: var(--vscode-editor-background);
        padding: var(--sdl-sp-md);
        margin: 0;
        line-height: 1.5;
    }
    h1, h2, h3, h4 {
        color: var(--vscode-foreground);
        margin: 0 0 var(--sdl-sp-sm);
        font-weight: 600;
    }
    h1 { font-size: 1.4em; }
    h2 { font-size: 1.2em; }
    h3 { font-size: 1.05em; }
    a { color: var(--vscode-textLink-foreground); text-decoration: none; }
    a:hover { text-decoration: underline; }

    /* ── Layout utilities ── */
    .flex { display: flex; }
    .flex-col { flex-direction: column; }
    .flex-1 { flex: 1; }
    .items-center { align-items: center; }
    .items-start { align-items: flex-start; }
    .justify-between { justify-content: space-between; }
    .flex-wrap { flex-wrap: wrap; }
    .gap-xs { gap: var(--sdl-sp-xs); }
    .gap-sm { gap: var(--sdl-sp-sm); }
    .gap-md { gap: var(--sdl-sp-md); }
    .mt-xs { margin-top: var(--sdl-sp-xs); }
    .mt-sm { margin-top: var(--sdl-sp-sm); }
    .mt-md { margin-top: var(--sdl-sp-md); }
    .mb-sm { margin-bottom: var(--sdl-sp-sm); }
    .mb-md { margin-bottom: var(--sdl-sp-md); }
    .text-xs { font-size: .8em; }
    .text-sm { font-size: .9em; }
    .text-muted { color: var(--vscode-descriptionForeground); }
    .text-center { text-align: center; }
    .hidden { display: none !important; }

    /* ── Cards ── */
    .card {
        background: var(--sdl-surface-card);
        border: 1px solid var(--sdl-border);
        border-radius: var(--sdl-r-md);
        padding: var(--sdl-sp-md);
        margin-bottom: var(--sdl-sp-sm);
    }
    .card-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: var(--sdl-sp-sm);
    }
    .card-flat {
        background: var(--sdl-surface-card);
        border: 1px solid var(--sdl-border);
        border-radius: var(--sdl-r-sm);
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        margin-bottom: var(--sdl-sp-sm);
    }

    /* ── Buttons ── */
    .btn {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 5px 12px;
        border: none;
        border-radius: var(--sdl-r-sm);
        font-size: var(--vscode-font-size);
        cursor: pointer;
        font-family: var(--vscode-font-family);
        transition: opacity .15s;
        white-space: nowrap;
    }
    .btn:hover { opacity: .85; }
    .btn:disabled { opacity: .4; cursor: not-allowed; }
    .btn-primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .btn-primary:hover { background: var(--vscode-button-hoverBackground); }
    .btn-secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
    .btn-secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }
    .btn-success { background: var(--sdl-color-success); color: #fff; }
    .btn-warning { background: var(--sdl-color-warning); color: #000; }
    .btn-danger { background: var(--sdl-color-danger); color: #fff; }
    .btn-sm { padding: 3px 8px; font-size: .85em; }
    .btn.selected { outline: 2px solid var(--sdl-color-accent); outline-offset: 1px; }

    /* ── Badges & Pills ── */
    .badge { display: inline-flex; align-items: center; padding: 1px 6px; border-radius: 10px; font-size: .75em; font-weight: 500; }
    .badge-status { padding: 2px 8px; border-radius: 3px; font-size: .7em; text-transform: uppercase; letter-spacing: .05em; }
    .badge-accepted, .badge-done, .badge-complete { background: var(--sdl-color-success); color: #fff; }
    .badge-pending, .badge-partial { background: var(--sdl-color-warning); color: #000; }
    .badge-rejected { background: var(--sdl-color-danger); color: #fff; }
    .badge-draft, .badge-empty { background: var(--sdl-color-info); color: #fff; }
    .badge-info { background: var(--sdl-color-info); color: #fff; }
    .badge-warning { background: var(--sdl-color-warning); color: #000; }
    .badge-suggestion { background: var(--vscode-charts-purple); color: #fff; }

    .pill { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: .75em; font-weight: 500; }
    .pill-sow { background: color-mix(in srgb, var(--sdl-color-info) 20%, transparent); color: var(--sdl-color-info); border: 1px solid color-mix(in srgb, var(--sdl-color-info) 40%, transparent); }
    .pill-req { background: color-mix(in srgb, var(--sdl-color-success) 20%, transparent); color: var(--sdl-color-success); border: 1px solid color-mix(in srgb, var(--sdl-color-success) 40%, transparent); }
    .pill-adr { background: color-mix(in srgb, var(--sdl-color-warning) 20%, transparent); color: var(--sdl-color-warning); border: 1px solid color-mix(in srgb, var(--sdl-color-warning) 40%, transparent); }
    .pill-scn { background: color-mix(in srgb, var(--vscode-charts-orange) 20%, transparent); color: var(--vscode-charts-orange); border: 1px solid color-mix(in srgb, var(--vscode-charts-orange) 40%, transparent); }

    /* ── Inputs ── */
    input, textarea, select {
        background: var(--sdl-surface-input);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border, var(--sdl-border));
        border-radius: var(--sdl-r-sm);
        padding: 5px 8px;
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        width: 100%;
    }
    input:focus, textarea:focus, select:focus {
        outline: 1px solid var(--sdl-color-accent);
        border-color: var(--sdl-color-accent);
    }
    textarea { resize: vertical; min-height: 60px; }

    /* ── Progress bar ── */
    .progress-bar {
        height: 6px;
        background: color-mix(in srgb, var(--vscode-progressBar-background) 25%, transparent);
        border-radius: 3px;
        overflow: hidden;
    }
    .progress-fill {
        height: 100%;
        background: var(--vscode-progressBar-background);
        transition: width .3s ease;
        border-radius: 3px;
    }

    /* ── Trace nodes ── */
    .trace-graph { display: flex; align-items: center; gap: var(--sdl-sp-xs); flex-wrap: wrap; }
    .trace-row { display: flex; align-items: center; gap: var(--sdl-sp-xs); flex-wrap: wrap; margin-bottom: 4px; }
    .trace-label { font-size: .75em; color: var(--vscode-descriptionForeground); min-width: 90px; font-weight: 500; }
    .trace-node {
        display: inline-flex; align-items: center; gap: 3px; padding: 2px 8px;
        border-radius: var(--sdl-r-sm); font-size: .8em; cursor: pointer;
        background: var(--vscode-badge-background); color: var(--vscode-badge-foreground);
    }
    .trace-node:hover { opacity: .8; background: var(--vscode-button-hoverBackground); }
    .trace-node.clickable { text-decoration: underline; text-underline-offset: 2px; }
    .trace-arrow { color: var(--vscode-descriptionForeground); font-size: .75em; }

    /* ── Section editor ── */
    .section-panel {
        border: 1px solid var(--sdl-border);
        border-radius: var(--sdl-r-md);
        margin-bottom: var(--sdl-sp-md);
        overflow: hidden;
    }
    .section-panel.section-active { border-color: var(--sdl-color-accent); }
    .section-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        background: var(--sdl-surface-card);
        border-bottom: 1px solid var(--sdl-border);
        cursor: pointer;
    }
    .section-header:hover { background: var(--vscode-list-hoverBackground); }
    .section-body { padding: var(--sdl-sp-md); }
    .section-body.collapsed { display: none; }
    .section-spinner {
        display: inline-flex; align-items: center; gap: var(--sdl-sp-xs);
        color: var(--vscode-descriptionForeground); font-size: .85em; margin-top: var(--sdl-sp-xs);
    }

    /* ── Companion observations ── */
    .companion-panel {
        border-left: 3px solid var(--vscode-charts-purple);
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        margin: var(--sdl-sp-sm) 0;
        background: color-mix(in srgb, var(--vscode-charts-purple) 5%, var(--sdl-surface-elevated));
        border-radius: 0 var(--sdl-r-sm) var(--sdl-r-sm) 0;
    }
    .companion-panel .dismiss-btn {
        float: right; background: none; border: none; color: var(--vscode-descriptionForeground);
        cursor: pointer; font-size: 1.1em;
    }
    .companion-observation {
        display: flex; align-items: flex-start; gap: var(--sdl-sp-sm);
        padding: var(--sdl-sp-xs) 0; border-bottom: 1px solid color-mix(in srgb, var(--sdl-border) 50%, transparent);
    }
    .companion-observation:last-child { border-bottom: none; }

    /* ── Elicitation ── */
    .elicitation-panel {
        border: 1px solid var(--sdl-color-accent);
        border-radius: var(--sdl-r-md);
        padding: var(--sdl-sp-md);
        margin: var(--sdl-sp-sm) 0;
        background: color-mix(in srgb, var(--sdl-color-info) 5%, var(--sdl-surface-elevated));
    }
    .elicitation-question { font-weight: 500; margin-bottom: var(--sdl-sp-sm); }
    .elicitation-options { display: flex; flex-direction: column; gap: var(--sdl-sp-xs); }
    .option-btn {
        display: block; width: 100%; text-align: left;
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        border: 1px solid var(--sdl-border); border-radius: var(--sdl-r-sm);
        background: var(--sdl-surface-card); color: var(--vscode-foreground);
        cursor: pointer; font-size: var(--vscode-font-size);
        font-family: var(--vscode-font-family);
        transition: border-color .15s, background .15s;
    }
    .option-btn:hover { border-color: var(--sdl-color-accent); background: var(--vscode-list-hoverBackground); }

    /* ── Inline questions (canvas) ── */
    .inline-question {
        border-left: 3px solid var(--sdl-color-warning);
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        margin: var(--sdl-sp-xs) 0;
        background: color-mix(in srgb, var(--sdl-color-warning) 5%, transparent);
        border-radius: 0 var(--sdl-r-sm) var(--sdl-r-sm) 0;
        font-size: .9em;
    }

    /* ── Tabs (dashboard) ── */
    .tab-bar { display: flex; border-bottom: 1px solid var(--sdl-border); margin-bottom: var(--sdl-sp-md); gap: 0; }
    .tab-btn {
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        cursor: pointer; border: none; background: none;
        color: var(--vscode-descriptionForeground); font-size: .9em;
        border-bottom: 2px solid transparent;
        font-family: var(--vscode-font-family);
        transition: color .15s, border-color .15s;
    }
    .tab-btn:hover { color: var(--vscode-foreground); }
    .tab-btn.active { color: var(--vscode-foreground); border-bottom-color: var(--sdl-color-accent); font-weight: 500; }
    .tab-content { display: none; }
    .tab-content.active { display: block; }

    /* ── Dashboard metrics ── */
    .metric-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--sdl-sp-sm); }
    .metric-label { font-size: .85em; color: var(--vscode-descriptionForeground); }
    .metric-value { font-size: .85em; font-weight: 600; }

    /* ── Chat container ── */
    .chat-container { display: flex; flex-direction: column; height: calc(100vh - 24px); padding: 0; overflow: hidden; }
    .chat-messages { flex: 1; overflow-y: auto; padding: var(--sdl-sp-sm) var(--sdl-sp-md); }
    .chat-input-area {
        border-top: 1px solid var(--sdl-border);
        padding: var(--sdl-sp-sm) var(--sdl-sp-md) var(--sdl-sp-md);
        background: var(--sdl-surface-card);
        flex-shrink: 0;
    }

    /* ── Chat messages (flat rows, no bubbles) ── */
    .message { padding: var(--sdl-sp-sm) 0; }
    .message + .message { border-top: 1px solid color-mix(in srgb, var(--sdl-border) 40%, transparent); }
    .message-header { display: flex; align-items: center; gap: var(--sdl-sp-xs); margin-bottom: 2px; }
    .message-avatar {
        width: 20px; height: 20px; display: flex; align-items: center; justify-content: center;
        border-radius: 50%; background: var(--vscode-badge-background); color: var(--vscode-badge-foreground);
        font-size: .65em; flex-shrink: 0;
    }
    .message-name { font-weight: 600; font-size: .85em; }
    .message-time { font-size: .7em; color: var(--vscode-descriptionForeground); margin-left: auto; }
    .message-body { padding-left: 28px; font-size: .9em; line-height: 1.5; }
    .message-body p { margin: 0 0 var(--sdl-sp-xs); }
    .message-body code {
        background: var(--sdl-surface-input); padding: 1px 4px;
        border-radius: 3px; font-family: var(--vscode-editor-font-family); font-size: .9em;
    }
    .source-actions { padding-left: 28px; margin-top: var(--sdl-sp-xs); display: flex; flex-wrap: wrap; gap: 4px; }
    .source-badge {
        display: inline-flex; align-items: center; gap: 2px; padding: 1px 6px;
        border-radius: 3px; font-size: .7em; background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground); cursor: pointer;
    }
    .source-badge:hover { opacity: .8; }

    /* ── Chat mode bar ── */
    .chat-mode-bar { display: flex; align-items: center; gap: var(--sdl-sp-sm); margin-top: var(--sdl-sp-xs); }
    .chat-toolbar-btn {
        background: none; border: none; color: var(--vscode-descriptionForeground);
        cursor: pointer; padding: 2px 4px; border-radius: var(--sdl-r-sm); display: inline-flex;
    }
    .chat-toolbar-btn:hover { background: var(--vscode-toolbar-hoverBackground); color: var(--vscode-foreground); }
    .context-chip {
        display: inline-flex; align-items: center; gap: 3px;
        padding: 1px 6px; border-radius: 3px; font-size: .75em;
        background: var(--vscode-badge-background); color: var(--vscode-badge-foreground);
    }

    /* ── Chat panel (sc- prefix) ── */
    .sc-root { display:flex; flex-direction:column; height:calc(100vh - 4px); overflow:hidden; font-family:var(--vscode-font-family); }
    .sc-header {
        display:flex; align-items:center; gap:4px;
        padding:4px 8px; flex-shrink:0;
        border-bottom:1px solid var(--vscode-panel-border, var(--sdl-border));
    }
    .sc-header-title { font-size:.85em; font-weight:600; }
    .sc-hbtn {
        background:none; border:none; color:var(--vscode-descriptionForeground);
        cursor:pointer; padding:3px 5px; border-radius:3px; display:inline-flex;
        align-items:center; justify-content:center; flex-shrink:0;
    }
    .sc-hbtn:hover { background:var(--vscode-toolbar-hoverBackground); color:var(--vscode-foreground); }
    .sc-mode-sel {
        background:var(--vscode-dropdown-background, var(--vscode-input-background));
        color:var(--vscode-dropdown-foreground, var(--vscode-foreground));
        border:1px solid var(--vscode-dropdown-border, var(--vscode-input-border, var(--sdl-border)));
        border-radius:3px;
        padding:2px 4px; font-size:.8em; font-weight:600; cursor:pointer;
        font-family:var(--vscode-font-family);
        -webkit-appearance:none; appearance:none;
        padding-right:16px;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 16 16'%3E%3Cpath fill='%23888' d='M4 6l4 4 4-4z'/%3E%3C/svg%3E");
        background-repeat:no-repeat; background-position:right 3px center;
    }
    .sc-mode-sel option { background:var(--vscode-dropdown-listBackground, var(--vscode-dropdown-background, var(--vscode-input-background))); color:var(--vscode-dropdown-foreground, var(--vscode-foreground)); }
    .sc-mode-sel:hover { border-color:var(--vscode-focusBorder); }
    .sc-mode-sel:focus { outline:1px solid var(--vscode-focusBorder); border-color:transparent; }
    .sc-spacer { flex:1; }

    /* Context bar */
    .sc-ctx {
        display:flex; flex-wrap:wrap; gap:3px; padding:3px 8px;
        border-bottom:1px solid var(--vscode-panel-border, var(--sdl-border));
        font-size:.75em;
    }
    .sc-link-chip {
        display:inline-flex; align-items:center; padding:1px 6px;
        border-radius:3px; background:var(--vscode-badge-background);
        color:var(--vscode-badge-foreground);
    }
    .sc-chip {
        display:inline-flex; align-items:center; gap:3px;
        padding:1px 6px; border-radius:3px;
        background:var(--vscode-badge-background); color:var(--vscode-badge-foreground);
    }
    .sc-chip-x { cursor:pointer; opacity:.6; }
    .sc-chip-x:hover { opacity:1; }

    /* Messages area */
    .sc-msgs { flex:1; overflow-y:auto; padding:0; }
    .sc-msg { padding:10px 12px; }
    .sc-msg + .sc-msg { border-top:1px solid var(--vscode-panel-border, color-mix(in srgb, var(--sdl-border) 40%, transparent)); }
    .sc-msg-hd { display:flex; align-items:center; gap:5px; margin-bottom:3px; }
    .sc-av {
        width:20px; height:20px; display:flex; align-items:center; justify-content:center;
        border-radius:50%; font-size:.6em; font-weight:700; flex-shrink:0;
        background:var(--vscode-badge-background); color:var(--vscode-badge-foreground);
    }
    .sc-av-s { background:var(--vscode-descriptionForeground); color:var(--vscode-editor-background); }
    .sc-name { font-weight:600; font-size:.8em; }
    .sc-time { font-size:.7em; color:var(--vscode-descriptionForeground); margin-left:auto; }
    .sc-mode-tag {
        font-size:.6em; padding:1px 4px; border-radius:2px;
        text-transform:uppercase; letter-spacing:.03em; font-weight:600;
        background:color-mix(in srgb, var(--vscode-descriptionForeground) 12%, transparent);
        color:var(--vscode-descriptionForeground);
    }
    .sc-msg-bd { padding-left:25px; font-size:.87em; line-height:1.5; color:var(--vscode-foreground); }
    .sc-msg-bd p { margin:0 0 5px; }
    .sc-msg-bd code {
        background:var(--vscode-textCodeBlock-background); padding:1px 3px;
        border-radius:2px; font-family:var(--vscode-editor-font-family); font-size:.9em;
    }
    .sc-msg-bd h3, .sc-msg-bd h4 { margin:6px 0 3px; font-size:.92em; }
    .sc-msg-bd ul { margin:0 0 5px; padding-left:1.2em; }
    .sc-msg-bd li { margin-bottom:1px; }
    .sc-pre {
        background:var(--vscode-textCodeBlock-background); border:1px solid var(--vscode-panel-border, var(--sdl-border));
        border-radius:3px; padding:6px 8px; margin:4px 0; font-size:.85em;
        overflow-x:auto; white-space:pre-wrap; word-break:break-word;
    }

    /* Tools, sources, actions */
    .sc-tools { padding-left:25px; margin-bottom:3px; display:flex; align-items:center; gap:3px; }
    .sc-tool {
        display:inline-flex; padding:1px 5px; border-radius:2px; font-size:.65em;
        background:color-mix(in srgb, var(--vscode-descriptionForeground) 10%, transparent);
        color:var(--vscode-descriptionForeground); font-weight:500;
    }
    .sc-sources { padding-left:25px; margin-top:6px; display:flex; flex-direction:column; gap:3px; }
    .sc-sources-label { font-size:.65em; color:var(--vscode-descriptionForeground); font-weight:600; text-transform:uppercase; letter-spacing:.04em; margin-bottom:2px; }
    .sc-src {
        display:inline-flex; align-items:center; gap:4px; padding:3px 8px; border-radius:3px; font-size:.7em;
        background:var(--vscode-badge-background); color:var(--vscode-badge-foreground); cursor:pointer;
        border:1px solid transparent; transition:border-color .15s, background .15s; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
    }
    .sc-src:hover { border-color:var(--vscode-focusBorder); background:var(--vscode-list-hoverBackground); }
    .sc-src-icon { flex-shrink:0; opacity:.7; }
    .sc-src-rel { margin-left:auto; padding-left:6px; font-size:.85em; opacity:.6; flex-shrink:0; }
    .sc-src:active { opacity:.7; }
    .sc-actions { padding-left:25px; margin-top:4px; }
    .sc-insert-btn {
        background:none; border:1px solid var(--vscode-panel-border, var(--sdl-border));
        color:var(--vscode-foreground); cursor:pointer; font-size:.75em;
        padding:2px 6px; border-radius:3px; display:inline-block;
    }
    .sc-insert-btn:hover { background:var(--vscode-toolbar-hoverBackground); }
    .sc-ins-grp { margin-top:2px; }
    .sc-ins-sum {
        cursor:pointer; font-size:.75em; color:var(--vscode-descriptionForeground);
        padding:1px 0; user-select:none; list-style:none;
    }
    .sc-ins-sum::-webkit-details-marker { display:none; }
    .sc-ins-sum::before { content:'\u25B6 '; font-size:.6em; }
    details[open] > .sc-ins-sum::before { content:'\u25BC '; }
    .sc-ins-sum:hover { color:var(--vscode-foreground); }
    .sc-ins-opts { padding-left:10px; }
    .sc-ins-opt {
        font-size:.75em; padding:2px 5px; cursor:pointer; border-radius:2px;
        color:var(--vscode-descriptionForeground);
    }
    .sc-ins-opt:hover { background:var(--vscode-list-hoverBackground); color:var(--vscode-foreground); }

    /* Trace log (per-message agent/tool log) */
    .sc-trace {
        padding-left:25px; margin:2px 0 3px;
    }
    .sc-trace-sum {
        display:flex; align-items:center; gap:4px; cursor:pointer; user-select:none;
        font-size:.7em; color:var(--vscode-descriptionForeground); list-style:none;
        padding:2px 0;
    }
    .sc-trace-sum::-webkit-details-marker { display:none; }
    .sc-trace-sum svg { opacity:.6; }
    .sc-trace-sum:hover { color:var(--vscode-foreground); }
    .sc-trace-sum:hover svg { opacity:1; }
    .sc-trace-body {
        display:flex; flex-wrap:wrap; gap:3px; padding:3px 0 2px 16px;
    }
    .sc-trace-agent {
        display:inline-flex; align-items:center; gap:3px;
        padding:1px 6px; border-radius:2px; font-size:.7em; font-weight:500;
        background:color-mix(in srgb, var(--vscode-descriptionForeground) 10%, transparent);
        color:var(--vscode-descriptionForeground);
    }
    .sc-trace-agent::before { content:'\u2699 '; font-size:.85em; }
    .sc-trace-agent-tokens {
        font-size:.9em; opacity:.75; font-variant-numeric:tabular-nums; margin-left:2px;
    }
    .sc-trace-tool {
        display:inline-flex; padding:1px 6px; border-radius:2px; font-size:.7em;
        background:color-mix(in srgb, var(--vscode-descriptionForeground) 8%, transparent);
        color:var(--vscode-descriptionForeground);
    }
    .sc-trace-tokens {
        display:inline-flex; padding:1px 6px; border-radius:2px; font-size:.7em;
        color:var(--vscode-descriptionForeground); font-variant-numeric:tabular-nums;
    }

    /* Token bar */
    .sc-token-bar {
        position:relative; height:14px; border-radius:2px;
        background:color-mix(in srgb, var(--vscode-descriptionForeground) 10%, transparent);
        margin-bottom:4px; overflow:hidden;
    }
    .sc-token-fill {
        position:absolute; left:0; top:0; bottom:0;
        background:color-mix(in srgb, var(--vscode-descriptionForeground) 25%, transparent);
        border-radius:2px; transition:width .3s ease;
    }
    .sc-token-label {
        position:relative; z-index:1; display:flex; align-items:center; justify-content:center;
        height:100%; font-size:.6em; font-variant-numeric:tabular-nums;
        color:var(--vscode-descriptionForeground);
    }

    /* Recent chats strip */
    .sc-recent {
        display:flex; gap:4px; margin-top:4px; overflow-x:auto;
    }
    .sc-recent-item {
        flex:0 0 auto; max-width:140px; padding:3px 6px; border-radius:3px;
        border:1px solid var(--vscode-panel-border, var(--sdl-border));
        cursor:pointer; font-size:.7em; overflow:hidden;
    }
    .sc-recent-item:hover { background:var(--vscode-list-hoverBackground); }
    .sc-recent-title {
        display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        color:var(--vscode-foreground);
    }
    .sc-recent-meta {
        display:block; font-size:.85em; color:var(--vscode-descriptionForeground);
    }

    /* Input area */
    .sc-input {
        border-top:1px solid var(--vscode-panel-border, var(--sdl-border));
        padding:6px 8px 14px; flex-shrink:0;
    }
    .sc-input-row { display:flex; gap:4px; align-items:flex-end; }
    .sc-input-row textarea {
        flex:1; resize:none; min-height:32px; max-height:140px;
        background:var(--vscode-input-background); color:var(--vscode-input-foreground);
        border:1px solid var(--vscode-input-border, var(--sdl-border));
        border-radius:4px; padding:6px 8px; font-family:var(--vscode-font-family);
        font-size:var(--vscode-font-size); line-height:1.35;
    }
    .sc-input-row textarea:focus { outline:none; border-color:var(--vscode-focusBorder); }
    .sc-send {
        width:26px; height:26px; border-radius:4px; border:none; cursor:pointer;
        background:var(--vscode-button-background); color:var(--vscode-button-foreground);
        display:flex; align-items:center; justify-content:center; flex-shrink:0;
    }
    .sc-send:hover { background:var(--vscode-button-hoverBackground); }
    .sc-send:disabled { opacity:.35; cursor:not-allowed; }
    .sc-input-ft {
        display:flex; align-items:center; gap:6px; margin-top:3px; padding:0 1px;
    }
    .sc-ft-label { font-size:.7em; color:var(--vscode-descriptionForeground); margin-left:auto; }

    /* Dots animation */
    .sc-dots span {
        display:inline-block; width:4px; height:4px; border-radius:50%;
        background:var(--vscode-descriptionForeground); margin:0 1px;
        animation:scPulse 1.2s ease-in-out infinite;
    }
    .sc-dots span:nth-child(2) { animation-delay:.2s; }
    .sc-dots span:nth-child(3) { animation-delay:.4s; }
    @keyframes scPulse { 0%,80%,100%{opacity:.25;transform:scale(.8)} 40%{opacity:1;transform:scale(1)} }

    /* Welcome */
    .sc-welcome {
        display:flex; flex-direction:column; align-items:center;
        justify-content:center; padding:32px 16px; text-align:center; flex:1;
    }
    .sc-welcome-title { font-size:1em; font-weight:600; margin-bottom:2px; }
    .sc-welcome-sub { font-size:.78em; color:var(--vscode-descriptionForeground); margin-bottom:14px; }
    .sc-welcome-hints { display:flex; flex-direction:column; gap:4px; width:100%; max-width:260px; }
    .sc-whint {
        display:flex; align-items:center; gap:6px; padding:6px 10px;
        border:1px solid var(--vscode-panel-border, var(--sdl-border)); border-radius:4px;
        cursor:pointer; font-size:.8em; color:var(--vscode-foreground);
    }
    .sc-whint:hover { background:var(--vscode-list-hoverBackground); }
    .sc-whint-slash {
        font-family:var(--vscode-editor-font-family); font-size:.85em; font-weight:600;
        color:var(--vscode-descriptionForeground);
    }

    /* Slash popup */
    .sc-slash-pop {
        position:absolute; bottom:100%; left:0; right:0;
        background:var(--vscode-editorWidget-background, var(--sdl-surface-card));
        border:1px solid var(--vscode-editorWidget-border, var(--sdl-border));
        border-radius:4px; padding:2px 0; margin-bottom:2px;
        box-shadow:0 -2px 6px rgba(0,0,0,.12); z-index:10;
    }
    .sc-slash-it { padding:5px 10px; cursor:pointer; font-size:.8em; }
    .sc-slash-it:hover { background:var(--vscode-list-hoverBackground); }

    /* History list */
    .sc-hist-list { flex:1; overflow-y:auto; }
    .sc-hist-item {
        padding:8px 12px; cursor:pointer; border-bottom:1px solid var(--vscode-panel-border, color-mix(in srgb, var(--sdl-border) 40%, transparent));
    }
    .sc-hist-item:hover { background:var(--vscode-list-hoverBackground); }
    .sc-hist-active { background:var(--vscode-list-activeSelectionBackground); color:var(--vscode-list-activeSelectionForeground); }
    .sc-hist-title { font-size:.83em; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .sc-hist-meta {
        display:flex; align-items:center; gap:4px; font-size:.7em;
        color:var(--vscode-descriptionForeground); margin-top:1px;
    }
    .sc-hist-active .sc-hist-meta { color:var(--vscode-list-activeSelectionForeground); opacity:.7; }
    .sc-hist-del {
        background:none; border:none; color:inherit; cursor:pointer;
        font-size:1.1em; padding:0 2px; opacity:.5; line-height:1;
    }
    .sc-hist-del:hover { opacity:1; }

    /* ── Loading spinner ── */
    .spinner {
        display: inline-block; width: 14px; height: 14px;
        border: 2px solid var(--vscode-descriptionForeground); border-top-color: transparent;
        border-radius: 50%; animation: spin .8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .loading-dots span {
        display: inline-block; width: 5px; height: 5px; border-radius: 50%;
        background: var(--vscode-descriptionForeground); margin: 0 2px;
        animation: dotPulse 1.2s ease-in-out infinite;
    }
    .loading-dots span:nth-child(2) { animation-delay: .2s; }
    .loading-dots span:nth-child(3) { animation-delay: .4s; }
    @keyframes dotPulse { 0%,80%,100% { opacity: .3; transform: scale(.8); } 40% { opacity: 1; transform: scale(1); } }

    /* ── Wizard/Stepper (BDD review) ── */
    .wizard-steps { margin-bottom: var(--sdl-sp-md); }
    .wizard-step {
        border: 1px solid var(--sdl-border); border-radius: var(--sdl-r-md);
        margin-bottom: var(--sdl-sp-sm); overflow: hidden;
    }
    .wizard-step-active { border-color: var(--sdl-color-accent); }
    .wizard-step-completed { border-left: 3px solid var(--sdl-color-success); }
    .wizard-step-header {
        display: flex; align-items: center; gap: var(--sdl-sp-sm);
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        background: var(--sdl-surface-card); cursor: pointer;
    }
    .wizard-step-header:hover { background: var(--vscode-list-hoverBackground); }
    .wizard-step-number {
        width: 22px; height: 22px; border-radius: 50%;
        background: var(--vscode-badge-background); color: var(--vscode-badge-foreground);
        display: flex; align-items: center; justify-content: center;
        font-size: .75em; font-weight: 700; flex-shrink: 0;
    }
    .wizard-step-title { font-weight: 500; flex: 1; }
    .wizard-step-badge { font-size: .7em; padding: 1px 6px; border-radius: 3px; text-transform: uppercase; }
    .wizard-badge-done { background: var(--sdl-color-success); color: #fff; }
    .wizard-badge-rejected { background: var(--sdl-color-danger); color: #fff; }
    .wizard-badge-pending { background: var(--sdl-color-warning); color: #000; }
    .wizard-step-body { padding: var(--sdl-sp-md); }
    .stepper-nav {
        display: flex; align-items: center; justify-content: space-between;
        padding: var(--sdl-sp-sm) 0; border-top: 1px solid var(--sdl-border);
    }

    /* ── Code blocks ── */
    pre, code { font-family: var(--vscode-editor-font-family); font-size: var(--vscode-editor-font-size); }
    pre {
        background: var(--vscode-textCodeBlock-background); border: 1px solid var(--sdl-border);
        border-radius: var(--sdl-r-sm); padding: var(--sdl-sp-sm); overflow-x: auto;
        white-space: pre-wrap; word-break: break-word;
    }
    .gherkin-block { font-size: .85em; line-height: 1.4; }
    .gherkin-hidden { display: none; }
    .gherkin-hidden.visible { display: block; }

    /* ── Gherkin formatted display ── */
    .gherkin-formatted {
        font-family: var(--vscode-editor-font-family);
        font-size: .85em;
        line-height: 1.6;
        background: var(--vscode-textCodeBlock-background);
        border: 1px solid var(--sdl-border);
        border-radius: var(--sdl-r-sm);
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        overflow-x: auto;
    }
    .gherkin-line { white-space: pre-wrap; padding: 1px 0; }
    .gherkin-keyword { font-weight: 700; color: var(--vscode-charts-purple); margin-right: 4px; }
    .gherkin-feature { font-size: 1.05em; margin-bottom: 4px; }
    .gherkin-feature .gherkin-keyword { color: var(--sdl-color-info); }
    .gherkin-scenario { margin-top: 8px; margin-bottom: 2px; }
    .gherkin-scenario .gherkin-keyword { color: var(--vscode-charts-green); }
    .gherkin-step { padding-left: 16px; }
    .gherkin-step .gherkin-keyword { color: var(--vscode-charts-purple); }
    .gherkin-step .gherkin-string { color: var(--vscode-charts-yellow, #e5c07b); }
    .gherkin-step .gherkin-param { color: var(--vscode-charts-blue, #61afef); font-style: italic; }
    .gherkin-table { padding-left: 24px; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); }
    .gherkin-table .gherkin-cell-sep { color: var(--vscode-charts-purple); font-weight: 500; }
    .gherkin-comment { color: var(--vscode-descriptionForeground); font-style: italic; }
    .gherkin-tag { color: var(--vscode-charts-orange); font-size: .9em; font-weight: 500; }

    /* ── Lock banner ── */
    .lock-banner {
        display: flex; align-items: center; gap: var(--sdl-sp-sm);
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        background: color-mix(in srgb, var(--sdl-color-info) 10%, transparent);
        border: 1px solid color-mix(in srgb, var(--sdl-color-info) 30%, transparent);
        border-radius: var(--sdl-r-md); margin-bottom: var(--sdl-sp-md); font-size: .9em;
    }

    /* ── Focus mode nav (canvas overview/focus) ── */
    .focus-nav { display: flex; align-items: center; gap: var(--sdl-sp-sm); margin-bottom: var(--sdl-sp-md); }
    .focus-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: var(--vscode-descriptionForeground); opacity: .4;
        cursor: pointer; transition: opacity .2s, transform .2s;
    }
    .focus-dot.active { opacity: 1; transform: scale(1.3); background: var(--sdl-color-accent); }

    /* ── MCP tool toggles ── */
    .tool-toggle { display: flex; align-items: center; gap: var(--sdl-sp-xs); padding: 2px 0; font-size: .85em; cursor: pointer; }
    .tool-toggle input { accent-color: var(--sdl-color-info); }

    /* ── Status panel specifics ── */
    .status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sdl-sp-sm); }
    .status-cell { padding: var(--sdl-sp-sm); border-radius: var(--sdl-r-sm); background: var(--sdl-surface-card); }
    .status-cell .label { font-size: .75em; text-transform: uppercase; letter-spacing: .05em; color: var(--vscode-descriptionForeground); }
    .status-cell .value { font-size: 1.1em; font-weight: 600; margin-top: 2px; }

    /* ── Auto-review & Artifact banner ── */
    .auto-review-hint { font-size: .8em; color: var(--vscode-descriptionForeground); display: flex; align-items: center; gap: var(--sdl-sp-xs); }
    .auto-review-done { flex-direction: column; align-items: flex-start; gap: 2px; }
    .auto-review-item { display: flex; align-items: center; gap: var(--sdl-sp-xs); }
    .artifact-review-banner {
        background: color-mix(in srgb, var(--sdl-color-info) 8%, var(--sdl-bg-elevated));
        border: 1px solid color-mix(in srgb, var(--sdl-color-info) 30%, transparent);
        border-radius: var(--sdl-radius);
        padding: var(--sdl-sp-sm) var(--sdl-sp-md);
        margin-bottom: var(--sdl-sp-md);
        display: flex; align-items: flex-start; gap: var(--sdl-sp-sm);
    }

    /* ── Trace Graph (git-graph style) ── */
    .gg-container { position: relative; overflow-x: auto; }
    .gg-rows { position: relative; }
    .gg-row {
        display: flex; min-height: 40px; border-bottom: 1px solid color-mix(in srgb, var(--sdl-border) 50%, transparent);
        cursor: pointer; transition: background .1s;
    }
    .gg-row:hover { background: var(--vscode-list-hoverBackground); }
    .gg-row-recent { background: color-mix(in srgb, var(--sdl-color-success) 5%, transparent); }
    .gg-graph-cell { position: relative; overflow: visible; flex-shrink: 0; }
    .gg-info-cell {
        flex: 1; display: flex; align-items: center; gap: var(--sdl-sp-sm);
        padding: var(--sdl-sp-xs) var(--sdl-sp-sm); overflow: hidden;
    }
    .gg-type-tag {
        font-size: .65em; font-weight: 700; padding: 1px 5px; border-radius: 2px;
        text-transform: uppercase; letter-spacing: .03em; flex-shrink: 0;
    }
    .gg-id { font-weight: 600; font-size: .82em; flex-shrink: 0; }
    .gg-title { flex: 1; font-size: .85em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .gg-age { font-size: .72em; color: var(--vscode-descriptionForeground); flex-shrink: 0; white-space: nowrap; }
    .gg-connections { position: absolute; top: 0; left: 0; pointer-events: none; z-index: 1; }

    /* ── Diff view ── */
    .diff-view {
        font-family: var(--vscode-editor-font-family);
        font-size: .85em;
        line-height: 1.5;
        background: var(--vscode-textCodeBlock-background);
        border: 1px solid var(--sdl-border);
        border-radius: var(--sdl-r-sm);
        padding: var(--sdl-sp-xs) var(--sdl-sp-sm);
        overflow-x: auto;
        max-height: 300px;
        overflow-y: auto;
    }
    .diff-line { white-space: pre-wrap; padding: 1px 4px; }
    .diff-same { color: var(--vscode-foreground); }
    .diff-added { background: color-mix(in srgb, var(--sdl-color-success) 15%, transparent); color: var(--sdl-color-success); }
    .diff-removed { background: color-mix(in srgb, var(--sdl-color-danger) 15%, transparent); color: var(--sdl-color-danger); text-decoration: line-through; }
    .diff-changed { color: var(--vscode-foreground); }
    .diff-word-del { background: color-mix(in srgb, var(--sdl-color-danger) 20%, transparent); color: var(--sdl-color-danger); text-decoration: line-through; }
    .diff-word-add { background: color-mix(in srgb, var(--sdl-color-success) 20%, transparent); color: var(--sdl-color-success); font-weight: 600; }

    /* ── Inline diff (sentence-level, textarea-style) ── */
    .diff-inline {
        width: 100%;
        background: var(--sdl-surface-input);
        border: 1px solid var(--sdl-border);
        border-radius: var(--sdl-r-sm);
        padding: var(--sdl-sp-sm);
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        line-height: 1.6;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .diff-inline .diff-kept {
        background: color-mix(in srgb, var(--sdl-color-success) 12%, transparent);
        border-radius: 2px;
        padding: 0 1px;
    }
    .diff-inline .diff-removed {
        background: color-mix(in srgb, var(--sdl-color-danger) 18%, transparent);
        color: var(--sdl-color-danger);
        text-decoration: line-through;
        border-radius: 2px;
        padding: 0 1px;
        opacity: 0.8;
    }
    .diff-inline .diff-added {
        background: color-mix(in srgb, var(--sdl-color-success) 25%, transparent);
        color: var(--sdl-color-success);
        font-weight: 600;
        border-radius: 2px;
        padding: 0 1px;
    }

    /* ── Section textarea (SOW / SRS panels) ── */
    .section-textarea {
        width: 100%;
        background: var(--sdl-surface-input);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border, var(--sdl-border));
        border-radius: var(--sdl-r-sm);
        padding: var(--sdl-sp-sm);
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        resize: none;
        overflow: hidden;
        min-height: 60px;
        line-height: 1.5;
    }
    .section-textarea:focus {
        outline: 1px solid var(--sdl-color-accent);
        border-color: var(--sdl-color-accent);
    }

    /* ── Requirement field textarea (SRS cards) ── */
    .req-field-textarea {
        width: 100%;
        background: var(--sdl-surface-input);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border, var(--sdl-border));
        border-radius: var(--sdl-r-sm);
        padding: var(--sdl-sp-sm);
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        resize: none;
        overflow: hidden;
        min-height: 36px;
        line-height: 1.5;
    }
    .req-field-textarea:focus {
        outline: 1px solid var(--sdl-color-accent);
        border-color: var(--sdl-color-accent);
    }

    /* ── Probe input ── */
    .probe-input {
        background: var(--sdl-surface-input);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border, var(--sdl-border));
        border-radius: var(--sdl-r-sm);
        padding: 4px 8px;
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
    }
    .probe-input:focus {
        outline: 1px solid var(--sdl-color-accent);
        border-color: var(--sdl-color-accent);
    }

    /* ── Section content rendered ── */
    .section-content p { margin: 0 0 var(--sdl-sp-xs); }
    .section-content ul { margin: 0 0 var(--sdl-sp-xs); padding-left: 1.5em; }
    .section-content li { margin-bottom: 2px; }
    .section-content h2, .section-content h3, .section-content h4 { margin-top: var(--sdl-sp-sm); }

    /* ── Scrollbar styling ── */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--vscode-scrollbarSlider-background); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--vscode-scrollbarSlider-hoverBackground); }
`;

/**
 * Wrap HTML body content in a full webview page with VS Code theme integration,
 * nonce-based CSP, and the shared design system.
 */
export function wrapHtml(body: string, nonce: string, scripts?: string, extraStyles?: string): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
    <title>Sdlicit</title>
    <style nonce="${nonce}">${SHARED_CSS}${extraStyles ?? ''}</style>
</head>
<body>
    ${body}
    <script nonce="${nonce}">
        const vscode = acquireVsCodeApi();
        ${scripts ?? ''}
    </script>
</body>
</html>`;
}
