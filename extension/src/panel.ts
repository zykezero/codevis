/** The webview host, and the bridge between the graph and the real editor.
 *
 * The biggest win of moving into VS Code: `CodePanel` from the feature spec does
 * not need building. "Hover a node -> scroll a synced code panel to the span and
 * flash-highlight it" is showTextDocument + revealRange + a decoration. The user
 * already has the code open; we were about to ship a worse copy of it.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { CodeGraph, GraphIndex, Span, Symbol } from './graph';
import { Contextualizer } from './contextualize';
import { applyEdit, StaleError } from './edits';

// Two decorations, because hover and click mean different things.
//   HOVER  — a calm, persistent band that follows the cursor around the chart.
//            Replaced on every hover, cleared on mouseleave.
//   FLASH  — a brief, loud pulse on click, so a deliberate jump is unmistakable.
const HOVER = vscode.window.createTextEditorDecorationType({
  backgroundColor: new vscode.ThemeColor('editor.rangeHighlightBackground'),
  isWholeLine: true,
  overviewRulerColor: new vscode.ThemeColor('editorOverviewRuler.rangeHighlightForeground'),
  overviewRulerLane: vscode.OverviewRulerLane.Full
});
const FLASH = vscode.window.createTextEditorDecorationType({
  backgroundColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
  isWholeLine: true,
  border: '1px solid',
  borderColor: new vscode.ThemeColor('editor.findMatchBorder')
});

export class CodevisPanel {
  static current: CodevisPanel | undefined;

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly ctx: vscode.ExtensionContext,
    private gi: GraphIndex,
    private readonly ctxr: Contextualizer,
    private readonly workspaceRoot: string
  ) {
    panel.webview.html = this.html();
    panel.onDidDispose(() => { CodevisPanel.current = undefined; }, null, ctx.subscriptions);
    panel.webview.onDidReceiveMessage(m => this.onMessage(m), null, ctx.subscriptions);
  }

  /** Set by the host so a save can trigger a re-index without a circular import. */
  static onEdited?: () => Promise<void>;

  static show(ctx: vscode.ExtensionContext, g: CodeGraph, ctxr: Contextualizer,
              root: string, focusFile?: string) {
    const gi = new GraphIndex(g);
    if (CodevisPanel.current) {
      CodevisPanel.current.gi = gi;
      CodevisPanel.current.panel.webview.html = CodevisPanel.current.html();
      CodevisPanel.current.panel.reveal();
    } else {
      const panel = vscode.window.createWebviewPanel(
        'codevis', 'codevis', vscode.ViewColumn.Beside,
        { enableScripts: true, retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.file(path.join(ctx.extensionPath, 'media'))] });
      CodevisPanel.current = new CodevisPanel(panel, ctx, gi, ctxr, root);
    }
    if (focusFile) {
      CodevisPanel.current!.post({ type: 'focusFile', file: focusFile });
    }
  }

  private post(m: any) { this.panel.webview.postMessage(m); }

  private html(): string {
    const media = path.join(this.ctx.extensionPath, 'media');
    const tpl = fs.readFileSync(path.join(media, 'template.html'), 'utf8');
    const app = fs.readFileSync(path.join(media, 'app.js'), 'utf8');
    const host = fs.readFileSync(path.join(media, 'host.js'), 'utf8');

    // This webview holds the full text of every indexed file AND can post
    // applyEdit messages that write to the workspace — so indexed source must
    // never become markup. Three defenses, each of which has caught a real bug:
    //
    // 1. Escape `<` in the JSON as < (same bytes after JS string parsing).
    //    The HTML parser ends a <script> element at the literal "</script>"
    //    regardless of string context; an indexed file containing that text
    //    would otherwise break out and execute with acquireVsCodeApi in hand.
    //
    // 2. ONE regex pass with a replacer function, not chained .replace calls.
    //    A replacer function avoids `$&`/`$'` expansion (a regex like r'...$'
    //    in indexed source once spliced the template into its own data), and
    //    the single pass means substituted content is never rescanned — the
    //    payload legitimately contains "__APP__" whenever the indexed project
    //    does, and a chained replace would hit that occurrence first.
    //
    // 3. A CSP that only executes the two nonce-carrying script blocks, as the
    //    backstop for any escaping bug the first two rules miss.
    const nonce = crypto.randomBytes(16).toString('base64url');
    const parts: Record<string, string> = {
      __NONCE__: nonce,
      __ROOT__: this.gi.g.root.replace(/&/g, '&amp;').replace(/</g, '&lt;'),
      __INDEX__: JSON.stringify(this.gi.g).replace(/</g, '\\u003c'),
      __APP__: app + '\n' + host,
    };
    return tpl.replace(/__(?:NONCE|ROOT|INDEX|APP)__/g, m => parts[m]);
  }

  private lastHovered?: vscode.TextEditor;

  /** Reveal a span in the real editor and mark it. This IS the CodePanel. */
  private async reveal(span: Span, mode: 'hover' | 'click') {
    const uri = vscode.Uri.file(path.join(this.workspaceRoot, span.file));
    let doc: vscode.TextDocument;
    try { doc = await vscode.workspace.openTextDocument(uri); }
    catch { return; }

    const ed = await vscode.window.showTextDocument(doc, {
      viewColumn: vscode.ViewColumn.One,
      preserveFocus: true,     // hovering must not steal focus from the chart
      preview: true            // reuse one tab instead of littering the tab bar
    });

    const range = new vscode.Range(
      Math.max(0, span.start_line - 1), 0,
      Math.max(0, (span.end_line || span.start_line) - 1), Number.MAX_SAFE_INTEGER);
    ed.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);

    // clear the previous hover band, even if it was in a different editor
    if (this.lastHovered && this.lastHovered !== ed) {
      this.lastHovered.setDecorations(HOVER, []);
    }
    ed.setDecorations(HOVER, [range]);
    this.lastHovered = ed;

    if (mode === 'click') {
      ed.setDecorations(FLASH, [range]);
      setTimeout(() => ed.setDecorations(FLASH, []), 900);
    }
  }

  private clearHover() {
    this.lastHovered?.setDecorations(HOVER, []);
    this.lastHovered = undefined;
  }

  private async onMessage(m: any) {
    switch (m.type) {
      case 'hover': {
        const s = this.gi.byId.get(m.id);
        if (s) await this.reveal(s.body ?? s.span, 'hover');
        return;
      }
      case 'hoverEnd':
        this.clearHover();
        return;
      case 'reveal': {
        const s = this.gi.byId.get(m.id);
        if (s) await this.reveal(s.body ?? s.span, 'click');
        return;
      }
      case 'revealSpan':
        await this.reveal(m.span, 'click');
        return;

      case 'applyEdit': {
        const sym = this.gi.byId.get(m.id);
        if (!sym) return;
        try {
          await applyEdit(this.workspaceRoot, sym, m.text);
          this.post({ type: 'editApplied', id: m.id });
          // The index now describes code that no longer exists. Re-index rather
          // than leave the view quietly lying about the file.
          if (CodevisPanel.onEdited) await CodevisPanel.onEdited();
        } catch (e: any) {
          const stale = e instanceof StaleError;
          this.post({ type: 'editFailed', id: m.id, error: String(e?.message ?? e), stale });
          if (stale) {
            const pick = await vscode.window.showWarningMessage(
              String(e.message), 'Re-index now');
            if (pick) await vscode.commands.executeCommand('codevis.reindex');
          }
        }
        return;
      }

      case 'contextualize': {
        const target = this.gi.byId.get(m.id);
        const caller = m.callerId ? this.gi.byId.get(m.callerId) ?? null : null;
        if (!target) return;

        const cached = m.force ? undefined : await this.ctxr.get(target, caller);
        if (cached) { this.post({ type: 'contextualized', id: m.id, data: cached, cached: true }); return; }

        this.post({ type: 'contextualizing', id: m.id });
        try {
          const data = await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification,
              title: `codevis: describing ${target.name}…`, cancellable: true },
            (_p, tok) => this.ctxr.run(this.gi, target, caller, tok, !!m.force));
          this.post({ type: 'contextualized', id: m.id, data, cached: false });
        } catch (e: any) {
          this.post({ type: 'contextualizeError', id: m.id, error: String(e?.message ?? e) });
        }
        return;
      }
    }
  }
}
