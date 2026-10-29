"use strict";

const $ = (id) => document.getElementById(id);

// "warmup"/"benchmarking"/"cooldown"/"saving" are only ever reached with real
// support from GuideLLM's own accumulator.timings.status and progress hooks
// (see telemetry.py); when telemetry is unavailable, execution collapses to
// the generic "benchmarking" -- never invented.
const STAGES = [
  { key: "checking", label: "Checking system" },
  { key: "downloading", label: "Downloading verified files" },
  { key: "verifying", label: "Verifying files" },
  { key: "loading_model", label: "Loading the model" },
  { key: "warmup", label: "Warming up" },
  { key: "benchmarking", label: "Running the benchmark" },
  { key: "cooldown", label: "Cooling down" },
  { key: "saving", label: "Saving results" },
];
const SETUP_ACTIVE = new Set(["preparing", "cancelling"]);
const RUN_ACTIVE = new Set(["starting_server", "waiting_ready", "running_benchmark", "cancelling"]);
const STALE_TELEMETRY_SECONDS = 20;

let setup = null;
let recipes = [];
let recipesById = {};
let selectedRecipeId = null;
let reviewReport = null;
let storageDir = "";
let activeKind = null; // "setup" | "run" | null
let activeJobId = null;
let benchmarkJobId = null;
let finishedManagedJob = null; // last terminal managed run job detail, with .result
let flowStartedAt = null;
let lastKnownStageIndex = 0;
let lastKnownPercent = null; // real remaining_fraction-derived percent, frozen (never reset to 0, never advanced to 100) on failure/cancellation
let preview = null;
let pending = false;
let timers = [];
let logAutoFollow = true;
let lastLogText = "";

const incoming = new URLSearchParams(location.hash.slice(1)).get("token");
if (incoming) {
  sessionStorage.setItem("omobench-token", incoming);
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("omobench-token") || "";

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
  $("notice").hidden = !message;
}

async function api(path, data, binary = false) {
  const headers = { "X-OmoBench-Token": token };
  const options = { headers };
  if (data !== undefined) {
    headers["Content-Type"] = "application/json";
    options.method = "POST";
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json();
    throw new Error(body.error || "The request failed.");
  }
  return binary ? response.blob() : response.json();
}

function isUnknownJobError(error) {
  return /^Unknown managed (setup job|run)\.$/.test(error.message);
}

