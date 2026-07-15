/** Writing a card's edits back to the real file.
 *
 * The card renders a SNAPSHOT of the source taken when the project was indexed.
 * The file on disk is the truth and it can move underneath us — the user edits in
 * the editor, a formatter runs, an agent rewrites it. If we blindly replace
 * "lines 40-58" using a stale span, we silently destroy whatever is there now.
 *
 * So every write is guarded: recompute the hash of what is CURRENTLY at that span
 * and compare it to the hash recorded at index time. Mismatch => refuse. This is
 * the same content_hash from D17, doing the job it was split out for — identity
 * says WHICH function, the hash says WHICH VERSION, and here we need the version.
 */
import * as crypto from 'crypto';
import * as path from 'path';
import * as vscode from 'vscode';
import { Symbol } from './graph';

/** Must match spike/frontend_python.py exactly, or every write is refused. */
export function contentHash(text: string): string {
  const norm = text.replace(/\s+/g, ' ').trim();
  return crypto.createHash('sha1').update(norm, 'utf8').digest('hex').slice(0, 12);
}

export class StaleError extends Error {}

export async function applyEdit(
  workspaceRoot: string, sym: Symbol, newText: string
): Promise<void> {
  const body = sym.body ?? sym.span;
  const uri = vscode.Uri.file(path.join(workspaceRoot, body.file));
  const doc = await vscode.workspace.openTextDocument(uri);

  const lastLine = Math.min(body.end_line, doc.lineCount) - 1;
  const range = new vscode.Range(
    new vscode.Position(body.start_line - 1, 0),
    doc.lineAt(lastLine).range.end
  );

  const current = doc.getText(range);
  if (sym.content_hash && contentHash(current) !== sym.content_hash) {
    throw new StaleError(
      `\`${body.file}\` has changed since it was indexed, so codevis no longer knows ` +
      `which lines \`${sym.name}\` occupies. The edit was NOT applied — writing to a ` +
      `stale range would overwrite whatever is there now. Re-index and try again.`);
  }

  // A WorkspaceEdit, not a raw file write: it participates in the editor's undo
  // stack, respects dirty buffers, and shows in the diff view like any other edit.
  const we = new vscode.WorkspaceEdit();
  we.replace(uri, range, newText.replace(/\s+$/, ''));
  const ok = await vscode.workspace.applyEdit(we);
  if (!ok) throw new Error(`VS Code refused the edit to ${body.file}.`);
}
