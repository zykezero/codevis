/** LLMClient — provider-agnostic, resolved at call time (feature spec §B.2).
 *
 * The spec assumed we would ship a BYOK adapter: take the user's key, store it,
 * call the provider ourselves. Inside VS Code that is mostly unnecessary work.
 * `vscode.lm` hands us a model the user has ALREADY authorised — VS Code owns the
 * key, the consent prompt, and the billing. Anything registered there is fair
 * game: Copilot's models, or your own Anthropic/OpenAI key added through
 * `Chat: Manage Language Models`.
 *
 * We never filter by vendor. The earlier version did not either — but it took
 * `models[0]`, which silently meant "whatever VS Code happened to list first".
 * With Copilot installed that is Copilot, every time, with no way to see it or
 * change it. Arbitrary-but-invisible is indistinguishable from hardcoded.
 */
import * as vscode from 'vscode';

export type ChatResult = { text: string; model: string };

/** An already-resolved model choice. `id` is the stable spelling used in cache
 * keys ("vendor/family"); `handle` is whatever the client needs to run it. */
export type ResolvedModel = { id: string; handle?: unknown };

export interface LLMClient {
  available(): Promise<boolean>;
  /** Resolve the active model ONCE per operation and thread the result into
   * chat(). Resolving separately for the cache key and the request risks the
   * two disagreeing when the model list changes between them — the answer
   * would be cached under a different model than produced it. */
  resolveActive(): Promise<ResolvedModel | undefined>;
  chat(system: string, user: string, token: vscode.CancellationToken,
       resolved?: ResolvedModel): Promise<ChatResult>;
}

/** A choice, as written in settings: "vendor/family", or a bare "family". */
export type ModelPick = { vendor: string; family: string; id: string; name: string };

const SETTING = 'codevis.model';

const NL = String.fromCharCode(10);
const BR = NL;   // a blank markdown line

/** How a pick is spelled in settings.json. Stable across sessions and machines. */
export function toSetting(m: { vendor: string; family: string }): string {
  return `${m.vendor}/${m.family}`;
}

function parseSetting(v: string): { vendor?: string; family: string } | null {
  const s = (v || '').trim();
  if (!s) return null;
  const i = s.indexOf('/');
  return i < 0 ? { family: s } : { vendor: s.slice(0, i), family: s.slice(i + 1) };
}

export function describe(m: vscode.LanguageModelChat): string {
  return `${m.vendor}/${m.family}`;
}

export class VSCodeLM implements LLMClient {
  /** The SETTING is the source of truth, not a hidden store.
   *
   * A model choice that lives only in extension state is invisible: you cannot
   * see it in the Settings UI, cannot commit it to a workspace, cannot diff it,
   * and cannot fix it without launching a command. It is a user preference, so it
   * belongs in `settings.json` like every other one. The picker is a convenience
   * that WRITES this setting; it is not a parallel source of truth.
   */
  private cfg() { return vscode.workspace.getConfiguration(); }

  /** Every model VS Code will give us, regardless of who provides it.
   *
   * An empty selector SHOULD mean "everything". It does not always: model
   * discovery has been flaky across VS Code versions, and a provider that has not
   * finished registering yet reports nothing. Ask several ways and union the
   * results rather than trusting one call and concluding "you have no models".
   */
  async list(): Promise<vscode.LanguageModelChat[]> {
    const seen = new Map<string, vscode.LanguageModelChat>();
    const selectors: vscode.LanguageModelChatSelector[] = [
      {},                        // should be everything
      { vendor: 'copilot' },     // Copilot, incl. the Claude models it resells
      { vendor: 'anthropic' },   // a provider extension registering its own vendor
    ];
    for (const sel of selectors) {
      try {
        for (const m of await vscode.lm.selectChatModels(sel)) {
          if (!seen.has(m.id)) seen.set(m.id, m);
        }
      } catch { /* a selector that matches nothing is not an error */ }
    }
    return [...seen.values()];
  }

  /** What VS Code reports, verbatim — for when the picker says "no models". */
  async diagnose(): Promise<string> {
    const L: string[] = ['# codevis: language model diagnostics' + BR];
    L.push(`VS Code: ${vscode.version}`);
    L.push(`codevis.model setting: \`${this.getSetting() || '(unset)'}\`` + BR);

    L.push('## What `vscode.lm.selectChatModels(...)` returns' + BR);
    const selectors: [string, vscode.LanguageModelChatSelector][] = [
      ['{} (everything)', {}],
      ["{ vendor: 'copilot' }", { vendor: 'copilot' }],
      ["{ vendor: 'anthropic' }", { vendor: 'anthropic' }],
    ];
    for (const [label, sel] of selectors) {
      try {
        const ms = await vscode.lm.selectChatModels(sel);
        L.push(`- \`${label}\` -> ${ms.length} model(s)`);
        for (const m of ms) {
          L.push(`    - **${m.name}** — vendor \`${m.vendor}\`, family \`${m.family}\`, ` +
                 `id \`${m.id}\`, ${m.maxInputTokens.toLocaleString()} input tokens`);
          L.push(`        - put this in \`codevis.model\`: \`${toSetting(m)}\``);
        }
      } catch (e: any) {
        L.push(`- \`${label}\` -> THREW: ${e?.message ?? e}`);
      }
    }

    L.push(BR + '## Relevant extensions' + BR);
    for (const id of ['github.copilot', 'github.copilot-chat', 'anthropic.claude-code']) {
      const ext = vscode.extensions.getExtension(id);
      L.push(`- \`${id}\`: ${ext ? (ext.isActive ? 'installed, active' : 'installed, NOT active') : 'not installed'}`);
    }

    L.push(BR + '## If the lists above are empty' + BR);
    L.push('`vscode.lm` only exposes models registered with VS Code **as a language');
    L.push('model provider**. That is not the same as having Claude available in a');
    L.push('chat sidebar — an extension can talk to Claude privately without');
    L.push('publishing it to `vscode.lm`, and then no other extension can use it.' + BR);
    L.push('Things worth checking, in order:');
    L.push('1. Run **Chat: Manage Language Models** — is Anthropic listed there with a key?');
    L.push('2. Consent: VS Code asks permission the first time an extension uses a model.');
    L.push('   Press **Describe** on any card once and see whether a prompt appears.');
    L.push('3. If Copilot is installed, it resells Claude — those appear as');
    L.push('   `copilot/claude-*` and are usable by this extension.');
    return L.join('\n');
  }