function formatBytes(value) {
  if (value === null || value === undefined) return "unknown";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = value, index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatElapsed(startedAt) {
  if (!startedAt) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed`;
}

function download(blob, name) {
  const url = URL.createObjectURL(blob), a = document.createElement("a");
  a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function clearTimers() { timers.forEach(clearTimeout); timers = []; }

async function action(work) {
  if (pending) return;
  pending = true;
  try { await work(); }
  catch (error) { notice(error.message, true); }
  finally { pending = false; }
}

// -- Screen navigation --------------------------------------------------------

function showScreen(name) {
  ["choose", "run", "results", "share"].forEach((key) => { $(`screen-${key}`).hidden = key !== name; });
  document.querySelectorAll(".step-tab").forEach((tab) => {
    const isCurrent = tab.dataset.screen === name;
    tab.classList.toggle("current", isCurrent);
    if (isCurrent) tab.setAttribute("aria-current", "step"); else tab.removeAttribute("aria-current");
  });
}

document.querySelectorAll(".step-tab").forEach((tab) => tab.addEventListener("click", () => action(async () => {
  const target = tab.dataset.screen;
  if (target === "run") {
    if (activeKind && activeJobId) {
      showScreen("run");
    } else if (recipesById[selectedRecipeId]) {
      await chooseRecipe(selectedRecipeId);
    } else {
      notice("Choose a test below to prepare a new benchmark. Your saved results remain available.");
      showScreen("choose");
    }
    return;
  }
  if ((target === "results" || target === "share") && !finishedManagedJob) {
    notice("Choose a saved run below to reopen its results. Only completed benchmarks can be shared.");
    showScreen("choose");
    await loadRecentRuns();
    return;
  }
  if (target === "results") showResults(finishedManagedJob.result);
  if (target === "share") await enterShareScreen();
  showScreen(target);
})));

// -- Screen 1: choose your test -----------------------------------------------

async function loadRunnerStatus() {
  setup = await api("/api/setup");
  $("runner-status").textContent = setup.runner_message;
  $("runner-status").classList.toggle("error", !setup.can_execute);
}

async function loadRecipes() {
  const data = await api("/api/managed/recipes");
  recipes = data.recipes;
  recipesById = Object.fromEntries(recipes.map((r) => [r.id, r]));
  storageDir = data.default_storage_dir || "";
  const recommendedId = recipes.length > 1
    ? recipes.reduce((a, b) => (b.max_duration_seconds || 0) > (a.max_duration_seconds || 0) ? b : a).id
    : null;
  const list = $("recipe-list");
  list.replaceChildren();
  recipes.forEach((recipe) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "recipe-card";
    const minutes = recipe.max_duration_seconds ? Math.round(recipe.max_duration_seconds / 60) : null;
    const badge = recipe.id === recommendedId ? '<span class="badge">Recommended for slower hardware</span>' : "";
    const duration = minutes
      ? `Ends at its configured request or time limit, up to ${minutes} minutes, whichever comes first; it may finish earlier.`
      : "Ends at its own configured request or time limit; it may finish earlier.";
    card.innerHTML = `${badge}<h3>${recipe.title}</h3>
      <p class="hint">Model ${recipe.model_id} (${recipe.quantization}) · ${recipe.execution_device.toUpperCase()} only</p>
      <p class="hint">${recipe.measured_requests ? `Targets ${recipe.measured_requests} measured requests. ` : ""}${duration}</p>`;
    card.addEventListener("click", () => action(() => chooseRecipe(recipe.id)));
    list.append(card);
  });
}

async function chooseRecipe(recipeId) {
  selectedRecipeId = recipeId;
  notice("");
  showScreen("run");
  $("summary-panel").hidden = false;
  $("progress-panel").hidden = true;
  $("summary-checking").hidden = false;
  $("summary-body").hidden = true;
  $("storage-dir").value = storageDir;
  $("storage-location-editor").hidden = true;
  await doReview();
}

async function loadRecentRuns() {
  const [managedRuns, setupJobs] = await Promise.all([api("/api/managed/runs"), api("/api/managed/setup-jobs")]);
  const activeRun = managedRuns.find((job) => RUN_ACTIVE.has(job.status));
  const activeSetup = !activeRun ? setupJobs.find((job) => SETUP_ACTIVE.has(job.status)) : null;
  const active = activeRun || activeSetup;
  if (active) {
    selectedRecipeId = active.recipe;
    storageDir = active.storage_dir;
    activeKind = activeRun ? "run" : "setup";
    activeJobId = active.id;
    flowStartedAt = new Date(active.created_at).getTime();
    lastKnownStageIndex = 0;
    lastKnownPercent = null;
    showScreen("run");
    $("summary-panel").hidden = true;
    $("progress-panel").hidden = false;
    $("cancel-button").hidden = false;
    $("start-over-button").hidden = true;
    $("run-error").hidden = true;
    const recipe = recipesById[selectedRecipeId];
    $("dashboard-subtitle").textContent = recipe
      ? `${recipe.model_id} (${recipe.quantization}) · ${recipe.benchmark_profile} · ${recipe.execution_device.toUpperCase()}`
      : "";
    notice("Reconnected to a run already in progress on this panel. Its graph and progress resume below.");
    pollFlow();
    return;
  }
  const recent = managedRuns.filter((job) => !RUN_ACTIVE.has(job.status));
  $("recent-panel").hidden = recent.length === 0;
  $("recent-list").replaceChildren(...recent.map((job) => {
    const row = document.createElement("button");
    row.type = "button"; row.className = "recent-item";
    const recipe = recipesById[job.recipe];
    const label = job.saved_local ? `Saved local run: ${job.model || "Unknown model"} · ${job.profile || "Unknown profile"}`
      : recipe ? recipe.title : job.recipe;
    row.textContent = `${label} · ${job.status} · ${new Date(job.created_at).toLocaleString()}`;
    row.addEventListener("click", () => action(async () => {
      // History describes a past run, not the next-run draft. In particular,
      // legacy rows have no recipe/storage and must not erase current choices.
      // Fetch fresh status, rather than trust a list captured before packaging
      // or submission. Failed/interrupted jobs never inherit an earlier result.
      await openTerminalRun(job.id);
    }));
    return row;
  }));
}

// -- Screen 2: prepare and run -------------------------------------------------

async function doReview() {
  storageDir = $("storage-dir").value.trim();
  $("summary-checking").hidden = false;
  $("summary-body").hidden = true;
  $("storage-error").hidden = true;
  if (!storageDir) {
    $("summary-checking").hidden = true;
    $("summary-body").hidden = false;
    $("storage-error").hidden = false;
    $("storage-error").textContent = "Enter a storage folder.";
    $("start-button").disabled = true;
    return;
  }
  try {
    reviewReport = await api("/api/managed/review", { recipe: selectedRecipeId, storage_dir: storageDir });
    renderSummary(reviewReport);
  } catch (error) {
    $("storage-error").hidden = false;
    $("storage-error").textContent = error.message;
    $("start-button").disabled = true;
  } finally {
    $("summary-checking").hidden = true;
    $("summary-body").hidden = false;
  }
}

function renderSummary(report) {
  const recipe = recipesById[selectedRecipeId];
  const facts = $("summary-facts");
  facts.replaceChildren();
  const totalBytes = report.files.reduce((sum, file) => sum + (file.size_bytes || 0), 0);
  const bytesToDownload = report.bytes_to_download ?? totalBytes;
  const minutes = recipe.max_duration_seconds ? Math.round(recipe.max_duration_seconds / 60) : null;
  const alreadyInstalledValid = report.already_installed.installed && report.already_installed.valid;

  // Total artifact size vs. bytes this folder still actually needs to
  // download are different numbers: a file already verified in the shared
  // downloads/ cache (for example because the other recipe installed it
  // first) is reused, not re-fetched, even when this recipe itself still
  // needs local preparation (extracting the runtime, writing its manifest).
  const cachedKinds = report.files.filter((file) => file.already_cached).map((file) => file.kind);
  let downloadLine;
  if (cachedKinds.length === report.files.length) {
    downloadLine = `${formatBytes(totalBytes)} total, every file already verified in this folder's cache; nothing left to download.`
      + (alreadyInstalledValid ? "" : " This recipe still needs local preparation (extracting the runtime) before it can run.");
  } else if (cachedKinds.length > 0) {
    downloadLine = `${formatBytes(totalBytes)} total, ${formatBytes(bytesToDownload)} still to download `
      + `(${cachedKinds.join(" and ")} already verified in this folder's cache and reused).`;
  } else {
    downloadLine = `${formatBytes(totalBytes)} total to download.`;
  }

  const entries = [
    ["Model", `${report.recipe.model_id} (${report.recipe.quantization})`],
    ["Execution device", report.recipe.execution_device.toUpperCase() + " only"],
    ["Download size", downloadLine],
    ["Storage destination", report.storage_dir + (report.storage_dir_exists ? "" : " (will be created)")],
    ["Licences", report.files.map((file) => `${file.kind}: ${file.licence}`).join(" · ")],
    ["Measured requests target", recipe.measured_requests ? String(recipe.measured_requests) : "Set by the profile."],
    ["Maximum duration", minutes
      ? `${minutes} minutes, the run ends at its configured request or time limit, whichever comes first; it may finish before the time limit.`
      : "Bounded by the profile's own request or time limit."],
  ];
  entries.forEach(([label, value]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    const pair = document.createElement("div");
    pair.className = "fact-pair";
    dt.textContent = label; dd.textContent = value; pair.append(dt, dd); facts.append(pair);
  });

  $("platform-message").textContent = report.platform.message
    + (report.already_installed.installed && !report.already_installed.valid
      ? ` Previously installed files failed re-verification: ${report.already_installed.error}`
      : "");
  $("platform-message").classList.toggle("error", !report.platform.ok || (report.already_installed.installed && !report.already_installed.valid));

  // Free space has three distinct states, not two: known-and-trustworthy,
  // known-but-unverifiable (WSL2's own virtual disk, whose reported capacity
  // can exceed what the Windows drive backing it can actually provide), and
  // unknown. Only the first ever claims "enough space".
  const spaceMessage = $("space-message");
  spaceMessage.classList.remove("error");
  if (report.free_space_verified === false) {
    spaceMessage.textContent = `Virtual filesystem free space: ${formatBytes(report.free_space_bytes)} (${formatBytes(report.required_bytes)} required). ${report.space_note || "This has not been verified against the actual backing drive."}`;
  } else if (report.free_space_bytes === null || report.free_space_bytes === undefined) {
    spaceMessage.textContent = "Could not determine free space for this folder.";
  } else if (report.has_enough_space === false) {
    spaceMessage.textContent = `Not enough free space: ${formatBytes(report.free_space_bytes)} free, ${formatBytes(report.required_bytes)} required.`;
    spaceMessage.classList.add("error");
  } else {
    spaceMessage.textContent = `Free space: ${formatBytes(report.free_space_bytes)} (${formatBytes(report.required_bytes)} required), enough space.`;
  }

  $("review-files").replaceChildren();
  report.files.forEach((file) => {
    const card = document.createElement("div");
    card.className = "file-card";
    card.innerHTML = `<strong>${file.kind.toUpperCase()}</strong> · ${file.filename} · ${formatBytes(file.size_bytes)} · licence ${file.licence}
      <div class="path">Repository: ${file.repository} @ ${file.revision}</div>
      <div class="path">SHA-256: ${file.sha256}</div>
      <div class="path">${file.url}</div>`;
    $("review-files").append(card);
  });

  const canStart = report.platform.ok && report.has_enough_space !== false;
  $("start-button").disabled = !canStart;
  $("start-button").textContent = alreadyInstalledValid ? "Start benchmark" : "Download and start";
  $("start-button").dataset.alreadyInstalled = alreadyInstalledValid ? "1" : "";
}

