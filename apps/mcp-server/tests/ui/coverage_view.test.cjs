// Real Cesium entity/clock APIs; only WebGL rendering and asset loading are stubbed.
// This does not replace a browser/WebGL visual check.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { JSDOM } = require("jsdom");
const Cesium = require("cesium");
const root = path.resolve(__dirname, "../../../..");
const fs = require("node:fs");
const { gzipSync } = require("node:zlib");
const fixture = JSON.parse(
  execFileSync(
    process.env.TATC_PYTHON || "python3",
    [
      "-c",
      [
        "from pathlib import Path",
        "from src.coverage_analysis import CoverageRequest, analyze",
        "import json",
        "request = CoverageRequest.model_validate_json(Path('apps/mcp-server/src/coverage-scenario.json').read_text())",
        "print(json.dumps(analyze(request, include_tracks=True)))",
      ].join("\n"),
    ],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: path.join(root, "apps/mcp-server") },
      encoding: "utf8",
    },
  ),
);
// A real pointed study: TATC gives one valid epoch inside a wider envelope.
const pointedFixture = JSON.parse(
  execFileSync(
    process.env.TATC_PYTHON || "python3",
    [
      "-c",
      [
        "from src.coverage_analysis import CoverageRequest, analyze",
        "import json",
        "request = CoverageRequest.model_validate({'satellites': [{'name': 'S', 'orbit': {'type': 'circular', 'mean_altitude': 550000, 'inclination': 97.5, 'right_ascension_ascending_node': 0, 'true_anomaly': 0, 'epoch': '2026-10-07T00:00:00Z'}, 'instruments': [{'name': 'Pointed', 'field_of_regard': 100, 'cross_track_field_of_view': 100, 'along_track_field_of_view': 2}]}], 'points': [{'name': 'P', 'latitude': 45, 'longitude': -100}], 'start_time': '2026-10-07T00:00:00Z', 'end_time': '2026-10-07T12:00:00Z'})",
        "print(json.dumps(analyze(request, include_tracks=True)))",
      ].join("\n"),
    ],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: path.join(root, "apps/mcp-server") },
      encoding: "utf8",
    },
  ),
);
const asPointedStudy = (data) => Object.assign(data, pointedFixture);
const conicalFixture = JSON.parse(
  execFileSync(
    process.env.TATC_PYTHON || "python3",
    [
      "-c",
      [
        "from src.coverage_analysis import CoverageRequest, analyze",
        "import json",
        "request = CoverageRequest.model_validate({'satellites': [{'name': 'S', 'orbit': {'type': 'circular', 'mean_altitude': 550000, 'inclination': 97.5, 'right_ascension_ascending_node': 0, 'true_anomaly': 0, 'epoch': '2026-10-07T00:00:00Z'}, 'instruments': [{'name': 'Conical', 'cone_angle': 47.5, 'along_track_field_of_view': 1}]}], 'points': [{'name': 'P', 'latitude': 45, 'longitude': -100}], 'start_time': '2026-10-07T00:00:00Z', 'end_time': '2026-10-07T12:00:00Z'})",
        "print(json.dumps(analyze(request, include_tracks=True)))",
      ].join("\n"),
    ],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: path.join(root, "apps/mcp-server") },
      encoding: "utf8",
    },
  ),
);
const html = fs.readFileSync(
  path.join(root, "apps/mcp-server/src/coverage_view.html"),
  "utf8",
);
const code = fs.readFileSync(
  path.join(root, "apps/mcp-server/src/coverage_view.js"),
  "utf8",
);

