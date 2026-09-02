// ---------------------------------------------------------------------------
// Sdlicit — KB Sync Service
// ---------------------------------------------------------------------------
// Tracks which files in .sdlicit/knowledge/ are synced to the RAG KB.
// Provides async ingestion, status tracking, and event emission.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { SdlicitClient } from './sdlicitClient';

export type KBFileStatus = 'synced' | 'uploading' | 'pending' | 'error';

export interface KBFileEntry {
    fileName: string;
    filePath: string;
    relativePath: string;
    status: KBFileStatus;
    lastSynced?: string;
    error?: string;
}

interface ManifestEntry {
    fileName: string;
    syncedAt: string;
    contentHash: string;
}

export class KBSyncService {
    private _onStatusChange = new vscode.EventEmitter<KBFileEntry>();
    readonly onStatusChange = this._onStatusChange.event;

    private _onFilesChange = new vscode.EventEmitter<void>();
    readonly onFilesChange = this._onFilesChange.event;

    private statusMap: Map<string, KBFileEntry> = new Map();
    private projectDir: string | undefined;

    constructor(private readonly client: SdlicitClient) {}

    get knowledgeDir(): string | undefined {
        if (!this.projectDir) { return undefined; }
        return path.join(this.projectDir, '.sdlicit', 'knowledge');
    }

    setProjectDir(dir: string): void {
        this.projectDir = dir;
        this.ensureKnowledgeDir();
        this.loadManifest();
        this.scan();
        // Async: pull real ingestion status from the backend
        this.syncFromBackend().catch(() => { /* best-effort */ });
    }

