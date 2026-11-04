(async () => {
  const data = await window.tatcStudy;
  const link = document.getElementById("open-study");
  const url = new URL("./", location.href);
  url.hash = location.hash;
  link.href = url.href;
  const payload = {
    url: url.href,
    workspaceId: data.ui?.workspace_id,
    // The calculated request, not a model-authored description of the result.
    request: data.request
      ? {
          ...data.request,
          presentation: data.ui.presentation,
          workspace_id: data.ui.workspace_id,
        }
      : null,
    title:
      data.dataset?.title ||
      (data.points?.length ? "Coverage study" : "Orbit study"),
  };
  document.getElementById("launch-detail").textContent = payload.title;
  let rejected = false,
    paneAvailable = false,
    pendingMessage;
  parent.postMessage({ type: "tatc-workspace:hello", payload }, "*");
  parent.postMessage({ type: "ui-size-change", payload: { height: 72 } }, "*");
  addEventListener("message", (event) => {
    if (event.source !== parent) return;
    if (
      event.data?.type === "tatc-workspace:result-mismatch" &&
      event.data.payload?.workspaceId === payload.workspaceId
    ) {
      rejected = true;
      link.removeAttribute("href");
      link.textContent = "Study inputs did not match";
      document.getElementById("launch-detail").textContent =
        "Result not applied";
      document.getElementById("launch-detail").setAttribute("role", "alert");
    }
    if (event.data?.type === "tatc-workspace:available") {
      paneAvailable = true;
      if (!rejected) link.textContent = "Open study pane →";
    }
    if (pendingMessage && event.data?.messageId === pendingMessage) {
      if (event.data.type === "ui-message-received")
        parent.postMessage(
          {
            type: "tatc-workspace:submitted",
            payload: { workspaceId: payload.workspaceId },
          },
          "*",
        );
      if (event.data.type === "ui-message-response") {
        if (event.data.payload?.error)
          parent.postMessage(
            {
              type: "tatc-workspace:submission-error",
              payload: { workspaceId: payload.workspaceId },
            },
            "*",
          );
        pendingMessage = null;
      }
    }
    if (
      event.data?.type === "tatc-workspace:rerun" &&
      event.data.payload?.workspaceId === payload.workspaceId
    ) {
      pendingMessage = crypto.randomUUID();
      parent.postMessage(
        {
          messageId: pendingMessage,
          type: "intent",
          payload: {
            intent: "rerun_tatc_study",
            params: event.data.payload.params,
          },
        },
        "*",
      );
    }
  });
  link.onclick = (event) => {
    if (rejected) {
      event.preventDefault();
      return;
    }
    if (paneAvailable) {
      event.preventDefault();
      parent.postMessage({ type: "tatc-workspace:open", payload }, "*");
    }
  };
})().catch((error) => {
  document.getElementById("launch-detail").textContent = error.message;
});
