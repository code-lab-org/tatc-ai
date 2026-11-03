// Research views use computed values. Cesium is loaded only when Map is selected.
(async () => {
  const data = await window.tatcStudy;
  const $ = (id) => document.getElementById(id);
  const coverage = !!data.points?.length,
    dataset = data.dataset;
  let pointIndex = 0,
    rows = [],
    currentTab = "overview";
  const ui = data.ui || {};
  if (window.name === "tatc-study-frame")
    document.body.classList.add("in-pane");
  document.body.classList.toggle("has-dataset", !!dataset);
  const text = (tag, value, className) => {
    const e = document.createElement(tag);
    e.textContent = value;
    if (className) e.className = className;
    return e;
  };
  const fmt = (value, digits = 1) =>
    value == null
      ? "—"
      : Number(value).toLocaleString(undefined, {
          ...(value !== 0 && Math.abs(value) < 0.1
            ? { maximumSignificantDigits: 3 }
            : { maximumFractionDigits: digits }),
        });
  const utc = (value) =>
    new Date(value)
      .toISOString()
      .replace("T", " ")
      .replace(/\.\d+Z$/, " UTC");
  $("view-title").textContent =
    dataset?.title || (coverage ? "Coverage study" : "Orbit study");
  $("study-subtitle").textContent = dataset
    ? dataset.source
    : `${utc(data.scenario.start_time)} — ${utc(data.scenario.end_time)}`;
  $("study-subtitle").hidden = !!dataset && !dataset.source?.trim();
  $("study-context").textContent = dataset
    ? `${dataset.labels.length} ${dataset.labels.length === 1 ? "record" : "records"}`
    : `${data.tracks.length} ${data.tracks.length === 1 ? "satellite" : "satellites"}${coverage ? ` · ${data.points.length} locations` : ""}`;
  $("chart-title").parentElement.hidden = !!dataset;
  $("metrics-grid").hidden = !coverage;
  $("metric-label").hidden = !coverage;
  $("distribution").hidden = !coverage;
  $("table-kind-label").hidden = !coverage;
  $("time-controls").hidden = !!dataset;
  document.querySelector('[data-tab="map"]').hidden = !!dataset;
  document.querySelector('[data-tab="inputs"]').hidden = !!dataset;
  function showTab(tab) {
    currentTab = tab;
    document.body.dataset.view = tab;
    for (const name of ["overview", "map", "table", "inputs"])
      $(name + "-panel").hidden = name !== tab;
    for (const button of document.querySelectorAll("[data-tab]"))
      button.setAttribute("aria-pressed", String(button.dataset.tab === tab));
    $("time-controls").hidden = !!dataset || tab === "inputs";
    $("coverage").hidden = !coverage || tab === "inputs";
    $("study-timeline").hidden = !!dataset || tab === "inputs";
    if (tab === "map") {
      window.tatcMapRequested = true;
      window.dispatchEvent(new Event("tatc-map-show"));
    } else window.dispatchEvent(new Event("tatc-map-hide"));
    if (tab === "table") renderTable();
  }
  for (const button of document.querySelectorAll("[data-tab]"))
    button.onclick = () => showTab(button.dataset.tab);
  $("visual").hidden = false;
  function selectPoint(index) {
    $("location").value = String(index);
    $("location").dispatchEvent(new Event("change"));
  }
  function bars(container, entries, suffix, click) {
    container.replaceChildren();
    const peak = Math.max(0, ...entries.map((e) => e.value ?? 0));
    // A labelled, shared numerical axis distinguishes measurements from progress.
    const rawStep = (peak || 1) / 4;
    const magnitude = 10 ** Math.floor(Math.log10(rawStep));
    const step =
      ([1, 2, 2.5, 5, 10].find((n) => n * magnitude >= rawStep) || 10) *
      magnitude;
    const max = click ? step * 4 : peak;
    const min = Math.min(0, ...entries.map((e) => e.value ?? 0));
    const span = max - min || 1;
    entries.forEach((entry, i) => {
      const row = text(click ? "button" : "div", "", "bar-row");
      if (click) {
        row.type = "button";
        row.onclick = () => click(i);
        row.setAttribute("aria-pressed", String(pointIndex === i));
      }
      row.append(text("span", entry.name, "bar-label"));
      const track = text("span", "", "bar-track"),
        fill = text("span", "", "bar-fill");
      const origin = (-min / span) * 100,
        endpoint = (((entry.value ?? 0) - min) / span) * 100;
      fill.style.left = Math.min(origin, endpoint) + "%";
      fill.style.width = Math.abs(endpoint - origin) + "%";
      track.append(fill);
      row.append(
        track,
        text(
          "span",
          `${fmt(entry.value)}${entry.value == null ? "" : suffix}`,
          "bar-value",
        ),
      );
      container.append(row);
    });
    if (click) {
      const axis = text("div", "", "comparison-axis");
      axis.setAttribute("aria-label", "Value axis");
      const scale = text("div", "", "comparison-ticks");
      for (let i = 0; i <= 4; i++)
        scale.append(text("span", fmt((max * i) / 4, 2)));
      axis.append(
        text("span", ""),
        scale,
        text("span", suffix.trim() || "count", "axis-unit"),
      );
      container.append(axis);
    }
  }
  function barChart(container, chart) {
    const ns = "http://www.w3.org/2000/svg";
    const colors = [
      "#8bbcc2",
      "#baa17a",
      "#c7a9e1",
      "#9fc991",
      "#d18b9b",
      "#a5badb",
      "#dad792",
      "#a3c4be",
    ];
    const exact = (value) => (value == null ? "—" : String(value));
    const label = (value, unit = chart.unit) =>
      `${exact(value)}${value != null && unit ? " " + unit : ""}`;
    const values = chart.series.flatMap((s) =>
      s.values.filter((v) => v != null),
    );
    const low = Math.min(0, ...values),
      high = Math.max(0, ...values);
    // Include zero, with legible numerical ticks (e.g. 0, 1, 2, 3, 4 hours).
    const interval = Math.max(Number.MIN_VALUE, high / 4 - low / 4);
    const magnitude = Math.max(
      Number.MIN_VALUE,
      10 ** Math.floor(Math.log10(interval)),
    );
    const multiple =
      [1, 2, 2.5, 5, 10].find((n) => n * magnitude >= interval) || 10;
    const step = high === low ? 1 : multiple * magnitude;
    const lower = Math.floor(low / step) * step;
    const upper = Math.ceil(high / step) * step;
    const min = Number.isFinite(lower) ? lower : low;
    const max = high === low ? 1 : Number.isFinite(upper) ? upper : high;
    const normalizer = Math.max(Math.abs(min), Math.abs(max));
    let lastWidth;
    function draw() {
      const available = container.clientWidth || 640;
      if (available === lastWidth) return;
      lastWidth = available;
      container.replaceChildren();
      const left = 62,
        right = 20,
        top = 52,
        bottom = 300;
      const groupMinimum = Math.max(82, chart.series.length * 26 + 18);
      const width = Math.max(
        320,
        available,
        left + right + chart.labels.length * groupMinimum,
      );
      const plotWidth = width - left - right;
      const y = (value) =>
        bottom -
        ((value / normalizer - min / normalizer) /
          (max / normalizer - min / normalizer)) *
          (bottom - top);
      const svg = document.createElementNS(ns, "svg");
      svg.classList.add("data-plot");
      svg.setAttribute("viewBox", `0 0 ${width} 390`);
      svg.style.minWidth = `${width}px`;
      svg.setAttribute("role", "group");
      svg.setAttribute(
        "aria-label",
        `${chart.title}. Bar chart; values in ${chart.unit || "the supplied units"}.`,
      );
      function element(tag, attributes, value) {
        const e = document.createElementNS(ns, tag);
        for (const [key, v] of Object.entries(attributes))
          e.setAttribute(key, v);
        if (value != null) e.textContent = value;
        svg.append(e);
        return e;
      }
      element("title", {}, chart.title);
      const metric = chart.series.length === 1 ? chart.series[0].name : "Value";
      element(
        "text",
        { x: left, y: 22, class: "plot-axis-title" },
        `${metric}${chart.unit ? " (" + chart.unit + ")" : ""}`,
      );
      const tickCount = Math.min(10, Math.round(max / step - min / step));
      for (let i = 0; i <= tickCount; i++) {
        const tick = Number((min + i * step).toPrecision(12));
        element("line", {
          x1: left,
          x2: width - right,
          y1: y(tick),
          y2: y(tick),
          class: tick === 0 ? "plot-zero" : "grid-line",
        });
        element(
          "text",
          {
            x: left - 12,
            y: y(tick) + 4,
            "text-anchor": "end",
            class: "plot-tick",
          },
          Math.abs(tick) >= 1e6 ? tick.toExponential(1) : fmt(tick, 6),
        );
      }
      element("line", {
        x1: left,
        x2: left,
        y1: top,
        y2: bottom,
        class: "plot-axis",
      });
      const groupWidth = plotWidth / chart.labels.length;
      const clusterWidth = Math.min(
        groupWidth * 0.62,
        chart.series.length * 100,
      );
      const slot = clusterWidth / chart.series.length;
      const barWidth = slot * 0.82;
      const inspection = text(
        "p",
        "Point to a bar or focus it to inspect the exact value.",
        "plot-inspection",
      );
      inspection.setAttribute("aria-live", "polite");
      chart.labels.forEach((category, index) => {
        const center = left + groupWidth * (index + 0.5);
        chart.series.forEach((series, seriesIndex) => {
          const value = series.values[index];
          const x = center - clusterWidth / 2 + seriesIndex * slot + slot / 2;
          const detail = `${category} · ${series.name}: ${value == null ? "no value supplied" : label(value)}`;
          if (value != null) {
            const mark = element("rect", {
              x: x - barWidth / 2,
              y: Math.min(y(0), y(value)),
              width: barWidth,
              height: Math.abs(y(value) - y(0)),
              fill: colors[seriesIndex],
              rx: 2,
              class: "plot-bar",
              tabindex: 0,
              role: "img",
              "aria-label": detail,
            });
            const title = document.createElementNS(ns, "title");
            title.textContent = detail;
            mark.append(title);
            mark.onmouseenter = mark.onfocus = () => {
              inspection.textContent = detail;
            };
          }
          const annotation = element(
            "text",
            {
              x,
              y: value == null ? y(0) - 10 : y(value) + (value < 0 ? 17 : -10),
              "text-anchor": "middle",
              class: "plot-value",
              "data-value": value == null ? "" : String(value),
            },
            exact(value),
          );
          const title = document.createElementNS(ns, "title");
          title.textContent = detail;
          annotation.append(title);
        });
        // Keep category text readable without shrinking the entire chart on mobile.
        const limit = Math.max(8, Math.floor(groupWidth / 7));
        const words = category.split(/\s+/);
        const lines = [""];
        words.forEach((word) => {
          const i = lines.length - 1;
          if (lines[i] && (lines[i] + " " + word).length > limit)
            lines.push(word);
          else lines[i] += (lines[i] ? " " : "") + word;
        });
        const categoryText = element("text", {
          x: center,
          y: bottom + 28,
          "text-anchor": "middle",
          class: "plot-category",
        });
        lines.slice(0, 2).forEach((line, i) => {
          const span = document.createElementNS(ns, "tspan");
          span.setAttribute("x", center);
          span.setAttribute("dy", i ? 16 : 0);
          span.textContent =
            line.length > limit
              ? line.slice(0, limit - 1) + "…"
              : line + (i === 1 && lines.length > 2 ? "…" : "");
          categoryText.append(span);
        });
        const title = document.createElementNS(ns, "title");
        title.textContent = category;
        categoryText.append(title);
      });
      if (chart.x_label)
        element(
          "text",
          {
            x: left + plotWidth / 2,
            y: 378,
            "text-anchor": "middle",
            class: "plot-axis-title",
          },
          chart.x_label,
        );
      const scroll = text("div", "", "plot-scroll");
      scroll.append(svg);
      container.append(scroll);
      if (chart.series.length > 1) {
        const legend = text("div", "", "chart-legend");
        chart.series.forEach((s, i) => {
          const item = text("span", s.name);
          item.style.color = colors[i];
          legend.append(item);
        });
        container.append(legend);
      }
      container.append(inspection);
    }
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(container);
    addEventListener("pagehide", () => observer.disconnect(), { once: true });
  }

  function lineChart(container, labels, series, unit) {
    container.replaceChildren();
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 640 240");
    svg.setAttribute("role", "img");
    svg.setAttribute(
      "aria-label",
      `Series chart, ${unit || "values"}; exact values in Table`,
    );
    const values = series.flatMap((s) => s.values.filter((v) => v != null));
    if (!values.length) {
      container.append(
        text("p", "No measured values in this series.", "muted"),
      );
      return;
    }
    const lower = Math.min(...values),
      upper = Math.max(...values);
    const padding = (upper - lower || Math.abs(upper) || 1) * 0.05;
    const min = lower - padding,
      max = upper + padding,
      span = max - min;
    for (let i = 0; i <= 4; i++) {
      const y = 20 + i * 45;
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", "52");
      line.setAttribute("x2", "625");
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
      line.setAttribute("class", "grid-line");
      svg.append(line);
      const label = document.createElementNS(ns, "text");
      label.setAttribute("x", "44");
      label.setAttribute("y", y + 4);
      label.setAttribute("text-anchor", "end");
      label.textContent = fmt(max - (i * span) / 4);
      svg.append(label);
    }
    const colors = [
      "#8bbcc2",
      "#baa17a",
      "#c7a9e1",
      "#9fc991",
      "#d18b9b",
      "#a5badb",
      "#dad792",
      "#a3c4be",
    ];
    series.forEach((s, index) => {
      let path = "",
        pen = false;
      s.values.forEach((value, i) => {
        if (value == null) {
          pen = false;
          return;
        }
        const x = 52 + (i * 573) / Math.max(1, labels.length - 1),
          y = 200 - ((value - min) / span) * 180;
        path += `${pen ? "L" : "M"}${x},${y} `;
        pen = true;
        if (
          labels.length <= 60 ||
          (s.values[i - 1] == null && s.values[i + 1] == null)
        ) {
          const dot = document.createElementNS(ns, "circle");
          dot.setAttribute("cx", x);
          dot.setAttribute("cy", y);
          dot.setAttribute("r", "2.5");
          const tip = document.createElementNS(ns, "title");
          tip.textContent = `${labels[i]} · ${s.name}: ${value}${unit ? " " + unit : ""}`;
          dot.append(tip);
          dot.setAttribute("fill", colors[index]);
          svg.append(dot);
        }
      });
      const line = document.createElementNS(ns, "path");
      line.setAttribute("d", path);
      line.setAttribute("stroke", colors[index]);
      line.setAttribute("fill", "none");
      line.setAttribute("stroke-width", "2");
      svg.append(line);
    });
    container.append(svg);
    const legend = text("div", "", "chart-legend");
    series.forEach((s, i) => {
      const item = text("span", s.name);
      item.style.color = colors[i];
      legend.append(item);
    });
    container.append(legend);
    container.append(
      text(
        "p",
        `${labels[0] || ""} → ${labels.at(-1) || ""}${unit ? " · " + unit : ""}`,
        "muted",
      ),
    );
  }
  function overview() {
    if (dataset) {
      $("chart-title").textContent = dataset.title;
      if (dataset.kind === "bar") {
        barChart($("comparison-chart"), dataset);
      } else
        lineChart(
          $("comparison-chart"),
          dataset.labels,
          dataset.series,
          dataset.unit,
        );
      const hasMissing = dataset.series.some((s) => s.values.includes(null));
      $("chart-note").textContent = [
        hasMissing ? "— indicates a missing value." : "",
        dataset.kind === "line"
          ? "Labels are equally spaced in supplied order."
          : "",
      ]
        .filter(Boolean)
        .join(" ");
      $("chart-note").hidden = !$("chart-note").textContent;
      return;
    }
    if (!coverage) {
      $("chart-title").textContent = "Altitude over time";
      lineChart(
        $("comparison-chart"),
        data.tracks[0].samples.map((s) => utc(s[0])),
        data.tracks.map((t) => ({
          name: t.name,
          values: t.samples.map((s) => s[3] / 1000),
        })),
        "km",
      );
      $("chart-note").textContent =
        "Sampled, propagated positions. This chart does not represent sensor coverage.";
      return;
    }
    const key = $("metric").value;
    const scale =
      key === "coverage_fraction" ? 100 : key.endsWith("_seconds") ? 1 / 60 : 1;
    bars(
      $("comparison-chart"),
      data.points.map((p) => ({
        name: p.name,
        value: p.metrics[key] == null ? null : p.metrics[key] * scale,
      })),
      key === "coverage_fraction"
        ? "%"
        : key.endsWith("_seconds")
          ? " min"
          : "",
      selectPoint,
    );
    $("chart-note").textContent = {
      longest_gap_seconds:
        "Study points only. Longest gaps include the study window boundaries.",
      coverage_fraction:
        "Study time covered by at least one instrument, at each study point.",
      observation_count:
        "Individual opportunities at each study point. Overlapping opportunities are counted separately.",
      mean_complete_revisit_seconds:
        "Mean gap bounded by coverage on both sides. — means no complete gap was measured.",
    }[key];
    const p = data.points[pointIndex],
      m = p.metrics;
    $("metrics-grid").replaceChildren();
    for (const [label, value] of [
      ["Location", p.name],
      ["Observations", fmt(m.observation_count, 0)],
      ["Time covered", fmt(m.coverage_fraction * 100) + "%"],
      ["Longest gap", fmt(m.longest_gap_seconds / 60) + " min"],
      [
        "Mean complete revisit",
        m.mean_complete_revisit_seconds == null
          ? "—"
          : fmt(m.mean_complete_revisit_seconds / 60) + " min",
      ],
      ["Covered duration", fmt(m.covered_seconds / 60) + " min"],
    ]) {
      const cell = text("div", "", "metric");
      cell.append(text("small", label), text("strong", value));
      $("metrics-grid").append(cell);
    }
    const longest = Math.max(1, ...p.gaps.map((g) => g.duration_seconds / 60)),
      bins = Array.from({ length: 5 }, (_, i) => ({
        name: `${fmt((i * longest) / 5)}–${fmt(((i + 1) * longest) / 5)} min`,
        value: 0,
      }));
    p.gaps.forEach(
      (g) =>
        bins[Math.min(4, Math.floor((g.duration_seconds / 60 / longest) * 5))]
          .value++,
    );
    bars($("gap-chart"), bins, " gaps");
  }
  function tableRows() {
    if (dataset)
      return [
        [
          dataset.x_label || "Label",
          ...dataset.series.map(
            (s) => s.name + (dataset.unit ? " (" + dataset.unit + ")" : ""),
          ),
        ],
        ...dataset.labels.map((label, i) => [
          label,
          ...dataset.series.map((s) => s.values[i]),
        ]),
      ];
    if (!coverage)
      return [
        [
          "Satellite",
          "Time (UTC)",
          "Longitude (°)",
          "Latitude (°)",
          "Altitude (km)",
        ],
        ...data.tracks.flatMap((t) =>
          t.samples.map((s) => [t.name, s[0], s[1], s[2], s[3] / 1000]),
        ),
      ];
    const p = data.points[pointIndex];
    if ($("table-kind").value === "observations")
      return [
        ["Satellite", "Instrument", "Start (UTC)", "End (UTC)", "Duration (s)"],
        ...p.observations.map((e) => [
          e.satellite,
          e.instrument,
          e.start,
          e.end,
          e.duration_seconds,
        ]),
      ];
    if ($("table-kind").value === "gaps")
      return [
        ["Start (UTC)", "End (UTC)", "Duration (min)", "Boundary gap"],
        ...p.gaps.map((g) => [
          g.start,
          g.end,
          g.duration_seconds / 60,
          g.boundary ? "Yes" : "No",
        ]),
      ];
    return [
      [
        "Location",
        "Observations",
        "Time covered (%)",
        "Longest gap (min)",
        "Mean complete revisit (min)",
      ],
      ...data.points.map((p) => [
        p.name,
        p.metrics.observation_count,
        p.metrics.coverage_fraction * 100,
        p.metrics.longest_gap_seconds / 60,
        p.metrics.mean_complete_revisit_seconds == null
          ? null
          : p.metrics.mean_complete_revisit_seconds / 60,
      ]),
    ];
  }
  function renderTable() {
    rows = tableRows();
    const table = document.createElement("table");
    const head = document.createElement("thead"),
      tr = document.createElement("tr");
    const numericColumns = rows[0].map((_, i) =>
      rows.slice(1).some((row) => typeof row[i] === "number"),
    );
    rows[0].forEach((v, i) => {
      const heading = text("th", v, numericColumns[i] ? "numeric" : "");
      heading.scope = "col";
      tr.append(heading);
    });
    head.append(tr);
    table.append(head);
    const body = document.createElement("tbody");
    rows.slice(1).forEach((row, index) => {
      const tr = document.createElement("tr");
      row.forEach((value, column) =>
        tr.append(
          text(
            "td",
            value == null
              ? "—"
              : typeof value === "number"
                ? fmt(value, 4)
                : value,
            numericColumns[column] ? "numeric" : "",
          ),
        ),
      );
      if (coverage && $("table-kind").value !== "locations") {
        tr.tabIndex = 0;
        tr.title = "Inspect this interval";
        const select = () => {
          const records =
            $("table-kind").value === "gaps"
              ? data.points[pointIndex].gaps
              : data.points[pointIndex].observations;
          const e = records[index];
          window.dispatchEvent(
            new CustomEvent("tatc-seek", {
              detail:
                ((Date.parse(e.start) + Date.parse(e.end)) / 2 -
                  Date.parse(data.scenario.start_time)) /
                1000,
            }),
          );
          $("event-detail").textContent =
            `${e.start} — ${e.end} · ${fmt(e.duration_seconds)} s${e.boundary ? " · touches window boundary" : ""}`;
          $("event-detail").title = $("event-detail").textContent;
        };
        tr.onclick = select;
        tr.onkeydown = (e) => {
          if (e.key === "Enter") select();
        };
      }
      body.append(tr);
    });
    table.append(body);
    $("result-table").replaceChildren(table);
  }
  $("metric").onchange = overview;
  $("table-kind").onchange = renderTable;
  addEventListener("tatc-point", (event) => {
    pointIndex = event.detail;
    overview();
    if (currentTab === "table") renderTable();
  });
  $("trail-minutes").onchange = () => {
    if ($("trail-minutes").checkValidity())
      window.dispatchEvent(
        new CustomEvent("tatc-trail", {
          detail: Number($("trail-minutes").value) * 60,
        }),
      );
  };
  $("export-csv").onclick = () => {
    const quote = (value) =>
      '"' +
      (typeof value === "string"
        ? value.replace(/^[=+@\-]/, "'$&")
        : String(value ?? "")
      ).replaceAll('"', '""') +
      '"';
    const csv = tableRows()
      .map((row) => row.map(quote).join(","))
      .join("\r\n");
    const url = URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "tatc-study.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  if (!dataset) {
    const draft = structuredClone(data.request || {}),
      fields = $("study-fields");
    function field(
      parent,
      label,
      value,
      apply,
      options = {},
      validate = () => "",
    ) {
      const wrap = document.createElement("label");
      wrap.append(text("span", label));
      const input = document.createElement("input");
      Object.assign(input, { type: "number", step: "any", value, ...options });
      input.required = true;
      input.setCustomValidity(validate(input.valueAsNumber));
      input.oninput = () => {
        input.setCustomValidity(validate(input.valueAsNumber));
        apply(input.type === "number" ? Number(input.value) : input.value);
        $("run-status").textContent =
          "Draft changed. Displayed results still use the previous inputs.";
      };
      wrap.append(input);
      parent.append(wrap);
      return input;
    }
    function choice(parent, label, value, apply, options) {
      const wrap = document.createElement("label");
      wrap.append(text("span", label));
      const select = document.createElement("select");
      for (const [name, option] of options)
        select.add(
          new Option(name, JSON.stringify(option), false, option === value),
        );
      select.onchange = () => {
        apply(JSON.parse(select.value));
        $("run-status").textContent =
          "Draft changed. Displayed results still use the previous inputs.";
      };
      wrap.append(select);
      parent.append(wrap);
    }
    // TATC returns durations in ISO 8601, such as PT1M30S.
    function durationSeconds(value) {
      const [, d = 0, h = 0, m = 0, sec = 0] =
        /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?$/.exec(
          value || "",
        ) || [];
      return ((Number(d) * 24 + Number(h)) * 60 + Number(m)) * 60 + Number(sec);
    }
    const times = text("fieldset", "", "time-fields");
    times.append(text("legend", "Analysis window · UTC"));
    fields.append(times);
    const scenario = draft.scenario;
    const start = scenario?.start_time || draft.start_time;
    // datetime-local omits zero seconds; use the server's UTC representation.
    const utcInput = (value) =>
      value ? new Date(value + "Z").toISOString().replace(/\.000Z$/, "Z") : "";
    field(
      times,
      "Start",
      start?.slice(0, 19) || "",
      (v) => {
        if (scenario) scenario.start_time = utcInput(v);
        else draft.start_time = utcInput(v);
      },
      { type: "datetime-local", step: "1" },
    );
    if (scenario)
      field(
        times,
        "End",
        scenario.end_time.slice(0, 19),
        (v) => (scenario.end_time = utcInput(v)),
        { type: "datetime-local", step: "1" },
      );
    else
      field(
        times,
        "Duration",
        draft.duration || "1 hour",
        (v) => (draft.duration = v),
        { type: "text" },
      );
    field(
      times,
      "Orbit display step (seconds)",
      scenario?.display_step_seconds || draft.display_step_seconds || 120,
      (v) => {
        if (scenario) scenario.display_step_seconds = v;
        else draft.display_step_seconds = v;
      },
      { min: "30", max: "300", step: "1" },
    );
    if (scenario) {
      scenario.satellites.forEach((sat) => {
        const group = text("fieldset", "", "satellite-fields");
        group.append(text("legend", sat.name));
        fields.append(group);
        if (sat.orbit.type === "circular") {
          const altitudeKey =
            "mean_altitude" in sat.orbit ? "mean_altitude" : "altitude";
          const normalizedOrbit = data.scenario.satellites?.find(
            (s) => s.name === sat.name,
          )?.orbit;
          field(
            group,
            "Altitude (km)",
            sat.orbit[altitudeKey] / 1000,
            (v) => (sat.orbit[altitudeKey] = v * 1000),
            { min: "0" },
          );
          field(
            group,
            "Inclination (°)",
            sat.orbit.inclination ?? normalizedOrbit?.inclination,
            (v) => (sat.orbit.inclination = v),
            { min: "0", max: "180" },
            (v) =>
              v >= 180 ? "Inclination must be less than 180 degrees." : "",
          );
        } else
          group.append(
            text(
              "p",
              `Orbit model: ${sat.orbit.type}. Orbital elements are preserved.`,
              "muted",
            ),
          );
        sat.instruments.forEach((inst, index) => {
          group.append(text("p", inst.name, "instrument-name"));
          field(
            group,
            "Field of regard (°)",
            inst.field_of_regard,
            (v) => (inst.field_of_regard = v),
            { min: "0", max: "360" },
            (v) => (v <= 0 ? "Field of regard must be greater than zero." : ""),
          );
          // Pointing limits accesses to the footprint, so it changes results.
          if ("cross_track_field_of_view" in inst)
            for (const [key, label, min, max] of [
              ["cross_track_field_of_view", "Cross-track view (°)", 0, 180],
              ["along_track_field_of_view", "Along-track view (°)", 0, 180],
              ["roll_angle", "Roll (°)", -180, 180],
              ["pitch_angle", "Pitch (°)", -180, 180],
            ])
              field(
                group,
                label,
                inst[key] ?? 0,
                (v) => (inst[key] = v),
                { min: String(min), max: String(max) },
                (v) =>
                  min === 0 && v <= 0
                    ? "Field of view must be greater than zero."
                    : "",
              );
          const normalized = data.scenario.satellites?.find(
            (s) => s.name === sat.name,
          )?.instruments[index];
          field(
            group,
            "Minimum access (s)",
            durationSeconds(normalized?.min_access_time),
            (v) => (inst.min_access_time = v),
            { min: "0" },
          );
          for (const [key, label] of [
            ["req_self_sunlit", "Satellite lighting"],
            ["req_target_sunlit", "Target lighting"],
          ])
            choice(group, label, inst[key] ?? null, (v) => (inst[key] = v), [
              ["Any", null],
              ["Sunlit", true],
              ["Eclipse", false],
            ]);
        });
      });
      scenario.points.forEach((point) => {
        const group = text("fieldset", "", "point-fields");
        group.append(text("legend", point.name));
        fields.append(group);
        field(
          group,
          "Latitude (°)",
          point.latitude,
          (v) => (point.latitude = v),
          { min: "-90", max: "90" },
        );
        field(
          group,
          "Longitude (°)",
          point.longitude,
          (v) => (point.longitude = v),
          { min: "-180", max: "180" },
        );
        field(
          group,
          "Elevation (m)",
          point.elevation || 0,
          (v) => (point.elevation = v),
          { min: "-500", max: "10000" },
        );
      });
    } else
      fields.append(
        text(
          "p",
          "Satellites: " + (draft.satellite_identifiers || []).join(", "),
          "muted",
        ),
      );
    const facts = data.assumptions || [
      data.scenario.source || "TATC propagated orbit samples.",
      // Orbit studies: the provenance of each propagated element set.
      ...(data.scenario.satellites || []).map(
        (s) => `${s.name} (NORAD ${s.norad_id}): TLE epoch ${s.orbit_epoch}`,
      ),
    ];
    facts.forEach((value) => $("provenance").append(text("p", value)));
    if (data.software)
      $("provenance").append(
        text(
          "p",
          Object.entries(data.software)
            .map(([k, v]) => k + " " + v)
            .join(" · "),
        ),
      );
    $("study-form").onsubmit = (event) => {
      event.preventDefault();
      if (!draft.view) {
        $("run-status").textContent =
          "This saved result has no editable request. Create a new study through chat.";
        return;
      }
      if (
        scenario &&
        !(
          Date.parse(scenario.end_time) > Date.parse(scenario.start_time) &&
          Date.parse(scenario.end_time) - Date.parse(scenario.start_time) <=
            86400000
        )
      ) {
        $("run-status").textContent =
          "Choose a positive analysis window of at most 24 hours.";
        return;
      }
      const params = {
        ...draft,
        presentation:
          currentTab === "map"
            ? $("view-2d").getAttribute("aria-pressed") === "true"
              ? "map2d"
              : "simulation3d"
            : ui.presentation || "overview",
        workspace_id: ui.workspace_id,
      };
      $("run-status").textContent = "Sending updated parameters to chat…";
      $("study-notice").hidden = true;
      parent.postMessage(
        {
          type: "tatc-workspace:run",
          payload: { workspaceId: ui.workspace_id, params },
        },
        "*",
      );
      setTimeout(() => {
        if (
          $("run-status").textContent === "Sending updated parameters to chat…"
        )
          $("run-status").textContent =
            "Chat is not connected to this window. Open the study pane from the conversation to rerun.";
      }, 2000);
    };
    addEventListener("message", (event) => {
      if (
        event.source !== parent ||
        event.data?.payload?.workspaceId !== ui.workspace_id
      )
        return;
      if (event.data.type === "tatc-workspace:result-mismatch") {
        const message =
          "The returned study used different inputs. Your draft and previous results are unchanged. Check the chat response before submitting again.";
        $("study-notice").textContent = message;
        $("study-notice").hidden = false;
        $("run-status").textContent = message;
      }
      if (
        event.source === parent &&
        event.data?.type === "tatc-workspace:submission-error"
      )
        $("run-status").textContent =
          "Chat could not submit this study. Check the conversation and try again.";
      if (
        event.source === parent &&
        event.data?.type === "tatc-workspace:submitted"
      )
        $("run-status").textContent =
          "Submitted to chat. These results remain unchanged until the new TATC calculation returns.";
    });
  }
  overview();
  showTab(
    ["map2d", "simulation3d"].includes(ui.presentation)
      ? "map"
      : ui.presentation === "table"
        ? "table"
        : "overview",
  );
  if (ui.presentation === "timeline") {
    $("overview-panel").hidden = true;
    $("coverage").scrollIntoView?.();
  }
})().catch((error) => {
  const e = document.getElementById("research-error");
  e.hidden = false;
  e.textContent = error.message;
});
