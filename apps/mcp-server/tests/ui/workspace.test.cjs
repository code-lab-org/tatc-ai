const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const source = fs.readFileSync(
  path.resolve(__dirname, "../../../librechat/assets/tatc-workspace.js"),
  "utf8",
);
const origin = "http://localhost:8000";
const workspaceId = "12345678-1234-4234-8234-123456789abc";
const request = {
  view: "coverage",
  workspace_id: workspaceId,
  presentation: "overview",
  scenario: {
    satellites: [
      {
        name: "A",
        orbit: { inclination: 97.5 },
        instruments: [{ field_of_regard: 120 }],
      },
    ],
    points: [{ name: "Phoenix", latitude: 33.4484 }],
    start_time: "2026-10-07T00:00:00Z",
    end_time: "2026-10-08T00:00:00Z",
  },
};
function setup({ initial = true, url = "http://localhost:3080/c/study" } = {}) {
  const dom = new JSDOM(
    '<body><div id="root"><main><div class="absolute top-0"><div class="hide-scrollbar"></div></div></main></div><script data-viewer-origin="http://localhost:8000"></script></body>',
    { url, runScripts: "outside-only" },
  );
  const w = dom.window,
    d = w.document;
  const observers = [],
    NativeObserver = w.MutationObserver;
  w.MutationObserver = class extends NativeObserver {
    constructor(callback) {
      super(callback);
      observers.push(this);
    }
  };
  const resized = [];
  w.ResizeObserver = class {
    constructor(callback) {
      resized.push(callback);
    }
    observe() {}
  };
  Object.defineProperty(d, "currentScript", {
    value: d.querySelector("script"),
  });
  w.eval(source);
  const send = (source, type, payload) =>
    w.dispatchEvent(
      new w.MessageEvent("message", {
        source,
        origin,
        data: { type, payload },
      }),
    );
  function resource(id, actual = request, before = null) {
    const el = d.createElement("iframe");
    el.src = origin + "/visualization/v1/launch#v1." + id;
    d.body.insertBefore(el, before);
    const payload = {
      workspaceId,
      title: "Coverage study",
      url: origin + "/visualization/v1/#v1." + id,
      request: actual,
    };
    send(el.contentWindow, "tatc-workspace:hello", payload);
    return {
      el,
      payload,
      open: () => send(el.contentWindow, "tatc-workspace:open", payload),
    };
  }
  const baseline = initial && resource("baseline");
  if (baseline) baseline.open();
  const messages = [];
  const frame = () => d.getElementById("tatc-study-frame");
  if (frame())
    frame().contentWindow.postMessage = (message) => messages.push(message);
  const submit = (params = request) =>
    send(frame().contentWindow, "tatc-workspace:run", { workspaceId, params });
  return {
    dom,
    w,
    d,
    baseline,
    resource,
    submit,
    frame,
    messages,
    resized,
    close: () => {
      observers.forEach((o) => o.disconnect());
      dom.window.close();
    },
  };
}
test("altered or missing returned inputs cannot replace a submitted study", () => {
  const s = setup();
  try {
    s.submit();
    for (const change of [
      (r) => r.scenario.satellites.push({ name: "Unrequested satellite" }),
      (r) => (r.scenario.satellites[0].instruments[0].field_of_regard = 100),
      (r) => (r.scenario.start_time = "2026-10-07T01:00:00Z"),
      (r) => (r.scenario.points[0].latitude = 34),
      (r) => (r.view = "example"),
    ]) {
      const altered = structuredClone(request);
      change(altered);
      const bad = s.resource("bad" + s.messages.length, altered);
      bad.open();
      assert.equal(s.frame().src, s.baseline.payload.url);
      assert.equal(s.messages.at(-1).type, "tatc-workspace:result-mismatch");
    }
    s.resource("missing", null);
    assert.equal(s.frame().src, s.baseline.payload.url);
    // An exact response can still arrive after a rejected response. Key order
    // is immaterial, but values and satellite/point order are preserved.
    const exact = Object.fromEntries(Object.entries(request).reverse());
    const accepted = s.resource("accepted", exact);
    assert.equal(s.frame().src, accepted.payload.url);
    s.resource("latebad", { ...request, presentation: "table" });
    assert.equal(s.frame().src, accepted.payload.url);
  } finally {
    s.close();
  }
});
test("closing the pane does not allow a mismatched pending result to open", () => {
  const s = setup();
  try {
    s.submit();
    s.d.querySelector('[aria-label="Close study workspace"]').click();
    const bad = s.resource("closedbad", { ...request, view: "example" });
    bad.open();
    assert.equal(s.d.getElementById("tatc-study-pane").hidden, true);
    assert.equal(s.frame().src, s.baseline.payload.url);
    const good = s.resource("closedgood");
    good.open();
    assert.equal(s.frame().src, good.payload.url);
  } finally {
    s.close();
  }
});

