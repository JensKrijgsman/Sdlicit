// ---------------------------------------------------------------------------
// Sdlicit — Server Lifecycle Manager
// ---------------------------------------------------------------------------
// Manages auto-start of the backend server, health polling, and reconnection.
// ---------------------------------------------------------------------------

import * as vscode from 'vscode';
import { SdlicitClient } from './sdlicitClient';
import * as path from 'path';

export type ServerState = 'disconnected' | 'starting' | 'connected' | 'error';

export class ServerLifecycle {
    private _state: ServerState = 'disconnected';
    private _onStateChange = new vscode.EventEmitter<ServerState>();
    readonly onStateChange = this._onStateChange.event;
    private terminal: vscode.Terminal | undefined;
    private healthInterval: ReturnType<typeof setInterval> | undefined;
    private projectDir: string | undefined;
    private reconnectCancellation: vscode.CancellationTokenSource | undefined;
    private isReconnecting: boolean = false;

    constructor(private readonly client: SdlicitClient) {}

    get state(): ServerState { return this._state; }

    private setState(state: ServerState): void {
        this._state = state;
        this._onStateChange.fire(state);
        // Automatically trigger reconnect when entering a failed state
        if ((state === 'error' || state === 'disconnected') && this.projectDir && !this.isReconnecting) {
            this.startAutoReconnect();
        }
    }

    /**
     * Attempt to connect to an existing server, or auto-start one.
     */
    async connect(workspaceRoot: string): Promise<boolean> {
        this.projectDir = workspaceRoot;

        // Try connecting to existing server first
        if (await this.client.health()) {
            await this.initialize(workspaceRoot);
            return true;
        }

        // Check if auto-start is enabled
        const config = vscode.workspace.getConfiguration('sdlicit');
        const autoStart = config.get<boolean>('autoStartServer', true);

        if (autoStart) {
            return await this.startServer(workspaceRoot);
        }

        this.setState('disconnected');
        return false;
    }

    /**
     * Start the backend server in a VS Code terminal.
     */
    async startServer(workspaceRoot: string): Promise<boolean> {
        this.setState('starting');
        this.projectDir = workspaceRoot;

        // Create terminal for the server
        this.terminal = vscode.window.createTerminal({
            name: 'Sdlicit Server',
            cwd: workspaceRoot,
            hideFromUser: false,
        });
        this.terminal.sendText('uv run uvicorn sdlicit.main:app');

        // Poll for health
        const started = await this.waitForHealth(30_000);
        if (started) {
            await this.initialize(workspaceRoot);
            return true;
        }

        this.setState('error');
        this.startHealthPolling();
        vscode.window.showErrorMessage(
            'Sdlicit: Backend server failed to start within 30s. Check the "Sdlicit Server" terminal.'
        );
        return false;
    }

    /**
     * Initialize the backend with the project directory.
     */
    private async initialize(workspaceRoot: string): Promise<void> {
        try {
            await this.client.init(workspaceRoot);
            this.cancelReconnect();
            this.setState('connected');
        } catch (err) {
            this.setState('error');
            const message = err instanceof Error ? err.message : String(err);
            vscode.window.showErrorMessage(`Sdlicit: Init failed — ${message}`);
        }
        // Always keep health polling active regardless of outcome
        this.startHealthPolling();
    }

    /**
     * Poll health endpoint until server responds (or timeout).
     */
    private async waitForHealth(timeoutMs: number): Promise<boolean> {
        const start = Date.now();
        while (Date.now() - start < timeoutMs) {
            if (await this.client.health()) {
                return true;
            }
            await new Promise(r => setTimeout(r, 1000));
        }
        return false;
    }

    /**
     * Periodic health check — detect disconnections.
     */
    private startHealthPolling(): void {
        this.stopHealthPolling();
        this.healthInterval = setInterval(async () => {
            const ok = await this.client.health();
            if (!ok && this._state === 'connected') {
                this.setState('disconnected');
            } else if (ok && (this._state === 'disconnected' || this._state === 'error')) {
                if (this.projectDir) {
                    await this.initialize(this.projectDir);
                }
            }
        }, 15_000);
    }

    /**
     * Auto-reconnect loop with a cancellable notification.
     * Retries every 10 seconds until the backend responds AND init succeeds,
     * or the user cancels.
     */
    private startAutoReconnect(): void {
        if (this.isReconnecting) { return; }
        this.isReconnecting = true;
        this.reconnectCancellation = new vscode.CancellationTokenSource();

        vscode.window.withProgress(
            {
                location: vscode.ProgressLocation.Notification,
                title: 'Sdlicit: Lost connection to backend. Reconnecting…',
                cancellable: true,
            },
            async (progress, token) => {
                // Also honour our own cancellation source
                const merged = this.reconnectCancellation!;
                token.onCancellationRequested(() => merged.cancel());

                let attempt = 0;
                while (!merged.token.isCancellationRequested) {
                    attempt++;
                    progress.report({ message: `Attempt ${attempt}…` });

                    const ok = await this.client.health();
                    if (ok && this.projectDir) {
                        try {
                            await this.client.init(this.projectDir);
                            this.isReconnecting = false;
                            this.setState('connected');
                            this.startHealthPolling();
                            vscode.window.showInformationMessage('Sdlicit: Reconnected to backend.');
                            return;
                        } catch {
                            // Backend responded to health but init failed (e.g. busy)
                            // — keep retrying
                            progress.report({ message: `Attempt ${attempt} — backend busy, retrying…` });
                        }
                    }

                    // Wait 10 seconds, but abort early if cancelled
                    await new Promise<void>(resolve => {
                        const timer = setTimeout(resolve, 10_000);
                        merged.token.onCancellationRequested(() => {
                            clearTimeout(timer);
                            resolve();
                        });
                    });
                }

                // User cancelled
                this.isReconnecting = false;
                vscode.window.showWarningMessage('Sdlicit: Auto-reconnect stopped.');
            },
        );
    }

    /** Cancel any ongoing auto-reconnect loop. */
    cancelReconnect(): void {
        if (this.reconnectCancellation) {
            this.reconnectCancellation.cancel();
            this.reconnectCancellation.dispose();
            this.reconnectCancellation = undefined;
        }
    }

    private stopHealthPolling(): void {
        if (this.healthInterval) {
            clearInterval(this.healthInterval);
            this.healthInterval = undefined;
        }
    }

    /**
     * Reveal the server terminal to show logs.
     */
    revealTerminal(): void {
        if (this.terminal) {
            this.terminal.show();
        } else {
            vscode.window.showInformationMessage('Sdlicit: No server terminal active. Start the server first.');
        }
    }

    /**
     * Stop the server and cleanup.
     */
    async dispose(): Promise<void> {
        this.stopHealthPolling();
        this.cancelReconnect();
        if (this.terminal) {
            this.terminal.dispose();
            this.terminal = undefined;
        }
        this.setState('disconnected');
    }
}