function stageIndex(key) { return STAGES.findIndex((stage) => stage.key === key); }

function renderStageList(activeStageIndex, cancelling) {
  const list = $("stage-list");
  list.replaceChildren();
  STAGES.forEach((stage, index) => {
    const li = document.createElement("li");
    const state = index < activeStageIndex ? "done" : index === activeStageIndex ? (cancelling ? "cancelling" : "active") : "pending";
    li.className = `stage-item ${state}`;
    li.textContent = stage.label;
    list.append(li);
  });
}

async function doStart() {
  flowStartedAt = Date.now();
  lastKnownStageIndex = 0;
  lastKnownPercent = null;
  $("progress-heading").textContent = "Running";
  $("elapsed").textContent = "";
  $("run-error").hidden = true;
  $("start-over-button").hidden = true;
  $("cancel-button").hidden = false;
  $("cancel-button").disabled = false;
  $("cancel-button").textContent = "Cancel";
  $("summary-panel").hidden = true;
  $("progress-panel").hidden = false;
  renderStageList(0, false);
  $("server-log-wrap").hidden = true;
  lastLogText = "";
  logAutoFollow = true;
  $("jump-to-latest-button").hidden = true;
  $("benchmark-log").textContent = "Waiting…";
  $("log-connection-status").textContent = "Panel connected · refreshes automatically";
  inspectedSampleTime = null;
  renderChart(null);
  $("chart-empty").textContent = "Waiting for the first measurement…";
  renderProgressBar(null);
  renderChart(null);
  const recipe = recipesById[selectedRecipeId];
  $("dashboard-subtitle").textContent = recipe
    ? `${recipe.model_id} (${recipe.quantization}) · ${recipe.benchmark_profile} · ${recipe.execution_device.toUpperCase()}`
    : "";
  const alreadyInstalled = $("start-button").dataset.alreadyInstalled === "1";
  if (alreadyInstalled) {
    activeKind = "run";
    const job = await api("/api/managed/runs", { recipe: selectedRecipeId, storage_dir: storageDir, mode: "execute" });
    activeJobId = job.id;
  } else {
    activeKind = "setup";
    const job = await api("/api/managed/setup-jobs", { recipe: selectedRecipeId, storage_dir: storageDir, confirmed: true });
    activeJobId = job.id;
  }
  pollFlow();
}