async function setup({
  failure,
  orbitOnly = false,
  presentation = "simulation3d",
  dataset,
  editFixture,
  manualClock = false,
  beforeAssetsLoaded,
} = {}) {
  const data = structuredClone(fixture);
  if (orbitOnly) data.points = [];
  if (dataset) {
    data.dataset = dataset;
    data.points = [];
  }
  data.ui = {
    presentation,
    workspace_id: "12345678-1234-4234-8234-123456789abc",
  };
  data.request = {
    view: "coverage",
    scenario: JSON.parse(
      fs.readFileSync(
        path.join(root, "apps/mcp-server/src/coverage-scenario.json"),
        "utf8",
      ),
    ),
  };
  editFixture?.(data);
  const fragment = gzipSync(JSON.stringify(data)).toString("base64url");
  const dom = new JSDOM(html, {
    runScripts: "outside-only",
    pretendToBeVisual: true,
    url: "http://localhost/visualization/v1/#v1." + fragment,
  });
  const w = dom.window,
    get = (id) => w.document.getElementById(id);
  Object.assign(w, { Blob, DecompressionStream, Uint8Array, structuredClone });
  const timers = new Map();
  let now = 0,
    timerId = 0;
  if (manualClock) {
    w.performance.now = () => now;
    w.setTimeout = (callback, delay) => {
      timers.set(++timerId, { callback, at: now + delay });
      return timerId;
    };
    w.clearTimeout = (id) => timers.delete(id);
  }
  let rendered;
  let notifyResize;
  const sizes = [];
  // JSDOM has no layout/ResizeObserver. Exercise the host message contract here;
  // the browser tests measure the real content and iframe dimensions.
  w.ResizeObserver = class {
    constructor(callback) {
      this.callback = callback;
    }
    observe(target) {
      if (target === w.document.body) notifyResize = this.callback;
    }
    disconnect() {}
  };
  let contentHeight = 40;
  w.document.body.getBoundingClientRect = () => ({ height: contentHeight });
  w.postMessage = (message) => sizes.push(message);
  // Real browsers have one Date realm; Cesium checks instanceof Date.
  w.Date = Date;
  const imagery = {
    errorEvent: new Cesium.Event(),
    rectangle: Cesium.Rectangle.MAX_VALUE,
    tilingScheme: new Cesium.GeographicTilingScheme(),
    minimumLevel: 0,
    maximumLevel: 3,
  };
  class Viewer {
    constructor(id, options) {
      assert.equal(
        get("visual").hidden,
        false,
        "Cesium needs a visible layout box at construction",
      );
      assert.equal(options.geocoder, false);
      assert.ok(options.baseLayer instanceof Cesium.ImageryLayer);
      assert.equal(options.baseLayerPicker, false);
      assert.equal(options.shouldAnimate, false);
      assert.equal(options.requestRenderMode, true);
      this.clock = new Cesium.Clock({ shouldAnimate: false });
      this.entities = new Cesium.EntityCollection();
      this.scene = {
        renderError: new Cesium.Event(),
        requestRender: () => {},
        mode: Cesium.SceneMode.SCENE3D,
        globe: { tilesLoaded: failure !== "stalled" },
        postRender: new Cesium.Event(),
      };
      queueMicrotask(() => this.scene.postRender.raiseEvent());
      this.camera = {
        frustum: { fovy: Math.PI / 3, aspectRatio: 2 },
        setView(options) {
          this.lastView = options;
        },
      };
      this.selectedEntityChanged = new Cesium.Event();
      this.destroyed = false;
      rendered = this;
    }
    resize() {}
    isDestroyed() {
      return this.destroyed;
    }
    destroy() {
      this.destroyed = true;
    }
  }
  w.Cesium = {
    ...Cesium,
    Viewer,
    TileMapServiceImageryProvider: {
      fromUrl: () =>
        failure === "imagery"
          ? Promise.reject(new Error("Imagery unavailable"))
          : Promise.resolve(imagery),
    },
    buildModuleUrl: (value) => value,
  };
  for (const name of ["scene_data.js", "research_view.js"])
    w.eval(
      fs.readFileSync(path.join(root, "apps/mcp-server/src", name), "utf8"),
    );
  w.eval(code);
  for (
    let i = 0;
    i < 100 &&
    ![...w.document.scripts].some((s) => s.src.endsWith("/Cesium.js"));
    i++
  )
    await new Promise(setImmediate);
  const script = [...w.document.scripts].find((s) =>
    s.src.endsWith("/Cesium.js"),
  );
  const style = w.document.querySelector('link[href$="Widgets/widgets.css"]');
  beforeAssetsLoaded?.(w, data);
  if (script) {
    if (failure === "script") script.onerror();
    else script.onload();
    if (failure === "style") style.onerror();
    else style.onload();
  }
  await new Promise(setImmediate);
  return {
    dom,
    w,
    data,
    get,
    get viewer() {
      return rendered;
    },
    imagery,
    sizes,
    advance: (ms) => {
      now += ms;
      for (const [id, timer] of timers) {
        if (timer.at <= now) {
          timers.delete(id);
          timer.callback();
        }
      }
    },
    resize: (height) => {
      contentHeight = height;
      notifyResize();
    },
  };
}

test("iframe sizing follows content changes and avoids repeating identical sizes", async () => {
  const { dom, sizes, resize } = await setup();
  try {
    resize(540);
    resize(540);
    resize(600);
    resize(40);
    assert.deepEqual(
      sizes
        .filter((m) => m.type === "ui-size-change")
        .map((m) => [m.type, m.payload.height]),
      [
        ["ui-size-change", 40],
        ["ui-size-change", 540],
        ["ui-size-change", 600],
        ["ui-size-change", 40],
      ],
    );
  } finally {
    dom.window.close();
  }
});

test("globe loading deadline excludes time in another study tab", async () => {
  const { dom, w, get, advance } = await setup({
    failure: "stalled",
    manualClock: true,
  });
  try {
    advance(10000);
    w.document.querySelector('[data-tab="table"]').click();
    advance(60000);
    await new Promise(setImmediate);
    assert.doesNotMatch(get("status").textContent, /timed out/);
    w.document.querySelector('[data-tab="map"]').click();
    advance(19000);
    await new Promise(setImmediate);
    assert.doesNotMatch(get("status").textContent, /timed out/);
    advance(1001);
    await new Promise(setImmediate);
    assert.match(get("status").textContent, /timed out/);
  } finally {
    dom.window.close();
  }
});

