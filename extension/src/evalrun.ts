/** The A/B test: does the resolved graph beat handing the model the raw files?
 *
 * Both arms get the SAME questions and the SAME model. Only the context differs:
 * raw source vs the index rendered as structural facts. The index arm carries
 * ~5x less text, so a win for it cannot be explained by "we fed it more".
 *
 * Scoring is set precision/recall/F1, because the answers are sets of strings.
 * That is arithmetic — I cannot rig it and neither can the model. The answer key
 * is recomputed from source by eval/facts.py and never reads the index.
 */
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { LLMClient, parseJsonLoose } from './llm';

type Question = { id: string; question: string; answer: string[]; why: string };
type Arms = {
  root: string;
  questions: Question[];
  arms: Record<string, { label: string; context: string }>;
};

const SYSTEM = `You answer questions about a codebase. Reply with ONLY a JSON object:
{"answer": ["item", "item"], "confidence": "high"|"medium"|"low"}

- "answer" is a flat list of strings, exactly in the format the question asks for.
- Be exhaustive. A missing item costs as much as a wrong one.
- If the answer is genuinely empty, return [].
- Do not explain. Do not use code fences.`;

/** Compare as sets. Normalise only what is cosmetic: case, slashes, backticks. */
function norm(s: string): string {
  return s.trim().toLowerCase()
    .replace(/[`'"]/g, '')
    .replace(/\\/g, '/')
    .replace(/^\.\//, '')
    .replace(/\(\)$/, '');
}

function score(got: string[], want: string[]) {
  const G = new Set(got.map(norm)), W = new Set(want.map(norm));
  const hit = [...G].filter(x => W.has(x));
  const precision = G.size ? hit.length / G.size : (W.size ? 0 : 1);
  const recall = W.size ? hit.length / W.size : 1;
  const f1 = precision + recall ? (2 * precision * recall) / (precision + recall) : 0;
  return {
    precision, recall, f1,
    missed: [...W].filter(x => !G.has(x)),
    spurious: [...G].filter(x => !W.has(x))
  };
}

export async function runEval(
  llm: LLMClient, evalDir: string, out: vscode.OutputChannel
): Promise<string> {
  const armsPath = path.join(evalDir, 'arms.json');
  if (!fs.existsSync(armsPath)) {
    throw new Error(`No eval bundle. Run:\n    python eval/facts.py demo_project\n` +
                    `    python eval/build_arms.py demo_project`);
  }
  const A: Arms = JSON.parse(fs.readFileSync(armsPath, 'utf8'));
  const armNames = Object.keys(A.arms);
  const results: Record<string, Record<string, any>> = {};
  let modelUsed = '';

  // Resolve ONCE — both arms must face the same model, or the comparison is
  // not a comparison. The resolved model is threaded into every chat() below.
  const resolved = await llm.resolveActive();

  // A model whose context window cannot hold the RAW arm silently rigs the
  // experiment in the index arm's favour: the baseline errors, scores 0, and
  // the index arm "wins" on context size rather than on structure. That is the
  // exact false-confidence failure this tool exists to argue against, so refuse
  // the run instead of reporting it. (Estimate chars/4 — rough, hence the 10%
  // margin; we only need to catch "does not remotely fit".)
  if (resolved?.maxInputTokens) {
    const tooBig = armNames
      .map(a => ({ a, est: Math.round(A.arms[a].context.length / 4) }))
      .filter(x => x.est > resolved.maxInputTokens! * 0.9);
    if (tooBig.length) {
      throw new Error(
        `The selected model (${resolved.id}) has a ${resolved.maxInputTokens.toLocaleString()}-token ` +
        `context window, which cannot hold ${tooBig.map(x => `the "${x.a}" arm (~${x.est.toLocaleString()} tokens)`).join(' or ')}. ` +
        `That arm would fail and the comparison would be meaningless — a win by truncation, not by structure.\n\n` +
        `Pick a larger model with "codevis: Select language model" and run this again.`);
    }
  }

  const total = armNames.length * A.questions.length;
  let done = 0;

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification,
      title: 'codevis: running A/B eval', cancellable: true },
    async (prog, token) => {
      for (const arm of armNames) results[arm] = {};
      // Question-major, arms interleaved: if provider quota degrades mid-run,
      // arm-major order would feed every error to one arm and bias the
      // comparison. Interleaving spreads degradation across both evenly.
      for (const q of A.questions) {
        for (const arm of armNames) {
          if (token.isCancellationRequested) throw new Error('cancelled');
          prog.report({ message: `${q.id} · ${arm}`, increment: 100 / total });

          const user = `${A.arms[arm].context}\n\n---\n\nQUESTION\n${q.question}`;
          let got: string[] = [];
          let raw = '';
          let failure = '';
          try {
            const r = await llm.chat(SYSTEM, user, token, resolved);
            raw = r.text; modelUsed = r.model;
            const p = parseJsonLoose<{ answer: string[] }>(raw);
            got = Array.isArray(p?.answer) ? p!.answer.map(String) : [];
          } catch (e: any) {
            // A request that never completed is NOT a wrong answer. Record it
            // as a failure so the report cannot score it as one — an F1 of 0.00
            // means "the model answered and was wrong", and saying that about a
            // request that 400'd is the worst kind of confidently-wrong output.
            failure = String(e?.message ?? e);
            raw = `ERROR: ${failure}`;
          }
          results[arm][q.id] = { got, raw, failure, ...score(got, q.answer) };
          out.appendLine(failure
            ? `[${arm}] ${q.id}: FAILED — ${failure.split('\n')[0].slice(0, 120)}`
            : `[${arm}] ${q.id}: F1=${results[arm][q.id].f1.toFixed(2)} ` +
              `(got ${got.length}, want ${q.answer.length})`);
          done++;
        }
      }
    });

  // ---- report ------------------------------------------------------------
  const failures = armNames.flatMap(a =>
    A.questions.filter(q => results[a][q.id].failure).map(q => ({ arm: a, q: q.id,
      why: results[a][q.id].failure as string })));
  const allFailed = failures.length === total;

  const L: string[] = [];
  L.push(`# codevis A/B eval — ${A.root}`);
  L.push(`\nModel: \`${modelUsed || (resolved?.id ?? 'unknown')}\`` +
         (modelUsed ? '' : ' — **requested; no request ever succeeded**') +
         (resolved?.maxInputTokens
           ? ` (${resolved.maxInputTokens.toLocaleString()}-token context — both arms fit)`
           : '') +
         ` · ${new Date().toISOString()}`);

  if (allFailed) {
    // Do not print a scoreboard. Every number in it would be an artefact of
    // the failure, and a reader skimming "0.00 vs 0.00 — tie" would conclude
    // the experiment ran and found no difference. It did not run at all.
    L.push(`\n## This run produced no data\n`);
    L.push(`**All ${total} requests failed.** No answer was received from any model, so`);
    L.push(`there are no scores to report — a failed request is not a wrong answer, and`);
    L.push(`presenting it as F1 0.00 would be a measurement of nothing.\n`);
    const seen = new Map<string, number>();
    for (const f of failures) seen.set(f.why, (seen.get(f.why) ?? 0) + 1);
    L.push(`### What failed\n`);
    for (const [why, n] of [...seen].sort((a, b) => b[1] - a[1])) {
      L.push(`- **${n}× ** \`${why.split('\n')[0]}\``);
    }
    L.push(`\nFix the cause above and run **codevis: Run A/B eval** again.`);
    const report = L.join('\n');
    fs.writeFileSync(path.join(evalDir, 'RESULTS.md'), report, 'utf8');
    fs.writeFileSync(path.join(evalDir, 'results.json'),
                     JSON.stringify({ model: modelUsed, results }, null, 2), 'utf8');
    return report;
  }

  if (failures.length) {
    L.push(`\n> ⚠️ **${failures.length} of ${total} requests failed** and are marked \`ERR\` below.`);
    L.push(`> They are excluded from the means — a failed request is not a wrong answer.`);
    L.push(`> Affected: ${failures.map(f => `\`${f.arm}/${f.q}\``).join(', ')}.`);
    L.push(`> First error: \`${failures[0].why.split('\n')[0]}\``);
  }
  L.push(`\nBoth arms received the same questions and the same model. Only the`);
  L.push(`context differed. Answers are sets; scoring is precision/recall/F1.`);
  L.push(`The answer key was recomputed from source by \`eval/facts.py\` and never`);
  L.push(`reads the codevis index.\n`);

  L.push(`## Context size\n`);
  L.push(`| arm | chars | ~tokens | what it got |`);
  L.push(`|---|---:|---:|---|`);
  for (const arm of armNames) {
    const n = A.arms[arm].context.length;
    L.push(`| \`${arm}\` | ${n.toLocaleString()} | ${Math.round(n / 4).toLocaleString()} | ${A.arms[arm].label} |`);
  }

  L.push(`\n## Scores\n`);
  L.push(`| question | ${armNames.map(a => `${a} F1`).join(' | ')} | winner |`);
  L.push(`|---|${armNames.map(() => '---:').join('|')}|---|`);
  const totals: Record<string, number> = {};
  const counts: Record<string, number> = {};
  for (const q of A.questions) {
    const cells = armNames.map(a => results[a][q.id]);
    armNames.forEach((a, i) => {
      if (!cells[i].failure) {
        totals[a] = (totals[a] || 0) + cells[i].f1;
        counts[a] = (counts[a] || 0) + 1;
      }
    });
    // A row where either arm failed is not a comparison — say so rather than
    // crowning the surviving arm by default.
    const anyFailed = cells.some(c => c.failure);
    const f1s = cells.map(c => c.failure ? 'ERR' : c.f1.toFixed(2));
    let winner = 'n/a — request failed';
    if (!anyFailed) {
      const nums = cells.map(c => c.f1);
      const best = Math.max(...nums);
      const winners = armNames.filter((_, i) => nums[i] === best);
      winner = winners.length === armNames.length ? 'tie' : winners.join(', ');
    }
    L.push(`| \`${q.id}\` | ${f1s.join(' | ')} | ${winner} |`);
  }
  L.push(`| **mean** | ${armNames.map(a =>
    counts[a] ? (totals[a] / counts[a]).toFixed(2) + (counts[a] < A.questions.length
      ? ` (of ${counts[a]})` : '') : 'ERR').join(' | ')} | |`);

  L.push(`\n## What each arm missed\n`);
  for (const q of A.questions) {
    L.push(`### \`${q.id}\``);
    L.push(`\n${q.question}\n`);
    L.push(`*Why this question:* ${q.why}\n`);
    L.push(`**Correct answer (${q.answer.length}):** ${q.answer.map(a => `\`${a}\``).join(', ')}\n`);
    for (const arm of armNames) {
      const r = results[arm][q.id];
      if (r.failure) {
        // No "missed:" list here — the model was never asked successfully, and
        // listing the whole answer key as "missed" reads as a wrong answer.
        L.push(`- **${arm}** — REQUEST FAILED: \`${r.failure.split('\n')[0]}\``);
        continue;
      }
      L.push(`- **${arm}** — F1 ${r.f1.toFixed(2)} (precision ${r.precision.toFixed(2)}, recall ${r.recall.toFixed(2)})`);
      if (r.missed.length) L.push(`    - missed: ${r.missed.map((m: string) => `\`${m}\``).join(', ')}`);
      if (r.spurious.length) L.push(`    - invented: ${r.spurious.map((m: string) => `\`${m}\``).join(', ')}`);
      if (!r.missed.length && !r.spurious.length) L.push(`    - exact`);
    }
    L.push('');
  }

  const report = L.join('\n');
  fs.writeFileSync(path.join(evalDir, 'RESULTS.md'), report, 'utf8');
  fs.writeFileSync(path.join(evalDir, 'results.json'),
                   JSON.stringify({ model: modelUsed, results }, null, 2), 'utf8');
  return report;
}