async function startExecute() {
  const job = await api("/api/managed/runs", { recipe: selectedRecipeId, storage_dir: storageDir, mode: "execute" });
  activeKind = "run";
  activeJobId = job.id;
  pollFlow();
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return null;
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}m ${total % 60}s`;
}

function currentStageKey(setupJob, runJob, telemetry) {
  if (setupJob && SETUP_ACTIVE.has(setupJob.status)) return "downloading";
  if (!runJob) return "checking";
  if (runJob.status === "starting_server") return "verifying";
  if (runJob.status === "waiting_ready") return "loading_model";
  if (runJob.status === "running_benchmark") {
    if (!runJob.benchmark_job_id) {
      // The managed run's own status flipped to "running_benchmark" before
      // the benchmark Controller job it delegates to was actually created
      // (see ManagedController._run_managed) -- there is no runner process
      // yet, so this must not be reported as "warmup"/"benchmarking" (that
      // was the guided-dashboard bug: a startup failure at exactly this
      // boundary showed "Running the benchmark" with warmup marked done,
      // even though GuideLLM had never been invoked).
      return "loading_model";
    }
    if (telemetry && telemetry.available && telemetry.stage) {
      // GuideLLM's own accumulator.timings.status ("pending"/"warmup"/
      // "active"/"cooldown"), mapped to this page's vocabulary in
      // telemetry.py -- "starting" there still means "loading_model" here,
      // since no request has been sent yet.
      return telemetry.stage === "starting" ? "loading_model" : telemetry.stage;
    }
    return "benchmarking"; // telemetry unavailable, but a real runner process exists: a real stage, just not sub-divided
  }
  return null; // terminal
}

function renderProgressBar(percent, { complete = false } = {}) {
  const fill = $("progress-bar-fill"), label = $("progress-bar-label"), wrap = $("progress-bar-wrap");
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

// Display only finite, observed samples. The chart never creates measurements.
let chartSamples = [];
let chartScale = { maxX: 1, maxY: 1 };
let inspectedSampleTime = null;

function liveNumber(value, unit = "") {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) + unit : "Pending";
}

function inspectChartSample(index) {
  const sample = chartSamples[index];
  if (!sample) return;
  const x = 52 + sample.elapsed_seconds / chartScale.maxX * 728;
  const y = 200 - sample.output_tokens_per_second / chartScale.maxY * 176;
  $("chart-cursor").setAttribute("x1", x.toFixed(1));
  $("chart-cursor").setAttribute("x2", x.toFixed(1));
  $("chart-point").setAttribute("cx", x.toFixed(1));
  $("chart-point").setAttribute("cy", y.toFixed(1));
  $("chart-readout").textContent = `${formatDuration(sample.elapsed_seconds)} · ${liveNumber(sample.output_tokens_per_second)} tok/s`;
  $("chart-scrub").value = String(index);
  $("chart-scrub").setAttribute("aria-valuetext", $("chart-readout").textContent);
}

function renderChart(telemetry) {
  const available = !!(telemetry && telemetry.available);
  const rawHistory = available && Array.isArray(telemetry.chart_history) ? telemetry.chart_history : [];
  chartSamples = rawHistory.filter(sample => sample
    && Number.isFinite(sample.elapsed_seconds) && sample.elapsed_seconds >= 0
    && Number.isFinite(sample.output_tokens_per_second) && sample.output_tokens_per_second >= 0)
    .slice().sort((a, b) => a.elapsed_seconds - b.elapsed_seconds);
  const metrics = available ? telemetry.latest_metrics || {} : {};
  const requests = available ? telemetry.requests || {} : {};
  const observedThroughput = typeof metrics.output_tokens_per_second === "number"
    ? metrics.output_tokens_per_second : chartSamples[chartSamples.length - 1]?.output_tokens_per_second;
  $("live-throughput").textContent = liveNumber(observedThroughput);
  $("live-ttft").textContent = liveNumber(metrics.time_to_first_token_ms, " ms");
  $("live-request-latency").textContent = liveNumber(metrics.request_latency_seconds, " s");
  $("live-successful").textContent = liveNumber(requests.successful);
  $("live-errors").textContent = liveNumber(requests.errored);
  $("live-cancelled").textContent = liveNumber(requests.cancelled);
  $("chart-empty").hidden = chartSamples.length > 0;
  // SVG elements do not reflect the HTML hidden property. Toggle the attribute.
  if (chartSamples.length === 0) $("chart-svg").setAttribute("hidden", "");
  else $("chart-svg").removeAttribute("hidden");
  $("chart-inspector").hidden = chartSamples.length === 0;
  if (chartSamples.length === 0) {
    $("chart-line").setAttribute("points", "");
    $("chart-area").setAttribute("points", "");
    $("chart-readout").textContent = "";
    inspectedSampleTime = null;
    return;
  }
  const maxX = Math.max(1, chartSamples[chartSamples.length - 1].elapsed_seconds);
  const maxY = Math.max(1, ...chartSamples.map(sample => sample.output_tokens_per_second)) * 1.15;
  chartScale = { maxX, maxY };
  const coordinates = chartSamples.map(sample => [
    52 + sample.elapsed_seconds / maxX * 728,
    200 - sample.output_tokens_per_second / maxY * 176,
  ]);
  const points = coordinates.map(([x,y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  $("chart-line").setAttribute("points", points);
  $("chart-area").setAttribute("points", `${coordinates[0][0].toFixed(1)},200 ${points} ${coordinates[coordinates.length - 1][0].toFixed(1)},200`);
  $("chart-y-max").textContent = liveNumber(maxY);
  $("chart-y-mid").textContent = liveNumber(maxY / 2);
  $("chart-x-end").textContent = formatDuration(maxX);
  $("chart-svg").setAttribute("aria-label", `Output throughput, ${chartSamples.length} observed samples across ${formatDuration(maxX)}. Cumulative average in tokens per second.`);
  $("chart-scrub").max = String(chartSamples.length - 1);
  let selected = chartSamples.length - 1;
  if (inspectedSampleTime !== null) {
    selected = chartSamples.reduce((best, sample, index) =>
      Math.abs(sample.elapsed_seconds - inspectedSampleTime) < Math.abs(chartSamples[best].elapsed_seconds - inspectedSampleTime) ? index : best, 0);
  }
  inspectChartSample(selected);
}

$("chart-scrub").addEventListener("input", () => {
  const index = Number($("chart-scrub").value);
  inspectedSampleTime = chartSamples[index]?.elapsed_seconds ?? null;
  inspectChartSample(index);
});

function renderConnectionStatus(runJob, telemetry) {
  const el = $("log-connection-status");
  if (runJob && !RUN_ACTIVE.has(runJob.status)) {
    el.textContent = "Runner process exited.";
    return;
  }
  if (telemetry && telemetry.available && typeof telemetry.updated_at === "number") {
    const age = Date.now() / 1000 - telemetry.updated_at;
    if (age > STALE_TELEMETRY_SECONDS) {
      el.textContent = `Panel connected; no runner update in ${Math.round(age)}s (normal during a slow request).`;
      return;
    }
  }
  el.textContent = "Panel connected · refreshes automatically";
}

function renderTechnicalTelemetry(telemetry) {
  const facts = $("telemetry-technical-facts");
  facts.replaceChildren();
  if (!telemetry || !telemetry.available) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = "Live telemetry";
    dd.textContent = "Not available for this run.";
    facts.append(dt, dd);
    return;
  }
  const entries = [
    ["Reported stage", telemetry.stage || "unknown"],
    ["Remaining fraction", telemetry.remaining_fraction != null ? telemetry.remaining_fraction.toFixed(3) : "unknown"],
    ["Remaining seconds (time limit)", telemetry.remaining_seconds_duration_limit != null ? Math.round(telemetry.remaining_seconds_duration_limit) : "unknown"],
    ["Time budget", telemetry.duration_budget_exhausted === true
      ? "Spent, the runner cannot interrupt an in-flight request, so it finishes the current one before stopping"
      : "Within budget"],
    ["Last update", new Date(telemetry.updated_at * 1000).toLocaleTimeString()],
  ];
  if (telemetry.observer_error) {
    entries.push(["Live monitoring", `Stopped: ${telemetry.observer_error} (the benchmark itself is unaffected)`]);
  }
  entries.forEach(([label, value]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = String(value); facts.append(dt, dd);
  });
}

function renderProgress(setupJob, runJob, benchJob, telemetry) {
  const key = currentStageKey(setupJob, runJob, telemetry);
  const cancelling = (setupJob && setupJob.status === "cancelling") || (runJob && runJob.status === "cancelling");
  if (key) lastKnownStageIndex = stageIndex(key);
  renderStageList(lastKnownStageIndex, cancelling);
  $("cancel-button").disabled = cancelling;
  $("cancel-button").textContent = cancelling ? "Cancelling…" : "Cancel";

  const recipe = recipesById[runJob ? runJob.recipe : setupJob ? setupJob.recipe : selectedRecipeId];
  const telemetryOk = telemetry && telemetry.available;
  const elapsedSeconds = telemetryOk && typeof telemetry.elapsed_seconds === "number"
    ? telemetry.elapsed_seconds
    : (flowStartedAt ? (Date.now() - flowStartedAt) / 1000 : null);
  $("elapsed").textContent = formatElapsed(flowStartedAt);
  $("fact-elapsed").textContent = elapsedSeconds != null ? formatDuration(elapsedSeconds) : "N/A";

  // The run's own time budget is spent, but the runner is still working:
  // GuideLLM cannot interrupt an in-flight request, so it finishes the
  // current one before stopping. This is reported by telemetry.py from
  // GuideLLM's own clamped remaining_duration, and only ever while the run is
  // still active -- never for a finished, cancelled, or failed run.
  const overrunning = telemetryOk && telemetry.duration_budget_exhausted === true && key !== "saving";
  if (key === "saving") {
    $("fact-eta").textContent = "Saving results…";
  } else if (overrunning) {
    // Never "0s remaining": a countdown frozen at zero while work continues
    // reads as a stuck run, not as an honest "we are past the budget".
    $("fact-eta").textContent = "Time limit reached, finishing current request";
  } else if (!telemetryOk) {
    $("fact-eta").textContent = "N/A";
  } else if (telemetry.eta_seconds === null || telemetry.eta_seconds === undefined) {
    $("fact-eta").textContent = "Estimating…";
  } else {
    $("fact-eta").textContent = formatDuration(telemetry.eta_seconds) + " remaining";
  }

  if (telemetryOk && telemetry.requests) {
    const r = telemetry.requests;
    const total = r.total_budget != null ? r.total_budget : "?";
    const target = telemetry.measured_requests_target;
    $("fact-requests").textContent = `${r.processed ?? 0} / ${total} total requests`
      + (target ? ` (target: ${target} measured)` : "");
  } else {
    $("fact-requests").textContent = "N/A";
  }
  // The ceiling bounds the benchmark's own execution, not model startup or
  // cleanup -- which is why elapsed flow time can exceed it without the
  // benchmark having overrun anything.
  $("fact-max-duration").textContent = recipe && recipe.max_duration_seconds
    ? `${Math.round(recipe.max_duration_seconds / 60)} min ceiling (benchmark execution only)`
    : "N/A";

  const percent = telemetryOk && telemetry.remaining_fraction != null ? (1 - telemetry.remaining_fraction) * 100 : null;
  // Never record 100 for "saving": evidence finalization can still fail, and
  // a frozen-at-100 bar would then describe a failed run as a finished one.
  if (percent !== null) lastKnownPercent = percent;
  if (key === "saving") {
    // GuideLLM has stopped, but compression, environment.json, and
    // reproduce.md are still being written. Not finished yet.
    renderProgressBarSaving();
  } else if (overrunning) {
    // remaining_fraction is legitimately 0 once the budget is spent, which
    // would render a finished-looking 100% bar over a still-running
    // benchmark. Hold just short of complete and say why.
    renderProgressBarOverrunning();
  } else {
    renderProgressBar(percent);
  }
  renderChart(telemetry);
  renderConnectionStatus(runJob, telemetry);
  renderTechnicalTelemetry(telemetry);

  // Genuine byte-level download progress (never a manufactured percentage):
  // straight from the resumable downloader's own done/total byte counts.
  const progressContainer = $("download-progress");
  progressContainer.replaceChildren();
  if (setupJob && setupJob.progress) {
    Object.entries(setupJob.progress).forEach(([label, info]) => {
      const row = document.createElement("div");
      row.className = "progress-row";
      const donePercent = info.total ? Math.min(100, (100 * info.done) / info.total) : 0;
      row.innerHTML = `<span>${label}: ${formatBytes(info.done)} / ${formatBytes(info.total)}</span>
        <div class="progress-bar"><span style="width:${donePercent}%"></span></div>`;
      progressContainer.append(row);
    });
  }

  if (runJob && runJob.server_log) {
    $("server-log-wrap").hidden = false;
    $("server-log").textContent = runJob.server_log;
  }
  if (benchJob) renderLog(benchJob.log || "Waiting…");
}

function renderLog(text) {
  if (text === lastLogText) return;
  lastLogText = text;
  const el = $("benchmark-log");
  el.textContent = text;
  if (logAutoFollow) {
    el.scrollTop = el.scrollHeight;
    $("jump-to-latest-button").hidden = true;
  } else {
    $("jump-to-latest-button").hidden = false;
  }
}

function renderProgressBarSaving() {
  // Deliberately 99%, not 100%: execution has finished, but the evidence
  // files are still being written and that step can still fail. 100% is
  // reserved for the terminal success path, after finalization succeeded.
  const fill = $("progress-bar-fill"), label = $("progress-bar-label"), wrap = $("progress-bar-wrap");
  fill.classList.remove("indeterminate");
  fill.style.width = "99%";
  label.textContent = "Benchmark finished, writing and verifying the evidence files";
  wrap.setAttribute("aria-valuenow", "99");
}

function renderProgressBarOverrunning() {
  // Deliberately 99%, not 100%: the benchmark's time budget is spent, but its
  // last request is still streaming, so claiming completion would be wrong.
  // Not indeterminate either -- real, near-complete progress is known.
  const fill = $("progress-bar-fill"), label = $("progress-bar-label"), wrap = $("progress-bar-wrap");
  fill.classList.remove("indeterminate");
  fill.style.width = "99%";
  label.textContent = "Time limit reached, the runner is finishing its current request and stopping";
  wrap.setAttribute("aria-valuenow", "99");
}

function renderProgressBarFrozen() {
  // A terminal state must never keep the indeterminate spinner animating
  // (it is a pure CSS animation -- once polling stops, nothing else would
  // ever remove it) and must never claim a percentage that was never
  // actually observed. Freezes at whatever real, determinate value was
  // last rendered; a bar that never left "indeterminate" is shown as no
  // progress at all, honestly, rather than picking a number.
  const fill = $("progress-bar-fill"), label = $("progress-bar-label"), wrap = $("progress-bar-wrap");
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

// True exactly when the runner (the GuideLLM-driving benchmark Controller
// job a managed run delegates to) never actually started -- as opposed to
// starting and then exiting, failing, or being cancelled mid-run. A managed
// run's own job.benchmark_job_id is only ever set once that delegated job
// is genuinely created (see ManagedController._run_managed), so it is a
// reliable, server-persisted signal even after the job has gone terminal
// (unlike its transient status, which is overwritten to "failed"/
// "cancelled" and so cannot itself say what was reached beforehand).
function runnerNeverStarted(runJob) {
  return !runJob || !runJob.benchmark_job_id;
}

function finishFlow(success, errorMessage, runJob) {
  clearTimers();
  if (runJob?.history_warning) notice(runJob.history_warning, true);
  $("cancel-button").hidden = true;
  $("start-over-button").hidden = false;

  // A real created_at/finished_at pair is the honest total duration,
  // whether this is a live poll noticing the run just ended or a much
  // later reopen -- never a live "elapsed" figure that would otherwise
  // keep climbing against the current time once nothing is polling anymore.
  if (runJob && runJob.created_at && runJob.finished_at) {
    const totalSeconds = (new Date(runJob.finished_at).getTime() - new Date(runJob.created_at).getTime()) / 1000;
    $("elapsed").textContent = formatDuration(totalSeconds) + " total";
    $("fact-elapsed").textContent = formatDuration(totalSeconds);
  }

  if (success && runJob && runJob.result?.results && runJob.benchmark_job_id) {
    // 100% only here: execution finished *and* local evidence finalization
    // (compression, environment.json, reproduce.md) already completed --
    // runJob only reaches a terminal "completed" status after that. Every
    // other caller of renderProgressBar() is capped below 100 (see its
    // `complete` option), so this is the single place that can display a
    // finished run.
    $("progress-heading").textContent = "Running";
    renderProgressBar(100, { complete: true });
    finishedManagedJob = runJob;
    benchmarkJobId = runJob.benchmark_job_id;
    showResults(runJob.result);
    showScreen("results");
    loadRecentRuns().catch(() => {});
    return;
  }

  // Cancellation or failure: the stage list, chart, and technical facts
  // were already rendered from the real last-known data by the
  // renderProgress() call that always precedes this one (see pollFlow()
  // and openTerminalRun()) -- never reset to zero, never advanced past
  // what the runner actually reached. This section only freezes the
  // still-live-looking pieces (the progress bar's animation) and makes the
  // terminal state itself unmistakable, distinguishing a runner that never
  // started from one that started and then exited or was cancelled.
  const cancelled = runJob && runJob.status === "cancelled";
  const interrupted = runJob && runJob.status === "interrupted";
  const smoke = runJob && runJob.kind === "smoke";
  const neverStarted = runnerNeverStarted(runJob);
  $("progress-heading").textContent = interrupted ? "Benchmark interrupted"
    : smoke ? "Server check, no benchmark measurements"
    : runJob?.status === "unavailable" ? "Saved results unavailable" : cancelled
    ? "Benchmark cancelled"
    : neverStarted ? "Could not start benchmark" : "Benchmark failed";
  renderProgressBarFrozen();
  if (runJob && !runJob.finished_at) {
    $("elapsed").textContent = "End time unknown";
    $("fact-elapsed").textContent = "Unknown, panel interrupted";
  }
  // "Estimating…"/"Xm Ys remaining" implied more progress was still coming;
  // once terminal, a remaining-time estimate is stale, not a real fact.
  $("fact-eta").textContent = neverStarted ? "N/A" : cancelled ? "Cancelled" : "Stopped, did not finish";

  if ($("chart-empty").hidden === false) {
    $("chart-empty").textContent = neverStarted
      ? "No measurements: the benchmark process never started."
      : "No measurements were recorded before the run stopped.";
  }
  if (neverStarted) {
    $("fact-requests").textContent = "Benchmark measurements never started.";
    if (lastLogText === "" || lastLogText === "Waiting…") {
      $("benchmark-log").textContent = "No runner log: the benchmark process never started.";
    }
    $("log-connection-status").textContent = "The benchmark runner never started.";
    if (!runJob || !runJob.server_log) {
      $("server-log-wrap").hidden = false;
      $("server-log").textContent = "No managed server diagnostic log is available for this run.";
    }
  } else {
    $("log-connection-status").textContent = interrupted
      ? "Saved log only. This panel has not resumed the previous process."
      : cancelled ? "Runner process stopped (cancelled)." : "Runner process exited.";
  }

  $("run-error").hidden = false;
  $("run-error").textContent = errorMessage || (smoke
    ? "This was a server readiness check. It produced no benchmark results to share." : neverStarted
    ? "The benchmark process never started. See the technical details below for why."
    : "The run did not finish. Local logs above describe what happened.");
  loadRecentRuns().catch(() => {});
}

// A terminal job's own status ("failed"/"cancelled") no longer says which
// stage was active right before that -- unlike a live poll, which tracks
// lastKnownStageIndex incrementally as it happens, reopening a run cold
// only has this one final snapshot to work from. telemetry.stage (still
// on disk after the run ends) is the most specific real signal available;
// otherwise a real benchmark_job_id at least proves the runner reached
// "benchmarking", and its complete absence means it never got that far.
function lastReachedStageIndexForTerminalJob(runJob, telemetry) {
  if (telemetry && telemetry.available && telemetry.stage) {
    const key = telemetry.stage === "starting" ? "loading_model" : telemetry.stage;
    const index = stageIndex(key);
    if (index >= 0) return index;
  }
  return runJob && runJob.benchmark_job_id ? stageIndex("benchmarking") : 0;
}

async function openTerminalRun(jobId) {
  const runJob = await api(`/api/managed/runs/${jobId}`);
  clearTimers();
  finishedManagedJob = null;
  preview = null;
  $("submit-confirmed").checked = false;
  $("submit-button").disabled = true;
  $("submission-preview").hidden = true;
  let benchJob = null, telemetry = null;
  if (runJob.benchmark_job_id) {
    benchJob = await api(`/api/runs/${runJob.benchmark_job_id}`).catch(() => null);
    telemetry = await api(`/api/runs/${runJob.benchmark_job_id}/telemetry`).catch(() => null);
  }
  activeKind = null;
  activeJobId = null;
  benchmarkJobId = runJob.benchmark_job_id;
  flowStartedAt = new Date(runJob.created_at).getTime();
  lastKnownStageIndex = lastReachedStageIndexForTerminalJob(runJob, telemetry);
  lastKnownPercent = telemetry && telemetry.available && telemetry.remaining_fraction != null
    ? (1 - telemetry.remaining_fraction) * 100
    : null;
  showScreen("run");
  $("summary-panel").hidden = true;
  $("progress-panel").hidden = false;
  $("cancel-button").hidden = true;
  $("start-over-button").hidden = false;
  $("run-error").hidden = true;
  $("server-log-wrap").hidden = true;
  lastLogText = "";
  logAutoFollow = true;
  $("jump-to-latest-button").hidden = true;
  $("benchmark-log").textContent = "Waiting…";
  const recipe = recipesById[runJob.recipe];
  $("dashboard-subtitle").textContent = recipe
    ? `${recipe.model_id} (${recipe.quantization}) · ${recipe.benchmark_profile} · ${recipe.execution_device.toUpperCase()}`
    : "";
  renderProgress(null, runJob, benchJob, telemetry);
  finishFlow(runJob.status === "completed", runJob.error, runJob);
}

function interruptedByRestart() {
  clearTimers();
  activeKind = null;
  activeJobId = null;
  notice(
    "The local panel appears to have restarted since this run began. If a benchmark was in progress, it was "
    + "interrupted, it did not silently resume. Check .omobench-runs for any partial evidence, then start again.",
    true,
  );
  showScreen("choose");
  loadRecentRuns().catch(() => {});
}

function pollFlow() {
  clearTimers();
  const tick = async () => {
    try {
      let setupJob = null, runJob = null, benchJob = null, telemetry = null;
      if (activeKind === "setup") {
        setupJob = await api(`/api/managed/setup-jobs/${activeJobId}`);
      } else if (activeKind === "run") {
        runJob = await api(`/api/managed/runs/${activeJobId}`);
        if (runJob.benchmark_job_id) {
          benchmarkJobId = runJob.benchmark_job_id;
          benchJob = await api(`/api/runs/${benchmarkJobId}`);
          // Telemetry is best-effort: a fetch failure here (or the panel
          // simply not having any yet) must never break the main poll loop
          // that tracks whether the benchmark itself is still running.
          telemetry = await api(`/api/runs/${benchmarkJobId}/telemetry`).catch(() => null);
        }
      } else {
        return;
      }
      if (/^The request failed\.?$|Panel connection/.test($("notice").textContent)) notice(""); // a prior connection-lost notice, if any, is stale now
      renderProgress(setupJob, runJob, benchJob, telemetry);

      if (setupJob) {
        if (setupJob.status === "completed") { await startExecute(); return; }
        if (setupJob.status === "cancelled" || setupJob.status === "failed") { finishFlow(false, setupJob.error); return; }
      }
      if (runJob && !RUN_ACTIVE.has(runJob.status)) {
        finishFlow(runJob.status === "completed", runJob.error, runJob);
        return;
      }
      timers.push(setTimeout(tick, 1200));
    } catch (error) {
      if (isUnknownJobError(error)) { interruptedByRestart(); return; }
      $("log-connection-status").textContent = "Panel connection lost, retrying…";
      notice(error.message, true);
      timers.push(setTimeout(tick, 2500));
    }
  };
  tick();
}

async function cancelActive() {
  if (activeKind === "setup" && activeJobId) await api(`/api/managed/setup-jobs/${activeJobId}/cancel`, {});
  else if (activeKind === "run" && activeJobId) await api(`/api/managed/runs/${activeJobId}/cancel`, {});
}

function resetFlow() {
  clearTimers();
  activeKind = null; activeJobId = null; benchmarkJobId = null; finishedManagedJob = null;
  $("run-error").hidden = true;
  showScreen("choose");
}

// -- Screen 3: your results ----------------------------------------------------

function metricWithNote(label, value, unit, note) {
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

function showResults(summary) {
  const r = summary.results;
  $("results-status").textContent = "Execution completed. These saved measurements can be reviewed without rerunning the benchmark. "
    + "Completion does not mean this result meets publication requirements; check acceptance and submission status in Share results.";
  $("results-metrics").replaceChildren(
    metricWithNote("Time to first token · p50", r.latency_ms.ttft.p50, "ms", "Median time until the first streamed token; lower is better."),
    metricWithNote("Time to first token · p95", r.latency_ms.ttft.p95, "ms", "95th-percentile time until the first streamed token; lower is better."),
    metricWithNote("Inter-token latency · p50", r.latency_ms.inter_token.p50, "ms", "Median delay between output tokens; lower is better."),
    metricWithNote("Output tokens / second", r.output_tokens_per_second, "", "Output-token throughput; higher is better."),
    metricWithNote("Total tokens / second", r.total_tokens_per_second, "", "Input-plus-output token throughput."),
    metricWithNote("Requests / second", r.requests_per_second, "", "How many requests the workload completed per second."),
    metricWithNote("Successful / attempted", `${r.requests.succeeded} / ${r.requests.attempted}`, "", "Requests that completed successfully, out of those attempted in the measured window."),
    metricWithNote("Error rate", r.error_rate_percent, "%", "Share of attempted requests that failed or stayed incomplete; lower is better."),
  );
  $("go-share-button").hidden = false;
}

// -- Screen 4: share results ----------------------------------------------------

function statusChecklist(detail) {
  const list = $("status-checklist");
  list.replaceChildren();
  const items = [];
  items.push(["Execution completed", !!detail?.summary, detail?.summary ? "The benchmark finished and produced measurements." : "Not completed yet."]);
  if (detail?.validation) {
    const errors = detail.validation.errors || [];
    const acceptanceErrors = errors.filter((message) => message.includes("acceptance:"));
    const otherErrors = errors.filter((message) => !message.includes("acceptance:"));
    items.push(["Profile acceptance", acceptanceErrors.length === 0,
      acceptanceErrors.length ? acceptanceErrors.join(" ") : "Meets this profile's minimum successful requests and maximum error rate."]);
    items.push(["Bundle validation", otherErrors.length === 0,
      otherErrors.length ? otherErrors.join(" ") : "Schema, catalog, and artifact checks passed."]);
  } else {
    items.push(["Profile acceptance", null, "Package this run to check."]);
    items.push(["Bundle validation", null, "Package this run to check."]);
  }
  const pr = detail?.submission?.pull_request_url;
  items.push(["Submission created", !!pr, pr || "Not submitted yet."]);
  list.replaceChildren(...items.map(([label, ok, message]) => {
    const li = document.createElement("li");
    li.className = `status-item ${ok === null ? "pending" : ok ? "ok" : "fail"}`;
    const strong = document.createElement("strong"), span = document.createElement("span");
    strong.textContent = label; span.textContent = message;
    li.append(strong, span);
    return li;
  }));
}

function renderShareFacts(detail) {
  const facts = $("package-facts");
  facts.replaceChildren();
  const entries = [
    ["Model", detail.model],
    ["Model revision", detail.model_revision || "(not set)"],
    ["Weights source", detail.model_source || "(same as the canonical model repository)"],
    ["Quantization", detail.quantization || "(not recorded)"],
    ["Runtime", `${detail.framework || "(not recorded)"} ${detail.framework_version || ""}`.trim()],
    ["Startup arguments", (detail.runtime_args || []).join(" ") || "(none recorded)"],
    ["Profile", detail.profile],
    ["Execution device", (detail.execution_device || "(not recorded)").toUpperCase()],
  ];
  entries.forEach(([label, value]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = value; facts.append(dt, dd);
  });
  $("edit-model-source").value = detail.model_source || "";
  $("edit-model-revision").value = detail.model_revision || "";
  $("edit-quantization").value = detail.quantization || "";
  $("edit-framework-version").value = detail.framework_version || "";
  $("edit-runtime-args").value = (detail.runtime_args || []).join("\n");
}

function renderValidation(detail) {
  $("validation-result").hidden = false;
  $("validation-result").textContent = detail.validation.valid
    ? "Bundle validation passed. Review the files before submission."
    : detail.validation.errors.join("\n");
  $("validation-result").classList.toggle("error", !detail.validation.valid);
  $("prepare-review-button").hidden = !detail.validation.valid;
}

async function enterShareScreen() {
  showScreen("share");
  preview = null;
  $("submit-confirmed").checked = false;
  $("submit-button").disabled = true;
  $("submission-preview").hidden = true;
  $("submission-link").hidden = true;
  $("edit-details-toggle").checked = false;
  $("package-editable").hidden = true;
  const detail = await api(`/api/runs/${benchmarkJobId}`);
  const guidedPackaging = detail.execution_device === "cpu" && detail.framework === "llama.cpp";
  $("package-button").disabled = !guidedPackaging;
  $("advanced-package-note").hidden = guidedPackaging;
  renderShareFacts(detail);
  statusChecklist(detail);
  $("validation-result").hidden = !detail.validation;
  $("prepare-review-button").hidden = !detail.validation?.valid;
  if (detail.validation) renderValidation(detail);
}

// -- Wiring ----------------------------------------------------------------

$("benchmark-log").addEventListener("scroll", () => {
  const el = $("benchmark-log");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  logAutoFollow = atBottom;
  $("jump-to-latest-button").hidden = atBottom;
});
$("jump-to-latest-button").addEventListener("click", () => {
  const el = $("benchmark-log");
  el.scrollTop = el.scrollHeight;
  logAutoFollow = true;
  $("jump-to-latest-button").hidden = true;
});

$("change-location-button").addEventListener("click", () => {
  const editor = $("storage-location-editor");
  editor.hidden = !editor.hidden;
  if (!editor.hidden) $("storage-dir").focus();
});
$("recheck-button").addEventListener("click", () => action(doReview));
$("start-button").addEventListener("click", () => action(doStart));
$("cancel-button").addEventListener("click", () => action(cancelActive));
$("start-over-button").addEventListener("click", () => action(async () => { resetFlow(); await loadRecipes(); await loadRecentRuns(); }));
$("run-another-button").addEventListener("click", () => action(async () => { resetFlow(); await loadRecipes(); await loadRecentRuns(); }));
$("go-share-button").addEventListener("click", () => action(async () => { await enterShareScreen(); }));
$("download-summary-button").addEventListener("click", () => action(async () => {
  const recipe = recipesById[finishedManagedJob.recipe];
  const summary = {
    kind: "omobench-local-summary", execution_device: finishedManagedJob.execution_device || recipe?.execution_device,
    model: finishedManagedJob.model || recipe?.model_id, profile: finishedManagedJob.profile || recipe?.benchmark_profile,
    ...finishedManagedJob.result,
  };
  download(new Blob([JSON.stringify(summary, null, 2) + "\n"], { type: "application/json" }), `${finishedManagedJob.id}-local-summary.json`);
}));

$("edit-details-toggle").addEventListener("change", () => { $("package-editable").hidden = !$("edit-details-toggle").checked; });

$("submit-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const detail = await api(`/api/runs/${benchmarkJobId}`);
    if (detail.execution_device !== "cpu" || detail.framework !== "llama.cpp") {
      throw new Error("Use the advanced panel to package this run with its recorded device and runtime metadata.");
    }
    const editing = $("edit-details-toggle").checked;
    const submitter = $("submitter").value.trim();
    if (!submitter) throw new Error("Enter a submitter identity.");
    const data = {
      model: detail.model,
      model_revision: editing ? $("edit-model-revision").value.trim() : (detail.model_revision || ""),
      framework: detail.framework || "",
      framework_version: editing ? $("edit-framework-version").value.trim() : (detail.framework_version || ""),
      quantization: editing ? $("edit-quantization").value.trim() : (detail.quantization || ""),
      model_source: editing ? $("edit-model-source").value.trim() : (detail.model_source || ""),
      runtime_args: (editing ? $("edit-runtime-args").value : (detail.runtime_args || []).join("\n"))
        .split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
      submitter,
    };
    preview = null;
    $("submission-preview").hidden = true;
    $("submission-link").hidden = true;
    $("submit-confirmed").checked = false;
    const updated = await api(`/api/runs/${benchmarkJobId}/package`, data);
    renderValidation(updated);
    statusChecklist(updated);
  });
});

const community = new CommunitySubmission(api, () => {
  preview = null;
  $("submission-preview").hidden = true;
  $("submit-confirmed").checked = false;
  $("submit-button").disabled = true;
});

$("prepare-review-button").addEventListener("click", () => action(async () => {
  const reviewingJob = benchmarkJobId, ticket = community.ticket();
  const prepared = await api(`/api/runs/${reviewingJob}/prepare`, { mode: ticket.mode });
  if (benchmarkJobId !== reviewingJob || !community.current(ticket)) return;
  preview = prepared;
  $("repository").textContent = community.destination(preview);
  $("submission-files").replaceChildren(...preview.files.map((file) => {
    const li = document.createElement("li"), detail = document.createElement("span");
    li.textContent = `${file.path} · ${(file.bytes / 1024).toFixed(1)} KiB`;
    detail.textContent = `SHA-256 ${file.sha256}`;
    li.append(detail);
    return li;
  }));
  $("result-json").textContent = JSON.stringify(preview.result, null, 2);
  $("pr-description").textContent = preview.title + "\n\n" + preview.body;
  $("github-message").textContent = preview.message || "Creates a new branch and PR. CI and maintainer review follow on GitHub.";
  $("submit-confirmed").checked = false;
  $("submission-preview").hidden = false;
  $("submit-button").disabled = true;
}));

$("download-bundle-button").addEventListener("click", () => action(async () =>
  download(await api(`/api/runs/${benchmarkJobId}/bundle`, undefined, true), `${benchmarkJobId}-submission.zip`)));

function downloadComparisonResult() {
  if (!preview?.result) throw new Error("Package, validate and review the submission files first.");
  // Export the already-reviewed normalized record exactly as shown. Never
  // send a local result or the panel token to the hosted comparison page.
  download(new Blob([JSON.stringify(preview.result, null, 2) + "\n"], { type: "application/json" }), "result.json");
}

$("download-comparison-button").addEventListener("click", () => action(downloadComparisonResult));

$("submit-confirmed").addEventListener("change", () => {
  $("submit-button").disabled = !preview?.can_submit || !$("submit-confirmed").checked;
});

$("submit-button").addEventListener("click", () => action(async () => {
  notice("Uploading the reviewed bundle and creating a pull request. This may take a minute…");
  const detail = await api(`/api/runs/${benchmarkJobId}/submit`,
    community.payload(preview, $("submit-confirmed").checked));
  notice(`Submission ${detail.submission?.pull_request_state || "available"}. Follow its checks and maintainer review on GitHub.`);
  $("submit-confirmed").checked = false;
  statusChecklist(detail);
  const pr = detail.submission?.pull_request_url;
  if (typeof pr === "string" && /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+$/.test(pr)) {
    const link = document.createElement("a");
    link.href = pr; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Open pull request on GitHub ↗";
    $("submission-link").replaceChildren(link);
    $("submission-link").hidden = false;
  }
}));

async function init() {
  try {
    if (!token) throw new Error("Open the full connection link printed by omobench ui, including its #token part.");
    community.refresh();
    await loadRunnerStatus();
    await loadRecipes();
    await loadRecentRuns();
    if (!activeKind && recipes.length === 1) await chooseRecipe(recipes[0].id);
  } catch (error) { notice(error.message, true); }
}
init();
