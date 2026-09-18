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
  if (target === "run" && !selectedRecipeId) return;
  if ((target === "results" || target === "share") && !finishedManagedJob) return;
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
      ? `Ends at its configured request or time limit — up to ${minutes} minutes — whichever comes first; it may finish earlier.`
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
  const recent = managedRuns.filter((job) => !RUN_ACTIVE.has(job.status)).slice(0, 5);
  $("recent-panel").hidden = recent.length === 0;
  $("recent-list").replaceChildren(...recent.map((job) => {
    const row = document.createElement("button");
    row.type = "button"; row.className = "recent-item";
    const recipe = recipesById[job.recipe];
    row.textContent = `${recipe ? recipe.title : job.recipe} · ${job.status} · ${new Date(job.created_at).toLocaleString()}`;
    row.addEventListener("click", () => action(async () => {
      if (job.result) {
        selectedRecipeId = job.recipe; storageDir = job.storage_dir;
        finishedManagedJob = job; benchmarkJobId = job.benchmark_job_id;
        showResults(job.result); showScreen("results");
      } else {
        notice(job.error || "This run did not produce a result.", true);
      }
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
    downloadLine = `${formatBytes(totalBytes)} total — every file already verified in this folder's cache; nothing left to download.`
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
      ? `${minutes} minutes — the run ends at its configured request or time limit, whichever comes first; it may finish before the time limit.`
      : "Bounded by the profile's own request or time limit."],
  ];
  entries.forEach(([label, value]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = value; facts.append(dt, dd);
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
    spaceMessage.textContent = `Free space: ${formatBytes(report.free_space_bytes)} (${formatBytes(report.required_bytes)} required) — enough space.`;
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
    if (telemetry && telemetry.available && telemetry.stage) {
      // GuideLLM's own accumulator.timings.status ("pending"/"warmup"/
      // "active"/"cooldown"), mapped to this page's vocabulary in
      // telemetry.py -- "starting" there still means "loading_model" here,
      // since no request has been sent yet.
      return telemetry.stage === "starting" ? "loading_model" : telemetry.stage;
    }
    return "benchmarking"; // telemetry unavailable: a real stage, just not sub-divided
  }
  return null; // terminal
}

function renderProgressBar(percent) {
  const fill = $("progress-bar-fill"), label = $("progress-bar-label"), wrap = $("progress-bar-wrap");
  if (percent === null || percent === undefined || !Number.isFinite(percent)) {
    fill.classList.add("indeterminate");
    fill.style.width = "";
    label.textContent = "Progress unavailable for this stage";
    wrap.setAttribute("aria-valuenow", "0");
  } else {
    const clamped = Math.max(0, Math.min(100, Math.round(percent)));
    fill.classList.remove("indeterminate");
    fill.style.width = `${clamped}%`;
    label.textContent = `${clamped}%`;
    wrap.setAttribute("aria-valuenow", String(clamped));
  }
}

function renderChart(telemetry) {
  const history = (telemetry && telemetry.available && telemetry.chart_history) || [];
  $("chart-empty").hidden = history.length > 0;
  $("chart-svg").hidden = history.length === 0;
  if (history.length === 0) {
    $("chart-line").setAttribute("points", "");
    return;
  }
  const maxX = Math.max(1, history[history.length - 1].elapsed_seconds);
  const maxY = Math.max(1, ...history.map((sample) => sample.output_tokens_per_second));
  const points = history
    .map((sample) => {
      const x = (sample.elapsed_seconds / maxX) * 600;
      const y = 155 - (sample.output_tokens_per_second / maxY) * 145;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  $("chart-line").setAttribute("points", points);
}

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

  const recipe = recipesById[selectedRecipeId];
  const telemetryOk = telemetry && telemetry.available;
  const elapsedSeconds = telemetryOk && typeof telemetry.elapsed_seconds === "number"
    ? telemetry.elapsed_seconds
    : (flowStartedAt ? (Date.now() - flowStartedAt) / 1000 : null);
  $("elapsed").textContent = formatElapsed(flowStartedAt);
  $("fact-elapsed").textContent = elapsedSeconds != null ? formatDuration(elapsedSeconds) : "—";

  if (key === "saving") {
    $("fact-eta").textContent = "Saving results…";
  } else if (!telemetryOk) {
    $("fact-eta").textContent = "—";
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
    $("fact-requests").textContent = "—";
  }
  $("fact-max-duration").textContent = recipe && recipe.max_duration_seconds
    ? `${Math.round(recipe.max_duration_seconds / 60)} min ceiling`
    : "—";

  const percent = telemetryOk && telemetry.remaining_fraction != null ? (1 - telemetry.remaining_fraction) * 100 : null;
  renderProgressBar(key === "saving" ? 100 : percent);
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

function finishFlow(success, errorMessage, runJob) {
  clearTimers();
  $("cancel-button").hidden = true;
  $("start-over-button").hidden = false;
  if (success && runJob && runJob.result) {
    // 100% only here: execution finished *and* local evidence finalization
    // (compression, environment.json, reproduce.md) already completed --
    // runJob only reaches a terminal "completed" status after that.
    renderProgressBar(100);
    finishedManagedJob = runJob;
    benchmarkJobId = runJob.benchmark_job_id;
    showResults(runJob.result);
    showScreen("results");
  } else {
    // Cancellation or failure: leave the progress bar, stage list, and
    // chart exactly as last rendered -- their real last-known state, not
    // reset to zero and not advanced to 100%.
    $("run-error").hidden = false;
    $("run-error").textContent = errorMessage || "The run did not finish. Local logs above describe what happened.";
  }
  loadRecentRuns().catch(() => {});
}

function interruptedByRestart() {
  clearTimers();
  activeKind = null;
  activeJobId = null;
  notice(
    "The local panel appears to have restarted since this run began. If a benchmark was in progress, it was "
    + "interrupted — it did not silently resume. Check .omobench-runs for any partial evidence, then start again.",
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
      $("log-connection-status").textContent = "Panel connection lost — retrying…";
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
  $("results-status").textContent = "Execution completed. These are real CPU measurements from the managed server, "
    + "staged as a normal local evidence directory — nothing has been uploaded.";
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
    ["Runtime", `${detail.framework || "llama.cpp"} ${detail.framework_version || ""}`.trim()],
    ["Startup arguments", (detail.runtime_args || []).join(" ") || "(none recorded)"],
    ["Profile", detail.profile],
    ["Execution device", (detail.execution_device || "cpu").toUpperCase()],
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
  $("submission-preview").hidden = true;
  $("submission-link").hidden = true;
  $("edit-details-toggle").checked = false;
  $("package-editable").hidden = true;
  const detail = await api(`/api/runs/${benchmarkJobId}`);
  renderShareFacts(detail);
  statusChecklist(detail);
  $("validation-result").hidden = !detail.validation;
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
  const recipe = recipesById[selectedRecipeId];
  const summary = {
    kind: "omobench-local-summary", execution_device: "cpu",
    model: recipe ? recipe.model_id : finishedManagedJob.recipe, profile: recipe ? recipe.benchmark_profile : undefined,
    ...finishedManagedJob.result,
  };
  download(new Blob([JSON.stringify(summary, null, 2) + "\n"], { type: "application/json" }), `${finishedManagedJob.id}-local-summary.json`);
}));

$("edit-details-toggle").addEventListener("change", () => { $("package-editable").hidden = !$("edit-details-toggle").checked; });

$("submit-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const detail = await api(`/api/runs/${benchmarkJobId}`);
    const editing = $("edit-details-toggle").checked;
    const submitter = $("submitter").value.trim();
    if (!submitter) throw new Error("Enter a submitter identity.");
    const data = {
      model: detail.model,
      model_revision: editing ? $("edit-model-revision").value.trim() : (detail.model_revision || ""),
      framework: "llama.cpp",
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

$("prepare-review-button").addEventListener("click", () => action(async () => {
  preview = await api(`/api/runs/${benchmarkJobId}/prepare`, {});
  $("repository").textContent = preview.repository
    ? `Destination: ${preview.repository} · base: ${preview.base_branch}`
    : "Direct submission is unavailable here. Download the validated bundle below to submit manually.";
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

$("submit-confirmed").addEventListener("change", () => {
  $("submit-button").disabled = !preview?.can_submit || !$("submit-confirmed").checked;
});

$("submit-button").addEventListener("click", () => action(async () => {
  notice("Uploading the reviewed bundle and creating a pull request. This may take a minute…");
  const detail = await api(`/api/runs/${benchmarkJobId}/submit`,
    { repository: preview.repository, bundle_digest: preview.bundle_digest, confirmed: $("submit-confirmed").checked });
  notice("Pull request created. Review its checks on GitHub before merging.");
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
    await loadRunnerStatus();
    await loadRecipes();
    await loadRecentRuns();
    if (!activeKind && recipes.length === 1) await chooseRecipe(recipes[0].id);
  } catch (error) { notice(error.message, true); }
}
init();
