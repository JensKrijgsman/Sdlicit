# sdlicit — VS Code extension

This small extension lists README files under the workspace `.sdlc` folder and opens them from the activity bar.

Quick usage

- Install dependencies and build:

```bash
cd vscode-extension
npm install
npm run compile
```

- Open in VS Code and press `F5` to run the extension in Extension Development Host.

Installing into WSL for testing

1. Ensure VS Code Remote - WSL is installed and you're connected to the WSL workspace.
2. In the extension development host window (launched with `F5`), the activity bar contains an `sdlicit` icon. Click it and expand `sdlicit Readmes`.
3. If your workspace has a `.sdlc` folder with README files, they will be listed. Click an item to open it; markdown files show a preview to the side.

Commands

- `sdlicit: Refresh sdlicit Readmes` — rescans the `.sdlc` folder.

Notes

- The extension looks for files matching `.sdlc/**/README*` under the first workspace folder.
- If you have multiple workspace folders, reopen the workspace with the desired root first.

That's it — simple, focused behavior for browsing SDLC readmes.
