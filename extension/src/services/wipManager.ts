// ---------------------------------------------------------------------------
// Sdlicit — WIP Manager
// ---------------------------------------------------------------------------
// Utility for detecting and prompting about Work-In-Progress data.
// Shows a QuickPick when WIP data exists, letting users choose between
// resuming their previous session or starting fresh.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';

export type WipType = 'sow' | 'srs' | 'adr' | 'bdd' | 'stories';

const WIP_LABELS: Record<WipType, string> = {
    sow: 'Statement of Work',
    srs: 'Software Requirements Specification',
    adr: 'Architectural Decision Record',
    bdd: 'BDD Scenarios',
    stories: 'User Stories',
};

export type WipDecision = 'resume' | 'fresh';

export class WipManager {
    constructor(private readonly globalStoragePath: string) {}

    private wipFilePath(type: WipType): string {
        return path.join(this.globalStoragePath, 'wip', `wip_${type}.json`);
    }

    /** Check if meaningful WIP data exists for the given type. */
    hasWip(type: WipType): boolean {
        const wp = this.wipFilePath(type);
        if (!fs.existsSync(wp)) { return false; }
        try {
            const raw = JSON.parse(fs.readFileSync(wp, 'utf-8'));
            return this.hasContent(type, raw);
        } catch {
            return false;
        }
    }

    /** Delete WIP data for the given type. */
    clearWip(type: WipType): void {
        const wp = this.wipFilePath(type);
        if (fs.existsSync(wp)) { fs.unlinkSync(wp); }
    }

    /**
     * Show a QuickPick asking the user to resume or start fresh.
     * Returns 'resume' if user wants cached data, 'fresh' if they want a clean slate.
     * Only shows the prompt if WIP data exists; returns 'fresh' otherwise.
     */
    async promptIfWipExists(type: WipType): Promise<WipDecision> {
        if (!this.hasWip(type)) { return 'fresh'; }

        const wp = this.wipFilePath(type);
        let savedAt = '';
        try {
            const raw = JSON.parse(fs.readFileSync(wp, 'utf-8'));
            if (raw.savedAt) {
                const d = new Date(raw.savedAt);
                savedAt = ` (saved ${d.toLocaleDateString()} ${d.toLocaleTimeString()})`;
            }
        } catch { /* ignore */ }

        const label = WIP_LABELS[type];
        const items: vscode.QuickPickItem[] = [
            {
                label: '$(history) Resume previous session',
                description: `Continue where you left off${savedAt}`,
                detail: `Restore your in-progress ${label} data`,
            },
            {
                label: '$(add) Start fresh',
                description: 'Discard cached data and begin anew',
                detail: `Create a new ${label} from scratch`,
            },
        ];

        const picked = await vscode.window.showQuickPick(items, {
            title: `Sdlicit — ${label}`,
            placeHolder: `Previous ${label} session found. Resume or start fresh?`,
        });

        if (!picked || picked.label.includes('Start fresh')) {
            this.clearWip(type);
            return 'fresh';
        }
        return 'resume';
    }

    private hasContent(type: WipType, raw: any): boolean {
        switch (type) {
            case 'sow':
                return raw.sections?.some((s: any) => s.content?.trim());
            case 'srs':
                return (raw.textSections?.some((s: any) => s.content?.trim()))
                    || (raw.requirements?.length > 0);
            case 'adr':
                return raw.fields?.some((f: any) => f.content?.trim());
            case 'bdd':
                return raw.scenarios?.length > 0;
            case 'stories':
                return raw.stories?.length > 0;
            default:
                return false;
        }
    }
}
