import * as path from 'path';
import * as vscode from 'vscode';
import { CodeGraph } from './graph';
import { indexWorkspace, IndexerError, pythonPath } from './indexer';
// `describe` here is the MODEL formatter, not the feature — aliased to keep the
// feature's new name unambiguous in this file.
import { SMALL_CONTEXT, VSCodeLM, describe as describeModel, toSetting } from './llm';
import { Contextualizer } from './contextualize';
import { CodevisPanel } from './panel';
import { runEval } from './evalrun';

let cached: CodeGraph | undefined;
let cachedFolder: string | undefined;

function toolRoot(ctx: vscode.ExtensionContext): string {
  // The Python indexer ships beside the extension. Keeping it as a plain CLI means
  // `python codevis.py <folder>` still works standalone — the extension is a host,
  // not a rewrite.
  return path.join(ctx.extensionPath, 'indexer');
}

function workspaceFolder(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders?.length) return undefined;
  // Multi-root: prefer the folder that owns the active editor rather than
  // silently always using folder 0.
  const doc = vscode.window.activeTextEditor?.document.uri;
  if (doc) {
    const own = vscode.workspace.getWorkspaceFolder(doc);
    if (own) return own.uri.fsPath;
  }
  return folders[0].uri.fsPath;
}

async function getGraph(ctx: vscode.ExtensionContext, force = false, diffRef?: string) {
  const folder = workspaceFolder();
  if (!folder) throw new Error('Open a folder first — codevis indexes a workspace.');
  if (cached && cachedFolder === folder && !force && !diffRef) return cached;

  return vscode.window.withProgress(
    { location: vscode.ProgressLocation.Window, title: 'codevis: indexing…' },
    async () => {
      const g = await indexWorkspace(toolRoot(ctx), folder, diffRef);
      // A diff-annotated graph is a one-shot answer to "what does this ref
      // threaten?" — caching it would leave changed/blast-radius state on the
      // next plain Open, and the stale-write guard would start refusing edits
      // with a message that reads like a bug.
      if (!diffRef) { cached = g; cachedFolder = folder; }
      return g;
    });
}

