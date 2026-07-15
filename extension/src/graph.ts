/** The Code Graph — the shared artifact both views read (feature spec §1).
 *
 * This mirrors the Python index schema exactly. It is deliberately dumb: the
 * indexer is the only thing that derives structure. The flowchart and the cards
 * are two VIEWS of this one model, never independent derivations of it.
 */

export type Span = {
  file: string;
  start_line: number; start_col: number;
  end_line: number; end_col: number;
};

export type Param = { name: string; annotation: string; default: string };

export type Symbol = {
  id: string;              // IDENTITY: "<file>::<qualname>". Never version.
  name: string;
  qualname: string;
  kind: 'function' | 'method' | 'class' | 'variable' | 'dataset' | 'column';
  span: Span;
  body: Span | null;
  doc: string;
  entry: boolean;
  changed: boolean;
  impact: number;
  signature: string;
  params: Param[];
  content_hash: string;    // VERSION. Cache key component, not identity.
};

export type Reference = {
  span: Span;
  text: string;
  resolves_to: string | null;
  target_kind: 'project' | 'local' | 'external' | 'data' | 'unresolved';
  confidence: number;
  enclosing: string | null;
};

export type Edge = {
  from_symbol: string;
  to_symbol: string;
  kind: 'calls' | 'reads' | 'produces' | 'consumes'
      | 'reads_column' | 'writes_column' | 'has_column';
  detail: string;
  call_sites: Span[];
};

export type CodeGraph = {
  language: string;
  root: string;
  symbols: Symbol[];
  references: Reference[];
  edges: Edge[];
  files: Record<string, string>;
  langs: Record<string, string>;
  fingerprint: string;
  diff_ref: string;
};

export class GraphIndex {
  readonly byId = new Map<string, Symbol>();
  readonly calls = new Map<string, string[]>();
  readonly calledBy = new Map<string, string[]>();

  constructor(readonly g: CodeGraph) {
    for (const s of g.symbols) this.byId.set(s.id, s);
    for (const e of g.edges) {
      if (e.kind !== 'calls') continue;
      push(this.calls, e.from_symbol, e.to_symbol);
      push(this.calledBy, e.to_symbol, e.from_symbol);
    }
  }

  /** Functions/methods/classes defined in one file — the nodes of its flowchart. */
  symbolsInFile(file: string): Symbol[] {
    return this.g.symbols
      .filter(s => s.span.file === file && ['function', 'method', 'class'].includes(s.kind))
      .sort((a, b) => a.span.start_line - b.span.start_line);
  }

  /** The source text of a symbol's whole definition. */
  sourceOf(s: Symbol): string {
    const b = s.body || s.span;
    const src = this.g.files[b.file];
    if (!src) return '';
    return src.split('\n').slice(b.start_line - 1, b.end_line).join('\n');
  }

  /** Resolve a name the model mentioned back to a real node.
   *
   * Deliberately ordered most-specific first. A model writes module-qualified
   * names naturally ("scoring.percent_rank") while our qualnames are bare
   * ("percent_rank"), so we also match module.name against <file stem>::<name>.
   * That is resolution, not invention.
   *
   * The LAST resort is a bare unique name. If a name is ambiguous across files we
   * return null and the UI renders plain text — a wrong link is worse than no link
   * (the rule that has held since the resolver spike).
   */
  resolveName(name: string): Symbol | null {
    const n = name.trim().replace(/\(\)$/, '').replace(/^`|`$/g, '');
    const code = (s: Symbol) => ['function', 'method', 'class'].includes(s.kind);

    const exactId = this.byId.get(n);
    if (exactId) return exactId;

    const byQual = this.g.symbols.filter(s => s.qualname === n && code(s));
    if (byQual.length === 1) return byQual[0];

    // "module.fn" / "pkg.module.fn" -> <file stem>::<fn>
    if (n.includes('.')) {
      const parts = n.split('.');
      const leaf = parts[parts.length - 1];
      const mod = parts[parts.length - 2];
      const hit = this.g.symbols.filter(s =>
        code(s) &&
        (s.name === leaf || s.qualname === leaf || s.qualname.endsWith('.' + leaf)) &&
        stem(s.span.file) === mod);
      if (hit.length === 1) return hit[0];
    }

    const byName = this.g.symbols.filter(s => s.name === n && code(s));
    return byName.length === 1 ? byName[0] : null;   // ambiguous => no link
  }
}

function stem(file: string): string {
  // The indexer normalises to POSIX, but an index can be produced on one OS and
  // read on another — accept both separators rather than trusting the producer.
  const base = file.split(/[\\/]/).pop() ?? file;
  return base.replace(/\.(py|R|r|sql)$/, '');
}

function push<K, V>(m: Map<K, V[]>, k: K, v: V) {
  const a = m.get(k);
  if (a) { if (!a.includes(v)) a.push(v); } else { m.set(k, [v]); }
}
