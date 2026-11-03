// Real HTTP MCP responses, deployed assets, CSP, URL iframe sandbox, and WebGL.
// Run against the dev server. No CDN interception or replacement asset responses.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");
const http = require("node:http");
const root = path.resolve(__dirname, "../../../..");
const endpoint = process.env.TATC_TEST_SERVER || "http://localhost:8000";

function callTool(argumentsList) {
  return JSON.parse(
    execFileSync(
      process.env.TATC_PYTHON || "python3",
      [
        "-c",
        `
import asyncio, json, os, sys
from fastmcp import Client
arguments_list = json.load(sys.stdin)
async def run():
    output = []
    async with Client(os.environ['TATC_TEST_SERVER'] + '/mcp', timeout=60) as client:
        for arguments in arguments_list:
            result = await client.call_tool('visualize', arguments)
            resource = next(c.resource for c in result.content if c.type == 'resource')
            assert resource.mimeType == 'text/uri-list'
            output.append(resource.text)
    print(json.dumps(output))
asyncio.run(run())
`,
      ],
      {
        cwd: root,
        env: { ...process.env, TATC_TEST_SERVER: endpoint },
        input: JSON.stringify(argumentsList),
        encoding: "utf8",
      },
    ),
  );
}
const dataset = {
  title: "Revisit Times",
  labels: ["Design A", "Design B"],
  series: [{ name: "Revisit Time", values: [3.5, 1.2] }],
  unit: "hours",
  x_label: "Design",
};

// The deployed Header's class contract, kept independent of localized labels.
const chatHeader =
  '<main><div class="absolute top-0" style="display:flex;align-items:center;gap:8px"><div class="hide-scrollbar" style="flex:1"><h2>TATC research conversation</h2></div></div><p>Open the study alongside this conversation.</p></main>';
function host(url) {
  return `<!doctype html><html><head><link rel="stylesheet" href="/workspace.css"><style>body{margin:0;background:#202123;color:#e7e9eb;font:15px system-ui}#root{padding:24px;box-sizing:border-box}iframe{display:block;border:0;width:100%;height:72px}</style></head><body><div id="root">${chatHeader}</div><script src="/workspace.js" data-viewer-origin="${endpoint}"></script><script>
 window.actions=[];
 window.addResource = url => {const f=document.createElement('iframe');f.className='resource';f.setAttribute('sandbox','allow-scripts allow-same-origin allow-popups');f.src=url;document.getElementById('root').append(f);};
 addEventListener('message',e=>{if(e.data?.type==='intent'){window.actions.push(e.data);e.source.postMessage({type:'ui-message-received',messageId:e.data.messageId},e.origin);e.source.postMessage({type:'ui-message-response',messageId:e.data.messageId},e.origin);}});
 if (${JSON.stringify(url)}) addResource(${JSON.stringify(url)});
 </script></body></html>`;
}