export function activate(ctx: vscode.ExtensionContext) {
  // After a card writes to a file, the index is stale by definition. Re-index so
  // the views never describe code that is no longer there.
  const llm = new VSCodeLM();
  const ctxr = new Contextualizer(llm, ctx.workspaceState);

  // A status bar entry, because an invisible model choice is the bug we just
  // fixed. If Contextualize is going to spend your tokens, you should be able to
  // see whose tokens without opening a menu.
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  status.command = 'codevis.selectModel';
  status.tooltip = 'codevis: language model used by Describe — click to change';
  ctx.subscriptions.push(status);

  const refreshStatus = async () => {
    const models = await llm.list();
    if (!models.length) {
      status.text = '$(sparkle) codevis: no model';
      status.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
      const active = await llm.resolveActive();
      status.text = `$(sparkle) ${active ? active.id : 'no model'}`;
      status.backgroundColor = undefined;
    }
    status.show();
  };
  void refreshStatus();
  // The setting is editable in the Settings UI and settings.json, so react to it
  // being changed anywhere — not just through our picker.
  ctx.subscriptions.push(vscode.workspace.onDidChangeConfiguration(ev => {
    if (ev.affectsConfiguration('codevis.model')) void refreshStatus();
  }));

  // An ordinary save makes the cached graph stale — card-driven edits already
  // re-index, but nothing invalidated the cache for edits made in the editor.
  // Clearing is cheap; the re-index happens lazily on the next open.
  ctx.subscriptions.push(vscode.workspace.onDidSaveTextDocument(doc => {
    if (/\.(py|r|sql)$/i.test(doc.fileName)) {
      cached = undefined;
      cachedFolder = undefined;
    }
  }));

  const guard = (fn: () => Promise<void>) => async () => {
    try { await fn(); }
    catch (e: any) {
      const msg = String(e?.message ?? e);
      if (e instanceof IndexerError) {
        vscode.window.showErrorMessage('codevis: ' + msg.split('\n')[0], 'Show details')
          .then(pick => { if (pick) vscode.window.showErrorMessage(msg, { modal: true }); });
      } else {
        vscode.window.showErrorMessage('codevis: ' + msg);
      }
    }
  };

  CodevisPanel.onEdited = async () => {
    const folder = workspaceFolder();
    if (!folder) return;
    const g = await getGraph(ctx, true);
    CodevisPanel.show(ctx, g, ctxr, folder);
  };

  ctx.subscriptions.push(
    vscode.commands.registerCommand('codevis.diagnoseModels', guard(async () => {
      const doc = await vscode.workspace.openTextDocument({
        content: await llm.diagnose(), language: 'markdown' });
      await vscode.window.showTextDocument(doc, { preview: false });
    })),

    vscode.commands.registerCommand('codevis.selectModel', guard(async () => {
      const models = await llm.list();
      if (!models.length) {
        // Do not just say "no models" — that is what happened and it told the user
        // nothing. Offer the diagnostic that reports what VS Code actually returns.
        const go = await vscode.window.showWarningMessage(
          'codevis: VS Code reports no language models available to extensions. ' +
          'Having Claude in a chat sidebar is not the same thing — a model must be ' +
          'registered with VS Code as a provider for other extensions to use it.',
          'Show diagnostics', 'Manage Language Models');
        if (go === 'Show diagnostics') await vscode.commands.executeCommand('codevis.diagnoseModels');
        if (go === 'Manage Language Models') await vscode.commands.executeCommand('workbench.action.chat.manageLanguageModels');
        return;
      }

      const current = llm.getSetting();
      // Biggest context first. The list order VS Code hands us is arbitrary and
      // put a 12k model at the top, which cannot hold this extension's own
      // context bundles — a picker that leads with an unusable model is a trap.
      const usable = models.filter(m => m.maxInputTokens > 0)
        .sort((a, b) => b.maxInputTokens - a.maxInputTokens);
      const items: (vscode.QuickPickItem & { m?: vscode.LanguageModelChat })[] =
        usable.map(m => ({
          // The name alone is not identifying — two providers both label a model
          // "Auto". The setting string is what distinguishes them, so lead with it.
          label: (toSetting(m) === current ? '$(check) ' : '') + toSetting(m),
          description: m.name,
          detail: `${m.maxInputTokens.toLocaleString()} input tokens` +
                  (m.maxInputTokens < SMALL_CONTEXT
                    ? ' — small; may not fit Describe or the eval'
                    : ''),
          m
        }));

      // Zero-token entries are advertised by VS Code but cannot carry a prompt.
      const unusable = models.length - usable.length;
      if (unusable) {
        items.push({ label: '', kind: vscode.QuickPickItemKind.Separator },
                   { label: `$(info) ${unusable} model(s) hidden`,
                     description: 'reported 0 input tokens — unusable' });
      }

      items.push(
        { label: '', kind: vscode.QuickPickItemKind.Separator },
        { label: '$(settings-gear) Open codevis settings',
          description: 'edit codevis.model by hand' },
        { label: '$(add) Manage Language Models…',
          description: 'add a provider or your own API key' });

      const pick = await vscode.window.showQuickPick(items, {
        title: 'codevis: model for Describe',
        placeHolder: current
          ? `currently: ${current}`
          : 'not set — using the largest-context model available'
      });
      if (!pick) return;
      if (pick.label.includes('Open codevis settings')) {
        await vscode.commands.executeCommand('workbench.action.openSettings', 'codevis.model');
        return;
      }
      if (pick.label.includes('Manage Language Models')) {
        await vscode.commands.executeCommand('workbench.action.chat.manageLanguageModels');
        return;
      }
      if (!pick.m) return;

      // Write the SETTING. The picker is a helper for a value the user owns and
      // can see in the Settings UI — not a hidden preference of its own.
      await llm.setSetting(toSetting(pick.m));
      await refreshStatus();
      vscode.window.showInformationMessage(
        `codevis: Describe will use ${describeModel(pick.m)} (saved to codevis.model).`);
    })),

    vscode.commands.registerCommand('codevis.open', guard(async () => {
      const g = await getGraph(ctx);
      CodevisPanel.show(ctx, g, ctxr, workspaceFolder()!);
    })),

    vscode.commands.registerCommand('codevis.flowForFile', guard(async () => {
      const ed = vscode.window.activeTextEditor;
      if (!ed) { vscode.window.showWarningMessage('codevis: no file open'); return; }
      const folder = workspaceFolder()!;
      const rel = path.relative(folder, ed.document.uri.fsPath).split(path.sep).join('/');
      const g = await getGraph(ctx);
      CodevisPanel.show(ctx, g, ctxr, folder, rel);
    })),

    vscode.commands.registerCommand('codevis.reindex', guard(async () => {
      const g = await getGraph(ctx, true);
      CodevisPanel.show(ctx, g, ctxr, workspaceFolder()!);
      vscode.window.showInformationMessage(
        `codevis: ${g.symbols.length} symbols, ${g.edges.length} edges.`);
    })),

    vscode.commands.registerCommand('codevis.diff', guard(async () => {
      const folder = workspaceFolder();
      if (!folder) throw new Error('Open a folder first — codevis indexes a workspace.');
      const ref = await vscode.window.showInputBox({
        prompt: 'Show the blast radius of changes against which git ref?',
        value: 'HEAD'
      });
      if (!ref) return;
      // Validate before indexing: a typo'd ref otherwise surfaces minutes
      // later as a raw Python error instead of "not a git ref".
      const cp = await import('child_process');
      const valid = await new Promise<boolean>(res =>
        cp.execFile('git', ['-C', folder, 'rev-parse', '--verify', '--quiet',
                            `${ref}^{commit}`],
          err => res(!err)));
      if (!valid) {
        vscode.window.showErrorMessage(
          `codevis: "${ref}" is not a git ref in this repository.`);
        return;
      }
      const g = await getGraph(ctx, true, ref);
      CodevisPanel.show(ctx, g, ctxr, folder);
    })),

    vscode.commands.registerCommand('codevis.runEval', guard(async () => {
      const folder = workspaceFolder();
      if (!folder) throw new Error('Open a folder first — the eval runs against a workspace.');

      // Build the bundle here rather than making the user remember two python
      // invocations and which directory to run them from.
      const scriptsDir = path.join(ctx.extensionPath, 'eval');
      // Outputs go to global storage, never the install directory: that is
      // wiped on every update, may be read-only in managed installs, and
      // writing into it can trip the editor's extension-integrity check.
      const outDir = ctx.globalStorageUri.fsPath;
      const exe = await pythonPath();
      const cp = await import('child_process');
      const run = (script: string) => new Promise<void>((res, rej) =>
        cp.execFile(exe, [path.join(scriptsDir, script), folder, outDir],
          { maxBuffer: 64 * 1024 * 1024 },
          (err, _o, se) => err ? rej(new Error(`${script}: ${se || err.message}`)) : res()));

      const out = vscode.window.createOutputChannel('codevis eval');
      out.show(true);
      out.appendLine(`workspace: ${folder}`);
      out.appendLine(`outputs: ${outDir}`);
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Window, title: 'codevis: building eval bundle…' },
        async () => { await run('facts.py'); await run('build_arms.py'); });
      out.appendLine('answer key + both arms built. asking the model…');

      const report = await runEval(llm, outDir, out);
      const doc = await vscode.workspace.openTextDocument({
        content: report, language: 'markdown' });
      await vscode.window.showTextDocument(doc, { preview: false });
    })),

    vscode.commands.registerCommand('codevis.exportHtml', guard(async () => {
      // The standalone artifact survives: one file, no server, emailable.
      const dest = await vscode.window.showSaveDialog({
        filters: { HTML: ['html'] },
        defaultUri: vscode.Uri.file(path.join(workspaceFolder()!, 'codevis.html'))
      });
      if (!dest) return;
      const cp = await import('child_process');
      const folder = workspaceFolder()!;
      const exe = await pythonPath();   // NOT hardcoded 'python3' — Windows rarely has one
      cp.execFile(exe, [path.join(toolRoot(ctx), 'codevis.py'), folder, '-o', dest.fsPath],
        err => {
          if (err) vscode.window.showErrorMessage('codevis: export failed — ' + err.message);
          else vscode.window.showInformationMessage('codevis: exported to ' + dest.fsPath);
        });
    }))
  );
}

export function deactivate() { cached = undefined; cachedFolder = undefined; }
