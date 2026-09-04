// Minimal stand in for the 'vscode' module in unit tests.
//
// The real 'vscode' module only exists inside a running extension host, it
// is not a real npm package. Files under test import it at module scope
// (`import * as vscode from 'vscode'`) even when the specific functions
// exercised in a given test never call into it, so the import still has
// to resolve. This mock covers exactly the runtime surface that actually
// executes during construction of the classes tested here: EventEmitter.
// It is aliased in via vitest.config.ts, it does not touch the real
// extension code.

export class EventEmitter<T> {
    private listeners: Array<(e: T) => void> = [];

    event = (listener: (e: T) => void) => {
        this.listeners.push(listener);
        return { dispose: () => {
            this.listeners = this.listeners.filter((l) => l !== listener);
        } };
    };

    fire(data: T): void {
        for (const listener of this.listeners) {
            listener(data);
        }
    }

    dispose(): void {
        this.listeners = [];
    }
}
