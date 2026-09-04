import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { KBSyncService } from '../src/services/kbSyncService';
import type { SdlicitClient } from '../src/services/sdlicitClient';

// A bare stub is enough: syncFromBackend() is fire and forget with its own
// .catch(), and none of the methods under test here await it.
const stubClient = { scanDocuments: async () => ({ documents: [] }) } as unknown as SdlicitClient;

describe('KBSyncService', () => {
    let projectDir: string;
    let service: KBSyncService;

    beforeEach(() => {
        projectDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sdlicit-kbsync-'));
        service = new KBSyncService(stubClient);
    });

    afterEach(() => {
        fs.rmSync(projectDir, { recursive: true, force: true });
    });

    it('knowledgeDir is undefined before a project dir is set', () => {
        expect(service.knowledgeDir).toBeUndefined();
    });

    it('setProjectDir creates the knowledge directory', () => {
        service.setProjectDir(projectDir);
        const expected = path.join(projectDir, '.sdlicit', 'knowledge');
        expect(service.knowledgeDir).toBe(expected);
        expect(fs.existsSync(expected)).toBe(true);
    });

    it('scan returns an empty list when the knowledge dir has no supported files', () => {
        service.setProjectDir(projectDir);
        fs.writeFileSync(path.join(service.knowledgeDir!, 'notes.exe'), 'x');
        expect(service.scan()).toEqual([]);
    });

    it('scan picks up files with supported extensions as pending by default', () => {
        service.setProjectDir(projectDir);
        fs.writeFileSync(path.join(service.knowledgeDir!, 'design.md'), '# Design\n');

        const files = service.scan();
        expect(files).toHaveLength(1);
        expect(files[0].fileName).toBe('design.md');
        expect(files[0].status).toBe('pending');
    });

    it('scan reports synced when the manifest hash matches the current content', () => {
        // The manifest is only read from disk once, inside setProjectDir's
        // loadManifest() call, so both the file and the manifest need to
        // exist on disk before setProjectDir runs, not after.
        const knowledgeDir = path.join(projectDir, '.sdlicit', 'knowledge');
        fs.mkdirSync(knowledgeDir, { recursive: true });
        const filePath = path.join(knowledgeDir, 'design.md');
        fs.writeFileSync(filePath, '# Design\n');

        // Compute the same hash the service uses (sha256 of the base64
        // content, truncated to 16 hex chars) to seed a matching manifest,
        // rather than reaching into the service's private hashing method.
        const base64Content = fs.readFileSync(filePath).toString('base64');
        const hash = crypto.createHash('sha256').update(base64Content).digest('hex').slice(0, 16);

        fs.mkdirSync(path.join(projectDir, '.sdlicit'), { recursive: true });
        fs.writeFileSync(
            path.join(projectDir, '.sdlicit', 'kb_manifest.json'),
            JSON.stringify({
                entries: [{ fileName: 'design.md', contentHash: hash, syncedAt: '2026-01-01' }],
            }),
        );

        service.setProjectDir(projectDir);
        const files = service.scan();
        expect(files[0].status).toBe('synced');
        expect(files[0].lastSynced).toBe('2026-01-01');
    });

    it('scan reports pending when the manifest hash no longer matches', () => {
        // As above: both files need to exist on disk before setProjectDir
        // runs, otherwise this would pass for the wrong reason (no
        // manifest entry found at all, rather than a genuine mismatch).
        const knowledgeDir = path.join(projectDir, '.sdlicit', 'knowledge');
        fs.mkdirSync(knowledgeDir, { recursive: true });
        fs.writeFileSync(path.join(knowledgeDir, 'design.md'), '# Design v1\n');

        fs.mkdirSync(path.join(projectDir, '.sdlicit'), { recursive: true });
        fs.writeFileSync(
            path.join(projectDir, '.sdlicit', 'kb_manifest.json'),
            JSON.stringify({
                entries: [{ fileName: 'design.md', contentHash: 'stale-hash', syncedAt: '2026-01-01' }],
            }),
        );

        service.setProjectDir(projectDir);
        const files = service.scan();
        expect(files[0].status).toBe('pending');
        // lastSynced reflects the manifest entry's own syncedAt whenever a
        // matching filename is found, independent of whether the hash
        // still matches, it is not cleared just because content changed.
        expect(files[0].lastSynced).toBe('2026-01-01');
    });

    it('getFiles delegates to scan', () => {
        service.setProjectDir(projectDir);
        fs.writeFileSync(path.join(service.knowledgeDir!, 'a.txt'), 'hello');
        expect(service.getFiles()).toEqual(service.scan());
    });

    it('getStatus defaults to pending for a file that was never scanned', () => {
        service.setProjectDir(projectDir);
        expect(service.getStatus('never-seen.md')).toBe('pending');
    });
});
