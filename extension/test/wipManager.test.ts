import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { WipManager } from '../src/services/wipManager';

// hasWip()/clearWip() touch only fs, not any vscode UI API, so they are
// exercised for real here. promptIfWipExists() (the QuickPick prompt) is
// not covered, it is fundamentally a live VS Code UI interaction.
describe('WipManager', () => {
    let storagePath: string;
    let manager: WipManager;

    const writeWip = (type: string, data: unknown) => {
        const dir = path.join(storagePath, 'wip');
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(path.join(dir, `wip_${type}.json`), JSON.stringify(data));
    };

    beforeEach(() => {
        storagePath = fs.mkdtempSync(path.join(os.tmpdir(), 'sdlicit-wip-'));
        manager = new WipManager(storagePath);
    });

    afterEach(() => {
        fs.rmSync(storagePath, { recursive: true, force: true });
    });

    it('hasWip is false when no wip file exists at all', () => {
        expect(manager.hasWip('sow')).toBe(false);
    });

    it('hasWip is false when the wip file exists but is corrupt JSON', () => {
        const dir = path.join(storagePath, 'wip');
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(path.join(dir, 'wip_sow.json'), '{not valid json');
        expect(manager.hasWip('sow')).toBe(false);
    });

    it('sow wip is meaningful only when a section has non blank content', () => {
        writeWip('sow', { sections: [{ content: '   ' }] });
        expect(manager.hasWip('sow')).toBe(false);

        writeWip('sow', { sections: [{ content: 'Project scope' }] });
        expect(manager.hasWip('sow')).toBe(true);
    });

    it('srs wip is meaningful from either text sections or a requirements list', () => {
        writeWip('srs', { textSections: [], requirements: [] });
        expect(manager.hasWip('srs')).toBe(false);

        writeWip('srs', { requirements: ['REQ-01'] });
        expect(manager.hasWip('srs')).toBe(true);

        writeWip('srs', { textSections: [{ content: 'Some requirement text' }] });
        expect(manager.hasWip('srs')).toBe(true);
    });

    it('adr wip is meaningful only when a field has non blank content', () => {
        writeWip('adr', { fields: [{ content: '' }] });
        expect(manager.hasWip('adr')).toBe(false);

        writeWip('adr', { fields: [{ content: 'Context and problem statement' }] });
        expect(manager.hasWip('adr')).toBe(true);
    });

    it('bdd wip is meaningful when there is at least one scenario', () => {
        writeWip('bdd', { scenarios: [] });
        expect(manager.hasWip('bdd')).toBe(false);

        writeWip('bdd', { scenarios: [{ title: 'Login' }] });
        expect(manager.hasWip('bdd')).toBe(true);
    });

    it('stories wip is meaningful when there is at least one story', () => {
        writeWip('stories', { stories: [] });
        expect(manager.hasWip('stories')).toBe(false);

        writeWip('stories', { stories: [{ title: 'As a user...' }] });
        expect(manager.hasWip('stories')).toBe(true);
    });

    it('clearWip removes the file so hasWip becomes false again', () => {
        writeWip('sow', { sections: [{ content: 'Something real' }] });
        expect(manager.hasWip('sow')).toBe(true);

        manager.clearWip('sow');
        expect(manager.hasWip('sow')).toBe(false);
    });

    it('clearWip on a type with no file is a no-op, not an error', () => {
        expect(() => manager.clearWip('adr')).not.toThrow();
    });
});
