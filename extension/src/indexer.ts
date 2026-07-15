/** Runs the Python indexer and hands back a Code Graph.
 *
 * The indexer stays in Python on purpose. Jedi is Python-only, and Jedi is what
 * resolves `rec.record()` back to `StepRecorder.record` with no annotation
 * anywhere — 85.7% resolution on real code, every hard case, zero false links.
 * Reimplementing that in TypeScript would throw away the only part of this tool
 * that is actually hard. So: shell out, and be loud when Python is missing.
 */
import * as cp from 'child_process';
import * as path from 'path';
import * as vscode from 'vscode';
import { CodeGraph } from './graph';

export class IndexerError extends Error {}

/** The interpreter the user's Python extension already selected, if any. */
export async function pythonPath(): Promise<string> {
  const configured = vscode.workspace.getConfiguration('codevis').get<string>('pythonPath');
  if (configured) return configured;

  try {
    const ext = vscode.extensions.getExtension('ms-python.python');
    if (ext) {
      if (!ext.isActive) await ext.activate();
      const api = ext.exports;
      const env = api?.environments?.getActiveEnvironmentPath?.();
      if (env?.path) return env.path;
    }
  } catch { /* fall through — the Python extension is optional */ }

  return process.platform === 'win32' ? 'python' : 'python3';
}

function run(exe: string, args: string[], cwd: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const p = cp.execFile(exe, args, { cwd, maxBuffer: 256 * 1024 * 1024 },
      (err, stdout, stderr) => {
        // Stock Windows aliases `python` to a Microsoft Store redirect that
        // prints a message and exits WITHOUT running anything — sometimes
        // with code 9009, sometimes "successfully". Detect the signature and
        // route it into the existing no-interpreter guidance ("could not
        // run" is what indexWorkspace keys on).
        const noise = `${stdout ?? ''}\n${stderr ?? ''}`;
        if (/Python was not found/i.test(noise) &&
            /Microsoft Store|app execution aliases/i.test(noise)) {
          return reject(new IndexerError(
            `could not run '${exe}': it is the Windows Store alias, not an installed interpreter.`));
        }
        if (err) reject(new IndexerError(stderr?.trim() || err.message));
        else resolve(stdout);
      });
    p.on('error', e => reject(new IndexerError(
      `could not run '${exe}': ${e.message}`)));
  });
}

export async function indexWorkspace(
  toolRoot: string, folder: string, diffRef?: string
): Promise<CodeGraph> {
  const exe = await pythonPath();
  const args = [path.join(toolRoot, 'codevis.py'), folder, '--emit-json'];
  if (diffRef) args.push('--diff', diffRef);

  let out: string;
  try {
    out = await run(exe, args, toolRoot);
  } catch (e: any) {
    const msg = String(e.message || e);
    // The indexer already prints the precise pip line it needs — including the
    // fact that R (tree-sitter) is optional and Python/SQL do not require it.
    // Don't paraphrase it into something less accurate.
    if (/pip install/.test(msg)) throw new IndexerError(msg);

    if (/No module named (jedi|sqlglot)/.test(msg)) {
      throw new IndexerError(
        `The indexer's Python dependencies are missing. Run:\n` +
        `    ${exe} -m pip install jedi sqlglot\n\n${msg}`);
    }
    if (/ENOENT|could not run/.test(msg)) {
      throw new IndexerError(
        `No Python interpreter found. Set \`codevis.pythonPath\`, or install the ` +
        `Python extension and select an interpreter.\n\n${msg}`);
    }
    throw e;
  }

  try {
    return JSON.parse(out) as CodeGraph;
  } catch {
    throw new IndexerError(`indexer did not return JSON:\n${out.slice(0, 400)}`);
  }
}
