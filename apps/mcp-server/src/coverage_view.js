(async () => {
  const DATA = await window.tatcStudy;
  if (DATA.dataset) return;
  const $ = (id) => document.getElementById(id);
  const start = Date.parse(DATA.scenario.start_time),
    end = Date.parse(DATA.scenario.end_time);
  const duration = (end - start) / 1000;
  const points = DATA.points;
  const eventStudy = !!DATA.accounting?.event_based;
  // An epoch is an instant; hold its map link this long either side so playback
  // can show it. This is a display aid, not a viewing duration.
  const EVENT_LINK_HOLD_MS = 30000;

  let cancelGlobeLoad = () => {};
  let paneVisible = true;
  let sceneObserver;
  let viewer,
    selected = 0,
    seconds = 0,
    frame = null,
    previous = null,
    failed = false;
  const colors = ["#58c5ff", "#f7b95c", "#bca4ff", "#70ddab"];
  // LibreChat defaults embedded resources to 150px until this message arrives.
  // Measure content, not documentElement (whose height includes the iframe).
  let reportedHeight = 0;
  function resizeHost() {
    const height = Math.ceil(document.body.getBoundingClientRect().height);
    if (height > 0 && height !== reportedHeight) {
      reportedHeight = height;
      parent.postMessage({ type: "ui-size-change", payload: { height } }, "*");
    }
  }
  const sizeObserver = new ResizeObserver(resizeHost);
  sizeObserver.observe(document.body);
  resizeHost();
  $("time-slider").max = duration;
  $("play").disabled = false;
  // Labels give the event lanes a readable UTC scale; interval buttons keep
  // their full timestamps for precise inspection.
  for (let i = 0; i <= 4; i++) {
    const instant = new Date(start + ((end - start) * i) / 4).toISOString();
    const label = document.createElement("span");
    label.textContent = instant.slice(11, 16);
    label.title = instant;
    $("timeline-axis").append(label);
  }
  function playbackLabel(playing) {
    const label = document.createElement("span");
    label.className = "sr-only";
    label.textContent = playing ? "Pause" : "Play";
    $("play").replaceChildren(label);
    $("play").classList.toggle("is-playing", playing);
  }
  playbackLabel(false);
  function pause() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    previous = null;
    playbackLabel(false);
  }
  function mapVisible() {
    return (
      paneVisible &&
      document.visibilityState !== "hidden" &&
      !$("map-panel").hidden
    );
  }
  function updateRendering() {
    if (!paneVisible || document.visibilityState === "hidden") pause();
    if (!viewer || failed) return;
    viewer.useDefaultRenderLoop = mapVisible();
    if (mapVisible()) {
      viewer.resize();
      viewer.scene.requestRender();
    }
  }
  window.addEventListener("message", (event) => {
    const message = event.data;
    if (
      event.source !== parent ||
      message?.type !== "tatc-workspace:visibility" ||
      message.payload?.workspaceId !== DATA.ui?.workspace_id ||
      typeof message.payload?.visible !== "boolean"
    )
      return;
    paneVisible = message.payload.visible;
    updateRendering();
    window.dispatchEvent(new Event("tatc-pane-visibility"));
  });
  document.addEventListener("visibilitychange", updateRendering);
  window.addEventListener("tatc-map-show", updateRendering);
  window.addEventListener("tatc-map-hide", updateRendering);
  parent.postMessage(
    {
      type: "tatc-workspace:ready",
      payload: { workspaceId: DATA.ui?.workspace_id },
    },
    "*",
  );
  function fail(message) {
    if (failed) return;
    failed = true;
    cancelGlobeLoad();
    sceneObserver?.disconnect();
    pause();
    if (viewer && !viewer.isDestroyed()) viewer.destroy();
    $("globe").hidden = true;
    $("status").hidden = false;
    $("status").textContent = message;
    // Numerical results and their timeline remain usable if the map fails.
    resizeHost();
  }
  function setTime(value) {
    seconds = Math.max(0, Math.min(duration, value));
    $("time-slider").value = seconds;
    $("time").textContent = new Date(start + seconds * 1000)
      .toISOString()
      .replace(/\.\d{3}Z$/, " UTC")
      .replace("T", " ");
    document
      .querySelectorAll(".cursor")
      .forEach((e) => (e.style.left = (seconds / duration) * 100 + "%"));
    window.dispatchEvent(new CustomEvent("tatc-clock", { detail: seconds }));
    if (viewer && !failed) {
      viewer.clock.currentTime = Cesium.JulianDate.fromDate(
        new Date(start + seconds * 1000),
      );
      viewer.scene.requestRender();
    }
  }
  function tick(now) {
    if (previous !== null)
      setTime(seconds + ((now - previous) / 1000) * Number($("speed").value));
    previous = now;
    if (seconds >= duration) pause();
    else frame = requestAnimationFrame(tick);
  }
  $("play").onclick = () => {
    if (frame !== null) {
      pause();
      return;
    }
    if (seconds >= duration) setTime(0);
    playbackLabel(true);
    frame = requestAnimationFrame(tick);
  };
  $("time-slider").oninput = () => {
    pause();
    setTime(Number($("time-slider").value));
  };
  function minutes(value) {
    return (value / 60).toFixed(1) + " min";
  }
  function intervals(id, records, gap) {
    const lane = $(id);
    lane.replaceChildren();
    for (const interval of records) {
      const epochOnly = !!interval.envelope_start;
      const left = Date.parse(epochOnly ? interval.epoch : interval.start),
        right = epochOnly ? left : Date.parse(interval.end);
      const label = gap
        ? "Gap"
        : interval.satellite + " / " + interval.instrument;
      const detail = epochOnly
        ? label +
          ": viewing epoch " +
          interval.epoch +
          "; field-of-regard envelope " +
          interval.envelope_start +
          " – " +
          interval.envelope_end +
          " (metadata, not viewing time)"
        : label +
          ": " +
          interval.start +
          " – " +
          interval.end +
          " (" +
          minutes(interval.duration_seconds) +
          ")" +
          (interval.boundary ? "; touches window boundary" : "");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "event" + (gap ? " gap" : "");
      button.title = detail;
      button.setAttribute("aria-label", detail);
      button.style.left = ((left - start) / (end - start)) * 100 + "%";
      button.style.width = ((right - left) / (end - start)) * 100 + "%";
      button.onclick = () => {
        pause();
        setTime(((left + right) / 2 - start) / 1000);
        $("event-detail").textContent = detail;
        $("event-detail").title = detail;
      };
      lane.append(button);
    }
    const cursor = document.createElement("span");
    cursor.className = "cursor";
    lane.append(cursor);
  }
  function selectPoint(index) {
    selected = index;
    $("location").value = String(index);
    const point = points[index],
      metrics = point.metrics;
    $("metrics").textContent = eventStudy
      ? metrics.observation_count +
        " viewing events · Mean spacing " +
        (metrics.mean_event_spacing_hours == null
          ? "—"
          : metrics.mean_event_spacing_hours.toFixed(2) + " h") +
        " · Continuous coverage and gaps unavailable"
      : metrics.observation_count +
        " opportunities · " +
        (metrics.coverage_fraction * 100).toFixed(1) +
        "% time covered · Longest gap " +
        minutes(metrics.longest_gap_seconds) +
        " (includes boundaries)";
    intervals("observations", point.observations, false);
    intervals("gaps", point.gaps, true);
    $("event-detail").textContent = "Select an interval to inspect it.";
    $("event-detail").removeAttribute("title");
    setTime(seconds);
    window.dispatchEvent(new CustomEvent("tatc-point", { detail: index }));
  }
  $("gaps").parentElement.hidden = eventStudy;
  points.forEach((point, i) => {
    const option = document.createElement("option");
    option.value = i;
    option.textContent = point.name;
    $("location").append(option);
  });
  $("location").onchange = () => selectPoint(Number($("location").value));
  $("coverage").hidden = points.length === 0;
  $("visual").hidden = false;
  $("links-label").hidden = points.length === 0;
  $("footprints-label").hidden = !DATA.tracks.some((t) => t.footprints);
  if (points.length) selectPoint(0);
  else setTime(0);
  const assetRoot = new URL("../cesium/1.146/", location.href).href;
  window.CESIUM_BASE_URL = assetRoot;
  let styleReady, scriptReady;
  function loadCesium() {
    styleReady = new Promise((resolve, reject) => {
      const style = document.createElement("link");
      style.rel = "stylesheet";
      style.href = assetRoot + "Widgets/widgets.css";
      style.onload = resolve;
      style.onerror = () => reject(new Error("Cesium styles could not load."));
      document.head.append(style);
    });
    scriptReady = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = assetRoot + "Cesium.js";
      script.onload = resolve;
      script.onerror = () => reject(new Error("Cesium could not load."));
      document.head.append(script);
    });
  }
  async function initializeGlobe() {
    loadCesium();
    await Promise.all([styleReady, scriptReady]);
    const C = Cesium;
    const imagery = await C.TileMapServiceImageryProvider.fromUrl(
      C.buildModuleUrl("Assets/Textures/NaturalEarthII"),
    );
    // A user can close the pane while assets are loading. Wait for a visible
    // map before creating WebGL or measuring its initial camera.
    if (!mapVisible())
      await new Promise((resolve) => {
        const events = ["tatc-pane-visibility", "tatc-map-show"];
        function resume() {
          if (!mapVisible()) return;
          for (const event of events) window.removeEventListener(event, resume);
          document.removeEventListener("visibilitychange", resume);
          resolve();
        }
        for (const event of events) window.addEventListener(event, resume);
        document.addEventListener("visibilitychange", resume);
      });
    if (failed) return;
    // Cesium computes its initial camera from the container dimensions.
    // Construct it only after the globe has a real, visible layout box.
    $("status").textContent = "Loading globe…";
    $("globe").hidden = false;
    viewer = new C.Viewer("globe", {
      baseLayer: new C.ImageryLayer(imagery),
      baseLayerPicker: false,
      geocoder: false,
      animation: false,
      timeline: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: true,
      shouldAnimate: false,
      requestRenderMode: true,
      // Render at devicePixelRatio; the default 1x canvas blurs labels on HiDPI screens.
      useBrowserRecommendedResolution: false,
      skyBox: false,
    });
    let autoFrame = true;
    const first = DATA.tracks[0].samples[0];
    const focus = points.length
      ? [points[0].longitude, points[0].latitude]
      : [first[1], first[2]];
    const radius =
      C.Ellipsoid.WGS84.maximumRadius +
      Math.max(
        0,
        ...DATA.tracks.flatMap((track) =>
          track.samples.map((sample) => sample[3]),
        ),
      );
    function frameEarth() {
      if (
        !mapVisible() ||
        viewer.isDestroyed() ||
        viewer.scene.mode !== C.SceneMode.SCENE3D
      )
        return;
      viewer.resize();
      const frustum = viewer.camera.frustum;
      const horizontal =
        2 * Math.atan(Math.tan(frustum.fovy / 2) * frustum.aspectRatio);
      const distance =
        (radius * 1.15) / Math.sin(Math.min(frustum.fovy, horizontal) / 2);
      viewer.camera.setView({
        destination: C.Cartesian3.fromDegrees(
          focus[0],
          focus[1],
          distance - C.Ellipsoid.WGS84.maximumRadius,
        ),
        orientation: { heading: 0, pitch: -C.Math.PI_OVER_TWO, roll: 0 },
      });
      viewer.scene.requestRender();
    }
    frameEarth();
    // Preserve a user's manually chosen camera; resize the initial overview only.
    for (const event of ["pointerdown", "wheel"])
      $("globe").addEventListener(
        event,
        () => {
          autoFrame = false;
        },
        { passive: true },
      );
    sceneObserver = new ResizeObserver(() => {
      if (autoFrame) frameEarth();
    });
    sceneObserver.observe($("globe"));
    viewer.scene.backgroundColor = C.Color.fromCssColorString("#091321");
    viewer.scene.renderError.addEventListener(() =>
      fail("Unable to render the visualization."),
    );
    imagery.errorEvent.addEventListener(() =>
      fail("Unable to load globe imagery."),
    );
    viewer.clock.startTime = C.JulianDate.fromDate(new Date(start));
    viewer.clock.stopTime = C.JulianDate.fromDate(new Date(end));
    viewer.clock.clockRange = C.ClockRange.CLAMPED;
    const satellites = new Map();
    // Longitudes move the short way round, so a footprint crossing the
    // antimeridian between samples does not sweep across the globe.
    const lerp = ([lon, lat], [lon2, lat2], f) => {
      const d = lon2 - lon - 360 * Math.round((lon2 - lon) / 360);
      return [lon + d * f, lat + (lat2 - lat) * f];
    };
    DATA.tracks.forEach((track, i) => {
      const color = C.Color.fromCssColorString(colors[i % colors.length]);
      const legendItem = document.createElement("div");
      legendItem.className = "legend-item";
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = colors[i % colors.length];
      const name = document.createElement("span");
      name.textContent = track.name;
      name.title = track.name;
      legendItem.append(swatch, name);
      $("legend").append(legendItem);
      const position = new C.SampledPositionProperty(C.ReferenceFrame.FIXED);
      const samples = track.samples.map(([time, lon, lat, height]) => ({
        time: Date.parse(time),
        ground: C.Cartesian3.fromDegrees(lon, lat),
        position: C.Cartesian3.fromDegrees(lon, lat, height),
      }));
      position.addSamples(
        samples.map((s) => C.JulianDate.fromDate(new Date(s.time))),
        samples.map((s) => s.position),
      );
      const entity = viewer.entities.add({
        name: track.name,
        position,
        point: { pixelSize: 8, color },
        // A filled callout stays legible over any imagery; the marker, track
        // and legend carry the satellite's colour.
        label: {
          text: track.name,
          font: "500 12px Inter, ui-sans-serif, sans-serif",
          pixelOffset: new C.Cartesian2(0, -20),
          fillColor: C.Color.WHITE,
          showBackground: true,
          backgroundColor: C.Color.fromCssColorString("#16181bd9"),
          backgroundPadding: new C.Cartesian2(7, 4),
        },
        path: {
          show: true,
          leadTime: Number($("trail-minutes").value) * 60,
          trailTime: Number($("trail-minutes").value) * 60,
          width: 2,
          material: color,
        },
      });
      satellites.set(track.name, entity);
      // TATC footprints and boresights of pointed instruments, interpolated
      // between display samples so they move with the satellite.
      const sampleAt = (time) => {
        const current = C.JulianDate.toDate(time).getTime();
        const step = DATA.scenario.display_step_seconds * 1000;
        const k = Math.max(
          0,
          Math.min(Math.floor((current - start) / step), samples.length - 2),
        );
        const f =
          (current - samples[k].time) / (samples[k + 1].time - samples[k].time);
        return [k, Math.max(0, Math.min(1, f))];
      };
      for (const footprint of track.footprints || []) {
        const ring = (time, part) => {
          const [k, f] = sampleAt(time);
          const [a, b] = [footprint.parts[k], footprint.parts[k + 1]];
          // A footprint split at the antimeridian keeps its sampled shape.
          if (a.length !== b.length || a[part]?.length !== b[part]?.length)
            return (f < 0.5 ? a : b)[part];
          return a[part]?.map((p, n) => lerp(p, b[part][n], f));
        };
        const show = (time, part) =>
          $("show-footprints").checked && !!ring(time, part);
        const parts = Math.max(...footprint.parts.map((p) => p.length));
        for (let part = 0; part < parts; part++)
          viewer.entities.add({
            name: track.name + " / " + footprint.instrument,
            polygon: {
              show: new C.CallbackProperty((t) => show(t, part), false),
              hierarchy: new C.CallbackProperty(
                (t) =>
                  new C.PolygonHierarchy(
                    C.Cartesian3.fromDegreesArray((ring(t, part) || []).flat()),
                  ),
                false,
              ),
              height: 0,
              material: color.withAlpha(0.45),
              outline: true,
              outlineColor: color,
            },
          });
        viewer.entities.add({
          polyline: {
            show: new C.CallbackProperty((t) => show(t, 0), false),
            width: 1.5,
            arcType: C.ArcType.NONE,
            material: color.withAlpha(0.9),
            positions: new C.CallbackProperty((t) => {
              const [k, f] = sampleAt(t);
              const center = lerp(
                footprint.centers[k],
                footprint.centers[k + 1],
                f,
              );
              const location = entity.position.getValue(t);
              return location
                ? [location, C.Cartesian3.fromDegrees(...center)]
                : [];
            }, false),
          },
        });
      }
      viewer.entities.add({
        polyline: {
          show: new C.CallbackProperty(() => $("show-ground").checked, false),
          width: 1,
          material: color.withAlpha(0.6),
          clampToGround: true,
          positions: new C.CallbackProperty((time) => {
            const current = C.JulianDate.toDate(time).getTime();
            return samples
              .filter(
                (s) =>
                  Math.abs(s.time - current) <=
                  Number($("trail-minutes").value) * 60000,
              )
              .map((s) => s.ground);
          }, false),
        },
      });
    });
    points.forEach((point, index) => {
      const position = C.Cartesian3.fromDegrees(
        point.longitude,
        point.latitude,
        point.elevation,
      );
      viewer.entities.add({
        id: "study-point-" + index,
        name: point.name,
        position,
        point: {
          pixelSize: 9,
          color: C.Color.WHITE,
          outlineColor: C.Color.BLACK,
          outlineWidth: 1,
        },
      });
      for (const [name, satellite] of satellites) {
        const events = point.observations
          .filter((e) => e.satellite === name)
          .map((e) =>
            e.envelope_start
              ? [
                  Date.parse(e.epoch) - EVENT_LINK_HOLD_MS,
                  Date.parse(e.epoch) + EVENT_LINK_HOLD_MS,
                ]
              : [Date.parse(e.start), Date.parse(e.end)],
          );
        viewer.entities.add({
          polyline: {
            width: 2,
            material: C.Color.LIME,
            show: new C.CallbackProperty((time) => {
              const current = C.JulianDate.toDate(time).getTime();
              return (
                $("show-links").checked &&
                selected === index &&
                events.some(
                  ([left, right]) => current >= left && current < right,
                )
              );
            }, false),
            positions: new C.CallbackProperty((time) => {
              const location = satellite.position.getValue(time);
              return location ? [position, location] : [];
            }, false),
          },
        });
      }
    });
    viewer.selectedEntityChanged.addEventListener((entity) => {
      if (entity && entity.id.startsWith("study-point-"))
        selectPoint(Number(entity.id.slice(12)));
    });
    // A location chosen outside the map is centred in 2D or 3D, keeping the zoom.
    $("location").addEventListener("change", () => {
      const point = points[Number($("location").value)];
      focus[0] = point.longitude;
      focus[1] = point.latitude;
      if (viewer.isDestroyed() || viewer.scene.mode !== C.SceneMode.SCENE2D)
        return frameEarth();
      // A 2D view's height is its frustum width (Camera.setView in 2D).
      const frustum = viewer.camera.frustum;
      viewer.camera.setView({
        destination: C.Cartesian3.fromDegrees(
          point.longitude,
          point.latitude,
          frustum.right - frustum.left,
        ),
      });
      viewer.scene.requestRender();
    });
    function setMode(mode) {
      pause();
      if (mode === "2d") viewer.scene.morphTo2D(0);
      else {
        viewer.scene.morphTo3D(0);
        autoFrame = true;
        frameEarth();
      }
      $("view-2d").setAttribute("aria-pressed", String(mode === "2d"));
      $("view-3d").setAttribute("aria-pressed", String(mode === "3d"));
      viewer.scene.requestRender();
    }
    $("view-2d").onclick = () => setMode("2d");
    $("view-3d").onclick = () => setMode("3d");
    $("show-orbits").onchange = () => {
      for (const satellite of satellites.values())
        satellite.path.show = $("show-orbits").checked;
      viewer.scene.requestRender();
    };
    for (const id of ["show-ground", "show-links", "show-footprints"])
      $(id).onchange = () => viewer.scene.requestRender();
    $("globe").hidden = false;
    viewer.resize();
    setTime(seconds);
    // Do not report a usable scene while terrain workers have not rendered Earth.
    // Cesium's transfer probe can otherwise stall without a renderError event.
    await new Promise((resolve, reject) => {
      let remove = () => {};
      let timer = null,
        remaining = 30000,
        visibleSince = 0;
      const finish = (error) => {
        clearTimeout(timer);
        remove();
        document.removeEventListener("visibilitychange", updateDeadline);
        window.removeEventListener("tatc-map-show", updateDeadline);
        window.removeEventListener("tatc-map-hide", updateDeadline);
        window.removeEventListener("tatc-pane-visibility", updateDeadline);
        cancelGlobeLoad = () => {};
        if (error) reject(error);
        else resolve();
      };
      // A hidden map/background document can stop producing render frames.
      // Keep the same loading budget, counting only time the map is visible.
      function updateDeadline() {
        if (timer !== null) {
          remaining -= performance.now() - visibleSince;
          clearTimeout(timer);
          timer = null;
        }
        if (!mapVisible()) return;
        visibleSince = performance.now();
        timer = setTimeout(
          () =>
            finish(
              new Error(
                "Globe loading timed out. Check that the viewer assets and workers are allowed.",
              ),
            ),
          Math.max(0, remaining),
        );
        viewer.scene.requestRender();
      }
      document.addEventListener("visibilitychange", updateDeadline);
      window.addEventListener("tatc-map-show", updateDeadline);
      window.addEventListener("tatc-map-hide", updateDeadline);
      window.addEventListener("tatc-pane-visibility", updateDeadline);
      cancelGlobeLoad = () => finish(new Error("Globe loading cancelled."));
      remove = viewer.scene.postRender.addEventListener(() => {
        if (viewer.scene.globe.tilesLoaded) finish();
      });
      updateDeadline();
    });
    if (failed) return;
    $("status").hidden = true;
    for (const id of ["play", "time-slider", "speed"]) $(id).disabled = false;
    resizeHost();
  }
  let started = false;
  function showMap() {
    if (started) {
      if (viewer && !failed) {
        viewer.resize();
        viewer.scene.requestRender();
      }
      return;
    }
    started = true;
    initializeGlobe()
      .then(() => {
        if (DATA.ui?.presentation === "map2d" && !failed) $("view-2d").click();
      })
      .catch((error) => fail("Unable to show visualization: " + error.message));
  }
  window.addEventListener("tatc-map-show", showMap);
  if (window.tatcMapRequested) showMap();
  window.addEventListener("tatc-seek", (event) => {
    pause();
    setTime(event.detail);
  });
  window.addEventListener("tatc-trail", (event) => {
    if (!viewer || failed) return;
    for (const entity of viewer.entities.values)
      if (entity.path) {
        entity.path.leadTime = event.detail;
        entity.path.trailTime = event.detail;
      }
    viewer.scene.requestRender();
  });
  window.addEventListener("pagehide", () => {
    failed = true;
    cancelGlobeLoad();
    sceneObserver?.disconnect();
    pause();
    sizeObserver.disconnect();
    if (viewer && !viewer.isDestroyed()) viewer.destroy();
  });
})().catch((error) => {
  document.getElementById("status").textContent =
    "Unable to show visualization: " + error.message;
  parent.postMessage(
    {
      type: "ui-size-change",
      payload: {
        height: Math.ceil(document.body.getBoundingClientRect().height),
      },
    },
    "*",
  );
});
