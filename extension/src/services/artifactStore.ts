// ---------------------------------------------------------------------------
// Sdlicit — Artifact File Store
// ---------------------------------------------------------------------------
// Local file I/O for .sdlicit/artifacts/ — reads and writes artifacts.
// The backend determines canonical filenames and paths via `artifact_meta`.
// Frontends use `saveByMeta()` to persist at the backend-specified location.
// ---------------------------------------------------------------------------

import * as fs from 'fs';
import * as path from 'path';

/**
 * Artifact metadata returned by the backend in every generation response.
 * Frontends must use `relative_path` for saving.
 */
export interface ArtifactMeta {
    tag: string;           // e.g. "ADR-0001", "SOW", "SRS"
    filename: string;      // e.g. "ADR-0001-use-react.md"
    relative_path: string; // e.g. "adr/ADR-0001-use-react.md"
    artifact_type: 'sow' | 'adr' | 'srs' | 'personas' | 'stories' | 'bdd';
}

export interface LocalArtifact {
    id: string;
    type: 'sow' | 'adr' | 'srs' | 'personas' | 'stories' | 'gherkin' | 'unknown';
    title: string;
    filePath: string;
    relativePath: string;
    dateModified: string;
}

export class ArtifactStore {
    private projectDir: string;

    constructor(projectDir: string) {
        this.projectDir = projectDir;
    }

    get artifactsDir(): string {
        return path.join(this.projectDir, '.sdlicit', 'artifacts');
    }

    /**
     * Save artifact content using the backend-provided ArtifactMeta.
     * This is the preferred method — ensures consistent naming across frontends.
     */
    saveByMeta(meta: ArtifactMeta, content: string): string {
        const filePath = path.join(this.artifactsDir, meta.relative_path);
        fs.mkdirSync(path.dirname(filePath), { recursive: true });
        fs.writeFileSync(filePath, content, 'utf-8');
        return filePath;
    }

    /**
     * List all artifacts from the .sdlicit/artifacts/ folder.
     */
    listArtifacts(): LocalArtifact[] {
        const dir = this.artifactsDir;
        if (!fs.existsSync(dir)) { return []; }

        const artifacts: LocalArtifact[] = [];
        this.walkDir(dir, artifacts);
        return artifacts.sort((a, b) => a.id.localeCompare(b.id));
    }

    private walkDir(dir: string, results: LocalArtifact[]): void {
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
            const fullPath = path.join(dir, entry.name);
            if (entry.isDirectory()) {
                this.walkDir(fullPath, results);
            } else if (entry.name.endsWith('.md') || entry.name.endsWith('.feature') || entry.name.endsWith('.json')) {
                const stat = fs.statSync(fullPath);
                const relativePath = path.relative(this.artifactsDir, fullPath);
                results.push({
                    id: this.inferId(entry.name, relativePath),
                    type: this.inferType(entry.name, relativePath),
                    title: this.inferTitle(entry.name, fullPath),
                    filePath: fullPath,
                    relativePath,
                    dateModified: stat.mtime.toISOString().split('T')[0],
                });
            }
        }
    }

    private inferType(filename: string, relativePath: string): LocalArtifact['type'] {
        // Canonical paths first
        if (relativePath === 'sow.md') { return 'sow'; }
        if (relativePath === 'srs.md') { return 'srs'; }
        if (relativePath === 'personas.md' || relativePath === 'personas.json') { return 'personas'; }
        if (relativePath === 'stories.md' || relativePath === 'stories.json') { return 'stories'; }
        // Subfolder-based detection
        const parts = relativePath.split(path.sep);
        if (parts[0] === 'adr') { return 'adr'; }
        if (parts[0] === 'bdd') { return 'gherkin'; }
        // Legacy fallback: filename-based
        const lower = filename.toLowerCase();
        if (lower.startsWith('sow')) { return 'sow'; }
        if (lower.startsWith('adr') || lower.includes('adr')) { return 'adr'; }
        if (lower.startsWith('srs') || lower.includes('srs')) { return 'srs'; }
        if (lower.includes('persona')) { return 'personas'; }
        if (lower.includes('stor')) { return 'stories'; }
        if (lower.endsWith('.feature') || lower.includes('gherkin') || lower.includes('bdd')) { return 'gherkin'; }
        return 'unknown';
    }

    private inferId(filename: string, relativePath: string): string {
        return filename.replace(/\.(md|feature|json)$/, '');
    }

    private inferTitle(filename: string, fullPath: string): string {
        try {
            const content = fs.readFileSync(fullPath, 'utf-8');
            const match = content.match(/^#\s+(.+)$/m);
            if (match) { return match[1]; }
        } catch { /* fall through */ }
        return filename
            .replace(/\.(md|feature|json)$/, '')
            .replace(/[-_]/g, ' ')
            .replace(/^\w/, c => c.toUpperCase());
    }

    /**
     * Save artifact content to the .sdlicit/artifacts/ folder.
     * @deprecated Use saveByMeta() with backend-provided artifact_meta instead.
     */
    saveArtifact(filename: string, content: string, subfolder?: string): string {
        const dir = subfolder
            ? path.join(this.artifactsDir, subfolder)
            : this.artifactsDir;
        fs.mkdirSync(dir, { recursive: true });
        const filePath = path.join(dir, filename);
        fs.writeFileSync(filePath, content, 'utf-8');
        return filePath;
    }

    /**
     * Read artifact content.
     */
    readArtifact(filePath: string): string | null {
        try {
            return fs.readFileSync(filePath, 'utf-8');
        } catch {
            return null;
        }
    }

    /**
     * Get the SOW file content (canonical: sow.md).
     */
    getLatestSOW(): string | null {
        // Canonical location
        const canonical = path.join(this.artifactsDir, 'sow.md');
        if (fs.existsSync(canonical)) {
            return this.readArtifact(canonical);
        }
        // Legacy fallback
        const artifacts = this.listArtifacts().filter(a => a.type === 'sow');
        if (artifacts.length === 0) { return null; }
        artifacts.sort((a, b) => b.dateModified.localeCompare(a.dateModified));
        return this.readArtifact(artifacts[0].filePath);
    }

    /**
     * Get the SRS file content (canonical: srs.md).
     */
    getLatestSRS(): string | null {
        // Canonical location
        const canonical = path.join(this.artifactsDir, 'srs.md');
        if (fs.existsSync(canonical)) {
            return this.readArtifact(canonical);
        }
        // Legacy fallback
        const artifacts = this.listArtifacts().filter(a => a.type === 'srs');
        if (artifacts.length === 0) { return null; }
        artifacts.sort((a, b) => b.dateModified.localeCompare(a.dateModified));
        return this.readArtifact(artifacts[0].filePath);
    }

    /**
     * Get all ADR file contents.
     */
    getADRContents(): string[] {
        return this.listArtifacts()
            .filter(a => a.type === 'adr')
            .map(a => this.readArtifact(a.filePath))
            .filter((c): c is string => c !== null);
    }
}
