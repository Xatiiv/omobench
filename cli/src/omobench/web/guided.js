"use strict";

const $ = (id) => document.getElementById(id);

const SETUP_ACTIVE = new Set(["preparing", "cancelling"]);
const RUN_ACTIVE = new Set(["starting_server", "waiting_ready", "running_benchmark", "cancelling"]);

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

const incoming = new URLSearchParams(location.hash.slice(1)).get("token");
if (incoming) {
  sessionStorage.setItem("omobench-token", incoming);
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("omobench-token") || "";

// One shared dashboard component, the same one the advanced panel mounts.
const dashboard = new BenchmarkDashboard($("dashboard-mount"), {
  stages: BenchmarkDashboard.MANAGED_STAGES,
  stageNote: "From preparation to saved evidence",
  downloadProgress: true,
  serverLog: true,
});

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
      showScreen("run");
    $("summary-panel").hidden = true;
    $("progress-panel").hidden = false;
    const recipe = recipesById[selectedRecipeId];
    dashboard.prepareForRun({
      subtitle: recipe ? `${recipe.model_id} (${recipe.quantization}) · ${recipe.benchmark_profile} · ${recipe.execution_device.toUpperCase()}` : "",
    });
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

function stageIndex(key) { return dashboard.stageIndex(key); }

async function doStart() {
  flowStartedAt = Date.now();
  lastKnownStageIndex = 0;
  $("summary-panel").hidden = true;
  $("progress-panel").hidden = false;
  const recipe = recipesById[selectedRecipeId];
  dashboard.prepareForRun({
    subtitle: recipe ? `${recipe.model_id} (${recipe.quantization}) · ${recipe.benchmark_profile} · ${recipe.execution_device.toUpperCase()}` : "",
  });
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

// One live frame of the managed flow, expressed in the shared dashboard's
// vocabulary. Every rule about what may be displayed -- progress only from the
// runner's own remaining_fraction, "Pending" rather than an invented zero,
// never 100% before evidence finalization -- lives in that component and so is
// identical on the advanced panel.
function renderProgress(setupJob, runJob, benchJob, telemetry) {
  const key = currentStageKey(setupJob, runJob, telemetry);
  const cancelling = (setupJob && setupJob.status === "cancelling") || (runJob && runJob.status === "cancelling");
  if (key) lastKnownStageIndex = stageIndex(key);
  const recipe = recipesById[runJob ? runJob.recipe : setupJob ? setupJob.recipe : selectedRecipeId];
  dashboard.render({
    stageKey: key,
    lastKnownStageIndex,
    cancelling,
    runActive: !(runJob && !RUN_ACTIVE.has(runJob.status)),
    telemetry,
    maxDurationSeconds: recipe && recipe.max_duration_seconds,
    startedAt: flowStartedAt,
    logText: benchJob ? (benchJob.log || "Waiting…") : null,
    serverLog: runJob && runJob.server_log,
    // Genuine byte-level download progress (never a manufactured percentage):
    // straight from the resumable downloader's own done/total byte counts.
    downloadProgress: setupJob && setupJob.progress,
  });
  $("cancel-button").disabled = !!cancelling;
  $("cancel-button").textContent = cancelling ? "Cancelling…" : "Cancel";
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

  // Cancellation or failure: the stage list, chart, and technical facts were
  // already rendered from the real last-known data by the renderProgress()
  // call that always precedes this one (see pollFlow() and openTerminalRun())
  // -- never reset to zero, never advanced past what the runner actually
  // reached. The shared dashboard only freezes the still-live-looking pieces
  // and makes the terminal state itself unmistakable.
  const ok = !!(success && runJob && runJob.result?.results && runJob.benchmark_job_id);
  dashboard.finish({
    ok,
    status: runJob?.status,
    smoke: runJob && runJob.kind === "smoke",
    kindLabel: runJob && runJob.kind === "smoke" ? "Server check, no benchmark measurements" : null,
    runnerStarted: !runnerNeverStarted(runJob),
    errorMessage,
    createdAt: runJob?.created_at,
    finishedAt: runJob?.finished_at,
    hasServerLog: !!runJob?.server_log,
  });
  if (ok) {
    finishedManagedJob = runJob;
    benchmarkJobId = runJob.benchmark_job_id;
    showResults(runJob.result);
    showScreen("results");
  }
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
  showScreen("run");
  $("summary-panel").hidden = true;
  $("progress-panel").hidden = false;
  const recipe = recipesById[runJob.recipe];
  dashboard.prepareForRun({
    subtitle: recipe ? `${recipe.model_id} (${recipe.quantization}) · ${recipe.benchmark_profile} · ${recipe.execution_device.toUpperCase()}` : "",
  });
  $("cancel-button").hidden = true;
  $("start-over-button").hidden = false;
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
      dashboard.setLogStatus("Panel connection lost, retrying…");
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

function showResults(summary) {
  $("results-status").textContent = "Execution completed. These saved measurements can be reviewed without rerunning the benchmark. "
    + "Completion does not mean this result meets publication requirements; check acceptance and submission status in Share results.";
  $("results-metrics").replaceChildren(...BenchmarkDashboard.metricCards(summary));
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
  renderRequirements(await api(`/api/runs/${benchmarkJobId}/requirements`));
  $("validation-result").hidden = !detail.validation;
  $("prepare-review-button").hidden = !detail.validation?.valid;
  if (detail.validation) renderValidation(detail);
}

// -- Wiring ----------------------------------------------------------------

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
  const report = await api(`/api/runs/${benchmarkJobId}/local-report`);
  download(new Blob([JSON.stringify(report, null, 2) + "\n"], { type: "application/json" }), `${benchmarkJobId}-local-report.json`);
}));

// Every publication requirement at once, separate from the local report.
function renderRequirements(report) {
  $("publication-checklist").replaceChildren(...report.items.map((item) => {
    const li = document.createElement("li");
    li.className = `status-item ${item.ok === null ? "pending" : item.ok ? "ok" : "fail"}`;
    const strong = document.createElement("strong"), span = document.createElement("span");
    strong.textContent = item.label; span.textContent = item.detail;
    li.append(strong, span);
    return li;
  }));
  const missing = report.items.filter((item) => item.ok === false).length;
  $("publication-summary").textContent = missing
    ? `${missing} requirement${missing === 1 ? "" : "s"} still missing for a community submission. Your measurements stay available, and Save local report works regardless.`
    : "Every requirement this panel can check is satisfied. Packaging and the registry validator remain the authority.";
}

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