  /** The raw setting value, e.g. "anthropic/claude-sonnet-4". */
  getSetting(): string { return this.cfg().get<string>(SETTING, '') || ''; }

  async setSetting(v: string) {
    await this.cfg().update(SETTING, v, vscode.ConfigurationTarget.Global);
  }

  /** The last fallback we warned about, as "<setting> -> <substitute>".
   * A misconfigured model must warn ONCE, not once per resolution — a single
   * Describe resolves more than once (cache key, then request), and the doubled
   * toast was pure noise. Warn again only when the situation changes. */
  private lastWarnedFallback = '';

  /** Resolve the setting against what is actually available right now. */
  async resolve(): Promise<vscode.LanguageModelChat | undefined> {
    const models = await this.list();
    if (!models.length) return undefined;

    const want = parseSetting(this.getSetting());
    if (!want) return models[0];         // unset: first available, and the status bar says which

    const hit = models.find(m =>
      m.family === want.family && (!want.vendor || m.vendor === want.vendor))
      ?? models.find(m => m.id === want.family);
    if (hit) {
      this.lastWarnedFallback = '';      // configured model is back — re-arm the warning
      return hit;
    }

    // The configured model is not available (key revoked, provider uninstalled,
    // a typo). Say so — answering with a DIFFERENT model than the one configured,
    // silently, is exactly the failure this whole change exists to remove.
    const fallback = `${this.getSetting()} -> ${describe(models[0])}`;
    if (fallback !== this.lastWarnedFallback) {
      this.lastWarnedFallback = fallback;
      void vscode.window.showWarningMessage(
        `codevis: the configured model "${this.getSetting()}" is not available. ` +
        `Using ${describe(models[0])} instead.`, 'Choose a model', 'Open settings')
        .then(a => {
          if (a === 'Choose a model') vscode.commands.executeCommand('codevis.selectModel');
          if (a === 'Open settings') vscode.commands.executeCommand(
            'workbench.action.openSettings', SETTING);
        });
    }
    return models[0];
  }

  async available(): Promise<boolean> {
    return (await this.list()).length > 0;
  }

  async resolveActive(): Promise<ResolvedModel | undefined> {
    const m = await this.resolve();
    return m ? { id: describe(m), handle: m } : undefined;
  }

  async chat(system: string, user: string, token: vscode.CancellationToken,
             resolved?: ResolvedModel): Promise<ChatResult> {
    const model = (resolved?.handle as vscode.LanguageModelChat | undefined)
      ?? await this.resolve();
    if (!model) {
      throw new Error(
        'No language model is available to VS Code. Run "Chat: Manage Language Models" ' +
        'to add one (your own Anthropic/OpenAI key, or a provider extension), then try again.');
    }
    try {
      const messages = [vscode.LanguageModelChatMessage.User(system + '\n\n' + user)];
      const res = await model.sendRequest(messages, {}, token);
      let text = '';
      for await (const chunk of res.text) text += chunk;
      return { text, model: describe(model) };
    } catch (e: any) {
      // Consent denial, quota exhaustion and filtering arrive as a typed
      // LanguageModelError; shown raw it reads like a crash. Translate the
      // codes the user can actually act on.
      if (e instanceof vscode.LanguageModelError) {
        const friendly: Record<string, string> = {
          NoPermissions:
            `you have not granted this extension access to ${describe(model)} ` +
            `(or declined the consent prompt). Run Describe again and accept the ` +
            `prompt, or review access under "Chat: Manage Language Models".`,
          Blocked:
            `the provider refused the request — usually quota exhaustion or ` +
            `content filtering. Wait a bit, or pick a different model with ` +
            `"codevis: Select language model".`,
          NotFound:
            `the model ${describe(model)} was not found — the provider may have ` +
            `signed out or removed it. Pick another with "codevis: Select language model".`,
        };
        throw new Error(friendly[e.code] ?? `language model request failed (${e.code}): ${e.message}`);
      }
      throw e;
    }
  }
}

/** Strip fences and parse. The model was ASKED for JSON; it may still wrap it. */
export function parseJsonLoose<T>(raw: string): T | null {
  const tries = [
    raw,
    raw.replace(/^[\s\S]*?```(?:json)?\s*/m, '').replace(/```[\s\S]*$/m, ''),
    (raw.match(/\{[\s\S]*\}/) || [''])[0]
  ];
  for (const t of tries) {
    try { const v = JSON.parse(t.trim()); if (v && typeof v === 'object') return v as T; }
    catch { /* next */ }
  }
  return null;
}