    private ensureKnowledgeDir(): void {
        const dir = this.knowledgeDir;
        if (dir && !fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
    }

    private get manifestPath(): string | undefined {
        if (!this.projectDir) { return undefined; }
        return path.join(this.projectDir, '.sdlicit', 'kb_manifest.json');
    }

    private manifest: Map<string, ManifestEntry> = new Map();

    private loadManifest(): void {
        const mp = this.manifestPath;
        if (!mp || !fs.existsSync(mp)) { return; }
        try {
            const data = JSON.parse(fs.readFileSync(mp, 'utf-8'));
            this.manifest.clear();
            for (const entry of data.entries ?? []) {
                this.manifest.set(entry.fileName, entry);
            }
        } catch { /* ignore corrupt manifests */ }
    }

    private saveManifest(): void {
        const mp = this.manifestPath;
        if (!mp) { return; }
        const dir = path.dirname(mp);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        const entries = Array.from(this.manifest.values());
        fs.writeFileSync(mp, JSON.stringify({ entries }, null, 2), 'utf-8');
    }

    private hashContent(content: string): string {
        return crypto.createHash('sha256').update(content).digest('hex').slice(0, 16);
    }

    /** Scan the knowledge directory and determine statuses. */
    scan(): KBFileEntry[] {
        const dir = this.knowledgeDir;
        if (!dir || !fs.existsSync(dir)) { return []; }

        const entries: KBFileEntry[] = [];
        const files = fs.readdirSync(dir, { withFileTypes: true });

        for (const file of files) {
            if (file.isDirectory()) { continue; }
            const ext = path.extname(file.name).toLowerCase();
            const supported = ['.md', '.txt', '.feature', '.pdf', '.json', '.yaml', '.yml'];
            if (!supported.includes(ext)) { continue; }

            const filePath = path.join(dir, file.name);
            const relativePath = path.relative(this.projectDir!, filePath);

            // Determine status
            const existing = this.statusMap.get(file.name);
            if (existing && existing.status === 'uploading') {
                entries.push(existing);
                continue;
            }

            const manifestEntry = this.manifest.get(file.name);
            let status: KBFileStatus = 'pending';

            if (manifestEntry) {
                const content = fs.readFileSync(filePath);
                const currentHash = this.hashContent(content.toString('base64'));
                status = currentHash === manifestEntry.contentHash ? 'synced' : 'pending';
            }

            const entry: KBFileEntry = {
                fileName: file.name,
                filePath,
                relativePath: relativePath,
                status,
                lastSynced: manifestEntry?.syncedAt,
            };
            this.statusMap.set(file.name, entry);
            entries.push(entry);
        }

        return entries;
    }

    getFiles(): KBFileEntry[] {
        return this.scan();
    }

    getStatus(fileName: string): KBFileStatus {
        return this.statusMap.get(fileName)?.status ?? 'pending';
    }

    /** Add a file to the knowledge directory (copy it in). */
    async addFile(sourceUri: vscode.Uri): Promise<string> {
        const dir = this.knowledgeDir;
        if (!dir) { throw new Error('No project dir set'); }
        this.ensureKnowledgeDir();

        const fileName = path.basename(sourceUri.fsPath);
        const destPath = path.join(dir, fileName);
        fs.copyFileSync(sourceUri.fsPath, destPath);

        const entry: KBFileEntry = {
            fileName,
            filePath: destPath,
            relativePath: path.relative(this.projectDir!, destPath),
            status: 'pending',
        };
        this.statusMap.set(fileName, entry);
        this._onFilesChange.fire();
        this._onStatusChange.fire(entry);
        return destPath;
    }

    /** Save content directly to the knowledge directory. */
    saveToKnowledge(fileName: string, content: string): string {
        const dir = this.knowledgeDir;
        if (!dir) { throw new Error('No project dir set'); }
        this.ensureKnowledgeDir();

        const filePath = path.join(dir, fileName);
        fs.writeFileSync(filePath, content, 'utf-8');

        // Check if content matches what was already ingested (hash-based dedup)
        const contentHash = this.hashContent(Buffer.from(content, 'utf-8').toString('base64'));
        const manifestEntry = this.manifest.get(fileName);
        const alreadySynced = manifestEntry && manifestEntry.contentHash === contentHash;

        const entry: KBFileEntry = {
            fileName,
            filePath,
            relativePath: path.relative(this.projectDir!, filePath),
            status: alreadySynced ? 'synced' : 'pending',
            lastSynced: alreadySynced ? manifestEntry.syncedAt : undefined,
        };
        this.statusMap.set(fileName, entry);
        this._onFilesChange.fire();
        this._onStatusChange.fire(entry);
        return filePath;
    }

    /** Ingest a specific file into the KB asynchronously. */
    async ingestFile(fileName: string): Promise<void> {
        const dir = this.knowledgeDir;
        if (!dir || !this.projectDir) { return; }

        const filePath = path.join(dir, fileName);
        if (!fs.existsSync(filePath)) { return; }

        const entry = this.statusMap.get(fileName);
        if (entry) {
            entry.status = 'uploading';
            this._onStatusChange.fire(entry);
            this._onFilesChange.fire();
        }

        try {
            // Backend expects relative paths for selected_files filtering
            const relativePath = path.relative(this.projectDir, filePath);
            await vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: `Sdlicit: Ingesting "${fileName}"`,
                    cancellable: false,
                },
                async (progress) => {
                    await this.client.ingestKB(this.projectDir!, [relativePath], (event) => {
                        if (event.message) {
                            progress.report({ message: event.message });
                        } else if (event.type === 'progress' && event.current && event.total_chunks) {
                            progress.report({ message: `chunk ${event.current}/${event.total_chunks}` });
                        }
                    });
                },
            );

            // Mark as synced
            const content = fs.readFileSync(filePath);
            const hash = this.hashContent(content.toString('base64'));
            this.manifest.set(fileName, {
                fileName,
                syncedAt: new Date().toISOString(),
                contentHash: hash,
            });
            this.saveManifest();

            const updated: KBFileEntry = {
                fileName,
                filePath,
                relativePath: path.relative(this.projectDir, filePath),
                status: 'synced',
                lastSynced: new Date().toISOString(),
            };
            this.statusMap.set(fileName, updated);
            this._onStatusChange.fire(updated);
            this._onFilesChange.fire();
            vscode.window.showInformationMessage(`Sdlicit: "${fileName}" ingested into KB ✓`);
        } catch (err: any) {
            const errorEntry: KBFileEntry = {
                fileName,
                filePath,
                relativePath: path.relative(this.projectDir!, filePath),
                status: 'error',
                error: err.message,
            };
            this.statusMap.set(fileName, errorEntry);
            this._onStatusChange.fire(errorEntry);
            this._onFilesChange.fire();
            vscode.window.showErrorMessage(`Sdlicit: Ingestion failed for "${fileName}" — ${err.message}`);
        }
    }

    /** Ingest a file into KB without blocking the caller. */
    ingestFileAsync(fileName: string): void {
        this.ingestFile(fileName).catch(err => {
            console.warn(`Sdlicit: KB ingestion failed for ${fileName}:`, err.message);
        });
    }

    /** Delete a file from the knowledge folder and remove from KB. */
    async deleteFromKB(fileName: string, artifactType: string): Promise<void> {
        try {
            await this.client.deleteFromKB(artifactType, fileName);
        } catch (err: any) {
            console.warn(`Sdlicit: KB deletion failed for ${fileName}:`, err.message);
        }
    }

    /** Ingest all pending files asynchronously. */
    ingestAllPending(): void {
        const files = this.scan().filter(f => f.status === 'pending');
        for (const file of files) {
            this.ingestFileAsync(file.fileName);
        }
    }

    /** Sync local status map with the backend's actual ingestion state.
     *
     *  Calls scan-documents on the backend and marks any files the backend
     *  reports as "complete" as "synced" locally (including updating the
     *  manifest).  Also downgrades files from "synced" to "pending" if
     *  the backend reports them as "none" (e.g. LightRAG was reset).
     *  This ensures the Knowledge Browser tree reflects the real KB state.
     */
    async syncFromBackend(): Promise<void> {
        if (!this.projectDir) { return; }
        try {
            const scan = await this.client.scanDocuments(this.projectDir);
            let changed = false;
            for (const doc of scan.documents) {
                // Extract the file name from the relative path
                const fileName = path.basename(doc.relative_path);
                const knowledgeDir = this.knowledgeDir;
                if (!knowledgeDir) { continue; }

                const filePath = path.join(knowledgeDir, fileName);
                // Only update for files that actually exist in the knowledge dir
                if (!fs.existsSync(filePath)) { continue; }

                const currentEntry = this.statusMap.get(fileName);

                if (doc.ingestion_status === 'complete' && (!currentEntry || currentEntry.status !== 'synced')) {
                    // Backend says it's ingested — update local state
                    const content = fs.readFileSync(filePath);
                    const hash = this.hashContent(content.toString('base64'));
                    this.manifest.set(fileName, {
                        fileName,
                        syncedAt: new Date().toISOString(),
                        contentHash: hash,
                    });

                    const entry: KBFileEntry = {
                        fileName,
                        filePath,
                        relativePath: path.relative(this.projectDir!, filePath),
                        status: 'synced',
                        lastSynced: new Date().toISOString(),
                    };
                    this.statusMap.set(fileName, entry);
                    this._onStatusChange.fire(entry);
                    changed = true;
                } else if (doc.ingestion_status === 'none' && currentEntry?.status === 'synced') {
                    // Backend says it's NOT ingested but we think it's synced —
                    // LightRAG was likely reset. Downgrade to pending.
                    this.manifest.delete(fileName);
                    const entry: KBFileEntry = {
                        fileName,
                        filePath,
                        relativePath: path.relative(this.projectDir!, filePath),
                        status: 'pending',
                    };
                    this.statusMap.set(fileName, entry);
                    this._onStatusChange.fire(entry);
                    changed = true;
                }
            }
            if (changed) {
                this.saveManifest();
                this._onFilesChange.fire();
            }
        } catch {
            // Best-effort: backend may not be running yet
        }
    }

    dispose(): void {
        this._onStatusChange.dispose();
        this._onFilesChange.dispose();
    }
}
