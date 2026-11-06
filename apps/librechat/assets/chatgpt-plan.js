// Development only: shows the ChatGPT plan connection inside TATC AI. Sign-in
// stays in its own tab because OpenAI's pages and callback cannot run in a frame.
(() => {
  const bridge = "http://127.0.0.1:8788";
  const usageUrl = "https://chatgpt.com/settings/usage";
  let button, dialog, body, timer;

  const style = document.createElement("style");
  style.textContent = `
    #tatc-plan-toggle { display: inline-flex; align-items: center; flex-shrink: 0; height: 36px;
      margin-left: 8px; padding: 0 11px; border: 1px solid var(--border-light); border-radius: 12px;
      background: var(--presentation); color: inherit; font: inherit; font-size: 13px; cursor: pointer; }
    #tatc-plan-toggle:hover { background: var(--surface-hover); }
    #tatc-plan-dialog { width: min(440px, calc(100vw - 32px)); padding: 24px; border: 1px solid var(--border-light);
      border-radius: 16px; background: var(--presentation); color: var(--text-primary); font-size: 14px; line-height: 1.5; }
    #tatc-plan-dialog::backdrop { background: rgba(0, 0, 0, 0.5); }
    #tatc-plan-dialog h2 { margin: 0 0 12px; font-size: 18px; font-weight: 600; }
    #tatc-plan-dialog p { margin: 0 0 12px; }
    #tatc-plan-dialog a { text-decoration: underline; }
    #tatc-plan-dialog .tatc-plan-error { color: #d9534f; }
    #tatc-plan-dialog .tatc-plan-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
    #tatc-plan-dialog button { padding: 8px 14px; border: 1px solid var(--border-light); border-radius: 8px;
      background: var(--surface-hover); color: inherit; cursor: pointer; }`;
  document.head.append(style);

  function element(tag, text, properties = {}) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    return Object.assign(node, properties);
  }

  function link(text, href) {
    return element("a", text, { href, target: "_blank", rel: "noreferrer" });
  }

  function paragraph(...children) {
    const node = element("p");
    node.append(...children);
    return node;
  }

  function signInForm() {
    const form = element("form", "", { method: "post", action: `${bridge}/connect`, target: "_blank" });
    form.append(element("button", "Continue with ChatGPT", { type: "submit" }));
    return form;
  }

  async function saveKey(key) {
    // The same request LibreChat's own key dialog sends, with no expiry.
    const session = await fetch("/api/auth/refresh", { method: "POST" });
    if (!session.ok) throw new Error(`TATC AI could not confirm your session (HTTP ${session.status}).`);
    const { token } = await session.json();
    const saved = await fetch("/api/keys", {
      method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ name: "ChatGPT Plan", value: JSON.stringify({ apiKey: key }), expiresAt: "" }),
    });
    if (!saved.ok) throw new Error(`TATC AI could not save the key (HTTP ${saved.status}).`);
  }

  function showConnected(status) {
    const result = element("p");
    const use = element("button", "Use with this account", { type: "button" });
    use.onclick = async () => {
      use.disabled = true;
      try {
        await saveKey(status.key);
        result.className = "";
        result.textContent = "Saved. Choose a model from ChatGPT plan (local only).";
      } catch (error) {
        result.className = "tatc-plan-error";
        result.textContent = error.message;
      }
      use.disabled = false;
    };
    const actions = element("div", "", { className: "tatc-plan-actions" });
    actions.append(use, close());
    body.replaceChildren(
      paragraph(`Connected as ${status.email || "your ChatGPT account"}.`),
      paragraph("Using ChatGPT plan · ", link("Manage usage", usageUrl), " · ", link("Manage connection", bridge)),
      element("p", "Requests from this account count toward your plan limits. No other provider is used if a request fails."),
      result,
      actions,
    );
  }

  function close() {
    const button = element("button", "Close", { type: "button" });
    button.onclick = () => dialog.close();
    return button;
  }

  function showSignIn(message) {
    const actions = element("div", "", { className: "tatc-plan-actions" });
    actions.append(signInForm(), close());
    body.replaceChildren(element("p", message), actions);
  }

  function showError(message) {
    const actions = element("div", "", { className: "tatc-plan-actions" });
    actions.append(close());
    body.replaceChildren(element("p", message, { className: "tatc-plan-error" }), actions);
  }

  async function refresh() {
    clearTimeout(timer);
    if (!dialog.open) return;
    let status;
    try {
      const response = await fetch(`${bridge}/status`);
      if (!response.ok) throw new Error();
      status = await response.json();
    } catch {
      showError(`The ChatGPT plan service is not reachable at ${bridge}. Check that the chatgpt-bridge container is running.`);
      timer = setTimeout(refresh, 3000);
      return;
    }
    if (status.connected && status.plan_enabled) {
      // Leave this in place so a result message or a click is not replaced.
      if (dialog.dataset.key !== status.key) {
        dialog.dataset.key = status.key;
        showConnected(status);
      }
      return;
    }
    delete dialog.dataset.key;
    showSignIn(status.connected
      ? "You signed in, but ChatGPT plan usage was not enabled. Enable it to use your plan here, or add another provider's API key."
      : "Use your ChatGPT Plus or Pro plan on this computer. Usage counts toward your existing plan limits.");
    timer = setTimeout(refresh, 3000);
  }

  function open() {
    if (!dialog) {
      dialog = element("dialog", "", { id: "tatc-plan-dialog" });
      body = element("div");
      dialog.append(element("h2", "ChatGPT plan"), body);
      dialog.onclose = () => clearTimeout(timer);
      document.body.append(dialog);
    }
    delete dialog.dataset.key;
    dialog.showModal();
    refresh();
  }

  function mount() {
    const header = document.querySelector("#root main .absolute.top-0 > .hide-scrollbar")?.parentElement;
    if (!header || button?.isConnected) return;
    button ??= element("button", "ChatGPT plan", { id: "tatc-plan-toggle", type: "button", onclick: open });
    header.append(button);
  }

  new MutationObserver(mount).observe(document.body, { childList: true, subtree: true });
  mount();
})();