// Optional integration with the installed LibreChat bundle. Resolve exported
// symbols from that build instead of copying its renderer implementation.
async function libreChatHost() {
  if (!process.env.TATC_LIBRECHAT_URL) return null;
  const origin = process.env.TATC_LIBRECHAT_URL;
  const response = await fetch(origin);
  const index = await response.text();
  assert.ok(
    /\/assets\/tatc-workspace\.js\?v=[a-f0-9]{12}/.test(index),
    "LibreChat startup must install the host adapter",
  );
  assert.equal(
    await (await fetch(origin + "/assets/tatc-workspace.js")).text(),
    await fs.readFile(
      path.join(root, "apps/librechat/assets/tatc-workspace.js"),
      "utf8",
    ),
  );
  const appPath = index.match(/src="(\.\/assets\/index[^" ]+\.js)"/)[1];
  const appSource = await (await fetch(new URL(appPath, origin))).text();
  assert.ok(
    appSource.includes("absolute top-0") &&
      appSource.includes(
        "hide-scrollbar flex w-full items-center justify-between",
      ),
    "Installed LibreChat Header must match the Study button integration point",
  );
  const asset = (name) =>
    new URL(
      index.match(new RegExp("\\./assets/" + name + '[^" ]+\\.js'))[0],
      origin,
    ).href;
  const vendor = asset("vendor"),
    advanced = asset("advanced-inputs");
  const source = await (await fetch(vendor)).text();
  const renderer = source.match(/(\w+)\.displayName=`UIResourceRenderer`/)[1];
  const exported = (name) =>
    source.match(new RegExp("(?:[,{])" + name + " as (\\w+)[,}]"))[1];
  const react = source.match(/(\w+) as require_react[,}]/)[1];
  return {
    origin,
    headers: response.headers.get("content-security-policy")
      ? {
          "content-security-policy": response.headers.get(
            "content-security-policy",
          ),
        }
      : {},
    html: (
      url,
    ) => `<!doctype html><html><head><link rel="stylesheet" href="/workspace.css"><style>body{margin:0;background:#202123;color:#e7e9eb;font:15px system-ui}#root{padding:24px;box-sizing:border-box}iframe{display:block;border:0}</style></head><body><div id="root">${chatHeader}</div>
    <script src="/workspace.js" data-viewer-origin="${endpoint}"></script>
    <script type="module">
      import {${exported(renderer)} as Resource, ${exported("require_client")} as client} from ${JSON.stringify(vendor)};
      import {${react} as react} from ${JSON.stringify(advanced)};
      const React = react(); window.actions=[];
      window.addResource = url => {
        const element=document.createElement('div');document.getElementById('root').append(element);
        client().createRoot(element).render(React.createElement(Resource, {
          resource: {uri:'ui://tatc/test/'+crypto.randomUUID(), mimeType:'text/uri-list', text:url},
          onUIAction: action => {window.actions.push(action);},
          htmlProps:{autoResizeIframe:{width:true,height:true},sandboxPermissions:'allow-popups',iframeProps:{className:'resource'}}
        }));
      };
      if (${JSON.stringify(url)}) addResource(${JSON.stringify(url)});
    </script></body></html>`,
  };
}

function scene(url) {
  return JSON.parse(
    require("node:zlib").gunzipSync(
      Buffer.from(new URL(url).hash.slice(4), "base64url"),
    ),
  );
}
function viewerUrl(url) {
  return url.replace("/launch#", "/#");
}