test("background time preserves the remaining load budget and completion cancels it", async () => {
  const { dom, w, get, viewer, advance } = await setup({
    failure: "stalled",
    manualClock: true,
  });
  try {
    advance(12000);
    Object.defineProperty(w.document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    w.document.dispatchEvent(new w.Event("visibilitychange"));
    advance(60000);
    await new Promise(setImmediate);
    assert.doesNotMatch(get("status").textContent, /timed out/);
    Object.defineProperty(w.document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    w.document.dispatchEvent(new w.Event("visibilitychange"));
    advance(17000);
    viewer.scene.globe.tilesLoaded = true;
    viewer.scene.postRender.raiseEvent();
    await new Promise(setImmediate);
    assert.equal(get("status").hidden, true);
    advance(60000);
    await new Promise(setImmediate);
    assert.equal(viewer.isDestroyed(), false);
    assert.equal(get("status").hidden, true);
    assert.equal(viewer.scene.postRender.numberOfListeners, 0);
  } finally {
    dom.window.close();
  }
});

for (const failure of ["script", "style", "imagery"]) {
  test(`${failure} failure reports an error without substituting a view`, async () => {
    const { dom, get, viewer } = await setup({ failure });
    try {
      assert.equal(viewer, undefined);
      assert.equal(get("visual").hidden, false);
      assert.equal(get("globe").hidden, true);
      assert.equal(get("status").hidden, false);
      assert.match(get("status").textContent, /Unable to show visualization/);
      assert.equal(get("play").disabled, false);
      assert.equal(get("raw-results"), null);
    } finally {
      dom.window.close();
    }
  });
}

test("timeline drives the clock and links follow computed observation intervals", async () => {
  const { dom, w, data, get, viewer } = await setup();
  try {
    assert.ok(viewer, get("status").textContent);
    assert.equal(get("visual").hidden, false);
    assert.equal(get("play").textContent, "Play");
    assert.equal(viewer.clock.shouldAnimate, false);
    const observation = data.points[0].observations[0];
    const button = [...w.document.querySelectorAll(".event")].find((b) =>
      b.title.startsWith(
        observation.satellite + " / " + observation.instrument + ":",
      ),
    );
    button.click();
    const expected =
      (Date.parse(observation.start) + Date.parse(observation.end)) / 2;
    assert.ok(
      Math.abs(
        Cesium.JulianDate.toDate(viewer.clock.currentTime).getTime() - expected,
      ) < 2,
    );
    const link = viewer.entities.values.find(
      (e) =>
        e.polyline?.show?.getValue(viewer.clock.currentTime) &&
        !e.polyline.clampToGround,
    );
    assert.ok(link);
    assert.equal(
      link.polyline.positions.getValue(viewer.clock.currentTime).length,
      2,
    );
    const satellite = viewer.entities.values.find(
      (e) => e.name === observation.satellite,
    );
    assert.ok(
      Cesium.Cartographic.fromCartesian(
        satellite.position.getValue(viewer.clock.currentTime),
      ).height > 400000,
    );
    const ground = viewer.entities.values.find((e) =>
      e.polyline?.clampToGround?.getValue(viewer.clock.currentTime),
    );
    assert.ok(
      ground.polyline.positions.getValue(viewer.clock.currentTime).length > 1,
    );
    get("show-links").checked = false;
    assert.equal(link.polyline.show.getValue(viewer.clock.currentTime), false);
    get("show-orbits").checked = false;
    get("show-orbits").dispatchEvent(new w.Event("change"));
    assert.equal(satellite.path.show.getValue(viewer.clock.currentTime), false);
    viewer.selectedEntityChanged.raiseEvent(
      viewer.entities.getById("study-point-2"),
    );
    assert.equal(get("location").value, "2");
    assert.match(
      get("metrics").textContent,
      new RegExp(data.points[2].metrics.observation_count + " opportunities"),
    );
    get("show-links").checked = true;
    w.document.querySelector(".event.gap").click();
    assert.match(get("event-detail").textContent, /^Gap:/);
    assert.equal(link.polyline.show.getValue(viewer.clock.currentTime), false);
    get("play").click();
    assert.equal(get("play").textContent, "Pause");
    get("time-slider").value = 50;
    get("time-slider").dispatchEvent(new w.Event("input"));
    assert.equal(get("play").textContent, "Play");
    assert.equal(get("download-results"), null);
  } finally {
    dom.window.close();
  }
});

test("orbit-only view has no sensor controls and starts paused", async () => {
  const { dom, get, viewer } = await setup({ orbitOnly: true });
  try {
    assert.ok(viewer, get("status").textContent);
    assert.equal(get("coverage").hidden, true);
    assert.equal(get("links-label").hidden, true);
    assert.equal(get("play").textContent, "Play");
    assert.ok(viewer.entities.values.some((e) => e.path));
  } finally {
    dom.window.close();
  }
});

test("orbit-only methods show each satellite's TLE epoch", async () => {
  const { dom, get } = await setup({
    orbitOnly: true,
    editFixture: (d) => {
      delete d.assumptions;
      d.scenario.source = "CelesTrak TLEs propagated with TATC";
      d.scenario.satellites = [
        { name: "ISS", norad_id: "25544", orbit_epoch: "2021-06-05T07:19:36Z" },
      ];
      d.request = { view: "orbit", satellite_identifiers: ["25544"] };
    },
  });
  try {
    assert.match(
      get("provenance").textContent,
      /ISS \(NORAD 25544\): TLE epoch 2021-06-05T07:19:36Z/,
    );
  } finally {
    dom.window.close();
  }
});

test("choosing another location frames it on the map", async () => {
  const { dom, get, viewer, data } = await setup();
  try {
    get("location").value = "1";
    get("location").dispatchEvent(new dom.window.Event("change"));
    const destination = Cesium.Cartographic.fromCartesian(
      viewer.camera.lastView.destination,
    );
    assert.ok(
      Math.abs(
        Cesium.Math.toDegrees(destination.longitude) - data.points[1].longitude,
      ) < 1e-6,
    );
  } finally {
    dom.window.close();
  }
});

test("closing during asset loading waits for the pane before constructing the globe", async () => {
  const s = await setup({
    beforeAssetsLoaded(w, data) {
      w.dispatchEvent(
        new w.MessageEvent("message", {
          source: w.parent,
          data: {
            type: "tatc-workspace:visibility",
            payload: { workspaceId: data.ui.workspace_id, visible: false },
          },
        }),
      );
    },
  });
  try {
    assert.equal(s.viewer, undefined);
    s.w.dispatchEvent(
      new s.w.MessageEvent("message", {
        source: s.w.parent,
        data: {
          type: "tatc-workspace:visibility",
          payload: { workspaceId: s.data.ui.workspace_id, visible: true },
        },
      }),
    );
    await new Promise(setImmediate);
    assert.ok(s.viewer);
    assert.equal(s.get("status").hidden, true);
  } finally {
    s.dom.window.close();
  }
});

test("hiding the study pauses playback and rendering without resetting the clock or camera", async () => {
  const { dom, w, data, get, viewer } = await setup();
  try {
    const visibility = (
      visible,
      workspaceId = data.ui.workspace_id,
      source = w.parent,
    ) =>
      w.dispatchEvent(
        new w.MessageEvent("message", {
          source,
          data: {
            type: "tatc-workspace:visibility",
            payload: { workspaceId, visible },
          },
        }),
      );
    get("time-slider").value = 1234;
    get("time-slider").dispatchEvent(new w.Event("input"));
    const position = viewer.camera.lastView;
    get("play").click();
    visibility(false, "wrong-workspace");
    visibility(false, data.ui.workspace_id, null);
    assert.equal(get("play").textContent, "Pause");
    visibility(false);
    assert.equal(get("play").textContent, "Play");
    assert.equal(viewer.useDefaultRenderLoop, false);
    assert.equal(viewer.isDestroyed(), false);
    visibility(true);
    assert.equal(viewer.useDefaultRenderLoop, true);
    assert.equal(get("play").textContent, "Play");
    assert.equal(get("time-slider").value, "1234");
    assert.equal(viewer.camera.lastView, position);
  } finally {
    dom.window.close();
  }
});

for (const failure of ["render", "tile"]) {
  test(`${failure} failure stops playback and removes the broken view`, async () => {
    const { dom, get, viewer, imagery } = await setup();
    try {
      get("play").click();
      (failure === "render"
        ? viewer.scene.renderError
        : imagery.errorEvent
      ).raiseEvent(new Error("Failure"));
      assert.equal(viewer.destroyed, true);
      assert.equal(get("visual").hidden, false);
      assert.equal(get("globe").hidden, true);
      assert.equal(get("play").textContent, "Play");
      assert.equal(get("play").disabled, false);
    } finally {
      dom.window.close();
    }
  });
}

test("coverage results work without loading Cesium and show computed values", async () => {
  const { dom, w, data, get, viewer } = await setup({
    presentation: "overview",
  });
  try {
    assert.equal(viewer, undefined);
    assert.equal(w.document.querySelector('script[src$="Cesium.js"]'), null);
    assert.equal(
      get("research-error").hidden,
      true,
      get("research-error").textContent,
    );
    assert.equal(
      get("comparison-chart").querySelectorAll(".bar-row").length,
      3,
    );
    assert.deepEqual(
      [
        ...get("comparison-chart").querySelectorAll(".comparison-ticks span"),
      ].map((e) => e.textContent),
      ["0", "250", "500", "750", "1,000"],
    );
    get("comparison-chart").querySelectorAll("button")[1].click();
    assert.equal(get("location").value, "1");
    assert.match(get("metrics-grid").textContent, /Svalbard/);
    w.document.querySelector('[data-tab="table"]').click();
    assert.equal(get("result-table").querySelectorAll("tbody tr").length, 3);
    get("table-kind").value = "gaps";
    get("table-kind").dispatchEvent(new w.Event("change"));
    get("result-table").querySelector("tbody tr").click();
    const gap = data.points[1].gaps[0];
    assert.equal(
      Number(get("time-slider").value),
      ((Date.parse(gap.start) + Date.parse(gap.end)) / 2 -
        Date.parse(data.scenario.start_time)) /
        1000,
    );
  } finally {
    dom.window.close();
  }
});

test("numeric series keep missing and negative values without any orbital scene", async () => {
  const dataset = {
    title: "Research comparison",
    source: "Supplied test values",
    labels: ["A", "B", "C"],
    series: [{ name: "Value", values: [-2, null, 3] }],
    kind: "bar",
    unit: "km",
    x_label: "Location",
  };
  const { dom, w, get, viewer } = await setup({
    presentation: "overview",
    dataset,
  });
  try {
    assert.equal(viewer, undefined);
    assert.equal(
      get("research-error").hidden,
      true,
      get("research-error").textContent,
    );
    assert.deepEqual(
      [...get("comparison-chart").querySelectorAll(".plot-value")].map((e) =>
        e.getAttribute("data-value"),
      ),
      ["-2", "", "3"],
    );
    assert.equal(w.document.querySelector('[data-tab="map"]').hidden, true);
    w.document.querySelector('[data-tab="table"]').click();
    assert.equal(
      get("result-table").querySelectorAll("tbody tr")[1].lastChild.textContent,
      "—",
    );
  } finally {
    dom.window.close();
  }
});

test("editing a study sends an exact draft through chat and does not mutate results", async () => {
  const { dom, w, get, data, sizes } = await setup({
    presentation: "overview",
  });
  try {
    const original = get("metrics-grid").textContent;
    w.document.querySelector('[data-tab="inputs"]').click();
    const sensor = [...w.document.querySelectorAll("label")]
      .find((e) => e.textContent.includes("Field of regard"))
      .querySelector("input");
    sensor.value = "80";
    sensor.dispatchEvent(new w.Event("input"));
    const startField = [
      ...w.document.querySelectorAll('input[type="datetime-local"]'),
    ][0];
    startField.value = "2026-10-07T01:00";
    startField.dispatchEvent(new w.Event("input"));
    get("study-form").dispatchEvent(
      new w.Event("submit", { cancelable: true }),
    );
    const sent = sizes.find((m) => m.type === "tatc-workspace:run");
    assert.ok(sent);
    assert.equal(sent.payload.params.view, "coverage");
    assert.equal(
      sent.payload.params.scenario.satellites[0].instruments[0].field_of_regard,
      80,
    );
    assert.equal(sent.payload.params.workspace_id, data.ui.workspace_id);
    assert.equal(
      sent.payload.params.scenario.start_time,
      "2026-10-07T01:00:00Z",
    );
    assert.equal(
      sent.payload.params.scenario.satellites[0].orbit.true_anomaly,
      data.request.scenario.satellites[0].orbit.true_anomaly,
    );
    assert.equal(get("metrics-grid").textContent, original);
    w.dispatchEvent(
      new w.MessageEvent("message", {
        source: w.parent,
        data: {
          type: "tatc-workspace:result-mismatch",
          payload: { workspaceId: data.ui.workspace_id },
        },
      }),
    );
    assert.equal(get("study-notice").hidden, false);
    assert.match(get("study-notice").textContent, /different inputs/);
    assert.equal(get("metrics-grid").textContent, original);
    assert.equal(sensor.value, "80");
  } finally {
    dom.window.close();
  }
});

test("inputs retain altitude aliases and accept TATC sensor bounds", async () => {
  const { dom, w, get, sizes } = await setup({
    presentation: "overview",
    editFixture: (data) => {
      const orbit = data.request.scenario.satellites[0].orbit;
      orbit.altitude = orbit.mean_altitude;
      delete orbit.mean_altitude;
      delete orbit.inclination;
    },
  });
  try {
    const field = (label) =>
      [...w.document.querySelectorAll("label")]
        .find((e) => e.textContent.trim() === label)
        .querySelector("input");
    const altitude = field("Altitude (km)"),
      inclination = field("Inclination (°)"),
      sensor = [...w.document.querySelectorAll("label")]
        .find((e) => e.textContent.includes("Field of regard"))
        .querySelector("input");
    assert.equal(altitude.value, "550");
    assert.equal(inclination.value, "97.5");
    altitude.value = "600";
    altitude.dispatchEvent(new w.Event("input"));
    sensor.value = "270";
    sensor.dispatchEvent(new w.Event("input"));
    assert.equal(sensor.checkValidity(), true);
    inclination.value = "180";
    inclination.dispatchEvent(new w.Event("input"));
    assert.equal(inclination.checkValidity(), false);
    inclination.value = "90";
    inclination.dispatchEvent(new w.Event("input"));
    get("study-form").dispatchEvent(
      new w.Event("submit", { cancelable: true }),
    );
    const sent = sizes.find((m) => m.type === "tatc-workspace:run").payload
      .params.scenario.satellites[0];
    assert.equal(sent.orbit.altitude, 600000);
    assert.equal("mean_altitude" in sent.orbit, false);
    assert.equal(sent.instruments[0].field_of_regard, 270);
  } finally {
    dom.window.close();
  }
});

test("inputs edit pointing and access constraints of TATC instruments", async () => {
  const { dom, w, get, sizes } = await setup({
    presentation: "overview",
    editFixture: (data) => {
      Object.assign(data.request.scenario.satellites[0].instruments[0], {
        cross_track_field_of_view: 20,
        along_track_field_of_view: 5,
        roll_angle: 15,
        req_target_sunlit: true,
      });
      data.scenario.satellites[0].instruments[0].min_access_time = "PT1M30S";
    },
  });
  try {
    const control = (label, selector = "input") =>
      [...w.document.querySelectorAll("label")]
        .find((e) => e.textContent.startsWith(label))
        .querySelector(selector);
    const roll = control("Roll (°)"),
      view = control("Cross-track view (°)"),
      access = control("Minimum access (s)"),
      target = control("Target lighting", "select");
    assert.equal(roll.value, "15");
    assert.equal(control("Pitch (°)").value, "0");
    assert.equal(access.value, "90");
    assert.equal(target.selectedOptions[0].textContent, "Sunlit");
    // Basic instruments have no pointing fields.
    assert.equal(
      [...w.document.querySelectorAll("label")].filter((e) =>
        e.textContent.startsWith("Roll"),
      ).length,
      1,
    );
    roll.value = "-20";
    roll.dispatchEvent(new w.Event("input"));
    view.value = "0";
    view.dispatchEvent(new w.Event("input"));
    assert.equal(view.checkValidity(), false);
    view.value = "30";
    view.dispatchEvent(new w.Event("input"));
    access.value = "60";
    access.dispatchEvent(new w.Event("input"));
    target.value = "false";
    target.dispatchEvent(new w.Event("change"));
    control("Satellite lighting", "select").value = "true";
    control("Satellite lighting", "select").dispatchEvent(
      new w.Event("change"),
    );
    get("study-form").dispatchEvent(
      new w.Event("submit", { cancelable: true }),
    );
    const sent = sizes.find((m) => m.type === "tatc-workspace:run").payload
      .params.scenario.satellites[0].instruments[0];
    assert.equal(sent.roll_angle, -20);
    assert.equal(sent.cross_track_field_of_view, 30);
    assert.equal(sent.min_access_time, 60);
    assert.equal(sent.req_target_sunlit, false);
    assert.equal(sent.req_self_sunlit, true);
  } finally {
    dom.window.close();
  }
});

test("line charts preserve isolated measurements separated by missing values", async () => {
  const { dom, get } = await setup({
    presentation: "overview",
    dataset: {
      title: "Small measurements",
      source: "Supplied data",
      kind: "line",
      unit: "m",
      x_label: "Sample",
      labels: ["A", "B", "C"],
      series: [{ name: "Value", values: [0.0001, null, 0.0002] }],
    },
  });
  try {
    assert.equal(get("comparison-chart").querySelectorAll("circle").length, 2);
    assert.match(
      get("comparison-chart").querySelector("title").textContent,
      /0.0001 m/,
    );
    assert.equal(
      (
        get("comparison-chart")
          .querySelector("path")
          .getAttribute("d")
          .match(/M/g) || []
      ).length,
      2,
    );
  } finally {
    dom.window.close();
  }
});

test("supplied revisit values form a labelled chart with a shared zero baseline", async () => {
  const dataset = {
    title: "Revisit Times",
    unit: "hours",
    x_label: "Design",
    labels: ["Design A", "Design B"],
    series: [{ name: "Revisit Time", values: [3.5, 1.2] }],
    kind: "bar",
    source: "",
  };
  const { dom, w, get } = await setup({ presentation: "overview", dataset });
  try {
    const svg = get("comparison-chart").querySelector("svg");
    assert.ok(svg);
    assert.deepEqual(
      [...svg.querySelectorAll(".plot-axis-title")].map((e) => e.textContent),
      ["Revisit Time (hours)", "Design"],
    );
    assert.deepEqual(
      [...svg.querySelectorAll(".plot-tick")].map((e) => e.textContent),
      ["0", "1", "2", "3", "4"],
    );
    const bars = [...svg.querySelectorAll(".plot-bar")];
    const heights = bars.map((b) => Number(b.getAttribute("height")));
    assert.ok(Math.abs(heights[0] / heights[1] - 3.5 / 1.2) < 1e-9);
    const baseline = Number(svg.querySelector(".plot-zero").getAttribute("y1"));
    bars.forEach((b, i) =>
      assert.ok(
        Math.abs(Number(b.getAttribute("y")) + heights[i] - baseline) < 1e-9,
      ),
    );
    bars[1].dispatchEvent(new w.Event("focus"));
    assert.equal(
      get("comparison-chart").querySelector(".plot-inspection").textContent,
      "Design B · Revisit Time: 1.2 hours",
    );
    assert.equal(get("study-subtitle").hidden, true);
    assert.equal(get("metrics-grid").hidden, true);
    assert.equal(get("chart-note").hidden, true);
  } finally {
    dom.window.close();
  }
});

test("pointed studies show epochs and never present envelopes as coverage", async () => {
  const { dom, w, data, get } = await setup({
    presentation: "overview",
    editFixture: asPointedStudy,
  });
  try {
    assert.equal(get("research-error").hidden, true);
    assert.deepEqual(
      [...get("metric").options].map((o) => o.value),
      ["distinct_event_count", "mean_event_spacing_hours"],
    );
    assert.deepEqual(
      [...get("table-kind").options].map((o) => o.value),
      ["locations", "observations"],
    );
    assert.equal(get("distribution").hidden, true);
    const grid = get("metrics-grid").textContent;
    assert.match(grid, /Viewing events1/);
    assert.match(grid, /Time covered and gapsUnavailable/);
    assert.doesNotMatch(grid, /NaN|Covered duration|Longest gap/);
    assert.match(get("metrics").textContent, /gaps unavailable/);
    assert.equal(get("gaps").parentElement.hidden, true);

    const event = data.points[0].observations[0];
    const marker = w.document.querySelector("#observations .event");
    // Zero duration: CSS min-width alone makes the epoch visible.
    assert.equal(marker.style.width, "0%");
    assert.match(marker.title, /metadata, not viewing time/);
    marker.click();
    const expected =
      (Date.parse(event.epoch) - Date.parse(data.scenario.start_time)) / 1000;
    assert.ok(Math.abs(Number(get("time-slider").value) - expected) < 0.2);

    w.document.querySelector('[data-tab="table"]').click();
    get("table-kind").value = "observations";
    get("table-kind").dispatchEvent(new w.Event("change"));
    const cells = [...get("result-table").querySelectorAll("tbody td")].map(
      (e) => e.textContent,
    );
    assert.deepEqual(cells, [
      "S",
      "Pointed",
      event.epoch,
      event.envelope_start,
      event.envelope_end,
      "Epoch only",
    ]);
    get("result-table").querySelector("tbody tr").click();
    assert.match(get("event-detail").textContent, /^Viewing epoch/);
  } finally {
    dom.window.close();
  }
});

test("a pointed study's map link marks the epoch, not its envelope", async () => {
  const { dom, get, data, viewer } = await setup({ editFixture: asPointedStudy });
  try {
    assert.ok(viewer, get("status").textContent);
    const event = data.points[0].observations[0];
    const at = (time) => Cesium.JulianDate.fromIso8601(time);
    // The Observations layer alone; the footprint boresight line is always drawn.
    const shown = () =>
      viewer.entities.values.filter((e) =>
        e.polyline?.show?.getValue(at(event.epoch)),
      );
    const withLinks = shown();
    get("show-links").checked = false;
    const withoutLinks = shown();
    get("show-links").checked = true;
    const links = withLinks.filter((e) => !withoutLinks.includes(e));
    assert.equal(links.length, 1);
    const link = links[0].polyline.show;
    assert.equal(link.getValue(at(event.envelope_start)), false);
    assert.equal(link.getValue(at(event.envelope_end)), false);
  } finally {
    dom.window.close();
  }
});

test("the study download holds inputs, results and provenance", async () => {
  const { dom, w, get, data } = await setup({
    presentation: "overview",
    editFixture: asPointedStudy,
  });
  try {
    let content, type, filename;
    w.Blob = class {
      constructor(parts, options) {
        content = parts.join("");
        type = options.type;
      }
    };
    w.URL.createObjectURL = () => "blob:study";
    w.URL.revokeObjectURL = () => {};
    w.HTMLAnchorElement.prototype.click = function () {
      filename = this.download;
    };
    assert.equal(get("export-json").hidden, false);
    get("export-json").click();
    assert.equal(type, "application/json");
    assert.equal(filename, `tatc-study-${data.analysis_id}.json`);
    const study = JSON.parse(content);
    assert.equal(study.analysis_id, data.analysis_id);
    assert.deepEqual(study.scenario, data.scenario);
    assert.deepEqual(study.request, data.request);
    assert.deepEqual(study.results.points, data.points);
    assert.deepEqual(study.results.tracks, data.tracks);
    assert.deepEqual(study.assumptions, data.assumptions);
    assert.deepEqual(study.software, data.software);
    assert.equal(study.accounting.event_based, true);
    assert.equal(study.ui, undefined);

    get("export-csv").click();
    assert.match(type, /^text\/csv/);
    assert.equal(filename, "tatc-study.csv");
  } finally {
    dom.window.close();
  }
});

test("orbit views offer no study download without an analysis", async () => {
  const { dom, get } = await setup({ orbitOnly: true });
  try {
    assert.equal(get("export-json").hidden, true);
    assert.equal(get("export-csv").hidden, false);
  } finally {
    dom.window.close();
  }
});

test("inputs edit a conical instrument that has no field of regard", async () => {
  const instrument = {
    name: "Conical",
    cone_angle: 47.5,
    along_track_field_of_view: 1,
  };
  const { dom, w, get, sizes } = await setup({
    presentation: "overview",
    editFixture: (data) => {
      Object.assign(data, conicalFixture);
      data.request = {
        view: "coverage",
        scenario: {
          ...data.request.scenario,
          satellites: [
            {
              name: "S",
              orbit: conicalFixture.scenario.satellites[0].orbit,
              instruments: [{ ...instrument }],
            },
          ],
        },
      };
    },
  });
  try {
    assert.equal(get("research-error").hidden, true);
    const labels = [...w.document.querySelectorAll("label")];
    const control = (label) =>
      labels.find((e) => e.textContent.startsWith(label))?.querySelector("input");
    assert.equal(control("Field of regard"), undefined);
    assert.equal(control("Cone angle (°)").value, "47.5");
    assert.equal(control("Along-track view (°)").value, "1");
    // Shows TATC's values without adding them to the draft.
    assert.equal(control("Scan center azimuth (°)").value, "0");
    assert.equal(control("Scan half width (°)").value, "180");
    assert.equal(get("study-form").checkValidity(), true);
    const submit = () => {
      get("study-form").dispatchEvent(
        new w.Event("submit", { cancelable: true }),
      );
      return sizes.filter((m) => m.type === "tatc-workspace:run").at(-1).payload
        .params.scenario.satellites[0].instruments[0];
    };
    assert.deepEqual({ ...submit() }, instrument);

    const cone = control("Cone angle (°)");
    cone.value = "90";
    cone.dispatchEvent(new w.Event("input"));
    assert.equal(cone.checkValidity(), false);
    cone.value = "40";
    cone.dispatchEvent(new w.Event("input"));
    assert.equal(cone.checkValidity(), true);
    const halfWidth = control("Scan half width (°)");
    halfWidth.value = "60";
    halfWidth.dispatchEvent(new w.Event("input"));
    assert.deepEqual(
      { ...submit() },
      { ...instrument, cone_angle: 40, scan_half_width: 60 },
    );
  } finally {
    dom.window.close();
  }
});

test("a conical field of regard stays editable when it was supplied", async () => {
  const { dom, w } = await setup({
    presentation: "overview",
    editFixture: (data) => {
      Object.assign(data.request.scenario.satellites[0].instruments[0], {
        cone_angle: 47.5,
        along_track_field_of_view: 1,
        field_of_regard: 120,
      });
    },
  });
  try {
    const value = [...w.document.querySelectorAll("label")]
      .find((e) => e.textContent.startsWith("Field of regard"))
      .querySelector("input").value;
    assert.equal(value, "120");
  } finally {
    dom.window.close();
  }
});

test("a mixed study counts only real viewing events", async () => {
  const { dom, get, w } = await setup({
    presentation: "overview",
    editFixture: (data) => {
      asPointedStudy(data);
      const point = data.points[0];
      point.observations.push({
        satellite: "S",
        instrument: "Basic",
        start: point.observations[0].envelope_start,
        end: point.observations[0].envelope_end,
        epoch: point.observations[0].epoch,
        duration_seconds: 100,
        accounting_method: "interval_union",
      });
      point.metrics.observation_count = 2;
    },
  });
  try {
    assert.match(get("metrics-grid").textContent, /Viewing events1/);
    assert.match(get("metrics").textContent, /^1 viewing events/);
    w.document.querySelector('[data-tab="table"]').click();
    assert.equal(
      get("result-table").querySelector("tbody td:nth-child(2)").textContent,
      "1",
    );
  } finally {
    dom.window.close();
  }
});
