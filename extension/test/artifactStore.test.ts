import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { ArtifactStore } from '../src/services/artifactStore';

describe('ArtifactStore', () => {
    let projectDir: string;
    let store: ArtifactStore;

    beforeEach(() => {
        projectDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sdlicit-artifact-store-'));
        store = new ArtifactStore(projectDir);
    });

    afterEach(() => {
        fs.rmSync(projectDir, { recursive: true, force: true });
    });

    it('saveByMeta writes to artifactsDir joined with the relative path', () => {
        const filePath = store.saveByMeta(
            {
                tag: 'ADR-0001',
                filename: 'ADR-0001-use-jwt.md',
                relative_path: 'adr/ADR-0001-use-jwt.md',
                artifact_type: 'adr',
            },
            '# ADR-0001: Use JWT\n',
        );
        expect(filePath).toBe(path.join(store.artifactsDir, 'adr', 'ADR-0001-use-jwt.md'));
        expect(fs.readFileSync(filePath, 'utf-8')).toBe('# ADR-0001: Use JWT\n');
    });

    it('saveByMeta creates nested directories that do not exist yet', () => {
        store.saveByMeta(
            {
                tag: 'BDD-alex',
                filename: 'alex.feature',
                relative_path: 'bdd/alex.feature',
                artifact_type: 'bdd',
            },
            'Feature: something\n',
        );
        expect(fs.existsSync(path.join(store.artifactsDir, 'bdd'))).toBe(true);
    });

    it('listArtifacts returns an empty array when artifactsDir does not exist', () => {
        expect(store.listArtifacts()).toEqual([]);
    });

    it('listArtifacts infers type from the canonical single file paths', () => {
        fs.mkdirSync(store.artifactsDir, { recursive: true });
        fs.writeFileSync(path.join(store.artifactsDir, 'sow.md'), '# SOW\n');
        fs.writeFileSync(path.join(store.artifactsDir, 'srs.md'), '# SRS\n');

        const artifacts = store.listArtifacts();
        const byPath = Object.fromEntries(artifacts.map((a) => [a.relativePath, a]));
        expect(byPath['sow.md'].type).toBe('sow');
        expect(byPath['srs.md'].type).toBe('srs');
    });

    it('listArtifacts infers type from the adr and bdd subfolders', () => {
        fs.mkdirSync(path.join(store.artifactsDir, 'adr'), { recursive: true });
        fs.mkdirSync(path.join(store.artifactsDir, 'bdd'), { recursive: true });
        fs.writeFileSync(
            path.join(store.artifactsDir, 'adr', 'ADR-0001-x.md'),
            '# ADR-0001\n',
        );
        fs.writeFileSync(path.join(store.artifactsDir, 'bdd', 'alex.feature'), 'Feature: x\n');

        const artifacts = store.listArtifacts();
        const byPath = Object.fromEntries(artifacts.map((a) => [a.relativePath, a]));
        expect(byPath[path.join('adr', 'ADR-0001-x.md')].type).toBe('adr');
        expect(byPath[path.join('bdd', 'alex.feature')].type).toBe('gherkin');
    });

    it('listArtifacts falls back to filename based type inference outside canonical paths', () => {
        fs.mkdirSync(store.artifactsDir, { recursive: true });
        fs.writeFileSync(path.join(store.artifactsDir, 'my-personas.json'), '[]');

        const artifacts = store.listArtifacts();
        expect(artifacts[0].type).toBe('personas');
    });

    it('listArtifacts ignores files with unsupported extensions', () => {
        fs.mkdirSync(store.artifactsDir, { recursive: true });
        fs.writeFileSync(path.join(store.artifactsDir, 'notes.txt'), 'not an artifact');
        expect(store.listArtifacts()).toEqual([]);
    });

    it('listArtifacts is sorted by id', () => {
        fs.mkdirSync(store.artifactsDir, { recursive: true });
        fs.writeFileSync(path.join(store.artifactsDir, 'zzz.md'), '# Z\n');
        fs.writeFileSync(path.join(store.artifactsDir, 'aaa.md'), '# A\n');

        const artifacts = store.listArtifacts();
        expect(artifacts.map((a) => a.id)).toEqual(['aaa', 'zzz']);
    });

    it('inferTitle prefers the first H1 heading over the filename', () => {
        fs.mkdirSync(store.artifactsDir, { recursive: true });
        fs.writeFileSync(
            path.join(store.artifactsDir, 'adr-0001-x.md'),
            'preamble\n# Use JWT for sessions\nmore text\n',
        );
        const artifacts = store.listArtifacts();
        expect(artifacts[0].title).toBe('Use JWT for sessions');
    });

    it('inferTitle falls back to a humanised filename when there is no H1', () => {
        fs.mkdirSync(store.artifactsDir, { recursive: true });
        fs.writeFileSync(path.join(store.artifactsDir, 'my_stories-list.md'), 'no heading here');
        const artifacts = store.listArtifacts();
        expect(artifacts[0].title).toBe('My stories list');
    });

    it('getLatestSOW reads the canonical sow.md when present', () => {
        fs.mkdirSync(store.artifactsDir, { recursive: true });
        fs.writeFileSync(path.join(store.artifactsDir, 'sow.md'), 'canonical sow');
        expect(store.getLatestSOW()).toBe('canonical sow');
    });

    it('getLatestSOW returns null when nothing exists', () => {
        expect(store.getLatestSOW()).toBeNull();
    });

    it('getADRContents reads every adr file under the adr subfolder', () => {
        fs.mkdirSync(path.join(store.artifactsDir, 'adr'), { recursive: true });
        fs.writeFileSync(path.join(store.artifactsDir, 'adr', 'ADR-0001.md'), 'first');
        fs.writeFileSync(path.join(store.artifactsDir, 'adr', 'ADR-0002.md'), 'second');

        const contents = store.getADRContents();
        expect(contents.sort()).toEqual(['first', 'second']);
    });

    it('readArtifact returns null for a file that does not exist', () => {
        expect(store.readArtifact(path.join(projectDir, 'missing.md'))).toBeNull();
    });
});
