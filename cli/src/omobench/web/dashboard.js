"use strict";

// The one benchmark dashboard, shared by the guided recipes and the advanced
// "connect your own server" panel. Both pages mount this component and feed it
// the same normalized view; neither keeps its own copy of the markup, the
// chart, the progress rules, or the terminal states.
//
// Everything here renders only what was actually observed. A missing value is
// "Pending" or "unavailable", never a zero; progress comes from the runner's
// own remaining_fraction, never from elapsed time; and 100% is reserved for a
// run whose evidence finalization also succeeded.
globalThis.BenchmarkDashboard = class BenchmarkDashboard {
  // "warmup"/"benchmarking"/"cooldown"/"saving" are only ever reached with real
  // support from GuideLLM's own accumulator.timings.status and progress hooks
  // (see telemetry.py); when telemetry is unavailable, execution collapses to
  // the generic "benchmarking" -- never invented.
  static MANAGED_STAGES = [
    { key: "checking", label: "Checking system" },
    { key: "downloading", label: "Downloading verified files" },
    { key: "verifying", label: "Verifying files" },
    { key: "loading_model", label: "Loading the model" },
    { key: "warmup", label: "Warming up" },
    { key: "benchmarking", label: "Running the benchmark" },
    { key: "cooldown", label: "Cooling down" },
    { key: "saving", label: "Saving results" },
  ];

  // A server the contributor runs themselves. OmoBench never downloaded its
  // weights, never started it and never loaded the model, so those stages do
  // not exist here: the only thing it observes is its own runner process.
  static EXTERNAL_STAGES = [
    { key: "starting_runner", label: "Starting the runner" },
    { key: "warmup", label: "Warming up" },
    { key: "benchmarking", label: "Running the benchmark" },
    { key: "cooldown", label: "Cooling down" },
    { key: "saving", label: "Saving results" },
  ];

  static STALE_TELEMETRY_SECONDS = 20;

  static markup({ stageNote, downloadProgress, serverLog }) {
    return `
      <div class="section-head dashboard-head">
        <div><h2 id="progress-heading">Running</h2><p id="dashboard-subtitle" class="muted small"></p></div>
        <span id="elapsed" class="pill"></span>
      </div>

      <div class="progress-bar-wrap" role="progressbar" id="progress-bar-wrap" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" aria-label="Benchmark progress">
        <div class="progress-bar-track"><div class="progress-bar-fill" id="progress-bar-fill"></div></div>
        <span id="progress-bar-label" class="progress-bar-label">Starting…</span>
      </div>

      <div class="performance-console">
        <div class="throughput-score">
          <p class="eyebrow">OUTPUT THROUGHPUT</p>
          <div class="score-value" id="live-throughput">Pending</div>
          <p class="score-unit">tokens / second</p>
          <p class="score-note">Cumulative average across the run.<br>Measured-window results appear below after completion.</p>
          <div class="score-divider"></div>
          <dl class="latency-pair"><div><dt>First token, mean</dt><dd id="live-ttft">Pending</dd></div><div><dt>Request time, mean</dt><dd id="live-request-latency">Pending</dd></div></dl>
        </div>
        <div class="chart-wrap">
          <div class="section-head chart-head"><div><p class="eyebrow">PERFORMANCE OVER TIME</p><h3>Output throughput</h3></div><span class="chart-key"><i></i> tok/s</span></div>
          <p class="hint" id="chart-label">Cumulative average since the first request</p>
          <div id="chart-empty" class="chart-empty"><span class="waiting-mark" aria-hidden="true">▥</span><strong>Waiting for the first measurement</strong><span>Your graph appears as the runner reports real samples.</span></div>
          <svg id="chart-svg" viewBox="0 0 800 240" preserveAspectRatio="none" role="img" aria-label="Output tokens per second over elapsed time" hidden>
            <defs><linearGradient id="throughput-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f7bd55" stop-opacity=".25"/><stop offset="100%" stop-color="#f7bd55" stop-opacity="0"/></linearGradient></defs>
            <path class="chart-grid" d="M52 24H780M52 112H780M52 200H780"/>
            <text id="chart-y-max" x="42" y="28" text-anchor="end">0</text><text id="chart-y-mid" x="42" y="116" text-anchor="end">0</text><text x="42" y="204" text-anchor="end">0</text>
            <text x="52" y="229">0m</text><text id="chart-x-end" x="780" y="229" text-anchor="end">0m</text>
            <polygon id="chart-area" points="" fill="url(#throughput-gradient)"/>
            <polyline id="chart-line" points=""/>
            <line id="chart-cursor" x1="52" y1="24" x2="52" y2="200"/>
            <circle id="chart-point" cx="52" cy="200" r="4"/>
          </svg>
          <div class="chart-inspector" id="chart-inspector" hidden><label for="chart-scrub">Inspect a sample</label><input id="chart-scrub" type="range" min="0" max="0" value="0" step="1" aria-label="Inspect throughput samples"><output id="chart-readout" for="chart-scrub"></output></div>
        </div>
      </div>
      <dl class="dashboard-facts" id="dashboard-facts">
        <div><dt>Elapsed</dt><dd id="fact-elapsed">Pending</dd></div>
        <div><dt>Estimated remaining</dt><dd id="fact-eta">Estimating…</dd></div>
        <div><dt>Requests finished</dt><dd id="fact-requests">Pending</dd></div>
        <div><dt>Maximum duration</dt><dd id="fact-max-duration">Pending</dd></div>
      </dl>
      <div class="request-health"><span>All phases, warmup and cooldown included</span><span>Successful <strong id="live-successful">Pending</strong></span><span>Errored <strong id="live-errors">Pending</strong></span><span>Cancelled <strong id="live-cancelled">Pending</strong></span><span>Declared measured target <strong id="live-measured-target">Pending</strong></span></div>
      <p id="eta-basis-note" class="hint">The run ends at its configured request limit or its time limit, whichever comes first, so a run bounded by time finishes below the request limit. The counts above are every request the runner finished, warmup and cooldown included; the published measurements come from the measured window alone, which is why the final successful/attempted figures are smaller.</p>
      <p id="measured-window-note" class="hint" hidden></p>
      <div class="stage-heading"><h3>Run stages</h3><span class="hint">${stageNote}</span></div>
      <ol class="stage-list" id="stage-list"></ol>
      ${downloadProgress ? '<div id="download-progress"></div>' : ""}

      <p id="run-error" class="notice error" role="status" hidden></p>
      <div class="actions" id="dashboard-actions">
        <button id="cancel-button" type="button" class="danger">Cancel</button>
        <button id="start-over-button" type="button" class="secondary" hidden>Choose a different test</button>
      </div>

      <details id="live-log-details" open>
        <summary>Live log <span class="hint" id="log-connection-status">Refreshes automatically</span></summary>
        <div class="log-toolbar">
          <button id="jump-to-latest-button" type="button" class="quiet" hidden>Jump to latest ↓</button>
        </div>
        <pre id="benchmark-log" tabindex="0">Waiting…</pre>
      </details>
      <details id="technical-details-progress">
        <summary>Technical details <span class="hint">Raw progress data${serverLog ? " and managed server diagnostic log" : ""}</span></summary>
        ${serverLog ? '<div id="server-log-wrap" hidden><p class="hint">Managed server diagnostic log</p><pre id="server-log"></pre></div>' : ""}
        <dl class="kv" id="telemetry-technical-facts"></dl>
      </details>`;
  }

  constructor(mount, options = {}) {
    this.stages = options.stages || BenchmarkDashboard.MANAGED_STAGES;
    this.hasDownloadProgress = options.downloadProgress !== false;
    this.hasServerLog = options.serverLog !== false;
    this.chartSamples = [];
    this.chartScale = { maxX: 1, maxY: 1 };
    this.inspectedSampleTime = null;
    this.logAutoFollow = true;
    this.lastLogText = "";
    if (mount) {
      mount.innerHTML = BenchmarkDashboard.markup({
        stageNote: options.stageNote || "From preparation to saved evidence",
        downloadProgress: this.hasDownloadProgress,
        serverLog: this.hasServerLog,
      });
    }
    this.$ = (id) => document.getElementById(id);
    this.wire();
  }

  wire() {
    const scrub = this.$("chart-scrub");
    if (scrub) {
      scrub.addEventListener("input", () => {
        const index = Number(scrub.value);
        this.inspectedSampleTime = this.chartSamples[index]?.elapsed_seconds ?? null;
        this.inspectChartSample(index);
      });
    }
    const log = this.$("benchmark-log");
    if (log) {
      log.addEventListener("scroll", () => {
        const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
        this.logAutoFollow = atBottom;
        this.$("jump-to-latest-button").hidden = atBottom;
      });
    }
    const jump = this.$("jump-to-latest-button");
    if (jump) {
      jump.addEventListener("click", () => {
        const el = this.$("benchmark-log");
        el.scrollTop = el.scrollHeight;
        this.logAutoFollow = true;
        jump.hidden = true;
      });
    }
  }

  // -- formatting ------------------------------------------------------------

  static formatDuration(seconds) {
    if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return null;
    const total = Math.max(0, Math.round(seconds));
    return `${Math.floor(total / 60)}m ${total % 60}s`;
  }

  static formatElapsed(startedAt) {
    if (!startedAt) return "";
    const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed`;
  }

  static finite(value) { return typeof value === "number" && Number.isFinite(value); }

  static liveNumber(value, unit = "") {
    return typeof value === "number" && Number.isFinite(value) && value >= 0
      ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) + unit : "Pending";
  }

  // -- stages ----------------------------------------------------------------

  stageIndex(key) { return this.stages.findIndex((stage) => stage.key === key); }

  renderStageList(activeStageIndex, cancelling) {
    const list = this.$("stage-list");
    list.replaceChildren();
    this.stages.forEach((stage, index) => {
      const li = document.createElement("li");
      const state = index < activeStageIndex ? "done" : index === activeStageIndex ? (cancelling ? "cancelling" : "active") : "pending";
      li.className = `stage-item ${state}`;
      li.textContent = stage.label;
      list.append(li);
    });
  }

  // -- progress bar ----------------------------------------------------------

  renderProgressBar(percent, { complete = false } = {}) {
    const fill = this.$("progress-bar-fill"), label = this.$("progress-bar-label"), wrap = this.$("progress-bar-wrap");
    if (percent === null || percent === undefined || !Number.isFinite(percent)) {
      fill.classList.add("indeterminate");
      fill.style.width = "";
      label.textContent = "Progress unavailable for this stage";
      wrap.setAttribute("aria-valuenow", "0");
    } else {
      // 100% is reserved for a run that genuinely finished: execution *and*
      // local evidence finalization both succeeded (`complete: true`, set only
      // by the terminal success path). An in-flight run rounds DOWN and is
      // capped at 99, so a real 99.6% never displays as a finished 100% while
      // requests are still running.
      const clamped = complete ? 100 : Math.max(0, Math.min(99, Math.floor(percent)));
      fill.classList.remove("indeterminate");
      fill.style.width = `${clamped}%`;
      label.textContent = `${clamped}%`;
      wrap.setAttribute("aria-valuenow", String(clamped));
    }
  }

  renderProgressBarSaving() {
    // Deliberately 99%, not 100%: execution has finished, but the evidence
    // files are still being written and that step can still fail. 100% is
    // reserved for the terminal success path, after finalization succeeded.
    const fill = this.$("progress-bar-fill"), label = this.$("progress-bar-label"), wrap = this.$("progress-bar-wrap");
    fill.classList.remove("indeterminate");
    fill.style.width = "99%";
    label.textContent = "Benchmark finished, writing and verifying the evidence files";
    wrap.setAttribute("aria-valuenow", "99");
  }

  renderProgressBarOverrunning() {
    // Deliberately 99%, not 100%: the benchmark's time budget is spent, but its
    // last request is still streaming, so claiming completion would be wrong.
    // Not indeterminate either -- real, near-complete progress is known.
    const fill = this.$("progress-bar-fill"), label = this.$("progress-bar-label"), wrap = this.$("progress-bar-wrap");
    fill.classList.remove("indeterminate");
    fill.style.width = "99%";
    label.textContent = "Time limit reached, the runner is finishing its current request and stopping";
    wrap.setAttribute("aria-valuenow", "99");
  }

  renderProgressBarFrozen() {
    // A terminal state must never keep the indeterminate spinner animating
    // (it is a pure CSS animation -- once polling stops, nothing else would
    // ever remove it) and must never claim a percentage that was never
    // actually observed. Freezes at whatever real, determinate value was
    // last rendered; a bar that never left "indeterminate" is shown as no
    // progress at all, honestly, rather than picking a number.
    const fill = this.$("progress-bar-fill"), label = this.$("progress-bar-label"), wrap = this.$("progress-bar-wrap");
    const wasIndeterminate = fill.classList.contains("indeterminate");
    fill.classList.remove("indeterminate");
    if (wasIndeterminate) {
      fill.style.width = "0%";
      label.textContent = "No progress was recorded before this run stopped.";
      wrap.setAttribute("aria-valuenow", "0");
    } else {
      const value = wrap.getAttribute("aria-valuenow") || "0";
      label.textContent = `${value}%, stopped here`;
    }
  }

  // -- chart -----------------------------------------------------------------

  inspectChartSample(index) {
    const sample = this.chartSamples[index];
    if (!sample) return;
    const x = 52 + sample.elapsed_seconds / this.chartScale.maxX * 728;
    const y = 200 - sample.output_tokens_per_second / this.chartScale.maxY * 176;
    this.$("chart-cursor").setAttribute("x1", x.toFixed(1));
    this.$("chart-cursor").setAttribute("x2", x.toFixed(1));
    this.$("chart-point").setAttribute("cx", x.toFixed(1));
    this.$("chart-point").setAttribute("cy", y.toFixed(1));
    this.$("chart-readout").textContent =
      `${BenchmarkDashboard.formatDuration(sample.elapsed_seconds)} · ${BenchmarkDashboard.liveNumber(sample.output_tokens_per_second)} tok/s`;
    this.$("chart-scrub").value = String(index);
    this.$("chart-scrub").setAttribute("aria-valuetext", this.$("chart-readout").textContent);
  }

  // Display only finite, observed samples. The chart never creates measurements.
  renderChart(telemetry) {
    const available = !!(telemetry && telemetry.available);
    const rawHistory = available && Array.isArray(telemetry.chart_history) ? telemetry.chart_history : [];
    this.chartSamples = rawHistory.filter(sample => sample
      && Number.isFinite(sample.elapsed_seconds) && sample.elapsed_seconds >= 0
      && Number.isFinite(sample.output_tokens_per_second) && sample.output_tokens_per_second >= 0)
      .slice().sort((a, b) => a.elapsed_seconds - b.elapsed_seconds);
    const metrics = available ? telemetry.latest_metrics || {} : {};
    const requests = available ? telemetry.requests || {} : {};
    const observedThroughput = typeof metrics.output_tokens_per_second === "number"
      ? metrics.output_tokens_per_second : this.chartSamples[this.chartSamples.length - 1]?.output_tokens_per_second;
    this.$("live-throughput").textContent = BenchmarkDashboard.liveNumber(observedThroughput);
    this.$("live-ttft").textContent = BenchmarkDashboard.liveNumber(metrics.time_to_first_token_ms, " ms");
    this.$("live-request-latency").textContent = BenchmarkDashboard.liveNumber(metrics.request_latency_seconds, " s");
    this.$("live-successful").textContent = BenchmarkDashboard.liveNumber(requests.successful);
    this.$("live-errors").textContent = BenchmarkDashboard.liveNumber(requests.errored);
    this.$("live-cancelled").textContent = BenchmarkDashboard.liveNumber(requests.cancelled);
    this.$("chart-empty").hidden = this.chartSamples.length > 0;
    // SVG elements do not reflect the HTML hidden property. Toggle the attribute.
    if (this.chartSamples.length === 0) this.$("chart-svg").setAttribute("hidden", "");
    else this.$("chart-svg").removeAttribute("hidden");
    this.$("chart-inspector").hidden = this.chartSamples.length === 0;
    if (this.chartSamples.length === 0) {
      this.$("chart-line").setAttribute("points", "");
      this.$("chart-area").setAttribute("points", "");
      this.$("chart-readout").textContent = "";
      this.inspectedSampleTime = null;
      return;
    }
    const maxX = Math.max(1, this.chartSamples[this.chartSamples.length - 1].elapsed_seconds);
    const maxY = Math.max(1, ...this.chartSamples.map(sample => sample.output_tokens_per_second)) * 1.15;
    this.chartScale = { maxX, maxY };
    const coordinates = this.chartSamples.map(sample => [
      52 + sample.elapsed_seconds / maxX * 728,
      200 - sample.output_tokens_per_second / maxY * 176,
    ]);
    const points = coordinates.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    this.$("chart-line").setAttribute("points", points);
    this.$("chart-area").setAttribute("points", `${coordinates[0][0].toFixed(1)},200 ${points} ${coordinates[coordinates.length - 1][0].toFixed(1)},200`);
    this.$("chart-y-max").textContent = BenchmarkDashboard.liveNumber(maxY);
    this.$("chart-y-mid").textContent = BenchmarkDashboard.liveNumber(maxY / 2);
    this.$("chart-x-end").textContent = BenchmarkDashboard.formatDuration(maxX);
    this.$("chart-svg").setAttribute("aria-label",
      `Output throughput, ${this.chartSamples.length} observed samples across ${BenchmarkDashboard.formatDuration(maxX)}. Cumulative average in tokens per second.`);
    this.$("chart-scrub").max = String(this.chartSamples.length - 1);
    let selected = this.chartSamples.length - 1;
    if (this.inspectedSampleTime !== null) {
      selected = this.chartSamples.reduce((best, sample, index) =>
        Math.abs(sample.elapsed_seconds - this.inspectedSampleTime) < Math.abs(this.chartSamples[best].elapsed_seconds - this.inspectedSampleTime) ? index : best, 0);
    }
    this.inspectChartSample(selected);
  }

  // -- logs and telemetry ----------------------------------------------------

  renderLog(text) {
    if (text === this.lastLogText) return;
    this.lastLogText = text;
    const el = this.$("benchmark-log");
    el.textContent = text;
    if (this.logAutoFollow) {
      el.scrollTop = el.scrollHeight;
      this.$("jump-to-latest-button").hidden = true;
    } else {
      this.$("jump-to-latest-button").hidden = false;
    }
  }

  setLogStatus(text) { this.$("log-connection-status").textContent = text; }

  renderConnectionStatus(runActive, telemetry) {
    const el = this.$("log-connection-status");
    if (!runActive) {
      el.textContent = "Runner process exited.";
      return;
    }
    if (telemetry && telemetry.available && typeof telemetry.updated_at === "number") {
      const age = Date.now() / 1000 - telemetry.updated_at;
      if (age > BenchmarkDashboard.STALE_TELEMETRY_SECONDS) {
        el.textContent = `Panel connected; no runner update in ${Math.round(age)}s (normal during a slow request).`;
        return;
      }
    }
    el.textContent = "Panel connected · refreshes automatically";
  }

  // A declared request-count phase that the runner resolves as a clock window
  // is a real mismatch, and the dashboard says so rather than repeating the
  // declared target as though it were guaranteed.
  renderMeasuredWindow(contract) {
    const note = this.$("measured-window-note");
    if (!note) return;
    if (!contract || !contract.phase_basis) {
      note.hidden = true;
      note.textContent = "";
      return;
    }
    if (contract.phase_basis === "requests") {
      note.textContent = `Measured window: this profile excludes ${contract.warmup} warmup and `
        + `${contract.cooldown} cooldown requests by count, so its declared measured target is what the runner enforces.`;
      note.classList.remove("error");
    } else {
      const window = contract.measured_window_seconds;
      note.textContent = "Measured window: a fixed slice of the clock, not a request count — the runner excludes the first "
        + `${Math.round(contract.warmup)} s and the last ${Math.round(contract.cooldown)} s of this profile's `
        + `${contract.enforced_max_duration_seconds} s limit, leaving ${window != null ? Math.round(window) : "?"} s. `
        + (contract.declaration_matches_execution
          ? ""
          : `The profile declares ${contract.declared_measured_requests} measured, `
            + `${contract.declared_warmup_requests} warmup and ${contract.declared_cooldown_requests} cooldown requests; `
            + "those counts are not what is enforced, so how many requests land inside the window depends on how long each one takes.");
      note.classList.toggle("error", !contract.declaration_matches_execution);
    }
    note.hidden = false;
  }

  renderTechnicalTelemetry(telemetry) {
    const facts = this.$("telemetry-technical-facts");
    facts.replaceChildren();
    if (!telemetry || !telemetry.available) {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = "Live telemetry";
      dd.textContent = "Not available for this run.";
      facts.append(dt, dd);
      return;
    }
    const counts = telemetry.requests && typeof telemetry.requests === "object" ? telemetry.requests : {};
    const entries = [
      ["Reported stage", typeof telemetry.stage === "string" && telemetry.stage ? telemetry.stage : "unknown"],
      ["Configured request limit (enforced)", BenchmarkDashboard.finite(telemetry.configured_max_requests)
        ? String(telemetry.configured_max_requests) : "unknown"],
      ["Runner-reported request total", BenchmarkDashboard.finite(counts.total_budget)
        ? `${counts.total_budget} (the scheduler's own figure for whichever request constraint it is reporting)`
        : "unknown"],
      ["Requests finished (all phases)", BenchmarkDashboard.finite(counts.processed)
        ? String(counts.processed) : "unknown"],
      ["Remaining requests (runner estimate)", BenchmarkDashboard.finite(telemetry.remaining_requests)
        ? String(telemetry.remaining_requests) : "not reported"],
      ["Remaining fraction", BenchmarkDashboard.finite(telemetry.remaining_fraction) ? telemetry.remaining_fraction.toFixed(3) : "unknown"],
      ["Remaining seconds (time limit)", BenchmarkDashboard.finite(telemetry.remaining_seconds_duration_limit)
        ? Math.round(telemetry.remaining_seconds_duration_limit) : "unknown"],
      ["Time budget", telemetry.duration_budget_exhausted === true
        ? "Spent, the runner cannot interrupt an in-flight request, so it finishes the current one before stopping"
        : "Within budget"],
      ["Last update", BenchmarkDashboard.finite(telemetry.updated_at)
        ? new Date(telemetry.updated_at * 1000).toLocaleTimeString() : "unknown"],
    ];
    if (telemetry.observer_error) {
      entries.push(["Live monitoring", `Stopped: ${telemetry.observer_error} (the benchmark itself is unaffected)`]);
    }
    entries.forEach(([label, value]) => {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = label; dd.textContent = String(value); facts.append(dt, dd);
    });
  }

  renderDownloadProgress(progress) {
    const container = this.$("download-progress");
    if (!container) return;
    container.replaceChildren();
    if (!progress) return;
    Object.entries(progress).forEach(([label, info]) => {
      const row = document.createElement("div");
      row.className = "progress-row";
      const donePercent = info.total ? Math.min(100, (100 * info.done) / info.total) : 0;
      row.innerHTML = `<span>${label}: ${BenchmarkDashboard.formatBytes(info.done)} / ${BenchmarkDashboard.formatBytes(info.total)}</span>
        <div class="progress-bar"><span style="width:${donePercent}%"></span></div>`;
      container.append(row);
    });
  }

  static formatBytes(value) {
    if (value === null || value === undefined) return "unknown";
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let size = value, index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  // -- one live frame --------------------------------------------------------

  // `view` is what each page already knows, normalized:
  //   stageKey            the stage the runner actually reached, or null
  //   cancelling          a cancellation is in flight
  //   runActive           the run is still going
  //   telemetry           the /telemetry snapshot, or null
  //   maxDurationSeconds  the profile's own ceiling, or null
  //   startedAt           epoch ms, for elapsed when telemetry has none
  //   logText             runner log, or null to leave it alone
  //   serverLog           managed server diagnostic log, or null
  //   downloadProgress    managed download byte counts, or null
  //   lastKnownStageIndex caller-held monotonic stage, so a terminal frame
  //                       never walks backwards
  render(view) {
    const key = view.stageKey;
    if (key) this.lastKnownStageIndex = this.stageIndex(key);
    const stageIndex = view.lastKnownStageIndex !== undefined ? view.lastKnownStageIndex : (this.lastKnownStageIndex || 0);
    this.renderStageList(stageIndex, !!view.cancelling);

    const telemetryOk = !!(view.telemetry && typeof view.telemetry === "object" && view.telemetry.available);
    const elapsedSeconds = telemetryOk && BenchmarkDashboard.finite(view.telemetry.elapsed_seconds)
      ? view.telemetry.elapsed_seconds
      : (view.startedAt ? (Date.now() - view.startedAt) / 1000 : null);
    this.$("elapsed").textContent = BenchmarkDashboard.formatElapsed(view.startedAt);
    this.$("fact-elapsed").textContent = elapsedSeconds != null ? BenchmarkDashboard.formatDuration(elapsedSeconds) : "N/A";

    // The run's own time budget is spent, but the runner is still working:
    // GuideLLM cannot interrupt an in-flight request, so it finishes the
    // current one before stopping. This is reported by telemetry.py from
    // GuideLLM's own clamped remaining_duration, and only ever while the run is
    // still active -- never for a finished, cancelled, or failed run.
    const overrunning = telemetryOk && view.telemetry.duration_budget_exhausted === true && key !== "saving";
    if (key === "saving") {
      this.$("fact-eta").textContent = "Saving results…";
    } else if (overrunning) {
      // Never "0s remaining": a countdown frozen at zero while work continues
      // reads as a stuck run, not as an honest "we are past the budget".
      this.$("fact-eta").textContent = "Time limit reached, finishing current request";
    } else if (!telemetryOk) {
      this.$("fact-eta").textContent = "N/A";
    } else if (!BenchmarkDashboard.finite(view.telemetry.eta_seconds)) {
      this.$("fact-eta").textContent = "Estimating…";
    } else {
      this.$("fact-eta").textContent = BenchmarkDashboard.formatDuration(view.telemetry.eta_seconds) + " remaining";
    }

    const counts = telemetryOk && view.telemetry.requests;
    const target = telemetryOk ? view.telemetry.measured_requests_target : null;
    this.$("live-measured-target").textContent = BenchmarkDashboard.finite(target) ? String(target) : "Pending";
    const configuredLimit = telemetryOk ? view.telemetry.configured_max_requests : null;
    if (counts && typeof counts === "object" && BenchmarkDashboard.finite(counts.processed)) {
      this.$("fact-requests").textContent = BenchmarkDashboard.finite(configuredLimit)
        ? `${counts.processed} finished, of at most ${configuredLimit} configured`
        : `${counts.processed} finished`;
    } else {
      this.$("fact-requests").textContent = "N/A";
    }

    // The ceiling bounds the benchmark's own execution, not model startup or
    // cleanup -- which is why elapsed flow time can exceed it without the
    // benchmark having overrun anything.
    this.$("fact-max-duration").textContent = view.maxDurationSeconds
      ? `${Math.round(view.maxDurationSeconds / 60)} min ceiling (benchmark execution only)`
      : "N/A";

    const percent = telemetryOk && BenchmarkDashboard.finite(view.telemetry.remaining_fraction)
      ? (1 - view.telemetry.remaining_fraction) * 100 : null;
    if (key === "saving") {
      // GuideLLM has stopped, but compression, environment.json, and
      // reproduce.md are still being written. Not finished yet.
      this.renderProgressBarSaving();
    } else if (overrunning) {
      // remaining_fraction is legitimately 0 once the budget is spent, which
      // would render a finished-looking 100% bar over a still-running
      // benchmark. Hold just short of complete and say why.
      this.renderProgressBarOverrunning();
    } else {
      this.renderProgressBar(percent);
    }
    this.renderMeasuredWindow(telemetryOk ? view.telemetry.phase_contract : null);
    this.renderChart(view.telemetry);
    this.renderConnectionStatus(view.runActive, view.telemetry);
    this.renderTechnicalTelemetry(view.telemetry);
    this.renderDownloadProgress(view.downloadProgress);

    if (view.serverLog && this.$("server-log-wrap")) {
      this.$("server-log-wrap").hidden = false;
      this.$("server-log").textContent = view.serverLog;
    }
    if (view.logText != null) this.renderLog(view.logText);
    return { percent, overrunning };
  }

  // -- terminal states -------------------------------------------------------

  prepareForRun({ heading = "Running", subtitle = "", logStatus = "Panel connected · refreshes automatically" } = {}) {
    this.$("progress-heading").textContent = heading;
    this.$("dashboard-subtitle").textContent = subtitle;
    this.$("elapsed").textContent = "";
    this.$("run-error").hidden = true;
    this.$("start-over-button").hidden = true;
    this.$("cancel-button").hidden = false;
    this.$("cancel-button").disabled = false;
    this.$("cancel-button").textContent = "Cancel";
    this.lastKnownStageIndex = 0;
    this.renderStageList(0, false);
    if (this.$("server-log-wrap")) this.$("server-log-wrap").hidden = true;
    this.lastLogText = "";
    this.logAutoFollow = true;
    this.$("jump-to-latest-button").hidden = true;
    this.$("benchmark-log").textContent = "Waiting…";
    this.setLogStatus(logStatus);
    this.inspectedSampleTime = null;
    this.renderChart(null);
    this.$("chart-empty").textContent = "Waiting for the first measurement…";
    this.renderProgressBar(null);
  }

  // A terminal frame. `view` carries what the page knows about how it ended:
  //   ok               completed with results
  //   status           "completed"|"failed"|"cancelled"|"interrupted"|"unavailable"
  //   kindLabel        optional heading override (a smoke check, say)
  //   runnerStarted    the runner process genuinely started
  //   errorMessage     the server's own message, if any
  //   createdAt/finishedAt ISO strings for the honest total duration
  //   hasServerLog     a managed diagnostic log was rendered
  finish(view) {
    this.$("cancel-button").hidden = true;
    this.$("start-over-button").hidden = false;

    // A real created_at/finished_at pair is the honest total duration,
    // whether this is a live poll noticing the run just ended or a much
    // later reopen -- never a live "elapsed" figure that would otherwise
    // keep climbing against the current time once nothing is polling anymore.
    if (view.createdAt && view.finishedAt) {
      const totalSeconds = (new Date(view.finishedAt).getTime() - new Date(view.createdAt).getTime()) / 1000;
      this.$("elapsed").textContent = BenchmarkDashboard.formatDuration(totalSeconds) + " total";
      this.$("fact-elapsed").textContent = BenchmarkDashboard.formatDuration(totalSeconds);
    }

    if (view.ok) {
      // 100% only here: execution finished *and* local evidence finalization
      // (compression, environment.json, reproduce.md) already completed. A run
      // that reached this point did pass through every stage, so the list can
      // say so -- and a remaining-time estimate is now stale, not a fact.
      this.$("progress-heading").textContent = "Benchmark completed";
      for (const id of ["live-ttft", "live-request-latency"]) {
        if (this.$(id).textContent === "Pending") this.$(id).textContent = "Not recorded";
      }
      this.renderStageList(this.stages.length, false);
      this.$("fact-eta").textContent = "Completed";
      this.renderProgressBar(100, { complete: true });
      return;
    }

    const cancelled = view.status === "cancelled";
    const interrupted = view.status === "interrupted";
    const neverStarted = !view.runnerStarted;
    this.$("progress-heading").textContent = interrupted ? "Benchmark interrupted"
      : view.kindLabel ? view.kindLabel
      : view.status === "unavailable" ? "Saved results unavailable"
      : cancelled ? "Benchmark cancelled"
      : neverStarted ? "Could not start benchmark" : "Benchmark failed";
    this.renderProgressBarFrozen();
    if (view.createdAt && !view.finishedAt) {
      this.$("elapsed").textContent = "End time unknown";
      this.$("fact-elapsed").textContent = "Unknown, panel interrupted";
    }
    // "Estimating…"/"Xm Ys remaining" implied more progress was still coming;
    // once terminal, a remaining-time estimate is stale, not a real fact.
    this.$("fact-eta").textContent = neverStarted ? "N/A" : cancelled ? "Cancelled" : "Stopped, did not finish";

    if (this.$("chart-empty").hidden === false) {
      this.$("chart-empty").textContent = neverStarted
        ? "No measurements: the benchmark process never started."
        : "No measurements were recorded before the run stopped.";
    }
    if (neverStarted) {
      this.$("fact-requests").textContent = "Benchmark measurements never started.";
      if (this.lastLogText === "" || this.lastLogText === "Waiting…") {
        this.$("benchmark-log").textContent = "No runner log: the benchmark process never started.";
      }
      this.setLogStatus("The benchmark runner never started.");
      if (this.hasServerLog && !view.hasServerLog && this.$("server-log-wrap")) {
        this.$("server-log-wrap").hidden = false;
        this.$("server-log").textContent = "No managed server diagnostic log is available for this run.";
      }
    } else {
      this.setLogStatus(interrupted
        ? "Saved log only. This panel has not resumed the previous process."
        : cancelled ? "Runner process stopped (cancelled)." : "Runner process exited.");
    }

    this.$("run-error").hidden = false;
    this.$("run-error").textContent = view.errorMessage || (view.smoke
      ? "This was a server readiness check. It produced no benchmark results to share." : neverStarted
      ? "The benchmark process never started. See the technical details below for why."
      : "The run did not finish. Local logs above describe what happened.");
  }

  // -- results ---------------------------------------------------------------

  static metricCard(label, value, unit, note) {
    const box = document.createElement("dl"), dt = document.createElement("dt"), dd = document.createElement("dd"),
      suffix = document.createElement("span"), explain = document.createElement("p");
    box.className = "metric";
    dt.textContent = label;
    dd.textContent = typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value;
    suffix.textContent = unit; dd.append(suffix);
    explain.className = "metric-note"; explain.textContent = note;
    box.append(dt, dd, explain);
    return box;
  }

  // The identical measured-window report on both routes.
  static metricCards(summary) {
    const r = summary.results;
    return [
      BenchmarkDashboard.metricCard("Time to first token · p50", r.latency_ms.ttft.p50, "ms", "Median time until the first streamed token; lower is better."),
      BenchmarkDashboard.metricCard("Time to first token · p95", r.latency_ms.ttft.p95, "ms", "95th-percentile time until the first streamed token; lower is better."),
      BenchmarkDashboard.metricCard("Inter-token latency · p50", r.latency_ms.inter_token.p50, "ms", "Median delay between output tokens; lower is better."),
      BenchmarkDashboard.metricCard("Output tokens / second", r.output_tokens_per_second, "", "Output-token throughput; higher is better."),
      BenchmarkDashboard.metricCard("Total tokens / second", r.total_tokens_per_second, "", "Input-plus-output token throughput."),
      BenchmarkDashboard.metricCard("Requests / second", r.requests_per_second, "", "How many requests the workload completed per second."),
      BenchmarkDashboard.metricCard("Successful / attempted", `${r.requests.succeeded} / ${r.requests.attempted}`, "", "Measured window only: requests that completed successfully, out of those attempted after warmup and before cooldown. Smaller than the live all-phase counts, by design."),
      BenchmarkDashboard.metricCard("Error rate", r.error_rate_percent, "%", "Share of attempted requests that failed or stayed incomplete; lower is better."),
    ];
  }
};
