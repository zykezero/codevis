// ============================================================================
// Webview <-> extension host bridge.
//
// Everything above this line is the SAME vanilla JS that powers the standalone
// HTML export. A webview is a browser context, so none of it needed porting —
// this file only adds the things a browser could not do: reveal code in the real
// editor, and talk to a language model the user already authorised.
// ============================================================================
const VS = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : null;
const IN_VSCODE = !!VS;

function host(msg) { if (VS) VS.postMessage(msg); }

if (IN_VSCODE) {
  document.body.classList.add('in-vscode');

  // Hover a node -> reveal its code in the editor (spec A.3).
  window.addEventListener('codevis:nodeHover', e => host({ type: 'hover', id: e.detail.id }));
  window.addEventListener('codevis:nodeHoverEnd', () => host({ type: 'hoverEnd' }));
  window.addEventListener('codevis:reveal', e => host({ type: 'reveal', id: e.detail.id }));
  window.addEventListener('codevis:revealSpan', e =>
    host({ type: 'revealSpan', span: e.detail.span }));
  window.addEventListener('codevis:contextualize', e => host({
    type: 'contextualize', id: e.detail.id,
    callerId: e.detail.callerId, force: e.detail.force
  }));
  window.addEventListener('codevis:applyEdit', e =>
    host({ type: 'applyEdit', id: e.detail.id, text: e.detail.text }));

  window.addEventListener('message', ev => {
    const m = ev.data;
    if (m.type === 'contextualizing') setCtxState(m.id, 'loading');
    if (m.type === 'contextualized') renderCtx(m.id, m.data, m.cached);
    if (m.type === 'contextualizeError') setCtxState(m.id, 'error', m.error);
    if (m.type === 'focusFile') { setView('flow'); setFlowFile(m.file); }
    if (m.type === 'editApplied') editApplied(m.id);
    if (m.type === 'editFailed') editFailed(m.id, m.error);
  });
}