test(
  "research pane works with real LibreChat resources, numerical views, WebGL and exact reruns",
  { timeout: 210000 },
  async () => {
    const [coverageUrl, simulationUrl, dataUrl] = callTool([
      { view: "example" },
      { view: "example", presentation: "simulation3d" },
      { view: "data", dataset },
    ]);
    const urls = {
      coverage: coverageUrl,
      simulation: simulationUrl,
      data: dataUrl,
    };
    const librechat = await libreChatHost();
    const hostJs = await fs.readFile(
      path.join(root, "apps/librechat/assets/tatc-workspace.js"),
    );
    const hostCss = await fs.readFile(
      path.join(root, "apps/librechat/assets/tatc-workspace.css"),
    );
    const server = http.createServer((req, res) => {
      if (req.url === "/workspace.js") {
        res.setHeader("content-type", "text/javascript");
        res.end(hostJs);
        return;
      }
      if (req.url === "/workspace.css") {
        res.setHeader("content-type", "text/css");
        res.end(hostCss);
        return;
      }
      res.setHeader("content-type", "text/html; charset=utf-8");
      if (librechat)
        for (const [key, value] of Object.entries(librechat.headers))
          res.setHeader(key, value);
      res.end(
        (librechat ? librechat.html : host)(urls[req.url.slice(3)] || null),
      );
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    let browser;
    try {
      browser = await chromium.launch({
        ...(process.env.TATC_BROWSER_CHANNEL
          ? { channel: process.env.TATC_BROWSER_CHANNEL }
          : {}),
        headless: true,
      });
      const page = await browser.newPage({
        viewport: { width: 1400, height: 1050 },
        colorScheme: "dark",
      });
      await page.addInitScript(() => {
        window.__testClicks = [];
        document.addEventListener(
          "click",
          (event) => {
            window.__testClicks.push({
              id: event.target.id,
              tag: event.target.tagName,
              x: event.clientX,
              y: event.clientY,
            });
          },
          true,
        );
        let cesium;
        Object.defineProperty(window, "Cesium", {
          configurable: true,
          get: () => cesium,
          set: (value) => {
            const Viewer = new Proxy(value.Viewer, {
              construct(target, args) {
                const viewer = Reflect.construct(target, args);
                window.__testViewer = viewer;
                return viewer;
              },
            });
            cesium = new Proxy(value, {
              get: (target, key) =>
                key === "Viewer" ? Viewer : Reflect.get(target, key),
            });
          },
        });
      });
      const errors = [],
        requests = [],
        workers = [];
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("console", (m) => {
        if (m.type() === "error") errors.push(m.text());
      });
      page.on("request", (r) => requests.push(r.url()));
      page.on("worker", (w) => workers.push(w.url()));
      await page.route("**/*", (route) => {
        if (
          !["localhost", "127.0.0.1"].includes(
            new URL(route.request().url()).hostname,
          )
        ) {
          errors.push("External request");
          return route.abort();
        }
        return route.continue();
      });
      const hostOrigin = `http://127.0.0.1:${server.address().port}`;
      async function open(view) {
        await page.goto(hostOrigin + "/c/" + view);
        const launch = page.frameLocator("iframe.resource").first();
        await launch.getByRole("link", { name: "Open study pane →" }).waitFor();
        assert.equal(
          await page.locator("#tatc-study-pane").count(),
          0,
          "Historical results must not automatically open a pane",
        );
        await page.evaluate(
          ({ url, workspaceId, origin }) =>
            window.dispatchEvent(
              new MessageEvent("message", {
                origin,
                source: window,
                data: {
                  type: "tatc-workspace:open",
                  payload: { url, workspaceId, title: "Untrusted message" },
                },
              }),
            ),
          {
            url: viewerUrl(urls[view]),
            workspaceId: scene(urls[view]).ui.workspace_id,
            origin: endpoint,
          },
        );
        assert.equal(
          await page.locator("#tatc-study-pane").count(),
          0,
          "An unverified source cannot open the pane",
        );
        await launch.getByRole("link", { name: "Open study pane →" }).click();
        await page.locator("#tatc-study-frame").waitFor();
        const frame = page.frameLocator("#tatc-study-frame");
        await frame.locator("#visual:not([hidden])").waitFor();
        assert.equal(await frame.locator("#research-error").isVisible(), false);
        assert.deepEqual(
          await page.evaluate(() => window.actions),
          [],
          "Internal pane messages must not become chat actions",
        );
        return frame;
      }
      async function screenshot(name) {
        if (!process.env.TATC_SCREENSHOT_DIR) return;
        await fs.mkdir(process.env.TATC_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({
          path: path.join(process.env.TATC_SCREENSHOT_DIR, name + ".png"),
          fullPage: true,
        });
      }
      async function settlePaneLayout() {
        // Parent resize and the cross-origin child viewport update separately.
        // Wait for both before aiming a pointer at controls inside the iframe.
        const element = await page.locator("#tatc-study-frame").elementHandle();
        const child = await element.contentFrame();
        const width = await element.evaluate((e) => e.clientWidth);
        await child.waitForFunction(
          (expected) => innerWidth === expected,
          width,
        );
        await child.evaluate(
          () =>
            new Promise((resolve) =>
              requestAnimationFrame(() => requestAnimationFrame(resolve)),
            ),
        );
      }
      await page.goto(hostOrigin + "/c/empty");
      const studyButton = page.getByRole("button", {
        name: "Study",
        exact: true,
      });
      await studyButton.waitFor();
      assert.equal(await page.locator("#tatc-study-pane").count(), 0);
      await studyButton.click();
      await page.getByRole("heading", { name: "No study open" }).waitFor();
      assert.equal(await page.locator("iframe").count(), 0);
      assert.equal(
        requests.some((u) => u.includes("Cesium.js")),
        false,
      );
      assert.deepEqual(await page.evaluate(() => window.actions), []);
      await screenshot("empty-study");
      await page.getByRole("button", { name: "Close study workspace" }).click();
      await page.evaluate((url) => window.addResource(url), dataUrl);
      await page
        .frameLocator("iframe.resource")
        .getByRole("link", { name: "Open study pane →" })
        .waitFor();
      assert.equal(await page.locator("#tatc-study-frame").count(), 0);
      await studyButton.click();
      await page
        .frameLocator("#tatc-study-frame")
        .locator(".plot-bar")
        .first()
        .waitFor();
      let frame = await open("data");
      assert.equal(await frame.locator('[data-tab="map"]').isVisible(), false);
      assert.deepEqual(
        await frame
          .locator(".plot-value")
          .evaluateAll((es) => es.map((e) => e.getAttribute("data-value"))),
        ["3.5", "1.2"],
      );
      assert.deepEqual(
        await frame.locator(".plot-axis-title").allTextContents(),
        ["Revisit Time (hours)", "Design"],
      );
      assert.deepEqual(await frame.locator(".plot-tick").allTextContents(), [
        "0",
        "1",
        "2",
        "3",
        "4",
      ]);
      await frame.locator(".plot-bar").last().focus();
      assert.equal(
        await frame.locator(".plot-inspection").textContent(),
        "Design B · Revisit Time: 1.2 hours",
      );
      await screenshot("revisit-plot-desktop");
      await page.setViewportSize({ width: 390, height: 1050 });
      await frame
        .locator(".data-plot")
        .evaluate(
          (e) =>
            new Promise((resolve) =>
              requestAnimationFrame(() => requestAnimationFrame(resolve)),
            ),
        );
      const plotBounds = await frame.locator(".data-plot").boundingBox();
      assert.ok(plotBounds.width <= 390 && plotBounds.height >= 300);
      await screenshot("revisit-plot-mobile");
      await page.setViewportSize({ width: 1400, height: 1050 });
      await settlePaneLayout();
      assert.equal(
        requests.some((u) => u.includes("Cesium.js")),
        false,
      );
      await frame.getByRole("button", { name: "Table", exact: true }).click();
      await frame
        .locator("#table-panel:not([hidden]) tbody tr")
        .first()
        .waitFor();
      assert.equal(await frame.locator("tbody tr").count(), 2);
      const download = page.waitForEvent("download");
      await frame.getByRole("button", { name: "Export CSV" }).click();
      const csv = await fs.readFile(await (await download).path(), "utf8");
      assert.ok(csv.includes('"Design B","1.2"'));
      assert.ok(csv.includes('"3.5"'));
      await screenshot("data-table");
      await page.getByRole("button", { name: "Close study workspace" }).click();
      assert.equal(await page.locator("#tatc-study-pane").isVisible(), false);
      await page.getByRole("button", { name: "Study", exact: true }).click();
      await frame.locator("#table-panel:not([hidden])").waitFor();

      frame = await open("coverage");
      assert.equal(
        await frame.locator("#comparison-chart .bar-row").count(),
        3,
      );
      assert.equal(
        requests.some((u) => u.includes("Cesium.js")),
        false,
      );
      const oldScene = scene(coverageUrl);
      await screenshot("coverage-results");
      await frame.locator("#comparison-chart button").nth(1).click();
      assert.equal(await frame.locator("#location").inputValue(), "1");
      await frame.getByRole("button", { name: "Table", exact: true }).click();
      await frame.locator("#table-kind").selectOption("gaps");
      await frame.locator("tbody tr").first().click();
      const gap = oldScene.points[1].gaps[0];
      assert.ok(
        Math.abs(
          Number(await frame.locator("#time-slider").inputValue()) -
            ((Date.parse(gap.start) + Date.parse(gap.end)) / 2 -
              Date.parse(oldScene.scenario.start_time)) /
              1000,
        ) < 0.051,
      );

      // UI draft must reach the installed resource renderer's actual onUIAction.
      await frame.getByRole("button", { name: "Inputs", exact: true }).click();
      await frame
        .getByLabel("Study sensor · field of regard (°)")
        .first()
        .fill("80");
      const draftFrame = page
        .frames()
        .find((f) => f.url() === viewerUrl(coverageUrl));
      await page.getByRole("button", { name: "Close study workspace" }).click();
      await page.getByRole("button", { name: "Study", exact: true }).click();
      assert.ok(
        page.frames().includes(draftFrame),
        "Closing must retain the iframe and its draft",
      );
      assert.equal(
        await frame
          .getByLabel("Study sensor · field of regard (°)")
          .first()
          .inputValue(),
        "80",
      );
      await settlePaneLayout();
      await frame
        .getByRole("button", { name: "Run updated study in chat" })
        .click();
      await page
        .waitForFunction(() => window.actions.length === 1, null, {
          timeout: 5000,
        })
        .catch(async () => {
          throw new Error(
            JSON.stringify({
              status: await frame.locator("#run-status").textContent(),
              invalid: await frame.locator("input:invalid").evaluateAll((es) =>
                es.map((e) => ({
                  name: e.parentNode.textContent,
                  value: e.value,
                  validation: e.validationMessage,
                })),
              ),
              errors,
              actions: await page.evaluate(() =>
                window.actions.map((a) => a.type),
              ),
            }),
          );
        });
      const action = await page.evaluate(() => window.actions[0]);
      assert.equal(action.type, "intent");
      assert.equal(action.payload.intent, "rerun_tatc_study");
      const params = action.payload.params;
      assert.equal(params.workspace_id, oldScene.ui.workspace_id);
      assert.equal(
        params.scenario.satellites[0].instruments[0].field_of_regard,
        80,
      );
      assert.equal(
        params.scenario.satellites[1].instruments[0].field_of_regard,
        100,
      );
      assert.equal(
        params.scenario.start_time,
        oldScene.request.scenario.start_time,
      );
      assert.match(
        await frame.locator("#run-status").textContent(),
        /Submitted to chat/,
      );
      // The real conversation can unmount the originating message while it
      // streams the next response. Keep the study visible across that refresh.
      await page.evaluate(() => {
        document.querySelectorAll("iframe.resource").forEach((f) => f.remove());
      });
      assert.equal(await page.locator("#tatc-study-pane").count(), 1);
      // A schema-valid model response can still change the mission. Exercise
      // the same extra-satellite failure observed in the real conversation.
      const altered = structuredClone(params);
      altered.scenario.satellites.push({
        ...structuredClone(altered.scenario.satellites[0]),
        name: "Unrequested satellite",
      });
      const [alteredUrl] = callTool([altered]);
      await page.evaluate((url) => window.addResource(url), alteredUrl);
      await frame.locator("#study-notice").waitFor({ state: "visible" });
      assert.match(
        await frame.locator("#study-notice").textContent(),
        /different inputs/,
      );
      assert.equal(
        await page.locator("#tatc-study-frame").getAttribute("src"),
        viewerUrl(coverageUrl),
      );
      await page
        .frameLocator("iframe.resource")
        .last()
        .getByText("Result not applied")
        .waitFor();
      assert.equal(
        await page
          .frameLocator("iframe.resource")
          .last()
          .locator("#open-study")
          .getAttribute("href"),
        null,
      );
      assert.equal(
        await page.locator("#tatc-study-frame").getAttribute("src"),
        viewerUrl(coverageUrl),
      );
      // Perform the exact MCP call the model is asked to make, then render its real response.
      const [rerunUrl] = callTool([params]);
      const rerun = scene(rerunUrl);
      assert.notEqual(rerun.analysis_id, oldScene.analysis_id);
      assert.notDeepEqual(
        rerun.points.map((p) => p.metrics),
        oldScene.points.map((p) => p.metrics),
      );
      await page.evaluate((url) => window.addResource(url), rerunUrl);
      await page.waitForFunction(
        (url) => document.getElementById("tatc-study-frame").src === url,
        viewerUrl(rerunUrl),
      );
      await frame.locator("#comparison-chart .bar-row").first().waitFor();
      assert.equal(await page.locator("#tatc-study-pane").count(), 1);
      // Remounting old chat history must not roll the open study back. A
      // duplicate current resource must not reload it and discard UI state.
      await frame.getByRole("button", { name: "Table", exact: true }).click();
      await page.evaluate((url) => window.addResource(url), coverageUrl);
      await page.evaluate((url) => window.addResource(url), rerunUrl);
      await page
        .frameLocator("iframe.resource")
        .last()
        .getByRole("link", { name: "Open study pane →" })
        .waitFor();
      assert.equal(
        await page.locator("#tatc-study-frame").getAttribute("src"),
        viewerUrl(rerunUrl),
      );
      assert.equal(await frame.locator("#table-kind").isVisible(), true);
      await frame.getByRole("button", { name: "Results", exact: true }).click();
      await screenshot("coverage-rerun");
      await page.getByRole("separator", { name: "Resize study pane" }).focus();
      await page.keyboard.press("ArrowLeft");
      assert.equal(
        Math.round(
          (await page.locator("#tatc-study-pane").boundingBox()).width,
        ),
        690,
      );
      await settlePaneLayout();

      urls.coverage = rerunUrl;
      for (const view of ["coverage", "simulation"]) {
        if (view === "simulation") frame = await open("simulation");
        else
          await frame.getByRole("button", { name: "Map", exact: true }).click();
        await frame
          .locator("#status[hidden]")
          .waitFor({ state: "attached", timeout: 35000 })
          .catch(async (error) => {
            const diagnosticFrame = page
              .frames()
              .find(
                (f) =>
                  f.url() ===
                  viewerUrl(view === "simulation" ? simulationUrl : rerunUrl),
              );
            await screenshot("map-load-failure");
            throw new Error(
              JSON.stringify({
                view,
                status: await frame.locator("#status").textContent(),
                errors,
                state: await diagnosticFrame?.evaluate(() => ({
                  mapRequested: window.tatcMapRequested,
                  cesiumLoaded: !!window.Cesium,
                  viewerCreated: !!window.__testViewer,
                  activeTab: document.querySelector(
                    '[data-tab][aria-pressed="true"]',
                  )?.dataset.tab,
                  resources: performance
                    .getEntriesByType("resource")
                    .map((r) => ({
                      path: new URL(r.name).pathname,
                      duration: Math.round(r.duration),
                    })),
                })),
              }),
              { cause: error },
            );
          });
        let child = page
          .frames()
          .find(
            (f) =>
              f.url() ===
              viewerUrl(view === "simulation" ? simulationUrl : rerunUrl),
          );
        await child.waitForFunction(
          () =>
            window.__testViewer?.scene.globe._surface._tilesToRender.length > 0,
          null,
          { timeout: 30000 },
        );
        assert.notEqual(
          new URL(page.url()).origin,
          new URL(child.url()).origin,
        );
        for (const width of [1400, 390]) {
          await page.setViewportSize({
            width,
            height: width === 390 ? 844 : 900,
          });
          await settlePaneLayout();
          await child.waitForFunction(
            () =>
              window.__testViewer.scene.canvas.clientWidth ===
              document.getElementById("globe").clientWidth,
          );
          const bounds = await page.locator("#tatc-study-pane").boundingBox(),
            canvas = await frame.locator("canvas").first().boundingBox();
          assert.ok(canvas.width >= bounds.width - 2);
          assert.ok(canvas.height >= 350);
          const timeline = await frame.locator("#study-timeline").boundingBox();
          assert.ok(
            timeline.y + timeline.height <= bounds.y + bounds.height + 1,
            "Timeline stays inside the visible pane",
          );
          assert.ok(
            canvas.y + canvas.height <= timeline.y + 1,
            "Map fits above the timeline without scrolling",
          );
          const fit = await child.evaluate(() => {
            const v = window.__testViewer,
              C = window.Cesium,
              f = v.camera.frustum,
              horizontal = 2 * Math.atan(Math.tan(f.fovy / 2) * f.aspectRatio),
              radius = Math.asin(
                C.Ellipsoid.WGS84.maximumRadius /
                  C.Cartesian3.magnitude(v.camera.positionWC),
              );
            return (2 * radius) / Math.min(f.fovy, horizontal);
          });
          assert.ok(fit > 0.6 && fit < 0.9, "Earth fits pane: " + fit);
          assert.equal(
            await frame
              .locator("body")
              .evaluate(
                () => document.documentElement.scrollWidth > innerWidth,
              ),
            false,
          );
          await screenshot(`${view}-pane-${width}`);
        }
        // Test mobile interactions with its viewport set before navigation.
        // The preceding resize sequence separately checks responsive geometry.
        frame = await open(view);
        if (view === "coverage")
          await frame.getByRole("button", { name: "Map", exact: true }).click();
        await frame
          .locator("#status[hidden]")
          .waitFor({ state: "attached", timeout: 35000 });
        child = page.frames().find((f) => f.url() === viewerUrl(urls[view]));
        await frame.locator("#view-2d").click();
        assert.equal(
          await child.evaluate(() => window.__testViewer.scene.mode),
          2,
        );
        await frame.locator("#view-3d").click();
        assert.equal(
          await child.evaluate(() => window.__testViewer.scene.mode),
          3,
        );
        await frame.locator("#play").click();
        const playing = await frame.locator("#play").textContent();
        if (playing !== "Pause") {
          await screenshot("playback-failure");
          throw new Error(
            JSON.stringify({
              view,
              playing,
              errors,
              state: await child.evaluate(() => ({
                status: document.getElementById("status").textContent,
                hidden: document.getElementById("status").hidden,
                seconds: document.getElementById("time-slider").value,
                clicks: window.__testClicks.slice(-8),
                buttonBounds: document
                  .getElementById("play")
                  .getBoundingClientRect()
                  .toJSON(),
                viewport: [innerWidth, innerHeight],
              })),
              hostClicks: await page.evaluate(() =>
                window.__testClicks.slice(-8),
              ),
              frameBounds: await page
                .locator("#tatc-study-frame")
                .boundingBox(),
            }),
          );
        }
        await page
          .getByRole("button", { name: "Close study workspace" })
          .click();
        await child.waitForFunction(
          () => !window.__testViewer.useDefaultRenderLoop,
        );
        const preserved = await child.evaluate(() => ({
          seconds: document.getElementById("time-slider").value,
          camera: window.Cesium.Cartesian3.clone(
            window.__testViewer.camera.positionWC,
          ),
        }));
        assert.equal(await frame.locator("#play").textContent(), "Play");
        await page.getByRole("button", { name: "Study", exact: true }).click();
        await child.waitForFunction(
          () => window.__testViewer.useDefaultRenderLoop,
        );
        assert.ok(page.frames().includes(child));
        assert.deepEqual(
          await child.evaluate(() => ({
            seconds: document.getElementById("time-slider").value,
            camera: window.Cesium.Cartesian3.clone(
              window.__testViewer.camera.positionWC,
            ),
          })),
          preserved,
        );
        await settlePaneLayout();
        await frame.locator(".map-tools summary").click();
        await frame.locator("#trail-minutes").fill("10");
        await frame.locator("#trail-minutes").press("Tab");
        assert.equal(
          await child.evaluate(() =>
            window.__testViewer.entities.values
              .find((e) => e.path)
              .path.trailTime.getValue(),
          ),
          600,
        );
        if (view === "coverage") {
          await frame.locator("#location").selectOption("1");
          await frame.locator("#gaps .event").first().click();
          assert.match(
            await frame.locator("#event-detail").textContent(),
            /^Gap:/,
          );
        }
        await page.setViewportSize({ width: 1400, height: 1050 });
      }
      assert.ok(workers.length > 0);
      assert.ok(requests.some((u) => u.includes("/NaturalEarthII/")));
      assert.deepEqual(errors, []);
      await page.evaluate(() => {
        history.pushState({}, "", "/another-conversation");
        document.getElementById("root").append(document.createElement("p"));
      });
      await page.locator("#tatc-study-pane").waitFor({ state: "detached" });
      // A map failure must leave the analysis available, with a visible map error.
      const blocked = await browser.newPage();
      await blocked.route("**/visualization/v1/", async (route) => {
        const response = await route.fetch();
        const headers = response.headers();
        headers["content-security-policy"] = headers[
          "content-security-policy"
        ].replace("worker-src 'self' blob:", "worker-src 'none'");
        await route.fulfill({ response, headers });
      });
      await blocked.goto(viewerUrl(coverageUrl));
      await blocked.getByRole("button", { name: "Map", exact: true }).click();
      await blocked.waitForFunction(
        () =>
          document
            .getElementById("status")
            .textContent.includes("Globe loading timed out"),
        null,
        { timeout: 40000 },
      );
      assert.equal(await blocked.locator("#globe").isVisible(), false);
      await blocked
        .getByRole("button", { name: "Results", exact: true })
        .click();
      assert.equal(
        await blocked.locator("#comparison-chart .bar-row").count(),
        3,
      );
      assert.equal(await blocked.locator("#play").isDisabled(), false);
      await blocked.close();
    } finally {
      if (browser) await browser.close();
      await new Promise((resolve) => server.close(resolve));
    }
  },
);
