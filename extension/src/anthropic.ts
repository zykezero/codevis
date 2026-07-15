/** Direct Anthropic API adapter — the fallback for users without Copilot.
 *
 * `vscode.lm` is still the preferred path (VS Code owns the key, the consent
 * prompt, and the billing), but a user without Copilot has NO model there and
 * no way to add one, because the "bring your own key" flow the diagnostics
 * point to ships inside Copilot. This adapter closes that dead-end: an API key
 * in the OS keychain (SecretStorage), the live model list from the Models API,
 * and a streamed chat with real cancellation.
 *
 * The key never touches settings, workspace state, logs, or the webview.
 */
import Anthropic from '@anthropic-ai/sdk';
import * as vscode from 'vscode';
import type { ChatResult } from './llm';

const SECRET_KEY = 'codevis.anthropicKey';

/** Sensible default when the setting is just the bare prefix. */
export const DEFAULT_ANTHROPIC_MODEL = 'claude-opus-4-8';

export type DirectModel = { id: string; displayName: string };

export class AnthropicDirect {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  private key(): Thenable<string | undefined> { return this.secrets.get(SECRET_KEY); }

  async hasKey(): Promise<boolean> { return !!(await this.key()); }

  async storeKey(key: string): Promise<void> { await this.secrets.store(SECRET_KEY, key); }

  async clearKey(): Promise<void> { await this.secrets.delete(SECRET_KEY); }

  private async client(): Promise<Anthropic> {
    const key = await this.key();
    if (!key) {
      throw new Error(
        'No Anthropic API key is stored. Run "codevis: Set Anthropic API key" to add one.');
    }
    return new Anthropic({ apiKey: key });
  }

  /** Live list from the Models API — nothing hardcoded beyond the default. */
  async listModels(): Promise<DirectModel[]> {
    const client = await this.client();
    const out: DirectModel[] = [];
    for await (const m of client.models.list()) {
      out.push({ id: m.id, displayName: m.display_name });
    }
    return out;
  }

  /** Verify a key with one live Models API call. Null on success, else a
   * human-readable reason — so a bad key fails at paste time, not on the
   * first Describe. */
  static async verify(key: string): Promise<string | null> {
    try {
      const client = new Anthropic({ apiKey: key });
      await client.models.list();
      return null;
    } catch (e: any) {
      if (e instanceof Anthropic.AuthenticationError) {
        return 'the key was rejected by the API (authentication failed)';
      }
      if (e instanceof Anthropic.APIConnectionError) {
        return 'could not reach api.anthropic.com — check your network or proxy';
      }
      return String(e?.message ?? e);
    }
  }

  /** Stream a completion. Unlike the vscode.lm path this owns the request
   * shape, so the system prompt is a REAL system prompt. The cancellation
   * token maps to an abort signal so the panel's Cancel button keeps working. */
  async chat(model: string, system: string, user: string,
             token: vscode.CancellationToken): Promise<ChatResult> {
    const client = await this.client();
    const ac = new AbortController();
    const sub = token.onCancellationRequested(() => ac.abort());
    try {
      const stream = client.messages.stream({
        model,
        max_tokens: 8192,
        system,
        messages: [{ role: 'user', content: user }],
      }, { signal: ac.signal });
      const final = await stream.finalMessage();
      const text = final.content
        .filter((b): b is Anthropic.TextBlock => b.type === 'text')
        .map(b => b.text)
        .join('');
      return { text, model: `anthropic-api/${model}` };
    } catch (e: any) {
      if (token.isCancellationRequested) throw new Error('cancelled');
      if (e instanceof Anthropic.AuthenticationError) {
        throw new Error(
          'the stored Anthropic API key was rejected. Run "codevis: Set Anthropic API key" ' +
          'with a fresh key, or "codevis: Clear Anthropic API key" to remove it.');
      }
      if (e instanceof Anthropic.NotFoundError) {
        throw new Error(
          `the model "${model}" was not found on the Anthropic API. ` +
          'Pick another with "codevis: Select language model".');
      }
      if (e instanceof Anthropic.RateLimitError) {
        throw new Error('Anthropic API rate limit reached — wait a moment and try again.');
      }
      if (e instanceof Anthropic.APIConnectionError) {
        throw new Error('could not reach api.anthropic.com — check your network or proxy.');
      }
      throw e;
    } finally {
      sub.dispose();
    }
  }
}