test("Study opens empty without a viewer, then preserves a result across close/reopen", () => {
  const s = setup({ initial: false });
  try {
    const button = s.d.getElementById("tatc-study-toggle");
    assert.ok(button);
    assert.equal(s.d.getElementById("tatc-study-pane"), null);
    button.click();
    assert.equal(
      s.frame(),
      null,
      "Empty Study must not fetch a viewer or Cesium",
    );
    assert.match(
      s.d.querySelector(".tatc-study-empty").textContent,
      /No study open/,
    );
    assert.equal(button.getAttribute("aria-expanded"), "true");
    const result = s.resource("newresult");
    const original = s.frame();
    assert.equal(original.src, result.payload.url);
    button.click();
    assert.equal(s.d.getElementById("tatc-study-pane").hidden, true);
    button.click();
    assert.equal(
      s.frame(),
      original,
      "Reopening must preserve the browsing context",
    );
    result.open();
    assert.equal(
      s.frame(),
      original,
      "The current resource link must also preserve edits",
    );
    assert.equal(button.getAttribute("aria-expanded"), "true");
  } finally {
    s.close();
  }
});

test("historical resources stay closed, and new results wait for reopening", () => {
  const s = setup({ initial: false });
  try {
    const first = s.resource("first");
    const newest = s.resource("newest");
    s.resource("late-history", request, first.el);
    assert.equal(s.frame(), null);
    s.d.getElementById("tatc-study-toggle").click();
    assert.equal(s.frame().src, newest.payload.url);
    s.d.querySelector('[aria-label="Close study workspace"]').click();
    const original = s.frame();
    const next = s.resource("next");
    next.el.remove();
    const remounted = s.resource("next");
    assert.equal(
      s.frame(),
      original,
      "No hidden result should initialize its viewer",
    );
    assert.equal(s.d.getElementById("tatc-study-pane").hidden, true);
    s.d.getElementById("tatc-study-toggle").click();
    assert.equal(s.frame().src, next.payload.url);
    const relayed = [];
    remounted.el.contentWindow.postMessage = (message) => relayed.push(message);
    s.submit();
    assert.equal(relayed.at(-1).type, "tatc-workspace:rerun");
    s.w.history.pushState({}, "", "/c/different");
    s.w.dispatchEvent(new s.w.PopStateEvent("popstate"));
    assert.equal(s.frame(), null);
    s.d.getElementById("tatc-study-toggle").click();
    assert.equal(s.frame(), null, "Conversations must not share a study");
    s.w.history.pushState({}, "", "/login");
    s.w.dispatchEvent(new s.w.PopStateEvent("popstate"));
    assert.equal(s.d.getElementById("tatc-study-toggle"), null);
    assert.equal(s.d.getElementById("tatc-study-pane"), null);
  } finally {
    s.close();
  }
});
test("a newer submission supersedes a delayed response and history remains available", () => {
  const s = setup();
  try {
    s.submit();
    const next = structuredClone(request);
    next.scenario.satellites[0].instruments[0].field_of_regard = 80;
    s.submit(next);
    s.resource("delayed", request);
    assert.equal(s.frame().src, s.baseline.payload.url);
    const good = s.resource("latest", next);
    assert.equal(s.frame().src, good.payload.url);
    s.baseline.open();
    assert.equal(s.frame().src, s.baseline.payload.url);
  } finally {
    s.close();
  }
});

test("a new chat keeps its open Study pane when LibreChat assigns its ID", () => {
  const s = setup({ initial: false, url: "http://localhost:3080/c/new" });
  try {
    s.d.getElementById("tatc-study-toggle").click();
    s.w.history.replaceState({}, "", "/c/assigned-id");
    s.w.dispatchEvent(new s.w.PopStateEvent("popstate"));
    assert.equal(s.d.getElementById("tatc-study-pane").hidden, false);
    const result = s.resource("first");
    assert.equal(s.frame().src, result.payload.url);
    s.w.history.pushState({}, "", "/c/other");
    s.w.dispatchEvent(new s.w.PopStateEvent("popstate"));
    assert.equal(s.d.getElementById("tatc-study-pane"), null);
    s.d.getElementById("tatc-study-toggle").click();
    assert.equal(s.frame(), null, "Conversations must not share a study");
  } finally {
    s.close();
  }
});

test("an open sidebar narrows the pane so the chat stays visible", () => {
  const s = setup({ initial: false });
  try {
    const main = s.d.querySelector("#root main");
    let sidebar = 455;
    main.getBoundingClientRect = () => ({ left: sidebar });
    Object.defineProperty(s.w, "innerWidth", { value: 1030 });
    const paneWidth = () =>
      s.d.documentElement.style.getPropertyValue("--tatc-pane-width");
    s.d.getElementById("tatc-study-toggle").click();
    assert.equal(paneWidth(), "380px");
    sidebar = 0;
    s.resized.forEach((callback) => callback());
    assert.equal(paneWidth(), "650px", "the chosen width returns");
  } finally {
    s.close();
  }
});
