/** Feature B — the context bundle, the structured response, the cache.
 *
 * The spec calls this "contextualize"; the UI says "describe", which is what a
 * reader is actually asking for. The type names below still track the spec doc,
 * deliberately — it is the contract this was built against.
 *
 * The crux (spec §B.3): because we already hold a RESOLVED graph, we send a tight
 * scoped bundle instead of dumping files. That is what makes the explanation both
 * good and cheap — the model gets exactly the callee, the one call site it was
 * opened from, and the signatures (not bodies) of what it depends on.
 */
import * as vscode from 'vscode';
import { GraphIndex, Symbol } from './graph';
import { LLMClient, parseJsonLoose } from './llm';

export type ContextualizeRequest = {
  target: { qualified_name: string; source: string; signature: string; docstring: string | null };
  caller?: { qualified_name: string; call_site_source: string };
  target_dependencies: { qualified_name: string; signature: string }[];
  runtime_sample?: { args: unknown; return: unknown } | null;
  question: string;
};

export type ContextualizeResponse = {
  explanation: string;
  referenced_symbols: string[];
};

const SYSTEM = `You explain code to a reviewer who is reading it as a document, not running it.
Answer ONLY with a JSON object, no prose outside it, no code fences:
{"explanation": "<markdown>", "referenced_symbols": ["<qualified name>", ...]}

Rules:
- explanation: 2-5 short paragraphs of markdown. Explain what the target DOES and,
  if a caller is given, what its behaviour MEANS FOR THAT CALLER specifically.
- Be concrete. Reference real parameter and symbol names from the provided context.
- Do not speculate about code you were not shown. Say what is not determinable.
- referenced_symbols: qualified names, drawn ONLY from the context provided, that a
  reader should follow next. Omit anything you were not shown. Never invent a name.`;

/** Build the bundle. Bounded on purpose — see spec open decisions. */
export function buildRequest(
  gi: GraphIndex, target: Symbol, caller: Symbol | null
): ContextualizeRequest {
  const cfg = vscode.workspace.getConfiguration('codevis');
  const pad = cfg.get<number>('describe.callerLines', 6);
  const maxDeps = cfg.get<number>('describe.maxDependencies', 12);

  let callerPart: ContextualizeRequest['caller'];
  if (caller) {
    // Send the CALL SITE plus a few lines — not the whole caller. Cheaper, and the
    // spec's recommended default: start narrow, widen only if answers are weak.
    const edge = gi.g.edges.find(e =>
      e.kind === 'calls' && e.from_symbol === caller.id && e.to_symbol === target.id);
    const site = edge?.call_sites?.[0];
    const src = gi.g.files[caller.span.file]?.split('\n') ?? [];
    let snippet: string;
    if (site) {
      const a = Math.max(0, site.start_line - 1 - pad);
      const b = Math.min(src.length, site.start_line + pad);
      snippet = src.slice(a, b).join('\n');
    } else {
      snippet = gi.sourceOf(caller).split('\n').slice(0, pad * 2).join('\n');
    }
    callerPart = { qualified_name: caller.qualname, call_site_source: snippet };
  }

  const deps = (gi.calls.get(target.id) ?? [])
    .map(id => gi.byId.get(id))
    .filter((s): s is Symbol => !!s)
    .slice(0, maxDeps)
    .map(s => ({ qualified_name: s.qualname, signature: s.signature || s.name }));

  const question = caller
    ? `Explain what ${target.qualname} does, and specifically how its behaviour affects ` +
      `${caller.qualname} at the call site shown.`
    : `Explain what ${target.qualname} does and what role it plays in this codebase.`;

  return {
    target: {
      qualified_name: target.qualname,
      source: gi.sourceOf(target),
      signature: target.signature || target.name,
      docstring: target.doc || null
    },
    caller: callerPart,
    target_dependencies: deps,
    runtime_sample: null,          // populated once the trace layer exists (D1/D10)
    question
  };
}

function render(req: ContextualizeRequest): string {
  const L: string[] = [];
  L.push(`## Target: ${req.target.qualified_name}`);
  L.push('```\n' + req.target.signature + '\n```');
  if (req.target.docstring) L.push(`Docstring: ${req.target.docstring}`);
  L.push('Source:\n```\n' + req.target.source + '\n```');
  if (req.caller) {
    L.push(`## Opened from: ${req.caller.qualified_name}`);
    L.push('Call site:\n```\n' + req.caller.call_site_source + '\n```');
  }
  if (req.target_dependencies.length) {
    L.push('## What the target calls (signatures only)');
    L.push(req.target_dependencies.map(d => `- ${d.qualified_name}: ${d.signature}`).join('\n'));
  }
  if (req.runtime_sample) {
    L.push('## Observed at runtime');
    L.push('```json\n' + JSON.stringify(req.runtime_sample, null, 2) + '\n```');
  }
  L.push('## Question');
  L.push(req.question);
  return L.join('\n\n');
}

/** Cache key: (target, caller, VERSION, MODEL).
 *
 * The spec asked for (target, caller, code hash) — right, until the model became
 * user-selectable. Two models give two different explanations of the same code,
 * so the model is part of the answer's identity. Without it, switching from
 * Copilot to Claude and re-opening a card silently returns the Copilot answer
 * labelled with Claude's name.
 *
 * Identity alone would go stale on edit; version alone would collide across
 * callers. All four.
 */
export function cacheKey(target: Symbol, caller: Symbol | null, model = ''): string {
  return `${target.id}|${caller?.id ?? '-'}|${target.content_hash}|${model}`;
}

export type Contextualized = ContextualizeResponse & {
  model: string;
  links: { name: string; id: string | null }[];
};

export class Contextualizer {
  constructor(
    private readonly llm: LLMClient,
    private readonly memento: vscode.Memento
  ) {}

  /** The cached answer FOR THE CURRENTLY SELECTED MODEL, if any. */
  async get(target: Symbol, caller: Symbol | null): Promise<Contextualized | undefined> {
    const model = await this.activeModel();
    return this.memento.get<Contextualized>('ctx:' + cacheKey(target, caller, model));
  }

  private async activeModel(): Promise<string> {
    const anyLlm = this.llm as { resolve?: () => Promise<{ vendor: string; family: string } | undefined> };
    if (typeof anyLlm.resolve !== 'function') return '';
    const m = await anyLlm.resolve();
    return m ? `${m.vendor}/${m.family}` : '';
  }

  async run(
    gi: GraphIndex, target: Symbol, caller: Symbol | null,
    token: vscode.CancellationToken, force = false
  ): Promise<Contextualized> {
    const active = await this.activeModel();
    const key = 'ctx:' + cacheKey(target, caller, active);
    if (!force) {
      const hit = this.memento.get<Contextualized>(key);
      if (hit) return hit;
    }

    const req = buildRequest(gi, target, caller);
    const { text, model } = await this.llm.chat(SYSTEM, render(req), token);

    const parsed = parseJsonLoose<ContextualizeResponse>(text);
    // Malformed JSON must degrade, not error: show the prose, drop the links.
    const resp: ContextualizeResponse = parsed?.explanation
      ? parsed
      : { explanation: text, referenced_symbols: [] };

    const links = (resp.referenced_symbols ?? []).map(name => {
      const hit = gi.resolveName(name);
      return { name, id: hit ? hit.id : null };   // null => render as plain text
    });

    const out: Contextualized = { ...resp, model, links };
    await this.memento.update(key, out);
    return out;
  }
}
