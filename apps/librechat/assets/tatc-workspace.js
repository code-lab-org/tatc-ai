// Optional LibreChat host adapter. Study is always available in chat; only
// trusted TATC resource frames can supply its content.
(() => {
  const script = document.currentScript;
  const origin = new URL(script.dataset.viewerOrigin).origin;
  const path = "/visualization/v1/";
  const seenResources = new Set();
  const expectedRequests = new Map();
  const rejectedResources = new Set();
  const resources = new Map();
  let conversationPath = location.pathname;
  let panel,
    frame,
    launcher,
    workspaceId,
    activeUrl,
    previousFocus,
    studyButton,
    pendingResult,
    width = 650;
  function isOpen() {
    return panel && !panel.hidden;
  }
  function notifyVisibility() {
    frame?.contentWindow.postMessage(
      {
        type: "tatc-workspace:visibility",
        payload: { workspaceId, visible: !!isOpen() },
      },
      origin,
    );
  }
  function latestResult() {
    let latest;
    for (const result of resources.values()) {
      if (!result.source.isConnected) continue;
      if (
        !latest ||
        latest.source.compareDocumentPosition(result.source) &
          Node.DOCUMENT_POSITION_FOLLOWING
      )
        latest = result;
    }
    return latest;
  }
  function mountButton() {
    if (studyButton?.isConnected && /^\/c\//.test(location.pathname)) return;
    // This is the Header row in the deployed LibreChat build, on both desktop
    // and mobile. Keep this one integration point covered by the browser test.
    const header = /^\/c\//.test(location.pathname)
      ? document.querySelector("#root main .absolute.top-0 > .hide-scrollbar")
          ?.parentElement
      : null;
    if (!header) {
      studyButton?.remove();
      return;
    }
    if (!studyButton) {
      studyButton = document.createElement("button");
      studyButton.id = "tatc-study-toggle";
      studyButton.type = "button";
      studyButton.setAttribute("aria-controls", "tatc-study-pane");
      studyButton.setAttribute("aria-expanded", "false");
      studyButton.innerHTML =
        '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14 4v16"/></svg><span>Study</span>';
      studyButton.onclick = () => {
        if (isOpen()) close();
        else {
          const result = pendingResult || (!frame && latestResult());
          show(studyButton);
          if (result) loadResult(result.source, result.payload);
        }
      };
    }
    if (studyButton.parentElement !== header) header.append(studyButton);
  }
  function resourceFrame(source) {
    return [...document.querySelectorAll("iframe")].find((f) => {
      if (f === frame || f.contentWindow !== source) return false;
      try {
        const u = new URL(f.src);
        return u.origin === origin && u.pathname === path + "launch";
      } catch {
        return false;
      }
    });
  }
  function valid(payload) {
    try {
      const u = new URL(payload.url);
      return (
        u.origin === origin &&
        u.pathname === path &&
        /^#v1\.[\w-]+$/.test(u.hash) &&
        u.hash.length <= 2000004 &&
        /^[0-9a-f-]{36}$/i.test(payload.workspaceId) &&
        typeof payload.title === "string" &&
        payload.title.length <= 120
      );
    } catch {
      return false;
    }
  }
  function size(value) {
    if (innerWidth > 800)
      width = Math.max(380, Math.min(value, innerWidth - 360));
    // The sidebar shares #root with the chat; keep 360px of chat beside it
    // without forgetting the chosen width once the sidebar closes.
    const sidebar =
      document.querySelector("#root main")?.getBoundingClientRect().left || 0;
    document.documentElement.style.setProperty(
      "--tatc-pane-width",
      Math.max(380, Math.min(width, innerWidth - sidebar - 360)) + "px",
    );
  }
  // Compare JSON objects independently of property ordering. Arrays and every
  // input value must match; a validated but altered mission is still a mismatch.
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.keys(value)
          .sort()
          .map((key) => [key, canonical(value[key])]),
      );
    return value;
  }
  function requestKey(value) {
    return JSON.stringify(canonical(value));
  }
  function rejectResult(url, resultWorkspaceId, source) {
    rejectedResources.add(url);
    source.contentWindow.postMessage(
      {
        type: "tatc-workspace:result-mismatch",
        payload: { workspaceId: resultWorkspaceId },
      },
      origin,
    );
    if (resultWorkspaceId !== workspaceId) return;
    frame?.contentWindow.postMessage(
      {
        type: "tatc-workspace:result-mismatch",
        payload: { workspaceId },
      },
      origin,
    );
  }
  function close() {
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    notifyVisibility();
    studyButton?.setAttribute("aria-expanded", "false");
    document.documentElement.classList.remove("tatc-pane-open");
    if (previousFocus?.isConnected) previousFocus.focus();
  }
  function checkConversation() {
    // LibreChat gives a new chat its ID after the first prompt (/c/new to
    // /c/<id>). That is the same conversation; keep the Study pane as it is.
    if (conversationPath === "/c/new" && /^\/c\/[^/]+$/.test(location.pathname))
      conversationPath = location.pathname;
    if (location.pathname !== conversationPath) {
      close();
      panel?.remove();
      panel = frame = launcher = workspaceId = activeUrl = pendingResult = null;
      resources.clear();
      seenResources.clear();
      expectedRequests.clear();
      rejectedResources.clear();
      conversationPath = location.pathname;
    }
    mountButton();
  }
  function show(source) {
    if (!panel) {
      panel = document.createElement("aside");
      panel.id = "tatc-study-pane";
      panel.setAttribute("aria-label", "TATC study workspace");
      const top = document.createElement("div");
      top.className = "tatc-pane-header";
      const label = document.createElement("span");
      label.className = "tatc-pane-title";
      label.setAttribute("role", "heading");
      label.setAttribute("aria-level", "1");
      label.textContent = "Study";
      const button = document.createElement("button");
      button.textContent = "×";
      button.setAttribute("aria-label", "Close study workspace");
      button.onclick = close;
      top.append(label, button);
      const handle = document.createElement("div");
      handle.className = "tatc-pane-resize";
      handle.tabIndex = 0;
      handle.setAttribute("role", "separator");
      handle.setAttribute("aria-orientation", "vertical");
      handle.setAttribute("aria-label", "Resize study pane");
      handle.onpointerdown = (event) => {
        handle.setPointerCapture(event.pointerId);
        panel.classList.add("resizing");
      };
      handle.onpointermove = (event) => {
        if (handle.hasPointerCapture(event.pointerId))
          size(innerWidth - event.clientX);
      };
      handle.onpointerup = (event) => {
        handle.releasePointerCapture(event.pointerId);
        panel.classList.remove("resizing");
      };
      handle.onlostpointercapture = () => panel.classList.remove("resizing");
      handle.onkeydown = (event) => {
        if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
          event.preventDefault();
          size(width + (event.key === "ArrowLeft" ? 40 : -40));
        }
      };
      const empty = document.createElement("div");
      empty.className = "tatc-study-empty";
      const heading = document.createElement("h2");
      heading.textContent = "No study open";
      const hint = document.createElement("p");
      hint.textContent =
        "Ask in chat for a plot, coverage study, or orbit simulation. Your results will appear here.";
      empty.append(heading, hint);
      panel.append(handle, top, empty);
      document.body.append(panel);
      // Toggling the sidebar resizes the chat without a window resize event.
      const main = document.querySelector("#root main");
      if (main) new ResizeObserver(() => isOpen() && size(width)).observe(main);
    }
    if (
      !isOpen() ||
      !document.documentElement.classList.contains("tatc-pane-open")
    ) {
      previousFocus = source;
      panel.hidden = false;
      document.documentElement.classList.add("tatc-pane-open");
      studyButton?.setAttribute("aria-expanded", "true");
      size(width);
      notifyVisibility();
      panel.querySelector("button").focus();
    }
  }
  function loadResult(source, payload) {
    pendingResult = null;
    launcher = source;
    if (activeUrl === payload.url) return;
    panel.querySelector(".tatc-pane-title").textContent = payload.title;
    workspaceId = payload.workspaceId;
    activeUrl = payload.url;
    // A new fragment alone does not reload an iframe. Replace the browsing
    // context so each returned study is decoded and initialized exactly once.
    const next = document.createElement("iframe");
    next.id = "tatc-study-frame";
    next.name = "tatc-study-frame";
    next.title = "TATC study workspace";
    next.setAttribute(
      "sandbox",
      "allow-scripts allow-same-origin allow-downloads allow-forms",
    );
    next.referrerPolicy = "no-referrer";
    next.onload = notifyVisibility;
    next.src = payload.url;
    if (frame) frame.replaceWith(next);
    else panel.querySelector(".tatc-study-empty").replaceWith(next);
    frame = next;
  }
  function open(source, payload) {
    show(source);
    loadResult(source, payload);
  }
  addEventListener(
    "message",
    (event) => {
      checkConversation();
      if (event.origin !== origin) return;
      const message = event.data;
      if (!message || typeof message !== "object") return;
      if (
        frame &&
        event.source === frame.contentWindow &&
        message.type === "tatc-workspace:ready" &&
        message.payload?.workspaceId === workspaceId
      ) {
        event.stopImmediatePropagation();
        notifyVisibility();
        return;
      }
      if (
        frame &&
        event.source === frame.contentWindow &&
        message.type === "tatc-workspace:run"
      ) {
        const payload = message.payload;
        if (
          payload?.workspaceId !== workspaceId ||
          payload.params?.workspace_id !== workspaceId
        )
          return;
        event.stopImmediatePropagation();
        if (!launcher?.isConnected) {
          frame.contentWindow.postMessage(
            {
              type: "tatc-workspace:submission-error",
              payload: { workspaceId },
            },
            origin,
          );
          return;
        }
        // The resource renderer owns the authenticated chat callback. Relay the
        // intent through its original launcher; do not create an open compute API.
        expectedRequests.set(workspaceId, requestKey(payload.params));
        launcher.contentWindow.postMessage(
          { type: "tatc-workspace:rerun", payload },
          origin,
        );
        return;
      }
      if (
        [
          "tatc-workspace:submitted",
          "tatc-workspace:submission-error",
        ].includes(message.type) &&
        launcher &&
        event.source === launcher.contentWindow &&
        message.payload?.workspaceId === workspaceId
      ) {
        event.stopImmediatePropagation();
        frame?.contentWindow.postMessage(message, origin);
        return;
      }
      if (
        !["tatc-workspace:hello", "tatc-workspace:open"].includes(
          message.type,
        ) ||
        !valid(message.payload)
      )
        return;
      const source = resourceFrame(event.source);
      if (!source) return;
      event.stopImmediatePropagation();
      const alreadySeen = seenResources.has(message.payload.url);
      seenResources.add(message.payload.url);
      const expected = expectedRequests.get(message.payload.workspaceId);
      if (
        !alreadySeen &&
        expected &&
        requestKey(message.payload.request) !== expected
      )
        rejectResult(message.payload.url, message.payload.workspaceId, source);
      if (message.type === "tatc-workspace:hello") {
        source.contentWindow.postMessage(
          { type: "tatc-workspace:available" },
          origin,
        );
        if (rejectedResources.has(message.payload.url)) return;
        resources.set(message.payload.url, {
          source,
          payload: message.payload,
        });
        if (pendingResult?.payload.url === message.payload.url)
          pendingResult = { source, payload: message.payload };
        if (message.payload.url === activeUrl) {
          // LibreChat can remount a message while a response streams. Rebind
          // its callback without resetting the open study or playback.
          if (!launcher?.isConnected) launcher = source;
        } else if (!alreadySeen && latestResult()?.source === source) {
          // Keep a closed pane closed. Defer loading the new result until it is
          // opened, so a hidden spatial result cannot initialize Cesium.
          pendingResult = { source, payload: message.payload };
          if (isOpen()) loadResult(source, message.payload);
        }
      } else if (rejectedResources.has(message.payload.url)) {
        rejectResult(message.payload.url, message.payload.workspaceId, source);
      } else open(source, message.payload);
    },
    { capture: true },
  );
  addEventListener("resize", () => {
    if (panel) size(width);
  });
  addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close();
  });
  addEventListener("popstate", checkConversation);
  // Resource nodes also disappear during message refreshes/virtualization.
  // Only a conversation change should close the pane. A returning historical
  // resource must not replace a newer calculation for the same workspace.
  new MutationObserver(checkConversation).observe(document.body, {
    childList: true,
    subtree: true,
  });
  checkConversation();
})();
