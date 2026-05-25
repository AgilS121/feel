import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import * as child_process from 'child_process';

export function activate(context: vscode.ExtensionContext) {
    // Register format document command
    const formatCommand = vscode.commands.registerCommand('feel.formatDocument', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.document.languageId !== 'feel') {
            vscode.window.showWarningMessage('No Feel file is currently open.');
            return;
        }
        await formatDocument(editor.document);
    });

    // Register run file command
    const runCommand = vscode.commands.registerCommand('feel.runFile', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.document.languageId !== 'feel') {
            vscode.window.showWarningMessage('No Feel file is currently open.');
            return;
        }
        await editor.document.save();
        runFeelFile(editor.document.uri.fsPath, context);
    });

    // Register document formatting provider
    const formattingProvider = vscode.languages.registerDocumentFormattingEditProvider('feel', {
        async provideDocumentFormattingEdits(document: vscode.TextDocument): Promise<vscode.TextEdit[]> {
            const formatted = await formatDocumentContent(document.getText(), document.uri.fsPath);
            if (formatted === null) return [];
            const fullRange = new vscode.Range(
                document.positionAt(0),
                document.positionAt(document.getText().length)
            );
            return [vscode.TextEdit.replace(fullRange, formatted)];
        }
    });

    // Auto-format on save if enabled
    const onSave = vscode.workspace.onWillSaveTextDocument(async (event) => {
        const config = vscode.workspace.getConfiguration('feel');
        if (!config.get('formatting.enable', true)) return;
        if (event.document.languageId !== 'feel') return;

        const formatted = await formatDocumentContent(event.document.getText(), event.document.uri.fsPath);
        if (formatted === null) return;

        const fullRange = new vscode.Range(
            event.document.positionAt(0),
            event.document.positionAt(event.document.getText().length)
        );
        event.waitUntil(Promise.resolve([vscode.TextEdit.replace(fullRange, formatted)]));
    });

    context.subscriptions.push(formatCommand, runCommand, formattingProvider, onSave);
}

async function formatDocument(document: vscode.TextDocument): Promise<void> {
    const formatted = await formatDocumentContent(document.getText(), document.uri.fsPath);
    if (formatted === null) return;

    const editor = vscode.window.activeTextEditor;
    if (!editor) return;

    await editor.edit(editBuilder => {
        const fullRange = new vscode.Range(
            document.positionAt(0),
            document.positionAt(document.getText().length)
        );
        editBuilder.replace(fullRange, formatted);
    });
}

async function formatDocumentContent(content: string, filePath: string): Promise<string | null> {
    const interpreterPath = findInterpreter(filePath);
    if (!interpreterPath) return null;

    return new Promise((resolve) => {
        const proc = child_process.spawn('python', [interpreterPath, 'fmt', '--stdin'], {
            cwd: path.dirname(filePath)
        });

        let output = '';
        let error = '';

        proc.stdin.write(content);
        proc.stdin.end();

        proc.stdout.on('data', (data) => { output += data.toString(); });
        proc.stderr.on('data', (data) => { error += data.toString(); });

        proc.on('close', (code) => {
            if (code === 0 && output) {
                resolve(output);
            } else {
                resolve(null);
            }
        });

        proc.on('error', () => resolve(null));
    });
}

function runFeelFile(filePath: string, context: vscode.ExtensionContext): void {
    const interpreterPath = findInterpreter(filePath);
    const terminal = vscode.window.createTerminal({
        name: `Feel: ${path.basename(filePath)}`,
        cwd: path.dirname(filePath)
    });

    terminal.show();

    if (interpreterPath) {
        terminal.sendText(`python "${interpreterPath}" run "${filePath}"`);
    } else {
        // Try feel binary
        terminal.sendText(`feel run "${filePath}"`);
    }
}

function findInterpreter(filePath: string): string | null {
    const config = vscode.workspace.getConfiguration('feel');
    const configPath = config.get<string>('interpreter.path', '');
    if (configPath && fs.existsSync(configPath)) return configPath;

    // Walk up from file to find main.py
    let dir = path.dirname(filePath);
    for (let i = 0; i < 6; i++) {
        const candidate = path.join(dir, 'main.py');
        if (fs.existsSync(candidate)) return candidate;
        const parent = path.dirname(dir);
        if (parent === dir) break;
        dir = parent;
    }

    // Check workspace folders
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
        const candidate = path.join(folder.uri.fsPath, 'main.py');
        if (fs.existsSync(candidate)) return candidate;
    }

    return null;
}

export function deactivate() {}
